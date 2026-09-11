"""시나리오 카탈로그 로더·검증 (plans/94 §3).

카탈로그가 **실행 정본**이다. 문서(docs/29)가 아니라 이 YAML이 러너의 입력이며,
`plans` 역추적 필드가 "plans 폴더의 기능들을 테스트한다"는 요건을 기계 검증 가능하게 만든다.

로더는 **의심스러운 것을 통과시키지 않는다**. 조용히 넘어간 결함은 리포트에서
"합격"으로 보이기 때문이다 - 거부 사유는 전부 사람이 읽을 수 있는 문장으로 남긴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from . import REPO_ROOT

SCENARIO_DIR = REPO_ROOT / "testdata" / "scenarios"
PROFILES_PATH = REPO_ROOT / "config" / "scenarios" / "profiles.yaml"

# 대응 등급 (plans/94 §3.8). 7등급 + 금지 3종.
RESPONSE_MODES: frozenset[str] = frozenset(
    {"answer", "correct", "clarify", "guide", "partial", "refuse", "error"}
)
FORBIDDEN_MODES: frozenset[str] = frozenset({"silent_wrong", "hang", "crash"})

KINDS: frozenset[str] = frozenset(
    {"normal", "compound", "misuse", "mistake", "misconception", "control"}
)
# 대조군 쌍을 강제하는 kind (V16).
#
# 가드를 새로 넣게 만드는 축만 강제한다. 오용·실수·착각의 처방은 거의 전부 "거부하거나
# 되묻는 규칙"이고, 그 규칙은 정상 동작까지 함께 막는 과잉 거부를 낳는다(R12). 대조군이
# 없으면 그 부작용이 같은 리포트에서 보이지 않는다.
# compound(R1)는 제외한다 - 분해 정확도 축이라 가드를 유발하지 않는다.
CONTROL_REQUIRED_KINDS: frozenset[str] = frozenset({"misuse", "mistake", "misconception"})

ENDPOINTS: frozenset[str] = frozenset({"stream", "plain", "file", "file_stream"})
ENVS: frozenset[str] = frozenset({"closed", "sandbox", "both"})
CACHE_STATES: frozenset[str] = frozenset({"cold", "warm"})

# ID 형식: 군 문자 + 일련번호. docs/29 의 A-01 · SYN-A-01 · 대조군 접미 R4-01C 를 모두 받는다.
_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+[a-z]?){1,2}$")


class CatalogError(Exception):
    """카탈로그가 실행에 쓸 수 없는 상태다. 사유 전건을 담는다."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"카탈로그 거부 {len(errors)}건")


@dataclass(frozen=True)
class Turn:
    """시나리오 1턴. `send`는 요청 본문, `expect`는 단언 선언."""

    send: dict[str, Any]
    expect: dict[str, Any]


@dataclass(frozen=True)
class Group:
    """군 헤더. 성능 목표는 군 단위로 정의된다(§2-2)."""

    id: str
    name: str
    latency_target_ms: int
    # G-10 (b): 대응 등급 정책이 사용자 확정되기 전에는 R군 판정을 내리지 않는다.
    # false면 금지 등급 3종만 불합격이고 나머지는 전부 manual(관측)로 남는다.
    policy_confirmed: bool = False
    source: Optional[str] = None


@dataclass(frozen=True)
class Scenario:
    """시나리오 1건."""

    id: str
    group: str
    plans: list[int]
    title: str
    turns: list[Turn]
    kind: str = "normal"
    env: str = "both"
    profile: str = "baseline"
    cache_state: str = "warm"
    endpoint: str = "stream"
    perf: dict[str, Any] = field(default_factory=dict)
    response_modes: list[str] = field(default_factory=list)
    pair_with: Optional[str] = None
    teardown: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    repeat: Optional[int] = None
    upload: Optional[str] = None
    # 원문이 산문이라 실제 프롬프트가 아직 없는 초안. 러너가 사유와 함께 건너뛴다.
    # 산문을 LLM 에 보내면 무의미한 결과에 돈만 나간다 - 조용히 흘리지 않고 리포트에 남긴다.
    prompt_authored: bool = True
    notes: Optional[str] = None
    # 무과금 모의 실행(--mock)에서 서버가 돌려줄 응답. 없으면 일반 canned 응답이 나간다.
    mock: Optional[dict[str, Any]] = None
    source_file: Optional[str] = None

    @property
    def target_ms(self) -> Optional[int]:
        value = self.perf.get("target_ms")
        return int(value) if value is not None else None

    @property
    def is_r_group(self) -> bool:
        return self.kind in {"compound", "misuse", "mistake", "misconception"}


