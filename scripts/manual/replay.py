"""실 파이프라인 이벤트 녹화·재생 (plans/116 §4.9.3 ①).

녹화: 실 그래프의 ``astream_events`` 를 그대로 흘려보내면서, ``src/api/routes/query.py`` 가
소비하는 이벤트만 골라 JSON 으로 저장한다. 재생: 같은 이벤트를 다시 내보낸다. 라우트는 재생
그래프를 실 그래프와 구별하지 못하므로 결과 저장·다운로드·이력·역질문 패널이 실제 경로로 돈다.

키 = (질의 문구, 첨부 파일 sha256 앞 12자, 폼필 답변 유무). 사례 수집기(``samples``)가 같은
함수로 파일명을 계산해 사례 ID 와 잇는다.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as _dt
import decimal
import hashlib
import json
import logging
import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

logger = logging.getLogger("manual.replay")

# 녹화 대상 이벤트 종류 — query.py 가 읽는 것만
_CHAIN = ("on_chain_start", "on_chain_end")
_PROGRESS = ("on_tool_start", "on_tool_end", "on_custom_event")
_TOKEN = "on_chat_model_stream"
_TOKEN_NODES = ("output_generator", "general_inference")
_USER_RESPONSE_TAG = "user_response"  # src/llm.py USER_RESPONSE_TAG 와 같은 값

_LIST_CAP_NODE = 50  # 중간 노드 출력의 리스트 상한(처리 현황 미리보기는 10행)
_LIST_CAP_FINAL = 500  # 최종 출력 상한(CSV 다운로드용)


def record_key(query: str, file_bytes: bytes | None, has_answers: bool) -> str:
    """녹화 파일명 키(16자)."""
    fh = hashlib.sha256(file_bytes).hexdigest()[:12] if file_bytes else ""
    raw = f"{query.strip()}\x1f{fh}\x1f{int(bool(has_answers))}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _key_of(input_state: dict) -> str:
    return record_key(
        str(input_state.get("user_query") or ""),
        input_state.get("uploaded_file"),
        bool(input_state.get("form_fill_answers")),
    )


# ── 직렬화 ────────────────────────────────────────────────────────────────


def encode(obj: Any, cap: int) -> Any:
    """JSON 가능 형태로 바꾼다(바이트·메시지·Decimal·datetime 보존 표지)."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, bytes):
        return {"__bytes__": base64.b64encode(obj).decode("ascii")}
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    if isinstance(obj, (_dt.datetime, _dt.date, _dt.time)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): encode(v, cap) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        items = list(obj)[:cap]
        return [encode(v, cap) for v in items]
    content = getattr(obj, "content", None)
    msg_type = getattr(obj, "type", None)
    if isinstance(msg_type, str) and content is not None:
        return {"__msg__": msg_type, "content": encode(content, cap)}
    return str(obj)


def decode(obj: Any) -> Any:
    """``encode`` 의 역변환."""
    if isinstance(obj, list):
        return [decode(v) for v in obj]
    if isinstance(obj, dict):
        if "__bytes__" in obj and len(obj) == 1:
            return base64.b64decode(obj["__bytes__"])
        if "__msg__" in obj:
            from langchain_core.messages import AIMessage, HumanMessage

            cls = HumanMessage if obj["__msg__"] == "human" else AIMessage
            return cls(content=obj.get("content") or "")
        return {k: decode(v) for k, v in obj.items()}
    return obj


class _Chunk:
    """토큰 청크 대역 — query.py ``_token_text`` 는 ``.content`` 만 읽는다."""

    def __init__(self, content: str) -> None:
        self.content = content


def _slim(event: dict) -> dict | None:
    """녹화할 이벤트면 축약본을, 아니면 None."""
    kind = event.get("event", "")
    name = event.get("name", "") or ""
    depth = len(event.get("parent_ids") or ())
    if kind in _CHAIN:
        data = event.get("data") or {}
        out = data.get("output") if kind == "on_chain_end" else None
        final = isinstance(out, dict) and "final_response" in out and depth <= 1
        if depth > 1 and not final:
            return None
        rec = {"event": kind, "name": name, "depth": depth}
        if isinstance(out, dict):
            rec["output"] = encode(out, _LIST_CAP_FINAL if final else _LIST_CAP_NODE)
        return rec
    if kind in _PROGRESS:
        data = event.get("data") or {}
        keep: dict = {}
        if kind == "on_tool_start" and isinstance(data.get("input"), dict):
            sq = data["input"].get("sub_query")
            if isinstance(sq, str):
                keep["input"] = {"sub_query": sq}
        elif kind == "on_custom_event" and isinstance(data, dict):
            keep = encode(data, _LIST_CAP_NODE)
        return {"event": kind, "name": name, "depth": depth, "data": keep}
    if kind == _TOKEN:
        tags = event.get("tags") or []
        node = (event.get("metadata") or {}).get("langgraph_node", "")
        if _USER_RESPONSE_TAG in tags or node in _TOKEN_NODES:
            chunk = (event.get("data") or {}).get("chunk")
            text = getattr(chunk, "content", "") if chunk is not None else ""
            if isinstance(text, str) and text:
                return {
                    "event": kind,
                    "name": name,
                    "depth": depth,
                    "node": node,
                    "tags": [_USER_RESPONSE_TAG],
                    "content": text,
                }
    return None


