"""업무 서비스 보조 신호 S8~S12 — 묶음 키 추출 · 신호 순도 보정 · 전파형 후보 (plans/215 §3.2.3).

보조 신호는 **서비스를 스스로 만들지 않는다**. 이미 배정된 서버(닻)의 서비스로 미배정 서버를
후보로 끌어올 뿐이다(D-348 — 확정은 사람). 순도 보정(`signal_status`)은 S6·S7에도 쓴다.

- 묶음형(S8 등록 시각 · S9 IP 대역 · S10 계약): 같은 묶음의 다른 서버가 이웃이다.
- 이웃형(S11 알람 동시 발생 · S12 가용성 의존): 서버마다 직접 이웃을 모은 묶음(중심 + 이웃).
- 묶음은 전부 존별이다(순도를 존별로 재므로).

계층: domain — stdlib + 같은 domain 모듈만 쓴다.
"""

from __future__ import annotations

import ipaddress
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass

from noise_gate.domain.biz_service_model import (
    REASON_ATTR_FAILED,
    REASON_CONFIG_INVALID,
    REASON_CONFIG_OFF,
    REASON_FEW_GROUPS,
    REASON_LOW_PURITY,
    SIG_ALARM_COOCCURRENCE,
    SIG_AVAIL_DEPENDENCY,
    SIG_HOST_PREFIX,
    SIG_IP_SUBNET,
    SIG_ITAM_CONTRACT,
    SIG_REGISTERED_BATCH,
    SIG_TREE_GROUP,
    AlarmMinute,
    AttrFailure,
    AuxSignalsConfig,
    BizServiceConfig,
    CandidateSignal,
    ServerAttr,
    ServerRef,
    SignalStatus,
    SmsServer,
    aux_basis,
    ref_key,
    text,
)

SIGNAL_LABELS: Mapping[str, str] = {
    SIG_HOST_PREFIX: "S6 호스트 접두 코드",
    SIG_TREE_GROUP: "S7 자원 트리 그룹",
    SIG_REGISTERED_BATCH: "S8 등록 시각 묶음",
    SIG_IP_SUBNET: "S9 IP 대역",
    SIG_ITAM_CONTRACT: "S10 ITAM 계약",
    SIG_ALARM_COOCCURRENCE: "S11 알람 동시 발생",
    SIG_AVAIL_DEPENDENCY: "S12 가용성 의존",
}


@dataclass(frozen=True)
class AuxBundle:
    """보조 신호 묶음 1개 — 이웃형이면 `center`가 그 묶음의 중심 서버다."""

    signal: str
    zone: str
    key: str
    refs: frozenset[ServerRef]
    center: ServerRef | None = None


@dataclass(frozen=True)
class AuxBuild:
    """묶음 추출 결과 — `notes`는 신호 아님으로 뺀 건수(대량 등록 묶음 · 폭주 분 등)."""

    bundles: tuple[AuxBundle, ...]
    notes: Mapping[str, int]


# ── 순도 보정 ───────────────────────────────────────────────────────────
def bundle_purity(
    bundles: Iterable[frozenset[ServerRef]], confirmed: Mapping[ServerRef, str]
) -> tuple[int, int]:
    """`(평가 묶음 수, 서비스 1개뿐인 묶음 수)` — 확정 닻 2대 이상인 묶음만 평가한다.

    같은 서버 집합의 묶음은 한 번만 센다.
    """
    seen: set[frozenset[ServerRef]] = set()
    evaluated = pure = 0
    for refs in bundles:
        if refs in seen:
            continue
        seen.add(refs)
        services = [confirmed[r] for r in refs if r in confirmed]
        if len(services) < 2:
            continue
        evaluated += 1
        if len(set(services)) == 1:
            pure += 1
    return evaluated, pure


def _failure_for(signal: str, zone: str, failures: Sequence[AttrFailure]) -> str | None:
    for f in failures:
        if f.signal == signal and (f.zone is None or f.zone == zone):
            return text(f.reason) or "보강 읽기 실패"
    return None


