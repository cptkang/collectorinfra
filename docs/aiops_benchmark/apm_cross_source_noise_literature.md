# 문헌 조사: 제니퍼(APM) 이벤트 노이즈 캔슬링 + 폴스타×제니퍼 크로스소스 상관 (`plans/144` 동반)

작성 2026-10-07 · 코드 수정 없음 · 존재·제목·저자·게재처는 WebSearch/WebFetch로 하나씩 확인했다(확인 수단은 표의 「확인」 열). 인용수는 사용하지 않았다(OpenAlex는 AI/SE 과소집계, positional 질의는 노이즈가 커서 일부 DOI 확인에만 썼다). dblp·Semantic Scholar는 rate limit/파싱 오류로 쓰지 못했다.

## 0. 사용자 기억 대비 정정 (날조 방지)

| 요청문 기재 | 확인 결과 |
|---|---|
| iPACK = Chen et al., "Towards intelligent incident management..." | 서로 다른 두 논문. iPACK은 **"Incident-aware Duplicate Ticket Aggregation for Cloud Systems"**, 제1저자 **Jinyang Liu**, **ICSE 2023**. "Towards intelligent incident management: why we need it and how we make it"는 **Zhuangbin Chen** et al., **ESEC/FSE 2020 (Industry)**, 시스템명 IcM BRAIN |
| "Identifying impactful service system problems via log analysis" | 실재(He et al., ESEC/FSE 2018, Log3C). 알람 상관이 아니라 로그+KPI 클러스터링이라 주 목록에서는 제외 |
| AlertStorm = Zhao et al., ICSE-SEIP 2020 | 맞음. 제1저자 Nengwen Zhao, ICSE-SEIP 2020, pp.162-171. 대형 상업은행(China Everbright Bank) 적용 사례 |
| AlertRank (DSN?) | **IEEE INFOCOM 2020**, "Automatically and Adaptively Identifying Severe Alerts for Online Service Systems", Nengwen Zhao et al. (DSN 아님) |
| "Fighting the fog of war" (Gao? FSE) | **Liqun Li** et al., **USENIX ATC 2021** (FSE 아님) |
| ToN 2018 "Wang et al." | **Weng**, Wang, Yang, Yang. IEEE/ACM ToN 26(4):1646-1659, 2018 (제목은 "Root cause analysis of anomalies of multitier services in public clouds"). 서지 정보는 검색 스니펫 기준이며 원문 페이지는 열지 못함 |
| COLA (2023~) | 실재. 단 **2024**, "Knowledge-aware Alert Aggregation in Large-scale Cloud Systems: a Hybrid Approach", Jinxi Kuang et al., **ICSE-SEIP 2024**(arXiv 코멘트 기준) |
| "Correlating Alerts Across Layers" | **미확인 — 해당 제목의 논문을 찾지 못함. 인용 금지** |
| FLASH, "Mining dependencies between alerts", PAL | 이번 조사에서 확인하지 못함 — **미확인, 표에서 제외** |
| Moogsoft/Datadog Watchdog/Elastic | Moogsoft·BigPanda·PagerDuty·Splunk·Dynatrace는 공식 문서 페이지 확인. Datadog Watchdog·Elastic은 검색하지 않음 — **미확인, 제외** |

## 1. 3층 분리 표

용어: **요건 ①** = APM 이벤트 자체의 노이즈 캔슬링, **요건 ②** = 인프라×APM 크로스소스·크로스레이어 상관/억제.

### (a) 동료심사 논문

