# Kaggle 경기·데이터셋 심층 조사 — 모니터링 지표 + 로그로 장애를 진단·예측하는 알고리즘

> **보관 메모(2026-09-20)**: `plans/101`(ML 기반 장애 진단·예측·RCA) v3 작성용 조사 원본. 사용자 지시 *"모니터링 정보와 로그 정보를 이용하여 ml 을 통해 장애를 진단하는 알고리즘을 캐글에서 심도 있게 찾아보고 분석하여 101번의 계획을 업데이트하라"*에 따른 것이다. 계획서 §3.8·§14가 이 문서의 항목 ID(`K-*`·`KC-*`·`KD-*`·`KL-*`·`KG-*`)를 인용한다. **설계 해석의 정본은 계획서**이며 이 문서는 서지·수치·검증 등급의 근거다.
> 앞선 세 문서(`ml_rca_literature.md`·`ml_anomaly_prediction_literature.md`·`ml_library_vendor_survey.md`)는 **동료심사 문헌과 라이브러리**를 다뤘다. 이 문서는 **경기(competition) 결과와 공개 해법**이라는 다른 증거 계열을 다루며, 그래서 결론도 "무엇이 이기는가"가 아니라 **"구현·평가에서 무엇이 실제로 점수를 만들고 무엇이 속임수인가"**에 있다.

## 0. 이 조사의 신뢰 한계 (먼저 읽을 것)

- **등급**: **V1** 원문 본문·표 대조 · **V2** 1차 출처 페이지 직접 확인(arXiv 초록 페이지·공식 블로그·작성자 본인 해법 글) · **V3** 2차 자료(검색 요약·3자 블로그·GitHub README 요약).
- **Kaggle 자체 페이지는 정적으로 읽히지 않는다.** 경기 개요·데이터·규칙·토론·리더보드 페이지는 JS 렌더링이라 이 조사 환경(HTTP fetch)에서 제목만 돌아왔다. 브라우저 자동화도 이 세션에서는 확장이 연결되지 않았다. 따라서 **경기 메타데이터(팀 수·기간·상금)와 데이터셋 라이선스 문자열은 대부분 V3**다. 반입·사용 결정 전에 해당 페이지를 사람이 직접 열어 확인해야 한다(→ `plans/101` J-11).
- **Meta Kaggle / Meta Kaggle Code**(Kaggle이 공개하는 플랫폼 메타데이터 덤프)를 쓰면 경기·노트북을 계량 조사할 수 있으나 이 조사에서는 내려받지 않았다(다운로드 = 사용자 승인 사항).
- **경기 결과는 동료심사가 아니다.** 아래 수치는 "그 데이터셋·그 지표에서 그 팀이 얻은 값"이며 일반화 주장이 아니다. 특히 **경기 상위 해법의 상당수가 누수를 쓴다**(§4). 그래서 이 문서의 1차 쓸모는 성능 기대치가 아니라 **평가 설계의 함정 목록**이다.
- 아래에서 **경기**는 Kaggle 주최 경기를 뜻한다. Kaggle이 아닌 경기(KDD Cup 등)는 `(Kaggle 외)`로 표시한다.

---

## 1. 핵심 결론 10개 (K-1 ~ K-10)

