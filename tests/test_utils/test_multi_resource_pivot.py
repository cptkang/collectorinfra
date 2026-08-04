"""EAV 다중 리소스 피벗 강제 블록 회귀 테스트 (D-068).

CPU 코어 수(LOGICALCORE=server.Cpus)·메모리 용량(TotalSize=server.Memory) 등
자식 리소스 EAV 속성은 server.Server 행 config에만 조인하면 NULL이 된다. 강제 블록이
resource_type 구분 + platform_resource_id GROUP BY 피벗을 생성하는지 검증한다.
"""

from __future__ import annotations

from datetime import date

from src.utils.query_gen_common import (
    build_stat_month_block,
    correct_servername_hostname_mapping,
    is_hostname_target,
    is_servername_to_hostname,
    resolve_stat_month_range,
)
# 폴스타 EAV/피벗 조립기는 어댑터로 이동(Plan 63 P2, D-089) — 이동-불변.
from src.db_adapters.polestar.assembler import (
    build_form_fill_pivot_sql,
    build_multi_resource_pivot_block,
    build_semantic_pivot_sql,
    classify_metric_field,
    eav_attr_resource_types,
)

_SCHEMA_INFO = {
    "_structure_meta": {
        "patterns": [
            {
                "type": "eav",
                "entity_table": "cmm_resource",
                "config_table": "core_config_prop",
                "attribute_column": "name",
                "value_column": "stringvalue_short",
                "direct_join": {
                    "entity_column": "resource_conf_id",
                    "config_column": "configuration_id",
                },
                "known_attributes": [
                    {"name": "OSType", "description": "운영체제 종류 [resource_type: server.Server]"},
                    {"name": "LOGICALCORE", "description": "논리코어 [resource_type: server.Cpus]"},
                    {
                        "name": "TotalSize",
                        "description": "메모리 [resource_type: server.Memory] / 디스크 [resource_type: server.Disks]",
                    },
                ],
            }
        ]
    }
}


def test_resource_type_map_extracted_from_description():
    rt = eav_attr_resource_types(_SCHEMA_INFO)
    assert rt["OSTYPE"] == "server.Server"
    assert rt["LOGICALCORE"] == "server.Cpus"
    # 복수 resource_type 설명이면 첫 번째(server.Memory)만 사용
    assert rt["TOTALSIZE"] == "server.Memory"


def test_resource_type_map_empty_when_no_meta():
    assert eav_attr_resource_types(None) == {}
    assert eav_attr_resource_types({}) == {}


def test_resource_type_map_reads_flattened_known_attributes_detail():
    """`_load_manual_profile`이 known_attributes를 문자열로 평탄화한 실 런타임 shape에서도
    resource_type을 추출하는지(회귀). 원본 객체는 known_attributes_detail에 보존된다.

    버그(2026-07-13): eav_attr_resource_types가 known_attributes(평탄화 후 문자열)만 읽어
    실 런타임에서 attr_rt가 항상 비었고, 폼필 결정적 피벗이 발동하지 않아 LLM 폴백으로
    떨어져 SQL 에러(단일/멀티 공통). 기존 단위 테스트는 dict shape만 먹여 이 결함을 못 잡음.
    """
    flattened = {
        "_structure_meta": {
            "patterns": [
                {
                    "type": "eav",
                    # _load_manual_profile 변환 결과: 문자열 리스트 + 원본 detail 보존
                    "known_attributes": ["OSType", "LOGICALCORE", "TotalSize"],
                    "known_attributes_detail": [
                        {"name": "OSType", "description": "운영체제 [resource_type: server.Server]"},
                        {"name": "LOGICALCORE", "description": "논리코어 [resource_type: server.Cpus]"},
                        {"name": "TotalSize", "description": "메모리 [resource_type: server.Memory]"},
                    ],
                }
            ]
        }
    }
    rt = eav_attr_resource_types(flattened)
    assert rt["LOGICALCORE"] == "server.Cpus"
    assert rt["TOTALSIZE"] == "server.Memory"


