# 51. 스트리밍 응답 UX 개선 (① 조건부 자동 스크롤 + 플로팅 버튼 · ② 표 가로 스크롤 보존)

> 작성일: 2026-06-26
> 상위/관련 계획: `plans/23-ui-progress-and-excel-fix.md`(SSE 인디케이터·다운로드 버튼), `plans/08-ui-screens.md`
> 관련 결정: D-009(최종 사용자 응답 SSE 토큰 스트리밍 — `astream_text` + `USER_RESPONSE_TAG`)
> 신규 결정: **D-044**(스트리밍 조건부 자동 스크롤 — stick-to-bottom + 플로팅 버튼, **기록 완료**) · **D-045**(스트리밍 비파괴 렌더 — DOM 모핑, **기록 완료**). ※ D-043은 같은 날 supersedes 결정이 선점 → 변경 이력 표 grep 후 D-044/D-045 부여(2026-06-25 번호 충돌 교훈).
> 범위: 프론트엔드 전용(`src/static/js/app.js`, `src/static/index.html`, `src/static/css/style.css`). 백엔드/SSE 프로토콜 변경 없음. ※ Part 2는 폐쇄망 사유로 morphdom 벤더 대신 **자체 morph 구현**으로 완료(외부 의존성 0).

---

## 진행 상태

| 파트 | 내용 | 상태 |
|------|------|------|
| **Part 1** (§1~§7) | 조건부 자동 스크롤(stick-to-bottom) + 맨 아래 이동 플로팅 버튼 + 신규 내용 강조 | ✅ **구현 완료** (수동/Playwright 검증만 잔여) |
| **Part 2** (§8~) | 스트리밍 중 표 **가로 스크롤 위치 초기화** 해결 — DOM 모핑(B-1) + rAF 렌더 코얼레싱 | ✅ **구현 완료** (자체 morph, 수동 검증만 잔여) |

---

## 1. 배경 — 관찰된 문제

D-009로 최종 사용자 응답이 SSE `token` 이벤트로 토큰 단위 스트리밍된다. 현재 프론트는 토큰을 받을 때마다 **무조건 맨 아래로 스크롤**한다.

### 1.1 문제 A — 매 chunk마다 강제 스크롤되어 응답을 즉시 읽기 어려움

`src/static/js/app.js:812-816`:

```js
if (event.type === "token") {
    accumulatedText += event.content;
    var textEl = document.getElementById("streamingText");
    if (textEl) textEl.innerHTML = renderMarkdown(accumulatedText);
    scrollToBottom();           // ← 토큰마다 무조건 호출
}
```

`scrollToBottom()` (`app.js:1209-1213`)은 `chatMessages.scrollTop = chatMessages.scrollHeight`를 `requestAnimationFrame`으로 실행한다. 여기에 더해 `.chat-messages`에 **`scroll-behavior: smooth`**(`style.css:226`)가 걸려 있어, 토큰마다 부드러운 스크롤 애니메이션이 재트리거된다. 결과적으로:

- 초당 수십 토큰이 들어오면 매번 스크롤 위치가 재설정되어 화면이 "튀는(refresh)" 느낌.
- `innerHTML = renderMarkdown(...)`로 텍스트 노드가 통째로 재생성되며 레이아웃이 출렁임.
- ChatGPT/Claude 등은 **사용자가 맨 아래에 있을 때만** 부드럽게 따라 내려가고, 위로 올려 읽는 중에는 가만히 둔다. 현재 구현은 이 구분이 없다.

### 1.2 문제 B — 위로 스크롤해도 강제로 다시 끌려 내려감 (면역 없음)

사용자가 긴 응답 중간을 읽으려고 위로 스크롤해도, 다음 토큰이 도착하는 순간 `scrollToBottom()`이 다시 맨 아래로 끌어내린다. 스트리밍 중에는 사실상 위로 읽는 것이 불가능하다.

### 1.3 문제 C — 맨 아래로 빠르게 이동하는 수단 부재

위로 스크롤해 읽다가 다시 최신 응답(맨 아래)으로 돌아가려면 수동으로 끝까지 스크롤해야 한다. 플로팅 "맨 아래로" 버튼이 없다.

### 1.4 같은 패턴이 쓰이는 호출 지점

`scrollToBottom()` 호출처(`app.js`): 524, 555, 701, 727, **816**, 976, **1096**, 1283, 1929.

