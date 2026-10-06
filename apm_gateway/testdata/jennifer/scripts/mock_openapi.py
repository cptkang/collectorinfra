"""제니퍼 Open API 목(mock) 서버 (plans/87 §0.8 (6) 폴백 · J0-L-a ⑤).

녹화 fixture(`recorded/<source>/`)를 응답 원천으로 쓰고, 로컬 실측(§0.10)에서 확인한 실서버
동작을 흉내 낸다:
- 인증: Bearer 토큰 · 쿼리 `?token=`도 받는다(실서버와 같음 — 게이트웨이가 거부해야 한다)
  · 없으면 401
- 필수 파라미터 누락 → 500 `{"exception":{"message":"Required request parameter ..."}}`
- `profile.txt`에 `Accept: application/json` → 404
- `--mode disconnected` → 인스턴스 단위 API 500 "Domain is not connected"
- 허용목록 밖 경로(민감 GET·쓰기 경로·`.xml`·POST 변형)도 존재하는 것처럼 응답하고
  **접근을 기록**한다
  → 게이트웨이 테스트는 `/__mock/hits`로 "허용목록 밖 호출 0회"를 단언한다.
- 접근 기록의 `bearer_fp`는 요청 Bearer 값의 sha256 앞 12자리다(값 자체는 남기지 않는다) — 다중 소스
  테스트가 "소스 A 토큰이 소스 B 요청에 0회"를 단언한다(plans/87 J8 M-10).
- 접근 기록의 `query`는 쿼리 키·값이다(`token` 값은 `***`) — 계약 테스트가 실제로 넘긴 인자 값을
  단언한다(plans/134 W1 `T-COV-*-args`).
- `/api/dbsearch/error`는 `error_type`(대문자 비교)으로, `/api/dbsearch/event`는 `level`(대소문자
  무시 정확 일치)로 걸러 돌려준다 — 실서버 의미는 W10 확인 전 가정이다.
- `/api/status/*`는 `max_row`만큼 앞 행을 돌려주고, `/api-v2/deploy/<도메인ID>`(맨 배열 응답)는
  `collectTime`이 `startTime`~`endTime`(양 끝 포함)인 항목만 돌려준다(plans/134 W2 — 정렬·패턴
  검색·25시간 초과 처리는 흉내 내지 않는다 · 실서버 동작은 W10 확인 전 가정).
- `/api/transaction/guid`(plans/134 W5)는 픽스처의 거래 중 `domainId`·`guid`가 쿼리와 같고 구간
  (`startTime`~`endTime`)이 `start_time`~`end_time`과 겹치는 것만 돌려준다 — 여러 도메인에 같은 GUID
  거래를 두는 합성 응답이다. `/__mock/guid_fail_domains`로 고른 도메인만 500 "Domain is not
  connected"로 답하게 할 수 있다(일부 도메인 실패 · 실서버 의미는 W10 확인 전 가정).

한계: 실제 EVENT 발생·필드 변형은 재현하지 못한다. 이벤트는 `/__mock/events`로 주입한다.

제어 경로(인증 없음 · 127.0.0.1 전용):
    GET  /__mock/hits     접근 기록        POST /__mock/reset   기록·주입 이벤트·사용량 초기화
    POST /__mock/events   EventData 목록 주입(13필드 부분집합 · time 필수)
    GET  /__mock/usage    토큰 사용량
    POST /__mock/mode     {"mode": "fixtures|connected|disconnected"}
    POST /__mock/guid_fail_domains  {"domains": [2000]} — GUID 조회만 그 도메인을 실패시킨다

사용:
    python mock_openapi.py --fixtures ../recorded/local-docker --port 17901 --token mock-token
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jennifer_catalog import (  # noqa: E402
    ALLOWED,
    EVENT_FIELDS,
    SENSITIVE_GET,
    SENSITIVE_GET_DOMAIN_PREFIX,
    WRITE_PATHS_ON_GET,
    match_template,
    missing_param_message,
)

MODES = ("fixtures", "connected", "disconnected")
NOT_FOUND_HTML = "<!DOCTYPE html><html><head><title>JENNIFER5</title></head><body>404</body></html>"


def bearer_fingerprint(token: str) -> str:
    """접근 기록용 Bearer 지문(sha256 앞 12자리)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


