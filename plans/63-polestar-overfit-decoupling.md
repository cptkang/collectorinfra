# 63. 폴스타 과적합 분리 — DB 어댑터 격리 + 공통 경로 LLM 일반화 (Polestar Decoupling & LLM Generalization)

> 작성일: 2026-07-18 · **갱신**: 2026-07-20 (소스 전수 재검토 — §1.1 L1~L3·L5 실측 현행화, L8 추가, §1.3 신설, 트랙 P1·P2·P4 상세 보강)
> **대상 기능**: 공용 SQL 생성 유틸(`src/utils/query_gen_common.py`), 검증(`src/nodes/query_validator.py`), 스키마 분석(`src/nodes/schema_analyzer.py`), 생성 프롬프트(`src/prompts/query_generator.py`), 단일/멀티 생성 경로(`src/nodes/query_generator.py`, `src/nodes/multi_db_executor.py`), 시맨틱 컴파일러(`src/nodes/semantic_compiler.py`), 프로필/시맨틱 모델(`config/db_profiles/*.yaml`, `config/semantic_models/*.yaml`), 아키텍처 검사(`scripts/arch_check.py`)
> **선행/근거**: 2026-07-18 과적합 검토 실측(본 문서 §1 인벤토리), D-020(폴스타 하드코딩 제거 — LLM 기반 범용 구조 분석), D-035(결정적 규칙=판단·LLM=보조), D-066(단일/멀티 경로 대칭), D-068(폼필 결정적 조립 — LLM 반복 실패의 결정화 이력), D-072(EX 평가 하네스), D-076(시맨틱 모델 채널), D-085~D-087(최근 가드류). ※ D-086·D-087은 2026-07-18 **구현 완료·`docs/02_decision.md` 등재**(2026-07-20 현재 워킹트리) — 본 계획 L1 대상 블록(`build_prior_rows_block`)은 죽은 배선이 아니라 단일/멀티 **활성 배선** 상태다(§1.1 L1 참조)
> **사용자 지시(2026-07-18)**: ①폴스타 특화 영역은 **별도로 관리**하고, ②타 모니터링 솔루션 DB 조회 시에는 **공통 경로를 같이 쓰는** 방식으로 과적합을 분리하며, ③코드로 과적합된 부분은 **최대한 LLM을 활용하는 방식**으로 전환한다.
> **신규 결정(본 계획 예약, 착수 시 등재)**: **D-088**(계층 분리 원칙 + 과적합 재발 방지 가드), **D-089**(폴스타 DB 어댑터 분리 — 동작 불변 이동), **D-090**(공통 경로 어휘 매핑 LLM 전환 — 프로필 오버라이드 병행), **D-091**(모의 비폴스타 DB 범용성 회귀 하네스)
> ※ 번호 규칙: 2026-07-18 실측 — `docs/02_decision.md` 등재 최댓값 **D-087**(채번 라인 "다음 D-088" 일치), `grep -roE "D-0[89][0-9]" docs/ plans/` 예약 충돌 없음. **재확인 2026-07-20**: 최댓값 D-087 유지, `02_decision.md` 채번 규칙 라인에 "D-088~D-091(Plan 63 폴스타 과적합 분리 예약 — 착수 시 등재)" 명기 완료. 착수 직전 `## D-` 헤더·「변경 이력」 표를 재확인하여 충돌 시 다음 빈 번호로 재조정. 계획 번호 = `ls plans/` 최댓값(62) + 1 = **63**.
> **상태**: 계획(미착수)

---

## 1. 배경 — 과적합 검토 실측 인벤토리 (2026-07-18)

시스템의 폴스타 격리 채널(POLESTAR_DB_IDS 템플릿 게이트, db_profiles EAV 게이트, semantic_models)은 대체로 지켜지고 있으나,
**공용 계층에 폴스타 스키마 리터럴이 누적**되는 누수가 실측됐다. 현재 `ACTIVE_DB_IDS=polestar` 단독이라 실동작 문제는 없지만,
등록된 비폴스타 DB(cloud_portal·itsm·itam — 프로필/시맨틱 모델 없음)를 활성화하는 순간 잘못된 프롬프트 주입·기능 침묵 무력화로 드러난다.

