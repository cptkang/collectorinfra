"""떠 있는 웹 서버에 질의하는 HTTP 클라이언트 — 웹 UI 와 같은 계약으로 보낸다.

- 질의: `POST /api/v1/query/stream`(JSON) · 업로드 `POST /api/v1/query/file/stream`(multipart)
- 인증: `POST /api/v1/auth/login` 본문 `{user_id, password}` → Bearer 토큰
- 이력 소유: 로그인 사용자는 user_id, 인증이 꺼진 서버는 `X-Client-Id`(브라우저 식별자)로
  묶인다(`src/api/thread_history.py history_owner`). 그래서 인증이 꺼진 서버에서는 브라우저의
  식별자를 `--client-id` 로 넘겨야 그 브라우저의 대화 목록에 나타난다.
- 역질문 답: 웹 UI 의 칩 확인 버튼과 같은 구조화 필드(`selected_db_ids`·`selected_sources`·
  `selected_apm_source_ids`·`form_fill_answers`)로 보낸다 — 자연어로 다시 쓰지 않는다.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx

#: 폼필 답변 턴의 질의 문구 — 웹 UI(app.js)가 보내는 것과 같다.
FORM_FILL_REPLY_QUERY = "[양식 미해결 항목 답변]"
#: 승인 대기 답 — 서버의 결정적 승인 어휘에 있다.
APPROVAL_REPLY = "승인"

_DB_SELECT_KINDS = frozenset({"zone_select", "scope_select"})
_SOURCE_SELECT_KIND = "source_select"


@dataclass
class TurnResult:
    """질의 1회의 결과. 판정하지 않는다 — 화면에 보인 것과 소요 시간만 남긴다."""

    sent: dict[str, Any]
    upload: Optional[str] = None
    http_status: Optional[int] = None
    elapsed_s: float = 0.0
    response: str = ""
    done: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    auto_answer: bool = False
    downloaded: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.done)

    @property
    def status(self) -> str:
        if self.error:
            return "error"
        question = pending_question(self.done)
        if question:
            return f"clarify:{question.kind}"
        return str(self.done.get("status") or "completed")


@dataclass(frozen=True)
class Question:
    """답을 기다리는 역질문. kind = db_select | source_select | apm_source | form_fill | approval | text"""

    kind: str
    payload: dict[str, Any]


def pending_question(done: dict[str, Any]) -> Optional[Question]:
    clar = done.get("clarification")
    if isinstance(clar, dict):
        if clar.get("kind") in _DB_SELECT_KINDS:
            return Question("db_select", clar)
        if clar.get("kind") == _SOURCE_SELECT_KIND:
            return Question("source_select", clar)
    form = done.get("form_fill_clarification")
    if isinstance(form, dict) and form.get("fields"):
        return Question("form_fill", form)
    apm = done.get("apm_source_clarification")
    if isinstance(apm, dict) and apm.get("options"):
        return Question("apm_source", apm)
    if done.get("awaiting_approval"):
        return Question("approval", {})
    if clar:
        # 기간 되묻기 등 선택지 없는 글 질문 — 자동으로 답할 근거가 없다(발표자가 웹에서 답한다).
        return Question("text", clar if isinstance(clar, dict) else {"question": str(clar)})
    return None


def _offered(clar: dict[str, Any]) -> tuple[list[str], list[str]]:
    """선택지가 제시한 (db_id 목록, 소스 코드 목록) — 표시 순서대로."""
    ids: list[str] = []
    sources: list[str] = []
    for option in clar.get("options") or []:
        if not isinstance(option, dict):
            continue
        for db_id in ([option["db_id"]] if option.get("db_id") else list(option.get("db_ids") or [])):
            if db_id and str(db_id) not in ids:
                ids.append(str(db_id))
        src = option.get("source")
        if src and str(src) not in sources:
            sources.append(str(src))
    return ids, sources


def auto_answer(
    question: Question,
    *,
    last_query: str,
    zone_preference: tuple[str, ...] = (),
    apm_preference: tuple[str, ...] = (),
) -> Optional[dict[str, Any]]:
    """역질문에 보낼 본문(thread_id 제외). 자동으로 답할 수 없으면 None.

    - 존·범위 선택: 우선 존 목록에서 제시된 첫 존 → 없으면 첫 선택지(존 전체는 비용이 크다)
    - 소스 선택: 제시된 소스 **전부** — 교차 조회를 보여 주는 것이 시연 목적이다
    - 제니퍼 소스: 우선 목록 → 없으면 첫 선택지
    - 폼필 미해결 열: 전부 비워 둔다(후보를 임의로 고르면 오답을 채운다)
    """
    payload = question.payload
    original = str(payload.get("original_query") or last_query)
    if question.kind == "db_select":
        ids, _ = _offered(payload)
        chosen = [z for z in zone_preference if z in ids][:1] or ids[:1]
        return {"query": original, "selected_db_ids": chosen} if chosen else None
    if question.kind == "source_select":
        ids, sources = _offered(payload)
        if not ids and not sources:
            return None
        return {"query": original, "selected_db_ids": ids, "selected_sources": sources}
    if question.kind == "apm_source":
        keys = [str(o.get("key")) for o in payload.get("options") or []
                if isinstance(o, dict) and o.get("key")]
        chosen = [k for k in apm_preference if k in keys][:1] or keys[:1]
        return {"query": original, "selected_apm_source_ids": chosen} if chosen else None
    if question.kind == "form_fill":
        answers = {str(f["name"]): {"action": "blank", "value": None}
                   for f in payload.get("fields") or [] if isinstance(f, dict) and f.get("name")}
        return {"query": FORM_FILL_REPLY_QUERY, "form_fill_answers": answers} if answers else None
    if question.kind == "approval":
        return {"query": APPROVAL_REPLY}
    return None


class DemoClient:
    def __init__(
        self,
        base_url: str,
        *,
        user: Optional[str] = None,
        password: Optional[str] = None,
        client_id: Optional[str] = None,
        timeout_s: float = 600.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.api = base_url.rstrip("/") + "/api/v1"
        self._user = user
        self._password = password
        self._client_id = client_id
        self._token: Optional[str] = None
        # read 타임아웃은 SSE 청크 간격 상한이다(서버가 하트비트를 보낸다).
        self._http = httpx.Client(timeout=httpx.Timeout(timeout_s, connect=10.0), transport=transport)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "DemoClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- 준비 ---------------------------------------------------------

    def headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if self._client_id:
            headers["X-Client-Id"] = self._client_id
        return headers

    def health(self) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        try:
            resp = self._http.get(f"{self.api}/health", timeout=30.0)
        except httpx.HTTPError as exc:
            return None, f"서버에 연결하지 못했습니다 ({type(exc).__name__}: {exc})"
        if resp.status_code != 200:
            return None, f"헬스체크 http {resp.status_code}"
        return resp.json(), None

    def login(self) -> Optional[str]:
        """사용자 로그인. 크레덴셜이 없으면 아무것도 하지 않는다. 실패 사유를 돌려준다."""
        if not self._user:
            return None
        try:
            resp = self._http.post(f"{self.api}/auth/login",
                                   json={"user_id": self._user, "password": self._password or ""},
                                   timeout=20.0)
        except httpx.HTTPError as exc:
            return f"로그인 요청 실패: {type(exc).__name__}: {exc}"
        if resp.status_code != 200:
            return f"로그인 실패 (http {resp.status_code}): {resp.text[:200]}"
        token = (resp.json() or {}).get("access_token") or (resp.json() or {}).get("token")
        if not token:
            return "로그인 응답에 토큰이 없습니다"
        self._token = str(token)
        return None

    def admin_providers(self, username: str, password: str) -> tuple[Optional[tuple[str, str]], str]:
        """서버의 실효 (LLM_PROVIDER, ORCHESTRATOR_PROVIDER)를 운영자 설정 화면 API 에서 읽는다."""
        try:
            resp = self._http.post(f"{self.api}/admin/login",
                                   json={"username": username, "password": password}, timeout=20.0)
            if resp.status_code != 200:
                return None, f"운영자 로그인 실패 (http {resp.status_code})"
            token = (resp.json() or {}).get("access_token") or (resp.json() or {}).get("token")
            schema = self._http.get(f"{self.api}/admin/settings/schema",
                                    headers={"Authorization": f"Bearer {token}"}, timeout=20.0)
        except httpx.HTTPError as exc:
            return None, f"설정 조회 실패: {type(exc).__name__}: {exc}"
        if schema.status_code != 200:
            return None, f"설정 조회 실패 (http {schema.status_code})"
        values: dict[str, str] = {}
        for group in schema.json().get("groups", []):
            for item in group.get("settings", []):
                if item.get("env_key"):
                    values[str(item["env_key"])] = str(item.get("effective_value") or "")
        worker = values.get("LLM_PROVIDER", "unknown").strip().lower() or "unknown"
        orch = values.get("ORCHESTRATOR_PROVIDER", "unknown").strip().lower() or "unknown"
        return (worker, orch), "서버 설정"

    # --- 질의 ---------------------------------------------------------

    def send(self, body: dict[str, Any], upload: Optional[Path] = None) -> TurnResult:
        """1회 전송. 401 이면 재로그인 후 1회만 다시 보낸다(계정 잠금 방지 — 무한 재시도 금지)."""
        result = self._send_once(body, upload)
        if result.http_status == 401 and self._user and self.login() is None:
            result = self._send_once(body, upload)
        return result

    def _send_once(self, body: dict[str, Any], upload: Optional[Path]) -> TurnResult:
        result = TurnResult(sent=body, upload=str(upload) if upload else None)
        started = time.perf_counter()
        try:
            if upload is None:
                ctx = self._http.stream("POST", f"{self.api}/query/stream",
                                        json=body, headers=self.headers())
                self._consume(ctx, result)
            else:
                form = {"query": str(body.get("query") or "")}
                if body.get("thread_id"):
                    form["thread_id"] = str(body["thread_id"])
                if body.get("selected_db_ids"):
                    form["selected_db_ids"] = ",".join(body["selected_db_ids"])
                with upload.open("rb") as handle:
                    files = {"file": (upload.name, handle, "application/octet-stream")}
                    ctx = self._http.stream("POST", f"{self.api}/query/file/stream",
                                            data=form, files=files, headers=self.headers())
                    self._consume(ctx, result)
        except httpx.HTTPError as exc:
            result.error = f"{type(exc).__name__}: {exc}"
        result.elapsed_s = round(time.perf_counter() - started, 1)
        return result

    def _consume(self, ctx: Any, result: TurnResult) -> None:
        tokens: list[str] = []
        with ctx as resp:
            result.http_status = resp.status_code
            if resp.status_code >= 400:
                resp.read()
                result.error = f"http {resp.status_code}: {resp.text[:300]}"
                return
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    event = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                kind = event.get("type")
                if kind == "token":
                    tokens.append(str(event.get("content") or ""))
                elif kind == "error":
                    result.error = str(event.get("message") or event.get("detail") or "error")
                elif kind == "done":
                    result.done = event
        result.response = str(result.done.get("response") or "".join(tokens))
        if not result.done and result.error is None:
            result.error = "done 이벤트 없이 스트림이 끝났습니다"

    def download(self, query_id: str, dest: Path) -> Optional[Path]:
        """양식 산출물을 내려받는다. 실패하면 None(시연은 계속한다)."""
        try:
            resp = self._http.get(f"{self.api}/query/{query_id}/download",
                                  headers=self.headers(), timeout=120.0)
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return dest
