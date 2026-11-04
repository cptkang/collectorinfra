# plans/213 — 심층 조사 회복력: Harmony 파싱 오류 재시도 · 재분석 합류 예외 · 생략 경로 진단

**상태**: 완료(2026-11-04 · W1~W3 구현·검증 · 원 211 → 212 재부여 · 원 D-363 → D-366) · 결정 D-366 · 잔여는 폐쇄망 검증(§6)뿐 · 후속 검토 후보: W2 `skipped` 범위(심층 mode off·트리거 미충족·고정 알려진 오류도 합류 제외 — §8) · W3 재시도 직전 취소 확인(D-347)
**브랜치**: `sre_improvement`(8608c8ee에서 분기)
**계획 시작 SHA**: `c4716d9e`
**회귀 대상 파일**: docs/26_sre_agent_guide.md · scripts/manual/content/admin.md · scripts/manual/content/user.md · src/api/routes/alarm.py · src/static/manual/admin.html · src/static/manual/help_index.json · src/static/manual/user.html · sre_agent/.env.example · sre_agent/sre_agent/application/investigation_dispatcher.py · sre_agent/sre_agent/diagnosis.py · sre_agent/sre_agent/domain/answer_markup.py · sre_agent/sre_agent/settings.py · sre_agent/sre_agent/infrastructure/harmony_retry_guard.py(신규) · sre_agent/tests/test_plan188_w4_markup.py · sre_agent/tests/test_plan188_w5_tool_call_parse.py · sre_agent/tests/test_plan213_w1_deep_skip_diag.py · sre_agent/tests/test_plan213_w1_verify.py · sre_agent/tests/test_plan213_w2_briefing_shape.py · sre_agent/tests/test_plan213_w3_harmony_retry.py · sre_agent/tests/test_plan213_w3_verify.py · tests/test_plan213_w2_join_exception.py · tests/test_plan213_w2_verify.py
**관련 결정**: D-366(본 계획) · D-342(W4 — 형식 불일치 미결 종결) · D-343(C8 마크업 변환 가드 · 부기 1 맨 JSON) · D-213(같은 LLM 경계 가드 · 기본 on 예외 선례) · D-341 ㉔(G-31 같은 알람 10분 합류) · D-347(수동 전용 · 부하 상한) · D-339(로그 비밀값 가림) · D-161(플래그·만료일) · D-255(매뉴얼 동반)

## 1. 배경 — 폐쇄망 보고(2026-11-03~04)

1. **생략 오안내**: 플레이북 「심층 조사」가 「생략 — 조사 LLM 응답에 도구 호출 원문이 섞임 — vLLM 파서 확인」으로 끝났다. 그런데 같은 조사의 턴 로그는 도구 호출 19건이 모두 `경로=native`(파서 정상)였고 요약은 `답 도구 호출 형식 섞임=True`였다. 원문은 어디에도 남지 않아 무엇에 걸렸는지 판독하지 못했고, 안내 문구를 따라 파서 의심 → 모델 교체(qwen3-coder-30b-a3b → gpt-oss-120b)까지 검토했다.
2. **Harmony 파싱 오류**: 모델을 gpt-oss-120b로 바꾼 뒤 심층 조사가 `BadRequestError … 400 - {'detail': '500: Unexpected token 173781 while expecting start token 200006'}`로 실패했다(200006 = Harmony `<|start|>`). 서버 vLLM이 모델 출력을 Harmony로 해석하지 못한 것이다. **같은 서버에 다시 요청하면 정상 반환됨을 사용자가 실측**했다.
3. **재분석이 돌지 않음**: 실패 직후 「다시 분석」이 「같은 알람의 진행 중(또는 최근) 요청 조사에 합류했습니다」만 내고 새 조사를 띄우지 않았다. 10분 뒤 재시도로 정상 출력됐다.

## 2. 원인 (코드 실측)

