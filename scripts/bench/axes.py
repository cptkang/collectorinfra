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
from typing import Any, Iterable, Mapping, Optional, Sequence

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
    #: 소비처가 **전부** 이 단 전용 모듈이다(plans/118 B-3 ② · `TIER_EXCLUSIVE_MODULES`).
    #: 기준선 단이 이 단이 아니면 `sweep.ConfigSnapshot.unreachable_arms` 가 도달 불가로 뺀다.
    tier_only: Optional[str] = None
    consumers: tuple[str, ...] = ()

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
    stage: str      # F0(구조 축) | F1 | F2 | F3(소비처 · plans/118 B-3)
    reason: str
    #: F3 제외 축이 **제외되지 않았다면** 받았을 등급(primary·secondary). 캠페인이 같은 등급의
    #: 제외 축만 「미측정」 행으로 싣는다(118 B-3 ③). 다른 단계에서는 None.
    tier: Optional[str] = None


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


# ── 소비처 정적 판정 (plans/118 B-3) ─────────────────────────────────────


#: 소비처를 세는 패키지 — 설정을 읽어 **동작을 바꾸는** 코드가 사는 곳이다(`plans/118` §2.5).
#: 패키지 안의 `tests/`·`scripts/` 는 제품 동작이 아니므로 뺀다(arch_check 와 같은 경계).
CONSUMER_ROOTS: tuple[str, ...] = ("src", "noise_gate")

#: 필드명이 나와도 **소비처가 아닌** 모듈 — 저장소 루트 기준 경로.
#:
#: - `src/config.py` — 정의다.
#: - `src/observability/investigation_metrics.py` — 기동 로그 에코다(값을 읽지만 동작을 바꾸지
#:   않는다 · 118 §1 ①의 오인 원천).
#: - `src/api/settings_catalog.py` — 전 필드를 `getattr(holder, spec.field_name)` 로 **일괄**
#:   읽는 설정 화면·리로드 diff 인트로스펙션이다. 소비처로 세면 모든 필드가 「동적 접근」이
#:   되어 판정 자체가 사라진다(계획서 목록 밖 — 118 §4 랜딩 기록에 적는다).
NON_CONSUMER_MODULES: frozenset[str] = frozenset({
    "src/config.py",
    "src/observability/investigation_metrics.py",
    "src/api/settings_catalog.py",
})

#: **단 전용 모듈** → 그 모듈이 도는 사다리 단(`ladder.LadderTier` 값). 짧은 명시 표다 —
#: import 그래프로 추정하지 않는다(M-3 원칙: 모르는 것은 판정하지 않는다). 소비처가 **전부**
#: 이 표의 같은 단 모듈이면 그 축은 그 단이 아닐 때 도달 불가다(`sweep.ConfigSnapshot`).
TIER_EXCLUSIVE_MODULES: Mapping[str, str] = {
    "src/orchestration/deepagents_tools.py": "deep_agent",
    "src/orchestration/deep_agent.py": "deep_agent",
}

#: 설정 루트(AppConfig)를 가리키는 관용 이름 — `general` 그룹 필드의 동적 접근 판정에 쓴다.
_ROOT_CONFIG_NAMES: frozenset[str] = frozenset({"config", "app_config", "cfg", "settings"})


@dataclass(frozen=True)
class ConsumerIndex:
    """AST 로 모은 설정 읽기 흔적 — 판정은 `consumers_of()` 가 한다."""

    #: 이름(속성 이름·문자열 상수) → 그 이름을 읽는 모듈 경로 집합
    reads: Mapping[str, frozenset[str]]
    #: 동적 접근 지점 `(모듈 경로, 행, 대상 식의 식별자들, 모듈이 속성으로 쓰는 이름들)`
    dynamic: tuple[tuple[str, int, frozenset[str], frozenset[str]], ...] = ()


@dataclass(frozen=True)
class ConsumerVerdict:
    """축 1개의 소비처 판정."""

    modules: tuple[str, ...]            # 소비 모듈(정렬)
    undetermined: Optional[str] = None  # 판정하지 않는 사유(동적 접근) — 있으면 종전대로 잰다
    tier_only: Optional[str] = None     # 소비처가 전부 이 단 전용 모듈이다

    @property
    def none(self) -> bool:
        return not self.modules and self.undetermined is None


def _docstring_nodes(tree: Any) -> set[int]:
    """모듈·클래스·함수 독스트링 상수 노드의 id — 소비처로 세지 않는다(118 §2.5 오탐 사례)."""
    import ast

    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                out.add(id(body[0].value))
    return out


