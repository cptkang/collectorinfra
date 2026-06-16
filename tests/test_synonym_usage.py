"""extract_synonym_usage 단위 테스트.

생성된 SQL에서 유사어 사전 항목 역조회 및 사전 미등록 리터럴 감지를 검증한다.
"""

from src.utils.synonym_usage import extract_synonym_usage

EAV_SYNONYMS = {
    "LOGICALCORE": ["논리코어", "논리 코어", "CPU", "CPU코어", "코어수", "코어"],
    "TotalSize": ["메모리", "MEMORY", "메모리크기", "총메모리", "메모리용량"],
    "MODEL": ["CPU모델", "CPU 모델", "프로세서"],
}

RT_SYNONYMS = {
    "server.Memory": ["메모리", "물리 메모리", "RAM"],
    "server.VirtualMemory": ["가상 메모리", "스왑"],
    "server.Cpus": ["CPU 컨테이너", "전체 CPU"],
}

COL_SYNONYMS = {
    "cmm_resource.hostname": ["서버명", "호스트명"],
    "cmm_resource.avail_status": ["가용성", "가용성 상태"],
}

SAMPLE_SQL = """
SELECT svr.hostname, c.NAME, c.stringvalue
FROM cmm_resource r
JOIN core_config_prop c ON r.resource_conf_id = c.configuration_id
WHERE c.NAME IN ('LOGICALCORE', 'TotalSize')
  AND r.resource_type = 'server.Memory'
LIMIT 1000
"""


class TestMappings:
    def test_eav_name_detected_from_in_clause(self):
        result = extract_synonym_usage(
            SAMPLE_SQL,
            eav_name_synonyms=EAV_SYNONYMS,
            resource_type_synonyms=RT_SYNONYMS,
        )
        eav_keys = [m["key"] for m in result["mappings"] if m["type"] == "eav_name"]
        assert "LOGICALCORE" in eav_keys
        assert "TotalSize" in eav_keys
        assert "MODEL" not in eav_keys

    def test_resource_type_detected_without_substring_false_positive(self):
        # 'server.Memory' 리터럴이 'server.VirtualMemory'로 오인되지 않아야 함
        result = extract_synonym_usage(
            SAMPLE_SQL,
            eav_name_synonyms=EAV_SYNONYMS,
            resource_type_synonyms=RT_SYNONYMS,
        )
        rt_keys = [m["key"] for m in result["mappings"] if m["type"] == "resource_type"]
        assert rt_keys == ["server.Memory"]

    def test_column_detected_only_with_matched_user_term(self):
        result = extract_synonym_usage(
            SAMPLE_SQL,
            column_synonyms=COL_SYNONYMS,
            query_targets=["서버명"],
        )
        col_keys = [m["key"] for m in result["mappings"] if m["type"] == "column"]
        assert "cmm_resource.hostname" in col_keys
        assert "cmm_resource.avail_status" not in col_keys

    def test_column_without_user_term_excluded(self):
        # SQL에 hostname이 등장해도 사용자 용어와 연결되지 않으면 제외
        result = extract_synonym_usage(
            SAMPLE_SQL,
            column_synonyms=COL_SYNONYMS,
        )
        assert [m for m in result["mappings"] if m["type"] == "column"] == []

    def test_shared_column_name_deduped_across_tables(self):
        # 여러 테이블에 같은 컬럼명이 있어도 1건으로 합쳐져야 함
        col_synonyms = {
            "cmm_resource.name": ["이름", "장비명"],
            "cmm_alarm.name": ["이름", "알람명"],
            "core_config_prop.name": ["속성명"],
        }
        sql = "SELECT r.name FROM cmm_resource r"
        result = extract_synonym_usage(
            sql,
            column_synonyms=col_synonyms,
            query_targets=["장비명"],
        )
        col_mappings = [m for m in result["mappings"] if m["type"] == "column"]
        assert len(col_mappings) == 1
        assert col_mappings[0]["key"] == "name"  # 다중 테이블 → bare 컬럼명 표시
        assert "장비명" in col_mappings[0]["synonyms"]

    def test_mappings_capped(self):
        # 매핑 항목이 상한(15건)을 넘지 않아야 함
        eav = {f"ATTR{i}": [f"속성{i}"] for i in range(30)}
        sql = "WHERE NAME IN (" + ", ".join(f"'ATTR{i}'" for i in range(30)) + ")"
        result = extract_synonym_usage(sql, eav_name_synonyms=eav)
        assert len(result["mappings"]) <= 15

    def test_matched_user_terms(self):
        result = extract_synonym_usage(
            SAMPLE_SQL,
            eav_name_synonyms=EAV_SYNONYMS,
            query_targets=["CPU 용량", "메모리 용량"],
        )
        by_key = {m["key"]: m for m in result["mappings"]}
        # "CPU 용량" 정규화("cpu용량")에 유사어 "CPU"("cpu")가 포함됨
        assert "CPU 용량" in by_key["LOGICALCORE"]["matched_user_terms"]
        # "메모리 용량" 정규화("메모리용량")가 유사어 "메모리용량"과 일치
        assert "메모리 용량" in by_key["TotalSize"]["matched_user_terms"]

    def test_empty_sql_returns_empty(self):
        result = extract_synonym_usage(
            "",
            eav_name_synonyms=EAV_SYNONYMS,
            resource_type_synonyms=RT_SYNONYMS,
        )
        assert result == {"mappings": [], "unregistered": []}


class TestUnregistered:
    def test_unregistered_eav_literal_detected(self):
        sql = "SELECT * FROM core_config_prop c WHERE c.NAME = 'OSVerson'"
        result = extract_synonym_usage(sql, eav_name_synonyms=EAV_SYNONYMS)
        assert result["unregistered"] == [
            {"type": "eav_name", "literal": "OSVerson"}
        ]

    def test_unregistered_resource_type_detected(self):
        sql = "SELECT * FROM cmm_resource WHERE resource_type = 'server.Disks'"
        result = extract_synonym_usage(sql, resource_type_synonyms=RT_SYNONYMS)
        assert result["unregistered"] == [
            {"type": "resource_type", "literal": "server.Disks"}
        ]

    def test_registered_literal_not_reported(self):
        result = extract_synonym_usage(
            SAMPLE_SQL,
            eav_name_synonyms=EAV_SYNONYMS,
            resource_type_synonyms=RT_SYNONYMS,
        )
        assert result["unregistered"] == []

    def test_like_wildcard_stripped_for_comparison(self):
        sql = "SELECT * FROM core_config_prop WHERE NAME LIKE 'TotalSize%'"
        result = extract_synonym_usage(sql, eav_name_synonyms=EAV_SYNONYMS)
        assert result["unregistered"] == []

    def test_custom_attribute_column(self):
        sql = "SELECT * FROM metrics WHERE definition_name = 'Utilization'"
        result = extract_synonym_usage(
            sql,
            eav_name_synonyms=EAV_SYNONYMS,
            attribute_columns=["definition_name"],
        )
        assert result["unregistered"] == [
            {"type": "eav_name", "literal": "Utilization"}
        ]

    def test_duplicate_literals_deduped(self):
        sql = (
            "SELECT * FROM t WHERE NAME = 'Foo' "
            "UNION SELECT * FROM t2 WHERE NAME = 'Foo'"
        )
        result = extract_synonym_usage(sql, eav_name_synonyms=EAV_SYNONYMS)
        assert result["unregistered"] == [{"type": "eav_name", "literal": "Foo"}]
