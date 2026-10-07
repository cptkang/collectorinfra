---
name: itam-knowledge
description: ITAM 반출 run의 근거 묶음으로 지식 자산 원천 파일(K1 가이드·K4 규칙 섹션·K3 설명·유사어·K2 예시·K8 템플릿)을 쓰고 검증한다. 「ITAM 반출 run이 왔다」「ITAM 지식 자산 갱신」「itam knowledge」「근거 묶음으로 가이드 써 줘」처럼 폐쇄망 ITAM 반출물을 받아 자산을 고칠 때 쓴다(plans/143 · D-316).
---

# ITAM 지식 자산 작성

D-316 ①에 따라 앱 안에는 LLM 생성기가 없다. 이 스킬을 실행하는 Claude Code가 작성자다. 값을 보지 못하므로 근거 묶음에 있는 것만 쓴다.

## 언제

- 폐쇄망 ITAM 반출 run(`results/itam_bench/<run_id>/`)이 새로 들어와 지식 자산 원천을 갱신할 때
- 내부망 검증 결과(실패 항목)가 돌아와 철회 처리가 필요할 때

## 절차

1. 근거 묶음 생성: `python -m scripts.itam_bench --evidence results/itam_bench/<run_id>`
   - 결과는 `<run>/knowledge_evidence/`(값 0 · 누출 관문 통과분만). 관문이 실패하면 멈추고 보고한다.
2. 근거 묶음 읽기: `index.yaml` → `scenarios.yaml` → `turns.yaml` → `p1.yaml` → `previous_cycle.yaml` → 필요한 군의 `tables.<군>.yaml`만. 큰 군 파일(`tables.tcdmsif.yaml` 등)은 grep이나 부분 읽기로 좁힌다.
3. 작성 순서: **K6 → K1 → K4 → K3 → K2 → K8**
   - K6: 코드(`derive_kind_rules`)가 정의 `kind`에서 파생한다. 원천 파일을 쓰지 말고, 검증 출력의 `derived` 규칙만 확인한다. K1·K4는 K6 문장을 되풀이하지 않는다.
4. 검증: `python -m scripts.itam_bench --validate-knowledge`
   - 모의 DB(`testdata/itam_closed_sim` · 3308)가 기동돼 있으면 정적 검사와 실행을 함께 돌린다. 기동돼 있지 않을 때만 `--static-only`를 붙이고, 그 사실을 보고에 적는다.
   - 오류가 0이 될 때까지 원천을 고친다. 검증기가 오탐을 내면 검증기는 고치지 말고 보고한다.
5. 빌드: `python -m scripts.itam_bench --build-assets results/itam_bench/<run_id>`
6. `git diff`로 `config/`와 원천 파일의 변경을 확인한다. 커밋은 사용자가 지시할 때만 한다.

## 원천 파일 계약

위치는 `testdata/itam_bench/closed/knowledge/`다. 정본은 `src/domain/knowledge_assets.py`(형식·정적 검사)와 `src/domain/query_templates.py`(K8)다. 충돌하면 그 모듈을 따른다.

공통: 파일 머리에 `version: 1`과 `items:` 목록을 둔다(K8만 `templates:`). 항목마다 다음 칸을 단다.
- `id`: 파일 안에서 유일하다.
- `origin: claude_code`
- `evidence: <run_id>`
- `status: active | withdrawn`

내부망 검증에서 실패한 항목(`previous_cycle.yaml`의 `validation_failed`)은 **지우지 않는다**. `status: withdrawn`과 `reason`을 달아 이력을 남긴다.

