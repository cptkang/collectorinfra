# ML 기반 이상탐지·장애 예측(선제 탐지) 문헌 심층 조사

> **보관 메모(2026-09-17)**: `plans/101`(ML 기반 장애 진단·예측·RCA) 작성용 조사 원본. 조사 에이전트가 작성했고, 계획서 §3·§14가 이 문서의 항목 ID(PRED-*)를 인용한다. 설계 해석의 정본은 계획서이며, 이 문서는 서지·수치·검증 등급의 근거다. 성능 수치는 원문 대조(V1)분만 적었다.

- 작성일: 2026-09-17 · 대상: 폐쇄망 금융권 AIOps(폴스타·제니퍼 APM·DPM·Prometheus) · 설계 원칙: 결정은 결정적 수치·규칙, LLM은 해석만
- 규모: **60개 항목**(계열 병합 포함, 근거 문헌·자료 약 95건). 목표치(40~55건)를 넘긴 것은 2025~2026년 시계열 파운데이션 모델(TSFM)의 버전·라이선스 변동이 커서 계열별로 따로 적어야 했기 때문이다.
- 검증 등급: **V1** 원문(PDF 본문 텍스트 추출·표 확인) · **V2** 초록/메타데이터(Crossref·arXiv API·OpenAlex·PMLR/OpenReview/학회 페이지·공식 문서·HF/GitHub API) · **V3** 2차 자료(검색 요약·블로그)
- 수치는 V1으로 확인한 것만 적었다. V2 항목의 수치는 저자가 초록에 쓴 주장이며 그렇다고 따로 표시했다.
- 인용수는 영향력 판단에 쓰지 않았다(OpenAlex는 AI 분야를 적게 센다. Chronos·TimesFM·Moirai는 제목 검색으로 레코드가 나오지 않았다).

## 0. 핵심 결론 요약

