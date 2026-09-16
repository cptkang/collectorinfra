"""인증 경로 — 벤치 러너가 서버에 실제로 붙는가 (2026-09-14 회귀).

실 스위프 1984턴이 **전량 HTTP 401** 로 끝났다. 러너가 토큰을 한 번도 받지 않았고,
설정 에코도 401 이었는데 62개 프로파일이 전부 valid 로 기록됐다. 원인은 셋이다.

  1. 로그인을 부르는 코드가 없다 (`login()` 은 정의만 있고 호출부 0)
  2. `login()` 본문 키가 서버 계약과 다르다 (`username` vs `user_id`)
  3. HTTP 4xx 가 관측치의 `error` 로 옮겨지지 않아 판정이 `manual` 로 샜다

여기서 셋 모두를 못박는다. 네트워크 없이 전송 계층만 대역한다.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from scripts.scenario import runner as runner_mod
from scripts.scenario.assertions import INVALID_VERDICT, Observation, evaluate_turn
from scripts.scenario.catalog import Group, Scenario, Turn
from scripts.scenario.client import ClientConfig, ScenarioClient


class FakeHTTPResponse:
    def __init__(self, status_code: int, payload: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@pytest.fixture()
def client() -> Any:
    instance = ScenarioClient(ClientConfig(port=1))
    yield instance
    instance.close()


# --- 로그인 계약 ---------------------------------------------------------

def test_사용자_로그인은_user_id_키로_보낸다(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """서버는 `UserLoginRequest.user_id` 를 요구한다. `username` 이면 401 이 아니라 422 다."""
    sent: dict[str, Any] = {}

    def fake_post(url: str, json: dict, timeout: float) -> FakeHTTPResponse:
        sent["url"] = url
        sent["json"] = json
        return FakeHTTPResponse(200, {"access_token": "T-user"})

    monkeypatch.setattr(client._client, "post", fake_post)
    token, error = client.login("bench", "pw")

    assert (token, error) == ("T-user", None)
    assert sent["url"].endswith("/auth/login")
    assert sent["json"] == {"user_id": "bench", "password": "pw"}


def test_운영자_로그인은_username_키로_보낸다(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """운영자 계약은 반대다 - 한 함수로 합칠 수 없는 이유다."""
    sent: dict[str, Any] = {}

    def fake_post(url: str, json: dict, timeout: float) -> FakeHTTPResponse:
        sent["url"] = url
        sent["json"] = json
        return FakeHTTPResponse(200, {"access_token": "T-admin"})

    monkeypatch.setattr(client._client, "post", fake_post)
    token, error = client.admin_login("admin", "pw")

    assert (token, error) == ("T-admin", None)
    assert sent["url"].endswith("/admin/login")
    assert sent["json"] == {"username": "admin", "password": "pw"}


def test_로그인_실패는_상태코드와_본문을_함께_남긴다(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """401(크레덴셜)·422(계약)·503(인증DB)은 조치가 전부 달라 구별돼야 한다."""
    monkeypatch.setattr(
        client._client, "post",
        lambda url, json, timeout: FakeHTTPResponse(422, text="field required: user_id"),
    )
    token, error = client.login("bench", "pw")

    assert token is None
    assert "422" in error and "user_id" in error


# --- 토큰 분리 -----------------------------------------------------------

def test_설정_에코는_운영자_토큰을_쓴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/admin/settings/schema` 는 require_admin_user 다 - 질의 토큰으로는 열리지 않는다(D-070)."""
    config = ClientConfig(port=1, token="T-user", admin_token="T-admin")
    instance = ScenarioClient(config)
    seen: dict[str, Any] = {}

    def fake_get(url: str, headers: dict, timeout: float) -> FakeHTTPResponse:
        seen["headers"] = headers
        return FakeHTTPResponse(200, {"groups": []})

    monkeypatch.setattr(instance._client, "get", fake_get)
    try:
        values, error = instance.effective_settings()
    finally:
        instance.close()

    assert error is None and values == {}
    assert seen["headers"] == {"Authorization": "Bearer T-admin"}


# --- 4xx 는 오류다, 보류가 아니다 ----------------------------------------

