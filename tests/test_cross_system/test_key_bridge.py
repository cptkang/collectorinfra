"""값 기반 키 브리지 — 선택 · 조립 · 판정 · 보고 (plans/102 X-3 · §3.3 ②~⑤ · D-224 ③④).

검증 계약:
- ② 선택: 컬럼명이 아니라 값으로 키를 고른다(§1.2 실행 재현 뒤집힘) · 대상 매니페스트 우선순(P4) ·
  둘 다 없으면 "찾은 값 타입 · 대상이 받는 타입" 사유 · 이름 힌트만이면 종전 경로(§3.2).
- ③ 조립: 대상 컬럼을 코드가 확정(위임 문구 제거 · X-T2) · FQDN 단축명·단일 레이블 LIKE ·
  다중값 IP는 토큰 LIKE · 상한 100과 절단 보고 · 매니페스트 부재여도 무스코프 금지.
- ④⑤ 판정·보고: 샌드박스 호스트 키 4형 기대 등급 · per_ip 묶음 · ambiguous 미포함 ·
  합계 = N · 노트 1건.

LLM·네트워크 0(D-127). SQL 조건의 의미는 표준 SQL 연산자만 쓰므로 인메모리 sqlite로 확인한다.
"""

from __future__ import annotations

import copy
import dataclasses
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import src.nodes.key_bridge as kb
from src.domain.entity_key import FAMILY_HOSTNAME, FAMILY_IP
from src.schema_cache.entity_key_manifest import (
    clear_manifest_cache,
    load_entity_key_manifest,
    load_manifest_file,
)
from src.utils.prior_dependency import (
    NOTE_BRIDGE,
    REASON_PRIOR_NO_IDENTITY,
    DependencyVerdict,
    assess_prior_dependency,
)
from src.utils.query_gen_common import build_prior_rows_block

ROOT = Path(__file__).resolve().parents[2]
ITAM_LOCAL = ROOT / "testdata" / "itam" / "entity_keys.local.yaml"

#: §1.2 실행 재현 — 자산관리 선행 행
ITAM_PRIOR_ROW = {"sevrHostName": "svr-web-01", "iPCtnt": "10.0.1.1"}
#: 샌드박스 호스트 키 4형(testdata/itam/init/02_seed.sql)
ITAM_FOUR_FORMS = [
    {"sevrHostName": "svr-web-01", "iPCtnt": "10.0.1.1", "mntnCtrtEndYmd": "20271231"},
    {"sevrHostName": "SVR-WEB-02", "iPCtnt": "10.0.1.2", "mntnCtrtEndYmd": "20271231"},
    {
        "sevrHostName": "svr-web-03.synth.example",
        "iPCtnt": "10.0.1.3",
        "mntnCtrtEndYmd": "20271231",
    },
    {"sevrHostName": "svr-web-04", "iPCtnt": "10.0.1.4,10.0.1.104", "mntnCtrtEndYmd": "20271231"},
]
T2 = {"task_id": "t2", "agent": "data_query", "input_from": ["t1"], "depends_on": ["t1"]}


@pytest.fixture(autouse=True)
def itam_manifest(monkeypatch):
    """런타임 로더는 testdata를 보지 않는다 — 테스트만 로컬 하네스 매니페스트를 주입한다."""
    clear_manifest_cache()
    local = load_manifest_file(ITAM_LOCAL, "itam")
    assert local is not None

    def _load(db_id, *, profiles_dir=None):
        return (
            local if db_id == "itam" else load_entity_key_manifest(db_id, profiles_dir=profiles_dir)
        )

    monkeypatch.setattr(kb, "load_entity_key_manifest", _load)
    yield local
    clear_manifest_cache()


def _verdict(values: list[str], col: str = "hostname") -> DependencyVerdict:
    return DependencyVerdict(ok=True, scope_col=col, scope_values=values, scope_size=len(values))


# ──────────────────────────────────────────────
# 플래그
# ──────────────────────────────────────────────