@dataclass
class Catalog:
    """군·시나리오·프로파일의 묶음."""

    groups: dict[str, Group] = field(default_factory=dict)
    scenarios: list[Scenario] = field(default_factory=list)
    profiles: dict[str, dict[str, str]] = field(default_factory=dict)

    def by_id(self, scenario_id: str) -> Optional[Scenario]:
        for scenario in self.scenarios:
            if scenario.id == scenario_id:
                return scenario
        return None

    def plans_index(self) -> dict[int, list[str]]:
        """계획서 번호 -> 시나리오 ID 역집계. 커버리지 판정의 재료다(§6.4)."""
        index: dict[int, list[str]] = {}
        for scenario in self.scenarios:
            for plan in scenario.plans:
                index.setdefault(plan, []).append(scenario.id)
        return index

    def select(
        self,
        groups: Optional[list[str]] = None,
        only: Optional[list[str]] = None,
        env: Optional[str] = None,
        kinds: Optional[list[str]] = None,
    ) -> list[Scenario]:
        """실행 대상을 고른다. `env` 불일치는 건너뛴 사유와 함께 리포트 10절에 남는다."""
        picked = list(self.scenarios)
        if only:
            wanted = {item.strip() for item in only if item.strip()}
            picked = [s for s in picked if s.id in wanted]
        if groups:
            prefixes = tuple(g.strip().upper() for g in groups if g.strip())
            picked = [s for s in picked if s.group.upper().startswith(prefixes)]
        if kinds:
            picked = [s for s in picked if s.kind in set(kinds)]
        if env:
            picked = [s for s in picked if s.env in (env, "both")]
        return picked


def load_profiles(path: Path = PROFILES_PATH) -> dict[str, dict[str, str]]:
    """플래그 프로파일을 읽는다. 값은 전부 문자열이어야 한다(환경변수 주입)."""
    if not path.exists():
        raise CatalogError([f"프로파일 파일이 없다: {path}"])
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    profiles = raw.get("profiles")
    if not isinstance(profiles, dict):
        raise CatalogError([f"{path}: 최상위 'profiles' 매핑이 필요하다"])

    errors: list[str] = []
    result: dict[str, dict[str, str]] = {}
    for name, mapping in profiles.items():
        if mapping is None:
            mapping = {}
        if not isinstance(mapping, dict):
            errors.append(f"프로파일 '{name}': 매핑이 아니다")
            continue
        bad = [k for k, v in mapping.items() if not isinstance(v, str)]
        if bad:
            errors.append(
                f"프로파일 '{name}': 환경변수 값은 문자열이어야 한다 - {', '.join(sorted(bad))}"
            )
            continue
        result[str(name)] = {str(k): v for k, v in mapping.items()}
    if errors:
        raise CatalogError(errors)
    if "baseline" not in result:
        raise CatalogError(["프로파일 'baseline'이 없다 - 운영 설정 기준선이 사라진다"])
    return result


def _parse_turns(raw_turns: Any, scenario_id: str, errors: list[str]) -> list[Turn]:
    turns: list[Turn] = []
    if not isinstance(raw_turns, list) or not raw_turns:
        errors.append(f"{scenario_id}: 'turns'가 비었다 - 보낼 것이 없는 시나리오는 실행되지 않는다")
        return turns
    for index, item in enumerate(raw_turns, start=1):
        if not isinstance(item, dict):
            errors.append(f"{scenario_id} 턴{index}: 매핑이 아니다")
            continue
        send = item.get("send")
        if not isinstance(send, dict) or not send:
            errors.append(f"{scenario_id} 턴{index}: 'send'가 비었다")
            continue
        expect = item.get("expect") or {}
        if not isinstance(expect, dict):
            errors.append(f"{scenario_id} 턴{index}: 'expect'가 매핑이 아니다")
            continue
        turns.append(Turn(send=send, expect=expect))
    return turns


# 정규식을 받는 단언 키. 로드 시점에 컴파일해 본다.
_REGEX_KEYS = ("sql_must_match", "sql_must_not_match")


def _validate_patterns(turns: list[Turn], scenario_id: str, errors: list[str]) -> None:
    """정규식을 **로드 시점에** 컴파일한다.

    실행 도중 re.error 가 나면 그 시점까지의 측정이 통째로 날아가고, 폐쇄망에서는
    그 손실이 그대로 재실행 비용이다. 1단(--dry-run)에서 잡는 것이 가장 싸다.
    (실측 2026-09-11: `(?i)` 를 표현식 중간에 둔 패턴이 Python 3.11+ 에서 거부된다)
    """
    for index, turn in enumerate(turns, start=1):
        for key in _REGEX_KEYS:
            for pattern in turn.expect.get(key) or []:
                try:
                    re.compile(str(pattern))
                except re.error as exc:
                    errors.append(
                        f"{scenario_id} 턴{index} {key}: 정규식 컴파일 실패 - {pattern!r} ({exc})"
                    )


