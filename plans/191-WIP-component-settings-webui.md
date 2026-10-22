# 191. 분리 컴포넌트 설정 웹UI — sre_agent · apm_gateway · mcp_server · alarm_server · fabrix_proxy 설정을 관리자 화면에서 조회·저장한다

> **작성일**: 2026-10-19 · **v1.1**(2026-10-21 심도 검토 · 실측 정정 · Wave 재분할)
> **재부여**: 원 `plans/189` · D-344 → 2026-10-22 main 병합 시 `plans/191` · D-345(원격 main이 `plans/189` 운영 Prometheus 라벨 앵커 · `plans/190` · D-344를 먼저 푸시 — 미푸시 쪽을 옮김)
> **상태**: **WIP — W0~W7 구현·검증 완료(2026-10-21 · 커밋 전)** · 잔여: 매뉴얼 A-81 캡처(도커 DB 리셋으로 실패) · CLAUDE.md 「패키지 경계」 표 1줄(사용자 편집 대상) · 계획 끝 회귀 rc=0(모듈 단위 — 본체 실패 61건 전부 원래 실패) · verifier·security-auditor 한 라운드(Critical 1·High 1·Major 2 → 교정 완료) · 게이트 G-1~G-7 확정(§5 — 사용자 위임 2026-10-21 「검토하고 정리하여 진행」 · 권고안 채택 · G-3·G-4 실측 보정)
> **계획 시작 SHA**: `a4514020`(2026-10-21 세션 시작 HEAD와 동일 확인)
> **회귀 대상 파일**(W1~W7 교정 누적 · 59개): `.encenv.example` · `.env.example` · `.gitignore` · `apm_gateway/.env.example` · `apm_gateway/apm_gateway/__main__.py` · `apm_gateway/apm_gateway/config.py` · `apm_gateway/apm_gateway/interface/config_admin.py` · `apm_gateway/apm_gateway/interface/config_admin_adapter.py` · `apm_gateway/apm_gateway/interface/server.py` · `apm_gateway/tests/test_config_admin.py` · `config/settings_help/component_admin.yaml` · `fabrix_proxy/.env.example` · `fabrix_proxy/fabrix_proxy/__main__.py` · `fabrix_proxy/fabrix_proxy/config.py` · `fabrix_proxy/fabrix_proxy/config_admin.py` · `fabrix_proxy/fabrix_proxy/config_admin_adapter.py` · `fabrix_proxy/tests/test_config_admin.py` · `mcp_server/.env.example` · `mcp_server/mcp_server/__main__.py` · `mcp_server/mcp_server/config.py` · `mcp_server/mcp_server/config_admin.py` · `mcp_server/mcp_server/config_admin_adapter.py` · `mcp_server/tests/test_config_admin.py` · `noise_gate/alarm_server/__main__.py` · `noise_gate/alarm_server/alarm_server.env.example` · `noise_gate/alarm_server/config.py` · `noise_gate/alarm_server/config_admin.py` · `noise_gate/alarm_server/config_admin_adapter.py` · `noise_gate/tests/test_alarm_server_config_admin.py` · `noise_gate/tests/test_alarm_server_log_masking.py` · `scripts/manual/content/admin.md` · `scripts/manual/features.yaml` · `src/api/routes/admin_components.py` · `src/api/server.py` · `src/api/settings_catalog.py` · `src/config/__init__.py` · `src/config/services.py` · `src/infrastructure/component_admin_client.py` · `src/static/admin/dashboard.html` · `src/static/css/style.css` · `src/static/js/admin-components.js` · `src/static/js/admin.js` · `src/static/manual/admin.html` · `src/static/manual/help_index.json` · `sre_agent/.env.example` · `sre_agent/sre_agent/interface/config_admin.py` · `sre_agent/sre_agent/interface/config_admin_adapter.py` · `sre_agent/sre_agent/interface/mcp_service.py` · `sre_agent/sre_agent/run_service.py` · `sre_agent/sre_agent/settings.py` · `sre_agent/tests/test_config_admin.py` · `tests/test_api/test_admin_components.py` · `tests/test_api/test_settings_catalog.py` · `tests/test_utils/test_config_admin_copies.py` · `fabrix_proxy/tests/test_log_masking_entry.py` · `mcp_server/tests/test_log_masking_entry.py` · `sre_agent/sre_agent/__init__.py` · `noise_gate/alarm_server/__init__.py` · `sre_agent/tests/test_config_admin_verifier.py`
> **계약**: `spec/SPEC-component-config-admin.md`(W0 산출 — 본 계획 §3보다 우선)
> **결정**: **D-345**(2026-10-21 본문 등재 — D-179 개정: 별도 패키지 설정을 본체 UI에서 *쓰지 않는다* → *각 컴포넌트가 자기 설정 관리 API를 소유하고 본체 UI는 그 API를 프록시한다*). D-179 상태·부기 갱신.
> **관련 결정**: D-179(설정 UI 경계 — **개정 대상**) · D-139(패키지 경계 · 양방향 import 0) · D-129(카탈로그 SSOT · 시크릿 편집 차단 · 3단 검증·원자 교체·감사) · D-135(리로드 — 본 계획은 *재기동 필요*만) · D-274(apm_gateway 독립 · 제니퍼 토큰 격리) · D-118(sre_agent 경계) · D-125(정적 Bearer) · D-339 ②(별도 프로세스 패키지는 사본을 둔다) · D-255(매뉴얼 동반) · D-070/D-071(시크릿 분리)
> **성격**: 기능 추가. 컴포넌트마다 **기본 off = 관리 라우트 미등록 = 현행 비트 동일**.