| ID | 결론 | 근거(대표) | 등급 |
|---|---|---|---|
| **K-1** | **표로 접은 시계열·이벤트 데이터에서는 GBDT가 정본이다.** 장애·고장 예측 계열 경기에서 상위 해법은 예외 없이 그래디언트 부스팅(LightGBM·XGBoost) 중심이었고, 딥러닝 단독이 이긴 사례는 원시 신호(waveform)·초장기 계열 경기로 한정됐다 | M5 Accuracy(상위 전원 순수 ML·대부분 LightGBM · 모든 통계 기준선과 그 조합을 유의미하게 앞선 첫 M-competition) · ASHRAE GEPIII(상위 = LightGBM 앙상블) · Telstra(우승 = GBT·NN·RF 3층 스태킹) · Bosch(XGBoost) · Grinsztajn NeurIPS'22(≈10K 표본 규모 표 데이터에서 트리 모델이 딥 아키텍처 튜닝 후에도 SOTA) | V3 / V2 |
| **K-2** | **점수를 만드는 것은 모델이 아니라 피처와 전처리다. 그리고 그 피처는 거의 항상 "다중 창 롤링 집계 + 범주형 인코딩 + 마지막 이벤트 이후 경과"다** | Telstra 상위권 자평(*"feature engineering, rather than ensembling or XGBoost tuning"*) · ASHRAE 설문(전처리·피처추출이 가장 중요한 단계) · Azure PM 템플릿(최근 w구간 이동평균·이동표준편차 + 오류 유형별 건수 + 부품 교체 후 경과) · M5(7일·28일 rolling mean·요일·skewness·kurtosis·과거 분위수) · C-MAPSS(rolling 피처로 test RMSE 21.89 → 20.19) | V2 / V3 |
| **K-3** | **경기 상위 해법의 "마법 피처"는 대부분 누수다. 그리고 그 누수는 거의 항상 ① 레코드 순서 ② 단조 ID 차이 ③ 사후 기입 필드 ④ 사건 그룹 분할 실패에서 온다** | Telstra(행이 위치별로 정렬돼 있어 위치 내 행 번호가 시간축을 복원 → 점수 급등, 작성자 본인이 누수 의심) · Bosch(`mindate_id_diff`·`mindate_id_diff_reverse` = 정렬된 제품 ID 차이 → **실환경 배포에는 제거 필요**로 명시) · ASHRAE(일부 상위 해법이 외부에 공개된 test 실측치로 앙상블 가중치 결정) | V2 / V3 |
| **K-4** | **분포 이동은 추측하지 않고 측정한다 — adversarial validation.** train/test를 이진 분류해 AUC가 0.7을 넘으면 두 구간은 같은 분포가 아니다 | Kaggle 표준 진단 기법 · NVIDIA "Kaggle Grandmasters Playbook" 1항(train-test 분포 점검을 *"생략하면 멀쩡한 워크플로가 무너진다"*) · 신용평가 적용 논문(arXiv 2112.10078) | V2 / V3 |
| **K-5** | **극단 불균형에서는 지표와 임계가 모델보다 중요하다. 임계값 자체가 점수의 일부다** | Backblaze SMART 불균형 비 11,501:1(문헌 범위 5,702:1~19,038:1) · Bosch·VSB가 불균형 때문에 MCC 채택 · VSB 1위 임계 0.350 vs 다른 모델 0.434(같은 지표에서 임계만으로 결과가 갈림) | V3 |
| **K-6** | **"계열마다 이상 1개, top-1로 채점" 과제에서는 matrix profile류가 상위권이다** | (Kaggle 외) KDD Cup 2021 Multi-dataset TSAD: 250 계열 각 이상 1개 · top-1이 정답 구간 ±100 포인트 안이면 정답 · 공개된 5위 해법은 **계열별로 subsequence 길이를 바꾼 matrix profile**만으로 217/250 = 86.8% | V3 |
| **K-7** | **예측에서 다중 창 median 기준선은 경기에서도 강했다.** 우승은 딥러닝이었지만 2위는 "median을 피처로 + 잔차에 XGBoost"였고, 같은 팀이 CNN·LSTM·Seq2Seq보다 단순 feed-forward가 나았다고 보고했다 | Web Traffic Time Series Forecasting(2017 · SMAPE): 1위 attention RNN seq2seq(핵심은 1년 전·1분기 전 값 피처) / 2위 5개 아이디어 중 하나가 "원값 대신 median을 피처로" · 널리 쓰인 공개 기준선이 창 길이를 피보나치로 늘린 **다중 창 median의 median** | V3 |
| **K-8** | **Kaggle의 "IT 운영 모니터링" 데이터는 대부분 합성이거나 연구 데이터 재업로드다.** 사내 기대치 근거로 쓸 수 없고 쓸모는 구현 회귀·누수 점검 연습에 있다 | 네이티브 업로드는 합성(AI4I 2020은 "synthetic" 명시 · Playground S3E17은 AI4I 기반 합성 생성 · "Logging & Monitoring Anomalies Dataset"(100k행, 2026-02)·"CPU Performance Metrics (System Monitoring Logs)" 등은 출처 서술 없음) / 실데이터에 가까운 것은 재업로드(SMD·NAB·Loghub HDFS·BGL)와 Backblaze | V3 |
| **K-9** | **스태킹·시드 앙상블의 이득은 소수점 셋째 자리다.** 마지막에 오는 기법이고 운영 해석성·지연을 대가로 지불한다 | NVIDIA Grandmasters Playbook 7항: XGBoost 100 시드 앙상블 ≈ MAP@3 0.379 vs 단일 시드 평균 0.376(+0.003) · 5항 스태킹은 "모델별로 다른 패턴을 잡을 때"로 조건이 붙는다 | V2 |
| **K-10** | **CV–LB 관계를 믿고 단일 최고점을 믿지 않는다.** 우리 말로 옮기면 "리플레이셋 점수와 섀도 성적의 관계를 보고, 리플레이 최고점 하나를 채택 근거로 쓰지 않는다" | Playground S6E2 1위 자평(*"trusting the CV–LB relation, not the leaderboard, and not the single highest CV in isolation"*) · 비판 측 지적(Kaggle은 리더보드 소수점·데이터셋 quirk 착취·고정 test 최적화를 보상한다) | V3 |