| 파일 | 자산 | 항목 칸 | 계약 |
|---|---|---|---|
| `guide.yaml` | K1 `query_guide` | `text` | 연결본 ≤ 8,000자(목표 3,000~5,000자). 중괄호·코드 펜스를 쓰지 않는다. 언급하는 식별자는 실존해야 하고, 조회 대상(`allowed_tables`) 밖 테이블은 언급하지 않는다. 테이블 군별 역할, 현행·이력·적재 구분, 질문 유형별 후보 테이블을 적는다. |
| `prompt_section.yaml` | K4 DB 전용 규칙 섹션 | `text` | 항목과 연결본 모두 ≤ 6,000자. 중괄호를 쓰지 않는다. SQL 펜스는 블록 5개까지이고 실존 식별자만 쓰며 모의 DB에서 실행된다. 실행 검증이 없는 회차에는 펜스 없이 규칙 문장만 쓴다. 백틱 안에는 테이블·컬럼 이름만 넣는다(함수·형식 문자열은 백틱 밖에 쓴다). |
| `descriptions.yaml` | K3 컬럼 설명 | `table`·`column`·`text` | 컬럼은 실존해야 하고 같은 컬럼에 항목은 하나다. 설명은 ≤ 200자이며 값(따옴표 리터럴·코드 열거·5자리 이상 숫자)을 넣지 않는다. 컬럼 이름을 되풀이하지 말고 연결 키·형식·코드 처리처럼 이름에 없는 정보를 쓴다. 내부망 Redis에 이미 있는 설명은 덮지 않으므로, 이름만으로 뜻이 모호한 칸(코드·여부·구분·날짜·식별자) 위주로 쓴다. |
| `synonyms.yaml` | K3 유사어 시드 | `table`·`column`·`words` | 낱말은 2자 이상이어야 한다. 다른 컬럼 이름과 같은 낱말, 여러 컬럼에 걸리는 다의어, 같은 컬럼의 중복 항목은 쓰지 않는다(D-142). 시나리오 어휘 ↔ 컬럼 연결 근거가 있는 것만 소수로 쓴다. |
| `examples.yaml` | K2 예시 + 이력 시드 | `question`·`sql`·`description`·`tables` | `SQLGuard` → `validate_sql`(한글 식별자 포함) → `tables`와 SQL 참조 일치 → 모의 DB 실행 순으로 검증된다. 사용률 질문은 쓰지 않는다. |
| `query_templates.yaml` | K8 결정적 조립 템플릿 | `id`·`intent`·`triggers`·`slots`·`sql`·`tables` | MariaDB SELECT 한 문장으로 쓴다. 자리표는 `:slot`, 한글 식별자는 백틱으로 감싼다. 세미콜론·주석·중괄호·백슬래시를 쓰지 않는다. 슬롯 형식은 `center`·`dept_code`·`date`·`date_range`·`hostname`·`code`(column 필수)·`keyword`다. 상세는 아래 「K8 작성 규칙」. |

### K8 작성 규칙

`check_templates`(계약)·`bind_template`(바인딩)과 `--validate-knowledge`가 실제로 거절하는 규칙이다. 근거는 `src/domain/query_templates.py`이고, 어긋나면 그 모듈을 따른다.

- **항목 칸**: `id`는 영숫자로 시작하는 64자 이하(`A-Z a-z 0-9 _ . -`)이며 파일 안에서 유일하다. `intent`는 비워 두지 않는다. `triggers`는 문자열 목록이다. `tables`는 비지 않은 문자열 목록이다. `status`는 `active`·`withdrawn` 중 하나이고 생략하면 `active`다.
- **슬롯 칸**: `name`은 영소문자로 시작하는 32자 이하(`a-z 0-9 _`)이며 템플릿 안에서 유일하다. `type`은 위 7종 중 하나다. `required`는 bool이고 생략하면 `true`다.
  - `code` 슬롯에는 `column: 테이블.컬럼`이 반드시 있어야 하고, 그 컬럼은 카탈로그에 있어야 한다. 런타임 허용 값은 스키마 `_structure_meta.code_values`에서 이 `column` 문자열과 **정확히 같은 키**로 찾는다(카탈로그 원 이름과 대소문자를 맞춘다). 허용 목록이 없으면 바인딩이 거절되고 LLM 생성으로 넘어간다. 검증에서는 근거 run `code_samples.yaml`에 대표값이 없으면 그 조합이 `code_unverified`(실행 보류)로 남는다.
  - `code`가 아닌 슬롯에는 `column`을 두지 않는다.
  - `date`·`date_range` 슬롯만 `format`(`yyyymmdd`·`iso` · 생략하면 `iso`)을 둔다. 날짜가 아닌 슬롯에 `format`을 두면 거절된다.
  - 형식은 비교할 컬럼 타입에 맞춘다. char형 `…년월일` 문자열이면 `yyyymmdd`, `date`·`datetime`이면 `iso`다. 이 대응은 코드가 검사하지 않는다(슬롯에 컬럼이 없다). 잘못 고르면 거절 대신 0행이 나오므로 정의·컬럼 타입 근거로 직접 맞춘다.
