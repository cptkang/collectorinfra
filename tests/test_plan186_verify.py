"""plans/186 독립 검증(verifier) — M7 · K5 · J4 · P3 · J7 적대 입력·경계 단언.

구현자 테스트(`test_plan186_k5_j4_p3.py` · `test_nodes/test_plan186_multi_regen_metric.py`)가
덮지 않은 경계를 본다. LLM·DB 0.

  M7  `safe_stop_detail_text`는 어떤 입력에도 고정 어휘(고정 사유·머리말) 밖 문자열을 내지 않는다
      (요약 꼴 입력은 `db_error_text` 계약을 따른다). kind 판정은 머리말과 무관하다.
  K5  호출부가 빈 `dependency_notes`를 넘겨도 그 값을 존중한다.
  J4  운영 단일 설정(넘긴 설정 = 전역 설정)에서 프롬프트 렌더가 넘김·미지정 양쪽 같다.
  P3  mtime 같고 크기만 다름 · 크기 같고 mtime만 다름 → 다시 읽음 · 실패는 캐시에 안 남음 ·
      비-dict 결과 뒤 고친 파일을 바로 읽음.
  J7  DB2 어댑터 케이스도 단일과 같은 회수 대상 · 적용된 답변 오버라이드는 회수에서 빠짐.
"""

from __future__ import annotations

import importlib
import os
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from src.config import AppConfig, MultiDBConfig, RouterConfig
from src.domain import disclosure as disc
from src.routing.domain_config import DB_DOMAINS
from src.utils import user_error as ue
from src.utils.user_error import safe_stop_detail_text, with_validation_head

agg_mod = importlib.import_module("src.orchestration.result_aggregator")
rp = importlib.import_module("src.orchestration.replanner")
sr = importlib.import_module("src.routing.semantic_router")
sa = importlib.import_module("src.nodes.schema_analyzer")
w0 = importlib.import_module("tests.test_nodes.test_plan166_w0_form_fill_pivot_inputs")
from tests.test_nodes.test_form_month_series import _EAV_META  # noqa: E402
from tests.test_nodes.test_plan166_w0_form_fill_pivot_inputs import (  # noqa: E402,F401 — 픽스처
    _TABLES,
    recorder,
)
from tests.test_nodes.test_plan185_single_metric_recovery import _CPU, _MEM, _case  # noqa: E402

# ── M7 ──────────────────────────────────────────────────────────────────────

#: 화면에 보일 수 있는 어휘 — 고정 사유 + 고정 머리말
_VOCAB = set(ue._FIXED_REASONS) | set(ue._PREFIX_REASONS)

_SECRETS = ("SELECT", "password", "hunter2", "postgres://", "10.0.0.1", "secret", "Traceback")


@pytest.mark.parametrize("raw", [
    # 예외 클래스명 접두가 db_id처럼 보이는 경우
    "ValueError: SQL 검증 실패: SELECT pw FROM users",
    # 검증 사유 안에 「 / x: 」가 든 경우 — 머리말만 늘 뿐 원문은 안 나간다
    "pg: SQL 검증 실패: col 'a / b: SQL 실행 에러: password=hunter2' 없음",
    "pg: SQL 검증 실패: x / evil: 모든 DB 쿼리가 실패했습니다.",
    "pg: SQL 검증 실패 secret",
    "pg: SQL 검증 실패:secret SELECT 1",
    "a.b-c: DB 연결 에러: host=10.0.0.1 password=hunter2",
    "pg: SQL 검증 실패: a\n / gp: postgres://u:p@h/db",
    "Traceback (most recent call last): / File: x / pg: SQL 실행 에러: secret",
    "pg:  SQL 실행 에러: postgres://u:p@h",
    "pg: 모든 DB 쿼리가 실패했습니다. secret",
    {"pg": "SQL 검증 실패: SELECT secret"},
    "pg: 모든 DB 쿼리가 실패했습니다:\n[A] DB 'a' 실행 에러: postgres://u:p@h",
])
def test_stop_detail_never_leaks_raw(raw: Any) -> None:
    got = safe_stop_detail_text(raw)
    if got is None:
        return
    for secret in _SECRETS:
        assert secret not in got, (raw, got)
    if not got.startswith(("모든 DB 쿼리가 실패했습니다:", "일부 DB 쿼리가 실패했습니다")):
        assert set(got.split(" · ")) <= _VOCAB, (raw, got)


def test_stop_detail_spurious_head_is_vocabulary_only() -> None:
    """검증 사유에 박힌 「 / x: 머리말」은 머리말 하나를 더 보일 뿐(정보 — 원문 미노출)."""
    got = safe_stop_detail_text("pg: SQL 검증 실패: a / b: SQL 실행 에러: z")
    assert got == "SQL 검증 실패 · SQL 실행 에러"


