# 52. 폴스타 b0 자원조회 토큰 폭증 + 재계획 오진 분석 및 해결

> 작성일: 2026-06-30
> 상위/관련 계획: `plans/48-deepagents-intent-orchestration.md`, `plans/49-phase2-dynamic-replanning.md`, `plans/50-multiturn-context-and-control-plane-token.md`
> 관련 결정: D-037(deepagents 이원 백엔드), D-042(제어 평면 예산·평면 분리), D-049(무의미 재시도 가드), D-050(EAV 피벗 HAVING)
> 신규 결정(본 계획에서 부여): **D-051**(데이터 평면 토큰 예산 가드 — FabriX도 ~95K 입력 한도), **D-052**(재계획 인프라성 에러 가드 + b0 hostname 권위)
> ※ 번호 정정 규칙(Known Mistakes 2026-06-25): `grep -roE "D-0[0-9]{2}"`로 변경 이력 표까지 확인한 결과 최댓값 D-050 → 다음 빈 번호 D-051/D-052 부여.

---

## 1. 배경 — 검증된 테스트 실패

**질의**: "은행 폴스타의 ### 서버의 IP 주소, OS 종류 및 버전, CPU 및 메모리 용량을 출력하시오"

**의도 분석**: 1개 작업(DB 조회)으로 정상 분해됨.
**입력 파싱**: `filter_conditions.field = "hostname"`, `value = "###"`로 **정확히** 추출됨(사용자 확인).

**실행 결과(4개 task 전부 실패)**:

| task | 대상 DB | 실패 사유 |
|------|---------|-----------|
| 1 (원 조회) | polestar_b0 | `GptOssAdapter.llm_call: Input tokens must be <= 95232. Given: 197951` |
| 2 (유사 이름) | polestar_b0 | 동일 (197951) |
| 3 (전체 인프라) | b0, cm_gp, cm_yd | `SQL0204N "SDQ000.CMM_RESOURCE" is an undefined name`(환각 테이블) |
| 4 ('###' 포함) | polestar_b0 | 동일 (197951) |

**재계획 3회** 모두 "0건 → 서버명 매칭 없음 → 범위 확대" 사유로 후속 task 추가(연번 제거·VM명 추정 등). 최종 응답 생성 실패.

---

## 1.5 [확정·2026-06-30 디버거 실측] 진짜 원인 — allowed_tables 유사어 확장이 화이트리스트를 무력화

> 본 절이 **확정 진단**이다. 아래 §2.x는 탐색 과정에서 세운 가설들(상당수 실측으로 반증됨)이며 참고용으로 남긴다.

VS Code 디버거로 b0(`polestar_b0`) 조회 경로를 단계별 실측한 결과:

| 측정 지점 | 값 | 의미 |
|-----------|----|------|
| `schema_analyzer.py:734` `len(relevant)` (LLM 선택 직후) | **4** | 전체-테이블 폴백 **미발동**(가설 반증). query_targets도 정상(`['서버','CPU','메모리','네트워크']`) |
| `query_generator.py:215` `len(system_prompt)//4` | **104,632 토큰** | 95K 한도 초과 — 폭증 지점 확정 |
| `len(str(eav_name_synonyms))//4` / `resource_type` | 315 / 261 | 전역 유사어 덤프 **무죄**(가설 반증) |
| `len(str(column_synonyms))//4` | **72,047 토큰** | 수천 항목 — 유사어가 수백 테이블에 걸쳐 등록됨 |
| `len(str(schema_info))//4` | **134,626 토큰** | relevant 테이블 스키마 텍스트가 거대 |
| `sample_data` 합계 | 200 | 샘플 **무죄** |
| 최종 relevant 테이블 | **"엄청 많음"** (CMM_RESOURCE, CMM_RESOURCE_SYSTEM, MON_HW, ...) | 4 → 수백으로 **부풀려짐** |

**최종 확인(디버거)**: `schema_analyzer.py:792`에서 `len(_allowed)=407`, `len(_filtered)=400`. 즉 프로필 화이트리스트 5개가 유사어 확장으로 **407개(전체 409 거의 전부)** 가 되었고 최종 relevant=400 테이블이 통째로 프롬프트에 주입됨. 의심 여지 없이 확정.

**확정된 원인 사슬**: b0 프로필 `allowed_tables`는 **5개**(cmm_resource/core_config_prop/cmm_metric_stat_m/d/h, `polestar_b0.yaml:436-441`)로 정상인데, 최종 relevant은 400개. 부풀린 범인은:

1. `schema_analyzer.py:756-762` — `cache_mgr.get_synonyms(db_id)`의 **모든 col_key의 테이블을 `_allowed`에 추가**. b0는 컬럼 유사어가 수백 테이블에 누적(column_synonyms=72K) → `_allowed`가 5 → 수백으로 폭증.
2. `schema_analyzer.py:781-789` (allowed_tables Step 2 보충) — `_allowed`의 **모든 테이블을 relevant에 무조건 주입**.
3. → relevant ≈ 유사어 보유 전 테이블 → `_format_schema_for_prompt`가 전부 덤프 → system_prompt 104K > 95K 한도 → `Input tokens must be <= 95232` 폭증.

