"""FabriX KBGenAI 목업 (Plan 80 WU-05 로직 검증용).

**왜 transport 레벨인가.** `KBGenAIChat._agenerate`는 `httpx.AsyncClient`로 실제 HTTP를 친다.
그 경계에서 갈아끼우면 **클라이언트 코드 전체가 그대로 실행된다** — 페이로드 조립
(`modelId`·`contents`·`systemPrompt`), `status != SUCCESS` 오류 규약, `remove_llm_junk`
후처리, PII 필터 훅(`log_filter_block_if_any`)까지. LLM 객체 자체를 대역으로 바꾸면
이 로직이 전부 우회된다.

**실 호출 0건 · 과금 0** — D-127 게이트와 무관하다.

결함 주입(fault injection)이 이 목업의 핵심 용도다. 하네스가 **회귀를 실제로 잡아내는지**
증명하지 못하면, 승인을 받아 실행해도 거짓 통과를 얻을 수 있다.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Callable, Optional

import httpx

# 질의 → (intent, [(db_id, score), ...]) 정답 대본.
# `testdata/routing_gold/routing.yaml`의 expect와 정합해야 한다.
SCRIPT: dict[str, tuple[str, list[tuple[str, float]]]] = {
    "여의도 개발 서버들의 CPU 사용률을 보여줘": ("data_query", [("polestar_cm_yd", 0.95)]),
    "김포 운영 서버 목록과 메모리 사양 알려줘": ("data_query", [("polestar_cm_gp", 0.93)]),
    "은행존 서버의 디스크 사용량 조회": ("data_query", [("polestar_b0", 0.94)]),
    "김포와 여의도 서버의 CPU 사용률을 비교해줘": (
        "data_query", [("polestar_cm_gp", 0.92), ("polestar_cm_yd", 0.90)]),
    "전체 존의 서버 대수와 VM 대수를 합쳐서 알려줘": (
        "data_query", [("polestar_b0", 0.85), ("polestar_cm_gp", 0.84),
                       ("polestar_cm_yd", 0.83), ("cloud_portal", 0.80)]),
    "인시던트가 발생한 서버들의 자산 정보와 현재 사양을 보여줘": (
        "data_query", [("itsm", 0.88), ("itam", 0.82)]),
    "현재 활성 상태인 심각 알람 목록 보여줘": ("alarm_query", [("polestar_b0", 0.9)]),
    "이번 달 CPU 임계값 초과 알람 이력 조회": ("alarm_query", [("polestar_b0", 0.88)]),
    "polestar DB의 스키마 캐시를 갱신해줘": ("cache_management", []),
    "조회 가능한 DB 목록과 설명을 알려줘": ("cache_management", []),
    "안녕하세요, 오늘 날씨 어때요?": ("general_inference", []),
    # A-1 노출 확대 — 저신뢰 대역이 실제로 나오는지 관측하는 케이스
    "서버 상태 좀 확인해줘": ("data_query", [("polestar_b0", 0.45), ("polestar_cm_gp", 0.38)]),
    "그 장비 정보": ("data_query", [("polestar_b0", 0.35)]),
}

# ── 결함 주입 모드 ───────────────────────────────────────────────────
FAULT_NONE = "none"
FAULT_COLLAPSE_MULTI = "collapse_multi"   # 멀티 DB를 1개로 축소 (불변식 ⑩ 위배)
FAULT_BAD_INTENT = "bad_intent"           # 오타 intent 산출 (F1)
FAULT_BAD_SCORE = "bad_score"             # relevance_score를 "높음"으로 (F2)
FAULT_MALFORMED = "malformed"             # JSON이 아닌 응답
FAULT_ERROR_STATUS = "error_status"       # status != SUCCESS


def _extract_query(payload: dict) -> str:
    """KBGenAI 페이로드의 `contents`에서 사용자 질의를 뽑는다."""
    contents = payload.get("contents") or []
    for c in reversed(contents):
        text = str(c).strip()
        if text:
            return text.split("\n")[-1].strip()
    return ""


def build_body(query: str, *, fault: str = FAULT_NONE) -> dict:
    """질의 하나에 대한 KBGenAI 응답 본문을 만든다."""
    if fault == FAULT_ERROR_STATUS:
        return {"status": "ERROR", "content": ""}

    intent, dbs = SCRIPT.get(query, ("data_query", [("polestar_b0", 0.5)]))

    if fault == FAULT_BAD_INTENT:
        intent = "prosess_query"
    if fault == FAULT_COLLAPSE_MULTI and len(dbs) > 1:
        dbs = dbs[:1]

    entries = []
    for i, (db_id, score) in enumerate(dbs):
        value: Any = score
        if fault == FAULT_BAD_SCORE and i == 0:
            value = "높음"
        entries.append({
            "db_id": db_id,
            "relevance_score": value,
            "sub_query_context": f"{db_id} 대상 조회",
            "user_specified": False,
            "reason": "목업 응답",
        })

    if fault == FAULT_MALFORMED:
        return {"status": "SUCCESS", "content": "죄송합니다, 응답을 생성할 수 없습니다."}

    body = json.dumps({"intent": intent, "databases": entries}, ensure_ascii=False)
    return {"status": "SUCCESS", "content": f"```json\n{body}\n```"}


def make_handler(
    *, fault: str = FAULT_NONE, on_request: Optional[Callable[[dict], None]] = None
) -> Callable[[httpx.Request], httpx.Response]:
    """`httpx.MockTransport`용 핸들러를 만든다."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        if on_request is not None:
            on_request(payload)
        return httpx.Response(200, json=build_body(_extract_query(payload), fault=fault))

    return handler


@contextmanager
def mock_kbgenai(*, fault: str = FAULT_NONE, on_request=None):
    """`KBGenAIChat`의 HTTP 경계만 갈아끼운다 — 클라이언트 로직은 그대로 실행된다."""
    import src.clients.fabrix_kbgenai as mod

    original = mod.httpx.AsyncClient
    transport = httpx.MockTransport(make_handler(fault=fault, on_request=on_request))

    def factory(*_a, **kw):
        kw.pop("verify", None)
        return original(transport=transport, **kw)

    mod.httpx.AsyncClient = factory
    try:
        yield
    finally:
        mod.httpx.AsyncClient = original


def make_llm():
    """목업과 함께 쓸 `KBGenAIChat` 인스턴스(실제 클래스다)."""
    from src.clients.fabrix_kbgenai import KBGenAIChat

    return KBGenAIChat(
        endpoint_url="https://mock.fabrix.invalid/v1/chat",
        x_openapi_token="mock-token",
        x_generative_ai_client="mock-client",
        asset_id="mock-model",
        kb_id="User",
        system_prompt="",
    )
