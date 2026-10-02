"""Plan 64 Wave 3-B (CW-B) — 장애 진단 pull 위임(fault_diagnosis) 계약·라우팅 테스트.

검증 범위(sre_agent 패키지 미import — MCP 클라이언트 계약을 모사한 FakeSreClient 사용):
  1. 노드 위임: diagnose→poll→자연어 진단 응답(final_response)·routing_intent 세팅.
  2. graceful: 서비스 다운/타임아웃/거부/미가용 시 사유 담은 응답(침묵 금지)·크래시 없음.
  3. 옵트인: fault_diagnosis_enabled off면 위임하지 않고 안내 응답(no client build).
  4. 대상 추출: parsed_requirements/conversation_context에서 server/host/db_id 추출.
  5. 프롬프트: fault_diagnosis 섹션은 옵트인 on일 때만 노출(off면 비트동일).
  6. 라우터: off면 fault_diagnosis 의도를 data_query로 강등(비트동일)·on이면 유지.
  7. D-004 3곳 대칭: _INTENT_ROUTE_MAP·build_graph 노드 등록·conditional_edges.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import src.nodes.fault_diagnosis as fd
from src.config import AppConfig, MultiDBConfig
from src.graph import _INTENT_ROUTE_MAP, build_graph, route_after_semantic_router
from src.nodes.fault_diagnosis import _extract_targets, fault_diagnosis
from src.prompts.semantic_router import SEMANTIC_ROUTER_SYSTEM_PROMPT_TEMPLATE
from src.routing.domain_config import DB_DOMAINS
from src.routing.semantic_router import _build_router_prompt, semantic_router
from src.state import create_initial_state


# ── 계약 페이크 ──────────────────────────────────────────────────────────
class FakeSreClient:
    """sre_agent MCP 계약(diagnose/poll)을 모사한 인메모리 페이크(패키지 import 0)."""

    def __init__(
        self, poll_result=None, sub_status="accepted",
        connect_error=None, never_terminal=False,
    ) -> None:
        self.poll_result = poll_result if poll_result is not None else {
            "status": "done", "answer": "web-01 메모리 고갈로 java(pid 12345) OOM 종료.",
        }
        self.sub_status = sub_status
        self.connect_error = connect_error
        self.never_terminal = never_terminal
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.diagnose_calls: list[dict] = []
        self.poll_calls: list[str] = []

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_error is not None:
            raise self.connect_error

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    async def diagnose(self, question, server_name=None, hostname=None, db_id=None) -> dict:
        self.diagnose_calls.append(
            {"question": question, "server_name": server_name,
             "hostname": hostname, "db_id": db_id}
        )
        if self.sub_status == "rejected":
            return {"status": "rejected", "investigation_id": None, "reason": "계약 위반"}
        return {"status": self.sub_status, "investigation_id": "inv-1"}

    async def poll(self, investigation_id: str) -> dict:
        self.poll_calls.append(investigation_id)
        if self.never_terminal:
            return {"status": "running"}
        return self.poll_result


def _gate_cfg(**over):
    from types import SimpleNamespace
    base = dict(
        fault_diagnosis_enabled=True,
        investigation_service_url="http://localhost:9098/sse",
        investigation_service_token="",
        investigation_mcp_call_timeout_seconds=10.0,
        investigation_poll_interval_seconds=0.0,
        investigation_total_timeout_seconds=5.0,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _app_cfg(gate, *, authz_mode: str = "admin_only"):
    """앱 설정 대역.

    `host_authz`·`composite`는 실제 `AppConfig`가 **항상** 갖는다(Plan 78 W3-5 · W1).
    빼면 조사 인가가 `unknown_authz_mode`로 차단된다 — fail-closed의 설계 의도이므로
    정책이 아니라 대역을 프로덕션 형태에 맞춘다.
    """
    from types import SimpleNamespace
    return SimpleNamespace(
        noise_gate=gate,
        host_authz=SimpleNamespace(mode=authz_mode),
        # 가용성 사전 판정(Plan 81)은 **명시적으로 꺼 둔다** — 이 파일은 위임 계약을 본다.
        # 판정 경로는 `tests/test_composite/test_fault_diagnosis_availability.py`가 따로 본다
        # (테스트 config는 검증 대상 필드를 명시한다 — Known Mistakes).
        composite=SimpleNamespace(
            prior_targets_enabled=False,
            max_targets=10,
            availability_precheck_enabled=False,
            availability_block_on_unavailable=False,
        ),
    )


def _state(**extra) -> dict:
    st = {
        "user_query": "web-01 서버 장애 원인 분석해줘",
        "active_db_id": "polestar_b0",
        "parsed_requirements": {
            "filter_conditions": [{"field": "hostname", "value": "web-01"}]
        },
        "conversation_context": None,
        # 조사 인가는 **위임 직전**에 판정한다(Plan 78 W3-5) — role이 없으면 그 앞에서
        # 차단되므로, 위임 이후 동작을 보는 테스트는 인가된 주체를 명시해야 한다.
        # 미인가 경로는 `TestHostAuthz`가 따로 본다.
        "user_role": "admin",
        "user_id": "admin-1",
    }
    st.update(extra)
    return st


def _patch_client(monkeypatch, client) -> None:
    monkeypatch.setattr(fd, "_build_client", lambda gate_cfg: client)


# ── 1. 노드 위임 ─────────────────────────────────────────────────────────
class TestNodeDelegates:
    async def test_diagnose_poll_returns_text(self, monkeypatch):
        client = FakeSreClient()
        _patch_client(monkeypatch, client)
        out = await fault_diagnosis(_state(), app_config=_app_cfg(_gate_cfg()))
        assert out["final_response"] == "web-01 메모리 고갈로 java(pid 12345) OOM 종료."
        assert out["routing_intent"] == "fault_diagnosis"
        assert out["current_node"] == "fault_diagnosis"
        assert out.get("messages")  # 멀티턴 누적
        # diagnose가 question·hostname·db_id를 실어 위임했는지.
        assert client.diagnose_calls[0]["question"] == "web-01 서버 장애 원인 분석해줘"
        assert client.diagnose_calls[0]["hostname"] == "web-01"
        assert client.diagnose_calls[0]["db_id"] == "polestar_b0"
        assert client.connect_calls == 1 and client.disconnect_calls == 1
        assert client.poll_calls == ["inv-1"]

    async def test_briefing_dict_rendered_to_text(self, monkeypatch):
        client = FakeSreClient(poll_result={
            "status": "done",
            "briefing": {"cause": "OOM으로 종료", "recommendation": "힙 상향"},
        })
        _patch_client(monkeypatch, client)
        out = await fault_diagnosis(_state(), app_config=_app_cfg(_gate_cfg()))
        assert "[원인] OOM으로 종료" in out["final_response"]
        assert "[권고] 힙 상향" in out["final_response"]

    async def test_stub_briefing_message(self, monkeypatch):
        client = FakeSreClient(poll_result={
            "status": "stub", "briefing": {"stub": True, "message": "조사 미실행(스텁)"},
        })
        _patch_client(monkeypatch, client)
        out = await fault_diagnosis(_state(), app_config=_app_cfg(_gate_cfg()))
        assert out["final_response"] == "조사 미실행(스텁)"


# ── 2. graceful ──────────────────────────────────────────────────────────
class TestGraceful:
    async def test_service_down_reason_response(self, monkeypatch):
        client = FakeSreClient(connect_error=ConnectionRefusedError("down"))
        _patch_client(monkeypatch, client)
        out = await fault_diagnosis(_state(), app_config=_app_cfg(_gate_cfg()))
        # 침묵 금지 — 사유를 담은 자연어 응답, routing_intent는 유지.
        assert "오류" in out["final_response"] or "완료하지 못" in out["final_response"]
        assert out["routing_intent"] == "fault_diagnosis"

    async def test_total_timeout_reason_response(self, monkeypatch):
        client = FakeSreClient(never_terminal=True)
        _patch_client(monkeypatch, client)
        gate = _gate_cfg(investigation_total_timeout_seconds=0.05,
                         investigation_poll_interval_seconds=0.0)
        out = await fault_diagnosis(_state(), app_config=_app_cfg(gate))
        assert "제한 시간" in out["final_response"]

    async def test_rejected_empty_text(self, monkeypatch):
        client = FakeSreClient(sub_status="rejected")
        _patch_client(monkeypatch, client)
        out = await fault_diagnosis(_state(), app_config=_app_cfg(_gate_cfg()))
        assert client.poll_calls == []  # rejected면 poll하지 않음
        assert "결과를 받지 못했" in out["final_response"]

    async def test_terminal_without_text(self, monkeypatch):
        client = FakeSreClient(poll_result={"status": "failed"})
        _patch_client(monkeypatch, client)
        out = await fault_diagnosis(_state(), app_config=_app_cfg(_gate_cfg()))
        assert "결과를 받지 못했" in out["final_response"]

    async def test_client_missing_reason_response(self, monkeypatch):
        _patch_client(monkeypatch, None)
        out = await fault_diagnosis(_state(), app_config=_app_cfg(_gate_cfg()))
        assert "연결할 수 없" in out["final_response"]
        assert out["routing_intent"] == "fault_diagnosis"


# ── 3. 옵트인 off ────────────────────────────────────────────────────────
class TestDisabled:
    async def test_disabled_flag_no_client_build(self, monkeypatch):
        # off면 _build_client조차 호출하지 않아야 한다(위임 없음).
        called = {"n": 0}

        def _boom(gate_cfg):
            called["n"] += 1
            raise AssertionError("off인데 클라이언트를 생성함")

        monkeypatch.setattr(fd, "_build_client", _boom)
        out = await fault_diagnosis(
            _state(), app_config=_app_cfg(_gate_cfg(fault_diagnosis_enabled=False))
        )
        assert called["n"] == 0
        assert "비활성화" in out["final_response"]
        assert out["routing_intent"] == "fault_diagnosis"


# ── 4. 대상 추출 ─────────────────────────────────────────────────────────
class TestExtractTargets:
    def test_from_filter_conditions(self):
        st = {
            "active_db_id": "polestar_cm_yd",
            "parsed_requirements": {
                "filter_conditions": [
                    {"field": "hostname", "value": "h9"},
                    {"field": "server_name", "value": "srv-9"},
                ]
            },
        }
        server, host, db = _extract_targets(st)
        assert server == "srv-9" and host == "h9" and db == "polestar_cm_yd"

    def test_conversation_context_fallback(self):
        # filter 없으면 직전 턴 식별 엔티티("해당 서버")로 폴백.
        st = {
            "active_db_id": None,
            "parsed_requirements": {},
            "conversation_context": {
                "previous_entities": [{"field": "hostname", "value": "prev-h"}],
                "previous_db_ids": ["polestar_b0"],
            },
        }
        server, host, db = _extract_targets(st)
        assert host == "prev-h" and db == "polestar_b0"

    def test_no_identifiers(self):
        server, host, db = _extract_targets({"parsed_requirements": {}})
        assert server is None and host is None and db is None


# ── 5. 프롬프트 노출(옵트인) ─────────────────────────────────────────────
class TestPromptExposure:
    def _domains(self):
        return list(DB_DOMAINS[:2])

    def test_omitted_when_off(self):
        prompt = _build_router_prompt(self._domains(), fault_diagnosis_enabled=False)
        assert "fault_diagnosis" not in prompt

    def test_included_when_on(self):
        prompt = _build_router_prompt(self._domains(), fault_diagnosis_enabled=True)
        assert "fault_diagnosis" in prompt
        assert "장애 진단 의도" in prompt

    def test_default_off_bit_identical(self):
        # 기본값(off)은 flag 미지정과 비트동일 → 기존 라우팅 프롬프트 무변경(회귀 0).
        assert _build_router_prompt(self._domains()) == _build_router_prompt(
            self._domains(), fault_diagnosis_enabled=False
        )

    def test_off_prompt_never_exposes_fault_diagnosis(self):
        """off면 **완성 프롬프트**에 fault_diagnosis가 어디에도 없어야 한다.

        (Plan 79 A-5) 종전에는 `"fault_diagnosis" not in SEMANTIC_ROUTER_SYSTEM_PROMPT_TEMPLATE`로
        템플릿 문자열만 검사했다. 그러나 fault_diagnosis 절이 스스로 "최우선 검토"라고 선언하면서
        프롬프트 맨 뒤(append)에 놓이는 모순이 있어, 절과 클래스 정의 줄을 **플레이스홀더로 주입**
        하도록 바꿨다. 그 결과 템플릿에는 플레이스홀더 이름이 남는다.

        검사 대상을 템플릿에서 **조립 결과**로 옮긴다 — 의도(off면 LLM이 이 클래스를 모른다)는
        동일하고, 조립 과정에서 새어나가는 경우까지 잡으므로 보장은 오히려 강해진다.
        """
        off_prompt = _build_router_prompt(self._domains(), fault_diagnosis_enabled=False)
        assert "fault_diagnosis" not in off_prompt
        # 미치환 플레이스홀더가 남으면 LLM에 중괄호 토큰이 그대로 노출된다.
        assert "{fault_diagnosis" not in off_prompt


# ── 6. 라우터 강등/유지 ──────────────────────────────────────────────────
def _router_config(*, fault_on: bool) -> AppConfig:
    cfg = AppConfig(
        multi_db=MultiDBConfig(active_db_ids_csv="polestar_b0"),
        enable_semantic_routing=True,
    )
    cfg.noise_gate.fault_diagnosis_enabled = fault_on
    return cfg


def _llm_intent(intent: str, databases: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.content = json.dumps(
        {"intent": intent, "databases": databases}, ensure_ascii=False
    )
    return resp


class TestRouterIntent:
    async def test_downgraded_when_off(self):
        # off면 LLM이 fault_diagnosis를 내도 data_query로 강등(라우팅 비트동일).
        llm = AsyncMock()
        llm.ainvoke.return_value = _llm_intent("fault_diagnosis", [
            {"db_id": "polestar_b0", "relevance_score": 0.9,
             "sub_query_context": "진단", "user_specified": False, "reason": "r"}
        ])
        state = create_initial_state(user_query="web-01 원인 분석해줘")
        out = await semantic_router(state, llm=llm, app_config=_router_config(fault_on=False))
        assert out["routing_intent"] == "data_query"

    async def test_preserved_when_on(self):
        llm = AsyncMock()
        llm.ainvoke.return_value = _llm_intent("fault_diagnosis", [
            {"db_id": "polestar_b0", "relevance_score": 0.9,
             "sub_query_context": "진단", "user_specified": False, "reason": "r"}
        ])
        state = create_initial_state(user_query="web-01 원인 분석해줘")
        out = await semantic_router(state, llm=llm, app_config=_router_config(fault_on=True))
        assert out["routing_intent"] == "fault_diagnosis"
        assert out["active_db_id"] == "polestar_b0"  # 진단 대상 스코프 힌트


# ── 7. D-004 3곳 대칭 ────────────────────────────────────────────────────
class TestIntentRouteMapAndRouting:
    def test_intent_route_map_has_fault_diagnosis(self):
        assert _INTENT_ROUTE_MAP.get("fault_diagnosis") == "fault_diagnosis"

    def test_route_after_semantic_router_maps_intent(self):
        state = create_initial_state(user_query="진단")
        state["routing_intent"] = "fault_diagnosis"
        assert route_after_semantic_router(state) == "fault_diagnosis"

    def test_existing_intents_unaffected(self):
        # 기존 의도 라우팅 무변경(회귀 0).
        for intent, node in (
            ("cache_management", "cache_management"),
            ("general_inference", "general_inference"),
            ("synonym_registration", "synonym_registrar"),
        ):
            state = create_initial_state(user_query="x")
            state["routing_intent"] = intent
            assert route_after_semantic_router(state) == node


def _graph_config(*, fault_on: bool) -> AppConfig:
    import os
    os.environ.pop("ENABLE_SEMANTIC_ROUTING", None)
    cfg = AppConfig(
        multi_db=MultiDBConfig(active_db_ids_csv="polestar_b0"),
        checkpoint_backend="sqlite",
        checkpoint_db_url=":memory:",
    )
    cfg.enable_semantic_routing = True
    cfg.enable_intent_orchestration = False
    cfg.enable_deepagents_package = False
    cfg.noise_gate.fault_diagnosis_enabled = fault_on
    return cfg


class TestGraphWiring:
    def test_node_wired_only_when_enabled(self):
        off = set(build_graph(_graph_config(fault_on=False)).get_graph().nodes.keys())
        on = set(build_graph(_graph_config(fault_on=True)).get_graph().nodes.keys())
        assert "fault_diagnosis" not in off
        assert "fault_diagnosis" in on
        # 노드 집합 차이는 fault_diagnosis 하나뿐(그 외 라우팅 비트동일).
        assert on - off == {"fault_diagnosis"}
