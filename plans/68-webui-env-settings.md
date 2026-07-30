# 68. 설정 웹UI 전면 개편 계획 (v2 — 설정 코드 정밀 분석 반영)

> 작성일: 2026-07-29 (v1) / **v2 갱신: 2026-07-29 — 설정 소비 지점 전수 분석·관리자 UI 스택 분석·pydantic 인트로스펙션 실측 반영** / **v2.1 갱신: 2026-07-30 — §6 Phase 4(즉시 반영 확대) 재론 분석 등재(사용자 재론 요청 — 실측: D-127 리스크 해소·단계별 방안·구조상 불가 목록 확정)** / **v2.2 갱신: 2026-07-30 — §6 Phase 4 구현 완료(§6.2 구현 기록·소비 지점 3에이전트 전수 재실측·apply_mode 3분류 확정표, D-135 등재)**
> **성격**: 구현 계획(implementation-ready). 요청 취지 — ".env, .env.example의 모든 옵션을 웹UI에서 설정" + "설정 관련 코드를 면밀히 분석하여 설정 웹UI가 작성될 수 있도록 계획 작성"
> **대상 기능**: `src/api/routes/admin.py`, `src/static/admin/dashboard.html`, `src/static/js/admin.js`, `src/static/css/style.css`, `src/config.py`(카탈로그 원천), `src/domain/audit.py`(감사 이벤트)
> **관련 결정**: D-127(과금 외부 API 건별 승인), D-070(운영자/사용자 시크릿 분리), D-071(기본 크레덴셜 제거), D-035(결정적=판단·LLM=보조)
> **신규 결정(착수 시 등재)**: **D-129**(설정 카탈로그 SSOT = pydantic 인트로스펙션 + 시크릿 웹UI 편집 차단 + 반영 정책 "재시작 필요 기본") 예약. ※ 채번: `grep "^## D-"` 실측 최댓값 D-127, **D-128은 plans/67 예약** — 등재 시 재실측.
> **상태**: **확정 — 착수 가능 (2026-07-29 사용자 승인)**. §9 게이트 5건 전부 권고안대로 확정 + **사용자 인터뷰 4건 확정(§9-보완)**. 옵션별 UI 구성은 **부록 A**(224필드 전수)에 확정 반영.
> **진행 방식(인터뷰 확정)**: **Phase 1~3 일괄 구현** 후 최종 보고(중간 승인 게이트 없음). 단 각 Phase 산출물은 독립 검증 가능하게 유지(§3.5 테스트 선통과 후 §4 착수).
> **분석 근거(전부 실측·file:line 병기)**: ① pydantic 인트로스펙션·dry-run 스크립트 실행 ② `load_config`/`os.getenv` 소비 지점 전수 스캔 ③ 관리자 UI 스택(HTML/JS/CSS/인증/감사/테스트) 정밀 분석. 과금 외부 API 호출 0건(D-127 준수).

---

## 0. 요약 — v2에서 확정·변경된 것

v1 계획의 골격(카탈로그 SSOT·3 Phase·시크릿 제외)은 유지하되, 코드 정밀 분석으로 다음이 **확정 또는 전면 수정**됐다.

1. **[확정] 카탈로그 인트로스펙션 성립** — `AppConfig`는 nested 그룹 **17개(209필드) + top-level 15필드 = 224필드**. `field.validation_alias.choices`(alias), `typing.get_args(Literal)`(enum), `SecretStr` 판별 모두 실측 동작. 검증 dry-run은 `AppConfig(_env_file=…)`가 **nested에 전파되지 않으므로**(default_factory가 자체 env_file 사용) **그룹 클래스 단위** `LLMConfig(_env_file=tmp)` 방식으로 확정 — 값 반영·ValidationError 발생 모두 실증됨. (v1 R1 해소)
2. **[전면 수정] 반영 정책** — **`load_config.cache_clear()`는 현 구조에서 거의 무효다.** `server.py:612`의 모듈 레벨 `app = create_app()`이 임포트 시점에 config를 `app.state.config`로 영구 고정하고(실증: cache_clear 후에도 `app.state.config`는 옛 객체), `graph.py:302-643`의 `build_graph`가 모든 노드에 `partial(node, app_config=config)`로 기동 시점 config를 주입한다(노드 내부 `load_config()` 28곳은 전부 `app_config is None` 폴백 = 서버 경로에서 사문). 즉시 반영되는 경로는 **4곳뿐**(§1.3). → UI는 **"재시작 필요"를 기본값으로 정직하게 표시**하고, 핫 리로드 확대는 선택 Phase 4(§6)로 분리한다.
3. **[신규 발견] 우선순위 체인 함정** — 유효값 우선순위는 **OS env > `.encenv` > `.env` > 코드 기본값**(실증). `.encenv` 등재 6키는 `.env`를 UI로 수정해도 **기능적으로 무시**되므로 편집 차단이 정책이 아니라 **필수**다. OS env로 export된 키도 동일 → UI에 **파일값과 실효값을 병기**하고 오버라이드 경고를 표시한다.
4. **[신규 발견] 미소비 필드 16개** — config에 정의됐지만 어디서도 읽지 않는 필드(§1.5-4)는 "미소비" 뱃지로 구분한다(노브 환상 방지).
5. **[신규 발견] `ENABLE_SEMANTIC_ROUTING` 무력화 버그** — `config.py:780`이 `os.getenv`로 읽어 `.env`의 false가 무시되고 `ACTIVE_DB_IDS` 존재 시 강제 True. Known Mistakes("os.getenv로 설정값 판단 금지") 위반 잔존 — 선행 교정 후보(§9 게이트 4).

---

## 1. 설정 코드 전체 지도 (실측)

### 1.1 카탈로그 원천 — `src/config.py` 구성

| cfg 경로 | env_prefix | env_file | 필드 수 | 비고 |
|---|---|---|---|---|
| `llm` | `LLM_` | [.env, .encenv] | 11 | api_key류 3필드는 시크릿 |
| `orchestrator` | `ORCHESTRATOR_` | [.env, .encenv] | 12 | api_key 시크릿 |
| `dbhub` | `DBHUB_` | .env | 4 | bearer_token 시크릿 |
| `query` | `QUERY_` | .env | 4 | |
| `synonym` | `SYNONYM_` | .env | 13 | |
| `text2sql` | `TEXT2SQL_` | .env | 9 | |
| `security` | `SECURITY_` | .env | 5 | list[str] 2필드 |
| `server` | `API_` | .env | 5 | cors_origins는 list[str] |
| `admin` | `ADMIN_` | [.env, .encenv] | 4 | password/jwt_secret 시크릿 |
| `auth` | `AUTH_` | [.env, .encenv] | 8 | jwt_secret 시크릿 |
| `multi_db` | `MULTI_DB_` | .env | 1 | **alias `ACTIVE_DB_IDS`** |
| `redis` | `REDIS_` | [.env, .encenv] | 6 | password 시크릿 |
| `schema_cache` | `SCHEMA_CACHE_` | .env | 5 | |
| `audit` | `AUDIT_` | .env | 8 | |
| `alarm` | `ALARM_` | .env | 21 | |
| `workb` | `WORKB_` | [.env, .encenv] | 9 | bearer_token 시크릿 |
| `noise_gate` | `NOISE_` | .env | **84** | 최대 그룹 — Plan 52/60/64 하위 구획 필요. `investigation_service_token`은 `SecretStr` |
| (top-level) | 없음 | .env | 15 | `CHECKPOINT_*`, `DB_*`, `ENABLE_*`, `LOG_LEVEL`, `POLESTAR_DB_IDS`, `MAX_REPLAN`, `WORKER_PROVIDER_OVERRIDE`, `CONVERSATION_*` |

파일 실측: `.env` 고유 키 **137**, `.env.example` 고유 키 **157**, 합집합 **165**. 어느 파일에도 없는 config 전용 필드가 수십 개(예: `AUTH_MAX_LOGIN_ATTEMPTS`, `AUDIT_ALERT_ON_*`, `ORCHESTRATOR_MAX_INPUT_TOKENS`, `NOISE_INVESTIGATION_*`, `ALARM_PROMETHEUS_*`, `SCHEMA_CACHE_FINGERPRINT_TTL_SECONDS`) → **파일 파싱만으로는 전체 옵션을 열거할 수 없고, config.py 인트로스펙션이 유일한 완전한 원천**이다.

특수 케이스(인트로스펙션 실측 확인):
- **alias**: `MultiDBConfig.active_db_ids_csv` → `validation_alias.choices == ['ACTIVE_DB_IDS', 'MULTI_DB_ACTIVE_DB_IDS_CSV']` — 첫 alias를 대표 env 키로 사용.
- **Literal enum**: `typing.get_args()`로 choices 추출 (`provider`, `semantic_backend`, `checkpoint_backend` 등).
- **`bool | None` 3상**: `enable_deepagent_orchestration` — 미설정(auto)/true/false.
- **`SecretStr`**: `NoiseGateConfig.investigation_service_token` — 자동 시크릿 판정.
- **`model_post_init` 보정**: `LLMConfig`가 `FABRIX_*`/`GOOGLE_API_KEY`/`LLM_API_KEY`를 `os.getenv`로 보정(`config.py:44-62`) — 전부 시크릿 영역이라 카탈로그 제외로 무관.

### 1.2 설정값 우선순위 체인 (실증)

```
OS 환경변수  >  .encenv  >  .env  >  config.py 기본값
```

- `env_file: [".env", ".encenv"]` 그룹은 **리스트 뒤쪽이 우선**(실증: 둘 다 있으면 .encenv 승) — 현재 `.encenv`에 실존하는 `ADMIN_JWT_SECRET, LLM_API_KEY, LLM_FABRIX_API_KEY, LLM_FABRIX_CLIENT_KEY, LLM_GEMINI_API_KEY, REDIS_PASSWORD` 6키는 **`.env`를 웹UI로 수정해도 반영되지 않는다**.
- OS environ은 두 파일 모두를 이긴다(실증) — 컨테이너/systemd export 키는 UI 수정이 영구 무시됨.
- 추가 함정: `src/clients/ollama_client.py:52`는 `os.getenv("LLM_OLLAMA_BASE_URL", base_url)`로 **OS env가 인자를 덮어쓰는** 역전 패턴(폴백 아님).
- **CWD 의존**: `admin.py:30` `_ENV_FILE`은 절대경로, pydantic `env_file: ".env"`는 **CWD 상대** — 프로젝트 루트 밖에서 기동하면 UI가 쓰는 파일과 config가 읽는 파일이 갈라진다.

### 1.3 소비 시점 지도 — "저장하면 언제 반영되는가"

