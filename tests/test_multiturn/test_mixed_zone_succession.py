"""혼합 존 스레드의 DB 승계 (D-206 · plans/90 v3 §11).

은행존+공동존을 함께 고른 스레드가 다음 턴에 첫 DB 하나로 좁혀지면 "선택된 폴스타로 계속"이 거짓이 된다.
`inherit_all`(존 동시 조회 개방)이면 직전 DB 집합 **전체**를 승계하고, 기본(상호배타)은 종전대로 1개다.
LLM·DB 0.
"""

from __future__ import annotations

from src.orchestration.subagents import _apply_db_succession

B0, GP, YD = "polestar_b0", "polestar_cm_gp", "polestar_cm_yd"
ACTIVE = [B0, GP, YD]


def _targets(*ids):
    return [{"db_id": d, "relevance_score": 0.5} for d in ids]


class TestInheritAll:
    def test_whole_previous_set_is_inherited(self):
        out, ok = _apply_db_succession(
            _targets(GP), "그 서버들 메모리", {"previous_db_ids": [B0, GP, YD]}, ACTIVE, inherit_all=True
        )
        assert ok is True
        assert [t["db_id"] for t in out] == [B0, GP, YD]

    def test_inactive_previous_ids_dropped(self):
        out, ok = _apply_db_succession(
            _targets(GP), "그 서버들", {"previous_db_ids": [B0, "ghost"]}, ACTIVE, inherit_all=True
        )
        assert ok is True and [t["db_id"] for t in out] == [B0]

    def test_already_equal_set_is_not_re_applied(self):
        out, ok = _apply_db_succession(
            _targets(GP, B0), "그 서버들", {"previous_db_ids": [B0, GP]}, ACTIVE, inherit_all=True
        )
        assert ok is False and [t["db_id"] for t in out] == [GP, B0]

    def test_new_location_signal_still_wins(self):
        out, ok = _apply_db_succession(
            _targets(YD), "여의도 서버 보여줘", {"previous_db_ids": [B0, GP]}, ACTIVE, inherit_all=True
        )
        assert ok is False and [t["db_id"] for t in out] == [YD]


class TestDefaultUnchanged:
    def test_single_candidate_when_exclusive(self):
        """상호배타(기본)에서는 종전대로 첫 DB 하나 — 팬아웃 b0+gp가 한 run에 섞이지 않는다."""
        out, ok = _apply_db_succession(
            _targets(GP, YD), "그 서버들", {"previous_db_ids": [B0, GP, YD]}, ACTIVE
        )
        assert ok is True and [t["db_id"] for t in out] == [B0]

    def test_converged_single_target_untouched_when_exclusive(self):
        out, ok = _apply_db_succession(
            _targets(GP), "그 서버들", {"previous_db_ids": [B0, GP, YD]}, ACTIVE
        )
        assert ok is False and [t["db_id"] for t in out] == [GP]
