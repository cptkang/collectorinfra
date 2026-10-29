"""유사 호스트 후보 검색 · 선택 서버 재확인 (plans/196 §1.1 ① · §5.5).

제니퍼 hostname으로 관측 DB 소스마다 고정 SELECT 2회(① a·b·c·e 한 번 · d 토큰 넓힘 한 번)를 돌려
행을 모으고, 등급·점수·사유는 `noise_gate.domain.host_link`가 매긴다(LLM 0).

규약:
- **소스 순서·인가는 호출부가 정한다** — `db_ids`는 인가된 존의 소스만, 조회 순서대로 받는다
  (`host_sweep.authorized_zones` 규약 · 이 모듈은 사용자를 모른다).
- 소스별 개별 try — 실패한 소스는 `unchecked`(「확인하지 못함」)로 남기고 「후보 없음」과 합치지
  않는다.
- 캐시하지 않는다(방금 등록된 서버를 놓치지 않게 · D-224 entity_locator 규약 4). 저장된 확정
  연결은 캐시가 아니라 사실이라 그대로 읽는다.

계층: application — domain(host_link) + infrastructure(리졸버 프로브 · 연결 저장소) 소비.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from src.domain.host_names import normalize_hostname

from noise_gate.domain.host_link import (
    MAX_CANDIDATES,
    build_probe_plan,
    NODE_UNCHECKED,
    is_confirmed_link,
    node_address_match,
    public_link_view,
    rank_candidates,
    valid_server_text,
)

logger = logging.getLogger(__name__)

#: `(db_id, names, prefixes, ips) -> 행 목록` — 미지정이면 `probe_hosts`(① a·b·c·e).
ProbeFn = Callable[[str, list[str], list[str], list[str]], Awaitable[list[dict[str, Any]]]]
#: `(db_id, tokens) -> 행 목록` — 미지정이면 `probe_host_tokens`(① d).
TokenProbeFn = Callable[[str, list[str]], Awaitable[list[dict[str, Any]]]]


def _default_probes(app_config: Any) -> tuple[ProbeFn, TokenProbeFn]:
    from noise_gate.infrastructure.polestar_hostname_resolver import (
        probe_host_tokens,
        probe_hosts,
    )

    async def _probe(
        db_id: str, names: list[str], prefixes: list[str], ips: list[str]
    ) -> list[dict[str, Any]]:
        return await probe_hosts(app_config, db_id, names=names, prefixes=prefixes, ips=ips)

    async def _tokens(db_id: str, tokens: list[str]) -> list[dict[str, Any]]:
        return await probe_host_tokens(app_config, db_id, tokens)

    return _probe, _tokens


#: 노드 주소를 찾을 관측 소스의 소유 시스템 키(레지스트리 · 후보 검색 라우트와 같은 값).
NODE_LOOKUP_SYSTEM = "polestar"


def node_lookup_db_ids(app_config: Any) -> list[str]:
    """노드 주소를 찾을 소스 — 그 시스템의 활성 DB **전부**(존 조회 순서 · plans/199 §2.2).

    순서는 실행 그룹 정본(은행존 → 공동존)이고 존 미배정 DB는 뒤에 붙인다(`host_sweep` ·
    `entity_locator.probe_scope`와 같은 규약 — 조립 계층을 import하지 않으려고 같은 정본
    `partition_execution_groups`를 직접 쓴다). 사용자 인가 필터는 없다 — 자동 경로는 시스템
    주체다(D-352 ② 「전 존에서 정확히 1대」).
    """
    from src.routing.execution_groups import partition_execution_groups
    from src.routing.registry import get_registry

    multi_db = getattr(app_config, "multi_db", None)
    active = set(multi_db.get_active_db_ids()) if multi_db is not None else set()
    members = [d for d in get_registry().system_db_ids(NODE_LOOKUP_SYSTEM) if d in active]
    ordered: list[str] = []
    for group in partition_execution_groups(list(members)):
        for db_id in group.get("db_ids") or []:
            if db_id in members and db_id not in ordered:
                ordered.append(db_id)
    return ordered + [d for d in members if d not in ordered]


async def node_address_link(
    app_config: Any,
    *,
    host_address: str,
    db_ids: Sequence[str] | None = None,
    probe: ProbeFn | None = None,
) -> dict[str, Any]:
    """파드 노드 주소를 소스 전부에서 IP 정확 일치로 찾는다 (plans/199 §2.2 · D-352 ②).

    소스별 개별 try — 실패한 소스가 있으면 `node_unchecked`로 남기고 「없음」과 합치지 않는다.
    캐시하지 않는다(방금 등록된 서버 · D-224 규약 4). 결과를 저장하지 않는다(알람 시점 사실).

    Returns:
        `node_address_match` 판정 + `unchecked`(실패 소스 `[{db_id, reason}]`).
    """
    probe = probe or _default_probes(app_config)[0]
    sources = list(db_ids) if db_ids is not None else node_lookup_db_ids(app_config)
    rows: list[tuple[str, Mapping[str, Any]]] = []
    unchecked: list[dict[str, str]] = []
    for db_id in sources:
        try:
            found = await probe(db_id, [], [], [host_address])
        except Exception as exc:  # noqa: BLE001 — 소스별 개별 실패(확인하지 못함)
            logger.warning(
                "노드 주소 조회 실패 — 확인하지 못함: db_id=%s addr=%s err=%s",
                db_id, host_address, exc,
            )
            unchecked.append({"db_id": db_id, "reason": _unchecked_reason(exc)})
            continue
        rows.extend((db_id, row) for row in found)
    if not sources:
        logger.warning("노드 주소 조회 대상 소스 없음 — 확인하지 못함: addr=%s", host_address)
        result = {"status": "unresolved", "reason": NODE_UNCHECKED}
    else:
        result = node_address_match(rows, host_address=host_address, unchecked=bool(unchecked))
    logger.info(
        "노드 주소 연결: addr=%s 소스=%d 행=%d 확인못함=%d status=%s reason=%s db_id=%s",
        host_address, len(sources), len(rows), len(unchecked), result.get("status"),
        result.get("reason", ""), result.get("db_id", ""),
    )
    return {**result, "unchecked": unchecked}


def _unchecked_reason(exc: BaseException) -> str:
    """「확인하지 못함」 사유 — 예외 종류만(SQL·접속 정보는 서버 로그에만)."""
    if isinstance(exc, LookupError):
        return "활성 소스가 아닙니다"
    return f"조회 실패({type(exc).__name__})"


async def find_host_candidates(
    app_config: Any,
    *,
    hostname: str,
    source_id: str,
    db_ids: Sequence[str],
    store: Any = None,  # HostLinkStore | None
    ip: str | None = None,
    probe: ProbeFn | None = None,
    token_probe: TokenProbeFn | None = None,
    limit: int = MAX_CANDIDATES,
    reveal_actor: bool = True,
) -> dict[str, Any]:
    """유사 호스트 후보를 찾는다 → `{hostname, source_id, link, candidates, unchecked}`.

    Args:
        app_config: 앱 설정(기본 프로브용)
        hostname: 제니퍼 hostname 원문
        source_id: 제니퍼 소스 id(연결 키)
        db_ids: 인가된 소스(조회 순서)
        store: 연결 저장소(없으면 확정 연결·패턴 없이 검색)
        ip: 인스턴스 IP(있으면 ① c)
        probe·token_probe: 조회 대역(테스트 주입점)
        limit: 후보 상한
        reveal_actor: False면(일반 사용자) `link`를 `public_link_view`로 줄이고 확정 사유에서
            누가·언제를 뺀다

    Returns:
        `link`는 저장 연결(없으면 None). 저장소 조회가 실패하면 `link_error`에 사유를 더한다.
    """
    default_probe, default_tokens = _default_probes(app_config)
    probe = probe or default_probe
    token_probe = token_probe or default_tokens
    link: dict[str, Any] | None = None
    patterns: dict[str, int] = {}
    out: dict[str, Any] = {"hostname": hostname, "source_id": source_id}
    if store is not None:
        try:
            link = await store.get(source_id, hostname)
            patterns = await store.patterns()
        except Exception as exc:  # noqa: BLE001 — 저장소 실패가 후보 검색을 막지 않는다
            logger.warning("호스트 연결 저장소 조회 실패: host=%s", hostname, exc_info=True)
            out["link_error"] = f"연결 저장소 조회 실패({type(exc).__name__})"
    plan = build_probe_plan(hostname, ip=ip, patterns=patterns)
    confirmed = link if is_confirmed_link(link) else None
    if not reveal_actor:
        link = public_link_view(link)
        confirmed = public_link_view(confirmed)

    rows: list[tuple[str, Mapping[str, Any]]] = []
    unchecked: list[dict[str, str]] = []
    for db_id in db_ids:
        names = list(plan.names)
        if confirmed is not None and str(confirmed.get("db_id")) == db_id:
            linked = str(confirmed.get("server_name") or "").strip().lower()
            if linked and linked not in names:
                names.append(linked)
        try:
            found: list[dict[str, Any]] = []
            if names or plan.prefixes or plan.ips:
                found.extend(await probe(db_id, names, list(plan.prefixes), list(plan.ips)))
            if plan.tokens:
                found.extend(await token_probe(db_id, list(plan.tokens)))
        except Exception as exc:  # noqa: BLE001 — 소스별 개별 실패(확인하지 못함)
            logger.warning(
                "유사 호스트 후보 조회 실패 — 확인하지 못함: db_id=%s host=%s err=%s",
                db_id, hostname, exc,
            )
            unchecked.append({"db_id": db_id, "reason": _unchecked_reason(exc)})
            continue
        rows.extend((db_id, row) for row in found)
    candidates = rank_candidates(plan, rows, patterns=patterns, confirmed=confirmed, limit=limit)
    logger.info(
        "유사 호스트 후보: host=%s 소스=%d 행=%d 후보=%d 확인못함=%d",
        hostname, len(db_ids), len(rows), len(candidates), len(unchecked),
    )
    out.update({"link": link, "candidates": candidates, "unchecked": unchecked})
    return out


def _row_matches(row: Mapping[str, Any], value: str) -> bool:
    """① a~c 정확 일치 — 서버명·hostname의 정규화 형태가 같거나(a 대소문자 · b FQDN 짧은 이름 ·
    c 짧은 이름 → FQDN) IP 입력이 행 IP와 같다."""
    wanted = normalize_hostname(value)
    if not wanted:
        return False
    if wanted in (
        normalize_hostname(str(row.get("name") or "")),
        normalize_hostname(str(row.get("hostname") or "")),
    ):
        return True
    return wanted == str(row.get("ipaddress") or "").strip().lower()


async def verify_selected_server(
    app_config: Any,
    *,
    db_id: str,
    server_name: str | None,
    hostname: str | None,
    probe: ProbeFn | None = None,
) -> dict[str, Any] | None:
    """사용자가 고르거나 입력한 서버가 그 소스에 있는지 ① a~c 정확 일치로 다시 확인한다.

    서버명 일치 행을 먼저, 없으면 hostname 일치 행을 고른다. 어느 쪽에도 정확히 맞지 않는 행은
    고르지 않는다(넓힘 결과로 대체하지 않는다). 서로 다른 서버가 둘 이상 걸리면 고르지 않는다
    (모호 — None이 아니라 `{"ambiguous": True}`).

    Returns:
        `{"server_name", "hostname", "ipaddress"}` 또는 `{"ambiguous": True}` 또는 None(없음).

    Raises:
        ValueError: 입력이 `[a-z0-9._-]` 밖의 문자를 담았을 때(호출부가 먼저 거른다)
        Exception: 조회 실패(호출부가 「확인하지 못함」으로 응답한다)
    """
    probe = probe or _default_probes(app_config)[0]
    values = [v.strip().lower() for v in (server_name, hostname) if v and v.strip()]
    if not values:
        return None
    if not all(valid_server_text(v) for v in values):
        raise ValueError("서버명·hostname에 허용하지 않는 문자")
    plan_names: list[str] = []
    prefixes: list[str] = []
    ips: list[str] = []
    for value in values:
        plan = build_probe_plan(value)
        plan_names.extend(n for n in plan.names if n not in plan_names)
        prefixes.extend(p for p in plan.prefixes if p not in prefixes)
        ips.extend(i for i in plan.ips if i not in ips)
    rows = await probe(db_id, plan_names, prefixes, ips)
    picked: list[Mapping[str, Any]] = []
    for value in values:
        picked = [r for r in rows if _row_matches(r, value)]
        if picked:
            break
    if not picked:
        return None
    distinct = {
        (str(r.get("name") or "").strip().lower(), str(r.get("hostname") or "").strip().lower())
        for r in picked
    }
    if len(distinct) > 1:
        return {"ambiguous": True}
    row = picked[0]
    return {
        "server_name": str(row.get("name") or "").strip(),
        "hostname": str(row.get("hostname") or "").strip(),
        "ipaddress": str(row.get("ipaddress") or "").strip(),
    }


async def locate_selected_server(
    app_config: Any,
    *,
    db_ids: Sequence[str],
    server_name: str | None,
    hostname: str | None,
    probe: ProbeFn | None = None,
) -> dict[str, Any]:
    """소스 없이 입력한 서버를 인가된 소스 전부에서 확인한다(직접 입력 · 후보 0건 경로).

    동명 서버가 여러 존에 있을 수 있어 전수 확인한다(첫 발견에서 끊지 않는다 · host_sweep 규약).

    Returns:
        `{"db_id", "row", "ambiguous", "unchecked"}` — 정확히 한 소스에서 한 서버가 맞으면
        `db_id`·`row`, 둘 이상이면 `ambiguous=True`, 실패한 소스는 `unchecked`(사유는 종류만).

    Raises:
        ValueError: 입력 문자 위반(`verify_selected_server`와 같다)
    """
    if not all(valid_server_text(v) for v in (server_name, hostname) if v and v.strip()):
        raise ValueError("서버명·hostname에 허용하지 않는 문자")
    found: list[tuple[str, dict[str, Any]]] = []
    unchecked: list[dict[str, str]] = []
    ambiguous = False
    for db_id in db_ids:
        try:
            row = await verify_selected_server(
                app_config, db_id=db_id, server_name=server_name, hostname=hostname, probe=probe
            )
        except Exception as exc:  # noqa: BLE001 — 소스별 개별 실패(확인하지 못함)
            logger.warning("직접 입력 서버 확인 실패 — 확인하지 못함: db_id=%s err=%s", db_id, exc)
            unchecked.append({"db_id": db_id, "reason": _unchecked_reason(exc)})
            continue
        if row is None:
            continue
        if row.get("ambiguous"):
            ambiguous = True
        else:
            found.append((db_id, row))
    if ambiguous or len(found) > 1:
        return {"db_id": None, "row": None, "ambiguous": True, "unchecked": unchecked}
    if not found:
        return {"db_id": None, "row": None, "ambiguous": False, "unchecked": unchecked}
    return {"db_id": found[0][0], "row": found[0][1], "ambiguous": False, "unchecked": unchecked}


async def polestar_link_for(
    store: Any,  # HostLinkStore | None
    *,
    source_id: str,
    hostname: str,
) -> dict[str, Any]:
    """자동 경로의 `meta.hints.polestar_link`(확정 연결만 · 저장소 실패는 `unresolved`)."""
    from datetime import UTC, datetime

    from noise_gate.domain.host_link import polestar_link_hint

    if store is None or not hostname:
        return {"status": "unresolved"}
    try:
        link = await store.get(source_id, hostname)
    except Exception:  # noqa: BLE001 — 저장소 실패가 조사 제출을 막지 않는다
        logger.warning(
            "호스트 연결 조회 실패 — 선택 대기로 진행: source=%s host=%s",
            source_id, hostname, exc_info=True,
        )
        return {"status": "unresolved"}
    return polestar_link_hint(link, datetime.now(UTC))


__all__ = [
    "NODE_LOOKUP_SYSTEM",
    "find_host_candidates",
    "locate_selected_server",
    "node_address_link",
    "node_lookup_db_ids",
    "polestar_link_for",
    "verify_selected_server",
]
