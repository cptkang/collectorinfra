# 105. Hermes Agent 도입 검토 — deepagents(사다리 1단) 대체 가능성 · 주요 기능 · 소스 실측 · 적용 형태

> **작성일**: 2026-09-17
> **상태**: 검토 완료 · 계획(미구현) — 사용자 확정 게이트 G-1~G-5 대기(§11) · 파일명 `-TODO`
> **성격**: 외부 에이전트 프레임워크 도입 검토 + 조건부 PoC 계획(코드 0건)
> **요청 취지(사용자 지시 원문, 2026-09-17)**:
> 1. *"deep agent대신 최근 발표한 에르메스 에이전트를 사용하려고 한다. 검토해봐라."*
> 2. *"에르메스 에이전트는 github에서 소스를 직접 받을 수 있어 폐쇄망 설치에는 문제는 없어 보인다. github의 소스를 보고 다시 검토해봐라."*
> 3. *"현재 검토한 내용을 별도의 계획파일에 정리하고 이 계획은 에르메스 에이전트의 주요 기능을 포함하여 정리하라."*
>
> **상위/연결 계획**: `plans/102` v3(기준 경로 3단 · D-225 예약) · `plans/103`(3단 LangGraph 동등성 · D-226 예약 — **이 검토의 대안 D와 같은 방향**) ·
> `plans/100`(MLX provider · D-222 — 스모크 무대) · `plans/48`·`plans/49`(D-037 트랙 B = 실제 deepagents 패키지) · `plans/101`(기능 소유 패키지 편입 선례)
> **관련 결정**: D-003(읽기 전용 3중 방어) · D-037 · D-092·D-093(1단 빈 응답 재개) · D-095·D-203(선행 결과 게이트) · D-127(과금 승인) · D-139(패키지 경계) ·
> D-161(경로 폐기 실측 4항) · D-174(vLLM = deepagents 전용) · D-181(`mcp<2`) · D-197(HolmesGPT RCA) · D-222(과금 판정 평면) · **D-225**·**D-226**(예약)
> **신규 결정 예약**: 없음 — G-1 확정 뒤 채번한다(`docs/02_decision.md` 「채번 이력」 등재 필요).
> **실측 기준**:
> - Hermes: GitHub `NousResearch/hermes-agent` 릴리스 태그 **`v2026.9.14` = 0.21.3**(commit `345cd2b0`, 2026-09-14). 태그 고정 worktree로 읽었다.
> - 저장소: `multiintent` HEAD `c64ef98` + 미커밋 작업 트리 · 루트 venv Python 3.12.11 · deepagents 0.6.10 · langgraph 1.2.11 · langchain-openai 1.3.2.
> - 실험은 **세션 scratchpad의 격리 venv**에서만 했다(루트 venv·저장소 코드 무변경). LLM 호출은 **로컬 MLX(127.0.0.1:8080, 비과금 — D-222)** 뿐이다.
>
> **근거 표기**: **실측** = 실행해서 확인 · **코드** = 소스 정독(`파일:라인`, Hermes 경로는 태그 루트 기준) · **문서** = 공식 문서 · **추정** = 검증 안 한 추론

---

## 0. 요약

### 0.1 결론

| 질문 | 답 | 근거 |
|---|---|---|
| 폐쇄망에 설치할 수 있나 | **예.** 공식 라이브러리 경로가 소스 checkout 후 editable 설치다. wheel/sdist 빌드는 Nix 밖에서 **코드로 막혀 있다**. 단 실행 중 pip 자동 설치(lazy install)를 반드시 꺼야 한다 | §4.1 |
| 루트 venv에 같이 넣을 수 있나 | **예(override 필요).** exact pin은 API 요구가 아니라 2026-05-12 공급망 정책이다. `openai==2.26.0` override로 루트 전체 extra와 해석·설치·실행된다. 대가: 현 venv 대비 **하향 6종 · 신규 20종** | §4.1 |
| deepagents 자리에 넣어 동작하나 | **동작은 한다.** 우리 형태의 async 도구로 MLX Qwen3.5-9B에서 도구 호출→정답까지 완주(94초) | §4.2 |
| 그럼 1단 대체를 권하나 | **아니다(서버 프로세스 내 대체).** 막는 것이 설치에서 운영으로 바뀌었다 — ①서버 프로세스 전역 오염(로거·stdout·`sys.path`·`os.environ`) ②요청별 도구 주입 경로 부재 → 동시 요청 권한 누수 위험 ③Hermes 고유 가치(학습·메모리·스킬·게이트웨이)를 전부 꺼야 우리 원칙이 지켜져, 남는 것이 deepagents와 같은 도구 루프 ④끌 수 없는 평문 디스크 기록 ⑤API 안정성 보증 없음 | §4.3 · §5 · §6 |
| 쓸 자리가 있나 | **별도 프로세스라면 있다.** (B) 1단을 별도 워커 프로세스로 격리, (C) 운영자 비서형 독립 프로세스(MCP로 `mcp_server` 연결). 권고는 **(D) 미도입 + `plans/103` 유지 + 설계 아이디어 차용**, 운영자 비서 수요가 확인되면 (C) | §8 |

### 0.2 정정 이력 — 1차 검토의 오판

1차 검토(같은 날)는 `uv pip compile` 해석 실패만 보고 **"같은 venv 불가 · 별도 프로세스 필수"**라고 적었다.
- `hermes-agent`의 `openai==2.24.0` ↔ `langchain-openai 1.3.2`의 `openai>=2.26.0`
- `hermes-agent[mcp]`의 `mcp==2.0.0` ↔ D-181 `mcp<2`

소스 재검토에서 뒤집혔다.
- 핀은 공급망 정책의 산물이다(`pyproject.toml:20-32` 주석 — *"every direct dep is exact-pinned … tightened on 2026-05-12 in response to the Mini Shai-Hulud worm"*, 커밋 `c1eb2dcda7`).
- MCP 클라이언트는 mcp 1.x/2.x를 **양쪽 다 지원**하고, 코어는 mcp 없이 import된다(코드 — `tools/mcp_tool.py:82-89,157-166`).
- override 설치 후 import·실행까지 됐다(실측 §4.1).

→ `docs/18_known_mistakes.md` 2026-09-17 행에 기록했다(방지책: 핀 사유 확인 → `--override` 해석 → 격리 venv 실행 실측 뒤에만 "불가" 판정).

### 0.3 권고 한 줄

