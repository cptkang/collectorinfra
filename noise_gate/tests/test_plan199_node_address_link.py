"""plans/199 §2.2 · D-352 — 제니퍼 파드 알람의 노드 주소 연결(noise_gate W3).

DB·Redis는 띄우지 않는다 — 프로브는 대역 함수, Redis는 `tests.mocks.async_redis.FakeAsyncRedis`.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
from types import SimpleNamespace
from typing import Any

import pytest
from tests.mocks.async_redis import FakeAsyncRedis

from noise_gate.application import host_candidates as hc
from noise_gate.application.nodes import investigation_trigger as trig
from noise_gate.domain import host_link as hl
from noise_gate.infrastructure.host_link_store import HostLinkStore

POD = "bfm-kbland-admin-price-deploy-6dd49d5df6"
NODE_IP = "198.51.100.7"  # 문서용 대역(RFC 5737)
SOURCES = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]


def _run(coro):  # noqa: ANN001, ANN202
    return asyncio.run(coro)


def _row(name: str, ip: str = NODE_IP, hostname: str | None = None) -> dict[str, Any]:
    return {"name": name, "hostname": hostname or name.lower(), "ipaddress": ip}


class _FakeProbe:
    """소스별 응답(행 목록 또는 예외)을 돌려주고 호출을 기록하는 프로브 대역."""

    def __init__(self, table: dict[str, Any]) -> None:
        self.table = table
        self.calls: list[tuple[str, list[str], list[str], list[str]]] = []

    async def __call__(
        self, db_id: str, names: list[str], prefixes: list[str], ips: list[str]
    ) -> list[dict[str, Any]]:
        self.calls.append((db_id, names, prefixes, ips))
        value = self.table.get(db_id, [])
        if isinstance(value, BaseException):
            raise value
        return list(value)


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    fake = _FakeProbe({})

    def _defaults(app_config: Any):  # noqa: ANN202
        return fake, None

    monkeypatch.setattr(hc, "_default_probes", _defaults)
    monkeypatch.setattr(hc, "node_lookup_db_ids", lambda app_config: list(SOURCES))
    return fake


def _apm(**apm: Any) -> dict[str, Any]:
    return {"apm": {"source_id": "b0", **apm}}


def _event(raw: Any = None, **over: Any) -> SimpleNamespace:
    base = dict(
        db_id="jennifer_b0", server_name=POD, hostname=POD, alarm_id="a1", severity=3,
        alarm_name="SQL_TOOMANY_FETCH", resource_type="apm.Instance", resource_name="",
        alarm_time=None, conditions="", condition_log="",
        raw_payload=raw if raw is not None else {"apm": {"source_id": "b0"}},
    )
    base.update(over)
    return SimpleNamespace(**base)


def _pod_event(status: str = "ok", addr: str = NODE_IP) -> SimpleNamespace:
    return _event(_apm(pod=True, host_address=addr, host_address_status=status))


def _cfg(host_link: bool) -> SimpleNamespace:
    return SimpleNamespace(
        noise_gate=SimpleNamespace(host_link_enabled=host_link),
        composite=SimpleNamespace(availability_precheck_enabled=False),
    )


def _payload() -> dict[str, Any]:
    return {"meta": {"hints": {"solution": "apm", "source_id": "b0"}}}


def _attach(cfg: Any, event: Any, **kw: Any) -> dict[str, Any]:
    payload = _payload()
    _run(trig._attach_polestar_link(cfg, event, payload, **kw))
    return payload["meta"]["hints"]


def _manual(cfg: Any, event: Any, **kw: Any) -> dict[str, Any]:
    decision = SimpleNamespace(tier="page", reason="r", fingerprint="fp", signals={})
    return _run(trig.build_manual_payload(
        cfg, event, decision, fingerprint="manual:a1:x", investigation_kind="manual", **kw
    ))


async def _confirmed_store(db_id: str, server_name: str) -> HostLinkStore:
    store = HostLinkStore(FakeAsyncRedis())
    await store.save("b0", POD, kind="link", confirmed=True, by="op", by_role="operator",
                     db_id=db_id, server_name=server_name, polestar_hostname=server_name.lower())
    return store


# ── 순수 판정 ───────────────────────────────────────────────────────────
def test_match_exactly_one_server_links():
    rows = [("polestar_cm_gp", _row("NODE-01")), ("polestar_cm_gp", _row("NODE-01"))]
    out = hl.node_address_match(rows, host_address=NODE_IP)
    assert out == {"status": "linked", "db_id": "polestar_cm_gp", "server_name": "NODE-01",
                   "hostname": "node-01"}


def test_match_zero_two_and_unchecked():
    assert hl.node_address_match([])["reason"] == hl.NODE_NOT_REGISTERED
    two = [("polestar_b0", _row("A")), ("polestar_cm_gp", _row("A"))]
    assert hl.node_address_match(two)["reason"] == hl.NODE_AMBIGUOUS
    assert hl.node_address_match([], unchecked=True)["reason"] == hl.NODE_UNCHECKED
    # 일부 실패 + 1대 → 연결한다(D-352 부기 2 · 실패 소스는 호출자가 근거에 적는다)
    one = [("polestar_cm_gp", _row("A"))]
    assert hl.node_address_match(one, unchecked=True)["status"] == "linked"
    # 일부 실패여도 이미 2대면 모호
    assert hl.node_address_match(two, unchecked=True)["reason"] == hl.NODE_AMBIGUOUS


def test_match_ignores_rows_with_other_ip():
    rows = [("polestar_cm_gp", _row("A")), ("polestar_cm_gp", _row("B", ip="198.51.100.8"))]
    assert hl.node_address_match(rows, host_address=NODE_IP)["server_name"] == "A"


# ── 소스 순회 ───────────────────────────────────────────────────────────
def test_node_lookup_db_ids_zone_order_and_active_only():
    cfg = SimpleNamespace(multi_db=SimpleNamespace(
        get_active_db_ids=lambda: ["polestar_cm_yd", "itam", "polestar_cm_gp", "polestar_b0"]
    ))
    ids = hc.node_lookup_db_ids(cfg)
    assert ids[0] == "polestar_b0"
    assert set(ids) == {"polestar_b0", "polestar_cm_gp", "polestar_cm_yd"}


def test_node_address_link_probes_every_source_with_ip_only(probe):  # noqa: ANN001
    probe.table = {"polestar_cm_gp": [_row("NODE-01")]}
    out = _run(hc.node_address_link(SimpleNamespace(), host_address=NODE_IP))
    assert out["status"] == "linked" and out["db_id"] == "polestar_cm_gp"
    assert [c[0] for c in probe.calls] == SOURCES
    assert all(c[1:] == ([], [], [NODE_IP]) for c in probe.calls)


# ── 힌트 · 사유 ─────────────────────────────────────────────────────────
def test_b0_alarm_links_to_other_zone_server(probe):  # noqa: ANN001
    probe.table = {"polestar_cm_gp": [_row("NODE-01")]}
    link = _attach(_cfg(True), _pod_event(), redis=FakeAsyncRedis())["polestar_link"]
    assert link["status"] == "linked" and link["basis"] == "node_address"
    assert (link["db_id"], link["server_name"], link["hostname"]) == (
        "polestar_cm_gp", "NODE-01", "node-01")
    assert link["host_address"] == NODE_IP and link["at"]
    assert "previous_link" not in link and "pod" not in link


@pytest.mark.parametrize(
    ("table", "reason"),
    [
        ({}, "node_not_registered"),
        ({"polestar_b0": [_row("A")], "polestar_cm_gp": [_row("B")]}, "node_ambiguous"),
        ({"polestar_cm_yd": RuntimeError("down")}, "node_unchecked"),
        ({"polestar_b0": LookupError("x")}, "node_unchecked"),
    ],
)
def test_unresolved_reasons(probe, table, reason):  # noqa: ANN001
    probe.table = table
    link = _attach(_cfg(True), _pod_event(), redis=FakeAsyncRedis())["polestar_link"]
    assert link == {"status": "unresolved", "reason": reason, "pod": True,
                    "host_address": NODE_IP}
    assert len(probe.calls) == len(SOURCES)


@pytest.mark.parametrize("status", ["stale", "error", "login_failed", "login_blocked",
                                    "not_found", "disabled"])
def test_gateway_status_not_ok_skips_probe(probe, status):  # noqa: ANN001
    probe.table = {"polestar_cm_gp": [_row("NODE-01")]}
    for on in (True, False):
        link = _attach(_cfg(on), _pod_event(status=status))["polestar_link"]
        assert link == {"status": "unresolved", "reason": f"node_lookup_{status}", "pod": True}
    assert probe.calls == []


@pytest.mark.parametrize("on", [True, False])
@pytest.mark.parametrize(
    "raw",
    [
        {"apm": {"source_id": "b0"}},
        _apm(pod=False, host_address=NODE_IP, host_address_status="ok"),
        _apm(host_address=NODE_IP, host_address_status="ok"),
        None,
    ],
)
def test_not_pod_is_bit_identical(probe, on, raw):  # noqa: ANN001
    probe.table = {"polestar_cm_gp": [_row("NODE-01")]}
    redis = FakeAsyncRedis()
    with_key = _attach(_cfg(on), _event(raw if raw is not None else {}), redis=redis)
    baseline = _attach(_cfg(on), _event({"apm": {"source_id": "b0"}}), redis=redis)
    assert with_key == baseline
    assert probe.calls == []
    if on:
        assert with_key["polestar_link"] == {"status": "unresolved"}
    else:
        assert "polestar_link" not in with_key


def test_host_link_disabled_still_links_pod(probe):  # noqa: ANN001
    probe.table = {"polestar_cm_gp": [_row("NODE-01")]}
    link = _attach(_cfg(False), _pod_event())["polestar_link"]
    assert link["status"] == "linked" and link["basis"] == "node_address"


def test_non_apm_untouched_even_for_pod(probe):  # noqa: ANN001
    payload = {"meta": {"hints": {"solution": "other"}}}
    _run(trig._attach_polestar_link(_cfg(True), _pod_event(), payload))
    assert "polestar_link" not in payload["meta"]["hints"]
    assert probe.calls == []


# ── 저장 연결과의 우선순위 ──────────────────────────────────────────────
def test_node_link_beats_stored_and_records_previous(probe):  # noqa: ANN001
    probe.table = {"polestar_cm_gp": [_row("NODE-01")]}
    store = _run(_confirmed_store("polestar_b0", "OLD-01"))
    link = _attach(_cfg(True), _pod_event(), store=store)["polestar_link"]
    assert link["basis"] == "node_address" and link["db_id"] == "polestar_cm_gp"
    assert link["previous_link"] == {"db_id": "polestar_b0", "server_name": "OLD-01"}


def test_same_stored_link_no_previous(probe):  # noqa: ANN001
    probe.table = {"polestar_cm_gp": [_row("NODE-01")]}
    store = _run(_confirmed_store("polestar_cm_gp", "NODE-01"))
    link = _attach(_cfg(True), _pod_event(), store=store)["polestar_link"]
    assert link["basis"] == "node_address" and "previous_link" not in link


def test_stored_link_beats_pod_unresolved(probe):  # noqa: ANN001
    store = _run(_confirmed_store("polestar_b0", "OLD-01"))
    link = _attach(_cfg(True), _pod_event(), store=store)["polestar_link"]
    assert link["status"] == "linked" and link["basis"] == "previous_confirmed"
    # 플래그 off면 저장 연결을 읽지 않는다 → 파드 사유
    off = _attach(_cfg(False), _pod_event(), store=store)["polestar_link"]
    assert off["reason"] == "node_not_registered"


def test_node_link_not_stored(probe):  # noqa: ANN001
    probe.table = {"polestar_cm_gp": [_row("NODE-01")]}
    redis = FakeAsyncRedis()
    _attach(_cfg(True), _pod_event(), redis=redis)
    assert _run(HostLinkStore(redis).get("b0", POD)) is None


# ── 세 호출 경로 대칭 ───────────────────────────────────────────────────
def test_manual_call_site_attaches_node_link(probe):  # noqa: ANN001
    probe.table = {"polestar_cm_gp": [_row("NODE-01")]}
    store = HostLinkStore(FakeAsyncRedis())
    out = _manual(_cfg(True), _pod_event(), host_link_store=store)
    link = out["meta"]["hints"]["polestar_link"]
    assert link["basis"] == "node_address" and link["db_id"] == "polestar_cm_gp"
    # 파드 아님 → 종전 그대로
    plain = _manual(_cfg(False), _event())
    assert "polestar_link" not in plain["meta"]["hints"]


def test_manual_user_selection_kept(probe):  # noqa: ANN001
    probe.table = {"polestar_cm_gp": [_row("NODE-01")]}
    selected = hl.user_selected_hint(db_id="d", server_name="s", polestar_hostname="h",
                                     by="u1", at="t")
    out = _manual(_cfg(True), _pod_event(), polestar_link=copy.deepcopy(selected))
    assert out["meta"]["hints"]["polestar_link"] == selected
    assert probe.calls == []


def test_three_call_sites_route_through_attach():
    src = inspect.getsource(trig)
    assert src.count("await _attach_polestar_link(") == 3
