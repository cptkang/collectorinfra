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
    DEGRADED_KEY,
    HOST_INSPECT_AGENT,
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
