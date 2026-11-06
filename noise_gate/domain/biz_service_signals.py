"""업무 서비스 축 ① — SMS 서버를 업무 서비스로 묶기(S1~S7 합치기 · S4·S5 후보) (plans/215 §3.2).

강 신호(S1 등록명 업무 꼬리)가 서비스의 뼈대다. 중 신호(S2 APM 도메인 · S3 ITAM 업무 · S6 호스트
접두 코드 · S7 자원 트리 그룹)는 꼬리 없는 서버를 **강 신호 서비스 1개에만** 붙이거나, 강 신호가
없는 묶음끼리 새 서비스를 만든다. 강 신호 서비스 둘 이상에 이어지는 묶음은 잇지 않고 충돌로 남긴다.

합치기(결정적 · 입력 순서 무관):
1. 중 신호 묶음마다 강 신호 집합을 구한다 — 묶음 안 S1 서버의 업무 + 묶음 이름 정규형이 S1 업무와
   같으면 그 업무. 둘 이상이면 충돌(그 묶음은 잇지 않는다).
2. 꼬리 없는 서버와 묶음 이름(라벨)을 노드로 한 연결 성분을 만든다(같은 라벨 = 같은 노드).
3. 성분의 강 신호가 1개면 그 서비스에 `inferred`로 붙이고, 0개면 라벨 우선순위(ITAM → 접두 코드 →
   트리 → APM 도메인)로 새 서비스 키를 정하고, 2개 이상이면 성분의 서버 전부를 충돌로 남긴다.

S6·S7은 S1과의 대응을 계산마다 다시 배우고(최빈 비율 ≥ `min_share` · 2대 이상), 순도 보정에서
꺼진 존의 서버는 쓰지 않는다.

계층: domain — stdlib + `src.domain.host_names` + 같은 domain 모듈만 쓴다.
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from src.domain.host_names import (
    canonical_host_key,
    host_keys_match,
    name_tokens,
    normalize_hostname,
    split_business_suffix,
)

from noise_gate.domain.apm_host_map import usable_targets
from noise_gate.domain.biz_service_aux import signal_status
from noise_gate.domain.biz_service_model import (
    CONF_CONFIRMED,
    CONF_INFERRED,
    CONFLICT_SMS,
    PREFIX_ONLY_CANDIDATE,
    SIG_APM_DOMAIN,
    SIG_BIZ_SUFFIX,
    SIG_HOST_PREFIX,
    SIG_HOST_STEM,
    SIG_ITAM,
    SIG_NAME_TOKENS,
    SIG_TREE_GROUP,
    ApmMapRow,
    BizServiceConfig,
    CandidateSignal,
    Conflict,
    HostPrefixConfig,
    ItamAppRow,
    ItamServerRow,
    ServerAttr,
    ServerRef,
    SignalStatus,
    SmsServer,
    is_generic_name,
    norm_name,
    ref_key,
    text,
)
from noise_gate.domain.incident_fingerprint import host_group

#: 꼬리 없는 서버의 근거 우선순위(서비스 키 순서와 같다 — §3.2 「서비스 키」).
BASIS_PRIORITY = (SIG_ITAM, SIG_HOST_PREFIX, SIG_TREE_GROUP, SIG_APM_DOMAIN)
#: S4 stem 최소 길이.
MIN_STEM_LEN = 4
_TREE_CODE = re.compile(r"\(([0-9A-Za-z]{2,10})\)\s*$")
_LETTERS = re.compile(r"[a-z]+")
_HAS_LETTER = re.compile(r"[a-z]")


# ── 서비스 키 ───────────────────────────────────────────────────────────
def itam_service_key(code: str) -> str:
    """ITAM 업무 코드 서비스 키."""
    return f"itam:{text(code).lower()}"


def prefix_service_key(code: str) -> str:
    """S6 접두 코드 서비스 키."""
    return f"prefix:{text(code).lower()}"


def tree_service_key(norm: str) -> str:
    """S7 트리 그룹 서비스 키(정규화 그룹명)."""
    return f"tree:{norm}"


def apm_service_key(source_id: str, domain_id: str) -> str:
    """S2 APM 도메인 서비스 키."""
    return f"apm:{text(source_id)}:{text(domain_id)}"


def _label_rank(label: str) -> int:
    for i, prefix in enumerate(("itam:", "prefix:", "tree:", "apm:")):
        if label.startswith(prefix):
            return i
    return 0  # 코드 없는 ITAM 업무명(정규형) — ITAM 단계


# ── 신호별 추출 ─────────────────────────────────────────────────────────
def s1_business(server_name: str, generic: Collection[str]) -> tuple[str, str]:
    """S1 — 등록명 업무 꼬리 → `(정규형, 원문)`. 꼬리가 없거나 일반어뿐이면 `("", "")`."""
    _, biz = split_business_suffix(server_name)
    if is_generic_name(biz, generic):
        return "", ""
    return norm_name(biz), biz


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def extract_prefix_code(server: SmsServer, cfg: HostPrefixConfig) -> tuple[str, bool]:
    """S6 — 호스트 접두 업무코드 → `(코드, hostname·등록명 불일치)`(§3.2.1 1).

    대상은 hostname → 비면 등록명 호스트 부분. 소문자 첫 레이블에서 `strip_prefix_patterns` 중 처음
    맞는 것 하나를 1회 떼고, 남은 앞 `length`글자를 구분자 제거 전 원문에서 자른다. 코드가
    영소문자가 아니거나, 코드 뒤에 영문자가 더 없거나(`srv001`·`web-01` — 이름 전체가
    역할어+번호), IP·제외 접두면 코드 없음.
    """
    host = text(server.hostname)
    name_host, _ = split_business_suffix(server.server_name)
    mismatch = bool(host and name_host and not host_keys_match(host, name_host))
    label = normalize_hostname(host or name_host)
    if not label or _is_ip(label):
        return "", mismatch
    for pattern in cfg.strip_prefix_patterns:
        m = re.match(pattern, label)
        if m:
            label = label[m.end():]
            break
    code, rest = label[: cfg.length], label[cfg.length:]
    if len(code) != cfg.length or not _LETTERS.fullmatch(code) or not _HAS_LETTER.search(rest):
        return "", mismatch
    if code in {text(p).lower() for p in cfg.exclude_prefixes}:
        return "", mismatch
    return code, mismatch


@dataclass(frozen=True)
class TreeGroup:
    """S7 — 서버의 트리 그룹(정규화 이름 · 원문 · 폴더 id · 괄호 코드)."""

    norm: str
    name: str
    folder_id: str
    tree_code: str = ""


def tree_group_of(
    server: SmsServer, attr: ServerAttr | None, generic: Collection[str]
) -> TreeGroup | None:
    """S7 — 서버에서 가까운 폴더부터 올라가 처음 남는 비일반어 폴더.

    서버 이름과 같은 폴더는 건너뛴다. 이름 끝 괄호 안 영숫자 2~10자는 `tree_code`로 뗀다.
    """
    if attr is None:
        return None
    name_host, _ = split_business_suffix(server.server_name)
    own = {norm_name(v) for v in (server.server_name, name_host, server.hostname) if text(v)}
    for folder in reversed(attr.tree_path):
        name = text(folder.name)
        if not name or is_generic_name(name, generic) or norm_name(name) in own:
            continue
        m = _TREE_CODE.search(name)
        return TreeGroup(norm_name(name), name, text(folder.folder_id),
                         m.group(1).upper() if m else "")
    return None


def host_stem(server: SmsServer) -> str:
    """S4 — 정규형 첫 레이블의 호스트군(`incident_fingerprint.host_group` 규칙)."""
    name_host, _ = split_business_suffix(server.server_name)
    key = canonical_host_key(server.hostname or name_host)
    if not key or _is_ip(key):
        return ""
    return host_group(key.split(".", 1)[0])


# ── 합치기 ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class MediumGroup:
    """중 신호 묶음 1개 — `refs`는 꼬리 없는 서버, `strong`은 이어지는 S1 업무."""

    signal: str
    label: str
    display: str
    strong: frozenset[str]
    refs: frozenset[ServerRef]
    all_refs: frozenset[ServerRef] = frozenset()
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Assignment:
    """서버 1대의 자동 배정."""

    service_key: str
    basis: str
    confidence: str
    signals: tuple[str, ...]
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ServiceDraft:
    """자동 서비스 초안(표시명 · 근거)."""

    display_name: str
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RawCandidate:
    """합치기 단계 후보(서버 → 서비스 · 근거 1개)."""

    ref: ServerRef
    service_key: str
    signal: CandidateSignal
    new_service_name: str = ""


@dataclass(frozen=True)
class AxisOne:
    """축 ① 결과."""

    assignments: Mapping[ServerRef, Assignment]
    services: Mapping[str, ServiceDraft]
    conflicts: tuple[Conflict, ...]
    candidates: tuple[RawCandidate, ...]
    signal_status: tuple[SignalStatus, ...]
    codes: Mapping[ServerRef, str]
    notes: Mapping[str, int] = field(default_factory=dict)


def _most_common(values: Sequence[str]) -> str:
    """최빈값(동률이면 사전순 앞)."""
    counts = Counter(values)
    best = max(counts.values())
    return min(v for v, n in counts.items() if n == best)


def _itam_apps_by_code(apps: Sequence[ItamAppRow]) -> dict[str, ItamAppRow]:
    """코드(소문자) → 업무 — 같은 코드가 둘 이상이면 고르지 않는다(D-224 ④)."""
    seen: dict[str, list[ItamAppRow]] = defaultdict(list)
    for app in apps:
        code = text(app.app_code).lower()
        if code:
            seen[code].append(app)
    return {c: rows[0] for c, rows in seen.items() if len({text(r.app_name) for r in rows}) == 1}


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[tuple[str, Any], tuple[str, Any]] = {}

    def find(self, x: tuple[str, Any]) -> tuple[str, Any]:
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: tuple[str, Any], b: tuple[str, Any]) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            lo, hi = sorted((ra, rb), key=repr)
            self.parent[hi] = lo


@dataclass
class _Ctx:
    servers: dict[ServerRef, SmsServer]
    s1: dict[ServerRef, str]
    s1_raw: dict[ServerRef, str]
    s1_keys: frozenset[str]
    generic: frozenset[str]
    apps_by_code: dict[str, ItamAppRow]

    def zone(self, ref: ServerRef) -> str:
        return self.servers[ref].zone

    def tailless(self, refs: Collection[ServerRef]) -> frozenset[ServerRef]:
        return frozenset(r for r in refs if r not in self.s1)

    def strong_of(self, refs: Collection[ServerRef], name_norms: Collection[str]) -> frozenset[str]:
        found = {self.s1[r] for r in refs if r in self.s1}
        found |= {n for n in name_norms if n in self.s1_keys}
        return frozenset(found)


def _zone_status(
    signal: str, ctx: _Ctx, keyed: Mapping[ServerRef, str], config: BizServiceConfig,
    confirmed: Mapping[ServerRef, str], inputs_failures: Sequence[Any],
) -> dict[str, SignalStatus]:
    """S6·S7 존별 순도 보정 — 키(코드·트리 그룹)가 같은 서버 묶음을 확정 닻과 대조한다."""
    zones = sorted({s.zone for s in ctx.servers.values()})
    out: dict[str, SignalStatus] = {}
    for zone in zones:
        groups: dict[str, set[ServerRef]] = defaultdict(set)
        for ref, key in keyed.items():
            if key and ctx.zone(ref) == zone:
                groups[key].add(ref)
        out[zone] = signal_status(
            signal, zone, config=config, bundles=[frozenset(g) for _, g in sorted(groups.items())],
            confirmed=confirmed, attr_failures=inputs_failures,
        )
    return out


def _s6_groups(
    ctx: _Ctx, codes: Mapping[ServerRef, str], on: Collection[str], config: BizServiceConfig,
    status: Mapping[str, SignalStatus],
) -> tuple[list[MediumGroup], list[RawCandidate], dict[str, str]]:
    """S6 대응 학습(§3.2.1 3) → 묶음 · 다업무 코드 후보 · `코드 → S1 업무` 대응."""
    cfg = config.host_prefix
    s1_members: dict[str, list[ServerRef]] = defaultdict(list)
    for ref, key in ctx.s1.items():
        if ctx.zone(ref) in on:
            s1_members[key].append(ref)
    belongs: dict[str, set[str]] = defaultdict(set)
    learned: dict[str, set[str]] = defaultdict(set)
    counts_by: dict[tuple[str, str], int] = {}
    for key in sorted(s1_members):
        refs = s1_members[key]
        for code, n in Counter(codes[r] for r in refs if codes.get(r)).items():
            counts_by[(code, key)] = n
            if n / len(refs) >= cfg.min_share:
                belongs[code].add(key)
                if n >= 2:
                    learned[code].add(key)
    invalid = {c: sorted(keys) for c, keys in belongs.items() if len(keys) >= 2}
    mapping = {c: next(iter(k)) for c, k in learned.items() if c not in invalid and len(k) == 1}

    mapped: dict[str, set[ServerRef]] = defaultdict(set)
    fresh: dict[str, set[ServerRef]] = defaultdict(set)
    cands: list[RawCandidate] = []
    for ref in sorted(codes):
        code = codes[ref]
        if not code or ref in ctx.s1 or ctx.zone(ref) not in on:
            continue
        purity = status[ctx.zone(ref)].purity
        if code in invalid:  # 전사 공통 접두 — 묶지 않고 「업무 후보 N개」로만
            for key in invalid[code]:
                cands.append(RawCandidate(ref, key, CandidateSignal(
                    SIG_HOST_PREFIX, prefix_service_key(code), counts_by.get((code, key), 0),
                    purity)))
        elif code in mapping:
            mapped[code].add(ref)
        else:
            fresh[code].add(ref)
    groups: list[MediumGroup] = []
    for code in sorted(mapped):
        groups.append(MediumGroup(
            SIG_HOST_PREFIX, prefix_service_key(code), code.upper(),
            frozenset({mapping[code]}), frozenset(mapped[code]), frozenset(mapped[code]),
            {"prefix_code": code, "learned_from": mapping[code]},
        ))
    for code in sorted(fresh):
        fresh_refs = fresh[code]
        if len(fresh_refs) < 2:
            continue
        app = ctx.apps_by_code.get(code)
        label = itam_service_key(code) if app else prefix_service_key(code)
        display = (text(app.app_name) or code.upper()) if app else code.upper()
        evidence: dict[str, Any] = {"prefix_code": code}
        if app:
            evidence["itam_code_match"] = text(app.app_code)
        if cfg.prefix_only_services == PREFIX_ONLY_CANDIDATE:
            for ref in sorted(fresh_refs):
                cands.append(RawCandidate(ref, label, CandidateSignal(
                    SIG_HOST_PREFIX, prefix_service_key(code), len(fresh_refs) - 1,
                    status[ctx.zone(ref)].purity), new_service_name=display))
            continue
        names = [norm_name(app.app_name)] if app else []
        groups.append(MediumGroup(SIG_HOST_PREFIX, label, display, ctx.strong_of((), names),
                                  frozenset(fresh_refs), frozenset(fresh_refs), evidence))
    return groups, cands, mapping


def _s7_groups(
    ctx: _Ctx, trees: Mapping[ServerRef, TreeGroup], on: Collection[str],
    config: BizServiceConfig,
) -> tuple[list[MediumGroup], int]:
    """S7 대응 학습(§3.2.2 3·4) → 묶음 · 섞인 그룹 수."""
    by_norm: dict[str, list[ServerRef]] = defaultdict(list)
    for ref in sorted(trees):
        if ctx.zone(ref) in on:
            by_norm[trees[ref].norm].append(ref)
    groups: list[MediumGroup] = []
    mixed = 0
    for norm in sorted(by_norm):
        refs = by_norm[norm]
        tailless = ctx.tailless(refs)
        if not tailless:
            continue
        display = _most_common([trees[r].name for r in refs])
        codes = sorted({trees[r].tree_code for r in refs if trees[r].tree_code})
        evidence: dict[str, Any] = {
            "tree_group": display,
            "folder_ids": sorted({trees[r].folder_id for r in refs}),
        }
        if codes:
            evidence["tree_code"] = codes[0]
        s1_counts = Counter(ctx.s1[r] for r in refs if r in ctx.s1)
        if not s1_counts:
            if len(refs) < 2:
                continue
            app = ctx.apps_by_code.get(codes[0].lower()) if len(codes) == 1 else None
            label = itam_service_key(codes[0]) if app else tree_service_key(norm)
            names = [norm]
            if app:
                display = text(app.app_name) or display
                evidence["itam_code_match"] = text(app.app_code)
                names.append(norm_name(app.app_name))
            groups.append(MediumGroup(SIG_TREE_GROUP, label, display, ctx.strong_of((), names),
                                      tailless, frozenset(refs), evidence))
            continue
        mode = _most_common(list(s1_counts.elements()))
        n, total = s1_counts[mode], sum(s1_counts.values())
        if n >= 2 and n / total >= config.tree_group.min_share:
            groups.append(MediumGroup(SIG_TREE_GROUP, tree_service_key(norm), display,
                                      frozenset({mode}), tailless, frozenset(refs), evidence))
        else:
            mixed += 1  # S1 업무가 섞인 폴더(센터·인프라) — 그 수준은 신호가 아니다
    return groups, mixed


def _s2_groups(ctx: _Ctx, maps: Sequence[ApmMapRow]) -> list[MediumGroup]:
    """S2 — 연계 매핑 `state=active` 행의 대상을 `(source, domain)`별로 합친 묶음."""
    index = target_index(ctx.servers.values())
    union: dict[tuple[str, str], set[ServerRef]] = defaultdict(set)
    names: dict[tuple[str, str], str] = {}
    for row in sorted(maps, key=lambda r: r.map_id):
        if row.state != "active":
            continue
        key = (text(row.source_id), text(row.domain_id))
        union[key] |= resolve_targets(row.targets, index)
        if text(row.domain_name):
            names.setdefault(key, text(row.domain_name))
    out: list[MediumGroup] = []
    for (source, domain), refs in sorted(union.items()):
        if not refs:
            continue
        label = apm_service_key(source, domain)
        out.append(MediumGroup(
            SIG_APM_DOMAIN, label, names.get((source, domain), label), ctx.strong_of(refs, ()),
            ctx.tailless(refs), frozenset(refs), {"source_id": source, "domain_id": domain},
        ))
    return out


def _itam_business(row: ItamServerRow, apps: Sequence[ItamAppRow]) -> tuple[str, str] | None:
    """ITAM 서버 1행의 업무 `(코드, 명)`.

    업무 칸이 비면 단서 칸에 업무명이 든 업무가 1건일 때만 쓴다(D-362 ⑥ · D-224 ④).
    """
    if text(row.app_name) or text(row.app_code):
        return text(row.app_code), text(row.app_name)
    clue = norm_name(row.clue_text)
    if not clue:
        return None
    hits = {(text(a.app_code), text(a.app_name)) for a in apps
            if len(norm_name(a.app_name)) >= 2 and norm_name(a.app_name) in clue}
    return next(iter(hits)) if len(hits) == 1 else None


def _s3_groups(
    ctx: _Ctx, apps: Sequence[ItamAppRow], rows: Sequence[ItamServerRow]
) -> list[MediumGroup]:
    """S3 — ITAM 서버의 업무 → `canonical_host_key` 1대 일치 폴스타 서버."""
    by_label: dict[str, set[ServerRef]] = defaultdict(set)
    display: dict[str, str] = {}
    name_of: dict[str, str] = {}
    first_label: dict[str, list[ServerRef]] = defaultdict(list)
    for ref, s in sorted(ctx.servers.items()):
        name_host, _ = split_business_suffix(s.server_name)
        keys = {canonical_host_key(v) for v in (s.hostname, name_host) if text(v)}
        for k in sorted(k.split(".", 1)[0] for k in keys if k):
            if ref not in first_label[k]:
                first_label[k].append(ref)
    for row in sorted(rows, key=lambda r: (text(r.hostname).lower(), r.app_code, r.app_name)):
        if row.active is False:
            continue
        biz = _itam_business(row, apps)
        if biz is None or (not biz[0] and is_generic_name(biz[1], ctx.generic)):
            continue
        key = canonical_host_key(row.hostname)
        if not key or _is_ip(key):
            continue
        hits = [
            r for r in first_label.get(key.split(".", 1)[0], [])
            if host_keys_match(row.hostname, ctx.servers[r].hostname)
            or host_keys_match(row.hostname, split_business_suffix(ctx.servers[r].server_name)[0])
        ]
        if len(hits) != 1:  # 0대 · 2대 이상은 잇지 않는다(D-224 ④)
            continue
        code, name = biz
        label = itam_service_key(code) if code else norm_name(name)
        if not label:
            continue
        by_label[label].add(hits[0])
        display.setdefault(label, name or code.upper())
        name_of.setdefault(label, norm_name(name))
    out: list[MediumGroup] = []
    for label in sorted(by_label):
        refs = by_label[label]
        names = [name_of[label]] if name_of.get(label) else []
        out.append(MediumGroup(SIG_ITAM, label, display[label], ctx.strong_of(refs, names),
                               ctx.tailless(refs), frozenset(refs), {"itam": display[label]}))
    return out


def target_index(servers: Collection[SmsServer]) -> dict[tuple[str, str, str], ServerRef]:
    """210 대상 → 서버 색인 `("rid", db, resource_id)` · `("name", db, 서버명 소문자)`."""
    index: dict[tuple[str, str, str], ServerRef] = {}
    for s in sorted(servers, key=lambda x: x.ref):
        index.setdefault(("rid", s.ref[0], s.ref[1]), s.ref)
        index.setdefault(("name", s.ref[0], text(s.server_name).lower()), s.ref)
    return index


def resolve_targets(
    targets: Sequence[Mapping[str, Any]], index: Mapping[tuple[str, str, str], ServerRef]
) -> set[ServerRef]:
    """210 대상 목록 → 스냅샷 서버.

    `kind=sms` · `target_gone` 아님만 본다. 자원 id로 찾고, 없으면 서버명(소문자)으로 찾는다.
    """
    out: set[ServerRef] = set()
    for t in usable_targets(list(targets), max_targets=len(targets) or 1):
        db, rid = text(t.get("db_id")), text(t.get("resource_id"))
        ref = index.get(("rid", db, rid)) if rid else None
        if ref is None:
            ref = index.get(("name", db, text(t.get("server_name")).lower()))
        if ref is not None:
            out.add(ref)
    return out


def merge_axis_one(
    servers: Sequence[SmsServer],
    *,
    config: BizServiceConfig,
    generic: frozenset[str],
    attrs: Sequence[ServerAttr] = (),
    apm_maps: Sequence[ApmMapRow] = (),
    itam_apps: Sequence[ItamAppRow] = (),
    itam_servers: Sequence[ItamServerRow] = (),
    confirmed_stored: Mapping[ServerRef, str] | None = None,
    attr_failures: Sequence[Any] = (),
) -> AxisOne:
    """축 ① S1~S7 합치기(§3.2).

    서버 배정 · 서비스 초안 · 충돌 · S6 다업무 후보 · S6·S7 존별 상태를 돌려준다.
    """
    by_ref = {s.ref: s for s in sorted(servers, key=lambda x: x.ref)}
    s1: dict[ServerRef, str] = {}
    s1_raw: dict[ServerRef, str] = {}
    for ref, s in by_ref.items():
        key, raw = s1_business(s.server_name, generic)
        if key:
            s1[ref], s1_raw[ref] = key, raw
    ctx = _Ctx(by_ref, s1, s1_raw, frozenset(s1.values()), generic, _itam_apps_by_code(itam_apps))
    confirmed = dict(s1)
    confirmed.update(confirmed_stored or {})

    hp = config.host_prefix
    codes: dict[ServerRef, str] = {}
    mismatch: dict[ServerRef, bool] = {}
    for ref, s in by_ref.items():
        codes[ref], mismatch[ref] = extract_prefix_code(s, hp)
    attr_by = {a.ref: a for a in attrs}
    trees = {ref: t for ref, s in by_ref.items()
             if (t := tree_group_of(s, attr_by.get(ref), generic)) is not None}

    s6_status = _zone_status(SIG_HOST_PREFIX, ctx, codes, config, confirmed, attr_failures)
    s7_status = _zone_status(SIG_TREE_GROUP, ctx, {r: t.norm for r, t in trees.items()}, config,
                             confirmed, attr_failures)
    on6 = {z for z, st in s6_status.items() if st.enabled}
    on7 = {z for z, st in s7_status.items() if st.enabled}

    groups: list[MediumGroup] = []
    s6, cands, mapping = _s6_groups(ctx, codes, on6, config, s6_status)
    s7, mixed_trees = _s7_groups(ctx, trees, on7, config)
    groups += _s2_groups(ctx, apm_maps) + _s3_groups(ctx, itam_apps, itam_servers) + s6 + s7

    conflicts: list[Conflict] = []
    uf = _UnionFind()
    valid: list[MediumGroup] = []
    for g in groups:
        if len(g.strong) >= 2:
            conflicts.append(Conflict(CONFLICT_SMS, g.label, tuple(sorted(g.strong)), g.signal, {
                "servers": sorted(ref_key(r) for r in g.all_refs),
                "tailless": sorted(ref_key(r) for r in g.refs),
            }))
            continue
        valid.append(g)
        for ref in g.refs:
            uf.union(("g", g.label), ("s", ref))

    comp_groups: dict[tuple[str, Any], list[MediumGroup]] = defaultdict(list)
    comp_refs: dict[tuple[str, Any], set[ServerRef]] = defaultdict(set)
    for g in valid:
        if not g.refs:
            continue
        root = uf.find(("g", g.label))
        comp_groups[root].append(g)
        comp_refs[root] |= set(g.refs)

    assignments: dict[ServerRef, Assignment] = {}
    drafts: dict[str, ServiceDraft] = {}
    for key in sorted(ctx.s1_keys):
        raws = [s1_raw[r] for r in sorted(s1) if s1[r] == key]
        drafts[key] = ServiceDraft(_most_common(raws), {"business_names": sorted(set(raws))})
    for ref in sorted(s1):
        ev: dict[str, Any] = {}
        code = codes.get(ref, "")
        if code and mapping.get(code) not in (None, s1[ref]):
            ev["prefix_mismatch"] = mapping[code]  # 「접두 코드 불일치」 — S1이 이긴다
        if mismatch.get(ref):
            ev["host_mismatch"] = True
        _tree_cross_check(ev, trees.get(ref), code)
        assignments[ref] = Assignment(s1[ref], SIG_BIZ_SUFFIX, CONF_CONFIRMED,
                                      (SIG_BIZ_SUFFIX,), ev)

    for root in sorted(comp_groups, key=repr):
        members = sorted(comp_refs[root])
        gs = sorted(comp_groups[root], key=lambda g: (_label_rank(g.label), g.label, g.signal))
        strong = frozenset().union(*(g.strong for g in gs))
        if len(strong) >= 2:
            for ref in members:
                sigs = sorted({g.signal for g in gs if ref in g.refs})
                conflicts.append(Conflict(CONFLICT_SMS, ref_key(ref), tuple(sorted(strong)),
                                          sigs[0], {"labels": sorted({g.label for g in gs})}))
            continue
        if strong:
            service = next(iter(strong))
        else:
            service = gs[0].label
            ev_s: dict[str, Any] = dict(gs[0].evidence)
            ev_s["labels"] = sorted({g.label for g in gs})
            drafts.setdefault(service, ServiceDraft(gs[0].display, ev_s))
        for ref in members:
            signals = tuple(sorted({g.signal for g in gs if ref in g.refs},
                                   key=BASIS_PRIORITY.index))
            ev = {}
            if mismatch.get(ref):
                ev["host_mismatch"] = True
            _tree_cross_check(ev, trees.get(ref), codes.get(ref, ""))
            assignments[ref] = Assignment(service, signals[0], CONF_INFERRED, signals, ev)

    statuses = [*s6_status.values(), *s7_status.values()]
    return AxisOne(
        assignments, drafts, tuple(sorted(conflicts, key=_conflict_sort)), tuple(cands),
        tuple(sorted(statuses, key=lambda s: (s.signal, s.zone))), codes,
        {"mixed_tree_groups": mixed_trees, "invalid_prefix_codes": len(invalid_codes(cands))},
    )


def invalid_codes(cands: Sequence[RawCandidate]) -> set[str]:
    """S6 다업무 코드(후보만 낸 코드) 집합."""
    return {c.signal.bundle_key for c in cands
            if c.signal.basis == SIG_HOST_PREFIX and not c.new_service_name}


def _conflict_sort(c: Conflict) -> tuple[str, str, str]:
    return (c.kind, c.target_key, c.signal)


def _tree_cross_check(ev: dict[str, Any], tree: TreeGroup | None, code: str) -> None:
    """S7 교차 확인(§3.2.2 5) — 트리 코드와 접두 코드가 같으면/다르면 근거만 남긴다(확신도 불변)."""
    if tree is None or not tree.tree_code or not code:
        return
    ev["tree_code_matches_prefix"] = tree.tree_code.lower() == code


# ── S4 · S5 후보 ────────────────────────────────────────────────────────
def stem_candidates(
    servers: Sequence[SmsServer],
    *,
    effective: Mapping[ServerRef, str],
    eligible: Collection[ServerRef],
    blocked: Collection[tuple[ServerRef, str]] = (),
) -> list[RawCandidate]:
    """S4 — 같은 stem(≥4자) 서버가 2대 이상이고 배정된 stem 동료가 한 서비스뿐이면, 미배정
    서버 → 그 서비스 후보.

    미배정 서버에만 낸다 — 이미 S6 등으로 서비스에 든 서버(같은 S6 서비스 안)의 S4 후보는 없다.
    """
    by_stem: dict[str, list[ServerRef]] = defaultdict(list)
    for s in sorted(servers, key=lambda x: x.ref):
        stem = host_stem(s)
        if len(stem) >= MIN_STEM_LEN:
            by_stem[stem].append(s.ref)
    eligible_set, blocked_set = set(eligible), set(blocked)
    out: list[RawCandidate] = []
    for stem in sorted(by_stem):
        refs = by_stem[stem]
        if len(refs) < 2:
            continue
        for x in refs:
            if x not in eligible_set:
                continue
            mates = [effective[r] for r in refs if r != x and r in effective]
            if not mates or len(set(mates)) != 1 or (x, mates[0]) in blocked_set:
                continue
            out.append(RawCandidate(x, mates[0], CandidateSignal(SIG_HOST_STEM, stem, len(mates))))
    return out


def _display_tokens(name: str, generic: Collection[str]) -> set[str]:
    spaced = re.sub(r"\s+", "-", unicodedata.normalize("NFKC", text(name)).lower())
    return {t for t in name_tokens(spaced, min_len=2) if t not in generic}


def merge_candidates(
    names: Mapping[str, str], generic: Collection[str]
) -> list[tuple[str, str, CandidateSignal]]:
    """S5 — 서비스 표시명끼리 `name_tokens` 겹침 → 병합 후보 `(앞 키, 뒤 키, 근거)`.

    겹침 = 같은 토큰 · 또는 3자 이상 토큰의 포함(`인터넷뱅킹` ⊂ `인터넷뱅킹시스템`).

    약어(「인뱅」)는 잡지 못한다.
    """
    tokens = {k: _display_tokens(v, generic) for k, v in names.items()}
    keys = sorted(k for k in tokens if tokens[k])
    out: list[tuple[str, str, CandidateSignal]] = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            shared = sorted(
                min(x, y, key=len) for x in tokens[a] for y in tokens[b]
                if x == y or (min(len(x), len(y)) >= 3 and (x in y or y in x))
            )
            if shared:
                out.append((a, b, CandidateSignal(SIG_NAME_TOKENS, shared[0], len(shared))))
    return out