### 1.1 누수 지점 (심각도 순)

| # | 위치 | 내용 | 비폴스타 DB에서의 증상 |
|---|------|------|----------------------|
| L1 | `query_gen_common.build_prior_rows_block` (D-086) | 공용 주입 블록 텍스트에 `cmm_resource`의 resource_type/resource_key 문장 하드코딩(`query_gen_common.py:624`) + `{col} IN`의 col을 `_PRIOR_HOSTNAME_HINTS`/`_PRIOR_NAME_HINTS` 휴리스틱으로 hostname/name 단정. ※2026-07-20 재실측: D-086 구현으로 이 블록은 단일(`query_generator.py:326`)·멀티(`multi_db_executor.py` `prior_block` 파라미터) **대칭 배선 + prior_rows 존재 시 트랙 C 컴파일 우회(양 경로)** 가 활성 — P1 수정 시 이 배선·우회 조건은 보존 대상 | 존재하지 않는 테이블을 언급하는 오지시 주입 |
| L2 | `query_gen_common.build_stat_month_block` (D-076 후속4) | 기본 `metric_table="cmm_metric_stat_m"` + `s.stat_date='YYYYMM'` 규약. 단일/멀티 **양 경로 무조건 주입**, 호출부가 파라미터 미사용(2026-07-20 재확인: 단일 `query_generator.py:314`·멀티 `multi_db_executor.py:509` 모두 `metric_table` 인자 미전달) | "지난달" 표현만 있으면 어느 DB든 폴스타 통계 테이블 지시 주입 |
| L3 | `query_gen_common`·`semantic_compiler`의 폴스타 시맨틱 상수·기본값 | `_SERVER_RESOURCE_TYPE="server.Server"`, `_METRIC_NOUN_RT`('cpu'→'server.Cpus' 등), `_metric_select_line`(avg_val/max_val), `build_multi_resource_pivot_sql`. **(2026-07-20 추가 실측)** ①`decimal_cast_example`(server.Cpus/Utilization/avg_val 예시 — 미매핑 alias 안내로 단일 `query_generator.py:876`·멀티 `multi_db_executor.py:712` **양 경로 호출**) ②`_eav_pattern_parts`의 폴스타 기본값(cmm_resource/core_config_prop/stringvalue_short/resource_conf_id/configuration_id) ③`_SERVER_NAME_TERMS`의 "폴스타등록명" ④`semantic_compiler.py`의 코드 기본값(`.get("entity_table","cmm_resource")`·`"cmm_metric_stat_m"`·`"server.Server"` — 373·396·423행)과 폴스타판 전용 프롬프트(`prompts/semantic_compiler.py`) | 발동은 프로필/시맨틱 모델 게이트로 차단되나 **재사용 불가**(다른 EAV DB에 코드 수정 필요) — 공용 계층에 DB 지식 상주 |
| L4 | `query_validator._check_routing_filter_misuse` | GROUP_PATH·RESOURCE_NAME·'폴스타' 키워드를 전 DB에서 검사 | 실해 없음(토큰 부재 시 무동작)이나 계층 위반 |
| L5 | `schema_analyzer`의 알람 도메인 지식 2개소 | ①`_alarm_core_set` 로컬 `{"cmm_alarm","cmm_alarm_def","cmm_alarm_active"}` 하드코딩(939행) ②**(2026-07-20 추가)** 테이블 선택 LLM 프롬프트의 alarm_query `intent_hint`(1262행)에 동일 테이블명 중복 하드코딩 — 두 지점 동시 이동 필요 | 존재 검사로 완충되나 비폴스타 알람 DB에서 폴백이 빈 목록 + 오지시 힌트 주입 |
| L6 | `query_validator._check_left_join_where_demotion` 메시지 (D-085) | "예: server.Server의 서버명" 예시 문구(727행) | 표면적(무해) |
| L7 | (경계선) `correct_servername_hostname_mapping` | 내용은 폴스타 규칙(서버명=name≠hostname)이나 프로필 `entity_table` 존재로 게이트 | 서버명=hostname이 정당한 미래 EAV DB에서 오교정 위험 |
| L8 | **(2026-07-20 신규)** 공통 `QUERY_GENERATOR_SYSTEM_TEMPLATE` | 스키마 접두사 지시의 예시가 `polestar.cmm_resource` 리터럴(`prompts/query_generator.py:21`) — POLESTAR 게이트가 **없는** 공통 템플릿에 실주입되는 유일한 폴스타 리터럴 | 비폴스타 DB 프롬프트에 폴스타 테이블 예시 노출(표면적이나 게이트 부재라 즉시 노출) |

