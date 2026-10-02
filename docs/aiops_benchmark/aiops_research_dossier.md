# AIOps 선진사례 조사 자료 종합 (Research Dossier)

> 작성: AIOps 아키텍트 · 2026-07-13  
> 목적: collectorinfra AIOps 고도화를 위한 선진 솔루션·도입사례·구현 메커니즘 조사의 **근거 자료(출처)와 산출물 인덱스**를 한 파일로 통합.

## 1. 조사 개요

인프라·시스템·서비스 운영의 AIOps 체계 설계를 위해 다음 4개 관점에서 선진사례를 조사했다:

1. **역량 벤치마킹** — 9개 선도 플랫폼(Dynatrace·Datadog·Splunk ITSI·Moogsoft·BigPanda·PagerDuty·New Relic·ServiceNow·Grafana/OSS)을 10개 AIOps 역량 축으로 비교.
2. **도입 사례** — 13개 기업(금융·통신·SW·미디어·공공)의 적용범위·도입방법·정량효과.
3. **기능별 구현 메커니즘** — 각 기능이 어떤 신호로 판단하고 어떤 알고리즘·자료구조로 동작하는지 벤더별로 분해.
4. **구현 계획 반영** — collectorinfra(폴스타 단일소스 인프라 AIOps)의 노이즈 게이트 실측 대조 + 고도화 계획(Plan 60).

### 핵심 결론

- 선진 솔루션의 노이즈·상관·RCA는 대부분 **결정적 규칙/그래프/통계 + 보조적 ML·LLM** 구조 → collectorinfra의 D-035 원칙(결정적 수치+LLM 해석)과 정합.
- 공통 전제조건은 **동적 baseline**(이상탐지·예측)과 **위상 의존성 그래프**(상관·RCA·변경 상관).
- collectorinfra는 노이즈 억제(Plan 52, D-048)를 이미 업계 수준으로 구현 → 다음 병목은 **토폴로지 그래프 → 변경 피드 → 동적 baseline** 순.

## 2. 산출물 인덱스

이번 조사로 생성된 분석 산출물(모두 프로젝트에 저장됨):

| 파일 | 내용 |
|------|------|
| `aiops_benchmark_brief.html` | 9개 플랫폼 × 10개 역량 벤치마크 브리프 + 7개 설계 패턴 |
| `aiops_capability_heatmap.png` | 9×10 역량 히트맵 |
| `aiops_platform_comparison.csv` | 9개 플랫폼 상세 비교(분류·메커니즘·강점·한계·적합대상) |
| `aiops_adoption_casestudy_brief.html` | 13개 기업 도입 사례·방법론·폐루프 아키텍처 + collectorinfra 3-Wave 권고 |
| `aiops_adoption_cases.csv` | 13개 기업 사례(산업·솔루션·적용범위·방법·정량효과) |
| `aiops_adoption_effects.png` | 도입 효과 정량 성과 차트 |
| `aiops_vendor_implementation_reference.html` | 벤더별 기능 구현 레퍼런스(7기능×27항목) — 구현 기준 문서 |
| `aiops_vendor_implementation_reference.csv` | 벤더별 구현 메커니즘·알고리즘·전제조건 27행 |
| `aiops_mechanism_brief.html` | 기능별 판단기준·알고리즘·실제 구현방법 분해 + 노이즈 메커니즘 다이어그램 |
| `aiops_mechanism_decomposition.csv` | 10개 기능 메커니즘 분해표 |
| `aiops_noise_mechanism.png` | 노이즈 캔슬링 신호→판정→라우팅 메커니즘 다이어그램 |
| `plans/60-noise-cancellation-benchmark-refinement.md` | 노이즈 캔슬링 고도화 구현 계획서(E1~E5, D-067~071) — collectorinfra 프로젝트 내 |

## 3. 조사 출처 (주제별, 중복 제거 후 120건)

> 벤더 기술문서는 구현 방식의 **공개 서술** 기준이며 내부 세부는 비공개일 수 있음. 
> 도입효과 수치는 **벤더/고객 발표 기준으로 제3자 독립검증이 아님** — 의사결정 시 원 출처를 직접 확인할 것.

### Dynatrace (Davis·Smartscape·RCA)  (23건)

