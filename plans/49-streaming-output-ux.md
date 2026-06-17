# Plan 49: 스트리밍 출력 UX 개선 — 스크롤 자유화 + "맨 아래로" 플로팅 버튼

작성일: 2026-06-17
상태: **핵심(§3) + §6.1(렌더 최적화) + §6.2(Stop 버튼) 구현 완료 (2026-06-17, D-041)**. §6.3~6.8은 백로그.
관련: Plan 48 §10(스트리밍 토큰 출력 배선), `src/static/js/app.js`, `src/static/css/style.css`, `src/static/index.html`, `docs/02_decision.md` D-041

---

## 1. 목표 / 배경

AI 응답이 SSE 스트리밍으로 한 토큰씩 출력되는 동안, 채팅 영역이 **토큰마다 강제로 맨 아래로 스크롤**된다. 그 결과:

- 사용자가 출력 중 위로 스크롤해 이전 내용을 확인하려 해도 즉시 맨 아래로 끌려감("화면이 새로고침되는 것 같다"는 체감).
- 긴 응답일수록 읽기가 불가능 — 스트리밍이 끝날 때까지 기다려야만 위 내용을 볼 수 있음.

**개선 목표** (타 AI 어시스턴트 웹 UI의 표준 동작 차용):

1. **스트리밍 중에도 사용자가 자유롭게 스크롤**할 수 있어야 한다. 사용자가 위로 스크롤하면 자동 스크롤을 멈춘다("스크롤 잠금 해제").
2. 사용자가 다시 맨 아래 근처로 오면 자동 스크롤을 재개한다("스크롤 따라가기").
3. 현재 위치가 맨 아래가 아닐 때 **"맨 아래로 이동" 플로팅 버튼**을 노출하고, 클릭 시 맨 아래로 부드럽게 이동하며 자동 따라가기를 재개한다. 스트리밍 중 새 내용이 쌓이면 버튼에 "새 응답" 신호를 준다.

---

## 2. 현재 동작 (코드 근거)

| 위치 | 현재 코드 | 문제 |
|------|----------|------|
| `app.js` token 핸들러(현 ~772) | `if (textEl) textEl.innerHTML = renderMarkdown(...); scrollToBottom();` | **토큰마다 무조건** 맨 아래로 강제 이동 |
| `app.js` `scrollToBottom()`(현 ~1149) | `requestAnimationFrame(() => chatMessages.scrollTop = chatMessages.scrollHeight)` | 사용자 스크롤 의도와 무관하게 항상 바닥 고정 |
| 메시지 append 다수(현 487/518/664/689/923/1038/1223/1857) | append 후 `scrollToBottom()` | 일부는 정당(사용자 전송 직후), 일부는 스트리밍과 충돌 |
| `style.css` `.chat-main`(현 210) | `position` 미지정, `overflow:hidden` | 플로팅 버튼 앵커(position 컨텍스트) 부재 |
| `style.css` `.chat-messages`(현 219) | `overflow-y:auto; scroll-behavior:smooth` | 스크롤 컨테이너 — 여기 기준으로 바닥 판정 |
| `index.html`(현 36) | `<div class="chat-messages" id="chatMessages">` 하단에 버튼 없음 | 플로팅 버튼 마크업 없음 |

핵심: **자동 스크롤이 "조건 없이" 실행**되는 것이 유일한 근본 원인. 사용자 스크롤 의도를 추적해 게이팅하면 해결된다.

---

## 3. 설계

### 3.1 "바닥 고정(stick-to-bottom)" 상태 추적

스크롤 컨테이너(`#chatMessages`)의 바닥과의 거리로 자동 스크롤 여부를 결정한다. **별도 플래그 동기화 없이** 스크롤 이벤트에서 매번 재계산하는 방식이 가장 견고하다(프로그램적 스크롤도 자연히 흡수됨).

```
distanceFromBottom = scrollHeight - scrollTop - clientHeight
STICK_THRESHOLD_PX = 80         // 이 이내면 "바닥에 붙어있음"으로 간주
autoStick = distanceFromBottom <= STICK_THRESHOLD_PX
```

