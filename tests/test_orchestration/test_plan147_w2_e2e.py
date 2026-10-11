"""plans/147 W2 · AC-4 종단 — 4번째 소스를 설정(게이트웨이 `.env`)과 레지스트리 한 행만으로 붙인다.

실프로세스: 목 Open API ↔ `python -m apm_gateway`(소스 4개 `bank`·`common`·`legacy`·`east` —
`JENNIFER_SOURCES` + 소스별 URL·토큰 환경 변수만) ↔ 실제 MCP SSE ↔ `run_apm_query`. 레지스트리는 실
레지스트리에 `east` 한 행(단어 「동부」)을 더한 대역이다(코드 수정 0). 성립을 고정하는 것:
  - AC-1 단어 지목 — 「동부」면 `east`만 조회(모든 호출 `source_ids`) · 행 라벨 「동부 제니퍼」.
  - AC-2 되묻기 — 지목 없음이면 조회 0 · 선택지 4 소스 + 「전체」.
  - AC-3 승계 — 이전 선택(`east`)이 다음 질문에 그대로 쓰인다 · 「전체」 답은 4소스 모두.
실 제니퍼·외부 네트워크·실 LLM·실 DB 0.
"""

from __future__ import annotations

import pytest

from src.orchestration import apm_query as aq
from src.routing import apm_source_select as sel
from src.routing.registry import SourceSpec
from tests.test_orchestration.test_plan147_w0_repro import (
    _decompose_and_run,
    _RegistryPlus,
    _row_sources,
    launch_gateway,
    recorded,  # noqa: F401 — 픽스처 재사용
)

pytestmark = pytest.mark.apm_source_ladder

SOURCES4 = ("bank", "common", "legacy", "east")
EAST = SourceSpec("east", "동부 제니퍼", terms=("동부", "동부 제니퍼"))


@pytest.fixture(scope="module")
def gateway4(tmp_path_factory):
    with launch_gateway(tmp_path_factory, SOURCES4) as url:
        yield url


@pytest.fixture(autouse=True)
def _registry_east(monkeypatch):
    monkeypatch.setattr(aq, "get_registry", lambda: _RegistryPlus(EAST))


async def test_fourth_source_named_queries_only_that_source(gateway4, recorded) -> None:  # noqa: F811
    res = await _decompose_and_run(gateway4, "동부 WAS 응답시간", [])
    assert res["source_status"][0]["status"] in ("ok", "partial"), res.get("error") or res
    assert recorded and all(a.get("source_ids") == ["east"] for _, a in recorded), recorded
    assert _row_sources(res) == {"east"}
    assert {r["source_label"] for r in res["query_results"]} == {"동부 제니퍼"}


async def test_fourth_source_is_a_clarification_option(gateway4, recorded) -> None:  # noqa: F811
    res = await _decompose_and_run(gateway4, "WAS 응답시간 알려줘", [])
    assert recorded == []
    ask = res[aq.SOURCE_CLARIFICATION_KEY]
    assert [o["key"] for o in ask["options"]] == [*SOURCES4, sel.ALL_SOURCES]
    assert ask["options"][3]["label"] == "동부 제니퍼"


async def test_fourth_source_inherited_and_all(gateway4, recorded) -> None:  # noqa: F811
    scope = {"ids": ["east"], "basis": "answered", "last_target_sources": ["east"]}
    res = await _decompose_and_run(gateway4, "힙 사용률은?", [], apm_source_scope=scope)
    assert recorded and all(a.get("source_ids") == ["east"] for _, a in recorded), recorded
    assert _row_sources(res) == {"east"}
    recorded.clear()
    res = await _decompose_and_run(gateway4, "WAS 응답시간 알려줘", [],
                                   selected_apm_source_ids=["*"], apm_source_basis="answered")
    assert recorded and all(a.get("source_ids") == list(SOURCES4) for _, a in recorded), recorded
    assert _row_sources(res) == set(SOURCES4)