---

## 2. 경기별 상세 (KC-*)

> 우리 문제(모니터링 지표 + 로그 → 장애 진단·예측)와의 거리 순으로 정렬했다.

### KC-01 · Telstra Network Disruptions — **로그 피처로 장애 심각도를 분류한 정면 사례**

- 개요: 호주 최대 통신망의 서비스 장애 심각도를 3분류(`fault_severity` 0/1/2)로 예측. 2015-11-25 ~ 2016-02-29 · 974팀 · 지표 multi-class logloss. (V3)
- 입력 구조(우리 데이터와 대응이 좋다): `train`(id, location, fault_severity) + `event_type` + `log_feature`(+`volume`) + `resource_type` + `severity_type` — **id 하나에 여러 이벤트·로그 피처가 다대일로 붙는 구조**다. 폴스타의 "알람 1건 ↔ 관련 이벤트·로그·자원 다건"과 형태가 같다. (V2)
- 점수를 만든 피처(작성자 본인 글 · 31위/974 · private 0.41577): ① location label·occurrence-count 인코딩 ② 빈출 event/resource one-hot ③ **log volume 로그변환 후 min·mean·max·std·sum 집계** ④ **location별 leave-one-out target 인코딩(0 방지 스무딩)** ⑤ **location 그룹 안에서 정규화한 시간 순서**. (V2)
- 우승: GBT·NN·RF 15모델 3층 스태킹 · logloss 0.395. (V3)
- **누수**: ⑤가 바로 누수다. 행이 위치별로 정렬돼 있어 그룹 내 행 번호가 시간축을 복원하고, 낮은 값 구간이 특정 심각도로 몰렸다. 작성자 본인이 "향상이 너무 커서 누수를 의심했다"고 적었다. (V2)
- **우리에게 옮길 것**: 로그 피처의 집계 레시피(③)와 엔티티 target 인코딩(④ · 단 시간순 OOF 안에서만)은 그대로 쓸 수 있다. ⑤는 **금지 목록**이다 — 우리 리플레이셋도 `cmm_alarm` 적재 순서·`alarm_id` 증가가 시간축을 복원하므로 같은 누수가 자동으로 생긴다.

### KC-02 · Microsoft Azure Predictive Maintenance (데이터셋·템플릿) — **"시간 집계 + 오류 로그 → N시간 내 고장" 정식화의 공개 선례**

- 구성: `PdM_telemetry.csv`(100대 × 2015년 1년 · **시간 평균** voltage·rotation·pressure·vibration · 876,099행) · `PdM_errors.csv`(가동 중 오류 — 고장이 아니며 시각을 시간 단위로 반올림) · `PdM_maint.csv`(부품 교체) · `PdM_failures.csv`(고장으로 인한 교체 = maint의 부분집합) · `PdM_machines.csv`(모델·기령). (V3)
- 피처 레시피: 최근 w구간 이동평균·이동표준편차, 구간 내 변화량·초기값 대비 변화·변화 속도·임계 초과 횟수, **오류 유형별 건수**, **부품 교체 후 경과(age)**. 라벨은 "향후 X시간 내 어느 부품이 고장나는가" 다중분류. (V3)
- **이 사례가 중요한 이유**: 폴스타 `cmm_metric_stat_h`가 시간 집계만 준다는 제약(`plans/101` GAP-1·GAP-2)과 **해상도가 같다.** 시간 집계로 사건 예측을 시도한 공개 선례가 있고 그 피처 명세가 문서화돼 있다. 다만 이 데이터는 Microsoft가 만든 샘플·시뮬레이션 계열이고 동료심사 실증이 아니다 → **문제 정식화와 피처 레시피는 차용, 성능 기대치는 차용 금지.**

### KC-03 · Bosch Production Line Performance — **극단 불균형 + MCC + 누수의 교과서**

