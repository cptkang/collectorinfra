"""plans/187 독립 검증 — sre_agent 쪽(플레이북 엔진 · KB 읽기 측 · 소견 가드 · 배선).

- 수용 기준 10: 플레이북·KE 경로 신규 모듈에 파일 쓰기 호출 0(AST) · 로더에 쓰기 API 없음
- 수용 기준 5: 플래그 off면 배선 생성 시 로더를 부르지 않는다
- D-035: `prior_findings` 재검증이 서술 키·문장 값을 버린다
- 지적 교정 확인(기대값 반전): 종합 소견 극성 검사 · 순위 서술 가드 어휘 확장 · 예시 KE는 픽스처로
  이동(런타임 기본 적재 0) · 수치 허용 집합의 시각 자리 분해 제거·환산 근거 한정 · 거부 파일 notes ·
  잡 종료 뒤 수집기 스텝 미시작
"""

from __future__ import annotations

import ast
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from sre_agent.application import investigation_dispatcher as disp_mod
from sre_agent.application.investigation_guidance import clean_prior_findings
from sre_agent.application.playbook_narrator import (
    SYNTH_POLARITY_DROPPED,
    guard_text,
    source_numbers,
    unknown_numbers,
)
from sre_agent.application.playbook_steps import StepResult
from sre_agent.infrastructure import known_error_loader, playbook_loader
from sre_agent.infrastructure.known_error_loader import DEFAULT_KNOWN_ERROR_DIR, load_known_errors
from sre_agent.infrastructure.tool_result_cache import CacheClosedError
from sre_agent.interface import mcp_service
from sre_agent.settings import AgentSettings
from test_plan187_w2_runner import FakeCaller, _load, _playbook_for
from test_plan187_w4_dispatch import alarm_of, build, make_job, make_settings, run_job

PKG = Path(__file__).resolve().parents[1] / "sre_agent"

NEW_RUNTIME_MODULES = [
    "application/playbook_narrator.py",
    "application/playbook_prompts.py",
    "application/playbook_runner.py",
    "application/playbook_steps.py",
    "domain/engine_agreement.py",
    "domain/known_error.py",
    "domain/playbook.py",
    "domain/playbook_expr.py",
    "domain/playbook_hypotheses.py",
    "domain/playbook_readings.py",
    "domain/source_adapter.py",
    "infrastructure/known_error_loader.py",
    "infrastructure/playbook_loader.py",
    "infrastructure/tool_result_cache.py",
]

#: 파일 시스템 쓰기 호출(문자열 `replace`·`dataclasses.replace`·`list.remove`는 오탐이라 뺀다).
_WRITE_ATTRS = {"write_text", "write_bytes", "unlink", "rename", "rmdir", "mkdir",
                "touch", "dump", "makedirs", "rmtree", "safe_dump"}


