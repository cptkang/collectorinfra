"""식별자 소재 프로브 실행 · 3단 노드 `entity_locator` (plans/102 §3.4 · X-6 · D-224 ⑤).

★ 이 파일이 지키는 계약
  ① 발동 — 데이터 조회 의도 + 명시 식별자(호스트명 계열은 식별 필드 ∧ 값 판정, IP는 값 판정)일 때만.
     식별자 없는 집계형·비데이터 의도·폼필·한 시스템 배포는 조회 0회 + 상태 무변경(`{}`).
  ② 비용 — 시스템(다중 존 시스템은 인가 존)당 고정 조회 1회, **LLM 0회**.
  ③ 인가 존만 조회 · 0건·실패 미캐시(두 번 물으면 두 번 조회).
  ④ 시스템·DB별 개별 try — 한 곳 실패가 다른 곳 판정을 막지 않고, 실패·매니페스트 없음은
     "확인하지 못함"으로 **미발견과 구분**된다.
  ⑤ 매칭은 `grade_matches` — per_ip 행 묶음(모호 아님) · 다중 IP 셀 토큰 확정 ·
     FQDN 단축명(possible).
  ⑥ 출력 — QUERY는 대상 DB를 좁히고, HALT는 `final_response`로 턴을 끝내며, NOTE_PROBE 1건.

DB·LLM·MCP 0(D-127) — 조회는 전부 대역 주입. 실 DB 클라이언트가 불리면 실패한다(autouse 가드).
"""

from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest

from noise_gate.infrastructure.polestar_hostname_resolver import (
    build_host_probe_sql,
    build_host_status_sql,
    probe_hosts,
)
from src.config import AppConfig, MultiDBConfig
from src.domain.entity_key import parse_manifest
from src.domain.host_discovery import (
    ROW_ALL_FOUND,
    ROW_AMBIGUOUS_ONE_FOUND,
    ROW_NONE_FOUND,
    ROW_OWNER_FOUND,
    ROW_OWNER_MISSING_ELSEWHERE,
    ROW_UNVERIFIED,
    SYSTEM_UNVERIFIED,
)
from src.graph import route_after_entity_locator, route_after_semantic_router
from src.orchestration.entity_locator import (
    build_manifest_probe_sql,
    entity_locator,
    extract_identifiers,
    probe_halted,
)
from src.routing.registry import get_registry
from src.utils.prior_dependency import NOTE_PROBE

MON = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]
ASSET = "itam"

# 로컬 하네스 매니페스트와 같은 모양(테스트 전용 — 공용 계층에는 이 리터럴이 없다).
ASSET_MANIFEST = parse_manifest(
    ASSET,
    {
        "entity": "server",
        "table": "TCDMSIF80",
        "row_multiplicity": "per_ip",
        "keys": [
            {"type": "hostname", "column": "sevrHostName", "priority": 1, "compare": "casefold"},
            {"type": "ip", "column": "iPCtnt", "priority": 2, "multi_value": True},
        ],
    },
)


@pytest.fixture(autouse=True)
def _no_real_db():
    """기본 조회 경로(실 DB 클라이언트)가 불리면 즉시 실패시킨다 — 로컬 MCP 9099 보호."""

    def _boom(self, db_id):
        raise AssertionError(f"실 DB 클라이언트 호출 금지: {db_id}")

    with patch("src.routing.db_registry.DBRegistry.get_client", _boom):
        yield


def _config(active=(*MON, ASSET)) -> AppConfig:
    """검증 대상 필드를 명시해 `.env` 누수를 막는다(Known Mistakes)."""
    config = AppConfig()
    config.multi_db = MultiDBConfig(active_db_ids_csv=",".join(active))
    return config


