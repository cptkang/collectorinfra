# 178. APM 게이트웨이 `ApmTools` God class 분할 — `ApmCore` + 도구군 Composition + Facade(M2) (166 단계 7d)

> **작성일**: 2026-10-16 · **v1.1**
> **상태**: **완료**(2026-10-16 · 커밋 전 — 메인이 커밋) — `ApmTools` 2,883줄·63메서드 → Facade 126줄·2메서드(`_Forward` 위임 14 + `apm_app_health` seam) · `ApmCore` 591줄·24 + 도구군 5 · tools.py 3,590→342 · 기존 테스트 수정 0 · 계획 끝 회귀 실패 0(모듈 단위) · verifier 차분 427케이스·HTTP 4,121건 불일치 0 · Critical·Major 0 · §4
> **상위**: `plans/166` §0.1 단계 7 · **계획 ID 7d**(스멜 M2 — §3.7 M2 행 · §5.7 W5 표 · §5.0 공통 규약 · §8 범위 밖). 선행 6e(`plans/175`) 완료. 같은 시간에 7c(`plans/177` — `noise_gate/`)·6c(`plans/174` — `src/`)가 돈다 — 이 계획은 `apm_gateway/` 안에서만 작업하므로 파일 겹침 0.
> **성격**: 동작 불변 구조 리팩터(D-337 · 166 §5.0) — MCP 도구 계약(이름·inputSchema·description)·도구 응답 JSON·로그 문구(로거 이름 포함)·모의 제니퍼 HTTP 호출(경로·쿼리·순서)·감사 로그 불변 · 기존 테스트 수정 0 · 스냅샷 갱신 금지
> **계획 시작 SHA**: `1ad7c161`(RCA) + 단계 6(6e) 미커밋 변경 위 — 기준 사본 `scratchpad/p178_base/apm_gateway/`(2026-10-16 16:28:52 · `.venv`·캐시 제외 rsync). `1ad7c161` worktree만으로 비교하면 6e 변경이 섞여 차분이 오염된다.
> **회귀 대상 파일(누적)**: 15 — `apm_gateway/apm_gateway/application/{tools,fleet_tools,scope_tools,manage_tools,batch,event_buffer}.py` · 새 `application/{tool_common,name_search,business_map,apm_core,instance_tools,health_tools,transaction_tools,stats_tools,compare_tools}.py` *(누적 끝 — 계획 끝 회귀 1회 실행: §4.3)* · verifier 새 테스트 `apm_gateway/tests/test_plan178_verify.py`
> **관련 결정**: D-337(리팩터 규약 — shim·seam·`_` 공개명+별칭·행동 변경 분리) · D-139·D-274(apm_gateway 독립 패키지 · 양방향 import 0 · 제니퍼 토큰 격리) · D-303(회귀) · D-255(매뉴얼 — 사용자 접점 변경 0이라 대상 아님)
> **적용 스킬**: `.claude/skills/refactoring/`

---

## 1. 범위 — 착수 시 실측(`smell_metrics.py` · 기준 사본)

| 대상 | 위치 | 줄 · 메서드 |
|---|---|---|
| `tools.py` 파일 | `apm_gateway/apm_gateway/application/tools.py` | 3,590줄 |
| `ApmTools` 클래스 | 같은 파일 707-3589 | 2,883줄 · 63메서드 |
| 모듈 수준 공용 정의(상수·인자 검증·봉투 보조·이름 검색·업무 매핑) | 같은 파일 103-706 | 약 600줄 |