class MockState:
    def __init__(self, fixtures_dir: Path | None, token: str, mode: str = "fixtures") -> None:
        if mode not in MODES:
            raise ValueError(mode)
        self.token = token
        self.mode = mode
        self.fixtures: dict[str, list[dict]] = {}
        self.events: list[dict] = []
        self.hits: list[dict] = []
        self.usage = 0
        self.guid_fail_domains: set[str] = set()
        self.lock = threading.Lock()
        if fixtures_dir is not None:
            self.load(fixtures_dir)

    def load(self, fixtures_dir: Path) -> None:
        for f in sorted(Path(fixtures_dir).glob("*.json")):
            if f.name == "index.json":
                continue
            fx = json.loads(f.read_text(encoding="utf-8"))
            self.fixtures.setdefault(fx["request"]["template"], []).append(fx)

    @property
    def domain_connected(self) -> bool:
        """fixtures 모드는 녹화 상태를 따른다.

        도메인 필수 API에 ok fixture가 있으면 연결로 본다.
        """
        if self.mode != "fixtures":
            return self.mode == "connected"
        return any(
            fx["variant"] == "ok"
            for tpl, fxs in self.fixtures.items()
            if ALLOWED[tpl].needs_domain and not ALLOWED[tpl].empty_when_disconnected
            for fx in fxs
        )

    def pick(self, template: str, query: dict[str, str]) -> dict | None:
        cands = self.fixtures.get(template, [])
        if self.mode == "connected":
            cands = [c for c in cands if c["variant"] == "ok"]
        if not cands:
            return None
        metric = query.get("metrics")
        return (
            next(
                (c for c in cands if metric and c.get("label") == metric and c["variant"] == "ok"),
                None,
            )
            or next((c for c in cands if c["variant"] == "ok"), None)
            or cands[0]
        )


