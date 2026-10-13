"""가짜 OpenAI 호환 LLM 서버 — E1 U층 드라이버 전용 (plans/152 §3.1 · W2 · D-323).

본체를 `LLM_PROVIDER=mlx`·`ORCHESTRATOR_PROVIDER=mlx` + base_url = 이 서버로 띄우면 두 평면이
비과금(`scripts.scenario.preflight.external_planes` 기준 mlx)으로 판정되고, 실 LLM·MLX 없이 2단
경로가 끝까지 돈다(W0 §7 ⓐ안). 127.0.0.1 임시 포트의 스레드 서버다.

응답은 시스템 프롬프트 표지로 고른다.
  - 분해(`INTENT_PLANNER_SYSTEM_TEMPLATE`의 「작업 계획 전문가」) → 드라이버가 턴마다 넣은 계획 JSON
  - 보기 재선택(`apm_query._SELECTION_SYSTEM`의 「보기 선택기」) → 계획의 APM task 보기·조건
  - 재계획(`REPLANNER_SYSTEM_TEMPLATE`의 「재계획 전문가」) → 후속 없음
  - 입력 파싱(`INPUT_PARSER_SYSTEM_PROMPT`의 「Middle-ware JSON Parser」) → 드라이버가 턴마다 넣은
    파싱 JSON(`parse_from_turn` — 대상 호스트 조건 · 기간 슬롯). 넣지 않았으면 요약 문장(종전)
  - 그 밖 → 짧은 요약 문장
`preflight.mlx_run_blockers`가 보는 `GET /v1/models`·1토큰 생성에도 답한다. 스트리밍(`stream: true`)
요청은 SSE 청크로 답한다.

기본 계획은 **시나리오 기대값에서 결정적으로 만든 「정답 계획」**(`plan_from_expect`)이다 —
오답 주입은
드라이버의 턴별 덮어쓰기 훅으로 한다. 받은 요청은 표지·사용자 메시지 앞부분만 `requests`에 남는다.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .scenarios import ANY_VALUE, Expect, PlaceholderError, fill_placeholders, placeholders_of

#: 시스템 프롬프트 표지(정본 프롬프트의 첫 문장 일부) → 요청 종류.
MARKERS: tuple[tuple[str, str], ...] = (
    ("작업 계획 전문가", "decompose"),
    ("보기 선택기", "reselect"),
    ("재계획 전문가", "replan"),
    ("Middle-ware JSON Parser", "parse"),
)
#: `preflight`가 보내는 모델 별칭(서버 --model 매핑) — 모델 목록에 싣는다.
MODEL_ID = "default_model"
SUMMARY_TEXT = "요청하신 조회 결과를 정리했습니다."
APM_AGENT = "apm_query"
#: 대상 표기 종류 → 분해 `targets.kind`(hostname 은 `targets`에 넣지 않는다 — 분해 프롬프트 규칙).
TARGET_KIND = {"inst": "instance", "biz": "business", "svc": "auto"}
#: 기록하는 사용자 메시지 앞부분 길이(자).
USER_HEAD = 200


@dataclass(frozen=True)
class FakeRequest:
    """가짜 LLM 이 받은 요청 1건(표지·앞부분만).

    Attributes:
        kind: decompose · reselect · replan · parse · other · probe
        system_head: 시스템 메시지 앞 80자
        user_head: 마지막 사용자 메시지 앞 `USER_HEAD`자
        stream: 스트리밍 요청이었나
        at: 수신 시각(monotonic)
    """

    kind: str
    system_head: str
    user_head: str
    stream: bool
    at: float


def _target_text(target: str, mapping: Mapping[str, str]) -> dict[str, str] | None:
    if target == "inst_w":
        return {"text": str(mapping["inst_w"]), "kind": "instance"} if "inst_w" in mapping else None
    kind, _, ref = target.partition(":")
    if kind not in TARGET_KIND or not ref:
        return None
    name = f"{kind}_{ref}"
    if name not in mapping:
        return None
    return {"text": str(mapping[name]), "kind": TARGET_KIND[kind]}


def _fill_value(value: Any, arg: Any, mapping: Mapping[str, str]) -> Any:
    """자리표를 채운다 — `int` 조건이면 숫자 문자열을 정수로 바꾼다(그 밖 값은 그대로)."""
    if not isinstance(value, str):
        return value
    try:
        filled = fill_placeholders(value, mapping)
    except PlaceholderError:
        return value
    return int(filled) if arg.type == "int" and filled.isdigit() else filled


def _any_value(arg: Any, any_values: Mapping[str, Any]) -> Any:
    """`"*"`(존재만 판정) 값 — 레지스트리 허용값 중 첫 값 · 없으면 자리표 파일 `any_values`."""
    if getattr(arg, "choices", ()):
        return arg.choices[0]
    if getattr(arg, "verified_values", ()):
        return arg.verified_values[0]
    if arg.name in any_values:
        return any_values[arg.name]
    if arg.type == "bool":
        return True
    if arg.type == "int":
        return arg.min if arg.min is not None else 1
    return None


def plan_from_expect(
    expect: Expect,
    prompt: str,
    *,
    mapping: Mapping[str, str],
    views: Mapping[str, Any],
    any_values: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """시나리오 기대값 → 분해 「정답 계획」 JSON(dict · 결정적).

    - 담당마다 task 하나(APM 아닌 담당은 `sub_query`만) · 거절 턴(담당 없음)은 `general_inference`
    - APM task: `views` = 기대 보기 · `view_args` = 기대 인자(자리표 채움 · `"*"`는 `_any_value`) ·
      `targets` = 대상 표기(인스턴스·업무·서비스만) · `sources` = 기대 소스 · `areas` = 보기 영역

    Args:
        expect: 턴 기대값
        prompt: 채운 사용자 프롬프트(`sub_query`)
        mapping: 자리표 → 값
        views: 레지스트리 보기 id → `ViewSpec`
        any_values: `"*"` 인자 이름 → 값(허용값 선언이 없는 인자)
    """
    any_values = any_values or {}
    tasks: list[dict[str, Any]] = []
    agents = list(expect.agents) or ["general_inference"]
    for order, agent in enumerate(agents, 1):
        task: dict[str, Any] = {
            "task_id": f"t{order}",
            "agent": agent,
            "sub_query": prompt,
            "depends_on": [],
            "input_from": [],
            "order": order,
        }
        if agent == APM_AGENT:
            specs = [views[v] for v in expect.views if v in views]
            view_args: dict[str, dict[str, Any]] = {}
            for key, value in expect.args.items():
                for spec in specs:
                    arg = next((a for a in spec.args if a.name == key), None)
                    if arg is None:
                        continue
                    filled = (_any_value(arg, any_values) if value == ANY_VALUE
                              else _fill_value(value, arg, mapping))
                    if filled is not None:
                        view_args.setdefault(spec.id, {})[key] = filled
                    break
            target = _target_text(expect.target, mapping)
            task.update(
                areas=list(dict.fromkeys(s.capability for s in specs if s.capability)),
                views=list(expect.views),
                view_args=view_args,
                targets=[target] if target else [],
                sources=list(expect.apm_sources),
            )
        tasks.append(task)
    return {"tasks": tasks, "clarification_needed": None}


#: 기간 표현(분해 프롬프트 기간 슬롯 절의 `last` 모양) — 접두(지난·최근…) · 수(숫자·한/두/세…) ·
#: 단위(분·시간·일) · 접미(동안·치·전부터·간). 「10분 간격」·「5분 단위」는 입도라 뺀다.
_SPAN_RE = re.compile(
    r"(?:(?:지난|최근|과거|직전)\s*)?(?P<n>[0-9]+|한|두|세|네|다섯|여섯|열)\s*(?P<u>분|시간|일)"
    r"(?!\s*(?:간격|단위))(?:\s*(?:동안|치|전부터|간))?"
)
_KOREAN_NUM = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "열": 10}
#: 기간 단위 → 슬롯 `unit`. 분은 슬롯 단위 `minute`(`time_spec.Unit` · D-326)으로 원문 그대로
#: 옮긴다 — 「정답 파서」는 원문을 바꾸지 않는다(사건 = 분 그대로 · 통계 = 정시 확장은 제품
#: 쪽이 정한다).
_SPAN_UNIT = {"분": "minute", "시간": "hour", "일": "day"}


def time_slot(prompt: str) -> dict[str, Any] | None:
    """프롬프트의 첫 기간 표현 → 기간 슬롯(`time_expr` · relation `last`) · 없으면 None."""
    m = _SPAN_RE.search(prompt)
    if m is None:
        return None
    raw = m.group("n")
    n = int(raw) if raw.isdigit() else _KOREAN_NUM[raw]
    return {"relation": "last", "n": n, "unit": _SPAN_UNIT[m.group("u")], "completeness": None,
            "anchor": None, "start": None, "end": None, "display_grain": "none",
            "span": m.group(0).strip()}


def parse_from_turn(expect: Expect, prompt_template: str, prompt: str, *,
                    mapping: Mapping[str, str]) -> dict[str, Any]:
    """턴 → 입력 파싱 「정답 응답」 JSON(dict · 결정적).

    - `filter_conditions`: 프롬프트 템플릿의 호스트 자리표(`{{host_*}}`) 값마다 `hostname` 조건
      (인스턴스·업무 이름은 분해 `targets`가 싣는다 — 파서 규칙상 식별 필터는 서버 식별자뿐)
    - `time_expr`: 기대 창이 `range:N`일 때만 프롬프트의 기간 표현(`time_slot`) — 나머지 창
      (오늘·어제·비교·없음)은 규칙 해석이 맡으므로 null
    - `query_targets`는 비운다(종전 요약 문장 응답과 같은 2회 시도 — APM 경로는 쓰지 않는다)
    """
    hosts = [mapping[n] for n in placeholders_of(prompt_template)
             if n.startswith("host_") and n in mapping]
    return {
        "query_targets": [],
        "filter_conditions": [{"field": "hostname", "op": "=", "value": h}
                              for h in dict.fromkeys(hosts)],
        "time_range": None,
        "aggregation": None,
        "limit": None,
        "output_format": "text",
        "target_db_hints": [],
        "time_expr": time_slot(prompt) if expect.window.startswith("range:") else None,
    }


def _fenced(payload: Any) -> str:
    """JSON 을 ```json 울타리로 감싼다 — 평문 파싱·MD_JSON 구조화 경로 둘 다 읽는다."""
    return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


class FakeLLM:
    """127.0.0.1 가짜 OpenAI 호환 서버(스레드).

    드라이버는 턴마다 `set_plan`으로 그 턴의 계획을 넣는다. 재선택 응답은 계획의 APM task
    보기·조건이
    기본이고 `set_reselect`로 덮어쓴다.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._host = host
        self._port = port
        self._lock = threading.Lock()
        self._plan: dict[str, Any] | None = None
        self._reselect: dict[str, Any] | None = None
        self._parse: dict[str, Any] | None = None
        self.requests: list[FakeRequest] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # --- 수명 ---------------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("가짜 LLM 서버가 기동 전이다")
        return f"http://{self._host}:{self._server.server_address[1]}/v1"

    def start(self) -> str:
        """서버를 띄우고 `/v1` base_url 을 돌려준다."""
        owner = self

        class Handler(_Handler):
            fake = owner

        self._server = ThreadingHTTPServer((self._host, self._port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.base_url

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    # --- 턴 입력 ------------------------------------------------------------------------

    def set_plan(self, plan: dict[str, Any] | None) -> None:
        """다음 분해 요청에 줄 계획(None 이면 빈 tasks → 본체 폴백)."""
        with self._lock:
            self._plan = plan
            self._reselect = None

    def set_reselect(self, selection: dict[str, Any] | None) -> None:
        """보기 재선택 응답 덮어쓰기(None = 계획의 APM task 보기·조건)."""
        with self._lock:
            self._reselect = selection

    def set_parse(self, parsed: dict[str, Any] | None) -> None:
        """입력 파싱 요청에 줄 JSON(None = 요약 문장 — 파싱 결과 없음)."""
        with self._lock:
            self._parse = parsed

    def kinds_since(self, index: int) -> list[str]:
        """`index`번째 이후 받은 요청 종류."""
        with self._lock:
            return [r.kind for r in self.requests[index:]]

    # --- 응답 ---------------------------------------------------------------------------

    def answer(self, body: Mapping[str, Any]) -> tuple[str, str]:
        """요청 본문 → (종류, 응답 텍스트)."""
        messages = [m for m in body.get("messages") or [] if isinstance(m, Mapping)]
        system = next((_text(m.get("content")) for m in messages if m.get("role") == "system"), "")
        user = next(
            (_text(m.get("content")) for m in reversed(messages) if m.get("role") == "user"), ""
        )
        kind = next((k for marker, k in MARKERS if marker in system), "other")
        if body.get("max_tokens") == 1 and not system:
            kind = "probe"
        with self._lock:
            self.requests.append(
                FakeRequest(kind, system[:80], user[:USER_HEAD], bool(body.get("stream")),
                            time.monotonic())
            )
            plan, reselect, parsed = self._plan, self._reselect, self._parse
        if kind == "decompose":
            return kind, _fenced(plan if plan is not None else {"tasks": []})
        if kind == "reselect":
            if reselect is None:
                apm = next((t for t in (plan or {}).get("tasks") or []
                            if isinstance(t, dict) and t.get("agent") == APM_AGENT), {})
                reselect = {"views": list(apm.get("views") or []),
                            "view_args": dict(apm.get("view_args") or {})}
            return kind, _fenced(reselect)
        if kind == "replan":
            return kind, _fenced({"needs_followup": False, "new_tasks": [], "reason": "충분"})
        if kind == "probe":
            return kind, "1"
        if kind == "parse" and parsed is not None:
            return kind, _fenced(parsed)
        return kind, SUMMARY_TEXT


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence):
        return "".join(str(p.get("text") or "") for p in content if isinstance(p, Mapping))
    return ""


class _Handler(BaseHTTPRequestHandler):
    fake: FakeLLM
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # 표준 오류 출력 억제
        pass

    def _json(self, status: int, payload: Any) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            self._json(200, {"object": "list", "data": [{"id": MODEL_ID, "object": "model"}]})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid json"})
            return
        _kind, text = self.fake.answer(body)
        model = str(body.get("model") or MODEL_ID)
        cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())
        usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        if not body.get("stream"):
            self._json(200, {
                "id": cid, "object": "chat.completion", "created": created, "model": model,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": text}}],
                "usage": usage,
            })
            return
        chunks = [
            {"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None},
            {"index": 0, "delta": {}, "finish_reason": "stop"},
        ]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        for choice in chunks:
            event = {"id": cid, "object": "chat.completion.chunk", "created": created,
                     "model": model, "choices": [choice]}
            self.wfile.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode())
        if (body.get("stream_options") or {}).get("include_usage"):
            event = {"id": cid, "object": "chat.completion.chunk", "created": created,
                     "model": model, "choices": [], "usage": usage}
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


__all__ = ["MARKERS", "MODEL_ID", "FakeLLM", "FakeRequest", "parse_from_turn", "plan_from_expect",
           "time_slot"]