### 1.2 잘 분리되어 있는 것 (변경 금지 기준선)

- **범용 코어**: D-085 강등 가드(SQL 의미론), D-087 CTE 전처리 일원화, D-086 오케스트레이션부(prior_rows 배선·input_from 교정·planner 예시 3-1), 금지 키워드/인젝션/LIMIT 검사.
- **폴스타 격리 채널**: POLESTAR 전용 템플릿(`POLESTAR_DB_IDS` 게이트 — `query_generator.py:602` 실측), `db_profiles/*.yaml`, `semantic_models/*.yaml`, `alarm_allowed_tables`. D-086 ④의 알람 환각 금지 규칙(Strict Constraint 5)도 폴스타 게이트 템플릿 내부에 추가되어 격리 유지 확인(2026-07-20).

### 1.3 스코프 구분 — DB 인스턴스 라우팅·별칭 어휘 하드코딩 (2026-07-20 재검토 신설)

스키마 리터럴(§1.1)과 별개로, **폴스타 인스턴스 식별 어휘(db_id·위치·별칭)** 가 공용 계층 전반에 분산 하드코딩되어 있다(P4-1 스캔 예행에서 함께 검출). 이는 "무엇이 어디에 있는가"(스키마 지식)가 아니라 "어느 DB로 보낼 것인가"(라우팅 지식)로 성격이 다르고, 준선언 채널(`src/routing/domain_config.py`의 `DB_DOMAINS.aliases`)이 이미 존재하나 각 지점이 이를 소비하지 않고 **중복 정의**하는 것이 문제다:

- `field_mapper._GENERIC_DB_TOKENS`·`_DB_FOREIGN_REGION_TOKENS`(209~222행 — db_id별 지역 변별 토큰)
- `process_query._LOCATION_DB_HINTS`(53~59행), `input_parser` 위치/환경 표면어(D-065)와 프롬프트 DB 별칭(규칙 10 등), `context_resolver` 위치/환경 키워드(39행), `subagents` DB명 키워드(62행)
- 프롬프트 예시류: `semantic_router`(폴스타 db_id 예시 25건), `intent_planner` 예시 4("여의도 개발 폴스타"), `replanner`(40행), `general_inference` 재질의 안내(179~186행), `cache_management` 예시

**본 계획에서의 처리**: 트랙 범위에 **포함하지 않는다** — 스키마 리터럴과 원인·수리 방식·회귀 리스크(D-065 계열 라우팅 회귀 이력)가 다르다. P4-1 overfit_check에서 **별도 카테고리(routing-vocab)로 분류·화이트리스트**하여 가시화만 하고, `DB_DOMAINS`/프로필 단일 출처화는 후속 계획으로 분리한다(§8 확인 항목 5).

## 2. 목표 / 비목표

**목표**
1. 폴스타 특화 코드·프롬프트·검사를 **DB 어댑터 계층으로 물리적 격리** — 공용 계층에는 특정 DB의 테이블/컬럼/리소스타입 리터럴 0.
2. 타 모니터링 솔루션 DB는 **공통 경로(LLM 생성 + 선언적 지식 주입)** 만으로 동작 — 신규 DB 편입 = 코드 0줄, 프로필/모델 yaml 추가만.
3. 코드에 하드코딩된 DB 지식(어휘→스키마 매핑 등)을 **LLM 추론 + 선언적 오버라이드** 구조로 전환.
4. 과적합 재발을 막는 **결정적 가드(리터럴 스캔)** 상설화.

