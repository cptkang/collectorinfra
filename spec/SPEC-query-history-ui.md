# Spec: 질의 프롬프트 이력 — 로컬 저장 + 접이식 "질의 이력" 사이드바 (Plan 84 트랙 A)

> 요구·실측 근거의 정본은 **`plans/84` §1.1·§2.1**이다. 배경을 복사하지 않는다.
> 모듈 id: **`query-history-ui`** (`CAPABILITY-MAP-84.md`) · 착수 결정: **D-183**(예약 등재 완료).
> 의존: 없음. `query-audit-path`와 병렬.

## Objective

지금 사용자는 **자기가 뭘 물었는지 다시 볼 수 없다**. `promptHistory`는 메모리 배열이라
(`app.js:250`) 새로고침하면 사라지고, ↑↓ 키 탐색 말고는 목록을 보는 방법이 없다.

**이 스펙이 만드는 것**: 브라우저에 남는 질의 목록과, 채팅 옆에서 열고 닫는 사이드바.

| 축 | 결정 | 근거 |
|---|---|---|
| 저장 위치 | **localStorage** | 사용자 확정 G-1. 인증이 꺼져 있어(`config.py:457`) 서버 저장은 전원 `anonymous`로 뭉친다 — 남의 질의가 내 목록에 섞인다 |
| 저장 항목 | **질의문 + 시각뿐** | 사용자 확정 G-2. SQL·결과·DB 식별자는 저장하지 않는다 |
| 목록 위치 | **접이식 왼쪽 사이드바** | 사용자 확정 G-3(2026-08-31 개정 — 최초는 세 번째 탭). 탭은 본문을 교체해 이력을 보는 동안 채팅이 사라진다 — 옛 질의를 골라 다시 묻는 쓰임에는 둘이 동시에 보여야 한다. 오른쪽 진행 패널과 대칭인 접기 패턴을 쓴다 |

**하지 않는 것**: 서버 이력 API · 사용자별 이력 테이블 · 즐겨찾기 · 이름 붙여 저장 · 질의 공유 ·
질의문 마스킹(`plans/84` §2.3).

## Tech Stack

기존 스택 그대로 — 바닐라 JS(ES5 스타일 `var`/`function`, 빌드 단계 없음) · CSS 커스텀 프로퍼티
토큰(D-180) · 정적 파일 서빙(FastAPI `StaticFiles`). **신규 의존성 0건.**

## Commands

```bash
# 테스트 (이 모듈)
.venv/bin/python -m pytest tests/test_api/test_ui_query_history.py -q

# 회귀 (UI 계약 전체)
.venv/bin/python -m pytest tests/test_api/test_ui_query_history.py \
    tests/test_api/test_ui_alarm_view.py tests/test_api/test_ui_theme.py -q

# 문법 검사 (빌드 단계가 없으므로 이것이 유일한 정적 검사)
node --check src/static/js/app.js

# 아키텍처 (UI는 대상 밖이지만 트랙 B와 함께 돌린다)
.venv/bin/python scripts/arch_check.py --ci
```

## Project Structure

```
src/static/index.html          → chat-layout 첫 열의 이력 사이드바 마크업
src/static/css/style.css       → .history-panel 계열 + grid 접힘 조합 4종
src/static/js/app.js           → 저장소 헬퍼 · 기록 · 패널 접기 상태 · 목록 렌더
tests/test_api/test_ui_query_history.py  → 정적 계약 고정 (신규)
```

## Code Style

기존 `app.js` 관례를 따른다 — `var`, 함수 선언, 한국어 주석으로 **왜**를 남긴다.

```js
// ─── 질의 이력 (D-183) ───
//
// 탭이 아니라 사이드바인 이유: 탭은 본문을 교체해 이력을 보는 동안 채팅이 사라진다.
// 옛 질의를 골라 다시 묻는 쓰임에는 둘이 동시에 보여야 한다.

var HISTORY_KEY = "query_prompt_history";   // 전례: alarm_receive_enabled
var HISTORY_MAX = 200;                       // 상한 없는 누적은 이 저장소의 금기

function loadHistory() {
    // 사생활 모드·용량 초과에서 localStorage는 던진다. 목록이 비는 것은
    // 허용해도, 그 때문에 질의 전송이 막히는 것은 허용하지 않는다.
    try {
        var raw = localStorage.getItem(HISTORY_KEY);
        var parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
        console.warn("[history] 로드 실패:", e);
        return [];
    }
}
```

