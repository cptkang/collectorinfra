# 이벤트 자동 조사·진단 에이전트 — 문헌·기술 조사 dossier

> 조사일: 2026-07-21 · 대상: **Plan 64**(이벤트 자동 조사·진단 브리핑 및 장애 대응) 기획 근거
> 조사 관점: 각 소스를 **메커니즘 / read-only·폐쇄망·"트레이스 없는 모니터링 DB" 제약 정합성 / 채택 vs 회피**로 평가.
> 검증 원칙: 핵심 시스템은 실제 인용 검증 완료. 검증 실패·부분검증 항목은 §7에 명시. **날조 없음.**

---

## 1. LLM 기반 자동 장애조사·진단 에이전트

### HolmesGPT — Robusta + Microsoft 공동유지 (오픈소스, CNCF Sandbox 2025-10 편입)
- 출처: github.com/HolmesGPT/holmesgpt (검증됨)
- **정체**: 알람을 받아 read-only 진단 명령을 반복 실행하고 근본원인 가설을 합성하는 오픈소스 SRE 에이전트(LLM-agnostic, BYO-LLM).
- **메커니즘**: LLM이 알람 읽기 → 도구 선택 → 결과 관찰 → 다음 확인 대상 결정하는 **agentic ReAct 루프**. 30~50+ read-only toolset(Prometheus/Grafana/Loki/Datadog, k8s 로그·이벤트·상태 via kubectl, DB PostgreSQL/MySQL/MongoDB, PagerDuty/OpsGenie/Jira). 커스텀 toolset은 REST API.
- **트리거**: AlertManager/PagerDuty/OpsGenie/Jira에서 알람 fetch → 조사 → 결과를 Slack/Teams/GitHub/Jira에 write-back.
- **read-only 강제**: "By design, read-only access, respects RBAC, safe in production." **신규 Operator Mode는 Kubernetes Remediation toolset(MCP)을 별도 옵트인 추가**해 scaling/rollback 실행 — 즉 **조사(read-only)와 조치(write)를 분리된 옵트인 모듈**로 둠.
- **폐쇄망**: Ollama 로컬 모델 지원("telemetry never leaves perimeter"). **단 Ollama는 experimental·tool-calling 불안정**(LiteLLM 경유만) → 소형 로컬모델의 tool-calling 신뢰 금지 신호.
- **채택**: 조사/조치 분리·alert→fetch→조사→브리핑 write-back·toolset 추상화. **회피**: 소형 로컬 LLM에 루프 주도 위임.

### RCACopilot — Chen, Xie, Ma et al., *Automatic Root Cause Analysis via LLMs for Cloud Incidents* (EuroSys 2024)
- 출처: dl.acm.org/doi/10.1145/3627703.3629553 (검증됨)
- **정체**: 진단정보 자동수집 + LLM 근본원인 **카테고리 예측 + 설명**. Microsoft 30+팀 진단수집 4년+ 프로덕션.
- **메커니즘(2단계)**: ① **incident handler(수작업 결정트리)** — 액션 3종: Scope Switching(조사 입도 조정), **Query Action(스크립트/DB쿼리 → key-value/enum으로 흐름 제어)**, Mitigation Action("재시작"·"팀 소집"을 **제안만**). "only relevant data gathered"(과잉정보 억제). ② FastText 임베딩 + **시간감쇠 유사도**(1/(1+dist)·e^(−α|Δt|), α=0.3)로 과거 top-K=5 few-shot → GPT CoT로 카테고리+설명. 2000+토큰 진단데이터는 120~140단어 사전요약.
- **성능**: Micro-F1 0.766, 추론 4.2초/건, 수집 15~841초/건.
- **HITL**: OCE가 예측 검토, handler 구축·수정.
- **채택**: **결정적 handler로 증거번들 조립 → LLM은 분류+설명만**(프로젝트 "결정=결정적, LLM=보조" 정확 일치). 시간감쇠 유사도로 과거 유사장애 few-shot. **회피**: handler 완전 수작업만 두면 유지보수 부담.

