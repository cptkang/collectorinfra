# 26. sre_agent 가이드 — 주요 기능 · 구동 방법 · 사용법

> **범위**: `sre_agent/` 패키지 자체의 레퍼런스다. 기능·설정·MCP 계약·검증 명령을 다룬다.
> **범위 밖**: 폴스타 알람 → 게이트 → 자동 조사 → 브리핑 첨부의 **엔드투엔드 기동 시나리오**는
> `docs/23_plan66_mvp_test_guide.md`가 정본이다(프로세스 4종 배치·터미널 구성·과금 레벨 구분).
> 대상 호스트 부하 가드의 **요구 계약**은 `docs/25_host_investigation_load_guard.md`가 정본이다.
>
> 근거 결정: D-118(패키지 분리) · D-119(관측 경계 일원화) · D-120(테스트 LLM) · D-123(submit/poll 계약)
> · D-124(게이트 배선) · D-125(전송 인증) · D-127(과금 승인) · D-003(읽기전용) · D-011(권고 전용) · D-168(미들웨어 조사)

---

## 1. sre_agent는 무엇인가

HolmesGPT(ReAct 조사 루프)를 감싼 **장애 조사 에이전트**다. 알람이나 자연어 질문을 받아
대상 호스트를 조사하고, **결정적 후처리**를 거쳐 중요도 판정·글래스박스 브리핑·조치 권고를 낸다.

핵심 설계는 **"조사 루프는 LLM, 판단·가드는 코드"** 다(D-035 계승).
LLM은 어떤 도구를 어떤 순서로 부를지만 정하고, 다음은 전부 코드가 결정한다:

| 코드가 전담하는 것 | 모듈 |
|---|---|
| 폭주 방지(dedup·동시성·타임아웃·예산·호스트 in-flight) | `application/investigation_dispatcher.py` |
| 중요도 2차 판정(도구 **원시 출력** 정규식 매칭·escalate-only) | `domain/severity_signatures.py` |
| 브리핑 6요소 조립(인용 검증 → 가설 강등) | `application/briefing_builder.py` |
| 조치 권고(시그니처 → 결정적 표 조회) | `domain/remediation.py` |
| 실행 가능한 명령 범위(부하 가드 allowlist) | `toolset_profiles.py` |

### 1.1 경계 불변식 (D-118)

- `sre_agent/`는 collectorinfra `src/`를 **절대 import하지 않는다**.
- `src/`도 `sre_agent`를 **절대 import하지 않는다**.
- 통신은 **MCP 계약뿐**(`sre_investigate_alarm` / `sre_get_investigation` / `sre_diagnose`).
- `tests/test_boundary.py`가 양방향으로 이 불변식을 고정한다.
- 분리 절차 = **폴더 복사 + URL 설정 변경**(계약·코드 무변경이 회귀 기준).

런타임도 격리돼 있다 — 본체는 Python ≥3.11 · LangGraph 스택, `sre_agent`는
**Python ≥3.13 · holmesgpt 0.36.0 스택 · 자체 `.venv` · 별도 프로세스**다.

### 1.2 프로세스 위상

```
[noise_gate/alarm_server]  ── TCP 9100 ──►  Redis Stream(alarm:raw)  ── 소비 ──┐
  독립 프로세스(수신 전용)                                                      │
                                                                              ▼
[본체 src/ · noise_gate]            [sre_agent]                     [mcp_server]
  게이트 판정 → 트리거 노드           조사 서비스                       관측 데이터 읽기 경계
  (워커는 본체 in-process)
  챗 fault_diagnosis 노드
        │  MCP(SSE) submit/poll             │  MCP(SSE) 도구 소비            │
        └───────► 127.0.0.1:9098/sse ───────┴──────► 0.0.0.0:9097/sse ───────┘
                                                     (폴스타 SQL 고수준 도구 · PromQL)
```

- **9098** = `sre_agent` 조사 서비스(이 문서의 대상 · `127.0.0.1` 고정).
- **`mcp_server`** = 관측 데이터. 조사가 대상 VM의 실 데이터를 얻는 **유일한 경로**다(D-119).
  프로파일이 둘이라 인스턴스도 둘로 갈린다 — **9099는 본체 NL→SQL용**(`expose_execute_sql=true`),
  **조사용은 별도 포트(9097 권장 · `expose_execute_sql=false`)** 로 띄운다(D-122 · `docs/23` §4.2).
  `AgentSettings.polestar_mcp_url` 기본값은 `9099`이므로, 조사 인스턴스를 9097로 띄웠다면
  **`POLESTAR_MCP_URL`을 그 포트로 반드시 맞춘다**.
- 원격 조사 시 `sre_agent`의 로컬 셸은 대상 VM이 아니다 — `remote_vm_profile()`이 bash를 core로
  좁히고 `REMOTE_VM_SHELL_NOTE`로 LLM에 그 사실을 명시한다.

---

### 1.3 HolmesGPT는 어디서 돌고, LLM 추론은 어디서 도는가

> **"sre_agent가 HolmesGPT가 구동되는 서버냐?"** → 맞다.
> **"그럼 vLLM에서 동작해야 되냐?"** → **HolmesGPT가 vLLM 위에서 도는 게 아니다.**
> HolmesGPT는 `sre_agent` 프로세스 **안에서** 돌고, 그것이 **호출하는 조사 LLM**을 서빙하는
> 쪽이 vLLM이다. 둘은 역할도 서버도 다르다.

`sre_agent`는 HolmesGPT를 **SDK로 임포트해 같은 프로세스에서 실행**한다
(`diagnosis.py` → `holmes.core.tool_calling_llm.ToolCallingLLM`). **별도의 HolmesGPT 서버를
띄우지 않는다.** 그래서 이 프로세스가 하는 일은 루프 운전·후처리이고 **추론은 하지 않는다** —
GPU가 필요 없다.

| 무엇이 | 어디서 도는가 | 자원 |
|---|---|---|
| HolmesGPT ReAct 루프(도구 선택·대화 누적) | **`sre_agent` 프로세스** (서버 A · `127.0.0.1:9098`) | CPU만 · **GPU 불요** |
| **LLM 추론** | **조사 LLM 백엔드** (서버 B · vLLM `/v1` 기본 8000) | **GPU VRAM이 병목** |
| 도구 실행(폴스타 SQL · PromQL) | `mcp_server` 프로세스 (서버 C) | 대상 VM 데이터의 **유일 경로**(D-119) |
| 중요도 판정 · 가드 · 브리핑 조립 | **`sre_agent` 프로세스**(코드) | LLM 미개입(D-035) |

서버 A/B/C의 역할·경계 근거·방화벽 방향은 **`docs/23` §3.3.1**이 정본이다.
`sre_agent`는 `DEFAULT_HOST = "127.0.0.1"` **하드코딩**이고 env 오버라이드가 없어
(`interface/mcp_service.py:32`) **본체 API와 반드시 같은 서버**여야 한다 — 원격 분리는
바인드 설정화가 선행돼야 한다.

#### 왜 하필 vLLM인가 (2026-08-25 사용자 결정 · Plan 66 §7-1)

HolmesGPT의 ReAct는 **네이티브 tool-calling**을 요구한다. 매 호출에 `tools`/`tool_choice`를
싣고, 거부되면 예외로 끝나며 **프롬프트 폴백이 없다**. 이 조건으로 사내 후보를 거르면 하나가 남는다.

| 백엔드 | 조사 LLM 가능? | 사유 |
|---|---|---|
| **vLLM**(사내 구축) | **가능 — 채택 경로** | OpenAI 호환 `/v1` + tool-calling 플래그 2개로 성립. D-037이 본체에서 같은 블로커를 vLLM으로 푼 선례 |
| FabriX(KBGenAIChat) | **불가 — 확정** | OpenAI 비호환. 요청 payload에 **도구 필드가 없고** `bind_tools()`는 dead store라 프로토콜 수준에서 불가 |
| Gemini API | 개발·테스트만 | 외부 SaaS — **운영 투입 금지 · 실 폴스타 데이터 송신 금지**(D-120) |
| Anthropic 등 외부 API | 폐쇄망 정책 대상 | 기본값(`anthropic/claude-sonnet-5`)일 뿐 운영 승인 사항 |

