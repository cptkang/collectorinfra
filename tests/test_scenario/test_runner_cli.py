"""러너·진입점 수용 기준 V11 + D-127 과금 게이트 (plans/94 §4.4 · §10)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.scenario import runner as runner_mod
from scripts.scenario.__main__ import main
from scripts.scenario.catalog import Catalog, Group, Scenario, Turn
from scripts.scenario.runner import RawLog, RunConfig, estimate, iter_executions
from scripts.scenario.server import pick_port


# --- V11 재개 -----------------------------------------------------------

def test_V11_이미_적재된_턴은_건너뛴다(tmp_path: Path) -> None:
    path = tmp_path / "raw.jsonl"
    log = RawLog(path)
    log.append({"profile": "baseline", "scenario_id": "T-01", "turn": 1, "repeat": 0})
    log.append({"profile": "baseline", "scenario_id": "T-01", "turn": 2, "repeat": 0})

    resumed = RawLog(path)
    assert resumed.already("baseline", "T-01", 1, 0)
    assert resumed.already("baseline", "T-01", 2, 0)
    assert not resumed.already("baseline", "T-01", 3, 0)
    assert not resumed.already("baseline", "T-01", 1, 1)  # 다른 반복은 별개다


def test_V11_재개해도_중복이_생기지_않는다(tmp_path: Path) -> None:
    path = tmp_path / "raw.jsonl"
    first = RawLog(path)
    first.append({"profile": "p", "scenario_id": "T-01", "turn": 1, "repeat": 0})
    second = RawLog(path)
    if not second.already("p", "T-01", 1, 0):
        second.append({"profile": "p", "scenario_id": "T-01", "turn": 1, "repeat": 0})
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1


def test_적재_파일은_CRLF가_되지_않는다(tmp_path: Path) -> None:
    """Windows 텍스트 모드 변환이 재개 대조를 어긋나게 한다(부록 A.1-4 · W4)."""
    path = tmp_path / "raw.jsonl"
    RawLog(path).append({"profile": "p", "scenario_id": "T-01", "turn": 1, "repeat": 0})
    assert b"\r\n" not in path.read_bytes()


# --- 실행 묶기 ----------------------------------------------------------

def _catalog() -> Catalog:
    def scenario(sid: str, profile: str = "baseline", cache: str = "warm", **kw) -> Scenario:
        return Scenario(id=sid, group="T", plans=[94], title=sid, profile=profile,
                        cache_state=cache, env="both",
                        turns=[Turn({"query": "q"}, {})], **kw)

    return Catalog(
        groups={"T": Group(id="T", name="t", latency_target_ms=10000)},
        scenarios=[scenario("T-01"), scenario("T-02", cache="cold"),
                   scenario("T-03", profile="optin_query")],
        profiles={"baseline": {}, "optin_query": {"TEXT2SQL_MULTI_CANDIDATE": "true"}},
    )


def test_프로파일별로_묶고_cold를_앞에_둔다() -> None:
    """프로파일 1개 = 서버 기동 1회. cold 캐시는 군 맨 앞이다(§2-3 · §3.5)."""
    grouped = dict(
        (profile, [s.id for s in scenarios])
        for profile, scenarios in iter_executions(_catalog(), RunConfig(env="sandbox"))
    )
    assert grouped == {"baseline": ["T-02", "T-01"], "optin_query": ["T-03"]}


def test_예상치는_가정치임을_밝힌다() -> None:
    result = estimate(_catalog(), RunConfig(env="sandbox"))
    assert result["turns"] == 3
    assert "가정치" in result["note"]
    assert result["estimated_llm_calls"] == 3 * result["assumed_llm_calls_per_turn"]


def test_R군은_반복_3회로_산정된다() -> None:
    catalog = _catalog()
    catalog.scenarios.append(
        Scenario(id="R4-01", group="T", plans=[94], title="착각", kind="misconception",
                 pair_with="R4-01C", env="both", turns=[Turn({"query": "q"}, {})])
    )
    result = estimate(catalog, RunConfig(env="sandbox"))
    assert result["r_group_turns"] == 3


def test_기동이_실패해도_프로파일이_리포트에서_사라지지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """사라진 프로파일은 '돌지 않았다'가 아니라 '없었다'로 읽힌다."""
    from scripts.scenario.runner import execute

    class Exploding:
        def __init__(self, **kwargs) -> None:
            self.port = kwargs["port"]

        def start(self) -> None:
            raise OSError("기동 실패 흉내")

        def stop(self, grace_sec: float = 5.0) -> None:
            pass

        def port_released(self, timeout_sec: float = 10.0) -> bool:
            return True

    monkeypatch.setattr(runner_mod, "RESULTS_ROOT", tmp_path)
    monkeypatch.setattr(runner_mod, "ServerHandle", Exploding)
    summary = execute(_catalog(), RunConfig(mode="mock", env="sandbox", only=["T-01"]))

    assert summary["profiles"], "기동 실패 프로파일이 리포트에서 사라졌다"
    assert any("기동/실행 예외" in r for r in summary["profiles"][0]["reasons"])
    assert summary["skipped"] and "기동 예외" in summary["skipped"][0]["reason"]
    assert summary["executed_turns"] == 0


# --- D-127 과금 게이트 ---------------------------------------------------

def test_D127_옵트인_없이는_실행이_즉시_거부된다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUN_E2E", raising=False)
    with pytest.raises(SystemExit) as exc:
        main(["--run"])
    assert exc.value.code == 2


def test_D127_키_존재만으로는_실행되지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """키는 .encenv 에 상존한다는 전제다 - 키 게이팅은 금지다."""
    monkeypatch.delenv("RUN_E2E", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "sk-fake")
    with pytest.raises(SystemExit) as exc:
        main(["--run"])
    assert exc.value.code == 2


def test_무과금_경로는_옵트인_없이_돈다(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.delenv("RUN_E2E", raising=False)
    assert main(["--dry-run"]) == 0
    assert "카탈로그 OK" in capsys.readouterr().out


def test_예상치_출력은_무과금이다(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.delenv("RUN_E2E", raising=False)
    assert main(["--estimate", "--env", "closed"]) == 0
    out = capsys.readouterr().out
    assert "예상 LLM 호출" in out and "가정" in out


def test_콘솔_출력에_비ASCII_구두점이_없다(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """cp949 콘솔에서 em-dash 가 UnicodeEncodeError 로 런을 죽인다(docs/18:82 · W5)."""
    monkeypatch.delenv("RUN_E2E", raising=False)
    main(["--dry-run"])
    out = capsys.readouterr().out
    for forbidden in ("—", "–", "·", "‘", "’", "“", "”"):
        assert forbidden not in out, f"금지 구두점 {forbidden!r} 이 콘솔 출력에 있다"


# --- 플랫폼 (부록 A.5) --------------------------------------------------

def test_W3_제외_대역_밖에서_포트를_고른다() -> None:
    port = pick_port(preferred=50000, excluded=[(49000, 51000)])
    assert not 49000 <= port <= 51000


def test_지정_포트가_제외_대역_밖이면_그대로_쓴다() -> None:
    assert pick_port(preferred=8123, excluded=[(49000, 51000)]) == 8123
