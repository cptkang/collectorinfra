# 인용 가능 주장 목록 (Verbatim Claims)

> 작성일: 2026-08-28 | **목적**: 문헌의 **원문 인용과 실측 수치**를 한 곳에 고정해, 이후 계획서가
> PDF·유료 사이트를 다시 열지 않고 인용할 수 있게 한다.
> **규칙**: 각 주장은 ①원문 인용(영문 그대로) ②출처 위치 ③확인 방법 ④우리 프로젝트에서의 함의를 갖는다.
> 인용을 **의역해서 강한 주장으로 바꾸지 않는다** — 원문에 없는 수치를 만들지 말 것.
> 서지 정보(저자·게재·DOI·인용수)는 `bibliography.csv`가 정본이다.

---

## C1. 무조건 묻기는 사용자를 방해한다 ★

**출처**: `CQ-COST-01` (Zou et al., IPM 2022) — 초록 및 §4.4
**확인**: 저자 소속 공개본 PDF `https://irlab.science.uva.nl/wp-content/papercite-data/pdf/zou2022asking.pdf`
(2026-08-28 본문 추출로 직접 확인. OpenAlex에는 초록 미색인 — DOI 페이지는 Elsevier 유료)

> "always showing all CQs may be risky and **low-quality CQs do disturb users**"

> "only showing high-quality CQs while **hiding other CQs receives better gains with less effort**"

**실험 규모**: 사용자 89명, 웹 검색 과제 세트, CQ 품질 궤적(고/저품질을 시간 순으로 배치) 조작.

**함의**: 되묻기는 **선택적 게이트 뒤에만** 두어야 한다. "일단 물어보고 사용자가 고르게 한다"는
기본값은 문헌이 명시적으로 위험하다고 판정한 설계다.

---

## C2. 저품질 CQ의 대가는 세션 시간 약 2배 ★

**출처**: `CQ-COST-01` §4.4 (Discussion)

> "Even though in some cases always showing low-quality CQs lead to a higher number of hits in a
> session, it comes with the cost of **nearly twice as much time** as they spend in a session with
> fewer shown CQs."

**함의**: 되묻기의 비용은 "왕복 몇 초"가 아니라 **세션 전체 시간의 배수**로 나타날 수 있다.
따라서 절감 추정치가 초 단위인 지점(예: 존당 SQL 실행 ~50ms)에서는 묻는 것이 **순손실**이다.

---

## C3. 더 많이 물어도 이득은 비례하지 않는다

**출처**: `CQ-COST-01` §4.4가 인용한 Aliannejadi et al. (2021a)

> "asking more CQs does not always return the same amount of gain (i.e., **lower rate of gain**)"

**주의**: 이는 `CQ-COST-01`이 **2차 인용**한 것이다. 원문(Aliannejadi et al. 2021a)을 직접 확인하지
않았으므로, 강한 근거가 필요하면 원문을 먼저 확보할 것.

**함의**: 질문 축을 늘리는 것(존 + 솔루션 + 기간 …)은 수익 체감이다. 축은 최소로 유지한다.

---

## C4. 긴 질의의 정통 해법은 부분 결과 즉시 제시다 ★

**출처**: `PROG-01` (Hellerstein, Haas & Wang, "Online Aggregation", SIGMOD 1997) — 인용 928
**확인**: 제목·서지·인용수 OpenAlex 조회(2026-08-28). 본문 미열람 — **개념 인용만 한다.**

**핵심 개념**: 집계 질의를 끝까지 기다리지 않고 **실행 중 부분 결과를 계속 갱신 제시**하며,
사용자가 충분하다고 판단하면 중단한다. 이후 progressive data analysis 계열로 이어진다(`PROG-02`).

**함의**: 대기 시간 문제에 대해 **사용자에게 묻는 것보다 앞서 검토해야 하는 대안**이다.
사용자 개입 0 · 정보 손실 0 · 첫 결과까지의 시간이 더 짧다.

> **인용 시 주의**: 원논문은 DB 집계 질의의 통계적 추정(신뢰구간 포함)을 다룬다. 우리가 차용하는 것은
> **"완료 전 부분 결과를 내보낸다"는 상호작용 패턴**뿐이며, 추정·신뢰구간은 차용하지 않는다.
> 이 구분을 흐리면 과대 인용이 된다.