---

## 0. 사용자 결정 (2026-10-19 인터뷰)

| # | 질문 | 결정 |
|---|---|---|
| U-1 | D-179 개정 방식 | **각 컴포넌트 자체 설정 API** — 패키지가 자기 설정 스키마·조회·저장을 관리 전용 엔드포인트로 노출하고, 본체 UI는 그 API를 프록시한다(import 0·계약만 → D-139 유지 · 다른 호스트 배치 지원) |
| U-2 | 대상 범위 | **5종 전부** — `sre_agent` · `apm_gateway` · `mcp_server` · `noise_gate/alarm_server` · `fabrix_proxy` |
| U-3 | 반영 방식 | **저장 + 재기동 필요 표시** — 재기동은 운영자가 직접 한다. 컴포넌트 리로드 엔드포인트는 범위 밖 |

---

## 1. 현황 실측 (2026-10-19 · `a4514020`)

### 1.1 컴포넌트별 설정 원천

| 컴포넌트 | 프로세스·HTTP 표면 | 설정 로딩 | 키(예시 파일 기준) | 시크릿 | 본체가 아는 접속 설정 |
|---|---|---|---|---|---|
| `sre_agent` | uvicorn + FastMCP SSE(Starlette · `interface/mcp_service.py:515` `build_asgi_app`) · 포트 `DEFAULT_PORT`(9098) · 정적 Bearer `SERVICE_BEARER_TOKEN` | pydantic `AgentSettings`(`settings.py`) · `env_file=(".env", ".encenv")` **CWD 기준**(뒤 파일 `.encenv`가 이긴다) | `.env.example` 38키(활성 20 · 주석 18 · `ANTHROPIC_API_KEY`·`OPENAI_API_KEY`는 필드 아닌 holmes 통과 env) · 필드 ~45 | `SecretStr` 5종(`api_key`·`polestar_mcp_token`·`apm_mcp_token`·`gemini_api_key`·`service_bearer_token`) | `ALARM_INVESTIGATION_SERVICE_URL`(`src/config/alarm.py:370`) |
| `apm_gateway` | uvicorn + SSE MCP(`interface/server.py:170` `build_asgi_app` · `BearerPrincipalMiddleware`) · 주체별 Bearer(`APM_GATEWAY_BEARER_TOKEN(S)`) · 이벤트 폴러 같은 루프(`__main__.py`) · 포트 9096 | 자체 `load_dotenv`(`config.py:288` — **패키지 루트 `.env` 고정** · **이미 있는 `os.environ` 키는 덮지 않음** → 기동 후 `os.environ`에 파일 값이 섞여 OS 원천 판정에 주입 키 기록 필요) → dataclass 조립(`load_config(env=…)` 매핑 주입 가능) · `JENNIFER_SOURCES` 접두 키(소스 N개 — 동적) · 정책 YAML 3종(`config/*.yaml`) | `.env.example` 33키 | 일반 `str`(`token`·`bearer_token(s)`·`password`) — **타입으로 구분 불가** | `APM_MCP_URL`(`src/config/alarm.py:466`) · 데이터 접근 시스템 코드 맵(`data_access.py:36`) |
| `mcp_server` | uvicorn + FastMCP SSE(`server.py:71` `build_asgi_app` · `StaticBearerAuthMiddleware`) · 포트 9099 · **별도 VM 배치 전제**(`config.toml:5`) | `config.toml` → `_load_dotenv`(`config.toml`과 같은 디렉토리 `.env` → `os.environ`, 기존 키 보존 · `config.py:201`) → env 오버라이드(`_apply_env_overrides` `config.py:348` · `os.environ.get` 368~459 — **`os.environ`을 직접 읽어 dry-run에 매핑 주입 경로 필요**) · `[[sources]]` 9개 | `.env.example` 34키(대부분 주석) · toml 섹션 3 + sources 9 | 일반 `str`(`MCP_BEARER_TOKEN` · `PROMETHEUS_AUTH_HEADER` · `{SOURCE}_CONNECTION` = DB 접속 문자열) | `server_url`(`src/config/data_access.py:29`) |
| `noise_gate/alarm_server` | **HTTP 없음** — asyncio TCP 수신만(`tcp_receiver.py:34` · 9100) | pydantic `AlarmServerConfig` · `env_file=["alarm_server.env", ".env"]`(→ 2026-10-21 `[".env", "alarm_server.env"]`로 교정) **CWD 기준**(루트에서 `python -m noise_gate.alarm_server` → 레포 루트 `alarm_server.env` · 예시 파일만 패키지 안) — **실측: 뒤 파일 `.env`가 이긴다**(코드 주석 「전용 우선」과 반대 · pydantic-settings 2.13.1) | 8키 | `ALARM_SERVER_REDIS_PASSWORD` | 없음(Redis Stream `alarm:raw` 계약만) |
| `fabrix_proxy` | FastAPI(`app.py:251` · `/v1/chat/completions`·`/v1/models`·`/health`) · 포트 9095 · `FABRIX_PROXY_TOKEN` 필수 | pydantic `ProxySettings` · `env_file=(PACKAGE_ROOT/.env, PACKAGE_ROOT/.encenv)` **패키지 루트 고정** | `.env.example` 24키 · `.encenv`가 `.env`를 이긴다 | 일반 `str`(`fabrix_proxy_token`·`fabrix_api_key`·`fabrix_client_key`) | 본체 LLM base_url(운영 배선 시) · **PoC 단계(plans/148)** · CLAUDE.md 저장소 지도 미등재 |

