"""호스트 조사 경로 선택 + 진입 게이트 (Plan 78 W3-2·W3-3 / Plan 80 WU-18).

`SPEC-host-inspect-routing.md` §6 성공 기준 S1~S8을 1:1로 단언한다.

**정확도는 검증하지 않는다**(SPEC §0.1) — 어떤 질의가 어느 경로로 가야 *옳은가*는
WU-06(분포 실측 · G-BILL)이 공급할 재료이며, 여기서 고정하는 것은 **구조**다:
플래그가 꺼져 있으면 비트 동일하고, 켜져 있으면 결정적으로 같은 판정을 낸다.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from src.config import AppConfig, load_config
from src.orchestration.host_inspect import (
    _PROFILE_IDENTIFIER,
    _PROFILE_KEYWORDS,
    DEGRADED_KEY,
    HOST_INSPECT_AGENT,
    _metric_filter,
    detect_profile,
    run_host_inspect,
)
from src.orchestration.intent_planner import _coerce_host_inspect_intent
from src.orchestration.subagents import SUBAGENT_REGISTRY


def _cfg(*, investigation: bool) -> AppConfig:
    """조사 플래그만 뒤집은 설정 사본.

    `.env` 누수를 막기 위해 검증 대상 필드를 **명시로 덮는다**(Known Mistakes).
    """
    cfg = load_config()
    cfg.composite.investigation_enabled = investigation
    return cfg


# ──────────────────────────────────────────────
# S1·S2 — 도구 목록은 **고정**이다 (W3-3 · P14)
# ──────────────────────────────────────────────

def test_s1_registry_registers_agent():
    """레지스트리에 등재된다 — 디스패치·`allowed_agents()`가 이 경로를 알아야 한다."""
    assert HOST_INSPECT_AGENT in SUBAGENT_REGISTRY


def test_s2_tool_list_never_depends_on_flag():
    """★ P14 — 라우팅을 **도구 목록 제거로 구현하지 않는다**.

    도구 정의는 직렬화 컨텍스트의 접두부라, 런타임에 목록이 흔들리면 이후 전 턴의 KV 캐시가
    무효화된다(캐시 토큰이 10배 싸다). 목록은 고정하고 **가용성만** handler가 제어한다.
    """
    from src.orchestration import deepagents_tools

    src = inspect.getsource(deepagents_tools.build_tools)
    # 목록 원천이 레지스트리 그 자체여야 한다 — 플래그로 거르지 않는다.
    assert "SUBAGENT_REGISTRY.items()" in src
    assert "investigation_enabled" not in src


def test_s2_orchestrator_prompt_lists_the_tool():
    """노출한 도구는 프롬프트에도 있어야 한다.

    누락되면 오케스트레이터가 존재를 모른 채 다른 도구로 대체하거나 지어낸다
    (`query_live_processes` 누락 실측 — Plan 67 Phase 0 ②).
    """
    from src.orchestration.deepagents_tools import _TOOL_NAMES
    from src.prompts.orchestrator import ORCHESTRATOR_INSTRUCTIONS

    assert f"- {_TOOL_NAMES[HOST_INSPECT_AGENT]}:" in ORCHESTRATOR_INSTRUCTIONS


# ──────────────────────────────────────────────
# S3 — handler 게이트 (W3-3 두 번째 겹 · fail-closed)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_s3_handler_refuses_when_flag_off():
    """플래그 off인데 도달하면 **구조화 거부**를 반환한다(예외 아님 · 침묵 아님)."""
    result = await run_host_inspect(
        {"sub_query": "web-01 서버의 OS 정보 보여줘"},
        {},
        llm=None,
        app_config=_cfg(investigation=False),
    )
    assert result[DEGRADED_KEY] == "composite_investigation_disabled"
    assert result["error"]


@pytest.mark.asyncio
async def test_s3_handler_refuses_unknown_profile():
    """프로파일을 못 정하면 조사하지 않고 사유를 남긴다."""
    result = await run_host_inspect(
        {"sub_query": "지난달 매출 알려줘"},
        {},
        llm=None,
        app_config=_cfg(investigation=True),
    )
    assert result[DEGRADED_KEY] == "profile_undetected"


@pytest.mark.asyncio
async def test_s3_handler_refuses_without_target():
    """대상 미식별이면 조사하지 않는다 — 엉뚱한 호스트로 폴백하지 않는다."""
    result = await run_host_inspect(
        {"sub_query": "OS 정보 보여줘"},
        {"parsed_requirements": {}, "conversation_context": {}},
        llm=None,
        app_config=_cfg(investigation=True),
    )
    assert result[DEGRADED_KEY] == "target_unresolved"


# ──────────────────────────────────────────────
# S4·S5 — 경로 선택 교정 (W3-2)
# ──────────────────────────────────────────────

# `filter_conditions`는 **`[{field, value}]` 리스트**다(실측 — `_targets_from_conditions`).
# 계획서 의사코드가 아니라 실제 계약에 맞춘다(Known Mistakes: 실측 우선).
_STATE_WITH_HOST = {
    "parsed_requirements": {
        "filter_conditions": [{"field": "hostname", "value": "svweb001"}]
    },
    "conversation_context": {},
}


def test_s5_no_coercion_when_flag_off():
    """플래그 off면 **아무것도 하지 않는다** — task 리스트가 그대로다(비트 동일)."""
    tasks = [{"agent": "data_query", "sub_query": "svweb001 OS 정보"}]
    out = _coerce_host_inspect_intent(tasks, _STATE_WITH_HOST, _cfg(investigation=False))
    assert out[0]["agent"] == "data_query"


def test_s4_coerces_when_all_three_conditions_met():
    tasks = [{"agent": "data_query", "sub_query": "svweb001 OS 정보 보여줘"}]
    out = _coerce_host_inspect_intent(tasks, _STATE_WITH_HOST, _cfg(investigation=True))
    assert out[0]["agent"] == HOST_INSPECT_AGENT


def test_s4_no_coercion_without_keyword():
    """키워드가 없으면 `data_query`를 잠식하지 않는다 — 주력 경로 보호."""
    tasks = [{"agent": "data_query", "sub_query": "svweb001 CPU 사용률 조회"}]
    out = _coerce_host_inspect_intent(tasks, _STATE_WITH_HOST, _cfg(investigation=True))
    assert out[0]["agent"] == "data_query"


def test_s4_no_coercion_without_target():
    """대상 신호가 없으면 교정하지 않는다(조건 3)."""
    tasks = [{"agent": "data_query", "sub_query": "OS 정보 보여줘"}]
    out = _coerce_host_inspect_intent(
        tasks, {"parsed_requirements": {}, "conversation_context": {}},
        _cfg(investigation=True),
    )
    assert out[0]["agent"] == "data_query"


def test_s4_does_not_touch_other_agents():
    """`data_query` 외의 분류는 건드리지 않는다 — 프로세스/알람 교정 결과를 뒤집지 않는다."""
    tasks = [
        {"agent": "process_query", "sub_query": "svweb001 OS 정보 프로세스"},
        {"agent": "alarm_query", "sub_query": "svweb001 OS 정보 알람"},
    ]
    out = _coerce_host_inspect_intent(tasks, _STATE_WITH_HOST, _cfg(investigation=True))
    assert [t["agent"] for t in out] == ["process_query", "alarm_query"]


# ──────────────────────────────────────────────
# S6 — 프로파일 판정의 결정성
# ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "text,expected",
    [
        ("svweb001 OS 정보", "os_config"),
        ("운영체제 버전 알려줘", "os_config"),
        ("커널 버전 확인", "os_config"),
        ("자원 현황 보여줘", "resource_status"),
        ("리소스현황 조회", "resource_status"),
        ("메트릭 추세 보여줘", "metric_trend"),
        ("지표 추세 확인", "metric_trend"),
        ("서버 목록 조회", None),
        ("", None),
    ],
)
def test_s6_profile_detection_is_deterministic(text, expected):
    assert detect_profile(text) == expected
    assert detect_profile(text) == expected  # 같은 입력 → 같은 출력


def test_s6_processes_is_not_in_this_route():
    """실시간 프로세스 조회는 이 경로가 아니다 — `process_query`가 1급이다(D-041)."""
    assert detect_profile("현재 실행 중인 프로세스 리스트") is None


# ──────────────────────────────────────────────
# S7·S8 — 반환 계약 보존 · 읽기 전용 불변
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_s7_server_contract_passes_through(monkeypatch):
    """서버 반환 계약(D-122)을 **변형 없이** 싣는다 — 본체는 방언·마스킹에 무지하다."""
    server_payload = {
        "rows": [{"prop_name": "OSType", "prop_value": "Linux"}],
        "row_count": 1,
        "queried_at": "2026-08-28T00:00:00",
        "source_kind": "polestar_db",
        "source": "polestar_gimpo",
        "engine": "postgres",
    }

    class _FakeClient:
        async def inspect_host(self, **kwargs):
            self.seen = kwargs
            return dict(server_payload)

    fake = _FakeClient()

    class _Ctx:
        async def __aenter__(self):
            return fake

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr("src.orchestration.host_inspect.get_db_client", lambda *a, **k: _Ctx())

    result = await run_host_inspect(
        {"sub_query": "svweb001 OS 정보 보여줘"},
        _STATE_WITH_HOST,
        llm=None,
        app_config=_cfg(investigation=True),
    )
    for key, value in server_payload.items():
        assert result[key] == value, f"{key}가 변형됐다"
    assert result["profile"] == "os_config"
    # os_config은 hostname을 요구한다(D-046 — server_name으로 대체하지 않는다)
    assert fake.seen["hostname"] == "svweb001"
    assert fake.seen["server_name"] is None


def test_s8_handler_never_touches_execute_sql():
    """읽기 전용 불변 — 이 경로는 `execute_sql`을 부르지 않는다(D-122 ④)."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(run_host_inspect)))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "execute_sql" not in called