| 분류 | 대표 근거 | 결과 |
|---|---|---|
| (a) 모듈 임포트 캡처 | `server.py:612` `app = create_app()` → `:510` `load_config()` → `:525` `app.state.config` 고정. CORS `:530` | **cache_clear로도 갱신 불가.** `request.app.state.config` 소비처 전량(dependencies/query/alarm/user_auth/admin_auth/admin/health 라우트) 해당 |
| (b) 기동 시 1회 캡처 | `server.py:236-418`(로깅·체크포인터·**build_graph:248**·감사·cache_manager·**AlarmWorker:317-320**·SSE·incident), `graph.py:302-643` partial 주입, `alarm_worker.py:69` `self._config` 보관, `main.py:70` uvicorn, `synonym_semantic.py:120,128` 임베더 영구 래치 | **재시작 필요** |
| (c) 요청 시점 fresh `load_config()` | `schema_cache.py` 라우트 15개(`:518`은 LLM도 요청마다 생성), `document/field_mapper.py:310`(synonym 플래그), `redis_cache.py:130`(governance), `cache_manager.py:1293`(auto_generate_descriptions) | **cache_clear 후 다음 요청부터 반영 — 이 4경로뿐** |
| (c′) 부분 kwargs 재구성 | `db/__init__.py:50-54`, `db_registry.py:136-141` — `DBHubConfig(server_url=…)` 시 미명시 필드(`bearer_token`)를 매번 env에서 재독(실증) | cache_clear 없이도 반영 |
| (d) 사문 폴백 | `src/nodes/*`·`src/orchestration/*`·`semantic_router.py`의 `load_config()` 28곳 — 전부 `if app_config is None:` 폴백 | 서버 경로에서 실행 안 됨 |

**그룹별 requires_restart 확정표** (카탈로그 메타 초기값 — 근거는 소비 지점 분석 보고):

| 그룹 | 판정 | 즉시 반영 예외 필드 |
|---|---|---|
| llm, orchestrator, text2sql, security, server, admin, auth, redis, audit, alarm, workb, noise_gate, top-level 전부 | **재시작 필요** | 없음 (llm은 schema_cache 라우트 한정 fresh — 주 질의 경로는 재시작) |
| dbhub | 혼재 | `bearer_token`만 즉시 (c′) |
| query | 재시작 필요 | schema_cache 라우트 경유 시만 fresh |
| synonym | **혼재** | `fuzzy_match`/`semantic_match`/`*_confidence_min`(field_mapper 경로)·`governance` 즉시. `semantic_backend`/`semantic_model_path`/`semantic_vllm_*`는 영구 래치로 재시작, `max_synonym_supplement_tables` 재시작 |
| multi_db | 재시작 필요 | schema_cache 라우트 한정 fresh. 그래프 토폴로지 결정(`config.py:783,791`)이라 실질 재시작 |
| schema_cache | **혼재** | `auto_generate_descriptions`만 즉시. `backend`/`cache_dir`/`enabled`/`fingerprint_ttl_seconds`는 싱글톤(`cache_manager.py:1695-1713`, `reset_cache_manager()`는 prod 호출부 0건) 재시작 |

### 1.4 기존 웹UI 스택 (재사용 자산·부재 목록)

- **라우트**: `admin.router`가 `prefix="/api/v1"`로 등록(`server.py:548`) → `/api/v1/admin/settings`. HTML은 `FileResponse` 핸들러(`/admin`:595), `/static`은 라우트 등록 **이후** mount(`:601-606`).
- **기존 설정 API**: `GET/PUT /admin/settings`(`admin.py:296-371`) — `.env` 직접 파싱(`_read_env_file:158`), 주석·순서 보존 갱신(`_write_env_file:187`), 민감 키워드 마스킹(`_is_sensitive_key:152`). **임의 키·임의 값 무검증 저장 + 마스킹 값 재저장 서버 가드 없음 + 백업 없음.**
- **인증**: `require_admin_user`(`dependencies.py:188-215`) — ① `auth.enabled=False`면 익명 통과(개발 우회) ② break-glass 관리자 JWT(`type=="admin"` 필수) ③ 사용자 JWT + DB 실시간 `role=="admin"`. 토큰은 `localStorage["admin_token"|"user_token"]`, `admin.js:12`에서 **1회 캡처**(만료 시 무안내 실패 — 401 처리 추가 권장).
- **admin.js**(890줄): 단일 IIFE. 재사용 — `apiRequest`(:109, 항상 Bearer+JSON, **raw Response 반환**), `showError/showSuccess`(:123), 탭 전환(:139), `.loading` 관용구. 교체 — `renderSettings`(:194)·저장 핸들러(:224)·`#settingsTable` DOM(dashboard.html:59-67). **설정 탭은 eager 로드(:175)** → lazy 전환 필요. `escapeHtml`(:566)은 `'` 미이스케이프 → **innerHTML 조립 금지, createElement/textContent 사용**.
- **CSS**(`style.css` 3257줄, **다크 전용** — 테마 분기 없음): 재사용 가능 `.card/.toolbar/.tabs/.settings-table/.value-input/.btn*/.form-*/.alert-*/.loading/.spinner/.status-badge--*`, enum 세그먼트 원형 `.db-type-option.selected`(:3074), 뱃지 원형 `.step-data-badge--*`(:651), 아코디언 원형 `.pipeline-step.expanded`(:567). **부재(신규 작성)**: 모달/오버레이·아코디언·토글 스위치·태그 에디터·diff 하이라이트·검색 입력. **함정**: `--bg-tertiary`/`--text-primary`는 `:root`에 **미정의**(기존 코드 복붙 시 투명 배경 재생산) — 정의된 `--bg/--surface/--surface-hover/--text/--text-secondary/--text-muted/--border/--accent`만 사용.
- **감사**: admin.py는 현재 **기록 0건**(전부 `logger.info`). 쓰기 패턴은 `user_auth.py:66-91` `_log_audit_event` 이식 — `app.state.audit_service`(**None 가능**, `server.py:259-284`) → `AuditService.log`(JSONL+DB 이중 기록) → 폴백 `audit_repo.log_event`. `AuditEvent` enum(`domain/audit.py:15-39`)에 설정 멤버 없음 → `SETTINGS_UPDATE` 신설 + **dashboard.html:206-218 이벤트 필터 `<select>` 옵션 동반 추가**. IP/요청ID는 `request.state.client_ip/request_id`(AuditMiddleware가 세팅).
- **테스트**: 경로는 **`tests/test_api/`**(v1 §5의 `tests/api/`는 오타). 인증 우회는 `dependency_overrides`가 아니라 **라우트 함수 직접 호출 + `_admin={"sub":"admin"}` 주입**(`test_plan59a.py:35-58` 패턴). `.env` 격리는 `monkeypatch.setattr("src.api.routes.admin._ENV_FILE", tmp_path/".env")` + `GroupCls(_env_file=None)` 관용구(`test_admin_rbac.py:144`). `asyncio_mode="auto"`. e2e(Playwright)는 `RUN_E2E` 옵트인.

### 1.5 특이사항 (설계에 반영해야 할 함정)

1. **`.encenv` 6키는 `.env` 수정이 기능적으로 무효**(§1.2) → 카탈로그에서 `is_secret` + `managed_in: ".encenv"`로 편집 차단.
2. **OS env 오버라이드** → 실효값 병기 + 오버라이드 경고(§3.2 `override` 필드).
3. **`ENABLE_SEMANTIC_ROUTING` 무력화**: `config.py:780` `os.getenv` 잔존 — `.env`에 false를 저장해도 `ACTIVE_DB_IDS` 비어있지 않으면 강제 True. `enable_deepagent_orchestration`(`bool|None` 패턴, `:790`)과 동일 방식으로 교정하는 선행 수정 후보(§9 게이트 4). 교정 전까지는 카탈로그 설명에 이 동작을 명기.
4. **미소비 필드 16개**(전수 확인): `orchestrator.max_input_tokens`·`context_budget_ratio`, `query.max_retry_count`(재시도 상한 3은 `graph.py:60,100,114` 하드코딩)·`sufficiency_optional_threshold`, `security.partial_mask_columns`, `auth.default_allowed_db_ids`, `audit.sensitive_tables`·`night_alert_start`·`night_alert_end`, `alarm.prometheus_enabled`(게이팅 실체 없음), `noise_gate.debounce_seconds`·`correlation_field_weights_csv`·`business_hours_csv`·`ai_severity_escalate_only`, `conversation_max_turns`·`conversation_ttl_hours` → 카탈로그 `consumed=false`. **UI 기본 숨김 — 툴바 "미소비 필드 표시" 토글로만 노출**(노출 시 "미소비" 뱃지 + 편집 허용) [인터뷰 확정 2026-07-29]. ※ 삭제·배선은 본 계획 범위 외(별건).
5. **`.env`에 `NOISE_ENABLE_NOISE_GATE` 중복 2회 등재** — `_read_env_file`은 마지막 값, `_write_env_file`은 전 매칭 줄 갱신이라 동작은 되나 혼란 요인 → 저장 로직에 중복 키 정리(첫 줄 유지·이후 제거) 추가 + 착수 시 `.env` 일회 정리.
6. **모듈 임포트 부작용**: `server.py:612` `app = create_app()` — Phase 4(§6)를 하지 않는 한 건드리지 않는다(기존 동작 유지).

---

## 2. 설계 결정 (v2 확정)

1. **카탈로그 SSOT = pydantic 인트로스펙션**(D-129 후보). 파일 나열이 아니라 `AppConfig.model_fields` 순회로 224필드 전량 자동 도출 — config.py에 필드를 추가하면 UI에 자동 편입. `.env`/`.env.example`은 카탈로그의 원천이 아니라 **현재값·설명의 원천**으로만 사용.
2. **설명은 `.env.example` 주석 파싱** — 키 직전 연속 주석 블록을 도움말로 매핑. 없는 키는 카탈로그 오버라이드 dict로 보강(설명 부재가 노출을 막지 않음).
3. **시크릿 편집 차단(필수)**: `SecretStr` + `.encenv.example` 등재 키(9종) + `model_post_init` 보정 키. "설정됨/미설정" 상태만 read-only 표시. 근거: 정책(D-070/071) + **기능(§1.2 — .env 수정이 무효)**.
4. **반영 정책 = 정직한 재시작 필요 기본**: §1.3 확정표를 카탈로그 메타로 내장. 즉시 반영 예외 필드만 `requires_restart=false`. 저장 응답과 UI에 재시작 필요 키를 명시(침묵 금지). 핫 리로드 확대는 선택 Phase 4로 분리.
5. **실효값 병기**: 파일값(`_read_env_file`)과 실효값(`AppConfig()` 직접 인스턴스화 — lru_cache 우회·부작용 0, OS env/.encenv 반영된 값)을 함께 반환. 불일치 시 오버라이드 출처(os/encenv) 추정 표시.
6. **검증은 결정적 3단**: 필드 타입 검증 → 값 sanitize(개행·인라인 `#` 금지) → **그룹 클래스 단위 pydantic dry-run**(`GroupCls(_env_file=tmp)` — 실측 확정).
7. **저장 안전장치**: `.env.bak` 백업 → 임시 파일 → `os.replace` 원자 교체 → 실패 롤백 → `load_config.cache_clear()`(4개 즉시 경로용) → 감사 로그.
8. **범위 외**: `mcp_server/*`, `alarm_server/*`, `.encenv` 편집, `dbhub.toml`(deprecated), `config/*.yaml`, 미소비 필드 정리, `server.py` 임포트 구조 변경(Phase 4 제외 시). 기존 "DB 연결 설정" 탭 유지.

---

