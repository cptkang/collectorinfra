# 대안 수립 - 오용·실수·착각 처방 축

관측을 처방 **축**으로 지목할 뿐이고 문구·임계값·플래그 결정은 사람이 한다.

## 결정적으로 확정된 실패 (Y-6)

시나리오 자체의 반복이 3회 이상이고 **전건 동일 실패**다 - LLM 흔들림과 구별된다. run 단위 `--repeat` 이 1회여도 보류하지 않는다.

| 시나리오 | 반복 | 판정 | 깨진 단언 |
|---|---|---|---|
| K-01 | 5 | fail | status |
| K-03 | 3 | fail | status |
| K-04 | 3 | fail | sql_must_match, sql_must_match, sql_must_match |
| R1-01 | 3 | fail | row_count.min, status |
| R1-02 | 3 | fail | status |
| R1-03 | 3 | fail | has_file, status |
| R1-05 | 3 | error | - |
| R1-06 | 3 | error | - |
| R1-07 | 3 | error | - |
| R1-08 | 3 | error | - |
| R1-10 | 3 | error | - |
| R2-05 | 3 | error | - |
| R2-09 | 3 | error | - |
| R3-01 | 3 | error | - |
| R3-03 | 3 | fail | row_count.max |
| R3-04 | 3 | error | - |
| R3-05 | 3 | error | - |
| R3-08 | 3 | fail | sql_must_not_match |
| R3-09 | 3 | error | - |
| R3-10 | 3 | fail | status |
| R4-02 | 3 | error | - |
| R4-03 | 3 | error | - |
| R4-04 | 3 | error | - |
| R4-05 | 3 | error | - |
| R4-06 | 3 | error | - |
| R4-08 | 3 | error | - |
| R4-09 | 3 | error | - |
| R4-10 | 3 | fail | row_count.min |

## 관측 -> 처방 축

| 관측 신호 | 건수 | 처방 축 | 후보 조치 | 소유 계획서 |
|---|---|---|---|---|
| control_broken | 21 | 금지 규칙 범위 축소 | 보류 (반복 부족) | Known Mistakes(부정 지시 범위) |

## 처방 우선순위

조용한 오답이 항상 맨 위다 - 사용자가 알아차릴 수 없는 실패이기 때문이다.

| 순위 | 신호 | 관측 |
|---|---|---|
| 1 | silent_wrong | 0 |
| 2 | hang | 198 |
| 3 | crash | 1 |
| 4 | control_broken | 21 |
| 5 | clarify_missing | 0 |

## 제약

- 1회 관측으로 처방하지 않는다. 3회 중 1회만 어긋난 건은 `불안정`으로 제외한다.
- **단, 시나리오 반복 3회 이상 전건 동일 실패는 결정적이다**(Y-6) - run 단위 반복이 1회여도 보류하지 않는다.
- 신규 `enable_*` 추가가 불가피하다고 판단되면 **그 사실 자체를 사람 판단 항목으로 올린다**(D-162).