def test_flag_reads_only_true(mock_config):
    assert kb.key_bridge_enabled(mock_config) is False
    assert kb.key_bridge_enabled(MagicMock()) is False  # 설정 대역 오발동 금지
    assert kb.key_bridge_enabled(None) is False
    mock_config.cross_system_key_bridge_enabled = True
    assert kb.key_bridge_enabled(mock_config) is True


# ──────────────────────────────────────────────
# ② 선택
# ──────────────────────────────────────────────


def test_itam_prior_row_is_blocked_by_name_rule_but_passes_by_value():
    """★ §1.2 실행 재현이 뒤집힌다 — 이름 판정은 prior_no_identity, 값 판정은 hostname 키."""
    prior = {"t1": {"query_results": [ITAM_PRIOR_ROW]}}
    legacy = assess_prior_dependency(T2, prior)
    assert legacy is not None and legacy.reason == REASON_PRIOR_NO_IDENTITY

    gate = kb.resolve_gate_identity(T2, prior)
    assert gate.identity == ("sevrHostName", ["svr-web-01"])
    assert gate.context == kb.BridgeContext(family=FAMILY_HOSTNAME, source_col="sevrHostName")
    verdict = assess_prior_dependency(
        T2, prior, identity=gate.identity, no_identity_hint=gate.no_identity_hint
    )
    assert verdict is not None and verdict.ok and verdict.scope_values == ["svr-web-01"]


def test_target_priority_decides_family():
    """대상 매니페스트가 받는 계열 우선순으로 값이 있는 첫 계열(P4)."""
    rows = [ITAM_PRIOR_ROW]
    sel, _ = kb.select_prior_key(rows, (FAMILY_IP, FAMILY_HOSTNAME))
    assert sel is not None and (sel.family, sel.column, sel.normalized) == (
        FAMILY_IP,
        "iPCtnt",
        ["10.0.1.1"],
    )
    sel, _ = kb.select_prior_key(rows, (FAMILY_HOSTNAME, FAMILY_IP))
    assert sel is not None and sel.family == FAMILY_HOSTNAME


def test_falls_to_second_family_when_first_has_no_values():
    rows = [{"ipaddr_text": "10.0.1.9", "cpu": 91.2}]
    sel, _ = kb.select_prior_key(rows, (FAMILY_HOSTNAME, FAMILY_IP))
    assert sel is not None and sel.family == FAMILY_IP and sel.normalized == ["10.0.1.9"]


def test_no_key_reason_names_found_and_accepted_types():
    task = {**T2, "db_ids": ["polestar"]}
    prior = {"t1": {"query_results": [{"cpu": 91.2, "cnt": 3}, {"cpu": 88.0, "cnt": 4}]}}
    gate = kb.resolve_gate_identity(task, prior)
    assert gate.identity is None and gate.context is None
    assert "찾은 키 타입: 없음" in gate.no_identity_hint and "hostname, ip" in gate.no_identity_hint
    verdict = assess_prior_dependency(
        task, prior, identity=gate.identity, no_identity_hint=gate.no_identity_hint
    )
    assert verdict is not None and verdict.reason == REASON_PRIOR_NO_IDENTITY
    assert verdict.detail.endswith(gate.no_identity_hint)


def test_found_type_not_accepted_by_target_is_reported(monkeypatch, itam_manifest):
    """대상이 IP만 받는데 선행에 호스트명만 있으면 막히고, 사유에 양쪽 타입을 적는다."""
    ip_only = dataclasses.replace(
        itam_manifest, keys=tuple(k for k in itam_manifest.keys if k.family == FAMILY_IP)
    )
    monkeypatch.setattr(
        kb, "load_entity_key_manifest", lambda db_id, **_: ip_only if db_id == "itam" else None
    )
    gate = kb.resolve_gate_identity(
        {**T2, "db_ids": ["itam"]}, {"t1": {"rows": [{"host": "svr-web-01"}]}}
    )
    assert gate.identity is None
    assert (
        "hostname(`host`)" in gate.no_identity_hint and "받는 키 타입: ip" in gate.no_identity_hint
    )


