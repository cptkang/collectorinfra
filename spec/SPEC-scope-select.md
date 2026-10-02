# Spec: scope-select (Wave 6.5 · 요구 3)

> 지도: `CAPABILITY-MAP-execution-groups.md` v4 · 계획: `plans/82` v6 §5.3~§5.5 · Wave 6.5
> 결정: **D-176 후속4**(착수 시 부기) · 선행: `group-runner`(1차 구현 완료)
> 사용자 확정: **U9=존 축도 통합** · **U10=전체 조회로 진행** · **U11=임계 없음(그룹 2개 이상이면 항상)** · **U13=지금 구현**

## ASSUMPTIONS (실측 확인분 — 추정 아님)

1. **존 축은 한 턴에 실제로 2개가 잡힌다.** `partition_execution_groups(['polestar_b0',
   'polestar_cm_gp','polestar_cm_yd'])` → **2 그룹**(은행존 / 공동존). 실측 2026-08-28.
2. `ZONE_GROUP_EXCLUSIVE`는 **혼합 조회를 막지 않는다.** `_zone_group_exclusive_or_none`
   (`src/api/routes/query.py:668`)은 ①혼합 `selected_db_ids` ②혼합 **텍스트 지정**일 때만
   발동한다. 존을 지정하지 않은 질의는 두 조건 모두 불성립 → 전 존 팬아웃된다.
3. 존 역질문(`_zone_clarification_or_none`)도 `is_full_scan_query(query) and "서버" in query`가
   아니면 **비발동**한다 — 서버 식별자가 있는 질의는 역질문 없이 통과한다.
4. **등록된 solution은 `polestar` 하나뿐**이다(apm·dpm은 `config/db_registry.yaml`에 주석 처리).
   따라서 **솔루션 축은 오늘 발동하지 않는다** — 축 선택이 U9로 뒤집힌 실질 근거다.
5. clarification 왕복 계약이 이미 있다 — `build_zone_clarification()`
   (`src/utils/query_gen_common.py:1320`)이 `{kind, question, options[{db_id,label,group}],
   original_query, multi, group_exclusive?}` 를 만들고, 프론트 `renderZoneClarification`
   (`src/static/js/app.js:1303`)이 렌더해 **`selected_db_ids`로 재전송**한다.
6. 1차가 그룹 계측을 이미 수집한다 — `group_results[*].elapsed_ms`(`_collect_group`).
   **표본은 아직 없다**(운영 배포 전).

## ★ 계획서에서 벗어나는 지점 2건 (근거 첨부 · 사용자 확정)

### ① 축 — 계획서는 존 축을 `answerable=false`로 **못박았다**. 사용자가 뒤집었다(U9).

계획서 §5.3 불변식 1·§5.4는 *"존 축은 사용자가 답을 모를 수 있어 저품질 CQ"* 라며 발동을
금지했다. 그러나 **솔루션 축이 오늘 발동하지 않으므로**(ASSUMPTION 4) 그대로면 이 모듈 전체가
죽은 코드가 된다. 사용자는 이 실측을 보고 **존 축 통합**을 확정했다(2026-08-28).

저품질 CQ 위험(IPM 2022 · 세션 시간 ~2배)은 **다른 장치로 상쇄**한다:
- *"전체 조회"* 가 **항상 첫 선택지이고 기본값**이다 — 모르면 그냥 진행하면 된다(U10).
- 미응답이 진행을 막지 않는다 — 모호성 해소 질문과 성격이 다르다(§5.3 불변식 2).
- **발동률을 관측**한다(계획서 필수 사항) — 습관화가 측정되면 되돌린다.

### ② 임계 — 계획서는 `SCOPE_SELECT_MIN_SECONDS=30`(잠정)을 권고했다. 사용자가 **임계 없음**으로 확정했다(U11).

계획서는 *"근거 없는 임계가 무기한 실동작한 전례(D-174 ② MIN_RELEVANCE_SCORE=0.3)"* 를 들어
잠정값+정산을 권고했다. 사용자 확정은 **그룹 2개 이상이면 항상 묻는다**이다.

**이 선택은 잠정 상수를 아예 만들지 않으므로 D-174 ②의 위험(근거 없는 임계 잔존)은 오히려 없다.**
남는 위험은 **반복 노출로 인한 습관화**이고, 그 상쇄가 발동률 관측이다. 시간 추정 문구는
표본 n≥20 전까지 **생략**하고 그룹 수만 표시한다(계획서 §5.5 S-C 유지 — 이건 뒤집히지 않았다).

## Objective

복합 조회에서 **사용자가 범위를 좁혀 조회 시간을 줄일 수 있게** 한다(요구 3).
좁히지 않아도 진행되며, 좁히면 **무엇을 조회하지 않았는지**가 응답에 남고 1클릭으로 재확장된다.

성공은 **묻는 것이 진행을 막지 않으면서 선택지를 주는 것**이다.

