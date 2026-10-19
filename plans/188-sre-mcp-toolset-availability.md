# 188. SRE 조사 — 폴스타 MCP 도구 미가용 시 오동작 교정

> **작성일**: 2026-10-19 · **v1.0**
> **재부여**: 원 `plans/187` · D-340 · D-341 → 2026-10-19 main 병합 시 `plans/188` · D-342 · D-343(원격 main이 `plans/187` 알람 조사 플레이북 · D-340 워커 vllm · D-341을 먼저 푸시 — 미푸시 쪽을 옮김)
> **상태**: **완료 · 커밋 전**(2026-10-19) — W1(C4·C4′·C5)·W2(C3)·W3(C1·C2·C6)·W4(C7)·W5(C8) 구현·검증 · Q1 = **A 확정 → D-342**(W4는 D-342 부기) · W5 = **D-343** · 잔여: W0 폐쇄망 운영자 연결 확인 · W5 다음 턴 왕복 운영 확인(첫 실조사)
> **회귀 대상 파일**: `sre_agent/sre_agent/{infrastructure/mcp_tool_client.py, application/evidence_prefetch.py, application/investigation_dispatcher.py, application/investigation_guidance.py, application/briefing_builder.py, diagnosis.py, interface/mcp_service.py, domain/answer_markup.py, infrastructure/tool_call_markup_guard.py, settings.py}` · `sre_agent/tests/{test_mcp_tool_client, test_evidence_prefetch, test_investigation_dispatcher, test_investigation_guidance, test_apm_consumer, test_mcp_service, test_plan188_toolset_availability, test_plan188_verify, test_plan188_w4_markup, test_plan188_w4_verify, test_plan188_w5_tool_call_parse, test_plan188_w5_verify}.py` · `scripts/manual/content/admin.md` · `src/static/manual/{admin.html,user.html,help_index.json}` · `docs/26_sre_agent_guide.md` · `sre_agent/.env.example`
> **검증**: (W4 포함 계획 끝 1회) `regress.py --base 8616d0d7 --files <22개>` rc=0 — sre_agent 통과 1,276·실패 0·건너뜀 3 · 본체 통과 420·실패 0 · arch/overfit/config_fallback 통과 · ruff diff 줄 0 · 범위: 모듈 단위 — 전체 미실행 · 교정 뒤 선택 재실행 134 passed · verifier Critical 0(W3: Major 1·Minor 3 · W4: Major 1·Minor 4 교정·기록 완료) · **W5 계획 끝 1회** `regress.py --base 8616d0d7 --files <26개>` rc=0 — sre_agent 통과 1,356·실패 0·건너뜀 3 · 본체 통과 420·실패 0 · 범위: 모듈 단위 — 전체 미실행 · 교정 뒤 교정 파일 선택 재실행 rc=0(sre_agent 1,440·본체 144·실패 0) · verifier Critical 0(Major 1·Minor 3 교정)
> **계획 시작 SHA**: `8616d0d7`(RCA)
> **발단**: 폐쇄망 조사 브리핑(2026-10-19 10:05 · `com0-pc-csssv02` · 게이트 page) — 요약·타임라인·원인·원인 가설이 전부 도구 오류 문구로 채워짐
> **성격**: 행동 변경(D-337 ⑥). 항목마다 바뀐 동작을 단언하는 테스트를 더한다.
> **관련 결정**: D-119(관측 접근 경계 = mcp_server 단일) · D-197(사전수집·결정적 상관) · D-123(잡 상태 어휘 불변) · D-161(옵트인 경로) · D-139(패키지 경계 — `sre_agent`↔`noise_gate` import 0)
> **회귀**: `sre_agent`는 자체 venv — `cd sre_agent && .venv/bin/python -m pytest <파일>`

---

## 0. 증상 → 원인

로컬에서 **닫힌 포트(127.0.0.1:9199)로 두 오류 문구를 글자 그대로 재현했다**(2026-10-19 · holmesgpt 0.36.0 · mcp 1.25.0).

