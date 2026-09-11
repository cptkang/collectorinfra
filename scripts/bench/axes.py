"""축 자동 선별과 arm 전개 (plans/93 §3.2·§3.3).

**개발자가 축을 고르지 않는다**(§0.4). 카탈로그에서 코드가 뽑는다.

두 단계로 좁힌다.

    F1  카탈로그 기반 제외 — `catalog.f1_filter` (미소비·크레덴셜·접속정보·비질의 그룹)
    F2  영향 경로 판정     — 아래 네 경로 중 하나에 닿는 것만 축이 된다

영향 경로는 넷뿐이다(§3.2): **LLM 호출 수 · DB 왕복 수 · 프롬프트 길이 · 재시도 예산**.
이 넷 중 어디에도 닿지 않으면 성능이 달라질 이유가 없다.

판정 근거는 항상 함께 낸다(`--explain`) — 자동 선별에 이의가 있을 때 근거 없이는 다툴 수 없다.

**한계**: 판정은 이름 신호이지 코드 정적 분석이 아니다. 이름이 역할을 드러내지 않는 노브는 놓친다
(실측 사례: `COMPOSITE_MAX_TARGETS`가 fan-out 대상 수인데 어휘에 `target`이 없어 빠졌다 —
계획서 §3.2의 손으로 고른 기대 표와 대조해서 발견했다). 그래서 기대 표와의 **차이 자체를 회귀 감시**
대상으로 두고(`test_bench_axes.py`), 자동 선별이 그 표를 대체한다고 보지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from scripts.bench import catalog

#: 영향 경로 판정용 어휘. 이름이 곧 신호다 — 코드 정적 분석까지는 하지 않는다(§3.2 주석).
_PATH_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("llm_calls", (
        "candidate", "selection", "multi_candidate", "two_stage", "llm_assist",
        "complexity_gate", "semantic_compose", "rerank", "reflect", "replan",
        "llm_enabled", "annotation", "inference",
    )),
    ("db_roundtrips", (
        "cache", "prefetch", "precheck", "discovery", "fanout", "concurrency",
        "probe", "postcheck", "sequential", "prior_scope", "batch", "sweep",
        # `max_targets`는 fan-out 대상 수라 왕복에 직접 영향하는데 위 어휘로는 안 잡힌다
        # (2026-09-11 실측 — 이름 신호 선별의 한계 사례. §3.2 기대 표와 대조해서 발견했다).
        "target", "zone", "db_ids",
    )),
    ("prompt_size", (
        "token_budget", "max_input", "context", "few_shot", "fewshot", "example",
        "knowledge", "profile", "hint", "schema_detail", "truncate", "top_k", "limit",
    )),
    ("retry_budget", (
        "retry", "max_attempt", "fallback", "timeout", "budget", "threshold", "recursion",
    )),
)


#: 상한(cap)·예산 성격의 이름. 정상 경로에서는 발동하지 않으므로 **1차 축이 아니다**.
#: 타임아웃을 줄이면 빨라지는 것이 아니라 실패가 는다 — 성능 레버로 오해하기 쉬운 대표 유형이다.
_CAP_MARKERS: tuple[str, ...] = (
    "timeout", "_max_", "max_", "_limit", "limit_", "threshold", "maxlen", "recursion",
    "retention", "budget", "expire", "interval", "ttl",
)


def _is_cap(knob: catalog.KnobSpec) -> bool:
    haystack = f"{knob.env_key} {knob.field_name}".lower()
    return any(m in haystack for m in _CAP_MARKERS)


@dataclass(frozen=True)
class AxisCandidate:
    """축 후보 1건과 **왜 축인지**."""

    env_key: str
    group_key: str
    type: str
    levels: tuple[str, ...]
    impact_paths: tuple[str, ...]
    rationale: str
    tier: str = "primary"   # primary(동작 모드) | secondary(상한·예산)


@dataclass(frozen=True)
class AxisDecision:
    """선별 판정 1건 — 포함이든 제외든 사유가 붙는다."""

    env_key: str
    included: bool
    stage: str      # F1 | F2
    reason: str


def _impact_paths(knob: catalog.KnobSpec) -> tuple[str, ...]:
    """이 노브가 닿는 영향 경로."""
    haystack = f"{knob.env_key} {knob.field_name}".lower()
    hits = [name for name, markers in _PATH_MARKERS if any(m in haystack for m in markers)]
    return tuple(hits)


def _levels_of(knob: catalog.KnobSpec) -> tuple[str, ...]:
    """이 축이 시도할 값들. 기준선은 러너가 따로 잡으므로 여기는 **변이만** 낸다."""
    if knob.enum_choices:
        return tuple(knob.enum_choices)
    if knob.type == "bool":
        return ("true", "false")
    if knob.type == "int":
        base = _as_int(knob.default)
        if base is None:
            return ("1", "3")
        return tuple(str(v) for v in sorted({max(0, base // 2), base, base * 2}) if v >= 0)
    if knob.type == "float":
        base = _as_float(knob.default)
        if base is None:
            return ("1.0", "2.0")
        return tuple(str(round(v, 3)) for v in sorted({base / 2, base, base * 2}))
    return ()


def _as_int(text: Optional[str]) -> Optional[int]:
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return None


def _as_float(text: Optional[str]) -> Optional[float]:
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return None


def select_axes(
    knobs: Optional[Iterable[catalog.KnobSpec]] = None,
) -> tuple[list[AxisCandidate], list[AxisDecision]]:
    """축을 자동으로 뽑고 전 판정을 함께 돌려준다."""
    knobs = list(knobs) if knobs is not None else catalog.load_knobs()
    kept, dropped = catalog.f1_filter(knobs)

    decisions: list[AxisDecision] = [
        AxisDecision(env_key=d.env_key, included=False, stage="F1", reason=d.reason)
        for d in dropped
    ]
    axes: list[AxisCandidate] = []

    for knob in kept:
        paths = _impact_paths(knob)
        if not paths:
            decisions.append(AxisDecision(
                env_key=knob.env_key, included=False, stage="F2",
                reason="영향 경로 없음 — LLM 호출·DB 왕복·프롬프트 길이·재시도 예산 어디에도 닿지 않는다",
            ))
            continue
        levels = _levels_of(knob)
        if len(levels) < 2:
            decisions.append(AxisDecision(
                env_key=knob.env_key, included=False, stage="F2",
                reason=f"비교할 값이 부족하다(타입 {knob.type} · 후보 {len(levels)}개)",
            ))
            continue
        tier = "secondary" if _is_cap(knob) else "primary"
        if tier == "secondary":
            rationale = (f"영향 경로 {'·'.join(paths)} · **상한·예산** — 정상 경로에서는 발동하지 않아 2차 축")
        else:
            rationale = f"영향 경로 {'·'.join(paths)} — 동작 모드를 바꾼다"
        axes.append(AxisCandidate(
            env_key=knob.env_key, group_key=knob.group_key, type=knob.type,
            levels=levels, impact_paths=paths, rationale=rationale, tier=tier,
        ))
        decisions.append(AxisDecision(
            env_key=knob.env_key, included=True, stage="F2", reason=rationale))

    axes.sort(key=lambda a: (a.tier != "primary", -len(a.impact_paths), a.env_key))
    return axes, decisions


def expand_ofat(axes: Sequence[AxisCandidate], *, baseline_label: str = "baseline") -> list[dict]:
    """OFAT arm 전개 — 기준선에서 축 하나씩만 움직인다(§3.3 S2).

    전수(full factorial)는 쓰지 않는다. 축 12개만 해도 4096 arm이다.
    """
    arms: list[dict] = [{"arm_id": baseline_label, "axis": None, "level": None, "env": {}}]
    for axis in axes:
        for level in axis.levels:
            arms.append({
                "arm_id": f"S2-{axis.env_key}-{level}",
                "axis": axis.env_key,
                "level": level,
                "env": {axis.env_key: level},
            })
    return arms


def render_explain(decisions: Sequence[AxisDecision], *, only_included: bool = False) -> str:
    """`--explain` 출력 — 각 키가 왜 포함·제외됐는지 한 줄씩."""
    rows = [d for d in decisions if d.included] if only_included else list(decisions)
    rows.sort(key=lambda d: (not d.included, d.stage, d.env_key))
    lines = ["| 키 | 판정 | 단계 | 사유 |", "|---|---|---|---|"]
    lines += [
        f"| `{d.env_key}` | {'축' if d.included else '제외'} | {d.stage} | {d.reason} |"
        for d in rows
    ]
    return "\n".join(lines) + "\n"


def to_yaml(axes: Sequence[AxisCandidate]) -> str:
    """`config/bench/axes.yaml` 생성물.

    **자동 생성물이다**(§7.1) — 사람이 유지하는 파일이 아니라 검토·동결용이다.
    의존을 늘리지 않으려고 PyYAML 없이 쓴다(구조가 단순해 손으로 충분하다).
    """
    lines = [
        "# 자동 생성물 — 손으로 고치지 않는다 (plans/93 §7.1)",
        "# 재생성: python -m scripts.bench.axes --write",
        "axes:",
    ]
    for axis in axes:
        lines += [
            f"  - id: {axis.env_key}",
            f"    group: {axis.group_key}",
            f"    type: {axis.type}",
            f"    impact: [{', '.join(axis.impact_paths)}]",
            f"    levels: [{', '.join(axis.levels)}]",
            f"    tier: {axis.tier}",
            f"    rationale: \"{axis.rationale}\"",
        ]
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m scripts.bench.axes",
                                     description="축 자동 선별 (plans/93 §3.2)")
    parser.add_argument("--explain", action="store_true", help="전 판정 근거 출력")
    parser.add_argument("--all-tiers", action="store_true", help="2차 축(상한·예산)도 포함")
    parser.add_argument("--write", action="store_true", help="config/bench/axes.yaml 생성")
    args = parser.parse_args(argv)

    axes, decisions = select_axes()
    primary = [a for a in axes if a.tier == "primary"]
    secondary = [a for a in axes if a.tier != "primary"]
    if args.all_tiers:
        shown = axes
    else:
        shown = primary
    print(f"축 {len(shown)}개 (1차 {len(primary)} · 2차 {len(secondary)}) · 판정 {len(decisions)}건")
    if args.explain:
        print(render_explain(decisions))
    else:
        for axis in shown:
            mark = " " if axis.tier == "primary" else "~"
            print(f" {mark}{axis.env_key:46s} {'·'.join(axis.impact_paths):30s} {list(axis.levels)}")
        if not args.all_tiers and secondary:
            print(f"\n  2차 축 {len(secondary)}개는 상한·예산이라 기본 스위프에서 제외한다(--all-tiers로 포함).")
    if args.write:
        from pathlib import Path
        out = Path(__file__).resolve().parent.parent.parent / "config" / "bench" / "axes.yaml"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(to_yaml(axes), encoding="utf-8")
        print(f"생성: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
