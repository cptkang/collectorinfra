# 50. 장애진단 · 원인분석 (Fault Diagnosis & Root Cause Analysis)

> 작성일: 2026-06-26
> **상위 로드맵**: **Plan 62(AIOps 전체 역량 마스터 로드맵) — Phase P2(진단·RCA, 핵심)**. 선행자산: Plan 60 E4 토폴로지 그래프(P1). 대응 벤치마크 역량 C4(RCA 상관→인과).
> **관련 Plan**: Plan 44(alarm_query 의도), Plan 46(알람 소켓 수신), Plan 47(알람 이력 패턴 분석),
> Plan 47-1(영향 프로세스 보강), Plan 48/49(의도 분해 오케스트레이션)
> **관련 결정**: D-029(알람 의도 분리), D-030(해소 이력 포함), D-031(알람 파이프라인), D-032(알람 메시지 포맷),
> D-035(알람 이력 패턴 분석), D-036(영향 프로세스 보강), D-037(오케스트레이션) /
> 착수 시 신규 결정 등재 — **D-038은 이미 소진**(`docs/02_decision.md:313`) → §14 개정 블록 참조
> **상태**: **잔여 구현 완료(v2.2, 2026-09-02 · D-197) — G1~G6 해소, 실 조사 e2e(D-127)·Phase C′만 남음**. 모듈 맵 `CAPABILITY-MAP-50.md` · 스펙 `SPEC-{briefing-contract,incident-window-tools,investigation-guidance,incident-scope,evidence-correlation,diagnosis-briefing}.md` · 태스크 `tasks/plan-50.md`·`tasks/todo-50.md`. 직전 상태(v2.1): 부분 구현 — 잔여 재측정 + 소유권 확정(§0). 본문 §1~§17은 2026-06-26 원안이며 **처분은 §0.5 표가 정본**이다. 이전 판정(2026-08-31 · `plans/85` §4 A-1): **조사 실행·증거 수집·LLM 인과·리포트·pull/push 트리거는 `sre_agent` 위임 방식으로 구현 완료** — `src/nodes/fault_diagnosis.py`(pull · D-124 CW-B) · `noise_gate/.../investigation_trigger.py`(push · D-124 CW-A) · `mcp_server` 조사 도구 8종 + PromQL 7종(D-122·D-119) · `sre_agent` `DiagnosisAgent`·`briefing_builder`(D-118·D-123). **`src/diagnosis/` 자체 서브그래프는 만들지 않는다**(D-118 위임 — 재편은 이미 실행됐다). **진짜 잔여 = 결정적 상관 축 5건**: §6.1 통합 타임라인 병합 · §6.2 metric_anomaly(z-score·지속성·선행성) · §6.4 CorrelationResult(leading_signal·notes) · §7.2 복수 가설 rank·confidence · §9.1 상대시각 타임라인. 공통점은 **LLM이 못 하는 결정적 계산**이라는 것이며(D-035 취지), 착수 범위는 **`sre_agent` 도구 출력을 입력으로 받는 순수 함수 계층**으로 좁혀진다. 절별 대조표는 `plans/85` §4 A-1 참조.
> **v2 개정(2026-09-01)**: 그 5건을 **코드 시그니처에 대고 다시 측정**한 결과 **선행 갭 G1이 드러났다** — `sre_diagnose`·`polestar_metric_trend`·`polestar_alarm_history`·`prom_metric_range` 어디에도 **사건 시각 인자가 없어**(grep 0건) 증거 자체를 사건 구간에서 가져올 수 없다. §3.4의 *"now() 금지"* 가 도구 계약 수준에서 깨져 있으며, **상관 엔진만 만들어도 입력이 now 앵커면 무의미하다**. 여기에 **G6 브리핑 키 계약 불일치**(생산 `limitations`/list ↔ 소비 `limitation` — 한계가 사용자에게 도달하지 않음, pull·push 양쪽 동일)가 더해져 잔여는 **6건**이 됐다. 갭·배치·절별 처분·착수 단계는 **§0**에 있다.
> **v2.1 개정(2026-09-02)**: 잔여의 **소유권을 확정**했다 — 상관 계산·증거 사전수집은 **`sre_agent` 안에 둔다**(별도 최상위 폴더도 `mcp_server`도 아니다). 결정적 근거는 **원시 도구 출력이 `sre_agent`를 벗어나지 않는다**는 실측(poll은 `briefing` + description 목록만)이며, Plan 50 §5의 5노드 파이프라인이 **이미 `sre_agent`의 파이프라인**이라는 대조(§0.4-b)가 이를 뒷받침한다. `mcp_server`에는 **구간 앵커 SQL(G1~G3)만** 남는다(읽기 경계).

---

## 0. 2026-09-02 개정 — **SRE Agent 보완 관점 재작성** (v2.1)

> **요구(사용자, 2026-09-01)**: *"50번의 RCA 구현을 위해 현재 구현되어 있는 SRE Agent의 기능을
> 보완하기 위해 50번 계획을 차용하거나 수정해야 될 사항이 있는지 확인하여 50번 계획을 업데이트하라."*
>
> `plans/85` §4 A-1이 잔여를 *"결정적 상관 축 5건"* 으로 좁혔다. 본 개정은 그 5건을 **실제
> `sre_agent`·`mcp_server`·`src`·`noise_gate` 코드에 대고 다시 측정**해 ①무엇이 비어 있는지
> ②Plan 50의 어느 절을 그대로 차용할 수 있는지 ③어느 절을 폐기해야 하는지 ④신규 코드가
> **어느 패키지에** 들어가야 하는지를 확정한다.
>
> **§1~§17 원문은 이력으로 보존**한다. 절별 처분은 **§0.5 표가 정본**이고, 각 절 머리의 개정
> 블록은 그 표를 가리키는 요약이다(`plans/85` §1 교훈 — 같은 사실을 두 곳에 쓰면 어긋난다).
>
> **v2.1(2026-09-02)에서 추가된 것**: 사용자 질의 *"50번 계획의 전체적인 목표를 정리하고,
> 이 기능이 별도 폴더가 아닌 sre agent에 포함되어야 되는 기능인지 분석하라"* 에 따라
> **§0.0(목표 재정의)** 과 **§0.4-b(소유권 분석)** 를 신설하고, v2가 `mcp_server`로 잡았던
> 상관 계산의 소재지를 **`sre_agent`로 정정**했다(§0.4-a). 이어진 질의 *"mcp_server와 sre_agent
> 각각의 역할이 뭐냐"* 에 따라 **§0.2-a(패키지 역할 지도 · 런타임 호출 사슬)** 를 신설했다.
> 정정 사유는 §18 변경 이력 참조.

### 0.0 이 계획의 목표 — 원안 3층과 **잔여 1문장**

원안(2026-06-26)이 세운 목표는 **세 층의 전환**이다.

| 층 | 전환 | 원안 절 |
|---|---|---|
| **분석 단위** | "알람 이벤트 1건" → **"리소스 × 시간 구간의 사건(incident)"** | §1.1 · §3.4 |
| **입력 신호** | 단일 알람 + 동일알람 이력 → **사건 구간의 다중 신호**(알람 다발·메트릭 시계열·프로세스·토폴로지·변경) | §1.5 · §4 |
| **결론의 형태** | 추정 원인 1줄 → **근거 인용 + 신뢰도가 붙은 원인 가설 순위** | §7.2 · §9.1 |

이를 관통하는 원칙이 §3.3 **"수치·상관은 Python(결정적), 인과 해석만 LLM"** 이고(D-035 계승),
성공 기준은 한 문장으로 압축된다 — **"X 서버 어제 14시쯤 장애 원인 분석해줘"에 답한다**(§1.4-1).

**2026-09-02 시점에 이 목표는 무엇으로 축소되었는가.** 실행 축(증거 수집·LLM 인과·리포트·
pull/push 트리거)은 `sre_agent`가 가져갔다(D-118 · §0.2). 남은 목표는 하나다:

> **조사에 시간 좌표계와 결정적 상관 계산을 부여해, "무엇이 먼저 일어났는가"를
> 서술이 아니라 계산으로 답하게 만든다.**

이 한 문장이 Plan 50이 아직 살아 있는 이유의 전부다. *"무엇이 먼저 일어났는가"* 는 **계산해야
알 수 있고 서술로는 확인할 수 없다** — `briefing_builder`의 인용 검증이 *"근거 없는 단정"* 은
막아도 *"순서를 잘못 말한 단정"* 은 막지 못한다(인용된 두 라인의 선후는 검증 대상이 아니다).

### 0.1 실측 방법 (재현 명령)

`plans/85` §2 축 4(기능 축 대조)를 잇되, **대조 대상을 "계획서 절"이 아니라 "도구·함수 시그니처"**로
바꿨다. 절 단위 판정("타임라인 병합 없음")은 무엇을 만들지 알려주지 않기 때문이다.

```bash
cd /Users/cptkang/AIOps/collectorinfra
# ① 증거 수집 도구의 시간 인자 — 사건 구간을 지정할 수 있는가
grep -rn "reference_time\|window_start\|incident_window" mcp_server/mcp_server/ sre_agent/sre_agent/   # → 0건
sed -n '117,120p' mcp_server/mcp_server/polestar_tools.py     # _time_floor → NOW() - INTERVAL
sed -n '250,296p' mcp_server/mcp_server/polestar_tools.py     # build_metric_trend_sql → ORDER BY DESC LIMIT n
sed -n '277,306p' mcp_server/mcp_server/promql_tools.py       # run_metric_range → end = time.time()
# ② 위임 계약이 기준시각을 나르는가
sed -n '185,203p' sre_agent/sre_agent/interface/mcp_service.py  # sre_diagnose 인자
sed -n '77,92p'   sre_agent/sre_agent/interface/mcp_service.py  # _job_to_question
sed -n '33,95p'   noise_gate/domain/investigation_payload.py    # payload.event.alarmTime 실재
# ③ 브리핑 생산자 ↔ 소비자 키 계약
grep -n "return {" -A 12 sre_agent/sre_agent/application/briefing_builder.py | tail -14
grep -n "_BRIEFING_ORDER" src/nodes/fault_diagnosis.py
grep -n "ordered = \[" noise_gate/application/nodes/alarm_notifier.py
# ④ 조사 지침 주입 배선
grep -n "system_prompt_additions" sre_agent/sre_agent/interface/mcp_service.py   # → 0건(미전달)
# ⑤ 품질 게이트 스캔 경계(신규 코드 배치 제약)
sed -n '55,75p' scripts/overfit_check.py
```

### 0.2 SRE Agent가 **이미 하는 일** — 재구현 금지 목록

| 기능 | 구현 | 판정 |
|---|---|---|
| 조사 실행 루프(ReAct) | `sre_agent/diagnosis.py::DiagnosisAgent` (holmesgpt 0.36.0) | Plan 50 §7 `causal_reasoner`를 **대체** |
| 결정적 폭주 가드 6종(가용성·dedup·동시·타임아웃·예산·in-flight) | `application/investigation_dispatcher.py` | Plan 50에 없던 자산 — **그대로 둔다** |
| 도구 원시 출력 보존 | `diagnosis.py::ToolCallRecord.output` (`get_stringified_data()`) | §6 상관 엔진의 **입력원** |
| 인용 검증·가설 강등 | `briefing_builder._is_cited` | §7.3 요구를 **초과 충족** |
| 시그니처 기반 심각도 판정 | `domain/severity_signatures.judge` | §6.3 "병목" 축을 부분 충족 |
| 조치 권고(제시 전용) | `domain/remediation.recommend_lines` | §9 권고를 충족(D-138) |
| 폴스타 고정 SQL 도구 8종 + PromQL 7종 | `mcp_server/polestar_tools.py`·`promql_tools.py` | §3.2 "고정 템플릿" 원칙을 **이미 실현** |
| pull/push 트리거 | `src/nodes/fault_diagnosis.py` · `noise_gate/.../investigation_trigger.py` | §8.1·§8.2 **구현 완료** |
| 인가·가용성 사전 게이트 | `fault_diagnosis.py` (D-175·Plan 78 W3-5) | Plan 50에 없던 자산 |

> **따라서 Plan 50이 남긴 일은 "진단 시스템을 만드는 것"이 아니라 "이미 도는 조사에
> 결정적 시간 좌표와 결정적 계산을 넣는 것"이다.**

### 0.2-a 패키지 역할 지도 — `mcp_server` ↔ `sre_agent`

> 두 패키지 모두 **MCP 서버**라 혼동하기 쉽다. 차이는 *"무엇을 서빙하는가"* 다.
> 본 계획의 잔여가 이 둘에 나뉘어 배치되므로(§0.4), 그 전에 역할을 못 박는다.

| | `mcp_server/` | `sre_agent/` |
|---|---|---|
| **역할** | **관측 데이터 읽기 경계** — 데이터를 꺼내 준다 | **장애 조사 실행자** — 꺼낸 데이터로 판단한다 |
| **근거 결정** | D-119(읽기 접근 경계 일원화) · D-122(고수준 도구 정책) | D-118(조사 기능 독립 패키지) |
| **포트** | **9099** (SSE · `mcp_server/config.py:28`) | **9098** (SSE · `interface/mcp_service.py:31`) |
| **LLM** | **없음** — 순수 데이터 서버 | **있음** — HolmesGPT ReAct 루프 |
| **소비자** | **둘** — `sre_agent` + **collectorinfra 본체**(`DB_BACKEND=dbhub`) | **하나** — collectorinfra 본체 |
| **런타임** | 별도 프로세스 · **자체 venv 없음(루트 공유)** | 별도 프로세스 · **자체 venv**(Python 3.13 · holmesgpt 0.36.0) |

#### `mcp_server/` — 데이터를 *어떻게* 꺼낼지 아는 쪽

DB 방언·스키마·조인 규칙을 **소비자 대신 알고 있는 서버**다. 소비자는 SQL 텍스트를 넘기지 않고
**값 인자만** 넘긴다(D-122). 도구 **19종**(실측 `grep -c "@mcp.tool"` · **v2.2: `polestar_incident_alarms` 신설로 20종**):

| 파일 | 수 | 내용 |
|---|---|---|
| `tools.py` | 4 | 범용(`execute_sql`·`list_sources` 등 — 조사 배치에서는 비노출, D-122) |
| `polestar_tools.py` | **8** | 고수준 폴스타 조회(`polestar_metric_trend`·`polestar_alarm_history`·`polestar_topology`·`polestar_process_snapshot`·`polestar_resource_status`·`polestar_condition_log`·`polestar_os_config`·`polestar_change_history`) |
| `promql_tools.py` | **7** | Prometheus(`prom_metric_instant`·`prom_metric_range` + 원시 5종은 옵트인) |

책임: PostgreSQL/DB2 **방언 분기**(`LIMIT` vs `FETCH FIRST`·스키마 대소문자) · **읽기 전용 강제**
(D-003) · timeout·max_rows · **프로세스 args 마스킹**(서버가 하므로 우회 불가) ·
반환 계약 `{rows, row_count, queried_at, source_kind}` 또는 `{error}`.

> **주의 — 공유 경계다.** 운영 `.env`가 `DB_BACKEND=dbhub`이므로 **text2sql 파이프라인도 이 서버를
> 쓴다.** 조사 전용이 아니다. 이것이 §0.4-a에서 상관 계산을 여기 두지 않기로 한 근거 ⓓ다.

#### `sre_agent/` — 꺼낸 데이터로 *판단*하는 쪽

HolmesGPT를 감싸고 그 위에 **결정적 통제**를 씌운 조사 서비스다.

| 계층 | 모듈 | 하는 일 |
|---|---|---|
| domain | `severity_signatures.py` | 도구 **원시 출력**에 시그니처 매칭 → 심각도 판정(**상향 전용**) |
| domain | `remediation.py` | 조치 권고 도출 — **제시만**(실행 경로 없음 · D-011) |
| application | `investigation_dispatcher.py` | 폭주 가드 6종(대상 가용성·dedup TTL·동시 상한·전체 타임아웃·시간당 예산·호스트 in-flight) |
| application | `investigation_jobs.py` | submit/poll 잡 저장소 |
| application | `briefing_builder.py` | 6요소 브리핑 결정적 조립 + **인용 없는 단정을 `[가설]`로 강등** |
| (코어) | `diagnosis.py` | `DiagnosisAgent` — HolmesGPT ReAct 루프 · 원시 출력 보존(`ToolCallRecord`) |
| interface | `mcp_service.py` | MCP 도구 **5종**(`sre_diagnose`·`sre_investigate_alarm`·`sre_get_investigation`·`sre_list_investigations`·`sre_health`) |