| 브리핑 증상 | 원인 | 근거 |
|---|---|---|
| 한계 「알람/cpu/memory/filesystem/disk_io/변경 축 결손: MCP 세션 실패: unhandled errors in a TaskGroup (1 sub-exception)」 | **R0** 사전수집이 `POLESTAR_MCP_URL`에 TCP 연결 실패 · **C4** 예외 그룹을 풀지 않아 사유가 가려짐 | `infrastructure/mcp_tool_client.py:63-66` |
| 가설 본문 「Failed to load mcp server polestar: All connection attempts failed」 | **R0** holmes `RemoteMCPToolset` 선행검사 실패 → 툴셋 FAILED · 도구 0 | holmes `toolset_mcp.py:990-996`(httpx `ConnectError`) |
| 타임라인 「`prom_metric_range` (Failed to find tool)」 등 3건 | **C2** 조사 지침이 툴셋 가용 여부와 무관하게 도구 이름을 박아 넣음 → LLM이 없는 도구를 호출 | `application/investigation_guidance.py:39-44,74` → holmes `tool_calling_llm.py:770` |
| 원인·원인 가설(medium)이 오류 문구 | **C3** 실패한 도구 호출도 `tool_names`에 들어가 오류 라인이 「인용된 근거」로 판정 | `application/investigation_dispatcher.py:492` · `application/briefing_builder.py:248-259` |
| 한계 「사건 구간 내 알람 0건 — 알람 축 선후 판정 불가」 | **C5** 알람 축 **조회 실패**를 **0건**으로 취급 | `application/evidence_prefetch.py:241-244` → `domain/correlation.py:269` |
| (잠재) mcp_server가 살아나도 계속 실패 | **C1** `DiagnosisAgent`·`ToolCallingLLM`을 프로세스 수명 동안 캐시 — FAILED 툴셋을 다시 검사하지 않음. holmes 문구 「Holmes will retry automatically」는 holmes **서버 모드**(`config.py:494` refresh) 얘기라 SDK 경로엔 해당 없음 | `interface/mcp_service.py:194-201` · `diagnosis.py:333-347` |
| 요약·타임라인·원인에 `<\|tool_call>call:TodoWrite{…}<tool_call\|><\|tool_call>call:polestar_incident_alarms{…}<tool_call\|>` 원문(2026-10-19 10:48 폐쇄망) | **C7** 사내 vLLM `--tool-call-parser`가 모델(Gemma 4 계열 네이티브 표기로 보임)과 불일치 → `tool_calls`가 비고 원문이 `content`로 → holmes가 최종 답으로 받음 → 브리핑에 날것으로. 거르는 코드 0건 | `docs/26` §5.6.3 판정표 「본문에 도구 JSON 조각 → 파서 불일치」 · `grep 'tool_call>' sre_agent/sre_agent` 0건 |
| C7을 막아도 조사 자체가 진행되지 않음 — 운영 측은 vLLM 설정을 바꾸기 어렵다(사용자 2026-10-19) | **C8** 모델은 holmes 내장 `TodoWrite`를 정확한 인자 구조로 부름 = vLLM은 tools를 렌더링하고 **응답 파싱만** 빠졌다 → sre_agent가 클라이언트 측에서 Gemma 표기를 OpenAI `tool_calls`로 바꾸면 조사가 진행된다 | holmes 0.36.0 전 LLM 호출이 `DefaultLLM.completion(stream=False)`를 지남(`tool_calling_llm.py:1163`) · 같은 지점에 D-213 가드 선례(`infrastructure/llm_message_guard.py`) |
| (잠재) 연결 상태를 미리 알 방법 없음 | **C6** `sre_health.polestar_mcp_reachable`이 항상 `None`(미구현 자리) | `interface/mcp_service.py:337-353` |

**R0(운영 원인)은 코드 결함이 아니다** — 10:04 시점 sre_agent 프로세스에서 `POLESTAR_MCP_URL`(기본 `http://localhost:9099/sse`)로 TCP 연결이 안 됐다. 401(토큰 불일치)이면 문구가 `HTTP 401`이므로 인증 문제는 아니다. 후보: ① mcp_server 미기동/중단 ② sre_agent `.env`에 `POLESTAR_MCP_URL` 미기재로 기본값 `localhost`를 썼는데 mcp_server가 다른 호스트 ③ 방화벽·포트 상이. **코드만 고치면 브리핑은 정직해지지만 조사는 여전히 도구가 없다 — W0가 먼저다.**

