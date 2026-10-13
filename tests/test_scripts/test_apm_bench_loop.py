"""APM 조회 벤치 — few-shot 개선 루프 도구(지문 · 과적합 검사 · 회차 비교 · 재현 · 수정 이력).

plans/152 W5 · D-323.

네트워크·실 LLM 0. 재현(`replay`)은 메시지를 기록하는 가짜 LLM 을 주입하고, 레지스트리 렌더
(`render_view_rows`)를 monkeypatch 로 바꿔 「E1에서 예문 1건을 바꿔 재현·비교 1회」를 한다.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from langchain_core.messages import AIMessage

from scripts.apm_bench import fingerprints as fp
from scripts.apm_bench import loop
from scripts.apm_bench import scenarios as sc
from src.orchestration import apm_query

#: W0 실측(2026-10-12 · `ee13b47`) — F1~F4 를 의도적으로 고치면 fewshot_log 와 함께 갱신한다.
W0_FINGERPRINTS = {
    "F1": "9c2eb9cd7418",
    "F1_render": "271766d508c1",
    "F2": "99ee2e9f9bf9",
    "F3": "f5646d317265",
    "F4": "71ff3e68f138",
}


@pytest.fixture(scope="module")
def registry() -> sc.RegistryInfo:
    return sc.load_registry_info()


@pytest.fixture(scope="module")
def real() -> list[sc.Scenario]:
    return sc.load_scenarios()


# --- 지문 · 크기 ------------------------------------------------------------------------------


def test_fingerprints_deterministic(registry: sc.RegistryInfo) -> None:
    # 현재 값을 고정하지 않는다 — F1~F4 는 개선 루프가 고치는 대상이라 값 고정은 다른 작업의
    # 회귀를 깬다.
    first = fp.fewshot_fingerprints()
    assert first == fp.fewshot_fingerprints()
    for key in (*W0_FINGERPRINTS, "registry"):
        assert re.fullmatch(r"[0-9a-f]{12}", first[key] or ""), key
    assert fp.fewshot_fingerprints(registry)["F1"] == first["F1"]


def test_section_size_within_limits() -> None:
    sizes = fp.apm_section_size()
    assert sizes == fp.apm_section_size()
    assert sizes["section_chars"] <= fp.APM_SECTION_MAX_CHARS
    assert sizes["increment_chars"] <= fp.APM_INCREMENT_MAX_CHARS
    assert sizes["view_rows_chars"] < sizes["section_chars"]
    assert fp.size_gate(sizes) == []


def test_size_gate_detects_overflow(monkeypatch: pytest.MonkeyPatch, registry: sc.RegistryInfo,
                                    real: list[sc.Scenario]) -> None:
    over = fp.size_gate({"section_chars": fp.APM_SECTION_MAX_CHARS + 1,
                         "increment_chars": fp.APM_INCREMENT_MAX_CHARS + 1})
    assert len(over) == 2
    original = apm_query.render_view_rows
    monkeypatch.setattr(apm_query, "render_view_rows",
                        lambda: original() + "\n- 예: " + "가" * 3000)
    found = [v for v in loop.fewshot_check(real, registry) if v["kind"] == "size"]
    assert found and "상한" in found[0]["message"]


# --- 과적합 검사 ------------------------------------------------------------------------------


def test_fewshot_check_current_repo_has_no_violation(registry: sc.RegistryInfo,
                                                     real: list[sc.Scenario]) -> None:
    assert loop.fewshot_check(real, registry) == []
    loci = [locus for locus, _ in loop.fewshot_texts(registry)]
    assert any(x.startswith("F1:") for x in loci)
    assert any(x.startswith("F2:") for x in loci)
    assert any(x.startswith("F3:") for x in loci)
    assert "다음에 돌릴 명령" in loop.render_fewshot_check([])


def test_similarity_is_char_ngram_jaccard() -> None:
    assert loop.similarity("응답시간 보여줘", "응답시간  보여줘!") == 1.0
    assert loop.similarity("가나다", "") == 0.0
    assert 0 < loop.similarity("web01 응답시간", "web01 힙 추세") < 0.5


def _fake_registry(*examples: str) -> sc.RegistryInfo:
    view = SimpleNamespace(id="apm.fake", examples=tuple(examples), args=())
    return sc.RegistryInfo(views={"apm.fake": view}, source_ids=frozenset())


def test_fewshot_check_blocks_copied_scenario_prompt(real: list[sc.Scenario]) -> None:
    by_id = {s.id: s for s in real}
    plain = by_id["APM-RANK-tps-asc"].turns[0].prompt
    held = by_id["APM-RANK-svc-slow3"].turns[0].prompt  # 자리표 {{svc_2}} 포함
    assert "{{svc_2}}" in held
    renamed = held.replace("{{svc_2}}", "web-01")
    registry = _fake_registry(f"「{plain}」 → n=5", f"「{renamed}」 → n=3", "「전혀 다른 질문」")
    got = loop.fewshot_check(real, registry, sizes={"section_chars": 0, "increment_chars": 0})
    hits = {(v["locus"], v["scenario"], v["variant"]) for v in got}
    assert ("F1:apm.fake:example#1", "APM-RANK-tps-asc#0", "raw") in hits
    assert ("F1:apm.fake:example#2", "APM-RANK-svc-slow3#0", "placeholder_removed") in hits
    assert not any(v["locus"] == "F1:apm.fake:example#3" for v in got)
    assert all(v["score"] >= loop.SIMILARITY_THRESHOLD for v in got)
    assert "[차단]" in loop.render_fewshot_check(got)


# --- 수정 이력 ------------------------------------------------------------------------------


def _entry(round_no: int, targets: list[str], run_id: str = "run-a") -> dict[str, Any]:
    return {"round": round_no, "date": "2026-10-20", "targets": targets,
            "locus": [{"slot": "F1", "view": "apm.ranking", "arg": "order", "example": 3}],
            "fingerprints": {"before": dict(W0_FINGERPRINTS), "after": {"F1": "000000000000"}},
            "run_id": run_id, "memo": ""}


def test_log_file_is_empty_and_append_keeps_header(tmp_path: Path) -> None:
    assert loop.load_log() == []
    path = tmp_path / "fewshot_log.yaml"
    shutil.copy(loop.FEWSHOT_LOG_PATH, path)
    loop.append_entry(_entry(1, ["APM-RANK-tps-asc#0"]), path)
    loop.append_entry(_entry(2, ["APM-RANK-tps-asc#0", "APM-INST-domain#0"]), path)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# plans/152")
    assert "targets:" in text
    entries = loop.load_log(path)
    assert [e["round"] for e in entries] == [1, 2]
    counts = loop.fix_counts(entries)
    assert counts["APM-RANK-tps-asc#0"] == 2 and counts["APM-INST-domain#0"] == 1


def test_log_rejects_bad_and_holdout_targets(tmp_path: Path) -> None:
    path = tmp_path / "fewshot_log.yaml"
    with pytest.raises(ValueError, match="holdout"):
        loop.append_entry(_entry(1, ["APM-INST-list#0"]), path, holdout_ids={"APM-INST-list#0"})
    bad = {**_entry(1, ["no-turn-id"]), "locus": [{"slot": "F9"}]}
    problems = loop.validate_entry(bad)
    assert any("targets" in p for p in problems) and any("slot" in p for p in problems)
    assert not path.exists()


def test_load_log_rejects_hand_written_holdout_target(tmp_path: Path) -> None:
    """손으로 쓴 holdout 대상 항목도 읽을 때 거부한다(`holdout_ids` 전달 · 권고 ③)."""
    path = tmp_path / "fewshot_log.yaml"
    path.write_text(yaml.safe_dump({"entries": [_entry(1, ["APM-INST-list#0"])]},
                                   allow_unicode=True), encoding="utf-8")
    assert len(loop.load_log(path)) == 1
    with pytest.raises(ValueError, match="holdout"):
        loop.load_log(path, holdout_ids={"APM-INST-list#0"})


# --- 수정 후보 ------------------------------------------------------------------------------


def _rec(sid: str, split: str, ok: bool, *, cats: tuple[str, ...] = ("view_wrong",),
         turn: int = 0) -> dict[str, Any]:
    per = {c: {"fewshot_fixable": c != "window_wrong",
               "locus": ["code:plan_window"] if c == "window_wrong" else [f"F1:apm.{sid}"]}
           for c in cats}
    return {"scenario_id": sid, "turn": turn, "split": split, "covers": ["COV-X"],
            "verdict": {"turn_pass": ok, "axes": {}, "categories": [] if ok else list(cats),
                        "axis_detail": {} if ok else {"categories": per},
                        "expected_clarify": False, "expected_refusal": False}}


def test_fix_candidates_dev_only() -> None:
    records = [
        _rec("d1", "dev", False),
        _rec("d2", "dev", False, cats=("window_wrong",)),  # 코드 교정만 — 빠진다
        _rec("d3", "dev", True),
        _rec("h1", "holdout", False),
    ]
    got = loop.fix_candidates(records)
    assert [c["id"] for c in got] == ["d1#0"]
    assert got[0]["locus"] == ["F1:apm.d1"] and got[0]["categories"] == ["view_wrong"]


# --- 회차 비교 ------------------------------------------------------------------------------


def _run(tmp: Path, name: str, records: list[dict[str, Any]], *, env: str = "e2",
         planes: dict[str, str] | None = None, sha: str = "aaa",
         fps: dict[str, Any] | None = None) -> Path:
    out = tmp / name
    out.mkdir()
    meta = {"run_id": name, "sha": sha, "env": env,
            "planes": planes if planes is not None else {"worker": "fabrix",
                                                         "orchestrator": "vllm"},
            "fingerprints": fps or dict(W0_FINGERPRINTS)}
    (out / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    (out / "trace.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
    return out


def test_compare_holdout_up_targets_worsened_and_promotion(tmp_path: Path) -> None:
    a = _run(tmp_path, "run-a", [
        _rec("h1", "holdout", False), _rec("h2", "holdout", True),
        _rec("d1", "dev", False), _rec("d2", "dev", True), _rec("d3", "dev", False),
    ])
    b = _run(tmp_path, "run-b", [
        _rec("h1", "holdout", True), _rec("h2", "holdout", True),
        _rec("d1", "dev", True), _rec("d2", "dev", False), _rec("d3", "dev", False),
    ])
    log = [_entry(1, ["d3#0"], run_id="old"), _entry(2, ["d1#0", "d3#0"], run_id="run-a")]
    got = loop.compare(a, b, log=log)
    assert got["decisive"] is True and got["label"] == ""
    assert got["holdout"]["direction"] == "상승"
    assert got["holdout"]["a"]["accuracy"] == 0.5 and got["holdout"]["b"]["accuracy"] == 1.0
    assert got["target_basis"] == "run_id=run-a"
    result = {t["id"]: t["result"] for t in got["targets"]}
    assert result == {"d1#0": "해소", "d3#0": "잔존"}
    assert got["worsened"] == {"dev": ["d2#0"], "holdout": 0}
    assert got["promote"] == [{"id": "d3#0", "fixes": 2}]
    assert got["fingerprints"] is None  # 같은 SHA
    md = loop.render_compare(got)
    assert "**상승**" in md and "d3#0 (수정 2회)" in md and "방향 확인용" not in md


@pytest.mark.parametrize(("b_ok", "direction"), [((False, False), "하락"), ((False, True), "불변")])
def test_compare_holdout_down_and_flat(tmp_path: Path, b_ok: tuple[bool, bool],
                                       direction: str) -> None:
    a = _run(tmp_path, "run-a", [_rec("h1", "holdout", True), _rec("h2", "holdout", False)])
    b = _run(tmp_path, "run-b", [_rec("h1", "holdout", b_ok[0]),
                                 _rec("h2", "holdout", b_ok[1])])
    got = loop.compare(a, b, log=[])
    assert got["holdout"]["direction"] == direction
    md = loop.render_compare(got)
    if direction == "하락":
        # holdout 악화는 건수만 — ID 상세를 수정 대상으로 내놓지 않는다(권고 ③)
        assert got["worsened"] == {"dev": [], "holdout": 1}
        assert "h1#0" not in md and "holdout: 1건" in md


def test_compare_different_turn_sets_uses_common_turns(tmp_path: Path) -> None:
    """전체 run 대 일부 턴 run — 공통 턴만으로 판정하고 표시한다(전체 대 3턴 「상승」 오판 방지)."""
    a = _run(tmp_path, "run-a", [_rec("h1", "holdout", True), _rec("h2", "holdout", False),
                                 _rec("h3", "holdout", False)])
    b = _run(tmp_path, "run-b", [_rec("h1", "holdout", True)])
    got = loop.compare(a, b, log=[])
    assert got["turn_sets"] == {"same": False, "common": 1, "only_a": 2, "only_b": 0}
    assert got["holdout"]["a"]["total"] == 1 and got["holdout"]["direction"] == "불변"
    assert "공통 턴 1개만으로 판정" in loop.render_compare(got)
    c = _run(tmp_path, "run-c", [_rec("h9", "holdout", True)])
    assert loop.compare(a, c, log=[])["holdout"]["direction"] == "판정 불가"


def test_compare_e1_or_mlx_run_is_directional_only(tmp_path: Path) -> None:
    a = _run(tmp_path, "run-a", [_rec("h1", "holdout", False)],
             planes={"worker": "mlx", "orchestrator": "mlx"})
    b = _run(tmp_path, "run-b", [_rec("h1", "holdout", True)], env="e1", sha="bbb",
             fps={**W0_FINGERPRINTS, "F1": "111111111111"})
    got = loop.compare(a, b, log=[])
    assert got["decisive"] is False and got["label"] == loop.DIRECTIONAL_LABEL
    assert any("MLX" in r for r in got["runs"]["a"]["directional"])
    assert any("E1" in r for r in got["runs"]["b"]["directional"])
    assert got["holdout"]["direction"] == "상승"
    assert got["fingerprints"]["changed"] == {"F1": {"a": W0_FINGERPRINTS["F1"],
                                                     "b": "111111111111"}}
    md = loop.render_compare(got)
    assert loop.DIRECTIONAL_LABEL in md and "(방향 확인용)" in md and "## 지문 변화" in md
    assert loop.directional_reasons({"env": "e2", "planes": {"worker": "fabrix",
                                                             "orchestrator": "vllm"}}) == []
    assert loop.directional_reasons({"env": "e2"}) == ["planes 칸 없음"]


# --- 재현 ------------------------------------------------------------------------------------


class RecordingLLM:
    """응답을 차례로 내고 받은 메시지를 기록하는 가짜 LLM(분해·재선택 공용)."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = [json.dumps(r, ensure_ascii=False) for r in responses]
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any], **_: Any) -> AIMessage:
        self.calls.append(list(messages))
        return AIMessage(content=self.responses.pop(0))


