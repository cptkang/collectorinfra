"""Softcamp ServiceLinker(scsl.jar) 복호화 어댑터 (Plan 69 §2.4·§3.4).

Java CLI 래퍼(`tools/drm-wrapper/Decrypt.java`)를 단일 소스 실행(single-file
source launch, JDK 11+)으로 호출한다 — 별도 빌드 없음:

    java -cp <scsl.jar> <Decrypt.java> <in> <out> <properties> <keyfile> <groupId>

래퍼는 `CreateDecryptFileDAC` 반환값을 stdout에 `RET:<n>` 형식으로 출력하고,
성공(0·-36)이면 exit 0, 그 외 exit 1로 종료한다. 에러코드 의미는 사내 연동
가이드 실측(계획서 §1.3) 기준.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from src.infrastructure.drm.decryptor import DrmDecryptError

if TYPE_CHECKING:
    from src.config import DrmConfig

logger = logging.getLogger(__name__)

# 가이드 에러코드 → 사용자 노출 사유 (전체 표는 계획서 §1.3 / 연동 가이드 참조)
_RET_REASONS: dict[int, str] = {
    1: "키 정보 오류",
    -31: "원본 파일이 존재하지 않음(래퍼 내부 오류)",
    -33: "파일 접근 에러",
    -41: "파일이 손상되었습니다",
    -71: "지원하지 않는 확장자입니다(서버 확장자 설정 확인 필요)",
    -81: "BYPASS 모드입니다 — 관리자에게 문의하세요",
    3000: "키 파일이 없습니다 — KeyManager 동작 확인 필요",
    3003: "서버 ID의 키 파일을 찾을 수 없습니다 — KeyManager 동작 확인 필요",
    3030: "인증되지 않은 키 파일 — KeyManager 재시작 필요",
    4001: "복호화에 실패했습니다",
    2002: "원본/대상 경로 동일(래퍼 내부 오류)",
}

# 성공 취급 반환값: 0=성공, -36=원본이 평문(가이드: "에러 처리 불필요, 성공과 동일")
_RET_OK = {0, -36}


class ScslCliDecryptor:
    """scsl.jar 래퍼를 subprocess로 호출하는 복호화 어댑터."""

    def __init__(self, config: "DrmConfig") -> None:
        self._config = config
        self._temp_dir = self._resolve_temp_dir(config)
        self._sweep_temp_dir()

    @staticmethod
    def _resolve_temp_dir(config: "DrmConfig") -> Path:
        temp_dir = (
            Path(config.temp_dir)
            if config.temp_dir
            else Path(tempfile.gettempdir()) / "drm_scsl"
        )
        temp_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        return temp_dir

    @staticmethod
    def _is_own_temp_file(path: Path) -> bool:
        """이 어댑터가 만든 작업 파일인지 판정한다 (`<uuid4hex>_in|out.<ext>`).

        운영 서비스가 root로 기동될 수 있으므로 sweep 대상을 **우리 파일로 한정**한다.
        DRM_TEMP_DIR가 실수로 공유 경로(/tmp 등)를 가리켜도 남의 파일을 지우지 않는다.
        """
        if not path.is_file():
            return False
        stem = path.stem  # "<token>_in" / "<token>_out"
        token, _, suffix = stem.rpartition("_")
        if suffix not in ("in", "out"):
            return False
        return len(token) == 32 and all(c in "0123456789abcdef" for c in token)

    def _sweep_temp_dir(self) -> None:
        """기동 시 잔여 평문 파일 sweep (프로세스당 1회 — get_decryptor 싱글턴 전제)."""
        removed = 0
        for leftover in self._temp_dir.iterdir():
            if not self._is_own_temp_file(leftover):
                continue
            try:
                leftover.unlink()
                removed += 1
            except OSError:
                pass
        if removed:
            logger.warning("DRM temp 잔여 파일 %d개 정리: %s", removed, self._temp_dir)

    def _validate_config(self) -> None:
        cfg = self._config
        missing = [
            name
            for name, value in (
                ("DRM_SCSL_JAR_PATH", cfg.scsl_jar_path),
                ("DRM_WRAPPER_PATH", cfg.wrapper_path),
                ("DRM_PROPERTIES_PATH", cfg.properties_path),
                ("DRM_KEY_FILE_PATH", cfg.key_file_path),
            )
            if not value
        ]
        if missing:
            raise DrmDecryptError(f"DRM 설정 누락: {', '.join(missing)}")

    async def decrypt(self, file_bytes: bytes, filename: str) -> bytes:
        plain, _ret = await self.decrypt_detailed(file_bytes, filename)
        return plain

    async def decrypt_detailed(
        self, file_bytes: bytes, filename: str
    ) -> tuple[bytes, int]:
        """복호화 결과와 scsl 반환값(ret)을 함께 반환한다.

        진단 도구(diagnostics.verify_sample)가 성공 사유를 구분하기 위해 사용한다 —
        ret 0(복호화 성공)과 -36(원본이 애초에 평문)은 운영 판단이 다르다.
        """
        self._validate_config()
        cfg = self._config

        # 원 확장자 유지(scsl이 확장자를 체크), in/out 파일명 상이(에러 2002 예방)
        ext = Path(filename).suffix or ".bin"
        token = uuid.uuid4().hex
        in_path = self._temp_dir / f"{token}_in{ext}"
        out_path = self._temp_dir / f"{token}_out{ext}"

        cmd = [
            cfg.java_bin,
            "-cp",
            cfg.scsl_jar_path,
            cfg.wrapper_path,
            str(in_path),
            str(out_path),
            cfg.properties_path,
            cfg.key_file_path,
            cfg.group_id,
        ]

        try:
            in_path.write_bytes(file_bytes)
            try:
                exit_code, stdout, stderr = await asyncio.wait_for(
                    self._run_wrapper(cmd), timeout=cfg.timeout_sec
                )
            except asyncio.TimeoutError:
                raise DrmDecryptError(f"복호화 타임아웃({cfg.timeout_sec}s)")
            except FileNotFoundError:
                raise DrmDecryptError(f"java 실행 파일을 찾을 수 없음: {cfg.java_bin}")

            ret = self._parse_ret(stdout)
            if ret is None:
                # 래퍼가 RET을 출력하지 못함 — 소스 컴파일 실패 등
                raise DrmDecryptError(
                    "래퍼 실행 실패(RET 미출력)",
                    detail=(stderr or stdout or "")[:500],
                )
            if ret not in _RET_OK:
                reason = _RET_REASONS.get(ret, "복호화 실패")
                logger.error(
                    "DRM 복호화 실패: file=%s ret=%s exit=%s stderr=%s",
                    filename, ret, exit_code, (stderr or "")[:500],
                )
                raise DrmDecryptError(reason, ret_code=ret)

            if out_path.exists() and out_path.stat().st_size > 0:
                return out_path.read_bytes(), ret
            if ret == -36:
                # 원본이 평문 — dst 미생성 시 src 바이트를 그대로 사용(방어, §2.4-4)
                return file_bytes, ret
            raise DrmDecryptError("복호화 산출 파일이 생성되지 않음", ret_code=ret)
        finally:
            for path in (in_path, out_path):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("DRM temp 파일 삭제 실패: %s", path)

    @staticmethod
    async def _run_wrapper(cmd: list[str]) -> tuple[int, str, str]:
        """래퍼 프로세스를 실행하고 (exit_code, stdout, stderr)를 반환한다."""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await proc.communicate()
        except asyncio.CancelledError:
            # wait_for 타임아웃 시 프로세스 잔존 방지
            proc.kill()
            raise
        return (
            proc.returncode if proc.returncode is not None else -1,
            stdout_b.decode("utf-8", errors="replace"),
            stderr_b.decode("utf-8", errors="replace"),
        )

    @staticmethod
    def _parse_ret(stdout: str) -> Optional[int]:
        """래퍼 stdout에서 `RET:<n>` 값을 파싱한다."""
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("RET:"):
                try:
                    return int(line[4:].strip())
                except ValueError:
                    return None
        return None
