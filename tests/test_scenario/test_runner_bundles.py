"""러너 동작 확장 (D-217) - 부하 묶음 · 실행 SQL 수집 · 선행 상태 · 러너 동작 · 실행 순서.

서버도 Redis 도 부르지 않는다. Redis 를 쓰는 함수는 monkeypatch 로 바꾼다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

from scripts.scenario import runner as runner_mod
from scripts.scenario.assertions import Observation
from scripts.scenario.catalog import Catalog, CatalogError, Group, Scenario, Turn, load_catalog
from scripts.scenario.runner import (
    RawLog,
    RunConfig,
    SqlAuditTail,
    iter_executions,
    judge_seed_reload,
    planned_turns,
)

from .conftest import write

META = {"run_id": "r", "env": "closed", "mode": "run"}
STEP = {"kind": "synonym_add", "db_id": "polestar_cm_gp",
        "column": "polestar.cmm_resource.hostname", "words": ["검증용사용률"]}


class FakeClient:
    def __init__(self, respond: Optional[Callable[[str, dict], Observation]] = None) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self._respond = respond or (
            lambda _endpoint, _payload: Observation(http_status=200, status="completed", row_count=1,
                                                    wall_ms=5.0, processing_time_ms=5.0)
        )

    def send(self, endpoint: str, payload: dict[str, Any], upload: Optional[Path] = None) -> Observation:
        self.sent.append((endpoint, dict(payload)))
        return self._respond(endpoint, payload)

    def download(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


def _scenario(sid: str, query: str = "q", **kw: Any) -> Scenario:
    expect = kw.pop("expect", {"status": "completed"})
    return Scenario(id=sid, group=kw.pop("group", "T"), plans=[94], title=sid,
                    env=kw.pop("env", "both"), turns=[Turn({"query": query}, expect)], **kw)


def _catalog(*scenarios: Scenario) -> Catalog:
    groups = {g: Group(g, g, 60000) for g in {"T", "K", "A"}}
    return Catalog(groups=groups, scenarios=list(scenarios), profiles={"baseline": {}})


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _audit(thread: str, sql: str, attempt: int = 0, source: str = "polestar_cm_gp") -> str:
    return json.dumps({"timestamp": "t", "sql": sql, "row_count": 3, "success": True,
                       "thread_id": thread, "retry_attempt": attempt, "source_name": source,
                       "event": "query_executed"}, ensure_ascii=False)


# --- 실행 SQL 수집 --------------------------------------------------------

def test_감사_로그에서_턴_시작_이후_그_스레드의_SQL만_모은다(tmp_path: Path) -> None:
    log = tmp_path / "server.log"
    log.write_text(_audit("scn-A", "SELECT 이전_턴") + "\n", encoding="utf-8")
    tail = SqlAuditTail(log, settle_sec=0.0)
    since = tail.mark()
    with open(log, "a", encoding="utf-8") as handle:
        handle.write("2026-09-15 [INFO] 쿼리 완료: 3건\n")
        handle.write(_audit("scn-A", "SELECT 1") + "\n")
        handle.write(_audit("scn-B", "SELECT 다른_세션") + "\n")
        handle.write('{"event": "query_executed", "thread_id": "scn-A", 깨진 줄\n')
        handle.write(_audit("scn-A", "SELECT 2", attempt=2, source="polestar_b0") + "\n")

    entries = tail.collect(since, "scn-A")
    assert [entry["sql"] for entry in entries] == ["SELECT 1", "SELECT 2"]
    assert entries[1]["source"] == "polestar_b0" and entries[1]["retry_attempt"] == 2


def test_수집한_SQL을_원시_로그에_남기고_SQL별로_판정한다(tmp_path: Path) -> None:
    log = tmp_path / "server.log"
    log.write_text("", encoding="utf-8")
    scenario = _scenario("T-01", expect={
        "status": "completed",
        "sql_must_match": ["(?i)limit 10"],
        "sql_must_not_match": ["(?i)\\bdelete\\b"],
    })

    def respond(_endpoint: str, payload: dict) -> Observation:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(_audit(payload["thread_id"], "SELECT a FROM t LIMIT 10") + "\n")
            handle.write(_audit(payload["thread_id"], "DELETE FROM t", attempt=3) + "\n")
        return Observation(http_status=200, status="completed", row_count=1)

    runner_mod._run_once(_catalog(scenario), RunConfig(mode="run"), META, "baseline", scenario, 0,
                         FakeClient(respond), RawLog(tmp_path / "raw.jsonl"), tmp_path, [],
                         preference=[], sql_tail=SqlAuditTail(log, settle_sec=0.0))

    (row,) = _rows(tmp_path / "raw.jsonl")
    assert [entry["sql"] for entry in row["executed_sqls"]] == ["SELECT a FROM t LIMIT 10", "DELETE FROM t"]
    assert row["retries"] == 3, "감사 로그 retry_attempt 가 재시도 회차를 보강한다"
    assert [f["key"] for f in row["failed_assertions"]] == ["sql_must_not_match"]
    assert row["failed_assertions"][0]["actual"] == "DELETE FROM t"


# --- 부하 묶음 ------------------------------------------------------------

def test_반복_묶음은_참조를_회차마다_새_스레드로_돌고_묶음_ID로_적재한다(tmp_path: Path) -> None:
    b1, b2 = _scenario("B-01", "질의1"), _scenario("B-02", "질의2")
    bundle = _scenario("K-01", "메모", group="K", perf={"target_ms": 10000},
                       replay={"scenarios": ["B-01", "B-02"], "repeat": 2},
                       expect={"manual_review": "p95 10s 이내"})
    client = FakeClient()
    raw = RawLog(tmp_path / "raw.jsonl")

    executed = runner_mod._run_replay(_catalog(b1, b2, bundle), RunConfig(mode="run"), META,
                                      "baseline", bundle, client, raw, tmp_path, [],
                                      preference=[], sql_tail=None)

    rows = _rows(tmp_path / "raw.jsonl")
    assert executed == 4 and len(rows) == 4
    assert {row["scenario_id"] for row in rows} == {"K-01"}
    assert [(r["repeat"], r["turn"], r["replay_of"]) for r in rows] == [
        (0, 1, "B-01"), (0, 101, "B-02"), (1, 1, "B-01"), (1, 101, "B-02")]
    assert [payload["query"] for _, payload in client.sent] == ["질의1", "질의2", "질의1", "질의2"]
    assert len({payload["thread_id"] for _, payload in client.sent}) == 4
    assert rows[0]["group"] == "K" and rows[0]["bundle_note"] == "p95 10s 이내"
    assert rows[0]["perf_verdict"] == "pass"


def test_동시_묶음은_세션마다_별도_클라이언트로_돌고_행이_겹치지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    b1, c1 = _scenario("B-01", "b"), _scenario("C-01", "c")
    bundle = _scenario("K-06", "메모", group="K",
                       concurrent={"scenarios": ["B-01", "C-01"], "sessions": [3, 4]})
    created: list[FakeClient] = []

    class Worker(FakeClient):
        def __init__(self, _config: Any) -> None:
            super().__init__()
            created.append(self)

    monkeypatch.setattr(runner_mod, "ScenarioClient", Worker)
    executed = runner_mod._run_concurrent(_catalog(b1, c1, bundle), RunConfig(mode="run"), META,
                                          "baseline", bundle, object(), RawLog(tmp_path / "raw.jsonl"),
                                          tmp_path, [], preference=[], sql_tail=None)

    rows = _rows(tmp_path / "raw.jsonl")
    assert executed == 7 and len(rows) == 7
    assert len(created) == 7, "세션마다 클라이언트를 따로 만든다"
    assert len({(r["repeat"], r["turn"]) for r in rows}) == 7, "재개 키가 겹치면 행이 사라진다"
    assert sorted(r["concurrent_of"] for r in rows if r["repeat"] == 0) == ["B-01", "B-01", "C-01"]
    assert {r["sessions"] for r in rows if r["repeat"] == 1} == {4}


# --- 선행 상태 (K-10) -----------------------------------------------------

def _record_setup(monkeypatch: pytest.MonkeyPatch, calls: list) -> None:
    def fake(steps: list[dict], *, remove: bool) -> list[str]:
        calls.append(("remove" if remove else "add", steps[0]["words"]))
        return ["ok"]

    monkeypatch.setattr(runner_mod, "apply_synonym_setup", fake)


def test_선행_상태는_턴_전에_만들고_끝나면_그_단어만_되돌린다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list = []
    _record_setup(monkeypatch, calls)
    scenario = _scenario("K-10", env="closed", setup=[STEP])
    client = FakeClient(lambda _e, p: (calls.append(("send", p["query"])),
                                       Observation(http_status=200, status="completed"))[1])

    runner_mod._run_once(_catalog(scenario), RunConfig(mode="run"), META, "baseline", scenario, 0,
                         client, RawLog(tmp_path / "raw.jsonl"), tmp_path, [], preference=[])

    assert calls == [("add", ["검증용사용률"]), ("send", "q"), ("remove", ["검증용사용률"])]
    assert _rows(tmp_path / "raw.jsonl")[0]["setup"] == ["ok"]


def test_턴이_예외로_끝나도_선행_상태를_되돌린다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    _record_setup(monkeypatch, calls)

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("판정기 예외")

    monkeypatch.setattr(runner_mod, "evaluate_turn", boom)
    scenario = _scenario("K-10", env="closed", setup=[STEP])
    with pytest.raises(RuntimeError):
        runner_mod._run_once(_catalog(scenario), RunConfig(mode="run"), META, "baseline", scenario, 0,
                             FakeClient(), RawLog(tmp_path / "raw.jsonl"), tmp_path, [], preference=[])
    assert calls[-1][0] == "remove"


def test_선행_상태를_만들지_못하면_실행하지_않고_사유를_남긴다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(_steps: list, *, remove: bool) -> list[str]:
        raise RuntimeError("Redis 에 연결하지 못했다")

    monkeypatch.setattr(runner_mod, "apply_synonym_setup", fail)
    scenario = _scenario("K-10", env="closed", setup=[STEP])
    client, skipped = FakeClient(), []
    assert runner_mod._run_once(_catalog(scenario), RunConfig(mode="run"), META, "baseline", scenario, 0,
                                client, RawLog(tmp_path / "raw.jsonl"), tmp_path, skipped,
                                preference=[]) == 0
    assert client.sent == []
    assert "setup 실패" in skipped[0]["reason"]


@pytest.mark.parametrize("mode, run_env, note", [
    ("mock", "closed", "모의 실행 - setup 미수행"),
    ("run", "sandbox", "환경 불일치 - setup 미수행"),
])
def test_모의_실행과_환경_불일치에서는_Redis를_건드리지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, run_env: str, note: str
) -> None:
    monkeypatch.setattr(runner_mod, "apply_synonym_setup",
                        lambda *_a, **_k: pytest.fail("Redis 를 쓰면 안 된다"))
    scenario = _scenario("K-10", env="closed", setup=[STEP])
    runner_mod._run_once(_catalog(scenario), RunConfig(mode=mode), {**META, "env": run_env}, "baseline",
                         scenario, 0, FakeClient(), RawLog(tmp_path / "raw.jsonl"), tmp_path, [],
                         preference=[])
    assert _rows(tmp_path / "raw.jsonl")[0]["setup"] == [note]


def test_실행_시작_때_남은_선행_상태를_먼저_지운다(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list = []
    monkeypatch.setattr(runner_mod, "apply_synonym_setup",
                        lambda steps, *, remove: (seen.append((remove, len(steps))), ["삭제 완료"])[1])
    closed = _scenario("K-10", env="closed", setup=[STEP])
    other_env = _scenario("S-01", env="sandbox", setup=[STEP])
    plain = _scenario("T-01")

    assert runner_mod._cleanup_leftover_setup([closed, other_env, plain], "closed") == ["삭제 완료"]
    assert seen == [(True, 1)], "실행 환경의 setup 만, 되돌리기(remove)로 부른다"
    assert runner_mod._cleanup_leftover_setup([plain], "closed") == []


# --- 러너 동작 (SYN-F-05) -------------------------------------------------

def test_시드_재적재_판정은_적재_오류_2회차_변화_기존_단어_손실을_잡는다() -> None:
    before = {"global": {"cpu": ["씨피유"]}}
    first = {"global": {"cpu": ["CPU사용률", "씨피유"]}}
    assert judge_seed_reload(before, first, first, []) == []

    failures = judge_seed_reload(before, {"global": {"cpu": ["CPU사용률"]}},
                                 {"global": {"cpu": ["CPU사용률", "추가"]}}, ["gp: boom"])
    assert [f.key for f in failures] == ["seed_reload.load", "seed_reload.idempotent", "seed_reload.lossless"]
    assert failures[2].actual == {"global:cpu": ["씨피유"]}


def test_러너_동작은_행_하나로_적재되고_모의_실행에서는_Redis를_쓰지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shot = {"per_db:polestar_cm_gp": {"c": ["a", "b"]}}
    monkeypatch.setattr(runner_mod, "run_seed_reload_idempotency", lambda: {
        "active": ["polestar_cm_gp"], "seeded": ["polestar_cm_gp"],
        "before": shot, "first": shot, "second": shot, "errors": []})
    scenario = _scenario("SYN-F-05", action={"kind": "seed_reload_idempotency"})

    assert runner_mod._run_action(RunConfig(mode="run"), META, "baseline", scenario,
                                  RawLog(tmp_path / "raw.jsonl"), []) == 1
    (row,) = _rows(tmp_path / "raw.jsonl")
    assert row["func_verdict"] == "pass"
    assert row["seed_reload"]["words"] == {"before": 2, "first": 2, "second": 2}

    monkeypatch.setattr(runner_mod, "run_seed_reload_idempotency",
                        lambda: pytest.fail("모의 실행에서 Redis 를 쓰면 안 된다"))
    skipped: list = []
    assert runner_mod._run_action(RunConfig(mode="mock"), META, "baseline", scenario,
                                  RawLog(tmp_path / "mock.jsonl"), skipped) == 0
    assert "모의 실행" in skipped[0]["reason"]


def test_러너_동작이_실패하면_오류로_적재한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> dict:
        raise RuntimeError("Redis 에 연결하지 못했다")

    monkeypatch.setattr(runner_mod, "run_seed_reload_idempotency", boom)
    scenario = _scenario("SYN-F-05", action={"kind": "seed_reload_idempotency"})
    runner_mod._run_action(RunConfig(mode="run"), META, "baseline", scenario,
                           RawLog(tmp_path / "raw.jsonl"), [])
    (row,) = _rows(tmp_path / "raw.jsonl")
    assert row["func_verdict"] == "error" and "Redis" in row["error"]


def test_시드_파일이_없으면_판정_불가로_남긴다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_mod, "run_seed_reload_idempotency", lambda: {
        "active": ["itam"], "seeded": [], "before": {}, "first": {}, "second": {}, "errors": []})
    scenario = _scenario("SYN-F-05", action={"kind": "seed_reload_idempotency"})
    runner_mod._run_action(RunConfig(mode="run"), META, "baseline", scenario,
                           RawLog(tmp_path / "raw.jsonl"), [])
    assert _rows(tmp_path / "raw.jsonl")[0]["func_verdict"] == "manual"


# --- 실행 순서 · 예상치 ---------------------------------------------------

def test_cold_부하_묶음이_프로파일_맨_앞이고_나머지_cold가_뒤를_잇는다() -> None:
    """K-02 는 서버 기동 직후 첫 요청이어야 cold 근사가 성립한다(D-217)."""
    catalog = _catalog(
        _scenario("A-01", group="A"),
        _scenario("A-05", group="A", cache_state="cold"),
        _scenario("K-01", group="K", replay={"scenarios": ["A-01"], "repeat": 1}),
        _scenario("K-02", group="K", cache_state="cold", replay={"scenarios": ["A-01"], "repeat": 2}),
    )
    order = [s.id for _profile, scenarios in iter_executions(catalog, RunConfig()) for s in scenarios]
    assert order == ["K-02", "A-05", "A-01", "K-01"]


def test_예상치는_묶음과_러너_동작을_펼쳐_센다() -> None:
    b1 = _scenario("B-01")
    c1 = Scenario(id="C-01", group="T", plans=[94], title="c", env="both",
                  turns=[Turn({"query": "1"}, {}), Turn({"query": "2"}, {})])
    replay = _scenario("K-01", group="K", replay={"scenarios": ["B-01", "C-01"], "repeat": 3})
    concurrent = _scenario("K-06", group="K", concurrent={"scenarios": ["B-01", "C-01"], "sessions": [2, 3]})
    action = _scenario("SYN-F-05", action={"kind": "seed_reload_idempotency"})
    catalog = _catalog(b1, c1, replay, concurrent, action)

    assert planned_turns(catalog, replay, RunConfig()) == (1 + 2) * 3
    assert planned_turns(catalog, concurrent, RunConfig()) == (1 + 2) + (1 + 2 + 1)
    assert planned_turns(catalog, action, RunConfig()) == 1


# --- 카탈로그 -------------------------------------------------------------

def test_러너_동작_선언이_틀리면_로더가_거부한다(scenario_dir: Path, profiles_path: Path) -> None:
    write(scenario_dir / "t.yaml", """version: 1
