# 그럴듯한 오답(오용·실수·착각 미탐지) 방지 — 문헌·산업 조사

> `plans/123` §3의 전문. 조사일 2026-09-29 · 읽기 전용 조사 에이전트 산출을 그대로 옮겼다(요약·선별은 계획서 §3).
> 신규 L-번호(L-1~L-58)는 `docs/literature/bibliography.csv`에 아직 등재하지 않았다 — 계획 게이트 확정 뒤 등재한다.


조사일 2026-09-29 · 조사 에이전트(저장소 파일 미수정)

## 0. 조사 방법과 확인 수준

- 도구: OpenAlex CLI(이번 세션은 429 없이 동작) · arXiv API(`https://export.arxiv.org` — `http://`는 301이라 빈 응답) ·
  ACL Anthology·arXiv PDF를 `pdftotext`로 본문 추출 · 공식 제품 문서 WebFetch(AWS 문서는 JS 렌더라 curl로 받아 추출).
- **확인 수준 표기**: 「본문」=PDF/HTML 본문에서 직접 확인 · 「초록」=초록만 확인 · 「개념」=게재 서지만 확인, 내용은 통용 서술.
- WebFetch 요약기가 지어낸 수치를 1건 잡았다(P2SQL "권한 강화로 공격 약 70% 감소" — 본문에 없음). **본문 grep으로 대조한
  수치만** 아래에 적었다.
- 인용수는 적지 않았다(OpenAlex 과소집계). 선정 기준은 게재처 동료심사 여부 + 논리적 적합성.
- **기존 레지스트리와의 관계**: `docs/literature/bibliography.csv`에 이미 있는 `CQ-COST-01`(Zou et al. IPM 2022 — claims C1~C3) ·
  `CQ-SEL-01`(Aliannejadi SIGIR 2019) · `CQ-RISK-01`(C8) · `HABIT-01/02`(C5) · `EMPTY-MFS-01`·`EMPTY-RELAX-01`·`EMPTY-DIAG-01`(C9·C10)은
  **재등재하지 않고 교차 참조**한다. 아래 L-번호는 모두 신규다.

---

## 1층 — 동료심사 (L-1 … L-45)

### 축 1. 답할 수 없는·모호한 질문 탐지와 기권

