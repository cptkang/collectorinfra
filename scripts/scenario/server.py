"""프로파일별 서버 기동·검증·종료 (plans/94 §4.2 · §4.5 · 부록 A.5 W1~W9).

프로파일 1개 = 서버 기동 1회다. 플래그는 기동 시 1회 해석되고 사다리는 빌드 타임에 배타
확정되므로, 요청 시점에 바꾼 설정으로 잰 값은 거짓이다.

기동 후 **세 가지를 대조**한 뒤에야 그 프로파일의 결과를 합격으로 센다:
  1. 헬스 200
  2. 기동 로그의 사다리 단 (조용한 강등 차단)
  3. 실효 설정 에코 (주입이 조용히 무시되는 것을 차단 - D-129)
하나라도 확인되지 않으면 그 프로파일은 INVALID 이고, 사유가 리포트 10절에 남는다.
"""

from __future__ import annotations

import os
import platform
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import REPO_ROOT, run_capture, utf8_open
from .client import ClientConfig, ScenarioClient

IS_WINDOWS = os.name == "nt"

_LADDER_RE = re.compile(
    r"오케스트레이션 사다리 확정:\s*tier=(?P<tier>\S+)\s+degraded_reason=(?P<reason>\S+)"
)
HEALTH_WAIT_SEC = 90.0

#: 부가 경로 1단(deep_agent)을 플래그로 opt-in 했는데 **가용성 때문에** 성립하지 않은 사유
#: (`src/observability/ladder.py` `OPTIN_FAILURE_REASONS` 와 같은 집합).
#: 이 사유로 확정된 프로파일은 의도하지 않은 경로를 재므로 INVALID 다(§4.5 조용한 강등 차단).
#: 운영자가 플래그로 고른 비기준 단(`intent_flag_on` 2단 · `semantic_routing_off` 4단)은 의도한
#: 선택이라 여기 넣지 않는다 - 리포트 경고로만 남는다(D-221 O-c · D-225 기준 3단).
UNINTENDED_DEGRADATION = frozenset({"orchestrator_unavailable", "package_missing"})

#: 비스트리밍 요청의 대기 상한을 정하는 서버 실효값(설정 에코에서 읽는다 - `client._nonstream_timeout`).
SERVER_TIMEOUT_KEYS = ("API_QUERY_TIMEOUT", "API_FILE_QUERY_TIMEOUT")


@dataclass
class ProfileStatus:
    """기동 1회의 유효성. 사유 없는 INVALID 는 만들지 않는다."""

    name: str
    port: int
    valid: bool = False
    tier: Optional[str] = None
    degraded_reason: Optional[str] = None
    echo_ok: Optional[bool] = None          # None = 확인 못 함(토큰 없음 등)
    echo_mismatch: dict[str, dict[str, str]] = field(default_factory=dict)
    auth_enabled: Optional[bool] = None     # 이 기동의 AUTH_ENABLED 실효값(에코에서 읽는다)
    reasons: list[str] = field(default_factory=list)
    #: 서버 자신의 요청 상한(초) - `SERVER_TIMEOUT_KEYS` 중 에코에서 읽힌 것만 담는다.
    server_timeouts: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "port": self.port,
            "valid": self.valid,
            "tier": self.tier,
            "degraded_reason": self.degraded_reason,
            "echo_ok": self.echo_ok,
            "echo_mismatch": self.echo_mismatch,
            "auth_enabled": self.auth_enabled,
            "reasons": self.reasons,
        }