def _identifiers(node: Any) -> frozenset[str]:
    import ast

    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            names.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            names.add(sub.attr)
    return frozenset(names)


def scan_consumers(root: Optional[Any] = None,
                   packages: Sequence[str] = CONSUMER_ROOTS) -> ConsumerIndex:
    """`packages` 아래 `.py` 를 AST 로 읽어 **읽기 형태 3종**을 모은다(정규식 grep 금지 — 118 B-3).

    1. 속성 접근 `x.field` (Load 문맥만 — 대입은 읽기가 아니다)
    2. `getattr(x, "field")` — 문자열 상수라 3에 포함된다
    3. 필드명 문자열 상수 — `_KNOWLEDGE_RENDER_FIELD = "prompt_knowledge_render"` 형태.
       **독스트링은 뺀다**(`process_query.py:230` 이 필드를 언급만 한다).

    `getattr`·`hasattr` 의 이름 인자가 상수가 아니면 동적 접근으로 따로 남긴다. 단 그 인자가
    **같은 모듈의 문자열 상수 이름**이면 3에서 이미 셌으므로 동적으로 보지 않는다.
    """
    import ast
    from pathlib import Path

    base = Path(root) if root is not None else Path(__file__).resolve().parent.parent.parent
    reads: dict[str, set[str]] = {}
    dynamic: list[tuple[str, int, frozenset[str], frozenset[str]]] = []
    for package in packages:
        for path in sorted((base / package).rglob("*.py")):
            rel = path.relative_to(base).as_posix()
            parts = rel.split("/")
            if "tests" in parts[1:] or "scripts" in parts[1:] or rel in NON_CONSUMER_MODULES:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            docs = _docstring_nodes(tree)
            consts = {t.id for node in ast.walk(tree) if isinstance(node, ast.Assign)
                      and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
                      for t in node.targets if isinstance(t, ast.Name)}
            attrs: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                    reads.setdefault(node.attr, set()).add(rel)
                    attrs.add(node.attr)
                elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
                      and id(node) not in docs and node.value.isidentifier()):
                    reads.setdefault(node.value, set()).add(rel)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id in ("getattr", "hasattr") and len(node.args) >= 2):
                    name_arg = node.args[1]
                    if isinstance(name_arg, ast.Constant):
                        continue
                    if isinstance(name_arg, ast.Name) and name_arg.id in consts:
                        continue
                    dynamic.append((rel, node.lineno, _identifiers(node.args[0]),
                                    frozenset(attrs)))
    return ConsumerIndex(reads={k: frozenset(v) for k, v in reads.items()},
                         dynamic=tuple(dynamic))


_CONSUMER_INDEX: Optional[ConsumerIndex] = None


def consumer_index() -> ConsumerIndex:
    """저장소 소비처 색인(프로세스당 1회 · 약 1~2초)."""
    global _CONSUMER_INDEX
    if _CONSUMER_INDEX is None:
        _CONSUMER_INDEX = scan_consumers()
    return _CONSUMER_INDEX


