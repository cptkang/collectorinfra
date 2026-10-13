"""lifespan 종료 — 취소한 태스크를 기다리는 블록은 CancelledError 도 삼킨다.

내부망 2026-10-12: 알람 워커가 FabriX 호출 도중 취소되자 `except Exception` 이 CancelledError
(BaseException)를 못 잡아 「Application shutdown failed」로 뒤 정리 단계를 건너뛰었다.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap

from src.api import server


def _cancelled_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "cancel"
            and isinstance(node.func.value, ast.Name)
        ):
            names.add(node.func.value.id)
    return names


def _awaited_name(try_node: ast.Try) -> str | None:
    for stmt in try_node.body:
        if (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Await)
            and isinstance(stmt.value.value, ast.Name)
        ):
            return stmt.value.value.id
    return None


def _catches_cancelled(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    return "CancelledError" in ast.unparse(handler.type) or "BaseException" in ast.unparse(
        handler.type
    )


def test_every_cancelled_task_await_swallows_cancelled_error() -> None:
    tree = ast.parse(textwrap.dedent(inspect.getsource(server.lifespan)))
    cancelled = _cancelled_names(tree)
    assert {"alarm_worker_task", "sse_bridge_task", "incident_sub_task"} <= cancelled

    checked = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            name = _awaited_name(node)
            if name in cancelled:
                checked.append(name)
                assert any(_catches_cancelled(h) for h in node.handlers), name
    assert set(checked) == cancelled


async def test_except_tuple_swallows_task_cancelled_mid_await() -> None:
    """같은 꼴의 except 가 처리 도중 취소된 태스크의 CancelledError 를 실제로 삼킨다."""
    started = asyncio.Event()

    async def _busy() -> None:
        started.set()
        await asyncio.sleep(3600)  # 외부 호출 대기 흉내 — 취소를 잡지 않는다

    task = asyncio.create_task(_busy())
    await started.wait()
    task.cancel()
    try:
        await task
    except (Exception, asyncio.CancelledError):
        pass
    assert task.cancelled()