def windows_excluded_ports() -> list[tuple[int, int]]:
    """Windows 예약 제외 대역을 읽는다 (부록 A.1-6 · W3).

    Hyper-V·WSL2·Docker Desktop 이 대역을 선점하면 빈 포트를 골라도 바인딩이
    "forbidden by its access permissions" 로 실패한다. 실패를 INVALID 로 오판하지
    않으려면 고르기 전에 피해야 한다.
    """
    if not IS_WINDOWS:
        return []
    # 출력은 콘솔 코드페이지(cp949)다. text=True 로 받으면 PYTHONUTF8=1 하에서 깨진다.
    out = run_capture(
        ["netsh", "interface", "ipv4", "show", "excludedportrange", "protocol=tcp"]
    )
    ranges: list[tuple[int, int]] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            ranges.append((int(parts[0]), int(parts[1])))
    return ranges


def pick_port(preferred: Optional[int] = None, excluded: Optional[list[tuple[int, int]]] = None) -> int:
    """바인딩 가능한 포트를 고른다. Windows 제외 대역은 피한다."""
    ranges = windows_excluded_ports() if excluded is None else excluded

    def blocked(port: int) -> bool:
        return any(low <= port <= high for low, high in ranges)

    if preferred and not blocked(preferred):
        return preferred
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if not blocked(port):
            return port
    raise RuntimeError("제외 대역 밖에서 빈 포트를 찾지 못했다")


def platform_provenance() -> dict[str, str]:
    """측정 조건을 나중에 재구성할 수 있게 남긴다 (W6 · 부록 A.2).

    **이 함수는 절대 예외를 던지지 않는다.** provenance 는 부가 정보인데, 여기서 던진
    예외가 런 전체를 죽인 사고가 있었다(2026-09-11 폐쇄망 Windows - powercfg 의 cp949
    출력이 PYTHONUTF8=1 하에서 UnicodeDecodeError 를 내고 stdout 이 None 이 됐다).
    측정을 돕는 장치가 측정을 막으면 안 된다.
    """
    info: dict[str, str] = {}
    try:
        info.update({
            "os": platform.system(),
            "os_release": platform.release(),
            "python": platform.python_version(),
            "encoding": sys.stdout.encoding or "unknown",
            "pythonutf8": os.environ.get("PYTHONUTF8", "(미설정)"),
        })
    except Exception as exc:  # 플랫폼 조회조차 실패하면 그 사실을 남긴다
        info["error"] = f"{type(exc).__name__}: {exc}"
    if IS_WINDOWS:
        try:
            info["power_plan"] = (
                run_capture(["powercfg", "/getactivescheme"]).strip() or "(조회 실패)"
            )
            info["console_codepage"] = run_capture(["cmd", "/c", "chcp"]).strip() or "(조회 실패)"
        except Exception as exc:  # 측정을 돕는 장치가 측정을 막으면 안 된다
            info["power_plan"] = f"(조회 실패: {type(exc).__name__})"
        # 바이러스 검사 제외 여부는 관리자 권한이 필요해 조회하지 않는다.
        # 확인하지 않았다는 사실 자체를 남긴다 - 숨기지 않는 것이 요점이다(부록 A.2).
        info["av_exclusion"] = "미확인"
    return info