def signal_status(
    signal: str,
    zone: str,
    *,
    config: BizServiceConfig,
    bundles: Iterable[frozenset[ServerRef]],
    confirmed: Mapping[ServerRef, str],
    attr_failures: Sequence[AttrFailure] = (),
) -> SignalStatus:
    """존 하나의 신호 상태(§3.2.3 4) — 설정 → 보강 읽기 → 순도 순으로 판정한다.

    평가 묶음이 `min_groups` 미만이면 「평가 부족」, 순도가 `min_purity` 미만이면 「순도 미달」로
    사유를 구분한다.
    """
    label = SIGNAL_LABELS.get(signal, signal)
    invalid = config.disabled_reasons.get(signal)
    if invalid:
        return SignalStatus(signal, zone, False, reason=REASON_CONFIG_INVALID,
                            reason_text=f"{label} 설정 오류 — {invalid}")
    if not config.signal_enabled(signal):
        return SignalStatus(signal, zone, False, reason=REASON_CONFIG_OFF,
                            reason_text=f"{label} 설정 꺼짐")
    failed = _failure_for(signal, zone, attr_failures)
    if failed is not None:
        return SignalStatus(signal, zone, False, reason=REASON_ATTR_FAILED,
                            reason_text=f"{label} 보강 읽기 실패 — {failed}")
    aux = config.aux
    evaluated, pure = bundle_purity(bundles, confirmed)
    purity = round(pure / evaluated, 4) if evaluated else None
    if evaluated < aux.min_groups:
        return SignalStatus(
            signal, zone, False, purity, evaluated, pure, REASON_FEW_GROUPS,
            f"{label} 평가 부족 — 확정 구성원 2대 이상인 묶음 {evaluated}개"
            f"(기준 {aux.min_groups}개)",
        )
    if purity is not None and purity < aux.min_purity:
        return SignalStatus(
            signal, zone, False, purity, evaluated, pure, REASON_LOW_PURITY,
            f"{label} 순도 {purity:.2f} — 기준 {aux.min_purity:.2f} 미만(묶음이 업무가 아닌 "
            "다른 기준으로 나뉜 것으로 보임)",
        )
    return SignalStatus(signal, zone, True, purity, evaluated, pure)


# ── 묶음 추출 ───────────────────────────────────────────────────────────
def _registered_batches(
    servers: Sequence[SmsServer], attrs: Mapping[ServerRef, ServerAttr],
    aux: AuxSignalsConfig, notes: dict[str, int],
) -> list[AuxBundle]:
    """S8 — 존별 등록 시각 오름차순 · 앞 서버와 간격 ≤ `gap_seconds`면 같은 묶음 · 2~`max_batch`."""
    cfg = aux.registered_batch
    gap_ms = cfg.gap_seconds * 1000
    by_zone: dict[str, list[tuple[int, str, ServerRef]]] = defaultdict(list)
    for s in servers:
        attr = attrs.get(s.ref)
        if attr is None or attr.registered_at_ms is None:
            continue
        by_zone[s.zone].append((int(attr.registered_at_ms), s.member_key, s.ref))
    out: list[AuxBundle] = []
    for zone in sorted(by_zone):
        rows = sorted(by_zone[zone])
        chains: list[list[tuple[int, str, ServerRef]]] = []
        for row in rows:
            if chains and row[0] - chains[-1][-1][0] <= gap_ms:
                chains[-1].append(row)
            else:
                chains.append([row])
        for chain in chains:
            if len(chain) < 2:
                continue
            if len(chain) > cfg.max_batch:
                notes["mass_registered_batches"] = notes.get("mass_registered_batches", 0) + 1
                continue
            out.append(AuxBundle(SIG_REGISTERED_BATCH, zone, f"{zone}:{chain[0][0]}",
                                 frozenset(r[2] for r in chain)))
    return out


