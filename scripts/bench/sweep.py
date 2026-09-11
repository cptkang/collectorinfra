"""설정 축 스위프 — 94의 실행 원자 위에서 arm을 돌리고 축별로 비교한다 (plans/93 §4 · §1.5).

**러너를 다시 만들지 않는다.** 서버 기동·주입 에코 검증·시나리오 실행·원시 적재는
`scripts/scenario/`(plans/94 · D-212)가 이미 한다. 93이 더하는 것은 셋뿐이다.

    1. 축 → arm 전개      (`axes.expand_ofat`)
    2. arm을 프로파일로 주입 (94 `Catalog.profiles`는 이름→env 딕셔너리다 — arm과 같은 모양)
    3. 축별 **쌍체 비교**   (같은 시나리오 id 단위로 baseline과 대조 — `compare.py`)

경계(§1.5): 시나리오 카탈로그·실행 원자·판정기는 **94 소유**이고 93은 소비한다.
R군(복합·오용·실수·착각)은 쓰지 않는다 — 대응 등급은 설정의 함수가 아니라 프롬프트·가드의
함수라 arm 간 비교의 신호가 되지 못한다.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.bench import axes as axes_mod

BASELINE_ARM = "baseline"


class SweepUnavailable(RuntimeError):
    """94 하네스가 없어 스위프를 돌릴 수 없다."""


@dataclass(frozen=True)
class ArmSpec:
    """arm 1개 = 프로파일 1개 = 서버 기동 1회."""

    arm_id: str
    axis: Optional[str]
    level: Optional[str]
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    """시나리오 1건 실행 1회의 관측치 — 축 비교에 필요한 것만 추린다."""

    arm_id: str
    scenario_id: str
    repeat: int
    passed: bool
    wall_ms: Optional[float]
    llm_calls: Optional[int]
    tokens: Optional[int]
    retries: Optional[int]
    manual: bool = False        # mock 판정 보류 — 정확도 비교에서 제외한다


def scenario_harness():
    """94 하네스를 늦게 임포트한다.

    93은 94 없이도 **트랙 T(설정 검증)가 완결**되므로, 임포트 실패를 모듈 로드 시점의
    치명상으로 만들지 않는다. 스위프를 실제로 부를 때만 필요하다.
    """
    try:
        from scripts.scenario import catalog as sc_catalog
        from scripts.scenario import runner as sc_runner
    except Exception as exc:  # pragma: no cover - 환경 의존
        raise SweepUnavailable(
            "성능 스위프는 `scripts/scenario/`(plans/94)의 실행 원자 위에 섭니다. "
            f"임포트 실패: {type(exc).__name__}: {exc}"
        ) from exc
    return sc_catalog, sc_runner


def build_arms(
    selected_axes: Optional[Sequence[axes_mod.AxisCandidate]] = None,
    *,
    tier: str = "primary",
    limit: Optional[int] = None,
) -> list[ArmSpec]:
    """축에서 arm을 전개한다. 기본은 1차 축(동작 모드)만."""
    if selected_axes is None:
        found, _ = axes_mod.select_axes()
        selected_axes = [a for a in found if tier == "all" or a.tier == tier]
    if limit:
        selected_axes = list(selected_axes)[:limit]
    return [
        ArmSpec(arm_id=raw["arm_id"], axis=raw["axis"], level=raw["level"], env=dict(raw["env"]))
        for raw in axes_mod.expand_ofat(list(selected_axes), baseline_label=BASELINE_ARM)
    ]


def load_normal_catalog(*, env: str = "sandbox"):
    """94 카탈로그에서 **정상 군만** 고른 사본을 만든다.

    R군을 빼는 것이 계약이다(§1.5) — 그것은 94가 답하는 질문이지 93이 답하는 질문이 아니다.
    """
    sc_catalog, _ = scenario_harness()
    catalog = sc_catalog.load_catalog()
    normal = catalog.select(kinds=["normal"], env=env)
    return sc_catalog.Catalog(
        groups=dict(catalog.groups),
        scenarios=list(normal),
        profiles={},   # arm으로 채운다
    )


def fanout_scenarios(catalog, arms: Sequence[ArmSpec]):
    """**같은 시나리오를 모든 arm에 복제**한다 — 쌍체 비교의 전제다.

    94는 시나리오가 자기 프로파일을 지정한다(`Scenario.profile`, 기본 `baseline`).
    그 설계는 94의 질문(*"이 기능이 이 설정에서 동작하나"*)에 맞지만, 93의 질문
    (*"같은 질의가 설정 A와 B에서 어떻게 다른가"*)에는 맞지 않는다 — 그대로 두면
    baseline arm만 돌고 나머지는 대상 시나리오가 0건이 된다(2026-09-11 실측).

    그래서 시나리오를 arm 수만큼 복제하며 `profile`만 갈아끼운다. 시나리오 id는 유지해야
    쌍이 맺어지므로 **id는 건드리지 않는다**.
    """
    import dataclasses

    expanded = []
    for arm in arms:
        for scenario in catalog.scenarios:
            expanded.append(dataclasses.replace(scenario, profile=arm.arm_id))
    return expanded


def run_arms(
    arms: Sequence[ArmSpec],
    *,
    mode: str = "mock",
    env: str = "sandbox",
    repeat: int = 1,
    run_id: str = "",
    groups: Optional[list[str]] = None,
) -> dict[str, Any]:
    """arm 전체를 94 러너로 돌린다. 산출은 94 형식 그대로다(재분석 호환)."""
    sc_catalog, sc_runner = scenario_harness()
    catalog = load_normal_catalog(env=env)
    catalog.profiles = {arm.arm_id: dict(arm.env) for arm in arms}
    catalog.scenarios = fanout_scenarios(catalog, arms)

    config = sc_runner.RunConfig(
        mode=mode,
        env=env,
        repeat=repeat,
        groups=list(groups or []),
        profiles=[arm.arm_id for arm in arms],
        run_id=run_id,
    )
    return sc_runner.execute(catalog, config)


def read_observations(raw_path: Path) -> list[Observation]:
    """94의 `raw.jsonl`을 축 비교용 관측치로 옮긴다.

    **턴 단위가 아니라 시나리오 단위**로 접는다 — 멀티턴 시나리오는 턴마다 행이 생기는데
    축 비교의 단위는 "이 시나리오가 통과했는가"다. 한 턴이라도 실패하면 실패로 본다.
    """
    folded: dict[tuple[str, str, int], dict[str, Any]] = {}
    if not raw_path.exists():
        return []
    for line in raw_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = (str(row.get("profile")), str(row.get("scenario_id")), int(row.get("repeat", 0)))
        cell = folded.setdefault(key, {"passed": True, "manual": False, "wall_ms": 0.0,
                                       "llm_calls": 0, "tokens": 0, "retries": 0, "seen": 0})
        cell["seen"] += 1
        verdict = str(row.get("func_verdict"))
        if verdict == "manual":
            # mock 모드는 실제 응답이 없어 판정을 사람에게 남긴다 — 정확도 비교의 재료가 아니다.
            cell["manual"] = True
        elif verdict != "pass":
            cell["passed"] = False
        for src, dst in (("wall_ms", "wall_ms"), ("llm_calls", "llm_calls"),
                         ("tokens", "tokens"), ("retries", "retries")):
            value = row.get(src)
            if isinstance(value, (int, float)):
                cell[dst] += value

    return [
        Observation(
            arm_id=arm, scenario_id=scenario, repeat=repeat,
            passed=bool(cell["passed"]),
            manual=bool(cell["manual"]),
            wall_ms=cell["wall_ms"] or None,
            llm_calls=int(cell["llm_calls"]) or None,
            tokens=int(cell["tokens"]) or None,
            retries=int(cell["retries"]) or None,
        )
        for (arm, scenario, repeat), cell in sorted(folded.items())
    ]


def group_by_arm(observations: Iterable[Observation]) -> dict[str, list[Observation]]:
    grouped: dict[str, list[Observation]] = {}
    for obs in observations:
        grouped.setdefault(obs.arm_id, []).append(obs)
    return grouped
