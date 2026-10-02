"""SMQ IR 확장 골든 회귀 (Plan 67 Phase S3 / S-IR1~5 + R4 계측).

폐쇄망 CI 전제 — 실제 DB·LLM 없이 통과해야 한다(SMQ→SQL은 결정적 조립).

검증 대상은 계획서 §2.5의 IR 한계 5건을 푼 확장이며, 각 케이스는 골드셋의 실제 질의
형태를 기준으로 삼는다:
    S-IR1 전역 집계·건수      — gp-003("서버 수"), gp-013("전 서버 통틀은 단일 값")
    S-IR2 기간별 행 분해       — gp-010("지난 3개월간 서버별 월간 통계")
    S-IR3 order_by/limit 승격  — 표면어(_RANK_*_MARKERS·_TOP_N_RE)는 IR 부재 시 폴백으로 강등
    S-IR4 필터 안전장 확대     — gp-005/gp-006/gp-007(서버명·가용성), 측정치 임계, 기간 필터 승격
    S-IR5 알람 건수 랭킹       — gp-015("알람 최다 발생 상위 10 서버")

**확장 필드가 없는 SMQ는 기존 경로와 바이트 동일**해야 한다(`test_absent_ir_fields_*`).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.nodes.semantic_compiler import (
    SMQ,
    check_coverage,
    compile_from_nl,
    compile_smq,
    guard_counters,
    load_semantic_model,
    normalize_smq,
    reset_guard_counters,
)
from src.utils.query_gen_common import resolve_stat_month_range

_GP = "polestar_cm_gp"
_B0 = "polestar_b0"


@pytest.fixture()
def model():
    return load_semantic_model(_GP)


def _compile(smq_dict: dict, model: dict, db_id: str = _GP, **kwargs) -> str:
    """SMQ dict를 커버리지 판정까지 통과시킨 뒤 컴파일한다(밖이면 사유와 함께 실패)."""
    smq = SMQ.from_dict(smq_dict)
    cov = check_coverage(smq, model)
    assert cov.covered, f"커버리지 밖: {cov.reason}"
    return compile_smq(smq, db_id, model, **kwargs)


def _reason(smq_dict: dict, model: dict) -> str:
    """커버리지 밖 사유를 돌려준다(내부로 판정되면 실패)."""
    cov = check_coverage(SMQ.from_dict(smq_dict), model)
    assert not cov.covered, "커버리지 내로 판정됨(밖이어야 함)"
    return cov.reason


# ──────────────────────────────────────────────
# S-IR1: count/sum + 전역 집계 (GROUP BY 생략)
# ──────────────────────────────────────────────

def test_entity_count_compiles_single_value(model):
    """gp-003("서버 수를 조회해줘") 형태 — 엔티티 수를 단일 행으로 집계한다.

    alias는 카탈로그의 엔티티 resource_type에서 파생하므로(server.Server → server_count)
    컴파일러에 엔티티 이름을 하드코딩하지 않는다.
    """
    sql = _compile(
        {"pattern": "A", "resource_types": ["server.Server"],
         "global_aggregate": True, "entity_count": True},
        model, user_query="서버 수를 조회해줘",
    )
    assert 'COUNT(DISTINCT COALESCE(c.platform_resource_id, c.id)) AS "server_count"' in sql
    assert "GROUP BY" not in sql
    # 전역 집계에서 config 조인은 행만 증식시킨다(그룹마다 배수가 달라 평균을 왜곡).
    assert "core_config_prop" not in sql


def test_global_measure_aggregate_single_row(model):
    """gp-013("전체 서버를 통틀은 지난달 평균 CPU 사용률 단일 값") 형태."""
    sql = _compile(
        {"pattern": "B", "global_aggregate": True, "time_grain": "month",
         "measures": [{"agg": "avg", "definition_name": "Utilization",
                       "resource_type": "server.Cpus"}]},
        model, stat_month="202606",
    )
    assert "GROUP BY" not in sql
    assert "core_config_prop" not in sql
    assert "s.stat_date = '202606'" in sql
    assert "::numeric, 2) AS \"cpus_avg\"" in sql
    # 전역 집계에서는 식별 dimension 주입(default_dimensions)을 하지 않는다 — 단일 값이 깨진다.
    assert 'AS "name"' not in sql


def test_count_and_sum_agg_compile(model):
    """agg=count/sum이 커버리지 내로 들어오고 해당 집계 함수로 조립된다."""
    sql = _compile(
        {"pattern": "B", "dimensions": ["name"], "time_grain": "month",
         "measures": [{"agg": "count", "definition_name": "Utilization",
                       "resource_type": "server.Cpus"},
                      {"agg": "sum", "definition_name": "MaxIORate",
                       "resource_type": "server.Disks"}]},
        model,
    )
    assert 'COUNT(CASE WHEN c.resource_type=\'server.Cpus\'' in sql
    assert 'SUM(CASE WHEN c.resource_type=\'server.Disks\'' in sql
    assert 'AS "cpus_count"' in sql and 'AS "disks_sum"' in sql


def test_global_aggregate_shape_rules(model):
    """전역 집계는 dimension 불가·집계 대상 필수, 엔티티 수는 전역에서만."""
    assert "dimension 불가" in _reason(
        {"pattern": "A", "global_aggregate": True, "dimensions": ["name"]}, model)
    assert "집계 대상" in _reason(
        {"pattern": "A", "global_aggregate": True}, model)
    assert "전역 집계" in _reason(
        {"pattern": "A", "dimensions": ["name"], "entity_count": True}, model)
    assert "동시 지원 불가" in _reason(
        {"pattern": "B", "global_aggregate": True, "time_breakdown": True,
         "measures": [{"agg": "avg", "definition_name": "Utilization",
                       "resource_type": "server.Cpus"}]}, model)


def test_entity_count_rejects_non_entity_resource_type(model):
    """자식 리소스 수(예: CPU 개수)를 요청하면 밖으로 돌린다 — 세는 대상은 서버 행뿐이다.

    통과시키면 "CPU 몇 개" 질의에 서버 수를 돌려주는 조용한 오답이 된다.
    """
    reason = _reason(
        {"pattern": "A", "resource_types": ["server.Cpus"],
         "global_aggregate": True, "entity_count": True}, model)
    assert "엔티티 외 리소스 수 집계 미지원" in reason


def test_time_range_without_measure_is_outside(model):
    """설정 조회(measure 없음)에 기간이 붙으면 밖으로 돌린다 — 적용할 통계 조인이 없다."""
    assert "기간 조건을 적용할 measure 없음" in _reason(
        {"pattern": "A", "dimensions": ["name"], "time_range": ["202606"]}, model)


def test_unsupported_agg_still_outside(model):
    """카탈로그·집계 목록 밖 집계는 여전히 커버리지 밖(폴백)이다."""
    assert "미지원 집계" in _reason(
        {"pattern": "B", "dimensions": ["name"],
         "measures": [{"agg": "median", "definition_name": "Utilization",
                       "resource_type": "server.Cpus"}]}, model)
    # 대소문자를 흡수해도 유효값 검증은 유지된다(표기 정규화 ≠ 검증 완화).
    assert "미지원 집계" in _reason(
        {"pattern": "B", "dimensions": ["name"],
         "measures": [{"agg": "MEDIAN", "definition_name": "Utilization",
                       "resource_type": "server.Cpus"}]}, model)


def test_agg_case_is_normalized_deterministically(model):
    """LLM이 대문자·공백 섞인 집계 표기를 내도 커버리지·조립이 그대로 통과한다.

    종전에는 커버리지 판정이 대소문자를 구분해 `"AVG"`·`"COUNT"`가 "미지원 집계"로 밀려
    정확한 선택이 통째로 폴백됐다(S3 부수 발견 ①). 표기 흔들림은 결정적으로 흡수한다.
    """
    smq = SMQ.from_dict({
        "pattern": "B", "dimensions": ["name"], "time_grain": "month",
        "measures": [{"agg": "AVG", "definition_name": "Utilization",
                      "resource_type": "server.Cpus"},
                     {"agg": " Count ", "definition_name": "MaxIORate",
                      "resource_type": "server.Disks"}],
    })
    assert [m.agg for m in smq.measures] == ["avg", "count"]
    cov = check_coverage(smq, model)
    assert cov.covered, cov.reason
    sql = compile_smq(smq, _GP, model, stat_month="202606")
    # alias·집계 함수가 소문자 정규화 기준으로 일관되게 조립된다.
    assert 'AS "cpus_avg"' in sql and 'AS "disks_count"' in sql
    assert "AVG(CASE WHEN c.resource_type='server.Cpus'" in sql
    assert "COUNT(CASE WHEN c.resource_type='server.Disks'" in sql


# ──────────────────────────────────────────────
# S-IR2: 기간별 행 분해 (월별 폴백 게이트 해소)
# ──────────────────────────────────────────────

def test_time_breakdown_groups_by_stat_period(model):
    """gp-010 형태 — 통계 기간을 SELECT·GROUP BY에 넣어 월별 행을 만든다.

    기간이 GROUP BY에 들어가면 서버 행(server.Server)과 통계 행이 다른 그룹으로 갈리므로
    식별 컬럼은 부모 서버 조인에서 가져온다(그러지 않으면 월별 행의 서버명이 NULL).
    """
    sql = _compile(
        {"pattern": "B", "dimensions": ["name", "hostname"], "time_grain": "month",
         "time_breakdown": True,
         "measures": [{"agg": "avg", "definition_name": "Utilization",
                       "resource_type": "server.Cpus"},
                      {"agg": "avg", "definition_name": "MaxIORate",
                       "resource_type": "server.Disks"}]},
        model, stat_month=("202604", "202606"),
    )
    assert 's.stat_date AS "stat_date"' in sql
    assert "GROUP BY COALESCE(c.platform_resource_id, c.id), s.stat_date" in sql
    assert "LEFT JOIN polestar.cmm_resource svr ON svr.id = COALESCE(c.platform_resource_id, c.id)" in sql
    assert 'MAX(svr.name) AS "name"' in sql and 'MAX(svr.hostname) AS "hostname"' in sql
    # 통계가 없는 서버 행이 만드는 기간 NULL 그룹은 제외한다.
    assert "AND s.stat_date IS NOT NULL" in sql
    assert "s.stat_date BETWEEN '202604' AND '202606'" in sql


def test_time_breakdown_rejects_eav_dimension(model):
    """EAV 속성 dimension + 기간 분해는 조립 불가(폴백) — 속성 행이 다른 그룹으로 갈린다."""
    assert "EAV 속성 dimension 미지원" in _reason(
        {"pattern": "B", "dimensions": ["name", "TotalSize"], "time_breakdown": True,
         "measures": [{"agg": "avg", "definition_name": "Utilization",
                       "resource_type": "server.Cpus"}]}, model)


def test_time_breakdown_requires_measure(model):
    """측정치 없는 기간 분해는 기준 기간이 없어 조립 불가."""
    assert "measure 필요" in _reason(
        {"pattern": "A", "dimensions": ["name"], "time_breakdown": True}, model)


@pytest.mark.asyncio
async def test_monthly_query_promoted_instead_of_forced_fallback():
    """"월간/월별" 질의는 폴백 강제 대신 time_breakdown으로 승격된다 (S-IR2).

    변경 이력: 종전에는 `_MONTHLY_BREAKDOWN_RE`가 매칭되면 무조건 커버리지 밖으로 돌려
    LLM 폴백을 강제했다(서버당 1행 집계로 표현 불가했기 때문 — 실측 gp-010: 월별 4725행이
    1820행으로 붕괴). 컴파일러가 기간별 분해를 지원하게 되어 표면어는 승격 신호로 쓴다.
    표면어 정규식과 게이트는 남기고 발동만 계측한다(R4 — 가드 삭제 금지).
    """
    class _FakeLLM:
        async def ainvoke(self, messages):
            return SimpleNamespace(content=(
                '{"pattern": "B", "dimensions": ["name"], "time_grain": "month", '
                '"measures": [{"agg": "avg", "definition_name": "Utilization", '
                '"resource_type": "server.Cpus"}]}'
            ))

    reset_guard_counters()
    sql, smq, cov = await compile_from_nl(
        _FakeLLM(), "지난 3개월간 전체 서버별 월간 CPU 성능 통계를 조회해줘", _GP,
        stat_month=("202604", "202606"),
    )
    assert cov is not None and cov.covered, cov.reason if cov else "cov None"
    assert smq.time_breakdown is True
    assert "GROUP BY COALESCE(c.platform_resource_id, c.id), s.stat_date" in sql
    assert guard_counters().get("normalize.breakdown_promote") == 1


@pytest.mark.asyncio
async def test_monthly_gate_still_forces_fallback_when_not_promotable():
    """승격 조건(패턴 B + measure)에 못 미치는 월별 질의는 여전히 폴백이다(게이트 잔존)."""
    class _FakeLLM:
        async def ainvoke(self, messages):
            return SimpleNamespace(
                content='{"pattern": "B", "dimensions": ["name"], "measures": []}')

    reset_guard_counters()
    sql, _smq, cov = await compile_from_nl(
        _FakeLLM(), "서버별 월별 사용률 추이를 보여줘", _GP,
    )
    assert sql is None
    assert cov is not None and "월별 분해" in cov.reason
    assert guard_counters().get("gate.monthly_breakdown_fallback") == 1


# ──────────────────────────────────────────────
# S-IR3: order_by / limit IR 승격
# ──────────────────────────────────────────────

def _ranking_smq(**extra) -> dict:
    base = {
        "pattern": "B", "dimensions": ["name"], "time_grain": "month",
        "measures": [{"agg": "avg", "definition_name": "Utilization",
                      "resource_type": "server.Cpus"}],
    }
    base.update(extra)
    return base


def test_ir_order_by_and_limit_used_without_surface_words(model):
    """표면어가 없어도 IR order_by/limit만으로 정렬·상한이 결정된다."""
    sql = _compile(
        _ranking_smq(order_by={"field": "server.Cpus", "direction": "desc"}, limit=10),
        model, user_query="서버별 CPU 사용률", stat_month="202606",
    )
    assert 'ORDER BY "cpus_avg" DESC NULLS LAST' in sql
    assert sql.rstrip().endswith("LIMIT 10;")


def test_ir_limit_overrides_superlative_one_row_rule(model):
    """최상급 어휘가 있어도 IR limit이 있으면 그 값을 쓴다(상위 1건 축약은 IR 부재 시 규칙)."""
    sql = _compile(
        _ranking_smq(order_by={"field": "cpus_avg", "direction": "desc"}, limit=5),
        model, user_query="CPU 사용률이 가장 높은 서버들", stat_month="202606",
    )
    assert sql.rstrip().endswith("LIMIT 5;")


def test_ir_order_by_with_superlative_keeps_one_row(model):
    """IR 정렬 + 상한 미지정 + 최상급 어휘면 상위 1건을 유지한다(D-100)."""
    sql = _compile(
        _ranking_smq(order_by={"field": "server.Cpus", "direction": "desc"}),
        model, user_query="CPU 사용률이 가장 높은 서버", stat_month="202606",
    )
    assert sql.rstrip().endswith("LIMIT 1;")


def test_ir_order_by_accepts_dimension_and_key_variants(model):
    """dimension 이름 정렬과 LLM의 키 표기 흔들림(measure/dir)을 결정적으로 흡수한다."""
    sql = _compile(
        _ranking_smq(order_by={"field": "hostname", "dir": "asc"},
                     dimensions=["name", "hostname"]),
        model, stat_month="202606",
    )
    assert 'ORDER BY "hostname" ASC NULLS LAST' in sql
    swapped = _compile(
        _ranking_smq(order_by={"measure": "server.Cpus", "direction": "DESC"}),
        model, stat_month="202606",
    )
    assert 'ORDER BY "cpus_avg" DESC NULLS LAST' in swapped


def test_surface_ranking_remains_fallback_and_is_metered(model):
    """IR 정렬이 없으면 표면어 폴백이 그대로 동작하고 발동이 계측된다(R4)."""
    reset_guard_counters()
    sql = _compile(_ranking_smq(), model,
                   user_query="CPU 사용률이 가장 높은 서버", stat_month="202606")
    assert 'ORDER BY "cpus_avg" DESC NULLS LAST' in sql
    assert sql.rstrip().endswith("LIMIT 1;")
    assert guard_counters().get("ranking.surface_fallback") == 1


def test_ir_order_by_and_limit_out_of_range_are_outside(model):
    """해소 불가 정렬 대상·범위 밖 상한은 커버리지 밖(임의 컬럼 정렬 금지)."""
    assert "order_by 대상 미해소" in _reason(
        _ranking_smq(order_by={"field": "존재하지않는컬럼"}), model)
    assert "order_by 방향 미지원" in _reason(
        _ranking_smq(order_by={"field": "cpus_avg", "direction": "위로"}), model)
    assert "limit 범위 밖" in _reason(_ranking_smq(limit=0), model)
    assert "limit 범위 밖" in _reason(_ranking_smq(limit=999_999), model)


# ──────────────────────────────────────────────
# S-IR4: 필터 안전장 확대 (선언 정합 + 임계 + 기간 승격)
# ──────────────────────────────────────────────

def test_direct_column_filters_compile_to_having(model):
    """카탈로그 filterable 선언(name/hostname/ipaddress/avail_status)이 HAVING으로 조립된다.

    gp-006("비정상 서버 목록") 형태의 avail_status != 0, gp-007의 장비명 지목이 여기 해당한다.
    """
    sql = _compile(
        {"pattern": "A", "dimensions": ["name", "ipaddress"],
         "filters": [{"field": "avail_status", "op": "ne", "value": 0}]},
        model,
    )
    assert "HAVING MAX(CASE WHEN c.resource_type='server.Server' THEN c.avail_status END) <> 0" in sql

    multi = _compile(
        {"pattern": "A", "dimensions": ["name"],
         "filters": [{"field": "hostname", "op": "like", "value": "cocm-%"},
                     {"field": "name", "op": "in", "value": ["SV1", "SV2"]}]},
        model,
    )
    assert "THEN c.hostname END) LIKE 'cocm-%'" in multi
    assert "THEN c.name END) IN ('SV1', 'SV2')" in multi
    assert multi.count("HAVING") == 1 and "\n  AND MAX(" in multi


def test_measure_threshold_reuses_select_expression(model):
    """측정치 임계는 SELECT와 **동일한 집계식**으로 HAVING에 걸린다(alias 참조 불가)."""
    sql = _compile(
        _ranking_smq(filters=[{"field": "server.Cpus", "op": "gte", "value": 80}]),
        model, stat_month="202606",
    )
    select_expr = next(
        line.strip().split(" AS ")[0]
        for line in sql.splitlines() if 'AS "cpus_avg"' in line
    )
    assert f"HAVING {select_expr} >= 80" in sql


def test_measure_threshold_rejects_non_comparison_op(model):
    """집계값에 like/in은 의미가 없어 커버리지 밖이다."""
    assert "측정치 임계 op" in _reason(
        _ranking_smq(filters=[{"field": "server.Cpus", "op": "like", "value": "8%"}]),
        model)


def test_time_filter_promoted_to_time_range(model):
    """기간을 필터로 표현한 SMQ가 time_range로 승격되어 조립된다 (라이브 실측 형태).

    2026-07-30 스모크: 선택은 정확한데 기간만 `{'field': 'time', 'op': 'between',
    'value': ['202606','202606']}`로 나와 "미지원 필터"로 전량 폴백했다.
    """
    reset_guard_counters()
    smq = normalize_smq(
        SMQ.from_dict(_ranking_smq(
            filters=[{"field": "time", "op": "between", "value": ["202606", "202606"]}])),
        "2026년 6월 서버별 CPU 사용률", model,
    )
    assert smq.filters == []
    assert smq.time_range == ["202606"]
    assert guard_counters().get("normalize.time_filter_promote") == 1
    cov = check_coverage(smq, model)
    assert cov.covered, cov.reason
    # 호출부가 기간을 안 넘겨도 IR 기간으로 필터가 걸린다.
    assert "s.stat_date = '202606'" in compile_smq(smq, _GP, model)


def test_time_range_corrected_by_deterministic_parse(model):
    """LLM이 계산한 월은 질의의 결정적 해석으로 교정한다(D-035 결정적 우선)."""
    reset_guard_counters()
    smq = normalize_smq(
        SMQ.from_dict(_ranking_smq(time_range=["209901"])),
        "지난달 서버별 CPU 사용률", model,
    )
    expected = resolve_stat_month_range("지난달")
    assert smq.time_range == [expected[0]]
    assert guard_counters().get("normalize.time_range_override") == 1


def test_unparseable_period_filter_is_left_for_fallback(model):
    """기간 값을 결정적으로 못 뽑으면 필터를 남겨 폴백으로 돌린다(조건 조용히 버리기 금지)."""
    smq = normalize_smq(
        SMQ.from_dict(_ranking_smq(
            filters=[{"field": "period", "op": "gte", "value": "지난주"}])),
        "지난주 서버별 CPU 사용률", model,
    )
    assert smq.time_range is None
    assert [f.field for f in smq.filters] == ["period"]
    cov = check_coverage(smq, model)
    assert not cov.covered and "미지원 필터" in cov.reason


def test_time_filter_on_catalog_dimension_is_not_promoted(model):
    """카탈로그 dimension 필터는 값이 6자리여도 기간으로 오인하지 않는다."""
    smq = normalize_smq(
        SMQ.from_dict({"pattern": "A", "dimensions": ["name"],
                       "filters": [{"field": "name", "op": "eq", "value": "202606"}]}),
        "202606 서버 조회", model,
    )
    assert smq.time_range is None
    assert [f.field for f in smq.filters] == ["name"]


def test_value_index_gate_blocks_unproven_filter_literal(model):
    """값 인덱스가 그 필드를 수집했다면 미실증 리터럴은 커버리지 밖이다(S-IR4 실증 게이트)."""
    smq = SMQ.from_dict({"pattern": "A", "dimensions": ["name"],
                         "filters": [{"field": "name", "op": "eq", "value": "없는서버"}]})
    cov = check_coverage(smq, model, value_index={"name": ["cocm-hdkapp01"]})
    assert not cov.covered and "미검증 리터럴" in cov.reason
    # 실증된 값은 통과
    ok = SMQ.from_dict({"pattern": "A", "dimensions": ["name"],
                        "filters": [{"field": "name", "op": "eq", "value": "cocm-hdkapp01"}]})
    assert check_coverage(ok, model, value_index={"name": ["cocm-hdkapp01"]}).covered
    # 수집하지 않은 필드는 검증을 건너뛴다("가용 시" 게이트)
    assert check_coverage(smq, model, value_index={"resource_type": ["server.Server"]}).covered


def test_resource_type_filter_ignored_is_metered(model):
    """조립에 쓰지 않는 resource_type 필터는 무시 사실을 계측한다(침묵 무시 금지)."""
    reset_guard_counters()
    _compile({"pattern": "A", "dimensions": ["name"],
              "filters": [{"field": "resource_type", "op": "eq", "value": "server.Server"}]},
             model)
    assert guard_counters().get("compile.resource_type_filter_ignored") == 1


# ──────────────────────────────────────────────
# S-IR5: 패턴 C 알람 건수 + 랭킹
# ──────────────────────────────────────────────

def _alarm_count_smq(**extra) -> dict:
    base = {
        "pattern": "C", "entities": ["CMM_ALARM", "CMM_RESOURCE"],
        "dimensions": ["server_name"], "entity_count": True,
        "filters": [{"field": "ALARMSEVERITY", "op": "in", "value": [2, 3]}],
        "order_by": {"field": "alarm_count", "direction": "desc"},
        "limit": 10, "time_range": ["202604", "202606"],
    }
    base.update(extra)
    return base


def test_alarm_count_ranking_compiles(model):
    """gp-015("최근 3개월간 경고 이상 알람 최다 상위 10 서버") 형태."""
    sql = _compile(_alarm_count_smq(), model)
    assert "COUNT(*) AS alarm_count" in sql
    assert "GROUP BY SVR.NAME" in sql
    # NULLS LAST 필수(D-098) — 어댑터 검증도 "집계 내림차순 + 행 제한"에 이를 요구한다.
    assert "ORDER BY alarm_count DESC NULLS LAST" in sql
    assert "CA.ALARMSEVERITY IN (2, 3)" in sql
    assert "CA.CTIME >= DATE('2026-04-01')" in sql
    assert "CA.CTIME < DATE('2026-07-01')" in sql
    assert sql.rstrip().endswith("LIMIT 10;")
    # 건수 집계에서는 선별 근거(알람명·심각도)를 덧붙이지 않는다 — 그룹이 쪼개진다.
    assert "D.NAME AS alarm_name" not in sql
    assert "CA.ALARMSEVERITY AS severity" not in sql


def test_alarm_count_defaults_to_count_desc(model):
    """IR 정렬이 없으면 건수 내림차순으로 정렬한다(모델 기본 정렬은 GROUP BY 밖 컬럼)."""
    sql = _compile(_alarm_count_smq(order_by=None), model)
    assert "ORDER BY alarm_count DESC NULLS LAST" in sql
    assert "CA.CTIME DESC" not in sql


def test_alarm_count_db2_dialect(model):
    """DB2는 FETCH FIRST로 상한을 걸고 기간 리터럴은 엔진 공통 DATE()를 쓴다."""
    sql = _compile(_alarm_count_smq(), load_semantic_model(_B0), db_id=_B0)
    assert "POLESTAR.cmm_resource CR" in sql
    assert "DATE('2026-04-01')" in sql
    assert sql.rstrip().endswith("FETCH FIRST 10 ROWS ONLY;")


def test_alarm_global_count_without_dimensions(model):
    """dimension 없는 알람 건수는 전체 건수 단일 행이다(GROUP BY 없음)."""
    sql = _compile(_alarm_count_smq(dimensions=[], order_by=None, limit=None), model)
    assert "COUNT(*) AS alarm_count" in sql
    assert "GROUP BY" not in sql


def test_alarm_shape_and_order_rules(model):
    """알람은 전역/기간분해 형태 미지원이고, 정렬 대상은 카탈로그·건수 alias로 한정된다."""
    assert "형태 미지원" in _reason(_alarm_count_smq(global_aggregate=True), model)
    assert "order_by 대상 미해소" in _reason(
        _alarm_count_smq(order_by={"field": "임의컬럼"}), model)
    assert "미지원 알람 필터" in _reason(
        _alarm_count_smq(filters=[{"field": "CONDITIONLOGTEXT", "op": "like", "value": "x"}]),
        model)


# ──────────────────────────────────────────────
# 확장 필드 부재 시 기존 경로 보존 + gold_smq 호환
# ──────────────────────────────────────────────

def test_absent_ir_fields_keep_legacy_sql(model):
    """확장 필드를 명시적으로 비활성화한 SMQ는 필드 부재 SMQ와 동일한 SQL을 만든다."""
    legacy = {"pattern": "B", "dimensions": ["name"], "time_grain": "month",
              "measures": [{"agg": "avg", "definition_name": "Utilization",
                            "resource_type": "server.Cpus"}]}
    explicit = dict(legacy, global_aggregate=False, entity_count=False,
                    time_breakdown=False, order_by=None, limit=None, time_range=None)
    kwargs = {"user_query": "서버별 CPU 사용률", "stat_month": "202606"}
    assert _compile(legacy, model, **kwargs) == _compile(explicit, model, **kwargs)


def test_from_dict_accepts_gold_smq_without_new_fields():
    """gold_smq(확장 필드 없음)를 그대로 수용하고 기본값은 비활성이다."""
    smq = SMQ.from_dict({"pattern": "A", "dimensions": ["OSType"], "measures": [],
                         "filters": [], "time_grain": None})
    assert (smq.global_aggregate, smq.entity_count, smq.time_breakdown) == (False, False, False)
    assert smq.order_by is None and smq.limit is None and smq.time_range is None


def test_to_match_dict_roundtrip_with_ir_fields():
    """확장 필드가 실린 SMQ도 to_match_dict → from_dict 라운드트립이 보존된다."""
    smq = SMQ.from_dict(_alarm_count_smq())
    again = SMQ.from_dict(smq.to_match_dict())
    assert again.to_match_dict() == smq.to_match_dict()


@pytest.mark.parametrize("name,smq_dict,kwargs", [
    ("entity_count", {"pattern": "A", "global_aggregate": True, "entity_count": True}, {}),
    ("global_measure",
     {"pattern": "B", "global_aggregate": True, "time_grain": "month",
      "measures": [{"agg": "avg", "definition_name": "Utilization",
                    "resource_type": "server.Cpus"}]}, {"stat_month": "202606"}),
    ("time_breakdown",
     {"pattern": "B", "dimensions": ["name"], "time_breakdown": True,
      "time_grain": "month",
      "measures": [{"agg": "avg", "definition_name": "Utilization",
                    "resource_type": "server.Cpus"}]},
     {"stat_month": ("202604", "202606")}),
    ("filters_and_ranking",
     {"pattern": "B", "dimensions": ["name"], "time_grain": "month",
      "measures": [{"agg": "avg", "definition_name": "Utilization",
                    "resource_type": "server.Cpus"}],
      "filters": [{"field": "name", "op": "like", "value": "cocm-%"},
                  {"field": "server.Cpus", "op": "gte", "value": 80}],
      "order_by": {"field": "server.Cpus"}, "limit": 10}, {"stat_month": "202606"}),
    ("alarm_count", _alarm_count_smq(), {}),
])
def test_extended_sql_passes_polestar_validators(name, smq_dict, kwargs):
    """확장 형태 SQL이 폴스타 어댑터 검증 5종을 전부 통과한다(자기정합 회귀 가드).

    이 가드가 실제로 결함을 잡았다 — 알람 건수 랭킹의 `ORDER BY alarm_count DESC`에
    NULLS LAST가 없어 D-098 검증에 걸렸고(검증 실패는 결정적 조립 SQL이 런타임에 반려된다는
    뜻), 조립 쪽을 고쳐 해소했다.
    """
    from src.db_adapters import get_adapter

    m = load_semantic_model("polestar")
    sql = compile_smq(SMQ.from_dict(smq_dict), "polestar", m, **kwargs)
    adapter = get_adapter("polestar", {"polestar"})
    errors = [e for check in adapter.validator_checks() for e in check(sql)]
    assert errors == [], f"{name}: {errors}"


# ──────────────────────────────────────────────
# R4: 교정 가드 발동 계측
# ──────────────────────────────────────────────

def test_correction_guards_are_metered(model):
    """normalize의 교정 가드(코어 치환·용량 주입)가 각각 계측된다."""
    reset_guard_counters()
    normalize_smq(
        SMQ.from_dict({"pattern": "A", "dimensions": ["PHYSICALCORE"]}),
        "서버들의 CPU 용량 리스트", model,
    )
    counters = guard_counters()
    assert counters.get("normalize.physicalcore_swap") == 1

    reset_guard_counters()
    normalize_smq(
        SMQ.from_dict({"pattern": "A", "dimensions": ["LOGICALCORE", "PHYSICALCORE"]}),
        "서버들의 CPU, 메모리 용량 리스트", model,
    )
    counters = guard_counters()
    assert counters.get("normalize.physicalcore_drop") == 1
    assert counters.get("normalize.capacity_inject") == 1


@pytest.mark.asyncio
async def test_guard_delta_recorded_in_derivation_sink():
    """질의 1건에서 발동한 가드가 도출 레코드에 귀속 기록된다(stepwise ON 관측)."""
    class _FakeLLM:
        async def ainvoke(self, messages):
            return SimpleNamespace(content=(
                '{"pattern": "B", "dimensions": ["name"], "time_grain": "month", '
                '"measures": [{"agg": "avg", "definition_name": "Utilization", '
                '"resource_type": "server.Cpus"}]}'
            ))

    reset_guard_counters()
    sink: list[dict] = [{"path": "single"}]
    sql, _smq, cov = await compile_from_nl(
        _FakeLLM(), "CPU 사용률이 가장 높은 서버", _GP, stat_month="202606",
        derivation_sink=sink,
    )
    assert cov is not None and cov.covered and sql is not None
    assert sink[-1]["guards"] == {"ranking.surface_fallback": 1}