def consumers_of(knob: catalog.KnobSpec, index: ConsumerIndex) -> ConsumerVerdict:
    """이 노브의 소비 모듈 · 동적 접근으로 판정 불가 사유 · 단 전용 여부."""
    modules = set(index.reads.get(knob.field_name, frozenset()))
    modules |= index.reads.get(knob.env_key, frozenset())      # os.getenv("KEY") 등
    tier_only: Optional[str] = None
    if modules:
        tiers = {TIER_EXCLUSIVE_MODULES.get(m) for m in modules}
        if len(tiers) == 1 and None not in tiers:
            tier_only = next(iter(tiers))
        return ConsumerVerdict(modules=tuple(sorted(modules)), tier_only=tier_only)
    # 정적 읽기가 0건이다 — 이 그룹을 동적으로 읽는 지점이 있으면 판정하지 않는다(추정 금지).
    general = knob.group_key == "general"
    for rel, line, target, module_attrs in index.dynamic:
        hit = (bool(target & _ROOT_CONFIG_NAMES) if general
               else knob.group_key in target or knob.group_key in module_attrs)
        if hit:
            return ConsumerVerdict(modules=(), undetermined=(
                f"정적 읽기 0건이지만 `{rel}:{line}` 이 이 그룹을 동적으로 읽을 수 있다 — "
                "판정하지 않는다(종전대로 잰다)"))
    return ConsumerVerdict(modules=())


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
    consumers: Optional[ConsumerIndex] = None,
    check_consumers: Optional[bool] = None,
) -> tuple[list[AxisCandidate], list[AxisDecision]]:
    """축을 자동으로 뽑고 전 판정을 함께 돌려준다.

    **F3 소비처 판정**(plans/118 B-3) — F2 를 통과한 노브의 설정 필드를 읽는 코드를 AST 로 센다
    (`scan_consumers`). 소비처가 0 이면 「소비처 없음」으로 제외한다 — 값을 바꿔도 동작이 같은
    A/A 축이 구간을 통째로 쓰지 않게 한다(run `20260922-162132` 5.47시간). 소비처가 전부 단 전용
    모듈이면 축은 남기고 `tier_only` 를 단다(도달 여부는 기준선 단을 아는 스냅샷이 정한다).
    `check_consumers` 기본값은 `include_structural` 과 같은 규칙(**`knobs` 를 주지 않았을 때만**)
    이다 — 가짜 노브로 부른 단위 테스트가 저장소 코드에 묶이지 않게 한다.

    카탈로그 밖의 **구조 축**(사다리 단 · F0)을 맨 앞에 붙인다. 그 축은 노브 하나가 아니라
    3키 묶음이라 F1·F2 선별을 지나지 않는다.

    `include_structural` 기본값은 **`knobs` 를 주지 않았을 때만 True** 다 — *"이 노브들로
    축을 뽑아라"* 라는 호출(단위 테스트·부분 검사)에 노브가 아닌 축을 끼워 넣지 않는다.
    """
    if include_structural is None:
        include_structural = knobs is None
    if check_consumers is None:
        check_consumers = knobs is None or consumers is not None
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
                reason=f"구조 축 `{owner}` 이 다중 키로 전개한다 — "
                       "단일 키 축으로 중복 전개하지 않는다"))
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
        verdict = (consumers_of(knob, consumers if consumers is not None else consumer_index())
                   if check_consumers else None)
        if verdict is not None and verdict.none:
            decisions.append(AxisDecision(
                env_key=knob.env_key, included=False, stage="F3", tier=tier,
                reason=(f"소비처 없음 — `{knob.group_key}.{knob.field_name}` 을 읽는 코드가 "
                        f"{'·'.join(CONSUMER_ROOTS)} 에 0건이다(정의·기동 에코 제외 · AST 판정). "
                        "값을 바꿔도 동작이 같다(plans/118 B-3)")))
            continue
        if tier == "secondary":
            rationale = (f"영향 경로 {'·'.join(paths)} · **상한·예산** — 정상 경로에서는 발동하지 않아 2차 축")
        else:
            rationale = f"영향 경로 {'·'.join(paths)} — 동작 모드를 바꾼다"
        if verdict is not None and verdict.tier_only:
            rationale += (f" · 소비처가 `{verdict.tier_only}` 단 전용 모듈뿐이다"
                          f"({', '.join(verdict.modules)})")
        axes.append(AxisCandidate(
            env_key=knob.env_key, group_key=knob.group_key, type=knob.type,
            levels=levels, impact_paths=paths, rationale=rationale, tier=tier,
            tier_only=verdict.tier_only if verdict is not None else None,
            consumers=verdict.modules if verdict is not None else (),
        ))
        decisions.append(AxisDecision(
            env_key=knob.env_key, included=True, stage="F2", reason=rationale))

    axes.sort(key=lambda a: (a.tier != "primary", -len(a.impact_paths), a.env_key))
    # 구조 축이 맨 앞이다 — 캠페인이 첫 구간으로 뽑는 것과 같은 순서를 화면에서도 본다.
    return structural + axes, decisions


def consumer_exclusions(tier: str = "primary") -> dict[str, str]:
    """F3 로 제외된 축 → 사유 — 계획 표·합산 판정표의 「미측정」 행 원천(plans/118 B-3 ③).

    `tier` 는 `sweep.build_arms` 와 같은 규칙이다(`all` 이면 전 등급).
    """
    _, decisions = select_axes()
    return {d.env_key: d.reason for d in decisions
            if d.stage == "F3" and not d.included and (tier == "all" or d.tier == tier)}


def tier_exclusive_axes() -> dict[str, tuple[str, tuple[str, ...]]]:
    """축 id → (그 축만 소비하는 단, 소비 모듈) — 단 전용 축만(plans/118 B-3 ②)."""
    axes, _ = select_axes()
    return {a.env_key: (a.tier_only, a.consumers) for a in axes if a.tier_only}


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
