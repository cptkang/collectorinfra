"""plans/169 N1(a) 독립 검증 — 폼필 피벗 공용 전처리의 동작 불변·patch seam·공유 객체.

구현자 테스트(`test_plan169_form_fill_plan.py`)가 덮지 않는 축을 고정한다.

- patch seam(D-337 ②): 공용 모듈은 노드 네임스페이스에서 바꿔 끼우는 이름을 부르지 않는다.
  노드 모듈의 seam을 바꾸면 결과에 그대로 반영된다(단일·멀티 양쪽).
- 공유 객체(D-067 드리프트 가드 확장): 두 노드가 같은 공용 함수 객체를 쓴다.
- 특성화: 답변 오버라이드·llm_inferred·규칙이 겹치는 입력에서 state 매핑 갱신분의
  **키 순서와 값**·로그 순서가 공통화 전(3a0a1101)과 같다(차분 하네스로 실측한 값).
  W0 스냅샷(`test_plan166_w0_form_fill_pivot_inputs.py`)은 조립기 입력만 고정하고 이 축은 없다.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import src.db_adapters.polestar.assembler as assembler_mod
import src.utils.query_gen_common as qgc_mod
from src.db_adapters.polestar import form_fill_plan as ffp
from src.db_adapters.polestar.assembler import MonthSeries, resolve_form_fill_answers
from src.utils.query_gen_common import template_context_text
from tests.test_nodes.test_form_month_series import _AVG, _EAV_META, _PEAK, _cpu_form_mapping
from tests.test_nodes.test_plan166_w0_form_fill_pivot_inputs import (
    _TABLES,
    _app_config,
    _FixedDate,
)

single_mod = importlib.import_module("src.nodes.query_generator")
multi_mod = importlib.import_module("src.nodes.multi_db_executor")

_ORIGINAL = assembler_mod.build_form_fill_pivot_sql
_SCHEMA = {"tables": _TABLES, **_EAV_META}
_CPU_TITLE = "나. 주요 업무 CPU 사용현황"
_MEM_TITLE = "다. 주요 업무 주기억장치 사용현황"
_CPU_QUERY = "2026년 6월 기준 CPU 양식 채워줘"
_MEM_QUERY = "2026년 6월 기준 메모리 양식 채워줘"


def _month_fields() -> list[str]:
    out: list[str] = []
    for k in range(6):
        sub = "M" if k == 0 else f"M+{k}"
        out += [f"{_AVG}|{sub}", f"{_PEAK}|{sub}"]
    return out


def _memory_mapping_inferred() -> dict[str, Any]:
    """처리능력(GB)에 llm_inferred 매핑이 걸려 규칙(용량)과 겹치는 메모리 양식."""
    mapping = _cpu_form_mapping()
    del mapping["처리능력|(TPMC)"]
    mapping["처리능력|(GB)"] = "EAV:Whatever"
    return mapping


_E1_ANSWERS = {
    "제조사(모델명)": {"action": "literal", "value": "HP"},
    "비고": {"action": "blank", "value": None},
    "구분|분류": {"action": "column", "value": "hostname"},
    f"{_AVG}|M": {"action": "blank", "value": None},       # 월 시리즈 보호 → 거부
    "없는필드": {"action": "column", "value": "no_such"},  # 존재성 → 거부
}


@pytest.fixture
def calls(monkeypatch) -> list[dict[str, Any]]:
    """두 노드의 조립기 호출 인자를 기록하고 날짜를 고정한다."""
    recorded: list[dict[str, Any]] = []
    sig = inspect.signature(_ORIGINAL)

    def _record(*args: Any, **kwargs: Any) -> str:
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        recorded.append(dict(bound.arguments))
        return _ORIGINAL(*args, **kwargs)

    for mod in (single_mod, multi_mod):
        monkeypatch.setattr(mod, "build_form_fill_pivot_sql", _record)
    monkeypatch.setattr(assembler_mod, "date", _FixedDate)
    monkeypatch.setattr(qgc_mod, "date", _FixedDate)
    return recorded


def _run_single(mapping, sources, query, title, *, answers=None, db_id=""):
    state: dict[str, Any] = {
        "column_mapping": dict(mapping),
        "mapping_sources": dict(sources),
        "schema_info": _SCHEMA,
        "template_structure": {"sheets": [{"title_text": title, "name": "Sheet1"}]},
        "active_db_engine": "postgresql",
        "active_db_id": db_id,
    }
    if answers:
        state["form_fill_answers"] = answers
    return single_mod._try_build_form_fill_pivot_sql(state, 100_000, query, query_time=None)


def _run_multi(mapping, sources, query, title, *, answers=None, db_id=""):
    ffo: dict[str, Any] = {}
    template = {"sheets": [{"title_text": title, "name": "Sheet1"}]}
    parts, sql, unmapped = multi_mod._build_mapping_user_parts(
        {f: c for f, c in mapping.items() if c is not None},
        _SCHEMA,
        [f for f, c in mapping.items() if c is None],
        db_engine="postgresql", db_id=db_id, default_limit=100_000,
        parsed_requirements={"original_query": query},
        error_context=None,
        app_config=_app_config(set()),
        form_context_text=template_context_text(template),
        form_fill_out=ffo, form_intent=True,
        mapping_sources=dict(sources),
        form_fill_answers=answers,
        query_time=None,
    )
    return ffo, sql


# ── patch seam(D-337 ②) ──────────────────────────────────────────────


_SEAM_NAMES = {
    "build_form_fill_pivot_sql",
    "recognize_month_series",
    "get_domain_by_id",
    "resolve_stat_month_range",
    "is_scrub_samples_enabled",
}


def test_common_module_does_not_reference_seams_or_log():
    """공용 모듈은 노드 seam 이름을 참조하지 않고 로거를 갖지 않는다(로거 이름 불변)."""
    tree = ast.parse(Path(ffp.__file__).read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    imported = {
        a.asname or a.name
        for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in n.names
    }
    assert not (_SEAM_NAMES & (names | imported))
    assert "logging" not in imported and "logger" not in names


def test_single_seams_still_drive_result(calls, monkeypatch):
    """단일 노드 네임스페이스의 기간 해석·도메인 조회 seam이 조립기 인자로 이어진다."""
    monkeypatch.setattr(
        single_mod, "resolve_stat_month_range", lambda *a, **k: ("209901", "209902"),
    )
    monkeypatch.setattr(
        single_mod, "get_domain_by_id", lambda _id: SimpleNamespace(db_schema="SEAMSCHEMA"),
    )
    res = _run_single(_cpu_form_mapping(), {}, _CPU_QUERY, _CPU_TITLE)
    assert res is not None
    assert calls[-1]["stat_month"] == ("209901", "209902")
    assert calls[-1]["db_schema"] == "SEAMSCHEMA"


def test_single_month_series_seam(calls, monkeypatch):
    """`recognize_month_series` seam을 None으로 바꾸면 월 시리즈 산출(앵커·측정)이 사라진다."""
    monkeypatch.setattr(single_mod, "recognize_month_series", lambda *a, **k: None)
    res = _run_single(_cpu_form_mapping(), {}, _CPU_QUERY, _CPU_TITLE)
    assert res is not None and res["month_anchor"] is None
    assert calls[-1]["month_measures"] is None


def test_multi_seams_still_drive_result(calls, monkeypatch):
    monkeypatch.setattr(
        multi_mod, "resolve_stat_month_range", lambda *a, **k: ("209901", "209902"),
    )
    monkeypatch.setattr(
        multi_mod, "get_domain_by_id", lambda _id: SimpleNamespace(db_schema="SEAMSCHEMA"),
    )
    _ffo, sql = _run_multi(_cpu_form_mapping(), {}, _CPU_QUERY, _CPU_TITLE)
    assert sql
    assert calls[-1]["stat_month"] == ("209901", "209902")
    assert calls[-1]["db_schema"] == "SEAMSCHEMA"

    monkeypatch.setattr(multi_mod, "recognize_month_series", lambda *a, **k: None)
    ffo, _sql = _run_multi(_cpu_form_mapping(), {}, _CPU_QUERY, _CPU_TITLE)
    assert "month_anchor" not in ffo
    assert calls[-1]["month_measures"] is None


# ── 공유 객체(D-067 드리프트 가드 확장) ───────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "drop_llm_inferred_mappings",
        "month_series_rule_updates",
        "resolve_answer_overrides",
        "split_pivot_mapping",
        "plan_form_fill_pivot",
    ],
)
def test_both_nodes_share_common_function_objects(name):
    assert getattr(single_mod, name) is getattr(ffp, name)
    assert getattr(multi_mod, name) is getattr(ffp, name)


# ── 순수 함수: 답변 오버라이드의 결합 필터 ───────────────────────────


def test_resolve_answer_overrides_matches_inline_concat_filter():
    """종전 인라인(보호 필드 = 월 필드 · 결합에서 매핑·상수 덮인 필드 제외)과 같다."""
    eav = _EAV_META["_structure_meta"]["patterns"][0]
    ms = MonthSeries(measures=[], fields=[f"{_AVG}|M"], resource_type="server.Cpus")
    concat = [("제조사(모델명)", "Vendor", "Model"), ("제조사2", "Vendor", "Model")]
    answers = {
        "제조사(모델명)": {"action": "literal", "value": "HP"},
        "제조사2": {"action": "column", "value": "no_such"},  # 거부 → 결합 유지
        f"{_AVG}|M": {"action": "blank", "value": None},       # 보호 → 거부
        "구분": {"action": "eav", "value": "Vendor"},
    }
    got = ffp.resolve_answer_overrides(answers, _SCHEMA, eav, ms, concat)

    ov, ov_map, lit = resolve_form_fill_answers(
        answers, _SCHEMA, eav, protected_fields={f"{_AVG}|M"},
    )
    fields = set(ov_map) | set(lit)
    assert got == (ov, ov_map, lit, [c for c in concat if c[0] not in fields])
    assert got[3] == [("제조사2", "Vendor", "Model")]
    assert got[0][f"{_AVG}|M"]["applied"] is False
    # 월 시리즈 없음 → 보호 필드 없음(같은 답변이 적용된다)
    no_ms = ffp.resolve_answer_overrides(answers, _SCHEMA, eav, None, concat)
    assert no_ms[0][f"{_AVG}|M"]["applied"] is True


# ── 특성화: state 매핑 갱신분 순서·값 + 로그 순서 (3a0a1101 실측값) ──


def _expected_e3_updates() -> list[tuple[str, Any]]:
    # llm_inferred 강등(None)이 먼저 자리를 잡고, 용량 규칙이 같은 키의 값을 덮는다(dict 순서 유지)
    return (
        [("처리능력|(GB)", "EAV:TotalSize")]
        + [(f, None) for f in _month_fields()]
        + [("비고", None), ("제조사(모델명)", None)]
    )


def _expected_e1_updates() -> list[tuple[str, Any]]:
    return (
        [("호스트명", None), ("처리능력|(TPMC)", None)]
        + [(f, None) for f in _month_fields()]
        + [("비고", None), ("제조사(모델명)", None), ("구분|분류", "cmm_resource.hostname")]
    )


def test_single_inferred_overlaps_capacity_updates_order(calls, caplog):
    sources = {"처리능력|(GB)": "llm_inferred"}
    with caplog.at_level(logging.INFO, logger="src.nodes.query_generator"):
        res = _run_single(_memory_mapping_inferred(), sources, _MEM_QUERY, _MEM_TITLE)
    assert res is not None
    assert list(res["mapping_updates"].items()) == _expected_e3_updates()
    msgs = [
        r.getMessage() for r in caplog.records if r.name == "src.nodes.query_generator"
    ]
    assert msgs[0] == "폼필 llm_inferred 매핑 1건 채움 제외(D-149, 역질문 후보): ['처리능력|(GB)']"
    assert msgs[1].startswith("폼필 월 시리즈 인식(D-146): rt=server.Memory,")
    assert len(msgs) == 2


def test_multi_inferred_overlaps_capacity_updates_order(calls, caplog):
    sources = {"처리능력|(GB)": "llm_inferred"}
    with caplog.at_level(logging.INFO, logger="src.nodes.multi_db_executor"):
        ffo, sql = _run_multi(_memory_mapping_inferred(), sources, _MEM_QUERY, _MEM_TITLE)
    assert sql
    assert list(ffo["mapping_updates"].items()) == _expected_e3_updates()
    msgs = [
        r.getMessage() for r in caplog.records if r.name == "src.nodes.multi_db_executor"
    ]
    assert msgs[0] == (
        "DB '' 폼필 llm_inferred 매핑 1건 채움 제외(D-149, 역질문 후보): ['처리능력|(GB)']"
    )
    assert msgs[1].startswith("DB '' 폼필 월 시리즈 인식(D-146): rt=server.Memory,")
    assert msgs[2].startswith("DB '': 폼필 다중 리소스 피벗 SQL 결정적 조립(LLM 우회)")
    assert msgs[2].endswith(
        "regular=[('호스트명', 'cmm_resource.hostname'), ('비고', 'cmm_resource.name')], "
        "concat=['제조사(모델명)']"
    )
    assert len(msgs) == 3


def test_single_answers_override_concat_updates_and_logs(calls, caplog):
    sources = {"호스트명": "llm_inferred"}
    with caplog.at_level(logging.INFO, logger="src.nodes.query_generator"):
        res = _run_single(
            _cpu_form_mapping(), sources, _CPU_QUERY, _CPU_TITLE, answers=_E1_ANSWERS,
        )
    assert res is not None
    assert list(res["mapping_updates"].items()) == _expected_e1_updates()
    assert res["literals"] == {"제조사(모델명)": "HP"}
    # 결합 필드가 상수로 덮여 조립기 결합 인자에서 빠진다
    assert calls[-1]["concat_eav"] is None
    msgs = [
        r.getMessage() for r in caplog.records if r.name == "src.nodes.query_generator"
    ]
    assert [m.split(":")[0] for m in msgs] == [
        "폼필 llm_inferred 매핑 1건 채움 제외(D-149, 역질문 후보)",
        "폼필 답변 오버라이드 적용(D-151)",
        "폼필 답변 오버라이드 거부(D-151)",
        "폼필 월 시리즈 인식(D-146)",
    ]


def test_multi_answers_override_concat_updates(calls):
    sources = {"호스트명": "llm_inferred"}
    ffo, sql = _run_multi(
        _cpu_form_mapping(), sources, _CPU_QUERY, _CPU_TITLE, answers=_E1_ANSWERS,
    )
    assert sql
    assert list(ffo["mapping_updates"].items()) == _expected_e1_updates()
    assert ffo["literals"] == {"제조사(모델명)": "HP"}
    assert calls[-1]["concat_eav"] is None


# ── 순수 함수: 변이 검출로 드러난 빈칸 ─────────────────────────────────


def test_split_pivot_mapping_metric_check_is_case_insensitive():
    """사용률 통계 테이블 판정은 대소문자 무관(종전 인라인 `.lower()` — DB2 대문자 표기)."""
    mapping = {
        "서버": "CMM_RESOURCE.NAME",
        "메모리 사용률": "CMM_METRIC_STAT_M.mem_avg",
        "CPU 사용률": "Cmm_Metric_Stat_H.cpu_avg_val",
    }
    regular, eav, metric = ffp.split_pivot_mapping(mapping)
    assert regular == [("서버", "CMM_RESOURCE.NAME")]
    assert eav == []
    assert metric == [
        ("메모리 사용률", "CMM_METRIC_STAT_M.mem_avg"),
        ("CPU 사용률", "Cmm_Metric_Stat_H.cpu_avg_val"),
    ]


def test_plan_form_fill_pivot_no_identity_injection_when_concat_present():
    """결합(Vendor+Model) 필드가 있으면 서버 식별 컬럼을 주입하지 않는다(종전 조건 4항)."""
    schema = {"tables": {"cmm_resource": {"columns": [{"name": "name"}, {"name": "hostname"}]}}}
    concat = [("제조사(모델명)", "Vendor", "Model")]
    plan = ffp.plan_form_fill_pivot(
        [], [], [], {"entity_table": "cmm_resource"},
        schema_info=schema, concat_eav=concat, month_series=None, form_intent=True,
        query_time=None,
    )
    assert plan.regular_entries == [] and plan.concat_eav == concat
    # server_eav가 있어도 주입하지 않는다 / 게이트(월·폼필) 둘 다 없으면 주입하지 않는다
    for server_eav, form_intent in (([("OS", "OSVerson")], True), ([], False)):
        p = ffp.plan_form_fill_pivot(
            [], server_eav, [], {"entity_table": "cmm_resource"},
            schema_info=schema, concat_eav=[], month_series=None, form_intent=form_intent,
            query_time=None,
        )
        assert p.regular_entries == [] and p.concat_eav is None
    # entity_table 미지정 → 기본 엔티티 테이블로 주입
    p = ffp.plan_form_fill_pivot(
        [], [], [], {}, schema_info=schema, concat_eav=[], month_series=None,
        form_intent=True, query_time=None,
    )
    assert p.regular_entries == [
        ("server_name", "cmm_resource.name"), ("hostname", "cmm_resource.hostname"),
    ]