## Tech Stack

Python 3.12 · pytest. 프론트는 기존 clarification 렌더러 확장(신규 화면 0).

## Commands

```bash
pytest tests/test_scope_select -q
pytest tests/test_api/test_ui_scope_select.py -q
python3 scripts/arch_check.py --ci
```

## Project Structure

```
src/domain/scope_select.py           신규 — 발동 판정 · 페이로드 조립 · 미조회 범위 기록(순수)
src/api/routes/query.py              수정 — 게이트 배선(기존 zone clarification 옆)
src/static/js/app.js                 수정 — "전체 조회" 기본 선택 + 건너뛰기
src/config.py                        수정 — CompositeConfig 플래그
src/state.py                         수정 — scope_narrowed(요청 스코프)
tests/test_scope_select/             신규
```

## Code Style

```python
def scope_question_or_none(
    *,
    groups: Sequence[dict],
    ctx: Mapping[str, Any],
    enabled: bool,
) -> Optional[dict]:
    """범위 질문 페이로드를 만든다(묻지 않아야 하면 None).

    **묻지 않는 조건이 묻는 조건보다 많다** — 재개 턴·승계·비대화 채널·모호성 해소 대기·
    좁힐 여지 없음. 게이트는 전부 결정적이며 LLM을 쓰지 않는다.
    """
```

## Testing Strategy

**전부 mock — LLM·DB 미사용**(D-127).

| 대상 | 검증 |
|---|---|
| 발동 | 그룹 2개 이상 + 대화형 채널 + 첫 턴 → 페이로드 반환 |
| 비발동 | 그룹 1개 / 비대화 채널 / `selected_db_ids` 재개 턴 / 승계 / **모호성 해소 우선** / 플래그 off |
| 페이로드 | `"전체 조회"`가 **첫 선택지 + `default: true`** · 그룹 라벨이 선택지 |
| U10 미응답 | 답 없이 진행하면 **전체 조회**와 동일 결과(게이트가 진행을 막지 않는다) |
| U11 임계 | 시간 임계 상수가 **존재하지 않는다**(코드에 매직 넘버 0 — 테스트가 grep으로 단언) |
| 시간 문구 | 표본 n<20이면 **초 표기 없이 그룹 수만** · n≥20이면 p50~p90 범위 |
| 좁힘 기록 | `scope_narrowed: {selected, skipped}` 적재 + 응답 말미 **미조회 범위 명시** |
| 재확장 | 미조회 그룹을 포함한 `selected_db_ids` 재전송 페이로드가 응답에 실린다 |
| 발동률 | 관측 카운터가 증가한다(습관화 통제 재료) |

## Boundaries

**Always**
- **"전체 조회"가 항상 첫 선택지이고 기본값**이다(U10 — 모르면 그냥 진행).
- 모호성 해소 질문(`zone_select`)이 대기 중이면 **발동하지 않는다**(2연속 질문 금지).
- 비대화 채널(배치·평가·API 직접)은 **항상 비발동** — 기존 `zone_clarification_allowed` 공유.
- 좁혔으면 **미조회 범위를 응답에 명시**하고 재확장 경로를 준다(침묵 절단 금지).
- 한 번 확정된 범위는 **같은 스레드에서 재질문하지 않는다**.
- **발동률을 관측**한다 — 못 보면 습관화를 통제할 수 없다.

**Ask first**
- 시간 임계 재도입 (U11이 "임계 없음"으로 확정)
- 자동 축소(`auto_narrow`)로 그룹을 코드가 먼저 줄이기 — 솔루션 축이 열릴 때 함께
- 좁힌 범위의 멀티턴 영구 승계

**Never**
- 답하지 않으면 진행이 막히게 만들기 (모호성 해소 질문과 구분이 사라진다)
- 탐색(discovery) 그룹을 비용 산정에 포함 (존당 ~50ms)
- 좁힌 사실을 응답에서 생략
- LLM으로 발동 여부를 판정

## Success Criteria

1. 은행존·공동존 2그룹 조회에서 **범위 질문이 뜬다**(존 축 · U9).
2. `"전체 조회"` 가 첫 선택지이고 기본값이며, **답하지 않아도 전체 조회로 진행**된다(U10).
3. 시간 임계 상수가 코드에 **없다**(U11) — 그룹 2개 이상이면 항상 묻는다.
4. 표본 n<20이면 초 표기가 **나오지 않는다**(그룹 수만).
5. 좁히면 응답 말미에 **미조회 범위**가 명시되고 재확장 1클릭이 붙는다.
6. 모호성 해소 질문과 **같은 턴에 겹치지 않는다**.
7. 플래그 off면 기존 경로 **바이트 동일**.
8. `arch_check --ci` exit 0.

## Open Questions

- **발동률 임계 미정** — 관측 후 습관화가 확인되면 게이트를 되살릴지 판단(계획서 P15 · 만료일 2027-02-20).