@pytest.mark.parametrize("reason", [
    "SQL 검증 실패", "SQL 검증 실패: x", "SQL 검증 실패:x",
])
def test_with_validation_head_idempotent(reason: str) -> None:
    assert with_validation_head(reason) == reason
    assert with_validation_head(with_validation_head("y")) == "SQL 검증 실패: y"


_TASK = {"task_id": "t1", "sub_query": "서버 목록"}


@pytest.mark.parametrize("stop,kind", [
    # 읽기 전용 가드 표지는 머리말 뒤에 있어도 검색으로 잡힌다
    ({"reason": "validation_budget",
      "detail": "SQL 검증 실패: 금지된 키워드가 포함되어 있습니다: DELETE"}, disc.SQL_BLOCKED),
    ({"reason": "validation_budget",
      "detail": "gp: SQL 검증 실패: 감지된 타입: DROP"}, disc.SQL_BLOCKED),
    ({"reason": "non_sql", "detail": "SQL 검증 실패: SELECT 문만 허용됩니다"}, disc.FAIL_NON_SQL),
    ({"reason": "deadline", "detail": "gp: SQL 실행 에러: boom"}, disc.FAIL_DEADLINE),
    ({"reason": "backend_limit", "detail": "SQL 검증 실패: 토큰 초과"}, disc.QUERY_FAILED),
])
def test_disclosure_kind_independent_of_head(stop: dict[str, str], kind: str) -> None:
    error = "모든 DB 쿼리가 실패했습니다."
    d = agg_mod._failure_disclosure(_TASK, {"error": error, "regen_stop": stop})
    assert d["kind"] == kind
    # 머리말을 뗀 원문으로도 kind가 같다(판정이 머리말에 기대지 않는다)
    bare = {**stop, "detail": stop["detail"].split("SQL 검증 실패: ", 1)[-1]}
    d2 = agg_mod._failure_disclosure(_TASK, {"error": error, "regen_stop": bare})
    assert d2["kind"] == kind
    for secret in ("DELETE", "DROP", "boom", "토큰 초과", "SELECT 문만"):
        assert secret not in d["text"]


# ── K5 ──────────────────────────────────────────────────────────────────────


def test_k5_explicit_empty_dependency_notes_respected(mock_config) -> None:
    ctx = rp._ReplanCtx(
        state={"dependency_notes": []}, app_config=mock_config, replan_count=0,
        replan_history=[], tasks_now=[],
    )
    ctx.plan_notes = [{"kind": "decompose"}]
    ctx.replan_notes = [{"kind": "decompose"}]
    assert ctx.stop(dependency_notes=[])["dependency_notes"] == []
    out = ctx.stop(replan_stop_notice="n")
    assert out["dependency_notes"] == [{"kind": "decompose"}]
    assert out["replan_stop_notice"] == "n"


# ── J4 ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("ownership", [False, True])
def test_j4_single_config_render_same_passed_or_default(ownership: bool) -> None:
    """운영(설정 1벌): 넘긴 설정 = 전역 설정이면 넘김·미지정 렌더가 바이트 동일."""
    cfg = AppConfig(
        multi_db=MultiDBConfig(active_db_ids_csv="polestar_b0,polestar_cm_gp,itam"),
        router=RouterConfig(
            capability_ownership_enabled=ownership, two_stage_enabled=False,
            unknown_enabled=False,
        ),
    )
    domains = list(DB_DOMAINS.values()) if isinstance(DB_DOMAINS, dict) else list(DB_DOMAINS)
    with patch.object(sr, "load_config", return_value=cfg):
        default = sr._build_router_prompt(domains)
        passed = sr._build_router_prompt(domains, app_config=cfg)
        slots_d = sr._ownership_prompt_slots(domains)
        slots_p = sr._ownership_prompt_slots(domains, app_config=cfg)
    assert default == passed
    assert slots_d == slots_p
    assert all(v == "" for v in slots_p.values()) is (not ownership)


# ── P3 ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def pdir(tmp_path, monkeypatch):
    d = tmp_path / "config" / "db_profiles"
    d.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    sa._clear_manual_profile_cache()
    yield d
    sa._clear_manual_profile_cache()


def _doc(attr: str) -> dict[str, Any]:
    return {"source": "manual", "patterns": [{"known_attributes": [attr]}]}


def _attrs(db_id: str) -> list[str] | None:
    prof = sa._load_manual_profile(db_id)
    return None if prof is None else prof["patterns"][0]["known_attributes"]


