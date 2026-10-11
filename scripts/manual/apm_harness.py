"""제니퍼 다중 소스 장면 전용 캡처 서버 (plans/147 — captures.yaml ``server: apm-harness``).

왜 따로 두는가: 제니퍼 소스 되묻기·승계·칩은 소스 ≥2인 APM 활성 배포에서만 나오고 답을
조립하는 것이 파이프라인(집계기)이라, 녹화 재생 서버(``run_capture``)로는 장면을 만들 수 없다.
그래서 실제 질의·스코프·관리 라우트와 결정적 그래프(분해 대역 → 실제 ``agent_orchestrator`` →
실제 ``result_aggregator``) · 프로세스 안 모의 게이트웨이로 띄운다. LLM 호출 0 · 네트워크 0.
소스 표는 저장소 ``config/db_registry.yaml``(``solutions[apm].sources[]``)를 그대로 읽는다.

인증은 대역이다 — 토큰이 있으면 로그인한 사용자(관리 라우트는 관리자)로 본다. 캡처 장면은
``/login``에서 ``localStorage.user_token``을 넣고 들어간다(captures.yaml).

    python -m scripts.manual.apm_harness [--port 18987]
    uv run --no-project --with playwright==1.63.0 --with pyyaml \\
        python scripts/manual/capture.py --base http://127.0.0.1:18987 --server apm-harness

띄운 프로세스는 자기 PID 만 종료한다.
"""

from __future__ import annotations

import argparse
import json
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "src" / "static"
PORT = 18987
USER: dict[str, Any] = {"sub": "demo_user", "role": "user"}
ADMIN: dict[str, Any] = {"sub": "admin", "role": "admin"}

#: 관리 화면 예시 — 은행존·공동존은 정상, 레거시는 설정됐으나 도달 실패(선택지에서 빼지 않는다)
_HEALTH_ROWS = [
    {"source_id": "bank", "status": "ok", "jennifer_configured": True,
     "jennifer_reachable": True, "domain_count": 3},
    {"source_id": "common", "status": "ok", "jennifer_configured": True,
     "jennifer_reachable": True, "domain_count": 5},
    {"source_id": "legacy", "status": "degraded", "jennifer_configured": True,
     "jennifer_reachable": False, "domain_count": None},
]


class _Gateway:
    """모의 게이트웨이 — 조회는 고른 소스(없으면 전 소스)마다 인스턴스 1행, 헬스는 위 표."""

    def factory(self) -> Any:
        @asynccontextmanager
        async def open_(url: str, headers: Any) -> Any:
            yield self

        return open_

    async def call_tool(self, name: str, arguments: dict) -> SimpleNamespace:
        if name == "gateway_health":
            env: dict[str, Any] = {"rows": _HEALTH_ROWS, "row_count": len(_HEALTH_ROWS),
                                   "status": "ok", "tool": name}
        else:
            picked = arguments.get("source_ids") or [r["source_id"] for r in _HEALTH_ROWS]
            env = {"rows": [{"source_id": s, "instance_id": 11, "instance_name": f"{s}-was1",
                             "tps": 3.5} for s in picked],
                   "row_count": len(picked), "queried_at": "2026-10-08T10:00:00+09:00",
                   "source_kind": "apm_api", "source": "apm", "tool": name, "limits": [],
                   "sources": [{"source_id": s, "status": "ok", "reason": ""} for s in picked]}
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(env))], isError=False)


def _config() -> Any:
    from src.config import AppConfig, DBHubConfig, ServerConfig

    cfg = AppConfig(
        _env_file=None, db_backend="direct", db_connection_string="", log_level="WARNING",
        server=ServerConfig(query_timeout=60, file_query_timeout=120),
        dbhub=DBHubConfig(_env_file=None, server_url="http://127.0.0.1:9099/sse",
                          source_endpoints={"apm": "http://127.0.0.1:9096/sse"}),
    )
    cfg.server.sse_progress_events = True
    return cfg