def test_name_hint_only_keeps_legacy_path():
    """값으로 키가 안 잡히는 등록명류는 종전 컬럼명 판정이 그대로 돈다(§3.2)."""
    prior = {"t1": {"query_results": [{"name": "web01 (주문 DB)"}, {"name": "was02 (정산)"}]}}
    gate = kb.resolve_gate_identity(T2, prior)
    assert gate.identity is None and gate.context is None
    verdict = assess_prior_dependency(
        T2, prior, identity=gate.identity, no_identity_hint=gate.no_identity_hint
    )
    assert verdict == assess_prior_dependency(T2, prior)
    assert verdict is not None and verdict.ok and verdict.scope_col == "name"


def test_hostname_named_column_beats_registered_name_with_same_values():
    """D-061 — 같은 값·같은 개수여도 호스트명류 이름이 등록명보다 먼저다."""
    rows = [{"name": "svr-a", "hostname": "svr-a"}, {"name": "svr-b", "hostname": "svr-b"}]
    sel, _ = kb.select_prior_key(rows, (FAMILY_HOSTNAME,))
    assert sel is not None and sel.column == "hostname"


def test_single_row_code_column_does_not_steal_hostname_key():
    """행 1건이면 서로 다른 값 수가 동률이다 — 앞 컬럼의 코드값(값 판정상 hostname 계열)이
    호스트명 컬럼을 밀어내지 않는다(호스트명류 이름 우선)."""
    rows = [{"groupCoCd": "Z99", "sevrHostName": "svr-web-01", "iPCtnt": "10.0.1.1"}]
    sel, found = kb.select_prior_key(rows, (FAMILY_HOSTNAME, FAMILY_IP))
    # 도메인 판정은 둘 다 hostname 계열이다
    assert {kc.column for kc in found} >= {"groupCoCd", "sevrHostName"}
    assert sel is not None and sel.column == "sevrHostName" and sel.normalized == ["svr-web-01"]


def test_identity_columns_value_keys_plus_name_hint_without_source_tag():
    rows = [
        {
            "sevrHostName": "svr-web-01",
            "iPCtnt": "10.0.1.1",
            "server_id": 7,
            "cpu": 91.2,
            "_source_db": "itam",
        }
    ]
    assert kb.identity_columns(rows) == ["sevrHostName", "iPCtnt", "server_id"]


# ──────────────────────────────────────────────
# ③ 조립
# ──────────────────────────────────────────────


def test_polestar_target_block_fixes_column_and_removes_delegation():
    block = kb.bridge_prior_rows_block({"t1": [ITAM_PRIOR_ROW]}, "polestar")
    assert "LOWER(hostname) IN ('svr-web-01')" in block
    assert "LOWER(hostname) LIKE 'svr-web-01.%'" in block
    assert "`cmm_resource.hostname`" in block
    assert "동등한 식별 컬럼" not in block
    assert "별칭 없이" in block


def test_itam_target_block_uses_manifest_column():
    block = kb.bridge_prior_rows_block({"t1": [{"hostname": "SVR-WEB-02"}]}, "itam")
    assert "LOWER(sevrHostName) IN ('svr-web-02')" in block
    assert "`TCDMSIF80.sevrHostName`" in block


def test_fqdn_source_adds_short_name_and_single_label_adds_like():
    block = kb.bridge_prior_rows_block(
        {"t1": [{"hostname": "svr-web-03.synth.example"}, {"hostname": "svr-web-05"}]}, "itam"
    )
    assert (
        "LOWER(sevrHostName) IN ('svr-web-03.synth.example', 'svr-web-03', 'svr-web-05')" in block
    )
    assert "LOWER(sevrHostName) LIKE 'svr-web-05.%'" in block
    assert "LIKE 'svr-web-03.synth.example.%'" not in block


