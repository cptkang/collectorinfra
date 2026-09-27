"""질의 결과·다운로드 API의 소유자 인가 · CSV 마스킹 · 다운로드 감사 (보안 결함 ③④).

- ③ `GET /query/{id}/result`·`/mapping-report`·`/download`·`/download-csv`,
  `POST /query/mapping-feedback`이 무인증이라 query_id만 알면 남의 결과·파일을 받았다.
  CSV는 화면(`result_organizer`의 `DataMasker`)과 달리 가리지 않은 원본 행이었다.
- ④ `GET /query/{id}/attachment`는 로그인만 보고 **그 질문을 한 사람인지**는 보지 않았다.

인증(`AUTH_ENABLED=true`)은 실제 `require_user`를 지나게 한다 — 의존성 대역을 쓰면 무인증
결함 자체가 가려진다. 익명 모드(`AUTH_ENABLED=false`) 동작은 종전과 같아야 한다.
"""

from __future__ import annotations

import ast
import csv
import io
import os
import time
from pathlib import Path

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
SECRET = "q34-test-user-secret-" + "x" * 40

_MAPPING_MD = (
    "## 매핑 결과 요약\n\n"
    "| # | 양식 필드 | 매핑 대상 | DB | 매핑 방법 | 신뢰도 |\n"
    "|---|---|---|---|---|---|\n"
    "| 1 | 서버명 | CMM_RESOURCE.HOSTNAME | polestar | synonym | - |\n"
)
_RAW_ROWS = [
    {"hostname": "web-01", "db_password": "p@ssw0rd-raw", "note": "ok"},
    {"hostname": "web-02", "db_password": "hunter2-raw", "note": "ok"},
]
_GET_ROUTES = ("result", "mapping-report", "download", "download-csv", "attachment")


def _final_output() -> dict:
    from langchain_core.messages import HumanMessage

    return {
        "final_response": "서버 2대입니다",
        "generated_sql": "SELECT 1",
        "query_results": [dict(r) for r in _RAW_ROWS],
        "output_file": b"PK\x03\x04generated-xlsx",
        "output_file_name": "result_20260927_000000.xlsx",
        "mapping_report_md": _MAPPING_MD,
        "messages": [HumanMessage(content="q")],
    }


class _Graph:
    def get_state(self, config: dict) -> None:
        return None

    async def ainvoke(self, input_state: dict, config: dict) -> dict:
        return _final_output()

    async def astream_events(self, input_state: dict, config: dict, version: str = "v2"):
        yield {"event": "on_chain_start", "name": "input_parser", "data": {}}
        yield {"event": "on_chain_end", "name": "LangGraph", "data": {"output": _final_output()}}


class _Audit:
    """`AuditService` 대역 — 호출만 기록한다(시그니처는 실물과 같은 키워드)."""

    def __init__(self) -> None:
        self.downloads: list[dict] = []

    async def log_user_request(self, **kwargs) -> None:
        return None

    async def log_file_download(
        self, user_id, file_name, file_type, file_size, client_ip=None, request_id=None
    ) -> None:
        self.downloads.append({
            "user_id": user_id, "file_name": file_name, "file_type": file_type,
            "file_size": file_size, "client_ip": client_ip, "request_id": request_id,
        })


def _config(*, auth_enabled: bool):
    os.environ.setdefault("SCHEMA_CACHE_ENABLED", "false")
    from src.config import AppConfig, AuthConfig, SecurityConfig, ServerConfig

    return AppConfig(
        db_backend="direct",
        db_connection_string="",
        log_level="WARNING",
        server=ServerConfig(query_timeout=60, file_query_timeout=120),
        auth=AuthConfig(enabled=auth_enabled, jwt_secret=SECRET),
        # 마스킹 규칙은 검증 대상이라 명시한다(.env 누수 차단)
        security=SecurityConfig(
            sensitive_columns=["password", "token"],
            mask_pattern="***MASKED***",
            mask_ip=False,
            mask_email=False,
        ),
    )


def _client(*, auth_enabled: bool = True, audit: _Audit | None = None) -> TestClient:
    from src.api.routes import query as query_routes

    app = FastAPI()
    app.state.config = _config(auth_enabled=auth_enabled)
    app.state.graph = _Graph()
    if audit is not None:
        app.state.audit_service = audit
    app.include_router(query_routes.router, prefix="/api/v1")
    return TestClient(app)


