"""plans/102 §4.1.5 처분 — 사용자 권고 B·C·F·G·H·J·K (2026-09-21 승인).

각 권고마다 **바뀐 동작 1건 + 바뀌지 않아야 하는 반대 케이스 1건**을 고정한다. 반대 케이스가
없으면 "판정이 항상 한쪽으로 쏠린" 회귀를 잡지 못한다.

| 권고 | 무엇을 고정하나 | 반대 케이스 |
|---|---|---|
| B | opt-in 실패 run 은 리포트 **최상단 안내** + 제외 건수 | 정상 3단 run 은 안내 0 |
| C | 직접 지정이 정본이 아니면 **교정 없이 사유 노트** | 정본을 지정하면 노트 0 |
| F | 다른 DB(존)의 동명 호스트는 `ambiguous` → 결과 제외 | 한 DB 안의 `per_ip`는 한 엔터티 |
| G | 출처 DB 매니페스트 선언 컬럼이 값·이름 휴리스틱보다 앞선다 | 선언이 없으면 종전 판정 |
| H | 브리지 스코프만 있으면 결정적 컴파일을 건너뛴다 | 종전 스코프가 있으면 컴파일 |
| J | 모호 소유 영역은 **레지스트리 선언**에서 읽는다 | 선언을 지우면 모호 아님 |
| K | 다른 시스템 일치가 보조 컬럼뿐이면 HALT 아님(KEEP) | `hostname` 일치면 종전대로 HALT |

LLM·네트워크·실 DB 0 (D-127).
"""

from __future__ import annotations

import dataclasses
import importlib
from pathlib import Path

import pytest

import src.nodes.key_bridge as kb
import src.orchestration.entity_locator as el
from scripts.scenario import report as rp
from src.config import AppConfig, Text2SQLConfig
from src.domain.entity_key import (
    FAMILY_HOSTNAME,
    FAMILY_IP,
    EntityKeyManifest,
    ManifestKey,
    detect_key_columns,
)
from src.domain.host_discovery import (
    ACTION_HALT,
    ACTION_KEEP,
    ROW_OWNER_MISSING_ELSEWHERE,
    ROW_WEAK_ELSEWHERE,
    ProbePlan,
    SystemProbe,
    decide_systems,
)
from src.routing import capability_ownership as own
from src.routing.registry import get_registry, parse_registry
from src.schema_cache.entity_key_manifest import (
    clear_manifest_cache,
    load_entity_key_manifest,
    load_manifest_file,
)
from src.utils.prior_dependency import NOTE_BRIDGE, NOTE_OWNERSHIP, DependencyVerdict

ROOT = Path(__file__).resolve().parents[2]
ITAM_LOCAL = ROOT / "testdata" / "itam" / "entity_keys.local.yaml"

# 패키지 `__init__`가 동명 함수를 re-export해 모듈을 가린다 — importlib만 모듈을 준다.
qg = importlib.import_module("src.nodes.query_generator")


@pytest.fixture
def itam_manifest(monkeypatch):
    """런타임 로더는 testdata 를 보지 않는다 — 테스트만 로컬 하네스 매니페스트를 주입한다."""
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
# B — 시나리오 리포트 최상단 안내(사다리)
# ──────────────────────────────────────────────


def _summary(**profile) -> dict:
    # 기준 단(D-251 — 2단). 2단 플래그가 켜진 구성에서 1단 opt-in 이 실패하면 2단에 떨어진다.
    base = {"name": "p1", "port": 1, "valid": False, "tier": "intent_orchestration"}
    return {
        "meta": {"run_id": "R", "mode": "live"},
        "profiles": [{**base, **profile}],
        "skipped": [
            {"scenario_id": "s-1", "reason": "프로파일 p1 INVALID: 조용한 강등 …"},
            {"scenario_id": "s-2", "reason": "프로파일 p1 INVALID: 조용한 강등 …"},
        ],
        "invalid": {},
        "groups": {},
    }