### 2.1 Harmony 오류가 조사 전체를 실패시킨다
- holmes 0.36.0 `DefaultLLM.completion`에는 재시도가 없다(폐쇄망 실측 2026-11-04: `temperature` 인자 처리만 있고 retries 없음 · `holmes.common.env_vars.TEMPERATURE=1e-08`).
- sre_agent의 LLM 경계 가드(D-213 `llm_message_guard` · D-343 `tool_call_markup_guard`)는 요청·응답을 고치기만 하고 예외는 그대로 올린다 → 400 한 번이 심층 조사 `failed`로 끝난다.

### 2.2 심층만 실패한 조사는 `done`이라 합류 대상이 된다
- `investigation_dispatcher.py:2272` — 플레이북 모드는 심층이 실패해도 `job.status = "done"`.
- `src/api/routes/alarm.py:2720-2747` `_manual_join_target` — 10분 합류 창 안이고 상태가 `_FOLLOWUP_RETRYABLE`(`failed`·`timeout`·`rejected`·`not_found`)이 아니면 합류. 「실패는 다시 요청할 수 있다」는 의도가 심층만 실패한 `done`에는 적용되지 않는다. 추가 조사 합류(`alarm.py:2418`)도 같은 집합을 쓴다.

### 2.3 심층 생략 경로에는 증거가 남지 않는다
| | 주 경로(HolmesGPT 단독) | 플레이북 심층 섹션 |
|---|---|---|
| 원문 앞부분 warning | 있음(`dispatcher:1218`) | **없음**(`dispatcher:2040-2044`) |
| 감사 | `answer_malformed` | **없음** · `deep_dive_status`는 생략 전 값(`done`) |
| 화면 문구 | `MALFORMED_ANSWER_LIMITATION` 1종 | 같음 |

판정기 `has_tool_call_markup`는 성격이 다른 세 꼴(마크업 리터럴 · `call:이름{` · 답 전체가 맨 JSON)을 하나로 잡는데, 문구는 늘 「vLLM 파서 확인」이다.

## 3. 범위와 비범위

- **범위**: W1(C) · W2(B) · W3(A).
- **비범위**: 재시도 시 temperature 변경(사용자 결정 2026-11-04 — 건드리지 않는다) · 「심층만 다시」 재실행 · S3 `apm_slow_transactions` 창·X-View 사전 확인(사용자 결정: 우선 `APM_TOOL_TIMEOUT_SECONDS` 설정으로 운영) · 단계 소견 타이머 기점·클라이언트 타임아웃(사용자 결정: 발생 시 재검토) · 턴별 경로(native/변환)를 결과에 싣는 정밀 분류.

## 4. 설계

### W1 — 심층 생략 경로 진단 (C · D-366 ③)
1. **종류 분류**(순수 도메인 · `sre_agent/sre_agent/domain/answer_markup.py`): `classify_tool_call_markup(text) -> "markup" | "call_syntax" | "bare_json" | None`. 판정 범위는 `has_tool_call_markup`과 **같아야 한다**(분류가 None이면 섞임도 False — 단언).
2. **한계 문구 3종**(같은 모듈 상수 · 주 경로·심층 경로 공용 · 함수 `malformed_limitation(kind, tools_run)`):
   - 도구 0건 · `markup`/`call_syntax` → 현행 `MALFORMED_ANSWER_LIMITATION` 그대로.
   - 도구 0건 · `bare_json` → 「조사 LLM이 도구 호출을 본문 JSON으로 냄 — 파서 불일치 또는 모델 응답 문제(docs/26 §5.6.3 판정으로 구분)」.
   - 도구 1건 이상 → 「도구 N건은 처리됐으나 마지막 답이 서술이 아니라 도구 호출 꼴(<종류 한국어>)로 끝남 — 파서보다 모델 응답 문제 · 다시 분석 또는 모델 교체 검토」. **단정 금지** — C8 변환 호출도 N에 포함되므로 「파서 정상」이라 쓰지 않는다.
   - 도구 건수는 `result.tool_outputs` 길이.
