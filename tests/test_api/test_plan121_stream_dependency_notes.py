"""plans/121 TP-11.8 — 스트림 `done`·astream `response_data`에도 순차 처리 경과 노트(4경로 대칭).

종전에는 비스트림 두 경로와 스트림 폴백(`ainvoke`)의 응답 조립에만 `dependency_notes`가 있어,
측정 정본인 스트림 하네스가 게이트·브리지 경과를 구조로 받지 못했다(D-205·D-066 비대칭).

고정하는 계약:
① 그래프 경로 SSE done 4곳(텍스트 astream·폴백 · 파일 astream·폴백)이 같은 필드를 싣는다.
② astream `response_data` 2곳은 종료 노드 델타가 아니라 누적 상태(`_scope_state`)에서 싣는다.
③ 노트가 없으면 키를 싣지 않는다 — done 이벤트 바이트 불변.
LLM·DB 0.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.api.routes.query import _dependency_notes_field

_QUERY_PY = Path(__file__).resolve().parents[2] / "src" / "api" / "routes" / "query.py"


@pytest.fixture(scope="module")
def query_py() -> str:
    return _QUERY_PY.read_text(encoding="utf-8")


def test_done_events_all_carry_notes_field(query_py):
    assert query_py.count("**_dependency_notes_field(response_data),") == 4
    # done 4곳은 db_scope 줄 바로 뒤에 있다(같은 4곳 — test_db_scope_contract 선례)
    assert query_py.count(
        '"db_scope": response_data.get("db_scope"),  # D-205\n'
    ) == 4


def test_astream_response_data_uses_accumulated_state(query_py):
    assert query_py.count("**_dependency_notes_field(_scope_state),") == 2
    assert "_dependency_notes_field(output)" not in query_py


@pytest.mark.parametrize("state", [{}, {"dependency_notes": None}, {"dependency_notes": []}])
def test_absent_notes_add_no_key(state):
    assert _dependency_notes_field(state) == {}
    base = {"type": "done", "response": "r"}
    assert json.dumps({**base, **_dependency_notes_field(state)}) == json.dumps(base)


def test_present_notes_are_carried_as_is():
    notes = [{"kind": "gate", "task_id": "t2", "detail": "선행 0건"}]
    assert _dependency_notes_field({"dependency_notes": notes}) == {"dependency_notes": notes}