class TestOptinFailureNotice:
    def test_optin_failure_is_announced_at_top_with_excluded_count(self, tmp_path):
        """★ 1단 opt-in 이 성립하지 않아 기준 단(2단)에서 잰 run — 최상단 안내 + 못 잰 건수."""
        summary = _summary(degraded_reason="orchestrator_unavailable")
        assert [p["name"] for p in rp.optin_failure_profiles(summary)] == ["p1"]
        assert rp.optin_failure_excluded(summary, rp.optin_failure_profiles(summary)) == 2

        out = rp.render_markdown(summary, tmp_path, None)
        head = out.split("## 1.")[0]
        assert "[안내] 1단(deep_agent) opt-in 이 성립하지 않아" in head
        assert "시나리오 2건을 재지 않았다" in head
        assert "orchestrator_unavailable" in head
        # 기존 「기준 단이 아님」 경고와 섞이지 않는다 — 확정 단은 기준 단이다.
        assert "[경고] 기준 단이 아닌 실행 단으로 측정됐다" not in out

    def test_package_missing_is_the_same_row(self, tmp_path):
        summary = _summary(degraded_reason="package_missing")
        assert rp.optin_failure_profiles(summary)
        assert "package_missing" in rp.render_markdown(summary, tmp_path, None)

    def test_healthy_tier2_run_has_no_notice(self, tmp_path):
        """반대 케이스 — 정상 기준 단(2단) run 에는 이 안내가 없다(경고 상시화 금지)."""
        summary = _summary(valid=True, degraded_reason="intent_flag_on")
        assert rp.optin_failure_profiles(summary) == []
        assert "opt-in 이 성립하지 않아" not in rp.render_markdown(summary, tmp_path, None)

    def test_reason_set_comes_from_the_canonical_source(self):
        """사유 집합의 정본은 `ladder.OPTIN_FAILURE_REASONS`다 — 러너 사본이 아니다(D-053)."""
        from src.observability.ladder import OPTIN_FAILURE_REASONS

        for reason in OPTIN_FAILURE_REASONS:
            assert rp.optin_failure_profiles(_summary(degraded_reason=reason))

    def test_report_path_does_not_pull_the_transport_layer(self):
        """리포트는 산출물만 읽는 경로다 — `server.py`·httpx 를 끌고 오지 않는다.

        모듈 최상단에서 `.server` 를 import 하면 `client.py` 의 httpx 가 딸려 온다.
        판정 함수 안에서 정본을 지연 import 하는 관행(`preflight.py`)을 여기서 고정한다.
        """
        source = (ROOT / "scripts" / "scenario" / "report.py").read_text(encoding="utf-8")
        head = source.split("P95_MIN_SAMPLE", 1)[0]
        assert "from .server import" not in head and "import httpx" not in head


# ──────────────────────────────────────────────
# C — DB 직접 지정 존중 + 사유 표기
# ──────────────────────────────────────────────


def _target(db_id: str, caps: list[str], *, user_specified: bool) -> dict:
    return {
        "db_id": db_id,
        "relevance_score": 0.9,
        "sub_query_context": "q",
        "user_specified": user_specified,
        "capabilities": caps,
    }


