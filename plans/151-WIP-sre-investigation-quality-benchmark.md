# 151. sre_agent 장애 조사 품질 벤치마크 — 정답이 있는 합성 장애 세계로 HolmesGPT 조사를 반복 측정하고, 실패 유형에서 개선점을 찾아 A/B로 채택한다

> **작성일**: 2026-10-12 · **v1.0** · **v1.1**(같은 날 — 사용자 인터뷰로 게이트 G-1~G-8 확정 · 측정은 내부망 사내 vLLM 중심 · 외부망 Claude Code 검토 루프 · 로컬 MLX 최소화) · **v1.2**(2026-10-13 — 실행 구성 §4.7~§4.11 · 테스트 방법 §9 상세) · **v1.3**(같은 날 — 내부망 GPU 서버 동거 실행 · LLM 백엔드 vLLM 기본 / FabriX(`fabrix_proxy`) 대체 · §6.7) · **v1.4**(같은 날 — 실행 명령 정본 §9.3)
> **상태**: **WIP — W0~W5 구현 완료(2026-10-13) · W6 사용자 실행 대기**(내부망 GPU 서버 1회차 A/A · 명령 §9.3.7) · W7은 W6 반출 묶음 수신 뒤 · W8 미착수 · 게이트 G-1~G-8 **확정**(2026-10-12 인터뷰 · §10) · D-324 본문 등재 · 결과 요약: W0 실측 §4.7 · W2 §4.10 · W3 §4.5·§4.7 · W5 MLX 3세계 §9.3.5 · 키트 §9.3.6
> **요청(사용자 2026-10-12)**: *"sre_agent를 통해 장애 분석을 하는 기능에 대해 벤치마크 테스트를 통해 개선포인트를 개측하여 개선을 진행하려고 한다. 관련 계회파일을 작성하라."*
> **해석**: 「개측」 = 계측(측정). 이 계획의 대상은 **조사 품질**(원인을 맞히는가 · 근거를 대는가 · 끝까지 가는가 · 얼마나 드는가)이다. 노이즈 게이트 판정 품질(`noise_gate`)과 본체 text2sql 벤치(`scripts/bench`·`scripts/scenario`·`scripts/itam_bench`)는 범위 밖이다.
> **입력**: 저장소 `3c4adab` 읽기·grep 실측(서브에이전트 1갈래 — sre_agent 흐름·설정·도구·평가 자산·관측성) + 대화 중 `file:line` 재대조. 실 LLM 호출 0 · 서버 기동 0 · 패키지 설치 0.
> **관련 계획**: `plans/sre-agent/02`(HolmesGPT 조사 — §12 수용 기준은 구조·안전만) · `plans/91`(장애 조사 잔여 — 1-14 사내 vLLM 완주 판정 대기 · 1-17 MLX 9B 40 step 미완주) · **`plans/150`**(WIP — W5가 sre_agent 질문·지침·사전수집·`cross_source` 블록을 바꾼다 · §8 순서 조율) · **`plans/101`**(ML RCA — 라벨 스키마·브리핑 채점 P-24·누수 금지 KL-3를 이 벤치가 그대로 쓴다) · `plans/93`·`plans/97`(벤치 판정·반출 관례) · `plans/135`(벤치 계약 문서 형식)
> **관련 결정**: D-035(결정적=판단 · LLM=서술) · D-118·D-139(`sre_agent` 독립 패키지 · 양방향 import 0) · D-119(관측 읽기 경계 = `mcp_server`) · **D-120**(Gemini = 개발 전용 · 운영 데이터 외부 송신 금지) · D-123(조사 서비스 submit/poll · 상태 어휘) · **D-127·D-240**(실 LLM = 로컬 MLX · 과금 평면 건별 승인) · D-213(조사 타임아웃 join 타임박스) · D-229·D-230(배선 교정 · 실 조사 LLM 게이트) · **D-238**(처분 어휘 — 대조군 최적 = `기각`) · **D-219**(관측 0 = 판정 불가 · 무효표 고지) · D-254(축은 값을 읽는 코드가 있어야) · D-303(회귀)
> **D-번호**: **D-324 본문 등재**(2026-10-12 · `docs/decisions/D-324.md` · 「sre_agent 조사 품질 벤치마크 계약」)
> **계획 시작 SHA**: `f4a95f52971e0227cfb761a258e1b9d010f97950`(2026-10-13 구현 착수 — 설계 입력 `3c4adab` 뒤에 `plans/150` 커밋 `1686f3b`가 랜딩해 회귀 기준을 착수 시점으로 옮김)
> **회귀 대상 파일(W0~W5 확정 · 2026-10-13)**: `sre_agent/.env.example` `sre_agent/sre_agent/application/investigation_dispatcher.py` `sre_agent/sre_agent/application/investigation_jobs.py` `sre_agent/sre_agent/diagnosis.py` `sre_agent/sre_agent/interface/mcp_service.py` `sre_agent/sre_agent/settings.py` `sre_agent/tests/test_apm_consumer.py` `sre_agent/tests/test_briefing_builder.py` `sre_agent/tests/test_diagnosis_guard.py` `sre_agent/scripts/inv_bench.py` `sre_agent/scripts/investigation_bench/__init__.py` `sre_agent/scripts/investigation_bench/__main__.py` `sre_agent/scripts/investigation_bench/_serve.py` `sre_agent/scripts/investigation_bench/export.py` `sre_agent/scripts/investigation_bench/fake_llm.py` `sre_agent/scripts/investigation_bench/judge.py` `sre_agent/scripts/investigation_bench/lint.py` `sre_agent/scripts/investigation_bench/preflight.py` `sre_agent/scripts/investigation_bench/report.py` `sre_agent/scripts/investigation_bench/runner.py` `sre_agent/scripts/investigation_bench/schema.py` `sre_agent/scripts/investigation_bench/world_server.py` `sre_agent/scripts/investigation_bench/worlds.py` `sre_agent/tests/test_bench_e2e_fake_llm.py` `sre_agent/tests/test_bench_export.py` `sre_agent/tests/test_bench_judge.py` `sre_agent/tests/test_bench_lint.py` `sre_agent/tests/test_bench_preflight.py` `sre_agent/tests/test_bench_runner.py` `sre_agent/tests/test_bench_schema_drift.py` `sre_agent/tests/test_bench_world_server.py` `sre_agent/tests/test_bench_worlds_selfcheck.py` `sre_agent/tests/test_plan151_verify_judge.py` `sre_agent/tests/test_plan151_verify_security.py` `sre_agent/tests/test_plan151_w0_instrumentation_gaps.py` `sre_agent/tests/test_plan151_w1_instrumentation.py` + 데이터 `sre_agent/testdata/investigation_bench/*` · `sre_agent/tests/fixtures/investigation_bench/*` · 문서 `docs/26_sre_agent_guide.md` §10 (계획 예정에 있던 `briefing_builder.py`·`evidence_prefetch.py`·`investigation_guidance.py`는 W0~W5에서 바꾸지 않음 — H-후보는 W7)

---

## 0. 증거 규칙

- 과금 호출 0 · DB 호출 0 · 서버 기동 0. §2 앵커는 `3c4adab`에서 `grep -n`으로 줄 번호를 다시 확인했다.
- 「(추론)」은 코드 읽기로 판단했으나 실행으로 확인하지 않은 항목이다. W0에서 테스트·실측으로 먼저 확정한다.
- 이 개발 맥에는 `sre_agent/.venv`가 **없다**(실측 — holmesgpt 미설치). 설치(holmesgpt 0.36.0 · D-120)는 W0 첫 작업이다.
- 외부망 실 LLM은 로컬 MLX **대표 3세계 × 1회**뿐이다(하네스가 실 LLM으로 끝까지 도는지 확인용 · G-7). 기준선·A/B 측정은 내부망 **GPU 서버**(sre_agent·벤치를 vLLM과 같은 서버에 올림)에서 사용자가 돌리고, LLM 백엔드는 사내 vLLM이 기본 · 필요 시 FabriX(`plans/148` `fabrix_proxy` 경유)로 회차를 돌린다(G-4 · v1.3 · §6.7). 과금 평면(Gemini·anthropic)은 하네스가 막고, 쓰려면 건마다 사용자 승인을 받는다(§6.4).

## 1. 결론

### 1.1 한 줄

지금은 **조사 품질을 잴 수 없다**. 원인 정답이 붙은 조사 데이터가 저장소에 0건이고, 미완주 조사가 `done`으로 기록되며, 지연·step·도구 호출 수가 산출물에 남지 않는다. 이 계획은 ① 계측 빈칸을 메우고(W1) ② **정답을 구성으로 아는 합성 장애 세계**(가짜 MCP 서버가 시나리오별 도구 응답을 준다)를 만들어 실제 조사 경로(`sre_investigate_alarm` → 사전수집 → HolmesGPT → 판정 → 브리핑)를 반복 측정하는 하네스를 만든다(W2~W4). 측정은 **내부망 사내 vLLM에서 회차 단위로** 돈다 — ③ 1회차가 기준선(A/A)을 재서 반출하면(W6) ④ **외부망 Claude Code가 반출 로그를 검토**해 채점 교정안 · 실패 원인 진단과 개선 후보 · 개선 코드(플래그 뒤)를 낸다(W7) ⑤ 다음 회차가 대조군 vs 실험군을 홀드아웃에서 재고, 이긴 것만 채택한다(W8 — W7↔W8 반복). 세계가 합성이라 반출 묶음에 운영 데이터가 없다 — 브리핑 전문을 그대로 반출하고 내부 주소만 가린다. 로컬 MLX는 하네스가 끝까지 도는지 보는 대표 3세계 × 1회로 줄인다(W5).

### 1.2 무엇을 재는가 (P-24 · `plans/101` §3)

| 축 | 지표 | 판정 방식(결정적) |
|---|---|---|
| **원인 적중** (주 지표) | RC@1 · RC@3 — 가설 순위 1·3위 안에 정답 `root_entity`와 `kind`가 함께 있는가 | 세계 YAML의 라벨 + 허용 표면형(별칭) 목록 대조 |
| **증거 폐포** | ECR — 정답 원인을 닫는 핵심 증거 중 브리핑이 인용한 비율 | 핵심 증거 항목(도구 + 값/문구) 대조 · 세계 서버가 실제로 내준 출력에 있는 것만 인정 |
| **환각** | HALLU — 브리핑의 수치 주장 중 어떤 도구 출력에도 없는 비율 | 수치·단위 토큰 추출 → 세계 서버 응답 로그 대조(반올림 허용) |
| **유인 저항** | DIST — 유인 증거(정상 패턴의 높은 값 등)를 1위 원인으로 꼽은 비율 | 세계 YAML `distractors` 대조 |
| **기권 정확도** | ABST — 무장애·근거 결손 세계에서 단정하지 않는가 / 근거 충분 세계에서 「불충분」으로 빠지지 않는가 | `evidence_insufficient`·가설 confidence |
| **완주** | 완주율 · 미완주(step 상한) · 타임아웃 | W1 계측 키 |
| **비용** | 지연 p50/p90 · 도구 호출 수 · step 수 · 토큰 | W1 계측 키 + 세계 서버 호출 로그(도구 호출 수의 정본) |
| **일관성** | Maj@k — 같은 세계 k회 반복에서 1위 원인이 일치하는 비율 | 반복 실행 간 대조 |

LLM 심판(채점을 LLM에 맡기는 것)은 쓰지 않는다(D-035 · 비결정성 — G-5). 결정적 채점이 놓친 것(별칭 누락 등)은 반출 로그를 외부망 Claude Code가 검토해 채점 교정안으로 돌려주고 `--rejudge`로 재채점한다(§6.6).

### 1.3 범위 밖

- 실호스트 셸 조사(L3 · `vm_profile` · D-189) — 합성 세계에는 셸이 없다. 벤치는 `remote_vm_profile`(MCP 도구만)로 돈다. L3 품질은 후속.
- 운영 사건 리플레이 평가(사후 기입 원인 텍스트로 라벨링) — `plans/101` F0(리플레이 평가셋 빌더) 소유. 이 계획은 **같은 라벨 스키마·같은 채점기**를 써서 101이 재사용하게만 만든다.
- 샌드박스 PG에 장애 픽스처를 적재해 실 `mcp_server`까지 도는 종단 벤치 — G-1 (a) 확정으로 제외(필요해지면 후속).
- FabriX와 vLLM 결과의 **교차 비교**(같은 회차에서 두 백엔드를 실험군으로 나란히) — FabriX 점수에는 모델 차이와 프록시 도구 호출 에뮬레이션 품질이 섞여 원인을 가를 수 없다. FabriX는 **대체 백엔드**로만 쓴다(§6.7 · v1.3). 프록시 자체의 정확도·지연은 `plans/148` 소관.
- `noise_gate` 조사 트리거 조건·티어 판정 변경.

---

## 2. 현행 실측

### 2.1 조사 경로

| 지점 | 실측 | 근거 |
|---|---|---|
| 진입 | FastMCP SSE 127.0.0.1:9098 · 도구 5종(`sre_investigate_alarm` push · `sre_diagnose` pull · `sre_get_investigation` · `sre_list_investigations` · `sre_health`) | `run_service.py` · `interface/mcp_service.py` |
| 질문 | **고정 템플릿 1종** — 서버명 + 발생 시각 + 「부하→병목→원인 격리→로그 · 간결히 · 즉시 결론 · 도구 출력 인용」. 알람 종류(kind)와 무관 | `interface/mcp_service.py:85` `_job_to_question` |
| 사전수집 | `evidence_correlation_enabled`(기본 **off**)일 때만 주입. 켜지면 `polestar_incident_alarms` 1회 + `polestar_metric_trend` 4축(cpu·memory·filesystem·disk_io) 고정 · z>3.0 결정적 상관 · LLM 0 | `interface/mcp_service.py:164,171` · `application/evidence_prefetch.py:27` · `settings.py:166` |
| 지침 | 원격 셸 안내 → 사건 구간 → 상관 결과 → kind별 플레이북 → OpenMetrics → APM → 운영자 추가 지침 | `application/investigation_guidance.py:366` |
| HolmesGPT | `Config(model, api_key, api_base, max_steps, toolsets, mcp_servers)` → `llm.call` | `diagnosis.py:169,213` |
| 브리핑 | `severity` · `summary` · `timeline` · `bottleneck` · `cause` · `root_cause_hypotheses[{rank,cause,confidence,evidence,reasoning}]` · `recommendation` · `limitations` · `citations_verified` | `application/briefing_builder.py` |
| 인용 검증 | **마커 문자열**(`←`·`출처`·`근거` 등) 또는 도구명이 줄에 있으면 인용으로 본다 — 인용한 값이 실제 도구 출력에 있는지는 보지 않는다 | `application/briefing_builder.py:30,143` |

### 2.2 조정 가능한 설정(벤치 축 후보 · D-254 — 전부 소비 코드 있음)

| 필드 | 기본값 | 근거 |
|---|---|---|
| `model` · `api_base` · `api_key` | `anthropic/claude-sonnet-5` · None · None | `settings.py:20` |
| `max_steps` | 40 (주석: 10 step은 다중 메트릭 미완주 · 브로드 트리아지는 30도 초과) | `settings.py:26` |
| `investigation_timeout_seconds` | 300 | `settings.py:109` |
| `severity_judge_enabled` · `remediation_recommender_enabled` | False · False | `settings.py:136,141` |
| `openmetrics_guidance_enabled` · `apm_guidance_enabled` · `apm_signatures_enabled` | False · False · False | `settings.py:151,157,162` |
| `evidence_correlation_enabled` · `evidence_change_overlay_enabled` | False · False | `settings.py:166,169` |
| `polestar_mcp_url` · `apm_mcp_url` | `http://localhost:9099/sse` · `""` | `settings.py:34,39` |

**품질을 바꿀 수 있는 기능 대부분이 기본 off다.** 켰을 때 조사가 나아지는지 잰 기록은 없다.

### 2.3 계측 빈칸 — 이대로는 벤치가 성립하지 않는다

| # | 빈칸 | 근거 |
|---|---|---|
| M-1 | **미완주가 `done`으로 기록된다** — `DiagnosisResult.incomplete`를 application·interface 계층이 읽지 않는다(grep 0건) | `diagnosis.py:94,223` · `application/investigation_dispatcher.py:452` |
| M-2 | 미완주 결과는 answer 문구만 남고 토큰·도구 기록이 0이다(holmes 예외 경로) | `diagnosis.py:212-225` |
| M-3 | 타임아웃 잡은 토큰·도구 0으로 덮인다 | `application/investigation_dispatcher.py:407-417` |
| M-4 | `done` 감사 레코드에 지연·step 수·도구 호출 수·모델명이 없다(level·confidence·escalate·signals·tokens·cost만) | `application/investigation_dispatcher.py:459-472` |
| M-5 | 원시 도구 기록(`ToolCallRecord` — 인자·상태·출력)은 판정·정체 가드에만 쓰이고 잡에 남지 않는다. 잡에는 description 목록뿐 | `diagnosis.py:21-75` · `application/investigation_dispatcher.py:455` |