- **816, 1096**: 토큰 스트리밍 루프 내부(`executeStreamingQuery`, `executeFileStreamingQuery 류`) → **조건부로 바꿔야 하는 핵심 지점**.
- 그 외(524 사용자 메시지 추가, 555/701/727 신규 메시지·스트리밍 컨테이너 생성, 976 finalize, 1283/1929 기타 메시지 추가): **새 메시지 시작 시점**이므로 "강제 스크롤"이 자연스럽다. 단, 사용자가 위로 올려 과거를 읽는 중에 새 응답이 와도 끌어내리지 않으려면 동일한 조건부 정책을 적용하는 편이 일관적이다(§3.4에서 정책 결정).

---

## 2. 목표 동작 (ChatGPT/Claude 류 stick-to-bottom 모델)

1. **맨 아래 고정 상태(stick-to-bottom)** 를 추적한다. 사용자가 맨 아래(또는 임계값 이내)에 있으면 "고정됨", 위로 스크롤하면 "해제됨".
2. 토큰이 도착할 때:
   - 고정됨 → 부드럽지 않게(즉시) 맨 아래로 따라 내려간다.
   - 해제됨 → **스크롤 위치를 건드리지 않는다**(면역). 사용자가 읽던 위치 유지.
3. 사용자가 다시 맨 아래까지 스크롤하면 자동으로 "고정됨"으로 복귀.
4. **플로팅 "맨 아래로" 버튼**: 고정 해제 상태(=맨 아래가 아님)일 때만 표시. 클릭 시 맨 아래로 스크롤하고 고정 상태로 복귀.
5. **신규 내용 알림(결정 Q1·Q3 반영)**: 고정 해제 상태에서 새 토큰/새 응답이 도착하면, 사용자가 위치를 잃지 않도록 스크롤은 건드리지 않되 **버튼에 "새 내용 있음" 강조(점/뱃지)** 를 띄워 새 답변이 왔음을 인지시킨다. 버튼 클릭 또는 맨 아래 복귀 시 강조 해제.

---

## 3. 변경 설계

### 3.1 상태 추적 — `stickToBottom` 플래그와 임계값

`app.js` 상단 상태 변수 영역(현 `isProcessing`, `currentThreadId` 등과 같은 스코프)에 추가:

```js
var stickToBottom = true;          // 맨 아래 고정 여부
var hasNewContent = false;         // 고정 해제 상태에서 미확인 신규 출력 존재 여부(Q3 강조용)
var BOTTOM_THRESHOLD_PX = 24;      // 이 거리 이내면 "맨 아래"로 간주(80→24로 축소)
```

판정 헬퍼:

```js
function isNearBottom() {
    var gap = chatMessages.scrollHeight - chatMessages.scrollTop - chatMessages.clientHeight;
    return gap <= BOTTOM_THRESHOLD_PX;
}
```

### 3.2 스크롤 이벤트로 고정 상태 갱신 + 버튼 토글

`chatMessages`에 scroll 리스너 등록(초기화 블록, 예: `chatMessages` 정의 직후나 DOMContentLoaded 핸들러 내):

```js
chatMessages.addEventListener("scroll", function () {
    stickToBottom = isNearBottom();
    if (stickToBottom) hasNewContent = false;   // 맨 아래 복귀 → 신규 강조 해제
    updateScrollToBottomBtn();
}, { passive: true });
```

> 주의: 프로그램이 `scrollTop`을 설정해도 scroll 이벤트가 발생한다. `isNearBottom()` 기반으로 `stickToBottom`을 재계산하므로 프로그램/사용자 스크롤을 구분할 필요가 없다(자기 일관적). 사용자 휠로 위로 올리면 gap이 커져 자동 해제됨.

### 3.3 조건부 스크롤 함수 분리

기존 `scrollToBottom()`은 "무조건"이므로, 의미를 나눈다.

