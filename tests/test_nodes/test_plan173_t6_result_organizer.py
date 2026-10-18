"""plans/173 T6 특성화 — 노드 `result_organizer` 현행 동작 고정(Extract Function 전 안전망).

기대값은 「맞는 값」이 아니라 1ad7c161 시점의 「현재 값」이다. 시나리오마다 반환 dict 전체(키 순서
포함)·협력 함수 호출 순서와 인자 요약·`src.nodes.result_organizer` 로그 문구와 순서를 스냅샷으로
고정한다. 협력 함수(충분성 판정·이름 칸 힌트·0건 진단·레거시 필드 매핑·Layer 2)는 모듈 전역
seam으로 가짜 주입한다 — LLM·DB 미사용.
"""

from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.config import (
    AppConfig,
    DBHubConfig,
    LLMConfig,
    QueryConfig,
    SecurityConfig,
    ServerConfig,
)
from src.state import create_initial_state

_SNAP = Path(__file__).resolve().parents[1] / "snapshots" / "plan173_t6" / "result_organizer.json"
_LOGGER = "src.nodes.result_organizer"
_LLM = object()


def _config() -> AppConfig:
    """검증 대상 필드를 명시한다(.env 누수 차단 — tests/conftest.py mock_config와 같은 값)."""
    return AppConfig(
        llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
        dbhub=DBHubConfig(
            server_url="http://localhost:9099/sse", source_name="infra_db", mcp_call_timeout=60,
        ),
        query=QueryConfig(max_retry_count=3, default_limit=1000),
        security=SecurityConfig(
            sensitive_columns=["password", "secret", "token", "api_key"],
            mask_pattern="***MASKED***",
        ),
        server=ServerConfig(host="0.0.0.0", port=8000),
        checkpoint_backend="sqlite",
        checkpoint_db_url=":memory:",
    )


_ROWS = [
    {"hostname": "web-01", "usage_pct": 85.25, "total_gb": 1234.5, "password": "p1", "cnt": 7},
    {"hostname": "web-02", "usage_pct": 92.1, "total_gb": 64, "password": "p2", "cnt": 3},
]
_SRC_ROWS = [
    {"_source_db": "db_a", "hostname": "a1", "cpu_pct": 91.0},
    {"_source_db": "db_b", "hostname": "b1", "cpu_pct": 75.5},
    {"_source_db": "db_a", "hostname": "a2", "cpu_pct": 40.0},
]
_TWO_SHEETS = {
    "file_type": "xlsx",
    "sheets": [{"name": "S1", "headers": ["호스트"]}, {"name": "S2", "headers": []}],
}
_ONE_SHEET = {"file_type": "docx", "sheets": [{"name": "S1", "headers": ["호스트", "사용률"]}]}


def _parsed(fmt: str = "text") -> dict[str, Any]:
    return {"query_targets": ["서버", "CPU"], "output_format": fmt, "original_query": "q"}


def _state(**over: Any) -> dict[str, Any]:
    state = dict(create_initial_state(user_query="q"))
    state["parsed_requirements"] = _parsed()
    state["schema_info"] = {"tables": {"t": {}}}
    state.update(over)
    return state


