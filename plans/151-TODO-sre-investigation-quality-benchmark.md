# 151. sre_agent 장애 조사 품질 벤치마크 — 정답이 있는 합성 장애 세계로 HolmesGPT 조사를 반복 측정하고, 실패 유형에서 개선점을 찾아 A/B로 채택한다

> **작성일**: 2026-10-12 · **v1.0** · **v1.1**(같은 날 — 사용자 인터뷰로 게이트 G-1~G-8 확정 · 측정은 내부망 사내 vLLM 중심 · 외부망 Claude Code 검토 루프 · 로컬 MLX 최소화)
> **상태**: **TODO — 계획만(코드 0건)** · 게이트 G-1~G-8 **확정**(2026-10-12 인터뷰 · §10) · D-324 본문 등재
> **요청(사용자 2026-10-12)**: *"sre_agent를 통해 장애 분석을 하는 기능에 대해 벤치마크 테스트를 통해 개선포인트를 개측하여 개선을 진행하려고 한다. 관련 계회파일을 작성하라."*
> **해석**: 「개측」 = 계측(측정). 이 계획의 대상은 **조사 품질**(원인을 맞히는가 · 근거를 대는가 · 끝까지 가는가 · 얼마나 드는가)이다. 노이즈 게이트 판정 품질(`noise_gate`)과 본체 text2sql 벤치(`scripts/bench`·`scripts/scenario`·`scripts/itam_bench`)는 범위 밖이다.
> **입력**: 저장소 `3c4adab` 읽기·grep 실측(서브에이전트 1갈래 — sre_agent 흐름·설정·도구·평가 자산·관측성) + 대화 중 `file:line` 재대조. 실 LLM 호출 0 · 서버 기동 0 · 패키지 설치 0.
> **관련 계획**: `plans/sre-agent/02`(HolmesGPT 조사 — §12 수용 기준은 구조·안전만) · `plans/91`(장애 조사 잔여 — 1-14 사내 vLLM 완주 판정 대기 · 1-17 MLX 9B 40 step 미완주) · **`plans/150`**(WIP — W5가 sre_agent 질문·지침·사전수집·`cross_source` 블록을 바꾼다 · §8 순서 조율) · **`plans/101`**(ML RCA — 라벨 스키마·브리핑 채점 P-24·누수 금지 KL-3를 이 벤치가 그대로 쓴다) · `plans/93`·`plans/97`(벤치 판정·반출 관례) · `plans/135`(벤치 계약 문서 형식)
> **관련 결정**: D-035(결정적=판단 · LLM=서술) · D-118·D-139(`sre_agent` 독립 패키지 · 양방향 import 0) · D-119(관측 읽기 경계 = `mcp_server`) · **D-120**(Gemini = 개발 전용 · 운영 데이터 외부 송신 금지) · D-123(조사 서비스 submit/poll · 상태 어휘) · **D-127·D-240**(실 LLM = 로컬 MLX · 과금 평면 건별 승인) · D-213(조사 타임아웃 join 타임박스) · D-229·D-230(배선 교정 · 실 조사 LLM 게이트) · **D-238**(처분 어휘 — 대조군 최적 = `기각`) · **D-219**(관측 0 = 판정 불가 · 무효표 고지) · D-254(축은 값을 읽는 코드가 있어야) · D-303(회귀)
> **D-번호**: **D-324 본문 등재**(2026-10-12 · `docs/decisions/D-324.md` · 「sre_agent 조사 품질 벤치마크 계약」)
> **계획 시작 SHA**: `3c4adabde6d11797e5fdd831f9c3a6fb7bfb9ba6`
> **회귀 대상 파일(예정 — 착지 때 확정)**: `sre_agent/sre_agent/diagnosis.py` `sre_agent/sre_agent/application/investigation_dispatcher.py` `sre_agent/sre_agent/application/investigation_jobs.py` `sre_agent/sre_agent/application/briefing_builder.py` `sre_agent/sre_agent/application/evidence_prefetch.py` `sre_agent/sre_agent/application/investigation_guidance.py` `sre_agent/sre_agent/interface/mcp_service.py` `sre_agent/sre_agent/settings.py` + 신규 `sre_agent/scripts/investigation_bench/*` · `sre_agent/testdata/investigation_bench/*`