- 개요: 2016 · 1,373팀 · 상금 $30,000 · 부품이 품질검사에서 탈락할지(`Response=1`) 예측 · 지표 **MCC**(불균형 때문). 피처는 공정 station별 수치·범주·날짜 열이 1,000개 단위. (V3)
- 전처리 실측: station별 날짜 열이 중복이라 **약 1,100개 날짜 피처를 53개로 축소**했다(진입/이탈 시각이 갈리는 station 24·25만 예외). (V3)
- **누수**: `mindate_id_diff`·`mindate_id_diff_reverse`(정렬된 제품 ID 차이) 계열이 public 리더보드 점수를 만들었고, **실환경 배포에는 제거해야 하는 피처로 명시**됐다. (V3)
- **우리에게 옮길 것**: ① 불균형 지표로 MCC를 부지표에 넣는다 ② **ID 차이·정렬 위치 계열 피처를 금지 목록에 올린다** ③ "중복 열 축소"는 폴스타 EAV 피벗 결과에도 그대로 적용된다.

### KC-04 · ASHRAE Great Energy Predictor III — **대규모 다-엔티티 시계열 회귀의 공학 표준**

- 개요: 2019년 말 · 4,370명/3,614팀/94개국 · 1,448개 건물의 2,380개 계량기 · 훈련 2천만+ 점 · test 4,100만+ 점 · 제출 39,403건 · 공개 노트북 415건(전체 해법 40건+ 포함). (V3)
- 결과: 상위 해법은 **LightGBM 중심 GBDT 대규모 앙상블**. 공식 lessons-learned 논문 제목이 그대로 결론이다 — *"Gradient boosting machines and careful pre-processing work best."* 설문 응답자들은 **전처리·피처추출 단계가 최고 모델을 만드는 데 가장 중요했다**고 답했고 전원 Python·노트북 환경이었다. (V2)
- **누수**: 외부에 공개된 test 구간 실측치("leak")를 일부 상위 해법이 앙상블 가중 결정에 썼다. (V3)
- **우리에게 옮길 것**: "엔티티 수천 개 × 시간 집계 계열"이 우리 규모와 같다. 모델보다 **결측·이상치·단위 정합 전처리**가 성적을 갈랐다는 점을 M0 데이터 카드 작업의 우선순위 근거로 쓴다.

### KC-05 · M5 Forecasting (Accuracy / Uncertainty) — **분위수 예측을 경기가 검증한 사례**

- Accuracy: 상위 방법이 **전부 순수 ML(대부분 LightGBM)**이었고 **모든 통계 기준선과 그 조합보다 유의미하게 나았던 첫 M-competition**이다. LightGBM이 선택된 이유로 다형(수치·이진·범주) 피처 수용, 속도, 튜닝 파라미터가 적음이 꼽혔다. 크로스러닝(전 계열 공통 학습)의 이득은 **비영 관측이 많은 계열에서만** 나타났다. (V3)
- Uncertainty: 9개 분위수 × 여러 집계 수준. 우승자는 **집계 수준·분위수마다 별도 LightGBM**을 학습했고 피처는 rolling average·rolling median·요일·skewness·kurtosis·과거 분위수였다. 894팀 중 202팀(22.6%)만 ARIMA 기준선을 이겼고 우승은 24.6% 개선. **상위권의 공통 기법이 예측 구간을 경험적(empirical)으로 계산하는 것**이었다. (V3)
- **우리에게 옮길 것**: ① F2b(기대 대역)·F2a(ETA 구간)에서 **분위수별 별도 모델 + 경험적 구간**이 경기 검증된 단순 경로다 — conformal(P-13)과 경쟁이 아니라 기준선으로 병렬 배치한다 ② "희소 계열에서는 크로스러닝이 해롭다"는 관측은 알람이 드문 서버군에 그대로 적용된다(P-10 존별 분리 보고의 보강).

### KC-06 · Web Traffic Time Series Forecasting — **median 기준선의 강도**

- 개요: 2017 · Wikipedia 페이지 조회수 · SMAPE. 1위는 attention 기반 RNN seq2seq(cuDNN GRU)이고 핵심은 **1년 전·1분기 전 값을 피처로 넣은 것**. 2위는 ① 연 계절성 ② RMSE 대신 log1p 후 SMAPE 근사 ③ 이상치 제거 ④ Keras 예측의 **잔차에 XGBoost** ⑤ **원값 대신 median을 피처로** — 그리고 CNN·LSTM·Seq2Seq보다 단순 feed-forward가 나았다고 보고했다. 널리 쓰인 공개 기준선은 **다중 창 median의 median**이었다. (V3)
- **우리에게 옮길 것**: 의무 기준선에 **다중 창 median**을 추가한다(구현 몇 줄·의존 0). "작년 같은 시각" 피처는 폴스타 `_h` 보존이 1년 이상일 때만 성립하므로 J-1에 묶인다.