class Stubs:
    """조회 대역 — 호출을 기록하고, 시나리오별 행·실패를 돌려준다."""

    def __init__(
        self,
        *,
        mon_rows=None,
        mon_fail=(),
        asset_rows=None,
        asset_fail=False,
        manifest=ASSET_MANIFEST,
    ):
        self.mon_rows = mon_rows or {}
        self.mon_fail = set(mon_fail)
        self.asset_rows = asset_rows or []
        self.asset_fail = asset_fail
        self.manifest = manifest
        self.host_calls: list[str] = []
        self.sql_calls: list[tuple[str, str]] = []

    async def host_lookup(self, db_id, names, prefixes, ips):
        self.host_calls.append(db_id)
        if db_id in self.mon_fail:
            raise RuntimeError("연결 실패")
        return list(self.mon_rows.get(db_id, []))

    async def sql_runner(self, db_id, sql):
        self.sql_calls.append((db_id, sql))
        if self.asset_fail:
            raise RuntimeError("연결 실패")
        return list(self.asset_rows)

    def loader(self, db_id):
        return self.manifest if db_id == ASSET else None

    def kwargs(self):
        return {
            "host_lookup": self.host_lookup,
            "sql_runner": self.sql_runner,
            "manifest_loader": self.loader,
        }


def _state(*, value="svr-web-01", field="hostname", targets=MON, intent="data_query", **extra):
    state = {
        "user_query": "질의",
        "routing_intent": intent,
        "parsed_requirements": {"filter_conditions": [{"field": field, "op": "=", "value": value}]},
        "target_databases": [
            {"db_id": d, "relevance_score": 0.9, "reason": "라우터"} for d in targets
        ],
        "is_multi_db": len(targets) > 1,
        "active_db_id": targets[0] if targets else None,
        "allowed_db_ids": None,
        "dependency_notes": None,
    }
    state.update(extra)
    return state


def _mon_row(hostname, ip="10.0.1.1", name=None):
    return {"hostname": hostname, "name": name or hostname, "ipaddress": ip}


def _asset_row(host, ip):
    return {"sevrHostName": host, "iPCtnt": ip}


def _label(system):
    return get_registry().system_label(system)


# ──────────────────────────────────────────────
# 명시 식별자
# ──────────────────────────────────────────────


class TestExtractIdentifiers:
    def test_hostname_requires_identity_field(self):
        """★ 값만 보면 `LINUX`도 호스트명 구문이다 — 식별 필드가 아니면 식별자가 아니다."""
        ids = extract_identifiers(
            {
                "filter_conditions": [
                    {"field": "OSType", "op": "=", "value": "LINUX"},
                    {"field": "hostname", "op": "=", "value": "SVR-WEB-02"},
                ]
            }
        )
        assert [k.normalized for k in ids.hostnames] == ["svr-web-02"] and not ids.ips

    def test_ip_is_judged_by_value_regardless_of_field(self):
        ids = extract_identifiers(
            {"filter_conditions": [{"field": "ip_address", "op": "=", "value": "10.0.1.4"}]}
        )
        assert [k.normalized for k in ids.ips] == ["10.0.1.4"] and not ids.hostnames

    def test_fqdn_in_list_and_dedupe(self):
        ids = extract_identifiers(
            {
                "filter_conditions": [
                    {
                        "field": "hostname",
                        "op": "IN",
                        "value": ["svr-web-03.synth.example", "SVR-WEB-03.synth.example"],
                    },
                ]
            }
        )
        assert [k.key_type for k in ids.hostnames] == ["fqdn"]

    @pytest.mark.parametrize(
        "cond",
        [
            {"field": "hostname", "op": "=", "value": "해당 서버"},  # 지시어
            {"field": "hostname", "op": "!=", "value": "svr-web-01"},  # 지목이 아니다
            {"field": "hostname", "op": "LIKE", "value": "svr-web"},  # 부분 일치
            {"field": "cpu_usage", "op": ">=", "value": 80},  # 집계형
            {"field": "hostname", "op": "=", "value": "12345"},  # 숫자만 — 호스트명 아님
        ],
    )
    def test_non_anchors(self, cond):
        assert not extract_identifiers({"filter_conditions": [cond]})


# ──────────────────────────────────────────────
# 발동 게이트 — 조회 0회 · 상태 무변경
# ──────────────────────────────────────────────


