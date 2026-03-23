"""FastAPI 엔드포인트 테스트.

헬스체크, 질의 처리, 결과 조회 엔드포인트를 검증한다.
LangGraph 그래프 실행은 mock 처리한다.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from src.api.schemas import QueryRequest


@pytest.fixture
def test_client():
    """그래프 빌드를 mock하여 TestClient를 생성한다."""
    with patch("src.api.server.build_graph") as mock_build, \
         patch("src.api.server.setup_logging"), \
         patch("src.api.server.load_config") as mock_config:

        mock_config.return_value = MagicMock()
        mock_config.return_value.checkpoint_backend = "sqlite"
        mock_config.return_value.checkpoint_db_url = ":memory:"
        mock_config.return_value.server.query_timeout = 30.0

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={
            "final_response": "테스트 응답입니다.",
            "generated_sql": "SELECT * FROM servers LIMIT 10",
            "query_results": [{"hostname": "web-01"}],
            "output_file": None,
            "output_file_name": None,
        })
        mock_build.return_value = mock_graph

        from src.api.server import create_app
        app = create_app()
        app.state.graph = mock_graph
        app.state.config = mock_config.return_value

        with TestClient(app) as client:
            yield client


class TestHealthEndpoint:
    """GET /api/v1/health 엔드포인트 검증."""

    def test_health_returns_200(self, test_client):
        """헬스체크가 200을 반환한다."""
        with patch("src.api.routes.health.get_db_client") as mock_db:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=MagicMock(health_check=AsyncMock(return_value=False)))
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_db.return_value = mock_ctx

            response = test_client.get("/api/v1/health")
            assert response.status_code == 200

    def test_health_response_structure(self, test_client):
        """헬스체크 응답이 올바른 구조를 가진다."""
        with patch("src.api.routes.health.get_db_client") as mock_db:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=MagicMock(health_check=AsyncMock(return_value=False)))
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_db.return_value = mock_ctx

            response = test_client.get("/api/v1/health")
            data = response.json()
            assert "status" in data
            assert "version" in data
            assert "db_connected" in data
            assert "timestamp" in data


class TestQueryEndpoint:
    """POST /api/v1/query 엔드포인트 검증."""

    def test_valid_query_returns_200(self, test_client):
        """유효한 질의가 200을 반환한다."""
        response = test_client.post(
            "/api/v1/query",
            json={"query": "서버 목록 조회"},
        )
        assert response.status_code == 200

    def test_query_response_structure(self, test_client):
        """질의 응답이 올바른 구조를 가진다."""
        response = test_client.post(
            "/api/v1/query",
            json={"query": "CPU 사용률 조회"},
        )
        data = response.json()
        assert "query_id" in data
        assert "status" in data
        assert "response" in data
        assert data["status"] == "completed"

    def test_empty_query_returns_422(self, test_client):
        """빈 질의가 422를 반환한다."""
        response = test_client.post(
            "/api/v1/query",
            json={"query": ""},
        )
        assert response.status_code == 422

    def test_missing_query_returns_422(self, test_client):
        """query 필드 누락 시 422를 반환한다."""
        response = test_client.post(
            "/api/v1/query",
            json={},
        )
        assert response.status_code == 422


class TestQueryResultEndpoint:
    """GET /api/v1/query/{query_id}/result 엔드포인트 검증."""

    def test_nonexistent_query_returns_404(self, test_client):
        """존재하지 않는 query_id로 조회 시 404를 반환한다."""
        response = test_client.get("/api/v1/query/nonexistent-id/result")
        assert response.status_code == 404

    def test_existing_result_accessible(self, test_client):
        """질의 후 결과를 query_id로 조회할 수 있다."""
        # 먼저 질의 실행
        query_response = test_client.post(
            "/api/v1/query",
            json={"query": "서버 목록"},
        )
        query_id = query_response.json()["query_id"]

        # 결과 조회
        result_response = test_client.get(f"/api/v1/query/{query_id}/result")
        assert result_response.status_code == 200
        assert result_response.json()["query_id"] == query_id


class TestDownloadEndpoint:
    """GET /api/v1/query/{query_id}/download 엔드포인트 검증."""

    def test_nonexistent_query_returns_404(self, test_client):
        """존재하지 않는 query_id로 다운로드 시 404를 반환한다."""
        response = test_client.get("/api/v1/query/nonexistent/download")
        assert response.status_code == 404

    def test_no_file_returns_404(self, test_client):
        """파일이 없는 결과에 대해 다운로드 시 404를 반환한다."""
        # 먼저 질의 실행 (파일 없음)
        query_response = test_client.post(
            "/api/v1/query",
            json={"query": "서버 목록"},
        )
        query_id = query_response.json()["query_id"]

        response = test_client.get(f"/api/v1/query/{query_id}/download")
        assert response.status_code == 404


class TestQueryRequestValidation:
    """QueryRequest Pydantic 모델 검증."""

    def test_valid_request(self):
        req = QueryRequest(query="서버 목록 조회")
        assert req.query == "서버 목록 조회"
        assert req.output_format.value == "text"

    def test_max_length_enforcement(self):
        """query 길이 제한(2000자)이 적용된다."""
        with pytest.raises(Exception):
            QueryRequest(query="x" * 2001)

    def test_min_length_enforcement(self):
        """빈 문자열이 거부된다."""
        with pytest.raises(Exception):
            QueryRequest(query="")