**(W0 실측 2026-10-13 · holmesgpt 0.36.0 · 앵커는 HEAD f4a95f5 기준으로 다시 뽑음)** M-1~M-5는 `sre_agent/tests/test_plan151_w0_instrumentation_gaps.py`에 `xfail(strict=True)` 8건으로 고정했다(키 이름 = §7 W1 행 · 결과 속성 `DiagnosisResult.steps` 신설 전제).

- **step 수·토큰은 얻을 수 있다**: `LLMResult`(`holmes/core/tool_calling_llm.py:179-187`)에 `num_llm_calls`(LLM 호출 수 = step)와 `RequestStats` 상속 필드 `total_tokens`·`prompt_tokens`·`completion_tokens`·`total_cost`가 있다. 덤프 실측: 도구 1턴 + 결론 1턴 → `num_llm_calls=2`, usage 120×2 → `total_tokens=240`.
- **M-2 회수는 가능하다(H-5 실현 가능)**: `ToolCallingLLM.call`(575-694)은 `call_stream`을 끝까지 소비하는 래퍼라, step 상한 `Exception`(1460)이 오르면 `call`의 지역 변수 `all_tool_calls`(588)·`accumulated_stats`(591)가 버려진다. 넘긴 `messages`는 복사돼(1091) 변이도 없다. 대신 `call_stream`을 직접 소비하면 턴마다 `TOOL_RESULT`(도구 1건 `to_client_dict` — 이름·status·params·출력)와 `TOKEN_COUNT`(`metadata["costs"]` = 누적 RequestStats — 1028)가 나오므로 예외 직전까지 전부 회수된다. 덤프 실측: max_steps=3 loop에서 예외 전에 도구 결과 3건·누적 360 토큰을 받았다. step 수는 예외 문구 `i/max_steps`에도 있다.
- **M-1 보충 — 예외 없는 step 소진**: 마지막 턴(`i == max_steps`)은 holmes가 `tools`를 빼고 부른다(1108-1109). 모델이 그 턴에 텍스트로 답하면 예외 없이 `ANSWER_END`로 끝나 현 코드는 `incomplete=False`다. 상한 예외는 모델이 도구 없는 턴에도 `tool_calls`를 낼 때만 난다(덤프 실측). 「상한까지 써서 강제 결론」을 미완주로 볼지(`num_llm_calls == max_steps`)는 W1이 정한다 — W0 테스트는 예외 경로만 고정했다.
- **M-3은 「가능 범위」로 좁힌다**: 타임아웃은 dispatcher가 조사 스레드를 버리는 join 타임박스(`investigation_dispatcher.py:549-550`)라 diagnose_fn이 반환하지 않는다. 부분 토큰을 얻으려면 조사 진행을 잡 쪽으로 흘리는 통로가 따로 필요하다(W1 설계). W0 테스트는 `elapsed_s`·`model`·`max_steps`만 고정했다.
- **holmes 중복 호출 거부**: 같은 도구·같은 인자를 다시 부르면 holmes가 실행하지 않고 오류 결과("Refusing to run this tool call … exact same parameters" — `holmes/core/safeguards.py:24`)를 만든다. 세계 서버에는 호출이 가지 않는다. 그래서 도구 기록 수 ≠ 세계 서버 호출 수일 수 있다(F-LOOP 판정 입력).
- **사전수집 결과가 지침에 들어가는 범위(H-3 전제)**: 지침에는 `correlation_note`가 만든 한 단락만 들어간다(`application/investigation_guidance.py:105-131` · 조립 498-500). 담기는 것은 선행 신호, **이상(`is_anomalous`)인 지표만**의 kind·onset·peak 값·시각·z·알람 대비 시차, 구간 알람 건수·첫 알람·해소 수, 타임라인 앞 12줄, 한계 notes다. 빠지는 것은 넷이다. ① 정상 지표는 언급이 없다 — 「cpu는 봤고 정상」이 전달되지 않는다. ② 원 시계열이나 요약은 없다. ③ `change_finding`은 브리핑만 읽는다(`briefing_builder.py:110`). ④ **사전수집이 이미 부른 도구·인자 목록**이 없다. 사전수집 호출은 알람 1 + 지표 4종(cpu·memory·filesystem·disk_io) + 변경(옵션) + 연관 서버 알람이다(`evidence_prefetch.py:131-151`). 그래서 LLM이 같은 조회를 다시 할 여지가 있다 — H-3 전제가 성립한다.

### 2.4 평가 자산

| 자산 | 재는 것 | 한계 |
|---|---|---|
| `sre_agent/scripts/ab_promql_gate.py` | mock_exporter 고정값 4개를 답변에서 문자열 대조 · 완주·도구 수·지연·토큰 → `eval_results/d119_ab_gate.json` | **유일한 결정적 채점기**지만 세계 1개·사실 4개·원인 판정 없음. 채점 방식(`EXPECTED_FACTS` · `_score`)은 출발점으로 재사용 |
| `sre_agent/tests/test_investigation_e2e.py` | 완주 또는 graceful 미완주 · `max_steps=2` 강제 미완주 · 상관 on 브리핑 구조 | 값이 아니라 구조만 단언 |
| `sre_agent/tests/mvp_record.py` | `logs/mvp_test/runs.jsonl` — 모델·api_base·max_steps 지문 + 완주·도구 수·토큰 | 원인 정답 없음 |
| `noise_gate/scripts/mock_polestar_events.py` | 시나리오 14종(`sev3-page`·`cascade`·`change-corr`·`net-site-cascade` 등) | 기대값이 **게이트 티어**지 원인이 아니다 → 알람 이벤트 모양만 빌려 쓴다 |
| `testdata/pg/init/05_insert_dummy_data.sql` | `cmm_metric_stat_h` 더미(`stat_date_1` 문자열 등) | 시계열이 아니라 장애 재현에 못 쓴다 → G-1 (b)를 고르면 픽스처를 새로 써야 한다 |

**원인 정답 데이터셋: 0건**(`root_cause` grep은 브리핑 코드·계약 테스트만). `plans/101` §2도 라벨 부재를 명시한다.

### 2.5 이미 알려진 품질 신호

- MLX 9B로 실 조사 1회: 40 step 상한 미완주 · 173초 · 구조 단언만 통과(`plans/91` 1-17).
- `max_steps` 주석: 포커스 질의는 20에서 완주, 브로드 트리아지는 30도 초과(`settings.py:23-25`) — 질문이 브로드하면 step을 다 쓴다는 실측이 이미 있다.
- 운영 등급 모델(사내 vLLM·FabriX)의 완주는 미확인(`plans/91` 1-14 대기).

---

## 3. 설계 원칙

1. **정답은 구성으로 안다.** 세계를 만든 쪽이 원인을 정하므로 라벨이 사후 판단이 아니다. 누수 금지(KL-3): 정답 원인을 말하는 문구는 알람 이벤트·질문·지침 어디에도 넣지 않는다(린트로 강제).
2. **실제 조사 경로를 돈다.** 벤치는 `sre_investigate_alarm` submit/poll로 들어가 사전수집·지침·판정·브리핑을 모두 거친다. HolmesGPT를 직접 부르지 않는다.
3. **도구 응답만 가짜다.** 가짜 세계 서버는 실 `mcp_server`와 **같은 도구 이름·인자 스키마**를 노출한다. 스키마는 실 서버의 `list_tools` 스냅샷이 정본이고, 어긋나면 하네스가 멈춘다(§4.3).
4. **채점은 결정적이다.** 같은 브리핑이면 같은 점수다. 브리핑 → 점수 재계산(`--rejudge`)을 지원한다.
5. **개발셋과 홀드아웃을 나눈다.** 개선은 개발셋으로 만들고 채택은 홀드아웃으로 판정한다. 홀드아웃 세계 파일은 구현 위임 프롬프트에 넣지 않고, 세계 고유 토큰(호스트명·프로세스명·특정 수치)이 `sre_agent/sre_agent/` 소스에 들어오면 린트가 실패한다(벤치 과적합 방지).
6. **개선 하나 = 플래그 하나 = 실험군 하나.** 신규 동작은 기본 off(현행 비트 동일)로 넣고, 홀드아웃에서 이겼을 때만 기본값 변경을 결정한다(D-238 처분 어휘).
7. **판정 못 하는 것은 판정 불가로 적는다.** 완주 0건·도구 호출 0건인 실험군은 점수를 내지 않는다(D-219 ③ 준용).

---

## 4. 설계

### 4.1 구성

```
sre_agent/scripts/inv_bench.py             (신규 · 실행 진입 — sre_agent/scripts에 __init__.py가 없어 경로를 올리고 패키지를 부른다)
sre_agent/scripts/investigation_bench/     (신규 · sre_agent venv)
  __main__.py      CLI: --schema-snapshot · --lint · --show-env · --preflight · --run · --resume · --status · --cleanup · --report · --rejudge · --export (정본 §9.3.0)
  _serve.py        벤치 전용 sre_agent 진입(포트 인자 · 실효 설정 에코 — §4.7)
  fake_llm.py      OpenAI 호환 가짜 LLM — 세계 대본 재생(테스트 전용 · §4.11)
  preflight.py     과금 평면 차단 · venv·holmes 버전 · LLM 엔드포인트(MLX/사내 vLLM)·tool-calling 왕복 · 스키마 드리프트 · 포트
  export.py        반출 묶음(§6.5) — 내부 주소·계정·경로 가림 + 잔존 검사(관문 실패 = 묶음 미생성)
  world_server.py  가짜 세계 MCP 서버(FastMCP SSE · 127.0.0.1) — polestar_*·prom_*·om_* / apm_*
  runner.py        sre_agent 기동(벤치 전용 포트·env) → 세계별 k회 submit/poll → raw.jsonl
  judge.py         결정적 채점(§4.4) + 실패 유형 분류(§4.5)
  report.py        실험군 비교표 · 실패 유형 분포 · 처분(§6.2)
  lint.py          세계 YAML 검사 · 누수 검사 · 홀드아웃 토큰 유입 검사
sre_agent/testdata/investigation_bench/
  worlds/dev/*.yaml · worlds/holdout/*.yaml
  tool_schemas.json   실 mcp_server(+apm_gateway) list_tools 스냅샷
  specs/*.yaml        실행 명세(§4.7)
sre_agent/tests/fixtures/investigation_bench/   채점기·린트·반출 테스트용 소형 세계·브리핑·묶음 픽스처
산출: sre_agent/.data/investigation_bench/<run_id>/  (gitignore 확인됨 — .gitignore:50)
  manifest.json · world_calls.jsonl · raw.jsonl · scores.jsonl · report.md · arm-<name>/  (계약은 §4.9)
```

**세계 서버 라우팅**: 세계마다 고유 합성 호스트명(`bw-d01-app01` 형태)을 쓰고, 서버 하나가 전 세계를 싣는다. **현재 세계는 러너가 제어 엔드포인트로 지정**한다(호스트 인자가 없는 도구 — PromQL·사이트 단위 조회 — 도 세계를 알아야 하므로 인자로 고르지 않는다 · §4.10). sre_agent는 MCP 서버를 프로세스당 1회 등록하고 도구 호출마다 새 SSE 세션을 연다(W0 실측 · §4.7) — 세계 전환에 재기동이 필요 없다. 현재 세계에 없는 호스트명으로 부르면 「대상 없음」을 돌려주고 인자 오류로 센다(F-ARG).

**실행 단위**: 세계 × 실험군 × 반복 k. 기본은 직렬(지연 측정 오염 방지) · `investigation_max_concurrent=1` · 반복마다 `decision.fingerprint`를 바꿔 dedup·예산 가드를 피한다(`investigation_hourly_budget` 미설정 확인).

### 4.2 세계 YAML 형식 (초안)

```yaml
id: D03
split: dev
title: 로그 미회전으로 파일시스템 포화         # 사람용 — 조사에 주입되지 않음
event:                                          # sre_investigate_alarm payload.event
  serverName: bw-d03-app01
  hostname: bw-d03-app01
  severity: 3
  alarmName: "파일시스템 사용률 임계 초과"       # 증상만 — 원인 문구 금지(린트)
  alarmTime: "20261012031500"
label:                                          # plans/101 라벨 스키마와 같은 키
  root_entity: {host: bw-d03-app01, component: "/var/log"}
  layer: os            # os | app | db | network | change
  kind: disk_full
  change_related: false
  aliases:             # 원인 적중 판정 허용 표면형
    kind: [디스크 포화, 파일시스템 가득, filesystem full, 로그 미회전, logrotate]
key_evidence:                                   # ECR 분모
  - {tool: polestar_metric_trend, match: {metric: filesystem}, cite: ["98", "/var/log"]}
  - {tool: polestar_incident_alarms, cite: ["logrotate"]}
distractors:                                    # DIST 판정
  - {kind: cpu_high, aliases: [CPU 과부하, CPU 사용률]}   # 정시 배치의 정상 CPU 패턴
expect:
  abstain: false
  severity: {level: high, escalate: true}       # severity_judge on 실험군에서만 채점
tools:                                          # 세계 서버 응답 — 키 = 도구명 + 인자 매처
  polestar_metric_trend:
    - match: {metric: filesystem}
      reply: {series: [...], latest: 98.4, unit: "%"}
    - match: {metric: cpu}
      reply: {series: [...], note: "매시 정각 60%대 — 지난 14일 동일 패턴"}
  polestar_incident_alarms:
    - reply: {alarms: [...]}
  default: {error: null, rows: []}              # 정의 안 된 호출 = 빈 결과(오류 아님)
hosts: [bw-d03-app01]                           # 이 세계의 호스트 집합(호스트 인자 검사 · §4.10)
incident_window: {start: -45m, end: 0m}         # 알람 시각 기준 사건 구간(시간창 인자 검사 · 선택)
script:                                         # 가짜 LLM 대본 — 테스트 전용(§4.11) · 실측에는 쓰이지 않음
  model:                                        # 모범 답안: 채점기가 PASS를 내야 한다
    - {call: polestar_metric_trend, args: {hostname: bw-d03-app01, metric: filesystem}}
    - {call: polestar_incident_alarms, args: {hostname: bw-d03-app01}}
    - {answer: "/var/log 파일시스템 98.4% — logrotate 실패 알람 이후 증가 …"}
  neg:                                          # 일부러 틀리는 대본과 기대 실패 코드
    F-DIST: [{call: polestar_metric_trend, args: {hostname: bw-d03-app01, metric: cpu}}, {answer: "CPU 과부하가 원인 …"}]
    F-HALLU: [ … , {answer: "/var/log 99.7% …"}]   # 세계가 내주지 않은 수치
```

- 수치 시계열은 손으로 쓰지 않고 생성기 지시(`gen: {base: 45, spike_at: -30m, to: 98, noise: 2}`)로 적어 세계 서버가 결정적으로(시드 고정) 펼친다.
- 응답 모양은 실 `mcp_server` 도구 반환 모양을 따른다 — W2에서 도구별 반환 예시를 실 서버 테스트 픽스처에서 떠 와 검증한다.

### 4.3 스키마 드리프트 가드

- `--schema-snapshot`: 로컬에서 실 `mcp_server`(+`apm_gateway`)를 띄운 상태로 MCP `list_tools`를 받아 `tool_schemas.json`에 쓴다. **import가 아니라 MCP 프로토콜로 받는다**(D-139 양방향 import 0 유지).
- 세계 서버는 이 파일에서 도구 목록·인자 스키마를 읽어 등록한다. 세계 YAML이 스냅샷에 없는 도구·인자를 쓰면 린트 실패.
- `--preflight`는 스냅샷 해시를 manifest에 남긴다. 실 서버가 떠 있으면 다시 받아 대조하고, 다르면 멈춘다.

### 4.4 채점 (`judge.py`)

| 지표 | 계산 | 비고 |
|---|---|---|
| RC@1 / RC@3 | `root_cause_hypotheses` 순위 + `cause`에서 정답 `kind` 별칭 **그리고** `root_entity`(호스트·구성요소) 일치 | 요소별 부분 점수(entity만·kind만)도 기록 — OpenRCA 형식(`plans/101` §5.1) |
| ECR | 인용된 핵심 증거 수 / `key_evidence` 수. 인용 = `cite` 토큰이 브리핑에 있고 **그 도구가 그 응답을 실제로 내줬다**(세계 서버 로그) | 정답을 맞혀도 근거를 못 대면 낮다(P-24) |
| HALLU | 브리핑 수치 주장(숫자+단위) 중 세계 서버가 내준 어떤 출력에도 없는 것의 비율 | 반올림·단위 환산 허용 규칙을 테스트로 고정 |
| DIST | 1위 원인이 `distractors` 별칭과 일치 | |
| ABST | `expect.abstain`과 `evidence_insufficient`·최고 confidence의 일치 | |
| 완주 | W1 `incomplete`·`status` | |
| 비용 | W1 `elapsed_s`·`steps` · 세계 로그 도구 호출 수 · `tokens` | |
| Maj@k | 반복 k회 1위 (entity, kind) 최빈값 비율 | |

### 4.5 실패 유형 — 개선점을 가리키는 분류

한 실행은 아래 중 **가장 앞선 하나**로 분류한다(순서가 곧 진단 순서 — 끊긴 지점부터).

