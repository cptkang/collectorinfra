"""plans/210 W5 — ITAM 사슬(도메인 순수 함수) 단계별 판정."""

from __future__ import annotations

from noise_gate.domain.apm_host_map import BASIS_ITAM, SCOPE_DOMAIN, build_sms_index
from noise_gate.domain.apm_host_map_itam import (
    REASON_APPS_SPLIT,
    REASON_LINKED,
    REASON_NO_SERVERS,
    REASON_NO_SMS_MATCH,
    REASON_NO_TOKENS,
    REASON_TOO_MANY_SERVERS,
    REASON_TOO_MANY_TARGETS,
    STAGE_APP_CODE,
    STAGE_APP_NAME,
    STAGE_APP_NAME_CONTAINS,
    STAGE_SERVER_CLUE,
    ItamApp,
    ItamServer,
    domain_tokens,
    itam_proposal,
    parse_ipv4s,
    resolve_itam_chain,
    servers_from_rows,
)

SMS = [
    {"db_id": "zone_a", "server_name": "ordweb01", "hostname": "ordweb01", "ip": "10.1.0.1",
     "nic_ips": ["10.9.0.1"], "resource_id": "1"},
    {"db_id": "zone_a", "server_name": "ordwas01", "hostname": "ordwas01", "ip": "10.1.0.2",
     "nic_ips": [], "resource_id": "2"},
    {"db_id": "zone_b", "server_name": "payweb01", "hostname": "payweb01", "ip": "10.2.0.1",
     "nic_ips": [], "resource_id": "3"},
]
BY_IP, BY_HOST = build_sms_index(SMS)
GENERIC = ("prod", "운영", "시스템")


def _chain(domain_name: str, apps: list[ItamApp], servers: list[ItamServer], **kw):
    return resolve_itam_chain(domain_name=domain_name, apps=apps, servers=servers, by_ip=BY_IP,
                              by_host=BY_HOST, generic_terms=GENERIC, **kw)


ORDER_SERVERS = [
    ItamServer("ordweb01", ("10.1.0.1",), "주문관리 | 웹", True),
    ItamServer("ordwas01", (), "주문관리", None),
]


def test_tokens_drop_generic_and_short() -> None:
    assert domain_tokens(["ORDER-prod 운영 a", "주문관리 시스템"], GENERIC) == ["order", "주문관리"]


def test_parse_ipv4s_multi_value_and_invalid() -> None:
    assert parse_ipv4s("10.1.0.1, 10.1.0.2;bad / 10.1.0.1|::1") == ("10.1.0.1", "10.1.0.2")
    assert parse_ipv4s(["10.1.0.3 10.1.0.4"]) == ("10.1.0.3", "10.1.0.4")


def test_stage_app_code_exact() -> None:
    apps = [ItamApp("ORD", "주문관리"), ItamApp("PAY", "결제")]
    result = _chain("ord-prod", apps, ORDER_SERVERS)
    assert result.state == "active"
    assert result.evidence["stage"] == STAGE_APP_CODE
    assert result.evidence["reason"] == REASON_LINKED
    assert {t["server_name"] for t in result.targets} == {"ordweb01", "ordwas01"}


def test_stage_app_name_exact() -> None:
    result = _chain("주문관리", [ItamApp("A01", "주문관리")], ORDER_SERVERS)
    assert result.evidence["stage"] == STAGE_APP_NAME
    assert result.state == "active"


def test_stage_app_name_contains() -> None:
    result = _chain("주문", [ItamApp("A01", "주문관리")], ORDER_SERVERS)
    assert result.evidence["stage"] == STAGE_APP_NAME_CONTAINS
    assert result.state == "active"


def test_stage_server_clue_when_no_app() -> None:
    servers = [ItamServer("payweb01", ("10.2.0.1",), "결제게이트 웹", True)]
    result = _chain("결제게이트", [ItamApp("A01", "주문관리")], servers)
    assert result.evidence["stage"] == STAGE_SERVER_CLUE
    assert result.state == "active"
    assert [t["db_id"] for t in result.targets] == ["zone_b"]


def test_inactive_servers_ignored_and_failed() -> None:
    servers = [ItamServer("ordweb01", ("10.1.0.1",), "주문관리", False)]
    result = _chain("주문관리", [ItamApp("A01", "주문관리")], servers)
    assert result.state == "failed"
    assert result.evidence["reason"] == REASON_NO_SERVERS


def test_no_tokens_failed() -> None:
    result = _chain("prod 운영", [ItamApp("A01", "주문관리")], ORDER_SERVERS)
    assert (result.state, result.evidence["reason"]) == ("failed", REASON_NO_TOKENS)


def test_no_sms_match_failed() -> None:
    servers = [ItamServer("unknown01", ("10.7.7.7",), "주문관리", True)]
    result = _chain("주문관리", [ItamApp("A01", "주문관리")], servers)
    assert (result.state, result.evidence["reason"]) == ("failed", REASON_NO_SMS_MATCH)


def test_apps_split_into_different_server_sets_ambiguous() -> None:
    apps = [ItamApp("A01", "주문관리"), ItamApp("A02", "주문정산")]
    servers = [ItamServer("ordweb01", (), "주문관리", True),
               ItamServer("payweb01", (), "주문정산", True)]
    result = _chain("주문", apps, servers)
    assert (result.state, result.evidence["reason"]) == ("ambiguous", REASON_APPS_SPLIT)
    assert result.targets == ()


def test_too_many_itam_servers_ambiguous() -> None:
    servers = [ItamServer(f"h{i:03d}", (), "주문관리", True) for i in range(51)]
    result = _chain("주문관리", [ItamApp("A01", "주문관리")], servers)
    assert (result.state, result.evidence["reason"]) == ("ambiguous", REASON_TOO_MANY_SERVERS)


def test_too_many_targets_ambiguous() -> None:
    result = _chain("주문관리", [ItamApp("A01", "주문관리")], ORDER_SERVERS, max_targets=1)
    assert (result.state, result.evidence["reason"]) == ("ambiguous", REASON_TOO_MANY_TARGETS)


def test_servers_from_rows_merges_hostname() -> None:
    merged = servers_from_rows([
        {"hostname": "WEB01", "ips": ["10.1.0.1"], "clue_text": "a", "active": None},
        {"hostname": "web01", "ips": "10.1.0.2", "clue_text": "b", "active": True},
    ])
    assert len(merged) == 1
    assert merged[0].ips == ("10.1.0.1", "10.1.0.2")
    assert merged[0].active is True
    assert merged[0].clue == "a | b"


def test_proposal_shape_and_evidence_no_pii_keys() -> None:
    result = _chain("ord", [ItamApp("ORD", "주문관리")], ORDER_SERVERS)
    proposal = itam_proposal(domain_id="7", domain_name="ord", result=result,
                             extra_evidence={"run_id": "r1"})
    assert (proposal.scope, proposal.match_key, proposal.basis) == (SCOPE_DOMAIN, "", BASIS_ITAM)
    assert proposal.evidence["run_id"] == "r1"
    flat = repr(proposal.evidence)
    for forbidden in ("담당", "직원", "성명", "owner", "employee"):
        assert forbidden not in flat
