# Spec: 알람 UI 표시 레벨 선택 (Plan 83 트랙 B)

> 요구·실측 근거의 정본은 **`plans/83` §2**이다. 배경을 복사하지 않는다.
> 모듈 id: **`alarm-view-level`** (`CAPABILITY-MAP-83.md`) · 착수 결정: **D-178**(예약 등재 완료).
> 의존: `alarm-feedback-loop`(capabilities 계약) · `settings-ui-coverage`(섹션 누락 감지 테스트).

## Objective

지금 사용자가 알람 수신에 대해 고를 수 있는 것은 **on/off 토글 하나**뿐이다
(`alarm_receive_enabled`, localStorage, `app.js:2647`). 서버 필터는 존(zone) RBAC만 있고
(`alarm.py:945` `_visible()`), 티어·심각도 필터는 없다.

**이 스펙이 만드는 것**: 사용자가 **표시 레벨을 4단계로 선택**하고, 관리자는 억제된 알람까지
볼 수 있게 하는 옵트인 경로.

| 레벨 | 값 | 보이는 티어 | 대상 |
|---|---|---|---|
| 긴급만 | `page` | PAGE | 당직·야간 |
| 통보 대상 | `ticket` | PAGE, TICKET | 일반 운영자 |
| **전체 (기본)** | `dashboard` | PAGE, TICKET, DASHBOARD | 관제 |
| 억제 포함(감사) | `suppress` | 전부 + SUPPRESS | **관리자 전용** |

**티어를 기준축으로 삼는 이유**: 게이트의 최종 산출물이 티어이고, 심각도로 거르면 "심각도 1인데
승격돼 PAGE가 된 알람"을 놓친다 — 재현율 우선 원칙(D-048) 위배다.

**부수 효과**: `suppress` 레벨은 `docs/28`이 지적한 "오억제는 파일 tail로만 확인 가능" 문제를
UI에서 해소한다(SUPPRESS는 현재 SSE 미발행 — `alarm_notifier.py:633`).

**하지 않는 것**: 심각도 축 필터 · 알람 카드 시각 개편 · 관리자 세션 유무에 따른 발행 최적화.

## 확정된 게이트 (2026-08-28 사용자)

| # | 사항 | 확정 | 귀결 |
|---|---|---|---|
| G-1 | 저장 위치 | **localStorage** | **서버는 사용자의 레벨을 모른다** → 레벨 필터는 클라이언트 렌더 단계. 아래 §"권한과 선호의 분리" 참조 |
| G-2 | 기본값 | **`dashboard`** | 현행 동작 보존(회귀 0) — 지금 카드를 보던 사용자가 못 보게 되지 않는다 |
| G-4 | 비관리자 `suppress` | **조용한 강등** | 저장은 허용하되 실제로는 `dashboard`로 동작. 400 거부는 UX 마찰 |

## 권한과 선호의 분리 (G-1 localStorage 선택의 필연적 귀결) ★

레벨을 브라우저에 두면 **서버는 무엇을 보낼지 판단할 근거가 없다.** 그래서 두 축을 분리한다.

| 축 | 성격 | 판정 주체 | 근거 |
|---|---|---|---|
| 존(zone) | **권한** | **서버**(현행 유지) | `alarm.py:945` |
| page/ticket/dashboard 표시 | **개인 선호** | **클라이언트**(localStorage) | G-1 |
| SUPPRESS 수신 | **권한** | **서버**(`role == "admin"`) | 억제 알람 내용은 비관리자에게 도달해선 안 된다 |

**SUPPRESS만 서버 판정으로 남기는 것은 G-1의 예외가 아니라 그 보완이다.** 클라이언트 필터는
"이미 받은 것을 안 그린다"일 뿐이므로, SUPPRESS를 무조건 흘리면 **권한 없는 브라우저에 억제 알람
전문이 도달한 뒤 가려지는** 상태가 된다. 개발자 도구로 열면 그대로 보인다. 따라서:

```
SUPPRESS 이벤트는 NOISE_SSE_SUPPRESSED_ENABLED=true 이고
스트림 구독자가 role=="admin" 일 때만 전송한다(서버 판정).
그 외 티어는 존 필터만 통과시키고 레벨 판단은 클라이언트에 맡긴다.
```

## Tech Stack

기존 스택. FastAPI SSE · 바닐라 JS · localStorage. **신규 의존성 0** · **DB 스키마 변경 없음**(G-1).

## Commands