**외부가 쓰는 `_` 심볼(실측 grep)**
- 모듈 import: `_Limits`(fleet·scope·manage) · `_check_n`·`_hour_floor`·`_hour_ceil`·`_ABSOLUTE_MAX_MS`(fleet) · `_given`(batch·manage) · `_positive_int`·`_target_text`(manage) · `_domain_order`·`_fresh`·`_nonneg_int`(scope).
- `core._x` 접근: `_at` 8 · `_all_failed` 7 · `_reason_tail` 5 · `_resolve` 3 · `_inst_label` 2 · `_business_defs_of` 2 · `_business_defs`(속성) 2 · `_catalog`·`_metric_plan`(scope) · `_event_options`·`_filter_events`·`_period_stats`(fleet).
- 클래스 속성: `ApmTools._event_row`(event_buffer · 테스트) · `ApmTools._guid`·`_period_stats`·`_sum_stats`·`_stats_delta`·`ok`·`err`(테스트).
- 인스턴스 속성을 읽는 테스트: `tools._business_map`·`tools._business_defs`·`tools.sources`·`tools.cfg`.

**seam·가드(실측)**
- 모듈 patch는 `fleet_tools.expect_calls`·`client_mod.load_json_file`·`mod.asyncio.sleep` 3곳뿐 — `tools` 모듈·`ApmTools`를 patch하거나 속성에 직접 대입하는 테스트 0.
- 소스 가드: `test_plan134_w34_gw_fix.py::test_visit_hit_note_has_one_definition`이 `VISIT_HIT_NOTE` 앞머리 문자열이 패키지에서 **`tools.py` 한 파일에만** 있어야 한다고 단언한다 → 이 상수의 정의는 `tools.py`에 남고, 이를 쓰는 `apm_app_health`는 Facade에 남긴다(D-337 ② seam 소유자). `test_plan134_w7_credentials.py`는 `manage_tools.py` 텍스트를 읽는다(이번 변경과 무관한 문자열).
- 로그: `ApmTools` 메서드는 로그를 남기지 않는다. 모듈 함수 `parse_business_map`만 `logger.warning` 2곳 — 옮기면 로거 이름 `apm_gateway.application.tools`를 그대로 쓴다.
- CRLF 파일 0(application·interface).

## 2. 처방 — 목표 구조

| Wave | 새 모듈(`application/`) | 내용 |
|---|---|---|
| W1 | `tool_common.py` | 상수(창·건수·노트·식별자 칸 등 — `VISIT_HIT_NOTE` 제외) · `Catalog` · `now_iso` · `Window` · `Limits` · `limit_notice` · 인자 검증·행 보조 함수(공개명) |
| W1 | `name_search.py` | 이름 검색 상수·`normalize_name`·`search_query`·`InstanceSearch`·`name_tier`·`search_names`·`suggest_names`·`domain_order`·`search_instances`·`suggest`·`same_name` |
| W1 | `business_map.py` | `BusinessMapping`·`parse_business_map`(로거 이름 유지)·`business_key`·`fresh`·`BusinessHit`·업무 상수 |
| W2 | `apm_core.py` | `ApmCore` — 상태(외부가 읽는 `_business_map`·`_business_defs`·`_catalogs`는 같은 이름) · 봉투 `ok`·`err` · `window` · 해석·소스 호출 공용(`resolve`·`api`·`inst_label`·`realtime`·`xview`·`hourly`·`catalog`·`metric_plan`·`at`·`reason_tail`·`all_failed` 등) · **도구군 2개 이상이 쓰는 헬퍼**(이벤트 옵션·필터·행·오류 조회 · 업무 정의 캐시) · 외부가 쓰던 옛 `_` 이름 별칭 |
| W2·W3 | W2 `instance_tools.py` · `health_tools.py` / W3 `transaction_tools.py` · `stats_tools.py` · `compare_tools.py` | `core: ApmCore`를 받는 도구군 클래스 — 한 도구군만 쓰는 헬퍼·상태는 그 도구군이 소유 |
| W2 | `tools.py`(Facade) | `ApmTools(ApmCore)` — 도구군 합성 + 형식 보존 위임(`ParamSpec` 서술자) · `apm_app_health`(소스 가드 seam) · `VISIT_HIT_NOTE` 정의 · 옮긴 이름 재노출(옛 이름 그대로 — D-337 ①③) |