class Handler(BaseHTTPRequestHandler):
    state: MockState  # make_server가 주입한다
    server_version = "JenniferMock/1"

    def log_message(self, fmt: str, *args: Any) -> None:  # 표준 오류 출력 억제
        pass

    # --- 응답 헬퍼 ---
    def _send(self, status: int, body: Any, ctype: str = "application/json") -> int:
        raw = (
            body
            if isinstance(body, bytes)
            else (
                json.dumps(body, ensure_ascii=False).encode()
                if ctype.startswith("application/json")
                else str(body).encode()
            )
        )
        self.send_response(status)
        self.send_header("Content-Type", ctype + ("" if "charset" in ctype else ";charset=utf-8"))
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
        return status

    def _record(
        self,
        method: str,
        path: str,
        query: dict,
        template: str | None,
        allowlisted: bool,
        status: int,
        query_token: bool,
    ) -> None:
        auth = self.headers.get("Authorization", "")
        bearer = auth[len("Bearer ") :] if auth.startswith("Bearer ") else ""
        with self.state.lock:
            self.state.hits.append(
                {
                    "ts": time.time(),
                    "method": method,
                    "path": path,
                    "template": template,
                    "allowlisted": allowlisted,
                    "query_keys": sorted(query),
                    "query": {k: ("***" if k.lower() == "token" else v) for k, v in query.items()},
                    "query_token": query_token,
                    "bearer_fp": bearer_fingerprint(bearer) if bearer else "",
                    "status": status,
                }
            )

    def _body_json(self) -> Any:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"null") if n else None

    # --- 제어 경로 ---
    def _control(self, method: str, path: str) -> int:
        st = self.state
        if method == "GET" and path == "/__mock/hits":
            return self._send(200, st.hits)
        if method == "GET" and path == "/__mock/usage":
            return self._send(200, {"usage": st.usage})
        if method == "POST" and path == "/__mock/reset":
            with st.lock:
                st.hits.clear()
                st.events.clear()
                st.usage = 0
                st.guid_fail_domains.clear()
            return self._send(200, {"reset": True})
        if method == "POST" and path == "/__mock/mode":
            mode = (self._body_json() or {}).get("mode")
            if mode not in MODES:
                return self._send(400, {"error": f"mode는 {MODES} 중 하나"})
            st.mode = mode
            return self._send(200, {"mode": mode})
        if method == "POST" and path == "/__mock/guid_fail_domains":
            domains = (self._body_json() or {}).get("domains")
            if not isinstance(domains, list):
                return self._send(400, {"error": "domains는 도메인 ID 목록이어야 한다"})
            with st.lock:
                st.guid_fail_domains = {str(d) for d in domains}
            return self._send(200, {"guid_fail_domains": sorted(st.guid_fail_domains)})
        if method == "POST" and path == "/__mock/events":
            events = self._body_json()
            if not isinstance(events, list):
                return self._send(400, {"error": "EventData 목록(JSON 배열)이어야 한다"})
            for e in events:
                extra = set(e) - set(EVENT_FIELDS)
                if extra or "time" not in e:
                    return self._send(
                        400, {"error": f"허용 필드 밖 {sorted(extra)} 또는 time 누락"}
                    )
            with st.lock:
                st.events.extend({**e, "time": str(e["time"])} for e in events)
            return self._send(200, {"injected": len(events)})
        return self._send(404, {"error": "unknown control path"})

    # --- 본 처리 ---
    def _dispatch(self, method: str) -> None:
        split = urllib.parse.urlsplit(self.path)
        path = split.path
        query = {
            k: v[-1] for k, v in urllib.parse.parse_qs(split.query, keep_blank_values=True).items()
        }
        if path.startswith("/__mock/"):
            self._control(method, path)
            return
        st = self.state
        auth = self.headers.get("Authorization", "")
        query_token = "token" in query
        authed = auth == f"Bearer {st.token}" or query.get("token") == st.token
        template = match_template(path)
        allowlisted = method == "GET" and template is not None and not query_token
        if not authed:
            self._record(
                method,
                path,
                query,
                template,
                allowlisted,
                self._send(401, "401 unauthorized"),
                query_token,
            )
            return
        with st.lock:
            st.usage += 1
        status = self._route(method, path, query, template)
        self._record(method, path, query, template, allowlisted, status, query_token)

    def _route(self, method: str, path: str, query: dict[str, str], template: str | None) -> int:
        if path == "/api-v2/auth-test":
            return self._send(200, "OK")
        if template in W7_TEMPLATES:  # plans/134 W7 관리·민감 조회 합성 응답(파일 끝)
            return self._send(*w7_response(self.state, method, template, query, path))
        if template is not None and method in ("GET", "POST"):
            return self._allowed(template, query, path)
        if method == "GET" and path.endswith(".xml"):
            return self._xml_variant(path[:-4], query)
        if method == "GET" and path in SENSITIVE_GET:
            return self._send(200, SENSITIVE_GET[path])
        if method == "GET" and path.startswith(SENSITIVE_GET_DOMAIN_PREFIX):
            return (
                self._send(200, {"result": []})
                if self.state.domain_connected
                else self._send(500, "")
            )
        if path in WRITE_PATHS_ON_GET:
            if method == "GET":
                code, body = WRITE_PATHS_ON_GET[path]
                return self._send(code, body)
            return self._send(403, "mock refuses write")
        return self._send(404, NOT_FOUND_HTML, "text/html")

    def _xml_variant(self, base_path: str, query: dict[str, str]) -> int:
        """`.xml` 변형 — JSON 경로와 달리 도메인 미연결이면 빈 결과 경로도 500이다(실측)."""
        if base_path == "/api/auth/userlist":
            return self._send(
                200,
                "<result><item><id>canary</id><email>canary@example.invalid</email></item></result>\n",
                "application/xml",
            )
        template = match_template(base_path)
        if template is None:
            return self._send(404, NOT_FOUND_HTML, "text/html")
        if ALLOWED[template].needs_domain and not self.state.domain_connected:
            msg = f"{query.get('domain_id', '')} Domain is not connected"
            return self._send(
                500, f"<exception>\n    <message>{msg}</message>\n</exception>\n", "application/xml"
            )
        return self._send(200, "<result></result>\n", "application/xml")

    def _allowed(self, template: str, query: dict[str, str], path: str) -> int:
        ep = ALLOWED[template]
        accept = self.headers.get("Accept", "*/*")
        if (
            ep.accept == "text/plain"
            and "application/json" in accept
            and "*/*" not in accept
            and "text/plain" not in accept
        ):
            return self._send(404, NOT_FOUND_HTML, "text/html")
        for name in ep.required:
            if name not in query:
                return self._send(500, {"exception": {"message": missing_param_message(name)}})
        domain = query.get("domain_id") or path.rsplit("/", 1)[-1]
        if self.state.mode == "disconnected" and ep.needs_domain:
            if ep.empty_when_disconnected:
                return self._send(200, {"result": []})
            msg = f"{domain} Domain is not connected"
            if template.startswith("/api-v2/"):
                return self._send(
                    500, f"500 500 com.aries.view.core.nio.DataServerDownException: {msg}"
                )
            return self._send(500, {"exception": {"message": msg}})
        fx = self.state.pick(template, query)
        if template == "/api/dbsearch/event":
            return self._events(fx, query)
        if template == "/api/transaction/guid":
            return self._guid(fx, query)
        if template == "/api/dbsearch/error" and fx is not None and query.get("error_type"):
            wanted = query["error_type"].upper()
            rows = (fx["response"].get("body_json") or {}).get("result") or []
            kept = [r for r in rows if str(r.get("errorType") or "").upper() == wanted]
            return self._send(fx["response"]["status"], {"result": kept})
        if fx is None:
            return self._send(
                501, {"mock_error": f"fixture 없음: {template} (mode={self.state.mode})"}
            )
        resp = fx["response"]
        ctype = resp.get("content_type") or "application/json"
        body = resp["body_json"] if "body_json" in resp else resp.get("body_text", "")
        if resp["status"] == 200:
            body = self._narrow(template, query, body)
        return self._send(resp["status"], body, ctype.split(";")[0])

    @staticmethod
    def _narrow(template: str, query: dict[str, str], body: Any) -> Any:
        """선택 인자 흉내 — 통계 `max_row` · 변경 이력 구간(plans/134 W2)."""
        if template.startswith("/api/status/") and query.get("max_row", "").isdigit():
            rows = (body or {}).get("result") or []
            return {**body, "result": rows[: int(query["max_row"])]}
        if template == "/api-v2/deploy/{domainId}" and isinstance(body, list):
            lo, hi = int(query["startTime"]), int(query["endTime"])
            return [r for r in body if lo <= int(r.get("collectTime") or 0) <= hi]
        return body

    def _guid(self, fx: dict[str, Any] | None, query: dict[str, str]) -> int:
        """GUID 거래 흉내 — 도메인·GUID 일치 + 구간 겹침만(plans/134 W5 · 합성)."""
        domain = query["domain_id"]
        if domain in self.state.guid_fail_domains:
            return self._send(500, {"exception": {"message": f"{domain} Domain is not connected"}})
        if fx is None:
            return self._send(501, {"mock_error": "fixture 없음: /api/transaction/guid"})
        try:
            lo, hi = int(query["start_time"]), int(query["end_time"])
        except ValueError:
            return self._send(
                500, {"exception": {"message": "For input string: start_time/end_time"}}
            )
        rows = [
            r
            for r in (fx["response"].get("body_json") or {}).get("result") or []
            if str(r.get("domainId")) == domain
            and str(r.get("guid") or "") == query["guid"]
            and int(r.get("startTime") or 0) <= hi
            and int(r.get("endTime") or r.get("startTime") or 0) >= lo
        ]
        return self._send(fx["response"]["status"], {"result": rows})

    def _events(self, fx: dict | None, query: dict[str, str]) -> int:
        base: list = []
        if fx is not None:
            if fx["variant"] != "ok":
                resp = fx["response"]
                return self._send(resp["status"], resp.get("body_json", resp.get("body_text", "")))
            base = list((fx["response"].get("body_json") or {}).get("result") or [])
        try:
            lo, hi = int(query["start_time"]), int(query["end_time"])
        except ValueError:
            return self._send(
                500, {"exception": {"message": "For input string: start_time/end_time"}}
            )
        with self.state.lock:
            injected = [e for e in self.state.events if lo <= int(e["time"]) <= hi]
        rows = base + injected
        if query.get("level"):
            wanted = query["level"].upper()
            rows = [r for r in rows if str(r.get("eventLevel") or "").upper() == wanted]
        return self._send(200, {"result": rows})

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")