| 약칭 | 정식 제목 | 저자 | 연도 | 게재처 | DOI/URL | 핵심 기법 | 요건 · 차용 설계 요소 |
|---|---|---|---|---|---|---|---|
| AlertStorm | Understanding and Handling Alert Storm for Online Service Systems | Nengwen Zhao et al. | 2020 | ICSE-SEIP 2020 | https://2020.icse-conferences.org/details/icse-2020-Software-Engineering-in-Practice/24/Understanding-and-Handling-Alert-Storm-for-Online-Service-Systems | 알람 폭풍 실증연구 + 폭풍 탐지(F1>0.9) + 대표 알람 요약(검토 대상 98% 이상 감소). 은행 적용 | ①② 폭풍 구간을 먼저 탐지하고 그 안에서 대표 알람만 노출하는 2단 구조. 우리 4-티어 게이트의 "폭풍 모드" 진입 조건 근거 |
| AlertRank | Automatically and Adaptively Identifying Severe Alerts for Online Service Systems | Nengwen Zhao et al. | 2020 | IEEE INFOCOM 2020 | https://researchwith.stevens.edu/en/publications/automatically-and-adaptively-identifying-severe-alerts-for-online/ | 알람 텍스트/지표 특징 + XGBoost ranking으로 심각 알람 선별, F1 0.89 | ① 규칙 기반 심각도 대신 학습형 순위. 우리는 피드백 라벨이 쌓인 뒤 하향/상향 판정의 후보 특징(과거 조치율)으로 차용 |
| LiDAR | Identifying Linked Incidents in Large-Scale Online Service Systems | Yujun Chen et al. | 2020 | ESEC/FSE 2020, pp.304-314 | https://doi.org/10.1145/3368089.3409768 | 인시던트 텍스트 + 과거 연결 이력의 구조 정보로 연결(링크) 식별, 딥러닝 | ② 연결 이력을 정답 라벨로 쓰는 방식 — 운영자가 수동으로 묶은 사건을 라벨로 축적하는 피드백 루프 |
| IcM BRAIN | Towards Intelligent Incident Management: Why We Need It and How We Make It | Zhuangbin Chen et al. | 2020 | ESEC/FSE 2020 Industry | https://2020.esec-fse.org/details/esecfse-2020-industry-papers/10/Towards-Intelligent-Incident-Management-Why-We-Need-It-and-How-We-Make-It | 2년 실증: 의존성 불완전·자원 건강도 평가 부정확이 핵심 난점 | ② 토폴로지(서버-WAS 매핑)가 불완전하면 억제하지 말라는 근거 |
| Fog of War | Fighting the Fog of War: Automated Incident Detection for Cloud Systems | Liqun Li et al. | 2021 | USENIX ATC 2021 | https://www.usenix.org/conference/atc21/presentation/li-liqun | 다수 모니터의 이상을 종합해 인시던트를 자동 탐지(Azure) | ② "개별 알람이 아닌 사건 단위" 탐지 단위 정의 |
| GRLIA | Graph-based Incident Aggregation for Large-Scale Online Service Systems | Zhuangbin Chen et al. | 2021 | ASE 2021 | https://doi.org/10.1109/ase51524.2021.9678746 (arXiv 2108.12179) | 연쇄 장애 그래프에서 위상+시간 관계를 비지도 그래프 표현학습으로 인코딩해 인시던트 집계(Huawei Cloud) | ② 시간 인접 + 토폴로지 인접을 동시에 쓰는 집계. 시간창 단독 규칙의 하위 호환 대안 |
| iPACK | Incident-aware Duplicate Ticket Aggregation for Cloud Systems | Jinyang Liu et al. | 2023 | ICSE 2023 | https://arxiv.org/abs/2302.09520 | 고객 티켓과 클라우드 인시던트의 장애 정보를 융합해 중복 집계 | ① 텍스트 유사도만으론 의존성 때문에 놓친다는 논거. 우리 이벤트 중복 키에 구조 필드 포함 근거 |
| COLA | Knowledge-aware Alert Aggregation in Large-scale Cloud Systems: a Hybrid Approach | Jinxi Kuang et al. | 2024 | ICSE-SEIP 2024 (arXiv 코멘트 기준) | https://arxiv.org/abs/2403.06485 | 시간·공간 상관 마이닝(통계 근거) + 불확실 쌍만 LLM 추론으로 위임, F1 0.901-0.930 | ②★ 가장 직접적. 통계로 확정되는 쌍은 코드가, 애매한 쌍만 LLM에 보내는 하이브리드 → 우리 "결정적 가드 우선, LLM 최소" 원칙과 일치 |
| MicroRCA | MicroRCA: Root Cause Localization of Performance Issues in Microservices | Li Wu et al. | 2020 | IEEE/IFIP NOMS 2020 | https://hal.archives-ouvertes.fr/hal-02441640 | 서비스·머신 속성 그래프에서 애플리케이션 성능 증상과 자원 사용률을 상관, PageRank류 원인 순위(precision 89%) | ②★ "WAS 응답 지연 증상 ↔ 같은 호스트 자원 지표" 연결의 정식 선례. 계측 불필요 |
| ToN18 | Root Cause Analysis of Anomalies of Multitier Services in Public Clouds | J. Weng et al. | 2018 | IEEE/ACM Trans. Networking 26(4) | (서지만 확인, 원문 미열람) | 다계층 서비스의 이상 근본원인 분석 | ② 멀티티어(웹-앱-DB) 계층 모델 선례 |
| CloudRanger | CloudRanger: Root Cause Identification for Cloud Native Systems | P. Wang et al. | 2018 | IEEE/ACM CCGrid 2018 | https://research.ibm.com/publications/cloudranger-root-cause-identification-for-cloud-native-systems | 토폴로지 없이 동적 인과 영향 그래프 + 2차 랜덤워크 | ② 토폴로지 부정확 시 데이터 기반 그래프로 보강하는 대안 |
| Sage | Sage: Practical & Scalable ML-Driven Performance Debugging in Microservices | Yu Gan et al. | 2021 | ASPLOS 2021 | https://research.google/pubs/sage-practical-scalable-ml-driven-performance-debugging-in-microservices/ | 비지도 ML, 의존 영향을 반사실 방식으로 평가, 정확도 93%+ | ② 반사실("이 하위가 정상이었다면") 개념 — 억제 정당화에 개념 차용(구현은 과함) |
| CIRCA | Causal Inference-Based Root Cause Analysis for Online Service Systems with Intervention Recognition | Mingjie Li et al. | 2022 | KDD 2022 | https://arxiv.org/abs/2206.05871 | 시스템 구조 지식 + 인과 가정 기반 그래프, 부모 조건부 분포 변화로 원인 지표 판정(top-1 recall +25%) | ② 구조 지식(우리 서버↔인스턴스 매핑)을 인과 그래프 사전으로 쓰는 방식 |
| Groot | Groot: An Event-graph-based Approach for Root Cause Analysis in Industrial Settings | Hanzhang Wang et al. | 2021 | ASE 2021 | https://conf.researchr.org/details/ase-2021/ase-2021-papers/60/Groot-An-Event-graph-based-Approach-for-Root-Cause-Analysis-in-Industrial-Settings | 메트릭·로그·활동을 "이벤트"로 요약한 실시간 인과 그래프, SRE 사용자 정의 규칙 반영(eBay, top-3 95%) | ②★ 이벤트(알람) 단위 그래프 + 도메인 규칙 주입 — 우리 규칙(YAML) 기반 상관과 구조 동일 |
| CloudRCA | CloudRCA: A Root Cause Analysis Framework for Cloud Computing Platforms | Yingying Zhang et al. | 2021 | CIKM 2021 | https://arxiv.org/abs/2111.03753 | KPI·로그·토폴로지 이질 소스를 특징화, 지식 주입 계층 베이지안 네트워크(Alibaba, 대응시간 20%+ 절감) | ② 이질 소스 융합 + 운영자 지식 주입의 산업 검증 사례 |
| RCA-HowFar | Root Cause Analysis for Microservice System based on Causal Inference: How Far Are We? | Luan Pham et al. | 2024 | ASE 2024 | https://arxiv.org/abs/2408.13729 | 인과 발견 9종·RCA 21종 비교: 모든 상황에서 우수한 방법 없음, 합성 데이터 성능이 실제와 괴리 | ② 인과 추론 도입을 보류하는 근거(공백 절 참조) |
| COT | Fast Outage Analysis of Large-scale Production Clouds with Service Correlation Mining | Yaohui Wang 등(제1저자 미확인) | 2021 | ICSE 2021 | https://2021.icse-conferences.org/details/icse-2021/icse-2021-papers/66/Fast-Outage-Analysis-of-Large-scale-Production-Clouds-with-Service-Correlation-Mining | 수백 서비스 성능지표 상관으로 전역 서비스 상관 그래프 구축, 장애 트리아지(정확도 82-83%) | ② 서비스 간 상관을 마이닝으로 학습해 정적 토폴로지를 보강 |
| Donut | Unsupervised Anomaly Detection via Variational Auto-Encoder for Seasonal KPIs in Web Applications | Haowen Xu et al. | 2018 | WWW 2018 | https://arxiv.org/abs/1802.03903 | 계절성 KPI에 VAE, 재구성 확률로 이상 판정 | ① 응답시간·TPS 계절성 정규화 선례 |
| OmniAnomaly | Robust Anomaly Detection for Multivariate Time Series through Stochastic Recurrent Neural Network | Ya Su et al. | 2019 | KDD 2019 | (KDD 2019, 서지 확인) | GRU+VAE+normalizing flow, 다변량 이상, F1 0.86 | ① 다변량 APM 지표 이상 탐지(장기 로드맵 수준) |
| JumpStarter | Jump-Starting Multivariate Time Series Anomaly Detection for Online Service Systems | Minghua Ma et al. | 2021 | USENIX ATC 2021 | https://www.usenix.org/conference/atc21/presentation/ma | 압축 센싱으로 초기화 20분, F1 94% | ① 신규 WAS 인스턴스의 콜드스타트 대응 |
| TraceAnomaly | Unsupervised Detection of Microservice Trace Anomalies through Service-Level Deep Bayesian Networks | Ping Liu et al. | 2020 | ISSRE 2020 | (서지 확인, 제1저자·DOI 미확인) | 트레이스 단위 비지도 이상 우도 | ① 트레이스 기반 — 우리 제니퍼 이벤트 위주 입력에는 직접 적용 불가, 참고 |
| TASA | Rule Discovery in Telecommunication Alarm Data | M. Klemettinen, H. Mannila, H. Toivonen | 1999 | J. Network and Systems Management 7(4) | https://www.cs.helsinki.fi/u/htoivone/pubs/jnsm99.pdf | 알람 시퀀스에서 빈발 에피소드 규칙 발굴로 실시간 상관 규칙 구성 | ②★ 시간창 상관 규칙을 데이터로 발굴·검증하는 고전적 근거 |
| Episodes | Discovery of Frequent Episodes in Event Sequences | H. Mannila, H. Toivonen, A. I. Verkamo | 1997 | Data Mining and Knowledge Discovery 1(3) | (기억 기반 서지, 미재확인) | WINEPI: 창 폭 W 안의 빈도로 에피소드 정의 | ② 창 폭 W를 명시 파라미터로 두고 빈도 임계로 평가하는 틀 |
| EvTS | Correlating Events with Time Series for Incident Diagnosis | Chen Luo et al. | 2014 | ACM SIGKDD 2014 | https://www.microsoft.com/en-us/research/?p=249869 | 이벤트-시계열 상관의 존재·시간 순서·단조 효과를 검정 | ②★ 인프라 알람(이벤트)을 APM 응답시간(시계열)에 대해 검정 — 시간 순서(선행 여부) 검사로 "원인/증상" 방향 판정 |
| HawkesGC | Learning Granger Causality for Hawkes Processes | Hongteng Xu et al. | 2016 | ICML 2016 | https://proceedings.mlr.press/v48/xuc16.html | 점과정(Hawkes) 영향 함수로 이벤트 타입 간 Granger 인과 그래프 추정 | ② 이벤트 간 영향 지연 분포로 창 크기를 데이터에서 추정 |
| RCACopilot | Automatic Root Cause Analysis via Large Language Models for Cloud Incidents | Yinfang Chen et al. | 2024 | EuroSys 2024 | https://arxiv.org/abs/2305.15778 | 알람 유형별 핸들러가 진단 정보 수집 + LLM이 원인 범주 예측·서술(정확도 0.766) | ②(후순위) 알람 유형→진단 수집 핸들러 매핑은 우리 조사 위임 단계와 유사 |
| LLM-RCM | Recommending Root-Cause and Mitigation Steps for Cloud Incidents using Large Language Models | Toufique Ahmed et al. | 2023 | ICSE 2023 | https://arxiv.org/abs/2301.03797 | 4만+ 인시던트로 LLM 제로샷·파인튜닝 평가, 사람 평가 | 참고: LLM 출력은 "권고"에 한정하라는 근거 |
| Xpert | Xpert: Empowering Incident Management with Query Recommendations via Large Language Models | Yuxuan Jiang et al. | 2024 | ICSE 2024 | https://arxiv.org/abs/2312.11988 | 인시던트 이력+LLM으로 DSL 질의 추천 | 참고(우리 요건과 거리 있음) |
| OpenRCA | OpenRCA: Can Large Language Models Locate the Root Cause of Software Failures? | Junjielong Xu 등(제1저자 미확인) | 2025 | ICLR 2025 | https://iclr.cc/virtual/2025/poster/32093 | 335건 장애·68GB 텔레메트리 벤치, 최고 모델 11.34% 해결 | 평가: LLM 단독 RCA의 한계를 보여 보수적 설계 근거 |
| LLM-Fail | Stalled, Biased, and Confused: Uncovering Reasoning Failures in LLMs for Cloud-Based Root Cause Analysis | E. Riddell et al. | 2026 | FORGE '26 (검색 결과 기준) | https://arxiv.org/abs/2601.22208 | LLM RCA 추론 실패 유형 분류 | 보수적 설계 근거 |