class ServerHandle:
    """자식 서버 1개의 수명. 기동 로그를 읽어 사다리 단을 확정한다."""

    def __init__(
        self,
        profile: str,
        env_overrides: dict[str, str],
        port: int,
        log_path: Path,
        mock: bool = False,
    ) -> None:
        self.profile = profile
        self.port = port
        self.log_path = log_path
        self.mock = mock
        self._env = self._build_env(env_overrides, port)
        self._proc: Optional[subprocess.Popen] = None
        self._ladder: Optional[tuple[str, str]] = None
        self._reader: Optional[threading.Thread] = None
        self._health: Optional[tuple[bool, str]] = None

    @staticmethod
    def _build_env(overrides: dict[str, str], port: int) -> dict[str, str]:
        """자식 프로세스 환경만 만든다. `.env` 는 수정하지 않는다(§4.4)."""
        env = os.environ.copy()
        env.update(overrides)
        env["API_PORT"] = str(port)
        # 한글 출력에서 런이 죽지 않게 - cp949 콘솔 대응(W5 · 부록 A.1-3)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        return env

    def start(self) -> None:
        module = "scripts.scenario.mockserver" if self.mock else "scripts.scenario._serve"
        self._health = None   # 기동마다 새로 판정한다
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
        self._proc = subprocess.Popen(
            [sys.executable, "-m", module],
            cwd=str(REPO_ROOT),
            env=self._env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creation_flags,
            start_new_session=not IS_WINDOWS,
        )
        self._reader = threading.Thread(target=self._pump_log, daemon=True)
        self._reader.start()

    def _pump_log(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        with utf8_open(self.log_path, "w") as sink:
            for line in self._proc.stdout:
                sink.write(line)
                sink.flush()
                match = _LADDER_RE.search(line)
                if match and self._ladder is None:
                    self._ladder = (match.group("tier"), match.group("reason"))

    @property
    def ladder(self) -> Optional[tuple[str, str]]:
        return self._ladder

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def wait_healthy(self, timeout_sec: float = HEALTH_WAIT_SEC) -> tuple[bool, str]:
        """헬스 200 을 기다린다. **기동 1회당 한 번만 기다리고** 결과를 기억한다.

        러너는 로그인 전에 헬스를 확인하고(2026-09-14 폐쇄망 WinError 10061 대응), 기동 검증
        (`verify_profile`)이 다시 확인한다. 기억하지 않으면 살아 있지만 뜨지 않는 서버에서
        대기가 두 번 돌아 arm 당 최대 180초가 된다 — 환경이 깨진 62 arm 런이면 약 1.5시간을
        더 태운 뒤에야 전부 INVALID 가 드러난다. 판정은 기동 사이에 바뀌지 않으므로
        기억해도 잃는 정보가 없다(`start()` 가 초기화한다).
        """
        if self._health is None:
            self._health = self._poll_health(timeout_sec)
        return self._health

    def _poll_health(self, timeout_sec: float) -> tuple[bool, str]:
        client = ScenarioClient(ClientConfig(port=self.port))
        deadline = time.monotonic() + timeout_sec
        last = "기동 대기 시작"
        try:
            while time.monotonic() < deadline:
                if not self.alive():
                    return False, "자식 프로세스가 기동 중 종료됐다 (로그 확인)"
                ok, detail = client.health()
                if ok:
                    return True, "health 200"
                last = detail
                time.sleep(1.0)
        finally:
            client.close()
        return False, f"헬스 대기 {timeout_sec:.0f}초 초과 (마지막: {last})"

    def stop(self, grace_sec: float = 5.0) -> None:
        """종료한다. 고아 프로세스를 남기지 않는 것이 목적이다 (W1 · V20)."""
        if self._proc is None or self._proc.poll() is not None:
            return
        pid = self._proc.pid
        try:
            if IS_WINDOWS:
                # Windows 에는 SIGTERM/SIGKILL 이 없다.
                os.kill(pid, signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass

        try:
            self._proc.wait(timeout=grace_sec)
            return
        except subprocess.TimeoutExpired:
            pass

        try:
            if IS_WINDOWS:
                # /T = 자식까지. 리로더 없이 띄웠어도 워커가 남을 수 있다.
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, timeout=15,
                )
            else:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (OSError, ProcessLookupError, subprocess.SubprocessError):
            pass
        try:
            self._proc.wait(timeout=grace_sec)
        except subprocess.TimeoutExpired:
            pass

    def port_released(self, timeout_sec: float = 10.0) -> bool:
        """포트가 실제로 회수됐는지 확인한다 (V20)."""
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind(("127.0.0.1", self.port))
                    return True
                except OSError:
                    time.sleep(0.5)
        return False


def verify_profile(
    handle: ServerHandle,
    overrides: dict[str, str],
    admin_token: Optional[str],
    expected_tier: Optional[str] = None,
) -> ProfileStatus:
    """기동 검증 3종. 확인하지 못한 것을 통과로 세지 않는다.

    `admin_token` 은 **설정 에코 전용**이다 - `/admin/settings/schema` 는
    `require_admin_user` 를 타므로 질의용 사용자 토큰으로는 열리지 않는다(D-070).
    """
    status = ProfileStatus(name=handle.profile, port=handle.port)

    healthy, detail = handle.wait_healthy()
    if not healthy:
        status.reasons.append(f"헬스 실패: {detail}")
        return status

    if handle.mock:
        # 모의 서버는 사다리도 설정 카탈로그도 갖지 않는다. 배관 검증 전용이므로
        # 유효로 보되 그 사실을 사유로 남긴다 - 리포트가 실행 성격을 감추지 않게 한다.
        status.valid = True
        status.tier = "mock"
        status.reasons.append("모의 실행 - 사다리·설정 에코 대조 없음 (무과금 배관 검증)")
        return status

    ladder = handle.ladder
    if ladder is None:
        status.reasons.append(
            "기동 로그에서 사다리 확정 1줄을 찾지 못했다 - 어느 경로를 쟀는지 알 수 없다"
        )
    else:
        status.tier, status.degraded_reason = ladder
        if expected_tier and status.tier != expected_tier:
            status.reasons.append(
                f"조용한 강등: 의도 {expected_tier} 인데 {status.tier} 로 확정됐다"
            )
        elif status.degraded_reason in UNINTENDED_DEGRADATION:
            # 러너는 expected_tier 를 넘기지 않는다 - 프로파일마다 의도 단을 따로 계산하지 않고,
            # 기동 로그의 강등 사유가 "플래그는 켜졌는데 못 올라갔다"를 직접 말해 준다.
            status.reasons.append(
                f"조용한 강등: 플래그는 1단(deep_agent)인데 {status.tier} 로 확정됐다 "
                f"(degraded_reason={status.degraded_reason}) - 오케스트레이터 서빙"
                "(ORCHESTRATOR_BASE_URL 의 /models)과 deepagents 설치를 확인한다"
            )

    client = ScenarioClient(ClientConfig(port=handle.port, admin_token=admin_token))
    try:
        effective, echo_error = client.effective_settings()
    finally:
        client.close()

    if effective is None:
        status.echo_ok = False
        status.reasons.append(
            f"설정 에코 미확인: {echo_error or '(사유 없음)'} - "
            "주입이 실효값에 반영됐는지 확인하지 못했으므로 이 프로파일은 재지 않는다"
        )
    else:
        status.auth_enabled = effective.get("AUTH_ENABLED", "").strip().lower() == "true"
        for key in SERVER_TIMEOUT_KEYS:
            try:
                status.server_timeouts[key] = float(effective.get(key, ""))
            except ValueError:
                continue
        mismatch = {
            key: {"injected": value, "effective": effective.get(key, "(키 없음)")}
            for key, value in overrides.items()
            if effective.get(key, "").strip().lower() != value.strip().lower()
        }
        status.echo_mismatch = mismatch
        status.echo_ok = not mismatch
        if mismatch:
            status.reasons.append(
                f"주입이 실효값에 반영되지 않았다 ({len(mismatch)}건) - "
                "OS env·.encenv 우선순위 확인 (D-129)"
            )

    # 유효 판정은 **확인된 사실**로만 내린다.
    #
    # 종전에는 사유 문자열의 접두사 목록으로 판정했는데, 목록에 없는 사유("설정 에코
    # 미확인")가 통과로 새어 62개 프로파일이 전부 valid=true 로 기록됐다(2026-09-14 실측).
    # 문자열 매칭은 새 사유가 생길 때마다 조용히 뚫린다 - 상태 플래그로 못박는다.
    status.valid = bool(status.echo_ok) and not any(
        reason.startswith(("헬스 실패", "조용한 강등")) for reason in status.reasons
    )
    return status
