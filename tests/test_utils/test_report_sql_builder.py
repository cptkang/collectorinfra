"""report_sql_builder 테스트 (D-038 Phase 2).

양식 필드 분류 → 결정적 SQL 생성, 폴백(None) 조건, 실제 yd 프로필 통합을 검증한다.
"""

import os

import pytest
import sqlparse
import yaml

from src.utils.metric_classifier import load_metric_patterns
from src.utils.report_sql_builder import build_polestar_report_sql


@pytest.fixture
def metric_patterns() -> dict:
    return {
        "stat_tables": {
            "hour": {"table": "cmm_metric_stat_h", "stat_date_format": "YYYYMMDDHH"},
            "day": {"table": "cmm_metric_stat_d", "stat_date_format": "YYYYMMDD"},
            "month": {"table": "cmm_metric_stat_m", "stat_date_format": "YYYYMM"},
        },
        "default_resolution": "month",
        "value_columns": {"min": "min_val", "avg": "avg_val", "max": "max_val"},
        "aggregations": {
            "avg": ["평균", "avg"],
            "max": ["최대", "최고", "max"],
            "min": ["최소", "min"],
        },
        "metrics": [
            {"name": "cpu_utilization", "resource_type": "server.Cpus",
             "definition_name": "Utilization", "unit": "%",
             "domain_terms": ["CPU"], "synonyms": ["CPU 사용률"]},
            {"name": "memory_utilization", "resource_type": "server.Memory",
             "definition_name": "Utilization", "unit": "%",
             "domain_terms": ["메모리"], "synonyms": ["메모리 사용률"]},
        ],
    }


@pytest.fixture
def known_attrs() -> list:
    return [
        {"name": "OSType", "description": "운영체제 종류 [resource_type: server.Server]",
         "synonyms": ["운영체제"]},
        {"name": "OSVerson", "description": "운영체제 버전 [resource_type: server.Server]",
         "synonyms": ["OS버전"]},
        {"name": "Hostname", "description": "호스트명 [resource_type: server.Server]",
         "synonyms": ["호스트네임"]},
        {"name": "IPaddress", "description": "IP [resource_type: server.Server]",
         "synonyms": ["IP"]},
        {"name": "LOGICALCORE", "description": "논리 코어 [resource_type: server.Cpus]",
         "synonyms": ["코어수"]},
        {"name": "TotalSize",
         "description": "메모리 크기 [resource_type: server.Memory] / 디스크 용량 [resource_type: server.Disks]",
         "synonyms": ["메모리", "디스크용량"]},
    ]


@pytest.fixture
def value_joins() -> list:
    return [
        {"eav_attribute": "Hostname", "entity_column": "hostname"},
        {"eav_attribute": "IPaddress", "entity_column": "ipaddress"},
    ]


@pytest.fixture
def user_mapping() -> dict:
    """사용자가 겪은 그 양식의 컬럼 매핑(field_mapper 산출 가정)."""
    return {
        "서버 이름": "cmm_resource.name",
        "호스트네임": "cmm_resource.hostname",
        "IP주소": "EAV:IPaddress",
        "CPU 코어 수": "EAV:LOGICALCORE",
        "메모리 용량": "EAV:TotalSize",
        "OS 종류": "EAV:OSType",
        "OS 버전": "EAV:OSVerson",
        "CPU 평균": None,
        "CPU 최고": None,
        "메모리 평균": None,
        "메모리 최고": None,
    }