즉 **"vLLM에서 동작해야 하나"의 답은 "실 데이터로 조사하려면 사실상 그렇다"** 이다.
Gemini는 규정상 실데이터를 태울 수 없고 FabriX는 기술적으로 불가하므로, 남는 선택지가 vLLM이다.

**vLLM 기동 시 플래그 2개가 필수다** — `--enable-auto-tool-choice`와 `--tool-call-parser`.
없으면 서버가 **200을 주면서 `tool_calls`만 비는 형태로 조용히 실패한다**.
**구축부터 연결까지 전 절차는 §5.6**(서버 기동 → 서빙 확인 → tool-calling 판정 → 배선 →
서비스 기동 → 완주 판정 → 본체 연결)에 있다. 배치·방화벽의 넓은 맥락은 `docs/23` §3.3.1,
미채택 대안은 `docs/23` §7-V.6을 본다.

#### vLLM이 없어도 알람 처리는 돈다

조사 LLM이 없거나 죽으면 조사만 `status="stub"`으로 떨어지고 **게이트 판정·통보는 정상 동작**한다.
브리핑만 빠지고 사유가 감사에 남는다 — **조사 실패가 알람 처리를 막지 않는다**(§6.1 · `docs/23` §3.3.1).
따라서 노이즈 캔슬링을 먼저 운영하고 조사는 나중에 붙여도 된다.

#### 현재 상태 (2026-08-28 실측)

- 레포 `.env`에 `MODEL`·`API_BASE`·`API_KEY`가 **없다** → 조사 LLM 백엔드 **미지정**.
  기본값은 `anthropic/claude-sonnet-5`·무키라 이 상태로는 조사가 성립하지 않는다(§5.6 실측 표).
- `.env`에 `NOISE_INVESTIGATION_*` 키가 **0건** → 자동 조사 트리거는 **off**(§5.5).
- ⇒ **vLLM 미확보 상태이며 조사 경로는 아직 운영에 붙어 있지 않다.** 서빙 사양 확정 →
  tool-calling 왕복 판정 → 40-step 완주 판정이 남은 잔여다(Plan 66 §7-1).
- 혼동 주의: 본체 `.env`의 `ORCHESTRATOR_*`(현재 `gemini`)는 **deepagents 제어평면용**이며
  조사 LLM과 **별개 설정**이다. 같은 vLLM 서버를 가리키게 할 수는 있어도 설정은 따로 준다.

---

## 2. 모듈 지도

```
sre_agent/
├── pyproject.toml            # requires-python >=3.13, holmesgpt>=0.36.0
├── .env.example              # 환경 변수 예시 (실값은 .env — 커밋 금지)
├── sre_agent/
│   ├── run_service.py        # entry — 유일한 기동 단위(재기동 복구 → 인증 미들웨어 → uvicorn)
│   ├── settings.py           # AgentSettings (pydantic-settings · os.getenv 금지)
│   ├── diagnosis.py          # DiagnosisAgent — HolmesGPT 래퍼 + step 상한 graceful 가드
│   ├── toolset_profiles.py   # vm / remote_vm / middleware 프로파일 + 부하 가드 allowlist
│   ├── interface/
│   │   └── mcp_service.py    # FastMCP 도구 5종 + 정적 Bearer 미들웨어
│   ├── application/
│   │   ├── investigation_jobs.py       # JobStore — submit/poll 상태·감사 JSONL·재기동 복구
│   │   ├── investigation_dispatcher.py # 가드 5종 + 백그라운드 워커 + 후처리 오케스트레이션
│   │   └── briefing_builder.py         # 브리핑 6요소 결정적 조립
│   └── domain/
│       ├── severity_signatures.py      # 시그니처 치트시트 + escalate-only 판정
│       └── remediation.py              # 조치 권고(제시 전용 — 실행 경로 없음)
├── scripts/
│   ├── arch_check.py         # Clean Architecture 계층 게이트
│   ├── smoke_llm.py          # Gemini tool-calling 스모크(키 없으면 graceful 보류)
│   └── ab_promql_gate.py     # D-119 품질 게이트 — PromQL 경로 A/B 비교(RUN_E2E 옵트인)
└── tests/                    # 21개 파일 · 247 passed / 2 skipped (2026-08-28 실측)
```

계층 규칙은 본체와 동일하다: `domain → config/utils → prompts → infrastructure → application
→ orchestration → interface → entry`. dispatcher가 application 세부 타입을 **구조적 Protocol**
(`JobLike`·`DiagnosisLike`·`ToolOutputLike`)로 디커플링하고 조사 함수·브리핑 함수를 **주입**받는
이유가 이것이다.

---

## 3. 주요 기능

### 3.1 MCP 도구 5종 (submit/poll 비동기 잡 계약 — D-123)

조사는 최대 300초까지 걸리는데 collectorinfra의 MCP 호출 타임아웃은 60초다. 동기 호출로는
계약이 성립하지 않으므로 **submit/poll**로 나눈다.

| 도구 | 인자 | 반환(JSON) |
|---|---|---|
| `sre_investigate_alarm` | `payload: dict`, `wait_seconds: int = 0` | `{investigation_id, status: accepted\|duplicate\|rejected, reason?}` |
| `sre_get_investigation` | `investigation_id: str` | `{status, briefing?, verdict?, tool_calls_summary?, tokens?, cost?, error?, reason?}` |
| `sre_diagnose` | `question`, `server_name?`, `hostname?`, `db_id?` | `{investigation_id, status: accepted\|rejected, reason?}` |
| `sre_list_investigations` | `limit: int = 20` | `{count, investigations: [...]}` |
| `sre_health` | — | `{status, version, contract_version, holmes_ready, polestar_mcp_reachable}` |

`sre_health`는 collectorinfra 클라이언트의 `health_check_tool` 지정 대상이다.
`holmes_ready`는 **LLM 키 존재 여부를 정직하게 반영**한다(키 없으면 조사는 스텁으로 떨어진다).

**잡 상태 전이**

```
accepted ─► running ─┬─► done      조사 완주 + 후처리 완료(브리핑·verdict 포함)
                     ├─► timeout   investigation_timeout_seconds 초과(사유 구조화)
                     ├─► failed    워커 예외 또는 재기동 중단(reason="restart")
                     └─► stub      LLM 키 부재 / 조사함수 미주입 — 침묵하지 않고 명시
         └─► rejected              계약 위반 · dedup · 예산 초과 · 호스트 in-flight
```

`duplicate`는 동일 fingerprint의 기존 잡이 살아 있을 때 **기존 investigation_id를 그대로**
돌려준다(신규 잡을 만들지 않는다).

### 3.2 트리거 페이로드 계약 (`contract_version: "1"`)

```json
{
  "contract_version": "1",
  "event": {
    "dbId": "polestar", "serverName": "WEB-01", "hostname": "web01.example.com",
    "alarmId": "...", "severity": 3, "alarmName": "CPU Usage High",
    "resourceType": "CPU", "resourceName": "cpu_usage",
    "alarmTime": "20260828142530", "conditions": "...", "conditionLog": "..."
  },
  "decision": { "tier": "PAGE", "reason": "...", "fingerprint": "...", "signals": {} },
  "meta": { "recurrence": null, "cluster": null, "root_resource": null, "source": "collectorinfra" }
}
```

- **필수 필드**: `contract_version` 일치 + `event.serverName` + `event.hostname` + `event.severity`.
  하나라도 결측이면 `rejected`(사유 포함·감사 기록). `severity: 0`은 유효하다(빈 값만 거부).
- **선택 필드**(`decision.signals`·`meta.*`) 결측은 거부하지 않고 조사 스코프만 좁힌다.
- `decision.fingerprint`가 dedup 키다. `decision.tier`·`event.severity`는 중요도 판정의 baseline이 된다.
- 본체 측 직렬화는 `noise_gate/domain/investigation_payload.py:build_trigger_payload`.

### 3.3 HolmesGPT 조사 루프 (`DiagnosisAgent`)