### 1.2 본체 관리자 UI 현황

- 설정 탭은 본체 `AppConfig`만 다룬다(D-129 카탈로그). 분리 컴포넌트는 `src/static/admin/dashboard.html:148` 「이 화면 범위 밖 설정」 안내만 있다 — **`mcp_server`·`sre_agent`만 적혀 있고 `apm_gateway`·`alarm_server`·`fabrix_proxy`는 안내에서도 빠졌다.**
- 본체가 분리 컴포넌트와 맺은 관계는 **MCP/HTTP URL + 토큰**뿐이다. 파일 시스템 공유는 보장되지 않는다(`mcp_server` 별도 VM · `apm_gateway` 제니퍼 환경 선택 배포).
- 재사용할 본체 자산: `src/api/settings_catalog.py`의 스키마 형태(`SettingSchemaItem`·`SettingGroupSchema` · 마스킹 `********` · `apply_mode`) · `src/static/js/admin.js` 타입별 위젯·diff 모달·재기동 배너 · `require_admin_user` · 감사 `SETTINGS_UPDATE`.

### 1.3 이 계획이 풀어야 할 함정 (실측)

1. **"쓰는 파일 = 읽는 파일"이 컴포넌트마다 다르다.** `sre_agent`·`alarm_server`는 CWD 기준이라 레포 루트에서 기동하면 **본체 `.env`/`.encenv`를 읽는다**(`settings.py` 주석이 의도로 명시). 이때 컴포넌트가 "자기 `.env`"에 쓰면 반영되지 않고, 루트 `.env`에 쓰면 본체 카탈로그와 같은 파일을 두 주체가 고친다.
2. **시크릿 판정 근거가 제각각이다.** `sre_agent`만 `SecretStr`이고 나머지는 일반 `str` — 컴포넌트마다 **명시 시크릿 키 목록**이 필요하다.
3. **OS env 우선**: `apm_gateway`·`mcp_server`의 `load_dotenv`는 기존 `os.environ`을 덮지 않는다 → 셸·systemd env로 준 값은 파일을 고쳐도 무효(D-129 주의와 같은 함정).
4. **`mcp_server`는 읽기 전용 데이터 경계(D-003)** — 설정 쓰기 라우트는 데이터 도구 표면과 분리해야 하고, DB 접속 문자열(`sources[].connection`)은 절대 웹으로 쓰지 않는다.
5. **`config.toml` 쓰기 도구가 없다** — 표준 라이브러리 `tomllib`은 읽기 전용이다(쓰기는 `tomli_w` 등 신규 의존 필요).
6. **(2026-10-21 실측)** `alarm_server`의 전용 파일은 **CWD 기준**이라 패키지 디렉토리가 아니라 기동 CWD(보통 레포 루트)의 `alarm_server.env`다. 그리고 (교정 전) `.env`가 `alarm_server.env`를 **이겼다** — `.env`에 같은 키가 있으면 전용 파일에 써도 무효였다(`override_dotenv`). **→ 2026-10-21 사용자 지시로 순서 교정**: 로더 `[.env, alarm_server.env]` — 전용 파일이 이기고 `.env`는 보충만(D-345 부기).
7. **(2026-10-21 실측)** `sre_agent` `StaticBearerAuthMiddleware`는 Starlette 앱 **전체**를 감싼다 — 관리 라우트를 같은 앱에 붙이면 서비스 토큰을 요구받는다. 기존 앱 밖에 **경로 분기 ASGI 래퍼**를 둔다(SPEC §4). 또 필드명 `admin_token`은 루트 CWD 기동 시 본체 `.env`의 `ADMIN_TOKEN`을 읽는다 — alias를 줘도 `populate_by_name=True`라 필드명 env도 읽으므로(W1 실측) 필드명을 `sre_agent_admin_token`(env `SRE_AGENT_ADMIN_TOKEN`)으로 둔다.
8. **(2026-10-21 실측)** `mcp_server` `load_config`는 `os.environ`을 고친다(`_load_dotenv`) — dry-run은 이 함수를 그대로 부르면 안 된다.
9. **(2026-10-21 · 보안)** 웹 쓰기로 경계를 넓힐 수 있는 키(바인딩 호스트 · `mcp_server` 가드 `EXPOSE_EXECUTE_SQL`·`POLESTAR_DOMAIN_GUARD`·`EXPOSE_RAW_PROMQL`)는 읽기 전용으로 둔다(G-3 보정).
10. **(2026-10-21 · 환경)** 이 작업 트리(`collectorinfra_codex`)에는 `.venv`·`sre_agent/.venv`가 없어 형제 저장소 venv를 심볼릭 링크로 연결했다(`.git/info/exclude` 등재 · 코드는 이 트리에서 import됨을 확인).