## 3. Phase 1 — 카탈로그 + 스키마 API + 서버 안전장치 (UI 무변경 배포 가능)

### 3.1 신규 모듈 `src/api/settings_catalog.py`

```python
# 공개 함수
def build_catalog() -> CatalogResponse          # 그룹 목록 + 메타 (아래 3.2)
def parse_env_example_descriptions() -> dict[str, str]   # .env.example 주석 블록 → {env_key: 설명}
def serialize_value(meta: SettingMeta, raw: object) -> str    # UI 값 → .env 표기 (§3.4 표)
def validate_updates(updates: dict[str, str]) -> list[FieldError]  # 3단 검증 (§3.5)

# 내부 인트로스펙션 규칙 (실측 확정)
# - AppConfig.model_fields 순회: annotation이 BaseSettings 서브클래스면 그룹, 아니면 top-level
# - env_key = validation_alias.choices[0]  (있으면)  else  env_prefix + field_name.upper()
# - PrivateAttr 자동 제외(model_fields에 없음), property 제외
# - Literal → typing.get_args()로 enum_choices
# - is_secret = (annotation is SecretStr) or (env_key in ENCENV_KEYS) or 수동 목록
#   ENCENV_KEYS = .encenv.example 파싱 + {LLM_OLLAMA_API_KEY 등 post_init 보정 키}
# - requires_restart / consumed = §1.3·§1.5-4 확정표를 모듈 상수 dict로 내장
#   (기본값: requires_restart=True — 미분류 신규 필드는 보수적으로 재시작 필요)
# - 그룹 순서·한국어 제목·noise_gate 하위 구획(Plan 52 E1~E5 / Plan 60 / Plan 64 CW)은 상수로 정의
```

### 3.2 API 명세

**`GET /api/v1/admin/settings/schema`** (신설, `require_admin_user`)

```jsonc
{
  "groups": [{
    "group_key": "llm", "title": "LLM 설정",
    "settings": [{
      "env_key": "LLM_PROVIDER",
      "type": "enum",                    // bool|int|float|enum|json_list|csv|string|tristate
      "enum_choices": ["ollama","fabrix","gemini"],
      "default": "ollama",               // 코드 기본값의 .env 표기
      "file_value": "gemini",            // .env 실존 값 (없으면 null = 기본값 사용 중)
      "effective_value": "gemini",       // AppConfig() 실효값 (마스킹 규칙 동일 적용)
      "override": null,                  // null | "os" | "encenv" — file≠effective 시 출처 추정
      "is_secret": false, "is_sensitive": false,
      "requires_restart": true, "consumed": true,
      "description": ".env.example 주석에서 추출한 도움말"
    }]
  }],
  "env_file_path": "/…/.env",
  "warnings": ["CWD가 프로젝트 루트와 다릅니다 — …"]   // §1.2 CWD 불일치 감지 시
}
```

**`PUT /api/v1/admin/settings`** (강화 — 요청 shape `{"settings": {k: v}}` 하위호환, `reset_keys: [str]` 필드 추가)

처리 순서:
1. `is_secret` 키 → 400 (".encenv에서 관리 — 웹UI 수정은 반영되지 않습니다").
2. 카탈로그 밖 키 → 400 (사유 명시).
3. 마스킹 값(`********`) 수신 → 해당 키 무시(서버측 원값 보존 가드 — 현재는 프론트만 방어).
4. sanitize: 값에 개행·`#` 포함 → 400 (인라인 주석 금지 — Known Mistakes).
5. 타입 검증: bool `true/false` 소문자, int/float 파싱, enum choices, json_list는 `json.loads`+list 확인, tristate(`auto`→줄 제거).
6. **그룹 dry-run**: 변경 키가 속한 그룹별로 "현재 .env + 변경분"을 임시 env 파일로 만들어 `GroupCls(_env_file=tmp)` 재구성 — ValidationError를 400으로 변환(필드별 사유).
7. 저장: `.env` → `.env.bak` 복사 → 임시 파일에 전체 기록(`_write_env_file` 로직 + **중복 키 정리** + `reset_keys` 줄 제거) → `os.replace` → 실패 시 `.env.bak` 복원.
8. `load_config.cache_clear()` (4개 즉시 경로용 — 효과 범위를 과장하지 않는다).
9. 감사 로그(§3.3).
10. 응답: `{"updated_keys": [...], "reset_keys": [...], "requires_restart_keys": [...], "applied_immediately_keys": [...], "message": ...}`.

`GET /api/v1/admin/settings`(기존)는 유지(하위호환) — Phase 2에서 UI가 schema로 전환한 뒤 deprecated 주석.

### 3.3 감사 로그 배선 (신규 — 현재 admin.py는 기록 0건)

- `domain/audit.py` `AuditEvent`에 `SETTINGS_UPDATE = "settings_update"` 추가.
- `user_auth.py:66-91` `_log_audit_event` 패턴 이식: `app.state.audit_service`(None 가드) → `AuditLogEntry(event=SETTINGS_UPDATE, user_id=_admin["sub"], client_ip=request.state.client_ip, extra={"changes": {...}, "reset_keys": [...]})`.
- **기록 범위 [인터뷰 확정 2026-07-29]**: **비민감 키는 `{key: {"old": 이전값, "new": 새값}}` 쌍을 기록**(변경 추적·수동 복구 가능), 민감 키(`is_sensitive`/`is_secret`)는 **키 이름만**(값 일절 미기록). old 값은 저장 직전 `_read_env_file()` 스냅샷에서 취득(파일 미존재 키는 `null` = 기본값 사용 중이었음).
- audit_service·audit_repo 둘 다 None이면 `logger.warning` + 응답 `message`에 "감사 기록 불가(감사 저장소 미구성)" 명시(침묵 금지).
- `dashboard.html:206-218` 감사 이벤트 필터 `<select>`에 `settings_update` 옵션 추가.

### 3.4 타입 → .env 직렬화 표

| 카탈로그 type | 판정 규칙 | .env 표기 |
|---|---|---|
| bool | `annotation is bool` | `true`/`false` 소문자 |
| tristate | `bool \| None` | `true`/`false`/줄 제거(auto) |
| int / float | annotation | 그대로 |
| enum | `Literal` | choices 값 그대로 |
| json_list | `list[str]` | `json.dumps(..., ensure_ascii=False)` |
| csv | 필드명 `*_csv` 또는 `*_ids`(POLESTAR_DB_IDS류 수동 지정) | 쉼표 결합(항목 트림) |
| string | 그 외 str/SecretStr(표시 전용) | 그대로(개행·`#` 금지) |

### 3.5 Phase 1 검증 (tests/test_api/test_settings_catalog.py — 경로 교정: v1의 `tests/api/`는 오타)

- **T1 커버리지 게이트**: `카탈로그 env_key ⊇ (.env ∪ .env.example 고유 키 165개) − ENCENV_KEYS` 실파싱 전수 단언 — 신규 키 누락 시 CI 실패. 역방향(카탈로그에만 있는 config 전용 키)은 허용.
- **T2 인트로스펙션**: alias(`ACTIVE_DB_IDS`)·top-level 무prefix·tristate·SecretStr→secret·`*_csv`→csv·Literal choices·그룹 수 17·필드 합 224 고정.
- **T3 저장 왕복**: `monkeypatch.setattr("src.api.routes.admin._ENV_FILE", tmp)` + 라우트 직접 호출(`test_plan59a.py` 패턴, `_admin={"sub":"admin"}`) — 타입별 저장→재파싱·그룹 재구성 값 일치, 주석·순서·무관 키 보존, `.env.bak` 생성, 중복 키 정리, `reset_keys` 줄 제거.
- **T4 검증 거부**: json_list 오형식/enum 밖/인라인 `#`/개행/bool 대문자/미지 키/secret 키 → 각 400 + 사유.
- **T5 민감 가드**: 마스킹 응답, `********` 수신 시 원값 보존. 감사 extra 검증 — 비민감 키는 old→new 쌍 기록, 민감/시크릿 키는 키 이름만(값 문자열이 extra 직렬화 결과에 부재함을 단언).
- **T6 requires_restart 메타**: 즉시 반영 예외 필드 목록(§1.3)이 카탈로그 메타와 일치.

---

## 4. Phase 2 — 웹UI 개편 (dashboard.html + admin.js + style.css)

### 4.1 통합 방식

- **admin.js 내부 in-place 교체**(권고): 설정 탭 코드(:171-263)를 카탈로그 기반 모듈로 교체. 별도 파일 분리는 `apiRequest`/`showError`가 IIFE 클로저에 갇혀 `window.AdminUI` 노출이 선행돼야 하므로 1차에서는 하지 않는다.
- **eager → lazy 전환**: `loadSettings()` 즉시 호출(:175)을 탭 최초 활성화 시 1회 로드로 변경(다른 탭들의 클릭마다 재요청 패턴은 답습하지 않고, 로드 후 메모리 상태 유지 — 미저장 편집 보존).
- `dashboard.html`에 `?v=` 캐시 버스팅 도입(index.html 규약 정렬).
- (권고) `apiRequest`에 401 → `admin_token` 제거 + `/login?next=/admin` 리다이렉트 추가(토큰 1회 캡처·만료 무안내 해소).

### 4.2 DOM 구조 (신규, `#tab-settings` 내부 교체)

```
.card
├─ .toolbar  (h2 + 검색 input#settingsSearch + 필터 select(전체/변경됨/기본값과 다름/재시작 필요) + 미소비 표시 토글#showUnconsumed(기본 off) + #saveSettingsBtn)
├─ .settings-banner#restartBanner (숨김 — 저장 후 재시작 필요 키 안내)
├─ .settings-accordion
│   └─ .settings-group (×17+top-level, noise_gate는 하위 구획 소제목)
│       ├─ .settings-group-header (제목 + 변경 카운트 뱃지 + 펼침 토글)
│       └─ .settings-group-body
│           └─ .setting-row (×N)
│               ├─ .setting-label (env_key + 뱃지: [재시작]·[미소비]·[🔒 .encenv]·[OS 오버라이드] + 도움말 아이콘→설명)
│               ├─ .setting-widget (타입별 §4.3)
│               └─ .setting-state ("기본값 사용 중" 흐림 / 변경 하이라이트 / "기본값으로" 버튼)
└─ #settingsDiffModal (diff 확인 모달 — 변경 전→후 목록, 재시작 필요 표시, 확인/취소)
```

### 4.3 타입별 위젯

**옵션별 확정 구성(224필드 전수 — 위젯·기본값·반영 시점·뱃지)은 부록 A** 참조. 아래는 타입→위젯 매핑 규칙.

| type | 위젯 | 비고 |
|---|---|---|
| bool | 토글 스위치(신규 CSS) | |
| tristate | 세그먼트 3버튼(auto/true/false) — `.db-type-option` 원형 재사용 | auto=줄 제거 |
| enum | 세그먼트 또는 `<select>`(choices 4개 초과 시) | |
| int/float | `input[type=number]` | 범위 검증은 서버 위임 |
| json_list / csv | 태그(칩) 에디터(신규) — 내부 직렬화로 사용자가 JSON 문법을 직접 다루지 않음 | |
| string | `input[type=text]` | |
| secret | read-only "설정됨/미설정" 뱃지 | 편집 경로 없음 |