| L | 서지 | 확인 | 핵심 주장·수치 | 이 저장소에 주는 시사점 |
|---|---|---|---|---|
| L-1 | Wang, Gao, Li, Lou. *Know What I don't Know: Handling Ambiguous and Unanswerable Questions for Text-to-SQL*. **Findings of ACL 2023** | 본문 | 상용 제품의 실패 질의 약 3,000건 분석 → 약 20%가 문제 질의(모호 55% · 답 불가 45%). 6범주: 컬럼 모호 45% · 값 모호 10% · 컬럼 없음 30% · **값 없음 7%** · 계산 불가 6% · 범위 밖 2%. 문제 질의의 **약 95%는 사용자가 의도치 않게** 만든 것. 문제 구간을 짚은 설명을 보여주면 **90%를 사용자가 스스로 고쳤다**. 기존 파서는 문제 질의에도 "그럴듯한" SQL을 낸다 | "없는 존·호스트"는 **값 없음(value unanswerable)** 범주다. 사용자는 대부분 착각을 모른 채 묻는다 → 시스템이 짚어야 한다. 차단보다 **어느 말이 문제인지 지목**이 효과적 |
| L-2 | Bhaskar, Tomar, Sathe, Sarawagi. *Benchmarking and Improving Text-to-SQL Generation under Ambiguity* (AmbiQT). **EMNLP 2023 main** | 초록 | 3,000여 예제, 각 질의에 그럴듯한 SQL 2개. 모호성 원천 = 스키마 이름 중첩·혼동되는 조인 경로. beam search의 top-k는 토큰 수준 다양성일 뿐 해석 다양성이 아니다 | 모호성은 질문 문장이 아니라 **스키마(유사 컬럼·다중 경로)**에서 생긴다 → 스키마 쪽 사전 정보로 결정적 탐지 가능. LLM 샘플 여러 개 ≠ 해석 여러 개 |
| L-3 | Saparina, Lapata. *AMBROSIA: A Benchmark for Parsing Ambiguous Questions into Database Queries*. **NeurIPS 2024 Datasets & Benchmarks** (spotlight) | 본문 | 최고 모델(Llama3-70B)도 모호 질의 recall **31%** vs 비모호 66%. 모든 해석을 찾은 비율(AllFound) **1.9%**. 한 해석으로 강하게 쏠림(scope 모호 80% · attachment 모호 97% 이상 한쪽). **프롬프트로 "모호한가?" 물으면 실제와 무관하게 약 80%를 모호하다고 판정** | LLM은 모호 질의를 **말없이 한 해석으로 단정**한다(실패 사례 "모호 질의 단정"의 직접 근거). 동시에 LLM 자기판정 모호성 탐지는 **과잉 발동** → 되묻기 트리거를 LLM 판정에 맡기면 안 된다 |
| L-4 | Dong, Ashok Kumar, Hu, Chauhan et al. *PRACTIQ: A Practical Conversational Text-to-SQL dataset with Ambiguous and Unanswerable Queries*. **NAACL 2025 long** | 본문 | 모호 4범주(SELECT 컬럼 · WHERE 컬럼 · 컬럼 내 유사 값 · 필터 기준 모호) + 답 불가 4범주(없는 SELECT 컬럼 · 없는 WHERE 컬럼 · **없는 필터 값** · 지원 안 되는 조인). 일부 모호 범주는 되묻지 않고 **여러 해석을 함께 담은 "helpful SQL"**로 바로 답한다. 최고 분류 정확도 77.4%(Claude 3.5 Sonnet, 셀 값 오라클 제공) → 오라클 없으면 74.3%. 사람 이진 분류 93.75% | **없는 필터 값**이 독립 범주로 정식화돼 있다. 셀 값(실제 존재 값)을 주면 분류가 오른다 → 값 존재 여부는 **DB/레지스트리 조회로 결정적 판정**하는 게 맞다. "되묻기 vs 여러 해석 병기"를 범주별로 가르는 설계 선례 |
| L-5 | Lee, Hwang, Bae, Kwon, Shin, Yang, Seo, Kim, Choi. *EHRSQL: A Practical Text-to-SQL Benchmark for Electronic Health Records*. **NeurIPS 2022 Datasets & Benchmarks** | 초록 | 병원 직원 222명 설문으로 수집한 실사용 질의 + 시간 표현 + **답할 수 없는 질의를 의도적으로 포함** | 도메인 특화 + 신뢰성 평가의 가장 가까운 선례(의료). 우리 인프라 도메인도 답 불가 질의를 골드셋에 섞어야 한다 |
| L-6 | Lee, Kweon, Bae, Choi. *Overview of the EHRSQL 2024 Shared Task on Reliable Text-to-SQL Modeling on EHRs*. **ClinicalNLP Workshop @ NAACL 2024** (워크숍) | 본문 | **Reliability Score RS(c)**: 답 가능 질의에 정답 +1 · 기권 0 · **오답 −c** / 답 불가 질의에 답함 **−c** · 기권 +1. 본 지표 RS(10) = "정답 10건 = 오답 1건". 전부 기권(ABSTAIN-ALL)만 해도 20% | "그럴듯한 오답"을 기권보다 무겁게 채점하는 **바로 쓸 수 있는 지표**. R군(오용 시나리오) 판정화에 그대로 이식 가능 |
| L-7 | Chen, Chen, Koudas, Yu. *Reliable Text-to-SQL with Adaptive Abstention*. **PACMMOD 3(1) (SIGMOD 2025)** | 초록 | 스키마 링크 단계에서 오류 가능성을 탐지해 **기권하거나 사용자에게 묻는다**. 분기점 예측에 LLM **은닉층 + conformal** 사용 | 원리(스키마 링크 = 되묻기 지점)는 유효. 구현은 **은닉층 접근 필요** → FabriX/Gemini API에서는 불가(채택 금지 목록 참조) |
| L-8 | Alrashed, Sukoon, Karger, Noy. *Developing and Benchmarking Verification Algorithms to Improve Text-to-SQL Generation*. **PVLDB 2026** | 초록 | 실행은 되지만 의도를 못 담은 SQL = **"deceptive failures"**. 검증을 독립 과제로 정식화 — Round-Trip Critique(SQL→자연어 역번역으로 의미 표류 탐지) · Synthetic Execution Consistency. 최신 생성기 오류의 **64%를 탐지**. 벤치마크 "생성기 실패"의 2/3 이상이 사실은 정답 라벨 오류. 검증 신호로 선택적 생성 → **침묵 실패를 명시적 기권으로 전환** | 이 저장소 문제("그럴듯한 오답")의 학계 명칭이 deceptive/silent failure다. 역번역 검증은 **해석 고지 문장 생성과 같은 산출물**을 쓴다(고지 문장을 원 질의와 대조) |
| L-9 | Chen, Chen, Sun, Su. *Error Detection for Text-to-SQL Semantic Parsing*. **Findings of EMNLP 2023** | 초록 | 파서는 **과신(over-confident)**한다. 파서 독립 오류 탐지기가 파서 자체 불확실성 지표보다 낫다 | 생성기 자기 확신(로그확률·자기평가)을 게이트로 쓰지 말 것 |
| L-10 | Zeng, Lin, Xiong, Socher, Lyu, King, Hoi. *Photon: A Robust Cross-Domain Text-to-SQL System*. **ACL 2020 System Demonstrations** | 초록 | 번역 불가 입력을 플래그하고 **혼동 구간(confusion span)을 짚어 재표현을 제안**하는 human-in-the-loop 교정기 | L-1과 같은 처방(구간 지목)의 초기 시스템 사례 |
| L-11 | Ganti, Orr, Wu. *Evaluating Text-to-SQL Model Failures on Real-World Data*. **ICDE 2024** (트랙 미확인) | 초록 | 고객 로그 정확도가 Spider보다 평균 30% 낮음. 실사용식 표현은 명확한 벤치마크식 표현보다 실행 정확도 **12.3점** 하락 | 골드셋의 "깔끔한 질문" 통과율은 운영 오답률을 과소평가한다 |
| L-12 | Wen, Yao, Feng, Xu et al. *Know Your Limits: A Survey of Abstention in LLMs*. **TACL** (arXiv 코멘트 기준, 권호 미확인) | 초록 | 기권을 질의·모델·인간 가치 3관점으로 정리한 서베이 | 기권 사유 분류의 틀. 설계 근거보다 용어 정리용 |
| L-13 | Saxer, Aigner, Linzmeier, Weiler, Stockinger. *Query Carefully: Detecting the Unanswerables in Text-to-SQL Tasks*. **HC@AIxIA + HYDRA 2025 워크숍** (CCIS 2026) | 초록 | llama3.3:70b + 명시적 No-Answer Rule + few-shot. 답 불가 탐지 0.8이지만 **없는 값 질의 0.5 · 컬럼 모호 0.3** | 프롬프트 규칙만으로는 **"없는 값"을 반타작**한다 → 값 존재는 코드가 조회해야 한다(워크숍급 근거) |
| L-14 | Ren, Hu, Lu, Huang, Duan. *LatentRefusal: Latent-Signal Refusal for Unanswerable Text-to-SQL Queries*. **Findings of ACL 2026** (OpenAlex DOI 기준, Anthology 직접 미확인) | 초록 | 출력 수준 지시 준수형 거부는 환각 때문에 **취약(brittle)**하다고 명시. 은닉 활성 프로빙으로 F1 88.5% | "프롬프트로 거부시키기"의 한계를 재확인. 은닉 활성 필요 → 채택 불가 |

### 축 2. 거짓 전제·존재하지 않는 엔터티

