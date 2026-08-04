"""멀티 DB 검증 강화 옵트인 회귀 테스트 (Plan 69 P4-3, §0.3-4).

플래그 OFF = 종전 간이 검증과 동작 동일(회귀 0), ON = 단일 경로와 같은 full
validator(테이블 존재·어댑터 훅 포함) 소비 — 같은 SQL이 단일에선 차단되고
멀티에선 통과하던 방어 비대칭의 해소를 고정한다.
"""

from types import SimpleNamespace

from src.nodes.multi_db_executor import _validate_sql

_SCHEMA = {"tables": {"servers": {"columns": [{"name": "hostname"}]}}}
# 간이 검증은 테이블 존재를 검사하지 않으므로 통과하고, full validator는 거부하는 SQL
_SQL_UNKNOWN_TABLE = "SELECT x FROM ghost_table LIMIT 10"


def _cfg(full: bool):
    return SimpleNamespace(
        text2sql=SimpleNamespace(multi_full_validation=full),
        query=SimpleNamespace(default_limit=100),
        get_polestar_db_ids=lambda: set(),
    )


class TestMultiFullValidationFlag:
    def test_flag_off_keeps_simple_behavior(self):
        """OFF: 간이 검증 그대로 — 미존재 테이블 SQL이 통과한다(종전 동작 고정)."""
        assert _validate_sql(
            _SQL_UNKNOWN_TABLE, _SCHEMA,
            db_id="db_a", app_config=_cfg(False),
        ) is None

    def test_no_config_defaults_to_simple(self):
        """app_config 부재 시에도 간이 검증으로 폴백한다(안전 기본값)."""
        assert _validate_sql(_SQL_UNKNOWN_TABLE, _SCHEMA) is None

    def test_flag_on_rejects_unknown_table(self):
        """ON: full validator가 미존재 테이블을 거부한다(단일 경로 대칭)."""
        reason = _validate_sql(
            _SQL_UNKNOWN_TABLE, _SCHEMA,
            db_id="db_a", app_config=_cfg(True),
        )
        assert reason and "ghost_table" in reason

    def test_flag_on_passes_valid_sql(self):
        """ON: 정상 SQL은 통과한다(위양성 없는 기본 케이스)."""
        assert _validate_sql(
            "SELECT hostname FROM servers LIMIT 10", _SCHEMA,
            db_id="db_a", app_config=_cfg(True),
        ) is None
