"""러너·클라이언트 관측 수집 (plans/122 H-1 · H-2 · H-5 · J-3) — 관측 수집 담당.

고정하는 계약:
  - H-1 결과 행: `ScenarioClient.download_csv` 가 CSV 를 `Observation.result` 모양으로
    옮긴다(BOM · 행 상한 · 404 두 갈래 - 「행 없음」은 empty, 「저장소에 없음(축출)」은
    unavailable). 러너는 결과 행이 필요한 턴에서만, 턴 완료 직후·판정 전에 받고, 받은 행이
    판정에 들어간다. 계측값(wall_ms)은 그대로다.
  - H-2 앵커: 턴 송신 직전의 KST 초 단위 시각. 역질문 자동 응답이 끼어도 첫 송신 시각이다.
  - H-5: 스트림·비스트림 done 의 `dependency_notes`(목록일 때만)가 관측치에 실린다.
  - J-3: 환경 보류 턴은 `env_mismatch` 가 출처 맨 앞이고, 원 턴에 `manual_review` 가 없으면
    `catalog` 가 없다. `manual_notes` 문구는 그대로다.
  - 모의 서버 `/query/{id}/download-csv` 는 모의 블록 `query_results` 를 실 서버와 같은 CSV 로 낸다.
전부 무과금이다(LLM·DB·서버 프로세스 0 - HTTP 는 MockTransport·TestClient).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts.scenario import client as client_mod
from scripts.scenario import runner as runner_mod
from scripts.scenario.assertions import Observation, Verdict
from scripts.scenario.catalog import Catalog, Group, Scenario, Turn
from scripts.scenario.client import (
    RESULT_ROWS_MAX,
    ClientConfig,
    ScenarioClient,
    _apply_done,
    parse_result_csv,
)
from scripts.scenario.runner import RawLog, RunConfig, mark_env_hold, needs_result_rows

META = {"run_id": "r", "env": "closed", "mode": "run"}
BOM = "\ufeff"


def _http_client(
    handler: Callable[[httpx.Request], httpx.Response], **config: Any
) -> ScenarioClient:
    client = ScenarioClient(ClientConfig(port=1, **config))
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def _csv_response(text: str) -> httpx.Response:
    return httpx.Response(200, content=(BOM + text).encode("utf-8"),
                          headers={"content-type": "text/csv; charset=utf-8"})


# --- H-1 클라이언트: download-csv ----------------------------------------------------

def test_download_csv_는_BOM을_떼고_문자열_행으로_옮긴다() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _csv_response('hostname,cpu,note\r\nsrv-1,12.5,"a, b"\r\nsrv-2,,"줄\n바꿈"\r\n')

    result = _http_client(handler, token="tok").download_csv("q-123")
    assert seen[0].url.path == "/api/v1/query/q-123/download-csv"
    assert seen[0].headers["authorization"] == "Bearer tok", "질의한 사용자 토큰으로 받는다"
    assert result == {
        "status": "ok", "columns": ["hostname", "cpu", "note"],
        "rows": [{"hostname": "srv-1", "cpu": "12.5", "note": "a, b"},
                 {"hostname": "srv-2", "cpu": "", "note": "줄\n바꿈"}],
        "total_rows": 2, "truncated": False, "reason": None,
    }


def test_download_csv_는_행_상한을_넘으면_앞부분만_싣고_전체_수를_남긴다() -> None:
    body = "id\r\n" + "".join(f"{i}\r\n" for i in range(RESULT_ROWS_MAX + 7))
    result = _http_client(lambda _r: _csv_response(body)).download_csv("q")
    assert result["status"] == "ok" and result["truncated"] is True
    assert result["total_rows"] == RESULT_ROWS_MAX + 7
    assert len(result["rows"]) == RESULT_ROWS_MAX
    assert result["rows"][0] == {"id": "0"}


def test_download_csv_404_행_없음은_빈_결과다() -> None:
    result = _http_client(lambda _r: httpx.Response(
        404, json={"detail": "다운로드할 조회 결과가 없습니다."})).download_csv("q")
    assert result == {"status": "empty", "columns": [], "rows": [], "total_rows": 0,
                      "truncated": False, "reason": None}


def test_download_csv_404_저장소에_없음은_받지_못함이다() -> None:
    """LRU 축출·저장하지 않는 응답을 「빈 결과」로 세면 거짓 불합격이다(plans/122 §8)."""
    result = _http_client(lambda _r: httpx.Response(
        404, json={"detail": "결과를 찾을 수 없습니다."})).download_csv("q")
    assert result["status"] == "unavailable"
    assert "축출" in result["reason"] and "결과를 찾을 수 없습니다." in result["reason"]


@pytest.mark.parametrize("response, marker", [
    (httpx.Response(403, text="forbidden"), "http 403"),
    (httpx.Response(500, text="boom"), "http 500"),
])
def test_download_csv_기타_오류는_받지_못함과_사유다(response: httpx.Response, marker: str) -> None:
    result = _http_client(lambda _r: response).download_csv("q")
    assert result["status"] == "unavailable" and marker in result["reason"]


def test_download_csv_연결_실패도_예외_없이_받지_못함이다() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    result = _http_client(handler).download_csv("q")
    assert result["status"] == "unavailable" and "ConnectError" in result["reason"]


def test_parse_result_csv_빈_본문과_머리글만은_빈_결과다() -> None:
    assert parse_result_csv(b"")["status"] == "empty"
    only_header = parse_result_csv((BOM + "a,b\r\n").encode("utf-8"))
    assert only_header["status"] == "empty" and only_header["columns"] == ["a", "b"]
    assert parse_result_csv(b"a\r\n1\r\n2\r\n3\r\n", limit=2)["truncated"] is True


# --- H-5 dependency_notes 수집 ----------------------------------------------------

NOTES = [{"kind": "gate", "task": "t2", "reason": "선행 0건"}, {"kind": "bridge", "count": 3}]


def test_done_의_dependency_notes_목록을_그대로_싣는다() -> None:
    obs = Observation()
    _apply_done(obs, {"response": "r", "dependency_notes": NOTES})
    assert obs.dependency_notes == NOTES


@pytest.mark.parametrize("value", [None, {"kind": "gate"}, "gate"])
def test_목록이_아닌_dependency_notes_는_싣지_않는다(value: Any) -> None:
    obs = Observation()
    _apply_done(obs, {"response": "r", "dependency_notes": value})
    assert obs.dependency_notes == []


def test_스트림_done_의_dependency_notes_가_관측치에_실린다() -> None:
    events = [{"type": "node_start", "node": "intent_planner", "timestamp_ms": 0},
              {"type": "done", "response": "r", "query_id": "q", "dependency_notes": NOTES,
               "form_memory_panel": {"signature": "s"}}]
    body = "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events)
    client = _http_client(lambda _r: httpx.Response(
        200, content=body.encode("utf-8"), headers={"content-type": "text/event-stream"}))
    obs = client.send("stream", {"query": "q"})
    assert obs.dependency_notes == NOTES
    assert obs.form_memory_panel == {"signature": "s"}, "저장 값 패널 수집은 종전 그대로다"


def test_비스트림_응답의_dependency_notes_도_실린다() -> None:
    client = _http_client(lambda _r: httpx.Response(
        200, json={"query_id": "q", "response": "r", "status": "success",
                   "dependency_notes": NOTES}))
    assert client.send("plain", {"query": "q"}).dependency_notes == NOTES


# --- 러너 FakeClient -------------------------------------------------------------

class FakeClient:
    """`send`·`download_csv` 호출을 기록한다(다운로드 여부·순서를 본다)."""

    def __init__(self, responses: list[Observation],
                 result: dict[str, Any] | None = None, log: list[str] | None = None) -> None:
        self.responses = list(responses)
        self.sent: list[dict[str, Any]] = []
        self.downloads: list[str] = []
        self.result = result
        self.log = log if log is not None else []

    def send(self, endpoint: str, payload: dict[str, Any], upload: Path | None = None,
             **kwargs: Any) -> Observation:
        self.log.append("send")
        self.sent.append({"endpoint": endpoint, "payload": dict(payload), **kwargs})
        return self.responses.pop(0)

    def download(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def download_csv(self, query_id: str) -> dict[str, Any]:
        self.log.append("download_csv")
        self.downloads.append(query_id)
        assert self.result is not None
        return self.result


def _done(**kwargs: Any) -> Observation:
    base: dict[str, Any] = dict(http_status=200, status="completed", query_id="q-1",
                                wall_ms=5.0, processing_time_ms=5.0, response="결과")
    base.update(kwargs)
    return Observation(**base)


def _scenario(expect: dict[str, Any], **kwargs: Any) -> Scenario:
    base: dict[str, Any] = dict(id="T-01", group="T", plans=[122], title="t",
                                turns=[Turn({"query": "q"}, expect)])
    base.update(kwargs)
    return Scenario(**base)


def _run(tmp_path: Path, scenario: Scenario, client: FakeClient,
         meta: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    catalog = Catalog(groups={"T": Group("T", "T", 60000)}, scenarios=[scenario],
                      profiles={"baseline": {}})
    raw = tmp_path / "raw.jsonl"
    runner_mod._run_once(catalog, RunConfig(mode="run"), dict(meta or META), "baseline", scenario,
                         0, client, RawLog(raw), tmp_path, [], preference=[])  # type: ignore[arg-type]
    return [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines() if line]


OK_RESULT = {"status": "ok", "columns": ["hostname", "cpu"],
             "rows": [{"hostname": "srv-1", "cpu": "10"}, {"hostname": "srv-2", "cpu": "20"}],
             "total_rows": 2, "truncated": False, "reason": None}


@pytest.mark.parametrize("expect, needed", [
    ({"status": "completed"}, False),
    ({"period_covers": {"from": "2026-07-01", "to": "2026-08-01"}}, False),
    ({"result": {"columns": ["hostname"]}}, True),
    ({"period_covers": {"relative": "last_month"}}, True),
    ({"period_covers": {"relative": {"month_span": {"from": 11, "to": 2}}}}, True),
    ({"period_covers": {"month_span": {"from": 11, "to": 2}}}, True),
    # 「날짜 한정 없음」은 실행 SQL 만 본다(plans/122 H-2 통합) - 결과 행을 받지 않는다.
    ({"period_covers": {"unbounded": True}}, False),
    ({"oracle": {"id": "b01", "compare": "count"}}, True),
])
def test_결과_행은_필요한_턴에서만_받는다(expect: dict[str, Any], needed: bool) -> None:
    assert needs_result_rows(expect) is needed


def test_result_단언_턴은_결과_행을_받아_판정하고_요약만_적재한다(tmp_path: Path) -> None:
    log: list[str] = []
    client = FakeClient([_done()], result=OK_RESULT, log=log)
    rows = _run(tmp_path, _scenario({"status": "completed",
                                     "result": {"columns": ["hostname"],
                                                "value_range": {"cpu": [0, 100]}}}), client)
    (row,) = rows
    assert client.downloads == ["q-1"]
    assert log == ["send", "download_csv"], "턴 완료 직후에 받는다"
    assert row["func_verdict"] == "pass", "받은 행이 판정에 들어간다"
    assert row["result_check"] == {"status": "ok", "total_rows": 2, "column_count": 2,
                                   "truncated": False, "reason": None}
    assert row["wall_ms"] == 5.0, "다운로드는 계측 밖이다 - 송신 지연이 그대로다"
    assert "srv-1" not in json.dumps(row, ensure_ascii=False), "행 원문은 적재하지 않는다(G-4)"


def test_받은_결과_행이_단언과_어긋나면_불합격이다(tmp_path: Path) -> None:
    client = FakeClient([_done()], result=OK_RESULT)
    (row,) = _run(tmp_path, _scenario({"result": {"columns": ["없는열"]}}), client)
    assert row["func_verdict"] == "fail"
    assert [f["key"] for f in row["failed_assertions"]] == ["result.columns"]


def test_결과_행이_필요없는_턴은_받지_않는다(tmp_path: Path) -> None:
    client = FakeClient([_done()], result=OK_RESULT)
    (row,) = _run(tmp_path, _scenario({"status": "completed"}), client)
    assert client.downloads == [] and row["result_check"] is None


def test_query_id_가_없으면_받지_못함으로_남는다(tmp_path: Path) -> None:
    client = FakeClient([_done(query_id=None)], result=OK_RESULT)
    (row,) = _run(tmp_path, _scenario({"result": {"columns": ["hostname"]}}), client)
    assert client.downloads == []
    assert row["result_check"]["status"] == "unavailable"
    assert row["func_verdict"] == "manual", "받지 못한 결과는 불합격이 아니라 보류다"
    assert row["manual_sources"] == ["unobservable"]


def test_환경_보류_턴은_결과_행을_받지_않는다(tmp_path: Path) -> None:
    client = FakeClient([_done()], result=OK_RESULT)
    (row,) = _run(tmp_path, _scenario({"result": {"columns": ["hostname"]}}, env="sandbox"),
                  client)
    assert client.downloads == [], "보류할 단언을 위해 서버 감사에 다운로드를 남기지 않는다"
    assert row["result_check"] is None


# --- H-2 앵커 ------------------------------------------------------------------

def test_앵커는_KST_초_단위_ISO_다() -> None:
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+09:00", runner_mod.anchor_now())


def test_앵커는_턴_송신_직전에_잡고_자동_응답이_끼어도_첫_송신_시각이다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    log: list[str] = []
    stamps = iter(["2026-09-30T23:59:59+09:00", "2026-10-01T00:00:05+09:00"])

    def fake_anchor() -> str:
        log.append("anchor")
        return next(stamps)

    monkeypatch.setattr(runner_mod, "anchor_now", fake_anchor)
    question = _done(status="clarification", query_id="q-0", clarification={
        "kind": "zone_select", "question": "어느 존?",
        "options": [{"db_id": "polestar_cm_gp", "label": "김포"}]})
    client = FakeClient([question, _done()], log=log)
    (row,) = _run(tmp_path, _scenario({"status": "completed"}), client)
    assert log == ["anchor", "send", "send"], "앵커는 턴에 한 번 - 첫 송신 직전"
    assert row["anchor_at"] == "2026-09-30T23:59:59+09:00"
    assert row["auto_answers"], "역질문 자동 응답이 실제로 끼었다"


# --- J-3 환경 보류 출처 -----------------------------------------------------------

def test_환경_보류_턴의_출처는_env_mismatch_하나다(tmp_path: Path) -> None:
    (row,) = _run(tmp_path, _scenario({"status": "completed"}, env="sandbox"),
                  FakeClient([_done()]))
    assert row["manual_sources"] == ["env_mismatch"]
    assert row["manual_source"] == "env_mismatch"
    assert row["manual_notes"] == [
        "환경 불일치 - 시나리오 env=sandbox, 실행 env=closed. 데이터 의존 단언 1종 보류(status)"
    ], "문구는 종전 그대로다"


def test_원_문구가_있던_환경_보류_턴은_catalog_가_뒤에_남는다(tmp_path: Path) -> None:
    (row,) = _run(tmp_path, _scenario({"status": "completed", "manual_review": "원 문구"},
                                      env="sandbox"), FakeClient([_done()]))
    assert row["manual_sources"] == ["env_mismatch", "catalog"]
    assert row["manual_notes"][0].startswith("원 문구 / 환경 불일치 - ")


def test_환경이_맞는_턴은_출처를_손대지_않는다(tmp_path: Path) -> None:
    (row,) = _run(tmp_path, _scenario({"status": "completed", "manual_review": "원 문구"}),
                  FakeClient([_done()]))
    assert row["manual_sources"] == ["catalog"]


def test_mark_env_hold_는_env_mismatch_를_맨_앞에_두고_끼워_넣은_catalog_를_뺀다() -> None:
    verdict = Verdict(func="manual", manual_notes=["n"],
                      manual_sources=["unobservable", "catalog", "env_mismatch"])
    mark_env_hold(verdict, None)
    assert verdict.manual_sources == ["env_mismatch", "unobservable"]
    kept = Verdict(func="manual", manual_notes=["n"], manual_sources=["catalog", "env_mismatch"])
    mark_env_hold(kept, "원 문구")
    assert kept.manual_sources == ["env_mismatch", "catalog"]
    invalid = Verdict(func="invalid", manual_sources=[])
    mark_env_hold(invalid, None)
    assert invalid.manual_sources == [], "무효 턴은 출처를 비우는 계약이다"


# --- 모의 서버 download-csv ------------------------------------------------------

def _mock_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    from scripts.scenario import mockserver

    rows = [{"hostname": "a", "cpu": 1}, {"hostname": "b", "extra": "x"}]
    catalog = Catalog(groups={"T": Group("T", "T", 60000)}, profiles={"baseline": {}}, scenarios=[
        _scenario({}, id="T-01", turns=[Turn({"query": "행 있는 질의"}, {})],
                  mock={"turns": [{"response": "r", "query_results": rows}]}),
        _scenario({}, id="T-02", turns=[Turn({"query": "행 없는 질의"}, {})],
                  mock={"turns": [{"response": "r"}]}),
    ])
    monkeypatch.setattr(mockserver, "load_catalog", lambda: catalog)
    return mockserver.create_app()


def test_모의_서버는_query_results_를_실_서버와_같은_CSV_로_낸다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    with TestClient(_mock_app(monkeypatch)) as http:
        done = http.post("/api/v1/query", json={"query": "행 있는 질의", "thread_id": "t1"}).json()
        assert "query_results" not in done, "결과 행은 done 페이로드에 싣지 않는다"
        resp = http.get(f"/api/v1/query/{done['query_id']}/download-csv")
        assert resp.status_code == 200 and resp.content.startswith(BOM.encode("utf-8"))
        parsed = parse_result_csv(resp.content)
        assert parsed["columns"] == ["hostname", "cpu", "extra"]
        assert parsed["rows"] == [{"hostname": "a", "cpu": "1", "extra": ""},
                                  {"hostname": "b", "cpu": "", "extra": "x"}]

        empty = http.post("/api/v1/query", json={"query": "행 없는 질의", "thread_id": "t2"}).json()
        miss = http.get(f"/api/v1/query/{empty['query_id']}/download-csv")
        assert miss.status_code == 404
        assert miss.json()["detail"] == "다운로드할 조회 결과가 없습니다."
        unknown = http.get("/api/v1/query/없는-id/download-csv")
        assert unknown.status_code == 404 and unknown.json()["detail"] == "결과를 찾을 수 없습니다."


@pytest.mark.filterwarnings("ignore:You should not use the 'timeout' argument")
def test_클라이언트가_모의_서버의_두_404를_가른다(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    with TestClient(_mock_app(monkeypatch)) as http:
        client = ScenarioClient(ClientConfig(port=1))
        client._client.close()
        client._client = http
        done = http.post("/api/v1/query", json={"query": "행 있는 질의", "thread_id": "t1"}).json()
        assert client.download_csv(done["query_id"])["total_rows"] == 2
        empty = http.post("/api/v1/query", json={"query": "행 없는 질의", "thread_id": "t2"}).json()
        assert client.download_csv(empty["query_id"])["status"] == "empty"
        assert client.download_csv("없는-id")["status"] == "unavailable"


def test_결과_행_상한_상수는_계약값이다() -> None:
    assert client_mod.RESULT_ROWS_MAX == 5000
