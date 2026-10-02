"""query_validator 내부 헬퍼의 직접 테스트 (Plan 69 P1-2 / §1.8 공백 보강).

- `_extract_alias_map`: 별칭 추출은 컬럼 존재 검증·금지 조인 검출이 모두 의존하는 1차
  파싱인데 직접 테스트가 없었다(경유 테스트만 존재).
- `_validate_forbidden_joins`: 패턴 1(정·역)·패턴 2(정·역)는 `test_structure_analysis.py`가
  덮지만 **패턴 3(excluded 컬럼이 config_table 아닌 임의 테이블과 조인)과 그 역방향**은
  미커버였다 — 함수 본문 실측으로 확인한 공백을 여기서 채운다.

현행 동작 고정이 목적이므로 함수 출력을 그대로 단언한다(버그 수정 아님).
"""

from __future__ import annotations

import pytest

from src.nodes.query_validator import _extract_alias_map, _validate_forbidden_joins


# ──────────────────────────────────────────────────────────────
# 1. _extract_alias_map — FROM/JOIN 별칭 추출
# ──────────────────────────────────────────────────────────────


class TestExtractAliasMap:
    def test_from_with_as_keyword(self):
        sql = "SELECT a.id FROM polestar.cmm_resource AS a WHERE a.dtime IS NULL"
        assert _extract_alias_map(sql) == {"a": "polestar.cmm_resource"}

    def test_from_bare_alias(self):
        sql = "SELECT r.id FROM cmm_resource r WHERE r.dtime IS NULL"
        assert _extract_alias_map(sql) == {"r": "cmm_resource"}

    def test_from_without_alias_yields_nothing(self):
        """별칭이 없으면 매핑도 없다(테이블명 자체가 참조자)."""
        assert _extract_alias_map("SELECT id FROM cmm_resource WHERE dtime IS NULL") == {}

    def test_comma_separated_multi_from(self):
        sql = (
            "SELECT a.id, b.id, c.id "
            "FROM polestar.t1 AS a, t2 b, t3 "
            "WHERE a.id = b.id"
        )
        assert _extract_alias_map(sql) == {"a": "polestar.t1", "b": "t2"}

    def test_join_alias_with_and_without_as(self):
        sql = (
            "SELECT r.hostname FROM cmm_resource r "
            "LEFT JOIN core_config_prop AS p ON p.configuration_id = r.id "
            "INNER JOIN cmm_vendor v ON v.id = r.vendor_id"
        )
        alias_map = _extract_alias_map(sql)
        assert alias_map["r"] == "cmm_resource"
        assert alias_map["p"] == "core_config_prop"
        assert alias_map["v"] == "cmm_vendor"

    def test_join_without_alias_is_skipped(self):
        sql = "SELECT * FROM t1 a JOIN t2 ON t2.id = a.id"
        assert _extract_alias_map(sql) == {"a": "t1"}

    def test_subquery_alias_is_captured_from_inner_from(self):
        """서브쿼리 별칭(`) hi`)은 잡히지 않고, 내부 FROM의 별칭이 매핑된다(현행 동작).

        `_extract_alias_map`은 FROM 뒤 식별자 토큰만 보므로 닫는 괄호로 끝나는 파생
        테이블 별칭은 인식하지 못한다. 컬럼 검증이 파생 테이블 컬럼을 "미존재"로 오판하지
        않는 것은 참조 테이블 집합에 없으면 검사 자체를 건너뛰기 때문이다.
        """
        sql = (
            "SELECT svr.hostname, hi.avg_val "
            "FROM cmm_resource svr "
            "LEFT JOIN (SELECT s.id, AVG(s.avg_val) AS avg_val FROM cmm_metric_stat_m s "
            "GROUP BY s.id) hi ON svr.id = hi.id"
        )
        alias_map = _extract_alias_map(sql)
        assert alias_map["svr"] == "cmm_resource"
        assert alias_map["s"] == "cmm_metric_stat_m"
        assert "hi" not in alias_map

    def test_keyword_after_table_is_not_taken_as_alias(self):
        """FROM 다음 토큰이 예약어면 별칭으로 취급하지 않는다."""
        assert _extract_alias_map("SELECT * FROM cmm_resource WHERE id = 1") == {}
        assert _extract_alias_map("SELECT id FROM cmm_resource GROUP BY id") == {}

    def test_case_insensitive_keywords(self):
        sql = "select a.id from polestar.cmm_resource as a left join core_config_prop p on p.id = a.id"
        assert _extract_alias_map(sql) == {"a": "polestar.cmm_resource", "p": "core_config_prop"}


# ──────────────────────────────────────────────────────────────
# 2. _validate_forbidden_joins — 패턴 3(임의 테이블 조인)·역방향
# ──────────────────────────────────────────────────────────────