### Ahmed et al., *Recommending Root-Cause and Mitigation Steps for Cloud Incidents using LLMs* (ICSE 2023)
- 출처: arxiv.org/abs/2301.03797 (검증됨) · Ahmed, Ghosh, Bansal, Zimmermann, Zhang, Rajmohan
- **정체**: 인시던트 제목·설명만으로 근본원인+완화 추천(첫 대규모 연구). 40,000+ 인시던트/1000+ 서비스, fine-tuned GPT-3.5, **OCE 70%+ "유용" 평가**.
- **시사점**: 도구 없는 텍스트-only baseline. collectorinfra엔 증거수집 결합형이 우월하나, "최소 입력에서도 LLM 브리핑이 가치있다"는 근거.

### Roy et al., *Exploring LLM-based Agents for Root Cause Analysis* (FSE 2024 Industry)
- 출처: arxiv.org/abs/2403.04123 (ar5iv HTML로 메커니즘·수치 전량 검증) · Roy, Zhang, Bhave, Bansal, Las-Casas, Fonseca, Rajmohan
- **메커니즘**: thought→action→observation ReAct(**최대 20 스텝**). 도구: Incident Details, Historical Incidents, **DB Query Tool(쿼리 실행+Pandas Q&A)**, KBA Q&A, KBA Planning, **Human Interaction Tool**. RCACopilot의 사전정의 handler 없이 **동적·자율 진단수집**.
- **핵심 발견 — 환각률**: ReAct **4~6%** vs CoT 18% vs 순수검색 baseline **49%**. "정밀도 최고, 대신 raw accuracy는 낮음." → **사실성이 중요한 운영 브리핑에 도구기반 검증이 환각을 결정적으로 억제.**
- **설계 권고**: ① **도구 실패 시 에러메시지를 에이전트에 노출**(침묵실패 금지), ② KBA(도메인지식) 핵심, ③ 복잡 인시던트는 단발 부족→reflection/장기메모리, ④ **HITL 필수**.
- **정합성**: DB Query Tool=모니터링 DB read-only 쿼리 그대로 매핑. Human Interaction Tool=renice/kill 인간승인 게이트 원형. 에러노출 권고=프로젝트 "침묵적 폴백 금지"와 동일. **회피**: 20스텝 자율루프의 raw accuracy 한계 → 결정적 플레이북으로 스텝 고정이 폐쇄망·소형모델에 안전.

### 보조
- **FLASH** (Microsoft 2024, microsoft.com/research FLASH_Paper.pdf, 검증됨): "Status supervision + Hindsight integration"으로 재발 인시던트 자동화, 250건 SOTA 대비 +13.2%. → 결정적 상태감독으로 LLM 워크플로 신뢰성 보강.
- **Nissist** (Microsoft, arxiv 2402.17531, 검증됨): 트러블슈팅 가이드 기반 완화 코파일럿, TTM 단축.
- **Survey**: Huang et al., *A Survey of AIOps for Failure Management in the Era of LLMs* (arXiv 2406.11213) — 분야 지도.

---

## 2. 산업계 자동 triage / enrichment / 증거 번들링

| 시스템 | 자동 수집물 | 조치 게이팅 | 폐쇄망 |
|---|---|---|---|
| **PagerDuty AIOps** | 호출 **전** 사전진단·enrichment: 로그·메트릭·헬스 수집, 유력원인 표면화, runbook·과거인시던트 첨부 | L0 자동 runbook은 진단과 **분리**, 원클릭 인간트리거 | SaaS |
| **Datadog Bits AI SRE** | 자율 반복: 가설→텔레메트리 수집→데이터기반 추론(메트릭·APM·로그·이벤트·Change Tracking·Watchdog·DBM) | 다음단계 제안+**원클릭** triage | **클라우드 SaaS — 에어갭 불가** |
| **Grafana Sift** | **고정 결정적 check 세트**: 에러로그 패턴·Kube 크래시/OOMKill·과부하 호스트·최근배포·리소스경합·느린요청 → "interesting results" | 조치 없음(진단전용), OnCall 웹훅으로 알람그룹당 자동조사 | 셀프호스트 가능 |
| **Cleric AI** | 알람시 다중소스 자동실행, **다중가설 병렬검증**(<2분) | **strictly read-only**, 모든 조치 **인간승인 필수** | API연동, "no data leaves system" |