**비목표 (하지 않는 것)**
- 폴스타 **결정적 조립 경로의 제거** — D-068 계열은 LLM 반복 실패를 실측으로 결정화한 자산이다(Known Mistakes "프롬프트 강제가 반복 실패하면 결정적 조립 대상"). 어댑터로 **이동**하되 동작은 불변.
- 폴스타 경로의 동작 변경 — 전 트랙 "이동-불변(move-only)" 또는 "옵트인 증분" 원칙, EX 하네스(D-072)로 전후 동치 게이트.
- 스키마 자동 발견의 재설계(D-020 채널 유지).

## 3. 설계 원칙 — 3계층 구조

```
[계층 1] 공통 코어 (DB-agnostic)
  · LLM SQL 생성 파이프라인, SQL 의미론 가드(강등/CTE/인젝션), 오케스트레이션(prior_rows 등)
  · 스키마 리터럴 금지 — overfit_check가 강제
[계층 2] 선언적 지식 채널 (데이터)
  · db_profiles/*.yaml + semantic_models/*.yaml — DB별 어휘·구조·규칙은 전부 여기로
  · 프롬프트 주입 블록의 파라미터(통계 테이블명, 기간 컬럼, 식별 컬럼, 금지 규칙)의 단일 출처
[계층 3] DB 어댑터 (코드가 필요한 특화 로직만)
  · src/db_adapters/polestar/ — 결정적 조립기, 전용 템플릿, 전용 validator 검사
  · 레지스트리 디스패치: 코어는 어댑터의 존재를 모른다(어댑터가 훅에 등록)
```

**LLM vs 결정적 코드의 판단 기준(D-035 정합)** — "최대한 LLM 활용"은 **지식(무엇이 어디에 있는가)의 하드코딩 제거**를 뜻하며, **정합성 방어(무엇이 틀렸는가)** 는 계속 결정적 가드가 맡는다:
- 어휘→스키마 매핑, 테이블 선택, 관계 추론 → **LLM + 프로필 오버라이드** (프로필에 명시가 있으면 결정적 우선, 없으면 LLM 추론).
- SQL 안전성·구조 검증, 실측 회귀 이력이 있는 쿼리 형태(폼필 피벗 등) → **결정적 유지** (단, 폴스타 지식은 어댑터/모델로 이동).
- Known Mistakes "LLM 비결정성 대응"과의 절충: LLM 전환 항목은 반드시 EX 하네스 전후 측정을 통과해야 하며, 저하 시 해당 매핑을 프로필 오버라이드로 고정하는 폴백을 계획에 내장한다.

## 4. 트랙별 수정 계획

### 트랙 P1 — 공용 주입 블록 즉시 일반화 (저위험, 선행)

| 항목 | 수정 | 대상 누수 |
|------|------|----------|
| P1-1 | `build_prior_rows_block`: `cmm_resource` 문장(624행) 제거 → 일반 원칙("선별 조건을 대상 DB에 없는 테이블/컬럼으로 재표현·환각 금지")으로 교체. 폴스타 추가 힌트는 어댑터 `prompt_hints` 훅(P2-1)이 덧붙임. ※주의: 폴스타 성능 템플릿 Strict Constraint 5(D-086 ④)가 "[선행 작업 결과 서버 스코프] 블록"을 **제목으로 상호 참조** — 블록 제목 변경 시 템플릿 문구 동시 갱신 | L1 |
| P1-2 | `build_prior_rows_block`: `{col} IN` 컬럼명을 단정하지 않고 "선행 결과의 `{col}` 값 목록 — 대상 DB의 해당 식별 컬럼에 IN 적용"으로 완화. 식별 컬럼명은 프로필 `identity_columns`(신설) 우선, 없으면 현행 휴리스틱(`_PRIOR_HOSTNAME_HINTS`/`_PRIOR_NAME_HINTS` — hostname 우선·name 폴백, D-061 정합) 유지. prior_rows 존재 시 트랙 C 우회 조건(단일·멀티 대칭)은 불변 | L1 |
| P1-3 | `build_stat_month_block` 호출부 배선: 단일(`query_generator.py:314`)·멀티(`multi_db_executor.py:509`) 모두 프로필/시맨틱 모델의 통계 테이블·기간 컬럼을 파라미터로 주입(파라미터는 기존재). 프로필 부재 DB는 **블록 미주입**(일반 규칙 "질의의 기간 표현을 스키마의 시간 컬럼으로 해석하라"만 남김 — LLM 판단) | L2 |
| P1-4 | D-085 메시지 예시를 중립화("예: 피벗 기준 행") | L6 |
| P1-5 | **(2026-07-20 추가)** 공통 `QUERY_GENERATOR_SYSTEM_TEMPLATE`의 스키마 접두사 예시 `polestar.cmm_resource`(21행)를 형식 예시(`<스키마>.<테이블>`)로 중립화 — 게이트 없는 공통 템플릿의 유일한 실주입 리터럴이라 P1에서 즉시 처리 | L8 |

