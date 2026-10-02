# Spec: 스코프 칩 UI — `scope-chip-ui`

> `CAPABILITY-MAP-90.md` · `plans/90` §3.4·§9.3 · D-205 · 작성 2026-09-09

## Objective

입력창 바로 위에 **이 창이 다음 질의에서 볼 폴스타 존**을 칩으로 보여주고, 칩에서 존을 고르거나 ×로 해제한다.
칩은 서버 `db_scope`의 **거울**이다 — 프론트가 추측하지 않는다.

## Tech Stack

바닐라 JS(IIFE, `src/static/js/app.js`) · CSS 변수 테마 · 정적 문자열 회귀 테스트(pytest)

## Commands

```bash
node --check src/static/js/app.js
pytest tests/test_api/test_ui_zone_scope.py tests/test_api/test_ui_scope_select.py -q
```

## Project Structure

```
src/static/index.html   → #dbScopeChip 마크업(#promptConfirm 다음, .input-row 앞) · app.js?v=10
src/static/js/app.js    → 상태(currentDbScope · pendingDbIds · pendingReset) · renderDbScopeChip · 팝오버 · 전송 3경로 주입 · 응답 4경로 수신
src/static/css/style.css → .db-scope-chip* (.prompt-confirm 계열 위계)
tests/test_api/test_ui_zone_scope.py
```

## Code Style

```js
// 칩 상태는 서버 값(currentDbScope)과 "다음 전송에 실릴 것"(pendingDbIds/pendingReset)만으로 결정된다.
function dbScopeChipState() {
    if (pendingReset) return "pending-reset";
    if (pendingDbIds) return "pending";
    if (!currentDbScope || !currentDbScope.db_ids || !currentDbScope.db_ids.length) return "none";
    return currentDbScope.source === "selected" ? "selected" : "inherited";
}
```
- 라벨은 서버 값(`zone_group.label` · `scope/options`의 `label`)만. 프론트에 존 라벨 리터럴을 두지 않는다.
- 외부 데이터가 들어가는 텍스트는 `textContent`로. 마크업 문자열 조립 시 `escapeHtml`.
- 팝오버는 `renderZoneClarification`의 마크업·라디오 규칙을 그대로 재사용(옵션 형식이 같다).

## Testing Strategy

정적 문자열 단언(`test_ui_scope_select.py` 관례): 칩 DOM id · 5상태 문자열 · 3경로 주입 코드
(`streamBody.reset_db_scope` · `queryBody.reset_db_scope` · 파일 경로는 selected만) · 4경로 수신
(`renderDbScopeChip(` 호출 4곳) · `v=10` · `/api/v1/scope/options` fetch. 실 브라우저 확인은 T7(승인 후).

## Boundaries

- **Always**: `handleSend` 단일 진입점에서 pending을 소비·초기화 · 역질문 답 재전송 경로(`app.js:1418`) 무변경
- **Ask first**: 헤더 이동 · localStorage 저장 · 진행 패널 변경
- **Never**: 매 턴 `selected_db_ids` 자동 재전송(G-2 ⓑ) · 프론트 존 라벨 복제

## Success Criteria

1. 응답(`done`·JSON·파일 2경로)마다 칩이 `db_scope`로 갱신된다. `clarification` 응답(`db_scope` 없음)은 직전 값 유지.
2. 칩 텍스트: `none` "존 미지정 — 첫 질의처럼 확인" · `inherited` "{label} · 승계 중" · `selected` "{label} · 선택" ·
   `pending` "다음 질의부터: {label}" · `pending-reset` "다음 질의에서 다시 확인".
3. ▾ → 팝오버(옵션은 `GET /api/v1/scope/options`, 1회 로드·실패 시 팝오버 비활성 + 칩은 표시만) → 확정 시 `pendingDbIds`.
4. × → `pendingReset=true`(재선택하면 해제). `none` 상태에서는 × 없음.
5. `handleSend`: `pendingDbIds` → 텍스트 2경로·파일 경로에 `selected_db_ids`; `pendingReset` → 텍스트 2경로에 `reset_db_scope:true`; 전송 후 둘 다 초기화.
6. 솔루션 보조 줄은 `db_scope.solutions.length > 1`일 때만 렌더(오늘은 미표시).
7. `node --check` 통과 · 정적 회귀 통과.