def _auth(sub: str, role: str = "user") -> dict:
    token = jwt.encode(
        {"sub": sub, "name": sub, "role": role, "type": "user", "exp": int(time.time()) + 3600},
        SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _ask(client: TestClient, headers: dict, route: str = "/query") -> str:
    """질의를 실제 라우트로 보내 결과를 저장시키고 query_id를 돌려준다."""
    import json

    if route.startswith("/query/file"):
        # 존을 지정해 존 역질문(조기 반환)을 건너뛴다 — 결과 저장 지점까지 가야 한다
        r = client.post(
            "/api/v1" + route,
            data={"query": "양식 채워줘", "thread_id": "th-file", "selected_db_ids": "polestar"},
            files={"file": ("form.xlsx", io.BytesIO(b"PK\x03\x04upload-original"),
                            "application/octet-stream")},
            headers=headers,
        )
    else:
        r = client.post("/api/v1" + route, json={"query": "서버 목록"}, headers=headers)
    assert r.status_code == 200, r.text
    if not route.endswith("/stream"):
        return r.json()["query_id"]
    for line in r.text.splitlines():
        if line.startswith("data: "):
            ev = json.loads(line[6:])
            if ev.get("type") == "done":
                return ev["query_id"]
    raise AssertionError(f"done 이벤트가 없다: {r.text[:500]}")


def _url(qid: str, route: str) -> str:
    return f"/api/v1/query/{qid}/{route}"


# ---------------------------------------------------------------------------
# ③ 무인증 · 타인 접근
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", ["result", "mapping-report", "download", "download-csv"])
def test_result_routes_reject_first_visitor_without_login(route: str) -> None:
    """로그인하지 않은 첫 방문자는 query_id를 알아도 받지 못한다(수정 전 200)."""
    client = _client()
    qid = _ask(client, _auth("alice"))
    r = client.get(_url(qid, route))
    assert r.status_code == 401, (route, r.status_code, r.text[:200])


@pytest.mark.parametrize("route", ["result", "mapping-report", "download", "download-csv"])
def test_other_user_cannot_read_someone_elses_result(route: str) -> None:
    """남(bob)은 alice의 query_id로 결과·파일을 받지 못한다(수정 전 200)."""
    client = _client()
    qid = _ask(client, _auth("alice"))
    r = client.get(_url(qid, route), headers=_auth("bob"))
    assert r.status_code == 403, (route, r.status_code, r.text[:200])
    assert "p@ssw0rd-raw" not in r.text


@pytest.mark.parametrize("route", ["result", "mapping-report", "download", "download-csv"])
def test_owner_and_admin_can_read(route: str) -> None:
    client = _client()
    qid = _ask(client, _auth("alice"))
    assert client.get(_url(qid, route), headers=_auth("alice")).status_code == 200
    assert client.get(_url(qid, route), headers=_auth("root", role="admin")).status_code == 200


def test_stream_route_records_owner() -> None:
    """SSE 경로에서 저장한 결과도 소유자가 붙는다(진입점 대칭)."""
    client = _client()
    qid = _ask(client, _auth("alice"), route="/query/stream")
    assert client.get(_url(qid, "download-csv"), headers=_auth("bob")).status_code == 403
    assert client.get(_url(qid, "download-csv"), headers=_auth("alice")).status_code == 200


def test_unknown_query_id_is_still_404() -> None:
    client = _client()
    r = client.get(_url("no-such-id", "result"), headers=_auth("alice"))
    assert r.status_code == 404


def test_mapping_feedback_requires_login_and_ownership() -> None:
    """피드백 업로드(동의어 반영)도 소유자만 — 수정 전에는 무인증·타인 모두 200."""
    client = _client()
    qid = _ask(client, _auth("alice"))

    def _post(headers: dict | None):
        return client.post(
            "/api/v1/query/mapping-feedback",
            data={"query_id": qid},
            files={"file": ("report.md", io.BytesIO(_MAPPING_MD.encode("utf-8")), "text/markdown")},
            headers=headers or {},
        )

    assert _post(None).status_code == 401
    assert _post(_auth("bob")).status_code == 403
    ok = _post(_auth("alice"))
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "no_changes"


# ---------------------------------------------------------------------------
# ④ 첨부 원본 — 로그인만이 아니라 질의한 사람인지
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", ["/query/file", "/query/file/stream"])
def test_attachment_is_owner_only(route: str) -> None:
    """남은 로그인만으로 원본을 받지 못한다(수정 전 bob 200). 파일 경로 두 진입점 모두."""
    client = _client()
    qid = _ask(client, _auth("alice"), route=route)
    assert client.get(_url(qid, "attachment")).status_code == 401
    assert client.get(_url(qid, "attachment"), headers=_auth("bob")).status_code == 403
    r = client.get(_url(qid, "attachment"), headers=_auth("alice"))
    assert r.status_code == 200
    assert r.content == b"PK\x03\x04upload-original"
    admin = client.get(_url(qid, "attachment"), headers=_auth("root", role="admin"))
    assert admin.status_code == 200


def test_every_store_call_names_the_owner() -> None:
    """`_store_result` 호출 전부가 소유자를 넘긴다 — 한 진입점만 빠지는 비대칭 방지."""
    tree = ast.parse((ROOT / "src/api/routes/query.py").read_text(encoding="utf-8"))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_store_result"
    ]
    assert len(calls) >= 5, "결과 저장 지점이 줄었다 — 이 단언을 실측으로 갱신할 것"
    for call in calls:
        assert any(kw.arg == "owner" for kw in call.keywords), (
            f"query.py:{call.lineno} — _store_result에 owner가 없다"
        )


