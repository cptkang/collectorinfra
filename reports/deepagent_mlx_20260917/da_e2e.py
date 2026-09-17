"""plans/49 트랙 B 종단 검증 — 실제 build_graph()(사다리 1단 deep_agent)를 로컬 mlx로 돈다.

도구 내부는 실제 워커 파이프라인(mlx) + MCP(9099) 로컬 샌드박스 DB다. 읽기 전용.
과금 평면이 하나라도 해석되거나 루프백 mlx가 아니면, 또는 1단이 아니면 호출 전에 거부한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path

REPO = Path("/Users/cptkang/AIOps/collectorinfra")
sys.path.insert(0, str(REPO))
os.chdir(REPO)
for _k in ("LLM_GEMINI_API_KEY", "GOOGLE_API_KEY", "ORCHESTRATOR_API_KEY"):
    os.environ[_k] = ""
os.environ["ALARM_ENABLED"] = "false"

QUERIES = {
    "E1_simple": "서버 수를 조회해줘",
    "E2_two_metrics": "CPU 사용률 상위 3개 서버와 메모리 사용률 상위 3개 서버를 각각 알려줘",
    "E3_dependent": "현재 발생 중인 심각(severity 3) 알람이 있는 서버를 찾고, 그 서버들의 IP와 OS 종류를 조회해줘",
    "E4_general": "이 시스템으로 어떤 조회를 할 수 있어?",
}


class _Cap(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.lines: list[str] = []

    def emit(self, record):
        msg = record.getMessage()
        keys = ("deepagents 도구", "deep_agent", "deepagents 에이전트", "결정적 교정", "순차 게이트",
                "finish_reason", "SQL 검증 실패", "재시도", "라우팅")
        if any(k in msg for k in keys):
            self.lines.append(f"{record.levelname} {record.name}: {msg[:400]}")


async def one(graph, qid, query, timeout, cap):
    from da_probe import mlx_polite_gate
    from src.state import create_initial_state

    gate = mlx_polite_gate()
    tid = f"da-e2e-{qid}-{uuid.uuid4().hex[:8]}"
    cfg = {"configurable": {"thread_id": tid}, "recursion_limit": 80}
    state = create_initial_state(user_query=query, thread_id=tid)
    nodes: list[str] = []
    rec: dict = {"id": qid, "query": query, "thread_id": tid, "gate": gate}
    cap.lines = []
    t0 = time.monotonic()

    async def drive():
        async for chunk in graph.astream(state, cfg, stream_mode="updates"):
            nodes.extend(chunk)

    try:
        await asyncio.wait_for(drive(), timeout=timeout)
    except asyncio.TimeoutError:
        rec["error"] = f"timeout {timeout}s"
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"{type(e).__module__}.{type(e).__name__}: {str(e)[:400]}"
    rec["elapsed_s"] = round(time.monotonic() - t0, 1)
    snap = await graph.aget_state(cfg)
    v = snap.values or {}
    rec["nodes"] = nodes
    rec["final_response"] = v.get("final_response") or ""
    rec["task_plan"] = [{k: t.get(k) for k in ("task_id", "agent", "sub_query", "input_from", "status")}
                        for t in (v.get("task_plan") or [])]
    rec["log"] = list(cap.lines)
    print(f"  {qid}: {rec['elapsed_s']}s nodes={nodes} resp_len={len(rec['final_response'])} {rec.get('error') or ''}",
          flush=True)
    return rec


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default=",".join(QUERIES))
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    from langgraph.checkpoint.memory import InMemorySaver

    from scripts.scenario.preflight import external_planes
    from src.config import load_config
    from src.graph import build_graph
    from src.observability.ladder import current_ladder

    load_config.cache_clear()
    cfg = load_config()
    planes = external_planes(cfg.llm.provider, cfg.orchestrator.provider)
    urls = [cfg.llm.mlx_base_url or "", cfg.orchestrator.base_url or ""]
    if planes or cfg.llm.provider != "mlx" or cfg.orchestrator.provider != "mlx" \
            or not all(u.startswith("http://127.0.0.1:") for u in urls) or cfg.llm.gemini_api_key:
        print(f"거부: 비과금 루프백 mlx가 아니다 — planes={planes} urls={urls}")
        raise SystemExit(3)

    cap = _Cap()
    logging.getLogger("src").addHandler(cap)
    logging.getLogger("src").setLevel(logging.INFO)

    graph = build_graph(cfg, checkpointer=InMemorySaver())
    ladder = current_ladder() or {}
    if ladder.get("tier") != "deep_agent":
        print(f"거부: 1단 확정이 아니다 — {ladder}")
        raise SystemExit(4)
    meta = {"ladder": ladder, "active_db_ids": cfg.multi_db.get_active_db_ids(),
            "model": [cfg.llm.mlx_model, cfg.orchestrator.model],
            "recursion_limit": cfg.orchestrator.recursion_limit,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    print(json.dumps(meta, ensure_ascii=False), flush=True)
    out = Path(a.out)
    recs = []
    for qid in a.ids.split(","):
        recs.append(await one(graph, qid, QUERIES[qid], a.timeout, cap))
        out.write_text(json.dumps({"meta": meta, "records": recs}, ensure_ascii=False, indent=1, default=str))
    meta["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    out.write_text(json.dumps({"meta": meta, "records": recs}, ensure_ascii=False, indent=1, default=str))
    print(f"저장: {out}")


if __name__ == "__main__":
    asyncio.run(main())
