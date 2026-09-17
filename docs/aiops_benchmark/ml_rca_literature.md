# ML 기반 장애 진단·근본원인분석(RCA) 문헌 심층 조사

> **보관 메모(2026-09-17)**: `plans/101`(ML 기반 장애 진단·예측·RCA) 작성용 조사 원본. 조사 에이전트가 작성했고, 계획서 §3·§14가 이 문서의 항목 ID(RCA-*·S-*)를 인용한다. 설계 해석의 정본은 계획서이며, 이 문서는 서지·수치·검증 등급의 근거다. 성능 수치는 원문 대조(V1)분만 적었다.

- 조사일: 2026-09-17
- 대상: 폐쇄망 금융권 인프라(폴스타 SMS · 제니퍼 APM · DPM/맥스게이지 · 보조 Prometheus), 분산 트레이스 없음, 확정 라벨 거의 없음
- 설계 전제: 결정은 결정적 수치/규칙, LLM은 해석·서술만. ML은 HolmesGPT에 정량 증거(순위·점수·인과 후보)를 공급하는 read-only 도구
- 기조사 제외(인용만): HolmesGPT, RCACopilot(EuroSys'24), Ahmed et al.(ICSE'23), Roy et al.(FSE'24 Industry), FLASH, Nissist, Huang et al. survey(arXiv 2406.11213), ReAct, StepFly, CORTEX, 에어갭 로그 우선화(EuroMLSys'26)

## 0. 검증 방법과 등급

| 등급 | 의미 | 이번 조사에서 쓴 경로 |
|---|---|---|
| **V1** | 원문(본문 PDF/HTML) 확인 | arXiv/출판사/저자 PDF를 내려받아 `pdftotext`로 추출 후 수치를 원문 표·문장에서 직접 대조(일부는 PDF 페이지 이미지 판독) |
| **V2** | 초록·출판사/메타데이터 확인 | Crossref DOI 조회, OpenAlex, Semantic Scholar batch API(초록), arXiv API, 학회 프로그램/출판사 페이지 |
| **V3** | 2차 자료만 | 블로그(The Morning Paper), 연구실 소개 페이지, 검색 스니펫 |

- 성능 수치는 원칙적으로 **V1 원문에서 확인한 것만** 적었다. V2 항목의 수치는 초록 문장 그대로 적고 "(초록)"으로 표시했고, V3 수치는 "(V3)"로 표시했다.
- OpenAlex 인용수는 영향력 판단에 쓰지 않았다(분야 특성상 레코드 단위 과소집계).
- 조사 중 **게재처 추정이 틀렸던 사례**(검증으로 바로잡음): CausIL은 KDD가 아니라 **WWW 2023**, PetShop 데이터셋은 NeurIPS가 아니라 **CLeaR 2024(PMLR v236)**, Salesforce ICA는 2024가 아니라 **ICSE-SEIP 2022**, Lumos는 LinkedIn·KDD'22가 아니라 **Microsoft·KDD 2020**. Budhathoki et al. ICML'22의 arXiv판(1912.02724)은 제목("Causal structure based…")과 저자 순서(Janzing 1저자)가 달라 **별도 버전**이다. ITBench는 arXiv v1 초록(94개 시나리오, SRE 13.8%)과 ICML 게재본을 요약한 검색 스니펫(102개, SRE 11.4%, V3)의 수치가 달라 **인용할 때 버전을 밝혀야 한다**.

### 핵심 발견 요약