**검증**: 기존 폴스타 테스트 무회귀(블록 내용 단언 테스트는 일괄 갱신 — Known Mistakes "값 단언 테스트 repo 전체 grep". **실측**: `tests/test_orchestration/test_prior_rows_scope.py` 13건 중 블록 문안 단언부(38~64행 — "환각 금지" 문장·`IN` 절 형식)가 직접 갱신 대상이며, 단일/멀티 주입 대칭·트랙 C 우회 테스트는 그대로 재사용 자산) + 신규: 프로필 없는 DB 컨텍스트에서 두 블록에 `cmm_` 리터럴 부재 단언.

### 트랙 P2 — 폴스타 어댑터 분리 (구조 이동, 동작 불변)

**P2-1 어댑터 패키지 신설**: `src/db_adapters/polestar/` (arch_check `MODULE_LAYER_MAP`에 `src.db_adapters`=application 등재 — 2026-07-20 실측: 현행 맵은 `src.nodes`=application·`src.prompts`=prompts·`src.orchestration`=orchestration). 전용 템플릿을 어댑터로 옮겨도 공용 prompts 계층이 어댑터를 참조할 필요가 없도록 **노드 레벨 디스패치**(query_generator가 레지스트리에서 템플릿 조회)로 배선한다. **어댑터 등록 부트스트랩(어댑터 모듈 임포트 지점 — 그래프 조립 또는 엔트리)을 명시하고 배선 테스트로 고정**할 것 — "정의만 있고 소비처 없는" 죽은 레지스트리 방지(Known Mistakes·D-086 죽은 배선 계열). 어댑터 인터페이스(프로토콜)는 훅 5종으로 최소화:

```python
class DBAdapter(Protocol):
    db_ids: set[str]                          # 담당 DB (POLESTAR_DB_IDS 소비)
    def system_template(self, intent) -> str | None       # 전용 프롬프트 템플릿 (없으면 공통)
    def prompt_hints(self, context) -> list[str]          # 주입 블록 추가 힌트 (prior_rows 등)
    def try_deterministic_sql(self, state) -> str | None  # 결정적 조립 (D-068 폼필 피벗 등)
    def validator_checks(self) -> list[Callable]          # DB 전용 검증 (GROUP_PATH 등)
    def schema_table_policy(self, intent) -> TablePolicy | None  # alarm core set 등
```

**P2-2 이동 인벤토리** (순수 이동 + 레지스트리 배선, 로직 수정 금지):