class TestCorrectServernameHostnameMapping:
    """서버명/서버이름류가 EAV Hostname으로 오매핑되면 등록명 컬럼으로 교정하는지 (D-068 후속).

    버그(2026-07-13): 폼 '서버 이름'이 field_mapper에서 EAV:Hostname으로 매핑돼(전역 유사어
    Hostname:[서버명] 미끼 + LLM 비결정) 생성 SQL이
    `MAX(CASE WHEN cc.name='Hostname' THEN cc.stringvalue_short END) AS "서버 이름"` →
    '서버 이름'·'호스트네임' 둘 다 hostname으로 채워짐. 프로필 확정 규칙(서버명=name)을 결정적 교정.
    """

    def test_servername_eav_hostname_corrected_to_name(self):
        cm = {"서버 이름": "EAV:Hostname"}
        correct_servername_hostname_mapping(cm, "cmm_resource")
        assert cm["서버 이름"] == "cmm_resource.name"

    def test_hostname_field_untouched(self):
        cm = {"호스트네임": "EAV:Hostname", "호스트명": "cmm_resource.hostname"}
        correct_servername_hostname_mapping(cm, "cmm_resource")
        # 호스트명 표면어는 name-term이 아니므로 건드리지 않음
        assert cm["호스트네임"] == "EAV:Hostname"
        assert cm["호스트명"] == "cmm_resource.hostname"

    def test_name_term_variants_and_normalization(self):
        cm = {
            "서버명": "EAV:Hostname",
            "서버이름": "EAV:hostname",  # 대소문자 무관
            "장비명": "EAV:Hostname",
            "리소스명": "EAV:Hostname",
        }
        correct_servername_hostname_mapping(cm, "cmm_resource")
        assert all(v == "cmm_resource.name" for v in cm.values())

    def test_non_hostname_eav_untouched(self):
        # 서버명이 다른 EAV 속성에 붙은 경우(비정상이지만)는 이 가드 범위 밖 — Hostname만 교정
        cm = {"서버명": "EAV:SomethingElse", "CPU 코어 수": "EAV:LOGICALCORE"}
        correct_servername_hostname_mapping(cm, "cmm_resource")
        assert cm["서버명"] == "EAV:SomethingElse"
        assert cm["CPU 코어 수"] == "EAV:LOGICALCORE"

    def test_no_entity_table_noop(self):
        cm = {"서버 이름": "EAV:Hostname"}
        correct_servername_hostname_mapping(cm, "")
        assert cm["서버 이름"] == "EAV:Hostname"

    def test_direct_hostname_column_corrected(self):
        """EAV뿐 아니라 직접 `*.hostname` 컬럼 매핑도 교정한다(가드 사각지대 제거, D-068 후속).

        전역/per-DB 유사어가 서버명을 hostname 직접 컬럼에 붙이면 field_mapper가 EAV가 아닌
        직접 컬럼으로 매핑할 수 있고, 이 경우 EAV 전용 가드로는 못 잡았다.
        """
        cm = {
            "서버 이름": "polestar.cmm_resource.hostname",  # 스키마 접두사 포함 직접 컬럼
            "서버명": "cmm_resource.hostname",
            "호스트네임": "polestar.cmm_resource.hostname",  # 호스트명 표면어 → 유지
        }
        correct_servername_hostname_mapping(cm, "cmm_resource")
        assert cm["서버 이름"] == "cmm_resource.name"
        assert cm["서버명"] == "cmm_resource.name"
        assert cm["호스트네임"] == "polestar.cmm_resource.hostname"


class TestServernameHostnamePredicates:
    """is_hostname_target / is_servername_to_hostname 판정 (등록 가드·교정 공용)."""

    def test_is_hostname_target(self):
        assert is_hostname_target("EAV:Hostname")
        assert is_hostname_target("EAV:hostname")
        assert is_hostname_target("cmm_resource.hostname")
        assert is_hostname_target("polestar.cmm_resource.hostname")
        assert is_hostname_target("polestar_b0:cmm_resource.HOSTNAME")
        assert not is_hostname_target("cmm_resource.name")
        assert not is_hostname_target("EAV:LOGICALCORE")
        assert not is_hostname_target("")

    def test_is_servername_to_hostname(self):
        # 서버명류 → hostname : 차단/교정 대상
        assert is_servername_to_hostname("서버 이름", "EAV:Hostname")
        assert is_servername_to_hostname("서버명", "polestar.cmm_resource.hostname")
        assert is_servername_to_hostname("장비명", "cmm_resource.hostname")
        # 호스트명 표면어·정상 매핑 : 대상 아님
        assert not is_servername_to_hostname("호스트네임", "EAV:Hostname")
        assert not is_servername_to_hostname("서버 이름", "cmm_resource.name")
        assert not is_servername_to_hostname("CPU 코어 수", "EAV:LOGICALCORE")


