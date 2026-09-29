"""로컬 Docker 제니퍼 통합 테스트 — `RUN_DOCKER_IT=1` 옵트인 (plans/87 §0.8 (4) · J0-L 수용 기준).

기본 스위트에서는 skip한다. 로컬 Docker 제니퍼(`testdata/jennifer/` compose · 127.0.0.1 게시)와
저장소 밖에 둔 로컬 토큰으로만 돈다 — 운영 뷰 서버에 쓰지 않는다:

    RUN_DOCKER_IT=1 JENNIFER_IT_URL=http://127.0.0.1:17900 JENNIFER_IT_TOKEN=<로컬 토큰> \
      ../.venv/bin/python -m pytest tests/test_docker_it.py -q

라이선스가 없으면 도메인이 0건이라 도구는 `source_unavailable`을 돌려주는 것이 정상이다(§0.10 #18).
"""

from __future__ import annotations

import os

import pytest
from conftest import make_tools

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DOCKER_IT") != "1" or not os.environ.get("JENNIFER_IT_URL"),
    reason="RUN_DOCKER_IT=1 + JENNIFER_IT_URL 옵트인",
)


def _tools():
    url = os.environ["JENNIFER_IT_URL"]
    assert url.startswith(("http://127.0.0.1", "http://localhost")), "로컬 Docker 전용"
    tools, _ = make_tools(
        url, extra={"JENNIFER_API_TOKEN": os.environ.get("JENNIFER_IT_TOKEN", "")}
    )
    return tools


@pytest.mark.asyncio
async def test_health_reaches_local_server():
    tools = _tools()
    out = await tools.gateway_health()
    body = out["rows"][0]
    assert body["jennifer_reachable"] is True
    assert body["status"] in ("ok", "degraded")


@pytest.mark.asyncio
async def test_tool_result_is_contract_shaped():
    from apm_gateway.domain.errors import ApmError

    tools = _tools()
    try:
        out = await tools.apm_instance_map()
    except ApmError as e:  # 라이선스 없음 → 도메인 0건
        assert e.code == "source_unavailable"
    else:
        assert out["source_kind"] == "apm_api"