class TestUserSpecifiedOwnershipNote:
    ACTIVE = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd", "itam"]

    def test_non_canonical_direct_pick_is_kept_but_reported(self):
        """★ "ITAM DB에서 CPU 사용률" — 교정하지 않고 사유 노트 1건."""
        targets = [_target("itam", ["server_usage"], user_specified=True)]
        out, notes = own.enforce_target_ownership(targets, active_db_ids=self.ACTIVE)

        assert [t["db_id"] for t in out] == ["itam"]          # 교정 없음
        assert [t["capabilities"] for t in out] == [["server_usage"]]
        assert len(notes) == 1
        note = notes[0]
        assert note["kind"] == NOTE_OWNERSHIP
        assert note["reason"] == own.REASON_OWNER_USER_SPECIFIED
        assert note["from_db_ids"] == ["itam"]
        # 교정하지 않았다는 사실이 문구에 드러난다(침묵 금지의 반대편 — 오해 금지).
        assert "교정하지 않고" in note["detail"] and "정본이 아닙니다" in note["detail"]

    def test_canonical_direct_pick_has_no_note(self):
        """반대 케이스 — 지정 DB가 그 영역의 정본이면 알릴 것이 없다."""
        targets = [_target("itam", ["asset_contract"], user_specified=True)]
        out, notes = own.enforce_target_ownership(targets, active_db_ids=self.ACTIVE)
        assert [t["db_id"] for t in out] == ["itam"] and notes == []

    def test_non_user_specified_is_still_corrected(self):
        """반대 케이스 — 직접 지정이 아니면 종전대로 교정한다(노트 사유도 교정)."""
        targets = [_target("itam", ["server_usage"], user_specified=False)]
        out, notes = own.enforce_target_ownership(targets, active_db_ids=self.ACTIVE)
        assert "itam" not in [t["db_id"] for t in out]
        assert [n["reason"] for n in notes] == [own.REASON_OWNER_CORRECTED]

    def test_decompose_task_path_is_symmetric(self):
        """분해 task 경로(검증 지점 ②)도 같은 노트를 낸다 — 한쪽만 고치지 않는다."""
        owner = own.resolve_capability_owner("server_usage", active_db_ids=self.ACTIVE)
        assert owner is not None
        targets = [_target("itam", ["server_usage"], user_specified=True)]
        kept, notes = own.restrict_targets_to_owner(targets, owner, sub_query="q", task_id="t2")
        assert [t["db_id"] for t in kept] == ["itam"]           # 직접 지정은 유지
        assert [n["reason"] for n in notes] == [own.REASON_OWNER_USER_SPECIFIED]
        assert notes[0]["task_id"] == "t2"

    def test_undeclared_db_is_out_of_scope(self):
        """반대 케이스 — 답변 영역 선언이 없는 DB는 판정 밖이라 노트도 없다."""
        targets = [_target("cloud_portal", ["server_usage"], user_specified=True)]
        assert own.user_specified_ownership_notes(targets) == []


# ──────────────────────────────────────────────
# F — 다른 존의 동명 호스트는 ambiguous
# ──────────────────────────────────────────────


def _polestar_row(db_id: str, hostname: str, ip: str) -> dict:
    return {"hostname": hostname, "ipaddress": ip, "_source_db": db_id}


class TestCrossDbHomonym:
    def test_same_hostname_in_two_zones_is_ambiguous_and_excluded(self):
        """★ 같은 호스트명이 두 존(DB)에 있으면 한 엔터티가 아니다 — 모호로 빼고 사유를 남긴다."""
        rows = [
            _polestar_row("polestar_cm_gp", "web01", "10.0.1.1"),
            _polestar_row("polestar_cm_yd", "web01", "10.9.1.1"),
            _polestar_row("polestar_cm_gp", "web02", "10.0.1.2"),
        ]
        out = kb.apply_bridge_postcheck(
            kb.BridgeContext(FAMILY_HOSTNAME, "hostname"),
            _verdict(["web01", "web02"]),
            {"query_results": rows, "target_db_ids": ["polestar_cm_gp", "polestar_cm_yd"]},
            "t2",
        )
        note = out["dependency_notes"][0]
        assert note["counts"] == {
            "total": 2, "link": 1, "possible": 0, "non_link": 0, "ambiguous": 1,
        }
        assert [r["hostname"] for r in out["query_results"]] == ["web02"]
        assert note["cross_db_entities"] == {
            "web01": ["polestar_cm_gp", "polestar_cm_yd"]
        }
        assert "동명 호스트가 여러 DB(존)에 있어 모호 처리" in note["detail"]
        assert "web01" in note["detail"]

    def test_same_db_per_ip_rows_stay_one_entity(self, itam_manifest):
        """반대 케이스 — 같은 DB 안의 IP별 다중 행은 종전대로 한 엔터티다(모호 아님)."""
        rows = [
            {"sevrHostName": "svr-web-04", "iPCtnt": "10.0.1.4", "_source_db": "itam"},
            {"sevrHostName": "svr-web-04", "iPCtnt": "10.0.1.104", "_source_db": "itam"},
        ]
        out = kb.apply_bridge_postcheck(
            kb.BridgeContext(FAMILY_IP, "ip"),
            _verdict(["10.0.1.4", "10.0.1.104"], "ip"),
            {"rows": rows, "target_db_ids": ["itam"]},
            "t2",
        )
        note = out["dependency_notes"][0]
        assert note["counts"]["ambiguous"] == 0 and note["counts"]["link"] == 2
        assert "cross_db_entities" not in note
        assert out["rows"] == rows

    def test_different_hostnames_across_dbs_are_not_ambiguous(self):
        """반대 케이스 — DB 가 달라도 이름이 다르면 정상 link 다(과잉 모호 금지)."""
        rows = [
            _polestar_row("polestar_cm_gp", "web01", "10.0.1.1"),
            _polestar_row("polestar_cm_yd", "web09", "10.9.1.9"),
        ]
        out = kb.apply_bridge_postcheck(
            kb.BridgeContext(FAMILY_HOSTNAME, "hostname"),
            _verdict(["web01", "web09"]),
            {"query_results": rows, "target_db_ids": ["polestar_cm_gp", "polestar_cm_yd"]},
            "t2",
        )
        note = out["dependency_notes"][0]
        assert note["counts"]["link"] == 2 and note["counts"]["ambiguous"] == 0
        assert len(out["query_results"]) == 2


