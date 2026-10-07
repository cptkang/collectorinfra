"""scripts/rag_hyde_repeat.py — 반복 호출 진단의 판정·루트 찾기·실행 흐름(실 검색 0 · LLM 0)."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from src.clients.fabrix_retrieval import DocHit, RetrievalOutcome
from src.config import AppConfig, DBHubConfig, LLMConfig, RagConfig

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("rag_hyde_repeat", ROOT / "scripts" / "rag_hyde_repeat.py")
rhr = importlib.util.module_from_spec(_spec)
sys.modules["rag_hyde_repeat"] = rhr   # dataclass가 모듈을 찾는다
_spec.loader.exec_module(rhr)


def _run(hits: int, hyde: str = "", docs: tuple[str, ...] = (), status: str | None = None):
    return rhr.Run(status=status or ("ok" if hits else "empty"), hits=hits,
                   score_min=0.5 if hits else None, score_max=0.9 if hits else None,
                   elapsed_ms=100, hyde=hyde, doc_keys=docs)


# ── 판정 ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("runs", "code"), [
    ([_run(3, "h", ("d1",)), _run(3, "h", ("d1",))], "S0"),
    ([_run(3, "h", ("d1",)), _run(3, "h2", ("d2",))], "S1"),
    ([_run(3, "h", ("d1",)), _run(0, "h2")], "P1"),
    ([_run(3, "h", ("d1",)), _run(0, "")], "P2"),
    ([_run(0, "h"), _run(0, "h2")], "Z0"),
    ([_run(0, ""), _run(0, "")], "Z1"),
    ([_run(3, "h", ("d1",)), _run(0, "", status="timeout")], "E"),
])
def test_case_verdicts(runs, code) -> None:
    assert rhr.summarize("A1", "hq_manual", runs).verdict == code


def test_labels_count_distinct_hyde_and_doc_sets() -> None:
    s = rhr.summarize("A1", "hq_manual", [
        _run(3, "가상 답변 1", ("d1", "d2")), _run(3, "가상  답변 1", ("d1", "d2")),
        _run(0, ""), _run(3, "가상 답변 2", ("d3",)),
    ])
    assert s.hyde_labels == ["A", "A", "-", "B"]   # 공백 차이는 같은 문장
    assert s.doc_labels == ["1", "1", "-", "2"]


def test_overall_codes() -> None:
    p = rhr.summarize("A1", "hq", [_run(3, "a", ("d",)), _run(0, "b")])
    s = rhr.summarize("A2", "hq", [_run(3, "a", ("d",))])
    z = rhr.summarize("A3", "hq", [_run(0, "a")])
    e = rhr.summarize("A4", "hq", [_run(0, status="error")])
    assert rhr.overall([p, s])[0] == "H1"
    assert "HyDE 문장도 호출마다 달라졌습니다" in rhr.overall([p])[1]
    assert rhr.overall([s])[0] == "H0"
    assert rhr.overall([s, z])[0] == "H3"
    assert rhr.overall([p, e])[0] == "E"


# ── 루트 찾기 ─────────────────────────────────────────────────────────────────

def test_resolve_root_priority(tmp_path) -> None:
    fake = tmp_path / "agent"
    (fake / "src").mkdir(parents=True)
    (fake / "src" / "config.py").write_text("")
    (fake / "config").mkdir()
    elsewhere = tmp_path / "temp" / "rag_hyde_repeat.py"   # /fsapp/temp 로 복사한 경우
    assert rhr.resolve_root(str(fake), script_path=elsewhere, env={}) == fake.resolve()
    assert rhr.resolve_root(None, script_path=elsewhere,
                            env={"INFRA_AGENT_ROOT": str(fake)}) == fake.resolve()
    in_repo = ROOT / "scripts" / "rag_hyde_repeat.py"
    assert rhr.resolve_root(None, script_path=in_repo, env={}) == ROOT.resolve()


def test_resolve_root_fails_with_hint(tmp_path) -> None:
    with pytest.raises(SystemExit, match="--root"):
        rhr.resolve_root(str(tmp_path), script_path=tmp_path / "x.py", env={})


# ── 실행 흐름(검색 대역) ──────────────────────────────────────────────────────

def _config() -> AppConfig:
    rag = RagConfig(
        _env_file=None, enabled=True, chat_routing_enabled=True,
        hq_manual_endpoint="http://x/hq", hq_manual_token="t", hq_manual_client_key="k",
        hq_manual_retrieval_id="20260929000000_hq01",
        arch_docs_endpoint="http://x/ar", arch_docs_token="t", arch_docs_client_key="k",
        arch_docs_retrieval_id="20260929000000_ar01",
    )
    return AppConfig(_env_file=None, llm=LLMConfig(provider="ollama", model="x"),
                     dbhub=DBHubConfig(server_url="http://x/sse", source_name="i"), rag=rag,
                     checkpoint_backend="sqlite", checkpoint_db_url=":memory:")


def test_main_runs_cases_like_chat_and_writes_detail(monkeypatch, tmp_path, capsys) -> None:
    import src.clients.fabrix_retrieval as fr
    import src.config as cfg_mod

    calls: list[tuple[str, str]] = []

    async def fake_retrieve(col, query, **kwargs):
        calls.append((col.id, query))
        n = sum(1 for c in calls if c == (col.id, query))
        if col.id == "hq_manual" and query == "서버 관리자의 역할" and n % 2 == 0:
            return RetrievalOutcome(col.id, "empty", hyde_query="가상 답변 2", raw={})
        hit = DocHit(collection_id=col.id, doc_id=f"{col.id}-1", title="문서", filename="",
                     subtitle="절", content="본문", rank=1, rank_score=0.9, url="",
                     content_type="", catalog_id="", result_id="")
        return RetrievalOutcome(col.id, "ok", hits=[hit], hyde_query="가상 답변 1", raw={})

    monkeypatch.setattr(fr, "retrieve_one", fake_retrieve)
    monkeypatch.setattr(cfg_mod, "load_config", _config)
    args = rhr.build_parser().parse_args(
        ["-n", "2", "--interval", "0", "--only", "A1,A3,B1", "--out-dir", str(tmp_path)])
    code = asyncio.run(rhr.main_async(args, ROOT))
    out = capsys.readouterr().out

    assert code == 0
    # 채팅과 같은 해석: A1 → 본부 매뉴얼 1개, B1 → 두 문서군, 질의는 명령 꼬리 제거
    assert ("hq_manual", "서버 관리자의 역할") in calls and ("arch_docs", "서버 관리자의 역할") in calls
    assert ("hq_manual", "인프라변경관리") in calls
    assert "▶ 요약 A1/hq_manual: 0건 1/2 · HyDE 2종(없음 0) · 문서묶음 1종 · 판정 P1" in out
    assert "■ 종합 판정 H1" in out
    assert "t" not in [line.strip() for line in out.splitlines()]   # 토큰 미출력(형식 확인)
    [detail] = list(tmp_path.glob("rag_hyde_repeat_*.json"))
    payload = json.loads(detail.read_text(encoding="utf-8"))
    assert payload["overall"]["code"] == "H1"
    assert payload["cases"][0]["runs"][1]["hyde"] == "가상 답변 2"


def test_query_requires_collection(capsys) -> None:
    assert rhr.main(["-q", "인프라변경관리"]) == 6


# ── plans/141 W4 — 적중 판정 · 집계 · 채택 판정 · 기준 질의 모드 · 비교 ──────────

def test_is_hit_ignores_spacing_and_case() -> None:
    assert rhr.is_hit(["인프라변경관리 지침"], ["인프라 변경관리"]) is True
    assert rhr.is_hit(["TIMEOUT설정지침.pdf"], ["TIME OUT 설정 지침"]) is True
    assert rhr.is_hit(["서버시스템구성지침"], ["백업운영"]) is False
    assert rhr.is_hit(["무엇이든"], []) is None


def _case(label, col, runs, tail="hq01"):
    return {"label": label, "collection": col, "retrieval_id_tail": tail, "runs": [
        {"hits": h, "hit": hit, "elapsed_ms": ms, "doc_label": "1" if h else "-",
         "retried": rt, "retry_hits": rh, "retry_hit": rhit}
        for h, hit, ms, rt, rh, rhit in runs]}


def test_collection_metrics_counts_retry_and_hits() -> None:
    m = rhr.collection_metrics([
        _case("H01", "hq_manual", [(3, True, 100, False, None, None), (0, False, 300, True, 3, True)]),
        _case("H02", "hq_manual", [(0, False, 200, True, 0, False), (3, True, 100, False, None, None)]),
    ])["hq_manual"]
    assert m["runs"] == 4 and m["empty_rate"] == 0.5 and m["empty_after_retry_rate"] == 0.25
    assert m["hit_rate"] == 0.5 and m["hit_after_retry_rate"] == 0.75
    assert m["median_ms"] == 150 and m["retried"] == 2


@pytest.mark.parametrize(("after", "code"), [
    ({"empty_rate": 0.0, "hit_rate": 0.80, "median_ms": 2000, "retrieval_id_tail": "new1"}, "A0"),
    ({"empty_rate": 0.10, "hit_rate": 0.90, "median_ms": 2000, "retrieval_id_tail": "new1"}, "A1"),
    ({"empty_rate": 0.0, "hit_rate": 0.70, "median_ms": 2000, "retrieval_id_tail": "new1"}, "A2"),
    ({"empty_rate": 0.0, "hit_rate": 0.80, "median_ms": 4000, "retrieval_id_tail": "new1"}, "A3"),
    ({"empty_rate": 0.0, "hit_rate": 0.80, "median_ms": 2000, "retrieval_id_tail": "old1"}, "AX"),
])
def test_adopt_verdict(after, code) -> None:
    before = {"empty_rate": 0.24, "hit_rate": 0.78, "median_ms": 2000, "retrieval_id_tail": "old1"}
    assert rhr.adopt_verdict(before, after) == code


def test_compare_reports_marks_control_group() -> None:
    before = {"started_at": "t0", "repeat": 5, "cases": [
        _case("H01", "hq_manual", [(0, False, 100, False, None, None), (3, True, 100, False, None, None)], "old1"),
        _case("A01", "arch_docs", [(3, True, 100, False, None, None)], "arc1")]}
    after = {"started_at": "t1", "repeat": 5, "cases": [
        _case("H01", "hq_manual", [(3, True, 100, False, None, None), (3, True, 100, False, None, None)], "new1"),
        _case("A01", "arch_docs", [(3, True, 100, False, None, None)], "arc1")]}
    text = "\n".join(rhr.compare_reports(before, after))
    assert "■ 비교 판정 hq_manual: A0" in text
    assert "■ 대조군 arch_docs" in text
    assert "적중 하락 질의: 없음" in text


def test_gold_mode_with_retry_writes_metrics(monkeypatch, tmp_path, capsys) -> None:
    import src.clients.fabrix_retrieval as fr
    import src.config as cfg_mod

    gold = tmp_path / "gold.yaml"
    gold.write_text(
        "version: 1\nqueries:\n"
        "  - {id: H01, collection: hq_manual, query: 서버 관리자의 역할, expected: [서버시스템 관리]}\n"
        "  - {id: A01, collection: arch_docs, query: 서버 구성 단위, expected: [서버시스템구성지침]}\n",
        encoding="utf-8")
    seen: list[tuple[str, str]] = []

    async def fake_retrieve(col, query, **kwargs):
        seen.append((col.id, query))
        if col.id == "hq_manual" and len([s for s in seen if s[0] == "hq_manual"]) == 1:
            return RetrievalOutcome(col.id, "empty", hyde_query="h0", raw={})   # 첫 호출만 0건
        title = "서버시스템관리 지침" if col.id == "hq_manual" else "서버시스템구성지침"
        hit = DocHit(collection_id=col.id, doc_id=f"{col.id}-1", title=title, filename="",
                     subtitle="", content="", rank=1, rank_score=0.9, url="",
                     content_type="", catalog_id="", result_id="")
        return RetrievalOutcome(col.id, "ok", hits=[hit], hyde_query="h", raw={})

    monkeypatch.setattr(fr, "retrieve_one", fake_retrieve)
    monkeypatch.setattr(cfg_mod, "load_config", _config)
    args = rhr.build_parser().parse_args(
        ["--gold", str(gold), "--with-retry", "-n", "2", "--interval", "0",
         "--out", "before.json", "--out-dir", str(tmp_path)])
    assert asyncio.run(rhr.main_async(args, ROOT)) == 0
    out = capsys.readouterr().out
    assert seen[:2] == [("hq_manual", "서버 관리자의 역할"), ("hq_manual", "서버 관리자의 역할")]  # 재시도
    assert "▶ 요약 H01/hq_manual: 0건 1/2 · 재시도후 0/2 · 적중 1/2" in out
    assert "■ 집계 hq_manual(id …hq01): 회차 2 · 0건률 50% · 재시도후 0건률 0% · 적중률 50% · 재시도후 적중률 100%" in out
    payload = json.loads((tmp_path / "before.json").read_text(encoding="utf-8"))
    assert payload["metrics"]["arch_docs"]["hit_rate"] == 1.0
    assert payload["with_retry"] is True


def test_compare_cli_needs_no_config(tmp_path, capsys) -> None:
    rep = {"started_at": "t", "repeat": 1, "cases": [
        _case("H01", "hq_manual", [(3, True, 100, False, None, None)], "old1")]}
    (tmp_path / "b.json").write_text(json.dumps(rep), encoding="utf-8")
    rep["cases"][0]["retrieval_id_tail"] = "new1"
    (tmp_path / "a.json").write_text(json.dumps(rep), encoding="utf-8")
    assert rhr.main(["--compare", str(tmp_path / "b.json"), str(tmp_path / "a.json")]) == 0
    assert "■ 비교 판정 hq_manual: A0" in capsys.readouterr().out


# ── plans/141 §11 — 지연 비교 보정(결과 있는 회차 · 대조군) ──────────────────

def _lat_case(label, col, tail, runs):
    """runs = [(hits, ms)]"""
    return {"label": label, "collection": col, "retrieval_id_tail": tail, "runs": [
        {"hits": h, "hit": bool(h), "elapsed_ms": ms, "doc_label": "1" if h else "-",
         "retried": False, "retry_hits": None, "retry_hit": None} for h, ms in runs]}


def test_median_ok_ms_ignores_fast_empty_runs() -> None:
    m = rhr.collection_metrics([_lat_case("H01", "hq_manual", "old1",
                                          [(0, 400), (0, 420), (3, 2500), (3, 2600)])])["hq_manual"]
    assert m["median_ms"] == 1460.0          # 0건이 섞여 빨라 보인다
    assert m["median_ok_ms"] == 2550.0       # 결과 있는 회차만


def test_compare_does_not_flag_latency_when_before_was_failing_fast() -> None:
    """실측(2026-10-07) 형태 — 교체 전 45% 0건(빠름) → 교체 후 2% 0건. 전체 중앙값은 1.8배지만
    결과 있는 회차끼리는 비슷하다 → A3가 아니라 A0."""
    before_runs = [(0, 450)] * 9 + [(3, 2450)] * 11
    after_runs = [(3, 2525)] * 20
    before = {"started_at": "t0", "repeat": 5, "cases": [
        _lat_case("H", "hq_manual", "66bd", before_runs),
        _lat_case("A", "arch_docs", "9b06", [(3, 3183)] * 10)]}
    after = {"started_at": "t1", "repeat": 5, "cases": [
        _lat_case("H", "hq_manual", "0ae9", after_runs),
        _lat_case("A", "arch_docs", "9b06", [(3, 3180)] * 10)]}
    text = "\n".join(rhr.compare_reports(before, after))
    assert "■ 비교 판정 hq_manual: A0" in text
    assert "대조군 지연 비율(후/전): 1.00" in text


def test_compare_flags_real_latency_regression_after_control_correction() -> None:
    before = {"cases": [_lat_case("H", "hq_manual", "old1", [(3, 1000)] * 4),
                        _lat_case("A", "arch_docs", "same", [(3, 1000)] * 4)]}
    after = {"cases": [_lat_case("H", "hq_manual", "new1", [(3, 2000)] * 4),
                       _lat_case("A", "arch_docs", "same", [(3, 1000)] * 4)]}
    assert "■ 비교 판정 hq_manual: A3" in "\n".join(rhr.compare_reports(before, after))
    # 대조군도 같이 느려졌으면(플랫폼 상태) 교체 탓이 아니다
    after["cases"][1] = _lat_case("A", "arch_docs", "same", [(3, 2000)] * 4)
    assert "■ 비교 판정 hq_manual: A0" in "\n".join(rhr.compare_reports(before, after))


def test_compare_recomputes_old_result_files_without_new_metric() -> None:
    """예전 결과 파일(metrics에 median_ok_ms 없음)도 회차 기록으로 다시 집계한다."""
    before = {"metrics": {"hq_manual": {"median_ms": 1}},
              "cases": [_lat_case("H", "hq_manual", "old1", [(0, 400), (3, 2500)])]}
    after = {"cases": [_lat_case("H", "hq_manual", "new1", [(3, 2500), (3, 2500)])]}
    assert "■ 비교 판정 hq_manual: A0" in "\n".join(rhr.compare_reports(before, after))