# ──────────────────────────────────────────────
# metrics_live — exporter 현재값 (plans/92 O3 · F-4 · D-225 ⑦ 2단·3단 공통 함수)
# ──────────────────────────────────────────────

_STATE_WITH_SERVER = {
    "parsed_requirements": {
        "filter_conditions": [{"field": "server_name", "value": "svweb001"}]
    },
    "conversation_context": {},
}


def _fake_client_ctx(monkeypatch, payload: dict):
    """`get_db_client`를 대역으로 바꾸고 `inspect_host` 호출 인자를 기록한다."""

    class _FakeClient:
        seen: dict | None = None

        async def inspect_host(self, **kwargs):
            _FakeClient.seen = kwargs
            return dict(payload)

    class _Ctx:
        async def __aenter__(self):
            return _FakeClient()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr("src.orchestration.host_inspect.get_db_client", lambda *a, **k: _Ctx())
    return _FakeClient


_OM_PAYLOAD = {
    "data": {
        "resultType": "vector",
        "result": [
            {"metric": {"__name__": "node_load1", "nodename": "svweb001"},
             "value": [1790000000.0, "0.42"]},
        ],
    },
    "queried_at": "2026-09-22T12:00:00",
    "source_kind": "openmetrics",
    "query": 'node_load1{nodename="svweb001"}',
    "endpoint": "/metrics",
    "result_count": 1,
    "content_type": "application/openmetrics-text; version=1.0.0",
    "target": "svweb001",
    "truncated": False,
    "series_total": 1,
    "types": {"node_load1": "gauge"},
    "observed_at": "2026-09-22T12:00:00",
    "target_identity": "match",
}


