"""deepagents 트랙 B 단위 테스트 (Plan 49 §8).

검증 대상 (인프라 비의존 — vLLM/deepagents 패키지 없이 검증):
- OrchestratorConfig / enable_deepagents_package 기본값
- create_orchestrator_llm (vLLM ChatOpenAI 생성, base_url 필수)
- vllm_healthy / select_orchestration_backend (가용성 분기 §4.6)
- build_tools (SUBAGENT_REGISTRY → @tool 노출)
- _serialize_for_tool (행수 상한·실패·비조회형)
- _run_subagent_tool (handler 호출 동형 — FabriX 내부 호출)
- build_deep_agent (deepagents 미설치 시 명확한 RuntimeError)
"""

import pytest

from src.config import AppConfig, OrchestratorConfig
from src.llm import create_orchestrator_llm
from src.orchestration.deep_agent import (
    build_deep_agent,
    orchestrator_available,
    select_orchestration_backend,
    vllm_healthy,
)
from src.orchestration.deepagents_tools import (
    _MAX_TOOL_ROWS,
    _TOOL_NAMES,
    _run_subagent_tool,
    _serialize_for_tool,
    build_tools,
)
from src.orchestration.subagents import SUBAGENT_REGISTRY, SubAgentSpec


# ──────────────────────────────────────────────
# 설정 기본값
# ──────────────────────────────────────────────

def test_orchestrator_config_default_model():
    """OrchestratorConfig.model 기본값은 Qwen3.5-9B."""
    assert OrchestratorConfig.model_fields["model"].default == "Qwen3.5-9B"


def test_enable_deepagents_package_default_false():
    """enable_deepagents_package는 명시적 opt-in(기본 False)."""
    assert AppConfig.model_fields["enable_deepagents_package"].default is False


def test_orchestrator_config_default_provider_vllm():
    """provider 기본값은 vllm(운영). gemini는 명시 설정 시에만(테스트)."""
    assert OrchestratorConfig.model_fields["provider"].default == "vllm"


# ──────────────────────────────────────────────
# create_orchestrator_llm
# ──────────────────────────────────────────────

def test_create_orchestrator_llm_requires_base_url(mock_config):
    """base_url 미설정 시 ValueError (vLLM provider).

    provider를 명시한다 — OrchestratorConfig는 .env(env_prefix=ORCHESTRATOR_)를 읽으므로
    로컬 .env의 ORCHESTRATOR_PROVIDER 값이 누수되지 않도록 vllm을 고정한다.
    """
    mock_config.orchestrator = OrchestratorConfig(provider="vllm", base_url="")
    with pytest.raises(ValueError, match="ORCHESTRATOR_BASE_URL"):
        create_orchestrator_llm(mock_config)


def test_create_orchestrator_llm_builds_chatopenai(mock_config):
    """base_url 설정 시 ChatOpenAI(vLLM)를 모델명·엔드포인트로 생성한다.

    provider=vllm 명시 — .env(env_prefix=ORCHESTRATOR_)의 provider 누수 방지.
    """
    ChatOpenAI = pytest.importorskip("langchain_openai").ChatOpenAI
    mock_config.orchestrator = OrchestratorConfig(
        provider="vllm", base_url="http://vllm-host:8000/v1", model="Qwen3.5-9B"
    )
    llm = create_orchestrator_llm(mock_config)
    assert isinstance(llm, ChatOpenAI)
    assert llm.model_name == "Qwen3.5-9B"


def test_create_orchestrator_llm_gemini_requires_api_key(mock_config):
    """provider=gemini + api_key 없음 → ValueError (패키지 미설치와 무관하게 검증)."""
    mock_config.orchestrator = OrchestratorConfig(provider="gemini", api_key="")
    mock_config.llm.gemini_api_key = ""
    with pytest.raises(ValueError, match="Gemini"):
        create_orchestrator_llm(mock_config)


