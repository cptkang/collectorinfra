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
from typing import Any, Optional, Protocol, runtime_checkable

import httpx

from .assertions import AUTH_FAILURE_STATUSES, Observation

# 무이벤트 구간이 이 값을 넘으면 hang 후보로 본다(§3.8 · D-198 계열).
# 서버 하트비트 간격의 배수로 잡는다 - 하트비트가 꺼져 있어도 이 상한은 유효하다.
DEFAULT_HANG_GAP_MS = 120_000.0

#: 재시도 예산(`QUERY_MAX_RETRY_COUNT`)이 걸리는 회귀 지점.
#: `query_validator` 실패·`query_executor` SQL 에러·`result_organizer` 데이터 부족이
#: 전부 이 노드로 되돌아온다(CLAUDE.md 「LangGraph 노드」). 스트림의 `node_start` 를 세면
#: **서버를 고치지 않고** 재시도 횟수를 얻는다.
RETRY_ENTRY_NODE = "query_generator"


@runtime_checkable
class TokenProvider(Protocol):
    """질의 토큰의 수명 관리자(T-a·T-b). 구현은 러너의 `TokenSource` 다.

    클라이언트가 러너를 import 하면 순환이므로 **계약만** 여기에 둔다.
    """

    @property
    def token(self) -> Optional[str]:
        """지금 써야 할 토큰."""

    def refresh(self) -> Optional[str]:
        """재로그인해 새 토큰을 받는다. 못 받으면 None."""


@dataclass
class ClientConfig:
    """클라이언트 설정. 포트는 러너가 프로파일별로 정한다."""

    port: int
    token: Optional[str] = None
    admin_token: Optional[str] = None
    timeout_sec: float = 360.0
    hang_gap_ms: float = DEFAULT_HANG_GAP_MS
    artifact_dir: Optional[Path] = None
    #: 있으면 **토큰의 정본**이다(T-a·T-b). `token` 필드는 폴백으로만 남는다 -
    #: 동시 부하(K-06·K-07)는 같은 ClientConfig 로 세션마다 클라이언트를 새로 만들므로,
    #: 토큰을 값으로 복사해 두면 한 세션의 재발급이 다른 세션에 닿지 않는다.
    token_source: Optional[TokenProvider] = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/api/v1"

    @property
    def current_token(self) -> Optional[str]:
        return self.token_source.token if self.token_source is not None else self.token

    @property
    def headers(self) -> dict[str, str]:
        token = self.current_token
        return {"Authorization": f"Bearer {token}"} if token else {}

    @property
    def admin_headers(self) -> dict[str, str]:
        """설정 에코 전용 헤더.

        **질의 토큰과 같은 것이 아니다.** `/query/*` 는 `require_user`(auth.jwt_secret)를
        타고 `/admin/settings/schema` 는 `require_admin_user`(admin.jwt_secret 또는
        role=admin 사용자)를 탄다 - D-070 으로 두 시크릿이 분리돼 있어 한쪽 토큰을
        다른 쪽에 쓰면 401 이다. 하나로 합치면 "질의는 되는데 주입 검증만 조용히
        건너뛰는" 상태가 만들어진다(2026-09-14 실측: 1984건 전량 401).
        """
        return {"Authorization": f"Bearer {self.admin_token}"} if self.admin_token else {}


def _http_error(status_code: int, body: str) -> str:
    """HTTP 실패를 관측치의 `error` 로 옮긴다.

    **여기서 error 를 채우지 않으면 판정기가 그 턴을 `manual` 로 남긴다** -
    `evaluate_turn` 은 `obs.error` 가 비고 단언 실패도 없으면 "옮기지 않은 기대값이
    남았다"로 읽기 때문이다(assertions.py:337). 그래서 401 이 1984건 나도 리포트에는
    "판정 불가"만 찍히고 실패로는 한 건도 세지 않았다(2026-09-14 실측).
    """
    hint = ""
    if status_code in (401, 403):
        hint = " - 토큰 없음/만료. 러너에 크레덴셜을 넘겼는지 확인"
    return f"http {status_code}{hint}: {body[:300]}"


