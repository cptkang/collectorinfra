# 23. Plan 66 MVP 테스트 가이드 — 폴스타 알람 → 자동 조사 → 브리핑 첨부 (Gemini · FabriX 2종)

> **대상**: `plans/66-sre-agent-integrated-implementation-plan.md` Phase 3 완료 = **MVP** —
> *"PAGE 1건 → 자동 조사 1회 → 인용 있는 브리핑이 통보에 첨부"* 흐름을 폴스타 실계 없이 재현한다.
> **근거 결정**: D-118(`sre_agent` 독립 패키지)·D-119(PromQL `mcp_server` 통합)·D-120(개발·테스트 LLM=Gemini)·
> D-122(고수준 도구 8종)·D-123(조사 서비스 submit/poll)·D-124(게이트 훅 트리거)·D-125(Bearer)·D-126(PG 한정 검증)·
> **D-127(과금 API 건별 승인 게이트)**·D-137(즉시통보+후속 브리핑)·D-138(조치 권고)·D-139(패키지 배치).
> **실측일**: 2026-08-25 (포트·환경변수·파일 경로·설정 해석은 전부 당일 실행으로 확인).
> **선행 문서**: `docs/16`(Plan 52 게이트 E2E) · `docs/20`(Plan 60 기능 테스트) — 게이트 자체 검증은 그쪽이 정본이다.
> 본 문서는 **게이트 뒤에 붙는 조사·브리핑 구간**만 다룬다.
>
> **LLM 백엔드 2종**을 병행 정리한다 — **§7-G Gemini**(D-120 · 개발·테스트, 픽스처 한정)와
> **§7-V 사내 vLLM**(2026-08-25 채택 — 실연동·운영 후보). 레벨 A(스텁·과금 0)는 백엔드와
> 무관하므로 §0~§6은 공통이다.
> **사내 FabriX(KBGenAIChat)는 OpenAI 호환이 아니어서 HolmesGPT를 구동할 수 없다**(§2.2.1 실측
> 확정) — 그래서 사내 경로는 **별도 vLLM**으로 세운다(§7-V).
>
> **명령 실행 위치 표기(범례)** — 이 문서의 **모든** 명령·설정 블록은 첫 줄에 실행 위치를 단다:
> `# [서버 · CWD=… · 인터프리터]`. 서버 기호는 §3.3.1을 따른다 —
> **A**=에이전트 호스트(본체 API·워커·`sre_agent`·수신부), **B**=GPU 서버(vLLM),
> **C**=데이터 접근 호스트(`mcp_server` — 개발에서는 A와 동거).
> `★`가 붙은 블록은 **위치를 틀리면 동작하지 않거나 결과가 달라지는** 곳이다
> (예: `sre_agent` 기동 CWD가 과금을 가르고 §2.3, vLLM은 서버 B에서만 뜬다).
> 설정 블록은 CWD 대신 **어느 서버의 어느 파일**인지를 적는다.
>
> **프로세스·서버 배치는 §3** — 자체 프로세스 4개(본체 API+워커 / 알람 수신부 / `mcp_server` / `sre_agent`)
> + vLLM을 **어느 서버에서 띄우는지**(§3.3.1 서버 정의·§3.3.2 배치 지정표·§3.3.5 방화벽 방향), 인터프리터 2종, 포트, 파일 배치, 터미널.
> **`sre_agent`는 `127.0.0.1` 고정 바인드라 본체 API와 반드시 같은 서버**여야 한다(코드 실측).
> **데이터 소스 2종**: §4~§7은 **Docker 픽스처**, **§8은 실 폴스타 DB·실 Prometheus·실 알람 피드**다.
> **실데이터 전환 전 §8.0을 반드시 읽을 것** — D-120은 *실 폴스타 데이터의 외부 API(Gemini) 송신을
> 절대 제약으로 금지*하므로, **실연동의 조사 LLM은 사내 백엔드여야 한다**.

---

## 0. Quick Guide — 무과금 배관 검증 (5분 · 과금 0)

### 0.1 실행 기록은 **테스트 코드가 스스로 남긴다**

MVP 판정과 기록은 별도 실행 스크립트가 아니라 **테스트 코드 안**에 있다. 외부 스크립트가
감사 파일을 뒤져 판정을 재도출하면 판정 로직이 두 벌이 되고, pytest를 직접 돌렸을 때는
기록이 남지 않는다. 그래서 **어떤 방식으로 실행하든**(pytest·IDE·CI) 기록이 생긴다.

| 레벨 | 테스트 (= 판정 주체) | 기록기 |
|---|---|---|
| **A** 배관(과금 0) | `noise_gate/tests/test_mock_polestar_events.py::test_send_invest_trigger_e2e` | `noise_gate/tests/mvp_record.py` |
| **B** 실 조사 완주 | `sre_agent/tests/test_investigation_e2e.py::test_mvp_investigation_completes_or_graceful` | `sre_agent/tests/mvp_record.py` |

```bash
# 레벨 A — [서버 A · CWD=레포 루트 · .venv] · §0.2로 프로세스 4개를 띄운 뒤
RUN_E2E=1 .venv/bin/python -m pytest \
    noise_gate/tests/test_mock_polestar_events.py::test_send_invest_trigger_e2e -v

# 레벨 B — [서버 A · CWD=sre_agent/ · sre_agent/.venv] · vLLM(§7-V) 기동 + D-127 승인 후
cd sre_agent && RUN_E2E=1 .venv/bin/python -m pytest tests/test_investigation_e2e.py -v
```

남는 것:

| 위치 | 내용 | 수명 |
|---|---|---|
| `logs/mvp_test/runs.jsonl` | 런별 1줄 — 결과·소요·**환경 지문**·관측값 | **`logs/`는 gitignore → 로컬 한정** |
| **`logs/mvp_test/mvp_test_log.md`** | **실행 대장** — 런별 1행(사람이 읽는 누적 이력) | **`logs/`는 gitignore → 실행한 호스트에만** |

기록 규약:

- **실제로 실행된 MVP 테스트만 기록된다.** `RUN_E2E` 미설정으로 skip되면 남기지 않는다
  (기본 스위트가 매번 도는데 skip을 적으면 대장이 잡음으로 덮인다).
- **키 값은 절대 남기지 않는다** — `api_key_set: true/false`처럼 **설정 여부만** 남긴다.
- 지문에 **백엔드·모델·`api_base`**가 들어간다 — 나중에 *"어떤 모델로 완주했는가"*가
  §7-V.5 완주 판정의 핵심 근거가 되기 때문이다.
- 기록 실패는 테스트 결과를 바꾸지 않되 침묵하지 않는다(사유 출력).

> **기록기가 두 벌인 이유**: `sre_agent`는 별도 venv·**양방향 import 0** 경계(D-118/D-139)라
> collectorinfra 모듈을 import할 수 없다. 그래서 **공유하는 것은 모듈이 아니라 출력 파일
> 계약**이다 — `mcp_server`가 자체 미니 SQL 로거로 같은 `logs/sql/`에 append하는 D-140과
> 같은 판단이다.

### 0.2 수동 절차 (프로세스 기동 — 위 테스트의 전제)


LLM 키 없이 **전 구간 배관**(게이트 → 트리거 → submit → poll → 브리핑 첨부 → 감사)을 확인하는 최단 경로.
조사 내용만 스텁이고 나머지는 전부 운영 경로 그대로다.

```bash
# [서버 A(픽스처 호스트) · CWD=무관]
# ① 픽스처 기동 확인 (이미 떠 있으면 생략)
docker ps --format '{{.Names}}\t{{.Ports}}' | grep -E 'redis|polestar_pg|fixture_'
#   collectorinfra-redis  6380->6379   / polestar_pg 5434->5432
#   fixture_prometheus 9190->9090      / fixture_target_vm 9101->9100

# ② .env — 게이트·워커·트리거 활성 (인라인 주석 금지: pydantic-settings가 값에 포함시킨다)
ALARM_ENABLED=true
NOISE_ENABLE_NOISE_GATE=true
NOISE_INVESTIGATION_TRIGGER_ENABLED=true
ALARM_SERVER_REDIS_PORT=6380          # ★ 기본 6379 → 워커가 읽는 REDIS_PORT(6380)와 반드시 일치

# ③ 조사 서비스 기동 — **반드시 sre_agent/ 안에서** (레포 루트면 .encenv 키가 잡혀 과금 발생·§2)
cd sre_agent && .venv/bin/python -m sre_agent.run_service    # → 127.0.0.1:9098/sse

# ④ 본체 서버 기동 (워커 in-process) — 별 터미널, 레포 루트
python -m src.main --server            # 기동 로그: "알람 분석 워커 시작 (stream=alarm:raw)"

# ⑤ 알람 수신부 기동 — 별 터미널, 레포 루트
python -m noise_gate.alarm_server      # TCP 9100 → Redis Stream alarm:raw

# ⑥ 목업 [12] 주입
python noise_gate/scripts/mock_polestar_events.py --send invest-trigger

# ⑦ 감사 확인
grep '"type": "investigation"' logs/alarm_decisions.jsonl | tail -1 | python -m json.tool
tail -3 sre_agent/.data/investigation_audit.jsonl 2>/dev/null || echo "(감사 파일은 첫 조사 때 생성된다)"
```

**합격 기준**: `logs/alarm_decisions.jsonl`에 `type="investigation"` 레코드가 생기고
`investigation_id`가 채워져 있으며 `status`가 `stub`(무키 정상 경로)이다. 목업 출력은 `accepted`
또는 `duplicate`를 표시한다. 게이트 판정은 `PAGE`.

**여기까지는 LLM 백엔드와 무관하다.** 실 조사로 넘어갈 때 갈린다 —
**Gemini는 §7-G**(그대로 동작), **사내는 §7-V**(vLLM 서빙 → 판정 → 기동 → 완주 확인).
데이터를 **실 폴스타·실 Prometheus**로 바꾸는 것은 §8이며, 그때는 조사 LLM 선택이
규정 문제가 된다(§8.0).

---

## 1. MVP가 무엇인가 — 검증 대상 흐름

```
[목업 생성기]  noise_gate/scripts/mock_polestar_events.py  (시나리오 [12] invest-trigger)
      │ TCP 9100 (단일행 JSON)
      ▼
[수신부]      python -m noise_gate.alarm_server        →  Redis Stream  alarm:raw
      │
      ▼
[워커]        AlarmWorker (본체 API와 같은 프로세스·D-048)
      │  노이즈 게이트 4-티어 판정 → tier=PAGE
      ▼
[트리거]      investigation_trigger 노드  (NOISE_INVESTIGATION_TRIGGER_ENABLED)
      │  MCP(SSE) submit  →  sre_investigate_alarm(payload, contract_version="1")
      ▼
[조사 서비스] sre_agent  run_service   :9098/sse      ← 별 프로세스·별 venv
      │  dispatcher: dedup·동시상한·전체타임아웃 300s·시간당 예산
      │  ├─ LLM 키 없음 → status="stub"  (레벨 A · 과금 0)
      │  └─ LLM 키 있음 → DiagnosisAgent(HolmesGPT ReAct) (레벨 B · 과금)
      │        │ MCP(SSE) 도구 호출
      │        ▼
      │   [mcp_server] :9099/sse — 폴스타 고수준 도구 8종 + PromQL 고수준 2종
      │        ├─ PostgreSQL 픽스처 :5434 (polestar.cmm_resource)
      │        └─ Prometheus 픽스처 :9190 (nodename=svr-web-01)
      ▼
[후처리]      severity_judge(escalate-only) → briefing_builder(6요소·인용 검증)
      │        → remediation_recommender(권고만·실행 경로 없음)
      ▼
[통보]        alarm_notifier — 브리핑 블록을 worKB 본문에 첨부 (+ escalate 상향 안내)
[감사]        logs/alarm_decisions.jsonl (type="investigation") · sre_agent/.data/investigation_audit.jsonl
```

**불변 원칙**: 읽기전용·조치는 권고만(D-003) · 결정적=판단/LLM=보조(D-035) · 전 신규 기능 옵트인 기본 off ·
조사 서비스가 죽어 있어도 **게이트 판정·통보는 정상 완료**되고 트리거만 사유를 남기고 graceful 실패한다(비차단 계약).

---

## 2. 테스트 레벨 2종 × LLM 백엔드 2종

### 2.1 레벨 — 무엇까지 검증하나

| | 레벨 A — 스텁 조사 | 레벨 B — 실 조사 완주 |
|---|---|---|
| 목적 | **배관 전 구간** 검증(트리거·계약·감사·첨부) | HolmesGPT ReAct 루프·도구 인용·브리핑 품질 |
| LLM | 호출 없음 | 실 호출 — **과금/사내 자원 소비** |
| 승인 | 불요 | **D-127에 따라 사용자 사전 승인 필수(건별)** |
| 판정 | `status="stub"`, `briefing={"stub":true,...}` | `status="done"`, 6요소 브리핑 + 도구 인용 |
| 소요 | 즉시(초 단위) | Gemini 실측 **161초**(2026-07-28 MVP 완주) |

**레벨 A는 LLM 백엔드와 무관하다** — 키가 없으면 어떤 백엔드를 쓰든 스텁으로 확정된다.
백엔드 선택이 갈리는 것은 레벨 B뿐이다(§7).

### 2.2 백엔드 — 성립 조건은 **네이티브 tool-calling** 하나다

`sre_agent`의 조사 코어는 HolmesGPT `ToolCallingLLM`의 ReAct 루프이고, 이 루프는 litellm 경유로
**모델이 `tools`를 받아 `tool_calls`를 돌려주는 것**을 전제한다. holmes 0.36.0 소스 실측:
매 호출에 `tools=tools, tool_choice=tool_choice`를 실어 보내고(`core/tool_calling_llm.py:1165`),
응답에서 `msg.get("tool_calls")`를 읽으며(`:284`), 엔드포인트가 이를 거부하면 **예외로 끝난다**
— **프롬프트 기반 폴백 경로가 없다.**

| | **G. Gemini** (D-120) | **F. 사내 FabriX** | **V. 사내 vLLM** (채택) |
|---|---|---|---|
| 위치 | 외부 API (과금) | 사내 게이트웨이 | **사내 신규 서빙** |
| 프로토콜 | litellm 네이티브 `gemini/...` | FabriX 고유 REST(**OpenAI 비호환** — §2.2.1) | OpenAI 호환 `/v1` |
| 네이티브 tool-calling | **검증 완료** — MVP 실 완주 161초 | **불가 (현 구성 확정)** | **가능 — 단 플래그 2개 필수**(§7-V.1) |
| 조사 LLM 사용 | ✅ 단, **픽스처 데이터만**(§8.0) | 🚫 **HolmesGPT 구동 불가** | ✅ **채택 경로**(§7-V) — 실 데이터 가능 |