# ──────────────────────────────────────────────
# G — 출처 DB 매니페스트 컬럼 우선
# ──────────────────────────────────────────────

#: 값 판정만으로는 코드값·OS명도 호스트명 계열이다(영문자 든 단일 레이블). `svrNm`은 이름
#: 힌트에도 걸리지 않아 **선언이 없으면** 값이 더 다양한 `osNm`에 밀린다.
DECOY_ROWS = [
    {"osNm": "linux", "zoneCd": "Z99", "svrNm": "svr-web-01", "_source_db": "asset"},
    {"osNm": "windows", "zoneCd": "Z98", "svrNm": "svr-web-01", "_source_db": "asset"},
]

_ASSET_MANIFEST = EntityKeyManifest(
    db_id="asset",
    entity="server",
    table="T_ASSET",
    keys=(ManifestKey(family=FAMILY_HOSTNAME, column="svrNm", priority=1),),
)


@pytest.fixture
def asset_manifest(monkeypatch):
    """출처 DB(`asset`)만 키를 선언한 상태 — 다른 db_id는 선언이 없다."""
    monkeypatch.setattr(
        kb,
        "load_entity_key_manifest",
        lambda db_id, **_k: _ASSET_MANIFEST if db_id == "asset" else None,
    )
    return _ASSET_MANIFEST


class TestDeclaredColumnPriority:
    def test_declared_column_beats_value_heuristic(self, asset_manifest):
        """★ 선언 컬럼이 1순위 — 값이 더 다양한 코드값·OS명 컬럼에 밀리지 않는다."""
        declared = kb.declared_key_columns(DECOY_ROWS)
        assert declared == {"svrnm"}
        chosen = kb.ordered_key_columns(DECOY_ROWS, declared=declared)[0]
        assert chosen.column == "svrNm" and chosen.declared is True

        selection, _found = kb.select_prior_key(DECOY_ROWS, (FAMILY_HOSTNAME,))
        assert selection is not None and selection.column == "svrNm"

    def test_without_declaration_the_heuristic_still_decides(self, monkeypatch):
        """반대 케이스 — 선언이 없으면 종전 값·이름 휴리스틱 그대로(비트 동일 경계)."""
        monkeypatch.setattr(kb, "load_entity_key_manifest", lambda *_a, **_k: None)
        assert kb.declared_key_columns(DECOY_ROWS) == set()
        selection, _found = kb.select_prior_key(DECOY_ROWS, (FAMILY_HOSTNAME,))
        assert selection is not None and selection.column == "osNm"  # 종전 판정(값 수 우선)

    def test_source_db_ids_fallback_when_rows_untagged(self, asset_manifest):
        """출처 태그가 없으면 호출부가 아는 선행 DB 로 선언을 찾는다(권고 G 폴백)."""
        rows = [{k: v for k, v in r.items() if k != "_source_db"} for r in DECOY_ROWS]
        assert kb.declared_key_columns(rows) == set()
        assert kb.declared_key_columns(rows, ["asset"]) == {"svrnm"}
        selection, _ = kb.select_prior_key(rows, (FAMILY_HOSTNAME,), source_db_ids=["asset"])
        assert selection is not None and selection.column == "svrNm"

    def test_domain_sort_key_puts_declared_first(self):
        """도메인 순수 함수 수준 — 선언 > 이름 힌트 > 값 다양성 > 컬럼 순서."""
        rows = [{"osNm": "linux", "hostname": "web01"}, {"osNm": "windows", "hostname": "web01"}]
        # 이름 힌트(hostname)가 있어도 선언이 없으면 hostname 이 강한 키다.
        assert detect_key_columns(rows, name_hint=lambda c: c == "hostname")[0].column == "hostname"
        # 선언이 osNm 이면 osNm 이 먼저다.
        first = detect_key_columns(rows, name_hint=lambda c: c == "hostname", declared=["osNm"])[0]
        assert first.column == "osNm" and first.declared is True