3. **심층 경로 warning**(`_deep_section` 생략 분기): 주 경로와 같은 형식 1줄 — `investigation_id` · 종류 · 도구 건수 · `markup_log_excerpt(answer)`(앞 200자). `tools_unavailable` 생략은 종전대로(원문 없음).
4. **감사 가산 키**(플레이북 `done` 레코드): 생략이면 `deep_dive_display: "skipped"` · `deep_dive_malformed: <종류>`. **기존 `deep_dive_status`는 의미(심층 엔진 종결 상태)와 값을 바꾸지 않는다.** 값이 있을 때만 키를 더한다(정상 경로 레코드 모양 불변).
5. **주 경로 정합**: 주 경로 한계 문구도 2의 함수를 쓰고, 감사 `answer_malformed` 값은 종전 `tool_call_markup` 유지 + 가산 키 `answer_malformed_kind: <종류>`(기존 값 소비처 보호).
6. **불변**: 생략 판정 기준(`has_tool_call_markup`) · 원문을 브리핑·통보에 싣지 않음(plans/188 C7) · C8 변환 · 중요도 판정 입력.

### W2 — 재분석·추가 조사 합류 예외 (B · D-366 ②)
1. `src/api/routes/alarm.py`에 판정 함수 하나: 레코드 `briefing`의 심층 섹션 상태가 `failed`·`timeout`·`skipped`면 「다시 요청 가능」으로 본다. `incomplete`(step 상한 미완주)는 **포함하지 않는다**(사용자 결정 2026-11-04 — 권고안).
2. `_manual_join_target`(다시 분석)과 추가 조사 합류(`alarm.py:2418` 부근) **양쪽에** 같은 판정을 적용한다 — 상태가 `done`이어도 심층 실패면 합류하지 않는다. 진행 중 조사 합류 규칙·사용자별 진행 중 1건·시간당 상한은 불변.
3. **실 shape 선확인(W2 착수 조건)**: ① 본체 `investigation_store` 레코드의 `briefing`에서 플레이북 브리핑의 심층 섹션 키 경로(`deep_dive.status` 위치)를 sre_agent `build_playbook_briefing` 산출과 대조 ② `clip_briefing`(크기 초과 절단)이 그 키를 남기는지 — 잘려 판정 불가면 **종전대로 합류**(보수적)하고 debug 로그. 둘 다 테스트로 고정.
4. 경계: 본체는 sre_agent를 import하지 않는다(D-139 — 저장된 JSON만 읽음). 키 이름 리터럴은 본체 쪽 한 곳에 두고 sre_agent 브리핑 shape 테스트와 짝을 맞춘다.
5. 플래그 없음(결함 교정 — 「실패는 다시 요청 가능」 의도 복원 · D-341 ㉔ 부기). 매뉴얼 사용자편 「다시 분석」 항목 갱신(D-255).

