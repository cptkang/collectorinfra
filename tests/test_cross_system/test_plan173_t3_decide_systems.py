"""`decide_systems` 결정표 판정 특성화 — 입력 조합 × 출력 전 필드 골든 (plans/173 T3 · 166 I5).

plans/173 W1 T3 특성화 스냅샷 — Extract Function 전에 채록했다. 기대값은 「맞는 값」이 아니라
「지금 값」이다(D-337 ⑤). 리팩터 뒤 스냅샷을 갱신해야 한다면 그 변경은 리팩터가 아니다.

입력 매트릭스:

- 계획(`ProbePlan`) 7종 — SINGLE·BOTH·AMBIGUOUS × 시스템 2개·3개(BOTH는 필요 3개와 필요 2 + 다른 1)
- 시스템별 확인 결과 변형(`_VARIANTS`) — 발견(전부·일부+실패 DB·보조 컬럼뿐·보조+키 혼합) ·
  미발견(라벨 있음·없음) · 확인 실패(DB 오류·시스템 사유·조회 DB 0·전부 미확인 키) · 미조회(부재)
- 시스템 2개 계획은 전 변형의 곱, 3개 계획은 축소 변형(`_REDUCED`)의 곱
- 샘플 개수 기본(10)과 1(「외 N건」 절단) — 1은 `single_a|b` 계획에만

출력: `SystemDecision` 전 필드(action·row·systems·db_ids·message·unverified).

스냅샷 (재)채록: `UPDATE_PLAN173_T3_SNAPSHOT=1 python -m pytest <이 파일> -q -p no:cacheprovider`
"""

from __future__ import annotations

import itertools
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.domain.host_discovery import (
    DEFAULT_SAMPLE,
    MODE_AMBIGUOUS,
    MODE_BOTH,
    MODE_SINGLE,
    ProbePlan,
    SystemProbe,
    decide_systems,
)

SNAPSHOT_PATH = (
    Path(__file__).resolve().parent.parent / "snapshots" / "plan173_t3" / "decide_systems.json"
)
_UPDATE_ENV = "UPDATE_PLAN173_T3_SNAPSHOT"

IDS: tuple[str, ...] = ("h-01", "h-02", "h-03")


def _found_full(s: str) -> SystemProbe:
    return SystemProbe(
        system=s, label=f"{s}시스템", identifiers=IDS, probed_db_ids=(f"{s}1", f"{s}2"),
        hits={i: (f"{s}1",) for i in IDS},
    )


def _found_partial(s: str) -> SystemProbe:
    # 일부 식별자만 발견 + 한 DB 실패 — 상세 문장(찾지 못한 대상·확인하지 못한 DB) 경로
    return SystemProbe(
        system=s, label=f"{s}시스템", identifiers=IDS, probed_db_ids=(f"{s}1", f"{s}2"),
        hits={"h-01": (f"{s}2",)}, errors={f"{s}1": "Timeout"},
        db_labels={f"{s}1": f"{s}-존1"},
    )


def _found_weak(s: str) -> SystemProbe:
    return SystemProbe(
        system=s, label=f"{s}시스템", identifiers=IDS, probed_db_ids=(f"{s}1",),
        hits={"h-01": (f"{s}1",)}, weak_only=("h-01",), weak_label="등록명",
    )


def _found_mixed(s: str) -> SystemProbe:
    # 보조 컬럼 일치 + 키 컬럼 일치 — strong_hits가 비지 않는다
    return SystemProbe(
        system=s, label=f"{s}시스템", identifiers=IDS, probed_db_ids=(f"{s}1",),
        hits={"h-01": (f"{s}1",), "h-02": (f"{s}1",)}, weak_only=("h-01",),
    )


def _not_found(s: str) -> SystemProbe:
    return SystemProbe(system=s, label=f"{s}시스템", identifiers=IDS, probed_db_ids=(f"{s}1",))


def _not_found_nolabel(s: str) -> SystemProbe:
    return SystemProbe(system=s, identifiers=IDS, probed_db_ids=(f"{s}1", f"{s}2"))


def _unv_error(s: str) -> SystemProbe:
    return SystemProbe(
        system=s, label=f"{s}시스템", identifiers=IDS, probed_db_ids=(f"{s}1",),
        errors={f"{s}1": "ConnErr"},
    )


def _unv_system(s: str) -> SystemProbe:
    return SystemProbe(
        system=s, label=f"{s}시스템", identifiers=IDS, errors={"": "키 매니페스트 없음"},
    )


