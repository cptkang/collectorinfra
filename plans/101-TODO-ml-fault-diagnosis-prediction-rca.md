# 101. ML 기반 장애 진단·예측·RCA — 폴스타·제니퍼·DPM 연계, HolmesGPT에 정량 증거 공급

> **작성일**: 2026-09-17 · **v2**(같은 날 — 사용자 지시로 ML 배치를 `sre_agent` 편입으로 재검토·변경 · 버전·라이브러리 제약 해제 반영 · `sre_agent` venv 의존성 해석 실측) · **v3**(2026-09-20 — 사용자 지시로 **Kaggle 경기·데이터셋 심층 조사** 반영: 구현 공학 원칙 K-1~K-10 · **누수 금지 목록 KL-1~KL-6** · 평가 규약 11~13 · GAP-11·12) · **v4**(2026-10-13 — 사용자 지시로 **§R 「RCA 처리 흐름 정본」 신설**: 트리거→사건 시점 고정→결정적 수집→통계·ML 분석→원인 분류 표→조치 표→LLM 보고→전달·피드백 8단계 · CPU 100%→프로세스→WAS→제니퍼 인스턴스·서비스·메서드 사례 명세 · 현행 `apm_*` 24종 구현 반영 · G-12~G-14 · J-12~J-16) · **v5**(2026-10-13 — 사용자 지시로 **§R.8 SMS(폴스타)×APM(제니퍼) 알람 복합 분석**(양방향 트리거 · 시각 정렬 · 계층 순서·방향 판정 · 과거 동시발생·시간대 학습) · **§R.9 G-14 해결 — 함수 단위 집계**(profile.txt 구조화 파서 · 실행 중 요청 표본 · 핫 스레드 · 합의 집계) · **§R.10 AWS G5 GPU 실행 기반**(운영망 연결 VPC · ML 전용 · LLM 서빙은 툴 호출 필요 시만) · G-15~G-17 · J-17~J-20 · J-13 해소) · **v6**(2026-10-13 — 사용자 지시로 **G5 = ML 장애 분석 서버**로 재정의: (다) 혼합 배치 — 판정 필수 경량 통계는 `run_service`, 무거운 장애 분석 GA-1~GA-6(광역 이상 스캔·다차원 국소화·유사 사건·과거 연관·학습 원인 순위·예측 구간)과 그 학습·배치는 G5 · 판정 독립 불변식 I-11 · LLM 서빙은 부차 · RC-22~RC-25 · J-21 · 인스턴스 출발 g5.4xlarge)
> **성격**: 문헌·경기 조사 + 구현 계획 · **상태: 계획(코드 0건 · 사용자 확정 게이트 G-1~G-17 대기(G-8·G-15는 확정 · G-14는 v5 지시로 해소 · G-7은 v5 재질의) · 확인 사항 J-1~J-21 대기(J-13 해소))** — 파일명 `-TODO`
> **요청 취지(사용자 지시 원문)**: *"holmesgpt에서 분석하는 장애 분석과 관련하여 머신러닝을 이용하여 장애 분석과 예측 기능을 추가로 구축하려고 한다. 장애 진단, 예측, RCA 등과 관련된 머신러닝, AI 관련 문헌과 라이브러리를 심도있게 조사하여 현재 구현되었거나 계획하고 있는 폴스타, 재니퍼, DPM 솔루션을 연계하여 장애 대응 기능을 구현하려고 한다. 관련 문헌과 자료, 논문 등을 조사하여 구현 계획을 별도의 파일로 작성하라."*
> **v2 지시(원문)**: *"버전과 라이브러리는 필요에 따라 수정하거나 사용할 수 있다. 패키지는 별도가 아니라 sre_agent에 구성하는 것을 검토하라."*
> **v3 지시(원문)**: *"101 번은 장애 진단을 위한 ml 에 대한 계획이다. 이런 장애 진단을 위해 모니터링 정보와 로그 정보를 이용하여 ml 을 통해 장애를 진단하는 알고리즘을 캐글에서 심도 있게 찾아보고 분석하여 101번의 계획을 업데이트하라."*
> **v4 지시(원문)**: *"101번 계획을 분석하고 어떤 데이터로 어떻게 분석할지가 명확히 정리되어야 한다. 만약 cpu가 100%일때 어떤 프로세스가 cpu를 가장 많이 쓰는지 보고 만약 해당 프로세스가 WAS일때 제니퍼에서 어떤 컨테이너나 어떤 함수가 문제가 되는지 정보를 수집하고 어떻게 해결할지에 대한 답이 제시해야 한다. …이렇게 자세한 프로세스를 다시 정리하라."* (전문은 §R 머리)
> **v5 지시(원문)**: *"알람이 폴스타만 예시로 되어 있는데 제니퍼에서도 알람이 발생될 수 있고 시간대와 연관관계를 확인하여 SMS, APM 알람을 복합적으로 분석해야 한다. 관련 내용을 반영하라. ML모델은 aws G5 gpu 서버를 사용할 예정이다. gpu에 설치 구성하여 ML을 동작시키는 방식으로 계획을 정리해야한다. G-14의 문제를 해결하여 함수 단위의 집계가 될 수 있도록 수정하도록 계획을 정리하라."* — 사용자 답변(G5 위치 = 운영망 연결 VPC · 용도 = ML 전용, 툴 호출 필요 시만 별도 LLM 서빙)은 §R.10 머리
> **v6 지시(원문)**: *"gpu 서버는 llm 서빙 목적보다 ML 을 통한 장애 분석의 목적으로 사용하려고 한다. 이 내용이 고려되어 있냐?"* → 선택지 (가)·(나)·(다) 제시 뒤 *"다 권장에 맞게 계획을 수정하라"* — §R.10 머리·R.10.1
> **인수하는 공백**: `plans/62` §5.1 「예측·선제 탐지(C5)」 슬롯. 번호 63은 다른 주제(폴스타 과적합 분리)가 가져갔고, 예측 코드와 소유 계획은 **0건**이다(§2.6). 이 계획이 C5를 명시적으로 인수한다. RCA 축은 `plans/50` §0.8·`plans/91` C′의 **결정적 상관 위에 ML 계층을 얹는 확장**이다(대체하지 않는다).
> **선행·접점 계획**: `plans/50`(RCA 설계 정본) · `plans/51`(L1/L2/L3 수집) · `plans/55`(APM·DPM 로드맵) · `plans/60`(E3 동적 baseline) · `plans/64`·`66`(조사 위임·조치 거버넌스) · `plans/83`(피드백 = 라벨 원천) · **`plans/87`(제니퍼 Open API · `apm_*`)** · `plans/91`(C′ 잔여 장부) · `plans/92`(Prometheus·OpenMetrics) · `plans/95`(ITAM)
> **관련 결정**: D-003(읽기 전용) · **D-035**(결정적 수치 + LLM 해석) · **D-048**(.5·.11 "ML 미사용") · D-107(토폴로지) · **D-110·D-113**(HW·STL) · **D-114**(e5 임베딩 반입·주석 전용) · **D-118**(`sre_agent` 경계 — **범위 개정 대상** §4.2 C-6) · **D-119**(관측 데이터 읽기 경계 = `mcp_server`) · D-120(운영 데이터 외부 LLM 송신 금지) · **D-127**(과금 승인) · **D-139**(패키지 경계) · **D-161**(승격-폐기 동반·폐기 4항 실측) · D-162(플래그 만료) · D-174(개발 측정치 기준선 인용 금지) · D-176 ⑤(표본 <20 추정 문구 금지) · D-177(피드백) · D-181(공유 venv 파손) · D-189(L3 경로 B) · D-195(제니퍼 예약) · **D-197**(결정적 상관·가설 순위) · D-207~D-209(C′) · D-210(92 예약) · D-219(폐쇄망 산출물 축소 반출)
> **신규 결정 예약**: **D-223**(「채번 이력」 표 등재 — §13). ※ 채번 실측 2026-09-17: `## D-` 헤더 최댓값 D-222 · 「변경 이력」 표 최댓값 D-222 · 「채번 이력」 표에 D-223 이상 예약 없음.
> **실측 기준**: 브랜치 `multiintent` · HEAD `c64ef98` · 2026-09-17. 코드·계획 실측은 읽기 전용 조사 에이전트 2개, 문헌·라이브러리 조사는 에이전트 3개로 수행했다. **패키지 설치 0건 · 실 LLM 호출 0건 · 운영 DB 조회 0건.** 라이브러리 버전·라이선스는 이날 PyPI·GitHub·Hugging Face API로 실측했다. **v2**: holmesgpt 0.36.0 설치본 소스를 직접 읽었고(`additional_toolsets`), `uv pip compile`로 `sre_agent` venv 조건(Python 3.13)의 의존성 해석을 실측했다(설치 0건). **v3**(2026-09-20): Kaggle 조사는 웹 검색·1차 출처 페이지 fetch로만 했다. **Kaggle 경기·데이터셋 페이지는 JS 렌더링이라 정적으로 읽히지 않아 경기 메타데이터·라이선스 문자열은 대부분 3자 기록 기반(V3)**이며, 데이터 내려받기·계정 접속·패키지 설치는 0건이다. 확인이 필요한 항목은 J-11로 모았다.
> **동반 조사 문서(근거 정본 · v3에서 4종)**: `docs/aiops_benchmark/ml_rca_literature.md`(RCA 49+22건) · `docs/aiops_benchmark/ml_anomaly_prediction_literature.md`(이상탐지·예측 60항목) · `docs/aiops_benchmark/ml_library_vendor_survey.md`(라이브러리·HolmesGPT·벤더 AI) · **`docs/aiops_benchmark/ml_kaggle_competition_survey.md`(v3 신설 — Kaggle 경기 10건·데이터셋 8계열·누수 사례 6종)**. 본문의 `RCA-*`·`PRED-*`·**`K-*`·`KC-*`·`KD-*`·`KL-*`·`KG-*`** ID는 이 문서들의 항목 ID다.
> **▶ 장애 한 건이 들어왔을 때 무엇을 어디서 모아 어떻게 분석·판정·보고하는지는 §R(v4 실행 정본)이다. SMS·APM 복합 분석은 §R.8, 함수 단위 집계는 §R.9, G5 GPU 실행 기반과 G5 장애 분석 API(GA-1~GA-6 · v6)는 §R.10이다. 결정만 필요하면 §10 「사용자 확정 게이트」와 §R.7(G-12~G-14)·§R.11(G-15~G-17 · G-7 재질의), 문헌 근거 요약은 §3, 단계별 작업은 §7·§R.6·§R.11이다.**

---

## 0. 요약

### 0.1 한 줄 결론

ML은 **판정자가 아니라 정량 증거 생산자**로 들인다. 점수·순위·예측 구간·고갈 ETA를 만드는 ML 계층을 **`sre_agent` 안에 구성**한다(v2 · §4.2).
- **조사 경로**: 결정적 사전수집이 ML 분석 함수를 **직접 호출**하고, HolmesGPT에는 **in-process 도구셋**으로 공급한다.
- **게이트 경로**: 같은 패키지의 **별도 배치 프로세스(`ml_batch`)**가 Redis 신호로 `noise_gate`에 공급한다.
- 판정은 지금처럼 결정적 규칙이 하고, LLM은 그 수치를 인용해 서술만 한다.
- 첫 구현은 **폴스타 단독 · 강건 통계**다. 버전·라이브러리는 필요에 따라 올린다(holmesgpt 0.42.x 포함). 제니퍼·DPM 연계는 각 커넥터가 생긴 뒤 단계적으로 얹는다.

### 0.2 문헌·경기 증거가 이 설계를 정한 방식 — 여섯 가지

| # | 문헌 결론 | 수치(원문 대조 V1) | 설계 반영 |
|---|---|---|---|
| ① | **이상탐지는 단순 강건 통계가 딥러닝을 이긴다** | TSB-AD(NeurIPS'24) 단변량 VUS-PR: Sub-PCA 0.42 · TranAD 0.26 · Anomaly Transformer 0.12. SMD F1: PCA 재구성오차 0.572 > TranAD 0.457 > Anomaly Transformer 0.426(ICML'24) | 1차 탐지는 STL(일·주)+MAD·변화점, 2차는 PCA/IForest. 딥 탐지기는 들이지 않는다 (PRED-D-04·D-06) |
| ② | **point-adjust 평가는 무작위 점수도 SOTA로 만든다** | SWaT 무작위 점수 F1-PA 0.969 vs F1 0.216(AAAI'22). Yahoo 367계열 중 316개(86.1%)가 한 줄 규칙으로 풀린다(TKDE) | PA 금지 · VUS-PR · 이벤트 단위 지표 · 의무 기준선 (PRED-D-01·D-02) |
| ③ | **예측은 반대로 파운데이션 모델이 통계를 앞선다. 다만 가중치 라이선스가 1차 필터다** | GIFT-Eval WQL skill: Chronos-2 51.4% vs AutoARIMA 8.8%. 관측 데이터 BOOM MASE: Toto 0.617 vs Auto-ARIMA 0.824, **Time-MoE 0.881은 ARIMA보다 나쁨**. TimesFM 3.0·Moirai 전 계열은 비상업 가중치 | 파운데이션 모델은 **탐지기가 아니라 예측 구간·ETA 산출기**로만, 보안 심사 뒤 M7에 들인다 (PRED-G-02·G-06) |
| ④ | **데이터에서 인과를 "발견"하는 RCA는 무작위 수준이다. 도메인 지식 그래프는 통한다** | PC/Granger/PCMCI + PageRank 계열 ≈ Dummy(ASE'24). SimpleRCA 0.58 > BARO 0.24(RCAEval Top-1, FSE'26). **대형 은행 Oracle AAS 장애 99건**: CIRCA(DBA 작성 구조 그래프) AC@1 0.404 · NSigma 0.323 · PC 기반 랜덤워크 0.086(KDD'22) | 자동 인과 발견은 운영 경로에서 회피한다. 이벤트 그래프·강건 점수·다차원 국소화 → DBA/운영자 검토 스켈레톤 그래프 순서로 간다 (RCA-B-01·B-04·A-05) |
| ⑤ | **LLM 단독 RCA는 약하고, "결정은 코드 · LLM은 국소 해석"이 일관성을 높인다** | OpenRCA 최고 11.34%, 원인 요소 3개 과제 0%(ICLR'25). ITBench GPT-4o 진단 13.81% → **트레이스 없으면 9.52%**(ICML'25). EoG(결정적 컨트롤러)의 Majority@k F1은 ReAct의 7배(preprint). Cloud-OpsBench 정답률 0.76 vs 증거 폐포율 0.38(preprint) | LLM에 원시 시계열이나 재순위 권한을 주지 않는다. 브리핑은 원인 적중과 **증거 폐포율**로 함께 채점한다 (RCA-G-01·G-02·G-08·G-09) |
| ⑥ | **(v3 · Kaggle) 표로 접은 시계열·이벤트에서는 GBDT가 정본이고, 점수를 만드는 것은 모델이 아니라 피처다. 그리고 경기 상위 해법의 "마법 피처"는 대부분 누수다** | M5 Accuracy는 상위가 **전부 순수 ML(대부분 LightGBM)**이고 모든 통계 기준선·조합을 유의미하게 앞선 첫 M-competition이다. ASHRAE GEPIII(3,614팀 · 건물 1,448 · 계량기 2,380) 상위는 LightGBM 앙상블이고 공식 lessons-learned 논문 제목이 *"Gradient boosting machines and careful pre-processing work best"*다. Telstra(974팀) 우승은 GBT·NN·RF 3층 스태킹 logloss 0.395인데, 상위권 자평은 *"feature engineering, rather than ensembling or XGBoost tuning"*이다. 반대편도 실측됐다 — **Bosch `mindate_id_diff`는 "실환경 배포에는 제거해야 한다"고 명시된 누수 피처**이고, Telstra의 "마법 피처"는 위치 그룹 내 행 번호(= 시간축 복원)였다 | 사건 위험 점수(F2c) 모델을 **GBDT로 못 박고**(§5.3 (c)) 피처를 경기 검증된 레시피(다중 창 롤링 · 엔티티 인코딩 · 마지막 이벤트 이후 경과)로 명세한다. 동시에 **누수 금지 목록 KL-1~KL-6을 평가셋 빌더가 구조적으로 차단**한다(§5.1 · §6.2 규약 11~13). 앙상블·스태킹으로 지표를 쥐어짜는 것은 하지 않는다(K-9 · §12) |

### 0.3 가장 큰 병목은 알고리즘이 아니라 데이터다

| 병목 | 실측 | 영향 |
|---|---|---|
| 해상도 | `mcp_server` 폴스타 도구는 **`cmm_metric_stat_h/d/m` 집계만** 읽는다. 원시·5분 경로가 없다(§2.3) | **시간 집계로 RCA·장애 예측을 평가한 동료심사 문헌은 없다**(GAP-1·GAP-2). 문헌 수치를 사내 기대치로 옮길 수 없다 |
| 보존 기간 | `_h` 보존 기간 수치는 코드·설정 어디에도 없다(`plans/91` 1-9 외부 대기) | STL 주간 계절(168h×3주)·월말 2회 섀도가 성립하는지 모른다 |
| 라벨 | 피드백은 **알람 유형 단위**다. `alarm_id`·`fingerprint`·원인 필드가 없다(`noise_gate/infrastructure/feedback_store.py:86-101`) | 이벤트 단위 평가셋을 바로 만들 수 없다 |
| 조사 결과 휘발 | 브리핑 전문은 in-memory 잡(최대 500건 · **TTL 1시간**)에만 있고 감사 JSONL에는 요약만 남는다(`sre_agent/.../investigation_jobs.py:52-53`) | HolmesGPT 브리핑의 적중률을 사후 채점할 수 없다 |
| 미사용 원천 | `cmm_alarm.root_alarm_id`·`prev_alarm_id`(폴스타 자체 연관), `cmm_alarm_note.alarmcause`, `cmm_alarm_knowledge`, `cmm_dependency_link`·`cmm_service_associate`는 **코드·설정 참조 0건**(grep 실측) | RCA 그래프·약한 라벨 원천이 이미 있을 수 있다. 채움률 실측이 먼저다 |
| 외부 연계 | ~~제니퍼 `apm_*`는 계획만 있다(`plans/87` `-TODO`)~~ **[정정 v4 · 2026-10-13] `apm_gateway`에 `apm_*` 24종 구현됨 — §R.0** · **DPM 전용 계획서 없음** · `PROMETHEUS_URL` 공란(`plans/92` §0.2) | 교차 계층 RCA의 입력이 아직 없다 |
| **라벨·피처 경계**(v3) | 원인·조치 텍스트(`cmm_alarm_note.alarmcause`·`cmm_alarm_knowledge`)와 `resolution{duration}`·`recurrence`·ack 시각은 **사건이 끝난 뒤 사람이 기입한다** | 이 필드를 피처로 쓰면 누수다(KL-3). **라벨 원천으로만** 쓰고 피처 테이블 진입을 허용목록으로 막아야 한다. 이 경계를 세우지 않으면 리플레이셋 점수가 구조적으로 부풀어 채택 게이트 자체가 무의미해진다 |

→ **M0(데이터·전제 실측)이 모든 구현보다 앞선다.** 코드는 읽기 전용 실측 스크립트뿐이다.

---

## R. RCA 처리 흐름 정본 (v4 · 2026-10-13) — 어떤 데이터를 어디서 어떻게 모아, 무엇으로 분석해, 어떤 결론을 내고, 어떻게 보고하는가

> **v4 지시(원문)**: *"101번 계획을 분석하고 어떤 데이터로 어떻게 분석할지가 명확히 정리되어야 한다. 만약 cpu가 100%일때 어떤 프로세스가 cpu를 가장 많이 쓰는지 보고 만약 해당 프로세스가 WAS일때 제니퍼에서 어떤 컨테이너나 어떤 함수가 문제가 되는지 정보를 수집하고 어떻게 해결할지에 대한 답이 제시해야 한다. 이러한 예시처럼 정보를 어디서 어떤 api나 명령어, 스크립트로 수집하고 어떤 ML을 이용하여 분석하여 어떤 결론을 도출하고 그 결론에 따라 LLM을 통해 보고 내용을 정리하고 운영자에게 RCA에 대한 정보를 제공해야 한다. 이렇게 자세한 프로세스를 다시 정리하라."*
> **이 절의 지위**: v1~v3의 §3~§7은 **어떤 알고리즘이 옳은가**(문헌·경기 근거)와 **어떤 순서로 짓는가**(Wave)를 다룬다. 정작 **한 건의 장애가 들어왔을 때 무엇이 어떤 순서로 실행되는가**는 흩어져 있었다. 이 절이 그 실행 흐름의 **정본**이다. §3~§7과 충돌하면 근거는 §3~§7, 실행 순서·데이터 계약은 이 절을 따른다.
> **실측 기준**: 브랜치 `RCA` · HEAD `376d3be` · 2026-10-13. 코드 읽기만 했다(설치 0 · 실 LLM 0 · 운영 조회 0). 아래에서 **[구현]**은 코드에 있고 호출 가능하다는 뜻, **[신규]**는 이 계획이 만들 것, **[미확인]**은 실데이터로 확인해야 한다는 뜻이다.
> **용어**: 사용자 지시의 「컨테이너」는 **제니퍼 인스턴스**(JEUS 컨테이너·WebLogic 관리 서버처럼 JVM 하나에 해당하는 WAS 실행 단위)로 읽는다. K8s 컨테이너라면 매핑 원천이 달라지므로 J-12에서 확인한다.

### R.0 v3 이후 달라진 현행 — 계획서 본문이 낡은 지점

v3(2026-09-20) 본문은 제니퍼 `apm_*`를 "계획만 있다"로 적었다(§0.3 · §5.6 · G-2). **2026-10-13 현재 이 전제는 틀렸다.** CPU → 프로세스 → WAS → 함수 사슬의 부품은 대부분 구현돼 있고, 없는 것은 **사슬을 결정적으로 잇는 코드와 ML 분석·결론 판정 층**이다.

| 부품 | 상태 | 위치(실측) |
|---|---|---|
| 알람 수신 시 영향 프로세스 상위 N 첨부 | [구현] 게이트 enricher가 폴스타 실시간 프로세스 API를 불러 정렬·마스킹 | `noise_gate/application/nodes/alarm_context_enricher.py:313-383` · `noise_gate/domain/process_rank.py` |
| 프로세스 스냅샷 MCP 도구 | [구현] `polestar_process_snapshot(hostname, top_n, sort=cpu\|mem)` → REST `GET /rest/server/process/listByhostname` · **실시간 단면만**(이력 없음) | `mcp_server/mcp_server/polestar_tools.py:66,931` |
| PAGE 뒤 호스트 명령 수집(L3) | [구현] ssh 허용목록 · `timeout 20 nice -n 10` 접두 · cpu 프로파일 = `uptime` · `cat /proc/loadavg` · `top -b -n1` · `mpstat -P ALL 1 1` · `ps aux --sort=-%cpu` · 플래그 `l3_enrichment_enabled`(기본 off) | `noise_gate/infrastructure/host_diagnostic_collector.py:40-47` · `src/config.py:1063` |
| 프로세스 → 미들웨어 종류 식별 | [구현] cmdline 시그니처(jeus·weblogic·tomcat·jboss·websphere …) · 결정적 | `config/middleware_signatures.yaml` · `src/domain/middleware.py` (**`src` 소유 — `sre_agent`는 import 불가**) |
| **PID → 제니퍼 인스턴스** | [구현] `apm_config(kind=process_instance, process_id, hostname)` → `GET /api-v2/manage/instance?processId=&hostname=` | `apm_gateway/.../adapters/jennifer/manage_api.py:268-280` |
| 호스트 → 제니퍼 인스턴스 | [구현] `apm_instance_map(hostname)` → `/api/instance` + 정합 신뢰도 `match_confidence` | `apm_gateway/.../application/tools.py:1171` |
| WAS 골든 시그널·런타임 | [구현] `apm_app_health`(TPS·응답 p50/p95·에러율·액티브) · `apm_runtime_health`(`process_cpu_pct`·`heap_used_mb`·`gc_time_usage_pct`·스레드 · 5분 간격) · `apm_metrics(mode=series)` → `/api/realtime/instance` · `/api/dbmetrics/instance` | `tools.py:1595,1676,2651` |
| 서비스·SQL·외부 호출 통계 | [구현] `apm_status_stats(kind=application\|sql\|external_call)` · `apm_period_compare`(사건 vs 평소) → `/api/status/*` | `tools.py:2500,3375` |
| 실행 중 요청 | [구현] `apm_active_services`(`elapsed_ms`·`cpu_ms`·실행 모드·`active_ref`) → `/api/activeService/list` · `apm_active_detail` → `/api-v2/active-service/detail/{domainId}/{txid}` | `tools.py:1933` · `manage_api.py:248` |
| 느린 트랜잭션·프로파일 | [구현] `apm_slow_transactions`(시간 분해 `cpu_ms`·`sql_ms`·`fetch_ms`·`external_ms`·`network_ms` · `profile_ref`) → `/api/transaction/time` · `apm_transaction_profile` → `/api/transaction/profile.txt` + `/api/transaction/sql` | `tools.py:1872,2309` · `adapters/jennifer/fields.py` |
| 변경 감지 | [구현] `apm_source_changes` · `apm_change_impact`(변경 전후 비교) · 폴스타 `polestar_change_history`(PG 전용) | `tools.py:2843,3175` |
| 같은 증상 다른 WAS | [구현] `apm_fleet(mode=ranking\|events)` | `apm_gateway/.../interface/fleet_server.py:36` |
| WAS 증상 판정 | [구현] `was_signals` 8종(`was_service_queuing`·`was_thread_pool_exhaustion`·`was_db_pool_exhaustion`·`was_gc_stall`·`was_heap_pressure`·`was_slow_sql`·`was_external_call_delay`·`was_error_burst`) · 임계 `apm_gateway/config/was_signatures.yaml` | `apm_gateway/apm_gateway/domain/signals.py:22-31` |
| 조치 후보·검증·롤백 | [구현] OS·WAS 시그니처별 결정적 표(위험도·검증 방법·롤백) · 실행 경로 없음 | `sre_agent/sre_agent/domain/remediation.py:127-185` |
| 조사 사전수집·상관·브리핑 | [구현] 알람·지표 4종(시간 단위)·변경 → z-score 상관 → 규칙 가설 순위 → 브리핑 | `sre_agent/.../application/evidence_prefetch.py` · `domain/correlation.py` · `briefing_builder.py` |
| WAS 조사 순서 | [구현] **LLM 지침 문자열로만** 존재(① `apm_instance_map` → ② `apm_events` → ③ health → ④ 증상별 한 갈래) · `apm_guidance_enabled` | `sre_agent/.../application/investigation_guidance.py:273-295` |

**사슬이 끊긴 곳(이 절이 메우는 것)**

| # | 끊긴 곳 | 결과 |
|---|---|---|
| X-1 | 게이트가 알람 시점에 잡은 프로세스 스냅샷이 **조사 payload에 실리지 않는다**(`noise_gate/domain/investigation_payload.py`에 프로세스 필드 0건) | 조사가 몇 분 뒤 실시간 API를 다시 불러 **사건 시점이 아닌 단면**을 본다 |
| X-2 | CPU 상위 프로세스 → WAS 여부 → 제니퍼 인스턴스로 가는 분기를 **LLM이 지침을 읽고 고른다** | 같은 사건에서 매번 다른 경로를 탄다(EoG P-24의 반복 일관성 문제). D-035 「결정은 코드」에 어긋난다 |
| X-3 | `apm_transaction_profile`은 프로파일을 **텍스트 발췌로만** 돌려준다(`tools.py:2379-2441` `profile_excerpt`) | 「어떤 함수가 문제인가」를 코드가 집계할 수 없다 — LLM이 발췌를 읽고 짐작한다 |
| X-4 | 미들웨어 시그니처가 `src` 소유라 `sre_agent`가 못 쓴다(D-118 경계) | 제니퍼에 등록되지 않은 WAS·배치·DB 프로세스를 구분하지 못한다 |
| X-5 | 「평소에도 이 시각 CPU가 높은가」·「TPS 대비 CPU가 과한가」·「어느 서비스 몫이 늘었나」를 계산하는 층이 없다 | 원인 분류(부하형/효율 저하형/GC형/배포형)가 서술로만 나온다 |
| X-6 | 결론(원인 분류)과 조치 표를 잇는 **결정적 판정 표**가 없다 — 조치는 `was_signals` 단건 시그니처에만 걸린다 | 「CPU 100%·TPS 정상·배포 직후·특정 서비스 몫 급증」 같은 복합 증거를 하나의 결론으로 묶지 못한다 |
| X-7 | LLM 보고서의 수치가 증거 묶음에 있는 값인지 **사후 검사가 없다** | 환각 수치가 운영자에게 그대로 간다 |

### R.1 전체 흐름 — 8단계

```
 [알람]                                         [운영자 질의: "김포 A서버 CPU 왜 100%야?"]
   │ polestar 알람 → alarm_server(TCP 9100) → Redis alarm:raw          │ src fault_diagnosis → sre_diagnose(MCP)
   │ 제니퍼 이벤트 → apm_gateway 폴러 → Redis alarm:raw (v5 · §R.8)     │
   ▼                                                                    ▼
 S1 트리거·범위 확정 ─────────── 대상 엔티티(host·db_id·zone) · 기준시각 t_ref · 알람 kind(cpu) ──────────────┐
   │ (게이트 PAGE 또는 pull)                                                                                   │
 S2 1차 증거(사건 시점 고정) ──── 게이트가 알람 시점에 잡은 프로세스 상위 N · L3 명령 출력  [X-1 해소]             │
   ▼                                                                                                         │
 S3 계층별 결정적 수집 ────────── 호스트(폴스타) → 프로세스 분류 → (WAS면) 제니퍼 인스턴스·서비스·트랜잭션·프로파일 │ sre_agent
   │                              모든 호출은 mcp_server / apm_gateway MCP 도구로만(D-119 · D-274)              │ run_service
   ▼                                                                                                         │ (사전수집
 S4 분석(통계·ML) ─────────────── 이상 확인 · 부하 정규화 잔차 · 변화점 · 서비스 몫 국소화 · 메서드 핫스팟 · 피어 · 유사 사례 │  타임박스)
   │ ⇄ [v6] G5 ml_service 장애 분석 GA-1~GA-6 — S2 직후 비동기 요청 · S3·S4와 병행 · S5 진입 때 회수 · 보조 증거(I-11)  │
   ▼                                                                                                         │
 S5 결론 판정(결정적 규칙) ─────── 원인 분류 표(rca_rules.yaml) → 원인 후보 순위 · 확신도 · 기준선 병기                │
   ▼                                                                                                         │
 S6 조치 도출(결정적 표) ───────── 원인 분류 × was_signals → 조치·위험도·검증·롤백(remediation.py 확장)              │
   ▼                                                                                                         │
 S7 LLM 보고서 작성 ──────────── HolmesGPT(조사 LLM)가 RCA 묶음(JSON)을 받아 고정 양식으로 **서술만** · 수치 검사 [X-7] ┘
   ▼
 S8 전달·피드백 ──────────────── D-137 즉시통보 + 후속 브리핑 · 조사 화면 · 운영자 확정 원인 → 라벨(G-9)
```

| 단계 | 책임 모듈(예정) | 결정 주체 | 시간 예산(초기값 · J-9로 보정) |
|---|---|---|---|
| S1 | `noise_gate` 게이트(기존) · `sre_agent` `investigation_dispatcher`(기존) | 코드 | 기존 |
| S2 | `noise_gate` enricher·L3(기존) → payload 확장 [신규] | 코드 | 게이트 경로 밖(비차단) |
| S3 | `sre_agent/application/rca_collect.py` [신규] | 코드 | ≤ 60초(제니퍼 호출 상한 포함) |
| S4 | `sre_agent/domain/ml/*`(순수) + `infrastructure/ml/*`(lazy) [신규] · **[v6] G5 `ml_service` GA-1~GA-6**(R.10.1) | 코드 | `run_service` 경량 통계 ≤ 10초 · G5 GA는 S3와 병행해 S4 종료 + 10초에 회수(조사 타임박스 불변) |
| S5·S6 | `sre_agent/domain/rca/decide.py` + `config/ml/rca_rules.yaml` [신규] · `domain/remediation.py`(확장) | 코드 | ≤ 1초 |
| S7 | HolmesGPT(기존 조사 루프) + `briefing_builder`(확장) + 수치 검사 [신규] | LLM(서술) · 코드(검사) | 기존 조사 예산 |
| S8 | `alarm_notifier`(기존 D-137) · 피드백(G-9) | 코드 · 운영자 | — |

**불변식 재확인**(§4.1을 이 흐름에 적용): S3~S6은 LLM 없이 끝난다. LLM은 S7에서만 돈다. S7이 실패해도 운영자는 S5·S6의 구조화 결과를 받는다(서술 없는 표). 어느 단계도 서버·WAS·DB에 쓰기·조치를 하지 않는다(D-003).

### R.2 데이터 원천 카탈로그 — 무엇을, 어떤 도구·API·명령으로

> 모든 관측 읽기는 MCP 도구를 거친다(`sre_agent`는 DB·제니퍼 자격증명을 갖지 않는다 — D-118·D-119·D-274). 「하부 원천」 열은 도구 안에서 실제로 부르는 SQL 테이블·REST 경로·명령이다.

| ID | 데이터 | 수집 도구 | 하부 원천(API·SQL·명령) | 해상도·이력 | RCA에서 쓰는 곳 |
|---|---|---|---|---|---|
| D-P1 | 알람(사건 구간 전부) | `polestar_incident_alarms` · `polestar_alarm_history` | `cmm_alarm`(초 단위 CTIME) | 초 · 90일+ | S1 범위 · S4 선행 알람 · 반복 패턴(D-035) |
| D-P2 | 서버 CPU·메모리·FS·IO 추세 | `polestar_metric_trend(kind, granularity=h, reference_time, lookback_minutes, baseline_periods)` | `cmm_metric_stat_h`(avg/min/max) | **시간 집계** · 보존 [미확인 J-1] | S4-A 이상 확인(계절·MAD·변화점) |
| D-P3 | 프로세스 상위 N | 알람 시점: 게이트 enricher 첨부(X-1 해소 후 payload) · 조사 시점: `polestar_process_snapshot(hostname, top_n, sort=cpu)` | REST `GET /rest/server/process/listByhostname?hostname=` (`name`·`pid`·`ppid`·`user`·`p100cpu`·`pcpu`·`pmem`·`rss`·`args`(마스킹)) | **실시간 단면** · 이력 없음 | S4-B 프로세스 귀속 |
| D-P4 | 호스트 명령 출력 | L3 수집기(게이트 PAGE 뒤 · ssh 허용목록) | `top -b -n1` · `ps aux --sort=-%cpu` · `mpstat -P ALL 1 1` · `uptime` · `cat /proc/loadavg` | 실시간 · 상태지문 2회 비교 | S4-B CPU 성분(us/sy/wa/st)·코어 편중 |
| D-P5 | 변경 이력 | `polestar_change_history` | 폴스타 lifecycle(PG 전용 · DB2 미지원) | 초/ms [미확인 J-4] | S4-D 변경 정렬 |
| D-P6 | 토폴로지·구성 | `polestar_topology` · `polestar_os_config` · `polestar_resource_status` | `AVAIL_DEPEND` · `core_config_prop` | 현재 | S4-B 가상화(steal) 해석 · 연쇄 |
| D-J1 | PID → 인스턴스 | `apm_config(kind=process_instance, process_id, hostname)` | `GET /api-v2/manage/instance?processId=&hostname=` | 현재 | S3 WAS 확정(1순위) |
| D-J2 | 호스트 → 인스턴스 | `apm_instance_map(hostname)` | `/api/instance` + 정합(`match_confidence`) | 현재 | S3 WAS 확정(2순위) |
| D-J3 | JVM 런타임 | `apm_runtime_health(instance_name, reference_time, lookback_minutes, metrics?)` | `/api/dbmetrics/instance` · `/api/realtime/instance` | **5분 간격** 기본 · 보존 [미확인 J-7] | S4-C1 GC·힙·프로세스 CPU |
| D-J4 | 골든 시그널 | `apm_app_health(…)` · `apm_metrics(mode=series, metrics=[…], interval_minute)` | 같음 | **1분 조각**(`apm_app_health`) | S4-C1 TPS·응답·에러율 |
| D-J5 | 서비스·SQL·외부 호출 통계 | `apm_status_stats(kind=application\|sql\|external_call, sort_by, n)` · `apm_period_compare(current_*, baseline_*)` | `/api/status/application` · `/api/status/sql` · `/api/status/external_call` | **시 단위** | S4-C2 서비스 몫 국소화 |
| D-J6 | 실행 중 요청 | `apm_active_services(n)` → `apm_active_detail(active_ref)` | `/api/activeService/list` · `/api-v2/active-service/detail/{domainId}/{txid}` | **현재값 전용** | S4-C3 정체·루프 트랜잭션 |
| D-J7 | 느린 트랜잭션 | `apm_slow_transactions(n, reference_time, lookback_minutes)` | `/api/transaction/time`(시간 분해 `cpu_ms`·`sql_ms`·`fetch_ms`·`external_ms`·`network_ms` · `profile_ref`) | 건 단위 | S4-C3 표본 선정 |
| D-J8 | 트랜잭션 프로파일 | `apm_transaction_profile(source_id, domain_id, txid, time_ms)` | `/api/transaction/profile.txt` · `/api/transaction/sql` | 건 단위 · **조사당 호출 예산** | S4-C4 메서드 핫스팟 |
| D-J9 | 이벤트·오류 | `apm_events(level, lookback_minutes)` · `apm_fleet(mode=events)` | `/api/dbsearch/event` · `/api/dbsearch/error` | 건 단위 | S4 선행 이벤트 · 오류 유형 |
| D-J10 | 소스 변경 | `apm_source_changes` · `apm_change_impact(width_minutes)` | 게이트웨이 변경 감지 묶음 | 감지 시각(배포 확정 아님) | S4-D 배포 정렬 |
| D-J11 | 피어 WAS | `apm_fleet(mode=ranking, metric, domain_id)` | 도메인별 실시간·시 단위 통계 | 현재·기간 | S4-E 피어 비교 |
| D-K1 | 과거 사례 텍스트 | `polestar_alarm_history` 계열 + 조사 감사 상세(§4.5) | `cmm_alarm_note.alarmcause` · `cmm_alarm_knowledge` [채움률 미확인 J-3] · `sre_agent/.data/investigation_audit.jsonl` | 사건 단위 | S4-F 유사 사례 |
| D-R1 | (선택) 분 해상도 호스트 지표 | `prom_metric_range` | PromQL · 운영 `PROMETHEUS_URL` | 분 | S4-A 해상도 보강 |
| D-D1 | DB 내부(DPM) | **없음**(DPM 계획 미신설 · G-3) | — | — | DB 프로세스 분기는 폴스타 단독 + 「DB 내부 미관측」 명시 |

**사건 시점 고정 규칙**(실시간 원천 D-P3·D-P4·D-J6 공통)
- 실시간 원천은 응답의 `captured_at`과 사건 기준시각 `t_ref`의 차이 `Δcap`을 반드시 함께 싣는다.
- 우선순위: ① 게이트가 알람 수신 시 잡은 스냅샷(`Δcap` ≈ 수 초) ② L3 수집 출력 ③ 조사 시점 재조회.
- `Δcap` > `rca.snapshot_max_lag_seconds`(초기값 300)면 그 원천의 결론 기여를 **「사건 시점 단면 아님」**으로 표기하고, S5에서 그 원천만으로 성립하는 원인 분류의 확신도를 한 단계 낮춘다.

### R.3 CPU 100% 사례 — 단계별 실행 명세

> 입력 예: 폴스타 알람 `CPU 사용률 임계 초과(100%)` · `polestar_cm_gp` · 서버 `GP-APP-01` · `t_ref = 2026-10-13T14:07:00`.
> 아래의 임계·창 길이는 전부 **초기값**이며 `sre_agent/config/ml/rca_rules.yaml`에 둔다(코드 리터럴 금지 · 리플레이셋으로 보정 · §6).

#### S1 트리거·범위 확정
- **푸시**: 게이트가 PAGE를 결정하면 `investigation_trigger`가 `sre_investigate_alarm`을 부른다(기존). payload의 `event.dbId`·`serverName`·발생 시각에서 `EvidenceScope`를 만든다(`evidence_prefetch.scope_from_job` 기존).
- **풀**: 운영자 챗 질의가 `fault_diagnosis` → `sre_diagnose`로 들어온다(기존). 기준시각이 없으면 「현재」로 두고, 실시간 원천의 `Δcap` 규칙은 그대로 적용한다.
- **산출**: `RcaScope{db_id, zone, host, t_ref, kind="cpu", lookback=120분, baseline=504h(3주)}`. `kind`는 `process_rank.classify_alarm_kind`와 같은 규칙으로 정한다. **[v5] 제니퍼발 알람이면 이벤트 유형을 계층·kind로 매핑해 CPU 계열은 이 사슬에 합류하고, `was_*` 계열은 R.4 S-W·S-E로 간다 — 어느 쪽이든 §R.8 병합 타임라인(S3-T·S4-T)이 함께 돈다.**

#### S2 1차 증거 — 사건 시점 고정
- **[신규 X-1]** 게이트 enricher가 이미 만든 `process_snapshot`(상위 N · 마스킹 · `captured_at`)을 조사 payload `event.process_snapshot`으로 넘긴다. 플래그 `INVESTIGATION_PROCESS_SNAPSHOT_FORWARD`(기본 off · off면 payload 바이트 동일).
- L3가 켜져 있으면(`l3_enrichment_enabled`) PAGE 직후 `top -b -n1`·`mpstat -P ALL 1 1`·`ps aux --sort=-%cpu` 출력의 **결정적 요지**(상위 프로세스·us/sy/wa/st·코어별 편중)를 같은 방식으로 넘긴다. 원문은 넘기지 않는다(마스킹·4000자 절단은 기존 수집기 규칙).

#### S3 계층별 결정적 수집 — 분기 순서가 코드에 고정된다 [신규 `rca_collect.py` · X-2 해소]

```
S3-1 호스트:   polestar_metric_trend(cpu, h, reference_time=t_ref, lookback=120, baseline_periods=504)
               polestar_incident_alarms(t_ref, lookback=120)        polestar_change_history(t_ref−24h, t_ref)
S3-2 프로세스: S2 스냅샷이 없거나 Δcap 초과면 polestar_process_snapshot(host, top_n=10, sort=cpu)
               → 상위 프로세스 P1(및 점유율 합 80%까지의 P2..Pk)
S3-3 분류:    각 Pi에 대해
               (a) apm_config(kind=process_instance, process_id=Pi.pid, hostname=host)  → 인스턴스 있으면 class=WAS(제니퍼 관측)
               (b) 없으면 미들웨어 시그니처(cmdline)로 class ∈ {was_unmonitored, web, db, batch, agent, unknown}
               (c) 둘 다 실패하고 apm_instance_map(host).match_confidence=high 인 인스턴스가 1개면 class=WAS(호스트 정합) — 확신도 한 단계 ↓
S3-4 WAS:     class=WAS인 인스턴스 I에 대해(사건 구간 = [t_ref−120분, t_ref+15분] · 평소 구간 = 7일 전 같은 요일·시각 같은 길이)
               apm_runtime_health(I, reference_time, lookback)          → process_cpu_pct·heap·gc_time_usage_pct·thread 시계열
               apm_app_health(I, reference_time, lookback)              → TPS·응답 p50/p95·에러율·액티브(1분)
               apm_period_compare(사건 구간, 평소 구간, instance_name=I) → 서비스별 호출·실패·응답 증감
               apm_status_stats(kind=application, I, sort_by=total_cpu_ms, n=20)   ← [v5] J-13 해소 · §R.9.0
               apm_active_services(I, n=20)                             → elapsed_ms·cpu_ms·mode·active_ref (현재값)
               apm_slow_transactions(I, reference_time, lookback, n=20) → 시간 분해·profile_ref
               apm_events(I, reference_time, lookback, level=warning)   → JVM_CPU_HIGH·OUTOFMEMORY 등
               apm_source_changes(I, reference_time=t_ref, lookback=1440)
               apm_fleet(mode=ranking, metric=<CPU 지표>, domain_id=I.domain) → 같은 도메인 피어
S3-5 함수:    S4-C2가 고른 상위 서비스 s1..s3 각각에서 cpu_ms 상위 트랜잭션 최대 K=5건(조사당 예산 안)
               apm_transaction_profile(source_id, domain_id, txid, time_ms)  → profile.txt 전문(결과 파일) + SQL 목록
               정체 트랜잭션(S4-C3 후보)은 apm_active_detail(active_ref)
S3-6 비WAS:   class ∈ {batch, db, agent, unknown}이면 제니퍼를 부르지 않는다. polestar_alarm_history(같은 host, 30일)로 같은 시각대 반복 여부만 본다.
```

- 호출은 기존 `mcp_tool_client`의 **배치 호출**로 묶고 계층 단위로 병렬화한다. 도구 하나가 실패해도 나머지로 계속하며 실패는 `rca.collect_errors[]`에 구조화한다(침묵 폴백 금지).
- 제니퍼 호출은 게이트웨이의 기존 속도 제한·조사당 예산(`apm_transaction_profile`)을 따른다. 예산이 모자라면 서비스 순위가 높은 쪽부터 쓴다.
- 미들웨어 시그니처(S3-3 b)는 X-4 때문에 **`sre_agent/config/ml/process_classes.yaml`에 사본을 둔다**. 정본은 `config/middleware_signatures.yaml`이고, 두 파일이 같은지는 루트 `scripts/`의 동등성 검사로 고정한다(`catalog_diff.py`와 같은 자세). 대안은 G-13.

#### S4 분석 — 무엇을 어떤 방법(통계·ML)으로

| 분석 | 입력 | 방법 | 출력(결정적 값) | 라이브러리 · 단계 |
|---|---|---|---|---|
| **S4-A 이상 확인** — 「이 100%가 평소와 다른가」 | D-P2 cpu 시간 시계열(3주) | ① **robust STL**(일 24h · 주 168h) 분해 → 잔차 `r` ② 척도 점수 `z = \|r − med(r)\| / (1.4826·MAD(r))` ③ **계절 naive 비교**: 지난 3주 같은 요일·시각 값의 중앙값 대비 ④ **변화점** PELT(ruptures · 순수 CUSUM 폴백)로 수준 이동 시각 `t0` | `anomalous`(z ≥ 3.5) · `pattern ∈ {spike, sustained, recurring}` · `t0` · `typical_at_this_hour` | statsmodels·ruptures(`ml` extra) · 순수 폴백 · §5.2 단계 1 |
| **S4-B 프로세스 귀속** — 「누가 CPU를 쓰는가」 | D-P3 · D-P4 | ① 점유율 `share_i = cpu_i / Σcpu`(정렬과 같은 `p100cpu` 우선 · 없으면 `pcpu` — 두 값이 「전체 대비 %」인지 「코어당 %」인지는 미확인 · J-15) ② **지배 판정**: `share_1 ≥ 0.5` 또는 `cpu_1 / cpu_2 ≥ 2` ③ **CPU 성분**(mpstat): `%steal ≥ 10` → 가상화 경합 · `%iowait ≥ 20` → IO 대기(CPU 아님) · `%sys / %usr ≥ 1` → 커널 경로(락·컨텍스트 스위치) ④ 코어 편중: 한 코어만 100%면 단일 스레드 루프 의심 | `dominant_process` · `share` · `cpu_component` · `single_core_hot` | 순수 Python(결정적) |
| **S4-C1 부하 정규화** — 「TPS가 늘어서인가, 같은 TPS에 CPU를 더 쓰는가」 | D-J3 `process_cpu_pct` · D-J4 TPS(평소 구간 7일 · 사건 구간) | 평소 구간에서 **강건 회귀** `cpu = a + b·TPS`(Theil–Sen · 순수 폴백 Huber) 적합 → 사건 구간 잔차 `e = cpu − ŷ`를 평소 잔차 MAD로 척도화 `z_e` · TPS 자체의 척도 점수 `z_tps` | `z_tps` · `z_e` · `cpu_per_tx` 평소 대비 배율 | sklearn `TheilSenRegressor`(`ml` extra) · 순수 Theil–Sen 폴백(중앙값 기울기) · **신규 — PRISM 내부/외부 속성 분해(§3.4 P-21)·Squeeze 외부 원인 판정의 결정적 단순형** |
| **S4-C1′ GC 기여** | D-J3 `gc_time_usage_pct` · `heap_used_mb/heap_committed_mb` | 게이트웨이 `was_signals`(`was_gc_stall`·`was_heap_pressure`) **그대로 소비** · 추가로 `gc_time_usage_pct`와 `process_cpu_pct`의 사건 구간 순위 상관(Spearman) | `gc_driven`(신호 有 ∧ ρ ≥ 0.6) | 재구현 금지(D-274 ⑤) · 상관만 신규 |
| **S4-C2 서비스 국소화** — 「어느 서비스(URL) 몫이 늘었나」 | D-J5 사건·평소 서비스별 {호출 수, **CPU 시간 `total_cpu_ms`**(v5 · J-13 해소), 총 수행시간(보조)} | **Adtributor식 몫 변화**: 평소 몫 `p_s`, 사건 몫 `q_s` → 설명력 `EP_s = (A_s − F_s) / (A − F)`(A 실제·F 기대 = 평소 몫 × 사건 총량) · 놀람도 `S_s = JS(p_s, q_s)` → `EP_s ≥ 0.1`인 서비스를 놀람도 순으로 상위 3 · 다차원(인스턴스×서비스×업무)으로 넓히면 Squeeze(§5.4 R3) | `top_services[{service, EP, surprise, calls_ratio, time_per_call_ratio}]` | 순수 Python · **§5.4 R3 Squeeze의 1차원 선행판** |
| **S4-C3 정체·루프 트랜잭션** | D-J6 실행 중 요청 | `cpu_ms / elapsed_ms ≥ 0.8` ∧ `elapsed_ms ≥ 60초` → **CPU 루프 후보**(대기가 아니라 계산 중) · 같은 실행 모드 다수 정체는 게이트웨이 `was_thread_pool_exhaustion` 소비 | `runaway_tx[{service, txid, elapsed_ms, cpu_ms, active_ref}]` | 순수 Python · 임계는 설정 |
| **S4-C4 메서드 핫스팟** — 「어떤 함수가 문제인가」 | D-J8 profile.txt(표본 K건) · SQL 목록 | **[신규 X-3] 결정적 프로파일 파서**: 프로파일 줄 → `(depth, method, elapsed_ms, cpu_ms?)` 트리 → **자기 시간**(self = 자신 − 자식 합) → 표본 전체에서 메서드별 Σself·출현 표본 수 → 순위. SQL 줄은 메서드가 아니라 `sql` 버킷으로 합산(SQL 지연은 DB 원인 분기로 보냄) | `hot_methods[{method, self_ms_sum, samples_hit/K, share}]` · `sql_share` | 순수 Python · **profile.txt 실형식 미확인(J-14) — 로컬 녹화본은 `domain_not_connected`뿐** · **[v5] 입력·방법을 §R.9(F-1 구조화 파서 · F-2 실행 중 요청 표본 · F-3 핫 스레드 · 합의 집계)로 대체** |
| **S4-D 변경 정렬** — 「직전에 무엇이 바뀌었나」 | D-P5 · D-J10 · `t0`(S4-A 또는 D-J3 변화점) | 변경 시각 `t_c ∈ [t0 − 60분, t0]`이면 후보 · 피어가 있으면 **이중차분(DiD)**: 변경 인스턴스의 `cpu_per_tx` 변화 − 비변경 피어의 변화 · `apm_change_impact` 전후 비교값 병기 | `change_candidates[{t_c, kind, target, did_effect}]` | 순수 Python · §5.4 R1-4 |
| **S4-E 피어 비교** — 「이 인스턴스만인가, 도메인 전체인가」 | D-J11 | 같은 도메인 인스턴스들의 CPU 지표 분포에서 대상의 척도 점수(중앙값·MAD) · 대상만 이탈(z ≥ 3)이면 **국지**, 대다수가 함께 오르면 **공통**(부하·공유 의존) | `scope ∈ {local, common}` | 순수 Python · FUNNEL·MicroRCA 원리(§3.4) |
| **S4-F 유사 사례** | D-K1 | TF-IDF(문자 n-gram) 코사인 × 시간 감쇠 `e^(−α·Δdays)` + 증거 지문 Jaccard(원인 분류·프로세스 분류·`was_signals` 집합) | `similar[{date, host, cause, action, sim}]` 상위 3 · **「과거 유사 사례」로만 표기** | sklearn(`ml` extra) · §5.5 |
| **S4-G 강건 순위**(전 후보 공통) | 위 지표들의 사건 전·후 | BARO RobustScorer(사건 전 중앙값·IQR 척도 → 사건 후 최대 편차) + **의무 기준선** max-\|Z\|·알람 건수·Dummy 병렬 | `metric_rank` · `baseline_rank` · `agreement` | 순수 Python · §5.4 R2 · I-8 |

- **[v6] G5 장애 분석과의 관계**: 위 표는 `run_service`에서 도는 **판정 필수 경량판**이다. 같은 질문의 넓은 판은 G5 `ml_service`가 S3와 병행해 만든다(R.10.1).
  - S4-A의 광역판(연결 엔티티 전 지표)은 GA-1이다.
  - S4-C2의 다차원판(인스턴스 × 서비스 × 업무)은 GA-2다.
  - S4-F의 임베딩 하이브리드판은 GA-3이다.
  - 그밖에 과거 연관 조회 GA-4 · 학습 원인 순위 GA-5 · 예측 구간 GA-6이 있다.
  - GA 출력은 S5의 보조 조건·보조 후보로만 들어간다(I-11). G5가 없어도 이 표만으로 결론이 난다.
- **ML이 「판정」을 하지 않는 이유**: 위 출력은 전부 수치·순위다. 원인 분류는 S5 규칙표가 이 수치를 읽어 정한다(§4.1 · D-035). 지도학습 모델(F2c GBDT)은 확정 라벨이 쌓인 뒤 S5의 **보조 순위**로만 들어온다(M7c).
- **해상도 한계를 결론에 실어 보낸다**: 폴스타 `_h`는 60분 미만 스파이크를 평활화한다. S4-A가 `anomalous=false`인데 알람이 100%였다면 「시간 집계에서 보이지 않는 단기 스파이크」로 표기하고, 분 해상도 원천(D-J3 5분 · D-R1)을 우선한다.

#### S5 결론 판정 — 원인 분류 표 (`config/ml/rca_rules.yaml` · [신규 X-6])

> 위에서부터 평가해 **조건을 모두 만족한 분류를 전부** 후보로 올리고, 확신도 → 규칙 우선순위 → S4-G 점수 순으로 정렬한다. 어느 것도 성립하지 않으면 `CPU-0 판정 불가`다. 결론은 **후보 순위**이지 단정이 아니다.

| 분류 | 성립 조건(전부) | 확신도 | 결론 문장 틀(코드가 채움) |
|---|---|---|---|
| **CPU-1 가상화 경합** | `cpu_component=steal` | high | 「게스트 내부가 아니라 하이퍼바이저 자원 경합이다(steal X%). 같은 물리 호스트의 다른 VM을 확인해야 한다」 |
| **CPU-2 정기 작업** | class ∈ {batch, agent} ∧ (`pattern=recurring` ∨ 지난 30일 같은 시각대 알람 ≥ 3회) | high | 「정기 작업 P(사용자 U)가 매일 HH시에 CPU를 점유한다」 |
| **CPU-3 비WAS 단일 프로세스 폭주** | `dominant_process` ∧ class ∉ {WAS, batch, agent} ∧ `pattern ≠ recurring` | medium | 「P가 CPU X%를 점유한다(평소 이 시각 Y%)」 |
| **CPU-4 트래픽 증가형** | class=WAS ∧ `z_tps ≥ 3` ∧ `\|z_e\| < 2` | high(`scope=common`) / medium(`local`) | 「TPS가 평소 대비 N배이고 TPS당 CPU는 평소와 같다 — 부하 증가가 원인이다. 유입 상위 서비스: s1·s2」 |
| **CPU-5 GC 과부하형** | class=WAS ∧ `gc_driven` | high(`was_gc_stall` strong) / medium | 「GC 시간 비중 X% · 힙 Y% — CPU가 GC에 쓰이고 있다」 |
| **CPU-6 배포·변경 기인 효율 저하** | class=WAS ∧ `z_e ≥ 3` ∧ `\|z_tps\| < 2` ∧ `change_candidates` 비어 있지 않음 ∧ (피어 있으면 `did_effect > 0`) | high(DiD 성립) / medium | 「t_c 소스 변경 감지 뒤 TPS당 CPU가 N배다. 몫이 는 서비스 s1 · 상위 메서드 m1(자기 시간 X%)」 |
| **CPU-7 코드 핫스팟(변경 무관)** | class=WAS ∧ `z_e ≥ 3` ∧ `top_services` 비어 있지 않음 ∧ 변경 후보 없음 | medium | 「서비스 s1의 호출당 수행시간이 N배 · 상위 메서드 m1」 |
| **CPU-8 CPU 루프 트랜잭션** | class=WAS ∧ `runaway_tx` ≥ 1 | high(`single_core_hot`도 성립) / medium | 「트랜잭션 txid(서비스 s, 경과 T초, CPU 시간 비율 R)가 계산 루프 상태다」 |
| **CPU-9 WAS 미관측** | class=`was_unmonitored` | low | 「WAS로 보이는 프로세스지만 제니퍼에 등록되지 않았다 — 함수 단위 분석 불가」 |
| **CPU-0 판정 불가** | 위 모두 불성립 또는 필수 원천 결손 | — | 「결손 원천: …」(`collect_errors` 나열) |

- **SQL 비중 예외**: S4-C4 `sql_share ≥ 0.6`이면 WAS CPU 원인이 아니라 DB 대기다. 이때는 분류를 내지 않고 R.4 S-D(DB 계열)로 넘기며 그 사실을 결론에 적는다.
- **기준선 병기**(I-8): 결론마다 「max-\|Z\| 기준선은 같은 후보를 1위로 냈는가」를 `agreement`로 붙인다.

#### S6 조치 도출 — 결정적 표 (`domain/remediation.py` 확장)

| 분류 | 조치 후보(가역성 순) | 위험도 | 검증(도구) | 롤백 |
|---|---|---|---|---|
| CPU-1 | 가상화 관리자에게 호스트 재배치·예약(reservation) 검토 요청 | low | `mpstat` steal 하락 | 해당 없음(요청) |
| CPU-2 | 작업 일정을 업무 시간 밖으로 이동 · `nice` 우선순위 조정 요청 | low | 다음 실행 시각 CPU 추세(`polestar_metric_trend`) | 일정 원복 |
| CPU-3 | 프로세스 소유 팀 확인 → 원인 파악 전 종료 금지 · 스레드/스택 덤프 채취 | low→medium | `ps` 점유율 | 해당 없음(채취) |
| CPU-4 | 기존 `was_service_queuing` 후보(PLC 상한 임시 조정 · 인스턴스 증설 검토) | medium | `apm_app_health` 응답·거절률 | PLC 원복 |
| CPU-5 | 기존 `was_heap_pressure`·`was_gc_stall` 후보(힙 덤프 → 누수 분석 · 힙/GC 옵션 검토) | low→high | `apm_runtime_health` GC 비중 | 옵션 원복 후 재기동 |
| CPU-6 | 직전 배포본 롤백 검토 · 변경 담당에게 상위 메서드 m1·서비스 s1 전달 | high(롤백) / low(전달) | `apm_change_impact`·`apm_runtime_health` CPU 회복 | 재배포 |
| CPU-7 | 서비스 s1·메서드 m1을 개발팀에 전달 · 호출 급증이면 호출원 확인 | low | `apm_status_stats` 호출당 시간 | 해당 없음 |
| CPU-8 | 제니퍼 콘솔에서 해당 트랜잭션 인터럽트 검토 · 직전 스레드 덤프 채취 | medium | `apm_active_services`에서 소멸 · CPU 하락 | 인터럽트는 되돌릴 수 없음 — 대상 기록 후 업무 담당과 재처리 확인 |
| CPU-9 | 제니퍼 에이전트 적용 요청 · 임시로 `jstack` 채취 요청 | low | — | — |

- 기존 규칙을 그대로 쓴다: **고위험 × 저신뢰는 권고하지 않고 「검토 필요」로만 표기**한다(`remediation.py` 머리). 실행 경로는 없다(D-003·D-011).

#### S7 LLM 보고서 — 무엇을 넘기고 무엇을 금지하는가

**넘기는 것**: RCA 묶음 JSON 하나(`rca_bundle` · 크기 ≤ 8KB · §4.4 반환 계약 형식). 원시 시계열·프로파일 전문은 넘기지 않는다(§4.1 · I-7).

```json
{
  "scope": {"host": "GP-APP-01", "db_id": "polestar_cm_gp", "t_ref": "2026-10-13T14:07:00", "kind": "cpu"},
  "facts": {
    "host_cpu": {"anomalous": true, "z": 6.1, "pattern": "sustained", "t0": "13:52", "typical_at_this_hour": 38.0},
    "process": {"dominant": {"name": "java", "pid": 41213, "share": 0.86, "class": "WAS", "instance": "GP_SVC_01", "via": "pid"},
                "cpu_component": "user", "captured_lag_s": 4},
    "was": {"z_tps": 0.4, "z_e": 5.2, "cpu_per_tx_ratio": 3.1, "gc_driven": false, "scope": "local",
            "top_services": [{"service": "/acct/inquiry.do", "EP": 0.71, "time_per_call_ratio": 4.0}],
            "hot_methods": [{"method": "com.x.AcctDao.mergeRows", "share": 0.62, "samples_hit": "5/5"}],
            "change_candidates": [{"t_c": "13:48", "kind": "source_change", "did_effect": 2.7}]}
  },
  "conclusions": [{"rank": 1, "class": "CPU-6", "confidence": "high", "sentence": "...", "agreement": true}],
  "actions": [{"class": "CPU-6", "action": "...", "risk": "high", "verify": "...", "rollback": "..."}],
  "similar": [{"date": "2026-08-02", "cause": "...", "action": "..."}],
  "limitations": ["폴스타 지표는 시간 집계", "profile 표본 5건"],
  "collect_errors": []
}
```

**LLM 지시(고정 · `investigation_guidance`에 블록 추가)**
1. 순서와 양식은 고정이다: ① 한 줄 요약 ② 영향(대상·시각·지속) ③ **원인 후보**(`conclusions` 순서 그대로) ④ 근거(`facts` 수치 인용) ⑤ 조치(`actions` 그대로 · 위험도·검증·롤백 포함) ⑥ 과거 유사 사례 ⑦ 한계·미확인.
2. `conclusions`의 순서·확신도를 바꾸지 않는다. 새 원인을 만들지 않는다. 추가 근거가 필요하면 도구(§4.4 표면 ②)를 부르고, 그 결과는 「추가 확인」 절에만 적는다.
3. 숫자는 `rca_bundle`에 있는 값만 쓴다. 확률 표현(「90% 확률로」)을 쓰지 않는다(I-5).

**사후 검사 [신규 X-7]**: 서술 안의 숫자·식별자(서비스·메서드·PID·시각)를 추출해 `rca_bundle`과 대조한다. 묶음에 없는 값이 있으면 그 문장에 `[검증 안 됨]`을 붙이고 감사에 남긴다. 위반이 상한(초기값 2건)을 넘으면 서술을 버리고 **S5·S6 표만 렌더**한다(사유 표기). 이 검사가 §6.1 「증거 폐포율(ECR)」 채점의 운영판이다.

**모델**: 조사 LLM은 기존 설정 그대로다(내부망 FabriX · 로컬은 MLX). 운영 데이터를 외부 LLM으로 보내지 않는다(D-120). 실 LLM 평가는 D-127·D-240 규칙을 따른다.

#### S8 전달·피드백
- **전달**: D-137 「즉시통보 + 후속 메시지」를 그대로 쓴다. 즉시통보에는 S5 1위 분류와 확신도 한 줄만 싣고, 후속 메시지에 S7 보고서를 싣는다. 조사 화면·관제 대시보드는 `rca_bundle` 원본(표)과 서술을 함께 보여 준다.
- **피드백**: 운영자가 「원인 맞음/틀림 + 실제 원인 분류·엔티티」를 남긴다 → `FeedbackStore`의 선택 필드 `root_cause{entity, layer, kind}`(G-9 · M1 CU-6)에 쌓인다. 이것이 S5 규칙표 임계 보정과 F2c 지도학습의 라벨이다. **사후 기입이므로 피처로는 쓰지 않는다**(KL-3).
- **감사**: 조사 `done` 레코드에 `rca_bundle` 해시·분류·조치·LLM 검사 결과를 남긴다(§4.5 감사 상세화). 1시간 TTL 잡 메모리에만 두지 않는다.

### R.4 다른 장애 유형 — 같은 8단계, 다른 데이터·분석

> CPU 사례와 같은 틀(S1~S8)을 쓰고 S3·S4·S5만 갈아 끼운다. 각 행의 상세 규칙은 `rca_rules.yaml`의 유형별 블록으로 둔다. 구현 순서는 R.6을 따른다.

| 유형(트리거) | S3 수집(핵심 도구·명령) | S4 분석 | S5 대표 원인 분류 | S6 대표 조치 |
|---|---|---|---|---|
| **S-M 메모리 부족**(폴스타 memory) | `polestar_metric_trend(memory)` · 프로세스 `sort=mem` · L3 `free -m`·`vmstat 1 3`·`ps aux --sort=-%mem` · WAS면 `apm_runtime_health` | STL+MAD · 프로세스 RSS 점유 · 힙 계단식 상승(게이트웨이 `_leak_staircase`) · **고갈 ETA**(추세+conformal · §5.3 (a)) | 힙 누수형 · 비힙(네이티브·메타스페이스) 증가 · 캐시 정상 증가(사용 가능 메모리 충분) · 다중 프로세스 합산 | 힙 덤프 · 재기동 일정 · ETA 공지 |
| **S-F 파일시스템 포화**(폴스타 filesystem) | `polestar_metric_trend(filesystem)` · L3 `df -h`·`df -i` | 추세 + **고갈 ETA p10/p50/p90** · 변화점(급증 시각) | 로그 폭증(급증 시각 = 변경·오류 이벤트 직후) · 정상 증가(선형) · inode 고갈 | 기존 `inode_or_disk_full` 후보 · ETA 기반 증설 요청 |
| **S-W WAS 응답 지연**(제니퍼 이벤트 · `resourceType=apm.instance`) | `apm_app_health` · `apm_slow_transactions` · `apm_status_stats(kind=sql\|external_call)` · `apm_resource_pool` · `apm_active_services` · 호스트는 `polestar_metric_trend` 역조회 | 시간 분해 몫(cpu·sql·fetch·external) · S4-C2 서비스 국소화 · **관측 불일치**(호스트 정상 × 앱 이상 — §5.4 Gray Failure) | SQL 지연(→ DB) · 외부 호출 지연 · DB 풀 고갈 · 스레드 정체 · 호스트 자원 부족(→ S-CPU/S-M로 역전이) | 기존 `was_*` 조치 표 |
| **S-E WAS 오류 급증** | `apm_events(level=fatal)` · `apm_fleet(mode=events)` · `apm_change_impact` | 오류 유형별 건수 변화 · 변경 정렬 · 피어 비교 | 배포 기인 · 외부 의존 장애(전 인스턴스 공통) · 단일 인스턴스 이상 | 롤백 검토 · 의존 서비스 조사 전이 |
| **S-D DB 계열**(DB 프로세스 지배 · SQL 비중 ≥ 0.6) | 폴스타 호스트 지표 · `apm_status_stats(kind=sql)`(WAS 쪽에서 본 SQL 시간) · **DPM 없음** | 상위 SQL 몫 변화(WAS 관점) · 「고영향 SQL ≠ 원인 SQL」 표기(§5.4) | 특정 SQL 지연(WAS 관점) · DB 호스트 자원 · **DB 내부 원인 미관측** | DBA 검토 전달(SQL ID·호출 서비스) · DPM 연동 필요성 기록(G-3) |
| **S-N 네트워크·디스크 IO** | `polestar_metric_trend(disk_io)` · L3 `iostat -xz 1 2`·`ss -s`·`sar -n DEV 1 1` | STL+MAD · 변화점 · 같은 시각 다른 서버 동반 여부 | 장비·스토리지 공통(다수 서버 동반) · 단일 서버 | 인프라 담당 전이 |

### R.5 현행 대비 신규 구현 목록 — 이 흐름을 돌리려면

| ID | 대상 | 내용 | 계층 · 패키지 | 의존 | 플래그(기본 off) |
|---|---|---|---|---|---|
| RC-1 | `noise_gate` enricher → `investigation_payload` | 알람 시점 프로세스 스냅샷·L3 요지를 조사 payload로 전달(X-1) | noise_gate infrastructure/domain | 없음 | `INVESTIGATION_PROCESS_SNAPSHOT_FORWARD` |
| RC-2 | `sre_agent/application/rca_collect.py` | S3 분기 수집기(X-2) — PID→인스턴스 1순위 · 호스트 정합 2순위 · 실패 구조화 | sre_agent application | 기존 `mcp_tool_client` | `RCA_DETERMINISTIC_COLLECT` |
| RC-3 | `sre_agent/config/ml/process_classes.yaml` + 동등성 검사 | 미들웨어 시그니처 사본(X-4) | sre_agent config · 루트 scripts | 없음 | — |
| RC-4 | `sre_agent/domain/ml/{seasonal,changepoint,robust_rank}.py` | S4-A·S4-G(§7 M2·M3의 CU-8·CU-9·CU-15와 같은 것 — 중복 생성 금지) | domain(표준 라이브러리) + infrastructure 어댑터 | `ml` extra(폴백 有) | M2·M3 플래그 |
| RC-5 | `sre_agent/domain/ml/process_attribution.py` | S4-B 점유·지배·CPU 성분·코어 편중 | domain | 없음 | RC-2와 같음 |
| RC-6 | `sre_agent/domain/ml/load_normalized.py` | S4-C1 Theil–Sen 잔차(순수 중앙값 기울기 폴백) | domain | 없음 | 같음 |
| RC-7 | `sre_agent/domain/ml/dim_localize.py` | S4-C2 몫 변화(EP·JS 놀람도) — §7 M5 CU-28의 1차원 선행판 | domain | 없음 | 같음 |
| RC-8 | `apm_gateway` 프로파일 파서 + `apm_transaction_profile` 응답에 `methods[]` 추가 | S4-C4(X-3) · **파서는 게이트웨이 소유**(벤더 텍스트 형식은 어댑터가 안다 — D-274) · 계약은 추가만 | apm_gateway adapters/domain | 없음 | 게이트웨이 설정 |
| RC-8′(v5) | `apm_gateway` `apm_active_sample` 신규 도구 | F-2 실행 중 요청 반복 표본(§R.9.3) | apm_gateway application | 없음 | 게이트웨이 설정 |
| RC-9 | `sre_agent/domain/ml/profile_hotspot.py` | S4-C4 표본 간 자기 시간 집계·순위 | domain | 없음 | RC-2와 같음 |
| RC-10 | `sre_agent/domain/rca/decide.py` + `config/ml/rca_rules.yaml` | S5 원인 분류 표 평가(X-6) · 유형별 블록 | domain + config | 없음 | `RCA_DECISION_TABLE` |
| RC-11 | `sre_agent/domain/remediation.py` | S6 `CPU-*` 등 분류별 조치 행 추가(기존 행 불변) | domain | 없음 | 같음 |
| RC-12 | `briefing_builder` · `investigation_guidance` | `rca_bundle` 렌더 · LLM 고정 양식 지시 | application | 없음 | 같음 |
| RC-13 | `sre_agent/domain/rca/narrative_check.py` | S7 수치·식별자 대조(X-7) | domain | 없음 | `RCA_NARRATIVE_CHECK` |
| RC-14 | 피드백 선택 필드 | §7 M1 CU-6과 같음(G-9) | noise_gate · src | 없음 | — |

- RC-4·RC-7·RC-14는 기존 Wave의 CU와 **같은 산출물**이다. 새로 만들지 않고 그 CU를 앞당긴다.
- 전 항목이 **기본 off · off일 때 조사 결과·게이트 결정 비트 동일**(I-3)이다. `sre_agent` ↔ `src`·`noise_gate`·`apm_gateway` import 0(I-1)은 유지된다 — 제니퍼·폴스타는 MCP로만 부른다.
- 매뉴얼(D-255): RC-12로 조사 화면·통보 양식이 바뀌면 같은 작업에서 `src/static/manual/` 운영자 매뉴얼을 갱신한다.

### R.6 Wave 재배치 제안 — CPU→WAS 사슬을 먼저 (G-12)

v3의 G-2 권장 순서(M0→M1→M2→M3 폴스타 단독 → M4 → **M5 제니퍼(87 이후)**)는 「`apm_*` 코드 0건」을 전제로 했다. 그 전제가 사라졌으므로 다음 순서를 제안한다.

| 순서 | 묶음 | 내용 | 완료 판정 |
|---|---|---|---|
| ① | **M0 축소판** | J-1·J-3·J-7·**J-12~J-15**만 먼저 실측 | 데이터 카드에 실측값 · 미확인은 사유 |
| ② | **M-R1 결정적 사슬**(ML 의존 0) | RC-1·RC-2·RC-3·RC-5·RC-10(CPU 블록)·RC-11·RC-12·RC-13 — S4는 순수 Python 판정만(S4-B·S4-C3·S4-E · 게이트웨이 `was_signals` 소비) | 녹화 픽스처(폴스타·제니퍼)로 R.3 예시 사건이 `CPU-8`·`CPU-5`·`CPU-4` 각각으로 판정되는 종단 테스트 · off 비트 동일 |
| ③ | **M1 기반** | §7 M1 그대로(holmes 상향 · `ml` extra · 평가 하네스) | §7 M1 |
| ④ | **M-R2 통계 분석** | RC-4·RC-6·RC-7·RC-8·RC-9 → `CPU-4/6/7` 판정 활성화 | 합성 시계열·녹화 프로파일로 판정 분기 테스트 |
| ⑤ | M2·M3·M4 이후 | §7 그대로(ETA · 유사 사례 · 게이트 신호) | §7 |

- **[v5] 이 표는 §R.11 Wave 표(②′ 함수 단위 집계 · ③ M-G5 기반 추가)로 대체된다.**
- ②는 `ml` extra 없이 돈다 — 반입 일정(J-6)이 막지 않는다. 운영 효과는 §6.3 게이트(섀도 → 병합)로 판정한다. 즉 ②의 결론은 처음엔 `ml_evidence`/`rca_bundle`에만 싣고 기존 `root_cause_hypotheses` 순서를 바꾸지 않는다.

### R.7 이 절이 새로 여는 확인 사항·게이트

| ID | 내용 | 방법 | 막는 것 |
|---|---|---|---|
| **J-12** | 「컨테이너」의 실체 — 제니퍼 인스턴스(JEUS 컨테이너 등)인가, K8s 컨테이너인가. 운영 WAS가 컨테이너 플랫폼이면 PID→인스턴스 매핑 원천이 달라진다 | 운영 확인 | S3-3 |
| **J-13** | **[v5 해소 — §R.9.0 · `totalCpuTime`·`cpuTimePerTransaction` · `sort_by` 수용만 미확인]** `/api/status/application` 행에 **서비스별 CPU 시간** 필드가 있는가(없으면 S4-C2는 총 수행시간으로 대체) · `apm_status_stats(sort_by=…)`에 쓸 CPU 지표 이름 | 운영 제니퍼 녹화 1회 · `apm_metrics(mode=catalog)` | S4-C2 정밀도 |
| **J-14** | `/api/transaction/profile.txt` 실형식(메서드 줄의 경과·CPU 칸 유무·들여쓰기 규칙) — 로컬 녹화본은 `domain_not_connected`뿐 | 운영 녹화 3건(마스킹 후 반출 · D-219) | RC-8 |
| **J-15** | `/api-v2/manage/instance?processId=` 응답이 운영 제니퍼 버전에서 동작하는가 · 폴스타 프로세스 API의 `pid`와 같은 PID 공간인가(컨테이너 PID 네임스페이스면 다르다) · 폴스타 `p100cpu`·`pcpu`의 기준(전체 대비 % / 코어당 %) | 운영 확인 1건 | S3-3 (a) · S4-B 점유율 |
| **J-16** | 폴스타 프로세스 **이력** 원천이 있는가(있으면 「평소 이 시각 상위 프로세스」를 비교할 수 있다) | 폴스타 벤더·스키마 확인 | S4-B 평소 비교(현재는 불가로 표기) |

| G | 질문 | 선택지 | 권장 · 근거 |
|---|---|---|---|
| **G-12** | 구현 순서 | (가) R.6 — 결정적 CPU→WAS 사슬(M-R1)을 M1보다 먼저 (나) v3 G-2 순서 유지(M5 제니퍼는 M4 뒤) | **(가)** — `apm_*` 24종이 이미 있어 G-2 (가)의 근거(코드 0건)가 사라졌다. M-R1은 ML 의존이 0이라 반입 대기와 무관하고, 지금 LLM 지침으로만 존재하는 경로(X-2)를 코드로 고정하는 것이 운영 가치가 가장 크다 |
| **G-13** | 미들웨어 시그니처를 `sre_agent`가 쓰는 방식 | (가) `sre_agent/config/ml/`에 사본 + 동등성 검사 (나) `mcp_server`에 프로세스 분류 도구 추가(분류를 읽기 경계에서) (다) 제니퍼 PID 매핑만 쓰고 시그니처는 쓰지 않음 | **(가)** — D-118 경계 유지 · 판정은 소비자 쪽. (다)는 제니퍼 미등록 WAS·배치를 「unknown」으로 뭉갠다(CPU-2·CPU-9 분류 불가) |
| **G-14** | 프로파일 파서 소유 **[v5 해소 — 사용자 지시로 (가) 확정 + 세 경로 확장 · §R.9]** | (가) `apm_gateway`(벤더 형식은 어댑터 소관 · 응답에 `methods[]` 추가) (나) `sre_agent`가 발췌 텍스트를 파싱 | **(가)** — D-274(벤더 판정은 게이트웨이 한 곳) · 발췌는 앞 N줄 절단이라 (나)는 깊은 호출의 자기 시간을 잃는다 |

### R.8 SMS(폴스타) × APM(제니퍼) 알람 복합 분석 (v5 · 2026-10-13)

> **v5 지시(원문)**: *"알람이 폴스타만 예시로 되어 있는데 제니퍼에서도 알람이 발생될 수 있고 시간대와 연관관계를 확인하여 SMS, APM 알람을 복합적으로 분석해야 한다. 관련 내용을 반영하라."*
> **용어**: SMS는 **폴스타**(서버·시스템 관리 · 인프라 알람)이고, APM은 **제니퍼**(WAS 이벤트)다.
> **노이즈 게이트와 목적이 다르다**: D-317(`plans/144`)의 사건 상관은 **알림을 줄이는 것**이다. 원인 쪽을 남기고 증상 쪽을 DASHBOARD로 강등한다. 이 절은 **원인을 판정하는 것**이다. 두 기능은 사건 키(`zone|host_key`)와 규칙표 어휘를 함께 쓴다. 다만 RCA는 다음 셋이 게이트와 다르다.
> - 양방향을 모두 다룬다.
> - 사건 멤버 전부를 본다.
> - 강등 판단을 바꾸지 않는다.

#### R.8.0 현행 실측 — 두 알람이 어떻게 들어오고 어디서 만나는가

| 부품 | 상태 | 위치(실측) |
|---|---|---|
| 폴스타 알람 수신 | [구현] TCP 9100 → Redis `alarm:raw` | `noise_gate/alarm_server/` |
| **제니퍼 이벤트 폴링** | [구현] 게이트웨이가 `/api/dbsearch/event`를 도메인별로 폴링한다. 폴링 창은 60초씩 겹치고, Redis `SET NX`로 중복을 막으며, 실패하면 백오프한다. 결과는 `alarm:raw`로 발행한다(`resourceType=apm.Instance` · `raw.apm.event_type_norm` · `raw.apm.was_signals` · 인스턴스→hostname 역해소와 `match_confidence`) | `apm_gateway/apm_gateway/application/poller.py:194-275` |
| 제니퍼 푸시 | [부분] Java 어댑터 `jennifer_adapter`(v0.2.0 · `AlarmEventAdapter` · TCP 9110 가칭)는 있다. **게이트웨이 쪽 수신부는 없다** → 지금 지연은 폴링 주기에 겹침 창을 더한 만큼이다 | `apm_gateway/jennifer_adapter/` |
| 이벤트 유형 → WAS 시그니처 | [구현 · 11종] `SERVICE_QUEUING`·`OUTOFMEMORY`·`MAYBE_GC_TIME_DELAY`·`HIGH_RATE_FAIL` 등을 `was_*`로 매핑한다. **CPU 계열 이벤트 유형은 매핑이 없다.** 운영 유형 명칭도 미확정이다(U-1·U-13) | `apm_gateway/.../adapters/jennifer/fields.py:60-72` |
| 조회용 이벤트 버퍼 | [구현] 보존 60분 | `apm_gateway/.../application/event_buffer.py:120` |
| **사건 상관(D-317)** | [구현 · 기본 off] 같은 `zone\|host_key`의 비해소 알람은 **규칙과 무관하게** 한 사건(Episode)의 멤버가 된다. 규칙 3행은 아래와 같다. 모드는 `NOISE_CROSS_SOURCE_MODE` off/shadow/annotate/enforce다. enforce 조건은 shadow 2주 · 거짓 강등률 ≤2% · lift ≥3이다. 제니퍼발 조사는 사건당 1회이고 사건 id를 조사 payload에 싣는다. **사건 상태는 게이트 메모리에만 있다** | `noise_gate/domain/cross_source.py:253-420` · `config/cross_source_rules.yaml` |
| 결정 감사 | [구현] 두 소스의 판정을 1줄씩 JSONL로 남긴다(억제 판정 포함) → **운영 개시 이후의 결합 알람 이력은 이 파일이 유일한 단일 원천이다** | `noise_gate/infrastructure/decision_store.py` |
| 과거 이력 | 폴스타 `cmm_alarm`(90일+ · `polestar_alarm_history`) · 제니퍼 `/api/dbsearch/event` 기간 조회(`apm_events` · 보존 미확인 J-17) | — |

D-317 규칙표 3행(`config/cross_source_rules.yaml` — 머리에 **「설계 가정 · W6 리플레이로 보정」**이라고 적혀 있다):

| id | 원인 → 증상 | 창(증상 기준 원인 시각) | 동작 |
|---|---|---|---|
| `host_resource_to_was_latency` | 폴스타 cpu·memory → 제니퍼 `was_service_queuing`·`was_gc_stall`·`was_heap_pressure` | −600초 ~ +60초 | 증상 강등 |
| `host_down_to_was_all` | 폴스타 서버 가용성 알람(이름 미확정) → 제니퍼 전부 | −900 ~ +120 | 비활성 |
| `was_oom_to_process_down` | 제니퍼 `was_heap_pressure` → 폴스타 process | −300 ~ +60 | link만(역방향 강등 금지 · G-3) |

**끊긴 곳 Y-1~Y-6**

| # | 끊긴 곳 | 결과 |
|---|---|---|
| Y-1 | §R.1~R.7은 **폴스타 알람만** 트리거로 그렸다. 제니퍼 이벤트로 시작한 사건은 R.4 S-W로만 간다 | 같은 사건이라도 시작 소스에 따라 다른 사슬을 탄다. 호스트 CPU 사슬과 합류하지 못한다 |
| Y-2 | 제니퍼 CPU 계열 이벤트가 `was_signals`에 매핑되지 않는다 | 사건 규칙 선택자(`was_kinds`)로 고를 수 없다. 사건 멤버로는 묶이지만 규칙 링크는 생기지 않는다 |
| Y-3 | 사건 상관은 기본 off이고, 사건 상태가 게이트 메모리에만 있다. 조사 payload에는 사건 id만 실린다 | RCA가 사건 멤버(어느 소스의 무엇이 언제)를 받을 경로가 없다 |
| Y-4 | 두 소스의 시각 기준이 다르다. 폴스타 알람은 DB 벽시계(naive 로컬)이고, 제니퍼는 epoch ms를 `runtime.timezone`으로 바꾼 값이다. 시계 차이는 측정한 적이 없다 | 수십 초 차이로 「어느 쪽이 먼저였나」가 뒤집힐 수 있다 |
| Y-5 | 규칙 창(600/60초)과 방향은 문헌에서 가져온 초기값이다. 운영 지연 분포로 보정하지 않았다 | 창이 틀리면 연관을 놓치거나 우연한 동시 발생을 연관으로 묶는다 |
| Y-6 | 「어느 쪽이 먼저였나」 「매번 같이 오는가」 「특정 시간대에 몰리나」를 계산하는 층이 없다 | 복합 사건의 원인 방향이 서술로만 나온다 |

#### R.8.1 8단계에 끼우는 위치

```
 [폴스타 알람] ── alarm_server(9100) ──┐
                                       ├─► Redis alarm:raw ─► noise_gate 판정 ─► 사건(zone|host_key) 멤버 누적
 [제니퍼 이벤트] ── apm_gateway 폴러 ──┘                         │                (D-317 · 모드 off여도 RCA는 S3-T로 재구성)
   (푸시 수신부는 부재 · RC-21)                                   ▼ PAGE(어느 소스든 · 사건당 조사 1회)
 S1 트리거 ─ 시작 소스와 무관하게 같은 RcaScope{host, t_ref, kind} ─ 제니퍼발이면 instance→host 역해소 값 사용
 S2 사건 시점 고정 ─ 프로세스 스냅샷(RC-1) + 사건 스냅샷(RC-16: 멤버의 소스·유형·시각·티어)
 S3 계층별 수집 ─ R.3 S3-1~S3-6 + **S3-T 병합 타임라인 수집**
 S4 분석 ─ R.3 S4-A~G + **S4-T 시간·연관 분석(T1~T6)**
 S5 결론 ─ CPU-*/S-* 분류 + **복합 분류 XS-1~XS-6(방향·계층 순서)**
 S7 보고 ─ 양식 ③ 앞에 **「SMS·APM 타임라인」 표** 삽입
```

- **kind 결정(S1)**: 제니퍼발이면 `event_type_norm`을 `sre_agent/config/ml/event_layers.yaml`(RC-15)로 계층·kind에 매핑한다. CPU 계열이면 `kind=cpu` → **R.3 사슬로 합류**한다. `was_*` 계열이면 R.4 S-W·S-E로 간다. 어느 쪽이든 S3-T·S4-T는 항상 돈다.
- **사건 범위(S2)**: payload에 사건 스냅샷이 있으면 그 멤버를 출발점으로 쓴다. 없으면(모드 off · 게이트 재기동으로 사건 유실) S3-T가 두 소스를 직접 조회해 재구성한다. 사건 스냅샷은 **편의일 뿐이고 필수 입력이 아니다** — RCA가 게이트 모드에 종속되지 않게 한다.

#### R.8.2 S3-T 병합 타임라인 수집 — 무엇을 어디서

```
창: [t_first − 30분, t_ref + 15분]   t_first = 사건 최초 멤버 시각(없으면 t_ref)
T-1 폴스타 알람:   polestar_incident_alarms(host, reference_time=t_ref, lookback)
                   + 토폴로지 root·연관 서버(plans/91 1-3 · 알람 1건씩만 — evidence_prefetch 규약)
T-2 제니퍼 이벤트: apm_instance_map(host) → 인스턴스 I1..In
                   apm_events(instance=Ii, reference_time=t_ref, lookback, level=warning)
                   apm_fleet(mode=events, domain_id=Ii.domain)        ← 같은 도메인 다른 인스턴스 동반 여부
T-3 변화점용 지표: polestar_metric_trend(cpu|memory, h)  — 시간 집계(t0는 시 단위)
                   apm_runtime_health(Ii) — 5분 · apm_app_health(Ii) — 1분 · (선택) prom_metric_range — 분
T-4 과거 통계:     G5 ml_service 조회 cooccur(유형쌍)·time_profile(host·유형) — RC-19가 미리 만든 표(없으면 생략·사유 표기)
```

- 이벤트는 공통 레코드 `TimelineEvent{t_utc, t_src_raw, source∈{polestar,jennifer}, layer, kind, entity, severity, name, origin∈{alarm,metric_t0,change}}`로 정규화한다. 벤더 어휘(유형명·필드명)는 이 정규화 함수까지만 둔다(`correlation.py`의 벤더 중립 규약 계승).
- 계층(layer)은 6단이다: `infra_host`(CPU·메모리·FS·IO) · `process` · `jvm`(GC·힙·스레드) · `service`(TPS·응답·오류) · `dependency`(SQL·외부 호출·DB 풀) · `change`(배포·설정).

#### R.8.3 S4-T 시간·연관 분석 — T1~T6

| 단계 | 질문 | 방법 | 출력 | 실행 위치 |
|---|---|---|---|---|
| **T1 시각 정렬** | 두 소스의 시각을 같은 축에 올렸는가 | ① 전부 UTC epoch로 바꾼다 ② 소스별 시계 오프셋 `δ_src`를 보정한다(J-17 실측값 · 미측정이면 `δ_max` 초기값 60초를 불확실 구간으로 둔다) ③ 해상도를 기록한다(폴스타 알람 초 · 폴스타 지표 시 · 제니퍼 이벤트 ms · 런타임 5분 · 앱 1분) | `t_utc` · `t_uncertainty_s` | run_service |
| **T2 엔티티 연결** | 두 알람이 같은 대상인가 | D-317 공통 호스트 키(`host_key_strength` strong/weak/none) → 인스턴스(`apm_instance_map.match_confidence`) → 도메인·업무 | `link_strength` | run_service |
| **T3 계층별 최초 발생** | 어느 계층이 먼저 움직였나 | 계층마다 ① 최초 알람 시각 `t_first(layer)` ② 지표 변화점 `t0(layer)`(PELT · 각 원천 해상도 그대로)을 구한다. 소스 간 지연은 `lag = t_first(APM) − t_first(SMS)`이고 `t_uncertainty`를 병기한다 | `layer_order[]` · `lag_s ± u` | run_service |
| **T4 방향 판정**(결정적 표) | 원인 → 증상 방향은 | 아래 「방향 규칙」 · **시간 순서는 필요조건일 뿐이다**: 원인 후보는 `t0(원인) ≤ t0(증상) + u`를 만족해야 한다. 순서만으로 인과를 말하지 않는다(§0.2 ④) | `direction{from, to, basis[], confidence}` | run_service |
| **T5 과거 연관 통계**(ML · 오프라인) | 이 조합은 평소에도 같이 오는가 · 보통 몇 분 차이인가 · 어느 시간대에 몰리나 | 아래 「T5 상세」 | `cooccur{lift, p_b_given_a, lag_median, lag_iqr, n}` · `time_pattern` | **G5 `ml_batch`** 주 1회 → `ml_service` 조회 |
| **T6 산출** | 운영자에게 무엇을 보이나 | 병합 타임라인 표 · 계층 순서 · 방향과 근거 · 이번 `lag`과 과거 지연 분포 비교(「평소 지연 범위 안/밖」) | `rca_bundle.facts.timeline` | run_service |

**방향 규칙(T4)** — 위에서부터 평가하고, 성립한 첫 행이 방향이다. 나머지 성립 행은 `basis`에 남긴다.

| # | 조건 | 방향 | 근거 |
|---|---|---|---|
| D1 | S4-B 지배 프로세스가 제니퍼 인스턴스 I다(PID 매핑 · R.3 S3-3 (a)) | **APM → SMS**(앱이 호스트 자원을 소비) | 프로세스 귀속이 시간 순서보다 강한 증거다 |
| D2 | `cpu_component=steal` | **하이퍼바이저 → 둘 다 증상** | R.3 CPU-1 |
| D3 | 지배 프로세스가 비WAS(batch·db·agent·unknown)이고 APM 이상이 `t0(infra)` 뒤에 온다 | **SMS → APM**(자원 경합으로 WAS 지연) | D-317 `host_resource_to_was_latency`와 같은 방향 |
| D4 | 같은 도메인의 다수 인스턴스가 동시에 이상이고(`apm_fleet` · S4-E `scope=common`) 각 호스트 지표는 정상이다 | **공통 의존 → APM** | R.4 S-D·S-E로 전이 |
| D5 | APM만 이상이고 호스트는 정상이다 | **앱 단독**(관측 불일치 · §5.4 Gray Failure) | — |
| D6 | SMS만 이상이고 APM은 정상이다 | **인프라 단독** | — |
| D7 | 위 모두 불성립 · `\|lag\| < u` · 시각 원천 결손 | **판정 불가** | 사유 표기 |

- **게이트 규칙과 방향이 어긋나는 경우를 기록한다.** D1이 성립하면 앱이 원인이고 호스트 CPU 알람이 증상이다. 그런데 게이트 규칙 `host_resource_to_was_latency`는 같은 사건에서 제니퍼 알람을 「호스트 자원의 증상」으로 강등하려 한다(enforce일 때). RCA는 강등을 되돌리지 않는다. 대신 `rca_bundle.gate_rule_conflict=[rule_id]`를 남기고 감사에 적는다. 이 기록은 `plans/144` W6 리플레이의 보정 입력으로 넘긴다. 예를 들어 「지배 프로세스가 WAS면 강등 제외」 같은 조건 추가를 검토할 수 있다. **규칙 파일은 사람이 고친다 — 자동 쓰기 금지.**

**T5 상세 — 과거 이력으로 연관·시간대를 학습한다**(G5 `ml_batch` · 주 1회 · 결과는 Redis `ml:cooccur:*` · `ml:timeprofile:*` + 리포트 JSON)

1. **입력**: 폴스타 `cmm_alarm` 90일(`polestar_alarm_history`)과 제니퍼 이벤트 이력(`apm_events` 기간 조회 · 보존 J-17)을 쓴다. 운영 개시 뒤에는 게이트 결정 감사 JSONL을 보조로 쓴다(게이트 판정 티어 포함). **이 파일을 프로세스 밖에서 읽는 경로는 지금 없다** — 쓰려면 읽기 전용 내보내기(일 단위 축소본)를 새로 만들어야 한다(선택 · 없으면 두 소스 원본 이력만 쓴다). 유형은 `(source, kind 또는 event_type_norm)`으로 정규화한다.
2. **유형 쌍 동시발생**(빈발 에피소드 마이닝 — Mannila et al. 1997):
   - 같은 `host_key`에서 창 W ∈ {5, 10, 30분} 안에 A 뒤 B가 오는 건수를 센다.
   - 지표:
     - 지지도
     - 신뢰도 `P(B|A)`
     - **lift** `= P(B|A) / P(B)`
     - **지연 분포**(중앙값·IQR·95분위)
     - **방향 비대칭** `P(B after A) − P(A after B)`
   - **귀무 모형**: 호스트별 하루 단위 블록을 순열 셔플해서 같은 통계를 다시 잰다. 유형 쌍이 많으므로 Benjamini–Hochberg로 FDR을 보정한다. 표본 n < 20인 쌍은 수치를 내지 않는다(D-176 ⑤).
   - **용도**:
     - (a) D-317 규칙 창(600/60초)과 enforce 조건 lift ≥ 3의 **보정 근거**로 쓴다(`plans/144` W6에 리포트를 넘긴다).
     - (b) T4의 보조 근거로 쓴다. 「이 조합은 과거 lift 7.2, 지연 중앙값 4분」.
     - (c) XS-4 「독립 동시 발생」을 판정한다.
3. **이벤트 ↔ 시계열 상관**(Luo et al. KDD'14 *Correlating events with time series for incident diagnosis*):
   - 유형 A 이벤트의 앞 창과 뒤 창에서 상대 소스 지표(예: 제니퍼 이벤트 전후의 폴스타 CPU, 또는 반대)의 분포 차이를 2표본 검정한다.
   - 결과는 「A가 지표를 앞선다/뒤따른다/무관하다」로 낸다. 알람 임계에 걸리지 않은 지표 변화도 잡는다.
   - 해상도 한계: 폴스타는 시간 집계라 분 단위 판정은 제니퍼 런타임(5분)·Prometheus(분) 쪽에서만 성립한다.
4. **시간대 프로파일**:
   - (host, 유형)마다 시간×요일(24×7) 히스토그램을 만든다. 월말·월초·영업 개시(09시)·배치 창 같은 달력 표지를 함께 둔다.
   - 기대 건수는 계절 포아송(과산포면 음이항)으로 구하고, 이번 사건 시각의 놀람도를 계산한다.
   - 두 소스가 **같은 시간대 열점**을 공유하면 업무 부하 주기다. R.3 CPU-2(정기 작업) 판정에 제니퍼 쪽 증거를 더한다.
5. **교차 소스 유사 사건**: 사건 지문은 정렬된 `(source, 유형, 상대 지연 구간)` 시퀀스다. 지문 간 편집 거리·Jaccard로 과거 결합 사건을 찾는다(S4-F 확장). 알람 텍스트 유사도는 e5 임베딩으로 보조한다(D-114 — 주석 전용 · G5 `ml_service`).
6. **하지 않는 것**: Granger·PCMCI 같은 자동 인과 발견(§0.2 ④). 방향은 T4 규칙과 프로세스 귀속이 정하고, T5는 **보조 근거와 규칙 보정 자료**일 뿐이다.

#### R.8.4 S5 복합 분류 XS-1~XS-6 (`rca_rules.yaml` `cross_source` 블록)

| 분류 | 성립 조건 | 결론 문장 틀 | 기존 분류와의 관계 |
|---|---|---|---|
| **XS-1 앱 기인 호스트 포화** | 방향 D1 ∧ `t0(jvm 또는 service) ≤ t0(infra_host) + u` | 「WAS 인스턴스 I가 호스트 CPU를 소비했다 — 호스트 CPU 알람(시각)은 증상, 원인은 아래 WAS 분석」 | R.3 CPU-4~8 결론을 **원인**으로, 폴스타 알람을 **증상**으로 배치 |
| **XS-2 인프라 기인 앱 지연** | 방향 D3 ∧ `t0(infra_host) < t0(service)` ∧ (과거 lift ≥ 3 또는 T5 상관이 「앞선다」) | 「호스트 자원 포화(P · 시각)가 먼저이고 WAS 지연(시각)이 lag 뒤에 따랐다(과거 같은 조합 지연 중앙값 M분)」 | CPU-1~3 · S-M · S-N 결론이 원인, 제니퍼 이벤트는 증상 |
| **XS-3 공통 의존 장애** | 방향 D4 | 「도메인 D의 인스턴스 k개가 동시에 이상 · 호스트는 정상 — 공통 의존(DB·외부) 의심」 | S-D·S-E로 전이 |
| **XS-4 독립 동시 발생** | `link_strength ∈ {weak, none}` ∧ 과거 lift < 1.5(n ≥ 20) | 「같은 시간대지만 연관 근거가 없다 — 별건으로 본다」 | 각자 원래 분류 |
| **XS-5 정기 패턴** | 두 소스 모두 시간대 놀람도가 낮다(평소 이 시각에 반복) | 「매주 X요일 HH시 반복 — 업무 피크·정기 작업」 | CPU-2 강화 |
| **XS-6 순서 판정 불가** | 방향 D7 | 「두 소스 시각 차이가 측정 불확실(±u초) 안 — 선후 판정 불가」 | 단일 소스 분류만 |

#### R.8.5 R.3 예시를 제니퍼 트리거로 다시 보면

> 같은 사건이 **제니퍼 이벤트로 먼저 시작**한 경우다. 시각은 예시이고 유형명은 운영 명칭 미확정(J-18)이다.

| 시각 | 소스 | 계층 | 내용 | 출처 |
|---|---|---|---|---|
| 13:48 | 제니퍼 | change | 소스 변경 감지(인스턴스 GP_SVC_01) | `apm_source_changes` |
| 13:50 | 제니퍼 | jvm | `process_cpu_pct` 변화점 t0(5분 해상도) | `apm_runtime_health` + PELT |
| 13시대 | 폴스타 | infra_host | CPU 시간 평균 상승(시 해상도 — 분 단위 t0 없음) | `polestar_metric_trend` |
| 14:05 | 제니퍼 | service | 응답 지연 이벤트(`SERVICE_QUEUING` → `was_service_queuing`) — **이 이벤트가 PAGE·조사 1회** | 폴러 → `alarm:raw` |
| 14:07 | 폴스타 | infra_host | CPU 100% 알람 — 같은 사건 멤버 | `alarm:raw` |

→ S4-B에서 지배 프로세스는 PID 41213이고, 이것이 GP_SVC_01에 매핑된다. 따라서 방향은 **D1(APM → SMS)**이고 **XS-1**이 성립한다. 원인 판정은 R.3과 같은 **CPU-6**이다(배포 뒤 TPS당 CPU 3.1배 · 서비스 `/acct/inquiry.do` · 메서드 `AcctDao.mergeRows`). 보고서는 폴스타 14:07 알람을 「증상」으로 적는다. 게이트가 enforce였다면 `host_resource_to_was_latency`가 14:05 제니퍼 알람을 강등했을 것이다. 그래서 `gate_rule_conflict`를 기록한다. 조사는 사건당 1회라서 14:07 폴스타 PAGE는 같은 조사에 합류한다(D-317 ⑦).

#### R.8.6 신규 구현 RC-15~RC-21

| ID | 대상 | 내용 | 패키지 | 플래그(기본 off) |
|---|---|---|---|---|
| RC-15 | `sre_agent/config/ml/event_layers.yaml` | 제니퍼 `event_type_norm`과 폴스타 kind를 계층·kind로 매핑(Y-2) — 운영 명칭(J-18) 확인 뒤 채운다. 게이트웨이 `was_signals` 어휘는 바꾸지 않는다(어휘 추가는 `plans/144` 소관) | sre_agent config | — |
| RC-16 | `noise_gate` 조사 payload | 사건 스냅샷(멤버 ≤ 50건의 소스·유형·시각·티어 · `Episode.to_dict` 축약)을 실음(Y-3) — D-317 ⑦ 사건 id 확장 | noise_gate | `INVESTIGATION_EPISODE_SNAPSHOT_FORWARD` |
| RC-17 | `rca_collect.py` S3-T | 병합 타임라인 수집(T-1~T-4) | sre_agent application | `RCA_DETERMINISTIC_COLLECT` |
| RC-18 | `sre_agent/domain/ml/timeline.py` | T1~T4·T6(시각 정규화·계층 순서·방향 표) — 순수 Python | sre_agent domain | 같음 |
| RC-19 | `ml_batch` 작업 `cooccur`·`time_profile` | T5(G5 실행) · 결과 Redis `ml:cooccur:*`·`ml:timeprofile:*`(TTL 8일) + 리포트 | sre_agent(G5) | `ML_BATCH_COOCCUR` |
| RC-20 | `rca_rules.yaml` `cross_source` 블록 · 보고 양식 | XS-1~XS-6 · S7 양식에 「SMS·APM 타임라인」 절 추가 · `gate_rule_conflict` 감사 | sre_agent | `RCA_DECISION_TABLE` |
| RC-21 | (선택 · 이 계획 밖) 제니퍼 푸시 수신부 | `jennifer_adapter` → 게이트웨이 TCP 수신 → `alarm:raw` — 폴링 지연을 줄인다 | apm_gateway(`plans/144`·`87` 소관) | — |

### R.9 G-14 해결 — 함수(메서드) 단위 집계 (v5 · 2026-10-13)

> **v5 지시(원문)**: *"G-14의 문제를 해결하여 함수 단위의 집계가 될 수 있도록 수정하도록 계획을 정리하라."*
> **처리**: G-14는 **(가) 게이트웨이 소유 파서로 해소**한다(지시로 확정). 다만 profile.txt 한 경로만으로는 함수 단위 집계가 성립하지 않는 경우가 있다(아래 P-3~P-5). 그래서 **세 경로(F-1·F-2·F-3)와 합의 집계**로 넓힌다.

#### R.9.0 G-14가 막던 것 — 문제 다섯

| # | 문제 | 실측 |
|---|---|---|
| P-1 | 프로파일이 **발췌**로만 나온다. 60줄·4000자에서 잘리고, 줄당 400자로 마스킹 절단된다 | `apm_gateway/.../application/tools.py:108-109,2395-2399` — 깊은 호출의 자기 시간이 사라진다 |
| P-2 | profile.txt **실형식 미확인** | Open API 5.6.4 스펙에서 `/api/transaction/profile.txt`의 응답은 `text/plain`이다(인자 domain_id·txid·time 필수 · key·time_pattern 선택). **JSON 프로파일 경로는 없다.** 로컬 녹화본은 `domain_not_connected`뿐이다(J-14) |
| P-3 | **표본 편향** — 「완료된 느린 트랜잭션」만 잡힌다 | 지금 돌고 있는 CPU 루프(CPU-8)와 「짧지만 잦은 호출」의 누적 CPU는 못 잡는다 |
| P-4 | **호출 예산** | `APM_PROFILE_CALLS_PER_INVESTIGATION` 기본 5회/1시간이다(채팅 주체 면제) → 표본 K ≤ 5 |
| P-5 | **프로파일 범위가 에이전트 설정에 달렸다** | 제니퍼 메서드 프로파일은 설정한 대상(클래스·메서드 · 자동 프로파일 여부)만 기록한다. 범위 밖 메서드는 profile에 없다(J-19) |

**이미 해결된 것 — 서비스 단위 CPU(J-13 해소)**: 스펙의 `ApplicationStatus`에 `totalCpuTime`·`cpuTimePerTransaction`·`sqlTimePerTransaction`·`calls`가 있다. 게이트웨이도 이미 `total_cpu_ms`·`cpu_ms_per_tx`로 파싱한다(`adapters/jennifer/fields.py:421,435`). 따라서 S4-C2 서비스 국소화는 **CPU 시간 기준**으로 계산한다. `sort_by`에 이 값을 넣을 수 있는지는 게이트웨이도 「한계」로 표기하고 있다(`tools.py:2836`). 수용되지 않으면 상위 n을 넉넉히 받아 클라이언트에서 정렬한다.

#### R.9.1 세 경로

| 경로 | 원천 | 잡는 것 | 못 잡는 것 | 비용 | 소유 |
|---|---|---|---|---|---|
| **F-1 완료 트랜잭션 프로파일 구조화** | `/api/transaction/profile.txt` **전문** | 느린 완료 트랜잭션의 메서드별 자기 시간·SQL·외부 호출·예외 | 진행 중 루프 · 프로파일 설정 밖 메서드 | 기존 예산(K ≤ 5) — 파싱은 호출을 늘리지 않는다 | **apm_gateway**(파서) |
| **F-2 실행 중 요청 반복 표본** | `/api/activeService/list` 반복(예: 5초 × 12회) | **지금** 실행 중인 지점(`runningMode`·`runningHash`·`runningFullText`)과 `cpuTime` 증분 → 통계적 표본 프로파일 | 실행 지점의 표기가 메서드·SQL·외부 호출 중 무엇인지는 제니퍼 표기에 달렸다(J-19) | 게이트웨이 내부 12회(별도 상한) | apm_gateway(도구) · sre_agent(집계) |
| **F-3 호스트 핫 스레드** | **F-3a**: 조사 bash 도구셋의 `timeout 20 nice -n 10 pidstat -t -p <PID> 1 1`(`VM_DIAG_ALLOW` 가드형 · **이미 허용** — 게이트 L3 수집기 `cpu` 프로파일에는 없다) → 스레드 이름·스레드별 CPU · **F-3b**: `jcmd <PID> Thread.print` ×3(**미허용 · G-16**) → TID↔`nid=0x…` 대조로 스택 상위 프레임 | 제니퍼 미등록 WAS(CPU-9)에서도 동작한다. GC·JIT 스레드를 앱 스레드와 구분한다 | F-3a는 메서드를 모른다(스레드 이름까지) · F-3b는 safepoint 정지와 권한 문제가 있다 | F-3a 1회 · F-3b 3회 | sre_agent(조사 bash) |

**F-3a만으로 얻는 판정**(허용목록 확장 없이):
- 상위 스레드가 `GC Thread#*`·`G1 *`·`VM Thread`면 **CPU-5 GC형 확증**이다.
- 상위 스레드가 `C2 CompilerThre*`·`C1 CompilerThre*`면 **JIT 폭주**다. 새 분류 후보이며 배포 직후 워밍업과 구분한다.
- 앱 워커 스레드 하나가 ~100%(한 코어)면 **CPU-8 루프 증거 강화**다(`single_core_hot`과 교차).
- `ps -L`의 `pcpu`는 스레드 생애 평균이라 순간값이 아니다. 그래서 순간값은 `pidstat` 간격 측정을 1순위로 쓴다.

#### R.9.2 F-1 파서 명세 — `apm_gateway` (RC-8 확장)

- **입력**: profile.txt **전문**. 게이트웨이 안에서 파싱하고, 응답에는 `methods[]`와 품질 요약을 **추가**한다. 기존 `profile_excerpt`·전문 artifact(마스킹본)는 그대로 둔다. 계약은 필드 추가뿐이라 하위 호환이다.
- **문법 판별**: J-14 녹화로 확정하기 전에는 두 문법을 모두 지원하고, 형식 판별기가 고른다.
  - (A) **트리형** — 들여쓰기 또는 depth 칸 + 경과(+CPU)가 있다. `self = elapsed − Σ 직계 자식 elapsed`.
  - (B) **평면 순차형** — 줄마다 시작 오프셋(또는 시각)이 있다. 다음 줄까지의 간격(gap)을 앞 줄에 귀속하고, 경과 칸이 있으면 그 값을 쓴다.
  - 판별 실패면 `grammar=unknown`으로 두고 `methods[]`를 비운다(침묵 추정 금지).
- **줄 종류**: `METHOD` · `SQL`(바인드값 마스킹) · `FETCH` · `EXTERNAL_CALL` · `ERROR`(예외) · `MESSAGE` · `other`. 모르는 줄은 버리지 않고 `other`로 센다.
- **레코드**: `{seq, depth|null, t_offset_ms|null, elapsed_ms|null, self_ms, cpu_ms|null, kind, signature, line_hash}`. 서명은 **원형 그대로** 둔다. 정규화는 벤더 중립 작업이므로 소비자(`sre_agent`)가 한다.
- **품질 필드**:
  - `format_version` · `grammar` · `lines_total` · `lines_parsed`
  - `coverage = lines_parsed / lines_total`
  - `sum_check = |Σself − tx_elapsed| / tx_elapsed`
  - `coverage < 0.9` 또는 `sum_check > 0.1`이면 `degraded=true`와 사유를 붙인다. 소비자는 그 표본의 핫스팟 확신도를 한 단계 낮춘다.
- **크기 상한**: `methods[]` ≤ 2,000행이다. 넘으면 자기 시간 상위만 남기고 나머지를 `other_self_ms`로 합산한다. 잘림은 `truncated`로 표기한다.
- **픽스처**: ① 운영 녹화 3건을 마스킹해 반출한다(D-219 · J-14). ② 두 문법의 합성 픽스처. ③ `domain_not_connected` 응답.

#### R.9.3 F-2 표본 프로파일러

- **신규 게이트웨이 도구** `apm_active_sample(instance_name, samples=12, interval_seconds=5)`:
  - 게이트웨이가 **안에서** 반복 호출하고 한 응답으로 돌려준다. 조사 쪽 왕복과 LLM 노출이 없다.
  - 상한은 `samples ≤ 20` · `interval ≥ 2초`다. 기존 속도 제한을 공유한다.
  - 표본마다 요청별로 `txid`·`service`·`runningMode`·`runningHash`·`runningFullText`(마스킹·절단)·`cpuTime`·`elapseTime`·`threadHash`를 싣는다.
- **집계**(`sre_agent/domain/ml/profile_hotspot.py`):
  - **점유 추정**: 실행 지점(`runningHash`)별 출현 횟수를 전체 관측 수로 나눈다. Wilson 구간을 병기한다.
  - **CPU 귀속**: 같은 `txid`의 연속 표본 간 `Δ cpuTime`을 그 시점의 실행 지점에 귀속해 합산한다.
  - **루프 판정**: 같은 `txid`가 연속 m회(초기값 4) 같은 `runningHash`이고 `Δcpu / Δt ≥ 0.8`이면 「한 지점 계산 루프」다. R.3 CPU-8의 근거가 된다.
- **한계 표기**: 12표본의 점유 추정은 구간이 넓다. 그래서 비율을 단정하지 않고 **순위와 구간만** 쓴다(D-176 ⑤ · I-5).

#### R.9.4 합의 집계 — `profile_hotspot` (RC-9 확장)

1. **서명 정규화**: 줄 번호·`$$EnhancerByCGLIB$$…`·`$Proxy\d+`·`$$Lambda$\d+/0x…`·생성 해시 접미를 제거해 `패키지.클래스.메서드`로 맞춘다. 규칙은 `sre_agent/config/ml/signature_normalize.yaml`에 둔다.
2. **경로별 순위**:
   - F-1: 표본 간 Σself와 출현 표본 수(`samples_hit/K`)
   - F-2: 실행 지점 점유와 CPU 귀속
   - F-3b(허용 시): 상위 스레드 스택의 상위 프레임 빈도
3. **안정성**: F-1 표본을 부트스트랩으로 1,000회 재추출한다. 1위 메서드가 유지되는 비율이 `stability`다.
4. **합의**:
   - 경로 간 순위를 Borda 합으로 묶는다. 경로 가중은 두지 않는다(단순 · 근거 없는 가중 금지).
   - 메서드마다 `agreement`(성립한 경로 중 상위 3에 든 경로 수)를 붙인다.
5. **결론 문장 규칙**:
   - `agreement ≥ 2` ∧ `stability ≥ 0.7`이면 「상위 메서드 m1」로 표기한다(확신도 medium 이상 허용).
   - 경로 1개뿐이면 「메서드 후보 m1(근거: F-1 표본 5건)」으로 표기한다.
   - F-1이 `degraded`면 확신도를 한 단계 낮춘다.
6. **SQL 몫**: `sql_share`는 F-1의 `kind ∈ {SQL, FETCH}` 자기 시간 합을 전체로 나눈 값이다. R.3 「SQL 비중 예외(≥ 0.6 → S-D)」 규칙은 그대로다.
7. **출력**: `hot_methods[{method, f1_self_share, f1_samples_hit, f2_share, f2_ci, f3_hits, agreement, stability}]` · `hot_threads[{name_class∈{gc,jit,app,other}, cpu_pct}]` · `profile_quality{k, degraded_k, grammar}`.

#### R.9.5 R.3 갱신 사항

- **S3-4**: `apm_status_stats(kind=application, I, sort_by=total_cpu_ms, n=20)` — `sort_by`가 수용되지 않으면 n=100으로 받아 클라이언트에서 정렬한다(J-13 해소).
- **S3-5**: F-1(`apm_transaction_profile` → `methods[]`)과 F-2(`apm_active_sample`)를 **함께** 부른다. F-3a는 지배 PID에 1회 실행한다(조사 bash 도구셋 · 원격 호스트면 기존 `REMOTE_SSH_ALLOW` 옵트인 경로). 게이트 L3 수집기(`host_diagnostic_collector.L3_PROFILES`)에 넣으려면 Plan 60 §18.2 허용목록 변경이 따로 필요하다 — 이 계획은 넣지 않는다.
- **S4-C2**: 몫 변화 EP·JS를 `total_cpu_ms` 기준으로 계산한다. 호출 수·수행 시간 기준은 보조 열로 둔다.
- **S4-C4**: 입력이 「profile.txt 발췌」에서 「F-1 `methods[]` + F-2 표본 + F-3」으로 바뀐다. 방법은 R.9.4다.

### R.10 실행 기반 — AWS G5 GPU 서버 = ML 장애 분석 서버 (v5 · v6 개정 2026-10-13)

> **v5 지시(원문)**: *"ML모델은 aws G5 gpu 서버를 사용할 예정이다. gpu에 설치 구성하여 ML을 동작시키는 방식으로 계획을 정리해야한다."*
> **사용자 답변(2026-10-13)**:
> - **위치** = 「운영망 연결 VPC」다. G5는 운영 내부망과 전용선·VPN으로 연결된 기관 AWS 환경에 있다. `sre_agent`가 G5의 ML 서비스를 실시간 호출해 추론하고, 학습도 운영 데이터로 G5에서 한다.
> - **용도** = *"1.이다. 툴 호출이 필요한 경우 별도의 LLM도 서빙하는 2.를 사용 하지만 툴호출이 필요없으면 1.으로 진행한다."* → **기본은 ML 전용**이다. 툴 호출이 필요한 LLM 경로가 생길 때만 G5에 별도 LLM을 서빙한다.
>
> **v6 지시(원문)**: *"gpu 서버는 llm 서빙 목적보다 ML 을 통한 장애 분석의 목적으로 사용하려고 한다. 이 내용이 고려되어 있냐?"* → 점검해 보니 v5는 장애 분석 ML 대부분(S4 전체·T1~T4)을 `run_service`에 두었다. 그래서 G5가 예측·임베딩·학습·배치에 치우쳐 있었다. 이에 (가) 현행 유지 · (나) S4 전체를 G5로 이전 · (다) 혼합을 제시했고, 사용자 지시(*"다 권장에 맞게 계획을 수정하라"*)에 따라 **(다) 혼합 배치**를 적용한다.
>
> **G5의 목적 = ML 기반 장애 분석.** 실시간 장애 분석 가운데 무거운 분석은 G5에서 돈다. 대상은 광역 이상 스캔·다차원 국소화·유사 사건 검색·과거 연관 조회·학습 모델 원인 순위·예측 구간이다(GA-1~GA-6). 이 분석들의 기준선 적합·학습·배치도 G5에서 한다. **LLM 서빙은 부차다.** ML 인스턴스에는 올리지 않는다. R.10.6 조건이 실측될 때만 별도 인스턴스·별도 예산으로 검토한다.

#### R.10.1 무엇이 G5로 가고 무엇이 남는가 — (다) 혼합 배치 (v6)

**배치 원칙 두 줄**
- **판정에 꼭 필요한 경량 통계는 `run_service`에 남긴다.** S5 원인 분류의 **필수 조건**이 읽는 값은 모두 여기서 나온다. 그래서 G5가 멈춰도 결론 사슬이 끊기지 않는다(I-11).
- **무거운 장애 분석은 G5 `ml_service`의 장애 분석 API(`/v1/analyze`)로 보낸다.** 다음 넷 중 하나에 해당하면 「무겁다」로 본다.
  - 입력 폭이 넓다(엔티티 × 지표가 수백~수천 계열).
  - 탐색 공간이 크다(다차원 조합).
  - 학습된 모델이 필요하다.
  - GPU가 필요하다.

| 층 | 작업 | 실행 위치 | 이유 |
|---|---|---|---|
| 결정적 사슬 | S3 수집 · S5 판정 · S6 조치 · S7 수치 검사 | `run_service`(기존 조사 호스트) | 결정적·경량이다. MCP 자격 경로(D-119)를 그대로 쓴다 |
| **판정 필수 경량 통계** | S4-A(트리거 지표 1계열 STL+PELT · 504점) · S4-B · S4-C1·C1′ · S4-C2(1차원 몫 변화) · S4-C3 · S4-C4(R.9.4 합의·부트스트랩) · S4-D · S4-E · S4-G · T1~T4·T6 · S4-F TF-IDF(정본 · GA-3 폴백) | `run_service` 안(함수 호출) | 수 ms~수백 ms라 G5 왕복보다 싸다. S5 필수 조건의 입력이다 |
| **G5 장애 분석(온라인)** | **GA-1~GA-6**(아래 표) | **G5 `ml_service` `/v1/analyze`** | 폭·탐색 공간·학습 모델·GPU 중 하나 이상에 해당한다. **G5의 주 업무다** |
| **G5 학습·배치(오프라인)** | GA 기준선 적합(GA-1 계열별 STL·MAD·PCA — 야간) · 유사 사건 색인 갱신(GA-3) · T5 동시발생·시간대(RC-19 → GA-4) · GBDT 학습(GA-5 · M7c) · 파운데이션 모델 백테스트(GA-6 · G-7 (나′)) · 전 서버 배치 스코어링(게이트 `ml:*` 공급 — G-11) · 리플레이 평가 하네스 | **G5 `ml_batch`** | 운영 데이터를 대량으로 읽고 모델을 만든다. 온라인 분석이 쓰는 산출물을 미리 만든다 |
| GBDT 학습 엔진 | G5 `ml_batch` — 1차 sklearn `HistGradientBoostingClassifier`(CPU) | — | GPU 학습(XGBoost `device="cuda"` 등)은 **리플레이셋 우위나 학습 시간 이점이 실측될 때만** 쓴다(§5.3 (c) 「신규 의존은 우위가 신뢰구간 밖일 때만」 유지) |

**G5 장애 분석 API — GA-1~GA-6**

| ID | 질문 | 입력 | 방법 | 출력 → 쓰이는 곳 | 계산 자원 | 운영 노출 |
|---|---|---|---|---|---|---|
| **GA-1 광역 이상 스캔** | 트리거 지표 말고 무엇이 같이 이상한가 · 무엇이 먼저였나 | 사건 엔티티와 연결 엔티티의 **가용 지표 전부**. 연결 엔티티는 D-P6 토폴로지 1~2홉 · 같은 호스트의 제니퍼 인스턴스 · 같은 도메인 피어다. 원천은 폴스타 `_h`·제니퍼 5분이다. 온라인 입력은 사건 전 6시간 + 사건 창뿐이다. 3주 기준선은 `ml_batch` 캐시에서 쓴다 | S4-A와 같은 robust STL+MAD+PELT를 계열 전부에 적용한다. 계열별 계절 성분·척도는 `ml_batch`가 야간에 미리 적합해 둔다. 2차로 엔티티별 다변량 PCA 재구성 오차와 IForest를 낸다(§0.2 ① 2차 탐지기) | `wide_anomalies[{entity, metric, z, onset_t, pattern}]`(onset 순) · 엔티티별 다변량 점수 → **T3 계층별 최초 발생의 추가 후보** · S4-G 후보 확장 | 다중 코어 CPU·RAM + 기준선 캐시. GPU는 PCA 일괄 행렬 연산에만 쓴다. **GPU 이점이 작다는 것을 숨기지 않는다** — G5에 두는 이유는 폭과 기준선 캐시다 | 즉시(보조 증거) |
| **GA-2 다차원 국소화** | 어느 인스턴스 × 서비스 × 업무 조합이 문제인가 · 외부 원인인가 | D-J5 서비스별 지표를 가용 차원(인스턴스·서비스·업무 등)의 큐브로 만든다 | Squeeze/PSqueeze(§5.4 R3 · CU-28) + HALO 계층 가지치기 · 외부 원인 판정 | `dim_root[{조합, EP, surprise}]` · `external_cause` → S5 보조 근거 · **S4-C2 1차원 결과와 일치 여부** | 다중 코어 CPU(조합 탐색). GPU는 쓰지 않는다 | 즉시(보조 증거) |
| **GA-3 유사 사건 검색** | 과거에 같은 모양의 사건이 있었나 · 그때 원인·조치는 무엇이었나 | 사건 지문(R.8.3 「T5 상세」 5번 · 원인 분류·`was_signals`·프로세스 분류) + 마스킹한 알람·이벤트 텍스트 + `rca_bundle` 요약 | e5 임베딩 dense 검색 + TF-IDF sparse + 지문 Jaccard 하이브리드 · 시간 감쇠. 과거 사건 색인은 `ml_batch`가 갱신한다 | `similar[…]` 상위 3 → S4-F와 병기. **「과거 유사 사례」로만 표기한다** | **GPU**(임베딩) | 병기는 즉시. **1순위 교체는 §5.5 리플레이셋 우열 판정 뒤**다. 그 전까지 정본은 `run_service` TF-IDF다 |
| **GA-4 과거 연관 조회** | 이 조합은 평소에도 같이 오는가 · 보통 몇 분 차이인가 | 현재 사건의 이벤트 유형쌍 · host · 시각 | T5가 학습한 표를 조회한다(RC-19) | `cooccur` · `time_pattern` → T5 출력 · XS 분류 보조 | 경량(조회). 학습 산출물을 G5가 갖고 있으므로 G5가 답한다 | 즉시 |
| **GA-5 학습 원인 순위** | 확정 라벨로 배운 모델은 어느 원인을 위로 보나 | S4 출력 피처 + GA-1 요약. 피처는 누수 금지 KL-1~KL-6 허용목록만 쓴다 | GBDT(F2c · M7c) 추론 · TreeSHAP 기여도 | `learned_rank[{cause, score}]` · 상위 기여 피처 → S5 **보조 순위**(M7c 규정 · I-5 확률 표기 금지) | CPU 추론. 학습은 `ml_batch`에서 한다 | **M7c 게이트 통과 뒤.** 그 전에는 섀도 기록만 한다 |
| **GA-6 예측 구간 · 고갈 ETA** | 예측 구간을 벗어났나 · 자원이 언제 바닥나나 | 트리거 지표 · GA-1 상위 계열 | 파운데이션 예측(Chronos-2 등 · M7b)의 분위수 구간 대비 실제값 위치 · 고갈 ETA | `forecast_band_exceed` · `eta` → S5 보조 · S6 긴급도 | **GPU** | ETA는 M7b 게이트 뒤에 노출한다. 그 전과 G5 불가 시는 `run_service` 추세+conformal ETA다. 구간 이탈을 **탐지 점수로 쓰는 것은 섀도만**이다(§0.2 ① 탐지 이점 없음 — 백테스트로 뒤집힐 때만 노출) |

**호출 방식 — S3와 병행하는 비동기 잡**
1. S2(사건 시점 고정) 직후 `run_service`가 `POST /v1/analyze`를 보낸다. 본문에는 `{incident_id, t_ref, 대상 엔티티 목록, 트리거 지표, 요청 GA 목록}`을 싣는다. 응답은 잡 id다.
2. G5는 S3·S4와 **병행**해 GA를 돌린다.
3. `run_service`는 S5에 들어갈 때 `GET /v1/analyze/{id}`로 회수한다. 마감은 S4 종료 + 10초(초기값)다.
4. 마감까지 오지 않은 GA는 `limitations`에 「시간 초과」로 적고 그대로 진행한다. 늦게 도착한 결과는 감사 기록에만 남긴다.
5. 지연 목표(초기값 · J-20·J-21 실측으로 보정)는 다음과 같다.
   - GA-1 p95 ≤ 30초 · GA-2 ≤ 15초 · GA-6 ≤ 5초
   - GA-3 ≤ 2초 · GA-4 ≤ 1초 · GA-5 ≤ 1초
   - 모두 S3 상한(60초) 안에 들어가므로 조사 타임박스를 늘리지 않는다.

**판정 독립 불변식 I-11(v6 신설)** — §4.1에 추가한다.
1. S5 규칙의 **필수 조건은 `run_service` 출력만 읽는다.** GA 출력은 **보조 조건**(확신도 가산)과 **보조 후보**로만 들어간다. 보조 후보에는 `origin: ml_service`를 붙인다.
2. GA 결과가 결정적 사슬과 어긋나면 결론을 바꾸지 않는다. 예를 들어 GA-1은 DB 계층 선행 이상을 찾았는데 T3은 WAS 선행으로 판정한 경우다. 이때는 「ML 분석 불일치」를 `rca_bundle`에 싣고 보고서의 별도 절에 낸다. 운영자 확정(S8)이 쌓이면 규칙 보정 입력이 된다(규칙 파일 자동 쓰기 금지 — R.8.3과 같다).
3. 보조 순위(GA-5)가 정본 순위를 바꾸려면 §6.3 채택 게이트를 통과해야 한다(M7c).
4. 재현성을 위해 `rca_bundle.ml_service`에 요청 id · GA별 모델 해시 · 기준선 버전 · 소요 시간을 기록한다.

**G5 장애 시 폴백** — 무거운 계산을 `run_service`에서 대신 돌리지 않는다. 대신 경량 짝으로 내려가고, 사유를 반드시 표기한다(**침묵 폴백 금지**).

| GA | G5 불가 시 | `limitations` 표기 |
|---|---|---|
| GA-1 | S4-A(트리거 지표만) · S4-E 피어 | 「광역 이상 스캔 불가 — 트리거 지표만 분석(사유)」 |
| GA-2 | S4-C2 1차원 몫 변화 | 「다차원 국소화 불가 — 서비스 1차원만(사유)」 |
| GA-3 | S4-F TF-IDF | 「임베딩 검색 불가 — TF-IDF(사유)」 |
| GA-4 | 생략 | 「과거 연관 통계 불가(사유)」 |
| GA-5 | 생략(정본 순위에 영향 없음) | 「학습 순위 불가(사유)」 |
| GA-6 | 추세+conformal ETA | 「예측 모델 불가 — 추세 ETA(사유)」 |

- 게이트는 `ml:*` 키가 만료되면 E3로 폴백한다(§4.6 기존 규약).
- **호출 계약**: G5에는 DB·제니퍼 자격증명이 없다. 가진 것은 `mcp_server`·`apm_gateway`의 **MCP 토큰뿐**이다(D-119·D-274 유지).
  - v5에서는 「`ml_service`는 관측 데이터를 직접 읽지 않는다」였다. v6에서는 GA-1·GA-2에 한해 `ml_service`도 MCP로 읽는다. 폭이 넓은 입력을 `run_service`가 모아 실어 보내면 조사 호스트의 수집 시간이 늘기 때문이다.
  - 조회 범위는 `run_service`가 요청 본문에 지정한 엔티티와 시간 창으로 제한한다.
  - 상한은 사건당 엔티티 ≤ 20 · MCP 호출 ≤ 40 · 동시 사건 ≤ 3이다(초기값 · J-21). 이를 넘으면 엔티티 우선순위(사건 엔티티 → 1홉 → 피어)대로 자른다. 자른 사실은 `limitations`에 적는다.
  - GA-3~GA-6은 v5대로 `run_service`가 입력을 실어 보내거나 G5 캐시만 쓴다.
  - `ml_batch`의 대량 이력 조회는 v5와 같다. 호출 속도 상한과 야간 창을 둔다(`docs/25` 부하 가드 자세).
- **온라인 우선**: 같은 인스턴스에서 `ml_batch`는 cgroup CPU 상한(초기 50%)과 GPU 메모리 상한 안에서만 돈다. `/v1/analyze` 큐에 대기가 생기면 배치를 일시 중지한다.

#### R.10.2 코드 소유·배포 단위

- 코드는 **`sre_agent` 패키지 그대로**다(D-118 개정안 · §4.2 C-1~C-6). 엔트리는 셋이다.
  - `run_service`(기존)
  - `ml_batch`(§4.2 기존 계획)
  - **`ml_service`(신규 — HTTP/JSON 장애 분석 API `/v1/analyze` · v6)**
- **[v6] GA 분석 코드의 위치**: 분석 본체는 `sre_agent/domain/ml/`(순수 · I-10)와 `infrastructure/ml/`(lazy 어댑터)에 둔다. 예를 들면 `wide_scan.py`(GA-1)·`dim_localize.py`(GA-2 · CU-28)다. `ml_service`는 이 함수들을 HTTP로 노출하는 엔트리일 뿐이다. `run_service`의 경량 짝(S4-A·S4-C2·S4-F)과 같은 패키지를 쓰므로 계산 정의가 둘로 갈리지 않는다.
- **의존 분리(C-1 구체화)**:
  - 현재 `sre_agent/pyproject.toml`의 기본 의존은 `holmesgpt>=0.36.0`·`pydantic-settings`·`mcp<2`다(`requires-python >=3.13`).
  - G5에는 HolmesGPT가 필요 없다. 그래서 extra를 둘로 나눈다.
    - `ml-core`: numpy·scipy·scikit-learn·statsmodels·ruptures. `run_service`와 G5 공통이다.
    - `ml-gpu`: torch CUDA·transformers·파운데이션 모델 패키지·임베딩. G5 전용이다.
  - holmesgpt를 기본 의존에서 `investigate` extra로 내릴지는 M1 CU에서 해석 검사(C-4)로 정한다. 내리지 않으면 G5에도 설치된다 — 무해하지만 이미지가 커진다.
- **`run_service` 호스트에는 torch를 설치하지 않는다.** v2 R-17(venv 비대화)이 해소된다.

#### R.10.3 인스턴스

| 용도 | 인스턴스(출발점) | GPU | vCPU / RAM | 근거 |
|---|---|---|---|---|
| **ML 장애 분석(기본 · v6)** | **`g5.4xlarge`** 출발 → 실측으로 `g5.2xlarge` 축소 또는 `g5.8xlarge` 확대 | NVIDIA A10G 24GB × 1 | 16 / 64GiB(2xlarge 8 / 32GiB · 8xlarge 32 / 128GiB) | 파운데이션 예측 모델·e5 임베딩·GBDT·PCA는 모두 1B 파라미터 미만이라 24GB 한 장에 함께 올라간다. v6에서 **GA-1 광역 스캔·GA-2 다차원 국소화가 온라인 CPU 작업**으로 들어오고, 야간 배치와 같은 인스턴스를 쓴다. 그래서 병목은 GPU보다 **CPU 코어·RAM**이며, 출발점을 v5의 2xlarge에서 4xlarge로 올린다. 실측으로 정한다(J-20·J-21) |
| LLM 서빙(**부차 · 조건부** · R.10.6 · 기본 구성에 없음) | 별도 인스턴스 — `g5.12xlarge`(A10G × 4 · 96GB) 또는 `g5.48xlarge`(× 8) | — | 48 / 192GiB · 192 / 768GiB | **ML 인스턴스와 섞지 않는다**(VRAM 경합·장애 격리). ML 장애 분석 예산과 따로 잡는다. 모델은 툴 호출 품질 실측 뒤 고른다 |

- 비용은 리전·약정(Savings Plans)·가동 시간에 따라 다르다. `ml_service`는 조사가 실시간이므로 **상시 1대**를 둔다. 학습·대량 배치는 같은 인스턴스에서 야간 창에 돈다(R.10.1 「온라인 우선」). 예산은 J-20에서 확인한다.
- 이중화는 처음에는 두지 않는다. I-11 덕분에 G5가 멈춰도 결론 사슬은 유지되고 보조 증거만 빠진다. 장애 빈도가 운영에 부담이 될 만큼 실측되면 2대로 늘린다.

#### R.10.4 설치 구성 — 단계

| # | 단계 | 내용 | 확인 |
|---|---|---|---|
| 1 | OS·드라이버 | AWS **Deep Learning Base GPU AMI(Ubuntu 22.04)** — NVIDIA 데이터센터 드라이버·CUDA 12.x·cuDNN 포함. 기관 표준 이미지를 써야 하면 그 이미지에 드라이버·CUDA 런타임을 반입 설치한다 | `nvidia-smi`에 A10G·드라이버 버전 표시 |
| 2 | Python | `sre_agent`와 같은 **Python 3.13** venv(`uv`) — 해석 검사(C-4)를 같은 조건에서 돌린다 | `uv pip compile` 해석 OK |
| 3 | 패키지 반입 | 공인 인터넷이 없다. **내부 PyPI 미러** 또는 **S3 게이트웨이 VPC 엔드포인트의 반입 버킷**에서 `uv pip install --no-index --find-links … --require-hashes`로 설치한다. torch는 CUDA 12.x wheel을 쓴다 | `python -c "import torch; assert torch.cuda.is_available()"` |
| 4 | 모델 가중치 | 반입 절차로 S3 → 로컬 EBS. `HF_HUB_OFFLINE=1`·`TRANSFORMERS_OFFLINE=1` · **safetensors만**(pickle 금지) · SHA-256·라이선스 고정(§9 반입 계약). sklearn 모델은 skops 또는 ONNX | 로드 시 해시 대조 실패면 기동 거부 |
| 5 | 서비스 | systemd 유닛 `sre-ml-service`(장애 분석 API `/v1/analyze` · VPC 사설 IP 바인딩 · TLS · Bearer 토큰) · `sre-ml-batch.timer`(야간 — GA 기준선 적합·색인 갱신·T5·학습) · `ml_batch` cgroup CPU·GPU 메모리 상한(R.10.1 「온라인 우선」) · 기동 시 1회 GPU 자가 점검(작은 텐서 연산) | `/healthz`에 GPU·모델 해시·GA 기준선 버전·버전 표시 |
| 6 | 모니터링 | DCGM exporter 또는 `nvidia-smi` 수집 → OpenMetrics(`plans/92` 경로) — GPU 사용률·메모리·온도·ECC 오류 · **GA별** p95 지연·시간 초과율·실패율 · 폴백 횟수 · MCP 호출 수(J-21 상한 대비) | 관제 대시보드 패널 |
| 7 | 보안 | 공인 IP 없음 · SG 인바운드는 `run_service` 호스트만 · 아웃바운드는 `mcp_server`·`apm_gateway`(`ml_service` GA-1·GA-2와 `ml_batch`만 · MCP 토큰)·Redis(`ml:*` 쓰기)·내부 미러만 · **IMDSv2 강제** · EBS·S3 KMS 암호화 · IAM 역할은 반입 버킷 읽기만 · 요청 감사 로그(요청 id·호출자·모델 해시·입력 크기 — 입력 원문은 남기지 않음) | 보안 점검표(J-20) |
| 8 | 운영 | 인스턴스 장애 = R.10.1 폴백 · AMI·드라이버 갱신은 스테이징 G5에서 해석 검사·회귀 뒤 반영 · 모델 교체는 리플레이 게이트(§6.3) 통과 뒤에만 | — |

#### R.10.5 데이터 통제 — 운영 데이터가 G5로 간다

- **D-120과 충돌하지 않는다.** D-120이 금지하는 것은 운영 데이터를 **외부 SaaS LLM**으로 보내는 것이다. G5는 사용자 답변대로 기관 소유 AWS 계정에 있고, 운영망과 전용선·VPN으로 연결된 **운영 영역의 확장**으로 본다.
- **다만 이 해석이 성립하려면 기관 절차 확인이 먼저다(J-20)**. 확인할 것은 다음과 같다.
  - 금융권 클라우드 이용 절차(전자금융감독규정의 클라우드 이용 절차 — 업무 중요도 평가·정보보호위원회 심의·보고 등)가 이 용도에 적용되는가
  - 기관 보안성 검토
  - 데이터 분류(성능 지표·알람·프로파일의 등급)
  - **확인 전에는 G5에 운영 데이터를 넣지 않는다.** 그동안 경량 통계는 `run_service`에서 돌고, GA-1~GA-6과 학습·배치는 보류한다. 조사는 R.10.1 폴백 표기를 단 채 그대로 돈다(I-11).
- **보내는 것의 최소화**:
  - 장애 분석 요청(`/v1/analyze`)은 수치 시계열과 마스킹된 텍스트(기존 `mask_text`·`data_masker` 통과본)만 싣는다. GA-1·GA-2가 MCP로 직접 읽는 것도 수치 지표·서비스 집계뿐이다.
  - 호스트명·인스턴스명은 결과 해석에 필요한 만큼만 싣는다.
  - 프로파일 전문·SQL 원문은 보내지 않는다.
- **G-10 재해석**: (가) 「폐쇄망 서버 내 평가」의 「서버」를 **G5**로 본다. 평가·학습 데이터는 G5 EBS에만 둔다. 개발망 반출은 D-219 축소 지표뿐이다.

#### R.10.6 LLM 서빙 — 부차 · 조건부(사용자 답변 「툴 호출이 필요할 때만」)

> **[v6] G5의 목적은 ML 장애 분석이다.** LLM 서빙은 ML 인스턴스의 GPU·CPU를 쓰지 않는다. 착수하더라도 별도 인스턴스·별도 예산이며(R.10.3), 착수 여부는 G-17 기준 실측으로만 정한다. 이 계획의 G5 구성·설치·Wave(R.10.4 · R.11 ③~⑤)에는 LLM이 없다.

| 경로 | 툴 호출 | LLM |
|---|---|---|
| S7 보고서 서술(`rca_bundle` → 고정 양식) | **불필요** | 기존 조사 LLM(내부망 FabriX) — **G5에 LLM 없음** |
| HolmesGPT 조사 루프 · §4.4 표면 ② 「추가 증거 요청」 | **필요** | 기본은 FabriX다. **FabriX 툴 호출 품질이 기준 미달로 실측되면** G5 별도 인스턴스에 vLLM을 띄운다 |

- **연결**: vLLM OpenAI 호환 `/v1`(`--enable-auto-tool-choice` · 모델별 `--tool-call-parser`)을 쓴다. `sre_agent`는 `API_BASE`로 연결한다(`sre_agent/sre_agent/settings.py:71` `api_base` — litellm `api_base` 전달 실측). 본체 오케스트레이터 평면 `ORCHESTRATOR_PROVIDER=vllm`(`src/config.py:119-120` `base_url`)도 같은 엔드포인트를 쓸 수 있다. 과금 판정상 `vllm` 평면은 비과금이다(D-222). 하지만 G5 비용은 별도 예산이다.
- **판정 기준(초기값 · G-17)**: 조사 리플레이셋에서 다음 셋을 FabriX와 비교한다.
  - ① 툴 호출 성공률(스키마 유효 · 실행 성공)
  - ② 인자 정확도
  - ③ 증거 폐포율(ECR · §6.1)
  - FabriX가 ① < 95% 또는 ③이 리플레이 기준선보다 유의하게 낮으면 G5 vLLM을 검토한다. 비교는 D-119 A/B 품질 게이트 형식을 쓴다.
- **모델 후보는 이 계획에서 정하지 않는다.** 툴 호출 실측과 라이선스 필터(§9)를 거쳐 정한다. 개발 맥의 실 LLM 검증은 계속 MLX(D-240)다.

#### R.10.7 기존 계획 개정 대조표

| 위치 | 이전 | v5 · **[v6]** |
|---|---|---|
| §3.5 P-25 | CPU 서버에 PyTorch CPU wheel | G5에는 CUDA wheel을 쓴다. `run_service` 호스트에는 torch가 없다 |
| §4.2 배치 · C-2 | `run_service`와 `ml_batch`가 같은 호스트일 수 있음(프로세스 분리) | **노드 분리**: `run_service`(기존 호스트) / `ml_service`·`ml_batch`(G5) |
| §7 M0 J-9 | `sre_agent` 호스트의 배치 스코어링 CPU 여유 | `run_service` 호스트의 **경량 통계 여유만** 본다. 배치 용량은 G5 사양(J-20)으로 대체한다 |
| §10 **G-7** | (가) M7b까지 보류 — 근거: 탐지 이점 없음 · **CPU 지연 미실측(GAP-7)** · 가중치 심사 | 근거 중 「CPU 지연」은 G5로 사라진다. **권장 변경 제안 (나′)**: G5 준비 뒤 **오프라인 백테스트만** M2와 병행하고, 운영 경로 반영은 M7b 게이트를 그대로 둔다 → R.11 재질의 |
| §10 G-10 | 폐쇄망 서버 내 평가 | 그 서버 = G5(J-20 통과 전제) |
| §11 R-17 | venv 비대화 | GPU 스택은 G5에만 둔다 → 조사 서비스 배포 부담이 없다 |
| §7 M7b | `ml_batch`에서만 추론 · torch CPU | G5 `ml_batch`(백테스트·배치)·`ml_service`(온라인 예측 구간) · CUDA |
| §13 D-223 초안 2항 | 배치 = `sre_agent` 안 `run_service` + `ml_batch` | + **실행 노드 G5**(`ml_service`·`ml_batch`) · G5 장애 폴백 의무 · G5 자격증명 = MCP 토큰만 · **[v6]** G5 = ML 장애 분석 서버(GA-1~GA-6) · 판정 독립 I-11 |
| **[v6]** §R.1 흐름 · S4 표 | S4 전체가 `run_service` | S4-A·S4-C2·S4-F는 `run_service` 경량판으로 남는다. 광역·다차원·임베딩판은 G5 GA-1·GA-2·GA-3이 S3와 병행해 만든다 |
| **[v6]** §4.1 불변식 | I-1~I-10 | **I-11 판정 독립** 추가 — S5 필수 조건은 `run_service` 출력만 읽는다 |
| **[v6]** §5.4 CU-28 `dim_localize` | M5 `run_service` 분석 함수 | 실행 노드 = G5 `ml_service`(GA-2) · `run_service`는 S4-C2 1차원판만 |
| **[v6]** §5.5 F4 2차 e5 | `ml-embed` extra · CPU | G5 GPU(GA-3) · 1·2차 우열을 리플레이셋으로 판정하는 규칙은 그대로다 |
| **[v6]** R.10.1 호출 계약 | `ml_service`는 관측 데이터를 직접 읽지 않는다 | GA-1·GA-2에 한해 MCP로 읽는다(사건당 상한 · J-21) |
| **[v6]** R.10.3 인스턴스 | g5.2xlarge 출발 | **g5.4xlarge 출발**(온라인 CPU 작업) |

### R.11 v5·v6 Wave · 확인 사항 · 게이트

**Wave 갱신**(R.6 표를 이 순서로 대체 — G-12 (가) 전제)

| 순서 | 묶음 | v5 추가분 | 완료 판정 |
|---|---|---|---|
| ① | M0 축소판 | **J-17~J-20** 추가 | 데이터 카드·보안 확인서 |
| ② | M-R1 결정적 사슬 | RC-15·RC-16·RC-17·RC-18(T1~T4 · 순수 Python) · XS 블록 · **F-3a** | 녹화 픽스처로 R.8.5 사건이 시작 소스(폴스타·제니퍼)와 무관하게 **같은 XS-1·CPU-6**으로 판정되는 종단 테스트 · off 비트 동일 |
| ②′ | **M-F 함수 단위 집계** | RC-8 확장(F-1 파서) · `apm_active_sample`(F-2) · `profile_hotspot` 합의 | 합성 픽스처(두 문법) + 운영 녹화 3건으로 `coverage ≥ 0.9`·`sum_check ≤ 0.1` · 합의 순위 결정성 테스트 |
| ③ | M1 기반 + **M-G5 장애 분석 1차**(v6) | G5 프로비저닝·설치(R.10.4 1~7) · 모니터링 · **RC-22** `/v1/analyze` 비동기 잡·클라이언트·폴백 · **RC-23 GA-1 광역 스캔**(+ `ml_batch` 기준선 적합) · **RC-24 GA-2 다차원 국소화** · **RC-25** I-11 보조 조건·불일치 절 — 학습 라벨이 필요 없는 분석부터 한다 · **J-20 통과 뒤** | GPU 자가 점검 · 폴백 종단 테스트(G5 차단 시 GA별 `limitations` 표기 · S5 결론이 G5 유무와 무관하게 같음 — I-11) · 녹화 픽스처로 GA-1이 T3 추가 후보를 내는 종단 테스트 · `ML_SERVICE_ANALYZE` off 비트 동일 |
| ④ | M-R2 통계 분석 | RC-19(T5 · G5 `ml_batch`) → **GA-4 조회** | 동시발생 리포트 1회 → `plans/144` W6 보정 입력 전달 · GA-4 조회 결과가 RC-19 표와 일치 |
| ⑤ | M2~M4 | **GA-3** 유사 사건 하이브리드(병기 → §5.5 우열 판정) · **GA-6** 예측 구간·ETA(G-7 (나′) 백테스트 → M7b 게이트 뒤 노출) | §7 · §6.3 |
| ⑥ | 조건부 | **GA-5** 학습 원인 순위(M7c 게이트 — 그 전 섀도) · F-3b(G-16 (나)일 때) · G5 LLM 서빙(부차 · G-17 기준 충족 시 · 별도 인스턴스) | 각 게이트 |

**신규 구현(v6) RC-22~RC-25** — R.10.1 GA를 돌리는 데 새로 필요한 것

| ID | 대상 | 내용 | 패키지 | 플래그(기본 off) |
|---|---|---|---|---|
| RC-22 | `sre_agent/ml_service`(엔트리) · `infrastructure/ml/analysis_client.py` | `/v1/analyze` 비동기 잡(POST 접수 · GET 회수) · `/healthz` · `run_service` 쪽 S2 직후 요청 · S5 진입 시 마감 회수 · GA별 `limitations` 폴백 표기 · `rca_bundle.ml_service` 감사(요청 id·모델 해시·기준선 버전·소요) | sre_agent(G5 · run_service) | `ML_SERVICE_ANALYZE` — off면 요청 자체를 보내지 않아 v5와 비트 동일 |
| RC-23 | `domain/ml/wide_scan.py` · `ml_batch` 작업 `baseline_fit` | GA-1 — 계열별 STL·MAD·PCA 기준선 야간 적합·캐시 · 온라인 사건 창 잔차·onset · MCP 조회 상한(J-21) | sre_agent(G5) | 같음 |
| RC-24 | `domain/ml/dim_localize.py`(= CU-28) | GA-2 — Squeeze/PSqueeze·HALO · 외부 원인 판정 · S4-C2 대조 | sre_agent(G5) | 같음 |
| RC-25 | `rca_rules.yaml` · 보고 양식 | I-11 — 규칙별 필수/보조 조건 구분 · 보조 후보 `origin` · 「ML 분석 불일치」 절 · 보고서 「ML 장애 분석(G5)」 절(GA별 결과·폴백 사유) | sre_agent | `RCA_DECISION_TABLE` |

**확인 사항(사실 확인)**

| ID | 내용 | 방법 | 막는 것 |
|---|---|---|---|
| **J-13** | **해소(v5)** — 서비스별 CPU 필드는 `totalCpuTime`·`cpuTimePerTransaction`이다(스펙 · 게이트웨이 파싱 실측). `sort_by` 수용만 미확인이고, 클라이언트 정렬로 우회한다 | 운영 녹화 1회로 수용 여부만 | — |
| J-14(확장) | 녹화 3건에 **완료 트랜잭션 profile.txt**와 **active list 12회 연속**을 함께 담는다 | 운영 녹화(마스킹 반출) | F-1·F-2 |
| **J-17** | 시계·보존 — 폴스타 DB 서버·제니퍼 서버·게이트 호스트의 NTP 동기 여부와 오프셋 · 제니퍼 이벤트 DB 보존 기간과 90일 기간 조회 가능 여부(부하 포함) | 운영 확인 · 같은 순간 양쪽 시각 1회 대조 | T1 `δ` · T5 입력 |
| **J-18** | 제니퍼 이벤트 유형 운영 명칭(CPU 계열 포함) — U-1·U-13과 같은 확인 | 운영 제니퍼 이벤트 목록 | RC-15 |
| **J-19** | 제니퍼 에이전트 메서드 프로파일 설정 범위(대상 클래스·메서드 · 자동 프로파일 여부) · active list `runningMode`·`runningFullText`의 운영 값 형태 | 제니퍼 관리 화면·운영 녹화 | F-1 범위 · F-2 해석 |
| **J-21**(v6) | GA-1·GA-2 광역 조회 부하 — 사건당 엔티티 수 × 지표 수 분포 · 그 조회가 `mcp_server`(폴스타 DB)·`apm_gateway`(제니퍼)에 주는 부하와 지연 · 동시 사건 수 상한 | 리플레이셋 사건 20건으로 조회 계획만 산출(실행 0) → 운영 저부하 시간 1회 실측 | R.10.1 상한값 · GA-1 p95 |
| **J-20** | G5 반입·보안 — 금융권 클라우드 이용 절차·보안성 검토 적용 여부 · 패키지·가중치 반입 경로(내부 미러/S3 엔드포인트) · 네트워크 경로(G5 → `mcp_server`·`apm_gateway`·Redis) · 로그 수집 경로 · 예산·인스턴스 크기 | 기관 보안·인프라 담당 | **G5에 운영 데이터 투입 전부** |

**게이트**

| G | 질문 | 선택지 | 권장 · 근거 |
|---|---|---|---|
| **G-14** | 프로파일 파서 소유 | — | **해소(v5 · 사용자 지시)**: (가) 게이트웨이 소유 파서로 확정하고 F-1·F-2·F-3a 세 경로와 합의 집계로 넓힌다(R.9) |
| **G-15** | G5에 맡기는 범위 | — | **확정(2026-10-13 사용자 답변)**: 운영망 연결 VPC · 온라인 추론 + 배치 + 학습을 G5에서 · 기본 ML 전용. **[v6 확정 · 같은 날 사용자 지시]** G5의 목적 = **ML 장애 분석**이다. **(다) 혼합 배치**(R.10.1)로, 판정 필수 경량 통계는 `run_service`에 두고 GA-1~GA-6 무거운 장애 분석과 그 학습·배치는 G5에서 한다. LLM 서빙은 부차다. **J-20 통과가 투입 전제다** |
| **G-16** | L3에 `jcmd <PID> Thread.print`(F-3b)를 허용하는가 | (가) 허용하지 않는다 — F-1·F-2·F-3a로 운영하고, 부족이 실측되면 재질의한다 (나) 가드형으로 허용(`timeout 20 nice -n 10` · 사건당 3회 · WAS 실행 사용자 권한 규칙 · 출력 마스킹) | **(가)** — D-168(Plan 78 W7-1)은 「필요한 신호는 기존 명령으로 얻는다 — allowlist를 확장하지 않는다」를 명시했다(`sre_agent/toolset_profiles.py:120-123`). D-189 불변식(읽기 전용·변경 명령 물리 제외)도 있다. `jcmd`는 safepoint 정지와 실행 사용자 권한(sudo) 확대를 부른다. **재질의 조건**: F-1 `degraded` 비율 ≥ 50% 또는 CPU-9(제니퍼 미등록 WAS) 사건 비중이 크다고 실측될 때 |
| **G-17** | G5 LLM 서빙 착수 기준 | (가) R.10.6 기준(툴 호출 성공률 < 95% 또는 ECR 유의 저하)을 실측했을 때만 (나) 처음부터 G5 vLLM 병행 | **(가)** — 사용자 답변(「툴 호출이 필요할 때만 2」)의 운영 기준이다. 남은 결정은 **임계값**뿐이다 |
| **G-7**(재질의) | 파운데이션 모델 도입 시점 | (가) 기존 — M7b까지 보류 (나′) G5 준비 뒤 오프라인 백테스트만 M2와 병행하고, 운영 반영은 M7b 게이트 유지 | **(나′)** — G-7 (가)의 근거 셋 중 「CPU 지연 미실측」은 G5로 사라졌다. 「탐지 용도 이점 없음」과 「가중치 보안 심사」는 남는다. 그래서 운영 경로는 그대로 막고 **증거 수집(백테스트)만** 앞당긴다 |

---

## 1. 요구 해석과 범위

### 1.1 기능 축

| 축 | 정의 | 출력(모두 수치·구조화) | 소비처 |
|---|---|---|---|
| **F0 기반** | 데이터 카드 · 엔티티 정합 · 리플레이 평가셋 · 조사 결과 영속화 | 데이터 품질 보고서, 평가셋, 채점 하네스 | 개발·운영자 |
| **F1 이상탐지** | 계절·추세를 뺀 뒤의 이탈, 수준 이동, 계층 간 관측 불일치 | 이상 점수(상대 순위) · 구간 · 변화점 시각 | HolmesGPT 도구 · 게이트 주석 |
| **F2 예측** | ① 자원 고갈 ETA ② 기대 대역 ③ (라벨 축적 후) 사건 위험 점수 | ETA 분포(분위수) · 예측 구간 · 위험 순위 | 대시보드·브리핑 · (게이트 확정 시) 예측 이벤트 |
| **F3 진단·RCA** | 후보 원인(엔티티·지표·계층)의 순위화와 근거 | 후보 순위 · 척도화 편차 · 기준선 대비 · 근거 지표 | HolmesGPT 도구 · 브리핑 `root_cause_hypotheses` |
| **F4 유사 사건 검색** | 과거 장애·조치 기록 중 현재 사건과 닮은 것 | 유사 사례 상위 k(시간 감쇠 유사도) · 당시 원인·조치 | HolmesGPT 도구 |

### 1.2 범위 밖

- **자동 조치**: D-003 · `plans/64` §8.3 B-3 착수 금지 · `plans/91` L51. ML 결과로 무엇을 실행하는 경로를 만들지 않는다.
- **제니퍼·DPM 커넥터 구현 자체**: 제니퍼는 `plans/87`(J1~J2) 소관이다. DPM은 **전용 계획이 없으므로 신설이 필요하다**(G-3). 이 계획은 ML이 요구하는 **읽기 계약**만 정의한다(§5.7).
- LLM provider 교체, text2sql 경로, HolmesGPT 조사 루프 자체의 재작성.

### 1.3 해석 정정 · 용어

- **"DPM"** = DB 성능 모니터링 솔루션(`plans/55` L69 "맥스게이지·InterMax 등"). 운영 기관의 실제 제품·API 존재는 **미확인**이다(J-8).
- **"예측"**은 두 종류를 분리한다. ⓐ **추세 외삽**(디스크가 언제 90%에 닿는가)은 라벨 없이 지금 할 수 있다. ⓑ **사건 예측**(한 시간 뒤 장애가 날 확률)은 확정 사건 라벨이 수십 건 이상 필요하다. 문헌도 이 둘을 다른 문제로 다룬다(PRED-F vs PRED-E).
- **"점수"는 확률이 아니다.** ML 도구 응답은 모든 점수에 `score_semantics: "relative_rank"`를 명시한다. 보정된 확률로 쓰려면 conformal 구간(PRED-F-04)을 거친 값만 쓴다.

---

## 2. 현행 실측 — 무엇이 이미 있는가

### 2.1 조사 경로(`sre_agent`)

| 항목 | 실측 | 근거 |
|---|---|---|
| 스택 | Python 3.13.1 · holmesgpt **0.36.0**(하한만 `>=0.36.0`, 최신 0.42.0 2026-09-16) · mcp 1.25.0 · **numpy·pandas·sklearn 미설치** | `sre_agent/pyproject.toml:6-12` · venv 실측 |
| 진입 | push: 게이트 PAGE → `sre_investigate_alarm`(MCP SSE 9098) / pull: `src/nodes/fault_diagnosis.py:81` → `sre_diagnose` | `noise_gate/application/nodes/investigation_trigger.py:78` |
| 결정적 사전수집 | 지표 4종(cpu·memory·filesystem·disk_io) × **시간 단위 `avg_val`** · baseline 24구간 · 알람 1 · (변경 1) | `application/evidence_prefetch.py:26-43,139` |
| 결정적 상관 | baseline 평균·표준편차 z ≥ 3.0 또는 절대 임계 → onset · sustained/spike · 선행 지표. **확률 점수 없음** | `domain/correlation.py:17-19,107-160` |
| 가설 순위 | 규칙: 변경 선행 rank 1(medium) → 선행 지표(sustained high / spike medium) → 기타 이상 → LLM 인용 원인 | `application/briefing_builder.py:62-123` |
| 브리핑 계약 | 알 수 없는 키도 **말미에 자동 렌더**된다 → 신규 키 추가 시 소비자 수정 0 | `noise_gate/domain/investigation_briefing.py:114-121` |
| HolmesGPT 확장 지점 | 사용 중: `Config(toolsets, mcp_servers)` · `system_prompt_additions`. 미사용: `custom_toolsets` · `additional_toolsets` · `custom_skill_paths`(SKILL.md) | `diagnosis.py:140-173` · holmes `config.py:57-188` |
| MCP 결과 전달 | 0.36.0은 MCP 결과의 **text content만** 모델에 전달한다. `structuredContent` 전달은 0.42.0(PR #2459)부터 | 라이브러리 조사 §2.1 |
| 휘발성 | 잡 in-memory 500건 · TTL 3600초. 감사 JSONL `sre_agent/.data/investigation_audit.jsonl`에는 요약만 | `investigation_jobs.py:52-56` · `investigation_dispatcher.py:414-457` |
| 관찰(참고) | 실 조사 실행 여부가 `gemini_api_key is None`에 묶여 있다 — 운영 LLM을 `model`/`api_base`로만 설정하면 stub로 끝날 수 있다(의도 여부 미확인 · 본 계획 범위 밖 · 보고만) | `investigation_dispatcher.py:238` · `mcp_service.py:283` |

### 2.2 노이즈 게이트(`noise_gate`)의 통계 자산

| 자산 | 알고리즘 | 입력 | 플래그(기본) |
|---|---|---|---|
| 알람 이력 패턴(D-035) | 24h/7d/30d 건수 · 간격 CV · 첫 발생/급증/주기/산발 | `cmm_alarm` 90일 | `ALARM_HISTORY_ENABLED`(True) |
| 동적 baseline(D-110) | 순수 Python Holt-Winters(주기 24) · 잔차 z>3→2, z>4.5→3 · **escalate-only** | `stat_h` cpu·memory 168행 | `dynamic_baseline_enabled`(F) |
| STL(D-113) | statsmodels robust STL · 잔차 **표준편차** z · 실패 시 HW 폴백 | 위와 같음 | `anomaly_stl_enabled`(F · 만료 2027-02-20) |
| 크로스호스트 상관 | 토큰 Jaccard 온라인 그리디 군집 + 토폴로지 보너스 | 알람 스트림 | `cross_host_correlation_enabled`(F) |
| 플래핑·스톰·인히비션·자가복구 | Nagios식 %-state-change · 60초 5건 · 300초 창 | 알람 스트림 | 각 (F) |
| 토폴로지 연쇄(D-107) | `AVAIL_DEPEND` BFS | `cmm_resource` | `multi_hop_cascade_enabled`(F) |
| 변경 상관(D-111) | 알람 직전 3600초 `lifecycle_history` 겹침 | 변경 이력 | `change_correlation_enabled`(F · 만료 2027-02-20) |
| e5 임베딩(D-114) | 로컬 모델 · 코사인 · **감사·주석 전용** | 알람 텍스트 | `semantic_dedup_annotation_enabled`(F) |

- **모든 이상 계산은 알람이 도착한 순간에만** 돈다. 알람 전에 상시 스코어링하는 경로는 없다.
- 워커의 탐지 상태는 in-memory dict라 재기동하면 사라진다(`alarm_worker.py:106-137`).
- AI 판단의 경계는 이미 정해져 있다: **escalate-only 상향 · 주석 · 브리핑까지**. 최종 티어·억제·심각도3 PAGE는 결정적 코드가 전담한다(`plans/60` §15.3-15.4).

### 2.3 관측 데이터 읽기 경계(`mcp_server`)와 데이터

| 원천 | 읽기 도구 | 해상도·제약 |
|---|---|---|
| 폴스타 메트릭 | `polestar_metric_trend(kind∈{cpu,memory,filesystem,disk_io}, granularity∈{h,d,m}, reference_time?, lookback_minutes?, baseline_periods?)` | **시간/일/월 집계만** · 행 상한 `max_rows`(기본 10000) · 앵커 lookback 30일 · `avg/min/max`만 사용(`top_val`·`bottom_val`은 미사용) |
| 폴스타 알람 | `polestar_alarm_history` · `polestar_incident_alarms` · `polestar_condition_log` | 초 단위 CTIME |
| 변경 | `polestar_change_history` | **PG 전용**(DB2 미지원) · `event_time` 단위(초/ms) 미확정 · dev 데이터는 합성(`noise_gate/infrastructure/change_feed.py:13-21`) |
| 토폴로지 | `polestar_topology`(PG 재귀 · DB2 1홉) | `AVAIL_DEPEND`만 |
| 프로세스·OS | `polestar_process_snapshot`(REST) · `polestar_os_config` | 현재 시점 |
| Prometheus | `prom_metric_instant/range`(서버측 `nodename` 조립) | step 자유 · **운영 `PROMETHEUS_URL` 공란** |
| 스키마에만 있는 원천 | `metricstatisticseconddata`(초 단위 컬럼) · `cmm_trait_history_YYYYMM` · `cmm_availability_log` · `ping_down_history` · `cmm_dependency_link` · `cmm_service_associate` · `cmm_alarm_note(alarmcause)` · `cmm_alarm_knowledge(faulttypename, processcontent)` · `cmm_alarm.root_alarm_id/prev_alarm_id` | **운영 적재 여부 미확인**(J-2·J-3). 로컬 `testdata/pg/init`은 테이블당 5행 더미라 학습·검증 불가 |

- DB2(`polestar_b0`)는 FileSystem 단수형만 있고 Disks MaxIORate가 수집되지 않는다(`config/knowledge/polestar_b0/catalog.yaml:5-19`). **존별 피처 결손**을 설계에 넣어야 한다.

### 2.4 라벨·결과 원천

| 원천 | 단위 | 담긴 것 | ML 용도 |
|---|---|---|---|
| `FeedbackStore` `logs/alarm_feedback.jsonl`(D-177) | **알람 유형** | `noise`/`valid` · `investigation_id`(선택) · 20,000줄에서 `.1` 회전 | 게이트 탐지기 선택 라벨(Opprentice식). 이벤트 키가 없다 |
| `DecisionStore` `logs/alarm_decisions.jsonl` | 알람 이벤트 | tier · signals 스냅샷 · `resolution{duration}` · `recurrence` · `investigation{verdict}` | 약한 라벨 · 특징 |
| `alarm_incidents`(PG, D-049) | 사건 | firing→ack→resolved · `resolution(clear/self_heal)` | "사람이 대응한 사건" · MTTA/MTTR. **`incident_tracking_enabled` 기본 off**(`src/config.py:913`) |
| silence 규칙 | 알람 유형 | 운영자 억제 사유 | 노이즈 약한 라벨 |
| 폴스타 `cmm_alarm` ack·note·knowledge | 알람 이벤트 | ack 사용자·시각 · `alarmcause` · 장애 유형·처리 내용 | **원인 라벨 후보**(채움률 미확인) |
| 조사 감사 JSONL | 조사 | level·confidence·signals·leading_signal 요약 | 브리핑 채점에는 부족 |
| 장애 보고서(ITSM 등) | 사건 | 경위·원인·조치 | **존재·접근 미확인**(J-5). 금융권은 작성 의무가 있어 코퍼스가 있을 가능성이 높다(RCA-F-06 근거) |

### 2.5 의존성·반입

- 루트 venv(개발): Python 3.12.11 · numpy 2.5.1 · **pandas 3.0.5** · scipy 1.18.0 · scikit-learn 1.9.0 · statsmodels 0.14.6 · torch 2.13.0. `mcp_server`도 이 venv를 쓴다.
- **폐쇄망 반입 목록 `wheels/requirements_all.txt`에 ML 패키지가 없다.** statsmodels도 "폐쇄망 미반입·보안 협의 미완"이다(`noise_gate/infrastructure/metric_stl.py:8-13`, `plans/91` 1-16 기한 2027-02-20).
- **`sre_agent` venv는 루트와 분리돼 있다**: Python 3.13.1 · holmesgpt 0.36.0 · numpy·pandas·scipy·scikit-learn·statsmodels·redis **미설치**(실측). ML 스택을 extra로 얹었을 때의 해석 가능성은 §3.5.1에서 실측했다.
- **v2**: 사용자 허용에 따라 버전·라이브러리는 필요 시 올리거나 추가한다. 운영 반입 일정만 J-6에서 확인한다.

### 2.6 계획·결정 지형

- **C5 예측은 소유자가 없다.** `plans/62` L113의 "Plan 63(예측)은 별도 작성됨"은 사실과 다르다(실제 63은 과적합 분리). `plans/85` L370도 같은 표기를 따른다 → 이 계획이 인수하며, 두 문서의 정정은 §12 「보고」에 남긴다.
- **baseline이 두 벌이다.** 게이트 쪽 HW/STL(D-110·D-113)과 조사 쪽 정적 z-score(D-197)이며, 패키지 경계(D-118·D-139) 때문에 서로 import할 수 없다. 이 계획은 **세 번째 벌을 만들지 않고 수렴 경로**를 둔다(§4.6).
- **"학습형 미채택" 선례**: `plans/82` §17.3이 학습형 CQ 예측기를 "라벨 없음·D-035·계측 먼저"로 미채택하고 재검토 조건을 남겼다. D-048.5·.11도 "ML 모델 미사용"을 명시한다. 이 계획은 **라벨이 필요 없는 단계부터** 시작하고, 지도학습은 라벨 수 조건을 충족할 때만 연다(M7c).

---

## 3. 문헌 조사 결과 — 설계 원칙으로 옮긴 것

> 서지·수치·검증 등급의 정본은 동반 조사 문서 3종이다. 여기에는 **설계를 바꾼 결론**만 옮긴다. 인용수는 영향력 근거로 쓰지 않는다(OpenAlex 과소집계).
> 검증 등급: **V1** 원문 본문·표 대조 · **V2** 초록·출판사/DOI 메타데이터 · **V3** 2차 자료.

### 3.1 이상탐지 — "단순·강건·계절 분해"가 기본값이다

- **P-1 강건 통계 우선.**
  - TSB-AD(NeurIPS'24 D&B, V1)는 하이퍼파라미터를 튜닝한 40종을 비교했고, 결론은 *"simpler architectures and statistical methods often yield better performance"*다. 단변량 VUS-PR 순위는 Sub-PCA 0.42 · KShapeAD 0.40 · POLY 0.39 · … · IForest 0.30 · TranAD 0.26 · Donut 0.20 · Anomaly Transformer 0.12다.
  - Sarfraz et al.(ICML'24 Position, V1): SMD F1은 PCA 재구성오차 0.572 · GDN 0.526 · TranAD 0.457 · Anomaly Transformer 0.426 · USAD 0.426 · OmniAnomaly 0.415 · 무작위 0.080이다.
  - Schmidl et al.(PVLDB'22, V1)은 71종·976 데이터셋을 평가했다. 원문은 *"deep learning approaches are not (yet) competitive"*, *"no single algorithm clearly performs best"*다.
  → 1차 탐지기는 **robust STL(일 24h + 주 168h) 잔차의 MAD 척도 점수**, Seasonal Naive 대역, 변화점(BOCPD·PELT)이다. 2차는 PCA 재구성오차·IsolationForest·1-NN 거리다. 딥 탐지기(OmniAnomaly·USAD·Transformer 계열)는 들이지 않는다.
- **P-2 잔차 척도는 표준편차가 아니라 중앙값·MAD(또는 IQR)다.**
  - S-H-ESD(PRED-B-06), Grafana ML의 24시간 rolling median MAD(산업 자료), BARO RobustScorer의 중앙값/IQR(RCA-A-09, V1)가 같은 방향이다.
  - 현행 `metric_stl.py`·`correlation.py`는 평균·표준편차 z를 쓴다. 이상치가 baseline에 섞이면 σ가 부풀어 둔감해진다.
- **P-3 계절성 요건은 실측으로 확정한다.**
  - Datadog 공식 문서는 계절 알고리즘에 **최소 계절 주기의 3배 이력**을 요구한다(주간 계절 3주 · V2).
  - 금융권 월말·월초·급여일 효과를 명시한 벤치마크는 **없다**(공백). Prophet·Greykite식 달력 회귀자(PRED-F-01·I-01)를 결정적 플래그로 둔다.
- **P-4 파운데이션 모델의 제로샷 이상탐지는 점 이상·변화점 보조에만 쓴다.**
  - TSB-AD에서 파운데이션 모델은 점 이상에 강하고(TimesFM 1위, Chronos 3위) 구간 이상에 약하다.
  - Uray 2026(preprint)에 따르면 TimesFM이 지속 이상에서도 동역학을 너무 잘 따라가 이상을 구분하지 못한다.
  - **지속 열화(누수·증가)는 추세·ETA 규칙이 판정한다.**
- **P-5 시간 집계의 주 용도는 추세·패턴 이탈이다(추론 · 문헌 근거 없음).** 시간 평균은 분 단위 스파이크를 평활화한다. 폴스타 `_h`로 "짧은 스파이크 탐지"를 약속하지 않는다. `top_val`·`max_val`을 함께 써서 평활화 손실을 일부 보완한다(§5.2).

### 3.2 평가 방법론 — 이 계획의 채택 게이트

- **P-6 point-adjust 금지.** 무작위 점수의 F1-PA가 SWaT 0.969 · SMD 0.804인 반면, PA를 뺀 F1은 0.216 · 0.080이다(Kim et al. AAAI'22, V1). TSB-AD에서 무작위 점수는 PA-F1로 32개 탐지기 중 26위였다.
- **P-7 오프라인 알고리즘 비교는 VUS-PR**(PRED-D-03·D-04), **운영 채택은 이벤트 단위**로 판정한다. 이벤트 단위 지표는 정밀도·재현율, 탐지 지연 분포, **알람 예산**(서버-일당 알람 수)이다.
- **P-8 예측·선제 탐지는 리드타임 분포로 채점한다.** Salfner et al.(ACM CSUR'10)의 정의를 따라 리드타임 Δt_l ≥ 최소 경고시간 Δt_w일 때만 "예측"으로 센다. 겹치는 창의 중복 양성은 사건당 1회로 접는다. eWarn의 창 단위 F1(0.82)은 이 구조 때문에 부풀었을 수 있다(PRED-E-04 한계).
- **P-9 의무 기준선.** 기준선을 이기지 못하는 모델은 채택하지 않는다.

  | 과업 | 의무 기준선 |
  |---|---|
  | 탐지 | 무작위 · 입력값 자체 · Seasonal Naive · robust STL+MAD · PCA 재구성 · 1-NN 거리 · **다중 창 median 편차**(v3 · K-7) |
  | 예측 | Seasonal Naive · AutoETS · AutoARIMA · **다중 창 median의 median**(v3 · K-7 · Web Traffic 경기의 표준 공개 기준선) |
  | RCA | Dummy · max-\|Z\| · 알람 건수 · BARO식 강건 점수 |
  | 사건 예측 | 단순 스파이크 규칙(AirAlert에서 제안 기법과 0.6~2.7점 차이) · 연관규칙 · **피처 엔지니어링 없는 기본 설정 GBDT**(v3 · K-2 검증용 — 피처가 점수를 만든 게 맞는지 이 기준선과의 차이로 본다) |

- **P-10 합산 리더보드 금지 · 계층별 분리 보고.** OpenRCA·RCAEval·PetShop의 서브시스템 11개를 분석한 결과, 합산 1위를 고르면 최대 5개 서브시스템에서 더 나쁜 방법을 고르게 되고 후회가 최대 24.8%p였다(RCA-B-05, preprint V1). 존(은행존/공동존)·업무·계층(호스트/WAS/DB)별로 따로 보고한다.
- **P-11 시간순 분할 · 섀도 운영.** rolling-origin 분할을 쓰고 셔플은 금지한다. 테스트 구간에 **월말을 2회 이상** 넣는다. 결정 경로에 연결하기 전에 섀도 모드로 월말 2회 이상 병행한다.
- **P-12 공개 벤치마크 수치는 채택 근거에서 뺀다.**
  - 사전학습 데이터가 오염됐을 수 있다(Chronos-2가 GIFT-Eval 훈련 구간과의 부분 중첩을 명시함).
  - 벤치마크 자체의 결함도 있다(RCAEval 본문 서술과 표 불일치 · 동일 저자군이 방법과 평가를 함께 만듦).
  - 판정은 **내부 리플레이셋**으로만 한다.

### 3.3 예측 — 통계로 시작하고, 파운데이션 모델은 라이선스·CPU 관문 뒤에 둔다

- **P-13 고갈 ETA = 추세 외삽 + conformal 구간.** EnbPI(ICML'21)·ACI(NeurIPS'21)는 임의 예측기를 감싸 분포 이동에도 커버리지를 유지한다. 출력은 "72시간 안에 90% 도달 확률 구간"처럼 **구간**이다. 대상은 디스크·메모리(폴스타), JVM 힙(제니퍼·소프트웨어 노화 PRED-F-05), 테이블스페이스·세션(DPM)이다.
- **P-14 통계 기준선은 버리지 않는다.**
  - BOOM(관측 데이터)에서 Time-MoE(0.881)는 Auto-ARIMA(0.824)보다 나빴다. 파운데이션 모델이라고 모두 이기지는 않는다.
  - AutoETS는 GIFT-Eval 일부 계열에서 WQL −648.9%로 파국적으로 실패했다. 단독으로 두지 않고 Seasonal Naive와 함께 둔다.
- **P-15 파운데이션 모델 후보·배제 목록**(2026-09-17 HF API 실측)

  | 구분 | 모델 | 근거 |
  |---|---|---|
  | **후보**(Apache-2.0 · CPU 근거) | Chronos-2 small(28M · 112MB · 저자 "CPU-only settings" 명시) · Chronos-Bolt small/mini · Toto-2.0-4m/22m(관측 데이터 특화 · 4m은 "Edge/CPU" 권장) · TimesFM **2.5** · TTM-R2(0.8M · CPU-only 명시) | PRED-G-02·G-03·G-06·G-08 |
  | **배제**(라이선스) | **TimesFM 3.0**(가중치 비상업·비운영) · **Moirai 전 계열**(CC-BY-NC-4.0) · **TiRex 1.x**(연매출 1억 유로 초과 조직 상용 라이선스) · TimeGPT(가중치 비공개) | 모델 카드·LICENSE 원문 |
  | **보류** | TiRex-2(출시 3개월 미만 · `model.ckpt` pickle 계열 추정) · PatchTST-FM-r2(공개 8일차) | 라이브러리 조사 §1.4 |

  → **버전만 바꿔도 라이선스가 뒤집힌다**(TimesFM 2.5 → 3.0). 반입 시 가중치 SHA-256과 라이선스 문자열을 함께 고정한다.
- **P-16 사건 예측(eWarn식)은 라벨 조건부다.**
  - eWarn(ESEC/FSE'20, V1)은 China EverBright Bank 11개 시스템 × 3년 데이터를 썼다. 시스템당 사건 26~227건이고 리드타임은 10분, 평균 F1 0.82다.
  - AirAlert(WWW'19, V1)에서는 단순 스파이크 규칙이 제안 기법에 0.6~2.7점 차이로 근접했다.
  → 사건 라벨이 시스템당 수십 건 쌓이고 운영 리드타임 요구치(Δt_w)가 합의된 뒤에만 착수한다(M7c).

### 3.4 RCA — "자동 인과 발견"이 아니라 "규칙·강건 점수·지식 그래프"

- **P-17 자동 인과 발견은 운영 경로에서 회피한다.**
  - ASE'24(V1)의 판정은 *"PC / FCI / Granger / LiNGAM / fGES / NTLR-PageRank/random walk, CausalAI, RUN, and MicroCause mostly perform similarly to Dummy"*다.
  - PCMCI의 그래프 구성 F1은 0.12(PC 0.49)이고, 50노드에서는 0.04다. 라이브러리(tigramite)는 **GPL-3.0**이다.
  - 실환경 인과 그래프에는 정답이 없어 검증할 수단 자체가 없다(공백 10).
- **P-18 단순 기준선이 강하다.**
  - 공개 벤치마크 4종에서 SimpleRCA가 SOTA와 같거나 앞섰다(RCAEval Top-1 0.58 vs BARO 0.24).
  - 장애 1,430건 벤치마크에서는 11개 SOTA의 Top-1 평균이 0.21, 최고가 0.37(MicroRCA)이었다. 트레이스 기반 딥러닝은 무너졌다(Nezha 0.04 · Eadro 0.16).
  - 일부 서브시스템에서는 max-|Z|(0.544)가 BARO(0.200)를 이겼다.
  → **BARO식 강건 점수를 기준선으로 채택**한다(MIT · 0.01초/건). 단 max-|Z|·알람 건수·Dummy와 **항상 병렬로 산출**한다.
- **P-19 이벤트 그래프가 우리 자산 구성과 가장 같다.** Groot(ASE'21 · eBay · V1)는 메트릭·로그·**변경**을 이벤트로 요약하고, 서비스 의존과 SRE 규칙으로 간선을 둔다. 실운영 인시던트 952건에서 top-3 95% · top-1 78%였다. 폴스타 알람 + 변경 이력 + `AVAIL_DEPEND` + 운영자 규칙과 구성이 같다.
- **P-20 변경을 1순위 원인 가설로 둔다.**
  - SCWarn(ESEC/FSE'21, V1)의 **대형 상업은행 2년치 데이터에서 인시던트의 약 50.4%가 나쁜 변경 기인**이었다.
  - Gandalf(NSDI'20, V1)는 Azure에서 시공간 상관 순위화로 precision 92.4~94.9%를 냈다.
  - FUNNEL(CoNEXT'15)은 **이중차분(DiD)**으로 변경 대상과 비대상 그룹을 비교한다. 금융권은 이중화 서버군이 흔해 대조군을 만들기 쉽다.
  → 현행 C′-2(변경 직후 rank 1)의 방향이 맞다. 여기에 **대조군 비교**를 더한다.
- **P-21 계층별 검증된 원리를 쓴다.**

  | 계층 | 원리 | 근거 |
  |---|---|---|
  | 알람 | 알람 스톰 탐지·대표 요약 | China EverBright Bank 실배포, F1>0.9, 검토 알람 98%↓(ICSE-SEIP'20, V1) |
  | 알람(하이브리드) | 통계가 먼저 결정하고 **저신뢰 쌍만 LLM**에 넘김 | COLA(ICSE-SEIP'24, V1) — 동료심사 하이브리드 중 사실상 유일 |
  | WAS(제니퍼) | 파생 지표(평균 응답시간·에러율) 다차원 국소화 | Squeeze/PSqueeze(ISSRE'19 · JSS'23, **여러 은행 사례** · PSqueeze 외부 원인 판정 F1 0.90) · HALO 계층 인지(KDD'21 · precision 86.9% · recall 93.0%) · Adtributor(NSDI'14) |
  | DB(DPM) | DB Time/AAS를 대기 클래스·SQL·자원으로 분해 | ADDM(CIDR'05) |
  | DB(DPM) | 간헐 슬로우 쿼리, **군집당 1회 DBA 라벨** | iSQUAD(PVLDB'20 · Alibaba F1 80.4% · DBSherlock 대비 +49.2%) |
  | DB(DPM) | "상관된 SQL ≠ 원인 SQL" | PinSQL(ICDE'22) |
  | DB(DPM) | **DBA가 작성한 구조 그래프 + 회귀 가설검정** | CIRCA(KDD'22 · 은행 Oracle 99건 AC@1 0.404/AC@5 0.763 · CPU 0.578초 · BSD-3) |
  | 호스트↔앱 | 내부 속성(자원) → 외부 속성(응답·에러) 전파 공리 | PRISM(preprint, RCAEval Top-1 68% · 8ms) — **실험 트랙** |
  | 호스트↔앱 | 인프라는 정상인데 앱이 피해를 입는 **관측 불일치** | Gray Failure(HotOS'17) |

- **P-22 LLM 단독 결정 금지(수치 근거).**
  - OpenRCA 최고 11.34% · Hard 0%다.
  - ITBench에서 트레이스를 제거하면 9.52%로 떨어진다.
  - D-Bot 표의 **순수 GPT-4 단일 원인 정확도는 0.351**(D-Bot 0.754, HumanDBA 0.955)이다.
  - Flow-of-Action 실험의 HolmesGPT(GPT-3.5, 2025년 초 버전)는 11.11%였다. **현재 구성에 외삽하지 않는다.**
- **P-23 LLM에 재순위 권한을 주지 않는다.**
  - GALA(BARO AC@1 14.44% → 42.22%)와 KRCA(0.88 vs 0.57)는 "통계 순위 + LLM"의 이득을 보였지만, 둘 다 **LLM이 최종 순위를 결정**하고 **preprint**다.
  - "LLM은 순위를 바꾸지 않고 서술만"을 직접 검증한 연구는 없다(공백 6).
  → 기본값은 서술만 허용이다. LLM 재순위의 효과는 사내 A/B(D-127 승인)로만 검증하고, 채택은 별도 결정으로 한다.
- **P-24 브리핑 채점은 "원인 적중 + 증거 폐포율 + 반복 일관성"이다.** Cloud-OpsBench(preprint)에서 JRA 0.76인데 ECR은 0.38이었다(정답을 맞혀도 인용 증거가 원인을 닫지 못함). EoG(preprint)는 Majority@k로 반복 일관성을 잰다.

### 3.5 라이브러리 — 문헌 성능·라이선스로 고르고, 의존은 `sre_agent` extra로 격리한다

> **v2 전제 변경(사용자 지시 2026-09-17)**: *"버전과 라이브러리는 필요에 따라 수정하거나 사용할 수 있다."* v1은 폐쇄망 반입 부담을 선택 기준에 넣어 "추가 반입 최소"를 1차 조건으로 두었다. v2부터는 반입 부담을 선택 순위에서 뺀다. 선택 기준은 **문헌 성능 · 라이선스 · 유지보수 상태 · 해석 가능성**이다. 반입 행정(절차·소요)은 J-6에서 사실로만 확인한다.

| 범주 | 1차(M2~M3) | 2차·3차(extra · 필요 단계에서) | 회피(사유) |
|---|---|---|---|
| 기준·분해 | **statsmodels**(STL·ETS) · numpy · scipy | **statsforecast** 2.1.x(MSTL·AutoETS·AutoARIMA · `pandas<3` 고정 — §3.5.1) | — |
| 이상탐지 | **scikit-learn**(IsolationForest·PCA) · **pyod** 3.6.x(BSD-2) · **river** 0.26.x(HalfSpaceTrees · 온라인) · **ruptures** 1.1.x(PELT · py<3.14) · **stumpy** 1.14.x(matrix profile) | — | alibi-detect(**BSL 1.1**) · Merlion(**아카이브**) · Kats(py3.11+ 설치 불가) · ADTK(2020 방치) · Luminaire · Orion(py<3.12·TF) |
| RCA | **자체 구현**: 이벤트 그래프 + 강건 점수(BARO RobustScorer 재구현) + 변화점 정렬. 의존을 늘릴 이유가 없는 크기다 | **dowhy** 0.14 `gcm.attribute_anomalies`·**causal-learn**(MIT · py3.13에서 scipy≥1.15로 해석 OK) · CIRCA(BSD-3 · 방법 재구현 또는 코드 검토 후 사용) | PyRCA(2023 동결 · sklearn<1.2 · JDK) · RCAEval 런타임(164개 정확 핀 → **평가 참고만**) · tigramite(**GPL-3.0**) · cdt(R 필요) · lingam(cp313 wheel 없음) |
| 예측 | statsmodels ETS/STL · conformal 구간(자체 구현) | statsforecast · (M7b) **chronos-forecasting**(Chronos-2 small/Bolt · torch) · timesfm 2.5 · Toto-2(py≥3.12 · gluonts `pandas<3` — 해석 미검사) | sktime(numpy<2.5·pandas<3·sklearn<1.8 삼중 핀) · Greykite(정확 핀) · neuralforecast(딥 모델 운영 이점 없음 P-14) |
| 유사 사건(F4) | **scikit-learn** TF-IDF(문자 n-gram) + 시간 감쇠 | **sentence-transformers** + e5-small(D-114 반입 모델 재사용 · torch) | — |
| 로그/이벤트 템플릿 | **Drain 알고리즘 자체 구현**(고정 깊이 트리 · 결정적) | — | **drain3 0.9.11 — holmesgpt와 `cachetools` 충돌(§3.5.1 실측)** · drain3 0.9.1(2021 · 의존 메타데이터 없음) · loglizer·deep-loglizer(연구용) |
| 모델 영속화 | **skops** 0.15.x(pickle 없음) · SQLite 모델 메타(구간·파라미터·SHA-256) | onnxruntime + skl2onnx | **pickle·joblib·cloudpickle은 신뢰 경계를 넘는 전달 금지**(scikit-learn 공식: *"Loading can execute arbitrary code"*) · MLflow(무거움 · flavor가 cloudpickle 기반) |
| 게이트 신호 발행 | **redis**(8.x) | — | — |

- **P-25 torch는 쓸 수 있지만 쓰는 곳을 좁힌다.** 라이브러리 제약이 아니라 **문헌 근거** 때문이다. 탐지 용도로는 파운데이션 모델·딥 탐지기의 이점이 없고(P-1·P-4), 예측(M7b)·임베딩(F4 2차)에서만 이점이 있다. PyPI Linux torch wheel은 CUDA 의존을 선언하고 wheel 하나가 554MB다. CPU 서버에는 **PyTorch CPU 인덱스**(`download.pytorch.org/whl/cpu`) wheel을 쓴다. **[v5] torch는 G5(CUDA wheel)에만 두고 `run_service` 호스트에는 두지 않는다 — §R.10.2.**
- **P-26 루트 venv와 `sre_agent` venv의 차이를 이용한다.** 루트 venv는 pandas 3.0.5라 statsforecast 2.1.x·gluonts·sktime과 충돌한다. `sre_agent` venv(Python 3.13)는 **pandas가 아예 없고** 루트와 분리돼 있어 `pandas<3` 고정이 자유롭다. D-181(공유 venv 파손을 임포트 가드가 숨긴 사례)의 재발 위험도 루트 venv에 전파되지 않는다.

#### 3.5.1 `sre_agent` venv 의존성 해석 실측 (v2 · 2026-09-17)

`uv 0.10.2 pip compile`로 **메타데이터 해석만** 했다(설치 0건 · 설치본 venv 무변경). 대상 조건은 `--python-version 3.13 --python-platform x86_64-unknown-linux-gnu`이다. 후보 목록은 다음과 같다.
- 1차: numpy · scipy · pandas · scikit-learn · statsmodels · ruptures · stumpy · pyod · river · skops · drain3 · redis
- 2차: statsforecast · dowhy · causal-learn
- 3차: chronos-forecasting

| 조합 | 결과 | 해석된 핵심 버전 |
|---|---|---|
| holmesgpt **0.36.0** + 1차 | ✔ OK(212 핀) | numpy 2.5.3 · scipy 1.18.1 · pandas 3.0.5 · scikit-learn 1.9.1 · statsmodels 0.15.0 · pyod 3.6.5 · river 0.26.1 · ruptures 1.1.10 · stumpy 1.14.1 · skops 0.15.0 · redis 8.1.0 · numba 0.67.0 · mcp 1.25.0 |
| holmesgpt **0.42.0** + 1차 | ✔ OK(205) | 위와 같음 · mcp 1.28.1 · litellm 1.89.0 |
| 0.42.0 + 1차 + 2차 | ✔ OK(229) | ⚠ **statsforecast가 2.0.1로 하향** — 2.1.x가 선언한 `pandas<3`을 피하려고 해석기가 구버전을 골랐다. 해석은 되지만 pandas 3 런타임 호환은 보장되지 않는다 |
| 0.42.0 + 1·2·3차 | ✔ OK(255) | torch 2.14.0 · transformers 5.17.0 · chronos-forecasting 2.3.2 · dowhy 0.14 · causal-learn 0.1.4.8 |
| 위 + **`pandas<3` · `statsforecast>=2.1` 고정** | ✔ OK | **pandas 2.3.3 · statsforecast 2.1.1** · dowhy 0.14 · chronos 2.3.2 · torch 2.14.0 · cachetools 5.5.2 |
| 같은 전체 조합 · macOS arm64(개발 장비) | ✔ OK | — |
| 위 + `drain3>=0.9.11` | ✖ **FAIL** | *"holmesgpt==0.42.0 depends on cachetools>=5.5.0,<6.0.0 and drain3==0.9.11 depends on cachetools==4.2.1"* → 핀 없이 두면 drain3 **0.9.1(2021-02 · 의존 메타데이터 없음)**로 조용히 내려간다 |

- 확정 사항 네 가지:
  - `sre_agent` venv에 ML 스택 전체를 **holmesgpt 0.36·0.42 어느 쪽과도** 함께 둘 수 있다.
  - `pandas<3`을 고정한다.
  - drain3는 쓰지 않고 Drain을 자체 구현한다.
  - holmesgpt·ML 버전을 바꿀 때마다 이 해석 검사를 다시 돌린다(`sre_agent/scripts/ml/resolve_check.sh` · CU-1).
- **한계**: 메타데이터 해석이다. 설치·임포트·런타임 검증은 M1 CU-1에서 실제 venv로 한다. holmesgpt 0.36.0 → 0.42.0 사이 SDK 변경(§2.1)도 그때 `inspect`로 실측한다.

### 3.6 벤더 네이티브 AI — 재구현하지 않고 입력으로 받는다

> 벤더 공개 서술이며 독립 검증이 아니다.

| 벤더 | 공개 AI 기능 | 우리 판단 |
|---|---|---|
| **제니퍼 5** | Anomaly Event(응답시간·액티브 서비스·동시 사용자·CPU·메모리 5종 자동 기준선) · Metrics 상관(Pearson + 시차) · X-View 패턴 인식(브라우저 few-shot DL) · 인사이트 챗(Open API를 tool로 호출) · 공식 MCP(LLM 프록시 겸 · 5.6.5+ · JDK 17) | **WAS 단위 이상은 제니퍼 Anomaly Event를 입력 신호로 받는다**(G-6). 단 Open API 5.6.4 스펙에 `anomaly` 문자열이 0건이라 API 노출은 **미확인**이다(J-7). 공식 MCP는 `plans/87` G-8에서 이미 미채택으로 확정됐다 |
| **엑셈 XAIOps / MaxGauge** | 30~60분 선행 예측 · RCA · LLM 챗봇(QURI). **알고리즘명·외부 API·MCP 문서 비공개** | 운영 기관이 XAIOps를 도입했다면 예측 결과를 입력으로 받는다. 연동 가능성은 J-8로 실측한다 |
| **와탭**(참고) | 공식 MCP(MIT)의 `whatap_apm_anomaly`가 서버 안에서 통계 분석 후 **요약만 반환** | **우리 `ml_*` 도구 응답 설계의 참고 사례**(원시 시계열 반환 금지) |
| Grafana/Datadog/Elastic(참고) | MAD·DBSCAN 그룹 이상치 · rolling quantile·robust SARIMA·STL · 베이지안 분포 모델링 | 공개 서술된 주력 기법이 강건 통계 · 계절 분해라는 점이 P-1과 일치한다 |

**우리가 구현하는 차별 영역**은 어느 벤더도 주지 않는 **교차 소스(인프라 ↔ WAS ↔ DB) 상관·RCA**, 우리 스트림(`alarm:raw`) 위의 **게이트 정량 신호**, **폐쇄망 내 용량 ETA**다.

### 3.7 조사에서 확인한 공백 — 보수적 설계의 근거

| ID | 공백 | 설계 귀결 |
|---|---|---|
| GAP-1 | **시간·일 집계 해상도에서 평가한 RCA 문헌이 없다.** 공개 벤치마크는 1초(RCAEval) · 1분(OpenRCA·CIRCA Oracle) · 5분(PetShop)이다 | 문헌 수치를 기대치로 옮기지 않는다. 해상도별 민감도(원시 vs 1시간)를 평가 항목에 넣는다. 저해상도 공개 검증은 PetShop(5분)으로 한다 |
| GAP-2 | **시간 집계 메트릭만으로 서버 장애를 선제 예측한 동료심사 실증이 없다** | 폴스타 `_h` 단독의 "사건 예측"은 약속하지 않는다. 시간 집계는 추세·ETA·패턴 이탈에 쓴다 |
| GAP-3 | 트레이스 없는 **WAS↔DB↔호스트 교차 계층 RCA의 실환경 검증이 없다**(계층 내부 검증만 있음) | 교차 계층은 규칙·매핑 기반으로 시작하고 계층별 성적을 분리한다 |
| GAP-4 | JVM GC·스레드풀 병목 RCA, 대기 이벤트 기반 ML RCA, **제니퍼·맥스게이지 데이터 기반 학술 문헌 없음** | 벤더 문서 + ADDM식 결정적 분해가 출발점이다 |
| GAP-5 | 리드타임 기반 이벤트 단위 예측 평가 **표준 없음** | §6에 사내 규약을 정의한다(Salfner 정의 기반) |
| GAP-6 | "LLM은 서술만"을 **직접 검증한 연구 없음**. 하이브리드 RCA 이득은 preprint뿐 | LLM 재순위는 기본 금지, 사내 A/B로만 검증 |
| GAP-7 | 폐쇄망·CPU·소형 모델 조건의 RCA 에이전트 정량 결과 없음(자체 호스팅 사례는 RCAgent Vicuna-13B 하나) | 운영 모델로 사내 측정 전에는 성능을 주장하지 않는다(D-174) |
| GAP-8 | 운영자 희소 피드백으로 탐지기·임계를 고르는 2024~2026 후속 연구가 드묾(원형: Opprentice 2015 · iSQUAD 2020) | 라벨 설계는 "군집당 1회 라벨"로 최소화한다 |
| GAP-9 | 금융권 달력 효과를 다룬 이상탐지·예측 벤치마크 없음 | 달력 플래그를 결정적 공변량으로 두고 월말 2회 섀도로 검증 |
| GAP-10 | 모델 가중치 **라이선스 변동**을 다룬 문헌 없음(산업 자료로만 확인) | 반입 계약에 라이선스 문자열·해시 고정을 넣는다 |
| **GAP-11**(v3) | **경기(Kaggle)에도 "시간 집계 실데이터" 대조군이 없다.** 실데이터는 분 단위(SMD)·일 단위(Backblaze)이고, 시간 집계 사례(Azure PM)는 샘플·시뮬레이션이다(KD-01·KD-02·KD-05) | GAP-1·GAP-2가 경기 증거로도 메워지지 않음을 확정한다. Kaggle 데이터는 **구현 회귀·누수 점검 전용**이다(§6.2-7 · §3.8 K-8) |
| **GAP-12**(v3) | **경기 데이터·Kaggle 데이터셋의 라이선스·사용 조건을 정적으로 확인할 수 없었다**(페이지가 JS 렌더링). 경기 데이터는 규칙 동의가 전제이고 비상업·경기 목적 한정이 흔하다. NAB는 MIT 주장과 AGPL-3.0 이력 주장이 엇갈린다(KD-03) | 라이선스 **원문 확인 전 반입·사용 금지**(§9 · J-11). AGPL·비상업이면 §9 배제 목록으로 간다 |

### 3.8 경기(Kaggle) 조사 결과 — 구현 공학으로 옮긴 것 (v3 · 2026-09-20)

> 동료심사 문헌(§3.1~3.6)은 **무엇이 옳은 방법인가**를 준다. 경기 증거는 그것으로 답이 되지 않는 것을 준다 — **같은 방법으로 누가 실제로 점수를 냈고, 그 점수의 얼마가 누수였는가.** 정본은 `docs/aiops_benchmark/ml_kaggle_competition_survey.md`이고 아래 `K-*`·`KC-*`·`KL-*`·`KG-*`는 그 문서의 항목 ID다.
> **경기 수치도 P-12(공개 벤치마크 수치 인용 금지)의 적용 대상이다.** 아래 수치는 "그 방법이 그 데이터에서 통했다"는 존재 증명으로만 쓰고 사내 기대치로 옮기지 않는다. 경기 증거의 실제 산출은 알고리즘 목록이 아니라 **§3.8.1 누수 금지 목록과 §6.2 평가 규약**이다.

- **K-1 표로 접은 시계열·이벤트에서는 GBDT가 정본이다.**
  - M5 Accuracy는 상위가 전부 순수 ML(대부분 LightGBM)이고 **모든 통계 기준선과 그 조합보다 유의미하게 나았던 첫 M-competition**이다. ASHRAE GEPIII 상위는 LightGBM 앙상블이고, Telstra 우승은 GBT·NN·RF 3층 스태킹(logloss 0.395)이며, Bosch는 XGBoost였다. 동료심사 쪽 뒷받침도 있다 — Grinsztajn et al.(NeurIPS'22 D&B)은 **≈10K 표본 규모 표 데이터에서 딥 아키텍처를 충분히 튜닝한 뒤에도 트리 모델이 SOTA로 남는다**고 보고한다.
  → **F2c(사건 위험 점수)의 모델을 GBDT로 확정한다**(§5.3 (c)). 신규 의존을 늘리지 않는 경로가 있다 — scikit-learn `HistGradientBoostingClassifier`가 LightGBM 계열 히스토그램 부스팅이고 `ml` extra에 이미 들어 있다. lightgbm·xgboost·catboost 추가는 리플레이셋에서 sklearn 구현 대비 우위가 신뢰구간 밖일 때만 하고, 추가하면 해석 검사(C-4)를 다시 돌린다.
  → **탐지(F1)에는 옮기지 않는다.** 탐지는 라벨이 없고 P-1이 강건 통계를 지시한다. 경기 증거는 **라벨이 있는 표 문제에만** 적용한다.
- **K-2 점수를 만드는 것은 모델이 아니라 피처와 전처리다.**
  - Telstra 상위권 자평이 *"feature engineering, rather than ensembling or XGBoost tuning"*이고, ASHRAE 설문 응답자들은 **전처리·피처추출이 가장 중요한 단계**였다고 답했다. C-MAPSS 계열에서는 rolling 피처만으로 test RMSE 21.89 → 20.19였다.
  → §5.3 (c)의 피처를 경기 검증된 네 계열로 명세한다. ① **다중 창 롤링 통계**(median·MAD 포함 — P-2와 같은 척도) ② **로그·알람 템플릿별 건수와 volume 집계**(min·mean·max·std·sum — Telstra KC-01) ③ **엔티티 범주형 인코딩**(호스트·존·제품군 · target 인코딩은 시간순 OOF 안에서만 — KL-4) ④ **마지막 변경·점검·재기동 이후 경과시간**(Azure PM의 부품 age에 대응 · 우리에게는 `lifecycle_history`·점검 창이 있다). 이 넷 밖의 피처를 넣으려면 근거를 적는다.
- **K-3 경기 상위 해법의 "마법 피처"는 대부분 누수다.** → §3.8.1에서 금지 목록으로 다룬다.
- **K-4 분포 이동은 추측하지 않고 측정한다(adversarial validation).** train/test를 이진 분류해 AUC가 0.7을 넘으면 두 구간은 같은 분포가 아니다.
  → ① M0 데이터 카드에 **구간 간·존 간 adversarial AUC**를 넣는다 ② 재적합 시 드리프트 지표(§9)로 상시 계산한다 ③ AUC ≥ 0.7인 분할로 낸 성적은 채택 근거로 쓰지 않는다. 이 지표는 **P-11(테스트 구간에 월말 ≥2회)의 정량 검사**이기도 하다 — 월말 구간과 평시 구간의 adversarial AUC가 높다는 사실이 곧 월말을 테스트에 넣어야 하는 근거다.
- **K-5 극단 불균형에서는 지표와 임계가 모델보다 중요하다.** Backblaze 실측 불균형 비는 11,501:1(문헌 범위 5,702~19,038:1)이고, VSB에서는 같은 MCC 지표에서 1위 임계 0.350 vs 다른 모델 0.434로 **임계 자체가 점수의 일부**였다.
  → ① F2c 부지표에 **MCC**를 넣는다(§6.1) ② **임계는 학습 구간에서 고정하고 테스트·섀도에서 재조정하지 않는다**(규약 13) ③ 불균형 대응은 재표본이 아니라 `class_weight`/`scale_pos_weight` + 비용 민감 임계다(KL-6).
- **K-6 "계열마다 이상 1개, top-1 채점" 과제에서는 matrix profile류가 상위권이다.** (Kaggle 외) KDD Cup 2021 TSAD에서 공개된 5위 해법은 계열별 subsequence 길이를 바꾼 matrix profile만으로 217/250 = 86.8%였다.
  → `stumpy` matrix profile을 **단계 1의 보조 탐지기 후보로 승격**한다(§5.2). 라벨이 필요 없고, 시간 집계에서 "일·주 반복 패턴의 이탈"을 잡는 성격이 STL 잔차와 상보적이다. 채택은 리플레이셋 판정이다.
- **K-7 예측에서 다중 창 median 기준선은 경기에서도 강했다.** Web Traffic 2위 해법의 핵심 아이디어 중 하나가 "원값 대신 median을 피처로"였고, 표준 공개 기준선이 다중 창 median의 median이었다.
  → **의무 기준선에 추가한다**(§3.2 P-9 표). 구현 몇 줄·의존 0이며, 이것을 못 이기는 예측기는 들이지 않는다.
- **K-8 Kaggle의 "IT 운영 모니터링" 데이터는 대부분 합성이거나 연구 데이터 재업로드다.** 실데이터 대조군은 Backblaze(일 단위)·SMD(분 단위·운영자 인시던트 보고 기반 라벨)·Loghub뿐이고, 네이티브 업로드 계열은 출처 서술이 없거나 합성 표기다.
  → §6.2 규약 7을 확장한다: **Kaggle 데이터는 구현 회귀·누수 점검 연습 전용**이며 쓸 수 있는 것과 못 쓰는 것은 KD-01~KD-08이 구분한다. → GAP-11.
  → 단 **KC-02(Azure Predictive Maintenance)는 "시간 집계 지표 + 오류 로그 → N시간 내 고장"이라는 우리와 같은 정식화의 공개 선례**다(100대 × 1년 × 시간 평균 · 876,099행 · 오류 유형별 건수 · 부품 교체 후 경과). 데이터는 샘플·시뮬레이션이라 성능 근거가 아니지만 **문제 정식화와 피처 명세는 M7c의 출발점으로 차용한다.** GAP-2는 그대로 남는다 — **선례가 있다는 것과 실증이 있다는 것은 다르다.**
- **K-9 스태킹·다중 시드 앙상블의 이득은 소수점 셋째 자리다**(XGBoost 100 시드 ≈ MAP@3 0.379 vs 단일 시드 평균 0.376).
  → §12 「하지 않는 것」에 명시한다. 운영에서는 해석성·지연·모델 관리 비용이 그 이득보다 크다.
- **K-10 CV–LB 관계를 믿고 단일 최고점을 믿지 않는다.**
  → 규약 12: **리플레이셋 점수(=CV)와 섀도 성적(=LB)을 쌍으로 기록하고 그 관계를 채택 판단에 쓴다.** 리플레이 최고점 하나로 채택하지 않는다. 경기의 "shakeup"이 우리에게는 "섀도에서 무너짐"이고, 그 사고를 미리 막는 장치가 이 규약이다.

#### 3.8.1 누수 금지 목록 (KL-1~KL-6) — 리플레이 평가셋 설계의 1급 제약

| ID | 누수 형태 | 경기 사례 | 우리 데이터에서 같은 것 | 차단 |
|---|---|---|---|---|
| **KL-1** | 레코드 순서·행 번호 | Telstra: 위치 그룹 내 행 번호가 시간축을 복원해 점수 급등(작성자 본인이 누수 의심) | `cmm_alarm` 적재 순서 · 조회 결과 행 순서 · 평가셋 파일 기록 순서 | 행 index·정렬 위치 계열 피처 전면 배제. 빌더는 **시각 기준으로만** 정렬하고 index를 산출물에 남기지 않는다 |
| **KL-2** | 단조 증가 ID의 차이 | Bosch: `mindate_id_diff`·`..._reverse`가 public 점수를 만들었고 **실배포엔 제거 필요**로 명시됐다 | `alarm_id`·시퀀스 차이 · `investigation_id` 순번 · 사건 간 ID 간격 | ID는 조인 키로만 쓰고 수치 피처로 만들지 않는다 |
| **KL-3** | **사후 기입 필드** | ASHRAE: 공개된 test 실측치로 앙상블 가중 결정 | **`cmm_alarm_note.alarmcause`·`cmm_alarm_knowledge`·ack 시각·`resolution{duration}`·`recurrence`·피드백 `note`** | **라벨로만** 쓴다. 피처 테이블 진입을 허용목록으로 구조적으로 막고, 평가 보고서에 **피처/라벨 분류표**를 싣는다 |
| **KL-4** | 그룹(사건) 분할 실패 | Kaggle 실무 상시 지적 | 한 장애의 알람 수십 건 · 한 호스트의 연속 구간 · 한 변경의 대조군 쌍 | **사건 단위 group 분할 + 시간순(purged) 분할을 동시에** 적용하고 경계 구간은 버린다 |
| **KL-5** | 미래 창 집계 | C-MAPSS 노트북들의 반복 경고 | rolling median·MAD · STL 재적합 · 로그 템플릿 사전 학습 | 모든 집계는 **좌측(과거) 창만**. **로그 템플릿 사전(Drain)은 학습 구간에서 고정한 아티팩트**로 테스트 구간에 적용하고 재적합하지 않으며, 미지 템플릿은 OOV 버킷으로 보낸다 |
| **KL-6** | 재표본을 분할 전에 수행 | Kaggle 실무 상시 지적 | 불균형 대응 재표본(SMOTE 등)을 검증 분할 전에 적용 | 재표본 대신 `class_weight`/`scale_pos_weight` + 임계 조정. 필요하면 분할 **이후 train 폴드 안에서만** |

- 이 목록은 경고가 아니라 **구현 요구**다. §5.1 평가셋 빌더가 KL-1·KL-2를 산출물 스키마에서 배제하고, KL-3을 허용목록으로 막고, KL-4를 분할기로 강제하고, KL-5를 집계 함수 계약으로 보장하고, KL-6을 학습 파이프라인 순서로 보장한다. 검사는 `scripts/ml/leakage_audit.py`(M1 CU-4)가 돌린다.
- **왜 1급인가**: 누수는 성능을 과대평가하는 데서 끝나지 않는다. 채택 게이트(§6.3)가 전부 "기준선 대비 우위"로 쓰여 있으므로 누수가 섞이면 **게이트가 통과 도장을 찍는 기계로 변한다.** D-174(개발 측정치 기준선 인용 금지)·D-176 ⑤(표본 <20 문구 금지)와 같은 계열의 통제다.

#### 3.8.2 경기가 주지 않는 것

| ID | 공백 | 귀결 |
|---|---|---|
| KG-1 | 시간 집계 **실데이터**로 서버 장애를 선제 예측한 경기가 없다 | GAP-2·GAP-11 유지. 시간 집계는 추세·ETA·패턴 이탈에 쓴다(P-5) |
| KG-2 | 메트릭 + 로그 + 변경을 **함께** 준 경기가 없다 | 교차 소스 결합은 여전히 우리 고유 영역이다(§3.6 말미와 같은 결론) |
| KG-3 | RCA(원인 엔티티 순위)를 채점한 경기가 없다 — 경기 지표는 예측·분류·회귀뿐이다 | RCA 근거는 `ml_rca_literature.md`가 정본으로 남는다. §5.4 설계는 v3에서 바뀌지 않는다 |
| KG-4 | 금융권 달력 효과를 다룬 경기 데이터가 없다 | GAP-9 유지 |
| KG-5 | Kaggle 페이지 원문(규칙·라이선스·리더보드·토론)을 정적으로 확인하지 못했다 | J-11 |

---

## 4. 목표 아키텍처

### 4.1 역할 경계와 불변식

| 층 | 담당 | 할 수 있는 것 | 할 수 없는 것 |
|---|---|---|---|
| **결정적 규칙** | `noise_gate` 티어 판정 · `sre_agent` 상관·가설 규칙 · 억제·심각도3 PAGE | 최종 판정 | — |
| **ML 계층(`sre_agent` 내부)** | 점수 · 순위 · 예측 구간 · ETA · 유사 사례 | 증거 생산 · 주석 · (평가 통과 후) escalate-only 후보 | 억제 결정 · 티어 하향 · 조치 실행 · 원시 시계열 LLM 투입 |
| **LLM(HolmesGPT)** | 조사 서술 · 반증 질의 | ML 수치 **인용** 서술 · 추가 증거 요청(ML 도구 호출) | 순위 재결정 · 점수 재계산 · 확률 주장 |

**불변식**(테스트로 고정한다)

- **I-1 경계 유지**: `sre_agent` ↔ `src`·`noise_gate`·`mcp_server` 양방향 import 0(D-118 · `sre_agent/tests/test_boundary.py`). **ML 모듈도 이 테스트의 스캔 대상에 포함**한다. 외부와의 계약은 MCP(기존 `sre_*` 도구 + 신규 `sre_ml_*`) · Redis 키(`ml:*`)뿐이다.
- **I-2 읽기 경로 단일화**: 관측 데이터는 `mcp_server` 도구로만 읽는다(D-119). 기존 `infrastructure/mcp_tool_client.py`를 재사용하며, `sre_agent`는 DB 자격증명을 갖지 않는다.
- **I-3 비트 동일**: 모든 ML 플래그 기본 off. off이거나 **`ml` extra가 설치되지 않으면** 조사 결과·게이트 결정이 현행과 비트 동일하다(`plans/80` §5.4-③).
- **I-4 escalate-only**: ML 신호는 티어를 올리는 후보일 뿐 내리지 못한다. SUPPRESS 판정에 ML 값을 쓰지 않는다(D-048·`plans/60` §15.4).
- **I-5 확률 표기 금지**: 모든 점수는 `score_semantics: "relative_rank"`이고, 구간만 `coverage_target`을 갖는다.
- **I-6 모델 파일 무실행 로드**: pickle 계열 로드 금지(skops·ONNX·safetensors만). 로컬 디렉토리에서만 로드하고 런타임 다운로드는 금지한다(D-114 전례 · `HF_HUB_OFFLINE=1`).
- **I-7 원시 반출 금지**: 학습·평가 산출물은 서버에서 축소한 뒤 반출한다(D-219 준용). 운영 데이터는 외부 LLM에 보내지 않는다(D-120).
- **I-8 기준선 병행**: RCA·탐지 응답은 채택 모델 결과와 **의무 기준선 결과를 함께** 싣는다(P-9·P-18). 기준선과 결론이 갈리면 그 사실을 응답에 표기한다.
- **I-9 조사 서비스 비침해(v2 신설)**
  - 배치 스코어링·재적합·리플레이 평가는 **조사 서비스 프로세스(`run_service`)에서 돌지 않는다.**
  - 온디맨드 분석은 기존 사전수집 타임박스 안에서만 돈다.
  - ML 예외는 조사 잡을 실패시키지 않는다. `ml_evidence.error`에 사유만 남긴다(침묵 폴백 금지 · 부분 반환).
- **I-11 판정 독립(v6 신설 · §R.10.1)**: S5 원인 분류의 필수 조건은 `run_service` 출력만 읽는다. G5 `ml_service` 장애 분석(GA-1~GA-6) 결과는 보조 조건·보조 후보(`origin: ml_service`)로만 들어간다. 결정적 사슬과 어긋나면 결론을 바꾸지 않고 「ML 분석 불일치」로 표기한다. G5 장애·시간 초과는 GA별 `limitations` 표기로 처리하며 조사를 실패시키지 않는다.
- **I-10 `domain/ml`은 표준 라이브러리만(v2 신설)**: 기존 `domain/correlation.py:7` 규율을 따른다. numpy·statsmodels·sklearn 등은 `infrastructure/ml/` 어댑터에서 lazy import하고 순수 Python 폴백을 둔다. arch_check는 외부 패키지를 잡지 않으므로 **import 허용목록 스캔 테스트**로 고정한다.

### 4.2 배치 — `sre_agent` 안에 구성한다 (v2 재검토 결과)

```
          ┌─────────────────────── 관측 데이터 읽기 경계 (D-119) ───────────────────────┐
          │ mcp_server : polestar_* · prom_* · apm_*(plans/87) · dpm_*(신규 계획 · G-3)   │
          └──────────────────────────────▲──────────────────────────────────────────────┘
                                         │ 기존 infrastructure/mcp_tool_client (재사용)
┌────────────────────────────────────────┴─────────────────────────────────────────────────┐
│ sre_agent/  (기존 독립 패키지 · Python 3.13 venv · D-118 — 범위를 "장애 조사·진단·예측"으로)   │
│                                                                                          │
│  ▣ 프로세스 ① run_service (기존 조사 서비스 · :9098)                                       │
│     application/evidence_prefetch ──► application/ml_analysis   (앵커 사건 · 온디맨드)       │
│     domain/correlation (기존 z-score)   domain/ml/* (강건 점수·이벤트 그래프·ETA · 순수)       │
│     application/briefing_builder ◄── ml_evidence · 가설 병합(섀도→병합)                     │
│     diagnosis.py : Config(additional_toolsets=[ML 도구셋])  → HolmesGPT in-process 도구     │
│     interface/mcp_service : sre_ml_* 도구 (본체 챗·대시보드 pull)                           │
│                                                                                          │
│  ▣ 프로세스 ② ml_batch (신규 엔트리 · 같은 패키지·같은 venv · 별도 기동 단위)                │
│     application/ml_batch_scorer : 시간 주기 스코어링 · ETA 갱신 → Redis ml:* 발행            │
│     infrastructure/ml/model_store : skops 파일 + SQLite 메타 (sre_agent/.data/ml/)          │
│                                                                                          │
│  ▣ 수동 실행 scripts/ml/ : data_card · build_replay_set · backtest · resolve_check          │
│  infrastructure/ml/ : statsmodels · ruptures · sklearn · pyod · river 어댑터 (lazy · extra)  │
│  config/ml/ : metric_map · rca_rules · peer_groups (정본 설정 · 폴스타 리터럴은 여기만)       │
└───────────────┬──────────────────────────────────────────┬───────────────────────────────┘
                │ Redis ml:* (TTL)                           │ MCP sre_diagnose(기존) · sre_ml_*
                ▼                                            ▼
   noise_gate (off → shadow → annotate → escalate)    src fault_diagnosis · 관제 대시보드
```

**재검토 비교**(사용자 지시 *"패키지는 별도가 아니라 sre_agent에 구성하는 것을 검토하라"*)

| 기준 | **(가) `sre_agent` 편입(v2 권장)** | (나) 별도 최상위 `fault_ml/`(v1안) | (다) `mcp_server` 내부 | (라) `noise_gate` in-process |
|---|---|---|---|---|
| 소유·경계 | ✔ RCA 축(D-197 결정적 상관·가설)이 이미 `sre_agent`에 있다. 같은 기능을 한 패키지가 소유한다(D-139 "기능 단위 코드는 자기 패키지 안") | △ 상관은 `sre_agent`, ML RCA는 `fault_ml`로 한 기능이 둘로 갈린다 | ✖ "읽기 경계"(D-119)에 계산·모델이 섞인다 | ✖ 조사 쪽이 결국 MCP 호출이 필요해 계약이 갈린다 |
| 의존 격리 | ✔ 루트와 분리된 venv. ML 스택 전체 해석 OK(§3.5.1) | ✔ 새 venv | ✖ 루트 venv(pandas 3) 공유 → 충돌·D-181 재발 | ✖ 루트 venv 공유 |
| 데이터 접근 | ✔ `mcp_tool_client`·설정·인증 **재사용** | △ 동형 클라이언트를 새로 작성(import 금지) | ✔ 직접 | ✔ 기존 경로 |
| HolmesGPT 결합 | ✔ 사전수집 **직접 호출** + in-process 도구셋(0.36 설치본에 `additional_toolsets` 실재 — §4.4) · MCP hop 0 | △ MCP 서버 1개 추가 · 0.36은 MCP 결과 text만 전달 | △ MCP 경유 | ✖ 없음 |
| 조사 서비스 영향 | △ 같은 패키지 → **I-9로 배치를 별도 프로세스에 둬서 완화** | ✔ 완전 분리 | △ 데이터 경계 장애 전파 | ✖ API 프로세스 CPU 경쟁 |
| 운영 단위 | 기동 단위 +1(`ml_batch`). venv·패키지는 증가 0 | 패키지·venv·프로세스 각 +1 | 0 | 0 |
| 분리 가능성 | ✔ "폴더 복사 + URL 설정"(sre_agent README) 그대로. ML이 함께 이동 | ✔ | ✖ | ✖ |
| 게이트 overfit 스캔 | ✔ `sre_agent/domain`은 이미 스캔 대상(`plans/91` 1-8) → `domain/ml`도 자동 적용 | 신규 편입 필요 | 해당 | 해당 |

**결론**: **(가)를 권장한다.** v1이 별도 패키지를 권한 근거는 두 가지였다. ① 루트 venv 의존 충돌 ② `sre_agent`를 가볍게 유지하자는 D-118 취지다. ①은 `sre_agent` venv가 이미 루트와 분리돼 있어 해소된다(§3.5.1 실측). ②는 extra 분리와 배치 프로세스 분리(I-9)로 대체할 수 있다. 남는 비용은 아래 조건으로 관리한다.

**편입 조건**(C-번호 · D-223 결정 초안에 포함)

| C | 조건 | 이유 |
|---|---|---|
| **C-1** | ML 의존은 **optional extra**로만 넣는다: `ml`(1차) · `ml-forecast`(statsforecast) · `ml-causal`(dowhy·causal-learn) · `ml-tsfm`(chronos 등 · torch CPU) · `ml-embed`(sentence-transformers). base 설치와 extra 미설치 환경의 조사 경로는 비트 동일(I-3) | 조사 서비스 venv 비대화 방지 · 단계별 반입 |
| **C-2** | 배치·재적합·평가는 `sre_agent.ml_batch` 엔트리·`scripts/ml/`에서만 돈다. `run_service`는 온디맨드 분석만 하며, 사전수집 타임박스 · 동시성 상한 · 메모리 상한 안에서 돈다(I-9) | 조사 워커(BoundedSemaphore 2)와 자원 경쟁 방지 · 장애 격리. 선례: `noise_gate/alarm_server`(같은 패키지 안의 독립 프로세스 · D-139) |
| **C-3** | `domain/ml`은 표준 라이브러리만 쓴다(I-10). 외부 라이브러리는 `infrastructure/ml` lazy import + 순수 폴백 | 기존 계층 규율 · extra 미설치 시에도 도메인 테스트 가능 |
| **C-4** | holmesgpt·ML 버전 변경 시 **해석 검사**(`scripts/ml/resolve_check.sh` · Linux x86_64 · py3.13)를 필수 실행한다. 핀 충돌은 해석기가 **조용히 구버전을 고르는 방식으로 숨는다**(drain3 0.9.1 · statsforecast 2.0.1 실측) | holmesgpt 정확 핀(litellm·mcp·cachetools 등)과의 충돌 조기 발견 |
| **C-5** | extra 미설치를 테스트 skip으로 숨기지 않는다. dev 설치는 `.[dev,ml]`로 고정하고, **`ml` extra 설치를 단언하는 테스트 1건**을 둔다(D-181 교훈) | 임포트 가드가 파손을 숨긴 선례 |
| **C-6** | D-118의 `sre_agent` 범위를 "HolmesGPT 조사"에서 **"장애 조사·진단·예측(HolmesGPT + 결정적 분석·ML)"**으로 넓힌다. `sre_agent/README.md`·`CLAUDE.md` 패키지 경계 표·`docs/26_sre_agent_guide.md`를 갱신한다. 경계 불변식(I-1)은 그대로다 | 정체성 변경은 결정 사항이다(D-223) |

### 4.3 공통 데이터 모델(벤더 중립)

- **엔티티 키**: `entity = {layer: host|was|db|service, id, zone, db_id}`
  - host: 폴스타 `server_name`(Prometheus `nodename` 규약 D-119와 동일)
  - was: 제니퍼 `instance_id` ↔ host. 정합은 `plans/87` §5.3 `apm_instance_map` 결과를 **그대로 소비**한다(재구현 금지). 폴스타 `was_object`는 U-10 확인 전에는 단정하지 않는다
    - **(부기 2026-09-29 · `plans/87` v3.1 · D-274·D-195)** `apm_*`의 제공 주체는 `mcp_server`가 아니라 독립 패키지 **`apm_gateway`(자체 MCP 서버 · 두 번째 MCP 엔드포인트)**다. 소비 계약(`apm_instance_map`·`apm_slow_transactions`·WAS 판정 결과 `was_signals`)은 그대로이고 연결 대상만 다르다 — 이 문서의 `mcp_server : … · apm_*` 표기(§4 그림 등)도 이렇게 읽는다
  - db: DPM 대상 인스턴스 ↔ host. **매핑 원천은 G-3 DPM 계획 소관**이다
  - 매핑 신뢰도가 `high`가 아니면 **교차 계층 결합을 하지 않는다**(`plans/55` C-1 · `plans/87` `match_confidence`)
- **시계열 레코드**: `(source, entity, metric, resolution, ts, value, agg∈{avg,max,min,top,bottom,p95})`. 메트릭 이름은 `plans/87`이 쓰는 OpenTelemetry 시맨틱 이름(`http.server.request.duration`·`jvm.memory.used` 등)과 폴스타 kind(`cpu`·`memory`·`filesystem`·`disk_io`)를 **소스별 매핑 파일**(`sre_agent/config/ml/metric_map.yaml`)로 정규화한다. 폴스타 리터럴은 `domain/`에 두지 않는다(`sre_agent/domain`은 overfit 스캔 대상 · D-132 ④).
- **이벤트 레코드**: `(source, entity, ts, kind∈{alarm, anomaly, change, vendor_anomaly, changepoint}, severity, name, attrs)`. 폴스타 알람·제니퍼 이벤트·DPM 이벤트·변경·ML 이상을 한 스키마로 둔다(Groot 원리 P-19 · `plans/87` §5.5 "하나의 이벤트 스키마"와 정합).

### 4.4 도구 계약 — 한 구현, 세 표면

ML 분석 함수는 `application/ml_analysis.py` **한 곳**에 구현한다. 아래 세 표면은 모두 얇은 래퍼다.

| 표면 | 소비자 | 방식 | 근거·제약 |
|---|---|---|---|
| **① 결정적 사전수집** | 조사 파이프라인(푸시·풀 공통) | `evidence_prefetch`가 LLM 호출 없이 **함수 직접 호출** → `ml_evidence` | "결정은 코드"(D-035 · EoG P-24). 기본 경로 |
| **② HolmesGPT in-process 도구셋** | 조사 LLM(추가 증거 요청) | `Config(additional_toolsets=[Toolset(name="sre_ml", enabled=True, tools=[...])])` | **0.36.0 설치본 실측**: `holmes/config.py:152` `additional_toolsets: Optional[List[Toolset]]` → `core/toolset_manager.py:195-198`이 `type=CUSTOMIZED`로 등록. `Toolset.tags` 기본값 `[CORE]`라 현행 필터 `[CORE, CLI]`(`diagnosis.py:154-156`)를 통과한다. **`Toolset.enabled` 기본값이 `False`라 명시적으로 켜야 한다.** `Tool._invoke(params, context) -> StructuredToolResult`. MCP가 아니므로 0.36의 "MCP 결과 text만 전달" 제약과 무관하다 |
| **③ `sre_agent` MCP 도구 `sre_ml_*`** | 본체 챗(`fault_diagnosis`)·관제 대시보드 | 기존 `interface/mcp_service.py`(:9098 · Bearer)에 등록 | 기존 `sre_diagnose` 계약과 같은 인증·감사. text JSON + `structuredContent` 병행 |

| 도구(②는 `ml_*` · ③은 `sre_ml_*`) | 인자(값만 · D-122) | 반환 요지 | 단계 |
|---|---|---|---|
| `status` | 없음 | 모델·기준선 버전 · extra 설치 여부 · 데이터 신선도 · 비활성 사유 | M1 |
| `metric_anomalies` | `server_name`, `reference_time`, `lookback_minutes`, `kinds?` | 지표별 {판정, 강건 점수, 기준(계절·MAD), 이탈 구간, 변화점 시각} + 기준선(max-\|Z\|) | M2 |
| `exhaustion_forecast` | `server_name`, `kind`, `threshold_pct?`, `horizon_hours?` | ETA 분위수(p10/p50/p90) · 추세 · 구간 커버리지 목표 · 적합 구간 · **추세 신뢰 불가 사유** | M2 |
| `root_cause_candidates` | `server_name`, `reference_time`, `lookback_minutes`, `scope?(host\|topology\|cross_layer)` | 후보 상위 k {entity, metric/event, 점수, 근거} + **기준선 순위**(Dummy·max-\|Z\|·알람 건수) + 일치 여부 | M3 |
| `similar_incidents` | `server_name`, `reference_time`, `text?` | 과거 사례 상위 k {시각, 대상, 원인 요약, 조치, 유사도, 시간 감쇠} | M3 |
| `layer_divergence` | `server_name`, `reference_time`, `lookback_minutes` | 호스트 정상/앱 이상(또는 반대) 불일치 점수 · 계층별 판정 | M5 |
| `dimension_localize` | `server_name`, `reference_time`, `metric` | 다차원 조합(인스턴스×서비스×업무) 원인 후보 · 외부 원인 판정 | M5 |
| `incident_risk` | `server_name`, `reference_time` | (라벨 조건 충족 시) 위험 순위 · 설명 특징 | M7c |

**반환 계약**(공통)
```json
{
  "tool": "metric_anomalies", "contract_version": "1",
  "window": {"reference_time": "...", "lookback_minutes": 120, "resolution": "h"},
  "method": {"name": "stl_mad", "version": "1.0.0", "params_hash": "sha256:..."},
  "score_semantics": "relative_rank",
  "results": [ /* 상위 k만 · 원시 시계열 금지 */ ],
  "baselines": [ /* I-8 */ ],
  "limitations": ["시간 집계라 60분 미만 스파이크는 평활화됨", "..."],
  "data_quality": {"coverage": 0.97, "missing_periods": 2},
  "queried_at": "..."
}
```
- 응답 크기 상한을 둔다(상위 k ≤ 10 · 직렬화 ≤ 8KB). HolmesGPT는 큰 도구 결과를 디스크로 빼거나 버린다(`TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT`). **in-process 도구셋에도 같은 상한을 적용**한다.
- 데이터가 부족하면 빈 결과가 아니라 `{"error": {"code": "insufficient_history", "need_periods": 504, "have": 120}}`로 **사유를 구조화**한다(침묵 폴백 금지). `ml` extra가 없으면 `{"error": {"code": "ml_extra_not_installed"}}`를 반환한다.
- 인자 이름·앵커(`reference_time`·`lookback_minutes`)는 `polestar_*` 도구와 같게 둔다. `investigation_guidance`의 `ANCHORED_TOOLS`와 결합하기 위해서다.

### 4.5 조사 경로 연계

| 지점 | 변경 | 플래그(기본 off) |
|---|---|---|
| 사전수집 | `evidence_prefetch`가 mcp_server 배치를 받은 뒤 `ml_analysis.metric_anomalies`·`root_cause_candidates`를 **함수 호출**한다. mcp_server 호출을 중복하지 않도록 **이미 받은 시계열을 입력으로 넘긴다** — baseline 구간이 부족하면 필요한 만큼만 추가 조회한다 | `ML_EVIDENCE_PREFETCH_ENABLED` |
| in-process 도구셋 | `diagnosis.py`의 `Config(...)`에 `additional_toolsets` 주입 · `llm_instructions`: "점수는 상대 순위다 · 재계산 말고 인용하라 · 기준선과 갈리면 둘 다 인용하라" | `ML_EVIDENCE_TOOLS_ENABLED` |
| 지침 | `investigation_guidance`에 "ML 증거 인용" 블록(상관 인용 블록과 같은 형식) | 위 플래그에 종속 |
| 브리핑 | 신규 키 `ml_evidence`(공용 렌더러가 자동 렌더 · §2.1) | 위 플래그에 종속 |
| 가설 규칙 | **섀도**: ML 후보를 `ml_evidence.candidates`에만 싣고 `root_cause_hypotheses` 순서는 바꾸지 않는다. **병합**(평가 통과 후): ML 1위가 기존 규칙 후보와 일치하면 `agreement: true`만 표기한다. confidence 상한은 기존 규칙을 넘지 않는다. **LLM 인용 원인이 ML 순위를 바꾸지 않는다**(P-23) | `ML_HYPOTHESIS_MERGE`(섀도→병합은 §6 채택 게이트 후) |
| 결정적 상관과의 관계 | `domain/correlation.py`(평균·σ z-score · D-197)는 **보존**한다. ML 강건 점수는 `metric_findings`에 병렬 필드로 싣는다. 통합(σ→MAD 교체)은 M3 섀도 결과로 별도 판단한다 | — |
| 감사 영속화 | 조사 `done` 레코드에 `root_cause_hypotheses` 상위 3 · `ml_evidence` 요약 · 도구 호출 목록 · 브리핑 해시를 추가한다(크기 상한). **평가셋의 원천**이다 | `INVESTIGATION_AUDIT_DETAIL_ENABLED` |
| 풀 도구 | `interface/mcp_service.py`에 `sre_ml_*` 등록 | `ML_MCP_TOOLS_ENABLED` |

### 4.6 `noise_gate` 연계와 baseline 수렴

- **신호 전달(G-11)**: `sre_agent.ml_batch`가 Redis `ml:anomaly:{db_id}:{server_name}:{kind}` → `{score, verdict, method_version, computed_at, window}`를 발행한다(TTL = 스코어링 주기×2). 선례는 `alarm:baseline:*` 캐시 키 계약이다. `sre_agent`에는 `redis`(ml extra)와 `REDIS_URL` 설정이 추가된다. 대안인 "게이트가 `sre_ml_*`를 알람마다 동기 조회"는 워커 지연과 `sre_agent` 가용성 결합 때문에 권장하지 않는다.
- **소비(섀도 → 주석 → 상향)**
  1. 섀도: 게이트는 읽어서 `DecisionStore.signals`에 기록만 한다. 판정에 쓰지 않는다.
  2. 주석: 통보 본문·대시보드에 "ML 이상 점수" 표시.
  3. escalate-only: 기존 `anomaly_severity` 슬롯 매핑(D-110)을 재사용한다. `enable_ai_severity_boost`가 켜져 있을 때만 반영된다(`notification_policy.py:359-362`).
- **baseline 세 벌을 만들지 않는다.** `sre_agent` 배치의 STL+MAD가 섀도에서 게이트 E3(HW/STL-σ)를 이벤트 단위 지표로 이겼다고 판정되면, 게이트는 Redis 신호를 1순위로 읽는다.
  - **E3 in-process 계산은 폐기하지 않고 폴백으로 유지**한다. `ml_batch`가 멈추면 키가 만료되기 때문이다. 키가 만료되면 게이트는 E3로 돌아가고, 그 전환을 결정 로그에 남긴다.
  - 1순위 교체와 폴백 규칙은 같은 D-번호에 넣는다(D-161 ①). E3 폐기를 제안하려면 D-161 ② 4항 실측을 첨부해야 한다.

### 4.7 예측 결과의 통보 경로

예측은 새로운 알람 폭풍원이 될 수 있다(PRED-I-04 · RCA-F-03). 경로는 G-4로 확정한다. 권장은 **(가) 대시보드·브리핑 전용으로 시작해 섀도 월말 2회 뒤 재결정**이다.
- (나)를 택하면 예측 이벤트를 `source=sre_agent_ml` 이벤트로 게이트에 넣는다. 이 경우 **티어 상한은 TICKET**(PAGE 금지)이고, dedup·storm·silence를 그대로 거친다. 게이트 입력 스트림에 쓰는 것은 DB 쓰기가 아니므로 D-003 대상이 아니다. 다만 "관측 원천이 아닌 이벤트"의 첫 편입이라 신규 결정이 필요하다.

## 5. 기능별 설계

### 5.1 F0 — 데이터·라벨·평가 기반

| 산출물 | 내용 | 비고 |
|---|---|---|
| **데이터 카드**(`docs/`에 1건) | 원천별 해상도 · 보존 기간 · 적재 지연(시간 통계가 HH+몇 분에 생기는가) · 결측률 · 존별 결손 · 엔티티 정합률 | M0 실측값만 적는다. 추정 금지 |
| **리플레이 평가셋 빌더**(`sre_agent/scripts/ml/build_replay_set.py`) | 사건 = `alarm_incidents`(사람 대응) ∪ `cmm_alarm_note.alarmcause` 채워진 알람 ∪ 장애 보고서(J-5). 라벨 스키마 = `{root_entity, layer, kind, change_related?, confirmed_by, source}` · 요소별 부분 점수(OpenRCA 형식) | **새 라벨 저장소를 만들지 않는다**(U-F (ii)). 기존 원천을 읽어 **평가 전용 축소 산출물**을 서버 내에서 만든다(I-7). **v3: 산출물은 시각 기준으로만 정렬하고 행 index·원천 ID 차이를 남기지 않는다(KL-1·KL-2). 사후 기입 필드는 라벨 쪽에만 둔다(KL-3). 분할기는 사건 group ∧ 시간순(purged)을 동시에 강제하고 경계 구간을 버린다(KL-4)** |
| **피드백 이벤트 키 보강**(`plans/83` 편승) | `FeedbackStore.record`에 선택 필드 `alarm_id`·`fingerprint`·`root_cause{entity, layer, kind}` 추가. 값이 없으면 키를 넣지 않아 **기존 레코드 바이트 동일**(91 1-4 `investigation_id` 전례) | G-9. 20,000줄 회전이 평가셋을 자르지 않도록 빌더가 `.1`까지 읽는다 |
| **조사 감사 상세화** | §4.5 마지막 행 | 브리핑 채점(P-24)의 전제 |
| **골든셋 결함 점검** | 자명성(한 줄 규칙으로 풀리는가) · 이상 밀도 · 라벨 경계 오류 · 장애 직전 편향(PRED-D-01) | 평가 보고서 필수 절 |
| **누수 감사기**(v3 · `sre_agent/scripts/ml/leakage_audit.py`) | KL-1~KL-6을 기계적으로 검사한다. ① 피처 목록에 행 index·정렬 위치·ID 차이 계열이 있는가(KL-1·KL-2) ② **피처/라벨 분류표**를 출력하고 사후 기입 필드가 피처 쪽에 있으면 실패(KL-3) ③ 분할이 사건 group ∧ 시간순(purged)인가, 경계 구간을 버렸는가(KL-4) ④ 모든 롤링·분해 집계가 좌측 창만 쓰는가, 템플릿 사전이 학습 구간 고정 아티팩트인가(KL-5) ⑤ 재표본이 분할 이후 train 폴드 안에서만 일어나는가(KL-6) ⑥ **구간 간·존 간 adversarial AUC**(K-4) | **평가 보고서의 필수 절이자 CI 대상**. 하나라도 실패하면 그 평가 결과는 채택 게이트(§6.3) 입력으로 쓰지 않는다. `plans/101` §3.8.1의 근거 |

### 5.2 F1 — 이상탐지

**단계 1(M2, 라벨 불필요, torch 없음)**
- **계절 분해**: robust STL로 일(24h) 계절을 뽑는다. 이력이 3주 이상이면 주(168h) 계절도 추가한다. statsmodels가 없는 환경에서는 순수 Python 이중 계절 HW로 폴백한다(D-110 전례).
- **잔차 판정**: 중앙값·MAD 척도 점수 `|r − med| / (1.4826·MAD)`로 판정한다(P-2). 임계는 설정값이며, 달력 플래그(월말±1일·월초·급여일·배치 창)가 켜진 구간은 **별도 잔차 분포**로 판정한다.
- **평활화 보완**: `avg_val` 외에 `max_val`·`top_val` 계열을 병행 판정한다. 시간 평균이 지우는 스파이크를 일부 보완한다(P-5). 두 계열 판정이 갈리면 둘 다 표기한다.
- **변화점**: PELT(ruptures) 또는 순수 Python CUSUM으로 수준 이동 시각을 추정한다. 변경 이력·점검 창과 정렬해 "변경 직후 수준 이동"을 이벤트로 만든다(P-20).
- **반복 패턴 이탈**(v3 · K-6 · 보조 후보): matrix profile(`stumpy`)로 계열 자기 유사도가 가장 낮은 구간을 찾는다. 라벨·학습이 필요 없고, 시간 집계에서 "일·주 반복 패턴의 이탈"을 잡는 성격이 STL 잔차 점수와 상보적이다. 창 길이(subsequence length)를 24h·168h 등 복수로 돌리고 계열별로 고정한다(KDD Cup 2021 상위 해법의 방식). **채택은 리플레이셋에서 STL+MAD 대비 이벤트 단위 지표 우위가 확인될 때**이며, 그때까지는 점수를 병기만 한다.
- **피어 비교**(FUNNEL DiD 원리): 같은 역할 그룹(이중화 쌍·같은 호스트 그룹)이 있으면 대조군 대비 이탈을 계산한다. 그룹 정의는 **설정 파일**로 두고 자동 추정은 하지 않는다.

**단계 2(M7a, 평가 게이트 통과 시)**
- 서버별 다변량(CPU·메모리·디스크·네트워크) **PCA 재구성오차** · IsolationForest(sklearn) · 1-NN 거리를 비교군으로 둔다(PRED-D-06).
- **온라인 신호**: river HalfSpaceTrees를 게이트 보조 점수로 쓴다(스트림 증분). 재기동 시 상태 소실을 전제로 워밍업 구간을 표기한다.
- 운영자 피드백이 쌓이면 Opprentice식 탐지기 선택(랜덤포레스트)을 쓴다. 채택 조건은 §6.

**소스별 특칙**

| 소스 | 대상 지표 | 특칙 |
|---|---|---|
| 폴스타 | cpu·memory·filesystem·disk_io(b0는 filesystem 단수·disk_io 결손) | 시간 집계. 60분 미만 스파이크 탐지를 약속하지 않는다 |
| 제니퍼 | 응답시간 p95 · TPS · 에러율 · 액티브 서비스 · 힙 · GC | 분 단위(보존 J-7). **벤더 Anomaly Event가 API로 노출되면 입력으로 받고 같은 지표를 재계산하지 않는다**(G-6). 노출되지 않으면 단계 1을 보조로만 건다 |
| DPM | AAS · 대기 클래스 비율 · 락 대기 · 슬로우 SQL 수 | 단계 1 적용 + §5.4 DB 분해와 결합 |
| Prometheus | node_exporter 계열 | `PROMETHEUS_URL`이 설정된 환경만. 분 해상도라 ε-Diagnosis식 2표본 검정(RCA-A-03)이 의미를 갖는다 |

### 5.3 F2 — 예측

**(a) 고갈 ETA — M2**
- 대상: 폴스타 filesystem·memory 추세, (M5) 제니퍼 힙 used/committed·스레드풀, (M6) DPM 테이블스페이스·세션.
- 방법:
  1. 계절을 뺀 추세 성분에 강건 선형/로컬 선형 추세를 적합한다.
  2. 임계 도달 시각을 구한다.
  3. **split conformal 또는 ACI로 ETA 구간**(p10/p50/p90)을 만든다(P-13).
  4. 변화점 이후 구간만으로 재적합한다. 변화점이 최근 N구간 안에 있으면 `trend_unreliable` 사유를 반환한다.
- 출력은 **판정이 아니다.** "p50 ETA < 72h"를 대시보드·브리핑 강조 조건으로 쓸지는 규칙 설정이다.
- 평가: ETA 절대오차 분포, 구간 실측 커버리지 vs 목표, 조기·지연 비대칭 비용(§6).

**(b) 기대 대역 — M7b(보안 심사 후)**
- **선행 기준선(v3 · KC-05)**: 파운데이션 모델을 들이기 전에 **분위수별 별도 모델 + 경험적(empirical) 구간**을 먼저 구현한다. M5 Uncertainty 상위권의 공통 기법이 이것이었고(우승자는 집계 수준·분위수마다 별도 LightGBM), 894팀 중 ARIMA 기준선을 이긴 팀이 202팀(22.6%)뿐이었다는 사실이 "구간 예측은 어렵고 단순 경로가 이미 강하다"는 근거다. 우리 구현은 잔차 분위수 + 다중 창 median(K-7)로 충분히 시작한다.
- 파운데이션 모델을 **예측기로만** 쓴다. 분위수 대역이 단계 1 Seasonal Naive·STL·AutoETS 대비 MASE·CRPS에서 신뢰구간 밖으로 개선돼야 채택한다(P-14·P-15).
- 후보 순서: Chronos-2 small(28M) → Toto-2.0 small → TimesFM 2.5 → TTM-R2(CPU 하한 기준).
- 공변량(달력·배치 플래그)은 Chronos-2·TimesFM 2.5에 넣는다.
- 탐지 신호로는 점 이상·변화점 보조만 쓴다(P-4).
- 진입 조건: 가중치 라이선스 Apache-2.0/MIT · safetensors · 해시 고정 · **CPU 배치 창 안에 전 대상 추론 완료**(내부 측정 필수 · GAP-7).

**(c) 사건 위험 점수 — M7c(라벨 조건부)**
- **입력(v3 — K-2 · KC-01·KC-02 레시피로 구체화)**: 폴스타 알람 이력 → **Drain 자체 구현**(v2 확정 · drain3 미사용 — §3.5) 템플릿화 → eWarn식 특징을 **경기 검증된 네 계열**로 명세한다.
  1. **다중 창 롤링 통계** — 지표별 1h·6h·24h·168h 창의 median·MAD·max·기울기. **좌측(과거) 창만**(KL-5).
  2. **템플릿·알람 건수** — 템플릿별 건수, 심각도별 건수, volume의 min·mean·max·std·sum(Telstra가 로그 피처에서 점수를 낸 형태).
  3. **엔티티 인코딩** — 호스트·존·제품군·솔루션의 빈도 인코딩. target 인코딩은 **시간순 OOF 안에서만**(KL-4).
  4. **경과시간** — 마지막 변경·점검·재기동·직전 알람 이후 경과(Azure PM의 부품 age에 대응).
  - 여기에 단계 1의 이상 점수·ETA를 피처로 함께 넣는다. **사후 기입 필드(원인 텍스트·`resolution`·ack 시각)는 라벨로만 쓴다**(KL-3). 업무시간·달력 플래그는 결정적 공변량으로 둔다(P-3).
  - 네 계열 밖의 피처를 추가하려면 근거를 평가 보고서에 적는다.
- **모델(v3 — K-1로 확정)**: **GBDT**로 못 박는다. 1차는 **신규 의존이 없는 scikit-learn `HistGradientBoostingClassifier`**(LightGBM 계열 히스토그램 부스팅 · `ml` extra에 이미 포함)이고, lightgbm·xgboost·catboost 추가는 리플레이셋에서 sklearn 구현 대비 우위가 신뢰구간 밖일 때만 한다(추가 시 해석 검사 C-4 재실행). 저장은 skops. 불균형은 `class_weight`/`scale_pos_weight`로 다루고 **재표본은 분할 이후 train 폴드 안에서만**(KL-6). 비용 민감 임계(MING·CDEF 원리)는 **학습 구간에서 고정**하고 테스트·섀도에서 재조정하지 않는다(K-5 · 규약 13). 설명은 특징 기여로 낸다.
- **하지 않는 것**: 스태킹·다중 시드 앙상블로 지표를 쥐어짜는 것(K-9 — 이득은 소수점 셋째 자리이고 해석성·지연을 잃는다) · 딥 표 모델(K-1의 반대 근거).
- 진입 조건
  - 시스템당 확정 사건 **수십 건 이상**(eWarn 실측 26~227건/시스템 참고)
  - 운영 리드타임 요구치 Δt_w 합의
  - 단순 스파이크 규칙·연관규칙 기준선 구현 완료 · **피처 엔지니어링 없는 기본 설정 GBDT 기준선**도 함께(K-2 검증용)
  - **누수 감사기(§5.1) 통과** — 실패한 평가 결과는 채택 게이트 입력으로 쓰지 않는다
- 채택 조건: 이벤트 단위 정밀도·재현율과 리드타임 분포에서 기준선 대비 우위가 신뢰구간 밖이어야 한다(AirAlert 격차 0.6~2.7점 교훈). **v3: MCC와 임계 민감도 곡선을 함께 보고한다**(K-5).

### 5.4 F3 — 진단·RCA

**단계 R1 — 결정적 규칙·이벤트화(M3)**
1. **이벤트 정규화**(§4.3): 폴스타 알람, `ml_metric_anomalies` 이상, 변화점, 변경 이력, (M5) 제니퍼 이벤트, (M6) DPM 이벤트.
2. **간선**: `AVAIL_DEPEND` 토폴로지(D-107) · 같은 호스트 · (채움 확인 시) `cmm_dependency_link`·`cmm_service_associate` · (M5) 호스트↔WAS 매핑 · (M6) WAS↔DB 매핑. **폴스타 자체 `root_alarm_id`·`prev_alarm_id`가 채워져 있으면 1급 간선으로 쓴다**(J-3).
3. **규칙**: 변경 선행(D-209 C′-2 계승) · 토폴로지 조상 선행 · 알람 스톰 대표(ICSE-SEIP'20 원리). 규칙은 **정본 설정 파일**(`sre_agent/config/ml/rca_rules.yaml`)로 두고 코드에 쓰지 않는다(Groot 원리 · `apm_playbooks.yaml` 자세와 동일).
4. **변경 영향 판정**: 대조군이 정의된 경우 DiD로 "변경 대상만 이탈했는가"를 계산한다(P-20).

**단계 R2 — 라벨 불필요 강건 점수(M3)**
- **BARO식 RobustScorer**: 이상 시작 이전 구간의 중앙값·IQR로 척도화한 뒤 이후 최대 편차로 순위화한다. 이상 시작 시각은 변화점(다변량 BOCPD 또는 단계 1 변화점) 추정값을 쓴다. **재구현하며 의존은 0이다.**
- **의무 기준선 병렬 산출**: Dummy(무작위) · max-|Z| · 알람 건수(게이트가 이미 가진 신호). 결과가 기준선과 갈리면 표기한다(I-8).
- **장애 시각 민감도**: 알람 시각 ±30/60분에서 순위가 안정적인지 계산해 `stability`로 반환한다. CIRCA·NSigma가 시각 오차에 민감하다는 근거(RCA-B-01)에 따른 것이다.

**단계 R3 — 계층별 전용 분해(M5·M6)**

| 계층 | 방법 | 입력 | 근거 |
|---|---|---|---|
| WAS(제니퍼) | 응답시간 **시간 분해**(cpu·sql·fetch·external·network) → 지연이 쌓인 계층 판정. `plans/87` `apm_slow_transactions`가 결정적 1차 구현이며 **재구현하지 않고 소비**한다 | `apm_*` | P9(87) |
| WAS(제니퍼) | **Squeeze/PSqueeze** 다차원 국소화(인스턴스×서비스×업무) + HALO 계층 가지치기 · 외부 원인 판정 | 인스턴스·서비스별 응답시간·에러율 실제값 vs 기대값(단계 1 대역) | RCA-E-02·E-03 |
| 호스트↔WAS | **관측 불일치**: 호스트 지표 정상 × 앱 지표 이상(또는 역) | 폴스타 + 제니퍼 | Gray Failure · MicroRCA 이중 노드 |
| 호스트↔WAS(실험) | PRISM 내부/외부 속성 분해 순위. **공리 위배 유형(외부 부하 급증·DB 락 대기)은 규칙으로 먼저 거른다** | 위와 같음 | RCA-A-12(preprint) |
| DB(DPM) | **ADDM식 DB Time/AAS 분해**: 대기 클래스·SQL·자원의 몫 순위(결정적) | `dpm_wait_class_breakdown`·`dpm_top_sql` | RCA-D-01 |
| DB(DPM) | **고영향 SQL ≠ 원인 SQL** 구분 표기(목록을 그대로 원인으로 넘기지 않는다) | `dpm_top_sql` 시계열 | RCA-D-04 |
| DB(DPM) | iSQUAD: 슬로우 쿼리 KPI 이상 유형 추출 → 군집 → **군집당 1회 DBA 라벨** | DPM KPI + 폴스타 호스트 KPI | RCA-D-03 |

**단계 R4 — 지식 스켈레톤 구조 그래프(M7d)**
- **CIRCA**: DBA·운영자가 검토한 **메타 스켈레톤**을 둔다. 예: `호스트 자원 → JVM(힙·GC·스레드) → WAS 응답·액티브 서비스 → DB 세션·대기 클래스 → SQL`. 여기에 회귀 기반 가설검정과 하위 노드 보정을 적용한다.
  - 스켈레톤은 **정본 파일 + 검토 기록**으로 관리하고 자동 발견하지 않는다(P-17).
  - **DB 계층부터 시작한다.** 은행 Oracle 근거가 있기 때문이다(CIRCA AC@1 0.404).
- **DoWhy-GCM `attribute_anomalies`**: 스켈레톤이 DAG로 확정되는 좁은 구간에서만 쓰고, "기여도 추정치"로 표기한다. 분포 밖 반사실 추정이 불안정하다는 비판(ICLR'25 IDI)을 반영한다.
- **순환 의존**(WAS 풀 ↔ DB 세션)은 DAG로 강제하지 않는다. MRF 방식(Murphy, SIGCOMM'23)은 원문 확인 뒤 재평가한다.

**회피 목록**(운영 경로): PC/FCI/Granger/PCMCI/신경 Granger 자동 인과 발견 + PageRank/랜덤워크 · 트레이스 필수 모델(Nezha·MicroRank·TraceRCA) · 지도학습 멀티모달 딥러닝(Eadro·DiagFusion·MULAN·RCRank). 근거는 §3.4 P-17·P-18이다.

### 5.5 F4 — 유사 사건 검색(M3)

- **코퍼스**: `cmm_alarm_note`(alarmcause·message) · `cmm_alarm_knowledge`(faulttypename·faultcontent·processcontent) · 조사 감사 상세(§4.5) · (J-5 확인 시) 장애 보고서 · 피드백 `note`.
- **방법**: RCACopilot 방식이다(EuroSys'24, 기존 dossier). 텍스트 유사도 × **시간 감쇠** `e^(−α|Δt|)`로 계산한다. 1차는 scikit-learn TF-IDF(문자 n-gram — 한국어 형태소 분석기 없이 동작)로 torch 없이 시작한다. 2차는 **이미 반입 협의된 e5-small**(D-114 · 로컬 디렉토리 · CPU)을 `ml-embed` extra로 쓰며 신규 모델 반입은 0이다. 메트릭 특징(단계 1 이상 지표 집합의 Jaccard)을 보조 유사도로 결합한다. 1·2차 우열은 리플레이셋으로 판정한다. **[v6] 2차(e5 하이브리드)는 G5 GPU에서 돈다(GA-3 · §R.10.1). 1차 TF-IDF는 `run_service` 정본이자 폴백이다.**
- **출력**: 과거 사례의 원인·조치를 "**과거 유사 사례**"로만 표기한다. 현재 원인이라고 단정하지 않는다.
- **주의**: 임베딩 의미 오매칭 점검 의무(D-200 전례)를 둔다. 폴스타 `note` 자유 텍스트의 PII는 기존 마스킹 계층(`data_masker`)을 통과한 뒤에만 색인한다.

### 5.6 소스 × 기능 연계 매트릭스

| 소스 | F1 탐지 | F2 예측 | F3 RCA | F4 유사 사건 | 선행 조건 |
|---|---|---|---|---|---|
| **폴스타** | M2 STL+MAD·변화점·피어 | M2 filesystem/memory ETA | M3 이벤트 그래프·강건 점수·변경 DiD | M3 note/knowledge | M0 데이터 카드(`_h` 보존·`root_alarm_id` 채움) |
| **제니퍼** | M5(벤더 Anomaly Event 우선) | M5 힙·스레드풀 ETA | M5 시간 분해 소비·Squeeze·관측 불일치 | M5 이벤트 텍스트 | `plans/87` J1·J2(`apm_*`) · U-3(보존) · 인스턴스 정합 |
| **DPM** | M6 AAS·대기 비율 | M6 테이블스페이스·세션 ETA | M6 ADDM 분해·SQL 구분·iSQUAD → M7d CIRCA | M6 | **DPM 연동 계획 신설(G-3)** · 제품·API 확인(J-8) |
| **Prometheus** | M2 이후 선택(분 해상도) | 선택 | 분 해상도 보강 | — | `prom_metric_range`(PromQL · 운영 `PROMETHEUS_URL` — `plans/91` 1-12) · ~~`plans/92` 트랙 A~~ **[정정 2026-09-22]** 트랙 A(`om_*`)는 exporter **현재값만** 준다(92 §1.2 — 시계열 보관 제외). 분 해상도 **이력** 입력이 될 수 없다(92 v3 §0.0.4) |
| **ITAM** | — | — | 엔티티 속성(가상화 관계·운영환경)만 | — | `plans/95` 게이트. 사용률은 폴스타 정본(G-8 (가)) |

### 5.7 DPM 연계에 필요한 읽기 계약(신설 계획에 넘기는 요구 명세)

DPM 커넥터는 이 계획의 범위가 아니다. 다만 F1~F3가 요구하는 **최소 도구 표면**을 명세해 DPM 계획의 입력으로 넘긴다. 벤더 중립 이름을 쓰고 `mcp_server` 경계를 확장한다(D-119 ①).

| 도구(가칭) | 인자 | 반환 요지 | 용도 |
|---|---|---|---|
| `dpm_instance_map` | `hostname?` | DB 인스턴스 ↔ 호스트 · `match_confidence` | 엔티티 정합 |
| `dpm_activity_trend` | `db_instance`, `reference_time`, `lookback_minutes`, `resolution` | AAS · 활성 세션 · 락 대기 세션 시계열(요약) | F1·F2 |
| `dpm_wait_class_breakdown` | 동상 | 대기 클래스별 시간 몫 · 상위 대기 이벤트 | F3 ADDM 분해 |
| `dpm_top_sql` | 동상, `n≤20` | SQL ID별 경과시간·실행수·대기 몫(바인드 값 마스킹) | F3 SQL 구분 |
| `dpm_lock_chains` | `db_instance`, `reference_time` | 블로킹 체인 요약 | F3 |
| `dpm_events` | 동상 | 벤더 이벤트·(있다면) 예측 이벤트 | F1 입력 · G-6 |

---

## 6. 평가 체계 — 채택 게이트

### 6.1 과업별 지표

| 과업 | 오프라인 비교(알고리즘 선택) | 운영 채택(결정 경로 연결) |
|---|---|---|
| 탐지(F1) | **VUS-PR**(보조 AUC-PR) · PA 금지 · best-F(오라클 임계) 금지 | 이벤트 단위 정밀도·재현율 · 탐지 지연 분포 · **알람 예산**(서버-일당 신규 표시·알람 수) |
| 고갈 ETA(F2a) | ETA 절대오차 분포 · 구간 커버리지(목표 ± 허용폭) · 구간 폭 | 조기·지연 비대칭 비용 · 운영자 조치 유효율 |
| 기대 대역(F2b) | MASE · CRPS/WQL(Seasonal Naive 정규화 · 기하평균) · 분위수 커버리지 | 단계 1 대비 알람 예산 변화 |
| 사건 위험(F2c) | 이벤트 단위 P/R · **리드타임 분포**(Δt_w 미만 적중은 실패) · 창 단위 F1은 부지표 · **MCC와 임계 민감도 곡선**(v3 · K-5 — 불균형에서는 임계가 점수의 일부다) | 새 알람 폭풍 유발 여부 |
| RCA(F3) | **AC@1 · AC@3 · Avg@5** · 요소별 부분 점수 · 실행시간 · 장애 시각 ±오차 민감도 · **해상도별**(원시 vs 1시간) | 계층·존별 성적 · 기준선 대비 우위 |
| 브리핑(LLM) | **원인 적중 + 증거 폐포율(ECR) + Majority@k** | 운영자 "유용" 판정률 · 조사 시간 |

### 6.2 공통 규약

1. **의무 기준선**(P-9)을 결과표 첫 열에 둔다. 기준선을 이기지 못한 모델은 채택하지 않는다.
2. **계층·존·업무별 분리 보고**, 합산 리더보드 금지(P-10).
3. **시간순 rolling-origin**, 셔플 금지. 테스트 구간에 월말 ≥2회(P-11).
4. **유의성**: 부트스트랩 신뢰구간 또는 Friedman–Nemenyi 임계차. 평균 차이만으로 채택하지 않는다.
5. **표본 <20이면 성능 문구를 쓰지 않는다**(D-176 ⑤). 결과표에 `n`을 항상 적는다.
6. **개발·샌드박스 측정치를 운영 기준선으로 인용하지 않는다**(D-174). 로컬 `testdata`는 계약·구현 정확성 테스트 전용이다.
7. **공개 벤치마크 용도 제한**: RCAEval RE1(MIT)·PetShop(5분 · Apache-2.0)·TSB-AD(원천별 라이선스 선별)·BOOM(Apache-2.0)은 **구현 정확성 회귀**에만 쓴다. 사내 성능 기대치 근거로 쓰지 않는다(P-12). AIOps Challenge 데이터(비상업 조건)·LogHub(연구용)는 법무 확인 전 보류한다. **v3 — Kaggle 데이터도 같은 범주다**: 구현 회귀·누수 점검 연습 전용이며(K-8) 사내 성능 기대치 근거로 쓰지 않는다. 쓸 수 있는 것과 못 쓰는 것은 `ml_kaggle_competition_survey.md` KD-01~KD-08이 구분한다 — **경기 데이터(Telstra·Bosch·ASHRAE·M5·VSB)는 규칙 동의가 전제이고 비상업 한정이 흔해 폐쇄망 반입 대상이 아니다**(GAP-12). NAB는 라이선스가 엇갈려 원문 확인 전 사용 보류다.
8. **섀도 운영**: 결정 경로(게이트 상향·가설 병합·예측 통보)를 켜기 전에 월말 ≥2회 섀도 병행한다. 이벤트 단위 지표로 판정한다.
9. **실 LLM 평가(브리핑 채점·LLM 재순위 A/B)는 건별 사용자 승인**(D-127)이다. 내부망 fabrix·vllm은 D-211 ⑪·D-216 ①·D-222의 면제 규정을 따른다.
10. **A/B는 동일 커밋·subprocess 격리·설정 에코**로 한다(D-211 ⑧ · known mistakes L56·L123).
11. **(v3) 누수 감사기를 통과하지 못한 평가 결과는 채택 게이트 입력이 아니다.** KL-1~KL-6(§3.8.1)을 기계 검사하고 **피처/라벨 분류표**를 평가 보고서에 싣는다. 분할은 **사건 group ∧ 시간순(purged)을 동시에** 만족해야 하며 경계 구간은 버린다.
12. **(v3) 리플레이셋 점수와 섀도 성적을 쌍으로 기록하고 그 관계를 채택 판단에 쓴다**(K-10). 리플레이 최고점 하나로 채택하지 않는다. 두 값이 어긋나는 모델은 사유를 적고 보류한다.
13. **(v3) 임계·컷오프는 학습 구간에서 고정한다.** 테스트·섀도 구간에서 재조정하면 오라클 임계이며 §6.1의 best-F 금지와 같은 위반이다(K-5). **구간 간·존 간 adversarial AUC ≥ 0.7인 분할**로 낸 성적도 채택 근거로 쓰지 않는다(K-4).

### 6.3 채택 게이트 표

| 전환 | 조건(모두 충족) |
|---|---|
| 도구 노출(`ML_EVIDENCE_TOOLS_ENABLED`) | 계약 테스트 통과 · 응답 크기 상한 · `insufficient_history` 사유 구조화 · 데이터 카드상 대상 소스 해상도·보존 확인 |
| 결정적 사전수집 편입 | 위 + 사전수집 타임박스 내 p95 지연 실측 |
| 게이트 주석 | 섀도 기록 2주 · 알람 예산 영향 0(주석은 통보 수를 늘리지 않음) 확인 |
| 게이트 escalate-only | 섀도 월말 ≥2회 · 이벤트 단위 재현율 향상이 신뢰구간 밖 · 알람 예산 증가 허용치 이내(운영자 합의값) |
| 가설 병합(`ML_HYPOTHESIS_MERGE`) | 리플레이셋 AC@3이 기존 규칙 대비 우위(계층별) · 기준선 대비 우위 · n ≥ 20 |
| E3 강등(§4.6) | 게이트 escalate-only 조건 + D-161 ② 4항 실측 |
| 파운데이션 모델(M7b) | 라이선스·해시·보안 심사 통과 · CPU 배치 창 실측 · MASE·CRPS 우위 신뢰구간 밖 |
| 사건 예측(M7c) | 라벨 조건(§5.3 (c)) · 리드타임 합의 · 단순 규칙 대비 우위 |

---

## 7. 단계(Wave) 계획

> 모든 단계는 **코드 기본 off**로 랜딩한다. "완료"는 계약·단위 테스트와 샌드박스 동작까지다. 운영 효과 판정은 §6 채택 게이트가 한다.
> 경로는 `sre_agent/` 패키지 루트 기준이다(`sre_agent/sre_agent/…` = 파이썬 패키지 · `sre_agent/scripts/…`·`sre_agent/config/…`·`sre_agent/tests/…` = 패키지 루트). **`sre_agent` 테스트는 `sre_agent/` cwd에서 자체 venv로 실행한다**(known mistakes L193).

### M0 — 데이터·전제 실측 (선행 · 코드는 읽기 전용 실측 스크립트뿐)

| ID | 확인 사항 | 방법 | 판정에 쓰는 곳 |
|---|---|---|---|
| **J-1** | `cmm_metric_stat_h`·`_d` 보존 기간 · 적재 지연(HH+몇 분) · 서버별 결측률 · `top_val`/`bottom_val` 채움(**`plans/91` 1-9와 같은 조회 · 결과를 공유**) | `cmm_metric_stat_table_info` · `MIN/MAX(stat_date)` · 행 수 | 계절 요건(P-3) · 배치 주기 |
| **J-2** | 원시·분 단위 원천 적재 여부: `metricstatisticseconddata` · `cmm_trait_history_YYYYMM` · `cmm_availability_log` | 테이블별 최근 7일 행 수 · 간격 | 해상도(GAP-1) |
| **J-3** | `cmm_alarm.root_alarm_id`·`prev_alarm_id` 채움률 · `cmm_alarm_note.alarmcause`·`cmm_alarm_knowledge` 채움률 · `cmm_dependency_link`·`cmm_service_associate` 행 수 | 채움률 SQL(존·DB별) | RCA 간선 · 라벨 원천 |
| **J-4** | 변경 이력 `event_time` 단위·실데이터 여부(`change_feed.py:13-21`의 미확정) | 표본 대조 | 변경 DiD |
| **J-5** | 과거 장애 목록(ITSM·장애 보고서) 존재·접근 경로·건수(최근 2년) | 운영 부서 확인 | 리플레이셋 규모 · M7c 조건 |
| **J-6** | 운영 `sre_agent` venv(Python 3.13) 실재 · extra wheel 반입 절차·소요 기간 · torch CPU wheel 준비 방식(M7b·F4 2차에만 필요) | 운영 확인 | 반입 일정(v2: 라이브러리 선택 제약이 아니다) |
| **J-7** | 제니퍼 `/api/dbmetrics` 보존·해상도(**`plans/87` U-3과 공유**) · Anomaly Event가 `/api/dbsearch/event`로 조회되는가 | 실 서버 조회 | G-6 · M5 |
| **J-8** | 운영 DPM 제품·버전 · REST API 유무 · XAIOps 도입 여부 | 운영 확인 | G-3 |
| **J-9** | `sre_agent` 호스트의 배치 스코어링 가용 CPU·메모리(대상 서버 수 × 지표 수) · `run_service`와 공존 시 여유 **[v5: `run_service` 경량 통계 여유만 — 배치 용량은 G5(J-20) · §R.10.7]** | 운영 서버 여유 실측 | 배치 주기 · C-2 · M7b |
| **J-10** | `alarm_incidents` 운영 활성 여부(`incident_tracking_enabled`)와 누적 건수 | `.env` 실제값 · 테이블 행 수 | 리플레이셋 원천 |
| **J-11**(v3) | **공개 데이터 라이선스·사용 조건 원문**: 경기 규칙(Telstra·Bosch·ASHRAE·M5·VSB) · Backblaze 약관 · **NAB LICENSE**(MIT 주장과 AGPL-3.0 이력 주장이 엇갈린다) · SMD·Azure PM·AI4I 표기. 이 조사에서는 페이지가 JS 렌더링이라 정적 확인이 되지 않았다 | 해당 페이지를 사람이 직접 열어 라이선스 문자열을 확인한다(필요 시 법무) | 공개 데이터 사용 범위(§6.2-7) · 반입 가능 여부(§9) · GAP-12. **M1 착수를 막지는 않는다** — 공개 데이터를 실제로 쓰려 할 때의 선행 조건이다 |

- **산출물**: `sre_agent/scripts/ml/data_card.py`(읽기 전용 · 기존 `mcp_tool_client` 경유 · 결과는 축소 JSON), 데이터 카드 문서 1건.
- **완료 판정**: J-1~J-4·J-10 실측값이 문서에 있다. 확인 불가 항목은 "미확인 + 사유"로 적는다. **추정값을 적지 않는다.**
- **과금**: 없음.
- **분기**: J-1에서 `_h` 보존이 3주 미만이면 주간 계절을 끄고 일간 STL만 쓴다. J-2에서 분 단위 원천이 있으면 `mcp_server` 도구 확장을 별도 CU로 올린다(D-119 경계 · 신규 게이트).

### M1 — 기반

| CU | 대상 | 변경 | 규모 |
|---|---|---|---|
| CU-1 | `sre_agent/pyproject.toml` · `scripts/ml/resolve_check.sh` | **holmesgpt 0.42.x로 상향**(사용자 허용 · G-8) · extra `ml`(numpy · scipy · `pandas>=2.2,<3` · scikit-learn · statsmodels · ruptures · stumpy · pyod · river · skops · redis) · `ml-forecast`(statsforecast>=2.1) · `ml-causal`(dowhy · causal-learn) · `ml-tsfm`(chronos-forecasting · torch CPU) · `ml-embed`(sentence-transformers). 해석 검사 스크립트(C-4) · 실제 venv 설치 후 **`inspect`로 holmes SDK 시그니처 재실측**(`Config`·`additional_toolsets`·`ToolCallingLLM`·`build_initial_ask_messages`) | 중 |
| CU-2 | 패키지 골격 | `sre_agent/domain/ml/`(순수) · `sre_agent/application/ml_analysis.py`·`ml_toolset.py`·`ml_batch_scorer.py` · `sre_agent/infrastructure/ml/`(lazy 어댑터) · `sre_agent/ml_batch.py`(엔트리) · `config/ml/` · `scripts/arch_check.py` `MODULE_LAYER_MAP`에 신규 모듈 등록(`ml_batch`=entry) · `tests/test_boundary.py` 스캔 대상에 ML 모듈 포함(I-1) · **`domain/ml` 외부 import 허용목록 스캔 테스트**(I-10) · **`ml` extra 설치 단언 테스트**(C-5) | 중 |
| CU-3 | `application/ml_toolset.py` · `interface/mcp_service.py` | `status` 도구(②·③ 표면) · `ML_EVIDENCE_TOOLS_ENABLED`·`ML_MCP_TOOLS_ENABLED`(off) | 소 |
| CU-4 | `domain/ml/metrics_eval.py` · `scripts/ml/build_replay_set.py` · **`scripts/ml/leakage_audit.py`**(v3) | VUS-PR · AC@k · 이벤트 단위 P/R · 리드타임 · 커버리지 · 기준선(Dummy·max-\|Z\|·알람 건수 · **다중 창 median** K-7) · **MCC**(K-5) · **사건 group ∧ 시간순(purged) 분할기**(KL-4) · **누수 감사기 KL-1~KL-6 + adversarial AUC**(§3.8.1 · §5.1) | 중 |
| CU-5 | `application/investigation_dispatcher.py` | 감사 상세화(§4.5) · `INVESTIGATION_AUDIT_DETAIL_ENABLED`(off) | 소 |
| CU-6 | `noise_gate/infrastructure/feedback_store.py` + `src/api/routes/alarm.py` 요청 스키마 | 선택 필드 `alarm_id`·`fingerprint`·`root_cause` (값 없으면 키 미기록 → 바이트 동일) · **G-9 확정 후** | 소 |
| CU-7 | 문서 | `sre_agent/README.md` 범위·개발 명령(`.[dev,ml]`) · `docs/26_sre_agent_guide.md` ML 절 · `CLAUDE.md` 저장소 지도·패키지 경계 표(C-6)·기동 명령(`ml_batch`) | 소 |

- **완료 판정**
  - `sre_agent/tests`가 `.[dev]`와 `.[dev,ml]` 두 설치에서 통과한다(off 비트 동일).
  - `scripts/arch_check.py --ci` 0.
  - 루트 `overfit_check.py --ci` 신규 위반 0(`sre_agent/domain` 스캔 · 기준선 전면 재생성 금지).
  - holmes 0.42.x 상향 후 기존 조사 스텁·가드 테스트(`test_diagnosis_*`·`test_load_guard*`·`test_investigation_*`)가 전건 통과한다.
  - `resolve_check.sh`가 Linux x86_64·py3.13에서 성공한다.
- **과금**: 없음. holmes 상향의 실 LLM 회귀(조사 e2e)는 **D-127 건별 승인** 후에만 한다.

### M2 — 단계 1 탐지·ETA (폴스타)

| CU | 대상 | 변경 |
|---|---|---|
| CU-8 | `domain/ml/seasonal.py` · `infrastructure/ml/stl_adapter.py` | 이중 계절 분해(statsmodels STL · 순수 Python HW 폴백) · MAD 잔차 점수 · 달력 플래그 |
| CU-9 | `domain/ml/changepoint.py` · `infrastructure/ml/ruptures_adapter.py` | CUSUM(순수) · PELT(ruptures lazy) |
| CU-10 | `domain/ml/eta.py` | 추세 적합 · split conformal/ACI · `trend_unreliable` 사유 |
| CU-11 | `application/ml_analysis.py` · `ml_toolset.py` · `mcp_service.py` | `metric_anomalies` · `exhaustion_forecast`(§4.4 계약 · ②③ 표면) |
| CU-12 | `application/ml_batch_scorer.py` · `ml_batch.py` | 시간 주기 배치(적재 지연 J-1 반영 · 동시성·호출 속도 상한) · Redis `ml:anomaly:*` 발행(TTL) · 모델 메타 SQLite(`.data/ml/`) |
| CU-13 | 백테스트 `scripts/ml/backtest.py` | 리플레이셋 + 합성 시계열로 계약·수치 회귀. **운영 성능 판정은 섀도에서** |

- **완료 판정**: 합성 계절 시계열 단위 테스트 5종이 통과한다.
  - 일·주 계절 복원
  - 레벨 이동 검출
  - 누수형 추세 ETA 커버리지
  - 결측 구간 사유 반환
  - `avg`/`max` 판정 분기
- 계약 스냅샷 테스트, 응답 크기 상한 테스트, `ml_extra_not_installed` 사유 테스트도 통과해야 한다. `ml_batch`는 `run_service`와 **별도 프로세스로만 기동됨**을 테스트로 단언한다(엔트리 분리 · I-9).
- **과금**: 없음.

### M3 — 단계 1 RCA · 유사 사건 · 조사 연계 (폴스타)

| CU | 대상 | 변경 |
|---|---|---|
| CU-14 | `domain/ml/event_graph.py` | 이벤트 정규화 · 간선(토폴로지·같은 호스트·`root_alarm_id` 조건부) · 규칙 적용 |
| CU-15 | `domain/ml/robust_rank.py` | BARO식 RobustScorer 재구현 · 기준선 3종 · 시각 민감도 `stability` |
| CU-16 | `domain/ml/change_did.py` | 대조군 설정 기반 DiD |
| CU-17 | `config/ml/rca_rules.yaml` · `metric_map.yaml` · `peer_groups.yaml` | 정본 설정. 폴스타 리터럴은 여기와 어댑터에만 둔다 |
| CU-18 | `application/ml_similar_incidents.py` · `infrastructure/ml/text_index.py` | 1차 TF-IDF(sklearn · 문자 n-gram) + 시간 감쇠 · 마스킹 후 색인. 2차 e5(`ml-embed` · D-114 모델 경로) |
| CU-19 | `application/ml_analysis.py` · 표면 ②③ | `root_cause_candidates` · `similar_incidents` |
| CU-20 | `evidence_prefetch` · `investigation_guidance` · `briefing_builder` · `diagnosis.py` | §4.5 표(사전수집 함수 호출 · in-process 도구셋 · 지침 · `ml_evidence` · 가설 **섀도**) · 플래그 off |
| CU-21 | 평가 | 리플레이셋 AC@k(계층·존별) · 기준선 대비 · 해상도 민감도 보고서 |

- **완료 판정**
  - `sre_agent/tests`가 통과한다.
  - 플래그 off일 때 조사 결과가 비트 동일하다(골든 비교).
  - 스텁 조사 경로에서 `ml_evidence` 렌더를 확인한다.
  - ML 예외를 주입해도 조사 잡이 `done`으로 끝나고 `ml_evidence.error`만 남는다(I-9).
- **과금**: 실 HolmesGPT 조사 e2e는 **D-127 건별 승인** 후에만 한다(`plans/91` 1-17과 묶을 수 있음).

### M4 — 노이즈 게이트 신호

| CU | 대상 | 변경 |
|---|---|---|
| CU-22 | `noise_gate/infrastructure/ml_signal_reader.py` | Redis `ml:anomaly:*` 읽기(개별 try/except · 부분 반환 · known mistakes L35) · 키 만료 시 E3 폴백 표기 |
| CU-23 | `noise_gate` 컨텍스트 조립 | 섀도: `DecisionStore.signals.ml` 기록만 → 주석 → escalate-only(기존 `anomaly_severity` 슬롯) · 플래그 `ML_GATE_SIGNAL_MODE∈{off,shadow,annotate,escalate}`(기본 off · 만료일 부여) |
| CU-24 | 관제 대시보드(`plans/54` F4 자리) | ML 이상·ETA 패널(읽기 전용 · `sre_ml_*` 조회 또는 Redis) |
| CU-25 | `docs/flag_audit.md` | 신규 플래그 행 · 만료일(D-162) |

- **완료 판정**: off일 때 게이트 결정 로그가 비트 동일하다(E3 매트릭스 테스트 재사용 · known mistakes L37). shadow에서는 판정 불변을 단언한다.
- **과금**: 없음.

### M5 — 제니퍼 연계 (선행: `plans/87` J1·J2 · J-7)

| CU | 변경 |
|---|---|
| CU-26 | `config/ml/metric_map.yaml`에 제니퍼 지표 매핑 · 단계 1 탐지·ETA를 `apm_*` 입력으로 확장 |
| CU-27 | 벤더 이상 이벤트 편입(G-6 · J-7 노출 확인 시): 이벤트 스키마 `vendor_anomaly` · 같은 지표 재계산 억제 |
| CU-28 | `domain/ml/dim_localize.py`: Squeeze/PSqueeze·HALO 가지치기 · 외부 원인 판정 → `dimension_localize` **[v6: 실행 노드 G5 `ml_service` GA-2 · RC-24 · §R.10.1]** |
| CU-29 | `layer_divergence`(호스트↔WAS 관측 불일치) · 매핑 신뢰도 `high`만 결합 |
| CU-30 | 이벤트 그래프에 호스트↔WAS 간선 · RCA 계층별 평가 보고 |
| (실험) | PRISM 내부/외부 분해를 섀도 전용 트랙으로 둔다. 공리 위배 규칙이 선행한다 |

### M6 — DPM 연계 (선행: DPM 연동 계획 신설 · §5.7 계약 구현 · J-8)

| CU | 변경 |
|---|---|
| CU-31 | ADDM식 DB Time/AAS 분해 · 고영향/원인 SQL 구분 표기 |
| CU-32 | DB 테이블스페이스·세션 ETA |
| CU-33 | iSQUAD식 이상 유형 추출·군집 · **군집당 1회 DBA 라벨** 입력 경로(피드백 확장 재사용 · 별도 저장소 금지) |
| CU-34 | 이벤트 그래프에 WAS↔DB 간선 · 교차 계층 RCA 평가 |
| CU-35 | (XAIOps 도입 시) 벤더 예측 이벤트 편입 |

### M7 — 조건부 고도화 (각 항목이 §6.3 게이트를 독립 통과해야 착수)

| ID | 내용 | 착수 조건 |
|---|---|---|
| **M7a** | 경량 ML: PCA 재구성 · IsolationForest(sklearn) · pyod(ECOD 등) · river HalfSpaceTrees 게이트 보조 점수 · Opprentice식 탐지기 선택 | 단계 1 섀도 결과가 있고, 리플레이셋에서 VUS-PR·이벤트 단위 지표가 단계 1 대비 신뢰구간 밖으로 우위 |
| **M7b** | 파운데이션 모델 예측기(Chronos-2 small → Toto-2 small → TimesFM 2.5 → TTM-R2) · `ml-tsfm` extra · **`ml_batch` 프로세스에서만 추론**(C-2) · torch CPU wheel · safetensors **[v5: G5 `ml_batch`(백테스트·배치)·`ml_service`(온라인 예측 구간) · CUDA wheel · §R.10.7]** | G-7 · 가중치 보안 심사 · J-9 CPU 실측 · MASE·CRPS 우위 · 해석 검사(C-4) 통과 |
| **M7c** | 사건 위험 점수(eWarn식 · **모델 = GBDT** 1차 sklearn `HistGradientBoostingClassifier` — K-1) · **Drain 자체 구현** 템플릿(학습 구간 고정 아티팩트 · KL-5) · 피처 4계열(K-2) · skops 모델 · 비용 민감 임계(학습 구간 고정 · K-5) | 라벨 수 · Δt_w 합의 · 단순 규칙 기준선 + **기본 설정 GBDT 기준선** · **누수 감사기 통과**(규약 11) |
| **M7d** | CIRCA 스켈레톤 그래프(DB 계층부터) · DoWhy-GCM 기여도(`ml-causal` extra) | M6 완료 · DBA 스켈레톤 검토 기록 · R2 기준선 대비 우위 |
| **M7e** | (선택) LLM 재순위 A/B: "서술만" vs "재순위 허용" | D-127 승인 · 별도 결정. 기본값은 "서술만" 유지 |

---

## 8. 산출물 요약 · 게이트 영향

### 8.1 신규·변경 파일(예정)

| 구분 | 경로 |
|---|---|
| `sre_agent` 신규 | `sre_agent/domain/ml/` · `sre_agent/application/ml_analysis.py`·`ml_toolset.py`·`ml_batch_scorer.py`·`ml_similar_incidents.py` · `sre_agent/infrastructure/ml/` · `sre_agent/ml_batch.py` · `config/ml/` · `scripts/ml/` · `tests/test_ml_*` |
| `sre_agent` 변경 | `pyproject.toml`(holmes 상향 · extras) · `settings.py`(플래그 · `REDIS_URL`) · `diagnosis.py`(`additional_toolsets`) · `application/evidence_prefetch.py` · `application/investigation_guidance.py` · `application/briefing_builder.py` · `application/investigation_dispatcher.py`(감사 상세) · `interface/mcp_service.py`(`sre_ml_*`) · `scripts/arch_check.py` · `tests/test_boundary.py` · `README.md` |
| `noise_gate` | `infrastructure/ml_signal_reader.py`(신규) · 컨텍스트 조립 노드 · `infrastructure/feedback_store.py`(G-9) |
| `src` | `src/config.py`(게이트 플래그 · nested config `default_factory`) · `src/api/routes/alarm.py`(피드백 스키마 · G-9) · 대시보드 정적 자산(M4) |
| `mcp_server` | M0 J-2 분기 시에만(분 단위 원천 도구) · DPM 도구는 DPM 계획 소관 |
| 문서 | 데이터 카드 · `docs/26_sre_agent_guide.md` ML 절 · `docs/flag_audit.md` · `CLAUDE.md`(C-6) · `docs/02_decision.md`(D-223 등재 시 · D-118 범위 개정 표기) · `plans/INDEX.md` |
| 운영 | 기동 단위 `sre-agent-ml-batch`(systemd 등) 1건 추가 · `sre_agent/.data/ml/` |

### 8.2 품질 게이트 영향

- `sre_agent/scripts/arch_check.py`에 신규 모듈을 등록한다(`ml_batch`=entry · `domain.ml`=domain · `infrastructure.ml`=infrastructure · `application.ml_*`=application). 외부 패키지 import는 arch_check가 잡지 않으므로 **`domain/ml` 허용목록 스캔 테스트**로 막는다(I-10).
- 루트 `overfit_check.py`는 이미 `sre_agent/domain`을 스캔한다(`plans/91` 1-8). `domain/ml`에 폴스타 리터럴이 들어가면 게이트에 걸린다 → 리터럴은 `config/ml/`·어댑터에만 둔다. **기준선 전면 재생성 금지 · 자기 델타만.**
- 루트 `pytest` 자동 수집 대상에는 변화가 없다(`sre_agent/tests`는 원래 자체 venv 실행).
- `docs/flag_audit.md`에 신규 플래그와 만료일을 등재한다(D-162).

---

## 9. 안전·운영 통제

| 영역 | 통제 |
|---|---|
| 라이선스 | **허용**: BSD · MIT · Apache-2.0 · PSF. **배제**: GPL 계열(결합 배포) · BSL(alibi-detect) · CC-BY-NC 가중치(Moirai) · 비상업 가중치(TimesFM 3.0) · NXAI Community(TiRex 1.x) · 라이선스 미표기 코드(Chain-of-Event·Eadro·RCRank·RUN·DBSherlock는 법무 확인 전 코드 반입 금지 · 방법 재구현은 가능). 버전은 필요에 따라 올릴 수 있으나(사용자 허용) **올릴 때마다 가중치·패키지 라이선스를 다시 확인**한다(TimesFM 2.5→3.0 선례). **v3 — 공개 데이터셋에도 같은 통제를 적용한다**: 경기 데이터(Kaggle Telstra·Bosch·ASHRAE·M5·VSB)는 규칙 동의가 전제이고 비상업·경기 목적 한정이 흔해 **반입 대상이 아니다**. 라이선스 원문을 확인하지 못한 데이터(NAB 등)는 사용 보류다(J-11 · GAP-12) |
| 모델 파일 | skops · ONNX · safetensors만. `torch.load`는 `weights_only=True`에서만. 파일 SHA-256 · 라이선스 문자열 · 학습 구간 · 파라미터를 SQLite 모델 메타에 고정. 로컬 디렉토리 로드 · 런타임 다운로드 금지(`HF_HUB_OFFLINE=1`) |
| 의존 격리 | `sre_agent` venv + extra 분리(C-1). holmes·ML 버전 변경 시 해석 검사 필수(C-4). 해석기가 조용히 구버전을 고르는 경우를 **핀으로 막는다**(`pandas<3` · `statsforecast>=2.1`) |
| 프로세스 격리 | 배치·재적합·평가는 `ml_batch`·`scripts/ml`에서만(C-2 · I-9). `run_service` 온디맨드 분석은 사전수집 타임박스·동시성·메모리 상한 안에서만. ML 예외는 조사 잡을 실패시키지 않는다 |
| 자원 | 배치 스코어러 동시성 상한 · 서버당 호출 타임아웃 · 배치 창 초과 시 다음 주기로 이월 + 지연 지표 발행(누적 금지) · `mcp_server` 호출 속도 상한(운영 DB 부하 · `docs/25` 부하 가드 자세) · J-9로 `run_service`와의 공존 여유 확인 |
| 데이터 | 운영 데이터 외부 LLM 송신 금지(D-120) · 평가 산출물은 서버 내 축소 후 반출(D-219) · note 텍스트 마스킹 후 색인 |
| 과금 | 실 LLM 평가·holmes 상향 e2e는 건별 승인(D-127) · `RUN_E2E=1` 코드 게이트 · 키 존재만으로 실행 금지 |
| 수명 | 신규 플래그 만료일 부여·`docs/flag_audit.md` 등재(D-162). 모델 재적합 주기와 **드리프트 지표**(잔차 분포 이동·구간 커버리지 이탈·**학습 구간 대비 adversarial AUC** v3 · K-4)를 `status` 도구로 노출. 커버리지가 목표를 벗어나면 해당 도구가 `degraded` 사유를 반환 |
| 감사 | ML 도구 호출(표면 ①②③ 공통) 감사 로그(도구·인자·방법 버전·지연). 모델 교체 이력 |
| 읽기 전용 | `sre_agent`는 DB 자격증명이 없고 쓰기 경로가 없다(기존과 동일). Redis 발행은 `ml:*` 네임스페이스로 한정 |

---

## 10. 사용자 확정 게이트

| G | 질문 | 선택지 | 권장 · 근거 |
|---|---|---|---|
| **G-1** | ML 계층을 어디에 구성하는가 (**v2 재검토** — 사용자 지시 *"패키지는 별도가 아니라 sre_agent에 구성하는 것을 검토하라"*) | (가) **`sre_agent` 편입** · 온디맨드는 `run_service` · 배치는 같은 패키지의 별도 엔트리 `ml_batch` (나) `sre_agent` 편입 · 단일 프로세스(배치도 `run_service` 안) (다) 별도 최상위 패키지(v1안) (라) `mcp_server` 내부 | **(가)** — §4.2 비교표. RCA 축 단일 소유(D-197) · 루트와 분리된 venv에서 ML 스택 전체 해석 OK(§3.5.1) · `mcp_tool_client`·인증·감사 재사용 · HolmesGPT in-process 도구셋 실재(0.36 설치본). (나)는 조사 워커와 CPU·메모리를 경쟁하고 배치 장애가 조사 서비스로 번진다. 편입 조건 C-1~C-6을 함께 확정한다 |
| **G-2** | 착수 순서 | (가) M0→M1→M2→M3 폴스타 단독 → M4 → M5(87 이후) → M6(DPM 계획 이후) (나) 제니퍼 연계를 M2와 병행 | **(가)** — 제니퍼 `apm_*`는 코드 0건이고(`plans/87` `-TODO`) 게이트 G-3~G-7이 대기 중이다. 폴스타로 계약·평가 체계를 먼저 굳혀야 교차 계층에서 원인 분리가 된다 |
| **G-3** | DPM 연동 계획 | (가) J-8 확인 뒤 **DPM 전용 계획서 신설**, §5.7을 요구 명세로 넘김 (나) 이 계획에 DPM 커넥터 포함 | **(가)** — 커넥터는 벤더·자격증명·네트워크존 문제라 `plans/87`처럼 독립 계획 규모다. 이 계획이 커지면 소유가 흐려진다(`plans/91` 단일 소유 규칙) |
| **G-4** | 예측 결과 통보 경로 | (가) 대시보드·브리핑 전용 → 섀도 월말 2회 후 재결정 (나) 게이트 이벤트로 투입(TICKET 상한) (다) 즉시 통보 | **(가)** — 알람 폭풍 위험(ICSE-SEIP'20) · 시간 집계 예측의 실증 공백(GAP-2). (나)는 "관측 원천이 아닌 이벤트"의 첫 편입이라 별도 결정이 필요하다 |
| **G-5** | 게이트 ML 신호 반영 수위 | (가) off → shadow → annotate → escalate(§6.3 조건마다 전환) (나) 처음부터 escalate-only | **(가)** — D-048 재현율 우선이지만 알람 예산 영향을 먼저 재야 한다. D-110 E3도 escalate-only였지만 `enable_ai_severity_boost` 뒤에 있다 |
| **G-6** | 벤더 네이티브 AI 결과 편입 | (가) 제니퍼 Anomaly Event·XAIOps 예측을 **입력 신호로 받고 같은 지표를 재계산하지 않음** (나) 받지 않고 전부 자체 계산 | **(가)** — 중복 구현 방지. 단 API 노출이 확인(J-7·J-8)돼야 하며, 미노출이면 자동으로 (나)가 된다 |
| **G-7** | 파운데이션 모델 도입 시점 | (가) M7b 조건 충족까지 보류 (나) M2와 병행해 Chronos-2 small·Toto-2.0-4m 백테스트 착수 | **(가)** — v2에서 라이브러리 제약은 풀렸다(해석 OK). 그러나 **문헌상 탐지 용도 이점이 없고**(P-4), CPU 지연은 미실측이며(GAP-7), 가중치 보안 심사는 여전히 필요하다. 고갈 ETA 정확도가 단계 1에서 부족하다고 실측되면 (나)로 앞당긴다. **[v5 재질의 — G5로 「CPU 지연」 근거가 사라져 권장을 (나′) 「오프라인 백테스트만 병행 · 운영 반영은 M7b 게이트 유지」로 변경 제안 · §R.11]** |
| **G-8** | HolmesGPT 버전 | **허용 확정(2026-09-17 사용자: "버전과 라이브러리는 필요에 따라 수정하거나 사용할 수 있다")** → M1 CU-1에서 0.36.0 → **0.42.x** 상향 | 근거: 0.40.0 command injection·SSRF 수정 · 0.42.0 MCP `structuredContent` · SSE deprecated 대응 여지. ML 스택과의 해석 OK(§3.5.1). 되돌리기는 핀 1줄이다. **실 LLM 회귀만 D-127 승인** |
| **G-9** | 확정 원인 라벨 수집 | (가) `plans/83` 피드백에 이벤트 키·`root_cause` 선택 필드 확장(U-F (ii) "별도 저장소 금지" 준수) (나) 별도 라벨 저장소 | **(가)** — U-F (ii)가 이미 결정됐다. (나)는 그 결정을 다시 여는 것이다 |
| **G-10** | 평가 데이터 처리 위치 | (가) 폐쇄망 서버 내 평가 · 축소 지표만 반출(D-219 준용) (나) 원본 반출 후 개발망 평가 | **(가)** — D-219 · D-120. **[v5] 「서버」 = G5(운영망 연결 VPC) — J-20 통과 전제 · §R.10.5** |
| **G-11**(v2 신설) | 게이트로 ML 신호를 전달하는 방식 | (가) `ml_batch`가 Redis `ml:*` 발행 · 게이트는 키만 읽음 (나) 게이트가 알람마다 `sre_ml_*`를 동기 조회 | **(가)** — `alarm:baseline:*` 계약 선례 · 워커 지연 없음 · `sre_agent` 장애 시 키 만료로 E3 폴백(§4.6). (나)는 알람 처리 지연과 `sre_agent` 가용성이 결합된다 |

**확인 사항(결정이 아니라 사실 확인)**: J-1~J-11(§7 M0). v2에서 J-6은 라이브러리 선택 제약이 아니라 **반입 일정** 확인으로 바뀌었다. v3에서 J-11(공개 데이터 라이선스 원문)이 추가됐다 — M1 착수를 막지 않는다.
**v4는 게이트 G-12(구현 순서 — CPU→WAS 결정적 사슬 선행)·G-13(미들웨어 시그니처 사용 방식)·G-14(프로파일 파서 소유)와 확인 사항 J-12~J-16을 추가한다 — 정본은 §R.7.** G-12 (가)를 택하면 G-2 (가)의 「M5 제니퍼는 87 이후」는 §R.6 순서로 대체된다.
**v5는 G-15(G5 범위 — 사용자 답변으로 확정)·G-16(L3 `jcmd` 허용)·G-17(G5 LLM 서빙 기준)과 J-17~J-20을 추가하고, G-14를 해소(지시로 (가) 확정)하며, G-7을 재질의한다. v6은 G-15에 「G5 = ML 장애 분석 · (다) 혼합 배치」를 확정으로 덧붙이고 J-21을 추가한다 — 정본은 §R.11.**
**v3은 사용자 확정 게이트를 늘리지 않는다.** Kaggle 조사 결과는 전부 (가) 기존 결정의 구체화(F2c 모델 = GBDT · 피처 명세) (나) 평가 규약 추가(누수·임계·adversarial) (다) 사실 확인 항목(J-11)이며, G-1~G-11의 선택지를 바꾸지 않는다.

---

## 11. 리스크

| R | 내용 | 대응 |
|---|---|---|
| R-1 | **시간 집계만 있으면** 탐지·RCA 성능이 문헌보다 크게 낮을 수 있다(GAP-1·GAP-2) | M0에서 해상도 확정 · 해상도별 민감도 보고 · 약속 범위를 추세·ETA·패턴 이탈로 제한(P-5) |
| R-2 | 라벨 부족으로 RCA 평가 n<20 | 리플레이셋 원천 확장(J-3·J-5·J-10) · n<20이면 성능 문구 금지 · 군집당 1회 라벨(iSQUAD) |
| R-3 | 엔티티 정합 실패(호스트↔WAS↔DB) | 신뢰도 `high`만 결합 · 정합률을 데이터 카드에 게시 · 정합 원천은 87/DPM 계획 소관 |
| R-4 | 라이선스 변동(TimesFM 2.5→3.0 사례) | 버전·해시·라이선스 문자열 고정 · 버전 상향 시 재확인(§9) |
| R-5 | 배치 CPU가 운영 DB·서버에 부하 | 호출 속도 상한 · 배치 창 초과 이월 · J-9 실측 |
| R-6 | **ML 신호가 알람 피로를 늘림** | 주석 단계에서 알람 예산 0 증가 확인 · escalate는 섀도 통과 후 · 예측 통보는 G-4 |
| R-7 | **LLM이 ML 순위를 재해석해 사실상 재순위** | `llm_instructions`·지침에 인용 규칙 · 브리핑 결정 필드는 코드가 채움 · ECR 채점으로 감시 |
| R-8 | baseline 세 벌 공존 → 판정 불일치 | §4.6 수렴 경로(1순위 교체 · E3 폴백 유지) · 전환 규칙은 D-161로 같은 번호에 |
| R-9 | 벤더 API 미노출(제니퍼 Anomaly Event · 맥스게이지 API) | G-6 자동 폴백 · J-7·J-8 선확인 |
| R-10 | extra 반입 일정이 길어짐(폐쇄망 행정) | `domain/ml` 순수 Python 구현 · `infrastructure/ml` 폴백(HW 전례) · 1차 스택만 먼저 반입 · J-6 |
| R-11 | holmesgpt 상향(0.36→0.42.x)으로 조사 동작 변화 | CU-1에서 SDK 시그니처 `inspect` 재실측 · 기존 스텁·가드 테스트 전건 · 실 LLM 회귀는 D-127 승인 · 되돌리기는 핀 1줄 |
| R-12 | 공개 벤치마크 과적합(RCAEval은 규칙으로 풀릴 만큼 쉬움) | 공개 데이터는 구현 회귀 전용(§6.2-7) |
| R-13 | 운영자 피드백 편향(상반 라벨 합의 로직 없음 · 83 A9) | 평가셋 빌더가 상반 라벨을 제외·표기 · 라벨 출처 필드 |
| R-14 | 병렬 세션의 동시 편집(계획서·D-번호) | 등재 직전 재실측 · 충돌 시 뒤 번호 재부여 |
| **R-15**(v2) | **조사 서비스와 ML의 장애 결합**(같은 패키지) — 배치 메모리 폭주·예외가 조사를 멈춤 | 배치 별도 프로세스(C-2) · 온디맨드 타임박스 · ML 예외 격리 테스트(M3 완료 판정) |
| **R-16**(v2) | **holmesgpt 정확 핀과 ML 의존의 충돌** — 해석기가 조용히 구버전을 고른다(drain3 0.9.1 · statsforecast 2.0.1 실측) | 해석 검사 필수(C-4) · 핀 고정 · drain3 미사용(Drain 자체 구현) |
| **R-17**(v2) | `sre_agent` venv 비대화(torch·transformers 수 GB) → 조사 서비스 배포 부담 | extra 분리(C-1) · `ml-tsfm`·`ml-embed`는 해당 단계에서만 설치 · 조사 서비스 노드와 `ml_batch` 노드를 분리 배치할 수 있게 엔트리를 분리해 둔다. **[v5] GPU 스택은 G5에만 — 해소 방향 §R.10.2** |
| **R-18**(v2) | D-118 정체성 확장이 향후 "sre_agent 별도 프로젝트 분리" 시 범위를 키움 | ML이 함께 이동하도록 경로·설정을 패키지 루트 안에만 둔다(`config/ml`·`.data/ml`) · 루트 `config/` 참조 금지 |
| **R-19**(v3) | **누수로 부풀려진 사내 평가가 채택 게이트를 그대로 통과한다.** 경기에서 반복 확인된 형태다 — Telstra 행 번호, Bosch ID 차이(실배포엔 제거 필요로 명시), ASHRAE test 누수. 우리 데이터에는 **사후 기입 원인 텍스트**라는 더 강한 누수원이 있다 | 누수 감사기 통과를 채택 게이트 전제로 한다(규약 11) · 피처/라벨 분류표 · 사건 group ∧ purged 분할(KL-4) · 리플레이–섀도 관계 확인(규약 12) |
| **R-20**(v3) | **공개·합성 데이터 성적을 사내 기대치로 오인한다.** Kaggle의 IT 모니터링 데이터는 대부분 합성이거나 연구 데이터 재업로드다(K-8 · GAP-11) | 규약 7 확장 — 공개 데이터는 구현 회귀·누수 점검 전용. D-174와 같은 통제 계열 |
| **R-21**(v3) | **"GBDT로 확정"이 의존 추가 요구로 샌다**(lightgbm·xgboost·catboost 반입) | 1차는 sklearn `HistGradientBoostingClassifier`로 **신규 의존 0**. 추가는 리플레이셋 우위가 신뢰구간 밖일 때만 하고 해석 검사(C-4)를 다시 돌린다(R-16과 같은 경로) |

---

## 12. 하지 않는 것 · 보고

**하지 않는 것**
- 자동 조치·복구 실행(D-003 · `plans/64` B-3).
- LLM에 순위·판정 권한 부여(P-22·P-23) · 원시 시계열을 LLM에 직접 투입.
- 운영 경로의 자동 인과 발견(PC/Granger/PCMCI)과 트레이스 필수·딥 멀티모달 RCA 모델(P-17·P-18).
- 딥러닝 이상탐지기(Transformer·VAE 계열) 운영 투입(P-1).
- 비상업·조건부 라이선스 가중치 반입(P-15) · 외부 SaaS 연동.
- 공개 벤치마크·개발 측정치를 사내 성능 기대치로 인용(P-12 · D-174).
- 제니퍼·DPM 커넥터 구현(각 계획 소관) · 별도 라벨 저장소 신설(U-F (ii)).
- **별도 최상위 ML 패키지 신설(v1안) — v2에서 `sre_agent` 편입으로 대체**(§4.2).
- `run_service` 프로세스 안에서의 배치 스코어링·재적합(C-2).
- 사용자 승인 없는 실 LLM 호출(D-127).
- **(v3) 스태킹·다중 시드 앙상블로 지표를 쥐어짜는 것**(K-9 — 경기 실측 이득이 소수점 셋째 자리이고, 운영에서는 해석성·지연·모델 관리 비용이 그보다 크다).
- **(v3) 누수 피처를 쓰는 것**: 행 index·정렬 위치·단조 ID 차이·사후 기입 필드·미래 창 집계·분할 전 재표본(KL-1~KL-6). 사내 점수가 올라가도 쓰지 않는다.
- **(v3) Kaggle 경기 데이터의 폐쇄망 반입**(경기 규칙 동의·비상업 한정 · GAP-12) · 라이선스 원문을 확인하지 못한 공개 데이터의 사용(J-11).
- **(v3) 경기·합성 데이터 성적을 사내 기대치로 인용하는 것**(K-8 · 규약 7 · D-174).

**보고(이 계획이 수정하지 않은 문서 정정 대상)**
- `plans/62` L113 "Plan 63(예측)은 별도 작성됨"은 사실과 다르다 → C5 소유는 `plans/101`이다.
- `plans/85` L370 "63 예측 슬롯" 표기도 같다.
- `plans/55` L216 "D-043 등재 예정"은 무효다(D-043은 다른 결정).
- `sre_agent` 실 조사 실행 조건이 `gemini_api_key`에 묶여 있다(§2.1 관찰) — 의도 여부 확인 필요. holmes 상향(CU-1) 때 함께 점검하는 것이 자연스럽다.

---

## 13. 신규 결정 예약 — D-223

**상태**: 예약(「채번 이력」 표 등재). 게이트 G-1·G-2·G-4·G-5·G-9·G-11 확정 시 본문 등재한다. **D-118(`sre_agent` 범위) 개정을 포함**한다.

**결정 초안**
1. **역할 경계**: ML은 정량 증거 생산자다. 판정은 결정적 규칙이 하고, LLM은 인용 서술만 한다. **LLM 재순위 금지**가 기본값이다. 모든 점수는 상대 순위로 표기하고, 확률은 conformal 구간에만 쓴다.
2. **배치(v2)**: ML 계층은 **`sre_agent` 안에 구성**한다.
   - 온디맨드 분석은 조사 서비스(`run_service`)의 결정적 사전수집이 **함수 호출**로 쓴다. HolmesGPT에는 **in-process 도구셋**(`additional_toolsets`)으로 공급한다.
   - 외부 pull은 `sre_ml_*` MCP 도구로 한다.
   - 배치 스코어링은 같은 패키지의 **별도 엔트리 프로세스 `ml_batch`**가 맡고, Redis `ml:*`로 게이트에 공급한다.
   - 관측 데이터는 `mcp_server`로만 읽는다(D-119). `src`·`noise_gate`·`mcp_server`와의 양방향 import는 0이다(D-118 불변식 유지).
   - **D-118 개정**: `sre_agent` 범위를 "HolmesGPT 조사"에서 "장애 조사·진단·예측(HolmesGPT + 결정적 분석·ML)"으로 넓힌다. 편입 조건 C-1~C-6(extra 분리 · 배치 프로세스 분리 · `domain/ml` 표준 라이브러리 · 해석 검사 · skip 금지 · 문서 갱신)을 따른다.
   - **(v5·v6) 실행 노드**: 엔트리 `ml_service`·`ml_batch`는 **AWS G5(운영망 연결 VPC)**에서 돈다. **G5의 목적은 ML 장애 분석이다(v6)** — `ml_service`는 장애 분석 API(GA-1~GA-6)를 내고, `ml_batch`는 그 기준선·모델 학습과 배치를 맡는다. `run_service`는 기존 호스트에서 결정적 수집·판정 필수 경량 통계·판정을 맡는다. S5 필수 조건은 `run_service` 출력만 읽는다(I-11). G5에는 DB·제니퍼 자격증명이 없다(MCP 토큰만 · GA-1·GA-2만 사건 범위로 MCP 직접 조회 · 나머지 입력은 `run_service`가 실어 보낸다). G5 장애 시 경량 짝 폴백과 GA별 사유 표기는 의무다. LLM 서빙은 부차다 — 기본 FabriX이고, 툴 호출 품질 기준 미달이 실측될 때만 ML 인스턴스와 별도인 G5 인스턴스에 vLLM을 둔다(G-17).
3. **버전·라이브러리 정책**(사용자 허용 2026-09-17): 버전과 라이브러리는 필요에 따라 올리거나 추가한다. 선택 기준은 문헌 성능·라이선스·유지보수·해석 가능성이다. 변경마다 라이선스 재확인과 해석 검사를 거친다. holmesgpt는 0.42.x로 상향한다.
4. **알고리즘 채택 순서**
   - 탐지: 강건 통계(STL+MAD·변화점) → (보조 후보) matrix profile → 경량 ML(PCA·IForest)
   - 예측: **다중 창 median·Seasonal Naive 기준선** → 추세+conformal ETA → 분위수별 경험적 구간 → 라이선스 허용 파운데이션 모델(예측 전용)
   - **사건 위험(라벨 조건부): 모델을 GBDT로 고정한다** — 1차는 scikit-learn `HistGradientBoostingClassifier`로 신규 의존 0이며, 다른 GBDT 구현 추가는 리플레이셋 우위가 신뢰구간 밖일 때만 한다. 피처는 4계열(다중 창 롤링 · 템플릿·건수 집계 · 엔티티 인코딩 · 마지막 이벤트 이후 경과)로 명세한다. **딥 표 모델·스태킹·다중 시드 앙상블은 하지 않는다**(v3 · K-1·K-2·K-9)
   - RCA: 규칙·이벤트 그래프·강건 점수 → 계층별 분해(Squeeze·ADDM·iSQUAD) → 지식 스켈레톤(CIRCA)
   - **자동 인과 발견·딥 멀티모달·딥 탐지기는 운영 경로 회피**
5. **평가 계약**: PA 금지 · VUS-PR · 이벤트 단위·알람 예산 · RCA AC@k 계층·존별 분리 · 의무 기준선 · n<20 문구 금지 · 섀도 월말 2회 · 공개 벤치 수치 인용 금지. **v3 추가**: **누수 금지 6종(KL-1~KL-6)을 기계 검사**하고 통과하지 못한 결과는 채택 게이트 입력으로 쓰지 않는다 · 평가 보고서에 **피처/라벨 분류표** 필수 · 분할은 **사건 group ∧ 시간순(purged)** 동시 만족 · **임계·컷오프는 학습 구간에서 고정**(테스트·섀도 재조정 금지) · **adversarial AUC ≥ 0.7인 분할의 성적 불채택** · **리플레이셋 점수와 섀도 성적을 쌍으로 기록**하고 리플레이 최고점 단독 채택 금지 · 경기·합성 데이터 수치도 인용 금지.
6. **반입 계약**: 허용 라이선스 목록 · skops/ONNX/safetensors · pickle 금지 · 해시·라이선스 고정 · 로컬 로드.
7. **결정 경로 전환**: 게이트 신호(off→shadow→annotate→escalate) · 가설 병합 · 예측 통보는 §6.3 조건을 충족할 때만 연다. baseline 수렴 시 1순위 교체와 E3 폴백 규칙을 같은 번호에 넣는다(D-161).

**대안 기각(초안)**
- 별도 최상위 패키지(v1안): 한 기능(RCA)이 두 패키지로 갈리고, 데이터 클라이언트가 중복된다. v1의 의존 격리 근거는 `sre_agent` venv로 충족된다.
- `mcp_server` 내 ML: 루트 venv 충돌 · 읽기 경계 혼합.
- `noise_gate` in-process: 자원 경쟁 · 계약 이원화.
- `sre_agent` 단일 프로세스(배치 포함): 조사 서비스와 장애가 결합된다.
- 딥러닝 우선: 문헌 반증(P-1·P-18).
- LLM 재순위 기본 허용: 동료심사 근거가 없고 D-035와 충돌한다.

## 14. 참고 문헌

> 전체 서지·검증 등급·적합도 판정은 동반 조사 문서 3종이 정본이다. 여기에는 **이 계획의 설계를 바꾼 문헌**만 싣는다. 인용수는 싣지 않는다(OpenAlex AI 분야 과소집계). 기존 dossier(`docs/aiops_benchmark/incident_investigation_literature.md`)와 `plans/87` §12에 있는 문헌은 재조사하지 않고 인용만 했다.

### 14.1 동료심사

| 문헌 | 게재 | 식별자 | 등급 | 반영 |
|---|---|---|---|---|
| Liu & Paparrizos, The Elephant in the Room: Towards A Reliable Time-Series Anomaly Detection Benchmark (TSB-AD) | NeurIPS 2024 D&B | 10.52202/079017-3437 | V1 | P-1·P-4·P-7 |
| Sarfraz et al., Position: Quo Vadis, Unsupervised Time Series Anomaly Detection? | ICML 2024 | arXiv 2405.02678 | V1 | P-1·P-9 |
| Schmidl, Wenig, Papenbrock, Anomaly Detection in Time Series: A Comprehensive Evaluation | PVLDB 15(9) 2022 | 10.14778/3538598.3538602 | V1 | P-1 |
| Wu & Keogh, Current Time Series Anomaly Detection Benchmarks are Flawed… | IEEE TKDE | 10.1109/TKDE.2021.3112126 | V1 | P-6 · §5.1 결함 점검 |
| Kim et al., Towards a Rigorous Evaluation of Time-Series Anomaly Detection | AAAI 2022 | 10.1609/aaai.v36i7.20680 | V1 | P-6 |
| Paparrizos et al., TSB-UAD · Volume Under the Surface (VUS) | PVLDB 2022 | 10.14778/3529337.3529354 · 10.14778/3551793.3551830 | V2 | P-7 |
| Salfner, Lenk, Malek, A Survey of Online Failure Prediction Methods | ACM CSUR 2010 | 10.1145/1670679.1670680 | V2 | P-8 |
| Notaro et al., A Survey of AIOps Methods for Failure Management | ACM TIST 2021 | 10.1145/3483424 | V2 | §1.1 분류 |
| Ren et al., Time-Series Anomaly Detection Service at Microsoft (SR-CNN) | KDD 2019 | 10.1145/3292500.3330680 | V1 | M7a 보조 후보 |
| Liu et al., Opprentice | IMC 2015 | 10.1145/2815675.2815679 | V2 | M7a 탐지기 선택 |
| Wen et al., RobustSTL | AAAI 2019 | 10.1609/aaai.v33i01.33015409 | V2 | P-2 |
| Truong et al., Selective review of offline change point detection methods | Signal Processing 2020 | 10.1016/j.sigpro.2019.107299 | V2 | §5.2 변화점 |
| Xu & Xie, Conformal prediction interval for dynamic time-series (EnbPI) | ICML 2021 | PMLR 139 | V2 | P-13 |
| Gibbs & Candès, Adaptive Conformal Inference Under Distribution Shift | NeurIPS 2021 | — | V2 | P-13 |
| Hyndman & Khandakar, Automatic Time Series Forecasting: the forecast package | JSS 2008 | 10.18637/jss.v027.i03 | V2 | P-14 기준선 |
| Taylor & Letham, Forecasting at Scale (Prophet) | The American Statistician 2018 | 10.1080/00031305.2017.1380080 | V2 | P-3 달력 |
| Ansari et al., Chronos | TMLR 2024 | arXiv 2403.07815 | V2 | P-15 |
| Das et al., A decoder-only foundation model for time-series forecasting (TimesFM) | ICML 2024 | PMLR 235 | V2 | P-15 |
| Ekambaram et al., Tiny Time Mixers (TTM) | NeurIPS 2024 | arXiv 2401.03955 | V1 | P-15 CPU 하한 |
| Woo et al., Moirai · Liu et al., Moirai-MoE | ICML 2024 · 2025 | PMLR 235 · 267 | V2 | P-15 **배제(가중치 CC-BY-NC)** |
| Auer et al., TiRex | NeurIPS 2025 | arXiv 2505.23719 | V1 | P-15 **배제(NXAI 라이선스)** |
| Zhao et al., Real-Time Incident Prediction for Online Service Systems (eWarn) | ESEC/FSE 2020 | 10.1145/3368089.3409672 | V1 | P-16 · M7c |
| Chen et al., Outage Prediction and Diagnosis for Cloud Service Systems (AirAlert) | WWW 2019 | 10.1145/3308558.3313501 | V1 | P-9 · P-16 |
| Lin et al., Predicting Node Failure in Cloud Service Systems (MING) | ESEC/FSE 2018 | 10.1145/3236024.3236060 | V2 | M7c 비용 민감 임계 |
| Xu et al., Improving Service Availability of Cloud Systems by Predicting Disk Error (CDEF) | USENIX ATC 2018 | — | V1 | M7c |
| Huang et al., Gray Failure: The Achilles' Heel of Cloud-Scale Systems | HotOS 2017 | 10.1145/3102980.3103005 | V2 | P-21 관측 불일치 |
| He et al., Drain | IEEE ICWS 2017 | 10.1109/ICWS.2017.13 | V2 | M7c 템플릿 |
| Le & Zhang, Log-based Anomaly Detection with Deep Learning: How Far Are We? | ICSE 2022 | 10.1145/3510003.3510155 | V1 | 로그 DL 회피 |
| Pham, Ha, Zhang, Root Cause Analysis for Microservices based on Causal Inference: How Far Are We? | ASE 2024 | 10.1145/3691620.3695065 | V1 | P-17 |
| Fang et al., Rethinking the Evaluation of Microservice RCA with a Fault Propagation-Aware Benchmark | FSE 2026 (PACMSE) | 10.1145/3797100 | V1 | P-18 |
| Pham et al., RCAEval | WWW 2025 Companion | 10.1145/3701716.3715290 | V1 | §6.2-7 구현 회귀 |
| Hardt et al., The PetShop Dataset | CLeaR 2024 | PMLR v236 | V2 | GAP-1 · 5분 해상도 검증 |
| Pham, Ha, Zhang, BARO | FSE 2024 | 10.1145/3660805 | V1 | P-18 · R2 기준선 |
| Li et al., Causal Inference-Based RCA … with Intervention Recognition (CIRCA) | KDD 2022 | 10.1145/3534678.3539041 | V1 | P-21 · M7d |
| Budhathoki et al., Causal structure-based root cause analysis of outliers | ICML 2022 | PMLR 162 | V2 | M7d DoWhy-GCM |
| Blöbaum et al., DoWhy-GCM | JMLR 25 2024 | arXiv 2206.06821 | V2 | M7d |
| Runge et al., PCMCI | Science Advances 2019 | 10.1126/sciadv.aau4996 | V2 | P-17 **회피** |
| Wu et al., MicroRCA | NOMS 2020 | 10.1109/NOMS47738.2020.9110353 | V2 | P-21 이중 노드 |
| Harsh et al., Murphy | SIGCOMM 2023 | 10.1145/3603269.3604877 | V2 | §5.4 순환 의존 |
| Wang et al., Groot: An Event-graph-based Approach for RCA in Industrial Settings | ASE 2021 | 10.1109/ASE51524.2021.9678708 | V1 | P-19 · R1 |
| Yao et al., Chain-of-Event | FSE 2024 Industry | 10.1145/3663529.3663827 | V2 | M7 라벨 축적 후 |
| Li et al., DejaVu | ESEC/FSE 2022 | 10.1145/3540250.3549092 | V2 | M7 반복 장애 |
| Dias et al., Automatic Performance Diagnosis and Tuning in Oracle (ADDM) | CIDR 2005 | cidrdb P07 | V2 | P-21 · M6 |
| Yoon, Niu, Mozafari, DBSherlock | SIGMOD 2016 | 10.1145/2882903.2915218 | V2(수치 V3) | 확정 원인 재사용 구조 |
| Ma et al., iSQUAD | PVLDB 2020 | 10.14778/3389133.3389136 | V1 | P-21 · M6 |
| Liu et al., PinSQL | ICDE 2022 | 10.1109/ICDE53745.2022.00236 | V2 | P-21 · M6 |
| Zhou et al., D-Bot: Database Diagnosis System using LLMs | PVLDB 2024 | 10.14778/3675034.3675043 | V1 | P-22 |
| Singh et al., Panda | CIDR 2024 | — | V2 | LLM 근거·검증 원칙 |
| Bhagwan et al., Adtributor | NSDI 2014 | — | V1 | P-21 · M5 |
| Lin et al., iDice · Zhang et al., HALO | ICSE 2016 · KDD 2021 | 10.1145/2884781.2884795 · 10.1145/3447548.3467190 | V1 | P-21 · M5 |
| Li et al., Squeeze · PSqueeze | ISSRE 2019 · JSS 2023 | 10.1109/ISSRE.2019.00015 · 10.1016/j.jss.2023.111748 | V2·V1 | P-21 · M5 |
| Zhang et al., FUNNEL | CoNEXT 2015 | 10.1145/2716281.2836087 | V2(메커니즘 V3) | P-20 DiD |
| Li et al., Gandalf | NSDI 2020 | USENIX | V1 | P-20 |
| Zhao et al., SCWarn | ESEC/FSE 2021 | 10.1145/3468264.3468543 | V1 | P-20(은행 50.4%) |
| Zhao et al., Understanding and Handling Alert Storm | ICSE-SEIP 2020 | 10.1145/3377813.3381363 | V1 | P-21 · G-4 |
| Kuang et al., COLA | ICSE-SEIP 2024 | 10.1145/3639477.3639745 | V1 | P-21 하이브리드 |
| Saha & Hoi, Mining Root Cause Knowledge from Cloud Service Incident Investigations (ICA) | ICSE-SEIP 2022 | 10.1145/3510457.3513030 | V1 | F4 · J-5 |
| Pool et al., Lumos | KDD 2020 | 10.1145/3394486.3403306 | V1 | 변경 후 회귀 판정 |
| Xu et al., OpenRCA | ICLR 2025 | OpenReview M4qNIzQYpd | V1 | P-22 |
| Jha et al., ITBench | ICML 2025 | PMLR v267 | V1 | P-22 |
| Chen et al., AIOpsLab | MLSys 2025 | arXiv 2501.06706 | V1 | 평가 하네스 참고 |
| Wang et al., RCAgent | CIKM 2024 | 10.1145/3627673.3680016 | V1 | 자체 호스팅 선례 |
| Pei et al., Flow-of-Action | WWW 2025 Companion | 10.1145/3701716.3715225 | V1 | P-22 · SOP 제약 |
| Soldani & Brogi, Anomaly Detection and Failure RCA in (Micro)Service-Based Cloud Applications: A Survey | ACM CSUR 2022 | 10.1145/3501297 | V2 | 분류 |
| Zhang et al., Failure Diagnosis in Microservice Systems: A Comprehensive Survey | ACM TOSEM 2025 | 10.1145/3715005 | V2 | 데이터셋 색인 |
| Chen et al., RCACopilot (기존 dossier) | EuroSys 2024 | 10.1145/3627703.3629553 | (기존) | F4 시간 감쇠 |
| **(v3)** Grinsztajn, Oyallon, Varoquaux, Why do tree-based models still outperform deep learning on typical tabular data? | NeurIPS 2022 Datasets & Benchmarks | proceedings.neurips.cc(2022 D&B) | V2 | **K-1** — F2c 모델을 GBDT로 확정 |
| **(v3)** Ali et al., A Comprehensive Study of Machine Learning Techniques for Log-Based Anomaly Detection | Empirical Software Engineering 2025 | arXiv 2307.16714 | V2 | 로그 축의 P-1 보강(전통 ML ≈ 딥 ML · 준지도는 열위) |
| **(v3)** Bojer & Meldgaard, Kaggle forecasting competitions: An overlooked learning opportunity | Int. J. Forecasting 2021 | arXiv 2009.07701 | V2 | §3.8 배경(GBDT·신경망 병존 · 전역 앙상블 우세 · Kaggle 데이터의 높은 간헐성) |
| **(v3)** Miller et al., The ASHRAE Great Energy Predictor III competition: Overview and results | Science and Technology for the Built Environment 26(10), 2020 | arXiv 2007.06933 | V3 | **K-1·K-2**(대규모 다-엔티티 시계열에서 GBDT 앙상블 + 전처리) |
| **(v3)** Makridakis, Spiliotis, Assimakopoulos, The M5 Accuracy competition / The M5 uncertainty competition | Int. J. Forecasting 2022 | DOI 미확인(V3 · 원문 대조 필요) | V3 | **K-1·KC-05**(상위 전원 순수 ML · 분위수별 별도 모델 + 경험적 구간) |

### 14.2 Preprint (보조 근거 — 채택 근거는 사내 재현으로 보강)

| 문헌 | arXiv | 반영 |
|---|---|---|
| Ansari et al., Chronos-2: From Univariate to Universal Forecasting | 2510.15821 | P-15 후보 1순위 |
| Cohen et al., This Time is Different (Toto 1.0 + BOOM) · Khwaja et al., Toto 2.0 | 2505.14766 · 2605.20119 | P-14·P-15 |
| Aksu et al., GIFT-Eval (NeurIPS 2024 **워크숍**) | 2410.10393 | P-12 오염 주의 |
| Uray et al., Exploring Zero-Shot Foundation Models for Multivariate TSAD | 2607.12454 | P-4 |
| Pham, Graph-Free Root Cause Analysis (PRISM) | 2601.21359 | M5 실험 트랙 |
| Pham et al., TORAI (arXiv 코멘트상 FSE 2026 채택 · 출판본 미확인) | 2604.13522 | 관측 사각지대 참고 |
| Hu et al., Pooled Leaderboards Hide System-Specific Winners | 2606.29159 | P-10 |
| Tian et al., GALA | 2508.12472 | P-23 |
| Jiang et al., KRCA | 2607.01788 | P-23 · 스켈레톤 사전 |
| Jha et al., Think Locally, Explain Globally (EoG) | 2601.17915 | P-24 · 오케스트레이션 원칙 |
| Wang et al., Cloud-OpsBench | 2603.00468 | P-24 ECR |
| Hochenbaum et al., Automatic Anomaly Detection in the Cloud Via Statistical Learning (S-H-ESD) | 1704.07706 | P-2 |
| Adams & MacKay, Bayesian Online Changepoint Detection | 0710.3742 | §5.2 |
| **(v3)** Miller et al., Gradient boosting machines and careful pre-processing work best (GEPIII lessons learned) | 2202.02898 | K-1·K-2 |
| **(v3)** Kaggle Chronicles: 15 Years of Competitions | 2511.06304 | §3.8 배경(플랫폼 계량 · 정량 표는 초록 수준 미확인) |
| **(v3)** Managing dataset shift by adversarial validation for credit scoring | 2112.10078 | K-4 |

> ※ TimeRCD(arXiv 2509.21190)는 ICML 2026 포스터 페이지가 있으나 arXiv v5에 "withdrawn" 표기가 있어 근거에서 제외했다.

### 14.3 산업 자료 · 구현체 (벤더 공개 서술 — 독립 검증 아님)

- 라이브러리 실측(PyPI·GitHub·HF API, 2026-09-17): pyod · river · ruptures · stumpy · scikit-learn · statsmodels · statsforecast · dowhy · causal-learn · tigramite(GPL-3.0) · alibi-detect(BSL 1.1) · Merlion(archived) · PyRCA · RCAEval · fse-baro · CIRCA · PSqueeze · drain3 · skops · onnxruntime · chronos-forecasting · timesfm · toto · uni2ts · granite-tsfm · tirex — 상세는 `ml_library_vendor_survey.md` §1
- HolmesGPT: 릴리스 0.42.0(2026-09-16) · docs `remote-mcp-servers.md`(streamable-http 권장·SSE deprecated) · `skills.md` · `context-management.md` · PR #2459(`structuredContent`) · 0.40.0 보안 수정
- 제니퍼: 인사이트 블로그(2025-12-22 · 2026-01-26) · 설치 가이드 AI/MCP 장 · OpenAPI 5.6.4 스펙 — `plans/87` §12.3 [J-4]·[J-8]·[J-17]·[J-22]와 같은 출처
- 엑셈 XAIOps·MaxGauge: 공식 AIOps 페이지 · 기사(2023-11 · 2025-03 · 2026-04) — 알고리즘·API 비공개
- 와탭 `whatap-open-mcp`(MIT) `whatap_apm_anomaly`
- Grafana Cloud ML(Outlier: DBSCAN/MAD · Forecasting · Sift) · Grafana augurs · Datadog Anomaly Monitor(basic/agile/robust · 3× 계절 이력) · Elastic ML 알고리즘 문서
- Azure AI Anomaly Detector 퇴역 공지(2026-10-01)
- (v2 내부 실측) holmesgpt 0.36.0 설치본 `holmes/config.py:152`·`core/toolset_manager.py:195-198`·`core/tools.py:183,284,719` · PyPI `requires_dist`(holmesgpt 0.36.0/0.42.0 · drain3 0.9.1/0.9.11 · statsforecast 2.0.1/2.1.1) · `uv pip compile` 해석 결과(§3.5.1)
- 데이터셋: RCAEval(MIT) · PetShop(Apache-2.0 · archived) · TSB-AD · BOOM(Apache-2.0) · GIFT-Eval · AIOps Challenge(비상업 조건) · LogHub(연구용)
- **(v3) Kaggle 조사(2026-09-20)**: 경기 페이지 8건(`telstra-recruiting-network`·`bosch-production-line-performance`·`ashrae-energy-prediction`·`m5-forecasting-accuracy`·`m5-forecasting-uncertainty`·`web-traffic-time-series-forecasting`·`vsb-power-line-fault-detection`·`playground-series-s3e17`) · 데이터셋 페이지 8건(Azure PM·AI4I 2020·NAB·SMD·Backblaze·Loghub·모니터링 로그 업로드 계열·NASA C-MAPSS) · 해법·분석 글(gereleth Telstra writeup **V2** · Arturus 1위 저장소 · NVIDIA *Kaggle Grandmasters Playbook* **V2** · Playground S6E2 1위 writeup · Zak Jost adversarial validation · KDD Cup 2021 5위 해법 · 비판 측 *Kaggle Folklore Is Not Data Science*). **Kaggle 페이지 본문은 JS 렌더링으로 정적 확인 불가 → 경기 메타·라이선스는 대부분 V3**(J-11 · GAP-12). 상세는 `ml_kaggle_competition_survey.md` §8

### 14.4 내부 참조

`plans/50` §0.8 · `plans/51` §5 · `plans/53` Wave 5 · `plans/55` C-1·M0~M5 · `plans/60` E3·§13·§15 · `plans/62` §5.1 · `plans/64` §8.3 · `plans/82` §17.3 · `plans/83` A3·A6·A9 · `plans/87` §2.2·§5.2·§5.3·§12 · `plans/91` 1-8·1-9·1-16·1-17 · `plans/92` §0.2·§4.6(v3 보류 — 로컬 이력 원천 부재)·§0.0.4 · `plans/95` G-8 · `docs/aiops_benchmark/incident_investigation_literature.md` · `docs/aiops_benchmark/noise_cancellation_literature.csv` · **`docs/aiops_benchmark/ml_kaggle_competition_survey.md`(v3 신설 — `K-*`·`KC-*`·`KD-*`·`KL-*`·`KG-*` 정본)** · `docs/flag_audit.md` · `docs/18_known_mistakes.md`

### 14.5 조사에서 확인한 공백

§3.7 표(GAP-1~**GAP-12** · v3에서 11·12 추가)가 정본이다. 경기 조사가 메우지 못한 것은 §3.8.2(KG-1~KG-5)에 따로 적었다.

---

## 15. 변경 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| v1 | 2026-09-17 | 최초 작성. 코드·계획 실측(읽기 전용 에이전트 2) + 문헌 조사(RCA 49+22건 · 이상탐지·예측 60항목 · 라이브러리·HolmesGPT·벤더) 종합. `plans/62` §5.1 C5 슬롯 인수. D-223 예약. 동반 조사 문서 3종을 `docs/aiops_benchmark/`에 저장 |
| v2 | 2026-09-17 | 사용자 지시(*"버전과 라이브러리는 필요에 따라 수정하거나 사용할 수 있다. 패키지는 별도가 아니라 sre_agent에 구성하는 것을 검토하라"*) 반영. ①**배치 변경**: 별도 최상위 `fault_ml/` → **`sre_agent` 편입**(§4.2 비교표 · 편입 조건 C-1~C-6 · 불변식 I-9·I-10 신설 · 배치는 같은 패키지의 별도 엔트리 `ml_batch`) ②**실측 3건**: holmes 0.36.0 설치본에 in-process `additional_toolsets` 실재(`enabled` 기본 False 주의) · `sre_agent` venv(py3.13) ML 스택 전체 의존성 해석 OK(holmes 0.36·0.42 모두 · macOS arm64 포함) · **충돌 1건**(drain3 0.9.11 `cachetools==4.2.1` vs holmesgpt `<6,>=5.5`)과 **조용한 하향 2건**(drain3 0.9.1 · statsforecast 2.0.1) → `pandas<3` 고정 · Drain 자체 구현 · 해석 검사 필수(C-4) ③**라이브러리 기준 변경**: 반입 부담을 선택 순위에서 제외(§3.5) · F4 1차 TF-IDF ④G-8 holmesgpt 0.42.x 상향 **허용 확정** · G-11(게이트 신호 전달 방식) 신설 · J-6 의미 변경 ⑤리스크 R-15~R-18 추가 ⑥공백 ID를 게이트 ID와 구분하려고 `G-n` → `GAP-n`으로 변경 |
| v3 | 2026-09-20 | 사용자 지시(*"모니터링 정보와 로그 정보를 이용하여 ml 을 통해 장애를 진단하는 알고리즘을 캐글에서 심도 있게 찾아보고 분석하여 101번의 계획을 업데이트하라"*) 반영. **동반 조사 문서 4종으로 확장** — `ml_kaggle_competition_survey.md` 신설(경기 10건 `KC-01~KC-10` · 데이터셋 8계열 `KD-01~KD-08` · 누수 6종 `KL-1~KL-6` · 공백 `KG-1~KG-5`). ①**§3.8 신설**: 경기 증거를 구현 공학 원칙 `K-1~K-10`으로 정리 ②**§3.8.1 누수 금지 목록을 1급 제약으로 승격** — 경기 상위 해법의 "마법 피처"가 대부분 누수라는 실측(Telstra 그룹 내 행 번호 · Bosch `mindate_id_diff`가 "실배포엔 제거 필요"로 명시 · ASHRAE test 누수)에 근거하며, 우리 데이터의 최대 누수원은 **사후 기입 원인 텍스트**(`alarmcause`·`resolution`·ack)로 확정 ③**§5.3 (c) 구체화**: 모델을 **GBDT로 고정**(1차 sklearn `HistGradientBoostingClassifier` — 신규 의존 0) · 피처를 경기 검증된 4계열로 명세 · **v2 결정과 어긋나 남아 있던 "drain3 템플릿화" 표기를 "Drain 자체 구현"으로 정정** ④**§6.2 규약 11~13 신설**(누수 감사기 통과 전제 · 리플레이–섀도 관계 기록 · 임계 학습 구간 고정 + adversarial AUC 0.7 기준) ⑤§3.2 P-9 의무 기준선에 **다중 창 median**·기본 설정 GBDT 추가, §6.1에 **MCC·임계 민감도** 추가, §5.2에 **matrix profile 보조 후보** 추가, §5.3 (b)에 **분위수별 경험적 구간 선행 기준선** 추가 ⑥§5.1 **누수 감사기**(`scripts/ml/leakage_audit.py`) 산출물 신설 · M1 CU-4 편입 ⑦GAP-11(시간 집계 실데이터 대조군 부재)·GAP-12(공개 데이터 라이선스 미확인) · J-11(라이선스 원문 확인) · R-19~R-21 추가 ⑧§9 라이선스 통제를 공개 데이터셋까지 확장, §12 「하지 않는 것」 4건 추가. **사용자 확정 게이트는 늘지 않았다**(G-1~G-11 불변). 코드 0건 유지 · 데이터 내려받기 0건 · 패키지 설치 0건 |
| v3.1 | 2026-09-22 | 교차 정정(`plans/92` v3 재검토 후속 · 사용자 지시 *"고치지 않은 것들을 권고에 맞게 모두 수정하라"*). §5.6 매트릭스 Prometheus 행의 선행 조건을 `plans/92` 트랙 A에서 **`prom_metric_range`(PromQL · 운영 `PROMETHEUS_URL` — `plans/91` 1-12)**로 바꿨다. 트랙 A(`om_*`)는 exporter 현재값만 주므로 분 해상도 이력 입력이 될 수 없다. §14 참조의 `plans/92` §4.6에 「v3 보류」 표기를 붙였다. 게이트·범위 변경 없음 · 코드 0건 유지 |
| v4 | 2026-10-13 | 사용자 지시(*"어떤 데이터로 어떻게 분석할지 … cpu가 100%일때 … WAS일때 제니퍼에서 어떤 컨테이너나 어떤 함수가 문제가 되는지 … 어떻게 해결할지 … 자세한 프로세스를 다시 정리하라"*) 반영. **§R 신설(실행 정본)**: ①**현행 재실측**(브랜치 `RCA` · `376d3be`) — `apm_*` 24종·PID→인스턴스(`/api-v2/manage/instance?processId=`)·`was_signals` 8종·WAS 조치 표·프로세스 스냅샷·L3 명령 수집이 이미 있고, 끊긴 곳 X-1~X-7(스냅샷 미전달 · 분기를 LLM이 선택 · 프로파일 텍스트 발췌뿐 · 시그니처 `src` 소유 · 정량 분석층 부재 · 결론 판정 표 부재 · 서술 수치 검사 부재)을 확정 ②**8단계 흐름**(S1 트리거 → S2 사건 시점 고정 → S3 결정적 수집 → S4 통계·ML → S5 원인 분류 표 → S6 조치 표 → S7 LLM 서술+수치 검사 → S8 전달·피드백) ③**데이터 원천 카탈로그**(D-P1~P6 · D-J1~J11 · D-K1 · D-R1 · D-D1 — 도구·REST 경로·SQL 테이블·명령·해상도) ④**CPU 사례 명세**: 분석 S4-A~G(STL+MAD·변화점 · 점유·CPU 성분 · Theil–Sen 부하 정규화 잔차 · Adtributor식 서비스 몫 · CPU 루프 트랜잭션 · 프로파일 자기 시간 집계 · 변경 DiD · 피어 · 유사 사례 · BARO) → 원인 분류 CPU-0~9 → 분류별 조치·검증·롤백 → `rca_bundle` JSON → LLM 고정 양식 ⑤다른 유형 6종(S-M·S-F·S-W·S-E·S-D·S-N) 요약 ⑥신규 구현 RC-1~RC-14(기존 CU와 겹치는 것은 앞당김) ⑦Wave 재배치 제안(M-R1 결정적 사슬 · ML 의존 0) ⑧G-12~G-14 · J-12~J-16 · §0.3 외부 연계 행 정정. 코드 0건 유지 · 설치 0 · 실 LLM 0 · 운영 조회 0 |
| v5 | 2026-10-13 | 사용자 지시(*"제니퍼에서도 알람이 발생될 수 있고 시간대와 연관관계를 확인하여 SMS, APM 알람을 복합적으로 분석 … ML모델은 aws G5 gpu 서버 … G-14의 문제를 해결하여 함수 단위의 집계"*)와 답변(G5 = 운영망 연결 VPC · ML 전용, 툴 호출 필요 시만 별도 LLM 서빙) 반영. ①**§R.8 SMS×APM 복합 분석**: 현행 실측(제니퍼 이벤트 폴러 → `alarm:raw` · 푸시 수신부 부재 · CPU 계열 이벤트 유형 매핑 없음 · D-317 사건 상관 기본 off·메모리 전용) · 끊긴 곳 Y-1~Y-6 · 병합 타임라인 수집 S3-T · 시간·연관 분석 T1~T6(시각 정렬 · 엔티티 연결 · 계층별 최초 발생 · 방향 규칙 D1~D7 · 과거 동시발생(lift·지연 분포·블록 순열 귀무·BH)·이벤트↔시계열 상관·24×7 시간대 프로파일 · 산출) · 복합 분류 XS-1~XS-6 · 제니퍼 트리거 예시 · 게이트 규칙 방향 불일치 기록(`plans/144` W6 보정 입력) · RC-15~RC-21 ②**§R.9 G-14 해결**: 문제 P-1~P-5 · 세 경로(F-1 profile.txt 전문 구조화 파서 · F-2 `apm_active_sample` 실행 중 요청 표본 · F-3a 기허용 `pidstat -t` 핫 스레드 / F-3b `jcmd`는 G-16) · 합의 집계(서명 정규화·Borda·부트스트랩 안정성) · **J-13 해소**(`totalCpuTime`) ③**§R.10 G5**: 작업 배치(경량 통계·판정은 `run_service` · 온라인 추론 `ml_service`·배치 `ml_batch`는 G5) · 폴백 의무 · 의존 분리 · 인스턴스(g5.2xlarge 출발) · 설치 8단계 · 데이터 통제(D-120 비충돌 해석 + 기관 클라우드 절차 J-20) · LLM 서빙 조건 · 개정 대조표 ④§R.11 Wave(②′ M-F · ③ M-G5) · J-17~J-20 · G-15(확정)·G-16·G-17 · G-7 재질의 ⑤§10·§13 D-223 초안·P-25·J-9·M7b·R-17에 v5 표기. 코드 0건 유지 · 설치 0 · 실 LLM 0 · 운영 조회 0 |
| v6 | 2026-10-13 | 사용자 지시(*"gpu 서버는 llm 서빙 목적보다 ML 을 통한 장애 분석의 목적으로 사용하려고 한다. 이 내용이 고려되어 있냐?"* → *"다 권장에 맞게 계획을 수정하라"*) 반영. 점검 결과 v5는 장애 분석 ML 대부분을 `run_service`에 두어 G5가 예측·학습·배치에 치우쳐 있었다. **(다) 혼합 배치**를 적용했다. ①§R.10 머리·R.10.1 재작성: **G5 = ML 장애 분석 서버** · 판정 필수 경량 통계는 `run_service` · **G5 장애 분석 API `/v1/analyze` GA-1~GA-6**(광역 이상 스캔 · 다차원 국소화 · 유사 사건 하이브리드 · 과거 연관 조회 · 학습 원인 순위(M7c 뒤) · 예측 구간·ETA(M7b 뒤)) · S2 직후 비동기 요청·S5 진입 회수(조사 타임박스 불변) · GA별 경량 짝 폴백과 사유 표기 · GA-1·GA-2만 MCP 직접 조회(사건당 상한) · 온라인 우선(배치 cgroup 상한) ②**불변식 I-11 판정 독립**(§4.1) — S5 필수 조건은 `run_service` 출력만 · GA는 보조 조건·보조 후보 · 불일치는 표기만 ③R.10.3 출발 인스턴스 g5.2xlarge → **g5.4xlarge**(온라인 CPU 작업) · 이중화 보류 근거 ④LLM 서빙을 **부차**로 명시(R.10 머리·R.10.3·R.10.6 — ML 인스턴스·예산과 분리) ⑤RC-22~RC-25 · J-21(광역 조회 부하) · R.11 Wave ③~⑥ 재배치(라벨 불필요한 GA-1·GA-2 먼저) · G-15 v6 확정 문구 ⑥R.1·S4·§10·§13 D-223 초안·R.10.7 대조표에 v6 표기. 코드 0건 유지 · 설치 0 · 실 LLM 0 · 운영 조회 0 |