```bash
.venv/bin/python -m pytest tests/test_api/test_alarm_stream_suppress.py -q
.venv/bin/python -m pytest noise_gate/tests/test_notifier_suppress_sse.py -q
.venv/bin/python -m pytest tests/ noise_gate/ -q          # 전건 회귀
.venv/bin/python scripts/arch_check.py --ci
```

## Project Structure

```
noise_gate/application/nodes/alarm_notifier.py → SUPPRESS 분기에서 SSE 발행(플래그 게이트)
src/config.py                                  → NOISE_SSE_SUPPRESSED_ENABLED (기본 false)
src/api/routes/alarm.py                        → 스트림에서 관리자 아닌 구독자에게 SUPPRESS 미전송
src/api/settings_catalog.py                    → 신규 키 구획 등재(settings-ui-coverage 테스트 통과용)
src/static/index.html                          → 수신 토글 옆 레벨 셀렉트
src/static/js/app.js                           → localStorage 레벨 · 렌더 필터 · 툴팁 표기
.env / .env.example                            → 신규 키(인라인 주석 금지 — 주석은 별도 줄)
```

## Code Style

```javascript
// (Plan 83 B) 개인 표시 레벨 — alarm_receive_enabled 전례를 따라 localStorage에 둔다.
// 서버는 존(권한)만 거르고, 티어 표시 여부는 여기서 판단한다. tier 미상 이벤트는 항상 통과
// (analyze 테스트 경로 payload에는 tier가 없다 — 막으면 테스트 카드가 사라진다).
var ALARM_VIEW_LEVELS = ["page", "ticket", "dashboard", "suppress"];
var alarmViewLevel = localStorage.getItem("alarm_view_level") || "dashboard";

function isTierVisible(tier) {
    if (!tier) return true;                       // 미상 통과(재현율 우선)
    var allowed = ALARM_VIEW_LEVELS.indexOf(alarmViewLevel);
    var actual = ALARM_VIEW_LEVELS.indexOf(tier);
    return actual >= 0 && actual <= allowed;
}
```

## Testing Strategy

- **서버**: SUPPRESS 발행 플래그 on/off × 구독자 role(admin/user) 4조합에서 도달 여부를 단언.
  기본 off일 때 **현행과 비트 동일**(발행 0)을 고정한다.
- **클라이언트**: `isTierVisible`을 순수 함수로 분리해 레벨 4종 × 티어 4종 + `tier` 미상 조합을 단언
  (Node 없이 검증 가능한 구조 — 기존 프런트 테스트 관행이 없으므로 로직을 순수 함수로 떼어 둔다).
- **회귀**: 레벨 미설정(기본 `dashboard`) + 플래그 off에서 기존 카드 표시가 **동일**함을 확인.

## Boundaries

- **Always**: 기본 off 플래그의 비트 동일성을 테스트로 고정 · 존 필터는 레벨보다 **선행** ·
  신규 `.env` 키는 `.env.example`과 `SECTION_BY_KEY`에 동시 반영
- **Ask first**: 티어 정의·순서 변경 · SSE payload 스키마 확장 · 관리자 판정 기준 변경
- **Never**: 레벨 설정으로 **권한을 넓히는** 동작(존 무시·비관리자 SUPPRESS 수신) ·
  `tier` 미상 이벤트를 차단(테스트 경로 붕괴) · 억제 알람을 비관리자 브라우저로 전송

## Success Criteria

- [ ] 레벨 4종에서 도달·표시되는 티어 집합이 §Objective 표와 일치
- [ ] `tier` 없는 payload는 **모든 레벨에서 표시**된다
- [ ] `NOISE_SSE_SUPPRESSED_ENABLED=false`(기본)에서 SUPPRESS **발행 0** — 현행 비트 동일
- [ ] 플래그 on + 비관리자 구독자 → SUPPRESS **미도달**(브라우저에 전문이 도착하지 않음)
- [ ] 플래그 on + 관리자 구독자 + 레벨 `suppress` → 도달·표시
- [ ] 다른 존 이벤트는 레벨과 무관하게 미도달(존 우선 불변)
- [ ] 레벨 변경이 새로고침 없이 즉시 반영되고 재접속 후에도 유지된다
- [ ] `NOISE_SSE_SUPPRESSED_ENABLED`가 `settings-ui-coverage`의 섹션 테스트를 통과(구획 등재됨)
- [ ] `pytest` 전건 통과 · `arch_check --ci` 0위반

## Open Questions

없음 — G-1·G-2·G-4는 2026-08-28 사용자 확정.
