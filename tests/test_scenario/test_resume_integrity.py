"""재개(`--resume` · 벤치 `--segment` 이어쓰기)의 무결성.

`plans/110` `109·CS-16`·`109·CS-17`·`109·CS-19③`.

사용자 결정(2026-09-22): *"멀티턴은 다시 돌려라"* — 일부 턴만 끝난 멀티턴 시나리오는 재개 때
**1턴부터 새 thread 로** 다시 돈다(표지만 붙이는 안은 기각). 서버도 LLM 도 부르지 않는다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import pytest

from scripts.scenario import report as report_mod
from scripts.scenario import runner as runner_mod
from scripts.scenario import server as server_mod
from scripts.scenario.assertions import Observation
from scripts.scenario.catalog import Catalog, Group, Scenario, Turn
from scripts.scenario.runner import RawLog, RunConfig

META = {"run_id": "r", "env": "closed", "mode": "run"}


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send(
        self, endpoint: str, payload: dict[str, Any], upload: Optional[Path] = None
    ) -> Observation:
        self.sent.append(dict(payload))
        return Observation(http_status=200, status="completed", row_count=1,
                           wall_ms=5.0, processing_time_ms=5.0)

    def download(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _multiturn(sid: str = "M-01") -> Scenario:
    # 2턴은 질의 없이 존만 고른다 - 러너가 **직전 턴의 질의**로 채워야 서버가 받는다
    # (clarify.complete_payload).
    return Scenario(id=sid, group="T", plans=[94], title=sid, env="both", turns=[
        Turn({"query": "김포 서버 CPU"}, {"status": "completed"}),
        Turn({"selected_db_ids": ["polestar_cm_gp"]}, {"status": "completed"}),
    ])


def _catalog(*scenarios: Scenario) -> Catalog:
    return Catalog(groups={"T": Group("T", "T", 60000)}, scenarios=list(scenarios),
                   profiles={"baseline": {}})


def _seed(path: Path, *rows: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps({"profile": "baseline", "repeat": 0, **row},
                                    ensure_ascii=False) + "\n")


def _lines(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _run(tmp_path: Path, meta: dict[str, Any], scenario: Scenario) -> tuple[FakeClient, int]:
    client = FakeClient()
    executed = runner_mod._run_once(
        _catalog(scenario), RunConfig(mode="run"), meta, "baseline", scenario, 0, client,
        RawLog(tmp_path / "raw.jsonl"), tmp_path, [], preference=[], sql_tail=None,
    )
    return client, executed


# --- CS-17 멀티턴 도중 재개 ------------------------------------------------

def test_부분만_끝난_멀티턴은_1턴부터_새_thread로_다시_돈다(tmp_path: Path, capsys) -> None:
    """끝난 1턴을 건너뛰면 2턴이 **새 thread 에서 문맥 없이** 돈다.

    승계를 재는 시나리오가 다른 것을 잰다.
    """
    raw = tmp_path / "raw.jsonl"
    _seed(raw, {"scenario_id": "M-01", "turn": 1, "func_verdict": "pass"})
    meta = dict(META)

    client, executed = _run(tmp_path, meta, _multiturn())

    assert executed == 2 and len(client.sent) == 2, "1턴부터 다시 돈다"
    assert client.sent[0]["thread_id"] == client.sent[1]["thread_id"], "두 턴이 같은 새 thread 다"
    assert client.sent[1]["query"] == "김포 서버 CPU", "2턴이 1턴의 질의를 승계한다"
    assert [(r["turn"], r["func_verdict"]) for r in _lines(raw)[1:]] == [(1, "pass"), (2, "pass")]
    [record] = meta["rerun_partial"]
    assert record["scenario_id"] == "M-01" and record["done_turns"] == [1]
    assert record["turns"] == 2 and record["profile"] == "baseline" and record["repeat"] == 0
    assert "M-01" in capsys.readouterr().out, "다시 돈 사실을 콘솔에도 찍는다"


def test_1턴부터_다시_돈_행은_arm_출처를_그대로_싣는다(tmp_path: Path) -> None:
    """arm 쌍은 시나리오 id 로 맺어진다.

    재개 행에서 arm 이 빠지면 그 시나리오만 쌍체 비교에서 빠진다(36 보존 조건 ⑤).
    """
    profile = "optin_alarm+tier3_router"
    origin = {"profile": profile, "arm": "tier3_router", "base_profile": "optin_alarm"}
    _seed(tmp_path / "raw.jsonl",
          {"scenario_id": "M-01", "turn": 1, "func_verdict": "pass", **origin})
    binding = {"arm": "tier3_router", "base_profile": "optin_alarm"}
    meta = {**META, "arm_bindings": {profile: binding}}

    client = FakeClient()
    runner_mod._run_once(
        _catalog(_multiturn()), RunConfig(mode="run"), meta, profile, _multiturn(), 0, client,
        RawLog(tmp_path / "raw.jsonl"), tmp_path, [], preference=[], sql_tail=None,
    )

    rows = _lines(tmp_path / "raw.jsonl")[1:]
    assert [r["turn"] for r in rows] == [1, 2]
    assert all({k: r[k] for k in origin} == origin for r in rows)
    assert meta["rerun_partial"][0]["profile"] == profile


def test_뒤_턴이_무효였으면_1턴부터_다시_돈다(tmp_path: Path) -> None:
    """무효 턴(X-1)은 미완료다 - 그 턴만 새 thread 로 돌리면 역시 문맥이 없다."""
    _seed(tmp_path / "raw.jsonl",
          {"scenario_id": "M-01", "turn": 1, "func_verdict": "pass"},
          {"scenario_id": "M-01", "turn": 2, "func_verdict": "invalid"})
    meta = dict(META)

    client, _ = _run(tmp_path, meta, _multiturn())

    assert [p.get("query") for p in client.sent] == ["김포 서버 CPU", "김포 서버 CPU"]
    assert meta["rerun_partial"][0]["done_turns"] == [1]


def test_모든_턴이_끝난_시나리오는_건너뛴다(tmp_path: Path) -> None:
    _seed(tmp_path / "raw.jsonl",
          {"scenario_id": "M-01", "turn": 1, "func_verdict": "pass"},
          {"scenario_id": "M-01", "turn": 2, "func_verdict": "fail"})
    meta = dict(META)

    client, executed = _run(tmp_path, meta, _multiturn())

    assert client.sent == [] and executed == 0
    assert "rerun_partial" not in meta


def test_앞_턴이_불합격으로_끊긴_시나리오는_끝난_것이다(tmp_path: Path) -> None:
    """러너는 앞 턴이 fail/error 면 뒤 턴을 **일부러** 건너뛴다.

    재개가 그 뒤 턴을 돌리면 안 된다.
    """
    _seed(tmp_path / "raw.jsonl", {"scenario_id": "M-01", "turn": 1, "func_verdict": "fail"})
    meta = dict(META)

    client, executed = _run(tmp_path, meta, _multiturn())

    assert client.sent == [] and executed == 0
    assert "rerun_partial" not in meta


def test_끝난_것으로_본_시나리오도_남은_턴의_건너뜀_사유를_다시_남긴다(tmp_path: Path) -> None:
    """`run.json` 은 끝에서 이번 시도의 `skipped` 로 새로 쓰인다.

    재개가 건너뜀 사유를 다시 적지 않으면 앞 시도가 남긴 「후속 턴 판정 불가」가 사라진다.
    """
    _seed(tmp_path / "raw.jsonl", {"scenario_id": "M-01", "turn": 1, "func_verdict": "fail"})
    skipped: list[dict[str, Any]] = []
    client = FakeClient()

    runner_mod._run_once(
        _catalog(_multiturn()), RunConfig(mode="run"), dict(META), "baseline", _multiturn(), 0,
        client, RawLog(tmp_path / "raw.jsonl"), tmp_path, skipped, preference=[], sql_tail=None,
    )

    assert client.sent == []
    assert skipped == [{"scenario_id": "M-01", "turn": 2,
                        "reason": "선행 턴 1 이 fail - 후속 턴 판정 불가"}]


def test_새_run_은_종전과_같다(tmp_path: Path) -> None:
    meta = dict(META)

    client, executed = _run(tmp_path, meta, _multiturn())

    assert executed == 2 and len(client.sent) == 2
    assert "rerun_partial" not in meta, "새 run 의 meta 는 비트 동일해야 한다"


# --- 94 리포트 - 같은 턴은 마지막 행만 (재개 중복 적재) ---------------------

def test_리포트는_같은_턴의_마지막_행만_센다(tmp_path: Path) -> None:
    """재개는 같은 키를 한 번 더 적재한다. 리포트가 둘 다 세면 무효·지연이 두 번 들어간다."""
    _seed(tmp_path / "raw.jsonl",
          {"scenario_id": "A-01", "turn": 1, "func_verdict": "invalid", "group": "A"},
          {"scenario_id": "A-02", "turn": 1, "func_verdict": "pass", "group": "A",
           "processing_time_ms": 10.0},
          {"scenario_id": "A-01", "turn": 1, "func_verdict": "pass", "group": "A",
           "processing_time_ms": 20.0})

    rows = report_mod.load_rows(tmp_path)
    summary = report_mod.build_summary(tmp_path)

    assert [(r["scenario_id"], r["func_verdict"]) for r in rows] == [
        ("A-01", "pass"), ("A-02", "pass")], "뒤 행이 결과이고 위치는 처음 자리를 지킨다"
    assert summary["invalid"]["count"] == 0 and summary["invalid"]["total_turns"] == 2
    assert summary["scenario_verdicts"]["A-01"]["verdict"] == "pass"


@pytest.mark.parametrize("row", [
    {"profile": "p", "scenario_id": "S", "turn": 3, "repeat": 1},
    {"profile": "p", "scenario_id": "S", "turn": "3", "repeat": "1"},
    {"scenario_id": "S", "turn": 1},
])
def test_리포트의_턴_키는_러너_재개_키와_같다(row: dict[str, Any]) -> None:
    assert report_mod.turn_key(row) == runner_mod.row_key(row)


# --- CS-16 재개 시 서버 로그 보존 ------------------------------------------

class _FakeProc:
    pid = 12345

    def __init__(self, lines: list[str]) -> None:
        self.stdout = iter(lines)

    def poll(self) -> int:
        return 0


def _start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lines: list[str]
) -> server_mod.ServerHandle:
    monkeypatch.setattr(server_mod.subprocess, "Popen", lambda *a, **k: _FakeProc(lines))
    handle = server_mod.ServerHandle(profile="p", env_overrides={}, port=1,
                                     log_path=tmp_path / "logs" / "server-p.log")
    handle.start()
    assert handle._reader is not None
    handle._reader.join(timeout=5)
    return handle


def test_재개가_서버를_다시_띄워도_이전_서버_로그가_남는다(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "logs" / "server-p.log"
    log.parent.mkdir()
    log.write_text("끊기기 전 ERROR 기록\n", encoding="utf-8")

    _start(tmp_path, monkeypatch, ["재개 후 기동\n"])

    assert log.read_text(encoding="utf-8") == "재개 후 기동\n", "소비자는 최신 시도 로그만 읽는다"
    [kept] = list(log.parent.glob("server-p.log.prev-*"))
    assert kept.read_text(encoding="utf-8") == "끊기기 전 ERROR 기록\n"
    assert sorted(p.name for p in log.parent.glob("server-*.log")) == ["server-p.log"], \
        "보존본은 `server-*.log` glob 에 걸리지 않는다"


def test_새_run_은_서버_로그를_옮기지_않는다(tmp_path: Path, monkeypatch) -> None:
    _start(tmp_path, monkeypatch, ["기동\n"])

    assert sorted(p.name for p in (tmp_path / "logs").iterdir()) == ["server-p.log"]


# --- CS-19③ 시도별 출처 ---------------------------------------------------

@pytest.fixture()
def fake_git(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """git 출력을 바꿔 끼운다 - 커밋·작업 트리가 시도 사이에 바뀌는 상황을 만든다."""
    state = {"commit": "aaa", "status": "", "diff": ""}

    def capture(cmd: list[str], timeout: float = 10.0) -> str:
        if "rev-parse" in cmd:
            return state["commit"]
        if "status" in cmd:
            return state["status"]
        if "diff" in cmd:
            return state["diff"]
        return ""

    monkeypatch.setattr(runner_mod, "run_capture", capture)
    return state


@pytest.fixture()
def results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from src.config import load_config

    monkeypatch.setattr(runner_mod, "RESULTS_ROOT", tmp_path)
    load_config.cache_clear()
    yield tmp_path
    load_config.cache_clear()


@pytest.fixture()
def run_plan(monkeypatch: pytest.MonkeyPatch):
    """`_execute` 를 프로파일 없이 돌린다.

    `interrupt=True` 면 첫 프로파일 전에 끊긴다(kill 모사).
    """

    def execute(run_id: str, *, resume: bool = False, interrupt: bool = False) -> dict[str, Any]:
        def plan(_catalog: Catalog, _config: RunConfig):
            if interrupt:
                raise KeyboardInterrupt
            return iter(())

        monkeypatch.setattr(runner_mod, "iter_executions", plan)
        target = {"resume_from": run_id} if resume else {"run_id": run_id}
        config = RunConfig(mode="mock", **target)
        return runner_mod._execute(_catalog(), config)

    return execute


def _run_json(root: Path, run_id: str) -> dict[str, Any]:
    return json.loads((root / run_id / "run.json").read_text(encoding="utf-8"))


def test_끊긴_run_에도_출처가_남는다(results: Path, fake_git, run_plan) -> None:
    """종전에는 `run.json` 을 끝에서만 써서 끊긴 run 에는 커밋·dirty 가 아예 없었다."""
    with pytest.raises(KeyboardInterrupt):
        run_plan("20260922-000001", interrupt=True)

    meta = _run_json(results, "20260922-000001")["meta"]
    assert meta["in_progress"] is True
    [attempt] = meta["attempts"]
    assert attempt["commit"] == "aaa" and attempt["dirty"] is False and attempt["started_at"]


def test_재개하면_시도별_출처가_누적되고_커밋이_다르면_섞임을_표시한다(
        results: Path, fake_git, run_plan, capsys) -> None:
    with pytest.raises(KeyboardInterrupt):
        run_plan("20260922-000002", interrupt=True)
    fake_git["commit"] = "bbb"

    run_plan("20260922-000002", resume=True)

    meta = _run_json(results, "20260922-000002")["meta"]
    assert [a["commit"] for a in meta["attempts"]] == ["aaa", "bbb"]
    assert "커밋" in meta["provenance_mixed"]
    assert "in_progress" not in meta, "끝난 run 에는 진행 표지가 없다"
    assert "출처" in capsys.readouterr().out


def test_같은_커밋이라도_미커밋_변경이_바뀌면_섞임이다(results: Path, fake_git, run_plan) -> None:
    fake_git.update(status=" M src/graph.py", diff="+a")
    with pytest.raises(KeyboardInterrupt):
        run_plan("20260922-000003", interrupt=True)
    fake_git["diff"] = "+b"

    run_plan("20260922-000003", resume=True)

    meta = _run_json(results, "20260922-000003")["meta"]
    assert "작업 트리" in meta["provenance_mixed"]


def test_같은_판으로_재개하면_섞임이_없다(results: Path, fake_git, run_plan) -> None:
    with pytest.raises(KeyboardInterrupt):
        run_plan("20260922-000004", interrupt=True)

    run_plan("20260922-000004", resume=True)

    meta = _run_json(results, "20260922-000004")["meta"]
    assert len(meta["attempts"]) == 2 and "provenance_mixed" not in meta


def test_출처_기록이_없는_옛_run_을_이으면_확인_불가로_표시한다(
        results: Path, fake_git, run_plan) -> None:
    """이 수정 전에 끊긴 run 은 `run.json` 이 없다 - 같은 판인지 모른다는 사실을 숨기지 않는다."""
    run_dir = results / "20260922-000005"
    run_dir.mkdir()
    _seed(run_dir / "raw.jsonl", {"scenario_id": "A-01", "turn": 1, "func_verdict": "pass"})

    run_plan("20260922-000005", resume=True)

    assert "확인할 수 없다" in _run_json(results, "20260922-000005")["meta"]["provenance_mixed"]


def test_새_run_의_출처는_시도_1건이고_섞임이_없다(results: Path, fake_git, run_plan) -> None:
    summary = run_plan("20260922-000006")

    meta = summary["meta"]
    assert len(meta["attempts"]) == 1
    assert "provenance_mixed" not in meta and "in_progress" not in meta


def test_리포트_1절은_출처_섞임을_드러낸다(tmp_path: Path) -> None:
    summary = {"meta": {"run_id": "r", "provenance_mixed": "커밋이 다르다(aaa → bbb)",
                        "attempts": [{"commit": "aaa"}, {"commit": "bbb"}]}}
    text = report_mod.render_markdown(summary, tmp_path, None)
    section = text.split("## 1. 실행 요약")[1].split("## 2.")[0]
    assert "커밋이 다르다(aaa → bbb)" in section and "2회" in section


def test_리포트_1절은_새_run_에서_종전과_같다(tmp_path: Path) -> None:
    summary = {"meta": {"run_id": "r", "attempts": [{"commit": "aaa"}]}}
    text = report_mod.render_markdown(summary, tmp_path, None)
    section = text.split("## 1. 실행 요약")[1].split("## 2.")[0]
    assert "출처 섞임" not in text and "시도" not in section
