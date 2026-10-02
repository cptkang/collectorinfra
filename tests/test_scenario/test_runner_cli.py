"""러너·진입점 수용 기준 V11 + D-127 과금 게이트 (plans/94 §4.4 · §10)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.scenario import __main__ as cli
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


def test_프롬프트_미작성_시나리오는_사유와_함께_건너뛴다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """산문을 LLM 에 보내면 무의미한 결과에 돈만 나간다."""
    from scripts.scenario.runner import execute

    catalog = _catalog()
    catalog.scenarios.append(
        Scenario(id="T-99", group="T", plans=[94], title="산문 초안", env="both",
                 prompt_authored=False, turns=[Turn({"query": "B-01~B-06 각 5회 반복"}, {})])
    )
    monkeypatch.setattr(runner_mod, "RESULTS_ROOT", tmp_path)
    summary = execute(catalog, RunConfig(mode="mock", env="sandbox", only=["T-99"]))
    assert summary["executed_turns"] == 0
    # 사유 문구는 원인을 단정하지 않는다(산문일 수도, 러너가 표현 못 할 수도) - 필드명으로 본다.
    assert any("prompt_authored: false" in s["reason"] for s in summary["skipped"])


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

def test_D127_외부_프로바이더는_옵트인_없이_실행이_즉시_거부된다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "llm_providers", lambda: ("gemini", "vllm"))
    monkeypatch.delenv("RUN_E2E", raising=False)
    with pytest.raises(SystemExit) as exc:
        main(["--run"])
    assert exc.value.code == 2


def test_D127_키_존재만으로는_실행되지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """키는 .encenv 에 상존한다는 전제다 - 키 게이팅은 금지다."""
    monkeypatch.setattr(cli, "llm_providers", lambda: ("gemini", "vllm"))
    monkeypatch.delenv("RUN_E2E", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "sk-fake")
    with pytest.raises(SystemExit) as exc:
        main(["--run"])
    assert exc.value.code == 2


def test_D222_로컬_워커와_외부_오케스트레이터는_옵트인_없이_거부된다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """워커만 mlx 로 바꾸고 오케스트레이터가 gemini 로 남으면 과금 경로다(plans/100 §3.4)."""
    monkeypatch.setattr(cli, "llm_providers", lambda: ("mlx", "gemini"))
    monkeypatch.delenv("RUN_E2E", raising=False)
    with pytest.raises(SystemExit) as exc:
        main(["--run"])
    assert exc.value.code == 2


def test_D216_내부망_프로바이더는_옵트인과_승인_없이_실행된다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "llm_providers", lambda: ("fabrix", "vllm"))
    monkeypatch.delenv("RUN_E2E", raising=False)
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("내부망에서 승인을 묻지 않는다"))
    executed: list[RunConfig] = []

    def fake_execute(_catalog, config):
        executed.append(config)
        return {"out_dir": str(tmp_path), "executed_turns": 0, "skipped": []}

    monkeypatch.setattr(cli, "execute", fake_execute)
    monkeypatch.setattr(cli, "write_report", lambda *_: {"report": tmp_path / "report.md"})
    monkeypatch.setattr(cli, "analyze", lambda *_: [])

    assert main(["--run"]) == 0
    assert executed and executed[0].env is None, "옵션 없이 돌리면 환경을 좁히지 않는다"


def test_D216_설정을_못_읽으면_외부로_보고_과금_게이트가_산다(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.config

    def boom():
        raise RuntimeError("설정 없음")

    monkeypatch.setattr(src.config, "load_config", boom)
    monkeypatch.delenv("RUN_E2E", raising=False)
    assert all(p.startswith("unknown") for p in cli.llm_providers())
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
    # 제외 대역은 OS 임시 포트 범위 **밖**으로 둔다. 종전 (49000, 51000)은 macOS 임시
    # 포트 범위(49152-65535)와 겹쳐, OS 할당 커서가 그 구간에 있으면 pick_port의 시도가
    # 전부 막혀 RuntimeError가 났다(2026-09-16 실측 3/3 실패 · plans/94 §18).
    # 검사 대상은 "제외 대역을 피하는가"이지 "어느 대역이냐"가 아니므로 대역만 옮긴다.
    port = pick_port(preferred=2000, excluded=[(1024, 2048)])
    assert not 1024 <= port <= 2048


def test_지정_포트가_제외_대역_밖이면_그대로_쓴다() -> None:
    assert pick_port(preferred=8123, excluded=[(49000, 51000)]) == 8123


def test_MLX_서버가_생성하지_못하면_run_은_실행_전에_멈춘다(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """--run 은 사전 점검을 부르지 않았다 - 죽은 서버로 프로파일을 띄우면 1단이 조용히 강등된다."""
    from scripts.scenario.preflight import VERDICT_STOP, Check

    monkeypatch.setattr(cli, "llm_providers", lambda: ("mlx", "mlx"))
    monkeypatch.setattr(cli, "mlx_run_blockers", lambda: [
        Check("MLX 생성(워커)", "응답 없음", VERDICT_STOP, "서버를 내리고 다시 띄운다", detail="ReadTimeout")])
    monkeypatch.setattr(cli, "execute", lambda *_: pytest.fail("MLX 가 준비되지 않았는데 실행했다"))

    assert main(["--run"]) == 1
    err = capsys.readouterr().err
    assert "MLX 생성(워커)" in err and "ReadTimeout" in err


def test_MLX_서버가_준비되면_run_은_그대로_실행한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "llm_providers", lambda: ("mlx", "mlx"))
    monkeypatch.setattr(cli, "mlx_run_blockers", lambda: [])
    executed: list[RunConfig] = []

    def fake_execute(_catalog, config):
        executed.append(config)
        return {"out_dir": str(tmp_path), "executed_turns": 0, "skipped": []}

    monkeypatch.setattr(cli, "execute", fake_execute)
    monkeypatch.setattr(cli, "write_report", lambda *_: {"report": tmp_path / "report.md"})
    monkeypatch.setattr(cli, "analyze", lambda *_: [])
    assert main(["--run"]) == 0 and executed


def test_mlx_가_아니면_MLX_점검을_하지_않는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "llm_providers", lambda: ("fabrix", "vllm"))
    monkeypatch.setattr(cli, "mlx_run_blockers", lambda: pytest.fail("mlx 가 아닌데 점검했다"))
    monkeypatch.setattr(cli, "execute", lambda *_: {"out_dir": str(tmp_path), "executed_turns": 0,
                                                    "skipped": []})
    monkeypatch.setattr(cli, "write_report", lambda *_: {"report": tmp_path / "report.md"})
    monkeypatch.setattr(cli, "analyze", lambda *_: [])
    assert main(["--run"]) == 0