## Testing Strategy

브라우저 e2e(playwright)는 이 환경에서 돌지 않는다. D-182의 `test_ui_alarm_view.py`와 같은
방식으로 **계약이 되는 지점만 정적으로 고정**한다 — 조용히 되돌아가는 회귀(사이드바 누락, 기록
지점 분산, 즉시 전송 배선, 접힘 조합 누락)를 잡는 것이 목적이다. **12건.**

| # | 대상 | 단언 |
|---|---|---|
| 1 | `index.html` | `id="historyPanel"`·`historyToggle`·`historyList`·`historySearch` 존재 |
| 2 | `index.html` | 사이드바가 **`.chat-layout` 안**에 있다 — 밖으로 나가면 grid가 깨지고 인증 게이트도 안 걸린다 |
| 3 | `index.html` | 뷰 탭이 **정확히 2개**(chat·alarm) — 이력이 탭으로 되돌아가는 회귀 차단 |
| 4 | `style.css` | 접힘 조합 **4종이 모두 정의**(`panel-collapsed` / `history-collapsed` / 둘 다 / 기본) |
| 5 | `app.js` | `HISTORY_KEY`·`HISTORY_MAX` 존재, 상한이 유한한 정수 |
| 6 | `app.js` | 저장소 접근이 전부 try/catch 안에 있다 |
| 7 | `app.js` | 기록이 `handleSend` 안 `promptHistory` 인접에서 일어난다(호출 1회) |
| 8 | `app.js` | 이력 클릭 경로에 전송 호출(`handleSend`/`fetch`)이 **없다** |
| 9 | `app.js` | `setActiveView`가 뷰를 하드코딩 toggle하지 않는다(등록 테이블) |
| 10 | `app.js` | `viewRegistry`에 `history`가 **없다** — 사이드바는 본문을 교체하지 않는다 |
| 11 | `app.js` | 스크롤 복원이 `view === "chat"`에서만 돈다 |
| 12 | `app.js` | 패널 상태 키가 있고 **기본값이 접힘**(저장소가 막힌 환경 포함) |

## Boundaries

- **Always**: 저장소 접근은 try/catch · 상한 유지 · 기록 지점은 `handleSend` 하나 ·
  한국어 주석으로 "왜"를 남긴다 · 정적 자산 변경 시 `?v=` 증가
- **Ask first**: 저장 항목 확대(SQL·결과) · 서버 저장 전환 · 사이드바 기본 상태 변경 · 알람 탭 동작 변경
- **Never**: 목록 클릭으로 **즉시 전송** · 질의문을 서버로 전송 · 상한 없는 누적 ·
  `promptHistory`(↑↓ 탐색)의 기존 동작 변경 · 알람 뷰 마크업 재사용을 빙자한 공유 상태 도입

## Success Criteria

1. 질의를 보낸 뒤 **새로고침해도** 사이드바에 남아 있다.
2. 항목 클릭 → **입력창에 채워지며, 전송되지 않는다**(사이드바는 채팅 뷰 안이라 전환이 필요 없다).
3. 검색어 입력 시 목록이 즉시 걸러진다(부분일치·대소문자 무시).
4. 개별 삭제·모두 지우기가 즉시 반영되고, 비면 빈 상태 안내가 나온다.
5. `localStorage`가 막힌 환경에서 **질의 전송이 정상 동작**한다(목록만 빈다).
6. 같은 질의를 반복해도 목록에 **한 번만** 남는다(최신 시각으로 갱신).
7. 사이드바 접기/펼치기가 오른쪽 진행 패널과 **독립적으로** 동작한다(조합 4종).
8. 기본은 접힘 — 첫 방문 화면이 종전 2열과 같다. 편 상태는 다음 방문에도 유지된다.
9. 알람 탭 동작(배지·수신·스크롤 복원)에 회귀가 없다 — `test_ui_alarm_view.py` 9건 통과.
10. `node --check` 통과 · 클린 기준선 대비 신규 실패 0.

## Open Questions

- ~~모바일 414px에서 탭 3개~~ — 사이드바 개정으로 소멸(탭은 둘로 복귀). 640px 이하에서는
  진행 패널과 함께 사이드바도 숨긴다(기존 전례).
- 상한 200건의 적정성 — 감사 로그 일 평균으로 사후 조정(`plans/84` §6).