```js
// 무조건 맨 아래로 (새 메시지 시작 등 명시적 의도)
function scrollToBottom(smooth) {
    requestAnimationFrame(function () {
        if (smooth) {
            chatMessages.scrollTo({ top: chatMessages.scrollHeight, behavior: "smooth" });
        } else {
            chatMessages.scrollTop = chatMessages.scrollHeight;
        }
        stickToBottom = true;
        hasNewContent = false;       // 맨 아래로 강제 이동 → 신규 강조 해제
        updateScrollToBottomBtn();
    });
}

// 고정 상태일 때만 따라 내려감 (토큰 스트리밍 / 에이전트 출력 전용)
function scrollToBottomIfSticky() {
    if (!stickToBottom) {
        hasNewContent = true;        // 미확인 신규 출력 → 버튼 강조 대상(Q3)
        updateScrollToBottomBtn();   // 버튼 노출/강조 갱신
        return;
    }
    requestAnimationFrame(function () {
        chatMessages.scrollTop = chatMessages.scrollHeight;  // 즉시(비smooth)
    });
}
```

> `scrollToBottom(smooth)`(무조건판)와 scroll 리스너의 "맨 아래 복귀" 시에는 `hasNewContent = false`로 초기화한다(아래 §3.2·§3.6 반영).

### 3.4 호출 지점 교체 (결정 Q1 = (B) 확정)

사용자 결정: **에이전트 응답 추종은 조건부(B)** — 사용자가 과거를 읽는 중 새 응답이 도착해도 끌어내리지 않고, 대신 §3.6 신규 내용 강조로 인지시킨다. 단 **사용자 본인이 방금 보낸 질의 직후(524)** 는 직관상 무조건 내려간다.

| 위치(line) | 의미 | 변경 |
|---|---|---|
| 524 | 사용자 본인 질의 메시지 추가 | **`scrollToBottom()` 유지(무조건)** |
| 816, 1096 | 토큰 스트리밍 루프 | → **`scrollToBottomIfSticky()`** |
| 555, 701, 727 | 신규 에이전트 메시지·스트리밍 컨테이너 생성 | → **`scrollToBottomIfSticky()`** |
| 976 | finalize | → **`scrollToBottomIfSticky()`** |
| 1283, 1929 | 기타 에이전트/시스템 메시지 추가 | → **`scrollToBottomIfSticky()`** |

> 즉 사용자가 보낸 질의(524)만 무조건, 나머지 에이전트 측 출력은 전부 조건부. 고정 해제 상태에서 이들이 호출되면 위치는 그대로 두고 §3.6 강조만 켜진다.

### 3.5 `scroll-behavior: smooth` 제거(스트리밍 구간)

`style.css:226`의 `.chat-messages { scroll-behavior: smooth; }`는 토큰마다 애니메이션을 재트리거해 튐을 악화시킨다. 토큰 추종은 §3.3에서 비-smooth `scrollTop` 직접 대입으로 처리하므로, **전역 `scroll-behavior: smooth`를 제거**한다. "맨 아래로" 버튼 클릭의 부드러운 이동은 §3.3 `scrollToBottom(true)`의 `scrollTo({behavior:"smooth"})`로 국소 적용한다.

### 3.6 플로팅 "맨 아래로" 버튼

**HTML** (`index.html`) — `.chat-main` 내부, `.chat-messages` 형제로 추가(스크롤 컨테이너 밖, 입력 바 위에 오버레이):

```html
<button class="scroll-to-bottom-btn" id="scrollToBottomBtn" type="button"
        aria-label="맨 아래로 이동">
    <svg viewBox="0 0 24 24" width="20" height="20">
        <polyline points="6 9 12 15 18 9"></polyline>
    </svg>
    <!-- 신규 내용 강조 점(Q3): .has-new 클래스일 때만 보임 -->
    <span class="scroll-to-bottom-dot" aria-hidden="true"></span>
</button>
```

**CSS** (`style.css`) — `.chat-main`이 `position` 컨텍스트를 갖도록 하고(현재 `display:flex`만 있음 → `position: relative` 추가), 버튼을 입력 바 위쪽에 띄운다:

```css
.chat-main { position: relative; }   /* 기존 규칙에 추가 */

.scroll-to-bottom-btn {
    position: absolute;
    bottom: 120px;           /* 입력 바 높이 위. 실제 .input-bar 높이에 맞춰 조정 */
    right: 24px;             /* 우측 하단 정렬 */
    width: 40px; height: 40px;
    border-radius: 50%;
    background: var(--bg-elevated, #2a2a2a);
    border: 1px solid var(--border);
    box-shadow: 0 2px 8px rgba(0,0,0,0.3);
    display: flex; align-items: center; justify-content: center;
    cursor: pointer;
    opacity: 0; pointer-events: none;
    transition: opacity 0.15s ease;
    z-index: 20;
}
.scroll-to-bottom-btn.is-visible { opacity: 1; pointer-events: auto; }
.scroll-to-bottom-btn:hover { background: var(--bg-hover, #333); }
.scroll-to-bottom-btn svg { stroke: var(--text-secondary); fill: none; stroke-width: 2; }

/* 신규 내용 강조(Q3): 점 + 테두리 강조 */
.scroll-to-bottom-dot {
    position: absolute;
    top: -2px; right: -2px;
    width: 10px; height: 10px;
    border-radius: 50%;
    background: var(--accent, #4a9eff);
    border: 2px solid var(--bg-elevated, #2a2a2a);
    display: none;
}
.scroll-to-bottom-btn.has-new .scroll-to-bottom-dot { display: block; }
.scroll-to-bottom-btn.has-new { border-color: var(--accent, #4a9eff); }
```

> `hidden` 속성 대신 `.is-visible` 클래스로 토글(트랜지션 위함). 초기 `hidden`은 제거하고 JS가 클래스로 제어. 색상 변수는 `style.css`에 정의된 실제 토큰명으로 맞출 것(§5 검증).

**JS** (`app.js`):

```js
var scrollToBottomBtn = document.getElementById("scrollToBottomBtn");

function updateScrollToBottomBtn() {
    if (!scrollToBottomBtn) return;
    var show = !isNearBottom();
    scrollToBottomBtn.classList.toggle("is-visible", show);
    // 버튼이 보이고 미확인 신규 출력이 있을 때만 강조(Q3)
    scrollToBottomBtn.classList.toggle("has-new", show && hasNewContent);
}

if (scrollToBottomBtn) {
    scrollToBottomBtn.addEventListener("click", function () {
        scrollToBottom(true);   // smooth 이동 + stickToBottom=true 복귀
    });
}
```

리사이즈/콘텐츠 변경에도 버튼 상태가 어긋나지 않도록, finalize와 신규 메시지 추가 이후에도 `updateScrollToBottomBtn()`를 호출(또는 `scrollToBottom*` 내부에서 이미 호출하므로 충분한지 §5에서 확인).

### 3.7 우측 진행 패널(progress-panel)은 범위 외

`app.js:1367` `progressPipeline.scrollTop = ...`은 별도 패널이며 이번 UX 대상(채팅 응답)과 무관하므로 변경하지 않는다.

---

## 4. 엣지 케이스

| # | 상황 | 처리 |
|---|------|------|
| E1 | 응답이 화면보다 짧아 스크롤이 없음 | `isNearBottom()`이 항상 true → 버튼 숨김, 정상 |
| E2 | 스트리밍 시작 시점에 사용자가 이미 위로 올려둔 상태 | 결정(B): `createStreamingMessage()`/finalize 등 에이전트 측은 `scrollToBottomIfSticky()`라 끌어내리지 않고 `has-new` 강조만 켜짐. 위치 유지 |
| E3 | 토큰 도중 사용자가 위로 스크롤 | scroll 이벤트 → `stickToBottom=false` → 이후 토큰은 위치 불변(면역). 버튼 노출 + 신규 토큰 도착 시 `has-new` 강조 |
| E4 | 사용자가 다시 맨 아래로 휠 | gap≤임계값 → `stickToBottom=true` 자동 복귀, `hasNewContent=false`, 버튼/강조 숨김 |
| E5 | 마크다운 재렌더로 높이 급변 | 비-smooth 직접 대입이므로 즉시 보정. 고정 해제 시엔 건드리지 않음 |
| E6 | 모바일/반응형(`style.css:2806` `.chat-messages` 미디어쿼리) | 버튼 `bottom` 값이 입력 바와 겹치지 않게 미디어쿼리에서 조정 |

---

## 5. 검증 계획

1. **수동 시나리오** (실제 스트리밍 질의):
   - (a) 긴 응답 스트리밍 중 가만히 둔다 → 부드럽게(튐 없이) 따라 내려가는지.
   - (b) 스트리밍 중 위로 스크롤 → 토큰이 와도 위치 고정(면역)되는지, 플로팅 버튼이 뜨고 **신규 토큰 도착 시 `has-new` 점 강조**가 켜지는지.
   - (c) 버튼 클릭 → 맨 아래로 부드럽게 이동, 버튼/강조 사라지고 다시 추종 시작.
   - (d) 짧은 응답 → 버튼 안 뜸.
   - (e) 위로 올려둔 상태에서 **새 답변 턴 시작** → 끌려 내려가지 않고 `has-new` 강조로 새 답변 도착을 인지(결정 B+Q3).