---

## 0. 증거 규칙

- 과금 호출 0 · DB 호출 0 · 서버 기동 0. §2 앵커는 `3c4adab`에서 `grep -n`으로 줄 번호를 다시 확인했다.
- 「(추론)」은 코드 읽기로 판단했으나 실행으로 확인하지 않은 항목이다. W0에서 테스트·실측으로 먼저 확정한다.
- 이 개발 맥에는 `sre_agent/.venv`가 **없다**(실측 — holmesgpt 미설치). 설치(holmesgpt 0.36.0 · D-120)는 W0 첫 작업이다.
- 외부망 실 LLM은 로컬 MLX **대표 3세계 × 1회**뿐이다(하네스가 실 LLM으로 끝까지 도는지 확인용 · G-7). 기준선·A/B 측정은 내부망 사내 vLLM에서 사용자가 돌린다(G-4). 과금 평면(Gemini·anthropic)은 하네스가 막고, 쓰려면 건마다 사용자 승인을 받는다(§6.4).

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
- FabriX를 조사 모델로 쓰는 측정 — 내부망 측정 모델은 사내 vLLM 하나로 정했다(인터뷰). FabriX tool-calling은 `plans/148` 소관.
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
| M-1 | **미완주가 `done`으로 기록된다** — `DiagnosisResult.incomplete`를 application·interface 계층이 읽지 않는다(grep 0건) | `diagnosis.py:94,223` · `application/investigation_dispatcher.py:427` |
| M-2 | 미완주 결과는 answer 문구만 남고 토큰·도구 기록이 0이다(holmes 예외 경로) | `diagnosis.py:213-224` |
| M-3 | 타임아웃 잡은 토큰·도구 0으로 덮인다 | `application/investigation_dispatcher.py:396` |
| M-4 | `done` 감사 레코드에 지연·step 수·도구 호출 수·모델명이 없다(level·confidence·escalate·signals·tokens·cost만) | `application/investigation_dispatcher.py:434-446` |
| M-5 | 원시 도구 기록(`ToolCallRecord` — 인자·상태·출력)은 판정·정체 가드에만 쓰이고 잡에 남지 않는다. 잡에는 description 목록뿐 | `diagnosis.py:21-75` · `application/investigation_dispatcher.py:430` |

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
sre_agent/scripts/investigation_bench/     (신규 · sre_agent venv)
  __main__.py      CLI: --preflight · --run · --rejudge · --report · --lint · --schema-snapshot · --export
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
산출: sre_agent/.data/investigation_bench/<run_id>/  (gitignore 확인됨 — .gitignore:50)
  manifest.json(실험군·설정 에코·holmes 버전·모델·스키마 해시) · raw.jsonl · scores.jsonl · report.md
