"""폴스타 프롬프트 정본 렌더 검증 (Plan 67 R1-2 / R1 잔여).

- 정본(config/knowledge 카탈로그)에서 렌더한 프롬프트가 현행 상수와 **바이트 동일**해야 한다
  (무해 전환 — 플래그 없이 적용 가능한 근거).
- 폴백 상수가 정본과 어긋나면(드리프트) 즉시 실패한다 — R1이 없애려는 사본 드리프트의 재발 감시.
- 카탈로그를 못 읽는 상황에서도 프롬프트가 깨지지 않고 폴백으로 동작해야 한다.
- 잔여 블록(SQL 예제·심각도 매핑·알람 조인)은 옵트인 플래그 뒤에 있고, **OFF면 전환 전 텍스트와
  sha256까지 동일**해야 한다(회귀 0의 근거). ON에서 달라지는 블록은 diff 리포트가 사유를 갖는다.
"""

from __future__ import annotations

import hashlib
import re

import pytest

from src.db_adapters.polestar import prompts as polestar_prompts
from src.db_adapters.polestar.prompts import (
    POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE,
    POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE,
    knowledge_blocks,
    render_alarm_system_template,
    render_metric_catalog_block,
    render_system_template,
)
from src.schema_cache.catalog_builder import (
    build_catalog,
    load_knowledge_overrides,
    load_structure_profile,
)

#: 전환 전(2026-07-30 HEAD) 프롬프트 텍스트의 sha256 — 플래그 OFF 렌더가 이 값이어야 한다.
#: 2026-09-16 갱신(plans/98 CU-12 — **의도한 프롬프트 변경**): Template A/B의 EAV 숫자 속성
#: (LOGICALCORE·PHYSICALCORE·PHYSICALCPU) 예시가 `cc.stringvalue_short`를 캐스트 없이 보여
#: LLM이 문자열 비교·합계를 따라 생성했다(B-07·B-08 2/2). 예시를 `CAST(... AS NUMERIC)`으로
#: 고치고 방언 주석(PostgreSQL NUMERIC / DB2 DECIMAL)을 더했다. 알람 템플릿은 EAV 블록을 쓰지
#: 않아 sha 불변 — 그 사실 자체가 변경 범위가 데이터 템플릿에 한정됐다는 근거다.
_SHA_BEFORE_DATA = "0ca62e8e345ab1614fcca7c050eba17f02f173b467a08e1d27c6878d80cf7674"
_SHA_BEFORE_ALARM = "81590b5335688931b9b4e491024828504b2cb367c294b298c1bd3230220d367c"

_POLESTAR_DB_IDS = ("polestar_cm_gp", "polestar_cm_yd", "polestar_b0")

#: 정본 렌더가 현행 리터럴과 바이트 일치해야 하는 블록(=무해 전환분). 여기 실패하면 프롬프트
#: 예제와 정본(카탈로그·프로필)이 어긋난 것이다 — R1이 감시하는 사본 드리프트.
_BYTE_IDENTICAL_BLOCKS = (
    "[[metric_value_columns]]",
    "[[metric_case_lines]]",
    "[[eav_attribute_lines]]",
    "[[eav_resource_types]]",
    "[[eav_join_condition]]",
    "[[hi_attribute_lines]]",
    "[[severity_case_lines_12]]",
    "[[severity_case_lines_8]]",
    "[[severity_case_lines_4]]",
    "[[alarm_joins_8]]",
    "[[alarm_joins_4]]",
    "[[alarm_joins_0]]",
    "[[alarm_active_join]]",
)

