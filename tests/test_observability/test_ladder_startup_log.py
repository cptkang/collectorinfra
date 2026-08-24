"""사다리 기동 단 확정 로그 테스트 (D-143 / plans/70 P0-1).

경로 4종은 대등하게 병존하는 것이 아니라 **1 정본 + 3 폴백 사다리**이고, 확정은
`build_graph()` 내부에서 1회 일어나는 **빌드 타임 배타**다(요청별 강등이 아니다).
따라서 관측 대상은 "요청별 분포"가 아니라 **기동 시 확정된 단 1건**이다.

이 로그가 게이트 6(레거시 4단 제거 여부) 판정의 근거가 된다 — 추정으로 지우면
plans/70 v1의 오독을 반복한다.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from src.observability.ladder import (
    LadderTier,
    resolve_ladder_tier,
)


def _config(
    *,
    package: bool = False,
    orchestration: bool = False,
    semantic: bool = False,
    multi_db: bool = False,
    explicit: bool = True,
):
    cfg = MagicMock()
    cfg.enable_deepagents_package = package
    cfg.enable_intent_orchestration = orchestration
    cfg.enable_semantic_routing = semantic
    cfg.multi_db.get_active_db_ids.return_value = ["db1"] if multi_db else []
    return cfg


class TestTierResolution:
    """4단 중 어느 단으로 확정되는지."""

    def test_tier1_when_deep_agent_buildable(self):
        tier, reason = resolve_ladder_tier(
            _config(package=True), backend="deep_agent", buildable=True
        )

        assert tier is LadderTier.DEEP_AGENT
        assert reason == "none"

    def test_tier2_when_orchestration_enabled(self):
        tier, reason = resolve_ladder_tier(
            _config(orchestration=True), backend="semantic_router", buildable=False
        )

        assert tier is LadderTier.INTENT_ORCHESTRATION

    def test_tier3_when_semantic_routing_enabled(self):
        tier, reason = resolve_ladder_tier(
            _config(semantic=True), backend="semantic_router", buildable=False
        )

        assert tier is LadderTier.SEMANTIC_ROUTER

    def test_tier4_when_nothing_enabled(self):
        """레거시 모드 — 게이트 6의 판정 대상."""
        tier, reason = resolve_ladder_tier(
            _config(), backend="semantic_router", buildable=False
        )

        assert tier is LadderTier.LEGACY

    def test_orchestration_wins_over_semantic(self):
        """둘 다 켜지면 상위 단이 이긴다 (graph.py 분기 순서와 일치)."""
        tier, _ = resolve_ladder_tier(
            _config(orchestration=True, semantic=True),
            backend="semantic_router", buildable=False,
        )

        assert tier is LadderTier.INTENT_ORCHESTRATION


class TestDegradationReason:
    """왜 정본(1단)이 아닌지 — 사유가 구분되어야 진단이 된다."""

    def test_package_missing_when_selected_but_not_buildable(self):
        """백엔드는 deep_agent를 골랐는데 조립이 안 되는 경우."""
        _, reason = resolve_ladder_tier(
            _config(package=True, semantic=True), backend="deep_agent", buildable=False
        )

        assert reason == "package_missing"

    def test_orchestrator_unavailable_when_flag_on_but_backend_declined(self):
        """플래그는 켜졌는데 백엔드가 deep_agent를 고르지 않은 경우."""
        _, reason = resolve_ladder_tier(
            _config(package=True, semantic=True), backend="semantic_router", buildable=False
        )

        assert reason == "orchestrator_unavailable"

    def test_flag_off_when_package_disabled(self):
        _, reason = resolve_ladder_tier(
            _config(package=False, semantic=True), backend="semantic_router", buildable=False
        )

        assert reason == "flag_off"

    def test_none_when_tier1_confirmed(self):
        _, reason = resolve_ladder_tier(
            _config(package=True), backend="deep_agent", buildable=True
        )

        assert reason == "none"


class TestTriStateOrigin:
    """암묵 활성(멀티 DB 등록 시 자동)인지 명시 설정인지 구분한다."""

    def test_explicit_env_when_flag_is_bool(self):
        from src.observability.ladder import resolve_flag_origin

        assert resolve_flag_origin(True) == "explicit_env"
        assert resolve_flag_origin(False) == "explicit_env"

    def test_auto_multidb_when_flag_is_none(self):
        from src.observability.ladder import resolve_flag_origin

        assert resolve_flag_origin(None) == "auto_multidb"


class TestStartupLog:
    """빌드 완료 로그 1줄로 확정 단과 사유를 판독할 수 있어야 한다."""

    def test_log_contains_tier_and_reason(self, caplog):
        from src.observability.ladder import log_ladder_resolution

        with caplog.at_level(logging.INFO):
            log_ladder_resolution(
                LadderTier.SEMANTIC_ROUTER, "orchestrator_unavailable",
                flag_origin="auto_multidb",
            )

        msg = " ".join(r.getMessage() for r in caplog.records)
        assert "semantic_router" in msg
        assert "orchestrator_unavailable" in msg
        assert "auto_multidb" in msg

    def test_tier1_logs_without_degradation_noise(self, caplog):
        from src.observability.ladder import log_ladder_resolution

        with caplog.at_level(logging.INFO):
            log_ladder_resolution(LadderTier.DEEP_AGENT, "none", flag_origin="explicit_env")

        msg = " ".join(r.getMessage() for r in caplog.records)
        assert "deep_agent" in msg

    def test_non_canonical_tier_warns_once(self, caplog):
        """정본이 아니면 경고를 남긴다 — 다만 기동당 1회다(스팸 없음)."""
        from src.observability.ladder import log_ladder_resolution

        with caplog.at_level(logging.WARNING):
            log_ladder_resolution(LadderTier.LEGACY, "flag_off", flag_origin="explicit_env")

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 1


class TestGraphWiring:
    """실제 build_graph가 이 판정을 로그로 낸다."""

    def test_build_graph_logs_ladder(self):
        from pathlib import Path

        src = Path("src/graph.py").read_text(encoding="utf-8")
        assert "ladder_resolution" in src, "build_graph가 사다리 판정을 기록하지 않음"

    def test_no_new_flag_introduced(self):
        """관측을 위해 새 enable_* 플래그를 만들지 않는다 (자기모순 회피)."""
        from pathlib import Path

        src = Path("src/observability/ladder.py").read_text(encoding="utf-8")
        assert "enable_" not in src.replace("enable_deepagents_package", "").replace(
            "enable_intent_orchestration", ""
        ).replace("enable_semantic_routing", ""), "신규 플래그가 도입됨"