def test_create_orchestrator_llm_gemini_builds(mock_config):
    """provider=gemini + api_key 있음 → ChatGoogleGenerativeAI(gemini 모델) 생성(테스트 모드)."""
    genai = pytest.importorskip("langchain_google_genai")
    mock_config.orchestrator = OrchestratorConfig(
        provider="gemini", model="gemini-2.5-pro", api_key="test-key"
    )
    llm = create_orchestrator_llm(mock_config)
    assert isinstance(llm, genai.ChatGoogleGenerativeAI)


def test_create_orchestrator_llm_gemini_falls_back_model(mock_config):
    """provider=gemini인데 model이 vLLM 기본값(비-gemini)이면 gemini 모델로 폴백한다."""
    genai = pytest.importorskip("langchain_google_genai")
    mock_config.orchestrator = OrchestratorConfig(
        provider="gemini", model="Qwen3.5-9B", api_key="test-key"
    )
    mock_config.llm.gemini_model = ""
    llm = create_orchestrator_llm(mock_config)
    assert isinstance(llm, genai.ChatGoogleGenerativeAI)
    # Qwen 기본값이 그대로 전달되지 않고 gemini 모델로 폴백됨
    assert "gemini" in str(getattr(llm, "model", "")).lower()


# ──────────────────────────────────────────────
# vllm_healthy / select_orchestration_backend
# ──────────────────────────────────────────────

def test_vllm_healthy_empty_url_false():
    """base_url이 비면 health check 없이 False."""
    assert vllm_healthy("") is False


def test_vllm_healthy_200_true(monkeypatch):
    """/models 200 응답이면 True."""
    class _Resp:
        status_code = 200

    monkeypatch.setattr("requests.get", lambda url, timeout, verify=True: _Resp())
    assert vllm_healthy("http://vllm:8000/v1") is True


def test_vllm_healthy_verify_ssl_false_passes_verify(monkeypatch):
    """verify_ssl=False면 requests.get에 verify=False 전달(SSL 검증 스킵, D-060)."""
    class _Resp:
        status_code = 200

    captured = {}

    def _get(url, timeout, verify=True):
        captured["verify"] = verify
        return _Resp()

    monkeypatch.setattr("requests.get", _get)
    assert vllm_healthy("https://vllm:443/v1", verify_ssl=False) is True
    assert captured["verify"] is False


def test_vllm_healthy_exception_false(monkeypatch):
    """연결 예외 시 False(미가용)."""
    def _raise(url, timeout, verify=True):
        raise ConnectionError("refused")

    monkeypatch.setattr("requests.get", _raise)
    assert vllm_healthy("http://vllm:8000/v1") is False


def test_select_backend_flag_off(mock_config, monkeypatch):
    """플래그 off면 vLLM 가용해도 semantic_router."""
    mock_config.enable_deepagents_package = False
    mock_config.orchestrator = OrchestratorConfig(base_url="http://vllm:8000/v1")
    monkeypatch.setattr("src.orchestration.deep_agent.vllm_healthy", lambda *a, **k: True)
    assert select_orchestration_backend(mock_config) == "semantic_router"


def test_select_backend_vllm_down(mock_config, monkeypatch):
    """플래그 on이어도 vLLM 미서빙이면 semantic_router.

    provider=vllm 고정 — .env의 ORCHESTRATOR_PROVIDER 누수로 가용성 판정이 health check
    대신 gemini api_key 경로를 타지 않도록 한다(vLLM health 경로 검증 의도 보존).
    """
    mock_config.enable_deepagents_package = True
    mock_config.orchestrator = OrchestratorConfig(provider="vllm", base_url="http://vllm:8000/v1")
    monkeypatch.setattr("src.orchestration.deep_agent.vllm_healthy", lambda *a, **k: False)
    assert select_orchestration_backend(mock_config) == "semantic_router"


def test_select_backend_vllm_up(mock_config, monkeypatch):
    """플래그 on + vLLM 가용이면 deep_agent."""
    mock_config.enable_deepagents_package = True
    mock_config.orchestrator = OrchestratorConfig(provider="vllm", base_url="http://vllm:8000/v1")
    monkeypatch.setattr("src.orchestration.deep_agent.vllm_healthy", lambda *a, **k: True)
    assert select_orchestration_backend(mock_config) == "deep_agent"


