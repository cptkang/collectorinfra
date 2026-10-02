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


# --- 배선 회귀 감시 (D-237 · run 20260914-185540) -------------------------
#
# 그 런은 `clarify.py` 자체가 없던 커밋에서 돌아 6567턴 중 56%가 역질문에서 끝났고,
# 판정표는 정상 형태로 나왔다. **정의가 있어도 배선이 없으면 무효**라는 반복 실수이므로
# "자동 응답이 끊기면 턴이 역질문으로 끝난다"를 단언으로 못 박는다.


def test_자동_응답_배선이_끊기면_턴이_역질문으로_끝난다(tmp_path: Path, monkeypatch) -> None:
    """`_answer_questions` 를 무력화하면 같은 시나리오가 `clarify` 로 끝나야 한다.

    이 테스트가 통과한다는 것은 **정상 경로의 통과가 배선 덕분**임을 뜻한다 —
    배선 없이도 통과하면 위 정상 테스트들은 아무것도 지키지 못하는 것이다.
    """
    scenario = _scenario([Turn({"query": "전체 서버 수 알려줘"},
                               {"status": "completed", "db_ids": ["polestar_cm_gp"]})])
    responses = [_done(status="clarification", clarification=dict(ZONE)),
                 _done(db_ids=["polestar_cm_gp"])]

    # 1) 배선이 살아 있으면 역질문을 넘어간다.
    _client, rows = _run(tmp_path, scenario, list(responses))
    assert rows[0]["response_mode"] != "clarify"
    assert rows[0]["auto_answers"], "자동 응답 기록이 없으면 배선을 확인할 수 없다"

    # 2) 배선을 끊으면 같은 입력이 역질문으로 끝난다.
    # 배선만 끊는다 — 받은 관측치를 그대로 돌려주는 no-op 으로 바꾼다.
    monkeypatch.setattr(runner_mod, "_answer_questions",
                        lambda _c, _s, _e, _u, _t, _q, obs, *a, **kw: obs)
    _client2, rows2 = _run(tmp_path / "off", scenario, list(responses))
    assert rows2[0]["response_mode"] == "clarify"
    assert "auto_answers" not in rows2[0]


def test_응답_본문_없는_구조화_턴은_직전_역질문_원문으로_채워진다(tmp_path: Path) -> None:
    """`clarify.complete_payload` 배선 확인 — 없으면 서버가 422 로 끊는다.

    run 20260914-185540 의 F군 314턴이 정확히 이 형태(`body.query Field required`)였다.
    """
    scenario = _scenario([
        Turn({"query": "전체 서버 수 알려줘"}, {"status": "clarification"}, auto_answer=False),
        Turn({"selected_db_ids": ["polestar_cm_gp"]}, {"status": "completed"}),
    ])
    client, _rows = _run(tmp_path, scenario, [
        _done(status="clarification", clarification=dict(ZONE)),
        _done(db_ids=["polestar_cm_gp"]),
    ])

    second = client.sent[1][1]
    assert second["selected_db_ids"] == ["polestar_cm_gp"]
    # query 가 비면 서버가 422 로 끊는다(`QueryRequest.query` min_length=1).
    assert second.get("query")


# --- 실행 순서는 호출부가 정한다 (R-6 · D-237) ---------------------------


def _order_catalog(profiles: list[str]) -> Catalog:
    import dataclasses
    base = Scenario(id="S-01", group="T", plans=[94], title="t", env="both",
                    turns=[Turn({"query": "q"}, {"status": "completed"})])
    return Catalog(groups={"T": Group("T", "테스트군", 60000)},
                   scenarios=[dataclasses.replace(base, profile=p) for p in profiles],
                   profiles={p: {} for p in profiles})


def test_프로파일_순서를_주면_그대로_돈다(tmp_path: Path) -> None:
    """`"baseline"`(0x62) > `"S2-"`(0x53) 라 알파벳 정렬은 기준선을 늘 마지막에 놓는다.

    93 스위프는 62 arm 을 93.4시간 연속 돌렸고 기준선이 4일차에 돌아 쌍체 지연이
    실행 시각과 교란됐다(순서 대 중앙 지연 r=-0.267).
    """
    names = ["baseline", "S2-K-true", "S2-K-false"]
    catalog = _order_catalog(names)

    got = [p for p, _ in runner_mod.iter_executions(catalog, RunConfig(profiles=names))]

    assert got == names
    assert got[0] == "baseline", "기준선이 먼저 돌아야 시간 교란이 사라진다"


def test_프로파일_지정이_없으면_종전대로_알파벳순이다(tmp_path: Path) -> None:
    catalog = _order_catalog(["baseline", "S2-K-true", "S2-K-false"])

    got = [p for p, _ in runner_mod.iter_executions(catalog, RunConfig())]

    assert got == sorted(got), "94 단독 실행의 재현성은 그대로 둔다"


def test_지정에_없는_프로파일도_빠뜨리지_않는다(tmp_path: Path) -> None:
    """지정 순서를 지키되 **조용히 누락하지 않는다** — 뒤에 알파벳순으로 붙인다."""
    catalog = _order_catalog(["baseline", "S2-K-true", "S2-K-false"])

    got = [p for p, _ in runner_mod.iter_executions(
        catalog, RunConfig(profiles=["baseline", "S2-K-true", "S2-K-false", "없는arm"]))]

    assert got == ["baseline", "S2-K-true", "S2-K-false"]