| 코드 | 판정 | 가리키는 개선 지렛대 |
|---|---|---|
| F-INFRA | 세계 서버·MLX·sre_agent 오류(무효표 — 분모 제외 · D-241 준용) | 하네스 |
| F-TMO | 타임아웃 | 예산·사전수집 |
| F-INC | step 상한 미완주 | 질문 범위·수렴 지침·사전수집·`max_steps` |
| F-ARG | 도구를 불렀으나 인자 오류(엉뚱한 호스트·시간창 밖) | 지침의 인자 안내·사건 구간 주입 |
| F-LOOP | 같은 호출 반복(정체 가드 `unresolved` 발생) | 수렴 지침·정체 가드 |
| F-MISS | 핵심 증거 도구를 한 번도 안 부름 | kind별 질문·플레이북·사전수집 축 |
| F-READ | 핵심 증거를 받았는데 원인 오판 | 모델·지침의 해석 규칙·결정적 상관 |
| F-DIST | 유인 증거를 원인으로 | 기준선 비교(평시 패턴) 지침·사전수집 기준선 |
| F-HALLU | 원인은 맞으나 출력에 없는 수치 인용 | 인용 검증 결정화 |
| F-CITE | 원인은 맞으나 ECR < 0.5 | 인용 지침·브리핑 인용 검증 |
| F-ABST | 기권 오류(무장애에 단정 / 근거 충분한데 불충분) | 판정·한계 규칙 |
| PASS | RC@1 적중 · ECR ≥ 0.5 · HALLU = 0 | — |

**W3 구현 확정(2026-10-13 · `judge.py` `JUDGE_VERSION=j2` — j1 대비 HALLU 환산·응답 시각 숫자·정체 노트 교정)** — 표의 순서를 다음처럼 읽는다.
- 공통 F-INFRA > F-TMO > F-INC. F-INFRA = status가 `done`·`timeout`이 아님 · `infra_error` · `done`인데 브리핑 없음/stub(분모 제외). `timeout`은 유효표(F-TMO).
- **RC@1 미적중**: F-ARG(핵심 증거 도구 호출이 전부 `arg_error` — 핵심 증거 없는 세계는 전체 호출) > F-LOOP(`unresolved` 또는 같은 도구·같은 인자 3회 이상 — 정체 가드 임계와 같음) > F-MISS > **F-DIST(1위 = 유인) > F-READ(그 밖의 오판)**. 표 순서(F-READ가 앞)대로면 핵심 증거를 받은 뒤의 유인 채택이 F-DIST로 영영 안 잡혀 바꿨다.
- **RC@1 적중**: F-HALLU > F-CITE(ECR < 0.5 · 핵심 증거 없는 세계는 건너뜀) > F-ABST > PASS.
- **기권 기대 세계**(`expect.abstain: true`): RC를 매기지 않는다(`rc1`·`rc3`=None). 단정했으면 1위가 유인일 때 F-DIST, 아니면 F-ABST · 기권했으면 F-HALLU > F-CITE > PASS.
- 정규화(NFKC · 대소문자 · 공백·`_`·`-`·`·` 제거) · 가설은 `rank`로 정렬(없으면 `cause` 1위) · 가설이 세계 호스트를 하나도 말하지 않으면 알람 호스트를 지목한 것으로 본다(D07 같은 타 호스트 원인 세계에서 불일치) · 구성요소는 부분 점수(`component_hit`)만. DIST는 1위가 유인 표면형과 맞고 정답 표면형과는 안 맞을 때만(부정 서술 보호).
- HALLU 단위 = `%`·바이트 계열·`ms`/`s`/`초`·`건`/`회`(분·시간·일은 기간으로 보고 제외) · 호스트명·날짜·시각·버전·8~14자리 숫자는 주장 아님 — **응답 문자열에도 같은 시각·날짜·버전 마스크를 걸고 식별자에 붙은 숫자는 빼서 근거로 쓰지 않는다** · 그 실행에서 실제로 내준 응답 + 알람 값만 근거 · 같은 단위는 허용 폭 = max(주장·응답 자릿수 반 단위)(98 ↔ 98.4 양방향) · 단위 환산은 **응답 수치를 주장 단위로 바꿔** 주장 자릿수 반 단위만 허용(응답의 0·1이 812ms·700MB를 뒷받침하지 않게). ABST = `evidence_insufficient` 또는 최고 confidence ≤ low.

### 4.6 세계 목록 (초안 24건 · 개발 16 / 홀드아웃 8)

| id | 분할 | kind | layer | 함정 |
|---|---|---|---|---|
| D01 | dev | cpu_runaway(배치 프로세스 폭주) | app | — |
| D02 | dev | memory_leak_oom(JVM 힙 누수 → OOM kill) | app | — |
| D03 | dev | disk_full(로그 미회전) | os | CPU 정상 배치 패턴(유인) |
| D04 | dev | disk_io_saturation(백업 I/O 대기) | os | — |
| D05 | dev | change_induced(배포 직후 CPU 상승) | change | 변경 이력에만 단서 |
| D06 | dev | swap_thrash | os | — |
| D07 | dev | cross_host_cascade(DB 지연 → WAS 다수 알람) | db | 원인이 다른 호스트 |
| D08 | dev | network_site(회선 단절 → 다수 서버 무응답) | network | — |
| D09 | dev | no_fault(일시 스파이크 · 이미 정상) | — | **기권 기대** |
| D10 | dev | disk_full(유인 강화 — CPU 90%대 상시) | os | 강한 유인 |
| D11 | dev | memory_leak(메트릭 도구 일부 오류) | app | 한계 서술 기대 |
| D12 | dev | target_unavailable(호스트 다운) | os | 가용성 판정 |
| D13 | dev | inode_exhaustion | os | 용량은 여유(유인) |
| D14 | dev | process_down(서비스 프로세스 종료) | app | — |
| D15 | dev | time_window(원인은 알람 시각 이전 · 지금은 정상) | os | `reference_time` 사용 검증 — W4 구현은 라벨 kind `memory_exhaustion` + 시간창 함정 |
| D16 | dev | apm_pool_exhaustion(제니퍼 DB 커넥션 풀 고갈) | app | **크로스소스 — `plans/150` W5 뒤** |
| H01~H08 | holdout | D01·D02·D03·D05·D07·D09·D10·D16과 같은 kind · 다른 호스트·프로세스·수치·문구 | | 개선 채택 판정 전용 |

- 이벤트 모양은 `noise_gate/scripts/mock_polestar_events.py`의 `cascade`·`change-corr`·`net-site-cascade` 시나리오를 빌린다(티어 기대값은 쓰지 않는다).
- 린트(W4): 라벨 완전성 · 별칭 ≥2 · `key_evidence`가 `tools`에 실제로 있는가 · 알람 문구에 정답 kind 별칭 금지 · 홀드아웃과 개발셋 문구 중복 금지 · `hosts`가 `event` 호스트를 포함하는가 · 대본(`script`)의 도구·인자가 스냅샷에 있는가(대본은 조사에 주입되지 않으므로 누수 검사 대상에서 뺀다).

### 4.7 실행 구성 — 프로세스 · 포트 · 설정 주입

한 번의 `--run`은 아래 프로세스를 띄우고, 끝나면 자기가 띄운 PID만 내린다. 운영·개발 기본 포트(9097·9098·9099·8080)는 쓰지 않는다.

```
벤치 CLI (sre_agent venv · cwd = sre_agent/)
 │
 ├─ 세계 서버      world_server.py    127.0.0.1:19190/sse  polestar_*·prom_*·om_* (실 mcp_server 조사 프로파일 도구 표면)
 │                                    127.0.0.1:19191/sse  apm_* (APM 세계가 있을 때만)
 │                                    127.0.0.1:19190/_bench/active   현재 세계 지정(루프백 제어 엔드포인트)
 ├─ 가짜 LLM       fake_llm.py        127.0.0.1:19180/v1   ← 테스트(L3·L4)에서만. 실측 회차에서는 띄우지 않는다
 ├─ sre_agent 대조군  _serve.py --port 19200   cwd = <run>/arm-control/  (빈 디렉터리 — .env 없음)
 ├─ sre_agent 실험군1 _serve.py --port 19201   cwd = <run>/arm-<name>/
 └─ …
          │ (각 sre_agent가 세계 서버에 MCP SSE로 붙고, LLM은 아래 하나로 간다)
          ▼
 LLM 엔드포인트: 외부망 = 로컬 MLX 127.0.0.1:8080/v1(`scripts/mlx_server.sh`)
               내부망 = 사내 vLLM(같은 GPU 서버 · 기본) 또는 fabrix_proxy(OpenAI 호환 → FabriX · 대체) — §6.7
```

- **sre_agent 진입점을 따로 둔다**(`investigation_bench/_serve.py`): 운영 진입점 `run_service.py`는 포트가 상수(`interface/mcp_service.py:45` `DEFAULT_PORT = 9098`)라 벤치 포트를 줄 수 없다. `_serve.py`는 `create_service(settings)` → `build_asgi_app(mcp, None)` → `uvicorn.run(port=<인자>)`만 하고(제품 코드 변경 0 · `scripts/itam_bench/_serve.py` 전례), 기동 직후 **실효 설정 에코**(비밀 필드 제외)를 `<run>/arm-<name>/settings_echo.json`에 쓴다(D-219 ④ · D-238 ⑤ — 에코 없는 실험군은 판정에서 제외).
- **실험군마다 sre_agent 인스턴스 1개**: 설정은 기동 시 1회 해석되므로 실험군 = 프로세스다. 전 실험군을 동시에 띄워 두고 **호출은 직렬**로 교차한다(재기동 없이 세계 단위 교차 순서를 지키고, 한 번에 조사 1건만 돌아 지연이 오염되지 않는다).
- **설정은 OS 환경변수로만 준다**: sre_agent 설정은 `env_file=(".env", ".encenv")`를 **실행 cwd 기준**으로 읽는다(`settings.py:13-17`). 벤치는 빈 디렉터리를 cwd로 주고 필요한 필드를 전부 OS 환경변수로 명시한다 — 개발 맥의 `.env`·`.encenv`(Gemini 키 상존)가 섞이지 않는다. **(W3 검증 정정 2026-10-13)** 빈 cwd만으로는 부족하다 — litellm이 import 때 `load_dotenv()`(기본 `LITELLM_MODE=DEV`)로 설치 위치에서 위로 `.env`를 찾아 싣는다. 러너 고정 env에 `LITELLM_MODE=PRODUCTION`을 넣고, 설정 에코의 「기본값과 다른 필드」가 러너·명세가 정한 것뿐인지 대조해 어긋나면 exit 2(docs/26 §10.7 · docs/18).
- **감사 파일 분리**: 잡 감사 경로는 기본이 `sre_agent/.data/investigation_audit.jsonl` 고정(`application/investigation_jobs.py:57`)이고 `JobStore(audit_path=…)` 인자는 있으나 `create_service`가 넘기지 않는다. W1에서 설정 필드 `investigation_audit_path`(기본 None = 현행 경로 · 비트 동일)를 더해 벤치 감사를 `<run>/arm-<name>/audit.jsonl`로 보낸다 — 운영 감사 파일을 오염시키지 않는다.
- **MCP 도구 등록 시점 (W0 실측 2026-10-13)**: sre_agent는 holmes MCP 서버(`Config.mcp_servers`)를 **프로세스당 1회** 등록한다. 조사마다 만들지 않는다. `_default_diagnose_fn`이 `DiagnosisAgent`를 첫 조사에서 만들어 `holder`에 둔다(`interface/mcp_service.py:196-203`). holmes `ToolCallingLLM` 생성도 그 인스턴스 안에서 1회다(`diagnosis.py:180-195`). 생성 시 `RemoteMCPToolset.prerequisites_callable`이 `list_tools`를 1회 부르고(`holmes/plugins/toolsets/mcp/toolset_mcp.py:970`) 헬스체크 도구를 1회 호출한다(폴스타 `list_sources` · APM `gateway_health` — `mcp_service.py:176,179`). 그 뒤 도구 호출은 **호출마다 새 SSE 세션**이다(`toolset_mcp.py:419` `asyncio.run(_invoke_async)` → 282 `sse_client`). 벤치에 주는 함의는 셋이다. ① 세계 전환에 재기동이 필요 없다 — 제어 엔드포인트 방식이 성립한다. ② 세계 서버는 `list_sources`·`gateway_health`를 성공으로 답해야 한다. 아니면 그 toolset이 꺼진 채 프로세스 수명 내내 남는다. ③ 첫 조사 전에 세계 서버가 떠 있어야 한다. 또 헬스체크 1건이 첫 실행의 `world_calls`에 섞이므로 러너가 구간 밖으로 뺀다.

**실험군 하나의 sre_agent 환경변수**(러너가 조립 · `runner.build_arm_env`):

| 키 | 값 | 이유 |
|---|---|---|
| `MODEL` · `API_BASE` · `API_KEY` | 실행 명세의 LLM(`openai/<served>` · 엔드포인트 · 더미 허용) | 운영 배선 경로(D-229) |
| `INVESTIGATION_LLM_ENABLED` | `true` | 실 조사 게이트를 명시로 연다(D-230) |
| `GEMINI_API_KEY` · `LLM_GEMINI_API_KEY` | 빈 값 | 과금 폴백 차단(`settings.py:53` 별칭 둘 다) |
| `POLESTAR_MCP_URL` · `APM_MCP_URL` | `http://127.0.0.1:19190/sse` · `…19191/sse`(APM 세계 없으면 빈 값) | 세계 서버 |
| `MAX_STEPS` · `INVESTIGATION_TIMEOUT_SECONDS` · `OVERRIDE_MAX_CONTENT_SIZE` · `OVERRIDE_MAX_OUTPUT_TOKEN` | 실행 명세 `base_env`(내부망은 운영 실효값을 그대로 적는다) | 운영과 같은 조건 |
| `INVESTIGATION_MAX_CONCURRENT` | `1` | 직렬 |
| `INVESTIGATION_DEDUP_TTL_SECONDS` · `INVESTIGATION_HOURLY_BUDGET` | 설정하지 않음 | 반복 실행이 가드에 막히지 않게(반복마다 지문도 바꾼다) |
| `INVESTIGATION_AUDIT_PATH` | `<run>/arm-<name>/audit.jsonl` | W1 신설 필드 |
| 실험군 플래그 | 실행 명세 `arms.<name>` | 대조군은 빈 값 = 현행 기본값 |

**실행 명세**(`--spec <yaml>` · 저장소 `sre_agent/testdata/investigation_bench/specs/`):

```yaml
run:
  site: local            # local | closed — closed만 사설 주소 LLM 허용(§6.4)
  splits: [dev, holdout]
  worlds: all            # 또는 [D01, D03, D09] — 스모크
  k: 3
  order: interleave      # 세계마다 실험군을 번갈아(대조군 → 실험군1 → … 순서를 세계마다 회전)
  poll_interval_s: 2
  per_run_timeout_s: 420 # sre_agent 타임아웃(300) + 사전수집 + 여유. 넘으면 F-INFRA
llm:
  backend: mlx           # fake | mlx | vllm | fabrix_proxy — 사람이 적는 표기. manifest·보고서의 백엔드 칸이 되고, 처분 비교는 같은 backend끼리만(§6.7)
  model: "openai/<served>"
  api_base: "http://127.0.0.1:8080/v1"
  api_key: dummy
host:
  gpu_sample_interval_s: 30   # closed에서 nvidia-smi가 있으면 회차 중 GPU 사용률 샘플(§6.7) · 0 = 끔
base_env: {MAX_STEPS: 40, INVESTIGATION_TIMEOUT_SECONDS: 300}
arms:
  control: {}
  control_b: {}                       # 1회차 A/A
  # h4_citation: {CITATION_VALUE_CHECK: true}
```

저장소에 두는 명세: `fake_e2e.yaml`(가짜 LLM · 세계 2 × 실험군 2 × k=1 — 테스트) · `local_smoke.yaml`(D01·D03·D09 × k=1 · 대조군만 — W5) · `closed_round1.yaml`(전 세계 × k=3 · `control`·`control_b` · `backend: vllm`) · `closed_round1_fabrix.yaml`(같은 구성 · `backend: fabrix_proxy` · FabriX 기준선용 — §6.7) · `closed_roundN.template.yaml`(W7이 실험군을 채움).