| L | 서지 | 확인 | 핵심 주장·수치 | 시사점 |
|---|---|---|---|---|
| L-15 | Kaplan. *Cooperative responses from a portable natural language query system* (CO-OP). **Artificial Intelligence 19(2):165–187, 1982** | 개념 | 자연어 DB 질의의 **전제 실패(presupposition failure)**를 탐지해 빈 답 대신 교정 응답을 낸다(예: "그 과목은 그 학기에 개설되지 않았다") | 이 문제는 NLIDB 초창기부터 정식화된 문제다. 기존 C9·C10(`EMPTY-MFS-01`)의 원류 |
| L-16 | Motro. *SEAVE: a mechanism for verifying user presuppositions in query systems*. **ACM TOIS, 1986** ★ | 초록 | 잘못된 전제에 기반한 질의는 null 답을 낸다. **이 "가짜 null(fake nulls)"은 오도적이며, 사용자의 잘못된 전제를 오히려 긍정하는 것으로 읽힐 수도 있다.** 전제를 추출해 ①실제 데이터 ②**무결성 제약** ③**완전성 단언** 3저장소에 대조 검증 → null 대신 정보성 메시지 | 실패 사례 "없는 대상 → 전체 반환"·"미래 기간·모순 조건 → 데이터 없음"을 **한 틀로 묶는 1차 근거**. 무결성 제약(기간 ≤ 현재, 사용률 0~100%) = 모순·미래 조건 검사, 완전성 단언 = "수집하지 않는 지표" 고지 |
| L-17 | Gaasterland, Godfrey, Minker. *An overview of cooperative answering*. **Journal of Intelligent Information Systems, 1992** | 개념 | 협조적 응답(오개념 교정·질의 완화·내포적 답 등) 개관 | L-15·L-16·`EMPTY-MFS-01`을 묶는 개관 문헌 |
| L-18 | Kim, Pavlick, Karagol Ayan, Ramachandran. *Which Linguist Invented the Lightbulb? Presupposition Verification for Question-Answering*. **ACL 2021** | 초록 | Natural Questions의 답 불가 질의 중 약 21%가 **검증 불가 전제**로 설명된다. 사용자 선호 연구에서 **전제 실패를 짚는 응답이 선호**됨. 병목은 **검증** 단계(함의 모델로도 부족) | 개방형 QA에서는 검증이 병목이지만, **우리는 닫힌 DB·레지스트리라 검증이 결정적 조회로 끝난다** — 이 이점을 반드시 써야 한다 |
| L-19 | Kim, Htut, Bowman, Petty. *(QA)²: Question Answering with Questionable Assumptions*. **ACL 2023** | 초록 | 의심스러운 가정을 담은 질의는 **일반 답과 다른 응답 전략**이 필요("퀴리가 우라늄을 언제 발견?"). 현 모델은 크게 부족 | "0건입니다"와 "그 전제가 틀렸습니다"는 다른 응답 유형이어야 한다 |
| L-20 | Yu, Min, Zettlemoyer, Hajishirzi. *CREPE: Open-Domain QA with False Presuppositions*. **ACL 2023** | 초록 | 실제 정보탐색 포럼 질문의 **25%**에 거짓 전제. 전제를 찾는 것은 그럭저럭 되지만 **사실 여부 판정에서 실패** | 거짓 전제는 드문 예외가 아니다(4건 중 1건). 판정은 근거 조회가 좌우 |
| L-21 | Hu, Luo, Wang, Cheng et al. *Won't Get Fooled Again: Answering Questions with False Premises* (FalseQA). **ACL 2023 main** | 초록 | 거짓 전제 질문 2,365건 + 반박 설명. 소량 미세조정으로 판별·반박 가능 | 반박 문구(무엇이 왜 틀렸는지 + 올바른 전제 질문)의 형식 참고 |
| L-22 | Sharma, Tong, Korbak, Duvenaud et al. *Towards Understanding Sycophancy in Language Models*. **ICLR 2024** | 초록 | RLHF 어시스턴트 5종 모두 **사용자 믿음에 맞추는(sycophancy)** 경향. 사람·선호모델 모두 설득력 있는 영합 응답을 정답보다 선호하는 경우가 적지 않음 | 사용자의 착각(단위 오기·없는 대상)을 **LLM이 교정해 주리라 기대하면 안 된다** — 전제 검사는 LLM 밖 코드에 둔다 |

### 축 3. 명확화 질문 발화 시점

