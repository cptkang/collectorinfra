"""공통 pytest fixture 모듈.

테스트 전반에서 사용되는 mock state, mock config, 샘플 데이터를 정의한다.
과금 외부 API 무단 호출을 막는 전역 가드(D-127)도 여기서 설치한다.
"""

import ipaddress
import os
import socket

import pytest

from src.config import (
    AppConfig,
    DBHubConfig,
    LLMConfig,
    QueryConfig,
    SecurityConfig,
    ServerConfig,
)
from src.state import AgentState, create_initial_state

# ──────────────────────────────────────────────────────────────
# D-127 전역 가드 — 승인(RUN_E2E=1) 없는 외부 접속 차단
#
# 실 LLM 호출은 과금이 발생하므로 사용자 승인 없이 실행하면 안 된다. 파일 단위
# skip 게이팅만으로는 새 테스트가 게이트 밖에서 호출을 내는 것을 막지 못하므로
# (2026-07-29 실측: 게이트 누락 17건이 기본 스위트에서 실 Gemini 호출),
# 소켓 레벨에서 **공인 IP 접속 자체를 차단**한다. connect 이전에 예외를 던지므로
# 패킷이 나가지 않는다.
#
# - 루프백·사설 대역(docker 픽스처 PG 5433/5434·Redis 6380·Prometheus 9190 등)은 허용
# - 차단 시 어떤 테스트가 어디로 나가려 했는지 명시해 실패시킨다(침묵 skip 금지)
# - RUN_E2E=1(사용자 승인)이면 가드를 설치하지 않는다
# ──────────────────────────────────────────────────────────────

RUN_E2E = os.environ.get("RUN_E2E") == "1"

_CURRENT_TEST = {"nodeid": "<세션 초기화 단계>"}
_BLOCKED_ATTEMPTS: list[tuple[str, str, int]] = []


class ExternalConnectionBlocked(RuntimeError):
    """승인 없이 외부(공인 IP)로 접속하려 할 때 발생한다."""


def _target_addresses(address: object) -> list[str]:
    """connect 인자에서 검사할 IP 목록을 뽑는다(호스트명은 해석한다)."""
    if not isinstance(address, tuple) or len(address) < 2:
        return []  # AF_UNIX 등 — 검사 대상 아님
    host = address[0]
    if not isinstance(host, str) or not host:
        return []
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    try:
        resolved = socket.getaddrinfo(host, None)
    except OSError:
        return []
    return [info[4][0] for info in resolved]


def _is_external(ip: str) -> bool:
    """공인 IP인지 판정한다(루프백·사설·링크로컬은 내부로 본다)."""
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_unspecified
    )


def _install_external_connection_guard() -> None:
    """socket.connect / connect_ex를 감싸 외부 접속을 차단한다."""
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def check(address: object) -> None:
        external = [ip for ip in _target_addresses(address) if _is_external(ip)]
        if not external:
            return
        host, port = address[0], address[1]  # type: ignore[index]
        _BLOCKED_ATTEMPTS.append((_CURRENT_TEST["nodeid"], str(host), int(port)))
        raise ExternalConnectionBlocked(
            f"승인 없는 외부 접속을 차단했습니다(D-127): {host}:{port} "
            f"(해석된 공인 IP: {', '.join(external)})\n"
            f"  테스트: {_CURRENT_TEST['nodeid']}\n"
            "  과금 API 호출 가능성이 있는 테스트는 @pytest.mark.live_llm으로 표시하고, "
            "실행이 필요하면 사용자 승인 후 RUN_E2E=1로 실행하세요."
        )

    def guarded_connect(self, address):  # type: ignore[no-untyped-def]
        check(address)
        return real_connect(self, address)

    def guarded_connect_ex(self, address):  # type: ignore[no-untyped-def]
        check(address)
        return real_connect_ex(self, address)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex


if not RUN_E2E:
    _install_external_connection_guard()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live_llm: 실 LLM(과금 가능) 호출 — 사용자 승인(RUN_E2E=1) 시에만 실행 (D-127)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """RUN_E2E 미설정 시 live_llm 표시 테스트를 건너뛴다."""
    if RUN_E2E:
        return
    skip_live = pytest.mark.skip(
        reason="실 LLM 호출(과금 가능) — 사용자 승인 필수(D-127): RUN_E2E=1 시에만 실행",
    )
    for item in items:
        if item.get_closest_marker("live_llm"):
            item.add_marker(skip_live)


