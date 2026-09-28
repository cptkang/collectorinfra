"""plans/118 B-1 — 재개 시 **실효 설정** 대조(G-1 확정 2026-09-28).

run `20260922-162132` 는 끊긴 뒤 폐쇄망 `.env` 의 `API_QUERY_TIMEOUT` 이 60 → 180 으로 바뀐 채
이어 돌아, 기준선 39턴은 60초 · arm 은 180초로 한 `raw.jsonl` 에 섞였다. 커밋·작업 트리
대조(`provenance_mix`)는 `.env` 변경을 모른다.

  (a) 커밋이 같고 `API_QUERY_TIMEOUT` 만 다른 두 시도 → 재개 거부 · 사유에 키 이름과 값
  (b) 같은 실효 설정 → 종전대로 재개
  (c) 포트(러너가 시도마다 새로 정한다)는 차이로 세지 않는다
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.scenario import runner as runner_mod
from scripts.scenario import server as server_mod
from scripts.scenario.runner import (
    RunConfig,
    profile_config_record,
    resume_config_conflict,
)
from tests.test_scenario import test_resume_integrity as integrity_tests
from tests.test_scenario.test_resume_integrity import _catalog, _multiturn

#: 픽스처 재사용 — git 출력 바꿔 끼우기 · 결과 폴더 격리.
fake_git = integrity_tests.fake_git
results = integrity_tests.results

BASE = {"API_QUERY_TIMEOUT": "60", "LLM_PROVIDER": "fabrix", "API_PORT": "18001"}


def test_지문은_포트를_빼고_같은_설정이면_같다() -> None:
    one = profile_config_record(BASE)
    two = profile_config_record({**BASE, "API_PORT": "18999"})
    assert one["fingerprint"] == two["fingerprint"]
    assert "API_PORT" not in one["settings"]


def test_상한만_다른_두_시도는_바뀐_키와_값을_적는다() -> None:
    before = {"configs": {"baseline": profile_config_record(BASE)}}
    now = profile_config_record({**BASE, "API_QUERY_TIMEOUT": "180"})

    reason = resume_config_conflict([before], "baseline", now)

    assert reason is not None
    assert "API_QUERY_TIMEOUT 60 -> 180" in reason and "시도 1" in reason
    assert "새 run_id" in reason
    assert resume_config_conflict([before], "baseline", profile_config_record(BASE)) is None
    assert resume_config_conflict([{}], "baseline", now) is None, "비교할 기록이 없으면 판정 안 함"
    assert resume_config_conflict([before], "optin_alarm", now) is None, "다른 프로파일은 무관"


class _FakeHandle:
    def __init__(self, *, profile: str, env_overrides: dict, port: int, log_path: Path,
                 mock: bool) -> None:
        self.profile, self.port, self.mock = profile, port, False
        self.env_overrides = env_overrides

    def start(self) -> None:  # noqa: D401 - 서버를 띄우지 않는다
        return None

    def wait_healthy(self) -> tuple[bool, str]:
        return True, "ok"

    def stop(self) -> None:
        return None

    def port_released(self) -> bool:
        return True


@pytest.fixture()
def live_profile(monkeypatch: pytest.MonkeyPatch):
    """프로파일 1개를 **실행 모드**로 돌리되 서버·HTTP 는 가짜다 — 에코만 바꿔 끼운다."""
    echo: dict[str, str] = dict(BASE)
    ran: list[str] = []

    def verify(handle, expected, admin_token, expected_tier=None):
        status = server_mod.ProfileStatus(name=handle.profile, port=handle.port)
        status.effective_settings = {**echo, "API_PORT": str(handle.port)}
        status.echo_ok = status.valid = True
        return status

    monkeypatch.setattr(runner_mod, "ServerHandle", _FakeHandle)
    monkeypatch.setattr(server_mod, "verify_profile", verify)
    monkeypatch.setattr(runner_mod, "acquire_tokens", lambda port, config: (None, None, []))
    monkeypatch.setattr(runner_mod, "pick_port", lambda port=None: 18000 + len(ran))

    def run_profile(catalog, config, meta, profile, scenarios, *a, **kw) -> int:
        ran.append(profile)
        if len(ran) == 1:
            raise KeyboardInterrupt        # 첫 시도는 프로파일 도중 끊긴다(kill 모사)
        return len(scenarios)

    monkeypatch.setattr(runner_mod, "_run_profile", run_profile)
    scenario = _multiturn()
    monkeypatch.setattr(runner_mod, "iter_executions",
                        lambda _c, _cfg: iter([("baseline", [scenario])]))

    def execute(run_id: str, *, resume: bool = False) -> dict[str, Any]:
        target = {"resume_from": run_id} if resume else {"run_id": run_id}
        return runner_mod._execute(_catalog(scenario), RunConfig(mode="run", **target))

    return execute, echo, ran


def _meta(root: Path, run_id: str) -> dict[str, Any]:
    return json.loads((root / run_id / "run.json").read_text(encoding="utf-8"))["meta"]


def test_끊긴_시도에도_프로파일_설정_지문이_남는다(results: Path, fake_git, live_profile) -> None:
    execute, _, _ = live_profile
    with pytest.raises(KeyboardInterrupt):
        execute("20260928-000001")

    [attempt] = _meta(results, "20260928-000001")["attempts"]
    record = attempt["configs"]["baseline"]
    assert record["settings"]["API_QUERY_TIMEOUT"] == "60" and record["fingerprint"]


def test_env_상한이_바뀐_채_이으면_재개를_거부한다(results: Path, fake_git, live_profile,
                                        capsys) -> None:
    """★ run `20260922-162132` 재현 — 커밋은 같고 `.env` 만 60 → 180."""
    execute, echo, ran = live_profile
    with pytest.raises(KeyboardInterrupt):
        execute("20260928-000002")
    echo["API_QUERY_TIMEOUT"] = "180"

    summary = execute("20260928-000002", resume=True)

    meta = summary["meta"]
    assert "API_QUERY_TIMEOUT 60 -> 180" in meta["resume_refused"]
    assert meta["provenance_mixed"].startswith("재개 거부")
    assert ran == ["baseline"], "거부된 시도는 시나리오를 돌지 않는다"
    assert summary["executed_turns"] == 0
    assert all("재개 거부" in s["reason"] for s in summary["skipped"])
    assert "[멈춤] 재개 거부" in capsys.readouterr().out


def test_같은_설정으로_이으면_종전대로_재개한다(results: Path, fake_git, live_profile) -> None:
    execute, _, ran = live_profile
    with pytest.raises(KeyboardInterrupt):
        execute("20260928-000003")

    summary = execute("20260928-000003", resume=True)

    assert "resume_refused" not in summary["meta"]
    assert "provenance_mixed" not in summary["meta"]
    assert ran == ["baseline", "baseline"] and summary["executed_turns"] == 1