| L | 서지 | 확인 | 핵심 주장·수치 | 시사점 |
|---|---|---|---|---|
| L-23 | Horvitz. *Principles of Mixed-Initiative User Interfaces*. **CHI 1999** | 개념 | 행동·대화(되묻기)·무행동을 **기대효용**으로 고른다. "핵심 불확실성만 대화로 해소", "잘못 짐작한 행동의 비용 최소화", "불확실성에 맞춰 서비스 정밀도를 조정" | **되묻기 = 비용 등급 결정**의 원 정식화. 틀렸을 때 비용이 큰 축만 묻는다 |
| L-24 | Amershi, Weld, Vorvoreanu et al. *Guidelines for Human-AI Interaction*. **CHI 2019** | 개념 | G2 "시스템이 얼마나 잘하는지 명확히" · **G10 "확신이 없으면 범위를 좁혀라 — 모호성 해소를 시도하거나 우아하게 강등"** · G11 "왜 그렇게 했는지 명확히" · G9 "효율적 교정 지원" | 가정 고지(G11)·범위 고지(G2)·되묻기 또는 강등(G10)의 HCI 정본 |
| L-25 | Zhang, Choi. *Clarify When Necessary: Resolving Ambiguity Through Interaction with LMs*. **Findings of NAACL 2025** | 초록 | 되묻기를 ①필요 여부 ②무엇을 물을지 ③답 반영 3과제로 분해. 사용자 의도 엔트로피 추정(intent-sim). **되묻기 예산 10%**에서 무작위 대비 이득 2배 | 되묻기는 **예산이 있는 희소 자원**으로 다룬다. 발동률 상한을 두는 기존 C5(습관화)와 합치 |
| L-26 | Zhang, Knox, Choi. *Modeling Future Conversation Turns to Teach LLMs to Ask Clarifying Questions*. **ICLR 2025** | 초록 | 기존 LLM은 모호 요청에 **한 해석을 전제하고 답해** 다른 의도의 사용자를 좌절시킨다. 되묻기 필요 판단 정확도 +3% | L-3과 같은 실패 양상의 독립 확인 |
| L-27 | Zhang, Qin, Deng, Huang et al. *CLAMBER: Identifying and Clarifying Ambiguous Information Needs in LLMs*. **ACL 2024** | 본문 | 3차원 8범주 분류 — 인식 불일치(**Unfamiliar**: 모르는 엔터티 · **Contradiction**: 모순) · 언어 모호(Lexical · Semantic) · 출력 불확정(Who · What · **When** · Where). 약 12K 데이터. CoT·few-shot도 **과신만 키우고 개선은 미미** | 우리 실패 사례의 대응: 없는 호스트=Unfamiliar · 모순 조건=Contradiction · 기간/식별자 누락=When/Who. **LLM 프롬프트 기법으로는 탐지가 안 늘어난다** |
| L-28 | Zou, Aliannejadi, Kanoulas. *Users Meet Clarifying Questions: Toward a Better Understanding of User Interactions for Search Clarification*. **ACM TOIS, 2022** | 초록 | 대규모 사용자 연구: 고품질 되묻기는 성과·만족 향상, **저·중품질 되묻기는 해롭다** — 그 경우 되묻지 않는 편이 낫다 | 기존 `CQ-COST-01`(C1~C3)과 같은 연구진의 별도 연구 — "되묻기는 품질 확신이 있을 때만"의 두 번째 1차 근거 |
| L-29 | Rao, Daumé III. *Learning to Ask Good Questions: Ranking Clarification Questions using Neural EVPI*. **ACL 2018** | 초록 | 좋은 질문 = **답이 유용할 기대값(EVPI)**이 큰 질문 | 되묻기 후보가 여럿이면 "답에 따라 SQL이 실제로 달라지는 축"을 고른다 |
| L-30 | Saparina, Lapata. *Disambiguate First, Parse Later*. **Findings of ACL 2025** | 초록 | 모호 질의는 SQL 전에 **자연어 해석 목록**을 먼저 만들고 매핑. LLM은 선호 해석에 강한 편향 | "해석 고지 문장"을 SQL 생성의 **입력 단계 산출물**로 두는 설계 근거 |
| L-31 | Tian, Zhang, Ning, Li et al. *Interactive Text-to-SQL Generation via Editable Step-by-Step Explanations*. **EMNLP 2023** | 초록 | SQL의 단계별 자연어 설명을 사용자가 직접 고치게 함. 24명 사용자 연구에서 우위 | 비전문 사용자에게 SQL 원문보다 **자연어 해석**이 검증 수단으로 낫다 |

기존 등재 교차 참조: `CQ-COST-01`(무조건 되묻기의 해악·세션 시간 약 2배, C1~C3) · `CQ-SEL-01`(되묻기 1회로 P@1 170% 이상 향상 — 좋은 질문의 상한) · `HABIT-01/02`(반복 경고 습관화, C5) · `CQ-RISK-01`(되묻기 vs 결과 제시 = 위험 통제, preprint, C8).

### 축 4. 결과 검증·정합성·범위 고지

| L | 서지 | 확인 | 핵심 주장·수치 | 시사점 |
|---|---|---|---|---|
| L-32 | Rigger, Su. *Finding Bugs in Database Systems via Query Partitioning* (TLP). **OOPSLA 2020 (PACMPL)** | 초록 | 행을 빠뜨리는 논리 버그는 **사용자가 알아채기 어렵다**. 술어 p에 대해 `p` · `NOT p` · `p IS NULL` 세 분할의 합이 원 질의와 같아야 한다(메타모픽 관계) | **"조건이 결과를 좁혔는가"**를 코드가 검사하는 이론적 뼈대: `count(Q ∧ p) < count(Q)`가 아니면 조건이 무력화된 것. NULL 분할(세 번째 항)을 잊으면 오판 |
| L-33 | Chapman, Jagadish. *Why Not?* **SIGMOD 2009** | 개념 | 기대한 튜플이 결과에 **왜 없는지**를 질의의 어느 연산이 걸렀는지로 설명 | 0건일 때 "어느 조건에서 끊겼나"(기존 C10 MFS/XSS)의 provenance 계보 |
| L-34 | Mottin, Marascu, Basu Roy, Das, Palpanas, Velegrakis. *A probabilistic optimization framework for the empty-answer problem*. **PVLDB 6(14), 2013** | 초록 | 빈 답 질의에 **조건을 덜어낸 대안 질의를 사용자에게 제안**(대화형 완화). "어떤 조건을 빼도 빈 답"인 경우도 판별 | 완화는 **제안**이지 **자동 적용**이 아니다 — 조건을 말없이 떨어뜨리는 것과 정반대 |
| L-35 | Motro. *Integrity = validity + completeness*. **ACM TODS, 1989** | 개념 | DB 무결성을 타당성과 **완전성**으로 나누고, 완전성 단언으로 답이 완전한지 판정 | "전체"라고 답하려면 **완전성 근거**가 필요하다 |
| L-36 | Razniewski, Nutt. *Completeness of Queries over Incomplete Databases*. **PVLDB 2011** | 개념 | 부분적으로만 완전한 DB에서 **완전성 진술(completeness statements)**로 질의 답의 완전성을 추론 | 존·DB·지표별 "수집 범위 표"를 두면 "이 답은 공동존만 포함" 같은 고지를 **결정적으로** 생성할 수 있다 |
| L-37 | Schelter, Lange, Schmidt, Celikel, Biessmann, Grafberger. *Automating Large-Scale Data Quality Verification* (Deequ). **PVLDB 11(12), 2018** | 개념 | 데이터 품질을 **선언적 제약(완전성·범위·유일성 등)**으로 명세하고 자동 검증 | 단위·범위 상식(메모리 하한, 사용률 0~100)을 **선언 표 + 코드 검사**로 두는 근거(간접 — 데이터 검증이지 사용자 입력 검증은 아님) |
| L-38 | Rashkin, Nikolaev, Lamm, Aroyo, Collins, Das, Petrov, Tomar, Turc, Reitter. *Measuring Attribution in Natural Language Generation Models* (AIS). **Computational Linguistics, 2023** | 개념 | 생성 문장이 **식별된 출처에 귀속 가능한가**를 평가하는 틀 | 자연어 응답의 수치·"전체"·"없음" 주장은 **실행 결과에 귀속 가능해야** 한다(응답 후검사의 기준) |
| L-39 | Song, Szafir. *Where's My Data? Evaluating Visualizations with Missing Data*. **IEEE TVCG 25(1), 2019** | 본문(초록부) | 결측 표현 방식이 지각된 데이터 품질·확신을 바꾸고 **해석을 편향**시킨다 | 절단·누락을 어떻게 보이느냐가 판단을 바꾼다(간접 근거 — 시각화 연구) |
| L-40 | Vasconcelos, Jörke, Grunde-McLaughlin, Gerstenberg, Bernstein, Krishna. *Explanations Can Reduce Overreliance on AI Systems During Decision-Making*. **CSCW 2023 (PACM HCI)** | 초록 | 5개 연구 N=731. 설명이 과의존을 줄이는 것은 **AI 답을 검증하는 비용을 실제로 낮출 때**뿐 | 고지는 **싸게 검증 가능한 1줄**(적용 조건·범위·절단)이어야 한다. SQL 원문 노출은 검증 비용이 커서 과의존 감소 효과가 약하다 |

