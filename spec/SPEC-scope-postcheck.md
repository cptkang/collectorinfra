# Spec: scope-postcheck

> Module id: `scope-postcheck` | 근거: `plans/88` §4.3 · W3 | 예약 결정: **D-203**
> 계층: utils(판정) + orchestration(호출부 2곳)

## ASSUMPTIONS I'M MAKING

1. 대조 키는 게이트 verdict의 `scope_col` 종류와 **같은 종류**의 결과 컬럼이다 — hostname류↔hostname류, name류↔name류
   (D-061 혼합 금지). 결과 행에 같은 종류의 식별 컬럼이 없으면 대조하지 않고 노트만 남긴다(`postcheck_skipped`).
2. 값 비교는 `strip().casefold()` — DB2 결과 컬럼 소문자화·대문자 hostname 혼재를 흡수한다.
3. **outside 행은 제거**하고(플래그 on), **missing은 표기만** 한다. 제거 후 0건이 되면 결과는 0건이며 그 사실을 노트에 남긴다.
4. 결정적 컴파일 경로(HAVING IN)는 정의상 outside 0건이라 no-op이다 — 테스트로 고정한다.
5. 플래그 `COMPOSITE_SCOPE_POSTCHECK_ENABLED` 기본 off. off면 계산도 하지 않는다.

## Objective

**LLM 폴백 SQL이 선행 스코프를 어겨 목록 밖 서버를 돌려줘도 사용자에게 도달하지 않게 하고, 스코프 안 서버 중 결과가
없는 서버를 드러낸다.**

현행: `build_prior_rows_block` 규칙 3("목록 외 서버 금지")은 프롬프트 지시일 뿐 검사가 없다. 충족도 검증은 `prior_targets`
소비자에만 발동한다(`plans/88` §1.2).

## Tech Stack / Commands

기보유만.
```bash
python -m pytest -q tests/test_composite/test_scope_postcheck.py tests/test_orchestration/test_orchestrator.py
```

## Project Structure

| 경로 | 이 모듈에서 |
|---|---|
| `src/utils/prior_dependency.py` | `ScopeConformance` · `assess_scope_conformance(verdict, rows)` · `filter_outside_rows(...)` |
| `src/config.py` | `CompositeConfig.scope_postcheck_enabled: bool = False` |
| `src/orchestration/agent_orchestrator.py` | 레벨 결과 정규화 직후 대조 → 행 제거 · 노트 |
| `src/orchestration/deepagents_tools.py` | collector 적재 전 대조 |
| `tests/test_composite/test_scope_postcheck.py` | **신규** |

## Code Style

```python
class ScopeConformance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checked: bool                      # 대조를 수행했는가(같은 종류 식별 컬럼이 있었는가)
    result_col: str = ""
    outside: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
```

## Testing Strategy

| 케이스 | 기대 |
|---|---|
| 스코프 {a,b,c} · 결과 {a,b,z} | `outside=[z]`, `missing=[c]` · 행 z 제거 |
| 결과 컬럼이 `NAME`(대문자)·값 `A ` | 대소문자·공백 무시 일치 |
| 결과에 식별 컬럼 없음 | `checked=False` · 행 불변 · 노트 `postcheck_skipped` |
| 스코프 hostname · 결과 name만 | `checked=False`(종류 불일치 — 오제거 금지) |
| 제거 후 0건 | `query_results=[]` · `organized_data.rows=[]` · 노트에 "전부 스코프 밖" |
| 플래그 off | 행·노트 **불변** |
| 2단/1단 배선 | 둘 다 같은 함수 · `query_results`와 `organized_data.rows` **둘 다** 정리 |

## Boundaries

**Always** — 종류 불일치면 제거하지 않는다(오제거 > 미제거) · 결정적 경로 no-op
**Ask first** — missing 서버를 결과 행으로 채우기(빈 행 삽입) · 제거 대신 표기만으로 정책 변경
**Never** — 선행 스코프를 재계산(게이트 verdict만 신뢰) · 절단된 스코프(100 초과)를 기준으로 missing 계산(절단 시 missing 생략)

## Success Criteria

1. 표의 판정이 결정적으로 나온다.
2. 플래그 on에서 outside 행이 두 결과 필드에서 제거되고 노트가 남는다(1단·2단).
3. 플래그 off 비트 동일 · 기존 스위트 회귀 0.

## Open Questions

없음.