group: {id: T, name: "테스트군", latency_target_ms: 10000}
scenarios:
  - id: T-01
    plans: [94]
    title: "없는 참조"
    replay: {scenarios: [없음], repeat: 0}
    turns: [{send: {query: "a"}, expect: {}}]
  - id: T-02
    plans: [94]
    title: "동작을 참조"
    concurrent: {scenarios: [T-03], sessions: [0]}
    turns: [{send: {query: "b"}, expect: {}}]
  - id: T-03
    plans: [94]
    title: "모르는 동작"
    action: {kind: 모름}
    setup: [{kind: synonym_add, db_id: x}]
    turns: [{send: {query: "c"}, expect: {}}]
  - id: T-04
    plans: [94]
    title: "둘 다"
    replay: {scenarios: [T-03]}
    action: {kind: seed_reload_idempotency}
    turns: [{send: {query: "d"}, expect: {}}]
""")
    with pytest.raises(CatalogError) as exc:
        load_catalog(scenario_dir, profiles_path)
    text = "\n".join(exc.value.errors)
    for needle in ("replay.repeat", "replay 참조 '없음'", "concurrent.sessions",
                   "질의 시나리오가 아니다", "action.kind", "db_id·column·words", "하나만"):
        assert needle in text, needle


def test_저장소_카탈로그의_K군_SYN_F_05_I_06이_실행_가능한_모양이다() -> None:
    catalog = load_catalog()
    assert catalog.by_id("K-01").replay == {"scenarios": ["B-01", "B-02", "B-03", "B-04", "B-05", "B-06"],
                                            "repeat": 5}
    k02 = catalog.by_id("K-02")
    assert k02.cache_state == "cold" and k02.replay == {"scenarios": ["B-01"], "repeat": 2}
    assert catalog.by_id("K-06").concurrent == {"scenarios": ["B-01", "C-01"], "sessions": [5, 10]}
    assert catalog.by_id("K-07").concurrent == {"scenarios": ["F-07", "B-10"], "sessions": [2]}
    assert catalog.by_id("K-10").setup[0]["words"] == ["검증용사용률"]
    assert catalog.by_id("SYN-F-05").action == {"kind": "seed_reload_idempotency"}
    i06 = catalog.by_id("I-06")
    assert i06.turns[2].send == {"query": "이 양식에 저장된 내용은?"}
    assert "signature" not in i06.turns[3].send["form_memory_delete"], "서명은 러너가 패널에서 채운다"


# --- 유사어 쓰기 시나리오 되돌리기 (A-10 unregister_synonym) ----------------

def test_더해진_단어만_되돌릴_대상으로_고른다() -> None:
    before = {"global": {"cpu": ["core"]}, "per_db:polestar_cm_gp": {"t.cpu": ["코어"]}}
    after = {"global": {"cpu": ["core", "vcore"], "memory": ["mem"]},
             "per_db:polestar_cm_gp": {"t.cpu": ["코어"]}}
    assert runner_mod.synonym_additions(before, after) == {"global": {"cpu": ["vcore"], "memory": ["mem"]}}


def test_unregister_synonym_은_러너가_지원하는_teardown이다() -> None:
    scenario = _scenario("A-10", teardown=["drop_thread", "unregister_synonym", "모르는_정리"])
    assert runner_mod._teardown(scenario) == ["모르는_정리"]


def _record_restore(monkeypatch: pytest.MonkeyPatch, calls: list) -> None:
    monkeypatch.setattr(runner_mod, "snapshot_synonyms",
                        lambda: (calls.append("snapshot"), {"global": {}})[1])
    monkeypatch.setattr(runner_mod, "remove_synonym_additions",
                        lambda before: (calls.append(("restore", before)), ["삭제 global cpu ['vcore']: 완료"])[1])


def test_유사어_쓰기_시나리오는_기준선을_뜨고_끝나면_더한_단어만_지운다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list = []
    _record_restore(monkeypatch, calls)
    scenario = _scenario("A-10", "vcore, cpu, core은 동의어이다. 캐시에 등록하라.",
                         teardown=["drop_thread", "unregister_synonym"])
    client = FakeClient(lambda _e, p: (calls.append("send"),
                                       Observation(http_status=200, status="completed"))[1])
    meta = dict(META)
    runner_mod._run_once(_catalog(scenario), RunConfig(mode="run"), meta, "baseline", scenario, 0,
                         client, RawLog(tmp_path / "raw.jsonl"), tmp_path, [], preference=[])

    assert calls == ["snapshot", "send", ("restore", {"global": {}})]
    assert meta["teardown_log"] == [{"scenario_id": "A-10", "repeat": 0,
                                     "unregister_synonym": ["삭제 global cpu ['vcore']: 완료"]}]
    assert "teardown_unsupported" not in _rows(tmp_path / "raw.jsonl")[0]


def test_턴이_예외로_끝나도_유사어를_되돌린다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    _record_restore(monkeypatch, calls)
    monkeypatch.setattr(runner_mod, "evaluate_turn",
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("판정기 예외")))
    scenario = _scenario("A-10", teardown=["drop_thread", "unregister_synonym"])
    with pytest.raises(RuntimeError):
        runner_mod._run_once(_catalog(scenario), RunConfig(mode="run"), dict(META), "baseline",
                             scenario, 0, FakeClient(), RawLog(tmp_path / "raw.jsonl"), tmp_path, [],
                             preference=[])
    assert calls[-1][0] == "restore"


def test_기준선을_못_뜨면_쓰기_시나리오를_실행하지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail() -> dict:
        raise RuntimeError("Redis 에 연결하지 못했다")

    monkeypatch.setattr(runner_mod, "snapshot_synonyms", fail)
    scenario = _scenario("A-10", teardown=["drop_thread", "unregister_synonym"])
    client, skipped = FakeClient(), []
    assert runner_mod._run_once(_catalog(scenario), RunConfig(mode="run"), dict(META), "baseline",
                                scenario, 0, client, RawLog(tmp_path / "raw.jsonl"), tmp_path,
                                skipped, preference=[]) == 0
    assert client.sent == [] and "되돌릴 수 없다" in skipped[0]["reason"]


def test_모의_실행에서는_유사어_사전을_건드리지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner_mod, "snapshot_synonyms", lambda: pytest.fail("Redis 를 쓰면 안 된다"))
    monkeypatch.setattr(runner_mod, "remove_synonym_additions",
                        lambda _b: pytest.fail("Redis 를 쓰면 안 된다"))
    scenario = _scenario("A-10", teardown=["drop_thread", "unregister_synonym"])
    runner_mod._run_once(_catalog(scenario), RunConfig(mode="mock"), dict(META), "baseline", scenario, 0,
                         FakeClient(), RawLog(tmp_path / "raw.jsonl"), tmp_path, [], preference=[])
    assert len(_rows(tmp_path / "raw.jsonl")) == 1


def test_저장소_카탈로그의_A_05_A_10은_정리_가능한_teardown만_남는다() -> None:
    catalog = load_catalog()
    assert catalog.by_id("A-05").teardown == ["drop_thread"]
    assert runner_mod._teardown(catalog.by_id("A-10")) == []
    assert not [s.id for s in catalog.scenarios if runner_mod._teardown(s)], "정리하지 못하는 시나리오가 남았다"
