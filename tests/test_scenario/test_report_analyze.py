"""리포트/분석기 수용 기준 V8·V9·V10·V18 (plans/94 §10)."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.scenario import utf8_open
from scripts.scenario.analyze import analyze
from scripts.scenario.report import (
    P95_MIN_SAMPLE,
    build_summary,
    classify_failure,
    render_markdown,
    scenario_verdicts,
    write_report,
)


def _row(**kwargs) -> dict:
    base = {
        "run_id": "r", "profile": "baseline", "env": "sandbox", "mode": "mock",
        "repeat": 0, "group": "T", "scenario_id": "T-01", "turn": 1, "plans": [94],
        "kind": "normal", "pair_id": None, "func_verdict": "pass", "perf_verdict": "pass",
        "response_mode": "answer", "forbidden_mode": None, "failed_assertions": [],
        "manual_notes": [], "wall_ms": 100.0, "processing_time_ms": 100.0,
        "node_elapsed_ms": {}, "node_path": [], "sse_events": [], "executed_sql": "SELECT 1",
        "row_count": 5, "artifacts": [], "error": None,
    }
    base.update(kwargs)
    return base


def _make_run(tmp_path: Path, rows: list[dict], meta: dict | None = None) -> Path:
    run_dir = tmp_path / "20260911-000000"
    run_dir.mkdir(parents=True)
    with utf8_open(run_dir / "raw.jsonl", "w") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    payload = {
        "meta": {"run_id": run_dir.name, "env": "sandbox", "mode": "mock", "repeat": 1,
                 "provider": "mock", **(meta or {})},
        "profiles": [{"name": "baseline", "port": 1, "valid": True, "tier": "mock",
                      "echo_ok": None, "reasons": []}],
        "skipped": [],
    }
    with utf8_open(run_dir / "run.json", "w") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    return run_dir


def test_V8_표본이_부족하면_p95를_만들지_않는다(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path, [_row(scenario_id=f"T-{i:02d}") for i in range(5)])
    summary = build_summary(run_dir, None)
    latency = summary["groups"]["T"]["latency"]
    assert latency["p95"] is None
    assert "표본 부족" in latency["note"]
    assert "표본 부족" in render_markdown(summary, run_dir, None)


def test_V8_표본이_충분하면_p95가_나온다(tmp_path: Path) -> None:
    rows = [_row(scenario_id=f"T-{i:02d}", processing_time_ms=100.0 + i)
            for i in range(P95_MIN_SAMPLE)]
    summary = build_summary(_make_run(tmp_path, rows), None)
    assert summary["groups"]["T"]["latency"]["p95"] is not None


def test_섹션_순서가_고정이다(tmp_path: Path) -> None:
    """순서가 흔들리면 사람이 매번 리포트 구조를 다시 익혀야 한다(§5.2)."""
    run_dir = _make_run(tmp_path, [_row()])
    markdown = render_markdown(build_summary(run_dir, None), run_dir, None)
    expected = [
        "## 1. 실행 요약",
        "## 2. 기능 판정 요약",
        "## 3. 성능 목표 대조",
        "## 4. 계획서 커버리지",
        "## 5. 오용·실수·착각 대응(R군)",
        "## 6. 불합격 상세",
        "## 7. 노드별 지연 분해",
        "## 8. 직전 run 대비 회귀",
        "## 9. 수동 검토 목록",
        "## 10. 제외·무효 목록",
        "## 11. 재현 명령",
    ]
    positions = []
    for heading in expected:
        assert heading in markdown, f"{heading} 이 없다"
        positions.append(markdown.index(heading))
    assert positions == sorted(positions), "섹션 순서가 어긋났다"


def test_제외_목록이_비면_그_사실을_적는다(tmp_path: Path) -> None:
    """빈 칸을 남기지 않는다 - 비었는지 안 본 건지 구별되어야 한다."""
    run_dir = _make_run(tmp_path, [_row()])
    markdown = render_markdown(build_summary(run_dir, None), run_dir, None)
    assert "제외 0건" in markdown


def test_재현_명령이_run_id_를_담는다(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path, [_row()])
    markdown = render_markdown(build_summary(run_dir, None), run_dir, None)
    assert f"--report {run_dir.name}" in markdown
    assert f"--analyze {run_dir.name}" in markdown


def test_V9_수동_검토_목록은_비지_않는다(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path, [_row(func_verdict="manual", manual_notes=["눈으로 볼 것"])])
    markdown = render_markdown(build_summary(run_dir, None), run_dir, None)
    assert "눈으로 볼 것" in markdown
    assert "수동 검토 항목 없음" not in markdown


def test_반복_간_결과가_갈리면_불안정이다(tmp_path: Path) -> None:
    rows = [
        _row(repeat=0, func_verdict="pass"),
        _row(repeat=1, func_verdict="fail"),
        _row(repeat=2, func_verdict="pass"),
    ]
    verdicts = scenario_verdicts(rows)
    assert verdicts["T-01"]["verdict"] == "flaky"


def test_불안정은_합격으로도_불합격으로도_세지_않는다(tmp_path: Path) -> None:
    rows = [_row(repeat=0, func_verdict="pass"), _row(repeat=1, func_verdict="fail")]
    summary = build_summary(_make_run(tmp_path, rows), None)
    group = summary["groups"]["T"]
    assert group["flaky"] == 1
    assert group["pass"] == 0 and group["fail"] == 0


def test_조용한_오답은_리포트_맨_위로_승격된다(tmp_path: Path) -> None:
    rows = [_row(func_verdict="fail", forbidden_mode="silent_wrong",
                 failed_assertions=[{"key": "column_must_not_map", "expected": "cpu_usage",
                                     "actual": "실행 SQL 에 존재"}])]
    run_dir = _make_run(tmp_path, rows)
    markdown = render_markdown(build_summary(run_dir, None), run_dir, None)
    head = markdown.split("## 1.")[0]
    assert "silent_wrong" in head and "최우선" in head


def test_대조군_동반_실패를_쌍으로_잡는다(tmp_path: Path) -> None:
    rows = [
        _row(scenario_id="R4-01", group="R4", kind="misconception", pair_id="R4-01C",
             func_verdict="fail"),
        _row(scenario_id="R4-01C", group="R4", kind="control", pair_id="R4-01",
             func_verdict="fail"),
    ]
    summary = build_summary(_make_run(tmp_path, rows), None)
    assert summary["misuse"]["R4"]["control_broken"] == 1


def test_실패_분류는_위에서부터_먼저_맞는_것을_적용한다() -> None:
    assert classify_failure(_row(failed_assertions=[{"key": "db_ids"}])) == "routing"
    assert classify_failure(_row(failed_assertions=[{"key": "sql_must_not_match"}])) == "guard"
    assert classify_failure(
        _row(row_count=0, failed_assertions=[{"key": "row_count.min"}])) == "empty_result"
    assert classify_failure(_row(failed_assertions=[{"key": "알수없는키"}])) == "unclassified"


def test_V18_반복이_부족하면_처방을_제안하지_않는다(tmp_path: Path) -> None:
    rows = [_row(group="R4", kind="misconception", func_verdict="fail",
                 forbidden_mode="silent_wrong",
                 failed_assertions=[{"key": "column_must_not_map",
                                     "expected": "cpu_usage", "actual": "SQL"}])]
    run_dir = _make_run(tmp_path, rows, meta={"repeat": 1})
    write_report(run_dir, None)
    analyze(run_dir, None)
    text = (run_dir / "countermeasures.md").read_text(encoding="utf-8")
    assert "불안정·보류" in text
    assert "보류 (반복 부족)" in text


def test_V18_반복이_충분하면_처방_축이_나온다(tmp_path: Path) -> None:
    rows = [_row(repeat=i, group="R4", kind="misconception", func_verdict="fail",
                 forbidden_mode="silent_wrong",
                 failed_assertions=[{"key": "column_must_not_map",
                                     "expected": "cpu_usage", "actual": "SQL"}])
            for i in range(3)]
    run_dir = _make_run(tmp_path, rows, meta={"repeat": 3})
    write_report(run_dir, None)
    analyze(run_dir, None)
    text = (run_dir / "countermeasures.md").read_text(encoding="utf-8")
    assert "결정적 금지 매핑" in text
    assert "보류 (반복 부족)" not in text


def test_V10_분석기는_저장소_파일을_수정하지_않는다(tmp_path: Path) -> None:
    """제안까지만 하고 적용은 사람이 한다 - 어떤 파일도 고치지 않는다."""
    watched = [
        Path("src"), Path("plans"), Path("docs/17_future_improvements.md"),
        Path("config/scenarios/profiles.yaml"), Path("testdata/scenarios"),
    ]
    before = {p: p.stat().st_mtime_ns for p in watched if p.exists()}
    run_dir = _make_run(tmp_path, [_row()])
    write_report(run_dir, None)
    written = analyze(run_dir, None)
    after = {p: p.stat().st_mtime_ns for p in watched if p.exists()}
    assert before == after
    assert all(path.parent == run_dir for path in written)
    assert len(written) == 6