**왜 b0/DB2만**: DB2라서가 아니라 **b0가 컬럼 유사어가 가장 많이 누적된 DB**여서다. 유사어가 늘수록 악화되는 **시한폭탄형** 버그. gp/yd는 누적이 적어 아직 한도 아래. Known Mistakes 2026-06-11("column_synonyms는 전 테이블×컬럼 수백 키 규모")과 동일 함정의 재발.

**확정 수정(§4.★)**: 756-762의 "유사어 보유 테이블 전량 → `_allowed`" 확장을 **이번 질의 용어와 매칭된 유사어의 테이블만**으로 게이트(또는 제거)하고, Step 2 보충도 질의 관련분만. → relevant이 5개 이하로 수렴, 프롬프트 ~5-10K. (P0 토큰 가드·§4.0 DB2 스코프·P1 폴백 제거는 보조 방어선으로 유지.)

---

## 2. 근본 원인 분석 (코드 근거) — 탐색 이력(일부 가설은 §1.5에서 반증됨)

### 2.0 [가설→반증] DB2 introspection이 앱 스키마로 스코프되지 않음

**관찰**: 같은 폴스타 솔루션이므로 b0(DB2)·gp/yd(PostgreSQL)의 **논리 스키마는 동일**한데, 기존 PostgreSQL에서는 토큰 폭증이 없었고 **DB2에서만 발생**한다. 프로필 크기도 gp/yd(36KB) > b0(28KB)이므로 프로필/query_guide가 원인이 아니다. → 차이는 **스키마 수집(introspection) 범위**다.

| | PostgreSQL (gp/yd) | DB2 (b0) |
|---|--------------------|----------|
| 수집 경로 | `PostgresClient` 직접 연결(`src/db/client.py`) | `DBHubClient`→MCP→DB2(`src/dbhub/client.py`) |
| 시스템 스키마 제외 | ✅ `information_schema/pg_catalog/pg_toast`(`client.py:92,103`) | ✅ `TABSCHEMA NOT LIKE 'SYS%'`(`mcp_server/.../tools.py:397`) |
| **앱 스키마 한정** | ✅ DB 자체가 폴스타 전용(단일 테넌트) | ❌ **인스턴스 내 모든 비-SYS 스키마 전부** |

**기전**: DB2 introspection SQL(`mcp_server/mcp_server/tools.py:389-409` `_db2_search_objects_sql`)은 `SELECT TRIM(TABNAME) ... FROM SYSCAT.TABLES WHERE TYPE='T' AND TABSCHEMA NOT LIKE 'SYS%'` — **앱 스키마(SDQ000)로 한정하지 않고** DB2 인스턴스의 모든 비시스템 스키마 테이블을 전부 반환한다. 은행 DB2는 통상 공유 인스턴스(타 시스템·스테이징·ETL·이력 스키마 다수)이므로 폴스타 1개 스키마만 있는 PostgreSQL과 달리 수집 테이블 수가 수배~수십배가 된다. 이를 `DBHubClient.get_full_schema`(`src/dbhub/client.py:234-249`)가 전부 순회 → `schema_dict` 비대 → query_generator 프롬프트 198K로 직결.

> **검증 포인트(필수)**: `SourceConfig`(`mcp_server/mcp_server/config.py:34-44`)에 **스키마 한정 필드가 없다**(name/type/connection/readonly/max_rows/pool만). 즉 DB2를 앱 스키마로 좁힐 수단이 코드에 부재. 실패 run의 `DEBUG[1] full_schema tables`(`schema_analyzer.py:723`) 로그에서 **b0의 테이블 수를 gp/yd와 비교**하면 이 가설을 즉시 확정할 수 있다(b0 ≫ gp/yd 예상).

**부수 문제**:
- `_db2_get_columns`(`tools.py:412-426`)는 `TABNAME`만으로 필터(스키마 무관) → 같은 이름 테이블이 여러 스키마에 있으면 컬럼이 합쳐져 중복 팽창 + 정확성 위험.
- DB2가 `TRIM(TABNAME)`로 **스키마 접두사를 버리고** 반환(`tools.py:394`) → 프로필 few-shot의 `polestar.cmm_resource`(실제 `SDQ000`)와 어긋나 task 3의 `SQL0204N SDQ000.CMM_RESOURCE undefined` 환각에 직접 기여.

**[2026-06-30 측정으로 반증됨]** 실 DB2(b0)에서 `SYSCAT.TABLES` 집계 결과:

| 스키마 | 테이블 수 |
|--------|-----------|
| **POLESTAR** | **389** |
| DBA | 18 |
| EVMON | 2 |
| **합계(비-SYS)** | **409** |

폴스타 핵심 3테이블(CMM_RESOURCE/CORE_CONFIG_PROP/CMM_METRIC_STAT_M)은 모두 **POLESTAR** 스키마 소속. 즉 **앱 스키마로 스코프해도 20개(5%)만 절감** — 미스코프는 폭증의 **부차 요인**이었다.