범위 밖(기록만): 요약이 영어로 나온 것(조사 지침에 응답 언어 지시 없음 — 별건) · holmes 라이브러리 수정.

---

## 1. Wave

### W0 — 폐쇄망 연결 확인 (코드 0 · 운영자)

sre_agent가 도는 서버에서:

```bash
grep -n 'POLESTAR_MCP_URL\|POLESTAR_MCP_TOKEN' sre_agent/.env           # 미기재면 기본값 localhost:9099
curl -sS -m 5 -N -H 'Accept: text/event-stream' "$POLESTAR_MCP_URL" | head -2   # 'event: endpoint'가 나와야 정상
ss -ltnp | grep 9099                                                       # mcp_server 쪽 서버에서
```

수용 기준: curl이 `event: endpoint`를 받는다 → sre_agent **재기동**(C1 때문에 재기동 전에는 회복 안 됨) → 같은 알람 재조사에서 타임라인에 실 도구 출력.

### W1 — 사전수집 진단 문구 (C4 · C5)

| 항목 | 변경 | 수용 기준 |
|---|---|---|
| C4 | `mcp_tool_client.run_tool_batch_async`의 세션 실패 사유를 예외 그룹 말단으로 푼다(`noise_gate/infrastructure/apm_gateway_client.py:166` `_failure_detail`과 같은 꼴 — 경계상 import 금지, 사본) · 401/403은 「POLESTAR_MCP_TOKEN 확인」, `ConnectError`는 「URL 도달 불가」 + URL(토큰 제외) | 닫힌 포트 → `MCP 세션 실패: 연결 불가(ConnectError: All connection attempts failed) — POLESTAR_MCP_URL=http://…/sse` · 401 → 인증 문구 · 그룹 아닌 예외는 종전 문구 |
| C4′ | `prefetch_and_correlate`: 전 호출이 같은 세션 실패면 축별 6줄 대신 노트 1줄 | 한계에 「사전수집 MCP 세션 실패: …」 1줄 · 부분 실패는 종전대로 축별 |
| C5 | 알람 축이 오류면 `correlate(..., alarm_axis=False)`로 넘겨 「알람 0건」 노트를 내지 않음 | 알람 축 오류 → 「알람 0건」 없음 · 정상 0건 → 종전 노트 유지 |

테스트: `tests/test_mcp_tool_client.py`(66행 단언 갱신 + 신규) · `tests/test_evidence_prefetch.py`.

### W2 — 실패 호출을 근거에서 제외 (C3)

| 변경 | 수용 기준 |
|---|---|
| `investigation_dispatcher.py:492`의 `tool_names`를 status `success`·`no_data` 호출로 한정. `error` 호출은 이름을 모아 `limitations`에 「도구 호출 실패 N건: a, b …」 1줄 | 오류 호출만 있는 조사 → 요약·원인 `[가설]` · 타임라인 「근거 없음」 · 원인 가설에 오류 문구 없음 · 성공 호출 인용은 종전과 같음 |

severity_judge 입력(`tool_outputs` 원시)은 바꾸지 않는다 — 판정 경로 불변. 테스트: `tests/test_briefing_builder.py`·dispatcher 테스트에 오류 호출 픽스처.

### W3 — 툴셋 가용성 (C1 · C2 · C6)

| 항목 | 변경 | 수용 기준 |
|---|---|---|
| C1 | `DiagnosisAgent`에 `mcp_unavailable() -> dict[name, error]`(`llm.tool_executor.toolsets` 중 type=mcp·status FAILED). `_default_diagnose_fn`은 조사 직전 이것이 비어 있지 않으면 agent를 **다시 만든다**(1회) | mcp_server 다운 상태로 1건 → 서버 기동 → 다음 건은 재기동 없이 도구 사용 · 정상 시 재생성 0회(비용 불변) |
| C2 | 재생성 뒤에도 `polestar` FAILED면 **Q1 결정대로** 처리(§4). 어느 안이든 지침 `ANCHORED_TOOLS`·도구 이름 안내는 실제 로드된 도구로 걸러서 넣는다 | 없는 도구 이름이 지침에 0건 → 「Failed to find tool」 0건 |
| C6 | `sre_health.polestar_mcp_reachable`을 실제 값으로: 마지막 툴셋 검사 결과(`true/false/null` + `polestar_mcp_error`) — 헬스 호출마다 프로브하지 않음 | 다운 시 `false`+사유 · 미검사 `null` · 계약 필드 추가는 선택 키(CONTRACT_VERSION 불변) |