def _graph(cfg: Any) -> Any:
    """분해 대역(질의 원문 → APM task 1개 · 서버 web01) → 실제 오케스트레이터 → 실제 집계기."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, StateGraph

    from src.orchestration.agent_orchestrator import agent_orchestrator
    from src.orchestration.result_aggregator import result_aggregator
    from src.state import AgentState

    async def planner(state: dict) -> dict:
        query = state["user_query"]
        return {"task_plan": [{"task_id": "t1", "agent": "apm_query", "sub_query": query,
                               "views": ["apm.app_health"], "status": "pending",
                               "depends_on": []}],
                "task_results": {}, "replan_count": 0,
                "parsed_requirements": {"original_query": query, "filter_conditions": [
                    {"field": "hostname", "op": "=", "value": "web01"}]}}

    async def orchestrate(state: dict) -> dict:
        return await agent_orchestrator(state, llm=FakeListChatModel(responses=["x"]),
                                        app_config=cfg)

    async def aggregate(state: dict) -> dict:
        return await result_aggregator(state, llm=FakeListChatModel(responses=["요약"]),
                                       app_config=cfg)

    builder = StateGraph(AgentState)
    builder.add_node("intent_planner", planner)
    builder.add_node("agent_orchestrator", orchestrate)
    builder.add_node("result_aggregator", aggregate)
    builder.set_entry_point("intent_planner")
    builder.add_edge("intent_planner", "agent_orchestrator")
    builder.add_edge("agent_orchestrator", "result_aggregator")
    builder.add_edge("result_aggregator", END)
    return builder.compile(checkpointer=MemorySaver())


def build_app() -> Any:
    from fastapi import FastAPI, Request
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    from src.api.dependencies import require_admin_user, require_user
    from src.api.routes import admin as admin_routes
    from src.api.routes import query as query_routes
    from src.api.routes import scope as scope_routes
    from src.infrastructure import apm_job_store as store_mod
    from src.infrastructure.apm_job_store import ApmJobStore
    from src.orchestration import apm_query as aq
    from src.routing.apm_source_health import reconcile
    from src.routing.apm_source_select import set_available_apm_sources
    from src.routing.registry import get_registry

    store_mod._STORE = ApmJobStore(None)
    aq._SESSION_FACTORY = _Gateway().factory()
    cfg = _config()
    app = FastAPI()
    app.state.config = cfg
    app.state.graph = _graph(cfg)
    # 기동 정합 점검과 같은 결과(server.py `_check_apm_sources`) — 모의 헬스 표로 만든다
    report = replace(reconcile(get_registry().sources_of(aq.APM_SYSTEM),
                               {"rows": _HEALTH_ROWS, "status": "ok"}),
                     checked_at="2026-10-08T09:00:00+09:00")
    app.state.apm_source_report = report
    set_available_apm_sources(report.available)

    @app.get("/api/v1/auth/status")
    async def auth_status(request: Request) -> dict:
        if request.headers.get("authorization"):
            return {"auth_enabled": True,
                    "user": {"user_id": "demo_user", "username": "홍길동", "role": "user"}}
        return {"auth_enabled": True, "user": None}

    @app.get("/api/v1/health")
    async def health() -> dict:
        return {"status": "healthy", "db_connected": True}

    # 관리 화면 토큰 확인(admin.js verifyToken) — 관리 라우트보다 먼저 등록해 이 대역이 잡힌다
    @app.get("/api/v1/admin/users")
    async def admin_users() -> list:
        return []

    app.include_router(query_routes.router, prefix="/api/v1")
    app.include_router(scope_routes.router, prefix="/api/v1")
    app.include_router(admin_routes.router, prefix="/api/v1")
    app.dependency_overrides[require_user] = lambda: dict(USER)
    app.dependency_overrides[require_admin_user] = lambda: dict(ADMIN)

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/login")
    async def login() -> FileResponse:
        return FileResponse(STATIC / "login.html")

    @app.get("/admin")
    async def admin_page() -> FileResponse:
        return FileResponse(STATIC / "admin" / "dashboard.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=PORT)
    a = ap.parse_args()
    os.environ.setdefault("SCHEMA_CACHE_ENABLED", "false")
    os.chdir(REPO)

    import uvicorn

    uvicorn.run(build_app(), host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
