# Spec: 상관 on 경로 브리핑 계약 단언 (`plans/91` 1-1 · C′-0)

> 근거 정본: `plans/50` §0.8.3 C′-0 · §7.2(가설) · §9.1(상대시각) · `docs/23` §7. 모듈 id **`correlation-e2e-assertions`**. 의존 없음.
> 이 모듈에 의존: `change-event-overlay` · `related-host-consumption` · `investigation-feedback-ref`.

## Objective

"상관을 켜면 브리핑에 rank·confidence 가설, `T-` 상대시각 타임라인, 결정적 한계가 **실제로 도달한다**"를 코드로 고정한다.
현 `test_investigation_e2e.py`는 완주·도구 호출·토큰만 단언하고(실측 grep 0건), dispatcher 테스트는 `prefetch` 감사까지만 본다.

## 계약 결정

- **레벨 A(과금 0)**: `test_investigation_dispatcher.py`에 신규 테스트 — 가짜 배치(`test_evidence_prefetch`의 `_batch` 형태)로 `prefetch_and_correlate`를
  실제로 돌리는 `prefetch_fn`을 dispatcher에 주입 → `build_briefing`까지 통과 → 단언: ①`briefing["root_cause_hypotheses"]`의 rank가 1..n 연속·confidence ∈
  {high, medium, low}·rank 1이 선행 지표 ②`briefing["timeline"][0]`이 `T-` 접두 ③`briefing["limitations"]`에 `CORRELATION_NOT_CAUSATION_NOTE`와 상관
  `notes` 포함 ④감사 JSONL의 `prefetch` 이벤트에 `leading_signal`.
- **실 경로(RUN_E2E · D-127)**: `test_investigation_e2e.py`에 JobStore 경유 테스트 1건 추가 — `evidence_correlation_enabled=True` + `event.alarmTime` 페이로드 →
  같은 4단언(단, 수치는 단언하지 않는다 — 실 데이터). 파일 전역 `pytestmark`가 이미 RUN_E2E·Gemini·MCP 도달을 게이트한다.
- **바이트 동일**: 프로덕션 코드 변경 0.

## 성공 기준

1. 레벨 A 테스트가 과금 0으로 통과하고 상관 off(`prefetch_fn=None`)면 `root_cause_hypotheses == []`·타임라인에 `T-` 없음(현행) — 대조 테스트 동반.
2. 실 경로 테스트는 RUN_E2E 미설정 시 skip(수집만 되고 실행 0).
