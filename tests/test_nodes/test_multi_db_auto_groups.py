"""존 동시 조회 개방 시 실행기 자동 그룹 분할 (D-206 · plans/90 v3 §11 · D-176 배선 결손 보정).

★ 실측: `execution_groups`를 state에 싣는 코드가 0건이라 `_run_groups`는 죽은 경로였다. 이 테스트는
  1. `ZONE_GROUP_EXCLUSIVE=false`면 실행기가 **스스로** 존 그룹으로 나눠 은행존 → 공동존 순차 실행
  2. `true`(기본)·설정 부재·테스트 대역(MagicMock)에서는 종전 단일 그룹 경로 그대로(회귀 0)
  3. 그룹이 하나면(공동존만) 종전 경로 — 그룹 산출물 없음
을 못박는다. LLM·MCP·DB 0(D-127).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

_B0, _GP, _YD = "polestar_b0", "polestar_cm_gp", "polestar_cm_yd"


def _cfg(exclusive):
    return SimpleNamespace(multi_db=SimpleNamespace(zone_group_exclusive=exclusive))


def _run(state, app_config):
    import src.nodes.multi_db_executor as mod

    seen: list[str] = []
    runs: list[object] = []

    async def _fake_run_single(target, run):
        seen.append(target["db_id"])
        run.db_results[target["db_id"]] = [{"a": 1}]

    def _fake_prepare(st, llm, cfg):
        run = MagicMock()
        run.db_results, run.db_errors, run.db_schemas = {}, {}, {}
        run.all_attempts, run.validation_failed = [], {}
        run.sql_by_schema = {}
        run.mc_candidates, run.mc_derivations = [], []
        run.form_fill_out = {}
        run.skipped_dbs, run.dependency_notes = [], []
        runs.append(run)
        return run

    with patch.object(mod, "_run_single_target", AsyncMock(side_effect=_fake_run_single)), \
         patch.object(mod, "_prepare_multi_run", AsyncMock(side_effect=_fake_prepare)):
        out = asyncio.run(mod.multi_db_executor(state, llm=MagicMock(), app_config=app_config))
    return out, seen, runs


def _targets(*db_ids):
    return [{"db_id": d, "relevance_score": 0.9, "sub_query_context": "q"} for d in db_ids]


class TestOpened:
    def test_bank_runs_before_common_even_if_targets_shuffled(self):
        """사용자 요구: 함께 선택하면 은행존을 먼저, 완료되면 공동존."""
        state = {"user_query": "q", "target_databases": _targets(_YD, _B0, _GP)}
        _, seen, runs = _run(state, _cfg(False))
        assert seen == [_B0, _GP, _YD]
        assert len(runs) == 2, "그룹마다 run을 새로 만든다 — b0 재료와 gp/yd 재료가 섞이지 않는다"

    def test_group_results_carry_order_and_counts(self):
        state = {"user_query": "q", "target_databases": _targets(_B0, _GP, _YD)}
        out, _, _ = _run(state, _cfg(False))
        assert list(out["group_results"]) == ["polestar:bank", "polestar:common"]
        assert out["group_results"]["polestar:common"]["row_count"] == 2
        assert out["group_packets"][0]["label"] == "은행존"

    def test_merged_results_include_every_zone(self):
        state = {"user_query": "q", "target_databases": _targets(_B0, _GP, _YD)}
        out, _, _ = _run(state, _cfg(False))
        assert set(out["db_results"]) == {_B0, _GP, _YD}
        assert len(out["query_results"]) == 3

    def test_single_group_keeps_legacy_path(self):
        """공동존만 고르면 그룹이 하나 — 종전 경로(그룹 산출물 없음)."""
        state = {"user_query": "q", "target_databases": _targets(_GP, _YD)}
        out, seen, runs = _run(state, _cfg(False))
        assert seen == [_GP, _YD] and len(runs) == 1
        assert "group_results" not in out


class TestClosed:
    def test_exclusive_true_is_byte_identical(self):
        state = {"user_query": "q", "target_databases": _targets(_YD, _B0, _GP)}
        out, seen, runs = _run(state, _cfg(True))
        assert seen == [_YD, _B0, _GP] and len(runs) == 1
        assert "group_results" not in out

    def test_mock_config_counts_as_exclusive(self):
        """테스트 대역(MagicMock)은 개방으로 읽히면 안 된다 — 명시 false에서만 분할."""
        state = {"user_query": "q", "target_databases": _targets(_B0, _GP)}
        _, seen, runs = _run(state, MagicMock())
        assert seen == [_B0, _GP] and len(runs) == 1

    def test_explicit_execution_groups_still_win(self):
        """상위가 execution_groups를 실어 주면(향후) 그것을 그대로 쓴다."""
        from src.routing.execution_groups import partition_execution_groups

        state = {"user_query": "q", "target_databases": _targets(_B0, _GP),
                 "execution_groups": partition_execution_groups([_B0, _GP])}
        out, _, runs = _run(state, _cfg(True))
        assert len(runs) == 2 and "group_results" in out


class TestUnzonedResidualGroup:
    """존 미배정 DB는 마지막 잔여 그룹으로 실행한다 (plans/95 W-9 · D-214 ④).

    자산관리(`itam`)는 존이 없고 한 시스템이 전 존의 자산을 관리한다(사용자 확인 2026-09-17).
    종전에는 존 그룹이 둘 이상이면 그룹에 들지 않은 대상이 사유 없이 빠졌다.
    """

    _ITAM, _SANDBOX = "itam", "polestar"

    def test_unzoned_target_runs_last_instead_of_being_dropped(self):
        state = {"user_query": "q", "target_databases": _targets(_B0, _GP, _YD, self._ITAM)}
        out, seen, runs = _run(state, _cfg(False))
        assert seen == [_B0, _GP, _YD, self._ITAM]
        assert set(out["db_results"]) == {_B0, _GP, _YD, self._ITAM}
        assert list(out["group_results"]) == ["polestar:bank", "polestar:common", "unzoned"]
        assert out["group_results"]["unzoned"]["db_ids"] == [self._ITAM]
        assert out["group_packets"][-1]["label"] == "존 무관"
        assert len(runs) == 3, "잔여 그룹도 자기 run으로 격리된다"

    def test_residual_stays_last_regardless_of_input_order(self):
        state = {"user_query": "q",
                 "target_databases": _targets(self._ITAM, _YD, self._SANDBOX, _B0, _GP)}
        out, seen, _ = _run(state, _cfg(False))
        # 잔여 그룹 내부도 레지스트리 선언 순
        assert seen == [_B0, _GP, _YD, self._SANDBOX, self._ITAM]
        assert list(out["group_results"])[-1] == "unzoned"

    def test_explicit_groups_do_not_drop_uncovered_targets(self):
        from src.routing.execution_groups import partition_execution_groups

        state = {"user_query": "q", "target_databases": _targets(_B0, _GP, self._ITAM),
                 "execution_groups": partition_execution_groups([_B0, _GP, self._ITAM])}
        out, seen, _ = _run(state, _cfg(True))
        assert seen == [_B0, _GP, self._ITAM]
        assert list(out["group_results"]) == ["polestar:bank", "polestar:common", "unzoned"]


class TestUnzonedBitIdentical:
    """잔여 그룹이 끼어들지 않는 조합은 종전과 같다 — 현 운영(`b0·gp·yd`)·로컬 샌드박스 포함."""

    def test_no_unzoned_target_returns_same_groups_object(self):
        from src.nodes.multi_db_executor import _auto_execution_groups, _with_unzoned_group

        targets = _targets(_B0, _GP, _YD)
        groups = _auto_execution_groups(targets)
        assert _with_unzoned_group(groups, targets) is groups

    def test_single_zone_group_with_unzoned_keeps_legacy_path(self):
        """존 그룹이 하나면 종전처럼 전 대상 실행 — 잔여 그룹으로 경로를 바꾸지 않는다."""
        state = {"user_query": "q", "target_databases": _targets(_GP, _YD, "itam")}
        out, seen, runs = _run(state, _cfg(False))
        assert seen == [_GP, _YD, "itam"] and len(runs) == 1
        assert "group_results" not in out

    def test_sandbox_with_unzoned_keeps_legacy_path(self):
        state = {"user_query": "q", "target_databases": _targets("polestar", "itam")}
        out, seen, runs = _run(state, _cfg(False))
        assert seen == ["polestar", "itam"] and len(runs) == 1
        assert "group_results" not in out

    def test_exclusive_default_keeps_legacy_path_with_unzoned(self):
        state = {"user_query": "q", "target_databases": _targets("itam", _YD, _B0, _GP)}
        out, seen, runs = _run(state, _cfg(True))
        assert seen == ["itam", _YD, _B0, _GP] and len(runs) == 1
        assert "group_results" not in out

    def test_shared_partition_still_excludes_unzoned(self):
        """공용 분할·호스트 탐색 순서는 그대로다 — 잔여 그룹은 실행기 한정."""
        from src.orchestration.host_sweep import sweep_order
        from src.routing.execution_groups import partition_execution_groups

        ids = [_B0, _GP, _YD, "itam", "polestar"]
        assert [g["db_ids"] for g in partition_execution_groups(ids)] == [[_B0], [_GP, _YD]]
        assert sweep_order(ids) == [_B0, _GP, _YD]