def pytest_runtest_logstart(nodeid: str, location: object) -> None:
    _CURRENT_TEST["nodeid"] = nodeid


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:  # type: ignore[no-untyped-def]
    """차단된 외부 접속 시도를 요약한다(침묵 금지)."""
    if not _BLOCKED_ATTEMPTS:
        return
    terminalreporter.write_sep("=", "D-127 외부 접속 차단", red=True)
    for nodeid, host, port in _BLOCKED_ATTEMPTS:
        terminalreporter.write_line(f"  {host}:{port}  <- {nodeid}")


@pytest.fixture
def sample_schema_info() -> dict:
    """테스트용 스키마 정보를 반환한다."""
    return {
        "tables": {
            "servers": {
                "columns": [
                    {"name": "id", "type": "integer", "nullable": False, "primary_key": True, "foreign_key": False, "references": None},
                    {"name": "hostname", "type": "varchar(255)", "nullable": False, "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "ip_address", "type": "varchar(45)", "nullable": False, "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "os", "type": "varchar(100)", "nullable": True, "primary_key": False, "foreign_key": False, "references": None},
                ],
                "row_count_estimate": 50,
                "sample_data": [{"id": 1, "hostname": "web-01", "ip_address": "10.0.0.1", "os": "Ubuntu 22.04"}],
            },
            "cpu_metrics": {
                "columns": [
                    {"name": "id", "type": "integer", "nullable": False, "primary_key": True, "foreign_key": False, "references": None},
                    {"name": "server_id", "type": "integer", "nullable": False, "primary_key": False, "foreign_key": True, "references": "servers.id"},
                    {"name": "usage_pct", "type": "double", "nullable": True, "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "timestamp", "type": "timestamp", "nullable": False, "primary_key": False, "foreign_key": False, "references": None},
                ],
                "row_count_estimate": 500000,
                "sample_data": [],
            },
            "memory_metrics": {
                "columns": [
                    {"name": "id", "type": "integer", "nullable": False, "primary_key": True, "foreign_key": False, "references": None},
                    {"name": "server_id", "type": "integer", "nullable": False, "primary_key": False, "foreign_key": True, "references": "servers.id"},
                    {"name": "usage_pct", "type": "double", "nullable": True, "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "total_gb", "type": "double", "nullable": True, "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "timestamp", "type": "timestamp", "nullable": False, "primary_key": False, "foreign_key": False, "references": None},
                ],
                "row_count_estimate": 500000,
                "sample_data": [],
            },
        },
        "relationships": [
            {"from": "cpu_metrics.server_id", "to": "servers.id"},
            {"from": "memory_metrics.server_id", "to": "servers.id"},
        ],
    }


@pytest.fixture
def sample_state(sample_schema_info) -> AgentState:
    """기본 테스트용 AgentState를 반환한다."""
    state = create_initial_state(user_query="CPU 사용률이 80% 이상인 서버 목록")
    state["schema_info"] = sample_schema_info
    state["parsed_requirements"] = {
        "query_targets": ["서버", "CPU"],
        "filter_conditions": [{"field": "usage_pct", "op": ">=", "value": 80}],
        "time_range": None,
        "output_format": "text",
        "aggregation": None,
        "limit": None,
        "original_query": "CPU 사용률이 80% 이상인 서버 목록",
    }
    state["relevant_tables"] = ["cpu_metrics", "servers"]
    return state


@pytest.fixture
def mock_config() -> AppConfig:
    """테스트용 AppConfig를 반환한다."""
    return AppConfig(
        llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
        dbhub=DBHubConfig(server_url="http://localhost:9099/sse", source_name="infra_db", mcp_call_timeout=60),
        query=QueryConfig(max_retry_count=3, default_limit=1000),
        security=SecurityConfig(sensitive_columns=["password", "secret", "token", "api_key"], mask_pattern="***MASKED***"),
        server=ServerConfig(host="0.0.0.0", port=8000),
        checkpoint_backend="sqlite",
        checkpoint_db_url=":memory:",
    )


@pytest.fixture
def sample_query_results() -> list[dict]:
    """테스트용 쿼리 결과를 반환한다."""
    return [
        {"hostname": "web-01", "ip_address": "10.0.0.1", "usage_pct": 85.3},
        {"hostname": "web-02", "ip_address": "10.0.0.2", "usage_pct": 92.1},
        {"hostname": "db-01", "ip_address": "10.0.1.1", "usage_pct": 88.7},
    ]