구현 규칙: **모든 렌더링은 createElement/textContent**(escapeHtml `'` 미이스케이프 함정 회피, 설명은 한국어 주석이라 따옴표 빈발). 필드 인라인 에러는 `.setting-row` 하단에 표시(전역 `#alertError`는 통신 오류용으로만). 422 응답은 `detail`이 리스트일 수 있음을 처리.

### 4.4 CSS 신규 클래스 (style.css 말미 추가, **정의된 변수만 사용**: `--bg/--surface/--surface-hover/--text/--text-secondary/--text-muted/--border/--accent`)

`.settings-accordion`, `.settings-group(-header/-body)`(max-height 트랜지션 — `.pipeline-step.expanded` 원형), `.toggle-switch`, `.tag-editor(.tag-chip)`, `.setting-row/-label/-widget/-state`, `.badge--restart/--unconsumed/--secret/--override`(`.step-data-badge--*` 원형), `.settings-banner`, `.modal-overlay/.modal`(신규 — 기존 모달 CSS 전무), `.diff-line--old/--new`. ※ `--bg-tertiary`/`--text-primary`는 미정의 변수 — 사용 금지.

### 4.5 저장 흐름

변경 수집(파일값 대비 dirty만) → diff 모달(전→후, 재시작 뱃지) → `PUT`(settings + reset_keys) → 성공: `requires_restart_keys`가 있으면 `#restartBanner` 표시("다음 항목은 서버 재시작 후 반영됩니다: …"), `applied_immediately_keys`는 "다음 요청부터 반영" 안내 → schema 재로드. 실패: 필드별 인라인 에러 + 전역 배너.

### 4.6 Phase 2 검증

- 라우트 계약 테스트는 Phase 1 T1~T6로 커버(UI는 순수 클라이언트).
- 수동 점검 체크리스트: 그룹 17개 전개·검색·필터·타입별 위젯 왕복·secret read-only·diff 모달·재시작 배너·401 리다이렉트.
- (선택) Playwright e2e 1건(`tests/e2e/`, `RUN_E2E` 옵트인·기존 MockGraph 하네스): 설정 탭 로드→값 변경→저장→재로드 값 확인.

---

## 5. Phase 3 — 마감

- §1.3 확정표의 잔여 불확실 필드(synonym 일부 경로, schema_cache 라우트 한정 fresh 등) 실측 보완 후 카탈로그 메타 확정. LLM 실 호출이 필요한 확인은 **D-127 건별 승인** 후에만.
- `.env` 정리 1회: `NOISE_ENABLE_NOISE_GATE` 중복 제거.
- `docs/02_decision.md` D-129 등재(채번 재실측 + 안내 라인 갱신), `.env.example` 헤더에 웹UI 편집 안내 1줄.
- **[게이트 4 확정]** `config.py:776-791` `enable_semantic_routing`을 `bool | None` 패턴으로 교정(`enable_deepagent_orchestration`과 동일 방식) + 기존 자동 활성 동작(미설정 + `ACTIVE_DB_IDS` 존재 시 True) 보존 테스트. 교정 후 UI 위젯을 토글→tristate(auto·true·false)로 전환(부록 A.18).

---

## 6. Phase 4 — 즉시 반영 범위 확대 [게이트 5: 1차 제외 → **2026-07-30 사용자 재론 지시로 구현 완료(§6.2 / D-135)**]

현 구조에서 재시작 없이 반영을 넓히려면(소비 지점 분석 권고):

1. `POST /admin/settings/reload` 신설: `load_config.cache_clear()` → fresh `AppConfig()` → `app.state.config` 교체 — (a)류 라우트 계열(auth/admin/server 타임아웃/audit 판정/alarm 라우트)이 재시작 없이 반영됨.
2. 그래프 재빌드: `build_graph(fresh_config)` + `app.state.graph` 교체(체크포인터 재사용) — llm/text2sql/synonym/query 계열 반영.
3. `reset_cache_manager()`(현재 prod 호출부 0건)·`synonym_semantic._reset_state_for_tests()` 연동 — schema_cache/redis 반영.

**리스크**: 처리 중 요청과의 원자성(교체 시점 경합), 재빌드 중 LLM/오케스트레이터 health 호출 발생 가능(과금 게이트 D-127 검토 필요), AlarmWorker·CORS·uvicorn host/port는 **구조상 불가**(재시작 유일). 범위가 별도 계획 규모이므로 본 계획에서는 엔드포인트 자리만 예약하고 기본 미구현.

### 6.1 재론 분석 (2026-07-30 실측 — 사용자 재론 요청 접수)

사용자 요청("admin 설정 화면 대부분이 재시작 표기 — 재시작 없이 반영 확대 가능 여부 확인") = 게이트 5의 "운영 불편 실증" 재론 트리거. 아래는 코드 재실측 결과이며, **게이트 5 결정(1차 제외) 자체는 유지** — 구현 착수는 사용자 승인 시 별도 진행.

**현황 재확인 (v2 분석과 코드 일치 검증 완료)**

- 즉시 반영은 `IMMEDIATE_KEYS` 7키뿐(`settings_catalog.py:190` — `DBHUB_BEARER_TOKEN`, synonym 매칭 4종, `SYNONYM_GOVERNANCE`, `SCHEMA_CACHE_AUTO_GENERATE_DESCRIPTIONS`). 나머지 전부 `requires_restart=True`(`:444`).
- 고정 캡처 3곳 재검증: ① `server.py:249` `app.state.config` 고정(라우트 계열 — dependencies/query/alarm/user_auth/admin_auth/admin/health, `request.app.state.config` 소비 25곳) ② `server.py:248` `build_graph(config)` → `graph.py:302~` 노드 전량 partial 주입 ③ 워커·싱글톤(`server.py:320` `AlarmWorker(config)`, SSE 브리지 `:332~`, 감사 로테이션 `:300`, `cache_manager.py:1737` 싱글톤, synonym 임베더 영구 래치).

**단계별 방안 (v2 §6 1~3항의 실측 보강)**

| 단계 | 내용 | 반영 확대 범위 | 실측 보강 |
|---|---|---|---|
| 1 | `cache_clear()` → fresh `AppConfig()` → `app.state.config` 재대입 + `setup_logging(log_level)` 재호출 | 라우트가 읽는 설정 전반(auth/admin 인증 정책·감사 판정·alarm 라우트·쿼리 정책) + 로그 레벨 | 참조 교체는 원자적, in-flight 요청은 잡아둔 옛 config로 완주 — v2가 우려한 경합 리스크는 낮음(효과 대비 위험 최소 → **우선 착수 권장**) |
| 2 | `build_graph(fresh_config)` → `app.state.graph` 교체(체크포인터 재사용) | llm·orchestrator·text2sql·synonym·query·multi_db·security 등 노드 계열 | **D-127 리스크 해소(실측)**: 빌드 시점 외부 호출은 deepagents 경로 활성 시 vLLM `/models` health GET 1회뿐(`deep_agent.py:44` — 로컬·비과금), gemini 오케스트레이터는 키 유무 판정만(`:66`), `create_llm`은 클라이언트 객체 생성만 — **과금 API 호출 0**. 그래프 참조 교체도 원자적, 진행 중 질의는 옛 그래프로 완주 |
| 3 | `reset_cache_manager()`(`cache_manager.py:1758`, prod 호출부 0건 유지 확인) + 임베더 래치 리셋 연동 | schema_cache `backend`/`cache_dir` 계열, synonym `semantic_backend`/`semantic_model_path` 계열 | 리셋 후 다음 접근 시 모델·캐시 재로드 비용 발생(1회성 지연) |

**UI 파급**: 배지·저장 배너 모두 카탈로그 `requires_restart` 메타에서 자동 파생(`settings_catalog.py:444`, `admin.js:467,850,949`) → 서버 측 키 분류 확장만으로 화면 자동 갱신. 단 리로드 도입 시 "재시작 필요/리로드 시 반영/즉시 반영" **3분류**로 메타 확장 검토(현행 2분류 boolean).

**구조상 재시작 유일 (확정 목록)**

- uvicorn host/port/workers(`main.py` 기동 인자), CORS origins(`server.py:529` 앱 생성 시 고정)
- AlarmWorker·SSE 브리지·감사 로테이션 태스크가 캡처한 설정(alarm·noise_gate 대부분) — 태스크 cancel 후 재기동으로 가능은 하나 처리 중 알람 유실 리스크 → 별도 판단
- 체크포인터 종류·경로, 인증 DB 풀(`auth_db_url` — 풀 재생성 필요)
- OS env/`.encenv` 오버라이드 키(리로드해도 `.env` 값 무시 — 기존 오버라이드 뱃지로 이미 표시)
- 별도 프로세스(`alarm_server`/`mcp_server`) — 본 계획 범위 외 유지

**잔여 확인**: 필드 단위 최종 분류(리로드 반영 vs 재시작)는 §8 R3식 소비 지점 실측을 착수 시 1회 재수행해 확정. reload 트리거 방식(저장 시 자동 vs 명시 "반영" 버튼 — 게이트 2 "재시작 버튼 제외"와는 별개 사안)은 착수 게이트로 사용자 확정 필요. → **§6.2에서 해소(2026-07-30)**.

### 6.2 구현 기록 (2026-07-30 — 사용자 지시 "미구현 부분 구현", D-135)

**소비 지점 전수 재실측(§6.1 잔여 확인 이행).** 3개 병렬 탐색 에이전트로 226필드의 소비 지점을 file:line 단위 재매핑 — ①라우트·서버 골격(top-level/server/admin/auth/audit/redis) ②알람·워커 계열(alarm/noise_gate/workb) ③그래프·파이프라인 계열(llm/orchestrator/text2sql/query/security/synonym/multi_db/dbhub/schema_cache). 판정 규칙: 소비처가 기동 캡처(W — AlarmWorker·SSE 브리지·감사 태스크/서비스·CORS·uvicorn·체크포인터·인증 풀)를 하나라도 포함하면 restart(보수 기본), 요청 시점(R)·그래프 스코프(G)·리셋 가능 싱글톤(S)만이면 reload. 결과: **immediate 7 / reload 78 / restart 148** (`settings_catalog.py`의 `RELOADABLE_KEYS` 확정표가 SSOT — 주석에 그룹별 근거 명기).

**구현 확정 사항.**

