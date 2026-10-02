"""tri-state 암묵 활성 경고 테스트 (plans/70 L3 · plans/102 L-1 / D-225 ④).

`enable_semantic_routing` · `enable_intent_orchestration`은 `bool | None`이다.
`enable_semantic_routing`이 `None`이면 "멀티 DB 등록 여부"로 자동 결정되므로, **운영 실행
경로가 DB 등록 상태에 종속**된다 — DB를 하나 등록/해제하는 것만으로 확정 단이 바뀔 수 있다.
`enable_intent_orchestration`이 `None`이면 DB 등록과 무관하게 **항상 on**이다(D-251 ① — 기준 운영 단
2단. D-225 ④의 "항상 off"를 개정). DB 등록 상태에 종속되지 않는다는 성질(X-T11 해소)은 그대로다.

`model_post_init`이 `None`을 bool로 덮어쓰고 나면 명시 설정과 구별할 수 없다.
따라서 발동 사실은 **덮어쓰는 그 자리에서만** 남길 수 있다.
"""

from __future__ import annotations

import logging

import pytest

from src.config import AppConfig, MultiDBConfig
from src.observability.ladder import LadderTier, resolve_ladder_tier


def _cfg(**kwargs) -> AppConfig:
    """`.env` 누수를 막기 위해 판정 대상 필드를 명시해 생성한다."""
    kwargs.setdefault("enable_semantic_routing", False)
    kwargs.setdefault("enable_intent_orchestration", False)
    return AppConfig(**kwargs)


class TestAutoResolutionWarning:
    def test_warns_when_semantic_routing_is_auto_resolved(self, caplog):
        with caplog.at_level(logging.WARNING, logger="src.config"):
            _cfg(enable_semantic_routing=None)

        msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("enable_semantic_routing" in m for m in msgs), msgs

    def test_warns_when_orchestration_is_auto_resolved(self, caplog):
        with caplog.at_level(logging.WARNING, logger="src.config"):
            _cfg(enable_intent_orchestration=None)

        msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("enable_intent_orchestration" in m for m in msgs), msgs

    def test_warning_names_the_db_dependency(self, caplog):
        """경고가 '왜 문제인지'를 말해야 한다 — 값만 알려주면 조치할 수 없다."""
        with caplog.at_level(logging.WARNING, logger="src.config"):
            _cfg(enable_semantic_routing=None)

        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "멀티 DB" in joined or "multi_db" in joined

    def test_silent_when_both_explicitly_set(self, caplog):
        """명시 설정이면 자동 해석이 개입하지 않으므로 경고도 없다."""
        with caplog.at_level(logging.WARNING, logger="src.config"):
            _cfg(enable_semantic_routing=True, enable_intent_orchestration=False)

        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


class TestExplicitValuesUntouched:
    """명시값은 자동 해석이 **덮어쓰지 않는다** — 경고보다 이쪽이 본질이다."""

    @pytest.mark.parametrize("value", [True, False])
    def test_explicit_semantic_routing_survives(self, value):
        cfg = _cfg(enable_semantic_routing=value)
        assert cfg.enable_semantic_routing is value

    @pytest.mark.parametrize("value", [True, False])
    def test_explicit_orchestration_survives(self, value):
        cfg = _cfg(enable_intent_orchestration=value)
        assert cfg.enable_intent_orchestration is value

    def test_explicit_false_is_not_auto_upgraded(self):
        """False는 '미입력'이 아니다 — 멀티 DB가 있어도 켜지면 안 된다."""
        cfg = _cfg(enable_semantic_routing=False,
                   multi_db={"connections": {"db1": "postgresql://h/d"}})
        assert cfg.enable_semantic_routing is False

    def test_resolved_by_marks_auto_resolution(self):
        assert _cfg(enable_semantic_routing=None)._orchestration_resolved_by == "auto_multidb"
        assert _cfg()._orchestration_resolved_by == "explicit_env"


def _multi_db_cfg(**kwargs) -> AppConfig:
    """멀티 DB(활성 2건)가 연결된 설정. `.env` 누수를 끊고 1단 플래그는 off로 고정한다."""
    kwargs.setdefault("enable_deepagents_package", False)
    return AppConfig(
        _env_file=None,
        multi_db=MultiDBConfig(_env_file=None, active_db_ids_csv="db_a,db_b"),
        **kwargs,
    )