@pytest.mark.parametrize(
    "text,expected",
    [
        ("svweb001 실시간 메트릭 node_load1", "metrics_live"),
        ("실시간 지표 보여줘", "metrics_live"),
        ("현재 메트릭 node_ 전부", "metrics_live"),
        ("Exporter 메트릭 조회", "metrics_live"),
        ("익스포터 메트릭 확인", "metrics_live"),
        # 우선순위 — metrics_live는 맨 뒤라 기존 판정을 뺏지 않는다
        ("실시간 메트릭 추세 보여줘", "metric_trend"),
        ("OS 정보와 실시간 메트릭", "os_config"),
        ("자원 현황 실시간 메트릭", "resource_status"),
        # 좁다 — 단순 "메트릭"·"실시간"만으로는 이 경로가 아니다
        ("svweb001 메트릭 보여줘", None),
        ("실시간 프로세스 목록", None),
    ],
)
def test_metrics_live_keyword_detection(text, expected):
    assert detect_profile(text) == expected


def test_existing_profile_keywords_unchanged_and_metrics_live_last():
    """기존 세 프로파일의 키워드·순서는 그대로고 `metrics_live`는 최저 우선순위(맨 뒤)다."""
    assert _PROFILE_KEYWORDS[:3] == (
        ("os_config", ("os 정보", "os정보", "운영체제", "커널", "os 버전", "os 구성", "os구성")),
        ("resource_status", ("자원 현황", "자원현황", "리소스 현황", "리소스현황")),
        ("metric_trend", ("메트릭 추세", "지표 추세", "사용률 추세")),
    )
    assert _PROFILE_KEYWORDS[-1][0] == "metrics_live"