### (b) Preprint (동료심사 게재 미확인)

| 약칭 | 제목 | 저자 | 연도 | URL | 핵심 | 요건 · 차용 |
|---|---|---|---|---|---|---|
| DiLink | Dependency Aware Incident Linking in Large Cloud Systems | Supriyo Ghosh et al. | 2024 | https://arxiv.org/abs/2403.18639 | 텍스트 + 서비스 의존 그래프 임베딩을 Orthogonal Procrustes로 정렬, F1 0.96(Microsoft 610 서비스). arXiv 코멘트에 게재처 없음 | ② 의존 그래프를 억제의 "게이트 조건"으로 쓰는 설계의 근거(단 preprint) |
| RCAEval | RCAEval: A Benchmark for Root Cause Analysis of Microservice Systems with Telemetry Data | Luan Pham et al. | 2024 | https://arxiv.org/abs/2412.17015 | 3개 시스템 735 장애 케이스·15 베이스라인(WWW 2025 companion 발표로 검색됨) | 평가: 공개 데이터셋 |
| LEMMA-RCA | LEMMA-RCA: A Large Multi-modal Multi-domain Dataset for Root Cause Analysis | (저자 미확인) | 2024 | https://arxiv.org/abs/2406.05375 | IT/OT 다중 도메인 RCA 데이터셋 | 평가: 공개 데이터셋 |
| LLM-Agg | Leveraging Large Language Models for Efficient Alert Aggregation in AIOps (MDPI Electronics 2024) | 미확인 | 2024 | https://www.mdpi.com/2079-9292/13/22/4425 | 검색 요약상 시공간 군집 + LLM으로 연쇄 효과 추적. **페이지 403으로 열람 실패 — 서지 미검증, 인용 시 재확인** | 참고 |

