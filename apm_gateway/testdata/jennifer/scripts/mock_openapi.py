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

한계: 실제 EVENT 발생·필드 변형은 재현하지 못한다. 이벤트는 `/__mock/events`로 주입한다.

제어 경로(인증 없음 · 127.0.0.1 전용):
    GET  /__mock/hits     접근 기록        POST /__mock/reset   기록·주입 이벤트·사용량 초기화
    POST /__mock/events   EventData 목록 주입(13필드 부분집합 · time 필수)
    GET  /__mock/usage    토큰 사용량
    POST /__mock/mode     {"mode": "fixtures|connected|disconnected"}

사용:
    python mock_openapi.py --fixtures ../recorded/local-docker --port 17901 --token mock-token
"""

from __future__ import annotations

import argparse
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
        with self.state.lock:
            self.state.hits.append(
                {
                    "ts": time.time(),
                    "method": method,
                    "path": path,
                    "template": template,
                    "allowlisted": allowlisted,
                    "query_keys": sorted(query),
                    "query_token": query_token,
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
            return self._send(200, {"reset": True})
        if method == "POST" and path == "/__mock/mode":
            mode = (self._body_json() or {}).get("mode")
            if mode not in MODES:
                return self._send(400, {"error": f"mode는 {MODES} 중 하나"})
            st.mode = mode
            return self._send(200, {"mode": mode})
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
        if fx is None:
            return self._send(
                501, {"mock_error": f"fixture 없음: {template} (mode={self.state.mode})"}
            )
        resp = fx["response"]
        ctype = resp.get("content_type") or "application/json"
        body = resp["body_json"] if "body_json" in resp else resp.get("body_text", "")
        return self._send(resp["status"], body, ctype.split(";")[0])

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
        return self._send(200, {"result": base + injected})

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")


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
