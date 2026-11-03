"""plans/210 W3 §3.8 — 알람 다시 분석 `_manual_event`의 `apm` 블록 복원(본체 라우트).

플래그 on이면 판정 기록 `apm_ref`(옛 기록은 게이트웨이 역조회)로 `raw_payload["apm"]`를 되살리고,
off면 종전과 같은 이벤트(원문 블록 없음)다. DB·게이트웨이는 띄우지 않는다.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import noise_gate.infrastructure.apm_gateway_client as gw
from src.api.routes import alarm as alarm_routes

POD = "bfm-kbland-admin-price-deploy-6dd49d5df6-x7k2p"
REF = {"source_id": "jen", "domain_id": 7, "instance_name": POD,
       "host_address": "198.51.100.7", "host_address_status": "ok"}


def _config(on: bool | None) -> SimpleNamespace:
    ng = SimpleNamespace() if on is None else SimpleNamespace(apm_host_map_enabled=on)
    return SimpleNamespace(noise_gate=ng)


def _decision(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "db_id": "jennifer_jen", "server_name": POD, "hostname": POD,
        "resource_type": "apm.Instance", "resource_name": POD, "alarm_name": "X",
        "signals": {"severity": 3}, "apm_ref": REF,
    }
    base.update(over)
    return base


async def test_flag_on_restores_apm_block() -> None:
    event = await alarm_routes._manual_event(_config(True), "a1", _decision(), None)
    assert event is not None and event.raw_payload == {"apm": REF}


@pytest.mark.parametrize("on", [False, None])
async def test_flag_off_event_unchanged(on: bool | None) -> None:
    event = await alarm_routes._manual_event(_config(on), "a1", _decision(), None)
    assert event is not None and event.raw_payload == {}


async def test_old_record_uses_gateway_reverse_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    class _Client:
        async def apm_instance_map(self, **kw: Any) -> dict[str, Any]:
            calls.append(kw)
            return {"rows": [{"source_id": "jen", "instance_name": POD, "domain_id": 7,
                              "domain_name": "KB", "ip_address": "", "host_address": ""}]}

    monkeypatch.setattr(gw, "build_apm_gateway_client", lambda ng: _Client())
    event = await alarm_routes._manual_event(
        _config(True), "a1", _decision(apm_ref=None), None
    )
    assert calls == [{"query": POD, "source_ids": ["jen"]}]
    assert event is not None
    assert event.raw_payload == {"apm": {"source_id": "jen", "domain_id": 7,
                                         "domain_name": "KB", "instance_name": POD}}
