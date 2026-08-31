# Spec: `alarm-prompt-ui` — 카드 진입과 질의응답 인계

> Module id: `alarm-prompt-ui` (`CAPABILITY-MAP-86.md`) · `plans/86` §3.2·§3.3 · D-192
> **사용자 확정**: G-1=**서버명 + 하단 추천 칩**(둘 다) · G-2=**확인 후 전송**

## Objective

운영자가 알람 카드에서 **두 경로**로 추천 질의에 닿고, 질의응답 탭에서 **무엇이 나갈지 보고
확인한 뒤** 조회한다. 성공은 *"클릭 두 번 안에 알람이 질의로 바뀐다, 그리고 오전송이 0이다"* 이다.

### 가정 (진행 전 확인)

1. **자동 전송하지 않는다**(G-2 확정). D-183의 *"채우기만"* 정신을 유지하되, 채운 뒤
   **[이대로 조회] / [수정]** 을 붙여 확인 한 번으로 보낸다
2. 전송은 `handleSend()` **단일 진입점**을 통과한다 — 별도 전송 경로를 만들지 않는다(D-183이 확인한 계약)
3. 추천 칩은 피드백 섹션(D-177) **위**에 온다 — "조회할까" 다음에 "유용했나"가 오는 순서
4. `reuseHistoryQuery`(질의 이력)는 **손대지 않는다** — 그쪽은 전송하지 않는 것이 계약이다

## Tech Stack

바닐라 JS + CSS. 기존 `app.js`·`style.css`·`index.html`.

## Commands

```bash
node --check src/static/js/app.js
python3 -m pytest tests/test_api/test_ui_alarm_view.py
```

## Project Structure

```
src/static/js/app.js    → renderAlarmMessage 확장 · bindAlarmPrompts · runAlarmPrompt · 확인 바
src/static/css/style.css→ .alarm-prompt-* · .prompt-confirm-*
src/static/index.html   → 자산 캐시 버전 bump (D-187)
tests/test_api/test_ui_alarm_view.py
```

## Code Style

closure 캡처로 바인딩한다(인라인 `onclick` 금지 — D-049·D-177 선례).

```js
    // 추천 칩 바인딩. data를 closure로 잡아 카드마다 자기 알람을 안다.
    function bindAlarmPrompts(el, data) {
        el.querySelectorAll(".alarm-prompt-chip").forEach(function (chip) {
            chip.addEventListener("click", function () {
                stageAlarmPrompt(chip.dataset.prompt);   // 채우기 + 확인 바 (전송은 사용자 몫)
            });
        });
    }
```

## Testing Strategy

- **정적 회귀**: 칩 마크업 · 피드백 섹션 위 배치 · 접근성(`<button>`·`aria-label`) ·
  **`runAlarmPrompt`가 `handleSend`를 자기 손으로 부르지 않음**(확인 바를 거친다) ·
  **`reuseHistoryQuery` 불변**(D-183 회귀 방지)
- **실행 검증**: 함수 원문 추출 + DOM 스텁(D-190 방식) — 칩 클릭 → 탭 전환 · 입력창 값 · 확인 바 노출,
  [이대로 조회] → `handleSend` 1회 호출, [수정] → 확인 바만 닫힘
- **렌더**: 라이트·다크·모바일(414px) — 확장 미연결 시 미완으로 남기고 보고한다

## Boundaries

- **Always**: `handleSend()` 단일 진입점 사용 · 확인 바에 **전송될 전문**을 보여준다 ·
  칩 상한 3개 · `title`에 프롬프트 전문
- **Ask first**: 자동 전송으로의 전환(G-2 재확정 필요) · 칩 상한 변경 · 헤더 구조 변경
- **Never**: 확인 없이 전송 · `reuseHistoryQuery` 동작 변경 · 인라인 `onclick` ·
  알람 미확인 배지/필터(D-190) 로직 간섭

## Success Criteria

1. **진입 (a)** — 카드 헤더 서버명이 `<button class="alarm-prompt-trigger">`이고, 누르면 그 카드의
   추천 목록이 펼쳐진다(전송 아님). 키보드 도달 가능(Tab·Enter)
2. **진입 (b)** — 카드 하단 `.alarm-prompt-section`에 칩 최대 3개. 라벨은 짧은 요지,
   `title`은 프롬프트 전문
3. **추천이 0건이면 섹션을 렌더하지 않는다**(빈 제목만 남기지 않는다)
4. `is_routine === true`면 섹션이 **접힌 채** 시작한다(`이 알람 조회하기 ▸`) — 노이즈 게이트가
   "일상"으로 판정한 알람에 조회를 권하지 않는다
5. **인계** — 칩/트리거 클릭 시: `setActiveView("chat")` → `promptEl.value = text` →
   `autoResizeTextarea()` → **확인 바 표시**(`이대로 조회` / `수정`) → 포커스
6. `[이대로 조회]`는 `handleSend()`를 **1회** 호출하고 확인 바를 닫는다
7. `[수정]`은 확인 바만 닫고 입력창 내용·포커스를 유지한다
8. 진행 중(`isProcessing`)에는 확인 바의 조회 버튼이 비활성 — 연타로 중복 전송되지 않는다
9. 알람 뷰의 **레벨 필터·검색(D-190)과 간섭하지 않는다** — 칩이 카드 표시 여부를 바꾸지 않는다

## Open Questions

없음. G-1·G-2 확정으로 해소됨.