테스트: 가짜 holmes 툴셋 상태로 단위 테스트 · `scripts/investigation_bench`의 가짜 MCP 서버로 「서버 꺼짐 → 켜짐」 종단 1건(LLM은 가짜).

### W4 — 도구 호출 마크업 답변 방어 (C7 · 2026-10-19 추가)

근본 조치(vLLM `--enable-auto-tool-choice`·`--tool-call-parser`를 모델에 맞게)는 운영자 몫이다. W4는 오염된 답이 브리핑에 실리지 않게 하는 코드 방어다.

| 항목 | 변경 | 수용 기준 |
|---|---|---|
| 감지 | domain 순수 함수 — 답변에 도구 호출 마크업(`<\|tool_call>`·`<tool_call\|>`·`<tool_call>`·`</tool_call>`·`<\|"\|>` · `call:<식별자>{`)이 있는지 결정적으로 판정. 패턴 목록 상수 1곳 | 화면 원문 → 감지 · 정상 한국어/영어 서술·코드 인용·URL·JSON 인용 → 오탐 0 |
| 종결 | 감지되면 W3 A안 조립(`build_briefing(investigation_skipped=True)`)으로 끝낸다 — `status=done` · 요약 `[미결] 조사 실패 — 조사 LLM 도구 호출 형식 불일치` · 원인 `판단 불가` 계열 · `hypotheses` 빈 목록 · **원문은 브리핑 어디에도 없음** · 한계 1줄 「조사 LLM 응답에 도구 호출 원문이 섞임 — vLLM `--enable-auto-tool-choice`·`--tool-call-parser`를 모델에 맞게 확인(docs/26 §5.6.3)」 · 사전수집 상관 타임라인은 A안처럼 유지. 일부 도구 호출이 정상이었어도 최종 답만 오염되면 같은 종결. severity_judge 입력(`tool_outputs`) 불변 · D-123 어휘 불변 | 가짜 LLM이 마크업 답 → 미결 종결 · 브리핑 직렬화에 마크업 0 |
| 감사·로그 | 감사 `done` 레코드 선택 키 `answer_malformed: "tool_call_markup"` · warning 1줄(원문 앞 200자 이내) | 감사 키 존재 · 정상 조사 레코드엔 키 없음 |
| 헬스 | `sre_health` 선택 키로 마지막 조사의 형식 불일치 여부(CONTRACT_VERSION 불변) | 감지 뒤 true · 정상 뒤 false/미표기 |

테스트: 감지 단위(화면 원문 픽스처·정상 예 오탐 0) · 브리핑 조립 · dispatcher 종단(가짜 LLM).

### W5 — Gemma 도구 호출 마크업 클라이언트 측 파싱 (C8 · 2026-10-19 추가)

운영 측은 vLLM `--tool-call-parser`를 바꾸기 어렵다. W4는 방어 종결만 하므로, W5는 sre_agent가 응답을 후처리해 **조사가 실제로 진행되게** 한다. 근본 조치(파서 교정)는 여전히 운영자 권장 사항으로 남긴다.