1. **`POST /admin/settings/reload`** (admin.py) — 처리 순서: `cache_clear()` → fresh `AppConfig`(실패 400·기존 유지) → **JWT 자동생성 시크릿 승계**(`_jwt_secret_explicit` 아닐 때만 — 개발 모드 리로드가 전 토큰을 무효화해 운영자 세션이 즉시 로그아웃되는 함정 차단, 운영 모드는 `.encenv` 고정이라 무영향) → **운영 게이트 `_validate_production_secrets` 재실행**(D-071 fail-closed — 이 재실행 덕에 `AUTH_ENABLED`를 reload로 강등 가능) → 로그 레벨 변경 시 `setup_logging` 재적용(`LOG_LEVEL` reload 승격 근거) → **그래프 재빌드**(`app.state.checkpointer` 재사용 — server.py가 lifespan에서 보관하도록 1줄 추가, 스레드풀 실행, 실패 500·기존 유지) → **싱글톤 리셋 3종**(스키마 캐시는 disconnect 후 `reset_cache_manager()` — 실측 지적 "리셋만 하면 Redis 연결 누수" 반영, 질의 이력 `reset_query_history_store()`, 임베더 `reset_embedder_state()` — `_reset_state_for_tests`를 공개 함수로 개명) → `app.state.graph`/`config` **원자 교체**(처리 중 요청은 옛 객체로 완주) → redis 백엔드면 lifespan과 동일하게 즉시 재연결 확인 → `SETTINGS_RELOAD` 감사(**키 이름만**, 값 미기록) + 대시보드 감사 필터 옵션 추가.
2. **응답 계약**: `changed_keys`(실효값 diff — `diff_effective_keys`, 시크릿 제외·값 미노출)·`restart_only_keys`(바뀌었지만 기동 캡처 소비라 재시작 필요)·`graph_rebuilt`·`message`. 저장(`PUT`) 응답도 3분류로 확장 — `reload_keys` 신설, `requires_restart_keys`는 restart 전용으로 의미 축소.
3. **알람 워커 비대칭(양 에이전트 판정 상충의 해소)**: `LLM_*`·`DBHUB_*`·`ACTIVE_DB_IDS`는 질의 그래프(G — 재빌드로 반영)와 알람 워커(W — 캡처본 유지)가 공동 소비. 엄격 W-규칙이면 restart지만 §6이 정의한 리로드 효과 범위(질의 경로)를 기준으로 **reload로 분류**하고, 워커 활성+해당 키 변경 시 응답 메시지에 미반영 키를 명시(침묵 금지). **워커 재기동은 계속 범위 외**(처리 중 알람 유실·노이즈 게이트 in-memory 상태(플래핑·스톰·중복 억제) 초기화 리스크 — 별도 판단 유지).
4. **UI**: `apply_mode` 기반 "리로드" 뱃지(구버전 캐시 대비 `requires_restart` 폴백)·필터 옵션 "리로드 반영"·툴바 [설정 리로드] 버튼(미저장 변경 존재 시 거부)·저장 배너 3분류·diff 모달 뱃지. **트리거 방식 확정: 명시 버튼**(저장 시 자동 리로드 아님 — 매 저장마다 그래프 재빌드 방지 + 운영자가 반영 시점 통제). 캐시 버스팅 v 갱신.
5. **카탈로그 메타**: `FieldSpec`/`SettingSchemaItem`에 `apply_mode` 추가, `requires_restart`는 하위호환 유지(= not immediate — 기존 T6 의미 보존). **미소비 4건 추가 등재(16→20)**: `ORCHESTRATOR_MAX_HISTORY_TURNS`(D-129 부기 확인 건 해소)·`SYNONYM_DECAY_DAYS`·`ALARM_PROMETHEUS_BASE_URLS_CSV`·`ALARM_PROMETHEUS_TIMEOUT_SECONDS`(에이전트 판정을 워드 경계 grep으로 재검증 후 반영).

**검증.** 신규 `tests/test_api/test_settings_reload.py` 10건(교체·no-op diff·restart_only 보고·로드/빌드 실패 시 기존 상태 유지·게이트 거부·시크릿 승계·로그레벨 조건부 재적용·감사 키만 기록·워커 비대칭 경고 유/무) + 카탈로그 46건(3분류 파티션 일치·리로드 키 시크릿 0·스팟 체크·미소비 20). 실패 4건은 클린 HEAD worktree 동일 재현(기존 실패 — 회귀 0), `arch_check --ci` 0, **외부 호출 0**(재빌드 시 vLLM health GET은 deepagents 활성 시 1회·로컬 비과금 — D-127 저촉 없음 실측).

**잔여 주의(운영 가이드).** ① `ENABLE_SQL_APPROVAL`/`ENABLE_STRUCTURE_APPROVAL` 변경 리로드는 `interrupt_before` 구성이 바뀌어 승인 대기 중 스레드가 재개 불능이 될 수 있다 — 승인 대기가 없는 시점에 리로드 권장. ② OS env/`.encenv` 오버라이드 키는 리로드로도 불변(오버라이드 뱃지가 이미 경고). ③ YAML 파생 전역 캐시(`semantic_compiler._MODEL_CACHE`·위치 힌트 모듈 전역 `_LOCATION_DB_HINTS` 계열)는 env 리로드 범위 밖 — YAML 변경은 재시작 유일(별건). ④ `AUTH_ENABLED` 리로드 활성화 시 seed admin 부트스트랩은 미실행 — break-glass env 로그인은 요청 시점 판정이라 가능.

---

## 7. 산출물 목록

| Phase | 파일 | 내용 |
|---|---|---|
| 1 | `src/api/settings_catalog.py` (신규) | 인트로스펙션 카탈로그·주석 파서·직렬화/검증·restart/consumed 메타 |
| 1 | `src/api/routes/admin.py` | schema GET 신설, PUT 강화(검증·백업·원자 쓰기·reset_keys·감사·restart 응답) |
| 1 | `src/domain/audit.py` | `SETTINGS_UPDATE` 이벤트 추가 |
| 1 | `tests/test_api/test_settings_catalog.py` (신규) | T1~T6 |
| 2 | `src/static/admin/dashboard.html` | 설정 탭 DOM 교체·감사 필터 옵션·캐시 버스팅 |
| 2 | `src/static/js/admin.js` | 설정 모듈 교체(lazy·위젯·diff 모달·401 처리) |
| 2 | `src/static/css/style.css` | §4.4 신규 클래스 |
| 3 | `docs/02_decision.md`, `.env.example`, `.env` | D-129 등재·안내·중복 키 정리 |
| 3 | `src/config.py` | `enable_semantic_routing` `bool\|None` 교정 (게이트 4 확정) |
| 4 | `src/api/routes/admin.py` | `POST /admin/settings/reload`·응답 3분류(`reload_keys`)·감사 헬퍼 일반화 |
| 4 | `src/api/settings_catalog.py` | `RELOADABLE_KEYS` 확정표·`apply_mode`·`diff_effective_keys`·미소비 +4 |
| 4 | `src/api/server.py` | `app.state.checkpointer` 보관(재빌드 재사용) |
| 4 | `src/schema_cache/synonym_semantic.py` | `reset_embedder_state` 공개화 |
| 4 | `src/domain/audit.py` | `SETTINGS_RELOAD` 이벤트 추가 |
| 4 | UI 3종 + `dashboard.html` 감사 필터 | 리로드 뱃지·필터·버튼·배너 3분류·캐시 버스팅 |
| 4 | `tests/test_api/test_settings_reload.py` (신규) | 리로드 10건 |
| 4 | `docs/02_decision.md` | D-135 등재 |

## 8. 실측 잔여 항목 (착수 후 우선 해소)

- **R2**: `.env.example` 주석 파서 매핑률 실측 → 오버라이드 dict 규모 확정.
- **R3**: §1.3 혼재 그룹의 필드 단위 재검(특히 synonym·schema_cache) — Phase 3.
- **R4**: `AppConfig()` 직접 인스턴스화(실효값 계산)의 소요 시간 — GET schema 응답 지연 허용 범위 확인(문제 시 짧은 TTL 캐시).

## 9. 착수 게이트 — **전건 확정 (2026-07-29 사용자 승인, 권고안 채택)**

1. **시크릿 웹UI 편집** → **확정: 차단.** `.encenv` 관리 키는 read-only 상태 표시만(기능 근거 §1.2: `.env` 수정이 무효). 편집이 필요해지면 `.encenv` 쓰기 경로 신설을 별도 결정.
2. **재시작 버튼** → **확정: 제외.** 안내 배너만 제공.
3. **범위** → **확정: `mcp_server`/`alarm_server` 범위 외 유지.**
4. **`ENABLE_SEMANTIC_ROUTING` 교정** → **확정: Phase 3에서 `bool|None` 패턴으로 교정**(§5).
5. **Phase 4(즉시 반영 확대)** → **확정: 1차 제외.** 운영 불편 실증 후 별도 계획으로 재론(§6은 자리예약).

### §9-보완. 사용자 인터뷰 확정 (2026-07-29)

| # | 질문 | 확정 | 계획 반영 위치 |
|---|---|---|---|
| 6 | 설정 탭 레이아웃 | **아코디언 단일 페이지**(현안 유지) — 검색·필터 툴바 + 그룹 접기/펼치기 | §4.2 |
| 7 | 미소비 필드 16개 | **기본 숨김 + "미소비 필드 표시" 토글**로만 노출(노출 시 뱃지+편집 허용) | §1.5-4·§4.2·부록 A |
| 8 | 감사 기록 범위 | **비민감 키는 이전값→새값 쌍 기록**, 민감/시크릿 키는 키 이름만 | §3.3·T5 |
| 9 | 착수 방식 | **Phase 1~3 일괄 구현** 후 최종 보고(중간 승인 없음) | 헤더 진행 방식 |

## 10. 성공 기준

1. `.env` ∪ `.env.example` 전 옵션 165키(시크릿 제외) + config.py 전용 필드가 그룹·설명·타입 위젯·재시작/미소비/오버라이드 뱃지와 함께 웹UI에서 조회·수정 가능 — **T1이 CI에서 전수 보증**.
2. config.py에 필드 추가 시 코드 수정 없이 UI 자동 편입(보수적 기본 메타: 재시작 필요).
3. 형식 오류(JSON 배열·enum·인라인 주석·개행)가 저장 전 차단되고 필드별 사유가 표시된다.
4. 저장은 백업·원자 교체·롤백으로 보호되고, 변경 키 목록이 감사 로그에 남으며(값 제외), 반영 시점(재시작 필요/다음 요청)이 침묵 없이 사용자에게 표시된다.
5. `.encenv`/OS env 오버라이드로 "저장했는데 안 바뀌는" 상황이 UI에서 사전에 경고된다.

---

## 부록 A. 옵션별 웹UI 구성 확정표 (224필드 전수 — config.py 인트로스펙션 생성)

**생성 방법**: `AppConfig.model_fields` 인트로스펙션 스크립트로 자동 생성한 스냅샷(2026-07-29, §4.3 매핑 규칙 + §1.3 반영 분류 + §1.5-4 미소비 목록 적용). **구현의 SSOT는 카탈로그 생성기**(`settings_catalog.py`)이며 config.py 변경으로 본 표와 어긋나면 생성기가 정답이다.