> **2026-08-25 결정**: 사내 조사 LLM은 **별도 vLLM**으로 세운다. FabriX는 tool-calling을 못 하므로
> 조사 루프를 맡을 수 없고(§2.2.1), Gemini는 외부 SaaS라 실 데이터를 보낼 수 없다(§8.0).
> **두 제약이 겹치는 지점의 유일한 해가 사내 vLLM**이다.

#### 2.2.1 FabriX가 OpenAI 호환이 아니라는 실측 (2026-08-25 · 사용자 지적 확인)

사내 FabriX 경로의 실제 클라이언트는 **`KBGenAIChat`**(`src/clients/fabrix_kbgenai.py`)이다.
`src/llm.py:266`의 분기가 **`fabrix_client_key`가 있으면 KBGenAIChat**을 쓰고, `.encenv`에
`LLM_FABRIX_CLIENT_KEY`가 정의돼 있다 — 즉 사내 배치는 이쪽이다(D-037도 *"FabriX(KBGenAIChat)"*로
명시). 그 규격은 OpenAI와 다음이 전부 다르다:

| | OpenAI 규격 | **FabriX KBGenAI 실측** |
|---|---|---|
| 요청 본문 | `{model, messages:[{role,content}], tools, tool_choice}` | `{modelId, contents:[문자열 배열], isStream, isRagOn, executeRagFinalAnswer, executeRagStandaloneQuery, systemPrompt}` |
| 역할 구조 | role별 메시지 객체 | **role 소실** — 문자열 배열. system은 별도 필드 |
| 응답 | `{choices:[{message:{content, tool_calls}}]}` | `{status:"SUCCESS", content:"…"}` |
| **도구** | `tools` 전송 → `tool_calls` 수신 | **요청에 도구 필드가 없다** |
| 엔드포인트 | `/v1/chat/completions` | 임의 `endpoint_url` |
| 인증 | `Authorization: Bearer` | `x-openapi-token` + `x-generative-ai-client` |

**결정적 지점**: `KBGenAIChat.bind_tools()`는 도구를 `self.tool_registry` dict에 넣고 끝난다
(`:308-313`). 그리고 **`tool_registry`를 읽는 코드가 어디에도 없다** — 도구가 요청에 실리지
않으므로 tool-calling이 프로토콜 수준에서 불가능하다. `_generate`도 `AIMessage(content=…)`만
반환해 `tool_calls`가 아예 없다.

> **정정.** 본 문서 이전 판(2026-08-25 오전)은 FabriX를 *"OpenAI 호환 — tool-calling 지원 여부만
> 미검증"*으로 적었다. 그 근거였던 `src/clients/fabrix_client.py`(`FabriXAPIClient`)는
> **`fabrix_client_key`가 없을 때만 쓰이는 폴백 경로**이며 사내 배치의 실제 클라이언트가 아니다.
> 사용자 지적으로 재검토해 위와 같이 확정했다.

**따라서 litellm `openai/<model>` + `api_base`로 FabriX를 호출하는 시도는 tool-calling 이전에
프로토콜 단계에서 실패한다.** 이는 판정이 필요한 미지수가 아니라 **확정된 사실**이다
— 사내 조사 LLM은 **별도 vLLM**으로 세운다(§7-V).


### 2.3 ★ 가장 중요한 함정 — 조사 서비스를 어디서 기동하느냐로 과금이 갈린다

`AgentSettings`는 **CWD 기준으로** `.env` → `.encenv`를 읽고, `gemini_api_key`는
`AliasChoices("GEMINI_API_KEY", "LLM_GEMINI_API_KEY")`로 해석된다. 레포 루트의 `.encenv`에는
`LLM_GEMINI_API_KEY`가 **존재**하므로:

```bash
# [서버 A · CWD=레포 루트 — 두 명령의 CWD 차이가 요점이다]
# 실측 (2026-08-25)
cd sre_agent && .venv/bin/python -c "from sre_agent.settings import AgentSettings; \
    print('key set:', AgentSettings().gemini_api_key is not None)"
# → key set: False      ← 스텁 경로(레벨 A · 과금 0)

sre_agent/.venv/bin/python -c "import sys; sys.path.insert(0,'sre_agent'); \
    from sre_agent.settings import AgentSettings; print('key set:', AgentSettings().gemini_api_key is not None)"
# → key set: True       ← 실 조사 경로(레벨 B · 과금)
```

**즉 레포 루트에서 `run_service`를 기동하면 승인 없이도 실 Gemini 조사가 돈다.**
레벨 A를 의도한다면 반드시 `cd sre_agent` 후 기동하거나, 키를 명시적으로 비운다:

```bash
# [서버 A · CWD=sre_agent/ · sre_agent/.venv]
GEMINI_API_KEY= LLM_GEMINI_API_KEY= python -m sre_agent.run_service   # 강제 스텁
```

스텁 확정은 침묵하지 않는다 — dispatcher가 `status="stub"`,
`verdict="조사 미실행 — LLM 키 부재(스텁)"`로 감사에 남긴다
(`sre_agent/sre_agent/application/investigation_dispatcher.py:302`).

> **FabriX 경로에서도 같은 게이트가 그대로 적용된다.** `_finalize_stub`의 조건은
> `gemini_api_key is None` 하나다(실측). 사내 엔드포인트를 쓰더라도 이 필드를 채우지
> 않으면 스텁으로 떨어진다 — 처리 방법은 §7-V.3에 있다.
---

## 3. 구동 프로세스와 물리 배치

### 3.1 프로세스 인벤토리 (자체 코드 — 4개)

**같은 저장소지만 프로세스는 4개로 갈린다.** 경계를 만든 이유는 의존성·런타임 격리다 —
`sre_agent`는 holmesgpt 스택(Python **≥3.13**), 본체는 LangGraph 스택(**≥3.11**)이라 한
프로세스에 두면 충돌한다(D-016에서 "패키지 임베드"를 기각한 근거, D-118·D-123이 계승).

| # | 프로세스 | **기동 서버** | 기동 명령 | CWD | 인터프리터 | 포트(listen) | 붙는 곳 |
|---|---|---|---|---|---|---|---|
| ① | **본체 API + 알람 워커**<br>(`src/` + `noise_gate/`) | **A** 에이전트 호스트 | `python -m src.main --server` | 레포 루트 | `.venv` **3.12.11** | `API_PORT` **8050** (`0.0.0.0`) | Redis(소비) · `mcp_server` 9099 · `sre_agent` 9098 · worKB |
| ② | **알람 수신부**<br>(`noise_gate/alarm_server/`) | **A** (권장) | `python -m noise_gate.alarm_server` | 레포 루트 | `.venv` (①과 공유) | **9100** TCP (`0.0.0.0`) | Redis(적재) |
| ③ | **`mcp_server`**<br>(관측 읽기 경계) | **A 또는 C** 데이터 접근 호스트 | `python -m mcp_server` | 레포 루트<br>(`PYTHONPATH=./mcp_server`) | `.venv` 또는 시스템 3.13<br>*(전용 venv 없음 — 실측)* | **9099** SSE (`0.0.0.0`)<br>조사용은 **9097** 권장 | 폴스타 PG/DB2/REST · Prometheus |
| ④ | **`sre_agent` 조사 서비스** | **A — ①과 같은 서버 🔒** | `python -m sre_agent.run_service` | **`sre_agent/`**<br>(§2.3 — 과금 갈림) | **`sre_agent/.venv` 3.13.1** | **9098** SSE (**`127.0.0.1` 고정**) | `mcp_server` · LLM(vLLM/Gemini) |
| — | **vLLM** (조사 LLM · §7-V) | **B** GPU 서버 | §7-V.1 참조 | — | vLLM 스택 | **8000** `/v1` | — |

> **①과 ②는 왜 갈라져 있나** — 게이트·워커는 본체와 **같은 프로세스·같은 venv**에서
> in-process로 뜨지만(`src/api/server.py:379`, D-048), TCP 수신부만 **독립 프로세스**다.
> 수신부는 `src.` import가 0인 자립 모듈이라 폴스타 push를 받는 경계로 따로 세운다(D-139).
>
> **③은 프로파일이 둘이라 인스턴스도 둘이 될 수 있다** — 본체용(`expose_execute_sql=true`)과
> 조사용(`false`)은 양립하지 않는다(§4.2). 같은 호스트에서 둘 다 필요하면 포트를 갈라 두 개를
> 띄운다(9099 본체 / 9097 조사).
>
> **④의 엔트리는 `run_service` 하나뿐**이다(구 `run_gate`·수신부 엔트리는 통합으로 소멸).

### 3.2 인프라 프로세스 (외부 — 개발은 Docker)

| 구성 | 개발(Docker 픽스처) | 운영(실연동 §8) |
|---|---|---|
| Redis | `collectorinfra-redis` — 호스트 **6380**→6379 | 사내 Redis |
| 폴스타 DB | `polestar_pg` — **5434**→5432 (PostgreSQL) | 실 폴스타 **PG(gp·yd)·DB2(b0)** |
| Prometheus | `fixture_prometheus` — **9190**→9090 | 중앙 관측 스택 Prometheus |
| 스크레이프 대상 | `fixture_target_vm`(node_exporter) **9101**→9100<br>`fixture_mock_exporter` **9102**→80 | **대상 VM의 node_exporter** |
| 사내 메신저 | worKB 스텁 수신기 **28080**(§6) | worKB 포탈 |
| LLM | Gemini API(외부) | **사내 FabriX/vLLM**(§8.0) |

### 3.3 서버 배치 — 어떤 프로세스를 어느 서버에서 띄우나

**핵심 원칙: 에이전트는 중앙 1곳에서만 돈다.** 대상 VM에는 조사 에이전트를 **배포하지 않으며**,
**SSH도 쓰지 않는다**(sre-agent/06 §1 · D-004). 대상 VM에 있는 것은 **node_exporter뿐**이고,
그것도 우리가 붙는 게 아니라 **Prometheus가 스크레이프**한다.

#### 3.3.1 서버 A·B·C — 무엇이고, 왜 나누나

세 서버는 **자원 종류와 접근 권한이 달라서** 나뉜다. 개발에서는 한 대에 다 올려도 되지만
(§3.3.4), 경계의 이유를 알아야 무엇을 어디에 두는지 판단할 수 있다.

##### 서버 A — 에이전트 호스트 (**이 시스템의 본체**)

**한 줄**: 알람을 받아 노이즈를 걸러내고, 조사를 시켜서, 사람에게 통보하는 **모든 판단이
일어나는 곳**. 사용자가 접속하는 웹 UI·챗도 여기다.

| | |
|---|---|
| 도는 것 | ① 본체 API+알람 워커 · ② 알람 수신부 · ④ `sre_agent` 조사 서비스 (+개발 시 ③) |
| 런타임 | Python **3.12**(`.venv` — ①②③) **+ 3.13**(`sre_agent/.venv` — ④). **두 개가 같이 필요하다** |
| 배포물 | 레포 전체(`src/`·`noise_gate/`·`sre_agent/`) |
| 설정 | `<레포 루트>/.env`(플래그·Redis·worKB) · `.encenv`(LLM 키) |
| 자원 성격 | **GPU 불요**. LLM 추론은 서버 B가 하고 A는 대기(I/O bound) — CPU·메모리 중심 |
| inbound | 폴스타 → 9100(알람) · 사용자 → 8050(UI). **이 둘뿐** |
| outbound | Redis · 서버 B(LLM) · 서버 C(도구) · worKB |
| 운영 주체 | **우리** |

**왜 이 경계인가** — 두 가지가 A를 하나로 묶는다.

1. **`sre_agent`가 `127.0.0.1` 고정 바인드**라 본체 API와 물리적으로 같은 서버여야 한다
   (§3.3.2 🔒). 이건 선택이 아니라 코드가 강제한다.
2. **에이전트는 중앙 1곳에서만 돈다**(sre-agent/06 §1 · D-004) — 대상 VM에 뭘 설치하거나
   SSH로 들어가지 않는다. 그래서 "조사하는 쪽"이 여러 대로 흩어질 이유가 없다.

**주의점**

- **CWD가 동작을 바꾼다** — ④를 `sre_agent/`에서 띄우면 스텁, 레포 루트에서 띄우면 실 조사(과금)다(§2.3).
- **9100 포트 충돌** — 이 서버에 node_exporter를 함께 올리면 ②와 겹친다(§3.4).
- A가 죽으면 **알람 처리 전체가 멈춘다**. 단일 장애점이므로 운영 전환 시 이중화 검토 대상이다.

##### 서버 B — GPU 서버 (**조사 LLM 전용**)

**한 줄**: HolmesGPT가 ReAct 루프를 돌릴 **tool-calling 가능한 LLM을 서빙**하는 곳.
vLLM 프로세스 하나가 전부다.

| | |
|---|---|
| 도는 것 | vLLM OpenAI 호환 API 서버 (`/v1`, 기본 8000) |
| 런타임 | vLLM 스택 + GPU 드라이버 |
| 배포물 | 모델 가중치(예: Qwen3.5-9B) |
| 설정 | 기동 플래그 — **`--enable-auto-tool-choice`·`--tool-call-parser`·`--max-model-len`**(§7-V.1) |
| 자원 성격 | **GPU VRAM이 병목**. 모델 크기 + KV 캐시(`--max-model-len`에 비례) |
| inbound | 서버 A ④ 만 (8000/TCP) |
| outbound | 없음 |
| 운영 주체 | **우리**(신규 구축 — 2026-08-25 결정) |

**왜 분리하나** — 셋 다 A와 성격이 다르다.

1. **자원** — GPU가 필요하고, A는 GPU가 필요 없다. 한 대에 묶으면 비싼 자원을 놀린다.
2. **수명주기** — 모델 로딩에 수 분이 걸리고 모델 교체·튜닝으로 재기동이 잦다. A(알람 처리)가
   그때마다 같이 내려가면 안 된다.
3. **격리** — LLM 추론이 메모리를 먹고 죽어도 알람 게이트는 계속 돌아야 한다.

**주의점**

