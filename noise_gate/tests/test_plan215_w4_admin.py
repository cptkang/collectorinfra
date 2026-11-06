"""plans/215 W4 — 관리자 동작(구성원 키 · 후보 수락 · APM 대조 · 게이트웨이 업무 매핑 사본) +
관리자 편집 → 재계산 종단.

종단은 W3 계산 서비스(실물)와 메모리 저장소 가짜를 쓴다. 관리자 동작 가짜는 실 저장소 SQL과 같은
판정(옮긴 구성원 `origin=manual` · 병합 원 서비스 `disabled` 등)만 옮겼다.
수용 4·18·19(일부)·30을 본다.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

import pytest
import yaml

from noise_gate.application import biz_service_admin as adm
from noise_gate.application.biz_service_admin import (
    BizServiceAdminError,
    SnapshotDisplay,
    accept_candidate,
    apm_compare,
    build_member,
    gateway_business_map,
)
from noise_gate.infrastructure.biz_service_repository import candidate_key
from noise_gate.tests.test_plan215_w3_service import NAMES, FakeMaps, FakeStore, _service, _sms


class AdminFakeStore(FakeStore):
    """관리자 저장소 동작 가짜(실 저장소 SQL과 같은 판정)."""

    def _bump(self, sid: int) -> int:
        s = self.services[sid]
        s["version"] = int(s.get("version") or 1) + 1
        return int(s["version"])

    async def get_candidate(self, candidate_id: int) -> dict[str, Any] | None:
        c = self.candidates.get(candidate_id)
        return dict(c) if c else None

    async def add_member(self, service_id: int, *, kind: str, member_key: str,
                         display: Mapping[str, Any] | None = None, by: str = "",
                         **_: Any) -> dict[str, Any]:
        self._bump(service_id)
        mid = next(self._ids)
        self.members[mid] = {
            "id": mid, "service_id": service_id, "kind": kind, "member_key": member_key,
            "origin": "manual", "basis": "manual", "confidence": "confirmed", "state": "active",
            "apm_map_id": None, "layer": None, "layer_origin": None, "evidence": {},
            "gone_at": None, **dict(display or {})}
        return dict(self.members[mid])

    async def move_members(self, member_ids: Collection[int], *, from_service_id: int,
                           to_service_id: int, by: str = "", **_: Any) -> int:
        n = 0
        for mid in member_ids:
            mm = self.members[mid]
            if mm["service_id"] == from_service_id:
                mm.update(service_id=to_service_id, origin="manual",
                          evidence={**mm["evidence"], "moved_from": from_service_id})
                n += 1
        return n

    async def set_members_state(self, service_id: int, member_ids: Collection[int], *,
                                state: str, **_: Any) -> int:
        for mid in member_ids:
            self.members[mid]["state"] = state
        return len(member_ids)

    async def set_members_confirmed(self, service_id: int, member_ids: Collection[int], *,
                                    confirmed: bool, **_: Any) -> int:
        for mid in member_ids:
            self.members[mid].update(confidence="confirmed" if confirmed else "inferred",
                                     origin="manual")
        return len(member_ids)

    async def accept_member_candidate(self, candidate_id: int, *,
                                      display: Mapping[str, Any] | None = None,
                                      by: str = "") -> dict[str, Any]:
        c = self.candidates[candidate_id]
        sid, kind, key = c["service_id"], c["member_kind"], c["member_key"]
        aux = {"aux": list(c.get("signals") or []), "candidate_id": candidate_id}
        hit = next((m for m in self.members.values()
                    if (m["kind"], m["member_key"]) == (kind, key)), None)
        moved = None
        if hit is None:
            hit = await self.add_member(sid, kind=kind, member_key=key, display=display)
            hit = self.members[hit["id"]]
        else:
            moved = hit["service_id"] if hit["service_id"] != sid else None
            hit.update(service_id=sid, origin="manual", basis="manual",
                       confidence="confirmed", state="active")
        hit["evidence"] = {**hit["evidence"], **aux}
        c["status"] = "accepted"
        return {"member": dict(hit), "moved_from": moved,
                "service_version": self.services[sid]["version"], "candidate": dict(c)}

    async def merge_services(self, from_service_id: int, to_service_id: int, *,
                             by: str = "", candidate_id: int | None = None,
                             **_: Any) -> dict[str, Any]:
        moved = 0
        for mm in self.members.values():
            if mm["service_id"] == from_service_id:
                mm.update(service_id=to_service_id, origin="manual",
                          evidence={**mm["evidence"], "merged_from": from_service_id})
                moved += 1
        src = self.services[from_service_id]
        src.update(state="disabled", evidence={**src["evidence"], "merged_into": to_service_id})
        if candidate_id is not None:
            self.candidates[candidate_id]["status"] = "accepted"
        return {"moved": moved, "from_version": self._bump(from_service_id),
                "to_version": self._bump(to_service_id), "candidate": None}

    async def split_service(self, service_id: int, member_ids: Collection[int], *,
                            service_key: str, display_name: str, **_: Any) -> dict[str, Any]:
        sid = next(self._ids)
        self.services[sid] = {"id": sid, "service_key": service_key,
                              "display_name": display_name, "origin": "manual",
                              "state": "active", "verified": False, "monitored": False,
                              "evidence": {"split_from": service_id}, "version": 1}
        moved = await self.move_members(member_ids, from_service_id=service_id,
                                        to_service_id=sid)
        return {"service": dict(self.services[sid]), "moved": moved,
                "from_version": self._bump(service_id)}

    async def insert_member_candidates(self, service_id: int, service_key: str, items: Any,
                                       *, run_id: str | None = None) -> dict[str, int]:
        keys = {c["candidate_key"] for c in self.candidates.values()}
        out = {"inserted": 0, "existing": 0}
        for it in items:
            ck = candidate_key("member", service_key, it["member_kind"], it["member_key"])
            if ck in keys:
                out["existing"] += 1
                continue
            cid = next(self._ids)
            self.candidates[cid] = {
                "id": cid, "kind": "member", "candidate_key": ck, "status": "open",
                "service_id": service_id, "member_kind": it["member_kind"],
                "member_key": it["member_key"], "signals": list(it["signals"]),
                "evidence": {"service_key": service_key, "target_kind": it["member_kind"],
                             "target_key": it["member_key"], "display": it["display"]}}
            keys.add(ck)
            out["inserted"] += 1
        return out


async def _applied() -> tuple[Any, AdminFakeStore, dict[str, Any]]:
    store = AdminFakeStore()
    service, parts = _service(store=store)
    await service.apply((await service.preview("adm"))["run_id"], "adm")
    return service, store, parts


async def _recompute(service: Any) -> dict[str, Any]:
    preview = await service.preview("adm")
    await service.apply(preview["run_id"], "adm")
    return preview


def _snap(store: AdminFakeStore) -> dict[str, tuple[Any, ...]]:
    return {mm["member_key"]: (mm["service_id"], mm["origin"], mm["confidence"], mm["state"])
            for mm in store.members.values()}


# ── 종단: 관리자 편집 → 재계산 그대로 ─────────────────────────────────
async def test_admin_edits_survive_recompute() -> None:
    """수용 4·19(일부) — 추가·옮기기·제외·확인 취소 뒤 재계산·적용은 바꾸지 않는다."""
    service, store, _ = await _applied()
    ibk, card = store.service("인터넷뱅킹")["id"], store.service("카드")["id"]
    key, shown = build_member("sms_server", db_id="pg_a", resource_id="301")
    await store.add_member(card, kind="sms_server", member_key=key, display=shown)
    await store.move_members([store.member("pg_a|103")["id"]], from_service_id=ibk,
                             to_service_id=card)
    await store.set_members_state(ibk, [store.member("pg_a|102")["id"]], state="excluded")
    await store.set_members_confirmed(ibk, [store.member("pg_a|101")["id"]], confirmed=False)
    before = _snap(store)

    await _recompute(service)
    assert _snap(store) == before
    assert before["pg_a|301"] == (card, "manual", "confirmed", "active")
    assert before["pg_a|103"][:2] == (card, "manual")
    assert before["pg_a|102"][3] == "excluded"
    assert before["pg_a|101"][1:3] == ("manual", "inferred")
    second = await service.preview("adm")
    assert second["members"]["by_action"] == {}


async def test_aux_candidate_accept_keeps_evidence_and_survives_recompute() -> None:
    """수용 30 — 보조 신호 후보 수락 → `origin=manual`·`confirmed` · `evidence.aux`.

    재계산이 바꾸지 않는다.
    """
    service, store, parts = await _applied()
    ibk = store.service("인터넷뱅킹")["id"]
    signals = [{"basis": "aux:ip_subnet", "bundle_key": "10.0.3", "anchors": 2, "purity": 1.0}]
    await store.insert_member_candidates(ibk, "인터넷뱅킹", [
        {"member_kind": "sms_server", "member_key": "pg_a|301", "signals": signals,
         "display": {}}])
    cid = max(store.candidates)
    got = await accept_candidate(store, cid, display=SnapshotDisplay(parts["maps"]), by="adm")
    assert got["kind"] == "member" and got["moved_from"] is None
    mm = store.member("pg_a|301")
    assert (mm["origin"], mm["confidence"], mm["service_id"]) == ("manual", "confirmed", ibk)
    assert mm["evidence"]["aux"] == signals and mm["hostname"] == "etcsrv01"  # 스냅샷 표시 칸
    before = _snap(store)
    await _recompute(service)
    assert _snap(store) == before and store.candidates[cid]["status"] == "accepted"


async def test_merge_and_split_survive_recompute() -> None:
    """병합 A→B(A disabled · 구성원 B로) · 분할(새 수동 서비스) 뒤 재계산이 되돌리지 않는다."""
    service, store, _ = await _applied()
    ibk, card = store.service("인터넷뱅킹")["id"], store.service("카드")["id"]
    merged = await store.merge_services(card, ibk, by="adm")
    assert merged["moved"] == 2
    split = await store.split_service(ibk, [store.member("pg_a|101")["id"]],
                                      service_key="ibk_web", display_name="인터넷뱅킹 웹")
    new_id = split["service"]["id"]
    before = _snap(store)
    await _recompute(service)
    assert _snap(store) == before
    assert store.services[card]["state"] == "disabled"
    assert before["pg_a|201"][0] == ibk and before["pg_a|101"][0] == new_id
    assert sorted(s["service_key"] for s in store.services.values()) == [
        "ibk_web", "인터넷뱅킹", "카드"]  # 병합 원 키를 다시 만들지 않는다


# ── 구성원 키 ─────────────────────────────────────────────────────────
def test_build_member_keys_per_kind() -> None:
    assert build_member("sms_server", db_id="pg_a", resource_id="101")[0] == "pg_a|101"
    key, shown = build_member("apm_instance", source_id="jen1", domain_id="10",
                              instance_name=" WAS01_1 ")
    assert key == "jen1|10|instance|was01_1" and shown["instance_name"] == "WAS01_1"
    assert build_member("apm_instance", source_id="jen1", domain_id="10", scope="workload",
                        instance_name="web-a-7d9f8c6b5-x2kq9")[0] == "jen1|10|workload|web-a"
    assert build_member("apm_domain", source_id="jen1", domain_id="10")[0] == "jen1|10"
    assert build_member("apm_business", source_id="jen1", domain_id="10",
                        business_id="7")[0] == "jen1|10|7"


@pytest.mark.parametrize("kwargs", [
    {"kind": "bogus"},
    {"kind": "sms_server", "db_id": "pg_a"},
    {"kind": "sms_server", "db_id": "pg|a", "resource_id": "1"},
    {"kind": "apm_instance", "source_id": "jen1", "domain_id": "10", "scope": "domain",
     "instance_name": "x"},
    {"kind": "apm_instance", "source_id": "jen1", "domain_id": "10", "instance_name": ""},
])
def test_build_member_rejects_invalid(kwargs: dict[str, Any]) -> None:
    kind = kwargs.pop("kind")
    with pytest.raises(BizServiceAdminError) as info:
        build_member(kind, **kwargs)
    assert info.value.code == "invalid_member" and info.value.status == 400


async def test_snapshot_display_uses_snapshot_once_and_falls_back_to_key() -> None:
    maps = FakeMaps(_sms(NAMES))
    calls = []
    orig = maps.snapshot_rows

    async def counted(table: str) -> list[dict[str, Any]]:
        calls.append(table)
        return await orig(table)

    maps.snapshot_rows = counted  # type: ignore[method-assign]
    shown = SnapshotDisplay(maps, zone_of=lambda db: "z1")
    got, source = await shown.sms("pg_a", "101")
    assert source == "snapshot" and got["hostname"] == "ibkweb01" and got["zone"] == "z1"
    assert (await shown.sms("pg_a", "999"))[1] == "key"
    assert calls == ["sms_inventory"]
    none = SnapshotDisplay(None, zone_of=lambda db: "")
    assert await none.sms("pg_a", "101") == ({"db_id": "pg_a", "resource_id": "101",
                                               "zone": None}, "key")
    assert await none.for_member("apm_instance", "jen1|10|instance|web-a") == {
        "source_id": "jen1", "domain_id": "10", "instance_name": "web-a"}
    assert await none.for_member("apm_instance", "jen1|10|instance|web-a",
                                 {"display": {"instance_name": "Web-A"}}) == {
        "instance_name": "Web-A"}


# ── 후보 수락 분기 ────────────────────────────────────────────────────
async def test_accept_candidate_dispatch_and_refusals() -> None:
    store = AdminFakeStore()
    store.services = {1: {"id": 1, "service_key": "a", "state": "active", "evidence": {},
                          "version": 1, "origin": "auto", "verified": False},
                      2: {"id": 2, "service_key": "b", "state": "active", "evidence": {},
                          "version": 1, "origin": "auto", "verified": False}}
    store.candidates = {
        10: {"id": 10, "kind": "merge", "status": "open", "service_id": 1,
             "other_service_id": 2},
        11: {"id": 11, "kind": "apm_unlinked", "status": "open", "service_id": 1},
        12: {"id": 12, "kind": "member", "status": "open", "service_id": None},
        13: {"id": 13, "kind": "member", "status": "rejected", "service_id": 1},
    }
    shown = SnapshotDisplay(None)
    got = await accept_candidate(store, 10, display=shown, by="adm")
    assert (got["from_service_id"], got["to_service_id"]) == (2, 1)  # 다른 서비스 → 후보 서비스
    assert store.services[2]["state"] == "disabled"
    for cid, code, status in ((11, "candidate_not_acceptable", 400),
                              (12, "candidate_needs_service", 400),
                              (13, "candidate_closed", 409), (99, "not_found", 404)):
        with pytest.raises(BizServiceAdminError) as info:
            await accept_candidate(store, cid, display=shown, by="adm")
        assert (info.value.code, info.value.status) == (code, status)


# ── APM 대조 ──────────────────────────────────────────────────────────
class FakeCaller:
    def __init__(self, env: Any = None, exc: Exception | None = None) -> None:
        self.env, self.exc = env, exc
        self.calls: list[tuple[str, dict[str, Any], float | None]] = []

    async def call_tool(self, tool_name: str, arguments: dict[str, Any], *,
                        timeout: float | None = None) -> dict[str, Any]:
        self.calls.append((tool_name, arguments, timeout))
        if self.exc is not None:
            raise self.exc
        return self.env


def _apm_service() -> dict[str, Any]:
    def mem(mid: int, kind: str, key: str, **extra: Any) -> dict[str, Any]:
        return {"id": mid, "kind": kind, "member_key": key, "state": "active",
                "confidence": "confirmed", **extra}
    return {"id": 5, "service_key": "ibk", "display_name": "인터넷뱅킹", "members": [
        mem(1, "apm_instance", "jen1|10|instance|web-a", instance_name="web-a"),
        mem(2, "apm_instance", "jen1|10|workload|api", instance_name="api"),
        mem(3, "apm_domain", "jen1|20"),
        mem(4, "apm_instance", "jen1|10|instance|gone", instance_name="gone"),
        mem(5, "apm_instance", "jen1|10|instance|off", state="excluded"),
        mem(6, "sms_server", "pg_a|101"),
    ]}


def _row(dom: str, name: str) -> dict[str, Any]:
    return {"source_id": "jen1", "domain_id": dom, "domain_name": f"D{dom}",
            "instance_name": name}


async def test_apm_compare_diff_and_candidates_only() -> None:
    """수용 18 — 해석에만 있는 인스턴스는 후보(근거 gateway_business) · 구성원은 그대로."""
    store = AdminFakeStore()
    rows = [_row("10", "web-a"), _row("10", "api-5d8f9c7b6-k2x9q"), _row("20", "batch1"),
            _row("10", "new-b"), _row("10", "NEW-B"), _row("10", "off")]
    caller = FakeCaller({"rows": rows, "total_row_count": 6, "job": {"state": "done"}})
    service = _apm_service()
    got = await apm_compare(store, caller, service, by="adm")
    tool, args, timeout = caller.calls[0]
    assert tool == "apm_instance_map" and args["business"] == "인터넷뱅킹"
    assert args["owner"] == adm.GATEWAY_OWNER and timeout == adm.COMPARE_CALL_TIMEOUT
    assert {r["instance_name"]: r["matched_by"] for r in got["matched"]} == {
        "web-a": "jen1|10|instance|web-a", "api-5d8f9c7b6-k2x9q": "jen1|10|workload|api",
        "batch1": "jen1|20"}
    assert [r["member_key"] for r in got["only_in_gateway"]] == [
        "jen1|10|instance|new-b", "jen1|10|instance|off"]  # 대소문자 같은 키는 1번 · 제외는 미일치
    assert [m["member_id"] for m in got["only_in_members"]] == [4]
    assert got["gateway"] == {"status": "ok", "rows": 6, "total_row_count": 6, "partial": False}
    assert got["candidates"] == {"inserted": 2, "existing": 0, "truncated": 0}
    cand = store.candidates[max(store.candidates)]
    assert cand["kind"] == "member" and cand["service_id"] == 5
    assert cand["signals"][0]["basis"] == adm.BASIS_GATEWAY_BUSINESS
    assert store.members == {}  # 구성원은 바꾸지 않는다
    again = await apm_compare(store, caller, service, by="adm")
    assert again["candidates"] == {"inserted": 0, "existing": 2, "truncated": 0}


async def test_apm_compare_partial_and_errors() -> None:
    store = AdminFakeStore()
    service = _apm_service()
    got = await apm_compare(store, FakeCaller({"rows": [_row("10", "web-a")],
                                               "total_row_count": 50}), service, by="adm")
    assert got["gateway"]["partial"] is True and got["gateway"]["status"] == "partial"
    cases = [
        (None, "gateway_unavailable", 503),
        (FakeCaller(exc=RuntimeError("secret-token-abc")), "gateway_unreachable", 502),
        (FakeCaller({"error": {"code": "upstream_timeout"}}), "gateway_error", 502),
        (FakeCaller({"job": {"state": "running"}, "rows": []}), "gateway_pending", 409),
    ]
    for caller, code, status in cases:
        with pytest.raises(BizServiceAdminError) as info:
            await apm_compare(store, caller, service, by="adm")
        assert (info.value.code, info.value.status) == (code, status)
        assert "secret-token-abc" not in str(info.value)
    with pytest.raises(BizServiceAdminError) as info:
        await apm_compare(store, FakeCaller({}), {**service, "display_name": "x" * 201},
                          by="adm")
    assert info.value.code == "business_too_long"


# ── 게이트웨이 업무 매핑 사본 ─────────────────────────────────────────
def test_gateway_business_map_confirmed_instances_only() -> None:
    service = _apm_service()
    service["members"] += [
        {"id": 7, "kind": "apm_instance", "member_key": "jen2|1|instance|x1",
         "instance_name": "X1", "state": "active", "confidence": "confirmed"},
        {"id": 8, "kind": "apm_instance", "member_key": "jen2|1|instance|x2",
         "instance_name": "x2", "state": "active", "confidence": "inferred"},
    ]
    got = gateway_business_map(service)
    assert got["entries"] == [
        {"business": "인터넷뱅킹", "aliases": [], "instances": ["gone", "web-a"],
         "source_id": "jen1"},
        {"business": "인터넷뱅킹", "aliases": [], "instances": ["X1"], "source_id": "jen2"},
    ]
    assert got["instance_count"] == 3
    assert {s["member_id"]: s["reason"] for s in got["skipped"]} == {
        2: "workload_scope", 3: "not_instance", 8: "not_confirmed"}
    assert yaml.safe_load(got["yaml"]) == {"business_map": got["entries"]}
    assert "인터넷뱅킹" in got["yaml"]  # allow_unicode
    empty = gateway_business_map({"display_name": "x", "members": []})
    assert empty == {"yaml": "", "entries": [], "instance_count": 0, "skipped": []}