### KC-07 · VSB Power Line Fault Detection — **임계 최적화가 점수의 일부**

- 개요: 2019 · 3상 전력선 부분방전(partial discharge) 신호에서 결함 탐지 · 지표 **MCC** · 입력은 계측 1건당 800,000 포인트 신호. 확률 출력을 이진화하는 임계를 훈련 데이터에서 MCC 최대가 되도록 탐색하는 것이 표준 절차였고, **1위 임계 0.350 · 다른 모델 0.434**로 임계 자체가 모델별로 크게 달랐다. (V3)
- **우리에게 옮길 것**: 사건 예측(F2c)·게이트 상향 판정의 임계는 **학습 구간에서 고정하고 테스트·섀도 구간에서 재조정하지 않는다**는 규약. 재조정하면 "오라클 임계"가 되고 `plans/101` §6.1의 best-F 금지와 같은 위반이다.

### KC-08 · Kaggle Playground S3E17 "Binary Classification of Machine Failures" — **합성 데이터 경기의 한계**

- 2023-06 · AI4I 2020 기반 **합성 생성** 표 데이터 · 지표 AUC. 상위 해법은 튜닝된 GBDT + 원본(AI4I) 병합이 정석이었다. (V3)
- **우리에게 옮길 것**: 성능 근거로는 없다. "합성 표 데이터에서도 GBDT가 정본"이라는 K-1의 보강 사례로만 센다.

### KC-09 · (Kaggle 외) KDD Cup 2021 Multi-dataset Time Series Anomaly Detection — **matrix profile의 경기 근거**

- 250개 시계열, 각각 이상 **1개**. 각 알고리즘이 최고 이상 점수 지점(top-1)을 내고 정답 구간 ±100 포인트 안이면 정답. 1위 DeepBlueAI · 2위 Huawei Noah's Ark Lab · 3위 Hitachi America R&D. 공개된 5위 해법은 **계열별로 subsequence 길이를 바꾼 matrix profile**만으로 217/250 = 86.8%. (V3)
- **우리에게 옮길 것**: `stumpy`(이미 2차 라이브러리 후보)를 단계 1 **보조 탐지기 승격 후보**로 명시한다. 학습·라벨이 필요 없고 시간 집계의 "반복 패턴 이탈"에 맞다.

### KC-10 · (Kaggle 재업로드 · 대조) NASA C-MAPSS RUL

- 터보팬 run-to-failure 시뮬레이션 4개 부분집합. train은 고장까지 전 구간, test는 고장 전에 **우측 절단(right censored)**된다. Kaggle 노트북 계열에서 rolling mean·std 피처가 test RMSE 21.89 → 20.19로 개선된 보고가 있고 더 낮은 값을 주장하는 최신 결과도 있다. 노트북들이 반복해서 경고하는 것이 **rolling 피처를 과거만으로 계산할 것**이다. (V3)
- **우리에게 옮길 것**: "우측 절단"이 우리 ETA 평가와 같은 구조다 — 아직 임계에 닿지 않은 서버가 다수이므로, 도달한 케이스만 모아 오차를 평균하면 낙관 편향이 생긴다. 절단을 명시적으로 다루는 채점이 필요하다.

---

## 3. 데이터셋 장부 (KD-*) — 실/합성 · 라이선스 · 쓸 수 있는 용도

