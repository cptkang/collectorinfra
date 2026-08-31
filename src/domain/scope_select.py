"""범위 사전 선택 역질문 — 발동 판정 · 페이로드 조립 (Plan 82 Wave 6.5 · D-176 후속4).

**무엇을 하나.** 실행 그룹이 여럿일 때 *"범위를 좁히시겠습니까?"* 를 물을지 결정하고,
물을 페이로드를 만든다. **실행하지 않고 라우팅도 하지 않는다** — 순수 판정이다.

## 이 모듈의 성격은 기존 존 역질문과 다르다

| | `zone_select`(D-143) | `scope_select`(여기) |
|---|---|---|
| 성격 | **모호성 해소** — 존을 특정할 수 없어 진행 불가 | **성능 최적화** — 전부 할 수 있지만 시간이 든다 |
| 기본 진행 | 불가(답해야 진행) | **가능**(건너뛰면 전체 조회) |
| 문구 | "지정되지 않았습니다" | "범위를 좁히시겠습니까?" |

**둘이 같은 턴에 겹치면 모호성 해소가 이기고 이 질문은 발동하지 않는다** — 2연속 질문은
사용자를 두 번 붙잡고, 두 질문의 성격 차이를 학습할 기회도 뺏는다.

## 사용자 확정이 계획서를 개정한 지점 2건

- **U9 — 존 축도 묻는다.** 계획서 §5.3 불변식 1은 존 축을 `answerable=false`로 못박았으나,
  **등록된 solution이 `polestar` 하나뿐**이라 솔루션 축은 오늘 발동하지 않는다(실측
  2026-08-28). 그대로면 이 모듈 전체가 죽은 코드가 되므로 사용자가 존 축을 열었다.
  저품질 CQ 위험은 *"전체 조회"가 첫 선택지·기본값*(U10)과 **발동률 관측**으로 상쇄한다.
- **U11 — 시간 임계를 두지 않는다.** 계획서는 30초 잠정값+정산을 권고했다. 사용자 확정은
  *그룹 2개 이상이면 항상*이다. **근거 없는 잠정 상수를 아예 만들지 않으므로** D-174 ②의
  위험(무기한 실동작하는 임계)은 오히려 없다.

계층: domain — 순수 · I/O·LLM·전역 상태 0.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

#: 페이로드 종류 식별자. 프론트가 `zone_select`와 분기하는 키다.
KIND = "scope_select"

#: "전체 조회" 선택지의 키. **항상 첫 선택지이고 기본값**이다(U10).
ALL_KEY = "__all__"

#: 시간 추정 문구를 낼 최소 표본 수. 미만이면 초 표기를 **생략**하고 그룹 수만 말한다 —
#: 근거 없는 예상 시간은 사용자를 오도하고, 한 번 오도하면 다음 표기도 믿지 않는다.
MIN_SAMPLES_FOR_ESTIMATE = 20

QUESTION = "이 질의는 실행 그룹 {n}개를 순차 조회합니다.{estimate} 범위를 좁히시겠습니까?"
ALL_LABEL = "전체 조회 (권장 — 어디에 있는지 모를 때)"


def _distinct_groups(groups: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """비용이 드는 그룹만 남긴다 — **탐색(discovery)은 비용 산정에서 제외**한다.

    탐색은 존당 고정 SELECT 1회(≈50ms)라 좁혀도 아낄 것이 없다. 이것을 세면
    "3그룹 조회"처럼 부풀려진 수가 나와 불필요한 질문이 뜬다.
    """
    seen: set[str] = set()
    out: list[Mapping[str, Any]] = []
    for group in groups or []:
        if not isinstance(group, Mapping):
            continue
        if group.get("kind") == "discovery":
            continue
        key = str(group.get("group_key") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(group)
    return out


def _estimate_text(groups: Sequence[Mapping[str, Any]], samples: int) -> str:
    """예상 시간 문구 — 표본이 모자라면 **아예 내지 않는다**(계획서 §5.5 S-C).

    표본 없이 낸 숫자는 근거가 없고, 근거 없는 숫자는 한 번 어긋나면 이후 모든 표기의
    신뢰를 잃는다. 그룹 수만 말하는 편이 정직하다.
    """
    if samples < MIN_SAMPLES_FOR_ESTIMATE:
        return ""
    lo = sum(int(g.get("p50_ms") or 0) for g in groups) // 1000
    hi = sum(int(g.get("p90_ms") or 0) for g in groups) // 1000
    if hi <= 0:
        return ""
    return f" (예상 {lo}~{hi}초)"


def scope_question_or_none(
    *,
    groups: Sequence[Mapping[str, Any]],
    ctx: Mapping[str, Any],
    enabled: bool,
    samples: int = 0,
) -> Optional[dict]:
    """범위 질문 페이로드를 만든다(묻지 않아야 하면 None).

    **묻지 않는 조건이 묻는 조건보다 많다.** 게이트는 전부 결정적이며 LLM을 쓰지 않는다 —
    질문을 띄울지가 모델 출력에 좌우되면 같은 질의가 어떤 날은 묻고 어떤 날은 안 묻는다.

    Args:
        groups: `partition_execution_groups` 산출 그룹(각 `group_key`·`label`·`db_ids`)
        ctx: 턴 문맥 — `zone_clarification_allowed` · `selected_db_ids` ·
            `selected_scope` · `ambiguity_pending` · `previous_scope`
        enabled: `COMPOSITE_SCOPE_SELECT_ENABLED`
        samples: 그룹 계측 표본 수(시간 문구 게이트)

    Returns:
        clarification 페이로드 또는 None.
    """
    if not enabled:
        return None
    # 비대화 채널(배치·평가·API 직접)은 답할 사람이 없다 — 물으면 그대로 멈춘다.
    if not ctx.get("zone_clarification_allowed"):
        return None
    # 재개 턴·승계 턴은 이미 범위가 정해졌다. 다시 물으면 사용자는 자기 선택이
    # 먹지 않았다고 읽는다.
    if ctx.get("selected_db_ids") or ctx.get("selected_scope") or ctx.get("previous_scope"):
        return None
    # ★ 모호성 해소가 이긴다 — 2연속 질문 금지.
    if ctx.get("ambiguity_pending"):
        return None

    billable = _distinct_groups(groups)
    if len(billable) < 2:
        return None  # 좁힐 여지가 없다

    estimate = _estimate_text(billable, samples)
    options = [{
        "key": ALL_KEY,
        "label": ALL_LABEL,
        "db_ids": [d for g in billable for d in (g.get("db_ids") or [])],
        "default": True,
    }]
    for group in billable:
        options.append({
            "key": str(group.get("group_key")),
            "label": str(group.get("label") or group.get("group_key")),
            "db_ids": list(group.get("db_ids") or []),
            "default": False,
        })

    return {
        "kind": KIND,
        "question": QUESTION.format(n=len(billable), estimate=estimate),
        "axis": "zone_group",
        "options": options,
        "original_query": str(ctx.get("original_query") or ""),
        "multi": True,
        # 답하지 않아도 진행된다(U10) — 프론트가 건너뛰기를 렌더하는 신호.
        "skippable": True,
    }


def narrowed_record(
    groups: Sequence[Mapping[str, Any]], selected_db_ids: Sequence[str] | None
) -> Optional[dict]:
    """좁힌 사실을 기록한다 — **미조회 범위를 남기지 않으면 침묵 절단이다**.

    범위 축소는 정보 손실이 복구되지 않는 절단이므로, 무엇을 보지 않았는지가 응답과
    감사 로그 양쪽에 남아야 한다. 전부 선택했으면 절단이 아니므로 None.

    Returns:
        `{"selected": [...], "skipped": [...], "skipped_db_ids": [...]}` 또는 None.
    """
    billable = _distinct_groups(groups)
    if not billable or not selected_db_ids:
        return None
    chosen = set(selected_db_ids)
    selected: list[str] = []
    skipped: list[str] = []
    skipped_db_ids: list[str] = []
    for group in billable:
        label = str(group.get("label") or group.get("group_key"))
        db_ids = list(group.get("db_ids") or [])
        if any(d in chosen for d in db_ids):
            selected.append(label)
        else:
            skipped.append(label)
            skipped_db_ids.extend(db_ids)
    if not skipped:
        return None
    return {"selected": selected, "skipped": skipped, "skipped_db_ids": skipped_db_ids}


def render_narrowed_note(record: Mapping[str, Any] | None) -> str:
    """응답 말미에 붙일 미조회 범위 문구(없으면 빈 문자열)."""
    if not record or not record.get("skipped"):
        return ""
    skipped = ", ".join(record["skipped"])
    selected = ", ".join(record.get("selected") or []) or "선택한 범위"
    return (
        f"- {selected}만 조회했습니다. **{skipped}은(는) 조회하지 않았습니다** — "
        "전체 범위로 다시 조회하려면 아래 버튼을 눌러 주세요."
    )