**수정된 함의(근본 정정)**: 토큰 폭증의 진짜 원인은 **POLESTAR 앱 스키마 자체가 389 테이블**이고, 본 질의는 그 중 3개만 필요한데 **389개가 통째로 query_generator 프롬프트에 덤프**되는 것이다(389 × ~500토큰 ≈ 198K, 산술 일치). → **근본 수정은 §4.0(스코프)이 아니라 §4.2(P1: relevant 선택이 389→3으로 좁히기) + §4.1(P0: 토큰 가드)**. §4.0은 20테이블 정리·정합성용 부차 개선으로 강등한다.

> **DB2-전용 증상 재해석**: 같은 솔루션이므로 gp/yd PostgreSQL의 폴스타 스키마도 ~389 테이블일 가능성이 높다. 그렇다면 "PG는 멀쩡했다"는 그 테스트에서 **테이블 선택(P1)이 성공**했거나 더 단순한 질의였기 때문이며, 범인은 DB2가 아니라 **테이블 선택 견고성**이다. (검증: gp/yd PG에서 동일 테이블 수 집계 → 389 근사면 P1이 진짜 차별 요인) 단, 접속 기본 스키마(CURRENT SCHEMA)가 **SDQ000**으로 보이는데 실제 테이블은 **POLESTAR** 소속 → 무수식 SQL이 SDQ000으로 해석돼 `SQL0204N` 환각(task 3) 유발. SQL은 항상 `POLESTAR`로 수식하도록 강제 필요(§4.5/§4.0-3).

### 2.1 에러 출처 오인 — FabriX 데이터 평면도 ~95K 한도

오류 문자열 `Error occurred from orchestrator. reason: ... GptOssAdapter.llm_call: Input tokens must be <= 95232`는 **vLLM 제어 평면이 아니라 FabriX(KBGenAI) 데이터 평면 백엔드**가 REST 응답으로 던진 것이다. `KBGenAIChat._agenerate`가 `result["status"] != "SUCCESS"`/`raise_for_status`로 그대로 전파한다(`src/clients/fabrix_kbgenai.py:106-148`).

> **Plan 50 §1.2 전제 정정**: Plan 50은 평면 분리표에서 **"데이터(FabriX) = 컨텍스트 한계 큼(대용량 허용)"** 으로 적었으나, **실측상 FabriX 백엔드(GptOssAdapter)도 입력 95,232 토큰으로 제한**된다. 따라서 "큰 작업을 FabriX로 내린다(D-042 B4)"만으로는 본 문제가 해결되지 않는다. **데이터 평면 프롬프트에도 토큰 예산 가드가 필수**다.

**동일 값(197951) 반복의 의미**: 누적 폭증이면 재시도마다 숫자가 커져야 한다. 매번 정확히 같다는 것은 **polestar_b0 단일 DB의 SQL 생성 프롬프트가 고정적으로 ~198K(한도의 2.08배)** 임을 뜻한다 → 멀티턴/재계획 누적이 아니라 **단일 SQL 생성 프롬프트 자체가 비대**하다.

### 2.2 폭증 지점 — query_generator 프롬프트에 토큰 가드 전무

`_format_schema_for_prompt`(`src/nodes/query_generator.py:577-669`)가 **무제한**으로 시스템 프롬프트를 조립한다:

- relevant 테이블 전체 × 모든 컬럼 + 컬럼 설명 + `column_synonyms`
  - `column_synonyms`는 Known Mistakes(2026-06-11) 기록상 **전 테이블×컬럼(수백 키) 규모**
- 테이블별 **샘플 데이터 5행**(`schema_analyzer`가 `get_sample_data(limit=5)`로 적재)
- `src/nodes/query_generator.py:646-660`: `resource_type_synonyms` / `eav_name_synonyms`를 **relevant 필터 없이 전량** 덤프
- 추가로 `_format_structure_guide`가 프로필 `query_guide`(b0 = `config/db_profiles/polestar_b0.yaml`, 28KB) + 쿼리 예시 few-shot 전체 삽입

`src/nodes/` 어디에도 토큰 예산/절단 가드가 없다(grep 확인: 토큰 언급은 `output_generator.py`뿐). 즉 프롬프트가 한도를 넘어도 **그대로 전송**되어 하드 실패한다(graceful degradation 부재).

### 2.3 증폭 요인 — schema_analyzer의 "전체 테이블" 폴백

`_llm_select_relevant_tables`(`src/nodes/schema_analyzer.py:1053-1138`)는 다음 두 경우 **전체 테이블을 반환**한다:
- `query_targets`가 비어있음(`:1078-1079`)
- LLM 테이블 선택이 유효 테이블 0개 반환 또는 예외(`:1133-1138`)