def ipv4_subnet_key(ip: str, prefix_len: int, exclude_cidrs: Sequence[str] = ()) -> str:
    """S9 묶음 키 — 대표 IPv4의 /`prefix_len` 망. IPv6·형식 오류·제외 대역이면 빈 문자열."""
    try:
        addr = ipaddress.ip_address(text(ip))
    except ValueError:
        return ""
    if not isinstance(addr, ipaddress.IPv4Address):
        return ""
    for cidr in exclude_cidrs:
        try:
            if addr in ipaddress.ip_network(text(cidr), strict=False):
                return ""
        except ValueError:
            continue
    return str(ipaddress.ip_network(f"{addr}/{prefix_len}", strict=False))


def _keyed_bundles(
    signal: str, keyed: Iterable[tuple[str, str, ServerRef]]
) -> list[AuxBundle]:
    """`(존, 키, 서버)` → 존·키별 2대 이상 묶음."""
    groups: dict[tuple[str, str], set[ServerRef]] = defaultdict(set)
    for zone, key, ref in keyed:
        if key:
            groups[(zone, key)].add(ref)
    return [
        AuxBundle(signal, zone, key, frozenset(refs))
        for (zone, key), refs in sorted(groups.items()) if len(refs) >= 2
    ]


def _neighbor_bundles(
    signal: str, zone_of: Mapping[ServerRef, str], edges: Iterable[tuple[ServerRef, ServerRef]]
) -> list[AuxBundle]:
    """무방향 간선 → 서버마다 `중심 + 직접 이웃` 묶음(같은 존 이웃만)."""
    nbrs: dict[ServerRef, set[ServerRef]] = defaultdict(set)
    for a, b in edges:
        if a == b or a not in zone_of or b not in zone_of or zone_of[a] != zone_of[b]:
            continue
        nbrs[a].add(b)
        nbrs[b].add(a)
    return [
        AuxBundle(signal, zone_of[c], ref_key(c), frozenset({c, *nbrs[c]}), center=c)
        for c in sorted(nbrs)
    ]


def _alarm_edges(
    minutes: Sequence[AlarmMinute], known: Collection[ServerRef],
    aux: AuxSignalsConfig, notes: dict[str, int],
) -> list[tuple[ServerRef, ServerRef]]:
    """S11 — 같은 분 알람 서버 쌍의 겹친 분 수 ≥ `min_co`.

    그 분의 알람 서버가 `burst_hosts`대를 넘으면 그 분은 세지 않는다(망·공용 장애).
    """
    cfg = aux.alarm_cooccurrence
    per_minute: dict[int, set[ServerRef]] = defaultdict(set)
    for m in minutes:
        if m.ref in known:
            per_minute[int(m.minute)].add(m.ref)
    pairs: dict[tuple[ServerRef, ServerRef], int] = defaultdict(int)
    for minute in sorted(per_minute):
        refs = sorted(per_minute[minute])
        if len(refs) > cfg.burst_hosts:
            notes["burst_minutes"] = notes.get("burst_minutes", 0) + 1
            continue
        for i, a in enumerate(refs):
            for b in refs[i + 1:]:
                pairs[(a, b)] += 1
    return [pair for pair, n in sorted(pairs.items()) if n >= cfg.min_co]