기존 등재 교차 참조: `EMPTY-MFS-01`(Godfrey 1997 — MFS/XSS, C9·C10) · `EMPTY-RELAX-01`(Koudas et al. VLDB 2006) · `EMPTY-DIAG-01`(Fokou et al. KIS 2016).

### 축 5. 프롬프트 인젝션·사용자 SQL 입력

| L | 서지 | 확인 | 핵심 주장·수치 | 시사점 |
|---|---|---|---|---|
| L-41 | Pedro, Coimbra, Castro, Carreira, Santos. *Prompt-to-SQL Injections in LLM-Integrated Web Applications: Risks and Defenses*. **ICSE 2025** ★ | 본문 | 채팅창에 **SQL 원문("DROP TABLE users CASCADE")을 그대로 넣으면** 기본 LangChain 구성은 실행한다(Finding 1). **프롬프트 제한은 우회 입력의 부재를 보장할 수 없어 불충분**(Finding 2). DB 콘텐츠를 통한 간접 공격 가능(Finding 3). 방어 4종(질의 재작성 · SQL 검사 · 프롬프트 내 데이터 선적재 · LLM guard)을 **함께** 쓰면 식별된 공격을 모두 막았으나 보증 수준은 제각각(Finding 12). LLM guard는 정상 결과 150건에 오탐 0(Finding 11) | "붙여넣은 SQL 그대로 실행"은 알려진 공격 표면이다. 방어는 **프롬프트 문구가 아니라 코드 경로**(실행 경로 차단·파서 검사·권한)에 둔다 — 기존 D-003 3중 방어와 합치. 붙여넣은 SQL은 **의도 재구성 대상**이지 실행 대상이 아니다 |
| L-42 | Greshake, Abdelnabi, Mishra, Endres, Holz, Fritz. *Not what you've signed up for: Compromising Real-World LLM-Integrated Applications with Indirect Prompt Injection*. **AISec 2023** (ACM CCS 워크숍) | 초록 | LLM 통합 앱은 **데이터와 지시의 경계를 흐린다** — 검색된 데이터에 심은 프롬프트가 앱 기능을 조작 | 조회 결과의 자유 텍스트(알람 메시지·자산 설명 등)가 응답 생성 LLM에 들어가면 지시로 해석될 수 있다 → 결과 텍스트는 데이터로 격리 |
| L-43 | Zhang, Zhou, Hui, Liu, Li, Hu. *TrojanSQL: SQL Injection against Natural Language Interface to Database*. **EMNLP 2023 main** | 초록 | 텍스트→SQL 파서를 속여 **사용자 질의를 무력화(invalidate)하는 boolean 기반 주입**과 union 기반 주입. 성공률 최대 99%(미세조정) · 89%(LLM 프롬프팅) | **조건 무력화 검사(L-32)가 인젝션 탐지를 겸한다** — 항상 참 조건은 LLM 실수든 공격이든 같은 신호 |
| L-44 | Liu, Jia, Geng, Jia, Gong. *Formalizing and Benchmarking Prompt Injection Attacks and Defenses*. **USENIX Security 2024** | 개념(본문 미열람) | 프롬프트 인젝션 공격·방어의 형식화와 벤치마크 | 방어 평가 틀 참고용 |
| L-45 | Halfond, Viegas, Orso. *A Classification of SQL-Injection Attacks and Countermeasures*. ISSSE 2006(통용 서지 — OpenAlex 게재처 미기재) | 개념 | 고전 SQLi 분류 — **tautology(항상 참 조건) 공격** 포함 | L-43과 같은 "조건 무력화" 계보의 고전 |

---

## 2층 — 프리프린트 (L-46 … L-52) — 설계 근거로 단독 사용 금지