# ---------------------------------------------------------------------------
# 익명 모드(AUTH_ENABLED=false) — 종전 동작
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", ["result", "mapping-report", "download", "download-csv"])
def test_anonymous_mode_is_unchanged(route: str) -> None:
    client = _client(auth_enabled=False)
    qid = _ask(client, {})
    assert client.get(_url(qid, route)).status_code == 200


# ---------------------------------------------------------------------------
# CSV 마스킹 — 화면과 같은 규칙
# ---------------------------------------------------------------------------


def test_csv_masks_rows_like_the_screen() -> None:
    """CSV는 화면에 적용되는 `DataMasker`와 같은 규칙으로 가린다(수정 전 원문 노출)."""
    from src.security.data_masker import DataMasker

    client = _client()
    qid = _ask(client, _auth("alice"))
    r = client.get(_url(qid, "download-csv"), headers=_auth("alice"))
    assert r.status_code == 200
    body = r.content.decode("utf-8-sig")
    assert "p@ssw0rd-raw" not in body and "hunter2-raw" not in body

    expected = DataMasker(_config(auth_enabled=True).security).mask_rows(_RAW_ROWS)
    got = list(csv.DictReader(io.StringIO(body)))
    assert got == [{k: str(v) for k, v in row.items()} for row in expected]


# ---------------------------------------------------------------------------
# 다운로드 감사
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "route,file_type",
    [("download", "xlsx"), ("download-csv", "csv"), ("mapping-report", "md")],
)
def test_download_is_audited(route: str, file_type: str) -> None:
    audit = _Audit()
    client = _client(audit=audit)
    qid = _ask(client, _auth("alice"))

    assert client.get(_url(qid, route), headers=_auth("bob")).status_code == 403
    assert audit.downloads == [], "거부된 요청을 다운로드로 기록하면 안 된다"

    r = client.get(_url(qid, route), headers=_auth("alice"))
    assert r.status_code == 200
    assert len(audit.downloads) == 1
    entry = audit.downloads[0]
    assert entry["user_id"] == "alice"
    assert entry["file_type"] == file_type
    assert entry["file_size"] == len(r.content)
    assert entry["file_name"]


def test_attachment_download_is_audited() -> None:
    audit = _Audit()
    client = _client(audit=audit)
    qid = _ask(client, _auth("alice"), route="/query/file")
    r = client.get(_url(qid, "attachment"), headers=_auth("alice"))
    assert r.status_code == 200
    assert audit.downloads == [{
        "user_id": "alice", "file_name": "form.xlsx", "file_type": "xlsx",
        "file_size": len(r.content), "client_ip": audit.downloads[0]["client_ip"],
        "request_id": audit.downloads[0]["request_id"],
    }]


# ---------------------------------------------------------------------------
# 화면 — 토큰을 헤더로 · URL에 싣지 않음 · 401은 재로그인
# ---------------------------------------------------------------------------


def _app_js() -> str:
    return (ROOT / "src/static/js/app.js").read_text(encoding="utf-8")


def test_ui_downloads_go_through_authorized_fetch() -> None:
    js = _app_js()
    assert "function downloadWithAuth(" in js, "다운로드 링크를 헤더 인증 fetch로 받는 함수가 없다"
    body = js.split("function downloadWithAuth(", 1)[1].split("\n    }\n", 1)[0]
    assert "getAuthHeaders()" in body
    assert "redirectToLogin()" in body, "만료(401) 시 기존 재로그인 흐름을 따르지 않는다"
    assert "URL.createObjectURL" in body
    # 채팅 영역의 결과·CSV·보고서·원본 링크 클릭을 가로챈다
    assert 'closest("a.message-download, a.message-file-card")' in js


def test_ui_never_puts_token_in_url() -> None:
    js = _app_js()
    for bad in ("?token=", "&token=", "token=\" +", "token=' +"):
        assert bad not in js, f"토큰을 URL에 싣는 코드가 있다: {bad}"


def test_ui_mapping_feedback_handles_expired_login() -> None:
    js = _app_js()
    body = js.split("window.handleMappingFeedbackUpload = async function", 1)[1]
    body = body.split("\n    };\n", 1)[0]
    assert "getAuthHeaders()" in body
    assert "response.status === 401" in body and "redirectToLogin()" in body


def test_app_js_cache_version_bumped() -> None:
    import re

    html = (ROOT / "src/static/index.html").read_text(encoding="utf-8")
    m = re.search(r"app\.js\?v=(\d+)", html)
    assert m and int(m.group(1)) >= 13
