"""plans/183 독립 검증 — seam 실행 경로 반영 · 단독 프로세스 import · 가드 비공허성.

- `aq._SESSION_FACTORY`를 바꾼 값이 `ApmJobService._call` 실행 경로까지 닿는다(호출 시점 조회).
- 각 진입 모듈을 새 프로세스에서 단독 import해도 순환·부분 초기화 오류가 없다.
- 가드(`test_plan183_import_scc`)는 옛 지연 import를 되살리면 실패한다(공허하지 않다).
게이트웨이는 모의 세션이다(실 게이트웨이·LLM 0).
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from src.config import DBHubConfig, SecurityConfig
from src.orchestration import apm_jobs
from src.orchestration import apm_query as aq
from src.orchestration.apm import common

from tests.test_orchestration import test_plan183_import_scc as guard

REPO = guard.REPO


class _Gateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            yield self

        return open_

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        reply = {"rows": [], "row_count": 0, "tool": name}
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(reply))], isError=False)


def _config() -> Any:
    return SimpleNamespace(
        auth=SimpleNamespace(enabled=True), security=SecurityConfig(_env_file=None),
        dbhub=DBHubConfig(_env_file=None, source_endpoints={"apm": "http://127.0.0.1:9096/sse"},
                          source_tokens='{"apm": "gw-token"}', source_call_timeout=10.0))


# ── 1. seam 실행 경로 ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_patched_session_factory_reaches_call(monkeypatch: pytest.MonkeyPatch) -> None:
    gw = _Gateway()
    monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
    env = await apm_jobs.ApmJobService(_config())._call("apm_job_status", {"job_id": "x"})
    assert gw.calls == [("apm_job_status", {"job_id": "x"})]
    assert env.get("tool") == "apm_job_status"


@pytest.mark.asyncio
async def test_repatch_between_calls_is_seen(monkeypatch: pytest.MonkeyPatch) -> None:
    """공급자는 값이 아니라 이름을 호출 시점에 읽는다 — 두 번째 patch도 반영된다."""
    first, second = _Gateway(), _Gateway()
    svc = apm_jobs.ApmJobService(_config())
    monkeypatch.setattr(aq, "_SESSION_FACTORY", first.factory())
    await svc._call("apm_job_status", {"job_id": "a"})
    monkeypatch.setattr(aq, "_SESSION_FACTORY", second.factory())
    await svc._call("apm_job_status", {"job_id": "b"})
    assert [a["job_id"] for _, a in first.calls] == ["a"]
    assert [a["job_id"] for _, a in second.calls] == ["b"]


def test_registered_provider_reads_apm_query_globals() -> None:
    provider = apm_jobs.session_factory_provider
    assert provider is not apm_jobs._no_session_factory
    assert provider.__globals__ is vars(aq)
    assert apm_jobs._no_session_factory() is None


def test_endpoint_names_are_the_same_objects() -> None:
    assert apm_jobs.apm_endpoint is common.apm_endpoint is aq.apm_endpoint
    assert apm_jobs.APM_SYSTEM == common.APM_SYSTEM == aq.APM_SYSTEM == "apm"


# ── 2. 단독 프로세스 import ─────────────────────────────────────────────────

@pytest.mark.parametrize("module", [
    "src.orchestration.apm_jobs",
    "src.orchestration.apm.common",
    "src.orchestration.apm_query",
    "src.graph",
    "src.api.server",
    "src.api.routes.apm_jobs",
])
def test_standalone_import_succeeds(module: str) -> None:
    code = (
        f"import {module}\n"
        "import sys\n"
        "j = sys.modules['src.orchestration.apm_jobs']\n"
        "aq = sys.modules.get('src.orchestration.apm_query')\n"
        "assert j.session_factory_provider() is None\n"
        "assert aq is None or j.session_factory_provider() is aq._SESSION_FACTORY\n"
        "print('ok')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().endswith("ok")


# ── 3. 가드 비공허성 · 숨은 우회 ───────────────────────────────────────────

_OLD_DEFERRED = (
    "class S:\n"
    "    async def _call(self):\n"
    "        from src.orchestration import apm_query as aq\n"
)


def test_guard_detects_old_deferred_import() -> None:
    targets = guard._import_targets(ast.parse(_OLD_DEFERRED), "src.orchestration.apm_jobs")
    assert "src.orchestration.apm_query" in targets


def test_restoring_reverse_edge_reproduces_the_scc() -> None:
    g = guard._graph()
    g["src.orchestration.apm_jobs"] = g["src.orchestration.apm_jobs"] | {
        "src.orchestration.apm_query"}
    sccs = guard._sccs(g)
    assert len(sccs) == 1 and len(sccs[0]) == 8


def test_apm_jobs_has_no_dynamic_import_bypass() -> None:
    src = (REPO / "src/orchestration/apm_jobs.py").read_text(encoding="utf-8")
    for token in ("importlib", "sys.modules", "__import__", "apm_query\"", "apm_query'"):
        assert token not in src, token