> **collectorinfra 최적 모델 = Grafana Sift + Cleric**: Sift는 LLM 자율루프가 아닌 **결정적 check 목록으로 증거 큐레이션** → "결정적 우선"·폐쇄망·소형모델 제약에 최적. ①~④ 플레이북을 그대로 check 세트化. Cleric은 read-only+조치 인간승인 = renice/kill 게이팅 산업 레퍼런스. PagerDuty "호출 전 enrichment" = "PAGE 판정 시 자동조사→브리핑" 워크플로 위치와 동일.

---

## 3. 오퍼레이터 triage 방법론 형식화 (①~④ 매핑)

- **USE Method** (Brendan Gregg): 리소스마다 Utilization/Saturation/Errors 점검. Errors·Saturation 먼저(해석 쉬움)→Utilization.
- **60-Second Linux Perf** (Netflix 2015): uptime·dmesg·vmstat·mpstat·pidstat·iostat·free·sar·top — 처음 60초 에러·포화부터.
- **Google SRE Effective Troubleshooting**: 가설-연역법 **Triage→Examine→Diagnose→Test and Treat**. 함정: 무관 증상·지표 오독·과거원인 집착·우연한 상관을 인과로 착각.

**①~④ ↔ 형식 매핑**: ① 부하확인(top/uptime)=USE Utilization+Triage/Examine · ② 병목(us/sy·swap·wa)=CPU·메모리·디스크 **Saturation**+Examine · ③ 프로세스 격리(renice/kill 후보)=리소스→프로세스 드릴다운+**Diagnose** · ④ 로그분석(journalctl)=Errors+**Test and Treat**. → ①~④를 USE(리소스별 U/S/E)×SRE 4단계로 정형화해 **결정적 조사 그래프**로 코드화.

---

## 4. 에이전트/도구사용 + 안전 패턴

- **ReAct** (Yao et al., arXiv 2210.03629, ICLR 2023, 검증됨): thought↔action↔observation, 순수 action 대비 정확·해석가능·신뢰도↑. **시사점**: 관찰을 결정적 도구가 생성하면 소형 로컬 LLM으로도 안전 — LLM은 루프 주도보다 관찰의 **해석·요약**.
- **안전 패턴(업계 수렴 표준)**: read-only 도구 + 변경액션 인간승인 (HolmesGPT/Cleric/Roy Human Interaction Tool/PagerDuty 진단·조치 분리). **renice/kill은 WRITE → read-only 원칙 충돌 → 자동실행 금지, "권고안"으로만 노출 + 인간승인 게이트**. (top/journalctl은 read-only OS 명령 → 자동수집 허용; DB read-only를 "read-only 진단명령"으로 확장하는 것은 HolmesGPT 선례와 일치.)

---

## 5. LLM/결정적 결합 심각도 스코어링

- **결정적 severity matrix**: "same inputs → same SEV level"(SEV-0~3, 객관적 기술영향). **결정적=결정, LLM=보조** 정확 부합.
- **적용**: 노이즈 게이트가 이미 PAGE/TICKET/DASHBOARD/SUPPRESS를 결정적 판정 중 — 그 위에 **결정적 severity 매트릭스(메트릭 임계·토폴로지·importance 메타)로 SEV 산출**, LLM은 SEV를 못 뒤집고 **브리핑 서술·urgency 근거**만 담당.

---

## 6. 폐쇄망/에어갭 실현성