def test_profile_identifier_table_matches_client_specs():
    """식별자 표 — `metrics_live`는 server_name이다(도구 인자명 `hostname`과 혼동 금지 · D-119)."""
    from src.dbhub.client import DBHubClient

    assert _PROFILE_IDENTIFIER == {
        "os_config": "hostname",
        "resource_status": "server_name",
        "metric_trend": "server_name",
        "metrics_live": "server_name",
    }
    for profile, ident in _PROFILE_IDENTIFIER.items():
        assert DBHubClient.HOST_INSPECT_PROFILES[profile]["identifier"] == ident


@pytest.mark.parametrize(
    "text,exclude,expected",
    [
        ("svweb001 node_load1 실시간 메트릭", (), {"metric": "node_load1"}),
        ("mock_cpu_usage_percent 현재 메트릭", (), {"metric": "mock_cpu_usage_percent"}),
        ("node_load1을 실시간 메트릭으로 보여줘", (), {"metric": "node_load1"}),  # 조사 붙은 토큰
        ("node_ 실시간 메트릭 전부", (), {"prefix": "node_"}),
        ("node_memory_ 현재 메트릭", (), {"prefix": "node_memory_"}),
        ("실시간 메트릭 보여줘", (), None),
        ("svweb001 실시간 메트릭", (), None),               # `_` 없는 토큰은 후보가 아니다
        ("svr-web_01 실시간 메트릭", (), None),             # 하이픈 포함 토큰은 bare 이름이 아니다
        ("web_01.example.com 실시간 메트릭", (), None),     # FQDN도 통째로 탈락
        ("web_01 서버 node_load1 실시간 메트릭", ("web_01",), {"metric": "node_load1"}),
        ("WEB_01 서버 node_load1 실시간 메트릭", ("web_01", None), {"metric": "node_load1"}),
    ],
)
def test_metric_filter_extraction_is_deterministic(text, exclude, expected):
    assert _metric_filter(text, exclude=exclude) == expected
    assert _metric_filter(text, exclude=exclude) == expected


@pytest.mark.asyncio
async def test_metrics_live_without_metric_is_refused_not_silent(monkeypatch):
    """메트릭 토큰이 없으면 **호출하지 않고** 사유를 구조화해 돌려준다(침묵 금지)."""
    fake = _fake_client_ctx(monkeypatch, _OM_PAYLOAD)
    result = await run_host_inspect(
        {"sub_query": "svweb001 실시간 메트릭 보여줘"},
        _STATE_WITH_SERVER,
        llm=None,
        app_config=_cfg(investigation=True),
    )
    assert result == {
        "error": (
            "실시간 메트릭 조회에는 메트릭 이름(예: node_load1) 또는 접두(예: node_)가 필요합니다."
        ),
        DEGRADED_KEY: "metric_unspecified",
        "organized_data": "",
    }
    assert fake.seen is None