def _stub_config() -> Any:
    """APM 엔드포인트가 없는 설정 대역 — `replay_config`가 APM 을 활성으로 바꾼다."""
    return SimpleNamespace(
        router=SimpleNamespace(capability_ownership_enabled=True),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: ["polestar_cm_gp"]),
        composite=SimpleNamespace(task_frame_enabled=False),
        dbhub=SimpleNamespace(source_endpoint=lambda system: None),
        rag=None,
    )


def _turn_record(scenario: sc.Scenario, turn: int, plan_final: list[dict[str, Any]],
                 ok: bool = False) -> dict[str, Any]:
    t = scenario.turns[turn]
    e = t.expect
    return {
        "scenario_id": scenario.id, "turn": turn, "split": scenario.split,
        "prompt": t.prompt, "placeholders": {},
        "expect": {"agents": list(e.agents), "views": list(e.views), "target": e.target,
                   "window": e.window, "args": dict(e.args)},
        "observed": {"agents": ["apm_query"], "plan_raw": plan_final, "plan_final": plan_final,
                     "reselect": None},
        "verdict": {"turn_pass": ok, "categories": [] if ok else ["arg_missing"],
                    "axis_detail": {}},
    }


def test_replay_reflects_changed_example_and_compares(monkeypatch: pytest.MonkeyPatch,
                                                      registry: sc.RegistryInfo,
                                                      real: list[sc.Scenario]) -> None:
    scenario = {s.id: s for s in real}["APM-RANK-tps-asc"]
    record = _turn_record(scenario, 0, [{"task_id": "t1", "agent": "apm_query",
                                         "views": ["apm.ranking"],
                                         "view_args": {"apm.ranking": {"n": 5}}}])
    old = "「TPS 낮은 순 WAS 3개」"
    new = "「TPS 낮은 순 WAS 상위 3개」"
    original = apm_query.render_view_rows
    assert old in original()
    before_fp = fp.fewshot_fingerprints()["F1_render"]
    monkeypatch.setattr(apm_query, "render_view_rows", lambda: original().replace(old, new))
    assert fp.fewshot_fingerprints()["F1_render"] != before_fp

    llm = RecordingLLM([{"tasks": [{
        "task_id": "t1", "agent": "apm_query", "sub_query": scenario.turns[0].prompt,
        "areas": ["was_performance"], "views": ["apm.ranking"],
        "view_args": {"apm.ranking": {"metric": "tps", "order": "asc", "n": 5}},
        "targets": [], "depends_on": [], "input_from": [], "order": 1}]}])
    rows = loop.replay([record], llm=llm, registry=registry,
                       app_config=loop.replay_config(_stub_config()))
    assert len(llm.calls) == 1  # 분해 1회 · 영역을 덮어 재선택 없음
    system = llm.calls[0][0].content
    assert new in system and old not in system
    (row,) = rows
    assert row["id"] == "APM-RANK-tps-asc#0" and row["error"] is None
    assert row["label"] == loop.DIRECTIONAL_LABEL
    assert row["before"]["views"] == "pass" and row["before"]["args"] == "fail"
    assert row["after"]["views"] == "pass" and row["after"]["args"] == "pass"
    assert row["changed"] == {"args": ["fail", "pass"]}
    assert row["after"]["reselect"] is None


