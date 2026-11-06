"""plans/215 W5 — P1 조립(`biz_service_view`).

연계 그래프 선 · CSV 칸 · 변경 이력 거르기 · 목록 정렬.
"""

from __future__ import annotations

from typing import Any

from noise_gate.application.biz_service_view import (
    csv_cell,
    history_rows,
    link_graph,
    list_rows,
)

ZA, ZB = "gongjon", "bankjon"
ZONES = {"pa": ZA, "pb": ZB}


def _zone(db_id: str) -> str:
    return ZONES.get(db_id, "")


def _inst(mid: int, name: str, map_id: int | None, **extra: Any) -> dict[str, Any]:
    return {"id": mid, "kind": "apm_instance", "member_key": f"j|1|instance|{name}",
            "source_id": "j", "domain_id": "1", "domain_name": "IBK", "instance_name": name,
            "zone": ZA, "state": "active", "confidence": "confirmed", "apm_map_id": map_id,
            **extra}


def _srv(mid: int, db_id: str, rid: str) -> dict[str, Any]:
    return {"id": mid, "kind": "sms_server", "member_key": f"{db_id}|{rid}", "db_id": db_id,
            "resource_id": rid, "server_name": f"s{rid}", "zone": _zone(db_id), "state": "active"}


def _map(mid: int, *targets: dict[str, Any], verified: bool = True,
         state: str = "active") -> dict[str, Any]:
    return {"id": mid, "verified": verified, "state": state, "targets": list(targets)}


def _t(db_id: str, rid: str, gone: bool = False) -> dict[str, Any]:
    return {"kind": "sms", "db_id": db_id, "resource_id": rid, "server_name": f"s{rid}",
            "target_gone": gone}


def test_link_graph_lines_hidden_and_unlinked() -> None:
    members = [
        _inst(1, "was01", 10), _inst(2, "was02", 20), _inst(3, "was03", None),
        _inst(4, "was04", 40, confidence="inferred"), _srv(5, "pa", "101"), _srv(6, "pa", "999"),
    ]
    maps = [_map(10, _t("pa", "101"), _t("pb", "201")), _map(20, _t("pa", "102", gone=True),
                                                           verified=False),
            _map(40, _t("pa", "103"), state="disabled")]
    g = link_graph(members, maps, frozenset({ZA}), _zone)
    assert g["available"] is True and g["hidden_server_count"] == 1
    inst = {i["instance_name"]: i for i in g["domains"][0]["instances"]}
    assert inst["was01"]["line"] == "confirmed"
    assert inst["was01"]["servers"] == [{"db_id": "pa", "resource_id": "101",
                                         "server_name": "s101", "in_service": True,
                                         "line": "confirmed"}]
    assert inst["was02"]["line"] == "inferred"  # 매핑 미확인
    assert inst["was02"]["servers"][0]["line"] == "broken"  # 대상 사라짐
    assert inst["was03"]["line"] == "broken"  # 매핑 없음
    assert inst["was04"]["line"] == "broken"  # 매핑 비활성
    assert [u["member_id"] for u in g["unlinked_servers"]] == [6]
    assert "s201" not in str(g)
    off = link_graph(members, None, None, _zone)
    assert off["available"] is False and off["reason"]


def test_csv_cell_formula_leads() -> None:
    for raw in ("=SUM(1)", "+1", "-1", "@x", "\tx", "\rx"):
        assert csv_cell(raw) == "'" + raw
    assert csv_cell("ibk-01") == "ibk-01" and csv_cell(None) == "" and csv_cell(3) == "3"


def _log(extra: dict[str, Any], *, nested: bool = True) -> dict[str, Any]:
    return {"created_at": "t", "user_id": "adm",
            "detail": {"extra": extra} if nested else extra}


def test_history_rows_match_service_ids_not_candidate_ids() -> None:
    logs = [
        _log({"action": "biz_service_candidate_accept", "outcome": "ok",
              "before": {"id": 7, "kind": "member"}, "after": None}),  # 후보 id 7 ≠ 서비스 7
        _log({"action": "biz_service_update", "outcome": "ok",
              "before": {"id": 7, "service_key": "a"}, "after": {"id": 7, "service_key": "a"}}),
        _log({"action": "biz_service_merge", "outcome": "ok", "into_service_id": 7,
              "before": {"id": 3, "service_key": "b"}}, nested=False),
        _log({"action": "biz_service_bulk_assign", "outcome": "ok", "service_id": 7,
              "batch_id": "B"}),
        _log({"action": "apm_host_map_update", "service_id": 7}),
        {"detail": "깨진 행"},
    ]
    rows = history_rows(logs, 7)
    assert [r["action"] for r in rows] == ["biz_service_update", "biz_service_merge",
                                           "biz_service_bulk_assign"]
    assert rows[2]["batch_id"] == "B"
    assert len(history_rows(logs, 7, limit=1)) == 1


def test_list_rows_sort_and_none_last_tier() -> None:
    services = [{"id": 1, "service_key": "a", "display_name": "나", "tier": None},
                {"id": 2, "service_key": "b", "display_name": "가", "tier": 2},
                {"id": 3, "service_key": "c", "display_name": "다", "tier": 1}]
    stats = [{"service_id": i, "kind": "sms_server", "zone": ZA, "db_id": "pa",
              "confidence": "confirmed", "n": i} for i in (1, 2, 3)]
    rows, hidden = list_rows(services, stats, scope=None, metrics={})
    assert [r["id"] for r in rows] == [3, 2, 1] and hidden == 0
    rows, _ = list_rows(services, stats, scope=None, metrics={}, sort="name")
    assert [r["display_name"] for r in rows] == ["가", "나", "다"]
    rows, _ = list_rows(services, stats, scope=None, metrics={}, sort="sms", descending=True)
    assert [r["sms_count"] for r in rows] == [3, 2, 1]
