# 문헌 검색 로그 (Search Log)

> 목적: **검색을 재현·확장 가능하게** 남긴다. 무엇을 어떤 질의로 찾았는지, **무엇을 못 찾았는지**까지
> 기록한다. `plans/78` §11.4가 남긴 교훈("검색어 공백")과 같은 취지다 — 실패한 검색어는 다음 사람이
> 같은 벽에 부딪히지 않게 하는 자산이다.

## 세션 1 — 2026-08-28 · 「사용자에게 되묻는 행위」의 비용·효과

**동기**: `plans/82` §5(범위 사전 선택)를 뒷받침할 문헌이 저장소에 없었다.
기존 자산(`plans/78` §3.4·§11)은 *에이전트가 무엇을 할지*에 집중하며, 되묻기의 비용·효과는
2차 인용 한 줄(*안드로이드 권한 대화상자 — 주의 17% · 정확 이해 3%*)뿐이었다.

**도구**: `claude-scholar:openalex` 스킬 → `scripts/openalex.py`
**수용처**: `plans/82` §6(문헌 검토) · §16(참고문헌)

### 검색 질의와 결과

| # | 질의 (`title_and_abstract.search`) | 히트 | 성과 |
|---|---|---:|---|
| A | `asking clarifying questions information seeking conversations` | 237 | ✅ `CQ-SEL-01`(SIGIR 2019) |
| B | `mixed initiative conversational search` | 50 | ✅ `CQ-ANS-01`(ICTIR 2020) · `CQ-EVAL-01`(WSDM 2022) · `CQ-NEED-01`(ClariQ) |
| C | `identifying when to ask clarifying question risk` | 1039 | ❌ 의학·사회과학 노이즈만 — **일반 용어 조합은 무용** |
| D | `habituation security warnings attention` | 20 | ✅✅ `HABIT-01`(MISQ 2018) · `HABIT-02`(JMIS 2016) — **동료심사 상위 IS 저널** |
| E | `cost of interruption task resumption knowledge worker` | 1 | ❌ 사실상 무수확 |
| F | `progressive query processing partial results online aggregation` | 4 | △ `PROG-02`(Dagstuhl)만 |
| G | `when to ask clarification ambiguity detection conversational assistant` | 0 | ❌ **용어를 4개 이상 AND로 묶으면 0건** |
| H | `online aggregation interactive query processing early results` | 7 | ❌ 노이즈 |
| I | `faceted search interface query refinement exploratory` | 0 | ❌ AND 과다 |
| J | `large language model agents ask clarifying questions ambiguous user request` | 7 | △ *Learning to Clarify*(arXiv 2406.00222) 발견 — 미채택 |
| K | `online aggregation` (단독) | 9058 | ❌ 관련성 정렬 실패 — **인용수 정렬로는 주제어가 짧으면 무용** |
| L | `faceted search` (단독) | 10338 | ❌ 동일 |
| M | `clarifying question selection ranking search` | 25 | ❌ 노이즈 |
| N | `ambiguous queries clarification necessity prediction` | 0 | ❌ AND 과다 |
| O | **`clarifying questions benefit disturb users`** | 13 | ✅✅✅ **`CQ-COST-01`(IPM 2022) — 이 세션의 핵심 발견** |
| P | `selective prediction abstention deferral human expert` | 0 | ❌ AND 과다 |
| Q | `agent harness engineering LLM` | 309 | ❌ 교육·화학 등 무관 — **"harness"는 학술 색인에서 이 뜻으로 안 잡힌다** |
| R | `federated query orchestration heterogeneous data sources agent` | 2 | ❌ |
| S | `federated search resource selection distributed information retrieval` | 11 | △ 개관만 |
| T | **`selective search shard cutoff estimation efficiency`** | 4 | ✅✅ **`AUTONARROW-01`(CIKM 2012)** |

### DOI 직접 조회 (검색으로 찾은 뒤 서지 확정)

`batch-lookup works --id-field doi` 로 13건 일괄 확정. `PROG-01`(Online Aggregation, SIGMOD 1997)은
**검색으로는 안 잡혀 DOI를 직접 넣어 확보**했다(`10.1145/253260.253291` · 인용 928).

### 본문 확보