`Config(model, api_key, api_base, max_steps, toolsets, mcp_servers)` → `create_toolcalling_llm()`
→ `build_initial_ask_messages()` → `llm.call(messages)`.

- **step 상한 graceful 가드**: holmes는 상한 도달 시 전용 예외 없이 plain
  `Exception("Too many LLM calls - exceeded max_steps")`를 던진다(실측). 이를 하드 실패로
  전파하지 않고 `DiagnosisResult(incomplete=True)`로 **구조화 미완주** 반환한다.
  후처리는 이를 "가설/한계"로 표기하며 **escalate 신호로 취급하지 않는다**(미완주가 상향 근거가
  되면 오탐이 폭주한다).
- `max_steps` 기본 **40**. 실측상 10은 다중 메트릭 조사에서 미완주, 20은 포커스 질의만 완주,
  30도 브로드 트리아지에서 초과했다. 하드 백스톱은 dispatcher의 전체 타임아웃(300s)이다.
- **원시 출력 보존**: `DiagnosisResult.tool_outputs`가 `ToolCallRecord`(tool_name/status/
  **output**/error/return_code)를 담는다. 중요도 판정이 LLM 서술이 아니라 raw 출력에
  매칭하기 때문에 필수다.
- 부하 가드 안내(`LOAD_GUARD_NOTE`)는 `ask()`가 **항상 주입**한다. 호출자가 준
  `system_prompt_additions`는 덮어쓰지 않고 뒤에 덧붙인다.

### 3.4 toolset 프로파일 3종 + 부하 가드

| 프로파일 | 용도 | bash allowlist | 대상 데이터 |
|---|---|---|---|
| `vm_profile()` | 로컬 VM 진단 | `VM_DIAG_ALLOW`(확장) | 로컬 셸 |
| `remote_vm_profile()` | **원격 VM 진단(운영 경로)** | `[]` + `builtin_allowlist="core"` | 폴스타 MCP + PromQL(9099) |
| `middleware_profile()` | 미들웨어 조사(D-168) | `vm_profile`과 **동일**(확장 0) | 로컬 셸 |

`middleware_profile`이 별도로 존재하는 이유는 allowlist가 아니라 **조사 초점**이다
(`MIDDLEWARE_FOCUS_NOTE`): `ps`로 대상을 좁힌 뒤 **해당 pid에 한해** `pidstat`·`ss`를 보고,
**미들웨어 종류는 LLM이 추정하지 않고** 후단 결정적 매처(`src/domain/middleware.py`)에 넘긴다.

**부하 가드 — allowlist가 유일한 실효 강제다** (`docs/25` L-1~L-4)

```python
LOAD_GUARD_PREFIX = "timeout 20 nice -n 10 "        # L-1 nice + L-2 timeout
HEAVY_DIAG_COMMANDS = ("top -b -n 1", "vmstat", …, "journalctl", "dmesg", "lsof")
VM_DIAG_ALLOW = [*LIGHT_DIAG_COMMANDS, *(guarded(c) for c in HEAVY_DIAG_COMMANDS)]
```

무거운 명령은 **가드 형태로만** allow에 오르므로 가드 없는 형태는 자동 거부된다.
`top`은 `-n 1`로 고정한다(생략 시 무한 실행 — L-3). deny도 **가드 형태를 함께 등록**한다 —
`journalctl --vacuum`을 bare로만 막으면 `timeout … nice … journalctl --vacuum`이 비껴간다.

### 3.5 결정적 폭주 방지 가드 5종 (`InvestigationDispatcher`)

| # | 가드 | 설정 | 기본 | 위반 시 |
|---|---|---|---|---|
| 1 | fingerprint dedup TTL | `investigation_dedup_ttl_seconds` | `None`(off) | `rejected: dedup_ttl_active` |
| 2 | 동시 조사 상한(세마포어) | `investigation_max_concurrent` | `2` | 대기 |
| 3 | **조사 1건 전체** 타임아웃 | `investigation_timeout_seconds` | `300` | `timeout` |
| 4 | 시간당 예산 | `investigation_hourly_budget` | `None`(off) | `rejected: hourly_budget_exceeded` |
| 5 | 동일 호스트 in-flight(L-4) | — (항상 on) | — | `rejected: host_investigation_in_flight` |

- **③은 per-call이 아니라 전체**다(`asyncio.wait_for`). per-call만으로는 폴링 루프를 못 막는다.
- **⑤는 ①과 목적이 다르다**: ①은 *같은 알람*의 재조사를 TTL로 억제하고, ⑤는 *다른 알람이라도
  같은 호스트*의 **동시** 조사를 막는다(부하는 곱해진다). 대기가 아니라 **거부**하는 이유는
  조사가 분 단위(실측 161s)라 submit을 붙들면 MCP 동기 타임아웃(60s)을 넘기기 때문이다.
- in-memory 상태(dedup dict·budget window·in-flight 키)는 **값 bound + 키 만료 sweep**을 함께
  구현한다. in-flight 키는 타임아웃 2배가 지나면 방어적으로 축출한다 — 없으면 워커 유실 시
  그 호스트가 **영구히 조사 불가**가 된다(가드가 장애가 되는 형태).
- 잔여 한계(명시): 호스트 키는 `(db_id, host)`이고 `hostname` 없으면 `serverName`으로 대체한다.
  폴스타는 `server_name ≠ hostname`이므로(D-046) 같은 호스트가 두 이름으로 들어오면 다른 키가
  되어 ⑤를 비껴간다. 이름 해소는 본체(`cmm_resource`) 소관이라 경계상 여기서 부를 수 없다.

### 3.6 중요도 2차 판정 — escalate-only

`domain/severity_signatures.py`. 도구 **원시 출력 문자열**에 정규식 시그니처를 매칭한다.

- **강(strong·로그)**: OOM Killer · FS read-only 리마운트 · 서비스 재시작 루프 · soft lockup ·
  hung task · segfault/GPF · conntrack 고갈
- **중(medium·로그)**: FD 고갈(`too many open files`) · 디스크/inode 고갈(`no space left on device`)
- **강(strong·메트릭 대체)**: `node_vmstat_oom_kill` · `node_filesystem_readonly`
  — 원격 배치에는 dmesg/journal 원문이 없으므로 Prometheus 카운터로 대체한다(값이 0이 아닐 때만 매칭)

판정 규칙:

```
baseline = clamp(게이트 severity, 0..3)      # 0=해소 1=주의 2=경고 3=심각
strong 매칭 → 후보 = 심각(3)
medium 매칭 → 후보 = min(3, baseline+1)
level = max(baseline, 후보)                  # ★ 하향 경로가 코드에 존재하지 않는다
escalate = level > baseline                  # 엄밀 상향일 때만 True
```

원격(`remote=True`)에서 로그·대체 카운터가 **모두 무매칭**이면 `evidence_insufficient=True`로
상향을 보류한다. 로컬 무매칭은 "로그는 확보됐는데 신호가 없다"이므로 불충분이 아니다.

기본은 **off**(`severity_judge_enabled=False`) — off면 게이트 판정을 그대로 승계하고
`confidence="none"`, `escalate=False`다.

### 3.7 글래스박스 브리핑 6요소

`build_briefing()`이 `summary / timeline / bottleneck / cause / recommendation / limitations`를
결정적으로 조립하고, 별도로 `severity` 헤더를 붙인다.

- **인용 검증**: 도구명 언급 또는 인용 마커(`←`·`출처`·`근거`·`[원문` 등)가 없는 단정은
  `[가설]` 접두로 강등하고 `hypotheses` 배열에 모은다. `citations_verified`가 그 결과다.
- **한계 서술 강제**: "조사 시점 단면" 문구는 항상 붙고, 증거 불충분·인용 결여는 자동 추가된다.
- **권고는 항상 human-gated 문구 병기**: `※ 실행은 운영자 승인 후 수동 — 시스템은 제안만(자동 실행 경로 없음)`.
- 조사 미실행 시에는 6요소 대신 `{"stub": true, "message": <사유>, "elements": null}`을 낸다.