| ID | 데이터셋 | 실데이터? | 규모·해상도 | 라이선스 | 우리 용도 |
|---|---|---|---|---|---|
| **KD-01** | Backblaze HDD SMART(Kaggle 재업로드 다수 · 원천은 Backblaze 공개) | **실** — 운영 데이터센터 | 드라이브-일 단위(일 1행) · 연 수천만 행 | Backblaze 자체 약관(재판매 금지·출처 표기) — **원문 확인 필요** | 디스크 고장·고갈 예측(F2a·F2c)의 **유일한 실데이터 공개 대조군**. 불균형 비 11,501:1(문헌 범위 5,702~19,038:1) |
| **KD-02** | SMD(Server Machine Dataset · `SMD_OnmiAD` 등) | **실** — 대형 인터넷 기업 | 28대 × 38지표 × 5주 · **분 단위** · 3개 데이터센터 | OmniAnomaly 저장소 기준 — 확인 필요 | F1 다변량 탐지 **구현 회귀**. 라벨이 **운영자 인시던트 보고 기반**이라 우리 라벨 설계(G-9)와 성격이 같다 |
| **KD-03** | NAB(Numenta Anomaly Benchmark) | 실+인공 혼합(AWS CloudWatch 서버 지표 포함) | 50+ 라벨 계열 | **미확정** — 검색 요약은 MIT라 하고 과거 AGPL-3.0 표기 이력 주장이 함께 있다. **LICENSE 원문 확인 전 사용 보류** | 스트리밍 탐지 회귀. AGPL이면 `plans/101` §9 배제 대상 |
| **KD-04** | Loghub HDFS_v1 / BGL(Kaggle 노트북·재업로드) | **실** | HDFS_v1: 575,061 블록 중 16,838 이상 | 연구용(Loghub 조건) | **로그 템플릿(Drain) 파이프라인 구현 회귀 전용.** `plans/101` §6.2-7의 "연구용" 범주 |
| **KD-05** | Azure Predictive Maintenance(KC-02) | 샘플·시뮬레이션 | 100대 × 1년 · **시간 집계** · 876,099행 | Microsoft 샘플 조건 — 확인 필요 | **문제 정식화·피처 레시피 차용 전용.** 성능 기대치 금지 |
| **KD-06** | AI4I 2020 Predictive Maintenance | **합성**(원전이 명시) | 10,000행 × 14열 · 고장 유형 5종(TWF·HDF·PWF·OSF·RNF) | **CC BY 4.0**(V3 · UCI 표기) | 다중 고장 유형 분류 코드의 단위 테스트 픽스처 |
| **KD-07** | Kaggle 네이티브 "모니터링/로그 이상" 업로드 계열("Logging & Monitoring Anomalies Dataset" 100k행(2026-02) · "CPU Performance Metrics (System Monitoring Logs)" · "Industrial Alarm Monitoring Dataset (2018-2024)" 등) | **출처 서술 없음 — 합성 추정** | 수만~10만 행 | 업로더 지정(대개 CC0/CC BY) | **성능 근거·기대치로 쓰지 않는다.** 스키마 모양 참고만 |
| **KD-08** | 경기 데이터(Telstra·Bosch·ASHRAE·M5·VSB·Web Traffic) | 실(기업 제공) | §2 참조 | **경기 규칙 동의 필수** · 다수가 비상업·경기 목적 한정 | **폐쇄망 반입 대상 아님.** 개발망 사용도 규칙 원문 확인 후 |

> **판정**: Kaggle은 `plans/101` GAP-1·GAP-2(시간 집계 해상도에서 RCA·장애 예측을 평가한 실증의 공백)를 **메우지 못한다.** 실데이터는 분 단위(SMD)거나 일 단위(Backblaze)거나, 시간 집계지만 합성·샘플(Azure PM)이다. 대신 **문제 정식화·피처 레시피·누수 함정 목록**을 준다. 이것이 이 조사의 실제 산출이다.

---

## 4. 누수 사례 장부 (KL-*) — 리플레이 평가셋 설계의 금지 목록