def test_pivot_sql_servername_renders_name_not_hostname():
    """교정 후 결정적 피벗 SQL에서 '서버 이름'이 c.name으로, '호스트네임'이 c.hostname으로 렌더."""
    cm = {"서버 이름": "EAV:Hostname", "호스트네임": "cmm_resource.hostname"}
    correct_servername_hostname_mapping(cm, "cmm_resource")
    regular = [(f, c) for f, c in cm.items() if not c.startswith("EAV:")]
    eav_pattern = _SCHEMA_INFO["_structure_meta"]["patterns"][0]
    sql = build_form_fill_pivot_sql(
        regular, [], [("CPU 코어 수", "LOGICALCORE", "server.Cpus")], eav_pattern,
        db_engine="postgresql",
    )
    assert 'THEN c.name END) AS "서버 이름"' in sql
    assert 'THEN c.hostname END) AS "호스트네임"' in sql
    assert "cc.name='Hostname'" not in sql  # 서버 이름이 EAV Hostname으로 남지 않음


def test_resource_type_map_from_real_manual_profiles():
    """실제 폴스타 프로필(gp/b0/yd)을 _load_manual_profile로 로드했을 때 attr_rt가
    채워지는지 — 실 런타임 경로 end-to-end 회귀(mock 아님)."""
    from src.nodes.schema_analyzer import _load_manual_profile

    for db_id in ("polestar_cm_gp", "polestar_b0", "polestar_cm_yd"):
        prof = _load_manual_profile(db_id)
        if prof is None:
            continue  # 프로필 미존재 환경에서는 스킵
        schema_info = {
            "_structure_meta": {k: v for k, v in prof.items() if k != "source"}
        }
        attr_rt = eav_attr_resource_types(schema_info)
        assert attr_rt, f"{db_id}: attr_rt가 비어 폼필 결정적 피벗이 발동하지 않음"
        assert attr_rt.get("LOGICALCORE") == "server.Cpus"
        assert attr_rt.get("TOTALSIZE") == "server.Memory"


def test_child_resource_pivot_uses_correct_resource_type():
    pattern = _SCHEMA_INFO["_structure_meta"]["patterns"][0]
    block = build_multi_resource_pivot_block(
        regular_entries=[
            ("서버 이름", "cmm_resource.name"),
            ("호스트네임", "cmm_resource.hostname"),
        ],
        server_eav=[("OS 종류", "OSType")],
        child_eav=[
            ("CPU 코어 수", "LOGICALCORE", "server.Cpus"),
            ("메모리 용량", "TotalSize", "server.Memory"),
        ],
        eav_pattern=pattern,
    )

    # 자식 리소스 속성은 자기 resource_type로 구분되어야 함 (서버 행이 아님)
    assert "c.resource_type='server.Cpus' AND cc.name='LOGICALCORE'" in block
    assert "c.resource_type='server.Memory' AND cc.name='TotalSize'" in block
    # 서버 EAV/직접 컬럼은 server.Server 행에서
    assert "c.resource_type='server.Server' AND cc.name='OSType'" in block
    assert "c.resource_type='server.Server' THEN c.name END" in block
    # platform_resource_id로 묶는 GROUP BY 피벗
    assert "GROUP BY COALESCE(c.platform_resource_id, c.id)" in block
    assert "cc.configuration_id = c.resource_conf_id" in block
    # 필요한 resource_type이 모두 WHERE IN에 포함
    for rt in ("'server.Server'", "'server.Cpus'", "'server.Memory'"):
        assert rt in block
    # 양식 필드명(한글) 그대로 alias
    assert 'AS "CPU 코어 수"' in block
    assert 'AS "메모리 용량"' in block
    # 실패 원인이던 Hostname 브릿지 조인은 쓰지 말라고 명시
    assert "브릿지 조인하지 마세요" in block


