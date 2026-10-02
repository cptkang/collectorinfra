# 폴스타 편향 검토 (Polestar Bias Review) — 파이썬 코드의 특정 솔루션 편향 전수 검토

> 작성일: 2026-07-29
> **목적**: LLM toolset(프롬프트·도구 배선) 이외의 **파이썬 코드**가 특정 솔루션(폴스타)에 편향 구현된 곳을 전수 검토. 사용자 요청.
> **방법**: `scripts/overfit_check.py` 실행 실측(공용 4계층) + 검사 사각지대 디렉토리 전수 스캔(코드 읽기). 실 LLM 호출 없음.
> **관련**: Plan 63(폴스타 과적합 분리 — D-088~D-091, 완료), Plan 67 R2(DB 레지스트리), `docs/02_decision.md` D-088(공용 계층 DB-agnostic)·D-089(어댑터 레지스트리)

---

## 0. 결론 요약

코드베이스는 편향 관리 상태 기준으로 **3개 구역**으로 나뉜다:

| 구역 | 상태 | 규모 |
|---|---|---|
| ① 의도된 격리 구역 (`src/db_adapters/polestar/`, `src/alarm/infrastructure/polestar_*`, `mcp_server/polestar_tools.py` 등) | **정당** — 편향이 모이도록 설계된 곳 | 14개 파일 |
| ② 공용 4계층 (`src/utils`·`nodes`·`orchestration`·`prompts`) | **게이트 관리 중** — `overfit_check --ci`가 신규 유입 차단. 단 기준선 화이트리스트로 **schema-literal 82건 잔존 허용**, routing-vocab **130건은 스코프 아웃** | 58개 파일 |
| ③ 검사 사각지대 (`schema_cache`·`document`·`api`·`alarm`·`config.py`·`mcp_server`·`scripts` 등) | **미검사** — 이번 스캔에서 **부당한 편향 9건** 발견 | 아래 §2 |

즉 "편향이 있는가"의 답은 **있다** — ②의 잔존 82건(감소가 Plan 63 트랙 완료 지표)과 ③의 신규 발견 9건. 반면 `src/security`·`src/db`·`src/dbhub`·`src/clients`·`graph.py`·`llm.py`·`main.py`는 폴스타 토큰 0건으로 완전히 깨끗하다.

## 1. 공용 4계층 현황 (overfit_check 실측, 2026-07-29)

- 스캔 58개 파일, **schema-literal 82건**(게이트 대상) — 기준선 화이트리스트 43토큰으로 허용된 잔존분, 신규 유입 0건(기준선 준수)
- 상위: `multi_db_executor.py` 15 / `query_validator.py` 14 / `query_gen_common.py` 12 / `query_generator.py` 11 / `schema_analyzer.py` 11 / `semantic_compiler.py` 8
- **routing-vocab 130건은 스코프 아웃**(가시화만) — 위치·존 어휘(`김포`·`여의도` 등). 상위: `field_mapper.py` 28 / `process_query.py` 19 / `subagents.py` 15. → **Plan 67 R2(레지스트리)가 이 130건의 처방**이다.
- 잔존 82건의 소거는 Plan 63의 P2(어댑터 이동)·P3(선언 전환) 후속 몫 — 본 검토는 중복 계획하지 않음.

## 2. 사각지대에서 발견된 부당한 편향 — 9건 (범용이어야 할 코드의 폴스타 전제)

| # | 파일:라인 | 내용 | 유형 | 처방 |
|---|---|---|---|---|
| 1 | `src/schema_cache/cache_manager.py:1113,1119` | 프로필 부재/파싱 실패 시 폴백 `allowed_tables = ["cmm_resource"]` — 캐시 계층이 폴스타 테이블을 가상 스키마로 가정 | 스키마 리터럴 | R2 레지스트리 이동 |
| 2 | `src/document/field_mapper.py:540` | `CORE_TABLES = {"cmm_resource"}` — 문서 필드 매핑 Pass 1 우선순위가 폴스타 단일 테이블 고정 | 스키마 리터럴 | R2 레지스트리 이동 |
| 3 | `src/document/field_mapper.py:63-75,276` | `_schema_uses_metric_stat_pivot()` — synonyms 키의 `"cmm_metric_stat"` 부분문자열로 피벗 스키마 판정 → 사용률 필드 매핑 스킵 게이트 | 구조 전제 | R2 또는 structure_meta 패턴 판정으로 교체 |
| 4 | `src/config.py:424-426` | `process_api_base_urls_csv` **기본값에 운영 호스트 URL 하드코딩**(`http://polestar.kbonecloud.com` 등) | 운영 리터럴 | 기본값 공란화 + `.env` 전용 |
| 5 | `src/api/routes/alarm.py:49` | API 요청 스키마 `db_id` 기본값 `"polestar_b0"` — 특정 운영 인스턴스 고정 | 운영 리터럴 | 설정화(기본 DB는 config에서) |
| 6 | `mcp_server/mcp_server/security.py:32-36,117-154` (+`tools.py:123`) | 범용 `execute_sql` 경로가 `validate_polestar_domain()`을 **DB 종류 무관 무조건 적용**(CMM_* deny·`RESOURCE_CONF_ID=CONFIGURATION_ID` 조인 금지) | 구조 전제 | 프로필/플래그 게이트 |
| 7 | `mcp_server/mcp_server/server.py:122` | `register_polestar_tools(mcp)` **무게이트 등록**(`expose_execute_sql` 등은 게이트 있음 — 비대칭) | 구조 전제 | 설정 게이트 추가 |
| 8 | `scripts/synonym_seeds.py:79` | 레지스트리 실패 시 `db_id.endswith("b0")` 휴리스틱으로 폴스타 스키마 접두 강제 | 스키마 리터럴 | 레지스트리 단일화(폴백 제거) |
| 9 | `src/alarm/domain/anomaly.py:31-34` | `METRIC_SOURCE_BY_KIND = {"cpu": ("server.Cpus","Utilization"), …}` — **Clean Architecture domain 계층에 폴스타 스키마 상수** | 스키마 리터럴 | 어댑터/설정 주입으로 이동 |