핵심 설계: **LLM은 증거 수집과 서술만 하고, 트리거·중복·동시성·타임아웃·예산·심각도는 전부
코드가 결정한다**(D-035). 본 계획의 잔여(§0.3 G4)는 이 "코드가 결정하는" 목록에
**시간 좌표와 상관 계산을 추가**하는 일이다.

#### 런타임 호출 사슬

```
사용자 "웹서버 원인 분석해줘"              폴스타 알람 수신
        │                                        │
        ▼                                        ▼
  src/nodes/fault_diagnosis.py      noise_gate/…/investigation_trigger.py
  (인가 · 가용성 게이트)                     (게이트 판정 후)
        └──────────────────┬─────────────────────┘
                           │ MCP :9098  sre_diagnose / sre_investigate_alarm
                           ▼
              ┌──────────────────────────────────┐
              │  sre_agent        [조사 소유]      │
              │  JobStore → dispatcher(가드 6종)   │
              │        → DiagnosisAgent (LLM)     │
              └────────────────┬─────────────────┘
                               │ MCP :9099  polestar_* / prom_*
                               ▼        (ReAct 루프에서 LLM이 도구 선택)
              ┌──────────────────────────────────┐
              │  mcp_server       [읽기 경계]      │ ◄── text2sql 파이프라인도 여기로
              └────────────────┬─────────────────┘
                               ▼
                  PostgreSQL · DB2 · Prometheus   (읽기 전용)
```

**되돌아오는 것은 비대칭이다** — `mcp_server`는 **rows JSON**을 돌려주지만, `sre_agent`가 본체에
돌려주는 것은 **브리핑 + 도구 호출 description 목록**뿐이다(`InvestigationJob.summary()`).
**원시 rows는 `sre_agent`를 벗어나지 않는다** — 이것이 §0.4-a 실측 ①이며, 별도 최상위 폴더를
폐기한 결정적 근거다(§0.4-b).

#### 왜 나눠 놓았나

1. **런타임 격리** — 본체는 Python ≥3.11 + LangGraph 스택, `sre_agent`는 ≥3.13 + holmesgpt 스택.
   한 venv에 섞으면 의존성이 충돌한다(D-118 ②).
2. **양방향 import 0** — `sre_agent/tests/test_boundary.py`가 단언으로 고정한다. 통신은 MCP 계약뿐.
3. **분리 가능성** — D-118 ④ *"분리 절차 = 폴더 복사 + URL 설정 변경(계약·코드 무변경이 회귀 기준)"*.

> **부기(문서 불일치 · 2026-09-02 실측)**: CLAUDE.md 「저장소 지도」는 `mcp_server/`를
> *"별도 venv·별도 프로세스"* 로 적었으나 **`mcp_server/.venv`는 존재하지 않는다**(루트 venv 사용).
> 같은 CLAUDE.md의 「개발 명령」은 *"자체 venv 없음 — 루트 venv 사용"* 으로 맞게 적혀 있다.
> 본 계획의 판단은 **실측(자체 venv 없음)** 을 따른다. CLAUDE.md 정정은 본 계획 범위 밖이다.

### 0.3 ★ 실측 갭 6건 — Plan 50이 보완할 지점

> **★ v2.2 해소 실측(2026-09-02 · D-197)** — 아래 6건은 전부 구현·테스트로 닫혔다. 갭 서술은 착수 근거로 보존한다.
>
> | 갭 | 해소 | 검증 |
> |---|---|---|
> | **G1** | `polestar_metric_trend`·`polestar_alarm_history`에 `reference_time`·`lookback_minutes`, `prom_metric_range`에 `reference_time`(end 앵커), `sre_diagnose`에 시각 인자. **미지정 시 SQL 스냅샷과 문자열 동일** | `mcp_server/tests/test_incident_window.py` · `sre_agent/tests/test_incident_scope.py` |
> | **G2** | `baseline_periods` — 사건 직전 N단위를 같은 쿼리에 포함, 응답 `window.stat_date_incident_from`으로 분리 | 같은 파일 · `test_evidence_prefetch.py` |
> | **G3** | `polestar_incident_alarms` 신설(알람명 선택 · `CTIME ASC`) — 도구 19종 → **20종** | `test_incident_window.py` · `test_tool_gates.py` |
> | **G4·G4-b** | `sre_agent/domain/correlation.py`(§6.1~6.4 순수 함수) + `application/evidence_prefetch.py` + `infrastructure/mcp_tool_client.py`(B′ 선행 — holmes `Tool.invoke`가 LLM 객체 필수라 우회). `EVIDENCE_CORRELATION_ENABLED` 기본 off | `test_correlation.py`(골든: 디스크IO T-15m → CPU T-12m → 알람 T-10m ⇒ leading=disk_io) · `test_evidence_prefetch.py` · `test_mcp_tool_client.py` · dispatcher 3건 |
> | **G5** | `_default_diagnose_fn`이 `build_guidance()`를 `system_prompt_additions`로 전달(원격 셸 안내 · 사건 구간 · 상관 결과 · `INVESTIGATION_GUIDANCE_EXTRA`) | `test_investigation_guidance.py` |
> | **G6** | 정본=생산자, 공용 렌더러 `noise_gate/domain/investigation_briefing.py`, 소비자 2곳 같은 커밋 정렬, 스텁 비트 동일, 모르는 키 침묵 누락 금지 | `tests/test_briefing_contract.py`(16) ↔ `sre_agent/tests/test_briefing_builder.py` 대칭 리터럴 |
>
> §7.2·§9.1은 `root_cause_hypotheses`(rank·confidence·evidence·reasoning)·상대시각 타임라인으로 `briefing_builder`에 반영됐다(`SPEC-diagnosis-briefing.md`).

> `plans/85` §4 A-1의 ★ 5건을 코드 시그니처에 대고 다시 나눈 결과다. **G1이 §85의 5건 전부의
> 선행 조건**이라는 것이 이번 실측의 핵심 발견이며, §85는 이것을 잡아내지 못했다.

#### **G1 (선행·최상위)** — 증거 수집 계약에 **사건 시각 좌표가 없다**

Plan 50 §3.4는 *"현재 시각(now) 기준 금지 — 지연 처리에도 일관되게"* 를 못 박았다. **현행 구현은
그 반대다.** 사건 구간을 지정할 방법이 계약 어디에도 없다:

| 지점 | 실측 | 결과 |
|---|---|---|
| `sre_diagnose(question, server_name, hostname, db_id, target_state)` | 시각 인자 **없음** (`mcp_service.py:186`) | 위임 계약이 기준시각을 못 나른다 |
| `_job_to_question` (`mcp_service.py:77`) | pull=`job.question`(user_query 원문) / push=서버명만. **`payload.event.alarmTime`을 보유하고도 질문에 싣지 않는다** | 조사 LLM이 사건 시각을 모른다 |
| `build_metric_trend_sql` (`polestar_tools.py:250`) | `ORDER BY s.stat_date DESC` + `LIMIT n` = **최신 N개** | 과거 구간 조회 불가 |
| `build_alarm_history_sql` (`:205`) | `CA.CTIME >= _time_floor(hours)` → `NOW() - INTERVAL 'h hours'` (`:117`) | now 앵커 |
| `run_metric_range` (`promql_tools.py:277`) | `end = time.time()` | now 앵커 |

**파급**: *"어제 14시쯤 장애 원인 분석해줘"* 라는 §1.4 성공기준 1의 예시 질의가, **증거를 그
구간에서 가져올 수단 자체가 없어** 최신 데이터로 답한다. push도 워커 지연·재처리 시 동일하다.
**상관 엔진(§6)만 만들어도 입력이 사건 구간이 아니면 계산 결과가 무의미하다** — G1이 먼저다.

#### **G2** — baseline 조회 수단이 없다

§6.2 z-score는 *"사건 직전 동시간대 N기간의 μ·σ"* 를 요구한다. `polestar_metric_trend`는 **연속
단일 구간**만 돌려주므로 baseline 구간을 따로 뽑을 수 없다. `noise_gate/domain/anomaly.py`
(Holt-Winters·D-079)가 동적 baseline을 계산할 수 있으나 **알람 게이트 전용이고 진단 경로 소비 0건**이며,
패키지 경계(D-118·D-139)상 `sre_agent`가 import할 수 없다.

#### **G3** — 사건 구간 **전체 알람** 조회가 없다

`polestar_alarm_history(source, server_name, **alarm_name**, hours, ...)` — `alarm_name`이 **필수**이고
SQL은 `D.NAME = <lit>` 완전 일치다. 즉 *"같은 알람의 과거 이력"* 만 가져온다. Plan 50 §4.1의
`build_incident_alarms_sql`(구간 내 **모든** 알람, alarm_name 미지정)에 해당하는 도구가 없어
**§6.1 타임라인 병합의 알람 축이 구조적으로 비어 있다.**

#### **G4** — 결정적 상관 계층 부재 (= `plans/85` ★ 5건 본체)

`briefing_builder.build_briefing` 실측:
- `timeline`: `[c for c in claims if _is_cited(c, tool_names)]` — **인용된 서술 라인 필터**이지
  시각 좌표 병합이 아니다. 정렬·상대시각·선후 개념이 없다.
- `cause`: `cited[-1]` — **단일 문자열**. `rank`·`confidence` 없음(§7.2 미충족).
- `leading_signal`·`notes`(데이터 한계의 결정적 기록) 없음(§6.4 미충족).
- `limitations`는 있으나 **고정 문구 + verdict 플래그**이지 정밀도·단면 여부의 계산 결과가 아니다.

재사용 가능한 결정적 자산은 `noise_gate/domain/`에 있으나(`anomaly.py`·`topology.py::DependencyGraph`·
`change_correlation.py::overlay_changes`·`flapping.py`) **전부 게이트 전용이고 진단 경로 소비 0건**이다.

#### **G5** — 조사 지침 주입점이 **배선되지 않았다**

`_default_diagnose_fn`(`mcp_service.py:110`)은 `agent.ask(_job_to_question(job))`만 호출한다 —
`system_prompt_additions`를 **넘기지 않는다**(grep 0건). `diagnosis.py::_with_load_guard_note`가
`LOAD_GUARD_NOTE`만 자동 주입하므로, `MIDDLEWARE_FOCUS_NOTE`(`toolset_profiles.py:124`)조차
**프로덕션에서 주입되지 않는다**. `plans/85` §9 티어 1-①(Plan 51 플레이북 프롬프트 편입)이
값싸다고 판정한 그 작업도 **이 배선점이 없으면 착수 자체가 불가**하다.

#### **G6** — 브리핑 **생산자↔소비자 키 계약 불일치** (양쪽 경로 동시 결함)

| | 키 |
|---|---|
| 생산 (`briefing_builder`) | `severity`(dict) · `summary`(str) · `timeline`(**list**) · `bottleneck`(str) · `cause`(str) · `recommendation`(**dict** `{items,note}`) · `limitations`(**list**) · `citations_verified`(bool) · `hypotheses`(list) |
| 소비 pull (`fault_diagnosis.py:56` `_BRIEFING_ORDER`) | `timeline` · `bottleneck` · `cause` · **`evidence`** · `recommendation` · **`limitation`** |
| 소비 push (`alarm_notifier.py:148` `ordered`) | 동일 6키 |

결과(두 경로 **대칭 결함** — Known Mistakes "단일/멀티 경로 비대칭"의 변종):
- **`limitations`가 사용자에게 도달하지 않는다** — 키가 `limitation`(단수)로 어긋나고, 폴백 루프는
  `isinstance(val, (str,int,float,bool))`만 통과시키는데 값이 list라 **통째로 탈락**한다.
  `briefing_builder`가 문서로 못 박은 *"한계 서술 강제"* 가 **출력단에서 소멸**한다.
- **`evidence` 키는 애초에 생산되지 않는다** — `[근거]` 섹션은 영구히 비어 있다.
- `recommendation`(dict)·`timeline`(list)이 `str(val)`로 렌더돼 **Python repr이 그대로 노출**된다
  (`{'items': [...], 'note': '...'}` · `['line1', 'line2']`).
- `hypotheses`(가설 강등분)·`severity`도 폴백 루프에서 탈락한다.

> **이것은 Plan 50 §9(출력)의 정확한 소관**이며, §6·§7이 산출할 새 구조(rank·confidence·상대시각
> 타임라인)를 **얹기 전에 닫아야 하는 결함**이다. 지금 상태로 새 필드를 추가하면 그것도 똑같이 탈락한다.

### 0.4 배치 결정 — 신규 코드는 **어느 패키지에** 들어가는가

경계 제약(실측 · 두 패키지의 역할과 구성은 **§0.2-a** 참조): `sre_agent`·`mcp_server`는
**별도 프로세스, 양방향 import 0, 통신은 MCP뿐**(D-118·D-119·D-139). `noise_gate/domain`의 결정적 자산은 `sre_agent`에서 import 불가.
품질 게이트: `overfit_check` 스캔 대상에 `mcp_server/mcp_server`가 포함되나 **`polestar_tools.py`는 EXCLUDE**
(`scripts/overfit_check.py:60·72`). `sre_agent`는 루트 `arch_check`·`overfit_check` 대상이 아니고
**자체 `sre_agent/scripts/arch_check.py`** 를 가지며 `domain` 계층이 이미 등록돼 있다.

| 갭 | 배치 | 근거 |
|---|---|---|
| **G1·G2·G3** (구간 앵커 수집) | `mcp_server/mcp_server/polestar_tools.py` 인자 확장 | 고정 SQL이 이미 여기 있고, overfit EXCLUDE라 폴스타 리터럴이 합법. 도구 계약 확장이므로 소비자(HolmesGPT) 코드 변경 0 |
| **G4** (상관 계산) | **`sre_agent/domain/correlation.py`(순수 함수)** | **v2.1 정정** — 조사 로직의 소유권은 `sre_agent`다(D-118). `severity_signatures`가 이미 같은 모양(도구 출력 위의 결정적 domain 함수)으로 여기 있다. §0.4-a 참조 |
| **G4-b** (증거 사전수집) | **`sre_agent/application/evidence_prefetch.py`(신규)** | 상관 계산의 입력을 **LLM 선택에 맡기지 않고** 결정적으로 확보한다. 호출 수단은 이미 있다(§0.4-a 실측 ③) — 신규 의존 0 |
| **G5** (지침 주입) | `sre_agent/interface/mcp_service.py` 1곳 + `toolset_profiles.py` 상수 | 이미 있는 주입 경로에 인자를 넘기기만 하면 된다 |
| **G6** (브리핑 계약) | `briefing_builder`(생산) + `fault_diagnosis.py`·`alarm_notifier.py`(소비) **3파일 동시** | 한쪽만 고치면 비대칭이 남는다(Known Mistakes) |
| §7.2 rank·confidence / §9.1 상대시각 렌더 | `sre_agent/application/briefing_builder.py` (+ 필요 시 `sre_agent/domain/`) | 브리핑 조립은 이미 여기 소관 |
| 사건 시각 파싱(§5.2 `incident_scoper` 축소 잔존분) | `src/nodes/fault_diagnosis.py` + 신규 순수 함수 | pull 질의 원문은 본체에만 있다. 기존 `resolve_stat_month_range`는 **월 단위**라 "어제 14시"를 못 푼다(실측) — 시/분 해상도 함수 신설 |

#### 0.4-a 상관 계산의 소재지 — **`sre_agent`** (v2.1 정정)

> **⚠ v2 권고 정정.** v2는 *"(A) mcp_server 합성 도구"* 를 채택하고, 대안 (B)를 물리는 근거로
> *"`sre_agent`에 MCP 클라이언트를 새로 들이는 비용"* 을 들었다. **그 비용 전제가 사실과 다르다**
> (아래 실측 ③). 또한 v2는 선택지를 (A)/(B)/(C) 셋으로만 놓아 **(D) sre_agent 사전수집**을
> 검토 대상에 넣지 않았다 — 그것이 (A)의 장점을 그대로 가지면서 소유권 문제가 없는 안이다.

**결정적 실측 3건**

| # | 실측 | 재현 |
|---|---|---|
| ① | **원시 도구 출력은 `sre_agent`를 벗어나지 않는다.** poll 반환(`InvestigationJob.summary()`)은 `briefing` + `tool_calls_summary`(=`ToolCallResult.description` 목록)뿐이고, `ToolCallRecord.output`(원시 JSON)은 실리지 않는다 | `sed -n '73,89p' sre_agent/sre_agent/application/investigation_jobs.py` |
| ② | **`sre_agent`는 이미 "도구 출력 위의 결정적 domain 계층"을 갖고 있다** — `domain/severity_signatures.judge(gate_severity, texts)`. 상관 엔진은 입력·성격·소비처가 이것과 동일하다 | `grep -n "def judge" -A 6 sre_agent/sre_agent/domain/severity_signatures.py` |
| ③ | **`sre_agent`는 도구를 결정적으로 직접 호출할 수 있다 — 신규 의존 0.** ⓐ holmes `ToolExecutor.get_tool_by_name(name)` → `Tool.invoke(params, ToolInvokeContext)` ⓑ `mcp` 1.25.0이 **이미 venv에 설치**돼 있다(holmesgpt 전이 의존) + `AgentSettings`가 `polestar_mcp_url`·토큰을 보유 | `grep -n "def get_tool_by_name" sre_agent/.venv/lib/*/site-packages/holmes/core/tools_utils/tool_executor.py` · `ls sre_agent/.venv/lib/*/site-packages/ \| grep '^mcp'` |