1. **이상탐지(탐지)에서는 단순 기법이 딥러닝을 이긴다. 원문 표로 확인했다.** TSB-AD(NeurIPS'24 D&B)의 단변량 VUS-PR은 Sub-PCA 0.42인 반면 Anomaly Transformer 0.12, TranAD 0.26, TimesNet 0.26이다. 서버 데이터셋 SMD의 point-wise F1도 PCA 재구성오차가 0.572로 Anomaly Transformer(0.426)와 TranAD(0.457)보다 높다(Sarfraz et al. ICML'24).
2. **point-adjust(PA) 평가는 무작위 점수도 SOTA로 만든다.** SWaT에서 무작위 점수의 F1-PA는 0.969인데 PA를 빼면 F1이 0.216이다(Kim et al. AAAI'22). 따라서 PA는 금지한다.
3. **예측(forecasting)에서는 방향이 반대다. 파운데이션 모델이 통계 기준선을 크게 앞선다.** GIFT-Eval에서 Seasonal Naive 대비 WQL skill은 Chronos-2 51.4%, AutoARIMA 8.8%다. 관측 데이터 벤치마크 BOOM에서도 Toto MASE 0.617, Auto-ARIMA 0.824다. 그러므로 파운데이션 모델은 **탐지기보다 "기대 대역·ETA 산출기"로 쓰는 쪽이 문헌과 맞는다.**
4. **폐쇄망에서는 라이선스가 1차 필터다.** Moirai 계열(1.x·MoE·2.0)은 CC-BY-NC-4.0, TimesFM-3(2026-08)의 가중치는 비상업·비운영 전용이다. TiRex는 연매출 1억 유로를 넘는 조직이 상용 제품에 넣으려면 별도 상용 라이선스가 필요하다. Apache-2.0/MIT로 남는 모델은 Chronos·Chronos-Bolt·Chronos-2, TimesFM-2.5, Toto 1.0/2.0, TTM, Sundial/Timer/Time-MoE, Lag-Llama, MOMENT다.
5. **알람 기반 장애 예측의 금융권 실증이 있다.** eWarn(ESEC/FSE'20)은 대형 상업은행 11개 시스템에서 평균 F1 0.82를 보고했다. 다만 리드타임이 10분이고 겹치는 창 단위로 채점했다. AirAlert(WWW'19)에서는 단순 스파이크 규칙(F1 76.28)이 제안 기법(79.01)에 근접했다. 단순 규칙 기준선을 반드시 함께 둬야 한다는 근거다.
6. **최대 공백은 두 가지다.** 시간 단위 집계 메트릭만으로 서버 장애를 선제 예측한 동료심사 실증이 사실상 없다. 리드타임을 반영한 이벤트 단위 평가 지표도 표준이 없다.

---

## 1. 영역별 문헌

### A. 서베이·분야 지도

**PRED-A-01** · Salfner et al. · *A survey of online failure prediction methods* · ACM Computing Surveys, 2010 · DOI 10.1145/1670679.1670680 · 동료심사 · **V2**(메타·초록), 시간 파라미터 정의는 V3
- 메커니즘: 런타임 모니터링과 과거 경험으로 **온라인 고장 예측**을 한다. 접근법 분류 체계를 제시한다(초록). 리드타임 Δt_l은 최소 경고시간 Δt_w보다 커야 하고, 예측은 예측기간 Δt_p 동안 유효하다고 정의한다(V3: 2차 자료로 확인).
- 입력 요구: 분류 체계라서 해당 없음. 증상 모니터링(메트릭)과 오류 보고(알람·로그) 계열을 모두 포괄한다.
- 보고 성능: 해당 없음(서베이).
- 한계: 2010년 이전 문헌이라 딥러닝·파운데이션 모델을 다루지 않는다.
- 공개 코드: 해당 없음.
- **적합도 → 채택(평가 용어 체계).** 리드타임·경고시간·예측기간 정의를 §4 평가 프로토콜의 기준 용어로 쓴다.

**PRED-A-02** · Notaro et al. · *A Survey of AIOps Methods for Failure Management* · ACM TIST, 2021 · DOI 10.1145/3483424 · 동료심사 · **V2**
- 메커니즘: AIOps 장애관리 솔루션 100건을 **개입 시점(time intervention window)** 기준 5개 범주·14개 하위범주로 분류하고 적용 요건과 정량 결과를 정리했다(초록).
- 입력 요구·성능: 서베이라 해당 없음.
- 한계: 2021년까지만 포함하고 LLM·파운데이션 모델은 없다.
- **적합도 → 채택(분야 지도).** 선제 탐지(예측)와 사후 탐지·진단 사이의 경계를 설계 문서에 인용하기 좋다.

**PRED-A-03** · Zamanzadeh Darban et al. · *Deep Learning for Time Series Anomaly Detection: A Survey* · ACM CSUR, 2024 · DOI 10.1145/3691338 · 동료심사 · **V2**
- 함께 본 자료: Blázquez-García et al., *A Review on Outlier/Anomaly Detection in Time Series Data*, ACM CSUR 2021, DOI 10.1145/3444690(V2). Boniol et al., *Dive into Time-Series Anomaly Detection: A Decade Review*, arXiv 2412.20512(2024, preprint, V2).
- 메커니즘: 딥러닝 시계열 이상탐지를 탐지 전략(예측·재구성 등)과 모델 계열로 분류하고 장단점을 정리했다(초록). Blázquez-García는 비지도 이상치 탐지를, Boniol은 과정 중심 분류와 메타분석을 다룬다.
- 한계: 서베이는 원 논문의 보고 성능(대개 PA 기반)을 그대로 옮기는 경향이 있다. 성능 비교의 근거로는 D 영역 벤치마크를 써야 한다.
- **적합도 → 참고.**

**PRED-A-04** · Zhang et al. · *A Survey of AIOps in the Era of Large Language Models* · ACM CSUR, 2025 · DOI 10.1145/3746635(arXiv 2507.12472) · 동료심사 · **V2**
- 메커니즘: 2020-01~2024-12에 나온 183편을 네 가지 질문으로 분석했다. 장애 데이터원, 과업 진화, LLM 기법, 평가 방법이다(초록).
- 한계: LLM 중심이라 수치형 시계열의 결정적 탐지는 주변부로 다룬다.
- **적합도 → 참고.** "LLM은 해석, 결정은 규칙" 경계를 정당화할 때 분야 동향 근거로 쓴다.

### B. 단변량/KPI 이상탐지(운영 실전)

**PRED-B-01** · Liu et al. · *Opprentice: Towards Practical and Automatic Anomaly Detection Through Machine Learning* · IMC 2015 · DOI 10.1145/2815675.2815679 · 동료심사 · **V2**
- 메커니즘: 기존 탐지기 여러 개를 병렬로 돌려 이상 특징으로 쓰고, 운영자 라벨로 **랜덤포레스트**를 학습시켜 탐지기·파라미터 조합과 임계를 자동으로 고른다.
- 입력 요구: 단변량 KPI와 운영자의 주기적 라벨링(편의 도구 제공). 지도학습이다.
- 보고 성능(초록 주장): 검색엔진 KPI 3종에서 recall ≥0.66, precision ≥0.66을 만족하거나 근접했다. 라벨링은 수십 분이면 되지만 기존 방식의 수동 튜닝은 10일 이상 걸렸다.
- 한계: 2015년 결과이고 데이터가 비공개다. 라벨 품질에 의존한다.
- 공개 코드: 공식 저장소 미확인.
- **적합도 → 참고(단계 2~4 피드백 학습의 원형).** 노이즈 게이트의 운영자 알람 피드백을 "탐지기 앙상블 선택 라벨"로 재사용하는 설계와 맞는다. 랜덤포레스트라 CPU·결정성 요건을 충족한다.

**PRED-B-02** · Xu et al. · *Unsupervised Anomaly Detection via Variational Auto-Encoder for Seasonal KPIs in Web Applications* (Donut) · WWW 2018 · DOI 10.1145/3178876.3185996 · 동료심사 · **V1**(평가 절차 부분)
- 메커니즘: 계절성 KPI용 VAE다. 결측·이상 구간을 반영한 학습 목적함수와 MCMC 보정으로 재구성확률을 산출한다.
- 입력 요구: 단변량 분 단위 KPI(원문에 "alert delay 1 interval (1 minute)" 예시). 라벨 없이도 학습한다.
- 보고 성능: 대형 인터넷 기업 KPI의 best F-score 0.75~0.9. **best F-score는 모든 임계를 열거한 최댓값(오라클 임계)이고, 구간 단위 조정을 거쳤다(원문 확인).** 오늘날 point-adjust 평가 관행의 원형이다.
- 독립 재평가(V1): TSB-AD에서 Donut의 VUS-PR은 단변량 0.20, 다변량 0.26으로 하위권이다. SR-CNN 논문 표에서는 Yahoo F1이 0.026이다. 평가 설정에 극도로 민감하다는 뜻이다.
- 공개 코드: NetManAIOps/donut(GitHub 라이선스 미표기).
- **적합도 → 회피(운영 탐지기).** 계열별 학습 모델이 필요하고 성능 근거가 약하다. 평가 관행의 기원으로만 참고한다.

**PRED-B-03** · Ren et al. · *Time-Series Anomaly Detection Service at Microsoft* (SR-CNN) · KDD 2019 · DOI 10.1145/3292500.3330680 · 동료심사 · **V1**
- 메커니즘: 시각 saliency 분야의 **Spectral Residual(SR)**을 시계열에 적용하고, 합성 이상으로 학습한 CNN 판별기를 결합했다. Bing·Office·Azure의 지표 수백만 개를 감시하는 서비스에 쓰였다.
- 입력 요구: 단변량. SR 단독은 학습이 필요 없고, CNN은 합성 이상으로 학습한다. 해상도는 분·시간을 모두 다룬다.
- 보고 성능(V1, 원문 Table 2·3):
  - 콜드스타트 F1: SR-CNN은 KPI 0.732, Yahoo 0.655, Microsoft 0.537이고 SR 단독은 0.666, 0.529, 0.484다.
  - 시간순 절반 분할 테스트 F1: SR-CNN은 0.771, 0.652, 0.507이다.
  - **평가 방식:** 이상 구간 시작부터 k 지점 안에 한 번이라도 탐지하면 구간 전체를 정답으로 인정한다(분 단위 k=7, 시간 단위 k=3). 지연 상한이 있는 PA 변형이다.
- 독립 재평가(V1): TSB-AD 단변량에서 SR의 VUS-PR은 0.32로 중위권인데, **PA-F1은 0.87로 표에서 가장 높다.** PA가 소음형 점수를 우대한다는 사례다.
- 한계·산업 동향(V2): SR-CNN을 쓴 Azure AI Anomaly Detector는 2023-09-20부터 신규 생성이 막혔고 **2026-10-01에 퇴역**한다(I-06).
- 공개 코드: microsoft/anomalydetector, MIT(마지막 푸시 2022).
- **적합도 → 참고(SR 단독은 단계 2 보조 탐지기 후보).** 학습이 필요 없고 CPU로 가볍다. 점 이상 신호에 한정해 쓰고, 평가는 PA 없이 다시 해야 한다.

**PRED-B-04** · Yeh et al. · *Matrix Profile I: All Pairs Similarity Joins for Time Series* · IEEE ICDM 2016 · DOI 10.1109/ICDM.2016.0179 · 동료심사 · **V2**
- 메커니즘: 부분수열 전쌍 유사도 조인으로 각 부분수열의 최근접 거리(matrix profile)를 구한다. 거리가 가장 먼 부분수열(discord)이 이상이다. 중간에 멈춰도 근사해를 내는 anytime 알고리즘이다.
- 입력 요구: 단변량과 부분수열 길이 1개. 라벨 없음.
- 독립 재평가(V1): TSB-AD 단변량 VUS-PR 0.35(10위). 원문에 따르면 이상이 하나뿐인 경우 강하다.
- 한계: 부분수열 길이 선택에 민감하다(일반론). 이상이 반복되면 서로가 최근접이 되어 가려질 수 있다(일반론, 미검증).
- 공개 코드: STUMPY(GitHub SPDX 미판정).
- **적합도 → 참고.** 시간 단위 집계에서 "평소와 다른 하루 패턴"을 사후에 찾는 분석용이다.

**PRED-B-05** · Guha et al. · *Robust Random Cut Forest Based Anomaly Detection on Streams* · ICML 2016, PMLR 48:2712–2721 · 동료심사 · **V2**
- 메커니즘: 스트림 요약용 랜덤 컷 트리 앙상블이다. 점을 넣고 뺄 수 있고, 보지 못한 점이 나머지 데이터에 주는 영향으로 비모수 이상도를 정의한다.
- 입력 요구: 단·다변량(shingling). 라벨 없음. 온라인 갱신.
- 보고 성능: 원문 수치 미확인.
- 한계: TSB-AD·Sarfraz 표에 포함되지 않아 독립 비교 근거가 없다.
- 공개 코드: kLabUM/rrcf(MIT, JOSS 2019), aws/random-cut-forest-by-aws(Apache-2.0).
- **적합도 → 참고(단계 2 후보).** CPU 스트리밍에 적합하다. 채택 전에 내부 데이터로 PCA·KNN 기준선과 비교해야 한다.

**PRED-B-06** · 강건 계절 분해 + 강건 통계 계열
- Wen et al., *RobustSTL: A Robust Seasonal-Trend Decomposition Algorithm for Long Time Series*, AAAI 2019, DOI 10.1609/aaai.v33i01.33015409, 동료심사, V2
- Hochenbaum et al., *Automatic Anomaly Detection in the Cloud Via Statistical Learning*(Twitter S-H-ESD), arXiv 1704.07706(2017), preprint, V2
- 메커니즘:
  - RobustSTL(Alibaba): 긴 주기와 계절성 이동에 강건한 추세·계절 분해.
  - S-H-ESD: 계절 분해로 추세·계절을 걸러낸 뒤 **중앙값과 MAD** 기반 Generalized ESD로 이상을 판정한다. 계절 스파이크가 있어도 오탐이 적다(초록).
- 입력 요구: 단변량, 계절 주기 지정. 라벨 없음. CPU.
- 보고 성능: S-H-ESD는 운영 데이터로 Precision·Recall·F를 보고했다(초록, 수치 미확인).
- 한계(V1): Schmidl et al.(VLDB'22)의 71개 알고리즘 평가에서 **S-H-ESD는 처리 신뢰도(성공 처리 데이터셋 비율)가 52% 미만인 8개 알고리즘에 들었고 성능도 나빴다.** 구현 품질·설정 문제로 보인다.
- 공개 코드: twitter/AnomalyDetection은 **GPL-3.0이고 보관(archived) 상태다.** 코드 반입은 피하고 방법만 재구현한다.
- **적합도 → 채택(방법론, 단계 1).** 현행 `noise_gate/infrastructure/metric_stl.py`(robust STL + 잔차 모표준편차 z)에 바로 연결된다. 잔차 척도를 MAD 기반으로 바꾸는 것이 문헌과 맞는다.

**PRED-B-07** · 변화점 탐지
- Adams & MacKay, *Bayesian Online Changepoint Detection*, arXiv 0710.3742(2007, 기술보고서), 동료심사 미확인, V2
- Truong et al., *Selective review of offline change point detection methods*, Signal Processing 167:107299(2020), DOI 10.1016/j.sigpro.2019.107299, 동료심사, V2
- 메커니즘:
  - BOCPD: 마지막 변화점 이후 경과시간(run length)의 사후분포를 메시지 전달로 온라인 갱신한다.
  - Truong: 오프라인 변화점 탐지를 비용함수·탐색·제약으로 분해해 정리한 리뷰이고, ruptures 라이브러리의 이론 배경이다.
- 입력 요구: 단·다변량. 라벨 없음. CPU.
- 한계: BOCPD는 관측 모델 가정(사전분포)이 필요하다. 오프라인 방법은 사후 분할용이다.
- 공개 코드: deepcharles/ruptures(BSD-2-Clause).
- **적합도 → 채택(단계 1).** 배포·설정 변경 뒤의 수준 이동, 용량 추세 기울기 변화를 탐지하고 변경·점검 창과 연결한다. 파운데이션 모델이 지속 이상보다 분포 이동 탐지에 유망하다는 보고(G-12)와도 맞물린다.

### C. 다변량 이상탐지(딥러닝)

**PRED-C-01** · 확률적 VAE 계열(NetMan)
- Su et al., *Robust Anomaly Detection for Multivariate Time Series through Stochastic Recurrent Neural Network*(OmniAnomaly), KDD 2019, DOI 10.1145/3292500.3330672, 동료심사, V2
- Li et al., *Multivariate Time Series Anomaly Detection and Interpretation using Hierarchical Inter-Metric and Temporal Embedding*(InterFusion), KDD 2021, DOI 10.1145/3447548.3467075, 동료심사, V2
- 메커니즘:
  - OmniAnomaly: 확률적 RNN과 planar normalizing flow로 재구성확률을 산출하고 지표별 기여로 해석을 제공한다. **서버 데이터셋 SMD**를 공개했다.
  - InterFusion: 지표 간·시간 의존을 두 잠재변수로 분리한 계층 VAE이고, MCMC로 해석한다.
- 입력 요구: 다변량, 엔티티(서버)별 학습, 분 단위. SMD는 서버 28대·지표 38개(Sarfraz 기술 기준).
- 보고 성능(초록 주장): OmniAnomaly 전체 F1 0.86, InterFusion 평균 F1 >0.94. 둘 다 PA 계열 관행으로 채점했다.
- 독립 재평가(V1):
  - Kim et al.: OmniAnomaly의 **SMD F1-PA는 0.944지만 PA 없는 F1은 0.474(재현)**다.
  - Sarfraz et al.: SMD F1 0.415로 PCA 오차(0.572)보다 낮다.
  - TSB-AD: 다변량 VUS-PR 0.31로 상위(그림 기준 2위)이고 PCA와 같다.
- 공개 코드: OmniAnomaly(MIT). InterFusion은 NetManAIOps 조직에서 저장소를 찾지 못했다(404).
- **적합도 → 회피(운영 탐지기) / 참고(지표별 기여 해석).** 시간 단위 집계면 30일에 720점뿐이라 서버별 딥모델 학습 데이터가 부족하다. 같은 점수를 내는 PCA가 더 단순하다.

**PRED-C-02** · Audibert et al. · *USAD: UnSupervised Anomaly Detection on Multivariate Time Series* · KDD 2020 · DOI 10.1145/3394486.3403392 · 동료심사 · **V2**
- 메커니즘: 인코더 하나에 디코더 둘을 두고 적대적으로 학습하는 오토인코더다. 학습이 빠르고, Orange IT 운영에서 타당성을 검증했다(초록).
- 입력 요구: 다변량, 비지도.
- 독립 재평가(V1):
  - Kim et al.: SWaT F1 0.791, SMD F1 0.426(재현).
  - TSB-AD: 단변량 VUS-PR 0.36(8위), 다변량 0.30(5위).
  - Sarfraz et al.: SMD F1 0.426으로 PCA 0.572보다 낮다.
- 공개 코드: manigalati/usad(SPDX 미판정).
- **적합도 → 참고.** IT 운영에서 나왔고 가볍지만 PCA보다 낫다는 증거가 없다. 단계 2에서 비교군으로만 둔다.

**PRED-C-03** · Transformer 계열
- Xu et al., *Anomaly Transformer*, ICLR 2022(Spotlight, OpenReview LzQQ89U1qm_), V2
- Tuli et al., *TranAD*, PVLDB 2022, DOI 10.14778/3514061.3514067, V2
- Yang et al., *DCdetector*, KDD 2023, DOI 10.1145/3580305.3599295, V2
- 모두 동료심사.
- 메커니즘: Anomaly Transformer는 연관 불일치(association discrepancy), TranAD는 적대적·자기조건화 Transformer, DCdetector는 이중 주의 대조학습이다.
- 보고 성능: 원 논문은 PA 적용 F1로 높은 수치를 보고한다.
- 독립 재평가(V1):
  - TSB-AD 단변량 VUS-PR: **Anomaly Transformer 0.12(목록 최하위)**, TranAD 0.26.
  - TSB-AD 다변량: Anomaly Transformer 0.12, TranAD 0.18.
  - Sarfraz et al. SMD F1: Anomaly Transformer 0.426, TranAD 0.457 대 PCA 0.572.
  - SWaT F1-PA: Anomaly Transformer 0.941인데 **무작위 점수가 0.963**이다.
  - DCdetector는 이번 조사에서 독립 수치를 확인하지 못했다.
- 공개 코드: Anomaly-Transformer(MIT), TranAD(BSD-3), DCdetector(라이선스 미표기).
- **적합도 → 회피.** 독립 벤치마크에서 단순 기법보다 일관되게 열세이고, GPU 친화적이며, 해석성이 낮다.

**PRED-C-04** · Wu et al. · *TimesNet: Temporal 2D-Variation Modeling for General Time Series Analysis* · ICLR 2023 · arXiv 2210.02186 · 동료심사 · **V2**
- 메커니즘: FFT로 주요 주기를 찾아 1D 시계열을 2D 변동으로 바꾸고 2D 합성곱으로 모델링한다. 예측·이상탐지 범용이다.
- 독립 재평가(V1): TSB-AD VUS-PR 단변량 0.26, 다변량 0.19.
- 공개 코드: thuml/Time-Series-Library(MIT).
- **적합도 → 회피(탐지기) / 참고(라이브러리가 비교 실험 도구로 유용).**

**PRED-C-05** · Wu et al. · *CATCH: Channel-Aware multivariate Time Series Anomaly Detection via Frequency Patching* · ICLR 2025 · arXiv 2410.12261 · 동료심사 · **V2**
- 메커니즘: 주파수 영역을 대역 패치로 나누고, Channel Fusion Module(패치별 마스크와 masked attention)로 관련 채널을 군집화한다(초록).
- 보고 성능(초록 주장): 실데이터 10종·합성 12종에서 SOTA.
- 한계: TAB(D-07)과 같은 연구 그룹이라 **자체 벤치마크 편향 가능성**이 있다. 독립 재평가를 찾지 못했다.
- 공개 코드: decisionintelligence/CATCH(라이선스 미표기).
- **적합도 → 회피.** 2024~2026 신작이지만 독립 검증과 라이선스가 모두 없다.

### D. 평가 방법론 비판(가장 중요)

**PRED-D-01** · Wu & Keogh · *Current Time Series Anomaly Detection Benchmarks are Flawed and are Creating the Illusion of Progress* · IEEE TKDE(DOI 10.1109/TKDE.2021.3112126, 2021년 발행 표기), 확장 초록 ICDE 2022 · 동료심사 · **V1**
- 메커니즘(비판): 공개 벤치마크(Yahoo·Numenta·NASA 등)의 네 가지 결함을 지적한다. **자명성(triviality), 비현실적 이상 밀도, 오라벨, run-to-failure 편향**이다. 자명성은 "MATLAB 기본 연산 한 줄로 풀리면 자명하다"로 정의했다.
- 보고 수치(V1): **Yahoo 367개 계열 중 316개(86.1%)가 한 줄짜리 식으로 풀린다.** 대안으로 UCR Anomaly Archive를 제안했다(규모는 원문 재확인 필요).
- 한계: 한 줄짜리 식에는 사람이 고른 매직 넘버가 들어간다(저자 인정).
- **적합도 → 채택(평가 원칙).** 내부 골든셋을 만들 때 자명한 사례와 이상 밀도 과다를 피하고, 장애 직전 구간에 이상이 몰리는 편향을 점검한다.

**PRED-D-02** · Kim et al. · *Towards a Rigorous Evaluation of Time-Series Anomaly Detection* · AAAI 2022 · DOI 10.1609/aaai.v36i7.20680 · 동료심사 · **V1**
- 메커니즘(비판): point-adjust(PA)는 구간 중 한 점만 맞혀도 구간 전체를 정답으로 친다. 이 방식이 성능을 이론적·실험적으로 과대평가함을 보였다. 대안으로 **PA%K**를 제안했다. 구간의 K% 이상을 맞혔을 때만 조정하는 방식이다.
- 보고 성능(V1, Table 2): 무작위 점수(Case 1)의 F1-PA와 F1은 다음과 같다.

  | 데이터셋 | F1-PA | F1 |
  |---|---|---|
  | SWaT | 0.969 | 0.216 |
  | WADI | 0.965 | 0.109 |
  | MSL | 0.931 | 0.190 |
  | SMAP | 0.961 | 0.227 |
  | SMD | 0.804 | 0.080 |

  - SMD에서 PA 없는 F1: 입력 자체를 점수로 쓴 경우(Case 2) 0.494, 무작위 초기화 모델(Case 3) 0.466이다. OmniAnomaly 0.474, USAD 0.426, GDN 0.529와 비슷하다.
  - F1-PA와 F1의 상관은 SWaT에서 Pearson −0.59, Kendall 0.07이다.
- **적합도 → 채택(PA 금지의 1차 근거).**

**PRED-D-03** · Paparrizos et al.
- *TSB-UAD: An End-to-End Benchmark Suite for Univariate Time-Series Anomaly Detection*, PVLDB 2022, DOI 10.14778/3529337.3529354
- *Volume Under the Surface: A New Accuracy Evaluation Measure for Time-Series Anomaly Detection*, PVLDB 2022, DOI 10.14778/3551793.3551830
- 동료심사 · **V2**
- 메커니즘:
  - TSB-UAD: 계열 13,766개. 기존 18개 데이터셋 1,980계열, 분류 데이터셋 변환 958계열, 변형 10,828계열이다.
  - VUS: 임계와 구간 버퍼를 함께 변화시킨 **임계 독립·범위 인지** 척도(VUS-ROC/PR)다. 잡음·어긋남·이상 비율 변화에 강건하다(초록).
- **적합도 → 채택(오프라인 알고리즘 비교 척도로 VUS-PR).**

**PRED-D-04** · Liu & Paparrizos · *The Elephant in the Room: Towards A Reliable Time-Series Anomaly Detection Benchmark* (TSB-AD) · NeurIPS 2024 Datasets & Benchmarks Track · DOI 10.52202/079017-3437 · 동료심사 · **V1**
- 메커니즘: 40개 데이터셋에서 고른 고품질 계열 1,070개로 탐지기 40종(통계·신경망·파운데이션 모델)을 하이퍼파라미터 튜닝 후 비교했다. **VUS-PR이 가장 신뢰할 만한 척도**라고 결론 냈다.
- 보고 성능(V1, Table 5 평균 VUS-PR):

  | 구분 | 방법별 VUS-PR |
  |---|---|
  | 단변량(TSB-AD-U) | **Sub-PCA 0.42**, KShapeAD 0.40, POLY 0.39, Series2Graph 0.39, MOMENT(FT) 0.39, MOMENT(ZS) 0.38, KMeansAD 0.37, USAD 0.36, Sub-KNN 0.35, MatrixProfile 0.35, CNN 0.34, SR 0.32, TimesFM 0.30, IForest 0.30, OmniAnomaly 0.29, Lag-Llama 0.27, Chronos 0.27, TimesNet 0.26, TranAD 0.26, Donut 0.20, **AnomalyTransformer 0.12** |
  | 다변량(TSB-AD-M) | CNN 0.31, OmniAnomaly 0.31, PCA 0.31, LSTMAD 0.31, USAD 0.30, TimesNet 0.19, TranAD 0.18, AnomalyTransformer 0.12 |

  - 무작위 점수를 32개 탐지기와 함께 PA-F1로 순위 매기면 **26위**다. 실제 탐지기 여러 개보다 높다. PA-F1은 부적합하다.
  - 파운데이션 모델은 **점 이상에 강하고(TimesFM 1위, Chronos 3위) 연속 구간 이상에 약하다.** 예측 창이 짧기 때문이다. 사전학습 데이터 오염 위험이 있다(MOMENT 사전학습에 이상탐지 데이터셋이 포함됨).
- 한계: 그림 7의 다변량 순위(CNN·OmniAnomaly·PCA 순)와 본문 서술("second and third")이 어긋나 있다. 이 문서에는 표 수치를 적었다.
- 공개 코드: TheDatumOrg/TSB-AD(Apache-2.0, 2026-09 활동).
- **적합도 → 채택(1순위 근거).** 단계 2 후보군(Sub-PCA·KShapeAD·POLY·KMeansAD·IForest)과 VUS-PR 채택의 직접 근거다.

**PRED-D-05** · Schmidl, Wenig, Papenbrock · *Anomaly Detection in Time Series: A Comprehensive Evaluation* · PVLDB 15(9):1779–1797, 2022 · DOI 10.14778/3538598.3538602 · 동료심사 · **V1**
- 메커니즘: 알고리즘 71종을 재구현해 데이터셋 976종에서 평가했다(AUC-ROC 등).
- 보고 결과(V1, 원문 §5):
  - "deep learning approaches are not (yet) competitive despite their higher processing effort on training data"
  - "simple methods yield performance almost as good as more sophisticated methods"
  - "no single algorithm clearly performs best"
  - k-Means 부분수열 군집이 다변량에서 효과적이다.
  - 알고리즘 87%가 데이터셋 70% 이상을 처리했다. 처리 신뢰도가 52% 미만인 알고리즘은 8종이다.
- **적합도 → 채택.** 단일 최강 알고리즘이 없으므로 내부 데이터로 선택하는 절차가 필수라는 근거다.

**PRED-D-06** · 단순 기준선 실증
- Sarfraz et al., *Position: Quo Vadis, Unsupervised Time Series Anomaly Detection?*, ICML 2024(arXiv 2405.02678), 동료심사, **V1**
- Audibert et al., *Do deep neural networks contribute to multivariate time series anomaly detection?*, Pattern Recognition 132:108945(2022), DOI 10.1016/j.patcog.2022.108945, 동료심사, 메타 V2·내용 V3
- 메커니즘(비판): SOTA 딥 탐지기는 사실상 선형 사상을 학습하고, 복잡도를 늘려도 개선이 거의 없다(Sarfraz 초록). Audibert는 전통·ML·딥러닝 16종을 공개 데이터 5종에서 비교했고 어느 계열도 우위가 없었다(V3).
- 보고 성능(V1, Sarfraz Table 2):

  | 방법 | SMD F1 | WADI-127 F1 | SWaT F1 |
  |---|---|---|---|
  | **PCA 재구성오차** | **0.572** | **0.501** | **0.833** |
  | 1-NN 거리 | 0.463 | — | — |
  | L2-norm | 0.404 | — | — |
  | GDN | 0.526 | — | — |
  | TranAD | 0.457 | — | — |
  | Anomaly Transformer | 0.426 | 0.209 | — |
  | USAD | 0.426 | — | — |
  | OmniAnomaly | 0.415 | — | — |
  | GCN-LSTM 1층 | — | — | 0.829 |
  | Random | 0.080 | — | — |

  - F1-PA 기준으로는 Random이 SWaT 0.963, SMD 0.894다.
  - 원문은 SWaT를 결함 있는 벤치마크로 보고 사용 중단을 권고한다(Keogh 개인교신 인용).
- **적합도 → 채택.** 단계 2의 의무 기준선(PCA 재구성오차·1-NN 거리·L2-norm·무작위 점수)을 정하는 근거다.

**PRED-D-07** · 산업형·통합 벤치마크
- Qiu et al., *TAB: Unified Benchmarking of Time Series Anomaly Detection Methods*, PVLDB 2025(arXiv 2506.18046 "Accepted by PVLDB2025"), 동료심사, V2
- Si et al., *TimeSeriesBench: An Industrial-Grade Benchmark for Time Series Anomaly Detection Models*, ISSRE 2024(arXiv 2402.10802 comment), 동료심사, V2
- 메커니즘:
  - TAB: 다변량 29종·단변량 1,635계열이다. 비학습·ML·딥러닝·LLM·사전학습 방법을 통합 파이프라인으로 평가한다.
  - TimeSeriesBench: 곡선 수만 개를 **통합 모델 하나로 운영**하는 설정, 배포 후 새로 생기는 **미관측 계열**, 실무 요구에 맞춘 지표를 168개 이상 설정으로 평가하고 산업 데이터셋을 공개했다(초록).
- 한계: 이번 조사에서 수치를 추출하지 않았다.
- **적합도 → 참고.** TimeSeriesBench의 "통합 모델·미관측 계열" 설정은 서버 수천 대 환경과 같은 문제다. 내부 평가 설계에 차용한다.

### E. 장애·고장·아웃티지 예측

**PRED-E-01** · 노드 장애 예측
- Lin et al., *Predicting Node Failure in Cloud Service Systems*(MING), **ESEC/FSE 2018**, DOI 10.1145/3236024.3236060, 동료심사, V2
- Li et al., *Predicting Node Failures in an Ultra-Large-Scale Cloud Computing Platform: an AIOps Solution*(Alibaba), ACM TOSEM 2020, DOI 10.1145/3385187, 동료심사, V2
- **게재처 정정:** 요청서의 "MING, ATC'18"은 오기다. MING은 ESEC/FSE'18이고, ATC'18 논문은 디스크 오류 예측(CDEF, E-02)이다.
- 메커니즘:
  - MING: 시간 신호는 LSTM, 공간 신호는 랜덤포레스트로 처리한다. 두 결과를 랭킹 모델에 넣어 장애 경향 순위를 매기고, 비용 민감 함수로 최적 임계를 정한다. 불균형 데이터에 대응하며 산업에 적용됐다(초록).
  - Li: Alibaba 노드 장애 예측을 구축한 경험을 보고한다. 모델 성능만으로는 부족하고 신뢰성·해석성·유지보수성·확장성·맥락 평가가 필요하다고 말한다.
- 입력 요구: 노드별 시간 메트릭·이벤트와 공간(랙·클러스터) 정보. 과거 장애 라벨이 다수 필요하다.
- 보고 성능: MING은 Precision@k(k=10~500)와 비용비(CostRatio=2, 정밀도 우선)를 썼다(V3 검색 요약). 수치는 미확인.
- **적합도 → 참고(단계 4).** 대량 서버를 장애 경향 순위로 나열하고 비용 민감 임계로 자르는 설계는 폴스타 서버 수천 대에 맞는다. 다만 대규모 클라우드 수준의 장애 라벨 양이 전제다.

**PRED-E-02** · Xu et al. · *Improving Service Availability of Cloud Systems by Predicting Disk Error* (CDEF) · USENIX ATC 2018 · 동료심사 · **V1**(도입부)
- 메커니즘: 디스크 오류(섹터·지연 오류)를 **gray failure**로 보고 SMART와 시스템 수준 신호(Windows 이벤트 등)를 결합한다. 비용 민감 랭킹 모델로 위험 디스크 순위를 매긴다. 오탐 비용(VM 이전 비용)이 크다는 점을 명시했다(원문).
- 입력 요구: 디스크 SMART와 호스트 이벤트, 과거 오류 라벨.
- 보고 성능: 이번에 추출하지 않았다.
- **적합도 → 참고.** "오탐 비용이 크니 정밀도 우선 순위화"라는 원칙을 노이즈 게이트 연계에 차용한다. SMART 수집 여부는 확인이 필요하다.

**PRED-E-03** · Chen et al. · *Outage Prediction and Diagnosis for Cloud Service Systems* (AirAlert) · WWW 2019 · DOI 10.1145/3308558.3313501 · 동료심사 · **V1**
- 메커니즘: 클라우드 전체의 **알람 신호**를 모아 FCI 알고리즘으로 신호와 아웃티지 간 베이지안 네트워크를 추론한다. 관련 신호를 골라 강건한 그래디언트 부스팅 트리로 아웃티지를 예측한다.
- 입력 요구: 알람 신호의 시계열. 원문 데이터는 수십 개 데이터센터의 1년치를 **시간 단위 샘플(24h×365d)**로 만든 것이다. 아웃티지 라벨이 필요하다.
- 보고 성능(V1, 컴포넌트 수준 아웃티지 3종의 F1):

  | 방법 | 아웃티지 1 | 아웃티지 2 | 아웃티지 3 |
  |---|---|---|---|
  | AirAlert Related | 79.01 | 77.25 | 76.80 |
  | 단순 스파이크 규칙 | 76.28 | 70.58 | 76.18 |

  - **단순 규칙이 1·3번에서 제안 기법에 0.6~2.7점 차이로 근접했다.** 서비스 수준 아웃티지에서는 전반적으로 정밀도·재현율이 낮다(원문 서술).
- **적합도 → 참고(단계 4).** 시간 단위 알람 집계로 예측이 가능하다는 드문 실증이다. 동시에 단순 규칙 기준선이 필수라는 근거다.

**PRED-E-04** · Zhao et al. · *Real-Time Incident Prediction for Online Service Systems* (eWarn) · ESEC/FSE 2020 · DOI 10.1145/3368089.3409672 · 동료심사 · **V1**
- 메커니즘: 알람의 텍스트 특징(LDA 토픽)과 통계 특징(업무시간 여부 등)을 뽑는다. 다중 인스턴스 학습으로 무관한 알람의 영향을 줄이고, 분류 모델과 **LIME 설명 보고서**를 만든다. 저자에 China EverBright Bank 소속이 있다.
- 입력 요구: 알람 이력과 사건(incident) 라벨.
  - 시스템 11개 × 3년치. 시스템당 사건 26~227건, 알람 6,766~127,619건(원문 Table 3).
  - 관측창 w=60분, 10분마다 예측, **리드타임 10분**, 인스턴스 10분.
- 보고 성능(V1): **평균 F1 0.82.** 비교 기법은 AirAlert 0.51, 로그 기반 기법 변형 0.60, 은행의 기존 방식(FP-Growth 연관규칙) 0.10이다. 최고 F1은 0.98이고, 대형 상업은행 2곳에 적용했다.
- 한계(중요): 정답은 **창 단위**다. 한 사건에 겹치는 양성 창이 t_p/Δt개 생기므로 PA와 비슷한 부풀림 위험이 있다. 리드타임 10분은 선제 조치에 짧다. 비교 기법 구현은 저자가 재현했다.
- **적합도 → 채택(단계 4 1순위 설계 참조).** 금융권이고 알람 기반이며 해석을 제공한다. 폴스타 알람 이력·노이즈 게이트와 바로 연결된다. 평가는 §4의 이벤트 단위·리드타임 분포로 다시 설계한다.

**PRED-E-05** · 디스크 고장 예측과 생존 분석
- Lu et al., *Making Disk Failure Predictions SMARTer!*, USENIX FAST 2020, 동료심사, **V1**
- Botezatu et al., *Predicting Disk Replacement towards Reliable Data Centers*, KDD 2016, DOI 10.1145/2939672.2939699, 동료심사, V2
- Mallik et al., *A survivability analysis of enterprise hard drives incorporating the impact of workload*, Frontiers in Computer Science 2024, DOI 10.3389/fcomp.2024.1400943, 동료심사, V2
- 메커니즘:
  - Lu: **SMART에 디스크·서버 성능 지표와 위치(랙·사이트) 정보를 더하면** 예측이 크게 좋아진다. CNN-LSTM 등을 썼고, SMART는 **시간 단위**로 수집했다(원문).
  - Botezatu: 교체와 상관된 SMART 파라미터를 통계적으로 자동 선택한다.
  - Mallik: 총 읽기·쓰기량과 평균 접근률을 공변량으로 한 생존확률 모델이다.
- 보고 성능:
  - Lu(V1 초록): 디스크 380,000개·64개 사이트·2개월 데이터에서 **10일 예측 지평 F-measure 0.95, MCC 0.95.**
  - Botezatu(초록 주장): 디스크 30,000개·17개월 데이터에서 10~15일 전 교체 필요를 **98% 정확도**로 예측.
- 한계: 대규모 디스크 모집단과 고장 라벨이 필요하다. 모집단 불균형 때문에 정확도(accuracy)는 과장되기 쉽다.
- **적합도 → 참고(SMART 확보가 전제).** 생존분석(위험함수) 틀은 "임계 도달까지의 시간" 추정과 같은 문제다. Lu의 성능·위치 결합은 폴스타 디스크 I/O 지표를 활용할 근거다.

**PRED-E-06** · 로그 기반 고장 예측
- Das et al., *Desh: Deep Learning for System Health Prediction of Lead Times to Failure in HPC*, HPDC 2018, DOI 10.1145/3208040.3208051, 동료심사, V2
- Hadadi et al., *Systematic Evaluation of Deep Learning Models for Log-based Failure Prediction*, Empirical Software Engineering 29:105(2024), DOI 10.1007/s10664-024-10501-4, 동료심사, V2
- 메커니즘:
  - Desh: 3단계 학습 LSTM으로 **장애로 이어지는 로그 이벤트 사슬**을 식별하고 리드타임을 추정한다.
  - Hadadi: RNN·CNN·Transformer와 로그 임베딩 조합을 합성 데이터셋 360종으로 체계 평가했다. F1 최고는 CNN 인코더 + Logkey2vec이다(V3 요약).
- 한계: HPC 로그 또는 합성 데이터라 금융권 APM 이벤트로 일반화할 근거가 약하다.
- **적합도 → 참고.** 제니퍼 이벤트·폴스타 이벤트 텍스트를 템플릿화한 뒤(H-01) 선행 이벤트 사슬을 통계적으로 찾는 수준까지가 현실적이다.

**PRED-E-07** · Huang et al. · *Gray Failure: The Achilles' Heel of Cloud-Scale Systems* · HotOS 2017 · DOI 10.1145/3102980.3103005 · 동료심사(워크숍) · **V2**
- 메커니즘(개념): 대형 가용성 붕괴의 상당수가 fail-stop이 아닌 **gray failure**이고, 핵심 특징은 **differential observability**다. 시스템의 장애 탐지기는 문제를 못 보는데 애플리케이션은 이미 피해를 입는 상태를 말한다.
- **적합도 → 채택(설계 원칙).** 선행 지표는 인프라 관점(폴스타)과 애플리케이션 관점(제니퍼 응답시간·에러율)의 **관측 불일치**에서 찾아야 한다. 단일 원천 탐지보다 교차 원천 불일치 신호가 우선이다.

### F. 용량·자원 고갈 예측(임계 도달 ETA)

**PRED-F-01** · Taylor & Letham · *Forecasting at Scale* (Prophet) · The American Statistician, 2018 · DOI 10.1080/00031305.2017.1380080 · 동료심사 · **V2**
- 메커니즘: 해석 가능한 파라미터를 가진 모듈형 회귀(추세 + 계절 + 휴일 효과)다. 분석가가 개입하는 성능 분석과 이상 예측 표시 흐름을 갖췄다(초록).
- 입력 요구: 단변량, 달력(휴일) 정의. CPU.
- 보고 성능: 벤치마크 수치 미확인. GIFT-Eval·BOOM에는 포함되지 않았다.
- 공개 코드: facebook/prophet(MIT, 2026-08 활동).
- **적합도 → 참고.** **급여일·월말·월초·배치일을 휴일·회귀자로 명시하는 방식**은 금융권 계절성에 맞는다. ETA 엔진으로 단독 채택하기 전에 F-02 기준선·파운데이션 모델과 내부 백테스트로 비교해야 한다.

**PRED-F-02** · Hyndman & Khandakar · *Automatic Time Series Forecasting: The forecast Package for R* · Journal of Statistical Software 27(3), 2008 · DOI 10.18637/jss.v027.i03 · 동료심사 · **V2**
- 구현: Nixtla statsforecast(Apache-2.0)
- 메커니즘: 정보기준으로 ARIMA·ETS 모형을 자동 선택한다.
- 외부 벤치마크 수치(V1, 타 논문 표):
  - GIFT-Eval(Chronos-2 보고서 Table 4, Seasonal Naive 대비 skill score): AutoARIMA는 WQL 8.8%, MASE −7.4%다. **AutoETS는 WQL −648.9%로 일부 계열에서 파국적으로 실패했다.**
  - BOOM(Toto 논문 Table 2): Auto-ARIMA MASE 0.824·CRPS 0.736, Auto-ETS 0.842·1.975, Auto-Theta 1.123·1.018.
- 한계: 다중 계절성(일·주)과 달력 효과를 직접 다루지 못한다. ETS는 이상치에 취약하다.
- **적합도 → 채택(의무 기준선).** 파운데이션 모델이나 딥 예측 모델은 이 기준선과 Seasonal Naive를 내부 데이터로 이겨야 채택한다.

**PRED-F-03** · 딥 예측 모델 계열 · 모두 동료심사 · **V2**

  | 모델 | 게재처·식별자 | 메커니즘 | 초록 주장 |
  |---|---|---|---|
  | N-BEATS(Oreshkin et al.) | ICLR 2020, arXiv 1905.10437 | 잔차 연결 FC 스택 | M4 우승작 대비 +3%, 통계 기준 대비 +11% |
  | N-HiTS(Challu et al.) | AAAI 2023, DOI 10.1609/aaai.v37i6.25854 | 계층 보간·다중 샘플링 | 최신 Transformer 대비 정확도 약 20% 향상, 계산 50배 절감 |
  | PatchTST(Nie et al.) | ICLR 2023, arXiv 2211.14730 | 패치 + 채널 독립 | — |
  | DLinear(Zeng et al.) | AAAI 2023, DOI 10.1609/aaai.v37i9.26317 | 1층 선형 모델 | 9개 데이터셋 **모든 경우**에서 Transformer LTSF 모델보다 우수 |

- 외부 수치(V1): BOOMLET에서 DLinear MASE 0.823, Toto 0.617(BOOM 논문 Table 2).
- 한계: 데이터셋별 학습이 필요하다. 수천 계열이면 글로벌 모델 학습과 운영 부담이 생긴다.
- 공개 코드: neuralforecast(Apache-2.0), LTSF-Linear(Apache-2.0), PatchTST(Apache-2.0).
- **적합도 → 참고.** DLinear는 CPU로 학습할 수 있는 저비용 비교군으로 둘 수 있다. 딥 모델 자체는 파운데이션 모델 제로샷보다 운영 이점이 없다.

**PRED-F-04** · 순응 예측(conformal) 구간
- Xu & Xie, *Conformal prediction interval for dynamic time-series*(EnbPI), ICML 2021, PMLR 139, 동료심사, V2
- Gibbs & Candès, *Adaptive Conformal Inference Under Distribution Shift*(ACI), NeurIPS 2021, 동료심사, V2
- 메커니즘:
  - EnbPI: 부트스트랩 앙상블을 감싸 교환가능성 가정 없이 순차 예측구간을 만든다. 재학습 없이 확장한다.
  - ACI: 분포가 변할 때 신뢰수준 파라미터 하나를 온라인으로 갱신해 장기 커버리지를 보장한다.
- 입력 요구: 임의의 점·분위수 예측기 출력. CPU. 결정적 갱신 규칙.
- 공개 코드: hamrel-cxu/EnbPI(MIT).
- **적합도 → 채택(단계 1~3 공통 불확실성 계층).** ETA를 "P(72시간 안에 디스크 90% 도달)"처럼 구간·확률로 내보내 규칙 엔진의 결정 입력으로 쓴다. 계획 변경 뒤 분포 이동에는 ACI 방식의 적응 갱신이 맞다.

**PRED-F-05** · 자원 고갈 예측의 운영 근거
- Cortez et al., *Resource Central: Understanding and Predicting Workloads for Improved Resource Management in Large Cloud Platforms*, SOSP 2017, DOI 10.1145/3132747.3132772, 동료심사, V2
- Cotroneo et al., *A survey of software aging and rejuvenation studies*, ACM JETC 2014, DOI 10.1145/2539117, 동료심사, V2
- 메커니즘:
  - Resource Central: Azure VM 워크로드에서 **과거 행동이 미래를 잘 예측한다**는 관찰에 기반한다. 오프라인 학습·온라인 예측을 VM 스케줄러에 적용해 과다할당을 하면서도 물리 자원 고갈을 막았다(초록).
  - Cotroneo: 장시간 실행 소프트웨어의 노화(성능 저하·고장률 증가)를 통계적으로 예측하는 접근과 재기동(rejuvenation) 계획 연구를 정리했다.
- **적합도 → 참고.** 제니퍼의 JVM 힙·스레드풀 고갈 ETA는 소프트웨어 노화 추세 추정 문제로 볼 수 있다. 다만 최신 APM 환경의 동료심사 실증은 공백이다(§3).

### G. 시계열 파운데이션 모델(2024~2026)

> 라이선스는 Hugging Face API의 `cardData.license`(2026-09-17 조회)와 공식 README·LICENSE 페이지로 확인했다. 파라미터 수는 HF safetensors 총량이다.

**PRED-G-01** · Ansari et al. · *Chronos: Learning the Language of Time Series* · TMLR(10/2024, OpenReview) · arXiv 2403.07815 · 동료심사(저널) · **V2**
- 함께 본 자료: Chronos-Bolt(HF 모델카드, 산업 자료, V2)
- 메커니즘:
  - Chronos: 스케일링과 양자화로 값을 토큰화해 T5 계열(20M~710M)을 교차엔트로피로 학습한다. 42개 데이터셋 벤치마크에서 제로샷 성능이 경쟁력 있다(초록).
  - Bolt(모델카드 인용): base 205M 기준 "up to 250 times faster and 20 times more memory-efficient than the original Chronos". CPU 추론을 지원하고, 모델카드에 Chronos-2가 후속으로 표시돼 있다.
- 독립 재평가(V1): TSB-AD 단변량 VUS-PR 0.27(Chronos), 점 이상 3위.
- 라이선스: chronos-t5-base·chronos-bolt-base 모두 **Apache-2.0**. 코드 amazon-science/chronos-forecasting(Apache-2.0).
- **적합도 → 참고(G-02로 대체).**

**PRED-G-02** · Ansari et al. · *Chronos-2: From Univariate to Universal Forecasting* · arXiv 2510.15821(2025, 기술보고서) · preprint · **V1**
- 메커니즘: 120M 인코더 전용 모델이다. **그룹 어텐션**으로 관련 계열·다변량·공변량 간 in-context learning을 하고, 분위수 예측을 낸다. 실데이터와 대규모 합성 데이터로 학습했다.
- 보고 성능(V1, GIFT-Eval 97과제/55데이터셋, Table 4):

  | 모델 | WQL 승률 | WQL skill | MASE 승률 | MASE skill |
  |---|---|---|---|---|
  | **Chronos-2** | 81.9% | 51.4% | 83.8% | 30.2% |
  | TimesFM-2.5 | 77.5% | 51.0% | 77.7% | 29.5% |
  | TiRex | 76.5% | 50.2% | — | — |
  | Toto-1.0 | 67.4% | 48.6% | — | — |
  | Moirai-2.0 | 64.4% | 48.4% | — | — |
  | Sundial | 49.1% | 44.1% | — | — |
  | AutoARIMA | 21.8% | 8.8% | 24.4% | −7.4% |
  | Seasonal Naive | 16.6% | 0.0% | — | — |

  - 28M 소형 모델은 GIFT-Eval skill이 기본 모델보다 약 1%p 낮지만 **약 2배 빠르다.** 저자는 "CPU-only settings"에 적합하다고 명시했다.
  - 처리량은 A10G GPU 1장에서 초당 300개 이상 예측(초록, V2).
- 한계: preprint다. 사전학습 코퍼스가 GIFT-Eval 일부 데이터셋의 **훈련 구간과 부분 중첩**한다(저자 명시). 관측 데이터(BOOM) 결과는 이번 확인 범위에 없다.
- 라이선스: amazon/chronos-2는 **Apache-2.0**(119.5M).
- **적합도 → 채택 후보(단계 3 1순위).** 라이선스가 허용적이고, CPU 소형판이 있고, 공변량(달력·배치 플래그)을 넣을 수 있다. 분위수를 직접 출력하므로 규칙 엔진 입력으로 쓰기 쉽다. 반입 전에 내부 백테스트가 필요하다.

**PRED-G-03** · Das et al. · *A decoder-only foundation model for time-series forecasting* (TimesFM) · ICML 2024, PMLR 235:10148–10167 · 동료심사 · **V2**
- 후속 버전(공식 README, 산업 자료, V2):
  - **TimesFM 2.5**(2025-09-15): 200M, 컨텍스트 최대 16k, 선택형 30M 분위수 헤드, XReg 공변량(2025-10). 코드·가중치 모두 Apache-2.0.
  - **TimesFM 3.0**(2026-08): 330M, 네이티브 다변량과 동적 공변량. **가중치는 "restricted to non-commercial, non-production use"로 상업·운영 사용 불가**다.
- 메커니즘: 패치 단위 디코더 전용 어텐션 모델이다.
- 독립 재평가(V1): TSB-AD 단변량 VUS-PR 0.30. **점 이상에서는 1위**다.
- 라이선스(HF): timesfm-2.5-200m-pytorch **Apache-2.0**(231.3M, 헤드 포함), timesfm-2.0-500m Apache-2.0.
- **적합도 → 채택 후보(2.5, 단계 3) / 회피(3.0).** 3.0의 비상업 전환은 **버전 교체만으로 라이선스 위험이 생긴다**는 사례다. 반입할 버전의 가중치 해시와 라이선스를 고정해야 한다.

**PRED-G-04** · Moirai 계열(Salesforce)
- Woo et al., *Unified Training of Universal Time Series Forecasting Transformers*(Moirai), ICML 2024, PMLR 235:53140–53164, V2
- Liu et al., *Moirai-MoE*, ICML 2025, PMLR 267:38940–38962, V2
- Liu et al., *Moirai 2.0: When Less Is More for Time Series Forecasting*, arXiv 2511.11698(preprint), V2
- 메커니즘:
  - Moirai: 마스크드 인코더에 any-variate 어텐션을 쓰고 LOTSA 27B 관측으로 학습했다.
  - MoE: 빈도별 투영을 희소 전문가 혼합으로 대체했다.
  - 2.0: 디코더 전용·분위수·다중 토큰 예측을 쓰고, 계열 36M개로 학습했다. 1.0-Large보다 2배 빠르고 30배 작다(초록).
- 라이선스(HF): moirai-1.1-R-base, moirai-moe-1.0-R-base, **moirai-2.0-R-small(11.4M) 모두 CC-BY-NC-4.0.** 코드 uni2ts는 Apache-2.0이지만 가중치가 비상업이다.
- **적합도 → 회피(라이선스).** 금융기관 운영은 상업적 이용에 해당할 소지가 크다.

**PRED-G-05** · Goswami et al. · *MOMENT: A Family of Open Time-series Foundation Models* · ICML 2024(arXiv 2402.03885 comment) · 동료심사 · **V2**
- 메커니즘: Time series Pile로 사전학습한 마스크 재구성 모델이다. 예측·분류·**이상탐지**·결측대체를 제한된 감독으로 수행한다(초록).
- 독립 재평가(V1): TSB-AD 단변량 VUS-PR은 MOMENT(FT) 0.39(5위), **MOMENT(ZS) 0.38(6위).** 파운데이션 모델 중 최고지만 **사전학습에 이상탐지 데이터셋이 포함돼 오염 가능성**이 있다(TSB-AD 명시).
- 라이선스: AutonLab/MOMENT-1-large는 **MIT**(346M).
- **적합도 → 참고(단계 3 탐지 보조 후보).** 재구성 기반 제로샷 이상 점수를 낼 수 있다. 오염 문제 때문에 내부 데이터로만 판정한다.

**PRED-G-06** · Datadog Toto 계열
- Cohen et al., *This Time is Different: An Observability Perspective on Time Series Foundation Models*(Toto 1.0 + BOOM), arXiv 2505.14766(v2 2025-11), preprint, **V1**
- Cohen et al., *Toto: Time Series Optimized Transformer for Observability*, arXiv 2407.07874, preprint, V2
- Khwaja et al., *Toto 2.0: Time Series Forecasting Enters the Scaling Era*, arXiv 2605.20119(2026-05), preprint, V2
- 메커니즘: 151M 디코더 전용 모델에 다변량 관측 데이터용 구조를 더했다. 사전학습 코퍼스는 관측 데이터·공개 데이터·합성 데이터로, 기존 파운데이션 모델보다 4~10배 크다.
- BOOM 벤치마크: 실계열 2,807개·관측치 3.5억 개이고, 전부 Datadog 내부 텔레메트리다. **인프라 지표(CPU·메모리 등) 비중 34.4%**(원문 표). 샘플링 빈도가 계열마다 크게 다르다(원문).
- 보고 성능(V1, BOOM Table 2, Seasonal Naive 정규화):

  | 모델 | MASE | CRPS |
  |---|---|---|
  | **Toto** | **0.617** | **0.375** |
  | Moirai-Base | 0.710 | 0.428 |
  | TimesFM-2.0 | 0.725 | 0.447 |
  | Chronos-Bolt-Base | 0.726 | 0.451 |
  | Timer | 0.796 | 0.639 |
  | Auto-ARIMA | 0.824 | 0.736 |
  | Auto-ETS | 0.842 | 1.975 |
  | **Time-MoE-Base** | **0.881** | 0.643 |

  - **관측 데이터에서는 Time-MoE의 MASE가 Auto-ARIMA보다 나쁘다.** 파운데이션 모델이면 모두 통계 기준선을 이기는 것은 아니다.
  - GIFT-Eval(Table 3): Toto MASE 0.673, CRPS 0.437.
- Toto 2.0: 4M~2.5B 모델 5종이고 BOOM·GIFT-Eval·TIME에서 SOTA를 주장한다(초록, 미검증 preprint).
- 한계: preprint다. BOOM 제작사와 모델 제작사가 같아 **자사 벤치마크 편향 가능성**이 있다. CPU 추론 비용 수치는 미확인이다.
- 라이선스(HF): Toto-Open-Base-1.0(151.3M)·Toto-2.0-2.5B 모두 **Apache-2.0**. 코드 DataDog/toto(Apache-2.0, 2026-09 활동).
- **적합도 → 채택 후보(단계 3, 관측 데이터 특화 비교군).** 도메인이 가장 가깝다. 소형(Toto 2.0 4M~22M급)의 CPU 지연을 내부에서 측정한 뒤 Chronos-2 소형과 겨룬다.

**PRED-G-07** · Auer et al. · *TiRex: Zero-Shot Forecasting Across Long and Short Horizons with Enhanced In-Context Learning* · NeurIPS 2025(proceedings 확인) · arXiv 2505.23719 · 동료심사 · **V1**
- 메커니즘: **35M xLSTM** 기반 제로샷 예측 모델로, 상태 추적으로 단기·장기를 모두 다룬다.
- 보고 성능(V1): GIFT-Eval 제로샷 **CRPS 0.411(시드 6개 ±0.002).** 저자 기준으로 Chronos-Bolt-Base(200M)와 TimesFM-2.0(500M)을 앞선다. 실험은 A40 GPU에서 했고, 감사의 글에 CPU 효율화 작업이 언급된다.
- 라이선스(V2, LICENSE 원문): **NXAI Community License.** 연매출 1억 유로를 넘는 라이선시가 상용 제품·서비스에 넣으려면 **별도 상용 라이선스**가 필요하고, "Built with technology from NXAI" 표기 의무가 있다.
- **적합도 → 회피(라이선스).** 국내 대형 금융기관은 매출 기준을 넘을 가능성이 높다. 성능·크기로는 매력적이므로 법무 검토가 끝나면 재검토한다.

**PRED-G-08** · Ekambaram et al. · *Tiny Time Mixers (TTMs): Fast Pre-trained Models for Enhanced Zero/Few-Shot Forecasting of Multivariate Time Series* · NeurIPS 2024(arXiv 2401.03955 comment) · 동료심사 · **V1**(초록·CPU 기술)
- 메커니즘: TSMixer 기반 소형 사전학습 모델(**1M 파라미터부터**)이다. 공개 데이터만으로 학습했다. "lightweight and can be executed even on CPU-only machines"(원문).
- 라이선스(HF): ibm-granite/granite-timeseries-ttm-r2는 **Apache-2.0**(약 0.8M). 코드 granite-tsfm(Apache-2.0).
- **적합도 → 채택 후보(단계 3 CPU 하한 기준 모델).** 가장 작은 비용으로 제로샷 대역을 계산하는 기준점이다. 다만 2025~2026 리더보드에서는 Chronos-2·TimesFM-2.5보다 뒤처질 가능성이 높으니 내부 비교가 필요하다.

**PRED-G-09** · thuml·MoE 대형 계열 · 모두 동료심사 · **V2**
- Liu et al., *Timer*, ICML 2024, PMLR 235:32369–32399
- Liu et al., *Sundial*, ICML 2025 Oral, PMLR v267
- Shi et al., *Time-MoE*, ICLR 2025
- 메커니즘:
  - Timer: GPT형 다음 토큰 생성 모델로 예측·결측대체·이상탐지를 생성 과제로 통합했다.
  - Sundial: flow-matching 기반 TimeFlow Loss로 토큰화 없이 연속값을 사전학습했다. TimeBench 1조 점, 제로샷 예측 수 ms(초록).
  - Time-MoE: 희소 MoE로 2.4B까지 키웠고 Time-300B로 학습했다.
- 외부 수치(V1): GIFT-Eval에서 Sundial WQL 승률 49.1%·skill 44.1%(Chronos-2 보고서). BOOM에서 Timer MASE 0.796, Time-MoE-Base 0.881(Auto-ARIMA 0.824보다 나쁨).
- 라이선스(HF): sundial-base-128m, timer-base-84m, TimeMoE-50M(총 113M) 모두 Apache-2.0.
- **적합도 → 참고.** 라이선스는 허용적이지만 관측 데이터 근거가 약하다(Time-MoE는 BOOM에서 통계 기준선보다 열세).

**PRED-G-10** · 초기·폐쇄형 모델
- Rasul et al., *Lag-Llama: Towards Foundation Models for Probabilistic Time Series Forecasting*, arXiv 2310.08278, preprint(게재처 미확인), V2
- Garza et al., *TimeGPT-1*, arXiv 2310.03589, preprint, V2
- 메커니즘: Lag-Llama는 지연값을 공변량으로 쓰는 디코더 전용 확률 예측 모델이다. TimeGPT는 Nixtla의 사전학습 예측 모델이다.
- 독립 재평가(V1): TSB-AD 단변량 VUS-PR에서 Lag-Llama 0.27.
- 라이선스: Lag-Llama는 Apache-2.0(HF, 2.45M). TimeGPT는 가중치를 공개하지 않았고, 공개 저장소 Nixtla/nixtla는 SDK다(SPDX 미판정). 자체 호스팅 제공 여부는 미검증이다.
- **적합도 → 회피.** Lag-Llama는 세대가 뒤처졌고, TimeGPT는 폐쇄망에 반입할 수 없는 구조로 보인다(추정).

**PRED-G-11** · Aksu et al. · *GIFT-Eval: A Benchmark For General Time Series Forecasting Model Evaluation* · NeurIPS 2024 **Workshop**(Time Series in the Age of Large Models, 포스터) · arXiv 2410.10393 · 워크숍 동료심사 · **V2**
- 메커니즘: 일반 도메인 예측 벤치마크이고 MASE·CRPS를 Seasonal Naive로 정규화해 집계한다. 공개 리더보드(HF Space)를 운영한다.
- 한계: **메인 트랙이 아닌 워크숍**이다. 여러 모델의 사전학습 데이터가 부분 누수됐다는 보고가 있다(BOOM 논문 인용, Chronos-2 자체 명시). 관측·금융 IT 데이터 비중이 낮다.
- 산업 동향(V3): 2026-09 리더보드 상위는 Toto 2.0 앙상블·미세조정과 IBM PatchTST-FM-r2 등으로 매달 바뀐다.
- **적합도 → 참고.** 외부 순위는 후보 선별에만 쓰고 채택 판정은 내부 백테스트로 한다.

**PRED-G-12** · 파운데이션 모델 제로샷 **이상탐지** 근거(종합)
- Uray et al., *Exploring Zero-Shot Foundation Models for Multivariate Time Series Anomaly Detection*, arXiv 2607.12454(2026-07). EUROCAST 2026 LNCS 게재 예정으로 표기됐고, 제출본은 동료심사 전이다. preprint, V2.
- TSB-AD(D-04) 파운데이션 모델 분석, V1.
- 참고: *TimeRCD*(arXiv 2509.21190)는 ICML 2026 포스터 페이지가 있으나 **arXiv v5에 "withdrawn" 표기**가 있다(2026-09-17 확인). 상태가 불안정해 근거에서 제외했다.
- 발견:
  - Uray: TimesFM을 산업 다변량 이상탐지에 제로샷으로 쓰면 **모델이 시간 동역학을 너무 잘 따라가 지속 이상 구간에서도 오차가 낮다.** 그래서 지속 이상을 정상과 구분하지 못하지만, **분포 이동(변화점) 탐지에는 유망**하다(초록).
  - TSB-AD: 파운데이션 모델은 점 이상에 강하고 구간 이상에 약하다.
- **적합도 → 참고(설계 결론).** 파운데이션 모델 예측 잔차는 "점 이상·변화점 보조 신호"로만 쓴다. 지속 열화(메모리 누수·디스크 증가)는 추세·ETA 규칙(F 영역)으로 잡는다.

**PRED-G-13** · IBM · Granite Time Series **PatchTST-FM-r2** 공개(HF 블로그, 2026-09-09) · 산업 자료 · **V3**(블로그), 라이선스 V2
- 내용: 약 385M 모델이고 Apache-2.0과 OpenMDW-1.0 이중 라이선스다(HF API는 openmdw-1.0 표기, 384.6M). 블로그는 2026-09-08 기준 GIFT-Eval에서 제로샷·재현 가능 모델 중 CRPS·MASE 모두 2위, 허용 라이선스 모델 중 1위라고 주장한다.
- 한계: 공개 8일차라 독립 검증이 없다. CPU 추론 정보도 없다.
- **적합도 → 참고(단계 3 후속 후보 감시).**

#### G 영역 반입 후보 요약표

| 모델 | 파라미터(HF) | 가중치 라이선스 | 상업·운영 사용 | CPU 근거 | 동료심사 |
|---|---|---|---|---|---|
| Chronos-2 | 119.5M(소형 28M은 보고서 기준) | Apache-2.0 | 가능 | 28M판을 CPU-only 권장(V1) | 아니오(보고서) |
| Chronos-Bolt-Base | 205.3M | Apache-2.0 | 가능 | CPU 지원(모델카드) | 원 Chronos는 TMLR |
| TimesFM-2.5 | 231.3M(헤드 포함) | Apache-2.0 | 가능 | 미확인 | 1.0은 ICML'24 |
| TimesFM-3.0 | 330M(README) | 비상업·비운영 | **불가** | MLX 백엔드 | 아니오 |
| Toto 1.0 / 2.0 | 151.3M / 4M~2.5B | Apache-2.0 | 가능 | 미확인 | 아니오 |
| TTM-R2 | ~0.8M | Apache-2.0 | 가능 | CPU-only 명시(V1) | NeurIPS'24 |
| MOMENT-1-large | 346.4M | MIT | 가능 | 미확인 | ICML'24 |
| Sundial-base | 128.3M | Apache-2.0 | 가능 | 미확인 | ICML'25 |
| Time-MoE-50M | 113.4M(총) | Apache-2.0 | 가능 | 미확인 | ICLR'25 |
| Lag-Llama | 2.45M | Apache-2.0 | 가능 | 미확인 | 미확인 |
| Moirai 1.1 / MoE / 2.0 | 91.4M / 935M / 11.4M | CC-BY-NC-4.0 | **불가** | — | ICML'24·'25 / 2.0은 preprint |
| TiRex | 35M(논문) | NXAI Community | **매출 1억 유로 초과 시 상용 라이선스 필요** | CPU 효율화 언급 | NeurIPS'25 |
| PatchTST-FM-r2 | 384.6M | Apache-2.0 / OpenMDW | 가능(블로그) | 미확인 | 아니오 |

### H. 로그 기반 이상탐지(폴스타·제니퍼 이벤트 텍스트 적용성)

**PRED-H-01** · He et al. · *Drain: An Online Log Parsing Approach with Fixed Depth Tree* · IEEE ICWS 2017 · DOI 10.1109/ICWS.2017.13 · 동료심사 · **V2**
- 메커니즘: 고정 깊이 파스 트리와 규칙으로 로그를 스트리밍 템플릿화한다.
- 보고 성능(초록 주장): 실데이터 5종·1,000만 건 이상에서 4종 최고 정확도, 나머지 1종은 비슷한 수준이다. 기존 온라인 파서보다 실행시간이 51.85~81.47% 개선됐다.
- 공개 코드: Drain3(logpai/IBM, SPDX 미판정 — 반입 전 LICENSE 원문 확인 필요).
- **적합도 → 채택(전처리, 결정적).** 폴스타 알람 메시지와 제니퍼 이벤트를 템플릿 ID로 바꿔 E-04(eWarn)류 특징의 입력으로 쓴다.

**PRED-H-02** · 딥러닝 로그 이상탐지
- Du et al., *DeepLog*, ACM CCS 2017, DOI 10.1145/3133956.3134015, 동료심사, V2
- Zhang et al., *Robust log-based anomaly detection on unstable log data*(LogRobust), ESEC/FSE 2019, DOI 10.1145/3338906.3338931, 동료심사, V2
- 메커니즘: DeepLog은 LSTM으로 다음 로그 키를 예측하고 온라인으로 갱신한다. LogRobust는 로그 이벤트 의미 벡터와 attention BiLSTM을 써서, 로깅 문장이 바뀌거나 처리 잡음이 있어도 강건하다(Microsoft 실서비스 평가).
- 한계: H-03 참조.
- **적합도 → 회피(운영).** LogRobust가 지적한 로그 불안정성(버전 변경)은 설계 요구사항으로 참고한다.

**PRED-H-03** · Le & Zhang · *Log-based Anomaly Detection with Deep Learning: How Far Are We?* · ICSE 2022 · DOI 10.1145/3510003.3510155 · 동료심사 · **V1**(초록)
- 메커니즘(비판): 딥러닝 모델 5종을 공개 로그 4종에서 재평가했다. 대부분 HDFS에서 F>0.9를 보고하지만, **학습 데이터 선택·그룹화·클래스 분포·잡음·조기탐지 능력이 결과를 크게 바꾸고** 모든 모델이 늘 잘 작동하지는 않는다. "문제는 아직 풀리지 않았다."
- **적합도 → 채택(평가 원칙).** 시계열의 D 영역과 같은 결론이다.

**PRED-H-04** · 로그 데이터·파서 평가
- Zhu et al., *Loghub: A Large Collection of System Log Datasets for AI-driven Log Analytics*, ISSRE 2023, DOI 10.1109/ISSRE59848.2023.00071, 동료심사, V2
- Jiang et al., *A Large-Scale Evaluation for Log Parsing Techniques: How Far Are We?*(Loghub-2.0), ISSTA 2024(arXiv 2308.10828 comment), 동료심사, V2
- **적합도 → 참고.** 내부 파서를 평가하는 방법(템플릿 정확도 등)을 차용한다. 공개 데이터 자체는 금융 도메인이 아니다.

**PRED-H-05** · LLM 로그 파싱(2024~2025)
- Jiang et al., *LILAC: Log Parsing using LLMs with Adaptive Parsing Cache*, FSE 2024(PACMSE, DOI 10.1145/3643733), 동료심사, V2
- Ma et al., *LLMParser*, ICSE 2024, DOI 10.1145/3597503.3639150, 동료심사, 메타 V2·내용 V3
- Beck et al., *System Log Parsing with Large Language Models: A Review*, arXiv 2504.04877(2025), preprint, V2
- 메커니즘:
  - LILAC: 계층 후보 샘플링으로 in-context 예시를 고르고, **적응형 파싱 캐시**에 LLM이 만든 템플릿을 저장·정제해 호출을 줄인다. 템플릿 정확도 평균 F1이 기존보다 69.5% 높고, LLM 질의 수를 수 자릿수 줄였다(초록).
  - LLMParser: Flan-T5·LLaMA-7B 등 소형 LLM을 미세조정해 16개 시스템에서 평균 파싱 정확도 96%(V3).
  - Beck: LLM 파싱 방법 29종을 검토하고 7종을 벤치마크했다(V3).
- **적합도 → 참고(오프라인 보강에 한정).** 결정 경로는 Drain 규칙과 캐시된 템플릿으로 두고, LLM은 처음 보는 메시지의 템플릿 후보 제안·해석에만 쓴다. LILAC의 캐시 구조가 "LLM 출력을 결정적 산출물로 굳히는" 설계 원칙과 맞는다. 과금 LLM 호출은 D-127 승인 게이트 대상이다.

### I. 운영 적용 사례·산업 자료

**PRED-I-01** · Hosseini et al. · *Greykite: Deploying Flexible Forecasting at Scale at LinkedIn* · KDD 2022 · DOI 10.1145/3534678.3539165 · 동료심사 · **V2**
- 메커니즘: Silverkite는 시간변화 성장·계절성·자기상관·**휴일**·회귀자를 해석 가능하게 모델링한다. 해상도는 시간 미만부터 분기까지 다루고, LinkedIn 20개 이상 용례(자원 계획·이상탐지·RCA)에 쓰였다(초록).
- 공개 코드: linkedin/greykite(BSD-2-Clause, 마지막 푸시 2025-02).
- **적합도 → 참고.** 달력·휴일 효과를 해석 가능하게 모델링한 운영 사례다. 유지보수 활동이 둔화된 점에 유의한다.

**PRED-I-02** · Chakraborty et al. · *Building an Automated and Self-Aware Anomaly Detection System* (Luminaire, Zillow) · IEEE BigData 2020(arXiv 2011.05047 comment) · 동료심사 · **V2**
- 메커니즘: 모델이 **자기 성능을 추적해 사람 개입 없이 모델을 바꾼다.** 동기로 "오탐 때문에 비활성화되거나 무시되는 모니터"를 명시했다(초록).
- 공개 코드: zillow/luminaire(Apache-2.0).
- **적합도 → 참고.** 알람 피로가 모니터 무력화로 이어진다는 운영 증언이다. 모델 자가 진단 지표(오탐률 추적)를 설계에 반영한다.

**PRED-I-03** · Zhu & Laptev · *Deep and Confident Prediction for Time Series at Uber* · IEEE ICDMW 2017 · DOI 10.1109/ICDMW.2017.19 · 동료심사(워크숍) · **V2**
- 메커니즘: 예측과 불확실성 추정을 함께 내는 종단간 베이지안 딥 모델이다. 수백만 지표의 실시간 이상탐지에 적용했다(초록).
- **적합도 → 참고.** "예측구간 이탈 = 이상"이라는 운영 패턴의 사례다. 구간 산출은 F-04(conformal)로 대체할 수 있다.

**PRED-I-04** · Zhao et al. · *Understanding and handling alert storm for online service systems* · ICSE-SEIP 2020 · DOI 10.1145/3377813.3381363 · 동료심사 · **V2**
- 메커니즘: 알람 폭풍을 처음 실증 연구하고, 폭풍 탐지와 대표 알람 요약을 제안했다.
- 보고 성능(초록 주장): 폭풍 탐지 F1 0.9 초과. 요약으로 검토할 알람 수를 **98% 넘게 줄였다.** 대형 상업은행(China EverBright Bank)에 적용했다.
- **적합도 → 채택(노이즈 게이트 연계 근거).** 선제 탐지 결과가 새 알람 폭풍을 만들지 않도록, 예측 신호도 게이트의 억제·요약 규칙을 거치게 한다.

**PRED-I-05** · 상용 관측 제품의 이상탐지 방식(공식 문서) · 산업 자료
- **Datadog Anomaly Monitor**(docs.datadoghq.com, V2):
  - basic: 지연 롤링 분위수, 계절성 없음.
  - agile: SARIMA 기반, 수준 이동에 빠르게 적응.
  - robust: 계절-추세 분해, 장기 이상에도 예측을 유지.
  - 필요 이력: 주간 계절성 **최소 3주**, 일간 최소 3일.
- **Datadog Watchdog**(블로그·문서, V3): APM의 지연·에러·요청량과 인프라에서 설정 없이 자동으로 이상을 탐지한다.
- **Grafana Cloud ML**(공식 문서 검색 요약, V3): 그룹 이상치 탐지에 DBSCAN, 또는 **롤링 24시간 중앙값 기반 MAD**를 쓴다. 예측은 일·주 계절성을 반영한다.
- **Elastic ML**(공식 문서, V2): 군집화, 여러 시계열 분해, **베이지안 분포 모델링**, 상관분석을 조합한다. 버킷 단위로 정규화된 이상 점수를 낸다.
- **적합도 → 채택(설계 패턴).** 대형 상용 제품의 주력 탐지는 **계절 분해·강건 통계·분위수·SARIMA**다. D 영역 실증과 같은 방향이다. "주간 계절성 3주 이상"은 단계 1 진입 조건의 산업 참고치다.

**PRED-I-06** · Microsoft · Azure AI Anomaly Detector 퇴역 공지(MicrosoftDocs/azure-ai-docs deprecation.md) · 산업 자료 · **V2**
- 내용: "Starting 20 September 2023 you won't be able to create new Anomaly Detector resources. The Anomaly Detector service is being retired 1 October 2026." Microsoft Fabric 또는 오픈소스 anomaly-detector로 이전하라고 권고한다.
- **적합도 → 참고.** SR-CNN(B-03) 기반 SaaS조차 퇴역한다. 외부 SaaS에 의존할 수 없는 폐쇄망의 자체 호스팅 원칙을 뒷받침하고, 오픈소스 구현(MIT)은 계속 활용할 수 있다.

**PRED-I-07** · DB 성능 이상 진단(DPM 연계)
- Yoon et al., *DBSherlock: A Performance Diagnostic Tool for Transactional Databases*, SIGMOD 2016, DOI 10.1145/2882903.2915218, 동료심사, V2
- Ma et al., *Diagnosing Root Causes of Intermittent Slow Queries in Cloud Databases*(iSQUAD), PVLDB 2020, DOI 10.14778/3389133.3389136, 동료심사, V2
- 메커니즘:
  - DBSherlock: OLTP 성능 저하는 단일 느린 쿼리보다 동시 경합 트랜잭션의 비선형 누적인 경우가 많고, DBA의 수동 진단은 고되다는 문제를 제기한 진단 도구다. 세부 기법은 이번 조사에서 미확인.
  - iSQUAD: 외부 원인의 **간헐 슬로우 쿼리**를 이상 추출 → 의존 정제 → TOPIC 군집 → 베이지안 사례 모델로 진단한다. DBA는 군집마다 한 번만 라벨링한다(초록).
- **적합도 → 참고.** DPM의 슬로우 SQL·대기 이벤트는 **예측보다 진단 문헌이 중심**이다. iSQUAD의 "군집당 1회 라벨"은 라벨이 희소한 환경에서 운영자 피드백 비용을 줄이는 설계로 차용할 수 있다.

---

## 2. 동료심사 / preprint / 산업 자료 3층 분리

| 층 | 항목 ID | 비고 |
|---|---|---|
| **① 동료심사(학회·저널)** | A-01, A-02, A-03(CSUR'24·'21), A-04 / B-01, B-02, B-03, B-04, B-05, B-06(RobustSTL), B-07(Truong) / C-01~C-05 / D-01~D-07 / E-01~E-07 / F-01~F-05 / G-01(TMLR), G-03(TimesFM 1.0, ICML'24), G-04(Moirai ICML'24·MoE ICML'25), G-05, G-07, G-08, G-09 / H-01~H-04, H-05(LILAC·LLMParser) / I-01~I-04, I-07 | G-11 GIFT-Eval은 **워크숍** 심사(약한 층). E-07 Gray Failure, I-03 Uber는 워크숍. |
| **② preprint(동료심사 미확인)** | A-03의 Boniol decade review / B-06의 S-H-ESD / B-07의 BOCPD(기술보고서) / **G-02 Chronos-2** / **G-06 Toto·BOOM·Toto 2.0** / G-04의 Moirai 2.0 / G-10 Lag-Llama·TimeGPT-1 / G-12 Uray(EUROCAST'26 게재 예정 표기) · TimeRCD(withdrawn 표기) / H-05의 Beck review | 2025~2026 파운데이션 모델 최신작 다수가 이 층이다. 채택 근거는 내부 재현으로 보강해야 한다. |
| **③ 산업 자료(공식 문서·모델카드·블로그)** | G-01 Chronos-Bolt 모델카드 / G-03 TimesFM 2.5·3.0 README / G-07 NXAI LICENSE / G-13 IBM PatchTST-FM-r2 블로그 / I-05 Datadog·Grafana·Elastic 문서 / I-06 Azure 퇴역 공지 / HF·GitHub 라이선스 메타데이터 | 라이선스·버전 정보는 이 층이 1차 출처다. 성능 주장은 V3로 취급한다. |

---

## 3. 조사에서 발견한 공백

1. **시간 단위 집계 메트릭 기반 서버 장애 예측의 동료심사 실증이 사실상 없다.**
   - 이상탐지 실증은 대부분 1분 KPI(Donut·SR-CNN의 KPI 데이터셋)나 분 단위 다변량(SMD)이다.
   - 시간 단위를 쓴 드문 예는 AirAlert(**알람** 신호의 시간 샘플)과 Lu FAST'20(**SMART** 시간 단위 수집)뿐이다.
   - 폴스타 `cmm_metric_stat_h` 같은 **시간 평균 CPU·메모리만으로** 서버 장애를 선제 예측한 논문은 찾지 못했다.
   - 추론(문헌 근거 없음): 시간 평균은 분 단위 스파이크를 평활화해 점 이상 신호를 지운다. 그러므로 시간 집계의 주 용도는 **추세·고갈 ETA와 일·주 패턴 이탈**이라고 보는 것이 보수적이다.
2. **리드타임 기반 이벤트 단위 평가 표준이 없다.**
   - Salfner(2010)의 시간 파라미터 정의 이후 합의된 지표가 없다.
   - eWarn은 겹치는 창 단위 F1을, MING은 Precision@k를, Lu는 고정 지평 F-measure를 쓴다. 논문 간 비교가 불가능하다.
   - 창 단위 채점은 PA와 같은 부풀림 구조를 가질 수 있는데, 이를 정량 비판한 논문은 찾지 못했다.
3. **금융권 달력 효과(월말·월초·급여일·배치)를 명시한 이상탐지·예측 벤치마크가 없다.**
   - 금융권 실증(eWarn, alert storm)은 알람 데이터 중심이고 메트릭 계절성을 다루지 않는다.
   - GIFT-Eval·BOOM도 업무 달력 공변량을 평가하지 않는다.
4. **APM(JVM 힙·GC·스레드풀) 고갈 예측과 DPM(대기 이벤트·락) 선제 예측의 최신 동료심사 문헌이 드물다.**
   - 소프트웨어 노화 서베이(2014)와 DB 진단 문헌(DBSherlock 2016, iSQUAD 2020)은 있다.
   - 현대 APM 지표로 고갈 ETA의 정확도를 평가한 연구는 찾지 못했다.
5. **파운데이션 모델 이상탐지 근거가 얇고 불안정하다.**
   - TSB-AD는 점 이상 강점과 오염 우려를 보고했다. Uray 2026(preprint)은 지속 이상에 부적합하다고 했다. TimeRCD는 ICML 2026 포스터 페이지가 있는데 arXiv에 철회가 표기돼 있다.
   - 관측 데이터(BOOM)는 **예측** 벤치마크일 뿐 탐지 라벨이 없다.
6. **CPU 추론 비용이 비교 가능한 형태로 보고되지 않는다.**
   - Chronos-2는 A10G GPU 처리량, TiRex는 A40 GPU 실험을 보고했다. 확인한 범위에서 CPU 추론 시간을 표로 제시한 것은 TTM뿐이다.
   - 서버 수천 대 × 지표 수십 개를 CPU 배치로 돌리는 비용은 내부에서 측정해야 한다.
7. **라이선스 변동 위험을 다룬 문헌이 없다.** TimesFM이 2.5(Apache-2.0)에서 3.0(비상업)으로 바뀐 사례처럼, 같은 모델 계열 안에서도 버전에 따라 라이선스가 뒤집힌다. 산업 자료로만 확인된다.
8. **운영자 피드백(희소 라벨)을 탐지기 선택·임계 조정에 쓰는 최신 동료심사 연구가 적다.** Opprentice(2015)와 iSQUAD(2020, 군집당 1회 라벨)가 원형이지만 2024~2026 후속을 찾지 못했다.
9. **벤치마크 결함 문제는 IT 운영 데이터에도 그대로 있다.** SMD도 PA 기반 보고가 관행이고, SWaT는 사용 중단이 권고됐다. IT 운영 전용의 결함 없는 공개 탐지 벤치마크는 확인하지 못했다. 내부 골든셋이 불가피하다.

---

## 4. 종합 권고 — 단계적 도입안

### 4.0 공통 전제(모든 단계)

- **결정 경로:** 모든 단계의 산출은 **수치(점수·구간·ETA·확률)**이고, 판정은 규칙 엔진(임계·지속시간·변경창 억제·노이즈 게이트)이 한다. HolmesGPT와 LLM은 이 수치를 해석·서술만 한다(A-04, H-05 LILAC 캐시 원칙).
- **현행 코드 접점:** `noise_gate/infrastructure/metric_stl.py`는 robust STL과 잔차 모표준편차 z를 쓴다. `polestar_metric_baseline.py`는 `cmm_metric_stat_h`, 주기 24, statsmodels 미반입 시 순수 Python Holt-Winters 폴백이다.
  - 문헌 기반 개선 1: 잔차 척도를 **MAD**로 바꾼다(B-06, I-05 Grafana MAD).
  - 문헌 기반 개선 2: **주간 주기(168h)**를 추가한다(I-05 Datadog 3주 요건).
  - 문헌 기반 개선 3: 금융 달력 플래그를 넣는다(F-01, I-01).
- **반입 순서:** 폐쇄망의 병목은 모델 가중치보다 **패키지 반입**이다(statsmodels조차 미반입). 단계 1은 순수 Python·numpy로 구현할 수 있는 방법을 우선한다.

### 4.1 단계 1 — 통계 기준선 + 변화점(즉시)

- **방법:**
  - robust STL(일·주 이중 계절) 잔차를 MAD 척도로 판정한다(S-H-ESD식).
  - Seasonal Naive 대역을 둔다.
  - 수준 이동은 BOCPD 또는 CUSUM류로 탐지한다.
  - 추세 외삽과 conformal 구간으로 **자원 고갈 ETA**를 산출한다(디스크·메모리, 제니퍼 힙).
  - 변경·점검 창에서는 억제한다.
- **진입 조건:**
  - 데이터: 시간 집계 **3주 이상**(주간 계절 3주기, I-05 참고치). 월말 효과 추정에는 **3개월 이상**(월말 3회)이 필요하다.
  - 라벨: 필요 없다.
  - 반입: 순수 Python 구현 또는 statsmodels(BSD-3)·ruptures(BSD-2) 중 반입 승인된 것.
- **평가 기준:**
  - (a) 과거 사건 목록 대비 이벤트 재현율과 **서버-일당 알람 수(알람 예산)**.
  - (b) ETA는 실제 임계 도달 시각 대비 절대오차 분포와 구간 커버리지(목표 신뢰수준 ±허용폭).
  - (c) 무작위 점수를 온전성 점검용으로 함께 채점한다.
- **근거 ID:** B-06, B-07, D-01, D-02, D-04, D-05, F-02, F-04, I-05, E-07

### 4.2 단계 2 — 경량 ML·앙상블(단계 1 운영 4~8주 후)

- **방법:**
  - 서버별 다변량(CPU·메모리·디스크·네트워크) **PCA 재구성오차**를 쓴다.
  - Sub-PCA, KMeans·KShape 부분수열, IsolationForest·RRCF, 1-NN 거리를 **비교군**으로 둔다.
  - 제니퍼(TPS·응답시간·에러율)와 폴스타 간 **관측 불일치 점수**를 만든다(gray failure).
  - 운영자 피드백이 쌓이면 Opprentice식 랜덤포레스트로 탐지기와 임계를 선택한다.
- **진입 조건:**
  - 데이터: 시간 집계 3개월 이상. 제니퍼 분 단위는 2주 이상.
  - 라벨: 과거 사건과 운영자 판정을 모은 **내부 골든셋**. 결함 점검(D-01) 기준으로 자명한 사례와 이상 밀도 과다를 배제한다.
- **평가 기준(채택 게이트):**
  - 골든셋에서 단계 1 대비 **VUS-PR 우위**와 이벤트 단위 정밀도·재현율 우위를 **동시에** 보여야 한다.
  - 통계적 유의성은 부트스트랩 신뢰구간 또는 Friedman–Nemenyi(TSB-AD 방식)로 확인한다.
  - PCA, 1-NN, L2-norm, 무작위 점수를 이기지 못하는 모델은 채택하지 않는다(D-06).
- **근거 ID:** D-03, D-04, D-05, D-06, D-07, B-01, B-05, C-02, E-07, I-02

### 4.3 단계 3 — 파운데이션 모델 제로샷(예측·기대대역 전용)

- **방법:**
  - 파운데이션 모델을 **탐지기가 아니라 예측기**로 쓴다. 분위수 기대대역과 ETA를 산출한다.
  - 공변량(달력·배치 플래그)은 Chronos-2·TimesFM-2.5에 넣는다.
  - 탐지 신호로는 **점 이상·변화점 보조**에만 쓴다(G-12). 지속 열화는 단계 1 추세 규칙이 판정한다.
  - 후보 우선순위:
    1. Chronos-2 소형(28M)
    2. Toto 2.0 소형
    3. TimesFM-2.5
    4. TTM-R2(CPU 하한)
  - 회피: Moirai 전 계열(CC-BY-NC), TimesFM-3.0(비상업), TiRex(NXAI 매출 조건), TimeGPT(폐쇄 가중치).
- **진입 조건:**
  - 보안 심사: 가중치 라이선스 Apache-2.0/MIT, 가중치 해시와 버전 고정, 오프라인 로딩.
  - CPU 지연 예산: 수집 주기 안에 전 서버 배치 추론이 끝나야 한다(내부 측정 필수, §3-6).
  - 백테스트: 내부 rolling-origin 백테스트에서 **Seasonal Naive·AutoETS·AutoARIMA·단계 1 STL 대비 MASE와 CRPS(WQL) 개선**이 신뢰구간 밖이어야 한다(GIFT-Eval·BOOM과 같은 Seasonal Naive 정규화).
  - 오염: 공개 데이터 결과는 참고만 한다(D-04, G-11). 판정은 내부 데이터로만 한다.
- **평가 기준:**
  - 예측: MASE, CRPS/WQL, 분위수 구간 커버리지.
  - ETA: 절대오차와 조기·지연 비대칭 비용.
  - 운영: 단계 1 대비 알람 예산 변화.
- **근거 ID:** G-02, G-03, G-06, G-08, G-11, G-12, F-02, F-04, D-04

### 4.4 단계 4 — 지도 예측(사건 라벨 축적 후)

- **방법:**
  - 폴스타 알람 이력을 Drain으로 템플릿화하고 eWarn식 특징(템플릿 빈도·텍스트 토픽·통계·업무시간)을 만든다.
  - 그래디언트 부스팅 또는 랜덤포레스트로 **사건 발생 위험 점수**와 LIME식 설명을 낸다.
  - 대량 서버에는 MING식 **위험 순위와 비용 민감 임계**를 적용한다.
  - 디스크는 SMART가 확보되면 Lu 방식(SMART + 성능 + 위치)을 쓴다.
- **진입 조건:**
  - 라벨: 시스템별 사건 **수십 건 이상**. 참고치로 eWarn 실측은 3년에 26~227건/시스템이었다.
  - 운영 요구: 리드타임 요구치(예: 최소 경고시간 Δt_w)가 합의돼 있어야 한다.
  - 기준선: AirAlert의 "단순 스파이크 규칙"과 은행 기존 방식(연관규칙)을 구현해 둔다.
- **평가 기준:**
  - 이벤트 단위 정밀도·재현율과 **리드타임 분포**(Δt_w 미만 적중은 실패로 친다).
  - 창 단위 F1은 보고용 부지표로만 쓴다(§3-2).
  - 단순 규칙 대비 우위가 신뢰구간 밖이어야 한다(E-03의 격차가 0.6~2.7점에 불과했음).
  - 새 알람 폭풍을 만드는지 점검한다(I-04).
- **근거 ID:** E-01, E-02, E-03, E-04, E-05, H-01, H-03, I-04, A-01

### 4.5 평가 프로토콜 권고(필수)

1. **point-adjust 금지.** 결과표에 F1-PA를 넣지 않는다. 과거 문헌과 비교해야 한다면 PA%K(작은 K)와 PA 없는 F1을 **함께** 보고한다(D-02, D-04, D-06).
2. **오프라인 알고리즘 비교 척도는 VUS-PR**로 하고 AUC-PR을 보조로 둔다(D-03, D-04). 임계에 의존하는 best-F(오라클 임계)는 금지한다(B-02의 관행 비판).
3. **운영 채택 척도는 이벤트 단위**로 한다.
   - 정밀도: 발송한 알람 중 실제 사건에 연결된 비율.
   - 재현율: 사건 중 선행 탐지된 비율.
   - **탐지 지연(Time-to-Detect) 분포.**
   - **알람 예산**: 서버-일당·주당 알람 수와 운영자 검토 부하.
4. **예측·선제 탐지는 리드타임 분포로 채점**한다.
   - Salfner 정의를 채택한다(Δt_l ≥ Δt_w, 예측기간 Δt_p).
   - 최소 경고시간보다 늦은 적중은 "탐지"로 분리 집계한다.
   - 겹치는 창의 중복 양성은 사건당 1회로 접는다(§3-2).
5. **의무 기준선**을 함께 보고한다.
   - 탐지: 무작위 점수, 입력값 자체, Seasonal Naive 대역, robust STL + MAD, PCA 재구성, 1-NN 거리.
   - 예측: Seasonal Naive, AutoETS, AutoARIMA.
   - 사건 예측: 단순 스파이크 규칙, 연관규칙(D-02, D-06, E-03, E-04, F-02).
6. **분할은 시간순**으로 한다. rolling-origin을 쓰고 무작위 셔플은 금지한다. 월말·월초 주기를 최소 2회 이상 포함한 테스트 기간을 둔다(금융 계절성, §3-3).
7. **변경·점검 창 처리:** 계획된 변경·점검 구간을 라벨에서 분리 표기한다. 억제 규칙 적용 전후 성능을 둘 다 보고한다.
8. **골든셋 결함 점검:** 자명성(한 줄짜리 규칙으로 풀리는지), 이상 밀도, 라벨 경계 오류, 장애 직전 편향을 확인한다(D-01).
9. **통계적 유의성:** 부트스트랩 신뢰구간 또는 Friedman–Nemenyi 임계차 도표를 쓴다. 평균 점수 차이만으로 채택하지 않는다(D-04).
10. **예측 척도:** MASE와 CRPS(WQL)를 Seasonal Naive로 정규화하고 기하평균으로 집계한다(G-06, G-11 관행). 여기에 분위수 구간의 **실측 커버리지와 폭**, ETA 절대오차를 더한다.
11. **오염 통제:** 공개 벤치마크 성능은 채택 근거에서 뺀다. 파운데이션 모델은 사전학습 데이터 중첩 여부를 확인할 수 없으므로 내부 데이터로만 판정한다(D-04, G-02, G-11).
12. **섀도 운영:** 채택 전에 결정 경로에 연결하지 않고 섀도 모드로 병행 운영한다. 월말을 2회 이상 포함하는 기간 동안 이벤트 단위 지표로 판정한다.

---

## 5. 검색 로그(요약)

- **도구:**
  - OpenAlex CLI(claude-scholar, `title.search` 필터)
  - Crossref REST(`/works/{doi}`: 게재처·연도)
  - OpenAlex `works/doi:`(초록 inverted index 복원)
  - arXiv API(`id_list`: 제목·comment·journal_ref·초록)
  - WebSearch/WebFetch(PMLR·OpenReview·NeurIPS·USENIX·공식 문서)
  - 원문 PDF를 내려받아 `pdftotext`로 본문·표를 추출하고 grep으로 수치 확인(V1 항목)
  - Hugging Face API(`cardData.license`·safetensors 총량)
  - GitHub API(`gh api repos/...`: SPDX 라이선스·보관 여부·최근 푸시)
- **V1 원문 확인 문헌:** Wu & Keogh, Kim et al., TSB-AD, Schmidl et al., Sarfraz et al., SR-CNN, Donut(평가 절차), eWarn, AirAlert, CDEF(도입부), Lu FAST'20, Toto·BOOM, Chronos-2, TiRex, TTM(초록·CPU), Le & Zhang(초록).
- **요청서 전제와 다른 점(검증 결과):**
  - MING은 ATC'18이 아니라 **ESEC/FSE'18**이다. ATC'18은 CDEF(디스크 오류)다.
  - eWarn은 **ESEC/FSE'20**이고, 저자에 은행 소속이 있으며 은행 데이터를 썼다.
  - AirAlert는 WWW'19가 맞다.
  - GIFT-Eval은 NeurIPS'24 **워크숍**이다.
  - Chronos는 학회가 아니라 **TMLR**(2024-10)이다.
  - Lag-Llama·TimeGPT-1·Toto·BOOM·Chronos-2·Moirai 2.0·Toto 2.0은 **preprint**다.
  - Wu & Keogh는 TKDE(DOI 2021)와 ICDE'22 확장 초록으로 나뉜다.
- **검증 중 발견한 오류:**
  - WebFetch 요약 모델이 Wu & Keogh에 대해 "Yahoo·NASA 이상의 약 70%가 임계법으로 탐지된다"고 답했는데, 원문 텍스트에 없는 내용이었다. 원문 grep으로 **316/367(86.1%)**을 확인해 교체했다.
  - UCR Anomaly Archive의 규모(요약 모델이 "250"이라고 답함)는 원문에서 확인하지 못해 적지 않았다.
- **실패·제약:**
  - DBLP API는 429 속도 제한과 연결 종료로 쓰지 못했다.
  - OpenAlex 제목 검색으로 Chronos·TimesFM·Moirai·Lag-Llama·MOMENT·Toto·GIFT-Eval·Sundial·TiRex·TimeGPT 레코드가 나오지 않았다. ML 학회 색인이 누락된 것으로 보고 PMLR·OpenReview·NeurIPS proceedings·arXiv comment로 대체 확인했다.
  - MING 원문 PDF는 받지 못해 초록 수준(V2)으로 두었다.
  - InterFusion 공식 저장소는 GitHub에서 404였다.
- **2025~2026 최신 동향 웹 확인 항목:**
  - Chronos-2(2025-10)
  - TimesFM 2.5(2025-09)·3.0(2026-08, 비상업 가중치)
  - Moirai 2.0(2025-11, CC-BY-NC)
  - Toto 2.0(2026-05)
  - TiRex(NeurIPS'25, NXAI 라이선스)
  - IBM PatchTST-FM-r2(2026-09-09)
  - Uray et al. 제로샷 다변량 이상탐지(2026-07)
  - TimeRCD 철회 표기
  - TAB(PVLDB'25)
  - Azure Anomaly Detector 퇴역(2026-10-01)
  - LLM 로그 파싱 리뷰(2025)
