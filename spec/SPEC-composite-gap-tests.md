# Spec: composite-gap-tests

> Module id: `composite-gap-tests` · Map: `CAPABILITY-MAP-intent-extraction.md`
> WU-04 (`plans/80` §5.2 차수 0) · 설계: `plans/78` W0

## Objective

78이 해소할 갭 **G2**(선행 결과 → 조사 대상 전달 부재)와 **G3**(대상 N개를 1개로 절단)을
**실패하는 테스트로 먼저 고정**한다. 구현(W1·W2)은 **WU-11 이후**이고 그건 G-BILL 뒤에 있다.

## ⚠ 핵심 설계 결정 — `xfail(strict=True)`

W0의 원안은 *"현재 실패해야 정상"* 인 테스트다. 그런데 **W1이 언제 착수될지 모른다**(G-BILL 대기).
그대로 두면 **실패 테스트가 무기한 스위트에 남아** 회귀 판정을 오염시킨다.

→ `@pytest.mark.xfail(strict=True, reason="...")`로 고정한다.

| 성질 | 효과 |
|---|---|
| 갭이 살아 있으면 | `xfail` — 스위트는 초록, 갭은 **문서화된 채 추적**된다 |
| W1·W2가 갭을 해소하면 | `XPASS` → **strict라서 실패** — "고쳤으니 마커를 떼라"고 알려준다 |
| 기준선 오염 | **없음** — 실패 카운트가 늘지 않는다 |

**이것이 W0의 의도("실패로 고정")를 더 잘 만족한다** — 단순 실패는 무시되지만 xfail-strict는
해소 시점에 반드시 손을 대게 만든다.

## Scope

`tests/test_orchestration/test_composite_host_scope.py` **신설**.

| 테스트 | 단언 | 갭 |
|---|---|---|
| `T-G2` | t1(data_query, 3행) → t2(process_query)에서 **대상 집합이 비어 있다** | G2 |
| `T-G3` | 대상 3개 입력 시 조사 결과가 **1건으로 절단된다** | G3 |

**범위 밖**: `src/` 변경 일체. 이 모듈은 **테스트만** 만든다.

## ⚠ 제약 (`plans/80` §5.2 WU-04 단서)

**라우팅 결과·`relevance_score`·의도 분류에 단언하지 않는다.** 그 영역은 S-1 미검증이다.
`prior_targets` 전달과 fan-out **대상 수**만 단언한다. 이 선을 넘으면 WU-05 뒤로 미룬다.

## Testing Strategy

실 LLM·실 DB 없이 구성한다. `task_plan`/결과 행을 직접 만들고, 대상 해소 경로를 호출해
현재 산출을 관측한다. **기존 `tests/test_orchestration/test_prior_rows_scope.py`의 구성 방식을
차용**한다(사본 금지 — 픽스처가 재사용 가능하면 재사용).

## Success Criteria

| # | 조건 |
|---|---|
| **S1** | 두 테스트가 **`xfail`로 보고**된다(현행 코드에서 갭이 재현됨) |
| **S2** | `strict=True`이며 `reason`이 **갭 번호와 계획서 절**을 지목한다 |
| **S3** | 실패 메시지가 갭을 **읽고 이해할 수 있게** 서술한다(단순 `assert False` 금지) |
| **S4** | 라우팅·`relevance_score`·intent에 대한 단언이 **0건**이다(제약 준수 · grep으로 확인) |
| **S5** | `src/` 변경 **0건** |
| **S6** | 전체 회귀 기준선 동일(실패 수 불변) · `arch_check --ci` exit 0 |

## Open Questions

- 없음. W1 착수 시 마커 제거가 필요하다는 사실은 `strict=True`가 자동으로 알린다.
