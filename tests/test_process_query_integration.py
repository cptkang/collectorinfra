"""Plan 48 Phase 3 통합 테스트 (라우팅 + 그래프 배선 + 출력 채널).

다루는 시나리오:
    - process_query_node: 모의 API 응답으로 CPU 상위 / 메모리 상위 / 전체(both)
      현황 생성. LLM 입력이 마스킹된 요약만 포함(평문 비밀 비노출) 확인.
    - semantic_router: parsed_requirements.process_query 신호 시 routing_intent을
      "process_query"로 오버라이드하고 active_db_id를 확정·보존하는지 확인.
    - input_parser: process_query 파싱 시 process_query_target을 채우는지 확인.
    - graph: build_graph로 process_query 노드·엣지가 등록되어 컴파일되는지 확인.

실 API end-to-end는 운영 환경 수동 확인(테스트 제외).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.config import AppConfig, ProcessQueryConfig
from src.infrastructure.polestar_host_resolver import HostResolution
from src.nodes.input_parser import _build_process_query_target
from src.nodes.process_query_node import process_query_node
from src.routing.semantic_router import semantic_router


# ── Fakes ──────────────────────────────────────────────


class _FakeLLM:
    """ainvoke가 고정 텍스트를 .content로 반환. 받은 메시지 캡처."""

    def __init__(self, text="현황 분석 결과입니다."):
        self._text = text
        self.received = []

    async def ainvoke(self, messages):
        self.received = messages

        class _R:
            content = self._text

        return _R()


class _RouterLLM:
    """semantic_router용 fake LLM — 고정 분류 JSON 반환."""

    def __init__(self, db_id="polestar_cm_gp", user_specified=False, intent="data_query"):
        self._db_id = db_id
        self._user_specified = str(user_specified).lower()
        self._intent = intent

    async def ainvoke(self, messages):
        content = (
            '{"intent": "%s", "databases": [{"db_id": "%s", '
            '"relevance_score": 0.95, "reason": "test", '
            '"sub_query_context": "프로세스 조회", "user_specified": %s}]}'
        ) % (self._intent, self._db_id, self._user_specified)

        class _R:
            pass

        r = _R()
        r.content = content
        return r


class _FakeResolver:
    def __init__(self, by_db):
        self._by_db = by_db
        self.calls = []

    async def resolve(self, db_id, identifier):
        self.calls.append((db_id, identifier))
        return self._by_db.get(db_id, {}).get(identifier, HostResolution())


class _ApiResult:
    def __init__(self, processes, captured_at=None):
        self.processes = processes
        self.captured_at = captured_at


class _FakeProcessClient:
    def __init__(self, base_urls, results, *, fail=False):
        self._base_urls = base_urls
        self._results = results
        self._fail = fail
        self.calls = []

    def get_base_url(self, db_id):
        return self._base_urls.get(db_id)

    async def list_by_hostname(self, db_id, hostname):
        self.calls.append((db_id, hostname))
        if self._fail:
            raise RuntimeError("API 장애")
        return self._results.get((db_id, hostname))


def _app_config(enabled=True):
    cfg = AppConfig()
    cfg.process_query = ProcessQueryConfig(
        process_query_enabled=enabled,
        process_query_top_n=10,
        process_query_resolve_timeout_seconds=3,
    )
    return cfg


_NOW = datetime(2026, 6, 16, 12, 0, 0)
_BASE_URLS = {"polestar_cm_gp": "http://gp", "polestar_cm_yd": "http://yd"}

# 마스킹 대상 평문 비밀 포함 프로세스 목록
_SECRET = "java --password=topsecret123 token=abctoken jdbc:db://u:plainpw@h/x"
_PROCS = [
    {"name": "java", "pid": 1, "user": "app", "p100cpu": 80, "pmem": 15, "args": _SECRET},
    {"name": "postgres", "pid": 2, "user": "pg", "p100cpu": 10, "pmem": 70, "args": "postgres -D /data"},
    {"name": "nginx", "pid": 3, "user": "www", "p100cpu": 5, "pmem": 5, "args": "nginx -g daemon off"},
]


def _state(metric="both", **over):
    base = {
        "user_query": "myserver 프로세스 보여줘",
        "active_db_id": "polestar_cm_gp",
        "process_query_target": {"identifier": "myserver", "metric": metric, "top_n": 10},
        "user_specified_db": None,
        "file_type": None,
    }
    base.update(over)
    return base


def _node_inputs():
    resolver = _FakeResolver(
        {"polestar_cm_gp": {"myserver": HostResolution(hostname="saisvd01")}}
    )
    client = _FakeProcessClient(
        _BASE_URLS, {("polestar_cm_gp", "saisvd01"): _ApiResult(_PROCS, _NOW)}
    )
    return resolver, client


# ── process_query_node 시나리오 (CPU / MEM / both) ──────


@pytest.mark.asyncio
class TestProcessQueryScenarios:
    async def test_cpu_top_scenario(self):
        resolver, client = _node_inputs()
        llm = _FakeLLM()
        out = await process_query_node(
            _state(metric="cpu"),
            llm=llm,
            app_config=_app_config(),
            host_resolver=resolver,
            process_client=client,
        )
        ov = out["process_overview"]
        assert ov["metric"] == "cpu"
        # CPU 내림차순 상위 → java(80) 첫 행
        assert ov["top_by_cpu"][0]["name"] == "java"
        assert ov["top_by_cpu"][0]["p100cpu"] == 80
        assert out["routing_intent"] == "process_query"

    async def test_memory_top_scenario(self):
        resolver, client = _node_inputs()
        out = await process_query_node(
            _state(metric="memory"),
            llm=_FakeLLM(),
            app_config=_app_config(),
            host_resolver=resolver,
            process_client=client,
        )
        ov = out["process_overview"]
        assert ov["metric"] == "memory"
        # MEM 내림차순 상위 → postgres(70) 첫 행
        assert ov["top_by_mem"][0]["name"] == "postgres"
        assert ov["top_by_mem"][0]["pmem"] == 70

    async def test_both_scenario(self):
        resolver, client = _node_inputs()
        out = await process_query_node(
            _state(metric="both"),
            llm=_FakeLLM(),
            app_config=_app_config(),
            host_resolver=resolver,
            process_client=client,
        )
        ov = out["process_overview"]
        assert ov["metric"] == "both"
        assert ov["top_by_cpu"] and ov["top_by_mem"]
        assert ov["total_count"] == 3

    async def test_llm_input_masked_only_no_plaintext(self):
        """LLM 입력·overview·final_response 어디에도 평문 비밀이 없어야 한다."""
        resolver, client = _node_inputs()
        llm = _FakeLLM()
        out = await process_query_node(
            _state(metric="both"),
            llm=llm,
            app_config=_app_config(),
            host_resolver=resolver,
            process_client=client,
        )
        haystack = out["final_response"] + str(out["process_overview"])
        for msg in llm.received:
            haystack += str(getattr(msg, "content", ""))
        for secret in ("topsecret123", "abctoken", "plainpw"):
            assert secret not in haystack
        # LLM에 결정적 요약(마스킹 ***)이 실제로 주입되었는지 확인
        joined = "".join(str(getattr(m, "content", "")) for m in llm.received)
        assert "***" in joined
        assert "saisvd01" in joined


# ── semantic_router 오버라이드 ──────────────────────────


@pytest.mark.asyncio
class TestSemanticRouterOverride:
    async def test_process_query_signal_overrides_intent(self):
        """parsed_requirements.process_query 시 routing_intent=process_query, active_db_id 확정."""
        cfg = AppConfig()
        cfg.enable_semantic_routing = True
        cfg.multi_db.active_db_ids_csv = "polestar_cm_gp,polestar_cm_yd"
        state = {
            "user_query": "myserver 프로세스 보여줘",
            "parsed_requirements": {
                "query_targets": ["프로세스"],
                "process_query": {"identifier": "myserver", "metric": "both", "top_n": None},
            },
        }
        out = await semantic_router(
            state, llm=_RouterLLM(db_id="polestar_cm_gp"), app_config=cfg
        )
        assert out["routing_intent"] == "process_query"
        assert out["active_db_id"] == "polestar_cm_gp"

    async def test_user_specified_db_preserved(self):
        """user_specified=true가 process_query 오버라이드에서도 전파되어야 한다."""
        cfg = AppConfig()
        cfg.enable_semantic_routing = True
        cfg.multi_db.active_db_ids_csv = "polestar_cm_gp,polestar_cm_yd"
        state = {
            "user_query": "김포 폴스타의 myserver 프로세스 보여줘",
            "parsed_requirements": {
                "query_targets": ["프로세스"],
                "process_query": {"identifier": "myserver", "metric": "both", "top_n": None},
            },
        }
        out = await semantic_router(
            state,
            llm=_RouterLLM(db_id="polestar_cm_gp", user_specified=True),
            app_config=cfg,
        )
        assert out["routing_intent"] == "process_query"
        assert out["active_db_id"] == "polestar_cm_gp"
        assert out["user_specified_db"] == "polestar_cm_gp"

    async def test_no_process_signal_keeps_data_query(self):
        """process_query 신호가 없으면 기존 data_query 라우팅 유지(무회귀)."""
        cfg = AppConfig()
        cfg.enable_semantic_routing = True
        cfg.multi_db.active_db_ids_csv = "polestar_cm_gp"
        state = {
            "user_query": "서버 목록 보여줘",
            "parsed_requirements": {"query_targets": ["서버"]},
        }
        out = await semantic_router(
            state, llm=_RouterLLM(db_id="polestar_cm_gp"), app_config=cfg
        )
        assert out["routing_intent"] == "data_query"
        assert out["active_db_id"] == "polestar_cm_gp"

    async def test_llm_direct_process_query_intent(self):
        """LLM이 직접 process_query intent를 줘도 오버라이드 경로로 처리되고 DB 확정."""
        cfg = AppConfig()
        cfg.enable_semantic_routing = True
        cfg.multi_db.active_db_ids_csv = "polestar_cm_gp"
        state = {
            "user_query": "myserver 프로세스",
            "parsed_requirements": {"query_targets": ["프로세스"]},
        }
        out = await semantic_router(
            state,
            llm=_RouterLLM(db_id="polestar_cm_gp", intent="process_query"),
            app_config=cfg,
        )
        assert out["routing_intent"] == "process_query"
        assert out["active_db_id"] == "polestar_cm_gp"


# ── input_parser process_query_target ───────────────────


class TestInputParserTarget:
    def test_builds_target_from_parsed(self):
        parsed = {
            "query_targets": ["프로세스"],
            "process_query": {"identifier": "saisvd01", "metric": "cpu", "top_n": 5},
        }
        target = _build_process_query_target(parsed)
        assert target == {"identifier": "saisvd01", "metric": "cpu", "top_n": 5}

    def test_no_signal_returns_none(self):
        assert _build_process_query_target({"query_targets": ["서버"]}) is None

    def test_empty_identifier_returns_none(self):
        parsed = {"process_query": {"identifier": "   ", "metric": "cpu"}}
        assert _build_process_query_target(parsed) is None

    def test_metric_normalized_and_top_n_optional(self):
        parsed = {"process_query": {"identifier": "h1", "metric": "garbage"}}
        target = _build_process_query_target(parsed)
        assert target == {"identifier": "h1", "metric": "both", "top_n": None}


# ── graph 배선 ──────────────────────────────────────────


class TestGraphWiring:
    def test_build_graph_registers_process_query_node(self):
        """build_graph로 process_query 노드·엣지가 등록되고 컴파일되는지 확인."""
        from src.graph import build_graph

        cfg = AppConfig()
        cfg.enable_semantic_routing = True
        cfg.enable_sql_approval = False
        cfg.enable_structure_approval = False
        cfg.checkpoint_db_url = ":memory:"

        compiled = build_graph(cfg)
        node_names = set(compiled.get_graph().nodes.keys())
        assert "process_query" in node_names

    def test_intent_route_map_has_process_query(self):
        from src.graph import _INTENT_ROUTE_MAP

        assert _INTENT_ROUTE_MAP.get("process_query") == "process_query"

    def test_route_after_process_query_text_ends(self):
        from langgraph.graph import END

        from src.graph import route_after_process_query

        assert route_after_process_query({"file_type": None}) == END
        assert route_after_process_query({"file_type": "xlsx"}) == "output_generator"
        assert route_after_process_query({"file_type": "docx"}) == "output_generator"
