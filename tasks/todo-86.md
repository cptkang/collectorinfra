# Todo 86 — 알람 → 질의 프롬프트 인계

> `tasks/plan-86.md` · 모듈 id는 `CAPABILITY-MAP-86.md` 기준

## `alarm-prompt-builder`

- [x] **T1. `buildAlarmPrompts(data)` + 축 매핑표**
  - Acceptance: `SPEC-alarm-prompt-builder.md` 성공 기준 1~8. 축별 문구가 골드셋 동형 ·
    상한 3 · 급증형 1순위(`pattern_type==="급증"`) · 대상명 없으면 빈 배열 ·
    `server.Network`는 **제외**(T7 검증 전)
  - Verify: `pytest tests/test_api/test_ui_alarm_view.py` · 함수 원문 추출 실행 검증
  - Files: `src/static/js/app.js` · `tests/test_api/test_ui_alarm_view.py`

## `alarm-prompt-ui`

- [x] **T2. 카드 마크업 + CSS**
  - Acceptance: 하단 `.alarm-prompt-section`(칩 ≤3, `title`=전문) · 헤더 서버명
    `<button class="alarm-prompt-trigger">` · 추천 0건이면 섹션 미렌더 ·
    `is_routine===true`면 접힌 채 시작 · 피드백 섹션 **위**
  - Verify: 정적 회귀 + `node --check`
  - Files: `src/static/js/app.js` · `src/static/css/style.css`

- [x] **T3. 인계 배선 — 확인 후 전송**
  - Acceptance: `stageAlarmPrompt(text)` = 탭 전환 + 입력창 채움 + 확인 바 표시 + 포커스 ·
    `[이대로 조회]` → `handleSend()` 1회 · `[수정]` → 바만 닫힘 · `isProcessing`이면 조회 비활성 ·
    **`reuseHistoryQuery` 불변** · D-190 필터 무간섭
  - Verify: 정적 회귀(전송 경로 단일성·이력 경로 불변) + DOM 스텁 실행 검증
  - Files: `src/static/js/app.js` · `src/static/css/style.css` · `src/static/index.html`(캐시 bump)

## `alarm-prompt-llm-suggest`

- [x] **T4. 라우트 + 플래그 + 프롬프트 (기본 off)**
  - Acceptance: `SPEC-alarm-prompt-llm-suggest.md` 성공 기준 1~7. 503(off) · 403(존) ·
    LLM 실패 200+null · 플래그 off에서 기존 동작 바이트 동일
  - Verify: `pytest tests/test_api/test_alarm_prompt_suggest.py` · `arch_check --ci` ·
    `overfit_check --ci` · **실 LLM 호출 없음(mock)**
  - Files: `src/api/routes/alarm.py` · `src/config.py` ·
    `noise_gate/prompts/alarm_prompt_suggest.py` · `tests/test_api/test_alarm_prompt_suggest.py`

- [x] **T5. 프론트 연동 — 결정적 추천이 0건일 때만 호출**
  - Acceptance: 추천이 있으면 네트워크 요청 0 · 503/403/실패 시 칩 영역이 조용히 비고 카드는 정상 ·
    LLM 산출도 확인 후 전송
  - Verify: 정적 회귀(호출 조건) + `node --check`
  - Files: `src/static/js/app.js`

## 마무리

- [x] **T6. 문서**
  - Acceptance: D-192 본문 등재(채번 3곳 재확인) · `plans/86` 상태를 구현 완료로 · `plans/INDEX.md` 갱신
  - Verify: `grep`으로 채번 충돌 0
  - Files: `docs/02_decision.md` · `plans/86-*.md` · `plans/INDEX.md`

- [ ] **T7. (승인 필요) 답변 가능성 실측** — 축별 추천 1건씩 실 파이프라인 구동
  - Acceptance: 생성 SQL이 의도한 테이블 참조 · 실패 축은 매핑표에서 제거 ·
    `server.Network` 포함 여부 확정
  - Verify: **과금 경로 — 건별 사용자 승인 후 실행**(D-127)
  - Files: `src/static/js/app.js`(매핑표 조정 시)