@pytest.mark.asyncio
async def test_metrics_live_calls_with_server_name_and_filter(monkeypatch):
    """server_name 대상 + 추출한 필터로 부르고, 서버 계약은 변형 없이 싣는다(D-122)."""
    fake = _fake_client_ctx(monkeypatch, _OM_PAYLOAD)
    result = await run_host_inspect(
        {"sub_query": "svweb001 실시간 메트릭 node_load1 보여줘"},
        _STATE_WITH_SERVER,
        llm=None,
        app_config=_cfg(investigation=True),
    )
    assert fake.seen == {
        "profile": "metrics_live", "hostname": None, "server_name": "svweb001",
        "metric": "node_load1",
    }
    for key, value in _OM_PAYLOAD.items():
        assert result[key] == value, f"{key}가 변형됐다"
    assert result["profile"] == "metrics_live"
    # 서버 계약의 `target`(허용목록 타깃 이름)은 덮어쓰지 않고 조사 대상은 `inspect_target`에 싣는다
    assert result["target"] == "svweb001"
    assert result["inspect_target"]["server_name"] == "svweb001"


@pytest.mark.asyncio
async def test_metrics_live_prefix_filter(monkeypatch):
    fake = _fake_client_ctx(monkeypatch, _OM_PAYLOAD)
    await run_host_inspect(
        {"sub_query": "svweb001 현재 메트릭 node_ 전부"},
        _STATE_WITH_SERVER,
        llm=None,
        app_config=_cfg(investigation=True),
    )
    assert fake.seen["prefix"] == "node_" and "metric" not in fake.seen


@pytest.mark.asyncio
async def test_existing_profile_call_kwargs_unchanged(monkeypatch):
    """★ 기존 프로파일은 옵션 없이 종전 인자 그대로 부른다(`**{}` — 비트 동일)."""
    fake = _fake_client_ctx(monkeypatch, {"rows": [], "row_count": 0})
    await run_host_inspect(
        {"sub_query": "svweb001 OS 정보 보여줘"}, _STATE_WITH_HOST, llm=None,
        app_config=_cfg(investigation=True),
    )
    assert fake.seen == {"profile": "os_config", "hostname": "svweb001", "server_name": None}
    fake.seen = None
    await run_host_inspect(
        {"sub_query": "svweb001 자원 현황 보여줘"}, _STATE_WITH_SERVER, llm=None,
        app_config=_cfg(investigation=True),
    )
    assert fake.seen == {"profile": "resource_status", "hostname": None, "server_name": "svweb001"}


@pytest.mark.asyncio
async def test_existing_profile_payload_has_no_organized_rows(monkeypatch):
    """기존 프로파일의 성공 payload는 종전 키 그대로다 — 행 펼치기는 `metrics_live`에만 적용된다."""
    server = {"rows": [{"k": "v"}], "row_count": 1, "queried_at": "t",
              "source_kind": "polestar_db", "source": "s", "engine": "postgres"}
    _fake_client_ctx(monkeypatch, server)
    result = await run_host_inspect(
        {"sub_query": "svweb001 OS 정보 보여줘"}, _STATE_WITH_HOST, llm=None,
        app_config=_cfg(investigation=True),
    )
    assert set(result) == set(server) | {"profile", "target"}


def test_metrics_live_flag_off_no_coercion():
    """플래그 off면 실시간 메트릭 질의도 교정하지 않는다(비트 동일)."""
    tasks = [{"agent": "data_query", "sub_query": "svweb001 실시간 메트릭 node_load1"}]
    out = _coerce_host_inspect_intent(tasks, _STATE_WITH_HOST, _cfg(investigation=False))
    assert out[0]["agent"] == "data_query"


