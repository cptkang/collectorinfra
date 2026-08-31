# 문헌 자산 (`docs/literature/`)

> 개설: 2026-08-28 | **목적**: 계획서 작성 중 확보한 문헌을 **재사용·재현·갱신 가능한 형태**로 모은다.
> 계획서가 문헌을 각자 안고 있으면 ①같은 논문을 여러 번 다시 찾고 ②인용 강도가 계획서마다 달라지고
> ③인용수·게재 상태가 어디서도 갱신되지 않는다. 이 폴더가 그 세 문제를 담당한다.

## 이 폴더의 파일

| 파일 | 역할 | 형식 |
|---|---|---|
| `bibliography.csv` | **서지 정본** — 저자·게재·DOI·OpenAlex ID·인용수(시점별)·주제·역할·사용처 | 기계 판독(CSV) |
| `claims.md` | **인용 가능 주장** — 원문 인용문과 실측 수치를 고정. 인용 강도 A~D 등급 | 사람 판독 |
| `search_log.md` | **검색 로그** — 질의·히트수·성과 + **실패한 검색어와 교훈** | 사람 판독 |
| `refresh.sh` · `_apply_refresh.py` | 인용수 갱신(시점 컬럼 **추가**, 덮어쓰지 않음) | 실행 |

## 경계 — 여기에 있는 것과 없는 것

**여기에 있다**: 서지 식별 정보(누가·어디·언제·DOI·인용수)와 **원문 인용문**.
**여기에 없다**: *"우리 프로젝트에서 이게 무슨 의미인가"* 라는 해석. 그것은 **인용하는 계획서에 남는다.**

이 분리가 D-053(사본 금지)을 지키는 방식이다 — 식별자는 중복돼도 드리프트하지 않지만, 해석이
두 곳에 있으면 갈라진다. `bibliography.csv`의 `used_by` 컬럼이 해석의 위치를 가리킨다.

```
docs/literature/bibliography.csv   →  "이 논문은 무엇인가"          (식별 · 여기가 정본)
docs/literature/claims.md          →  "이 논문이 정확히 뭐라 했나"   (인용문 · 여기가 정본)
plans/82 §6                        →  "그래서 우리는 무엇을 바꾸나"  (해석 · 계획서가 정본)
```

## 저장소의 다른 문헌 자산 — 여기로 옮기지 않았다

기존 자산은 **각자의 위치가 정본**이며, 이 표는 **찾아가는 색인**일 뿐이다(내용 복사 없음).

| 자산 | 위치 | 주제 | 정본 |
|---|---|---|---|
| **하네스 엔지니어링 문헌 본체** — ETCLOVG 7계층 · P1~P15 · IPIGuard · Task Shield · RouteLLM · LLMLingua · MemGPT 등 (동료심사 16건 + preprint 17건 + 산업자료) | `plans/78` §3.4 · **§11** | 에이전트 하네스·인젝션 방어·도구 학습·계획 분해 | **`plans/78` §11** |
| 하네스 구현 명세(내부 문서) | `sample/하네스_엔지니어링_구현_구성요소.docx` | Tier 로드맵 · 세 결정 축 · 171 오픈소스 카탈로그 | 그 파일 |
| 하네스 문헌 615편(PDF·초록·메타데이터) | **`AIOps/article/refuser/`** (**본 저장소 밖**) | 서베이 원문 + 근거 문헌 | 그 디렉토리 |
| 시멘틱 라우팅 — IEEE Access 투고본 2요인 분해 | `plans/79` §2 | 지시문 개선 vs label-only · logprob 신뢰도 | `plans/79` |
| Text-to-SQL 품질 | `docs/text2sql_quality_research.md` · `docs/plan61_literature_review.md` | 후보 생성·선별·동의어 | 그 파일들 |
| 코드베이스 규모 적정성(문헌 16건) | `docs/codebase_scale_literature_review.md` | LLM 에이전트 코드량·간소화 | 그 파일 |
| 표현·명칭 표준화 | `docs/standardization_literature_review.md` | canonicalization | 그 파일 |
| 결정적 SQL 조립 · 정규식→LLM 전환 | `docs/deterministic_sql_composition_review.md` · `docs/regex_llm_conversion_review.md` | 결정성 vs LLM | 그 파일들 |
| Instructor / 구조화 출력 | `docs/instructor_intent_extraction_review.md` | 구조화 출력 백엔드 | 그 파일 |
| AIOps 선진사례(출처 120건 · 벤더 벤치마크) | `docs/aiops_benchmark/` + `docs/aiops_benchmark_research_dossier.md` | 플랫폼·도입사례·메커니즘 | 그 폴더 |
| 임베딩 도입 보안 검토 | `docs/plan60_embedding_import_security_review.md` | 폐쇄망 모델 반입 | 그 파일 |

