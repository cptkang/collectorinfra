"""Plan 67 Phase 0 ⑫ — 알람 테스트 요청 db_id 기본값 설정화 검증.

라우트에 하드코딩돼 있던 `"polestar_b0"`을 `ALARM_DEFAULT_TEST_DB_ID` 설정으로 옮겼다.
설정 미지정 시 기존 값이 유지되어(동작 불변) 기존 클라이언트가 영향받지 않아야 한다.
"""

from __future__ import annotations

from types import SimpleNamespace

import src.config
from src.api.routes.alarm import AlarmTestRequest, _default_test_db_id
from src.config import AlarmConfig, load_config


def test_config_default_preserves_current_value():
    """설정 미지정 시 기존 하드코딩 값과 동일해야 한다."""
    assert AlarmConfig(_env_file=None).default_test_db_id == "polestar_b0"


def test_field_default_is_wired_to_config():
    """db_id 기본값이 리터럴이 아니라 설정 조회 함수에 배선돼 있어야 한다."""
    assert AlarmTestRequest.model_fields["db_id"].default_factory is _default_test_db_id


def test_default_follows_config_value(monkeypatch):
    """db_id 생략 시 설정값이 요청마다 반영된다."""
    monkeypatch.setattr(
        src.config,
        "load_config",
        lambda: SimpleNamespace(alarm=SimpleNamespace(default_test_db_id="other_zone_db")),
    )
    assert _default_test_db_id() == "other_zone_db"
    assert AlarmTestRequest().db_id == "other_zone_db"


def test_explicit_db_id_wins():
    """명시적으로 준 db_id는 그대로 사용된다."""
    assert AlarmTestRequest(db_id="polestar_cm_yd").db_id == "polestar_cm_yd"


def test_default_matches_runtime_config():
    """설정 미변경 환경에서는 기존 기본값과 동일하다(동작 불변)."""
    assert AlarmTestRequest().db_id == load_config().alarm.default_test_db_id