**W3 구현 확정(2026-10-13)** — 명세에 더한 칸: `run.worlds_dir`(기본 `testdata/investigation_bench/worlds` — 그 아래 `dev/`·`holdout/`) · `run.snapshot` · `run.ports{fake_llm, world, world_apm, arm_base}`(0 = 빈 포트) · `run.out_root` · `run.schema_live_urls`(없으면 「미대조」) · `llm.script`(가짜 LLM 대본 `model`|`neg.<코드>`). 실험군 env에 고정 키 2개를 더한다 — `LITELLM_LOCAL_MODEL_COST_MAP=True`(litellm이 import 때 원격 비용표를 받으러 나가지 않게) · `PYTHONUNBUFFERED=1`. 명세 `base_env`·`arms`가 러너 보호 키나 `INVESTIGATION_DEDUP_TTL_SECONDS`·`INVESTIGATION_HOURLY_BUDGET`을 쓰면 exit 2. raw 행 가산 키: `wall_s`·`error`·`reason`·`excluded`(설정 에코 없는 실험군)·`phase_ambiguous`(`prefetch_failed`로 단계 경계 불명)·`arm_restarted_after`(이 실행 뒤 실험군 재기동)·`stray_calls_n`(+있으면 `stray_calls[]` — 떠돌이 호출) · world_calls 행에 `arm` · manifest `arms.<arm>.restarts`(+`restart_s[]` 소요). **시간 초과 뒤 정리**(W5 실측 — D03 타임아웃 뒤 버려진 holmes 스레드의 호출이 다음 실행 구간에 섞였다): 1선 — 실행이 sre_agent `timeout` 상태나 폴링 초과(무효표)로 끝나면 그 실험군 sre_agent만 자기 PID로 내리고 같은 포트·env·cwd로 다시 띄워 헬스 대기·설정 에코 재대조 뒤 다음 실행으로 간다(세계 서버·가짜 LLM은 그대로 · 등록 헬스체크 제외 기억도 초기화 · 가짜 LLM 실측 약 2.7s/회). 2선 — tool_trace를 만들 때 호스트 인자(`hostname`·`server_name`·`targets[]`)가 현재 세계가 아닌 다른 세계 호스트만 가리키는 호출은 `stray: true`로 빼고 개수를 센다(호스트 인자 없는 호출은 판별 불가라 유지 · world_calls 원본은 그대로). 가짜 LLM 대본은 `repeat: true`(step 소진용)를 지원한다. **세계 모범 답안은 도구명을 인용해야 한다**(없으면 sre_agent 인용 검사가 원인을 「[가설]」로 강등해 F-ABST) · 「근거」는 인용 표지라 기권 문구에 쓰지 않는다.

### 4.8 한 실행의 순서 (`runner.py`)

1. `POST /_bench/active {world_id, trial}` — 세계 서버가 현재 세계를 바꾸고 호출 로그 구간을 연다.
2. `sre_investigate_alarm(payload)` — `payload = {contract_version:"1", event: <세계 event>, decision:{fingerprint: "<run>:<arm>:<world>:<trial>"}, meta:{…세계 meta}}` · `wait_seconds=0`.
3. `sre_get_investigation(investigation_id)`를 `poll_interval_s`마다 — 상태가 종결(`done`·`failed`·`timeout`·`stub`·`rejected`)이거나 `per_run_timeout_s`가 지나면 멈춘다.
4. 세계 서버 호출 로그 구간을 닫고, 그 실험군 감사 파일에서 이 `investigation_id`의 이벤트를 모은다.
5. raw 1행을 쓴다(§4.9). 채점은 실행이 다 끝난 뒤 따로 한다(`--rejudge`와 같은 함수).

- `rejected`(가드에 막힘)·`stub`(LLM 게이트 닫힘)·호출 예외·폴링 시간 초과는 **무효표(F-INFRA)** 로 남기고 분모에서 뺀다.
- 사전수집 호출과 LLM 도구 호출의 구분: 감사의 `prefetch`·`prefetch_failed` 이벤트 시각 이전 호출을 `phase=prefetch`로 표시한다(W0 실측 2026-10-13). 워커가 `_run_and_postprocess`에서 `_prefetch`를 끝까지 기다린 뒤 조사를 시작하므로(`application/investigation_dispatcher.py:404-406`) 정상 경로에서는 겹치지 않는다. 사전수집은 별도 데몬 스레드(`sre-prefetch`)에서 돌지만 워커가 join한다(525-553). `prefetch` 감사는 사전수집이 **끝난 뒤** 한 번 기록된다(513-522). 따라서 「그 시각 이전 = 사전수집」이 성립한다. 시각은 sre_agent·세계 서버 모두 같은 호스트의 `time.time()`이다. 예외는 셋이다.
  - **사전수집 타임박스 만료**(`prefetch_failed` · 기본 `max(하한, 조사 타임아웃/2)`): 버려진 사전수집 스레드가 조사와 **겹쳐** 계속 호출할 수 있다. 이 실행은 시각만으로는 phase를 가를 수 없다(처리 방식은 W3가 정한다).
  - **기록 없이 끝나는 경로**: 사전수집 미주입(`EVIDENCE_CORRELATION_ENABLED` off)이나 `reference_time` 없음이면 호출 자체가 없다(494-495). 범위를 못 만들면(`scope_from_job` → None) 호출도 감사도 없다(`mcp_service.py:233-234`). 두 경우 모두 phase 구분이 필요 없다.
  - **보조 구분 신호**: 사전수집은 배치 1회에 **SSE 세션 1개로 여러 호출**을 보낸다(`infrastructure/mcp_tool_client.py:52-53`). holmes는 **호출마다 세션 1개**다(§4.7). 둘 다 MCP `clientInfo` 기본값이라 클라이언트 이름으로는 가를 수 없다.

### 4.9 데이터 계약 — 산출 파일

`<run>/` = `sre_agent/.data/investigation_bench/<run_id>/` (`run_id` = `YYYYMMDD-HHMMSS-<site>`)

| 파일 | 한 행/한 건 | 주요 필드 |
|---|---|---|
| `manifest.json` | 실행 1건 | `run_id` · 저장소 커밋 · 세계 목록과 세계별 내용 해시 · `tool_schemas.json` 해시 · holmesgpt·litellm·mcp 버전 · **LLM 백엔드**(`backend`) · LLM 모델명 · `api_base`(반출 시 가림) · 서빙 정보(`/v1/models` 응답 — vLLM이면 `max_model_len` 포함) · **실행 호스트 정보**(OS · CPU 수 · GPU 모델·장수·드라이버 — `nvidia-smi`가 있을 때 · 실행 전후 GPU 사용 프로세스 목록) · 실행 명세 전문 · 실험군별 `settings_echo`·`restarts`(시간 초과 뒤 재기동 수) · 사전 점검 결과 · 시작·종료 시각 · 실행당 평균 소요 · 무효표 수 · `judge_version` |
| `world_calls.jsonl` | 세계 서버 호출 1건 | `ts` · `world_id` · `trial` · `endpoint` · `tool` · `args` · `matcher`(맞은 규칙 id 또는 `default`) · `arg_error`(`host`·`time_window`·`required`·`null`) · `reply`(전문) · `reply_hash` |
| `raw.jsonl` | 조사 실행 1건 | `arm` · `world_id` · `split` · `trial` · `investigation_id` · `fingerprint` · `submitted_at` · `finished_at` · `elapsed_s` · `status` · `incomplete` · `verdict` · `tokens` · `steps` · `tool_calls_n` · **`briefing`(전문)** · `tool_trace[{seq, phase, tool, args, matcher, arg_error, reply_hash}]` · `audit[]`(그 조사의 감사 이벤트) · `infra_error` · `arm_restarted_after` · `stray_calls_n` |
| `scores.jsonl` | 조사 실행 1건 | `valid` · `rc1` · `rc3` · `entity_hit` · `kind_hit` · `ecr`(분자·분모) · `hallu`(분자·분모·걸린 수치 목록) · `dist` · `abst_ok` · `complete` · `failure_code` · `judge_version` · `notes[]` |
| `report.md` | 실행 1건 | 실험군 × 지표 표(개발·홀드아웃 분리) · 실패 유형 분포 · A/A 잡음폭 · 처분 초안 · **오답 검토표**(오답 판정 표본별 정답 라벨 · 1위 가설 원문 · 걸린 규칙) · 무효표 고지 |
| `gpu_samples.jsonl` | 샘플 1건(closed · `nvidia-smi` 있을 때) | `ts` · GPU별 사용률·메모리 · 사용 프로세스 수 (§6.7) |
| `pids.json` | 실행 1건 | 벤치가 띄운 프로세스(세계 서버·가짜 LLM·실험군별 sre_agent)의 PID·포트·시작 시각 — `--cleanup`은 이 목록만 종료 |
| `arm-<name>/settings_echo.json` · `audit.jsonl` · `stderr.log` | 실험군 1개 | 실효 설정 · 감사 · 프로세스 로그(무효표 진단용 · 반출 시 마지막 200줄만) |

`--rejudge <run_dir>`는 `raw.jsonl`·`world_calls.jsonl`과 **현재 저장소의 세계 YAML·채점기**로 `scores.jsonl`·`report.md`를 다시 쓴다(이전 판은 `scores.<judge_version>.jsonl`로 보존). 같은 입력이면 같은 출력이다.

### 4.10 세계 서버 동작 규칙 (`world_server.py`)

- **도구 등록**: `tool_schemas.json`의 이름·설명·`inputSchema`를 그대로 등록한다. 등록 방법은 sre_agent venv `mcp` 1.25.0에서 확인했다(W0 실측 2026-10-13).
  - FastMCP `add_tool(fn, name, description, …)`은 **함수 시그니처에서 스키마를 만든다**. 호출 인자도 그 pydantic 모델로 검증·절단한다(`fn_metadata.call_fn_with_arg_validation`). 따라서 스냅샷 스키마를 그대로 낼 수 없다.
  - 대신 `FastMCP` 인스턴스는 그대로 쓰되 저수준 서버 `mcp._mcp_server`의 `@list_tools()`와 `@call_tool(validate_input=False)` 핸들러를 덮어쓴다. `list_tools`는 `types.Tool(name, description, inputSchema=<스냅샷 그대로>)`을 돌려주고, `call_tool(name, arguments)`가 매처로 응답한다. `validate_input=True`로 두면 jsonschema 검증 실패가 오류 응답이 된다. 실 서버 거동과 맞출지는 W2가 정한다.
  - 추가 HTTP 경로는 `@mcp.custom_route("/_bench/active", methods=["POST","GET"])`로 붙인다. 이 경로는 `mcp.sse_app()`에 함께 실린다(`mcp/server/fastmcp/server.py` 939). 기동은 `mcp.run(transport="sse")` 또는 `uvicorn.run(mcp.sse_app(), host="127.0.0.1", port=…)`로 한다.
  - 프로토타입(scratchpad)에서 확인한 것: 클라이언트 `list_tools`가 스냅샷 `inputSchema`를 바이트 그대로 받았고, 스키마에 없는 인자도 핸들러까지 전달됐으며, `/_bench/active` POST로 바꾼 세계가 다음 호출에 반영됐다.
  - **주의**: holmes는 MCP 스키마를 LLM에 넘길 때 OpenAI strict 형태로 바꾼다. 선택 인자는 `anyOf:[T, null]`로 감싸 `required`에 넣고 `strict: true`를 붙인다(덤프 실측 · `holmes/core/openai_formatting.py:15`). 그래서 LLM은 선택 인자를 `null`로 보낼 수 있다 — 매처는 `null` 인자를 「없음」으로 다룬다. 스냅샷은 실 `mcp_server`를 **조사 프로파일**(`EXPOSE_EXECUTE_SQL=false`·`EXPOSE_RAW_PROMQL=false` — D-122 · `tests/test_investigation_e2e.py` 머리 주석)로 띄워 뜬다. 운영 조사가 보는 도구 표면과 같게 하기 위해서다.
- **세계 선택**: 러너가 지정한 현재 세계 하나. 도구 인자에 호스트(`hostname`·`server_name`·`resource_name` 등 — 키 목록은 스냅샷에서 뽑아 설정으로 둔다)가 있고 그 값이 현재 세계의 호스트 집합에 없으면 「대상 없음」 응답 + `arg_error=host`.
- **매처**: 세계 YAML `tools.<도구>[]`를 위에서부터 보고 `match`의 키·값이 인자에 모두 같으면 그 `reply`. 없으면 `default`. 정의되지 않은 도구는 빈 결과(오류 아님).
- **시간창**: 인자에 시간 범위가 있고 세계에 `incident_window`가 있으면, 범위가 사건 구간을 하나도 덮지 않을 때 빈 결과 + `arg_error=time_window`(F-ARG · D15 함정의 근거).
- **생성기**: `gen` 지시는 `(world_id, tool, 정렬된 args)` 해시를 시드로 펼친다 — 같은 호출 = 같은 응답(반복 간 응답 차이로 생기는 잡음 제거).
- **오류 주입**: `reply: {error: "...", inject: true}`는 실 서버의 도구 오류 모양으로 돌려준다(D11).
- **로그**: 모든 호출을 `world_calls.jsonl`에 쓴다. 제어 엔드포인트는 127.0.0.1에만 바인딩한다.
- **W2 구현 확정(2026-10-13)**:
  - 호스트 인자 키 = `hostname`·`server_name`(스냅샷 실측 — 폴스타 `server_name` 6종·`hostname` 4종, APM `hostname` 17종 · `instance_name`은 WAS 인스턴스라 제외) + 대상 목록 `targets`(APM 17종 — 항목마다 검사). 도구별로 스냅샷 `inputSchema`에 실제로 있는 키만 본다.
  - 시간창: 끝 = `reference_time`, 폭 = `lookback_minutes` → `hours`×60 → `window`(기간 문자열) 순(인자에 없으면 스키마 기본값) · `current_start`/`current_end`도 본다. 폭을 알 수 없으면 검사하지 않는다(거짓 F-ARG 방지).
  - **필수 인자 누락**(`null` 포함)은 실 FastMCP처럼 `isError=true` 검증 오류 + `arg_error=required`(F-ARG). 호스트 인자를 빠뜨린 호출에 세계 데이터를 내주면 정답이 새기 때문이다. 그 밖의 입력 검증은 하지 않는다(`validate_input=False`).
  - 응답 모양(실 서버 실측): 정상 = 텍스트 JSON + `structuredContent={"result": <텍스트>}`(실 스냅샷 전 도구에 `outputSchema`가 있어 없으면 클라이언트가 거부). 도구 오류도 `isError=false` + JSON `error` 키(polestar `{"error": msg}` · apm `{"error": code, "reason", "source_kind": "apm_api", "source": "jennifer", "tool"}`). 「대상 없음」 = polestar `{"rows": [], "row_count": 0, "message": "대상 없음 — …"}` · apm `{"error": "instance_unresolved", …}`. 빈 결과 = `{"rows": [], "row_count": 0}`(prom_*도 같은 모양 — 실 서버 `{data…}`를 단순화).
  - 헬스체크 도구(`list_sources`·`gateway_health`)는 내장 성공 응답이 세계 `default`보다 먼저다(세계가 명시 규칙을 두면 덮음). 세계 미지정에서 그 밖의 도구 = `{"error": "벤치 세계 미지정 …"}`. 매처 id = `<tool>#<i>` 또는 `default`.
  - `gen` 키: `base·to·spike_at·noise·points(12)·step(5m)·end(0m)·decimals(1)·ts_key(ts)·value_key(value)` · 시드에서 `null` 인자는 뺀다.
  - 단독 기동: `python -m investigation_bench.world_server --snapshot … --worlds … --log … --port 19190 [--apm-port 19191]`(러너가 별도 프로세스로 띄움).
  - §4.2 예시의 `metric: filesystem`은 실 인자명이 `kind`다 — 세계 YAML은 스냅샷 인자명을 쓴다(린트가 강제).

### 4.11 가짜 LLM (`fake_llm.py` · 테스트 전용)

- OpenAI 호환 `/v1/chat/completions`·`/v1/models`. holmes(litellm `openai/…`)가 부르는 그대로 받아, **세계 YAML의 대본**(`script:`)을 한 턴씩 내준다: `[{call: <도구>, args: {...}}, …, {answer: "<브리핑 서술>"}]`. 도구 결과 메시지가 들어오면 다음 대본 줄로 넘어간다.
- 대본 선택은 세계 서버와 같은 「현재 세계」 지정을 쓴다(러너가 둘 다에 보낸다).
- 대본 종류: `script.model`(모범 답안 — PASS가 나와야 한다) · `script.neg.<코드>`(일부러 틀리는 대본 — 원인 오판 · 없는 수치 인용 · 유인 채택 · 같은 호출 반복 · 끝없이 호출해 step 소진 · 엉뚱한 호스트). 음성 대본마다 기대 실패 코드를 함께 적는다.
- 응답 형식은 holmes 0.36.0이 실제로 보낸 요청을 덤프해 정했다(W0 실측 2026-10-13). 덤프는 127.0.0.1 덤프 서버에 litellm 1.89.0 · openai 2.54.0으로 붙여 떴다. 실 LLM 호출은 0이다.
  - **요청**: 생성 시 `GET /v1/models` 1회 → 턴마다 `POST /v1/chat/completions`. 본문 키는 `model`·`messages`·`tools`·`tool_choice`·`temperature`(1e-08)·`max_tokens`다. `max_tokens`는 모델표에 없으면 64000이고, `OVERRIDE_MAX_OUTPUT_TOKEN`이 덮는다. **`stream`은 보내지 않는다** — holmes가 `stream=False`로 부른다(`tool_calling_llm.py:1169`). 따라서 가짜 LLM은 비스트리밍 JSON 한 덩어리만 내면 된다.
  - **`tools` 모양**: `[{type:"function", function:{name, description, parameters, strict:true}}]`. `parameters`는 strict 변환된 스키마다(§4.10 주의). `tool_choice`는 `"auto"`다. 단 **마지막 턴(`i == max_steps`)은 `tools`·`tool_choice` 키 자체가 없다**(1108-1109).
  - **메시지 역할**: `system`·`user` → 이후 `assistant`(`tool_calls`와 `provider_specific_fields` 포함)·`tool` 반복. `tool` 메시지는 `{role, name, tool_call_id, content}`다. `content` 머리는 `Params used for the tool call: {…}. The tool call output follows on the next line.\ntool_call_metadata={…}`이고 그 뒤에 도구 출력이 붙는다. 대본 진행은 「마지막 메시지가 `tool`이면 다음 줄」로 판정한다.
  - **응답**: OpenAI `chat.completion` 형태 `{id, object, created, model, choices:[{index:0, message:{role:"assistant", content, tool_calls:[{id, type:"function", function:{name, arguments:<JSON 문자열>}}]}, finish_reason:"tool_calls"|"stop"}], usage:{prompt_tokens, completion_tokens, total_tokens}}`. `usage`를 주면 holmes가 그대로 누적한다(`total_tokens` 검증 가능).
  - **대본 주의**: 같은 도구·같은 인자를 다시 부르면 holmes가 실행하지 않고 거부한다(§2.3). 「같은 호출 반복」 음성 대본은 세계 서버 호출이 아니라 holmes 거부 오류로 나타난다. 「끝없이 호출해 step 소진」 대본은 마지막 도구 없는 턴에도 `tool_calls`를 내야 상한 예외(미완주)가 된다. 텍스트를 내면 정상 결론으로 끝난다.