**4안 비교**

| 안 | 내용 | 판정 |
|---|---|---|
| **(D) `sre_agent` 사전수집 + domain 상관** — dispatcher가 조사 **전에** 구간 증거를 결정적으로 수집·계산하고, 결과를 `system_prompt_additions`로 주입한 뒤 ReAct 루프를 돌린다 | LLM이 서술 전에 수치를 받아 **(A)의 장점을 그대로 갖고**, 도구 호출 여부와 무관하게 **항상 계산된다**. `briefing_builder`가 Python 객체를 직접 받아 §7.2·§9.1 조립이 단순해진다. 소유권(D-118)·계층(application→domain) 정합 | **채택** |
| (A) `mcp_server` 합성 도구 | 장점은 (D)와 같으나 ⓐ**호출 여부가 LLM 비결정에 걸린다** ⓑ`mcp_server` 헌장이 *"관측 데이터 읽기 경계"* 인데 이상판정·인과 순서화는 읽기가 아니다 ⓒ`overfit_check` 스캔 대상이라 상관 모듈에 **스키마 무지 제약**이 인위적으로 붙는다 ⓓ 소비자가 `sre_agent` 하나뿐인데 **collectorinfra와 공유하는 경계**를 넓힌다 ⓔ`briefing_builder`가 도구명 매칭으로 JSON을 다시 파싱해야 한다 | 비채택 |
| (B) dispatcher 사후 계산 | 입력이 *"LLM이 우연히 가져온 것"* 뿐이고, 서술이 이미 다른 결론을 쓴 뒤라 브리핑에 두 이야기가 섞인다 | 비채택 |
| (C) `src/diagnosis/` 또는 신규 최상위 `rca/` | **실측 ①에 걸린다** — 원시 증거를 볼 수 없어 재수집이 강제되고(폴스타 DB 2배 부하), 두 수집이 시각이 달라 서로 다른 결론을 낼 수 있다. D-118 ④ *"분리 절차 = 폴더 복사 + URL 변경"* 도 깨진다(조사가 본체 패키지에 의존하게 됨) | **폐기** |

**(D)의 알려진 비용·리스크(정직 기재)**

- 조사 전 SQL 3~5회만큼 **지연이 앞단에 붙는다**(조사 전체 예산 300s 대비 미미하나 측정 대상).
- 호출 수단 ⓐ는 **holmes 내부 API**다(`ToolInvokeContext`가 `llm`·`max_token_count` 등을 요구).
  버전 고정(holmesgpt 0.36.0)에 묶이므로, 안정성을 우선하면 ⓑ(`mcp` 클라이언트 직접)를 쓴다 —
  그때는 `mcp`를 **전이 의존에 기대지 말고 `pyproject.toml`에 명시 선언**한다.
- `sre_agent`는 `overfit_check` 스캔 대상이 **아니다** — (A)의 단점 ⓒ가 사라지는 대신 **스키마
  리터럴이 무검열로 쌓일 수 있다.** 대응: `correlation.py`는 벤더 중립(시각·수치·라벨만)으로 유지하고,
  폴스타 어휘는 `evidence_prefetch.py`의 도구 인자에만 둔다. 착수 시 `sre_agent`를 `overfit_check`
  스캔 대상에 편입할지 별도 판정한다(§16 U-D).

#### 0.4-b 왜 **별도 최상위 폴더**가 아닌가 — D-139 해석과 파이프라인 대조

D-139(기능별 최상위 폴더)는 *"신규 기능은 **소속** 패키지 폴더에 만들고, 본체 수정은 배선
최소로 한정한다"* 이다. **"기능마다 새 폴더"가 아니라 "기능은 자기 소속 패키지 안에서"** 다.
RCA의 소속은 D-118이 조사 소유권을 둔 곳 — `sre_agent`다. 따라서 D-139는 신규 `rca/`를
지지하지 않고 오히려 `sre_agent` 편입을 가리킨다.

**결정적 대조 — Plan 50 §5의 파이프라인은 이미 `sre_agent`의 파이프라인이다**

| Plan 50 §5 노드 | 실제 대응 | 소재지 | 상태 |
|---|---|---|---|
| `incident_scoper` | 사건 시각·대상 파싱 | **`src`** (pull 질의 원문은 본체에만 있다) | 잔여(A′-5) |
| `evidence_collector` | 구간 증거 결정적 수집 | **`sre_agent/application/evidence_prefetch.py`** | 잔여(A′-3) |
| `correlation_engine` | 타임라인·이상·선후 계산 | **`sre_agent/domain/correlation.py`** | 잔여(A′-3) |
| `causal_reasoner` | 인과 서술 | `sre_agent` `DiagnosisAgent`(HolmesGPT) | **구현 완료** |
| `diagnosis_reporter` | 리포트 조립 | `sre_agent` `briefing_builder` | **구현 완료** |

**앞뒤 두 노드가 이미 `sre_agent`인데 가운데 두 노드만 다른 폴더에 두는 구성은 성립하지 않는다.**
2026-06-26에 이것이 독립 서브시스템처럼 보였던 이유는 단순하다 — **그때 `sre_agent`가 없었다**
(D-118 결정일 2026-07-24 · 패키지 골격 2026-07-27). 원안은 "없는 것을 새로 만든다"는 전제 위에
그려졌고, 그 전제가 사라진 지금 남는 것은 **자리 배치**뿐이다.

**별도 폴더가 추가로 깨뜨리는 것 2건**

1. **증거 접근** — §0.4-a 실측 ①. 본체 venv의 `rca/`는 원시 도구 출력을 볼 수 없어 재수집이
   강제된다. 폴스타 DB 부하가 2배가 되고, 두 수집의 시각이 달라 **조사와 상관이 서로 다른
   결론**을 낼 수 있다(§3.3 "수치는 하나의 결정적 출처" 원칙 위반).
2. **분리 가능성** — D-118 ④ *"분리 절차 = 폴더 복사 + URL 설정 변경(계약·코드 무변경이 회귀
   기준)"*. RCA가 본체 패키지에 있으면 조사가 본체에 의존하게 되어 이 성질이 깨진다.

**그럼에도 `mcp_server`에 남는 것 — 구간 앵커 SQL(G1~G3)**

"`sre_agent`에 포함한다"가 "전부 `sre_agent`"를 뜻하지는 않는다. 사건 구간을 지정하는 SQL 인자
확장은 **조사 로직이 아니라 읽기 경계 작업**이고, 고정 SQL은 이미 `polestar_tools.py`에 있다
(D-119 단일 경계 · D-122). 경계선은 이렇게 그린다:

```
mcp_server   [읽기 경계]   무엇을 어느 구간에서 읽어 올 것인가   ← G1·G2·G3
    │ MCP 계약(JSON rows)
    ▼
sre_agent    [조사 소유]   읽어 온 것으로 무엇을 계산·판단할 것인가 ← G4·G4-b·§7.2·§9.1
    │ MCP 계약(briefing)
    ▼
src/noise_gate [표현]      판단을 사용자에게 어떻게 보일 것인가    ← G6
```

이 3층은 기존 D-118·D-119 경계와 정확히 일치한다 — **본 계획은 새 경계를 만들지 않고 기존
경계 위에 잔여를 배치할 뿐이다.**

> 위 그림은 **책임의 3층**이다. 같은 구조를 **런타임 호출 순서**로 본 것은 §0.2-a의 호출 사슬이며,
> 두 그림은 축이 다르다(책임 ↔ 호출). 패키지의 역할·도구 구성·분리 근거는 **§0.2-a가 정본**이다.

### 0.5 Plan 50 절별 처분표 (**정본**)

| 절 | 원 내용 | 처분 | 이관처 |
|---|---|---|---|
| §1.2~1.5 | 목표·설계원칙·분석 명세 | **차용(유효)** | — |
| §2 | 자산 재사용 지도 | **개정** — `src/alarm/*` 경로가 `noise_gate/*`로 이동됨(D-139). 실제 재사용처는 `mcp_server` 도구 | §0.2 |
| §3.1~3.3 | 조합 구현·고정 SQL·수치는 Python | **차용(유효)** — 이미 실현되었거나 본 개정의 근간 | — |
| **§3.4** | 진단 단위 = (db_id, 리소스, **사건 구간**) · now() 금지 | **차용 — 최우선 잔여** | **G1** |
| §3.5 | 트리거 이중화 | **완료** | `fault_diagnosis.py`·`investigation_trigger.py` |
| §3.6 | 단일서버 우선 | **차용(유효)** | — |
| §4.1 | 사건구간 다중 알람 SQL | **차용 → 도구 인자로 번역** | **G3** → `polestar_tools.py` |
| §4.2 | 메트릭 추이 SQL(정밀도 분기) | **차용 → 도구 인자로 번역** | **G1·G2** → `polestar_tools.py` |
| §4.3 | 프로세스 단면(R-2 한계) | **완료** | `polestar_process_snapshot` |
| §4.4 | 토폴로지 | **완료** | `polestar_topology` |
| §4.5 | 변경 이벤트 | **완료** | `polestar_change_history` |
| **§5 전체** | `src/diagnosis/` LangGraph 서브그래프·`DiagnosisState`·디렉토리 | **폐기(래퍼만)** | 노드 5종의 **책임은 살아남고 소재지만 이동**한다 — §0.4-b 대조표. `src/diagnosis/`·`DiagnosisState`·LangGraph 배선만 폐기 |
| **§6.1** | 통합 타임라인 병합 | **차용 — 잔여** | **G4** → `sre_agent/domain/correlation.py` |
| **§6.2** | metric_anomaly(z·지속성·선행성) | **차용 — 잔여** | **G4** → 동上 |
| §6.3 | 선후·연쇄 판정 | **부분 차용** — 다중서버 연쇄는 `noise_gate/topology.py`가 게이트에서 이미 수행. 진단은 **단일서버 서브리소스 시차**만 | **G4** → 동上 |
| **§6.4** | `CorrelationResult`(leading_signal·notes) | **차용 — 잔여** | **G4** → `correlation.py` 반환 dataclass(브리핑 정본) |
| §7.1·7.3 | LLM 입력·환각 차단 규칙 | **완료(초과 충족)** | `briefing_builder._is_cited` |
| **§7.2** | 복수 가설 rank·confidence | **차용 — 잔여** | **G4** → `briefing_builder` |
| §8.1·8.2 | pull·push 트리거 | **완료** | — |
| §8.3 | `POST /diagnosis/analyze` | **폐기** — 원문도 *선택*. 진입점은 챗·알람 2종으로 충분 | — |
| **§9.1** | 상대시각 타임라인 리포트 | **차용 — 잔여 + G6 선결** | `briefing_builder` + 소비자 2곳 |
| §9.2 | 채널 | **완료** | — |
| §9.3 | 진단 이력·피드백 | 범위 외 유지 | — |
| **§10.1** | `DiagnosisConfig` 신설 | **폐기** — `investigation_*` 13필드(`src/config.py:889~907`)·`AgentSettings`가 이미 존재 | 신규 플래그는 §10 개정 블록 |
| §11 | Phase A/B/C | **폐기 → §0.6으로 대체** | — |
| §12 | 테스트 계획 | **개정** — 배치처 변경 반영 | §12 개정 블록 |
| §13 | 리스크 | **차용 + 신규 4건** | §13 개정 블록 |
| §14 | **D-038 등재 예정** | **정정** — D-038은 **이미 소진**(`docs/02_decision.md:313` 도움말 디스커버리). 채번 재산출 필요 | §14 개정 블록 |
| §15 | 변경 파일 목록 | **폐기 → §0.4 표로 대체** | — |

### 0.6 개정 착수 단계 (Phase A′ / B′ / C′)

각 단계는 **앞 단계 없이는 뒤가 무의미**하다(G1 선행성). 전부 옵트인 플래그 뒤에 둔다.
**소재지는 §0.4-b의 3층 경계를 따른다** — A′-1·A′-2는 `mcp_server`(읽기), A′-3·A′-4·A′-6은
`sre_agent`(조사), A′-0·A′-5는 `src`/`noise_gate`(표현·질의 파싱).

| 단계 | 작업 | verify |
|---|---|---|
| **A′-0** ✅ | **G6 브리핑 키 계약 정합** — 생산자 키를 정본으로 삼고 소비자 2곳을 맞춘다. list·dict 값의 렌더러 신설(repr 노출 제거), `limitations`·`hypotheses` 노출 | 세 파일 키 집합 일치 단언 테스트 + 두 경로(pull·push) 렌더 골든. **한계 문자열이 실제 응답에 나타남**을 단언 |
| **A′-1** ✅ | **G1 도구 계약 확장** — `polestar_metric_trend`·`polestar_alarm_history`에 `reference_time`(ISO 또는 yyyyMMddHHmmss)·`lookback_minutes` 선택 인자 추가. **미지정 시 종전 동작 비트 동일**(now 앵커) | SQL 빌더 단위 테스트(구간 BETWEEN 생성·이스케이프·엔진 분기 h/d/m·미지정 시 종전 SQL과 문자열 동일) |
| **A′-2** ✅ | **G3** `polestar_incident_alarms` 신설(구간 내 전 알람, `alarm_name` 선택) + **G2** `baseline_periods` 인자 | 고정 SQL 테스트(D-030 해소 포함·COALESCE 조인·D-022 준수) |
| **A′-3** ✅ | **G4** `sre_agent/domain/correlation.py` 순수 함수 3종(§6.1 병합 · §6.2 이상탐지 · §6.3 시차 순서화) + **G4-b** `sre_agent/application/evidence_prefetch.py`(도구 호출자를 **주입**받는다 — `diagnose_fn`·`briefing_fn` 주입 패턴 계승) | 순수 함수 단위 테스트(기준시각 좌표계·now() 미사용·증거 결손 시 `notes` 결정적 기록) + 사전수집 부분실패 테스트. `sre_agent/scripts/arch_check.py`에 두 모듈 계층 등록(application→domain) |
| **A′-4** ✅ | **G5** `_default_diagnose_fn`에 `system_prompt_additions` 배선 + 상관 도구 사용 지침 | 주입 문자열이 `ask()`에 도달함을 단언(현재 0건인 것을 테스트로 고정) |
| **A′-5** ✅ | 사건 시각 파싱 — `src`에 시/분 해상도 순수 함수 신설, `sre_diagnose`에 `reference_time`·`lookback_minutes` 인자 추가, `_job_to_question`이 **push는 `event.alarmTime`을 질문에 싣도록** 수정 | "어제 14시" → 구간 산출 단위 테스트 + push 질문에 시각이 포함됨을 단언 |
| **A′-6** ✅ | **§7.2·§9.1** — `briefing_builder`가 `CorrelationResult`를 정본으로 `timeline`(상대시각 `T-15m`)·`root_cause_hypotheses`(rank·confidence)·`notes`를 조립. 도구 미호출 시 한계 명시 | 상관 있음/없음 두 경로 골든. 수치는 **주입값만** 사용(환각 0) 단언 |
| **B′** ✅(A′-3과 동시 — holmes `ToolInvokeContext`가 LLM 객체 필수라 처음부터 `mcp` 클라이언트) | 호출 수단 안정화 — A′-3이 holmes 내부 API(`ToolInvokeContext`)를 썼다면 `mcp` 클라이언트 직접 호출로 교체하고 `pyproject.toml`에 `mcp` **명시 선언**(현재 전이 의존) | holmesgpt 버전 상향 시 사전수집이 깨지지 않음을 회귀로 고정 |
| **C′** | 다중서버 연쇄 진단(§6.3 확장) · 변경 이벤트 오버레이 정식 편입 · 진단 이력·피드백 | 범위 외 유지 |

> **A′-0을 맨 앞에 두는 이유**: 지금 상태에서 §7.2·§9.1의 새 필드를 추가하면 **그 필드도 똑같이
> 출력단에서 탈락한다**(G6). 계약을 먼저 닫지 않으면 A′-6의 산출물이 사용자에게 도달했는지
> 검증할 수 없다.

### 0.7 이 개정이 **하지 않는 것**