### 3.8 조치 권고 — 제시 전용(D-003 · D-011)

매칭된 시그니처 → 결정적 표(`_CANDIDATES_BY_SIGNATURE`) 조회. **LLM 서술은 입력이 아니다** —
권고 목록에 환각이 개입할 수 없다. 미등재 시그니처는 권고를 만들지 않는다(근거 없는 권고 금지).

위험도 3등급: `low`(되돌리기 쉬움) / `medium`(단일 프로세스 영향) / `high`(서비스 영향).
**고위험 × 저신뢰는 정식 권고가 아니라 `[검토 필요]`로 강등**한다. 결과는 위험도 낮은 순 정렬.

**실행 경로는 어느 배치에도 존재하지 않는다.** 이 모듈은 문자열만 만들며, 명령 리터럴이
들어가지 못하도록 `tests/test_remediation.py`가 고정한다(주석의 예시조차 검사에 걸린다).

기본 **off**(`remediation_recommender_enabled=False`). `severity_judge_enabled`가 off면
매칭 시그니처가 없으므로 권고도 비어 있다.

### 3.9 잡 저장소 · 감사 · 재기동 복구

- **in-memory dict + 감사 JSONL 이중 기록**. 기본 경로 `sre_agent/.data/investigation_audit.jsonl`
  (첫 조사 때 생성). 모든 상태 변화가 append된다.
- sweep: terminal 잡은 TTL 3600s / 최대 500건 기준으로 제거하고 **active 잡은 보존**한다.
- **재기동 복구**: 기동 시 `recover_on_start()`가 감사 JSONL을 읽어 마지막 상태가
  `accepted`/`running`인 잡을 `failed(reason="restart")`로 확정한다(침묵 유실 금지).
- dispatcher의 decision 감사(토큰·비용·판정·시그니처)는 `audit_path` 미설정 시 로그로만 남는다.

---

## 4. 설정 (`AgentSettings`)

`sre_agent/sre_agent/settings.py`. **`.env` → `.encenv`를 CWD 기준으로** 읽는다.
설정 유무 판정은 **pydantic 필드로만** 한다 — `os.getenv()` 금지(CLAUDE.md Known Mistakes).

| 필드 | env | 기본값 | 설명 |
|---|---|---|---|
| `model` | `MODEL` | `anthropic/claude-sonnet-5` | 운영 LLM(litellm 표기) |
| `api_key` | `API_KEY` | `None` | 운영 LLM 키(SecretStr) |
| `api_base` | `API_BASE` | `None` | 사내 OpenAI 호환 엔드포인트(vLLM 등) |
| `max_steps` | `MAX_STEPS` | `40` | ReAct step 상한 |
| `investigation_llm_model` | `INVESTIGATION_LLM_MODEL` | `gemini/gemini-3.5-flash` | 개발·테스트 LLM(D-120) |
| `gemini_api_key` | `GEMINI_API_KEY` \| `LLM_GEMINI_API_KEY` | `None` | **이 값이 None이면 조사는 스텁** |
| `polestar_mcp_url` | `POLESTAR_MCP_URL` | `http://localhost:9099/sse` | mcp_server SSE 엔드포인트 |
| `polestar_mcp_token` | `POLESTAR_MCP_TOKEN` | `None` | mcp_server Bearer(D-125) |
| `service_bearer_token` | `SERVICE_BEARER_TOKEN` | `None` | 조사 서비스 자체 인증. None이면 무인증 |
| `investigation_timeout_seconds` | — | `300` | 조사 1건 전체 타임아웃 |
| `investigation_dedup_ttl_seconds` | — | `None` | fingerprint dedup TTL(off) |
| `investigation_max_concurrent` | — | `2` | 동시 조사 상한 |
| `investigation_hourly_budget` | — | `None` | 시간당 조사 상한(off) |
| `severity_judge_enabled` | — | `False` | 중요도 2차 판정 |
| `remediation_recommender_enabled` | — | `False` | 조치 권고 |

**주의**: `.env` 계열 파일에 **인라인 주석 금지**(주석은 별도 줄). pydantic-settings가 값에
포함시킨다. list/dict 필드는 JSON 배열 형식(`["a","b"]`)으로 쓴다.

`prometheus_url`은 여기 없다 — Prometheus 접속 설정은 **`mcp_server` 측으로 일원화**했다(D-119).

**조사 LLM은 `model`·`api_key`·`api_base` 3필드로 정해진다** — `investigation_llm_model`이
아니다(§5.6 함정 2). 백엔드별 배선(vLLM 운영 / Gemini 개발)은 **§5.6**을 본다.

---

## 5. 구동 방법

### 5.1 전제

```bash
# Python 3.13 (실측: 3.13.1) · 자체 venv — 본체 .venv와 절대 섞지 않는다
cd /Users/cptkang/AIOps/collectorinfra/sre_agent
.venv/bin/python --version          # → Python 3.13.1
```

의존 패키지가 없다면:

```bash
cd sre_agent
python3.13 -m venv .venv
.venv/bin/pip install -e ".[dev]"   # holmesgpt>=0.36.0, pydantic-settings, pytest, ruff
```

**조사 LLM 백엔드는 별도 배선이 필요하다(§5.6).** 안 하면 서비스는 정상 기동하지만 조사는
`status="stub"`으로만 응답한다 — 기동 성공과 조사 가능은 별개다.

### 5.2 서비스 기동

```bash
# ★ 반드시 sre_agent/ 안에서 기동한다 (5.3 참조)
cd sre_agent
.venv/bin/python -m sre_agent.run_service
# → sre_agent 조사 서비스 기동: http://127.0.0.1:9098/sse (인증=off)
```

기동 순서는 `run_service.main()`이 고정한다:
① 감사 JSONL 재기동 복구 → ② Bearer 인증 미들웨어를 씌운 ASGI 앱 조립 → ③ uvicorn 서빙.
**엔트리는 `run_service` 하나뿐이다** — 수신부·게이트 엔트리는 본체 통합으로 소멸했다.

인증을 켜려면 `sre_agent/.env`에 `SERVICE_BEARER_TOKEN=<토큰>`을 넣고, 본체 `.env`의
`NOISE_INVESTIGATION_SERVICE_TOKEN`을 같은 값으로 맞춘다(D-125).

### 5.3 ★ CWD가 과금을 가른다 (D-127)

> **보충(§5.6)**: 여기서 갈리는 것은 **스텁 게이트**다. 게이트를 벗어나도 `MODEL`·`API_KEY`가
> 없으면 기본값(`anthropic/claude-sonnet-5`·무키)으로 호출을 시도해 실패한다 — 실측 표는 §5.6.

`AgentSettings`는 **CWD 기준**으로 `.env`/`.encenv`를 읽고, `gemini_api_key`는
`AliasChoices("GEMINI_API_KEY", "LLM_GEMINI_API_KEY")`로 해석된다.
레포 루트의 `.encenv`에는 `LLM_GEMINI_API_KEY`가 **존재**한다.

```bash
# CWD=sre_agent/  → key set: False  → 스텁 경로(과금 0)
cd sre_agent && .venv/bin/python -c \
  "from sre_agent.settings import AgentSettings; print('key set:', AgentSettings().gemini_api_key is not None)"

# CWD=레포 루트   → key set: True   → 실 조사 경로(과금 발생)
sre_agent/.venv/bin/python -c \
  "import sys; sys.path.insert(0,'sre_agent'); from sre_agent.settings import AgentSettings; print('key set:', AgentSettings().gemini_api_key is not None)"
```

**레포 루트에서 `run_service`를 기동하면 승인 없이 실 Gemini 조사가 돈다.**
과금 없는 배관 검증을 의도한다면 `cd sre_agent` 후 기동하거나 키를 명시적으로 비운다:

```bash
GEMINI_API_KEY= LLM_GEMINI_API_KEY= .venv/bin/python -m sre_agent.run_service   # 강제 스텁
```

스텁은 침묵하지 않는다 — `status="stub"`, `verdict="조사 미실행 — LLM 키 부재(스텁)"`로
감사에 남는다(`investigation_dispatcher.py` `_finalize_stub`).

