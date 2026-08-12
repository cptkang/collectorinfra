"""Plan 69 DRM 복호화 실기 e2e (옵트인 — RUN_DRM_E2E=1).

실행 조건: scsl.jar·KeyManager·키 파일이 있는 환경(당분간 운영계뿐, 계획서 §4.1-4).

    RUN_DRM_E2E=1 DRM_E2E_SAMPLE=/path/to/encrypted.xlsx pytest tests/e2e/test_drm_e2e.py

.env의 DRM_* 설정(enabled=true 포함)을 그대로 사용한다. 첫 실행에서 확인할 것:
-36 시 dst 생성 여부, 10MB급 파일, 업로드 경유 수신 바이트 동일성(계획서 §5).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DRM_E2E") != "1",
    reason="실기 환경 전용 — RUN_DRM_E2E=1 옵트인",
)


@pytest.fixture
def sample_path() -> Path:
    raw = os.getenv("DRM_E2E_SAMPLE", "")
    if not raw or not Path(raw).exists():
        pytest.skip("DRM_E2E_SAMPLE 환경변수에 암호화 샘플 경로를 지정하세요")
    return Path(raw)


async def test_decrypt_real_sample(sample_path: Path):
    """암호화 샘플 → 복호화 → 평문(ZIP) 검증 전 구간."""
    from src.config import load_config
    from src.infrastructure.drm.detector import detect_file_kind
    from src.infrastructure.drm.scsl import ScslCliDecryptor

    config = load_config()
    assert config.drm.enabled, ".env에서 DRM_ENABLED=true 필요"

    encrypted = sample_path.read_bytes()
    assert detect_file_kind(encrypted) == "drm", "샘플이 SCDS 암호문이 아님"

    decryptor = ScslCliDecryptor(config.drm)
    plain = await decryptor.decrypt(encrypted, sample_path.name)

    assert detect_file_kind(plain) == "plain", "복호화 결과가 ZIP(xlsx/docx)이 아님"
    assert len(plain) > 0


async def test_decrypt_plain_input_returns_plain(sample_path: Path):
    """평문 입력 → ret -36 경로 — 성공 취급·평문 반환 확인 (dst 생성 여부 실측)."""
    from src.config import load_config
    from src.infrastructure.drm.detector import detect_file_kind
    from src.infrastructure.drm.scsl import ScslCliDecryptor

    config = load_config()
    plain_input = b"PK\x03\x04" + b"\x00" * 60

    decryptor = ScslCliDecryptor(config.drm)
    result = await decryptor.decrypt(plain_input, "plain.xlsx")
    assert detect_file_kind(result) == "plain"