> **왜 통합하지 않았나**: 각 문서는 문헌 목록이 아니라 **검토 보고서**다(문헌 + 실측 + 판정이 한 몸).
> 서지만 뽑아 옮기면 판정과 근거가 분리되고, 원 문서는 참조가 끊긴 목록을 남긴다. 통합이 필요해지면
> **`bibliography.csv`에 식별자 행만 추가하고 해석은 원 문서에 두는 방식**으로 하며, 그 결정은
> `docs/02_decision.md`에 등재해야 한다(현재 미결).

## 재사용 방법

### 1) 이미 있는 문헌인지 확인 — 검색보다 먼저

```bash
# 주제로 찾기
awk -F, 'NR==1 || $12 ~ /clarifying-question/' docs/literature/bibliography.csv | cut -d, -f1,2,12

# 어느 계획이 이미 쓰고 있는지
grep -o 'plans/[0-9]*' docs/literature/bibliography.csv | sort -u

# 하네스 계열은 plans/78이 정본이므로 그쪽을 함께 grep
grep -n 'P1[0-5]\|ETCLOVG' plans/78-composite-query-host-diagnostics-orchestration.md
```

### 2) 계획서에서 인용하기

- 서지는 **`entry_id`로 참조**한다(예: `CQ-COST-01`) — 저자·연도·DOI를 계획서에 다시 적지 않아도 된다.
- 수치·인용문은 **`claims.md`의 주장 ID(C1~C8)** 로 참조한다.
- **인용 강도 등급을 지킨다**: 설계 **근거**로는 A·B 등급만 쓴다. C(2차 인용)·D(preprint·저인용)는
  맥락·대안 언급에만 쓴다(`claims.md` 말미 규약).
- 계획서에 실을 서지 표는 **이 폴더가 정본**임을 명시하고 필요한 행만 발췌한다.

### 3) 인용수·게재 상태 갱신

```bash
./docs/literature/refresh.sh            # 비교만 (읽기 전용)
./docs/literature/refresh.sh --write    # cited_by_<YYYY_MM_DD> 컬럼 추가
```

시점 컬럼을 **추가**하고 기존 값을 덮어쓰지 않는다 — *"그 계획을 세울 때 이 문헌은 얼마나 인용됐나"*
가 나중에 확인 가능해야 한다. 스킬 경로가 바뀌면 `OPENALEX=$(find ~/.claude/plugins -name openalex.py | head -1)`.

### 4) 새 문헌을 추가할 때

1. `search_log.md`에 **세션 절을 추가**하고 질의·히트수·성과를 적는다. **실패한 검색어도 적는다**(자산이다).
2. `bibliography.csv`에 행을 추가한다 — `cited_by_*`는 **조회 시점 날짜**로 컬럼명을 맞춘다.
3. 원문 인용문을 확보했으면 `claims.md`에 주장을 추가하고 **등급(A~D)** 을 부여한다.
4. `entry_id` 규칙: `<주제약어>-<역할약어>-<순번>` (예: `CQ-COST-01` · `PROG-01` · `HABIT-02`).
5. 계획서에는 **해석만** 쓰고 서지는 `entry_id`로 참조한다.

## 주의 사항

- **인용수는 OpenAlex 레코드 단위 하한값**이다. 같은 논문의 arXiv본·학회본이 별도 레코드로 분산되거나
  학회본이 미색인되는 사례가 확인됐다(`plans/78` §11 서두 · `search_log.md` 교훈 7).
  **문헌 선정은 인용수가 아니라 게재처의 동료심사 여부 + 논리적 적합성**으로 한다.
- **PDF 바이너리는 이 폴더에 두지 않는다.** 저작권 문제와 저장소 비대화를 피하기 위해 URL과 추출
  인용문만 남긴다. 대량 PDF 보관은 `AIOps/article/`(본 저장소 밖) 소관이다.
- **원문을 읽지 않은 문헌을 A 등급으로 올리지 않는다.** 널리 알려진 개념을 차용할 때도 원논문이
  실제로 다루는 범위를 넘겨 인용하면 과대 인용이 된다(`claims.md` C4의 주의 참조).
- 실 LLM·과금 API 호출은 이 폴더의 작업 범위가 아니다(D-127 승인 게이트).

## 관련 결정

D-053(사본 금지 — 식별자/해석 분리의 근거) · D-035(결정적 판단 · `claims.md` C8 미채택 근거) ·
D-127(과금 API 승인 게이트) · D-161(승격–폐기 동반 — `plans/82` §11.6 덜어내기 계획의 근거)
