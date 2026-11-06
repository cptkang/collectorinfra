"""업무 서비스 연계 계산 설정 로더 — `config/knowledge/biz_service.yaml`.

plans/215 §3.2.1 6 · §3.2.3.

YAML을 읽어 값을 검증하고 도메인 설정(`BizServiceConfig`)으로 옮긴다. 일반어는 ITAM 사슬 설정
(`config/knowledge/itam/service_host_lookup.yaml`)의 `generic_terms`를 그대로 재사용한다(새 값 0).

규약:
- **값이 잘못되면 그 신호만 끈다** — `enabled=False` + `disabled_reasons[신호] = 사유`
  + `logger.warning`.
  실행 기록·P1 화면에 사유가 그대로 나간다(침묵 금지). 다른 신호는 그대로 돈다.
- 블록이 없거나 파일을 읽지 못해도 같은 방식으로 끈다(코드 기본값으로 몰래 켜지 않는다).
- `strip_prefix_patterns`의 각 패턴은 `^`로 시작하고 컴파일돼야 한다 — 아니면 S6 비활성.
- `sms_thresholds`의 잘못된 지표 항목은 그 지표만 뺀다(그 지표는 「판정 불가 · 임계 미설정」).
- `layer_tokens`가 잘못되면 계층 자동 제안 전체를 끈다(비움 — 지어내지 않는다).
- `sms_metric_source`(M3 · W0-8)는 지표마다 자원 유형·지표 정의 이름 둘 다 있을 때만 쓴다 — 빈 값은
  미설정(경고 없음), 한쪽만 있거나 문자 규칙 밖이거나 다른 지표와 같은 쌍이면 그 지표만 빼고 경고.

계층: application — domain 설정 모양 소비 · 파일 읽기.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, TypeVar

from noise_gate.domain.biz_service_model import (
    AUX_SIGNALS,
    DEFAULT_MAX_MEMBERS,
    LAYERS,
    PREFIX_ONLY_CANDIDATE,
    PREFIX_ONLY_INFERRED,
    SIG_ALARM_COOCCURRENCE,
    SIG_AVAIL_DEPENDENCY,
    SIG_HOST_PREFIX,
    SIG_IP_SUBNET,
    SIG_ITAM_CONTRACT,
    SIG_REGISTERED_BATCH,
    SIG_TREE_GROUP,
    AlarmCooccurrenceConfig,
    AuxSignalsConfig,
    AvailDependencyConfig,
    BizServiceConfig,
    HostPrefixConfig,
    IpSubnetConfig,
    ItamContractConfig,
    RegisteredBatchConfig,
    SmsThreshold,
    TreeGroupConfig,
)

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = _ROOT / "config" / "knowledge" / "biz_service.yaml"
DEFAULT_LOOKUP_PATH = _ROOT / "config" / "knowledge" / "itam" / "service_host_lookup.yaml"
SMS_METRICS = ("cpu", "memory", "filesystem")

T = TypeVar("T")


class _InvalidError(ValueError):
    """설정 값 오류(사유 문장)."""


@dataclass(frozen=True)
class BizServiceSettings:
    """로드 결과 — 도메인 설정 · 일반어 · 경고 목록(로그로도 남겼다) · M3 SMS 지표 원천.

    `sms_metric_source`는 지표(cpu·memory·filesystem) → (자원 유형, 지표 정의 이름)이고 설정된
    지표만 담는다(비면 SMS 수집 0).
    """

    config: BizServiceConfig
    generic_terms: frozenset[str]
    warnings: tuple[str, ...] = ()
    sms_metric_source: Mapping[str, tuple[str, str]] = field(default_factory=dict)


# ── 값 검증 ─────────────────────────────────────────────────────────────
def _block(raw: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = raw.get(name)
    if value is None:
        raise _InvalidError(f"{name} 블록 없음")
    if not isinstance(value, Mapping):
        raise _InvalidError(f"{name}는 매핑이어야 함")
    return value


def _bool(block: Mapping[str, Any], key: str) -> bool:
    value = block.get(key)
    if not isinstance(value, bool):
        raise _InvalidError(f"{key}는 true/false여야 함(값 {value!r})")
    return value


def _int(block: Mapping[str, Any], key: str, lo: int, hi: int | None = None) -> int:
    value = block.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < lo or (
            hi is not None and value > hi):
        bound = f"{lo} 이상" if hi is None else f"{lo}~{hi}"
        raise _InvalidError(f"{key}는 {bound} 정수여야 함(값 {value!r})")
    return value


def _share(block: Mapping[str, Any], key: str) -> float:
    value = block.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 < value <= 1:
        raise _InvalidError(f"{key}는 0 초과 1 이하 수여야 함(값 {value!r})")
    return float(value)


def _strings(block: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = block.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise _InvalidError(f"{key}는 빈 값 없는 문자열 목록이어야 함")
    return tuple(v.strip() for v in value)


def _strip_patterns(block: Mapping[str, Any]) -> tuple[str, ...]:
    patterns = _strings(block, "strip_prefix_patterns")
    for p in patterns:
        if not p.startswith("^"):
            raise _InvalidError(f"strip_prefix_patterns 패턴은 ^로 시작해야 함({p!r})")
        try:
            re.compile(p)
        except re.error as exc:
            raise _InvalidError(f"strip_prefix_patterns 정규식 오류({p!r}: {exc})") from exc
    return patterns


def _cidrs(block: Mapping[str, Any]) -> tuple[str, ...]:
    cidrs = _strings(block, "exclude_cidrs")
    for c in cidrs:
        try:
            ipaddress.ip_network(c, strict=False)
        except ValueError as exc:
            raise _InvalidError(f"exclude_cidrs 대역 형식 오류({c!r})") from exc
    return cidrs


# ── 블록 파서 ───────────────────────────────────────────────────────────
def _host_prefix(raw: Mapping[str, Any]) -> HostPrefixConfig:
    b = _block(raw, "host_prefix_code")
    mode = b.get("prefix_only_services")
    if mode not in (PREFIX_ONLY_INFERRED, PREFIX_ONLY_CANDIDATE):
        raise _InvalidError(f"prefix_only_services는 inferred|candidate여야 함(값 {mode!r})")
    return HostPrefixConfig(
        enabled=_bool(b, "enabled"), length=_int(b, "length", 1, 10),
        min_share=_share(b, "min_share"), prefix_only_services=str(mode),
        exclude_prefixes=tuple(p.lower() for p in _strings(b, "exclude_prefixes")),
        strip_prefix_patterns=_strip_patterns(b),
    )


def _tree_group(raw: Mapping[str, Any]) -> TreeGroupConfig:
    b = _block(raw, "tree_group")
    return TreeGroupConfig(enabled=_bool(b, "enabled"), min_share=_share(b, "min_share"))


def _aux_common(aux: Mapping[str, Any]) -> AuxSignalsConfig:
    return AuxSignalsConfig(
        min_anchors=_int(aux, "min_anchors", 1), min_purity=_share(aux, "min_purity"),
        min_groups=_int(aux, "min_groups", 1),
    )


def _registered_batch(aux: Mapping[str, Any]) -> RegisteredBatchConfig:
    b = _block(aux, "registered_batch")
    return RegisteredBatchConfig(_bool(b, "enabled"), _int(b, "gap_seconds", 0),
                                 _int(b, "max_batch", 2))


def _ip_subnet(aux: Mapping[str, Any]) -> IpSubnetConfig:
    b = _block(aux, "ip_subnet")
    return IpSubnetConfig(_bool(b, "enabled"), _int(b, "prefix_len", 1, 32), _cidrs(b))


def _itam_contract(aux: Mapping[str, Any]) -> ItamContractConfig:
    return ItamContractConfig(_bool(_block(aux, "itam_contract"), "enabled"))


def _alarm_cooccurrence(aux: Mapping[str, Any]) -> AlarmCooccurrenceConfig:
    b = _block(aux, "alarm_cooccurrence")
    return AlarmCooccurrenceConfig(_bool(b, "enabled"), _int(b, "min_co", 1),
                                   _int(b, "lookback_days", 1), _int(b, "burst_hosts", 1))


def _avail_dependency(aux: Mapping[str, Any]) -> AvailDependencyConfig:
    return AvailDependencyConfig(_bool(_block(aux, "avail_dependency"), "enabled"))


def _thresholds(raw: Mapping[str, Any], warn: Callable[[str], None]) -> dict[str, SmsThreshold]:
    value = raw.get("sms_thresholds", {})
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        warn("sms_thresholds는 매핑이어야 함 — SMS 판정 전체 「판정 불가 · 임계 미설정」")
        return {}
    out: dict[str, SmsThreshold] = {}
    for metric in sorted(value, key=str):
        entry = value[metric]
        name = str(metric).strip().lower()
        try:
            if name not in SMS_METRICS:
                raise _InvalidError(f"알 수 없는 지표 {metric!r}(cpu·memory·filesystem)")
            if not isinstance(entry, Mapping):
                raise _InvalidError("warn·crit 매핑이어야 함")
            levels: dict[str, float | None] = {}
            for level in ("warn", "crit"):
                v = entry.get(level)
                if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool)
                                      or not 0 <= v <= 100):
                    raise _InvalidError(f"{level}는 0~100 수여야 함(값 {v!r})")
                levels[level] = None if v is None else float(v)
            w, c = levels["warn"], levels["crit"]
            if w is not None and c is not None and w > c:
                raise _InvalidError("warn이 crit보다 큼")
            out[name] = SmsThreshold(w, c)
        except _InvalidError as exc:
            warn(f"sms_thresholds.{metric} 무시 — {exc}(그 지표는 「판정 불가 · 임계 미설정」)")
    return out


def _sms_metric_source(
    raw: Mapping[str, Any], warn: Callable[[str], None]
) -> dict[str, tuple[str, str]]:
    value = raw.get("sms_metric_source", {})
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        warn("sms_metric_source는 매핑이어야 함 — SMS 수집 꺼짐(지표 원천 미설정)")
        return {}
    out: dict[str, tuple[str, str]] = {}
    for metric in sorted(value, key=str):
        entry = value[metric]
        name = str(metric).strip().lower()
        try:
            if name not in SMS_METRICS:
                raise _InvalidError(f"알 수 없는 지표 {metric!r}(cpu·memory·filesystem)")
            if not isinstance(entry, Mapping):
                raise _InvalidError("resource_type·definition_name 매핑이어야 함")
            pair = tuple(str(entry.get(k) or "").strip()
                         for k in ("resource_type", "definition_name"))
            if not any(pair):
                continue  # W0-8 전 — 미설정
            # 문자 규칙은 SQL 빌더가 단일 출처(무거운 import라 값이 있을 때만 읽는다)
            from noise_gate.infrastructure.biz_service_sql import valid_sms_source_value

            if not all(valid_sms_source_value(v) for v in pair):
                raise _InvalidError("resource_type·definition_name 둘 다 영문으로 시작하는"
                                    " 영숫자·밑줄·점 64자 이하여야 함")
            if (pair[0], pair[1]) in out.values():
                raise _InvalidError("다른 지표와 같은 원천 쌍")
            out[name] = (pair[0], pair[1])
        except _InvalidError as exc:
            warn(f"sms_metric_source.{metric} 무시 — {exc}(그 지표는 수집하지 않음)")
    return out


def _layer_tokens(
    raw: Mapping[str, Any], warn: Callable[[str], None]
) -> dict[str, tuple[str, ...]]:
    value = raw.get("layer_tokens", {})
    if value is None:
        return {}
    try:
        if not isinstance(value, Mapping):
            raise _InvalidError("매핑이어야 함")
        out: dict[str, tuple[str, ...]] = {}
        for layer in sorted(value, key=str):
            if layer not in LAYERS:
                raise _InvalidError(f"알 수 없는 계층 {layer!r}({'·'.join(LAYERS)})")
            out[str(layer)] = tuple(t.lower() for t in _strings(value, str(layer)))
        return out
    except _InvalidError as exc:
        warn(f"layer_tokens 무시 — {exc}(계층 자동 제안 꺼짐 · 전 구성원 「미지정」)")
        return {}


def _parse(
    raw: Mapping[str, Any], fn: Callable[[Mapping[str, Any]], T], default: T, signal: str,
    reasons: dict[str, str], warn: Callable[[str], None],
) -> T:
    try:
        return fn(raw)
    except _InvalidError as exc:
        reasons[signal] = str(exc)
        warn(f"{signal} 비활성 — {exc}")
        return default


def _read_yaml(path: Path) -> Mapping[str, Any]:
    import yaml  # type: ignore[import-untyped]

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise _InvalidError(f"설정 파일을 읽지 못함({path.name}: {type(exc).__name__})") from exc
    if data is None:
        return {}
    if not isinstance(data, Mapping):
        raise _InvalidError(f"설정 파일 최상위가 매핑이 아님({path.name})")
    return data


def load_generic_terms(path: Path | None = None) -> tuple[frozenset[str], str | None]:
    """ITAM 사슬 설정의 `generic_terms` → `(일반어 집합, 실패 사유 또는 None)`."""
    try:
        raw = _read_yaml(path or DEFAULT_LOOKUP_PATH)
        terms = raw.get("generic_terms") or []
        if not isinstance(terms, list):
            raise _InvalidError("generic_terms는 목록이어야 함")
    except _InvalidError as exc:
        return frozenset(), str(exc)
    return frozenset(str(t).strip() for t in terms if str(t).strip()), None


def load_biz_service_config(
    path: Path | None = None,
    *,
    lookup_path: Path | None = None,
    max_members: int = DEFAULT_MAX_MEMBERS,
) -> BizServiceSettings:
    """설정 1벌을 읽는다 — 잘못된 값은 그 신호만 끄고 경고(`warnings`·`logger.warning`)."""
    warnings: list[str] = []

    def warn(message: str) -> None:
        warnings.append(message)
        logger.warning("업무 서비스 설정 — %s", message)

    reasons: dict[str, str] = {}
    try:
        raw = _read_yaml(path or DEFAULT_CONFIG_PATH)
    except _InvalidError as exc:
        warn(f"{exc} — S6·S7·보조 신호 전부 비활성")
        raw = {}

    host_prefix = _parse(raw, _host_prefix, HostPrefixConfig(enabled=False), SIG_HOST_PREFIX,
                         reasons, warn)
    tree_group = _parse(raw, _tree_group, TreeGroupConfig(enabled=False), SIG_TREE_GROUP,
                        reasons, warn)
    off = AuxSignalsConfig(
        registered_batch=RegisteredBatchConfig(enabled=False),
        ip_subnet=IpSubnetConfig(enabled=False),
        itam_contract=ItamContractConfig(enabled=False),
        alarm_cooccurrence=AlarmCooccurrenceConfig(enabled=False),
        avail_dependency=AvailDependencyConfig(enabled=False),
    )
    try:
        aux_raw = _block(raw, "aux_signals")
        aux = _aux_common(aux_raw)
    except _InvalidError as exc:
        for sig in AUX_SIGNALS:
            reasons[sig] = f"aux_signals 공통 값 — {exc}"
        warn(f"보조 신호 전부 비활성 — {exc}")
        aux, aux_raw = off, {}
    else:
        aux = replace(
            aux,
            registered_batch=_parse(aux_raw, _registered_batch, off.registered_batch,
                                    SIG_REGISTERED_BATCH, reasons, warn),
            ip_subnet=_parse(aux_raw, _ip_subnet, off.ip_subnet, SIG_IP_SUBNET, reasons, warn),
            itam_contract=_parse(aux_raw, _itam_contract, off.itam_contract, SIG_ITAM_CONTRACT,
                                 reasons, warn),
            alarm_cooccurrence=_parse(aux_raw, _alarm_cooccurrence, off.alarm_cooccurrence,
                                      SIG_ALARM_COOCCURRENCE, reasons, warn),
            avail_dependency=_parse(aux_raw, _avail_dependency, off.avail_dependency,
                                    SIG_AVAIL_DEPENDENCY, reasons, warn),
        )

    generic, failed = load_generic_terms(lookup_path)
    if failed:
        warn(f"일반어 목록 없음 — {failed}(일반어 업무명 걸러내기 꺼짐)")
    config = BizServiceConfig(
        host_prefix=host_prefix, tree_group=tree_group, aux=aux,
        sms_thresholds=_thresholds(raw, warn), layer_tokens=_layer_tokens(raw, warn),
        max_members=max(1, int(max_members)), disabled_reasons=reasons,
    )
    sms_source = _sms_metric_source(raw, warn)
    return BizServiceSettings(config, generic, tuple(warnings), sms_source)
