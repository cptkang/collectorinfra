# Cross-layer(App ↔ Container ↔ Node ↔ DB) 토폴로지 구축 및 RCA 학술 문헌 조사

> 조사 한계: WebSearch 스니펫 위주(전문 정독 아님). 각 항목에 [검증됨=검색 결과에서 확인 / 미검증=기억 기반 또는 2차 요약]을 표기. 세부 수치는 원문 확인 필요.

## (a) 계층 간 토폴로지/의존성 그래프 구축 — 파드→노드 엣지를 어떻게 얻는가

### Takeaway
대부분의 RCA 논문은 토폴로지를 (1) 트레이스에서 서비스 호출 그래프로 추출하고, (2) 호스팅 관계(service/pod→host)는 쿠버네티스 배포 메타데이터(정적)에서 가져오며, 서로 다른 모니터링 도구 간 키 정합(pod IP≠host IP)을 직접 다룬 논문은 거의 없다. 가장 직접적인 근거는 MicroRCA(서비스+호스트 속성 그래프), IBM의 cross-layer service topology, KylinRCA(host-pod-service 이종 그래프), DeepFlow(네트워크 계층 태깅)다.

### Cited Findings
- **MicroRCA** (Li Wu, Johan Tordsson, Erik Elmroth, Odej Kao, NOMS 2020, IEEE/IFIP). 앱 계측 없이 앱 수준 증상과 시스템 자원 사용량을 연결. 서비스와 호스트 머신을 정점으로, 서비스 호출 및 호스팅 관계를 방향 엣지로 하는 속성 그래프(정점마다 KPI 시계열)에서 이상 전파를 추적. K8s 클러스터 벤치마크 주입 실험에서 precision 89%, MAP 97% 보고. 토폴로지 출처는 서비스 호출(서비스 메시/트래픽 관측)+K8s 스케줄 정보로 추정되나 상세는 미검증 — [HAL](https://hal.archives-ouvertes.fr/hal-02441640) ; 그래프 구조 설명은 후속 논문 기술 인용([검색 결과](https://dl.ifip.org/db/conf/cnsm/cnsm2022/64.pdf)). 적용성: 우리 문제(pod→node 엣지)와 가장 구조가 같음. 호스팅 엣지를 pod→node 매핑 테이블로 채우면 된다.
- **Towards More Effective and Explainable Fault Management using Cross-Layer Service Topology** (Dhanya R. Mathews, Mudit Verma, J. Lakshmi, Pooja Aggarwal, IBM, IEEE CLOUD 2022, DOI 10.1109/CLOUD55607.2022.00026). 클라우드 마이크로서비스 앱의 cross-layer topology를 발견하는 도구를 제시하고, 이것이 관측 데이터 상관과 설명 가능한 장애 관리에 필요하다고 주장. 구축 방법·평가는 페이지에서 확인 못 함(원문 필요) — [IBM Research](https://research.ibm.com/publications/towards-more-effective-and-explainable-fault-management-using-cross-layer-service-topology). 적용성: 주제가 정확히 일치. 최우선 정독 대상.
- **KylinRCA** (Hou 외, arXiv 2509.12231, 2025-09, 게재 학회 미확인). 오프라인 정적 토폴로지 DB(배포·호출 관계)와 온라인 동적 인과 엣지를 합쳐 host–Pod–service 이종 그래프를 구성하고 type-aware GAT로 국소화. 자원층→컨테이너층→서비스층 전파를 모델링. 데이터: Alibaba Cluster Trace V2, AIOps Challenge 2023, 비공개 e-커머스 데이터(엔티티 F1 92.3%, 보고 수치). 정적 DB를 어떻게 채우는지는 미기재. 2차 요약 출처이므로 미검증 — [EmergentMind 요약](https://www.emergentmind.com/topics/kylinrca).
- **ClearCausal** (NCSU, ACSOS 2024). 앱 함수 트레이스와 인프라 메트릭(CPU·메모리·네트워크) 사이의 인과 분석 + 동적 의존 그래프로 원인 서비스·함수를 찾음. 오픈소스 공개. 정식 저자·제목 미확인(후속 검색에서 재확인 실패) — [PDF](https://dance.csc.ncsu.edu/papers/ACSOS24.pdf) (PDF 본문 파싱 실패, 요약은 검색 스니펫 기반, 미검증).
- **Tritium** (Boston Univ./IBM, WoC 2021). K8s 이벤트로 롤아웃을 감지하고 모든 메트릭을 후보 원인으로 취급(업스트림 다른 노드 메트릭 포함). 상관 기반이며 인과 증명 아님 — [PDF](https://www.bu.edu/peaclab/files/2022/01/Tritium-woc21.pdf) (스니펫 기반).
- **Groot** (Hanzhang Wang 외, eBay, ASE 2021 industry/experience). 3단계: 서비스 의존 그래프 구성 → 이벤트 인과 그래프(메트릭 편차·상태 변경·코드 배포 등 이벤트) → 원인 순위. eBay 프로덕션 5,000+ 서비스 지원 — [arXiv 2108.00344](https://arxiv.org/abs/2108.00344), [ASE 페이지](https://conf.researchr.org/details/ase-2021/ase-2021-papers/60/Groot-An-Event-graph-based-Approach-for-Root-Cause-Analysis-in-Industrial-Settings). 적용성: 이벤트(알람·배포)를 노드로 하므로 서로 다른 도구의 이종 신호를 이벤트로 정규화하는 설계 참고.
- **MicroHECL** (Fudan·Alibaba, ICSE-SEIP 2021, arXiv 2103.01782). 런타임에 서비스 호출 그래프를 동적 구성, 이상 전파 체인을 탐색하고 Pearson 상관으로 순위. Alibaba에서 top-3 hit 68%, 로컬라이제이션 30분→5분 — [arXiv](https://arxiv.org/abs/2103.01782v1), [Aliyun 글](https://developer.aliyun.com/article/781634). 서비스 계층만 다루고 호스트 계층 미포함(추정).
- **CloudRanger**: IBM 관련 시스템으로 보고됨, 연도 불일치(IBM 페이지는 ICSS 2014, 타 논문은 Wang et al., 2018로 인용). 정확한 서지 미확정 — [IBM Research](https://research.ibm.com/publications/cloudranger-root-cause-identification-for-cloud-native-systems). 인용 시 원문 재확인 필요.
- **DeepFlow** (Junxian Shen 외, "Network-Centric Distributed Tracing with DeepFlow: Troubleshooting Your Microservices in Zero Code", ACM SIGCOMM 2023, DOI 10.1145/3603269.3604823). eBPF/kernel hook 기반 제로코드 트레이싱으로 네트워크 계층(게이트웨이·서비스메시·DB·NIC)의 블라인드 스팟을 메우고 네트워크 데이터를 다른 계층과 연결. 성능 문제 원인의 47.3%가 네트워크 인프라라는 수치 인용 — [DOI](https://doi.org/10.1145/3603269.3604823), [PDF 사본](https://mmlab.snu.ac.kr/wp-content/uploads/2025/06/5BSIGCOMM27235D20Network-Centric20Distributed20Tracing20with20DeepFlow20Troubleshooting20Your20Microservices20in20Zero20Code-20250609-073054.pdf), [GitHub](https://cdn.jsdelivr.net/gh/deepflowio/deepflow@main/README.md). 세부(미검증, 기억 기반): span/flow에 K8s 리소스 태그(pod, node, namespace 등)를 주입하는 "smart-encoding"으로 계층 간 조인을 수행. 적용성: pod IP↔node 키 불일치를 에이전트 측에서 태그로 해소하는 설계 선례. 단, 도입에는 eBPF 에이전트 배포 필요(폐쇄망 운영 제약 별도 검토).
- **Eadro** (Cheryl Lee, Tianyi Yang, Zhuangbin Chen, Yuxin Su, Michael R. Lyu, ICSE 2023, DOI 10.1109/ICSE48619.2023.00150). 트레이스·로그·KPI로 서비스 내 행위와 서비스 간 의존을 모델링하고 이상 탐지+원인 국소화를 멀티태스크로 통합. 코드·데이터(Zenodo) 공개 — [arXiv 2302.05092](https://arxiv.org/pdf/2302.05092). 호스트 계층 미포함(추정).
- **Nezha** (Guangba Yu 외, Sun Yat-sen Univ., ESEC/FSE 2023). 메트릭·트레이스·로그를 동일 이벤트 형식으로 변환하고 이벤트 그래프를 마이닝해 정상 vs 장애 구간 패턴 비교. 코드영역·리소스 유형까지 국소화, 두 벤치마크 평균 top1 89.77% 보고 — [FSE 페이지](https://2023.esec-fse.org/details/fse-2023-research-papers/8/-Remote-Nezha-Interpretable-Fine-Grained-Root-Causes-Analysis-for-Microservices-on-), [GitHub](https://github.com/IntelligentDDS/Nezha). 적용성: "이질 도구 데이터를 공통 이벤트 스키마로" 접근이 우리 APM/SMS/DPM 정합에 가장 가까운 이식 대상.
- **DiagFusion** (Shenglin Zhang 외, arXiv 2302.10512). 메트릭·로그·트레이스 다중모달 이벤트를 인스턴스 수준에서 표현하여 원인 인스턴스·장애 유형 진단. 기존 대비 원인 인스턴스 국소화 20.9%~368% 향상 보고. 배포 데이터/의존 그래프 사용 여부는 확인 못 함 — [arXiv](https://arxiv.org/abs/2302.10512v2).
- **Sage** (Yu Gan, Mingyu Liang, Sundar Dev, David Lo, Christina Delimitrou, ASPLOS 2021). 비지도 학습, Causal Bayesian Network + Graphical VAE 기반으로 의존 그래프 위에서 성능 문제 디버깅; 정확도 91~95%(로컬)/93%+ 보고 — [PDF](https://www.csl.cornell.edu/~delimitrou/papers/2021.asplos.sage.pdf), [Google Research](https://research.google/pubs/sage-practical-scalable-ml-driven-performance-debugging-microservices/).
- **CausalRCA** (arXiv 2209.02500): 인과추론 기반 세밀한 원인 국소화 — [arXiv](https://arxiv.org/pdf/2209.02500) (스니펫 수준, 상세 미검증).
- **KGroot** (arXiv 2402.13264): 지식그래프+GCN 기반 RCA — [arXiv](https://arxiv.org/pdf/2402.13264) (스니펫만 확인, 미검증).
- **SynergyRCA** (arXiv 2506.02490): K8s 리소스 종류 그래프 + LLM 트리아지로 원인 위치 예측, 2개 프로덕션 K8s 클러스터에서 평가. 정확도 약 0.90은 2차 요약 — [arXiv](https://arxiv.org/pdf/2506.02490) (미검증).
- **GRALAF / IoMiRCA** (CNSM 2022 / SAC 2023 포스터): 컨테이너·노드 메트릭 + 서비스메시 지연, MicroRCA 확장 — [CNSM](https://dl.ifip.org/db/conf/cnsm/cnsm2022/64.pdf), [IoMiRCA poster](https://digitalcollection.zhaw.ch/bitstream/11475/28106/3/2023_Heeb-etal_IoMiRCA-Poster.pdf).
- 서지 서술만 확보하고 이번 조사에서 내용 확인 실패: **RCD(NeurIPS 2022), CIRCA(KDD 2022), MULAN(EMNLP 2024), PDiagnose** — 검색 결과에 해당 논문이 나오지 않음.

### Inferences
- 우리 시나리오의 핵심(APM pod ↔ SMS host IP 조인)은 논문들이 "배포 메타데이터(K8s API의 pod→nodeName/hostIP, 노드 InternalIP)에서 정적 호스팅 엣지를 만든다"는 암묵적 가정에 의존한다. 즉 MicroRCA/KylinRCA식 이종 그래프 모델에 K8s 메타를 조인 키로 얹는 방식이 학술적으로 가장 정합적이다(추론).
- Nezha/Groot의 "공통 이벤트 스키마" + MicroRCA의 "service–host 호스팅 엣지" 조합이 APM/SMS/DPM 정합의 현실적 설계로 보인다(추론).
- DPM(DB 세션) 계층을 명시적으로 포함한 논문은 이번 조사에서 확인하지 못했다. 앱→DB 엣지는 서비스 호출 그래프의 한 종류(DB를 서비스 노드로)로 취급하는 것이 일반적인 대안(추론).

### Gaps
- pod IP≠host IP 키 불일치 자체를 다루는 논문, IBM cross-layer topology 구축 알고리즘 세부, ClearCausal 정식 서지, CloudRanger 연도, RCD/CIRCA/MULAN/PDiagnose 내용은 이번 조사에서 확인하지 못함.
- Microsoft/Alibaba/Huawei의 CMDB·지식그래프 기반 토폴로지 산업 논문은 별도 검색하지 못함(예산 한계).

## (b) 토폴로지를 소비하는 LLM/에이전트 기반 RCA

### Takeaway
LLM 에이전트 RCA 논문 다수는 토폴로지를 명시적 그래프로 소비하지 않고 도구(쿼리/코드 실행)로 텔레메트리를 조회하는 방식이다. OpenRCA는 여전히 낮은 정확도를 보여 LLM 단독 RCA의 한계를 시사한다.

### Cited Findings
- **RCACopilot** (Microsoft 외, EuroSys 2024, arXiv 2305.15778). 수집 단계(on-call 엔지니어가 만든 incident handler로 진단 데이터 수집) + 예측 단계(유사 과거 사고 검색 후 LLM이 원인 범주 예측·설명). 1년치 MS 사고로 평가, 정확도 최대 0.766(지표 정의 확인 못 함). 수집 컴포넌트는 4년 이상 운영 — [arXiv](https://arxiv.org/pdf/2305.15778), [MSR](https://www.microsoft.com/en-us/research/publication/automatic-root-cause-analysis-via-large-language-models-for-cloud-incidents). 토폴로지는 handler 내 사전 정의 쿼리로 간접 반영(추정).
- **RCAgent** (Zefan Wang, Zichuan Liu, Yingying Zhang 외, Alibaba, CIKM 2024, arXiv 2310.16340). 사내 배포 LLM의 도구 보강 자율 에이전트. 궤적 self-consistency, 컨텍스트 관리, 도메인 지식 주입. ReAct 대비 우위 보고, Alibaba Cloud Flink 플랫폼에 통합 — [arXiv](https://arxiv.org/pdf/2310.16340).
- **OpenRCA** (Junjielong Xu 외, Microsoft·CUHK-SZ·Tsinghua, ICLR 2025). 장애 사례+텔레메트리에서 시간·컴포넌트·사유 식별 벤치마크, RCA-agent 베이스라인. 2차 보도에 따르면 Claude 3.5가 5.37%(oracle KPI), RCA-agent 적용 시 11.34% — [ICLR 포스터](https://iclr.cc/virtual/2025/poster/32093), [PDF](https://proceedings.iclr.cc/paper_files/paper/2025/file/d29b8d53678015079e1d245c023e49d2-Paper-Conference.pdf), [GitHub microsoft/OpenRCA](https://www.sourcepulse.org/projects/1834649). 수치는 36Kr 2차 출처 — 원문 확인 필요. 비판: [Traversal 블로그](https://www.traversal.com/blog/openrca-isnt-root-cause-analysis-why-that-matters). "OpenRCA 2.0"(arXiv 2606.27154)은 어그리게이터 요약만 확인, 미검증.
- **mABC** (Wei Zhang 외, Beihang·Cloudwise, EMNLP 2024 Findings, arXiv 2404.12135). 7개 전문 에이전트와 블록체인식 투표, 고정 워크플로로 순환 의존 문제 완화. AIOps challenge 및 Train-Ticket 데이터 — [arXiv](https://arxiv.org/pdf/2404.12135), [ACL Anthology](https://preview.aclanthology.org/eacl-dois/2024.findings-emnlp.232). 의존 그래프를 에이전트 도구가 순회하는 구조.
- **Flow-of-Action** (Changhua Pei 외, CNIC-CAS, WWW'25 Industry Track, arXiv 2502.08224). SOP를 제약으로 둔 멀티에이전트(ActionAgent·JudgeAgent·ObAgent·CodeAgent). ReAct 35.50% → 64.01% 보고 — [arXiv](https://arxiv.org/pdf/2502.08224).
- **KRCA** (Jiang 외, arXiv 2607.01788, 2026-07): API 수준 drill-down, skeleton 인과 그래프, 메모리 보강 멀티에이전트. Kuaishou 300건 장애, AC@1 0.88 — 2차 요약, 미검증 — [EmergentMind](https://www.emergentmind.com/topics/kylinrca).
- **TAMO** (arXiv 2504.20462): 도메인 모델 도구를 결합한 LLM 에이전트 RCA — [arXiv](https://www.arxiv.org/pdf/2504.20462v1) (스니펫만).
- **KubeRCA 저장소(kube-rca/agent)**: 학술 논문 아님. Prometheus·pod 로그/이벤트 수집 후 LLM이 보고서 생성, 토폴로지 그래프 사용 증거 없음 — [GitHub](https://github.com/kube-rca/agent).

### Inferences
- LLM 에이전트에게 토폴로지를 주는 가장 현실적 방법은 (1) 사전 구축한 pod→node, 서비스→DB 엣지를 조회 도구로 노출하거나 (2) RCACopilot처럼 handler에 고정 쿼리로 넣는 것이다(추론). 토폴로지 자체를 LLM이 생성하게 하는 것은 근거 없음.
- OpenRCA의 낮은 수치는 결정적 조인(IP→pod→node 매핑)을 LLM 추론이 아닌 코드로 선처리해야 한다는 방향을 지지한다(추론; 이 저장소의 "LLM 정합성 의존 금지" 원칙과도 부합).

### Gaps
- 토폴로지를 입력으로 평가한 LLM RCA 논문(ablation 포함)은 확인 못 함. Microsoft/Alibaba/Huawei 산업 논문(예: 지식그래프 토폴로지)은 미조사.

## 서베이

### Takeaway
RCA 서베이는 다중모달(로그·메트릭·트레이스·토폴로지) 활용의 부족을 지적한다.

### Cited Findings
- **A Comprehensive Survey on Root Cause Analysis in (Micro) Services: Methodologies, Challenges, and Trends** (Wang & Qi, arXiv 2408.00803, 2024) — [arXiv](https://arxiv.org/abs/2408.00803v1)
- **Failure Diagnosis in Microservice Systems: A Comprehensive Survey and Analysis** (Zhang, Xia, Fan 외, ACM TOSEM 2025, arXiv 2407.01710). 토폴로지 포함 다중모달 활용 연구의 부족 지적 — [arXiv](https://arxiv.org/pdf/2407.01710v2)
- **A Survey of AIOps in the Era of Large Language Models** (arXiv 2507.12472, 2025) — [arXiv](https://arxiv.org/pdf/2507.12472)
- **Anomaly Detection and Failure Root Cause Analysis in (Micro)Service-Based Cloud Applications: A Survey** (arXiv 2105.12378, 2021) — [arXiv](https://arxiv.org/pdf/2105.12378)
- **Root Cause Analysis for Microservices based on Causal Inference: How Far Are We?** (arXiv 2408.13729, 2024) — [arXiv](https://arxiv.org/html/2408.13729v1) (스니펫만; RCD/CIRCA 등 인과 기반 기법 비교 평가로 알려져 있으나 미검증).
- 요청한 "A Survey of AIOps Methods for Failure Management"는 검색에서 확인하지 못함.

### Gaps
- 서베이 전문 미정독, 각 서베이의 cross-layer 항목 별도 확인 필요.