- 실측 회차(W5·W6·W8)에서는 쓰지 않는다. 사전 점검은 `api_base`가 가짜 LLM 포트이면 `--test` 플래그 없이는 거부한다(가짜 결과가 측정으로 섞이지 않게).

---

## 5. 개선 후보 (가설 — 내부망 1회차 기준선이 순위를 정한다)

기준선 전이라 **채택 대상이 아니라 실험군 후보**다. 1회차(W6) 실패 유형 분포를 외부망 검토(W7)가 진단해 상위 유형을 가리키는 후보부터 다음 회차에 올리고, 표에 없는 새 가설도 검토에서 추가할 수 있다.

| ID | 가설 | 겨냥 유형 | 손댈 곳 | 플래그(기본 off) | `plans/150`과의 관계 |
|---|---|---|---|---|---|
| H-1 | 알람 kind별 질문 템플릿(고정 1종 → kind 축약 트리아지)이 step을 줄이고 F-MISS를 줄인다 | F-INC · F-MISS | `interface/mcp_service.py:85` | `investigation_question_by_kind` | 150 §4.6이 질문을 크로스소스용으로 확장 — **150 W5 랜딩 뒤 그 위에 얹는다** |
| H-2 | 사전수집을 기본 on으로 + 축을 kind에 맞춰 고른다(현 4축 고정) | F-MISS · F-INC | `application/evidence_prefetch.py:27` · `settings.py:166` | 기존 `evidence_correlation_enabled` + 신규 `evidence_prefetch_axes_by_kind` | 150이 사전수집 소스를 고친다 — 뒤에 |
| H-3 | 사전수집 결과 요약을 지침 앞에 넣어 같은 조회를 막는다(W0 실측: 지침에는 `correlation_note` 한 단락 — 이상 판정 지표·알람 요약·타임라인 앞 12줄만 들어가고 정상 지표·원 시계열·사전수집이 부른 도구·인자 목록은 빠진다 → 전제 성립) | F-LOOP · F-INC | `application/investigation_guidance.py` | `prefetch_summary_in_guidance` | 지침 조립 순서를 150이 바꾼다 — 뒤에 |
| H-4 | 인용 검증을 결정화한다 — 인용 수치를 도구 출력과 대조해 없으면 가설 강등·`limitations`에 표기(현 마커 문자열 검사) | F-HALLU · F-CITE | `application/briefing_builder.py:30,143` + `ToolCallRecord` 출력 사용 | `citation_value_check` | 독립 |
| H-5 | 미완주 시 수집된 도구 출력으로 결정적 부분 브리핑을 만든다(현 answer 문구만) | F-INC의 피해 축소 | `diagnosis.py:212-225` · `briefing_builder.py` | `partial_briefing_on_incomplete` | 독립 · W0 실측: `llm.call` 예외 경로로는 회수 불가, `call_stream`을 직접 소비하면 도구 결과·누적 토큰 회수 가능 → 실현 가능 |
| H-6 | 기본 off 기능 6종(`severity_judge`·`remediation_recommender`·`evidence_change_overlay`·`openmetrics_guidance`·`apm_guidance`·`apm_signatures`)을 하나씩 켠다 | 각 기능 겨냥 유형 | 설정만 | 기존 플래그 | 독립 — 축 측정만 |
| H-7 | 평시 기준선 비교 지침(「같은 시각 지난 N일 대비」) | F-DIST | `investigation_guidance.py` · 사전수집 `evidence_baseline_periods` | `baseline_compare_guidance` | 뒤에 |
| H-8 | `max_steps`·타임아웃 축(20/40/60) | F-INC · 비용 | 설정만 | 기존 필드 | 독립 |

모델·백엔드 축은 두지 않는다 — 회차마다 백엔드 하나(기본 사내 vLLM · 대체 FabriX)로 대조군과 실험군을 같이 돌리고, 처분은 같은 백엔드 안에서만 내린다(§6.7). 로컬 MLX 결과는 처분에 쓰지 않는다(D-127 「지연 결론은 내부망」).

---

## 6. 측정 절차와 판정

### 6.1 기본 규모

- **내부망(GPU 서버 · 사내 vLLM 기본)**: 반복 k=3(G-7) · 개발 16 · 홀드아웃 8 → 실험군 1개당 72회. 1회차는 대조군 2회(A/A) = 144회. 사내 vLLM 1회 소요는 미측정이라 1회차 manifest의 실측으로 다음 회차의 실험군 수를 정한다.
- **외부망(로컬 MLX)**: 대표 3세계 × 1회만(W5 하네스 확인) · 개선 코드는 바뀐 경로의 대표 1~2세계 × 1회만(로직 확인). MLX 9B 1회 ≈ 3분(`plans/91` 1-17 실측 173초)이라 A/B·기준선은 외부망에서 돌리지 않는다.
- 대조군은 같은 run 안에서 실험군과 번갈아 돈다(세계 단위 교차 — 시간대 영향 상쇄).

### 6.2 채택 판정 (D-238 처분 어휘)

1. **A/A 잡음폭**: 기준선 측정 때 대조군을 두 번 돌려 지표별 |Δ|를 잰다. 이것이 잡음폭이다.
2. 실험군 vs 대조군은 **홀드아웃**에서 세계×반복 쌍으로 비교한다.
3. 처분:
   - `채택 권고` — 주 지표(RC@1)가 잡음폭과 1세계분(1/8) 중 큰 값보다 크게 오르고, 가드 지표(완주율 · HALLU · DIST · 지연 p90 +50% 이내)가 나빠지지 않을 때 → 기본값 변경을 D-324 부기로 제안(사용자 확정)
   - `기각` — 대조군이 같거나 낫다(현행 유지 · 「권장하지 않음」 기록)
   - `판정 불가` — 차이가 잡음폭 안이거나, 실험군 완주 0건 · 무효표 > 20%(D-219 ③ 준용 — 판정표 머리에 무효 고지 후 `exit 1`)
4. 개발셋 점수는 보고만 하고 처분에 쓰지 않는다.
5. **W3 구현 확정**(`report.py`): 처분은 홀드아웃에서 세계×반복이 양쪽 모두 유효한 쌍만으로 낸다 · 기준 = max(A/A 잡음폭, 1/홀드아웃 세계 수) · Δ ≤ 0 → `기각` · 0 < Δ ≤ 기준 → `판정 불가` · Δ > 기준이어도 가드 악화(완주율·HALLU·DIST가 잡음폭을 넘게 나빠짐 · 지연 p90 1.5배 초과)면 **`기각`**(§6.2가 정하지 않아 보수 쪽으로 고름) · 그 회차에 A/A 쌍이 없으면 「1세계분만 적용」 고지. Maj@k는 유효 반복 2회 이상 세계만.

### 6.3 기록 위생

- 합성 세계라 운영 데이터는 없다. `raw.jsonl`에 브리핑 전문·세계 서버 호출 로그(도구·인자·응답)·W1 계측 키를 남긴다. 체크포인트·디버그 로그는 판정 입력이 아니다(D-219 ①).

### 6.4 과금 차단 (`preflight.py`)

- sre_agent 실효 설정의 `model` 프리픽스와 `api_base`를 읽어 **provider가 OpenAI 호환(`openai/…`)이고 `api_base`가 ① 127.0.0.1/localhost(외부망 MLX) 또는 ② `--site closed`로 실행하며 127.0.0.1(같은 GPU 서버의 vLLM·`fabrix_proxy`)·사설 주소·내부 호스트일 때만** 진행한다. `--site closed`의 127.0.0.1은 MLX로 보지 않고 명세의 `backend`를 기록한다. `gemini/…`·`anthropic/…`·공용 API 호스트(Google·Anthropic·OpenAI 도메인)는 거부(exit 2).
- 사전 점검은 tool-calling 왕복 1회를 포함한다(백엔드가 도구 호출을 돌려주지 못하면 회차 전체가 무효 — 실행 전에 멈춘다). `fabrix_proxy`는 OpenAI 호환 표면이라 같은 왕복으로 확인한다(프록시 `/health`는 생존만 보므로 왕복을 대신하지 못한다).
- FabriX는 비과금 평면이다(D-222 워커 비과금 `fabrix`) — `--allow-billed` 대상이 아니다. 단 호출량은 §6.7 규모 확인을 따른다.
- `--allow-billed`는 존재하되 사용자 승인 문구를 인자로 받아 manifest에 남긴다(D-127 — 건별 승인 · 포괄 승인 없음). Gemini 실행 시에도 D-120 데이터 통제(합성 세계뿐)는 자동 충족된다.
- 설정 판정은 pydantic 필드로만 한다(`os.getenv` 금지 · `.env`는 `os.environ`에 주입되지 않음 — 2026-08-28 실수 이력 · 실제 소비 필드는 `model`/`api_base`이지 `investigation_llm_model`이 아니다).

### 6.5 내부망 회차와 반출 묶음 (G-4 · G-7 · 인터뷰)

- **회차** = 내부망에서 사용자가 `--site closed --run`으로 한 번 돌리고 `--export`로 묶음을 만드는 단위다. 1회차는 대조군 A/A(기준선), 2회차부터는 대조군 + 그 회차의 실험군(외부망 검토가 정한 후보 · 플래그로 켬).
- **반출 묶음**(`<run_id>.bundle/`): `<run>/`의 `manifest.json` · `world_calls.jsonl` · `raw.jsonl`(**브리핑 전문** 포함) · `scores.jsonl` · `report.md` · `gpu_samples.jsonl`(있을 때) · `arm-<name>/settings_echo.json`·`stderr.log` 마지막 200줄(필드 계약 §4.9). 실험군 감사 파일은 `raw.jsonl`의 `audit[]`에 조사별로 이미 들어 있어 따로 싣지 않는다.
- **가리는 것은 내부 주소·계정·경로뿐이다**: `api_base` 호스트 · 실행 OS 계정 · 서버 호스트명 · 내부 절대 경로. `export.py`는 가린 뒤 잔존 검사(설정에서 읽은 원값·사설 IP 대역·홈 경로 패턴)를 돌리고, 걸리면 묶음을 만들지 않는다(D-301 「누출 관문 실패 = 미기록」 준용).
- 세계 YAML은 저장소에 있으므로 묶음에 넣지 않고 커밋 해시로 가리킨다.
- GPU 서버 호스트명·`nvidia-smi` 프로세스 목록의 사용자명·경로도 가림 대상이다(manifest 호스트 정보).

### 6.6 외부망 검토 — Claude Code (G-5 · 인터뷰)

반출 묶음을 받으면 외부망 Claude Code가 아래 셋을 낸다. 스킬로 고정하지 않고, 이 절과 W7 행이 절차의 정본이다.

| 산출 | 내용 | 반영 |
|---|---|---|
| ① **채점 교정안** | 결정적 채점이 틀린 건(별칭 누락으로 정답을 오답 처리 · 인용 토큰 경계 오류 · 수치 반올림 규칙 등)을 찾아 세계 YAML 별칭·채점 규칙 수정 | 커밋 후 같은 묶음을 `--rejudge`로 재채점 → 교정 전후 점수를 보고서에 나란히 |
| ② **실패 원인 진단 · 개선 후보** | 실패 유형별 표본의 도구 호출 순서·브리핑을 읽고 끊긴 지점(질문·지침·사전수집·인자·해석·모델 한계)을 판정 · H-* 순위 조정 · 새 가설 추가 | 계획서 「회차 기록」 절에 장부로 남기고 사용자에게 다음 회차 실험군을 확정받는다 |
| ③ **개선 코드** | 확정된 후보를 플래그 1개(기본 off · 비트 동일)로 구현 · 개발셋 기준 · MLX 대표 1~2세계 × 1회 로직 확인 | 다음 회차 키트에 실험군으로 실린다 |

- **홀드아웃 열람 규칙**: ①은 홀드아웃 원문도 본다(오채점이 처분을 왜곡하므로). ②·③은 **개발셋 원문만** 본다 — 홀드아웃은 점수·유형 집계만 쓴다. 검토 세션에 위임할 때 홀드아웃 원문 경로를 프롬프트에 넣지 않는다.
- 처분(§6.2)은 교정 후 재채점 점수로 내린다. ①이 홀드아웃 점수를 바꾸면 교정 내용과 영향 세계를 보고서에 명시한다(교정이 실험군 편을 드는지 사람이 볼 수 있게).

### 6.7 내부망 실행 환경 — GPU 서버 동거 · LLM 백엔드 (v1.3 · 사용자 2026-10-13)

**배치**: sre_agent(HolmesGPT)·세계 서버·벤치 CLI를 **vLLM이 도는 GPU 서버에 함께** 올린다. HolmesGPT는 CPU로 도는 파이썬 에이전트이고 GPU는 vLLM이 쓴다. 회차 하나의 프로세스는 §4.7 그림 그대로이며 LLM 엔드포인트만 같은 서버의 vLLM(`127.0.0.1:<vLLM 포트>`) 또는 `fabrix_proxy`(`plans/148` · 기본 포트 9095 — `fabrix_proxy/fabrix_proxy/config.py:46`)다.

| 항목 | 규칙 |
|---|---|
| venv | GPU 서버(리눅스 추정 — W5 키트에서 확정)에서 sre_agent venv를 새로 만든다. 맥 venv는 옮기지 않는다. holmesgpt 0.36.0 고정 · 휠은 외부망에서 대상 플랫폼용으로 받아 반입하거나 내부 미러를 쓴다(W5 키트에 두 경로) |
| 포트 | 벤치 포트 19180~19299는 vLLM(관례 8000)·`fabrix_proxy`(9095)·운영 sre_agent(9098)와 겹치지 않는다. 사전 점검이 사용 중 포트를 확인한다 |
| GPU 경합 | 같은 GPU를 다른 작업이 쓰면 지연 지표가 흔들린다. manifest에 실행 전후 GPU 사용 프로세스 목록을 남기고, 회차 중 `nvidia-smi` 사용률을 `gpu_samples.jsonl`로 샘플링한다(`host.gpu_sample_interval_s`). 보고서는 회차 평균·최대 사용률을 싣고, **지연 처분은 같은 GPU 조건(모델·장수·served model·`max_model_len`) 회차끼리만** 비교한다. 품질 지표(RC·ECR·HALLU)는 GPU 경합의 영향을 받지 않는 것으로 보고 그대로 쓴다(추론) |
| CPU 경합 | sre_agent 1인스턴스가 조사 1건씩 직렬로 돌아 CPU 부하는 작다(추론 — 1회차 manifest의 실측으로 확인) |

**LLM 백엔드**:

| 백엔드 | 위치 | 조건 · 규칙 |
|---|---|---|
| `vllm`(기본) | 같은 GPU 서버 | 기준선(1회차 A/A)과 A/B 회차의 기본 |
| `fabrix_proxy`(대체) | 별도 프로세스(FabriX 자격증명은 프록시에만 — D-274 전례 · 벤치는 FabriX 키를 모른다) | ① **`plans/148` 1단계 판정(G-4) 통과가 전제** — 통과 전에는 FabriX 회차를 돌리지 않는다. ② FabriX 회차는 **자기 기준선(FabriX A/A)을 따로** 잰다 — vLLM 기준선과 섞지 않는다. ③ 같은 회차에서 vLLM과 FabriX를 실험군으로 나란히 두지 않는다(§1.3). ④ 호출량: 조사 1건은 LLM 호출이 수~수십 회라 A/A 144건은 수천 회가 된다(148이 허용받은 규모 약 445회 순차와 다르다). 사전 점검이 예상 호출 수(세계 × k × 실험군 × vLLM 1회차 평균 step — 없으면 `MAX_STEPS`)를 출력하고, 사용자가 명세의 `worlds`·`k`로 규모를 정한다. ⑤ FabriX PII 필터 차단(400 `content_filter` — 148 런북)은 무효표(F-INFRA · 사유 `pii_block`)로 따로 센다. 세계 응답에는 실 IP·주민번호 형태 등 PII 규칙 패턴을 넣지 않는다(린트 경고 — 네트워크 세계는 예약 대역 `192.0.2.0/24` 등 문서용 주소만) |

