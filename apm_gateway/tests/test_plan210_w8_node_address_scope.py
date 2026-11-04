"""plans/210 W8 — 노드 주소 조회 범위 결정성 (§6.2 B · A의 게이트웨이 몫 · D-355 · D-274).

- B: `apm_instance_map`의 대상 지정 호출(hostname·domain_id·query·business)은 결과 행의 (소스,
  도메인)을 앞에서부터 `MAX_DOMAINS_PER_CALL`개까지 웹 조회한다(옛 상한 3 폐지) · 넘는 도메인은
  캐시만 + `[한계]` 1줄 + partial · 무인자 목록은 캐시 전용 · 플래그 off는 칸 없음(현행).
- A: `apm_node_addresses(instance_ids=…)` 가산 인자 — 그 인스턴스가 있는 도메인만 조회 ·
  `domain_ids`와 AND · 미지 id `[한계]` · 생략하면 현행 응답 비트 동일.

Open API는 합성(`fleet_fake.FakeFleet`), 웹 목록은 대역(`FakeLister`) — 실제 제니퍼·외부 네트워크·
실 LLM 없음.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from apm_gateway.application.node_address import NodeAddressService
from apm_gateway.application.node_address_tools import MAX_DOMAINS_PER_CALL, NodeAddressTools
from apm_gateway.application.sources import build_source_set
from apm_gateway.application.tools import ApmTools
from apm_gateway.domain.errors import ApmError
from apm_gateway.interface.server import create_server
from conftest import NOW_S
from fleet_fake import FakeFleet
from test_plan210_w2_node_addresses import FakeLister, _fleet_tools, _service

from apm_gateway.config import load_config

TOOL = "apm_node_addresses"


def _single_tools(
    tmp_path: Path, domains: int, service: NodeAddressService | None, **fleet: Any
) -> ApmTools:
    """단일 소스(id `default`) × 도메인 `domains`개 · 도메인마다 인스턴스 1개(id 도메인×100)."""
    fake = FakeFleet(domains, hosts=("bank.test",), per_domain=1, **fleet)
    cfg = load_config(fake.env(APM_SPOOL_DIR=str(tmp_path / "spool")))
    sources = build_source_set(cfg, transport=httpx.MockTransport(fake.handler))
    return ApmTools(sources, cfg, clock=lambda: NOW_S, node_address=service)


def _lister(domains: int) -> FakeLister:
    return FakeLister("default", domains=domains)


# ── B: 대상 지정 호출은 도메인 수와 무관하게 웹 조회(상한 50) ────────────────────────


async def test_targeted_query_over_four_domains_fetches_every_domain(tmp_path):
    lister = _lister(6)
    out = await _single_tools(tmp_path, 6, _service(lister)).apm_instance_map(query="bank-i")
    assert {r["domain_id"] for r in out["rows"]} == {1, 2, 3, 4, 5, 6}
    assert lister.calls == [1, 2, 3, 4, 5, 6]  # 옛 상한 3이면 0회였다
    assert {r["instance_id"]: r["host_address"] for r in out["rows"]}[400] == "10.136.4.7"
    assert not out.get("partial")
    assert not any("노드 주소" in x for x in out["limits"])


async def test_targeted_domain_id_fetches_that_domain(tmp_path):
    lister = _lister(6)
    out = await _single_tools(tmp_path, 6, _service(lister)).apm_instance_map(domain_id=5)
    assert lister.calls == [5]
    assert [r["host_address"] for r in out["rows"]] == ["10.136.5.7"]


async def test_targeted_hostname_fetches_and_keeps_envelope_shape(tmp_path):
    lister = _lister(6)
    on = await _single_tools(tmp_path, 6, _service(lister)).apm_instance_map(
        hostname="bank-h5-0"
    )
    off = await _single_tools(tmp_path, 6, None).apm_instance_map(hostname="bank-h5-0")
    assert lister.calls == [5]
    assert [r["host_address"] for r in on["rows"]] == ["10.136.5.7"]
    # 상한 안이면 봉투는 칸 하나(host_address) 말고 플래그 off와 같다(limits·partial 불변)
    assert on["limits"] == off["limits"] and on.get("partial") == off.get("partial")
    assert [{k: v for k, v in r.items() if k != "host_address"} for r in on["rows"]] == off["rows"]


async def test_targeted_business_fetches_every_domain(tmp_path):
    lister = _lister(5)
    tools = _single_tools(tmp_path, 5, _service(lister), domain_names=dict.fromkeys(
        range(1, 6), "주문"))
    out = await tools.apm_instance_map(business="주문")
    assert {r["domain_id"] for r in out["rows"]} == {1, 2, 3, 4, 5}
    assert lister.calls == [1, 2, 3, 4, 5]


async def test_untargeted_list_is_cache_only(tmp_path):
    lister = _lister(6)
    service = _service(lister)
    tools = _single_tools(tmp_path, 6, service)
    cold = await tools.apm_instance_map()
    assert lister.calls == [] and all(r["host_address"] == "" for r in cold["rows"])
    assert not cold.get("partial") and not any("노드 주소" in x for x in cold["limits"])
    await tools.apm_instance_map(source_ids=["default"], domain_id=2)  # 대상 지정 — 캐시를 채운다
    warm = await tools.apm_instance_map(source_ids=["default"])
    assert lister.calls == [2]  # 무인자(source_ids만)는 여전히 웹 호출 0
    by_id = {r["instance_id"]: r["host_address"] for r in warm["rows"]}
    assert by_id[200] == "10.136.2.7" and by_id[300] == ""


async def test_over_max_domains_reads_cache_with_limit_and_partial(tmp_path):
    assert MAX_DOMAINS_PER_CALL == 50
    lister = _lister(51)
    out = await _single_tools(tmp_path, 51, _service(lister)).apm_instance_map(query="bank-i")
    assert len({r["domain_id"] for r in out["rows"]}) == 51
    assert lister.calls == list(range(1, 51))  # 앞에서부터 50개만 · 51번째는 캐시만
    assert {r["instance_id"]: r["host_address"] for r in out["rows"]}[5100] == ""
    assert out["partial"] is True
    assert any("[한계] 노드 주소: 도메인 51개 중 50개만 새로 조회" in x for x in out["limits"])


async def test_hostname_over_cap_carries_limit_and_partial(tmp_path, monkeypatch):
    from apm_gateway.application import instance_tools

    monkeypatch.setattr(instance_tools, "MAX_DOMAINS_PER_CALL", 0)
    lister = _lister(3)
    out = await _single_tools(tmp_path, 3, _service(lister)).apm_instance_map(
        hostname="bank-h1-0"
    )
    assert lister.calls == []
    assert out["partial"] is True
    assert any("[한계] 노드 주소: 도메인 1개 중 0개만" in x for x in out["limits"])


async def test_flag_off_targeted_call_is_unchanged(tmp_path):
    out = await _single_tools(tmp_path, 6, None).apm_instance_map(query="bank-i")
    assert out["rows"] and all("host_address" not in r for r in out["rows"])
    assert not out.get("partial") and out["limits"] == []


# ── A: apm_node_addresses(instance_ids=…) ─────────────────────────────────────────


async def test_instance_ids_query_only_their_domains(tmp_path):
    bank = FakeLister("bank")
    out = await NodeAddressTools(_fleet_tools(tmp_path, _service(bank))).apm_node_addresses(
        source_ids=["bank"], instance_ids=[200]
    )
    assert bank.calls == [2]  # domain_ids 생략이어도 전 도메인 조회가 아니다
    assert [(r["domain_id"], r["instance_id"], r["host_address"]) for r in out["rows"]] == [
        (2, 200, "10.136.2.7")
    ]
    [src] = out["node_address_sources"]
    assert src["instance_status"] == {"ok": 1} and src["instances"] == 1 and src["domains"] == 1
    assert not out.get("partial") and out["limits"] == []


async def test_instance_ids_and_domain_ids_are_and(tmp_path):
    bank = FakeLister("bank")
    out = await NodeAddressTools(_fleet_tools(tmp_path, _service(bank))).apm_node_addresses(
        source_ids=["bank"], domain_ids=[3], instance_ids=[200, 301]
    )
    assert bank.calls == [3]
    assert [r["instance_id"] for r in out["rows"]] == [301]
    assert out["node_address_sources"][0]["instance_status"] == {"not_found": 1}
    assert any("[한계] 인스턴스 200은" in x for x in out["limits"])  # 도메인 3에 없는 id


async def test_unknown_instance_id_is_limit_not_silence(tmp_path):
    bank = FakeLister("bank")
    out = await NodeAddressTools(_fleet_tools(tmp_path, _service(bank))).apm_node_addresses(
        source_ids=["bank"], instance_ids=[100, 9999]
    )
    assert bank.calls == [1]
    assert [r["instance_id"] for r in out["rows"]] == [100]
    assert any("[한계] 인스턴스 9999은 조회한 APM 소스·도메인의 인스턴스 목록에 없다" in x
               for x in out["limits"])


async def test_empty_instance_ids_query_nothing(tmp_path):
    bank = FakeLister("bank")
    out = await NodeAddressTools(_fleet_tools(tmp_path, _service(bank))).apm_node_addresses(
        source_ids=["bank"], instance_ids=[]
    )
    assert bank.calls == [] and out["rows"] == []


@pytest.mark.parametrize("bad", [["x"], [-1], [True], [1.5]])
async def test_instance_ids_are_validated(tmp_path, bad):
    with pytest.raises(ApmError) as e:
        await NodeAddressTools(_fleet_tools(tmp_path, _service(FakeLister("bank")))
                               ).apm_node_addresses(instance_ids=bad)
    assert e.value.code == "invalid_argument" and "instance_ids" in e.value.reason


async def test_omitted_instance_ids_is_byte_identical(tmp_path):
    outs = []
    for flavor in ("omitted", "none"):
        bank, common = FakeLister("bank"), FakeLister("common")
        tools = NodeAddressTools(_fleet_tools(tmp_path / flavor, _service(bank, common)))
        if flavor == "omitted":
            outs.append(await tools.apm_node_addresses(domain_ids=[1, 2]))
        else:
            outs.append(await tools.apm_node_addresses(domain_ids=[1, 2], instance_ids=None))
    assert json.dumps(outs[0], sort_keys=True) == json.dumps(outs[1], sort_keys=True)
    assert len(outs[0]["rows"]) == 2 * 2 * 2  # 소스 2 × 도메인 2 × 인스턴스 2 — 거르지 않는다


async def test_mcp_surface_passes_instance_ids(tmp_path):
    bank = FakeLister("bank")
    mcp = create_server(_fleet_tools(tmp_path, _service(bank)))
    out = await mcp.call_tool(TOOL, {"source_ids": ["bank"], "instance_ids": [300]})
    content = out[0] if isinstance(out, tuple) else out
    env = json.loads(content[0].text)
    assert bank.calls == [3]
    assert [r["instance_id"] for r in env["rows"]] == [300]


async def test_instance_ids_over_cap_is_invalid_argument(tmp_path):
    from apm_gateway.application.node_address_tools import MAX_INSTANCE_IDS

    tools = NodeAddressTools(_fleet_tools(tmp_path, _service(FakeLister("bank"))))
    with pytest.raises(ApmError) as e:
        await tools.apm_node_addresses(instance_ids=list(range(MAX_INSTANCE_IDS + 1)))
    assert e.value.code == "invalid_argument" and "instance_ids" in e.value.reason
    out = await tools.apm_node_addresses(source_ids=["bank"],
                                         instance_ids=list(range(MAX_INSTANCE_IDS)))
    assert out["rows"]  # 상한 그대로는 받는다


async def test_unknown_ids_limit_and_job_label_are_shortened(tmp_path, monkeypatch):
    from apm_gateway.application.node_address_tools import shown_ids

    assert shown_ids([1, 2, 3]) == "1, 2, 3"
    assert shown_ids(list(range(8))) == "0, 1, 2, 3, 4 외 3개"
    bank = FakeLister("bank")
    out = await NodeAddressTools(_fleet_tools(tmp_path, _service(bank))).apm_node_addresses(
        source_ids=["bank"], instance_ids=[100, *range(9000, 9008)]
    )
    [limit] = [x for x in out["limits"] if "[한계] 인스턴스" in x]
    assert limit.startswith("[한계] 인스턴스 9000, 9001, 9002, 9003, 9004 외 3개은")
    from apm_gateway.interface import server as srv

    labels: list[str] = []
    real = srv.run_tool

    async def spy(tools, tool, target, call, **kw):
        labels.append(target)
        return await real(tools, tool, target, call, **kw)

    monkeypatch.setattr(srv, "run_tool", spy)
    mcp = create_server(_fleet_tools(tmp_path / "mcp", _service(FakeLister("bank"))))
    await mcp.call_tool(TOOL, {"source_ids": ["bank"], "instance_ids": list(range(300, 320))})
    assert labels == ["* instances:300,301,302,303,304 외 15개"]
