"""D-127 전역 외부 접속 가드 검증 (tests/conftest.py).

승인(RUN_E2E=1) 없이 공인 IP로 나가려는 접속이 **connect 이전에** 차단되는지 확인한다.
차단 경로는 패킷을 보내지 않으므로 이 테스트 자체도 외부 호출을 하지 않는다.
"""

from __future__ import annotations

import socket

import pytest

from tests.conftest import RUN_E2E, ExternalConnectionBlocked, _is_external

pytestmark = pytest.mark.skipif(
    RUN_E2E, reason="RUN_E2E=1(사용자 승인)이면 가드를 설치하지 않는다",
)

# 공인 IP 리터럴 — 가드가 connect 이전에 차단하므로 실제 접속은 일어나지 않는다.
_PUBLIC_IP = "142.250.0.1"


def test_external_connect_is_blocked():
    """공인 IP 접속은 예외로 차단된다(침묵 통과 금지)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(ExternalConnectionBlocked) as exc_info:
            sock.connect((_PUBLIC_IP, 443))
    finally:
        sock.close()

    message = str(exc_info.value)
    assert _PUBLIC_IP in message          # 어디로 나가려 했는지
    assert "test_external_connect_is_blocked" in message  # 어떤 테스트가
    assert "RUN_E2E=1" in message         # 어떻게 승인받는지


def test_external_connect_ex_is_blocked():
    """connect_ex(비차단 소켓·asyncio 경로)도 동일하게 차단된다."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(ExternalConnectionBlocked):
            sock.connect_ex((_PUBLIC_IP, 443))
    finally:
        sock.close()


def test_external_hostname_is_resolved_and_blocked():
    """호스트명으로 접속해도 해석된 공인 IP 기준으로 차단된다."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(ExternalConnectionBlocked):
            sock.connect(("generativelanguage.googleapis.com", 443))
    except socket.gaierror:
        pytest.skip("DNS 해석 불가 환경 — 호스트명 경로 검증 생략")
    finally:
        sock.close()


def test_loopback_connect_is_allowed():
    """루프백 접속은 그대로 허용된다(로컬 픽스처 무영향)."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client.connect(server.getsockname())  # 예외가 나면 안 된다
        assert client.getpeername()[0] == "127.0.0.1"
    finally:
        client.close()
        server.close()


@pytest.mark.parametrize(
    ("ip", "external"),
    [
        ("127.0.0.1", False),      # 루프백
        ("10.17.217.82", False),   # 사설 A
        ("172.17.0.2", False),     # 도커 브리지
        ("192.168.0.10", False),   # 사설 C
        ("169.254.1.1", False),    # 링크로컬
        ("142.250.0.1", True),     # 공인
        ("8.8.8.8", True),         # 공인
    ],
)
def test_is_external_classification(ip, external):
    """내부/외부 판정이 사설·루프백·링크로컬을 내부로 본다."""
    assert _is_external(ip) is external


def test_unix_socket_address_is_ignored():
    """AF_UNIX 등 튜플이 아닌 주소는 검사 대상이 아니다."""
    from tests.conftest import _target_addresses

    assert _target_addresses("/tmp/some.sock") == []
    assert _target_addresses(("127.0.0.1", 6379)) == ["127.0.0.1"]


def test_coverage_llm_dual_mode_selection():
    """이중 모드 선택(D-127): 자동 실행=스텁, 승인(RUN_E2E=1)=None(실 LLM 내부 획득)."""
    from tests.conftest import ColumnCoverageStubLLM, coverage_llm_for_mode

    assert isinstance(coverage_llm_for_mode(False), ColumnCoverageStubLLM)
    assert coverage_llm_for_mode(True) is None