def _parse_scenario(
    raw: Any, group_id: str, source: Path, errors: list[str]
) -> Optional[Scenario]:
    if not isinstance(raw, dict):
        errors.append(f"{source.name}: 시나리오 항목이 매핑이 아니다")
        return None

    scenario_id = str(raw.get("id") or "").strip()
    if not scenario_id:
        errors.append(f"{source.name}: 'id' 없는 시나리오가 있다")
        return None
    if not _ID_RE.match(scenario_id):
        errors.append(f"{scenario_id}: ID 형식이 아니다 (예: C-02 · SYN-A-01 · R4-01C)")

    # V1 - plans 빈 리스트 금지. 이 필드가 계획서 커버리지 요건을 떠받친다(§3.2).
    plans_raw = raw.get("plans")
    plans: list[int] = []
    if not isinstance(plans_raw, list) or not plans_raw:
        errors.append(
            f"{scenario_id}: 'plans'가 비었다 - 어느 계획서의 기능을 검증하는지 "
            "역추적할 수 없는 시나리오는 커버리지 분모를 부풀린다"
        )
    else:
        for item in plans_raw:
            try:
                plans.append(int(item))
            except (TypeError, ValueError):
                errors.append(f"{scenario_id}: plans 항목이 계획서 번호가 아니다 - {item!r}")

    kind = str(raw.get("kind") or "normal")
    if kind not in KINDS:
        errors.append(f"{scenario_id}: kind '{kind}' 는 정의 밖이다 ({', '.join(sorted(KINDS))})")

    env = str(raw.get("env") or "both")
    if env not in ENVS:
        errors.append(f"{scenario_id}: env '{env}' 는 정의 밖이다 ({', '.join(sorted(ENVS))})")

    endpoint = str(raw.get("endpoint") or "stream")
    if endpoint not in ENDPOINTS:
        errors.append(f"{scenario_id}: endpoint '{endpoint}' 는 정의 밖이다")

    cache_state = str(raw.get("cache_state") or "warm")
    if cache_state not in CACHE_STATES:
        errors.append(f"{scenario_id}: cache_state '{cache_state}' 는 cold|warm 이어야 한다")

    modes = raw.get("response_modes") or []
    if not isinstance(modes, list):
        errors.append(f"{scenario_id}: response_modes 가 리스트가 아니다")
        modes = []
    else:
        unknown = [m for m in modes if m not in RESPONSE_MODES]
        if unknown:
            errors.append(
                f"{scenario_id}: 정의 밖 대응 등급 {unknown} - "
                f"허용: {', '.join(sorted(RESPONSE_MODES))}"
            )

    turns = _parse_turns(raw.get("turns"), scenario_id, errors)
    _validate_patterns(turns, scenario_id, errors)

    perf = raw.get("perf") or {}
    if not isinstance(perf, dict):
        errors.append(f"{scenario_id}: perf 가 매핑이 아니다")
        perf = {}
    applies = perf.get("applies_to_turn")
    if applies is not None and turns and not (1 <= int(applies) <= len(turns)):
        errors.append(
            f"{scenario_id}: perf.applies_to_turn={applies} 이 턴 범위(1~{len(turns)}) 밖이다"
        )

    upload = raw.get("upload")
    if endpoint in ("file", "file_stream") and not upload:
        errors.append(f"{scenario_id}: endpoint={endpoint} 인데 'upload'(양식 파일 경로)가 없다")

    return Scenario(
        id=scenario_id,
        group=group_id,
        plans=plans,
        title=str(raw.get("title") or scenario_id),
        turns=turns,
        kind=kind,
        env=env,
        profile=str(raw.get("profile") or "baseline"),
        cache_state=cache_state,
        endpoint=endpoint,
        perf=perf,
        response_modes=[str(m) for m in modes],
        pair_with=(str(raw["pair_with"]) if raw.get("pair_with") else None),
        teardown=[str(t) for t in (raw.get("teardown") or [])],
        depends_on=[str(d) for d in (raw.get("depends_on") or [])],
        repeat=(int(raw["repeat"]) if raw.get("repeat") is not None else None),
        upload=(str(upload) if upload else None),
        prompt_authored=bool(raw.get("prompt_authored", True)),
        notes=(str(raw["notes"]) if raw.get("notes") else None),
        mock=(raw.get("mock") if isinstance(raw.get("mock"), dict) else None),
        source_file=source.name,
    )