> 과금이 발생하는 외부 API 호출은 **실행 건마다 사용자 승인**이 필요하다(D-127·포괄 승인 없음).
> `RUN_E2E=1` 설정 자체도 승인 후에만 한다.

### 5.4 함께 띄워야 하는 프로세스

**어디까지 띄우느냐는 무엇을 검증하느냐로 정해진다.**

| 검증 대상 | `sre_agent`<br>(9098) | `mcp_server`<br>(9097) | 본체 API 서버<br>(8050) | 알람 수신부<br>(TCP 9100) | Redis |
|---|---|---|---|---|---|
| `sre_agent` 단독(도구 계약·스텁) | ✅ | — | — | — | — |
| 실 데이터 조사(§6.3 직접 호출) | ✅ | ✅ | — | — | — |
| **챗 장애 진단**(pull · §6.2) | ✅ | ✅ | ✅ | — | — |
| **알람 자동 조사**(push · §6.1) | ✅ | ✅ | ✅ | ✅ | ✅ |

#### `noise_gate`는 별도 서버를 띄우는가 — **반만 그렇다**

`noise_gate`는 한 패키지지만 **런타임이 둘로 갈린다**(D-048 · D-139).

| 구성 | 실행 형태 | 별도 구동 | 기동 명령 |
|---|---|---|---|
| **게이트·분석 워커**<br>(`application/` · `domain/` · `orchestration/`) | **본체와 같은 프로세스·같은 venv**에서 in-process | **불필요** | `python -m src.main --server`에 포함 |
| **알람 수신부**<br>(`alarm_server/`) | **독립 프로세스** | **필요** | `python -m noise_gate.alarm_server` |

- 게이트·워커는 본체 API 서버가 `ALARM_ENABLED=true`일 때 `AlarmWorker`를 asyncio 태스크로
  띄운다(`src/api/server.py:378~385`). 기동 로그 `알람 분석 워커 시작 (stream=alarm:raw)`가
  그 증거다. **`sre_agent`처럼 별도 venv·별도 포트를 갖지 않는다** — 그래서
  `noise_gate/pyproject.toml`에도 자체 venv가 없다.
- 수신부만 독립 프로세스다. 폴스타 push를 TCP 9100으로 받아 **Redis Stream `alarm:raw`에
  적재**만 하는 자립 모듈(`src.` import 0)이라 수신 경계로 따로 세운다.
- 즉 **알람이 흐르는 경로는 `수신부 → Redis → 워커(본체 in-process) → 게이트 → 조사 트리거`** 이고,
  Redis가 두 프로세스를 잇는 유일한 매개다.

```bash
# [CWD=레포 루트 · .venv] — 알람 자동 조사(push)를 검증할 때만 필요
python -m src.main --server            # 본체 API + 게이트·워커(in-process)
python -m noise_gate.alarm_server      # 별 터미널 — TCP 9100 → Redis Stream alarm:raw
```

> **★ Redis 포트 불일치 함정**: 워커는 `REDIS_PORT`(레포 `.env` 실측 **6380**)를 읽고,
> 수신부는 `ALARM_SERVER_REDIS_PORT`(**기본 6379**)를 읽는다. `.env`에 후자가 없으면
> 수신부가 6379에 적재하고 워커는 6380을 보므로 **알람이 조용히 흐르지 않는다**.
> 반드시 `ALARM_SERVER_REDIS_PORT=6380`을 명시한다(`docs/23` §0.2).

> **챗 진단(pull)에는 수신부·Redis가 필요 없다.** `fault_diagnosis` 노드는 알람 스트림을
> 거치지 않고 본체 그래프에서 곧장 `sre_diagnose`를 호출한다.

#### `mcp_server` 조사 프로파일

실 데이터로 조사하려면 **`mcp_server`의 조사 프로파일 인스턴스**가 먼저 떠 있어야 한다.
`mcp_server`는 전용 venv가 없다(2026-08-25 실측) — 레포 루트 `.venv`에 `PYTHONPATH`를 얹어 띄운다.

```bash
# [CWD=레포 루트 · .venv] — 조사 프로파일(9097). 본체용 9099와 별도 인스턴스다.
POLESTAR_CONNECTION='postgresql://…@localhost:5434/infradb' \
PROMETHEUS_URL='http://localhost:9190' \
EXPOSE_EXECUTE_SQL=false \
EXPOSE_RAW_PROMQL=false \
EXPOSE_POLESTAR_TOOLS=true \
SERVER_PORT=9097 \
PYTHONPATH="$PWD/mcp_server" .venv/bin/python -m mcp_server
```

- **조사 배치는 `expose_execute_sql`·`expose_raw_promql`를 반드시 `false`** 로 둔다(D-122) —
  원시 SQL/PromQL을 열면 LLM이 방언 오류로 step을 소진한다. 레포의 `mcp_server/config.toml`은
  본체 NL→SQL 파이프라인용이라 `expose_execute_sql = true`이므로, 위처럼 **환경변수로 덮어
  별도 인스턴스**를 띄운다.
- **`PROMETHEUS_URL` 미설정이면 PromQL 도구가 전건 실패**한다(침묵 폴백 금지·명시적 오류).
  Prometheus 구성·도구 레퍼런스·운영 편입 절차는 `docs/27_prometheus_integration_guide.md` 참조.
  `mcp_server/.env`에는 이 키가 없다(2026-08-25 실측).
- 노출되는 것은 **폴스타 고수준 8종 + PromQL 고수준 2종**(`prom_metric_instant`·
  `prom_metric_range` — 서버측이 `{nodename="…"}`를 조립하는 hostname 앵커)이다.
- 조사 서비스가 이 인스턴스를 보도록 `sre_agent/.env`에
  `POLESTAR_MCP_URL=http://<host>:9097/sse`를 설정한다(기본값은 9099).

엔드투엔드(알람 수신부 · 본체 서버 · Docker 픽스처 포함) 기동은 `docs/23` §0.2·§3을 따른다.

### 5.5 본체 배선 플래그 (전부 기본 off · 회귀 0)

본체 `.env`에 `NOISE_` prefix로 설정한다(`src/config.py:843~880`).

| 플래그 | 기본 | 켜면 |
|---|---|---|
| `NOISE_INVESTIGATION_TRIGGER_ENABLED` | `false` | 게이트 PAGE 결정 직후 자동 조사 트리거(push) |
| `NOISE_INVESTIGATION_TRIGGER_MIN_TIER` | `PAGE` | 이 티어 이상에서만 트리거 |
| `NOISE_INVESTIGATION_SERVICE_URL` | `http://localhost:9098/sse` | 조사 서비스 엔드포인트 |
| `NOISE_INVESTIGATION_SERVICE_TOKEN` | `""` | 정적 Bearer(서비스 측과 일치해야 함) |
| `NOISE_INVESTIGATION_TOTAL_TIMEOUT_SECONDS` | `45.0` | submit+poll **전체** 타임아웃 |
| `NOISE_INVESTIGATION_FOLLOWUP_ENABLED` | `false` | 즉시 통보 + 브리핑 후속 발송(지연 0) |
| `NOISE_FAULT_DIAGNOSIS_ENABLED` | `false` | 챗 `fault_diagnosis` 의도 → `sre_diagnose` 위임(pull) |
| `NOISE_FAULT_ESCALATION_ENABLED` | `false` | `verdict.escalate=True`면 통보에 상향 안내 첨부 |

2026-08-28 실측 기준 레포 `.env`에는 이 키들이 없다 → **전부 기본값(off)** 이다.

---

### 5.6 조사 LLM 백엔드 — 별도 vLLM 구축·연결 (운영 경로)

**왜 vLLM인지는 §1.3.** 여기서는 서버 B에 vLLM을 세우고 `sre_agent`가 그것을 호출하게
만드는 **전 과정**을 순서대로 다룬다. 각 단계마다 판정을 두는 이유는, vLLM이 tool-calling에
실패할 때 **200 응답을 주면서 `tool_calls`만 비는 형태로 조용히 실패**하기 때문이다 —
판정 없이 다음 단계로 넘어가면 최종 증상(조사가 답만 장문으로 내놓음)에서 원인을 역추적하게 된다.