| 이동 대상 | 현 위치 | 이동 후 |
|-----------|--------|--------|
| `POLESTAR_*_SYSTEM_TEMPLATE` 3종 | `src/prompts/query_generator.py` | `src/db_adapters/polestar/prompts.py` |
| `_SERVER_RESOURCE_TYPE`·`_METRIC_NOUN_RT`·`_metric_select_line`·`build_multi_resource_pivot_sql/block`·`classify_metric_field` | `src/utils/query_gen_common.py` | `src/db_adapters/polestar/assembler.py` (P3-1에서 상수를 시맨틱 모델 로드로 대체) |
| `_check_routing_filter_misuse` | `src/nodes/query_validator.py` | `src/db_adapters/polestar/validators.py` → validator가 어댑터 훅 순회 |
| `_alarm_core_set` **+ alarm_query `intent_hint` 프롬프트(1262행)** | `src/nodes/schema_analyzer.py` | 어댑터 `schema_table_policy` (또는 프로필 `alarm_core_tables` 필드 — P3-4와 통합). 두 지점이 같은 테이블명을 중복 하드코딩하므로 **동일 훅에서 셋·힌트 텍스트를 함께 제공** |
| `correct_servername_hostname_mapping`의 규칙 | `src/utils/query_gen_common.py` | 규칙 데이터를 프로필 `identity_rules`로, 실행 엔진만 공용 잔류 (L7 해소). `_SERVER_NAME_TERMS`의 "폴스타등록명" 표면어도 프로필 선언으로 |
| `decimal_cast_example` **(2026-07-20 추가)** | `src/utils/query_gen_common.py` | 어댑터/프로필의 미매핑 alias 예시 제공 훅 — 호출부가 단일 `query_generator.py:876`·멀티 `multi_db_executor.py:712` **양 경로 대칭**임을 이동 시 실측 |
| `_eav_pattern_parts` 폴스타 기본값 **(2026-07-20 추가)** | `src/utils/query_gen_common.py` | 코드 기본값(cmm_resource 등 5종) 제거 → 폴스타 프로필 yaml에 명시(함수는 프로필 값 필수화로 공용 잔류) |
| `semantic_compiler` 폴스타 기본값·전용 프롬프트 **(2026-07-20 추가)** | `src/nodes/semantic_compiler.py`(373·396·423행)·`src/prompts/semantic_compiler.py` | 코드 기본값은 시맨틱 모델 yaml 명시 필수화(P3-2와 동일 커밋), 프롬프트의 폴스타 브랜딩 문구는 모델 메타에서 렌더 |

**주의**: 단일/멀티 경로 **양쪽 배선을 실측**(D-066 — 한쪽만 훅 전환하는 비대칭이 반복 실수 유형). 이동 커밋은 기능 커밋과 분리.

### 트랙 P3 — 코드 하드코딩 → LLM + 선언 전환

| 항목 | 현재(코드 하드코딩) | 전환 후 | 게이트 |
|------|--------------------|---------|-------|
| P3-1 메트릭 어휘 매핑 | `_METRIC_NOUN_RT` 명사→resource_type, 집계어→값컬럼 고정 | 시맨틱 모델 yaml의 measure 정의를 단일 출처로 어댑터가 로드. **공통 경로**: 프로필 무 DB는 스키마 컨텍스트 기반 **LLM 매핑**(기존 synonym/E5 계단과 통합, 확정 임계 미달 시 후보 제시 D-012) | EX 하네스 폴스타 전후 동치 |
| P3-2 기간/통계 테이블 판단 | `cmm_metric_stat_[h,d,m]` 분기·YYYYMM 규약이 프롬프트·코드 산재(+`semantic_compiler`의 `metric_tables` 코드 기본값) | 시맨틱 모델 `time_grain`(테이블·컬럼·포맷) 선언 → 주입 블록·컴파일러가 이를 렌더(코드 기본값 제거는 P2-2 이동분과 동일 커밋). 무선언 DB는 LLM이 스키마에서 시간 컬럼 판단(D-076 후속4의 결정적 월 해석 값 자체는 유지 — 해석은 결정적, **적용처만** 선언/LLM) | 〃 |
| P3-3 식별 컬럼/서버명 규칙 | hostname/name 힌트 상수, 서버명=name 교정 코드 | 프로필 `identity_columns`·`identity_rules` 선언 + 실행 엔진 공용화. 무선언 DB는 LLM이 스키마 설명으로 식별 컬럼 추론 | 폼필 회귀 스위트 |
| P3-4 알람 도메인 지식 | `_alarm_core_set` 하드코딩 | 프로필 `alarm_allowed_tables`(기존재)에 `alarm_core_tables` 추가. 무선언 DB는 LLM 테이블 선택에 위임(하드캡은 개수 상한만 공용 유지) | 알람 질의 회귀 |