---

## C5. 반복 노출되는 경고·대화상자는 효력이 감쇠한다 ★

**출처**: `HABIT-01` (Vance et al., MIS Quarterly 42(2), 2018) · `HABIT-02` (Anderson et al., JMIS 33(3), 2016)
**확인**: 제목·게재·인용수 OpenAlex 조회(2026-08-28). 본문 미열람 — **제목·초록 수준의 개념 인용.**

**핵심**: 반복 노출에 따른 주의 감쇠(habituation)를 **fMRI · 시선추적 · 현장실험** 3중 방법으로 측정.
`HABIT-01`은 종단(longitudinal) 설계, `HABIT-02`("From Warning to Wallpaper")는 그 기제를 다룬다.

**함의**: 같은 질문을 반복하면 무의미해진다 → ①**한 번만 묻고 승계** ②**발동률을 관측 지표로 두고
상한을 관리**한다. 발동률을 보지 않으면 습관화를 통제할 수 없다.

> **선행 참조**: `plans/78` §11.4의 2차 인용(*안드로이드 권한 대화상자 — 주의 17% · 정확 이해 3%*)이
> 같은 논거다. 위 두 편은 그 논거의 **동료심사 1차 출처**에 해당한다(수치 자체는 다른 연구).

---

## C6. "어느 소스를 몇 개까지 조회할지"는 자동 추정 문제다 ★

**출처**: `AUTONARROW-01` (Kulkarni, Tigelaar, Hiemstra & Callan, CIKM 2012)
**확인**: 제목·저자·게재·인용수 OpenAlex 조회(2026-08-28). 본문 미열람 — 개념 인용.

**핵심**: 주제별로 분할된 컬렉션(shard)에서 **어느 샤드를 랭킹하고 몇 개에서 끊을지(cutoff)** 를
**질의별로 자동 추정**한다. 분산 검색의 자원 선택(resource selection) 계보.

**함의**: 범위 축소는 **묻기 전에 자동으로 시도해야 한다.** 82에서는 `capabilities`/`requires` 매칭이
그 역할을 한다. `RouteLLM`(`plans/78` §11.1 · P12 — 강모델 호출 14%로 비용 −85%)도 **사용자 개입 0**으로
같은 이득을 얻는 사례다.

---

## C7. 명료화는 라운드 전체를 세밀하게 다뤄야 한다

**출처**: `CQ-ANS-01` (Krasakis et al., ICTIR 2020) — 초록(OpenAlex 색인분, 2026-08-28 확인)

> "there needs to be some **fine-grained treatment of the entire conversational round of
> clarification**, based on the explicit feedback which is present in such mixed-initiative settings"

**함의**: 질문을 던지는 것만으로는 부족하고 **답변을 어떻게 소비하는지**가 결과 품질을 좌우한다.
82에서는 `selected_scope`를 구조화 필드로 받아 **결정적으로 고정**하는 것이 이에 대응한다
(자연어 재조합·LLM 재해석 금지).

---

## C8. 묻기 vs 결과 제시는 위험 통제 문제로 정식화되어 있다

**출처**: `CQ-RISK-01` (Wang & Ai, arXiv 2101.06327, 2021) — **preprint · 인용 2**
**확인**: 제목·서지 OpenAlex 조회(2026-08-28). 본문 미열람.

**핵심**: 대화형 검색에서 **되묻기와 결과 제시 사이의 선택을 강화학습으로 통제**한다.

**함의**: 82의 결정적 비용 게이트와 **같은 문제**를 다루지만 **학습형**이다. 82는 미채택했다 —
근거는 라벨 부재 · D-035(결정적 게이트) · P13(학습형은 그 자체가 Tier 2) · 축이 2개뿐.
`plans/82` §16.3에 재검토 조건을 명시했다.

> **인용 강도 주의**: preprint이고 인용 2건이다. **설계 근거로 쓰지 말고** "같은 문제의 선행 정식화가
> 존재한다"는 맥락 제시에만 쓸 것.

---

## C9. 빈 결과는 "없다"가 아니라 **원인을 지목**해야 한다 — 협조적 응답 ★

