"""metric_classifier 유틸리티 테스트 (D-038 Phase 1).

양식 필드 → 메트릭 분류, 집계 감지, 통계 테이블 해석을 검증한다.
마지막에 실제 polestar_cm_yd.yaml의 metric_patterns로 사용자 양식 컬럼을 분류하는
통합 테스트를 포함한다.
"""

import os

import pytest
import yaml

from src.utils.metric_classifier import (
    MetricFieldSpec,
    StatTable,
    classify_metric_field,
    detect_aggregation,
    load_metric_patterns,
    resolve_stat_table,
)


@pytest.fixture
def metric_patterns() -> dict:
    """테스트용 metric_patterns 메타데이터 (프로필과 동일 구조)."""
    return {
        "stat_tables": {
            "hour": {"table": "cmm_metric_stat_h", "stat_date_format": "YYYYMMDDHH"},
            "day": {"table": "cmm_metric_stat_d", "stat_date_format": "YYYYMMDD"},
            "month": {"table": "cmm_metric_stat_m", "stat_date_format": "YYYYMM"},
        },
        "default_resolution": "month",
        "join": {
            "resource_id_column": "resource_id",
            "server_link_column": "platform_resource_id",
        },
        "value_columns": {"min": "min_val", "avg": "avg_val", "max": "max_val"},
        "aggregations": {
            "avg": ["평균", "avg", "average", "mean"],
            "max": ["최대", "최고", "피크", "max", "peak"],
            "min": ["최소", "최저", "min"],
        },
        "metrics": [
            {
                "name": "cpu_utilization",
                "resource_type": "server.Cpus",
                "definition_name": "Utilization",
                "unit": "%",
                "domain_terms": ["CPU", "씨피유", "프로세서"],
                "synonyms": ["CPU 사용률", "CPU 사용율", "프로세서 사용률"],
            },
            {
                "name": "memory_utilization",
                "resource_type": "server.Memory",
                "definition_name": "Utilization",
                "unit": "%",
                "domain_terms": ["메모리", "MEM", "RAM"],
                "synonyms": ["메모리 사용률", "RAM 사용률"],
            },
            {
                "name": "filesystem_utilization",
                "resource_type": "server.FileSystems",
                "definition_name": "Utilization",
                "unit": "%",
                "domain_terms": ["파일시스템", "FS"],
                "synonyms": ["파일시스템 사용률", "디스크 사용률"],
            },
            {
                "name": "disk_io",
                "resource_type": "server.Disks",
                "definition_name": "MaxIORate",
                "unit": "",
                "domain_terms": [],
                "synonyms": ["디스크 IO", "디스크 I/O", "MaxIORate"],
            },
        ],
    }


class TestDetectAggregation:
    def test_avg_keywords(self, metric_patterns):
        assert detect_aggregation("CPU 평균", metric_patterns) == "avg"
        assert detect_aggregation("average usage", metric_patterns) == "avg"

    def test_max_keywords(self, metric_patterns):
        assert detect_aggregation("CPU 최고", metric_patterns) == "max"
        assert detect_aggregation("메모리 최대", metric_patterns) == "max"
        assert detect_aggregation("피크 사용률", metric_patterns) == "max"

    def test_min_keyword(self, metric_patterns):
        assert detect_aggregation("CPU 최소", metric_patterns) == "min"

    def test_default_when_no_keyword(self, metric_patterns):
        assert detect_aggregation("CPU 코어 수", metric_patterns) == "avg"
        assert detect_aggregation("CPU 코어 수", metric_patterns, default=None) is None