def test_ip_keys_single_and_multi_value(itam_manifest):
    polestar_ip = load_entity_key_manifest("polestar").key_for(FAMILY_IP)
    keys = kb.select_prior_key([{"ip": "10.0.1.4"}, {"ip": "10.0.1.104"}], (FAMILY_IP,))[0].keys
    assert kb.build_scope_condition(polestar_ip, keys) == "ipaddress IN ('10.0.1.4', '10.0.1.104')"
    itam_ip = itam_manifest.key_for(FAMILY_IP)
    assert (
        kb.build_scope_condition(itam_ip, keys)
        == "(iPCtnt LIKE '%10.0.1.4%' OR iPCtnt LIKE '%10.0.1.104%')"
    )


def test_truncation_keeps_100_and_reports():
    rows = {"t1": [{"hostname": f"svr-{i:03d}"} for i in range(130)]}
    scope = kb.plan_target_scope(rows, "polestar")
    assert (
        scope is not None
        and len(scope["values"]) == 100
        and scope["truncated_count"] == 30
        and scope["total"] == 130
    )
    block = build_prior_rows_block(rows, target_scope=scope)
    assert "130대 중 상한 100대" in block and "초과 30대" in block
    assert "'svr-099'" in block and "'svr-100'" not in block


def test_missing_manifest_still_scopes_with_values():
    """대상 매니페스트가 없어도 선행 키가 있으면 블록이 비지 않는다 — 무스코프 조회 금지."""
    block = kb.bridge_prior_rows_block({"t1": [ITAM_PRIOR_ROW]}, "db_without_manifest")
    assert "sevrHostName IN ('svr-web-01')" in block and "동등한 식별 컬럼" in block


def test_no_value_key_equals_legacy_block():
    prior_rows = {"t1": [{"server_name": "SV-WEB-001"}, {"server_name": "SV BATCH 009"}]}
    assert (
        kb.bridge_prior_rows_block(prior_rows, "polestar")
        == build_prior_rows_block(prior_rows)
        != ""
    )
    assert (
        kb.bridge_prior_rows_block(None, "polestar")
        == ""
        == kb.bridge_prior_rows_block({}, "polestar")
    )


def _select(table_rows: list[tuple[str, str]], condition: str, column: str) -> set[str]:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (sevrHostName TEXT, iPCtnt TEXT, hostname TEXT, ipaddress TEXT)")
    conn.executemany(
        "INSERT INTO t (sevrHostName, iPCtnt, hostname, ipaddress) VALUES (?, ?, ?, ?)",
        [(h, ip, h, ip) for h, ip in table_rows],
    )
    got = {r[0] for r in conn.execute(f"SELECT {column} FROM t WHERE {condition}")}  # noqa: S608 — 테스트 상수
    conn.close()
    return got


def test_hostname_condition_selects_four_forms_in_sql(itam_manifest):
    """조건이 실제 SQL에서 4형(정확·대소문자·FQDN·다중 IP 행)을 모두 좁혀 온다(판정 전 후보)."""
    table = [(r["sevrHostName"], r["iPCtnt"]) for r in ITAM_FOUR_FORMS] + [
        ("svr-db-99", "10.9.9.9")
    ]
    keys = kb.select_prior_key(
        [{"hostname": f"svr-web-0{i}"} for i in range(1, 5)], (FAMILY_HOSTNAME,)
    )[0].keys
    cond = kb.build_scope_condition(itam_manifest.key_for(FAMILY_HOSTNAME), keys)
    assert _select(table, cond, "sevrHostName") == {r["sevrHostName"] for r in ITAM_FOUR_FORMS}