def test_orchestrator_available_gemini_by_api_key(mock_config):
    """gemini provider 가용성은 api_key 유무로 판정(health check 무관)."""
    mock_config.llm.gemini_api_key = ""
    mock_config.orchestrator = OrchestratorConfig(provider="gemini", api_key="k")
    assert orchestrator_available(mock_config) is True
    mock_config.orchestrator = OrchestratorConfig(provider="gemini", api_key="")
    assert orchestrator_available(mock_config) is False


def test_select_backend_gemini_available(mock_config):
    """테스트 모드: 플래그 on + gemini api_key 있으면 vLLM 없이도 deep_agent."""
    mock_config.enable_deepagents_package = True
    mock_config.llm.gemini_api_key = ""
    mock_config.orchestrator = OrchestratorConfig(provider="gemini", api_key="test-key")
    assert select_orchestration_backend(mock_config) == "deep_agent"


# ──────────────────────────────────────────────
# build_tools
# ──────────────────────────────────────────────

def test_orchestrator_instructions_list_every_exposed_tool():
    """프롬프트의 "사용 가능한 도구" 목록이 실제 노출 도구 전부를 담아야 한다.

    누락되면 오케스트레이터가 그 도구의 존재를 모른 채 다른 도구로 대체하거나 지어낸다
    (query_live_processes 누락 실측 — Plan 67 Phase 0 ②).
    """
    from src.prompts.orchestrator import ORCHESTRATOR_INSTRUCTIONS

    exposed = {_TOOL_NAMES.get(name, name) for name in SUBAGENT_REGISTRY}
    missing = sorted(t for t in exposed if f"- {t}:" not in ORCHESTRATOR_INSTRUCTIONS)
    assert missing == [], f"프롬프트 도구 목록 누락: {missing}"


def test_orchestrator_config_recursion_limit_default():
    """recursion_limit 기본값은 LangGraph 기본값과 동일한 25 (동작 불변)."""
    assert OrchestratorConfig(_env_file=None).recursion_limit == 25


def test_build_tools_exposes_five_named_tools(mock_config):
    """SUBAGENT_REGISTRY 전부가 약속된 도구 이름으로 노출된다.

    **목록은 플래그와 무관하게 고정**이다(78 P14) — `host_inspect`도 비활성일 때
    목록에 남고, 가용성은 handler 게이트가 구조화 거부로 제어한다.
    """
    tools = build_tools(worker_llm=None, app_config=mock_config)
    assert len(tools) == len(SUBAGENT_REGISTRY)
    names = {t.name for t in tools}
    assert names == set(_TOOL_NAMES.values())
    # 설명은 registry description을 계승
    by_name = {t.name: t for t in tools}
    assert by_name["query_infra_db"].description == SUBAGENT_REGISTRY["data_query"].description


# ──────────────────────────────────────────────
# _serialize_for_tool
# ──────────────────────────────────────────────

def test_serialize_error():
    """error 키가 있으면 실패 텍스트."""
    out = _serialize_for_tool({"error": "DB 연결 실패"})
    assert out.startswith("[실패]")
    assert "DB 연결 실패" in out


def test_serialize_caps_rows():
    """organized_data.rows가 상한을 넘으면 truncated=True + 행수 보존."""
    rows = [{"hostname": f"h{i}"} for i in range(_MAX_TOOL_ROWS + 10)]
    out = _serialize_for_tool({"organized_data": {"summary": "요약", "rows": rows}})
    assert '"row_count": %d' % (_MAX_TOOL_ROWS + 10) in out
    assert '"truncated": true' in out
    assert "요약" in out


def test_serialize_non_query_dict():
    """조회형이 아닌 결과(dict)는 전체를 직렬화한다."""
    out = _serialize_for_tool({"status": "ok", "registered": ["a", "b"]})
    assert "registered" in out
    assert "ok" in out