def test_metric_field_classification():
    assert classify_metric_field("CPU 평균") == ("server.Cpus", "AVG", "avg_val")
    assert classify_metric_field("CPU 최고") == ("server.Cpus", "MAX", "max_val")
    assert classify_metric_field("메모리 평균") == ("server.Memory", "AVG", "avg_val")
    assert classify_metric_field("메모리 최고") == ("server.Memory", "MAX", "max_val")
    # 사양(EAV) 필드는 집계어가 없어 metric 아님
    assert classify_metric_field("CPU 코어 수") is None
    assert classify_metric_field("메모리 용량") is None
    assert classify_metric_field("서버 이름") is None


def test_metric_fields_folded_into_single_pivot():
    """사용률 필드가 config 피벗과 같은 GROUP BY 스코프에 접혀 GROUP BY 위반이 없어야 한다."""
    pattern = _SCHEMA_INFO["_structure_meta"]["patterns"][0]
    block = build_multi_resource_pivot_block(
        regular_entries=[("서버 이름", "cmm_resource.name")],
        server_eav=[],
        child_eav=[("CPU 코어 수", "LOGICALCORE", "server.Cpus")],
        eav_pattern=pattern,
        metric_fields=["CPU 평균", "CPU 최고", "메모리 평균", "메모리 최고"],
        db_engine="postgresql",
    )
    # 사용률은 같은 c/s 조인으로 집계 (별도 스코프 아님)
    assert "LEFT JOIN cmm_metric_stat_m s ON s.resource_id = c.id" in block
    assert "AVG(CASE WHEN c.resource_type='server.Cpus' AND s.definition_name='Utilization' AND s.avg_val BETWEEN 0 AND 1000 THEN s.avg_val END)::numeric" in block
    assert "MAX(CASE WHEN c.resource_type='server.Memory' AND s.definition_name='Utilization' AND s.max_val BETWEEN 0 AND 1000 THEN s.max_val END)::numeric" in block
    # 단일 GROUP BY, 맨 컬럼 금지 명시
    assert block.count("GROUP BY COALESCE(c.platform_resource_id, c.id)") == 1
    assert "집계 밖의 맨 컬럼" in block
    # 모든 SELECT 라인이 집계(MAX/AVG/MIN)로 시작 → GROUP BY 위반 없음
    import re
    sql = re.search(r"```sql\n(.*?)```", block, re.S).group(1)
    select_body = sql.split("SELECT", 1)[1].split("FROM", 1)[0]
    for line in select_body.strip().splitlines():
        stripped = line.strip().rstrip(",")
        assert stripped.startswith(("MAX(", "ROUND(AVG(", "ROUND(MAX(", "ROUND(MIN(")), stripped


def test_db2_metric_uses_double_cast():
    """DB2 집계 전 캐스트는 DOUBLE — 고정 정밀도 DECIMAL(15,4)는 범위 밖 쓰레기 값(실측
    5.5e13)에서 SQL0413N 변환 오버플로로 쿼리가 죽는다(D-086)."""
    pattern = _SCHEMA_INFO["_structure_meta"]["patterns"][0]
    block = build_multi_resource_pivot_block(
        regular_entries=[],
        server_eav=[],
        child_eav=[("메모리 용량", "TotalSize", "server.Memory")],
        eav_pattern=pattern,
        metric_fields=["CPU 평균"],
        db_engine="db2",
    )
    assert "CAST(s.avg_val AS DOUBLE)" in block
    assert "DECIMAL(15,4)" not in block
    assert "::numeric" not in block


def test_resolve_stat_month_range():
    # 지난달: 월 롤백 + 연말→연초 롤오버 — 단일 월은 (m, m) 범위
    assert resolve_stat_month_range("지난달 1개월 통계", date(2026, 7, 13)) == ("202606", "202606")
    assert resolve_stat_month_range("지난달 통계", date(2026, 1, 5)) == ("202512", "202512")
    # 당월
    assert resolve_stat_month_range("이번달 통계", date(2026, 7, 13)) == ("202607", "202607")
    # 기간 표현 없으면 None(전체 월 평균)
    assert resolve_stat_month_range("모든 서버 조회") is None
    # "지난 1개월"(공백 포함) — 실측 질의 표현(D-076 후속4)
    assert resolve_stat_month_range(
        "CPU, 메모리 사용률을 지난 1개월 통계데이터로 확인", date(2026, 7, 14)
    ) == ("202606", "202606")


