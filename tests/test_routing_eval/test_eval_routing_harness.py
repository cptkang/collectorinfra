"""라우팅 평가 하네스 검출력 검증 (Plan 80 WU-05 선행).

**왜 하네스를 테스트하나.** S-1은 회귀 게이트다. 게이트가 **거짓 통과**를 내면 승인·과금을
쓰고도 아무것도 보장하지 못한다. 실제로 초안 하네스는 멀티 DB 축소만 보고 종료 코드를 정해
**의도 오분류와 LLM 전면 실패를 exit 0으로 통과**시켰다(2026-08-27 목업 결함 주입으로 발견).

실 LLM 호출 0건 — `KBGenAIChat`의 **HTTP 경계만** 갈아끼우므로 클라이언트 로직
(페이로드 조립·status 규약·`remove_llm_junk`·PII 훅)은 그대로 실행된다. D-127 무관.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

from tests.mocks import fabrix_kbgenai_mock as fx

_REPO = Path(__file__).resolve().parents[2]


def _harness():
    spec = importlib.util.spec_from_file_location(
        "eval_routing", _REPO / "scripts" / "eval_routing.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


H = _harness()


def _run(fault: str) -> dict:
    items = H.load_gold()
    with fx.mock_kbgenai(fault=fault):
        results = asyncio.run(H.run(items, llm=fx.make_llm()))
    return H.summarize(results)


class TestGoldsetIntegrity:
    def test_goldset_is_valid(self):
        """골든셋이 실제 도메인·intent와 정합한다(실행 전에 잡는다)."""
        assert H.validate_gold(H.load_gold()) == []

    def test_goldset_has_multi_db_cases(self):
        """멀티 DB 케이스가 없으면 불변식 ⑩을 감시할 수 없다."""
        items = H.load_gold()
        multi = [i for i in items if i.get("critical") == "multi_db"]
        assert len(multi) >= 3, f"멀티 DB 감시 케이스가 부족하다: {len(multi)}건"
        for it in multi:
            assert it["expect"]["min_databases"] >= 2


class TestBoundaryForbid:
    """관측/자산 경계(plans/95 W-8) — 금지 DB 선택은 리콜과 무관하게 실패여야 한다."""

    _ITEM = {
        "id": "t-boundary",
        "query": "q",
        "expect": {"intent": "data_query", "forbid_databases": ["itam"], "min_databases": 1},
    }

    def test_forbidden_db_selected_fails(self):
        got = {"intent": "data_query", "databases": [{"db_id": "polestar_b0"}, {"db_id": "itam"}]}
        r = H.judge(self._ITEM, got)
        assert r["db_forbidden"] == ["itam"]
        assert r["passed"] is False

    def test_forbidden_db_absent_passes(self):
        got = {"intent": "data_query", "databases": [{"db_id": "polestar_b0"}]}
        assert H.judge(self._ITEM, got)["passed"] is True

    def test_goldset_has_boundary_cases_in_both_directions(self):
        boundary = [i for i in H.load_gold() if i.get("critical") == "boundary"]
        assert any("itam" in (i["expect"].get("forbid_databases") or []) for i in boundary)
        assert any("itam" in (i["expect"].get("databases") or []) for i in boundary)

    def test_unknown_forbidden_db_rejected_by_validation(self):
        item = {
            "id": "t-x",
            "query": "q",
            "expect": {"intent": "data_query", "forbid_databases": ["nope"]},
        }
        assert H.validate_gold([item])


class TestCleanBaseline:
    def test_clean_mock_passes_everything(self):
        """정상 응답에 오탐을 내면 게이트를 신뢰할 수 없다."""
        s = _run(fx.FAULT_NONE)
        assert s["passed"] == s["total"], f"정상인데 실패했다: {s}"
        assert s["multi_db_preserved"] == s["multi_db_cases"]
        assert H._verdict(s) == 0

    def test_clean_mock_observes_low_confidence_band(self):
        """S-2 — A-1 이후 저신뢰 대역이 분포에 나타나야 관측이 성립한다."""
        s = _run(fx.FAULT_NONE)
        assert s["score_count"] > 0
        assert any("0.3~0.5" in k for k in s["score_distribution"]), (
            f"저신뢰 대역이 분포에 없다 — A-1 효과를 관측할 수 없다: {s['score_distribution']}"
        )


class TestRegressionDetection:
    """★ 이 클래스가 게이트의 존재 이유다 — 각 결함을 **반드시** 잡아야 한다."""

    @pytest.mark.parametrize(
        "fault",
        [
            fx.FAULT_COLLAPSE_MULTI,
            fx.FAULT_BAD_INTENT,
            fx.FAULT_BAD_SCORE,
            fx.FAULT_MALFORMED,
            fx.FAULT_ERROR_STATUS,
        ],
    )
    def test_every_injected_fault_is_detected(self, fault):
        s = _run(fault)
        assert H._verdict(s) == 1, (
            f"결함 {fault!r}이 게이트를 통과했다 — 거짓 통과다. summary={s}"
        )

    def test_multi_collapse_is_detected_even_when_tolerant(self):
        """멀티 DB 축소는 --tolerate와 무관하게 항상 회귀다."""
        s = _run(fx.FAULT_COLLAPSE_MULTI)
        assert H._verdict(s, tolerate=999) == 1

    def test_llm_errors_are_detected_even_when_tolerant(self):
        """호출 실패는 측정 자체가 무효다 — 관용 대상이 아니다."""
        s = _run(fx.FAULT_ERROR_STATUS)
        assert H._verdict(s, tolerate=999) == 1


class TestGuardsWorkThroughRealClientPath:
    """E-1·E-2가 **실제 KBGenAIChat 경로**에서 동작하는지(대역 LLM이 아니다)."""

    def test_e1_demotes_unknown_intent_end_to_end(self):
        """오타 intent가 data_query로 강등된다 — 하류 분기 낙하가 막힌다."""
        s = _run(fx.FAULT_BAD_INTENT)
        # 전 케이스가 'prosess_query'로 오염됐으나 E-1이 data_query로 강등하므로,
        # data_query를 기대하는 케이스만 일치한다. 오염이 그대로 흘렀다면 0이었을 것이다.
        assert s["intent_match"] > 0, "강등이 동작하지 않아 오염 intent가 그대로 흘렀다"
        assert s["intent_match"] < s["total"], "오염을 전혀 반영하지 못했다"

    def test_e2_isolates_bad_score_without_discarding_all(self):
        """★ 한 항목의 형식 오류가 분류 전체를 죽이지 않는다(F2 해소 실증)."""
        s = _run(fx.FAULT_BAD_SCORE)
        assert s["dropped_total"] > 0, "형식 오류 항목이 탈락 처리되지 않았다"
        assert s["intent_match"] == s["total"], (
            "점수 형식 오류가 intent 판정까지 오염시켰다 — 격리 실패"
        )
        assert s["multi_db_preserved"] > 0, (
            "형식 오류 하나로 멀티 DB가 전멸했다 — 종전 '분류 전체 폐기' 동작이 남아 있다"
        )


# ──────────────────────────────────────────────
# 교차 시스템 케이스 (plans/102 X-9 · H-1)
# ──────────────────────────────────────────────

def _run_ownership(monkeypatch, fault: str) -> dict:
    """소유 플래그 on 목업 실행.

    라우터 프롬프트에 소유표가 실려야 목업이 capabilities·chain을 낸다.
    """
    from src.config import load_config

    monkeypatch.setenv("ROUTER_CAPABILITY_OWNERSHIP_ENABLED", "true")
    load_config.cache_clear()
    try:
        return _run(fault)
    finally:
        monkeypatch.delenv("ROUTER_CAPABILITY_OWNERSHIP_ENABLED", raising=False)
        load_config.cache_clear()


class TestCrossSystemGold:
    _CHAIN_ITEM = {
        "id": "t-chain", "query": "q",
        "expect": {
            "intent": "data_query", "min_databases": 0,
            "chain": ["server_usage", "asset_contract"],
            "key_type": "hostname", "probe": True,
        },
    }

    def test_goldset_covers_requirement_groups(self):
        """R1·R2·R3·R4·R5·R6·G-1 — 교차 시스템 케이스가 모두 있고 두 방향 체인이 다 있다."""
        from src.routing.registry import get_registry

        reg = get_registry()
        cross = [i for i in H.load_gold() if i.get("critical") == "cross_system"]
        assert len(cross) >= 7
        chains = [i["expect"]["chain"] for i in cross if i["expect"].get("chain")]
        first_owner = {reg.capability_owners(c[0])[0] for c in chains}
        assert len(first_owner) >= 2, f"교차 체인이 한 방향뿐이다: {chains}"
        assert {i["expect"].get("key_type") for i in cross} >= {"ipv4", "hostname"}
        assert any(i["expect"].get("probe") is True for i in cross)
        assert any("itam" in (i["expect"].get("forbid_databases") or []) for i in cross)  # R2
        assert any(i["expect"].get("databases") == ["itam"] and i["expect"].get("forbid_databases")
                   for i in cross)  # R1

    @pytest.mark.parametrize("patch,needle", [
        ({"chain": ["nope"]}, "답변 영역"),
        ({"chain": "server_usage"}, "expect.chain"),
        ({"key_type": "mac"}, "key_type"),
        ({"probe": "yes"}, "probe"),
    ])
    def test_invalid_cross_system_fields_rejected(self, patch, needle):
        item = {"id": "t-x", "query": "q", "expect": {"intent": "data_query", **patch}}
        errs = H.validate_gold([item])
        assert errs and needle in errs[0]

    def test_chain_match_passes_and_mismatch_fails(self):
        base = {"intent": "data_query", "databases": []}
        ok = H.judge(self._CHAIN_ITEM, {**base, "chain": ["server_usage", "asset_contract"]})
        bad = H.judge(self._CHAIN_ITEM, {**base, "chain": ["asset_contract", "server_usage"]})
        assert ok["chain_scored"] and ok["chain_match"] and ok["passed"]
        assert bad["chain_scored"] and bad["chain_match"] is False and bad["passed"] is False

    def test_chain_is_unscored_not_passed_when_router_emits_none(self):
        """★ 거짓 통과 금지 — 라우터가 chain을 내지 않으면(플래그 off) 채점 불가로 **표기**한다."""
        r = H.judge(self._CHAIN_ITEM, {"intent": "data_query", "databases": []})
        assert r["chain_scored"] is False and r["chain_match"] is None
        assert "채점 불가" in r["chain_unscored_reason"]
        s = H.summarize([r])
        assert s["chain_cases"] == 1 and s["chain_scored"] == 0 and s["chain_matched"] == 0

    def test_key_type_and_probe_are_marked_router_unscored(self):
        got = {"intent": "data_query", "databases": [], "chain": ["server_usage", "asset_contract"]}
        r = H.judge(self._CHAIN_ITEM, got)
        assert r["router_unscored"] == {
            "key_type": "hostname", "probe": True, "reason": "라우터 단계 채점 불가(H-2 소관)",
        }
        assert H.summarize([r])["router_unscored_cases"] == 1

    def test_off_mock_reports_chain_unscored_without_regression(self):
        s = _run(fx.FAULT_NONE)
        assert s["passed"] == s["total"]
        assert s["chain_cases"] >= 7 and s["chain_scored"] == 0
        assert H._verdict(s) == 0

    def test_on_mock_scores_every_chain_case(self, monkeypatch):
        s = _run_ownership(monkeypatch, fx.FAULT_NONE)
        assert s["passed"] == s["total"], s
        assert s["chain_scored"] == s["chain_cases"] == s["chain_matched"]
        assert H._verdict(s) == 0

    def test_on_mock_detects_reversed_chain(self, monkeypatch):
        """★ chain 순서가 뒤집히면 게이트가 잡는다(D8 센서 검출력)."""
        s = _run_ownership(monkeypatch, fx.FAULT_CHAIN_REVERSED)
        assert s["chain_matched"] < s["chain_scored"]
        assert H._verdict(s) == 1


# ── 로컬 MLX 모드(D-240 부기) — 실 호출 없이 게이트 판정만 본다 ──────────────

def _cfg(worker, orchestrator, worker_url="http://127.0.0.1:8080/v1",
         orch_url="http://127.0.0.1:8080/v1"):
    from types import SimpleNamespace
    return SimpleNamespace(
        llm=SimpleNamespace(provider=worker, mlx_base_url=worker_url, mlx_model="m"),
        orchestrator=SimpleNamespace(provider=orchestrator, base_url=orch_url, model="m"))


class TestLocalMlxGate:
    def test_two_loopback_mlx_planes_open_without_run_e2e(self):
        ok, reason = H.local_mlx_mode(_cfg("mlx", "mlx"))
        assert ok, reason

    @pytest.mark.parametrize(("worker", "orchestrator"), [
        ("mlx", "gemini"),        # 오케스트레이터 과금 — 워커만 보면 무승인 호출(D-222)
        ("gemini", "mlx"),
        ("fabrix", "vllm"),       # 비과금이지만 로컬 MLX 가 아니다 — 이 모드의 대상 아님
        ("mlx", "vllm"),
    ])
    def test_any_non_mlx_plane_keeps_run_e2e_gate(self, worker, orchestrator):
        ok, _ = H.local_mlx_mode(_cfg(worker, orchestrator))
        assert not ok

    def test_remote_mlx_is_not_local(self):
        ok, reason = H.local_mlx_mode(_cfg("mlx", "mlx", orch_url="http://10.0.0.5:8080/v1"))
        assert not ok and "루프백" in reason

    def test_gate_refuses_without_run_e2e_when_not_local(self, monkeypatch):
        monkeypatch.delenv("RUN_E2E", raising=False)
        monkeypatch.setattr(H, "local_mlx_mode", lambda cfg=None: (False, "과금 평면이 있다"))
        with pytest.raises(SystemExit) as exc:
            H._require_optin()
        assert exc.value.code == 2

    def test_gate_opens_for_local_mlx_without_run_e2e(self, monkeypatch):
        monkeypatch.delenv("RUN_E2E", raising=False)
        monkeypatch.setattr(H, "local_mlx_mode", lambda cfg=None: (True, "로컬 MLX 루프백"))
        H._require_optin()   # SystemExit 없이 통과