- `scroll` 이벤트(throttle/rAF)에서 `autoStick` 갱신 + 플로팅 버튼 표시/숨김 갱신.
- **프로그램적 `scrollToBottom()`** → 바닥 도달 → 다음 scroll 이벤트에서 `distanceFromBottom≈0` → `autoStick=true` 유지(플래그 꼬임 없음).
- **사용자가 위로 스크롤** → `distanceFromBottom` 증가 → `autoStick=false` → 자동 스크롤 중단 + 버튼 노출.
- **사용자가 다시 바닥 근처로** → `autoStick=true` → 버튼 숨김, 다음 토큰부터 자동 따라가기 재개.

### 3.2 자동 스크롤 게이팅

`scrollToBottom()`을 두 가지로 분리한다:

- `scrollToBottom(opts)` — 즉시/부드럽게 강제 이동(버튼 클릭·사용자 메시지 전송 등 **의도적 이동**에 사용). 이동 후 `autoStick=true`.
- `autoScrollIfStuck()` — `autoStick`일 때만 바닥으로 이동(스트리밍 토큰·노드 진행·자동 append에 사용).

| 호출 지점 | 변경 |
|-----------|------|
| 토큰 핸들러(스트리밍) | `scrollToBottom()` → **`autoScrollIfStuck()`** |
| 노드 진행/프로세스 표 등 스트리밍 중 append | `autoScrollIfStuck()` |
| **사용자 메시지 전송 직후** | `scrollToBottom()` 유지 + `autoStick=true` 강제(전송 시 바닥으로 가는 건 기대 동작) |
| 스트리밍 시작 시 처리중 메시지 렌더 | `scrollToBottom()` 유지(전송 흐름의 일부) |
| 히스토리/초기 로드 append | 초기 1회 `scrollToBottom()` 후 사용자 스크롤 존중 |

토큰 렌더 시 `scroll-behavior: smooth`가 매 토큰마다 애니메이션을 유발해 끊겨 보일 수 있으므로, **자동 따라가기는 `behavior:'auto'`(즉시)**, 버튼 클릭 등 의도적 이동만 `smooth`로 한다.

### 3.3 "맨 아래로" 플로팅 버튼

- 마크업: `.chat-main` 내부에 `#scrollToBottomBtn`(absolute) 추가 — 입력 바(`.input-bar`) 바로 위, 우측.
- 표시 조건: `autoStick === false`(바닥에서 떨어져 있음). 표시/숨김은 `.is-visible` 토글 + opacity/transform 트랜지션.
- 클릭: `scrollToBottom({behavior:'smooth'})` → `autoStick=true` → 버튼 숨김.
- **새 응답 신호**: `autoStick=false`인 동안 스트리밍 토큰이 도착하면 버튼에 `.has-new` 부여(점/라벨 "새 응답 ↓"). 바닥 복귀 시 해제.
- 접근성: `aria-label="맨 아래로 이동"`, 키보드 포커스 가능, `prefers-reduced-motion` 시 `behavior:'auto'`.

```
.chat-main { position: relative; }              /* 앵커 컨텍스트 */
#scrollToBottomBtn {
    position: absolute; right: 24px;
    bottom: calc(<input-bar 높이> + 16px);
    width: 40px; height: 40px; border-radius: 50%;
    display: grid; place-items: center;
    opacity: 0; transform: translateY(8px); pointer-events: none;
    transition: opacity .15s, transform .15s;
    /* 색·그림자는 기존 토큰(var(--...)) 재사용 */
}
#scrollToBottomBtn.is-visible { opacity: 1; transform: none; pointer-events: auto; }
#scrollToBottomBtn.has-new::after { /* 작은 배지/점 */ }
@media (prefers-reduced-motion: reduce) { .chat-messages { scroll-behavior: auto; } }
```

### 3.4 동작 시나리오

1. 사용자 전송 → 바닥으로 이동(autoStick=true) → 응답 스트리밍이 바닥을 자연스럽게 따라감.
2. 사용자가 출력 중 위로 스크롤 → autoStick=false → 자동 스크롤 멈춤(읽기 가능) → 플로팅 버튼 노출, 토큰 도착 시 "새 응답" 신호.
3. 버튼 클릭(또는 직접 바닥까지 스크롤) → 바닥 이동 + autoStick=true → 다시 따라가기.

---

## 4. 변경 파일 (구현 완료 ✅)