### (c) 산업 자료

| 약칭 | 자료 | URL | 핵심 | 요건 · 차용 |
|---|---|---|---|---|
| SRE-WB | Google SRE Workbook, "Alerting on SLOs" (다중 창·다중 번레이트) | (검색으로 2차 해설 확인: https://grafana.com/blog/2025/02/28/how-to-implement-multi-window-multi-burn-rate-alerts-with-grafana-cloud/ — 원문 챕터는 열지 않음) | 장창(1h/6h/3d)+단창(5m/30m/6h) 동시 초과 시만 발화. 페이지 14.4x/6x, 티켓 1x | ①★ APM 응답시간·에러율 이벤트를 "단발 임계 초과" 대신 지속+현재 진행 이중 조건으로 |
| AM-inhibit | Prometheus Alertmanager inhibit_rules (source_matchers / target_matchers / equal) | https://prometheus.io/docs/alerting/0.21/configuration | 원인 알람 발화 중 동일 라벨(equal) 값의 증상 알람 억제 | ②★ 억제 규칙의 최소 형태: source(원인)·target(증상)·equal(호스트 키). 우리 크로스소스 억제의 표준 문법 |
| Dynatrace | Davis AI 인과 엔진 + Smartscape 토폴로지 (벤더 설명, 2차 요약으로 확인) | https://www.techtarget.com/it-infrastructure/tip/Dynatrace-bets-on-causal-intelligence-for-AI-observability | 수직(인프라) 의존과 수평(서비스 호출) 경로로 증상을 원인에 귀속, 수백 증상 알람을 하나의 problem으로 통합 | ② 호스트→프로세스→서비스 수직 계층 + 서비스 호출 수평 경로의 2축 모델 |
| BigPanda | Correlation Patterns | https://docs.bigpanda.io/reference/correlation-patterns | 소스 시스템·태그·시간창·필터로 패턴 정의, ML이 패턴 제안 | ② 상관 규칙 = {소스, 태그, 창, 필터}. 우리 규칙 스키마와 대응 |
| PagerDuty | Event Intelligence / Event Orchestration | https://www.pagerduty.com/platform/aiops/event-intelligence/ | 지능형·내용 기반·시간 기반 알람 그룹핑, 중복 제거·억제 | ② 내용 기반(태그)과 시간 기반 그룹핑 분리 |
| Splunk ITSI | Notable event aggregation policy → episode | https://docs.splunk.com/Documentation/ITSI/4.11.0/EA/AggOverview | 필터·분할(split)·종료(break) 기준으로 이벤트를 episode로 묶음, 자체 severity | ② "분할 기준·종료 기준" 개념(창 닫기 조건)을 명시 |
| Moogsoft | Situations (상관 그룹) | https://docs.moogsoft.com/v9/en/moogsoft-onprem-overview.html | 이벤트→알람 중복제거→유사도 기반 situation 그룹 | 참고 |

(산업 자료는 마케팅 수치를 포함한다. 예: PagerDuty "98% 노이즈 감소"는 벤더 주장이며 동료심사 근거로 쓰지 말 것.)

## 2. 주제별 시사점 (우리 설계에 옮길 원칙)

### A. 알람/인시던트 집계·상관
1. **2단 구조**: (i) 폭풍/사건 구간 탐지 → (ii) 구간 안 대표 알람 선정(AlertStorm). 우리 4-티어 게이트의 상위에 "사건 단위"를 둔다.
2. **통계 확정 쌍은 코드, 애매한 쌍만 LLM**(COLA). LLM은 최종 억제 결정자가 아니라 후보 제안자로만. 이는 프로젝트의 "LLM 비결정성" 원칙과 일치.
3. **시간 + 구조를 함께 쓴다**(GRLIA, DiLink). 시간창만으로 묶으면 무관 사건 병합, 구조만으로 묶으면 시간 무관 병합. 두 조건 AND가 보수적 기본.
4. **연결 라벨은 운영자 행동에서 축적**(LiDAR). 피드백(수동 병합·분리)을 라벨로 저장해 오탐/누락을 사후 평가.
5. **토폴로지 불완전이 주된 실패 원인**(IcM BRAIN). 서버↔WAS 인스턴스 매핑이 확인된 경우에만 억제, 불확실하면 묶기(그룹핑)만 하고 억제하지 않는다.

### B. 크로스레이어·토폴로지 기반 증상 억제
1. **방향성 규칙(원인→증상)**: 인프라 CPU/메모리 포화 → WAS 응답 지연·액티브 서비스 급증·GC 증가는 파생 방향. 반대(WAS 쪽 폭주가 CPU 포화를 유발: 무한루프, 대량 트래픽)도 가능하므로 **기본은 "묶되 억제하지 않음", 억제는 호스트 키 일치 + 선행 순서 + 지속 조건을 모두 만족할 때만**(Alertmanager의 equal 키, EvTS의 시간 순서 검정).
2. **원인 후보 우선순위**(MicroRCA, Groot): 같은 호스트의 인프라 알람 > 같은 호스트의 인스턴스 다운/힙·GC > 서비스 응답/에러. 하위 계층(자원)이 상위 계층(애플리케이션)의 증상을 설명한다는 순서를 기본값으로 하되 규칙 데이터화.
3. **인과 추론(Sage, CIRCA, CloudRanger)은 도입 보류**: 논문들은 마이크로서비스 벤치 또는 자사 클라우드 환경에서 평가됐고, 비교 연구(RCA-HowFar)는 "모든 상황에서 우수한 방법 없음·합성 데이터 성능은 실환경과 괴리"를 보고했다. 정적 매핑 + 시간 순서 검정이 보수적 출발.
4. **업계 공통 문법**: 원인 알람 발화 중 + 동일 키 → 증상 억제(Alertmanager inhibit), 수직·수평 2축(Dynatrace), 상관 규칙 {소스, 태그, 창, 필터}(BigPanda), 창 종료 조건(Splunk). 우리 규칙 스키마 설계 시 대응 필드를 갖출 것.
5. **"APM 정상이면 인프라 알람 하향"은 문헌 근거가 약하다**(공백 3-2). 하향은 억제보다 위험이 작다(티어 하향 + 요약에 기록)는 비대칭 원칙으로 보수적으로 시작.

### C. APM 이상탐지·노이즈
1. **단발 임계 대신 지속+현재 이중 창**(SRE-WB): 응답시간 초과·에러율은 장창(지속)과 단창(여전히 진행) 동시 조건. 회복되면 빠르게 닫힌다.
2. **계절성 정규화**(Donut): 업무시간/배치 시간대 패턴이 큰 은행 환경에서 고정 임계 이벤트는 반복 노이즈가 된다. 단, 제니퍼 이벤트는 이미 벤더가 임계로 생성한 이벤트이므로 우리 단계는 "재탐지"가 아니라 **이벤트 스트림의 사후 정규화**(시간대별 기준 발생률 대비 이상 여부)로 한정.
3. **콜드스타트**(JumpStarter): 신규 인스턴스는 이력이 없으므로 학습형 억제 비활성, 규칙형만 적용.
4. 다변량·트레이스 기반(OmniAnomaly, TraceAnomaly)은 제니퍼 이벤트 입력만으로는 불가. 로드맵 수준.

### D. 시간창 상관의 통계적 근거
1. **창 크기는 규칙으로 고정하되 데이터로 보정**: WINEPI의 창 폭 W(Mannila 1997), TASA 에피소드 규칙(Klemettinen 1999). 우리 이력에서 "A 발생 후 Δt 내 B 발생" 지연 분포를 구해 분위수(예: 90~95%)로 창을 정하고, 기준 발생률 대비 우연 동시발생 확률을 점검한다(구체 분위수는 본 조사에서 근거 문헌을 확인하지 못함 — 설계 가정으로 표시할 것).
2. **우연 동시발생 통제**: 임계 알람은 흔해서(특히 같은 호스트의 CPU와 응답 지연은 업무시간에 함께 오른다) 창 안 동시발생이 곧 상관은 아니다. 기준 발생률 대비 lift(조건부 확률/주변 확률)를 보는 것이 association-rule 계열의 표준 접근.
3. **이벤트-시계열 검정**(EvTS): 인프라 알람 시점 앞뒤로 APM 응답시간 시계열의 변화를 검정해 상관 존재, 시간 순서, 단조 효과를 판정. 이벤트-이벤트 창 매칭보다 정밀하나 구현 비용이 크므로 2차 단계.
4. **Hawkes/Granger 계열**(HawkesGC): 이벤트 타입 간 영향 지연을 추정해 창을 학습하는 길. 데이터 양이 충분해진 뒤의 선택지.

### E. LLM 기반 요약·분류·상관
1. COLA식 하이브리드(통계 우선, LLM은 불확실 쌍). 
2. RCACopilot·LLM-RCM은 **원인 범주 예측/서술**이지 억제 결정 평가가 아니다. 억제 판정을 LLM 출력에 의존하는 것은 문헌 근거가 없다. OpenRCA(최고 11.34%)와 LLM-Fail은 LLM RCA의 신뢰성 한계를 보고한다.
3. LLM은 사건 요약·한국어 통보문 생성·운영자 지식 정리에 한정하고, 억제 여부는 결정적 규칙이 낸다.

## 3. 공백 (동료심사 문헌이 없거나 약한 부분 — 보수적 설계 근거)

1. **APM 벤더 이벤트(제니퍼류) 단독 노이즈 캔슬링의 동료심사 평가는 확인하지 못했다.** 이번 검색에서 제니퍼 이벤트를 대상으로 한 연구는 찾지 못했다. APM 관련 문헌은 대부분 마이크로서비스 트레이스/메트릭 입력(TraceAnomaly 등)이며, 벤더가 임계로 생성한 이벤트의 후처리를 다룬 논문은 확인하지 못했다.
2. **"APM 정상 → 인프라 알람 하향"을 직접 평가한 동료심사 연구는 확인하지 못했다.** 사용자 영향(애플리케이션 증상) 기반 우선순위는 SLO 알림(SRE-WB)·산업 제품 관행에 있으나, 인프라 알람을 응용 정상성으로 하향하는 정확도·누락률 평가는 못 찾음. 하향은 억제보다 약한 조치로 시작하고 섀도 모드로 검증.
3. **크로스소스 억제의 거짓 억제(false suppression) 비용을 정량화한 연구가 드물다.** 대부분 집계 정확도(F1)나 RCA top-k를 보고하며, 억제로 인한 누락 사건/지연 비용은 보고하지 않는다. 억제율과 누락률을 우리가 직접 측정해야 한다.
4. **창 크기 선택의 일반 법칙은 없다**: 문헌은 데이터 의존적으로 정한다. 호스트·WAS 환경별 지연 분포를 직접 계측해 정해야 한다.
5. **인과 RCA 평가는 합성/벤치 환경 중심**이며(RCA-HowFar의 지적) 은행 온프레미스 Java WAS 환경에 대한 결과는 확인하지 못했다.
6. **산업 제품(Dynatrace, BigPanda, PagerDuty 등)의 성능 수치는 벤더 주장**이며 독립 검증이 없다.
7. 못 찾은 항목: "Correlating Alerts Across Layers" 및 요청문의 FLASH·"Mining dependencies between alerts"·PAL(제목 기준 확인 실패 — 존재 여부 미확인).
8. 이번 조사는 검색 스니펫과 소수 페이지 열람에 의존했다. 표의 "(서지 확인)" 항목은 원문 PDF를 열지 않았다. 구현 근거로 인용하기 전에 원문 확인이 필요하다.

## 4. 평가 방법 문헌 (지표·데이터셋)

### 지표
| 지표 | 정의 | 출처/비고 |
|---|---|---|
| 알람 감소율 | 검토 대상 알람 수 감소 | AlertStorm: 98% 이상(대표 알람 요약) |
| 폭풍 탐지 F1 | 폭풍 구간 탐지 정밀·재현 | AlertStorm: >0.9 |
| 집계 F1 | 쌍별(pairwise) 또는 클러스터 F1 | COLA 0.901-0.930, GRLIA, iPACK 0.871-0.935, DiLink 0.96 |
| 심각 알람 F1 / 순위 | 심각 알람 선별 | AlertRank 0.89 |
| RCA top-k / Avg@k | 원인 후보 순위 정확도 | MicroRCA(precision 89%, MAP 97%), Groot(top-1 78%, top-3 95%), CIRCA(top-1 recall), RCAEval 계열 |
| 대응시간 절감 | 운영 시간 단축 | CloudRCA(20%+). MTTA/MTTR은 업계 표준 지표로 우리 섀도 모드에 사용 |
| 우리 추가 지표(문헌에 없음, 설계 제안) | **거짓 억제율**(억제됐으나 운영자가 조치한 사건 비율), **누락 사건 수**, **억제율**, **그룹 순도/분할도** | 문헌이 거의 보고하지 않아 직접 정의 필요. 섀도 모드에서 "억제했을 알람"을 발송 상태로 두고 운영자 조치 여부와 대조 |

### 데이터셋
| 데이터셋 | 내용 | 공개 | 우리 적합성 |
|---|---|---|---|
| RCAEval | 3개 마이크로서비스 시스템 735 장애 케이스, metric/log/trace | 공개 | 인프라+서비스 메트릭 포함이나 알람 이벤트·제니퍼 형식 아님. 방법 비교용 |
| LEMMA-RCA | IT/OT 다중 도메인 RCA | 공개 | 동일 |
| OpenRCA | 335 장애·68GB 텔레메트리 | 공개(GitHub) | LLM RCA 평가용. 우리 용도엔 과함 |
| 알람/인시던트 집계 데이터(AlertStorm, GRLIA, COLA, LiDAR, iPACK 등) | 은행·Huawei·Microsoft 사내 데이터 | **비공개로 확인** — 공개 링크를 찾지 못함(iPACK은 코드 저장소 있음: https://github.com/OpsPAI/iPACK, 데이터 공개 여부 미확인) | 재현 불가. 우리 자체 이력 + 섀도 모드가 유일한 평가 수단 |

### 평가 절차 시사
1. 공개 알람 집계 데이터셋은 없다시피 하다. 자체 데이터로 **오프라인 재생(replay) → 섀도 모드 → 부분 적용** 순서.
2. 라벨은 운영자의 수동 병합/조치(LiDAR식)에서 확보하고, 라벨 편향(조치된 것만 라벨)을 보고서에 명시.
3. 보고 시 억제율만 단독으로 쓰지 않고 누락률과 함께 쌍으로 보고. 억제율이 높고 누락이 있으면 실패로 간주.