- **없어도 레벨 A는 돈다.** B가 없거나 죽으면 조사는 `status="stub"`으로 떨어지고
  게이트·통보는 정상 동작한다(§2.3) — **조사 실패가 알람 처리를 막지 않는다.**
- **플래그 2개를 빠뜨리면 조용히 실패한다** — 서버는 200을 주는데 `tool_calls`만 비는
  형태라, 반드시 §7-V.2로 판정하고 넘어간다.
- 모델 용량이 완주를 가른다 — D-037의 Qwen3.5-9B는 *"제어평면에 계획 신호만"* 용도로 고른
  것이라 40-step ReAct 완주는 별도 확인이 필요하다(§7-V.5).

##### 서버 C — 데이터 접근 호스트 (**관측 읽기 경계**)

**한 줄**: 폴스타 DB와 Prometheus를 **읽기 전용으로** 대신 조회해 주는 경계.
`mcp_server` 하나가 돈다.

| | |
|---|---|
| 도는 것 | ③ `mcp_server` (조사 프로파일 9097 / 본체 프로파일 9099) |
| 런타임 | Python 3.11+ (전용 venv 없음 — 본체 `.venv` 사용) |
| 설정 | `mcp_server/config.toml`(소스·도구 노출) · `mcp_server/.env`(연결 문자열·`PROMETHEUS_URL`) |
| inbound | 서버 A ①④ |
| outbound | 폴스타 PG/DB2/REST · Prometheus |
| 운영 주체 | **우리**(단, 붙는 대상은 남의 인프라) |

**왜 분리할 수 있나 / 언제 분리하나** — `0.0.0.0` 바인드라 원격 배치가 가능하다.
**폴스타 DB·Prometheus에 네트워크가 닿는 망**에 있어야 하므로, A와 망이 갈리면 C로 뺀다.
개발에서는 A와 동거한다. DB2(b0)를 쓰려면 이 서버에 **`ibm_db` 설치가 선행**된다(§8.1.4).

##### 우리가 만들지 않는 것

| | 위치 | 우리 역할 |
|---|---|---|
| 폴스타 DB(gp·yd·b0)·REST | 폴스타 인프라 | 읽기전용 계정으로 **조회만** |
| Prometheus | 중앙 관측 스택 | **조회만** — 단 `nodename` 라벨 규약은 협의 대상(§8.2.2) |
| node_exporter | 각 대상 VM | 수집 전제. **직접 붙지 않는다**(Prometheus가 스크레이프) |
| Redis | A 또는 사내 공용 | 수신부·워커가 **같은 인스턴스**만 보면 된다 |
| worKB 포탈 | 사내 | 통보 발송 대상 |

#### 3.3.2 프로세스별 배치 지정 (이 표가 기준이다)

| 프로세스 | **기동 서버** | 강제성 | 근거(실측 제약) |
|---|---|---|---|
| ① 본체 API + 알람 워커 | **A. 에이전트 호스트** | — | 사용자 UI·챗 진입점. `API_HOST=0.0.0.0` |
| ④ `sre_agent` 조사 서비스 | **A. 에이전트 호스트 (①과 같은 서버)** | 🔒 **필수** | **`DEFAULT_HOST="127.0.0.1"` 하드코딩**(`interface/mcp_service.py:32` — env 오버라이드 없음). 루프백 바인드라 **다른 서버에서 접근 불가** → 호출자인 ①과 반드시 동거 |
| ② 알람 수신부 `alarm_server` | **A. 에이전트 호스트** (권장) | 권장 | `0.0.0.0:9100` 바인드라 분리 가능하나, **폴스타가 push로 도달**해야 하고 **워커와 같은 Redis**를 봐야 한다. 분리하면 방화벽·Redis 경로가 둘로 늘 뿐 이점이 없다 |
| ③ `mcp_server` (조사 프로파일) | **A 또는 C. 데이터 접근 호스트** | 분리 가능 | `SERVER_HOST=0.0.0.0`·`SERVER_PORT` 조정 가능. **폴스타 DB·Prometheus에 네트워크 도달 가능한 망**에 있어야 한다 — 방화벽이 갈리면 C로 분리 |
| **vLLM** (조사 LLM · §7-V) | **B. GPU 서버** | 🔒 사실상 필수 | GPU 자원이 필요하고 ①④와 수명주기가 다르다. `API_BASE`로 원격 지정(§7-V.4) |
| Redis | A 또는 사내 공용 | 분리 가능 | 수신부·워커가 **같은 인스턴스**만 보면 된다(§4.5 포트 함정) |
| 폴스타 DB(gp·yd·b0)·REST | **폴스타 인프라** | 기존 | 접속만 — 읽기전용 계정(§8.1.2) |
| Prometheus | **중앙 관측 스택** | 기존 | 조회만. `nodename` 라벨 규약은 협의 대상(§8.2.2) |
| node_exporter | **각 대상 VM** | 기존 | 설치는 인프라 팀. **우리는 직접 붙지 않는다** |

> **🔒 가장 강한 제약은 ①④ 동거다.** `sre_agent`가 루프백에만 바인드하므로 조사 서비스를
> 별도 서버로 빼려면 **코드 변경(바인드 주소 설정화 + 인증 강제)이 선행**된다. 지금 구성에서는
> `NOISE_INVESTIGATION_SERVICE_URL`을 원격 주소로 바꿔도 **연결되지 않는다.**

#### 3.3.3 배치도

```
┌─ 서버 A · 에이전트 호스트 ─────────────────────────┐   ┌─ 서버 B · GPU ──────┐
│                                                    │   │                     │
│  ② alarm_server :9100 ←── TCP push ── [폴스타]     │   │  vLLM :8000/v1      │
│         │ XADD alarm:raw                           │   │  (조사 LLM · §7-V)  │
│         ▼                                          │   └──────▲──────────────┘
│      [Redis]  (A 또는 사내 공용)                    │          │ OpenAI 호환
│         │ 소비                                      │          │ (API_BASE)
│  ① 본체 API :8050  — AlarmWorker in-process        │          │
│         │ MCP(SSE) submit/poll                     │          │
│         ▼        ★ 127.0.0.1 — 같은 서버 필수       │          │
│  ④ sre_agent :9098  (HolmesGPT ReAct) ─────────────────────────┘
│         │ MCP(SSE) 도구 호출                        │
└─────────┼──────────────────────────────────────────┘
          ▼
┌─ 서버 C · 데이터 접근 (A와 동거 가능) ──────────────┐
│  ③ mcp_server :9097 (조사) / :9099 (본체)          │
└────┬───────────────────────────┬───────────────────┘
     │ SELECT/GET (읽기전용)      │ HTTP(PromQL)
     ▼                            ▼
[폴스타 인프라]              [중앙 관측 스택]
 PG(gp·yd)·DB2(b0)·REST       Prometheus
                                  ▲ scrape
                          [대상 VM: node_exporter만]
                          ※ 에이전트 미배포 · SSH 없음
```

#### 3.3.4 배치 시나리오

| 시나리오 | 배치 | 쓰임 |
|---|---|---|
| **개발·MVP 레벨 A** | **1대에 전부**(A=C, Docker 픽스처 동거, vLLM 불요 — 스텁) | §5 배관 검증 |
| **MVP 레벨 B** | **A**(①②③④) + **B**(vLLM) | §7-V 실 조사 완주 |
| **실연동**(§8) | **A**(①②④) + **B**(vLLM) + **C**(③ — 폴스타·Prometheus 도달 망) | 운영 데이터 |

#### 3.3.5 방화벽 — 열어야 하는 방향

방향이 중요하다. **inbound가 필요한 것은 ②뿐**이고 나머지는 전부 outbound다.

| 출발 | 도착 | 포트 | 용도 |
|---|---|---|---|
| **폴스타** | **A** ② | 9100/TCP | 알람 push (**유일한 inbound**) |
| 사용자 | A ① | 8050/TCP | 웹 UI·챗 |
| A ①④ | Redis | 6379(또는 매핑 포트) | Stream |
| A ④ | **B** vLLM | 8000/TCP | 조사 LLM (OpenAI 호환) |
| A ④ | C ③ | 9097/TCP | 조사 도구 호출(MCP SSE) |
| A ① | C ③ | 9099/TCP | 본체 NL→SQL 파이프라인 |
| C ③ | 폴스타 DB | 5432 / 50000 | 읽기전용 조회 |
| C ③ | Prometheus | 9090 | PromQL |
| A ① | worKB 포탈 | 사내 정의 | 통보 발송 |

> ①↔④는 **같은 서버 루프백**이라 방화벽 대상이 아니다(§3.3.2).

### 3.4 ★ 포트 충돌 — 9100은 알람 수신부가 이미 쓴다

**node_exporter의 기본 포트가 9100인데, 알람 수신부(②)도 9100이다.** 중앙 실행 호스트에
node_exporter를 함께 올리면 충돌한다. 픽스처가 `fixture_target_vm`을 **9101로 재배치**한 것이
바로 이 이유다(sre-agent/06 §8.1 실측). 실배치에서도 **중앙 호스트의 node_exporter는 포트를
옮기거나, 알람 수신 포트를 옮긴다**(`ALARM_SERVER_SOCKET_PORT`).

같은 이유로 Prometheus 픽스처가 **9190**에 있다 — 호스트 9090을 langfuse-minio가 점유했다(실측).

**현재 로컬 점유 현황 확인**:

```bash
# [서버 A · CWD=무관 · .venv]
python - <<'PY'
import socket
NAMES = {6380:"Redis(도커)", 5434:"폴스타 PG(픽스처)", 9190:"Prometheus(픽스처)",
         9101:"node_exporter(픽스처)", 9102:"mock_exporter(픽스처)",
         9099:"mcp_server(본체 프로파일)", 9097:"mcp_server(조사 프로파일)",
         9098:"sre_agent 조사 서비스", 9100:"alarm_server 수신부", 8050:"본체 API"}
for port, name in NAMES.items():
    s = socket.socket(); s.settimeout(0.6)
    try: s.connect(("127.0.0.1", port)); print(f"{port:5d} OPEN    {name}")
    except Exception: print(f"{port:5d} closed  {name}")
    finally: s.close()
PY
```

### 3.5 파일 시스템 배치 (레포 내)

```
/Users/cptkang/AIOps/collectorinfra/          ← 레포 루트 (①②의 CWD)
├── .venv/                    Python 3.12.11  ← ①② (그리고 ③도 이걸 씀)
├── .env / .encenv                            ← ①②가 읽음 · ④도 CWD가 루트면 읽음(§2.3)
├── src/                      본체 파이프라인·API·조립
├── noise_gate/               게이트·워커 + alarm_server/ (②의 엔트리)
├── mcp_server/               ③ — 자체 pyproject·config.toml·.env (전용 venv 없음)
│   ├── config.toml           소스 정의·도구 노출 게이트
│   └── .env                  {SOURCE}_CONNECTION · PROMETHEUS_URL
├── sre_agent/                ④ — 자체 pyproject
│   ├── .venv/                Python 3.13.1  ← ④ 전용 (holmesgpt 0.36.0·litellm 1.89.0)
│   └── .data/investigation_audit.jsonl       ← 조사 잡 감사(첫 조사 시 생성)
├── logs/
│   ├── alarm_decisions.jsonl                 ← 게이트 판정 + 조사 감사
│   ├── alarm_ticket_queue.jsonl
│   └── sql/YYYY-MM-DD.sql                    ← 실행 SQL 파일 로그(D-140 · ③도 여기에 append)
└── testdata/
    ├── pg/docker-compose.yml                 폴스타 PG 픽스처
    └── prometheus/docker-compose.yml         Prometheus·target-vm·mock_exporter 픽스처
```

> **venv 3개가 아니라 2개다** — `mcp_server`에는 전용 venv가 없다(실측). CLAUDE.md 관례대로
> 본체 `../.venv`로 실행하거나(테스트), 시스템 파이썬으로 띄운다(현재 구동 중인 인스턴스는
> pyenv 3.13.1). `sre_agent`만 3.13 요구 때문에 자체 venv를 갖는다.

### 3.6 기동 터미널 4개 (개발 시)

프로세스가 4개이므로 터미널도 4개다. 순서는 §5.2(의존 역순으로 띄우면 트리거가 graceful 실패).

| 터미널 | 서버 | 명령 | 확인 로그 |
|---|---|---|---|
| T1 | A 또는 C | `PYTHONPATH="$PWD/mcp_server" ... python -m mcp_server` | `MCP 서버 시작: dbhub-server (port=…)` |
| T2 | **A** | `cd sre_agent && .venv/bin/python -m sre_agent.run_service` | `sre_agent 조사 서비스 기동: http://127.0.0.1:9098/sse (인증=off)` |
| T3 | **A** | `python -m src.main --server` | `알람 분석 워커 시작 (stream=alarm:raw)` |
| T4 | A | `python -m noise_gate.alarm_server` | `alarm_server 시작: stream_key=alarm:raw port=9100` |

레벨 B는 여기에 **서버 B의 vLLM**(§7-V.1)이 선행으로 떠 있어야 한다.

목업 주입·로그 확인은 다섯 번째 터미널(또는 T4 종료 후 재사용)에서 한다.

---

### 3.7 명령·설정 실행 위치 일람 (한눈에)

문서의 모든 블록에는 첫 줄에 위치가 붙어 있다(범례는 문서 상단). 아래는 그 요약이다.

**기동 명령**

| 무엇 | 서버 | CWD | 인터프리터 | 절 |
|---|---|---|---|---|
| `python -m mcp_server` (조사 프로파일 9097) | A 또는 **C** | 레포 루트<br>`PYTHONPATH=./mcp_server` | `.venv` | §4.2 |
| `python -m sre_agent.run_service` | **A** 🔒 | **`sre_agent/`** ← 스텁 / **레포 루트** ← 실 조사 | `sre_agent/.venv` | §4.3·§2.3 |
| `python -m src.main --server` | **A** | 레포 루트 | `.venv` | §4.4 |
| `python -m noise_gate.alarm_server` | **A** | 레포 루트 | `.venv` | §4.5 |
| `python -m vllm.entrypoints.openai.api_server …` | **B (GPU)** | 무관 | vLLM 환경 | §7-V.1 |

**테스트·검증 명령**

