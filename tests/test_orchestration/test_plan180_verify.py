"""plans/180 W1 독립 검증 — `apm_query` Facade의 patch seam이 순수 이동 뒤에도 실효인가(D-337 ②).

고정하는 성질:
  1. Facade(`aq`)에서 patch한 seam 5종(`_SESSION_FACTORY`·`link_hostnames`·`link_business_names`·
     `get_registry`·`render_view_rows`)과 `aq.jobs.poll_job`이 `run_apm_query` 경로에 실제로 닿는다.
  2. `apm/` 모듈은 seam 이름을 자기 전역으로 들고 있지 않다(가지면 Facade patch가 공허해진다).
  3. 역방향 — 테스트·스크립트가 Facade에서 patch/대입하는 이름 중 `apm/` 모듈이 전역으로 읽는
     이름이 0이다(있으면 그 patch는 옮긴 코드에 닿지 않는다).
  4. 표 원소 seam — `_COERCERS`·`_CALL_BUILDERS`의 원소 함수는 표와 같은 모듈에 있다.
게이트웨이는 모의 MCP 세션이다(LLM·DB·네트워크 0).
"""

from __future__ import annotations

import ast
import importlib
import logging
from pathlib import Path
from typing import Any

import pytest

from src.infrastructure import apm_job_store as store_mod
from src.infrastructure.apm_job_store import ApmJobStore
from src.orchestration import apm_jobs
from src.orchestration import apm_query as aq
from src.orchestration.entity_link import LINKED, LinkEntry
from tests.test_orchestration.test_plan134_w2_verify import (
    _ALL_REPLIES,
    NOW,
    _cfg,
    _env,
    _Gateway,
    _isolated,
    _run,
    _SelectLLM,
)

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "src/orchestration/apm"
APM_MODULES = [
    importlib.import_module("src.orchestration.apm" if p.stem == "__init__"
                            else f"src.orchestration.apm.{p.stem}")
    for p in sorted(PACKAGE.glob("*.py"))
]
SEAMS = ("_SESSION_FACTORY", "link_hostnames", "link_business_names", "get_registry",
         "render_view_rows")
FACADE = "src.orchestration.apm_query"


@pytest.fixture
def gateway(monkeypatch):
    def install(replies: dict) -> _Gateway:
        gw = _Gateway(replies)
        monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
        return gw

    return install


@pytest.fixture(autouse=True)
def _memory_job_store(monkeypatch):
    monkeypatch.setattr(store_mod, "_STORE", ApmJobStore(None))


# ── 1. Facade patch → run_apm_query 경로 반영 ─────────────────────────────────

@pytest.mark.asyncio
async def test_session_factory_patch_on_facade_reaches_the_run(gateway) -> None:
    gw = gateway(_ALL_REPLIES)
    res = await _run(["apm.app_health"], "web01")
    assert [n for n, _ in gw.calls] == ["apm_app_health"]
    assert gw.calls[0][1]["hostname"] == "web01"
    assert res["source_status"][0]["status"] == "ok"


@pytest.mark.asyncio
async def test_link_hostnames_patch_on_facade_reaches_the_run(gateway, monkeypatch) -> None:
    seen: list[Any] = []

    async def linked(refs, *, consumer, app_config):
        seen.append([r.server_name for r in refs])
        return (["web07"],
                [LinkEntry("결제서버", "server_name", "E2", LINKED, grade="one", value="web07")],
                [])

    monkeypatch.setattr(aq, "link_hostnames", linked)
    gw = gateway(_ALL_REPLIES)
    isolated = _isolated()
    isolated["parsed_requirements"]["filter_conditions"] = [
        {"field": "server_name", "op": "=", "value": "결제서버"}]
    await aq.run_apm_query({"task_id": "t1", "agent": "apm_query", "views": ["apm.app_health"],
                            "sub_query": "질의"}, isolated, llm=None, app_config=_cfg(), now=NOW)
    assert seen == [["결제서버"]]
    assert [a.get("hostname") for a in gw.named("apm_app_health")] == ["web07"]


class _P180BoomError(RuntimeError):
    pass