2. **CSS 변수 실측**: `style.css`에 실제로 정의된 색상 토큰명(`--bg-elevated`, `--text-secondary`, `--border`, `--bg-hover` 등)을 grep으로 확인 후 일치시킨다(없는 변수 사용 시 버튼이 투명/검정으로 보일 수 있음).
3. **입력 바 높이 측정**: `.input-bar` 실제 높이를 확인해 버튼 `bottom` 오프셋(96px 가정)을 맞춘다.
4. **Playwright**(`plans/24-ui-playwright-test-plan.md` 흐름 참고, 선택): 스크롤 컨테이너 `scrollTop`을 조작해 (b)/(c) 상태 전이와 버튼 `is-visible` 토글을 자동 검증.
5. **회귀**: 새 메시지 전송 시 맨 아래로 정상 이동, finalize 후 다운로드 버튼/메타 표시 위치 정상.

---

## 6. 결정 사항 (사용자 확정 완료)

- **Q1 — 신규 메시지 도착 시 정책 → (B) 확정**: 에이전트 응답 추종은 **조건부**. 사용자가 과거를 읽는 중 새 응답이 시작돼도 끌어내리지 않고 면역 유지. 단 사용자 본인 질의 직후(524)만 무조건 이동. 새 답변 도착 사실은 **Q3 강조로 인지**시킨다. → §3.4 반영 완료.
- **Q2 — 임계값 → 24px 확정**: 초안 80px이 과해 `BOTTOM_THRESHOLD_PX = 24`로 축소. 버튼 위치도 중앙 하단→**우측 하단**으로 변경. → §3.1·§3.6 반영 완료.
- **Q3 — 버튼 강조 → 적용 확정**: 고정 해제 상태에서 신규 토큰/응답이 도착하면 버튼에 점(`has-new`)으로 "새 내용 있음" 표시. → §2(5), §3.3·§3.6 반영 완료.

---

## 7. 구현 체크리스트 — Part 1 (✅ 완료)

- [x] `app.js`: `stickToBottom`/`hasNewContent`/`BOTTOM_THRESHOLD_PX`(=24) 상태 + `isNearBottom()` 추가
- [x] `app.js`: `chatMessages` scroll 리스너로 상태/버튼 갱신(맨 아래 복귀 시 `hasNewContent=false`)
- [x] `app.js`: `scrollToBottom(smooth)` 무조건판(+`hasNewContent=false`) + `scrollToBottomIfSticky()`(고정 해제 시 `hasNewContent=true`) 분리
- [x] `app.js`: 토큰 루프(2개 스트리밍 함수) → `scrollToBottomIfSticky()` 교체
- [x] `app.js`: 에이전트 측 메시지/컨테이너/finalize(processing·streaming 컨테이너·agent·finalize·feedback·alarm) → `scrollToBottomIfSticky()` 교체. 사용자 질의(`renderUserMessage`)만 `scrollToBottom()` 유지 (결정 B)
- [x] `app.js`: `scrollToBottomBtn` 참조 + `updateScrollToBottomBtn()`(`is-visible`+`has-new` 토글) + 클릭 핸들러
- [x] `index.html`: 플로팅 버튼 마크업 추가(`.chat-main` 내, `.chat-messages` 형제) + 신규 강조 점(`.scroll-to-bottom-dot`)
- [x] `style.css`: `.chat-main { position: relative }`, `.scroll-to-bottom-btn`(+`.is-visible`,`.has-new`,`.scroll-to-bottom-dot`), `scroll-behavior: smooth` 제거, 반응형 `bottom` 조정
- [ ] 수동(시나리오 a~e) + (선택)Playwright 검증 — 실행 환경에서 확인 필요
- [x] `docs/02_decision.md`에 **D-044** 기록 — 결정 (B)+신규 강조 포함

---

# Part 2 — 스트리밍 표 가로 스크롤 초기화 해결 (B-1: DOM 모핑)

## 8. 배경 — 원인 확정

스트리밍 토큰 핸들러(두 SSE 루프, `app.js`)는 토큰마다 다음을 수행한다:

```js
if (event.type === "token") {
    accumulatedText += event.content;
    var textEl = document.getElementById("streamingText");
    if (textEl) textEl.innerHTML = renderMarkdown(accumulatedText);  // ← 전체 재파싱+DOM 재생성
    scrollToBottomIfSticky();
}
```

- `renderMarkdown`은 `marked.parse(accumulatedText)`로 **누적 텍스트 전체를 매 토큰 재파싱**하고, `innerHTML` 대입이 `#streamingText`의 **모든 자식 DOM을 파괴 후 재생성**한다.
- 표는 `style.css`에서 `.response-text table { display:block; overflow-x:auto }` — **테이블 요소 자체가 가로 스크롤 컨테이너**다. `scrollLeft`은 어트리뷰트가 아니라 라이브 요소의 런타임 프로퍼티이므로, 매 토큰 새 테이블이 생성되면 `scrollLeft=0`으로 초기화된다 → **사용자가 우측으로 스크롤한 표가 다음 토큰에 좌측으로 리셋**.
- 같은 원인으로 **스트리밍 중 텍스트 드래그 선택도 끊긴다**. 수직 스크롤이 멀쩡한 이유는 `scrollTop`을 Part 1에서 의도적으로 복원하기 때문(가로는 복원 안 함).
- **스트리밍 구간 한정** 문제다. `finalizeStreamingMessage`는 마크다운을 1회만 렌더해 이후 DOM이 고정되므로 응답 완료 후엔 정상.

### 비용 모델(성능 검토 요약)

응답 토큰 수 T, 최종 길이 L(∝T)일 때:

| 항목 | 현행 | 비고 |
|------|------|------|
| 파싱 | 매 토큰 전체 파싱 → **O(L²)** | 후반에 큰 표가 있으면 매 토큰 그 표 전체 재파싱 |
| DOM | 매 토큰 서브트리 전체 파괴/생성 | reflow·repaint·GC 폭주, 스크롤·선택 손실의 직접 원인 |
| 재렌더 대상 | **현재 스트리밍 메시지 1개**뿐 | 확정된 과거 메시지는 정적 → "대화 길이" 자체는 렌더 비용에 직접 영향 없음 |

> 별개 축: 확정 메시지를 DOM에서 제거하지 않으므로(가상화 없음) 턴이 누적되면 총 노드 수가 증가한다. B-1 범위 밖 — 진짜 "긴 대화" 최적화는 메시지 가상화로 별도 처리.

## 9. 설계 — B-1 (DOM 모핑) + (권장) rAF 렌더 코얼레싱

### 9.1 핵심: 비파괴 렌더 헬퍼 `renderStreamingMarkdown`

`innerHTML` 전체 교체 대신, full `marked.parse` 결과를 **기존 DOM에 diff 적용(morph)** 한다. 변하지 않은 노드(앞 표·그 `scrollLeft`·선택 영역)는 **요소 인스턴스가 재사용**되어 보존된다. 출력 HTML은 full-parse와 **동일**하므로 출력 정확성 영향이 없다.

- **구현 채택(변경)**: 폐쇄망이라 morphdom CDN 취득 불가 + 라이브러리 재현 정확성 리스크 → **외부 의존성 없는 자체 morph 구현**(`morphChildren`+`syncAttributes`)으로 완료. 동작은 morphdom과 동일(인덱스+nodeName 기준 노드 재사용, `isEqualNode`로 무변경 서브트리 스킵). `index.html` 스크립트 추가 없음.
- **폴백**: morph 중 예외 발생 시 **방안 A(가로 스크롤 위치만 스냅샷·복원하며 전체 교체)** 로 자동 강등 → 최소 보장.

실제 구현(자체 morph, `app.js` 발췌):