대형 은행 DB(b0)에서 이 폴백이 걸리면 relevant = 전체 테이블 → §2.2의 무제한 덤프와 결합해 즉시 폭발한다.
> 실제 이번 run에서 폴백 발동 여부는 코드에 남은 `logger.warning("DEBUG[2] LLM selected relevant: %s ...")`(`schema_analyzer.py:734`) 로그로 확정 가능. **착수 전 로그로 지배적 기여(전체 테이블 vs. 유사어 전량 vs. 샘플데이터)를 먼저 확인**한다.

### 2.4 재계획 오진 — 인프라성 에러를 "결과 0건"으로 오분류

`replanner`는 task error 텍스트를 LLM에 전달하나(`_summarize_result` → `실패 (error=...)`, `src/nodes/replanner.py:222`), `REPLANNER_SYSTEM_TEMPLATE`가 "0건 → 조건 완화" 쪽으로 유도해 **토큰 한계/연결 실패 같은 인프라성 에러를 '서버 못 찾음'으로 오인**한다. 결과:

- 범위를 넓힐수록(은행 → 전체 인프라 b0+gp+yd) **스키마 범위↑ → 토큰↑ → 같은 에러 재발**. 자기 악화 루프.
- `_filter_futile_retries`(`replanner.py:320`)는 ">0행 확보" 케이스만 차단하고 **인프라성 에러는 차단 대상이 아니다**.
- 사용자 관찰("연번 빼고 재검색")은 이 무의미한 범위 확대의 표면 증상.

### 2.5 멀티DB 경로 스키마 환각 (task 3)

task 3이 생성한 `polestar.cmm_resource` / `cmm_resource_system` / `cmm_measurement`는 **존재하지 않는 테이블**(`SQL0204N SDQ000.CMM_RESOURCE undefined`). b0는 DB2(스키마 `SDQ000`)이며 실제 컬럼은 `cmm_resource.hostname` 등 EAV 모델이다(프로필 `polestar_b0.yaml:36-43,224`). 멀티DB 분기(`subagents.py:591-593` → `multi_db_executor`)가 **실제 스키마 그라운딩 없이 SQL을 생성**한 것으로 보인다(단일DB 경로의 풀 검증 루프를 거치지 않음).

### 2.6 [도메인 정정] b0는 hostname == VM 이름 — 재매핑 불필요

사용자 확인: **은행 폴스타(polestar_b0)는 VM 이름과 호스트네임을 동일 값으로 사용**한다. 프로필이 이를 명시한다:
- `polestar_b0.yaml:36-43`: EAV `Hostname` 속성값 = `cmm_resource.hostname` (동일 값)
- `polestar_b0.yaml:85-87`: `Hostname` synonyms = ["EAV호스트명", "서버명", "호스트네임"]

반면 김포/여의도(cm_gp/cm_yd)는 계열사 간 호스트네임 규칙 충돌을 피하려 **별도 VM명(`r.name`)을 사용**하므로 `[filter_conditions 필드명 재매핑]`(Known Mistakes 2026-06-10)이 필요했다. **이 재매핑을 b0에 적용해선 안 된다.**

**함의**: 입력 파싱이 `field=hostname, value=###`로 이미 정답을 냈으므로, **토큰만 정상이었다면 task 1이 그대로 성공했어야 한다.** 재계획의 "유사 이름/연번 제거/VM명 추정"은 b0에서 **이중으로 틀린 방향**(① 인프라 에러를 결과부재로 오진 ② b0는 hostname이 권위)이다.

---

## 3. 목표 / 성공 기준

1. (P0) polestar_b0 단일 서버 조회의 SQL 생성 프롬프트가 데이터 평면 한도 내로 유지되어 task 1이 성공한다.
2. (P1) `schema_analyzer`가 어떤 경우에도 "전체 테이블"을 무제한 반환하지 않는다(상한·폴백 정책).
3. (P2) 재계획이 인프라성 에러(토큰/연결/undefined)를 "결과 0건"으로 오분류하지 않으며, 토큰 초과 task에 대해 "범위 확대" 재계획을 하지 않는다.
4. (P3) 멀티DB 경로도 단일DB와 동일한 스키마 검증을 통과하며, 스키마 로드 실패 DB는 환각 SQL 대신 skip + 진단한다.
5. (P4 도메인) b0 조회는 hostname 필터를 권위로 사용하고 gp/yd식 VM명 재매핑을 적용하지 않는다.
6. **회귀 없음**: 정상 크기 DB(gp/yd, test_db) 및 기존 단일/멀티 의도 경로는 무변경 동작.

---

## 4. 개선 설계

> **설계 관점(중요)**: "컨텍스트를 줄인다"는 *필요한 정보를 깎는다*가 아니라 *질의와 무관한 노이즈(전체 스키마·전량 유사어·샘플)를 제거하고 관련 컨텍스트만 정확히 남긴다*는 뜻이다. b0의 쓸모 있는 스키마는 프로필 선언상 **3개 테이블**(`cmm_resource`/`core_config_prop`/`cmm_metric_stat_[h,d,m]`, `polestar_b0.yaml:166`)이고 본 질의는 그 중 2개만 사용한다. 무관한 ~190K가 오히려 환각의 원천(task 3)이므로, 정확히 좁히는 것이 환각을 **줄인다**. EAV 조인 가이드·few-shot 예시는 작고 환각을 막는 핵심이므로 **끝까지 보존**한다. 최소 관련 집합조차 한도에 못 맞추면 **환각 SQL 대신 진단 종료**.