- Davis® AI — Dynatrace Docs — <https://docs.dynatrace.com/docs/discover-dynatrace/platform/davis-ai>
- Dynatrace bets on causal intelligence for AI observability | TechTarget — <https://www.techtarget.com/searchapparchitecture/tip/Dynatrace-bets-on-causal-intelligence-for-AI-observability>
- Dynatrace Davis Alternative: Open Source AI Root Cause Analysis (2026) | Aurora by Arvo AI — <https://www.arvoai.ca/blog/dynatrace-davis-alternative-open-source>
- How Davis Works :: Dynatrace Modernization Workshop — <https://dynatrace.awsworkshop.io/50_operate/20_how_davis_works.html>
- Dynatrace Intelligence — Dynatrace Docs — <https://docs.dynatrace.com/docs/dynatrace-intelligence>
- Davis AI — Dynatrace Docs — <https://docs.dynatrace.com/docs/semantic-dictionary/model/davis>
- Build trust with Dynatrace AI-driven root cause and impact analysis — <https://www.dynatrace.com/news/blog/build-trust-with-dynatrace-ai-driven-root-cause-and-impact-analysis/>
- Dynatrace Intelligence — <https://www.dynatrace.com/news/blog/next-generation-dynatrace-davis-ai-becomes-the-default-causation-engine/>
- Root cause analysis concepts — Dynatrace Docs — <https://docs.dynatrace.com/docs/dynatrace-intelligence/root-cause-analysis/concepts>
- AIOps strategy unlocks new possibilities for automation, customer satisfaction — <https://www.dynatrace.com/news/blog/aiops-strategy-unlocks-new-possibilities-for-automation-customer-satisfaction/>
- Dynatrace Platform Case Studies & Customer Success | Cuspera — <https://www.cuspera.com/products/dynatrace-platform-x-13693/customer-story>
- AI Case Studies | AI Success Stories & Lessons Learned — <https://www.itopsai.ai/case-studies/tdbank-dynatrace>
- 127 Dynatrace Case Studies, Success Stories, & Customer Stories | FeaturedCustomers — <https://www.featuredcustomers.com/vendor/dynatrace/case-studies>
- BT Digital customer story — <https://www.dynatrace.com/customers/bt-digital-transformation/>
- TD Bank customer story — <https://www.dynatrace.com/customers/td-bank/>
- How organizations are adopting AIOps and IT automation — <https://www.dynatrace.com/news/blog/how-organizations-are-adopting-aiops-and-it-automation/>
- Helping customers unlock the Power of Possible — <https://www.dynatrace.com/news/blog/helping-customers-unlock-the-power-of-possible/>
- Dynatrace AIops Use Cases: Practical, Data-Driven Observability for Modern Digital Experiences - Webeyez Insights — <https://webeyez.com/insights/guides/dynatrace-aiops-use-cases-guide>
- Customer stories — <https://www.dynatrace.com/customers/>
- Dynatrace expands Davis AI with Davis CoPilot, pioneering the first hypermodal AI platform for unified observability and security — <https://www.dynatrace.com/news/blog/hypermodal-ai-dynatrace-expands-davis-ai-with-davis-copilot/>
- Dynatrace Intelligence — <https://www.dynatrace.com/platform/artificial-intelligence/>
- Dynatrace Root Cause Analysis with Smartscape and ... — <https://www.linkedin.com/posts/techstevemancini_more-tools-wont-save-you-causality-will-activity-7416845483568078848-ameK>
- Root cause analysis — Dynatrace Docs — <https://docs.dynatrace.com/docs/dynatrace-intelligence/root-cause-analysis>

### Datadog (Watchdog·RCA)  (13건)