class TestClassifyMetricField:
    def test_domain_plus_aggregation(self, metric_patterns):
        """도메인어 + 집계어 조합으로 메트릭 분류."""
        spec = classify_metric_field("CPU 평균", metric_patterns)
        assert spec is not None
        assert spec.name == "cpu_utilization"
        assert spec.resource_type == "server.Cpus"
        assert spec.definition_name == "Utilization"
        assert spec.aggregation == "avg"
        assert spec.value_column == "avg_val"
        assert spec.unit == "%"

    def test_cpu_max(self, metric_patterns):
        spec = classify_metric_field("CPU 최고", metric_patterns)
        assert spec.name == "cpu_utilization"
        assert spec.aggregation == "max"
        assert spec.value_column == "max_val"

    def test_memory_avg_and_max(self, metric_patterns):
        avg = classify_metric_field("메모리 평균", metric_patterns)
        assert avg.name == "memory_utilization" and avg.aggregation == "avg"
        mx = classify_metric_field("메모리 최고", metric_patterns)
        assert mx.name == "memory_utilization" and mx.aggregation == "max"

    def test_full_synonym_without_aggregation_defaults_avg(self, metric_patterns):
        """완전 동의어 매칭 시 집계어 없으면 기본 avg."""
        spec = classify_metric_field("CPU 사용률", metric_patterns)
        assert spec.name == "cpu_utilization"
        assert spec.aggregation == "avg"

    def test_disk_usage_maps_to_filesystem(self, metric_patterns):
        """'디스크 사용률'은 파일시스템 Utilization으로 매핑(query_guide 규칙)."""
        spec = classify_metric_field("디스크 사용률", metric_patterns)
        assert spec.name == "filesystem_utilization"

    def test_disk_io_only_via_synonym(self, metric_patterns):
        spec = classify_metric_field("디스크 IO 평균", metric_patterns)
        assert spec.name == "disk_io"
        assert spec.aggregation == "avg"

    @pytest.mark.parametrize(
        "field",
        ["CPU 코어 수", "메모리 용량", "서버 이름", "호스트네임", "IP주소",
         "OS 종류", "OS 버전", "시리얼번호"],
    )
    def test_non_metric_fields_return_none(self, metric_patterns, field):
        """집계어/완전동의어가 없는 정적 속성은 메트릭이 아님."""
        assert classify_metric_field(field, metric_patterns) is None

    def test_empty_inputs(self, metric_patterns):
        assert classify_metric_field("", metric_patterns) is None
        assert classify_metric_field("CPU 평균", {}) is None


class TestResolveStatTable:
    def test_default_resolution_month(self, metric_patterns):
        st = resolve_stat_table(metric_patterns)
        assert st == StatTable(table="cmm_metric_stat_m", stat_date_format="YYYYMM")

    def test_explicit_resolutions(self, metric_patterns):
        assert resolve_stat_table(metric_patterns, "hour").table == "cmm_metric_stat_h"
        assert resolve_stat_table(metric_patterns, "day").table == "cmm_metric_stat_d"

    def test_unknown_resolution_falls_back_to_default(self, metric_patterns):
        st = resolve_stat_table(metric_patterns, "weekly")
        assert st.table == "cmm_metric_stat_m"

    def test_empty_patterns(self):
        assert resolve_stat_table({}) is None


class TestLoadMetricPatterns:
    def test_extract_from_structure_meta(self, metric_patterns):
        structure_meta = {"query_guide": "...", "metric_patterns": metric_patterns}
        assert load_metric_patterns(structure_meta) == metric_patterns

    def test_missing_or_none(self):
        assert load_metric_patterns(None) == {}
        assert load_metric_patterns({"query_guide": "..."}) == {}
        assert load_metric_patterns({"metric_patterns": "bad"}) == {}


class TestRealProfileIntegration:
    """실제 polestar_cm_yd.yaml의 metric_patterns로 사용자 양식 컬럼 분류."""

    @pytest.fixture
    def yd_patterns(self) -> dict:
        path = os.path.join(
            os.path.dirname(__file__), "..", "..",
            "config", "db_profiles", "polestar_cm_yd.yaml",
        )
        with open(path, encoding="utf-8") as f:
            profile = yaml.safe_load(f)
        return load_metric_patterns(profile)

    def test_user_template_columns(self, yd_patterns):
        """사용자 양식: 사용률 4컬럼만 메트릭, 나머지는 None."""
        assert yd_patterns, "metric_patterns가 프로필에 존재해야 함"

        metric_expect = {
            "CPU 평균": ("cpu_utilization", "avg"),
            "CPU 최고": ("cpu_utilization", "max"),
            "메모리 평균": ("memory_utilization", "avg"),
            "메모리 최고": ("memory_utilization", "max"),
        }
        for field, (name, agg) in metric_expect.items():
            spec = classify_metric_field(field, yd_patterns)
            assert spec is not None, f"{field}는 메트릭이어야 함"
            assert (spec.name, spec.aggregation) == (name, agg)

        non_metric = ["서버 이름", "호스트네임", "IP주소", "CPU 코어 수",
                      "메모리 용량", "OS 종류", "OS 버전"]
        for field in non_metric:
            assert classify_metric_field(field, yd_patterns) is None, (
                f"{field}는 메트릭이 아니어야 함"
            )