### 4.★ [확정·최우선] allowed_tables 유사어 확장 게이트 (schema_analyzer)

§1.5 실측으로 확정된 진짜 폭증원의 직접 수정. **이것만으로 b0 relevant이 수백 → ≤5로 수렴**한다.

**(1) 유사어→`_allowed` 확장을 질의 매칭분으로 게이트** (`schema_analyzer.py:756-762`):
- 현재: `get_synonyms(db_id)`의 **모든** col_key 테이블을 `_allowed`에 추가 → 누적 유사어 전 테이블이 들어옴.
- 수정: 해당 유사어(또는 그 한국어 표현)가 **이번 질의(user_query/query_targets)에 실제 등장**할 때만 그 테이블을 `_allowed`에 추가. 매칭 없으면 추가 안 함.
- 대안(더 단순): 이 동적 확장을 **제거**하고 프로필 `allowed_tables`(5개)를 권위로. 원래 취지("캐시 갱신 후 필터링 유실 방지")는 캐시 무효화 시점 처리로 대체.

**(2) Step 2 보충도 질의 관련분만** (`schema_analyzer.py:781-789`):
- 현재: `_allowed` 전 테이블을 relevant에 무조건 보충.
- 수정: LLM 미선택 allowed 테이블 중 **query_targets/필터 컬럼과 관련된 것만** 보충(또는 프로필 핵심 테이블만). 무조건 전량 주입 금지.

**(3) 회귀 가드**: b0 "### 서버 IP/OS/CPU/메모리" 질의에서 최종 relevant이 {cmm_resource, core_config_prop, cmm_metric_stat_*} 수준(≤5)으로 떨어지고 system_prompt < 95K인지 단위 검증. 유사어가 수천 개 등록된 상태를 픽스처로 재현(시한폭탄 회귀 방지).

> **주의(Known Mistakes 2026-06-11 정합)**: 사전류(column_synonyms)는 전 테이블×컬럼 규모일 수 있으므로, 이를 순회해 테이블 집합을 만드는 로직은 **반드시 질의 매칭 게이트 + 상한**을 둔다.

### 4.0 [부차] DB2 introspection 앱 스키마 스코프 (mcp_server)

> **강등 사유(2026-06-30 측정)**: POLESTAR=389/409이므로 스코프 효과는 ~5%(20테이블)뿐 — 토큰 폭증을 못 잡는다. 근본 수정은 §4.2(P1)다. 본 절은 ① DBA/EVMON 노이즈 제거 ② 다중 스키마 동명 테이블 컬럼 병합 차단 ③ 스키마 접두사 정합(환각 차단)의 **정합성·위생 개선**으로만 유효.

**(1) `SourceConfig`에 스키마 스코프 필드 추가** (`mcp_server/mcp_server/config.py:34-44`):

| 신규 필드 | 예 | 의미 |
|-----------|----|------|
| `schema` (또는 `db_schema`) | `"SDQ000"` (b0) | introspection을 이 스키마로 한정. 미설정 시 기존 동작(비-SYS 전체) 유지 |

- config.toml/env에서 소스별 설정(`{SOURCE}_SCHEMA`). PostgreSQL 소스는 미설정(기존대로 단일 DB).

**(2) DB2 introspection SQL을 스키마 한정으로 교체** (`mcp_server/mcp_server/tools.py`):
- `_db2_search_objects_sql`: `TABSCHEMA NOT LIKE 'SYS%'` → **`TABSCHEMA = '<schema>'`**(설정 시). 미설정이면 기존 폴백.
- `_db2_get_columns`/`_db2_get_primary_keys`/`_db2_get_foreign_keys`: `TABNAME = ...` 조건에 **`AND TABSCHEMA = '<schema>'`** 추가(다중 스키마 동명 테이블 컬럼 병합 차단).

**(3) 스키마 접두사 정합** (task 3 환각 차단): introspection이 스키마로 좁혀지면 반환 테이블이 곧 앱 스키마 소속임이 보장된다. 생성 SQL의 테이블 수식(`polestar.` vs `SDQ000.`)이 실제와 일치하도록, 프로필 few-shot 예시의 스키마 접두사를 실제(`SDQ000` 또는 무수식+기본스키마)로 정정하거나, DB2는 무수식 테이블명 + 연결 기본 스키마를 사용하도록 가이드.

**효과**: b0 introspected 스키마가 PG와 동일 크기로 수렴 → §2.2의 무제한 덤프 입력 자체가 작아져 토큰 폭증 원천 차단. P0/P1은 잔여 방어선으로 유지.

### 4.1 [P0] 데이터 평면 토큰 예산 가드 (query_generator)

