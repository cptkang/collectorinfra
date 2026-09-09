"""D-201 '이번 달' stat_m 반려 가드 — 폴스타 검증기 결정적 안전망.

프로필 query_guide 규칙(stat_d 당월 1일~어제 집계)의 LLM 순응은 비결정이므로,
검증기가 stat_m 당월 조회를 반려해 재생성 힌트를 준다(4차 실측 — 규칙만으로는
stat_m 당월 생성이 재현됨). 발동은 기간 해석이 정확히 (당월, 당월)일 때로 좁힌다.
"""

from src.db_adapters.polestar.adapter import PolestarAdapter
from src.db_adapters.polestar.validators import check_current_month_stat_table

_STAT_M_SQL = (
    "SELECT r.name, s.avg_val FROM polestar.cmm_metric_stat_m s "
    "JOIN polestar.cmm_resource r ON r.id = s.resource_id "
    "WHERE s.definition_name = 'Utilization'"
)
_STAT_D_SQL = (
    "SELECT r.name, ROUND(AVG(s.avg_val)::numeric, 2) AS cpu_avg "
    "FROM polestar.cmm_metric_stat_d s "
    "JOIN polestar.cmm_resource r ON r.id = s.resource_id "
    "GROUP BY r.name"
)
_CONFIG_SQL = "SELECT name, hostname FROM polestar.cmm_resource WHERE dtime IS NULL"


class TestCheckCurrentMonthStatTable:
    def test_current_month_query_with_stat_m_rejected(self):
        errors = check_current_month_stat_table(
            _STAT_M_SQL, "이번 달 서버별 CPU 사용률 보여줘"
        )
        assert errors
        assert "cmm_metric_stat_d" in errors[0]
        assert "당월 1일" in errors[0]

    def test_current_month_query_with_stat_d_passes(self):
        assert check_current_month_stat_table(
            _STAT_D_SQL, "이번 달 서버별 CPU 사용률 보여줘"
        ) == []

    def test_last_month_query_with_stat_m_passes(self):
        """직전월 질의의 stat_m은 정상 경로 — 미발동."""
        assert check_current_month_stat_table(
            _STAT_M_SQL, "지난달 서버별 CPU 사용률 보여줘"
        ) == []

    def test_range_query_with_stat_m_passes(self):
        """기간 범위 질의((당월,당월)가 아님)는 stat_m 사용이 정당 — 미발동."""
        assert check_current_month_stat_table(
            _STAT_M_SQL, "지난 3개월 서버별 CPU 사용률 통계"
        ) == []

    def test_current_month_non_stat_sql_passes(self):
        """stat_m 미참조 SQL(서버 목록 등)은 미발동."""
        assert check_current_month_stat_table(
            _CONFIG_SQL, "이번 달 등록된 서버 목록"
        ) == []


class TestAdapterWiring:
    def test_check_added_only_with_user_query(self):
        """user_query 미지정이면 종전 목록 그대로(하위 호환) — 지정 시 1건 추가."""
        adapter = PolestarAdapter()
        base = adapter.validator_checks()
        with_query = adapter.validator_checks(user_query="이번 달 CPU 사용률")
        assert len(with_query) == len(base) + 1

    def test_closure_binds_user_query(self):
        adapter = PolestarAdapter()
        checks = adapter.validator_checks(user_query="이번 달 서버별 CPU 사용률")
        errors = [e for c in checks for e in c(_STAT_M_SQL)]
        assert any("cmm_metric_stat_d" in e for e in errors)