- 형제 모듈(`fleet_tools`·`scope_tools`·`manage_tools`·`batch`·`event_buffer`)은 공개명으로 import·호출을 바꾼다(6e 후속 「`fleet_tools`의 `_Limits` import 정리」 포함). 시그니처 타입 `core: ApmTools`는 그대로 둔다.
- 6e가 만든 함수 경계(`server.py` `_Dispatch`·도구군 등록 7 · `period_stats.py`)는 유지한다.
- 상속·믹스인으로 도구군을 쪼개지 않는다(Composition). `ApmTools`가 `ApmCore`를 상속하는 것은 기존 `core` 계약(형제 모듈이 `core.sources`·`core.ok` 등을 쓴다)을 지키기 위해서다.

## 3. 성공 기준 · 검증

- `apm_gateway` 테스트(`cd apm_gateway && ../.venv/bin/python -m pytest`) 통과 · **기존 테스트 수정 0** · W0 스냅샷(`test_plan166_w0_tool_list_snapshot.py`·`test_plan166_w0_tool_output_golden.py`·`test_plan166_w1_contract_snapshot.py`) 갱신 없이 통과.
- 순환 import 0(새 모듈 각각을 단독 import해 확인) · 양방향 import 0(`test_boundary.py`) · overfit 신규 유입 0 · ruff·mypy 이번 diff 0(apm_gateway mypy는 캐시 바이너리 `--python-version 3.12`로 직접).
- `ApmTools` 클래스 줄·메서드 수 감소(목표: Facade 수백 줄 이하).
- Wave 중간: implementer 자기 테스트 + `python scripts/regress.py --base 1ad7c161 --no-tests --files <바꾼 파일…>`.
- 계획 끝 1회: `python scripts/regress.py --base 1ad7c161 --files <회귀 대상 파일…>`.
- verifier: 기준 사본과 나란히 — 도구 전 종류 × 대표 인자의 응답 텍스트·모의 HTTP 요청(순서 포함)·로그 레코드(감사 포함) 차분 0(6e 하네스 `scratchpad/v175/harness.py` 재사용 가능).

## 4. 결과

### 4.1 W1 (2026-10-16)

- 새 모듈 `tool_common.py`(296줄) · `name_search.py`(188줄) · `business_map.py`(99줄 · 로거 이름 `apm_gateway.application.tools` 유지). `tools.py` 3,590 → 3,152줄. `ApmTools` 본문 무변경.
- 공개명(옛 이름 → 새 이름, 별칭은 tools.py): `_Catalog→Catalog` · `_now_iso→now_iso` · `_Limits→Limits` · `_positive_int→positive_int` · `_nonneg_int→nonneg_int` · `_identifier→identifier_arg` · `_fingerprint→catalog_fingerprint` · `_check_n→check_n` · `_select→select_rows` · `_key→inst_key` · `_group→group_by_domain` · `_tag→tag_records` · `_inst_meta→inst_meta` · `_hour_floor/_hour_ceil→hour_floor/hour_ceil` · `_full_text→masked_full_text` · `_tx_fields→tx_fields` · `_identifier_fields→identifier_fields` · `_profile_ref→profile_ref_of` · `_given→is_given` · `_target_text→target_text` · `_with_hostnames→with_hostnames` · `_ABSOLUTE_MAX_MS→ABSOLUTE_MAX_MS` · `_domain_order→domain_order` · `_same_name→same_name` · `_business_key→business_key` · `_fresh→cache_fresh` · `_BusinessHit→BusinessHit`. 새 모듈 안에서만 쓰는 이름(`_suggest`·`_is_separator`·`_names`·`_business_entry`·`_IDENT`·`_NAME_SEPARATORS`)은 `_` 유지(§2 표의 `suggest`·`fresh` 표기와 다름 — 실측 반영).
- 형제 4모듈(fleet·scope·manage·batch)이 새 모듈에서 공개명으로 import(6e 후속 `_Limits` 정리 포함).
- 기준 tools.py 모듈 수준 이름 98개 전부 옛 이름으로 import 가능 · 옮긴 62개 동일 객체 · 새 3모듈 단독 import·순환 0 · 패키지 테스트 1,614 통과(실패 0) · `regress.py --no-tests` 종료 0(`범위: 정적 게이트만 — 테스트 미실행`) · mypy·ruff 기준 대비 새 오류 0.