class TestProbeDeclaredColumns:
    """프로브 쪽 대칭 — 존 순회 행의 컬럼 강도를 매니페스트 선언이 정한다."""

    def test_manifest_declaration_marks_name_as_weak(self):
        hosts, ips = el.zoned_key_columns(
            "polestar_cm_gp", lambda db_id: load_entity_key_manifest(db_id)
        )
        assert hosts == (("hostname", False, True), ("name", False, False))
        assert ips == (("ipaddress", False, True),)

    def test_no_manifest_falls_back_to_previous_defaults(self):
        """반대 케이스 — 매니페스트가 없으면 종전 기본값(hostname·ipaddress 가 선언)."""
        hosts, ips = el.zoned_key_columns("nope", lambda _db: None)
        assert hosts == (("hostname", False, True), ("name", False, False))
        assert ips == (("ipaddress", False, True),)

    def test_loader_failure_does_not_weaken_everything(self):
        """로더가 터져도 강도 판정만 기본값으로 — 프로브 전체가 '보조뿐'이 되지 않는다."""

        def _boom(_db):
            raise RuntimeError("loader down")

        hosts, _ = el.zoned_key_columns("polestar_cm_gp", _boom)
        assert hosts[0] == ("hostname", False, True)


# ──────────────────────────────────────────────
# H — 결정적 컴파일 건너뛰기(D-099)
# ──────────────────────────────────────────────


def _qg_cfg(*, bridge: bool) -> AppConfig:
    return AppConfig(
        cross_system_key_bridge_enabled=bridge,
        text2sql=Text2SQLConfig(semantic_compose=True),
    )


class _Ctx:
    """`_try_semantic` 가 읽는 것만 가진 최소 컨텍스트."""

    def __init__(self, cfg: AppConfig, *, bridge_only_scope: bool):
        self.is_retry = False
        self.app_config = cfg
        self.bridge_only_scope = bridge_only_scope
        self.user_query = "q"
        self.limit_value = 100
        self.stat_month = None
        self.prior_scope = None
        self.llm = None