---

## 2. 목표 · 비목표

**목표**
- 관리자 화면 「설정」 탭에서 컴포넌트를 골라 **실효 설정을 보고**(출처·마스킹 포함), **비시크릿 키를 검증 후 저장**하고, **재기동 대기 상태**를 본다.
- 연결 불가·관리 API 미활성 컴포넌트는 사유와 함께 표시한다(침묵 금지).
- 본체와 컴포넌트 사이 import 0을 유지한다(계약 = HTTP JSON).

**비목표**
- 컴포넌트 재기동·리로드(U-3) · 시크릿 웹 편집(D-129 ② 동일) · 정책·플레이북 YAML 편집(`apm_gateway/config/*.yaml` · `sre_agent/config/playbooks/` — 저장소 리뷰 대상 · `admin_incident_kb.py` 전례) · `mcp_server` `[[sources]]` 편집 · 본체 `AppConfig` 카탈로그 변경.

---

## 3. 설계

### 3.1 계약 — 컴포넌트 설정 관리 API (`spec/SPEC-component-config-admin.md` · W0 산출)

> **v1.1**: 항목 필드 이름은 본체 `SettingSchemaItem`과 같게 맞췄다(`env_key`·`file_value`·`effective_value`·`override`·`is_secret`·`is_set`) · PUT 본문은 본체와 같은 `{settings, reset_keys, dry_run}` · 쓰기 대상·우선 원천·읽기 전용 키는 SPEC §3 표가 정본이다. 아래 원문은 이력으로 둔다.

