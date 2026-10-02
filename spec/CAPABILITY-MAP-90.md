# Capability Map: 채팅창 폴스타(존) 스코프 표시·선택·해제 (`plans/90` · D-205)

> **작성일**: 2026-09-09 · **근거**: `plans/90-thread-zone-scope-indicator.md` v2(§9 APM·DPM 재검토 반영)
> **게이트 적용값(구현 착수 시 가정 — 사용자 지시 "구현을 진행하라"에 따라 계획서 권고안으로 진행)**:
> G-1=**승계 끊기**(다음 질의는 첫 턴 규칙) · G-2=**1회 전송 후 승계**(매 턴 하드 핀 아님) ·
> G-3=**입력창 위 칩** · G-4=**해제 시 `mapped_db_ids`도 비운다**

## 모듈

| Module id | 책임 | 소비자 | Depends on |
|---|---|---|---|
| `db-scope-resolve` | 최종 state → **다음 턴이 승계할 db_ids**(`resolve_thread_db_ids`)와 **축 구조 `db_scope`**(`build_db_scope`) · 축 옵션(`scope_axes_options`). 레지스트리 접근자만 사용, 리터럴 0. `context_resolver`와 라우트가 **같은 함수**를 쓴다 | `db-scope-contract` · `db-scope-reset` · `context_resolver` | registry |
| `db-scope-source` | "이 db_ids가 어디서 왔나" 신호 — state `db_scope_source`(요청 스코프). 3단 `semantic_router`·2단 `subagents`(`db_origin`)·승격(`_collect_db_promotion`)·1단은 승격 경로 공유 | `db-scope-resolve` | — |
| `db-scope-reset` | 요청 `reset_db_scope` → 후속 입력 초기화(ⓐ) · sticky 폴백 차단(ⓑ) · pre-gate 첫 턴 규칙 복귀(ⓒ) | 라우트 | `db-scope-resolve`(ⓒ 판정) |
| `db-scope-contract` | `QueryRequest.reset_db_scope` · `QueryResponse.db_scope` · 4 응답 경로 대칭 삽입 · `GET /api/v1/scope/options` | `scope-chip-ui` | `db-scope-resolve` · `db-scope-source` · `db-scope-reset` |
| `deep-agent-selected-db` | 1단 `_AMBIENT_KEYS`에 `selected_db_ids` 추가 — 칩 선택이 운영 경로에 닿게 | `db-scope-contract`(전제) | — |
| `scope-chip-ui` | 입력창 위 스코프 칩(5상태) · 축 옵션 팝오버(역질문 렌더러 공유) · × 해제 · 전송 3경로 주입 · 응답 4경로 수신 | 사용자 | `db-scope-contract` |

**Build order**: `deep-agent-selected-db` ∥ `db-scope-resolve` → `db-scope-source` → `db-scope-reset` → `db-scope-contract` → `scope-chip-ui`

## 경계가 이렇게 그어진 이유

- **resolve를 라우트에서 떼는 이유**: "칩이 보여주는 값"과 "다음 턴이 실제로 승계하는 값"이 어긋나면
  침묵 강등의 새 형태다. 한 함수를 두 소비자(`context_resolver`·라우트)가 쓰게 해 어긋날 길을 없앤다.
- **source를 별도 모듈로 두는 이유**: 세 단(`deep_agent`/`intent`/`semantic`)이 대상을 만드는 자리가
  다르다. 문자열(reason) 매칭이 아니라 각 자리가 구조화 키를 남겨야 하고, 그 키가 없는 경로는
  `classified`로 떨어지는 것을 테스트가 단별로 단언한다.
- **reset이 resolve에 의존하는 이유**: pre-gate가 "체크포인트 존재"가 아니라 "승계할 스코프 존재"로
  판정해야 하고, 그 판정은 resolve와 같은 함수여야 한다.
- **deep-agent 보정이 독립인 이유**: 1줄이고 다른 모듈과 파일이 겹치지 않는다. 그러나 없으면
  `scope-chip-ui`의 선택이 운영 경로에서 무시되므로 contract보다 먼저 랜딩한다.
- **순환 없음**: `scope-chip-ui`는 contract의 페이로드 형식만 따른다. 서버 모듈은 UI를 모른다.

## 인터페이스 (경계 계약)

```jsonc
// QueryResponse.db_scope · SSE done.db_scope (4 경로 동일)
{
  "zone_group": {"code": "bank", "label": "은행존", "db_ids": ["polestar_b0"]} | null,
  "solutions":  [{"code": "polestar", "label": "폴스타(인프라 모니터링)", "db_ids": ["polestar_b0"]}],
  "db_ids":     ["polestar_b0"],          // resolve_thread_db_ids — 다음 턴 승계 집합 원본
  "source":     "selected" | "inherited" | "hint" | "planned" | "classified" | "none"   // 문자열, 닫힌 enum 아님(`dependent` 예약)
}
// QueryRequest.reset_db_scope: bool = false (텍스트 라우트 2곳 — 파일 라우트는 §SPEC 근거로 제외)
// GET /api/v1/scope/options
{"axes": [{"axis": "zone_group", "exclusive": true,
           "options": [{"key": "bank", "label": "은행존", "db_ids": ["polestar_b0"]}, …]}]}
```

`axis`·`key`·`db_ids`·`label` 어휘는 `src/domain/scope_select.py` 페이로드와 같다 — 프론트 렌더러 하나가
역질문·범위 선택·칩 팝오버를 그린다.