- **폐기 제안이 아니다.** §5·§8.3·§10.1·§15의 "폐기"는 **본 계획서 안의 설계안 철회**이지 코드·모듈
  폐기가 아니다(해당 코드는 애초에 존재하지 않는다 — `src/diagnosis/` 부재 실측). D-161 ② 4항
  실측 의무의 대상이 아니다.
- **`sre_agent` 재편을 제안하지 않는다.** dispatcher·가드·severity_judge·remediation은 손대지 않는다.
- **과금 API를 호출하지 않았다.** 본 개정의 모든 판정은 정적 실측이며, 실 조사 재현은
  D-127 건별 승인 사항이다.

---

## 1. 개요 및 목표

### 1.1 배경

본 에이전트는 폴스타(Polestar) SMS와 연동하여 (1) 자연어→SQL로 인프라/성능 데이터를 조회하고,
(2) 알람 소켓을 수신하여 LLM으로 **단건 알람**을 분석·발송하는 기능을 이미 갖추고 있다.

현재 알람 분석(`alarm_analyzer`)은 단건 알람 + 동일 알람의 이력 통계(Plan 47) + 실시간 프로세스 스냅샷
(Plan 47-1)을 입력으로 `probable_cause`(추정 원인) / `recommended_action`(권고 조치)을 생성한다.
이는 **사실상 단일 신호 기반의 1차 진단**이다.

그러나 실제 운영의 "장애진단·원인분석"은 다음을 요구한다:

| 구분 | 현재 (단건 알람 분석) | 본 계획 (장애진단·원인분석) |
|------|----------------------|---------------------------|
| 입력 신호 | 알람 1건 + 동일알람 이력 + 현재 프로세스 | **사건 구간(window)의 다중 신호** — 알람 다발 + 성능 시계열 추이 + 프로세스 + 토폴로지(연관 리소스) |
| 분석 단위 | 알람 이벤트 1건 | **서버/리소스 × 시간 구간**의 사건(incident) |
| 시간 관점 | 발생 시점 단면 | **사건 전/중/후 타임라인** 복원 |
| 인과 관점 | 단일 추정 원인 1줄 | **근거 인용 + 신뢰도가 붙은 원인 가설 순위** |
| 연관 관점 | 단일 리소스 | **부모/자식/동일서버 리소스 간 연쇄(cascade)** 식별 |
| 호출 방식 | push(소켓 수신 시 자동) | push(자동) **+ pull(사용자가 "원인 분석해줘"로 요청)** |

### 1.2 목표

폴스타 모니터링 데이터(성능 시계열)와 이벤트 데이터(알람 이력)를 **사건 단위로 결합**하여,
LLM과 연동한 **장애진단(무엇이 문제인가)** 과 **원인분석(왜 발생했는가, 근거는 무엇인가)** 기능을 추가한다.

핵심 산출물: **구조화된 진단 리포트(DiagnosisReport)** — 타임라인 / 수집 증거 / 원인 가설 순위(근거·신뢰도) /
권고 조치. 자연어 응답 + (선택) Excel/Word 문서 + (push 시) 알림 채널로 제공.

### 1.3 설계 원칙 (기존 프로젝트 원칙 계승)

1. **읽기 전용 절대 불변** — 진단도 SELECT만 사용. 3중 읽기 전용 방어(D-003) 유지.
2. **수치·상관은 Python(결정적), 인과 해석만 LLM** — Plan 47 §3.3 원칙 계승. 발생 횟수·메트릭 이상·
   타임라인 정렬·연쇄 판정은 순수 함수로 결정적 계산하고, LLM에는 **계산된 증거 요약**만 주입하여
   환각·계산 오류를 차단한다.
3. **재구축이 아니라 조합(composition over rebuild)** — 기존 알람 이력 조회·메트릭 쿼리·프로세스 API·
   마스킹·알림·문서 생성·오케스트레이션 자산을 **재사용**하고, "사건 범위 설정 → 다중 증거 수집 →
   상관분석 → 인과 추론 → 리포트"의 얇은 조정 계층만 신설한다.
4. **graceful degradation** — 일부 증거(프로세스/메트릭/토폴로지) 수집 실패 시에도 가용 증거로
   진단을 진행한다. 폴스타 DB/API 의존이 진단 전체를 차단하지 않는다(Plan 47 §3.1 계승).
5. **근거 없는 단정 금지** — 원인 가설은 반드시 수집된 증거를 인용하고 신뢰도(확신도)를 함께 제시한다.
   증거가 부족하면 "추가 확인 필요"로 명시한다(환각 방지 — Known Mistakes 계승).
6. **폐쇄망 호환 / tool-calling 비의존** — 진단 서브그래프는 고정 파이프라인(LangGraph)으로 동작하며
   LLM tool-calling을 요구하지 않는다. 워커 LLM(FabriX/KBGenAIChat)로 동작 가능(D-037, Plan 49 계승).

### 1.4 성공 기준

1. 사용자가 자연어로 "X 서버 어제 14시쯤 장애 원인 분석해줘"라고 요청하면, **사건 구간**을 해석하여
   다중 신호(알람·메트릭·프로세스)를 수집하고 **원인 가설 순위 + 근거 + 권고**를 자연어로 반환한다.
2. 발생 횟수·메트릭 이상·타임라인·연쇄 판정 등 **수치는 Python이 결정적으로 계산**하고 LLM은 해석만 한다
   (LLM 응답에 계산값 환각 0건).
3. 고심각도(또는 첫 발생/급증) 알람 수신 시(push), 진단 서브그래프가 자동 실행되어 알림에
   **원인 가설·근거**가 첨부된다(기존 단건 분석 대체가 아니라 고도화, opt-in 플래그).
4. 진단 기능 비활성/실패 시 기존 알람 분석·데이터 조회 경로가 **무변경**으로 동작한다(회귀 없음).
5. `arch_check --ci` 통과(계층 위반 0), 신규 단위/통합 테스트 통과, 읽기 전용 검증 유지.
6. 증거 수집은 **고정/파라미터 SQL + 타임아웃 + 단기 캐시**로 폴스타 DB 부하를 보호한다(Plan 47 계승).

### 1.5 추가 수집 데이터 및 분석 방법 (명세)

> "무엇을 추가로 수집하고, 어떻게 분석하는가"의 한눈 요약. 상세는 §4(수집)·§6(상관분석)·§7(인과추론).

| # | 추가 수집 데이터 | 소스 · 수집 방법 | 분석 방법 (결정적 Python) | 산출 |
|---|-----------------|-----------------|--------------------------|------|
| 1 | **사건구간 알람 타임라인** (다중 알람, 해소 포함) | `cmm_alarm`+`cmm_alarm_def`+`cmm_resource` 사건구간 고정 SQL (§4.1) | 타임라인 병합·severity 추이·선후 판정 (§6.1) | 알람 발생 순서·다발 여부 |
| 2 | **성능 메트릭 추이** (CPU/메모리/FS/디스크IO, 전·중·후) | `cmm_metric_stat_h/d/m` 고정 SQL, 시간정밀도 분기 (§4.2) | baseline 대비 이상탐지(z-score·지속성)·**메트릭 선행성** (§6.2) | 이상 지표·선행 신호 |
| 3 | **실시간 프로세스 Top** | 폴스타 프로세스 API (§4.3) | top N 선별·마스킹 (한계: 현재 단면) | 자원 소비 주체 |
| 4 | **토폴로지** (부모/자식/동일서버) | `cmm_resource` 계층 고정 SQL (§4.4) | 서브리소스/연관 식별·연쇄(cascade) 판정 (§6.3) | 사건 시작점·연쇄 후보 |
| 5 | (Phase C) **변경/구성 이벤트** | 폴스타/ITSM 변경이력(가용성 선조사) (§4.5) | 타임라인 오버레이(변경기반 RCA) | 변경 용의 후보 |

**분석 절차**: ①~⑤를 `evidence_collector`가 동시 수집(부분실패 허용) → **`correlation_engine`(결정적)** 이
`CorrelationResult`(타임라인·메트릭 이상·선행 신호·연쇄·데이터 한계 notes) 산출 → **`causal_reasoner`(LLM)** 가
요약 텍스트만 받아 **원인 가설 순위 + 근거 인용 + 신뢰도 + 권고**를 생성(§7). 수치는 Python, 해석만 LLM.

---

## 2. 현재 자산 분석 (재사용 지도)

> **⚠ v2 개정(2026-09-01 · 처분: 개정)** — 아래 재사용 지도의 경로는 **낡았다.** `src/alarm/*`는
> D-139로 `noise_gate/*`에 이관됐고, 표가 "재사용 대상"으로 지목한 이력 조회·프로세스 API·
> 토폴로지는 이미 **`mcp_server` 고수준 도구 8종으로 구현·노출**되어 있다(D-122). 즉 이 절이
> 그리는 *"직접 repo를 만들어 쓴다"* 는 그림은 성립하지 않는다 — 진단의 증거 접근 경계는
> `mcp_server` 하나다(D-119). **현행 자산 지도는 §0.2**를, 재사용 불가 사유(패키지 경계)는
> **§0.4**를 본다.

진단 기능은 아래 기존 자산 위에 조립한다. **신규 비즈니스 로직은 상관분석·인과추론·사건범위 설정뿐**이다.

### 2.1 알람/이벤트 파이프라인 (재사용)

| 자산 | 경로 | 진단에서의 재사용 |
|------|------|------------------|
| 알람 도메인 모델 | `src/alarm/domain/alarm.py` (`AlarmEvent`, `AlarmHistoryEntry`, `AlarmHistoryStats`, `ProcessSnapshot`, `AlarmAnalysisResult`) | 사건 트리거·증거 구성의 입력 타입으로 재사용 |
| 알람 이력 조회 | `src/alarm/infrastructure/polestar_history.py` (`PolestarAlarmHistoryRepository`, `build_history_sql`) | 사건 구간·다중 알람으로 **확장 조회**(§4.1). 조인 패턴(C-2/C-6, COALESCE PLATFORM_RESOURCE_ID), `_sql_literal` 이스케이프, 서버명 매칭 규칙 재사용 |
| 패턴 통계 (결정적) | `src/alarm/domain/alarm_pattern.py` (`compute_history_stats`) | 사건 내 알람 빈도/주기/급증 판정에 재사용 |
| 실시간 프로세스 | `src/alarm/infrastructure/polestar_process_api.py` (`PolestarProcessApiClient.list_by_hostname`) | CPU/메모리 사건의 프로세스 증거. **한계: 실시간 단면만 제공**(§4.3, R-2) |
| 프로세스 선별/마스킹 | `src/alarm/domain/process_rank.py` (`select_top_processes`, `mask_args`, `classify_alarm_kind`) | 그대로 재사용 (마스킹 보장) |
| 증거 동시 수집 | `src/alarm/application/nodes/alarm_context_enricher.py` (`enrich_history`, `enrich_processes`, `asyncio.gather` + `wait_for` 타임아웃) | **다중 증거 fan-out 수집기**의 설계 템플릿(§5.2) |
| LLM 단건 분석 | `src/alarm/application/nodes/alarm_analyzer.py` | 인과추론 노드의 프롬프트 구성·JSON 파싱 패턴 참고 |
| 알림 발송 | `src/alarm/application/nodes/alarm_notifier.py` (WorkB/webhook), `infrastructure/notification_bus.py` (SSE) | push 진단 결과 발송에 재사용 |
| 알람 서브그래프 | `src/alarm/orchestration/alarm_graph.py` (`build_alarm_graph`, `AlarmState`) | **진단 서브그래프(diagnosis_graph)의 설계 템플릿**(§5) |
| 워커/수신 | `alarm_server/` (TCP→Redis), `src/alarm/application/alarm_worker.py` | push 트리거 연결점(§8.2) |

### 2.2 성능 시계열 메트릭 (재사용 — 진단의 핵심 신규 신호)

폴스타 성능 통계 테이블 `cmm_metric_stat_[h,d,m]` (시/일/월 집계). 프로필 query_guide
(`config/db_profiles/polestar_cm_gp.yaml:210-257`, `polestar_cm_yd.yaml`)에 조회 구조가 정의되어 있다.

| 컬럼 | 의미 |
|------|------|
| `resource_id` | `cmm_resource.id`와 조인 |
| `definition_name` | 지표 종류 — `'Utilization'`(사용률), `'MaxIORate'`(디스크 IO) |
| `stat_date` | 시: `YYYYMMDDHH` / 일: `YYYYMMDD` / 월: `YYYYMM` (문자열) |
| `min_val`, `avg_val`, `max_val` | 기간 내 최소/평균/최대 |

조회 가능 지표(resource_type × definition_name):
- `server.Cpus` + `Utilization` → CPU 사용률(%)
- `server.Memory` + `Utilization` → 메모리 사용률(%)
- `server.FileSystems` + `Utilization` → 파일시스템 사용률(%)
- `server.Disks` + `MaxIORate` → 디스크 IO

조인 구조: `cmm_resource r`(서브리소스) → `cmm_resource svr`(`svr.id = r.platform_resource_id AND
svr.resource_type='server.Server'`) → `cmm_metric_stat_? s`(`r.id = s.resource_id`).

**진단 관점 의미**: 알람 발생 시점 전/중/후의 메트릭 추이를 조회하여 "알람보다 먼저 CPU가 상승했는가",
"이상이 지속/급등인가"를 결정적으로 판정할 수 있다. **시간 정밀도 분기**(§4.2, R-3):
최근 사건은 `_h`(시 단위), 과거 사건은 `_d`/`_m`만 존재.

### 2.3 토폴로지 (재사용)

`cmm_resource` 단일 테이블에 모든 리소스 계층 표현:
- 부모-자식: `parent_resource_id = id`
- 동일 서버 소속: `platform_resource_id` 동일
- `resource_type`로 종류 구분(`server.Server`, `server.Cpus`, `server.Memory`, `server.FileSystem`,
  `server.NetworkInterface` 등), `dtime IS NULL`로 삭제 제외

**진단 관점 의미**: 한 서버 내 어떤 서브리소스(CPU/디스크/NW)에서 사건이 시작됐는지, 또는 연관 서버로
번졌는지(연쇄)를 토폴로지로 좁힐 수 있다.

### 2.4 오케스트레이션 / 라우팅 (재사용 — pull 트리거 연결점)

| 자산 | 경로 | 재사용 |
|------|------|--------|
| 의도 라우팅 | `src/routing/semantic_router.py`, `src/routing/domain_config.py` | 신규 `fault_diagnosis` 의도 추가(§8.1) |
| SubAgent 레지스트리 | `src/orchestration/subagents.py` (`SubAgentSpec`, `SUBAGENT_REGISTRY`, `run_data_query_pipeline`) | 신규 `fault_diagnosis` subagent 등록 → Track A(의도분해)·Track B(deepagents tool) 양쪽에서 자동 노출 |
| 의도 분해 플래너 | `src/orchestration/intent_planner.py`, `src/prompts/intent_planner.py` | 진단 의도 인식 프롬프트 보강 |
| 결과 종합 | `src/orchestration/result_aggregator.py` | 멀티 의도 질의에서 진단 결과 통합 |
| 데이터 조회 파이프라인 | `run_data_query_pipeline` (NL→SQL 전체) | 진단 중 **임시/후속 ad-hoc 조회**가 필요할 때 호출 |

### 2.5 공통 인프라 (재사용)

- DB 접근: `src/routing/db_registry.py` (`DBRegistry.get_client(db_id)` → `execute_sql`, 읽기 전용)
- 출력: `src/nodes/output_generator.py` (자연어/Excel/Word)
- 마스킹: `src/security/data_masker.py`
- 설정: `src/config.py` (pydantic-settings)
- 캐시: Redis (단기 조회 캐시)

> **결론**: 진단 서브시스템은 `src/diagnosis/`로 신설하되, 데이터 접근·통계·알림·출력은 **대부분 재사용**한다.
> 신규 코드는 (a) 사건 범위 설정, (b) 다중 증거 수집 조정, (c) **결정적 상관분석 엔진**, (d) **인과추론
> 프롬프트**, (e) 리포트 조립, (f) 트리거 연결(의도/subagent/알람훅)에 집중된다.

---

## 3. 핵심 설계 결정

각 결정은 §14에서 D-038 하위 항목으로 등재한다. 작업 전 `docs/02_decision.md`의 기존 결정과의 충돌 검토
결과: **충돌 없음**(추가적·읽기전용·기존 패턴 재사용). 단, 아래 결정은 사용자 확인이 필요할 수 있어 §16에 명시.

### 3.1 진단은 "조합(composition)"으로 구현 — 별도 거대 엔진 신설 금지

기존 알람/메트릭/프로세스/토폴로지 자산을 증거원(evidence source)으로 묶고, 그 위에 상관·추론·리포트
계층만 얹는다. 새 데이터 저장소·새 수집 데몬을 만들지 않는다(Plan 47 §3.1 "자체 저장소 미신설" 계승).