- 처분(§6.2)은 **같은 백엔드·같은 회차의 대조군 대비**로만 내린다. vLLM에서 `채택 권고`가 난 개선을 FabriX로 쓸지는 FabriX 회차에서 따로 확인한다(필요 시).
- 보고서·반출 묶음 머리에 백엔드를 크게 적는다(외부망 검토가 섞어 읽지 않도록).

---

## 7. Wave

| Wave | 범위 | 산출 | 검증 |
|---|---|---|---|
| **W0** | 실측·기준 고정 | `sre_agent/.venv` 설치(holmesgpt 0.36.0) · holmes `LLMResult`·예외 경로에서 step 수·부분 도구 기록 회수 가능 여부(`inspect` 실측 → H-5 실현성) · MCP 서버 등록 시점(기동 1회 vs 조사마다) · 사전수집 결과가 지침에 어디까지 들어가는지(H-3 전제) · M-1~M-5를 실패 테스트로 고정 · `plans/150` W5 진행 상태 확인 | 신규 테스트만(실패 상태로 커밋하지 않고 `xfail(strict=True)`) |
| **W1** | 계측 보강(M-1~M-5) | 잡·`done` 감사에 가산 키 `incomplete` · `elapsed_s` · `prefetch_s` · `steps`(W0에서 얻을 수 있을 때) · `tool_calls_n` · `model` · `max_steps` · 도구 기록 요약 `tool_calls[{name, args_digest, status}]`(원시 출력 제외 — 크기·민감도) · 미완주·타임아웃의 토큰 보존(가능 범위) · 미완주는 `status` 유지 + `incomplete:true`(G-3) · 설정 `investigation_audit_path`(기본 None = 현행 `.data/investigation_audit.jsonl` · 비트 동일 — 벤치 감사 분리 §4.7) | §9.2 L2 — `test_plan151_w1_instrumentation.py` · 기존 `test_investigation_jobs` · `test_diagnosis_*` · `test_payload_contract` · noise_gate 소비자 5종(§9.2) |
| **W2** | 세계 서버 · 스키마 | `world_server.py` · `--schema-snapshot` · `tool_schemas.json` · 생성기 지시(`gen`) · 호출 로그 | 세계 서버 단위 테스트(라우팅·매처·기본 응답·인자 오류 판정) · 스냅샷 드리프트 테스트 |
| **W3** | 러너 · 채점 · 보고 · 사전 점검 · 반출 | `runner.py`(벤치 포트·env 오버라이드·submit/poll·반복·교차 순서) · `judge.py`(§4.4·§4.5) · `report.py`(오답 검토표 포함) · `preflight.py`(§6.4 — `--site closed` · tool-calling 왕복) · `export.py`(§6.5) · `--rejudge` | 채점기 단위 테스트(손으로 쓴 브리핑 픽스처 — 지표별 경계값) · 반출 가림·잔존 검사 테스트(심은 내부 주소·계정이 걸리는가) · **가짜 LLM 종단**(holmes 대신 고정 응답 — 실 프로세스 sre_agent + 세계 서버 + 러너가 raw.jsonl·report.md·묶음까지) |
| **W4** | 세계 24건 · 린트 | `worlds/dev/D01~D16` · `worlds/holdout/H01~H08`(D16·H08은 150 W5 뒤) · `lint.py` | 린트 통과 · 세계마다 「모범 답안 브리핑」 픽스처로 채점기가 PASS를 내는지(세계 자체 결함 점검) |
| **W5** | 외부망 실 LLM 확인 · 내부망 키트 | 로컬 MLX **대표 3세계 × 1회**(sre_agent `model`/`api_base`가 127.0.0.1 MLX인지 `--preflight`로 확인 뒤) — 계측 키·채점·묶음이 실 LLM 출력으로 끝까지 도는지만 본다(점수는 쓰지 않음) · 내부망 키트(GPU 서버용 sre_agent venv 재현 — 대상 플랫폼 휠 반입 또는 내부 미러 · 사내 vLLM·`fabrix_proxy` 명세 예시 · `--site closed` 실행 · GPU 샘플링 · 반출 절차 · `docs/26` 1절 · §6.7) | 3건 완주 여부와 무관하게 raw·scores·묶음 생성 · 묶음 잔존 검사 통과 |
| **W6** | 내부망 1회차 (사용자) | GPU 서버 · 사내 vLLM · 대조군 A/A(144회) → 묶음 반출 · `plans/91` 1-14 판정 입력 겸용 · (선택) FabriX 기준선은 148 G-4 통과 뒤 `closed_round1_fabrix.yaml`로 별도 회차 | 사전 점검 통과(tool-calling 포함) · 무효표율 ≤ 20% |
| **W7** | 외부망 검토 (Claude Code · 회차마다) | §6.6 — ① 채점 교정 → `--rejudge` ② 실패 원인 진단 · 개선점 장부 · 다음 회차 실험군 제안(사용자 확정) ③ 확정 후보를 플래그 1개씩 구현 · MLX 대표 1~2세계 × 1회 | ①은 재채점 전후 대조표 · ③은 후보별 자기 테스트 · 플래그 off 비트 동일 테스트 · `regress.py --no-tests` |
| **W8** | 내부망 N회차 (사용자 · 반복) | 대조군 + 실험군 → 반출 → W7에서 처분(§6.2). `채택 권고`는 D-324 부기 + 기본값 변경 제안(사용자 확정) | 처분표 · `판정 불가`면 사유(잡음폭·무효표)와 재측정 여부 |

의존: W0 → W1 → W3 · W2 → W3 · W3 → W4 → W5 → W6 → W7 ⇄ W8. W2와 W1은 병렬 가능. 계획 종료는 사용자가 정한다(후보 소진 또는 목표 도달) — 그때 모듈 단위 회귀 1회.

### 7.1 `plans/150`과의 순서 (G-6)

- W0~W4는 **신규 파일만** 만든다(W1 제외 — W1은 `diagnosis.py`·`investigation_dispatcher.py`·`investigation_jobs.py`를 건드린다. 150 W5의 변경 대상은 질문·지침·사전수집·브리핑이라 겹침이 적지만, W1 착수 전 150 작업 트리 상태를 확인하고 같은 파일이면 150 W5 랜딩 뒤로 미룬다).
- **기준선(W6 내부망 1회차)은 150 W5 랜딩 뒤에 잰다.** 앞에서 재면 150이 질문·지침을 바꾼 순간 기준선이 무효가 된다. 하네스(W0~W5)는 150과 병행한다.
- H-1·H-2·H-3·H-7은 150이 바꾼 코드 위에 얹는다.

---

## 8. 위험

| ID | 위험 | 대응 |
|---|---|---|
| R-1 | **합성 세계가 실제 장애보다 쉽다** — 벤치 점수가 운영 품질을 과대평가 | 유인·도구 오류·시간창 함정 세계를 3분의 1 이상 · 홀드아웃 · 운영 리플레이 평가는 `plans/101` F0이 같은 채점기로 이어받는다 |
| R-2 | 벤치 과적합 — 개선이 세계 문구에 맞춰진다. 외부망 검토가 홀드아웃 원문을 보면 더 커진다 | 홀드아웃 열람 규칙(§6.6 — 진단·코드는 개발셋 원문만) · 세계 고유 토큰 유입 린트 · 개발셋 점수는 처분 근거 아님 · 채점 교정이 홀드아웃 점수를 바꾸면 교정 내용 명시 |
| R-3 | 가짜 세계 서버 응답 모양이 실 `mcp_server`와 어긋남 | 스키마 스냅샷 드리프트 가드 · 응답 예시를 실 서버 테스트 픽스처에서 떠 옴 · 어긋남이 의심되면 후속으로 소수 세계를 실 서버로 교차 확인 |
| R-4 | MLX 9B 결과로 운영 판단 | MLX는 하네스·로직 확인용으로만 돌리고 점수를 처분에 쓰지 않는다(G-4) |
| R-5 | 결정적 채점의 별칭 누락 → 맞는 답을 오답 처리 | 세계마다 모범 답안 픽스처 · 오답 판정 표본을 사람이 읽는 「오답 검토표」를 보고서에 싣고 별칭을 보강 · `--rejudge`로 재채점 |
| R-6 | 표본이 작다(홀드아웃 8 × k=3) | A/A 잡음폭 · 잡음폭 안이면 `판정 불가` · 필요 시 k 확대(G-7) |
| R-7 | 실행 시간 — 실험군당 72회 | 내부망에서 야간 실행 · 실험군 병렬은 하지 않음(지연 오염) · 1회차 1회 소요 실측으로 회차당 실험군 수 결정 |
| R-8 | W1 가산 키가 noise_gate 소비자를 깬다 | 추가 키만 · 상태 어휘 불변(G-3) · 소비자 grep·테스트 |
| R-9 | **하네스 결함이 내부망 회차를 통째로 낭비**(회차 왕복이 비싸다) | W3 가짜 LLM 종단 · W5 MLX 대표 3세계 · 사전 점검(tool-calling 왕복 · 스키마 · 포트)이 실패하면 실행 전에 멈춤 |
| R-10 | 사내 vLLM이 tool-calling을 지원하지 않거나 모델 설정이 다름 | 사전 점검 왕복 1회 · 실패 시 회차 무효 처리하고 `plans/91` 1-14로 보고 |
| R-12 | GPU 서버를 다른 작업과 나눠 써 지연 지표가 흔들림 | 실행 전후 GPU 프로세스 목록 · 회차 중 사용률 샘플 · 지연 처분은 같은 GPU 조건 회차끼리만(§6.7) |
| R-13 | FabriX 회차 결과가 vLLM 결과와 섞여 읽힘 · 프록시 에뮬레이션 실패가 모델 실패로 보임 | 백엔드 표기(manifest·보고서 머리) · FabriX 자기 기준선 · 교차 비교 금지 · 148 G-4 통과 전 FabriX 회차 금지 · 프록시 오류·PII 차단은 무효표 사유로 분리 |
| R-14 | FabriX 회차 호출량이 수천 회 | 사전 점검의 예상 호출 수 출력 · 명세 `worlds`·`k`로 축소 · 사용자가 규모 확정 |
| R-11 | 반출 묶음에 내부 주소·계정이 섞임 | 가림 + 잔존 검사 관문(걸리면 묶음 미생성) · 가림 테스트에 심은 값 |

---

## 9. 테스트 방법 · 회귀 · 매뉴얼

### 9.1 테스트 계층

벤치는 「측정 도구」라 도구 자체가 틀리면 내부망 회차 하나가 통째로 버려진다(R-9). 그래서 실 LLM 앞에 다섯 층을 두고, 위 층이 통과해야 아래 층으로 간다.

| 층 | 무엇을 | 어디서 · 무엇으로 | 실 LLM | 언제 |
|---|---|---|---|---|
| L0 정적 | 계층·경계·린트·타입 | `sre_agent/scripts/arch_check.py --ci` · `tests/test_boundary.py` · `inv_bench --lint` · `regress.py --no-tests --files …`(ruff·mypy) | 0 | 매 Wave |
| L1 단위 | 채점기·세계 서버 매처·반출 가림·사전 점검·러너 조립 | pytest · 손으로 쓴 픽스처(`tests/fixtures/investigation_bench/`) | 0 | W2·W3 |
| L2 계약 | W1 가산 키 · noise_gate 소비자 무시 · 스키마 드리프트 | pytest(sre_agent · noise_gate 양쪽) | 0 | W1·W2 |
| L3 프로세스 종단 | 러너 → 실 sre_agent 프로세스 → 세계 서버 → **가짜 LLM** → raw·scores·report·묶음 | pytest(실 프로세스 · 임의 포트) | 0 | W3 |
| L4 세계 자기검증 | 세계 24건 각각: 모범 대본 → PASS · 음성 대본 → 기대 실패 코드 | pytest 매개변수화(L3 장치 재사용) | 0 | W4 |
| L5 실 LLM 확인 | 실 holmes + 로컬 MLX로 끝까지 도는가(점수 안 씀) | `inv_bench --spec local_smoke.yaml` | MLX 3회 | W5 · W7 개선마다 1~2회 |
| L6 측정 | 대조군·실험군 처분 | `inv_bench --site closed --spec closed_round*.yaml` | 사내 vLLM | W6 · W8(사용자) |

- 기본은 가짜 LLM과 실 프로세스 종단이다(CLAUDE.md 「MLX 검증은 최소」). L3·L4는 holmes를 진짜로 돌리고 LLM만 가짜이므로 litellm 요청 모양·도구 등록·사전수집·브리핑 조립까지 실물로 지나간다.
- L3·L4 테스트는 포트를 `0`(임의 할당)으로 잡아 병렬로 돌아도 안전하게 한다(sre_agent pyproject에 `serial` 마커가 없다).
- 실 LLM이 필요한 테스트는 pytest에 넣지 않는다 — L5는 CLI로만 돈다(기존 `test_investigation_e2e.py`의 `RUN_E2E` 게이트와 섞지 않는다).

### 9.2 테스트 파일과 핵심 케이스

모두 `sre_agent/tests/` · 실행은 `cd sre_agent && .venv/bin/python -m pytest tests/<파일>`.

| 파일 | Wave | 핵심 케이스 |
|---|---|---|
| `test_plan151_w1_instrumentation.py` | W1 | step 상한 미완주 → `status=done` 유지 + `incomplete:true` · 정상 완주 → `incomplete:false` · 타임아웃 잡에도 `elapsed_s`·`model`·`max_steps` 키 · `done` 감사 이벤트에 가산 키 전부 · `tool_calls[]`에 원시 도구 출력이 없다(이름·인자 요약·상태만) · `investigation_audit_path` 미설정 = 기존 경로(비트 동일) · 설정 시 그 경로에만 쓴다 |
| noise_gate 소비자 5종(기존) | W1 | `test_investigation_followup` · `test_investigation_trigger` · `test_investigation_target_state` · `test_plan150_w2_investigation_store` · `test_plan150_w4_episode_investigation` — 가산 키가 있는 응답을 줘도 통과(필요하면 픽스처에 키만 더한다 · 단언 변경 없음) |
| `test_bench_world_server.py` | W2 | 스냅샷의 도구 이름·인자 스키마가 그대로 등록된다 · 매처 첫 일치 우선 · 부분집합 일치 · `default` · 정의 없는 도구 = 빈 결과 · 현재 세계 밖 호스트 → 「대상 없음」 + `arg_error=host` · 사건 구간을 안 덮는 시간창 → 빈 결과 + `arg_error=time_window` · `gen` 같은 인자 = 같은 응답(두 번 호출 대조) · 오류 주입 모양 · 제어 엔드포인트가 루프백에만 바인딩 · 호출 로그 한 줄 필드 |
| `test_bench_schema_drift.py` | W2 | 세계 YAML이 스냅샷에 없는 도구·인자를 쓰면 린트 실패 · 스냅샷 해시가 manifest에 실린다 · 실 서버가 떠 있지 않으면 대조를 건너뛰고 「미대조」를 manifest에 남긴다 |
| `test_bench_judge.py` | W3 | RC@1: entity와 kind 둘 다 맞아야 적중(하나만이면 부분 점수 기록) · 별칭·대소문자·공백·한영 혼용 정규화 · RC@3 순위 경계 · ECR: `cite` 토큰이 브리핑에 있어도 **세계 서버가 그 응답을 내준 적 없으면 불인정** · HALLU: 숫자+단위 추출 · 반올림(98.4 ↔ 98) · 단위 환산(MB↔GB) · 알람 이벤트 자체의 값은 허용 · 시각·호스트명 속 숫자는 수치 주장이 아님 · DIST · ABST(무장애에 단정 / 근거 충분한데 불충분) · 실패 유형 우선순위(F-INFRA > F-TMO > … > F-CITE) · 무효표는 분모 제외 · Maj@k · **재채점 멱등**(같은 입력 두 번 = 같은 `scores.jsonl`) |
| `test_bench_preflight.py` | W3 | `openai/…` + 127.0.0.1 허용 · 사설 주소는 `--site closed`일 때만 허용 · `gemini/…`·`anthropic/…`·공용 API 도메인 거부(exit 2) · `--site closed`의 127.0.0.1은 허용하되 명세 `backend`(vllm·fabrix_proxy)로 기록되고 `--site local`의 127.0.0.1만 MLX로 기록 · 명세에 `backend`가 없으면 거부 · `fabrix_proxy`면 예상 호출 수 출력 · 벤치 포트가 사용 중이면 중단 · `nvidia-smi`가 없으면 GPU 칸을 「미수집」으로 남기고 진행 · 가짜 LLM 포트는 `--test` 없이 거부 · tool-calling 왕복 실패 → 실행 전 중단 · `--allow-billed` 승인 문구가 manifest에 남는다 · 판정이 pydantic 필드로만 이뤄진다(`os.getenv` 미사용 — 소스 grep 단언) |
| `test_bench_runner.py` | W3 | 실험군 env 조립(§4.7 표 전 키) · Gemini 키 두 별칭 공란 · cwd가 빈 run 디렉터리 · 교차 순서(세계마다 실험군 순서 회전) · 실행마다 fingerprint 고유 · 폴링 시간 초과 → 무효표 F-INFRA · `rejected`·`stub` → 무효표 · 설정 에코가 없는 실험군은 판정 제외 표기 · 끝나면 자기 PID만 종료 · `--cleanup`은 `pids.json`의 PID만 종료(다른 프로세스 무관) · `--resume`은 끝난 `arm × world × trial`을 건너뛰고 명세·커밋이 다르면 거부 · `--site`가 명세와 다르면 거부 · `--dry-run`은 프로세스를 띄우지 않는다 |
| `test_bench_export.py` | W3 | 심어 둔 `api_base` 호스트·OS 계정·사설 IP·홈 경로·GPU 서버 호스트명·`nvidia-smi` 프로세스 목록의 사용자명이 전부 가려진다 · 하나라도 남으면 묶음 미생성(exit ≠ 0) · 세계 YAML은 묶음에 없고 커밋 해시만 · 브리핑 전문은 남는다(가림이 과하지 않은가) |
| `test_bench_e2e_fake_llm.py` | W3 | 실 프로세스: 세계 서버 + 가짜 LLM + sre_agent 2실험군 × 세계 2개 × k=1 → `manifest`·`world_calls`·`raw`·`scores`·`report`·묶음 생성 · raw의 계측 키가 W1 값과 일치 · 운영 감사 파일(`.data/investigation_audit.jsonl`)이 바뀌지 않는다 · 9097·9098·9099 포트를 열지 않는다 |
| `test_bench_worlds_selfcheck.py` | W4 | 세계마다 `script.model` → PASS · `script.neg.<코드>` → 그 코드 · 린트 전건 통과 · 홀드아웃·개발셋 문구 중복 0 |