| L | 서지 | 확인 | 핵심 주장·수치 | 시사점 |
|---|---|---|---|---|
| L-46 | Zhang, Dong, Chang, Yu, Shi, Zhang. *Did You Ask a Good Question? A Cross-Domain Question Intention Classification Benchmark for Text-to-SQL* (TriageSQL). arXiv 2010.12634, 2020(게재처 미확인) | 본문 | 5분류 — Improper(DB와 무관 → 사용법 안내) · **ExtKnow(DB에 없는 정보 필요 → 무엇이 없는지 지목)** · Ambiguous(모호 구간 지목 후 되묻기) · Non-SQL(일반 SQL 문법 밖 → 한계 인정) · Answerable. **유형마다 시스템 행동이 다르다**. 39만 쌍, RoBERTa F1 60% | "수집하지 않는 지표" = ExtKnow. **유형 → 행동 대응표** 설계의 원형 |
| L-47 | Lee, Chay, Cho, Choi. *TrustSQL: Benchmarking Text-to-SQL Reliability with Penalty-Based Scoring*. arXiv 2403.15879 ("under review" · OpenReview 레코드 존재 · 판정 미확인) | 초록 | 신뢰성 = 가능한 질의엔 정확한 SQL, 불가능한 질의(스키마 불일치·SQL 밖 기능)엔 기권. 파이프라인형(생성기 + 불가능 질의 탐지기 + SQL 오류 탐지기) vs 통합형 | L-6의 RS(c)를 일반 도메인으로 확장. 우리 구조(생성 앞뒤 결정적 게이트)는 파이프라인형에 해당 |
| L-48 | Wang, Tatwawadi, Brockschmidt, Huang et al. *Robust Text-to-SQL Generation with Execution-Guided Decoding*. arXiv 1807.03100, 2018(게재처 미확인) | 본문 | **빈 출력을 오류로 취급**해 후보에서 제거 — 예: "술어 c = v를 냈는데 **상수 v가 컬럼 c에 없는** 경우". 전제는 "모든 질의는 결과가 있어야 한다" | ⚠ **반면교사**. 이 휴리스틱은 "없는 값" 조건을 **다른 값으로 바꾸거나 떨어뜨린 SQL**을 고르게 만든다 = 실패 사례 "없는 대상 버리고 전체 반환"의 기제 |
| L-49 | Kuhn, Gal, Farquhar. *CLAM: Selective Clarification for Ambiguous Questions with Generative Language Models*. arXiv 2212.07769, 2022 | 초록 | 현 LM은 모호 질의에 **거의 되묻지 않고 오답**을 낸다. 선택적 되묻기 프레임워크 | L-26·L-3과 같은 방향 |
| L-50 | Richardson. *What Predicts Correctness in Text-to-SQL? A Selective-Prediction Study*. arXiv 2607.06799, 2026 | 초록 | 블랙박스 신호(자기일관성·구조·실행 일관성·실행 가능성) AUROC **0.61~0.68**, 로그확률 0.67. LLM 판정자 0.72~0.78, **공급사가 다른 판정자 2개 앙상블 0.82**. 미세조정 검증기는 **보지 못한 스키마에서 약 0.66으로 하락** | 자기일관성 투표는 약한 신호다. 학습형 검증기는 스키마가 바뀌면 무너진다(라벨도 없음) |
| L-51 | Wu, Zhelun. *Never the Number: Structural Abstention for AI Systems Whose Answers Are Consumed as Fact*. arXiv 2608.13926, 2026(기술 보고서 · **수치 없음** 명시) | 초록 | 불변식: "지어낼 수 있는 구성요소는 **어떤 질문에 답할지**엔 영향을 줄 수 있어도 **어떤 값을 반환할지**엔 영향을 줄 수 없다". 값 계산 전 사용자 확인, 표현 불가 요청은 근사하지 말고 거절 | 이 저장소의 "코드가 SQL을 직접 조립, LLM 우회"(폼필 D-146/D-149) 방향과 같은 주장. 근거 강도 낮음 — 방향 확인용 |
| L-52 | Beurer-Kellner, Buesser, Creţu, Debenedetti et al. *Design Patterns for Securing LLM Agents against Prompt Injections*. arXiv 2506.08837, 2025 | 초록 | 증명 가능한 인젝션 저항성을 갖는 에이전트 설계 패턴 모음과 효용-보안 절충 | 사용자 입력이 도구 호출을 직접 좌우하지 못하게 하는 구조 참고 |

---

## 3층 — 산업 문서 (L-53 … L-58) — 2026-09-29 열람

| L | 제품·문서 | 확인한 문구(요지) | 시사점 |
|---|---|---|---|
| L-53 | **Snowflake Cortex Analyst** — REST API · 개요 · VQR 문서 | 모호 질의면 **SQL을 반환하지 않고** `suggestion` 콘텐츠로 대안 질문 제시("Your question is ambiguous, here are some alternatives:"). 응답에 `warnings`(요청에 대한 경고 목록) 필드. 응답 텍스트 예 "**We interpreted your question as ...**". `confidence.verified_query_used`로 검증 질의(VQR) 사용 여부 노출. "SQL로 해결 가능한 질문만 답한다" | 모호 → **차단 + 대안 제시**형. 해석 고지·경고 필드·검증 질의 사용 표시가 **API 계약**에 들어 있다 |
| L-54 | **Databricks AI/BI Genie** — best practices · talk-to-genie · concepts | 되묻기는 **작성자가 도메인 규칙으로 지정**("sales 성과 분해를 묻는데 기간·채널·KPI가 없으면 **먼저 되물어라**"). "답할 수 없으면 후속 되묻기를 할 수 있다". Analysis 섹션 "Understanding the question: Genie가 질문을 어떻게 해석했는지". 검증된 자산을 쓰면 **Trusted** 표시. Request review. "환각·오류가 있을 수 있으니 정확성을 확인하라" | 되묻기 트리거를 **LLM 판단이 아니라 필수 슬롯 규칙**으로 둔 사례 — 우리 결정적 게이트와 같은 구조 |
| L-55 | **Looker Conversational Analytics** (Gemini in Looker) | "How was this calculated?" → 사용한 원 필드명·계산·**적용 필터**·정렬 등 평문 설명. 유사 이름 필드가 여럿이면 되물을 수 있다. **질의당 최대 50,000행**(검색 스니펫엔 5,000 — 문서 개정으로 보임). 절단 시 사용자 고지 방식은 **문서에 없음**. "그럴듯하지만 사실과 다른 출력"을 경고 | 적용 필터 고지는 표준 기능. **절단 고지는 선두 제품 문서에도 미기재** — 공백 |
| L-56 | **Amazon Quick Sight(구 QuickSight) Q&A** — Generative BI 문서 | "**Interpreted as**: 단어를 데이터에 대응시킨 해석 — 제대로 이해했는지 확인하라". "**Did you mean**: 해석이 여럿이면 대안 답 목록"(예: "top customers" → 매출/이익/고객 수). 서술 문장에 마우스를 올리면 **근거 시각화 식별** | 모호 → **최선 해석으로 답 + 대안 병기**형(차단하지 않음). 문장-근거 연결은 L-38(AIS)의 제품화 |
| L-57 | **Microsoft Copilot for Power BI** — Ask Copilot questions about your data | 텍스트 요약이 **시각화를 만들거나 필터한 필드를 나열**, "How Copilot arrived at this"로 필드·측정값·**필터** 검증. 예측·이상탐지·요인분석은 미지원. ⚠ "의미 모델과 관련된 질문이면 모델로 답하고, **그렇지 않으면 LLM의 일반 지식으로 답할 수 있다**". ⚠ 보고서 페이지의 필터·슬라이서를 답에 **적용하지 않음**(한계로만 명시) | 필터 고지는 공통. 반면 **"데이터에 없으면 일반 지식으로 답"은 그럴듯한 오답의 전형** — 반면교사 |
| L-58 | **OWASP Top 10 for LLM Applications 2025** | LLM01 Prompt Injection · LLM05 Improper Output Handling · LLM06 Excessive Agency · LLM09 Misinformation | 사용자 SQL·조회 결과 텍스트 취급(01·05), 도구 권한(06), 그럴듯한 오답(09)이 업계 표준 위험 목록에 있다 |