@pytest.mark.asyncio
async def test_link_business_names_patch_on_facade_reaches_the_run(gateway, monkeypatch,
                                                                   caplog) -> None:
    calls: list[Any] = []

    async def boom(texts, **kw):
        calls.append(list(texts))
        raise _P180BoomError("x")

    monkeypatch.setattr(aq, "link_business_names", boom)
    gateway(_ALL_REPLIES)
    caplog.set_level(logging.WARNING, logger=FACADE)
    await aq.run_apm_query(
        {"task_id": "t1", "agent": "apm_query", "views": ["apm.app_health"], "sub_query": "질의",
         "targets": [{"text": "결제업무", "kind": "business"}]},
        _isolated(), llm=None, app_config=_cfg(), now=NOW)
    assert calls == [["결제업무"]]
    assert any("업무명 간선(E6) 실패: _P180BoomError" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_get_registry_patch_on_facade_reaches_the_run(gateway, monkeypatch) -> None:
    real = aq.get_registry
    count = {"n": 0}

    def counted():
        count["n"] += 1
        return real()

    monkeypatch.setattr(aq, "get_registry", counted)
    gateway(_ALL_REPLIES)
    await _run(["apm.app_health"], "web01")
    assert count["n"] >= 1


@pytest.mark.asyncio
async def test_render_view_rows_patch_on_facade_reaches_the_selection_prompt(gateway,
                                                                           monkeypatch) -> None:
    monkeypatch.setattr(aq, "render_view_rows", lambda: "P180_SENTINEL_ROWS")
    gw = gateway(_ALL_REPLIES)
    llm = _SelectLLM({"views": ["apm.changes"]})
    await _run([], "web01", sub_query="변경 이력", areas=["was_change_detection"], llm=llm)
    assert llm.messages, "선택 재시도 LLM 을 불렀다"
    assert "P180_SENTINEL_ROWS" in llm.messages[0][0].content
    assert [n for n, _ in gw.calls] == ["apm_source_changes"]


@pytest.mark.asyncio
async def test_jobs_poll_job_patch_via_facade_reaches_moved_code(gateway, monkeypatch) -> None:
    job_id = "c" * 32  # 게이트웨이 작업 id 형식(hex 32)
    handle = {"job_id": job_id, "state": "running"}
    gateway({"apm_app_health": _env("apm_app_health", [], job=handle)})
    polled: list[str] = []

    async def poll_job(session, job_id, owner, *, until, interval=None):
        polled.append(job_id)
        return {"job": handle}

    monkeypatch.setattr(aq.jobs, "poll_job", poll_job)
    res = await _run(["apm.app_health"], "web01")
    assert polled == [job_id]
    assert res["accepted_jobs"] == [job_id]


# ── 2. apm/ 모듈은 seam 이름을 자기 전역으로 들지 않는다 ─────────────────────────

@pytest.mark.parametrize("module", APM_MODULES, ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_apm_modules_hold_no_seam_globals(module) -> None:
    held = [name for name in SEAMS if name in vars(module)]
    assert held == [], f"{module.__name__}가 {held}를 전역으로 가진다 — Facade patch 공허"
    jobs = vars(module).get("jobs")
    assert jobs is None or jobs is apm_jobs, "jobs 별칭은 apm_jobs 모듈 객체 그대로"
    assert "poll_job" not in vars(module), "poll_job 은 `jobs.poll_job` 속성 접근만"


# ── 3. 역방향 — Facade에 거는 patch가 옮긴 코드의 전역 이름이 아니다 ────────────────

def _facade_patched_names() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for root in ("tests", "noise_gate/tests", "scripts"):
        for path in sorted((REPO / root).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "apm_query" not in text:
                continue
            tree = ast.parse(text)
            aliases: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "src.orchestration":
                    aliases |= {a.asname or a.name for a in node.names if a.name == "apm_query"}
                elif isinstance(node, ast.Import):
                    aliases |= {a.asname for a in node.names if a.name == FACADE and a.asname}
            where = path.relative_to(REPO).as_posix()

            def is_facade(n: ast.AST) -> bool:
                return (isinstance(n, ast.Name) and n.id in aliases) or ast.unparse(n) == FACADE

            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and node.args:
                    last = ast.unparse(node.func).rsplit(".", 1)[-1]
                    first = node.args[0]
                    if last in {"setattr", "object", "delattr"} and is_facade(first) \
                            and len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                        out.setdefault(str(node.args[1].value), []).append(where)
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                                and arg.value.startswith(FACADE + "."):
                            out.setdefault(arg.value[len(FACADE) + 1:].split(".")[0],
                                           []).append(where)
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Attribute) and is_facade(target.value):
                            out.setdefault(target.attr, []).append(where)
    return out


def _apm_global_loads() -> dict[str, set[str]]:
    loads: dict[str, set[str]] = {}
    for path in sorted(PACKAGE.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                loads.setdefault(node.id, set()).add(path.stem)
    return loads


def test_facade_patches_do_not_target_names_read_by_moved_code() -> None:
    patched = _facade_patched_names()
    assert "_SESSION_FACTORY" in patched, "스캐너 자체 점검 — 알려진 seam 을 찾아야 한다"
    loads = _apm_global_loads()
    vacuous = {name: (sorted(loads[name]), patched[name][:3])
               for name in patched if name in loads}
    assert vacuous == {}, f"Facade patch가 옮긴 코드에 닿지 않는다: {vacuous}"


# ── 4. 표 원소 seam — 표와 원소 함수가 같은 모듈 ──────────────────────────────────

def test_dispatch_tables_live_with_their_element_functions() -> None:
    from src.orchestration.apm import args, builders

    assert aq._COERCERS is args._COERCERS and aq._CALL_BUILDERS is builders._CALL_BUILDERS
    assert {f.__module__ for f in args._COERCERS.values()} == {args.__name__}
    assert {f.__module__ for f in builders._CALL_BUILDERS.values()} == {builders.__name__}
    for name, fn in args._COERCERS.items():
        assert getattr(args, fn.__name__) is fn, name
    for name, fn in builders._CALL_BUILDERS.items():
        assert getattr(builders, fn.__name__) is fn, name
