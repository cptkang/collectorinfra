"""로그 비밀값 가림 사본 일치 (plans/167 W1 · D-139).

정본 `src/utils/log_masking.py`와 독립 패키지 사본 4개의 파일 바이트가 같아야 한다.
패키지를 import하지 않고 경로만 읽는다(양방향 import 0 경계 유지).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CANONICAL = REPO / "src" / "utils" / "log_masking.py"
COPIES = (
    REPO / "mcp_server" / "mcp_server" / "log_masking.py",
    REPO / "apm_gateway" / "apm_gateway" / "adapters" / "log_masking.py",
    REPO / "sre_agent" / "sre_agent" / "infrastructure" / "log_masking.py",
    REPO / "fabrix_proxy" / "fabrix_proxy" / "log_masking.py",
)


@pytest.mark.parametrize("copy", COPIES, ids=lambda p: str(p.relative_to(REPO)))
def test_copy_is_byte_identical_to_canonical(copy: Path) -> None:
    assert copy.read_bytes() == CANONICAL.read_bytes(), (
        f"{copy.relative_to(REPO)}가 정본과 다르다 — 정본을 그대로 복사할 것"
    )