```js
// 두 부모의 자식들을 인덱스 기준으로 reconcile(기존 노드 재사용 → scrollLeft/선택 보존)
function morphChildren(fromParent, toParent) {
    var toNodes = toParent.childNodes;
    var i = 0;
    while (i < toNodes.length) {
        var toNode = toNodes[i], fromNode = fromParent.childNodes[i];
        if (!fromNode) {
            fromParent.appendChild(toNode.cloneNode(true));
        } else if (fromNode.nodeType !== toNode.nodeType ||
                   (fromNode.nodeType === 1 && fromNode.nodeName !== toNode.nodeName)) {
            fromParent.replaceChild(toNode.cloneNode(true), fromNode);
        } else if (fromNode.nodeType === 3 || fromNode.nodeType === 8) {
            if (fromNode.nodeValue !== toNode.nodeValue) fromNode.nodeValue = toNode.nodeValue;
        } else if (fromNode.nodeType === 1 && !fromNode.isEqualNode(toNode)) {
            syncAttributes(fromNode, toNode);     // 어트리뷰트 동기화
            morphChildren(fromNode, toNode);      // 재귀
        }
        i++;                                       // isEqualNode==true면 기존 노드 그대로 유지
    }
    while (fromParent.childNodes.length > toNodes.length) fromParent.removeChild(fromParent.lastChild);
}

function renderStreamingMarkdown(el, md) {
    var html = renderMarkdown(md);
    try {
        var tpl = document.createElement("div"); tpl.innerHTML = html;
        morphChildren(el, tpl);
    } catch (_e) {
        // 폴백(방안 A): 표 가로 스크롤 위치만 보존하며 전체 교체
        var prev = el.querySelectorAll("table"), sc = [];
        for (var i = 0; i < prev.length; i++) sc[i] = prev[i].scrollLeft;
        el.innerHTML = html;
        var next = el.querySelectorAll("table");
        for (var j = 0; j < next.length && j < sc.length; j++) if (sc[j]) next[j].scrollLeft = sc[j];
    }
}
```

**왜 보존되는가**: morph는 자식을 (nodeName+위치) 기준으로 매칭해 **기존 요소를 재사용**하고 차이만 패치한다. 스트리밍 마크다운은 "앞 블록 고정 + 마지막 블록에 행/텍스트 추가"의 append 우세 패턴이라 prefix 매칭이 안정적이다. 성장 중인 마지막 표는 `<table>` 인스턴스 유지 + `<tr>`만 in-place 추가 → `scrollLeft`(라이브 프로퍼티) 보존. 단위 검증으로 표 2→3행 성장 시 `<table>` 인스턴스 재사용·`scrollLeft=120` 보존 확인.

### 9.2 토큰 루프 교체(두 스트리밍 함수)

각 루프의 `textEl.innerHTML = renderMarkdown(accumulatedText);` 한 줄을 **`renderStreamingMarkdown(textEl, accumulatedText);`** 로 교체한다(2곳). 이것만으로 표 가로 스크롤·선택 끊김이 해결된다(파싱 O(L²)는 그대로).

### 9.3 (권장) rAF 렌더 코얼레싱 — 파싱 O(L²) 상수 절감

토큰 버스트를 **프레임당 1회 렌더로 묶어** 재파싱 횟수를 토큰 수가 아니라 프레임 수(≤60/s)로 제한한다. 정확성 위험 없이 후반 끊김을 크게 완화한다.

두 루프가 각자 지역 `accumulatedText`를 쓰므로, 모듈 레벨 공유 변수와 스케줄러를 둔다:

```js
// 모듈 스코프
var _streamAccumulated = "";
var _streamRafQueued = false;

function scheduleStreamingRender() {
    if (_streamRafQueued) return;      // 이미 이번 프레임 렌더 예약됨 → 코얼레싱
    _streamRafQueued = true;
    requestAnimationFrame(function () {
        _streamRafQueued = false;
        var el = document.getElementById("streamingText");
        if (el) renderStreamingMarkdown(el, _streamAccumulated);
        scrollToBottomIfSticky();      // 렌더 후 높이 갱신된 상태에서 추종
    });
}
```

토큰 핸들러:

```js
if (event.type === "token") {
    accumulatedText += event.content;
    _streamAccumulated = accumulatedText;
    scheduleStreamingRender();          // 렌더+스크롤은 rAF에서 1회로 코얼레싱
}
```

> 주의: rAF 코얼레싱 도입 시 토큰 핸들러에서 `scrollToBottomIfSticky()` 직접 호출은 제거하고 스케줄러 rAF 내부로 옮긴다(렌더 후 스크롤). `done`/`error` 시에는 `finalizeStreamingMessage`가 `finalText`로 최종 1회 정식 렌더하므로 잔여 토큰 손실 없음(필요 시 finalize 직전 강제 flush 1회).

## 10. 엣지 케이스 (Part 2)

