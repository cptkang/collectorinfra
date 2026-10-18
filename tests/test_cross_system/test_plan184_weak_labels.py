"""plans/184 M6 — 보조 컬럼만 일치한 시스템이 여럿이면 표기를 모두 나열한다.

종전에는 첫 시스템의 `weak_label`만 문장에 실렸다. 새 동작은 표기 집합을 시스템 순서(계획의
`others` 순서)로 겹침 없이 나열하고, 표기가 하나면 종전 문장과 바이트 동일하다.
"""

from __future__ import annotations

from src.domain.host_discovery import (
    ACTION_KEEP,
    ROW_WEAK_ELSEWHERE,
    ProbePlan,
    SystemProbe,
    decide_systems,
)

_IDS = ("svr-01",)


def _probe(system: str, *, weak_label: str | None = None) -> SystemProbe:
    if weak_label is None:  # 소유 시스템 — 미발견
        return SystemProbe(system=system, label=system, identifiers=_IDS, probed_db_ids=("d0",))
    return SystemProbe(
        system=system, label=system, identifiers=_IDS, probed_db_ids=(f"{system}1",),
        hits={"svr-01": (f"{system}1",)}, weak_only=_IDS, weak_label=weak_label,
    )


def _decide(others: tuple[str, ...], labels: dict[str, str]):
    plan = ProbePlan(mode="single", required=("own",), others=others)
    probes = {"own": _probe("own")}
    probes.update({s: _probe(s, weak_label=labels[s]) for s in others})
    return decide_systems(plan, probes)


def test_distinct_weak_labels_all_listed_in_system_order() -> None:
    d = _decide(("sys_b", "sys_a"), {"sys_b": "자산명", "sys_a": "등록명"})
    assert (d.action, d.row) == (ACTION_KEEP, ROW_WEAK_ELSEWHERE)
    assert "sys_b, sys_a에서는 자산명, 등록명 일치만 있어 " in d.message


def test_duplicate_weak_labels_listed_once() -> None:
    d = _decide(
        ("s1", "s2", "s3"), {"s1": "등록명", "s2": "자산명", "s3": "등록명"},
    )
    assert "s1, s2, s3에서는 등록명, 자산명 일치만 있어 " in d.message


def test_single_label_message_unchanged() -> None:
    """표기가 하나면 종전 문장 그대로다(시스템이 여럿이어도)."""
    d = _decide(("s1", "s2"), {"s1": "등록명", "s2": "등록명"})
    assert d.message == (
        "own에서 찾지 못했습니다: svr-01. "
        "s1, s2에서는 등록명 일치만 있어 호스트명 기준 등록으로 보지 않았습니다 — "
        "조회를 막지 않습니다."
    )