def test_multi_value_ip_condition_is_broad_then_code_confirms(itam_manifest):
    table = [("svr-web-04", "10.0.1.4,10.0.1.104"), ("svr-web-40", "10.0.1.40")]
    keys = kb.select_prior_key([{"ip": "10.0.1.4"}], (FAMILY_IP,))[0].keys
    cond = kb.build_scope_condition(itam_manifest.key_for(FAMILY_IP), keys)
    assert _select(table, cond, "sevrHostName") == {"svr-web-04", "svr-web-40"}  # SQL은 넓게
    rows = [{"sevrHostName": h, "iPCtnt": ip} for h, ip in table]
    out = kb.apply_bridge_postcheck(
        kb.BridgeContext(FAMILY_IP, "ip"),
        _verdict(["10.0.1.4"], "ip"),
        {"query_results": rows, "target_db_ids": ["itam"]},
        "t2",
    )
    assert [r["sevrHostName"] for r in out["query_results"]] == [
        "svr-web-04"
    ]  # 코드가 토큰으로 확정
    assert out["dependency_notes"][0]["counts"]["link"] == 1


# ──────────────────────────────────────────────
# ④ 판정 · ⑤ 보고
# ──────────────────────────────────────────────


def _grade(rows, values, family=FAMILY_HOSTNAME, db="itam", **extra):
    result = {"query_results": rows, "target_db_ids": [db], **extra}
    return kb.apply_bridge_postcheck(
        kb.BridgeContext(family, "hostname"), _verdict(values), result, "t2"
    )


def test_sandbox_four_host_key_forms_expected_grades():
    """★ 정확·대소문자·다중 IP 행 → link · FQDN ↔ 단축명 → possible (H-2 ③ 오라클)."""
    out = _grade(
        copy.deepcopy(ITAM_FOUR_FORMS), ["svr-web-01", "svr-web-02", "svr-web-03", "svr-web-04"]
    )
    notes = out["dependency_notes"]
    assert len(notes) == 1
    note = notes[0]
    assert (
        note["kind"] == NOTE_BRIDGE and note["reason"] == "bridge_match" and note["task_id"] == "t2"
    )
    assert note["counts"] == {"total": 4, "link": 3, "possible": 1, "non_link": 0, "ambiguous": 0}
    assert note["coverage"] == 1.0
    assert (
        "선행 4대 → " in note["detail"]
        and "일치 3 · 가능 1 · 미발견 0 · 모호 0 · 키=hostname" in note["detail"]
    )
    assert len(out["query_results"]) == 4 and "removed_rows" not in note


def test_per_ip_rows_group_into_one_entity_not_ambiguous():
    """한 서버가 IP마다 행을 가진 대상(per_ip) — IP 키 둘이 같은 엔터티에 걸려도 모호가 아니다."""
    rows = [
        {"sevrHostName": "svr-web-04", "iPCtnt": "10.0.1.4"},
        {"sevrHostName": "svr-web-04", "iPCtnt": "10.0.1.104"},
    ]
    out = kb.apply_bridge_postcheck(
        kb.BridgeContext(FAMILY_IP, "ip"),
        _verdict(["10.0.1.4", "10.0.1.104"], "ip"),
        {"rows": rows, "target_db_ids": ["itam"]},
        "t2",
    )
    counts = out["dependency_notes"][0]["counts"]
    assert counts == {"total": 2, "link": 2, "possible": 0, "non_link": 0, "ambiguous": 0}
    assert out["rows"] == rows


def test_ambiguous_is_excluded_and_reported():
    rows = [
        {"sevrHostName": "svr-web-05.a.example", "iPCtnt": "10.0.2.1"},
        {"sevrHostName": "svr-web-05.b.example", "iPCtnt": "10.0.2.2"},
        {"sevrHostName": "svr-web-01", "iPCtnt": "10.0.1.1"},
    ]
    out = _grade(rows, ["svr-web-05", "svr-web-01", "svr-web-77"])
    note = out["dependency_notes"][0]
    assert note["counts"] == {"total": 3, "link": 1, "possible": 0, "non_link": 1, "ambiguous": 1}
    assert [r["sevrHostName"] for r in out["query_results"]] == ["svr-web-01"]
    assert (
        note["removed_rows"] == 2
        and "모호 1(svr-web-05)" in note["detail"]
        and "미발견 1(svr-web-77)" in note["detail"]
    )