#: 공유 리터럴과 **달라야 정상인** (db_id, marker) 조합. `_FALLBACK_BLOCKS`는 공동존(cm_gp·cm_yd)
#: 모양의 사본 하나뿐이라, 실측으로 다른 은행존(b0)까지 바이트 일치를 요구하면 정본이 틀렸다고
#: 오판한다. b0는 `config/knowledge/polestar_b0/catalog.yaml`이 근거를 갖고 오버라이드한다 —
#: 파일시스템 통계가 단수형(server.FileSystem) 행만 있고, 디스크 IO(server.Disks/MaxIORate)는
#: stat_m 0건으로 미수집이다(2d485af C-02~C-12 실측).
_EXPECTED_DIVERGENCE = {
    ("polestar_b0", "[[metric_case_lines]]"),
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _profile_catalog(db_id: str) -> dict:
    return build_catalog(
        load_structure_profile(db_id),
        db_id=db_id,
        overrides=load_knowledge_overrides(db_id),
    )


def _sql_blocks(text: str) -> list[str]:
    return re.findall(r"```sql\n(.*?)```", text, re.DOTALL)


def test_rendered_template_is_byte_identical_to_shipped_constant():
    assert render_system_template(knowledge_render=False) == POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE


def test_flag_off_matches_pre_transition_bytes():
    """플래그 OFF 렌더는 전환 전 텍스트와 sha256까지 동일하다(회귀 0)."""
    assert _sha(POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE) == _SHA_BEFORE_DATA
    assert _sha(POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE) == _SHA_BEFORE_ALARM
    assert _sha(render_system_template(knowledge_render=False)) == _SHA_BEFORE_DATA
    assert _sha(render_alarm_system_template(knowledge_render=False)) == _SHA_BEFORE_ALARM


def test_metric_block_matches_fallback_literal():
    """폴백 상수와 정본 렌더가 어긋나면 실패한다(사본 드리프트 감시)."""
    catalog = build_catalog(None, overrides=load_knowledge_overrides("_base"))
    assert render_metric_catalog_block(catalog) == polestar_prompts._METRIC_CATALOG_FALLBACK


def test_new_measure_flows_into_prompt_without_editing_the_constant():
    """정본에 measure를 1건 추가하면 프롬프트에 자동 반영된다(수정 지점 1곳)."""
    catalog = {"pattern_b": {"measures": [
        {"resource_type": "server.Net", "definition_name": "Throughput", "aliases": ["네트워크 처리량"]},
    ]}}
    rendered = render_system_template(catalog, knowledge_render=False)
    assert "- 'server.Net' + definition_name = 'Throughput' → 네트워크 처리량" in rendered
    assert "server.Cpus' + definition_name" not in rendered


def test_render_falls_back_when_catalog_missing(monkeypatch):
    """카탈로그 미가용 시 폴백 상수로 프롬프트를 유지한다(마커 누출 없음)."""
    monkeypatch.setattr(polestar_prompts, "_rendered_cache", {})
    monkeypatch.setattr(
        "src.schema_cache.catalog_builder.load_knowledge_overrides", lambda *a, **k: {})
    for knowledge_render in (False, True):
        rendered = render_system_template(knowledge_render=knowledge_render)
        assert polestar_prompts._METRIC_CATALOG_MARKER not in rendered
        assert "[[" not in rendered
        alarm = render_alarm_system_template(knowledge_render=knowledge_render)
        assert "[[" not in alarm


def test_sql_examples_read_hostname_and_ip_from_direct_columns():
    """예제 SQL이 빈 EAV 속성 대신 직접 컬럼으로 호스트명·IP를 읽어야 한다(D-058/D-061).

    EAV 'Hostname'/'IPaddress'는 실측상 비어 있어(카탈로그도 두 속성을 제외) EAV로 조회하면
    결과가 NULL이 된다. 2026-07-29 정본 대조로 발견해 교정한 사본 드리프트의 재발 감시.
    """
    for knowledge_render in (False, True):
        sql_lines = [
            line for line in render_system_template(knowledge_render=knowledge_render).splitlines()
            if not line.lstrip().startswith("--")
        ]
        body = "\n".join(sql_lines)
        assert "'Hostname'" not in body
        assert "'IPaddress'" not in body
        assert "THEN c.hostname END) AS hostname" in body
        assert "THEN c.ipaddress END) AS ipaddress" in body


#: 값이 순수 숫자 문자열인 EAV 속성 — 예제가 캐스트를 보여야 한다(plans/98 CU-12).
_NUMERIC_EAV_ATTRS = ("LOGICALCORE", "PHYSICALCORE", "PHYSICALCPU")
#: 값에 단위·문자열이 섞이는 속성 — 캐스트하면 실행 오류이거나 무의미하다.
_NON_NUMERIC_EAV_ATTRS = ("TotalSize", "Model", "MODEL", "OSType", "OSVerson", "PatchLevel")


@pytest.mark.parametrize("knowledge_render", [False, True])
def test_eav_numeric_attribute_examples_cast_before_aggregation(knowledge_render):
    """숫자 EAV 속성 예시는 집계 함수 안에서 NUMERIC 캐스트를 보여야 한다(plans/98 CU-12).

    B-07·B-08이 2/2로 캐스트 없는 문자열 비교·합계를 생성했다. 원인은 프롬프트 예제가
    `cc.stringvalue_short`를 캐스트 없이 보여준 것이다 — 지시를 덧붙이기 전에 **예제부터**
    고친다(프롬프트 강제가 few-shot 예시와 경쟁하면 반복 실패한다).
    """
    rendered = render_system_template(knowledge_render=knowledge_render)
    for attribute in _NUMERIC_EAV_ATTRS:
        for line in rendered.splitlines():
            if f"'{attribute}'" not in line or "MAX(CASE WHEN" not in line:
                continue
            assert "CAST(cc.stringvalue_short AS NUMERIC)" in line, (
                f"{attribute} 예시가 캐스트 없이 값 컬럼을 노출한다: {line}"
            )
            # 캐스트는 집계 **안쪽**이어야 한다(CLAUDE.md: 반드시 집계 전 캐스트).
            assert line.index("MAX(") < line.index("CAST("), line


@pytest.mark.parametrize("knowledge_render", [False, True])
def test_non_numeric_eav_attribute_examples_stay_uncast(knowledge_render):
    """문자열·단위 포함 속성(OS 종류·모델·메모리 '62.1 GB')에는 캐스트를 넣지 않는다."""
    rendered = render_system_template(knowledge_render=knowledge_render)
    for attribute in _NON_NUMERIC_EAV_ATTRS:
        for line in rendered.splitlines():
            if f"'{attribute}'" not in line or "MAX(CASE WHEN" not in line:
                continue
            assert "CAST(" not in line, f"{attribute}는 숫자 속성이 아니다: {line}"


@pytest.mark.parametrize("knowledge_render", [False, True])
def test_eav_cast_note_covers_both_dialects(knowledge_render):
    """캐스트 주석이 PostgreSQL·DB2 양 방언을 모두 적는다(방언 분기 필수)."""
    rendered = render_system_template(knowledge_render=knowledge_render)
    note = next(
        (ln for ln in rendered.splitlines() if "AS DECIMAL" in ln), None
    )
    assert note is not None, "DB2 캐스트 방언 안내가 없다"
    assert "AS NUMERIC" in note and "PostgreSQL" in note and "DB2" in note, note


def test_rendered_template_keeps_format_placeholders():
    """query_generator가 .format()으로 채우는 자리표시자가 렌더 후에도 남아 있어야 한다."""
    for knowledge_render in (False, True):
        rendered = render_system_template(knowledge_render=knowledge_render)
        for token in ("{schema}", "{structure_guide}", "{db_engine_hint}", "{default_limit}"):
            assert token in rendered
        alarm = render_alarm_system_template(knowledge_render=knowledge_render)
        for token in ("{schema}", "{structure_guide}", "{db_engine_hint}", "{default_limit}"):
            assert token in alarm


class TestKnowledgeBlocks:
    """옵트인 렌더 블록 — 정본과 현행 리터럴의 일치/차이가 의도대로인지."""

    def test_off_returns_every_marker_as_literal(self):
        blocks = knowledge_blocks(knowledge_render=False)
        assert blocks == polestar_prompts._FALLBACK_BLOCKS

    def test_no_marker_leaks_into_rendered_templates(self):
        """스켈레톤의 모든 마커에 치환값이 있어야 한다(마커 누출 = 프롬프트 파손)."""
        for skeleton in (polestar_prompts._SYSTEM_TEMPLATE_SKELETON,
                         polestar_prompts._ALARM_TEMPLATE_SKELETON):
            for marker in re.findall(r"\[\[\w+\]\]", skeleton):
                assert (marker in polestar_prompts._FALLBACK_BLOCKS
                        or marker == polestar_prompts._METRIC_CATALOG_MARKER), marker

    @pytest.mark.parametrize("db_id", _POLESTAR_DB_IDS)
    def test_canonical_render_matches_literal_for_harmless_blocks(self, db_id):
        """정본 렌더가 현행 예제와 바이트 일치해야 하는 블록 — 어긋나면 사본 드리프트다."""
        blocks = knowledge_blocks(_profile_catalog(db_id), knowledge_render=True)
        for marker in _BYTE_IDENTICAL_BLOCKS:
            literal = polestar_prompts._FALLBACK_BLOCKS[marker]
            if (db_id, marker) in _EXPECTED_DIVERGENCE:
                assert blocks[marker] != literal, (
                    f"{db_id} {marker}: 근거 있는 오버라이드가 사라졌다 — "
                    "카탈로그가 공유 리터럴로 되돌아갔는지 확인하라"
                )
                continue
            assert blocks[marker] == literal, marker

    def test_dropped_attribute_disappears_from_example(self):
        """정본에서 속성이 빠지면 예제 SELECT 줄도 사라진다(D-058형 드리프트 차단)."""
        catalog = _profile_catalog("polestar_cm_gp")
        catalog["pattern_a"]["dimensions"] = [
            dim for dim in catalog["pattern_a"]["dimensions"] if dim.get("name") != "SerialNumber"
        ]
        blocks = knowledge_blocks(catalog, knowledge_render=True)
        assert "'SerialNumber'" not in blocks["[[eav_attribute_lines]]"]
        assert "'Model'" in blocks["[[eav_attribute_lines]]"]

    def test_new_measure_flows_into_case_lines(self):
        """정본 measure 추가가 Template B 집계 줄에 자동 반영된다."""
        catalog = _profile_catalog("polestar_cm_gp")
        catalog["pattern_b"]["measures"].append(
            {"resource_type": "server.Net", "definition_name": "Throughput", "aliases": ["처리량"]})
        lines = knowledge_blocks(catalog, knowledge_render=True)["[[metric_case_lines]]"]
        assert "AS net_min," in lines and "AS net_max" in lines

    def test_severity_grades_come_from_canonical_map(self):
        """등급 CASE WHEN이 정본 severity_map에서 렌더된다(사본 6곳 → 정본 1곳)."""
        catalog = _profile_catalog("polestar_cm_gp")
        catalog["pattern_c"]["severity_map"] = {"위험": 3, "보통": 1}
        block = knowledge_blocks(catalog, knowledge_render=True)["[[severity_case_lines_12]]"]
        assert "WHEN CA.ALARMSEVERITY = 1 THEN '보통'" in block
        assert "WHEN CA.ALARMSEVERITY = 3 THEN '위험'" in block
        assert "'심각'" not in block

    def test_unrenderable_block_keeps_literal(self):
        """정본에서 렌더 못 한 블록은 리터럴을 유지한다(침묵 붕괴 금지)."""
        blocks = knowledge_blocks({"pattern_b": {}}, knowledge_render=True)
        for marker in ("[[metric_case_lines]]", "[[eav_attribute_lines]]",
                       "[[severity_case_lines_12]]", "[[alarm_joins_8]]"):
            assert blocks[marker] == polestar_prompts._FALLBACK_BLOCKS[marker], marker


class TestHiSubqueryJoinKey:
    """Template B `hi` 서브쿼리 조인 키 교정 (Plan 67 v10-⑦ — 플래그 ON에서만 적용)."""

    def test_off_keeps_current_value_column_join(self):
        assert ") hi ON svr.ipaddress = hi.ipaddress" in render_system_template(knowledge_render=False)

    def test_on_joins_on_server_identifier_and_drops_orphan_column(self):
        rendered = render_system_template(knowledge_render=True)
        assert ") hi ON svr.id = hi.id" in rendered
        assert "hi.ipaddress" not in rendered
        assert "END) AS ipaddress,\n        MAX" not in rendered
        # 외부 SELECT·GROUP BY는 서버 행의 IP를 그대로 쓴다(고아 참조 없음).
        assert "svr.ipaddress AS ipaddress" in rendered

    @pytest.mark.parametrize("knowledge_render", [False, True])
    def test_example_passes_registered_validators(self, knowledge_render, monkeypatch):
        """예제와 검증이 함께 움직인다 — 각 플래그 상태의 예제가 그 상태의 validator를 통과한다.

        OFF: 기본 11종 통과(현행 유지). ON: 교정 예제 + 값 컬럼 조인 검사 12종 통과.
        (플래그를 섞으면 — OFF 예제 + ON 검사 — 예제 자체가 반려된다: 아래 별도 테스트)
        카운트는 기본 등록 목록(adapter.validator_checks, user_query 미지정)과 함께 갱신한다
        — 7/8 시절 값이 D-175~D-202 검사 추가를 따라오지 못해 사전 실패로 방치돼 있었다.
        """
        from src.db_adapters.polestar.adapter import PolestarAdapter

        monkeypatch.setattr(
            "src.db_adapters.polestar.adapter.knowledge_render_enabled", lambda: knowledge_render)
        rendered = render_system_template(knowledge_render=knowledge_render)
        sql = next(b for b in _sql_blocks(rendered) if "cmm_metric_stat_m s" in b)
        sql = sql.replace("{default_limit}", "100")
        checks = PolestarAdapter().validator_checks()
        assert len(checks) == (12 if knowledge_render else 11)
        for check in checks:
            assert check(sql) == [], check.__name__

    def test_value_column_join_check_flags_current_example(self):
        """값 컬럼 조인 검사가 현행 예제를 위반으로 잡는다 — 검사를 무게이트로 등록할 수 없는 근거."""
        from src.db_adapters.polestar.validators import check_value_column_join

        sql = next(b for b in _sql_blocks(render_system_template(knowledge_render=False))
                   if "cmm_metric_stat_m s" in b)
        errors = check_value_column_join(sql.replace("{default_limit}", "100"))
        assert len(errors) == 1
        assert "ipaddress" in errors[0] and "svr.id = hi.id" in errors[0]

    def test_value_column_join_check_is_gated_by_flag(self, monkeypatch):
        """플래그 OFF에서는 값 컬럼 조인 검사가 등록되지 않는다(현행 예제 반려 방지)."""
        from src.db_adapters.polestar.adapter import PolestarAdapter
        from src.db_adapters.polestar.validators import check_value_column_join

        monkeypatch.setattr(
            "src.db_adapters.polestar.adapter.knowledge_render_enabled", lambda: False)
        assert check_value_column_join not in PolestarAdapter().validator_checks()
        monkeypatch.setattr(
            "src.db_adapters.polestar.adapter.knowledge_render_enabled", lambda: True)
        assert check_value_column_join in PolestarAdapter().validator_checks()


class TestPromptKnowledgeDriftGuards:
    """렌더로 전환하지 않은 블록의 정본 대조 — 프롬프트 바이트를 바꾸지 않고 드리프트만 감시."""

    def test_severity_surface_words_are_superset_of_canonical_map(self):
        """[심각도 매핑] 블록은 정본 severity_map의 **상위집합**이어야 한다.

        프롬프트는 정본에 없는 표면어(해제·cleared·normal·notice·대문자형)까지 가르치므로 렌더로
        전환하지 않았다(전환하면 그 표면어가 소실된다). 대신 정본에 있는 표기가 프롬프트에서
        빠지는 역방향 드리프트를 여기서 잡는다 — 정본 확장 시 프롬프트도 갱신해야 한다.
        """
        severity_map = build_catalog(
            None, overrides=load_knowledge_overrides("_base"))["pattern_c"]["severity_map"]
        block = POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE.split("[심각도 매핑]")[1].split("\n\n")[0]
        for label, value in severity_map.items():
            line = next((ln for ln in block.splitlines() if f"= {value}" in ln), "")
            assert label.lower() in line.lower(), (label, value, line)

    def test_stat_table_names_match_canonical_metric_tables(self):
        """프롬프트가 가르치는 통계 테이블명이 정본 pattern_b.metric_tables와 일치한다."""
        catalog = build_catalog(None, overrides=load_knowledge_overrides("_base"))
        expected = set((catalog["pattern_b"]["metric_tables"]).values())
        found = set(re.findall(r"\bcmm_metric_stat_[a-z]\b", POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE))
        assert found == expected

    @pytest.mark.parametrize("db_id", _POLESTAR_DB_IDS)
    def test_alarm_prompt_tables_are_declared_in_profile(self, db_id):
        """알람 템플릿이 참조하는 테이블은 프로필 alarm_allowed_tables에 선언돼 있어야 한다."""
        declared = {t.lower() for t in (load_structure_profile(db_id) or {}).get(
            "alarm_allowed_tables") or []}
        assert declared, db_id
        referenced = {
            table.lower()
            for table in re.findall(
                r"\b(?:FROM|JOIN)\s+((?:CMM|ACC)_[A-Z_]+)",
                POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE,
            )
        }
        assert referenced, "알람 템플릿에서 테이블 참조를 찾지 못했다"
        assert referenced <= declared, referenced - declared


class TestOptInFlagResolution:
    """플래그 판정 — 설정 필드로만 판정하고(os.getenv 금지) 실패 시 OFF로 강등한다."""

    def test_missing_config_field_means_off(self, monkeypatch):
        class _T2S:
            pass

        class _Cfg:
            text2sql = _T2S()

        monkeypatch.setattr("src.config.load_config", lambda: _Cfg())
        assert polestar_prompts.knowledge_render_enabled() is False

    def test_config_field_enables_render(self, monkeypatch):
        class _T2S:
            prompt_knowledge_render = True

        class _Cfg:
            text2sql = _T2S()

        monkeypatch.setattr("src.config.load_config", lambda: _Cfg())
        assert polestar_prompts.knowledge_render_enabled() is True

    def test_config_load_failure_degrades_to_off(self, monkeypatch):
        def _boom():
            raise RuntimeError("설정 로드 실패")

        monkeypatch.setattr("src.config.load_config", _boom)
        assert polestar_prompts.knowledge_render_enabled() is False

    def test_cache_is_keyed_by_flag(self, monkeypatch):
        monkeypatch.setattr(polestar_prompts, "_rendered_cache", {})
        off, on = render_system_template(knowledge_render=False), render_system_template(
            knowledge_render=True)
        assert off != on
        assert render_system_template(knowledge_render=False) == off
        assert render_system_template(knowledge_render=True) == on