**경계선 3건** (설계상 정당하나 설정화 여지): `src/routing/zones.py:16-17`(존↔db_id 매핑 — 단일 출처 설계로 정당, R2 흡수 대상), `src/routing/domain_config.py:38-121`(이 파일이 곧 레지스트리 — 편향이 모이는 목표 지점), `src/alarm/domain/correlation.py:27-29`(`_SITE_MARKER` 고객사 이벤트 포맷 정규식 — 플래그 게이트+graceful이라 실해 낮음, 정규식 설정화 가능).

**오탐 배제**: `schema_cache/value_index.py`·`document/mapping_report.py`의 폴스타 토큰은 docstring 예시뿐, 실 로직은 `structure_meta`의 `type=="eav"` 패턴 기반 완전 범용 — 편향 아님.

## 3. src/alarm/ 판정 — 폴스타 전용 파이프라인 (범용 프레임워크가 아님)

- `domain/alarm.py:17-30`: `AlarmEvent`가 "폴스타 단일행 JSON 템플릿 변수와 1:1 대응하도록 설계" 명시 — 이벤트 모델 자체가 벤더 결합.
- `application/alarm_worker.py:134-201`: 저장소·클라이언트를 Protocol/ABC 없이 `Polestar*` 구상 클래스 직접 생성 — 타 벤더 알람 소스 확장점 없음(추상화는 `IncidentStore` 1개뿐).
- 단 **infrastructure 계층은 정직하게 격리**되어 있음(`polestar_*` 접두 7개 파일). **domain 계층 누수는 2건뿐**(§2-9 `anomaly.py`, 경계선 `correlation.py`) — 이 2건만 내리면 domain이 벤더 중립이 된다.
- 판정: 알람 파이프라인의 폴스타 편향은 **현 단계에서 의도된 전용 구현**(멀티 소스 관측은 Plan 55 로드맵 영역). 부당한 것은 domain 계층 누수뿐.

## 4. 정당/부당 집계

| 구분 | 규모 |
|---|---|
| 부당한 편향 (사각지대 신규 발견) | **9건 / 8개 파일** (스키마 리터럴 4 · 운영 리터럴 2 · 구조 전제 3) |
| 공용 4계층 잔존 (기준선 허용, Plan 63 후속 소거 대상) | schema-literal 82건 + routing-vocab 130건(R2 처방) |
| 경계선 (정당하나 설정화 여지) | 3건 |
| 정당한 편향 (전용 모듈 격리) | 14개 파일 |
| 완전 무편향 | 15개 파일 (`security`·`db`·`dbhub`·`clients` 전부 등) |

## 5. overfit_check 검사 범위 확대 권고 (우선순위순)

1. **`src/document/`** — 최우선(공용 기능인데 리터럴이 매핑 우선순위·스킵 게이트를 직접 좌우, 기준선 0으로 시작 가능)
2. **`src/schema_cache/`** — 캐시 계층 DB-agnostic 전제 위반 폴백 검출용
3. **`mcp_server/mcp_server/`** — 단 `polestar_tools.py`는 EXCLUDE 대칭 처리(전용 모듈)
4. **`src/alarm/domain/`만 선별 편입** — infrastructure는 어댑터 논리로 제외
5. **운영 리터럴 신규 카테고리** — 현행 3개 패턴군으로는 `polestar.kbonecloud.com`이 routing-vocab(스코프 아웃)으로 분류되어 **게이트되지 않음**. `kbonecloud`·`sotori` 등 도메인 하드코딩 검출 카테고리 추가.
- 제외 유지: `scripts/`(픽스처 지배적), `src/routing/`(레지스트리 목표 지점), `alarm_server/`(폴스타 수신 전용).

## 6. Plan 67 연계 — 2026-07-29 인터뷰 확정 (전부 편입)

- §2의 1·2·3·8번(스키마 리터럴/구조 전제) → **Plan 67 Phase R2-4 합류 확정**.
- §2의 4·5번(운영 리터럴)·6·7번(mcp_server 게이트) → **Phase 0 결함 목록 ⑪~⑭ 편입 확정**.
- §5(검사 범위 확대) → **R2 완료 조건 편입 확정**.
- §2-9(`anomaly.py`) → R3-(v)(D-132 — 구 D-130 예약, Plan 68의 D-129 등재로 재부여) 인접 작업으로 처리 확정.