# ── plans/134 W7 — 관리·민감 조회 합성 응답 ────────────────────────────────────
# 스펙 5.6.4 인라인 스키마·v2 매뉴얼 응답 예의 필드명으로 만든 **합성** 응답이다(실응답 모양은
# W10). v2는 `{result: …}` 봉투 없이 맨 배열·객체·불리언을 돌려준다(COV E-18). 비밀 자리에는
# 카나리아(`W7Mock.secret`)를 넣어 게이트웨이 자격증명 경계를 시험한다. 테스트는
# `w7_state(state)`의 칸을 바꿔 404(개별 설정 없음 E-19 · 버전 미지원 E-28)·`compare`/
# `comparing` 표기(E-01)·모양 위반을 흉내 낸다. `w7_response`는 순수 함수라
# `httpx.MockTransport` 테스트도 같은 본문을 쓴다.

W7_TEMPLATES: frozenset[str] = frozenset(
    {
        "/api/auth/userlist",
        "/restapi/users",
        "/restapi/user/{id}",
        "/api-v2/manage/data-server/domains",
        "/api-v2/manage/data-server/resource",
        "/api-v2/manage/data-server/system-property-config",
        "/api-v2/manage/rule/active-service-color-range-boundary",
        "/api-v2/active-service/detail/{domainId}/{txid}",
        "/api-v2/manage/db/path/{domainId}",
        "/api-v2/environment-variable/{domainId}",
        "/api-v2/manage/instance",
        "/api-v2/loaded-class/{domainId}/{instanceId}",
        "/api-v2/manage/rule/event/error/{domainId}",
        "/api-v2/manage/rule/event/metric/{domainId}/{targetType}",
        "/api-v2/manage/rule/event/compare/{domainId}/{targetType}",
        "/api-v2/manage/rule/event/comparing/{domainId}/{targetType}",
        "/api-v2/manage/rule/event/error/{domainId}/{errorType}/applied",
        "/api-v2/manage/rule/event/error/{domainId}/{errorType}/individual-setting/{instanceId}",
        "/api-v2/manual-rdb-export",
    }
)
W7_SECRET = "CANARY-w7-mock-9Qx"
# 프로세스 ID → 인스턴스가 있는 PID(그 밖 PID는 `{}`)
W7_PROCESS_ID = "4242"