# ──────────────────────────────────────────────
# _run_subagent_tool (handler 호출 동형)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_subagent_tool_invokes_handler(mock_config, monkeypatch):
    """도구가 SUBAGENT_REGISTRY handler를 호출하고 결과를 직렬화한다(FabriX 내부 호출 지점)."""
    captured = {}

    async def fake_handler(task, isolated, *, llm, app_config):
        captured["sub_query"] = isolated.get("user_query")
        return {"organized_data": {"summary": "처리 완료", "rows": [{"hostname": "h1"}]}}

    monkeypatch.setitem(
        SUBAGENT_REGISTRY, "data_query",
        SubAgentSpec("data_query", "인프라 DB 조회", fake_handler),
    )
    out = await _run_subagent_tool(
        "data_query", "서버 목록 조회",
        worker_llm=None, app_config=mock_config, ambient_state={},
    )
    assert captured["sub_query"] == "서버 목록 조회"
    assert "처리 완료" in out


@pytest.mark.asyncio
async def test_run_subagent_tool_collects_full_result(mock_config, monkeypatch):
    """collector 주어지면 truncate 전 원본 결과를 (task, result)로 적재한다(step6 입력)."""
    # 도구 반환 텍스트는 상한이 적용되지만, collector에는 전체 행이 보존되어야 한다.
    full_rows = [{"hostname": f"h{i}"} for i in range(120)]

    async def fake_handler(task, isolated, *, llm, app_config):
        return {"organized_data": {"summary": "120행", "rows": full_rows, "is_sufficient": True}}

    monkeypatch.setitem(
        SUBAGENT_REGISTRY, "data_query",
        SubAgentSpec("data_query", "인프라 DB 조회", fake_handler),
    )
    collector: list = []
    await _run_subagent_tool(
        "data_query", "서버 목록",
        worker_llm=None, app_config=mock_config, ambient_state={}, collector=collector,
    )
    assert len(collector) == 1
    task, result = collector[0]
    assert task["agent"] == "data_query"
    assert task["status"] == "completed"
    # 원본 결과는 상한 없이 전체 보존 (120행)
    assert len(result["organized_data"]["rows"]) == 120


# ──────────────────────────────────────────────
# build_deep_agent (deepagents 미설치)
# ──────────────────────────────────────────────

def test_build_deep_agent_without_package_raises(mock_config):
    """deepagents 미설치 시 명확한 RuntimeError(폐쇄망 wheel 반입 안내)."""
    pytest.importorskip
    try:
        import deepagents  # noqa: F401
        pytest.skip("deepagents가 설치되어 있어 이 케이스는 건너뜀")
    except ImportError:
        pass

    mock_config.orchestrator = OrchestratorConfig(base_url="http://vllm:8000/v1")
    with pytest.raises(RuntimeError, match="deepagents"):
        build_deep_agent(mock_config)


# ──────────────────────────────────────────────
# 선행 결과 스코프 결정적 주입 (D-095)
# ──────────────────────────────────────────────

def _alarm_collector():
    """알람 조회가 선행된 collector 픽스처."""
    return [(
        {"task_id": "tool_alarm_query_1", "agent": "alarm_query", "order": 1,
         "status": "completed", "sub_query": "활성 심각 알람 서버 목록 조회"},
        {"organized_data": {"summary": "2건",
                            "rows": [{"server_name": "SV-WEB-001"},
                                     {"server_name": "SV-BATCH-009"}],
                            "is_sufficient": True}},
    )]


def test_dependency_scope_ranking_marker_injects():
    """순위/최상급 어휘(G3)가 있으면 선행 결과를 주입한다.

    회귀 방지(2026-07-20 라이브): "6월 CPU 최고 서버 조회" sub_query가 알람 서버
    한정 없이 전 서버 기준 SQL로 생성돼 알람 무관 서버가 답으로 나왔다.
    """
    from src.orchestration.deepagents_tools import _dependency_scope

    input_from, prior = _dependency_scope(
        "2026년 6월 CPU 사용률 평균이 가장 높았던 서버 조회", _alarm_collector()
    )
    assert input_from == ["tool_alarm_query_1"]
    assert "tool_alarm_query_1" in prior


def test_dependency_scope_value_match_injects():
    """sub_query에 선행 결과 식별 값이 등장하면(G1) 주입한다(SQL 재생성 필터 탈락 방지)."""
    from src.orchestration.deepagents_tools import _dependency_scope

    input_from, _ = _dependency_scope(
        "SV-WEB-001, SV-BATCH-009 서버의 제조사와 일련번호 조회", _alarm_collector()
    )
    assert input_from == ["tool_alarm_query_1"]


