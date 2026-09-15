"""무과금 모의 서버 (plans/94 §4.4 2단 `--mock`).

**LLM도 DB도 부르지 않는다.** 러너·단언기·리포트·분석기의 배관이 실제로 도는지만
확인한다(V12). 네트워크 가드(tests/conftest.py:83) 아래에서도 통과해야 한다 -
접속 대상이 127.0.0.1 뿐이기 때문이다.

응답은 시나리오의 `mock:` 블록이 정한다. 블록이 없으면 일반 canned 응답이 나가고,
그 시나리오의 단언은 대개 깨진다 - **이는 정상이다.** 모의 실행의 기능 판정은 배관
검증용이며 시스템 품질의 근거가 아니다(리포트 1절이 이 사실을 명시한다).
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import FastAPI, File, Form, Request, UploadFile  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402

from scripts.scenario.catalog import Catalog, Scenario, load_catalog  # noqa: E402

_MOCK_NODES = ("field_mapper", "schema_analyzer", "query_generator", "query_executor",
               "result_organizer", "output_generator")


class _Resolver:
    """요청을 시나리오·턴으로 되짚는다.

    1턴은 질의 문자열로 시나리오를 찾고, 이후 턴은 thread_id 로 이어 센다.
    구조화 필드만 보내는 턴(selected_db_ids 등)에는 질의 문자열이 없기 때문이다.
    """

    def __init__(self, catalog: Optional[Catalog]) -> None:
        self._by_query: dict[str, Scenario] = {}
        self._threads: dict[str, tuple[str, int]] = {}
        # 역질문을 돌려준 뒤 답을 기다리는 스레드(D-216). 러너의 자동 응답은 시나리오 턴을
        # 넘기지 않으므로, 모의 턴에 `answer` 가 있으면 다음 요청을 같은 턴의 답으로 받는다.
        self._pending: set[str] = set()
        self._catalog = catalog
        if catalog is None:
            return
        for scenario in catalog.scenarios:
            first = scenario.turns[0].send.get("query") if scenario.turns else None
            if first:
                self._by_query.setdefault(str(first), scenario)

    def resolve(
        self, query: Optional[str], thread_id: Optional[str]
    ) -> tuple[Optional[Scenario], int, bool]:
        """(시나리오, 턴, 자동 응답 여부)를 돌려준다."""
        if thread_id and thread_id in self._threads:
            scenario_id, turn = self._threads[thread_id]
            scenario = self._catalog.by_id(scenario_id) if self._catalog else None
            if thread_id in self._pending and _mock_turn(scenario, turn).get("answer"):
                self._pending.discard(thread_id)
                return scenario, turn, True
            turn += 1
            self._threads[thread_id] = (scenario_id, turn)
            return scenario, turn, False
        scenario = self._by_query.get(str(query or ""))
        if scenario and thread_id:
            self._threads[thread_id] = (scenario.id, 1)
        return scenario, 1, False

    def settle(self, thread_id: Optional[str], payload: dict[str, Any]) -> None:
        """응답이 역질문이면 그 스레드를 답 대기로 표시한다."""
        if not thread_id:
            return
        if payload.get("clarification") or payload.get("form_fill_clarification") \
                or payload.get("awaiting_approval"):
            self._pending.add(thread_id)
        else:
            self._pending.discard(thread_id)


def _mock_turn(scenario: Optional[Scenario], turn: int) -> dict[str, Any]:
    """시나리오 `mock:` 블록에서 이 턴의 응답 정의를 꺼낸다. 없으면 빈 매핑."""
    if not (scenario and scenario.mock):
        return {}
    turns = scenario.mock.get("turns")
    if isinstance(turns, list):
        if len(turns) >= turn and isinstance(turns[turn - 1], dict):
            return turns[turn - 1]
        return {}
    return scenario.mock


def _payload_for(
    scenario: Optional[Scenario], turn: int, query: str, answered: bool = False
) -> dict[str, Any]:
    """done 이벤트 본문을 만든다. `answered` 면 그 턴의 `answer`(역질문에 답한 뒤 응답)를 쓴다."""
    base: dict[str, Any] = {
        "response": f"[mock] '{query}' 조회 결과 5건",
        "executed_sql": "SELECT hostname FROM cmm_resource LIMIT 5",
        "row_count": 5,
        "has_file": False,
        "processing_time_ms": 1200.0,
    }
    override = _mock_control(scenario, turn, answered)
    base.update({k: v for k, v in override.items() if k not in ("delay_ms", "http_status", "answer")})
    return base


def _mock_control(scenario: Optional[Scenario], turn: int, answered: bool = False) -> dict[str, Any]:
    """지연·HTTP 상태 같은 실행 제어값을 포함한 이 턴의 응답 정의."""
    definition = _mock_turn(scenario, turn)
    if answered:
        answer = definition.get("answer")
        return answer if isinstance(answer, dict) else {}
    return definition


def create_app() -> FastAPI:
    try:
        catalog = load_catalog()
    except Exception as exc:  # 카탈로그가 깨져도 모의 서버는 떠야 사유를 볼 수 있다
        print(f"[mock] 카탈로그 로드 실패 - canned 응답만 제공한다: {exc}")
        catalog = None

    app = FastAPI(title="scenario mock server")
    resolver = _Resolver(catalog)
    artifacts: dict[str, bytes] = {}

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy", "mode": "mock"}

    def _respond(
        body: dict[str, Any], scenario: Optional[Scenario], turn: int, answered: bool = False
    ) -> dict[str, Any]:
        payload = _payload_for(scenario, turn, str(body.get("query") or ""), answered)
        payload["query_id"] = str(uuid.uuid4())
        payload["thread_id"] = body.get("thread_id")
        # 받은 존 선택을 스코프로 돌려준다 - db_ids 단언이 러너가 실제로 보낸 값을 검증하게 된다.
        if body.get("selected_db_ids") and "db_scope" not in payload:
            payload["db_scope"] = {"db_ids": list(body["selected_db_ids"])}
        if payload.get("clarification"):
            payload["status"] = "clarification"
        else:
            payload.setdefault("status", "completed")
        if payload.get("has_file"):
            artifacts[payload["query_id"]] = _sample_xlsx(payload.get("file_columns"))
            payload.setdefault("file_name", "mock_result.xlsx")
        resolver.settle(body.get("thread_id"), payload)
        return payload

    @app.post("/api/v1/query")
    async def query(request: Request) -> JSONResponse:
        body = await request.json()
        scenario, turn, answered = resolver.resolve(body.get("query"), body.get("thread_id"))
        control = _mock_control(scenario, turn, answered)
        if control.get("delay_ms"):
            await asyncio.sleep(float(control["delay_ms"]) / 1000.0)
        status_code = int(control.get("http_status") or 200)
        payload = _respond(body, scenario, turn, answered)
        return JSONResponse(payload, status_code=status_code)

    @app.post("/api/v1/query/stream")
    async def query_stream(request: Request) -> StreamingResponse:
        body = await request.json()
        scenario, turn, answered = resolver.resolve(body.get("query"), body.get("thread_id"))
        control = _mock_control(scenario, turn, answered)
        payload = _respond(body, scenario, turn, answered)
        return StreamingResponse(
            _sse(payload, control), media_type="text/event-stream"
        )

    def _form_body(query: str, thread_id: Optional[str], selected_db_ids: Optional[str]) -> dict[str, Any]:
        ids = [d for d in (selected_db_ids or "").split(",") if d]
        return {"query": query, "thread_id": thread_id, "selected_db_ids": ids}

    @app.post("/api/v1/query/file")
    async def query_file(
        query: str = Form(...),
        file: UploadFile = File(...),
        thread_id: Optional[str] = Form(None),
        selected_db_ids: Optional[str] = Form(None),
    ) -> JSONResponse:
        await file.read()
        scenario, turn, answered = resolver.resolve(query, thread_id)
        control = _mock_control(scenario, turn, answered)
        payload = _respond(_form_body(query, thread_id, selected_db_ids), scenario, turn, answered)
        # 업로드 가드(형식·크기)의 4xx 도 모사한다 - 없으면 R2-08 의 400 기대가 모의에서 늘 깨진다.
        return JSONResponse(payload, status_code=int(control.get("http_status") or 200))

    @app.post("/api/v1/query/file/stream")
    async def query_file_stream(
        query: str = Form(...),
        file: UploadFile = File(...),
        thread_id: Optional[str] = Form(None),
        selected_db_ids: Optional[str] = Form(None),
    ) -> Any:
        await file.read()
        scenario, turn, answered = resolver.resolve(query, thread_id)
        control = _mock_control(scenario, turn, answered)
        payload = _respond(_form_body(query, thread_id, selected_db_ids), scenario, turn, answered)
        status_code = int(control.get("http_status") or 200)
        if status_code >= 400:
            return JSONResponse(payload, status_code=status_code)
        return StreamingResponse(_sse(payload, control), media_type="text/event-stream")

    @app.get("/api/v1/query/{query_id}/download")
    async def download(query_id: str) -> Any:
        from fastapi.responses import Response

        data = artifacts.get(query_id)
        if data is None:
            return JSONResponse({"detail": "not found"}, status_code=404)
        return Response(
            data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    return app


async def _sse(payload: dict[str, Any], control: dict[str, Any]):
    def event(data: dict[str, Any]) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    elapsed = 0.0
    step = float(control.get("delay_ms") or 300.0) / len(_MOCK_NODES)
    if payload.get("status") == "clarification":
        # 역질문은 파이프라인 실행 전에 done 으로 돌아온다(query.py:1311 pre-gate).
        yield event({"type": "done", **payload})
        return
    for node in _MOCK_NODES:
        yield event({"type": "node_start", "node": node, "timestamp_ms": elapsed})
        await asyncio.sleep(step / 1000.0)
        elapsed += step
        yield event({"type": "node_complete", "node": node, "data": {}, "timestamp_ms": elapsed})
    yield event({"type": "token", "content": payload.get("response", "")})
    yield event({"type": "done", **payload})


def _sample_xlsx(columns: Optional[list[str]] = None) -> bytes:
    """폼필 산출물 흉내. openpyxl 이 없으면 빈 바이트를 돌려준다(단언은 수동으로 빠진다)."""
    try:
        from io import BytesIO

        from openpyxl import Workbook
    except ImportError:
        return b""
    book = Workbook()
    sheet = book.active
    sheet.title = "Sheet1"
    header = columns or ["hostname", "os_type", "vendor"]
    sheet.append(header)
    for index in range(1, 6):
        sheet.append([f"srv-{index}", "Linux", "HPE"][: len(header)])
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def main() -> None:
    import uvicorn

    import os

    port = int(os.environ.get("API_PORT", "8060"))
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