class W7Mock:
    """W7 경로 합성 응답 상태(토글) — 목 서버는 `state.w7`에 둔다."""

    def __init__(self, secret: str = W7_SECRET) -> None:
        self.secret = secret
        # True면 `compare` 표기는 404 · `comparing`만 답한다(COV E-01)
        self.compare_404 = False
        # "<domainId>/<errorType>/<instanceId>" → 개별 설정 값 · 없는 키는 404(설정 없음 · E-19)
        self.individual: dict[str, bool] = {}
        # 템플릿 → 404(이 버전이 경로를 지원하지 않는 흉내 · E-28)
        self.not_found: set[str] = set()
        # 템플릿 → (상태, 본문) 덮어쓰기(모양 위반·서버 오류 시험)
        self.bodies: dict[str, tuple[int, Any]] = {}


def w7_state(state: Any) -> W7Mock:
    mock = getattr(state, "w7", None)
    if mock is None:
        mock = W7Mock()
        state.w7 = mock
    return mock


def _w7_vars(template: str, path: str) -> dict[str, str]:
    return {t[1:-1]: p for t, p in zip(template.split("/"), path.split("/")) if t.startswith("{")}


def w7_body(mock: W7Mock, template: str, query: dict[str, str], path: str) -> tuple[int, Any]:
    """W7 경로의 (상태, 본문) — 404는 본문 None."""
    s = mock.secret
    v = _w7_vars(template, path)
    if template in mock.bodies:
        return mock.bodies[template]
    if template in mock.not_found:
        return 404, None
    if template.endswith("/compare/{domainId}/{targetType}") and mock.compare_404:
        return 404, None
    if template == "/api/auth/userlist":
        return 200, {
            "result": [
                {
                    "id": "canary",
                    "name": "CANARY",
                    "email": "canary@example.invalid",
                    "phoneNumber": "010-1234-5678",
                }
            ]
        }
    account = {
        "id": "canary",
        "name": "CANARY",
        "group": "admin",
        "password": s,
        "allowIp": "192.168.10.77",
        "creationTime": 0,
        "lastLoginTime": 0,
    }
    if template == "/restapi/users":
        return 200, [account, {**account, "id": "op01", "name": "Operator", "password": s}]
    if template == "/restapi/user/{id}":
        return (404, None) if v["id"] == "nobody" else (200, {**account, "id": v["id"]})
    if template == "/api-v2/manage/data-server/domains":
        return 200, {
            "count": 1,
            "list": [{"address": "ds01:5555", "domain": [{"id": 1000, "name": "demo-domain"}]}],
        }
    if template == "/api-v2/manage/data-server/resource":
        return 200, {
            "ds01:5555": {
                "cpu": {
                    "core": 8,
                    "system": 12,
                    "process": 5,
                    "steal": 0,
                    "loadAverage": {"1m": 1, "5m": 2, "15m": 3},
                },
                "memory": {"total": 16384, "used": 4096},
            }
        }
    if template == "/api-v2/manage/data-server/system-property-config":
        return 200, {
            "ds01:5555": {
                "keepAliveTimeout": 60000,
                "dbPath": "/data/jennifer/db",
                "logPath": "/data/jennifer/log",
                "listenAddress": "0.0.0.0",
                "listenPort": 5555,
                "backupPath": "/backup/jennifer",
                "warningUsableSizeInMB": 1024,
                "memoryLock": False,
                "bootstrapCheck": True,
                "otelPort": 4317,
                "otelProtocol": "grpc",
                "rdbExportPassword": s,
                "rdbUrl": f"jdbc:postgresql://jennifer:{s}@rdb.example/export",
            }
        }
    if template == "/api-v2/manage/rule/active-service-color-range-boundary":
        return 200, [3000, 8000, 15000]
    if template == "/api-v2/active-service/detail/{domainId}/{txid}":
        return 200, {
            "userId": "kimcs01",
            "guid": "guid-active-0001",
            "sql": f"select * from users where password = '{s}' and id = 42",
            "http": {"method": "POST", "query": f"user=kim&password={s}&page=2"},
            "elapsedTime": 700000,
        }
    if template == "/api-v2/manage/db/path/{domainId}":
        return 200, {
            "main": "/data/jennifer/db/main",
            "backup": f"jdbc:postgresql://backup:{s}@db.example/x",
        }
    if template == "/api-v2/environment-variable/{domainId}":
        return 200, {
            "1001": {
                "SYSTEM": {
                    "PATH": "/usr/local/bin:/usr/bin",
                    "JAVA_HOME": "/opt/java/openjdk",
                    "DB_PW2": s,
                    "PGPASSWORD": s,
                    "JAVA_OPTS": f"-Xmx1g -Ddb.password={s} -Dfile.encoding=UTF-8",
                },
                "JAVA": {
                    "java.vendor": "Eclipse Adoptium",
                    "db.password": s,
                    "spring.datasource.url": f"jdbc:mysql://app:{s}@db.example:3306/x",
                },
            },
            "1002": {"SYSTEM": {"PATH": "/usr/bin"}, "JAVA": {"java.vendor": "Eclipse Adoptium"}},
        }
    if template == "/api-v2/manage/instance":
        if "processId" not in query:
            return 500, {"exception": {"message": missing_param_message("processId")}}
        if query["processId"] != W7_PROCESS_ID:
            return 200, {}
        return 200, {"1000": {"1001": {"hostname": query.get("hostname") or "was-host01"}}}
    if template == "/api-v2/loaded-class/{domainId}/{instanceId}":
        classes = [
            {
                "className": "com.example.order.OrderService",
                "superClassName": "java.lang.Object",
                "interfaceClassNames": ["java.io.Serializable"],
                "classLoaderName": "app",
            },
            {
                "className": "com.example.pay.PayClient",
                "superClassName": "java.lang.Object",
                "interfaceClassNames": [],
                "classLoaderName": "app",
            },
        ]
        needle = query.get("search", "")
        return 200, [c for c in classes if needle in c["className"]]
    if template == "/api-v2/manage/rule/event/error/{domainId}":
        return 200, [
            {
                "errorType": "OUTOFMEMORY",
                "level": "FATAL",
                "applied": True,
                "checkTimeRange": 60000,
                "thresholdErrorCount": 1,
                "iconRecoveryTime": 300000,
                "customMessage": "OOM 담당 kim@example.com",
                "autoScriptCommand": f"/opt/jennifer/restart.sh --password {s} --user admin",
            }
        ]
    if template == "/api-v2/manage/rule/event/metric/{domainId}/{targetType}":
        return 200, [
            {
                "metricId": "heap_used",
                "level": "WARNING",
                "applied": True,
                "expression": "value>30",
                "checkTimeRange": 60000,
                "thresholdErrorCount": 3,
                "iconRecoveryTime": 300000,
                "customMessage": "",
                "autoScriptCommand": None,
            }
        ]
    if "/rule/event/compar" in template:
        return 200, [
            {
                "metricId": "service_time",
                "level": "WARNING",
                "applied": False,
                "iconRecoveryTime": 300000,
                "target": {"operator": ">", "period": "PREVIOUS_WEEK", "ratioInPercent": 130},
                "filter": {"metricId": "service_count", "minimumValue": 10},
            }
        ]
    if template.endswith("/applied"):
        return 200, True
    if template.endswith("/individual-setting/{instanceId}"):
        key = f"{v['domainId']}/{v['errorType']}/{v['instanceId']}"
        return (200, mock.individual[key]) if key in mock.individual else (404, None)
    return 200, [{"id": "311d6aaa", "date": "2026-10-01", "statusDescription": "COMPLETED"}]


