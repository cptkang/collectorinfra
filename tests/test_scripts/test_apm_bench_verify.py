"""plans/152 독립 검증(verifier) — APM 조회 벤치의 결함 재현과 경계 확인.

결함 재현은 `xfail(strict=True)`로 둔다 — 고쳐지면 XPASS 로 깨져 이 표지를 지우게 한다.
실 LLM·과금 API·제니퍼 접속 없음. 게이트웨이 테스트는 127.0.0.1 목 Open API + 벤치 게이트웨이
(서브프로세스)만 띄운다. 이름은 전부 합성 값이다.
"""

from __future__ import annotations

import ast
import asyncio
import re
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from scripts.apm_bench import driver as dv
from scripts.apm_bench import gateway as gw
from scripts.apm_bench import guard as gd
from scripts.apm_bench import hygiene as hy
from scripts.apm_bench import loop
from scripts.apm_bench.scenarios import load_registry_info

REPO = Path(__file__).resolve().parents[2]
BENCH_DIR = REPO / "scripts/apm_bench"
CANARY = "CANARY-V9X"


# --- 경계(통과 확인) ---------------------------------------------------------------------


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return out


def test_bench_never_imports_apm_gateway_package() -> None:
    """D-274 — 벤치는 게이트웨이를 서브프로세스·MCP 계약으로만 쓴다(import 0)."""
    offenders = {p.name: sorted(m for m in _imports(p) if m.split(".")[0] == "apm_gateway")
                 for p in BENCH_DIR.glob("*.py")}
    assert {k: v for k, v in offenders.items() if v} == {}


def test_bench_reaches_no_job_control_tool() -> None:
    """벤치가 직접 부를 수 있는 도구(레지스트리 보기 도구 + 고정 호출)에 job 제어 도구가 없다."""
    registry = load_registry_info()
    tools = {v.tool for v in registry.views.values()} | {"gateway_health", *(
        t for t, *_ in dv.INVENTORY_CALLS)}
    assert not {t for t in tools if "job" in t}
    # `apm_job_accepted`는 도구가 아니라 감사 로그 결과 종류 어휘(audit_digest)라 허용한다
    allowed = {"apm_job_accepted"}
    assert not allowed & {"apm_job_status", "apm_job_read", "apm_job_cancel", "apm_job_result"}
    for path in BENCH_DIR.glob("*.py"):
        found = set(re.findall(r"apm_job_[A-Za-z0-9_]+", path.read_text(encoding="utf-8")))
        assert not found - allowed, (path.name, sorted(found - allowed))


# --- [High] 팬아웃·복합 도구의 429 를 가드가 못 본다 ---------------------------------------


@pytest.fixture(scope="module")
def live_gateway() -> Iterator[tuple[gw.MockServer, Any]]:
    pytest.importorskip("mcp")
    session = Path(tempfile.mkdtemp(prefix="apm_verify_"))
    mock = gw.start_mock(gw.bench_fixtures(), "verify-mock-tok", session / "fixtures")
    proc = None
    try:
        proc = gw.start_gateway(session, sources=sorted(load_registry_info().source_ids),
                                mock=mock)
        proc.call_tool("gateway_health", {})
        yield mock, proc
    finally:
        if proc is not None:
            proc.stop()
        mock.stop()
        shutil.rmtree(session, ignore_errors=True)