1. **데이터에서 인과를 발견하는 RCA(PC/FCI/Granger/PCMCI + PageRank/랜덤워크)는 공개 벤치마크에서 무작위(Dummy) 수준**이다(RCA-B-01, V1). 합성 데이터 성능은 실환경 성능을 대표하지 못한다.
2. **단순 통계·규칙 기준선이 SOTA와 비슷하거나 낫다**: NSigma/BARO(0.01초/건), SimpleRCA, max-|Z|, 알람 건수(RCA-B-01·B-04·B-05, V1). 벤치마크가 너무 쉬우며, 여러 서브시스템을 합산한 리더보드는 시스템별 우열을 숨긴다.
3. 더 어려운 벤치마크(1,430건)에서는 **11개 SOTA의 Top@1이 평균 0.21, 최고 0.37**이고, 트레이스·로그 기반 딥러닝 계열(Nezha 0.04, Eadro 0.16)은 붕괴한다(RCA-B-04, V1).
4. **도메인 지식으로 만든 구조 그래프 + 회귀 가설검정(CIRCA)**은 **대형 은행 Oracle DB의 AAS(평균 활성 세션) 장애 99건**에서 AC@1 0.404, AC@5 0.763을 냈다. 그래프를 쓰지 않는 NSigma(0.323/0.662)보다 낫고, PC 기반 랜덤워크(0.086/0.449)보다 크게 낫다(RCA-A-05, V1). DPM 계층에 가장 가까운 실환경 증거다.
5. **LLM 단독 RCA는 낮다**: OpenRCA에서 최고 11.34%(Claude 3.5, 전용 에이전트), 요소 3개짜리 과제는 모든 모델이 0%다(G-01). ITBench SRE 진단에서 GPT-4o는 트레이스가 있을 때 13.81%, **없으면 9.52%**다(G-02). Flow-of-Action 실험에서 HolmesGPT(GPT-3.5-Turbo)는 11.11%였다(G-05, 2025년 초 버전 기준).
6. **"통계 도구 + LLM" 결합의 이득**은 preprint에서만 확인된다. BARO AC@1 14.44% → GALA 42.22%(G-06), 구조적 스켈레톤 + 멀티에이전트 KRCA 0.88 vs RCA-Agent 0.57(G-07). 동료심사를 거친 하이브리드는 **COLA(알람 집계, ICSE-SEIP'24)**가 사실상 유일하며, 구조는 "상관 마이닝 우선, 신뢰도 낮은 쌍만 LLM"이다(F-04).
7. **결정적 컨트롤러 + LLM 국소 추론(EoG)**은 ITBench에서 ReAct보다 Majority@k F1이 7배 높다(G-08, preprint). 결과 정답률과 증거 근거성 사이의 간극도 수치로 확인된다(Cloud-OpsBench: JRA 0.76인데 ECR 0.38, G-09).
8. **은행 실데이터·실배포 사례**가 있다: 알람 스톰 탐지·요약(China EverBright Bank, F1>0.9, 확인 대상 알람 98% 이상 감소, F-03), 다차원 원인 국소화 Squeeze(여러 은행 사례, E-03), CIRCA Oracle 은행 데이터(A-05), SCWarn(대형 상업은행 2년치 데이터에서 **인시던트의 약 50.4%가 나쁜 변경 기인**, S-F4).
9. **공백**: 시간/일 집계 해상도에서 RCA를 평가한 문헌이 없다(공개 벤치마크는 1초~5분 간격). WAS↔DB↔호스트를 가로지르는 트레이스 없는 RCA의 실환경 동료심사 검증, JVM GC·스레드풀 병목 RCA, 대기 이벤트 기반 ML RCA, 제니퍼/맥스게이지 데이터 기반 학술 문헌은 검색 범위 안에서 찾지 못했다.

---

## A. 트레이스 없는 메트릭 기반 RCA / 인과 추론

### RCA-A-01 · NetMedic
- **서지**: Kandula et al. · "Detailed Diagnosis in Enterprise Networks" · ACM SIGCOMM 2009 · DOI [10.1145/1592568.1592597](https://doi.org/10.1145/1592568.1592597) · 동료심사 O · **V1**(원문 1쪽 판독 + 본문 텍스트)
- **메커니즘**: 네트워크를 프로세스·설정 같은 세분화된 컴포넌트의 의존 그래프로 모델링한다. 각 컴포넌트는 여러 변수(자원 사용, 응답시간, 오류 코드 비율 등)로 표현한다. 간선 가중치는 **과거 이력에서 원천 컴포넌트가 지금과 비슷한 상태였던 구간에 대상 컴포넌트도 지금과 비슷했는지**로 추정한다(history-based primitive). 비슷한 이력이 없으면 기본 높은 가중치를 준다.
- **입력 요구**: OS·애플리케이션이 노출하는 성능 카운터(프로세스 단위), 컴포넌트 의존 템플릿(같은 머신, 통신 관계), 이력 구간. 트레이스·라벨 불필요.
- **보고 성능**: 실사용 데스크톱이 포함된 라이브 환경에 장애를 주입했을 때 "faulty component is correctly identified as the most likely culprit in **80%** of the cases and is almost always in the list of top five culprits"(초록 원문).
- **한계/비판**: 2009년 소규모 기업망이 대상이고, 주입 장애로만 평가했다. 이력 길이와 해상도 요구는 원문 숫자 추출에 실패해 미확인이다. 대규모 서버 팜 적용 사례는 확인하지 못했다.
- **코드**: 공개 코드 확인 못 함.
- **적합도**: 폴스타(프로세스 목록 + 호스트 메트릭 + 토폴로지) **적합**, 제니퍼/DPM **부분**. 트레이스 불필요, 라벨 불필요, 알고리즘이 단순해 폐쇄망·CPU 환경에서 성립한다. → **채택(원리 차용)**: "알려진 의존 관계 + 이력 유사도로 간선 영향을 추정"하는 방식은 인과 발견 없이 결정적으로 계산할 수 있는 2단계 그래프 가중 방식이다.

### RCA-A-02 · CloudRanger / MicroCause (PC 기반 인과 그래프 + 랜덤워크 계열)
- **서지**: (1) Wang et al. · "CloudRanger: Root Cause Identification for Cloud Native Systems" · CCGRID 2018, pp.492–502 · DOI [10.1109/CCGRID.2018.00076](https://doi.org/10.1109/CCGRID.2018.00076) · 동료심사 O · **V2**(Crossref). (2) Meng et al. · "Localizing Failure Root Causes in a Microservice through Causality Inference" · IWQoS 2020 · DOI [10.1109/IWQoS49365.2020.9213058](https://doi.org/10.1109/IWQoS49365.2020.9213058) · 동료심사 O · **V2**(Semantic Scholar 초록)
- **메커니즘**: 메트릭 시계열에서 PC 알고리즘(또는 변형)으로 인과 그래프를 만들고 랜덤워크로 근본원인을 순위화한다(RCA-B-01 원문의 계열 분류, V1). MicroCause는 순서 관계를 반영한 PCTS와 시간·우선순위를 통합한 TCORW 랜덤워크를 쓴다(초록).
- **입력 요구**: 서비스 내부 지표의 다변량 시계열, 장애 구간. 트레이스·라벨 불필요.
- **보고 성능**: MicroCause는 대형 온라인 쇼핑 서비스의 실제 장애 티켓 86건에서 AC@5 **98.7%**(최고 기준선보다 33.4% 높음, 초록).
- **한계/비판**: 독립 재평가(RCA-B-01, V1)에서 **PC/FCI/Granger/LiNGAM/fGES + PageRank/랜덤워크 계열과 MicroCause가 대부분 Dummy(무작위)와 비슷**했다. MicroCause는 일부 데이터셋(Table 5 Train Ticket 열)에서 건당 2시간 제한을 넘겨 결과가 누락됐다. RCAEval RE2-TT에서 MicroCause는 AC@1 0.1 / Avg@5 0.2였다(RCA-B-02, V1).
- **코드**: CloudRanger 공개 코드 확인 못 함. MicroCause는 RCAEval 프레임워크에 재구현 포함(MIT).
- **적합도**: 입력은 폴스타·제니퍼 메트릭과 맞지만, 원 논문 성능이 독립 재평가에서 재현되지 않았고 대규모에서 계산비가 폭증한다. → **회피**. 원 논문 수치와 독립 재평가의 괴리를 보여 주는 대표 사례로만 인용한다.

### RCA-A-03 · ε-Diagnosis
- **서지**: Shan et al. · "ε-Diagnosis: Unsupervised and Real-time Diagnosis of Small-window Long-tail Latency in Large-scale Microservice Platforms" · WWW 2019 · DOI [10.1145/3308558.3313653](https://doi.org/10.1145/3308558.3313653) · 동료심사 O · **V2**(ACM DL 페이지 확인, 초록은 검색 스니펫)
- **메커니즘**: 이상 구간과 정상 구간의 메트릭 분포를 **2표본 검정과 ε-통계량(시계열 유사도 척도)**으로 비교해, 소수 컨테이너에서 짧은 창(1분·1초)에 생기는 롱테일 지연의 원인 메트릭을 가려낸다. 그래프를 만들지 않는다.
- **입력 요구**: 컨테이너/호스트 메트릭(짧은 창 집계), 이상 시각. 라벨 불필요.
- **보고 성능**: 초록은 실제 웹 애플리케이션 데이터에서 실제 원인을 모두 찾고 후보 공간을 크게 줄였다고 주장한다(정량값 미확인). RCA-B-01(V1)에서는 장애 시각 지정 오차에 **강건한 편**(RCD·BARO와 같은 부류)으로 분류됐다.
- **한계/비판**: 소창(small-window) 전제라 시간 단위 집계에는 맞지 않는다. 독립 평가에서 BARO/NSigma보다 낫다는 증거는 없다.
- **코드**: RCAEval에 재구현 포함(MIT).
- **적합도**: 폴스타 원시 해상도가 분 단위라면 **부분 적합**, 시간 집계만 있다면 부적합. → **참고**: 1단계 통계 스코어러 후보군의 하나로 두되 BARO/NSigma 대비 이점은 사내에서 검증해야 한다.

### RCA-A-04 · MicroRCA
- **서지**: Wu et al. · "MicroRCA: Root Cause Localization of Performance Issues in Microservices" · IEEE/IFIP NOMS 2020 · DOI [10.1109/NOMS47738.2020.9110353](https://doi.org/10.1109/NOMS47738.2020.9110353) · 동료심사 O · **V2**(초록)
- **메커니즘**: 애플리케이션 성능 증상(응답시간)과 호스트·컨테이너 자원 사용을 상관시켜, **서비스와 머신을 함께 노드로 둔 속성 그래프**에서 이상 전파를 모델링하고 개인화 PageRank로 순위화한다. 애플리케이션 계측이 필요 없다(초록).
- **입력 요구**: 서비스↔서비스, 서비스↔호스트 연결 토폴로지, 서비스 응답시간, 자원 메트릭. 트레이스 불필요, 라벨 불필요.
- **보고 성능**: Kubernetes 벤치마크에 장애를 주입했을 때 precision **89%**, mean average precision **97%**(초록). 독립 재평가(RCA-B-04, V1, 새 벤치마크 1,430건)에서는 Top@1 **0.37**로 11개 방법 중 **최고**였지만 절대값은 낮다.
- **한계/비판**: 토폴로지 정확성에 의존하고, 원 평가는 소규모 주입 장애다.
- **코드**: 원저자 공개 코드 확인 못 함(RCA-B-04 프레임워크 재구현 존재).
- **적합도**: 폴스타 호스트 메트릭 + 제니퍼 응답시간 + 호스트↔WAS 인스턴스 매핑이 있으면 **적합**. 트레이스·라벨 부족 조건에서 성립한다. → **참고(2단계 후보)**: "호스트-서비스 이중 노드 그래프" 구조는 폴스타-제니퍼 결합에 그대로 대응한다. 다만 독립 평가 최고값도 0.37이라 단독 결정 근거로 쓰면 안 된다.

### RCA-A-05 · CIRCA (구조 그래프 + 회귀 기반 가설검정)
- **서지**: Li et al. · "Causal Inference-Based Root Cause Analysis for Online Service Systems with Intervention Recognition" · KDD 2022 · DOI [10.1145/3534678.3539041](https://doi.org/10.1145/3534678.3539041) · arXiv [2206.05871](https://arxiv.org/abs/2206.05871) · 동료심사 O · **V1**
- **메커니즘**: RCA를 **개입 인식(intervention recognition)**으로 정식화한다. 장애는 근본원인 변수의 부모 조건부 분포를 바꾸는 개입이다. 인과 그래프는 데이터에서 발견하지 않고 **시스템 아키텍처 지식과 인과 가정으로 만든 구조 그래프(Structural Graph)**를 쓴다. 각 변수를 부모로 회귀한 잔차의 이상도로 가설검정(RHT)하고, 하위 노드 편향을 보정(descendant adjustment)한다.
- **입력 요구**: 메트릭 시계열(1분 간격), 메타 메트릭 스켈레톤(수작업 매핑), 장애 탐지 시각. 라벨 불필요.
- **보고 성능(원문 Table 3, V1)**: **대형 은행 Oracle DB의 높은 AAS 장애 99건(D_O)**. DBA가 Oracle 공식 문서로 호출 그래프를 수작업 추출하고, 메트릭 197개를 메타 메트릭에 매핑해 간선 2,641개짜리 구조 그래프를 만들었다.
  - CIRCA(구조 그래프): AC@1 **0.404**, AC@5 **0.763**, Avg@5 0.603, 0.578초
  - NSigma(그래프 없음): 0.323 / 0.662 / 0.525
  - DFS-MH(구조 그래프): 0.268 / 0.439 / 0.372
  - RW(PCTS 인과 발견 그래프): 0.086 / 0.449 / 0.290, 약 24.7초
  - 사례 분석: DBA가 라벨링한 근본원인 "log file sync" 대기를 CIRCA는 찾았고 DFS 계열은 지나쳤다.
- **한계/비판**: (1) RCA-B-01(V1): CIRCA와 NSigma는 **장애 발생 시각 지정 오차에 민감**하다(특히 대형 시스템). (2) BARO 논문(V1): 호출 그래프 대신 PC로 만든 그래프를 넣으면 NSigma보다 못했고, Train Ticket에서는 PC 그래프 구성이 100건 중 15건만 성공했다. 즉 **그래프 품질에 크게 좌우**된다. (3) 스켈레톤 수작업 비용이 크다.
- **코드**: [NetManAIOps/CIRCA](https://github.com/NetManAIOps/CIRCA) · BSD-3-Clause(GitHub API 확인)
- **관련(보조 S-A1)**: CausIL(WWW'23)도 아키텍처 도메인 지식을 넣어 인스턴스 단위 인과 그래프를 추정한다.
- **적합도**: DPM(맥스게이지 AAS·대기 이벤트 지표)에 **직접 적합**하다. 은행 Oracle 사례가 우리 환경에 가장 가깝다. 폴스타·제니퍼 계층 간에도 "호스트 자원 → WAS 액티브 서비스·응답시간 → DB 세션" 스켈레톤을 수작업으로 정의할 수 있다. 트레이스·라벨 불필요, CPU 1초 미만. → **채택(2단계, DB 계층 우선)**. 조건: ① 스켈레톤을 DBA·운영자가 검토하는 정본 파일로 관리 ② 장애 시각 입력을 알람 시각 ±창 민감도로 함께 보고 ③ NSigma/BARO 기준선과 항상 병렬 산출.

### RCA-A-06 · RCD
- **서지**: Ikram et al. · "Root Cause Analysis of Failures in Microservices through Causal Discovery" · NeurIPS 2022, pp.31158–31170 · DOI [10.52202/068431-2259](https://doi.org/10.52202/068431-2259) · 동료심사 O · **V2**(초록 + Crossref)
- **메커니즘**: 장애를 근본원인에 대한 개입으로 보고, 전체 인과 그래프 대신 **장애 지표 F-node 주변만 국소·계층적으로 학습**(Ψ-PC 변형, 분할정복)해 조건부 독립 검정 수를 줄인다(초록).
- **입력 요구**: 정상/장애 구간 메트릭(이산화), 라벨 불필요.
- **보고 성능**: 합성 데이터와 Sock-shop에서 수정 PC 및 SOTA 대비 top-k recall 개선과 실행시간 단축(초록, 정량값 미확인).
- **한계/비판**: RCAEval RE2-TT(메트릭 전용)에서 AC@1 **0.09**, Avg@5 **0.13**(RCA-B-02, V1). BARO 논문에서 장애 주입 시각을 줄 때 지표 수준(fine-grained) Avg@5가 OB 0.21 / SS 0.09 / TT 0.02, 서비스 수준(coarse) Avg@5는 0.47 / 0.48 / 0.08이었다(V1). RCA-B-01에서는 RCD 합성 데이터에서는 잘하지만 CIRCA 합성 데이터에서는 못해 **데이터 생성기 의존성**이 지적됐다.
- **코드**: [azamikram/rcd](https://github.com/azamikram/rcd) · MIT
- **적합도**: 입력은 맞지만 독립 재평가 성능이 낮고 결과가 불안정하다. → **회피**(벤치마크 기준선으로만).

### RCA-A-07 · 인과 구조 기반 이상치 근본원인(Budhathoki et al.) + DoWhy-GCM
- **서지**: (1) Budhathoki, Minorics, Blöbaum, Janzing · "Causal structure-based root cause analysis of outliers" · ICML 2022, PMLR 162:2357–2369 · [PMLR](https://proceedings.mlr.press/v162/budhathoki22a.html) · 동료심사 O · **V2**(PMLR·ICML 포스터 페이지 확인, 본문 PDF 수신 실패). 선행 arXiv판 [1912.02724](https://arxiv.org/abs/1912.02724)은 제목·저자 순서가 다르다. (2) Blöbaum et al. · "DoWhy-GCM: An extension of DoWhy for causal inference in graphical causal models" · JMLR 25(147), 2024 · arXiv [2206.06821](https://arxiv.org/abs/2206.06821) · 동료심사 O · **V2**
- **메커니즘**: **인과 DAG와 함수적 인과 모델(FCM)이 주어졌다는 전제**에서, 목표 변수 이상치의 정보이론적(IT) 이상 점수를 각 상위 변수 노이즈 항의 기여로 분해한다. "노드 x가 정상적으로 행동했다면 이상이 여전히 관측됐을까?"라는 반사실 질문에 해당한다. DoWhy의 `gcm.attribute_anomalies`로 구현돼 있다(pywhy.org 문서, 산업 자료).
- **입력 요구**: 알려진 인과 그래프(비순환), 정상 구간 데이터로 적합한 메커니즘, 이상 표본. 라벨 불필요.
- **보고 성능**: 시뮬레이션과 하천 유량 극값 사례(PMLR 페이지 요약). IT 인프라 실데이터 수치는 확인하지 못했다.
- **한계/비판**: (1) 그래프가 틀리면 결과도 틀린다. (2) Nagalapatti et al.(보조 S-A4, ICLR'25, V1)은 **이상은 학습 분포 밖이라 적합된 SCM의 반사실 추정이 불안정**하다고 지적하고, 분포 안 개입 추정(IDI)을 제안했다(PetShop에서 비교). (3) 순환 의존(DB↔WAS 커넥션 풀 포화 같은 되먹임)은 DAG로 표현하기 어렵다.
- **코드**: [py-why/dowhy](https://github.com/py-why/dowhy) · MIT. IDI: [nlokeshiisc/IDI_release](https://github.com/nlokeshiisc/IDI_release) · Apache-2.0
- **적합도**: 도메인 DAG를 확정할 수 있는 좁은 영역(예: DB 내부 "대기 클래스 → AAS → 응답시간", 제니퍼 "GC 시간·스레드풀 대기 → 응답시간")에서 기여도 분해 점수를 HolmesGPT에 공급할 수 있다. MIT, CPU. → **참고(2단계 실험 후보)**: 그래프를 자동 발견하지 않는다는 조건에서만 쓰고, IDI 비판을 반영해 결과는 "기여도 추정치"로만 표기한다.

### RCA-A-08 · PCMCI
- **서지**: Runge et al. · "Detecting and quantifying causal associations in large nonlinear time series datasets" · Science Advances 2019 · DOI [10.1126/sciadv.aau4996](https://doi.org/10.1126/sciadv.aau4996) · 동료심사 O · **V2**(초록 + OpenAlex)
- **메커니즘**: PC 계열 조건부 독립 검정(선형/비선형)을 시계열 지연 구조에 맞춰 조합(PC 단계 + MCI 검정)해 고차원 시계열에서 인과 네트워크를 추정한다(초록).
- **입력 요구**: 충분히 긴 등간격 시계열. 라벨 불필요.
- **보고 성능**: 기후·심장 데이터와 합성 데이터에서 기존 기법보다 검출력이 높다(초록).
- **한계/비판(RCA 적용)**: RCA-B-01 Table 3(V1, 기본 설정 합성 데이터): **PCMCI F1 0.12 / F1-S 0.18 / SHD 32**(CIRCA10). 같은 조건에서 PC는 0.49 / 0.65 / 16이었고, 50노드에서는 PCMCI F1 0.04 / SHD 986이었다. 즉 **마이크로서비스 RCA용 그래프 구성에서 비교 대상 중 최저 수준**이다.
- **코드**: [jakobrunge/tigramite](https://github.com/jakobrunge/tigramite) · **GPL-3.0**(카피레프트 → 금융권 반입 시 법무 검토 필요)
- **적합도**: 자동 RCA 파이프라인에는 부적합하다. 시간 집계 데이터에서는 표본 수가 더 줄어든다. → **회피**(오프라인 탐색 분석용으로만 참고).

### RCA-A-09 · BARO
- **서지**: Pham, Ha, Zhang · "BARO: Robust Root Cause Analysis for Microservices via Multivariate Bayesian Online Change Point Detection" · FSE 2024(PACMSE 1(FSE), Art.98) · DOI [10.1145/3660805](https://doi.org/10.1145/3660805) · arXiv [2405.09330](https://arxiv.org/abs/2405.09330) · 동료심사 O · **V1**
- **메커니즘**: (1) 다변량 베이지안 온라인 변화점 탐지(BOCPD)로 이상 시작 시각을 추정한다. (2) **RobustScorer**: 이상 시각 이전 구간의 **중앙값과 IQR**로 각 메트릭을 척도화해 이후 최대 편차로 순위화한다. 평균·표준편차 대신 중앙값·IQR을 써서 이상 시각 오차와 이상치에 강건하다. 그래프를 만들지 않는다.
- **입력 요구**: 다변량 메트릭 시계열. 라벨·트레이스 불필요.
- **보고 성능(V1)**:
  - 원 논문 coarse RCA(Table 3, Avg@5 전체): Online Boutique **0.86** / Sock Shop **0.95** / Train Ticket **0.81**(CausalRCA 0.8 / 0.6 / 0.28). 같은 표에서 **단순 N-Sigma(주입 시각 제공)도 Train Ticket 0.77**로 BARO와 근접했다.
  - RCAEval RE2-TT(RCA-B-02 Table 6): AC@1 **0.67** / AC@3 0.82 / Avg@5 **0.80**. DISK 1.0 / 1.0 / 1.0이지만 DELAY 0.47 / 0.67 / 0.63, LOSS 0.53 / 0.6 / 0.64로 **네트워크 장애에 약함**
  - 실행시간 0.01초/건(RCA-B-01 Table 6)
- **한계/비판**: (1) **동일 저자군이 BARO·평가 연구(RCA-B-01)·벤치마크(RCA-B-02)를 함께 만들었다**(이해상충 가능성). (2) 제3자 재평가 RCA-B-04(V1): RCAEval에서 평균 Top@1 **0.24**로 규칙 기반 SimpleRCA(0.58)보다 낮고, 새 벤치마크에서는 Top@1 0.36이었다. (3) RCA-B-05(V1): RCAEval/Sock-Shop에서 Top-1 BARO 0.200 vs max-|Z| 0.544, Train-Ticket 0.160 vs 0.464로 **단순 z-점수보다 낮은 서브시스템이 있다**.
- **코드**: [phamquiluan/baro](https://github.com/phamquiluan/baro) · MIT. Zenodo 아티팩트 [10.5281/zenodo.11094092](https://doi.org/10.5281/zenodo.11094092)
- **적합도**: 폴스타·제니퍼·DPM 모든 메트릭에 그대로 적용할 수 있고, 트레이스·라벨 불필요, CPU 밀리초, MIT다. → **채택(1단계 통계 스코어러의 기준선)**. 단 "정답"이 아니라 **순위 후보 + 척도화 편차값**으로만 공급하고, max-|Z|·알람 건수 기준선과 함께 서브시스템(존·업무)별로 성적을 따로 관리한다.

### RCA-A-10 · Murphy
- **서지**: Harsh et al. · "Murphy: Performance Diagnosis of Distributed Cloud Applications" · ACM SIGCOMM 2023, pp.438–451 · DOI [10.1145/3603269.3604877](https://doi.org/10.1145/3603269.3604877) · 동료심사 O · **V2**(초록, 본문 PDF 수신 실패)
- **메커니즘**: 흔히 쓸 수 있는 모니터링 데이터에서 얻는 **느슨한 엔티티 연관(loosely-defined associations)**을 쓰고, 순환 의존을 허용하는 **마르코프 랜덤 필드(MRF)**로 특정 인시던트 맥락에서 엔티티 간 영향을 추론한다. 인과 DAG(비순환)를 요구하는 기존 방법의 한계를 겨냥한다(초록).
- **입력 요구**: 일반 모니터링 텔레메트리와 엔티티 연관(초록 기준). 트레이스 요구 여부는 본문 미확인.
- **보고 성능**: 에뮬레이션 마이크로서비스 환경과 **대기업 실제 인시던트**에서 진단 오류를 기존 방법이 지원하는 제한 환경 대비 약 **1.35배**, 일반 환경 대비 **4.7배 이상** 줄였다(초록).
- **한계/비판**: 본문을 확인하지 못해 학습 데이터 요구량, 추론 비용, 실인시던트 건수는 미확인이다.
- **코드/데이터**: [netarch/Murphy-traces](https://github.com/netarch/Murphy-traces)(DeathStarBench 텔레메트리 데이터) · MIT. 알고리즘 코드는 미확인.
- **적합도**: 금융권 3-tier는 WAS 커넥션 풀 ↔ DB 세션 같은 **되먹임(순환) 의존**이 흔해 DAG 전제 방법보다 개념적으로 맞다. → **참고**: 원문을 확인한 뒤 2단계 후보로 재평가한다.

### RCA-A-11 · TORAI
- **서지**: Pham et al. · "TORAI: Multi-source Root Cause Analysis for Blind Spots in Microservice Service Call Graph" · arXiv [2604.13522](https://arxiv.org/abs/2604.13522)(2026-04) · arXiv 코멘트상 **FSE 2026 Research Track 채택**(출판본 DOI 미확인) · **V1**(arXiv 원문)
- **메커니즘**: 호출 그래프를 만들지 않는다. (1) 사용 가능한 멀티소스 텔레메트리로 서비스별 이상 심각도를 측정하고, (2) 심각도 증상으로 서비스를 군집화(GMM)하고, (3) 군집 내에서 인과 분석으로 순위화한 뒤, (4) 군집 순위를 합치고 가설검정으로 세부 원인을 식별한다. **트레이스가 없는 블랙박스 서비스(blind spot)**를 겨냥한다.
- **입력 요구**: 메트릭(필수), 로그·트레이스(있으면 활용). 라벨 불필요.
- **보고 성능(V1)**: Online Boutique 평균 T1 / T3 / A5 = **0.83 / 0.95 / 0.93**(메트릭 기반 CausalRCA 0.21 / 0.68 / 0.6), Sock Shop 0.84 / 0.96 / 0.94, Train Ticket A5 0.89. 자체 구축 데이터(40–50 rps, Kubernetes 5노드)다.
- **한계/비판**: 공개 벤치마크 계열에서만 평가했고, BARO·RCAEval과 같은 저자군이다. 실환경 검증은 없다.
- **코드**: 원문에 RCAEval 저장소 링크만 확인.
- **적합도**: "일부 계층만 관측되는" 우리 환경(트레이스 없음, 제니퍼는 WAS만 관측)과 문제 설정이 일치한다. → **참고(유망, 사내 재현 필수)**.

### RCA-A-12 · PRISM (Graph-Free RCA)
- **서지**: Pham · "Graph-Free Root Cause Analysis" · arXiv [2601.21359](https://arxiv.org/abs/2601.21359)(2026-01) · 동료심사 X(preprint, 단독 저자) · **V1**
- **메커니즘**: 컴포넌트 속성을 **내부 속성(CPU·메모리·디스크 등)**과 **외부 속성(응답시간·오류율 등)**으로 나눈다. 장애는 내부 속성에서 시작해 외부 속성으로만 전파된다는 공리 아래, 가장 큰 이상 점수가 아니라 내부-외부 분해 점수로 순위화한다. 이 공리가 성립하는 부품 기반 시스템 부류에서 정확 순위를 이론적으로 보장한다고 주장한다.
- **입력 요구**: 컴포넌트별 내부·외부 메트릭 분류. 의존 그래프·라벨 불필요.
- **보고 성능(V1)**: RCAEval 9개 데이터셋 735건에서 Top-1 **68%**(차순위 BARO 19%), Top-3 91%, Avg@5 87%, **8ms/건**. 본문 서술상 구조 지식을 쓰는 LLM 에이전트 RCLAgent는 전체 Top-1 10%였다.
- **한계/비판**: 단독 저자 preprint이고 벤치마크 제작자 본인의 평가다. 공리(내부 기원·외부 전파)가 깨지는 장애(외부 부하 급증, DB 락 대기처럼 "외부" 요인이 원인인 경우)는 다루기 어렵다. 비교 기준선을 돌리려고 추가 계측을 붙였고 지연 오버헤드가 컸다고 저자가 밝혔다.
- **코드**: 공개 코드 확인 못 함.
- **적합도**: 내부 속성 = 폴스타 호스트 자원, 외부 속성 = 제니퍼 응답시간·에러율로 **자연스럽게 대응**한다. CPU·라벨 불필요. → **참고(1~2단계 실험 후보)**: 공리 위배 유형을 결정적 규칙으로 먼저 걸러낸 뒤 적용한다.

---

## B. RCA 벤치마크 · 재현 비판

### RCA-B-01 · 인과 추론 RCA, 어디까지 왔나 (How Far Are We?)
- **서지**: Pham, Ha, Zhang · "Root Cause Analysis for Microservices based on Causal Inference: How Far Are We?"(Crossref 등재 제목은 "…for Microservice System based on…") · ASE 2024, pp.706–715 · DOI [10.1145/3691620.3695065](https://doi.org/10.1145/3691620.3695065) · arXiv [2408.13729](https://arxiv.org/abs/2408.13729) · 동료심사 O · **V1**
- **설계**: 인과 발견 9종 + RCA 21종. 합성 데이터 6종(CIRCA·RCD·CausIL 생성기, 10–50노드)과 벤치마크 시스템 3종(Online Boutique, Sock Shop 2종, Train Ticket)에 장애 5종(CPU, MEM, DISK, DELAY, LOSS)을 넣었다. **Dummy(무작위) 기준선을 처음 도입**했다. 8 CPU / 16GB 서버.
- **핵심 결과(V1)**:
  - "PC / FCI / Granger / LiNGAM / fGES / NTLR-PageRank/random walk, CausalAI, RUN, and MicroCause **mostly perform similarly to Dummy**"
  - CausalRCA, RCD, CIRCA, NSigma, BARO가 상대적으로 낫지만 효율이나 파라미터 민감도 문제가 있다.
  - CIRCA·NSigma는 **장애 발생 시각 지정에 민감**하고, RCD·ε-Diagnosis·BARO는 강건하다.
  - **합성 데이터 성능은 실시스템 성능을 반영하지 못한다.**
  - 인과 발견 실행시간은 10→50노드에서 7배~수천 배로 늘고, PC+KCI는 10노드에서도 건당 평균 1시간을 넘는다. NSigma·BARO는 0.01초/건.
  - Train Ticket BARO(t_Δ=0) Avg@5: CPU 0.90, MEM 0.96, DISK 0.84, DELAY 0.77, LOSS 0.66
- **한계/비판**: 마이크로서비스 벤치마크에 한정되고 실운영 데이터가 없다. BARO 저자가 수행한 평가다.
- **코드**: RCAEval 저장소로 통합 · MIT
- **적합도**: → **채택(평가 프로토콜 근거)**: 사내 평가에 Dummy·NSigma 기준선, 장애 시각 오차 민감도, 실행시간을 필수 항목으로 넣는다. 인과 발견 계열을 회피하는 1차 근거다.

### RCA-B-02 · RCAEval
- **서지**: Pham et al. · "RCAEval: A Benchmark for Root Cause Analysis of Microservice Systems with Telemetry Data" · WWW 2025 Companion, pp.777–780 · DOI [10.1145/3701716.3715290](https://doi.org/10.1145/3701716.3715290) · arXiv [2412.17015](https://arxiv.org/abs/2412.17015) · 동료심사 O(4쪽 동반 논문) · **V1**
- **구성(V1)**: 장애 사례 735건, 장애 유형 11종, 시스템 3종.
  - RE1: 375건, 메트릭 전용, 장애 5종
  - RE2: 270건, 메트릭·로그·트레이스, 장애 6종(CPU·MEM·DISK·SOCKET·DELAY·LOSS)
  - RE3: 90건, 코드 수준 장애 5종
  - 기준선 15종(메트릭 9, 트레이스 2, 멀티소스 4). 부하 10–200 rps
- **결과(Table 6, RE2-TT, AC@1 / AC@3 / Avg@5 평균, V1)**:
  - 메트릭: BARO **0.67 / 0.82 / 0.80**, CIRCA 0.32 / 0.47 / 0.46, CausalRCA 0.22 / 0.47 / 0.43, MicroCause 0.1 / 0.22 / 0.2, RCD 0.09 / 0.13 / 0.13
  - 트레이스: TraceRCA 0.66 / 0.79 / 0.77, MicroRank 0.16 / 0.37 / 0.31
  - 멀티소스: BARO 0.69 / 0.82 / 0.81, PDiagnose 0.48 / 0.7 / 0.67, RCD 0.1 / 0.64 / 0.54, CIRCA 0.06 / 0.11 / 0.13
  - **주의**: 본문 5절은 "CIRCA and RCD obtain the best average Avg@5 score of 0.46 and 0.54"라고 적어 **표(BARO 0.80)와 서술이 불일치**한다. 인용할 때는 표 수치를 쓴다.
- **한계/비판**: 마이크로서비스 3종에 메트릭 샘플링 **1초**(RCA-B-04 Table 확인)라 우리 환경(시간·일 집계)을 대표하지 못한다. RCA-B-04는 이 벤치마크가 규칙 기반으로도 풀릴 만큼 쉽다고 비판했다.
- **코드·라이선스**: [phamquiluan/RCAEval](https://github.com/phamquiluan/RCAEval) · MIT. 논문 CC BY 4.0
- **적합도**: → **채택(오프라인 회귀 테스트 도구)**: 사내 구현한 스코어러의 구현 정확성 점검용으로 쓰고, 성능 기대치의 근거로는 쓰지 않는다.

### RCA-B-03 · PetShop 데이터셋
- **서지**: Hardt et al. · "The PetShop Dataset — Finding Causes of Performance Issues across Microservices" · CLeaR 2024, PMLR v236 · [PMLR PDF](https://proceedings.mlr.press/v236/hardt24a/hardt24a.pdf) · arXiv [2311.04806](https://arxiv.org/abs/2311.04806) · 동료심사 O · **V2**(PMLR 링크·Amazon Science 페이지·ML Anthology 확인)
- **구성**: AWS에 구성한 반려동물 입양 애플리케이션(DB·로드밸런서·큐·스토리지·컨테이너 마이크로서비스 등 41개 컴포넌트). 성능 문제 68건 주입. 지연·요청·가용성 메트릭을 **5분 간격**으로 수집. 인과/비인과 RCA를 모두 평가하도록 설계했다.
- **보고 성능**: RCA-B-05(V1) 재분석에서 PetShop 서브시스템별 BARO Top-1 0.154–0.250, max-|Z| 0.077–0.375로 전반적으로 낮다.
- **한계/비판**: 사례 수가 작고 서브시스템당 8–26건이라 분산이 크다. 저장소가 **archived** 상태다.
- **코드**: [amazon-science/petshop-root-cause-analysis](https://github.com/amazon-science/petshop-root-cause-analysis) · Apache-2.0 · archived
- **적합도**: 공개 RCA 데이터 중 **해상도(5분)가 우리 집계 데이터에 가장 가깝다**. → **참고(저해상도 메트릭 RCA 검증용 공개 데이터)**.

### RCA-B-04 · 장애 전파를 고려한 벤치마크로 본 마이크로서비스 RCA 재평가
- **서지**: Fang et al. · "Rethinking the Evaluation of Microservice RCA with a Fault Propagation-Aware Benchmark" · Proc. ACM Softw. Eng.(FSE 2026) · DOI [10.1145/3797100](https://doi.org/10.1145/3797100) · arXiv [2510.04711](https://arxiv.org/abs/2510.04711) · 동료심사 O · **V1**(arXiv v2) + V2(ACM DOI, Semantic Scholar)
- **핵심 결과(V1)**:
  - 예비 연구: **단순 규칙 기반 SimpleRCA가 공개 벤치마크 4종에서 SOTA와 비슷하거나 앞섰다.** RCAEval에서 BARO 평균 Top@1 0.24 vs SimpleRCA 0.58. 저자들은 SimpleRCA를 실용 해법이 아니라 벤치마크 난이도 탐침으로 규정했다.
  - 기존 데이터셋 샘플링 간격: RE2/RE3·Eadro **1초**, AIOps-2021·GAIA 30초, Nezha **60초**
  - 새 벤치마크: 장애 주입 9,152건 중 SLI 영향이 검증된 **1,430건**, 장애 유형 25종(6범주), 서비스→코드 수준의 계층 라벨
  - 11개 SOTA 평균 Top@1 **0.21**, 최고 **0.37**(MicroRCA). BARO 0.36, SimpleRCA 0.28, MicroDig 0.35, MicroHECL 0.34, CausalRCA 0.22(927초/건), Shapleyiq 0.22, Eadro 0.16, DiagFusion 0.13, MicroRank 0.04, **Nezha 0.04**, Art 0.04
  - 실패 패턴 3가지: 확장성(초→시간), **관측 사각지대**, 모델링 병목
- **한계/비판**: 여전히 마이크로서비스 벤치마크 시스템 기반이다.
- **코드**: 논문에 벤치마크 생성 프레임워크·데이터 공개 명시(저장소 URL은 본 조사에서 미확인).
- **적합도**: → **채택(보수적 설계 근거)**: 딥러닝 멀티모달 RCA를 회피하고 단순 통계 기준선을 필수로 두는 근거다. "관측 사각지대" 실패 패턴은 트레이스가 없는 우리 환경의 위험 요인과 같다.

### RCA-B-05 · 합산 리더보드는 시스템별 승자를 숨긴다 (보고 프로토콜 감사)
- **서지**: Hu et al. · "Pooled Leaderboards Hide System-Specific Winners: A Reporting-Protocol Audit of Offline Root-Cause Analysis Benchmarks" · arXiv [2606.29159](https://arxiv.org/abs/2606.29159)(2026-06) · 동료심사 X · **V1**
- **설계**: OpenRCA·RCAEval·PetShop의 서브시스템 11개, 대응 채점 단위 778건. BARO, CD-1min(1분 재표본 z-점수 + 변화율 결정적 규칙), **max-|Z|**, **알람 건수(alert-count)**를 비교했다. 랜덤효과 메타분석과 leave-one-system-out을 적용했다.
- **핵심 결과(V1)**:
  - 6개 쌍별 비교 모두에서 서브시스템 효과의 부호가 양쪽으로 갈렸고, 95% 예측구간이 모두 0을 가로질렀다.
  - leave-one-system-out에서 합산 1위 방법을 고르면 11개 중 최대 5개 서브시스템에서 더 낮은 방법을 고르게 되고, 후회(regret)가 최대 **24.8%p**(RCAEval/Sock-Shop)였다.
  - 예(Top-1): OpenRCA/Bank BARO 0.160 · max-|Z| 0.127 · 알람 건수 0.132. RCAEval/Sock-Shop 0.200 · **0.544** · 0.448. RCAEval/Train-Ticket 0.160 · **0.464** · 0.184
- **한계/비판**: 벤치마크 계열이 3개뿐이라 독립 단위가 사실상 3이라고 저자가 인정했다. preprint다.
- **코드**: 320줄 감사 모듈 공개 명시(URL 미확인).
- **적합도**: → **채택(평가 방법)**: 사내 평가를 존(은행존/공동존)·업무·계층별로 분리 보고하고, max-|Z|·알람 건수 같은 스키마 무관 기준선을 항상 포함한다. 알람 건수 기준선은 `noise_gate`가 이미 갖고 있는 신호라 비용이 거의 없다.

---

## C. 멀티모달 / 계층 교차 RCA

### RCA-C-01 · Groot (이벤트 그래프, eBay)
- **서지**: Wang et al. · "Groot: An Event-graph-based Approach for Root Cause Analysis in Industrial Settings" · ASE 2021, pp.419–429 · DOI [10.1109/ASE51524.2021.9678708](https://doi.org/10.1109/ASE51524.2021.9678708) · arXiv [2108.00344](https://arxiv.org/abs/2108.00344) · 동료심사 O(산업 사례) · **V1**
- **메커니즘**: 메트릭·로그·**활동(배포·설정 변경)**을 요약한 **이벤트**로 실시간 인과 그래프를 만들고, SRE가 정의한 **사용자 이벤트와 도메인 규칙**으로 확장한 뒤 이벤트 그래프에서 근본원인을 순위화한다. 서비스 의존 관계를 사용한다.
- **입력 요구**: 이벤트화한 이상 신호, 변경 이력, 서비스 의존, SRE 규칙. 학습 라벨 불필요(평가용 라벨만).
- **보고 성능(V1)**: 운영 서비스 5,000개. 15개월 동안 모은 **실제 운영 인시던트 952건**에서 top-3 **95%**, top-1 **78%**.
- **한계/비판**: 규칙 작성·유지 비용이 든다. eBay 내부 데이터라 재현할 수 없다.
- **코드**: 공개 코드 확인 못 함.
- **적합도**: 폴스타 알람 이력(발생·해제·심각도·정의) + 변경 이력 + 토폴로지 + 운영자 규칙이라는 **우리 자산 구성과 거의 같다**. 트레이스·라벨 불필요. → **채택(아키텍처 원형)**: "이상을 이벤트로 정규화 → 규칙·의존으로 간선 → 그래프 순위"를 1~2단계의 기본 틀로 쓴다. 규칙은 코드가 아니라 정본 설정 파일로 관리한다.

### RCA-C-02 · Chain-of-Event (해석 가능한 가중 이벤트 인과 그래프 학습)
- **서지**: Yao et al. · "Chain-of-Event: Interpretable Root Cause Analysis for Microservices through Automatically Learning Weighted Event Causal Graph" · FSE 2024 Companion(Industry) · DOI [10.1145/3663529.3663827](https://doi.org/10.1145/3663529.3663827) · 동료심사 O(산업 트랙) · **V2**(초록 + FSE 트랙 페이지)
- **메커니즘**: 멀티모달 관측을 이벤트로 바꾸고, **과거 인시던트로 이벤트 인과 그래프의 가중치를 자동 학습**한다. 파라미터가 SRE의 운영 경험과 맞도록 설계돼 전문가가 해석하고 직접 개입할 수 있다(초록).
- **입력 요구**: 이벤트화한 관측, 근본원인이 표시된 과거 인시던트(학습용).
- **보고 성능**: 서비스 5,000개 이상 전자상거래 시스템의 데이터셋 2종. Service 데이터셋 top-1 **79.30%**·top-3 98.8%, Business 데이터셋 top-1 85.3%·top-3 96.6%(초록).
- **한계/비판**: 라벨이 붙은 과거 인시던트가 필요하고, 산업 데이터라 재현할 수 없다.
- **코드**: [NetManAIOps/Chain-of-Event](https://github.com/NetManAIOps/Chain-of-Event) · **라이선스 표기 없음**(GitHub API) → 반입 전 확인 필요
- **적합도**: 가중치를 사람이 읽을 수 있어 "결정은 수치" 원칙과 맞는다. 초기에는 운영자가 가중치를 수동으로 넣고, 확정 원인이 쌓이면 학습하는 경로가 가능하다. → **참고(라벨 축적 후 4단계)**.

### RCA-C-03 · DejaVu (반복 장애의 실행 가능·해석 가능 국소화)
- **서지**: Li et al. · "Actionable and Interpretable Fault Localization for Recurring Failures in Online Service Systems" · ESEC/FSE 2022 · DOI [10.1145/3540250.3549092](https://doi.org/10.1145/3540250.3549092) · arXiv [2207.09021](https://arxiv.org/abs/2207.09021) · 동료심사 O · **V2**(초록)
- **메커니즘**: 과거 장애와 시스템 의존을 입력으로 오프라인 국소화 모델(그래프 신경망 계열)을 학습한다. 새 장애에서 **장애 컴포넌트와 장애 종류(지표 그룹)**를 추천한다. 반복 장애에 초점을 둔다.
- **입력 요구**: 의존 그래프, 컴포넌트별 지표 그룹, **라벨이 붙은 과거 장애**.
- **보고 성능**: 운영 시스템 3종 + 공개 벤치마크 1종의 장애 601건에서 1초 미만으로 정답을 평균 1.66~5.03위에 두었고, 기준선보다 54.52% 우수했다(초록).
- **한계/비판**: 새 유형 장애에는 약하고, 라벨 의존도가 높다.
- **코드**: [NetManAIOps/DejaVu](https://github.com/NetManAIOps/DejaVu) · MIT
- **적합도**: 라벨 부족 조건에서 당장은 성립하지 않는다. 운영자 확정 원인이 쌓여 반복 장애가 식별되면 가능하다. → **참고(4단계)**.

### RCA-C-04 · Nezha
- **서지**: Yu et al. · "Nezha: Interpretable Fine-Grained Root Causes Analysis for Microservices on Multi-modal Observability Data" · ESEC/FSE 2023, pp.553–565 · DOI [10.1145/3611643.3616249](https://doi.org/10.1145/3611643.3616249) · 동료심사 O · **V2**(초록)
- **메커니즘**: 메트릭·트레이스·로그를 동질 이벤트로 바꾸고, 이벤트 그래프에서 패턴을 뽑아 **정상 구간과 장애 구간의 패턴을 비교**해 코드 영역·자원 유형 단위 원인을 찾는다(초록).
- **입력 요구**: **트레이스와 trace ID가 붙은 로그 필수**, 메트릭.
- **보고 성능**: 마이크로서비스 애플리케이션 2종에서 코드 영역·자원 유형 수준 top-1 **89.77%**(초록).
- **한계/비판**: 독립 재평가(RCA-B-04, V1, 새 벤치마크)에서 Top@1 **0.04**로 붕괴했다. 원 데이터셋은 트레이스가 매우 적고(Nezha-TT 0.004M) QPS가 낮았다(0.13).
- **코드**: [IntelligentDDS/Nezha](https://github.com/IntelligentDDS/Nezha) · MIT
- **관련(보조)**: MULAN(S-C2), PDiagnose(S-C3), DiagFusion(S-C4)
- **적합도**: 트레이스가 필수라 전제가 성립하지 않고, 독립 재평가 성능도 낮다. → **회피**. 단, "정상 구간 패턴 대비 장애 구간 패턴 차이"라는 비교 원리는 결정적 규칙 설계에 참고할 수 있다.

---

## D. DB 성능 장애 진단 (DPM 연계)

### RCA-D-01 · ADDM (Oracle 자동 성능 진단)
- **서지**: Dias et al. · "Automatic Performance Diagnosis and Tuning in Oracle" · CIDR 2005 · [PDF](https://www.cidrdb.org/cidr2005/papers/P07.pdf) · 동료심사 O · **V2**(OpenAlex·Semantic Scholar 메타데이터, 요약)
- **메커니즘**: 모든 자원·활동의 성능 영향을 비교할 공통 화폐로 **Database Time(DB Time)**을 정의한다. ADDM은 DB 전체 처리량을 떨어뜨리는 병목을 DB Time 관점에서 자동 진단하고 실행 가능한 권고를 낸다. 필요한 성능 측정 유형을 규정한다.
- **입력 요구**: 주기 스냅숏 성능 통계(대기 클래스·SQL·자원별 시간). 라벨 불필요.
- **보고 성능**: 정량 정확도 보고는 확인하지 못했다(제품 설계 논문).
- **한계/비판**: Oracle 전용 제품 논리이고 알고리즘 세부는 공개가 제한적이다.
- **코드**: 비공개(상용).
- **적합도**: 맥스게이지의 대기 클래스별 대기 시간·Top 대기 이벤트·액티브 세션 대기 클래스 추이(벤더 문서) 구성과 개념이 같다. → **채택(DPM 1단계 규칙 분류의 원형)**: "시간(DB Time/AAS)을 대기 클래스·SQL·자원으로 분해하고 가장 큰 몫부터 원인 후보로 올리는" 결정적 분해를 기본으로 둔다.

### RCA-D-02 · DBSherlock
- **서지**: Yoon, Niu, Mozafari · "DBSherlock: A Performance Diagnostic Tool for Transactional Databases" · SIGMOD 2016 · DOI [10.1145/2882903.2915218](https://doi.org/10.1145/2882903.2915218) · 동료심사 O · **V2**(ACM DOI·SIGMOD 채택 목록). 성능 수치는 **V3**(The Morning Paper 요약)
- **메커니즘**: 사용자가 성능 그래프에서 이상 구간을 표시하면, 수백 개 OS·DBMS 통계를 구간으로 나눠 정상/이상을 구분하는 **술어(predicate)**를 생성한다. 도메인 지식 규칙(예: DBMS CPU → OS CPU)으로 2차 증상을 제거한다. DBA가 원인을 확정하면 그 술어 집합이 **인과 모델**로 저장·병합돼 다음 진단에 쓰인다.
- **입력 요구**: 1초 간격 OS 자원·DBMS 워크로드 통계, 쿼리 로그, 설정. 이상 구간 지정. 확정 원인은 선택(피드백)이다.
- **보고 성능(V3)**: 흔한 DB 성능 이상 10종에서 기존 방법보다 F1이 20–55% 높았고, 병합된 인과 모델이 평균 30% 더 정확했다.
- **한계/비판**: MySQL·단일 노드 OLTP 실험 환경이다. iSQUAD(RCA-D-03)가 실클라우드 데이터에서 F1 49.2% 차이로 앞섰다고 보고했다(V1).
- **코드**: [dongyoungy/dbsherlock_sigmod2016](https://github.com/dongyoungy/dbsherlock_sigmod2016) · 라이선스 표기 없음
- **관련(보조)**: DBPA(S-D2) — 트랜잭션 DB 성능 이상 9종 재현 벤치마크
- **적합도**: 운영자 피드백 루프("DBA 확정 원인 → 재사용 가능한 인과 모델")는 우리의 "운영자 노이즈/유효 표시"를 한 단계 확장한 형태와 같다. 술어 생성은 결정적이다. → **채택(원리)**: 확정 원인을 "술어 집합 + 원인명" 레코드로 축적하는 구조를 4단계 라벨 축적 설계의 근거로 쓴다.

### RCA-D-03 · iSQUAD (간헐적 슬로우 쿼리 근본원인)
- **서지**: Ma et al. · "Diagnosing Root Causes of Intermittent Slow Queries in Cloud Databases" · PVLDB 13, 2020 · DOI [10.14778/3389133.3389136](https://doi.org/10.14778/3389133.3389136) · 동료심사 O · **V1**
- **메커니즘**: (1) KPI를 이상/정상 이진이 아니라 **추세·스파이크 등 이상 유형으로 추출(Anomaly Extraction)**, (2) 상관이 강한 KPI 중복을 정리(Dependency Cleansing), (3) 유형 지향 패턴 통합 군집(TOPIC), (4) 베이지안 사례 모델로 설명. **DBA는 각 군집에 한 번만 원인 라벨을 붙인다**(새 유형이 나타나기 전까지). 오프라인 군집·설명 단계와 온라인 진단·갱신 단계로 나뉜다.
- **입력 요구**: 슬로우 쿼리 발생 시점 전후의 DB·호스트 KPI(DBA가 8유형으로 분류한 KPI 체계), 군집당 1회 라벨.
- **보고 성능(V1)**: Alibaba OLTP Database 실데이터에서 F1 **80.4%**(precision 84.1, recall 79.3). DBSherlock보다 **49.2%** 높았다.
- **한계/비판**: Alibaba 클라우드 DB(MySQL 계열) 환경이고, 군집 수와 신규 유형 판정 기준에 운영 튜닝이 필요하다.
- **코드**: 공개 코드 확인 못 함.
- **적합도**: 맥스게이지 슬로우 SQL + 세션·대기 KPI + 호스트 KPI(폴스타)와 입력 구조가 같다. "군집당 1회 라벨"은 라벨이 거의 없는 조건에서 가장 현실적인 최소 라벨 설계다. 알고리즘이 통계·군집 기반이라 CPU로 충분하다. → **채택(2단계 DB 계층)**.

### RCA-D-04 · PinSQL (원인 SQL 특정)
- **서지**: Liu et al. · "PinSQL: Pinpoint Root Cause SQLs to Resolve Performance Issues in Cloud Databases" · ICDE 2022 · DOI [10.1109/ICDE53745.2022.00236](https://doi.org/10.1109/ICDE53745.2022.00236) · 동료심사 O · **V2**(초록)
- **메커니즘**: 이상과 상관된 **고영향 SQL(H-SQL)**과 실제 원인인 **근본원인 SQL(R-SQL)**을 구분한다. 메트릭·쿼리 로그 수집 → 실시간 이상 탐지 → SQL 간 **전파 사슬 추적**으로 R-SQL 특정 → 복구 조치 제안(초록).
- **입력 요구**: 인스턴스 성능 메트릭, SQL별 실행 통계·쿼리 로그.
- **보고 성능**: Alibaba 운영 시스템에서 top-1 R-SQL 정확도 **80%**(초록).
- **한계/비판**: 전파 사슬 추적의 세부는 원문을 읽지 않아 미확인이다. 자동 복구 조치는 우리 읽기 전용 원칙과 맞지 않는다.
- **코드**: 공개 코드 확인 못 함.
- **적합도**: "상관된 SQL ≠ 원인 SQL" 구분은 DPM 슬로우 SQL 목록을 그대로 LLM에 넘기면 안 되는 이유와 같다. → **참고(원문 확인 후 2단계 채택 검토)**. 복구 모듈은 제외한다.

### RCA-D-05 · RCRank (슬로우 쿼리 근본원인 멀티모달 순위화)
- **서지**: Ouyang et al. · "RCRank: Multimodal Ranking of Root Causes of Slow Queries in Cloud Database Systems" · PVLDB 18(4), 2025 · DOI [10.14778/3717755.3717774](https://doi.org/10.14778/3717755.3717774) · arXiv [2503.04252](https://arxiv.org/abs/2503.04252) · 동료심사 O · **V1**(arXiv) + V2
- **메커니즘**: SQL 문, **실행계획**, 실행 로그, KPI를 멀티모달로 인코딩(자기지도 사전학습 + 원인 적응형 교차 Transformer)하고, 원인 유형을 **슬로우 쿼리 가속 잠재력(영향도)** 순으로 순위화한다.
- **입력 요구**: SQL 텍스트·실행계획·로그·KPI, **원인 라벨이 붙은 학습 데이터**, 모델 학습 자원.
- **보고 성능**: 실제·합성 데이터에서 여러 지표로 SOTA보다 일관되게 우수했다(초록).
- **한계/비판**: 지도학습 Transformer라 라벨과 학습 인프라가 필요하고, 폐쇄망 반입·재학습 비용이 크다.
- **코드**: [decisionintelligence/RCRank](https://github.com/decisionintelligence/RCRank) · 라이선스 표기 없음
- **적합도**: 라벨 부족·CPU 우선 조건에서 성립하지 않는다. → **회피(현 단계)**. "영향도 순 원인 순위"라는 출력 형태는 참고한다.

### RCA-D-06 · D-Bot (LLM 기반 DB 진단)
- **서지**: Zhou et al. · "D-Bot: Database Diagnosis System using Large Language Models" · PVLDB 17, 2024 · DOI [10.14778/3675034.3675043](https://doi.org/10.14778/3675034.3675043) · arXiv [2312.01454](https://arxiv.org/abs/2312.01454) · 동료심사 O · **V1**
- **메커니즘**: 진단 문서에서 오프라인 지식 추출 → 지식 매칭·도구 검색(미세조정 임베딩)으로 프롬프트 자동 생성 → 트리 탐색 기반 원인 분석 → 복합 원인은 여러 LLM이 협업한다. PostgreSQL 12.5(pg_stat_statements, hypopg).
- **보고 성능(V1, Table 2, 이상 539건 규모 벤치마크)**:
  - 단일 원인 Acc: HumanDBA **0.955**, D-Bot(GPT-4) 0.754, D-Bot(GPT-3.5) 0.542, DNN 0.352, DecisionTree 0.331, **순수 GPT-4 0.351**, 순수 GPT-3.5 0.266
  - 복합 원인 Acc: HumanDBA 0.487, D-Bot(GPT-4) **0.655**, D-Bot(GPT-3.5) 0.533, DNN 0.036, DecisionTree 0.086, 순수 GPT-4 0.105
  - 사례: 순수 GPT-4는 INSERT_LARGE_DATA에서 실행 프로세스 수 증가만 보고 진단을 일찍 끝냈다.
- **한계/비판**: 주력 결과가 외부 GPT-4다(Llama 2 등 미세조정 변형도 지원). PostgreSQL 주입 이상 기반이며 실운영 은행 DB 검증은 없다.
- **코드**: [TsinghuaDatabaseGroup/DB-GPT](https://github.com/TsinghuaDatabaseGroup/DB-GPT) · Apache-2.0
- **적합도**: 폐쇄망에서는 외부 LLM을 쓸 수 없다. "문서 지식 추출 + 도구 검색"은 HolmesGPT 런북 구성에 참고할 수 있다. 순수 LLM이 단일 원인에서 0.351에 그친 수치는 **LLM 단독 결정 금지**의 DB 영역 근거다. → **참고(LLM 해석 단계 설계 근거)**.

### RCA-D-07 · Panda (LLM 에이전트 DB 성능 디버깅 설계 원칙)
- **서지**: Singh et al. · "Panda: Performance Debugging for Databases using LLM Agents" · CIDR 2024 · [CIDR 페이지](https://vldb.org/cidrdb/2024/panda-performance-debugging-for-databases-using-llm-agents.html) · 동료심사 O(CIDR) · **V2**(초록·DBLP 레코드 링크·Amazon Science 페이지)
- **메커니즘**: ChatGPT에 DB 성능 질의를 하면 "기술적으로는 맞지만 모호하고 일반적인" 권고가 나와 숙련 DB 엔지니어가 신뢰하지 않는다는 문제의식에서 출발한다. 4요소 **Grounding(근거 연결), Verification(검증), Affordance(실행 가능성·영향 추정), Feedback(피드백)**으로 사전학습 LLM의 출력을 맥락적·실행 가능·정확하게 만든다.
- **보고 성능**: 정량 결과는 확인하지 못했다(비전 성격 논문).
- **관련(보조 S-D1)**: Andromeda(SIGMOD'25 데모) — 과거 질의·매뉴얼·텔레메트리·실행 로그 검색 증강 + 오픈소스 LLM 적응
- **코드**: 공개 코드 확인 못 함.
- **적합도**: Grounding·Verification 원칙은 "LLM은 수치 근거를 인용해 서술만 한다"는 우리 설계와 같은 방향이다. → **참고(설계 원칙)**.

---

## E. APM · 트랜잭션 계층 진단 (다차원 KPI 근본원인 국소화)

### RCA-E-01 · Adtributor
- **서지**: Bhagwan et al. · "Adtributor: Revenue Debugging in Advertising Systems" · USENIX NSDI 2014 · [USENIX PDF](https://www.usenix.org/system/files/conference/nsdi14/nsdi14-paper-bhagwan.pdf) · 동료심사 O · **V1**
- **메커니즘**: 가산형 KPI(매출)의 급변을 차원(국가·광고주·브라우저 등)별로 분해한다. **설명력(explanatory power)**, **놀라움(surprise, 분포 변화)**, **간결성(succinctness)** 세 기준으로 원인 차원 값을 고른다.
- **입력 요구**: 차원 속성이 붙은 가산형 KPI(예상값 vs 실제값). 라벨 불필요.
- **보고 성능(V1)**: 초대형 광고 시스템 배포·평가에서 정확도 **95% 초과**, 문제 해결 시간을 한 자릿수 배수(order of magnitude) 단축.
- **한계/비판**: 원인이 단일 차원이라는 가정이 강하다(후속 iDice·HotSpot·Squeeze가 다차원 조합으로 확장).
- **코드**: 비공개.
- **적합도**: 제니퍼 TPS·에러 건수를 (도메인, WAS 인스턴스, 서비스/URL, 업무) 차원으로, 폴스타 알람 건수를 (존, 호스트 그룹, 알람 정의) 차원으로 분해하는 데 바로 쓸 수 있다. 결정적이고 CPU로 충분하다. → **채택(2단계 다차원 분해)**.

### RCA-E-02 · iDice / HALO (속성 조합·계층 인지 국소화, Microsoft)
- **서지**: (1) Lin et al. · "iDice: Problem Identification for Emerging Issues" · ICSE 2016 · DOI [10.1145/2884781.2884795](https://doi.org/10.1145/2884781.2884795) · 동료심사 O · **V1**(Microsoft 저자 PDF). (2) Zhang et al. · "HALO: Hierarchy-aware Fault Localization for Cloud Systems" · KDD 2021 · DOI [10.1145/3447548.3467190](https://doi.org/10.1145/3447548.3467190) · 동료심사 O · **V1**(Microsoft PDF)
- **메커니즘**: iDice는 이슈 보고량이 급증할 때 그 이슈를 특징짓는 **효과적 속성 조합**을 찾는다. 영향 기반·변화 탐지 기반·격리력 기반 가지치기를 쓴다. HALO는 성공/실패 상태가 붙은 다차원 텔레메트리에서 **속성 간 계층 관계(예: 리전→데이터센터→클러스터)를 자동 학습**하고, 이를 이용해 적절한 입도의 장애 지시 조합을 찾는다.
- **입력 요구**: 다차원 속성 + 건수/성공·실패 텔레메트리. 라벨 불필요.
- **보고 성능(V1)**: iDice는 Microsoft 온라인 서비스 운영에 적용했다(정량값 본 조사 미추출). HALO는 Microsoft 365 FuseBot에 **2020년 6월부터 탑재**돼 문제 있는 Exchange Online 빌드 수십 개를 찾았고, precision **86.9%**, recall **93.0%**였다.
- **한계/비판**: 계층 구조가 뚜렷한 텔레메트리를 전제한다.
- **코드**: 공식 비공개(HALO 제3자 재현 [lotcher/HALO](https://github.com/lotcher/HALO), 라이선스 미확인).
- **적합도**: 폴스타(존 → 호스트 그룹 → 호스트)와 제니퍼(도메인 → 인스턴스 → 서비스)는 명확한 계층을 갖는다. → **채택(HALO의 계층 인지 가지치기)**.

### RCA-E-03 · 파문 효과(ripple effect) 계열: HotSpot → Squeeze → PSqueeze (+RiskLoc)
- **서지**:
  - Sun et al. · "HotSpot: Anomaly Localization for Additive KPIs With Multi-Dimensional Attributes" · IEEE Access 2018 · DOI [10.1109/ACCESS.2018.2804764](https://doi.org/10.1109/ACCESS.2018.2804764) · 동료심사 O · **V2**
  - Li et al. · "Generic and Robust Localization of Multi-dimensional Root Causes"(Squeeze) · ISSRE 2019, pp.47–57 · DOI [10.1109/ISSRE.2019.00015](https://doi.org/10.1109/ISSRE.2019.00015) · 동료심사 O · **V2**
  - Li et al. · "Generic and Robust Root Cause Localization for Multi-Dimensional Data in Online Service Systems"(PSqueeze) · Journal of Systems and Software 2023 · DOI [10.1016/j.jss.2023.111748](https://doi.org/10.1016/j.jss.2023.111748) · arXiv [2305.03331](https://arxiv.org/abs/2305.03331) · 동료심사 O · **V1**(arXiv)
  - Kalander · "RiskLoc: Localization of Multi-dimensional Root Causes by Weighted Risk" · arXiv [2205.10004](https://arxiv.org/abs/2205.10004)(2022) · 동료심사 X · **V1**
- **메커니즘**: 근본원인 조합에서 생긴 변화가 상위·하위 집계 조합에 비례적으로 퍼진다는 **파문 효과**를 이용한다. HotSpot은 잠재 점수 + MCTS + 계층 가지치기(가산형 KPI). Squeeze는 **일반화 파문 효과**로 성공률·평균 응답시간 같은 **파생 지표**까지 다루고, 상향식 후 하향식 탐색을 한다. PSqueeze는 확률적 군집 + 강건 휴리스틱 탐색 + **외부 원인(데이터 밖 원인) 판정**을 추가했다. RiskLoc은 2분할 + 가중 위험 점수를 쓴다.
- **입력 요구**: 다차원 속성별 실제값·예측값. 라벨 불필요.
- **보고 성능**: HotSpot은 대형 검색엔진 실데이터에서 전체 원인 유형 사례의 95%를 찾았다(기존 15%, 초록). **Squeeze는 여러 은행과 인터넷 기업 사례 연구**에서 수작업 분석보다 빠르고 정확했고, 준합성 데이터에서 F1이 기존 대비 평균 0.4 높았으며 약 10초였다(초록). PSqueeze는 실데이터 2종의 장애 5,400건에서 F1이 기준선 대비 **32.89%** 높았고, 국소화 약 10초, 외부 원인 판정 F1 **0.90**이었다(V1). RiskLoc은 차순위 대비 F1 최대 57% 향상(V1, preprint).
- **한계/비판**: 예측값(정상 기대값) 품질에 좌우된다. 조합 폭증 시 파라미터 민감도가 있다.
- **코드**: [NetManAIOps/PSqueeze](https://github.com/NetManAIOps/PSqueeze) · MIT. [shaido987/riskloc](https://github.com/shaido987/riskloc) · MIT
- **적합도**: 제니퍼 **평균 응답시간·에러율(파생 지표)**을 인스턴스·서비스·업무 조합으로 국소화하는 데 가장 직접적이다. 은행 사례가 있고 결정적이며 CPU로 충분하다. → **채택(2단계, 제니퍼 계층 핵심)**: Squeeze/PSqueeze 우선, RiskLoc은 사내 비교용이다.

---

## F. 변경 유발 장애 · 알람 상관 · 인시던트 연결

### RCA-F-01 · FUNNEL (소프트웨어 변경 영향 평가)
- **서지**: Zhang et al. · "Rapid and Robust Impact Assessment of Software Changes in Large Internet-based Services" · ACM CoNEXT 2015 · DOI [10.1145/2716281.2836087](https://doi.org/10.1145/2716281.2836087) · 동료심사 O · **V2**(Crossref). 메커니즘·수치는 **V3**(칭화대 NetMan 연구실 페이지)
- **메커니즘(V3)**: 특이 스펙트럼 변환(SST)을 개선한 변화 탐지로 KPI 변화를 잡고, **이중차분(Difference-in-Differences)**으로 변경 대상과 비대상 그룹을 비교해 성능 변화와 소프트웨어 변경 사이의 인과를 우연한 상관과 구분한다.
- **입력 요구**: 변경 이력(대상·시각), 변경·비변경 그룹의 KPI. 라벨 불필요.
- **보고 성능(V3)**: 실서비스 이력 데이터에서 정확도 99.7% 초과.
- **한계/비판**: 비교 대조군(같은 역할의 비변경 노드)이 있어야 하고, 확장판은 IEEE TSC(V3, 미검증)다.
- **코드**: 비공개.
- **적합도**: 폴스타 변경 이력 + 호스트/WAS KPI에 적합하다. DiD는 결정적 통계라 설계 원칙과 맞다. 금융권은 동일 역할 서버군(이중화)이 흔해 대조군을 만들기 쉽다. → **채택(1단계 변경 영향 판정)**.

### RCA-F-02 · Gandalf (Azure 안전 배포 분석)
- **서지**: Li et al. · "Gandalf: An Intelligent, End-To-End Analytics Service for Safe Deployment in Cloud-Scale Infrastructure" · USENIX NSDI 2020 · [USENIX](https://www.usenix.org/conference/nsdi20/presentation/li) · 동료심사 O · **V1**(USENIX PDF)
- **메커니즘**: 다양한 장애 신호를 모든 진행 중인 롤아웃과 **시공간 상관**으로 연결하고, 앙상블 순위화로 원인 롤아웃을 고른 뒤 이진 분류기로 영향 여부를 판정한다.
- **입력 요구**: 롤아웃 메타데이터(대상 노드·시각), 장애 신호(이벤트·로그 기반 장애 유형). 분류기 학습 데이터.
- **보고 성능(V1)**: Azure 운영 18개월 이상. 데이터 플레인 롤아웃 precision **92.4%** / recall **100%**, 컨트롤 플레인 precision **94.9%** / recall **99.8%**.
- **한계/비판**: Azure 규모의 롤아웃 텔레메트리를 전제한다.
- **코드**: 비공개.
- **적합도**: "알람 ↔ 변경"의 시공간 겹침 순위화는 폴스타 변경 이력으로 바로 구현할 수 있다. 분류기 부분은 라벨이 부족하므로 규칙 임계로 대체한다. → **채택(1단계 변경 상관 순위, 분류기는 제외)**.

### RCA-F-03 · 알람 스톰 이해와 처리 (은행 실배포)
- **서지**: Zhao et al. · "Understanding and Handling Alert Storm for Online Service Systems" · ICSE-SEIP 2020, pp.162–171 · DOI [10.1145/3377813.3381363](https://doi.org/10.1145/3377813.3381363) · 동료심사 O(산업 트랙) · **V1**(저자 PDF)
- **메커니즘**: 실알람 데이터로 알람 스톰에 대한 첫 실증 연구를 수행했다. (1) 고정 임계 대신 **알람 스톰 탐지**를 이상 탐지로 정식화하고, (2) **알람 스톰 요약**: 학습 기반 알람 노이즈 제거 → 군집 기반 판별 → 대표 알람 선택.
- **입력 요구**: 알람 이력(텍스트·속성·시계열). 대량 라벨 불필요.
- **보고 성능(V1)**: **China EverBright Bank(대형 상업은행)** 알람 데이터(최대 300만 건 규모 언급)에서 스톰 탐지 F1 **0.9 초과**, 확인이 필요한 알람 수 **98% 이상 감소**. 해당 은행 운영에 적용했다.
- **한계/비판**: 요약 품질 평가는 운영자 판단에 의존한다.
- **코드**: 비공개.
- **적합도**: `noise_gate`(폴스타 알람 수신·억제·통보)와 **직결되는 은행 실배포 동료심사 근거**다. → **채택(1단계, RCA 이전 전처리)**.

### RCA-F-04 · COLA (상관 마이닝 + LLM 하이브리드 알람 집계)
- **서지**: Kuang et al. · "Knowledge-aware Alert Aggregation in Large-scale Cloud Systems: a Hybrid Approach" · ICSE-SEIP 2024 · DOI [10.1145/3639477.3639745](https://doi.org/10.1145/3639477.3639745) · arXiv [2403.06485](https://arxiv.org/abs/2403.06485) · 동료심사 O(산업 트랙) · **V1**
- **메커니즘**: 상관 마이닝 모듈이 알람 간 **시간·공간(토폴로지) 관계**를 효율적으로 측정하고, **신뢰도가 낮은 불확실 쌍만 LLM 추론 모듈로 넘긴다**. LLM에는 알람 **SOP(표준 운영 절차)**를 외부 지식으로 준다. 빈번한 알람은 통계로, 드문 알람은 LLM으로 처리하는 구조다.
- **입력 요구**: 알람 스트림, 토폴로지, 알람별 SOP 문서.
- **보고 성능(V1)**: Huawei Cloud 운영 데이터셋 3종에서 F1 **0.901–0.930**, 효율은 기존 방법과 비슷했다. 운영 배포 경험을 공유했다.
- **한계/비판**: 외부 LLM(ChatGPT 계열) 전제의 지연 문제를 저자도 과제로 지적했다.
- **코드**: 공개 코드 확인 못 함.
- **적합도**: **"결정적 통계가 먼저 결정하고, 불확실 구간만 LLM이 보조"**하는 구조의 동료심사 근거다. 폐쇄망에서는 로컬 LLM으로 바꾸고, LLM 판정을 자동 결정이 아니라 "검토 필요" 표시로 격하해야 원칙에 맞는다. → **채택(구조)**.

### RCA-F-05 · LiDAR (연결된 인시던트 식별)
- **서지**: Chen et al. · "Identifying Linked Incidents in Large-Scale Online Service Systems" · ESEC/FSE 2020, pp.304–314 · DOI [10.1145/3368089.3409768](https://doi.org/10.1145/3368089.3409768) · 동료심사 O · **V2**(초록)
- **메커니즘**: 실제 인시던트 관리(IcM) 시스템에서 연결 인시던트의 지표를 조사하고, 인시던트 텍스트 설명과 **과거 연결 인시던트에서 뽑은 구조 정보**를 결합한 딥러닝으로 연결 후보를 찾는다(초록).
- **입력 요구**: 인시던트 텍스트, 과거 연결 이력(라벨).
- **보고 성능**: 실제 IcM 시스템에서 SOTA보다 우수했다(초록, 정량값 미확인).
- **관련(보조)**: iPACK(S-F1, ICSE'23 — 실존 확인: 고객 티켓과 클라우드 인시던트를 융합한 중복 티켓 집계), GRLIA(S-F2), DiLink(S-F3)
- **코드**: 비공개.
- **적합도**: 텍스트 딥러닝 + 연결 라벨이 필요해 현 단계에는 맞지 않는다. → **참고(4단계)**.

### RCA-F-06 · ICA (장애 조사 문서에서 근본원인 지식 마이닝, Salesforce)
- **서지**: Saha & Hoi · "Mining Root Cause Knowledge from Cloud Service Incident Investigations for AIOps" · ICSE-SEIP 2022 · DOI [10.1145/3510457.3513030](https://doi.org/10.1145/3510457.3513030) · arXiv [2204.11598](https://arxiv.org/abs/2204.11598) · 동료심사 O(산업 트랙) · **V1**(arXiv)
- **메커니즘**: PRB(문제 검토 위원회) 조사 문서에서 신경망 NLP로 대상 정보를 추출해 **구조화된 인과 지식 그래프**를 만들고, 새 인시던트의 증상으로 **과거 인시던트를 검색·순위화해 원인 후보를 추천**한다.
- **입력 요구**: 과거 장애 조사 보고서 코퍼스(수천 건 규모), NLP 추출 모델.
- **보고 성능**: Salesforce의 문서화된 클라우드 인시던트 조사 2천여 건으로 구축했다. 정량 벤치마크, 전문가 검증, 배포 후 사례로 효과를 제시했다(초록 수준, 세부 수치 미정리).
- **한계/비판**: 보고서 품질·형식 일관성에 좌우되고, 조직 고유라 재현할 수 없다.
- **코드**: 비공개.
- **적합도**: 금융권은 장애 보고서(장애 경위·원인·조치) 작성이 의무화돼 코퍼스가 존재할 가능성이 높다. 로컬 임베딩 검색으로 폐쇄망에서도 구현할 수 있다. → **참고(4단계, 과거 유사 장애 검색 도구로 HolmesGPT에 공급)**.

---

## G. LLM 에이전트 RCA 벤치마크 · 최신 동향 (2024–2026)

### RCA-G-01 · OpenRCA
- **서지**: Xu et al. · "OpenRCA: Can Large Language Models Locate the Root Cause of Software Failures?" · ICLR 2025 · [ICLR 논문집](https://proceedings.iclr.cc/paper_files/paper/2025/hash/d29b8d53678015079e1d245c023e49d2-Abstract-Conference.html) · [OpenReview](https://openreview.net/forum?id=M4qNIzQYpd) · 동료심사 O · **V1**
- **구성(V1)**: 장애 335건(Telecom 51 / **Bank 136** / Market 148), 텔레메트리 68.5GB(로그·메트릭·트레이스). 과거 AIOps 챌린지 데이터에서 원인 라벨이 있는 시스템만 골랐다. 과제는 근본원인 요소(컴포넌트·사유 등 1~3개) 식별이다.
- **보고 성능(V1, Table 2 Correct %)**: Claude 3.5 Sonnet — Balanced 샘플링 3.88 / Oracle 샘플링 5.37 / **RCA-agent 11.34**. GPT-4o 3.28 / 6.27 / 8.96. Gemini 1.5 Pro 6.27 / 7.16 / 2.69. Llama 3.1 Instruct 2.99 / 3.88 / 3.28. 요소 3개(Hard) 과제는 **모든 방법 0%**이고, 요소가 1개→2개로 늘면 정확도가 절반 이하로 떨어졌다. 복잡도가 낮은 Telecom이 가장 높았다.
- **한계/비판**: 2024년 모델 기준이다. 샘플링 간격 1분.
- **코드**: [microsoft/OpenRCA](https://github.com/microsoft/OpenRCA) · MIT
- **적합도**: → **채택(근거)**: LLM이 대용량 텔레메트리를 직접 읽고 원인을 결정하는 구조를 배제하는 1차 근거다. 은행 시스템 하위셋이 있어 사내 평가셋 형식(요소별 부분 점수)도 참고한다.

### RCA-G-02 · ITBench
- **서지**: Jha et al. · "ITBench: Evaluating AI Agents across Diverse Real-World IT Automation Tasks" · ICML 2025(Oral) · [PMLR v267](https://proceedings.mlr.press/v267/jha25a.html) · arXiv [2502.05352](https://arxiv.org/abs/2502.05352) · 동료심사 O · **V1**(arXiv v1)
- **구성**: SRE·CISO·FinOps. SRE 진단 과제는 경보·이벤트·트레이스·메트릭·토폴로지 스냅숏에서 근본원인 엔티티와 **장애 전파 사슬**을 식별하는 것이다.
- **보고 성능(V1, arXiv v1 Table 4, SRE 42개 시나리오 × 모델별 10회)**:
  - 진단 pass@1: GPT-4o **13.81%**, Llama-3.3-70B 3.10%, Granite-3.1-8B 3.57%, Llama-3.1-8B 0.99%. FL(NTAM) GPT-4o 0.39
  - **트레이스를 제거하면 GPT-4o 진단 pass@1이 13.81% → 9.52%**, 완화는 2.86%로 급락
  - 초록(v1): 94개 시나리오, SRE 13.8% / CISO 25.2% / FinOps 0%. ICML 게재본 요약 검색 스니펫은 102개·SRE 11.4%(V3) → **버전 차이 주의**
- **한계/비판**: Kubernetes 기반 환경이다.
- **코드**: [itbench-hub/ITBench](https://github.com/itbench-hub/ITBench) · Apache-2.0
- **적합도**: → **채택(근거)**: **트레이스가 없으면 LLM 에이전트 진단이 더 나빠진다**는 정량 증거다. 우리 환경에서 LLM에 원자료가 아니라 정제된 정량 증거를 줘야 하는 이유가 된다.

### RCA-G-03 · AIOpsLab
- **서지**: Chen et al. · "AIOpsLab: A Holistic Framework to Evaluate AI Agents for Enabling Autonomous Clouds" · MLSys 2025 · [MLSys 논문집](https://proceedings.mlsys.org/paper_files/paper/2025/hash/d1f9e4a9f109b6e8b75ed362736f22ec-Abstract-Conference.html) · arXiv [2501.06706](https://arxiv.org/abs/2501.06706) · 동료심사 O · **V1**
- **구성**: 문제 48개(탐지·국소화·원인분석·완화), 장애 주입·부하 생성·텔레메트리 수출·에이전트 인터페이스.
- **보고 성능(V1, Table 4)**: RCA 과제 정확도 — ReAct **45.45%**, GPT-4-w-shell 40.90%, FLASH 36.36%, GPT-3.5-w-shell 9.09%. 국소화 Acc@1 — GPT-4-w-shell 61.54%, ReAct 53.85%, FLASH 46.15%, **PDiagnose 15.38%**, RMLAD 7.69%. 백분율로 역산하면 과제별 문제 수가 11–13개 수준(추정)이라 표본이 작다.
- **한계/비판**: 소표본이고, 비LLM 기준선이 적어 "LLM > 전통 기법" 결론은 제한적이다(RCA-B-04의 단순 기준선 결과와 대조할 것).
- **코드**: [microsoft/AIOpsLab](https://github.com/microsoft/AIOpsLab) · MIT
- **적합도**: → **참고(에이전트 평가 하네스 설계)**.

### RCA-G-04 · RCAgent (사내 배포 LLM 기반 RCA 에이전트)
- **서지**: Wang et al. · "RCAgent: Cloud Root Cause Analysis by Autonomous Agents with Tool-Augmented Large Language Models" · CIKM 2024 · DOI [10.1145/3627673.3680016](https://doi.org/10.1145/3627673.3680016) · arXiv [2310.16340](https://arxiv.org/abs/2310.16340) · 동료심사 O · **V1**
- **메커니즘**: **프라이버시 때문에 외부 GPT 대신 내부 배포 모델(Vicuna-13B-v1.5-16K)**을 쓴다. 도구 증강 자율 에이전트이고, 행동 궤적 수준 Self-Consistency, 컨텍스트 관리·안정화, 도메인 지식 주입을 결합했다. few-shot 예시 없이 동작한다.
- **보고 성능(V1)**: Alibaba Cloud Apache Flink 실시간 컴퓨팅 플랫폼의 작업 장애에서 원인·해결책·증거·책임 예측 전반에 걸쳐 ReAct보다 일관되게 우수했다(자동 지표 + 인간 평가). 진단 워크플로에 통합했다.
- **한계/비판**: Flink 작업 장애라는 좁은 영역이다. 13B 모델도 CPU 추론에는 무겁다.
- **코드**: 공개 코드 확인 못 함.
- **적합도**: **폐쇄망·자체 호스팅 LLM으로 RCA 에이전트를 운영한 동료심사 사례**다. → **참고(HolmesGPT 로컬 LLM 운영 설계 근거)**.

### RCA-G-05 · Flow-of-Action (SOP 강화 멀티에이전트)
- **서지**: Pei et al. · "Flow-of-Action: SOP Enhanced LLM-Based Multi-Agent System for Root Cause Analysis" · WWW 2025 Companion, pp.422–431 · DOI [10.1145/3701716.3715225](https://doi.org/10.1145/3701716.3715225) · arXiv [2502.08224](https://arxiv.org/abs/2502.08224) · 동료심사 O · **V1**
- **메커니즘**: SRE의 진단 단계를 **SOP**로 명시해 LLM을 결정적 분기점마다 제약한다. SOP 검색·자동 생성·코드 변환 도구, 액션 집합(크기 5), 노이즈 제거·탐색 공간 축소·종료 판단 보조 에이전트로 구성된다.
- **보고 성능(V1, Online Boutique 장애 9종 90건, Table 3, 평균 = (LA+TA)/2)**:
  - Flow-of-Action(GPT-4-Turbo) **64.01**, (GPT-3.5-Turbo) 54.06
  - ReAct(GPT-4-Turbo) 35.50, Reflexion(GPT-4-Turbo) 29.06, CoT(GPT-4-Turbo) 32.61
  - **HolmesGPT(GPT-3.5-Turbo) 11.11**, K8SGPT 11.11 — 저자 설명: "한 가지 장애 유형만 다룰 수 있어 고정값"
- **관련(보조 S-G1)**: mABC(EMNLP Findings'24) — 블록체인식 가중 투표 멀티에이전트
- **한계/비판**: 소규모 자체 데이터셋이다. HolmesGPT 수치는 2025년 초 버전 + GPT-3.5 + 기본 도구셋 기준이라 **현재 우리 HolmesGPT 구성에 외삽할 수 없다**.
- **코드**: 공개 코드 확인 못 함.
- **적합도**: → **참고(SOP 제약의 정량 효과 근거)**: 운영 런북을 HolmesGPT 조사 절차의 결정적 뼈대로 쓰는 설계를 지지한다.

### RCA-G-06 · GALA (통계 인과 순위 + LLM 반복 추론)
- **서지**: Tian et al. · "GALA: Can Graph-Augmented Large Language Model Agentic Workflows Elevate Root Cause Analysis?" · arXiv [2508.12472](https://arxiv.org/abs/2508.12472)(2025-08) · 동료심사 X · **V1**
- **메커니즘**: Granger-PR·CausalRCA·BARO 같은 **메트릭 기반 순위를 초기값**으로 삼고, LLM이 로그·트레이스를 반복 조사하며 **재순위화**한 뒤 조치 권고를 생성한다. 인간 가이드 평가 점수(SURE-Score)를 제안했다.
- **보고 성능(V1, RCAEval RE2-OB 90건, Table 2 AC@1 %)**:
  - BARO 14.44 → **GALA(BARO, GPT-4.1-mini) 42.22**, GALA(BARO, GPT-4.1) 38.89
  - CausalRCA 26.67 → GALA(CausalRCA, GPT-4.1) 41.11
  - Granger-PR 12.22 → GALA 12.22~15.56
  - 참고: TraceRCA 13.19, Nezha 8.89, PC-PR 15.56. LLM 온도 1.0, 반복 최대 6회
- **한계/비판**: 단일 데이터셋·preprint이고, 비결정 설정(온도 1.0)의 반복 실행 분산을 보고하지 않았다. 초기 순위가 약하면(Granger-PR) 이득이 거의 없다. **LLM이 순위를 결정한다.**
- **코드**: 공개 코드 확인 못 함.
- **적합도**: "ML 도구가 공급한 정량 순위를 LLM이 쓰면 정확도가 오른다"는 **방향성 증거**다. 단 LLM이 최종 순위를 바꾸는 구조는 우리 원칙("결정은 수치")과 충돌한다. → **참고**: LLM에는 재순위가 아니라 **반증 질의·설명**만 허용하는 변형으로 사내 검증한다.

### RCA-G-07 · KRCA (Kuaishou 초대형 시스템, 구조 사전 + 멀티에이전트 검증)
- **서지**: Jiang et al. · "KRCA: An Efficient Root Cause Analysis System in Hyper-scale Microservice Systems via Agentic AI" · arXiv [2607.01788](https://arxiv.org/abs/2607.01788)(2026-07) · 동료심사 X · **V1**
- **메커니즘**: (1) API 수준 드릴다운(실패율·지연 점수, 임계치)으로 의심 서비스를 좁히고, (2) 이상 메트릭으로 **CIRCA에서 영감을 받은 스켈레톤 인과 그래프**(외부/내부 등 메타 범주)를 고재현율 구조 사전으로 만든 뒤, (3) 기억 증강 멀티에이전트가 인과를 검증하고 보고서를 쓴다. 딥러닝 기준선은 학습·유지 비용 때문에 제외했다.
- **보고 성능(V1, 실장애 300건, Table 1, 루트 서비스 AC@1)**:
  - Claude-opus-4.6 + GPT-5.1: KRCA **0.88** vs RCA-Agent 0.57 · Reflexion 0.48 · ReAct 0.42 · CoT 0.14
  - GPT-5.2 + GPT-5.1: KRCA 0.77 vs RCA-Agent 0.55
  - **오픈 웨이트 MiniMax-M2.7 + DeepSeek-V3: KRCA 0.70** vs RCA-Agent 0.46 · ReAct 0.33
  - 장애 유형 AC@1은 0.79(Claude 조합). 지연 95.7–146.6초/건. 운영 배포 6개월
- **한계/비판**: preprint다. 모든 방법이 같은 도구·지식베이스를 공유한 **LLM 방법끼리만 비교**했고 통계 기준선(BARO 등)과의 비교는 없다. 초대형 모델 의존으로 CPU 추론은 불가능하다.
- **코드**: 공개 코드 확인 못 함.
- **적합도**: "결정적 드릴다운 + 스켈레톤 구조 사전 → 에이전트는 검증"이라는 층위는 우리 단계 설계와 같다. 같은 모델이라면 **구조 사전이 있을 때 ReAct 대비 약 2배**라는 점이 핵심 증거다. → **참고**: 모델 규모는 폐쇄망에서 재현할 수 없으므로 구조만 차용한다.

### RCA-G-08 · EoG (결정적 컨트롤러 + LLM 국소 추론 + 신념 전파)
- **서지**: Jha et al. · "Think Locally, Explain Globally: Graph-Guided LLM Investigations via Local Reasoning and Belief Propagation" · arXiv [2601.17915](https://arxiv.org/abs/2601.17915)(2026-01, IBM) · 동료심사 X · **V1**
- **메커니즘**: 조사를 의존 그래프 위 귀추 추론으로 정식화한다. **LLM은 노드 단위 국소 증거 수집·라벨링(원인/증상)만** 맡고, **결정적 컨트롤러가 순회·상태·신념 전파**를 관리해 최소 설명 경계를 계산한다. ReAct가 탐색 순서에 민감하고 반복 실행마다 결론이 달라지는 문제(Pass@k는 높지만 Majority@k는 낮음)를 겨냥한다.
- **보고 성능(V1)**: ITBench SRE 진단 과제에서 ReAct 기준선 대비 정확도와 반복 일관성이 개선됐고, **Majority@k F1이 평균 7배**였다. 인용한 ReAct 기준 pass@1 recall은 13.81%.
- **한계/비판**: preprint이고, ITBench(Kubernetes) 한정이다.
- **코드**: 공개 코드 확인 못 함.
- **적합도**: HolmesGPT 앞단 오케스트레이션을 "결정은 코드, 해석은 LLM"으로 두는 우리 원칙과 **가장 정확히 일치**하는 최신 근거다. → **채택(오케스트레이션 원칙)**. 평가 지표로 Majority@k(반복 일관성)도 채택한다.

### RCA-G-09 · Cloud-OpsBench (결과 정답률 vs 증거 근거성)
- **서지**: Wang et al. · "Cloud-OpsBench: A Reproducible Benchmark for Agentic Root Cause Analysis in Cloud Systems" · arXiv [2603.00468](https://arxiv.org/abs/2603.00468)(2026-02, v2 2026-08) · 동료심사 X · **V1**
- **구성**: 런타임 검증된 사례 754건, 장애 유형 57종(애플리케이션·Kubernetes 계층, DB 자격증명·연결 고갈 포함). 상태 스냅숏 재생으로 같은 증거에서 비교할 수 있고, 사례마다 진단 증거 그래프가 있다.
- **보고 성능(V1)**: 에이전트 10종 중 최고 **공동 RCA 정확도(JRA) 0.76(OnlineBoutique) / 0.68(TrainTicket)**, 그러나 **증거 폐포율(ECR) 0.38 / 0.15**. 과거 궤적을 ICL로 넣으면 DeepSeek-V4-Flash의 JRA가 0.76→0.83, ECR이 0.38→0.57로 올랐고, SOP 프롬프팅도 비슷한 이득을 냈다.
- **한계/비판**: preprint이고 마이크로서비스 벤치마크다.
- **코드**: [LLM4Ops/Cloud-OpsBench](https://github.com/LLM4Ops/Cloud-OpsBench) · MIT
- **관련(보조)**: SREGym(S-G2), LM-PACE(S-G3)
- **적합도**: → **채택(평가 지표)**: HolmesGPT 브리핑을 "원인 적중"뿐 아니라 "인용한 정량 증거가 실제로 원인을 닫는가"로 채점하는 근거다.

---

## H. 서베이 (분야 지도)

### RCA-H-01 · Soldani & Brogi 서베이
- **서지**: Soldani & Brogi · "Anomaly Detection and Failure Root Cause Analysis in (Micro)Service-Based Cloud Applications: A Survey" · ACM Computing Surveys(2022, 온라인 2021) · DOI [10.1145/3501297](https://doi.org/10.1145/3501297) · arXiv [2105.12378](https://arxiv.org/abs/2105.12378) · 동료심사 O · **V2**
- **내용**: 멀티서비스 애플리케이션의 이상 탐지·근본원인 분석 기법을 구조적으로 개관·정성 분석하고, 열린 과제를 정리했다(초록).
- **적합도**: → **참고(분류 체계)**. 2021년까지가 범위라 LLM·최신 벤치마크 비판은 빠져 있다.

### RCA-H-02 · Notaro et al. AIOps 장애 관리 서베이
- **서지**: Notaro et al. · "A Survey of AIOps Methods for Failure Management" · ACM TIST 2021 · DOI [10.1145/3483424](https://doi.org/10.1145/3483424) · 동료심사 O · **V2**(OpenAlex 메타데이터, 초록 미확보)
- **내용**: AIOps 장애 관리(예방·예측·탐지·원인분석·복구) 방법 분류 서베이.
- **적합도**: → **참고(용어·단계 분류)**.

### RCA-H-03 · Zhang et al. 마이크로서비스 장애 진단 서베이
- **서지**: Zhang et al. · "Failure Diagnosis in Microservice Systems: A Comprehensive Survey and Analysis" · ACM TOSEM 2025 · DOI [10.1145/3715005](https://doi.org/10.1145/3715005) · arXiv [2407.01710](https://arxiv.org/abs/2407.01710) · 동료심사 O · **V2**
- **내용**: 2003년 이후 논문 98편을 검토하고, 공개 데이터셋·툴킷·평가지표를 정리했다(초록).
- **적합도**: → **참고(데이터셋·지표 색인)**. 목표 기반 분류는 보조 S-H1(arXiv 2510.19593, 135편, 2014–2025)이 보완한다.

---

## 보조 인용 (검증 완료, 요약 형식)

| ID | 저자 · 제목(요약) | 게재처·연도 | 식별자 | 심사 | 등급 | 요지 | 판정 |
|---|---|---|---|---|---|---|---|
| S-A1 | Chakraborty et al. · CausIL | WWW 2023 | [10.1145/3543507.3583274](https://doi.org/10.1145/3543507.3583274) | O | V2 | 오토스케일링 인스턴스 단위 인과 그래프, 아키텍처 도메인 지식 주입, 합성에서 SHD 약 25% 개선(초록). RCA-B-01의 합성 데이터 생성기로도 쓰임 | 참고 |
| S-A2 | Lin et al. · RUN(Neural Granger + 대조학습) | AAAI 2024 | [10.1609/aaai.v38i1.27772](https://doi.org/10.1609/aaai.v38i1.27772) | O | V2 | 신경 Granger 인과 + PageRank. RCA-B-01에서 Dummy와 비슷, 대형 시스템은 2시간 제한 초과(V1) | 회피 |
| S-A3 | Xin et al. · CausalRCA | JSS 2023 | [10.1016/j.jss.2023.111724](https://doi.org/10.1016/j.jss.2023.111724) | O | V2 | 기울기 기반 구조 학습 + PageRank. 새 벤치마크 Top@1 0.22, 927초/건(RCA-B-04, V1) | 회피 |
| S-A4 | Nagalapatti et al. · IDI(In-Distribution Interventions) | ICLR 2025(arXiv 코멘트) | [arXiv 2505.00930](https://arxiv.org/abs/2505.00930) | O | V1 | 이상은 분포 밖이라 SCM 반사실 추정이 불안정 → 분포 안 개입 추정. PetShop 평가. Apache-2.0 | 참고(A-07 보완) |
| S-B1 | Zheng et al. · LEMMA-RCA 데이터셋 | arXiv 2024(OpenReview 포럼 존재, 채택 미확인) | [arXiv 2406.05375](https://arxiv.org/abs/2406.05375) | 미확인 | V2 | IT·OT(수처리) 멀티모달 RCA 데이터, CC BY-ND 4.0 | 참고 |
| S-C1 | Lee et al. · Eadro | ICSE 2023, pp.1750–1762 | [10.1109/ICSE48619.2023.00150](https://doi.org/10.1109/ICSE48619.2023.00150) | O | V1 | 트레이스·로그·KPI 다중과제 지도학습(탐지+국소화). 원 평가 부하 2–3 rps라는 비판(RCA-B-02), 새 벤치마크 Top@1 0.16(RCA-B-04). 코드 라이선스 표기 없음 | 회피 |
| S-C2 | Zheng et al. · MULAN | WWW 2024, pp.4107–4116 | [10.1145/3589334.3645442](https://doi.org/10.1145/3589334.3645442) | O | V2 | 로그 언어모델 + 대조학습 멀티모달 인과 구조 학습 + RWR | 회피(학습·GPU·로그 의존) |
| S-C3 | Hou et al. · PDiagnose | IEEE ISPA/BDCloud 2021, pp.493–500 | [10.1109/ISPA-BDCloud-SocialCom-SustainCom52081.2021.00074](https://doi.org/10.1109/ISPA-BDCloud-SocialCom-SustainCom52081.2021.00074) | O | V2 | 경량 비지도 이상 탐지 + 투표 기반 국소화(메트릭·로그·트레이스), recall 84.8%(초록). RCAEval RE2-TT AC@1 0.48, AIOpsLab 국소화 15.38%(V1) | 참고(투표 방식) |
| S-C4 | Zhang et al. · DiagFusion | IEEE TSC 2023 | [10.1109/TSC.2023.3290018](https://doi.org/10.1109/TSC.2023.3290018) | O | V2 | 멀티모달 임베딩 + 배치·트레이스 의존 그래프 + GNN. 새 벤치마크 Top@1 0.13(RCA-B-04) | 회피 |
| S-C5 | Yu et al. · MicroRank | WWW 2021, pp.3087–3098 | [10.1145/3442381.3449905](https://doi.org/10.1145/3442381.3449905) | O | V2 | 정상/비정상 트레이스 스펙트럼 + PageRank. 트레이스 필수, 새 벤치마크 Top@1 0.04 | 회피 |
| S-D1 | (저자 미확인) · Andromeda | SIGMOD 2025 Companion(데모) | [10.1145/3722212.3725080](https://doi.org/10.1145/3722212.3725080) | O(데모) | V2 | 과거 질의·매뉴얼·텔레메트리·실행 로그 검색 증강 + 오픈소스 LLM 적응으로 DBMS 성능 디버깅 | 참고 |
| S-D2 | Huang et al. · DBPA 벤치마크 | PACMMOD 1(1) 2023(SIGMOD) | [10.1145/3588926](https://doi.org/10.1145/3588926) | O | V2 | 트랜잭션 DB 성능 이상 9종 재현 절차 | 참고(DB 평가셋 설계) |
| S-E1 | Sambasivan et al. · Spectroscope(요청 흐름 비교) | USENIX NSDI 2011 | [USENIX](https://www.usenix.org/conference/nsdi11/diagnosing-performance-changes-comparing-request-flows) | O | V2 | 두 실행 기간의 요청 흐름·타이밍 변화를 순위화. 분산 스토리지 사례 6건 | 참고(제니퍼 X-View 트랜잭션 프로파일 전후 비교에 원리 차용) |
| S-E2 | Pool et al. · Lumos(메트릭 회귀 진단) | KDD 2020 | [10.1145/3394486.3403306](https://doi.org/10.1145/3394486.3403306) | O(ADS) | V1 | A/B 테스트 원리로 릴리스 후 지표 회귀를 진단. Skype·Teams에서 오경보 수천 건 기각, 조사 시간 최대 95% 절감 | 참고(변경 후 제니퍼 지표 회귀 판정) |
| S-F1 | Liu et al. · iPACK(인시던트 인지 중복 티켓 집계) | ICSE 2023 | [10.1109/ICSE48619.2023.00193](https://doi.org/10.1109/ICSE48619.2023.00193) | O | V1(arXiv 초록) | 고객 티켓과 클라우드 인시던트 정보를 융합해 중복 티켓 집계, Azure 데이터 3종 F1 0.871–0.935 | 참고 |
| S-F2 | Chen et al. · GRLIA(그래프 기반 인시던트 집계) | ASE 2021 | [10.1109/ASE51524.2021.9678746](https://doi.org/10.1109/ASE51524.2021.9678746) | O | V1(arXiv) | 장애 연쇄 그래프 표현학습 + KPI로 영향 범위 복원, Huawei Cloud 배포. MIT | 참고 |
| S-F3 | Ghosh et al. · DiLink(의존 인지 인시던트 연결) | arXiv 2024(게재처 미확인) | [arXiv 2403.18639](https://arxiv.org/abs/2403.18639) | 미확인 | V2 | 텍스트 + 서비스 의존 그래프로 서비스 간 인시던트 연결 | 참고 |
| S-F4 | Zhao et al. · SCWarn(나쁜 변경 식별) | ESEC/FSE 2021 | [10.1145/3468264.3468543](https://doi.org/10.1145/3468264.3468543) | O | V1 | **대형 상업은행(China Guangfa Bank 공저) 2년치 실데이터**. 해당 은행 인시던트의 약 **50.4%가 나쁜 변경 기인**. 로그·메트릭 멀티모달(동적 GNN) 이상 탐지로 나쁜 변경 조기 경고, F1 평균 0.95 | 참고(모델은 딥러닝이라 보류, **"은행 장애 절반이 변경 기인"은 변경 상관 1단계 우선순위의 근거로 채택**) |
| S-G1 | Zhang et al. · mABC | EMNLP 2024 Findings, pp.4017–4033 | [10.18653/v1/2024.findings-emnlp.232](https://doi.org/10.18653/v1/2024.findings-emnlp.232) | O | V1 | 블록체인식 가중 투표 멀티에이전트. Train-Ticket 평균 51.3 vs ReAct 41.0(GPT-4-Turbo) | 참고 |
| S-G2 | Clark et al. · SREGym | arXiv 2026 | [arXiv 2605.07161](https://arxiv.org/abs/2605.07161) | X | V2 | 라이브 환경 SRE 문제 90개(주변 노이즈·준안정·상관 장애 포함), 에이전트 간 종단 결과 최대 40% 차이 | 참고 |
| S-G3 | LM-PACE(LLM 신뢰도 추정) | FSE 2024 Companion(Industry) | [10.1145/3663529.3663858](https://doi.org/10.1145/3663529.3663858) | O(산업) | V2 | 검색 증강 LLM 프롬프팅으로 원인 예측의 보정된 신뢰도 추정 → 환각 판별 | 참고(HolmesGPT 출력 신뢰도 표기) |
| S-H1 | Fang et al. · 목표 기반 RCA 서베이 | arXiv 2025 | [arXiv 2510.19593](https://arxiv.org/abs/2510.19593) | X | V2 | 135편(2014–2025)을 입력 데이터가 아니라 RCA 목표(빠른 분류 vs 확정 수정)로 분류 | 참고 |

---

## 2. 동료심사 / preprint / 산업 자료 3층 분리

### 2.1 동료심사 (학회·저널)

| 층 | ID | 제목(약칭) | 비고 |
|---|---|---|---|
| 동료심사 | RCA-A-01 | NetMedic | SIGCOMM'09 |
| 동료심사 | RCA-A-02 | CloudRanger / MicroCause | CCGRID'18 / IWQoS'20 |
| 동료심사 | RCA-A-03 | ε-Diagnosis | WWW'19 |
| 동료심사 | RCA-A-04 | MicroRCA | NOMS'20 |
| 동료심사 | RCA-A-05 | CIRCA | KDD'22 |
| 동료심사 | RCA-A-06 | RCD | NeurIPS'22 |
| 동료심사 | RCA-A-07 | 인과 구조 기반 이상치 RCA + DoWhy-GCM | ICML'22 / JMLR'24 |
| 동료심사 | RCA-A-08 | PCMCI | Science Advances'19 |
| 동료심사 | RCA-A-09 | BARO | FSE'24 |
| 동료심사 | RCA-A-10 | Murphy | SIGCOMM'23 |
| 동료심사(채택 표기, 출판본 미확인) | RCA-A-11 | TORAI | FSE'26(arXiv 코멘트) |
| 동료심사 | RCA-B-01 | How Far Are We? | ASE'24 |
| 동료심사(동반 논문집) | RCA-B-02 | RCAEval | WWW'25 Companion |
| 동료심사 | RCA-B-03 | PetShop | CLeaR'24 |
| 동료심사 | RCA-B-04 | Rethinking RCA Evaluation | PACMSE / FSE'26 |
| 동료심사(산업 사례) | RCA-C-01 | Groot | ASE'21 |
| 동료심사(산업 트랙) | RCA-C-02 | Chain-of-Event | FSE'24 Industry |
| 동료심사 | RCA-C-03 | DejaVu | ESEC/FSE'22 |
| 동료심사 | RCA-C-04 | Nezha | ESEC/FSE'23 |
| 동료심사 | RCA-D-01 | ADDM | CIDR'05 |
| 동료심사 | RCA-D-02 | DBSherlock | SIGMOD'16 |
| 동료심사 | RCA-D-03 | iSQUAD | PVLDB'20 |
| 동료심사 | RCA-D-04 | PinSQL | ICDE'22 |
| 동료심사 | RCA-D-05 | RCRank | PVLDB'25 |
| 동료심사 | RCA-D-06 | D-Bot | PVLDB'24 |
| 동료심사 | RCA-D-07 | Panda | CIDR'24 |
| 동료심사 | RCA-E-01 | Adtributor | NSDI'14 |
| 동료심사 | RCA-E-02 | iDice / HALO | ICSE'16 / KDD'21 |
| 동료심사(RiskLoc 제외) | RCA-E-03 | HotSpot / Squeeze / PSqueeze | IEEE Access'18 / ISSRE'19 / JSS'23 |
| 동료심사 | RCA-F-01 | FUNNEL | CoNEXT'15 |
| 동료심사 | RCA-F-02 | Gandalf | NSDI'20 |
| 동료심사(산업 트랙) | RCA-F-03 | 알람 스톰(은행) | ICSE-SEIP'20 |
| 동료심사(산업 트랙) | RCA-F-04 | COLA | ICSE-SEIP'24 |
| 동료심사 | RCA-F-05 | LiDAR | ESEC/FSE'20 |
| 동료심사(산업 트랙) | RCA-F-06 | ICA | ICSE-SEIP'22 |
| 동료심사 | RCA-G-01 | OpenRCA | ICLR'25 |
| 동료심사 | RCA-G-02 | ITBench | ICML'25 |
| 동료심사 | RCA-G-03 | AIOpsLab | MLSys'25 |
| 동료심사 | RCA-G-04 | RCAgent | CIKM'24 |
| 동료심사(동반 논문집) | RCA-G-05 | Flow-of-Action | WWW'25 Companion |
| 동료심사 | RCA-H-01 | Soldani & Brogi | ACM CSUR'22 |
| 동료심사 | RCA-H-02 | Notaro et al. | ACM TIST'21 |
| 동료심사 | RCA-H-03 | Zhang et al. | ACM TOSEM'25 |
| 동료심사 | S-A1~A4, S-C1~C5, S-D1~D2, S-E1~E2, S-F1, S-F2, S-F4, S-G1, S-G3 | 보조 인용 | 표 참조 |

### 2.2 Preprint (동료심사 미확인)

| 층 | ID | 제목(약칭) | 비고 |
|---|---|---|---|
| preprint | RCA-A-12 | PRISM / Graph-Free RCA | arXiv 2601.21359, 단독 저자 |
| preprint | RCA-B-05 | Pooled Leaderboards 감사 | arXiv 2606.29159 |
| preprint | RCA-E-03 일부 | RiskLoc | arXiv 2205.10004 |
| preprint | RCA-G-06 | GALA | arXiv 2508.12472 |
| preprint | RCA-G-07 | KRCA | arXiv 2607.01788 |
| preprint | RCA-G-08 | EoG(Think Locally, Explain Globally) | arXiv 2601.17915 |
| preprint | RCA-G-09 | Cloud-OpsBench | arXiv 2603.00468 |
| preprint | S-B1, S-F3, S-G2, S-H1 | LEMMA-RCA, DiLink, SREGym, 목표 기반 서베이 | 표 참조 |

### 2.3 산업 자료 (비학술)

| 층 | 자료 | 사용처 |
|---|---|---|
| 산업 자료 | DoWhy 문서 `gcm.attribute_anomalies`, 마이크로서비스 RCA 예제 노트북([pywhy.org](https://www.pywhy.org/dowhy/v0.11.1/example_notebooks/gcm_rca_microservice_architecture.html)) | RCA-A-07 구현 경로 |
| 산업 자료(V3) | The Morning Paper — DBSherlock 요약([blog.acolyer.org](https://blog.acolyer.org/2016/07/14/dbsherlock-a-performance-diagnostic-tool-for-transactional-databases/)) | RCA-D-02 성능 수치 |
| 산업 자료(V3) | 칭화대 NetMan 연구실 FUNNEL 소개 페이지 | RCA-F-01 메커니즘·수치 |
| 산업 자료 | 제니퍼소프트 기술 블로그 "X-View 실시간 패턴 분석"([jennifersoft.com](https://jennifersoft.com/ko/blog/tech/2023-07-26-jennifer-x-view/)) | 공백 3(학술 문헌 부재 확인) |
| 산업 자료 | 엑셈 MaxGauge 제품 페이지·사용자 가이드(대기 클래스·Top 대기 이벤트·액티브 세션 대기 클래스 추이) | RCA-D-01 대응 관계, 공백 4 |
| 산업 자료 | GitHub API 라이선스 조회 결과(각 저장소) | 코드·라이선스 표기 |

---

## 3. 조사에서 발견한 공백

아래 공백은 "근거가 없으니 보수적으로 설계해야 한다"는 설계 근거로 쓸 수 있다. 검색 범위(WebSearch + OpenAlex/Semantic Scholar/arXiv) 안에서 찾지 못했다는 뜻이며, 부재를 증명한 것은 아니다.

1. **시간·일 집계 해상도에서 평가한 RCA 문헌이 없다.** 공개 벤치마크의 메트릭 간격은 RCAEval RE2/RE3·Eadro **1초**, AIOps-2021·GAIA 30초, Nezha·CIRCA(Oracle)·OpenRCA **1분**, PetShop **5분**이다(RCA-B-04 표, RCA-A-05, RCA-G-01, RCA-B-03, V1/V2). 폴스타가 시간·일·월 집계 중심이라면 **어느 문헌의 성능 수치도 그대로 기대할 수 없다.** 원시 해상도를 얼마나 보존하는지 먼저 실측해야 한다.
2. **트레이스 없는 WAS↔DB↔호스트 교차 계층 RCA의 실환경 동료심사 검증이 없다.** 실환경 수치가 있는 것은 계층 내부 한정이다: DB 내부(CIRCA 은행 Oracle 99건), 알람 요약(은행), 다차원 국소화(Squeeze 은행 사례), 이벤트 그래프(Groot, eBay는 서비스 의존 사용). 전통적 금융 3-tier(WAS + RDBMS)에서 계층을 가로지르는 인과 순위를 검증한 연구는 찾지 못했다.
3. **APM 트랜잭션 프로파일(X-View류)과 JVM GC·스레드풀 병목의 RCA 학술 문헌이 없다.** 분산 요청 흐름 비교(Spectroscope, S-E1)가 가장 가까운 원리이고, JVM 수준 진단은 벤더 블로그·가이드만 검색됐다.
4. **대기 이벤트/ASH 분포를 직접 입력으로 하는 ML RCA 동료심사 문헌이 드물다.** ADDM(규칙, 정량 평가 없음), DBSherlock·iSQUAD·PinSQL(MySQL/클라우드 KPI·SQL 중심), D-Bot(PostgreSQL + LLM)이 있다. Oracle AAS·대기 이벤트를 쓴 실환경 정량 결과는 CIRCA의 은행 사례가 사실상 유일하다.
5. **확정 라벨 없이 운영자 피드백(노이즈/유효)만으로 RCA를 학습·평가한 문헌이 부족하다.** 최소 라벨 설계로는 "DBA 확정 원인 → 인과 모델 재사용"(DBSherlock)과 "군집당 1회 라벨"(iSQUAD) 정도만 확인된다.
6. **"ML 도구 + LLM 해석" 하이브리드의 동료심사 증거가 얇다.** 동료심사는 COLA(알람 집계)뿐이고, RCA 정확도 이득을 보인 GALA·KRCA·EoG는 모두 preprint다. **"LLM은 순위를 바꾸지 않고 서술만 한다"는 우리 설계를 직접 검증한 연구는 없다**(간접 지지만 있음). GALA·KRCA는 오히려 LLM이 최종 판정을 내린다.
7. **벤치마크 자체의 신뢰성이 흔들린다.** 단순 규칙(SimpleRCA, max-|Z|, 알람 건수)이 SOTA와 비슷하고(B-04, B-05), 한 저자군이 방법(BARO, TORAI, PRISM)과 평가(ASE'24, RCAEval)를 함께 만들어 **독립 재현이 부족**하다. 논문 수치를 사내 성능 기대치로 옮기면 안 된다.
8. **폐쇄망·CPU 추론 조건의 RCA 에이전트 평가가 없다.** 자체 호스팅 사례는 RCAgent(Vicuna-13B) 하나다. KRCA의 오픈 웨이트 결과(0.70)는 초대형 모델이다. CPU 전용·소형 모델 조건의 정량 결과는 찾지 못했다.
9. **한국 상용 도구(제니퍼·맥스게이지) 데이터 기반 학술 문헌을 찾지 못했다**(KCI 키워드 검색 포함 제한적 검색). 벤더 문서·사례 발표 자료만 있다.
10. **실환경 인과 그래프의 정답이 없다.** 인과 발견 평가는 합성 데이터에 의존하는데, 합성 성능이 실환경을 대표하지 못한다(B-01). 사내에서 인과 발견 결과를 검증할 수단이 원천적으로 없다는 뜻이므로 **도메인 지식 그래프를 정본으로 삼는 편이 검증 가능하다**.

---

## 4. 종합 권고 — 문헌 기반 단계적 RCA 아키텍처

전체 흐름: **(0) 데이터 전제 실측 → (1) 결정적 규칙·이벤트화 → (2) 라벨 불필요 통계 점수 → (3) 도메인 지식 구조 그래프(인과 "발견"이 아님) → (4) 운영자 확정 원인 축적 후 학습 → (5) LLM 해석**. 각 단계 산출물은 HolmesGPT가 호출하는 **read-only 도구 응답(순위·점수·근거 필드)**으로 노출한다. 최종 결정 필드는 수치·규칙이 채우고, LLM은 그 필드를 인용해 서술만 한다.

### 단계 0 — 데이터 전제 실측 (구현 전 필수)
- 폴스타 메트릭의 **원시 보존 해상도·보존 기간**, 제니퍼 Open API 조회 해상도, 맥스게이지 세션·대기 데이터 해상도를 확인한다. 1시간 집계만 있다면 1~3단계 문헌 수치를 기대치로 쓰지 않는다. 근거: 공백 1, RCA-B-03(5분 간격), RCA-B-04(데이터셋별 간격 표).
- **호스트 ↔ WAS 인스턴스 ↔ DB 인스턴스 매핑**을 정본 설정으로 만든다. 이것이 트레이스 대체물이다. 근거: RCA-A-04(서비스-머신 이중 그래프), RCA-A-01(의존 템플릿), RCA-C-01(서비스 의존 + 규칙).
- 시계 동기와 알람 발생 시각 정확도를 점검한다. 근거: RCA-B-01(CIRCA·NSigma는 장애 시각 오차에 민감).

### 단계 1 — 결정적 규칙·이벤트화 (라벨 불필요, CPU)
| 기능 | 근거 ID |
|---|---|
| 알람 스톰 탐지·대표 알람 요약(`noise_gate` 연계) | RCA-F-03(은행 실배포), RCA-F-04(상관 마이닝 우선) |
| 변경 이력 ↔ 알람·KPI 시공간 상관 순위, 이중차분 변경 영향 판정(은행 인시던트 절반이 변경 기인이라는 실증에 따라 우선 구현) | RCA-F-02, RCA-F-01, S-E2, S-F4 |
| 이상을 이벤트로 정규화 + 운영자 규칙 + 의존 간선 → 이벤트 그래프 | RCA-C-01 |
| DB: DB Time/AAS를 대기 클래스·SQL·자원으로 분해해 최대 몫부터 후보화 | RCA-D-01 |

### 단계 2 — 라벨 불필요 통계 점수 (CPU, 밀리초~초)
| 기능 | 근거 ID |
|---|---|
| 메트릭별 강건 척도화 편차(중앙값/IQR) 순위 — **기준선** | RCA-A-09, RCA-B-01 |
| max-|Z|·알람 건수·Dummy 기준선 병행 산출(평가·신뢰 표시용) | RCA-B-05, RCA-B-04, RCA-B-01 |
| 제니퍼 응답시간·에러율(파생 지표) 다차원 국소화: 인스턴스×서비스×업무 | RCA-E-03(Squeeze/PSqueeze), RCA-E-01, RCA-E-02(HALO 계층) |
| 내부(호스트 자원) vs 외부(응답·에러) 속성 분해 순위 — 실험 트랙 | RCA-A-12, RCA-A-11 |
| DB: KPI 이상 유형 추출 + 군집 + **군집당 1회 운영자 라벨** | RCA-D-03 |
| DB: 고영향 SQL과 원인 SQL 구분 | RCA-D-04 |
| 2표본 분포 검정 기반 스코어러(원시 해상도가 분 단위일 때) | RCA-A-03 |

### 단계 3 — 도메인 지식 구조 그래프 기반 인과 점수 (자동 인과 발견은 회피)
| 기능 | 근거 ID |
|---|---|
| **DBA·운영자가 검토한 스켈레톤 구조 그래프 + 회귀 가설검정 + 하위 노드 보정**(DB 계층부터) | RCA-A-05(은행 Oracle AAS: AC@1 0.404 vs NSigma 0.323), RCA-G-07(스켈레톤 사전의 효과) |
| 알려진 DAG 구간의 이상 기여도 분해(DoWhy-GCM, MIT) — "추정치"로 표기 | RCA-A-07, S-A4(반사실 불안정 비판) |
| 순환 의존(WAS 풀 ↔ DB 세션) 구간은 MRF 방식 검토 | RCA-A-10 |
| 이력 유사도 기반 간선 영향 가중 | RCA-A-01 |
| **회피**: PC/FCI/Granger/PCMCI/신경 Granger 기반 자동 인과 발견 + PageRank/랜덤워크 | RCA-B-01(≈Dummy), RCA-A-02, RCA-A-08, S-A2, S-A3, 공백 10 |
| **회피**: 트레이스 필수·지도학습 멀티모달 딥러닝 | RCA-C-04, S-C1, S-C2, S-C4, S-C5, RCA-B-04 |

### 단계 4 — 운영자 확정 원인 축적 후 (라벨 확보 시점부터)
| 기능 | 근거 ID |
|---|---|
| 확정 원인을 "술어 집합 + 원인명" 인과 모델로 저장·병합·재사용 | RCA-D-02 |
| 가중치를 사람이 읽을 수 있는 이벤트 인과 그래프 학습(초기 가중치는 수동) | RCA-C-02 |
| 반복 장애 분류·국소화 | RCA-C-03 |
| 과거 장애 보고서 검색(로컬 임베딩) → 유사 사례를 증거로 공급 | RCA-F-06, RCA-F-05, S-F1 |

### 단계 5 — LLM 해석 (HolmesGPT)
| 원칙 | 근거 ID |
|---|---|
| LLM이 원시 텔레메트리를 읽고 원인을 **결정하지 않는다** | RCA-G-01(최고 11.34%, Hard 0%), RCA-G-02(트레이스 없으면 9.52%), RCA-D-06(순수 GPT-4 단일 원인 0.351), RCA-G-05(HolmesGPT 11.11%, 구버전 기준) |
| 조사 순회·상태·신념 갱신은 **결정적 컨트롤러**가 맡고 LLM은 국소 증거 서술만 한다 | RCA-G-08 |
| 운영 런북·SOP로 조사 절차를 제약한다 | RCA-G-05, RCA-G-09(SOP·궤적 ICL 효과), RCA-D-07 |
| 통계가 확신하는 건은 통계로 종결하고, 저신뢰 건만 LLM 검토("검토 필요" 표시) | RCA-F-04 |
| LLM에 **재순위화 권한을 주지 않는다**. 반증 질의·설명만 허용하고, 재순위 효과(GALA·KRCA)는 사내 A/B로만 검증한다 | RCA-G-06, RCA-G-07, 공백 6 |
| 자체 호스팅 모델 운영(프라이버시·폐쇄망) | RCA-G-04 |
| 출력에 신뢰도 표기 | S-G3 |

### 사내 평가 체계 (모든 단계 공통)
- **내부 리플레이 평가셋**: 폴스타 알람 이력 + 운영자 노이즈/유효 표시 + 장애 보고서의 확정 원인(있는 경우)으로 구성하고, 요소별 부분 점수 형식을 쓴다. 근거: RCA-G-01, RCA-D-03.
- **필수 기준선**: Dummy, NSigma/BARO, max-|Z|, 알람 건수. 근거: RCA-B-01, RCA-B-04, RCA-B-05.
- **분리 보고**: 존(은행존/공동존)·업무·계층별 성적을 따로 내고 합산 리더보드를 금지한다. 근거: RCA-B-05.
- **민감도**: 장애 시각 ±오차, 입력 해상도(원시 vs 1시간 집계)별 성능. 근거: RCA-B-01, 공백 1.
- **LLM 단계 지표**: 원인 적중 + 증거 폐포율 + 반복 일관성(Majority@k). 근거: RCA-G-09, RCA-G-08.
- **라이선스 게이트**: MIT/BSD/Apache(BARO·RCAEval·CIRCA·DoWhy·PSqueeze·RiskLoc·DejaVu·ITBench·OpenRCA)는 반입 가능 후보다. GPL-3.0(tigramite/PCMCI)과 라이선스 미표기(Chain-of-Event·Eadro·RCRank·RUN·DBSherlock)는 반입 전 법무 확인이 필요하다.

---

## 5. 검색 로그

| 경로 | 질의/대상 | 성과 | 실패·제약 |
|---|---|---|---|
| OpenAlex API(`title.search`) | 후보 제목 54건 일괄 | 44건 DOI·연도 확인(CausIL=WWW'23 교정 등) | ε-Diagnosis·PDiagnose·DBSherlock·Panda·iDice·Gandalf·OpenRCA·AIOpsLab 미매칭. 2차 배치(21건)는 **HTTP 429** 레이트 리밋으로 전부 실패, 이후 초록 조회도 429 |
| DBLP 검색 API | CIRCA 등 | 없음 | **Anubis 봇 차단 페이지** 반환으로 사용 불가 |
| Crossref `/works/{doi}` | DOI 25건 | 게재처·페이지·저자 수 확인 | 2건 429 |
| Semantic Scholar batch API | DOI·arXiv ID 40여 건 | 초록 확보(MicroCause·MicroRCA·HotSpot·Squeeze·PinSQL·Chain-of-Event·Nezha·DejaVu·Murphy·RCD·PCMCI·Soldani·TOSEM 서베이 등) | ε-Diagnosis·RUN(DOI)·Notaro·FUNNEL·DBSherlock·iDice·DBPA 초록 없음 |
| arXiv API `id_list` | 2508.12472 외 25건 | 2025–2026 preprint 메타데이터·코멘트(채택 표기) 확인 | `search_query=ti:` 제목 검색은 **타임아웃/빈 응답**. 잘못된 ID 포함 시 400 |
| PDF 원문(curl + pdftotext) | arXiv·ICLR·USENIX·Microsoft·NetMan 등 35편 | V1 수치 대조 | umich(DBSherlock)·cidrdb(Panda)·illinois(Murphy) 다운로드 실패, dl.acm.org PDF는 HTML 반환 |
| WebFetch | NetMedic·알람 스톰 PDF, PMLR 페이지, Panda CIDR 페이지 | NetMedic·알람 스톰은 로컬 저장본을 pdftotext/페이지 이미지로 판독, Panda 초록 확보 | PMLR(ICML'22, ITBench) 페이지 fetch 실패, DBSherlock PDF 인증서/ECONNRESET, Murphy 인증서 불일치 |
| WebSearch(성공 질의 예) | "ε-Diagnosis … WWW 2019", "Causal structure-based root cause analysis of outliers ICML 2022 PMLR", "DBSherlock SIGMOD 2016", "Panda CIDR 2024", "OpenRCA ICLR 2025", "AIOpsLab MLSys 2025", "PDiagnose ISPA 2021", "iDice ICSE 2016", "Gandalf NSDI 2020", "ITBench ICML 2025", "2026 arXiv LLM agent RCA hybrid causal", "2025 RCA metrics-only without traces", "Chain-of-Event FSE 2024", "RCRank VLDB", "SCWarn", "PetShop NeurIPS"(→CLeaR'24로 교정), "LEMMA-RCA NeurIPS 2025"(→채택 미확인), "Lumos KDD 2022 LinkedIn"(→KDD'20 Microsoft로 교정), "Rethinking … SimpleRCA", "Andromeda SIGMOD 2025", "Murphy SIGCOMM 2023", "TOSEM failure diagnosis survey", "HALO KDD 2021", "Fast Dimensional Analysis SIGMETRICS", "Mining Root Cause Knowledge ICSE SEIP 2024"(→2022로 교정), "GRLIA ASE 2021", "DBPA SIGMOD 2023", "Spectroscope NSDI 2011", "LM-PACE FSE 2024", "Knowledge-aware Alert Aggregation ICSE-SEIP 2024", "FUNNEL CoNEXT 2015 SST DiD", "DoWhy gcm attribute_anomalies" | 게재처·DOI·코드 URL 확인 | — |
| WebSearch(성과 없음) | "JVM garbage collection thread pool performance anomaly root cause diagnosis … research paper" | 벤더 가이드·블로그만 → 공백 3 | 동료심사 문헌 미발견 |
| WebSearch(성과 없음) | "wait events active session history anomaly root cause machine learning paper" | 특허·DBPA·arXiv 2606.13699(미정독)만 | 동료심사 ML RCA 미발견 → 공백 4 |
| WebSearch(성과 없음) | "제니퍼 APM X-View 장애 원인 분석 논문 KCI", "맥스게이지 MaxGauge 대기 이벤트 성능 진단 머신러닝 논문" | 벤더 블로그·제품 문서만 | 학술 문헌 미발견 → 공백 9 |
| GitHub API | 저장소 24개 라이선스·보관 상태 | MIT/BSD/Apache/GPL/미표기 구분, PetShop archived 확인 | — |

- 조사에 쓴 원문 텍스트(pdftotext 추출본)는 조사 세션의 임시 작업 폴더에만 있었고 저장소에 보관하지 않았다. 수치를 다시 대조하려면 각 항목의 DOI·arXiv 원문을 사용한다.
- 미검증으로 목록에서 뺀 것: "A Multimodal Machine Learning Framework for Enterprise Database Workload-Aware Root Cause Analysis"(arXiv 2606.13699, 검색 결과만 확인하고 원문 미정독), Fast Dimensional Analysis(SIGMETRICS'20, DOI 미확인), FUNNEL 확장판(IEEE TSC, 연구실 페이지 언급만), DBA-Bench(arXiv 2607.22165, 초록만 확인하고 채택 판단 보류).