| 대상 | 경로 | 결과 |
|---|---|---|
| `CQ-COST-01` (IPM 2022) | DOI → Elsevier 유료 | ❌ 302 리다이렉트 · 본문 불가 |
| `CQ-COST-01` | **저자 소속 공개본** `irlab.science.uva.nl/.../zou2022asking.pdf` | ✅ PDF 확보 → `pypdf`로 본문 추출 → 인용문 4건 확정(`claims.md` C1·C2·C3) |
| `CQ-ANS-01` (ICTIR 2020) | OpenAlex `abstract_inverted_index` | ✅ 초록 복원(C7) |
| ETCLOVG 서베이 게재 상태 | `openreview.net/forum?id=eONq7FdiHa` | ❌ 봇 검증 페이지 — **재확인 실패**. `plans/78` 기재(TMLR under review) 승계 |

## 세션 2 — 2026-08-28 · 빈 결과(0건) 원인 진단

**동기**: 사용자 요구 — *"CPU 80% 넘는 게 없는지, 넘는 것 중 파일시스템 80% 넘는 게 없는지 사용자가
확인할 수 있어야 한다. 현재는 조회된 내용이 없다고만 나온다."* 이것이 학술적으로 이미 다뤄진 문제인지
확인이 필요했다. **결론: 30년 된 확립 영역이다**(cooperative answering / query relaxation).

**도구**: `claude-scholar:openalex` + WebSearch(개념 정의 대조)
**수용처**: `plans/82` §6(조건 퍼널 진단) · §7.4 · §17

| # | 질의 | 히트 | 성과 |
|---|---|---:|---|
| A | `why not questions query answers explanation` | 307 | ❌ 노이즈(교육·식물학) — "why-not"은 일반어 조합이라 안 잡힌다 |
| B | **`query relaxation empty answer database`** | 16 | ✅✅ `EMPTY-RELAX-01`(VLDB 2006) · `EMPTY-DUAL-01`(FSS 2008) · `EMPTY-DIAG-01`(KIS 2016) · `EMPTY-ML-01`(KDD 2004) |
| C | **`minimal failing subquery maximal succeeding`** | 11 | ✅✅✅ **`EMPTY-MFS-01`(Godfrey, IJCIS 1997 · 인용 114) — 이 영역의 정초** |
| D | WebSearch `"minimal failing subquery" MFS "maximal succeeding subquery" XSS` | — | ✅ MFS/XSS 정의 · **N회 질의 단순 알고리즘** · K 고정 시 다항/N차 NP-hard · 공개 구현체 `lias-laboratory/mfs4udb` 확인 |

**기각**: `EXS`(`10.1145/3289600.3290620` · 인용 115)는 검색에 걸렸으나 **DB 빈 답이 아니라 검색 랭킹
설명(WSDM 2019, Singh & Anand)** 이라 무관 — 제목만 보고 넣지 않았다.

**교훈 추가**: **도메인 전문 용어를 알면 히트율이 압도적으로 오른다.** 질의 A(일반어 "why not")는 307건
노이즈였고, 질의 C(전문어 "minimal failing subquery")는 11건 중 정초 논문이 1위였다. 개념을 모를 때는
**질의 B처럼 문제 상황을 서술**(`empty answer database`)해 진입하고, 거기서 얻은 전문 용어로 다시 검색한다.

## 세션 3 — 2026-08-28 · 급증(변화율) 판정의 기준선 설계 · **무수확**

**동기**: 사용자 요구 5 — *"1달 또는 1주일 전에 용량 대비 사용률이 많이 높아졌다면 급증"*. 설계 판단
(단일 직전 기간 대 분포 기준선)을 문헌으로 강화할 수 있는지 확인했다.

| # | 질의 | 히트 | 성과 |
|---|---|---:|---|
| A | `capacity trend threshold breach forecasting disk` | 733 | ❌ 최상위가 *Blockchain for AI* — 교훈 1(AND 과다) + 교훈 2(인용수 정렬) 동시 발현 |
| B | `change point detection baseline window` | 298,077 | ❌ AlphaFold·Faster R-CNN 등 무관 초대형 인용 논문만 — **교훈 2의 교과서적 사례** |