| 무엇 | 서버 | CWD | 인터프리터 | 절 |
|---|---|---|---|---|
| 레벨 A MVP 판정 (`test_send_invest_trigger_e2e`) | A | 레포 루트 | `.venv` | §0.1 |
| 레벨 B MVP 판정 (`test_investigation_e2e.py`) | A | **`sre_agent/`** 또는 레포 루트 | `sre_agent/.venv` | §0.1·§7-V.5 |
| vLLM tool-calling 판정 스니펫 | A | 레포 루트 | `sre_agent/.venv` | §7-V.2 |
| 포트 점유 확인 | A | 무관 | `.venv` | §3.4 |
| 회귀 스위트 4종 | A(+C) | 레포 루트(서브셸 진입) | 각 venv | §11 |
| `nodename` 라벨 측정(curl 5단계) | **Prometheus 도달 가능한 곳**(보통 C) | 무관 | — | §8.2.2 |
| `ibm_db` 설치 확인 | **C** | 레포 루트 | — | §8.1.4 |

**주입·관찰**

| 무엇 | 서버 | CWD | 인터프리터 | 절 |
|---|---|---|---|---|
| 목업 주입 `mock_polestar_events.py` | A | 레포 루트 | `.venv` | §5.3 |
| 감사 확인 (`logs/alarm_decisions.jsonl`) | A | 레포 루트 | — | §5.4 |
| worKB 스텁 수신기 | A | 무관 | `.venv` | §6 |
| 실행 대장 확인 (`logs/mvp_test/mvp_test_log.md`) | 실행한 호스트(보통 A) | 레포 루트 | — | §12.1 |

**설정 파일** — CWD가 아니라 *어느 서버의 어느 파일*이 기준이다.

| 파일 | 서버 | 담는 것 |
|---|---|---|
| `<레포 루트>/.env` | **A** | 게이트·트리거·후속·escalate 플래그, Redis, `ALARM_*`, `WORKB_*` |
| `<레포 루트>/.encenv` | **A** | LLM 키(`LLM_GEMINI_API_KEY` 등) — **CWD에 따라 조사 서비스가 이 키를 잡는다**(§2.3) |
| `<레포 루트>/mcp_server/.env` | **C** | `{SOURCE}_CONNECTION`, `PROMETHEUS_URL`, `PROCESS_API_BASE_URL` |
| `<레포 루트>/mcp_server/config.toml` | **C** | 소스 정의, 도구 노출 게이트 |
| `alarm_server.env`(선택) | **A** | `ALARM_SERVER_*` 전용 오버라이드 |
| vLLM 기동 플래그 | **B** | `--enable-auto-tool-choice`·`--tool-call-parser`·`--max-model-len` |

> **개발 단계에서는 A=B=C가 한 대일 수 있다.** 그때도 위 표의 **CWD·인터프리터 구분은 그대로
> 유효**하다 — 서버가 같아도 CWD를 틀리면 스텁/실조사가 갈리고(§2.3), 인터프리터를 틀리면
> `sre_agent` 임포트가 실패한다.

---

## 4. 구성 요소 5종 — 기동 방법과 설정

### 4.1 Docker 픽스처 (폴스타 PG · Prometheus · Redis)

| 컨테이너 | 포트(호스트) | 역할 |
|---|---|---|
| `polestar_pg` | **5434** → 5432 | 폴스타 PostgreSQL 픽스처 — `polestar.cmm_resource` **1,597행**, hostname `svr-web-01` |
| `fixture_prometheus` | **9190** → 9090 | Prometheus 2.53.0 — 보존 15d·무인증·`nodename` 커버리지 100% |
| `fixture_target_vm` | **9101** → 9100 | node_exporter(실 uname) — `nodename="svr-web-01"` |
| `fixture_mock_exporter` | **9102** → 80 | 결정적 합성 메트릭(단언 고정값) |
| `collectorinfra-redis` | **6380** → 6379 | 알람 Stream `alarm:raw` |

```bash
# [픽스처 호스트(개발=서버 A) · CWD=레포 루트]
docker ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
# 미기동이면
cd testdata/prometheus && docker compose up -d && cd -
cd testdata/pg         && docker compose up -d && cd -
```

검증:

```bash
# [Prometheus에 도달 가능한 곳(개발=서버 A) · CWD=무관]
curl -s 'http://localhost:9190/api/v1/query?query=up' | python -m json.tool | grep -E 'nodename|value'
# → nodename: svr-web-01 (job=node·job=mock 둘 다), value 1
docker exec polestar_pg psql -U polestar_user -d infradb -t \
  -c "select count(*) from polestar.cmm_resource;"     # → 1597
```

### 4.2 `mcp_server` — **조사 프로파일**로 기동 (관측 데이터 읽기 경계)

> **주의 — 본체 배치와 프로파일이 다르다.** 레포의 `mcp_server/config.toml`은 본체 NL→SQL
> 파이프라인용이라 `expose_execute_sql = true`다. 조사 배치는 **반드시 `false`**여야 한다 —
> 원시 SQL/PromQL을 노출하면 LLM이 방언 오류로 step을 소진한다(D-122 배치 규약).
> 본체 `mcp_server`가 이미 9099를 점유 중이면(`ps aux | grep 'python -m mcp_server'`)
> **다른 포트로 조사용 인스턴스를 따로 띄우고** `POLESTAR_MCP_URL`을 그 포트로 맞춘다.

```bash
# [서버 A 또는 C · CWD=레포 루트 · .venv]
cd /Users/cptkang/AIOps/collectorinfra
POLESTAR_CONNECTION='postgresql://polestar_user:polestar_pass_2024@localhost:5434/infradb' \
PROMETHEUS_URL='http://localhost:9190' \
EXPOSE_EXECUTE_SQL=false \
EXPOSE_RAW_PROMQL=false \
EXPOSE_POLESTAR_TOOLS=true \
SERVER_PORT=9097 \
PYTHONPATH="$PWD/mcp_server" .venv/bin/python -m mcp_server
```

노출되는 도구:

| 분류 | 도구 |
|---|---|
| 폴스타 고수준 8종 | `polestar_alarm_history` · `polestar_metric_trend` · `polestar_resource_status` · `polestar_topology` · `polestar_process_snapshot`(args 마스킹) · `polestar_os_config` · `polestar_change_history` · `polestar_condition_log` |
| PromQL 고수준 2종 | `prom_metric_instant` · `prom_metric_range` — **hostname 앵커**(서버측이 `{nodename="…"}`를 조립) |
| 원시(옵트인·기본 비노출) | `prom_query` · `prom_query_range` · `prom_labels` · `prom_metadata` · `prom_series` · `execute_sql` |

> **`PROMETHEUS_URL` 미설정이면 PromQL 도구가 전건 실패한다**(침묵 폴백 금지 — 명시적 오류 반환).
> 현재 `mcp_server/.env`에는 이 키가 없으므로 위처럼 환경변수로 주입해야 한다(2026-08-25 실측).

### 4.3 `sre_agent` 조사 서비스 (포트 9098)

```bash
# [서버 A · CWD=레포 루트에서 시작 → sre_agent/ · sre_agent/.venv]
cd sre_agent                      # ★ §2.1 — CWD가 과금을 가른다
.venv/bin/python -m sre_agent.run_service
```

주요 설정(`AgentSettings` — env_prefix 없음, 필드명을 대문자로):

| 환경변수 | 기본값 | 의미 |
|---|---|---|
| `POLESTAR_MCP_URL` | `http://localhost:9099/sse` | 조사용 `mcp_server` 엔드포인트 (§4.2에서 9097로 띄웠다면 맞출 것) |
| `POLESTAR_MCP_TOKEN` | *(없음)* | mcp_server Bearer (D-125) |
| `SERVICE_BEARER_TOKEN` | *(없음)* | 조사 서비스 자체 Bearer. 없으면 무인증(로컬) |
| `INVESTIGATION_LLM_MODEL` | `gemini/gemini-3.5-flash` | 개발·테스트 LLM (D-120 · ListModels 실측 확정) |
| `GEMINI_API_KEY` / `LLM_GEMINI_API_KEY` | *(CWD 의존)* | **있으면 실 조사, 없으면 스텁** |
| `MAX_STEPS` | `40` | ReAct 상한. 초과 시 하드 실패가 아니라 graceful 미완주 |
| `INVESTIGATION_TIMEOUT_SECONDS` | `300` | 조사 1건 전체 타임아웃 |
| `INVESTIGATION_MAX_CONCURRENT` | `2` | 동시 조사 상한 |
| `INVESTIGATION_DEDUP_TTL_SECONDS` | *(off)* | 동일 fingerprint 재조사 억제 간격 |
| `INVESTIGATION_HOURLY_BUDGET` | *(off)* | 시간당 조사 횟수 상한 |
| `SEVERITY_JUDGE_ENABLED` | `false` | 2차 중요도 판정(escalate-only). **끄면 매칭 시그니처가 없어 권고도 비게 된다** |
| `REMEDIATION_RECOMMENDER_ENABLED` | `false` | 조치 권고(D-138 · 제시 전용) |

노출 도구 5종: `sre_investigate_alarm`(submit) · `sre_get_investigation`(poll) · `sre_diagnose`(pull) ·
`sre_list_investigations` · `sre_health`.

### 4.4 본체 API 서버 (알람 워커 in-process)

```bash
# [서버 A · CWD=레포 루트 · .venv]
python -m src.main --server        # 포트는 .env API_PORT (현재 8050)
# 기동 로그에 "알람 분석 워커 시작 (stream=alarm:raw)"이 보여야 한다
```

`.env`의 관련 키:

| 키 | 필요값 | 비고 |
|---|---|---|
| `ALARM_ENABLED` | `true` | 워커 기동 조건(`src/api/server.py:379`) |
| `ALARM_MIN_SEVERITY` | `1` | 시나리오 전건을 통과시키려면 1 |
| `NOISE_ENABLE_NOISE_GATE` | `true` | 게이트 활성 |
| `REDIS_HOST`/`REDIS_PORT` | `localhost`/`6380` | 워커가 Stream을 읽는 곳 |
| `NOISE_DECISION_STORE_ENABLED` | `true` | 감사 JSONL 기록 |
| `NOISE_DECISION_STORE_PATH` | `logs/alarm_decisions.jsonl` | 관측 지점 |

### 4.5 알람 수신부 (TCP 9100)

```bash
# [서버 A · CWD=레포 루트 · .venv]
python -m noise_gate.alarm_server
```

> **★ Redis 포트 함정.** 수신부는 `ALARM_SERVER_` 접두사로 자체 설정을 읽고
> `ALARM_SERVER_REDIS_PORT` **기본값이 6379**다. 반면 워커는 `REDIS_PORT=6380`(도커)을 읽는다.
> 맞추지 않으면 수신부는 6379에 붙지 못하거나(현재 6379는 닫혀 있음) 워커가 못 읽는
> 다른 Redis에 적재한다 — **이벤트가 사라진 것처럼 보이는 전형적 증상**이다.
> `.env`에 `ALARM_SERVER_REDIS_PORT=6380`을 넣거나 `alarm_server.env`를 만들어 지정한다.

---

## 5. 레벨 A — 스텁 조사로 배관 전 구간 검증 (과금 0)

### 5.1 플래그

```dotenv
# [서버 A · 파일: <레포 루트>/.env]
# 필수
ALARM_ENABLED=true
NOISE_ENABLE_NOISE_GATE=true
NOISE_INVESTIGATION_TRIGGER_ENABLED=true
ALARM_SERVER_REDIS_PORT=6380

# 선택 — 조사 서비스 위치가 기본과 다를 때
NOISE_INVESTIGATION_SERVICE_URL=http://localhost:9098/sse
NOISE_INVESTIGATION_SERVICE_TOKEN=
NOISE_INVESTIGATION_TOTAL_TIMEOUT_SECONDS=45

# 선택 — CW-C escalate-only 상향 안내
NOISE_FAULT_ESCALATION_ENABLED=true
```

### 5.2 기동 순서 (역순으로 띄우면 트리거가 graceful 실패한다)

```
① Docker 픽스처  →  ② mcp_server(조사 프로파일)  →  ③ sre_agent 조사 서비스
                 →  ④ 본체 API(워커)             →  ⑤ alarm_server(수신부)
```

터미널 배정과 각 프로세스의 확인 로그는 **§3.6**, 포트 점유 확인 스니펫은 **§3.4**를 쓴다.

### 5.3 목업 주입

```bash
# [서버 A · CWD=레포 루트 · .venv]
# 대화형 — 메뉴에서 12 입력
python noise_gate/scripts/mock_polestar_events.py

# 단발(자동화)
python noise_gate/scripts/mock_polestar_events.py --send invest-trigger

# TCP 수신부를 못 띄우는 상황이면 Redis 직주입 폴백 — ★ 포트를 6380으로 명시할 것
python noise_gate/scripts/mock_polestar_events.py --send invest-trigger \
       --path redis --redis-url redis://localhost:6380/0
```

주요 옵션(전부 `--help`로 확인 가능): `--host`/`--port`(기본 localhost:9100) ·
`--path tcp|redis` · `--redis-url`(**기본 `redis://localhost:6379/0` — 도커는 6380**) ·
`--decision-log`(기본 `logs/alarm_decisions.jsonl`) · `--timeout`(판정 대기, 기본 30초) ·
`--db-id`(기본 `polestar_pg` — 알람 이벤트의 존 식별자).

시나리오 **[12] invest-trigger**는 `SAE 0011649` / severity 3 / "UPS 출력 전압 하한 경고"
단건을 넣어 PAGE 단락을 확정시킨다. 주입 전에 생성기가 스스로 점검해 출력한다:

- `[플래그] ✔/✘ NOISE_INVESTIGATION_TRIGGER_ENABLED`
- `[조사 서비스] ✔ 조사 서비스 localhost:9098 도달` / `✘ … 미도달 (…) — 트리거는 graceful 실패, 게이트 PAGE 판정·통보는 정상`

### 5.4 관측 지점 3곳

| 위치 | 무엇을 보나 |
|---|---|
| `logs/alarm_decisions.jsonl` | 게이트 판정(`type="decision"`) + **조사 감사(`type="investigation"` — `investigation_id`·`status`·`verdict`)** |
| `sre_agent/.data/investigation_audit.jsonl` | **JobStore**가 남기는 잡 상태 전이 — `accepted`·`duplicate`·`rejected`·`running`·`terminal`(그 안의 `status`가 `done`/`failed`/`timeout`/`stub`)·`restart_failed`. 첫 조사 시 자동 생성된다 |
| 서버 콘솔 | 트리거 노드의 graceful 실패 사유(침묵 금지) |