def build_aux_bundles(
    servers: Sequence[SmsServer],
    attrs: Sequence[ServerAttr],
    alarm_minutes: Sequence[AlarmMinute],
    aux: AuxSignalsConfig,
) -> AuxBuild:
    """S8~S12 묶음을 전부 만든다(켜짐 여부는 `signal_status`가 따로 판정한다)."""
    notes: dict[str, int] = {}
    attr_by = {a.ref: a for a in attrs}
    zone_of = {s.ref: s.zone for s in servers}
    bundles: list[AuxBundle] = []
    bundles += _registered_batches(servers, attr_by, aux, notes)
    bundles += _keyed_bundles(SIG_IP_SUBNET, (
        (s.zone, ipv4_subnet_key(s.ip, aux.ip_subnet.prefix_len, aux.ip_subnet.exclude_cidrs),
         s.ref) for s in servers
    ))
    contract: list[tuple[str, str, ServerRef]] = []
    for s in servers:
        attr = attr_by.get(s.ref)
        if attr is None:
            continue
        if text(attr.contract_no):
            contract.append((s.zone, f"contract:{text(attr.contract_no)}", s.ref))
        if text(attr.maint_contract_no):
            contract.append((s.zone, f"maint:{text(attr.maint_contract_no)}", s.ref))
    bundles += _keyed_bundles(SIG_ITAM_CONTRACT, contract)
    bundles += _neighbor_bundles(SIG_ALARM_COOCCURRENCE, zone_of,
                                 _alarm_edges(alarm_minutes, zone_of, aux, notes))
    dep_edges: list[tuple[ServerRef, ServerRef]] = []
    for s in servers:
        attr = attr_by.get(s.ref)
        for rid in attr.depends if attr is not None else ():
            target = (s.ref[0], text(rid))
            if target in zone_of:  # 서버가 아닌 대상(스냅샷 밖)은 이웃이 아니다
                dep_edges.append((s.ref, target))
    bundles += _neighbor_bundles(SIG_AVAIL_DEPENDENCY, zone_of, dep_edges)
    return AuxBuild(tuple(bundles), notes)


# ── 전파형 후보 ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class AuxCandidate:
    """보조 신호 후보 1건(서버 → 서비스 · 근거 1개)."""

    ref: ServerRef
    service_key: str
    signal: CandidateSignal


@dataclass(frozen=True)
class AuxPropagation:
    """전파 결과 — 후보 · 존·신호별 「공용」 묶음 수."""

    candidates: tuple[AuxCandidate, ...]
    shared: Mapping[tuple[str, str], int]


def propagate_candidates(
    bundles: Sequence[AuxBundle],
    *,
    anchors: Mapping[ServerRef, str],
    eligible: Collection[ServerRef],
    status: Mapping[tuple[str, str], SignalStatus],
    min_anchors: int,
    blocked: Collection[tuple[ServerRef, str]] = (),
) -> AuxPropagation:
    """§3.2.3 규칙 1~3 — 미배정 서버 x의 묶음(이웃)에서 닻이 `min_anchors` 이상이고 전부 한
    서비스면 후보. 닻이 서비스 둘 이상에 걸치면 「공용」으로 세기만 한다.

    Args:
        anchors: 닻 서버 → 서비스 키(합치기 결과 + 수동·확인 구성원 · 제외 구성원은 빼고 넘긴다)
        eligible: 후보를 낼 수 있는 미배정 서버
        status: `(신호, 존)` → 상태 — 꺼진 신호의 묶음은 쓰지 않는다
        blocked: 다시 내지 않을 `(서버, 서비스)`(그 서비스에서 제외된 서버 등)
    """
    eligible_set = set(eligible)
    blocked_set = set(blocked)
    out: list[AuxCandidate] = []
    shared: dict[tuple[str, str], int] = defaultdict(int)
    for b in sorted(bundles, key=lambda x: (x.signal, x.zone, x.key)):
        st = status.get((b.signal, b.zone))
        if st is None or not st.enabled:
            continue
        subjects = [b.center] if b.center is not None else sorted(b.refs)
        is_shared = False
        for x in subjects:
            if x is None or x not in eligible_set:
                continue
            services = [anchors[r] for r in b.refs if r != x and r in anchors]
            if len(services) < min_anchors:
                continue
            distinct = set(services)
            if len(distinct) != 1:
                is_shared = True
                continue
            service = next(iter(distinct))
            if (x, service) in blocked_set:
                continue
            out.append(AuxCandidate(x, service, CandidateSignal(
                aux_basis(b.signal), b.key, len(services), st.purity)))
        if is_shared:  # 닻이 서비스 둘 이상에 걸친 묶음 — 아무것도 내지 않고 건수만 센다
            shared[(b.signal, b.zone)] += 1
    return AuxPropagation(tuple(out), dict(shared))