class TestClassificationAndBuild:
    def test_full_user_template(self, metric_patterns, known_attrs, value_joins, user_mapping):
        result = build_polestar_report_sql(
            list(user_mapping.keys()), user_mapping, metric_patterns, known_attrs,
            value_joins=value_joins, resolution="month", limit=100000,
        )
        assert result is not None, "전 필드 분류 성공 시 SQL 생성"
        cls = result.classifications

        # 직접컬럼 (식별)
        assert cls["서버 이름"] == ("direct", "name")
        assert cls["호스트네임"] == ("direct", "hostname")
        # IPaddress는 value_joins로 직접컬럼 대체 (공동존 안전)
        assert cls["IP주소"] == ("direct", "ipaddress")
        # EAV
        assert cls["CPU 코어 수"] == ("eav", ("server.Cpus", "LOGICALCORE"))
        assert cls["OS 종류"] == ("eav", ("server.Server", "OSType"))
        assert cls["OS 버전"] == ("eav", ("server.Server", "OSVerson"))
        # TotalSize 모호성 → "메모리 용량" 도메인어로 server.Memory 해소
        assert cls["메모리 용량"] == ("eav", ("server.Memory", "TotalSize"))
        # 메트릭
        assert cls["CPU 평균"][0] == "metric" and cls["CPU 평균"][1].aggregation == "avg"
        assert cls["CPU 최고"][1].aggregation == "max"
        assert cls["메모리 평균"][1].name == "memory_utilization"

    def test_sql_structure(self, metric_patterns, known_attrs, value_joins, user_mapping):
        sql = build_polestar_report_sql(
            list(user_mapping.keys()), user_mapping, metric_patterns, known_attrs,
            value_joins=value_joins, resolution="month", limit=100000,
        ).sql

        # 단일 statement로 파싱 가능
        parsed = sqlparse.parse(sql)
        assert len(parsed) == 1

        # 메트릭은 LEFT JOIN (server.Server 탈락 방지, D-037)
        assert "LEFT JOIN polestar.cmm_metric_stat_m s" in sql
        assert "INNER JOIN" not in sql.upper()
        # EAV용 core_config_prop LEFT JOIN
        assert "LEFT JOIN polestar.core_config_prop cc" in sql
        # OSVerson 오탈자 원본 유지
        assert "cc.name = 'OSVerson'" in sql
        assert "'OSVersion'" not in sql
        # IP는 직접컬럼
        assert "THEN res.ipaddress END" in sql
        # resource_type 범위
        assert "'server.Server'" in sql and "'server.Cpus'" in sql and "'server.Memory'" in sql
        # 그룹/제한
        assert "GROUP BY COALESCE(res.platform_resource_id, res.id)" in sql
        assert "LIMIT 100000" in sql
        assert "s.definition_name = 'Utilization'" in sql

    def test_field_aliases_match_select(self, metric_patterns, known_attrs, value_joins, user_mapping):
        result = build_polestar_report_sql(
            list(user_mapping.keys()), user_mapping, metric_patterns, known_attrs,
            value_joins=value_joins,
        )
        # 메트릭 필드는 깨끗한 alias로 column_mapping 갱신됨
        assert result.field_aliases["CPU 평균"] == "metric_cpu_utilization_avg"
        assert result.field_aliases["메모리 용량"] == "EAV:TotalSize"
        assert result.field_aliases["호스트네임"] == "cmm_resource.hostname"
        # 모든 alias가 SELECT에 등장
        for alias in result.field_aliases.values():
            assert f'AS "{alias}"' in result.sql


class TestFallbackToNone:
    def test_unmapped_non_metric_field(self, metric_patterns, known_attrs):
        m = {"이상한 필드": None}
        assert build_polestar_report_sql(list(m), m, metric_patterns, known_attrs) is None

    def test_ambiguous_resource_type(self, metric_patterns, known_attrs):
        # "용량"만으로는 TotalSize가 Memory/Disks 모호 → 폴백
        m = {"용량": "EAV:TotalSize"}
        assert build_polestar_report_sql(list(m), m, metric_patterns, known_attrs) is None

    def test_no_metric_patterns(self, known_attrs):
        m = {"호스트네임": "cmm_resource.hostname"}
        assert build_polestar_report_sql(list(m), m, {}, known_attrs) is None

    def test_disallowed_direct_column(self, metric_patterns, known_attrs):
        m = {"이상": "cmm_resource.secret_column"}
        assert build_polestar_report_sql(list(m), m, metric_patterns, known_attrs) is None

    def test_empty_fields(self, metric_patterns, known_attrs):
        assert build_polestar_report_sql([], {}, metric_patterns, known_attrs) is None


class TestIdentityOnly:
    """메트릭/ EAV 없이 직접컬럼만 — cc/메트릭 조인 생략."""

    def test_direct_only(self, metric_patterns, known_attrs, value_joins):
        m = {"서버 이름": "cmm_resource.name", "호스트네임": "cmm_resource.hostname"}
        sql = build_polestar_report_sql(
            list(m), m, metric_patterns, known_attrs, value_joins=value_joins,
        ).sql
        assert "core_config_prop" not in sql   # EAV 없음 → cc 조인 생략
        assert "cmm_metric_stat" not in sql     # 메트릭 없음 → 조인 생략
        assert "FROM polestar.cmm_resource res" in sql


class TestRealProfileIntegration:
    @pytest.fixture
    def yd_profile(self) -> dict:
        path = os.path.join(
            os.path.dirname(__file__), "..", "..",
            "config", "db_profiles", "polestar_cm_yd.yaml",
        )
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    def test_build_from_real_profile(self, yd_profile, user_mapping):
        metric_patterns = load_metric_patterns(yd_profile)
        eav = next(p for p in yd_profile["patterns"] if p.get("type") == "eav")
        known = eav.get("known_attributes_detail") or eav.get("known_attributes")
        value_joins = eav.get("value_joins")

        result = build_polestar_report_sql(
            list(user_mapping.keys()), user_mapping, metric_patterns, known,
            value_joins=value_joins, resolution="month", limit=100000,
        )
        assert result is not None
        sql = result.sql
        assert sqlparse.parse(sql)
        assert "LEFT JOIN polestar.cmm_metric_stat_m s" in sql
        assert "cc.name = 'OSVerson'" in sql
        assert "THEN res.ipaddress END" in sql       # IP 직접컬럼
        assert "THEN res.hostname END" in sql        # 호스트명 직접컬럼
        assert "THEN res.name END" in sql            # 서버이름 직접컬럼
        # 메모리 용량은 server.Memory EAV로 정확 해소
        assert "res.resource_type = 'server.Memory' AND cc.name = 'TotalSize'" in sql