```bash
# [서버 A · CWD=레포 루트]
grep '"type": "investigation"' logs/alarm_decisions.jsonl | tail -1 | python -m json.tool
tail -3 sre_agent/.data/investigation_audit.jsonl 2>/dev/null || echo "(아직 조사 이력 없음)"
```

### 5.5 합격 기준

1. 게이트 판정이 `PAGE`(목업 판정기가 자동 대조).
2. `type="investigation"` 감사 레코드 생성 + `investigation_id` 존재.
3. `status`가 `stub`(키 부재 정상 경로) — 목업은 `accepted`/`duplicate`로 표시.
4. 같은 시나리오를 즉시 재주입하면 dispatcher dedup으로 `duplicate`.
5. 조사 서비스를 내린 뒤 재주입해도 **게이트 판정·통보는 정상 완료**되고 트리거만 사유를 남긴다.

---

## 6. 통보 본문에서 브리핑 블록을 실제로 보기 (worKB 스텁 수신기)

`_send_workb`는 `WORKB_BASE_URL`이 비면 `ValueError`로 끝난다 — 즉 **브리핑이 붙은 본문을
눈으로 볼 수 없다**. 로컬 에코 수신기를 띄우면 실제 HTML을 확인할 수 있다.
전송 대상은 `{WORKB_BASE_URL}/api/sendWorkbMsg`, JSON 본문의 `msgBody`가 통보 HTML이다.

```bash
# [서버 A · CWD=무관 · .venv — 통보 수신 확인용 임시 서버]
python - <<'PY'
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        d = json.loads(self.rfile.read(n) or b"{}")
        print("=" * 70); print("TITLE:", d.get("msgTitle"))
        print(d.get("msgBody", "")[:4000])
        self.send_response(200); self.send_header("Content-Type","application/json")
        self.end_headers(); self.wfile.write(b'{"result":"ok"}')
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", 28080), H).serve_forever()
PY
```

```dotenv
# [서버 A · 파일: <레포 루트>/.env]
WORKB_BASE_URL=http://127.0.0.1:28080
WORKB_BEARER_TOKEN=dummy
WORKB_SYSTEM_DIV=TEST
WORKB_SEND_ID=00000000
WORKB_USER_IDS_CSV=00000000
```

본문에서 확인할 블록:

- **자동 조사 브리핑** — 레벨 A에서는 `조사 미실행 — LLM 키 부재(스텁)`, 레벨 B에서는 6요소.
- **중요도 상향(자동 조사)** — `NOISE_FAULT_ESCALATION_ENABLED=true` + `verdict.escalate`일 때만.

> 스텁 수신기는 목업 데이터만 받는다. 실 worKB 엔드포인트를 향하게 두고 테스트하지 말 것.

---

## 7. 레벨 B — 실 HolmesGPT 조사 완주

§7.0(공통) → **§7-G(Gemini — 픽스처 검증용)** 또는 **§7-V(사내 vLLM — 채택 경로)** 중 하나를 따른다.

### 7.0 공통 — 승인 게이트와 공통 설정

**승인 게이트 (건너뛰지 말 것).** D-127은 과금 외부 API 호출을 **건별 사용자 승인**
사항으로 못박는다. 포괄 승인은 없고, `RUN_E2E=1` 설정 자체도 승인 후에 한다. 기본 스위트에는
전역 소켓 가드가 있어 실 호출이 0이다. 사내 FabriX는 외부 과금은 아니지만 **사내 자원 소비·
데이터 송신**이므로 같은 절차로 취급한다(어떤 데이터가 나가는지 §7.0의 데이터 통제 참조).

**데이터 통제.** 어느 백엔드든 LLM에 나가는 것은 **목업 알람 + Docker 픽스처 데이터**뿐이다
(운영 폴스타 미연결). 조사용 `mcp_server`가 픽스처 DB/Prometheus만 바라보게 두는 것이
그 물리적 보장이다(§4.2).

**공통 설정.** 본체 인라인 타임아웃은 늘린다 — 실 조사는 수십~수백 초라 기본 45초로는 실패한다.

```dotenv
# [서버 A · 파일: <레포 루트>/.env]
NOISE_INVESTIGATION_TOTAL_TIMEOUT_SECONDS=300
```

조사 서비스 쪽 공통 옵션:

```bash
# [서버 A · CWD=sre_agent/ · sre_agent/.venv — 기동 명령에 붙이는 env]
SEVERITY_JUDGE_ENABLED=true            # 브리핑 「권고」를 채우려면 선행 필수
REMEDIATION_RECOMMENDER_ENABLED=true   # 권고 카탈로그(D-138 · 제시 전용)
POLESTAR_MCP_URL=http://localhost:9097/sse
```

> 권고 입력은 LLM 서술이 아니라 `severity_judge`가 매칭한 **시그니처**다(환각 차단·D-035).
> `SEVERITY_JUDGE_ENABLED=false`면 매칭이 없어 권고도 비어 있다.

---

### 7-G. Gemini 백엔드 (D-120 · **검증 완료 경로**)

#### 7-G.1 설정

레포 루트의 `.encenv`에 `LLM_GEMINI_API_KEY`가 있으므로 **레포 루트를 CWD로** 기동하면 잡힌다.

```bash
# [서버 A · CWD=레포 루트 · sre_agent/.venv — ★ 루트여야 .encenv 키가 잡힌다]
cd /Users/cptkang/AIOps/collectorinfra
POLESTAR_MCP_URL=http://localhost:9097/sse \
SEVERITY_JUDGE_ENABLED=true \
REMEDIATION_RECOMMENDER_ENABLED=true \
sre_agent/.venv/bin/python -m sre_agent.run_service
```

| 설정 | 값 |
|---|---|
| `INVESTIGATION_LLM_MODEL` | `gemini/gemini-3.5-flash` (기본 · ListModels 실측 확정) |
| `GEMINI_API_KEY` / `LLM_GEMINI_API_KEY` | `.encenv` (CWD 의존 — §2.3) |

> 모델 선택 주의: `gemini-2.0-flash`는 **서버측 퇴역(404 실측)**, `gemini-2.5-*`는 D-021 사용 금지,
> `gemini-3.1-pro`는 preview만 존재. 문서 권장치가 아니라 **ListModels 가용 목록 실측**으로 고른다.

#### 7-G.2 실행 경로 3가지

**(a) e2e 테스트 — 가장 통제된 경로 (권장)**

```bash
# [서버 A · CWD=레포 루트 · sre_agent/.venv]
# 레포 루트에서 실행해야 .encenv 키가 잡힌다. mcp_server 미도달·키 부재면 skip(침묵 아님).
RUN_E2E=1 sre_agent/.venv/bin/python -m pytest sre_agent/tests/test_investigation_e2e.py -v
```

- `test_mvp_investigation_completes_or_graceful` — 완주 시 도구 인용·answer, 미완주 시 `incomplete=True`
- `test_step_limit_forces_graceful_incomplete` — step 상한 도달의 graceful 반환

승인 없이 실행 조건만 확인하려면 `RUN_E2E` 없이 돌린다 → `2 skipped`(과금 0, 2026-08-25 실측).

**(b) 목업 [12] 전 구간 — MVP 그대로.** §5와 동일하되 키가 잡힌 상태.
`status`가 `stub`이 아니라 `running` → `done`으로 진행하고 브리핑에 6요소가 채워진다.

**(c) pull 경로 — 챗에서 장애 진단 (CW-B)**

```dotenv
# [서버 A · 파일: <레포 루트>/.env]
NOISE_FAULT_DIAGNOSIS_ENABLED=true
```

`"svr-web-01 서버 원인 분석해줘"`류로 물으면 `fault_diagnosis` 의도로 라우팅되어 `sre_diagnose`에
위임하고 자연어 진단을 돌려준다. 서비스 다운·타임아웃도 침묵 폴백 없이 사유를 담아 응답한다.

#### 7-G.3 실측 참고치 (2026-07-28 MVP 실 완주)

| 항목 | 값 |
|---|---|
| 소요 | **161초** |
| PromQL 감사 | **37건** |
| 서버측 `{nodename=…}` 조립 | 실동작(D-119 실증) |
| 브리핑 | 한국어·인용 포함 6요소 |
| 모델 | `gemini/gemini-3.5-flash` |

---

### 7-V. 사내 vLLM 백엔드 (**채택 경로** · 2026-08-25 사용자 결정)

> **결정**: 조사 LLM은 **별도 vLLM**으로 세워 HolmesGPT를 구동한다.
> **근거**: 사내 FabriX(KBGenAIChat)는 OpenAI 호환이 아니고 도구를 요청에 싣지 못해 HolmesGPT를
> 구동할 수 없다(§2.2.1). D-037이 본체 트랙 B에서 같은 블로커를 vLLM으로 해소한 선례를 따른다.
> **미채택 대안**은 §7-V.6에 기록한다(조건이 바뀌면 다시 후보가 된다).

#### 7-V.1 vLLM 서빙 — tool-calling을 **명시적으로 켜야 한다**

vLLM은 OpenAI 호환 `/v1`을 제공하지만, **기본값으로는 `tools`를 받아도 `tool_calls`를 만들지
않는다.** 자동 도구 선택과 파서를 켜는 두 플래그가 필수다.

```bash
# [★ 서버 B(GPU) · CWD=무관 · vLLM 환경]
python -m vllm.entrypoints.openai.api_server \
  --model <모델 경로 또는 이름> \          # 예: Qwen/Qwen3.5-9B-Instruct
  --served-model-name Qwen3.5-9B \         # litellm에 넘길 이름
  --host 0.0.0.0 --port 8000 \
  --enable-auto-tool-choice \              # ★ 없으면 tool_calls가 안 나온다
  --tool-call-parser hermes \              # ★ 모델 계열에 맞는 파서 (Qwen 계열=hermes)
  --max-model-len 32768                     # ReAct 누적 대화가 길다 — 넉넉히
```

> **`--tool-call-parser` 값은 모델·vLLM 버전에 따라 다르다**(`hermes`·`llama3_json`·`mistral`·
> `qwen3_coder` 등). 문서 값을 믿지 말고 **§7-V.2 판정으로 실측 확인**한다 — 파서가 틀리면
> 서버는 200을 주는데 `tool_calls`가 비는 형태로 조용히 실패한다.
>
> **모델 선택**: D-037의 vLLM은 **Qwen3.5-9B(소용량)**를 *"제어평면에 계획 신호만"* 목적으로
> 골랐다. HolmesGPT 조사는 **도구 10종 안팎 × 최대 `MAX_STEPS`(기본 40) 왕복**으로 훨씬 무겁다.
> 9B로 시작하되 **§7-V.5의 완주 판정**을 반드시 통과시키고, 미완주가 반복되면 더 큰 모델로 올린다.

**컨텍스트 길이 주의**: ReAct는 매 스텝마다 이전 도구 결과를 누적해 보낸다. `--max-model-len`이
작으면 중반부터 컨텍스트 초과로 끊긴다. 폴스타 도구 반환이 JSON이라 특히 빨리 찬다.

#### 7-V.2 판정 — tool-calling이 실제로 도는가 (**HolmesGPT 붙이기 전**)

목업 도구 1개·한 문장으로 왕복만 확인한다. 여기서 막히면 그 위 단계는 전부 무의미하다.

```bash
# [서버 A(또는 vLLM에 도달 가능한 곳) · CWD=레포 루트 · sre_agent/.venv]
cd /Users/cptkang/AIOps/collectorinfra
export LLM_BASE_URL='http://<vllm-host>:8000/v1'
export LLM_API_KEY='dummy'                 # vLLM 무인증이면 아무 값
export LLM_MODEL_NAME='Qwen3.5-9B'         # --served-model-name 과 일치
sre_agent/.venv/bin/python - <<'PY'
import json, os, litellm

TOOL = {"type": "function", "function": {
    "name": "get_server_cpu_load",
    "description": "지정한 서버의 현재 CPU 사용률(%)을 반환한다. (스모크용 목업 도구)",
    "parameters": {"type": "object",
                   "properties": {"hostname": {"type": "string", "description": "대상 서버 호스트명"}},
                   "required": ["hostname"]}}}

resp = litellm.completion(
    model=f"openai/{os.environ['LLM_MODEL_NAME']}",
    api_base=os.environ["LLM_BASE_URL"],
    api_key=os.environ["LLM_API_KEY"],
    messages=[{"role": "user", "content": "server-01 서버의 현재 CPU 사용률을 확인해줘."}],
    tools=[TOOL],
    tool_choice="auto",
)
msg = resp.choices[0].message
calls = getattr(msg, "tool_calls", None)
if not calls:
    print("불합격 — tool_calls 없음. 본문:", (msg.content or "")[:300])
    print("→ --enable-auto-tool-choice 누락 또는 --tool-call-parser 불일치를 먼저 의심할 것.")
else:
    c = calls[0]
    print("합격 — 호출:", c.function.name, "| 인자:", json.loads(c.function.arguments))
PY
```

| 결과 | 원인 | 조치 |
|---|---|---|
| `합격 — 호출: get_server_cpu_load` | 정상 | §7-V.3으로 |
| `불합격` + 본문에 설명문 | **`--enable-auto-tool-choice` 누락** | 플래그 추가 후 재기동 |
| `불합격` + 본문에 도구 JSON 조각 | **파서 불일치** | `--tool-call-parser` 교체 |
| HTTP 404 `model not found` | `--served-model-name` 불일치 | 이름 맞추기 |
| 연결 실패 | 주소·포트·방화벽 | vLLM 로그 확인 |

#### 7-V.3 배선 — `api_base` (**2026-08-25 적용 완료**)

종전에는 `AgentSettings`에 `api_base`가 없어 사내 엔드포인트를 지정할 수 없었다.
**본 결정에 따라 배선을 적용했다**(코드 변경 2곳 · 기본값 `None`이라 기존 동작 불변):

| 파일 | 변경 |
|---|---|
| `sre_agent/sre_agent/settings.py` | `api_base: str \| None = None` 필드 추가 (env: **`API_BASE`**) |
| `sre_agent/sre_agent/diagnosis.py` | `Config(..., api_base=self.settings.api_base, ...)` 전달 |

검증(2026-08-25): `AgentSettings(api_base=…)` → `DiagnosisAgent._config.api_base` 도달 확인 ·
`sre_agent/tests` **164 passed·2 skipped**(기준선 불변) · `arch_check --ci` exit 0.