| # | 상황 | 처리 |
|---|------|------|
| P1 | `morphdom` 미로드(번들 누락/로드 실패) | 폴백 A로 자동 강등 — 가로 스크롤만이라도 보존 |
| P2 | 미완 블록 타입 전환(`p` → `table`: 구분행 `\|---\|` 도착 시) | 해당 마지막 노드만 교체. 아직 표가 아니라 잃을 스크롤 상태 없음. 이후 행 추가는 in-place |
| P3 | 코드펜스/리스트 미완 상태 | morph가 마지막 블록만 패치 → 앞 블록 영향 없음. 출력은 full-parse와 동일 |
| P4 | 출력 정확성 | morph 입력 = `marked.parse` 전체 결과 → **full 렌더와 동일 HTML**. 블록 증분 파싱(B-2)과 달리 맥락 오판 위험 없음 |
| P5 | 표 행 추가로 가로 폭/스크롤 범위 증가 | `scrollLeft`은 유지, 범위 초과분은 브라우저가 클램프 |
| P6 | 보안(XSS) | 현행과 동일하게 서버 생성 텍스트의 marked 출력을 신뢰. morph는 파싱 결과를 그대로 반영 → 보안 표면 변화 없음 |
| P7 | finalize 시점 | 스트리밍 중에만 morph 사용. 최종은 기존 `finalizeStreamingMessage`의 1회 렌더 유지(회귀 없음) |

## 11. 검증 계획 (Part 2)

1. **표 가로 스크롤 유지(핵심)**: 큰 표가 포함된 응답 스트리밍 중 표를 우측으로 스크롤 → 토큰이 계속 와도 위치 유지.
2. **텍스트 선택 유지(부수 효과)**: 스트리밍 중 본문 드래그 선택이 다음 토큰에 풀리지 않는지.
3. **출력 동등성**: 스트리밍 누적(morph) 결과 DOM과 `finalizeStreamingMessage`(full 렌더) 결과가 동일 구조인지(표/코드/리스트/링크 포함). diff 시 morph 매칭 키 점검.
4. **폴백 동작**: `morphdom`을 일시 제거(또는 전역 차단)하고 폴백 A로 가로 스크롤만이라도 보존되는지.
5. **성능**: 수백 행 표 응답에서 (a) 현행, (b) B-1, (c) B-1+rAF 코얼레싱의 후반 토큰 프레임타임/끊김 체감 비교(DevTools Performance). rAF on/off 차이 확인.
6. **회귀**: Part 1(stick-to-bottom·플로팅 버튼·신규 강조)이 morph 도입 후에도 정상 동작.

## 12. 구현 체크리스트 — Part 2 (✅ 완료)

- [x] ~~morphdom 로컬 벤더~~ → **자체 morph 구현**으로 대체(폐쇄망, 외부 의존성 0). `index.html` 변경 없음
- [x] `app.js`: `morphChildren`/`syncAttributes`(비파괴 DOM 모프) + `renderStreamingMarkdown(el, md)`(morph + 폴백 A) 추가
- [x] `app.js`: 두 스트리밍 토큰 루프의 `textEl.innerHTML = renderMarkdown(...)` → `scheduleStreamingRender()` 교체
- [x] `app.js`: rAF 렌더 코얼레싱(`_streamAccumulated`/`_streamRafQueued`/`scheduleStreamingRender`) 도입 + `scrollToBottomIfSticky()`를 스케줄러 rAF로 이동. `createStreamingMessage`에서 누적 초기화
- [x] 단위 검증: `morphChildren` 표 2→3행 성장 시 인스턴스 재사용·`scrollLeft` 보존 PASS, `node --check` 통과
- [ ] 수동(브라우저) 검증(§11) — 표 가로 스크롤 유지·선택 유지·출력 동등·폴백·성능·Part 1 회귀 — 실행 환경에서 확인 필요
- [x] `docs/02_decision.md`에 **D-045** 기록(변경 이력 표 grep으로 빈 번호 확정)

## 13. 후속(범위 밖, 참고)

- **B-2(블록 단위 증분 파싱)**: 완결 블록은 재파싱하지 않아 파싱을 O(L)에 근접시킴. 단 맥락 의존 마크다운에서 블록 경계 오판 시 **출력 정확성 회귀 위험** → 실사용 응답 크기 분포로 정당화된 뒤 별도 검토.
- **메시지 가상화/윈도잉**: 긴 대화에서 확정 메시지 DOM 누적으로 인한 메모리·레이아웃 비용 해소. 별도 계획.