### 3.2 증거 수집 SQL은 고정/파라미터 템플릿 (LLM 생성 아님)

진단 증거(알람 타임라인·메트릭 추이·토폴로지)는 **사전 정의된 파라미터 SQL**로 조회한다.

| 기준 | 고정 템플릿 SQL (채택) | NL→SQL 파이프라인 (비채택, 보조만) |
|------|----------------------|--------------------------------|
| 결정성 | 동일 입력 → 동일 쿼리 (감사·재현 용이) | 매 호출 LLM 생성 (편차·환각 위험) |
| 지연 | 검증 루프 불필요, 1회 SELECT | 생성→검증→(재시도) 루프 |
| 안전 | 읽기 전용 고정, 인젝션은 `_sql_literal` 이스케이프 | 검증 파이프라인 의존 |
| 부하 | 타임아웃+캐시 통제 용이 | 통제 복잡 |

→ **증거 수집은 고정 템플릿**(Plan 47 `build_history_sql` 방식 계승). 단, 사용자가 **추가 임의 조회**를
요청하거나 추론 중 보강이 필요하면 기존 `run_data_query_pipeline`(NL→SQL)을 **보조 경로**로 호출한다.

### 3.3 수치는 Python, 해석은 LLM (재확인)

상관분석 엔진(§6)은 순수 함수로 메트릭 이상·타임라인·연쇄를 계산한다. LLM은 그 **요약 텍스트**만 받아
인과 가설을 서술한다. (Plan 47 §3.3, Known Mistakes "output_generator avail_status 환각" 계승)

### 3.4 진단 단위 = (db_id, 대상 리소스, 사건 시간 구간)

- `db_id`: 폴스타 인스턴스 선택 (`DBRegistry.get_client`)
- 대상 리소스: 서버명(`server_name`/장비명 `r.name`) 또는 hostname → `cmm_resource` 식별
  (프로필별 매칭 컬럼 상이 — Plan 47 §5.3, Known Mistakes 2026-06-10 "공동존 서버명 매핑" 주의 계승)
- 사건 시간 구간: 알람 시각(push) 또는 사용자 지정 시각(pull)을 **기준 시각**으로 ±lookback 구간 설정.
  **현재 시각(now) 기준 금지** — 지연 처리에도 일관되게(Plan 47 시간창 규칙, Known Mistakes 계승)

### 3.5 트리거 이중화 — pull + push

- **pull**: 신규 의도 `fault_diagnosis` + subagent. 사용자가 명시적으로 요청할 때.
- **push**: 알람 파이프라인에서 고심각도/첫발생/급증 알람에 한해 진단 서브그래프 자동 실행(플래그 opt-in).
  기존 단건 `alarm_analyzer`를 **대체하지 않고**, 진단이 활성일 때 분석 결과를 고도화한다.

### 3.6 단계적 범위 — 단일서버 심층 RCA 먼저, 다중서버 연쇄는 후속

Phase A는 **단일 대상 리소스/서버**의 심층 진단(MVP). 다중 서버 연쇄(cascade) RCA는 토폴로지·시차
상관이 복잡하므로 Phase C로 분리한다(R-5).

---

## 4. 데이터 소스 및 증거 모델

진단은 4종 증거를 사건 구간으로 수집한다. 각 증거는 **독립 수집(부분 실패 허용)**.

### 4.1 알람 타임라인 증거 (이벤트)

기존 `PolestarAlarmHistoryRepository`를 **사건 구간·다중 알람**으로 확장한 조회.

- 현재 `build_history_sql`은 (server_name, **단일 alarm_name**, lookback_start)로 동일 알람 이력만 조회.
- 진단용 `build_incident_alarms_sql`(신규)은 (server_name, **window_start~window_end**, alarm_name 미지정)로
  **해당 서버의 사건 구간 내 모든 알람**을 시간순 조회. 선택적으로 연관 리소스(부모/자식/동일
  platform_resource) 알람 포함(Phase C).
- 조인 패턴(C-2/C-6, COALESCE PLATFORM_RESOURCE_ID), `ALARMSEVERITY IN (0,1,2,3)`(해소 포함, D-030),
  `CR.DTIME IS NULL`, `_sql_literal` 이스케이프, 서버명 매칭 규칙 모두 재사용.
- 산출: 시간순 알람 이벤트 리스트 → 결정적으로 "알람 순서/선후, severity 추이, 해소 여부, 다발 여부" 계산.

### 4.2 성능 메트릭 추이 증거 (모니터링)

신규 `PolestarMetricRepository`(고정 템플릿)로 사건 구간 전/중/후 메트릭을 조회.

- 대상: 대상 서버의 `server.Cpus`/`server.Memory`/`server.FileSystems`(Utilization), `server.Disks`(MaxIORate).
- **시간 정밀도 분기**(§2.2):
  - 사건이 최근(예: `_h` 보존 기간 내) → `cmm_metric_stat_h`로 **시간 단위 추이**(이상 탐지 정밀).
  - 과거 사건 → `cmm_metric_stat_d`(일) 또는 `_m`(월)만 가능 → 정밀도 한계 명시(R-3).
- 베이스라인: 사건 직전 N기간(예: 직전 7일 동시간대) 평균/표준편차를 함께 조회 → 이상 판정 기준.
- 산출: 지표별 (구간 전 baseline, 구간 중 min/avg/max, 구간 후) 시계열 → §6 이상 탐지 입력.

```
-- 예시(개념): 사건 시각 기준 시간단위 CPU/메모리 추이 (PostgreSQL/공동존 프로필 기준)
-- stat_date(YYYYMMDDHH)가 [window_start, window_end] 범위인 행을 시간순 조회
SELECT svr.name AS server_name, r.resource_type, s.definition_name,
       s.stat_date, s.min_val, s.avg_val, s.max_val
FROM polestar.cmm_resource r
JOIN polestar.cmm_resource svr
  ON svr.id = r.platform_resource_id AND svr.resource_type = 'server.Server'
JOIN polestar.cmm_metric_stat_h s ON r.id = s.resource_id
WHERE svr.name = :server_name
  AND r.resource_type IN ('server.Cpus','server.Memory','server.FileSystems','server.Disks')
  AND s.stat_date BETWEEN :window_start_hh AND :window_end_hh
  AND r.dtime IS NULL
ORDER BY s.stat_date;
-- :window_*_hh 는 기준 시각 ± lookback 에서 Python이 계산한 YYYYMMDDHH 리터럴(이스케이프 적용)
```

> 주의: 프로필별 서버 식별 컬럼(`svr.name` vs hostname)과 DB 엔진(DB2 `FETCH FIRST` vs PG `LIMIT`)
> 분기는 기존 프로필/엔진 분기 규칙을 따른다. 하드코딩 날짜 금지(프로필 규칙) — 기준 시각은 입력값에서 계산.

### 4.3 프로세스 증거 (실시간 단면)

기존 `PolestarProcessApiClient.list_by_hostname` + `select_top_processes`/`mask_args` 재사용
(CPU/메모리 사건 한정 게이팅도 재사용).

- **중대한 한계(R-2)**: 폴스타 프로세스 API는 **실시간 단면만** 제공(과거 시점 조회 불가).
  - push(알람 직후) 또는 "방금/현재" pull → 사건 시점에 근접 → 유효 증거.
  - 과거 사건 pull → 프로세스 증거는 "현재 상태"이며 사건 시점과 다를 수 있음 → 리포트에
    **"현재 시점 참고용"** 으로 명시하고 인과 단정에 사용하지 않는다(증거 신뢰도 차등).

### 4.4 토폴로지 증거 (구조)

신규 `PolestarTopologyRepository`(고정 템플릿)로 대상 리소스의 부모/자식/동일서버 리소스를 조회.

- Phase A: 대상 서버의 서브리소스(어느 CPU/디스크/NW에서 사건이 시작됐는지 좁히기).
- Phase C: 동일 platform_resource/연관 서버로 확장(연쇄 후보군).

### 4.5 (후속) 변경/구성 이벤트 증거

배포·구성 변경·패치 이벤트가 폴스타/연관 DB에 있다면 "변경 직후 장애" 인과에 강력하나, 현재 스키마에서
표준 위치가 불확실 → **Phase C 조사 항목**으로 분리(현 계획 범위 외, 데이터 가용성 선확인 필요).

---

## 5. 아키텍처 — 진단 서브그래프 (diagnosis_graph)

> **⛔ v2 개정(2026-09-01 · 처분: 폐기)** — **본 절의 `diagnosis_graph`·`DiagnosisState`·노드 5종·
> `src/diagnosis/` 디렉토리는 만들지 않는다.** 조사 실행은 `sre_agent`에 위임됐고(D-118) 그
> 재편은 이미 실행됐다(`plans/85` §8.1). 자체 서브그래프를 신설하면 증거 수집이 2벌로 돌고
> D-118과 정면 충돌한다(§0.4-a (C)).
>
> **축소 잔존분은 하나뿐이다** — §5.2 `incident_scoper`의 **사건 시각 파싱**. 이것은 pull 질의
> 원문이 본체에만 있으므로 `src/nodes/fault_diagnosis.py` 안의 순수 함수로 남는다(§0.6 A′-5).
> 나머지 4노드의 책임은 **전부 `sre_agent` 안으로** 흡수된다(v2.1 — §0.4-b 대조표):
> `evidence_collector` → `sre_agent/application/evidence_prefetch.py` /
> `correlation_engine` → `sre_agent/domain/correlation.py` /
> `causal_reasoner` → `DiagnosisAgent`(구현 완료) / `diagnosis_reporter` → `briefing_builder`(구현 완료).
> **즉 본 절의 파이프라인은 사라지는 것이 아니라 소재지가 바뀐다** — LangGraph 래퍼만 걷힌다.
>
> 아래 다이어그램·상태·디렉토리 트리는 **설계 이력으로만 보존**한다.

알람 서브그래프(`build_alarm_graph`)와 동형의 LangGraph 서브그래프를 신설한다.
tool-calling 비의존 고정 파이프라인 → 워커 LLM(FabriX)로 동작(폐쇄망 호환).

```
              ┌──────────────────────────────────────────────────────────┐
 (pull/push)  │ DiagnosisState                                            │
   trigger ─► │  incident_scoper                                          │
              │     │ 대상 리소스·기준시각·구간·관심신호 확정              │
              │     ▼                                                      │
              │  evidence_collector  ── asyncio.gather(타임아웃) ──┐       │
              │     ├─ 알람 타임라인 (§4.1, PolestarAlarmHistory*)  │       │
              │     ├─ 메트릭 추이   (§4.2, PolestarMetricRepo)     │       │
              │     ├─ 프로세스 단면 (§4.3, PolestarProcessApi)     │       │
              │     └─ 토폴로지      (§4.4, PolestarTopologyRepo)   │       │
              │     ▼ EvidenceBundle (부분 실패 허용)               ◄┘      │
              │  correlation_engine (결정적 Python, §6)                    │
              │     │ 타임라인 병합·메트릭 이상·선후/연쇄 판정         │
              │     ▼ CorrelationResult                                    │
              │  causal_reasoner (LLM, §7)                                 │
              │     │ 원인 가설 순위 + 근거 인용 + 신뢰도 + 권고        │
              │     ▼ DiagnosisReport                                      │
              │  diagnosis_reporter                                        │
              │     │ 자연어/문서 + (push 시) 알림                       │
              └─────┴────────────────────────────────────────────────────┘
```

### 5.1 DiagnosisState (TypedDict)

```python
class DiagnosisState(TypedDict, total=False):
    # 입력/범위
    db_id: str
    target: dict                 # {server_name, hostname, resource_id?, resource_type?}
    reference_time: str          # 기준 시각(ISO) — now() 금지(§3.4)
    window: dict                 # {start, end, lookback_minutes, granularity: h|d|m}
    signals_of_interest: list[str]  # ["cpu","memory","filesystem","disk","alarm"]
    trigger: str                 # "pull" | "push"
    source_alarm: Optional[dict] # push 트리거 알람(AlarmEvent dict)

    # 증거
    evidence: dict               # EvidenceBundle (알람/메트릭/프로세스/토폴로지)

    # 상관분석(결정적)
    correlation: dict            # CorrelationResult (타임라인·이상·선후·연쇄)

    # 결과
    report: dict                 # DiagnosisReport (가설 순위·근거·권고)
    error: Optional[str]
```

> AgentState(메인 그래프)와는 분리된 서브그래프 상태(알람 서브그래프 `AlarmState`와 동일 정책).
> pull 경로에서 subagent handler가 결과를 메인 `organized_data`/`final_response`로 변환(§8.1).

### 5.2 노드별 책임

| 노드 | 계층 | 책임 | 재사용/신규 |
|------|------|------|-----------|
| `incident_scoper` | application | 대상 리소스·기준시각·구간·정밀도(h/d/m)·관심신호 확정. pull은 NL 파싱(LLM 소폭) / push는 알람에서 직접 도출 | 신규(얇음). 서버명 매칭은 프로필 규칙 재사용 |
| `evidence_collector` | application | 4종 증거 동시 수집(`asyncio.gather` + `wait_for` 타임아웃, 부분실패 허용) | `alarm_context_enricher` 패턴 재사용 |
| `correlation_engine` | application→domain | EvidenceBundle → 타임라인 병합·메트릭 이상탐지·선후/연쇄 판정(순수 함수 호출) | 신규(domain 순수함수 §6) |
| `causal_reasoner` | application | CorrelationResult 요약 텍스트 → LLM 인과 가설 순위·근거·권고 | `alarm_analyzer` 프롬프트/파싱 패턴 재사용 |
| `diagnosis_reporter` | application | DiagnosisReport 조립 → 자연어/문서/알림 | `output_generator`·`alarm_notifier` 재사용 |

### 5.3 Clean Architecture 배치 (계층 규칙 준수)

기존 `src/alarm/` 구조를 미러링하여 `src/diagnosis/` 신설:

```
src/diagnosis/
├── domain/
│   ├── models.py            # IncidentScope, EvidenceBundle, MetricSeries,
│   │                        #   CorrelationResult, RootCauseHypothesis, DiagnosisReport
│   ├── correlation.py       # 결정적: 타임라인 병합·선후 판정·연쇄 판정 (순수 함수)
│   └── metric_anomaly.py    # 결정적: baseline 대비 이상탐지 (z-score/임계) (순수 함수)
├── infrastructure/
│   ├── polestar_metric.py   # PolestarMetricRepository (cmm_metric_stat_[h,d,m] 고정 SQL)
│   ├── polestar_topology.py # PolestarTopologyRepository (cmm_resource 계층 고정 SQL)
│   └── polestar_incident_alarms.py  # build_incident_alarms_sql (이력 repo 확장)
├── application/
│   └── nodes/
│       ├── incident_scoper.py
│       ├── evidence_collector.py
│       ├── correlation_engine.py
│       ├── causal_reasoner.py
│       └── diagnosis_reporter.py
├── orchestration/
│   └── diagnosis_graph.py   # build_diagnosis_graph, DiagnosisState
└── prompts/
    ├── incident_scoper.py   # (pull) NL→사건범위 파싱 프롬프트
    └── causal_reasoner.py    # 인과 추론 시스템/유저 프롬프트
```

의존 방향: `domain → infrastructure → application → orchestration` (정방향). `arch_check.py`로 검증.
프로세스 증거는 `src/alarm/infrastructure`·`src/alarm/domain`을 재사용(동일 application 계층 이하 의존 OK).

---

## 6. 결정적 상관분석 엔진 (correlation_engine)

> **★ v2 개정(2026-09-01 · 처분: 차용 — 잔여 본체)** — 본 절의 **알고리즘 명세는 그대로 유효**하고,
> 바뀌는 것은 **배치와 입력원**이다.
>
> - **배치**: `src/diagnosis/domain/`이 아니라 **`sre_agent/domain/correlation.py`**(순수 함수) +
>   `sre_agent/application/evidence_prefetch.py`(사전수집). 근거는 §0.4-a — 조사 로직의 소유권은
>   `sre_agent`이고(D-118), 원시 도구 출력은 그 프로세스를 **벗어나지 않는다**(실측 ①).
> - **제약(신규)**: `correlation.py`는 **벤더 중립**이어야 한다 — 시각·수치·라벨만 다루고 폴스타
>   어휘는 `evidence_prefetch.py`의 도구 인자에만 둔다. `sre_agent`는 `overfit_check` 스캔 대상이
>   **아니어서** 리터럴이 무검열로 쌓일 수 있으므로, 이 제약은 게이트가 아니라 **설계 규율**로
>   지켜야 한다(§0.4-a 말미 · §16 U-D).
> - **입력원의 선행 조건**: §6.1의 좌표계도 §6.2의 baseline도 **사건 구간 데이터가 있어야**
>   계산된다. 현행 도구는 전부 now 앵커다(**G1·G2·G3** — §0.3). **§0.6 A′-1~A′-3이 본 절의
>   선행 작업**이며, 그것 없이 §6만 구현하면 최신 데이터에 상관을 매기는 셈이 된다.
> - **§6.3 재조정**: 다중서버 연쇄는 `noise_gate/domain/topology.py::DependencyGraph`가 게이트에서
>   이미 수행한다(BFS·순환 가드). 진단이 새로 할 일은 **단일 서버 서브리소스 시차 순서화**뿐이다.