class TestDeterministicCompileSkip:
    ITAM_PRIOR = {"t1": [{"sevrHostName": "svr-web-01", "_source_db": "itam"}]}

    def test_bridge_scope_without_prior_scope_skips_compile(self, monkeypatch):
        """★ 종전 스코프 None + 브리지 스코프 있음 → 컴파일 건너뛰고 일반 경로."""
        state = {"prior_rows": self.ITAM_PRIOR, "active_db_id": "itam"}
        cfg = _qg_cfg(bridge=True)
        monkeypatch.setattr(qg, "plan_target_scope", lambda rows, db: {"values": ["svr-web-01"]})
        assert qg._prior_server_scope(state) is None       # §1.2 실행 재현: 컬럼명으론 못 잡는다
        assert qg._bridge_only_scope(state, cfg) is True

    def test_flag_off_never_skips(self, monkeypatch):
        """반대 케이스 — 브리지 off 면 판정 자체를 하지 않는다(비트 동일)."""
        monkeypatch.setattr(qg, "plan_target_scope", lambda *_a: pytest.fail("off인데 호출됐다"))
        state = {"prior_rows": self.ITAM_PRIOR, "active_db_id": "itam"}
        assert qg._bridge_only_scope(state, _qg_cfg(bridge=False)) is False

    def test_no_bridge_scope_means_no_skip(self, monkeypatch):
        """반대 케이스 — 브리지도 스코프를 못 잡으면 종전대로 컴파일 경로."""
        monkeypatch.setattr(qg, "plan_target_scope", lambda *_a: None)
        state = {"prior_rows": self.ITAM_PRIOR, "active_db_id": "itam"}
        assert qg._bridge_only_scope(state, _qg_cfg(bridge=True)) is False

    async def test_try_semantic_returns_without_compiling(self, monkeypatch):
        """건너뛸 때 컴파일러를 **부르지 않는다** — 커버리지 밖 판정도 아니다."""
        monkeypatch.setattr(
            qg, "compile_from_nl", lambda *_a, **_k: pytest.fail("컴파일러가 불렸다")
        )
        ctx = _Ctx(_qg_cfg(bridge=True), bridge_only_scope=True)
        assert await qg._try_semantic({}, ctx, None, []) == (None, False)

    async def test_try_semantic_still_compiles_otherwise(self, monkeypatch):
        """반대 케이스 — 조건이 아니면 종전대로 컴파일러를 부른다."""
        calls: list[str] = []

        async def _compile(*_a, **_k):
            calls.append("x")
            return "SELECT 1", None, None

        monkeypatch.setattr(qg, "compile_from_nl", _compile)
        ctx = _Ctx(_qg_cfg(bridge=True), bridge_only_scope=False)
        assert await qg._try_semantic({}, ctx, None, []) == ("SELECT 1", False)
        assert calls == ["x"]

    def test_skip_note_is_user_visible_and_merges(self):
        """건너뛴 사유가 노트로 남는다 — 교차 시스템 노트 종류라 응답에 렌더된다."""
        note = kb.compile_skip_note()
        assert note["kind"] == NOTE_BRIDGE
        assert note["reason"] == kb.REASON_BRIDGE_COMPILE_SKIPPED
        assert "결정적 SQL 조립을 건너뛰고" in note["detail"]


# ──────────────────────────────────────────────
# J — 모호 소유 영역을 레지스트리로
# ──────────────────────────────────────────────

_REG_YAML = """
version: 1
capabilities:
  - {code: server_spec, label: 사양, ambiguous_owner: true}
  - {code: server_usage, label: 사용률}
databases: []
"""


class TestAmbiguousOwnerDeclaration:
    def test_registry_declares_ambiguous_capabilities(self):
        """★ 코드 상수가 아니라 레지스트리 선언이 정본이다."""
        assert get_registry().ambiguous_capabilities() == frozenset({"server_spec"})
        assert not hasattr(el, "OWNERSHIP_AMBIGUOUS_CAPABILITIES")

    def test_flag_parses_from_yaml(self, tmp_path):
        path = tmp_path / "r.yaml"
        path.write_text(_REG_YAML, encoding="utf-8")
        reg = parse_registry(__import__("yaml").safe_load(path.read_text(encoding="utf-8")))
        assert reg.ambiguous_capabilities() == frozenset({"server_spec"})
        assert {s.code: s.ambiguous_owner for s in reg.capability_specs()} == {
            "server_spec": True, "server_usage": False,
        }

    def test_required_systems_reads_the_declaration(self):
        """판정이 선언을 따른다 — 선언을 지우면 모호가 아니다(두 번째 출처 없음)."""
        reg = get_registry()
        state = {"required_capabilities": ["server_spec"]}
        assert el.required_systems(state, reg)[1] is True

        plain = dataclasses.replace(
            reg,
            capabilities_=tuple(
                dataclasses.replace(s, ambiguous_owner=False) for s in reg.capability_specs()
            ),
        )
        assert plain.ambiguous_capabilities() == frozenset()
        assert el.required_systems(state, plain)[1] is False

    def test_non_ambiguous_capability_is_unaffected(self):
        reg = get_registry()
        assert el.required_systems({"required_capabilities": ["server_usage"]}, reg)[1] is False


