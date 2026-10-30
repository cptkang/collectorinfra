# 204. 알람 추천 질의·종류 분류의 폐쇄망 표시 이름 불일치 수정 — `ALARM_PROMPT_AXES` · `classify_alarm_kind`

> **작성일**: 2026-10-30 · v0(등재만 — 설계 전)
> **상태**: **완료(2026-10-30)** — 결함 수정 · 플래그 없음(사용자 승인) · 새 결정 없음(D-192 원칙 안의 대응표 추가)
> **계획 시작 SHA**: `a289818f`
> **회귀 대상 파일**: `src/static/js/app.js` · `src/static/index.html` · `noise_gate/domain/process_rank.py` · `sre_agent/sre_agent/application/investigation_guidance.py` · `src/domain/symptom_router.py` · `sre_agent/config/playbooks/host.fs.yaml` · 테스트 7(`tests/test_api/test_ui_alarm_view.py` · `noise_gate/tests/test_alarm_process_rank.py` · `sre_agent/tests/test_investigation_guidance.py` · 캐시 버전 단언 4)
> **구현 결과**: ① `app.js` `ALARM_PROMPT_AXIS_ALIASES`(소문자 표시 이름·`server.filesystem` → 축 키) + `alarmPromptAxisKey()`(대소문자 무시 · 코드값도 대소문자 무시 대조) — 「CPU」·「메모리」·「디스크」·「개별 파일시스템」·「로그 모니터」·「서버」가 결정적 추천을 받는다. 출력 `axis`는 기존 코드값 키. 「프로세스」·「WebURL 감시」는 미매핑 유지(D-192). ② `classify_alarm_kind` 디스크 키워드에 「파일시스템」 — 사본 **셋**(게이트 `process_rank` · sre_agent `investigation_guidance` · 본체 `symptom_router.KIND_KEYWORDS` — 사본 동등성 테스트가 셋째를 강제). 채팅 증상 라우터는 「파일시스템」 질의가 `kind:disk`(유형 미확정 → HolmesGPT 그대로)로 바뀔 뿐 경로 불변. ③ 백엔드 `/alarm/suggest-prompt`는 `resource_type`을 프롬프트 문자열로만 쓴다(코드값 의존 0) · 「결정적 추천이 있으면 보강 미호출」 규약(`if (!alarmPrompts.length) requestLlmPrompt`) 유지. ④ `app.js?v=46`. 매뉴얼: 대상 아님(U-42 설명이 추천 출처를 구분하지 않아 그대로 맞음).
> **검증**: 자기 테스트 본체 101 passed(node 실행 단언 포함) · sre_agent 433 passed(시스템 python) · `regress.py --base a289818f` arch/overfit/config_fallback 통과 · 본체 1,751 통과 · 실패 24 = 전부 기준선 동일(Windows 명령줄 길이 WinError 206 · 심볼릭 링크 권한 · 벤더 sha256 22건) + xdist 병렬에서만 흔들리는 `test_ui_plan193_w2_portal_tab` 2건(직렬 통과 · 기준선 동일) · `범위: 모듈 단위 — 전체 미실행`
> **관련 결정**: D-192(알람 → 질의 인계 · 골드셋 동형 추천) · D-354(담당 업무 — W3 추천 재정렬이 이 수정에 기댄다)
> **결정 번호**: 미배정 — 설계 때 필요하면 채번한다.

---

## 1. 현상 (`plans/201` §1.2 ①′·①″ · 곁가지 발견 2)

- 폐쇄망 알람 payload의 `resource_type`은 코드값(`server.Cpus`)이 아니라 **한글 표시 이름**(「CPU」·「메모리」·「개별 파일시스템」…)이다 — 결정 기록 표본에서 코드값 0건.
- **추천 질의**: `src/static/js/app.js:4085` `ALARM_PROMPT_AXES`는 코드값 키라 폐쇄망 폴스타 알람에서 **한 번도 맞지 않는다** — 전부 「미매핑 → LLM 보강」. 또 키가 `server.FileSystems`(복수)라 코드값이 와도 개별 파일시스템(`server.FileSystem`)과 맞지 않는다.
- **종류 분류**: `classify_alarm_kind`의 디스크 키워드(`noise_gate/domain/process_rank.py:34` · 사본 `sre_agent/sre_agent/application/investigation_guidance.py:223` `_KIND_KEYWORDS`)에 「파일시스템」이 없어 「개별 파일시스템」 알람은 알람명에 디스크 단어가 없으면 kind None → 영향 프로세스 표·메시지 기반 보강이 붙지 않는다.

## 2. W0 — 착수 전 확인 (2026-10-30 · `plans/201` 전체 검토)

- **보강 수집 증가 여부**: 「파일시스템」 키워드가 들어가면 「개별 파일시스템」 알람이 `disk`로 분류된다. 폐쇄망 `ALARM_MESSAGE_ENRICHMENT_ENABLED`(코드 기본 false · `src/config/alarm.py:323`)가 켜져 있으면 이 알람마다 호스트 스냅샷 조회(폴스타 프로세스 API · 타임아웃 `enrichment_l1_timeout_seconds` 3초 · `alarm_context_enricher.py:414-430`)가 새로 생긴다(b0 FileSystem 30일 36,942건). 켜져 있으면 키워드 추가를 분류 결과와 보강 수집으로 나눌지 정한다. → **확인 완료(2026-10-30)**: 폐쇄망 `.env`에 이 키가 없어 코드 기본값(false)으로 돈다 — 키워드를 추가해도 보강 수집은 늘지 않는다. 분류 결과만 바뀐다(영향 프로세스 표는 원래 cpu/memory 전용이라 무관). 나중에 이 플래그를 켤 때 파일시스템 알람 수집 부하를 함께 본다.
- **LLM 보강 추천 호출 수**: 지금 폴스타 알람 카드는 전부 LLM 보강 경로다 — 수정 뒤 줄어드는 호출 수를 기록한다(이득 측).

## 3. 범위 후보

- 추천 키에 표시 이름을 더하고 대소문자 무시로 대조한다. 표시 이름↔코드 대응은 `plans/201` §1.2 ①″ 실측표(세 존 동일 29행)를 쓴다 — `config/duty_areas.yaml`과 같은 원천.
- 「파일시스템」 키워드를 두 사본에 함께 넣는다(동형 유지 — 사본 대조 테스트 확인).
- 기존 단언 테스트 갱신: `tests/test_api/test_ui_alarm_view.py`(171·183·194행 부근 `ALARM_PROMPT_AXES` 슬라이스).

## 4. 주의

- 지금 폐쇄망의 모든 폴스타 알람은 LLM 보강 추천을 받는다. 고치면 결정적 추천(골드셋 동형 문구)으로 바뀌어 **사용자가 보는 추천 문구가 달라진다** — 매뉴얼 동반 정책(D-255) 대상인지 설계 때 확인한다.
- 플래그 없이 고치는 것이 권고다. 담당 플래그 뒤에 두면 담당 미지정 사용자가 계속 결함을 겪는다.