- Top AIOps Tools for DevOps Engineers in 2026: Datadog AI, Moogsoft, PagerDuty & More | DevOpsBoys — <https://devopsboys.com/blog/top-aiops-tools-for-devops-engineers-2026>
- PagerDuty vs Datadog: Which One Do You Actually Need? | NeuBird AI — <https://neubird.ai/blog/pagerduty-vs-datadog>
- Watchdog | Datadog — <https://www.datadoghq.com/product/platform/watchdog/>
- Datadog Watchdog™ — <https://docs.datadoghq.com/watchdog/>
- Watchdog Explains — <https://docs.datadoghq.com/dashboards/graph_insights/watchdog_explains/>
- Datadog Expands Its Watchdog AI Engine with Root Cause Analysis and Log Anomaly Detection | Datadog — <https://www.datadoghq.com/about/latest-news/press-releases/datadog-expands-its-watchdog-ai-engine-with-root-cause-analysis-and-log-anomaly-detection/>
- Datadog Expands Its Watchdog AI Engine with Root Cause Analysis and Log Anomaly Detection — <https://www.prnewswire.com/news-releases/datadog-expands-its-watchdog-ai-engine-with-root-cause-analysis-and-log-anomaly-detection-301525080.html>
- Watchdog: Auto-detect performance anomalies without setting alerts | Datadog — <https://www.datadoghq.com/blog/watchdog/>
- Watchdog RCA — <https://docs.datadoghq.com/watchdog/rca/>
- Datadog Expands Watchdog AI Engine with Root Cause Analysis and Log Anomaly Detection | APMdigest — <https://www.apmdigest.com/datadog-expands-watchdog-ai-engine-with-root-cause-analysis-and-log-anomaly-detection>
- Datadog Watchdog Guide: AI-Powered Alerts, Insights, and Root Cause Analysis - Webeyez Insights — <https://webeyez.com/insights/guides/datadog-watchdog-guide>
- Automated root cause analysis with Watchdog RCA | Datadog — <https://www.datadoghq.com/blog/datadog-watchdog-automated-root-cause-analysis/>
- Anomaly detection, predictive correlations: Using AI-assisted metrics monitoring | Datadog — <https://www.datadoghq.com/blog/ai-powered-metrics-monitoring/>

### Splunk ITSI (Event Analytics·Adaptive Thresholding)  (15건)

- Splunk ITSI Alert Noise: 7 Tuning Techniques That Actually Work — <https://www.bitsioinc.com/blog-post/itsi-incident-management-reduce-alert-noise>
- Machine Learning & ITSI Sitting In a Tree — <https://conf.splunk.com/files/2020/slides/ITO1820C.pdf>
- Breaking Through the Threshold: Leveling up ITSI Adaptive Thresholding with Splunk AI | Splunk — <https://www.splunk.com/en_us/blog/it/breaking-through-the-threshold-leveling-up-itsi-adaptive-thresholding-with-splunk-ai.html>
- What Is Adaptive Thresholding? | Splunk — <https://www.splunk.com/en_us/blog/learn/adaptive-thresholding.html>
- Alert Noise Reduction | Splunk — <https://www.splunk.com/en_us/solutions/alert-noise-reduction.html>
- Getting Started with Splunk ITSI Guide | PDF | Analytics | Predictive Analytics — <https://www.scribd.com/document/669710854/splunk-getting-started-with-itsi>
- Splunk ITSI Implementation Guide & Best Practices — <https://www.bitsioinc.com/blog-post/splunk-itsi-implementation-guide>
- Webinar: Adaptive Thresholds and Anomaly Detection: Unleash the Power of Machine Learning for Improved Operations with Splunk ITSI | Splunk — <https://www.splunk.com/en_us/form/unleash-the-power-of-machine-learning.html>
- Migrate anomaly detection to adaptive thresholding in ITSI | Splunk Docs — <https://help.splunk.com/en/splunk-it-service-intelligence/splunk-it-service-intelligence/visualize-and-assess-service-health/4.21/advanced-thresholding/migrate-anomaly-detection-to-adaptive-thresholding-in-itsi>
- Introducing Event iQ: Smarter Event Correlation in Splunk IT Service Intelligence (ITSI) | Splunk — <https://www.splunk.com/en_us/blog/observability/event-iq-splunk-it-service-intelligence-itsi.html>
- Tech Talk | Getting the Most Out of Event Correlation and Alert Storm Detection in Splunk ITSI — <https://community.splunk.com/t5/Community-Blog/Tech-Talk-Getting-the-Most-Out-of-Event-Correlation-and-Alert/ba-p/647049>
- Moogsoft vs Splunk: Evaluating AIOps Solutions for IT Operations Efficiency – appNeura — <https://appneura.com/moogsoft-vs-splunk/>
- Reduce Alert Noise - Splunk Lantern — <https://lantern.splunk.com/Observability/UCE/Guided_Insights/Reduce_Noise>
- Smarter Noise Reduction in ITSI | Splunk — <https://www.splunk.com/en_us/blog/platform/smarter-noise-reduction-in-itsi.html>
- Splunk integration - APEX AIOps - Moogsoft — <https://docs.moogsoft.com/moogsoft-cloud/en/splunk-integration.html>