# ──────────────────────────────────────────────
# K — 프로브 HALT 오판 방지 (G-3 연관)
# ──────────────────────────────────────────────

_IDS = ("svr-web-01",)


def _probe(system: str, *, hits: dict, weak: tuple[str, ...] = ()) -> SystemProbe:
    return SystemProbe(
        system=system,
        label=system,
        identifiers=_IDS,
        probed_db_ids=("db1",),
        hits=hits,
        weak_only=weak,
        weak_label="등록명",
    )


_PLAN = ProbePlan(mode="single", required=("asset",), others=("polestar",))


class TestWeakEvidenceDoesNotHalt:
    def test_name_only_match_keeps_instead_of_halting(self):
        """★ 다른 시스템 일치가 등록명뿐이면 ②(HALT)가 아니다 — KEEP + 사유."""
        probes = {
            "asset": _probe("asset", hits={}),
            "polestar": _probe("polestar", hits={"svr-web-01": ("db1",)}, weak=_IDS),
        }
        decision = decide_systems(_PLAN, probes)
        assert decision.action == ACTION_KEEP
        assert decision.row == ROW_WEAK_ELSEWHERE
        assert "등록명 일치만 있어" in decision.message
        assert "조회를 막지 않습니다" in decision.message

    def test_hostname_match_still_halts(self):
        """반대 케이스 — `hostname` 일치가 있으면 종전대로 ② HALT."""
        probes = {
            "asset": _probe("asset", hits={}),
            "polestar": _probe("polestar", hits={"svr-web-01": ("db1",)}),
        }
        decision = decide_systems(_PLAN, probes)
        assert decision.action == ACTION_HALT and decision.row == ROW_OWNER_MISSING_ELSEWHERE

    def test_mixed_evidence_halts_on_the_strong_one(self):
        """일부라도 선언 컬럼으로 맞았으면 반대 증거가 성립한다."""
        probe = _probe(
            "polestar", hits={"svr-web-01": ("db1",), "svr-web-09": ("db1",)}, weak=("svr-web-09",)
        )
        probes = {"asset": _probe("asset", hits={}), "polestar": probe}
        assert probe.strong_hits() == ("svr-web-01",)
        assert decide_systems(_PLAN, probes).action == ACTION_HALT

    def test_weak_only_is_still_found_for_existence(self):
        """보조 일치도 "있다"의 증거로는 남는다 — 상태를 미발견으로 바꾸지 않는다."""
        probe = _probe("polestar", hits={"svr-web-01": ("db1",)}, weak=_IDS)
        assert probe.status == "found" and probe.missing() == ()


class TestProbeMarksWeakHits:
    """존 순회 프로브가 `name`으로만 맞은 식별자를 보조로 표시한다."""

    async def _run(self, rows: list[dict]) -> SystemProbe:
        run = await el.probe_zoned_system(
            "polestar",
            label="폴스타",
            scope=["polestar_cm_gp"],
            identifiers=el.ProbeIdentifiers(
                hostnames=(el.TypedKey(key_type="hostname", normalized="db-main", raw="db-main"),)
            ),
            lookup=lambda *_a: _async(rows),
            db_labels={"polestar_cm_gp": "김포"},
            manifest_loader=load_entity_key_manifest,
        )
        return run.probe

    async def test_name_only_hit_is_weak(self):
        probe = await self._run([{"hostname": "svr-99", "name": "db-main", "ipaddress": None}])
        assert probe.hits == {"db-main": ("polestar_cm_gp",)}
        assert probe.weak_only == ("db-main",) and probe.strong_hits() == ()

    async def test_hostname_hit_is_strong(self):
        """반대 케이스 — `hostname` 으로 맞으면 보조가 아니다."""
        probe = await self._run([{"hostname": "db-main", "name": "DB 메인", "ipaddress": None}])
        assert probe.weak_only == () and probe.strong_hits() == ("db-main",)


async def _async(value):
    return value