| ID | 누수 형태 | 경기 사례 | 우리 데이터에서 같은 것 | 차단 |
|---|---|---|---|---|
| **KL-1** | **레코드 순서·행 번호** | Telstra: location 그룹 내 행 번호가 시간축을 복원해 점수 급등(KC-01) | `cmm_alarm` 적재 순서 · 조회 결과 행 순서 · 평가셋 파일 기록 순서 | 피처에서 행 index·정렬 위치 계열을 전면 배제. 빌더는 사건을 **시각 기준으로만** 정렬하고 index를 산출물에 남기지 않는다 |
| **KL-2** | **단조 증가 ID의 차이** | Bosch: `mindate_id_diff`·`..._reverse` — 실배포엔 제거 필요로 명시(KC-03) | `alarm_id`·시퀀스 차이 · `investigation_id` 순번 · 사건 간 ID 간격 | ID는 조인 키로만 쓰고 수치 피처로 만들지 않는다 |
| **KL-3** | **사후 기입 필드** | ASHRAE: 공개된 test 실측치로 앙상블 가중 결정(KC-04) | **`cmm_alarm_note.alarmcause`·`cmm_alarm_knowledge`·ack 시각·`resolution{duration}`·`recurrence`·피드백 `note`** — 모두 사건이 끝난 뒤 사람이 적는다 | 이 필드들은 **라벨로만** 쓴다. 피처 테이블 진입을 허용목록 방식으로 구조적으로 막고, 평가 보고서에 "피처/라벨 분류표"를 싣는다 |
| **KL-4** | **그룹(사건) 분할 실패** | Kaggle 실무 상시 지적: 같은 그룹 표본이 train·test로 갈리면 점수가 부풀음 | 한 장애의 알람 수십 건 · 한 호스트의 연속 구간 · 한 변경의 대조군 쌍 | **사건 단위 group 분할 + 시간순(purged) 분할을 동시에** 적용하고 경계 구간은 버린다 |
| **KL-5** | **미래 창 집계** | C-MAPSS 노트북들의 반복 경고(KC-10) | rolling median·MAD · STL 재적합 · 로그 템플릿 사전 학습 | 모든 집계는 **좌측(과거) 창만**. 템플릿 사전(Drain)은 **학습 구간에서 고정한 아티팩트**로 테스트 구간에 적용하고 재적합하지 않으며, 미지 템플릿은 OOV 버킷으로 보낸다 |
| **KL-6** | **재표본을 분할 전에 수행** | Kaggle 실무 상시 지적(분할 전 resampling은 폴드 간 정보 누수) | 불균형 대응으로 SMOTE 등을 검증 분할 전에 적용 | 재표본 대신 `class_weight`/`scale_pos_weight` + 임계 조정. 재표본이 필요하면 분할 **이후 train 폴드 안에서만** |

---

## 5. 로그 쪽 보강 근거 (동료심사 · 경기 밖)

- **EMSE 2025 · A Comprehensive Study of Machine Learning Techniques for Log-Based Anomaly Detection**(arXiv 2307.16714, V2): 지도학습에서 **전통 ML과 딥 ML이 탐지 정확도·예측 시간에서 대부분 대등**하고, 전통 ML이 **하이퍼파라미터에 덜 민감**하며, **준지도 기법은 지도 기법보다 유의미하게 나쁘다**. → `plans/101` P-1(단순·강건 우선)의 로그 축 대응 근거. 우리 조건에서 로그 이상탐지에 딥 모델을 들일 이유는 더 약하다.
- Loghub HDFS_v1 규모(575,061 블록 / 16,838 이상, V3)와 Drain 고정-적용 프로토콜(KL-5)은 M7c 템플릿 파이프라인 설계에 직접 들어간다.

## 6. 경기 증거의 반대편 (균형)

- 산업 비판(V3): Kaggle은 **리더보드 소수점·복잡도·데이터셋 quirk 착취·고정 test 최적화**를 보상하고, 그 관행을 실무에 그대로 옮기면 *"누수를 피처 엔지니어링으로, 복잡도를 실력으로, 과적합을 우승으로"* 착각하게 된다는 지적이 있다. 이 문서의 무게를 K-3·KL-*(누수 목록)·K-10(CV–LB)에 둔 이유다.
- 플랫폼 계량(V2): *Kaggle Chronicles: 15 Years of Competitions*(arXiv 2511.06304)는 Meta Kaggle·Meta Code로 2010~2025년을 훑고 "우승자들이 평균적으로 견고한 일반화 능력을 보인다"고 보고한다. 다만 경기별 방법 승패의 정량 표는 초록 수준에서 확인되지 않아 **배경 근거로만** 인용한다.
- 예측 축 학술 정리(V2): Bojer & Meldgaard, *Kaggle forecasting competitions: An overlooked learning opportunity*(arXiv 2009.07701 · Int. J. Forecasting 2021) — 6개 Kaggle 예측 경기 리뷰. **GBDT와 신경망이 함께 강했고, 전역(global) 앙상블 모델이 국소(local) 단일 모델을 앞서는 경향**이며, Kaggle 데이터가 M-competition보다 **간헐성·엔트로피가 높다**(= 실무에 더 가까운 지저분함)고 정리한다.

## 7. 공백 (이 조사로 메워지지 않은 것)