### Moogsoft (Cookbook·Tempus·클러스터링)  (8건)

- Clustering Algorithm Guide - APEX AIOps - Moogsoft — <https://docs.moogsoft.com/v9/en/clustering-algorithm-guide.html>
- Configure Deterministic Alert Clustering with Cookbook — <https://docs.moogsoft.com/Enterprise.8.0.0/en/configure-deterministic-alert-clustering-with-cookbook.html>
- Review and Adjust Clustering Settings with Situation Visualization — <https://docs.moogsoft.com/Enterprise.8.0.0/en/review-and-adjust-clustering-settings-with-situation-visualization.html>
- Configure Clustering Algorithms - APEX AIOps - Moogsoft — <https://docs.moogsoft.com/Enterprise.8.0.0/en/configure-clustering-algorithms.html>
- Understand how correlation works - APEX AIOps - Moogsoft — <https://docs.moogsoft.com/moogsoft-cloud/en/understand-how-correlation-works.html>
- © 2020 Moogsoft Inc. All rights reserved. AT A GLANCE | ALERT CORRELATION — <https://www.moogsoft.com/product/clustering-correlation/>
- Reduce IT Noise Up to 98% with Alert Correlation — <https://docs.moogsoft.com/moogsoft-cloud/en/use-case-video--power-of-alert-correlation--.html>
- Moogsoft Review: Features, Pricing, Pros & Cons (2026) — <https://www.siit.io/tools/trending/moogsoft-review>

### BigPanda (상관·압축·고객사례)  (19건)

- Why event correlation, and how is AIOps involved? | BigPanda — <https://www.bigpanda.io/blog/why-event-correlation-and-how-is-aiops-involved/>
- AIOps Event Correlation Software: Transform IT Incident Response — <https://www.bigpanda.io/blog/event-correlation/>
- BigPanda – AIOps-driven event correlation platform helping customers to deliver extraordinary digital experiences - CIO Bulletin — <https://ciobulletin.com/magazine/profile/bigpanda-digitalizing-aiops-based-organizations>
- BigPanda's AIOps Event Correlation and Automation platform — <https://www.bigpanda.io/our-product/platform/>
- BigPanda Event Enrichment Engine: The secret ingredient for AIOps — <https://www.bigpanda.io/blog/event-enrichment-the-secret-ingredient-for-aiops/>
- BigPanda AIOps — <https://docs.bigpanda.io/en/bigpanda-aiops>
- AI Incident Detection & Response | Fast MTTD & MTTR | BigPanda — <https://www.bigpanda.io/our-product/event-correlation/>
- BigPanda | LinkedIn — <https://www.linkedin.com/company/bigpanda>
- Incident Correlation | BigPanda — <https://www.bigpanda.io/incident-correlation/>
- What is an AIOps platform? | BigPanda — <https://www.bigpanda.io/blog/what-is-an-aiops-platform/>
- Achieving quick time to value with AIOps | BigPanda — <https://www.bigpanda.io/blog/aiops-time-to-value/>
- Transforming ITSM with AIOps: EMA research | BigPanda — <https://www.bigpanda.io/blog/ema-research-transform-itsm-aiops/>
- Bigpanda Mttr AIOps Insights | Restackio — <https://www.restack.io/p/bigpanda-mttr-answer-cat-ai>
- What is AIOps? Use cases, benefits, & getting started | BigPanda — <https://www.bigpanda.io/blog/what-is-aiops/>
- 6 AIOps use cases for enterprise IT | BigPanda — <https://www.bigpanda.io/blog/what-are-aiops-use-cases/>
- Improve incident triage with AIOps to reduce downtime | BigPanda — <https://www.bigpanda.io/blog/incident-triage-and-mttr/>
- The human element of implementing AIOps | BigPanda — <https://www.bigpanda.io/blog/organizational-change-management-aiops-adoption/>
- FreeWheel Case Study | BigPanda — <https://www.bigpanda.io/customer/freewheel-case-study/>
- AI-powered IT Operations and Incident Management, AIOps — <https://www.bigpanda.io/>

### New Relic (Holt-Winters·Predictive)  (1건)