**열 규약**:
- **위젯**: `토글`(bool) / `세그먼트: a·b·c`(enum ≤4) / `셀렉트: …`(enum >4 또는 미설정 포함) / `숫자(int|float)` / `태그(JSON 배열)`(list — 내부적으로 JSON 직렬화) / `태그(CSV)`(쉼표 결합) / `텍스트` / `🔒 read-only(.encenv 관리)`(게이트 1 확정 — 편집 차단, "설정됨/미설정" 뱃지만). `※` = str 타입 필드지만 카탈로그 choices 오버라이드로 enum 위젯 적용(config.py 주석의 허용값 기준).
- **반영**: `재시작` = 저장 후 서버 재시작 필요(뱃지+배너, §1.3) / `다음 요청` = cache_clear 후 다음 요청부터 반영 / `—` = 편집 불가.
- **비고**: `**미소비**` = 현재 코드가 읽지 않는 필드(§1.5-4) — **기본 숨김**이며 툴바 "미소비 필드 표시" 토글 시에만 노출(뱃지+편집 허용) [인터뷰 확정].
- 모든 행 공통: "기본값 사용 중"(파일 미설정) 흐림 표시·기본값 리셋 버튼·OS env/.encenv 오버라이드 경고 뱃지(§3.2 `override`)·`.env.example` 주석 도움말 툴팁이 적용된다(§4.2).
- 그룹 배치: A.1~A.18이 아코디언 순서다. `알람`·`노이즈 게이트`는 표의 "구획" 값이 하위 소제목이 된다(§4.2).

### A.1 LLM (`cfg.llm` · 11필드)
| env 키 | 위젯 | 기본값 | 반영 | 비고 |
|---|---|---|---|---|
| `LLM_PROVIDER` | 세그먼트: ollama·fabrix·gemini | `ollama` | 재시작 |  |
| `LLM_MODEL` | 텍스트 | `llama3.1:8b` | 재시작 |  |
| `LLM_OLLAMA_BASE_URL` | 텍스트 | `http://localhost:11434` | 재시작 |  |
| `LLM_OLLAMA_API_KEY` | 🔒 read-only(.encenv 관리) |  | — |  |
| `LLM_OLLAMA_TIMEOUT` | 숫자(int) | `180` | 재시작 |  |
| `LLM_GEMINI_API_KEY` | 🔒 read-only(.encenv 관리) |  | — |  |
| `LLM_GEMINI_MODEL` | 텍스트 |  | 재시작 |  |
| `LLM_FABRIX_BASE_URL` | 텍스트 |  | 재시작 |  |
| `LLM_FABRIX_API_KEY` | 🔒 read-only(.encenv 관리) |  | — |  |
| `LLM_FABRIX_CLIENT_KEY` | 🔒 read-only(.encenv 관리) |  | — |  |
| `LLM_FABRIX_CHAT_MODEL` | 텍스트 |  | 재시작 |  |

### A.2 오케스트레이터(deepagents) (`cfg.orchestrator` · 12필드)
| env 키 | 위젯 | 기본값 | 반영 | 비고 |
|---|---|---|---|---|
| `ORCHESTRATOR_PROVIDER` | 세그먼트: vllm·gemini | `vllm` | 재시작 |  |
| `ORCHESTRATOR_BASE_URL` | 텍스트 |  | 재시작 |  |
| `ORCHESTRATOR_MODEL` | 텍스트 | `Qwen3.5-9B` | 재시작 |  |
| `ORCHESTRATOR_API_KEY` | 🔒 read-only(.encenv 관리) |  | — |  |
| `ORCHESTRATOR_TIMEOUT` | 숫자(int) | `120` | 재시작 |  |
| `ORCHESTRATOR_HEALTH_TIMEOUT` | 숫자(int) | `3` | 재시작 |  |
| `ORCHESTRATOR_VERIFY_SSL` | 토글 | `true` | 재시작 |  |
| `ORCHESTRATOR_MAX_INPUT_TOKENS` | 숫자(int) | `12000` | 재시작 | **미소비** |
| `ORCHESTRATOR_CONTEXT_BUDGET_RATIO` | 숫자(float) | `0.8` | 재시작 | **미소비** |
| `ORCHESTRATOR_MAX_TOOL_RESULT_TOKENS` | 숫자(int) | `2000` | 재시작 |  |
| `ORCHESTRATOR_MAX_HISTORY_TURNS` | 숫자(int) | `6` | 재시작 |  |
| `ORCHESTRATOR_ENABLE_THINKING` | 토글 | `false` | 재시작 |  |

### A.3 DBHub(MCP) (`cfg.dbhub` · 4필드)
| env 키 | 위젯 | 기본값 | 반영 | 비고 |
|---|---|---|---|---|
| `DBHUB_SERVER_URL` | 텍스트 | `http://localhost:9099/sse` | 재시작 |  |
| `DBHUB_SOURCE_NAME` | 텍스트 |  | 재시작 |  |
| `DBHUB_MCP_CALL_TIMEOUT` | 숫자(int) | `60` | 재시작 |  |
| `DBHUB_BEARER_TOKEN` | 🔒 read-only(.encenv 관리) |  | — |  |

### A.4 쿼리 정책 (`cfg.query` · 4필드)
| env 키 | 위젯 | 기본값 | 반영 | 비고 |
|---|---|---|---|---|
| `QUERY_MAX_RETRY_COUNT` | 숫자(int) | `3` | 재시작 | **미소비** |
| `QUERY_DEFAULT_LIMIT` | 숫자(int) | `1000` | 재시작 |  |
| `QUERY_SUFFICIENCY_REQUIRED_THRESHOLD` | 숫자(float) | `0.7` | 재시작 |  |
| `QUERY_SUFFICIENCY_OPTIONAL_THRESHOLD` | 숫자(float) | `0.5` | 재시작 | **미소비** |

### A.5 동의어 매칭 (`cfg.synonym` · 13필드)
| env 키 | 위젯 | 기본값 | 반영 | 비고 |
|---|---|---|---|---|
| `SYNONYM_FUZZY_MATCH` | 토글 | `false` | 다음 요청 |  |
| `SYNONYM_VALUE_RETRIEVAL` | 토글 | `false` | 재시작 |  |
| `SYNONYM_SEMANTIC_MATCH` | 토글 | `false` | 다음 요청 |  |
| `SYNONYM_SEMANTIC_BACKEND` | 세그먼트: local·vllm | `local` | 재시작 |  |
| `SYNONYM_SEMANTIC_MODEL_PATH` | 텍스트 |  | 재시작 |  |
| `SYNONYM_SEMANTIC_VLLM_BASE_URL` | 텍스트 |  | 재시작 |  |
| `SYNONYM_SEMANTIC_VLLM_MODEL` | 텍스트 |  | 재시작 |  |
| `SYNONYM_SEMANTIC_VLLM_VERIFY_SSL` | 토글 | `true` | 재시작 |  |
| `SYNONYM_SEMANTIC_CONFIDENCE_MIN` | 숫자(float) | `0.65` | 다음 요청 |  |
| `SYNONYM_MATCH_CONFIDENCE_MIN` | 숫자(float) | `0.85` | 다음 요청 |  |
| `SYNONYM_MAX_SYNONYM_SUPPLEMENT_TABLES` | 숫자(int) | `15` | 재시작 |  |
| `SYNONYM_GOVERNANCE` | 토글 | `false` | 다음 요청 |  |
| `SYNONYM_DECAY_DAYS` | 숫자(int) | `180` | 재시작 |  |

### A.6 Text2SQL (`cfg.text2sql` · 9필드)
| env 키 | 위젯 | 기본값 | 반영 | 비고 |
|---|---|---|---|---|
| `TEXT2SQL_SEMANTIC_COMPOSE` | 토글 | `false` | 재시작 |  |
| `TEXT2SQL_SEMANTIC_FALLBACK` | 세그먼트: candidate_then_human·llm·human | `candidate_then_human` | 재시작 |  |
| `TEXT2SQL_FALLBACK_CONFIDENCE_MIN` | 숫자(float) | `0.0` | 재시작 |  |
| `TEXT2SQL_MULTI_CANDIDATE` | 토글 | `false` | 재시작 |  |
| `TEXT2SQL_CANDIDATE_COUNT` | 숫자(int) | `3` | 재시작 |  |
| `TEXT2SQL_CANDIDATE_STRATEGIES` | 세그먼트: temperature·multi_prompt | `multi_prompt` | 재시작 |  |
| `TEXT2SQL_COMPLEXITY_GATE` | 토글 | `false` | 재시작 |  |
| `TEXT2SQL_SELECTION` | 세그먼트: consistency·llm·hybrid | `hybrid` | 재시작 |  |
| `TEXT2SQL_GENERIC_LLM_MAPPING` | 토글 | `false` | 재시작 |  |

### A.7 보안 마스킹 (`cfg.security` · 5필드)
| env 키 | 위젯 | 기본값 | 반영 | 비고 |
|---|---|---|---|---|
| `SECURITY_SENSITIVE_COLUMNS` | 태그(JSON 배열) | `["password", "passwd", "pwd", "secret…]` | 재시작 |  |
| `SECURITY_MASK_PATTERN` | 텍스트 | `***MASKED***` | 재시작 |  |
| `SECURITY_PARTIAL_MASK_COLUMNS` | 태그(JSON 배열) | `[]` | 재시작 | **미소비** |
| `SECURITY_MASK_IP` | 토글 | `false` | 재시작 |  |
| `SECURITY_MASK_EMAIL` | 토글 | `false` | 재시작 |  |

### A.8 API 서버 (`cfg.server` · 5필드)
| env 키 | 위젯 | 기본값 | 반영 | 비고 |
|---|---|---|---|---|
| `API_HOST` | 텍스트 | `0.0.0.0` | 재시작 |  |
| `API_PORT` | 숫자(int) | `8000` | 재시작 |  |
| `API_CORS_ORIGINS` | 태그(JSON 배열) | `["*"]` | 재시작 |  |
| `API_QUERY_TIMEOUT` | 숫자(int) | `60` | 재시작 |  |
| `API_FILE_QUERY_TIMEOUT` | 숫자(int) | `120` | 재시작 |  |

### A.9 운영자 인증 (`cfg.admin` · 4필드)
| env 키 | 위젯 | 기본값 | 반영 | 비고 |
|---|---|---|---|---|
| `ADMIN_USERNAME` | 텍스트 |  | 재시작 |  |
| `ADMIN_PASSWORD` | 🔒 read-only(.encenv 관리) |  | — |  |
| `ADMIN_JWT_SECRET` | 🔒 read-only(.encenv 관리) |  | — |  |
| `ADMIN_JWT_EXPIRE_HOURS` | 숫자(int) | `24` | 재시작 |  |

### A.10 사용자 인증 (`cfg.auth` · 8필드)
| env 키 | 위젯 | 기본값 | 반영 | 비고 |
|---|---|---|---|---|
| `AUTH_ENABLED` | 토글 | `false` | 재시작 |  |
| `AUTH_AUTH_DB_URL` | 텍스트 |  | 재시작 | 접속 문자열 — 비밀번호 포함 시 마스킹 표시 |
| `AUTH_JWT_SECRET` | 🔒 read-only(.encenv 관리) |  | — |  |
| `AUTH_JWT_EXPIRE_HOURS` | 숫자(int) | `8` | 재시작 |  |
| `AUTH_MAX_LOGIN_ATTEMPTS` | 숫자(int) | `5` | 재시작 |  |
| `AUTH_LOCKOUT_MINUTES` | 숫자(int) | `30` | 재시작 |  |
| `AUTH_PASSWORD_MIN_LENGTH` | 숫자(int) | `8` | 재시작 |  |
| `AUTH_DEFAULT_ALLOWED_DB_IDS` | 텍스트 |  | 재시작 | **미소비** |