**스텁 게이트 주의(§2.3).** dispatcher가 실 조사를 도는 조건은 `gemini_api_key is None`이 아닌
경우 **하나뿐**이다(`investigation_dispatcher.py:142`). vLLM은 키가 필요 없을 수 있으나
**이 필드가 비면 조사가 스텁으로 떨어진다.** 당장은 아무 값이나 넣어 통과시킨다:

```bash
# [서버 A · 조사 서비스 기동 명령에 붙이는 env]
GEMINI_API_KEY=dummy   # 사실상 "조사 LLM 사용 가능" 플래그로 쓰이고 있음
```

> 이름과 의미가 어긋난 상태다. 정공법은 게이트 조건을 `investigation_api_key` 같은 **백엔드
> 중립 이름**으로 바꾸고 구 이름을 별칭 + 폐기 기한(**D-161 ①**)으로 두는 것이며,
> 이는 신규 결정 등재 대상이다(별건 작업).

#### 7-V.4 조사 서비스 기동

```bash
# [서버 A · CWD=레포 루트 · sre_agent/.venv]
cd /Users/cptkang/AIOps/collectorinfra
GEMINI_API_KEY=dummy \
MODEL="openai/Qwen3.5-9B" \
API_BASE="http://<vllm-host>:8000/v1" \
API_KEY=dummy \
POLESTAR_MCP_URL=http://localhost:9097/sse \
MAX_STEPS=40 \
SEVERITY_JUDGE_ENABLED=true \
REMEDIATION_RECOMMENDER_ENABLED=true \
sre_agent/.venv/bin/python -m sre_agent.run_service
```

| 설정 | 값 | 비고 |
|---|---|---|
| `MODEL` | `openai/<served-model-name>` | **`openai/` 접두사 필수** — litellm이 OpenAI 호환 경로로 보낸다. **`INVESTIGATION_LLM_MODEL`이 아니다**(아래 정정) |
| `API_KEY` | 아무 값 | `None`이면 litellm이 인증 헤더 없이 보내 400이 날 수 있다 |
| `API_BASE` | `http://<vllm-host>:8000/v1` | `/v1`까지 포함 |
| `GEMINI_API_KEY` | 아무 값 | 스텁 게이트 통과용(§7-V.3) |
| `MAX_STEPS` | 40(기본) | 소용량 모델은 상한 도달이 잦다 — 미완주는 graceful |

> **정정(2026-08-28 · 실측)**: 종전 이 명령은 `INVESTIGATION_LLM_MODEL`을 지정했으나
> **운영 조사 경로가 그 필드를 읽지 않는다.** `DiagnosisAgent`는 `Config(model=settings.model, …)`로
> 넘기고(`diagnosis.py:136`), `investigation_llm_model`을 읽는 곳은 `scripts/smoke_llm.py`와
> 테스트뿐이다. 실제로 종전 명령대로 주면 `Config.model`이 기본값 `anthropic/claude-sonnet-5`로
> 남고 `api_base`만 vLLM을 가리켜, litellm이 anthropic 프로바이더로 vLLM에 붙으려다 실패한다.
> ⇒ **`MODEL`로 지정한다.** 배선 도달 확인 절차는 `docs/26_sre_agent_guide.md` §5.6.4.

> **CWD 주의(§2.3 반대 방향)**: 레포 루트에서 띄우면 `.encenv`의 `LLM_GEMINI_API_KEY`도 함께
> 잡힌다. 위처럼 `GEMINI_API_KEY`를 명시 지정하면 그 값이 쓰이므로 **외부 Gemini 호출은 발생하지
> 않는다**(모델·엔드포인트가 vLLM을 가리키기 때문). 확실히 하려면 `LLM_GEMINI_API_KEY=` 로 비운다.

#### 7-V.5 완주 판정 — 여기까지 통과해야 "된다"고 말할 수 있다

§7-V.2는 **왕복 1회**만 본다. ReAct 다단계 완주는 별개이므로 실제 조사로 확인한다.

```bash
# [서버 A · CWD=레포 루트 · sre_agent/.venv]
# 픽스처 데이터 대상 실 조사 e2e (외부 과금 없음 — vLLM은 사내)
RUN_E2E=1 sre_agent/.venv/bin/python -m pytest sre_agent/tests/test_investigation_e2e.py -v
```

> 이 테스트는 `_gemini_ready()`로 게이팅되므로 §7-V.3대로 `GEMINI_API_KEY`가 채워져 있어야
> 실행된다. 그리고 `mcp_server`(조사 프로파일)가 도달 가능해야 한다.

**판정 기준**

| 관측 | 의미 |
|---|---|
| `status="done"` + 브리핑 6요소 + **도구 인용**(`←`·`출처`·도구명) | ✅ 합격 |
| `incomplete=True` 반복 | 모델이 ReAct를 못 끌고 감 → **모델 상향** 또는 §7-V.6 |
| 도구 호출 0회인데 답변만 장문 | 도구를 안 쓰고 지어냄 → 파서·프롬프트 확인, 인용 없으면 불합격 |
| 컨텍스트 초과 오류 | `--max-model-len` 상향 |
| 매우 느림(수백 초) | 통보 지연이 되므로 §9 **즉시통보+후속 브리핑**을 켠다 |

**참고 기준치**(Gemini 3.5-flash, 2026-07-28 실측): 완주 **161초**·PromQL 감사 37건.
소용량 vLLM은 이보다 느리고 스텝을 더 쓸 가능성이 높다 — 절대 비교가 아니라 **완주 여부**로 본다.

#### 7-V.6 미채택 대안 (조건이 바뀌면 재검토)

| 대안 | 왜 지금은 아닌가 |
|---|---|
| **FabriX 직결** | KBGenAIChat이 OpenAI 비호환·도구 미전송 — **프로토콜 수준 불가**(§2.2.1). 사내가 OpenAI 호환 tool-calling 엔드포인트를 별도 제공하면 재검토 |
| **B안: 고정 파이프라인**(Plan 64 §3·§4 부활) | 결정적 코드가 도구를 호출하고 LLM은 서술만 — **FabriX로도 가능**하고 D-035에 더 정합하나, 조사 루프 신규 구현이 필요. **vLLM이 §7-V.5를 통과하지 못하면 이 대안이 다시 1순위**가 된다. 그때 `mcp_server` 도구·severity_judge·briefing_builder·remediation_recommender·submit/poll 자산은 전량 재사용된다 |

이 채택은 Plan 66 **§7-1(운영 LLM 확정)** 게이트에 해당하므로, vLLM 서빙 사양(모델·파서·컨텍스트)이
확정되면 `docs/02_decision.md`에 등재한다(§5-5 규약).

---

## 8. 실 폴스타·실 Prometheus 직접 연결 (운영 데이터 경로)

§4~§7은 **Docker 픽스처**를 대상으로 한다. 여기서는 그 자리를 **실 폴스타 DB·실 Prometheus·실
알람 피드**로 바꾸는 방법을 다룬다. 바꾸는 지점은 셋뿐이고 나머지 배관은 그대로다.

```
          픽스처 경로                        실연동 경로
polestar_pg :5434  ─┐                ┌─ 실 폴스타 PG(gp·yd) / DB2(b0)
fixture_prometheus :9190 ─┼→ mcp_server →┼─ 실 Prometheus
목업 생성기 → TCP 9100 ─┘                └─ 실 폴스타 알람 → TCP 9100
```

### 8.0 ⚠️ 먼저 — 데이터 통제 게이트 (D-120 **절대 제약**)

**실 폴스타 데이터를 Gemini로 보내는 조합은 금지되어 있다.** D-120 「데이터 통제(절대 제약)」의
문구 그대로다:

> *"Gemini API는 외부 SaaS — 개발·테스트 전용, **운영 투입 금지**. 외부 송신 입력은 목업·로컬
> Docker 픽스처 데이터만 — **실 운영(폴스타) 데이터의 외부 API 송신 금지**(폐쇄망·마스킹 원칙
> 정합). 결정적 차단: 테스트 환경 `mcp_server` config.toml에는 로컬 픽스처 소스만 등록(운영
> `{NAME_UPPER}_CONNECTION` 미설정 → 소스 자동 비활성 — **물리적으로 실 데이터 접근 불가**)."*

즉 **본 절의 작업은 D-120이 세워둔 물리적 차단(연결 문자열 미설정)을 해제하는 일**이다.
해제하는 순간 조사 LLM 선택이 규정 문제가 된다.

| 데이터 \ 백엔드 | **Gemini**(외부 SaaS) | **FabriX·vLLM**(사내) |
|---|---|---|
| Docker 픽스처 (§4) | ✅ 허용 — D-120 개발·테스트 경로 | ✅ 허용 |
| **실 폴스타·실 Prometheus** | 🚫 **금지 — D-120 절대 제약 위반** | ✅ **본 절의 대상** |

> **따라서 실연동 MVP의 조사 LLM은 사내 백엔드여야 한다** — **별도 vLLM**(§7-V)이 그 경로다.
> FabriX는 OpenAI 비호환으로 조사 LLM이 될 수 없다(§2.2.1). **"vLLM이 아직 없으니 일단 Gemini로
> 실데이터를 돌려보자"는 선택지는 없다.**

실연동 전환 시 **Gemini 키가 조사 서비스에 잡히지 않도록** 명시적으로 비운다(§2.3의 CWD 함정
반대 방향):

```bash
# [서버 A · CWD=레포 루트 · sre_agent/.venv]
GEMINI_API_KEY= LLM_GEMINI_API_KEY= \
INVESTIGATION_LLM_MODEL="openai/<사내 모델명>" API_BASE="<사내 /v1>" ... run_service
```

> 실연동은 Plan 66 **P0-3**(운영 Prometheus 실측)·**§7-1**(운영 LLM 확정) 게이트에 걸려 있는
> 영역이다. 본 절은 *"연결하는 방법"*이고, **운영 전환 승인 자체는 별개**다(§11).

---

### 8.1 실 폴스타 DB 연결 (`mcp_server` 소스)

#### 8.1.1 소스명 ↔ 환경변수 규약

`mcp_server/config.toml`의 `[[sources]] name`을 **대문자로 바꾼 뒤 `_CONNECTION`**을 붙인 환경변수가
연결 문자열이다(실측 `config.py:291` — `f"{source.name.upper()}_CONNECTION"`).
**빈 값이거나 미설정이면 그 소스는 자동 비활성**된다 — 이것이 D-120의 물리적 차단 장치다.

| `config.toml` 소스 | 환경변수 | 엔진 | 용도 |
|---|---|---|---|
| `polestar` | `POLESTAR_CONNECTION` | postgresql | 단일 DB 모드 기본 |
| `polestar_cm_gp` | `POLESTAR_CM_GP_CONNECTION` | postgresql | 존: 과천/GP |
| `polestar_cm_yd` | `POLESTAR_CM_YD_CONNECTION` | postgresql | 존: 여의도/YD |
| `polestar_b0` | `POLESTAR_B0_CONNECTION` | **db2** | 존: B0 |

```bash
# [★ 서버 C(mcp_server 호스트) · 파일: <레포 루트>/mcp_server/.env]
# mcp_server/.env — 실 폴스타 (조사 배치)
POLESTAR_CM_GP_CONNECTION=postgresql://<readonly_user>:<pw>@<gp-host>:5432/<db>
POLESTAR_CM_YD_CONNECTION=postgresql://<readonly_user>:<pw>@<yd-host>:5432/<db>
POLESTAR_B0_CONNECTION=DATABASE=<db>;HOSTNAME=<b0-host>;PORT=50000;PROTOCOL=TCPIP;UID=<uid>;PWD=<pw>;
```

> DB2 연결 문자열은 URL이 아니라 **세미콜론 구분 키=값**이다(`.env.example` 실측 형식).
> `.env` 계열 파일에 **인라인 주석 금지** — pydantic-settings가 값에 포함시킨다(Known Mistakes).

#### 8.1.2 읽기 전용은 DB 계정으로 보장한다

`config.toml`의 `readonly = true`는 **`execute_sql` 도구에만** `validate_readonly`를 적용한다
(실측 `tools.py:118`). 조사 배치는 `execute_sql`을 아예 노출하지 않고(§4.2) 고수준 도구가
SELECT만 조립하지만, **실 DB에 붙는 계정 자체를 읽기 전용으로 발급**하는 것이 D-003의 실제
방어선이다. 이건 코드로 강제할 수 없다.

#### 8.1.3 방언 — 존마다 다르다

고수준 도구가 이미 분기하고 있으나(실측 `polestar_tools.py`), 결과 해석 시 알아둘 것:

| | PostgreSQL(gp·yd) | DB2(b0) |
|---|---|---|
| 스키마 한정 | 소문자 `polestar.` | **대문자 `POLESTAR.`** |
| 행 제한 | `LIMIT n` | `FETCH FIRST n ROWS ONLY` |
| 결과 칼럼명 | 그대로 | 대문자 반환 → **서버가 소문자로 정규화** |

금지 규칙도 그대로 적용된다 — `RESOURCE_CONF_ID` JOIN 금지(hostname 브릿지 조인만·D-022),
`cmm_vendor`/`cmm_os`/`cmm_os_param` lookup 미참조(D-028).

#### 8.1.4 ★ DB2(b0)는 지금 연결할 수 없다 — 드라이버 부재 (실측)

```bash
# [서버 C(mcp_server를 실행할 호스트) · CWD=레포 루트]
# 2026-08-25 실측 — 세 인터프리터 모두 미설치
for py in ~/.pyenv/versions/3.13.1/bin/python .venv/bin/python sre_agent/.venv/bin/python; do
  $py -c "import ibm_db" 2>&1 | tail -1
done      # → ModuleNotFoundError: No module named 'ibm_db'
```

`mcp_server/mcp_server/db.py`의 DB2 경로는 `import ibm_db`를 요구한다. 따라서 **b0 존은
`ibm_db` 반입·설치가 선행**된다(폐쇄망이면 wheel 반입 행정). 이것이 **D-126**이
*"실 DB 런타임 검증 = PostgreSQL 한정"*으로 스코프를 좁힌 이유이며, DB2는 여전히 방언 단위
테스트로만 커버된다. **gp·yd(PostgreSQL) 두 존만으로 MVP를 먼저 돌리는 것을 권한다.**

#### 8.1.5 확인

```bash
# [서버 C · CWD=레포 루트]
# 조사 배치 mcp_server 기동 후 — 도구가 실제로 실 DB를 읽는지
docker ps | grep polestar_pg    # 픽스처가 떠 있으면 헷갈린다. 실연동 시엔 내리거나 포트를 피할 것
# 서버 로그에 소스 활성화 라인이 뜨는지 확인(빈 연결 문자열 소스는 조용히 비활성)
```

