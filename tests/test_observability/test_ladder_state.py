"""확정된 사다리 단의 조회·트레이스 반영 테스트 (plans/70 O2).

O1은 기동 로그 1줄을 남긴다. 그러나 로그는 로테이션되고, 실패 진단 시점에는
"이 요청이 어느 파이프라인으로 돌았는가"를 되짚을 수단이 없다 — 사다리 단이
달라지면 노드 구성 자체가 달라지므로, 이 정보 없이는 실패 트레이스의 node_path를
해석할 기준이 없다.

O2는 확정 결과를 **프로세스 내에서 조회 가능**하게 하고, 실패 트레이스 헤더에
싣는다. 새 플래그 없음 · 새 패키지 없음(AD-2/AD-3).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import pytest

from src.observability import ladder as ld
from src.observability import trace_collector as tc
from src.observability import trace_writer as tw
from src.observability.levels import TraceLevel


@pytest.fixture(autouse=True)
def _clean():
    ld.reset_ladder()
    tc.reset_all()
    yield
    ld.reset_ladder()
    tc.reset_all()


def _trace_dir(root: Path) -> Path:
    return root / "logs" / "trace" / datetime.now().strftime("%Y-%m-%d")


def _seed(request_id: str = "req1") -> None:
    tc.start_request(request_id, thread_id="t1", user_query="CPU 사용률 조회")
    tc.record_step(
        request_id,
        tc.TraceStep(step=1, node="query_generator", level=TraceLevel.INFO,
                     event="node.exit", elapsed_ms=1.0),
    )


class TestLadderQuery:
    """확정 결과가 조회 가능하다."""

    def test_unresolved_before_build(self):
        """빌드 전에는 확정값이 없다 — 없는 것을 있는 척하지 않는다."""
        assert ld.current_ladder() is None

    def test_records_tier_reason_and_origin(self):
        ld.record_ladder_resolution(
            ld.LadderTier.SEMANTIC_ROUTER, "flag_off", flag_origin="auto_multidb"
        )

        snap = ld.current_ladder()
        assert snap == {
            "tier": "semantic_router",
            "degraded_reason": "flag_off",
            "resolved_by": "auto_multidb",
        }

    def test_rebuild_overwrites(self):
        """그래프 재빌드 시 최신 확정만 유효하다 (누적 아님)."""
        ld.record_ladder_resolution(ld.LadderTier.LEGACY, "flag_off")
        ld.record_ladder_resolution(ld.LadderTier.DEEP_AGENT, "none")

        assert ld.current_ladder()["tier"] == "deep_agent"

    def test_snapshot_is_not_live_reference(self):
        """반환값을 호출부가 바꿔도 내부 상태가 오염되지 않는다."""
        ld.record_ladder_resolution(ld.LadderTier.DEEP_AGENT, "none")

        ld.current_ladder()["tier"] = "tampered"

        assert ld.current_ladder()["tier"] == "deep_agent"

    def test_record_also_logs(self, caplog):
        """기록과 로그는 한 진입점 — 둘이 어긋날 여지를 만들지 않는다."""
        with caplog.at_level(logging.INFO):
            ld.record_ladder_resolution(ld.LadderTier.DEEP_AGENT, "none")

        assert "tier=deep_agent" in " ".join(r.getMessage() for r in caplog.records)

    def test_non_canonical_warns_once(self, caplog):
        with caplog.at_level(logging.WARNING):
            ld.record_ladder_resolution(ld.LadderTier.LEGACY, "flag_off")

        assert len([r for r in caplog.records if r.levelno >= logging.WARNING]) == 1


class TestTraceHeader:
    """실패 트레이스가 확정 단을 함께 싣는다."""

    def _dump(self, tmp_path) -> dict:
        _seed()
        written = tw.flush_if_failed(
            "req1", {"error_message": "boom"}, project_root=tmp_path
        )
        assert written is not None
        return json.loads(written.read_text(encoding="utf-8").splitlines()[0])

    def test_header_carries_ladder(self, tmp_path):
        ld.record_ladder_resolution(ld.LadderTier.DEEP_AGENT, "none")

        header = self._dump(tmp_path)

        assert header["ladder"] == {
            "tier": "deep_agent",
            "degraded_reason": "none",
            "resolved_by": "explicit_env",
        }

    def test_header_ladder_is_none_when_unresolved(self, tmp_path):
        """확정 전 실패(기동 실패 등)에도 덤프는 성공한다 — 관측이 요청을 깨지 않는다."""
        header = self._dump(tmp_path)

        assert header["ladder"] is None

    def test_degraded_tier_visible_in_dump(self, tmp_path):
        """강등 상태에서 난 실패는 사유까지 파일에서 읽힌다."""
        ld.record_ladder_resolution(ld.LadderTier.SEMANTIC_ROUTER, "package_missing")

        header = self._dump(tmp_path)

        assert header["ladder"]["degraded_reason"] == "package_missing"


class TestNoNewFlag:
    def test_observability_adds_no_flag(self):
        src = Path("src/observability/ladder.py").read_text(encoding="utf-8")
        known = ("enable_deepagents_package", "enable_intent_orchestration",
                 "enable_semantic_routing")
        stripped = src
        for name in known:
            stripped = stripped.replace(name, "")
        assert "enable_" not in stripped, "관측을 위해 새 플래그가 도입됨"
