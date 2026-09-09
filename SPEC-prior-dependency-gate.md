# Spec: prior-dependency-gate

> Module id: `prior-dependency-gate` | 근거: `plans/88` §4.1 · §2.2(E-2 게이트 대칭) · W1 | 예약 결정: **D-203**
> 계층: utils(`src/utils/prior_dependency.py`) + orchestration(호출부 2곳)

## ASSUMPTIONS I'M MAKING

1. **G-1 = (a) 미실행 + 사유**. 플래그 `COMPOSITE_SEQUENTIAL_GATE_ENABLED` 기본 off — off면 판정은 **로그만**
   남기고 실행·상태·응답 전부 비트 동일.
2. 선행 결과의 행은 3가지 shape(`rows` / `query_results` / `organized_data.rows`) 중 하나다 — `subagents._prior_result_rows`와
   같은 규약을 utils에 **재구현**한다(utils → orchestration 역방향 import 금지).
3. `input_from`이 여럿이면 **하나라도 실패하면 실패**다(부분 스코프로 조용히 좁히지 않는다). 행이 있는 소스가 하나도
   없으면 0건이다.
4. 1단(deepagents)에서는 `input_from`이 런타임에 계산된다. 선행 결과가 0건/실패면 `_dependency_scope`가 후보를 만들지
   않아 `input_from`이 비므로, **참조어(G2)·순위어(G3)가 있는데 생산자 결과가 전부 빈/실패**인 경우를 게이트가
   별도로 잡는다. 값 일치(G1)는 빈 결과에서 성립할 수 없다.
5. 후속 task를 건너뛸 때 결과는 `{"error": <사유 문장>, "skipped": True, "skip_reason": <코드>}`이고 `status="skipped"`다.
   `error` 키가 있어야 기존 집계기가 부분 실패 문구로 처리한다(CAPABILITY-MAP-88 소비처 표).

## Objective

**선행 조회가 비었거나 실패했을 때 후속 조회가 스코프 없이 전체 서버를 조회하는 침묵 오류를 막는다.**

현행(`plans/88` §3): t1이 0건이면 `build_prior_rows_block`이 `""`를 돌려주고 t2는 전체 서버의 1개월 CPU를 답으로
내놓는다 — 사용자는 "높은 서버가 없다"를 "모든 서버 목록"으로 받는다. 1단도 후보가 비면 미주입으로 같다.

**성공**: 선행이 실패·0건·식별 컬럼 부재이면 후속은 실행되지 않고, 그 사유가 task 결과와 노트에 구조화돼 남는다.
정상이면 종전과 같은 주입이 일어나고 절단 여부가 기록된다.

## Tech Stack

기보유만 — pydantic v2 · pytest(+asyncio) · 신규 라이브러리 0.

## Commands

```bash
python -m pytest -q tests/test_composite/test_sequential_gate.py \
                    tests/test_orchestration/test_orchestrator.py \
                    tests/test_composite/test_prior_targets_wiring.py tests/test_orchestration/test_deep_agent.py
python scripts/arch_check.py --ci && python scripts/overfit_check.py --ci
```

## Project Structure

| 경로 | 이 모듈에서 |
|---|---|
| `src/utils/prior_dependency.py` | **신규** — `DependencyVerdict` · `assess_prior_dependency(task, prior)` · `verdict_note(...)` · 사유 코드 상수 |
| `src/config.py` | `CompositeConfig.sequential_gate_enabled: bool = False` |
| `src/orchestration/agent_orchestrator.py` | 레벨 실행 전 판정 → skipped 처리 · 노트 수집 |
| `src/orchestration/deepagents_tools.py` | `_dependency_scope` 뒤 판정(참조어+빈 생산자 포함) → 도구 미실행 반환 |
| `src/static/js/app.js` | `skipped` 배지 1줄 |
| `tests/test_composite/test_sequential_gate.py` | **신규** |

## Code Style

`src/utils/prior_targets.py`와 같은 자세 — 사유를 코드 상수로, 결정 번호를 주석으로.

```python
REASON_PRIOR_FAILED = "prior_failed"
REASON_PRIOR_EMPTY = "prior_empty"
REASON_PRIOR_NO_IDENTITY = "prior_no_identity"


class DependencyVerdict(BaseModel):
    """input_from 선행 결과 판정 (D-203 · plans/88 §4.1). LLM 0회."""
    model_config = ConfigDict(extra="forbid")
    ok: bool
    reason: Optional[str] = None
    source_task_ids: list[str] = Field(default_factory=list)
    scope_col: str = ""
    scope_size: int = 0
    truncated: bool = False
    truncated_count: int = 0
```

## Testing Strategy

pytest · 전부 mock · LLM·네트워크 0. 현행 결함을 **먼저 빨갛게** 만든다(Prove-It).

| 케이스 | 기대 |
|---|---|
| 선행 `error` | `ok=False, reason=prior_failed` |
| 선행 행 0건(3 shape 각각) | `prior_empty` |
| 행은 있으나 서버 식별 컬럼 없음 | `prior_no_identity` |
| hostname 7행 | `ok, scope_col="hostname", scope_size=7` |
| 130행 | `truncated=True, truncated_count=30, scope_size=100` |
| `input_from` 없음 | `None`(판정 대상 아님) |
| 2단 · 플래그 on · t1 0건 | t2 handler **호출되지 않음** · `status=="skipped"` · 결과에 `error`·`skip_reason` |
| 2단 · 플래그 off · t1 0건 | t2 handler 호출됨 · 결과·상태 **현행과 동일**(현행 결함 재현) |
| 1단 · 플래그 on · 생산자 0건 + "그 서버들" | handler 미호출 · 반환 문자열에 사유·재호출 금지 문구 |
| 1단 · 플래그 off | 현행과 동일 |
| 1단·2단이 같은 `assess_prior_dependency`를 호출 | 대칭 고정 |

## Boundaries

**Always** — 플래그 off면 실행·상태·응답 바이트 동일 · utils는 orchestration을 import하지 않는다 · 사유는 코드 상수
**Ask first** — `input_from` 부분 실패의 의미(교집합/합집합) 변경 · 상한 100 변경(G-7)
**Never** — 되묻기 도입(G-1 (b)) · `_make_isolated_input` 내부의 주입 규칙 변경 · `prior_targets` 스키마 변경

## Success Criteria

1. `assess_prior_dependency`가 표의 판정을 결정적으로 돌려준다(LLM 0회).
2. 플래그 on에서 선행 0건/실패/식별 컬럼 부재이면 후속 handler가 **호출되지 않고** `status="skipped"`다(1단·2단).
3. 플래그 off에서 기존 스위트(618 passed) 회귀 0 · 신규 테스트의 "현행과 동일" 케이스 통과.
4. 1단·2단이 같은 함수를 호출한다(테스트 고정).
5. `arch_check --ci`·`overfit_check --ci` 0.

## Open Questions

없음 — G-1이 (b)로 바뀌면 이 모듈의 "미실행" 분기만 되묻기로 교체한다(판정 함수는 그대로).
