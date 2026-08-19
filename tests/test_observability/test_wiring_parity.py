"""트레이스 배선 대칭 테스트 (D-141).

그래프 실행 진입점은 HTTP 4곳(`ainvoke` 2 + `astream_events` 2)과 CLI 1곳이다.
라우트마다 배선하면 한 곳만 빠져도 그 경로의 관측이 비므로, 실제 배선은
**미들웨어 1곳 + CLI 1곳**으로 수렴시켰다. 그 구조가 유지되는지 여기서 고정한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _read(rel: str) -> str:
    return Path(rel).read_text(encoding="utf-8")


class TestSinglePointWiring:
    def test_middleware_starts_and_flushes(self):
        """모든 HTTP 요청이 지나는 미들웨어가 수명을 관리한다."""
        src = _read("src/api/middleware/audit_middleware.py")

        assert "start_request(" in src, "미들웨어가 트레이스를 시작하지 않음"
        assert "flush_if_failed(" in src, "미들웨어가 트레이스를 덤프하지 않음"

    def test_flush_is_in_finally(self):
        """예외로 빠져나온 요청도 덤프된다 — 그때가 가장 진단이 필요하다."""
        src = _read("src/api/middleware/audit_middleware.py")
        finally_block = src.split("finally:", 1)[1]

        assert "flush_if_failed(" in finally_block

    def test_cli_entrypoint_is_wired(self):
        """CLI는 미들웨어를 지나지 않으므로 별도 배선이 필요하다."""
        src = _read("src/main.py")

        assert "start_request(" in src and "flush_if_failed(" in src

    def test_routes_do_not_duplicate_wiring(self):
        """라우트에 개별 배선이 없어야 한다 (중복 덤프·비대칭 방지)."""
        src = _read("src/api/routes/query.py")

        assert "flush_if_failed" not in src, (
            "라우트에 개별 flush가 배선되면 미들웨어와 중복되고, "
            "일부 라우트만 배선되는 비대칭이 재발한다"
        )


class TestGraphProxyWiring:
    def test_single_stategraph_construction(self):
        """`StateGraph` 생성이 한 곳뿐이어야 프록시 배선이 대칭이다."""
        src = _read("src/graph.py")

        assert src.count("StateGraph(AgentState)") == 1

    def test_proxy_wraps_construction(self):
        src = _read("src/graph.py")

        assert "TracedGraph(" in src

    def test_no_manual_traced_calls_in_graph(self):
        """노드별 수동 래핑이 없어야 한다 (프록시가 일괄 처리)."""
        src = _read("src/graph.py")

        assert "traced(" not in src, "노드별 수동 래핑은 누락 위험이 있다 — 프록시로 일원화"


class TestConfigGate:
    @pytest.mark.parametrize("path", [
        "src/api/middleware/audit_middleware.py",
        "src/main.py",
        "src/graph.py",
    ])
    def test_gated_by_trace_enabled(self, path):
        """모든 배선 지점이 `trace_enabled` 게이트를 통과한다 (off면 비트동일)."""
        assert "trace_enabled" in _read(path), f"{path}에 설정 게이트가 없음"