```

**세계 서버 라우팅**: 세계마다 고유 합성 호스트명(`bw-d01-app01` 형태)을 쓰고, 서버 하나가 전 세계를 싣는다. 도구 호출의 `hostname`/`server_name` 인자로 세계를 고른다 — sre_agent가 기동 시 MCP 서버를 등록하는 구조라도(추론 — W0 확인) 세계 전환에 재기동이 필요 없다. 엉뚱한 호스트명으로 부르면 「대상 없음」을 돌려주고 인자 오류로 센다(F-ARG).

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
| D15 | dev | time_window(원인은 알람 시각 이전 · 지금은 정상) | os | `reference_time` 사용 검증 |
| D16 | dev | apm_pool_exhaustion(제니퍼 DB 커넥션 풀 고갈) | app | **크로스소스 — `plans/150` W5 뒤** |
| H01~H08 | holdout | D01·D02·D03·D05·D07·D09·D10·D16과 같은 kind · 다른 호스트·프로세스·수치·문구 | | 개선 채택 판정 전용 |

- 이벤트 모양은 `noise_gate/scripts/mock_polestar_events.py`의 `cascade`·`change-corr`·`net-site-cascade` 시나리오를 빌린다(티어 기대값은 쓰지 않는다).
- 린트(W4): 라벨 완전성 · 별칭 ≥2 · `key_evidence`가 `tools`에 실제로 있는가 · 알람 문구에 정답 kind 별칭 금지 · 홀드아웃과 개발셋 문구 중복 금지.

---

## 5. 개선 후보 (가설 — 내부망 1회차 기준선이 순위를 정한다)

기준선 전이라 **채택 대상이 아니라 실험군 후보**다. 1회차(W6) 실패 유형 분포를 외부망 검토(W7)가 진단해 상위 유형을 가리키는 후보부터 다음 회차에 올리고, 표에 없는 새 가설도 검토에서 추가할 수 있다.

| ID | 가설 | 겨냥 유형 | 손댈 곳 | 플래그(기본 off) | `plans/150`과의 관계 |
|---|---|---|---|---|---|
| H-1 | 알람 kind별 질문 템플릿(고정 1종 → kind 축약 트리아지)이 step을 줄이고 F-MISS를 줄인다 | F-INC · F-MISS | `interface/mcp_service.py:85` | `investigation_question_by_kind` | 150 §4.6이 질문을 크로스소스용으로 확장 — **150 W5 랜딩 뒤 그 위에 얹는다** |
| H-2 | 사전수집을 기본 on으로 + 축을 kind에 맞춰 고른다(현 4축 고정) | F-MISS · F-INC | `application/evidence_prefetch.py:27` · `settings.py:166` | 기존 `evidence_correlation_enabled` + 신규 `evidence_prefetch_axes_by_kind` | 150이 사전수집 소스를 고친다 — 뒤에 |
| H-3 | 사전수집 결과 요약을 지침 앞에 넣어 같은 조회를 막는다(현 상관 결과 주입이 충분한지 W0 확인) | F-LOOP · F-INC | `application/investigation_guidance.py` | `prefetch_summary_in_guidance` | 지침 조립 순서를 150이 바꾼다 — 뒤에 |
| H-4 | 인용 검증을 결정화한다 — 인용 수치를 도구 출력과 대조해 없으면 가설 강등·`limitations`에 표기(현 마커 문자열 검사) | F-HALLU · F-CITE | `application/briefing_builder.py:30,143` + `ToolCallRecord` 출력 사용 | `citation_value_check` | 독립 |
| H-5 | 미완주 시 수집된 도구 출력으로 결정적 부분 브리핑을 만든다(현 answer 문구만) | F-INC의 피해 축소 | `diagnosis.py:213-224` · `briefing_builder.py` | `partial_briefing_on_incomplete` | 독립 · holmes 예외 경로에서 도구 기록 회수 가능 여부 W0 실측 |
| H-6 | 기본 off 기능 6종(`severity_judge`·`remediation_recommender`·`evidence_change_overlay`·`openmetrics_guidance`·`apm_guidance`·`apm_signatures`)을 하나씩 켠다 | 각 기능 겨냥 유형 | 설정만 | 기존 플래그 | 독립 — 축 측정만 |
| H-7 | 평시 기준선 비교 지침(「같은 시각 지난 N일 대비」) | F-DIST | `investigation_guidance.py` · 사전수집 `evidence_baseline_periods` | `baseline_compare_guidance` | 뒤에 |
| H-8 | `max_steps`·타임아웃 축(20/40/60) | F-INC · 비용 | 설정만 | 기존 필드 | 독립 |

모델 축은 두지 않는다 — 측정 모델은 내부망 사내 vLLM 하나다. 로컬 MLX 결과는 처분에 쓰지 않는다(D-127 「지연 결론은 내부망」).

---

## 6. 측정 절차와 판정

### 6.1 기본 규모

- **내부망(사내 vLLM)**: 반복 k=3(G-7) · 개발 16 · 홀드아웃 8 → 실험군 1개당 72회. 1회차는 대조군 2회(A/A) = 144회. 사내 vLLM 1회 소요는 미측정이라 1회차 manifest의 실측으로 다음 회차의 실험군 수를 정한다.
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

### 6.3 기록 위생

- 합성 세계라 운영 데이터는 없다. `raw.jsonl`에 브리핑 전문·세계 서버 호출 로그(도구·인자·응답)·W1 계측 키를 남긴다. 체크포인트·디버그 로그는 판정 입력이 아니다(D-219 ①).

### 6.4 과금 차단 (`preflight.py`)

- sre_agent 실효 설정의 `model` 프리픽스와 `api_base`를 읽어 **provider가 OpenAI 호환(`openai/…`)이고 `api_base`가 ① 127.0.0.1/localhost(외부망 MLX) 또는 ② `--site closed`로 실행하며 사설 주소·내부 호스트(사내 vLLM)일 때만** 진행한다. `gemini/…`·`anthropic/…`·공용 API 호스트(Google·Anthropic·OpenAI 도메인)는 거부(exit 2).
- 사전 점검은 tool-calling 왕복 1회를 포함한다(사내 vLLM이 도구 호출을 지원하지 않으면 회차 전체가 무효 — 실행 전에 멈춘다).
- `--allow-billed`는 존재하되 사용자 승인 문구를 인자로 받아 manifest에 남긴다(D-127 — 건별 승인 · 포괄 승인 없음). Gemini 실행 시에도 D-120 데이터 통제(합성 세계뿐)는 자동 충족된다.
- 설정 판정은 pydantic 필드로만 한다(`os.getenv` 금지 · `.env`는 `os.environ`에 주입되지 않음 — 2026-08-28 실수 이력 · 실제 소비 필드는 `model`/`api_base`이지 `investigation_llm_model`이 아니다).

### 6.5 내부망 회차와 반출 묶음 (G-4 · G-7 · 인터뷰)

- **회차** = 내부망에서 사용자가 `--site closed --run`으로 한 번 돌리고 `--export`로 묶음을 만드는 단위다. 1회차는 대조군 A/A(기준선), 2회차부터는 대조군 + 그 회차의 실험군(외부망 검토가 정한 후보 · 플래그로 켬).
- **반출 묶음**(`<run_id>.bundle/`): `manifest.json`(실험군 · 설정 에코 · holmes 버전 · 모델명 · 스키마 해시 · 1회 소요 실측) · `raw.jsonl`(**브리핑 전문** · 도구 호출 순서와 인자 · 세계 서버 응답 · W1 계측 키 · 상태) · `scores.jsonl` · `report.md`(비교표 · 실패 유형 분포 · 오답 검토표 · 처분 초안).
- **가리는 것은 내부 주소·계정·경로뿐이다**: `api_base` 호스트 · 실행 OS 계정 · 서버 호스트명 · 내부 절대 경로. `export.py`는 가린 뒤 잔존 검사(설정에서 읽은 원값·사설 IP 대역·홈 경로 패턴)를 돌리고, 걸리면 묶음을 만들지 않는다(D-301 「누출 관문 실패 = 미기록」 준용).
- 세계 YAML은 저장소에 있으므로 묶음에 넣지 않고 커밋 해시로 가리킨다.

### 6.6 외부망 검토 — Claude Code (G-5 · 인터뷰)

반출 묶음을 받으면 외부망 Claude Code가 아래 셋을 낸다. 스킬로 고정하지 않고, 이 절과 W7 행이 절차의 정본이다.

| 산출 | 내용 | 반영 |
|---|---|---|
| ① **채점 교정안** | 결정적 채점이 틀린 건(별칭 누락으로 정답을 오답 처리 · 인용 토큰 경계 오류 · 수치 반올림 규칙 등)을 찾아 세계 YAML 별칭·채점 규칙 수정 | 커밋 후 같은 묶음을 `--rejudge`로 재채점 → 교정 전후 점수를 보고서에 나란히 |
| ② **실패 원인 진단 · 개선 후보** | 실패 유형별 표본의 도구 호출 순서·브리핑을 읽고 끊긴 지점(질문·지침·사전수집·인자·해석·모델 한계)을 판정 · H-* 순위 조정 · 새 가설 추가 | 계획서 「회차 기록」 절에 장부로 남기고 사용자에게 다음 회차 실험군을 확정받는다 |
| ③ **개선 코드** | 확정된 후보를 플래그 1개(기본 off · 비트 동일)로 구현 · 개발셋 기준 · MLX 대표 1~2세계 × 1회 로직 확인 | 다음 회차 키트에 실험군으로 실린다 |

- **홀드아웃 열람 규칙**: ①은 홀드아웃 원문도 본다(오채점이 처분을 왜곡하므로). ②·③은 **개발셋 원문만** 본다 — 홀드아웃은 점수·유형 집계만 쓴다. 검토 세션에 위임할 때 홀드아웃 원문 경로를 프롬프트에 넣지 않는다.
- 처분(§6.2)은 교정 후 재채점 점수로 내린다. ①이 홀드아웃 점수를 바꾸면 교정 내용과 영향 세계를 보고서에 명시한다(교정이 실험군 편을 드는지 사람이 볼 수 있게).

---

## 7. Wave

| Wave | 범위 | 산출 | 검증 |
|---|---|---|---|
| **W0** | 실측·기준 고정 | `sre_agent/.venv` 설치(holmesgpt 0.36.0) · holmes `LLMResult`·예외 경로에서 step 수·부분 도구 기록 회수 가능 여부(`inspect` 실측 → H-5 실현성) · MCP 서버 등록 시점(기동 1회 vs 조사마다) · 사전수집 결과가 지침에 어디까지 들어가는지(H-3 전제) · M-1~M-5를 실패 테스트로 고정 · `plans/150` W5 진행 상태 확인 | 신규 테스트만(실패 상태로 커밋하지 않고 `xfail(strict=True)`) |
| **W1** | 계측 보강(M-1~M-5) | 잡·`done` 감사에 가산 키 `incomplete` · `elapsed_s` · `prefetch_s` · `steps`(W0에서 얻을 수 있을 때) · `tool_calls_n` · `model` · `max_steps` · 도구 기록 요약 `tool_calls[{name, args_digest, status}]`(원시 출력 제외 — 크기·민감도) · 미완주·타임아웃의 토큰 보존(가능 범위) · 미완주는 `status` 유지 + `incomplete:true`(G-3) | `test_investigation_jobs` · `test_diagnosis_*` · `test_payload_contract` · noise_gate 쪽 `sre_get_investigation` 소비자가 추가 키를 무시하는지 grep·테스트 |
| **W2** | 세계 서버 · 스키마 | `world_server.py` · `--schema-snapshot` · `tool_schemas.json` · 생성기 지시(`gen`) · 호출 로그 | 세계 서버 단위 테스트(라우팅·매처·기본 응답·인자 오류 판정) · 스냅샷 드리프트 테스트 |
| **W3** | 러너 · 채점 · 보고 · 사전 점검 · 반출 | `runner.py`(벤치 포트·env 오버라이드·submit/poll·반복·교차 순서) · `judge.py`(§4.4·§4.5) · `report.py`(오답 검토표 포함) · `preflight.py`(§6.4 — `--site closed` · tool-calling 왕복) · `export.py`(§6.5) · `--rejudge` | 채점기 단위 테스트(손으로 쓴 브리핑 픽스처 — 지표별 경계값) · 반출 가림·잔존 검사 테스트(심은 내부 주소·계정이 걸리는가) · **가짜 LLM 종단**(holmes 대신 고정 응답 — 실 프로세스 sre_agent + 세계 서버 + 러너가 raw.jsonl·report.md·묶음까지) |
| **W4** | 세계 24건 · 린트 | `worlds/dev/D01~D16` · `worlds/holdout/H01~H08`(D16·H08은 150 W5 뒤) · `lint.py` | 린트 통과 · 세계마다 「모범 답안 브리핑」 픽스처로 채점기가 PASS를 내는지(세계 자체 결함 점검) |
| **W5** | 외부망 실 LLM 확인 · 내부망 키트 | 로컬 MLX **대표 3세계 × 1회**(sre_agent `model`/`api_base`가 127.0.0.1 MLX인지 `--preflight`로 확인 뒤) — 계측 키·채점·묶음이 실 LLM 출력으로 끝까지 도는지만 본다(점수는 쓰지 않음) · 내부망 키트(sre_agent venv 반입·사내 vLLM 설정 예시·`--site closed` 실행·반출 절차 · `docs/26` 1절) | 3건 완주 여부와 무관하게 raw·scores·묶음 생성 · 묶음 잔존 검사 통과 |
| **W6** | 내부망 1회차 (사용자) | 사내 vLLM · 대조군 A/A(144회) → 묶음 반출 · `plans/91` 1-14 판정 입력 겸용 | 사전 점검 통과(tool-calling 포함) · 무효표율 ≤ 20% |
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
| R-11 | 반출 묶음에 내부 주소·계정이 섞임 | 가림 + 잔존 검사 관문(걸리면 묶음 미생성) · 가림 테스트에 심은 값 |

---

## 9. 검증 · 회귀 · 매뉴얼

- 회귀(D-303): Wave 중간은 자기 테스트 + `python scripts/regress.py --no-tests --files …`. 계획 끝 1회 `python scripts/regress.py --base 3c4adab --files <회귀 대상>`. sre_agent는 **`cd sre_agent && .venv/bin/python -m pytest tests`**(루트 cwd 금지 — 2026-09-10 실수 이력). 전체 회귀는 사용자 요청 시만.
- 품질 게이트: `sre_agent/scripts/arch_check.py --ci`(벤치 스크립트는 `scripts/`라 계층 검사 밖 · 세계 서버는 `sre_agent.*` import 0) · 경계 테스트 `sre_agent/tests/test_boundary.py`(벤치가 `mcp_server`·`src` import 0) · `overfit_check --ci`(개선 Wave가 `sre_agent/sre_agent/domain`에 폴스타 스키마 리터럴을 넣지 않는지).
- 실 LLM(외부망): W4까지 0회(가짜 LLM). W5는 로컬 MLX 대표 3세계 × 1회, W7 개선 코드는 바뀐 경로 대표 1~2세계 × 1회 · 모두 `--preflight` 통과 뒤. 측정(W6·W8)은 내부망 사내 vLLM에서 사용자가 돌린다.
- **매뉴얼(D-255)**: 대상 아님(내부 벤치·계측). W6에서 기본값이 바뀌어 사용자에게 보이는 브리핑 모양이 달라지면(H-4 `limitations` 표기 · H-5 부분 브리핑) 그 Wave에서 관리자 매뉴얼의 조사 브리핑 읽는 법을 함께 고친다.
- 가이드: `docs/26_sre_agent_guide.md`에 벤치 실행·내부망 회차·반출 절차 1절(W5).

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
| G-4 | (a) — 측정 모델 = **내부망 사내 vLLM**(FabriX 제외) | 모델 축 없음 · §1.3 |
| G-5 | (a) 결정적 채점 **+ 결과 로그를 남겨 외부망 Claude Code가 검토** — 산출: 채점 교정안 · 실패 원인 진단·개선 후보 · 개선 코드(스킬화는 하지 않음) | §6.5 반출 묶음 · §6.6 검토 · W7 |
| G-6 | (a) 하네스 병행 · 측정은 150 W5 뒤 | §7.1 |
| G-7 | (a) 16 + 8 · k=3 — 단 **MLX는 느리니 최소화하고 대부분 내부망에서** → 외부망 MLX는 대표 3세계 × 1회 | §6.1 · W5 |
| G-8 | (a) | §6.2 |
| (추가) 반출 범위 | 브리핑 전문 · 도구 호출·응답 로그 포함 · 내부 주소·계정·경로만 가림 | §6.5 |

---

## 11. 의사결정 정합 · 신규 결정 예약

- **충돌 없음(사용자 확인이 필요한 충돌 0)**. 확인한 결정: D-035(채점·분류는 결정적, LLM은 조사 서술만 — 정합) · D-120(Gemini 데이터 통제 — 합성 세계라 자동 충족, 과금 승인은 별도) · D-123(상태 어휘 — G-3 (a)로 불변) · D-127·D-240(과금 차단 — §6.4) · D-139(import 0 — 스키마는 MCP 프로토콜로) · D-219·D-238(판정 불가·처분 어휘 준용).
- **D-324 본문 등재**(2026-10-12) — 「sre_agent 조사 품질 벤치마크 계약」: ①정답 = 구성으로 아는 합성 세계(가짜 MCP 서버) · 누수 금지 ②실제 조사 경로(submit/poll) ③도구 스키마 = 실 서버 스냅샷 ④결정적 채점·실패 유형 + 외부망 Claude Code 검토(교정·진단·코드) ⑤개발/홀드아웃 분리 · 홀드아웃으로만 처분 · 검토의 홀드아웃 열람 제한 ⑥측정 = 내부망 사내 vLLM · 외부망 MLX 최소 · 과금 평면 차단 ⑦계측 가산 키 상시 · 미완주 `incomplete:true` ⑧반출 = 전문 + 내부 주소·계정·경로 가림.
- `plans/101` 부기 예정: 라벨 스키마·채점기를 이 벤치와 공유(F0 리플레이 평가가 `judge.py` 재사용).

---

## 12. 변경 이력

| 날짜 | 내용 |
|---|---|
| 2026-10-12 | **v1.0 신규(`-TODO`)** — 현행 실측(조사 경로 · 설정 축 · 계측 빈칸 M-1~M-5 · 평가 자산 · 원인 정답 0건) · 합성 장애 세계 벤치 설계(세계 서버 · 스키마 드리프트 가드 · 결정적 채점 8지표 · 실패 유형 12종) · 세계 24건 초안 · 개선 후보 H-1~H-8 · 판정 규칙(A/A 잡음폭 · 홀드아웃 · D-238) · Wave W0~W7 · `plans/150` 순서 조율 · 게이트 G-1~G-8 · D-324 예약 · 코드 0 |
| 2026-10-12 | **v1.1** — 사용자 인터뷰로 G-1~G-8 확정(§10 확정표). 바뀐 것: 측정을 내부망 사내 vLLM 회차로 옮김(W6 1회차 A/A · W8 N회차) · 외부망 Claude Code 검토 W7 신설(채점 교정 → `--rejudge` · 실패 원인 진단·개선 후보 · 플래그 뒤 개선 코드 · 홀드아웃 열람 규칙 §6.6) · 반출 묶음 §6.5(전문 + 내부 주소·계정·경로 가림 · 잔존 검사 관문 · `export.py`) · 외부망 MLX를 대표 3세계 × 1회로 축소(W5) · 사전 점검에 `--site closed`·tool-calling 왕복 · 모델 축 제거(FabriX 범위 밖) · 위험 R-9~R-11 · D-324 본문 등재 · 코드 0 |