`src/diagnosis/domain/correlation.py` + `metric_anomaly.py` — **순수 함수, DB/LLM 비의존**(테스트·감사 용이).

### 6.1 통합 타임라인 병합

- 입력: 알람 이벤트(시각·severity·alarm_name·resource), 메트릭 이상점(시각·지표·값).
- 처리: 모든 증거를 **기준 시각** 좌표계로 정렬(`reference_time` 기준 상대 분). now() 미사용.
- 산출: `[{t_offset_min, kind, detail}]` 시간순 — "무엇이 먼저 일어났는가"의 결정적 근거.

### 6.2 메트릭 이상 탐지 (`metric_anomaly.py`)

- baseline: 사건 직전 동시간대 N기간의 `avg_val` 평균(μ)·표준편차(σ).
- 이상 판정(택1, 보수적 결합):
  - z-score: `(window_max - μ) / σ ≥ z_threshold`(기본 3.0) → "급등".
  - 절대 임계: 알람 임계(가능 시) 또는 사용률 ≥ 고정선(예: CPU 90%, FS 90%) 초과.
  - 지속성: 연속 K구간 이상 → "지속적", 1구간 → "스파이크".
- 선행성: 메트릭 이상 시각이 알람 발생보다 앞서면 "메트릭 선행"(인과 후보 강화 신호).
- 산출: 지표별 `{is_anomalous, kind(spike|sustained), peak_value, peak_time, lead_lag_vs_alarm}`.

### 6.3 선후/연쇄 판정

- 단일 서버(Phase A): 어느 서브리소스 이상이 먼저였는지(예: 디스크 IO 급등 → CPU iowait 상승 →
  CPU 알람) 시차로 순서화. **상관 ≠ 인과**임을 명시(가설 신뢰도에 반영).
- 다중 서버(Phase C): 토폴로지상 연관 서버 간 알람 시차로 연쇄 후보 산출.

### 6.4 출력: CorrelationResult

```python
@dataclass
class CorrelationResult:
    timeline: list[dict]              # 정렬된 사건 타임라인
    metric_findings: dict             # 지표별 이상 판정
    alarm_summary: dict               # 사건 구간 알람 빈도/severity 추이/해소 여부
    leading_signal: Optional[str]     # 가장 먼저 이상을 보인 신호(인과 후보)
    cascade: list[dict]               # (Phase C) 연쇄 후보
    notes: list[str]                  # 데이터 한계(정밀도 부족/프로세스 단면 등) 결정적 기록
```

`notes`에 "메트릭 월단위만 존재 → 시간 정밀도 없음", "프로세스는 현재 단면" 등 한계를 **결정적으로** 기록 →
LLM이 이를 그대로 신뢰도에 반영(환각 방지).

---

## 7. LLM 인과 추론 (causal_reasoner)

> **v2 개정(2026-09-01)** — §7.1·§7.3은 **구현 완료(초과 충족)**다. `briefing_builder._is_cited`가
> 인용 없는 단정을 `[가설]`로 **결정적으로 강등**하며, 이는 §7.3의 "인용 강제"보다 강하다.
> LLM 인과 서술 자체는 `sre_agent` `DiagnosisAgent`가 담당한다(§7 전체를 대체).
>
> **잔여는 §7.2 하나** — `briefing.cause`가 **단일 문자열**이라 `rank`·`confidence`·복수 가설이
> 없다(실측 `briefing_builder.py`: `cause = cited[-1]`). 아래 JSON 스키마는 **`briefing_builder`의
> 확장 목표 형태**로 차용하되, **출력 계약(G6)을 먼저 닫아야 한다** — 지금 새 필드를 추가하면
> 소비자 2곳의 키 목록에 없어 **그대로 탈락한다**(§0.3 G6 · §0.6 A′-0).

`alarm_analyzer`의 프롬프트/파싱 패턴을 계승. **계산된 증거 요약 텍스트만** 입력.

### 7.1 입력 (LLM에 주입)

- 사건 범위(대상·기준시각·구간·정밀도)
- 알람 요약(빈도·severity 추이·주요 알람명, Plan 47 통계 렌더 재사용)
- 메트릭 findings 요약(지표별 이상/선행 여부·피크)
- 프로세스 단면(있으면, 마스킹된 top N — "현재 시점" 라벨)
- 토폴로지 요약(서브리소스/연관)
- 상관 notes(데이터 한계)

### 7.2 출력 스키마 (JSON)

```json
{
  "incident_summary": "사건 한줄 요약",
  "root_cause_hypotheses": [
    {
      "rank": 1,
      "cause": "추정 원인",
      "confidence": "high|medium|low",
      "evidence": ["인용한 증거(타임라인/메트릭/프로세스 항목)"],
      "reasoning": "왜 이 근거가 이 원인을 가리키는가"
    }
  ],
  "recommended_actions": ["권고 조치(우선순위순)"],
  "further_investigation": ["증거 부족으로 추가 확인이 필요한 항목"],
  "data_limitations": ["정밀도/단면 등 한계"]
}
```

### 7.3 프롬프트 규칙 (환각 차단 — Known Mistakes 계승)

- 주입된 수치/시각만 사용. **새 수치·시각을 생성·추정 금지**.
- 모든 가설은 `evidence`에 실제 주입된 증거 항목을 인용. 인용 불가하면 `further_investigation`으로.
- `data_limitations`(상관 notes)를 반드시 신뢰도에 반영(예: 월단위 메트릭만 → confidence ≤ medium).
- 프로세스 단면은 "현재 시점" — 과거 사건이면 인과 단정 금지(참고로만).
- 상관 ≠ 인과 — 선행 신호도 "유력 후보"로 서술(단정 금지).
- 폴스타 도메인 지식 주입(재사용): severity(3=심각,2=경고,1=주의,0=해소), avail_status(0=정상, ≠0=비정상).

---

## 8. 트리거 통합

> **✅ v2 개정(2026-09-01 · 처분: §8.1·§8.2 완료 / §8.3 폐기)** — 트리거 이중화는 구현됐다.
> pull은 `fault_diagnosis` 의도 + `src/nodes/fault_diagnosis.py`(인가 게이트·가용성 사전 판정 포함),
> push는 `noise_gate/application/nodes/investigation_trigger.py`(D-124 CW-A)다.
> §8.3 `POST /diagnosis/analyze`는 **폐기** — 진입점은 챗·알람 2종으로 충분하고, 라우트 신설은
> 인증·감사 표면만 늘린다.
>
> **단, 트리거가 기준시각을 나르지 않는다**(G1) — `sre_diagnose`에 시각 인자가 없고
> `_job_to_question`은 push의 `payload.event.alarmTime`을 **보유하고도 질문에 싣지 않는다**.
> 이 절의 보완 작업은 §0.6 **A′-5** 하나다.

### 8.1 pull — 의도 + subagent (메인 그래프 / 오케스트레이션)

1. **의도 추가** (`src/routing/semantic_router.py`, `domain_config.py`): `fault_diagnosis` 의도.
   - 트리거 표현: "원인 분석", "장애 진단", "왜 ~ 알람", "무슨 일이 있었는지", "장애 분석".
   - `data_query`/`alarm_query`(단순 조회)와 구분: 진단은 **사건 구간 + 인과**를 요구.
2. **subagent 등록** (`src/orchestration/subagents.py`):
   ```python
   "fault_diagnosis": SubAgentSpec(
       "fault_diagnosis", "장애 진단·원인 분석(사건 구간 다중신호 상관·인과)", run_fault_diagnosis
   ),
   ```
   - `run_fault_diagnosis(task, isolated, *, llm, app_config)`:
     - `task["sub_query"]`에서 incident_scoper 입력 도출 → `build_diagnosis_graph(...).ainvoke(...)`
     - DiagnosisReport → `{organized_data, query_results, source}` 형태로 변환(메인 응답 계약 준수).
   - 등록만으로 **Track A(의도분해)** 위임 대상이 되고, **Track B(deepagents)** 에서는
     `deepagents_tools.build_tools`가 레지스트리를 `@tool`로 노출하므로 자동 편입(Plan 49 §4.2).
3. **플래너 프롬프트 보강** (`src/prompts/intent_planner.py`): 복합 질의에서 진단 task 분해 인식
   (예: "A 서버 사양 알려주고, 어제 장애 원인도 분석해줘" → data_query + fault_diagnosis 2 task).
4. **(폴백) semantic_router 단일 경로**: 오케스트레이션 미활성 시 `fault_diagnosis` 의도를 진단
   서브그래프로 직접 라우팅(`general_inference`처럼 고정 노드 연결).

### 8.2 push — 알람 파이프라인 훅 (opt-in)

1. `src/alarm/orchestration/alarm_graph.py`: `enable_diagnosis_on_alarm=true`이고 알람이
   **고심각도(severity≥경고) 또는 첫발생/급증(`AlarmHistoryStats.pre_classification`)** 이면,
   `alarm_analyzer` 후 진단 서브그래프를 호출(또는 enricher 결과를 DiagnosisState로 넘겨 재사용).
2. 진단 결과(원인 가설·근거)를 `AlarmAnalysisResult`에 병합 → 기존 `alarm_notifier`가 알림에 첨부.
3. **기존 단건 분석 대체 아님** — 플래그 off거나 저심각도면 기존 경로 무변경(회귀 없음, 성공기준 4).
4. 부하 보호: push 진단은 enricher가 이미 수집한 알람이력·프로세스를 **재사용**하고 메트릭/토폴로지만
   추가 수집(중복 조회 최소화).

### 8.3 API 엔드포인트 (선택)

`src/api/routes/diagnosis.py`(신규, `alarm.py` 테스트 엔드포인트 패턴 계승):
- `POST /diagnosis/analyze` — {db_id, target, reference_time, lookback} → DiagnosisReport(dry-run 지원).
- (재사용) 진단 결과 SSE는 기존 `alarm_bus`/notification stream 재활용 가능.

---

## 9. 출력 (DiagnosisReport)

> **★ v2 개정(2026-09-01 · 처분: §9.1 차용 — 단 선결 결함 있음)** — §9.2 채널은 완료다
> (`fault_diagnosis._respond` · `alarm_notifier`). §9.1의 **상대시각 타임라인(`T-15m`)은 잔여**다.
>
> **선결 결함(G6 — 실측)**: 생산자 `briefing_builder`가 내는 키와 소비자 2곳이 읽는 키가 어긋난다.
> `limitations`(list) ↔ `limitation`(단수)이라 **한계 서술이 사용자에게 도달하지 않고**,
> `evidence` 키는 애초에 생산되지 않으며, `recommendation`(dict)·`timeline`(list)은 `str(val)`로
> 렌더돼 **Python repr이 그대로 노출**된다. pull(`fault_diagnosis.py:56`)과
> push(`alarm_notifier.py:148`)가 **같은 잘못된 키 목록**을 쓰는 대칭 결함이다.
> 상세·처분은 §0.3 G6, 착수는 §0.6 **A′-0**(최우선).

### 9.1 구조화 리포트

```
[장애 진단 리포트]
대상: <서버/리소스>   사건 시각: <기준시각>   구간: <start~end> (정밀도: 시/일/월)

■ 사건 요약: <한줄>

■ 타임라인:
  T-15m  메트릭  디스크 IO 급등 (MaxIORate baseline 대비 +320%)
  T-12m  메트릭  CPU iowait 상승 (avg 35%→88%)
  T-10m  알람    [심각] CPU Utilization Critical
  T-2m   알람    [해소] CPU Utilization Clear

■ 원인 가설(신뢰도):
  1) (high)  디스크 IO 폭주로 인한 CPU iowait 상승 — 근거: 디스크 IO가 CPU 알람 12분 선행
  2) (medium) 배치 작업 동시 실행 — 근거: 동시간대 주기적 패턴 이력

■ 권고 조치: ...
■ 추가 확인 필요: ...
■ 데이터 한계: 프로세스는 현재 시점 단면(사건 시점 아님)
```

### 9.2 채널

- 자연어 응답(기본) — pull/push 공통.
- (선택) Excel/Word — 기존 `output_generator` 재사용(양식 업로드 시).
- (push) WorkB/webhook/SSE — 기존 `alarm_notifier`/`notification_bus` 재사용.

### 9.3 (Phase C) 진단 이력/피드백

진단 결과·운영자 피드백("실제 원인은 X였다")을 저장하여 추후 재현/학습 입력으로 활용(범위 외, 후속).

---

## 10. 상태 / 설정 / API 변경

> **⛔ v2 개정(2026-09-01 · 처분: §10.1 폐기 / §10.2·10.3 폐기)** — 신규 `DiagnosisConfig`는
> **만들지 않는다.** 같은 역할의 설정이 이미 두 곳에 있다: 본체 `investigation_*` 13필드
> (`src/config.py:889~907` — url/token/타임아웃/게이트) 와 `sre_agent/settings.py`
> (`investigation_timeout_seconds`·`max_concurrent`·`hourly_budget`·`dedup_ttl`·`severity_judge_enabled`·
> `remediation_recommender_enabled`). 세 번째 설정 계층을 만들면 어느 값이 유효한지가 흐려진다.
>
> **본 개정이 필요로 하는 신규 플래그는 다음뿐이며, 각각 소속 패키지 설정에 붙인다**
> (전부 기본 off = 현행 동작 비트 동일 — `plans/80` §5.4-③):
>
> | 플래그 | 소속 | 용도 |
> |---|---|---|
> | `incident_correlation_enabled` | `sre_agent/settings.py` | 사전수집·상관 계산 활성(A′-3). **기본 off** |
> | `correlation_z_threshold`(기본 3.0) · `correlation_baseline_periods`(기본 7) | `sre_agent/settings.py` | §6.2 판정 상수 |
> | `investigation_prompt_additions_enabled` | `sre_agent/settings.py` | G5 지침 주입 배선(A′-4) |
> | `investigation_reference_time_enabled` | 본체 `NoiseGateConfig` | A′-5 시각 파싱·전달 |
>
> §10.2(상태)는 §5 폐기와 함께 무효, §10.3(API)은 §8.3 폐기와 함께 무효다.

### 10.1 설정 (`src/config.py`) — 신규 `DiagnosisConfig`

```python
class DiagnosisConfig(BaseSettings):           # env_prefix="DIAGNOSIS_"
    enabled: bool = False                       # 진단 기능 전체 게이트
    enable_on_alarm: bool = False               # push(알람 자동 진단) opt-in
    on_alarm_min_severity: int = 2              # 자동 진단 최소 심각도(경고 이상)
    default_lookback_minutes: int = 120         # 기본 사건 구간(기준시각 ±)
    metric_baseline_days: int = 7               # 이상탐지 baseline 기간
    anomaly_z_threshold: float = 3.0            # z-score 임계
    collect_timeout_seconds: float = 8.0        # 증거 수집 전체 타임아웃
    evidence_cache_ttl_seconds: int = 300       # 단기 조회 캐시 TTL
    metric_max_rows: int = 5000                 # 메트릭 조회 상한(10,000 이내)
    include_topology_neighbors: bool = False     # Phase C 연쇄 토폴로지 확장
```

- `.env.example`에 `DIAGNOSIS_*` 추가. 기본 비활성(opt-in) → 회귀 없음.

### 10.2 상태

- 진단 서브그래프는 자체 `DiagnosisState`(§5.1) 사용 — 메인 `AgentState`에 대량 필드 추가 불필요.
- pull subagent 결과는 기존 `organized_data`/`final_response` 계약으로 변환(추가 필드 최소).

### 10.3 API

- (선택) `POST /diagnosis/analyze` 추가(§8.3). 인증/마스킹/감사 로그는 기존 미들웨어 재사용.

---

## 11. 단계별 구현 계획

> **⛔ v2 개정(2026-09-01 · 처분: 폐기 → §0.6으로 대체)** — 아래 Phase A/B/C는 `src/diagnosis/`
> 서브그래프 신설을 전제로 짜여 있어 **더 이상 실행 가능한 순서가 아니다**. 현행 착수 단계의
> 정본은 **§0.6 (A′-0 ~ C′)** 이다. 아래는 설계 이력으로만 보존한다.