각 컴포넌트가 **자기 HTTP 앱에** 같은 형태의 라우트 3개를 단다. 경로 접두 `/admin/config`.

| 메서드 | 경로 | 응답·동작 |
|---|---|---|
| `GET` | `/admin/config/schema` | `component`(이름·버전·기동 시각·PID) · `source`(**실제로 읽은 파일 경로 목록과 우선순위** · CWD) · `groups[].items[]` = `{key, type, default, effective, source(os_env/file/default), secret, secret_set, description, writable, restart_pending}` · `warnings[]` |
| `PUT` | `/admin/config` | 본문 `{updates: {KEY: "값"}, dry_run: bool}` → sanitize(개행·인라인 `#` 금지) → 타입 검증 → **컴포넌트 자기 설정 클래스로 dry-run 로드** → `.bak` 백업·임시 파일·`os.replace` 원자 교체 → 컴포넌트 감사 JSONL 1줄(비시크릿만 이전→새값) → `{saved_keys, ignored_keys(마스킹·시크릿·OS env 오버라이드), errors[], restart_required: true}` |
| `GET` | `/admin/config/status` | 가벼운 상태 — `restart_pending_keys`(파일 값 ≠ 기동 시 로드 값) · 쓰기 가능 여부와 불가 사유 |

- **스키마 형태는 본체 `SettingSchemaItem`의 부분집합**으로 맞춰 `admin.js` 위젯을 그대로 쓴다. `apply_mode`는 전부 `restart`(U-3).
- **인증**: 관리 라우트는 **MCP·서비스 Bearer와 다른 관리 전용 토큰**(`<COMP>_ADMIN_TOKEN` · 각 컴포넌트 `.encenv`)만 받는다(G-1). 토큰이 없으면 **라우트를 등록하지 않는다**(404) = 기본 off · 현행 비트 동일.
- **쓰기 대상 파일**: 컴포넌트가 실제로 읽는 파일 중 **자기 전용 파일**(`sre_agent/.env` · `apm_gateway/.env` · `mcp_server/.env` · `noise_gate/alarm_server/alarm_server.env` · `fabrix_proxy/.env`)에만 쓴다. 실효 출처가 본체 루트 `.env`이거나 전용 파일이 없으면 **쓰기 거부 + 사유**(G-2 · G-4).
- **재기동 대기 판정**: 기동 시 로드한 값 스냅샷과 현재 파일 파싱 값을 비교한다. 파일 mtime이 아니라 값 비교(같은 값 재저장은 대기 아님).

### 3.2 컴포넌트 쪽 공용 모듈 — 패키지별 사본

- 공용 로직(파일 파싱·sanitize·원자 교체·마스킹·감사 줄·응답 직렬화 ~200줄 목표)은 **`<pkg>/…/config_admin.py` 사본**으로 각 패키지에 둔다(D-339 ② — 별도 프로세스 패키지는 바이트 동일 사본). 사본 동일성은 본체 쪽 테스트 1건이 해시로 단언한다(`log_masking` 사본 가드 전례).
- 컴포넌트별로 다른 것은 **필드 표 어댑터** 하나다:
  - `sre_agent` · `alarm_server` · `fabrix_proxy`: pydantic `model_fields` 인트로스펙션(D-129 방식) + 명시 시크릿 목록(`SecretStr` 자동 + `str` 시크릿 수동).
  - `apm_gateway`: dataclass + 수동 env 파싱이라 **명시 필드 표**(키·타입·기본값·시크릿·설명)를 패키지에 둔다. `JENNIFER_SOURCES` 접두 키는 G-5.
  - `mcp_server`: `.env` 오버라이드 키만 쓰기 대상. `config.toml` 값은 **읽기 전용 표시**(출처 `toml`) — G-3.
