"""plans/215 W5b — CSV 가져오기 판정(파싱 · 일치 판정 · 계획 · 적용 전 검사 · 보관).

§14 가져오기 벡터 3행 · 수용 21(1대 = 추가 · 0대 = 스냅샷에 없음 · 2대 이상 = 고르지 않음 ·
다른 서비스 = 옮김) · 크기·행 상한. 저장소·210 스냅샷은 메모리 가짜(SQL 0)다.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any

import pytest

from noise_gate.application.biz_service_admin import BizServiceAdminError
from noise_gate.application.biz_service_import import (
    MAX_BYTES,
    MAX_ROWS,
    ImportPreviewStore,
    apply_import,
    build_preview,
    parse_csv,
)
from noise_gate.tests.test_plan215_w3_service import FakeMaps

HEADER = "service_key,display_name,tier,owner_org,db_id,server_name,hostname,layer"
DBS = ("polestar_cm_gp", "pg_b")


def _snap(*rows: tuple[str, str, str, str]) -> list[dict[str, Any]]:
    return [{"db_id": d, "resource_id": rid, "server_name": name, "hostname": host,
             "ip": f"10.0.0.{rid}", "nic_ips": []} for d, rid, name, host in rows]


SNAP = _snap(
    ("polestar_cm_gp", "1", "ibkweb01", "ibkweb01"),
    ("polestar_cm_gp", "2", "ibkwas01(인터넷뱅킹)", "ibk-was01"),
    ("polestar_cm_gp", "3", "dupsrv", "dupsrv"),
    ("polestar_cm_gp", "4", "dupsrv", "dup-srv-b"),
    ("polestar_cm_gp", "5", "cardweb01", "cardweb01"),
)


class FakeStore:
    """가져오기가 부르는 저장소 메서드(읽기 2종 + 적용 기록)."""

    def __init__(self) -> None:
        self.services: dict[int, dict[str, Any]] = {}
        self.owners: dict[str, dict[str, Any]] = {}
        self.applied: list[dict[str, Any]] = []

    def seed(self, sid: int, key: str, **extra: Any) -> dict[str, Any]:
        self.services[sid] = {"id": sid, "service_key": key, "display_name": key, "tier": None,
                              "owner_org": None, "state": "active", "version": 3, **extra}
        return self.services[sid]

    def own(self, member_key: str, sid: int, state: str = "active") -> None:
        self.owners[member_key] = {"member_id": 90, "service_id": sid,
                                   "service_name": self.services[sid]["display_name"],
                                   "state": state}

    async def member_owners(self, kind: str, member_keys: Collection[str] | None = None
                            ) -> dict[str, dict[str, Any]]:
        return {k: v for k, v in self.owners.items() if member_keys is None or k in member_keys}

    async def services_brief(self, *, keys: Collection[str] = (),
                             ids: Collection[int] = ()) -> list[dict[str, Any]]:
        return [dict(s) for s in self.services.values()
                if s["service_key"] in set(keys) or s["id"] in set(ids)]

    async def import_members(self, *, creates: Sequence[Mapping[str, Any]],
                             versions: Mapping[int, int], items: Sequence[Mapping[str, Any]],
                             by: str = "") -> dict[str, Any]:
        self.applied.append({"creates": list(creates), "versions": dict(versions),
                             "items": list(items), "by": by})
        return {"created": [], "members": []}


def _csv(*lines: str, bom: bool = False) -> bytes:
    return (("﻿" if bom else "") + "\r\n".join([HEADER, *lines]) + "\r\n").encode("utf-8")


def _no_pii(value: str) -> list[str]:
    return ["전화번호"] if "010-" in value else []


async def _preview(data: bytes, store: FakeStore | None = None,
                   snap: list[dict[str, Any]] | None = None) -> Any:
    return await build_preview(parse_csv(data), repo=store or FakeStore(),
                               maps=FakeMaps(SNAP if snap is None else snap),
                               known_db_ids=DBS, pii_rules=_no_pii,
                               zone_of=lambda d: "gongjon")


def _status(plan: Any) -> list[str]:
    return [r["status"] for r in plan.view["rows"]]


# ── 파싱 ────────────────────────────────────────────────────────────────
def test_parse_bom_crlf_blank_lines_and_formula_unescape() -> None:
    rows = parse_csv(_csv("a,'=SUM(1),1,,polestar_cm_gp,X,,web", "", " , , , , , , , ",
                          "b,B,,,pg_b,y,,", bom=True))
    assert [r.line for r in rows] == [2, 5]
    assert rows[0].values["display_name"] == "=SUM(1)"
    assert rows[0].values["service_key"] == "a" and rows[1].error is None


def test_parse_size_cap_exact_and_plus_one() -> None:
    body = _csv("a,A,,,pg_b,y,,")
    ok = body + b"\n" * (MAX_BYTES - len(body))
    assert len(ok) == MAX_BYTES and len(parse_csv(ok)) == 1
    with pytest.raises(BizServiceAdminError) as exc:
        parse_csv(ok + b"\n")
    assert exc.value.status == 413 and exc.value.code == "import_too_large"


def test_parse_row_cap_5000_ok_5001_rejected() -> None:
    line = "a,A,,,pg_b,y,,"
    assert len(parse_csv(_csv(*[line] * MAX_ROWS))) == MAX_ROWS
    with pytest.raises(BizServiceAdminError) as exc:
        parse_csv(_csv(*[line] * (MAX_ROWS + 1)))
    assert exc.value.status == 400 and exc.value.code == "import_too_many_rows"


@pytest.mark.parametrize(("data", "code"), [
    (b"", "import_empty"),
    (HEADER.encode() + b"\r\n", "import_empty"),
    (b"service_key,display_name\r\na,b\r\n", "import_bad_header"),
    ("service_key,표시명\r\n".encode("cp949"), "import_bad_encoding"),
    ((HEADER + '\r\n"a,b\r\n').encode(), "import_bad_csv"),
])
def test_parse_rejects_whole_file(data: bytes, code: str) -> None:
    with pytest.raises(BizServiceAdminError) as exc:
        parse_csv(data)
    assert exc.value.code == code and exc.value.status == 400


def test_parse_row_level_errors_column_count_and_length() -> None:
    rows = parse_csv(_csv("a,A,,", "a,A,," + "x" * 61 + ",pg_b,y,,"))
    assert rows[0].error and "칸 수" in rows[0].error
    assert rows[1].error and "owner_org" in rows[1].error


# ── 판정 — §14 벡터 · 수용 21 ──────────────────────────────────────────
async def test_vector_canonical_single_match_adds_with_layer() -> None:
    """`polestar_cm_gp,IBKWEB01,,web` · 스냅샷에 `ibkweb01` 1대 → 추가 · 계층 web."""
    plan = await _preview(_csv("ibk,인터넷뱅킹,1,,polestar_cm_gp,IBKWEB01,,web"))
    row = plan.view["rows"][0]
    assert row["status"] == "add" and row["label"] == "추가"
    assert row["match"]["resource_id"] == "1" and row["member_key"] == "polestar_cm_gp|1"
    item = plan.items[0]
    assert item["layer"] == "web" and item["owner_id"] is None and item["service_id"] is None
    assert item["display"] == {"db_id": "polestar_cm_gp", "resource_id": "1",
                               "server_name": "ibkweb01", "hostname": "ibkweb01",
                               "ip": "10.0.0.1", "zone": "gongjon"}
    assert plan.creates == [{"service_key": "ibk", "display_name": "인터넷뱅킹", "tier": 1,
                             "owner_org": None}]
    assert plan.snapshot_run_id == "scan1"


async def test_hostname_and_business_suffix_match_by_canonical_form() -> None:
    plan = await _preview(_csv("ibk,인터넷뱅킹,,,polestar_cm_gp,,IBK_WAS01,was"))
    assert _status(plan) == ["add"] and plan.view["rows"][0]["match"]["resource_id"] == "2"


async def test_vector_unknown_db_rejected() -> None:
    plan = await _preview(_csv("ibk,인터넷뱅킹,,,nope_db,ibkweb01,,"))
    row = plan.view["rows"][0]
    assert row["status"] == "unknown_db" and row["label"] == "등록되지 않은 DB"
    assert plan.items == [] and plan.creates == []


async def test_vector_same_server_two_services_both_rows_conflict() -> None:
    plan = await _preview(_csv("ibk,인터넷뱅킹,,,polestar_cm_gp,ibkweb01,,",
                               "card,카드,,,polestar_cm_gp,IBKWEB01,,",
                               "card,카드,,,polestar_cm_gp,cardweb01,,"))
    assert _status(plan) == ["file_conflict", "file_conflict", "add"]
    assert plan.view["rows"][0]["label"] == "파일 안 충돌"
    assert [i["member_key"] for i in plan.items] == ["polestar_cm_gp|5"]


async def test_zero_match_and_two_or_more_not_chosen() -> None:
    plan = await _preview(_csv("x,X,,,polestar_cm_gp,ghost01,,", "x,X,,,polestar_cm_gp,dupsrv,,"))
    rows = plan.view["rows"]
    assert [r["label"] for r in rows] == ["스냅샷에 없음", "고르지 않음"]
    assert rows[1]["match_count"] == 2 and rows[1]["match"] is None
    assert plan.items == [] and plan.creates == []  # 적용할 행이 없는 새 서비스는 만들지 않는다


async def test_other_service_owner_is_move_and_versions_cover_both_sides() -> None:
    store = FakeStore()
    store.seed(7, "ibk", display_name="인터넷뱅킹", version=4)
    store.seed(9, "old", display_name="옛 서비스", version=2)
    store.own("polestar_cm_gp|1", 9)
    store.own("polestar_cm_gp|5", 7, state="excluded")
    plan = await _preview(_csv("ibk,,,,polestar_cm_gp,ibkweb01,,",
                               "ibk,,,,polestar_cm_gp,cardweb01,,db"), store)
    rows = plan.view["rows"]
    assert [r["status"] for r in rows] == ["move", "keep"]
    assert rows[0]["label"] == "옮김" and rows[0]["owner"]["service_id"] == 9
    assert "옛 서비스" in rows[0]["reason"] and rows[1]["reason"]
    assert plan.versions == {7: 4, 9: 2} and plan.creates == []
    assert [(i["service_id"], i["owner_id"]) for i in plan.items] == [(7, 9), (7, 7)]
    assert plan.view["moves"] == [{"from_service_id": 9, "from_service_name": "옛 서비스",
                                   "to_service_key": "ibk", "count": 1}]
    svc = plan.view["services"][0]
    assert (svc["is_new"], svc["add"], svc["move_in"], svc["keep"]) == (False, 0, 1, 1)
    assert svc["attr_note"] is None


async def test_existing_service_attributes_not_overwritten_note() -> None:
    store = FakeStore()
    store.seed(7, "ibk", display_name="인터넷뱅킹")
    plan = await _preview(_csv("ibk,다른 이름,2,디지털부,polestar_cm_gp,ibkweb01,,"), store)
    assert plan.creates == [] and plan.view["services"][0]["attr_note"]
    assert plan.view["services"][0]["display_name"] == "인터넷뱅킹"


async def test_duplicate_row_same_service_and_service_only_row() -> None:
    plan = await _preview(_csv("ibk,인터넷뱅킹,,,polestar_cm_gp,ibkweb01,,",
                               "ibk,인터넷뱅킹,,,polestar_cm_gp,ibkweb01,,web",
                               "apmonly,APM만,,,,,,"))
    assert _status(plan) == ["add", "duplicate", "service_only"]
    assert len(plan.items) == 1 and plan.items[0]["layer"] is None
    assert {c["service_key"] for c in plan.creates} == {"ibk", "apmonly"}


async def test_new_service_without_display_name_rejected() -> None:
    plan = await _preview(_csv("ibk,,,,polestar_cm_gp,ibkweb01,,"))
    assert _status(plan) == ["invalid"] and "표시명" in plan.view["rows"][0]["reason"]
    assert plan.items == [] and plan.creates == []


async def test_pii_owner_org_rejected_value_not_echoed() -> None:
    plan = await _preview(_csv("ibk,인터넷뱅킹,,홍길동 010-1234-5678,polestar_cm_gp,ibkweb01,,"))
    row = plan.view["rows"][0]
    assert row["status"] == "pii_rejected" and "전화번호" in row["reason"]
    assert "010-1234" not in repr(plan.view) and plan.items == []


@pytest.mark.parametrize(("line", "word"), [
    ("ibk,I,4,,polestar_cm_gp,ibkweb01,,", "tier"),
    ("ibk,I,,,polestar_cm_gp,ibkweb01,,front", "layer"),
    (",I,,,polestar_cm_gp,ibkweb01,,", "service_key"),
    ("ibk,I,,,,ibkweb01,,", "db_id"),
    ("ibk,I,,,polestar_cm_gp,서버,,", "형식"),
])
async def test_invalid_rows(line: str, word: str) -> None:
    plan = await _preview(_csv(line))
    assert _status(plan) == ["invalid"] and word in plan.view["rows"][0]["reason"]


async def test_layer_case_insensitive_and_counts_labels() -> None:
    plan = await _preview(_csv("ibk,인터넷뱅킹,,,polestar_cm_gp,ibkweb01,,WEB"))
    assert plan.items[0]["layer"] == "web"
    assert plan.view["counts"]["add"] == 1 and plan.view["labels"]["ambiguous"] == "고르지 않음"
    assert plan.view["applicable"] == 1 and plan.view["creates"] == 1


async def test_no_snapshot_rejects_preview() -> None:
    maps = FakeMaps(SNAP)
    maps.current.clear()
    with pytest.raises(BizServiceAdminError) as exc:
        await build_preview(parse_csv(_csv("a,A,,,pg_b,y,,")), repo=FakeStore(), maps=maps,
                            known_db_ids=DBS, pii_rules=_no_pii)
    assert exc.value.code == "snapshot_missing" and exc.value.status == 409


# ── 적용 전 검사 ────────────────────────────────────────────────────────
async def test_apply_snapshot_changed_409_and_nothing_400() -> None:
    store = FakeStore()
    plan = await _preview(_csv("ibk,인터넷뱅킹,,,polestar_cm_gp,ibkweb01,,web"), store)
    maps = FakeMaps(SNAP, run_id="scan2")
    with pytest.raises(BizServiceAdminError) as exc:
        await apply_import(plan, repo=store, maps=maps, by="ops")
    assert exc.value.code == "snapshot_changed" and exc.value.status == 409
    assert store.applied == []
    await apply_import(plan, repo=store, maps=FakeMaps(SNAP), by="ops")
    assert store.applied[0]["items"] == plan.items and store.applied[0]["by"] == "ops"
    empty = await _preview(_csv("x,X,,,polestar_cm_gp,ghost01,,"), store)
    with pytest.raises(BizServiceAdminError) as exc:
        await apply_import(empty, repo=store, maps=FakeMaps(SNAP), by="ops")
    assert exc.value.code == "import_nothing"


# ── 미리보기 보관 ───────────────────────────────────────────────────────
async def test_store_ttl_owner_and_cap() -> None:
    now = [0.0]
    store = ImportPreviewStore(ttl_seconds=10, max_entries=2, clock=lambda: now[0])
    plan = await _preview(_csv("ibk,인터넷뱅킹,,,polestar_cm_gp,ibkweb01,,"))
    pid = store.put("ops", plan)
    assert store.get(pid, "ops") is plan
    with pytest.raises(BizServiceAdminError) as exc:
        store.get(pid, "other")
    assert exc.value.code == "preview_not_found" and exc.value.status == 404
    now[0] = 11.0
    with pytest.raises(BizServiceAdminError):
        store.get(pid, "ops")
    a, b, c = store.put("ops", plan), store.put("ops", plan), store.put("ops", plan)
    with pytest.raises(BizServiceAdminError):
        store.get(a, "ops")
    assert store.get(b, "ops") and store.get(c, "ops")
    store.drop(c)
    with pytest.raises(BizServiceAdminError):
        store.get(c, "ops")
