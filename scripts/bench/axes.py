"""축 자동 선별과 arm 전개 (plans/93 §3.2·§3.3).

**개발자가 축을 고르지 않는다**(§0.4). 카탈로그에서 코드가 뽑는다.

두 단계로 좁힌다.

    F1  카탈로그 기반 제외 — `catalog.f1_filter` (미소비·크레덴셜·접속정보·비질의 그룹)
    F2  영향 경로 판정     — 아래 네 경로 중 하나에 닿는 것만 축이 된다

영향 경로는 넷뿐이다(§3.2): **LLM 호출 수 · DB 왕복 수 · 프롬프트 길이 · 재시도 예산**.
이 넷 중 어디에도 닿지 않으면 성능이 달라질 이유가 없다.

판정 근거는 항상 함께 낸다(`--explain`) — 자동 선별에 이의가 있을 때 근거 없이는 다툴 수 없다.

**한계 ①(이름 신호)**: 판정은 이름 신호이지 코드 정적 분석이 아니다. 이름이 역할을 드러내지 않는
노브는 놓친다 (실측 사례: `COMPOSITE_MAX_TARGETS`가 fan-out 대상 수인데 어휘에 `target`이 없어
빠졌다 — 계획서 §3.2의 손으로 고른 기대 표와 대조해서 발견했다). 그래서 기대 표와의 **차이 자체를
회귀 감시** 대상으로 두고(`test_bench_axes.py`), 자동 선별이 그 표를 대체한다고 보지 않는다.

**한계 ②(설정 표면 밖)** *(2026-09-21 추가)*: 축 후보는 `catalog.load_knobs()`가 돌려주는
**`AppConfig` 필드로 한정된다**(pydantic 인트로스펙션 · D-129). 코드 상수로 박힌 값은 위 영향 경로
넷 중 하나에 닿더라도 **축이 되지 않는다** — 이름 문제가 아니라 애초에 노브가 아니라서다.
실사례: `src/graph.py:63` `NON_SQL_RETRY_BUDGET = 1`은 재시도 예산(영향 경로 `retry_budget`)인데
모듈 상수라 후보에 오르지 않는다. 같은 계열의 `src/config.py:188` `max_retry_count`는 설정이라
축이 된다(D-099). **이 한계는 여기서 고치지 않는다** — 설정 표면을 늘리는 것은 노브를 줄이려는
이 계획의 방향과 별개 판단이고, 승격 여부는 재측정 뒤에 정한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence

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
    """축 후보 1건과 **왜 축인지**.

    `env_key` 는 **축 id** 다. 단일 키 축에서는 그것이 곧 env 키이고, 다중 키 축
    (`109·CS-31` X1)에서는 env 키가 아니라 대표 이름이다 — 실제 주입은 `env_for()` 가 낸다.
    """

    env_key: str
    group_key: str
    type: str
    levels: tuple[str, ...]
    impact_paths: tuple[str, ...]
    rationale: str
    tier: str = "primary"   # primary(동작 모드) | secondary(상한·예산)
    #: **다중 키 축**: 레벨 이름 → 그 레벨이 주입할 env 전체(키 여럿).
    #: 비어 있으면 단일 키 축이고 `{env_key: level}` 하나를 주입한다.
    #: 튜플로 두는 이유는 `frozen=True` 데이터클래스의 필드를 해시 가능하게 유지하기 위해서다.
    level_env: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = ()
    #: 구조 축인가 — 노드 집합 자체를 바꿔 **다른 축의 효과를 조건부로 만든다**(plans/114 M-0).
    #: 캠페인은 구조 축을 첫 구간에 둔다.
    structural: bool = False

    @property
    def multi_key(self) -> bool:
        return bool(self.level_env)

    @property
    def env_keys(self) -> tuple[str, ...]:
        """이 축이 실제로 움직이는 env 키 전부."""
        if not self.level_env:
            return (self.env_key,)
        seen: list[str] = []
        for _, pairs in self.level_env:
            for key, _value in pairs:
                if key not in seen:
                    seen.append(key)
        return tuple(seen)

    def env_for(self, level: str) -> dict[str, str]:
        """이 레벨이 주입할 env. 단일 키 축이면 `{env_key: level}` 이다.

        **모르는 레벨에는 빈 딕셔너리를 돌려주지 않는다** — 빈 주입은 곧 기준선이라
        조용히 A/A arm 이 된다. 다중 키 축에서 모르는 레벨은 호출부의 버그이므로 드러낸다.
        """
        if not self.level_env:
            return {self.env_key: level}
        for name, pairs in self.level_env:
            if name == level:
                return {key: value for key, value in pairs}
        raise KeyError(f"축 `{self.env_key}` 에 레벨 `{level}` 이 없다 — 레벨: {self.levels}")


def multi_key_axis(
    axis_id: str,
    *,
    group_key: str,
    levels: Mapping[str, Mapping[str, str]],
    impact_paths: Sequence[str],
    rationale: str,
    structural: bool = False,
) -> AxisCandidate:
    """레벨 → env 매핑으로 다중 키 축 1개를 만든다(`109·CS-31` X1 의 일반 기제).

    레벨 순서는 **호출부가 준 순서 그대로** 둔다 — 첫 레벨이 표·계획에서 먼저 보인다.
    """
    return AxisCandidate(
        env_key=axis_id, group_key=group_key, type="multi",
        levels=tuple(levels), impact_paths=tuple(impact_paths), rationale=rationale,
        level_env=tuple((name, tuple(sorted(env.items()))) for name, env in levels.items()),
        structural=structural,
    )


@dataclass(frozen=True)
class AxisDecision:
    """선별 판정 1건 — 포함이든 제외든 사유가 붙는다."""

    env_key: str
    included: bool
    stage: str      # F0(구조 축) | F1 | F2
    reason: str


# ── 구조 축 — 사다리 단 (plans/114 M-0 · D-250 ① · `109·CS-31` X1) ────────────


#: 사다리 단 축의 id. **env 키가 아니다** — 3키를 한 축으로 묶은 대표 이름이다.
LADDER_AXIS = "LADDER_TIER"

#: 사다리 3키. 이름 신호로는 어느 것도 영향 경로에 걸리지 않아 종전에는 축이 되지 못했다
#: (`plans/114` §4.1 M-0) — 그래서 벤치는 서버 `.env` 가 우연히 확정한 단을 기준선으로 뒀다.
LADDER_ENV_KEYS = ("ENABLE_DEEPAGENTS_PACKAGE", "ENABLE_INTENT_ORCHESTRATION",
                   "ENABLE_SEMANTIC_ROUTING")

#: 기본 레벨 = `config/scenarios/profiles.yaml` 의 arm 정의(`plans/110` 실행 가이드 ③).
#:
#: **1단(`deep_agent`)은 넣지 않는다** — 오케스트레이터 서빙(`ORCHESTRATOR_BASE_URL`)이 전제라
#: 불성립 시 조용히 하위 단으로 내려가 arm 이 무효가 된다(D-250 ①). 재려면 profiles.yaml 에
#: `ENABLE_DEEPAGENTS_PACKAGE: "true"` 인 프로파일(예: `tier1_deep`)을 더하고 이 목록에 그
#: 이름을 넣는다 — 그러면 레벨 3개짜리 축이 되고 구간 예산이 arm 4개로 늘어난다.
LADDER_LEVEL_PROFILES = ("tier2_intent", "tier3_router")

#: 사다리 축의 설정 카테고리 — 구간 이름이 `ladder-1` 이 된다.
LADDER_CATEGORY = "ladder"


class StructuralAxisUnavailable(RuntimeError):
    """구조 축 정의를 읽지 못했다. **조용히 빼지 않는다** — 사유를 들고 다닌다."""


def ladder_axis(profiles: Optional[Mapping[str, Mapping[str, str]]] = None) -> AxisCandidate:
    """사다리 단 축 1개. 레벨 값은 `config/scenarios/profiles.yaml` 을 **읽어서** 쓴다.

    사본을 두지 않는다(D-053) — 110 의 재테스트 arm 과 벤치 축이 같은 정의를 봐야
    *"두 하네스가 같은 단을 재고 있다"* 가 성립한다.

    정의를 읽지 못하거나 프로파일이 사다리 3키를 다 싣지 않으면 `StructuralAxisUnavailable`
    이다 — 축이 조용히 사라지면 캠페인이 종전처럼 `.env` 가 정한 단을 기준선으로 돈다.
    """
    if profiles is None:
        try:
            from scripts.scenario.catalog import load_profiles

            profiles = load_profiles()
        except Exception as exc:   # 하네스 부재·YAML 오류 — 사유를 들고 올라간다
            raise StructuralAxisUnavailable(
                f"`config/scenarios/profiles.yaml` 을 읽지 못했다 — "
                f"{type(exc).__name__}: {str(exc)[:200]}") from exc

    levels: dict[str, dict[str, str]] = {}
    for name in LADDER_LEVEL_PROFILES:
        env = profiles.get(name)
        if env is None:
            raise StructuralAxisUnavailable(
                f"프로파일 `{name}` 이 profiles.yaml 에 없다 — 레벨 정의를 사본으로 두지 않는다")
        missing = [k for k in LADDER_ENV_KEYS if k not in env]
        if missing:
            raise StructuralAxisUnavailable(
                f"프로파일 `{name}` 이 사다리 키 {', '.join(missing)} 를 싣지 않는다 — "
                "세 키를 모두 명시해야 tri-state 자동 결정이 배제된다(D-225 ④)")
        levels[name] = {k: str(env[k]) for k in LADDER_ENV_KEYS}

    return multi_key_axis(
        LADDER_AXIS, group_key=LADDER_CATEGORY, levels=levels,
        impact_paths=("llm_calls", "db_roundtrips", "prompt_size", "retry_budget"),
        rationale=(
            "**구조 축** — 사다리 3키를 한 축으로 전개한다(D-250 ①). 단은 노드 집합을 바꿔 "
            "다른 축의 효과를 조건부로 만들므로 캠페인 첫 구간에서 잰다. 레벨 정의는 "
            "`config/scenarios/profiles.yaml` 을 읽어 쓴다(사본 금지)"),
        structural=True,
    )


def structural_axes() -> tuple[list[AxisCandidate], list[AxisDecision]]:
    """구조 축 전부와 그 판정(F0). 현재는 사다리 단 하나다."""
    try:
        axis = ladder_axis()
    except StructuralAxisUnavailable as exc:
        return [], [AxisDecision(env_key=LADDER_AXIS, included=False, stage="F0",
                                 reason=f"구조 축 정의를 읽지 못했다 — {exc}")]
    return [axis], [AxisDecision(env_key=LADDER_AXIS, included=True, stage="F0",
                                 reason=axis.rationale)]


def find_axis(axis_id: str, candidates: Optional[Sequence[AxisCandidate]] = None
              ) -> Optional[AxisCandidate]:
    """축 id 로 축을 되찾는다 — 처분 제안이 다중 키 축을 실제 env 키로 펼칠 때 쓴다."""
    if candidates is None:
        candidates, _ = structural_axes()
    return next((a for a in candidates if a.env_key == axis_id), None)


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
    *,
    include_structural: Optional[bool] = None,
) -> tuple[list[AxisCandidate], list[AxisDecision]]:
    """축을 자동으로 뽑고 전 판정을 함께 돌려준다.

    카탈로그 밖의 **구조 축**(사다리 단 · F0)을 맨 앞에 붙인다. 그 축은 노브 하나가 아니라
    3키 묶음이라 F1·F2 선별을 지나지 않는다.

    `include_structural` 기본값은 **`knobs` 를 주지 않았을 때만 True** 다 — *"이 노브들로
    축을 뽑아라"* 라는 호출(단위 테스트·부분 검사)에 노브가 아닌 축을 끼워 넣지 않는다.
    """
    if include_structural is None:
        include_structural = knobs is None
    knobs = list(knobs) if knobs is not None else catalog.load_knobs()
    kept, dropped = catalog.f1_filter(knobs)

    structural, decisions = structural_axes() if include_structural else ([], [])
    decisions = list(decisions)
    decisions += [
        AxisDecision(env_key=d.env_key, included=False, stage="F1", reason=d.reason)
        for d in dropped
    ]
    axes: list[AxisCandidate] = []
    #: 구조 축이 이미 전개하는 키는 단일 키 축으로 **또** 뽑지 않는다 — 같은 설정을 두 축이
    #: 흔들면 구간이 갈리고 판정이 서로 모순된다.
    claimed = {key for axis in structural for key in axis.env_keys}

    for knob in kept:
        if knob.env_key in claimed:
            owner = next(a.env_key for a in structural if knob.env_key in a.env_keys)
            decisions.append(AxisDecision(
                env_key=knob.env_key, included=False, stage="F2",
                reason=f"구조 축 `{owner}` 이 다중 키로 전개한다 — 단일 키 축으로 중복 전개하지 않는다"))
            continue
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
    # 구조 축이 맨 앞이다 — 캠페인이 첫 구간으로 뽑는 것과 같은 순서를 화면에서도 본다.
    return structural + axes, decisions


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
                # 다중 키 축이면 여기서 키 여럿이 나온다(`env_for`). arm id 는 축 id·레벨로
                # 짓는다 — 키가 여럿이어도 쌍체 비교의 단위는 레벨 하나다.
                "env": axis.env_for(level),
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
        if axis.multi_key:
            # 다중 키 축은 **어느 키가 어떤 값이 되는지**가 보여야 검토가 된다.
            lines.append(f"    keys: [{', '.join(axis.env_keys)}]")
            lines.append("    structural: true" if axis.structural else "    structural: false")
            lines.append("    level_env:")
            for level in axis.levels:
                pairs = ", ".join(f"{k}: {v}" for k, v in sorted(axis.env_for(level).items()))
                lines.append(f"      {level}: {{{pairs}}}")
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
            mark = "*" if axis.structural else (" " if axis.tier == "primary" else "~")
            print(f" {mark}{axis.env_key:46s} {'·'.join(axis.impact_paths):30s} {list(axis.levels)}")
            if axis.multi_key:
                print(f"  {'':46s} 키 {', '.join(axis.env_keys)}")
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