| 파일 | 변경 | 상태 |
|------|------|------|
| `src/static/index.html` | `.chat-main` 내부 `#scrollToBottomBtn`(↓ 아이콘+배지, aria-label) 마크업, 전송 버튼에 정지 아이콘(`.icon-stop`) 추가 | ✅ |
| `src/static/css/style.css` | `.chat-main{position:relative}`, `.chat-messages` 전역 `scroll-behavior` 제거(JS 제어), `.scroll-to-bottom-btn`/`.is-visible`/`.has-new`/배지 + 반응형·reduced-motion, `.input-btn--send.is-stop` 아이콘 토글 | ✅ |
| `src/static/js/app.js` | (1) `autoStick` 상태 + `#chatMessages` scroll 리스너(rAF throttle)로 `isNearBottom()` 판정·버튼 토글 (2) `scrollToBottom(opts)`(의도적·smooth)/`autoScrollIfStuck()`(바닥 고정 시만) 분리 (3) 스트리밍 토큰을 `scheduleStreamRender()`(rAF 배칭·선택 보존)로 교체, finalize는 `autoScrollIfStuck()` (4) 플로팅 버튼 클릭·"새 응답" 배지 (5) `AbortController`+전송/중지 버튼 모드 전환·중지 시 부분 응답 확정 | ✅ |
| `tests/e2e/test_basic_ui.py` | B-07을 "전송 후 Stop 모드 전환"으로 갱신(비활성화→is-stop) | ✅ |

> 서버/그래프/도메인 변경 없음 — **프런트엔드(정적 자원) 한정** + e2e 테스트 1건 갱신.

---

## 5. 검증 체크리스트

코드/구조 검증은 완료(✅), 실제 브라우저 상호작용 항목은 e2e/수동 확인 필요(▶).

- [x] (구현) 토큰마다 무조건 `scrollToBottom()`을 제거하고 `autoStick`일 때만 따라가도록 게이팅
- [x] (구현) 플로팅 버튼 마크업/스타일/표시 토글, "새 응답" 배지(`has-new`), 버튼 클릭 시 smooth 이동
- [x] (구현) 전송 직후 `scrollToBottom()`으로 바닥 이동+`autoStick=true`(전송 시 강제 이동 유지)
- [x] (구현) finalize는 `autoScrollIfStuck()` — 사용자가 위로 스크롤 중이면 바닥으로 끌어내리지 않음
- [x] (구현) 진행 패널(우측 `progressPipeline`)은 미변경 — 독립 스크롤 유지
- [x] (구현) `node --check` 문법 통과, e2e B-07 계약 갱신
- [ ] ▶ 긴 응답 스트리밍 중 위로 스크롤하면 끌려가지 않고 읽을 수 있다(브라우저 확인)
- [ ] ▶ 위로 스크롤 시 플로팅 버튼 노출 + 토큰 도착 시 "새 응답" 배지, 클릭 시 바닥 복귀·따라가기 재개
- [ ] ▶ 바닥 근처로 직접 스크롤하면 버튼이 사라지고 따라가기가 재개
- [ ] ▶ `prefers-reduced-motion`/모바일 폭에서 버튼이 입력 바와 겹치지 않고 애니메이션 최소화
- [ ] ▶ 텍스트 선택(드래그)이 스트리밍 중 유지(§6.1: 선택 중 프레임 렌더 보류 → 완료 시 1회 강제 렌더)
- [ ] ▶ 스트리밍 중 "중지" 클릭 시 스트림이 취소되고 부분 응답이 그대로 확정(§6.2)

---

## 6. 추가 제안 (UX 개선 백로그)

본 계획의 스크롤 개선과 함께 검토할 만한 항목. 우선순위 표기.

### 6.1 [높음] ✅ 완료 — 스트리밍 토큰 렌더 최적화 (텍스트 선택 끊김·깜빡임 해소)
현재 토큰마다 `textEl.innerHTML = renderMarkdown(전체 누적 텍스트)`로 **매번 전체 DOM을 재생성**한다. 부작용:
- 사용자가 응답 텍스트를 드래그 선택하는 중에도 선택이 풀린다(재생성 때문).
- 누적 길이에 비례해 매 토큰 렌더 비용 증가(긴 응답에서 체감 지연·깜빡임).

**제안**: (a) 렌더를 `requestAnimationFrame`으로 **배칭**(토큰마다가 아니라 프레임당 1회 렌더), (b) 스트리밍 중에는 평문/경량 렌더로 누적하고 **완료 시 1회 마크다운 렌더**, 또는 (c) 증분 마크다운 렌더러 도입. 최소안은 (a)+(b).