def test_replay_reselection_with_previous_turn_context(registry: sc.RegistryInfo,
                                                       real: list[sc.Scenario]) -> None:
    scenario = {s.id: s for s in real}["APM-MT-profile-slowest"]
    first = _turn_record(scenario, 0, [{"task_id": "t1", "agent": "apm_query",
                                        "views": ["apm.slow_tx"],
                                        "view_args": {"apm.slow_tx": {"n": 3}}}], ok=True)
    second = _turn_record(scenario, 1, [{"task_id": "t1", "agent": "apm_query",
                                         "views": ["apm.slow_tx"], "view_args": {}}])
    llm = RecordingLLM([
        {"tasks": [{"task_id": "t1", "agent": "apm_query", "sub_query": "맨 위 거래 구간별",
                    "areas": ["was_transaction"], "views": [], "view_args": {},
                    "targets": [], "depends_on": [], "input_from": [], "order": 1}]},
        {"views": ["apm.profile"], "view_args": {"apm.profile": {"ref": 1}}},
    ])
    rows = loop.replay([first, second], llm=llm, registry=registry,
                       app_config=loop.replay_config(_stub_config()))
    assert [r["id"] for r in rows] == ["APM-MT-profile-slowest#1"]  # 실패 턴만
    assert len(llm.calls) == 2  # 분해 + 재선택
    assert "직전 질의: " + scenario.turns[0].prompt in llm.calls[0][-1].content
    (row,) = rows
    assert row["before"]["views"] == "fail"
    assert row["after"]["views"] == "pass" and row["after"]["args"] == "pass"
    assert row["after"]["reselect"]["result_views"] == ["apm.profile"]


