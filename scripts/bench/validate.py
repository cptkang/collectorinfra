"""트랙 T — 전수 설정 검증 L1~L4 (plans/93 §3.5).

  L1 카탈로그 정합   `catalog.check_integrity` 위임
  L2 기동 안전성     경계값을 넣고 config 로드가 되는가
  L3 주입 실효성     주입값 = 자식이 실제로 읽은 값인가
  L4 소비 실증       값을 바꾸면 무언가 달라지는가

**성능(L5)은 여기 없다.** 이 모듈은 LLM을 한 번도 부르지 않는다.

설계 원칙 하나만 기억하면 된다 — **실패는 예외가 아니라 결과다.** 335필드를 도는 동안
한 건이 죽었다고 전체가 멈추면 아무것도 못 잰다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

from scripts.bench import catalog, probe

#: L2가 시도할 경계값. 타입별로 "정상 1 + 비정상 1"이 최소 단위다.
#: 전수를 다 돌면 335 × N 프로세스라 기본은 대표값만 쓴다(`exhaustive=False`).
_BOOL_VALUES = ("true", "false")
_INT_VALUES = ("0", "1", "-1")
_FLOAT_VALUES = ("0", "1.5", "-1")
_STR_VALUES = ("", "x")


@dataclass(frozen=True)
class ShadowedKey:
    """L3 — `.env`에 적어도 무효인 키. *"바꿨는데 왜 안 바뀌지"* 의 정체."""

    env_key: str
    injected: str
    effective: Optional[str]
    suspected_source: str  # os_env | encenv | unknown


@dataclass(frozen=True)
class BootFinding:
    """L2 — 어떤 값을 넣었을 때 기동이 어떻게 됐는가."""

    env_key: str
    value: str
    ok: bool
    error_type: Optional[str] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class ConsumptionFinding:
    """L4 — 값을 바꿨을 때 실효 설정이 달라졌는가."""

    env_key: str
    verdict: str  # changed | unchanged | unchecked
    detail: str = ""


@dataclass(frozen=True)
class UnconsumedComparison:
    """L4 대조 — 손으로 관리하는 `UNCONSUMED_KEYS`와 실측의 3분류."""

    agreed_unconsumed: tuple[str, ...]      # 목록에 있고 실측도 불변
    list_missed: tuple[str, ...]            # 목록에 없는데 실측이 불변 → 등재 검토
    list_stale: tuple[str, ...]             # 목록에 있는데 실측이 변함 → 목록이 낡음


def _dotted_of(knob: catalog.KnobSpec) -> str:
    """env 키가 아니라 **카탈로그의 필드명**으로 에코 경로를 만든다.

    env 접두와 그룹 이름이 다른 경우가 실재한다 — `ServerConfig`는 `env_prefix="API_"`인데
    그룹 키는 `server`라서 `API_PORT`를 접두 제거로 풀면 `server.api_port`가 되어 빗나간다
    (실제 경로는 `server.port`). 그래서 문자열을 깎지 않고 `FieldSpec.field_name`을 쓴다.
    """
    if knob.group_key == "general":
        return knob.field_name
    return f"{knob.group_key}.{knob.field_name}"


def _sample_values(knob: catalog.KnobSpec, *, exhaustive: bool) -> tuple[str, ...]:
    """L2가 시도할 값 목록."""
    if knob.enum_choices:
        return tuple(knob.enum_choices) if exhaustive else (knob.enum_choices[0],)
    if knob.type == "bool":
        return _BOOL_VALUES if exhaustive else ("true",)
    if knob.type == "int":
        return _INT_VALUES if exhaustive else ("1",)
    if knob.type == "float":
        return _FLOAT_VALUES if exhaustive else ("1.5",)
    return _STR_VALUES if exhaustive else ("x",)


def _flip_value(knob: catalog.KnobSpec) -> Optional[str]:
    """L4용 — 기본값과 **다른** 값 하나. 만들 수 없으면 None(=미검사)."""
    default = (knob.default or "").strip().lower()
    if knob.enum_choices:
        for choice in knob.enum_choices:
            if choice.lower() != default:
                return choice
        return None
    if knob.type == "bool":
        return "false" if default in ("true", "1", "yes") else "true"
    if knob.type == "int":
        return "2" if default != "2" else "3"
    if knob.type == "float":
        return "2.5" if default != "2.5" else "3.5"
    if knob.type in ("str", "csv"):
        return "bench-probe-sentinel" if default != "bench-probe-sentinel" else "bench-probe-other"
    return None


def check_injection(
    knobs: Iterable[catalog.KnobSpec],
    *,
    base_env: Optional[Mapping[str, str]] = None,
    runner: Optional[probe.Runner] = None,
) -> list[ShadowedKey]:
    """L3 — 주입이 실제로 먹는지 전수 확인한다.

    가림의 출처는 두 가지다(D-129 실측 우선순위 OS env > `.encenv` > `.env`).
    어느 쪽인지까지 말해줘야 운영자가 조치할 수 있다.
    """
    shadowed: list[ShadowedKey] = []
    encenv = catalog.sc.encenv_managed_keys() | catalog.sc.encenv_present_keys()
    env_base = dict(base_env) if base_env is not None else None

    for knob in knobs:
        probe_value = _flip_value(knob)
        if probe_value is None:
            continue
        result = probe.echo_config({knob.env_key: probe_value}, base_env=env_base, runner=runner)
        if not result.ok:
            continue  # 기동 실패는 L2의 소관이다
        dotted = _dotted_of(knob)
        effective = result.value_of(dotted)
        if effective is None and dotted not in result.config:
            continue  # 경로를 못 찾으면 판정하지 않는다(추측 금지)
        if _norm(effective) != _norm(probe_value):
            source = "encenv" if knob.env_key in encenv else (
                "os_env" if env_base is not None and knob.env_key in env_base else "unknown"
            )
            shadowed.append(ShadowedKey(
                env_key=knob.env_key,
                injected=probe_value,
                effective=None if effective is None else str(effective),
                suspected_source=source,
            ))
    return shadowed


def _norm(value: object) -> str:
    """비교용 정규화 — `True`/`"true"`, `1`/`"1"`을 같게 본다."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip().lower()
    if text in ("1", "yes", "on"):
        return "true"
    if text in ("0", "no", "off"):
        return "false"
    try:
        number = float(text)
        return str(int(number)) if number.is_integer() else str(number)
    except ValueError:
        return text