def test_dependency_scope_reference_marker_injects():
    """참조/선별 어휘(G2 — "해당 서버")가 있으면 주입한다."""
    from src.orchestration.deepagents_tools import _dependency_scope

    input_from, _ = _dependency_scope("해당 서버들의 디스크 사용량 조회", _alarm_collector())
    assert input_from == ["tool_alarm_query_1"]


def test_dependency_scope_global_marker_blocks():
    """전역 범위 명시("전체 서버")면 순위 어휘가 있어도 주입하지 않는다(독립 조회 보호)."""
    from src.orchestration.deepagents_tools import _dependency_scope

    input_from, prior = _dependency_scope(
        "전체 서버 중 CPU 사용률이 가장 높은 서버 조회", _alarm_collector()
    )
    assert input_from == []
    assert prior == {}


def test_dependency_scope_no_signal_no_injection():
    """게이트 신호가 없는 독립 조회는 주입하지 않는다."""
    from src.orchestration.deepagents_tools import _dependency_scope

    input_from, prior = _dependency_scope("디스크 사용량 현황 조회", _alarm_collector())
    assert input_from == []
    assert prior == {}


def test_dependency_scope_skips_failed_and_rowless_prior():
    """실패했거나 행이 없는 선행 결과는 주입 후보에서 제외한다."""
    from src.orchestration.deepagents_tools import _dependency_scope

    collector = [
        ({"task_id": "t1", "agent": "alarm_query", "sub_query": "알람"}, {"error": "실패"}),
        ({"task_id": "t2", "agent": "data_query", "sub_query": "조회"},
         {"organized_data": {"summary": "0건", "rows": []}}),
    ]
    input_from, prior = _dependency_scope("가장 높은 서버 조회", collector)
    assert input_from == []
    assert prior == {}


@pytest.mark.asyncio
async def test_run_subagent_tool_injects_prior_rows(mock_config, monkeypatch):
    """게이트 충족 시 handler의 isolated 입력에 prior_rows(식별 키 행)가 실린다(D-095)."""
    captured = {}

    async def fake_handler(task, isolated, *, llm, app_config):
        captured["input_from"] = task.get("input_from")
        captured["prior_rows"] = isolated.get("prior_rows")
        return {"organized_data": {"summary": "1건", "rows": [{"v": 1}]}}

    monkeypatch.setitem(
        SUBAGENT_REGISTRY, "data_query",
        SubAgentSpec("data_query", "인프라 DB 조회", fake_handler),
    )
    collector = _alarm_collector()
    await _run_subagent_tool(
        "data_query", "2026년 6월 CPU 사용률 평균이 가장 높았던 서버 조회",
        worker_llm=None, app_config=mock_config, ambient_state={}, collector=collector,
    )
    assert captured["input_from"] == ["tool_alarm_query_1"]
    assert captured["prior_rows"] == {
        "tool_alarm_query_1": [{"server_name": "SV-WEB-001"},
                               {"server_name": "SV-BATCH-009"}],
    }


@pytest.mark.asyncio
async def test_run_subagent_tool_no_injection_without_signal(mock_config, monkeypatch):
    """게이트 미충족이면 기존과 동일하게 prior_rows 없이 실행된다(기존 동작 불변)."""
    captured = {}

    async def fake_handler(task, isolated, *, llm, app_config):
        captured["input_from"] = task.get("input_from")
        captured["prior_rows"] = isolated.get("prior_rows")
        return {"organized_data": {"summary": "1건", "rows": [{"v": 1}]}}

    monkeypatch.setitem(
        SUBAGENT_REGISTRY, "data_query",
        SubAgentSpec("data_query", "인프라 DB 조회", fake_handler),
    )
    await _run_subagent_tool(
        "data_query", "디스크 사용량 현황 조회",
        worker_llm=None, app_config=mock_config, ambient_state={},
        collector=_alarm_collector(),
    )
    assert captured["input_from"] == []
    assert captured["prior_rows"] is None
