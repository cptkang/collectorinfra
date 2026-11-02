"""plans/196 §1.1 · §5 — 호스트 연결 학습(정규화·등급·패턴·저장소·후보 검색·페이로드 힌트).

DB·Redis는 띄우지 않는다 — 프로브는 대역 함수, Redis는 `tests.mocks.async_redis.FakeAsyncRedis`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from src.domain.host_names import (
    k8s_workload_name,
    name_similarity,
    name_tokens,
    normalize_hostname,
)
from tests.mocks.async_redis import FakeAsyncRedis

from noise_gate.application import host_candidates as hc
from noise_gate.application.nodes import investigation_trigger as trig
from noise_gate.domain import host_link as hl
from noise_gate.infrastructure.host_link_store import (
    PATTERNS_KEY,
    HostLinkChangedError,
    HostLinkStore,
    build_host_link_store,
    link_key,
)
from noise_gate.infrastructure.investigation_store import investigation_view
from noise_gate.infrastructure.polestar_hostname_resolver import build_host_token_sql

POD = "bfm-kbland-admin-price-deploy-6dd49d5df6"


def _run(coro):  # noqa: ANN001, ANN202
    return asyncio.run(coro)


# ── src/domain/host_names ───────────────────────────────────────────────
def test_normalize_hostname():
    assert normalize_hostname("  WEBAP01.kb.local ") == "webap01"
    assert normalize_hostname("192.0.2.7") == "192.0.2.7"
    assert normalize_hostname(None) == ""


def test_k8s_workload_name():
    assert k8s_workload_name(POD) == "bfm-kbland-admin-price"
    assert k8s_workload_name("app-deploy-6dd49d5df6-x7k2p") == "app"
    assert k8s_workload_name("webap01") is None
    assert k8s_workload_name("192.0.2.7") is None


def test_name_tokens_and_similarity():
    assert name_tokens(POD) == ["bfm", "kbland", "admin", "price", "deploy"]
    assert name_tokens("a_bc-kbland.x") == ["kbland"]
    assert name_similarity("WEB01", "web01") == 1.0
    assert 0.0 < name_similarity("web01", "web02") < 1.0


# ── 패턴 ────────────────────────────────────────────────────────────────
def test_extract_and_apply_patterns():
    assert hl.extract_patterns("WEBAP01.kb.local", ["webap01"]) == ["normalize||"]
    assert hl.extract_patterns(POD, ["bfm-kbland-admin-price"]) == ["k8s||"]
    assert hl.extract_patterns(POD, ["kbl-kbland-admin-price"]) == ["prefix|bfm|kbl"]
    assert hl.extract_patterns("web_ap01", ["web-ap01"]) == ["sep|_|-"]
    assert hl.extract_patterns("web01", ["web01"]) == []
    produced = hl.apply_pattern("prefix|bfm|kbl", "bfm-kbland-pay-deploy-6dd49d5df6")
    assert produced == "kbl-kbland-pay"
    assert hl.apply_pattern("sep|_|-", "app_ap02") == "app-ap02"
    assert hl.apply_pattern("prefix|bfm|kbl", "zzz-x") is None


# ── 등급 ────────────────────────────────────────────────────────────────
def test_grades_exact_likely_similar():
    plan = hl.build_probe_plan("WEBAP01.kb.local", ip="192.0.2.7")
    rows = [
        ("db1", {"hostname": "webap01.kb.local", "name": "WEBAP01", "ipaddress": ""}),
        ("db1", {"hostname": "other", "name": "x", "ipaddress": "192.0.2.7"}),
    ]
    out = hl.rank_candidates(plan, rows)
    assert [c["grade_key"] for c in out] == ["exact", "likely"]
    assert out[0]["reasons"] == ["대소문자만 다름", "FQDN 짧은 이름"]
    assert out[1]["reasons"] == ["IP 같음"]

    plan = hl.build_probe_plan(POD)
    rows = [("db2", {"hostname": "kbland-price-ap01", "name": "KBLAND-PRICE-AP01"})]
    (cand,) = hl.rank_candidates(plan, rows)
    assert cand["grade"] == "유사" and cand["grade_key"] == "similar"
    assert cand["reasons"][0] == "디플로이먼트 이름 토큰 kbland·price 일치"
    assert "실행 노드 아님 가능" in cand["reasons"][1]
    assert 0 < cand["score"] < 0.8
    # 근거가 하나도 없으면 후보가 아니다
    assert hl.rank_candidates(plan, [("db2", {"hostname": "zzz", "name": "yyy"})]) == []


def test_pattern_and_confirmed_grades():
    patterns = {"prefix|bfm|kbl": 3}
    plan = hl.build_probe_plan(POD, patterns=patterns)
    assert "kbl-kbland-admin-price" in plan.names
    row = {"hostname": "kbl-kbland-admin-price", "name": "kbl-kbland-admin-price"}
    (cand,) = hl.rank_candidates(plan, [("db1", row)], patterns=patterns)
    assert cand["grade_key"] == "likely"
    assert "이전 확정 패턴 「접두 치환 bfm → kbl」 적용(3건)" in cand["reasons"]

    link = {"state": "confirmed", "kind": "link", "db_id": "db1",
            "server_name": "KBL-KBLAND-ADMIN-PRICE", "by": "op", "at": "t"}
    (cand,) = hl.rank_candidates(plan, [("db1", row)], patterns=patterns, confirmed=link)
    assert cand["grade"] == "확정" and cand["reasons"][0] == "이전 확정 — op·t"


def test_polestar_link_hint_rules():
    now = datetime(2026, 10, 27, tzinfo=UTC)
    assert hl.polestar_link_hint(None, now) == {"status": "unresolved"}
    pending = {"state": "pending", "kind": "link", "db_id": "d", "server_name": "s"}
    assert hl.polestar_link_hint(pending, now) == {"status": "unresolved"}
    confirmed = {**pending, "state": "confirmed", "polestar_hostname": "h", "by": "op", "at": "t"}
    assert hl.polestar_link_hint(confirmed, now) == {
        "status": "linked", "db_id": "d", "server_name": "s", "hostname": "h",
        "basis": "previous_confirmed", "by": "op", "at": "t",
    }
    absent = {"state": "confirmed", "kind": "absent", "by": "op", "at": "t",
              "expires_at": (now + timedelta(days=1)).isoformat()}
    assert hl.polestar_link_hint(absent, now) == {"status": "absent", "by": "op", "at": "t"}
    expired = {**absent, "expires_at": (now - timedelta(days=1)).isoformat()}
    assert hl.polestar_link_hint(expired, now) == {"status": "unresolved"}


def test_apm_source_id():
    assert hl.apm_source_id("jennifer_cm_prd") == "cm_prd"
    assert hl.apm_source_id("jennifer", "s1") == "s1"
    assert hl.apm_source_id("jennifer") == "jennifer"


# ── SQL 빌더 ─────────────────────────────────────────────────────────────
def test_token_sql_dialects_and_safety():
    pg = build_host_token_sql("polestar_cm_gp", ["kbland", "pr%ice", "price"])
    assert pg.startswith("SELECT ") and "LIMIT 50" in pg
    assert "'%kbland%'" in pg and "pr%ice" not in pg
    db2 = build_host_token_sql("polestar_b0", ["kbland"], db_engine="db2")
    assert "FETCH FIRST 50 ROWS ONLY" in db2 and "LIMIT" not in db2
    with pytest.raises(ValueError):
        build_host_token_sql("polestar_cm_gp", ["a'b"])
    for word in ("INSERT", "UPDATE", "DELETE", "DROP"):
        assert word not in pg.upper()


# ── 저장소 ──────────────────────────────────────────────────────────────
def test_store_confirm_pending_patterns_delete():
    redis = FakeAsyncRedis()
    store = HostLinkStore(redis)

    async def _scenario():  # noqa: ANN202
        rec, stored = await store.save(
            "cm_prd", POD, kind="link", confirmed=True, by="op", by_role="operator",
            db_id="polestar_cm_gp", server_name="kbl-kbland-admin-price",
            polestar_hostname="kbl-kbland-admin-price", grade="similar",
        )
        assert stored and rec["state"] == "confirmed"
        assert await store.patterns() == {"prefix|bfm|kbl": 1}
        # 일반 사용자 대기 선택은 확정을 덮지 않는다
        _, stored2 = await store.save(
            "cm_prd", POD.upper(), kind="link", confirmed=False, by="u1", by_role="user",
            db_id="polestar_cm_gp", server_name="other",
        )
        assert stored2 is False
        assert (await store.get("cm_prd", POD))["server_name"] == "kbl-kbland-admin-price"
        assert len(await store.list_links()) == 1
        deleted = await store.delete("cm_prd", POD)
        assert deleted is not None and await store.patterns() == {}
        assert await store.get("cm_prd", POD) is None

        # 대기 → 승인 → 패턴 건수
        await store.save("cm_prd", "WEB01.kb.local", kind="link", confirmed=False, by="u1",
                         by_role="user", db_id="d", server_name="web01")
        assert await store.patterns() == {}
        approved = await store.approve("cm_prd", "web01", by="op")
        assert approved["state"] == "confirmed" and approved["approved_by"] == "op"
        assert await store.patterns() == {"normalize||": 1}

    _run(_scenario())
    assert link_key("cm_prd", "WEB01.kb.local") == "hostlink:cm_prd:web01"
    assert PATTERNS_KEY in redis.dump()


def test_store_absent_expires():
    store = HostLinkStore(FakeAsyncRedis())
    past = datetime.now(UTC) - timedelta(days=31)

    async def _scenario():  # noqa: ANN202
        rec, _ = await store.save("s", "pod-x", kind="absent", confirmed=True, by="op",
                                  by_role="operator", now=past)
        assert rec["kind"] == "absent" and rec["expires_at"]
        assert await store.get("s", "pod-x") is None
        assert (await store.list_links())[0]["expired"] is True

    _run(_scenario())


def test_build_store_flag_off_none():
    cfg = SimpleNamespace(noise_gate=SimpleNamespace(host_link_enabled=False))
    assert build_host_link_store(cfg, FakeAsyncRedis()) is None


# ── 후보 검색 ───────────────────────────────────────────────────────────
def test_find_candidates_per_source_failure_is_unchecked():
    calls: list[tuple] = []

    async def _probe(db_id, names, prefixes, ips):  # noqa: ANN001, ANN202
        calls.append(("probe", db_id, tuple(names), tuple(prefixes)))
        if db_id == "polestar_b0":
            raise RuntimeError("down")
        return [{"hostname": "kbland-price-ap01", "name": "KBLAND-PRICE-AP01", "ipaddress": ""}]

    async def _tokens(db_id, tokens):  # noqa: ANN001, ANN202
        calls.append(("tokens", db_id, tuple(tokens)))
        return []

    out = _run(hc.find_host_candidates(
        None, hostname=POD, source_id="cm_prd", db_ids=["polestar_b0", "polestar_cm_gp"],
        probe=_probe, token_probe=_tokens,
    ))
    assert out["unchecked"] == [{"db_id": "polestar_b0", "reason": "조회 실패(RuntimeError)"}]
    assert [c["db_id"] for c in out["candidates"]] == ["polestar_cm_gp"]
    assert out["link"] is None and out["hostname"] == POD and out["source_id"] == "cm_prd"
    assert ("tokens", "polestar_cm_gp", ("bfm", "kbland", "admin", "price")) in calls


def test_verify_selected_server():
    async def _probe(db_id, names, prefixes, ips):  # noqa: ANN001, ANN202
        return [{"hostname": "web01.kb.local", "name": "WEB01", "ipaddress": "192.0.2.1"}]

    row = _run(hc.verify_selected_server(None, db_id="d", server_name="web01", hostname=None,
                                         probe=_probe))
    assert row == {"server_name": "WEB01", "hostname": "web01.kb.local", "ipaddress": "192.0.2.1"}

    async def _two(db_id, names, prefixes, ips):  # noqa: ANN001, ANN202
        return [{"hostname": "a.x", "name": "a"}, {"hostname": "a.y", "name": "b"}]

    assert _run(hc.verify_selected_server(None, db_id="d", server_name=None, hostname="a",
                                          probe=_two)) == {"ambiguous": True}

    async def _none(db_id, names, prefixes, ips):  # noqa: ANN001, ANN202
        return []

    assert _run(hc.verify_selected_server(None, db_id="d", server_name="z", hostname=None,
                                          probe=_none)) is None


def test_verify_requires_exact_match_no_fallback():
    """넓힘 결과(정확히 맞지 않는 행)로 대체하지 않는다 — a~c 정확 일치만 받는다."""

    async def _near(db_id, names, prefixes, ips):  # noqa: ANN001, ANN202
        return [{"hostname": "web01-dr.kb.local", "name": "WEB01-DR", "ipaddress": "192.0.2.9"}]

    assert _run(hc.verify_selected_server(None, db_id="d", server_name="web01", hostname=None,
                                          probe=_near)) is None
    # c — 짧은 이름 입력이 행 FQDN과 맞는다
    assert _run(hc.verify_selected_server(None, db_id="d", server_name="web01-dr",
                                          hostname=None, probe=_near))["server_name"] == "WEB01-DR"
    # IP 입력은 행 IP와 맞는다
    assert _run(hc.verify_selected_server(None, db_id="d", server_name=None,
                                          hostname="192.0.2.9", probe=_near)) is not None


@pytest.mark.parametrize("bad", ["web01'", "web 01", "web01;drop", "w%", "웹01", "a:b"])
def test_verify_rejects_unsafe_text_before_probe(bad: str):
    called: list[str] = []

    async def _probe(db_id, names, prefixes, ips):  # noqa: ANN001, ANN202
        called.append(db_id)
        return []

    with pytest.raises(ValueError):
        _run(hc.verify_selected_server(None, db_id="d", server_name=bad, hostname=None,
                                       probe=_probe))
    with pytest.raises(ValueError):
        _run(hc.locate_selected_server(None, db_ids=["d"], server_name=bad, hostname=None,
                                       probe=_probe))
    assert called == []


def test_locate_selected_server_exactly_one_source():
    async def _probe(db_id, names, prefixes, ips):  # noqa: ANN001, ANN202
        if db_id == "down":
            raise RuntimeError("x")
        if db_id in ("a", "b") and "web01" in names:
            return [{"hostname": "web01", "name": "WEB01", "ipaddress": ""}]
        return []

    one = _run(hc.locate_selected_server(None, db_ids=["a", "c", "down"], server_name="web01",
                                         hostname=None, probe=_probe))
    assert one["db_id"] == "a" and one["row"]["server_name"] == "WEB01"
    assert one["ambiguous"] is False
    assert one["unchecked"] == [{"db_id": "down", "reason": "조회 실패(RuntimeError)"}]
    two = _run(hc.locate_selected_server(None, db_ids=["a", "b"], server_name="web01",
                                         hostname=None, probe=_probe))
    assert two["ambiguous"] is True and two["db_id"] is None
    none = _run(hc.locate_selected_server(None, db_ids=["c"], server_name="web01",
                                          hostname=None, probe=_probe))
    assert none["row"] is None and none["ambiguous"] is False


def test_token_sql_drops_wildcard_and_quote_tokens():
    """L1 불변 — `%`·`'`·`_`·`;`가 든 토큰은 SQL에 실리지 않는다."""
    bad = ["a%b", "o'neil", "x_y", "drop;", "ok"]
    sql = build_host_token_sql("polestar_cm_gp", bad)
    assert "'%ok%'" in sql
    for token in bad[:-1]:
        assert token not in sql
    assert sql.count(";") == 0
    with pytest.raises(ValueError):
        build_host_token_sql("polestar_cm_gp", bad[:-1])


@pytest.mark.parametrize(("source", "host"), [("cm:prd", POD), ("cm_prd", "a:b"), ("", POD),
                                              ("cm_prd", "  "), ("cm_prd", ".")])
def test_link_key_rejects_separator_and_empty(source: str, host: str):
    assert hl.valid_link_key(source, host) is False
    with pytest.raises(ValueError):
        link_key(source, host)


def test_approve_compare_and_set_and_hincrby_patterns():
    redis = FakeAsyncRedis()
    store = HostLinkStore(redis)

    async def _scenario():  # noqa: ANN202
        rec, _ = await store.save("s", "WEB01.kb.local", kind="link", confirmed=False, by="u1",
                                  by_role="user", db_id="d", server_name="web01")
        seen = {"db_id": "d", "server_name": "web01", "at": rec["at"]}
        with pytest.raises(HostLinkChangedError):
            await store.approve("s", "web01", by="op", expect={**seen, "server_name": "web02"})
        assert (await store.get("s", "web01"))["state"] == "pending"
        approved = await store.approve("s", "web01", by="op", expect=seen)
        assert approved["state"] == "confirmed"
        assert await redis.hget(PATTERNS_KEY, "normalize||") == "1"
        await store.delete("s", "web01")
        assert await redis.hgetall(PATTERNS_KEY) == {}

    _run(_scenario())


def test_public_link_view_drops_actor_keys():
    link = {"state": "confirmed", "kind": "link", "db_id": "d", "server_name": "s",
            "polestar_hostname": "h", "grade": "exact", "by": "u1", "by_role": "user",
            "approved_by": "op", "investigation_id": "inv", "at": "t", "source_id": "x"}
    assert hl.public_link_view(link) == {"state": "confirmed", "kind": "link", "db_id": "d",
                                         "server_name": "s", "polestar_hostname": "h",
                                         "grade": "exact"}
    assert hl.public_link_view(None) is None


# ── 페이로드 힌트(자동·수동 대칭) ──────────────────────────────────────────
def _apm_event(**over):  # noqa: ANN003, ANN202
    base = dict(
        db_id="jennifer_cm_prd", server_name=POD, hostname=POD, alarm_id="a1", severity=3,
        alarm_name="SQL_TOOMANY_FETCH", resource_type="apm.Instance", resource_name="",
        alarm_time=None, conditions="", condition_log="",
        raw_payload={"apm": {"source_id": "cm_prd"}},
    )
    base.update(over)
    return SimpleNamespace(**base)


def _cfg(enabled: bool):  # noqa: ANN202
    return SimpleNamespace(
        noise_gate=SimpleNamespace(host_link_enabled=enabled),
        composite=SimpleNamespace(availability_precheck_enabled=False),
    )


def _decision():  # noqa: ANN202
    return SimpleNamespace(tier="page", reason="r", fingerprint="fp", signals={})


def _manual(cfg, event, **kw):  # noqa: ANN001, ANN003, ANN202
    return _run(trig.build_manual_payload(
        cfg, event, _decision(), fingerprint="manual:a1:x", investigation_kind="manual", **kw
    ))


def test_flag_off_payload_bit_identical():
    store = HostLinkStore(FakeAsyncRedis())
    off = _manual(_cfg(False), _apm_event(), host_link_store=store)
    base = _manual(_cfg(False), _apm_event())
    assert off == base and "polestar_link" not in off["meta"]["hints"]


def test_manual_and_auto_attach_confirmed_link():
    redis = FakeAsyncRedis()
    store = HostLinkStore(redis)
    _run(store.save("cm_prd", POD, kind="link", confirmed=True, by="op", by_role="operator",
                    db_id="polestar_cm_gp", server_name="KBL-01", polestar_hostname="kbl-01"))
    manual = _manual(_cfg(True), _apm_event(raw_payload=None), host_link_store=store)
    link = manual["meta"]["hints"]["polestar_link"]
    assert link["status"] == "linked" and link["basis"] == "previous_confirmed"
    assert link["server_name"] == "KBL-01" and link["db_id"] == "polestar_cm_gp"

    payload = {"meta": {"hints": {"solution": "apm", "source_id": "cm_prd"}}}
    _run(trig._attach_polestar_link(_cfg(True), _apm_event(), payload, redis=redis))
    assert payload["meta"]["hints"]["polestar_link"] == link


def test_unresolved_without_link_and_non_apm_untouched():
    store = HostLinkStore(FakeAsyncRedis())
    out = _manual(_cfg(True), _apm_event(), host_link_store=store)
    assert out["meta"]["hints"]["polestar_link"] == {"status": "unresolved"}
    polestar = _manual(_cfg(True), _apm_event(db_id="polestar_cm_gp", resource_type="server"),
                       host_link_store=store)
    assert "hints" not in polestar["meta"]


def test_rerun_hint_and_user_selection():
    selected = hl.user_selected_hint(db_id="d", server_name="s", polestar_hostname="h",
                                     by="u1", at="t")
    rerun = {"parent_investigation_id": "inv-1", "axes": ["polestar"], "prior_sections": [{}]}
    out = _manual(_cfg(True), _apm_event(), polestar_link=selected, rerun=rerun)
    assert out["meta"]["hints"]["polestar_link"]["basis"] == "user_selected"
    assert out["meta"]["hints"]["rerun"] == rerun


# ── 섹션 개발자 사유 분리(§5.3) ─────────────────────────────────────────────
def test_reason_detail_only_in_operator_view():
    rec = {
        "investigation_id": "i", "status": "done", "alarm_ids": ["a1"],
        "briefing": {"playbook": {"sections": [
            {"step": "S4", "status": "skipped", "reason": "서버 선택 대기",
             "reason_detail": "W21 P6"},
        ]}},
    }
    user = investigation_view(rec, mask_fn=lambda s: s)
    assert "reason_detail" not in user["briefing"]["playbook"]["sections"][0]
    assert user["briefing"]["playbook"]["sections"][0]["reason"] == "서버 선택 대기"
    op = investigation_view(rec, operator_view=True)
    assert op["briefing"]["playbook"]["sections"][0]["reason_detail"] == "W21 P6"