---

## 4. 조사에서 확인한 공백 (동료심사 근거 없음 → 보수적 설계 근거)

| G | 공백 | 가장 가까운 간접 근거 | 보수적 결론 |
|---|---|---|---|
| G-1 | **사용자 입력 상수의 단위·크기 이상**("메모리 64MB 이상", "CPU 800%")을 탐지하는 text-to-SQL/NLIDB 연구 | L-16(무결성 제약 대조) · L-37(선언적 범위 제약) · L-1(값 없음 범주) | 명백한 범위 밖(도메인 범위표 기준)만 코드가 짚고, **차단보다 "해석 고지 + 진행"**. 임계를 추정해 교정하지 말 것 |
| G-2 | **결과 상한 절단 고지**의 효과·형식 | L-38 · L-39 · L-24 G2 / 산업 문서도 행 상한만 적고 고지 방식은 미기재(L-55) | 절단은 **항상 결정적으로 고지**(행 수 = LIMIT이면 "상위 N건만 표시, 전체 건수 미확인"). 효과 연구가 없으니 형식은 최소 1줄 |
| G-3 | **"조건이 결과를 실제로 좁혔는가"**를 런타임에 검사하는 NL 인터페이스 연구 | L-32(DBMS 테스트의 메타모픽 관계) · L-43·L-45(인젝션의 조건 무력화) | 근거가 다른 분야에서 차용된 것임을 명시. 비용(추가 COUNT 질의)을 계량해 켜기 |
| G-4 | **미래 기간·모순 조건**을 "데이터 없음"과 구분하는 text-to-SQL 연구 | L-16(무결성 제약) · L-27(Contradiction 범주 — 일반 LLM) | 시간 상한(now) 같은 **자명한 제약만** 코드로 검사 |
| G-5 | 다중 DB·존 **부분 조회 후 "전체"라고 답하는** 에이전트 응답 연구 | L-35·L-36(완전성 이론 — DB 이론 수준) | 조회한 대상 목록과 실패·미수집 대상을 응답에 **열거**. "전체" 어휘는 완전성 근거가 있을 때만 |
| G-6 | 붙여넣은 SQL에 대한 **의도 확인 UX**(실행 금지 이후 무엇을 할지) | L-41은 실행 경로 차단만 다룸 | 실행하지 않고 **자연어 의도로 재구성해 보여준 뒤 진행**. UX 효과 근거는 없음 |
| G-7 | **한국어**(단위 표기 "기가/메가", 존·지역 표면어) 모호성·거짓 전제 벤치마크 | 모든 벤치마크가 영어·중국어 | 한국어 표면어 사전은 레지스트리(결정적)로 유지 |
| G-8 | **인프라 관측 도메인**(메트릭·알람) 신뢰성 연구 | L-5·L-6(의료 EHR)이 유일한 도메인+신뢰성 선례 | EHRSQL 방식(답 불가 질의를 골드셋에 섞고 RS(c)로 채점)을 이식 |
| G-9 | 5개 산업 제품 문서 중 **"없는 필터 값"(존재하지 않는 호스트·존) 처리 방식을 적은 곳이 없다** | L-4(학계 범주는 있음) | 제품 관행에 기댈 수 없다 — 자체 결정적 검사 필요 |

---

## 5. 문헌 → 설계 원칙 (10개)