각 단계는 verify 기준을 동반(목표 기반 실행 — CLAUDE.md §4).

### Phase A — pull 단일서버 심층 진단 (MVP)

1. domain 모델·순수함수: `models.py`, `correlation.py`, `metric_anomaly.py`
   → verify: 단위 테스트(타임라인 병합·이상탐지·선후 판정, now() 미사용 검증), `arch_check` 통과.
2. infrastructure repo: `polestar_metric.py`, `polestar_topology.py`, `polestar_incident_alarms.py`
   → verify: 고정 SQL 생성·`_sql_literal` 이스케이프·읽기전용·프로필별 서버명 컬럼 분기 단위 테스트.
3. application 노드 5종 + `diagnosis_graph.py`
   → verify: 증거 일부 실패 시 graceful 진행, 타임아웃 동작, 모의 데이터 E2E(LLM 모킹).
4. causal_reasoner 프롬프트 + 파싱
   → verify: 주입 외 수치 환각 없음(증거 인용 강제), data_limitations 반영, JSON 파싱 견고성.
5. pull 트리거: 의도(`fault_diagnosis`) + subagent 등록 + 플래너 프롬프트 보강
   → verify: "원인 분석" 질의가 진단 경로로 라우팅, 단순 조회는 기존 경로 유지(분류 회귀).
6. 출력: 자연어 리포트
   → verify: 타임라인·가설·근거·권고·한계 포함, 마스킹 적용.

### Phase B — push 알람 자동 진단 (opt-in)

7. `alarm_graph` 훅 + `enable_on_alarm` 게이트 + enricher 증거 재사용
   → verify: 고심각도/첫발생/급증만 진단, 저심각도/off는 기존 단건 분석 무변경(회귀), 알림 첨부.

### Phase C — 다중서버 연쇄 RCA + 문서/이력 (후속)

8. 토폴로지 연쇄 상관(`include_topology_neighbors`) + 연관 서버 알람/메트릭 확장.
9. Excel/Word 리포트, 진단 이력/피드백 저장, 변경/구성 이벤트 증거(데이터 가용성 선조사).

---

## 12. 테스트 계획

> **v2 개정(2026-09-01 · 처분: 개정)** — 아래 표의 **검증 내용은 대부분 유효하나 배치처가 바뀐다**
> (`tests/test_diagnosis/` → `mcp_server/tests/`·`sre_agent/tests/`·`tests/`). 각 패키지가 자기
> 테스트를 소유하므로(D-139) 아래처럼 나뉜다.
>
> | 원 테스트 | 개정 후 배치 | 비고 |
> |---|---|---|
> | `test_correlation_timeline` · `test_metric_anomaly` · `test_cascade_single_server` | `sre_agent/tests/test_correlation.py` | 순수 함수. now() 미사용 단언 유지 |
> | `test_metric_repo_sql` · `test_incident_alarms_sql` | `mcp_server/tests/test_polestar_tools.py` 확장 | **미지정 시 종전 SQL과 문자열 동일** 단언 추가(회귀 0 증명) |
> | `test_evidence_collector_partial_fail` | `sre_agent/tests/test_evidence_prefetch.py` | 도구 호출자를 스텁 주입. 부분 실패 시 `notes` 결정적 기록 |
> | `test_causal_reasoner_no_hallucination` | `sre_agent/tests/test_briefing_builder.py` 확장 | 주입 수치만 사용 |
> | `test_diagnosis_graph_e2e` | **폐기**(§5 폐기) | 대체: `sre_agent/tests/test_investigation_e2e.py` |
> | `test_pull_intent_routing` · `test_push_gate` | 기존 `tests/`·`noise_gate/tests/` (이미 존재) | 회귀 확인만 |
> | `test_readonly_guard` · `test_arch_check` | 기존 게이트 유지 | `mcp_server`·`sre_agent`는 자체 arch_check |
>
> **신규 필수 3건**(본 개정 고유):
> - `test_briefing_key_contract` — 생산자 키 집합 ⊇ 소비자 키 목록. **pull·push 두 소비자를 함께**
>   단언한다(한쪽만 고치는 비대칭 재발 차단).
> - `test_briefing_render_no_repr` — 렌더 결과에 `{'`·`['`가 없음(dict/list repr 누출 차단).
> - `test_prompt_additions_wired` — `_default_diagnose_fn`이 `system_prompt_additions`를 실제로
>   전달함(현재 0건인 사실을 테스트로 고정).

| 테스트 | 검증 내용 |
|--------|----------|
| `test_correlation_timeline` | 타임라인 병합·정렬, 기준시각 좌표계, now() 미사용 |
| `test_metric_anomaly` | z-score/임계/지속성 판정, baseline 계산, 선후(lead/lag) |
| `test_cascade_single_server` | 단일서버 서브리소스 선후 순서화(Phase A) |
| `test_metric_repo_sql` | 고정 SQL 생성·이스케이프·정밀도 분기(h/d/m)·프로필 서버명 컬럼 |
| `test_incident_alarms_sql` | 사건구간 다중알람 조회 SQL(해소 포함 D-030, COALESCE 조인) |
| `test_evidence_collector_partial_fail` | 증거 일부 실패/타임아웃 시 graceful 진행 |
| `test_causal_reasoner_no_hallucination` | 주입 외 수치 미생성, 증거 인용 강제, 한계 반영 |
| `test_diagnosis_graph_e2e` | (LLM 모킹) scoper→…→reporter 전체 흐름 |
| `test_pull_intent_routing` | `fault_diagnosis` 의도 분류·subagent 위임, 단순조회 회귀 |
| `test_push_gate` | `enable_on_alarm` on/off·심각도 게이트, off 시 알람경로 무변경 |
| `test_readonly_guard` | 진단 전 경로 SELECT only 유지 |
| `test_arch_check` | `src/diagnosis` 계층 위반 0 |

> 폴스타 DB/프로세스 API 실연동 테스트는 통합 마커로 분리(CI 스킵, 자원 가용 시 실행) — Plan 49 정책 계승.

---

## 13. 리스크 및 대응

> **v2 개정(2026-09-01)** — R-1~R-10은 **유효**하다(특히 R-2 프로세스 단면·R-3 정밀도·R-10 기준시각).
> 본 개정이 드러낸 리스크를 추가한다(**v2.1 반영** — R-12 소멸 · R-14 재정의 · R-15·R-16 신설).
>
> | # | 리스크 | 심각도 | 대응 |
> |---|---|---|---|
> | **R-11** | **now 앵커 증거로 과거 사건을 진단**해 근거 없는 결론을 낸다(G1 — 현행 실동작) | **High** | A′-1·A′-5 선행. 시각 인자 미지원/미전달이면 브리핑 `limitations`에 *"증거 구간이 사건 시각과 다를 수 있음"* 을 **결정적으로** 명시 |
> | ~~R-12~~ | ~~상관 도구를 LLM이 호출하지 않는다~~ | — | **소멸(v2.1)** — 사전수집이 결정적이라 LLM 호출 여부에 걸리지 않는다(§0.4-a (D)) |
> | **R-13** | 브리핑 계약을 한쪽만 고쳐 **pull·push 비대칭**이 남는다 | Med | 세 파일 동시 수정 + `test_briefing_key_contract`가 두 소비자를 함께 단언 |
> | ~~R-14~~ | ~~`correlation.py`의 스키마 리터럴이 overfit_check 기준선을 오염~~ | — | **재정의(v2.1)** — 상관 모듈이 `sre_agent`로 옮겨 스캔 대상 자체가 아니게 됐다. 위험이 사라진 게 아니라 **검열 없는 축적**으로 성격이 바뀌었다 → R-16 |
> | **R-15** *(v2.1)* | 사전수집이 **holmes 내부 API**(`ToolInvokeContext`)에 의존해 holmesgpt 상향 시 깨진다 | Med | 버전 고정(0.36.0) 하에 착수하고 B′에서 `mcp` 클라이언트 직접 호출로 교체 + `pyproject.toml` 명시 선언 |
> | **R-16** *(v2.1)* | `sre_agent`가 `overfit_check` 스캔 밖이라 상관 모듈에 **폴스타 리터럴이 무검열로 축적** | Low | `correlation.py` 벤더 중립 규율(게이트 아님 — 설계 규율) + 착수 시 스캔 편입 판정(§16 U-D) |

| # | 리스크 | 심각도 | 대응 |
|---|--------|--------|------|
| R-1 | LLM 인과 환각(근거 없는 단정) | High | 수치는 Python 결정(§6), 증거 인용 강제·신뢰도·한계 반영(§7.3), no-hallucination 테스트 |
| R-2 | 프로세스 API 실시간 단면뿐 → 과거 사건 부정합 | High | "현재 시점" 라벨, 과거 사건 인과 단정 금지(참고만), push/최근 pull에서만 강증거(§4.3) |
| R-3 | 메트릭 시간 정밀도(과거는 일/월만) | Med | 정밀도 분기(h/d/m), 한계를 correlation notes·리포트에 명시, confidence 상한 |
| R-4 | 폴스타 DB 부하(메트릭 대량 조회) | Med | 고정 SQL + 행수 상한(`metric_max_rows`) + 타임아웃 + 단기 캐시(TTL) — Plan 47 계승 |
| R-5 | 다중서버 연쇄 상관 복잡·오탐 | Med | Phase C로 분리, 단일서버 MVP 우선, 상관≠인과 명시 |
| R-6 | 프로필별 서버 식별 컬럼 상이(공동존 r.name) | Med | 기존 프로필 규칙·Plan 47 §5.3 재사용, 프로필별 분기 테스트(Known Mistakes 2026-06-10) |
| R-7 | 진단 도입이 기존 경로 회귀 유발 | Med | 전 기능 opt-in 플래그(`DIAGNOSIS_*` 기본 off), 별도 서브그래프/서브패키지, 회귀 테스트 |
| R-8 | 의도 분류 혼동(단순 조회 vs 진단) | Low | 의도 설명·few-shot로 "사건 구간+인과"만 진단, 모호 시 조회로 보수적 폴백 |
| R-9 | DB 엔진 분기(DB2/PG) 문법 차이 | Low | 기존 엔진 분기 규칙 재사용(LIMIT/FETCH FIRST, 날짜 함수), 프로필 기반 |
| R-10 | 기준 시각/타임존 오류 | Low | now() 금지·입력 기준시각 사용(§3.4), 타임라인 좌표계 단위 테스트 |

---

## 14. 의사결정 영향 (`docs/02_decision.md`)

> **⚠ v2 정정(2026-09-01 · 채번 오류)** — 아래 본문의 **D-038은 이미 소진됐다**:
> `docs/02_decision.md:313` = *"D-038. 사용법/지원 소스 안내 — general_inference 그라운딩"*.
> 2026-06-26 작성 시점의 예약이 **`docs/02_decision.md`에 등재되지 않은 채** 다른 작업에 나갔다
> — D-161 부기의 *"계획서에만 적힌 예약은 효력이 없다"* 가 정확히 실현된 사례다.
>
> **착수 시 채번 규칙을 다시 돌린다** — `docs/02_decision.md`의 ①`## D-` 헤더 ②「변경 이력」 표
> ③「채번 이력」 표를 모두 grep해 최댓값+1. **2026-09-01 실측 최댓값 D-193 → 다음 D-197**이나,
> **본 계획서에 적는 것만으로는 예약 효력이 없다** — 착수 시 「채번 이력」 표에 행으로 등재해야 한다.
>
> **등재 내용도 개정한다** — 아래 D-038.1~.5 중 **.1·.2·.3은 이미 다른 결정으로 실현**됐고
> (D-118 위임 · D-122 고정 SQL 도구 · D-035 결정적 계산), **.4는 구현 완료**(D-124)다.
> 신규 결정으로 남는 것은 다음 3건이다:
>
> - **(a) 사건 시각 좌표계를 조사 계약의 1급 인자로 승격** — `sre_diagnose`·폴스타 도구에
>   `reference_time`/`lookback`을 추가하고 미지정 시 종전 동작 유지. 근거: §3.4 now() 금지가
>   도구 계약에서 깨져 있었다(G1).
> - **(b) 결정적 상관 계산의 소재지 = `sre_agent`** (v2.1 정정) — 증거 사전수집(application) +
>   상관 계산(domain)을 조사 소유자 안에 두고, 결과를 조사 **전에** 프롬프트로 주입한다.
>   근거: §0.4-a 4안 비교 — 원시 도구 출력이 `sre_agent`를 벗어나지 않으므로(실측 ①) 별도 폴더는
>   증거 재수집이 강제되고, `mcp_server`에 두면 읽기 경계의 헌장을 넘어선다.
> - **(c) 브리핑 6요소 계약의 정본 = 생산자(`briefing_builder`)** — 소비자는 생산자 키를 따르고,
>   list·dict는 전용 렌더러를 거친다. 근거: G6 대칭 결함.
>
> 기존 결정과의 정합: D-003·D-118·D-119·D-122·D-124·D-035·D-139와 **충돌 없음**(전부 읽기 전용·
> 계약 확장·기본 off). 아래 원문은 이력으로 보존한다.

작업 착수 시 **D-038. 장애진단·원인분석 서브시스템 도입**을 신규 등재한다(번호 체계 D-NNN 준수).
핵심 결정 사항(§3):

- **D-038.1** 진단은 기존 자산 조합으로 구현(별도 저장소·데몬 미신설). 근거: Plan 47 §3.1 계승, 정합성·비용.
- **D-038.2** 증거 수집은 고정/파라미터 SQL(LLM 생성 아님). 근거: 결정성·지연·안전(§3.2).
- **D-038.3** 수치는 Python 결정, 인과 해석만 LLM. 근거: 환각 차단(§3.3, Plan 47 §3.3).
- **D-038.4** 트리거 이중화(pull 의도/subagent + push 알람훅 opt-in), 기존 경로 무변경.
- **D-038.5** 단일서버 심층 RCA 우선, 다중서버 연쇄는 Phase C.

기존 결정과의 정합:
- D-003(읽기 전용) 유지 — 진단도 SELECT만.
- D-029/D-030/D-031/D-032/D-035/D-036(알람 계열) 재사용·확장 — 충돌 없음.
- D-037(오케스트레이션) — subagent 등록으로 Track A/B에 자연 편입, tool-calling 비의존(폐쇄망 호환).

> **사용자 확인 필요 항목**(§16)에 대한 결정이 내려지면 D-038 본문에 반영한다.

---

## 15. 변경 범위 요약

> **⛔ v2 개정(2026-09-01 · 처분: 폐기 → §0.4 표로 대체)** — 아래 신규/수정 파일 목록은
> `src/diagnosis/` 신설을 전제하므로 **전부 무효**다. §15.0의 백엔드 배선 분석(Track A/B/
> semantic_router)도 **이미 실행 완료**된 내용이다(`fault_diagnosis` 의도·노드·subagent 실재).
> 현행 변경 범위의 정본은 **§0.4 배치 표**이며, 요약하면 다음 6파일이다:
>
> | 파일 | 변경 |
> |---|---|
> | `mcp_server/mcp_server/polestar_tools.py` | 도구 인자 확장(G1·G2) + `polestar_incident_alarms` 신설(G3) — **읽기 경계 작업만** |
> | `sre_agent/sre_agent/domain/correlation.py` | **신규** — §6.1·6.2·6.3 순수 함수(벤더 중립) |
> | `sre_agent/sre_agent/application/evidence_prefetch.py` | **신규** — 구간 증거 결정적 사전수집(호출자 주입) |
> | `sre_agent/sre_agent/interface/mcp_service.py` | `sre_diagnose` 시각 인자 · `_job_to_question` 시각 반영 · `system_prompt_additions` 배선(G5) + 사전수집 조립·주입 |
> | `sre_agent/scripts/arch_check.py` | 신규 2모듈 계층 등록(`domain`·`application`) |
> | `sre_agent/sre_agent/application/briefing_builder.py` | rank·confidence·상대시각 타임라인·`notes`(§7.2·§9.1) |
> | `src/nodes/fault_diagnosis.py` | 시각 파싱·전달(A′-5) + 브리핑 키 정합(G6) |
> | `noise_gate/application/nodes/alarm_notifier.py` | 브리핑 키 정합(G6 — pull과 **대칭**) |
>
> 아래 원문은 이력으로 보존한다.

### 15.0 기존 코드 통합 분석 (백엔드 배선 — deepagents/Track A/semantic_router)

> 실측(2026-06-26, `src/graph.py`·`orchestration/*`·`routing/*` 코드 분석) 기반 통합 지점.

**핵심 판단 — "deepagents로 의도 분석하고 LangGraph를 새로 구성해야 하는가?"**