**Hermes를 사다리 1단의 in-process 대체로 쓰지 않는다.** 기준 경로가 3단 `semantic_router`(D-225 예약)이고 1단 기능을 3단 LangGraph로 옮기는 `plans/103`(D-226 예약)이 진행 중이므로, Hermes에서는 **설계 아이디어만 차용**(§8.3)한다. 운영자용 대화형 비서·메신저 연동 수요가 확인될 때 **독립 프로세스(C)**로 별도 검토한다.

---

## 1. Hermes Agent 개요

| 항목 | 값 | 근거 |
|---|---|---|
| 정체 | Nous Research의 오픈소스 **자율 에이전트**. "The agent that grows with you" — 학습 루프(메모리·스킬 자가 생성)를 내장한 개인 비서형 에이전트 | 문서 · `README.md` |
| 라이선스 | **MIT** (Copyright (c) 2025 Nous Research) | 코드 — `LICENSE` |
| 이력 | 첫 커밋 2025-07-22 · 공개 2026-02 · 태그 40개 · 커밋 36,383건 · 최신 v0.21.3(2026-09-14) | 실측 — `git log` |
| 규모 | Python 약 76만 줄(tests·website 제외) · toolset 60개 · 도구 모듈 265개 파일 | 실측 |
| 언어·런타임 | Python `>=3.11,<3.14` · 일부 UI는 Node.js(TUI·desktop·web) | 코드 — `pyproject.toml` |
| 프레임워크 의존 | **LangChain/LangGraph 미사용** — 자체 루프 + openai SDK(+선택 anthropic 등) | 코드 |
| 배포 | 셸 설치기 · Docker · Nix. **wheel/sdist 빌드 차단**(Nix 밖, `setup.py:34-72`) · 라이브러리 사용은 소스 checkout editable 설치 | 코드 · 문서 |
| 변경 속도 | 태그 이후 3일간 main 커밋 **1,680건**(09-15 946 · 09-16 604) · 2026-09 대규모 모듈 분해(PR #102117) | 실측 · 코드 — `COMPAT_MANIFEST.md:1-11` |
| API 안정성 | *"Internal import paths are not a stable API"* — 호환 포인터는 임시(2026-09-14 제거 예정). `AIAgent`·`run_conversation`을 안정 API로 선언한 문서 없음 | 코드 — `COMPAT_MANIFEST.md:3-9` |

---

## 2. 주요 기능 카탈로그

**우리 환경 판정 열**: ✅ 활용 가능 · ⛔ 반드시 비활성(D-003 읽기 전용 · 자동 등록 금지 · 폐쇄망) · ➖ 무관 · 💡 아이디어 차용 후보

### 2.1 에이전트 코어

| 기능 | 내용 | 위치 | 판정 |
|---|---|---|---|
| 대화 루프 `AIAgent` | 프로바이더 선택·프롬프트 조립·도구 실행·재시도·폴백·콜백·압축·영속화를 한 클래스가 담당. **동기 API만**(`run_conversation`·`chat`) | `run_agent.py` · `agent/conversation_loop.py` · `agent/turn_facade.py:22-31,202-204` | ✅(어댑터 필요) |
| 프로바이더 | model-provider 플러그인 **39종**(openai·anthropic·gemini·vertex·bedrock·azure·openrouter·nous·deepseek·qwen·ollama-cloud·**custom** 등). 전송 3종: `chat_completions` · `anthropic_messages` · `codex_responses` | `plugins/model-providers/` · `agent/transports/` | ✅ custom(vLLM·MLX) · Gemini 네이티브 |
| Gemini 네이티브 | `functionDeclarations`·`toolConfig`·`functionCall` id 왕복 지원 | `agent/gemini_native_adapter.py:223-233,339-366` | ✅(과금 — D-127) |
| 요청 오버라이드 | `request_overrides={"extra_body": …}` 병합(custom 프로필 경로) | `agent/transports/chat_completions.py:484-487` | ✅ `chat_template_kwargs` 대응 |
| 컨텍스트 길이 탐지 | config → 캐시 → 엔드포인트 `/models` → models.dev → OpenRouter 순. **64K 미만이면 생성자에서 `ValueError`** | `agent/model_metadata.py:318,1931-2025` · `agent/agent_init.py:1884-1906` | ⚠ config 고정 필수 |
| 컨텍스트 압축 | 임계 초과 시 중간 턴 요약 | `agent/context_compressor.py` | 💡 |
| 프롬프트 캐싱 | Anthropic 캐시 브레이크포인트 | `agent/prompt_caching.py` | ➖ |
| 빈 응답 가드 | 기본 3회 재시도 → 폴백 체인 → `turn_exit_reason="empty_response_exhausted"` | `agent/empty_response_guard.py:54` · `agent/turn_empty_response.py:110-124` | 💡(D-092·D-093과 같은 문제) |
| 반복 예산 | `max_iterations`(기본 **무제한** `sys.maxsize` — 문서의 500은 틀림) · 부모·자식 공유 `IterationBudget` · 소진 시 도구 없이 요약 1회 | `run_agent.py:237` · `agent/iteration_budget.py:25-56` · `agent/turn_finalizer.py:125-150` | ✅(명시 필수) |
| 시간 예산 | `run_budget_seconds` — **강제 중단 아님**, 80% 경과 시 안내 1회 주입 | `agent/conversation_loop.py:119-151` | ⚠ |
| 중단·조향 | `interrupt(message, hard_cancel=…)` 다른 스레드에서 호출 · API 서버 `steer` | `agent/interrupt_control.py:93-99` | ✅ 클라이언트 끊김 대응 |
| 도구 지연 노출 `tool_search` | 기본 `auto` — 코어가 아닌 도구를 `tool_search`/`tool_describe`/`tool_call` 뒤로 숨겨 프롬프트 토큰 절약 | `tools/tool_search.py` · `hermes_cli/config_defaults.py:1830` | ⚠ 우리 도구 7개면 off(§4.2 실측 호출 2→4회) · 💡 도구가 많아질 때 |
| 콜백 | `tool_start/complete`·`stream_delta`·`thinking`·`step`·`status`·`clarify` 등 20여 종. **루프 밖 스레드에서 호출**, 예외는 삼킴 | `run_agent.py`(생성자) · `agent/tool_executor.py:899-955` | ✅(큐 브리지 필요) |
| 폴백·자격 풀 | `fallback_model` · `credential_pool` · 보조 모델 폴백 체인(openrouter→nous…) | `agent/auxiliary_client.py:2930-2933` | ⛔(과금 폴백 위험) |

### 2.2 도구 · toolset

| 기능 | 내용 | 위치 | 판정 |
|---|---|---|---|
| 도구 레지스트리 | **프로세스 전역 싱글턴**. `register(name, toolset, schema, handler, …, is_async, override, scope)` · 같은 toolset 재등록은 **조용히 덮어씀** · `scope`는 프로필(HERMES_HOME) 단위 | `tools/registry.py:601-665,944` | ⚠ §6 R-2 |
| toolset **60개** | file · terminal · code_execution · web · search · browser · computer_use · vision · image_gen · video(_gen) · tts · memory · skills · session_search · todo · clarify · delegation · kanban · cronjob · spotify · homeassistant · discord 등 + 플랫폼 프리셋 `hermes-*` 24종 | `toolsets.py`(실측 `TOOLSETS` 60키) | ⛔ `enabled_toolsets` 화이트리스트 필수 |
| 기본 활성 | `enabled_toolsets=None`이면 **전부 활성**(terminal·write_file·execute_code·delegate·memory·skill_manage 포함) | `model_tools.py:216,324-327` | ⛔ |
| 터미널 백엔드 7종 | local · Docker · SSH · Singularity · Modal · Daytona · Vercel Sandbox | `tools/environments/` | ⛔ |
| 도구 실행 | 도구 1건마다 전용 스레드 · async 핸들러는 **새 이벤트 루프**에서 실행(`_run_async`) · 커스텀 도구는 순차 | `model_tools.py:81-128` · `agent/tool_executor.py:856-857` · `agent/tool_dispatch_helpers.py:31-45` | ⚠ §6 R-3 |
| 결과 상한 | 결과 1건 100K자 · 턴 합계 200K자 초과 시 `cache/spillover/*.txt`로 파일화 | `tools/budget_config.py:13-14` · `tools/tool_result_storage.py` | ⚠ §6 R-5 |
| lazy install | 기능 사용 시 필요한 패키지를 **실행 중 venv에 설치**(기본 허용) | `tools/lazy_deps.py:324-334,578-607` | ⛔ `HERMES_DISABLE_LAZY_INSTALLS=1` |

### 2.3 학습 루프 (Hermes의 차별점)

| 기능 | 내용 | 위치 | 판정 |
|---|---|---|---|
| 내장 메모리 | `MEMORY.md`·`USER.md`를 시스템 프롬프트에 주입 · 사용자 10턴마다 기록 넛지 · **user_id 구분 없음(HOME 단위)** | `agent/agent_init.py:1233-1265` · `tools/memory_tool_store.py` | ⛔ 자동 등록 금지 원칙 |
| 외부 메모리 provider | holographic(SQLite FTS5) · honcho · mem0 · supermemory · hindsight · byterover · openviking · retaindb | `plugins/memory/` | ⛔ |
| 스킬 자가 생성·개선 | `skill_manage` — 반복 작업을 스킬로 저장·수정 · 도구 10회 반복마다 넛지 · 번들 스킬 14 · 선택 스킬 24 · Skills Hub(agentskills.io·GitHub 등) | `tools/skill_manager_tool.py` · `tools/skills_hub*.py` · `skills/` · `optional-skills/` | ⛔ |
| 백그라운드 리뷰 | 턴 종료 후 포크 에이전트가 대화를 돌아보고 메모리·스킬 갱신(기본 on) | `agent/background_review.py` · `agent/turn_finalizer.py:613-623` | ⛔ `skip_background_review=True` |
| 큐레이터 | 스킬·메모리 정리(CLI·gateway 경로에서만 호출) | `agent/curator.py` | ⛔ |
| 세션 검색 | 과거 대화 FTS5 검색 + LLM 요약 | `tools/session_search_tool.py` · `hermes_state*.py` | ⛔(다중 사용자 격리 없음) |
| 자기 진화(별도 저장소) | DSPy + GEPA로 스킬·프롬프트·코드 최적화 | `NousResearch/hermes-agent-self-evolution` | ➖ |

### 2.4 멀티에이전트 · 작업 관리

| 기능 | 내용 | 위치 | 판정 |
|---|---|---|---|
| 서브에이전트 `delegate_task` | 부모 toolset 교집합 상속(delegate·clarify·memory·send_message·cronjob 차단) · 병렬 기본 10 · 깊이 기본 1 | `tools/delegate_tool*.py` · `tools/delegate_tool_toolsets.py:14-22,71-115` | 💡(1단 `task` 대응) |
| todo | `todo_list` — `{id, content, status}`, status = pending/in_progress/completed/cancelled. **반환 dict에 없음**(private `agent._todo_store`) | `tools/todo_tool.py:9,78-79` · `agent/agent_init.py:1156` | ⚠ |
| Kanban | 영속 멀티에이전트 작업 보드(v0.13 "Tenacity Release") | `tools/kanban_tools.py` · `plugins/kanban/` | ➖ |
| `/goal` | 목표 상태를 세션에 고정해 에이전트 초점 유지·재개 | `hermes_cli/goals.py` | ➖ |
| 체크포인트 | 작업 파일 스냅숏·되감기(기본 off) | `tools/checkpoint_manager.py` | ➖ |
| Cron | 예약 작업·감시(no_agent watchdog)·결과 배달 | `cron/` · `tools/cronjob_tools.py` | ➖ |
| 배치·궤적 | `batch_runner.py` 병렬 실행 · trajectories JSONL · `trajectory_compressor.py`(RL 데이터) | 루트 | 💡 평가 하네스 참고 |

### 2.5 통합 표면

| 기능 | 내용 | 위치 | 판정 |
|---|---|---|---|
| CLI / TUI | 멀티라인 편집·슬래시 명령·`/model` 전환 | `hermes_cli/` · `ui-tui/` | ➖ |
| 메시징 게이트웨이 | Telegram · Discord · Slack · WhatsApp · Signal · Matrix · Mattermost · Email · SMS · Teams · Feishu · DingTalk · WeCom · Weixin · QQ · BlueBubbles · Home Assistant · Webhook 등 25+ 어댑터 · 사용자 인가·페어링 | `gateway/` · `toolsets.py`(`hermes-*`) | 💡 (C) 운영자 비서 |
| API 서버(OpenAI 호환) | 포트 8642 · `/v1/chat/completions` · `/v1/responses` · `/v1/models` · **Runs API** `/v1/runs`·`/{id}/events`·`/{id}/approval`·`/{id}/steer`·`/{id}/stop` · `/v1/toolsets`·`/v1/skills`·`/v1/capabilities` · 아티팩트 업·다운로드 | `gateway/platforms/api_server*.py`(실측 경로 추출) | 💡 (B)·(C) 프로세스 경계 |
| MCP 클라이언트 | stdio · HTTP · SSE · OAuth 2.1 · mTLS · 서버별 `tools.include/exclude`(glob) · 도구명 `mcp_<서버>_<도구>` · **sampling 기본 on** · mcp 1.x/2.x 양쪽 지원 | `tools/mcp_tool*.py` | ✅ (C) — sampling은 off |
| MCP 서버 모드 | Hermes 도구를 MCP로 노출(mcp 2.x 전용) | `mcp_serve.py` | ➖ |
| ACP 어댑터 | 에디터(Agent Client Protocol) 연동 | `acp_adapter/` | ➖ |
| Python 라이브러리 | `from run_agent import AIAgent` · 요청마다 인스턴스 생성·공유 금지 | 문서 `website/docs/guides/python-library.md` | ✅ (A)·(B) |
| 데스크톱·웹 | desktop 앱 · 웹 대시보드 | `apps/` · `web/` | ➖ |
| 관측 | langfuse 플러그인 · OTLP extra · 텔레메트리 기본 off | `plugins/observability/langfuse` · `hermes_cli/config_defaults.py:2123-2132` | 💡 |

### 2.6 보안 기능

| 기능 | 내용 | 위치 | 판정 |
|---|---|---|---|
| 위험 명령 승인 | 모드 `manual`/`smart`(보조 LLM이 위험도 판정)/`off` · YOLO · 끌 수 없는 hardline 차단(`rm -rf /`·fork bomb 등) · 사용자 deny glob | `tools/approval*.py` · `tools/approval_context.py:197` | ➖(셸 도구 자체를 끔) |
| 도구 호출 검증 | `valid_tool_names` 밖 호출은 오류 결과, 3회면 중단 · **`handle_function_call` 직접 호출은 검사 없음** | `agent/turn_tool_validation.py:91-136` · `model_tools.py:858-951` | ⚠ 한 겹 |
| 자격 증명 보호 | terminal·execute_code 자식 프로세스 환경변수 제거 · MCP 자식 env 화이트리스트 · `~/.ssh`·`.env` 등 파일 차단 | 문서 security | ➖ |
| SSRF 차단 | URL 도구가 사설망·루프백·메타데이터 주소 차단 | 문서 security | ➖ |
| 프롬프트 인젝션 스캔 | 컨텍스트 파일(AGENTS.md 등) 주입 전 스캔 | 문서 security | 💡 |
| 공급망 | 직접 의존성 exact pin · advisory checker | `pyproject.toml:20-32` · 커밋 `c1eb2dcda7` | 💡 |

---

## 3. 대체 대상 — 현행 deepagents(1단) 통합 표면

| 항목 | 실측 |
|---|---|
| 패키지 import | 코드 전체에서 **한 곳** — `src/orchestration/deep_agent.py:114` `from deepagents import create_deep_agent`(+ `scripts/scenario/preflight.py:367` 설치 점검) |
| 호출 | `create_deep_agent(tools=, model=, system_prompt=)` 인자 3개(`deep_agent.py:132-136`). 서브에이전트·미들웨어·체크포인터·`interrupt_on` 미사용 |
| 도구 | `StructuredTool` 7개(`query_infra_db` · `query_live_processes` · `query_alarm` · `manage_cache` · `register_synonym` · `general_answer` · `inspect_host`), `async (sub_query: str) -> str`, **요청별 클로저**로 사용자 컨텍스트 19키 + collector 캡처(`src/orchestration/deepagents_tools.py:72-81,418-463`) |
| 실행 | 요청마다 조립 → `await agent.ainvoke({"messages":[user]}, {"recursion_limit": N})`(`deep_agent.py:215,230-233`) → 빈 응답이면 최대 3회 재개(D-092·D-093, `todos` 읽음) → FabriX `result_aggregator` 합성 |
| SSE | 바깥 그래프 `astream_events(v2)`가 `on_tool_start`/`on_tool_end`·`on_custom_event`를 받음(`src/api/routes/query.py:188,236-275`) — **도구가 같은 루프·같은 콜백 컨텍스트에서 실행된다는 전제** |
| 프레임워크 무관 자산 | D-095/D-203 게이트 · `SUBAGENT_REGISTRY` 핸들러 · `result_aggregator` · 감사 로그 · 바깥 체크포인터 — **재사용 가능** |
| 현행 공백 | 1단에는 HITL 없음 · 오케스트레이터 대화 이력 없음(교체 동등성 기준도 "없음") |
| 위상 | 사용자 기준 2026-09-17: 기본 경로 = 3단 `semantic_router`, 1단은 **부가 경로**(D-225 예약) · 1단 기능의 3단 이식 = `plans/103`(D-226 예약) |

---

## 4. 실측 결과

### 4.1 설치 · 의존성

| 시험 | 결과 |
|---|---|
| `hermes-agent`(태그) + `langchain-openai==1.3.2` + deepagents·langgraph·`mcp<2` 해석 | **실패** — `openai==2.24.0` ↔ `openai>=2.26.0` |
| `hermes-agent[mcp]` + `mcp<2` | **실패** — `mcp==2.0.0` 핀 |
| Hermes 단독 venv | 성공 |
| **루트 전체 extra**(`dev,document,deepagents,gemini,semantic,structured,stl`) + `mcp_server` 의존(ibm-db 제외) + Hermes, `--override openai==2.26.0` | **성공** |
| 현 루트 venv 대비 변화 | **하향 6종**: requests 2.34.2→2.33.0 · websockets 16.1.1→15.0.1 · python-dotenv 1.2.3→1.2.2 · packaging 26.3→26.0 · cryptography 50.0.1→50.0.0 · certifi 2026.7.22→2026.5.20. **신규 20종**(fire · ruamel.yaml · prompt_toolkit · croniter · psutil · Pillow · pillow-heif · **nemo-relay**(네이티브) 등) |
| 비 editable 설치 | **차단** — `setup.py`가 `bdist_wheel`·`sdist`를 Nix 밖에서 `RuntimeError` |
| editable 설치(`--no-deps -e`) | 성공 · 최상위 이름 **51개** 매핑(`agent` · `tools` · `utils` · `cli` · `providers` · `plugins` · `cron` · `gateway` · `run_agent` · `model_tools` · `toolsets` · `hermes_state*` 등) |
| 저장소 루트에서 import | `import run_agent` 성공 · 우리 `tools.redis_migration`도 **import 순서 양방향 모두 정상**(PEP 420 가림 우려는 실측상 재현 안 됨) |
| import 소요 | `import run_agent` 약 3.5초 · 도구 모듈 45개 import·등록(비활성 도구 포함) |

### 4.2 스모크 — 로컬 MLX에서 우리 형태의 도구 1개

- **구성**: `AIAgent(provider="custom", base_url=127.0.0.1:8080/v1, model=Qwen3.5-9B-OptiQ-4bit, enabled_toolsets=["collectorinfra"], quiet_mode, skip_memory, skip_context_files, skip_background_review, max_iterations=6)`. 도구는 `register(name="query_infra_db", toolset="collectorinfra", is_async=True)`로 등록한 `async (args, **kw)` 클로저. FastAPI를 흉내 내 `asyncio.run` 루프 안에서 `await asyncio.to_thread(agent.run_conversation, …)`로 호출했다.
- **환경**: `env -i` + 전용 `HERMES_HOME`(scratchpad) — API 키 0개로 과금 폴백 원천 차단.

| 관찰 | 값 |
|---|---|
| 결과 | `final_response` = "김포 웹서버 (gimpo-web-01) 의 CPU 사용률은 87.5% 입니다." — 도구 결과만 사용 |
| 소요 · 호출 | 94초 · API 4회 · 도구 턴 3회 — `tool_search` → `tool_describe` → `query_infra_db`(**tool_search 기본 auto가 도구 1개도 숨김**) |
| 반환 키 | `final_response` · `messages` · `completed` · `failed` · `partial` · `interrupted` · `turn_exit_reason` · `api_calls` · 토큰 8종 · 비용 3종 등 31키 — `todos` 없음 |
| 도구 실행 스레드·루프 | `ThreadPoolExecutor-2_0` · **FastAPI 루프와 다른 이벤트 루프**(`same_loop_as_fastapi=false`) |
| ContextVar | 도구까지 **전파됨**(`request-A`) |
| 핸들러 kwargs | `session_id` · `task_id` · `user_task` — 우리 컨텍스트는 클로저·ContextVar로만 전달 가능 |
| 콜백 스레드 | `tool_start` = 도구 워커 스레드 · `tool_complete` = 턴 스레드 |
| 컨텍스트 길이 | 엔드포인트 탐지 실패 → 카탈로그 'qwen' 매칭 131,072 사용(외부 조회 없음) |
| **실행 중 pip 설치** | `tools.lazy_deps: Lazy-installing boto3==1.42.89 for feature 'provider.bedrock'` — provider가 custom인데도 **venv에 boto3가 설치됨** |
| 디스크 기록 | `state.db` · `SOUL.md` · `cache/schema_columns.json` · `cache/tool_discovery_cache.json` · `logs/agent.log` · `logs/errors.log` + 빈 디렉터리 10개(memories·skills·sessions·cron·hooks 등) |

### 4.3 프로세스 전역 부작용 (실측 — `import run_agent` + `AIAgent()` 1회)

| 대상 | 전 → 후 | 영향 |
|---|---|---|
| root 로거 레벨 | WARNING → **INFO** | 우리 앱 전체 로그량 변화 |
| root 핸들러 | 0 → `_NonFormattingQueueHandler` 추가 | 우리 앱 로그가 `$HERMES_HOME/logs/agent.log`에도 기록 |
| `LogRecordFactory` | **교체** | 우리 로그 포맷·감사 로그 필드에 간섭 가능(추정) |
| `sys.stdout` | `TextIOWrapper` → `_SafeWriter` | uvicorn·CLI 출력 경로 변경 |
| `sys.path` | 맨 앞에 Hermes 루트 삽입 | `utils`·`cli` 등 흔한 최상위 이름 선점 |
| `os.environ` | `HERMES_SESSION_ID` · `TERMINAL_*` 10여 개 주입 | 프로세스 전역 · 동시 에이전트 간 공유(추정: 세션 ID 경합) |

---

## 5. 연동 계약 비교

| 계약 | 현행 deepagents | Hermes 0.21.3 | 필요한 어댑터 |
|---|---|---|---|
| 도구 주입 | 요청마다 클로저 `StructuredTool` 리스트 전달 | 생성자 `tools=` 없음 · 전역 레지스트리 + `enabled_toolsets` 필터 | **A1 도구 바인딩**: 기동 시 1회 등록 + 요청 컨텍스트는 ContextVar(또는 `task_id`→컨텍스트 맵)로 조회 |
| 실행 | `await ainvoke` · 같은 이벤트 루프 | 동기 `run_conversation` · 도구는 별도 스레드·새 루프 | **A2 루프 브리지**: 턴은 `asyncio.to_thread` · 도구는 `run_coroutine_threadsafe(tool.ainvoke(…, parent_config), main_loop)`로 메인 루프에 되돌림(`is_async` 미사용) |
| LLM | LangChain ChatModel 그대로 | 자체 provider · `extra_body`는 `request_overrides` · SSL verify off는 **config.yaml `custom_providers[].ssl_verify`만** | **A5 설정 격리**: 전용 `HERMES_HOME`/config.yaml 생성 |
| 결과 | `{"messages", "todos"}` | `final_response`·`turn_exit_reason`·`interrupted` 등 · todos는 private | **A4 결과 변환**: 재개 판정을 `turn_exit_reason`으로 · todos는 `agent._todo_store.read()`(private 의존) |
| 순회 상한 | `recursion_limit` 설정값 | `max_iterations` 기본 무제한 · `run_budget_seconds`는 안내만 | A4에서 명시 매핑 · 전체 타임아웃은 `interrupt(hard_cancel=True)` 감시자 |
| 시스템 프롬프트 | `system_prompt=ORCHESTRATOR_INSTRUCTIONS` | `ephemeral_system_prompt`는 **기본 프롬프트 뒤에 덧붙음**(identity·작업 완료·병렬 호출·qwen 실행 안내 등 약 1.1K~2K 토큰) | 프롬프트 회귀 재측정(`prompt_render_diff` 대상 밖) |
| SSE | 바깥 `astream_events`가 자동 수신 | 콜백이 루프 밖 스레드 | **A3 이벤트 브리지**: `call_soon_threadsafe` → asyncio 큐 → SSE, 또는 A2의 부모 config 전달로 기존 이벤트 유지 |
| 컨텍스트 창 | 제약 없음 | **64K 미만이면 생성자 예외** | vLLM `max_model_len`·MLX 설정 ≥ 64K 또는 config로 실제 값 명시 |
| HITL | 없음(현행 공백) | `clarify` · API 서버 Runs `approval` | 동등성 기준 "없음" |

---

## 6. 위험 목록

| ID | 위험 | 심각도 | 끌 수 있나 | 완화 |
|---|---|---|---|---|
| R-1 | **서버 프로세스 전역 오염**(§4.3) | 높음 | **아니오** | 별도 프로세스로만 격리 가능 → (B)·(C) |
| R-2 | **동시 요청 권한 누수** — 요청마다 같은 이름으로 클로저를 재등록하면 조용히 덮어써 A 요청의 호출이 B의 `allowed_db_ids`로 실행 | **치명** | 설계로 회피 | A1(1회 등록 + ContextVar) + 동시성 격리 테스트 필수 |
| R-3 | 도구 코루틴이 다른 이벤트 루프에서 실행 → 메인 루프에 묶인 자원(비동기 클라이언트·LangChain 콜백 큐) 오동작 | 높음 | 설계로 회피 | A2 · 현 DB 클라이언트는 호출마다 생성(`src/db/__init__.py:20`)이라 영향 작으나 전수 확인 필요 |
| R-4 | 기본값이 우리 원칙과 반대 — 전 toolset 활성 · 메모리·백그라운드 리뷰 on · lazy install on · tool_search auto | 높음 | 예(§7) | 잠금 설정 + 부수효과 화이트리스트 테스트 |
| R-5 | **끌 수 없는 평문 디스크 기록** — 비재시도 4xx·재시도 소진 시 요청 본문 전체(DB 조회 결과 포함)를 `sessions/request_dump_*.json`에 기록(시크릿 패턴만 마스킹) · 대용량 결과 spillover 파일 | 높음 | **아니오**(스위치 없음 — 코드 `agent/turn_recovery.py:685,861`) | `data_masker`·PII 정책 밖 → HERMES_HOME 수명 관리·정리 작업 또는 패치 유지 |
| R-6 | 네이티브 `nemo-relay`가 코어 의존성으로 설치되고 LLM 요청 경로를 경유 | 중간 | 추정: 초기화 실패 시 Noop(`agent/relay_runtime.py:775-778`) | 소스로 검증 불가 → 제외 설치 가능 여부 확인 |
| R-7 | API 안정성 보증 없음 · 3일 1,680커밋 · 문서와 소스 불일치 2건(`max_iterations` 기본값 · `ephemeral_system_prompt` 동작) | 중간 | — | 태그 고정 + 계약 테스트 + 갱신 주기 정책(G-5) |
| R-8 | 64K 하한 강제 · 로컬 9B·vLLM 설정에 따라 기동 실패 | 중간 | 설정 | 컨텍스트 값 명시 |
| R-9 | 다중 사용자 격리 없음 — 메모리·세션·로그·레지스트리가 HOME·프로세스 단위 | 높음((A)(B)) | 기능 off로 완화 | (C)는 운영자 단일 사용자 전제 |
| R-10 | 의존성 하향 6종 · 신규 20종 · 루트 venv 공유 시 다른 패키지 영향 | 중간((A)) | — | (B)는 같은 venv라도 프로세스 분리 · (C)는 별도 venv |
| R-11 | Hermes 가치의 상실 — 학습 루프·메모리·스킬·게이트웨이를 끄면 남는 것이 deepagents와 같은 도구 루프 | 판단 | — | §8 비교 |
| R-12 | HOME 오염 시 임의 코드 실행 — user model-provider 플러그인은 `HERMES_SAFE_MODE`와 무관하게 `$HERMES_HOME/plugins/model-providers`에서 로드 | 중간 | 권한 | HERMES_HOME 읽기 전용 소유권 · 무결성 점검 |

---

## 7. 잠금 설정 목록 (도입 시 필수)

### 7.1 환경변수 — **import 전에** 설정

| 변수 | 값 | 이유 |
|---|---|---|
| `HERMES_HOME` | 전용 빈 디렉터리 | 로그 경로가 import 시점에 고정 · `~/.hermes` 오염 방지 |
| `HERMES_DISABLE_LAZY_INSTALLS` | `1` | 실행 중 pip 설치 차단(§4.2 실측) |
| `HERMES_SAFE_MODE` | `1` | 플러그인 탐색 생략(`hermes_cli/plugins.py:1221-1222`) — 단 `$HERMES_HOME/plugins/model-providers`는 별도 로드(R-12) |
| 설정 금지 | `HERMES_LAZY_INSTALL_TARGET` · `HERMES_DUMP_REQUESTS` · `HERMES_KANBAN_TASK`(설정 시 kanban 자동 추가) · `HERMES_NEMO_RELAY_PLUGINS_TOML` · 각종 provider API 키 | 우회·과금 폴백 |

### 7.2 `AIAgent` 인자

| 인자 | 값 |
|---|---|
| `enabled_toolsets` | 우리 toolset만(오타 시 quiet 모드에서 **조용히 도구 0개** — 기동 시 노출 도구 단언 필요) |
| `max_iterations` | 유한값(현 `recursion_limit` 대응) |
| `skip_memory` · `skip_context_files` · `skip_background_review` · `quiet_mode` | `True` |
| `load_soul_identity` · `save_trajectories` · `checkpoints_enabled` | `False` |
| `session_db` | `None` |
| `provider` · `base_url` · `api_key` | 내부 엔드포인트만 · `fallback_model`·`credential_pool` 미지정 |

### 7.3 `$HERMES_HOME/config.yaml`

`memory.memory_enabled: false` · `memory.user_profile_enabled: false` · `memory.provider: ""` · `auxiliary.background_review.enabled: false` · `curator.enabled: false` ·
`agent.environment_probe: false` · `tools.tool_search.enabled: "off"` · `security.allow_lazy_installs: false` · `updates.check: false` · `plugins.enabled: []` · `secrets` 미설정 ·
`custom_providers[]`에 base_url·`ssl_verify`·`models.<모델>.context_length` 명시(탐지·models.dev 조회 차단) · MCP 사용 시 서버별 `sampling` off·`tools.include` 화이트리스트

> 키 이름은 소스 정독 기준이다(코드 — `hermes_cli/config_defaults.py`). 적용 효과는 PoC P3에서 실측으로 확정한다.

---

## 8. 적용 형태 대안

### 8.1 대안 비교

| 안 | 형태 | 장점 | 비용·위험 | 판정 |
|---|---|---|---|---|
| **A** | 1단 **in-process 교체** — FastAPI 프로세스 안에서 `AIAgent` 호출 | 배선 변경 최소 | R-1(끌 수 없음) · R-2·R-3 · 루트 venv 하향 6종 · 어댑터 A1~A5 | **비권장** |
| **B** | 1단을 **별도 워커 프로세스**로 — 본체가 요청·컨텍스트를 워커에 전달, 워커가 `AIAgent` + 우리 핸들러(`src` 임포트) 실행, 이벤트를 스트림으로 회신. 또는 Hermes API 서버 Runs API(`/v1/runs/{id}/events`) 사용 | R-1 격리 · 본체 로깅·stdout 보호 | 프로세스 간 컨텍스트·이벤트 계약 신설 · 워커 안에서도 R-2·R-3·R-5 · D-037/D-174 개정 · 1단은 이미 부가 경로 | 조건부(G-1) |
| **C** | **운영자 비서형 독립 프로세스** — 자체 venv·HERMES_HOME · MCP로 `mcp_server`(읽기 전용)만 연결 · 필요 시 사내 메신저 게이트웨이 | 전역 부작용 완전 격리(D-139 경계 — MCP 계약만) · 운영자 단일 사용자면 메모리·스킬 일부 허용 여지 · Hermes 고유 가치(게이트웨이·학습)를 살릴 수 있는 유일한 형태 | HolmesGPT(`sre_agent`, D-197)와 역할 중복 · 학습 루프 오염 통제 정책 필요 · Hermes MCP 클라이언트 ↔ `mcp_server`(FastMCP 1.x) 실연결 미검증 | 수요 확인 시 별도 계획 |
| **D** | **미도입** — `plans/103`(3단 LangGraph 동등성)으로 진행하고 Hermes는 설계 아이디어만 차용 | 기준 경로(3단) 방향과 일치 · 추가 의존·추종 비용 0 · 기존 SSE·체크포인터·감사 그대로 | Hermes 기능(게이트웨이·학습)은 얻지 못함 | **권고** |

### 8.2 판단 근거

1. **얻는 것 대비 비용.** 우리 원칙(D-003 읽기 전용 · 자동 등록 금지 · 다중 사용자 · 폐쇄망)을 지키려면 §2.3 학습 루프와 §2.5 게이트웨이를 전부 꺼야 한다. 남는 것은 도구 루프·빈 응답 가드·압축·중단이며, 현행 deepagents 의존은 `create_deep_agent` 인자 3개 한 곳이다.
2. **경로 위상.** 1단은 2026-09-17 사용자 기준으로 부가 경로이고, 1단 기능은 `plans/103`이 3단 워크플로로 옮긴다. 1단 프레임워크 교체는 그 방향과 투자 대상이 겹친다.
3. **격리 가능성.** R-1은 설정으로 끌 수 없어 in-process(A)는 구조적으로 막힌다. 프로세스 분리(B·C)만 남는다.

### 8.3 아이디어 차용 후보 (D 채택 시 `plans/103` 참고 입력)

| Hermes 기능 | 우리 대응처 | 차용 내용 |
|---|---|---|
| 빈 응답 가드(§2.1) | D-092·D-093 재개 로직 · `plans/103` 재계획 루프 | 재시도 횟수·폴백·종료 사유(`turn_exit_reason`) 구조화 |
| 반복 예산 공유(`IterationBudget`) | `plans/103` 재계획 상한 | 부모·자식이 공유하는 스레드 안전 예산 · 소진 시 도구 없이 요약 1회 |
| `tool_search` 지연 노출 | 도구·에이전트 수 증가 시 라우터 프롬프트 | 임계(컨텍스트 %) 기반 지연 노출 — 도구가 적을 땐 오히려 손해(§4.2) |
| 서브에이전트 toolset 교집합 상속 | 태스크 서브그래프 권한 | 자식은 부모 권한의 부분집합만 · 위험 도구 차단 목록 |
| 종료 사유 구조화 반환 | SSE·감사 로그 | `completed`/`partial`/`interrupted`/`failed` + 사유 문자열 |
| 공급망 exact pin 정책 | 폐쇄망 wheel 반입 | 직접 의존성 정확 핀 + 갱신 시 lock 재생성 원칙 |
| request_dump(반면교사) | PII·감사 정책 | 오류 덤프에 조회 결과가 평문으로 남는 설계를 피함 — `logs/pii_block/` 정책과 대조 |

---

## 9. 결정 문서와의 관계

| 결정 | 관계 | (A)/(B) 채택 시 | (C) 채택 시 |
|---|---|---|---|
| D-037 트랙 B | "실제 deepagents 패키지" 경로 | **개정 필요** | 무관 |
| D-174 | vLLM = deepagents 전용 제어 평면 | **개정 필요**(소비처 변경) | 오케스트레이터 평면 공유 여부 결정 |
| D-181 | `mcp<2` | Hermes 코어는 mcp 불요 — 충돌 없음 | 별도 venv라 무관(클라이언트는 1.x/2.x 지원) |
| D-003 · 자동 등록 금지 원칙 | 읽기 전용 · LLM 자동 등록 오염 | §7 잠금 필수 | 학습 루프 허용 범위를 새로 결정 |
| D-139 | 패키지 경계 | (B) 워커 엔트리 위치 결정 | 소유 패키지 검토 먼저(`sre_agent` 편입 vs 신설 — `plans/101` 선례) |
| D-161 | 경로 폐기 실측 4항 | deepagents 제거 시 적용 | 무관 |
| D-197 | HolmesGPT RCA(`sre_agent`) | 무관 | **역할 중복 정리 필요** |
| D-225 · D-226(예약) | 3단 기준 · 3단 동등성 | 1단 투자와 방향 충돌 — 사용자 확정 필요 | 무관 |

충돌 항목은 CLAUDE.md 규칙에 따라 **임의로 진행하지 않고 G-1~G-2로 사용자 결정을 받는다.**

---

## 10. 조건부 PoC 계획 — G-1에서 (B)가 선택될 때만

> (D) 선택 시 이 절은 실행하지 않는다. (C) 선택 시 별도 계획서로 분리한다.
> LLM은 로컬 MLX(비과금 · 두 평면 루프백 확인 후)로만 수행한다. Gemini 등 과금 평면은 D-127에 따라 건별 승인.

| Phase | 작업 | verify(완료 판정) |
|---|---|---|
| **P0 반입·고정** | Hermes 태그 고정 소스 반입 위치 결정(G-4) · 워커 전용 설치(`--override openai==2.26.0`, `--no-deps -e`) · `HERMES_DISABLE_LAZY_INSTALLS=1` | 폐쇄망 모의(네트워크 차단)에서 import·`AIAgent()` 생성 성공 · 설치 후 venv 패키지 목록 변화 0(lazy install 없음) |
| **P1 도구 바인딩(A1)** | 7개 도구를 기동 시 1회 등록 · 요청 컨텍스트 ContextVar 조회 · 등록 후 노출 도구 단언 | **동시성 격리 테스트**: 서로 다른 `allowed_db_ids`의 요청 2건 동시 실행 × 50회에서 교차 사용 0건 |
| **P2 루프·이벤트 브리지(A2·A3)** | 도구를 본체 핸들러 루프로 실행 · tool_start/complete·진행 이벤트를 스트림으로 회신 | `tests/test_api/test_stream_nested_events.py` 등가 시나리오에서 이벤트 순서·필드 동등 |
| **P3 잠금·부수효과(A5)** | §7 전 항목 적용 · 본체 프로세스와 로깅·stdout·환경변수 분리 확인 | 1회 실행 후 `HERMES_HOME` 파일이 화이트리스트 내 · 본체 프로세스 root 로거·`sys.stdout`·`os.environ` 불변 · 외부 egress 0(차단 환경 로그) |
| **P4 결과·재개(A4)** | `turn_exit_reason` 기반 재개 · todos 대응 · 전체 타임아웃 감시자 | D-092·D-093 재개 테스트 등가 통과 |
| **P5 비교 평가** | `plans/94` 시나리오 하네스 + `testdata/routing_gold`로 deepagents 1단 vs Hermes 워커 비교(MLX) | 정확도 ≥ deepagents · p50 지연 ≤ 1.2배 · API 호출 수 · 실패 유형 분류 보고 |

**중단 조건**: P1 격리 테스트 실패, P3에서 R-5 기록을 운영 정책으로 수용할 수 없음, P5 정확도 미달 — 이 중 하나면 (D)로 복귀한다.

---

## 11. 사용자 확정 게이트

| ID | 질문 | 권고 |
|---|---|---|
| **G-1** | 적용 형태: (A) in-process 교체 / (B) 별도 워커 1단 / (C) 운영자 비서 독립 프로세스 / (D) 미도입 + `plans/103` 유지 + 아이디어 차용 | **(D)** · 운영자 비서 수요가 있으면 (C) 별도 계획 |
| **G-2** | (A)/(B)/(C) 채택 시 D-037·D-174 개정 · (C)는 D-197과 역할 분담 · 신규 D-번호 채번 승인 | 선택된 안에 따름 |
| **G-3** | R-5(오류 시 요청 본문 평문 덤프 · spillover)를 운영 정책상 수용할 수 있는가 — 불가면 소스 패치 유지 부담 발생 | 수용 불가 전제로 판단 |
| **G-4** | 반입 방식 · 위치: 태그 고정 소스(서브트리/벤더링/사내 미러) · 갱신 주기 | (B)/(C) 채택 시 결정 |
| **G-5** | Hermes 추종 정책: 태그 고정 기간 · 계약 테스트 범위 · 문서-소스 불일치 대응 | (B)/(C) 채택 시 결정 |

---

## 12. 미확인 · 후속 확인 사항

| ID | 항목 | 확인 방법 |
|---|---|---|
| U-1 | Hermes MCP 클라이언트 ↔ `mcp_server`(FastMCP 1.x, SSE) 실연결 | (C) 착수 시 임시 포트에서 연결·도구 목록·호출 1건 |
| U-2 | 다도구(7개) 조건에서 Qwen3.5-9B 품질 — 스모크는 도구 1개 | P5 |
| U-3 | Gemini 네이티브 경로 tool calling 실동작 | 과금 — D-127 건별 승인 후 |
| U-4 | vLLM custom provider tool calling — 과거 `tool_choice` 누락 버그(issue #39099, API 서버 경로, PR #39209로 종결) 재발 여부 | (B)/(C) 착수 시 vLLM 모의 |
| U-5 | `nemo-relay` 제외 설치 가능 여부와 LLM 트래픽 경유 동작 | 워커 venv에서 제거 후 import·실행 |
| U-6 | §7.3 config 키의 실효 — 소스 정독 기준이라 실측 필요 | P3 |
| U-7 | `os.environ`의 `HERMES_SESSION_ID`가 동시 에이전트 간 경합하는지 | P1 동시성 테스트에 포함 |

**부수 발견**: `mcp_server/pyproject.toml:29`의 `build-backend = "setuptools.backends._legacy:_Backend"`는 `uv pip compile`의 격리 빌드에서 실패한다(이번 해석 실측에서는 의존성을 직접 나열해 우회). `mcp_server`는 설치하지 않고 cwd에서 실행하는 구조라 현 운영 영향은 없다(추정).

---

## 부록 A. 재현 명령 (scratchpad 격리 · 루트 venv 무변경)

```bash
S=<세션 scratchpad>
git clone https://github.com/NousResearch/hermes-agent.git $S/hermes-src
git -C $S/hermes-src worktree add ../hermes-tag v2026.9.14

# 해석: 루트 전체 extra + mcp_server 의존(ibm-db 제외) + Hermes, openai override
printf 'openai==2.26.0\nmcp==1.29.1\n' > overrides.txt
uv pip compile full.in --python-version 3.12 --override overrides.txt -o full.txt

# 설치: 의존성 먼저(override) → Hermes는 --no-deps editable (wheel 빌드는 setup.py가 차단)
uv venv --python 3.12 venv
uv pip install --python venv/bin/python -r cohab_nohermes.txt --override overrides.txt
uv pip install --python venv/bin/python --no-deps -e $S/hermes-tag

# 스모크: API 키 없는 환경 + 전용 HERMES_HOME + 로컬 MLX
env -i HOME=$S/fakehome HERMES_HOME=$S/hhome PATH=/usr/bin:/bin venv/bin/python hermes_smoke.py
```

## 부록 B. 출처

- 소스: [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) 태그 `v2026.9.14`(0.21.3)
- 문서: [Hermes Agent Documentation](https://hermes-agent.nousresearch.com/docs/) · [Architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture) · [Python Library](https://hermes-agent.nousresearch.com/docs/guides/python-library) · [Security](https://hermes-agent.nousresearch.com/docs/user-guide/security) · [MCP](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp) · [Providers](https://hermes-agent.nousresearch.com/docs/integrations/providers) · [API Server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server)
- 릴리스: [Releases](https://github.com/NousResearch/hermes-agent/releases) · 이슈: [#39099](https://github.com/NousResearch/hermes-agent/issues/39099)
- 관련 저장소: [hermes-agent-self-evolution](https://github.com/NousResearch/hermes-agent-self-evolution)