**핵심**: §4.0 적용 후에도 남는 방어선. SQL 생성 직전에 입력 토큰을 보수적으로 추산하고, 데이터 평면 모델 한도 초과 시 **노이즈 우선 제거(noise-first pruning)** 로 무관 컨텍스트를 걷어내며, 그래도 최소 관련 집합이 한도를 넘으면 환각 SQL 대신 명확한 에러로 종료한다.

**(1) 신규 노브 — `WorkerConfig` 또는 기존 LLM 설정에 추가** (D-042 B6의 제어 평면 노브와 **별도**):

| 신규 설정 (env) | 기본값 | 의미 |
|-----------------|--------|------|
| `WORKER_MAX_INPUT_TOKENS` | `90000` | FabriX(GptOss) 입력 안전 상한(서버 95,232 − 여유 ~5K) |
| `WORKER_TOKEN_ESTIMATE_DIVISOR` | `4` | 보수적 근사(chars/divisor). tiktoken 미반입 폐쇄망 폴백 |

- Known Mistakes(2026-03-23 JSON, 2026-06-10 systemd) 정합: 단순 int 설정, pydantic-settings 필드로 읽음(`os.getenv` 금지).

**(2) 노이즈 우선 제거 순서** — 질의와 무관한 것부터 걷어내고 관련 컨텍스트는 보존:
1. **샘플 데이터 제거**(`schema_info["tables"][*]["sample_data"]`) — SQL 생성에 영향 최소
2. **`resource_type_synonyms`/`eav_name_synonyms`를 질의 용어 매칭분만**(전량 덤프 `query_generator.py:646-660` 중단; Known Mistakes 2026-06-11의 "사용자 용어와 매칭된 항목만" 원칙을 **프롬프트 주입에도** 적용 — 당시엔 UI 역조회만 고침)
3. **`column_synonyms`도 매칭분만** 유지
4. **무관 테이블 제거**(query_targets/필터 컬럼이 참조하지 않는 테이블) — relevant를 질의가 실제 건드리는 집합으로 좁힘. **EAV 가이드·few-shot 예시는 보존**(환각 방지 핵심)
5. 최소 관련 집합(필요 테이블 + 가이드)조차 초과 → `error_message`에 "대상 DB 스키마가 커서 단일 호출에 담을 수 없습니다(토큰 초과). 조회 컬럼/조건을 좁혀주세요." 설정 후 종료(**환각 SQL 금지**)

**(3) 적용 지점**: `query_generator._build_system_prompt` 반환 직후 추산→축소 루프. 토큰 계측은 chars/4 근사(상한 트리거용으로 충분), tiktoken 가용 시에만 정밀 사용.

### 4.2 [P1·근본/최우선] schema_analyzer 전체-테이블 폴백 제거 + 선택 견고화

> **근본 수정**: §2.0 측정상 POLESTAR=389 테이블. 폭증은 이 389개가 통째로 덤프되는 것이며, 그 1차 트리거는 `_llm_select_relevant_tables`가 389→3으로 못 좁히고 **전체를 반환**하는 것. 따라서 본 절이 P0보다 상위 근본 수정이다.

`_llm_select_relevant_tables`의 전체 반환(`:1078-1079`, `:1133-1138`)을 다음으로 교체:
1. 프로필 `allowed_tables`가 있으면 그 집합으로 제한(b0/gp/yd 모두 보유 — 389 중 폴스타 핵심 테이블만 남김)
2. 없으면 query_targets 도메인 키워드 휴리스틱 + 필터 컬럼(hostname 등) 보유 테이블 매칭
3. 그래도 비면 **상한 N개**(예: 15)로 캡 + `logger.warning` + 처리현황 노출("테이블 자동 선택 실패, 상위 N개로 제한")
- **절대 무제한 전체 반환 금지**(389-테이블 덤프의 1차 트리거 차단).
- **선행 검증**: 실패 run `DEBUG[2] LLM selected relevant`가 ~389였는지 확인 → 폴백 발동이면 본 절이 직접 해소. ~3인데도 198K였으면 §4.1(P0) step2(전역 유사어 덤프)가 지배 요인.
- **table_summaries 입력도 주의**(`schema_analyzer.py:1083-1090`): 389 테이블 전체 요약을 테이블 선택 LLM에 보내는 것도 부담. allowed_tables가 있으면 그 범위만 요약하여 선택 LLM 입력도 축소.

### 4.3 [P2] 재계획 인프라성 에러 결정적 가드

**(1) 에러 분류기 추가** — task error 문자열에서 인프라성 패턴 감지:
- 토큰 초과: `Input tokens must be`, `context length`, `maximum context`
- DB 연결/실행: `SQL0204N`/`undefined name`(스키마 환각), 연결 timeout/refused
- 위 패턴이면 **"결과 0건/엔티티 미발견"으로 요약하지 않는다.**

**(2) `_filter_futile_retries` 확장**(또는 신규 가드): 선행 task가 **인프라성 에러로 실패**했으면, "같은 의도를 범위만 넓혀" 재조회하는 후속(독립 data_query, 같은 대상)을 **차단**. 토큰 초과는 범위를 넓히면 악화되기 때문.