class TestTrigger:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "override",
        [
            {
                "parsed_requirements": {
                    "filter_conditions": [{"field": "cpu_usage", "op": ">=", "value": 90}]
                }
            },
            {"parsed_requirements": {}},
            {"routing_intent": "cache_management"},
            {"routing_intent": "general_inference"},
            {"routing_intent": "zone_clarification", "zone_clarification": {"question": "?"}},
            {"template_structure": {"sheets": []}},
        ],
    )
    async def test_no_probe(self, override):
        stubs = Stubs()
        out = await entity_locator({**_state(), **override}, app_config=_config(), **stubs.kwargs())
        assert out == {}
        assert stubs.host_calls == [] and stubs.sql_calls == []

    @pytest.mark.asyncio
    async def test_skip_path_clears_stale_halt_marker(self):
        """델타 병합 — 지난 턴 HALT 표지가 남은 채 이번 턴이 발동하지 않으면 스스로 지운다."""
        stale = {**_state(intent="cache_management"), "entity_probe": {"action": "halt"}}
        out = await entity_locator(stale, app_config=_config(), **Stubs().kwargs())
        assert out == {"entity_probe": None}
        assert not probe_halted({**stale, **out})

    @pytest.mark.asyncio
    async def test_single_system_deployment_does_not_probe(self):
        """고를 시스템이 없다 — 폴스타만 활성인 배포(운영 현행)는 비용 0."""
        stubs = Stubs(mon_rows={"polestar_cm_gp": [_mon_row("svr-web-01")]})
        out = await entity_locator(_state(), app_config=_config(active=MON), **stubs.kwargs())
        assert out == {} and stubs.host_calls == []

    @pytest.mark.asyncio
    async def test_inactive_other_system_is_not_probed(self):
        stubs = Stubs()
        out = await entity_locator(_state(), app_config=_config(active=MON), **stubs.kwargs())
        assert out == {} and stubs.sql_calls == []


# ──────────────────────────────────────────────
# 결정표 행 — 노드 수준
# ──────────────────────────────────────────────


class TestNodeDecisions:
    @pytest.mark.asyncio
    async def test_owner_found_narrows_to_found_zone_one_lookup_per_zone(self):
        stubs = Stubs(mon_rows={"polestar_cm_gp": [_mon_row("svr-web-01")]})
        out = await entity_locator(_state(), app_config=_config(), **stubs.kwargs())

        assert sorted(stubs.host_calls) == sorted(MON), "존당 고정 조회 1회"
        assert stubs.sql_calls == [], "소유 시스템에서 찾았으면 다른 시스템은 보지 않는다(G-5)"
        assert out["entity_probe"]["row"] == ROW_OWNER_FOUND
        assert [t["db_id"] for t in out["target_databases"]] == ["polestar_cm_gp"]
        assert out["is_multi_db"] is False and out["active_db_id"] == "polestar_cm_gp"
        assert out["target_databases"][0]["reason"] == "라우터", "원래 대상 항목을 보존한다"
        assert "final_response" not in out, (
            "HALT가 아니면 최종 응답 키를 싣지 않는다(SSE 완료 오판 방지)"
        )
        notes = [n for n in out["dependency_notes"] if n["kind"] == NOTE_PROBE]
        assert len(notes) == 1 and notes[0]["reason"] == ROW_OWNER_FOUND

    @pytest.mark.asyncio
    async def test_owner_missing_found_elsewhere_halts(self):
        stubs = Stubs(asset_rows=[_asset_row("svr-web-01", "10.0.1.1")])
        state = _state()
        out = await entity_locator(state, app_config=_config(), **stubs.kwargs())

        assert len(stubs.sql_calls) == 1, "다른 시스템도 고정 조회 1회"
        assert out["entity_probe"]["row"] == ROW_OWNER_MISSING_ELSEWHERE
        expected = f"{_label('polestar')}에 등록되지 않은 서버입니다({_label(ASSET)}에는 있음)"
        assert expected in out["final_response"]
        assert out["messages"][0].content == out["final_response"]
        assert out["target_databases"] == [] and out["active_db_id"] is None
        merged = {**state, **out}
        assert probe_halted(merged)
        assert route_after_entity_locator(merged, delegate=route_after_semantic_router) == "__end__"

    @pytest.mark.asyncio
    async def test_asset_owner_missing_in_asset_found_in_monitoring_halts_with_plan_wording(self):
        """계획서 예문 방향 — *"자산관리에 등록되지 않은 서버입니다(폴스타에는 있음)"*."""
        stubs = Stubs(mon_rows={"polestar_b0": [_mon_row("svr-web-01")]})
        out = await entity_locator(_state(targets=[ASSET]), app_config=_config(), **stubs.kwargs())
        assert (
            f"{_label(ASSET)}에 등록되지 않은 서버입니다({_label('polestar')}에는 있음)"
            in out["final_response"]
        )
        assert len(stubs.sql_calls) == 1 and sorted(stubs.host_calls) == sorted(MON)

    @pytest.mark.asyncio
    async def test_both_capabilities_probe_both_and_add_missing_system_target(self):
        stubs = Stubs(
            mon_rows={"polestar_cm_yd": [_mon_row("svr-web-01")]},
            asset_rows=[_asset_row("svr-web-01", "10.0.1.1")],
        )
        state = _state(required_capabilities=["server_usage", "asset_contract"])
        out = await entity_locator(state, app_config=_config(), **stubs.kwargs())

        assert out["entity_probe"]["mode"] == "both" and out["entity_probe"]["row"] == ROW_ALL_FOUND
        assert [t["db_id"] for t in out["target_databases"]] == ["polestar_cm_yd", ASSET]
        added = out["target_databases"][1]
        assert added["reason"] == "소재 프로브로 확인한 DB" and added["user_specified"] is False
        assert out["is_multi_db"] is True

    @pytest.mark.asyncio
    async def test_ambiguous_capability_selects_the_system_that_has_it(self):
        stubs = Stubs(asset_rows=[_asset_row("svr-web-01", "10.0.1.1")])
        state = _state(required_capabilities=["server_spec"])
        out = await entity_locator(state, app_config=_config(), **stubs.kwargs())
        assert out["entity_probe"]["mode"] == "ambiguous"
        assert out["entity_probe"]["row"] == ROW_AMBIGUOUS_ONE_FOUND
        assert [t["db_id"] for t in out["target_databases"]] == [ASSET]

    @pytest.mark.asyncio
    async def test_targets_outside_system_model_are_preserved(self):
        stubs = Stubs(mon_rows={"polestar_cm_gp": [_mon_row("svr-web-01")]})
        state = _state(targets=[*MON, "cloud_portal"])
        out = await entity_locator(
            state, app_config=_config(active=(*MON, ASSET, "cloud_portal")), **stubs.kwargs()
        )
        assert [t["db_id"] for t in out["target_databases"]] == ["polestar_cm_gp", "cloud_portal"]