def test_metrics_live_flag_on_coerces():
    tasks = [{"agent": "data_query", "sub_query": "svweb001 실시간 메트릭 node_load1"}]
    out = _coerce_host_inspect_intent(tasks, _STATE_WITH_HOST, _cfg(investigation=True))
    assert out[0]["agent"] == HOST_INSPECT_AGENT


# ── 결과 소비 — 응답 조립기가 metrics_live 결과를 실제로 읽는다 (실측 2026-09-22) ──────────

async def _metrics_live_result(monkeypatch, payload: dict) -> dict:
    _fake_client_ctx(monkeypatch, payload)
    return await run_host_inspect(
        {"sub_query": "svweb001 실시간 메트릭 node_load1"}, _STATE_WITH_SERVER, llm=None,
        app_config=_cfg(investigation=True),
    )


@pytest.mark.asyncio
async def test_metrics_live_vector_is_flattened_to_rows(monkeypatch):
    """vector(`data.result`)는 `rows`가 아니라 조립기가 읽지 못한다 — 행으로 펼쳐 더한다."""
    result = await _metrics_live_result(monkeypatch, _OM_PAYLOAD)
    rows = [{"server_name": "svweb001", "metric": "node_load1", "value": "0.42"}]
    assert result["organized_data"]["rows"] == rows
    assert result["query_results"] == rows
    summary = result["organized_data"]["summary"]
    assert "현재값" in summary and "2026-09-22T12:00:00" in summary and "누적값" in summary
    assert "절단" not in summary and "mismatch" not in summary


@pytest.mark.asyncio
async def test_metrics_live_summary_surfaces_truncation_and_identity_mismatch(monkeypatch):
    payload = {
        **_OM_PAYLOAD,
        "data": {"resultType": "vector", "result": [
            {"metric": {"__name__": "node_cpu_seconds_total", "cpu": "0", "mode": "idle",
                        "nodename": "svweb001", "exported_nodename": "other"},
             "value": [1790000000.0, "12.5"]},
        ]},
        "truncated": True, "series_total": 40, "target_identity": "mismatch",
    }
    result = await _metrics_live_result(monkeypatch, payload)
    assert result["organized_data"]["rows"] == [{
        "server_name": "svweb001", "metric": "node_cpu_seconds_total", "cpu": "0", "mode": "idle",
        "exported_nodename": "other", "value": "12.5",
    }]
    summary = result["organized_data"]["summary"]
    assert "40개 중 1개" in summary and "target_identity=mismatch" in summary


@pytest.mark.asyncio
async def test_metrics_live_result_reaches_response_assembly(monkeypatch):
    """★ `_finalize_task`가 metrics_live 결과를 output_generator로 넘긴다("처리 결과가 없습니다" X).

    세 경로(1단 deep_agent · 2단 · 3단)가 모두 이 함수로 task 결과를 최종화한다.
    """
    import importlib

    from src.orchestration.deepagents_tools import _serialize_for_tool

    # 패키지 `src.orchestration`이 같은 이름의 함수를 재노출하므로 모듈을 직접 잡는다
    ra = importlib.import_module("src.orchestration.result_aggregator")

    result = await _metrics_live_result(monkeypatch, _OM_PAYLOAD)
    captured: dict = {}

    async def _fake_output_generator(state, **kwargs):
        captured.update(state)
        return {"final_response": "node_load1 현재값 0.42"}

    monkeypatch.setattr(ra, "output_generator", _fake_output_generator)
    task = {"task_id": "t1", "agent": HOST_INSPECT_AGENT,
            "sub_query": "svweb001 실시간 메트릭 node_load1"}
    out = await ra._finalize_task(
        task, result, {"parsed_requirements": {}}, llm=None, app_config=None,
    )
    assert out["text"] == "node_load1 현재값 0.42"
    assert captured["organized_data"]["rows"][0]["value"] == "0.42"
    assert out["query_results"] == result["query_results"]
    # 1단(deep_agent) 제어 평면 요약도 행을 싣는다
    assert '"row_count": 1' in _serialize_for_tool(result)