```
[서버 A] sre_agent 조사 서비스 ──── outbound 8000/TCP ────► [서버 B] vLLM /v1
  HolmesGPT ReAct 루프(CPU)                                    LLM 추론(GPU)
```

방화벽은 **A→B 단방향 8000/TCP** 하나면 된다(B는 inbound만·outbound 없음).

#### 5.6.1 vLLM 서버 세우기 (서버 B)

```bash
# [서버 B(GPU) · CWD 무관 · vLLM 환경]
pip install vllm

python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen3.5-9B-Instruct \
  --served-model-name Qwen3.5-9B \
  --host 0.0.0.0 --port 8000 \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  --max-model-len 32768
```

| 플래그 | 왜 필요한가 |
|---|---|
| `--enable-auto-tool-choice` | **없으면 `tool_calls`가 아예 안 나온다.** vLLM은 `/v1`이 OpenAI 호환이어도 기본값으로는 도구를 만들지 않는다 |
| `--tool-call-parser` | 모델이 낸 도구 호출 텍스트를 파싱하는 규칙. **모델 계열·vLLM 버전마다 다르다**(`hermes`·`llama3_json`·`mistral`·`qwen3_coder` 등) — 문서 값을 믿지 말고 §5.6.3으로 실측 확인한다 |
| `--served-model-name` | `sre_agent`의 `MODEL`에 넣을 이름. 생략하면 `--model` 경로 전체가 이름이 된다 |
| `--max-model-len` | ReAct는 **매 스텝마다 이전 도구 결과를 누적**해 보낸다. 폴스타 도구 반환이 JSON이라 특히 빨리 찬다 — 작으면 조사 중반부터 컨텍스트 초과로 끊긴다 |

> **모델 선택 주의**: D-037이 쓰는 Qwen3.5-9B는 *"제어평면에 계획 신호만"* 목적으로 고른
> 소용량 모델이다. 조사는 **도구 10종 안팎 × 최대 `MAX_STEPS`(기본 40) 왕복**으로 훨씬 무겁다.
> 9B로 시작하되 **§5.6.6 완주 판정을 반드시 통과시키고**, 미완주가 반복되면 모델을 올린다.

#### 5.6.2 서빙 확인 (서버 A에서)

```bash
# [서버 A] 도달성 + 모델 이름 확인 — 여기 실패면 방화벽·바인드 문제다
curl -s http://<vllm-host>:8000/v1/models | python3 -m json.tool
```

`data[].id`가 `--served-model-name`과 같아야 한다. 이 값이 곧 `MODEL`에 들어간다.

#### 5.6.3 ★ tool-calling 왕복 판정 (HolmesGPT를 붙이기 **전에**)

목업 도구 1개로 왕복만 본다. **여기서 막히면 그 위 단계는 전부 무의미하다.**

```bash
# [서버 A · CWD=레포 루트 · sre_agent/.venv]
VLLM_BASE=http://<vllm-host>:8000/v1 VLLM_MODEL=Qwen3.5-9B \
sre_agent/.venv/bin/python - <<'PY'
import json, os, litellm

TOOL = {"type": "function", "function": {
    "name": "get_server_cpu_load",
    "description": "지정한 서버의 현재 CPU 사용률(%)을 반환한다. (판정용 목업 도구)",
    "parameters": {"type": "object",
                   "properties": {"hostname": {"type": "string", "description": "대상 서버 호스트명"}},
                   "required": ["hostname"]}}}

resp = litellm.completion(
    model=f"openai/{os.environ['VLLM_MODEL']}",
    api_base=os.environ["VLLM_BASE"],
    api_key="dummy",
    messages=[{"role": "user", "content": "server-01 서버의 현재 CPU 사용률을 확인해줘."}],
    tools=[TOOL], tool_choice="auto",
)
msg = resp.choices[0].message
calls = getattr(msg, "tool_calls", None)
if not calls:
    print("불합격 — tool_calls 없음. 본문:", (msg.content or "")[:300])
else:
    c = calls[0]
    print("합격 — 호출:", c.function.name, "| 인자:", json.loads(c.function.arguments))
PY
```

| 결과 | 원인 | 조치 |
|---|---|---|
| `합격 — 호출: get_server_cpu_load …` | — | 다음 단계로 |
| `불합격` + 본문이 **설명문** | `--enable-auto-tool-choice` 누락 | 플래그 추가 후 재기동 |
| `불합격` + 본문에 **도구 JSON 조각** | `--tool-call-parser` 불일치 | 파서 값을 모델 계열에 맞게 교체 |
| 연결 오류 | 방화벽·바인드 | §5.6.2부터 |

#### 5.6.4 `sre_agent` 배선 — 필드 4개

```bash
# sre_agent/.env — 인라인 주석 금지(주석은 별도 줄)
MODEL=openai/Qwen3.5-9B
API_BASE=http://<vllm-host>:8000/v1
API_KEY=dummy
GEMINI_API_KEY=dummy
```

| env | 값 | 설명 |
|---|---|---|
| `MODEL` | `openai/<served-model-name>` | **`openai/` 접두사 필수** — litellm이 OpenAI 호환 경로로 보내게 하는 신호다. 붙이지 않으면 프로바이더를 못 고른다 |
| `API_BASE` | `http://<host>:8000/v1` | **`/v1`까지** 포함 |
| `API_KEY` | 아무 값 | vLLM 무인증이면 미검증. 다만 `None`이면 litellm이 인증 헤더 없이 보내 400이 날 수 있다 |
| `GEMINI_API_KEY` | 아무 값 | **스텁 게이트 통과용** — 함정 1 참조. vLLM 경로에서 Gemini를 호출하지는 않는다 |

**배선 도달 확인** — 설정이 실제로 holmes `Config`까지 갔는지 본다(추정 금지).

```bash
# [서버 A · CWD=sre_agent · sre_agent/.venv]
cd sre_agent && .venv/bin/python -c "
from sre_agent.settings import AgentSettings
from sre_agent.diagnosis import DiagnosisAgent
c = DiagnosisAgent(AgentSettings())._config
print('model   =', c.model)      # → openai/Qwen3.5-9B
print('api_base=', c.api_base)   # → http://<vllm-host>:8000/v1
print('api_key =', 'set' if c.api_key is not None else 'None')
"
```

`model`이 `anthropic/claude-sonnet-5`로 나오면 **`MODEL`이 안 먹은 것**이다 — 함정 2를 본다.

#### 5.6.5 조사 서비스 기동

```bash
# [서버 A · CWD=sre_agent · sre_agent/.venv]
cd sre_agent
.venv/bin/python -m sre_agent.run_service
# → sre_agent 조사 서비스 기동: http://127.0.0.1:9098/sse (인증=off)
```

`.env`에 넣는 대신 기동 명령에 얹어도 된다(CWD가 레포 루트일 때 권장 — §5.3):

```bash
# [서버 A · CWD=레포 루트 · sre_agent/.venv]
MODEL="openai/Qwen3.5-9B" \
API_BASE="http://<vllm-host>:8000/v1" \
API_KEY=dummy \
GEMINI_API_KEY=dummy \
LLM_GEMINI_API_KEY= \
POLESTAR_MCP_URL=http://localhost:9097/sse \
MAX_STEPS=40 \
sre_agent/.venv/bin/python -m sre_agent.run_service
```

`LLM_GEMINI_API_KEY=`로 비우는 것은 `.encenv` 키가 함께 잡히는 것을 막기 위해서다(§5.3).
`POLESTAR_MCP_URL`은 **조사 프로파일 인스턴스**(9097 권장)를 가리켜야 한다 — 본체용 9099를
그대로 쓰면 `execute_sql`이 노출돼 LLM이 raw SQL 방언 오류로 step을 소진한다(D-122).

#### 5.6.6 조사 완주 판정 — 여기까지 통과해야 "된다"

§5.6.3은 **왕복 1회**만 본 것이다. ReAct 다단계 완주는 별개다.

