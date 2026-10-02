"""로그인은 서버 헬스 확인 뒤에만 한다 (2026-09-14 폐쇄망 회귀).

실 서버는 기동에 수 초가 걸린다. 러너가 `handle.start()` 직후 `acquire_tokens` 를 부르면
아직 열리지 않은 포트에 붙어 `ConnectError`(Windows: WinError 10061)로 토큰을 못 받고,
설정 에코가 401 이 되어 프로파일이 INVALID - 턴 0회로 끝난다(런 20260914-150834).
모의 경로는 로그인을 건너뛰고, 기존 테스트는 `acquire_tokens` 를 대역해 이 순서를 보지 못했다.
"""

from __future__ import annotations

from typing import Any

import pytest

from scripts.scenario import runner as runner_mod
from scripts.scenario.catalog import Catalog, Group, Scenario, Turn
from scripts.scenario.server import ProfileStatus


class _BootingHandle:
    """ServerHandle 대역. `wait_healthy` 를 거쳐야만 서버가 '떠 있다'."""

    instances: list[_BootingHandle] = []
    healthy = True

    def __init__(self, profile, env_overrides, port, log_path, mock):
        self.profile = profile
        self.port = port
        self.mock = mock
        self.ladder = ("intent_orchestration", "intent_flag_on")
        self.up = False
        _BootingHandle.instances.append(self)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def port_released(self) -> bool:
        return True

    def wait_healthy(self, timeout_sec: float = 90.0):
        if not self.healthy:
            return False, "자식 프로세스가 기동 중 종료됐다 (로그 확인)"
        self.up = True
        return True, "health 200"


def _catalog() -> Catalog:
    return Catalog(
        groups={"T": Group(id="T", name="t", latency_target_ms=10000)},
        scenarios=[Scenario(id="T-01", group="T", plans=[94], title="t",
                            turns=[Turn(send={"query": "q"}, expect={})],
                            profile="baseline")],
        profiles={"baseline": {}},
    )


@pytest.fixture()
def harness(tmp_path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {"login_when_up": [], "admin_token": "(미호출)"}
    _BootingHandle.instances = []
    _BootingHandle.healthy = True

    def fake_acquire(port, config):
        up = _BootingHandle.instances[-1].up
        seen["login_when_up"].append(up)
        return ("T-user", "T-admin", []) if up else (None, None, ["ConnectError: 10061"])

    def fake_verify(handle, overrides, admin_token, expected_tier=None):
        seen["admin_token"] = admin_token
        status = ProfileStatus(name=handle.profile, port=handle.port)
        status.reasons.append("테스트 - 기동 검증에서 멈춘다")
        return status

    monkeypatch.setattr(runner_mod, "RESULTS_ROOT", tmp_path)
    monkeypatch.setattr(runner_mod, "ServerHandle", _BootingHandle)
    monkeypatch.setattr(runner_mod, "pick_port", lambda port=None: 5095)
    monkeypatch.setattr(runner_mod, "acquire_tokens", fake_acquire)
    monkeypatch.setattr("scripts.scenario.server.verify_profile", fake_verify)
    return seen


def test_로그인은_서버가_뜬_뒤에_한다(harness) -> None:
    runner_mod.execute(_catalog(), runner_mod.RunConfig(mode="run", env="sandbox"))

    assert harness["login_when_up"] == [True], "기동 전 로그인은 ConnectError 로 토큰을 못 받는다"
    assert harness["admin_token"] == "T-admin", "받은 운영자 토큰이 설정 에코로 넘어가야 한다"


def test_헬스_실패면_로그인을_시도하지_않는다(harness) -> None:
    """떠 있지 않은 서버에 로그인하면 사유가 ConnectError 로 오염돼 진짜 원인(헬스)이 가려진다."""
    _BootingHandle.healthy = False

    runner_mod.execute(_catalog(), runner_mod.RunConfig(mode="run", env="sandbox"))

    assert harness["login_when_up"] == []
    assert harness["admin_token"] is None