def test_401_은_manual_이_아니라_무효로_판정된다() -> None:
    """종전에는 obs.error 가 비어 판정기가 `manual` 을 줬다 - 1984건이 전부 그렇게 샜다.

    **T-c(D-218): `error` 가 아니라 `invalid` 다.** D-217 이 401 을 보이게 만들었더니
    이번에는 `http_status` 단언이 기대 200 vs 실제 401 을 **기능 불합격**으로 세어,
    run 20260915-131903 에서 불합격 31건·「과잉 거부 의심」 26건이 전건 허위가 됐다.
    401 은 보이되 **기능 판정의 분모에서는 빠져야** 한다.
    """
    scenario = Scenario(
        id="X-1", group="T", plans=[93], title="t",
        turns=[Turn(send={"query": "q"}, expect={})],
    )
    group = Group(id="T", name="t", latency_target_ms=10000)
    obs = Observation(status="error", http_status=401, response="Not authenticated")
    obs.error = "http 401 - 토큰 없음/만료"

    verdict = evaluate_turn(scenario, 1, scenario.turns[0], obs, group)

    assert verdict.func == INVALID_VERDICT
    assert verdict.func not in ("manual", "pass")
    assert verdict.response_mode == "error"
    assert "401" in (verdict.invalid_reason or "")


def test_V24_401_은_기능_단언을_불합격으로_남기지_않는다() -> None:
    """기대 200 vs 실제 401 을 기능 불합격으로 세면 판정표가 통째로 거짓이 된다(T-c)."""
    scenario = Scenario(
        id="X-2", group="T", plans=[94], title="t",
        turns=[Turn(send={"query": "q"}, expect={"http_status": 200, "row_count": {"min": 1}})],
    )
    group = Group(id="T", name="t", latency_target_ms=10000)
    obs = Observation(status="error", http_status=401, error="http 401 - 토큰이 만료되었습니다")

    verdict = evaluate_turn(scenario, 1, scenario.turns[0], obs, group)

    assert verdict.func == INVALID_VERDICT
    assert verdict.failures == []          # http_status·row_count 가 401 의 그림자로 세이지 않는다
    assert verdict.perf == "n/a"           # 인증에 튕긴 왕복을 지연 표본으로 쓰지 않는다


def test_401_을_기대하는_턴은_무효가_아니다() -> None:
    """기대한 오류는 측정이 성립한 것이다 - 가드 시나리오를 무효로 지우지 않는다."""
    scenario = Scenario(
        id="X-3", group="T", plans=[94], title="t",
        turns=[Turn(send={"query": "q"}, expect={"http_status": 401})],
    )
    group = Group(id="T", name="t", latency_target_ms=10000)
    obs = Observation(status="error", http_status=401, error="http 401: 인증 필요")

    assert evaluate_turn(scenario, 1, scenario.turns[0], obs, group).func == "pass"


# --- 기동마다 다시 받는다 ------------------------------------------------

class FakeLoginClient:
    """ScenarioClient 대역. 포트별로 다른 토큰을 준다."""

    created: list[int] = []

    def __init__(self, config: ClientConfig) -> None:
        self.port = config.port
        FakeLoginClient.created.append(config.port)

    def admin_login(self, user: str, password: str) -> tuple[Optional[str], Optional[str]]:
        return f"admin-{self.port}", None

    def login(self, user_id: str, password: str) -> tuple[Optional[str], Optional[str]]:
        return f"user-{self.port}", None

    def close(self) -> None:
        pass


def test_토큰은_프로파일마다_다시_받는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """JWT 시크릿이 .env 에 없으면 기동마다 난수다 - 앞 기동의 토큰은 다음에서 401 이다."""
    FakeLoginClient.created = []
    monkeypatch.setattr(runner_mod, "ScenarioClient", FakeLoginClient)
    config = runner_mod.RunConfig(
        user_id="bench", user_password="pw", admin_user="admin", admin_password="pw"
    )

    first = runner_mod.acquire_tokens(5001, config)
    second = runner_mod.acquire_tokens(5002, config)

    assert first[:2] == ("user-5001", "admin-5001")
    assert second[:2] == ("user-5002", "admin-5002")
    assert FakeLoginClient.created == [5001, 5002]