---

### 8.2 실 Prometheus 연결

> **연동 정본**: Prometheus의 구성·기동·도구 사용·운영 편입 체크리스트는
> `docs/27_prometheus_integration_guide.md`에 한 곳으로 모았다. 이 절은 그중 실연동 절차 부분이다.

#### 8.2.1 설정 (mcp_server 전용 — `sre_agent`는 보유하지 않는다)

D-119에 따라 Prometheus 접속 정보는 **`mcp_server` 측에만** 둔다. `sre_agent`는 주소를 모른다.

| 환경변수 | 의미 |
|---|---|
| `PROMETHEUS_URL` | 실 Prometheus base URL (예: `http://prom.internal:9090`) |
| `PROMETHEUS_AUTH_HEADER` | 인증 헤더 **전체 값** (예: `Bearer <token>`). 비면 헤더 미부착 |
| `PROMETHEUS_QUERY_TIMEOUT` | 서버가 강제하는 쿼리 timeout(초, 기본 30) |
| `EXPOSE_RAW_PROMQL` | 조사 배치에서는 **`false` 유지**(§4.2) |

**미설정이면 PromQL 도구가 전건 실패**한다(침묵 폴백 금지 — 명시적 오류 반환).

#### 8.2.2 ★ 전제는 `nodename` 라벨 규약이다 (R-D · P0-3)

고수준 도구는 `hostname`(=폴스타 `server_name`) 인자에서 **서버가 `{nodename="<hostname>"}`을
결정적으로 조립**한다(LLM은 라벨을 만지지 않는다 — D-035 3차 방어). 따라서 **실 Prometheus의
메트릭에 `nodename` 라벨이 없거나 값이 폴스타 서버명과 다르면 조회가 전건 빈 결과**가 된다.

Docker 픽스처에서 2026-08-06에 실측한 사실(참고 기준):

- `nodename` 커버리지 **1404/1404 = 100%** — 스크레이프 `static_configs.labels`로 **수집 시점에
  주입**되므로 job과 무관하게 전 메트릭이 보유한다.
- node_exporter가 `node_uname_info`에 싣는 자기 `nodename`은 타깃 라벨과 충돌해
  **`exported_nodename`으로 밀린다**(타깃 라벨이 승리 → 조립 안전). 소비 측은 반드시 `nodename`을 쓴다.

**실 Prometheus에서 같은 것을 측정하는 절차**(P0-3의 실체):

```bash
# [★ 실 Prometheus에 도달 가능한 곳(보통 서버 C) · CWD=무관]
PROM=http://prom.internal:9090

# ① 무인증 여부·도달성
curl -s -o /dev/null -w '%{http_code}\n' "$PROM/api/v1/status/buildinfo"

# ② nodename 라벨 존재 여부와 값 목록
curl -s "$PROM/api/v1/label/nodename/values" | python -m json.tool | head -20

# ③ 커버리지 — 전 시리즈 대비 nodename 보유 시리즈
curl -s --data-urlencode 'query=count({__name__=~".+"})'            "$PROM/api/v1/query" | python -c 'import json,sys;print("전체:",json.load(sys.stdin)["data"]["result"])'
curl -s --data-urlencode 'query=count({__name__=~".+",nodename!=""})' "$PROM/api/v1/query" | python -c 'import json,sys;print("nodename 보유:",json.load(sys.stdin)["data"]["result"])'

# ④ 폴스타 서버명과 실제로 일치하는가 — 대표 호스트 1건으로 왕복
HOST='<폴스타 server_name 하나>'
curl -s --data-urlencode "query=up{nodename=\"$HOST\"}" "$PROM/api/v1/query" | python -m json.tool

# ⑤ 보존 기간 (조사 시점 range 조회 가능 범위)
curl -s "$PROM/api/v1/status/runtimeinfo" | python -m json.tool | grep -i retention
```

> **명령 자체의 자가 검증**: 위 5개를 `PROM=http://localhost:9190`(픽스처)으로 먼저 돌려보면
> `200` / `['svr-web-01']` / `1404`·`1404` / `2 시리즈` / `15d`가 나온다(2026-08-25 재현 확인).
> 실서버에서 결과가 다르면 **명령이 아니라 환경이 다른 것**이다.

**판정**

| ③ 커버리지 | ④ 일치 | 결론 |
|---|---|---|
| 높음 | 일치 | 그대로 진행 |
| 높음 | **불일치**(예: FQDN vs 단축명) | 폴스타 `server_name` ↔ `nodename` **정규화 규약**을 먼저 합의(P0-3 협의 항목) |
| 낮음/0 | — | 스크레이프 설정에 `nodename` 타깃 라벨 주입이 필요 — **인프라 소유자 협의**(§7-3 아님·행정) |

> 라벨 표준화 없이 조사를 돌리면 **"도구는 성공했는데 데이터가 비어 있는"** 상태가 되고,
> LLM은 그 공백을 서술로 메우려 한다. 이 규약이 D-119 서버측 조립 설계의 **전제**다.

#### 8.2.3 게이트(노이즈 캔슬링) 쪽 Prometheus는 별개다

조사 경로와 달리 **게이트 측 Prometheus 채널은 현재 배선되어 있지 않다.**

```dotenv
# [서버 A · 파일: <레포 루트>/.env]
ALARM_PROMETHEUS_ENABLED=false                 # 프로덕션 참조 0건
ALARM_PROMETHEUS_BASE_URLS_CSV=polestar_cm_gp=http://prom-gp:9090,polestar_cm_yd=http://prom-yd:9090
ALARM_PROMETHEUS_TIMEOUT_SECONDS=3
```

`noise_gate/infrastructure/prometheus_client.py`는 구현은 있으나 **호출부가 0건**이며
(`polestar_metric_baseline.py:24`가 *"§5.2 확정 설계상 배선하지 않는다"*로 사유를 남겼다),
`plans/70` P1-1에서 **처리 방식 택1이 대기 중**이다. 즉 존별 CSV는 지금 채워도 아무 효과가 없다.
조사 경로 Prometheus(§8.2.1)와 혼동하지 말 것.

---

### 8.3 실 알람 피드 (목업 생성기 → 실 폴스타 TCP)

수신부는 그대로 두고 **송신자만 바뀐다** — 실 폴스타가 TCP로 단일행 JSON을 보낸다.

```dotenv
# [서버 A · 파일: <레포 루트>/.env 또는 alarm_server.env]
ALARM_SERVER_SOCKET_HOST=0.0.0.0      # 외부 수신이므로 루프백 금지
ALARM_SERVER_SOCKET_PORT=9100
ALARM_SERVER_REDIS_HOST=<redis-host>
ALARM_SERVER_REDIS_PORT=<redis-port>  # ★ 워커의 REDIS_PORT와 반드시 일치(§4.5)
ALARM_SERVER_STREAM_KEY=alarm:raw
```

확인 순서:

1. **방화벽·바인드** — 폴스타 → 수신부 9100 인바운드 허용. `0.0.0.0` 바인드 확인.
2. **적재** — 알람 1건 도착 시 Redis Stream `alarm:raw` 길이 증가.
3. **소비** — 워커 로그에 판정 라인, `logs/alarm_decisions.jsonl`에 `type="decision"`.
4. **존 매핑** — 이벤트의 `db_id`가 실제 존(`polestar_cm_gp`/`polestar_cm_yd`/`polestar_b0`)으로
   들어오는지. 존이 틀리면 노이즈 컨텍스트 조회가 엉뚱한 DB로 간다.

**존별 실 엔드포인트 매핑**(이미 `.env`에 실주소 형태로 존재):

```dotenv
# [서버 A · 파일: <레포 루트>/.env]
ALARM_PROCESS_API_BASE_URLS_CSV=polestar_cm_gp=http://polestar.kbonecloud.com,polestar_cm_yd=http://yd-polestar.kbonecloud.com,polestar_b0=http://10.37.16.51:9010
```

조사 도구 `polestar_process_snapshot`이 쓰는 것은 **`mcp_server` 쪽** 설정이다:

```dotenv
# [★ 서버 C · 파일: <레포 루트>/mcp_server/.env]
PROCESS_API_BASE_URL=<조사 대상 존의 프로세스 API base_url>   # 비면 도구가 오류 반환
```

> 프로세스 args/커맨드라인은 **서버가 마스킹**한다(우회 불가). 실데이터에서는 이 경로에
> 자격증명이 섞여 들어올 수 있으므로 마스킹 동작을 실제 출력으로 한 번 확인할 것.

---

### 8.4 권장 전환 순서 (한 번에 다 바꾸지 말 것)

각 단계마다 §5.4의 관측 지점으로 확인하고 다음으로 넘어간다. 한꺼번에 바꾸면 실패 원인이
DB인지 Prometheus인지 라벨인지 알 수 없다(Known Mistakes — 라우팅부터 확정).

| 단계 | 바꾸는 것 | 확인 |
|---|---|---|
| 0 | (사전) §7-V.5 완주 판정 통과 + §8.0 데이터 통제 확인 | vLLM 조사 완주 확인 · 외부 Gemini 미사용 |
| 1 | **Prometheus만** 실서버로 (DB·알람은 픽스처) | §8.2.2 ②③④ 왕복 성공 |
| 2 | **폴스타 PG(gp 또는 yd) 1존** 추가 (b0 제외) | `polestar_resource_status` 실 hostname 반환 |
| 3 | 조사 1건 완주 (목업 알람 [12] + 실 데이터 도구) | 브리핑 6요소 + **실 hostname 인용** |
| 4 | **실 알람 피드**로 전환 | `type="decision"` → `type="investigation"` 연쇄 |
| 5 | 나머지 존 확대 (b0는 `ibm_db` 반입 후) | 존별 라우팅 정합 |

> 3단계까지가 *"실 데이터로 MVP가 돈다"*의 실체다. 4단계부터는 운영 트래픽이므로
> 통보 대상(`WORKB_USER_IDS_CSV`)을 **테스트 수신자로 좁혀 두고** 시작한다.

### 8.5 실연동에서 추가로 지켜야 할 것

- **마스킹** — 민감 정보(password/token/api_key/credential 등)는 LLM·UI·worKB 노출 **전에**
  마스킹된다. 실데이터에서 실제로 마스킹되는지 산출 파일의 전 칼럼으로 확인한다(미리보기 일부 ✗).
- **감사** — 모든 쿼리 실행은 감사 로그에 남는다. 실행 SQL 파일 로그는 `logs/sql/YYYY-MM-DD.sql`
  (D-140 — `mcp_server`도 같은 루트에 append).
- **읽기 전용** — 계정 권한으로 보장(§8.1.2). 조사 배치는 `execute_sql`·`raw_promql` 비노출 유지.
- **조사 예산** — 실 알람 볼륨에서는 폭주 가드를 반드시 켠다:
  `INVESTIGATION_MAX_CONCURRENT` · `INVESTIGATION_HOURLY_BUDGET` · `INVESTIGATION_DEDUP_TTL_SECONDS`.
- **통보 지연** — 실 조사는 수십~수백 초다. 실 알람 경로에서는 §9의 **즉시통보 + 후속 브리핑**을
  켜는 것을 기본으로 본다(D-137이 만들어진 이유가 이것이다).
- **P0-3 산출물** — §8.2.2의 측정 결과(커버리지·일치·보존·인증)는 Plan 66 P0-3의 실측 산출물이다.
  확정되면 `docs/02_decision.md`에 등재하고 Plan 66 §1.5 잔여 ②를 갱신한다.

---

## 9. 3-E 즉시통보 + 후속 브리핑 모드 (D-137)

레벨 B에서 161초는 곧 **PAGE 통보 지연**이다. 옵트인으로 분리한다.

```dotenv
# [서버 A · 파일: <레포 루트>/.env]
NOISE_INVESTIGATION_FOLLOWUP_ENABLED=true
NOISE_INVESTIGATION_FOLLOWUP_TIMEOUT_SECONDS=300
NOISE_INVESTIGATION_FOLLOWUP_MAX_INFLIGHT=8
```

동작: 트리거는 **submit까지만** → 통보 즉시 발송(지연 0) → 백그라운드 태스크가 poll 완주 →
**브리핑을 후속 메시지로 별도 발송**. 검증 포인트:

1. 첫 통보가 조사 완주를 기다리지 않고 즉시 나간다(스텁 수신기에 바로 찍힘).
2. 수십 초 뒤 두 번째 메시지가 브리핑만 담아 도착한다.
3. 원 통보가 실제 발송된 경우에만 후속을 보낸다(`notifications_sent["workb"]` 검사) — 빈 후속은 발송하지 않는다.
   ⇒ **`WORKB_BASE_URL`이 비어 있으면 즉시 통보가 실패하므로 후속도 아예 나가지 않는다.** 이 모드를 검증하려면 §6 스텁 수신기가 필수다.
4. 후속 실패·타임아웃은 감사에 사유가 남고 통보·판정에는 영향이 없다.

---

## 10. 문제 해결 — 증상 → 원인 → 확인