**결론(중요)**: **인용을 채택하지 않았다.** 이 판정은 **연구 문제가 아니라 스키마·의미론 문제**다 —
①"용량 대비 사용률"이 우리 DB에 이미 퍼센트로 저장돼 있는지 ②두 기간 비교가 우리 엔진에서 표현되는지
③파이프라인이 그것을 막고 있는지. 세 질문 모두 **저장소 실측으로 답한다**(`plans/82` §6.9). 반면 §6.3의
MFS/XSS는 *"어떤 알고리즘으로 끊긴 지점을 찾는가"* 라서 문헌이 실질을 바꿨다 — **차이는 "설계 선택지가
문헌에 이미 정식화돼 있는가"** 다.

**교훈 추가**: **문헌을 못 찾은 것과 문헌이 필요 없는 것을 구별한다.** 두 질의 실패 후 세 번째를 시도하지
않은 것은 포기가 아니라 판정이다 — 억지로 인용을 붙이면(예: 일반 이상탐지 서베이) 근거처럼 보이지만
실제 설계 결정(차분 대 비율 · 집계 축)에 아무 제약을 주지 못하는 **장식 인용**이 된다. `claims.md`의
등급 체계(A~D)가 이런 인용을 막기 위한 것이므로, **등급을 매길 수 없으면 넣지 않는다.**

## 검색 교훈 (다음 세션용)

1. **`title_and_abstract.search`에 4개 이상 단어를 AND로 넣으면 0건이 된다** (질의 G·I·N·P).
   3단어 이하 + 도메인 고유어 조합이 유효했다.
2. **일반 용어 단독 + 인용수 정렬은 무용하다** (K·L) — OpenAlex가 관련성으로 재정렬하지 않는다.
3. **논문 제목의 수사(rhetoric)를 그대로 검색하면 맞는다** — 최고 성과 질의 O(`benefit disturb users`)는
   논문 제목의 대조 표현을 그대로 쓴 것이다. 분야 사람이 제목에 쓸 만한 표현을 상상해 넣을 것.
4. **"harness"는 학술 색인에 이 의미로 없다**(Q). 하네스 문헌은 arXiv/OpenReview 직접 탐색이 필요하며,
   그것이 `plans/78`이 `article/refuser/`(615편, 본 저장소 밖)를 별도로 갖는 이유다.
5. **고전 논문은 검색보다 DOI 직접 조회가 빠르다**(`PROG-01`). 분야의 정본을 알고 있다면 바로 찍는다.
6. **유료 논문은 저자 소속 페이지를 먼저 본다** — 대학 IR 랩은 공개본을 올려 둔다(UvA IRLab 사례).
7. **문헌이 필요 없는 질문을 구별한다**(세션 3) — 저장소 실측으로 답할 수 있는 것(우리 스키마에 무엇이
   있는가·우리 파이프라인이 무엇을 막는가)에 문헌을 붙이면 장식 인용이 된다. **등급(A~D)을 매길 수
   없으면 넣지 않는다.** 판단 기준: *설계 선택지가 문헌에 이미 정식화돼 있는가*(MFS/XSS는 예, 급증
   기준선은 아니오).
8. **인용수는 레코드 단위 하한값**이다 — arXiv본·학회본 분산, 학회본 미색인이 흔하다
   (`plans/78` §11 서두 교훈과 동일. `CQ-RISK-01`이 인용 2로 잡히는 것도 preprint 단독 레코드 탓).

## 미탐색으로 남긴 영역 (다음 후보)

| 영역 | 왜 필요할 수 있는가 | 진입 단서 |
|---|---|---|
| 명료화 **답변 품질** 예측 | `answerable` 판정을 학습형으로 갈 경우 | Sekulić et al. "engagement level prediction"(2021) |
| 다중 소스 **순차 vs 병렬** 실행 비용 모델 | `plans/82` §11.2 지연 판단의 근거 강화 | 분산 질의 최적화(distributed query optimization) 계보 |
| **점진 결과 UI**의 사용자 효과 | 정정 ②의 효과 크기 정량화 | `PROG-02` Dagstuhl 18411의 참가자 목록 → 후속 논문 |
| 관측 데이터 **RBAC·소재 노출** | 탐색이 인가 밖 존의 존재를 노출하는 문제(§11.4) | inference attack / metadata leakage in federated search |
