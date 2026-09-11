"""HTTP/SSE 클라이언트 (plans/94 §4.3).

측정 경로는 `/api/v1/query/stream` 이 정본이다. 실패 트레이스는 **실패 요청만** 파일로
남기므로(trace_writer.flush_if_failed) 성공 건의 노드별 지연은 거기서 못 얻는다. 반면
스트림은 node_start/node_complete/progress 를 timestamp_ms 와 함께 흘리고 done 에
processing_time_ms·executed_sql·row_count·has_file 이 전부 실린다 - `src/` 수정 없이
성공 건의 지연 분해를 얻는 유일한 경로다(§0.3-3).

기준 URL은 **127.0.0.1 고정**이다. Windows에서 `localhost`는 ::1(IPv6)을 먼저 시도할 수
있는데 API_HOST=0.0.0.0 은 IPv4 전용 바인딩이라 간헐 실패한다(부록 A.1-5 · W2).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

from .assertions import Observation

# 무이벤트 구간이 이 값을 넘으면 hang 후보로 본다(§3.8 · D-198 계열).
# 서버 하트비트 간격의 배수로 잡는다 - 하트비트가 꺼져 있어도 이 상한은 유효하다.
DEFAULT_HANG_GAP_MS = 120_000.0


@dataclass
class ClientConfig:
    """클라이언트 설정. 포트는 러너가 프로파일별로 정한다."""

    port: int
    token: Optional[str] = None
    timeout_sec: float = 360.0
    hang_gap_ms: float = DEFAULT_HANG_GAP_MS
    artifact_dir: Optional[Path] = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/api/v1"

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}


def _derive_status(payload: dict[str, Any]) -> str:
    """SSE done 이벤트에는 status 키가 없다 - 존재하는 키로 상태를 유도한다.

    역질문은 clarification 키의 존재로만 판별된다(query.py:1311 의 pre-gate done).
    이 유도 규칙을 한 곳에 두지 않으면 스트림 경로와 비스트림 경로의 판정이 갈린다.
    """
    if payload.get("clarification"):
        return "clarification"
    if payload.get("awaiting_approval"):
        return "awaiting_approval"
    return str(payload.get("status") or "completed")


def _apply_done(obs: Observation, payload: dict[str, Any]) -> None:
    obs.query_id = payload.get("query_id")
    obs.response = str(payload.get("response") or obs.response)
    obs.executed_sql = payload.get("executed_sql")
    obs.row_count = payload.get("row_count")
    obs.has_file = bool(payload.get("has_file"))
    obs.file_name = payload.get("file_name")
    obs.clarification = payload.get("clarification")
    obs.form_fill_clarification = payload.get("form_fill_clarification")
    obs.processing_time_ms = payload.get("processing_time_ms")
    obs.status = _derive_status(payload)
    scope = payload.get("db_scope") or {}
    if isinstance(scope, dict) and scope.get("db_ids"):
        obs.db_ids = [str(d) for d in scope["db_ids"]]


class ScenarioClient:
    """시나리오 1턴을 보내고 관측치를 돌려준다."""

    def __init__(self, config: ClientConfig) -> None:
        self._config = config
        self._client = httpx.Client(timeout=config.timeout_sec)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ScenarioClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- 헬스·설정 에코 -------------------------------------------------

    def health(self) -> tuple[bool, str]:
        try:
            resp = self._client.get(f"{self._config.base_url}/health", timeout=10.0)
        except httpx.HTTPError as exc:
            return False, f"{type(exc).__name__}: {exc}"
        return resp.status_code == 200, f"http {resp.status_code}"

    def effective_settings(self) -> tuple[Optional[dict[str, str]], Optional[str]]:
        """실효 설정 에코를 읽는다 (§4.5 · 계획서 §4.2-2 정정).

        `GET /admin/settings` 는 **`.env`에 실존하는 키만** 돌려주는 DEPRECATED 평면
        목록이다(admin.py:535). 프로파일은 OS env로 주입하는데 `.env`에 없는 키는 거기
        나오지 않으므로, 주입 무시를 잡아야 할 장치가 주입 자체를 못 본다.
        정본은 `/admin/settings/schema` 다 - build_catalog 가 effective_value 와
        override("os"/"encenv")까지 준다(settings_catalog.py:1029).
        """
        url = f"{self._config.base_url}/admin/settings/schema"
        try:
            resp = self._client.get(url, headers=self._config.headers, timeout=20.0)
        except httpx.HTTPError as exc:
            return None, f"설정 에코 요청 실패: {type(exc).__name__}: {exc}"
        if resp.status_code in (401, 403):
            # AUTH_ENABLED=true 인 환경에서 토큰이 없다. 확인 못 한 것을 통과로 세지 않는다(G-3).
            return None, f"설정 에코 미확인 (http {resp.status_code} - 관리자 토큰 필요)"
        if resp.status_code != 200:
            return None, f"설정 에코 미확인 (http {resp.status_code})"
        values: dict[str, str] = {}
        for group in resp.json().get("groups", []):
            for item in group.get("settings", []):
                key = item.get("env_key")
                if key:
                    values[str(key)] = "" if item.get("effective_value") is None else str(
                        item["effective_value"]
                    )
        return values, None

    def login(self, username: str, password: str) -> tuple[Optional[str], Optional[str]]:
        url = f"{self._config.base_url}/auth/login"
        try:
            resp = self._client.post(
                url, json={"username": username, "password": password}, timeout=20.0
            )
        except httpx.HTTPError as exc:
            return None, f"로그인 실패: {type(exc).__name__}: {exc}"
        if resp.status_code != 200:
            return None, f"로그인 실패 (http {resp.status_code})"
        body = resp.json()
        token = body.get("access_token") or body.get("token")
        return (str(token), None) if token else (None, "로그인 응답에 토큰이 없다")

    # --- 질의 -----------------------------------------------------------

    def send(
        self, endpoint: str, payload: dict[str, Any], upload: Optional[Path] = None
    ) -> Observation:
        if endpoint == "plain":
            return self._post_plain(payload)
        if endpoint == "stream":
            return self._post_stream(payload)
        if endpoint in ("file", "file_stream"):
            return self._post_file(endpoint, payload, upload)
        raise ValueError(f"알 수 없는 endpoint: {endpoint}")

    def _post_plain(self, payload: dict[str, Any]) -> Observation:
        obs = Observation()
        started = time.perf_counter()
        try:
            resp = self._client.post(
                f"{self._config.base_url}/query",
                json=payload,
                headers=self._config.headers,
            )
        except httpx.HTTPError as exc:
            obs.wall_ms = (time.perf_counter() - started) * 1000
            obs.error = f"{type(exc).__name__}: {exc}"
            obs.hang = isinstance(exc, httpx.TimeoutException)
            obs.status = "error"
            return obs
        obs.wall_ms = (time.perf_counter() - started) * 1000
        obs.http_status = resp.status_code
        if resp.status_code >= 400:
            obs.status = "error"
            obs.response = resp.text[:4000]
            return obs
        _apply_done(obs, resp.json())
        return obs

    def _consume_sse(self, response: httpx.Response, obs: Observation, started: float) -> None:
        """SSE 라인을 소비하며 노드 지연·무이벤트 간격을 측정한다."""
        last_event = started
        node_start: dict[str, float] = {}
        max_gap = 0.0
        saw_done = False
        tokens: list[str] = []

        for line in response.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            now = time.perf_counter()
            max_gap = max(max_gap, (now - last_event) * 1000)
            last_event = now
            try:
                payload = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            kind = str(payload.get("type") or "")
            obs.sse_events.append(kind)

            if kind == "node_start":
                name = str(payload.get("node") or "")
                node_start[name] = float(payload.get("timestamp_ms") or 0.0)
                obs.node_path.append(name)
                if obs.ttfb_ms is None:
                    obs.ttfb_ms = (now - started) * 1000
            elif kind == "node_complete":
                name = str(payload.get("node") or "")
                end_ms = float(payload.get("timestamp_ms") or 0.0)
                if name in node_start:
                    obs.node_elapsed_ms[name] = round(end_ms - node_start[name], 1)
            elif kind == "progress":
                obs.progress_events.append(payload)
            elif kind == "token":
                tokens.append(str(payload.get("content") or ""))
            elif kind == "error":
                obs.error = str(payload.get("message") or payload.get("detail") or "error")
                obs.status = "error"
            elif kind == "done":
                saw_done = True
                _apply_done(obs, payload)

        obs.max_event_gap_ms = round(max_gap, 1)
        if not obs.response and tokens:
            obs.response = "".join(tokens)
        if not saw_done:
            # done 없이 끊겼다. 조용히 성공으로 세지 않는다.
            obs.hang = True
            obs.error = obs.error or "done 이벤트 없이 스트림이 끝났다"
            obs.status = "error" if obs.status == "unknown" else obs.status
        elif max_gap > self._config.hang_gap_ms:
            obs.hang = True

    def _post_stream(self, payload: dict[str, Any]) -> Observation:
        obs = Observation()
        started = time.perf_counter()
        try:
            with self._client.stream(
                "POST",
                f"{self._config.base_url}/query/stream",
                json=payload,
                headers=self._config.headers,
            ) as resp:
                obs.http_status = resp.status_code
                if resp.status_code >= 400:
                    resp.read()
                    obs.status = "error"
                    obs.response = resp.text[:4000]
                    obs.wall_ms = (time.perf_counter() - started) * 1000
                    return obs
                self._consume_sse(resp, obs, started)
        except httpx.HTTPError as exc:
            obs.error = f"{type(exc).__name__}: {exc}"
            obs.hang = isinstance(exc, httpx.TimeoutException)
            obs.status = "error"
        obs.wall_ms = (time.perf_counter() - started) * 1000
        return obs

    def _post_file(
        self, endpoint: str, payload: dict[str, Any], upload: Optional[Path]
    ) -> Observation:
        obs = Observation()
        if upload is None or not Path(upload).exists():
            obs.status = "error"
            obs.error = f"업로드 파일이 없다: {upload}"
            return obs

        form: dict[str, str] = {"query": str(payload.get("query") or "")}
        if payload.get("thread_id"):
            form["thread_id"] = str(payload["thread_id"])
        if payload.get("selected_db_ids"):
            form["selected_db_ids"] = ",".join(payload["selected_db_ids"])

        path = Path(upload)
        started = time.perf_counter()
        url = f"{self._config.base_url}/query/file"
        if endpoint == "file_stream":
            url += "/stream"
        try:
            with open(path, "rb") as handle:
                files = {"file": (path.name, handle, "application/octet-stream")}
                if endpoint == "file":
                    resp = self._client.post(
                        url, data=form, files=files, headers=self._config.headers
                    )
                    obs.http_status = resp.status_code
                    obs.wall_ms = (time.perf_counter() - started) * 1000
                    if resp.status_code >= 400:
                        obs.status = "error"
                        obs.response = resp.text[:4000]
                        return obs
                    _apply_done(obs, resp.json())
                else:
                    with self._client.stream(
                        "POST", url, data=form, files=files, headers=self._config.headers
                    ) as resp:
                        obs.http_status = resp.status_code
                        if resp.status_code >= 400:
                            resp.read()
                            obs.status = "error"
                            obs.response = resp.text[:4000]
                            obs.wall_ms = (time.perf_counter() - started) * 1000
                            return obs
                        self._consume_sse(resp, obs, started)
                    obs.wall_ms = (time.perf_counter() - started) * 1000
        except httpx.HTTPError as exc:
            obs.wall_ms = (time.perf_counter() - started) * 1000
            obs.error = f"{type(exc).__name__}: {exc}"
            obs.hang = isinstance(exc, httpx.TimeoutException)
            obs.status = "error"
        return obs

    def download(self, query_id: str, dest_dir: Path, name_hint: str) -> Optional[Path]:
        """산출물을 내려받는다. 전 칼럼 검증(V7)의 재료다."""
        url = f"{self._config.base_url}/query/{query_id}/download"
        try:
            resp = self._client.get(url, headers=self._config.headers, timeout=120.0)
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / name_hint
        dest.write_bytes(resp.content)
        return dest