- 각 컴포넌트에 "**표 ⊇ `.env.example` 키 − 시크릿**" 커버리지 테스트를 둔다(D-129 T1 전례 — 예시 파일에만 키가 추가되면 실패).

### 3.3 컴포넌트별 HTTP 배선

| 컴포넌트 | 배선 | 비고 |
|---|---|---|
| `sre_agent` | `build_asgi_app`이 만든 Starlette에 `/admin/config` 마운트. 기존 `StaticBearerAuthMiddleware`는 관리 경로를 관리 토큰으로 판정하도록 경로 분기 | 자체 venv — 테스트는 `sre_agent/.venv` |
| `apm_gateway` | `__main__.py` uvicorn 앱에 마운트 · 주체별 Bearer 미들웨어와 분리 | 제니퍼 토큰은 응답에 `secret_set`만 |
| `mcp_server` | `server.py` `build_asgi_app`에 마운트 · **MCP 도구 표면(`@mcp.tool`)에는 아무것도 더하지 않는다** | 데이터 경계 readonly 불변(D-003) |
| `alarm_server` | HTTP 없음 → **옵트인 관리 HTTP 리스너**(Starlette + `uvicorn.Server`를 TCP 수신과 같은 asyncio 루프에서 · 루트 venv에 이미 있음) · `ALARM_SERVER_ADMIN_PORT` 미설정 = 미기동 | `noise_gate` 계층 매핑(`arch_check`) 갱신 필요 |
| `fabrix_proxy` | FastAPI 앱에 라우터 추가 | PoC 단계 — G-6 |

### 3.4 본체 쪽 — 프록시 · UI

- **설정**(`src/config/services.py` 후보 · nested `Field(default_factory=…)`): `component_admin_endpoints: dict[str, str]`(JSON · `{"sre_agent": "http://127.0.0.1:9098", …}` · 미입력 = 기능 off) · 토큰은 `.encenv`의 `COMPONENT_ADMIN_TOKENS`(JSON · 시크릿 — D-129 ② 대상). 이 필드도 본체 카탈로그에 자동 편입된다.
- **클라이언트**(`src/infrastructure/component_admin_client.py`): httpx · 짧은 타임아웃 · 컴포넌트별 개별 try/except(독립 신호 수집) · 실패 사유 구조화(연결 거부 / 401 / 404=관리 API 미활성 / 타임아웃).
- **라우트**(`src/api/routes/admin_components.py`): `GET /admin/components` · `GET /admin/components/{name}/config` · `PUT /admin/components/{name}/config` — 전부 `require_admin_user`(role·`type` 클레임 명시 검증) · `{name}`은 설정된 키 허용목록만 · 본체 감사 `SETTINGS_UPDATE`에 `component` 표기(키 이름만 — 값은 컴포넌트 감사가 보관).
- **UI**(`dashboard.html` · `admin.js` · `style.css`): 설정 탭 상단에 대상 선택 `[본체] [sre_agent] [apm_gateway] [mcp_server] [alarm_server] [fabrix_proxy]` · 컴포넌트 카드 = 연결 상태·버전·기동 시각·**읽는 파일 경로**·쓰기 가능 여부 · 기존 아코디언·위젯·diff 모달 재사용 · 저장 후 「**재기동 대기: N키 — `<기동 명령>`으로 재기동하세요**」 배너 · 「이 화면 범위 밖 설정」 안내는 *정책 YAML·`[[sources]]`·시크릿* 으로 축소 갱신.

---

## 4. Wave