### A.11 멀티 DB (`cfg.multi_db` · 1필드)
| env 키 | 위젯 | 기본값 | 반영 | 비고 |
|---|---|---|---|---|
| `ACTIVE_DB_IDS` | 태그(CSV) |  | 재시작 | 그래프 토폴로지 결정 — 변경 영향 大 |

### A.12 Redis (`cfg.redis` · 6필드)
| env 키 | 위젯 | 기본값 | 반영 | 비고 |
|---|---|---|---|---|
| `REDIS_HOST` | 텍스트 | `localhost` | 재시작 |  |
| `REDIS_PORT` | 숫자(int) | `6379` | 재시작 |  |
| `REDIS_DB` | 숫자(int) | `0` | 재시작 |  |
| `REDIS_PASSWORD` | 🔒 read-only(.encenv 관리) |  | — |  |
| `REDIS_SSL` | 토글 | `false` | 재시작 |  |
| `REDIS_SOCKET_TIMEOUT` | 숫자(int) | `5` | 재시작 |  |

### A.13 스키마 캐시 (`cfg.schema_cache` · 5필드)
| env 키 | 위젯 | 기본값 | 반영 | 비고 |
|---|---|---|---|---|
| `SCHEMA_CACHE_CACHE_DIR` | 텍스트 | `.cache/schema` | 재시작 |  |
| `SCHEMA_CACHE_ENABLED` | 토글 | `true` | 재시작 |  |
| `SCHEMA_CACHE_BACKEND` | 세그먼트: redis·file ※ | `redis` | 재시작 |  |
| `SCHEMA_CACHE_AUTO_GENERATE_DESCRIPTIONS` | 토글 | `true` | 다음 요청 |  |
| `SCHEMA_CACHE_FINGERPRINT_TTL_SECONDS` | 숫자(int) | `1800` | 재시작 |  |

### A.14 감사 로그 (`cfg.audit` · 8필드)
| env 키 | 위젯 | 기본값 | 반영 | 비고 |
|---|---|---|---|---|
| `AUDIT_JSONL_ENABLED` | 토글 | `true` | 재시작 |  |
| `AUDIT_DB_ENABLED` | 토글 | `true` | 재시작 |  |
| `AUDIT_RETENTION_DAYS` | 숫자(int) | `90` | 재시작 |  |
| `AUDIT_SENSITIVE_TABLES` | 태그(JSON 배열) | `[]` | 재시작 | **미소비** |
| `AUDIT_ALERT_ON_FAILED_LOGIN` | 숫자(int) | `5` | 재시작 |  |
| `AUDIT_ALERT_ON_LARGE_RESULT` | 숫자(int) | `5000` | 재시작 |  |
| `AUDIT_NIGHT_ALERT_START` | 숫자(int) | `2` | 재시작 | **미소비** |
| `AUDIT_NIGHT_ALERT_END` | 숫자(int) | `6` | 재시작 | **미소비** |

### A.15 알람 (`cfg.alarm` · 21필드)
| 구획 | env 키 | 위젯 | 기본값 | 반영 | 비고 |
|---|---|---|---|---|---|
| 기본 | `ALARM_ENABLED` | 토글 | `false` | 재시작 |  |
| 기본 | `ALARM_REDIS_STREAM_KEY` | 텍스트 | `alarm:raw` | 재시작 |  |
| 기본 | `ALARM_REDIS_CONSUMER_GROUP` | 텍스트 | `alarm-workers` | 재시작 |  |
| 기본 | `ALARM_MIN_SEVERITY` | 숫자(int) | `2` | 재시작 |  |
| 기본 | `ALARM_DEDUP_TTL_SECONDS` | 숫자(int) | `300` | 재시작 |  |
| 기본 | `ALARM_NOTIFICATION_CHANNELS_CSV` | 태그(CSV) | `workb` | 재시작 |  |
| 기본 | `ALARM_WEBHOOK_URL` | 텍스트 |  | 재시작 |  |
| 기본 | `ALARM_WEBHOOK_TIMEOUT_SECONDS` | 숫자(int) | `10` | 재시작 |  |
| Plan 47 이력 | `ALARM_HISTORY_ENABLED` | 토글 | `true` | 재시작 |  |
| Plan 47 이력 | `ALARM_HISTORY_LOOKBACK_DAYS` | 숫자(int) | `90` | 재시작 |  |
| Plan 47 이력 | `ALARM_HISTORY_MAX_ROWS` | 숫자(int) | `2000` | 재시작 |  |
| Plan 47 이력 | `ALARM_HISTORY_CACHE_TTL_SECONDS` | 숫자(int) | `300` | 재시작 |  |
| 기본 | `ALARM_ENRICH_TIMEOUT_SECONDS` | 숫자(int) | `5` | 재시작 |  |
| 기본 | `ALARM_BURST_THRESHOLD_24H` | 숫자(int) | `5` | 재시작 |  |
| Plan 47-1 프로세스 | `ALARM_PROCESS_ENRICH_ENABLED` | 토글 | `true` | 재시작 |  |
| Plan 47-1 프로세스 | `ALARM_PROCESS_API_BASE_URLS_CSV` | 태그(CSV) | `polestar_cm_gp=http://polestar.kbonec…` | 재시작 |  |
| Plan 47-1 프로세스 | `ALARM_PROCESS_API_TIMEOUT_SECONDS` | 숫자(int) | `3` | 재시작 |  |
| Plan 47-1 프로세스 | `ALARM_PROCESS_TOP_N` | 숫자(int) | `5` | 재시작 |  |
| Prometheus | `ALARM_PROMETHEUS_ENABLED` | 토글 | `false` | 재시작 | **미소비** |
| Prometheus | `ALARM_PROMETHEUS_BASE_URLS_CSV` | 태그(CSV) |  | 재시작 |  |
| Prometheus | `ALARM_PROMETHEUS_TIMEOUT_SECONDS` | 숫자(int) | `3` | 재시작 |  |

### A.16 worKB 발송 (`cfg.workb` · 9필드)
| env 키 | 위젯 | 기본값 | 반영 | 비고 |
|---|---|---|---|---|
| `WORKB_BASE_URL` | 텍스트 |  | 재시작 |  |
| `WORKB_BEARER_TOKEN` | 🔒 read-only(.encenv 관리) |  | — |  |
| `WORKB_SYSTEM_DIV` | 텍스트 |  | 재시작 |  |
| `WORKB_SEND_ID` | 텍스트 |  | 재시작 |  |
| `WORKB_USER_IDS_CSV` | 태그(CSV) |  | 재시작 |  |
| `WORKB_ALIAS` | 텍스트 | `[인프라알람]` | 재시작 |  |
| `WORKB_CRITICAL_USER_IDS_CSV` | 태그(CSV) |  | 재시작 |  |
| `WORKB_WARNING_USER_IDS_CSV` | 태그(CSV) |  | 재시작 |  |
| `WORKB_TIMEOUT_SECONDS` | 숫자(int) | `10` | 재시작 |  |