# ──────────────────────────────────────────────
# 인가 · 캐시 · 실패 분리
# ──────────────────────────────────────────────


class TestSweepRules:
    @pytest.mark.asyncio
    async def test_only_authorized_zones_are_probed(self):
        stubs = Stubs(asset_rows=[])
        state = _state(allowed_db_ids=["polestar_cm_gp", ASSET])
        await entity_locator(state, app_config=_config(), **stubs.kwargs())
        assert stubs.host_calls == ["polestar_cm_gp"], (
            "권한 밖 존을 순회하면 존재 여부가 새어 나간다"
        )

    @pytest.mark.asyncio
    async def test_unauthorized_other_system_is_not_compared(self):
        stubs = Stubs()
        state = _state(allowed_db_ids=list(MON))
        out = await entity_locator(state, app_config=_config(), **stubs.kwargs())
        assert out == {} and stubs.sql_calls == []

    @pytest.mark.asyncio
    async def test_zero_hits_are_not_cached(self):
        stubs = Stubs()
        for _ in range(2):
            out = await entity_locator(_state(), app_config=_config(), **stubs.kwargs())
            assert out["entity_probe"]["row"] == ROW_NONE_FOUND
        assert len(stubs.host_calls) == 2 * len(MON) and len(stubs.sql_calls) == 2

    @pytest.mark.asyncio
    async def test_zone_failure_does_not_stop_other_zones_and_keeps_failed_zone(self):
        stubs = Stubs(
            mon_fail={"polestar_b0"}, mon_rows={"polestar_cm_yd": [_mon_row("svr-web-01")]}
        )
        out = await entity_locator(_state(), app_config=_config(), **stubs.kwargs())
        assert sorted(stubs.host_calls) == sorted(MON)
        probe = out["entity_probe"]["probes"]["polestar"]
        assert probe["errors"] == {"polestar_b0": "RuntimeError"} and probe["found_db_ids"] == [
            "polestar_cm_yd"
        ]
        assert [t["db_id"] for t in out["target_databases"]] == ["polestar_b0", "polestar_cm_yd"]

    @pytest.mark.asyncio
    async def test_missing_manifest_is_unverified_not_absent(self):
        """★ 운영 자산 프로필은 아직 없다(G-4) — 매니페스트 없음은 "미발견"이 아니다."""
        absent = await entity_locator(_state(), app_config=_config(), **Stubs().kwargs())
        no_manifest_stubs = Stubs(manifest=None)
        unknown = await entity_locator(_state(), app_config=_config(), **no_manifest_stubs.kwargs())

        assert absent["entity_probe"]["row"] == ROW_NONE_FOUND
        assert unknown["entity_probe"]["row"] == ROW_UNVERIFIED
        assert unknown["entity_probe"]["probes"][ASSET]["status"] == SYSTEM_UNVERIFIED
        assert unknown["entity_probe"]["probes"][ASSET]["errors"] == {ASSET: "키 매니페스트 없음"}
        assert no_manifest_stubs.sql_calls == [], "매니페스트가 없으면 SQL을 추측해 만들지 않는다"
        assert "키 매니페스트 없음" in unknown["dependency_notes"][-1]["detail"]
        assert "target_databases" not in unknown, "확인하지 못했으면 라우팅을 바꾸지 않는다"

    @pytest.mark.asyncio
    async def test_other_system_failure_never_halts(self):
        stubs = Stubs(asset_fail=True)
        out = await entity_locator(_state(), app_config=_config(), **stubs.kwargs())
        assert out["entity_probe"]["row"] == ROW_UNVERIFIED and "final_response" not in out
        assert out["entity_probe"]["probes"][ASSET]["errors"] == {ASSET: "RuntimeError"}


