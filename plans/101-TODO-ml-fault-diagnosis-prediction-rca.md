# 101. ML 기반 장애 진단·예측·RCA — 폴스타·제니퍼·DPM 연계, HolmesGPT에 정량 증거 공급

> **작성일**: 2026-09-17 · **v2**(같은 날 — 사용자 지시로 ML 배치를 `sre_agent` 편입으로 재검토·변경 · 버전·라이브러리 제약 해제 반영 · `sre_agent` venv 의존성 해석 실측) · **v3**(2026-09-20 — 사용자 지시로 **Kaggle 경기·데이터셋 심층 조사** 반영: 구현 공학 원칙 K-1~K-10 · **누수 금지 목록 KL-1~KL-6** · 평가 규약 11~13 · GAP-11·12)
> **성격**: 문헌·경기 조사 + 구현 계획 · **상태: 계획(코드 0건 · 사용자 확정 게이트 G-1~G-11 대기(G-8은 허용 확정) · 확인 사항 J-1~J-11 대기)** — 파일명 `-TODO`
> **요청 취지(사용자 지시 원문)**: *"holmesgpt에서 분석하는 장애 분석과 관련하여 머신러닝을 이용하여 장애 분석과 예측 기능을 추가로 구축하려고 한다. 장애 진단, 예측, RCA 등과 관련된 머신러닝, AI 관련 문헌과 라이브러리를 심도있게 조사하여 현재 구현되었거나 계획하고 있는 폴스타, 재니퍼, DPM 솔루션을 연계하여 장애 대응 기능을 구현하려고 한다. 관련 문헌과 자료, 논문 등을 조사하여 구현 계획을 별도의 파일로 작성하라."*
> **v2 지시(원문)**: *"버전과 라이브러리는 필요에 따라 수정하거나 사용할 수 있다. 패키지는 별도가 아니라 sre_agent에 구성하는 것을 검토하라."*
> **v3 지시(원문)**: *"101 번은 장애 진단을 위한 ml 에 대한 계획이다. 이런 장애 진단을 위해 모니터링 정보와 로그 정보를 이용하여 ml 을 통해 장애를 진단하는 알고리즘을 캐글에서 심도 있게 찾아보고 분석하여 101번의 계획을 업데이트하라."*
> **인수하는 공백**: `plans/62` §5.1 「예측·선제 탐지(C5)」 슬롯. 번호 63은 다른 주제(폴스타 과적합 분리)가 가져갔고, 예측 코드와 소유 계획은 **0건**이다(§2.6). 이 계획이 C5를 명시적으로 인수한다. RCA 축은 `plans/50` §0.8·`plans/91` C′의 **결정적 상관 위에 ML 계층을 얹는 확장**이다(대체하지 않는다).
> **선행·접점 계획**: `plans/50`(RCA 설계 정본) · `plans/51`(L1/L2/L3 수집) · `plans/55`(APM·DPM 로드맵) · `plans/60`(E3 동적 baseline) · `plans/64`·`66`(조사 위임·조치 거버넌스) · `plans/83`(피드백 = 라벨 원천) · **`plans/87`(제니퍼 Open API · `apm_*`)** · `plans/91`(C′ 잔여 장부) · `plans/92`(Prometheus·OpenMetrics) · `plans/95`(ITAM)
> **관련 결정**: D-003(읽기 전용) · **D-035**(결정적 수치 + LLM 해석) · **D-048**(.5·.11 "ML 미사용") · D-107(토폴로지) · **D-110·D-113**(HW·STL) · **D-114**(e5 임베딩 반입·주석 전용) · **D-118**(`sre_agent` 경계 — **범위 개정 대상** §4.2 C-6) · **D-119**(관측 데이터 읽기 경계 = `mcp_server`) · D-120(운영 데이터 외부 LLM 송신 금지) · **D-127**(과금 승인) · **D-139**(패키지 경계) · **D-161**(승격-폐기 동반·폐기 4항 실측) · D-162(플래그 만료) · D-174(개발 측정치 기준선 인용 금지) · D-176 ⑤(표본 <20 추정 문구 금지) · D-177(피드백) · D-181(공유 venv 파손) · D-189(L3 경로 B) · D-195(제니퍼 예약) · **D-197**(결정적 상관·가설 순위) · D-207~D-209(C′) · D-210(92 예약) · D-219(폐쇄망 산출물 축소 반출)
> **신규 결정 예약**: **D-223**(「채번 이력」 표 등재 — §13). ※ 채번 실측 2026-09-17: `## D-` 헤더 최댓값 D-222 · 「변경 이력」 표 최댓값 D-222 · 「채번 이력」 표에 D-223 이상 예약 없음.
> **실측 기준**: 브랜치 `multiintent` · HEAD `c64ef98` · 2026-09-17. 코드·계획 실측은 읽기 전용 조사 에이전트 2개, 문헌·라이브러리 조사는 에이전트 3개로 수행했다. **패키지 설치 0건 · 실 LLM 호출 0건 · 운영 DB 조회 0건.** 라이브러리 버전·라이선스는 이날 PyPI·GitHub·Hugging Face API로 실측했다. **v2**: holmesgpt 0.36.0 설치본 소스를 직접 읽었고(`additional_toolsets`), `uv pip compile`로 `sre_agent` venv 조건(Python 3.13)의 의존성 해석을 실측했다(설치 0건). **v3**(2026-09-20): Kaggle 조사는 웹 검색·1차 출처 페이지 fetch로만 했다. **Kaggle 경기·데이터셋 페이지는 JS 렌더링이라 정적으로 읽히지 않아 경기 메타데이터·라이선스 문자열은 대부분 3자 기록 기반(V3)**이며, 데이터 내려받기·계정 접속·패키지 설치는 0건이다. 확인이 필요한 항목은 J-11로 모았다.
> **동반 조사 문서(근거 정본 · v3에서 4종)**: `docs/aiops_benchmark/ml_rca_literature.md`(RCA 49+22건) · `docs/aiops_benchmark/ml_anomaly_prediction_literature.md`(이상탐지·예측 60항목) · `docs/aiops_benchmark/ml_library_vendor_survey.md`(라이브러리·HolmesGPT·벤더 AI) · **`docs/aiops_benchmark/ml_kaggle_competition_survey.md`(v3 신설 — Kaggle 경기 10건·데이터셋 8계열·누수 사례 6종)**. 본문의 `RCA-*`·`PRED-*`·**`K-*`·`KC-*`·`KD-*`·`KL-*`·`KG-*`** ID는 이 문서들의 항목 ID다.
> **▶ 결정만 필요하면 §10 「사용자 확정 게이트」만 읽으면 된다. 문헌 근거 요약은 §3, 단계별 작업은 §7이다.**

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
| 외부 연계 | 제니퍼 `apm_*`는 계획만 있다(`plans/87` `-TODO`) · **DPM 전용 계획서 없음** · `PROMETHEUS_URL` 공란(`plans/92` §0.2) | 교차 계층 RCA의 입력이 아직 없다 |
| **라벨·피처 경계**(v3) | 원인·조치 텍스트(`cmm_alarm_note.alarmcause`·`cmm_alarm_knowledge`)와 `resolution{duration}`·`recurrence`·ack 시각은 **사건이 끝난 뒤 사람이 기입한다** | 이 필드를 피처로 쓰면 누수다(KL-3). **라벨 원천으로만** 쓰고 피처 테이블 진입을 허용목록으로 막아야 한다. 이 경계를 세우지 않으면 리플레이셋 점수가 구조적으로 부풀어 채택 게이트 자체가 무의미해진다 |