**(3) `_summarize_result` 보정**(`replanner.py:211-238`): error 결과를 `실패(인프라성: 토큰 초과)` 처럼 **분류 라벨**을 붙여 LLM이 "0건"으로 오인하지 않게 한다.

**(4) 종료 메시지**: 인프라성 에러로 종료 시 일반 "데이터 없음"이 아니라 원인을 보존(Known Mistakes 2026-06-26 — 진단 summary를 일반 문구로 덮지 말 것 / D-039 처리현황에 생성 SQL·에러 노출).

### 4.4 [P3] 멀티DB 경로 스키마 검증 강제

- `multi_db_executor` 경로의 각 DB SQL도 `query_validator`의 "참조 테이블/컬럼 존재" 검사를 통과시킨다(단일DB와 동등).
- 스키마 로드 실패/빈 스키마인 DB는 **환각 SQL 생성 대신 skip + `db_errors`에 진단 기록**.
- P0 토큰 가드가 멀티DB 경로(target별 호출)에도 동일 적용되는지 점검.

### 4.5 [P4] b0 hostname 권위 명시 (도메인 정정)

- `config/db_profiles/polestar_b0.yaml` `query_guide`에 `[filter_conditions 필드명]` 주석 추가:
  - "b0는 VM 이름과 호스트네임이 동일하다. `field=hostname`이면 `cmm_resource.hostname`을 그대로 사용하라. **gp/yd식 `r.name`(VM명) 재매핑을 적용하지 말 것.**"
- replanner/intent 프롬프트가 b0 대상에서 "연번 제거·유사 이름·VM명 추정" 류 범위 확대를 생성하지 않도록 가이드(P2 가드와 병행 — 정확한 hostname 조회가 0건이면 그건 "그 서버가 없음"이지 명명 규칙 문제가 아님).

---

## 5. 변경 파일 (예상)

| 파일 | 변경 |
|------|------|
| `mcp_server/mcp_server/config.py` | `SourceConfig.schema`(앱 스키마 스코프) 필드 + env/toml 로드 추가(P0/근본 §4.0) |
| `mcp_server/mcp_server/tools.py` | `_db2_search_objects_sql`·`_db2_get_columns/pk/fk`에 `TABSCHEMA = '<schema>'` 한정(§4.0) |
| `src/config.py` | `WORKER_MAX_INPUT_TOKENS`/`WORKER_TOKEN_ESTIMATE_DIVISOR` 노브 추가(데이터 평면 전용, 제어 평면 D-042 노브와 별도) |
| `src/nodes/query_generator.py` | SQL 생성 전 토큰 예산 가드 + 단계적 축소(P0). 유사어 전량 덤프(`:646-660`)를 매칭분만으로 |
| `src/nodes/schema_analyzer.py` | **(확정·최우선 §4.★)** 유사어→`_allowed` 확장(`:756-762`)을 질의 매칭분만으로 게이트, Step2 보충(`:781-789`) 질의 관련분만. (보조) 전체-테이블 폴백 제거 → allowed_tables/휴리스틱/상한 캡(P1) |
| `src/nodes/replanner.py` | 인프라성 에러 분류기 + 무의미 범위확대 차단 + `_summarize_result` 라벨링(P2) |
| `src/prompts/replanner.py` | "인프라성 실패는 범위 확대 대상 아님" 규칙 추가(P2) |
| `src/nodes/multi_db_executor.py` / `query_validator.py` | 멀티DB 스키마 검증·빈 스키마 skip(P3) |
| `config/db_profiles/polestar_b0.yaml` | hostname 권위·재매핑 금지 주석(P4) |
| `docs/02_decision.md` | D-051, D-052 추가 |
| `tests/test_nodes/test_query_generator.py` | 토큰 초과 시 단계적 축소·종료 회귀 |
| `tests/test_orchestration/test_replanner*.py` | 인프라성 에러 → 범위확대 미생성 회귀 |

---

## 6. 단계별 작업

| 단계 | 내용 | 의존 | 검증 |
|------|------|------|------|
| 0 (완료) | 디버거 실측 — §1.5 확정(relevant 4→수백, system_prompt 104K, 유사어 확장이 범인) | — | ✅ 완료 |
| 1 ✅ | **§4.★ allowed_tables 유사어 확장 게이트** (`_synonym_tables_matching_query`, 상한 15) — 구현 완료, D-051 기록 | 0 | ✅ synonym_gate 8 + eav_supplement 통합 양성/음성 15 passed, arch_check exit 0 |
| 2 | P1 schema_analyzer 전체-폴백 제거 + 선택 견고화 (보조) | 0 | 단위: query_targets 비어도 전체 미반환 |
| 3 | P0 query_generator 토큰 예산 가드 + 노이즈 우선 제거 (보조 방어선) | 0 | 단위: 한도 초과 시뮬 → 축소/진단 종료 |
| 4 | P2 replanner 인프라성 에러 가드 | — | 단위: 토큰 에러 task → 범위확대 후속 0개, 진단 보존 |
| 5 | P3 멀티DB 스키마 검증·skip | 1,2 | 통합: 환각 테이블(SQL0204N) 미생성 |
| 6 | P4 b0 hostname 권위 + POLESTAR 수식 강제(기본스키마 SDQ000≠테이블스키마 POLESTAR) | — | 통합: hostname 직사용, 무수식 SQL 미발생 |
| 7 (부차) | §4.0 DB2 introspection 스키마 스코프(DBA/EVMON 제거·동명 병합 차단·접두사 정합) | — | 통합: b0 수집 409→389, 위생 개선(폭증 해소 아님) |
| 8 | `arch_check` + 회귀(gp/yd/test_db 무변경) + D-051/D-052 기록 | 1-7 | `python scripts/arch_check.py --ci` |