```bash
# [서버 A · CWD=레포 루트 · sre_agent/.venv] — 픽스처 대상. vLLM은 사내라 외부 과금 없음
RUN_E2E=1 sre_agent/.venv/bin/python -m pytest sre_agent/tests/test_investigation_e2e.py -v
```

`mcp_server`(조사 프로파일)가 도달 가능해야 하고, 게이팅 때문에 `GEMINI_API_KEY`가 채워져 있어야 한다.

| 관측 | 의미 · 조치 |
|---|---|
| `status="done"` + 브리핑 6요소 + **도구 인용** | ✅ 합격 |
| `incomplete=True` 반복 | 모델이 ReAct를 못 끌고 감 → **모델 상향**(9B→상위), `MAX_STEPS` 확인 |
| 도구 호출 0회인데 답변만 장문 | 도구를 안 쓰고 지어냄 → 파서 재확인(§5.6.3). **인용 없으면 불합격** |
| 컨텍스트 초과 오류 | `--max-model-len` 상향 |
| 수백 초 소요 | 통보가 그만큼 지연된다 → 본체에서 `NOISE_INVESTIGATION_FOLLOWUP_ENABLED=true`(§5.5) |

**참고 기준치**(Gemini 3.5-flash · 2026-07-28 실측): 완주 161초 · PromQL 감사 37건.
소용량 vLLM은 이보다 느리고 스텝을 더 쓴다 — **절대 비교가 아니라 완주 여부로 판정**한다.

#### 5.6.7 본체(게이트)에 연결

조사 서비스가 서 있어도 본체 플래그가 off면 트리거되지 않는다(§5.5).

```bash
# .env — 인라인 주석 금지
NOISE_INVESTIGATION_TRIGGER_ENABLED=true
NOISE_INVESTIGATION_SERVICE_URL=http://localhost:9098/sse
NOISE_INVESTIGATION_TOTAL_TIMEOUT_SECONDS=45.0
```

전체 타임아웃 기본 45초는 **실 LLM 조사에는 짧다**(§5.6.6 기준치 161초). 즉시 통보를 유지하려면
타임아웃을 늘리는 대신 `NOISE_INVESTIGATION_FOLLOWUP_ENABLED=true`로 **후속 발송**을 쓴다.

#### 5.6.8 ★ 함정 1 — `GEMINI_API_KEY`가 비면 vLLM이 멀쩡해도 조사가 안 돈다

스텁 게이트가 **`gemini_api_key is None` 단일 조건**이다
(`application/investigation_dispatcher.py:216`·`:434`). 백엔드를 vLLM으로 바꿔도 이 필드는
"실 조사를 켜는 스위치" 역할로 남아 있어서, 비어 있으면 `status="stub"`으로 떨어진다.
**값 자체는 조사에 쓰이지 않으므로 아무 문자열이면 된다** — vLLM 경로에서는 Gemini를 호출하지
않는다. 중립 이름(`investigation_api_key`) 개명은 별건 결정으로 미뤄져 있다(Plan 66 §7-1 부기).

#### 5.6.9 ★ 함정 2 — `INVESTIGATION_LLM_MODEL`은 조사 모델이 아니다

이름과 달리 **운영 조사 경로가 읽지 않는다.** `DiagnosisAgent`가 holmes `Config`에 넘기는 값은
`model`/`api_key`/`api_base`다(`diagnosis.py:136-139`). `investigation_llm_model`을 읽는 곳은
`scripts/smoke_llm.py`와 테스트뿐이다(2026-08-28 전수 grep).

**실측** — `INVESTIGATION_LLM_MODEL`로 vLLM 모델을 지정하고 `DiagnosisAgent`를 만들었을 때:

| | 값 |
|---|---|
| `settings.investigation_llm_model` | `openai/Qwen3.5-9B` (지정한 값) |
| **`Config.model`** (실제 호출 모델) | **`anthropic/claude-sonnet-5`** ← 기본값 그대로 |
| `Config.api_base` | `http://vllm-host:8000/v1` |

⇒ **api_base만 vLLM을 가리키고 모델은 Anthropic**이라, litellm이 anthropic 프로바이더로
vLLM 엔드포인트에 붙으려다 실패한다. `MODEL`로 주면 `Config.model`이 정상 도달한다(§5.6.4 확인 절차).

> **`docs/23` §7-V.4 정정(2026-08-28)**: 그 기동 명령이 `INVESTIGATION_LLM_MODEL`을 쓰고 있어
> 그대로 따르면 위 증상이 난다. 해당 절은 `MODEL`로 정정했다.

| 바꾸려는 것 | 만질 env |
|---|---|
| **실제 조사가 쓰는 모델** | `MODEL` (+ `API_BASE` · `API_KEY`) |
| `scripts/smoke_llm.py` 스모크 모델 | `INVESTIGATION_LLM_MODEL` |
| 실 조사 on/off(스텁 여부) | `GEMINI_API_KEY` 유무 |

#### 5.6.10 개발·테스트(Gemini) 배선 — `.encenv` 키만으로는 조사가 돌지 않는다 (2026-08-28 실측)

```bash
MODEL=gemini/gemini-3.5-flash
API_KEY=<gemini 키>
```

`.encenv`의 `LLM_GEMINI_API_KEY`는 **스텁 게이트만 통과시킨다.** 실제 호출에 쓰이는 값은
`model`·`api_key`이고, pydantic의 `env_file` 로딩은 **`os.environ`에 주입되지 않아서**
litellm의 환경변수 폴백(`GEMINI_API_KEY`)도 걸리지 않는다.

레포 루트에서 아무 설정 없이 `AgentSettings()`를 만든 실측값:

| 필드 | 값 | 결과 |
|---|---|---|
| `gemini_api_key` | set (`.encenv`의 `LLM_GEMINI_API_KEY`) | 스텁 게이트 **통과** → 실 조사 경로 진입 |
| `model` | `anthropic/claude-sonnet-5` (기본값) | **Gemini가 아니다** |
| `api_key` | `None` | 키 없음 |
| `os.environ["GEMINI_API_KEY"]` | 없음 | litellm 폴백도 불가 |

⇒ **스텁은 벗어나지만 Anthropic 무키 호출로 실패한다.** §5.3의 "루트에서 띄우면 실 조사"는
*스텁 게이트를 벗어난다*는 뜻이며, 조사가 실제로 돌려면 **`MODEL`·`API_KEY`(또는 vLLM 3필드)를
반드시 함께 준다.** 이 경로는 **실 폴스타 데이터 금지**다(D-120 · §8-4).

---

## 6. 사용법

### 6.1 알람 자동 조사 (push 경로)

```
게이트 판정(PAGE) → investigation_trigger 노드 → sre_investigate_alarm(payload)
   → poll(sre_get_investigation) → 브리핑을 통보에 첨부 → decision_store 감사
```

- **전제 프로세스**: 알람 수신부(별도) + Redis + 본체 서버(게이트·워커 in-process) + 조사 서비스.
  게이트·워커는 별도로 띄우지 않는다 — §5.4 참조.
- 노드: `noise_gate/application/nodes/investigation_trigger.py`
- 페이로드 직렬화: `noise_gate/domain/investigation_payload.py`
- 클라이언트: `noise_gate/infrastructure/sre_agent_client.py`
- **호스트 인가 게이트**가 위임 직전에 판정한다 — 조회 권한 ≠ 조사 권한.
- 서비스 다운·타임아웃·거부는 **graceful**: 브리핑만 빠지고 게이트 통보·판정은 정상 완료하며
  사유가 감사에 남는다(침묵 폴백 금지).
- 실 LLM 조사는 수십~수백 초라 PAGE 통보가 그만큼 지연된다. `FOLLOWUP_ENABLED=true`면
  submit까지만 하고 즉시 통보한 뒤 백그라운드가 브리핑을 **후속 메시지로 별도 발송**한다.

### 6.2 챗 장애 진단 (pull 경로)