- **자리표**: SQL의 자리표 집합은 슬롯 자리표 집합과 같아야 한다. `date_range` 슬롯 `x`는 `:x_start`·`:x_end` 둘을 쓴다. 문자열 리터럴·백틱 안의 `:x`는 자리표로 세지 않는다. 선택 슬롯(`required: false`)이 비면 SQL `NULL`이 들어가므로 `(:x IS NULL OR col = :x)`처럼 쓴다.
- **LIKE**: `LIKE`·`NOT LIKE` 피연산자(함수 호출 포함, 예: `CONCAT('%', :kw, '%')`)에 들어가는 자리표는 `keyword` 슬롯만 쓴다. 다른 슬롯 값의 `_`가 와일드카드로 남기 때문이다.
- **SQL 문**: `SELECT` 또는 `WITH … SELECT` 한 문장으로 쓴다. 다음은 모두 거절된다.
  - 세미콜론, 중괄호, 백슬래시
  - 주석(`--`·`#`·`/* */`). MariaDB 실행 주석 `/*! … */`·`/*M! … */`는 주석 안이 실행되므로 리터럴 안에 있어도 거절된다.
  - 부수효과 함수
- **테이블**: `tables`는 SQL의 `FROM`·`JOIN` 참조(CTE 제외 · 맨 이름 · 대소문자 무시)와 같아야 한다. 쉼표 조인(`FROM a, b`)은 참조로 읽히지 않으므로 명시 `JOIN`으로 쓴다. 모든 테이블은 카탈로그에 있어야 하고 조회 대상(`allowed_tables`) 안이어야 한다.
- **리터럴 대신 슬롯**: 사용자 질문에서 오는 값(센터·부서·날짜·호스트·코드·검색어)은 리터럴로 박지 말고 슬롯으로 받는다. 치환 코드값(`substituted_literal`)과 근거 run 리터럴(`evidence_literal` — 실행 SQL의 문자열·숫자 리터럴, 주석·정의 글의 코드 열거 값)은 템플릿 어디에 있어도 거절된다. 자리표 이름은 이 대조에서 빠진다.
- **검증 실행**: 슬롯의 모든 조합(선택 슬롯 있음/없음)을 대표값으로 바인딩한다. 그 결과를 `SQLGuard` → 부수효과 함수 → `validate_sql` → 모의 DB(바깥 행 제한) 순으로 통과시킨다. 대표값은 `center` `A`, `dept_code` `D001`, `date` `2026-01-01`, `hostname` `host01`, `keyword` `test`, `code`는 근거 run 치환값 첫 값이다.

## 작성 원칙

- 근거 묶음(정의 `kind`·`manages`·`notes`·`related`, 컬럼 이름·타입, 시나리오, 실패 턴, P1)에 없는 사실은 쓰지 않는다.
- 이름만 보고 판단한 의미·형식·연결에는 `(추정)`을 붙인다. 정의에 이미 `추정`으로 적힌 것도 같다.
- 값을 쓰지 않는다. 코드값·사람·호스트·IP·업무 이름 예시가 모두 해당한다. 코드 칸 리터럴은 프롬프트의 「### 코드값」 블록(K5 — 승인된 P1 코드값)에 그 칸이 있을 때만 쓴다.
- 근거 run 리터럴을 옮겨 적지 않는다. 대상은 `turns.yaml` 실행 SQL에 보이는 문자열·숫자 리터럴(행 수 제한 수와 3자 미만 값은 제외)과 주석·정의 글의 코드 열거 값이다. 철회 항목의 `reason`도 포함된다. 원천 파일은 커밋되므로 검증이 `evidence_literal`로 거절한다(D-301 · D-308). 날짜 예시가 필요하면 `YYYYMMDD`처럼 형식으로 쓴다.
- 사용률을 ITAM에서 답하라고 쓰지 않는다. 사용률 정본은 관측 DB다(D-308 G-6). 사용률을 언급하는 문장에는 「관측 DB」나 부정 표현을 같은 문장에 둔다.
- 「JOIN 관계 부재」처럼 근거 없는 부정 단정을 쓰지 않는다. 컬럼 목록으로 확인한 사실(예: 어떤 칸이 그 테이블에 없음)만 쓴다.
- K2·K8은 정답 SQL 근거(오라클·관측된 성공 SQL과 행 수)가 있을 때만 쓴다. 근거가 없는 회차에는 파일을 만들지 않고 「다음 반출 뒤 작성」으로 보고한다.
- K5 코드 라벨은 내부망 P1의 몫이다. 값이 필요하므로 여기서는 쓰지 않는다.
- 근거 run이 바뀌어 다시 쓴 항목은 `evidence`를 새 run ID로 바꾼다.

## 모델·위임

모델과 위임은 CLAUDE.md 「에이전트 모델과 위임」(D-304 · D-312)을 따른다. Agent 호출에 `model` 인자를 넣지 않는다.