def test_outside_scope_rows_removed_and_input_not_mutated():
    rows = [{"hostname": "SVR-WEB-01", "cpu": 1}, {"hostname": "svr-db-99", "cpu": 2}]
    result = {
        "query_results": rows,
        "organized_data": {"summary": "s", "rows": list(rows)},
        "target_db_ids": ["polestar"],
    }
    before = copy.deepcopy(result)
    out = kb.apply_bridge_postcheck(
        kb.BridgeContext(FAMILY_HOSTNAME, "sevrHostName"), _verdict(["svr-web-01"]), result, "t2"
    )
    assert result == before
    assert [r["hostname"] for r in out["query_results"]] == ["SVR-WEB-01"]
    assert [r["hostname"] for r in out["organized_data"]["rows"]] == ["SVR-WEB-01"]
    assert out["dependency_notes"][0]["removed_rows"] == 1


def test_result_column_case_insensitive_and_source_tag_selects_manifest():
    """엔진이 결과 컬럼 대소문자를 바꿔도 찾고, 행 출처 태그가 매니페스트를 고른다."""
    rows = [
        {"HOSTNAME": "svr-web-01", "_source_db": "polestar_b0"},
        {"sevrhostname": "svr-web-02", "_source_db": "itam"},
    ]
    out = _grade(rows, ["svr-web-01", "svr-web-02"], db="polestar_cm_gp")
    assert out["dependency_notes"][0]["counts"]["link"] == 2 and len(out["query_results"]) == 2


def test_unjudgeable_rows_are_kept_with_note():
    rows = [{"서버": "svr-web-01", "cpu": 1}]
    out = _grade(rows, ["svr-web-01"], db="polestar")
    note = out["dependency_notes"][0]
    assert note["reason"] == "bridge_unjudged" and "판정을 하지 못했습니다" in note["detail"]
    assert out["query_results"] == rows and "counts" not in note


def test_empty_result_is_all_non_link():
    out = _grade([], ["svr-web-01", "svr-web-02"], db="polestar")
    assert out["dependency_notes"][0]["counts"] == {
        "total": 2,
        "link": 0,
        "possible": 0,
        "non_link": 2,
        "ambiguous": 0,
    }
    assert out["dependency_notes"][0]["coverage"] == 0.0


def test_no_op_for_error_failed_verdict_or_missing_context():
    ok = _verdict(["svr-web-01"])
    ctx = kb.BridgeContext(FAMILY_HOSTNAME, "hostname")
    err = {"error": "x", "target_db_ids": ["polestar"]}
    assert kb.apply_bridge_postcheck(ctx, ok, err, "t2") is err
    bad = DependencyVerdict(ok=False, reason="prior_empty")
    res = {"query_results": [{"hostname": "a"}], "target_db_ids": ["polestar"]}
    assert kb.apply_bridge_postcheck(ctx, bad, res, "t2") is res
    assert kb.apply_bridge_postcheck(None, ok, res, "t2") is res
    assert kb.apply_bridge_postcheck(ctx, None, res, "t2") is res


@pytest.mark.parametrize(
    "values",
    [
        ["svr-web-01"],
        ["svr-web-01", "svr-web-02", "svr-web-03", "svr-web-04", "svr-web-05", "svr-db-99"],
        ["svr-web-03.synth.example", "svr-web-03", "SVR-WEB-02".casefold()],
    ],
)
def test_grade_sum_equals_source_key_count(values):
    """D5 — 등급 합계 = 선행 키 수 N(침묵 0)."""
    rows = copy.deepcopy(ITAM_FOUR_FORMS) + [
        {"sevrHostName": "svr-web-05.a.example", "iPCtnt": "10.0.2.1"},
        {"sevrHostName": "svr-web-05.b.example", "iPCtnt": "10.0.2.2"},
    ]
    counts = _grade(rows, values)["dependency_notes"][0]["counts"]
    assert (
        counts["link"] + counts["possible"] + counts["non_link"] + counts["ambiguous"]
        == counts["total"]
        == len(values)
    )