def _eav_schema() -> dict:
    """excluded_join_columns를 가진 EAV 프로필 schema_info."""
    return {
        "tables": {
            "polestar.cmm_resource": {"columns": [{"name": "id", "type": "bigint"}]},
            "polestar.core_config_prop": {"columns": [{"name": "id", "type": "bigint"}]},
            "polestar.cmm_vendor": {"columns": [{"name": "id", "type": "bigint"}]},
        },
        "_structure_meta": {
            "patterns": [
                {
                    "type": "eav",
                    "entity_table": "cmm_resource",
                    "config_table": "core_config_prop",
                    "attribute_column": "name",
                    "value_column": "stringvalue_short",
                    "excluded_join_columns": [
                        {
                            "table": "cmm_resource",
                            "column": "resource_conf_id",
                            "reason": "운영 DB에서 NULL",
                        }
                    ],
                }
            ]
        },
    }


class TestForbiddenJoinsPattern3:
    """excluded 컬럼이 config_table이 아닌 레거시 lookup 테이블과 조인되는 경우."""

    def test_excluded_column_joined_to_arbitrary_table(self):
        sql = (
            "SELECT r.hostname FROM polestar.cmm_resource r "
            "LEFT JOIN polestar.cmm_vendor v ON r.resource_conf_id = v.id LIMIT 100"
        )
        errors = _validate_forbidden_joins(sql, _eav_schema())
        assert len(errors) == 1
        assert "resource_conf_id이 JOIN ON 절에 사용되었습니다" in errors[0]
        assert "운영 DB에서 NULL" in errors[0]

    def test_excluded_column_on_right_side_of_arbitrary_join(self):
        """역방향(오른쪽이 excluded 컬럼)도 동일하게 차단한다."""
        sql = (
            "SELECT r.hostname FROM polestar.cmm_resource r "
            "LEFT JOIN polestar.cmm_vendor v ON v.id = r.resource_conf_id LIMIT 100"
        )
        errors = _validate_forbidden_joins(sql, _eav_schema())
        assert len(errors) == 1
        assert "resource_conf_id이 JOIN ON 절에 사용되었습니다" in errors[0]

    def test_config_table_join_reports_only_pattern2_message(self):
        """config_table 상대 조인은 패턴 2 메시지 1건만 나온다(패턴 3과 배타)."""
        sql = (
            "SELECT r.hostname FROM polestar.cmm_resource r "
            "LEFT JOIN polestar.core_config_prop p ON r.resource_conf_id = p.configuration_id "
            "LIMIT 100"
        )
        errors = _validate_forbidden_joins(sql, _eav_schema())
        assert len(errors) == 1
        assert "core_config_prop과의 조인에 사용되었습니다" in errors[0]

    def test_excluded_column_outside_on_clause_is_ignored(self):
        """WHERE 절의 excluded 컬럼 사용은 조인이 아니므로 검출 대상이 아니다."""
        sql = (
            "SELECT r.hostname FROM polestar.cmm_resource r "
            "WHERE r.resource_conf_id IS NOT NULL LIMIT 100"
        )
        assert _validate_forbidden_joins(sql, _eav_schema()) == []

    def test_unaliased_table_reference_also_detected(self):
        """별칭 없이 테이블명을 그대로 쓴 조인도 같은 규칙으로 판정한다."""
        sql = (
            "SELECT hostname FROM cmm_resource "
            "LEFT JOIN cmm_vendor ON cmm_resource.resource_conf_id = cmm_vendor.id LIMIT 100"
        )
        errors = _validate_forbidden_joins(sql, _eav_schema())
        assert len(errors) == 1
        assert "resource_conf_id" in errors[0]

    @pytest.mark.parametrize("missing", ["entity_table", "config_table"])
    def test_incomplete_eav_pattern_is_skipped(self, missing):
        """entity/config 테이블 선언이 비면 그 패턴은 검사에서 제외된다."""
        schema = _eav_schema()
        schema["_structure_meta"]["patterns"][0][missing] = ""
        sql = (
            "SELECT r.hostname FROM polestar.cmm_resource r "
            "LEFT JOIN polestar.cmm_vendor v ON r.resource_conf_id = v.id LIMIT 100"
        )
        assert _validate_forbidden_joins(sql, schema) == []

    def test_excluded_entry_without_column_is_skipped(self):
        """excluded_join_columns 항목에 컬럼명이 없으면 건너뛴다(패턴 1은 여전히 동작)."""
        schema = _eav_schema()
        schema["_structure_meta"]["patterns"][0]["excluded_join_columns"] = [
            {"table": "cmm_resource", "reason": "컬럼 미지정"}
        ]
        sql = (
            "SELECT r.hostname FROM polestar.cmm_resource r "
            "LEFT JOIN polestar.cmm_vendor v ON r.resource_conf_id = v.id LIMIT 100"
        )
        assert _validate_forbidden_joins(sql, schema) == []
