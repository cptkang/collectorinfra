"""러너의 역질문 자동 응답 · 환경 판정 · 환경 불일치 보류 (D-216).

서버를 띄우지 않는다 - 가짜 클라이언트가 응답을 순서대로 돌려주고 러너가 무엇을 보냈는지 기록한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import pytest

from scripts.scenario import runner as runner_mod
from scripts.scenario.assertions import Observation
from scripts.scenario.catalog import Catalog, Group, Scenario, Turn
from scripts.scenario.mockserver import _Resolver
from scripts.scenario.runner import RawLog, RunConfig, detect_env, resolve_env, zoned_db_ids

ZONE = {
    "kind": "zone_select",
    "question": "조회할 존이 지정되지 않았습니다.",
    "original_query": "전체 서버 수 알려줘",
    "options": [
        {"db_id": "polestar_b0", "label": "은행존"},
        {"db_id": "polestar_cm_gp", "label": "공동존(김포)"},
    ],
}


class FakeClient:
    def __init__(self, responses: list[Observation]) -> None:
        self.responses = list(responses)
        self.sent: list[tuple[str, dict[str, Any], Optional[Path]]] = []

    def send(self, endpoint: str, payload: dict[str, Any], upload: Optional[Path] = None) -> Observation:
        self.sent.append((endpoint, dict(payload), upload))
        return self.responses.pop(0)

    def download(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _run(tmp_path: Path, scenario: Scenario, responses: list[Observation],
         run_env: Optional[str] = "closed") -> tuple[FakeClient, list[dict]]:
    catalog = Catalog(groups={"T": Group("T", "테스트군", 60000)}, scenarios=[scenario],
                      profiles={"baseline": {}})
    client = FakeClient(responses)
    raw = RawLog(tmp_path / "raw.jsonl")
    meta = {"run_id": "r", "env": run_env, "mode": "run"}
    runner_mod._run_once(catalog, RunConfig(mode="run"), meta, "baseline", scenario, 0,
                         client, raw, tmp_path, [], preference=["polestar_cm_gp"])
    rows = [json.loads(line) for line in (tmp_path / "raw.jsonl").read_text(encoding="utf-8").splitlines()]
    return client, rows


def _scenario(turns: list[Turn], **kwargs: Any) -> Scenario:
    return Scenario(id="T-01", group="T", plans=[94], title="t", turns=turns,
                    env=kwargs.pop("env", "closed"), **kwargs)


def _done(**kwargs: Any) -> Observation:
    return Observation(http_status=200, status=kwargs.pop("status", "completed"), **kwargs)


# --- 존 선택 자동 응답 --------------------------------------------------

def test_존_역질문에_김포로_답하고_답을_받은_뒤로_판정한다(tmp_path: Path) -> None:
    scenario = _scenario([Turn({"query": "전체 서버 수 알려줘"},
                               {"status": "completed", "db_ids": ["polestar_cm_gp"]})])
    client, rows = _run(tmp_path, scenario, [
        _done(status="clarification", clarification=ZONE, wall_ms=20.0),
        _done(db_ids=["polestar_cm_gp"], row_count=12),
    ])

    assert len(client.sent) == 2
    endpoint, body, upload = client.sent[1]
    assert endpoint == "stream" and upload is None
    assert body["selected_db_ids"] == ["polestar_cm_gp"]
    assert body["query"] == "전체 서버 수 알려줘"
    assert body["thread_id"] == client.sent[0][1]["thread_id"], "같은 스레드로 답해야 한다"

    (row,) = rows
    assert row["func_verdict"] == "pass"
    assert row["auto_answers"][0]["selected_db_ids"] == ["polestar_cm_gp"]
    assert row["auto_answers"][0]["question_status"] == "clarification"


def test_YAML_명시값이_있으면_그_존으로_답한다(tmp_path: Path) -> None:
    scenario = _scenario([Turn({"query": "q"}, {"status": "completed"})],
                         auto_answer={"selected_db_ids": ["polestar_b0"]})
    client, _ = _run(tmp_path, scenario, [
        _done(status="clarification", clarification=ZONE), _done(),
    ])
    assert client.sent[1][1]["selected_db_ids"] == ["polestar_b0"]


def test_역질문을_기대하는_턴에는_답하지_않는다(tmp_path: Path) -> None:
    scenario = _scenario([Turn({"query": "q"}, {"status": "clarification"})])
    client, rows = _run(tmp_path, scenario, [_done(status="clarification", clarification=ZONE)])
    assert len(client.sent) == 1
    assert rows[0]["func_verdict"] == "pass"
    assert "auto_answers" not in rows[0]


def test_auto_answer_false_턴은_역질문을_그대로_판정한다(tmp_path: Path) -> None:
    """승계 회귀를 잡는 턴 - 자동 응답이 회귀를 가리면 안 된다(F-06)."""
    scenario = _scenario([Turn({"query": "그 서버들의 메모리도"}, {"status": "completed"},
                               auto_answer=False)])
    client, rows = _run(tmp_path, scenario, [_done(status="clarification", clarification=ZONE)])
    assert len(client.sent) == 1
    assert rows[0]["func_verdict"] == "fail"


def test_같은_역질문이_되풀이되면_멈추고_역질문으로_판정한다(tmp_path: Path) -> None:
    scenario = _scenario([Turn({"query": "q"}, {"status": "completed"})])
    same = _done(status="clarification", clarification=ZONE)
    client, rows = _run(tmp_path, scenario, [same, _done(status="clarification", clarification=ZONE)])
    assert len(client.sent) == 2
    assert rows[0]["func_verdict"] == "fail"
    assert len(rows[0]["auto_answers"]) == 1


def test_존_선택_뒤_승인_대기까지_연속으로_답한다(tmp_path: Path) -> None:
    scenario = _scenario([Turn({"query": "q"}, {"status": "completed"})])
    client, rows = _run(tmp_path, scenario, [
        _done(status="clarification", clarification=ZONE),
        _done(status="awaiting_approval", response="이 SQL 을 실행할까요?"),
        _done(),
    ])
    assert client.sent[2][1]["query"] == "승인"
    assert [a["kind"] for a in rows[0]["auto_answers"]] == ["zone_select", "approval"]
    assert rows[0]["func_verdict"] == "pass"


def test_폼필_역질문은_JSON_경로로_공란_답변을_보낸다(tmp_path: Path) -> None:
    form = {"fields": [{"name": "담당자", "label": "담당자"}], "candidates": []}
    scenario = _scenario([Turn({"query": "김포 서버로 채워줘"}, {"status": "completed"})],
                         endpoint="file_stream", upload="testdata/scenarios/fixtures/form_sample.xlsx")
    client, _ = _run(tmp_path, scenario, [
        _done(form_fill_clarification=form, has_file=False), _done(),
    ])
    endpoint, body, upload = client.sent[1]
    assert endpoint == "stream" and upload is None
    assert body["form_fill_answers"] == {"담당자": {"action": "blank", "value": None}}


def test_파일_경로의_존_역질문은_파일을_다시_올린다(tmp_path: Path) -> None:
    scenario = _scenario([Turn({"query": "채워줘"}, {"status": "completed"})],
                         endpoint="file_stream", upload="testdata/scenarios/fixtures/form_sample.xlsx")
    client, _ = _run(tmp_path, scenario, [
        _done(status="clarification", clarification={**ZONE, "has_file": True}), _done(),
    ])
    endpoint, body, upload = client.sent[1]
    assert endpoint == "file_stream" and upload is not None
    assert body["selected_db_ids"] == ["polestar_cm_gp"]


def test_질의_없는_답변_턴은_직전_역질문의_원문으로_채운다(tmp_path: Path) -> None:
    """서버는 query 를 필수로 받는다 - 채우지 않으면 F-01 2턴이 422 로 끝난다."""
    scenario = _scenario([
        Turn({"query": "전체 서버 수 알려줘"}, {"status": "clarification"}),
        Turn({"selected_db_ids": ["polestar_cm_gp"]}, {"status": "completed"}),
    ])
    client, rows = _run(tmp_path, scenario, [
        _done(status="clarification", clarification=ZONE), _done(),
    ])
    assert client.sent[1][1]["query"] == "전체 서버 수 알려줘"
    assert [row["func_verdict"] for row in rows] == ["pass", "pass"]


# --- 환경 판정 · 불일치 보류 ---------------------------------------------

def test_레지스트리의_존_DB만_운영_폴스타로_본다() -> None:
    zoned = zoned_db_ids()
    assert {"polestar_b0", "polestar_cm_gp", "polestar_cm_yd"} <= zoned
    assert "polestar" not in zoned


@pytest.mark.parametrize("active, expected", [
    (["polestar_b0", "polestar_cm_gp"], "closed"),
    (["polestar"], "sandbox"),
    (["polestar", "polestar_cm_yd"], "closed"),
    (["itam"], None),
    ([], None),
])
def test_활성_DB로_환경을_판정한다(active: list[str], expected: Optional[str]) -> None:
    assert detect_env(active, {"polestar_b0", "polestar_cm_gp", "polestar_cm_yd"}) == expected


def test_env_명시가_자동_판정을_이기고_모의_실행은_판정하지_않는다() -> None:
    assert resolve_env(RunConfig(mode="run", env="sandbox")) == ("sandbox", "cli")
    assert resolve_env(RunConfig(mode="mock")) == (None, "mock")


def test_환경이_다른_시나리오는_실행하되_데이터_의존_단언을_보류한다(tmp_path: Path) -> None:
    scenario = _scenario([Turn({"query": "DB-ORA-023 커널 파라미터"},
                               {"status": "completed", "row_count": {"min": 2},
                                "sql_must_not_match": ["(?i)delete"]})], env="sandbox")
    client, rows = _run(tmp_path, scenario, [_done(row_count=0)], run_env="closed")

    assert len(client.sent) == 1, "보류는 실행을 막지 않는다"
    (row,) = rows
    assert row["func_verdict"] == "manual"
    assert row["failed_assertions"] == []
    assert any("환경 불일치" in note and "row_count" in note for note in row["manual_notes"])
    assert row["env_mismatch"] == {"scenario_env": "sandbox", "run_env": "closed"}


def test_환경을_판정하지_못하면_환경_전용_시나리오는_보류한다(tmp_path: Path) -> None:
    scenario = _scenario([Turn({"query": "q"}, {"status": "completed"})], env="closed")
    _, rows = _run(tmp_path, scenario, [_done(status="clarification")], run_env=None)
    assert rows[0]["func_verdict"] == "manual"


def test_both_시나리오는_어느_환경에서나_그대로_판정한다(tmp_path: Path) -> None:
    scenario = _scenario([Turn({"query": "q"}, {"status": "completed"})], env="both")
    _, rows = _run(tmp_path, scenario, [_done(status="error", error="boom")], run_env="sandbox")
    assert rows[0]["func_verdict"] in ("fail", "error")
    assert "env_mismatch" not in rows[0]


# --- 모의 서버의 자동 응답 모사 -----------------------------------------

def test_모의_서버는_answer_가_있는_턴의_다음_요청을_같은_턴의_답으로_받는다() -> None:
    scenario = Scenario(id="T-02", group="T", plans=[94], title="t",
                        turns=[Turn({"query": "q"}, {})],
                        mock={"turns": [{"clarification": ZONE, "answer": {"row_count": 1}}]})
    resolver = _Resolver(Catalog(groups={}, scenarios=[scenario]))

    first = resolver.resolve("q", "th")
    assert first[1:] == (1, False)
    resolver.settle("th", {"clarification": ZONE})
    assert resolver.resolve(None, "th")[1:] == (1, True)
    resolver.settle("th", {"row_count": 1})
    assert resolver.resolve(None, "th")[1:] == (2, False), "답을 받은 뒤에는 다음 턴으로 넘어간다"