---

## 7. 기록할 의사결정 (docs/02_decision.md 반영 예정)

- **D-051. allowed_tables 유사어 확장 게이트(확정 근본) + 데이터 평면 토큰 예산 가드(보조)**: ① **확정 근본**(디버거 실측, §1.5) — `schema_analyzer`가 `allowed_tables`(b0=5개 화이트리스트)에 **등록된 모든 컬럼 유사어의 테이블을 무조건 추가**(`:756-762`)하고 Step2가 이를 전량 relevant로 보충(`:781-789`)하여, 유사어 누적과 함께 relevant이 수백 테이블로 부풀고 system_prompt가 104K로 95K 한도를 초과한다. → 유사어→`_allowed` 확장을 **이번 질의 용어 매칭분만**으로 게이트(또는 제거)하고 Step2 보충도 질의 관련분만으로 한정한다(시한폭탄형 회귀 가드 포함). ② **보조 방어** — Plan 50/D-042의 "데이터 평면=대용량" 전제는 실측과 다르며 FabriX(GptOss)도 입력 95,232 토큰 제한이므로, 데이터 평면 프롬프트에 `WORKER_MAX_INPUT_TOKENS` 가드 + 전체-폴백 제거(P1) + 노이즈 우선 제거(P0)를 보조로 둔다. ③ **부차** — DB2 introspection 앱 스키마 스코프(위생). D-042 전제 정정·보강. **(주의: POLESTAR=389 테이블 자체가 큰 것은 사실이나, 정상 경로에서는 allowed_tables 5개로 좁혀지므로 그 자체가 원인은 아니다 — 원인은 유사어 확장이 그 좁힘을 무력화한 것.)**
- **D-052. 재계획 인프라성 에러 가드 + b0 hostname 권위**: replanner는 토큰 초과·연결 실패·undefined name 등 **인프라성 에러를 "결과 0건"으로 오분류하지 않으며**, 그런 task에 대해 "검색 범위 확대" 후속을 생성하지 않는다(범위 확대는 토큰 문제를 악화시킴). 또한 **polestar_b0는 VM 이름과 호스트네임이 동일**하므로 `field=hostname`을 `cmm_resource.hostname`으로 직접 사용하고 gp/yd식 VM명 재매핑을 적용하지 않는다. D-049(무의미 재시도) 확장, Known Mistakes 2026-06-10(gp/yd 재매핑)과 비충돌(대상 DB가 다름).

---

## 8. 리스크 / 주의

| 리스크 | 대응 |
|--------|------|
| DB2 앱 스키마가 1개가 아님(여러 스키마에 폴스타 객체 분산) | `SourceConfig.schema`를 리스트 허용 또는 IN 절. 운영 DB2 스키마 구성을 배포 전 확인(`SELECT DISTINCT TABSCHEMA FROM SYSCAT.TABLES WHERE TABNAME LIKE 'CMM_%'`) |
| 스키마 한정으로 정작 필요한 타 스키마 테이블 누락 | 미설정 시 기존 동작(비-SYS 전체) 유지하는 폴백. b0만 명시 스코프 |
| 토큰 축소가 SQL 정확도를 떨어뜨림(필요 테이블/유사어 누락) | 노이즈부터 제거(샘플→유사어 전량→매칭만→무관 테이블), 필터 컬럼·query_targets 참조 테이블·EAV 가이드·few-shot은 끝까지 보존 |
| chars/4 근사가 부정확 | 상한 트리거용으로 충분(보수적). 정밀 필요 시 tiktoken 가용 시에만, 폐쇄망 미반입 시 근사 폴백 |
| 전체-테이블 폴백 제거로 정상 케이스 누락 | allowed_tables 우선 + 상한 캡 + 처리현황 경고 노출(투명성). gp/yd/test_db 회귀로 확인 |
| 재계획 가드가 정당한 후속(0건→완화)까지 막음 | 인프라성 에러일 때만 차단. 진짜 0건(>0행 미확보 & 에러 없음)은 기존대로 허용 |
| b0 외 DB에 hostname 권위 규칙 오적용 | 규칙을 b0 프로필에만 한정. gp/yd 재매핑(2026-06-10)은 그대로 유지 |
| 데이터 평면 한계가 모델 교체로 바뀜 | `WORKER_MAX_INPUT_TOKENS`를 env 노브로 노출(하드코딩 금지) |
