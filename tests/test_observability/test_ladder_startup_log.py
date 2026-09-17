"""사다리 기동 단 확정 로그 테스트 (D-161 / plans/70 P0-1 · D-225 기준 개정).

경로 4종은 대등하게 병존하는 것이 아니라 빌드 타임에 한 단만 확정되는 사다리이고, 확정은
`build_graph()` 내부에서 1회 일어나는 **빌드 타임 배타**다(요청별 강등이 아니다).
따라서 관측 대상은 "요청별 분포"가 아니라 **기동 시 확정된 단 1건**이다.

기준 경로는 3단 `semantic_router`, 1단 `deep_agent`는 부가 경로 opt-in이다(D-225 · plans/102 L-2).

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


class TestCanonicalTier:
    """기준 단 = 3단, 부가 경로 = 1단 (D-225 ①②)."""

    def test_semantic_router_is_canonical(self):
        assert LadderTier.SEMANTIC_ROUTER.is_canonical is True
        assert [t for t in LadderTier if t.is_canonical] == [LadderTier.SEMANTIC_ROUTER]

    def test_deep_agent_is_optin_not_canonical(self):
        assert LadderTier.DEEP_AGENT.is_optin is True
        assert LadderTier.DEEP_AGENT.is_canonical is False
        assert [t for t in LadderTier if t.is_optin] == [LadderTier.DEEP_AGENT]


class TestDegradationReason:
    """왜 기준 단(3단)이 아니거나 opt-in이 성립하지 않았는지 — 사유가 구분되어야 진단이 된다."""

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

    def test_none_when_tier3_confirmed_with_package_off(self):
        """1단 플래그 off는 기준 상태다 — 3단 확정에 사유가 붙지 않는다(종전 `flag_off` 폐기)."""
        tier, reason = resolve_ladder_tier(
            _config(package=False, semantic=True), backend="semantic_router", buildable=False
        )

        assert (tier, reason) == (LadderTier.SEMANTIC_ROUTER, "none")

    def test_intent_flag_on_when_tier2_confirmed_with_package_off(self):
        tier, reason = resolve_ladder_tier(
            _config(orchestration=True, semantic=True),
            backend="semantic_router", buildable=False,
        )

        assert (tier, reason) == (LadderTier.INTENT_ORCHESTRATION, "intent_flag_on")

    def test_semantic_routing_off_when_tier4_confirmed(self):
        tier, reason = resolve_ladder_tier(
            _config(), backend="semantic_router", buildable=False
        )

        assert (tier, reason) == (LadderTier.LEGACY, "semantic_routing_off")

    @pytest.mark.parametrize(
        "orchestration,semantic,tier",
        [
            (True, True, LadderTier.INTENT_ORCHESTRATION),
            (False, True, LadderTier.SEMANTIC_ROUTER),
            (False, False, LadderTier.LEGACY),
        ],
    )
    @pytest.mark.parametrize(
        "backend,expected",
        [("semantic_router", "orchestrator_unavailable"), ("deep_agent", "package_missing")],
    )
    def test_optin_failure_reason_wins_on_every_lower_tier(
        self, orchestration, semantic, tier, backend, expected
    ):
        """1단 opt-in이 켜졌는데 불성립이면 어느 하위 단으로 갔든 opt-in 실패가 사유다."""
        got = resolve_ladder_tier(
            _config(package=True, orchestration=orchestration, semantic=semantic),
            backend=backend, buildable=False,
        )

        assert got == (tier, expected)

    def test_flag_off_is_never_emitted(self):
        """폐기된 어휘가 어떤 조합에서도 나오지 않는다(D-225)."""
        from itertools import product

        from src.observability.ladder import OPTIN_FAILURE_REASONS

        allowed = {"none", "intent_flag_on", "semantic_routing_off", *OPTIN_FAILURE_REASONS}
        for package, orchestration, semantic, backend, buildable in product(
            (True, False), (True, False), (True, False),
            ("deep_agent", "semantic_router"), (True, False),
        ):
            _, reason = resolve_ladder_tier(
                _config(package=package, orchestration=orchestration, semantic=semantic),
                backend=backend, buildable=buildable,
            )
            assert reason in allowed, (package, orchestration, semantic, backend, buildable)

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

    def test_tier1_optin_logs_info_not_warning(self, caplog):
        """1단 opt-in은 강등이 아니다 — WARNING이 아니라 INFO 안내다(plans/102 성공 기준 11)."""
        from src.observability.ladder import log_ladder_resolution

        with caplog.at_level(logging.INFO, logger="src.observability.ladder"):
            log_ladder_resolution(LadderTier.DEEP_AGENT, "none", flag_origin="explicit_env")

        records = [r for r in caplog.records if r.name == "src.observability.ladder"]
        assert "tier=deep_agent" in records[0].getMessage()
        assert [r for r in records if r.levelno >= logging.WARNING] == []
        infos = [r.getMessage() for r in records if r.levelno == logging.INFO]
        assert len(infos) == 2 and "부가 경로(deep_agent) opt-in" in infos[1]

    def test_tier3_canonical_logs_single_info_line(self, caplog):
        """기준 단 + 사유 없음이면 확정 1줄뿐이다 — 추가 안내·경고 없음."""
        from src.observability.ladder import log_ladder_resolution

        with caplog.at_level(logging.DEBUG, logger="src.observability.ladder"):
            log_ladder_resolution(LadderTier.SEMANTIC_ROUTER, "none", flag_origin="auto_multidb")

        records = [r for r in caplog.records if r.name == "src.observability.ladder"]
        assert [(r.levelno, r.getMessage()) for r in records] == [(
            logging.INFO,
            "오케스트레이션 사다리 확정: tier=semantic_router degraded_reason=none "
            "resolved_by=auto_multidb",
        )]

    @pytest.mark.parametrize(
        "tier,reason",
        [(LadderTier.LEGACY, "semantic_routing_off"),
         (LadderTier.INTENT_ORCHESTRATION, "intent_flag_on")],
    )
    def test_non_canonical_tier_warns_once(self, caplog, tier, reason):
        """기준 단이 아니면 경고를 남긴다 — 다만 기동당 1회다(스팸 없음)."""
        from src.observability.ladder import log_ladder_resolution

        with caplog.at_level(logging.WARNING):
            log_ladder_resolution(tier, reason, flag_origin="explicit_env")

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 1
        assert "기준 경로(semantic_router)가 아닌" in warnings[0].getMessage()

    @pytest.mark.parametrize(
        "tier,reason",
        [(LadderTier.SEMANTIC_ROUTER, "orchestrator_unavailable"),
         (LadderTier.INTENT_ORCHESTRATION, "package_missing"),
         (LadderTier.LEGACY, "orchestrator_unavailable")],
    )
    def test_optin_failure_warns_once(self, caplog, tier, reason):
        """opt-in 실패는 의도하지 않은 경로다 — 3단에 떨어져도 경고하되 1회만."""
        from src.observability.ladder import log_ladder_resolution

        with caplog.at_level(logging.WARNING):
            log_ladder_resolution(tier, reason, flag_origin="explicit_env")

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 1
        assert "opt-in이 성립하지 않아" in warnings[0].getMessage()

    @pytest.mark.parametrize("tier", list(LadderTier))
    def test_first_line_parses_with_scenario_runner_regex(self, caplog, tier):
        """첫 줄 형식은 로그 판독 도구(`scripts/scenario/server.py`)와의 계약이다."""
        from scripts.scenario.server import _LADDER_RE
        from src.observability.ladder import log_ladder_resolution

        with caplog.at_level(logging.INFO, logger="src.observability.ladder"):
            log_ladder_resolution(tier, "none", flag_origin="code_default")

        match = _LADDER_RE.search(caplog.records[0].getMessage())
        assert match and match.group("tier") == tier.value
        assert match.group("reason") == "none"

    def test_optin_failure_reasons_match_scenario_runner(self):
        """러너가 INVALID로 보는 사유와 로그가 경고하는 opt-in 실패 사유는 같은 집합이다."""
        from scripts.scenario.server import UNINTENDED_DEGRADATION
        from src.observability.ladder import OPTIN_FAILURE_REASONS

        assert UNINTENDED_DEGRADATION == OPTIN_FAILURE_REASONS


class TestGraphWiring:
    """실제 build_graph가 이 판정을 로그로 낸다."""

    def test_build_graph_logs_ladder(self):
        from pathlib import Path

        src = Path("src/graph.py").read_text(encoding="utf-8")
        assert "ladder_resolution" in src, "build_graph가 사다리 판정을 기록하지 않음"

    def test_unset_flags_with_multi_db_start_on_tier3(self, caplog):
        """plans/102 성공 기준 11 — 설정 미입력 + 멀티 DB면 기동 로그가 `tier=semantic_router`다.

        종전(D-037)에는 2단 플래그 미입력 + 멀티 DB가 2단으로 자동 확정됐다(X-T11).
        실제 `build_graph()`를 돌려 기동 로그 1줄을 읽는다 — 판정 함수만 부르면
        배선 누락을 못 잡는다.
        """
        from src.config import (
            AppConfig,
            DBHubConfig,
            LLMConfig,
            MultiDBConfig,
            QueryConfig,
            SecurityConfig,
            ServerConfig,
        )
        from src.graph import build_graph
        from src.observability import ladder as ld

        cfg = AppConfig(
            _env_file=None,
            llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
            dbhub=DBHubConfig(server_url="http://localhost:9099/sse", source_name="infra_db",
                              mcp_call_timeout=60),
            query=QueryConfig(max_retry_count=3, default_limit=1000),
            security=SecurityConfig(sensitive_columns=["password"], mask_pattern="***"),
            server=ServerConfig(host="0.0.0.0", port=8000),
            multi_db=MultiDBConfig(_env_file=None, active_db_ids_csv="db_a,db_b"),
            enable_semantic_routing=None,
            enable_intent_orchestration=None,
            enable_deepagents_package=False,
            checkpoint_backend="sqlite",
            checkpoint_db_url=":memory:",
        )
        ld.reset_ladder()
        try:
            with caplog.at_level(logging.INFO, logger="src.observability.ladder"):
                compiled = build_graph(cfg)
            snap = ld.current_ladder()
        finally:
            ld.reset_ladder()

        assert snap == {"tier": "semantic_router", "degraded_reason": "none",
                        "resolved_by": "auto_multidb"}
        ladder_records = [r for r in caplog.records if r.name == "src.observability.ladder"]
        assert [r.getMessage() for r in ladder_records] == [
            "오케스트레이션 사다리 확정: tier=semantic_router degraded_reason=none "
            "resolved_by=auto_multidb"
        ]
        names = set(compiled.get_graph().nodes.keys())
        assert "semantic_router" in names and "intent_planner" not in names

    def test_no_new_flag_introduced(self):
        """관측을 위해 새 enable_* 플래그를 만들지 않는다 (자기모순 회피)."""
        from pathlib import Path

        src = Path("src/observability/ladder.py").read_text(encoding="utf-8")
        assert "enable_" not in src.replace("enable_deepagents_package", "").replace(
            "enable_intent_orchestration", ""
        ).replace("enable_semantic_routing", ""), "신규 플래그가 도입됨"