**LLM 전환 공통 안전장치**: ①프로필/모델 선언이 있으면 **항상 결정적 우선**(LLM은 무선언 DB의 폴백) — 폴스타는 선언 완비 상태라 동작 불변. ②LLM 매핑 결과는 자동 등록 금지(Known Mistakes 오염 자기강화 루프 — 쓰기 지점 결정적 차단 유지). ③신규 LLM 호출은 스키마 캐시 열 설명과 함께 1회 배치(호출 수 증가 상한 명시).

### 트랙 P4 — 재발 방지 가드 + 범용성 회귀 하네스

- **P4-1 `scripts/overfit_check.py`**: 공용 계층(`src/utils`·`src/nodes`·`src/orchestration`·공용 `src/prompts`)에서 DB 스키마 리터럴(`cmm_`, `server\.`, `polestar`(문자열 리터럴), `stat_date` 등 — 어댑터 디렉토리 제외) grep 스캔. 기존 잔존분은 명시적 화이트리스트(사유 주석 필수)로 기준선화, 신규 유입은 CI 실패(`--ci`). `/arch-check`처럼 스킬 등록. **2026-07-20 스캔 예행 실측**: 공용 계층 24개 파일에서 검출(주요 카운트 — `prompts/query_generator` 115건은 대부분 POLESTAR 게이트 템플릿 내부라 P2 이동으로 자연 소거, `query_gen_common` 44건, `semantic_router` 25건, `query_generator` 23건, `multi_db_executor` 18건, `process_query` 17건, `field_mapper` 15건, `semantic_compiler` 10건). 스키마 리터럴과 라우팅 어휘(§1.3)를 **카테고리 분리 집계**(schema-literal / routing-vocab)하여 후자는 본 계획에서 소거 대상이 아님을 리포트에 명시.
- **P4-2 모의 비폴스타 DB 픽스처**: 제2 모니터링 솔루션 스키마(예: `generic_mon` — servers/metrics/alerts 평탄 테이블, 프로필·시맨틱 모델 **없음**)를 testdata에 추가. 범용성 회귀 테스트: ①공통 경로만으로 기본 질의 SQL 생성·실행, ②주입 블록에 폴스타 리터럴 무오염, ③폴스타 어댑터 미발동(훅 호출 0), ④E2E는 `RUN_E2E=1` 옵트인.
- **P4-3 신규 DB 편입 체크리스트 갱신**: Known Mistakes의 기존 체크리스트(위치 힌트·base_url·방언·스키마 한정)에 "프로필/시맨틱 모델 작성(선택) — 없으면 공통 LLM 경로로 동작"을 추가.

## 5. 실행 순서와 회귀 전략

```
P1 (주입 블록 일반화) ──→ P2 (어댑터 이동) ──→ P3 (LLM 전환) ──→ P4-2 (범용성 하네스)
                                   │
P4-1 (overfit_check) ──── P1 직후 도입(기준선) → P2·P3가 화이트리스트를 소거해 가는 구조
```

- 각 트랙 완료 시 `python scripts/arch_check.py --ci` + 전체 pytest + **EX 하네스(D-072) 폴스타 3경로 측정**(`scripts/eval_text2sql.py --path {graph,orchestration,multidb}` + `tests/text2sql/test_ex_harness.py` — 2026-07-20 실체 확인) — 전후 EX 동치가 이동-불변의 판정 기준.
- P3는 항목별 독립 플래그 불필요(선언 우선 원칙으로 폴스타 동작 불변이 구조적으로 보장) — 단 P3-1 LLM 폴백 경로만 `GENERIC_LLM_MAPPING=true` 옵트인으로 시작.
- 커밋 단위: 트랙별 분리, P2는 "이동만" 커밋과 "배선" 커밋 분리(diff 리뷰 가능성).

## 6. 검증 기준 (트랙별 verify)