### 9.3 실행 명령 (정본)

> 이 절이 명령의 정본이다. `inv_bench.py`의 옵션은 **W3에서 이 모양으로 구현하는 CLI 계약**이다(지금은 코드 0). 구현 중 옵션이 바뀌면 이 절을 같은 커밋에서 고친다. 블록 머리의 `[위치 · CWD · venv]`를 지킨다 — sre_agent 명령은 **`sre_agent/` 안에서 `sre_agent/.venv`로**(루트 cwd 금지 · 2026-09-10 실수 이력), 본체·noise_gate·mcp_server는 **레포 루트 `.venv`로** 돈다. 이 작업 트리(`collectorinfra_second`)에는 루트 `.venv`도 `sre_agent/.venv`도 없다(2026-10-13 실측) — W0에서 sre_agent venv를 만들고, 루트 venv는 `python3.13 -m venv .venv && .venv/bin/pip install -e ".[dev,document]"`로 만들거나 기존 체크아웃의 venv 경로로 바꿔 쓴다.

#### 9.3.0 `inv_bench.py` 옵션 요약

| 옵션 | 하는 일 | 실 LLM |
|---|---|---|
| `--schema-snapshot --polestar-url U [--apm-url U]` | 실 서버 `list_tools` → `testdata/investigation_bench/tool_schemas.json` | 0 |
| `--lint` | 세계 YAML 전건 검사(§4.6 린트) | 0 |
| `--show-env --spec S` | 실험군별로 조립될 sre_agent 환경변수 출력(키 값은 가림) | 0 |
| `--preflight --spec S [--site closed]` | §6.4 사전 점검만 · 통과 = exit 0 | tool-calling 왕복 1회 |
| `--run --spec S [--site closed] [--dry-run] [--test]` | 사전 점검 → 프로세스 기동 → 실행 → 채점 → 보고서. `--dry-run`은 실행 계획(세계 × 실험군 × k · 포트 · 예상 호출 수)만 출력 · `--test`는 가짜 LLM 허용(테스트 전용) | 있음 |
| `--resume <run_dir>` | 끊긴 실행을 이어서(끝난 `arm × world × trial`은 건너뜀 · manifest의 명세·커밋이 같을 때만) | 있음 |
| `--status <run_dir>` | 진행 수 · 무효표 수 · 경과 시간 | 0 |
| `--cleanup <run_dir>` | `<run>/pids.json`에 적힌 자기 프로세스만 종료(실행이 비정상 종료됐을 때) | 0 |
| `--report <run_dir 또는 묶음>` | `report.md` 다시 만들기 | 0 |
| `--rejudge <run_dir 또는 묶음>` | 현재 세계 YAML·채점기로 재채점(이전 판 보존 · §4.9) | 0 |
| `--export <run_dir>` | 반출 묶음 `<run_dir>.bundle/` 생성(가림 + 잔존 검사 · §6.5) | 0 |

- (W3 구현) `--out-root <dir>` — run 디렉터리 루트(기본 `sre_agent/.data/investigation_bench`). `--allow-billed`는 빈 문구를 거부한다. 모드 옵션은 상호 배타다.
- `--site`는 기본 `local`이다. 명세의 `run.site`와 CLI `--site`가 **둘 다 `closed`일 때만** 사설 주소 LLM을 허용한다(이중 확인).
- 명세의 `llm.backend`: `fake`(테스트) · `mlx`(외부망) · `vllm`·`fabrix_proxy`(내부망). `fake`는 `--test` 없이는 거부한다.

#### 9.3.1 외부망 개발 맥 — 준비 (W0)

```bash
# [개발 맥 · CWD=sre_agent · 새 venv]
cd sre_agent
python3.13 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pip install "holmesgpt==0.36.0"         # 필수 — pyproject 제약이 >=0.36.0이라 그냥 설치하면 0.43.0·mcp 1.28.1이 깔린다(W0 실측). 고정하면 mcp 1.25.0
.venv/bin/pip show holmesgpt | head -2            # → Version: 0.36.0
.venv/bin/python -c "import holmes, litellm, mcp; print('ok')"
```

#### 9.3.2 테스트 — Wave 중 (자기 테스트 + 정적 게이트)

```bash
# [개발 맥 · CWD=sre_agent · sre_agent/.venv] — 자기가 쓰거나 바꾼 파일만
.venv/bin/python -m pytest tests/test_plan151_w1_instrumentation.py -q            # W1
.venv/bin/python -m pytest tests/test_bench_world_server.py tests/test_bench_schema_drift.py -q   # W2
.venv/bin/python -m pytest tests/test_bench_judge.py tests/test_bench_preflight.py \
    tests/test_bench_runner.py tests/test_bench_export.py tests/test_bench_e2e_fake_llm.py -q   # W3
.venv/bin/python -m pytest tests/test_bench_worlds_selfcheck.py -q                  # W4
.venv/bin/python scripts/arch_check.py --ci
.venv/bin/python -m pytest tests/test_boundary.py -q

# [개발 맥 · CWD=레포 루트 · 루트 .venv] — W1의 noise_gate 소비자 5종
.venv/bin/python -m pytest noise_gate/tests/test_investigation_followup.py \
    noise_gate/tests/test_investigation_trigger.py noise_gate/tests/test_investigation_target_state.py \
    noise_gate/tests/test_plan150_w2_investigation_store.py \
    noise_gate/tests/test_plan150_w4_episode_investigation.py -q

# [개발 맥 · CWD=레포 루트] — 정적 게이트(ruff·mypy·arch·overfit)
python scripts/regress.py --no-tests --files <이 Wave가 바꾼 파일…>
python scripts/overfit_check.py --ci
```

계획 끝 1회(모듈 단위 회귀 — team-lead):

```bash
# [개발 맥 · CWD=레포 루트]
python scripts/regress.py --base f4a95f52971e0227cfb761a258e1b9d010f97950 --files <회귀 대상 파일…>
cd sre_agent && .venv/bin/python -m pytest tests -q        # sre_agent는 regress 수집 밖이라 따로
```

#### 9.3.3 도구 스키마 스냅샷 (W2 · 실 서버 기동 · 실 LLM 0)

```bash
# 터미널 1 [개발 맥 · CWD=레포 루트 · 루트 .venv] — mcp_server 조사 프로파일을 벤치 전용 포트로
#   W2 실측: list_tools에 DB·Prometheus 접속은 필요 없다(풀 초기화 실패는 삼켜지고 list_sources는 [] 성공) — docker 불필요.
#   prometheus_client가 없는 인터프리터면 mcp_server가 import 단계에서 죽는다(루트 pyproject 의존성 — 루트 venv 재현 시 확인).
GEMINI_API_KEY= LLM_GEMINI_API_KEY= \
EXPOSE_EXECUTE_SQL=false EXPOSE_RAW_PROMQL=false EXPOSE_POLESTAR_TOOLS=true \
SERVER_HOST=127.0.0.1 SERVER_PORT=19197 PYTHONPATH="$PWD/mcp_server" .venv/bin/python -m mcp_server   # 기본 바인딩 0.0.0.0이라 명시

# 터미널 2 [개발 맥 · CWD=apm_gateway · 루트 .venv] — apm_* 도구(W2 실측: 제니퍼 자격증명 없이 list_tools 가능 · gateway_health=not_configured 성공 · 기본 127.0.0.1)
cd apm_gateway && APM_GATEWAY_PORT=19196 ../.venv/bin/python -m apm_gateway

# 터미널 3 [개발 맥 · CWD=sre_agent · sre_agent/.venv]
.venv/bin/python scripts/inv_bench.py --schema-snapshot \
    --polestar-url http://127.0.0.1:19197/sse --apm-url http://127.0.0.1:19196/sse
git diff --stat testdata/investigation_bench/tool_schemas.json     # 바뀐 도구만 확인 후 커밋
# 끝나면 터미널 1·2를 Ctrl-C (자기 프로세스만)
```

#### 9.3.4 세계 린트 · 가짜 LLM 종단 (W3·W4 · 실 LLM 0)

```bash
# [개발 맥 · CWD=sre_agent · sre_agent/.venv]
.venv/bin/python scripts/inv_bench.py --lint
.venv/bin/python scripts/inv_bench.py --run --spec testdata/investigation_bench/specs/fake_e2e.yaml --test --dry-run
.venv/bin/python scripts/inv_bench.py --run --spec testdata/investigation_bench/specs/fake_e2e.yaml --test
#   → .data/investigation_bench/<run_id>/ 에 manifest·world_calls·raw·scores·report 생성
.venv/bin/python scripts/inv_bench.py --export .data/investigation_bench/<run_id>
```

`fake_e2e.yaml`은 `backend: fake` · 세계 2개 · 실험군 2개 · k=1이다(L3 테스트와 같은 구성을 손으로 돌려 보는 용도).

#### 9.3.5 외부망 로컬 MLX 확인 (W5 · W7 개선 확인 · 실 LLM = MLX만)

```bash
# 터미널 1 [개발 맥 · CWD=레포 루트] — 캐시 모델만 · 127.0.0.1
MLX_PORT=8080 scripts/mlx_server.sh

# 터미널 2 [개발 맥 · CWD=sre_agent · sre_agent/.venv]
S=testdata/investigation_bench/specs/local_smoke.yaml        # D01·D03·D09 × k=1 · 대조군 · backend: mlx
#   llm.model = openai/<mlx_server.sh가 띄운 --model>(W5 실측: openai/mlx-community/Qwen3.5-9B-OptiQ-4bit). /v1/models는 로컬 캐시의
#   모델을 전부 나열하므로 served name은 거기서 고르지 않고 `ps`의 --model 인자로 확인한다(다른 이름이면 요청마다 재적재)
.venv/bin/python scripts/inv_bench.py --show-env --spec $S   # MODEL=openai/… · API_BASE=127.0.0.1:8080 · GEMINI 키 공란 확인
.venv/bin/python scripts/inv_bench.py --preflight --spec $S  # exit 0이어야 다음 줄(D-127 — 두 조건 충족 시 승인 없이 실행)
.venv/bin/python scripts/inv_bench.py --run --spec $S        # 약 10~12분(1회 ≈ 3분 · plans/91 1-17 · W5 실측 11.7분 — 완주 93초 + 타임아웃 300초 × 2)
.venv/bin/python scripts/inv_bench.py --export .data/investigation_bench/<run_id>
# 끝나면 터미널 1 Ctrl-C (MLX 서버 — 자기 PID만)
```

W7 개선 확인은 같은 명령에 `local_smoke.yaml` 대신 개선 실험군 1개와 대표 1~2세계를 적은 명세를 쓴다.

#### 9.3.6 내부망 키트 만들기 (W5 · 외부망에서)

```bash
# [개발 맥 · CWD=sre_agent · sre_agent/.venv] — GPU 서버(리눅스 x86_64 · Python 3.13 가정 — 다르면 플랫폼 인자 교체)
mkdir -p ../dist/inv_bench_kit
.venv/bin/pip freeze --exclude-editable > ../dist/inv_bench_kit/requirements.lock
.venv/bin/pip download -r ../dist/inv_bench_kit/requirements.lock hatchling \
    --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 --python-version 3.13 --only-binary=:all: \
    -d ../dist/inv_bench_kit/wheels
#   W5 실측(2026-10-13): manylinux2014 태그만 주면 confluent-kafka·jq·pymssql·pyroaring·tiktoken이 실패한다 → manylinux_2_28 태그를
#   함께 줘야 받힌다(GPU 서버 glibc >= 2.28 필요). clickhouse-sqlalchemy==0.3.2는 바이너리 휠이 없어 sdist를 받아
#   `pip wheel --no-deps <sdist> -w ../dist/inv_bench_kit/wheels`로 순수 파이썬 휠(py3-none-any)을 만들어 넣는다.
#   pip는 첫 실패에서 멈추므로 실패 목록을 보려면 패키지마다 `--no-deps`로 받는다. 결과: 휠 197개(hatchling 빌드 의존성 포함) · 약 140MB.
git -C .. rev-parse HEAD > ../dist/inv_bench_kit/COMMIT
```

키트 = 저장소 커밋(내부망 반입 경로는 기존 관례) + `dist/inv_bench_kit/`(휠 · lock · 커밋 해시). `dist/`는 gitignore 대상이다(`.gitignore:14`).

#### 9.3.7 내부망 GPU 서버 — 회차 실행 (W6 · W8 · 사용자)

```bash
# [GPU 서버 · CWD=sre_agent] — 1회만: venv 재현
cd <저장소>/sre_agent
python3.13 -m venv .venv
.venv/bin/pip install --no-index --find-links <키트>/wheels -r <키트>/requirements.lock
.venv/bin/pip install --no-index --find-links <키트>/wheels --no-deps -e .
.venv/bin/python -c "import holmes, litellm, mcp; print('ok')"

# [GPU 서버] — 매 회차: LLM과 GPU 상태 확인
nvidia-smi                                                    # 다른 작업이 GPU를 쓰는지
curl -s --noproxy '*' http://127.0.0.1:8000/v1/models         # vLLM served name · max_model_len

# [GPU 서버 · CWD=sre_agent · sre_agent/.venv] — 명세 준비(저장소 밖 작업 폴더에 복사해 고친다)
mkdir -p ~/inv_bench && cp testdata/investigation_bench/specs/closed_round1.yaml ~/inv_bench/round1.yaml
#   고칠 칸: run.site=closed · llm.backend=vllm · llm.model=openai/<served name> · llm.api_base=http://127.0.0.1:8000/v1
#            base_env = 운영 실효값(MAX_STEPS · INVESTIGATION_TIMEOUT_SECONDS · OVERRIDE_MAX_CONTENT_SIZE · OVERRIDE_MAX_OUTPUT_TOKEN)
S=~/inv_bench/round1.yaml
.venv/bin/python scripts/inv_bench.py --site closed --show-env  --spec $S
.venv/bin/python scripts/inv_bench.py --site closed --preflight --spec $S     # 실패면 멈추고 plans/91 1-14로 보고
.venv/bin/python scripts/inv_bench.py --site closed --run --spec $S --dry-run # 144회 · 포트 · 순서 확인
nohup .venv/bin/python scripts/inv_bench.py --site closed --run --spec $S > ~/inv_bench/round1.log 2>&1 &

# 진행 확인 · 중단 복구
.venv/bin/python scripts/inv_bench.py --status .data/investigation_bench/<run_id>
.venv/bin/python scripts/inv_bench.py --cleanup .data/investigation_bench/<run_id>    # 비정상 종료 뒤 남은 자기 프로세스 정리
.venv/bin/python scripts/inv_bench.py --site closed --resume .data/investigation_bench/<run_id>

# 끝나면: 무효표율(report.md 머리) ≤ 20% 확인 → 반출 묶음
.venv/bin/python scripts/inv_bench.py --export .data/investigation_bench/<run_id>
#   → .data/investigation_bench/<run_id>.bundle/ 만 외부망으로 가져간다(잔존 검사 실패면 생성되지 않는다)
```

**FabriX 회차**(대체 백엔드 · `plans/148` G-4 통과 뒤만 · §6.7):

