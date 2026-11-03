"""plans/210 W3 §3.8 — 판정 기록 `apm_ref` · 수동 재분석 `apm` 블록 복원 · 수동 경로 고속 경로.

판정 기록은 임시 경로의 실제 `DecisionStore`, 게이트웨이는 `apm_instance_map` 대역이다.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noise_gate.application import apm_host_map_lookup as lk
from noise_gate.application import host_candidates as hc
from noise_gate.application.nodes import investigation_trigger as trig
from noise_gate.application.nodes import notification_gate as gate
from noise_gate.domain import apm_host_map as hm
from noise_gate.infrastructure import apm_gateway_client as gw
from noise_gate.infrastructure.decision_store import DecisionStore

from .test_plan210_w3_fast_path import (
    NODE_IP,
    POD,
    SOURCES,
    _apm,
    _cache_with,
    _cfg,
    _event,
    _FakeProbe,
    _Redis,
    _row,
)


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> _FakeProbe:
    fake = _FakeProbe()
    monkeypatch.setattr(hc, "_default_probes", lambda app_config: (fake, None))
    monkeypatch.setattr(hc, "node_lookup_db_ids", lambda app_config: list(SOURCES))
    return fake


def _decision() -> SimpleNamespace:
    return SimpleNamespace(tier="page", reason="r", priority=1, fingerprint="fp",
                           signals={"severity": 3}, stage="", evidence={})


def _record(tmp_path: Path, gate_cfg: Any, event: Any) -> dict[str, Any]:
    store = DecisionStore(tmp_path / "d.jsonl", True, 1000)
    gate._record_decision(store, _decision(), event, SimpleNamespace(error=None), {}, gate_cfg, {})
    lines = (tmp_path / "d.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    return json.loads(lines[0])


def _gate_cfg(**over: Any) -> SimpleNamespace:
    base: dict[str, Any] = dict(decision_analysis_text_enabled=False,
                                investigation_manual_enabled=True)
    base.update(over)
    return SimpleNamespace(**base)


# ── 판정 기록 apm_ref ───────────────────────────────────────────────────
def test_record_carries_apm_ref_only_when_flag_on(tmp_path: Path) -> None:
    event = _event(ip_address="10.1.2.3")
    on = _record(tmp_path / "on", _gate_cfg(apm_host_map_enabled=True), event)
    assert on["apm_ref"] == {
        "source_id": "jen", "domain_id": 7, "domain_name": "KB", "instance_name": POD,
        "ip": "10.1.2.3", "host_address": NODE_IP, "host_address_status": "ok",
    }
    off = _record(tmp_path / "off", _gate_cfg(), event)
    legacy = _record(tmp_path / "legacy", _gate_cfg(apm_host_map_enabled=False), event)
    for rec in (off, legacy):
        rec.pop("ts")
    assert "apm_ref" not in off and off == legacy
    on.pop("ts")
    on.pop("apm_ref")
    assert on == off  # 그 밖의 칸은 같다


def test_record_without_apm_block_has_no_apm_ref(tmp_path: Path) -> None:
    event = _event(raw_payload={})
    rec = _record(tmp_path, _gate_cfg(apm_host_map_enabled=True), event)
    assert "apm_ref" not in rec


def test_apm_ref_is_operator_only_in_views(tmp_path: Path) -> None:
    _record(tmp_path, _gate_cfg(apm_host_map_enabled=True), _event())
    store = DecisionStore(tmp_path / "d.jsonl", True, 1000)
    assert "apm_ref" in (store.get_decision("a1", operator_view=True) or {})
    assert "apm_ref" not in (store.get_decision("a1") or {})


# ── 복원 ────────────────────────────────────────────────────────────────
async def test_restore_from_apm_ref() -> None:
    ref = {"source_id": "jen", "domain_id": 7, "instance_name": POD, "host_address": NODE_IP,
           "host_address_status": "ok", "extra": "x"}
    apm = await lk.restore_apm_block(_cfg(), {"db_id": "jennifer_jen", "apm_ref": ref})
    assert apm == {k: v for k, v in ref.items() if k != "extra"}


class _Gateway:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def apm_instance_map(self, **kw: Any) -> Any:
        self.calls.append(kw)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _inst(name: str, **over: Any) -> dict[str, Any]:
    return {"source_id": "jen", "instance_name": name, "domain_id": 7, "domain_name": "KB",
            "ip_address": "10.9.9.9", "host_address": NODE_IP, **over}


async def test_old_record_reverse_lookup() -> None:
    client = _Gateway({"rows": [_inst(POD), _inst(POD + "x")]})
    decision = {"db_id": "jennifer_jen", "resource_name": POD}
    apm = await lk.restore_apm_block(_cfg(), decision, client=client)
    assert client.calls == [{"query": POD, "source_ids": ["jen"]}]
    assert apm == {"source_id": "jen", "domain_id": 7, "domain_name": "KB",
                   "instance_name": POD, "ip": "10.9.9.9", "host_address": NODE_IP,
                   "host_address_status": "ok"}


@pytest.mark.parametrize("result", [
    gw.ApmGatewayClientError("down"),
    {"rows": []},
    {"rows": [_inst(POD), _inst(POD, domain_id=8)]},
])
async def test_old_record_reverse_lookup_failure_is_logged(
    result: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=lk.__name__)
    decision = {"db_id": "jennifer_jen", "resource_name": POD}
    assert await lk.restore_apm_block(_cfg(), decision, client=_Gateway(result)) is None
    assert any("apm_ref 역조회" in r.getMessage() for r in caplog.records)


async def test_non_apm_record_is_not_reverse_looked_up() -> None:
    client = _Gateway({"rows": [_inst(POD)]})
    decision = {"db_id": "polestar_cm_gp", "resource_name": POD}
    assert await lk.restore_apm_block(_cfg(), decision, client=client) is None
    assert client.calls == []


async def test_gateway_client_instance_map_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    client = gw.ApmGatewayClient("http://127.0.0.1:1/sse")
    seen: list[tuple[str, dict[str, Any]]] = []

    async def _call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        seen.append((tool, arguments))
        return {"rows": []}

    monkeypatch.setattr(client, "_call", _call)
    assert await client.apm_instance_map(query=POD, source_ids=["jen"]) == {"rows": []}
    assert seen == [("apm_instance_map", {"query": POD, "source_ids": ["jen"]})]


# ── 수동 경로 — 같은 조회 순서 ──────────────────────────────────────────
async def test_manual_payload_uses_map_cache_hit(probe: _FakeProbe) -> None:
    redis = _Redis()
    cache = await _cache_with(redis, _row("instance", POD, rid=21))
    decision = SimpleNamespace(tier="page", reason="r", fingerprint="fp", signals={})
    payload = await trig.build_manual_payload(
        _cfg(), _event(), decision, fingerprint="manual:a1:x", investigation_kind="manual",
        map_cache=cache,
    )
    link = payload["meta"]["hints"]["polestar_link"]
    assert link["basis"] == hm.HINT_BASIS and link["map_id"] == 21 and probe.calls == []
    assert [m["type"] for m in redis.requests()] == ["touch"]


async def test_manual_payload_live_learns_via_stream(probe: _FakeProbe) -> None:
    """수동 경로 되적재 — 같은 스트림 발행(API 소비자가 단일 기록자로 반영한다)."""
    probe.table["polestar_cm_gp"] = [{"name": "NODE-01", "hostname": "node-01",
                                      "ipaddress": NODE_IP}]
    redis = _Redis()
    cache = await _cache_with(redis)
    decision = SimpleNamespace(tier="page", reason="r", fingerprint="fp", signals={})
    payload = await trig.build_manual_payload(
        _cfg(), _event(_apm()), decision, fingerprint="manual:a1:x",
        investigation_kind="manual", map_cache=cache,
    )
    assert payload["meta"]["hints"]["polestar_link"]["basis"] == "node_address"
    assert [m["type"] for m in redis.requests()] == ["learn"]