def w7_response(
    state: Any, method: str, template: str, query: dict[str, str], path: str
) -> tuple[int, Any, str]:
    """목 서버 W7 응답 — (상태, 본문, Content-Type). 비GET은 쓰기 거부(403)."""
    if method != "GET":
        return 403, "mock refuses write", "application/json"
    if getattr(state, "mode", "") == "disconnected" and "{domainId}" in template:
        domain = _w7_vars(template, path).get("domainId", "")
        return (
            500,
            f"500 500 com.aries.view.core.nio.DataServerDownException: {domain} Domain is not"
            " connected",
            "application/json",
        )
    status, body = w7_body(w7_state(state), template, query, path)
    if status == 404 and body is None:
        return 404, NOT_FOUND_HTML, "text/html"
    return status, body, "application/json"


def make_server(
    fixtures_dir: Path | None, token: str, mode: str = "fixtures", port: int = 0
) -> tuple[ThreadingHTTPServer, MockState]:
    """127.0.0.1에만 바인딩한 목 서버를 만든다(port=0이면 임의 포트)."""
    state = MockState(fixtures_dir, token, mode)
    handler = type("BoundHandler", (Handler,), {"state": state})
    return ThreadingHTTPServer(("127.0.0.1", port), handler), state


def main() -> int:
    here = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", default=str(here / "recorded" / "local-docker"))
    ap.add_argument("--port", type=int, default=17901)
    ap.add_argument("--token", default="mock-token")
    ap.add_argument("--mode", choices=MODES, default="fixtures")
    args = ap.parse_args()
    server, state = make_server(Path(args.fixtures), args.token, args.mode, args.port)
    n = sum(len(v) for v in state.fixtures.values())
    print(
        f"제니퍼 목 서버: http://127.0.0.1:{server.server_address[1]}"
        f" · fixture {n}건 · mode={args.mode}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
