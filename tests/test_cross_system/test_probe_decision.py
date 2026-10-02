"""식별자 소재 판정 — (시스템, 존) 축 결정표 (plans/102 §3.4 · X-5 · D-224 ⑤).

★ 이 파일이 지키는 계약
  ① 결정표 7행이 각각 재현된다(행 코드·동작·조회 대상).
  ② **"없다"(NOT_FOUND)와 "확인하지 못했다"(UNVERIFIED)를 합치지 않는다** — 같은 입력에서 실패만
     바꾸면 동작·행·문구가 달라진다. 특히 소유 시스템이 "없음"일 때만 사유 노출(HALT)이 나고,
     다른 시스템이 "확인 실패"면 HALT하지 않는다.
  ③ 순수 — I/O·LLM 0, 시스템 이름·라벨은 인자로만 받는다.

DB·LLM 0(D-127).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.domain.host_discovery import (
    ACTION_HALT,
    ACTION_KEEP,
    ACTION_QUERY,
    AMBIGUOUS,
    MODE_AMBIGUOUS,
    MODE_BOTH,
    MODE_SINGLE,
    NOT_FOUND,
    RESOLVED,
    ROW_ALL_FOUND,
    ROW_AMBIGUOUS_BOTH_FOUND,
    ROW_AMBIGUOUS_ONE_FOUND,
    ROW_NONE_FOUND,
    ROW_OWNER_FOUND,
    ROW_OWNER_MISSING_ELSEWHERE,
    ROW_PARTIAL_FOUND,
    ROW_UNVERIFIED,
    SYSTEM_FOUND,
    SYSTEM_NOT_FOUND,
    SYSTEM_UNVERIFIED,
    ProbePlan,
    SystemProbe,
    classify_systems,
    decide_systems,
    pending_systems,
    plan_probe,
    system_trace_payload,
)

IDS = ("svr-web-01",)
MON_DBS = ("zone_a", "zone_b", "zone_c")


def mon(*, found=(), failed=(), ids=IDS, probed=MON_DBS, label="모니터링"):
    """다중 존 시스템 확인 결과 대역 — `found`는 발견 DB, `failed`는 실패 DB."""
    return SystemProbe(
        system="mon",
        label=label,
        identifiers=ids,
        probed_db_ids=probed,
        hits={i: tuple(found) for i in ids} if found else {},
        errors={d: "RuntimeError" for d in failed},
        db_labels={"zone_a": "존A", "zone_b": "존B", "zone_c": "존C"},
    )


def asset(*, found=False, error=None, ids=IDS, label="자산관리"):
    """단일 DB 시스템 확인 결과 대역."""
    return SystemProbe(
        system="asset",
        label=label,
        identifiers=ids,
        probed_db_ids=("asset_db",),
        hits={i: ("asset_db",) for i in ids} if found else {},
        errors={"asset_db": error} if error else {},
    )


SINGLE_ASSET = ProbePlan(mode=MODE_SINGLE, required=("asset",), others=("mon",))
SINGLE_MON = ProbePlan(mode=MODE_SINGLE, required=("mon",), others=("asset",))
BOTH = ProbePlan(mode=MODE_BOTH, required=("mon", "asset"))
AMBIG = ProbePlan(mode=MODE_AMBIGUOUS, required=("mon",), others=("asset",))


# ──────────────────────────────────────────────
# SystemProbe — 상태 3분
# ──────────────────────────────────────────────


class TestSystemProbeStatus:
    def test_found_when_any_hit(self):
        assert mon(found=("zone_b",)).status == SYSTEM_FOUND

    def test_not_found_only_when_every_probed_db_answered(self):
        assert mon().status == SYSTEM_NOT_FOUND

    def test_failure_without_hit_is_unverified_not_absent(self):
        """★ 한 존이라도 실패했는데 히트가 없으면 "없다"라고 말할 수 없다."""
        assert mon(failed=("zone_a",)).status == SYSTEM_UNVERIFIED

    def test_zero_probed_db_is_unverified(self):
        """확인한 DB가 0개 = 확인한 것이 없다 — "없음"이 아니다."""
        probe = SystemProbe(system="asset", label="자산관리", identifiers=IDS)
        assert probe.status == SYSTEM_UNVERIFIED
        assert "조회 가능한 DB 없음" in probe.failure_text()

    def test_partial_zone_failure_with_hit_keeps_failed_zone_in_scope(self):
        probe = mon(found=("zone_b",), failed=("zone_a",))
        assert probe.status == SYSTEM_FOUND
        assert probe.found_db_ids() == ("zone_b",)
        assert probe.scope_db_ids() == ("zone_a", "zone_b"), (
            "실패한 존을 빼면 '거기엔 없다'고 판정한 셈이다"
        )

    def test_unchecked_identifier_is_not_counted_as_missing(self):
        probe = SystemProbe(
            system="asset",
            label="자산관리",
            identifiers=("svr-web-01", "10.9.9.9"),
            probed_db_ids=("asset_db",),
            unchecked=("10.9.9.9",),
            errors={"": "키 매니페스트가 받지 않는 식별자: 10.9.9.9"},
        )
        assert probe.missing() == ("svr-web-01",)
        assert probe.status == SYSTEM_UNVERIFIED


# ──────────────────────────────────────────────
# plan_probe · pending_systems — 발동 조건(G-5)
# ──────────────────────────────────────────────


class TestPlan:
    def test_two_required_systems_is_both(self):
        plan = plan_probe(["mon", "asset"], ambiguous=False, available_systems=["mon", "asset"])
        assert plan == ProbePlan(mode=MODE_BOTH, required=("mon", "asset"))

    def test_single_owner_compares_with_other_available_systems(self):
        plan = plan_probe(["asset"], ambiguous=False, available_systems=["mon", "asset"])
        assert plan == ProbePlan(mode=MODE_SINGLE, required=("asset",), others=("mon",))

    def test_ambiguous_capability(self):
        plan = plan_probe(["mon"], ambiguous=True, available_systems=["mon", "asset"])
        assert plan == ProbePlan(mode=MODE_AMBIGUOUS, required=("mon",), others=("asset",))

    def test_no_other_system_means_no_probe(self):
        """한 시스템만 있는 배포에는 고를 시스템이 없다 — 프로브하지 않는다."""
        assert plan_probe(["mon"], ambiguous=False, available_systems=["mon"]) is None
        assert plan_probe(["mon"], ambiguous=True, available_systems=["mon"]) is None

    def test_nothing_required_means_no_probe(self):
        assert plan_probe([], ambiguous=False, available_systems=["mon", "asset"]) is None

    def test_single_probes_owner_first_then_others_only_when_absent(self):
        assert pending_systems(SINGLE_MON, {}) == ("mon",)
        assert pending_systems(SINGLE_MON, {"mon": mon(found=("zone_a",))}) == ()
        assert pending_systems(SINGLE_MON, {"mon": mon()}) == ("asset",)
        assert pending_systems(SINGLE_MON, {"mon": mon(), "asset": asset()}) == ()

    def test_single_does_not_look_elsewhere_when_owner_unverified(self):
        """소유 시스템을 확인하지 못했으면 "소유 시스템에서 못 찾음"(b)이 성립하지 않는다."""
        assert pending_systems(SINGLE_MON, {"mon": mon(failed=MON_DBS)}) == ()

    def test_both_and_ambiguous_probe_everything_at_once(self):
        assert pending_systems(BOTH, {}) == ("mon", "asset")
        assert pending_systems(AMBIG, {}) == ("mon", "asset")
        assert pending_systems(AMBIG, {"mon": mon()}) == ("asset",)


# ──────────────────────────────────────────────
# 결정표 7행 (§3.4)
# ──────────────────────────────────────────────


class TestDecisionTable:
    def test_row1_owner_found_queries_owner_only(self):
        d = decide_systems(SINGLE_MON, {"mon": mon(found=("zone_b",))})
        assert (d.action, d.row) == (ACTION_QUERY, ROW_OWNER_FOUND)
        assert d.systems == ("mon",) and d.db_ids == ("zone_b",), "프로브 결과를 스코프로"

    def test_row2_owner_missing_elsewhere_found_halts_with_reason(self):
        d = decide_systems(SINGLE_ASSET, {"asset": asset(), "mon": mon(found=("zone_a",))})
        assert (d.action, d.row) == (ACTION_HALT, ROW_OWNER_MISSING_ELSEWHERE)
        assert d.systems == () and d.db_ids == ()
        assert "자산관리에 등록되지 않은 서버입니다(모니터링에는 있음)" in d.message
        assert "svr-web-01" in d.message

    def test_row3_both_required_both_found(self):
        d = decide_systems(BOTH, {"mon": mon(found=("zone_c",)), "asset": asset(found=True)})
        assert (d.action, d.row) == (ACTION_QUERY, ROW_ALL_FOUND)
        assert d.systems == ("mon", "asset") and d.db_ids == ("zone_c", "asset_db")

    def test_row4_both_required_one_found_queries_found_side_with_reason(self):
        d = decide_systems(BOTH, {"mon": mon(found=("zone_a",)), "asset": asset()})
        assert (d.action, d.row) == (ACTION_QUERY, ROW_PARTIAL_FOUND)
        assert d.systems == ("mon",) and d.db_ids == ("zone_a",)
        assert "자산관리에 등록되지 않은 서버입니다(모니터링에는 있음)" in d.message

    def test_row5_ambiguous_one_found_selects_found_system(self):
        d = decide_systems(AMBIG, {"mon": mon(), "asset": asset(found=True)})
        assert (d.action, d.row) == (ACTION_QUERY, ROW_AMBIGUOUS_ONE_FOUND)
        assert d.systems == ("asset",) and d.db_ids == ("asset_db",)
        assert "모니터링에는 없고 자산관리에서 확인돼" in d.message, "선택 근거 노출"

        d2 = decide_systems(AMBIG, {"mon": mon(found=("zone_a",)), "asset": asset()})
        assert (d2.action, d2.row, d2.systems) == (ACTION_QUERY, ROW_AMBIGUOUS_ONE_FOUND, ("mon",))

    def test_row6_ambiguous_both_found_selects_canonical_and_mentions_other(self):
        d = decide_systems(AMBIG, {"mon": mon(found=("zone_a",)), "asset": asset(found=True)})
        assert (d.action, d.row) == (ACTION_QUERY, ROW_AMBIGUOUS_BOTH_FOUND)
        assert d.systems == ("mon",)
        assert "자산관리에도 있음" in d.message

    @pytest.mark.parametrize(
        "plan, probes, action",
        [
            # 소유 시스템 확인 실패 → 라우팅 유지 + 사유
            (SINGLE_MON, {"mon": mon(failed=MON_DBS)}, ACTION_KEEP),
            # 소유 미발견 + 다른 시스템 확인 실패 → HALT하지 않는다(반대 증거가 없다)
            (SINGLE_MON, {"mon": mon(), "asset": asset(error="키 매니페스트 없음")}, ACTION_KEEP),
            # 두 시스템 필요 · 한쪽 발견 + 한쪽 실패 → 실패한 쪽도 조회 대상에 남긴다
            (
                BOTH,
                {"mon": mon(found=("zone_a",)), "asset": asset(error="RuntimeError")},
                ACTION_QUERY,
            ),
            # 정본 확인 실패 → 라우팅 유지
            (AMBIG, {"mon": mon(failed=MON_DBS), "asset": asset(found=True)}, ACTION_KEEP),
        ],
    )
    def test_row7_lookup_failure_is_never_merged_with_absence(self, plan, probes, action):
        d = decide_systems(plan, probes)
        assert (d.action, d.row) == (action, ROW_UNVERIFIED)
        assert "확인하지 못했습니다" in d.message and "'없음'이 아닙니다" in d.message
        assert "등록되지 않은" not in d.message
        assert d.unverified

    def test_row7_both_keeps_failed_system_scope(self):
        d = decide_systems(
            BOTH, {"mon": mon(found=("zone_a",)), "asset": asset(error="RuntimeError")}
        )
        assert d.systems == ("mon", "asset") and d.db_ids == ("zone_a", "asset_db")


class TestAbsentVersusUnverified:
    """★ 입력에서 "실패"만 바꿨을 때 판정이 갈린다 — 합쳐져 있으면 두 결과가 같아진다."""

    def test_other_system_absent_vs_unverified(self):
        absent = decide_systems(SINGLE_MON, {"mon": mon(), "asset": asset()})
        unknown = decide_systems(
            SINGLE_MON, {"mon": mon(), "asset": asset(error="키 매니페스트 없음")}
        )
        assert absent.row == ROW_NONE_FOUND and unknown.row == ROW_UNVERIFIED
        assert (
            "키 매니페스트 없음" in unknown.message and "키 매니페스트 없음" not in absent.message
        )

    def test_owner_absent_vs_unverified(self):
        halt = decide_systems(SINGLE_ASSET, {"asset": asset(), "mon": mon(found=("zone_a",))})
        keep = decide_systems(
            SINGLE_ASSET, {"asset": asset(error="RuntimeError"), "mon": mon(found=("zone_a",))}
        )
        assert halt.action == ACTION_HALT and keep.action == ACTION_KEEP

    def test_none_found_does_not_halt(self):
        """확인은 고정 규칙이라 본 조회가 찾을 여지가 있고, 다른 시스템에 있다는 증거도 없다."""
        d = decide_systems(SINGLE_MON, {"mon": mon(), "asset": asset()})
        assert (d.action, d.row) == (ACTION_KEEP, ROW_NONE_FOUND)
        assert "찾지 못했습니다" in d.message


class TestMessages:
    def test_missing_sample_is_capped_at_ten(self):
        """G-6 기본 가정 — 미발견 샘플 10개 + 나머지 건수."""
        ids = tuple(f"svr-x-{i:02d}" for i in range(12))
        probe = SystemProbe(
            system="asset",
            label="자산관리",
            identifiers=ids,
            probed_db_ids=("asset_db",),
            hits={ids[0]: ("asset_db",)},
        )
        d = decide_systems(SINGLE_ASSET, {"asset": probe})
        assert "찾지 못한 대상 11건" in d.message
        assert "svr-x-10" in d.message and "svr-x-11" not in d.message and "외 1건" in d.message

    def test_labels_come_from_arguments(self):
        d = decide_systems(
            SINGLE_ASSET,
            {"asset": asset(label="원장"), "mon": mon(found=("zone_a",), label="관측")},
        )
        assert "원장에 등록되지 않은 서버입니다(관측에는 있음)" in d.message

    def test_partial_zone_failure_is_reported_on_query(self):
        d = decide_systems(SINGLE_MON, {"mon": mon(found=("zone_b",), failed=("zone_a",))})
        assert d.db_ids == ("zone_a", "zone_b")
        assert "존A(RuntimeError)" in d.message


class TestTraceAndClassify:
    def test_classify_systems_uses_zone_axis_vocabulary(self):
        assert classify_systems([mon(found=("zone_a",)), asset()]) == RESOLVED
        assert classify_systems([mon(found=("zone_a",)), asset(found=True)]) == AMBIGUOUS
        assert classify_systems([mon(failed=MON_DBS), asset()]) == NOT_FOUND

    def test_trace_payload_is_plain_and_separates_failures(self):
        probes = {
            "mon": mon(found=("zone_b",), failed=("zone_a",)),
            "asset": asset(error="키 매니페스트 없음"),
        }
        d = decide_systems(BOTH, probes)
        trace = system_trace_payload(BOTH, probes, d)
        assert trace["probes"]["asset"]["status"] == SYSTEM_UNVERIFIED
        assert trace["probes"]["asset"]["errors"] == {"asset_db": "키 매니페스트 없음"}
        assert trace["probes"]["mon"]["found_db_ids"] == ["zone_b"]
        assert trace["row"] == d.row and trace["action"] == d.action
        import json

        json.dumps(trace, ensure_ascii=False)  # 체크포인터 직렬화 가능


class TestPurity:
    def test_module_imports_only_domain_and_stdlib(self):
        """I/O·LLM 0 — 도메인 밖 import가 없다(판정은 입력만으로 결정된다)."""
        path = Path(__file__).resolve().parents[2] / "src" / "domain" / "host_discovery.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
            elif isinstance(node, ast.Import):
                modules.update(a.name for a in node.names)
        assert all(
            m in {"__future__", "dataclasses", "typing"} or m.startswith("src.domain")
            for m in modules
        ), modules

    def test_no_system_names_hardcoded(self):
        text = (
            Path(__file__).resolve().parents[2] / "src" / "domain" / "host_discovery.py"
        ).read_text(encoding="utf-8")
        for literal in ("itam", "자산관리", "polestar", "폴스타"):
            assert literal not in text.lower(), literal