- Intelligent alerting with New Relic: Leveraging AI-powered alerting for anomaly detection and noise reduction — <https://newrelic.com/blog/ai/intelligent-alerting-with-new-relic-leveraging-ai-powered-alerting-for-anomaly-detection-and-noise>

### Google SRE  (1건)

- Top 7 AI SRE Tools for 2026: Essential Solutions for Modern Site Reliability — <https://stackgen.com/blog/top-7-ai-sre-tools-for-2026-essential-solutions-for-modern-site-reliability>

### 이상탐지·동적 baseline  (8건)

- Holt-Winters: Seasonal Forecasting Explained | MCP Analytics — <https://mcpanalytics.ai/whitepapers/whitepaper-holt-winters>
- Anomaly Detection with Holt Winters Simple Exponential Smoothing in Python | by İsmail Kağan Acar | Medium — <https://medium.com/@acarismailkagan/anomaly-detection-with-holt-winters-simple-exponential-smoothing-in-python-c5b11537b0a9>
- What Is Anomaly Detection? · Dash0 — <https://www.dash0.com/faq/what-is-anomaly-detection>
- Holt-Winters Algorithm & Anomaly Detection | VMware Avi Load Balancer - VMware Load Balancing & WAF Blog — <https://blogs.vmware.com/load-balancing/2024/03/20/holt-winters-algorithm-anomaly-detection-vmware-avi-load-balancer/>
- Data anomaly detection — <https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11921609>
- Data anomaly detection — <https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11341374>
- Building a real-time anomaly detection system for time series at Pinterest | by Pinterest Engineering | Pinterest Engineering Blog | Medium — <https://medium.com/pinterest-engineering/building-a-real-time-anomaly-detection-system-for-time-series-at-pinterest-a833e6856ddd>
- Best AI-Powered Observability Platforms for Anomaly Detection 2026 — <https://www.prompthalo.ai/feeds/blog/ai-powered-observability-platforms-anomaly-detection-2025>

### 자동복구·폐루프(Self-healing)  (5건)

- Building Self-Healing Infrastructure Using Observability, AIOps and Automated Incident Remediation | HackerNoon — <https://hackernoon.com/building-self-healing-infrastructure-using-observability-aiops-and-automated-incident-remediation>
- Closed-Loop Remediation & Self-Healing AIOps — <https://aicompetence.org/closed-loop-remediation-self-healing-aiops/>
- Self-Healing ITOps: Close the Loop From Detection to Resolution | LogicMonitor — <https://www.logicmonitor.com/blog/self-healing-itops-close-the-loop-from-detection-to-resolution>
- Self-Healing IT Operations Using AIOps and Incident Automation — <https://vitria.com/blog/self-healing-with-aiops/>
- Agentic AIOps in Action: LogicMonitor, IBM, and Red Hat Deliver Self-Healing IT | LogicMonitor — <https://www.logicmonitor.com/blog/agentic-aiops-self-healing-it-logicmonitor-ibm-red-hat>

### 도입 방법론·성숙도(Crawl-Walk-Run)  (9건)

- The Crawl-Walk-Run Approach to Nonprofit AI Adoption - Whole Whale — <https://wholewhale.com/resources/the-ai-crawl-walk-run-approach/>
- Crawl, Walk, Run: A Strategic Guide to Implementing Artificial Intelligence in Your Organization — <https://www.linkedin.com/pulse/crawl-walk-run-strategic-guide-implementing-your-mark-silver-tcuzc>
- Crawl, Walk, Run: A Practical AI Adoption Roadmap for Mid-Sized Companies - Thrive — <https://thrivenextgen.com/crawl-walk-run-a-practical-ai-adoption-roadmap-for-mid-sized-companies/>
- Understanding the 'Crawl, Walk, Run' Approach to AI Training — <https://www.simbo.ai/blog/understanding-the-crawl-walk-run-approach-to-ai-training-building-confidence-and-innovation-in-employees-941569/>
- 5 Steps to Kickstart AI Implementation for SMEs (Crawl, Walk, Run Framework) - Hatz AI — <https://hatz.ai/articles/5-steps-to-kickstart-ai-implementation-for-smes-(crawl-walk-run-framework)>
- Crawl, Walk, Run: A Practitioner's Guide to AI Maturity in the SOC | Microsoft Community Hub — <https://techcommunity.microsoft.com/blog/microsoft-security-blog/crawl-walk-run-a-practitioners-guide-to-ai-maturity-in-the-soc/4500433>
- AI Implementation and Enablement – Why the Crawl, Walk, Run Approach Always Wins | Framework IT — <https://www.frameworkit.com/blog/ai-implementation-and-enablement-why-the-crawl-walk-run-approach-always-wins>
- AIOps strategy: Key components and best practices - N-iX — <https://www.n-ix.com/aiops-strategy/>
- Crafting an Effective AIOps Strategy | xMatters - xMatters — <https://www.xmatters.com/blog/aiops-strategy>

