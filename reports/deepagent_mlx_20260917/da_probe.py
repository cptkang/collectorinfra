"""plans/49(deepagents 트랙 B) 성공기준을 로컬 mlx로 검증하는 프로브.

모드:
  tools        — LLM 0회. 오케스트레이터가 실제로 보는 도구 목록·시스템 프롬프트 크기(스파이 모델)
  ladder       — LLM 0회. build_graph() 사다리 확정(환경변수로 변형 — 서브프로세스에서 1회씩)
  runtime_fail — LLM 0회. 기동 후 오케스트레이터가 죽었을 때 run_deep_agent 동작(R-B10)
  orch         — 실 mlx. 도구 핸들러만 대역(스텁)으로 바꿔 오케스트레이터 위임·재계획 판단을 측정.
                 최종 응답은 실제 run_deep_agent → result_aggregator(워커=mlx)가 만든다.
과금 평면이 하나라도 해석되거나 루프백 mlx가 아니면 LLM 호출 전에 거부한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

REPO = Path("/Users/cptkang/AIOps/collectorinfra")
sys.path.insert(0, str(REPO))
os.chdir(REPO)
# 과금 폴백 차단: .encenv의 Gemini 키를 OS env 빈 값으로 덮는다(OS env가 env_file보다 우선).
for _k in ("LLM_GEMINI_API_KEY", "GOOGLE_API_KEY", "ORCHESTRATOR_API_KEY"):
    os.environ[_k] = ""
os.environ.setdefault("ALARM_ENABLED", "false")


def guard():
    from scripts.scenario.preflight import external_planes
    from src.config import load_config

    load_config.cache_clear()
    cfg = load_config()
    planes = external_planes(cfg.llm.provider, cfg.orchestrator.provider)
    urls = [cfg.llm.mlx_base_url or "", cfg.orchestrator.base_url or ""]
    ok = (not planes and cfg.llm.provider == "mlx" and cfg.orchestrator.provider == "mlx"
          and all(u.startswith("http://127.0.0.1:") for u in urls)
          and not cfg.llm.gemini_api_key and not cfg.orchestrator.api_key)
    if not ok:
        print(f"거부: 비과금 루프백 mlx가 아니다 — planes={planes} urls={urls}")
        raise SystemExit(3)
    return cfg


# ───────────────────────── tools ─────────────────────────
async def mode_tools():
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    import src.llm as llm_mod
    from src.orchestration.deep_agent import build_deep_agent

    cap: dict = {}

    class Spy(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "spy"

        def bind_tools(self, tools, **kw):
            cap["tools"] = tools
            cap["bind_kw"] = {k: str(v) for k, v in kw.items()}
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kw):
            cap["messages"] = messages
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    cfg = guard()
    llm_mod.create_orchestrator_llm = lambda _c: Spy()  # build_deep_agent가 함수 안에서 import한다
    agent = build_deep_agent(cfg, worker_llm=Spy())
    await agent.ainvoke({"messages": [{"role": "user", "content": "테스트"}]})

    def _name(t):
        if isinstance(t, dict):
            return t.get("name") or (t.get("function") or {}).get("name")
        return getattr(t, "name", str(t))

    sys_text = "".join(
        m.content if isinstance(m.content, str) else json.dumps(m.content, ensure_ascii=False)
        for m in cap.get("messages", []) if m.type == "system"
    )
    tool_schema_chars = sum(
        len(json.dumps(getattr(t, "args_schema", None).model_json_schema() if getattr(t, "args_schema", None) and hasattr(getattr(t, "args_schema"), "model_json_schema") else (t if isinstance(t, dict) else {}), ensure_ascii=False))
        + len(getattr(t, "description", "") or "")
        for t in cap.get("tools", [])
    )
    print(json.dumps({
        "tool_names": [_name(t) for t in cap.get("tools", [])],
        "tool_count": len(cap.get("tools", [])),
        "bind_kw": cap.get("bind_kw"),
        "system_prompt_chars": len(sys_text),
        "tool_schema_desc_chars": tool_schema_chars,
        "system_prompt_head": sys_text[:300],
    }, ensure_ascii=False, indent=1))


# ───────────────────────── ladder ─────────────────────────
async def mode_ladder():
    from langgraph.checkpoint.memory import InMemorySaver

    from src.graph import build_graph
    from src.observability.ladder import current_ladder

    cfg = guard() if os.environ.get("PROBE_SKIP_GUARD") != "1" else None
    if cfg is None:
        from src.config import load_config
        load_config.cache_clear()
        cfg = load_config()
    t0 = time.monotonic()
    graph = build_graph(cfg, checkpointer=InMemorySaver())
    nodes = sorted(graph.get_graph().nodes)
    print(json.dumps({
        "variant": os.environ.get("PROBE_VARIANT"),
        "enable_deepagents_package": cfg.enable_deepagents_package,
        "orchestrator_base_url": cfg.orchestrator.base_url,
        "ladder": current_ladder(),
        "has_deep_agent_node": "deep_agent" in nodes,
        "has_semantic_router_node": "semantic_router" in nodes,
        "has_intent_planner_node": "intent_planner" in nodes,
        "build_s": round(time.monotonic() - t0, 2),
    }, ensure_ascii=False))


# ───────────────────────── runtime_fail ─────────────────────────
async def mode_runtime_fail():
    from src.llm import create_llm
    from src.orchestration.deep_agent import run_deep_agent

    cfg = guard()
    dead = cfg.model_copy(update={"orchestrator": cfg.orchestrator.model_copy(
        update={"base_url": "http://127.0.0.1:9/v1", "timeout": 5})})
    t0 = time.monotonic()
    rec: dict = {}
    try:
        out = await run_deep_agent({"user_query": "서버 수를 조회해줘", "thread_id": "probe-dead"},
                                   app_config=dead, worker_llm=create_llm(cfg))
        rec["returned"] = {k: str(v)[:200] for k, v in out.items()}
    except Exception as e:  # noqa: BLE001
        rec["raised"] = f"{type(e).__module__}.{type(e).__name__}: {str(e)[:200]}"
    rec["elapsed_s"] = round(time.monotonic() - t0, 1)
    print(json.dumps(rec, ensure_ascii=False, indent=1))


# ───────────────────────── orch (실 mlx) ─────────────────────────
CPU_HOT = [{"hostname": "web-01", "cpu_usage": 96.3}, {"hostname": "web-02", "cpu_usage": 88.1},
           {"hostname": "web-03", "cpu_usage": 71.4}]
CPU_COOL = [{"hostname": "web-01", "cpu_usage": 72.0}, {"hostname": "web-02", "cpu_usage": 65.5},
            {"hostname": "web-03", "cpu_usage": 61.2}]
CPU_90 = [{"hostname": "web-01", "cpu_usage": 95.2}, {"hostname": "web-02", "cpu_usage": 91.0}]
MEM_TOP = [{"hostname": "db-01", "mem_usage": 93.0}, {"hostname": "db-02", "mem_usage": 90.4},
           {"hostname": "db-03", "mem_usage": 87.9}]
ALARMS = {"web-01": [{"hostname": "web-01", "severity": 3, "alarm_msg": "CPU 사용률 임계 초과", "occurred_at": "2026-09-17 09:12"}],
          "web-02": [{"hostname": "web-02", "severity": 2, "alarm_msg": "응답 지연", "occurred_at": "2026-09-17 08:40"}]}
EVENT_ROWS = [{"hostname": "app-07", "severity": 3, "alarm_msg": "프로세스 다운", "occurred_at": "2026-09-17 10:02"}]

SCENARIOS = {
    # key: (질의, cpu 스텁 선택, 기대 판정 이름)
    "S1_single": ("CPU 사용률 상위 3개 서버를 알려줘", "hot"),
    "S2_independent": ("CPU 사용률 상위 3개 서버와 메모리 사용률 상위 3개 서버를 각각 알려줘", "hot"),
    "S3_data_dependent": ("CPU 사용률이 90% 이상인 서버를 찾아서, 그 서버들의 최근 알람 이력을 조회해줘", "90"),
    "S4a_conditional_pos": ("CPU 사용률 상위 3개 서버를 조회하고, 그중 사용률이 90%를 넘는 서버가 있으면 그 서버의 최근 알람 이력도 조회해줘", "hot"),
    "S4b_conditional_neg": ("CPU 사용률 상위 3개 서버를 조회하고, 그중 사용률이 90%를 넘는 서버가 있으면 그 서버의 최근 알람 이력도 조회해줘", "cool"),
    "S5_empty_requery": ("호스트명에 web이 들어간 서버의 디스크 사용률을 조회하고, 결과가 없으면 호스트명에 was가 들어간 서버로 다시 조회해줘", "hot"),
    "S6_general": ("이 시스템으로 어떤 조회를 할 수 있어?", "hot"),
    "S7_event_routing": ("최근 event가 발생한 서버 목록 알려줘", "hot"),
}


def _data_result(rows, summary, sql="SELECT /* stub */ 1"):
    return {"organized_data": {"summary": summary, "rows": rows, "is_sufficient": True},
            "query_results": rows, "generated_sql": sql, "source": [{"db_id": "polestar"}]}


def make_stubs(calls: list, cpu_variant: str):
    async def data_query(task, isolated, *, llm, app_config):
        sq = task.get("sub_query", "")
        calls.append({"agent": task["agent"], "sub_query": sq, "input_from": task.get("input_from"),
                      "prior_rows_keys": sorted((isolated.get("prior_rows") or {}).keys()) if isinstance(isolated.get("prior_rows"), dict) else None})
        low = sq.lower()
        if task["agent"] == "alarm_query":
            hosts = [h for h in ALARMS if h in low]
            if hosts:
                rows = [r for h in hosts for r in ALARMS[h]]
            elif task.get("input_from"):
                rows = [r for h in ALARMS for r in ALARMS[h]]
            else:
                rows = EVENT_ROWS
            return _data_result(rows, f"알람 {len(rows)}건")
        if "디스크" in sq or "disk" in low:
            if "was" in low:
                rows = [{"hostname": "was-01", "disk_usage": 81.5}, {"hostname": "was-02", "disk_usage": 64.0}]
                return _data_result(rows, "호스트명 was 포함 서버 디스크 사용률 2건")
            return _data_result([], "조건에 맞는 서버가 없습니다(0건)")
        if "메모리" in sq or "memory" in low:
            return _data_result(MEM_TOP, "메모리 사용률 상위 3건")
        rows = {"hot": CPU_HOT, "cool": CPU_COOL, "90": CPU_90}[cpu_variant]
        if "90" in sq and cpu_variant == "90":
            rows = CPU_90
        return _data_result(rows, f"CPU 사용률 조회 {len(rows)}건")

    async def general(task, isolated, *, llm, app_config):
        calls.append({"agent": task["agent"], "sub_query": task.get("sub_query", ""), "input_from": task.get("input_from")})
        return {"final_response": "[스텁 general_answer] 서버 사양·사용량·성능 통계, 알람 이력, 실시간 프로세스를 조회할 수 있습니다."}

    async def generic(task, isolated, *, llm, app_config):
        calls.append({"agent": task["agent"], "sub_query": task.get("sub_query", ""), "input_from": task.get("input_from")})
        return {"final_response": f"[스텁 {task['agent']}] 처리됨"}

    return data_query, general, generic


def mlx_polite_gate(max_wait_s: float = 600) -> str:
    """시나리오 시작 전: ①다른 클라이언트가 8080에 붙어 있으면 대기(상한) ②1토큰 생성으로 생존 확인.

    좀비 서버(/health 200·생성 무응답)면 SystemExit — 건당 타임아웃까지 헛돌지 않는다.
    """
    import subprocess
    import urllib.request

    me = str(os.getpid())
    t0 = time.monotonic()
    while True:
        out = subprocess.run(["lsof", "-nP", "-iTCP:8080", "-sTCP:ESTABLISHED"],
                             capture_output=True, text=True).stdout.splitlines()[1:]
        servers = set(subprocess.run(["pgrep", "-f", "mlx_lm.server"], capture_output=True,
                                     text=True).stdout.split())
        others = {ln.split()[1] for ln in out if ln.split()[1] not in servers and ln.split()[1] != me}
        if not others or time.monotonic() - t0 > max_wait_s:
            break
        time.sleep(10)
    waited = round(time.monotonic() - t0)
    req = urllib.request.Request(
        "http://127.0.0.1:8080/v1/chat/completions",
        data=json.dumps({"model": "default_model", "messages": [{"role": "user", "content": "hi"}],
                         "max_tokens": 1}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:  # noqa: BLE001
        print(f"중단: MLX 생성 무응답(좀비 의심) — {e}", flush=True)
        raise SystemExit(5)
    return f"waited={waited}s others={sorted(others) if others else []}"


def _msg_tool_calls(messages):
    seq = []
    for m in messages or []:
        for tc in getattr(m, "tool_calls", None) or []:
            args = tc.get("args") or {}
            seq.append({"name": tc.get("name"), "args": {k: (str(v)[:160]) for k, v in args.items()}})
    return seq


async def mode_orch(keys: list[str], repeat: int, out: Path, timeout: float):
    import src.orchestration.deep_agent as da_mod
    from src.llm import create_llm
    from src.orchestration import subagents as sa_mod

    cfg = guard()
    worker = create_llm(cfg)
    original_registry = dict(sa_mod.SUBAGENT_REGISTRY)
    real_build = da_mod.build_deep_agent
    records = []
    if out.exists():
        records = json.loads(out.read_text())

    for n in range(repeat):
        for key in keys:
            query, variant = SCENARIOS[key]
            calls: list = []
            invokes: list = []
            data_q, general, generic = make_stubs(calls, variant)
            for name, spec in original_registry.items():
                h = data_q if name in ("data_query", "alarm_query") else general if name == "general_inference" else generic
                sa_mod.SUBAGENT_REGISTRY[name] = replace(spec, handler=h)

            def build_spy(*a, **kw):
                agent = real_build(*a, **kw)

                class _Proxy:
                    async def ainvoke(self, inp, config=None):
                        t = time.monotonic()
                        r = await agent.ainvoke(inp, config=config)
                        invokes.append({"result": r, "s": round(time.monotonic() - t, 1)})
                        return r
                return _Proxy()

            da_mod.build_deep_agent = build_spy
            rec: dict = {"key": key, "round": n + 1, "query": query, "variant": variant}
            rec["gate"] = mlx_polite_gate()
            t0 = time.monotonic()
            try:
                res = await asyncio.wait_for(
                    da_mod.run_deep_agent({"user_query": query, "thread_id": f"probe-{key}-{n}"},
                                          app_config=cfg, worker_llm=worker),
                    timeout=timeout)
                rec["final_response"] = res.get("final_response", "")
            except Exception as e:  # noqa: BLE001
                rec["error"] = f"{type(e).__name__}: {str(e)[:300]}"
            finally:
                da_mod.build_deep_agent = real_build
                sa_mod.SUBAGENT_REGISTRY.clear()
                sa_mod.SUBAGENT_REGISTRY.update(original_registry)
            rec["elapsed_s"] = round(time.monotonic() - t0, 1)
            rec["stub_calls"] = calls
            rec["ainvoke_count"] = len(invokes)
            rec["ainvoke_s"] = [i["s"] for i in invokes]
            last = invokes[-1]["result"] if invokes else {}
            rec["orchestrator_tool_calls"] = [tc for i in invokes for tc in _msg_tool_calls(i["result"].get("messages"))]
            rec["todos"] = last.get("todos") if isinstance(last, dict) else None
            msgs = last.get("messages") if isinstance(last, dict) else []
            rec["message_count"] = len(msgs or [])
            ai_last = next((m for m in reversed(msgs or []) if getattr(m, "type", "") == "ai"), None)
            rec["orchestrator_last_text"] = (ai_last.content if ai_last is not None and isinstance(ai_last.content, str) else "")[:400]
            records.append(rec)
            out.write_text(json.dumps(records, ensure_ascii=False, indent=1, default=str))
            names = [c["name"] for c in rec["orchestrator_tool_calls"]]
            print(f"[{n + 1}/{repeat}] {key}: {rec['elapsed_s']}s tools={names} stubs={[c['agent'] for c in calls]} "
                  f"invokes={rec['ainvoke_count']} {rec.get('error') or ''}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("tools", "ladder", "runtime_fail", "orch"))
    ap.add_argument("--keys", default=",".join(SCENARIOS))
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("src.orchestration").setLevel(logging.INFO)
    if a.mode == "tools":
        asyncio.run(mode_tools())
    elif a.mode == "ladder":
        asyncio.run(mode_ladder())
    elif a.mode == "runtime_fail":
        asyncio.run(mode_runtime_fail())
    else:
        asyncio.run(mode_orch(a.keys.split(","), a.repeat, Path(a.out), a.timeout))


if __name__ == "__main__":
    main()