| 트랙 | 판정 |
|------|------|
| P1 | 프로필 무 DB 컨텍스트에서 주입 블록 `cmm_` 리터럴 0 (신규 테스트) + 폴스타 기존 테스트 무회귀 |
| P2 | EX 하네스 전후 동치(3경로) + `grep -rn "cmm_\|server\." src/utils src/nodes` 히트가 화이트리스트만 잔존 + 단일/멀티 훅 배선 실측 테스트 |
| P3 | 폴스타 EX 동치(선언 우선 경로) + `generic_mon`에서 LLM 폴백 매핑으로 기본 질의 성공 + LLM 매핑 자동 등록 0(쓰기 차단 테스트) |
| P4 | overfit_check `--ci` 통과(화이트리스트 외 0건) + 범용성 회귀 스위트 통과 |

## 7. 산출물

- `src/db_adapters/__init__.py`(레지스트리)·`src/db_adapters/polestar/{prompts,assembler,validators,policy}.py`
- `config/db_profiles/*.yaml` 스키마 확장: `identity_columns`·`identity_rules`·`alarm_core_tables`·`time_grain`(semantic_models와 중복 시 모델 우선 — 단일 출처 명시)
- `scripts/overfit_check.py` + `.claude/skills/overfit-check.md`
- `testdata/generic_mon/` 픽스처 + `tests/test_generic_path/` 범용성 회귀 스위트
- `docs/02_decision.md` D-088~D-091 등재, `scripts/arch_check.py` LAYER_MAP 갱신

## 8. 착수 전 사용자 확인 항목

1. **어댑터 위치/이름**: `src/db_adapters/polestar/` 제안 — 기존 `src/domain`(auth/user 등 코어 도메인)과 구분되는 이름인지 확인.
2. **LLM 호출 증가 허용치**: P3 공통 경로의 무선언 DB는 질의당 어휘 매핑 LLM 호출이 1회 추가될 수 있음(스키마 캐시에 배치·캐싱 전제). 허용 여부.
3. **generic_mon 픽스처 범위**: 최소 3테이블 평탄 스키마로 제안 — 실제 도입 예정인 타 모니터링 솔루션(예: Zabbix/OpenSearch 계열)이 정해져 있으면 그 스키마를 모사하는 편이 검증 가치가 높음.
4. **P2 이동 범위**: 전용 템플릿 3종의 어댑터 이동은 diff가 큼(약 500줄) — 1차에서 상수/조립기·validator만 이동하고 템플릿은 2차로 미루는 축소안도 가능.
5. **(2026-07-20 추가) 라우팅·인스턴스 어휘(§1.3) 스코프**: 본 계획은 스키마 리터럴만 다루고, 위치·별칭 어휘의 `DB_DOMAINS`/프로필 단일 출처화는 **후속 계획으로 분리하는 안을 권장**(성격·회귀 리스크가 다름, D-065 계열 라우팅 회귀 이력). 본 계획에 트랙으로 포함할지 확인.

## 9. 위험과 완화

| 위험 | 완화 |
|------|------|
| LLM 어휘 매핑의 비결정성(Known Mistakes 핵심 원칙과 긴장) | 선언 우선 원칙(폴스타 불변)·확정 임계 미달 시 후보 제시(D-012)·EX 게이트·자동 등록 차단. LLM은 "선언 없는 DB의 폴백"으로 한정 |
| 이동 중 경로 비대칭(단일만 훅 전환 등) | D-066 대칭 실측 테스트를 트랙 P2 완료 조건에 포함 |
| 어댑터 과설계(현재 어댑터 1개뿐) | 훅 5종으로 인터페이스 최소화, 두 번째 어댑터가 생기기 전까지 추상화 확장 금지(Simplicity First) |
| 화이트리스트 형해화(신규 리터럴을 화이트리스트로 회피) | 화이트리스트 항목에 사유·소거 예정 트랙 주석 필수, P2·P3 완료 기준에 "화이트리스트 감소량" 포함 |
| 이동-불변 검증 누락 | EX 하네스 3경로(graph/orchestration/multidb) 전후 측정을 트랙 완료 게이트로 고정 |
