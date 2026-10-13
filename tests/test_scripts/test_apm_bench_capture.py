"""plans/152 W2 — 본체 관측 지점 3곳(run_capture.emit)과 벤치 수신기(`scripts/apm_bench/_serve.py`).

- 미설치: 세 지점 모두 결과가 설치 때와 비트 동일하다(emit 은 즉시 반환)
- 설치: 지점마다 kind·payload 키가 계약대로 온다
- 수신기: 이름·인자만 즉시 복사하고 원본을 보관하지 않는다 · 결과 행은 옮기지 않는다 ·
  APM 이 아닌 조사 backend 는 옮기지 않는다
"""

from __future__ import annotations

import asyncio
import copy
import importlib
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.apm_bench import _serve
from src.config import AppConfig
from src.observability import run_capture
from src.orchestration import apm_query, investigation_audit

# `src.orchestration`가 같은 이름의 노드 함수를 재노출하므로 모듈은 import_module 로 잡는다
intent_planner = importlib.import_module("src.orchestration.intent_planner")


@pytest.fixture
def sink() -> Iterator[list[tuple[str, Any]]]:
    got: list[tuple[str, Any]] = []

    def receive(kind: str, payload: Any) -> None:
        got.append((kind, copy.deepcopy(payload)))

    run_capture.install(receive)
    try:
        yield got
    finally:
        run_capture.uninstall()


def _plan() -> dict[str, Any]:
    return {"tasks": [
        {"task_id": "t1", "agent": "apm_query", "sub_query": "q", "order": 1,
         "views": ["apm.app_health", "apm.nope"], "view_args": {"apm.app_health": {}},
         "targets": [{"text": "was01_a", "kind": "instance"}], "sources": ["common", 3],
         "areas": ["was_performance"]},
        {"task_id": "t2", "agent": "data_query", "sub_query": "q2", "order": 2},
    ], "clarification_needed": None}


# --- 지점 ② intent_planner._sanitize_task_views ------------------------------------------


def test_plan_raw_uninstalled_bit_identical(sink: list) -> None:
    cfg = AppConfig()
    with_sink = _plan()
    intent_planner._sanitize_task_views(with_sink, cfg)
    run_capture.uninstall()
    without = _plan()
    intent_planner._sanitize_task_views(without, cfg)
    assert json.dumps(with_sink, sort_keys=True) == json.dumps(without, sort_keys=True)


def test_plan_raw_emits_before_sanitize(sink: list) -> None:
    intent_planner._sanitize_task_views(_plan(), AppConfig())
    kinds = [k for k, _ in sink]
    assert kinds == ["apm_plan_raw"]
    payload = sink[0][1]
    assert payload["tasks"][0]["views"] == ["apm.app_health", "apm.nope"]  # 정제 전
    record = _serve.capture_record(*sink[0])
    assert record is not None and record["kind"] == "apm_plan_raw"
    assert record["tasks"][0]["sources"] == ["common", 3]
    assert set(record["tasks"][1]) == {"task_id", "agent"}


# --- 지점 ① investigation_audit.audited_investigation -----------------------------------


def _result() -> dict[str, Any]:
    return {
        "final_response": "응답", "answer_lines": ["판정 A"], "query_results": [{"secret": 1}],
        "disclosures": [{"kind": "bridge", "text": "t", "source": "apm", "extra": 1}],
        apm_query.META_KEY: {
            "selection": {"areas": ["a"], "planned": ["apm.app_health"], "retried": False,
                          "result": "planned", "latency_ms": 3},
            "views": ["apm.app_health"],
            "targets": {"texts": [{"text": "was01_a", "kind": "instance", "searched": ["name"],
                                   "instances": 1, "evidence": {"이름": 1},
                                   "suggestions": [], "failures": []}],
                        "instances": [{"apm_instance_name": "was01_a", "apm_instance_id": "1001",
                                       "apm_domain_id": "1000", "apm_source_id": "common",
                                       "hostname": None}]},
            "hostnames": [], "inserted_steps": [{"view": "apm.ranking", "mode": "ranking"}],
            "provenance": [{"view": "apm.app_health", "tool": "apm_app_health", "rows": 1,
                            "window": {"start": "s", "end": "e", "minutes": 30},
                            "args": {"instance_id": 1001}, "reference": None,
                            "raw_rows": [{"secret": 2}]}],
            "failures": [], "source_status": {"rows": [{"secret": 3}]},
        },
    }


