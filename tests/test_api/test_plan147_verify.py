"""plans/147 독립 검증(verifier) — 글 답 규칙 반례 · 단어 반례 · 관리 API 인증 · SSE 종료 노드 쓰기.

- 글 답(`_apm_source_text_answer`): 정상 질문이 답으로 오인되지 않는지 · 「〇〇 전체」가 전 소스로
  넓혀지지 않아야 한다(D-290 ⑥) — 현재 결함은 strict xfail로 고정한다(고치면 xfail 제거).
- 소스 단어(`match_source_terms`): D-271 제외어 · 라틴 경계 반례.
- 관리 API `/admin/apm/sources`: 사용자 토큰의 role 클레임 위조 · 시크릿 교차 서명 거부.
- SSE: 되묻기 턴의 스레드 칸(`apm_source_pending`)은 체크포인트 쓰기가 느려도 남는다(드레인) ·
  APM 칸이 없는 턴의 종료 노드 쓰기(AI 메시지)는 유실된다 — 이 계획 밖 기존 결함(strict xfail).

실 LLM·네트워크 0. 설정은 `_env_file=None`(구현 테스트 `_config`)으로 `.env` 누수를 막는다.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import jwt
import pytest

from src.routing import apm_source_select as sel
from src.routing.registry import get_registry

USER: dict[str, Any] = {"sub": "u1", "role": "user"}
PENDING = {"query": "WAS 응답시간 알려줘", "choices": ["bank", "common", "legacy", "*"]}


def _answer(text: str):
    from src.api.routes.query import _apm_source_text_answer

    return _apm_source_text_answer(text, PENDING, USER)


# ── 1. 글 답 규칙 — 오탐(정상 질문을 답으로) ──────────────────────────────────────

@pytest.mark.apm_source_ladder
@pytest.mark.parametrize("text", [
    "공동존 WAS 응답시간", "김포 WAS 힙 추세", "은행존 WAS 응답시간", "전체 서버 CPU",
    "전체 말고 공동존", "운영체제", "운영 중", "운영자", "운영팀", "DRM", "개발자",
    "레거시 서버 목록", "공동존 서버 CPU와 WAS 응답시간",
])
def test_question_like_text_is_not_an_answer(text: str) -> None:
    assert _answer(text) is None


@pytest.mark.apm_source_ladder
@pytest.mark.parametrize(("text", "expected"), [
    ("DR", (["common"], None)), ("dr로", (["common"], None)), ("K리전", (["bank"], None)),
    ("레거시요", (["legacy"], None)), ("은행", (None, "은행")),
    ("공동존 제니퍼로 조회해 주세요", (["common"], None)),
])
def test_short_answers(text: str, expected) -> None:
    assert _answer(text) == expected


@pytest.mark.apm_source_ladder
@pytest.mark.parametrize("text", ["공동존 전체", "레거시 전체요", "전체 김포"])
def test_named_source_plus_all_word_does_not_widen(text: str) -> None:
    assert _answer(text) != (["*"], None)


# ── 2. 소스 단어 반례(D-271 제외어 · 라틴 경계) ─────────────────────────────────────

@pytest.mark.apm_source_ladder
@pytest.mark.parametrize(("text", "ids"), [
    ("운영체제별 WAS 응답시간", ()), ("운영 중인 WAS 응답시간", ()), ("운영자 WAS 목록", ()),
    ("운영팀 WAS", ()), ("DRM 서버 WAS 응답시간", ()), ("ADDRESS WAS", ()),
    ("DR 서버 WAS 응답시간", ("common",)), ("dr서버 WAS", ("common",)),
    ("운영 중요 WAS", ("common",)), ("은행존 제니퍼 WAS", ("bank",)),
    ("레거시 제니퍼와 은행존 제니퍼", ("bank", "legacy")),
])
def test_source_term_counterexamples(text: str, ids: tuple[str, ...]) -> None:
    got = sel.match_source_terms(text, get_registry().sources_of("apm")).source_ids
    assert got == ids


@pytest.mark.apm_source_ladder
def test_two_single_source_words_ask_between_them() -> None:
    """현행 동작 고정(§4.1 단 2 문언) — 서로 다른 단일 소스 단어 둘도 되묻는다(복수 지목 글 답
    포함)."""
    d = sel.select_apm_sources(get_registry().sources_of("apm"), text="공동존과 레거시")
    assert d.action == "ask" and d.choices == ("common", "legacy")


# ── 3. 요청 경계 — 대량·이상 입력 ───────────────────────────────────────────────

@pytest.mark.apm_source_ladder
def test_selection_authorization_large_and_odd_input() -> None:
    from src.api.routes.query import apply_apm_source_selection_authorization as authz

    # 개수 상한(앞 32개만 본다 — W2 교정 3)이 있어 이상 값·유효 값을 앞에, 대량 잡음을 뒤에 둔다
    junk = ["<script>", "", "*", "common", "COMMON", " common"] + [f"x{i}" for i in range(5000)]
    assert authz(junk, USER) == ["*", "common"]
    assert authz(["common"], {"sub": "u", "role": "user", "allowed_sources": ["polestar"]}) is None


# ── 4. 관리 API 인증 — 클레임 위조 · 시크릿 교차 ────────────────────────────────────

def test_admin_api_rejects_forged_role_claim_and_cross_signed_tokens(
        monkeypatch, available) -> None:
    from tests.test_api import test_plan147_w4_admin_startup as w4

    gw = w4._use(monkeypatch, w4._Gateway(w4._health("bank", "common", "legacy")))
    client = w4._client(w4._config(auth=True))
    url = "/api/v1/admin/apm/sources?refresh=1"
    # 사용자 시크릿 · type=user · role 클레임만 admin(DB 실 role = user) → 403
    forged_role = jwt.encode({"sub": "u1", "name": "U", "role": "admin", "type": "user",
                              "exp": w4._exp()}, w4.AUTH_SECRET, algorithm="HS256")
    assert client.get(url, headers={"Authorization": f"Bearer {forged_role}"}).status_code == 403
    # type=admin 을 사용자 시크릿으로 서명 → 운영자 경로·사용자 경로 모두 거부
    cross = jwt.encode({"sub": "ops", "type": "admin", "exp": w4._exp()}, w4.AUTH_SECRET,
                       algorithm="HS256")
    assert client.get(url, headers={"Authorization": f"Bearer {cross}"}).status_code in (401, 403)
    # 정상 운영자 토큰은 통과하고 응답에 URL·토큰이 없다
    ok = client.get(url, headers={"Authorization": f"Bearer {w4._admin_token()}"})
    assert ok.status_code == 200
    body = ok.text
    assert "http" not in body and "9096" not in body and "token" not in body.lower()
    assert len(gw.calls) == 1, "거부된 두 요청은 재점검을 부르지 않는다"


@pytest.fixture
def available():
    sel.set_available_apm_sources(None)
    yield
    sel.set_available_apm_sources(None)


# ── 5. SSE 종료 노드 체크포인트 쓰기 ─────────────────────────────────────────────

def _slow_saver_cls(delay: float):
    from langgraph.checkpoint.memory import MemorySaver

    class _Slow(MemorySaver):
        async def aput(self, *a, **k):
            await asyncio.sleep(delay)
            return await super().aput(*a, **k)

        async def aput_writes(self, *a, **k):
            await asyncio.sleep(delay)
            return await super().aput_writes(*a, **k)

    return _Slow


@pytest.fixture
def slow_env(monkeypatch):
    """구현 테스트(W2 라우트) 장치 그대로 + 체크포인트 쓰기 지연(실 저장소 I/O 지연 대역)."""
    import langgraph.checkpoint.memory as mem
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.dependencies import require_user
    from src.api.routes import query as query_routes
    from src.infrastructure import apm_job_store as store_mod
    from src.infrastructure.apm_job_store import ApmJobStore
    from src.orchestration import apm_query as aq
    from tests.test_api import test_plan147_w2_route as w2

    monkeypatch.setattr(store_mod, "_STORE", ApmJobStore(None))
    gw = w2._Gateway()
    monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
    monkeypatch.setattr(mem, "MemorySaver", _slow_saver_cls(0.2))
    cfg = w2._config()
    compiled = w2._graph(cfg)
    app = FastAPI()
    app.state.config = cfg
    app.state.graph = compiled
    app.include_router(query_routes.router, prefix="/api/v1")
    app.dependency_overrides[require_user] = lambda: dict(USER)
    return TestClient(app), compiled, gw


def _sse(client, query: str, thread: str) -> dict:
    r = client.post("/api/v1/query/stream", json={"query": query, "thread_id": thread})
    assert r.status_code == 200, r.text
    events = [json.loads(x[6:]) for x in r.text.splitlines() if x.startswith("data: ")]
    (done,) = [e for e in events if e["type"] == "done"]
    return done


@pytest.mark.apm_source_ladder
def test_sse_ask_turn_pending_survives_slow_checkpoint(slow_env) -> None:
    client, graph, _gw = slow_env
    done = _sse(client, "WAS 응답시간 알려줘", "p147v-slow-ask")
    assert done["apm_source_clarification"]["options"]
    values = graph.get_state({"configurable": {"thread_id": "p147v-slow-ask"}}).values
    assert values.get("apm_source_pending", {}).get("query") == "WAS 응답시간 알려줘"


@pytest.mark.xfail(strict=True, reason="기존 결함(이 계획 밖): SSE가 done 직후 생산자를 취소해 "
                                       "종료 노드(result_aggregator)의 체크포인트 쓰기가 유실된다")
def test_sse_terminal_node_messages_survive_slow_checkpoint(slow_env) -> None:
    """사다리 off(conftest가 unscoped 고정) — APM 칸이 없어 드레인하지 않는 일반 턴."""
    client, graph, _gw = slow_env
    _sse(client, "WAS 응답시간 알려줘", "p147v-slow-plain")
    values = graph.get_state({"configurable": {"thread_id": "p147v-slow-plain"}}).values
    assert [m for m in values.get("messages") or [] if m.type == "ai"], "AI 답이 대화 기록에 남는다"


# ── 6. 복합 질의 — 폴스타 위치어가 APM task 소스 판정을 오염시키는가 ─────────────────────

@pytest.mark.apm_source_ladder
def test_composite_polestar_location_word_does_not_override_apm_task_source() -> None:
    from src.orchestration import apm_query as aq

    q = "김포 서버 CPU와 레거시 WAS 응답시간"
    task = {"task_id": "t2", "agent": "apm_query", "sub_query": "레거시 WAS 응답시간"}
    d = aq._source_decision(task, {"original_user_query": q, "user_query": task["sub_query"]}, ())
    assert (d.action, d.source_ids) == ("query", ("legacy",))
