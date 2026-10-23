"""컴포넌트 설정 관리 공용 모듈 사본 일치 (plans/191 · D-345 ⑥ · D-339 ② 전례).

정본 `sre_agent/sre_agent/interface/config_admin.py`와 별도 프로세스 패키지 사본 4개의 파일 바이트가
같아야 한다. 패키지를 import하지 않고 경로만 읽는다(양방향 import 0 경계 유지).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CANONICAL = REPO / "sre_agent" / "sre_agent" / "interface" / "config_admin.py"
COPIES = (
    REPO / "apm_gateway" / "apm_gateway" / "interface" / "config_admin.py",
    REPO / "mcp_server" / "mcp_server" / "config_admin.py",
    REPO / "noise_gate" / "alarm_server" / "config_admin.py",
    REPO / "fabrix_proxy" / "fabrix_proxy" / "config_admin.py",
)


@pytest.mark.parametrize("copy", COPIES, ids=lambda p: str(p.relative_to(REPO)))
def test_copy_is_byte_identical_to_canonical(copy: Path) -> None:
    assert copy.read_bytes() == CANONICAL.read_bytes(), (
        f"{copy.relative_to(REPO)}가 정본과 다르다 — 정본을 그대로 복사할 것"
    )