def _parse_file(path: Path, errors: list[str]) -> tuple[Optional[Group], list[Scenario]]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        errors.append(f"{path.name}: YAML 파싱 실패 - {exc}")
        return None, []
    if not isinstance(raw, dict):
        errors.append(f"{path.name}: 최상위가 매핑이 아니다")
        return None, []

    group_raw = raw.get("group")
    if not isinstance(group_raw, dict) or not group_raw.get("id"):
        errors.append(f"{path.name}: 'group.id' 가 없다")
        return None, []

    group_id = str(group_raw["id"]).strip()
    # V2 - 군 목표 누락 거부. 목표가 없으면 성능 판정이 조용히 n/a 로 빠진다.
    target = group_raw.get("latency_target_ms")
    if target is None:
        errors.append(
            f"{path.name}: 군 '{group_id}' 에 latency_target_ms 가 없다 - "
            "목표 없는 군은 성능 판정이 전부 n/a 가 되어 불합격이 사라진다"
        )
        target = 0
    group = Group(
        id=group_id,
        name=str(group_raw.get("name") or group_id),
        latency_target_ms=int(target),
        policy_confirmed=bool(group_raw.get("policy_confirmed", False)),
        source=(str(group_raw["source"]) if group_raw.get("source") else None),
    )

    scenarios: list[Scenario] = []
    for item in raw.get("scenarios") or []:
        parsed = _parse_scenario(item, group_id, path, errors)
        if parsed is not None:
            scenarios.append(parsed)
    return group, scenarios


def _cross_validate(catalog: Catalog, errors: list[str]) -> None:
    seen: dict[str, str] = {}
    for scenario in catalog.scenarios:
        # V2 - ID 중복 거부. 중복이 있으면 재개(resume) 키가 충돌해 한 건이 조용히 사라진다.
        if scenario.id in seen:
            errors.append(
                f"{scenario.id}: ID 중복 ({seen[scenario.id]} · {scenario.source_file})"
            )
        else:
            seen[scenario.id] = scenario.source_file or "?"

        # V2 - 프로파일 미정의 거부.
        if scenario.profile not in catalog.profiles:
            errors.append(
                f"{scenario.id}: 프로파일 '{scenario.profile}' 가 "
                f"{PROFILES_PATH.name} 에 없다"
            )

        if scenario.group not in catalog.groups:
            errors.append(f"{scenario.id}: 군 '{scenario.group}' 헤더가 없다")

        for dep in scenario.depends_on:
            if catalog.by_id(dep) is None:
                errors.append(f"{scenario.id}: depends_on '{dep}' 가 카탈로그에 없다")

    # V16 - 대조군 쌍 강제.
    for scenario in catalog.scenarios:
        if scenario.kind in CONTROL_REQUIRED_KINDS and not scenario.pair_with:
            errors.append(
                f"{scenario.id}: kind={scenario.kind} 인데 pair_with 가 비었다 - "
                "대조군 없이는 가드가 정상 동작까지 막는 과잉 거부를 같은 리포트에서 볼 수 없다"
            )
            continue
        if not scenario.pair_with:
            continue
        partner = catalog.by_id(scenario.pair_with)
        if partner is None:
            errors.append(f"{scenario.id}: pair_with '{scenario.pair_with}' 가 카탈로그에 없다")
            continue
        if partner.pair_with != scenario.id:
            errors.append(
                f"{scenario.id} <-> {partner.id}: pair_with 가 서로를 가리키지 않는다 "
                f"(상대는 '{partner.pair_with}')"
            )
        if scenario.kind in CONTROL_REQUIRED_KINDS and partner.kind != "control":
            errors.append(
                f"{scenario.id}: 짝 '{partner.id}' 의 kind 가 control 이 아니다 "
                f"(현재 '{partner.kind}') - 대조군은 정상 동작이어야 의미가 있다"
            )


def load_catalog(
    scenario_dir: Path = SCENARIO_DIR,
    profiles_path: Path = PROFILES_PATH,
) -> Catalog:
    """카탈로그 전건을 읽고 검증한다. 하나라도 문제가 있으면 CatalogError로 거부한다."""
    errors: list[str] = []
    profiles: dict[str, dict[str, str]] = {}
    try:
        profiles = load_profiles(profiles_path)
    except CatalogError as exc:
        errors.extend(exc.errors)

    catalog = Catalog(profiles=profiles)
    if not scenario_dir.exists():
        raise CatalogError(errors + [f"시나리오 디렉터리가 없다: {scenario_dir}"])

    files = sorted(p for p in scenario_dir.glob("*.yaml") if not p.name.startswith("_"))
    if not files:
        raise CatalogError(errors + [f"시나리오 파일이 0건이다: {scenario_dir}"])

    for path in files:
        group, scenarios = _parse_file(path, errors)
        if group is None:
            continue
        if group.id in catalog.groups:
            errors.append(f"군 '{group.id}' 헤더가 두 파일에 있다 ({path.name})")
        catalog.groups[group.id] = group
        catalog.scenarios.extend(scenarios)

    _cross_validate(catalog, errors)
    if errors:
        raise CatalogError(errors)
    return catalog