### A.17 노이즈 게이트 (`cfg.noise_gate` · 84필드)
| 구획 | env 키 | 위젯 | 기본값 | 반영 | 비고 |
|---|---|---|---|---|---|
| E1 기본 | `NOISE_ENABLE_NOISE_GATE` | 토글 | `false` | 재시작 |  |
| E1 기본 | `NOISE_SUPPRESS_MAX_SEVERITY` | 숫자(int) | `2` | 재시작 |  |
| E1 기본 | `NOISE_IMPORTANCE_VALUE_MAP_CSV` | 태그(CSV) |  | 재시작 |  |
| E1 기본 | `NOISE_SELF_HEAL_WINDOW_SECONDS` | 숫자(int) | `300` | 재시작 |  |
| E2 억제 | `NOISE_DEBOUNCE_SECONDS` | 숫자(int) | `0` | 재시작 | **미소비** |
| E2 억제 | `NOISE_FLAP_HIGH_THRESHOLD` | 숫자(float) | `20.0` | 재시작 |  |
| E2 억제 | `NOISE_FLAP_LOW_THRESHOLD` | 숫자(float) | `5.0` | 재시작 |  |
| E2 억제 | `NOISE_FLAPPING_ENABLED` | 토글 | `false` | 재시작 |  |
| E2 억제 | `NOISE_DEPENDENCY_SUPPRESSION` | 토글 | `false` | 재시작 |  |
| Plan 60 E4 토폴로지 | `NOISE_MULTI_HOP_CASCADE_ENABLED` | 토글 | `false` | 재시작 |  |
| Plan 60 E4 토폴로지 | `NOISE_TOPOLOGY_CACHE_TTL_SECONDS` | 숫자(int) | `86400` | 재시작 |  |
| Plan 60 E4 토폴로지 | `NOISE_TOPOLOGY_MAX_HOPS` | 숫자(int) | `5` | 재시작 |  |
| E2 억제 | `NOISE_INHIBITION_ENABLED` | 토글 | `false` | 재시작 |  |
| E2 억제 | `NOISE_INHIBITION_WINDOW_SECONDS` | 숫자(int) | `300` | 재시작 |  |
| E2 억제 | `NOISE_STORM_GROUPING_ENABLED` | 토글 | `false` | 재시작 |  |
| E2 억제 | `NOISE_STORM_WINDOW_SECONDS` | 숫자(int) | `60` | 재시작 |  |
| E2 억제 | `NOISE_STORM_THRESHOLD` | 숫자(int) | `5` | 재시작 |  |
| Plan 60 E2 크로스-호스트 상관 | `NOISE_CROSS_HOST_CORRELATION_ENABLED` | 토글 | `false` | 재시작 |  |
| Plan 60 E2 크로스-호스트 상관 | `NOISE_CORRELATION_SIM_THRESHOLD` | 숫자(float) | `0.5` | 재시작 |  |
| Plan 60 E2 크로스-호스트 상관 | `NOISE_CORRELATION_WINDOW_SECONDS` | 숫자(int) | `120` | 재시작 |  |
| Plan 60 E2 크로스-호스트 상관 | `NOISE_CORRELATION_MIN_CLUSTER_SIZE` | 숫자(int) | `2` | 재시작 |  |
| Plan 60 E2 크로스-호스트 상관 | `NOISE_CORRELATION_BUFFER_MAX` | 숫자(int) | `1000` | 재시작 |  |
| Plan 60 E2 크로스-호스트 상관 | `NOISE_CORRELATION_FIELD_WEIGHTS_CSV` | 태그(CSV) |  | 재시작 | **미소비** |
| Plan 60 E2 크로스-호스트 상관 | `NOISE_CORRELATION_TOPOLOGY_WEIGHT_ENABLED` | 토글 | `false` | 재시작 |  |
| Plan 60 E2 크로스-호스트 상관 | `NOISE_CORRELATION_TOPOLOGY_WEIGHT` | 숫자(float) | `0.2` | 재시작 |  |
| E3 메타·보강 | `NOISE_BUSINESS_HOURS_CSV` | 태그(CSV) |  | 재시작 | **미소비** |
| E1 기본 | `NOISE_REPEAT_INTERVAL_SECONDS` | 숫자(int) | `14400` | 재시작 |  |
| E1 기본 | `NOISE_SEV3_REPEAT_INTERVAL_SECONDS` | 숫자(int) | `14400` | 재시작 |  |
| Plan 60 E1 재발 감사 | `NOISE_RECURRENCE_AUDIT_EVERY_N` | 숫자(int) | `1` | 재시작 |  |
| E1 기본 | `NOISE_NOISE_CONTEXT_TIMEOUT_SECONDS` | 숫자(float) | `3.0` | 재시작 |  |
| E1 기본 | `NOISE_NOISE_CONTEXT_CACHE_TTL_SECONDS` | 숫자(int) | `300` | 재시작 |  |
| E3 메타·보강 | `NOISE_META_ALERT_SUPPRESS_RATIO` | 숫자(float) | `0.9` | 재시작 |  |
| E3 메타·보강 | `NOISE_META_ALERT_WINDOW_SECONDS` | 숫자(int) | `3600` | 재시작 |  |
| E3 메타·보강 | `NOISE_META_ALERT_MIN_EVENTS` | 숫자(int) | `1` | 재시작 |  |
| E3 메타·보강 | `NOISE_ENABLE_AI_SEVERITY_BOOST` | 토글 | `false` | 재시작 |  |
| E3 메타·보강 | `NOISE_AI_SEVERITY_ESCALATE_ONLY` | 토글 | `true` | 재시작 | **미소비** |
| Plan 60 E3 동적 baseline | `NOISE_DYNAMIC_BASELINE_ENABLED` | 토글 | `false` | 재시작 |  |
| Plan 60 E3 동적 baseline | `NOISE_ANOMALY_Z_HIGH` | 숫자(float) | `3.0` | 재시작 |  |
| Plan 60 E3 동적 baseline | `NOISE_ANOMALY_MIN_PERIODS` | 숫자(int) | `3` | 재시작 |  |
| Plan 60 E3 동적 baseline | `NOISE_ANOMALY_BASELINE_CACHE_TTL_SECONDS` | 숫자(int) | `3600` | 재시작 |  |
| Plan 60 E3 동적 baseline | `NOISE_ANOMALY_STL_ENABLED` | 토글 | `false` | 재시작 |  |
| Plan 60 B-7 임베딩 주석 | `NOISE_SEMANTIC_DEDUP_ANNOTATION_ENABLED` | 토글 | `false` | 재시작 |  |
| Plan 60 B-7 임베딩 주석 | `NOISE_TOPOLOGY_TEXT_FUSION_ENABLED` | 토글 | `false` | 재시작 |  |
| Plan 60 B-7 임베딩 주석 | `NOISE_EMBEDDING_MODEL_PATH` | 텍스트 |  | 재시작 |  |
| Plan 60 B-7 임베딩 주석 | `NOISE_EMBEDDING_SIMILARITY_THRESHOLD` | 숫자(float) | `0.87` | 재시작 |  |
| Plan 60 B-7 임베딩 주석 | `NOISE_EMBEDDING_TIMEOUT_SECONDS` | 숫자(float) | `2.0` | 재시작 |  |
| E4 피드백 | `NOISE_ENABLE_LLM_ACTIONABILITY` | 토글 | `false` | 재시작 |  |
| E4 피드백 | `NOISE_FEEDBACK_STORE_PATH` | 텍스트 | `logs/alarm_feedback.jsonl` | 재시작 |  |
| E4 피드백 | `NOISE_FEEDBACK_STORE_ENABLED` | 토글 | `true` | 재시작 |  |
| E4 피드백 | `NOISE_ACTIONABILITY_FEWSHOT_COUNT` | 숫자(int) | `3` | 재시작 |  |
| E5 agentic enricher | `NOISE_ENABLE_AGENTIC_ENRICHER` | 토글 | `false` | 재시작 |  |
| E5 agentic enricher | `NOISE_AGENTIC_ENRICHER_FALLBACK` | 세그먼트: semantic_routing·deterministic_only ※ | `semantic_routing` | 재시작 |  |
| E5 agentic enricher | `NOISE_AGENTIC_ENRICHER_TIMEOUT_SECONDS` | 숫자(float) | `8.0` | 재시작 |  |
| E5 agentic enricher | `NOISE_AGENTIC_ENRICHER_MAX_TOOL_CALLS` | 숫자(int) | `5` | 재시작 |  |
| E5 agentic enricher | `NOISE_AGENTIC_ENRICHER_MESSAGE_ALARMS_ONLY` | 토글 | `true` | 재시작 |  |
| E1 기본 | `NOISE_RESOLVED_TO_DASHBOARD` | 토글 | `false` | 재시작 |  |
| E1 기본 | `NOISE_DECISION_STORE_PATH` | 텍스트 | `logs/alarm_decisions.jsonl` | 재시작 |  |
| E1 기본 | `NOISE_DECISION_STORE_ENABLED` | 토글 | `true` | 재시작 |  |
| E3 메타·보강 | `NOISE_TICKET_BATCH_QUEUE_PATH` | 텍스트 | `logs/alarm_ticket_queue.jsonl` | 재시작 |  |
| E3 메타·보강 | `NOISE_TICKET_BATCH_QUEUE_ENABLED` | 토글 | `true` | 재시작 |  |
| SSE 브리지 | `NOISE_SSE_BRIDGE_ENABLED` | 토글 | `false` | 재시작 |  |
| SSE 브리지 | `NOISE_SSE_BRIDGE_CHANNEL` | 텍스트 | `alarm:sse` | 재시작 |  |
| D-049 incident 계측 | `NOISE_INCIDENT_TRACKING_ENABLED` | 토글 | `false` | 재시작 |  |
| D-049 incident 계측 | `NOISE_INCIDENT_EVENT_CHANNEL` | 텍스트 | `alarm:incident` | 재시작 |  |
| Plan 60 E6 통보 보강 | `NOISE_MESSAGE_ENRICHMENT_ENABLED` | 토글 | `false` | 재시작 |  |
| Plan 60 E6 통보 보강 | `NOISE_ENRICHMENT_MIN_TIER` | 셀렉트: PAGE·TICKET·DASHBOARD·SUPPRESS ※ | `PAGE` | 재시작 |  |
| Plan 60 E6 통보 보강 | `NOISE_ENRICHMENT_L1_TIMEOUT_SECONDS` | 숫자(float) | `3.0` | 재시작 |  |
| Plan 60 E6 통보 보강 | `NOISE_ENRICHMENT_PROFILE_MAP_CSV` | 태그(CSV) |  | 재시작 |  |
| Plan 60 E5 변경 상관 | `NOISE_CHANGE_CORRELATION_ENABLED` | 토글 | `false` | 재시작 |  |
| Plan 60 E5 변경 상관 | `NOISE_CHANGE_WINDOW_SECONDS` | 숫자(int) | `3600` | 재시작 |  |
| Plan 60 E7 ITSM 보완 | `NOISE_ANNOTATION_HARVEST_ENABLED` | 토글 | `false` | 재시작 |  |
| Plan 60 E7 ITSM 보완 | `NOISE_ANNOTATION_PLANNED_SUPPRESS` | 토글 | `false` | 재시작 |  |
| Plan 60 E7 ITSM 보완 | `NOISE_NON_ALARM_FILTER_ENABLED` | 토글 | `false` | 재시작 |  |
| Plan 60 E7 ITSM 보완 | `NOISE_FORMAT_TOLERANT_PARSING_ENABLED` | 토글 | `false` | 재시작 |  |
| Plan 60 E7 ITSM 보완 | `NOISE_CORRELATION_SITE_DIMENSION_ENABLED` | 토글 | `false` | 재시작 |  |
| Plan 64 CW-A/B/C 자동조사 | `NOISE_INVESTIGATION_TRIGGER_ENABLED` | 토글 | `false` | 재시작 |  |
| Plan 64 CW-A/B/C 자동조사 | `NOISE_INVESTIGATION_TRIGGER_MIN_TIER` | 셀렉트: PAGE·TICKET·DASHBOARD·SUPPRESS ※ | `PAGE` | 재시작 |  |
| Plan 64 CW-A/B/C 자동조사 | `NOISE_INVESTIGATION_SERVICE_URL` | 텍스트 | `http://localhost:9098/sse` | 재시작 |  |
| Plan 64 CW-A/B/C 자동조사 | `NOISE_INVESTIGATION_SERVICE_TOKEN` | 🔒 read-only(.encenv 관리) | `(미설정)` | — |  |
| Plan 64 CW-A/B/C 자동조사 | `NOISE_INVESTIGATION_MCP_CALL_TIMEOUT_SECONDS` | 숫자(float) | `10.0` | 재시작 |  |
| Plan 64 CW-A/B/C 자동조사 | `NOISE_INVESTIGATION_POLL_INTERVAL_SECONDS` | 숫자(float) | `1.0` | 재시작 |  |
| Plan 64 CW-A/B/C 자동조사 | `NOISE_INVESTIGATION_TOTAL_TIMEOUT_SECONDS` | 숫자(float) | `45.0` | 재시작 |  |
| Plan 64 CW-A/B/C 자동조사 | `NOISE_FAULT_DIAGNOSIS_ENABLED` | 토글 | `false` | 재시작 |  |
| Plan 64 CW-A/B/C 자동조사 | `NOISE_FAULT_ESCALATION_ENABLED` | 토글 | `false` | 재시작 |  |

### A.18 전역(top-level) (15필드)
| env 키 | 위젯 | 기본값 | 반영 | 비고 |
|---|---|---|---|---|
| `CHECKPOINT_BACKEND` | 세그먼트: sqlite·postgres | `sqlite` | 재시작 |  |
| `CHECKPOINT_DB_URL` | 텍스트 | `checkpoints.db` | 재시작 |  |
| `DB_BACKEND` | 세그먼트: dbhub·direct | `direct` | 재시작 |  |
| `DB_CONNECTION_STRING` | 텍스트 |  | 재시작 | 기존 'DB 연결 설정' 전용 탭으로 편집(마스킹) |
| `ENABLE_SEMANTIC_ROUTING` | 토글 | `false` | 재시작 | 게이트4 교정 후 tristate 전환(§5) |
| `ENABLE_DEEPAGENT_ORCHESTRATION` | 세그먼트: auto·true·false | `(미설정)` | 재시작 |  |
| `MAX_REPLAN` | 숫자(int) | `3` | 재시작 |  |
| `ENABLE_DEEPAGENTS_PACKAGE` | 토글 | `false` | 재시작 |  |
| `WORKER_PROVIDER_OVERRIDE` | 셀렉트: (미설정)·ollama·fabrix·gemini | `(미설정)` | 재시작 |  |
| `POLESTAR_DB_IDS` | 태그(CSV) |  | 재시작 |  |
| `LOG_LEVEL` | 세그먼트: DEBUG·INFO·WARNING·ERROR | `INFO` | 재시작 |  |
| `ENABLE_SQL_APPROVAL` | 토글 | `false` | 재시작 |  |
| `ENABLE_STRUCTURE_APPROVAL` | 토글 | `true` | 재시작 |  |
| `CONVERSATION_MAX_TURNS` | 숫자(int) | `20` | 재시작 | **미소비** |
| `CONVERSATION_TTL_HOURS` | 숫자(int) | `24` | 재시작 | **미소비** |