### W3 — Harmony 파싱 오류 재시도 가드 (A · D-366 ①)
1. **위치**: `sre_agent/sre_agent/infrastructure/` 새 모듈 — `DefaultLLM.completion` 경계(D-213·D-343과 같은 꼴 · 멱등 표식 · 설치 실패는 warning). **가장 안쪽**(실제 호출 바로 바깥)에 둔다 — `diagnosis.py`에서 `install_system_message_guard()`보다 **먼저** 설치해 C8이 최종 응답을 보게 한다. 설치 순서를 테스트로 고정.
2. **대상 오류**: 예외 메시지(문자열화)에 Harmony 파싱 실패 표지(`expecting start token`)가 있는 litellm `BadRequestError`·`InternalServerError`만. 표지 리터럴은 상수 1곳. 그 밖의 예외는 그대로 올린다(컨텍스트 초과·인증·403 등).
3. **재시도**: 같은 인자 그대로 **1회**(설정 `LLM_HARMONY_RETRY_MAX` 기본 1 · 0이면 off). temperature 등 **요청을 바꾸지 않는다**(사용자 결정). 재시도도 실패하면 마지막 예외를 그대로 올린다(종전 실패 경로).
4. **범위**: tools를 보내는 조사 루프 호출(`tools` 있고 `tool_choice == "auto"` — C8 `_sends_tools`와 같은 조건)**과** 단계 소견·종합 호출 모두. 단 소견·종합은 바깥 타이머(60·90초)가 이미 끝났으면 재시도 결과가 버려지므로 낭비다 — 재시도 전 경과 시간을 보고 **첫 시도가 `LLM_HARMONY_RETRY_SKIP_AFTER_S`(기본 45초)를 넘겼으면 재시도하지 않는다**(조사 루프에도 같은 기준 · 조사 전체 600초 보호).
5. **관측**: 재시도마다 warning 1줄(`Harmony 파싱 오류 재시도 n/N — 첫 시도 경과 Xs · 호출 종류=조사|요약`) · 결과(통과/재실패) info 1줄. 오류 원문은 앞 200자만(D-339 가림 경유).
6. **플래그**: 기본 on 예외(D-213·D-343 선례 — 표지 오류가 날 때만 동작, 그 밖은 비트 동일) · 만료일 2027-05-04(D-161). `.env.example` 갱신(전체 키 · 코드 기본값).
7. **테스트**: 가짜 `DefaultLLM` — 표지 오류 1회 후 정상 → 응답 반환 · 2회 연속 → 예외 · 비표지 400 → 즉시 예외(재시도 0) · 경과 초과 → 재시도 0 · 플래그 0 → 재시도 0 · C8·D-213 가드와 함께 설치 시 순서·멱등.

## 5. Wave · 위임

| Wave | 범위 | 담당 |
|---|---|---|
| W1 | §4 W1 + `docs/26` §5.6.3 결과 표 · 매뉴얼 admin 형식 불일치 항목 · 기존 C7 문구 단언 갱신(`test_plan188_w4_markup.py` 등 repo 전체 grep) | implementer → verifier |
| W2 | §4 W2 + 매뉴얼 user 「다시 분석」 | implementer → verifier |
| W3 | §4 W3 + `.env.example` · `docs/26` LLM 연결 절 1단락 | implementer → verifier |

- Wave 중간: 자기 테스트 + `regress.py --no-tests --files …`. 마지막에 team-lead가 `python scripts/regress.py --base c4716d9e --files <계획이 바꾼 파일…>` 1회(D-303). sre_agent 테스트는 자체 `.venv`.
- 실 LLM 호출 없음(가짜 LLM). 폐쇄망 검증 항목은 §6.

## 6. 폐쇄망 검증 항목

1. W1: 생략 발생 시 `journalctl -u sre-agent | grep "심층 조사 답변에 도구 호출 형식"`으로 종류·앞부분 판독 · 감사 `deep_dive_display`.
2. W2: 심층 실패 직후 「다시 분석」 → 새 조사 접수(`joined=false`).
3. W3: `grep "Harmony 파싱 오류 재시도"` 건수와 통과율 — 재실패가 잦으면 재시도 무용 판정 후 근본 조치(엔드포인트 vLLM·openai-harmony 버전)를 운영자에게 요청.

## 7. 반입 파일 · 확인 grep