class TestIntentOrchestrationCodeDefault:
    """D-251 ① — 2단 플래그 미입력은 DB 등록과 무관하게 on이고 기준 단(2단)으로 확정된다."""

    def test_none_is_on_with_multi_db(self):
        cfg = _multi_db_cfg(enable_semantic_routing=True, enable_intent_orchestration=None)

        assert cfg.multi_db.get_active_db_ids() == ["db_a", "db_b"]
        assert cfg.enable_intent_orchestration is True

    def test_none_is_on_without_db(self):
        """DB 등록과 무관하다 — 종전 X-T11(DB 등록만으로 단이 바뀜)은 재발하지 않는다."""
        cfg = AppConfig(
            _env_file=None,
            multi_db=MultiDBConfig(_env_file=None, active_db_ids_csv=""),
            enable_semantic_routing=True,
            enable_intent_orchestration=None,
            enable_deepagents_package=False,
        )

        assert cfg.enable_intent_orchestration is True

    def test_resolved_by_is_code_default_when_only_intent_is_unset(self):
        cfg = _multi_db_cfg(enable_semantic_routing=True, enable_intent_orchestration=None)

        assert cfg._orchestration_resolved_by == "code_default"

    def test_unset_intent_resolves_to_tier2(self):
        cfg = _multi_db_cfg(enable_semantic_routing=True, enable_intent_orchestration=None)

        got = resolve_ladder_tier(cfg, backend="semantic_router", buildable=False)

        assert got == (LadderTier.INTENT_ORCHESTRATION, "intent_flag_on")
        assert got[0].is_canonical

    def test_both_unset_with_multi_db_resolves_to_tier2(self):
        """설정 미입력 + 멀티 DB — 2단 기본 on(기준) · 3단 자동 on · 출처는 auto_multidb."""
        cfg = _multi_db_cfg(enable_semantic_routing=None, enable_intent_orchestration=None)

        assert (cfg.enable_semantic_routing, cfg.enable_intent_orchestration) == (True, True)
        assert cfg._orchestration_resolved_by == "auto_multidb"
        assert resolve_ladder_tier(cfg, backend="semantic_router", buildable=False) == (
            LadderTier.INTENT_ORCHESTRATION, "intent_flag_on"
        )

    def test_explicit_false_confirms_tier3_comparison_arm(self):
        """명시 false면 3단(비교 arm)이다 — 코드 기본값이 덮지 않는다."""
        cfg = _multi_db_cfg(enable_semantic_routing=True, enable_intent_orchestration=False)

        assert cfg.enable_intent_orchestration is False
        assert resolve_ladder_tier(cfg, backend="semantic_router", buildable=False) == (
            LadderTier.SEMANTIC_ROUTER, "none"
        )

    def test_explicit_true_still_confirms_tier2(self):
        """명시 true는 코드 기본값이 덮지 않는다 — 운영자가 고른 2단이다."""
        cfg = _multi_db_cfg(enable_semantic_routing=True, enable_intent_orchestration=True)

        assert cfg.enable_intent_orchestration is True
        assert cfg._orchestration_resolved_by == "explicit_env"
        assert resolve_ladder_tier(cfg, backend="semantic_router", buildable=False) == (
            LadderTier.INTENT_ORCHESTRATION, "intent_flag_on"
        )

    def test_warning_says_on_and_how_to_opt_out(self, caplog):
        """경고는 '멀티 DB로 자동 결정'이 아니라 'on 확정(기준 단) + 3단으로 끄는 방법'을 말한다."""
        with caplog.at_level(logging.WARNING, logger="src.config"):
            _multi_db_cfg(enable_semantic_routing=True, enable_intent_orchestration=None)

        msgs = [r.getMessage() for r in caplog.records
                if r.levelno >= logging.WARNING and "enable_intent_orchestration" in r.getMessage()]
        assert len(msgs) == 1, msgs
        assert "on으로 확정" in msgs[0]
        assert "ENABLE_INTENT_ORCHESTRATION=false" in msgs[0]
        assert "멀티 DB" not in msgs[0]