### 4.2 W2·W3 (2026-10-16)

| 대상 | 줄 · 메서드 | 내용 |
|---|---|---|
| `tools.py` | 3,590 → 342줄 | `VISIT_HIT_NOTE` 정의 · `_Forward` 서술자 · `ApmTools` Facade · 옮긴 이름 옛 이름 재노출(기준 모듈 수준 이름 98개 전부 · 옮긴 96개 동일 객체) |
| `ApmTools(ApmCore)` | 2,883·63 → **126·2** | `__init__`(도구군 5 합성) · `apm_app_health`(소스 가드 seam — 노트 정의가 tools.py에만 있어야 함) · `_Forward` 위임 14 · 클래스 재노출 `_guid`·`_period_stats`·`_sum_stats`·`_stats_delta` |
| `apm_core.py` `ApmCore` | 591·24 | 공유 상태(`sources`·`cfg`·`clock`·`poller_status`·`th`·`tz`·`_catalogs`·`_business_map`·`_business_defs`) · `ok`·`err`·`window` · 공개명 `resolve`·`api`·`inst_label`·`ids_text`·`realtime`·`xview`·`window_stats`·`hourly`·`reason_tail`·`all_failed`·`where`·`catalog`·`metric_names`·`metric_plan`·`at`·`event_options`·`filter_events`·`fetch_errors`(옛 `_errors`)·`event_row`·`business_defs_of` · 옛 이름 별칭 11(클래스 밖에서 쓰이던 것만) |
| `instance_tools.py` `InstanceTools` | 470·12 | 인스턴스 지도·이름 검색·업무 검색 · 상태 `_business_seen` |
| `health_tools.py` `HealthTools` | 255·5 | 런타임 건강·자원 풀·`gateway_health` · 상태 `_health_cache` |
| `transaction_tools.py` `TransactionTools` | 471·7 | 느린 거래·활성 서비스·이벤트·프로파일 · 상태 `_profile_budget` |
| `stats_tools.py` `StatsTools` | 424·9 | 상태 통계·지표 |
| `compare_tools.py` `CompareTools` | 643·10 | 변경 감지·GUID 추적·변경 전후·기간 비교 · `_guid`→`guid` |

- 이름 변경(별칭 없음 — 클래스 밖 사용 0 grep): `_th→th` · `_tz→tz` · 코어로 올린 내부 전용 메서드의 `_` 제거. 별칭을 둔 것: `_at`·`_all_failed`·`_reason_tail`·`_resolve`·`_inst_label`·`_catalog`·`_metric_plan`·`_business_defs_of`·`_event_options`·`_filter_events`·`_event_row`(코어) · `_guid`(Facade).
- 형제 모듈: `fleet`·`scope`·`manage`의 `core._x` → 공개명 · fleet `core._period_stats` → `period_stats` 직접 import · `event_buffer`는 `ApmCore.event_row`. **`ScopeTools.__init__` 형을 `ApmTools`→`ApmCore`로 넓혔다** — W3에서 `stats_tools.apm_metrics`의 `ScopeTools(self)`가 옮긴 뒤 도구군을 넘겨 테스트 20건이 실패해 `ScopeTools(self.core)`로 교정(넘어가는 객체는 기준과 같은 `ApmTools` 인스턴스).
- 이동 본문 정규화 비교: 옮긴 메서드 전부 기준 대비 토큰 차이 0(줄 길이 맞춤 줄바꿈만). 도구 15종 시그니처·코루틴 여부·독스트링이 클래스·인스턴스 접근 모두 기준과 같다.

