"""측정 신뢰성 계약 — 수용 기준 V21·V24·V25 (plans/94 §18 · D-218).

run `20260915-131903` 은 383턴 중 **103턴(26.9%)이 러너 토큰 만료로 무효**였는데
리포트 어디에도 그 사실이 없었다. 그 결과 세 가지 거짓이 만들어졌다:

  1. R3·R4 96턴이 「전건 error」로 보고됐다 — 한 번도 측정되지 않았는데.
  2. 「과잉 거부 의심」 26건이 올라갔다 — 본군과 대조군이 함께 401 이었을 뿐이다.
  3. `--resume` 을 걸어도 그 103턴을 건너뛴다 — `already()` 가 「성공」이 아니라
     「기록됨」을 봤기 때문이다.

여기서 넷을 못박는다: 재개 기준(X-1) · 토큰 수명(T-a·T-b) · 무효 판정(T-c) ·
무효율 경고와 회귀 제외(T-e). 전부 무과금이다 — 네트워크도 서버도 없다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import pytest

from scripts.scenario import runner as runner_mod
from scripts.scenario import utf8_open
from scripts.scenario.analyze import regression
from scripts.scenario.assertions import INVALID_VERDICT, Observation, row_is_invalid
from scripts.scenario.client import ClientConfig, ScenarioClient
from scripts.scenario.report import build_summary, render_markdown

# --- X-1. 재개는 「기록됨」이 아니라 「성공」을 본다 -----------------------


def _write_raw(path: Path, rows: list[dict[str, Any]]) -> Path:
    with utf8_open(path, "w") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def _raw_row(**kwargs: Any) -> dict[str, Any]:
    base = {"profile": "baseline", "scenario_id": "R3-01", "turn": 1, "repeat": 0,
            "func_verdict": "pass", "error": None}
    base.update(kwargs)
    return base


def test_V21_재개는_무효_턴만_다시_돈다(tmp_path: Path) -> None:
    """성공 턴은 건너뛰고 401 턴만 대상이 된다."""
    path = _write_raw(tmp_path / "raw.jsonl", [
        _raw_row(scenario_id="A-01", func_verdict="pass"),
        _raw_row(scenario_id="A-02", func_verdict="fail"),
        _raw_row(scenario_id="R3-01", func_verdict=INVALID_VERDICT,
                 error="http 401 - 토큰이 만료되었습니다"),
    ])
    raw = runner_mod.RawLog(path)

    assert raw.already("baseline", "A-01", 1, 0) is True
    assert raw.already("baseline", "A-02", 1, 0) is True   # 불합격은 측정된 것이다
    assert raw.already("baseline", "R3-01", 1, 0) is False


def test_X1_T_c_이전에_적재된_401_행도_무효로_본다(tmp_path: Path) -> None:
    """**이번 사고의 복구 가능 여부가 여기에 달려 있다.**

    run 20260915-131903 의 103턴은 `func_verdict` 가 `error`/`fail` 로 적재돼 있다
    (T-c 가 없던 시점). 판정값만 보면 그 run 은 영영 무효로 식별되지 않아 `--resume` 이
    바로 그 턴들을 건너뛴다 — 계획서가 말한 "영영 복구 불가"가 정확히 이 지점이다.
    """
    path = _write_raw(tmp_path / "raw.jsonl", [
        _raw_row(scenario_id="R4-01", func_verdict="error",
                 error="http 401 - 토큰 없음/만료: 토큰이 만료되었습니다"),
        _raw_row(scenario_id="R4-02", func_verdict="fail",
                 error="http 401 - 토큰 없음/만료: 토큰이 만료되었습니다"),
    ])
    raw = runner_mod.RawLog(path)

    assert raw.already("baseline", "R4-01", 1, 0) is False
    assert raw.already("baseline", "R4-02", 1, 0) is False


def test_X1_재개로_복구한_턴은_다시_돌지_않는다(tmp_path: Path) -> None:
    """같은 키가 두 번 나오면 **뒤에 적재된 행이 결과**다 - 무한 재실행을 만들지 않는다."""
    path = _write_raw(tmp_path / "raw.jsonl", [
        _raw_row(func_verdict=INVALID_VERDICT, error="http 401"),
        _raw_row(func_verdict="pass"),
    ])

    assert runner_mod.RawLog(path).already("baseline", "R3-01", 1, 0) is True


def test_X1_적재_직후에도_같은_규칙이_선다(tmp_path: Path) -> None:
    raw = runner_mod.RawLog(tmp_path / "raw.jsonl")
    raw.append(_raw_row(turn=1, func_verdict=INVALID_VERDICT, error="http 401"))
    raw.append(_raw_row(turn=2, func_verdict="pass"))

    assert raw.already("baseline", "R3-01", 1, 0) is False
    assert raw.already("baseline", "R3-01", 2, 0) is True


@pytest.mark.parametrize(
    "row,expected",
    [
        ({"func_verdict": "pass", "error": None}, False),
        ({"func_verdict": "fail", "error": "행이 0건"}, False),
        ({"func_verdict": "error", "error": "http 500: 내부 오류"}, False),
        ({"func_verdict": "error", "error": "http 401 - 토큰 없음"}, True),
        ({"func_verdict": "error", "error": "http 403 - 권한 없음"}, True),
        ({"func_verdict": INVALID_VERDICT, "error": None}, True),
        # 응답 **본문**에 401 이 있는 것과 전송 계층 401 은 다르다.
        ({"func_verdict": "fail", "error": "ReadTimeout: 401 초 경과"}, False),
    ],
)
def test_무효_행_판별은_전송_계층_401_만_본다(row: dict[str, Any], expected: bool) -> None:
    assert row_is_invalid(row) is expected


# --- T-a. 401 이면 재로그인 후 1회 재시도 ----------------------------------


class _FakeSource:
    """`TokenProvider` 대역. 몇 번 재발급을 요청받았는지 센다."""

    def __init__(self, tokens: list[Optional[str]]) -> None:
        self._tokens = list(tokens)
        self._current: Optional[str] = "T-old"
        self.calls = 0

    @property
    def token(self) -> Optional[str]:
        return self._current

    def refresh(self) -> Optional[str]:
        self.calls += 1
        nxt = self._tokens.pop(0) if self._tokens else None
        if nxt:
            self._current = nxt
        return nxt


def _client_with(source: Any) -> ScenarioClient:
    return ScenarioClient(ClientConfig(port=1, token="T-old", token_source=source))


def test_T_a_401_이면_재로그인_후_1회_다시_보낸다(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _FakeSource(["T-new"])
    client = _client_with(source)
    seen: list[Optional[str]] = []

    def fake_dispatch(endpoint: str, payload: dict, upload: Any) -> Observation:
        seen.append(client._config.current_token)
        if len(seen) == 1:
            return Observation(status="error", http_status=401, error="http 401 - 만료")
        return Observation(status="completed", http_status=200, row_count=3)

    monkeypatch.setattr(client, "_dispatch", fake_dispatch)
    try:
        obs = client.send("stream", {"query": "q"})
    finally:
        client.close()

    assert seen == ["T-old", "T-new"]       # 재시도는 **새 토큰으로** 나간다
    assert obs.http_status == 200
    assert obs.auth_retried is True
    assert source.calls == 1


def test_T_a_재시도도_401_이면_한_번만_두드린다(monkeypatch: pytest.MonkeyPatch) -> None:
    """무한 재시도는 `max_login_attempts`(기본 5)에 걸려 계정을 잠근다."""
    source = _FakeSource(["T-new", "T-newer"])
    client = _client_with(source)
    calls = {"n": 0}

    def fake_dispatch(endpoint: str, payload: dict, upload: Any) -> Observation:
        calls["n"] += 1
        return Observation(status="error", http_status=401, error="http 401 - 만료")

    monkeypatch.setattr(client, "_dispatch", fake_dispatch)
    try:
        obs = client.send("stream", {"query": "q"})
    finally:
        client.close()

    assert calls["n"] == 2 and source.calls == 1
    assert obs.auth_retried is True


def test_T_a_재발급_경로가_없으면_조용히_넘기지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """주입 토큰(--token)에 크레덴셜이 없는 경우. 401 은 401 로 남아 무효로 적재된다."""
    client = ScenarioClient(ClientConfig(port=1, token="T-injected"))
    calls = {"n": 0}

    def fake_dispatch(endpoint: str, payload: dict, upload: Any) -> Observation:
        calls["n"] += 1
        return Observation(status="error", http_status=401, error="http 401 - 만료")

    monkeypatch.setattr(client, "_dispatch", fake_dispatch)
    try:
        obs = client.send("stream", {"query": "q"})
    finally:
        client.close()

    assert calls["n"] == 1
    assert obs.auth_retried is False and obs.http_status == 401


def test_토큰_헤더는_수명_관리자를_정본으로_쓴다() -> None:
    """값으로 복사해 두면 동시 부하(K-06·K-07)의 한 세션 재발급이 다른 세션에 닿지 않는다."""
    source = _FakeSource(["T-new"])
    config = ClientConfig(port=1, token="T-old", token_source=source)

    assert config.headers == {"Authorization": "Bearer T-old"}
    source.refresh()
    assert config.headers == {"Authorization": "Bearer T-new"}


# --- T-b. 만료 선제 갱신 ---------------------------------------------------


def _source(lifetime: float, elapsed: float, tokens: Optional[list[str]] = None,
            monkeypatch: Optional[pytest.MonkeyPatch] = None) -> runner_mod.TokenSource:
    handed = list(tokens or ["T-new"])

    def relogin() -> tuple[Optional[str], Optional[str]]:
        return (handed.pop(0) if handed else None), (None if handed else "인증 DB 없음")

    import time as _time

    now = _time.monotonic()
    source = runner_mod.TokenSource(
        token="T-old", relogin=relogin, lifetime_sec=lifetime, issued_at=now - elapsed
    )
    return source


def test_T_b_수명의_80퍼센트가_지나면_미리_받는다() -> None:
    source = _source(lifetime=8 * 3600, elapsed=6.5 * 3600)   # 81.25%
    assert source.maybe_refresh() == "T-new"
    assert source.token == "T-new"
    assert len(source.refreshes) == 1


def test_T_b_아직_여유가_있으면_받지_않는다() -> None:
    source = _source(lifetime=8 * 3600, elapsed=3 * 3600)     # 37.5%
    assert source.maybe_refresh() is None
    assert source.token == "T-old" and source.refreshes == []


def test_T_b_갱신하면_시계가_다시_선다() -> None:
    """한 번 갱신한 뒤 매 턴 재발급을 두드리지 않는다."""
    source = _source(lifetime=8 * 3600, elapsed=7 * 3600, tokens=["T-1", "T-2"])
    assert source.maybe_refresh() == "T-1"
    assert source.maybe_refresh() is None


def test_T_b_주입_토큰은_선제_갱신_대상이_아니다() -> None:
    """발급 시각을 모르는 토큰의 만료 시점을 추정하지 않는다."""
    source = runner_mod.TokenSource(token="T-injected", relogin=lambda: ("T-new", None))
    assert source.can_refresh is True          # T-a 는 살아 있다
    assert source.can_preempt is False
    assert source.maybe_refresh() is None


def test_재발급_실패는_사유로_남는다() -> None:
    source = runner_mod.TokenSource(
        token="T-old", relogin=lambda: (None, "/auth/login 로그인 실패 (http 503)")
    )
    assert source.refresh() is None
    assert source.failures and "503" in source.failures[0]


def test_주입_토큰에_크레덴셜이_없으면_재발급_경로를_만들지_않는다() -> None:
    """내장 테스트 계정으로 대신 로그인하면 **신원이 조용히 바뀐다**(D-215 전용 벤치 계정)."""
    config = runner_mod.RunConfig(mode="run", token="T-injected")
    source = runner_mod.build_token_source(7000, config, "T-injected")

    assert source.can_refresh is False and source.can_preempt is False


def test_크레덴셜을_함께_주면_재발급_경로가_산다() -> None:
    config = runner_mod.RunConfig(mode="run", token="T-injected",
                                  user_id="bench", user_password="pw")
    source = runner_mod.build_token_source(7000, config, "T-injected")

    assert source.can_refresh is True
    assert source.can_preempt is False   # 주입 토큰의 발급 시각은 여전히 모른다


# --- T-c·T-d·T-e. 무효 턴은 분모에서 빠지고 별도 절에 뜬다 ------------------


def _row(**kwargs: Any) -> dict[str, Any]:
    base = {
        "run_id": "r", "profile": "baseline", "env": "sandbox", "mode": "mock",
        "repeat": 0, "group": "T", "scenario_id": "T-01", "turn": 1, "plans": [94],
        "kind": "normal", "pair_id": None, "func_verdict": "pass", "perf_verdict": "pass",
        "response_mode": "answer", "forbidden_mode": None, "failed_assertions": [],
        "manual_notes": [], "wall_ms": 100.0, "processing_time_ms": 100.0,
        "node_elapsed_ms": {}, "node_path": [], "sse_events": [], "executed_sql": "SELECT 1",
        "row_count": 5, "artifacts": [], "error": None, "invalid_reason": None,
    }
    base.update(kwargs)
    return base


def _invalid(**kwargs: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "func_verdict": INVALID_VERDICT, "perf_verdict": "n/a", "response_mode": "error",
        "processing_time_ms": None, "executed_sql": None, "row_count": None,
        "error": "http 401 - 토큰이 만료되었습니다",
        "invalid_reason": "러너 인증 실패 - http 401 - 토큰이 만료되었습니다",
    }
    defaults.update(kwargs)
    return _row(**defaults)


def _make_run(tmp_path: Path, rows: list[dict[str, Any]], name: str = "20260916-000000") -> Path:
    run_dir = tmp_path / name
    run_dir.mkdir(parents=True)
    _write_raw(run_dir / "raw.jsonl", rows)
    with utf8_open(run_dir / "run.json", "w") as handle:
        json.dump({
            "meta": {"run_id": name, "env": "sandbox", "mode": "mock", "repeat": 1,
                     "provider": "mock"},
            "profiles": [{"name": "baseline", "port": 1, "valid": True, "tier": "mock",
                          "echo_ok": None, "reasons": []}],
            "skipped": [],
        }, handle, ensure_ascii=False)
    return run_dir


def test_V24_무효_턴은_판정표_분모에서_빠진다(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path, [
        _row(scenario_id="T-01", func_verdict="pass"),
        _invalid(scenario_id="T-02"),
        _invalid(scenario_id="T-03"),
    ])
    summary = build_summary(run_dir, None)
    group = summary["groups"]["T"]

    assert (group["pass"], group["fail"], group["error"]) == (1, 0, 0)
    assert group["invalid"] == 2
    assert summary["failures"] == []          # 실패 분류에도 올라가지 않는다
    assert summary["invalid"]["count"] == 2


def test_무효_턴은_지연_표본에도_들어가지_않는다(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path, [
        _row(scenario_id="T-01", processing_time_ms=100.0),
        _invalid(scenario_id="T-02", processing_time_ms=9_000.0),
    ])
    latency = build_summary(run_dir, None)["groups"]["T"]["latency"]

    assert latency["n"] == 1 and latency["max"] == 100.0


def test_일부만_무효인_시나리오는_남은_유효_턴으로_판정한다(tmp_path: Path) -> None:
    """401 한 번에 시나리오 전체가 불합격이 되던 것이 허위 신호의 절반이었다."""
    run_dir = _make_run(tmp_path, [
        _row(scenario_id="K-01", repeat=0, func_verdict="pass"),
        _invalid(scenario_id="K-01", repeat=1),
    ])
    verdicts = build_summary(run_dir, None)["scenario_verdicts"]

    assert verdicts["K-01"]["verdict"] == "pass"
    assert verdicts["K-01"]["invalid_repeats"] == 1


def test_전건_무효_시나리오는_불합격이_아니라_무효다(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path, [_invalid(scenario_id="R3-01", repeat=r) for r in range(3)])
    verdicts = build_summary(run_dir, None)["scenario_verdicts"]

    assert verdicts["R3-01"]["verdict"] == INVALID_VERDICT
    assert verdicts["R3-01"]["repeats"] == 0


def test_T_d_과잉_거부_의심은_유효_턴에서만_산출된다(tmp_path: Path) -> None:
    """본군과 대조군이 **함께 401** 인 것을 「가드가 정상 동작까지 막았다」로 읽지 않는다.

    run 20260915-131903 의 26건이 전건 이 모양이었다.
    """
    rows = [
        _invalid(group="R3", scenario_id="R3-01", kind="mistake", pair_id="R3-01C"),
        _invalid(group="R3", scenario_id="R3-01C", kind="control", pair_id="R3-01"),
    ]
    misuse = build_summary(_make_run(tmp_path, rows), None)["misuse"]

    assert "R3" not in misuse or misuse["R3"]["control_broken"] == 0


def test_T_d_유효_턴에서_함께_깨진_쌍은_여전히_잡는다(tmp_path: Path) -> None:
    """T-d 가 과잉 거부 탐지를 꺼 버리지는 않는다."""
    rows = [
        _row(group="R3", scenario_id="R3-01", kind="mistake", pair_id="R3-01C",
             func_verdict="fail", response_mode="refuse"),
        _row(group="R3", scenario_id="R3-01C", kind="control", pair_id="R3-01",
             func_verdict="fail", response_mode="refuse"),
    ]
    misuse = build_summary(_make_run(tmp_path, rows), None)["misuse"]

    assert misuse["R3"]["control_broken"] == 1


def test_V25_무효율_5퍼센트_초과면_리포트_최상단에_경고가_뜬다(tmp_path: Path) -> None:
    rows = [_row(scenario_id=f"T-{i:02d}") for i in range(90)]
    rows += [_invalid(scenario_id=f"R3-{i:02d}") for i in range(10)]
    run_dir = _make_run(tmp_path, rows)
    summary = build_summary(run_dir, None)
    markdown = render_markdown(summary, run_dir, None)

    assert summary["invalid"]["over_threshold"] is True
    head = markdown.split("## 1.")[0]
    assert "[경고]" in head and "무효 턴 10건" in head
    assert "회귀 비교 대상에서 제외" in head


def test_V25_무효율이_낮으면_경고하지_않는다(tmp_path: Path) -> None:
    rows = [_row(scenario_id=f"T-{i:02d}") for i in range(99)] + [_invalid(scenario_id="T-99")]
    run_dir = _make_run(tmp_path, rows)
    summary = build_summary(run_dir, None)

    assert summary["invalid"]["over_threshold"] is False
    assert "[경고] 무효 턴" not in render_markdown(summary, run_dir, None).split("## 1.")[0]


def test_V25_무효율_초과_run_은_회귀_비교에서_빠진다(tmp_path: Path) -> None:
    previous = _make_run(tmp_path, [_row(func_verdict="pass")], name="20260915-000000")
    rows = [_row(scenario_id=f"T-{i:02d}") for i in range(90)]
    rows += [_invalid(scenario_id=f"R3-{i:02d}") for i in range(10)]
    current = _make_run(tmp_path, rows, name="20260916-000000")
    summary = build_summary(current, None)

    assert previous.exists()
    assert "회귀 비교 **제외**" in render_markdown(summary, current, None)
    assert "회귀 비교 **제외**" in regression(current, summary)


def test_무효_턴_절이_건수와_구간을_명시한다(tmp_path: Path) -> None:
    """「filled_rows 0」이 *빈 파일*로 읽혔듯, 「error 48건」은 *제품 실패*로 읽힌다."""
    rows = [_row(scenario_id=f"T-{i:02d}") for i in range(5)]
    rows += [_invalid(scenario_id=f"R3-{i:02d}", group="R3") for i in range(3)]
    run_dir = _make_run(tmp_path, rows)
    markdown = render_markdown(build_summary(run_dir, None), run_dir, None)

    assert "### 무효 턴(측정 미성립)" in markdown
    assert "6~8번째 턴" in markdown            # 실행 순서 구간
    assert "R3:3" in markdown                  # 군별 건수
    assert "--resume" in markdown              # 무엇을 하면 복구되는가


# --- X-2·X-3. 분할 실행과 무효 복구 (V22·V23) ------------------------------


def _scn(scenario_id: str, cache_state: str = "warm"):
    from scripts.scenario.catalog import Scenario, Turn

    return Scenario(
        id=scenario_id, group=scenario_id[0], plans=[94], title="t",
        cache_state=cache_state, turns=[Turn(send={"query": "q"}, expect={})],
    )


def test_V22_세그먼트는_시나리오_경계에서만_끊는다() -> None:
    """턴 경계로 끊으면 멀티턴 승계(thread_id·이전 턴 DB)가 깨져 다른 것을 재게 된다."""
    scenarios = [_scn(f"T-{i:02d}") for i in range(5)]
    chunks = runner_mod.segments(scenarios, 2)

    assert [[s.id for s in c] for c in chunks] == [
        ["T-00", "T-01"], ["T-02", "T-03"], ["T-04"],
    ]
    # 어느 시나리오도 두 세그먼트에 걸치지 않는다
    seen = [s.id for c in chunks for s in c]
    assert len(seen) == len(set(seen)) == 5


def test_V22_분할하지_않으면_통째로_한_세그먼트다() -> None:
    scenarios = [_scn(f"T-{i:02d}") for i in range(3)]
    for size in (None, 0):
        assert len(runner_mod.segments(scenarios, size)) == 1


def test_V22_cold_묶음은_첫_세그먼트에_남는다() -> None:
    """K-02 는 "서버 기동 직후 첫 요청"으로 cold 를 근사한다 - 순서가 깨지면 무의미하다."""
    ordered = sorted(
        [_scn("T-01"), _scn("K-02", cache_state="cold"), _scn("T-02")],
        key=runner_mod._execution_order,
    )
    first = runner_mod.segments(ordered, 1)[0]

    assert [s.id for s in first] == ["K-02"]


def test_V23_무효와_오류_시나리오만_고른다(tmp_path: Path, monkeypatch) -> None:
    """불합격(`fail`)은 제외한다 - 측정이 성립한 결과라 다시 돌려도 달라지지 않는다."""
    run_dir = tmp_path / "20260915-131903"
    run_dir.mkdir()
    _write_raw(run_dir / "raw.jsonl", [
        _raw_row(scenario_id="A-01", func_verdict="pass"),
        _raw_row(scenario_id="B-04", func_verdict="fail", error="행이 0건"),
        _raw_row(scenario_id="C-06", func_verdict="error", error="http 500: 내부 오류"),
        _raw_row(scenario_id="R2-09", func_verdict=INVALID_VERDICT, error="http 401"),
    ])
    monkeypatch.setattr(runner_mod, "RESULTS_ROOT", tmp_path)

    ids, stats = runner_mod.failed_scenarios("20260915-131903")

    assert ids == ["C-06", "R2-09"]
    assert stats == {"rows": 4, "invalid_turns": 1, "error_turns": 1, "scenarios": 2}


def test_V23_T_c_이전_적재본의_401도_복구_대상이다(tmp_path: Path, monkeypatch) -> None:
    """**X-3 의 존재 이유.** run 20260915-131903 의 103턴은 `error`/`fail` 로 적재돼 있다.

    `func_verdict == "invalid"` 만 보면 그 run 이 통째로 빠져 복구가 안 된다.
    """
    run_dir = tmp_path / "20260915-131903"
    run_dir.mkdir()
    legacy = "http 401 - 토큰 없음/만료: 토큰이 만료되었습니다"
    _write_raw(run_dir / "raw.jsonl", [
        _raw_row(scenario_id="R3-01", func_verdict="error", error=legacy),
        _raw_row(scenario_id="R3-01C", func_verdict="fail", error=legacy),
        _raw_row(scenario_id="R4-02", func_verdict="fail", error=legacy),
    ])
    monkeypatch.setattr(runner_mod, "RESULTS_ROOT", tmp_path)

    ids, stats = runner_mod.failed_scenarios("20260915-131903")

    assert ids == ["R3-01", "R3-01C", "R4-02"]
    assert stats["invalid_turns"] == 3        # fail 로 적재됐어도 무효로 센다
    assert stats["error_turns"] == 0


def test_V23_같은_시나리오는_한_번만_고른다(tmp_path: Path, monkeypatch) -> None:
    """R3-01 이 48턴 전건 401 이어도 재실행 대상 시나리오는 1건이다."""
    run_dir = tmp_path / "r"
    run_dir.mkdir()
    _write_raw(run_dir / "raw.jsonl", [
        _raw_row(scenario_id="R3-01", turn=t, func_verdict=INVALID_VERDICT, error="http 401")
        for t in range(1, 49)
    ])
    monkeypatch.setattr(runner_mod, "RESULTS_ROOT", tmp_path)

    ids, stats = runner_mod.failed_scenarios("r")
    assert ids == ["R3-01"] and stats["invalid_turns"] == 48


def test_V23_원본이_없으면_조용히_넘기지_않는다(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(runner_mod, "RESULTS_ROOT", tmp_path)
    with pytest.raises(FileNotFoundError):
        runner_mod.failed_scenarios("없는run")


# --- O-c. 사다리 강등 경고 --------------------------------------------------


def _summary_with_tier(tier: str, reason: str = "intent_flag_on") -> dict[str, Any]:
    return {
        "meta": {"run_id": "r", "mode": "run"},
        "profiles": [{"name": "baseline", "port": 1, "valid": True,
                      "tier": tier, "degraded_reason": reason, "echo_ok": True, "reasons": []}],
        "groups": {}, "plans_coverage": {}, "misuse": {}, "failures": [],
        "skipped": [], "silent_wrong_total": 0, "scenario_verdicts": {},
        "invalid": {"count": 0, "total_turns": 0, "ratio": 0.0, "over_threshold": False,
                    "by_group": {}, "first_index": None, "last_index": None,
                    "first_scenario": None, "turns": []},
    }


@pytest.mark.parametrize(
    "tier,reason",
    [("intent_orchestration", "intent_flag_on"), ("legacy", "semantic_routing_off")],
)
def test_Oc_기준_단이_아니면_최상단에_경고가_뜬다(tier: str, reason: str, tmp_path: Path) -> None:
    """지난 run 은 2단으로 돌았는데 그 사실이 1절 표의 한 칸이었다. 기준 단은 3단이다(D-225)."""
    summary = _summary_with_tier(tier, reason)
    head = render_markdown(summary, tmp_path, None).split("## 1.")[0]

    assert "[경고] 기준 단이 아닌 실행 단으로 측정됐다" in head
    assert tier in head and reason in head
    assert "[안내]" not in head


@pytest.mark.parametrize("tier,notice", [("semantic_router", False), ("deep_agent", True)])
def test_Oc_기준_단과_부가_경로_단은_경고하지_않는다(
    tier: str, notice: bool, tmp_path: Path
) -> None:
    """기준 3단은 아무것도 올리지 않는다. 1단 opt-in 은 강등이 아니지만, 판정표가 기준 단
    수치가 아니라는 사실은 **안내**로 남긴다(D-225 ②)."""
    summary = _summary_with_tier(tier, "none")
    head = render_markdown(summary, tmp_path, None).split("## 1.")[0]
    assert "기준 단이 아닌" not in head
    assert ("[안내] 부가 경로(opt-in) 단으로 측정됐다" in head) is notice
    assert ("`baseline` = **deep_agent**" in head) is notice


@pytest.mark.parametrize("tier", ["mock", None, ""])
def test_Oc_미관측은_강등이_아니다(tier, tmp_path: Path) -> None:
    """미관측을 강등으로 세면 경고가 상시 켜져 사람이 읽지 않게 된다."""
    head = render_markdown(_summary_with_tier(tier), tmp_path, None).split("## 1.")[0]
    assert "기준 단이 아닌" not in head
    assert "[안내]" not in head


# --- T-b 토큰 수명: 읽지 않고 주입한다 (2026-09-16 개정) -------------------

def test_토큰_수명은_주입값이라_env_를_읽지_않는다(monkeypatch) -> None:
    """러너가 서버를 직접 띄우므로 수명은 추정 대상이 아니라 러너가 정한 값이다.

    종전에는 `AuthConfig.jwt_expire_hours` 를 읽어 맞혔다. 그러면 OS env·.encenv
    우선순위로 실효값이 달라져도 러너는 자기가 맞다고 믿고 엉뚱한 시점에 갱신한다.
    """
    import src.config

    def boom():
        raise RuntimeError("설정을 못 읽는 상황")

    monkeypatch.setattr(src.config, "load_config", boom)
    # 설정을 못 읽어도 주입값이 있으므로 선제 갱신은 살아 있다.
    assert runner_mod.jwt_lifetime_sec() == runner_mod.SERVER_JWT_EXPIRE_HOURS * 3600.0


def test_주입_수명은_서버에_실제로_주입되고_에코_대상이다() -> None:
    """주입하지 않으면 T-b 의 근거가 추정으로 돌아간다.

    `ISOLATION_ENV` 는 `expected` 로도 쓰여(runner `_execute`) 설정 에코 대조를 받는다 —
    주입이 먹지 않으면 프로파일이 INVALID 로 선다. `AUTH_JWT_EXPIRE_HOURS` 가
    `src/api/settings_catalog.py` 의 노출 목록에 있어야 이 대조가 성립한다.
    """
    assert runner_mod.ISOLATION_ENV["AUTH_JWT_EXPIRE_HOURS"] == str(
        runner_mod.SERVER_JWT_EXPIRE_HOURS
    )
    from src.api import settings_catalog

    assert "AUTH_JWT_EXPIRE_HOURS" in settings_catalog.RELOADABLE_KEYS


def test_외부_서버에_붙으면_주입이_성립하지_않아_읽는다(monkeypatch) -> None:
    """`--port` 로 남이 띄운 서버에 붙으면 러너가 수명을 정하지 못한다."""
    import src.config

    monkeypatch.setattr(
        src.config, "load_config", lambda: type("C", (), {"auth": type("A", (), {"jwt_expire_hours": 3})()})()
    )
    assert runner_mod.jwt_lifetime_sec(injected=False) == 3 * 3600.0