| 항목 | 변경 | 수용 기준 |
|---|---|---|
| 위치 | 새 infrastructure 모듈 — `DefaultLLM.completion` 경계에 **응답 후처리** 몽키패치(D-213 가드와 같은 꼴: 멱등 표식 · 설치 실패는 예외 없이 warning). 설치는 `DiagnosisAgent.__init__`의 기존 가드 옆 | 두 가드 설치 순서와 무관하게 둘 다 작동 · 재설치 무해 |
| 조건 | `choices[0].message.tool_calls`가 비고 `content`에 Gemma 블록(`<\|tool_call>call:이름{…}<tool_call\|>`)이 있을 때만 | 정상 응답(tool_calls 있음 / 마크업 없음)은 **입력 객체 그대로**(동일성) |
| 변환 | 블록마다 OpenAI tool call(`id` 고유 · `type="function"` · `function.name` · `function.arguments` JSON 문자열) · content에서 블록 제거 · `finish_reason="tool_calls"` · litellm 실 타입(`ModelResponse`/`Message`/`ChatCompletionMessageToolCall`) | 원문 픽스처 → 이름 2개(`TodoWrite`·`polestar_incident_alarms`) · 인자 JSON 정확(중첩 배열·객체) |
| 인자 파서 | 문자열 구간(`<\|"\|>…<\|"\|>`)을 먼저 토큰화하는 작은 결정적 파서 — 따옴표 없는 키, 숫자·true/false/null, 중첩 배열·객체. 정규식 치환 한 방 금지 | 문자열 안 `:`·`,`·`{`·`}`·`"`·역슬래시 안전 |
| 실패 | 블록 하나라도 파싱 실패 → **원본 그대로** 반환(부분 변환 금지) + warning(원문은 `markup_log_excerpt`) → W4 방어가 그대로 작동 | 깨진 블록 → 원본 동일성 · 이후 W4 미결 종결 |
| 플래그 | `AgentSettings.tool_call_markup_parse_enabled` **기본 on(명시적 예외)** — 마크업이 없으면 no-op, off는 곧 「파서 불일치 환경에서 조사 전건 실패」. 근거·만료일(D-161)을 settings 주석·`.env.example`에 | off → 설치 안 함 → W4 종결 |
| 패턴 단일 출처 | 마크업 리터럴은 `domain/answer_markup.py` 상수를 재사용·확장(두 곳 정의 금지) | grep으로 리터럴 중복 0 |

종단 테스트: 가짜 OpenAI 호환 LLM이 1턴째 Gemma 마크업 본문으로 `polestar_incident_alarms`를 부르고 2턴째 정상 결론 → holmes `DiagnosisAgent.ask`가 도구를 실제 실행하고 결론까지 감 · 브리핑이 W4 미결이 아님. 플래그 off → W4 미결 종결.

**운영 확인 항목(로컬 실측 불가)**: holmes는 다음 요청에 assistant `tool_calls` + `role=tool` 메시지를 싣는다 — vLLM 대화 템플릿이 이를 Gemma 형식으로 렌더링하는지는 내부망에서만 판정된다. **첫 실조사에서 2턴 이상 도구 호출이 이어지는지**로 판정한다(이어지지 않으면 파서 교정이 유일한 해법).

---

## 2. 순서와 크기

W0(운영, 즉시) → W1(S) → W2(S) → W3(M). W1·W2는 Q1과 무관하게 착수 가능. 계획 끝에 `sre_agent` 변경 파일 모듈 단위 회귀 1회(D-303).

## 3. 매뉴얼 (D-255)

C2에서 「도구 미가용」 브리핑 문구가 생기면 관리자 매뉴얼의 조사 브리핑 절에 의미·조치(W0 명령) 한 단락 추가. W1·W2는 문구만 정직해지는 것이라 대상 아님.

## 4. 사용자 결정

**Q1. 폴스타 MCP가 끝내 연결 안 될 때 조사를 어떻게 하나?**

| 안 | 동작 | 결과 |
|---|---|---|
| **A (권장)** | LLM 조사를 생략하고 「조사 불가 — 관측 도구 연결 실패(사유)」 브리핑으로 종결(`status=done` · `[미결]` · D-123 어휘 불변) | LLM 비용 0 · 오류 문구가 가설이 되는 일 원천 차단 · 운영자가 즉시 연결 문제로 인지 |
| B | 도구 이름을 지침에서 빼고 LLM 조사는 진행 | 원격 프로파일은 bash도 꺼져 있어(D-233 · `toolset_profiles.py:240`) 남는 데이터 도구가 사실상 없음(APM 게이트웨이가 붙은 환경만 예외) — LLM 비용만 쓰고 근거 없는 서술이 나올 여지 |

