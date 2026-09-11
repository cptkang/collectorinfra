"""기동 검증 3종 수용 기준 V4·V5·V13 (plans/94 §4.2 · §4.5 · 부록 A.5).

기동 후 **세 가지를 대조한 뒤에야** 그 프로파일의 결과를 합격으로 센다:
헬스 · 사다리 단 · 실효 설정 에코. 확인하지 못한 것을 통과로도 실패로도 세지 않는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest

from scripts.scenario import server as server_mod
from scripts.scenario.server import ServerHandle, platform_provenance, verify_profile


class FakeHandle:
    """ServerHandle 의 검증 표면만 흉내낸다(프로세스 미기동)."""

    def __init__(
        self,
        healthy: bool = True,
        ladder: Optional[tuple[str, str]] = ("deep_agent", "none"),
        mock: bool = False,
    ) -> None:
        self.profile = "baseline"
        self.port = 9999
        self.mock = mock
        self.ladder = ladder
        self._healthy = healthy

    def wait_healthy(self, timeout_sec: float = 90.0) -> tuple[bool, str]:
        return (True, "health 200") if self._healthy else (False, "헬스 대기 초과")


class FakeClient:
    """ScenarioClient 를 대신해 설정 에코만 돌려준다."""

    def __init__(self, effective: Optional[dict[str, str]], error: Optional[str] = None) -> None:
        self._effective = effective
        self._error = error

    def effective_settings(self) -> tuple[Optional[dict[str, str]], Optional[str]]:
        return self._effective, self._error

    def close(self) -> None:
        pass


@pytest.fixture()
def patch_client(monkeypatch: pytest.MonkeyPatch):
    def install(effective: Optional[dict[str, str]], error: Optional[str] = None) -> None:
        monkeypatch.setattr(
            server_mod, "ScenarioClient", lambda _config: FakeClient(effective, error)
        )
    return install


# --- V4 주입 실효성 ------------------------------------------------------

def test_V4_주입이_실효값에_반영되면_유효하다(patch_client) -> None:
    patch_client({"TEXT2SQL_MULTI_CANDIDATE": "true"})
    status = verify_profile(
        FakeHandle(), {"TEXT2SQL_MULTI_CANDIDATE": "true"}, token=None
    )
    assert status.valid is True
    assert status.echo_ok is True
    assert status.echo_mismatch == {}


def test_V4_주입이_무시되면_INVALID_다(patch_client) -> None:
    """OS env/.encenv 우선순위로 주입이 조용히 무시되면 '기능 off 인데 합격'으로 오독된다(D-129)."""
    patch_client({"TEXT2SQL_MULTI_CANDIDATE": "false"})
    status = verify_profile(
        FakeHandle(), {"TEXT2SQL_MULTI_CANDIDATE": "true"}, token=None
    )
    assert status.valid is False
    assert status.echo_ok is False
    assert status.echo_mismatch["TEXT2SQL_MULTI_CANDIDATE"] == {
        "injected": "true", "effective": "false"
    }
    assert any("주입이" in reason for reason in status.reasons)


def test_V4_에코에_키가_아예_없어도_불일치로_잡는다(patch_client) -> None:
    patch_client({})
    status = verify_profile(FakeHandle(), {"ROUTER_TWO_STAGE_ENABLED": "true"}, token=None)
    assert status.valid is False
    assert status.echo_mismatch["ROUTER_TWO_STAGE_ENABLED"]["effective"] == "(키 없음)"


def test_대소문자_차이는_불일치로_보지_않는다(patch_client) -> None:
    patch_client({"ENABLE_X": "True"})
    status = verify_profile(FakeHandle(), {"ENABLE_X": "true"}, token=None)
    assert status.echo_ok is True


def test_G3_토큰이_없어_에코를_못_읽으면_미확인이지_실패가_아니다(patch_client) -> None:
    """확인하지 못한 것을 통과로도 실패로도 세지 않는다 - 사유가 리포트 10절에 남는다."""
    patch_client(None, "설정 에코 미확인 (http 403 - 관리자 토큰 필요)")
    status = verify_profile(FakeHandle(), {"ENABLE_X": "true"}, token=None)
    assert status.echo_ok is None
    assert status.valid is True          # INVALID 로 단정하지 않는다
    assert any("미확인" in reason for reason in status.reasons)


# --- V5 조용한 강등 ------------------------------------------------------

def test_V5_사다리_단이_의도와_다르면_INVALID_다(patch_client) -> None:
    patch_client({})
    status = verify_profile(
        FakeHandle(ladder=("semantic_router", "deepagents_unavailable")),
        {}, token=None, expected_tier="deep_agent",
    )
    assert status.valid is False
    assert status.tier == "semantic_router"
    assert any("조용한 강등" in reason for reason in status.reasons)


def test_V5_사다리_로그를_못_찾으면_사유를_남긴다(patch_client) -> None:
    """어느 경로를 쟀는지 모르면 node_path 를 해석할 기준이 없다."""
    patch_client({})
    status = verify_profile(FakeHandle(ladder=None), {}, token=None)
    assert any("사다리 확정" in reason for reason in status.reasons)


def test_사다리_단이_의도와_같으면_유효하다(patch_client) -> None:
    patch_client({})
    status = verify_profile(FakeHandle(), {}, token=None, expected_tier="deep_agent")
    assert status.valid is True
    assert status.tier == "deep_agent"


def test_헬스가_실패하면_이후_대조를_하지_않는다(patch_client) -> None:
    patch_client({})
    status = verify_profile(FakeHandle(healthy=False), {}, token=None)
    assert status.valid is False
    assert status.echo_ok is None
    assert status.reasons[0].startswith("헬스 실패")


def test_모의_서버는_사다리_에코_대조_없음을_사유로_남긴다(patch_client) -> None:
    """리포트가 실행 성격을 감추지 않게 한다."""
    patch_client({})
    status = verify_profile(FakeHandle(mock=True), {}, token=None)
    assert status.valid is True
    assert status.tier == "mock"
    assert any("모의 실행" in reason for reason in status.reasons)


# --- V13 운영 상태 오염 방지 ---------------------------------------------

def test_V13_주입은_자식_환경에만_하고_env_파일을_고치지_않는다(tmp_path: Path) -> None:
    env_file = Path(".env")
    before = env_file.stat().st_mtime_ns if env_file.exists() else None
    env = ServerHandle._build_env({"TEXT2SQL_MULTI_CANDIDATE": "true"}, port=12345)
    after = env_file.stat().st_mtime_ns if env_file.exists() else None
    assert before == after
    assert env["TEXT2SQL_MULTI_CANDIDATE"] == "true"
    assert env["API_PORT"] == "12345"


def test_W5_자식_프로세스에_UTF8_강제를_주입한다() -> None:
    """cp949 콘솔에서 한글 출력이 UnicodeEncodeError 로 런을 죽인다(docs/18:82)."""
    env = ServerHandle._build_env({}, port=1)
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_V13_체크포인트는_런_전용_경로로_격리된다(tmp_path: Path) -> None:
    """운영 checkpoints.db(실측 82MB) 오염 금지 - runner.execute 가 주입한다."""
    from scripts.scenario import runner as runner_mod

    source = Path(runner_mod.__file__).read_text(encoding="utf-8")
    assert 'overrides["CHECKPOINT_DB_URL"]' in source
    assert "checkpoints-" in source


# --- W6 provenance -------------------------------------------------------

def test_W6_측정_조건을_재구성할_수_있게_남긴다() -> None:
    info = platform_provenance()
    for key in ("os", "os_release", "python", "encoding", "pythonutf8"):
        assert key in info, f"provenance 에 {key} 가 없다"


def test_W3_netsh_출력_형식을_파싱한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows 예약 제외 대역. POSIX 에서는 빈 목록이어야 한다."""
    if server_mod.IS_WINDOWS:
        pytest.skip("POSIX 전용 단언")
    assert server_mod.windows_excluded_ports() == []