def _expand(rec: dict) -> dict:
    """축약본 → LangGraph v2 이벤트 모양."""
    depth = int(rec.get("depth") or 0)
    ev: dict = {
        "event": rec["event"],
        "name": rec.get("name", ""),
        "parent_ids": ["p"] * depth,
        "tags": list(rec.get("tags") or []),
    }
    if rec["event"] == _TOKEN:
        ev["metadata"] = {"langgraph_node": rec.get("node", "")}
        ev["data"] = {"chunk": _Chunk(rec.get("content", ""))}
    elif "output" in rec:
        ev["data"] = {"output": decode(rec["output"])}
    else:
        ev["data"] = decode(rec.get("data") or {})
    return ev


# ── 녹화 ─────────────────────────────────────────────────────────────────


class RecordingGraph:
    """실 그래프 프록시 — 이벤트를 통과시키며 녹화한다."""

    def __init__(self, inner: Any, out_dir: Path) -> None:
        self._inner = inner
        self._out = Path(out_dir)
        self._out.mkdir(parents=True, exist_ok=True)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)

    async def astream_events(
        self, input_state: dict, config: dict, **kw: Any
    ) -> AsyncGenerator[dict, None]:
        key = _key_of(input_state)
        recs: list[dict] = []
        state = {"final": False}

        def save() -> None:
            doc = {
                "key": key,
                "query": input_state.get("user_query"),
                "has_file": bool(input_state.get("uploaded_file")),
                "has_answers": bool(input_state.get("form_fill_answers")),
                "recorded_at": _dt.datetime.now().isoformat(timespec="seconds"),
                "events": recs,
                "complete": state["final"],
            }
            path = self._out / f"{key}.json"
            path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
            logger.info("녹화 저장 %s (이벤트 %d · 완료=%s)", path.name, len(recs), state["final"])

        # 라우트는 최종 이벤트를 받자마자 return 한다 — 이 제너레이터는 끝까지 돌지 않을 수 있으므로
        # 최종 이벤트를 녹화한 순간 저장하고, 중단·예외 때도 finally 에서 한 번 더 저장한다.
        try:
            async for ev in self._inner.astream_events(input_state, config, **kw):
                try:
                    rec = _slim(ev)
                    if rec is not None:
                        recs.append(rec)
                        out = rec.get("output")
                        if (
                            isinstance(out, dict)
                            and "final_response" in out
                            and rec.get("depth", 0) <= 1
                        ):
                            state["final"] = True
                            save()
                except Exception:  # 녹화 실패가 실 응답을 막으면 안 된다
                    logger.exception("녹화 축약 실패 — 이 이벤트는 건너뜀")
                yield ev
        finally:
            save()


# ── 재생 ─────────────────────────────────────────────────────────────────


class ReplayGraph:
    """녹화 파일을 재생하는 그래프 대역. 키가 없으면 그 사실을 응답으로 드러낸다."""

    def __init__(self, fixtures_dir: Path, delay: float = 0.03) -> None:
        self._dir = Path(fixtures_dir)
        self._delay = delay

    def _load(self, key: str) -> dict | None:
        path = self._dir / f"{key}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    async def astream_events(
        self, input_state: dict, config: dict, **kw: Any
    ) -> AsyncGenerator[dict, None]:
        key = _key_of(input_state)
        doc = self._load(key)
        if doc is None:
            logger.error("재생 녹화 없음 key=%s query=%r", key, input_state.get("user_query"))
            yield {
                "event": "on_chain_end",
                "name": "LangGraph",
                "parent_ids": [],
                "tags": [],
                "data": {
                    "output": {
                        "final_response": f"[매뉴얼 캡처] 녹화 없음 — key={key}",
                        "messages": [],
                        "query_results": [],
                    }
                },
            }
            return
        for rec in doc["events"]:
            await asyncio.sleep(self._delay if rec["event"] != _TOKEN else self._delay / 6)
            yield _expand(rec)

    async def ainvoke(self, input_state: dict, config: dict) -> dict:
        doc = self._load(_key_of(input_state)) or {"events": []}
        for rec in reversed(doc["events"]):
            out = rec.get("output")
            if isinstance(out, dict) and "final_response" in out:
                return decode(out)
        return {"final_response": "[매뉴얼 캡처] 녹화 없음", "messages": []}

    def get_state(self, config: dict) -> None:
        return None

    async def aget_state(self, config: dict) -> None:
        return None


def wrap_for_mode(graph: Any) -> Any:
    """``MANUAL_MODE`` 환경변수에 따라 그래프를 감싼다(record|replay)."""
    mode = os.environ.get("MANUAL_MODE", "replay")
    fixtures = Path(os.environ["MANUAL_FIXTURES_DIR"])
    if mode == "record":
        return RecordingGraph(graph, fixtures)
    return ReplayGraph(fixtures)
