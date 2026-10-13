"""plans/152 W2 — U층 드라이버(가짜 LLM · 게이트웨이 수명 · 관측 조립 · E1 종단).

E1 종단 1건은 목 Open API + 벤치 게이트웨이(서브프로세스) + 가짜 LLM + 본체(서브프로세스)를 실제로
띄운다(실 LLM·과금 API·제니퍼 접속 없음 · 127.0.0.1 임시 포트) — 2단 확정과 APM 관측>0을 본다.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

from scripts.apm_bench import driver as dv
from scripts.apm_bench import gateway as gw
from scripts.apm_bench.fake_llm import (
    MARKERS,
    FakeLLM,
    parse_from_turn,
    plan_from_expect,
    time_slot,
)
from scripts.apm_bench.scenarios import (
    Expect,
    Turn,
    fill_placeholders,
    load_registry_info,
    load_scenarios,
)

PLACEHOLDERS_E1 = Path(__file__).resolve().parents[2] / "testdata/apm_bench/placeholders.e1.yaml"


@pytest.fixture(scope="module")
def registry():  # type: ignore[no-untyped-def]
    return load_registry_info()


@pytest.fixture(scope="module")
def placeholders() -> dv.Placeholders:
    return dv.load_placeholders(PLACEHOLDERS_E1)


# --- 자리표 파일 ---------------------------------------------------------------------------


def test_e1_placeholders_fill_every_prompt(placeholders: dv.Placeholders) -> None:
    for scenario in load_scenarios():
        for turn in scenario.turns:
            fill_placeholders(turn.prompt, placeholders.values)


def test_e1_answers_cover_registry_sources(placeholders: dv.Placeholders, registry) -> None:  # type: ignore[no-untyped-def]
    assert set(placeholders.answers) == set(registry.source_ids)
    assert placeholders.source_for("inst_b") == "bank"
    assert placeholders.source_for("inst_1") == placeholders.source_default == "common"


def test_target_placeholder() -> None:
    assert dv.target_placeholder("inst:c") == "inst_c"
    assert dv.target_placeholder("host:2") == "host_2"
    assert dv.target_placeholder("inst_w") == "inst_w"
    assert dv.target_placeholder("none") is None and dv.target_placeholder("ref") is None


def test_turn_placeholders_only_used() -> None:
    expect = Expect(agents=("apm_query",), views=("apm.trace",), target="inst:1",
                    window="default", args={"guid": "{{guid_1}}"})
    turn = Turn("{{inst_1}} 거래 추적", expect)
    values = {"inst_1": "a", "guid_1": "g", "host_1": "h"}
    assert dv.turn_placeholders(turn, values) == {"inst_1": "a", "guid_1": "g"}


def test_inherited_target_placeholder_for_ref_turn() -> None:
    """대상 승계 턴은 앞 턴 대상 자리표 값을 레코드에 싣는다(판정이 그 값으로 승계를 본다)."""
    from scripts.apm_bench.scenarios import Scenario

    first = Turn("{{host_2}} 인스턴스", Expect(("apm_query",), ("apm.instances",), "host:2",
                                            "none"))
    mid = Turn("다음", Expect(("apm_query",), ("apm.runtime",), "none", "default"))
    ref = Turn("그 인스턴스 힙", Expect(("apm_query",), ("apm.runtime",), "ref", "default"))
    scenario = Scenario("S", ("COV-X",), "dev", (first, mid, ref))
    assert dv.inherited_target(scenario, 2) == "host_2"
    assert dv.inherited_target(scenario, 0) is None
    values = {"host_2": "h2"}
    assert dv.turn_placeholders(ref, values, inherited="host_2") == {"host_2": "h2"}


# --- 가짜 LLM -----------------------------------------------------------------------------


def test_markers_present_in_product_prompts() -> None:
    from src.orchestration.apm_query import _SELECTION_SYSTEM
    from src.prompts.input_parser import INPUT_PARSER_SYSTEM_PROMPT
    from src.prompts.intent_planner import INTENT_PLANNER_SYSTEM_TEMPLATE
    from src.prompts.replanner import REPLANNER_SYSTEM_TEMPLATE

    texts = {"decompose": INTENT_PLANNER_SYSTEM_TEMPLATE, "reselect": _SELECTION_SYSTEM,
             "replan": REPLANNER_SYSTEM_TEMPLATE, "parse": INPUT_PARSER_SYSTEM_PROMPT}
    for marker, kind in MARKERS:
        assert marker in texts[kind], kind


def test_plan_from_expect_fills_args(registry, placeholders: dv.Placeholders) -> None:  # type: ignore[no-untyped-def]
    expect = Expect(agents=("apm_query",), views=("apm.instances",), target="none",
                    window="none", args={"domain_id": "{{domain_id_1}}"},
                    apm_sources=("common",))
    plan = plan_from_expect(expect, "q", mapping=placeholders.values, views=registry.views,
                            any_values=placeholders.any_values)
    task = plan["tasks"][0]
    assert task["agent"] == "apm_query" and task["views"] == ["apm.instances"]
    assert task["view_args"] == {"apm.instances": {"domain_id": 1000}}  # int 인자 정수화
    assert task["sources"] == ["common"] and task["targets"] == []


def test_plan_from_expect_refusal_and_target(registry, placeholders: dv.Placeholders) -> None:  # type: ignore[no-untyped-def]
    refusal = Expect(agents=(), views=(), target="none", window="none", refusal="unsupported")
    plan = plan_from_expect(refusal, "q", mapping=placeholders.values, views=registry.views)
    assert [t["agent"] for t in plan["tasks"]] == ["general_inference"]
    inst = Expect(agents=("apm_query",), views=("apm.app_health",), target="inst:c",
                  window="range:30")
    task = plan_from_expect(inst, "q", mapping=placeholders.values,
                            views=registry.views)["tasks"][0]
    assert task["targets"] == [{"text": "was01_b", "kind": "instance"}]


def test_time_slot_spans() -> None:
    assert time_slot("a 최근 10분 느린 거래")["span"] == "최근 10분"  # type: ignore[index]
    slot = time_slot("h 한 시간 전부터 에러")
    assert slot is not None and (slot["n"], slot["unit"], slot["span"]) == (1, "hour",
                                                                           "한 시간 전부터")
    slot = time_slot("지난 3시간 CPU 추세 5분 단위로")  # 입도(「5분 단위」)가 아니라 기간
    assert slot is not None and (slot["n"], slot["unit"]) == (3, "hour")
    assert time_slot("오늘 GC 추세 10분 간격으로") is None
    assert time_slot("느린 거래도 10분치")["unit"] == "minute"  # type: ignore[index]


def test_parse_from_turn_hosts_and_window(placeholders: dv.Placeholders) -> None:
    expect = Expect(agents=("apm_query",), views=("apm.app_health",), target="host:1",
                    window="range:30")
    template = "{{host_1}} WAS 지난 30분 에러율"
    prompt = fill_placeholders(template, placeholders.values)
    parsed = parse_from_turn(expect, template, prompt, mapping=placeholders.values)
    assert parsed["filter_conditions"] == [
        {"field": "hostname", "op": "=", "value": placeholders.values["host_1"]}]
    assert parsed["time_expr"]["span"] == "지난 30분" and parsed["query_targets"] == []
    inst = Expect(agents=("apm_query",), views=("apm.app_stats",), target="inst:1",
                  window="hourly:today")
    parsed = parse_from_turn(inst, "{{inst_1}} 오늘 통계", "a 오늘 통계",
                             mapping=placeholders.values)
    assert parsed["filter_conditions"] == [] and parsed["time_expr"] is None


def test_fake_llm_dispatch_and_http() -> None:
    fake = FakeLLM()
    url = fake.start()
    try:
        fake.set_plan({"tasks": [{"task_id": "t1", "agent": "apm_query",
                                  "views": ["apm.instances"], "view_args": {}}]})

        def post(body: dict) -> dict:
            req = urllib.request.Request(url + "/chat/completions", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read())

        out = post({"messages": [{"role": "system", "content": "… 작업 계획 전문가입니다."},
                                 {"role": "user", "content": "q"}]})
        assert '"apm.instances"' in out["choices"][0]["message"]["content"]
        out = post({"messages": [{"role": "system", "content": "보기 선택기"},
                                 {"role": "user", "content": "q"}]})
        assert '"views": ["apm.instances"]' in out["choices"][0]["message"]["content"]
        out = post({"messages": [{"role": "user", "content": "hi"}], "max_tokens": 1})
        assert out["choices"][0]["message"]["content"] == "1"
        with urllib.request.urlopen(url + "/models", timeout=5) as resp:
            assert json.loads(resp.read())["data"][0]["id"]
        parser = {"messages": [{"role": "system", "content": "'Middle-ware JSON Parser'"},
                               {"role": "user", "content": "q"}]}
        out = post(parser)
        assert out["choices"][0]["message"]["content"] == "요청하신 조회 결과를 정리했습니다."
        fake.set_parse({"query_targets": [], "filter_conditions": [{"field": "hostname"}]})
        out = post(parser)
        assert '"filter_conditions"' in out["choices"][0]["message"]["content"]
        assert fake.kinds_since(0) == ["decompose", "reselect", "probe", "parse", "parse"]
    finally:
        fake.stop()


# --- 게이트웨이 env · 감사 줄 -------------------------------------------------------------


def test_gateway_env_strips_parent_and_pins_bench_values(tmp_path: Path) -> None:
    base = {"PATH": "/bin", "JENNIFER_API_URL": "http://prod", "JENNIFER_COMMON_API_TOKEN": "x",
            "APM_SPOOL_DIR": "/var/x", "APM_EVENT_POLLER_ENABLED": "true"}
    mock = gw.MockServer("http://127.0.0.1:1", "tok", None, None, None)  # type: ignore[arg-type]
    env = gw.gateway_env(base, port=5, spool=tmp_path, tokens={"chat": "c"},
                         sources=["bank", "common"], mock=mock)
    assert env["PATH"] == "/bin" and "JENNIFER_COMMON_API_TOKEN" not in base.keys() - {
        "JENNIFER_COMMON_API_TOKEN"}
    assert env["JENNIFER_COMMON_API_TOKEN"] == "tok" and env["JENNIFER_API_URL"] == ""
    assert env["APM_SPOOL_DIR"] == str(tmp_path) and env["APM_EVENT_POLLER_ENABLED"] == "false"
    assert env["JENNIFER_RATE_LIMIT_PER_SEC"] == "2.0"
    assert env["JENNIFER_BANK_RATE_LIMIT_PER_SEC"] == "2.0"
    assert env["APM_GATEWAY_HOST"] == "127.0.0.1" and env["REDIS_PORT"] == "1"
    e2 = gw.gateway_env(base, port=5, spool=tmp_path, tokens={}, sources=["common"])
    assert not any(k.startswith("JENNIFER_") and k.endswith(("_API_URL", "_API_TOKEN"))
                   for k in e2)


def test_parse_audit_line() -> None:
    ok = ("2026-10-12 17:27:32,247 [INFO] apm_gateway.audit: apm audit: tool=apm_app_health "
          "target=was01_a elapsed_ms=12.5 api_calls=3 sources=bank:1,common:2 rows=4 "
          "investigation_id=- thread_id=th-1 principal=chat job_id=- search=name")
    entry = gw.parse_audit_line(ok)
    assert entry == {"tool": "apm_app_health", "elapsed_ms": 12.5, "api_calls": 3,
                     "sources": {"bank": 1, "common": 2}, "rows": 4, "error": None,
                     "thread_id": "th-1", "principal": "chat", "job_id": None}
    err = ("[WARNING] apm audit: tool=apm_metrics target=a b elapsed_ms=10001.0 api_calls=1 "
           "sources=- error=apm_quota_exceeded investigation_id=- thread_id=- principal=chat "
           "job_id=j-1")
    entry = gw.parse_audit_line(err)
    assert entry is not None and entry["error"] == "apm_quota_exceeded"
    assert entry["thread_id"] is None and entry["sources"] == {} and entry["job_id"] == "j-1"
    assert gw.parse_audit_line("unrelated") is None


def test_audit_tail_partial_line(tmp_path: Path) -> None:
    path = tmp_path / "g.log"
    line = ("apm audit: tool=t target=- elapsed_ms=1.0 api_calls=1 sources=- rows=1 "
            "investigation_id=- thread_id=a principal=chat job_id=-")
    path.write_text(line + "\n" + line[:20], encoding="utf-8")
    tail = gw.AuditTail(path)
    entries, offset = tail.read(0)
    assert len(entries) == 1 and offset == len(line) + 1
    assert tail.collect(0, "b") == []


def test_audit_tail_folds_http_lines(tmp_path: Path) -> None:
    """응답 줄(DEBUG)은 다음 감사 항목 `http` 칸으로 접히고, 끝에 남은 것은 합성 항목
    (collect 제외)."""
    path = tmp_path / "g.log"
    audit = ("apm audit: tool=apm_fleet target=- elapsed_ms=9.0 api_calls=3 sources=bank:3 "
             "rows=4 investigation_id=- thread_id=a principal=bench job_id=-")
    http = "apm_gateway.adapters.jennifer.client: apm http: path=/api/x keys=['a'] status={} " \
           "elapsed_ms=2.4"
    path.write_text("\n".join([http.format(200), http.format(429), audit, http.format(200)])
                    + "\n", encoding="utf-8")
    tail = gw.AuditTail(path)
    entries, _ = tail.read(0)
    assert entries[0]["http"] == {"responses": 2, "statuses": {"200": 1, "429": 1}}
    assert entries[1]["tool"] is None and entries[1]["http"]["responses"] == 1
    assert [e["tool"] for e in tail.collect(0)] == ["apm_fleet"]
    env = gw.gateway_env({}, port=5, spool=tmp_path, tokens={}, sources=[])
    assert env["APM_GATEWAY_LOG_LEVEL"] == gw.BENCH_LOG_LEVEL == "DEBUG"


# --- 관측 조립 -----------------------------------------------------------------------------


def test_build_observed_maps_capture_audit_done() -> None:
    raw_task = {"task_id": "t1", "agent": "apm_query", "views": ["apm.app_health", "apm.bad"],
                "targets": [{"text": "was01_a", "kind": "instance"}], "sources": []}
    final_task = {"task_id": "t1", "agent": "apm_query", "views": ["apm.app_health"],
                  "targets": [{"text": "was01_a", "kind": "instance"}]}
    captures = [
        {"kind": "apm_plan_raw", "tasks": [raw_task, {"task_id": "t2", "agent": "x"}]},
        {"kind": "investigation_result", "thread_id": "th", "task": final_task,
         "answer_lines": ["판정"], "degraded_reason": None,
         "meta": {"selection": {"retried": False, "result": "planned"},
                  "views": ["apm.app_health"],
                  "targets": {"texts": [{"text": "was01_a", "kind": "instance", "instances": 1,
                                         "suggestions": []}],
                              "instances": [{"apm_instance_name": "was01_a"}]},
                  "inserted_steps": [],
                  "provenance": [{"view": "apm.app_health", "tool": "apm_app_health",
                                  "hostname": None, "reference": None,
                                  "args": {"instance_id": 1001}, "rows": 1,
                                  "window": {"start": "s", "end": "e", "minutes": 30}}]}},
    ]
    audit = [{"tool": "apm_instance_map", "rows": 3, "error": None, "elapsed_ms": 1.0,
              "api_calls": 1, "sources": {"common": 1}},
             {"tool": "apm_app_health", "rows": 1, "error": None, "elapsed_ms": 2.0,
              "api_calls": 2, "sources": {"common": 2}}]
    done = {"thread_id": "th", "response": "응답", "time_resolution": {"present": True},
            "disclosures": [{"kind": "bridge", "text": "t"}],
            "plan_summary": {"tasks": [{"agent": "apm_query"}]},
            "apm_source_scope": {"ids": ["common"]}}
    obs = type("Obs", (), {"response": "", "node_elapsed_ms": {"a": 1.0}, "wall_ms": 12.3,
                           "error": None})()
    observed = dv.build_observed(obs=obs, done=done, captures=captures, audit=audit)
    assert observed["agents"] == ["apm_query"] and observed["apm_called"] is True
    assert observed["plan_raw"] == [raw_task] and observed["plan_final"] == [final_task]
    assert observed["sanitize_changes"] == [{
        "task_id": "t1", "field": "views", "before": ["apm.app_health", "apm.bad"],
        "after": ["apm.app_health"], "reason": "sanitize"}]
    assert observed["targets"][0]["resolved"] is True
    assert observed["targets"][0]["selected"] == ["was01_a"]
    first, second = observed["calls"]
    assert first["tool"] == "apm_instance_map" and first["args"] == {}
    assert second["view"] == "apm.app_health" and second["args"] == {"instance_id": 1001}
    assert second["window"] == {"start": "s", "end": "e", "minutes": 30}
    assert second["sources"] == ["common"] and second["api_calls"] == 2
    assert observed["answer_lines"] == ["판정"] and observed["response"] == "응답"
    assert observed["clarify_kind"] is None and observed["refused"] is False
    assert observed["time_resolution"] == {"present": True}


def test_build_observed_required_arg_clarify() -> None:
    done = {"disclosures": [{"kind": "apm_unresolved_condition",
                             "text": "보기: 필요한 조건(PID)이 없거나 해석하지 못해 …"}],
            "plan_summary": {"tasks": [{"agent": "apm_query"}]}}
    obs = type("Obs", (), {"response": "", "node_elapsed_ms": {}, "wall_ms": 1, "error": None})()
    observed = dv.build_observed(obs=obs, done=done, captures=[], audit=[])
    assert observed["clarify_kind"] == "required_arg" and observed["apm_called"] is False


def _obs() -> object:
    return type("Obs", (), {"response": "", "node_elapsed_ms": {}, "wall_ms": 1, "error": None})()


def test_build_observed_time_clarify_is_not_refusal() -> None:
    from src.domain.query_time import clarify_message

    done = {"response": clarify_message("unresolved"),
            "time_resolution": {"clarify": "unresolved", "slot_status": "absent"},
            "plan_summary": {"tasks": []}}
    observed = dv.build_observed(obs=_obs(), done=done, captures=[], audit=[])
    assert observed["clarify_kind"] == "time" and observed["refused"] is False
    assert observed["clarify_text"] == clarify_message("unresolved")
    # 해석 흔적만 있고 응답이 되묻기 문구가 아니면 시간 되묻기가 아니다
    done = {**done, "response": "다른 응답"}
    observed = dv.build_observed(obs=_obs(), done=done, captures=[], audit=[])
    assert observed["clarify_kind"] is None and observed["refused"] is True


def test_build_observed_zone_pre_gate() -> None:
    done = {"response": "어느 존?", "plan_summary": {"plan_path": "pre_gate", "tasks": []},
            "clarification": {"kind": "zone_select", "question": "어느 존을 조회할까요?"}}
    observed = dv.build_observed(obs=_obs(), done=done, captures=[], audit=[])
    assert observed["clarify_kind"] == "zone" and observed["refused"] is False
    assert observed["clarify_text"] == "어느 존을 조회할까요?"
    other = {**done, "clarification": {"kind": "scope_select", "question": "q"}}
    assert dv.build_observed(obs=_obs(), done=other, captures=[],
                             audit=[])["clarify_kind"] == "scope_select"


def test_mlx_blockers() -> None:
    assert dv.mlx_blockers("http://10.0.0.5:8080/v1", "m", run_blockers=lambda _c: [])[0] \
        .startswith("MLX 주소가 루프백이 아니다")
    seen: list[object] = []

    def ok(cfg: object) -> list[object]:
        seen.append(cfg)
        return []

    assert dv.mlx_blockers("http://127.0.0.1:8080/v1", "m", run_blockers=ok) == []
    cfg = seen[0]
    assert cfg.llm.provider == cfg.orchestrator.provider == "mlx"  # type: ignore[attr-defined]
    check = type("Check", (), {"key": "MLX 서버(워커)", "observed": "도달 실패"})()
    out = dv.mlx_blockers("http://127.0.0.1:8080/v1", "m", run_blockers=lambda _c: [check])
    assert out == ["MLX 점검 중단: MLX 서버(워커)=도달 실패"]


def test_run_mlx_blocked_starts_nothing(placeholders: dv.Placeholders) -> None:
    with pytest.raises(dv.LLMBlockedError):
        dv.run([], env="e1", placeholders=placeholders, llm="mlx",
               mlx_check=lambda _u, _m: ["MLX 점검 중단"], say=lambda _t: None)
    with pytest.raises(ValueError):
        dv.run([], env="e2", placeholders=placeholders, llm="mlx", view_server_version="5.6.2.18",
               say=lambda _t: None)


def test_collect_inventory_names_and_errors() -> None:
    def call_tool(name: str, args: dict) -> dict:
        if name == "apm_instance_map":
            return {"rows": [{"instance_name": "a", "domain_name": "d", "host_name": "h",
                              "ip_address": "10.0.0.1"}, {"instance_name": "b", "host_name": ""}]}
        raise TimeoutError

    hosts: dict[str, list[str]] = {}
    names, calls, errors = dv.collect_inventory(call_tool, hosts=hosts)
    assert names == ["a", "b", "d", "h"] and calls == 2 and errors == ["apm_business:TimeoutError"]
    assert hosts == {"a": ["h"]}  # 호스트 빈 행은 대응 없음


def test_admin_token_uses_configured_operator(monkeypatch: pytest.MonkeyPatch) -> None:
    """인증이 켜진 본체(E2)의 설정 에코는 운영자 토큰이 있어야 열린다 — 설정 계정으로 받는다."""
    from scripts.scenario import client as sc
    from scripts.scenario import runner as sr

    seen: list[tuple[str, str]] = []

    def fake_login(self: object, user: str, password: str) -> tuple[str, None]:
        seen.append((user, password))
        return "admin-jwt", None

    monkeypatch.setattr(sr, "resolve_admin_credentials", lambda _u, _p: ("ops", "pw"))
    monkeypatch.setattr(sc.ScenarioClient, "admin_login", fake_login)
    assert dv._admin_token(1) == ("admin-jwt", None) and seen == [("ops", "pw")]

    monkeypatch.setattr(sr, "resolve_admin_credentials", lambda _u, _p: (None, None))
    token, error = dv._admin_token(1)
    assert token is None and error is not None and "ADMIN_USERNAME" in error


# --- E1 종단 ------------------------------------------------------------------------------


@pytest.mark.serial
def test_e1_end_to_end(placeholders: dv.Placeholders) -> None:
    pytest.importorskip("mcp")
    wanted = ("APM-INST-list", "APM-HEALTH-inst", "APM-SRC-none-all")
    scenarios = [s for s in load_scenarios() if s.id in wanted]
    lines: list[str] = []
    records, meta = dv.run(scenarios, env="e1", placeholders=placeholders, say=lines.append)
    assert meta["tier"] == dv.EXPECTED_TIER
    assert meta["aborted"] is None and meta["apm_observed_turns"] > 0
    assert meta["gateway_var_absent"] is True
    assert meta["guard"]["baseline_ms"] is not None
    assert meta["inventory"]["calls"] == 2 and "was01_a" in meta["inventory_names"]
    assert meta["planes"] == {"worker": "fake", "orchestrator": "fake"} and meta["llm"] == "fake"
    by_key = {(r["scenario_id"], r["turn"]): r for r in records}
    assert set(by_key) == {("APM-INST-list", 0), ("APM-HEALTH-inst", 0),
                           ("APM-SRC-none-all", 0), ("APM-SRC-none-all", 1)}
    health = by_key[("APM-HEALTH-inst", 0)]
    assert health["observed"]["apm_called"] and health["thread_id"]
    assert health["observed"]["targets"][0]["selected"] == ["was01_a"]
    assert any(c["tool"] == "apm_app_health" for c in health["observed"]["calls"])
    assert health["extra"]["source_auto_answer"]["source"] == "common"  # 되묻기 자동 답
    ask = by_key[("APM-SRC-none-all", 0)]
    assert ask["observed"]["clarify_kind"] == "source" and not ask["observed"]["apm_called"]
    assert "source_auto_answer" not in ask["extra"]  # 되묻기가 정답 — 답하지 않는다
    answer = by_key[("APM-SRC-none-all", 1)]  # 「전체」 글 답
    assert answer["thread_id"] == ask["thread_id"]  # 같은 스레드
    assert answer["observed"]["apm_source_scope"]["ids"] == ["*"]
    assert answer["observed"]["apm_called"]