def test_resolve_stat_month_range_n_months():
    """"지난 N개월" 범위 해석(D-085) — 폼필에서 기간이 침묵 누락되던 실측 버그의 회귀 방지.

    "지난 3개월간 통계 자료를 사용하시오" 질의가 결정적 빌더에서 stat_month=None으로
    떨어져 전체 월 평균이 조회되던 문제. 진행 중인 달은 제외(D-076 후속4 원칙).
    """
    # 실측 질의 표현 그대로 — 직전 완결 월부터 3개월
    assert resolve_stat_month_range(
        "CPU, 메모리 사용률은 지난 3개월간 통계 자료를 사용하시오.", date(2026, 7, 16)
    ) == ("202604", "202606")
    # 연초 롤오버
    assert resolve_stat_month_range("지난 3개월 통계", date(2026, 2, 10)) == ("202511", "202601")
    # "최근 N개월" 표현 + 12개월
    assert resolve_stat_month_range("최근 12개월 사용률", date(2026, 7, 16)) == ("202507", "202606")
    # N=1은 지난달과 동일(호환)
    assert resolve_stat_month_range("지난 1개월", date(2026, 7, 16)) == ("202606", "202606")
    # 붙여쓰기("지난 3개월간"의 "개 월" 공백 변형 포함)
    assert resolve_stat_month_range("지난3개월 데이터", date(2026, 7, 16)) == ("202604", "202606")


def test_build_stat_month_block_forces_single_month_equality():
    """기간 결정적 해석 블록 — 단일 월 등호 필터 강제 + 동적 재계산 금지(D-076 후속4).

    LLM이 시스템 템플릿의 'CURRENT_DATE 동적 계산' 일반 규칙을 따라 BETWEEN으로 진행 중인
    달까지 포함하던 실측 오류의 회귀 방지.
    """
    block = build_stat_month_block("202606")
    assert "s.stat_date = '202606'" in block
    assert "BETWEEN" in block  # 금지 안내 문구
    assert "우선" in block  # 일반 규칙(하드코딩 금지)보다 우선함을 명시


def test_build_stat_month_block_empty_without_period():
    """기간 표현이 없으면(stat_month=None) 블록을 만들지 않는다(프롬프트 무변경)."""
    assert build_stat_month_block(None) == ""
    assert build_stat_month_block("") == ""


def test_build_stat_month_block_range_uses_between():
    """N개월 범위는 BETWEEN 필터를 강제한다(D-085). 단일 월 튜플은 등호 유지."""
    block = build_stat_month_block(("202604", "202606"))
    assert "s.stat_date BETWEEN '202604' AND '202606'" in block
    assert "우선" in block
    # (m, m) 튜플은 단일 월 등호로 접힘
    assert "s.stat_date = '202606'" in build_stat_month_block(("202606", "202606"))