# ──────────────────────────────────────────────
# 매칭 — per_ip · 다중 IP 셀 · FQDN 단축명
# ──────────────────────────────────────────────


class TestMatching:
    @pytest.mark.asyncio
    async def test_per_ip_rows_are_one_entity_not_ambiguous(self):
        stubs = Stubs(
            asset_rows=[_asset_row("svr-web-04", "10.0.1.4"), _asset_row("svr-web-04", "10.0.2.4")]
        )
        out = await entity_locator(
            _state(value="svr-web-04", targets=[ASSET]), app_config=_config(), **stubs.kwargs()
        )
        grades = out["entity_probe"]["probes"][ASSET]["grades"]["hostname"]
        assert grades == {"link": 1, "possible": 0, "non_link": 0, "ambiguous": 0}

    @pytest.mark.asyncio
    async def test_multi_value_ip_cell_confirmed_by_token(self):
        """SQL LIKE는 `10.0.1.4`로 `10.0.1.40`도 끌어온다 — 코드가 토큰으로 확정한다(X-T6)."""
        stubs = Stubs(
            asset_rows=[
                _asset_row("svr-web-40", "10.0.1.40"),
                _asset_row("svr-web-04", "10.0.2.4, 10.0.1.4"),
            ]
        )
        state = _state(field="ipaddress", value="10.0.1.4", targets=[ASSET])
        out = await entity_locator(state, app_config=_config(), **stubs.kwargs())
        assert out["entity_probe"]["row"] == ROW_OWNER_FOUND
        assert out["entity_probe"]["probes"][ASSET]["grades"]["ip"] == {
            "link": 1,
            "possible": 0,
            "non_link": 0,
            "ambiguous": 0,
        }
        assert "LIKE '%10.0.1.4%'" in stubs.sql_calls[0][1]

    @pytest.mark.asyncio
    async def test_fqdn_in_target_is_possible_match(self):
        stubs = Stubs(asset_rows=[_asset_row("svr-web-03.synth.example", "10.0.1.3")])
        out = await entity_locator(
            _state(value="svr-web-03", targets=[ASSET]), app_config=_config(), **stubs.kwargs()
        )
        assert out["entity_probe"]["row"] == ROW_OWNER_FOUND
        assert out["entity_probe"]["probes"][ASSET]["grades"]["hostname"]["possible"] == 1
        assert "LIKE 'svr-web-03.%'" in stubs.sql_calls[0][1]

    @pytest.mark.asyncio
    async def test_case_difference_is_link(self):
        stubs = Stubs(asset_rows=[_asset_row("SVR-WEB-02", "10.0.1.2")])
        out = await entity_locator(
            _state(value="svr-web-02", targets=[ASSET]), app_config=_config(), **stubs.kwargs()
        )
        assert out["entity_probe"]["probes"][ASSET]["grades"]["hostname"]["link"] == 1

    @pytest.mark.asyncio
    async def test_display_name_with_spaces_is_not_split_into_keys(self):
        """단일값 컬럼(등록명)의 공백을 쪼개면 `web`이 키가 되어 엉뚱한 식별자와 일치한다."""
        stubs = Stubs(
            mon_rows={
                "polestar_cm_gp": [
                    {"hostname": "other-01", "name": "web server", "ipaddress": None}
                ]
            }
        )
        out = await entity_locator(
            _state(value="web", targets=MON), app_config=_config(), **stubs.kwargs()
        )
        assert out["entity_probe"]["probes"]["polestar"]["found_db_ids"] == []