> **v1.1 재분할(2026-10-21)**: W1(공용 모듈 정본 + `sre_agent`)이 끝나면 W2(본체)·W3(`apm_gateway`)·W4(`mcp_server`)·W5(`alarm_server`·`fabrix_proxy`)는 파일이 겹치지 않아 **병렬**로 진행한다. 매뉴얼(D-255)은 UI를 만드는 W2에 넣는다(역방향 가드가 새 버튼을 즉시 잡는다). 회귀는 D-303(계획 끝 1회 · Wave 중간은 자기 테스트 + `regress.py --no-tests`).

| Wave | 내용 | 산출·검증 |
|---|---|---|
| **W0** | 게이트 확정 · `spec/SPEC-component-config-admin.md` · D-345 등재 · D-179 부기 · 계획 실측 정정 | 문서만 — **완료(2026-10-21)** |
| **W1** | 공용 `config_admin.py` 정본(`sre_agent/sre_agent/interface/`) + `sre_agent` 어댑터 + 경로 분기 래퍼 배선(`build_asgi_app`·`run_service`) + `SRE_AGENT_ADMIN_TOKEN` + CWD 판정 쓰기 차단 + 감사 JSONL | `sre_agent/tests/test_config_admin*.py` — 토큰 없음 = 현행 동일 · 401 · 시크릿·마스킹·override 무시 · dry-run 실패 시 파일 불변 · 원자 교체·`.bak` · 재기동 대기 · 중복 키 거부 · `.env.example` 커버리지 · 관리 토큰 = 서비스 토큰이면 미등록 |
| **W2** ∥ | 본체 — `ComponentAdminConfig` · 클라이언트 · 라우트 · UI(대상 선택·카드·배너) · 「범위 밖」 안내 축소 · `.env.example`/`.encenv.example` · **관리자 매뉴얼**(D-255) | `tests/test_api/test_admin_components.py`(가짜 컴포넌트 ASGI) · `test_settings_catalog.py`·`test_bench_validate.py`(dict 설정 전례) · jsdom · `tests/test_manual` · `[웨이브 회귀 필요]`(`src/config/`) → Wave 끝 모듈 단위 회귀 |
| **W3** ∥ | `apm_gateway` — 사본 + 명시 필드 표 + `load_dotenv` 주입 키 기록 + `build_asgi_app` 래퍼 | 패키지 테스트 · 제니퍼 토큰 `is_set`만 · 주체별 Bearer와 관리 토큰 분리 · `test_boundary` 통과 |
| **W4** ∥ | `mcp_server` — 사본 + `.env` 키 쓰기 · toml 읽기 전용 · `[[sources]]` 접속 문자열 미노출 · 가드 키 읽기 전용 · `os.environ` 무변경 dry-run | MCP 도구 목록 불변 단언 · `overfit_check`(독스트링 리터럴 D-088) |
| **W5** ∥ | `alarm_server` 옵트인 관리 리스너(TCP 수신과 같은 루프) · `fabrix_proxy` 라우터 | 관리 포트·토큰 미설정 시 리스너 미기동 · TCP 수신 무영향 · `arch_check` · `.env` 우선 판정 |
| **W6** | 사본 동일성 가드(루트 `tests/test_utils/test_config_admin_copies.py`) · CLAUDE.md 「패키지 경계」 표 1줄 · 각 패키지 `.env.example` 관리 토큰 줄 확인 | 가드 통과 |
| **W7** | verifier + `security-auditor` 한 라운드(관리 토큰·SSRF·경로·로그 마스킹·XSS) · 계획 끝 회귀 `regress.py --base a4514020 --files <회귀 대상 파일>` | 보고에 범위 줄 그대로 옮김 |

---

## 5. 게이트 — **확정(2026-10-21 · 사용자 위임 「검토하고 정리하여 진행」 → 권고안 채택 · D-345 ④)**

