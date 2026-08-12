"""Plan 69 DRM 해제 계층 테스트.

감지(detector), 어댑터(Passthrough/ScslCli), 라우트 헬퍼(_resolve_uploaded_bytes)를
검증한다. 실기(scsl.jar·KeyManager)는 개발 환경에 없으므로 subprocess는 mock —
실기 검증은 운영계 e2e(RUN_DRM_E2E=1, tests/e2e/test_drm_e2e.py)가 게이트.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest
from fastapi import HTTPException

from src.config import DrmConfig
from src.infrastructure.drm import (
    DrmDecryptError,
    PassthroughDecryptor,
    detect_file_kind,
    get_decryptor,
    header_hex,
)
from src.infrastructure.drm.decryptor import reset_decryptor
from src.infrastructure.drm.scsl import ScslCliDecryptor

# 사내 실측(2026-08-07) 암호문 헤더: SCDSA002 + 00 00 f0 06 00 00 (계획서 §1.4).
# 실샘플 파일 확보 시 tests/fixtures/drm/의 선두 64바이트 픽스처로 교체한다.
SCDS_HEADER = bytes.fromhex("5343445341303032" + "0000f0060000") + b"\xa7\x4d\xd9\x5e"
ZIP_HEADER = b"PK\x03\x04" + b"\x14\x00\x06\x00"


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_decryptor()
    yield
    reset_decryptor()


@pytest.fixture
def scsl_config(tmp_path: Path) -> DrmConfig:
    """검증 대상 필드를 명시해 .env 누수를 차단한 활성 설정."""
    return DrmConfig(
        enabled=True,
        java_bin="java",
        wrapper_path=str(tmp_path / "Decrypt.java"),
        scsl_jar_path=str(tmp_path / "scsl.jar"),
        properties_path=str(tmp_path / "softcamp.properties"),
        key_file_path=str(tmp_path / "keyDAC_SVR0.sc"),
        temp_dir=str(tmp_path / "drm_temp"),
        timeout_sec=5,
    )


class TestDetector:
    def test_plain_zip(self):
        assert detect_file_kind(ZIP_HEADER + b"rest") == "plain"

    def test_drm_scds(self):
        assert detect_file_kind(SCDS_HEADER + b"cipher") == "drm"

    def test_drm_version_variant(self):
        # 5~8바이트가 달라져도(버전 변동) SCDS 4바이트만으로 판정한다
        assert detect_file_kind(b"SCDSB003" + b"\x00" * 8) == "drm"

    def test_unknown(self):
        assert detect_file_kind(b"\x00\x01\x02\x03garbage") == "unknown"

    def test_empty(self):
        assert detect_file_kind(b"") == "unknown"

    def test_header_hex(self):
        assert header_hex(b"SCDSA002rest") == "5343445341303032"


class TestPassthrough:
    async def test_returns_input(self):
        data = b"anything"
        assert await PassthroughDecryptor().decrypt(data, "a.xlsx") == data


class TestFactory:
    def test_disabled_returns_passthrough(self):
        cfg = DrmConfig(enabled=False)
        assert isinstance(get_decryptor(cfg), PassthroughDecryptor)

    def test_enabled_returns_scsl(self, scsl_config):
        assert isinstance(get_decryptor(scsl_config), ScslCliDecryptor)

    def test_singleton(self, scsl_config):
        assert get_decryptor(scsl_config) is get_decryptor(scsl_config)


class TestScslCliDecryptor:
    async def test_missing_config_raises(self, tmp_path):
        cfg = DrmConfig(enabled=True, scsl_jar_path="", temp_dir=str(tmp_path))
        dec = ScslCliDecryptor(cfg)
        with pytest.raises(DrmDecryptError, match="설정 누락"):
            await dec.decrypt(SCDS_HEADER, "a.xlsx")

    async def test_success_ret0(self, scsl_config, monkeypatch):
        dec = ScslCliDecryptor(scsl_config)
        plain = b"PK\x03\x04decrypted-content"

        async def fake_run(cmd):
            # cmd: [java, -cp, <scsl.jar>, <wrapper>, <in>, <out>, <props>, <key>, <group>]
            out_path = Path(cmd[5])
            out_path.write_bytes(plain)
            return 0, "RET:0\n", ""

        monkeypatch.setattr(dec, "_run_wrapper", fake_run)
        result = await dec.decrypt(SCDS_HEADER + b"cipher", "form.xlsx")
        assert result == plain

    async def test_temp_files_cleaned_after_success(self, scsl_config, monkeypatch):
        dec = ScslCliDecryptor(scsl_config)

        async def fake_run(cmd):
            Path(cmd[5]).write_bytes(b"x")
            return 0, "RET:0\n", ""

        monkeypatch.setattr(dec, "_run_wrapper", fake_run)
        await dec.decrypt(SCDS_HEADER, "form.xlsx")
        assert list(Path(scsl_config.temp_dir).glob("*")) == []

    async def test_temp_files_cleaned_after_failure(self, scsl_config, monkeypatch):
        dec = ScslCliDecryptor(scsl_config)

        async def fake_run(cmd):
            return 1, "RET:4001\n", "decrypt fail"

        monkeypatch.setattr(dec, "_run_wrapper", fake_run)
        with pytest.raises(DrmDecryptError):
            await dec.decrypt(SCDS_HEADER, "form.xlsx")
        assert list(Path(scsl_config.temp_dir).glob("*")) == []

    async def test_ret_minus36_without_dst_returns_src(self, scsl_config, monkeypatch):
        """-36(원본이 평문) + dst 미생성 → src 바이트 그대로 (방어, §2.4-4)."""
        dec = ScslCliDecryptor(scsl_config)

        async def fake_run(cmd):
            return 0, "RET:-36\n", ""

        monkeypatch.setattr(dec, "_run_wrapper", fake_run)
        src = ZIP_HEADER + b"already-plain"
        assert await dec.decrypt(src, "form.xlsx") == src

    async def test_error_ret_mapped_reason(self, scsl_config, monkeypatch):
        dec = ScslCliDecryptor(scsl_config)

        async def fake_run(cmd):
            return 1, "RET:3030\n", ""

        monkeypatch.setattr(dec, "_run_wrapper", fake_run)
        with pytest.raises(DrmDecryptError) as exc_info:
            await dec.decrypt(SCDS_HEADER, "form.xlsx")
        assert exc_info.value.ret_code == 3030
        assert "KeyManager 재시작" in exc_info.value.reason

    async def test_missing_ret_line(self, scsl_config, monkeypatch):
        """래퍼가 RET을 출력하지 못한 경우(소스 컴파일 실패 등)."""
        dec = ScslCliDecryptor(scsl_config)

        async def fake_run(cmd):
            return 1, "", "error: cannot find symbol"

        monkeypatch.setattr(dec, "_run_wrapper", fake_run)
        with pytest.raises(DrmDecryptError, match="RET 미출력"):
            await dec.decrypt(SCDS_HEADER, "form.xlsx")

    async def test_timeout(self, scsl_config, monkeypatch):
        dec = ScslCliDecryptor(scsl_config)

        async def fake_run(cmd):
            raise asyncio.TimeoutError()

        monkeypatch.setattr(dec, "_run_wrapper", fake_run)
        with pytest.raises(DrmDecryptError, match="타임아웃"):
            await dec.decrypt(SCDS_HEADER, "form.xlsx")

    async def test_in_out_filenames_differ_and_keep_ext(self, scsl_config, monkeypatch):
        """에러 2002 예방(경로 상이) + 원 확장자 유지(scsl 확장자 체크)."""
        dec = ScslCliDecryptor(scsl_config)
        captured: dict = {}

        async def fake_run(cmd):
            captured["in"], captured["out"] = cmd[4], cmd[5]
            Path(cmd[5]).write_bytes(b"x")
            return 0, "RET:0\n", ""

        monkeypatch.setattr(dec, "_run_wrapper", fake_run)
        await dec.decrypt(SCDS_HEADER, "월간보고.docx")
        assert captured["in"] != captured["out"]
        assert captured["in"].endswith(".docx")
        assert captured["out"].endswith(".docx")

    def test_sweep_on_init(self, scsl_config):
        temp_dir = Path(scsl_config.temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
        own = temp_dir / (("a" * 32) + "_in.xlsx")  # uuid4().hex 형태
        own.write_bytes(b"stale-plain")
        ScslCliDecryptor(scsl_config)
        assert not own.exists()

    def test_sweep_spares_foreign_files(self, scsl_config):
        """서비스가 root로 기동될 수 있으므로 남의 파일은 절대 지우지 않는다."""
        temp_dir = Path(scsl_config.temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
        foreign_names = [
            "important.txt",            # 무관한 파일
            "systemd-private-xyz",      # 공유 temp의 타 프로세스 산출물
            "notauuid_in.xlsx",         # 패턴 유사하나 토큰이 uuid가 아님
            (("g" * 32) + "_in.xlsx"),  # 32자이나 hex가 아님
        ]
        for name in foreign_names:
            (temp_dir / name).write_bytes(b"keep me")
        (temp_dir / "subdir").mkdir()

        ScslCliDecryptor(scsl_config)

        for name in foreign_names:
            assert (temp_dir / name).exists(), f"{name}이 삭제됨"
        assert (temp_dir / "subdir").is_dir()


class TestResolveUploadedBytes:
    """라우트 공통 헬퍼 — /query/file·/query/file/stream 대칭 배선의 단일 지점."""

    @pytest.fixture(autouse=True)
    def _mute_audit(self, monkeypatch):
        import src.security.audit_logger as audit_logger

        async def noop(**kwargs):
            self.audit_calls.append(kwargs)

        self.audit_calls = []
        monkeypatch.setattr(audit_logger, "log_drm_decrypt", noop)

    @pytest.fixture
    def app_config(self):
        class _Cfg:
            drm = DrmConfig(enabled=False)

        return _Cfg()

    async def test_plain_passthrough(self, app_config):
        from src.api.routes.query import _resolve_uploaded_bytes

        data = ZIP_HEADER + b"plain xlsx"
        assert await _resolve_uploaded_bytes(data, "xlsx", "a.xlsx", app_config) == data
        assert self.audit_calls == []

    async def test_unknown_rejected_400(self, app_config):
        from src.api.routes.query import _resolve_uploaded_bytes

        with pytest.raises(HTTPException) as exc_info:
            await _resolve_uploaded_bytes(b"\x00garbage", "xlsx", "a.xlsx", app_config)
        assert exc_info.value.status_code == 400
        assert "손상" in exc_info.value.detail

    async def test_drm_disabled_rejected_400_with_audit(self, app_config):
        from src.api.routes.query import _resolve_uploaded_bytes

        with pytest.raises(HTTPException) as exc_info:
            await _resolve_uploaded_bytes(
                SCDS_HEADER + b"cipher", "xlsx", "a.xlsx", app_config, user_id="u1"
            )
        assert exc_info.value.status_code == 400
        assert "비활성화" in exc_info.value.detail
        assert len(self.audit_calls) == 1
        assert self.audit_calls[0]["error"] == "drm_disabled"

    async def test_drm_enabled_decrypts(self, scsl_config, monkeypatch):
        import src.infrastructure.drm as drm_pkg
        from src.api.routes.query import _resolve_uploaded_bytes

        plain = ZIP_HEADER + b"decrypted"

        class FakeDecryptor:
            async def decrypt(self, file_bytes, filename):
                return plain

        monkeypatch.setattr(drm_pkg, "get_decryptor", lambda cfg: FakeDecryptor())

        class _Cfg:
            drm = scsl_config

        result = await _resolve_uploaded_bytes(
            SCDS_HEADER + b"cipher", "xlsx", "a.xlsx", _Cfg(), user_id="u1"
        )
        assert result == plain
        assert self.audit_calls[-1]["success"] is True

    async def test_drm_decrypt_failure_502(self, scsl_config, monkeypatch):
        import src.infrastructure.drm as drm_pkg
        from src.api.routes.query import _resolve_uploaded_bytes

        class FailingDecryptor:
            async def decrypt(self, file_bytes, filename):
                raise DrmDecryptError("복호화에 실패했습니다", ret_code=4001)

        monkeypatch.setattr(drm_pkg, "get_decryptor", lambda cfg: FailingDecryptor())

        class _Cfg:
            drm = scsl_config

        with pytest.raises(HTTPException) as exc_info:
            await _resolve_uploaded_bytes(
                SCDS_HEADER + b"cipher", "xlsx", "a.xlsx", _Cfg(), user_id="u1"
            )
        assert exc_info.value.status_code == 502
        assert "복호화에 실패" in exc_info.value.detail
        assert self.audit_calls[-1]["ret_code"] == 4001


class TestDiagnostics:
    """어드민 진단 도구 (Plan 69 §4.2) — 항상 구조화된 결과를 반환한다."""

    def test_status_disabled_renders(self, tmp_path):
        """개발 PC(비활성) 상태에서도 화면이 렌더 가능한 결과를 준다."""
        from src.infrastructure.drm.diagnostics import check_environment

        result = check_environment(DrmConfig(enabled=False, temp_dir=str(tmp_path)))
        assert result["enabled"] is False
        assert result["ready"] is False
        assert "비활성" in result["summary"]
        assert len(result["checks"]) == 4
        assert "java" in result and "temp_dir" in result

    def test_status_missing_paths_flagged(self, scsl_config):
        """활성인데 경로가 없으면 문제로 보고한다(예외 없이)."""
        from src.infrastructure.drm.diagnostics import check_environment

        result = check_environment(scsl_config)
        assert result["enabled"] is True
        assert result["ready"] is False
        assert "설정 문제" in result["summary"]
        assert all(c["exists"] is False for c in result["checks"])

    def test_status_key_file_staleness(self, scsl_config):
        """키 파일 mtime이 오래되면 KeyManager 이상으로 판정한다."""
        import os
        import time as _time

        from src.infrastructure.drm.diagnostics import check_environment

        for path_str in (
            scsl_config.wrapper_path, scsl_config.scsl_jar_path,
            scsl_config.properties_path, scsl_config.key_file_path,
        ):
            Path(path_str).write_bytes(b"x")

        old = _time.time() - 40 * 3600  # 40시간 전 (임계 30h 초과)
        os.utime(scsl_config.key_file_path, (old, old))

        result = check_environment(scsl_config)
        key_check = result["checks"][3]
        assert key_check["stale"] is True
        assert key_check["age_hours"] >= 30
        assert "KeyManager" in key_check["message"]
        assert result["ready"] is False

    async def test_verify_unknown_file(self, scsl_config):
        from src.infrastructure.drm.diagnostics import verify_sample

        result = await verify_sample(scsl_config, b"\x00garbage", "a.xlsx")
        assert result["detected"] == "unknown"
        assert result["success"] is False
        assert "손상" in result["message"]

    async def test_verify_plain_file(self, scsl_config):
        from src.infrastructure.drm.diagnostics import verify_sample

        result = await verify_sample(scsl_config, ZIP_HEADER + b"x", "a.xlsx")
        assert result["detected"] == "plain"
        assert result["success"] is False
        assert "복호화 대상이 아닙니다" in result["message"]

    async def test_verify_drm_when_disabled(self, tmp_path):
        """비활성이면 감지까지만 하고 복호화는 시도하지 않는다."""
        from src.infrastructure.drm.diagnostics import verify_sample

        cfg = DrmConfig(enabled=False, temp_dir=str(tmp_path))
        result = await verify_sample(cfg, SCDS_HEADER + b"cipher", "a.xlsx")
        assert result["detected"] == "drm"
        assert result["success"] is False
        assert "DRM_ENABLED=false" in result["message"]
        assert result["header_hex"].startswith("53434453")

    async def test_verify_success_reports_ret_and_parse(self, scsl_config, monkeypatch):
        """성공 시 ret·산출물 검증 결과를 보고하고 평문은 반환하지 않는다."""
        import openpyxl

        import src.infrastructure.drm.diagnostics as diag

        buffer = io.BytesIO()
        wb = openpyxl.Workbook()
        wb.active.title = "서식"
        wb.save(buffer)
        real_xlsx = buffer.getvalue()

        class FakeDecryptor:
            async def decrypt_detailed(self, file_bytes, filename):
                return real_xlsx, 0

        monkeypatch.setattr(diag, "get_decryptor", lambda cfg: FakeDecryptor())
        result = await diag.verify_sample(scsl_config, SCDS_HEADER, "form.xlsx")

        assert result["success"] is True
        assert result["ret"] == 0
        assert result["output"]["is_zip"] is True
        assert result["output"]["parsed"] is True
        assert result["output"]["sheet_names"] == ["서식"]
        assert result["elapsed_ms"] is not None
        # 평문 바이트가 응답에 포함되지 않는다(복호화 오라클 방지)
        assert not any(isinstance(v, bytes) for v in result.values())
        assert not any(isinstance(v, bytes) for v in result["output"].values())

    async def test_verify_ret_minus36_is_flagged(self, scsl_config, monkeypatch):
        """감지는 암호문인데 scsl이 -36을 주면 설정 이상 신호로 안내한다."""
        import src.infrastructure.drm.diagnostics as diag

        class FakeDecryptor:
            async def decrypt_detailed(self, file_bytes, filename):
                return file_bytes, -36

        monkeypatch.setattr(diag, "get_decryptor", lambda cfg: FakeDecryptor())
        result = await diag.verify_sample(scsl_config, SCDS_HEADER, "form.xlsx")
        assert result["ret"] == -36
        assert "키·정책 설정" in result["message"]

    async def test_verify_failure_returns_diagnostics(self, scsl_config, monkeypatch):
        """실패도 예외가 아니라 진단 결과로 반환한다(화면이 깨지지 않도록)."""
        import src.infrastructure.drm.diagnostics as diag

        class FailingDecryptor:
            async def decrypt_detailed(self, file_bytes, filename):
                raise DrmDecryptError("인증되지 않은 키 파일", ret_code=3030)

        monkeypatch.setattr(diag, "get_decryptor", lambda cfg: FailingDecryptor())
        result = await diag.verify_sample(scsl_config, SCDS_HEADER, "form.xlsx")
        assert result["success"] is False
        assert result["ret"] == 3030
        assert "인증되지 않은 키 파일" in result["message"]

    async def test_verify_unexpected_exception_contained(self, scsl_config, monkeypatch):
        import src.infrastructure.drm.diagnostics as diag

        class ExplodingDecryptor:
            async def decrypt_detailed(self, file_bytes, filename):
                raise RuntimeError("boom")

        monkeypatch.setattr(diag, "get_decryptor", lambda cfg: ExplodingDecryptor())
        result = await diag.verify_sample(scsl_config, SCDS_HEADER, "form.xlsx")
        assert result["success"] is False
        assert "예기치 못한 오류" in result["message"]