### 4.3 검증

- **계획 끝 회귀(1회)**: `python scripts/regress.py --base 1ad7c161 --files <회귀 대상 15>` 종료 0 — 저장소 전역 가드 통과 144 · apm_gateway 패키지 전체(④) 통과 1,614 · 실패 0 · 건너뜀 14 · arch·overfit·config_fallback 통과 · ruff 이번 diff 줄 0. `범위: 모듈 단위 — 전체 미실행`. 권고 블록 없음.
- **mypy**(도구가 「대상 파일 없음」으로 건너뜀 → 캐시 바이너리 `--python-version 3.12`로 15파일 직접): 1건 — `fleet_tools.py:527` list-item, 기준 사본과 같은 1건.
- **W0 안전망**: 도구 목록·출력 골든·계약 스냅샷 갱신 없이 통과 · 기존 테스트 수정 0.
- **verifier 차분**(기준 사본 `p178_base` ↔ 작업 트리 · 트리별 2회): 6e 하네스 285케이스 · HTTP 2,550건 · 로그 3,108건(감사 포함) + 보강 하네스(같은 인스턴스 연속 호출 — 예산·건강 캐시·업무·카탈로그 캐시·`ScopeTools(self.core)` 경로) 16시퀀스·142단계 · HTTP 1,571건 · 로그 1,619건 — 불일치 0(동시 작업 로그만 다중집합 비교). 마스킹 호출 34=34. `VISIT_HIT_NOTE` 소스 가드 변이 2건 실패 확인(공허화 없음). 순환·패키지 밖 import 0. Critical·Major 0 · Minor 3(§5). 새 검증 테스트 `test_plan178_verify.py` 15 통과.

## 5. 후속

- (Minor · 의미 기록) `_Forward`를 클래스에서 읽으면 도구군 함수가 나온다 — `ApmTools.apm_x(인스턴스, …)`처럼 클래스 경유로 부르면 `AttributeError`(기준에서는 동작). 이렇게 부르는 곳 0. 필요해지면 서술자 `__get__(None, …)`이 Facade 인스턴스를 받는 래퍼를 돌려주게 바꾼다.
- (Minor) `tools.py`의 `logger`는 더 쓰이지 않는다 — 기준 모듈 수준 이름 재노출 완결성 때문에 남겼다. 별칭 정리 때 함께 지운다.
- (D-337 ③ 별칭 정리 — 다음 사이클) tools.py W1 별칭 블록·옮긴 이름 재노출 · `ApmCore` 옛 이름 별칭 11 · Facade `_guid`·`_period_stats`·`_sum_stats`·`_stats_delta`. 제거 조건은 `grep -w <옛 이름>` 0(테스트가 `ApmTools._guid`·`_period_stats` 등을 아직 쓴다 — 지우려면 테스트 수정을 같은 계획에서).
- (6e 이월 · 행동 변경 후보 · 미수정) `fleet_tools`의 인스턴스 id None 경로(mypy list-item 1건).
- `apm_app_health`가 Facade에 남은 것은 소스 가드(`test_visit_hit_note_has_one_definition` — 노트 정의 파일명 `tools.py` 고정) 때문이다. 가드를 「정의 1곳」으로 바꾸면(테스트 수정) `HealthTools`로 옮길 수 있다.
- 검증 못 한 것: 실 제니퍼 종단(모의 HTTP만) · EventBuffer 적중 경로(픽스처상 0건).

## 6. 이력

- 2026-10-16 v1.0 — 착수(166 단계 7d). 기준 사본·외부 `_` 심볼·seam·소스 가드 실측.
- 2026-10-16 v1.1 — W1·W2·W3 완료 · 계획 끝 회귀 · verifier 차분 0 · 완료(파일명 상태 태그 제거).
