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
    """반환 계약은 CU-16으로 `(에러, 보정된 SQL)` 튜플이 됐다 — 플래그 의미는 그대로다."""

    def test_flag_off_keeps_simple_behavior(self):
        """OFF: 간이 검증 그대로 — 미존재 테이블 SQL이 통과한다(종전 동작 고정)."""
        error, _fixed = _validate_sql(
            _SQL_UNKNOWN_TABLE, _SCHEMA,
            db_id="db_a", app_config=_cfg(False),
        )
        assert error is None

    def test_no_config_defaults_to_simple(self):
        """app_config 부재 시에도 간이 검증으로 폴백한다(안전 기본값)."""
        assert _validate_sql(_SQL_UNKNOWN_TABLE, _SCHEMA)[0] is None

    def test_flag_on_rejects_unknown_table(self):
        """ON: full validator가 미존재 테이블을 거부한다(단일 경로 대칭)."""
        reason, fixed = _validate_sql(
            _SQL_UNKNOWN_TABLE, _SCHEMA,
            db_id="db_a", app_config=_cfg(True),
        )
        assert reason and "ghost_table" in reason
        assert fixed is None          # 거부한 SQL의 보정본을 내보내지 않는다

    def test_flag_on_passes_valid_sql(self):
        """ON: 정상 SQL은 통과한다(위양성 없는 기본 케이스)."""
        assert _validate_sql(
            "SELECT hostname FROM servers LIMIT 10", _SCHEMA,
            db_id="db_a", app_config=_cfg(True),
        )[0] is None


class TestCU16RowLimitParity:
    """행 상한이 단일 경로와 **대칭**인가 (plans/98 CU-16).

    D-176이 멀티 경로에 방언 그물을 놓으면서 *"행 제한 절 부재는 문제로 보지 않는다 —
    상향은 별 관심사"*로 남겨 둔 구멍이다. CU-2가 단일 경로에서 닫은 것과 같은 함수·같은
    경계값을 쓴다. 이 run 에서 3-DB 팬아웃은 유효 턴의 24%(66/280)였다.
    """

    _NO_LIMIT = "SELECT hostname FROM servers"

    def test_기본_경로도_행_상한을_붙인다(self):
        """`multi_full_validation` **기본값(False)** 에서도 붙어야 한다 — 그게 기본 경로다."""
        error, fixed = _validate_sql(
            self._NO_LIMIT, _SCHEMA, db_id="db_a", app_config=_cfg(False),
        )
        assert error is None
        assert fixed is not None and "LIMIT 100" in fixed

    def test_전체_조회_질의는_상향된다(self):
        """CU-2와 같은 판정·같은 상수(`_ALL_QUERY_LIMIT`) — 생략이 아니라 상향이다."""
        from src.utils.query_gen_common import _ALL_QUERY_LIMIT

        _error, fixed = _validate_sql(
            self._NO_LIMIT, _SCHEMA, db_id="db_a",
            user_query="모든 서버 조회", app_config=_cfg(False),
        )
        assert fixed is not None and f"LIMIT {_ALL_QUERY_LIMIT}" in fixed

    def test_DB2_는_FETCH_FIRST_로_붙는다(self):
        """방언은 `_add_limit_clause`가 처리한다 — 문자열 치환이 아니라 같은 함수 재사용."""
        _error, fixed = _validate_sql(
            self._NO_LIMIT, _SCHEMA, db_id="db_b",
            db_engine="db2", app_config=_cfg(False),
        )
        assert fixed is not None and "FETCH FIRST 100 ROWS ONLY" in fixed

    def test_이미_있는_절은_건드리지_않는다(self):
        """부재일 때만 손댄다 — 중첩·OFFSET 동반 형태의 치환 위험을 만들지 않는다(D-176)."""
        _error, fixed = _validate_sql(
            "SELECT hostname FROM servers LIMIT 5", _SCHEMA,
            db_id="db_a", app_config=_cfg(False),
        )
        assert fixed is None

    def test_거부된_SQL_에는_상한을_붙이지_않는다(self):
        """에러가 먼저다 — 금지 키워드 SQL을 보정해서 내보내면 안 된다."""
        error, fixed = _validate_sql(
            "DELETE FROM servers", _SCHEMA, db_id="db_a", app_config=_cfg(False),
        )
        assert error and fixed is None

    def test_full_validation_ON_이면_보정본을_버리지_않는다(self):
        """**CU-16의 출발점.** 종전에는 `outcome.auto_fixed_sql`을 읽지 않아 켜나 마나였다."""
        _error, fixed = _validate_sql(
            self._NO_LIMIT, _SCHEMA, db_id="db_a", app_config=_cfg(True),
        )
        assert fixed is not None and "LIMIT 100" in fixed