```bash
# [GPU 서버] — 프록시 기동은 plans/148 런북(fabrix_proxy/POC_RUNBOOK.md 2~4절)의 REPO·PY·RUN_DIR·.env/.encenv를 그대로 따른다
cd "$REPO/fabrix_proxy" && nohup "$PY" -m fabrix_proxy > "$RUN_DIR/proxy.log" 2>&1 &
curl -s --noproxy '*' http://127.0.0.1:9095/v1/models

# [GPU 서버 · CWD=sre_agent · sre_agent/.venv]
cp testdata/investigation_bench/specs/closed_round1_fabrix.yaml ~/inv_bench/round1_fabrix.yaml
#   고칠 칸: llm.backend=fabrix_proxy · llm.api_base=http://127.0.0.1:9095/v1 · llm.model=openai/<프록시 /v1/models 이름>
S=~/inv_bench/round1_fabrix.yaml
.venv/bin/python scripts/inv_bench.py --site closed --preflight --spec $S     # 예상 FabriX 호출 수 출력 → 규모 확정(worlds·k로 축소)
nohup .venv/bin/python scripts/inv_bench.py --site closed --run --spec $S > ~/inv_bench/round1_fabrix.log 2>&1 &
```

#### 9.3.8 외부망 검토 (W7 · Claude Code)

```bash
# [개발 맥 · CWD=sre_agent · sre_agent/.venv] — 반입한 묶음 위치
B=.data/investigation_bench/imported/<run_id>.bundle
.venv/bin/python scripts/inv_bench.py --report $B          # 보고서 다시 보기(변경 없음)
# ① 채점 교정을 커밋한 뒤
.venv/bin/python scripts/inv_bench.py --rejudge $B         # scores 재작성 · 이전 판은 scores.<judge_version>.jsonl
```

Claude Code 세션에 주는 요청 예(§6.6 · 홀드아웃 열람 규칙 포함):

```text
plans/151 §6.6 절차로 sre_agent/.data/investigation_bench/imported/<run_id>.bundle 을 검토하라.
① 채점 교정안(홀드아웃 포함) → 커밋 후 --rejudge 전후 대조표
② 실패 원인 진단·개선 후보 — 개발셋 원문만 읽는다(홀드아웃은 집계만)
③ 사용자가 확정한 후보만 플래그 1개로 구현 · MLX 대표 1~2세계 × 1회
```

다음 회차 명세는 `closed_roundN.template.yaml`을 복사해 `arms:`에 확정된 실험군 플래그를 적고, 9.3.7을 같은 순서로 반복한다.

### 9.4 Wave별 완료 기준

| Wave | 완료 = 아래가 모두 참 |
|---|---|
| W0 | 실측 결과가 계획서 §2·§4.7·§4.11에 반영(holmes step 수 회수 가능 여부 · MCP 등록 시점 · litellm 요청 모양 덤프 · 사전수집/도구 호출 구분 방법) · M-1~M-5 `xfail(strict=True)` 고정 |
| W1 | `test_plan151_w1_instrumentation.py` 통과 · W0 xfail이 통과로 바뀜(strict라 자동 검출) · noise_gate 소비자 5종 통과 · `regress.py --no-tests` 통과 |
| W2 | `test_bench_world_server.py`·`test_bench_schema_drift.py` 통과 · `tool_schemas.json` 커밋 |
| W3 | 9.2의 W3 행 전부 통과 · `test_bench_e2e_fake_llm.py`가 2분 안에 끝남 |
| W4 | 세계 24건(D16·H08은 150 W5 뒤) 린트 통과 · 자기검증 전건 통과 |
| W5 | MLX 3건 raw·scores 생성 · 묶음 잔존 검사 통과 · 내부망 키트·`docs/26` 절 작성 |
| W6~W8 | §7 표의 검증 칸 |

### 9.5 회귀 · 게이트

- 회귀(D-303): Wave 중간은 자기 테스트 + `python scripts/regress.py --no-tests --files …`. 계획 끝 1회 `python scripts/regress.py --base f4a95f5 --files <회귀 대상>`. sre_agent는 **`cd sre_agent && .venv/bin/python -m pytest tests`**(루트 cwd 금지 — 2026-09-10 실수 이력). 전체 회귀는 사용자 요청 시만.
- 품질 게이트: `sre_agent/scripts/arch_check.py --ci`(벤치 스크립트는 `scripts/`라 계층 검사 밖 · 세계 서버는 `sre_agent.*` import 0) · 경계 테스트 `sre_agent/tests/test_boundary.py`(벤치가 `mcp_server`·`src` import 0) · `overfit_check --ci`(개선 Wave가 `sre_agent/sre_agent/domain`에 폴스타 스키마 리터럴을 넣지 않는지).
- 실 LLM(외부망): W4까지 0회(가짜 LLM). W5는 로컬 MLX 대표 3세계 × 1회, W7 개선 코드는 바뀐 경로 대표 1~2세계 × 1회 · 모두 `--preflight` 통과 뒤. 측정(W6·W8)은 내부망 사내 vLLM에서 사용자가 돌린다.

### 9.6 매뉴얼 · 가이드

- **매뉴얼(D-255)**: 대상 아님(내부 벤치·계측). W8에서 기본값이 바뀌어 사용자에게 보이는 브리핑 모양이 달라지면(H-4 `limitations` 표기 · H-5 부분 브리핑) 그 Wave에서 관리자 매뉴얼의 조사 브리핑 읽는 법을 함께 고친다.
- 가이드: `docs/26_sre_agent_guide.md`에 벤치 실행·내부망 회차·반출 절차 1절(W5) — 내용은 §4.7·§9.3을 옮긴다.

---

## 10. 게이트 — 사용자 결정

| ID | 질문 | 선택지 | 권고 |
|---|---|---|---|
| **G-1** | 장애 세계 구현 | (a) 가짜 세계 MCP 서버(도구 응답 합성) (b) 샌드박스 PG에 장애 픽스처 적재 + 실 `mcp_server` (c) (a) 기본 + 소수 세계를 (b)로 교차 확인 | **(a)** — 정답·응답을 세계마다 통제 · 운영 반출 문제 없음 · 현 샌드박스 메트릭은 더미라 (b)는 픽스처 신규 작성 비용이 크다. (c)는 W6 이후 필요해지면 |
| **G-2** | 계측 키(W1) 추가 방식 | (a) 잡·감사에 가산 키 상시(동작 무변경) (b) 플래그 뒤 | **(a)** — 동작이 아니라 기록이고, 플래그로 숨기면 운영 감사에서 미완주를 영영 못 본다 |
| **G-3** | 미완주 표기 | (a) `status`는 `done` 유지 + `incomplete:true` 가산 (b) 새 상태 `incomplete` | **(a)** — D-123 상태 어휘와 noise_gate 소비자(후속 통보·150 조사 저장소)를 건드리지 않는다 |
| **G-4** | 판정 모델 | (a) 개발 루프 = 로컬 MLX · 기본값 변경 = 폐쇄망 운영 모델 재확인 뒤 (b) MLX 결과만으로 기본값 변경 (c) Gemini 병행(과금 · 건별 승인) | **(a)** |
| **G-5** | 채점 | (a) 결정적 채점만 (b) + 로컬 MLX LLM 심판을 참고 지표로 | **(a)** — 별칭 누락은 오답 검토표로 메운다. (b)는 심판 자체의 신뢰도를 또 재야 한다 |
| **G-6** | `plans/150`과의 순서 | (a) W0~W4 즉시 병행 · 기준선(W5)·개선(W6)은 150 W5 랜딩 뒤 (b) 150 완료 뒤 전체 착수 | **(a)** |
| **G-7** | 규모 | (a) 개발 16 + 홀드아웃 8 · k=3 (b) k=5 (c) 세계 40건 | **(a)** — A/A 잡음폭이 크면 그때 (b) |
| **G-8** | 채택 규칙 | (a) §6.2 — 홀드아웃 · A/A 잡음폭 · 가드 지표 · D-238 처분 어휘 (b) 개발+홀드아웃 합산 | **(a)** |

**확정(2026-10-12 · 사용자 인터뷰)**:

| ID | 답 | 계획 반영 |
|---|---|---|
| G-1 | (a) 가짜 세계 MCP 서버 | 그대로 |
| G-2·G-3 | (a) 상시 가산 · `status` 유지 + `incomplete:true` | W1 그대로 |
| G-4 | (a) — 측정 모델 = **내부망 사내 vLLM**(FabriX 제외) → **v1.3 개정(2026-10-13)**: 측정은 **GPU 서버 동거**(sre_agent·벤치를 vLLM 서버에) · 백엔드는 vLLM 기본 · **FabriX(`fabrix_proxy`)는 대체 백엔드**(148 G-4 통과 전제 · 자기 기준선 · 교차 비교 금지) | §1.3 · §6.7 · W5·W6 · R-12~R-14 |
| G-5 | (a) 결정적 채점 **+ 결과 로그를 남겨 외부망 Claude Code가 검토** — 산출: 채점 교정안 · 실패 원인 진단·개선 후보 · 개선 코드(스킬화는 하지 않음) | §6.5 반출 묶음 · §6.6 검토 · W7 |
| G-6 | (a) 하네스 병행 · 측정은 150 W5 뒤 | §7.1 |
| G-7 | (a) 16 + 8 · k=3 — 단 **MLX는 느리니 최소화하고 대부분 내부망에서** → 외부망 MLX는 대표 3세계 × 1회 | §6.1 · W5 |
| G-8 | (a) | §6.2 |
| (추가) 반출 범위 | 브리핑 전문 · 도구 호출·응답 로그 포함 · 내부 주소·계정·경로만 가림 | §6.5 |

---

## 11. 의사결정 정합 · 신규 결정 예약

- **충돌 없음(사용자 확인이 필요한 충돌 0)**. 확인한 결정: D-035(채점·분류는 결정적, LLM은 조사 서술만 — 정합) · D-120(Gemini 데이터 통제 — 합성 세계라 자동 충족, 과금 승인은 별도) · D-123(상태 어휘 — G-3 (a)로 불변) · D-127·D-240(과금 차단 — §6.4) · D-139(import 0 — 스키마는 MCP 프로토콜로) · D-219·D-238(판정 불가·처분 어휘 준용).
- **D-324 본문 등재**(2026-10-12) — 「sre_agent 조사 품질 벤치마크 계약」: ①정답 = 구성으로 아는 합성 세계(가짜 MCP 서버) · 누수 금지 ②실제 조사 경로(submit/poll) ③도구 스키마 = 실 서버 스냅샷 ④결정적 채점·실패 유형 + 외부망 Claude Code 검토(교정·진단·코드) ⑤개발/홀드아웃 분리 · 홀드아웃으로만 처분 · 검토의 홀드아웃 열람 제한 ⑥측정 = 내부망 GPU 서버(사내 vLLM 기본 · FabriX `fabrix_proxy` 대체 — 2026-10-13 부기) · 외부망 MLX 최소 · 과금 평면 차단 ⑦계측 가산 키 상시 · 미완주 `incomplete:true` ⑧반출 = 전문 + 내부 주소·계정·경로 가림.
- `plans/101` 부기 예정: 라벨 스키마·채점기를 이 벤치와 공유(F0 리플레이 평가가 `judge.py` 재사용).

---

## 12. 변경 이력

| 날짜 | 내용 |
|---|---|
| 2026-10-12 | **v1.0 신규(`-TODO`)** — 현행 실측(조사 경로 · 설정 축 · 계측 빈칸 M-1~M-5 · 평가 자산 · 원인 정답 0건) · 합성 장애 세계 벤치 설계(세계 서버 · 스키마 드리프트 가드 · 결정적 채점 8지표 · 실패 유형 12종) · 세계 24건 초안 · 개선 후보 H-1~H-8 · 판정 규칙(A/A 잡음폭 · 홀드아웃 · D-238) · Wave W0~W7 · `plans/150` 순서 조율 · 게이트 G-1~G-8 · D-324 예약 · 코드 0 |
| 2026-10-12 | **v1.1** — 사용자 인터뷰로 G-1~G-8 확정(§10 확정표). 바뀐 것: 측정을 내부망 사내 vLLM 회차로 옮김(W6 1회차 A/A · W8 N회차) · 외부망 Claude Code 검토 W7 신설(채점 교정 → `--rejudge` · 실패 원인 진단·개선 후보 · 플래그 뒤 개선 코드 · 홀드아웃 열람 규칙 §6.6) · 반출 묶음 §6.5(전문 + 내부 주소·계정·경로 가림 · 잔존 검사 관문 · `export.py`) · 외부망 MLX를 대표 3세계 × 1회로 축소(W5) · 사전 점검에 `--site closed`·tool-calling 왕복 · 모델 축 제거(FabriX 범위 밖) · 위험 R-9~R-11 · D-324 본문 등재 · 코드 0 |
| 2026-10-13 | **v1.2** — 사용자 요청 *"151번 계획에 구성 및 테스트 방법도 자세히 추가로 정리하라."* 추가: §4.7 실행 구성(프로세스·포트 토폴로지 · 벤치 전용 진입 `_serve.py` — 운영 진입 포트 상수 `mcp_service.py:45` · 실험군 = 프로세스 · 빈 cwd + OS env 주입으로 `.env`·`.encenv` 차단 · 실험군 env 표 · 실행 명세 YAML) · §4.8 실행 순서 · §4.9 산출 파일 데이터 계약(`world_calls.jsonl` 신설 · `--rejudge` 보존 규칙) · §4.10 세계 서버 규칙(현재 세계 = 러너 지정 · 호스트·시간창 인자 오류) · §4.11 가짜 LLM(세계 대본 재생 · 테스트 전용) · §4.2에 `hosts`·`incident_window`·`script` · W1에 설정 `investigation_audit_path`(기본 None = 비트 동일 — 감사 경로 고정 `investigation_jobs.py:57`) · §9를 테스트 계층 L0~L6 · 파일별 핵심 케이스 · 실 LLM 실행 절차(외부망 MLX · 내부망 6단계) · Wave별 완료 기준으로 확장 · §9.6 매뉴얼 연동 Wave를 W8로 바로잡음 · 코드 0 |
| 2026-10-13 | **v1.3** — 사용자 지시 *"내부망에서는 holmesGPT는 GPU에서 돌릴 예정이다. 또한 필요한경우 fabrix나 vLLM에서 LLM을 사용할 예정이다."* + 확인 답(같은 GPU 서버에 함께 · FabriX = 대체 백엔드). 바뀐 것: §6.7 신설(GPU 서버 동거 배치 · venv 재현 · 포트 · GPU 경합 기록과 지연 처분 조건 · 백엔드 표 — vLLM 기본 / `fabrix_proxy` 대체: 148 G-4 통과 전제 · 자기 기준선 · 교차 비교 금지 · 호출량 확인 · PII 차단 무효표) · 실행 명세 `llm.backend`·`host.gpu_sample_interval_s` · manifest 백엔드·서빙·호스트·GPU 칸 · 사전 점검(closed의 127.0.0.1 허용 · FabriX 비과금) · §1.3 FabriX 범위 밖을 「교차 비교」로 좁힘 · W5 키트 · W6 · §9.3 내부망 절차 · R-12~R-14 · G-4 확정표 개정 · D-324 ⑤ 부기 · 코드 0 |
| 2026-10-13 | **v1.4** — 사용자 요청 *"계획서에 실제 실행할 명령어를 명확히 정리하라."* §9.3을 「실행 명령(정본)」으로 재작성: 9.3.0 `inv_bench.py` 옵션 계약 표(`--show-env`·`--resume`·`--status`·`--cleanup`·`--dry-run`·`--test` 추가) · 9.3.1 venv 준비 · 9.3.2 Wave 테스트·정적 게이트·계획 끝 회귀 · 9.3.3 스키마 스냅샷(mcp_server 조사 프로파일 19197 · apm_gateway 19196) · 9.3.4 린트·가짜 LLM 종단 · 9.3.5 로컬 MLX · 9.3.6 내부망 키트(휠 다운로드) · 9.3.7 GPU 서버 회차(vLLM · FabriX) · 9.3.8 외부망 검토 요청 예 · 명세 `fake_e2e.yaml` · `pids.json` · 블록마다 `[위치 · CWD · venv]` · 이 작업 트리에 venv 없음 실측 · 코드 0 |
| 2026-10-13 | **v1.5 · W0~W5 구현(`-TODO` → `-WIP`)** — 사용자 지시 *"151번 계획을 구현하라."* 계획 시작 SHA를 `f4a95f5`로 옮김(§9.3.2·§9.5 base 교정). W0 sre_agent venv(holmesgpt==0.36.0 고정 — 미고정 시 0.43.0) · 계측 빈칸 테스트 · W1 계측(`_StreamDrain`·`INSTRUMENTATION_KEYS`·미완주 `_AnswerOnly`·`investigation_audit_path`) · W2 세계 서버·스키마 스냅샷 · W3 채점(j2)·보고·반출·가짜 LLM·사전 점검·러너·CLI · W4 세계 24건·린트·자기검증 · W5 로컬 MLX 3세계 1회(D01 F-READ · D03·D09 F-TMO) · 내부망 키트(휠 197 · manylinux_2_28 5종 · sdist 빌드 1종). 확정값은 §4.5·§4.7·§4.9·§4.10·§9.3.x 「W? 확정/실측」 블록에 반영. 검증 지적(Major 4 · Minor 7) 교정 반영 |
