"""ITAM 업무 추정 사슬 — APM 도메인 → ITAM 업무 → ITAM 서버 → SMS 서버.

plans/210 §3.4 itam · D-362.

스캔 스냅샷을 **메모리에서 대조**하는 순수 규칙만 둔다(LLM 0 · I/O 0). 도메인명·설명 같은
외부 입력은 SQL에 들어가지 않는다 — 관측 DB·ITAM 조회는 스캐너(infrastructure)가
고정 SELECT로 미리 끝낸다.

사슬(첫 성공 단계만 쓴다):
1. 토큰 — 도메인명·설명을 영숫자·한글 덩어리로 쪼개고 일반어(설정)·한 글자를 뺀다.
2. 업무 — 코드 정확 → 명 정확 → 명 포함. 고른 업무의 서버는 서버 원장의 업무 단서 자유 기재 칸에
   업무명이 들어 있는 활성 서버다(원장에 업무 코드 키가 없다 — 「추정」).
3. 업무가 0건이거나 업무 서버가 0대면 서버 단서 칸에 토큰이 직접 들어 있는 활성 서버.
4. ITAM 서버 수가 상한(기본 50)을 넘으면 `ambiguous`. 업무가 서로 다른 서버 집합으로 갈려도
   `ambiguous`.
5. ITAM 서버 → SMS 색인(대표 IP·NIC IP·정규화 hostname — `canonical`이면 정규형 · plans/211 W7)
   정확 일치 1대인 서버만 대상.
6. 대상 1~상한(10) `active` · 0 `failed` · 상한 초과 `ambiguous`.

근거(evidence)는 단계·건수·업무 코드/명·서버 호스트/IP만 싣는다(담당자 등 PII 없음).

계층: domain — stdlib + 같은 패키지 `apm_host_map` + `src.domain.host_names`(stdlib 순수)만 쓴다.
ITAM 테이블·칼럼 이름은 여기 두지 않는다(D-088 — 이름은 `config/knowledge/itam/` 설정이 정본).
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from noise_gate.domain.apm_host_map import (
    BASIS_ITAM,
    DEFAULT_MAX_TARGETS,
    SCOPE_DOMAIN,
    STATE_ACTIVE,
    STATE_AMBIGUOUS,
    STATE_FAILED,
    AutoProposal,
    normalize_targets,
    sms_host_lookup,
    vm_exact_match,
)

#: ITAM 서버 상한 — 넘으면 너무 넓은 추정이라 `ambiguous`.
DEFAULT_MAX_SERVERS = 50
#: 토큰 최소 길이(한 글자는 일반어와 구분이 안 된다).
MIN_TOKEN_LEN = 2

#: 사슬 단계(`evidence.stage`).
STAGE_APP_CODE = "app_code"
STAGE_APP_NAME = "app_name"
STAGE_APP_NAME_CONTAINS = "app_name_contains"
STAGE_SERVER_CLUE = "server_clue"
STAGE_NONE = "none"

#: 결과 사유(`evidence.reason`).
REASON_LINKED = "linked"
REASON_NO_TOKENS = "no_tokens"
REASON_NO_SERVERS = "no_itam_servers"
REASON_TOO_MANY_SERVERS = "too_many_itam_servers"
REASON_APPS_SPLIT = "apps_split"
REASON_NO_SMS_MATCH = "no_sms_match"
REASON_TOO_MANY_TARGETS = "too_many_targets"

#: 근거에 싣는 표본 상한(행 값 나열이 길어지지 않게).
_EVIDENCE_SAMPLE = 10
_TOKEN_SPLIT = re.compile(r"[^0-9A-Za-z가-힣]+")
_IP_SPLIT = re.compile(r"[\s,;/|]+")


@dataclass(frozen=True)
class ItamApp:
    """ITAM 업무 1건(코드·명)."""

    code: str
    name: str


@dataclass(frozen=True)
class ItamServer:
    """ITAM 서버 원장 1건 — `clue`는 업무 단서 자유 기재 칸을 이은 문자열 · `active` None = 미상."""

    hostname: str
    ips: tuple[str, ...] = ()
    clue: str = ""
    active: bool | None = None


@dataclass(frozen=True)
class ItamChainResult:
    """사슬 결과 — `state`(active·ambiguous·failed) · 진단 대상(active일 때만) · 근거."""

    state: str
    targets: tuple[dict[str, Any], ...] = ()
    evidence: Mapping[str, Any] = field(default_factory=dict)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fold(value: Any) -> str:
    """대조용 — 소문자 · 공백 제거."""
    return re.sub(r"\s+", "", _text(value)).lower()


# ── 입력 정규화 ─────────────────────────────────────────────────────────
def parse_ipv4s(raw: Any) -> tuple[str, ...]:
    """IP 칸(다중값 — 쉼표·세미콜론·공백·`/`·`|` 구분 또는 목록) → IPv4만(형식 검증 · 순서 유지)."""
    parts: Iterable[Any]
    if isinstance(raw, (list, tuple)):
        parts = [p for item in raw for p in _IP_SPLIT.split(_text(item))]
    else:
        parts = _IP_SPLIT.split(_text(raw))
    out: list[str] = []
    for part in parts:
        try:
            ip = ipaddress.IPv4Address(_text(part))
        except ValueError:
            continue
        text = str(ip)
        if text not in out:
            out.append(text)
    return tuple(out)


def domain_tokens(texts: Iterable[Any], generic_terms: Collection[str] = ()) -> list[str]:
    """도메인명·설명 → 대조 토큰(소문자 · 일반어·한 글자 제외 · 순서 유지 · 중복 제거)."""
    generic = {_fold(t) for t in generic_terms}
    out: list[str] = []
    for text in texts:
        for token in _TOKEN_SPLIT.split(_text(text)):
            folded = token.lower()
            if len(folded) < MIN_TOKEN_LEN or folded in generic or folded in out:
                continue
            out.append(folded)
    return out


def apps_from_rows(rows: Iterable[Mapping[str, Any]]) -> tuple[ItamApp, ...]:
    """업무 스냅샷 행(`app_code`·`app_name`) → 업무 목록(빈 행 제거 · 중복 제거)."""
    seen: set[tuple[str, str]] = set()
    out: list[ItamApp] = []
    for row in rows:
        code, name = _text(row.get("app_code")), _text(row.get("app_name"))
        if not (code or name) or (code, name) in seen:
            continue
        seen.add((code, name))
        out.append(ItamApp(code, name))
    return tuple(out)


def servers_from_rows(rows: Iterable[Mapping[str, Any]]) -> tuple[ItamServer, ...]:
    """서버 스냅샷 행(`hostname`·`ips`·`clue_text`·`active`) → 서버 목록.

    같은 hostname(대소문자 무시) 행은 하나로 합친다 — IP 합집합 · 단서 이어 붙임 · 활성은 하나라도
    활성이면 활성(전부 미상이면 미상).
    """
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        host = _text(row.get("hostname"))
        if not host:
            continue
        slot = merged.setdefault(host.lower(), {"hostname": host, "ips": [], "clues": [],
                                                "active": []})
        for ip in parse_ipv4s(row.get("ips")):
            if ip not in slot["ips"]:
                slot["ips"].append(ip)
        clue = _text(row.get("clue_text"))
        if clue and clue not in slot["clues"]:
            slot["clues"].append(clue)
        slot["active"].append(row.get("active"))
    out: list[ItamServer] = []
    for slot in merged.values():
        flags = [a for a in slot["active"] if isinstance(a, bool)]
        active = None if not flags else any(flags)
        out.append(ItamServer(slot["hostname"], tuple(slot["ips"]), " | ".join(slot["clues"]),
                              active))
    return tuple(out)


# ── 사슬 단계 ───────────────────────────────────────────────────────────
def match_apps(
    tokens: Sequence[str], domain_name: str, apps: Sequence[ItamApp]
) -> tuple[str, list[ItamApp]]:
    """토큰 → 업무(코드 정확 → 명 정확 → 명 포함 · 첫 성공 단계) → `(단계, 업무 목록)`.

    명 정확은 토큰 또는 공백을 뺀 도메인명 전체와 같은 업무명이다. 0건이면 `(STAGE_NONE, [])`.
    """
    token_set = set(tokens)
    by_code = [a for a in apps if a.code and a.code.lower() in token_set]
    if by_code:
        return STAGE_APP_CODE, by_code
    whole = token_set | ({_fold(domain_name)} if _fold(domain_name) else set())
    by_name = [a for a in apps if a.name and _fold(a.name) in whole]
    if by_name:
        return STAGE_APP_NAME, by_name
    contains = [
        a for a in apps if a.name and any(t in _fold(a.name) for t in tokens)
    ]
    if contains:
        return STAGE_APP_NAME_CONTAINS, contains
    return STAGE_NONE, []


def _usable(server: ItamServer) -> bool:
    return server.active is not False


def servers_for_app(app: ItamApp, servers: Sequence[ItamServer]) -> list[ItamServer]:
    """업무명이 서버 단서 칸에 들어 있는 활성 서버(대소문자·공백 무시)."""
    name = _fold(app.name)
    if len(name) < MIN_TOKEN_LEN:
        return []
    return [s for s in servers if _usable(s) and name in _fold(s.clue)]


def servers_by_clue(tokens: Sequence[str], servers: Sequence[ItamServer]) -> list[ItamServer]:
    """서버 단서 칸에 토큰 하나라도 직접 들어 있는 활성 서버."""
    if not tokens:
        return []
    return [s for s in servers if _usable(s) and any(t in _fold(s.clue) for t in tokens)]


def _sms_servers(
    server: ItamServer,
    by_ip: Mapping[str, Sequence[Mapping[str, Any]]],
    by_host: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    canonical: bool = False,
) -> list[Mapping[str, Any]] | None:
    """ITAM 서버 1대 → SMS 서버(정확히 1대면 `[서버]` · 0대 `[]` · 2대 이상 None)."""
    ip_hits = [s for ip in server.ips for s in by_ip.get(ip, ())]
    host_hits = list(sms_host_lookup(by_host, server.hostname, canonical=canonical) or ())
    match = vm_exact_match(ip_hits, host_hits)
    if match.status == "linked":
        return [match.targets[0]]
    if match.status == "ambiguous":
        return None
    return []


def _app_evidence(apps: Sequence[ItamApp]) -> list[dict[str, str]]:
    return [{"code": a.code, "name": a.name} for a in apps[:_EVIDENCE_SAMPLE]]


def _host_evidence(servers: Sequence[ItamServer]) -> list[dict[str, str]]:
    return [
        {"hostname": s.hostname, "ip": s.ips[0] if s.ips else ""}
        for s in servers[:_EVIDENCE_SAMPLE]
    ]


def resolve_itam_chain(
    *,
    domain_name: str,
    domain_description: str = "",
    apps: Sequence[ItamApp],
    servers: Sequence[ItamServer],
    by_ip: Mapping[str, Sequence[Mapping[str, Any]]],
    by_host: Mapping[str, Sequence[Mapping[str, Any]]],
    generic_terms: Collection[str] = (),
    max_servers: int = DEFAULT_MAX_SERVERS,
    max_targets: int = DEFAULT_MAX_TARGETS,
    canonical: bool = False,
) -> ItamChainResult:
    """APM 도메인 하나의 ITAM 사슬 판정(§3.4 itam — 모듈 머리 1~6).

    `canonical`은 `by_host`가 `build_sms_index(canonical=True)`로 만든 색인일 때 켠다(같은 키 함수).
    """
    tokens = domain_tokens((domain_name, domain_description), generic_terms)
    evidence: dict[str, Any] = {"source": "itam", "tokens": tokens[:_EVIDENCE_SAMPLE]}
    if not tokens:
        return ItamChainResult(STATE_FAILED, (), {**evidence, "stage": STAGE_NONE,
                                                  "reason": REASON_NO_TOKENS})
    stage, matched = match_apps(tokens, domain_name, apps)
    picked: list[ItamServer] = []
    if matched:
        sets: dict[frozenset[str], list[ItamApp]] = {}
        members: dict[frozenset[str], list[ItamServer]] = {}
        for app in matched:
            found = servers_for_app(app, servers)
            if not found:
                continue
            key = frozenset(s.hostname.lower() for s in found)
            sets.setdefault(key, []).append(app)
            members[key] = found
        evidence.update(stage=stage, app_count=len(matched), apps=_app_evidence(matched))
        if len(sets) > 1:
            return ItamChainResult(STATE_AMBIGUOUS, (), {
                **evidence, "reason": REASON_APPS_SPLIT, "server_sets": len(sets),
                "itam_servers": len({h for k in sets for h in k}),
            })
        if sets:
            picked = next(iter(members.values()))
    if not picked:
        picked = servers_by_clue(tokens, servers)
        if picked:
            evidence.update(stage=STAGE_SERVER_CLUE)
        else:
            evidence.setdefault("stage", stage)
    evidence["itam_servers"] = len(picked)
    if not picked:
        return ItamChainResult(STATE_FAILED, (), {**evidence, "reason": REASON_NO_SERVERS})
    cap_servers = max(1, int(max_servers))
    if len(picked) > cap_servers:
        return ItamChainResult(STATE_AMBIGUOUS, (), {**evidence,
                                                     "reason": REASON_TOO_MANY_SERVERS})
    evidence["itam_hosts"] = _host_evidence(picked)
    found_targets: list[Mapping[str, Any]] = []
    unmatched = ambiguous = 0
    for server in picked:
        hits = _sms_servers(server, by_ip, by_host, canonical=canonical)
        if hits is None:
            ambiguous += 1
        elif not hits:
            unmatched += 1
        else:
            found_targets.extend(hits)
    targets = normalize_targets(found_targets, None)
    evidence.update(matched=len(targets), unmatched=unmatched, ambiguous_servers=ambiguous)
    if not targets:
        return ItamChainResult(STATE_FAILED, (), {**evidence, "reason": REASON_NO_SMS_MATCH})
    if len(targets) > max(1, int(max_targets)):
        return ItamChainResult(STATE_AMBIGUOUS, (), {**evidence,
                                                     "reason": REASON_TOO_MANY_TARGETS})
    return ItamChainResult(STATE_ACTIVE, tuple(targets), {**evidence, "reason": REASON_LINKED})


def itam_proposal(
    *, domain_id: str, domain_name: str, result: ItamChainResult, instance_example: str = "",
    extra_evidence: Mapping[str, Any] | None = None,
) -> AutoProposal:
    """사슬 결과 → 도메인 기본값 행 제안(scope domain · match_key `""` · basis itam)."""
    return AutoProposal(
        domain_id=_text(domain_id),
        scope=SCOPE_DOMAIN,
        match_key="",
        basis=BASIS_ITAM,
        state=result.state,
        targets=tuple(result.targets),
        evidence={**dict(result.evidence), **dict(extra_evidence or {})},
        domain_name=_text(domain_name),
        instance_example=_text(instance_example),
    )


__all__ = [
    "DEFAULT_MAX_SERVERS",
    "MIN_TOKEN_LEN",
    "REASON_APPS_SPLIT",
    "REASON_LINKED",
    "REASON_NO_SERVERS",
    "REASON_NO_SMS_MATCH",
    "REASON_NO_TOKENS",
    "REASON_TOO_MANY_SERVERS",
    "REASON_TOO_MANY_TARGETS",
    "STAGE_APP_CODE",
    "STAGE_APP_NAME",
    "STAGE_APP_NAME_CONTAINS",
    "STAGE_NONE",
    "STAGE_SERVER_CLUE",
    "ItamApp",
    "ItamChainResult",
    "ItamServer",
    "apps_from_rows",
    "domain_tokens",
    "itam_proposal",
    "match_apps",
    "parse_ipv4s",
    "resolve_itam_chain",
    "servers_by_clue",
    "servers_for_app",
    "servers_from_rows",
]