def check_boot(
    knobs: Iterable[catalog.KnobSpec],
    *,
    exhaustive: bool = False,
    base_env: Optional[Mapping[str, str]] = None,
    runner: Optional[probe.Runner] = None,
) -> list[BootFinding]:
    """L2 — 경계값을 넣고 config 로드가 되는지 본다. 실패 원문을 보존한다."""
    findings: list[BootFinding] = []
    env_base = dict(base_env) if base_env is not None else None
    for knob in knobs:
        for value in _sample_values(knob, exhaustive=exhaustive):
            result = probe.echo_config({knob.env_key: value}, base_env=env_base, runner=runner)
            findings.append(BootFinding(
                env_key=knob.env_key,
                value=value,
                ok=result.ok,
                error_type=result.error_type,
                error=(result.error or None),
            ))
    return findings


def check_consumption(
    knobs: Iterable[catalog.KnobSpec],
    *,
    baseline: Optional[probe.EchoResult] = None,
    nondeterministic: frozenset[str] = frozenset(),
    base_env: Optional[Mapping[str, str]] = None,
    runner: Optional[probe.Runner] = None,
) -> list[ConsumptionFinding]:
    """L4 — 값을 바꾸면 실효 설정이 달라지는가.

    **"불변 = 삭제해도 된다"가 아니다.** 여기서 보는 것은 *설정 객체*의 변화이고,
    그 값을 읽는 코드가 있는지는 별개다. 판정어를 `unchanged`로만 두고 해석을 리포트에 맡긴다.
    """
    env_base = dict(base_env) if base_env is not None else None
    base = baseline or probe.echo_config(base_env=env_base, runner=runner)
    if not base.ok:
        return [ConsumptionFinding(env_key=k.env_key, verdict="unchecked",
                                   detail="기준 설정을 읽지 못했다") for k in knobs]
    base_fp = probe.config_fingerprint(base, exclude_keys=nondeterministic)

    findings: list[ConsumptionFinding] = []
    for knob in knobs:
        flipped = _flip_value(knob)
        if flipped is None:
            findings.append(ConsumptionFinding(
                env_key=knob.env_key, verdict="unchecked",
                detail=f"타입 {knob.type}에서 기본값과 다른 값을 만들 수 없다"))
            continue
        result = probe.echo_config({knob.env_key: flipped}, base_env=env_base, runner=runner)
        if not result.ok:
            findings.append(ConsumptionFinding(
                env_key=knob.env_key, verdict="unchecked",
                detail=f"그 값으로 기동 실패({result.error_type}) — L2 소관"))
            continue
        changed = probe.config_fingerprint(result, exclude_keys=nondeterministic) != base_fp
        findings.append(ConsumptionFinding(
            env_key=knob.env_key,
            verdict="changed" if changed else "unchanged",
            detail=f"{knob.default!r} → {flipped!r}",
        ))
    return findings


def compare_with_unconsumed(findings: Iterable[ConsumptionFinding]) -> UnconsumedComparison:
    """L4 대조 — 손으로 관리하는 목록의 거짓을 드러낸다(plans/93 §3.5.4).

    세 번째 분류(`list_stale`)가 이 층의 값어치다. 목록에 미소비로 적혀 있는데 실측이
    변하면 **그 사이 소비가 생긴 것**이고, 아무도 목록을 고치지 않았다는 뜻이다.
    """
    declared = set(catalog.sc.UNCONSUMED_KEYS)
    agreed, missed, stale = [], [], []
    for finding in findings:
        if finding.verdict == "unchanged":
            (agreed if finding.env_key in declared else missed).append(finding.env_key)
        elif finding.verdict == "changed" and finding.env_key in declared:
            stale.append(finding.env_key)
    return UnconsumedComparison(
        agreed_unconsumed=tuple(sorted(agreed)),
        list_missed=tuple(sorted(missed)),
        list_stale=tuple(sorted(stale)),
    )