def test_p3_same_mtime_different_size_rereads(pdir) -> None:
    path = pdir / "v.yaml"
    path.write_text(yaml.safe_dump(_doc("A")), encoding="utf-8")
    assert _attrs("v") == ["A"]
    st = os.stat(path)
    path.write_text(yaml.safe_dump(_doc("LONGER")), encoding="utf-8")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))  # mtime 그대로 되돌림
    assert os.stat(path).st_mtime_ns == st.st_mtime_ns
    assert _attrs("v") == ["LONGER"]


def test_p3_same_size_different_mtime_rereads(pdir) -> None:
    path = pdir / "v.yaml"
    path.write_text(yaml.safe_dump(_doc("A")), encoding="utf-8")
    assert _attrs("v") == ["A"]
    st = os.stat(path)
    path.write_text(yaml.safe_dump(_doc("B")), encoding="utf-8")  # 같은 크기
    assert os.stat(path).st_size == st.st_size
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1))  # 1ns만 차이
    assert _attrs("v") == ["B"]


def test_p3_failure_leaves_no_cache_entry(pdir) -> None:
    path = pdir / "v.yaml"
    path.write_text("source: [manual\n", encoding="utf-8")
    assert sa._load_manual_profile("v") is None
    assert os.path.abspath(os.path.join("config", "db_profiles", "v.yaml")) not in (
        sa._MANUAL_PROFILE_CACHE
    )


def test_p3_non_dict_then_fixed_file(pdir) -> None:
    path = pdir / "v.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    assert sa._load_manual_profile("v") is None
    st = os.stat(path)
    path.write_text(yaml.safe_dump(_doc("A")), encoding="utf-8")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1))
    assert _attrs("v") == ["A"]


def test_p3_nested_detail_mutation_does_not_leak(pdir) -> None:
    (pdir / "v.yaml").write_text(yaml.safe_dump({"source": "manual", "patterns": [
        {"known_attributes": [{"name": "OSType", "synonyms": ["os"]}]},
    ]}, allow_unicode=True), encoding="utf-8")
    a = sa._load_manual_profile("v")
    a["patterns"][0]["known_attributes_detail"][0]["synonyms"].append("오염")
    b = sa._load_manual_profile("v")
    assert b["patterns"][0]["known_attributes_detail"][0]["synonyms"] == ["os"]


# ── J7 ──────────────────────────────────────────────────────────────────────


def _no_stat(engine: str, db_id: str) -> dict[str, Any]:
    case = _case(engine, db_id)
    case["schema_info"] = {"tables": _TABLES, **_EAV_META}
    return case


async def _run_multi_with_answers(case, calls, answers) -> dict[str, Any]:
    start = len(calls)
    ids = w0._adapter_ids(case)
    template = {"sheets": [{"title_text": case["title"], "name": "Sheet1"}]}
    sql = await w0.multi_mod._generate_sql(
        llm=None,  # type: ignore[arg-type]
        parsed_requirements={"original_query": case["query"]},
        schema_info=case["schema_info"],
        sub_query_context="양식 채우기",
        default_limit=100_000,
        column_mapping={f: c for f, c in case["mapping"].items() if c is not None},
        db_engine=case["engine"],
        db_id=case["db_id"],
        unmapped_fields=[f for f, c in case["mapping"].items() if c is None],
        app_config=w0._app_config(ids),
        form_context_text=w0.template_context_text(template),
        form_fill_out={},
        form_intent=True,
        mapping_sources=dict(case["sources"]),
        form_fill_answers=answers,
        query_time=case["query_time"],
    )
    new = calls[start:]
    assert len(new) == 1
    assert new[0]["sql"] == sql
    return new[0]


@pytest.mark.asyncio
async def test_j7_db2_adapter_recovers_like_single(recorder) -> None:  # noqa: F811
    multi = await w0._run_multi(_no_stat("db2", "polestar_b0"), recorder)
    single = await w0._run_single(_case("db2", "polestar_b0"), recorder)
    fields = multi["inputs"]["metric_fields"]
    assert _CPU in fields and _MEM in fields and "비고" not in fields
    assert set(fields) == set(single["inputs"]["metric_fields"])


@pytest.mark.asyncio
async def test_j7_applied_override_excluded_from_recovery(recorder) -> None:  # noqa: F811
    """답변 오버라이드(blank)가 적용된 사용률 필드는 스키마 탈락 회수에서도 빠진다(D-151)."""
    case = _no_stat("postgresql", "")
    out = await _run_multi_with_answers(
        case, recorder, {_CPU: {"action": "blank", "value": None}},
    )
    assert _CPU not in out["inputs"]["metric_fields"]
    assert _MEM in out["inputs"]["metric_fields"]
