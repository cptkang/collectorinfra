"""시연 세션 실행기(scripts/demo) — 카탈로그·자리표·역질문 자동 응답·실행 루프.

실 서버·LLM 없이 httpx MockTransport 로 웹 서버 계약(SSE done 이벤트)을 흉내 낸다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts.demo import __main__ as demo_main
from scripts.demo.catalog import (
    DEFAULT_ENV_PATH,
    SESSIONS_PATH,
    Environment,
    Session,
    Turn,
    load_catalog,
    load_environment,
    placeholders_in,
    render_session,
    select_sessions,
)
from scripts.demo.client import DemoClient, Question, auto_answer, pending_question

CLOSED_TEMPLATE = SESSIONS_PATH.parent / "env.closed.template.yaml"


def _env(sources: tuple[str, ...] = ("polestar",), **values: str) -> Environment:
    return Environment(name="t", sources=sources, values=dict(values))


# --- 카탈로그 -------------------------------------------------------------


def test_committed_catalog_and_sandbox_env_are_clean() -> None:
    catalog = load_catalog()
    env = load_environment(DEFAULT_ENV_PATH)
    assert catalog.problems == []
    chosen, _ = select_sessions(catalog, env)
    assert chosen, "샌드박스에서 실행할 세션이 있어야 한다"
    for session in chosen:
        _, problems = render_session(session, env)
        assert problems == [], problems


def test_closed_template_flags_blank_values() -> None:
    """템플릿은 실제 값이 비어 있다 - 채우기 전에 실행되면 안 된다."""
    catalog = load_catalog()
    env = load_environment(CLOSED_TEMPLATE)
    chosen, _ = select_sessions(catalog, env)
    problems = [p for s in chosen for p in render_session(s, env)[1]]
    assert any("apm_instance" in p for p in problems)


def test_every_placeholder_in_catalog_is_declared_in_both_env_files() -> None:
    names: set[str] = set()
    for session in load_catalog().sessions:
        for t in session.turns:
            names |= placeholders_in([t.query, t.upload, t.selected_db_ids, t.form_fill_answers])
    for path in (DEFAULT_ENV_PATH, CLOSED_TEMPLATE):
        declared = set(load_environment(path).values)
        assert names <= declared, f"{path.name} 에 없는 자리표: {sorted(names - declared)}"


def test_core_set_covers_every_group() -> None:
    catalog = load_catalog()
    groups = {s.group_id for s in catalog.sessions}
    core_groups = {s.group_id for s in catalog.sessions if s.core}
    assert groups <= core_groups


def test_unknown_turn_key_and_source_are_reported(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "groups:\n  - id: X\n    name: x\n    sessions:\n"
        "      - id: X-1\n        requires: [nope]\n        turns:\n"
        "          - {query: a, selcted_db_ids: [b]}\n",
        encoding="utf-8",
    )
    problems = load_catalog(path).problems
    assert any("모르는 소스" in p for p in problems)
    assert any("selcted_db_ids" in p for p in problems)


def test_render_fills_placeholders_and_reports_missing() -> None:
    session = Session("S", "G", "g", "t", "", False, (), (
        Turn(query="{{server}} 상태"),
        Turn(query="{{month}} 통계"),
        Turn(form_fill_answers={"담당자": {"action": "literal", "value": "{{owner}}"}}),
    ))
    rendered, problems = render_session(session, _env(server="svr-1", owner="팀"))
    assert rendered.turns[0].query == "svr-1 상태"
    assert rendered.turns[2].form_fill_answers == {"담당자": {"action": "literal", "value": "팀"}}
    assert problems == ["S 턴2: 값이 비었거나 없는 자리표 ['month']"]


def test_select_skips_sessions_needing_unconnected_sources() -> None:
    catalog = load_catalog()
    chosen, skipped = select_sessions(catalog, _env(("polestar", "itam")), only=["C1", "A1", "ZZ"])
    assert [s.id for s in chosen] == ["C1"]
    reasons = {s.id: r for s, r in skipped}
    assert "apm" in reasons["A1"]
    assert "카탈로그에 없는" in reasons["ZZ"]


# --- 역질문 자동 응답 -----------------------------------------------------


def test_zone_answer_prefers_configured_zone_else_first() -> None:
    clar = {"kind": "zone_select", "original_query": "원문",
            "options": [{"db_id": "polestar_b0"}, {"db_id": "polestar_cm_gp"}]}
    q = pending_question({"clarification": clar})
    assert q is not None and q.kind == "db_select"
    assert auto_answer(q, last_query="x", zone_preference=("polestar_cm_gp",)) == {
        "query": "원문", "selected_db_ids": ["polestar_cm_gp"]}
    assert auto_answer(q, last_query="x")["selected_db_ids"] == ["polestar_b0"]


def test_source_select_answers_all_offered_sources() -> None:
    clar = {"kind": "source_select", "original_query": "원문", "options": [
        {"db_id": "polestar", "source": "polestar"}, {"db_ids": ["itam"], "source": "itam"},
        {"label": "프로메테우스", "source": "prometheus"}]}
    reply = auto_answer(pending_question({"clarification": clar}), last_query="x")
    assert reply == {"query": "원문", "selected_db_ids": ["polestar", "itam"],
                     "selected_sources": ["polestar", "itam", "prometheus"]}


def test_form_fill_and_apm_and_text_questions() -> None:
    form = pending_question({"form_fill_clarification": {"fields": [{"name": "담당자"}]}})
    assert auto_answer(form, last_query="x") == {
        "query": "[양식 미해결 항목 답변]", "form_fill_answers": {"담당자": {"action": "blank", "value": None}}}
    apm = pending_question({"apm_source_clarification": {
        "original_query": "q", "options": [{"key": "b0"}, {"key": "cm_prd"}]}})
    assert auto_answer(apm, last_query="x", apm_preference=("cm_prd",))["selected_apm_source_ids"] == ["cm_prd"]
    assert auto_answer(apm, last_query="x", apm_preference=("zz",))["selected_apm_source_ids"] == ["b0"]
    text = pending_question({"clarification": {"question": "기간은?"}})
    assert text == Question("text", {"question": "기간은?"})
    assert auto_answer(text, last_query="x") is None
    assert pending_question({"response": "끝"}) is None


# --- 실행 루프 (가짜 서버) ------------------------------------------------


def _sse(done: dict[str, Any]) -> bytes:
    return ("data: " + json.dumps({"type": "token", "content": "…"}) + "\n\n"
            + "data: " + json.dumps({"type": "done", **done}, ensure_ascii=False) + "\n\n").encode()


class FakeServer:
    """요청을 기록하고, 준비된 done 이벤트를 차례로 돌려준다."""

    def __init__(self, dones: list[dict[str, Any]]) -> None:
        self.dones = list(dones)
        self.requests: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/auth/login"):
            return httpx.Response(200, json={"access_token": "tok"})
        if "/query/file/stream" in path:
            body = {"multipart": request.content.decode("utf-8", "replace")}
        else:
            body = json.loads(request.content or b"{}")
        self.requests.append((path, body, dict(request.headers)))
        return httpx.Response(200, content=_sse(self.dones.pop(0)),
                              headers={"content-type": "text/event-stream"})


def _args(**over: Any) -> Any:
    args = demo_main.build_parser().parse_args([])
    for k, v in over.items():
        setattr(args, k, v)
    return args


def test_session_auto_answers_zone_question_on_same_thread(tmp_path: Path) -> None:
    server = FakeServer([
        {"response": "존을 골라 주세요", "clarification": {
            "kind": "zone_select", "original_query": "OS 종류", "options": [{"db_id": "polestar_cm_gp"}]}},
        {"response": "김포 OS 분포", "row_count": 3},
        {"response": "상위 5대", "row_count": 5},
    ])
    session = Session("S", "G", "g", "t", "", False, (), (Turn(query="OS 종류"), Turn(query="CPU 상위 5대")))
    with DemoClient("http://x", user="u", password="p", client_id="abcdef12",
                    transport=httpx.MockTransport(server)) as client:
        assert client.login() is None
        results = demo_main.run_session(client, session, _env(), _args(), tmp_path)

    assert [r.auto_answer for r in results] == [False, True, False]
    bodies = [b for _, b, _ in server.requests]
    assert bodies[1]["selected_db_ids"] == ["polestar_cm_gp"]
    assert bodies[1]["query"] == "OS 종류"
    assert len({b["thread_id"] for b in bodies}) == 1, "세션 전체가 한 대화여야 한다"
    headers = server.requests[0][2]
    assert headers["authorization"] == "Bearer tok"
    assert headers["x-client-id"] == "abcdef12"

    transcript = demo_main._write_outputs(tmp_path / "out", [(session, results)], _env())
    text = transcript.read_text(encoding="utf-8")
    assert "**자동 응답**: OS 종류" in text and "김포 OS 분포" in text
    lines = (tmp_path / "out" / "results.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3 and json.loads(lines[1])["auto_answer"] is True


def test_expected_clarify_is_answered_by_next_declared_turn(tmp_path: Path) -> None:
    form_q = {"response": "담당자 열은?", "form_fill_clarification": {"fields": [{"name": "담당자"}]}}
    server = FakeServer([form_q, {"response": "채웠습니다", "has_file": False}])
    upload = "testdata/templates/server_list_template.xlsx"
    session = Session("S", "G", "g", "t", "", False, (), (
        Turn(query="채워줘", upload=upload, expect_clarify=True),
        Turn(upload=upload, form_fill_answers={"담당자": {"action": "literal", "value": "팀"}},
             form_fill_remember=True),
    ))
    with DemoClient("http://x", transport=httpx.MockTransport(server)) as client:
        results = demo_main.run_session(client, session, _env(), _args(), tmp_path)

    assert len(results) == 2 and not any(r.auto_answer for r in results)
    (p1, _, _), (p2, b2, _) = server.requests
    assert p1.endswith("/query/file/stream")
    assert p2.endswith("/query/stream"), "양식 답변 턴은 파일을 다시 올리지 않는다"
    assert b2["query"] == "[양식 미해결 항목 답변]"
    assert b2["form_fill_answers"]["담당자"]["value"] == "팀" and b2["form_fill_remember"] is True


def test_stop_mode_leaves_question_for_presenter(tmp_path: Path) -> None:
    server = FakeServer([{"response": "?", "clarification": {
        "kind": "zone_select", "options": [{"db_id": "a"}]}}])
    session = Session("S", "G", "g", "t", "", False, (), (Turn(query="q"), Turn(query="never")))
    with DemoClient("http://x", transport=httpx.MockTransport(server)) as client:
        results = demo_main.run_session(client, session, _env(), _args(clarify="stop"), tmp_path)
    assert len(results) == 1 and len(server.requests) == 1


@pytest.mark.parametrize("argv", [["--check"], ["--list", "--core"]])
def test_cli_offline_modes_pass_on_sandbox(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert demo_main.main(argv) == 0
    assert "실행 대상" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["--check", "--group", "G1"], ["--check", "--core", "--group", "ZZ"]])
def test_cli_check_fails_when_nothing_is_selected(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    """선택 결과가 0세션이면 「점검 통과」가 아니다 — 옛 군 id(G1)·없는 군은 사유로 드러난다."""
    assert demo_main.main(argv) == 1
    out = capsys.readouterr().out
    assert "점검 통과" not in out and "카탈로그에 없는 군 id" in out


def test_empty_catalog_is_a_problem(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """반입 중 잘린·빈 카탈로그를 조용히 0세션으로 통과시키지 않는다."""
    empty = tmp_path / "sessions.yaml"
    empty.write_text("version: 1\n", encoding="utf-8")
    assert demo_main.main(["--check", "--sessions", str(empty)]) == 1
    assert "세션이 하나도 없습니다" in capsys.readouterr().out