1. **의도 분석/위임은 신규 구현 불필요.** 시스템은 이미 의도 처리를 3중 백엔드로 수행한다
   (`src/graph.py:294-476` 빌드 시 1회 백엔드 확정):
   - **Track B(deepagents)**: `enable_deepagents_package` + vLLM 가용 → `deep_agent` 노드. vLLM 오케스트레이터가
     tool-calling으로 위임/재계획(Plan 49).
   - **Track A(의도분해)**: `enable_deepagent_orchestration` → `intent_planner → agent_orchestrator ↔ replanner`.
   - **semantic_router(폴백)**: 그 외. LLM intent 분류 → 고정 노드 라우팅.
2. **진단은 "subagent 1개"로 추가하면 Track A·B에 자동 편입된다.** 두 트랙 모두 **`SUBAGENT_REGISTRY`로
   디스패치**한다(Track A: `agent_orchestrator._run_agent` `subagents.py` 레지스트리 lookup; Track B:
   `deepagents_tools.build_tools`가 `for ... in SUBAGENT_REGISTRY.items()`로 **자동 순회·@tool 노출**).
   → **레지스트리에 `fault_diagnosis` 핸들러 1개 등록이면 두 트랙은 코드 추가 없이 동작.**
3. **진단 서브그래프 자체는 deepagents/tool-calling을 쓰지 않는다.** `diagnosis_graph`는 **고정 LangGraph
   파이프라인**으로 subagent handler 내부(FabriX 워커)에서 실행된다. deepagents는 "바깥 의도분석·위임"만
   담당하고, 진단 내부는 결정적 서브그래프(Plan 49 원칙: 도구 내부는 tool-calling 미강요).
4. **새로 만드는 LangGraph는 `diagnosis_graph` 하나뿐**(alarm_graph와 동형). 메인 `src/graph.py` 본체는
   **semantic_router 폴백 경로에만** `fault_diagnosis` 노드를 추가하면 된다(Track A/B는 handler가 서브그래프 호출).

**백엔드별 추가 작업 매트릭스**

| 백엔드 | 디스패치(자동) | 추가로 손볼 곳 |
|--------|---------------|---------------|
| Track A | `agent_orchestrator`→`SUBAGENT_REGISTRY`(자동) | `src/prompts/intent_planner.py` — agent 목록(L22-26)·분류 우선순위(L32-35)·예시에 `fault_diagnosis` 추가 |
| Track B | `deepagents_tools.build_tools`→레지스트리 순회(자동) | `deepagents_tools.py:30` `_TOOL_NAMES`에 도구명 + `src/prompts/orchestrator.py:17-22` "사용 가능한 도구" 목록 추가 |
| semantic_router | 수동 라우팅 | `src/prompts/semantic_router.py`(intent 추가) + `routing/semantic_router.py`(intent 반환 분기) + `src/graph.py:117 _INTENT_ROUTE_MAP`·`:480 조건부엣지 맵`·`fault_diagnosis` 노드 등록·import |

**공통**: `src/orchestration/subagents.py`에 `run_fault_diagnosis(task, isolated, *, llm, app_config)` +
`SUBAGENT_REGISTRY["fault_diagnosis"]` 등록(핸들러 시그니처·`_make_isolated_input` 재사용 — Known Mistakes 준수).
push 경로는 §8.2(alarm_graph 훅). 모든 변경 후 `arch_check --ci` 통과 확인.

### 신규 파일
- `src/diagnosis/domain/models.py`, `correlation.py`, `metric_anomaly.py`
- `src/diagnosis/infrastructure/polestar_metric.py`, `polestar_topology.py`, `polestar_incident_alarms.py`
- `src/diagnosis/application/nodes/{incident_scoper,evidence_collector,correlation_engine,causal_reasoner,diagnosis_reporter}.py`
- `src/diagnosis/orchestration/diagnosis_graph.py`
- `src/diagnosis/prompts/{incident_scoper,causal_reasoner}.py`
- `src/api/routes/diagnosis.py` (선택)
- `tests/test_diagnosis/...`

### 수정 파일 (최소·게이트)
- `src/config.py` — `DiagnosisConfig` 추가
- `src/routing/semantic_router.py`, `src/routing/domain_config.py` — `fault_diagnosis` 의도
- `src/orchestration/subagents.py` — `run_fault_diagnosis` + `SUBAGENT_REGISTRY` 항목
- `src/prompts/intent_planner.py` — 진단 의도 분해 인식
- `src/alarm/orchestration/alarm_graph.py` — push 진단 훅(opt-in)
- `.env.example` — `DIAGNOSIS_*`
- `docs/02_decision.md` — D-038 등재
- (선택) `src/api/server.py` — diagnosis 라우터 등록

### 변경하지 않는 파일 (재사용·보존)
- 알람 이력 repo·패턴 통계·프로세스 API/선별·마스킹·알림 버스·기존 노드/그래프·NL→SQL 파이프라인·
  result_aggregator·output_generator (그대로 호출만).

---

## 16. 사용자 확인 필요 항목 (착수 전 결정)

> **v2 개정(2026-09-01)** — 아래 5건 중 **1·2·3은 답이 나왔다**: ①pull 우선 → **둘 다 이미 구현**
> (§8 개정 블록) ②고정 템플릿 → **D-122로 확정·구현** ③다중서버 연쇄 → **C′ 유지**(§0.6).
> ④변경 이벤트 가용성 → **`polestar_change_history` 도구로 확인됨**(PostgreSQL 전용 — gp/yd).
> ⑤`_h` 보존 기간은 **여전히 미확인**이며 §6.2 이상탐지 정밀도에 직결한다.
>
> **v2가 새로 요구하는 사용자 확인 3건**:
>
> | # | 쟁점 | 권고 |
> |---|---|---|
> | **U-A** | 착수 순서 — §0.6 A′-0(브리핑 계약)을 먼저 할 것인가, A′-1(시각 인자)을 먼저 할 것인가 | **A′-0 먼저**. 소규모(3파일)이고, 이것 없이는 이후 산출물이 사용자에게 도달했는지 검증할 수 없다 |
> | **U-E** *(v2.1 신설)* | 소유권 확정(§0.4-b)을 `docs/02_decision.md`에 **지금 등재**할 것인가, 착수 시점에 할 것인가 | **착수 시점**. 계획서 예약은 효력이 없으나(§14) 코드 착수 전 등재는 결정을 먼저 굳혀 실측 여지를 줄인다. 다만 사용자가 지금 확정을 원하면 D-197로 등재 가능 |
> | **U-B** *(v2.1 개정)* | 사전수집의 도구 호출 수단 — holmes `ToolExecutor`(내부 API·연결 재사용) vs `mcp` 클라이언트 직접(계약 수준·안정) | **A′는 `ToolExecutor`로 착수**(신규 의존 0·즉시 가능), **B′에서 `mcp` 직접으로 교체**. 처음부터 후자면 A′가 커진다 |
> | **U-C** | `_h` 보존 기간 확인 경로 — 운영 DB 실조회가 필요(과금 아님, DB 접속 필요) | 확인 전까지 §6.2는 **정밀도를 `notes`에 결정적으로 기록**하고 confidence 상한을 건다 |
> | **U-D** *(v2.1 신설)* | `sre_agent`를 `overfit_check` 스캔 대상에 편입할 것인가 — 상관 모듈이 그 패키지에 들어가며 벤더 리터럴 검열 공백이 생긴다 | **편입 권고**. 단 `severity_signatures`의 기존 OS/폴스타 어휘가 기준선에 대량 유입되므로, 편입 시 **기준선은 자기 델타만 소거**(전면 재생성 금지) |

계획 자체는 추가적·읽기전용이라 기존 결정과 충돌하지 않으나, 다음은 범위/우선순위에 영향이 커 확인을 권장한다.

1. **우선 트리거**: pull(사용자 요청형) 먼저 vs push(알람 자동) 먼저? — 본 계획은 **pull(Phase A) 우선**을 권장
   (검증 용이·회귀 위험 최소). push는 Phase B.
2. **증거 SQL 방식**: 고정 템플릿(권장) 확정 여부 — NL→SQL 보조 경로 허용 범위.
3. **다중서버 연쇄 RCA 필요 시점**: Phase C로 분리(권장) vs 초기 포함.
4. **변경/구성 이벤트 증거**: 폴스타/연관 DB에 배포·구성변경 이력 테이블이 있는지(있다면 인과력 큼) —
   데이터 가용성 선조사 필요.
5. **메트릭 시간 정밀도(`_h`) 보존 기간**: 폴스타 환경에서 시간단위 통계 보존 기간 확인(이상탐지 정밀도 직결).

---

## 17. 참고

- 상위/연관 계획: Plan 44, 46, 47, 47-1, 48, 49 (`plans/`)
- 의사결정: `docs/02_decision.md` D-029~D-037 (→ 본 계획으로 D-038 등재)
- 데이터 모델: `config/db_profiles/polestar_cm_gp.yaml`(메트릭/EAV/토폴로지 query_guide), `polestar_cm_yd.yaml`
- 알람 자산: `src/alarm/`, `alarm_server/`
- 처리 흐름: `docs/07_processing_flow.md`, 아키텍처: `docs/05_system_architecture.md`
- Clean Architecture 검사: `python scripts/arch_check.py --ci`

---

## 18. 변경 이력

| 날짜 | 버전 | 내용 |
|---|---|---|
| 2026-06-26 | v1 | 최초 작성. `src/diagnosis/` 진단 서브그래프 설계(§5), 증거 4종·상관 엔진·인과 추론·트리거 이중화. D-038 등재 예정(**예약 미등재 → 소진**). |
| 2026-08-31 | v1.1 | `plans/85` §7.3 X6 — 헤더 상태를 `계획 (미구현)` → **부분 구현 재판정**으로 교체. 잔여를 결정적 상관 축 5건으로 축소 표기. |
| **2026-09-01** | **v2** | **사용자 지시 — *"SRE Agent 기능을 보완하기 위해 50번 계획을 차용하거나 수정해야 될 사항"* 확인 후 개정.** **§0 신설**(실측 방법·재구현 금지 목록·**갭 6건**·배치 결정·**절별 처분표**·착수 단계 A′~C′). 핵심 발견 2건: **G1** — 조사 계약·폴스타 도구·PromQL 도구 어디에도 사건 시각 인자가 없어 §3.4 *"now() 금지"* 가 깨져 있고, 이것이 `plans/85` ★ 5건 전부의 **선행 조건**이다. **G6** — 브리핑 생산자↔소비자 키 불일치로 **한계 서술이 사용자에게 도달하지 않으며**(pull·push 대칭 결함) dict/list가 repr로 노출된다. 처분: **§5·§8.3·§10.1·§11·§15 폐기**, §2·§12·§13·§14·§16 개정, §6·§7.2·§9.1 차용(배치만 이동). **§14 채번 정정** — D-038 소진 확인, 착수 시 재채번(실측 최댓값 D-193). |
| **2026-09-02** | **v2.1** | **사용자 질의 — *"이 기능이 별도 폴더가 아닌 sre agent에 포함되어야 되는 기능인지 분석하라"*.** 배치 근거를 재실측해 **v2 §0.4-a 권고를 정정**했다. v2가 대안 (B)를 물린 근거(*"MCP 클라이언트 신규 비용"*)가 사실과 달랐다 — `mcp` 1.25.0이 `sre_agent` venv에 **이미 있고**(holmesgpt 전이) holmes `ToolExecutor.get_tool_by_name().invoke()`로 **신규 의존 0**에 결정적 호출이 가능하다. 또 v2는 **(D) `sre_agent` 사전수집**을 선택지에 넣지 않았다. 재판정: **G4 상관 계산 = `sre_agent/domain/correlation.py`** · **G4-b 사전수집 = `sre_agent/application/evidence_prefetch.py`**(mcp_server → sre_agent 이동). 결정적 근거는 **원시 도구 출력이 `sre_agent`를 벗어나지 않는다**는 실측(poll은 briefing + description만) — 별도 최상위 폴더(`rca/`)는 증거를 볼 수 없어 재수집이 강제되고 D-118 ④(폴더 복사 분리)도 깨진다. `mcp_server`에는 **구간 앵커 SQL(G1~G3)만** 남는다(읽기 경계). R-12 소멸 · R-15·R-16 신설 · §10 플래그 소속 이동 · §16 U-B 개정·U-D 신설. |
| **2026-09-02** | **v2.1 (2차)** | 사용자 지시 *"검토한 내용을 기반으로 계획을 수정하라"* — 대화에서만 있던 검토 결과를 계획서에 편입하고 v2 잔재를 정합화했다. **신설**: **§0.0**(목표 재정의 — 원안 3층 + *"조사에 시간 좌표계와 결정적 상관 계산을 부여해 「무엇이 먼저 일어났는가」를 서술이 아니라 계산으로 답하게 만든다"* 라는 잔여 1문장) · **§0.4-b**(소유권 분석 — **D-139는 신규 `rca/`가 아니라 `sre_agent` 편입을 가리킨다**, §5 5노드 ↔ `sre_agent` 파이프라인 대조표, *"2026-06-26엔 sre_agent가 없었다"*, 읽기/조사/표현 **3층 경계도**) · §16 **U-E**. **정합화**: §5 개정 블록의 `mcp_server` 잔재 제거(→ *"파이프라인은 사라지지 않고 소재지만 바뀐다"*) · §13 표의 blockquote 이탈 복구 + **R-14 재정의**(스캔 대상 이탈로 위험의 성격이 "기준선 오염"→"검열 없는 축적"으로 바뀜) · 헤더 v2.1 요약 · §0.5 §5 행을 *"폐기(래퍼만)"* 로 정정 · §0.6에 3층 소재지 명시. |
| **2026-09-02** | **v2.1 (3차)** | 사용자 질의 *"mcp_server와 sre_agent 각각의 역할이 뭐냐"* → *"정리한 내용과 구성도를 계획서에 포함시켜줘"*. **§0.2-a 패키지 역할 지도 신설** — 역할 대비표(포트 9099/9098 · LLM 유무 · **소비자 수의 비대칭**) · `mcp_server` 도구 19종 구성(4+8+7 실측) · `sre_agent` 계층 표(domain 2 · application 3 · interface 1) · **런타임 호출 사슬 다이어그램** · **반환의 비대칭**(rows는 `sre_agent`를 벗어나지 않음 → §0.4-a 실측 ①과 연결) · 분리 근거 3건. §0.4-b의 **책임 3층 그림**과 축이 다름을 명시해 중복 기술을 방지했다(`plans/85` §1 교훈). **부기**: CLAUDE.md 「저장소 지도」가 `mcp_server`를 *"별도 venv"* 로 적었으나 **`mcp_server/.venv`는 부재**(루트 venv 사용) — 같은 문서 「개발 명령」은 맞게 적혀 있다. 본 계획은 실측을 따르며 CLAUDE.md 정정은 범위 밖. |
| **2026-09-02** | **v2.2** | 사용자 지시 *"50번 계획의 잔여 계획을 구현하라"* — **G1~G6 전부 구현·검증 완료(D-197 등재)**. SDD 절차(`CAPABILITY-MAP-50.md` 6모듈 → 모듈별 `SPEC-*.md` → `tasks/plan-50.md`·`todo-50.md` → TDD)로 진행. ★실측이 설계를 바꾼 3건: ①**G6 결함 재현** — 두 소비자가 생산자 미산출 키(`evidence`·`limitation`)를 기다려 `[한계]`·`[가설]`·`[중요도]` 미도달 + repr 누출, 기존 테스트가 그 키를 정답으로 굳힘 → 정본=생산자 · 공용 렌더러(`noise_gate/domain/investigation_briefing.py`) · 모르는 키 침묵 누락 금지 ②**`stat_date`는 varchar** → 앵커는 granularity 포맷 문자열 비교, 경계는 파이썬 계산·리터럴 보간, 미지정 시 SQL 스냅샷 동일 ③**holmes `Tool.invoke`가 `ToolInvokeContext(llm=…)` 필수** → A′-3의 holmes 경유 대신 **B′ `mcp` 클라이언트 선행 채택**(`pyproject` `mcp<2` 명시). 신설: `polestar_incident_alarms`(20종) · `src/domain/incident_time.py`(결정적 시각 파서) · `sre_agent/domain/{incident_scope,correlation}.py` · `application/{investigation_guidance,evidence_prefetch}.py` · `infrastructure/mcp_tool_client.py` · 브리핑 `root_cause_hypotheses`. 플래그 `EVIDENCE_CORRELATION_ENABLED`·`INVESTIGATION_GUIDANCE_EXTRA` 기본 off/None. 검증: 본체 340 · mcp_server 219 · sre_agent 325 · arch/overfit 0 · 실 LLM 0. **§0.3에 해소 표 삽입 · §0.6 A′/B′ ✅ 표기.** 남은 것: 실 조사 e2e(D-127) · Phase C′. |