def _run_audited(backend: str) -> Any:
    async def handler(task: dict, isolated: dict, *, llm: Any, app_config: Any) -> Any:
        return _result()

    wrapped = investigation_audit.audited_investigation(handler, backend)
    cfg = SimpleNamespace(composite=SimpleNamespace(audit_enabled=False))  # 감사 기록 끔
    return asyncio.run(wrapped({"task_id": "t1", "agent": "apm_query", "views": ["apm.app_health"]},
                               {"thread_id": "th-1"}, llm=None, app_config=cfg))


def test_investigation_uninstalled_bit_identical(sink: list) -> None:
    with_sink = _run_audited(investigation_audit.BACKEND_APM)
    run_capture.uninstall()
    without = _run_audited(investigation_audit.BACKEND_APM)
    assert with_sink == without == _result()


def test_investigation_emits_and_copies_meta_only(sink: list) -> None:
    _run_audited(investigation_audit.BACKEND_APM)
    assert [k for k, _ in sink] == ["investigation_result"]
    backend, task, isolated, result = sink[0][1]
    assert backend == investigation_audit.BACKEND_APM and isolated["thread_id"] == "th-1"
    record = _serve.capture_record(*sink[0])
    assert record is not None
    assert record["thread_id"] == "th-1" and record["task"]["views"] == ["apm.app_health"]
    assert record["answer_lines"] == ["판정 A"]
    assert record["meta"]["targets"]["texts"][0]["instances"] == 1
    assert record["meta"]["provenance"][0]["window"] == {"start": "s", "end": "e", "minutes": 30}
    text = json.dumps(record, ensure_ascii=False)
    assert "secret" not in text and "query_results" not in text and "extra" not in text


def test_non_apm_backend_not_recorded() -> None:
    assert _serve.capture_record(
        "investigation_result", ("process_api", {}, {"thread_id": "x"}, _result())) is None


def test_record_detached_from_source() -> None:
    result = _result()
    record = _serve.capture_record(
        "investigation_result", (investigation_audit.BACKEND_APM, {"views": ["v"]}, {}, result))
    result[apm_query.META_KEY]["provenance"][0]["args"]["instance_id"] = 9
    result["answer_lines"].append("늦게 붙인 줄")
    assert record is not None
    assert record["meta"]["provenance"][0]["args"] == {"instance_id": 1001}
    assert record["answer_lines"] == ["판정 A"]


# --- 지점 ③ apm_query._retry_selection ---------------------------------------------------


class _LLM:
    def __init__(self, content: str) -> None:
        self.content = content

    async def ainvoke(self, messages: Any) -> Any:
        return type("R", (), {"content": self.content})()


def _reselect() -> Any:
    llm = _LLM('{"views": ["apm.app_health"], "view_args": {"apm.app_health": {}}}')
    return asyncio.run(apm_query._retry_selection(
        llm, {"sub_query": "응답시간"}, ["was_performance"], {"thread_id": "th-2"}))


def test_reselect_uninstalled_bit_identical(sink: list) -> None:
    with_sink = _reselect()
    run_capture.uninstall()
    assert _reselect() == with_sink


def test_reselect_emits_parsed(sink: list) -> None:
    _reselect()
    assert [k for k, _ in sink] == ["apm_view_reselect"]
    assert set(sink[0][1]) == {"parsed", "areas", "thread_id"}
    record = _serve.capture_record(*sink[0])
    assert record == {"kind": "apm_view_reselect", "thread_id": "th-2",
                      "areas": ["was_performance"], "parsed_is_dict": True,
                      "parsed_views": ["apm.app_health"],
                      "parsed_view_args": {"apm.app_health": {}}}


# --- 수신 파일 ----------------------------------------------------------------------------


def test_file_sink_and_tail(tmp_path: Path) -> None:
    path = tmp_path / "capture.jsonl"
    sink = _serve.FileSink(path)
    tail = _serve.CaptureTail(path)
    first = tail.mark()
    sink("apm_plan_raw", _plan())
    sink("unknown_kind", {"x": 1})
    middle = tail.mark()
    sink("apm_view_reselect", {"parsed": None, "areas": [], "thread_id": "t"})
    assert [r["kind"] for r in tail.collect(first)] == ["apm_plan_raw", "apm_view_reselect"]
    assert [r["kind"] for r in tail.collect(middle)] == ["apm_view_reselect"]


def test_install_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(_serve.CAPTURE_ENV, raising=False)
    assert _serve.install_from_env() is False
    monkeypatch.setenv(_serve.CAPTURE_ENV, str(tmp_path / "c.jsonl"))
    try:
        assert _serve.install_from_env() is True and run_capture.installed()
    finally:
        run_capture.uninstall()
