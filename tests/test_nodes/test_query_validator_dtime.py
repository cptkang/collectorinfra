"""cmm_resource 조회 시 dtime IS NULL 필터 부재 결정적 검출 (D-087 계열).

폐쇄망 실측(2026-07-21 b0-005): LLM 폴백 SQL이 dtime 필터를 통째로 누락해 삭제된 서버
약 99대가 결과에 섞임(rows 475 vs 골드 376). 프롬프트 규칙만으로는 비결정적으로 재발하므로
단일(query_validator)·멀티(_validate_sql_simple) 검증 경로가 공유하는 결정적 가드로 차단한다.
"""

from __future__ import annotations

from src.utils.query_gen_common import MISSING_DTIME_ERROR, missing_dtime_filter


class TestMissingDtimeFilter:
    def test_missing_filter_detected(self):
        """b0-005 실측 pred 형태 — cmm_resource 조회인데 dtime 필터 전무 → 검출."""
        sql = """
        SELECT svr.name AS server_name, svr.hostname AS hostname
        FROM POLESTAR.cmm_resource r
        JOIN POLESTAR.cmm_resource svr ON svr.id = r.platform_resource_id
        JOIN POLESTAR.cmm_metric_stat_m s ON r.id = s.resource_id
        WHERE r.resource_type = 'server.Memory' AND s.max_val > 90
        GROUP BY svr.name, svr.hostname
        """
        assert missing_dtime_filter(sql) is True

    def test_present_filter_passes(self):
        sql = """
        SELECT r.hostname FROM polestar.cmm_resource r
        WHERE r.resource_type = 'server.Server' AND r.dtime IS NULL
        """
        assert missing_dtime_filter(sql) is False

    def test_single_dtime_suffices_for_alarm_view(self):
        """알람 표준 뷰 — CR만 dtime 필터, 부모 SVR LEFT JOIN은 무필터가 정당(오탐 금지)."""
        sql = """
        SELECT SVR.NAME, CR.NAME FROM polestar.cmm_resource CR
        JOIN polestar.cmm_alarm CA ON CA.RESOURCE_ID = CR.ID
        LEFT JOIN polestar.cmm_resource SVR
            ON SVR.ID = COALESCE(CR.PLATFORM_RESOURCE_ID, CR.SERVICE_RESOURCE_ID, CR.ID)
        WHERE CR.DTIME IS NULL AND CA.ALARMSEVERITY IN (2, 3)
        """
        assert missing_dtime_filter(sql) is False

    def test_no_cmm_resource_not_flagged(self):
        assert missing_dtime_filter(
            "SELECT COUNT(*) FROM polestar.cmm_alarm WHERE ALARMSEVERITY = 3"
        ) is False

    def test_dtime_only_in_comment_still_flagged(self):
        """주석 속 dtime 표기는 필터가 아니다 — 제거 후 검사."""
        sql = """
        -- dtime is null 필터는 생략
        SELECT r.hostname FROM polestar.cmm_resource r
        """
        assert missing_dtime_filter(sql) is True

    def test_empty_or_none_safe(self):
        assert missing_dtime_filter("") is False
        assert missing_dtime_filter(None) is False


class TestSharedAcrossPaths:
    def test_multi_db_simple_validator_flags_missing_dtime(self):
        """멀티 경로(_validate_sql_simple)도 동일 가드를 공유한다(D-066)."""
        from src.nodes.multi_db_executor import _validate_sql_simple

        sql = "SELECT r.hostname FROM polestar.cmm_resource r WHERE r.resource_type = 'server.Server'"
        assert _validate_sql_simple(sql, {}) == MISSING_DTIME_ERROR

    def test_multi_db_simple_validator_passes_with_dtime(self):
        from src.nodes.multi_db_executor import _validate_sql_simple

        sql = (
            "SELECT r.hostname FROM polestar.cmm_resource r "
            "WHERE r.resource_type = 'server.Server' AND r.dtime IS NULL"
        )
        assert _validate_sql_simple(sql, {}) is None
