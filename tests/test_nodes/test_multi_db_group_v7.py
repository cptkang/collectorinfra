"""존 그룹 순차 실행의 v7 결함·갭 수정 (plans/82 v7 R-1·R-2·R-3·R-5 · D-249).

1. **R-1** 동일 스키마 소급 복구(D-153)가 **그룹 안에서** 끝난다 — 종전에는 병합 run이 첫 그룹
   (은행존)의 `sql_by_schema`만 가져 공동존 gp 복구가 로그 없이 빠졌다
2. **R-2·R-3** 그룹 시작·완료를 진행 이벤트로 낸다(노드 안이라 사다리 전 단 공통). peer 그룹은
   **마스킹한** 행 미리보기를 싣고, 값은 `JSON.parse`가 받을 수 있는 형태다
3. **R-5** 그룹 소요를 감사 로그에 남긴다

LLM·MCP·DB 0(D-127) — 전부 대역.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.config import SecurityConfig

_B0, _GP, _YD = "polestar_b0", "polestar_cm_gp", "polestar_cm_yd"
_PG_KEY = ("postgresql", "polestar")
_DB2_KEY = ("db2", "POLESTAR")


def _cfg(exclusive=False, preview_rows=10, security=True):
    return SimpleNamespace(
        multi_db=SimpleNamespace(zone_group_exclusive=exclusive),
        server=SimpleNamespace(sse_group_preview_rows=preview_rows),
        security=SecurityConfig() if security else None,
    )


def _targets(*db_ids):
    return [{"db_id": d, "relevance_score": 0.9, "sub_query_context": "q"} for d in db_ids]


def _run(state, app_config, *, behave, audit_error=None):
    """`behave(db_id, run)`가 대상별 결과를 정한다. 소급 복구는 대역 클라이언트로 관측한다."""
    import src.nodes.multi_db_executor as mod

    recovered: list[str] = []
    events: list[tuple[str, dict]] = []
    audits: list[dict] = []

    class _Client:
        def __init__(self, db_id):
            self.db_id = db_id

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute_sql(self, sql):
            recovered.append(self.db_id)
            return SimpleNamespace(rows=[{"recovered": self.db_id}], row_count=1)

    async def _fake_single(target, run):
        behave(target["db_id"], run)

    def _fake_prepare(st, llm, cfg):
        run = MagicMock()
        run.state = st
        run.db_results, run.db_errors, run.db_schemas, run.db_sqls = {}, {}, {}, {}
        run.all_attempts, run.validation_failed = [], {}
        run.sql_by_schema = {}
        run.mc_candidates, run.mc_derivations = [], []
        run.form_fill_out = {}
        run.skipped_dbs, run.dependency_notes = [], []
        run.registry.get_client = lambda db_id: _Client(db_id)
        return run

    async def _fake_dispatch(name, data):
        events.append((name, data))

    async def _fake_audit(**kw):
        if audit_error is not None:
            raise audit_error
        audits.append(kw)

    with patch.object(mod, "_run_single_target", AsyncMock(side_effect=_fake_single)), \
         patch.object(mod, "_prepare_multi_run", AsyncMock(side_effect=_fake_prepare)), \
         patch.object(mod, "log_query_execution", AsyncMock()), \
         patch.object(mod, "log_group_execution", AsyncMock(side_effect=_fake_audit)), \
         patch.object(mod, "dispatch_progress_event", AsyncMock(side_effect=_fake_dispatch)):
        out = asyncio.run(mod.multi_db_executor(state, llm=MagicMock(), app_config=app_config))
    return out, recovered, events, audits


def _gp_fails_yd_succeeds(db_id, run):
    """공동존 gp는 검증에 실패하고, 같은 스키마 yd가 성공해 복구원 SQL을 남긴다."""
    if db_id == _GP:
        run.validation_failed[db_id] = _PG_KEY
        run.db_errors[db_id] = "검증 실패"
        return
    run.db_results[db_id] = [{"a": 1}]
    if db_id == _YD:
        run.sql_by_schema[_PG_KEY] = "SELECT 1"


class TestR1RetroRecoveryInsideGroup:
    def test_grouped_path_recovers_common_zone(self):
        """★ 결함 재현 고정 — 존 동시 조회(그룹 경로)에서도 gp가 yd SQL로 복구된다."""
        state = {"user_query": "q", "target_databases": _targets(_B0, _GP, _YD)}
        out, recovered, _, _ = _run(state, _cfg(False), behave=_gp_fails_yd_succeeds)
        assert recovered == [_GP]
        assert _GP in out["db_results"] and _GP not in out["db_errors"]
        assert out["group_results"]["polestar:common"]["row_count"] == 2, "복구분이 그룹 건수에 들어간다"
        assert out["group_results"]["polestar:common"]["errors"] == {}

    def test_single_run_path_still_recovers(self):
        """상호배타(종전 단일 run) 경로의 복구 동작은 그대로다."""
        state = {"user_query": "q", "target_databases": _targets(_B0, _GP, _YD)}
        out, recovered, _, _ = _run(state, _cfg(True), behave=_gp_fails_yd_succeeds)
        assert recovered == [_GP]
        assert "group_results" not in out

    def test_no_cross_group_recovery(self):
        """은행존 b0(DB2)는 공동존(PG) SQL로 복구하지 않는다 — 키가 다르고 그룹도 다르다."""
        def behave(db_id, run):
            if db_id == _B0:
                run.validation_failed[db_id] = _DB2_KEY
                run.db_errors[db_id] = "검증 실패"
                return
            run.db_results[db_id] = [{"a": 1}]
            run.sql_by_schema[_PG_KEY] = "SELECT 1"

        state = {"user_query": "q", "target_databases": _targets(_B0, _GP, _YD)}
        out, recovered, _, _ = _run(state, _cfg(False), behave=behave)
        assert recovered == []
        assert out["db_errors"].get(_B0) == "검증 실패"


class TestR2GroupEvents:
    def _ok(self, db_id, run):
        run.db_results[db_id] = [{"host": f"{db_id}-h{i}", "password": "p@ss", "cpu": 1.5} for i in range(3)]

    def test_start_and_end_per_group_in_query_order(self):
        state = {"user_query": "q", "target_databases": _targets(_YD, _B0, _GP)}
        _, _, events, _ = _run(state, _cfg(False), behave=self._ok)
        seq = [(d["phase"], d["group"]["group_key"]) for n, d in events if n == "group"]
        assert seq == [
            ("start", "polestar:bank"), ("end", "polestar:bank"),
            ("start", "polestar:common"), ("end", "polestar:common"),
        ]

    def test_end_carries_count_elapsed_and_masked_preview(self):
        state = {"user_query": "q", "target_databases": _targets(_B0, _GP, _YD)}
        _, _, events, _ = _run(state, _cfg(False, preview_rows=2), behave=self._ok)
        end = [d["group"] for n, d in events if n == "group" and d["phase"] == "end"]
        common = end[1]
        assert common["row_count"] == 6 and common["elapsed_ms"] >= 0
        preview = common["preview"]
        assert preview["columns"] == ["host", "password", "cpu"]
        assert len(preview["rows"]) == 2 and preview["truncated"] == 4
        assert all(r[1] == SecurityConfig().mask_pattern for r in preview["rows"]), "민감 컬럼은 마스킹 후 전송"
        assert preview["rows"][0][2] == 1.5

    def test_preview_values_are_strict_json(self):
        """Decimal·날짜·NaN이 섞여도 `JSON.parse`가 받는다 — 한 줄이 깨지면 이후 이벤트가 다 무시된다."""
        def behave(db_id, run):
            run.db_results[db_id] = [{
                "v": Decimal("1.25"), "at": dt.datetime(2026, 9, 22, 9, 0),
                "nan": float("nan"), "long": "디스크 사용률 경고 " * 60, "none": None,
            }]

        state = {"user_query": "q", "target_databases": _targets(_B0, _GP)}
        _, _, events, _ = _run(state, _cfg(False), behave=behave)
        for name, data in events:
            json.dumps(data, allow_nan=False)  # 예외가 나면 실패
        row = [d for n, d in events if d["phase"] == "end"][0]["group"]["preview"]["rows"][0]
        assert row[0] == "1.25" and row[1].startswith("2026-09-22")
        assert row[2] == "nan" and len(row[3]) == 200 and row[4] is None

    def test_no_rows_when_limit_zero_or_no_security_config(self):
        state = {"user_query": "q", "target_databases": _targets(_B0, _GP)}
        for cfg in (_cfg(False, preview_rows=0), _cfg(False, security=False)):
            _, _, events, _ = _run(state, cfg, behave=self._ok)
            ends = [d["group"] for n, d in events if d["phase"] == "end"]
            assert ends and all("preview" not in g for g in ends)
            assert all(g["row_count"] == 3 for g in ends), "행이 없어도 건수 알림은 나간다"

    def test_error_dbs_are_display_names_not_messages(self):
        """에러 문자열에는 SQL·값이 섞일 수 있어 화면에는 DB 표시 이름만 보낸다."""
        def behave(db_id, run):
            if db_id == _GP:
                run.db_errors[db_id] = "syntax error near 'secret-value'"
                return
            run.db_results[db_id] = [{"a": 1}]

        state = {"user_query": "q", "target_databases": _targets(_B0, _GP, _YD)}
        _, _, events, _ = _run(state, _cfg(False), behave=behave)
        common = [d["group"] for n, d in events if d["phase"] == "end"][1]
        assert common["error_dbs"] and "secret-value" not in json.dumps(common, ensure_ascii=False)

    def test_dependent_group_sends_no_preview(self):
        """dependent는 앞 그룹만으로 답이 되지 않는다 — 부분 노출이 오해를 만든다(§4.9)."""
        from src.routing.execution_groups import partition_execution_groups

        groups = [dict(g, kind="dependent") for g in partition_execution_groups([_B0, _GP])]
        state = {"user_query": "q", "target_databases": _targets(_B0, _GP), "execution_groups": groups}
        out, _, events, _ = _run(state, _cfg(True), behave=self._ok)
        ends = [d["group"] for n, d in events if d["phase"] == "end"]
        assert len(ends) == 2 and all("preview" not in g for g in ends)
        assert out["group_packets"] == []

    def test_single_group_path_emits_nothing(self):
        """공동존만이면 그룹 실행이 아니다 — 이벤트·감사 0(종전 경로 그대로)."""
        state = {"user_query": "q", "target_databases": _targets(_GP, _YD)}
        _, _, events, audits = _run(state, _cfg(False), behave=self._ok)
        assert events == [] and audits == []


class TestR5GroupAudit:
    def test_each_group_is_audited_with_elapsed_and_errors(self):
        state = {"user_query": "q", "target_databases": _targets(_B0, _GP, _YD),
                 "user_id": "u1", "thread_id": "t1"}

        def behave(db_id, run):
            if db_id == _B0:
                run.db_errors[db_id] = "boom"
                return
            run.db_results[db_id] = [{"a": 1}]

        _, _, _, audits = _run(state, _cfg(False), behave=behave)
        assert [a["group_key"] for a in audits] == ["polestar:bank", "polestar:common"]
        bank, common = audits
        assert bank["error_db_ids"] == [_B0] and bank["row_count"] == 0
        assert common["row_count"] == 2 and common["db_ids"] == [_GP, _YD]
        assert all(a["user_id"] == "u1" and a["thread_id"] == "t1" and a["elapsed_ms"] >= 0 for a in audits)

    def test_audit_failure_does_not_break_query(self):
        state = {"user_query": "q", "target_databases": _targets(_B0, _GP)}
        out, _, events, _ = _run(
            state, _cfg(False), audit_error=OSError("disk full"),
            behave=lambda d, r: r.db_results.__setitem__(d, [{"a": 1}]),
        )
        assert set(out["db_results"]) == {_B0, _GP}
        assert len([1 for n, d in events if d["phase"] == "end"]) == 2, "감사 실패 뒤에도 완료 이벤트는 나간다"