def _write_calls(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr in _WRITE_ATTRS:
            found.append(fn.attr)
        if (isinstance(fn, ast.Name) and fn.id == "open") or (
            isinstance(fn, ast.Attribute) and fn.attr == "open"
        ):
            modes = list(node.args[1:2]) + [k.value for k in node.keywords if k.arg == "mode"]
            for m in modes:
                is_str = isinstance(m, ast.Constant) and isinstance(m.value, str)
                if is_str and set(m.value) & set("wax+"):
                    found.append(f"open({m.value!r})")
    return found


@pytest.mark.parametrize("rel", NEW_RUNTIME_MODULES)
def test_ac10_no_file_write_calls_in_playbook_kb_modules(rel: str) -> None:
    tree = ast.parse((PKG / rel).read_text(encoding="utf-8"))
    assert _write_calls(tree) == []


@pytest.mark.parametrize("mod", [known_error_loader, playbook_loader])
def test_ac10_loaders_expose_no_write_api(mod) -> None:  # noqa: ANN001
    public = [n for n in dir(mod) if not n.startswith("_")]
    banned = ("write", "save", "dump", "store")
    assert not [n for n in public if any(w in n.lower() for w in banned)]


def test_ac5_wiring_off_does_not_load_playbooks_or_known_errors(monkeypatch) -> None:  # noqa: ANN001
    def boom(*a, **k):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("플래그 off인데 로더를 불렀다")

    monkeypatch.setattr(mcp_service, "load_playbooks", boom)
    monkeypatch.setattr(mcp_service, "load_known_errors", boom)
    settings = AgentSettings(_env_file=None, model="test/model", sre_playbook_enabled=False)
    assert mcp_service._default_playbook_wiring(settings, lambda: None) is None


def test_d035_prior_findings_drops_narrative() -> None:
    raw = {
        "version": 1, "source": "playbook", "playbook_id": "was.latency",
        "opinion": "DB 지연이 원인으로 보인다", "synthesis": {"narrative": "x"},
        "readings": {"dominant": "sql", "note": "문장 값은 버린다 공백 포함"},
        "sections": [{"step": "S3", "status": "ok", "opinion": "LLM 소견", "verdict": "degraded",
                      "verdict_source": "was_signals"}],
        "tools": ["apm_transaction_top", "rm -rf /"],
    }
    out = clean_prior_findings(raw)
    assert out is not None
    flat = repr(out)
    assert "opinion" not in flat and "synthesis" not in flat and "narrative" not in flat
    assert out["readings"] == {"dominant": "sql"}
    assert out["tools"] == ["apm_transaction_top"]


TOP = "DB 지연 → 풀 고갈 → 응답 지연"
GUARD_KW = dict(verdict=None, allowed=[], max_sentences=6, max_chars=900, top_title=TOP,
                rank_guard=True)


def test_fixed_synthesis_polarity_checked_against_supported_top() -> None:
    """[교정 · 지적 7①] 1순위 가설이 지지인데 「모든 지표 정상」 종합은 버린다."""
    op = guard_text("모든 지표가 정상이며 특이사항 없다.", top_status="supported", **GUARD_KW)
    assert op.text == "" and op.dropped_reason == SYNTH_POLARITY_DROPPED
    # 1순위가 판단 보류면 극성 검사 없음(기존 동작) · 이상 어휘가 함께 있으면 유지.
    assert guard_text("모든 지표가 정상이다.", top_status="inconclusive", **GUARD_KW).text
    assert guard_text("응답 지연 외 지표는 정상이다.", top_status="supported", **GUARD_KW).text


@pytest.mark.parametrize("claim", [
    "가장 가능성이 높은 원인은 GC 과다다.",
    "가장 유력한 원인은 GC 과다다.",
    "주된 원인은 GC 과다다.",
    "근본 원인은 GC 과다다.",
    "GC 과다가 원인으로 확정된다.",
])
def test_fixed_rank_guard_paraphrases_removed(claim: str) -> None:
    """[교정 · 지적 7②] 순위·확정 진술을 바꿔 써도 코드 1순위 제목이 없으면 제거된다."""
    op = guard_text(claim + " 관찰만 적는다.", **GUARD_KW)
    assert op.text == "관찰만 적는다."
    assert guard_text(f"가장 가능성이 높은 원인은 {TOP}이다.", **GUARD_KW).text


def test_fixed_time_digits_not_decomposed() -> None:
    """[교정 · 지적 7③] 시각은 문자열 전체로만 허용 — 각 자리 숫자(07·20·19)는 수치가 아니다."""
    allowed = source_numbers("변경 at 2026-10-19T07:20:00")
    assert unknown_numbers("07:20 변경이 있었다.", allowed) == []
    assert unknown_numbers("07시 20분 변경이 있었다.", allowed) == []
    assert unknown_numbers("2026-10-19 변경이다.", allowed) == []
    assert unknown_numbers("20건 실패했다.", allowed) == ["20"]
    assert unknown_numbers("19분 지연됐다.", allowed) == ["19"]
    assert unknown_numbers("08:20 변경이 있었다.", allowed) == ["08", "20"]


def test_fixed_unit_conversion_only_with_column_basis() -> None:
    """[교정 · 지적 7③] ×/÷100·1000 환산은 ms↔s · 비율↔% 근거 칸에만."""
    table = ('{"columns":["metric","last"],"rows":[["db_pool_usage_ratio",0.73],'
             '["thread_current",180.0]]} {"columns":["app","sql_ms"],"rows":[["/a",6100]]}')
    allowed = source_numbers(table + " count 42")
    assert unknown_numbers("풀 사용률 73%다.", allowed) == []  # 비율 → %
    assert unknown_numbers("SQL 6.1초다.", allowed) == []  # ms → 초
    assert unknown_numbers("스레드 0.18이다.", allowed) == ["0.18"]  # 근거 없는 ÷1000
    assert unknown_numbers("스레드 18000이다.", allowed) == ["18000"]  # 근거 없는 ×100
    assert unknown_numbers("0.42건 비율이다.", allowed) == ["0.42"]  # 근거 없는 ÷100


def test_fixed_example_known_error_moved_out_of_runtime_dir() -> None:
    """[교정 · Major] 예시 KE는 테스트 픽스처로 옮겼다 — 런타임 기본 경로 적재 0 · README는 무시."""
    loaded = load_known_errors(DEFAULT_KNOWN_ERROR_DIR)
    assert loaded.known_errors == () and loaded.rejected == ()
    assert (DEFAULT_KNOWN_ERROR_DIR / "README.md").is_file()
    fixture = Path(__file__).resolve().parent / "fixtures" / "plan187" / "known_errors"
    assert [k.id for k in load_known_errors(fixture).known_errors] == ["KE-EXAMPLE-001"]


# ── 지적 9 · 8 교정 ──────────────────────────────────────────────────────────


def test_fixed_rejected_files_note_in_briefing(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """[교정 · 지적 9] 기동 거부 파일명이 배선에 실리고 브리핑 notes에 1줄(거부 0이면 문구 없음)."""
    (tmp_path / "bad.yaml").write_text("id: KE-BAD\nstate: validated\n", "utf-8")
    monkeypatch.setattr(mcp_service, "DEFAULT_KNOWN_ERROR_DIR", tmp_path)
    wiring = mcp_service._default_playbook_wiring(make_settings(), lambda: None)
    assert wiring is not None and wiring.rejected_files == ("bad.yaml",)

    caller = FakeCaller(_load("latency_sql"))
    d = build(make_settings(), caller)
    clean = run_job(d, alarm_of("latency_sql"))
    assert not any("설정 파일 거부" in n for n in clean.briefing["playbook"]["notes"])
    d = build(make_settings(), caller)
    assert d._playbook is not None
    d._playbook = disp_mod.PlaybookWiring(playbooks=d._playbook.playbooks, polestar=caller,
                                          apm=caller, rejected_files=("bad.yaml", "x.yaml"))
    job = run_job(d, alarm_of("latency_sql"))
    note = "설정 파일 거부 2건(bad.yaml, x.yaml) — 관리자 확인 필요"
    assert note in job.briefing["playbook"]["notes"]


def test_fixed_stopped_collector_starts_no_more_steps(monkeypatch) -> None:  # noqa: ANN001
    """[교정 · 지적 8] 닫힌 캐시 호출은 스텝 사유로 남기고 종료 신호를 세운다(조용히 삼키지 않음) —
    신호가 선 뒤의 스텝은 시작하지 않는다."""
    calls: list[str] = []

    def closed(ctx):  # noqa: ANN001, ANN202
        calls.append("closed")
        raise CacheClosedError("도구 결과 캐시가 닫혔다(잡 종료)")

    def ok(ctx):  # noqa: ANN001, ANN202
        calls.append("ok")
        return StepResult(status="ok")

    monkeypatch.setattr(disp_mod, "STEP_LIBRARY", {"t.closed": closed, "t.ok": ok})
    fx = _load("latency_sql")
    pb, matched_by = _playbook_for(fx)
    d = build(make_settings(), FakeCaller(fx))
    stop = threading.Event()
    lib = d._progress_library(make_job(alarm_of("latency_sql")), pb, matched_by, {}, stop)
    res = lib["t.closed"](SimpleNamespace(tools=[]))
    assert res.status == "missing" and "도구 캐시가 닫힘" in (res.reason or "")
    assert stop.is_set()
    res2 = lib["t.ok"](SimpleNamespace(tools=[]))
    assert res2.status == "missing" and res2.reason == disp_mod.STEP_STOPPED_REASON
    assert calls == ["closed"]