사용자가 "○○ 서버 원인 분석해줘"류로 요청 → 시멘틱 라우터가 `fault_diagnosis` 의도로 분류
→ `src/nodes/fault_diagnosis.py`가 `sre_diagnose(question, server_name?, hostname?, db_id?)`
위임 → poll → 자연어 진단 응답 반환. 연결 설정은 push 경로와 **동일 서비스**를 재사용한다.

### 6.3 MCP 도구 직접 호출 (수동 확인)

```python
# CWD=sre_agent/ · sre_agent/.venv — 서비스를 띄우지 않고 JobStore를 직접 두드리는 최소 경로
from sre_agent.settings import AgentSettings
from sre_agent.interface.mcp_service import create_service, get_job_store

store = get_job_store(create_service(AgentSettings()))
res = store.submit({
    "contract_version": "1",
    "event": {"serverName": "WEB-01", "hostname": "web01.example.com", "severity": 3},
    "decision": {"tier": "PAGE", "fingerprint": "fp-demo"},
})
print(res)                                   # {'investigation_id': '...', 'status': 'accepted'}
print(store.get(res["investigation_id"]))    # status/briefing/verdict/tokens/cost
```

기동한 서비스를 네트워크로 확인하려면 `sre_health`를 부른다 — `holmes_ready=false`면
LLM 키가 없어 조사가 스텁으로 떨어지는 상태다.

### 6.4 조사 결과 스키마

```json
{
  "investigation_id": "…", "kind": "alarm", "status": "done",
  "fingerprint": "…",
  "verdict": "심각(신뢰도 high) escalate=True",
  "briefing": {
    "severity": {"level": "심각", "confidence": "high", "escalate": true,
                 "gate_tier": "PAGE", "signals": ["oom_kill"], "evidence_insufficient": false},
    "summary": "…", "timeline": ["…"], "bottleneck": "메모리 고갈(OOM Killer)",
    "cause": "…",
    "recommendation": {"items": ["…"], "note": "※ 실행은 운영자 승인 후 수동 — …"},
    "limitations": ["프로세스·자원 스냅샷은 조사 시점 단면일 수 있음"],
    "citations_verified": true, "hypotheses": []
  },
  "tool_calls_summary": ["…"], "tokens": 12345, "cost": 0.0123,
  "error": null, "reason": null, "created_at": 0.0, "updated_at": 0.0
}
```

---

## 7. 개발 · 검증

**모든 명령은 `sre_agent/.venv/bin/python`으로 실행한다**(본체 venv와 섞지 않는다).

```bash
cd sre_agent

# 단위·계약 테스트 — 실 LLM 호출 없음
.venv/bin/python -m pytest tests -q
# 실측 2026-08-28: 247 passed, 2 skipped (6.53s)

# 계층 게이트 (위반 시 exit 1)
.venv/bin/python scripts/arch_check.py --ci

# Gemini tool-calling 스모크 — 키 없으면 "보류(GEMINI_API_KEY 미설정)" 출력 후 exit 0
.venv/bin/python scripts/smoke_llm.py
```

**과금이 걸리는 검증(사용자 승인 필수 · D-127)**

```bash
RUN_E2E=1 .venv/bin/python -m pytest tests/test_investigation_e2e.py -q   # 실 조사 완주 e2e
RUN_E2E=1 python sre_agent/scripts/ab_promql_gate.py --trials 2           # D-119 PromQL A/B 게이트
```

`ab_promql_gate.py`는 Docker 픽스처(Prometheus 9190)의 고정값(cpu user 97.5 / system 1.5 /
memory 8GiB / oom_kills 3)을 조사 서술이 집어냈는지 **문자열로 대조**한다 — LLM을 심판으로
쓰지 않는다(D-035).

**주요 테스트 파일**

| 파일 | 고정하는 계약 |
|---|---|
| `test_boundary.py` | D-118 양방향 import 0 |
| `test_payload_contract.py` | `contract_version`·필수 필드 |
| `test_investigation_jobs.py` | 상태 전이·dedup·sweep·재기동 복구·감사·스텁 명시성 |
| `test_investigation_dispatcher.py` | 가드 5종·sweep·감사 |
| `test_load_guard.py` / `test_load_guard_l4.py` | 부하 가드 L-1~L-3 / L-4 |
| `test_severity_signatures.py` | 시그니처 표·escalate-only·증거 불충분 |
| `test_briefing_builder.py` | 6요소·가설 강등·human-gated·한계 강제 |
| `test_remediation.py` | **실행 경로 부재**(명령 리터럴 차단) |
| `test_mcp_service.py` | 도구 5종 계약·Bearer 미들웨어 |
| `test_remote_vm_profile.py` / `test_middleware_profile.py` | 원격 프로파일·`mcp_servers` / D-168 |

---

## 8. 제약 · 주의

1. **읽기 전용 · 조치 없음(D-003 · D-011)** — 조사는 읽기 명령만 하고, 권고는 문자열로만 낸다.
   실행 코드 경로가 어느 배치에도 존재하지 않으며 테스트가 그 부재를 고정한다.
2. **과금 승인 게이트(D-127)** — Gemini 등 과금 API는 **건별 사용자 승인** 없이 호출 금지.
   `RUN_E2E=1` 설정도 승인 후에만. CWD로 키가 잡히는 함정(§5.3)을 항상 먼저 확인한다.
3. **조사 LLM은 네이티브 tool-calling이 필수다** — holmes는 매 호출에 `tools`/`tool_choice`를
   싣고 거부 시 예외로 끝나며 **프롬프트 폴백이 없다**. 사내 FabriX(KBGenAIChat)는 OpenAI
   비호환·도구 미전송으로 **조사 LLM 불가가 확정**이며, 운영 경로는 **별도 vLLM**이다(§1.3 · §5.6).
4. **데이터 통제(D-120)** — Gemini는 외부 SaaS다. **개발·테스트 전용이며 운영 투입 금지**.
   외부 송신 입력은 목업·로컬 픽스처만 허용한다. 실 폴스타 데이터로 조사할 때의 LLM 선택은
   규정 문제이며 `docs/23` §8.0의 판단을 따른다.
5. **경계 불변식(D-118)** — 어느 방향으로든 import를 만들지 않는다. 새 기능은 MCP 계약 확장으로
   푼다. 계약을 바꾸면 `contract_version`을 올리고 구버전 1개 병행 수용을 검토한다.
6. **관측 경계 일원화(D-119)** — 대상 VM 데이터는 `mcp_server`(9099) 경유로만 얻는다.
   holmesgpt 내장 `prometheus/metrics` toolset 직결은 미채택이며 `remote_vm_profile()`이 이를 끈다.
7. **LLM 비결정성** — 브리핑 서술은 인용 검증 후처리를 거치지만 여전히 LLM 산출물이다.
   판정·권고·가드는 전부 코드 소관이며, 이 분담을 흐리는 변경(예: LLM에 중요도 판정을 맡김)은
   D-035 위반이다.
8. **잔여 한계** — L-4 호스트 키의 `hostname`/`serverName` 이원화(§3.5), `sre_health`의
   `polestar_mcp_reachable` 라이브 프로브 미구현(항상 `None`).

---

## 9. 참조

| 문서 | 내용 |
|---|---|
| `docs/23_plan66_mvp_test_guide.md` | **엔드투엔드 기동·테스트 정본**(프로세스 배치·터미널 구성·과금 레벨) |
| `docs/25_host_investigation_load_guard.md` | 부하 가드 L-1~L-4 요구 계약 정본 |
| `docs/24_middleware_profile_spec.md` | 미들웨어 판정 스펙(D-168) |
| `docs/02_decision.md` | D-118~D-127 · D-168 등 의사결정 원문 |
| `plans/sre-agent/02-incident-investigation-holmesgpt.md` | 조사 본체 계획(중심) |
| `plans/sre-agent/05-collectorinfra-interop.md` | 서비스 경계·submit/poll 계약 기준 문서 |
| `plans/sre-agent/04-polestar-mcp-integration.md` | mcp_server 고수준 도구 확장 |
| `plans/sre-agent/06-remote-vm-access.md` | 원격 VM 2축 데이터 경로 |
| `sre_agent/README.md` | 패키지 최소 안내(경계 불변식·개발 명령) |