def test_운영자_크레덴셜은_설정에서_읽고_사용자_것은_읽지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ADMIN_USERNAME`/`ADMIN_PASSWORD` 는 `.env` 에 이미 있다 - 다시 타이핑시키지 않는다.

    사용자 크레덴셜은 설정에 없다(인증 DB 소관). 그래서 사용자 토큰은 명시해야만 생기고,
    인증이 켜진 서버에서 계정이 없으면 스위프는 서버를 띄우기 전에 멈춘다(G-3).
    """
    FakeLoginClient.created = []
    monkeypatch.setattr(runner_mod, "ScenarioClient", FakeLoginClient)
    monkeypatch.setattr(runner_mod, "resolve_admin_credentials",
                        lambda u, p: (u or "cfg-admin", p or "cfg-pw"))

    user, admin, reasons = runner_mod.acquire_tokens(5003, runner_mod.RunConfig())

    assert admin == "admin-5003", "운영자는 설정에서 읽어 로그인한다"
    assert user is None, "사용자는 설정에 없으므로 로그인하지 않는다"
    assert reasons == []


def test_설정을_읽지_못해도_토큰_획득이_죽지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """인증이 꺼진 서버는 토큰 없이 성립한다 - 설정 로드 실패가 런을 죽이면 안 된다."""
    FakeLoginClient.created = []
    monkeypatch.setattr(runner_mod, "ScenarioClient", FakeLoginClient)
    monkeypatch.setattr(runner_mod, "resolve_admin_credentials", lambda u, p: (None, None))

    assert runner_mod.acquire_tokens(5004, runner_mod.RunConfig()) == (None, None, [])


# --- 인증이 켜졌는데 토큰이 없으면 실행하지 않는다 -----------------------

class _StubHandle:
    """ServerHandle 대역. 프로세스를 띄우지 않는다."""

    instances: list = []

    def __init__(self, profile, env_overrides, port, log_path, mock):
        self.profile = profile
        self.port = port
        self.mock = mock
        self.ladder = ("intent_orchestration", "flag_off")
        _StubHandle.instances.append(self)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def port_released(self) -> bool:
        return True

    def wait_healthy(self, timeout_sec: float = 90.0):
        return True, "health 200"


def test_AUTH_ENABLED_인데_토큰이_없으면_시나리오를_돌리지_않는다(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """전건 401 원시 로그는 '실행되지 않음'과 구별되지 않아 리포트를 거짓으로 만든다."""
    from scripts.scenario.catalog import Catalog, Group, Scenario, Turn
    from scripts.scenario.server import ProfileStatus

    _StubHandle.instances = []
    monkeypatch.setattr(runner_mod, "RESULTS_ROOT", tmp_path)
    monkeypatch.setattr(runner_mod, "ServerHandle", _StubHandle)
    monkeypatch.setattr(runner_mod, "pick_port", lambda port=None: 5099)

    def fake_verify(handle, overrides, admin_token, expected_tier=None):
        status = ProfileStatus(name=handle.profile, port=handle.port)
        status.valid = True
        status.echo_ok = True
        status.auth_enabled = True           # 서버는 인증을 켠 채로 떴다
        return status

    monkeypatch.setattr("scripts.scenario.server.verify_profile", fake_verify)

    catalog = Catalog(
        groups={"T": Group(id="T", name="t", latency_target_ms=10000)},
        scenarios=[Scenario(id="T-01", group="T", plans=[93], title="t",
                            turns=[Turn(send={"query": "q"}, expect={})],
                            profile="baseline")],
        profiles={"baseline": {}},
    )
    summary = runner_mod.execute(catalog, runner_mod.RunConfig(mode="run", env="sandbox"))

    assert summary["executed_turns"] == 0
    assert len(summary["skipped"]) == 1
    assert "401" in summary["skipped"][0]["reason"]
    assert summary["profiles"][0]["valid"] is False


# --- 비용 축은 잴 수 있는 것으로 잰다 (2026-09-14 회귀) ------------------
#
# 실행된 두 런 3968행 전부에서 `llm_calls`·`tokens`·`retries` 가 비어 있었다.
# 셋 다 관측치에 선언돼 있고 원시 로그·예산 단언·93 비용 비교가 소비하는데,
# **대입하는 코드가 어디에도 없었다.** LLM 호출 수와 토큰 수는 `done` 페이로드에
# 아예 없어 구조적으로 측정 불가다. 재시도와 노드 수는 스트림에서 실제로 세진다.

def test_재시도는_회귀_노드_재진입으로_실측된다(client) -> None:
    import json as _json
    import time as _time

    class _Resp:
        def __init__(self, lines):
            self._lines = lines

        def iter_lines(self):
            for item in self._lines:
                yield f"data: {_json.dumps(item, ensure_ascii=False)}"
                yield ""

    # 서버는 node_start 를 노드마다 **한 번만** 보내고 node_complete 는 회차마다 보낸다
    # (query.py `_seen_nodes`). 회귀 회차는 query_generator 의 완료 횟수로 드러난다.
    events = []
    for i, (kind, n) in enumerate([
            ("node_start", "schema_analyzer"), ("node_complete", "schema_analyzer"),
            ("node_start", "query_generator"), ("node_complete", "query_generator"),
            ("node_start", "query_validator"), ("node_complete", "query_validator"),
            ("node_complete", "query_generator"), ("node_complete", "query_validator"),  # 1회 회귀
            ("node_complete", "query_generator"),                                         # 2회 회귀
            ("node_start", "query_executor"), ("node_complete", "query_executor"),
            ("node_start", "result_organizer"), ("node_start", "output_generator")]):
        events.append({"type": kind, "node": n, "timestamp_ms": i * 10})
    events.append({"type": "done", "response": "ok", "executed_sql": "SELECT 1"})

    obs = Observation()
    client._consume_sse(_Resp(events), obs, started=_time.perf_counter())

    assert obs.retries == 2, "query_generator 재진입 2회 = 재시도 2회"
    assert obs.node_count == 9
    assert obs.llm_calls is None and obs.tokens is None, "추정치를 만들지 않는다"


def test_회귀가_없으면_재시도는_0이다(client) -> None:
    import json as _json
    import time as _time

    class _Resp:
        def __init__(self, lines):
            self._lines = lines

        def iter_lines(self):
            for item in self._lines:
                yield f"data: {_json.dumps(item, ensure_ascii=False)}"
                yield ""

    events = [
        {"type": "node_start", "node": "query_generator", "timestamp_ms": 0},
        {"type": "node_complete", "node": "query_generator", "timestamp_ms": 5},
        {"type": "node_start", "node": "query_executor", "timestamp_ms": 5},
    ]
    events.append({"type": "done", "response": "ok"})

    obs = Observation()
    client._consume_sse(_Resp(events), obs, started=_time.perf_counter())

    assert obs.retries == 0 and obs.node_count == 2


# --- kill 로 멈춰도 서버를 남기지 않는다 (2026-09-14) ----------------------
#
# 프로파일 서버는 별도 세션에 뜬다. 부모가 SIGTERM 에 죽으면 파이썬 기본 동작은
# `finally` 없이 종료라 자식이 고아로 남았다 — 강제 종료된 런들에서 모의 서버 7개가
# ppid=1 로 남아 있었다. nohup 장시간 런을 멈추는 방법이 kill 이다.

def _one_scenario_catalog():
    from scripts.scenario.catalog import Catalog, Group, Scenario, Turn

    return Catalog(
        groups={"T": Group(id="T", name="t", latency_target_ms=10000)},
        scenarios=[Scenario(id="T-01", group="T", plans=[94], title="t",
                            turns=[Turn(send={"query": "q"}, expect={})],
                            profile="baseline")],
        profiles={"baseline": {"TEXT2SQL_X": "true"}},
    )


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX 신호 전달 경로")
def test_SIGTERM_을_받아도_프로파일_서버를_정리한다(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import signal as _signal
    import time as _time

    stopped: list[str] = []

    class _KilledDuringStart(_StubHandle):
        def start(self) -> None:
            os.kill(os.getpid(), _signal.SIGTERM)   # 기동 도중 kill 이 들어온다
            _time.sleep(1.0)

        def stop(self) -> None:
            stopped.append(self.profile)

    monkeypatch.setattr(runner_mod, "RESULTS_ROOT", tmp_path)
    monkeypatch.setattr(runner_mod, "ServerHandle", _KilledDuringStart)
    monkeypatch.setattr(runner_mod, "pick_port", lambda port=None: 5097)
    before = _signal.getsignal(_signal.SIGTERM)

    with pytest.raises(SystemExit) as exited:
        runner_mod.execute(_one_scenario_catalog(),
                           runner_mod.RunConfig(mode="mock", env="sandbox", run_id="sigterm"))

    assert stopped == ["baseline"], "finally 가 돌지 않으면 서버가 고아로 남는다"
    assert exited.value.code == 128 + _signal.SIGTERM
    assert _signal.getsignal(_signal.SIGTERM) == before, "런이 끝나면 신호 처리를 되돌린다"


def test_알람_워커는_모든_프로파일에서_끄고_에코로_확인한다(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """운영 `alarm:raw` 를 같은 consumer group 으로 나눠 소비하지 않게 한다."""
    from scripts.scenario.server import ProfileStatus

    seen: dict[str, dict[str, str]] = {}

    class _Capture(_StubHandle):
        def __init__(self, profile, env_overrides, port, log_path, mock):
            super().__init__(profile, env_overrides, port, log_path, mock)
            seen["env"] = dict(env_overrides)

    def fake_verify(handle, overrides, admin_token, expected_tier=None):
        seen["verified"] = dict(overrides)
        status = ProfileStatus(name=handle.profile, port=handle.port)
        status.reasons.append("테스트 - 기동 검증에서 멈춘다")
        return status

    monkeypatch.setattr(runner_mod, "RESULTS_ROOT", tmp_path)
    monkeypatch.setattr(runner_mod, "ServerHandle", _Capture)
    monkeypatch.setattr(runner_mod, "pick_port", lambda port=None: 5096)
    monkeypatch.setattr(runner_mod, "acquire_tokens", lambda port, config: (None, None, []))
    monkeypatch.setattr("scripts.scenario.server.verify_profile", fake_verify)

    runner_mod.execute(_one_scenario_catalog(),
                       runner_mod.RunConfig(mode="run", env="sandbox", run_id="isolate"))

    assert seen["env"]["ALARM_ENABLED"] == "false"
    assert seen["verified"]["ALARM_ENABLED"] == "false", "주입했으면 에코로 확인해야 한다"
    assert seen["verified"]["TEXT2SQL_X"] == "true", "프로파일 값은 그대로 실린다"
    assert "CHECKPOINT_DB_URL" not in seen["verified"], "런 전용 경로는 에코 대조 대상이 아니다"


def test_프로파일이_알람을_명시하면_프로파일이_이긴다() -> None:
    """알람 자체를 시험하는 프로파일은 격리 기본값에 막히면 안 된다."""
    merged = {**runner_mod.ISOLATION_ENV, **{"ALARM_ENABLED": "true"}}
    assert merged["ALARM_ENABLED"] == "true"


def test_회귀_노드가_스트림에_없으면_재시도는_측정_불가다(client) -> None:
    """intent_orchestration 단은 SQL 생성이 하위 에이전트 안에서 돌아 상위 스트림에 보이지 않는다."""
    import json as _json
    import time as _time

    class _Resp:
        def __init__(self, lines):
            self._lines = lines

        def iter_lines(self):
            for item in self._lines:
                yield f"data: {_json.dumps(item, ensure_ascii=False)}"
                yield ""

    events = [{"type": "node_start", "node": n, "timestamp_ms": 0}
              for n in ("context_resolver", "input_parser", "field_mapper", "intent_planner",
                        "agent_orchestrator", "replanner", "result_aggregator")]
    events.append({"type": "done", "response": "ok"})

    obs = Observation()
    client._consume_sse(_Resp(events), obs, started=_time.perf_counter())

    assert obs.retries is None, "0 은 '재시도 없음'이다 — 보지 못한 것을 0 으로 적지 않는다"
    assert obs.node_count == 7