def test_replay_turn_ids_and_llm_error_is_recorded(registry: sc.RegistryInfo,
                                                   real: list[sc.Scenario]) -> None:
    scenario = {s.id: s for s in real}["APM-RANK-tps-asc"]
    record = _turn_record(scenario, 0, [], ok=True)

    class Broken:
        async def ainvoke(self, messages: list[Any], **_: Any) -> AIMessage:
            raise RuntimeError("boom")

    assert loop.replay([record], llm=Broken(), registry=registry,
                       app_config=loop.replay_config(_stub_config())) == []
    (row,) = loop.replay([record], llm=Broken(), registry=registry,
                         turn_ids=["APM-RANK-tps-asc#0"],
                         app_config=loop.replay_config(_stub_config()))
    # 분해 LLM 예외는 본체가 단일 data_query 폴백으로 바꾼다 — APM task 없음 = 보기 실패
    assert row["error"] is None and row["after"]["views"] == "fail"
    assert row["after"]["plan"] == []


def test_make_mlx_llm_refuses_without_two_loopback_mlx_planes() -> None:
    paid = SimpleNamespace(llm=SimpleNamespace(provider="gemini"),
                           orchestrator=SimpleNamespace(provider="vllm"))
    with pytest.raises(loop.ReplayBlockedError, match="과금"):
        loop.make_mlx_llm(paid)
    half = SimpleNamespace(llm=SimpleNamespace(provider="fabrix"),
                           orchestrator=SimpleNamespace(provider="mlx"))
    with pytest.raises(loop.ReplayBlockedError, match="mlx"):
        loop.make_mlx_llm(half)
    remote = SimpleNamespace(
        llm=SimpleNamespace(provider="mlx", mlx_base_url="http://10.0.0.5:8080/v1"),
        orchestrator=SimpleNamespace(provider="mlx", base_url="http://127.0.0.1:8080/v1"))
    with pytest.raises(loop.ReplayBlockedError, match="루프백"):
        loop.make_mlx_llm(remote)