| ID | 공백 |
|---|---|
| **KG-1** | **시간 집계 모니터링 지표만으로 서버 장애를 선제 예측한 "실데이터" 경기가 없다.** 시간 집계 사례(Azure PM)는 샘플·시뮬레이션이고 실데이터(SMD)는 분 단위다 → `plans/101` GAP-2 유지 |
| **KG-2** | **메트릭 + 로그 + 변경을 함께 준 경기가 없다.** Telstra는 로그 피처만(메트릭 없음), Azure PM은 메트릭+오류로그(변경은 maint로 부분), Bosch는 공정 계측만 |
| **KG-3** | **RCA(원인 엔티티 순위)를 채점한 경기가 없다.** 경기 지표는 예측·분류·회귀뿐이다 → RCA 근거는 `ml_rca_literature.md`가 계속 정본 |
| **KG-4** | **금융권 달력 효과(월말·급여일)를 다룬 경기 데이터가 없다** → `plans/101` GAP-9 유지 |
| **KG-5** | Kaggle 페이지 원문(규칙·라이선스·리더보드·토론)을 이 조사에서 정적으로 확인하지 못했다 → `plans/101` J-11 |

## 8. 출처

**경기·데이터셋 페이지**(제목만 정적 확인 · 내용은 3자 기록 기반)
- `kaggle.com/competitions/` : `telstra-recruiting-network` · `bosch-production-line-performance` · `ashrae-energy-prediction` · `m5-forecasting-accuracy` · `m5-forecasting-uncertainty` · `web-traffic-time-series-forecasting` · `vsb-power-line-fault-detection` · `playground-series-s3e17`
- `kaggle.com/datasets/` : `arnabbiswas1/microsoft-azure-predictive-maintenance` · `stephanmatzka/predictive-maintenance-dataset-ai4i-2020` · `boltzmannbrain/nab` · `mgusat/smd-onmiad` · `priyamsaha17/backblaze-hard-drive-failure-dataset-2023` · `mirzayasirabdullah07/logging-and-monitoring-anomalies-dataset` · `mohammedarfathr/cpu-performance-metrics-system-monitoring-logs` · `behrad3d/nasa-cmaps`

**해법·분석 글**
- gereleth, *Telstra Network Disruptions Competition Writeup*(github.io · 작성자 본인 · V2)
- Arturus/kaggle-web-traffic(1위 해법 저장소) · Web Traffic 2위 해법 요약(V3)
- NVIDIA Technical Blog, *The Kaggle Grandmasters Playbook: 7 Battle-Tested Modeling Techniques for Tabular Data*(V2)
- Kaggle Playground S6E2 1위 writeup(*"trusting the CV–LB relation"* · V3)
- Zak Jost, *Using adversarial validation to diagnose overfitting problems*(V3) · `kaggle.com/code/lukeimurfather/adversarial-validation-train-vs-test-distribution`
- KDD Cup 2021 TSAD 5위 해법(`intellygenta/KDDCup2021` · V3) · KDD 2021 수상팀 보도자료(V3)
- Valeriy Manokhin, *Kaggle Folklore Is Not Data Science*(비판 측 · V3)

**논문**
- Grinsztajn, Oyallon, Varoquaux, *Why do tree-based models still outperform deep learning on typical tabular data?* · NeurIPS 2022 Datasets & Benchmarks (V2)
- Makridakis, Spiliotis, Assimakopoulos, *The M5 Accuracy competition: Results, findings and conclusions* · Int. J. Forecasting 2022 (V3) · 같은 저자군 *The M5 uncertainty competition: Results, findings and conclusions* (V3)
- Miller et al., *The ASHRAE Great Energy Predictor III competition: Overview and results* · Science and Technology for the Built Environment 26(10), 2020 · arXiv 2007.06933 (V3)
- Miller et al., *Gradient boosting machines and careful pre-processing work best: ASHRAE Great Energy Predictor III lessons learned* · arXiv 2202.02898 (V2)
- Bojer & Meldgaard, *Kaggle forecasting competitions: An overlooked learning opportunity* · Int. J. Forecasting 2021 · arXiv 2009.07701 (V2)
- *A Comprehensive Study of Machine Learning Techniques for Log-Based Anomaly Detection* · EMSE 2025 · arXiv 2307.16714 (V2)
- *Kaggle Chronicles: 15 Years of Competitions* · arXiv 2511.06304 (V2)
- Matzka, *Explainable Artificial Intelligence for Predictive Maintenance Applications* · AI4I 2020(AI4I 데이터셋 원전 · V3)
- 디스크 고장 불균형 수치: *Predicting severely imbalanced data disk drive failures with machine learning models*(Machine Learning with Applications, 2022) 등 (V3)
- 분포 이동 적용: *Managing dataset shift by adversarial validation for credit scoring* · arXiv 2112.10078 (V3)
