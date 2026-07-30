"""계층 taxonomy 상위어 모호성 처리 검증 (Plan 67 N4 / D-133).

폐쇄망 CI 전제 — 실 DB·LLM 없이 통과한다(SMQ 정규화·컴파일은 결정적).

검증 축:
    1. 상위어 단독 질의 → 하위 전부 제시(전체 제시), 가드 계측 발동.
    2. **하위어 명시 질의는 동작 불변**(골든 문장 실측) — N4의 절대 조건.
    3. 플래그 OFF면 SMQ·SQL 바이트 무변경(옵트인 증분).
    4. 기존 교정 가드가 최종 중재자(확장을 먼저 돌리는 순서의 근거).
    5. alias 해소가 parent를 소비한다(형제 다수가 주장하는 상위어 이름은 미등록).
    6. 확장이 커버리지를 깨지 않는다(전역 집계·기간별 분해 형태에서 dimension 미확장).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.nodes.semantic_compiler import (
    SMQ,
    GUARD_HYPERNYM_EXPAND,
    GUARD_PHYSICALCORE_DROP,
    _dimension_index,
    _resolve_dim,
    check_coverage,
    compile_from_nl,
    compile_smq,
    guard_counters,
    load_semantic_model,
    normalize_smq,
    reset_guard_counters,
)

_GP = "polestar_cm_gp"


@pytest.fixture
def model() -> dict:
    return load_semantic_model(_GP, use_cache=False)


def _cfg(*, hypernym: bool) -> SimpleNamespace:
    """검증 대상 플래그만 명시한 설정 대역(.env 누수 차단)."""
    return SimpleNamespace(
        text2sql=SimpleNamespace(
            stepwise_derivation=False, hypernym_ambiguity=hypernym,
        )
    )


def _norm(raw: dict, query: str, model: dict, *, hypernym: bool) -> SMQ:
    return normalize_smq(
        SMQ.from_dict(raw), query, model, hypernym_ambiguity=hypernym,
    )


def _measure_keys(smq: SMQ) -> list[tuple[str, str]]:
    return [(m.resource_type, m.agg) for m in smq.measures]


# 1방 선택이 상위어를 임의의 한 갈래로 좁힌 SMQ(실측 실패 형태의 재현).
_ONE_CHILD_MEASURE = {
    "pattern": "B", "dimensions": ["name"], "time_grain": "month",
    "measures": [
        {"agg": "avg", "definition_name": "Utilization", "resource_type": "server.Cpus"}
    ],
}


# ──────────────────────────────────────────────
# 1. 상위어 단독 질의 → 전체 제시
# ──────────────────────────────────────────────

class TestHypernymOnlyQuery:
    def test_usage_hypernym_expands_to_all_children(self, model):
        reset_guard_counters()
        smq = _norm(_ONE_CHILD_MEASURE, "전체 서버 사용률 보여줘", model, hypernym=True)
        assert _measure_keys(smq) == [
            ("server.Cpus", "avg"), ("server.Memory", "avg"),
            ("server.FileSystems", "avg"),
        ]
        assert guard_counters().get(GUARD_HYPERNYM_EXPAND) == 1

    def test_expansion_keeps_selected_aggregations(self, model):
        """형제는 선택된 집계(평균·최대)를 그대로 물려받는다 — 뜻이 다른 컬럼 혼입 방지."""
        raw = dict(_ONE_CHILD_MEASURE, measures=[
            {"agg": "avg", "definition_name": "Utilization", "resource_type": "server.Cpus"},
            {"agg": "max", "definition_name": "Utilization", "resource_type": "server.Cpus"},
        ])
        smq = _norm(raw, "사용률 리스트 조회", model, hypernym=True)
        assert _measure_keys(smq) == [
            ("server.Cpus", "avg"), ("server.Cpus", "max"),
            ("server.Memory", "avg"), ("server.Memory", "max"),
            ("server.FileSystems", "avg"), ("server.FileSystems", "max"),
        ]

    def test_hypernym_alias_surface_also_triggers(self, model):
        """상위어 표면 변형(taxonomy aliases)도 같은 판정을 받는다."""
        smq = _norm(_ONE_CHILD_MEASURE, "이용률 알려줘", model, hypernym=True)
        assert len(smq.measures) == 3

    def test_dimension_hypernym_expands_siblings(self, model):
        smq = _norm(
            {"pattern": "A", "dimensions": ["name", "Model"]},
            "서버별 모델 알려줘", model, hypernym=True,
        )
        assert smq.dimensions == ["name", "Model", "MODEL"]

    def test_expanded_smq_stays_inside_coverage_and_compiles(self, model):
        smq = _norm(_ONE_CHILD_MEASURE, "사용률 보여줘", model, hypernym=True)
        cov = check_coverage(smq, model)
        assert cov.covered, cov.reason
        sql = compile_smq(smq, _GP, model)
        for alias in ("cpus_avg", "memory_avg", "filesystems_avg"):
            assert alias in sql

    def test_no_child_selected_means_no_expansion(self, model):
        """상위어를 언급했지만 SMQ가 하위를 하나도 고르지 않았으면 손대지 않는다."""
        smq = _norm(
            {"pattern": "A", "dimensions": ["name", "OSType"]},
            "사용률 관련 서버 OS 조회", model, hypernym=True,
        )
        assert smq.dimensions == ["name", "OSType"] and smq.measures == []

    def test_parentless_measure_is_untouched(self, model):
        """무부모 measure(디스크 IO)는 상위어 계층 밖 — 확장 대상이 아니다."""
        raw = dict(_ONE_CHILD_MEASURE, measures=[
            {"agg": "max", "definition_name": "MaxIORate", "resource_type": "server.Disks"}
        ])
        smq = _norm(raw, "디스크 IO 알려줘", model, hypernym=True)
        assert _measure_keys(smq) == [("server.Disks", "max")]


# ──────────────────────────────────────────────
# 2. 하위어 명시 질의는 동작 불변 (절대 조건)
# ──────────────────────────────────────────────

# 골드셋 실측 문장 — 상위어 어휘("사용률"·"코어"·"모델")를 품고 있지만 하위어를 명시한다.
_CHILD_EXPLICIT_CASES = [
    (
        "gp-009/b0-004",
        "전체 서버들의 CPU, 메모리 용량 및 사용률(평균·최대) 리스트를 조회해줘. "
        "사용률은 지난달 1개월 통계 기준으로.",
        {
            "pattern": "B", "dimensions": ["name"], "time_grain": "month",
            "measures": [
                {"agg": "avg", "definition_name": "Utilization",
                 "resource_type": "server.Cpus"},
                {"agg": "max", "definition_name": "Utilization",
                 "resource_type": "server.Cpus"},
                {"agg": "avg", "definition_name": "Utilization",
                 "resource_type": "server.Memory"},
                {"agg": "max", "definition_name": "Utilization",
                 "resource_type": "server.Memory"},
            ],
        },
    ),
    (
        "gp-004",
        "전체 서버별 호스트명, IP, 시리얼번호, CPU모델, CPU코어수, 메모리를 조회해줘",
        {"pattern": "A", "dimensions": [
            "hostname", "ipaddress", "SerialNumber", "MODEL", "LOGICALCORE", "TotalSize"]},
    ),
    (
        "gp-013",
        "김포 폴스타 전체 서버를 통틀은 지난달 평균 CPU 사용률을 단일 값 하나로 알려줘",
        {
            "pattern": "B", "dimensions": [], "global_aggregate": True,
            "time_grain": "month",
            "measures": [{"agg": "avg", "definition_name": "Utilization",
                          "resource_type": "server.Cpus"}],
        },
    ),
    (
        "gp-010",
        "지난 3개월간 전체 서버별 월간 CPU, 메모리, 파일시스템, 디스크 IO 성능 통계를 조회해줘",
        {
            "pattern": "B", "dimensions": ["name"], "time_grain": "month",
            "measures": [
                {"agg": "avg", "definition_name": "Utilization",
                 "resource_type": "server.Cpus"},
                {"agg": "avg", "definition_name": "Utilization",
                 "resource_type": "server.Memory"},
                {"agg": "avg", "definition_name": "Utilization",
                 "resource_type": "server.FileSystems"},
                {"agg": "max", "definition_name": "MaxIORate",
                 "resource_type": "server.Disks"},
            ],
        },
    ),
]


@pytest.mark.parametrize(
    "label,query,raw", _CHILD_EXPLICIT_CASES, ids=[c[0] for c in _CHILD_EXPLICIT_CASES]
)
def test_child_explicit_query_is_byte_identical(label, query, raw, model):
    """하위어를 명시한 질의는 플래그 ON에서도 SMQ·SQL이 바이트 동일하다."""
    reset_guard_counters()
    off = _norm(raw, query, model, hypernym=False)
    on = _norm(raw, query, model, hypernym=True)
    assert on.to_match_dict() == off.to_match_dict()
    assert compile_smq(on, _GP, model, user_query=query) == \
        compile_smq(off, _GP, model, user_query=query)
    assert guard_counters().get(GUARD_HYPERNYM_EXPAND) is None


# ──────────────────────────────────────────────
# 3. 플래그 OFF = 무변경 / taxonomy 없는 모델도 무변경
# ──────────────────────────────────────────────

def test_flag_off_leaves_hypernym_query_untouched(model):
    reset_guard_counters()
    smq = _norm(_ONE_CHILD_MEASURE, "사용률 보여줘", model, hypernym=False)
    assert _measure_keys(smq) == [("server.Cpus", "avg")]
    assert guard_counters() == {}


def test_model_without_taxonomy_is_inert(model):
    """taxonomy 선언이 없는 모델(동결 사본 폴백)에서는 ON이어도 확장이 없다."""
    stripped = {k: v for k, v in model.items() if k != "taxonomy"}
    smq = _norm(_ONE_CHILD_MEASURE, "사용률 보여줘", stripped, hypernym=True)
    assert _measure_keys(smq) == [("server.Cpus", "avg")]


@pytest.mark.asyncio
async def test_compile_from_nl_wires_flag(model):
    """compile_from_nl이 설정 플래그를 그대로 배선한다(경로 대칭 — 진입점 단일)."""
    class _MockLLM:
        async def ainvoke(self, messages):
            return SimpleNamespace(content=(
                '{"pattern": "B", "dimensions": ["name"], "time_grain": "month", '
                '"measures": [{"agg": "avg", "definition_name": "Utilization", '
                '"resource_type": "server.Cpus"}]}'
            ))

    query = "전체 서버 사용률 보여줘"
    sql_off, smq_off, _ = await compile_from_nl(
        _MockLLM(), query, _GP, app_config=_cfg(hypernym=False))
    sql_on, smq_on, _ = await compile_from_nl(
        _MockLLM(), query, _GP, app_config=_cfg(hypernym=True))
    assert len(smq_off.measures) == 1 and len(smq_on.measures) == 3
    assert "memory_avg" not in sql_off and "memory_avg" in sql_on


@pytest.mark.asyncio
async def test_missing_app_config_means_off(model):
    """설정 미주입 경로는 OFF로 본다(테스트가 환경에 좌우되지 않게)."""
    class _MockLLM:
        async def ainvoke(self, messages):
            return SimpleNamespace(content=(
                '{"pattern": "B", "dimensions": ["name"], "time_grain": "month", '
                '"measures": [{"agg": "avg", "definition_name": "Utilization", '
                '"resource_type": "server.Cpus"}]}'
            ))

    _sql, smq, _cov = await compile_from_nl(_MockLLM(), "사용률 보여줘", _GP)
    assert len(smq.measures) == 1


# ──────────────────────────────────────────────
# 4. 기존 교정 가드가 최종 중재자
# ──────────────────────────────────────────────

def test_existing_guard_arbitrates_core_expansion(model):
    """"코어" 확장 후 '물리' 신호가 없으면 기존 가드가 PHYSICALCORE를 다시 뺀다.

    확장을 가드보다 먼저 돌리는 순서의 근거 — 실측 운영 관행(VM 위주 = 논리코어,
    D-076 후속)이 상위어 확장보다 우선한다.
    """
    reset_guard_counters()
    smq = _norm(
        {"pattern": "A", "dimensions": ["name", "LOGICALCORE"]},
        "서버별 코어 알려줘", model, hypernym=True,
    )
    assert smq.dimensions == ["name", "LOGICALCORE"]
    counters = guard_counters()
    assert counters.get(GUARD_HYPERNYM_EXPAND) == 1
    assert counters.get(GUARD_PHYSICALCORE_DROP) == 1


def test_physical_signal_keeps_child_selection(model):
    """'물리 코어'는 하위어 명시이므로 확장·교정 모두 발동하지 않는다."""
    reset_guard_counters()
    smq = _norm(
        {"pattern": "A", "dimensions": ["name", "PHYSICALCORE"]},
        "서버별 물리 코어 수 알려줘", model, hypernym=True,
    )
    assert smq.dimensions == ["name", "PHYSICALCORE"]
    assert guard_counters() == {}


# ──────────────────────────────────────────────
# 5. alias 해소의 parent 소비
# ──────────────────────────────────────────────

class TestAliasResolution:
    def test_contested_parent_alias_is_not_registered(self):
        """형제 둘이 상위어 이름을 별칭으로 주장하면 어느 하나로도 확정하지 않는다."""
        pattern_a = {"dimensions": [
            {"name": "MemSize", "parent": "메모리", "aliases": ["메모리", "메모리크기"]},
            {"name": "MemUsed", "parent": "메모리", "aliases": ["메모리", "사용메모리"]},
        ]}
        index = _dimension_index(pattern_a)
        assert _resolve_dim("메모리", index) is None
        assert _resolve_dim("메모리크기", index)["name"] == "MemSize"
        assert _resolve_dim("사용메모리", index)["name"] == "MemUsed"

    def test_single_claim_keeps_curated_binding(self):
        """주장하는 형제가 하나뿐이면 큐레이션 결속을 유지한다(해제는 alias_deny)."""
        pattern_a = {"dimensions": [
            {"name": "MemSize", "parent": "메모리", "aliases": ["메모리"]},
            {"name": "MemUsed", "parent": "메모리", "aliases": ["사용메모리"]},
        ]}
        assert _resolve_dim("메모리", _dimension_index(pattern_a))["name"] == "MemSize"

    def test_real_catalog_alias_bindings_unchanged(self, model):
        """실 카탈로그에서는 이 규칙의 발동이 0건이다(기본 동작 무변경 실증)."""
        index = _dimension_index(model["pattern_a"])
        assert _resolve_dim("모델", index)["name"] == "Model"
        assert _resolve_dim("논리코어", index)["name"] == "LOGICALCORE"
        assert _resolve_dim("물리코어", index)["name"] == "PHYSICALCORE"


# ──────────────────────────────────────────────
# 6. 확장이 커버리지를 깨지 않는다
# ──────────────────────────────────────────────

def test_global_aggregate_expands_measures_only(model):
    """전역 집계는 dimension을 가질 수 없으므로 measure만 채운다."""
    raw = {
        "pattern": "B", "dimensions": [], "global_aggregate": True, "time_grain": "month",
        "measures": [{"agg": "avg", "definition_name": "Utilization",
                      "resource_type": "server.Cpus"}],
    }
    smq = _norm(raw, "전체를 통틀은 사용률 단일 값", model, hypernym=True)
    assert smq.dimensions == [] and len(smq.measures) == 3
    assert check_coverage(smq, model).covered


def test_time_breakdown_does_not_gain_eav_dimension(model):
    """기간별 분해는 EAV 속성 dimension을 못 쓴다 — 채우면 질의 전체가 폴백된다."""
    raw = {
        "pattern": "A", "dimensions": ["name", "Model"], "time_breakdown": True,
        "time_grain": "month",
        "measures": [{"agg": "avg", "definition_name": "Utilization",
                      "resource_type": "server.Cpus"}],
    }
    smq = _norm(raw, "월별 모델 추이", model, hypernym=True)
    assert smq.dimensions == ["name", "Model"]


def test_lob_child_is_never_filled(model):
    """LOB 하위(컴파일 불가)는 확장 대상에서 빠진다."""
    pattern_a = {"dimensions": [
        {"name": "A", "parent": "묶음", "source": "eav", "attribute": "A", "aliases": []},
        {"name": "B", "parent": "묶음", "source": "eav", "attribute": "B",
         "aliases": [], "lob": True},
    ]}
    stub = {
        "pattern_a": pattern_a,
        "taxonomy": {"묶음": {"dimensions": ["A", "B"]}},
    }
    smq = _norm({"pattern": "A", "dimensions": ["A"]}, "묶음 조회", stub, hypernym=True)
    assert smq.dimensions == ["A"]


def test_alarm_pattern_is_out_of_scope(model):
    """패턴 C(알람)는 taxonomy 대상이 아니다(상위어 선언이 A/B 항목뿐)."""
    smq = _norm(
        {"pattern": "C", "dimensions": ["server_name"], "active_only": True},
        "사용률 관련 알람 목록", model, hypernym=True,
    )
    assert smq.dimensions == ["server_name"]