class TestDeterministicPivotSql:
    """폼필 다중 리소스 피벗의 runnable SQL 결정적 조립(LLM 우회, D-068 2차)."""

    _PATTERN = _SCHEMA_INFO["_structure_meta"]["patterns"][0]
    _REG = [("서버 이름", "cmm_resource.name"), ("호스트네임", "cmm_resource.hostname")]
    _SEAV = [("OS 종류", "OSType")]
    _CEAV = [("CPU 코어 수", "LOGICALCORE", "server.Cpus"), ("메모리 용량", "TotalSize", "server.Memory")]
    _MF = ["CPU 평균", "CPU 최고", "메모리 평균", "메모리 최고"]

    def _sql(self, **kw):
        base = dict(
            regular_entries=self._REG, server_eav=self._SEAV, child_eav=self._CEAV,
            eav_pattern=self._PATTERN, metric_fields=self._MF,
        )
        base.update(kw)
        return build_form_fill_pivot_sql(**base)

    def test_single_group_by_no_duplication(self):
        """서버당 1행 — GROUP BY는 platform_resource_id 하나뿐(월별 중복 없음)."""
        sql = self._sql(db_engine="postgresql")
        assert sql.count("GROUP BY") == 1
        assert "GROUP BY COALESCE(c.platform_resource_id, c.id)" in sql
        # stat_date는 GROUP BY에 없어야 함(월별 분해 = 서버 중복의 원인)
        gb = sql.split("GROUP BY", 1)[1]
        assert "stat_date" not in gb

    def test_config_and_metric_resource_types(self):
        sql = self._sql(db_engine="postgresql")
        assert "c.resource_type='server.Cpus' AND cc.name='LOGICALCORE'" in sql
        assert "c.resource_type='server.Memory' AND cc.name='TotalSize'" in sql
        assert "LEFT JOIN cmm_metric_stat_m s ON s.resource_id = c.id" in sql

    def test_schema_prefix_and_limit_pg(self):
        sql = self._sql(db_engine="postgresql", db_schema="polestar", limit=100000)
        assert "FROM polestar.cmm_resource c" in sql
        assert "polestar.core_config_prop" in sql
        assert sql.rstrip().endswith("LIMIT 100000;")

    def test_schema_prefix_and_fetch_db2(self):
        sql = self._sql(db_engine="db2", db_schema="POLESTAR", limit=100000)
        assert "FROM POLESTAR.cmm_resource c" in sql
        assert "CAST(s.avg_val AS DOUBLE)" in sql
        assert "::numeric" not in sql
        assert sql.rstrip().endswith("FETCH FIRST 100000 ROWS ONLY;")

    def test_db2_metric_scale_fixed_to_two(self):
        """DB2 사용률 집계는 최종 `CAST(... AS DECIMAL(31,2))`로 스케일 고정(엑셀 제로필 방지, D-068 후속).

        DB2 집계는 스케일을 크게 확장해 ROUND(x,2)로 값은 맞아도 타입 스케일이 남아
        6.51000000000000000000처럼 직렬화된다. 외부 CAST로 scale 2 고정(정밀도는 15→31 확장, D-086).
        """
        sql = self._sql(db_engine="db2", db_schema="POLESTAR")
        assert "CAST(ROUND(AVG(" in sql
        assert "AS DECIMAL(31,2)) AS" in sql
        # PostgreSQL은 ::numeric 사용 — 외부 DECIMAL 캐스트 없음
        pg = self._sql(db_engine="postgresql", db_schema="polestar")
        assert "AS DECIMAL(31,2))" not in pg
        assert "::numeric, 2)" in pg

    def test_utilization_value_guard_applied(self):
        """Utilization 값 타당성 게이트(D-086) — 범위 밖 쓰레기(실측 max=5.5e13·avg=1.2e9·음수)를
        필드 단위로 집계에서 제외. 상한은 100이 아닌 1000(gp/yd 만재 피크가 100.1까지 기록됨)."""
        for engine in ("postgresql", "db2"):
            sql = self._sql(db_engine=engine)
            # 필드별 val_col에 각각 적용(avg는 avg_val, max는 max_val)
            assert "AND s.avg_val BETWEEN 0 AND 1000 THEN" in sql
            assert "AND s.max_val BETWEEN 0 AND 1000 THEN" in sql

    def test_non_utilization_measure_not_guarded(self):
        """MaxIORate 등 Utilization 외 지표에는 0~1000 게이트를 걸지 않는다(의미 없음)."""
        pattern = _SCHEMA_INFO["_structure_meta"]["patterns"][0]
        sql = build_semantic_pivot_sql(
            regular_entries=[], server_eav=[], child_eav=[],
            eav_pattern=pattern, db_engine="db2", db_schema="POLESTAR",
            explicit_measures=[("디스크 IO 평균", "server.Disks", "AVG", "avg_val", "MaxIORate")],
        )
        assert "s.definition_name IN " in sql or "s.definition_name = 'MaxIORate'" in sql
        assert "BETWEEN 0 AND 1000" not in sql
        # 크래시 면역(DOUBLE)은 게이트 없는 지표에도 적용
        assert "CAST(s.avg_val AS DOUBLE)" in sql

    def test_stat_month_filter_applied(self):
        sql = self._sql(db_engine="postgresql", stat_month="202606")
        assert "s.stat_date = '202606'" in sql
        # 미지정이면 월 필터 없음
        sql2 = self._sql(db_engine="postgresql", stat_month=None)
        assert "s.stat_date" not in sql2

    def test_stat_month_range_filter_applied(self):
        """N개월 범위(D-085) — "지난 3개월" 폼필에서 기간 필터가 침묵 누락되던 회귀 방지."""
        sql = self._sql(db_engine="postgresql", stat_month=("202604", "202606"))
        assert "AND s.stat_date BETWEEN '202604' AND '202606'" in sql
        # 범위여도 서버당 1행 구조(단일 GROUP BY)는 불변
        assert sql.count("GROUP BY") == 1
        # (m, m) 튜플은 단일 월 등호로 접힘
        sql2 = self._sql(db_engine="postgresql", stat_month=("202606", "202606"))
        assert "s.stat_date = '202606'" in sql2

    def test_all_select_lines_aggregated(self):
        """모든 SELECT 컬럼이 집계 → GROUP BY 위반(r.name 등) 원천 차단."""
        sql = self._sql(db_engine="postgresql")
        body = sql.split("SELECT", 1)[1].split("FROM", 1)[0]
        for line in body.strip().splitlines():
            stripped = line.strip().rstrip(",")
            assert stripped.startswith(("MAX(", "ROUND(AVG(", "ROUND(MAX(", "ROUND(MIN(")), stripped


