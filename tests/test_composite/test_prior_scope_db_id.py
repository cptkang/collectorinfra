"""선행 결과 출처 → 대상 스코프 배관 (D-176 · plans/82 §2 · SPEC-prior-scope-wiring).

실측 배경: 부품은 다 있는데 배관 두 곳이 끊겨 있었다.
  - `TargetRef.db_id` 필드는 있다 (`prior_targets.py`)
  - 병합 행의 `_source_db` 태그도 있다 (`multi_db_executor._merge_results`)
  - 그런데 `build_prior_targets`가 **행별 출처를 읽지 않고** 호출부가 준 db_id 하나를
    전 대상에 도장했고, `_resolve_db_id`는 `prior_targets`를 후보에 넣지도 않았다.
  → abd00이 공동존에 있어도 후속 프로세스 조회가 은행존 API를 쳤다.

전부 mock — LLM·네트워크·DB 미사용(D-127).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_B0, _GP, _YD = "polestar_b0", "polestar_cm_gp", "polestar_cm_yd"


def _rows(*pairs):
    """(hostname, _source_db) 쌍으로 병합 결과 행을 만든다."""
    return [
        {"hostname": h, **({"_source_db": src} if src else {})} for h, src in pairs
    ]


class TestRowLevelSourceDb:
    """`build_prior_targets` — 행별 `_source_db`가 db_id 정본이다."""

    def _build(self, rows, **kw):
        from src.utils.prior_targets import build_prior_targets

        return build_prior_targets(rows, **kw)

    def test_per_row_source_db_wins(self):
        """팬아웃 결과는 행마다 출처가 다르다 — 대상도 행별로 달라야 한다."""
        res = self._build(_rows(("abd00", _GP), ("bnk01", _B0)), db_id=_B0)
        by_host = {t.hostname: t.db_id for t in res.targets}
        assert by_host == {"abd00": _GP, "bnk01": _B0}

    def test_falls_back_to_caller_db_id_without_tag(self):
        """태그가 없으면 호출부 db_id — 단일 DB 경로는 현행과 동일하다."""
        res = self._build(_rows(("abd00", None), ("bnk01", None)), db_id=_B0)
        assert {t.db_id for t in res.targets} == {_B0}

    @pytest.mark.parametrize("bad", ["", "   ", None])
    def test_blank_tag_falls_back(self, bad):
        """빈 태그로 빈 db_id를 만들지 않는다."""
        rows = [{"hostname": "abd00", "_source_db": bad}]
        res = self._build(rows, db_id=_B0)
        assert res.targets[0].db_id == _B0

    def test_mixed_tagged_and_untagged(self):
        res = self._build(_rows(("abd00", _YD), ("bnk01", None)), db_id=_B0)
        by_host = {t.hostname: t.db_id for t in res.targets}
        assert by_host == {"abd00": _YD, "bnk01": _B0}

    def test_source_db_not_picked_as_identifier_column(self):
        """`_source_db`는 내부 태그다 — 서버 식별자로 오인되면 안 된다."""
        from src.utils.prior_targets import _pick_identifier_column

        col, _ = _pick_identifier_column(["_source_db", "hostname"])
        assert col == "hostname"

    def test_source_db_alone_is_not_an_identifier(self):
        """식별자 컬럼이 `_source_db`뿐이면 대상을 만들지 않는다."""
        res = self._build([{"_source_db": _GP, "cnt": 3}])
        assert not res.resolved

    def test_source_db_never_becomes_target_value(self):
        """태그 값이 hostname으로 새어 들어가지 않는다."""
        res = self._build(_rows(("abd00", _GP)), db_id=_B0)
        assert [t.hostname for t in res.targets] == ["abd00"]


class TestResolveDbIdPriority:
    """`_resolve_db_id` 우선순위: task.db_ids > prior_targets > previous_db_ids > 위치어."""

    def _resolve(self, task, isolated, sub_query="프로세스 조회"):
        from src.orchestration.process_query import _resolve_db_id

        cfg = SimpleNamespace(
            alarm=SimpleNamespace(get_process_api_base_url=lambda d: f"http://{d}")
        )
        return _resolve_db_id(task, isolated, sub_query, cfg)

    def _iso(self, **kw):
        base = {"conversation_context": {}, "parsed_requirements": {}}
        base.update(kw)
        return base

    def test_task_db_ids_beat_prior_targets(self):
        """존 선택 UI 확정(task.db_ids)은 어떤 추론보다 우선한다(D-143)."""
        got = self._resolve(
            {"db_ids": [_B0]},
            self._iso(prior_targets=[{"hostname": "abd00", "db_id": _GP}]),
        )
        assert got == _B0

    def test_prior_targets_used_when_no_task_db_ids(self):
        """이번 턴 선행 결과가 있으면 그 존을 쓴다 — 탐색형의 핵심 배관."""
        got = self._resolve(
            {}, self._iso(prior_targets=[{"hostname": "abd00", "db_id": _GP}])
        )
        assert got == _GP

    def test_prior_targets_beat_previous_db_ids(self):
        """이번 턴 결과가 직전 턴 승계보다 강하다(요청 스코프 우선)."""
        got = self._resolve(
            {},
            self._iso(
                prior_targets=[{"hostname": "abd00", "db_id": _GP}],
                conversation_context={"previous_db_ids": [_B0]},
            ),
        )
        assert got == _GP

    def test_previous_db_ids_used_without_prior_targets(self):
        got = self._resolve(
            {}, self._iso(conversation_context={"previous_db_ids": [_B0]})
        )
        assert got == _B0

    def test_location_hint_still_works(self):
        """prior_targets가 없으면 현행 3단 그대로."""
        got = self._resolve({}, self._iso(), sub_query="김포 서버 프로세스")
        assert got == _GP

    def test_no_signal_returns_none(self):
        """아무 신호도 없으면 종전대로 None — 탐색은 2차 모듈 소관."""
        assert self._resolve({}, self._iso()) is None

    def test_prior_targets_without_db_id_ignored(self):
        """db_id 없는 대상은 후보가 되지 않는다."""
        got = self._resolve({}, self._iso(prior_targets=[{"hostname": "abd00"}]))
        assert got is None

    def test_prior_targets_base_url_preference(self):
        """base_url이 매핑된 후보를 우선한다(기존 규칙 유지)."""
        from src.orchestration.process_query import _resolve_db_id

        cfg = SimpleNamespace(
            alarm=SimpleNamespace(
                get_process_api_base_url=lambda d: "http://x" if d == _YD else None
            )
        )
        got = _resolve_db_id(
            {},
            self._iso(prior_targets=[
                {"hostname": "a", "db_id": _GP}, {"hostname": "b", "db_id": _YD},
            ]),
            "프로세스",
            cfg,
        )
        assert got == _YD


class TestFlagOffParity:
    """`COMPOSITE_PRIOR_TARGETS_ENABLED=false`면 아무것도 달라지지 않는다."""

    def test_build_prior_targets_unaffected_by_flag(self):
        """`build_prior_targets`는 플래그를 보지 않는다 — 게이트는 호출부에 있다."""
        from src.utils.prior_targets import build_prior_targets

        res = build_prior_targets(_rows(("abd00", _GP)), db_id=_B0)
        assert res.targets[0].db_id == _GP

    def test_task_builder_returns_none_when_flag_off(self):
        import src.orchestration.subagents as mod

        cfg = MagicMock()
        cfg.composite.prior_targets_enabled = False
        with patch.object(mod, "load_config", MagicMock(return_value=cfg)):
            assert mod._build_prior_targets_for_task({}, {}, {}) is None
