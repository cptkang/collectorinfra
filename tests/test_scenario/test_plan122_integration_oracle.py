"""오라클 배선 통합 (plans/122 O-1·O-2·O-4 · 통합 담당).

고정하는 계약:
  1. 로더 — 턴 `expect.oracle` 을 `oracle.validate_oracle_spec` 으로 검사한다(정본 파일 없음 ·
     엔진 미커버 · 정의 밖 키 · 대상 DB 없음 → `CatalogError`). 대상 DB 는 러너와 같은
     `oracle_targets`(spec `db_ids` → 턴 `expect.db_ids`)로 정한다.
  2. 판정기 — `evaluate_oracle` 결과를 pass(무표시) · fail(`Failure("oracle", 선언, 상세)`) ·
     hold(보류 출처 `oracle_unavailable` · 문구 `oracle …` 로 시작)로 옮긴다. 모의 실행 규칙은
     내용 단언과 같다.
  3. 러너 — 실 모드에서만 · pre(송신 직전) → 송신 → 결과 행 → post 순 · 계측 밖 · 직렬. 환경 보류 ·
     모의 · `source: fixture` 는 DB 를 부르지 않는다. 대상 DB 가 없으면 부르지 않고 보류 사유.
     `oracle_check` 에는 요약만(행 원문 없음 · G-4). `oracle_log.jsonl` 은 run 디렉터리에 남는다.
  4. 오라클 자리표(`anchor_values`)는 해석기 `relative_window` 로 옮긴 뒤에도 종전 값과 같다.
DB 는 가짜 클라이언트다(네트워크 가드) · LLM 0.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.scenario import oracle as oracle_mod
from scripts.scenario import runner as runner_mod
from scripts.scenario.assertions import Observation, evaluate_turn
from scripts.scenario.catalog import (
    Catalog,
    CatalogError,
    Group,
    Scenario,
    Turn,
    load_catalog,
    oracle_targets,
)
from scripts.scenario.runner import RawLog, RunConfig
from src.config import SecurityConfig
from tests.test_scenario.conftest import GOOD_GROUP, write

ALL_DBS = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]
GROUP = Group("T", "T", 60000)
META = {"run_id": "R-1", "env": "closed", "mode": "run"}
ANCHOR = "2026-09-15T10:00:00+09:00"


# --- 1 로더 ----------------------------------------------------------------------------------

def _load_expect(scenario_dir: Path, profiles_path: Path, expect: str) -> Catalog:
    write(scenario_dir / "t.yaml", GOOD_GROUP.replace(
        "        expect: {status: completed}", f"        expect: {expect}"))
    return load_catalog(scenario_dir=scenario_dir, profiles_path=profiles_path)


def _load_errors(scenario_dir: Path, profiles_path: Path, expect: str) -> list[str]:
    with pytest.raises(CatalogError) as exc:
        _load_expect(scenario_dir, profiles_path, expect)
    return exc.value.errors


def test_loader_accepts_canon_and_fixture_oracle(scenario_dir: Path, profiles_path: Path) -> None:
    """정본 오라클 선언은 로드된다."""
    catalog = _load_expect(scenario_dir, profiles_path, (
        "{db_ids: [polestar_b0, polestar_cm_gp, polestar_cm_yd], "
        "oracle: {id: B-01, compare: count, system: row_counts_by_db}}"))
    assert catalog.scenarios[0].turns[0].expect["oracle"]["id"] == "B-01"
    fixture = _load_expect(scenario_dir, profiles_path, (
        "{oracle: {id: M-01, source: fixture, compare: keyset, key: [[hostname]], "
        "field: producer.expected}}"))
    assert fixture.scenarios[0].turns[0].expect["oracle"]["source"] == "fixture"


@pytest.mark.parametrize("expect, needle", [
    ("{db_ids: [polestar_cm_gp], oracle: {id: NOPE-1, compare: count}}", "정본 파일이 없다"),
    ("{db_ids: [polestar_cm_gp], oracle: {id: B-01, compare: count, snapshots: once}}",
     "정의 밖 키 ['snapshots']"),
    ("{oracle: {id: B-01, compare: count}}", "대상 DB 가 없다"),
    ("{db_ids: [itam], oracle: {id: B-01, compare: count}}", "오라클 대상 엔진이 아니다"),
    ("{db_ids: [polestar_cm_gp], oracle: {id: B-01, compare: median}}", "compare 는"),
    ("{oracle: {}}", "oracle 은 비지 않은 매핑"),
])
def test_loader_rejects_bad_oracle_spec(
    scenario_dir: Path, profiles_path: Path, expect: str, needle: str,
) -> None:
    """틀린 오라클 선언은 로드 시점에 거부한다."""
    errors = _load_errors(scenario_dir, profiles_path, expect)
    assert any(needle in e and "T-01 턴1 oracle:" in e for e in errors), errors


def test_loader_engine_coverage_uses_runner_targets(
    scenario_dir: Path, profiles_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """엔진 미커버는 거부하고 spec db ids 가 턴 db ids 보다 먼저다."""
    canon = tmp_path / "oracles"
    write(canon / "X-1.pg.sql", "SELECT COUNT(*) AS n FROM polestar.t LIMIT 1\n")
    monkeypatch.setattr(oracle_mod, "ORACLE_DIR", canon)
    # 턴 db_ids 에 DB2 가 섞이면 db2 정본이 없어 거부(조용한 pass 누수 차단 · §9.2).
    errors = _load_errors(scenario_dir, profiles_path, (
        "{db_ids: [polestar_b0, polestar_cm_gp], oracle: {id: X-1, compare: count}}"))
    assert any("polestar_b0: 정본 파일이 없다" in e for e in errors), errors
    # spec db_ids 가 PG 만 가리키면 러너도 PG 에서만 돈다 - 로더도 그 DB 만 검사한다.
    catalog = _load_expect(scenario_dir, profiles_path, (
        "{db_ids: [polestar_b0, polestar_cm_gp], "
        "oracle: {id: X-1, compare: count, db_ids: [polestar_cm_gp]}}"))
    assert catalog.scenarios[0].turns[0].expect["oracle"]["db_ids"] == ["polestar_cm_gp"]


def test_oracle_targets_order_and_shape() -> None:
    """oracle targets 는 spec 다음 턴 순이고 모양이 틀리면 건너뛴다."""
    expect = {"db_ids": ["polestar_cm_gp", "polestar_cm_gp", "polestar_b0"]}
    assert oracle_targets({"db_ids": ["polestar_cm_yd"]}, expect) == ["polestar_cm_yd"]
    assert oracle_targets({}, expect) == ["polestar_cm_gp", "polestar_b0"]
    assert oracle_targets({"db_ids": "polestar_cm_yd"}, expect) == ["polestar_cm_gp", "polestar_b0"]
    assert oracle_targets({"db_ids": []}, {}) == []
    assert oracle_targets(None, {"db_ids": [""]}) == []


def test_current_catalog_still_loads() -> None:
    """현 카탈로그는 오라클을 쓰지 않아도 로드된다."""
    catalog = load_catalog()
    assert catalog.scenarios, "현 작업 트리 카탈로그가 새 로더 검사를 통과한다"


# --- 2 판정기 매핑 ---------------------------------------------------------------------------

def _scenario(expect: dict[str, Any], **kwargs: Any) -> Scenario:
    base: dict[str, Any] = dict(id="T-01", group="T", plans=[122], title="t",
                                turns=[Turn({"query": "q"}, expect)])
    base.update(kwargs)
    return Scenario(**base)


def _outcome(rows_by_db: dict[str, list[dict[str, Any]]], phase: str = "post",
             status: str = "ok", reason: str | None = None) -> dict[str, Any]:
    return {"status": status, "reason": reason, "rows_by_db": rows_by_db, "elapsed_ms": 3.0,
            "phase": phase, "limit_by_db": {db: 1000 for db in rows_by_db}}


def _csv(values: list[str], column: str = "hostname") -> dict[str, Any]:
    return {"status": "ok", "columns": [column], "rows": [{column: v} for v in values],
            "total_rows": len(values), "truncated": False, "reason": None}


COUNT = {"id": "B-01", "compare": "count", "db_ids": ["polestar_cm_gp"]}


def _judge(spec: dict[str, Any], obs: Observation, *, mock: bool = False,
           scenario_mock: dict[str, Any] | None = None) -> Any:
    scenario = _scenario({"oracle": spec}, mock=scenario_mock)
    return evaluate_turn(scenario, 1, scenario.turns[0], obs, GROUP, mock=mock)


def _obs(**kwargs: Any) -> Observation:
    return Observation(http_status=200, status="completed", response="r", **kwargs)


def test_judge_match_passes_silently() -> None:
    """일치는 합격이고 아무것도 표시하지 않는다."""
    record = {"id": "B-01", "targets": ["polestar_cm_gp"], "pre": None,
              "post": _outcome({"polestar_cm_gp": [{"n": 2}]})}
    verdict = _judge(COUNT, _obs(oracle=record, result=_csv(["a", "b"])))
    assert verdict.func == "pass" and not verdict.failures and not verdict.manual_notes


def test_judge_mismatch_fails_with_spec_as_expected() -> None:
    """불일치는 oracle 불합격이고 기대값은 선언 그대로다."""
    record = {"id": "B-01", "targets": ["polestar_cm_gp"], "pre": None,
              "post": _outcome({"polestar_cm_gp": [{"n": 3}]})}
    verdict = _judge(COUNT, _obs(oracle=record, result=_csv(["a", "b"])))
    assert verdict.func == "fail"
    (failure,) = verdict.failures
    assert failure.key == "oracle" and failure.expected == COUNT
    assert failure.actual["oracle_total"] == 3 and failure.actual["system_total"] == 2


@pytest.mark.parametrize("record, result, needle", [
    (None, _csv(["a"]), "오라클 미실행"),
    ({"id": "B-01", "targets": [], "pre": None,
      "post": _outcome({}, status="unavailable", reason="대상 DB 가 없다")}, _csv(["a"]),
     "오라클 불가 - 대상 DB 가 없다"),
    ({"id": "B-01", "targets": ["polestar_cm_gp"], "pre": None,
      "post": _outcome({"polestar_cm_gp": []})}, _csv(["a"]), "오라클 0행"),
    ({"id": "B-01", "targets": ["polestar_cm_gp"], "pre": None,
      "post": _outcome({"polestar_cm_gp": [{"n": 1}]})}, None, "결과 행 미수집"),
])
def test_judge_unavailable_is_held(
    record: dict[str, Any] | None, result: dict[str, Any] | None, needle: str,
) -> None:
    """오라클 불가 결과 미수집은 보류다."""
    verdict = _judge(COUNT, _obs(oracle=record, result=result))
    assert verdict.func == "manual" and not verdict.failures
    assert verdict.manual_sources == ["oracle_unavailable"]
    (note,) = verdict.manual_notes
    assert note.startswith("oracle B-01 를 확인하지 못했다") and needle in note, note


def test_judge_passes_audit_counts_and_pre_post() -> None:
    """감사 DB별 행 수와 pre post 를 그대로 넘긴다."""
    by_audit = {"id": "B-01", "compare": "count", "system": "row_counts_by_db",
                "db_ids": ["polestar_b0", "polestar_cm_gp"]}
    record = {"id": "B-01", "targets": by_audit["db_ids"], "pre": None,
              "post": _outcome({"polestar_b0": [{"n": 5}], "polestar_cm_gp": [{"n": 7}]})}
    ok = _judge(by_audit, _obs(oracle=record,
                               row_counts_by_db={"polestar_b0": 5, "polestar_cm_gp": 7}))
    assert ok.func == "pass"
    bad = _judge(by_audit, _obs(oracle=record,
                                row_counts_by_db={"polestar_b0": 6, "polestar_cm_gp": 6}))
    assert bad.func == "fail" and bad.failures[0].actual["mismatch_dbs"] == ALL_DBS[:2]

    between = {"id": "D-01", "compare": "keyset", "key": [["alarm_id"]], "snapshot": "pre_post",
               "db_ids": ["polestar_cm_gp"]}
    pre = _outcome({"polestar_cm_gp": [{"alarm_id": "1"}, {"alarm_id": "2"}]}, phase="pre")
    post = _outcome({"polestar_cm_gp": [{"alarm_id": "2"}, {"alarm_id": "3"}]})
    record = {"id": "D-01", "targets": ["polestar_cm_gp"], "pre": pre, "post": post}
    assert _judge(between, _obs(oracle=record, result=_csv(["2", "3"], "alarm_id"))).func == "pass"
    lost = _judge(between, _obs(oracle=record, result=_csv(["3"], "alarm_id")))
    assert lost.func == "fail", "전·후 모두에 있던 키가 시스템에 없으면 불합격"
    no_pre = _judge(between, _obs(oracle={**record, "pre": None},
                                  result=_csv(["2", "3"], "alarm_id")))
    assert no_pre.func == "manual" and "턴 전 오라클 불가" in no_pre.manual_notes[0]


def test_judge_fixture_reads_answer_table() -> None:
    """fixture 오라클은 obs oracle 없이 정답표로 판정한다."""
    spec = {"id": "M-01", "source": "fixture", "compare": "keyset", "key": [["hostname"]],
            "field": "producer.expected"}
    expected = ["svr-web-05", "svr-web-08", "svr-was-03", "svr-was-07", "svr-db-04",
                "svbatch009", "svr-app-03", "svr-bat-02"]
    assert _judge(spec, _obs(result=_csv(expected))).func == "pass"
    assert _judge(spec, _obs(result=_csv(expected[:3]))).func == "fail"


def test_judge_mock_rule_matches_content_assertions() -> None:
    """모의 실행 규칙은 내용 단언과 같다."""
    record = {"id": "B-01", "targets": ["polestar_cm_gp"], "pre": None,
              "post": _outcome({"polestar_cm_gp": [{"n": 3}]})}
    canned = _judge(COUNT, _obs(oracle=record, result=_csv(["a"])), mock=True)
    assert canned.func == "manual" and not canned.failures
    assert "oracle" in canned.manual_notes[0] and canned.manual_sources == ["unobservable"]
    # `mock:` 블록이 있으면 판정한다 - 러너는 모의 실행에서 오라클을 돌리지 않으므로 보류다.
    authored = _judge(COUNT, _obs(result=_csv(["a"])), mock=True,
                      scenario_mock={"turns": [{"response": "r"}]})
    assert authored.func == "manual" and authored.manual_sources == ["oracle_unavailable"]


# --- 3 러너 -----------------------------------------------------------------------------------

class FakeClient:
    """`send`·`download_csv` 를 기록한다(순서를 본다)."""

    def __init__(self, responses: list[Observation], result: dict[str, Any] | None,
                 log: list[str]) -> None:
        self.responses = list(responses)
        self.result = result
        self.log = log
        self.downloads = 0

    def send(self, endpoint: str, payload: dict[str, Any], upload: Path | None = None,
             **kwargs: Any) -> Observation:
        self.log.append("send")
        return self.responses.pop(0)

    def download(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def download_csv(self, query_id: str) -> dict[str, Any]:
        self.log.append("download_csv")
        self.downloads += 1
        assert self.result is not None
        return self.result


def _done(**kwargs: Any) -> Observation:
    base: dict[str, Any] = dict(http_status=200, status="completed", query_id="q-1",
                                wall_ms=5.0, processing_time_ms=4.0, response="결과")
    base.update(kwargs)
    return Observation(**base)


RESULT = _csv(["srv-a", "srv-b"])
SECRET = "srv-secret-host-01"


class FakeOracle:
    """`oracle.run_oracle` 대역 — 호출 인자·순서를 기록하고 행 원문에 표지를 심는다."""

    def __init__(self, log: list[str], rows_by_db: dict[str, list[dict[str, Any]]] | None = None,
                 ) -> None:
        self.log = log
        self.calls: list[dict[str, Any]] = []
        self.rows_by_db = rows_by_db or {"polestar_cm_gp": [{"n": 2, "hostname": SECRET}]}

    def __call__(self, spec: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.log.append(f"oracle:{kwargs['phase']}")
        self.calls.append({"spec": spec, **kwargs})
        return {"status": "ok", "reason": None, "rows_by_db": self.rows_by_db,
                "elapsed_ms": 12.5, "phase": kwargs["phase"],
                "limit_by_db": {db: 1 for db in self.rows_by_db}}


def _run(tmp_path: Path, scenario: Scenario, client: FakeClient, *, mode: str = "run",
         meta: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    catalog = Catalog(groups={"T": GROUP}, scenarios=[scenario], profiles={"baseline": {}})
    raw = tmp_path / "raw.jsonl"
    runner_mod._run_once(catalog, RunConfig(mode=mode), dict(meta or META), "baseline",
                         scenario, 0, client, RawLog(raw), tmp_path, [],  # type: ignore[arg-type]
                         preference=[])
    return [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines() if line]


@pytest.fixture()
def fake_oracle(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeOracle, list[str]]:
    log: list[str] = []
    fake = FakeOracle(log)
    monkeypatch.setattr(oracle_mod, "run_oracle", fake)
    monkeypatch.setattr(runner_mod, "anchor_now", lambda: ANCHOR)
    return fake, log


def test_runner_pre_post_order_outside_timing(
    tmp_path: Path, fake_oracle: tuple[FakeOracle, list[str]],
) -> None:
    """pre post 는 송신 직전과 결과 행 뒤에 돌고 계측 밖이다."""
    fake, log = fake_oracle
    spec = {"id": "B-01", "compare": "count", "snapshot": "pre_post",
            "db_ids": ["polestar_cm_gp"]}
    client = FakeClient([_done()], RESULT, log)
    (row,) = _run(tmp_path, _scenario({"oracle": spec}), client)
    assert log == ["oracle:pre", "send", "download_csv", "oracle:post"]
    pre, post = fake.calls
    for call in (pre, post):
        assert call["spec"] == spec and call["db_ids"] == ["polestar_cm_gp"]
        assert call["anchor_at"] == ANCHOR, "두 phase 가 같은 앵커(같은 자리표 리터럴)를 쓴다"
        assert call["run_id"] == "R-1" and call["scenario_id"] == "T-01"
        assert call["log_path"] == tmp_path / "oracle_log.jsonl"
        assert call["timeout_sec"] == runner_mod.ORACLE_TIMEOUT_PER_DB_SEC * 1
    assert [c["phase"] for c in fake.calls] == ["pre", "post"]
    assert row["wall_ms"] == 5.0 and row["processing_time_ms"] == 4.0, "오라클은 계측 밖이다"
    assert row["anchor_at"] == ANCHOR
    assert row["func_verdict"] == "pass", (row["failed_assertions"], row["manual_notes"])


def test_runner_once_runs_post_only(
    tmp_path: Path, fake_oracle: tuple[FakeOracle, list[str]],
) -> None:
    """once 는 post 만 돈다."""
    fake, log = fake_oracle
    client = FakeClient([_done()], RESULT, log)
    _run(tmp_path, _scenario({"oracle": COUNT}), client)
    assert log == ["send", "download_csv", "oracle:post"]
    assert [c["phase"] for c in fake.calls] == ["post"]


def test_runner_targets_and_timeout(
    tmp_path: Path, fake_oracle: tuple[FakeOracle, list[str]],
) -> None:
    """대상 DB 는 spec 다음 턴 db ids 이고 타임아웃은 DB 수에 비례한다."""
    fake, log = fake_oracle
    spec = {"id": "B-01", "compare": "count"}
    client = FakeClient([_done(db_ids=["polestar_cm_gp"])], RESULT, log)
    _run(tmp_path, _scenario({"db_ids": ALL_DBS, "oracle": spec}), client)
    (call,) = fake.calls
    assert call["db_ids"] == ALL_DBS, "시스템이 고른 DB(관측 db_ids)가 아니라 선언을 쓴다"
    assert call["timeout_sec"] == runner_mod.ORACLE_TIMEOUT_PER_DB_SEC * 3


def test_runner_oracle_check_is_summary_only(
    tmp_path: Path, fake_oracle: tuple[FakeOracle, list[str]],
) -> None:
    """oracle check 에는 요약만 싣고 행 원문은 싣지 않는다."""
    _fake, log = fake_oracle
    spec = {**COUNT, "snapshot": "pre_post"}
    (row,) = _run(tmp_path, _scenario({"oracle": spec}), FakeClient([_done()], RESULT, log))
    assert row["oracle_check"] == {
        "id": "B-01", "targets": ["polestar_cm_gp"],
        "pre": {"phase": "pre", "status": "ok", "reason": None,
                "rows_by_db": {"polestar_cm_gp": 1}, "elapsed_ms": 12.5},
        "post": {"phase": "post", "status": "ok", "reason": None,
                 "rows_by_db": {"polestar_cm_gp": 1}, "elapsed_ms": 12.5},
    }
    assert SECRET not in json.dumps(row, ensure_ascii=False), "행 원문 금지(G-4)"


def test_runner_no_targets_skips_with_reason(
    tmp_path: Path, fake_oracle: tuple[FakeOracle, list[str]],
) -> None:
    """대상 DB 가 없으면 부르지 않고 보류 사유를 남긴다."""
    fake, log = fake_oracle
    (row,) = _run(tmp_path, _scenario({"oracle": {"id": "B-01", "compare": "count"}}),
                  FakeClient([_done()], RESULT, log))
    assert fake.calls == [] and "oracle:post" not in log
    assert row["oracle_check"]["post"]["status"] == "unavailable"
    assert "대상 DB 가 없다" in row["oracle_check"]["post"]["reason"]
    assert row["func_verdict"] == "manual" and row["manual_sources"] == ["oracle_unavailable"]
    assert "대상 DB 가 없다" in row["manual_notes"][0]


@pytest.mark.parametrize("case", ["env_hold", "mock", "fixture", "no_oracle"])
def test_runner_turns_that_never_call_db(
    tmp_path: Path, fake_oracle: tuple[FakeOracle, list[str]], case: str,
) -> None:
    """DB 를 부르지 않는 턴."""
    fake, log = fake_oracle
    expect: dict[str, Any] = {"oracle": COUNT}
    kwargs: dict[str, Any] = {}
    mode = "run"
    if case == "env_hold":
        kwargs["env"] = "sandbox"          # 실행 env=closed - 판정용 expect 에서 oracle 이 빠진다
    elif case == "mock":
        mode = "mock"
    elif case == "fixture":
        expect = {"oracle": {"id": "M-01", "source": "fixture", "compare": "keyset",
                             "key": [["hostname"]], "field": "producer.expected"}}
    else:
        expect = {"status": "completed"}
    (row,) = _run(tmp_path, _scenario(expect, **kwargs), FakeClient([_done()], RESULT, log),
                  mode=mode)
    assert fake.calls == [], case
    assert row["oracle_check"] is None
    assert not (tmp_path / "oracle_log.jsonl").exists()
    if case == "fixture":
        assert log == ["send", "download_csv"], "fixture 는 결과 행만 받아 판정기가 정답표를 읽는다"
        assert row["func_verdict"] == "fail", "정답표와 다른 결과 행은 불합격이다"
        assert [f["key"] for f in row["failed_assertions"]] == ["oracle"]
    if case == "env_hold":
        assert log == ["send"], "보류할 단언을 위해 결과 행도 받지 않는다"
        assert row["manual_sources"] == ["env_mismatch"]


def _security_cfg() -> SimpleNamespace:
    return SimpleNamespace(db_backend="dbhub", security=SecurityConfig(
        sensitive_columns=["password"], mask_ip=False, mask_email=False,
        mask_pattern="***MASKED***"))


def test_runner_end_to_end_with_real_run_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """가짜 DB 클라이언트로 `oracle.run_oracle` 을 실제로 태운다(렌더·태그·로그·마스킹 대칭)."""
    seen: list[tuple[str, str]] = []

    def factory(_cfg: Any, db_id: str) -> Any:
        class _Client:
            async def execute_sql(self, sql: str) -> Any:
                seen.append((db_id, sql))
                return SimpleNamespace(rows=[{"n": 2}], truncated=False)

        @asynccontextmanager
        async def _ctx() -> AsyncIterator[_Client]:
            yield _Client()

        return _ctx()

    import src.config

    monkeypatch.setattr(oracle_mod, "_open_client", factory)
    monkeypatch.setattr(src.config, "load_config", _security_cfg)
    monkeypatch.setattr(runner_mod, "anchor_now", lambda: ANCHOR)
    log: list[str] = []
    (row,) = _run(tmp_path, _scenario({"oracle": COUNT}), FakeClient([_done()], RESULT, log))
    ((db_id, sql),) = seen
    assert db_id == "polestar_cm_gp"
    assert sql.startswith("/* scenario-oracle run=R-1 scn=T-01 */")
    (line,) = (tmp_path / "oracle_log.jsonl").read_text(encoding="utf-8").splitlines()
    record = json.loads(line)
    assert record["status"] == "ok" and record["rows"] == 1 and record["phase"] == "post"
    assert record["anchor_at"] == ANCHOR
    assert row["func_verdict"] == "pass", (row["failed_assertions"], row["manual_notes"])
    assert row["oracle_check"]["post"]["rows_by_db"] == {"polestar_cm_gp": 1}


# --- 4 오라클 자리표 = 해석기 -----------------------------------------------------------------

KST = timezone(timedelta(hours=9))


def _legacy_anchor_values(anchor_at: str) -> dict[str, str]:
    """`relative_window` 로 옮기기 전 `oracle.anchor_values` 의 월 산술(대조용 사본).

    2026-09-29 작업 트리 `scripts/scenario/oracle.py` 에서 옮겼다.
    """
    moment = datetime.fromisoformat(anchor_at)
    moment = moment.replace(tzinfo=KST) if moment.tzinfo is None else moment.astimezone(KST)
    today = moment.date()

    def shift(delta: int) -> tuple[int, int]:
        index = today.year * 12 + (today.month - 1) + delta
        return index // 12, index % 12 + 1

    prev_y, prev_m = shift(-1)
    next_y, next_m = shift(1)
    yesterday = today - timedelta(days=1)
    values = {
        "anchor_at": moment.replace(microsecond=0).isoformat(),
        "today": today.isoformat(), "today_ymd": today.strftime("%Y%m%d"),
        "yesterday": yesterday.isoformat(), "yesterday_ymd": yesterday.strftime("%Y%m%d"),
        "month_start": today.replace(day=1).isoformat(),
        "month_start_ymd": today.replace(day=1).strftime("%Y%m%d"),
        "anchor_month": f"{today.year:04d}{today.month:02d}",
        "prev_month": f"{prev_y:04d}{prev_m:02d}",
        "prev_month_start": f"{prev_y:04d}-{prev_m:02d}-01",
        "next_month_start": f"{next_y:04d}-{next_m:02d}-01",
    }
    for back in range(1, 13):
        year, month = shift(-back)
        values[f"month_minus_{back}"] = f"{year:04d}{month:02d}"
    return values


def test_anchor_values_unchanged_after_resolver_alignment() -> None:
    """자리표는 해석기로 옮긴 뒤에도 종전 값과 같다."""
    day = date(2027, 11, 1)
    anchors = []
    while day < date(2029, 3, 5):          # 연 넘김 2회 · 윤년 2028-02-29 포함
        for clock in ("00:00:00", "23:59:59"):
            anchors.append(f"{day.isoformat()}T{clock}+09:00")
        day += timedelta(days=1)
    anchors += ["2026-09-30T20:00:00Z", "2026-09-30T23:00:00", "2026-12-31T15:30:00Z"]
    for anchor in anchors:
        assert oracle_mod.anchor_values(anchor) == _legacy_anchor_values(anchor), anchor