# ──────────────────────────────────────────────
# LLM 0 · 조회 SQL
# ──────────────────────────────────────────────


class TestNoLlm:
    def test_node_signature_has_no_llm(self):
        assert "llm" not in inspect.signature(entity_locator).parameters

    @pytest.mark.asyncio
    async def test_llm_factory_is_never_called(self):
        stubs = Stubs(asset_rows=[_asset_row("svr-web-01", "10.0.1.1")])
        with (
            patch("src.llm.create_llm", side_effect=AssertionError("LLM 호출 금지")) as factory,
            patch(
                "langchain_core.language_models.BaseChatModel.ainvoke",
                side_effect=AssertionError("LLM 호출 금지"),
            ) as ainvoke,
        ):
            await entity_locator(_state(), app_config=_config(), **stubs.kwargs())
        factory.assert_not_called()
        ainvoke.assert_not_called()


class TestProbeSql:
    def test_manifest_sql_shape(self):
        ids = extract_identifiers(
            {
                "filter_conditions": [
                    {
                        "field": "hostname",
                        "op": "IN",
                        "value": ["svr-web-01", "svr-web-03.synth.example"],
                    },
                    {"field": "ip", "op": "=", "value": "10.0.1.4"},
                ]
            }
        )
        sql = build_manifest_probe_sql(ASSET_MANIFEST, table_ref="TCDMSIF80", identifiers=ids)
        assert sql.startswith("SELECT sevrHostName, iPCtnt FROM TCDMSIF80 WHERE ")
        assert (
            "LOWER(sevrHostName) IN ('svr-web-01', 'svr-web-03.synth.example', 'svr-web-03')" in sql
        )
        assert "LOWER(sevrHostName) LIKE 'svr-web-01.%'" in sql
        assert "iPCtnt LIKE '%10.0.1.4%'" in sql

    def test_local_harness_manifest_builds_same_sql(self):
        """트랙 K의 로컬 하네스 매니페스트(실파일)가 이 조립기와 맞물린다 — 대역 모양과 같다."""
        from pathlib import Path

        from src.schema_cache.entity_key_manifest import load_manifest_file

        path = Path(__file__).resolve().parents[2] / "testdata" / "itam" / "entity_keys.local.yaml"
        manifest = load_manifest_file(path, ASSET)
        if manifest is None:
            pytest.skip("로컬 하네스 매니페스트 없음")
        ids = extract_identifiers(
            {"filter_conditions": [{"field": "hostname", "op": "=", "value": "svr-web-01"}]}
        )
        assert build_manifest_probe_sql(
            manifest, table_ref=manifest.table, identifiers=ids
        ) == build_manifest_probe_sql(ASSET_MANIFEST, table_ref="TCDMSIF80", identifiers=ids)

    def test_manifest_sql_rejects_bad_identifier(self):
        bad = parse_manifest(
            ASSET,
            {
                "entity": "server",
                "table": "T",
                "keys": [
                    {"type": "hostname", "column": "x; DROP TABLE T", "priority": 1},
                ],
            },
        )
        ids = extract_identifiers(
            {"filter_conditions": [{"field": "hostname", "op": "=", "value": "svr-web-01"}]}
        )
        with pytest.raises(ValueError):
            build_manifest_probe_sql(bad, table_ref="T", identifiers=ids)

    def test_manifest_without_needed_family_raises(self):
        host_only = parse_manifest(
            ASSET,
            {
                "entity": "server",
                "table": "T",
                "keys": [
                    {"type": "hostname", "column": "h", "priority": 1},
                ],
            },
        )
        ids = extract_identifiers(
            {"filter_conditions": [{"field": "ip", "op": "=", "value": "10.0.1.4"}]}
        )
        with pytest.raises(ValueError):
            build_manifest_probe_sql(host_only, table_ref="T", identifiers=ids)

    @pytest.mark.asyncio
    async def test_unsupported_family_is_unchecked_not_missing(self):
        host_only = parse_manifest(
            ASSET,
            {
                "entity": "server",
                "table": "T",
                "keys": [
                    {"type": "hostname", "column": "h", "priority": 1},
                ],
            },
        )
        stubs = Stubs(manifest=host_only, asset_rows=[])
        state = _state(targets=[ASSET])
        state["parsed_requirements"]["filter_conditions"].append(
            {"field": "ip", "op": "=", "value": "10.0.9.9"}
        )
        out = await entity_locator(state, app_config=_config(), **stubs.kwargs())
        probe = out["entity_probe"]["probes"][ASSET]
        assert probe["unchecked"] == ["10.0.9.9"] and "10.0.9.9" not in probe["missing"]
        assert probe["status"] == SYSTEM_UNVERIFIED

    def test_zone_probe_sql_is_separate_from_shared_lookup_sql(self):
        sql = build_host_probe_sql(
            "polestar_cm_gp",
            names=["svr-web-01"],
            prefixes=["svr-web-01"],
            ips=["10.0.1.1"],
        )
        assert (
            "LOWER(r.hostname) IN ('svr-web-01')" in sql
            and "LOWER(r.name) IN ('svr-web-01')" in sql
        )
        assert (
            "LOWER(r.hostname) LIKE 'svr-web-01.%'" in sql and "r.ipaddress IN ('10.0.1.1')" in sql
        )
        assert "r.dtime IS NULL" in sql and "LIMIT" not in sql
        shared = build_host_status_sql("polestar_cm_gp", ["svr-web-01"])
        assert "LOWER(" not in shared and "ipaddress" not in shared, (
            "세 진입 경로 공용 SQL은 그대로다"
        )

    def test_zone_probe_sql_db2_and_escaping(self):
        sql = build_host_probe_sql(
            "polestar_b0", names=["a'b"], prefixes=[], ips=[], db_engine="db2"
        )
        assert "'a''b'" in sql and "LIMIT" not in sql and "FETCH" not in sql

    def test_zone_probe_sql_requires_candidates(self):
        with pytest.raises(ValueError):
            build_host_probe_sql("polestar_cm_gp", names=[], prefixes=[], ips=[])

    @pytest.mark.asyncio
    async def test_probe_hosts_raises_instead_of_fail_open(self):
        """프로브는 실패를 빈 결과로 바꾸지 않는다 — 호출부가 "확인하지 못함"으로 기록해야 한다."""
        with pytest.raises(LookupError):
            await probe_hosts(
                _config(active=("polestar_cm_gp",)),
                "polestar_b0",
                names=["x1"],
                prefixes=[],
                ips=[],
            )

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute_sql(self, sql):
                raise RuntimeError("DB 연결 실패")

        with patch("src.routing.db_registry.DBRegistry.get_client", lambda self, db_id: _Client()):
            with pytest.raises(RuntimeError):
                await probe_hosts(
                    _config(active=("polestar_cm_gp",)),
                    "polestar_cm_gp",
                    names=["x1"],
                    prefixes=[],
                    ips=[],
                )

    @pytest.mark.asyncio
    async def test_probe_hosts_normalizes_driver_key_case(self):
        class _Result:
            rows = [{"HOSTNAME": "svr-web-01", "NAME": "웹1", "IPADDRESS": "10.0.1.1"}]

        class _Client:
            def __init__(self):
                self.sql = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute_sql(self, sql):
                self.sql = sql
                return _Result()

        client = _Client()
        with patch("src.routing.db_registry.DBRegistry.get_client", lambda self, db_id: client):
            rows = await probe_hosts(
                _config(active=("polestar_b0",)),
                "polestar_b0",
                names=["svr-web-01"],
                prefixes=[],
                ips=[],
            )
        assert rows == [{"hostname": "svr-web-01", "name": "웹1", "ipaddress": "10.0.1.1"}]
        assert client.sql.startswith("SELECT")
