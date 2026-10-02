# Capability Map: Plan 84 — 질의 프롬프트 이력

> 요구 근거의 정본은 **`plans/84`**이다. 여기에 배경을 복사하지 않는다.
> **승인**: 2026-08-28 사용자 — G-1~G-3 확정 후 "84번 계획을 구현하라".
> 결정 예약: **D-183**(질의 프롬프트 이력) — `docs/02_decision.md` 「채번 이력」 등재 완료.

## 모듈

| Module id | 책임 | 의존 |
|---|---|---|
| `query-history-ui` | 질의 프롬프트의 브라우저 로컬 저장 + "질의 이력" 탭(목록·검색·재사용·삭제) — `plans/84` 트랙 A | — |
| `query-audit-path` | 질의 감사 기록의 경로 일원화 — `AuditService.log_user_request` 호출부 복구 · 노드→라우트 이설 · CLI 보존 — 트랙 B | — |

**빌드 순서**: `query-history-ui` ∥ `query-audit-path` (의존 없음 · 병렬 가능)

## 경계가 이 자리인 이유

- **소비자가 다르다.** `query-history-ui`의 소비자는 **질의를 던지는 사용자**(내가 뭘 물었나),
  `query-audit-path`의 소비자는 **운영자·감사**(누가 언제 무엇을 물었나)다. 같은 데이터(질의문)를
  다루지만 보존 주체·수명·조회 경로가 전부 다르다.
- **독립 출하 가능.** `query-history-ui`만 랜딩해도 사용자 요청(§0 위쪽 행)은 충족된다.
  `query-audit-path`만 랜딩해도 관리자 감사 화면의 결손은 해소된다.
- **의존 방향: 없음.** 두 모듈이 공유하는 코드 자산이 없다 —
  UI는 `src/static/*`, 감사는 `src/api/routes/query.py`·`src/nodes/input_parser.py`·`src/main.py`.
  브라우저 목록은 서버 감사를 **읽지 않는다**(G-1이 서버 조회 API를 범위 밖으로 뒀다).
- **한 모듈을 잘라도 나머지 요구가 재작성되지 않는다.** `query-audit-path`를 통째로 빼도
  `query-history-ui`의 수용 기준 5개(§5.1~5.5)는 그대로 성립한다.

## 공유 자산 소유권

| 자산 | 소유 모듈 | 근거 |
|---|---|---|
| `src/static/js/app.js` | `query-history-ui` | 감사 트랙은 프론트를 건드리지 않는다 |
| `src/static/js/admin.js` (감사 탭 표기) | `query-audit-path` | 표기 대상이 DB 감사 상태(anonymous)다 |
| `tests/test_api/test_ui_query_history.py` | `query-history-ui` | — |
| `tests/test_api/test_query_audit_path.py` | `query-audit-path` | — |
| 정적 자산 캐시 버전(`?v=`) | `query-history-ui` | `index.html`·`style.css`를 바꾸는 쪽 |

## 모듈 스펙

- `SPEC-query-history-ui.md`
- `SPEC-query-audit-path.md`

## 이 맵이 두 모듈인 이유 (한 모듈이 아닌)

`plans/84`의 요청 문장은 하나("질의 프롬프트를 저장해 목록으로 보고 싶다")지만, 실측이 드러낸
결손은 둘이고 **수용 기준이 서로 겹치지 않는다**. 한 스펙으로 묶으면 UI 작업의 모든 태스크가
감사 경로 계약(파일/DB 이중 기록·CLI 보존)을 함께 이고 가야 한다 — 그 계약은 브라우저 목록과
아무 관계가 없다.