**구현(✅)**: `scheduleStreamRender(text)`가 누적 텍스트를 보관하고 `requestAnimationFrame`으로 **프레임당 1회** `renderMarkdown`을 수행(토큰당 재렌더 제거 → O(n²)·깜빡임 해소). 렌더 직전 `hasSelectionInside(#streamingText)`로 **사용자가 응답 텍스트를 선택(드래그) 중이면 그 프레임 렌더를 보류**하여 선택이 풀리지 않게 한다. 스트림 종료 시 `flushFinalStreamRender()`가 보류분을 강제로 1회 렌더해 최종 텍스트를 보장한다.

### 6.2 [높음] ✅ 완료 — "응답 중지(Stop)" 버튼
스트리밍 중 전송 버튼을 중지 버튼으로 전환하고, `AbortController`로 fetch/SSE 스트림을 취소한다. 긴/원치 않는 응답을 끊을 수 있어 타 AI UI의 표준 기능.

**구현(✅)**: 전송 버튼 클릭 핸들러를 "처리 중이면 `stopStreaming()`, 아니면 `handleSend()`"로 분기. `executeStreamingQuery`/`executeFileQuery`가 `AbortController`를 만들어 `fetch(..., {signal})`에 전달하고, 처리 중에는 버튼을 비활성화하지 않고 `setSendButtonMode(true)`로 **정지(`is-stop`) 모드**(활성 유지, aria-label "응답 중지", 사각형 아이콘)로 전환한다. 중지 클릭 → `abort()` → `AbortError`를 잡아 **부분 응답을 그대로 확정**(`finalizeStreamingMessage`)하고 에러로 처리하지 않는다. `finally`에서 `setSendButtonMode(false)`로 전송 모드 복귀. (서버 측 생성 중단은 클라이언트 연결 종료 시 동작 — 운영 환경 확인 권장.)

### 6.3 [중간] 메시지 복사 버튼
완료된 AI 응답 말풍선에 "복사" 버튼(전체 응답 마크다운/평문). 코드 블록·SQL 블록 단위 복사 버튼도 함께.

### 6.4 [중간] 자동 스크롤/따라가기 정책의 전역 일관화
`renderProcessingMessage`·`finalizeStreamingMessage`·히스토리 로드 등 모든 append 경로가 §3.2 정책(autoStick 존중, 전송 시에만 강제)을 따르도록 정리. 산발적 `scrollToBottom()` 호출을 단일 헬퍼로 수렴.

### 6.5 [중간] 진행 패널과의 동기화
좌측 응답이 스트리밍될 때 우측 진행 패널(`progressPipeline`)은 자체적으로 맨 아래로 스크롤(현 1307). 두 영역의 스크롤이 독립적임을 유지하되, 진행 패널도 동일한 "바닥 고정 해제" 원칙을 적용할지 검토.

### 6.6 [낮음] 새 응답 도착 토스트/배지 고도화
`autoStick=false`에서 새 응답이 시작되면 플로팅 버튼에 미리보기 라벨("새 응답 ↓") 또는 미읽음 카운트 표시.

### 6.7 [낮음] 접근성·모션 전반
`aria-live="polite"`로 스트리밍 영역 보조기기 안내, 포커스 관리, `prefers-reduced-motion` 전역 존중, 키보드 단축키(예: `End`로 맨 아래 이동).

### 6.8 [낮음] 스크롤 위치 보존
진행 패널 토글·창 리사이즈 시 현재 읽던 위치를 보존(바닥 고정 상태가 아니면 위치 유지).

---

## 7. 의사결정 기록 — `docs/02_decision.md` **D-041** (구현 완료)

**D-041**로 기재됨: "스트리밍 출력 UX — 자동 스크롤을 바닥 고정(`autoStick`) 상태로 게이팅하고 '맨 아래로' 플로팅 버튼 도입. 토큰 렌더 rAF 배칭+선택 보존(§6.1), `AbortController` 기반 '응답 중지' 버튼(§6.2) 포함. 프런트엔드(정적 자원) 한정, 서버/그래프/도메인 무변경(+e2e B-07 계약 갱신)." 자세한 내용은 결정 문서 참조.