def _count_retries(obs: Observation) -> tuple[Optional[int], bool]:
    """재생성 회차를 센다. (회차, 하한 여부). 볼 수 있는 신호가 없으면 None 이다.

    - 단일 그래프 경로: 회귀 노드 `query_generator` 의 **완료 횟수 - 1**. node_start 는 노드마다
      한 번만 오므로(query.py `_seen_nodes`) 시작을 세면 늘 0 이었다.
    - 서브에이전트 경로(intent_orchestration·deep_agent): 진행 이벤트 `pipeline.generate` 시작 수에서
      파이프라인 수(`pipeline.schema` 시작)를 뺀다(subagents.py - 회차마다 generate 가 다시 시작한다).
    - 멀티 DB 경로(`pipeline.multi_db`)는 존별 검증 거부 재시도가 스트림에 없다 - 하한으로 표시한다.
    러너가 감사 로그의 `retry_attempt` 로 이 값을 보강한다.
    """
    counts: dict[str, int] = {}
    for event in obs.progress_events:
        if str(event.get("phase") or "start") == "start":
            name = str(event.get("name") or "")
            counts[name] = counts.get(name, 0) + 1
    partial = counts.get("pipeline.multi_db", 0) > 0
    if obs.node_calls.get(RETRY_ENTRY_NODE):
        return max(0, obs.node_calls[RETRY_ENTRY_NODE] - 1), partial
    if counts.get("pipeline.generate"):
        return max(0, counts["pipeline.generate"] - counts.get("pipeline.schema", 0)), partial
    return (0 if partial else None), partial


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
    obs.form_memory_panel = payload.get("form_memory_panel")
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
            resp = self._client.get(url, headers=self._config.admin_headers, timeout=20.0)
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

    def login(self, user_id: str, password: str) -> tuple[Optional[str], Optional[str]]:
        """사용자 로그인 - `/query/*` 용 토큰을 받는다.

        본문 키는 **`user_id`** 다(`UserLoginRequest`, schemas.py:185). `username` 으로
        보내면 로그인 실패가 아니라 422 가 돌아온다 - 둘은 다른 사고이므로 사유도 달라야 한다.
        """
        return self._post_login("/auth/login", {"user_id": user_id, "password": password})

    def admin_login(self, username: str, password: str) -> tuple[Optional[str], Optional[str]]:
        """운영자 로그인 - 설정 에코(`/admin/settings/schema`) 용 토큰을 받는다.

        운영자 크레덴셜은 `ADMIN_USERNAME`/`ADMIN_PASSWORD` 를 그대로 대조하므로
        (admin_auth.py:164) 인증 DB 없이도 성립한다. 본문 키는 **`username`** 이다 -
        사용자 로그인과 반대라 한 함수로 합칠 수 없다.
        """
        return self._post_login("/admin/login", {"username": username, "password": password})

    def _post_login(
        self, path: str, body: dict[str, str]
    ) -> tuple[Optional[str], Optional[str]]:
        url = f"{self._config.base_url}{path}"
        try:
            resp = self._client.post(url, json=body, timeout=20.0)
        except httpx.HTTPError as exc:
            return None, f"{path} 로그인 실패: {type(exc).__name__}: {exc}"
        if resp.status_code != 200:
            # 본문을 붙인다. 401(크레덴셜 불일치)·422(본문 계약 어긋남)·503(인증 DB 없음)은
            # 조치가 전부 달라서 상태코드만으로는 다음 행동이 정해지지 않는다.
            return None, f"{path} 로그인 실패 (http {resp.status_code}): {resp.text[:200]}"
        try:
            payload = resp.json()
        except ValueError:
            return None, f"{path} 로그인 응답이 JSON 이 아니다"
        token = payload.get("access_token") or payload.get("token")
        return (str(token), None) if token else (None, f"{path} 로그인 응답에 토큰이 없다")

    # --- 질의 -----------------------------------------------------------

    def send(
        self, endpoint: str, payload: dict[str, Any], upload: Optional[Path] = None
    ) -> Observation:
        """턴 1회의 요청. **401/403 이면 재로그인 후 1회만 다시 보낸다**(T-a).

        run 20260915-131903 은 8시간을 넘기는 순간(`AuthConfig.jwt_expire_hours = 8`)
        280번째 턴부터 마지막까지 **103턴 전건이 401** 이었다. 러너가 프로파일 기동 시
        한 번만 토큰을 받고 재발급 경로가 없었기 때문이다.

        재시도는 **1회뿐**이다. 크레덴셜이 틀려서 나는 401 을 무한히 두드리면
        `max_login_attempts`(기본 5)에 걸려 계정이 잠긴다.
        """
        obs = self._dispatch(endpoint, payload, upload)
        if obs.http_status not in AUTH_FAILURE_STATUSES:
            return obs
        source = self._config.token_source
        if source is None or source.refresh() is None:
            # 재발급 경로가 없다(주입 토큰 · 크레덴셜 부재 · 재로그인 실패).
            # 조용히 넘기지 않는다 - 판정기가 이 턴을 `invalid` 로 적재한다(T-c).
            return obs
        retried = self._dispatch(endpoint, payload, upload)
        retried.auth_retried = True
        return retried

    def _dispatch(
        self, endpoint: str, payload: dict[str, Any], upload: Optional[Path]
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
            obs.error = _http_error(resp.status_code, resp.text)
            return obs
        _apply_done(obs, resp.json())
        return obs

    def _consume_sse(self, response: httpx.Response, obs: Observation, started: float) -> None:
        """SSE 라인을 소비하며 노드 지연·무이벤트 간격을 측정한다."""
        last_event = started
        # 노드 구간의 시작 경계. 서버는 node_start 를 노드마다 **한 번만** 보내고(query.py
        # `_seen_nodes`) node_complete 는 회차마다 보낸다. 최상위 노드는 순차로 돌므로, 재진입한
        # 회차는 직전 완료 시각에 시작한 것이다. 종전의 "마지막 완료 - 첫 시작" 은 재계획 루프에서
        # 구간이 겹쳐 노드 합계가 전체 소요를 넘었다(run 20260914-154940: 7,700s > 6,278s).
        boundary_ms: Optional[float] = None
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
                start_ms = float(payload.get("timestamp_ms") or 0.0)
                boundary_ms = start_ms if boundary_ms is None else max(boundary_ms, start_ms)
                obs.node_path.append(name)
                if obs.ttfb_ms is None:
                    obs.ttfb_ms = (now - started) * 1000
            elif kind == "node_complete":
                name = str(payload.get("node") or "")
                end_ms = float(payload.get("timestamp_ms") or 0.0)
                begin_ms = end_ms if boundary_ms is None else boundary_ms
                obs.node_elapsed_ms[name] = round(
                    obs.node_elapsed_ms.get(name, 0.0) + max(0.0, end_ms - begin_ms), 1
                )
                obs.node_calls[name] = obs.node_calls.get(name, 0) + 1
                boundary_ms = max(begin_ms, end_ms)
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
        # 비용 축은 스트림에서 나오는 것만 센다.
        #
        # `done` 페이로드에는 LLM 호출 수도 토큰 수도 없다(query.py 의 done 이벤트 키 목록).
        # 그래서 `llm_calls`·`tokens` 는 **구조적으로 측정 불가**이며 여기서 추정하지 않는다 -
        # 추정치를 넣으면 리포트가 "쟀다"고 말하게 된다. 대신 실제로 세지는 둘을 남긴다:
        #   `retries`    회귀 지점 재진입 수 (재시도 예산의 실측)
        #   `node_count` 실행된 노드 수 (파이프라인이 한 일의 양 - 비용 대리 지표)
        # 실행된 노드 **회차** 수. 완료 이벤트는 회차마다 오고 시작은 노드마다 한 번이라, 완료 회차에
        # 끝나지 않은(완료 이벤트가 없는) 시작을 더한다 - 시작만 세면 재계획·재시도 루프가 사라진다.
        obs.node_count = sum(obs.node_calls.values()) + sum(
            1 for name in obs.node_path if name not in obs.node_calls
        )
        # 회귀 노드가 스트림에 보일 때만 센다. intent_orchestration·deep_agent 단은 SQL 생성이
        # 하위 에이전트 안에서 돌아 query_generator 의 node_start 가 상위 스트림에 나오지 않는다
        # (2026-09-14 폐쇄망 런: 93턴 전부 상위 노드 7개 · 재진입 0) — 0 이 아니라 "못 봤다"다.
        obs.retries, obs.retries_partial = _count_retries(obs)
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
                    obs.error = _http_error(resp.status_code, resp.text)
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
                        obs.error = _http_error(resp.status_code, resp.text)
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
                            obs.error = _http_error(resp.status_code, resp.text)
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
