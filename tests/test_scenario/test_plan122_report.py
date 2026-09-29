"""plans/122 트랙 J·C 리포트.

헤드라인 교정(J-1) · 수동 사유 분류(J-3) · 환경 보류 성능 표본 분리(C-4a) · 관측 절(K-1) ·
판정 계약 비교 거부(J-1 ⑤ · G-17).

전부 합성 행으로 고정한다(LLM 0 · 서버 0). 폐쇄망 run 재현 수치(2단 67.2% 41/61 · 커버리지 58.7%)는
`raw.jsonl` 이 저장소 밖(`results/`)이라 여기서 단언하지 않는다 — 같은 정의를 합성 행으로 못 박는다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.bench.compare import _scored
from scripts.bench.sweep import Observation as BenchObservation
from scripts.scenario import analyze, utf8_open
from scripts.scenario.catalog import Catalog, Group, Scenario, Turn, judgement_digest
from scripts.scenario.report import (
    JUDGEMENT_REPORT_VERSION,
    arm_breakdown,
    build_summary,
    contract_mismatch,
    judged_counts,
    judgement_ceiling,
    note_sources,
    perf_rows,
    render_markdown,
    row_env_held,
    row_manual_sources,
    row_unevaluated,
    unevaluated_summary,
)

ENV_NOTE = ("환경 불일치 - 시나리오 env=sandbox, 실행 env=closed. "
            "데이터 의존 단언 1종 보류(row_count)")


def _row(**kw: Any) -> dict[str, Any]:
    base = {
        "run_id": "r", "profile": "baseline", "arm": None, "env": "closed", "mode": "run",
        "repeat": 0, "group": "A", "scenario_id": "A-01", "turn": 1, "plans": [122],
        "kind": "normal", "pair_id": None, "func_verdict": "pass", "perf_verdict": "pass",
        "response_mode": "answer", "forbidden_mode": None, "failed_assertions": [],
        "manual_notes": [], "wall_ms": 100.0, "processing_time_ms": 100.0,
        "node_elapsed_ms": {}, "node_path": [], "sse_events": [], "executed_sql": None,
        "row_count": 1, "artifacts": [], "error": None, "unevaluated_reason": None,
    }
    base.update(kw)
    return base


def _run_dir(tmp_path: Path, rows: list[dict], profiles: list[dict] | None = None,
             meta: dict | None = None, name: str = "20260929-000000") -> Path:
    run_dir = tmp_path / name
    run_dir.mkdir(parents=True)
    with utf8_open(run_dir / "raw.jsonl", "w") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    payload = {
        "meta": {"run_id": name, "env": "closed", "mode": "run", "repeat": 1, **(meta or {})},
        "profiles": profiles or [{"name": "baseline", "tier": "intent_orchestration",
                                  "valid": True, "reasons": []}],
        "skipped": [],
    }
    with utf8_open(run_dir / "run.json", "w") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    return run_dir


# --- J-1 ①② 정의 -------------------------------------------------------------------------

def _mixed_rows() -> list[dict[str, Any]]:
    return [
        _row(scenario_id="A-01", func_verdict="pass"),
        _row(scenario_id="A-02", func_verdict="pass"),
        _row(scenario_id="A-03", func_verdict="fail"),
        _row(scenario_id="A-04", func_verdict="error"),
        _row(scenario_id="A-05", func_verdict="manual", manual_notes=["눈으로 볼 것"]),
        _row(scenario_id="A-06", func_verdict="manual", manual_notes=["눈으로 볼 것"]),
        # D-241 제거 사유 - 분자·분모 모두 아니다.
        _row(scenario_id="A-07", func_verdict="error", error="처리 시간이 초과되었습니다",
             unevaluated_reason="timeout"),
    ]


def test_j1_func_pass_rate_excludes_manual_and_removed() -> None:
    counts = judged_counts(_mixed_rows())
    assert counts["scored"] == 6                      # timeout 1턴 제거
    assert (counts["pass"], counts["fail"], counts["error"], counts["manual"]) == (2, 1, 1, 2)
    assert counts["judged"] == 4
    assert counts["func_pass_rate"] == 0.5            # 2 / (2 + 1 + 1)
    assert counts["coverage"] == round(4 / 6, 4)      # (2 + 1 + 1) / 6


def test_j1_same_definition_as_bench_compare_scored() -> None:
    """같은 턴을 벤치 관측치로 옮겨 `_scored` 로 잰 합격률과 일치한다(D-241 ③ · G-1)."""
    rows = _mixed_rows()
    observations = [
        BenchObservation(arm_id="a", scenario_id=r["scenario_id"], repeat=0,
                         passed=r["func_verdict"] == "pass", wall_ms=None, llm_calls=None,
                         tokens=None, retries=None, manual=r["func_verdict"] == "manual",
                         unevaluated=row_unevaluated(r))
        for r in rows
    ]
    pool = _scored(observations)
    assert judged_counts(rows)["func_pass_rate"] == round(
        sum(o.passed for o in pool) / len(pool), 4)


def test_j1_no_ratio_when_denominator_zero() -> None:
    counts = judged_counts([_row(func_verdict="manual")])
    assert counts["func_pass_rate"] is None
    assert counts["coverage"] == 0.0


def test_j1_legacy_headline_value_unchanged() -> None:
    """종전 헤드라인(`pass / 판정 분모`)은 참고 줄로 **종전과 같은 값**을 보인다."""
    summary = unevaluated_summary(_mixed_rows())
    assert summary["pass_rate"] == round(2 / 6, 4)


# --- J-1 ③ arm별 · 기준 arm 첫 줄 ------------------------------------------------------------

def _arm_run() -> tuple[list[dict], dict]:
    profiles = [
        {"name": "baseline+tier3", "arm": "tier3", "base_profile": "baseline",
         "tier": "semantic_router"},
        {"name": "baseline", "arm": "baseline", "base_profile": "baseline",
         "tier": "intent_orchestration"},
        {"name": "optin+baseline", "arm": "baseline", "base_profile": "optin",
         "tier": "intent_orchestration"},
    ]
    rows = [
        _row(profile="baseline+tier3", arm="tier3", func_verdict="fail"),
        _row(profile="baseline", arm="baseline", func_verdict="pass"),
        _row(profile="optin+baseline", arm="baseline", scenario_id="D-01", func_verdict="fail"),
        _row(profile="baseline", arm="baseline", scenario_id="A-02", func_verdict="manual"),
    ]
    return rows, {"profiles": profiles}


def test_j1_reference_arm_first_regardless_of_run_order() -> None:
    rows, run = _arm_run()
    cells = arm_breakdown(rows, run)
    assert [c["arm"] for c in cells] == ["baseline", "tier3"]
    assert cells[0]["reference"] and not cells[1]["reference"]
    # 같은 arm 의 기저 프로파일 둘(baseline · optin)은 한 줄로 합친다.
    assert cells[0]["profiles"] == ["baseline", "optin+baseline"]
    assert (cells[0]["pass"], cells[0]["fail"], cells[0]["manual"]) == (1, 1, 1)


def test_j1_no_reference_arm_when_tier_unobserved() -> None:
    rows = [_row(func_verdict="pass")]
    cells = arm_breakdown(rows, {"profiles": [{"name": "baseline", "tier": "mock"}]})
    assert not any(cell["reference"] for cell in cells)


def test_j1_headline_renders_reference_first_and_mixed_reference_line(tmp_path: Path) -> None:
    rows, run = _arm_run()
    run_dir = _run_dir(tmp_path, rows, run["profiles"])
    markdown = render_markdown(build_summary(run_dir, None), run_dir, None)
    section = markdown.split("### 기능 합격률")[1].split("### 판정 분모")[0]
    lines = [line for line in section.splitlines() if line.startswith("| ")][1:]   # 머리줄 제외
    assert lines[0].startswith("| **baseline (기준)**")
    assert "**50.0% (1/2)**" in lines[0]
    assert lines[-1].startswith("| 참고: arm 혼합 합계")
    assert "참고(종전 헤드라인 · 판정 정의 v1)" in section
    # 종전 헤드라인 줄은 「판정 분모」 표에 같은 값으로 남는다(값 불변 · 이름만 참고로).
    assert "참고: 종전 헤드라인(판정 정의 v1) - 합격 / 판정 분모 | 25.0%" in markdown


# --- J-1 ④ 판정 상한 ------------------------------------------------------------------------

def _catalog() -> Catalog:
    group = Group(id="A", name="a", latency_target_ms=10000)
    return Catalog(
        groups={"A": group, "K": Group(id="K", name="k", latency_target_ms=10000)},
        scenarios=[
            Scenario(id="A-01", group="A", plans=[122], title="t",
                     turns=[Turn({"query": "q"}, {"status": "completed"})]),
            Scenario(id="A-02", group="A", plans=[122], title="t",
                     turns=[Turn({"query": "q"}, {"manual_review": "눈으로 볼 것"})]),
            Scenario(id="K-01", group="K", plans=[122], title="t",
                     turns=[Turn({"query": "q"}, {"manual_review": "묶음 메모"})],
                     replay={"scenarios": ["A-01"], "repeat": 2}),
            Scenario(id="K-02", group="K", plans=[122], title="t",
                     turns=[Turn({"query": "q"}, {})],
                     replay={"scenarios": ["A-01", "A-02"], "repeat": 1}),
        ],
        profiles={"baseline": {}},
    )


def test_j1_ceiling_is_ratio_of_executed_without_manual_review() -> None:
    rows = [_row(scenario_id=sid) for sid in ("A-01", "A-02", "K-01", "K-02", "Z-99")]
    rows.append(_row(scenario_id="P-01", kind="probe", func_verdict="manual"))
    ceiling = judgement_ceiling(rows, _catalog())
    # 묶음은 자기 메모가 아니라 참조 시나리오로 판단한다 - K-01(A-01만) 가능 · K-02(A-02 포함) 불가.
    assert ceiling["executed"] == 4 and ceiling["passable"] == 2
    assert ceiling["ratio"] == 0.5
    assert ceiling["blocked"] == ["A-02", "K-02"]
    assert ceiling["missing"] == ["Z-99"]              # 카탈로그에 없으면 분모에서 뺀다
    assert ceiling["catalog_digest"] == judgement_digest(_catalog())


def test_j1_ceiling_reason_without_catalog(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path, [_row()])
    summary = build_summary(run_dir, None)
    assert summary["judgement"]["ceiling"]["measured"] is False
    markdown = render_markdown(summary, run_dir, None)
    assert "판정 상한: 계산하지 않았다 - 카탈로그 없이 생성했다" in markdown


def test_j1_ceiling_line_with_catalog(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path, [_row(scenario_id="A-01"), _row(scenario_id="A-02")])
    markdown = render_markdown(build_summary(run_dir, _catalog()), run_dir, _catalog())
    assert ("**판정 상한: 실행 시나리오 2건 중 `manual_review` 없는 시나리오 1건(50.0%)**"
            in markdown)


# --- J-1 ⑤ 판정 계약 ------------------------------------------------------------------------

def test_j1_judgement_contract_in_summary_meta(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path, [_row()], meta={"catalog_digest": "abc123"})
    contract = build_summary(run_dir, None)["meta"]["judgement_contract"]
    # 판정기 지문(J-1 ⑤ 보강)은 run 메타에 있을 때만 실린다 - 이 합성 run 은 옛 형식이라 None.
    assert contract == {"report_version": JUDGEMENT_REPORT_VERSION, "catalog_digest": "abc123",
                        "judge_digest": None}
    assert JUDGEMENT_REPORT_VERSION == 2


def test_j1_legacy_run_has_no_catalog_digest(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path, [_row()])
    summary = build_summary(run_dir, None)
    assert summary["meta"]["judgement_contract"]["catalog_digest"] is None
    assert "카탈로그 지문 **미기록**" in render_markdown(summary, run_dir, None)


def _summary(digest: str | None, verdict: str = "pass") -> dict[str, Any]:
    meta: dict[str, Any] = {"env": "closed", "mode": "run", "repeat": 3}
    if digest:
        meta["judgement_contract"] = {"report_version": 2, "catalog_digest": digest}
    return {"meta": meta, "invalid": {"over_threshold": False},
            "profiles": [{"name": "baseline", "tier": "intent_orchestration"}],
            "scenario_verdicts": {"A-01": {"verdict": verdict}}}


def test_contract_digest_mismatch_refuses_comparison(tmp_path: Path, monkeypatch) -> None:
    runs = tmp_path / "scenario"
    for name in ("20260901-000000", "20260902-000000", "20260903-000000"):
        (runs / name).mkdir(parents=True)
    fake = {"20260901-000000": _summary("aaa"), "20260902-000000": _summary("bbb")}
    monkeypatch.setattr(analyze, "build_summary", lambda path, _cat: fake[path.name])
    text = analyze.regression(runs / "20260903-000000", _summary("aaa", "fail"))
    assert "직전 비교 대상: `20260901-000000`" in text
    assert "`20260902-000000`(판정 계약이 다르다(카탈로그 지문 bbb → aaa)" in text
    assert "J-4 재판정" in text
    assert "판정 계약 미기록" not in text


def test_contract_unrecorded_compares_with_note(tmp_path: Path, monkeypatch) -> None:
    runs = tmp_path / "scenario"
    for name in ("20260901-000000", "20260902-000000"):
        (runs / name).mkdir(parents=True)
    monkeypatch.setattr(analyze, "build_summary", lambda path, _cat: _summary(None))
    text = analyze.regression(runs / "20260902-000000", _summary("aaa"))
    assert "직전 비교 대상: `20260901-000000`" in text
    assert "판정 계약 미기록 - 카탈로그 동일성 미확인" in text
    assert contract_mismatch(_summary(None), _summary("aaa")) is None


def test_report_section8_skips_other_contract(tmp_path: Path) -> None:
    older = _run_dir(tmp_path, [_row(func_verdict="fail")], meta={"catalog_digest": "old"},
                     name="20260901-000000")
    assert older.exists()
    now = _run_dir(tmp_path, [_row()], meta={"catalog_digest": "new"}, name="20260902-000000")
    markdown = render_markdown(build_summary(now, None), now, None)
    section = markdown.split("## 8.")[1].split("## 9.")[0]
    assert "판정 계약이 다르다(카탈로그 지문 old → new)" in section
    assert "직전 비교 대상" not in section


# --- K-1 관측 절 ----------------------------------------------------------------------------

def test_k1_probe_turns_excluded_and_listed(tmp_path: Path) -> None:
    rows = [_row(func_verdict="pass"),
            _row(scenario_id="A-09", kind="probe", func_verdict="manual",
                 manual_notes=["저신뢰 프로브 - 기록만 한다"]),
            _row(scenario_id="A-10", kind="probe", func_verdict="fail")]
    counts = judged_counts(rows)
    assert counts["scored"] == 1 and counts["judged"] == 1 and counts["probe"] == 2
    run_dir = _run_dir(tmp_path, rows)
    markdown = render_markdown(build_summary(run_dir, None), run_dir, None)
    section = markdown.split("### 관측(probe)")[1].split("### 시나리오별 판정")[0]
    assert "| A-09 | 1 |" in section and "저신뢰 프로브" in section
    assert "| A-10 | 1 |" in section
    assert "관측(probe) 턴 2건은 위 표의 분모에서 뺐다" in markdown


def test_k1_no_probe_says_zero(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path, [_row()])
    assert "관측 턴 0건." in render_markdown(build_summary(run_dir, None), run_dir, None)


# --- J-3 수동 사유 출처 ----------------------------------------------------------------------

def test_j3_row_field_is_primary_source() -> None:
    row = _row(func_verdict="manual", manual_sources=["catalog", "fanout"],
               manual_notes=["아무 문구"])
    assert row_manual_sources(row) == ["catalog", "fanout"]
    assert row_manual_sources(_row(manual_source="policy")) == ["policy"]


@pytest.mark.parametrize(("note", "expected"), [
    ("행 수가 전체 서버 수와 일치하는지", ("catalog",)),
    (ENV_NOTE, ("env_mismatch",)),
    (f"원문 / {ENV_NOTE}", ("catalog", "env_mismatch")),
    ("대응 등급 'answer' 가 선언 ['guide'] 밖이다 (등급 정책 미확정 - 1차는 관측으로만 쓴다)",
     ("policy",)),
    ("row_count 는 단일 DB 턴 전용이다 - 이 턴은 3개 DB 팬아웃이라 합계(1)와", ("fanout",)),
    ("sql_must_match: 역질문으로 끝난 턴이라 SQL 이 없다(판정 보류)", ("unobservable",)),
    ("retries.max=3 예산을 확인하지 못했다 (query_generator 가 …)", ("unobservable",)),
    ("result 를 확인하지 못했다 - 결과 행 미수집(러너가 download-csv 를 받지 않았다)",
     ("unobservable",)),
    ("H-01-0-1-result.docx: xlsx 가 아니라 자동 칼럼 검증 대상이 아니다", ("unobservable",)),
])
def test_j3_legacy_note_sources(note: str, expected: tuple[str, ...]) -> None:
    assert note_sources(note) == expected


def test_j3_legacy_env_mismatch_field_is_source() -> None:
    row = _row(func_verdict="error", env_mismatch={"scenario_env": "sandbox", "run_env": "closed"})
    assert row_manual_sources(row) == ["env_mismatch"]
    assert row_env_held(row)


def test_j3_taxonomy_per_arm_with_bundle_inheritance(tmp_path: Path) -> None:
    rows = [
        _row(scenario_id="A-01", func_verdict="manual", manual_notes=["눈으로 볼 것"]),
        _row(scenario_id="A-02", func_verdict="manual",
             manual_notes=["눈으로 볼 것", "row_count 는 단일 DB 턴 전용이다 - 2개 DB"]),
        _row(scenario_id="K-01", group="K", func_verdict="manual", replay_of="A-01",
             manual_notes=["눈으로 볼 것"]),
        _row(scenario_id="L-01", group="L", func_verdict="manual",
             manual_notes=[f"원문 / {ENV_NOTE}"], env_mismatch={"scenario_env": "sandbox"}),
        _row(scenario_id="A-03", func_verdict="manual", unevaluated_reason="timeout"),
    ]
    counts = analyze.manual_taxonomy_counts(rows)
    assert counts["turns"] == 4                          # D-241 제거 턴은 세지 않는다
    assert counts["by_source"] == {"catalog": 4, "fanout": 1, "bundle": 1, "env_mismatch": 1}
    assert counts["single"] == {"catalog": 1}
    assert counts["combos"] == {"catalog + fanout": 1, "catalog + bundle": 1,
                                "env_mismatch + catalog": 1}
    text = analyze.manual_taxonomy({"profiles": [{"name": "baseline",
                                                  "tier": "intent_orchestration"}]}, rows)
    assert "## (arm 없음) (기준) - 수동 4턴" in text
    assert ("§2.3 형식: 카탈로그 단일 출처 1 · 정책 0 · 환경 불일치 1 · K 묶음 상속 1 · "
            "팬아웃 1") in text


def test_j3_analyzer_writes_manual_taxonomy(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path, [_row(func_verdict="manual", manual_notes=["눈으로 볼 것"])])
    written = analyze.analyze(run_dir, None)
    assert run_dir / "manual_taxonomy.md" in written
    assert "카탈로그 `manual_review` | 1 | 1" in (run_dir / "manual_taxonomy.md").read_text(
        encoding="utf-8")


# --- C-4a 환경 보류 행을 성능 표본에서 뺀다 ---------------------------------------------------

def _perf_rows() -> list[dict[str, Any]]:
    return [
        _row(scenario_id="A-01", processing_time_ms=100.0, perf_verdict="pass"),
        _row(scenario_id="A-02", processing_time_ms=300.0, perf_verdict="fail"),
        _row(scenario_id="L-01", group="L", func_verdict="manual", processing_time_ms=9000.0,
             perf_verdict="fail", manual_notes=[f"원문 / {ENV_NOTE}"],
             env_mismatch={"scenario_env": "sandbox", "run_env": "closed"},
             node_elapsed_ms={"input_parser": 9000.0}),
        _row(scenario_id="L-02", group="L", func_verdict="pass", processing_time_ms=200.0,
             perf_verdict="pass"),
    ]


def test_c4a_held_rows_leave_latency_sample_others_unchanged(tmp_path: Path) -> None:
    rows = _perf_rows()
    assert [r["scenario_id"] for r in perf_rows(rows)] == ["A-01", "A-02", "L-02"]
    summary = build_summary(_run_dir(tmp_path, rows), None)
    assert summary["groups"]["L"]["latency"]["n"] == 1
    assert summary["groups"]["L"]["latency"]["max"] == 200.0
    assert (summary["groups"]["L"]["perf_pass"], summary["groups"]["L"]["perf_fail"]) == (1, 0)
    # 환경 일치 군은 보류 행이 없던 run 과 같은 값이다.
    plain = build_summary(_run_dir(tmp_path / "plain", rows[:2] + rows[3:]), None)
    assert summary["groups"]["A"]["latency"] == plain["groups"]["A"]["latency"]
    assert summary["by_profile"][0]["latency"] == plain["by_profile"][0]["latency"]
    assert summary["env_hold"]["count"] == 1
    assert summary["env_hold"]["by_group"] == {"L": 1}


def test_c4a_held_count_heads_perf_table(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path, _perf_rows())
    markdown = render_markdown(build_summary(run_dir, None), run_dir, None)
    section3 = markdown.split("## 3.")[1].split("## 4.")[0]
    assert "**환경 보류 턴 1건**" in section3
    section7 = markdown.split("## 7.")[1].split("## 8.")[0]
    assert "input_parser" not in section7                # 보류 행의 노드 지연도 뺐다


def test_c4a_zero_held_is_stated_with_legacy_caveat(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path, [_row()])
    markdown = render_markdown(build_summary(run_dir, None), run_dir, None)
    assert "**환경 보류 턴 0건**" in markdown
    assert "D-216 이전 run 이면 안 센 것이다" in markdown
    new_run = _run_dir(tmp_path / "new", [_row(manual_sources=[])])
    new_md = render_markdown(build_summary(new_run, None), new_run, None)
    assert "**환경 보류 턴 0건**" in new_md and "안 센 것이다" not in new_md


def test_c4a_bottleneck_excludes_held_rows(tmp_path: Path) -> None:
    rows = [_row(scenario_id=f"A-{i:02d}", node_elapsed_ms={"output_generator": 10.0})
            for i in range(5)]
    rows.append(_row(scenario_id="L-01", env_mismatch={"scenario_env": "sandbox"},
                     node_elapsed_ms={"output_generator": 99999.0}))
    body = analyze.bottleneck(tmp_path, rows)
    assert "환경 보류 턴 1건" in body
    assert "| output_generator | 5 | 10.0 | 50.0 |" in body


# --- J-1 ⑤ 보강: 판정기 지문(123 교차 검토 122-③) ------------------------------------

def _summary_with_judge(catalog_digest: str | None, judge: str | None) -> dict:
    summary = _summary(catalog_digest)
    contract = dict(summary["meta"].get("judgement_contract") or {"report_version": 2,
                                                                  "catalog_digest": catalog_digest})
    contract["judge_digest"] = judge
    summary["meta"]["judgement_contract"] = contract
    return summary


def test_judge_digest_is_stable_and_tracks_sources() -> None:
    """판정기 지문은 판정 소스 바이트에서 나온다 — 두 번 불러도 같고 16자다."""
    from scripts.scenario.assertions import JUDGE_SOURCES, judge_digest

    assert judge_digest() == judge_digest()
    assert len(judge_digest()) == 16
    # plans/123 V-4 - 불변식 판정기도 판정 소스다(바뀌면 판정이 바뀐다)
    assert set(JUDGE_SOURCES) == {"assertions.py", "oracle.py", "invariants.py"}


def test_judge_change_note_warns_but_does_not_refuse() -> None:
    """판정기 지문이 다르면 주의 줄만 — 카탈로그 지문이 같으면 비교는 거부하지 않는다."""
    from scripts.scenario.report import judge_change_note

    prev, cur = _summary_with_judge("aaa", "j1"), _summary_with_judge("aaa", "j2")
    note = judge_change_note(prev, cur)
    assert note and "판정기 코드가 다르다" in note and "j1 → j2" in note
    assert contract_mismatch(prev, cur) is None
    assert judge_change_note(prev, _summary_with_judge("aaa", "j1")) is None
    assert judge_change_note(_summary_with_judge("aaa", None), cur) is None


def test_run_meta_records_judge_digest() -> None:
    """러너 run 메타의 판정 계약에 판정기 지문이 실린다(리포트 요약이 그대로 옮긴다)."""
    from scripts.scenario.assertions import judge_digest
    from scripts.scenario.catalog import load_catalog
    from scripts.scenario.report import judgement_contract
    from scripts.scenario.runner import RunConfig, run_meta

    meta = run_meta(RunConfig(mode="mock"), load_catalog())
    assert meta["judgement_contract"]["judge_digest"] == judge_digest()
    assert judgement_contract(meta)["judge_digest"] == judge_digest()