- **Kim et al. (CoS Lab), *LLM-based AIOps via Log Prioritization in Air-Gapped Systems* (EuroMLSys '26, 2026-04-28)** — dl.acm.org/doi/10.1145/3805621.3807626 (ACM 등재 검증, **본문 PDF는 403으로 미확인 — 초록·수치는 검색 스니펫 기반, 본문 인용 전 재확인 권장**): raw 로그 직접투입은 비현실적 → **규칙기반 변환→구조화 이벤트→시간집계→결정적 우선순위화**로 이벤트 51% 감축, **LLM 토큰 43% 절감** 후 로컬 LLM 진단. → 결정적 전처리로 증거 압축·선별 → 로컬 LLM은 마지막 해석만.
- **로컬모델**: 오픈웨이트 1B~8B가 16GB RAM/8GB GPU 오프라인 구동, Ollama가 에어갭 표준. **단 소형모델 tool-calling 불안정(HolmesGPT 실측)** → 에이전트 루프는 결정적 코드가 주도, 로컬 LLM은 요약·분류·심각도 서술 단발추론만.

---

## 7. 종합 아키텍처·테이크아웃·근거 강도

### 종합 — 문헌이 가리키는 아키텍처
**RCACopilot/Sift형(결정적 수집) + Roy/HolmesGPT형(도구 관찰로 환각 억제) + 로그우선화형(결정적 압축 후 로컬 LLM)** 하이브리드:

```
[노이즈 게이트: PAGE 판정 (기존, 결정적)]
 → [결정적 조사 오케스트레이터]  ← RCACopilot handler / Sift check / USE·SRE 4단계
     ①부하 ②병목 ③프로세스 ④로그 (read-only 모니터링 DB 쿼리 + read-only OS 명령)
     · 각 스텝=고정 플레이북, 관찰은 결정적 도구가 생성 · 도구 실패는 사유 구조화 노출
 → [결정적 압축·우선순위화]  ← 로그우선화 논문 (토큰 43%↓)
 → [결정적 severity 매트릭스 → SEV 등급 (=결정)]
 → [로컬 LLM: 단발 해석만]  브리핑 서술 + SEV urgency 근거 (결정 불변)
 → [오퍼레이터 브리핑]  + renice/kill = "권고안"만, 인간승인 게이트 (write 금지)
```
**핵심: LLM에 조사 루프 주도권을 주지 않는다.** 조사·심각도 결정은 결정적 코드, LLM은 마지막 해석·서술만.

### 채택 가능한 설계 테이크아웃 6
1. **조사 플레이북을 결정적 조사 그래프로 코드화**(RCACopilot handler + Sift check). ①~④를 USE×SRE로 정형화, 각 노드는 read-only DB 쿼리 또는 read-only OS 명령. LLM은 루프 미주도.
2. **read-only 기본 + 조치 분리 게이트**(HolmesGPT Operator Mode/Cleric/PagerDuty). renice/kill 자동실행 금지, "권고안+근거"로만 노출+인간승인.
3. **결정적 severity 매트릭스가 SEV 결정, LLM은 서술만**.
4. **증거를 결정적으로 압축 후 로컬 LLM 투입**(에어갭 로그우선화). 구조화 이벤트로 정규화·선별해 토큰 절감.
5. **도구 실패를 침묵시키지 말고 사유 구조화 노출**(Roy 권고 + 프로젝트 기존 원칙).
6. **HITL를 1급 도구로**(Roy Human Interaction Tool). 조사 애매/조치 필요 시 오퍼레이터에 명시 이관.

### 근거 강도
- **잘 뒷받침됨(peer-reviewed/프로덕션 실측)**: RCACopilot 2단계·Micro-F1 0.766·MS 4년+운영(EuroSys 2024) · Roy 환각률 4~6% vs 검색 49%(FSE 2024) · Ahmed 40,000+·OCE 70%+(ICSE 2023) · 에어갭 로그우선화 이벤트 51%·토큰 43%(EuroMLSys 2026, 본문 재확인 전제) · USE/60초/SRE(확립된 방법론) · ReAct(ICLR 2023) · HolmesGPT read-only·CNCF Sandbox(오픈소스 검증가능).
- **벤더 마케팅/불확실(자체발표·독립검증 없음)**: Datadog Bits "90% faster", Cleric "<2분", PagerDuty "context flywheel" — **메커니즘 패턴은 신뢰, 성능수치 검증불가**. Datadog/Cleric은 크로스-테넌트 데이터 의존 → **폐쇄망 직접 이식 불가**. "소형 로컬모델로 충분" 블로그 — 하드웨어 요건은 사실이나 **tool-calling 신뢰성은 HolmesGPT 실측이 반박** → 로컬모델 루프주도 회피.
- **검증 실패/부분검증**: (a) 에어갭 로그우선화 본문 PDF는 ACM 403 미확인(초록·수치는 스니펫). (b) Roy arxiv PDF 파싱 실패했으나 ar5iv HTML로 전량 검증. 그 외(HolmesGPT·RCACopilot·Ahmed·Roy·FLASH·ReAct)는 실제 인용 검증 완료.

### ⚠ 아키텍처 긴장 (Plan 64 D-102 근거)
위 문헌 대다수는 마이크로서비스/K8s + 로그·트레이스·APM 환경. collectorinfra는 **트레이스 없이 모니터링 DB에 메트릭 시계열만** 있고, ①~④ 플레이북(top/journalctl/renice)은 **모니터링 DB가 아니라 대상 호스트의 라이브 OS 상태**를 요구. 즉 조사 데이터 소스가 **두 갈래**(과거 메트릭=DB read-only 쿼리 / 현재 OS 상태=호스트 read-only 명령)로 갈리며, 후자는 "DB read-only" 범위를 넘어 **read-only 실행 계층**을 새로 정의해야 함 — HolmesGPT read-only toolset 확장이 선례. **이 실행 계층의 존재·경계는 명시적 의사결정(D-번호) 대상** → Plan 64 D-102·§7로 반영.

---

## 8. 메시지 기반 타깃 보강·도구증강 트리아지 (Plan 64 §4.8 근거 · 2026-07-21 추가)

노이즈 억제 후 **생존 통보를 이벤트 메시지 분석 기반으로 타깃 보강**(어느 프로세스가 원인인지 등 자동 조회·첨부)하는 요건의 근거. §1(RCACopilot 2단계·HolmesGPT read-only tool)이 백본이고, 아래가 "메시지→무엇을 조회할지 결정 + 도구로 수집"을 직접 뒷받침한다.

- **StepFly** (Mao et al., Microsoft, arXiv 2510.10074, 2025) — LLM이 오프라인에서 트러블슈팅 가이드를 **DAG로 구조화** + 온라인 **결정적 스케줄러 실행**(병렬). GPT-4.1 ~94% 성공·실행시간 32.9~70.4%↓. → **"LLM=분류/구조화, 실행=결정적"(D-035)** 직접 근거. [검증: arXiv 초록·방법]
- **CORTEX** (Wei et al., arXiv 2510.00311, 2025) — 멀티에이전트가 **외부 시스템을 도구로 조회해 증거 수집→감사가능 판정**(behavior/evidence/reasoning 역할 분리). 도메인 SOC 보안. → 증거기반·글래스박스 인용 근거. [검증: arXiv 초록·방법]
- **LLM-IRAgent** (JRPS 2025) — 정책구동 LLM, SOC 플레이북→**triage·enrichment·containment 권고** 구조화 추론. [검증: 스니펫]
- **Autonomous Alert Triage w/ Tool-Augmented Reasoning** (IJSRCSEIT 2026) — MCP식 표준 도구통합·**다소스 동적 조회·실시간 증거수집**. [검증: 스니펫]
- **산업 기술자료**: Datadog *Actionable Alerting* · Elastic *investigation guides*(알람 임베드 컨텍스트 플레이북) · Rootly(noise→actionable). [벤더 발표·수치 미인용]

**검증·한계**: StepFly·CORTEX는 arXiv 초록·방법 확인(2025). 나머지는 검색 스니펫 기준(본문 독립검증 아님). **도메인 다수가 SOC 보안** → 인프라 알람(CPU/메모리/디스크) 적용 시 신호·도구 차이 유의.
