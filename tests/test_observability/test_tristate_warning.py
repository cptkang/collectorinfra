"""tri-state 암묵 활성 경고 테스트 (plans/70 L3).

`enable_semantic_routing` · `enable_deepagent_orchestration`은 `bool | None`이다.
`None`이면 "멀티 DB 등록 여부"로 자동 결정되므로, **운영 실행 경로가 DB 등록 상태에
종속**된다 — DB를 하나 등록/해제하는 것만으로 확정 단이 바뀔 수 있다.

`model_post_init`이 `None`을 bool로 덮어쓰고 나면 명시 설정과 구별할 수 없다.
따라서 발동 사실은 **덮어쓰는 그 자리에서만** 남길 수 있다.
"""

from __future__ import annotations

import logging

import pytest

from src.config import AppConfig


def _cfg(**kwargs) -> AppConfig:
    """`.env` 누수를 막기 위해 판정 대상 필드를 명시해 생성한다."""
    kwargs.setdefault("enable_semantic_routing", False)
    kwargs.setdefault("enable_deepagent_orchestration", False)
    return AppConfig(**kwargs)


class TestAutoResolutionWarning:
    def test_warns_when_semantic_routing_is_auto_resolved(self, caplog):
        with caplog.at_level(logging.WARNING, logger="src.config"):
            _cfg(enable_semantic_routing=None)

        msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("enable_semantic_routing" in m for m in msgs), msgs

    def test_warns_when_orchestration_is_auto_resolved(self, caplog):
        with caplog.at_level(logging.WARNING, logger="src.config"):
            _cfg(enable_deepagent_orchestration=None)

        msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("enable_deepagent_orchestration" in m for m in msgs), msgs

    def test_warning_names_the_db_dependency(self, caplog):
        """경고가 '왜 문제인지'를 말해야 한다 — 값만 알려주면 조치할 수 없다."""
        with caplog.at_level(logging.WARNING, logger="src.config"):
            _cfg(enable_semantic_routing=None)

        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "멀티 DB" in joined or "multi_db" in joined

    def test_silent_when_both_explicitly_set(self, caplog):
        """명시 설정이면 자동 해석이 개입하지 않으므로 경고도 없다."""
        with caplog.at_level(logging.WARNING, logger="src.config"):
            _cfg(enable_semantic_routing=True, enable_deepagent_orchestration=False)

        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


class TestExplicitValuesUntouched:
    """명시값은 자동 해석이 **덮어쓰지 않는다** — 경고보다 이쪽이 본질이다."""

    @pytest.mark.parametrize("value", [True, False])
    def test_explicit_semantic_routing_survives(self, value):
        cfg = _cfg(enable_semantic_routing=value)
        assert cfg.enable_semantic_routing is value

    @pytest.mark.parametrize("value", [True, False])
    def test_explicit_orchestration_survives(self, value):
        cfg = _cfg(enable_deepagent_orchestration=value)
        assert cfg.enable_deepagent_orchestration is value

    def test_explicit_false_is_not_auto_upgraded(self):
        """False는 '미입력'이 아니다 — 멀티 DB가 있어도 켜지면 안 된다."""
        cfg = _cfg(enable_semantic_routing=False,
                   multi_db={"connections": {"db1": "postgresql://h/d"}})
        assert cfg.enable_semantic_routing is False

    def test_resolved_by_marks_auto_resolution(self):
        assert _cfg(enable_semantic_routing=None)._orchestration_resolved_by == "auto_multidb"
        assert _cfg()._orchestration_resolved_by == "explicit_env"