_SCENARIOS: dict[str, dict[str, Any]] = {
    # 0행 + 이름 칸 힌트 발동 → 힌트 반환(진단·포맷 생략)
    "hint": {"state": {"query_results": []}, "sufficient": False, "hint": "HINT-X"},
    # 0행 + 힌트 없음 + 진단 재생성 가능 → data_insufficient(진단 델타 포함)
    "insufficient_diag": {
        "state": {"query_results": []}, "sufficient": False, "diag": True,
    },
    # 0행 + 진단 재생성 불가 → 충분으로 승격해 정리 완료
    "diag_not_regenerable": {
        "state": {"query_results": []}, "sufficient": False, "diag": False,
    },
    # 0행 + 진단 None + 부족 → data_insufficient(진단 델타 없음)
    "insufficient_no_diag": {
        "state": {"query_results": []}, "sufficient": False, "diag": None,
    },
    # 행 있음 + 부족 + 재시도 예산 소진 → 정리 완료(텍스트 숫자 포맷·마스킹)
    "budget_exhausted": {
        "state": {"query_results": _ROWS, "retry_count": 3}, "sufficient": False,
    },
    # 폼필 결정적 결과 억제 + field_mapper 매핑 재사용 + Layer 1/2
    "form_suppress_mapping": {
        "state": {
            "query_results": _ROWS, "template_structure": _ONE_SHEET,
            "parsed_requirements": _parsed("xlsx"),
            "column_mapping": {"호스트": "hostname", "사용률": "cpu_usage", "비고": "memo"},
        },
        "sufficient": False,
        "layer2": {"사용률": "usage_pct"},
    },
    # 폼필 월 앵커만 있고 Layer 2가 빈 결과
    "form_anchor_layer2_empty": {
        "state": {
            "query_results": _ROWS, "form_month_anchor": "2026-09",
            "column_mapping": {"호스트": "hostname", "x": "nope"},
        },
        "sufficient": False,
        "layer2": None,
    },
    # 레거시 폴백 — 다중 시트 xlsx → 시트별 매핑
    "legacy_per_sheet": {
        "state": {
            "query_results": _ROWS, "template_structure": _TWO_SHEETS,
            "parsed_requirements": _parsed("xlsx"), "target_sheets": ["S1"],
        },
        "sufficient": True,
        "per_sheet": [
            {"sheet_name": "S1", "column_mapping": {"호스트": "hostname", "용량": "disk"},
             "resolved_mapping": None, "rows": []},
            {"sheet_name": "S2", "column_mapping": {}, "resolved_mapping": None, "rows": []},
        ],
        "layer2": {"용량": "total_gb"},
    },
    # 레거시 폴백 — 시트별 매핑 실패(None) → 매핑 없음
    "legacy_per_sheet_none": {
        "state": {
            "query_results": _ROWS, "template_structure": _TWO_SHEETS,
            "parsed_requirements": _parsed("xlsx"),
        },
        "sufficient": True,
        "per_sheet": None,
    },
    # 레거시 폴백 — 단일 시트 docx → 전체 매핑
    "legacy_single": {
        "state": {
            "query_results": _ROWS, "template_structure": _ONE_SHEET,
            "parsed_requirements": _parsed("docx"),
        },
        "sufficient": True,
        "single": {"호스트": "hostname", "사용률": "usage_pct"},
    },
    # 양식 있음 + text 출력 → 매핑 단계 건너뜀
    "template_text_output": {
        "state": {"query_results": _ROWS, "template_structure": _ONE_SHEET},
        "sufficient": True,
    },
    # 멀티 DB 전역 재정렬 적용 + 집계 종합
    "ranking_applied": {
        "state": {
            "query_results": _SRC_ROWS,
            "merged_ranking": {
                "applied": True, "rows": _SRC_ROWS[:2], "db_ids": ["db_a", "db_b"],
                "limit": 2, "source_row_count": 3, "key": "cpu_pct", "descending": True,
            },
            "merged_aggregates": {"applied": True, "source_row_count": 3, "per_db": {"db_a": 2}},
        },
        "sufficient": True,
    },
    # 재정렬 미적용(사유만 운반) · 집계 행 수 불일치(잔존 방어로 미포함)
    "ranking_not_applied": {
        "state": {
            "query_results": _SRC_ROWS,
            "merged_ranking": {"applied": False, "reason": "no_order", "rows": []},
            "merged_aggregates": {"applied": True, "source_row_count": 99},
        },
        "sufficient": True,
    },
}


async def _run(name: str, monkeypatch, caplog) -> dict[str, Any]:
    ro = importlib.import_module("src.nodes.result_organizer")
    sc = _SCENARIOS[name]
    events: list[Any] = []

    async def _suff(rows, parsed, template, *, column_mapping, llm, app_config):
        events.append(["sufficiency", len(rows), bool(template), column_mapping, llm is _LLM])
        return sc["sufficient"]

    def _hint(state, *, max_retry, min_retries_left):
        events.append(["hint", max_retry, min_retries_left])
        return sc.get("hint")

    async def _diag(state, parsed, app_config):
        events.append(["diagnose"])
        if sc.get("diag") is None:
            return None
        return SimpleNamespace(regenerable=sc["diag"])

    def _as_payload(d):
        events.append(["as_payload", d.regenerable])
        return {"regenerable": d.regenerable}

    async def _per_sheet(*, llm, app_config, template, schema_info, target_sheets, rows):
        events.append(["per_sheet", llm is _LLM, len(template["sheets"]), target_sheets, len(rows)])
        return sc.get("per_sheet")

    async def _single(*, llm, app_config, template, schema_info):
        events.append(["single", llm is _LLM, len(template["sheets"]), sorted(schema_info)])
        return sc.get("single")

    async def _layer2(*, llm, column_mapping, unresolved_fields, result_keys, app_config):
        events.append(["layer2", llm is _LLM, unresolved_fields, sorted(result_keys)])
        return sc.get("layer2")

    monkeypatch.setattr(ro, "_check_data_sufficiency", _suff)
    monkeypatch.setattr(ro, "name_match_retry_hint", _hint)
    monkeypatch.setattr(ro, "_diagnose_empty_result", _diag)
    monkeypatch.setattr(ro, "as_payload", _as_payload)
    monkeypatch.setattr(ro, "_perform_per_sheet_field_mapping", _per_sheet)
    monkeypatch.setattr(ro, "_perform_field_mapping", _single)
    monkeypatch.setattr(ro, "_resolve_unmatched_via_llm", _layer2)
    caplog.set_level(logging.DEBUG, logger=_LOGGER)

    out = await ro.result_organizer(_state(**sc["state"]), llm=_LLM, app_config=_config())
    logs = [[r.levelname, r.getMessage()] for r in caplog.records if r.name == _LOGGER]
    return {"result": out, "events": events, "logs": logs}


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, default=repr)


@pytest.mark.parametrize("name", list(_SCENARIOS))
async def test_scenario_matches_snapshot(name, monkeypatch, caplog):
    """시나리오별 반환 dict·호출 순서·로그가 1ad7c161 값과 같다."""
    got = await _run(name, monkeypatch, caplog)
    expected = json.loads(_SNAP.read_text(encoding="utf-8"))[name]
    assert _dump(got) == _dump(expected)