**확정: A**(사용자 2026-10-19). 신규 D-번호로 등재(색인 최댓값+1 — 등재 직전 `ls docs/decisions/ | tail -3` 재확인).


## 5. 완료 기록 (2026-10-19)

- **W1**: 세션 실패 사유를 예외 그룹 말단으로(`_failure_detail` 사본 · `_redact_url`) — 닫힌 포트 → `MCP 세션 실패: 연결 불가(ConnectError: All connection attempts failed) — POLESTAR_MCP_URL=…` · 401/403 → 토큰 확인 문구 · 전 호출 세션 실패면 노트 1줄 · 알람 축 조회 실패면 `alarm_axis=False`(「알람 0건」 미표기). `domain/correlation.py`는 `alarm_axis` 인자가 이미 있어 미변경. 이탈: 그룹 아닌 맨 HTTP/ConnectError도 분류 문구(참조 원본과 동일 · 되돌림 1줄).
- **W2**: 브리핑 `tool_names`를 success·no_data로 한정 · error 호출은 「도구 호출 실패 N건: …」 한계 1줄 · severity_judge 입력 불변. 부수: cross_source `apm_tools_called`도 실패 호출 제외.
- **W3**: holmes 0.36.0은 MCP 툴셋을 생성 시 즉시 검사(지연 아님 · 실측 테스트 고정) → 조사 직전 FAILED면 에이전트 1회 재생성 · 그래도 polestar FAILED면 A안(D-342 — LLM 0 · `build_briefing(investigation_skipped=True)` · 요약 `[미결] 조사 불가 — 관측 도구 연결 실패` · 원인 `판단 불가(조사 생략)` · 사유는 정리해 한계 1줄 · 감사 선택 키 `tools_unavailable`) · 지침 도구 이름은 실제 로드된 도구로 필터(운영자 자유 지침은 제외) · `sre_health.polestar_mcp_reachable` 실값 + 선택 키 `polestar_mcp_error`. 종단: 가짜 세계 MCP 서버 꺼짐(A안·ask 0) → 켬(재기동 없이 재생성 1회·도구 로드) → 다음 건 재생성 0.
- **검증 교정**: verifier Major(A안 오류 문구가 요약·원인·가설로 실림 · 매뉴얼 문구와 불일치)·Minor(holmes 사유 userinfo·여러 줄 · 감사 구분 키 · A안에 어긋난 한계 줄) 교정.
- **매뉴얼(D-255)**: admin 「25. 조사 트리거」에 「조사 불가」 브리핑 의미·W0 조치 단락 · build · `tests/test_manual` 619 passed. 사용자 매뉴얼은 일반 「미결」 설명으로 갈음(미변경).
- **잔여**: W0(폐쇄망 `POLESTAR_MCP_URL` 도달 확인 · 이 변경 배포 뒤에는 mcp_server만 살리면 재기동 불필요, `.env` 수정 시는 재기동) · 응답 언어 지시 부재(별건) · A안 타임라인은 상관 없으면 「타임라인 근거 없음」 문구 유지.
- **W4(C7 · 2026-10-19 추가)**: `domain/answer_markup.py`(패턴 상수·`has_tool_call_markup`·고정 문구·`markup_log_excerpt`) · dispatcher가 A안(도구 연결 실패) 아닌 답에서 마크업을 감지하면 `build_briefing(investigation_skipped=True, skipped_cause=…)`에 원문 대신 고정 요약을 넘겨 종결 — 요약 `[미결] 조사 실패 — 조사 LLM 도구 호출 형식 불일치` · 원인 `판단 불가(조사 LLM 응답 형식 불일치)` · 한계 1줄 · 상관 타임라인·결정적/규칙 가설 유지 · W2 실패 호출 줄 유지 · APM/정체 가드 한계 생략(A안과 같음) · severity_judge 입력 불변 · incomplete도 같은 종결 · 감사 `answer_malformed: "tool_call_markup"` · warning 1줄(`<|"|>`→`"` 정규화 뒤 200자 → 레코드 팩토리 가림) · `sre_health` 선택 키 `last_answer_malformed`. A안 기본 출력 비트 동일(테스트 고정). 테스트 `test_plan188_w4_markup.py` 36 + verifier `test_plan188_w4_verify.py` 23.
- **W4 검증 교정**: verifier Major(Gemma 따옴표 `<|"|>` 때문에 경고 로그의 api_key·password·token 값이 가려지지 않음 → 정규화 헬퍼로 교정) · Minor(`call:` 정규식 경계와 범위 밖 계열 명시 · 타임아웃 뒤 헬스 값 지연 docstring · docs/26 `sre_health` 계약표 키 · 매뉴얼 문구 보완). 범위 밖(감지 안 함): Llama `<|python_tag|>`·Mistral `[TOOL_CALLS]`·맨 JSON — 실측되면 상수에 추가.
- **W4 매뉴얼(D-255)**: admin 「25. 조사 트리거」에 「조사 실패 — 조사 LLM 도구 호출 형식 불일치」 단락(의미·`vllm serve --help | grep -A3 tool-call-parser`·§5.6.3 왕복 판정·재기동 범위) · `docs/26` §5.6.3 판정표 1행 · build · `tests/test_manual` 619 passed.
- **W5(C8 · 2026-10-19 추가 · D-343)**: `infrastructure/tool_call_markup_guard.py` — `DefaultLLM.completion` 응답 후처리(D-213 가드와 같은 꼴 · 두 가드 설치 순서 무관·멱등 테스트 고정). tools가 실리고 `tool_choice="auto"`인 호출의 응답에서 `tool_calls`가 비고 content에 Gemma 블록이 있으면 결정적 재귀 하강 파서로 litellm `ChatCompletionMessageToolCall`을 만든 **사본**을 돌려줌(`model_copy(deep=True)` · 남은 서술 없으면 content `None` — holmes `model_dump(exclude_none)`에서 키가 빠져 vLLM 정상 응답과 같은 모양 · `finish_reason="tool_calls"`). 해석 실패(중복 키·비유한 숫자·블록 밖 마크업 포함)면 원본 + warning → W4 종결. 정상 응답은 입력 객체 그대로. 플래그 `TOOL_CALL_MARKUP_PARSE_ENABLED` 기본 on 예외(만료 2027-04-19). 리터럴은 `answer_markup.GEMMA_*` 단일 출처. 종단: 실 holmes + 가짜 OpenAI 호환 LLM(1턴 마크업 → 가짜 세계 MCP `polestar_incident_alarms` 실행 → 2턴 결론 · 2턴째 요청에 assistant `tool_calls`+`role=tool` 실림) · 플래그 off → W4 미결. 테스트 `test_plan188_w5_tool_call_parse.py` + verifier `test_plan188_w5_verify.py`(합 141).
- **W5 검증 교정**: verifier Major(tools 없는 호출 — holmes 압축 `compaction.py:132`·요약 `llm_summarize.py:156` — 도 변환해 요약이 사라지고 짝 없는 `tool_calls`가 남음 → `DefaultLLM.completion`과 같은 조건 `tools and tool_choice=="auto"`일 때만 변환) · Minor(중복 키·`1e400` 거부 · `markup_log_excerpt`가 200자 경계에서 문자열을 연 채 자르면 비밀값 앞부분이 노출 → 마지막 여는 구분자 앞에서 절단 · 도달 불가 분기 제거). 팀 리드 보충: tools·tool_choice 위치/키워드 인자 판정 테스트 5건 · ruff 교정.
- **W5 매뉴얼(D-255)**: admin 「25. 조사 트리거」 W4 단락에 「Gemma 계열 표기는 sre_agent가 자동 해석 — 그래도 이 브리핑이 나오면 다른 계열 표기이거나 해석 실패(warning 확인)」 · `docs/26` §5.6.3 판정표 1행 · build · `tests/test_manual` 619 passed.
- **W5 운영 확인 항목(로컬 실측 불가)**: 다음 요청의 assistant `tool_calls`+`role=tool`을 사내 vLLM Gemma 템플릿이 렌더링하는지 — **첫 실조사에서 도구 호출이 2턴 이상 이어지는지**로 판정. 이어지지 않으면 파서 교정(운영자)이 유일한 해법.
