"""시맨틱 컴파일러 골든 회귀 테스트 (Plan 61 트랙 C / E6-4 / D-076).

폐쇄망 CI 전제 — 실제 DB·LLM 없이 통과해야 한다(SMQ→SQL은 결정적 조립이라 DB 불필요).
검증:
    1. 골드셋 gold_smq 항목: SMQ 라운드트립(smq_match)·커버리지 내·결정적 컴파일 성공.
    2. 패턴 A/B/C 컴파일 구조(resource_type 구분 CASE WHEN·단일 GROUP BY·알람 정규화 조인).
    3. 엔진별 방언(PG ::numeric/LIMIT, DB2 CAST DECIMAL/FETCH FIRST·POLESTAR 스키마).
    4. 커버리지 판정(LOB·미정의·서버필터·기간필터 → 밖) + E5-2 값 인덱스 리터럴 검증.
    5. NL→SMQ 파싱·coverage_router(mock LLM)·플래그 OFF 무진입.
    6. 결정성(같은 SMQ → 동일 SQL) + Model/MODEL 대소문자 충돌 해소.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from src.nodes.semantic_compiler import (
    SMQ,
    check_coverage,
    compile_from_nl,
    compile_smq,
    load_semantic_model,
    parse_smq_response,
    render_catalog,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GOLD_DIR = _REPO_ROOT / "testdata" / "text2sql_gold"
_DB_IDS = ["polestar_cm_gp", "polestar_cm_yd", "polestar_b0"]


def _load_harness():
    """scripts/eval_text2sql.py를 모듈로 로드해 load_goldset/smq_match를 재사용한다."""
    path = _REPO_ROOT / "scripts" / "eval_text2sql.py"
    spec = importlib.util.spec_from_file_location("eval_text2sql", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_HARNESS = _load_harness()


def _gold_items_with_smq():
    """gold_smq를 가진 골드셋 항목만 추린다(파라미터라이즈 소스)."""
    items = _HARNESS.load_goldset(_GOLD_DIR)
    return [it for it in items if it.gold_smq]


# ──────────────────────────────────────────────
# 1. 시맨틱 모델 로드
# ──────────────────────────────────────────────

@pytest.mark.parametrize("db_id", _DB_IDS)
def test_semantic_model_loads_with_three_patterns(db_id):
    model = load_semantic_model(db_id, use_cache=False)
    assert model is not None, f"{db_id} 시맨틱 모델 로드 실패"
    assert model.get("pattern_a", {}).get("dimensions"), "패턴 A dimension 없음"
    assert model.get("pattern_b", {}).get("measures"), "패턴 B measure 없음"
    assert model.get("pattern_c", {}).get("entities"), "패턴 C 엔터티 없음"


# ──────────────────────────────────────────────
# 2. 골드셋 gold_smq — 라운드트립 + 커버리지 + 컴파일
# ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "item", _gold_items_with_smq(), ids=lambda it: it.id
)
def test_gold_smq_roundtrip_and_compile(item):
    smq = SMQ.from_dict(item.gold_smq)
    # 라운드트립: to_match_dict가 골드 SMQ와 하네스 기준 일치
    assert _HARNESS.smq_match(item.gold_smq, smq.to_match_dict()), (
        f"{item.id} SMQ 라운드트립 불일치: {smq.to_match_dict()}"
    )
    model = load_semantic_model(item.db_id, use_cache=False)
    cov = check_coverage(smq, model)
    if item.coverage == "inside":
        assert cov.covered, f"{item.id} inside인데 커버리지 밖: {cov.reason}"
        sql = compile_smq(smq, item.db_id, model)
        assert sql.strip().upper().startswith("SELECT")
        assert sql.rstrip().endswith(";")


# ──────────────────────────────────────────────
# 3. 패턴별 컴파일 구조 (골든 스냅샷 성격)
# ──────────────────────────────────────────────

def test_pattern_a_pivot_structure():
    m = load_semantic_model("polestar_cm_gp")
    smq = SMQ.from_dict({"pattern": "A", "dimensions": ["Hostname", "OSType", "Vendor"]})
    sql = compile_smq(smq, "polestar_cm_gp", m)
    # Hostname은 EAV가 아니라 direct 컬럼(D-058/D-061)
    assert "c.hostname END) AS \"hostname\"" in sql
    assert "cc.name='OSType'" in sql and "cc.name='Vendor'" in sql
    assert "GROUP BY COALESCE(c.platform_resource_id, c.id)" in sql
    assert sql.count("GROUP BY") == 1


def test_pattern_a_child_resource_types():
    m = load_semantic_model("polestar_cm_gp")
    smq = SMQ.from_dict({"pattern": "A", "dimensions": ["LOGICALCORE", "TotalSize"]})
    sql = compile_smq(smq, "polestar_cm_gp", m)
    assert "c.resource_type='server.Cpus' AND cc.name='LOGICALCORE'" in sql
    assert "c.resource_type='server.Memory' AND cc.name='TotalSize'" in sql


def test_pattern_b_measures_pivot():
    m = load_semantic_model("polestar_cm_gp")
    smq = SMQ.from_dict({
        "pattern": "B", "dimensions": ["name"],
        "measures": [
            {"agg": "avg", "definition_name": "Utilization", "resource_type": "server.Cpus"},
            {"agg": "max", "definition_name": "Utilization", "resource_type": "server.Memory"},
        ],
        "time_grain": "month",
    })
    sql = compile_smq(smq, "polestar_cm_gp", m)
    assert "LEFT JOIN polestar.cmm_metric_stat_m s" in sql
    assert "AVG(CASE WHEN c.resource_type='server.Cpus' AND s.definition_name='Utilization' AND s.avg_val BETWEEN 0 AND 1000 THEN s.avg_val END)::numeric" in sql
    assert "MAX(CASE WHEN c.resource_type='server.Memory' AND s.definition_name='Utilization' AND s.max_val BETWEEN 0 AND 1000 THEN s.max_val END)::numeric" in sql


def test_pattern_b_injects_default_identity_dimensions_when_empty():
    """measure만 있고 dimension이 비면 서버 식별 dimension(name)을 결정적으로 주입한다.

    실측상 LLM SMQ가 dimensions=[]로 값만 나열하는 선택 누락을 범한다(Plan 61 §7 SMQ 정확도 축).
    프롬프트 유도가 아니라 모델 pattern_b.default_dimensions 기반 코드 보정이다(D-035, D-076 후속).
    주입은 최소 식별자(name)만 — hostname까지 주입하면 질의 미명시 컬럼이 끼어
    컬럼 스코프 규약 위반(2026-07-21 b0-004 골드 5컬럼 정합).
    """
    m = load_semantic_model("polestar_cm_gp")
    smq = SMQ.from_dict({
        "pattern": "B", "dimensions": [],
        "measures": [{"agg": "avg", "definition_name": "Utilization", "resource_type": "server.Cpus"}],
        "time_grain": "month",
    })
    sql = compile_smq(smq, "polestar_cm_gp", m)
    assert 'THEN c.name END) AS "name"' in sql
    assert 'AS "hostname"' not in sql


def test_pattern_b_injects_identity_when_only_attribute_dimensions():
    """속성(용량) dimension만 고르고 식별 dimension이 없으면 name을 주입한다.

    실측(2026-07-21 yd-004): LLM SMQ가 [LOGICALCORE, TotalSize]+사용률 measure만 선택해
    서버 이름 없는 사용률 리스트가 나옴 — dimension이 완전히 빈 경우만 보정하던 기존
    게이트의 사각지대. 식별 direct 컬럼(name/hostname/ipaddress)이 하나도 없으면 주입한다.
    """
    m = load_semantic_model("polestar_cm_gp")
    smq = SMQ.from_dict({
        "pattern": "B", "dimensions": ["LOGICALCORE", "TotalSize"],
        "measures": [
            {"agg": "avg", "definition_name": "Utilization", "resource_type": "server.Cpus"},
            {"agg": "max", "definition_name": "Utilization", "resource_type": "server.Cpus"},
        ],
        "time_grain": "month",
    })
    sql = compile_smq(smq, "polestar_cm_gp", m)
    assert 'THEN c.name END) AS "name"' in sql
    assert "cc.name='LOGICALCORE'" in sql
    assert "cc.name='TotalSize'" in sql
    assert 'AS "hostname"' not in sql


def test_monthly_breakdown_regex_gate():
    """월별 분해("월간/월별") 질의는 패턴 B 컴파일에서 결정적으로 제외한다(D-087 후속2).

    실측(2026-07-21 gp-010): 프롬프트 단서만으로는 LLM이 "서버별 월간 통계"를 패턴 B로
    과포획(월별 4725행 → 서버당 1행 1820행 붕괴, 보강 후에도 재발) → 코드 게이트.
    "N개월간"의 '월간'은 기간 표현이므로 매칭하지 않는다(부정 후방탐색).
    """
    from src.nodes.semantic_compiler import _MONTHLY_BREAKDOWN_RE

    assert _MONTHLY_BREAKDOWN_RE.search("지난 3개월간 전체 서버별 월간 CPU 성능 통계를 조회해줘")
    assert _MONTHLY_BREAKDOWN_RE.search("서버별 월별 사용률 추이를 보여줘")
    # 기간 표현만 있는 질의는 컴파일 유지(과차단 금지)
    assert not _MONTHLY_BREAKDOWN_RE.search("서버들의 사용률 리스트. 지난달 1개월 통계 기준으로.")
    assert not _MONTHLY_BREAKDOWN_RE.search("최근 6개월간 평균 사용률 리스트를 조회해줘")


def test_normalize_smq_drops_physicalcore_without_physical_hint():
    """LOGICALCORE·PHYSICALCORE 동시 선택 시 질의에 '물리' 신호가 없으면 PHYSICALCORE 제거.

    실측(2026-07-21 yd-004): "CPU 용량"에 LLM이 둘 다 선택 — 운영 관행(VM 위주)은
    LOGICALCORE. 프롬프트 유도가 아니라 결정적 교정 가드(D-076 후속).
    """
    from src.nodes.semantic_compiler import normalize_smq

    smq = SMQ.from_dict({
        "pattern": "B", "dimensions": ["LOGICALCORE", "PHYSICALCORE", "TotalSize"],
        "measures": [{"agg": "avg", "definition_name": "Utilization", "resource_type": "server.Cpus"}],
    })
    out = normalize_smq(smq, "서버들의 CPU, 메모리 용량 및 사용률 리스트를 조회해줘")
    assert out.dimensions == ["LOGICALCORE", "TotalSize"]
    # '물리' 명시 시 선택 존중(불변)
    kept = normalize_smq(smq, "서버들의 물리 코어 수와 논리 코어 수를 조회해줘")
    assert kept.dimensions == ["LOGICALCORE", "PHYSICALCORE", "TotalSize"]
    # PHYSICALCORE 단독 선택 + '물리' 미명시 → LOGICALCORE로 치환
    # (실측 2026-07-21 gp-009 회귀: 같은 질의에서 단독 선택으로도 흔들림)
    only_phys = SMQ.from_dict({"pattern": "A", "dimensions": ["PHYSICALCORE", "TotalSize"]})
    swapped = normalize_smq(only_phys, "서버들의 CPU, 메모리 용량 리스트를 조회해줘")
    assert swapped.dimensions == ["LOGICALCORE", "TotalSize"]
    # '물리' 명시 시 단독 선택도 불변
    assert normalize_smq(only_phys, "물리 코어 수를 조회해줘").dimensions == ["PHYSICALCORE", "TotalSize"]


def test_normalize_smq_injects_omitted_capacity_dimensions():
    """질의가 명시한 용량 차원을 SMQ가 통째로 누락하면 결정적으로 주입한다.

    실측(2026-07-21 gp-009 2차 회귀): 같은 질의("CPU, 메모리 용량 및 사용률")에서 SMQ 선택이
    동시선택→단독선택→미선택으로 흔들림 — 치환 가드로도 미선택 케이스는 못 잡아 주입 가드 신설.
    """
    from src.nodes.semantic_compiler import normalize_smq

    q = "전체 서버들의 CPU, 메모리 용량 및 사용률(평균·최대) 리스트를 조회해줘. 사용률은 지난달 1개월 통계 기준으로."
    # CPU 용량 차원 누락 → LOGICALCORE 주입
    smq = SMQ.from_dict({
        "pattern": "B", "dimensions": ["TotalSize"],
        "measures": [{"agg": "avg", "definition_name": "Utilization", "resource_type": "server.Cpus"}],
    })
    assert normalize_smq(smq, q).dimensions == ["TotalSize", "LOGICALCORE"]
    # 둘 다 누락 → 둘 다 주입
    empty = SMQ.from_dict({
        "pattern": "B", "dimensions": [],
        "measures": [{"agg": "avg", "definition_name": "Utilization", "resource_type": "server.Cpus"}],
    })
    assert set(normalize_smq(empty, q).dimensions) == {"LOGICALCORE", "TotalSize"}
    # 이미 있으면 불변(중복 주입 금지)
    full = SMQ.from_dict({"pattern": "B", "dimensions": ["LOGICALCORE", "TotalSize"]})
    assert normalize_smq(full, q).dimensions == ["LOGICALCORE", "TotalSize"]
    # 용량을 묻지 않는 질의는 주입하지 않음(사용률만)
    usage_only = SMQ.from_dict({
        "pattern": "B", "dimensions": [],
        "measures": [{"agg": "avg", "definition_name": "Utilization", "resource_type": "server.Cpus"}],
    })
    assert normalize_smq(usage_only, "서버들의 CPU, 메모리 사용률(평균·최대) 리스트를 조회해줘").dimensions == []
    # 알람(패턴 C)은 무관 — 불변
    alarm = SMQ.from_dict({"pattern": "C", "dimensions": []})
    assert normalize_smq(alarm, "CPU 용량 알람").dimensions == []


def test_pattern_b_explicit_dimensions_not_overridden_by_default():
    """명시 dimension이 있으면 default_dimensions를 주입하지 않는다(LLM 선택 존중)."""
    m = load_semantic_model("polestar_cm_gp")
    smq = SMQ.from_dict({
        "pattern": "B", "dimensions": ["ipaddress"],
        "measures": [{"agg": "avg", "definition_name": "Utilization", "resource_type": "server.Cpus"}],
    })
    sql = compile_smq(smq, "polestar_cm_gp", m)
    assert 'THEN c.ipaddress END) AS "ipaddress"' in sql
    assert 'AS "hostname"' not in sql


def test_pattern_b_maxiorate_and_mixed_definition_names():
    m = load_semantic_model("polestar_cm_gp")
    smq = SMQ.from_dict({
        "pattern": "B", "dimensions": ["name"],
        "measures": [
            {"agg": "avg", "definition_name": "Utilization", "resource_type": "server.Cpus"},
            {"agg": "avg", "definition_name": "MaxIORate", "resource_type": "server.Disks"},
        ],
        "time_grain": "month",
    })
    sql = compile_smq(smq, "polestar_cm_gp", m)
    # 여러 definition_name → 조인 필터는 IN으로 확장
    assert "s.definition_name IN ('MaxIORate', 'Utilization')" in sql
    assert "s.definition_name='MaxIORate' THEN s.avg_val" in sql


def test_pattern_b_time_grain_selects_table():
    m = load_semantic_model("polestar_cm_gp")
    for grain, table in [("hour", "cmm_metric_stat_h"), ("day", "cmm_metric_stat_d"), ("month", "cmm_metric_stat_m")]:
        smq = SMQ.from_dict({
            "pattern": "B", "dimensions": ["name"],
            "measures": [{"agg": "avg", "definition_name": "Utilization", "resource_type": "server.Cpus"}],
            "time_grain": grain,
        })
        sql = compile_smq(smq, "polestar_cm_gp", m)
        assert f"LEFT JOIN polestar.{table} s" in sql


def test_pattern_c_alarm_active_joins():
    m = load_semantic_model("polestar_cm_gp")
    smq = SMQ.from_dict({
        "pattern": "C",
        "entities": ["CMM_ALARM", "CMM_ALARM_DEF", "CMM_ALARM_ACTIVE", "CMM_RESOURCE"],
        "dimensions": ["ALARMSEVERITY", "CTIME", "NAME"],
        "filters": [{"field": "ALARMSEVERITY", "op": "eq", "value": 3}],
        "active_only": True,
    })
    sql = compile_smq(smq, "polestar_cm_gp", m)
    assert "JOIN polestar.cmm_alarm CA ON CA.RESOURCE_ID = CR.ID" in sql
    assert "JOIN polestar.cmm_alarm_def D ON CA.DEFINITION_ID = D.ID" in sql
    assert "JOIN polestar.cmm_alarm_active A ON A.ALARM_ID = CA.ID" in sql
    assert "CA.ALARMSEVERITY = 3" in sql
    assert "CR.DTIME IS NULL" in sql


def test_pattern_c_history_no_active_join_default_severity():
    m = load_semantic_model("polestar_cm_gp")
    smq = SMQ.from_dict({
        "pattern": "C", "entities": ["CMM_ALARM", "CMM_RESOURCE"],
        "dimensions": ["NAME", "CTIME"], "active_only": False,
    })
    sql = compile_smq(smq, "polestar_cm_gp", m)
    assert "cmm_alarm_active" not in sql
    assert "CA.ALARMSEVERITY IN (0, 1, 2, 3)" in sql


def test_pattern_c_empty_dimensions_compile_context_columns():
    """컬럼 미명시 알람 질의(빈 dimensions)는 알람 선별 근거(서버명·알람명·심각도)를 결정적으로 포함한다.

    머지(2026-07-22): `_compile_c` 알람 SELECT는 D-100(팀장 채택 — context 항상 prepend)으로
    확정. 빈 dimensions는 default_dimensions 표준 뷰(구 UX D-076 후속7) 대신 `_ALARM_CONTEXT_DIMS`
    (서버명·알람명·심각도)를 결정적으로 조립한다 — 결정적 컴파일 경로라 실행마다 오가는
    플립플롭이 원천 불가(yd-006 우려 무효화). 부모 서버(SVR) 기준 식별은 시맨틱 모델이 정의.
    """
    m = load_semantic_model("polestar_cm_gp")
    smq = SMQ.from_dict({
        "pattern": "C", "entities": ["CMM_ALARM", "CMM_ALARM_DEF", "CMM_RESOURCE"],
        "dimensions": [],
        "filters": [{"field": "ALARMSEVERITY", "op": "in", "value": [2, 3]}],
        "active_only": False,
    })
    sql = compile_smq(smq, "polestar_cm_gp", m)
    # D-100: 빈 dimensions여도 선별 근거 3종을 결정적으로 포함
    assert "SVR.NAME AS server_name" in sql
    assert "D.NAME AS alarm_name" in sql
    assert "CA.ALARMSEVERITY AS severity" in sql
    assert "LEFT JOIN polestar.cmm_resource SVR" in sql          # 부모 서버 조인(모델 정의)
    # 결정적 컴파일 — 실행마다 동일(플립플롭 불가)
    assert compile_smq(smq, "polestar_cm_gp", m) == sql
    assert "ORDER BY CA.CTIME DESC, CA.ID DESC" in sql           # 경계 동률 tiebreak
    assert "CA.ALARMSEVERITY IN (2, 3)" in sql


def test_pattern_c_explicit_dimensions_skip_default_view():
    """특정 컬럼을 명시 선택하면 표준 뷰를 주입하지 않는다(요청 항목만)."""
    m = load_semantic_model("polestar_cm_gp")
    smq = SMQ.from_dict({
        "pattern": "C", "entities": ["CMM_ALARM", "CMM_RESOURCE"],
        "dimensions": ["ALARMSEVERITY", "CTIME"], "active_only": True,
    })
    sql = compile_smq(smq, "polestar_cm_gp", m)
    assert "alarm_id" not in sql
    assert "severity_grade" not in sql


# ──────────────────────────────────────────────
# 4. 엔진별 방언 (DB2 b0)
# ──────────────────────────────────────────────

def test_db2_dialect_decimal_and_fetch_first():
    m = load_semantic_model("polestar_b0")
    smq = SMQ.from_dict({
        "pattern": "B", "dimensions": ["name"],
        "measures": [{"agg": "avg", "definition_name": "Utilization", "resource_type": "server.Cpus"}],
        "time_grain": "month",
    })
    sql = compile_smq(smq, "polestar_b0", m)
    assert "POLESTAR.cmm_resource" in sql          # 대문자 스키마(D-057)
    assert "CAST(s.avg_val AS DOUBLE)" in sql      # 고정 정밀도 DECIMAL은 SQL0413N 위험(D-086)
    assert "::numeric" not in sql
    assert sql.rstrip().endswith("FETCH FIRST 100 ROWS ONLY;")


def test_db2_alarm_fetch_first():
    m = load_semantic_model("polestar_b0")
    smq = SMQ.from_dict({
        "pattern": "C", "entities": ["CMM_ALARM", "CMM_RESOURCE"],
        "dimensions": ["ALARMSEVERITY", "CTIME"], "active_only": True,
    })
    sql = compile_smq(smq, "polestar_b0", m)
    assert "POLESTAR.cmm_resource CR" in sql
    assert "FETCH FIRST" in sql and "LIMIT" not in sql


# ──────────────────────────────────────────────
# 5. 커버리지 판정 (밖으로 돌려야 하는 케이스)
# ──────────────────────────────────────────────

@pytest.mark.parametrize("smq_dict,expect_reason_kw", [
    ({"pattern": "A", "dimensions": ["OSParameter"]}, "LOB"),
    ({"pattern": "A", "dimensions": ["존재하지않는속성"]}, "미정의 dimension"),
    ({"pattern": "A", "dimensions": ["name"],
      "filters": [{"field": "name", "op": "eq", "value": "SV1"}]}, "미지원 필터"),
    ({"pattern": "B", "dimensions": ["name"],
      "measures": [{"agg": "avg", "definition_name": "Nope", "resource_type": "server.Cpus"}]}, "미정의 measure"),
    ({"pattern": "C", "entities": ["CMM_ALARM"], "dimensions": ["NAME"],
      "filters": [{"field": "CTIME", "op": "gte", "value": "x"}]}, "미지원 알람 필터"),
    ({"pattern": "C", "entities": ["NOPE_TABLE"], "dimensions": ["NAME"]}, "미정의 알람 엔터티"),
])
def test_coverage_out_cases(smq_dict, expect_reason_kw):
    m = load_semantic_model("polestar_cm_gp")
    cov = check_coverage(SMQ.from_dict(smq_dict), m)
    assert not cov.covered
    assert expect_reason_kw in cov.reason


def test_coverage_empty_smq_out():
    m = load_semantic_model("polestar_cm_gp")
    assert not check_coverage(SMQ.from_dict({"pattern": "A"}), m).covered


# E5-2 값 검색 연결 — 미검증 리터럴은 밖으로
def test_value_index_literal_validation():
    m = load_semantic_model("polestar_cm_gp")
    smq = SMQ.from_dict({"pattern": "A", "dimensions": ["name"],
                         "filters": [{"field": "resource_type", "op": "eq", "value": "server.Xyz"}]})
    # 값 인덱스에 없는 리터럴 → 커버리지 밖
    cov = check_coverage(smq, m, value_index={"resource_type": ["server.Server"]})
    assert not cov.covered and "미검증 리터럴" in cov.reason
    # 값 인덱스에 있으면(그리고 필터필드가 허용범위면) 통과 — resource_type eq는 AB safe 필터가 아니므로
    # 여기서는 like(무시)로 확인
    smq2 = SMQ.from_dict({"pattern": "A", "dimensions": ["name"],
                          "filters": [{"field": "resource_type", "op": "like", "value": "server.%"}]})
    assert check_coverage(smq2, m, value_index={"resource_type": ["server.Server"]}).covered


# ──────────────────────────────────────────────
# 6. Model/MODEL 대소문자 충돌 해소
# ──────────────────────────────────────────────

def test_model_case_collision_resolution():
    m = load_semantic_model("polestar_cm_gp")
    sql = compile_smq(SMQ.from_dict({"pattern": "A", "dimensions": ["Model", "MODEL"]}), "polestar_cm_gp", m)
    assert "c.resource_type='server.Server' AND cc.name='Model'" in sql   # 서버 모델명
    assert "c.resource_type='server.Cpus' AND cc.name='MODEL'" in sql     # CPU 모델명


# ──────────────────────────────────────────────
# 7. 결정성
# ──────────────────────────────────────────────

def test_compile_is_deterministic():
    m = load_semantic_model("polestar_cm_gp")
    smq = SMQ.from_dict({
        "pattern": "B", "dimensions": ["name", "hostname"],
        "measures": [{"agg": "avg", "definition_name": "Utilization", "resource_type": "server.Cpus"}],
        "time_grain": "month",
    })
    a = compile_smq(smq, "polestar_cm_gp", m)
    b = compile_smq(SMQ.from_dict(smq.to_match_dict()), "polestar_cm_gp", m)
    assert a == b


# ──────────────────────────────────────────────
# 8. NL→SMQ 파싱 + render_catalog
# ──────────────────────────────────────────────

def test_parse_smq_response_variants():
    assert parse_smq_response('{"pattern": "A", "dimensions": ["OSType"]}').pattern == "A"
    fenced = '```json\n{"pattern": "C", "entities": ["CMM_ALARM"], "dimensions": ["NAME"]}\n```'
    assert parse_smq_response(fenced).pattern == "C"
    assert parse_smq_response('{"pattern": "none"}') is None       # 커버리지 밖 신호
    assert parse_smq_response("설명만 있고 JSON 없음") is None
    assert parse_smq_response('{"pattern": "Z"}') is None          # 미지원 패턴


def test_render_catalog_contains_sections():
    cat = render_catalog(load_semantic_model("polestar_cm_gp"))
    assert "OSType" in cat and "server.Cpus" in cat
    assert "Utilization" in cat
    assert "CMM_ALARM" in cat


# ──────────────────────────────────────────────
# 9. coverage_router (compile_from_nl) — mock LLM
# ──────────────────────────────────────────────

class _MockLLM:
    def __init__(self, content):
        self._content = content

    async def ainvoke(self, messages):
        class _R:
            content = self._content
        return _R()


@pytest.mark.asyncio
async def test_compile_from_nl_inside_compiles():
    llm = _MockLLM('{"pattern": "A", "resource_types": ["server.Server"], "dimensions": ["OSType", "Vendor"]}')
    sql, smq, cov = await compile_from_nl(llm, "OS종류와 벤더 조회", "polestar_cm_gp")
    assert sql and sql.strip().upper().startswith("SELECT")
    assert smq.pattern == "A" and cov.covered


@pytest.mark.asyncio
async def test_compile_from_nl_none_falls_back():
    llm = _MockLLM('{"pattern": "none"}')
    sql, smq, cov = await compile_from_nl(llm, "유사한 사양 서버 찾아줘", "polestar_cm_gp")
    assert sql is None and smq is None    # 파싱 단계 커버리지 밖 → 폴백


@pytest.mark.asyncio
async def test_compile_from_nl_coverage_out_returns_none_sql():
    llm = _MockLLM('{"pattern": "A", "dimensions": ["OSParameter"]}')
    sql, smq, cov = await compile_from_nl(llm, "OS파라미터 조회", "polestar_cm_gp")
    assert sql is None and smq is not None and not cov.covered   # SMQ는 나왔지만 커버리지 밖


@pytest.mark.asyncio
async def test_compile_from_nl_llm_error_falls_back():
    class _BoomLLM:
        async def ainvoke(self, messages):
            raise RuntimeError("boom")
    sql, smq, cov = await compile_from_nl(_BoomLLM(), "질의", "polestar_cm_gp")
    assert sql is None and smq is None    # LLM 실패는 폴백(회귀 0)


# ──────────────────────────────────────────────
# 선행 스코프 + 순위 결정적 조립 (D-099)
# ──────────────────────────────────────────────

def _scoped_smq():
    """사용자 실측 질의(알람 서버 중 6월 CPU 최고 서버의 제조사·일련번호)의 SMQ."""
    return SMQ.from_dict({
        "pattern": "B",
        "dimensions": ["Vendor", "SerialNumber"],
        "measures": [{"agg": "avg", "definition_name": "Utilization",
                      "resource_type": "server.Cpus"}],
        "time_grain": "month",
    })


def test_server_scope_compiles_to_having():
    """선행 결과 서버 스코프가 HAVING 집계 필터로 결정적 조립된다(D-099)."""
    m = load_semantic_model("polestar")
    sql = compile_smq(
        _scoped_smq(), "polestar", m, stat_month="202606",
        server_scope=("name", ["SV-WEB-001", "SV-BATCH-009"]),
    )
    assert "HAVING MAX(CASE WHEN c.resource_type='server.Server' THEN c.name END) IN " in sql
    assert "'SV-WEB-001', 'SV-BATCH-009'" in sql
    # WHERE에 서버명 필터가 새면 자식 리소스 행이 탈락한다(D-096)
    where_seg = sql.split("WHERE", 1)[1].split("GROUP BY", 1)[0]
    assert "SV-WEB-001" not in where_seg


def test_server_scope_forces_identity_dimension_in_select():
    """스코프가 있으면 식별 컬럼을 SELECT에 결정적으로 포함한다(D-097 — 같은 행 식별)."""
    m = load_semantic_model("polestar")
    sql = compile_smq(
        _scoped_smq(), "polestar", m, stat_month="202606",
        server_scope=("name", ["SV-WEB-001"]),
    )
    assert 'THEN c.name END) AS "name"' in sql
    # 요청 속성도 함께 한 행에 나온다
    assert "cc.name='Vendor'" in sql
    assert "cc.name='SerialNumber'" in sql


def test_ranking_query_orders_by_measure_nulls_last():
    """최상급 질의는 measure alias로 정렬하고 NULLS LAST를 부여한다(D-098/D-099)."""
    m = load_semantic_model("polestar")
    sql = compile_smq(
        _scoped_smq(), "polestar", m,
        user_query="2026년 6월 CPU 사용률 평균이 가장 높은 서버의 제조사와 일련번호",
        stat_month="202606",
        server_scope=("name", ["SV-WEB-001", "SV-BATCH-009"]),
    )
    assert 'ORDER BY "cpus_avg" DESC NULLS LAST' in sql


def test_lowest_ranking_orders_ascending():
    """'가장 낮은' 질의는 오름차순으로 정렬한다."""
    m = load_semantic_model("polestar")
    sql = compile_smq(
        _scoped_smq(), "polestar", m,
        user_query="CPU 사용률 평균이 가장 낮은 서버",
        stat_month="202606",
    )
    assert 'ORDER BY "cpus_avg" ASC NULLS LAST' in sql


def test_no_ranking_marker_no_order_by():
    """최상급 어휘가 없으면 정렬을 추가하지 않는다(기존 동작 불변)."""
    m = load_semantic_model("polestar")
    sql = compile_smq(
        _scoped_smq(), "polestar", m,
        user_query="서버별 6월 CPU 사용률 평균",
        stat_month="202606",
    )
    assert "ORDER BY" not in sql


def test_compiled_scoped_ranking_sql_passes_polestar_validators():
    """결정적 조립 SQL이 폴스타 어댑터 검증 5종을 모두 통과한다(자기정합 회귀 가드)."""
    from src.db_adapters import get_adapter

    m = load_semantic_model("polestar")
    sql = compile_smq(
        _scoped_smq(), "polestar", m,
        user_query="2026년 6월 CPU 사용률 평균이 가장 높은 서버의 제조사와 일련번호",
        stat_month="202606",
        server_scope=("name", ["SV-WEB-001", "SV-BATCH-009"]),
    )
    adapter = get_adapter("polestar", {"polestar"})
    errors = [e for check in adapter.validator_checks() for e in check(sql)]
    assert errors == [], errors


@pytest.mark.asyncio
async def test_scope_strips_identity_filters_to_enter_coverage(monkeypatch):
    """선행 스코프가 있으면 SMQ의 서버 식별 필터를 제거해 결정적 조립이 발동한다 (D-099).

    회귀 방지(2026-07-20 라이브 실측): 오케스트레이터 sub_query에 서버 목록이 실려 LLM SMQ가
    name 필터를 방출 → 패턴 A/B 안전 필터(resource_type뿐) 밖으로 밀려 커버리지 실패 →
    결정적 조립이 발동하지 못하고 LLM 폴백으로 떨어졌다. 스코프가 더 신뢰도 높은 출처다.
    """
    from types import SimpleNamespace

    class _FakeLLM:
        async def ainvoke(self, messages):
            return SimpleNamespace(content=(
                '{"pattern": "B", "dimensions": ["Vendor", "SerialNumber"], '
                '"measures": [{"agg": "avg", "definition_name": "Utilization", '
                '"resource_type": "server.Cpus"}], '
                '"filters": [{"field": "name", "op": "in", '
                '"value": ["SV-WEB-001", "SV-BATCH-009"]}], "time_grain": "month"}'
            ))

    sql, smq, cov = await compile_from_nl(
        _FakeLLM(),
        "2026년 6월 CPU 사용률 평균이 가장 높은 서버의 제조사와 일련번호 조회",
        "polestar",
        stat_month="202606",
        server_scope=("name", ["SV-WEB-001", "SV-BATCH-009"]),
    )

    assert cov is not None and cov.covered, cov.reason if cov else "cov None"
    assert sql is not None
    assert "HAVING MAX(CASE WHEN c.resource_type='server.Server' THEN c.name END) IN " in sql
    assert 'ORDER BY "cpus_avg" DESC NULLS LAST' in sql
    # 원본 SMQ의 필터는 제거되어 커버리지 내로 들어온다
    assert smq.filters == []


@pytest.mark.asyncio
async def test_no_scope_keeps_identity_filter_outside_coverage():
    """스코프가 없으면 서버 식별 필터는 기존대로 커버리지 밖(LLM 폴백)이다."""
    from types import SimpleNamespace

    class _FakeLLM:
        async def ainvoke(self, messages):
            return SimpleNamespace(content=(
                '{"pattern": "A", "dimensions": ["Vendor"], '
                '"filters": [{"field": "name", "op": "eq", "value": "SV-WEB-001"}]}'
            ))

    sql, _smq, cov = await compile_from_nl(
        _FakeLLM(), "SV-WEB-001 서버의 제조사", "polestar",
    )
    assert sql is None
    assert cov is not None and not cov.covered


# ──────────────────────────────────────────────
# 알람 조회 컨텍스트 컬럼 + 최상급 LIMIT 1 (D-100)
# ──────────────────────────────────────────────

def test_alarm_query_includes_context_columns():
    """알람 조회(패턴 C)는 서버명만 요청해도 알람명·심각도를 결정적으로 포함한다(D-100)."""
    m = load_semantic_model("polestar")
    smq = SMQ.from_dict({
        "pattern": "C", "entities": ["CMM_RESOURCE"],
        "dimensions": ["server_name"],
        "filters": [{"field": "ALARMSEVERITY", "op": "eq", "value": 3}],
        "active_only": True,
    })
    sql = compile_smq(smq, "polestar", m)
    # 서버 식별은 부모 서버(SVR) 기준 — 시맨틱 모델 정의(구 UX 알람 뷰 개선 병합, 2026-07-22)
    assert "SVR.NAME AS server_name" in sql
    assert "D.NAME AS alarm_name" in sql
    assert "CA.ALARMSEVERITY AS severity" in sql


def test_alarm_query_no_duplicate_when_requested():
    """요청 dimension이 컨텍스트와 겹쳐도 중복 SELECT하지 않는다."""
    m = load_semantic_model("polestar")
    smq = SMQ.from_dict({
        "pattern": "C", "entities": ["CMM_RESOURCE"],
        "dimensions": ["NAME", "ALARMSEVERITY", "server_name", "IPADDRESS"],
        "active_only": True,
    })
    sql = compile_smq(smq, "polestar", m)
    assert sql.count("D.NAME AS alarm_name") == 1
    assert sql.count("CA.ALARMSEVERITY AS severity") == 1
    # 추가 요청 dimension(IP)은 뒤에 포함 — 부모 서버(SVR) 기준(모델 정의, 2026-07-22 병합)
    assert "SVR.IPADDRESS AS ipaddress" in sql


def test_ranking_query_limits_to_one():
    """최상급 순위 질의는 결정적 조립에서 상위 1건으로 제한한다(D-100)."""
    m = load_semantic_model("polestar")
    smq = _scoped_smq()
    sql = compile_smq(
        smq, "polestar", m,
        user_query="2026년 6월 CPU 사용률 평균이 가장 높은 서버의 제조사와 일련번호",
        stat_month="202606",
        server_scope=("name", ["SV-WEB-001", "SV-BATCH-009"]),
    )
    assert sql.rstrip().endswith("LIMIT 1;")


def test_non_ranking_keeps_default_limit():
    """최상급 어휘가 없으면 기본 LIMIT를 유지한다(순위 아님)."""
    m = load_semantic_model("polestar")
    smq = _scoped_smq()
    sql = compile_smq(
        smq, "polestar", m,
        user_query="서버별 6월 CPU 사용률 평균과 제조사",
        default_limit=1000, stat_month="202606",
    )
    assert "LIMIT 1000" in sql
    assert "LIMIT 1;" not in sql