| # | 질문 | 확정 |
|---|---|---|
| **G-1** | 관리 토큰을 MCP/서비스 Bearer와 분리할까 | **분리**. 같은 값이면 관리 라우트를 등록하지 않는다(경고 로그) |
| **G-2** | `sre_agent`를 레포 루트 CWD로 기동해 루트 `.env`/`.encenv`를 읽는 배치에서 쓰기 | **쓰기 전체 거부 + 사유 표시**(읽기는 허용). 판정 = CWD가 `sre_agent/` 패키지 루트인가 |
| **G-3** | `mcp_server/config.toml` 편집 | **읽기 전용**. `.env` env 오버라이드 키만 쓴다. **보정**: 가드 키(`EXPOSE_EXECUTE_SQL`·`POLESTAR_DOMAIN_GUARD`·`EXPOSE_RAW_PROMQL`)·`SERVER_HOST`도 읽기 전용(웹으로 경계를 넓히지 않는다) |
| **G-4** | `alarm_server` 전용 파일 부재 | **쓰기 거부 + 「전용 파일 생성」 안내**. **보정(실측)**: 전용 파일은 CWD `alarm_server.env`이고 CWD `.env`가 이긴다 → `.env`에 있는 키는 `override_dotenv`로 제외. 우선순위 교정은 행동 변경이라 별건(주석만 사실대로 고친다) → **2026-10-21 사용자 지시로 교정 완료** — 전용 파일 우선 · `.env` 키도 쓰기 가능(`fallback`) |
| **G-5** | `apm_gateway` `JENNIFER_SOURCES` | **기존 소스의 비시크릿 키 수정만**. `JENNIFER_SOURCES` 자체는 읽기 전용 |
| **G-6** | `fabrix_proxy` 포함 | **포함**(W5). 운영 미배치면 본체 endpoints에 넣지 않는다 |
| **G-7** | 관리 라우트 바인딩 | **서비스와 같은 호스트·포트**(alarm_server만 옵트인 리스너 · 기본 `127.0.0.1`). 각 컴포넌트 바인딩 호스트 키는 읽기 전용 |

## 6. 보안

- 시크릿은 읽기·쓰기 모두 `secret_set` 불리언만 오간다 — 값은 컴포넌트 밖으로 나가지 않는다(본체 응답·로그·감사 포함). 마스킹 값 수신 시 무시.
- 본체 프록시는 `{name}`을 설정 키 허용목록으로만 해석한다(임의 URL 금지 — SSRF). 컴포넌트 응답의 `description` 등 문자열은 UI에서 텍스트로만 렌더(XSS).
- 컴포넌트 라우트는 키를 **필드 표 허용목록**으로만 받는다(임의 키 기록 금지 — D-129 이전 결함 재발 방지). 값의 개행·`#`·NUL 거부.
- 감사 이원화: 본체 = 누가·어느 컴포넌트·어떤 키 / 컴포넌트 = 이전값→새값(비시크릿). 요청 id를 헤더로 전파해 대조 가능하게 한다.

## 7. 리스크

| 리스크 | 완화 |
|---|---|
| 파일은 바뀌었는데 재기동을 잊음 | 상태 API의 `restart_pending_keys`를 카드 배지로 상시 표시 |
| 사본 5벌 드리프트 | 해시 동일성 테스트 1건 · 공용 원본은 `sre_agent` 사본으로 지정 |
| 저장한 값이 컴포넌트 기동을 깨뜨림 | dry-run이 컴포넌트 **자기 설정 클래스**로 로드 · 실패 시 파일 불변 · `.bak` 보존 |
| 범위 확대 유혹(재기동 버튼·YAML 편집) | 비목표(§2)로 고정 — 필요 시 별도 계획 |

## 8. 회귀 대상 파일(예상)

`sre_agent/sre_agent/{config_admin.py, settings.py, interface/mcp_service.py, run_service.py}` · `apm_gateway/apm_gateway/{config_admin.py, config.py, __main__.py}` · `mcp_server/mcp_server/{config_admin.py, server.py, __main__.py}` · `noise_gate/alarm_server/{config_admin.py, config.py, __main__.py}` · `fabrix_proxy/fabrix_proxy/{config_admin.py, app.py}` · `src/config/services.py` · `src/infrastructure/component_admin_client.py` · `src/api/routes/admin_components.py` · `src/api/server.py`(라우터 등록) · `src/static/{admin/dashboard.html, js/admin.js, css/style.css}` · `scripts/manual/…` · 각 패키지 `.env.example`·`.encenv.example`(관리 토큰 키)
