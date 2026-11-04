"""plans/211 W5 — ITAM 서버 원장 키 매니페스트 · 정규형 키 브리지 · 소재 프로브 (D-363 ⑤).

★ 이 파일이 지키는 계약
  ① `parse_manifest`가 `compare: canonical`을 받는다(`casefold|exact|canonical`).
  ② **off 가드(단일 지점)** — `HOST_CANONICAL_MATCH_ENABLED` off면 `canonical` 키를
     가진 매니페스트는 로더가 None으로 돌려준다(ITAM = 매니페스트 없음과 같은 결과).
     `casefold` 매니페스트는 영향 없다.
  ③ on이면 canonical 매니페스트가 읽힌다. ITAM 키는 빌더 경로(`host_compare="canonical"`)로만
     프로필에 들어가므로(D-311 ③ · D-316) 여기서는 임시 프로필 디렉터리 픽스처로 주입한다.
  ④ 키 브리지 — on이면 `web01(업무)`·`web_ap01` 칸도 선행 키로 잡고, canonical 호스트
     키는 `build_canonical_host_condition`(괄호 분기 없음)으로 조건을 만들고, 사후
     판정은 정규형으로 접는다.
  ⑤ 소재 프로브 — 한글 식별자 허용 · canonical 매니페스트 조회 조건 · 정규형 등급
     (존 순회·매니페스트 행).
  ⑥ off 비트 동일 — 폴스타 casefold 조건 문자열 그대로.

SQL 기대값은 `build_canonical_host_condition` 반환값으로 만든다(조립 정본 한 곳 —
§3.4 `REPLACE` 가드 결정 대기). 읽기 전용 가드 통과 단언은 이 Wave에서 넣지 않는다.
DB·LLM·네트워크 0(D-127).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import src.pipeline.key_bridge as kb
import src.schema_cache.entity_key_manifest as ekm
from src.domain.entity_key import (
    FAMILY_HOSTNAME,
    FAMILY_IP,
    KEY_HOSTNAME,
    ROW_MULTIPLICITY_PER_IP,
    ManifestError,
    ManifestKey,
    TypedKey,
    parse_manifest,
    typed_tokens,
)
from src.domain.host_names import build_canonical_host_condition
from src.orchestration import entity_locator as el
from src.utils.prior_dependency import DependencyVerdict

ITAM = "itam"
HOST_COL = "서버호스트명"
IP_COL = "IP주소내용"


def _set_flag(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    """플래그를 명시한다 — `.env` 누수 방지(Known Mistakes). 로더·키 브리지가 같은 함수를 본다."""
    monkeypatch.setattr(
        ekm, "load_config", lambda: SimpleNamespace(host_canonical_match_enabled=enabled)
    )
    ekm.clear_manifest_cache()


@pytest.fixture
def canonical_on(monkeypatch: pytest.MonkeyPatch):
    _set_flag(monkeypatch, True)
    yield
    ekm.clear_manifest_cache()


@pytest.fixture
def canonical_off(monkeypatch: pytest.MonkeyPatch):
    _set_flag(monkeypatch, False)
    yield
    ekm.clear_manifest_cache()


def _itam_manifest_data(compare: str = "canonical") -> dict[str, Any]:
    return {
        "entity": "server",
        "table": "tcdmsif72",
        "row_multiplicity": "per_ip",
        "keys": [
            {"type": "hostname", "column": HOST_COL, "priority": 1, "compare": compare},
            {"type": "ip", "column": IP_COL, "priority": 2, "multi_value": True},
        ],
    }


@pytest.fixture
def itam_profile(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """ITAM 서버 원장 매니페스트를 담은 임시 프로필 디렉터리를 런타임 로더 기본 경로로 둔다."""
    import yaml

    (tmp_path / f"{ITAM}.yaml").write_text(
        yaml.safe_dump({"entity_keys": _itam_manifest_data()}, allow_unicode=True),
        encoding="utf-8",
    )
    monkeypatch.setattr(ekm, "PROFILES_DIR", tmp_path)
    ekm.clear_manifest_cache()
    yield tmp_path
    ekm.clear_manifest_cache()


def _host_key(compare: str, column: str = HOST_COL) -> ManifestKey:
    return ManifestKey(family=FAMILY_HOSTNAME, column=column, priority=1, compare=compare)


def _hk(value: str) -> TypedKey:
    tokens = typed_tokens(value, canonical=True)
    assert len(tokens) == 1 and tokens[0].family == FAMILY_HOSTNAME, value
    return tokens[0]


# ──────────────────────────────────────────────
# ① parse_manifest
# ──────────────────────────────────────────────


def test_parse_manifest_accepts_canonical_compare():
    manifest = parse_manifest(ITAM, _itam_manifest_data())
    host = manifest.key_for(FAMILY_HOSTNAME)
    assert host is not None and host.compare == "canonical"


def test_parse_manifest_rejects_unknown_compare_with_new_vocabulary():
    with pytest.raises(ManifestError, match="casefold\\|exact\\|canonical"):
        parse_manifest(ITAM, _itam_manifest_data(compare="fuzzy"))


def test_parse_manifest_casefold_unchanged():
    manifest = parse_manifest(ITAM, _itam_manifest_data(compare="casefold"))
    host = manifest.key_for(FAMILY_HOSTNAME)
    assert host is not None and host.compare == "casefold"


# ──────────────────────────────────────────────
# ② off 가드 · ③ on 로드
# ──────────────────────────────────────────────


def test_off_itam_manifest_is_absent(canonical_off, itam_profile):
    assert ekm.load_entity_key_manifest(ITAM) is None


def test_off_guard_only_drops_canonical_manifests(canonical_off, tmp_path):
    import yaml

    (tmp_path / "canon.yaml").write_text(
        yaml.safe_dump({"entity_keys": _itam_manifest_data()}, allow_unicode=True),
        encoding="utf-8",
    )
    (tmp_path / "plain.yaml").write_text(
        yaml.safe_dump({"entity_keys": _itam_manifest_data("casefold")}, allow_unicode=True),
        encoding="utf-8",
    )
    assert ekm.load_entity_key_manifest("canon", profiles_dir=tmp_path) is None
    assert ekm.load_entity_key_manifest("plain", profiles_dir=tmp_path) is not None
    # 폴스타 정본 매니페스트(casefold)는 off에서도 그대로다.
    polestar = ekm.load_entity_key_manifest("polestar_cm_gp")
    assert polestar is not None and polestar.key_for(FAMILY_HOSTNAME).compare == "casefold"


def test_off_guard_logs_once_per_db(canonical_off, itam_profile, caplog):
    with caplog.at_level("INFO", logger=ekm.__name__):
        ekm.load_entity_key_manifest(ITAM)
        ekm.load_entity_key_manifest(ITAM)
    hits = [r for r in caplog.records if "미적용" in r.getMessage() and ITAM in r.getMessage()]
    assert len(hits) == 1


def test_on_itam_canonical_manifest_loads(canonical_on, itam_profile):
    manifest = ekm.load_entity_key_manifest(ITAM)
    assert manifest is not None
    assert (manifest.entity, manifest.table) == ("server", "tcdmsif72")
    assert manifest.row_multiplicity == ROW_MULTIPLICITY_PER_IP
    host, ip = manifest.keys_by_priority()
    assert (host.family, host.column, host.priority, host.compare) == (
        FAMILY_HOSTNAME, HOST_COL, 1, "canonical",
    )
    assert (ip.family, ip.column, ip.priority, ip.multi_value) == (FAMILY_IP, IP_COL, 2, True)
    assert str(itam_profile) in manifest.source


def test_host_canonical_enabled_requires_true_identity():
    """설정 대역(MagicMock)·truthy 값이 플래그를 켜지 않는다(`is True`)."""
    from unittest.mock import MagicMock

    assert ekm.host_canonical_enabled(MagicMock()) is False
    assert ekm.host_canonical_enabled(SimpleNamespace(host_canonical_match_enabled=1)) is False
    assert ekm.host_canonical_enabled(SimpleNamespace(host_canonical_match_enabled=True)) is True


# ──────────────────────────────────────────────
# ④ 키 브리지 — 선택 · 조건 · 사후 판정
# ──────────────────────────────────────────────


def test_select_prior_key_on_accepts_business_suffix_and_underscore(canonical_on):
    rows = [{"서버명": "web01(인터넷뱅킹)"}, {"서버명": "web_ap01"}]
    selection, _ = kb.select_prior_key(rows, (FAMILY_HOSTNAME,))
    assert selection is not None
    assert selection.normalized == ["web01", "web_ap01"]


def test_select_prior_key_off_is_unchanged(canonical_off):
    rows = [{"서버명": "web01(인터넷뱅킹)"}, {"서버명": "web_ap01"}]
    selection, found = kb.select_prior_key(rows, (FAMILY_HOSTNAME,))
    assert selection is None and found == []


def test_scope_condition_canonical_single_label():
    keys = [_hk("web-ap01")]
    cond = kb.build_scope_condition(_host_key("canonical"), keys)
    assert cond == build_canonical_host_condition(HOST_COL, ["web-ap01"], business_suffix=False)
    assert "(%" not in cond, "ITAM SQL에는 괄호 업무명 분기가 없다(D-363 부기 1)"


def test_scope_condition_canonical_fqdn_and_multi():
    keys = [_hk("WEB-AP01.corp.local"), _hk("web_db02")]
    cond = kb.build_scope_condition(_host_key("canonical"), keys)
    assert cond == build_canonical_host_condition(
        HOST_COL, ["web-ap01.corp.local", "web_db02"], business_suffix=False
    )
    assert "'webap01.corp.local'" in cond and "'webdb02'" in cond
    assert "(%" not in cond


def test_scope_condition_canonical_without_valid_key_falls_back_to_casefold():
    """정규형이 빈 값이면 L0 조건을 만들지 않고 현행 casefold 조건을 둔다(기능 축소 방지)."""
    odd = TypedKey(key_type=KEY_HOSTNAME, normalized="web%01", raw="web%01")
    assert kb.build_scope_condition(_host_key("canonical"), [odd]) == kb.build_scope_condition(
        _host_key("casefold"), [odd]
    )


@pytest.mark.parametrize("flag", [False, True])
def test_scope_condition_polestar_casefold_is_bit_identical(monkeypatch, flag):
    _set_flag(monkeypatch, flag)
    key = _host_key("casefold", column="hostname")
    keys = typed_tokens("web01") + typed_tokens("svr-db-02.corp.local")
    assert kb.build_scope_condition(key, keys) == (
        "(LOWER(hostname) IN ('web01', 'svr-db-02.corp.local', 'svr-db-02') "
        "OR LOWER(hostname) LIKE 'web01.%')"
    )
    ekm.clear_manifest_cache()


def test_scope_condition_casefold_underscore_value_has_no_like(canonical_on):
    """on — `_`가 든 선행 값은 LIKE 와일드카드가 되므로 IN에만 둔다(D-363 ④)."""
    key = _host_key("casefold", column="hostname")
    keys = [_hk("web_ap01"), _hk("web01")]
    assert kb.build_scope_condition(key, keys) == (
        "(LOWER(hostname) IN ('web_ap01', 'web01') OR LOWER(hostname) LIKE 'web01.%')"
    )


def test_scope_condition_casefold_underscore_only_is_in_only(canonical_on):
    key = _host_key("casefold", column="hostname")
    cond = kb.build_scope_condition(key, [_hk("web_ap01(인터넷뱅킹)")])
    assert cond == "LOWER(hostname) IN ('web_ap01')"
    assert "LIKE" not in cond


def test_plan_target_scope_itam_on_uses_canonical_condition(canonical_on, itam_profile):
    prior = {"t1": [{"hostname": "web_ap01(인터넷뱅킹)"}]}
    scope = kb.plan_target_scope(prior, ITAM)
    assert scope is not None
    assert (scope["target_table"], scope["target_col"]) == ("tcdmsif72", HOST_COL)
    assert scope["condition"] == build_canonical_host_condition(
        HOST_COL, ["web_ap01"], business_suffix=False
    )


def test_plan_target_scope_itam_off_has_no_target(canonical_off, itam_profile):
    """off — ITAM 매니페스트가 없으므로 대상 컬럼 미확정 스코프(종전)."""
    scope = kb.plan_target_scope({"t1": [{"hostname": "web-ap01"}]}, ITAM)
    assert scope is not None and "condition" not in scope and "target_col" not in scope


def _postcheck(values: list[str], rows: list[dict[str, Any]]) -> dict[str, Any]:
    ctx = kb.BridgeContext(family=FAMILY_HOSTNAME, source_col="hostname")
    verdict = DependencyVerdict(
        ok=True, scope_col="hostname", scope_values=values, scope_size=len(values)
    )
    result = {"query_results": rows, "target_db_ids": [ITAM]}
    return kb.apply_bridge_postcheck(ctx, verdict, result, "t2")


def _counts(out: dict[str, Any]) -> dict[str, int]:
    note = out["dependency_notes"][-1]
    assert note["reason"] == kb.REASON_BRIDGE_MATCH
    return note["counts"]


def test_postcheck_canonical_dash_vs_underscore_links(canonical_on, itam_profile):
    out = _postcheck(["web-ap01"], [{HOST_COL: "web_ap01", IP_COL: "10.0.0.1"}])
    assert _counts(out)["link"] == 1
    assert len(out["query_results"]) == 1


def test_postcheck_canonical_per_ip_rows_are_one_entity(canonical_on, itam_profile):
    rows = [
        {HOST_COL: "web-ap01", IP_COL: "10.0.0.1"},
        {HOST_COL: "WEB_AP01", IP_COL: "10.0.0.2"},
    ]
    out = _postcheck(["web-ap01"], rows)
    counts = _counts(out)
    assert (counts["link"], counts["ambiguous"]) == (1, 0)
    assert len(out["query_results"]) == 2


def test_postcheck_canonical_other_domain_fqdn_is_non_link(canonical_on, itam_profile):
    out = _postcheck(["webap01.a.local"], [{HOST_COL: "webap01.b.local", IP_COL: "10.0.0.1"}])
    counts = _counts(out)
    assert (counts["link"], counts["possible"], counts["non_link"]) == (0, 0, 1)
    assert out["query_results"] == []


def test_postcheck_canonical_two_entities_are_ambiguous(canonical_on, itam_profile):
    rows = [
        {HOST_COL: "web-ap01.a.local", IP_COL: "10.0.0.1"},
        {HOST_COL: "web_ap01.b.local", IP_COL: "10.0.0.2"},
    ]
    out = _postcheck(["webap01"], rows)
    counts = _counts(out)
    assert (counts["link"], counts["possible"], counts["ambiguous"]) == (0, 0, 1)
    assert out["query_results"] == []


def test_postcheck_off_itam_has_no_manifest_and_keeps_rows(canonical_off, itam_profile):
    """off — ITAM 매니페스트가 없어 판정 불가(행 유지 · 종전 사유)."""
    rows = [{HOST_COL: "web_ap01", IP_COL: "10.0.0.1"}]
    out = _postcheck(["web-ap01"], rows)
    assert out["dependency_notes"][-1]["reason"] == kb.REASON_BRIDGE_UNJUDGED
    assert out["query_results"] == rows


# ──────────────────────────────────────────────
# ⑤ 소재 프로브
# ──────────────────────────────────────────────


def test_identifier_accepts_hangul_column():
    assert el._identifier(HOST_COL) == HOST_COL
    assert el._identifier(IP_COL) == IP_COL
    for bad in ("x; DROP TABLE T", "서버 호스트명", "1col", "a-b"):
        with pytest.raises(ValueError):
            el._identifier(bad)


def test_manifest_probe_sql_hangul_ip_only():
    """IP만 지목하면 호스트 조건 없이 한글 컬럼 비인용 SELECT가 조립된다(종전엔 식별자 오류)."""
    manifest = parse_manifest(ITAM, _itam_manifest_data())
    ids = el.extract_identifiers(
        {"filter_conditions": [{"field": "ip", "op": "=", "value": "10.1.2.3"}]}
    )
    sql = el.build_manifest_probe_sql(manifest, table_ref="tcdmsif72", identifiers=ids)
    assert sql == (
        f"SELECT {HOST_COL}, {IP_COL} FROM tcdmsif72 WHERE {IP_COL} LIKE '%10.1.2.3%'"
    )


def test_manifest_host_condition_canonical():
    key = _host_key("canonical")
    hosts = (_hk("web-ap01"), _hk("WEBAP01.corp.local"))
    assert el._host_conditions(key, hosts) == [
        build_canonical_host_condition(
            HOST_COL, ["web-ap01", "webap01.corp.local"], business_suffix=False
        )
    ]


def test_manifest_host_condition_casefold_unchanged():
    key = _host_key("casefold", column="sevrHostName")
    hosts = (_hk("svr-web-01"),)
    assert el._host_conditions(key, hosts) == [
        "LOWER(sevrHostName) IN ('svr-web-01')",
        "LOWER(sevrHostName) LIKE 'svr-web-01.%'",
    ]


def _hostname_filter(value: str) -> dict[str, Any]:
    return {"filter_conditions": [{"field": "hostname", "op": "=", "value": value}]}


def test_extract_identifiers_on_accepts_suffix_and_underscore():
    on = el.extract_identifiers(_hostname_filter("web_ap01(인터넷뱅킹)"), canonical=True)
    assert [k.normalized for k in on.hostnames] == ["web_ap01"]
    off = el.extract_identifiers(_hostname_filter("web_ap01(인터넷뱅킹)"))
    assert not off.hostnames


def test_extract_identifiers_on_dedupes_by_canonical_key():
    parsed = {
        "filter_conditions": [
            {"field": "hostname", "op": "in", "value": ["web-ap01", "WEB_AP01"]},
        ]
    }
    assert len(el.extract_identifiers(parsed, canonical=True).hostnames) == 1
    assert len(el.extract_identifiers(parsed).hostnames) == 1  # off: `WEB_AP01`은 키가 아니다


def test_manifest_rows_graded_by_canonical_key():
    ids = el.extract_identifiers(_hostname_filter("web-ap01"), canonical=True)
    entities = el._Entities.empty(canonical=True)
    entities.add_rows(
        [{HOST_COL: "WEB_AP01", IP_COL: "10.0.0.1"}, {HOST_COL: "web-ap01", IP_COL: "10.0.0.2"}],
        db_id=ITAM,
        host_columns=((HOST_COL, False, True),),
        ip_columns=((IP_COL, True, True),),
        per_ip=True,
    )
    hits, grades, weak = entities.grade(ids)
    assert hits == {"web-ap01": (ITAM,)}
    assert grades[FAMILY_HOSTNAME] == {"link": 1, "possible": 0, "non_link": 0, "ambiguous": 0}
    assert weak == ()


def test_manifest_rows_off_do_not_match_underscore():
    ids = el.extract_identifiers(_hostname_filter("web-ap01"))
    entities = el._Entities.empty()
    entities.add_rows(
        [{HOST_COL: "web_ap01", IP_COL: "10.0.0.1"}],
        db_id=ITAM,
        host_columns=((HOST_COL, False, True),),
        ip_columns=((IP_COL, True, True),),
        per_ip=True,
    )
    hits, grades, _ = entities.grade(ids)
    assert hits == {} and grades[FAMILY_HOSTNAME]["non_link"] == 1


async def _zoned(canonical: bool, rows: list[dict[str, Any]], value: str) -> el.ProbeRun:
    async def lookup(db_id, names, prefixes, ips):
        return rows

    ids = el.extract_identifiers(_hostname_filter(value), canonical=canonical)
    return await el.probe_zoned_system(
        "polestar",
        label="모니터링",
        scope=["polestar_cm_gp"],
        identifiers=ids,
        lookup=lookup,
        db_labels={"polestar_cm_gp": "공동존"},
        canonical=canonical,
    )


@pytest.mark.asyncio
async def test_zoned_rows_on_match_business_suffix_and_underscore():
    rows = [{"hostname": "WEB_AP01(인터넷뱅킹)", "name": "x", "ipaddress": "10.0.0.1"}]
    run = await _zoned(True, rows, "web-ap01")
    assert run.probe.hits == {"web-ap01": ("polestar_cm_gp",)}
    assert run.grades[FAMILY_HOSTNAME]["link"] == 1


@pytest.mark.asyncio
async def test_zoned_rows_off_are_unchanged():
    rows = [{"hostname": "WEB_AP01(인터넷뱅킹)", "name": "x", "ipaddress": "10.0.0.1"}]
    run = await _zoned(False, rows, "web-ap01")
    assert run.probe.hits == {}
    assert run.grades[FAMILY_HOSTNAME]["non_link"] == 1


@pytest.mark.asyncio
async def test_node_reads_flag_from_app_config(monkeypatch):
    """노드는 `app_config.host_canonical_match_enabled`(is True)로 식별자 판정을 바꾼다."""
    seen: list[bool] = []
    real = el.extract_identifiers

    def spy(parsed, *, canonical=False):
        seen.append(canonical)
        return real(parsed, canonical=canonical)

    monkeypatch.setattr(el, "extract_identifiers", spy)
    state = {
        "routing_intent": "data_query",
        "parsed_requirements": {"filter_conditions": []},
    }
    for flag in (True, False):
        cfg = SimpleNamespace(host_canonical_match_enabled=flag)
        assert await el.entity_locator(state, app_config=cfg) == {}  # type: ignore[arg-type]
    assert seen == [True, False]


# ──────────────────────────────────────────────
# ⑦ 빌더 경로 — `entity_keys_asset(host_compare=…)` (D-311 ③ · D-316)
# ──────────────────────────────────────────────

_ENTITY = {
    "srv": {
        "hostname": {"column": "host_name", "ratio": 1.0, "multi_value": False},
        "ip": {"column": "ip_addr", "ratio": 1.0, "multi_value": True},
    }
}


def test_entity_keys_asset_default_is_casefold():
    from src.domain import schema_inference as inf

    out = inf.entity_keys_asset(_ENTITY, {"srv": 5})
    assert out is not None and out["keys"][0]["compare"] == "casefold"


def test_entity_keys_asset_canonical_parses_as_canonical_manifest():
    from src.domain import schema_inference as inf

    out = inf.entity_keys_asset(_ENTITY, {"srv": 5}, host_compare="canonical")
    assert out == {
        "entity": "server", "table": "srv",
        "keys": [
            {"type": "hostname", "column": "host_name", "priority": 1, "compare": "canonical"},
            {"type": "ip", "column": "ip_addr", "priority": 2, "multi_value": True},
        ],
    }
    manifest = parse_manifest(ITAM, out)
    assert manifest.key_for(FAMILY_HOSTNAME).compare == "canonical"


def test_entity_keys_asset_rejects_unknown_host_compare():
    from src.domain import schema_inference as inf

    with pytest.raises(ValueError, match="casefold\\|canonical"):
        inf.entity_keys_asset(_ENTITY, {"srv": 5}, host_compare="exact")