→ **M0(데이터·전제 실측)이 모든 구현보다 앞선다.** 코드는 읽기 전용 실측 스크립트뿐이다.

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

- **P-25 torch는 쓸 수 있지만 쓰는 곳을 좁힌다.** 라이브러리 제약이 아니라 **문헌 근거** 때문이다. 탐지 용도로는 파운데이션 모델·딥 탐지기의 이점이 없고(P-1·P-4), 예측(M7b)·임베딩(F4 2차)에서만 이점이 있다. PyPI Linux torch wheel은 CUDA 의존을 선언하고 wheel 하나가 554MB다. CPU 서버에는 **PyTorch CPU 인덱스**(`download.pytorch.org/whl/cpu`) wheel을 쓴다.
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
- **방법**: RCACopilot 방식이다(EuroSys'24, 기존 dossier). 텍스트 유사도 × **시간 감쇠** `e^(−α|Δt|)`로 계산한다. 1차는 scikit-learn TF-IDF(문자 n-gram — 한국어 형태소 분석기 없이 동작)로 torch 없이 시작한다. 2차는 **이미 반입 협의된 e5-small**(D-114 · 로컬 디렉토리 · CPU)을 `ml-embed` extra로 쓰며 신규 모델 반입은 0이다. 메트릭 특징(단계 1 이상 지표 집합의 Jaccard)을 보조 유사도로 결합한다. 1·2차 우열은 리플레이셋으로 판정한다.
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
| **J-9** | `sre_agent` 호스트의 배치 스코어링 가용 CPU·메모리(대상 서버 수 × 지표 수) · `run_service`와 공존 시 여유 | 운영 서버 여유 실측 | 배치 주기 · C-2 · M7b |
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
| CU-28 | `domain/ml/dim_localize.py`: Squeeze/PSqueeze·HALO 가지치기 · 외부 원인 판정 → `dimension_localize` |
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
| **M7b** | 파운데이션 모델 예측기(Chronos-2 small → Toto-2 small → TimesFM 2.5 → TTM-R2) · `ml-tsfm` extra · **`ml_batch` 프로세스에서만 추론**(C-2) · torch CPU wheel · safetensors | G-7 · 가중치 보안 심사 · J-9 CPU 실측 · MASE·CRPS 우위 · 해석 검사(C-4) 통과 |
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
| **G-7** | 파운데이션 모델 도입 시점 | (가) M7b 조건 충족까지 보류 (나) M2와 병행해 Chronos-2 small·Toto-2.0-4m 백테스트 착수 | **(가)** — v2에서 라이브러리 제약은 풀렸다(해석 OK). 그러나 **문헌상 탐지 용도 이점이 없고**(P-4), CPU 지연은 미실측이며(GAP-7), 가중치 보안 심사는 여전히 필요하다. 고갈 ETA 정확도가 단계 1에서 부족하다고 실측되면 (나)로 앞당긴다 |
| **G-8** | HolmesGPT 버전 | **허용 확정(2026-09-17 사용자: "버전과 라이브러리는 필요에 따라 수정하거나 사용할 수 있다")** → M1 CU-1에서 0.36.0 → **0.42.x** 상향 | 근거: 0.40.0 command injection·SSRF 수정 · 0.42.0 MCP `structuredContent` · SSE deprecated 대응 여지. ML 스택과의 해석 OK(§3.5.1). 되돌리기는 핀 1줄이다. **실 LLM 회귀만 D-127 승인** |
| **G-9** | 확정 원인 라벨 수집 | (가) `plans/83` 피드백에 이벤트 키·`root_cause` 선택 필드 확장(U-F (ii) "별도 저장소 금지" 준수) (나) 별도 라벨 저장소 | **(가)** — U-F (ii)가 이미 결정됐다. (나)는 그 결정을 다시 여는 것이다 |
| **G-10** | 평가 데이터 처리 위치 | (가) 폐쇄망 서버 내 평가 · 축소 지표만 반출(D-219 준용) (나) 원본 반출 후 개발망 평가 | **(가)** — D-219 · D-120 |
| **G-11**(v2 신설) | 게이트로 ML 신호를 전달하는 방식 | (가) `ml_batch`가 Redis `ml:*` 발행 · 게이트는 키만 읽음 (나) 게이트가 알람마다 `sre_ml_*`를 동기 조회 | **(가)** — `alarm:baseline:*` 계약 선례 · 워커 지연 없음 · `sre_agent` 장애 시 키 만료로 E3 폴백(§4.6). (나)는 알람 처리 지연과 `sre_agent` 가용성이 결합된다 |

**확인 사항(결정이 아니라 사실 확인)**: J-1~J-11(§7 M0). v2에서 J-6은 라이브러리 선택 제약이 아니라 **반입 일정** 확인으로 바뀌었다. v3에서 J-11(공개 데이터 라이선스 원문)이 추가됐다 — M1 착수를 막지 않는다.
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
| **R-17**(v2) | `sre_agent` venv 비대화(torch·transformers 수 GB) → 조사 서비스 배포 부담 | extra 분리(C-1) · `ml-tsfm`·`ml-embed`는 해당 단계에서만 설치 · 조사 서비스 노드와 `ml_batch` 노드를 분리 배치할 수 있게 엔트리를 분리해 둔다 |
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