class TestResolveStatMonthAbsolute:
    """절대 월 표현 해석 (D-099). 반환형은 D-102(구 UX D-085) 병합으로 (시작, 끝) 범위 튜플.

    회귀 방지(2026-07-20 라이브 실측): "2026년 6월"이 None을 반환해 결정적 조립 SQL에
    stat_date 필터가 빠지고 전 기간 평균으로 순위가 뒤집혔다. 절대 월은 단일 월이므로 (m, m).
    """

    def test_korean_absolute_month(self):
        from src.utils.query_gen_common import resolve_stat_month_range

        assert resolve_stat_month_range(
            "2026년 6월 CPU 사용률 평균이 가장 높은 서버"
        ) == ("202606", "202606")

    def test_hyphen_and_slash_forms(self):
        from src.utils.query_gen_common import resolve_stat_month_range

        assert resolve_stat_month_range("2026-06 CPU 평균") == ("202606", "202606")
        assert resolve_stat_month_range("2026/6 CPU 평균") == ("202606", "202606")

    def test_absolute_takes_priority_over_relative(self):
        """절대 표현이 상대 표현보다 우선한다(더 명시적)."""
        from datetime import date

        from src.utils.query_gen_common import resolve_stat_month_range

        assert resolve_stat_month_range(
            "지난달 대비 2026년 3월 CPU", today=date(2026, 7, 20)
        ) == ("202603", "202603")

    def test_relative_still_works(self):
        """기존 상대 표현 해석은 불변(회귀 0)."""
        from datetime import date

        from src.utils.query_gen_common import resolve_stat_month_range

        assert resolve_stat_month_range(
            "지난달 CPU 평균", today=date(2026, 7, 20)
        ) == ("202606", "202606")
        assert resolve_stat_month_range(
            "이번달 CPU", today=date(2026, 7, 20)
        ) == ("202607", "202607")

    def test_month_count_expression_is_range_not_absolute(self):
        """'지난 3개월'은 절대 단일 월로 오인하지 않고 N개월 범위로 해석한다(D-102)."""
        from datetime import date

        from src.utils.query_gen_common import resolve_stat_month_range

        # 7월 기준 직전 완결 3개월 = 4~6월 → (시작 202604, 끝 202606)
        assert resolve_stat_month_range(
            "지난 3개월 CPU 추이", today=date(2026, 7, 20)
        ) == ("202604", "202606")

    def test_invalid_month_ignored(self):
        """13월 등 범위 밖 값은 무시한다."""
        from src.utils.query_gen_common import resolve_stat_month_range

        assert resolve_stat_month_range("2026년 13월 데이터") is None

    def test_no_period_expression(self):
        from src.utils.query_gen_common import resolve_stat_month_range

        assert resolve_stat_month_range("CPU 사용률 현황") is None