def _unv_noprobe(s: str) -> SystemProbe:
    return SystemProbe(system=s, label=f"{s}시스템", identifiers=IDS)


def _unv_unchecked(s: str) -> SystemProbe:
    return SystemProbe(
        system=s, label=f"{s}시스템", identifiers=IDS, probed_db_ids=(f"{s}1",), unchecked=IDS,
    )


_VARIANTS: dict[str, Callable[[str], SystemProbe] | None] = {
    "found_full": _found_full,
    "found_partial": _found_partial,
    "found_weak": _found_weak,
    "found_mixed": _found_mixed,
    "not_found": _not_found,
    "not_found_nolabel": _not_found_nolabel,
    "unv_error": _unv_error,
    "unv_system": _unv_system,
    "unv_noprobe": _unv_noprobe,
    "unv_unchecked": _unv_unchecked,
    "absent": None,  # probes 매핑에 없다(미조회)
}
_REDUCED: tuple[str, ...] = ("found_partial", "found_weak", "not_found", "unv_error", "absent")

_PLANS: dict[str, ProbePlan] = {
    "single_a|b": ProbePlan(mode=MODE_SINGLE, required=("a",), others=("b",)),
    "single_a|bc": ProbePlan(mode=MODE_SINGLE, required=("a",), others=("b", "c")),
    "both_ab": ProbePlan(mode=MODE_BOTH, required=("a", "b")),
    "both_abc": ProbePlan(mode=MODE_BOTH, required=("a", "b", "c")),
    "both_ab|c": ProbePlan(mode=MODE_BOTH, required=("a", "b"), others=("c",)),
    "amb_a|b": ProbePlan(mode=MODE_AMBIGUOUS, required=("a",), others=("b",)),
    "amb_a|bc": ProbePlan(mode=MODE_AMBIGUOUS, required=("a",), others=("b", "c")),
}


def _cases() -> list[tuple[str, ProbePlan, dict[str, SystemProbe], int]]:
    out: list[tuple[str, ProbePlan, dict[str, SystemProbe], int]] = []
    for name, plan in _PLANS.items():
        systems = plan.required + plan.others
        pool = tuple(_VARIANTS) if len(systems) == 2 else _REDUCED
        samples = (DEFAULT_SAMPLE, 1) if name == "single_a|b" else (DEFAULT_SAMPLE,)
        for combo in itertools.product(pool, repeat=len(systems)):
            probes: dict[str, SystemProbe] = {}
            for system, variant in zip(systems, combo):
                make = _VARIANTS[variant]
                if make is not None:
                    probes[system] = make(system)
            for sample in samples:
                key = f"{name}|n={sample}|" + ",".join(
                    f"{s}={v}" for s, v in zip(systems, combo)
                )
                out.append((key, plan, probes, sample))
    return out


def _observe() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, plan, probes, sample in _cases():
        if sample == DEFAULT_SAMPLE:
            d = decide_systems(plan, probes)
        else:
            d = decide_systems(plan, probes, sample=sample)
        result[key] = [
            d.action, d.row, list(d.systems), list(d.db_ids), d.message, list(d.unverified),
        ]
    return result


def test_decide_systems_matches_snapshot() -> None:
    """입력 매트릭스 전 케이스의 판정이 채록 시점과 같다(필드 순서: action·row·systems·db_ids·
    message·unverified)."""
    observed = _observe()
    if os.environ.get(_UPDATE_ENV) == "1":
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        lines = ",\n".join(
            f" {json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)}"
            for k, v in observed.items()
        )
        SNAPSHOT_PATH.write_text("{\n" + lines + "\n}\n", encoding="utf-8")
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert set(observed) == set(expected)
    diff = [k for k in expected if observed[k] != expected[k]]
    assert not diff, "\n".join(
        f"{k}\n  기대 {expected[k]}\n  실제 {observed[k]}" for k in diff[:10]
    )


def test_matrix_reaches_every_row_and_action() -> None:
    """매트릭스가 결정표 전 행과 세 동작을 모두 지난다(입력 축 축소 회귀 방지)."""
    observed = _observe()
    rows = {v[1] for v in observed.values()}
    actions = {v[0] for v in observed.values()}
    assert rows == {
        "owner_found", "owner_missing_elsewhere", "owner_missing_weak_elsewhere", "all_found",
        "partial_found", "ambiguous_one_found", "ambiguous_both_found", "unverified",
        "none_found",
    }
    assert actions == {"query", "halt", "keep"}
