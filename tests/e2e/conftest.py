"""E2E Playwright 테스트 공용 fixture.

테스트용 FastAPI 앱을 별도 포트(18980)에서 실행하고,
LLM/DB 호출은 MockGraph로 대체하여 외부 의존성 없이 테스트한다.
"""

from __future__ import annotations

import asyncio
import io
import time
from multiprocessing import Process
from typing import Any, AsyncGenerator, Optional

import pytest
from playwright.sync_api import Page

TEST_PORT = 18980
TEST_BASE_URL = f"http://localhost:{TEST_PORT}"


# ---------------------------------------------------------------------------
# MockGraph: LangGraph 그래프를 흉내내는 Mock 객체
# ---------------------------------------------------------------------------


class _MockChunk:
    """LLM 스트리밍 토큰을 흉내내는 객체."""

    def __init__(self, content: str) -> None:
        self.content = content


class MockGraph:
    """UI 테스트용 Mock LangGraph 그래프.

    query.py의 SSE 핸들러가 기대하는 이벤트 형식에 맞춰
    ainvoke, astream_events, get_state 메서드를 제공한다.
    """

    async def ainvoke(
        self, input_state: dict[str, Any], config: dict[str, Any]
    ) -> dict[str, Any]:
        """일반 POST 용 Mock 응답을 반환한다.

        Args:
            input_state: 에이전트 입력 상태
            config: LangGraph 설정

        Returns:
            에이전트 출력 상태 딕셔너리
        """
        from langchain_core.messages import HumanMessage

        query = input_state.get("user_query", "")
        has_file = input_state.get("uploaded_file") is not None

        result: dict[str, Any] = {
            "final_response": (
                f"'{query}'에 대한 조회 결과입니다.\n\n"
                "총 5건의 데이터가 조회되었습니다."
            ),
            "generated_sql": "SELECT * FROM servers LIMIT 5",
            "query_results": [
                {"id": i, "hostname": f"srv-{i}", "ip": f"10.0.0.{i}"}
                for i in range(1, 6)
            ],
            "messages": [HumanMessage(content=query)],
            "awaiting_approval": False,
            "output_file": None,
            "output_file_name": None,
            "mapping_report_md": None,
        }

        if has_file:
            result["output_file"] = _create_sample_xlsx_bytes()
            result["output_file_name"] = "result_20260324.xlsx"

        return result

    async def astream_events(
        self,
        input_state: dict[str, Any],
        config: dict[str, Any],
        version: str = "v2",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """SSE 스트리밍 Mock 이벤트를 생성한다.

        query.py의 event_generator가 처리하는 이벤트 형식:
        - on_chain_start + name -> node_start
        - on_chain_end + name + output(dict) -> node_complete
        - on_chat_model_stream + data.chunk.content -> token
        - on_chain_end + output에 final_response -> meta + done

        Args:
            input_state: 에이전트 입력 상태
            config: LangGraph 설정
            version: 이벤트 스트림 버전

        Yields:
            LangGraph astream_events 형식의 이벤트 딕셔너리
        """
        from langchain_core.messages import HumanMessage

        query = input_state.get("user_query", "")
        has_file = input_state.get("uploaded_file") is not None

        # 파이프라인 노드 목록과 각 노드의 mock output
        nodes = [
            ("input_parser", {
                "parsed_requirements": {"query_type": "data_query", "domain": "servers"},
            }),
            ("schema_analyzer", {
                "relevant_tables": ["servers"],
                "schema_info": {
                    "servers": {
                        "columns": [
                            {"name": "id"},
                            {"name": "hostname"},
                            {"name": "ip"},
                        ]
                    }
                },
            }),
            ("query_generator", {
                "generated_sql": "SELECT * FROM servers LIMIT 5",
            }),
            ("query_validator", {
                "validation_result": {"passed": True, "reason": ""},
            }),
            ("query_executor", {
                "query_results": [
                    {"id": i, "hostname": f"srv-{i}", "ip": f"10.0.0.{i}"}
                    for i in range(1, 6)
                ],
            }),
            ("result_organizer", {
                "organized_data": {
                    "summary": "5건 조회",
                    "rows": [{"id": i} for i in range(1, 6)],
                    "is_sufficient": True,
                },
            }),
            ("output_generator", {
                "status": "응답 생성 완료",
            }),
        ]

        # 각 노드에 대해 start/end 이벤트 생성
        for node_name, node_output in nodes:
            # on_chain_start
            yield {
                "event": "on_chain_start",
                "name": node_name,
                "data": {},
            }
            await asyncio.sleep(0.05)

            # on_chain_end (노드 완료 데이터)
            yield {
                "event": "on_chain_end",
                "name": node_name,
                "data": {"output": node_output},
            }
            await asyncio.sleep(0.05)

        # LLM 토큰 스트리밍 시뮬레이션
        response_text = (
            f"'{query}'에 대한 조회 결과입니다.\n\n"
            "총 5건의 데이터가 조회되었습니다."
        )
        tokens = _split_into_tokens(response_text)
        for token in tokens:
            yield {
                "event": "on_chat_model_stream",
                "name": "ChatModel",
                "data": {"chunk": _MockChunk(token)},
            }
            await asyncio.sleep(0.02)

        # 최종 출력 이벤트 (final_response 포함)
        final_output: dict[str, Any] = {
            "final_response": response_text,
            "generated_sql": "SELECT * FROM servers LIMIT 5",
            "query_results": [
                {"id": i, "hostname": f"srv-{i}", "ip": f"10.0.0.{i}"}
                for i in range(1, 6)
            ],
            "messages": [HumanMessage(content=query)],
            "awaiting_approval": False,
            "output_file": None,
            "output_file_name": None,
            "mapping_report_md": None,
        }

        if has_file:
            final_output["output_file"] = _create_sample_xlsx_bytes()
            final_output["output_file_name"] = "result_20260324.xlsx"

        yield {
            "event": "on_chain_end",
            "name": "__end__",
            "data": {"output": final_output},
        }

    def get_state(self, config: dict[str, Any]) -> None:
        """체크포인트 상태를 반환한다. 테스트에서는 항상 None."""
        return None


def _split_into_tokens(text: str) -> list[str]:
    """텍스트를 토큰 단위로 분할한다.

    한글은 글자 단위, 영어/숫자는 단어 단위로 분할하여
    실제 LLM 스트리밍과 유사한 출력을 시뮬레이션한다.

    Args:
        text: 분할할 텍스트

    Returns:
        토큰 문자열 리스트
    """
    tokens: list[str] = []
    current = ""
    for char in text:
        if char in ("\n", " ", ".", ",", "'", "\""):
            if current:
                tokens.append(current)
                current = ""
            tokens.append(char)
        else:
            current += char
            # 한글은 2글자씩 끊어서 전송
            if len(current) >= 2 and ord(char) >= 0xAC00:
                tokens.append(current)
                current = ""
    if current:
        tokens.append(current)
    return tokens


def _create_sample_xlsx_bytes() -> bytes:
    """Mock 응답용 간단한 Excel 파일 바이트를 생성한다.

    Returns:
        Excel 파일의 바이트 데이터
    """
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "결과"
    ws.append(["서버명", "IP주소", "CPU사용률"])
    for i in range(1, 6):
        ws.append([f"srv-{i}", f"10.0.0.{i}", f"{20 + i * 10}%"])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 테스트 설정 헬퍼
# ---------------------------------------------------------------------------


def _create_test_config() -> Any:
    """테스트용 AppConfig를 생성한다.

    Returns:
        테스트용 AppConfig 인스턴스
    """
    import os

    # 테스트 환경에서 불필요한 환경변수 충돌 방지
    os.environ.setdefault("DB_BACKEND", "direct")
    os.environ.setdefault("DB_CONNECTION_STRING", "")
    os.environ.setdefault("SCHEMA_CACHE_BACKEND", "file")
    os.environ.setdefault("SCHEMA_CACHE_ENABLED", "false")

    from src.config import AppConfig, ServerConfig

    config = AppConfig(
        db_backend="direct",
        db_connection_string="",
        log_level="WARNING",
        server=ServerConfig(
            host="0.0.0.0",
            port=TEST_PORT,
            cors_origins=["*"],
            query_timeout=60,
            file_query_timeout=120,
        ),
    )
    return config


# ---------------------------------------------------------------------------
# 테스트 서버 프로세스
# ---------------------------------------------------------------------------


def _run_test_server() -> None:
    """테스트용 서버를 실행한다.

    lifespan을 사용하지 않고 별도 FastAPI 앱을 구성하여
    MockGraph를 직접 주입한다.
    DB 의존성이 있는 health 라우트는 등록하지 않고
    간단한 health 엔드포인트를 직접 추가한다.
    """
    import uvicorn
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from pathlib import Path

    config = _create_test_config()

    # lifespan 없이 앱 생성
    app = FastAPI(title="E2E Test App")

    # Mock graph 주입
    app.state.config = config
    app.state.graph = MockGraph()

    # CORS 설정
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 간단한 health 엔드포인트 (DB 접속 불필요)
    @app.get("/api/v1/health")
    async def health_check() -> JSONResponse:
        """테스트용 헬스체크."""
        return JSONResponse({
            "status": "healthy",
            "version": "0.1.0-test",
            "db_connected": True,
            "timestamp": "2026-03-24T00:00:00",
        })

    # query 라우트 등록 (실제 프로덕션 라우트 재사용)
    from src.api.routes import query
    app.include_router(query.router, prefix="/api/v1")

    # 정적 파일 디렉토리
    static_dir = Path(__file__).resolve().parent.parent.parent / "src" / "static"

    # HTML 페이지 라우트
    @app.get("/", include_in_schema=False)
    async def user_page() -> FileResponse:
        """사용자 메인 화면."""
        return FileResponse(static_dir / "index.html")

    # 정적 파일 서빙
    if static_dir.exists():
        app.mount(
            "/static",
            StaticFiles(directory=str(static_dir)),
            name="static",
        )

    uvicorn.run(app, host="0.0.0.0", port=TEST_PORT, log_level="warning")


# ---------------------------------------------------------------------------
# Pytest Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def test_server() -> str:
    """테스트 서버를 세션 범위로 실행한다.

    multiprocessing.Process로 서버를 기동하고,
    health 엔드포인트 응답을 확인한 후 yield 한다.
    테스트 종료 시 서버 프로세스를 종료한다.

    Yields:
        테스트 서버 base URL (예: "http://localhost:18980")
    """
    import requests

    proc = Process(target=_run_test_server, daemon=True)
    proc.start()

    # 서버 준비 대기 (최대 15초)
    server_ready = False
    for _ in range(30):
        try:
            resp = requests.get(f"{TEST_BASE_URL}/api/v1/health", timeout=1)
            if resp.status_code == 200:
                server_ready = True
                break
        except Exception:
            pass
        time.sleep(0.5)

    if not server_ready:
        proc.terminate()
        proc.join(timeout=5)
        raise RuntimeError(
            f"테스트 서버가 {TEST_BASE_URL}에서 시작되지 않았습니다."
        )

    yield TEST_BASE_URL

    proc.terminate()
    proc.join(timeout=5)
    if proc.is_alive():
        proc.kill()
        proc.join(timeout=3)


@pytest.fixture()
def page(test_server: str, page: Page) -> Page:
    """각 테스트 전에 메인 페이지를 로드한다.

    pytest-playwright가 제공하는 page fixture를 받아서
    테스트 서버의 메인 페이지로 이동한 후 반환한다.

    Args:
        test_server: 테스트 서버 base URL
        page: pytest-playwright의 Page 인스턴스

    Returns:
        메인 페이지가 로드된 Page 인스턴스
    """
    page.goto(test_server)
    page.wait_for_load_state("networkidle")
    return page