### 학술 연구 (arXiv)  (6건)

- A Topology-Aware, Memory-Centric Architecture that Separates Root-Cause Derivation from Root-Cause Explanation — <https://arxiv.org/pdf/2606.20758>
- A Comprehensive Forecasting-Based Framework for Time Series Anomaly Detection: Benchmarking on the Numenta Anomaly Benchmark (NAB) — <https://arxiv.org/html/2510.11141v1>
- Fast and explainable clustering in the Manhattan and Tanimoto distance — <https://arxiv.org/html/2601.08781v1>
- Detecting Malicious Code by Exploiting Dependencies of System-call   Groups — <https://arxiv.org/pdf/1412.8712>
- Large-scale text processing pipeline with Apache Spark — <https://arxiv.org/pdf/1912.00547>
- NLP-Based Techniques for Cyber Threat Intelligence — <https://arxiv.org/pdf/2311.08807>

### 기타 (일반·산업 분석)  (12건)

- Ramp up carefully during AIOps implementation | TechTarget — <https://www.techtarget.com/searchitoperations/feature/Ramp-up-carefully-during-AIOps-implementation>
- Which AI Observability Tools Accelerate Root Cause Analysis? — <https://logz.io/blog/ai-powered-observability-tools-root-cause-analysis/>
- Best AIOps Platforms 2026: Top 10 Ranked and Compared | Nova AI Ops Blog — <https://novaaiops.com/blog/best-aiops-platforms-2026>
- Best Root Cause Analysis Tools in 2026 - Neubird — <https://neubird.ai/blog/root-cause-analysis-tools/>
- 10 best AIOps tools for 2026, ranked and reviewed - Guideflow Blog — <https://www.guideflow.com/blog/aiops-tools>
- AIOps in 2026: AI Monitoring & Incident Response | TechPlained — <https://www.techplained.com/aiops-explained>
- Case Study: How Enterprises Use AIOps to Cut MTTR by 40% | by Alexendra Scott | Medium — <https://medium.com/@alexendrascott01/case-study-how-enterprises-use-aiops-to-cut-mttr-by-40-576600a4215a>
- AIOps - All About AIOps: Use Cases, Benefits, and How to Get It Right — <https://hydrolix.io/glossary/aiops/>
- AiOps Automation in Action: Real-World Case Studies - AiOps Redefined!!! — <https://www.theaiops.com/aiops-automation-in-action-real-world-case-studies/>
- What Is AIOps | AI-Driven IT Operations Automation | Imperva — <https://www.imperva.com/learn/data-security/aiops/>
- AIOps & AI for IT Operations: From Reactive Intervention to Autonomous Action — <https://efs.consulting/en/insights/article/artificial-intelligence/aiops/>
- Top 15 AIOps Software Solutions to Transform Your IT Operations [2026] — <https://monday.com/blog/service/aiops-software/>

## 4. 조사↔산출물↔계획 추적

| 조사 관점 | 산출물 | collectorinfra 반영 |
|-----------|--------|---------------------|
| 역량 벤치마킹 | benchmark_brief, heatmap, comparison.csv | 갭 진단(토폴로지·변경·자동복구) |
| 도입 사례·방법론 | adoption_casestudy_brief, cases.csv, effects.png | 3-Wave 도입 권고 |
| 기능 구현 메커니즘 | mechanism_brief, decomposition.csv, noise_mechanism.png | 노이즈 게이트 실측 대조 |
| 벤더별 구현 기준 | vendor_implementation_reference(.html/.csv) | Plan 60 E1~E5 구현 방향 근거 |
| 구현 계획 | plans/60-noise-cancellation-benchmark-refinement.md | D-067~071(착수 전 사용자 확인) |

---
*이 문서는 조사 자료의 인덱스·출처 통합본이다. 각 분석의 상세 내용·표·다이어그램은 §2 산출물 파일 참조.*