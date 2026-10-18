"""단일 DB 폼필 사용률 회수 — 멀티 경로와 같은 규칙 (plans/185 W1 · 182 J1 · D-066).

field_mapper(유사어·힌트 출처)가 사용률 필드를 사용률 통계 칼럼(`cmm_metric_stat_[h|d|m].<칼럼>`)에
매핑하면, 그 매핑은 강제 SELECT에서 빠진다(RC2). 멀티 경로는 그 필드를 필드명 기반 피벗으로
회수했지만 단일 경로는 버려 빈칸이었다 — 두 경로가 `select_metric_recovery_fields`를 함께 쓴다.

단일/멀티 구동은 plans/166 W0 하네스(`test_plan166_w0_form_fill_pivot_inputs`)를 재사용한다.
그 스냅샷에는 케이스를 더하지 않는다(별도 파일).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from src.db_adapters.polestar.form_fill_plan import select_metric_recovery_fields
from src.domain.query_time import resolve_query_time
from tests.test_nodes.test_form_month_series import _EAV_META
from tests.test_nodes.test_plan166_w0_form_fill_pivot_inputs import (  # noqa: F401 — 픽스처
    _TABLES,
    _run_multi,
    _run_single,
    recorder,
)

_CPU = "CPU 평균 사용률"
_MEM = "메모리 최대 사용률"
_KST = timezone(timedelta(hours=9))
_NOW = datetime(2026, 9, 29, 10, 0, tzinfo=_KST)

# 사용률 통계 테이블을 스키마에 둔다 — 멀티 경로는 스키마에 없는 테이블 매핑을 미리 거른다
_STAT_TABLES = {
    **_TABLES,
    "cmm_metric_stat_h": {"columns": [
        {"name": "cpu_avg_val", "type": "numeric"},
        {"name": "mem_max_val", "type": "numeric"},
    ]},
}


def _case(engine: str, db_id: str) -> dict[str, Any]:
    """사용률 통계 칼럼 매핑(유사어 출처) + 미매핑 사용률 + 비사용률 필드의 통계 칼럼 매핑."""
    query = "최근 3개월 서버 사용률 양식 채워줘"
    return {
        "mapping": {
            "서버명": "cmm_resource.name",
            "호스트명": "cmm_resource.hostname",
            _CPU: "cmm_metric_stat_h.cpu_avg_val",
            _MEM: None,
            # 사용률이 아닌 필드가 통계 칼럼에 잘못 매핑돼도 회수 대상이 아니다
            "비고": "cmm_metric_stat_h.mem_max_val",
        },
        "sources": {"서버명": "synonym", "호스트명": "synonym", _CPU: "synonym", "비고": "hint"},
        "schema_info": {"tables": _STAT_TABLES, **_EAV_META},
        "query": query,
        "title": "서버 사용률 현황",
        "engine": engine,
        "db_id": db_id,
        "query_time": resolve_query_time(query, _NOW) if db_id else None,
    }


_CASES = {
    "postgresql_surface": _case("postgresql", ""),
    "db2_b0_adapter": _case("db2", "polestar_b0"),
}


@pytest.mark.parametrize("name", sorted(_CASES))
async def test_single_recovers_metric_stat_mapped_field(recorder, name):  # noqa: F811
    """단일 경로가 통계 칼럼 매핑 사용률 필드를 회수한다(옛 동작: 빠져 빈칸)."""
    single = await _run_single(_CASES[name], recorder)
    fields = single["inputs"]["metric_fields"]
    assert _CPU in fields
    assert _MEM in fields
    assert "비고" not in fields
    assert f'AS "{_CPU}"' in single["sql"]


@pytest.mark.parametrize("name", sorted(_CASES))
async def test_single_and_multi_metric_fields_match(recorder, name):  # noqa: F811
    """같은 양식 매핑을 두 경로 입력 형태로 넣으면 회수 대상이 같다(순서 포함)."""
    single = await _run_single(_CASES[name], recorder)
    multi = await _run_multi(_CASES[name], recorder)
    assert set(single["inputs"]["metric_fields"]) == set(multi["inputs"]["metric_fields"])
    assert single["inputs"]["metric_fields"] == multi["inputs"]["metric_fields"]


async def test_llm_inferred_metric_stat_mapping_recovered_once(recorder):  # noqa: F811
    """llm_inferred 강등(제자리 None) 필드도 한 번만 회수한다 — 두 경로 집합 동일.

    순서는 다를 수 있다(종전과 같음): 단일은 강등 필드가 매핑 순서 자리의 None이고, 멀티는
    강등 필드를 별도 묶음(뒤)으로 받는다. writer는 alias로 찾으므로 SELECT 순서는 채움과 무관.
    """
    case = _case("postgresql", "")
    case["sources"] = {**case["sources"], _CPU: "llm_inferred"}
    single = await _run_single(case, recorder)
    multi = await _run_multi(case, recorder)
    assert single["inputs"]["metric_fields"].count(_CPU) == 1
    assert multi["inputs"]["metric_fields"].count(_CPU) == 1
    assert set(single["inputs"]["metric_fields"]) == set(multi["inputs"]["metric_fields"])


def _is_metric(field: str) -> bool:
    return "사용률" in field


class TestSelectMetricRecoveryFields:
    """회수 대상 선택 공통 함수 — 제외·판정·순서·중복 제거."""

    def test_order_follows_groups_and_dedupes(self):
        got = select_metric_recovery_fields(
            ["CPU 사용률", "서버명", "디스크 사용률"],
            ["메모리 사용률", "CPU 사용률"],
            ["디스크 사용률", "스왑 사용률"],
            exclude=set(),
            is_metric=_is_metric,
        )
        assert got == ["CPU 사용률", "디스크 사용률", "메모리 사용률", "스왑 사용률"]

    def test_excludes_month_concat_and_override_fields(self):
        got = select_metric_recovery_fields(
            ["CPU 사용률|M", "제조사 사용률", "답변 사용률", "메모리 사용률"],
            ["CPU 사용률|M", "답변 사용률"],
            exclude={"CPU 사용률|M"} | {"제조사 사용률"} | {"답변 사용률"},
            is_metric=_is_metric,
        )
        assert got == ["메모리 사용률"]

    def test_non_metric_field_in_metric_stat_group_is_dropped(self):
        got = select_metric_recovery_fields(
            [], ["비고", "CPU 사용률"], exclude=frozenset(), is_metric=_is_metric,
        )
        assert got == ["CPU 사용률"]

    def test_empty_groups(self):
        assert select_metric_recovery_fields(exclude=(), is_metric=_is_metric) == []