| 증상 | 유력 원인 | 확인 |
|---|---|---|
| 이벤트를 넣어도 아무 로그가 없다 | **수신부/워커 Redis 포트 불일치**(§4.5) | `docker port collectorinfra-redis` → 6380. `ALARM_SERVER_REDIS_PORT` 확인 |
| `type="investigation"` 레코드가 없다 | 트리거 플래그 off | `grep NOISE_INVESTIGATION_TRIGGER_ENABLED .env` |
| `investigation_id`가 비어 있다 | 조사 서비스 미도달(graceful 실패) | 목업의 `[조사 서비스]` 라인 · 9098 포트 점검 |
| 승인 안 했는데 실 LLM이 돌았다 | **레포 루트에서 `run_service` 기동**(§2.1) | `cd sre_agent` 후 기동하거나 키를 명시적으로 비운다 |
| PromQL 도구가 전건 실패 | `PROMETHEUS_URL` 미설정 | `mcp_server`를 §4.2 환경변수로 재기동 |
| LLM이 step만 소진하고 못 끝낸다 | `execute_sql`/`raw_promql` 노출(D-122) | `EXPOSE_EXECUTE_SQL=false EXPOSE_RAW_PROMQL=false` |
| 브리핑 「권고」가 항상 비어 있다 | `SEVERITY_JUDGE_ENABLED=false` | 권고 입력은 judge 매칭 시그니처다 — 먼저 켠다 |
| 통보 본문을 볼 수 없다 | `WORKB_BASE_URL` 미설정 → `ValueError` | §6 스텁 수신기 |
| 조사가 45초에 끊긴다 | 본체 인라인 타임아웃 기본값 | `NOISE_INVESTIGATION_TOTAL_TIMEOUT_SECONDS=300` 또는 §9 후속 모드 |
| 수신부가 9100 바인드 실패 | **node_exporter가 9100 선점**(§3.4) | 둘 중 하나를 이동 — 픽스처는 9101로 재배치했다 |
| `mcp_server`를 띄웠는데 조사가 raw SQL을 쓴다 | **본체 프로파일 인스턴스**에 붙었다 | 조사용은 별 포트(9097)·`EXPOSE_*=false`(§3.1·§4.2) |
| `sre_agent`에 외부에서 못 붙는다 | **`127.0.0.1` 고정 바인드**(`mcp_service.py:32` · env 오버라이드 없음) | **①과 같은 서버에 두는 것이 전제**(§3.3.2). 분리하려면 바인드 설정화 + 인증 강제가 선행 |
| `NOISE_INVESTIGATION_SERVICE_URL`을 원격 주소로 바꿨는데 연결 실패 | 위와 동일 — 루프백은 원격에서 도달 불가 | 원격 분리는 현 구성에서 **불가**(§3.3.2 🔒) |
| 폴스타 알람이 안 들어온다 | ②는 **유일한 inbound** — 방화벽 미개방 | 폴스타 → 서버 A 9100/TCP 개방 확인(§3.3.5) |
| ③이 폴스타 DB·Prometheus에 못 붙는다 | 망이 갈려 있음 | ③을 **데이터 접근 가능한 서버 C로 분리**(§3.3.1·§3.3.2) |
| 두 번째 주입이 `duplicate` | dispatcher fingerprint dedup(정상) | `INVESTIGATION_DEDUP_TTL_SECONDS` 확인 |
| `--path redis`로 넣었는데 무반응 | 목업의 `--redis-url` 기본이 **6379** | `--redis-url redis://localhost:6380/0` 명시 |
| 조사가 계속 `incomplete` | 모델이 ReAct를 못 끌고 감(소용량) | 모델 상향 · `--max-model-len` 확인 → 안 되면 §7-V.6 B안 |
| `tool_calls`가 안 나온다 | **`--enable-auto-tool-choice` 누락 · 파서 불일치** | §7-V.2 판정표 |
| vLLM을 붙였는데 `status="stub"` | 스텁 게이트 조건이 `gemini_api_key` **단일** | §7-V.3 — `GEMINI_API_KEY=dummy` |
| 브리핑 본문이 비어 온다 | 사내 게이트웨이 PII 필터 차단 | `docs/pii_filtering_rules.md` · 게이트웨이 로그 |
| 브리핑에 인용이 없다 | 도구를 안 부르고 지어냄 | 인용 마커(`←`·`출처`·도구명) 확인 → §7-V.2 재판정 |
| **(실연동)** 도구는 성공인데 메트릭이 **빈 배열** | `nodename` 라벨 부재·값 불일치 | §8.2.2 ②③④ — 라벨 표준화는 P0-3 협의 |
| **(실연동)** 폴스타 도구가 소스를 못 찾는다 | `{SOURCE_NAME_UPPER}_CONNECTION` 미설정 → **자동 비활성** | `config.toml` 소스명과 env 키 대조(§8.1.1) |
| **(실연동)** b0(DB2) 조회가 `ModuleNotFoundError` | **`ibm_db` 미설치**(실측 — 전 인터프리터) | 반입·설치 선행. 우선 gp·yd만으로 진행(§8.1.4) |
| **(실연동)** 실데이터인데 조사 LLM이 Gemini | **D-120 절대 제약 위반** | 즉시 중단 → §8.0. 사내 백엔드로 전환 |
| **(실연동)** 통보가 수백 초 늦다 | 인라인 첨부 모드 | §9 즉시통보 + 후속 브리핑 |
| 목업이 플래그 off를 못 읽는다 | 비-editable `.venv/src` stale 사본 | `pip install -e . --no-deps` |

---

## 11. 회귀 기준선 (변경 후 무회귀 확인)

```bash
# [서버 A · CWD=레포 루트에서 시작 · 각 패키지는 서브셸로 진입]
# ※ cd를 연달아 쓰면 두 번째 cd가 실패한다(이미 하위 디렉토리에 있으므로) — 서브셸로 감쌀 것
.venv/bin/python -m pytest noise_gate/tests -q              # 1040 passed · 9 skipped · 4 failed(사전 존재)
( cd mcp_server && ../.venv/bin/python -m pytest tests -q ) # 183 passed · 2 skipped   ← 서버 C 배포 시 그쪽에서
( cd sre_agent  && .venv/bin/python -m pytest tests -q )    # 164 passed · 2 skipped
.venv/bin/python scripts/arch_check.py --ci                 # exit 0 (202파일 · error 0)
( cd sre_agent && .venv/bin/python scripts/arch_check.py --ci )  # exit 0
```

(2026-08-25 실측. `noise_gate`의 4 failed는 클린 기준선에도 동일한 사전 존재분이다.)

---

## 12. 실행 기록을 Plan 66 진행에 쓰는 법

Plan 66의 잔여 웨이브(§13)를 이어서 진행할 때, **먼저 대장을 읽고 시작한다.**
"직전에 무엇이 어떤 환경에서 어떻게 동작했는가"를 코드에서 재추론하지 않기 위해서다.

> **기록은 `logs/` 아래에만 있고 `logs/`는 gitignore다** — 즉 **테스트를 실행한 그 호스트에만**
> 남는다. 다른 작업자·세션에 넘겨야 하면 ①`logs/mvp_test/` 폴더를 첨부하거나
> ②요지를 **Plan 66 §8 변경 이력**에 옮겨 적는다(모델·판정·소요·커밋 4가지면 충분하다).
> 그래서 §12.3의 "기록이 없을 때" 절차가 예외가 아니라 **자주 밟는 경로**다.

### 12.1 착수 전 3분 — 대장으로 현재 위치 확인

```bash
# [서버 A · CWD=레포 루트]
tail -20 logs/mvp_test/mvp_test_log.md         # 최근 실행 이력(사람이 읽는 누적)
tail -3 logs/mvp_test/runs.jsonl | python -m json.tool   # 지문 상세(같은 폴더)
```

읽는 순서와 해석:

| 열 | 무엇을 판단하나 |
|---|---|
| **레벨** | `A`만 있고 `B`가 없다면 **실 조사는 아직 한 번도 완주하지 않았다** — §7-V부터다 |
| **결과** | 마지막 행이 FAILED면 **그 원인부터** 해결한다. 새 기능을 얹기 전이다 |
| **커밋** | 마지막 실행 이후 코드가 얼마나 움직였는지(`git log <커밋>..HEAD`). `+dirty`면 그 실행은 커밋되지 않은 상태에서 나온 결과라 **재현 불가**로 취급한다 |
| **관측 요약** | 레벨 A는 `tiers`·`status`, 레벨 B는 `완주`·`도구호출`·`토큰` |
| **소요** | 통보 지연 판단 근거 — 수십 초를 넘으면 §9 후속 브리핑 모드가 사실상 필수다 |

`runs.jsonl`의 `env` 지문에서 추가로 보는 것:

- `env.llm.backend` / `investigation_llm_model` / `api_base` — **어떤 모델로 나온 결과인가**.
  모델이 다르면 완주·미완주를 **비교하면 안 된다**(§7-V.5 판정의 전제).
- `env.flags` — 그 실행에서 트리거·후속·escalate 플래그가 실제로 켜져 있었는지.
- `env.run_e2e` — 옵트인 실행이었는지(비어 있으면 실 LLM 호출은 없었던 실행).

### 12.2 웨이브별 — 대장에서 무엇을 확인하고 착수하나

| 잔여 웨이브 | 착수 전 대장에서 확인할 것 |
|---|---|
| **§7-V vLLM 도입**(§7-1 게이트) | 레벨 B 행의 `완주`·`도구호출`. 모델을 바꿔가며 실행하면 **행이 쌓여 모델별 완주율 비교표**가 된다 — 그것이 곧 게이트 판정 근거다 |
| **R10 원격 실연동**(P0-3) | 마지막 레벨 B 행이 **픽스처 기준으로 PASS**인지. 픽스처에서 안 되는 것이 실서버에서 될 리 없다 |
| **R11 E8**(P0-4) | 레벨 A 행의 `tiers` — 게이트 판정이 안정적인지. E8은 게이트에 probe를 더하는 작업이라 기준선이 필요하다 |
| **3-E 후속 모드**(D-137) | 레벨 B의 `소요`. 이 값이 후속 모드 필요성의 정량 근거다 |
| **운영 전환** | 레벨 B가 **실 데이터(§8)** 조건에서 PASS인 행이 있는지. 픽스처 PASS만으로 전환하지 않는다 |

### 12.3 기록이 없을 때 / 오래됐을 때

- **대장이 비어 있다** → MVP가 한 번도 실행되지 않은 상태다. §0.2로 프로세스를 띄우고
  **레벨 A부터** 돌려 기준선을 만든다(과금 0).
- **마지막 행이 오래됐다** → 그 사이 코드가 움직였으므로 **그대로 신뢰하지 않는다.**
  잔여 웨이브 착수 전에 레벨 A를 한 번 다시 돌려 기준선을 갱신한다.
- **`+dirty` 행뿐이다** → 재현 불가 기록이다. 커밋된 상태에서 한 번 더 돌린다.

### 12.4 기록에 남기지 않는 것

키·토큰·연결 문자열은 **값을 남기지 않는다**(`api_key_set: true/false`처럼 설정 여부만).
`logs/`는 커밋되지 않지만 **파일 첨부·화면 공유로 밖으로 나갈 수 있다** — 대장에는 요약 수치만
남기고 브리핑 본문 같은 운영 정보는 싣지 않는다. 특히 실 데이터 조사(§8) 결과를 옮겨 적을
때는 호스트명·계정·연결 문자열이 섞이지 않았는지 확인한다.

---

## 13. 이 가이드로 검증되지 않는 것 (운영 전환 전 잔여)

Plan 66 §1.5 기준 — 전부 **코드 외 선행조건**이라 현 환경에서는 재현할 수 없다.

| # | 잔여 | 막고 있는 것 |
|---|---|---|
| R10 | 운영 Prometheus 실연동·`nodename` 라벨 표준화 | P0-3 인프라 실측. **연결 방법·측정 절차는 §8.2에 문서화했으나, 실서버 측정과 라벨 표준화 협의 자체는 미완**이다 |
| R11·R12 | E8 L3 게이트(폴스타 에이전트 스냅샷 채널) | P0-4 벤더 협의 |
| R14·R15 | Text-to-SQL 잔여·EX 라이브 재측정 | 실 DB 접속·데이터 적재 |
| — | **운영 LLM 확정** | §7-1 게이트 — **사내 vLLM 채택**(2026-08-25 사용자 결정 · §7-V). 잔여는 **서빙 사양 확정**(모델·`--tool-call-parser`·`--max-model-len`)과 **§7-V.5 완주 판정**, 그리고 `docs/02_decision.md` 등재 |
| — | DB2 실 인스턴스 런타임 검증 | D-126으로 스코프 밖 보류(검증은 PostgreSQL 한정). **추가 실측(2026-08-25): `ibm_db`가 어느 인터프리터에도 미설치** — 드라이버 반입이 선행 조건(§8.1.4) |
| — | **자동 조치 실행** | **착수 금지 유지** — D-003 예외 거버넌스 미확정. `sre_agent`에 실행 경로가 없음을 테스트로 고정 |

---

## 14. 참조

- 계획: `plans/66-sre-agent-integrated-implementation-plan.md`(§2 아키텍처·§3 Phase·§1.5 잔여) ·
  `plans/sre-agent/02·04·05·06` · `plans/64`(§0.2 CW) · `plans/65`(목업 §5.3)
- 결정: `docs/02_decision.md` — D-118~D-127 · D-137 · D-138 · D-139
- 인접 가이드: `docs/16`(Plan 52 게이트 E2E) · `docs/20`(Plan 60 기능별 §8 목업 사용법) ·
  `docs/09`(DB 설정) · `docs/19`(임베딩 모델 설치)
- **실행 기록**: `noise_gate/tests/mvp_record.py`·`sre_agent/tests/mvp_record.py`(테스트 내장 기록기 · §0.1) ·
  **`logs/mvp_test/mvp_test_log.md`(실행 대장)** · `logs/mvp_test/runs.jsonl`(지문 상세) — 둘 다 `logs/` 아래(gitignore) ·
  해석 방법은 **§12**
- **배치 근거**: `plans/sre-agent/05` §2(배치 구성 — 별도 프로세스·별도 venv 단일 프로파일) ·
  `plans/sre-agent/06` §1(**에이전트 중앙 1곳 실행 · 대상 VM 미배포 · SSH 미채택**)·§8.1(픽스처 포트 재배치 근거) ·
  `CLAUDE.md`(패키지 경계 표 — 실행 형태)
- 코드 진입점: `noise_gate/alarm_server/__main__.py` · `src/api/server.py:379`(워커 기동) ·
  `noise_gate/application/nodes/investigation_trigger.py` · `sre_agent/sre_agent/run_service.py` ·
  `mcp_server/mcp_server/__main__.py`
- **백엔드 판정 근거**: `src/clients/fabrix_client.py`(`_build_payload`가 `tools` 미전송 ·
  `_parse_response`의 Few-shot JSON 파싱) · `docs/02_decision.md` **D-037**(tool-calling 블로커 ↔
  vLLM 제어평면/FabriX 데이터평면 분리) · `sre_agent/sre_agent/diagnosis.py`(`Config` 생성부) ·
  `sre_agent/sre_agent/application/investigation_dispatcher.py:142`(스텁 게이트) ·
  `sre_agent/scripts/smoke_llm.py`(Gemini 2단계 스모크 — vLLM판은 §7-V.2 스니펫) ·
  **`src/clients/fabrix_kbgenai.py`**(사내 FabriX 실제 클라이언트 — OpenAI 비호환 근거) · `src/llm.py:266`(클라이언트 분기)
- 버전 실측(2026-08-25): holmesgpt **0.36.0** · litellm **1.89.0** ·
  holmes `Config`는 `model`·`api_key`·**`api_base`**·`api_version` 필드 보유