| 파일 | 확인 grep 심볼 |
|---|---|
| `sre_agent/sre_agent/domain/answer_markup.py` | `classify_tool_call_markup` · `malformed_limitation` · `MALFORMED_AFTER_TOOLS_LIMITATION` |
| `sre_agent/sre_agent/application/investigation_dispatcher.py` | `심층 조사 답변에 도구 호출 형식` · `deep_dive_malformed` · `answer_malformed_kind` |
| `sre_agent/sre_agent/infrastructure/harmony_retry_guard.py`(신규) | `HARMONY_PARSE_ERROR_MARKER` · `install_harmony_retry_guard` |
| `sre_agent/sre_agent/diagnosis.py` | `install_harmony_retry_guard(` |
| `sre_agent/sre_agent/settings.py` | `llm_harmony_retry_max` · `llm_harmony_retry_skip_after_s` |
| `src/api/routes/alarm.py` | `_deep_dive_retryable(` · `_DEEP_DIVE_RETRYABLE` · `not _deep_dive_retryable(current)` |
| `src/static/manual/user.html` · `admin.html` · `help_index.json` | user: 「심층 조사(HolmesGPT) 칸이 실패·타임아웃·생략」 · admin: 「도구 N건은 처리됐으나」 |

- `sre_agent/.env.example`은 참고용(신규 2키 `LLM_HARMONY_RETRY_MAX=1`·`LLM_HARMONY_RETRY_SKIP_AFTER_S=45.0` — 운영 `.env`는 기본값으로 동작하므로 추가 불필요 · config 정본 덮어쓰기 금지).
- 테스트·`docs/26`·`scripts/manual/` 원천은 반입 불필요.

## 8. 구현 기록 (2026-11-04)

- **W1**: 분류기는 `has_tool_call_markup`의 부분 판정을 같은 순서로 재사용(None ⇔ 섞임 False 단언). 종류 한국어 이름 「마크업 리터럴」·「call:이름{…} 구문」·「본문 JSON」(계획서에 값 없어 정함). `_deep_section`에 선택 인자 `investigation_id` 추가. 감사 가산 키는 `_finish_playbook`의 `status=="done"`·`deep_dive.status=="skipped"`일 때만. 결과 표는 실제 표가 있는 `docs/26` §5.6.6에 반영(§5.6.3 아님). 기존 `test_plan188_w4_markup.py` 픽스처가 도구 1건이라 기대 문구가 3번째 문구로 바뀜(설계대로).
- **W2**: 실 shape — `briefing["deep_dive"]["status"]`(sre_agent `briefing_builder.py` → `job.summary()["briefing"]` → 본체 `investigation_poll.persist_terminal` → `investigation_store.record`가 `clip_briefing` 후 dict 저장). 축소 후보는 모두 `status` 보존, 최소 후보도 넘치면 `deep_dive` 키 자체가 빠짐 → 판정 불가 = 종전 합류. 최종 값 집합 `skipped`·`failed`·`timeout`·`done`(`incomplete`는 `done` 아래 불리언). `partial_briefing`(running)은 저장되지 않음. 추가 조사는 kind `diagnosis`라 플레이북 브리핑이 없어 현 런타임에선 판정이 늘 False(대칭 배선만 · 해 없음).
  - **후속 검토 후보**: `skipped`에는 W1 서술 생략·시간 부족 외에 심층 mode off·고정 알려진 오류·트리거 미충족도 들어간다. mode off(또는 conditional 미충족) 배치에서는 끝난 플레이북 조사에 10분 합류 창이 사실상 사라진다(진행 중 합류·사용자별 시간당 상한은 유지 — 한 알람 총부하 = 사용자 수 × 상한). 사용자 확정안대로 구현했고, 사유별 세분은 별도 결정 사항.
- **W3**: 사슬 C8 → D-213 → Harmony → 원본(조사 계측 훅이 먼저 설치되면 Harmony 안쪽). `LLM_HARMONY_RETRY_MAX=0`이면 미설치. 경과는 첫 시도 시작부터 누적(N=1이면 같음). 생략 시 info 1줄 추가. 로컬 holmes 0.45.0 실 `DefaultLLM.completion`+litellm을 127.0.0.1 가짜 서버로 종단 확인 — 재시도 본문 바이트 동일(temperature 1e-08 포함). 0.36.0 동일성은 추정. 재시도 직전 취소(D-347 `cancel_event`) 확인 없음 — 후속 후보. 매뉴얼 대상 아님(내부 가드).