| # | 원칙 | 근거 L |
|---|---|---|
| P1 | **전제는 코드가 검증한다.** 질의에 나온 대상(존·호스트·DB·지표)의 존재를 조회 **전에** 레지스트리/사전/DB로 결정적으로 확인하고, 없으면 조건을 버리지 말고 "X는 등록돼 있지 않다 + 가까운 후보"로 답한다. 빈 결과는 전제를 긍정하는 "가짜 null"이 될 수 있다 | L-16 ★ · L-15 · L-18(검증이 병목 — 닫힌 DB에선 결정적) · L-1(값 없음 범주 · 설명 시 90% 자가 교정) · L-4(없는 필터 값) · L-13(프롬프트 규칙은 없는 값 0.5) · L-22(영합) |
| P2 | **빈 결과를 곧 오류로 보고 재생성시키지 않는다.** 0건은 ①진짜 0건 ②거짓 전제 ③생성 오류로 먼저 가르고, 재생성은 ③에만. 재생성이 조건을 바꾸거나 떨어뜨렸는지는 사후 대조 | L-48(빈 출력=오류 휴리스틱의 부작용) · L-16 · L-33 · L-34(완화는 제안이지 자동 적용 아님) · 기존 C9·C10 |
| P3 | **조건이 결과를 좁혔는지 코드가 검사한다.** ①사용자 조건 각각이 SQL에 반영됐는지(구문 대조) ②반영된 조건이 실제로 행을 줄였는지(메타모픽 — 조건 제거 COUNT와 비교, NULL 분할 주의). 조건 누락과 조건 무력화(항상 참)를 한 신호로 잡는다 | L-32 · L-43 · L-45 · L-8(deceptive failures) · L-11 |
| P4 | **되묻기는 비용 등급으로.** 차단형 되묻기는 "틀리면 비싸고 · 해석이 실제로 갈리며 · 결정적으로 탐지된" 경우에만. 나머지는 **최선 해석으로 진행 + 가정 고지 + 대안 병기**. 트리거는 필수 슬롯 규칙(결정적)이지 LLM의 "모호한가?" 자기판정이 아니다. 발동률을 관측 지표로 두고 상한 관리 | L-23 · L-24(G10) · L-25(예산 10%) · L-26 · L-27 · L-3(LLM 판정 80% 과잉) · L-28 · `CQ-COST-01`(C1~C3) · `HABIT-01/02`(C5) · L-54(규칙형 트리거) · L-56(대안 병기) · L-29(EVPI) |
| P5 | **가정·해석은 응답에 짧게 밝힌다.** "해석: 존=공동존(김포) · 기간=최근 1시간 · 지표=CPU 평균" 같은 1줄. SQL 원문 덤프가 아니라 검증 비용이 낮은 자연어. 해석 문장은 SQL 생성 전 산출물로 두어 역번역 검증에도 쓴다 | L-40 ★ · L-24(G11) · L-31 · L-30 · L-8(역번역) · L-1(구간 지목) · L-53·L-54·L-55·L-56·L-57(5개 제품 모두 해석/필터 고지 제공) |
| P6 | **범위·완전성·절단은 결과와 함께 고지한다.** 조회한 DB·존 목록, 실패·미수집 대상, LIMIT 절단 여부. "전체" 어휘는 완전성 근거가 있을 때만. 고지 문구는 결정적으로 생성 | L-35 · L-36 · L-38 · L-39 · L-24(G2) · G-2·G-5(공백 → 보수적) |
| P7 | **답할 수 없는 질의를 유형으로 가르고 유형별 행동을 고정한다.** 예: 범위 밖(사용법 안내) · 수집하지 않는 지표(무엇이 없는지 지목) · 없는 대상(전제 교정) · 모호(규칙형 되묻기 또는 병기) · SQL로 불가(한계 인정) | L-46(TriageSQL 유형→행동) · L-1 · L-4 · L-27 · L-12 · L-53("SQL로 해결 가능한 질문만") |
| P8 | **평가는 오답에 벌점을 준다.** R군(오용 시나리오)을 RS(c)로 채점 — 정답 +1 · 기권 0 · 오답 −c · 답 불가에 답함 −c · 올바른 기권 +1. c=10을 기본으로 "정답 10건 = 오답 1건" | L-6 ★ · L-5 · L-47 · L-8(선택적 생성) |
| P9 | **사용자가 붙여넣은 SQL과 조회 결과 텍스트는 명령이 아니라 데이터다.** 원문 SQL 실행 경로를 두지 않고(의도 재구성 후 고지), 방어는 프롬프트 문구가 아니라 파서·권한·실행 경로에 둔다 | L-41 ★(Finding 1·2·12) · L-42 · L-43 · L-58 · (저장소 D-003과 합치) |
| P10 | **검증 신호의 신뢰 순서는 결정적 검사 > 교차 LLM 판정 > 자기일관성·자기확신.** LLM 판정은 결정적 검사가 닿지 않는 의미 표류에만 보조로 | L-9(과신) · L-50(자기일관성 AUROC 0.61~0.68 · 교차 판정 0.82 — preprint) · L-8 · L-3 · L-14(출력 수준 거부는 취약) |

---

## 6. 채택하지 말아야 할 것

| # | 채택 금지 | 근거 |
|---|---|---|
| N1 | **빈 결과·데이터 부족을 곧바로 SQL 재생성 루프에 넣기**(실행 유도 디코딩의 "빈 출력 = 오류" 휴리스틱). 거짓 전제 질의에서 LLM이 조건을 바꾸거나 떨어뜨린 SQL로 "성공"하게 만든다 | L-48 · L-16 · L-34. **저장소 확인 권고**: `src/nodes/result_organizer.py:120` 부근의 "데이터 부족으로 재시도 요청" 경로가 재생성 시 원 조건 보존을 강제하는지(미확인 — 코드 분석 담당이 확인) |
| N2 | **LLM에게 "모호한가 / 답할 수 있는가"를 물어 되묻기·기권을 결정**하기 | L-3(약 80% 과잉 판정) · L-27(CoT·few-shot이 과신만 키움) · L-13(없는 값 0.5 · 컬럼 모호 0.3) · L-14(출력 수준 거부는 취약) |
| N3 | **은닉층·로짓 기반 기권**(conformal 분기점 예측, 은닉 활성 프로빙) | L-7 · L-14 — 운영 LLM(FabriX·Gemini API)은 은닉 활성에 접근할 수 없다 |
| N4 | **무조건 되묻기 / 모든 모호 축 되묻기** | `CQ-COST-01`(C1·C2 — 세션 시간 약 2배) · L-28(저·중품질 되묻기는 해롭다) · `HABIT-01/02`(습관화) · L-25(예산 10%에서 이득 최대) |
| N5 | **프롬프트 금지 문구로 SQL 실행·인젝션을 막는다고 간주** | L-41 Finding 2 · L-42 |
| N6 | **데이터에 없으면 LLM 일반 지식으로 답하기** | L-57(Power BI Copilot 폴백 — 그럴듯한 오답의 전형) · L-51 · 저장소 원칙 "침묵적 폴백 금지" |
| N7 | **학습형 검증기·학습형 되묻기 정책을 지금 도입** | L-50(보지 못한 스키마에서 0.66으로 하락 — preprint) · 라벨 부재 · 기존 C8 판단(`CQ-RISK-01` 미채택)과 같은 이유 |
| N8 | **SQL 원문 노출로 해석 고지를 갈음** | L-40(검증 비용을 못 낮추면 과의존 감소 없음) · L-31(자연어 설명이 검증 수단) |
| N9 | **모든 해석의 SQL을 전부 실행해 나열**을 기본값으로 | L-2·L-4는 해석 2개 수준에서만 다룸 · L-3 AllFound 1.9%(해석 열거 자체가 불완전) — 해석이 2개이고 비용이 낮을 때만 조건부(L-56 "Did you mean" 수준) |