**출처**: `EMPTY-MFS-01` (Godfrey, IJCIS 1997 · 인용 114) 계열의 **cooperative answering** 원칙
**확인**: 제목·게재·인용수 OpenAlex 조회(2026-08-28) + 후속 문헌·구현체 설명(웹) 대조. 본문 미열람 — **개념 인용**.

> "When a query fails, it is more cooperative to identify the cause of failure rather than just
> reporting the empty answer set."

**함의**: `"조건에 해당하는 데이터가 없습니다"` 는 문헌 기준으로 **불충분한 응답**이다. 어느 조건에서
끊겼는지 지목하는 것이 30년 된 DB 연구의 기본 처방이다. 이는 우리 프로젝트의
*"침묵적 폴백·강등 금지"* · *"0건/실패 진단은 진입·게이트별로 끊긴 지점부터 확정"* 과 같은 원칙이다.

---

## C10. MFS / XSS — 끊긴 지점을 찾는 표준 개념과 **N회 질의 알고리즘** ★

**출처**: `EMPTY-MFS-01`(Godfrey 1997 — 정초) · `EMPTY-DIAG-01`(Fokou, Jean, Hadjali & Baron,
KIS 2016 — *"from diagnosis to relaxation"*)
**확인**: 제목·게재·인용수 OpenAlex(2026-08-28) + 개념 정의는 후속 문헌·공개 구현체
(`lias-laboratory/mfs4udb`) 설명 대조. 본문 미열람 — **개념 인용**.

| 개념 | 정의 | 우리 쪽 대응 |
|---|---|---|
| **MFS** (Minimal Failing Subquery) | 그 자체로 이미 0건이 되는 **최소** 조건 부분집합 | *"여기서 끊겼습니다"* — 첫 0건 조건 |
| **XSS** (maXimal Succeeding Subquery) | 결과가 남는 **최대** 조건 부분집합 | *"여기까지는 N건 있습니다"* — 마지막 성공 단계 |

**알고리즘 사실 2가지**(엔지니어링 판단의 근거):

1. **조건이 N개면 후속 질의 N회로 MFS/XSS 하나를 찾을 수 있다** — 단순 순차 알고리즘이 존재한다.
   → 조건을 하나씩 더해가며 `COUNT(*)`를 재는 **계단 프로브**가 교과서적 방법이고, 새 발명이 아니다.
2. **N차 규모의 MFS를 모두 찾는 것은 NP-hard이지만, K를 고정하면 다항**이다.
   → 프로브 수에 **상한을 두는 것이 정당한 설계**다(전수 탐색 금지).

**함의**: 우리 구현은 ①조건 순서대로 누적 프로브 ②상한 K로 절단 ③절단 사실 노출 — 이 세 가지를
지키면 문헌의 단순 알고리즘과 정합하며, 비용은 조건 수 × 1회 COUNT다.

> **주의**: 원 문헌은 **관계형/RDF 질의의 조건 집합**을 다룬다. 우리는 자연어에서 파싱된
> `filter_conditions`를 그 조건 집합으로 취급하는데, **파싱 자체가 조건을 누락했을 가능성**은
> 문헌이 다루지 않는 별개 위험이다(예: "갑자기 상승"이 SQL로 표현되지 못하는 경우). 그 위험은
> MFS/XSS로 진단되지 않으므로 **별도로 노출해야 한다.**

---

## 인용 강도 등급 (이 파일의 규약)

| 등급 | 의미 | 이 파일의 해당 주장 |
|---|---|---|
| **A — 원문 직접 확인** | 본문/초록을 직접 읽고 인용문을 추출했다 | C1 · C2 · C7 |
| **B — 개념 인용** | 제목·게재·인용수만 확인하고 널리 알려진 핵심 개념만 차용했다 | C4 · C5 · C6 · **C9 · C10** |
| **C — 2차 인용** | 다른 논문이 인용한 것을 재인용했다. 원문 미확인 | C3 |
| **D — 맥락 제시용** | preprint·저인용. 설계 근거로 쓰지 않는다 | C8 |

**규칙**: 계획서가 **설계 근거**로 쓰는 주장은 A 또는 B여야 한다. C·D는 맥락·대안 언급에만 쓴다.
등급을 올리려면 원문을 확보해 인용문을 이 파일에 추가하고 등급을 갱신한다.