def _call_with_429(mock: gw.MockServer, proc: Any, tool: str,
                   args: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    tail = gw.AuditTail(proc.log_path)
    mark = tail.mark()
    mock.inject(gw.Faults(status=429, remaining=1))
    try:
        envelope = proc.call_tool(tool, args)
    finally:
        mock.inject(None)
    return envelope, tail.collect(mark)


@pytest.mark.serial
@pytest.mark.parametrize("tool, args", [
    ("apm_fleet", {"mode": "ranking"}),
    ("apm_app_health", {"instance_name": "was01_a"}),
])
def test_partial_429_must_abort_guard(live_gateway: tuple[gw.MockServer, Any], tool: str,
                                      args: dict[str, Any]) -> None:
    mock, proc = live_gateway
    envelope, entries = _call_with_429(mock, proc, tool, args)
    # 전제: 429 가 실제로 났고 봉투에는 흔적이 남는다
    assert envelope.get("partial") is True and envelope.get("limits")
    assert not envelope.get("error")
    assert entries and all(e.get("principal") == gw.BENCH_PRINCIPAL for e in entries)
    guard = gd.Guard(timeout_sec=gw.BENCH_API_TIMEOUT_SEC)
    assert guard.observe(entries) == "quota_429"


# --- [Medium] E2 는 본체 LLM 평면의 과금 여부를 보지 않고 기동한다 ---------------------------


class _StartedError(Exception):
    """게이트웨이 기동에 닿았다는 표지."""


def test_e2_billing_plane_blocks_before_anything_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.config as cfg_mod

    plane = type("Plane", (), {"provider": "gemini"})()
    fake_cfg = type("Cfg", (), {"llm": plane, "orchestrator": plane})()
    monkeypatch.setattr(cfg_mod, "load_config", lambda *a, **k: fake_cfg)
    started: list[str] = []

    def no_start(*_a: Any, **_k: Any) -> Any:
        started.append("gateway")
        raise _StartedError

    monkeypatch.setattr(gw, "start_gateway", no_start)
    monkeypatch.setattr(gw, "dotenv_sources", lambda *a, **k: [])
    placeholders = dv.load_placeholders(REPO / "testdata/apm_bench/placeholders.e1.yaml")
    with pytest.raises(Exception):  # noqa: B017,PT011 — 차단 예외 종류는 수정 측이 정한다
        dv.run([], env="e2", placeholders=placeholders, view_server_version="5.6.2.18",
               say=lambda _t: None)
    assert started == []


# --- [Medium] 위생: 부분 이름 누출 ------------------------------------------------------------


def _record(prompt: str, response: str, clarification: dict[str, str] | None = None
            ) -> dict[str, Any]:
    observed: dict[str, Any] = {
        "agents": ["apm_query"], "plan_raw": [], "plan_final": [], "sanitize_changes": [],
        "reselect": None, "targets": [], "untargeted": True, "time_resolution": None,
        "calls": [], "answer_lines": [], "disclosures": [], "clarify_kind": None,
        "clarify_text": None, "refused": False, "response": response,
        "response_len": len(response), "apm_called": True, "error": None,
    }
    if clarification is not None:
        observed["apm_source_clarification"] = clarification
    return {
        "v": 1, "scenario_id": "APM-V1", "turn": 0, "split": "dev", "covers": ["COV-X"],
        "heavy": False, "was_touch": False, "sensitive": False,
        "prompt_template": prompt, "prompt": prompt, "placeholders": {},
        "expect": {"agents": ["apm_query"], "views": [], "target": "none", "window": "default",
                   "args": {}, "apm_sources": [], "clarify": None, "refusal": None},
        "thread_id": None, "sent_at": "2026-10-13T09:00:00+09:00", "observed": observed,
        "verdict": {"turn_pass": True, "axes": {}, "categories": []}, "probe": [],
        "evidence": {},
    }


def _sanitize_and_gate(record: dict[str, Any], inventory: list[str]
                       ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    san = hy.RunSanitizer(canaries=[CANARY])
    out = san.sanitize([record], inventory_names=inventory)
    staged = hy.stage_outputs(out.records, out.run_meta, out.probe_rows, "# c\n", "# r\n")
    gate = hy.make_gate(inventory_names=inventory, placeholders={}, user_values={},
                        canaries=[CANARY], sanitizer=san)
    return out.records[0], gate.check(staged)


def test_free_text_three_char_token_is_replaced_control() -> None:
    """대조군 — 3자 이상 ASCII 토큰은 자유 문장에서 바뀐다(설계대로)."""
    rec, violations = _sanitize_and_gate(_record("zeta 업무 지연", "zeta 업무 지연"),
                                         ["zeta-api02"])
    assert "zeta" not in rec["observed"]["response"] and violations == []


def test_structured_sentence_field_token_leak() -> None:
    clar = {"original_query": "zeta 업무 지연", "question": "zeta 업무를 어느 제니퍼에서 볼까요?"}
    rec, violations = _sanitize_and_gate(_record("zeta 업무 지연", "확인", clar), ["zeta-api02"])
    assert "zeta" not in rec["prompt"]  # 전제: 자유 문장은 바뀐다
    leaked = "zeta" in str(rec["observed"]["apm_source_clarification"])
    assert not leaked or violations  # 남았으면 관문이라도 막아야 한다


def test_two_char_ascii_token_leak() -> None:
    rec, violations = _sanitize_and_gate(_record("주문 지연", "kb 계열 주문 지연"),
                                         ["kb-order-was01"])
    assert "kb" not in rec["observed"]["response"].lower().split() or violations


def test_camel_case_joined_token_leak() -> None:
    rec, violations = _sanitize_and_gate(_record("주문 지연", "ordpay 응답 느림"), ["OrdPay01"])
    assert "ordpay" not in rec["observed"]["response"].lower() or violations


# --- [Medium] holdout 실패가 수정 위치로 노출된다(권고 ③) -------------------------------------


def _failed(sid: str, split: str) -> dict[str, Any]:
    return {"scenario_id": sid, "turn": 0, "split": split, "covers": ["COV-X"],
            "observed": {}, "verdict": {
                "turn_pass": False, "axes": {"views": "fail"}, "categories": ["view_wrong"],
                "axis_detail": {"categories": {"view_wrong": {
                    "fewshot_fixable": True, "locus": [f"F1:apm.{sid}:example#1"]}}},
                "fewshot_copy": {"view": "apm.ranking", "example_index": 1, "args": {},
                                 "attached": "views"},
                "expected_clarify": False, "expected_refusal": False}}


def test_report_hides_holdout_fix_locations() -> None:
    from scripts.apm_bench.__main__ import render_report

    text = render_report([_failed("devx", "dev"), _failed("holdx", "holdout")],
                         {"run_id": "r", "env": "e1"}, term_lines=[], missing=[],
                         probe_rows=[])
    assert "F1:apm.devx" in text  # 전제: dev 위치는 실린다
    assert "holdx" not in text


def test_replay_default_skips_holdout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_turn(record: Any, *_a: Any, **_k: Any) -> dict[str, Any]:
        return {"observed": {"plan_final": [], "reselect": None}, "degraded": False}

    monkeypatch.setattr(loop, "_replay_turn", fake_turn)
    monkeypatch.setattr(loop, "_views_args_axes", lambda *a, **k: {"views": "fail",
                                                                  "args": "fail"})
    rows = asyncio.run(loop.areplay([_failed("devx", "dev"), _failed("holdx", "holdout")],
                                    llm=object(), registry=load_registry_info(),
                                    app_config=object()))
    assert [r["id"] for r in rows] == ["devx#0"]
