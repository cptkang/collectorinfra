"""실 런타임 toolset 활성 = 프로파일 dict — holmes 홈 캐시가 덮지 못한다 (2026-09-17 MLX 검증 결함).

실측(holmesgpt 0.36.0): `create_toolcalling_llm()` 기본값(`prerequisite_cache=ENABLED` ·
`enable_all_toolsets_possible=True`)은 `~/.holmes/toolsets_status.json`이 있으면
`toolset_manager.load_toolset_with_status`가 **캐시의 enabled로 프로파일 설정을 덮는다**.
그래서 `{"bash": {"enabled": False}}`를 줘도 `bash`·`fetch_webpage`가 LLM에 붙었고,
`toolsets={}` 스모크가 개발 맥에서 `kubectl get`을 실제 실행했다.

기존 프로파일 테스트(test_vm_profile·test_remote_vm_profile)는 `create_tool_executor`를
캐시 off로 **직접** 불러 검증했기에 운영 경로(`DiagnosisAgent.llm`)의 캐시 의존을 못 잡았다
(mock/우회 통과 ≠ 프로덕션). 여기서는 **`agent.llm`을 그대로** 만들고, 홈 캐시 대신
`tmp_path`의 **낡은 캐시**(모든 기본 toolset enabled)를 가리키게 해 결정적으로 재현한다.
홈 디렉토리 파일은 읽지도 쓰지도 않는다. LLM 호출 0.
"""

import json

import pytest

from sre_agent.diagnosis import DiagnosisAgent
from sre_agent.settings import AgentSettings
from sre_agent.toolset_profiles import (
    NO_HOST_ACCESS_TOOLS,
    no_host_access_profile,
    remote_vm_profile,
    vm_profile,
)

# 낡은 캐시 — 프로파일이 끄는 toolset을 전부 "enabled"로 적어 둔다(덮어쓰기 재현용).
_STALE_ENABLED = ("bash", "internet", "connectivity_check", "kubernetes/logs",
                  "prometheus/metrics", "core_investigation", "skills")


@pytest.fixture
def stale_cache(tmp_path, monkeypatch):
    """holmes 기본 캐시 경로를 tmp의 낡은 캐시로 돌린다 — 홈(~/.holmes)은 건드리지 않는다."""
    import holmes.core.toolset_manager as tm

    path = tmp_path / "toolsets_status.json"
    path.write_text(json.dumps([
        {"name": n, "status": "enabled", "enabled": True, "type": "built-in", "path": None, "error": None}
        for n in _STALE_ENABLED
    ]))
    monkeypatch.setattr(tm, "DEFAULT_TOOLSET_STATUS_LOCATION", str(path))
    return path


def _agent(toolsets) -> DiagnosisAgent:
    # 검증 대상 필드를 명시해 .env 누수 차단. 루프백 더미 — 호출하지 않는다.
    s = AgentSettings(_env_file=None, model="openai/test-model", api_key="dummy",
                      api_base="http://127.0.0.1:9/v1", max_steps=3, gemini_api_key=None)
    return DiagnosisAgent(settings=s, toolsets=toolsets)


def _tools(agent: DiagnosisAgent) -> set[str]:
    return set(agent.llm.tool_executor.tools_by_name)


def test_disabled_toolsets_stay_disabled_despite_stale_cache(stale_cache):
    before = stale_cache.read_text()
    agent = _agent({"bash": {"enabled": False}, "internet": {"enabled": False}})

    tools = _tools(agent)

    assert "bash" not in tools
    assert "fetch_webpage" not in tools
    # 캐시를 읽지도 갱신하지도 않는다 — 프로파일이 유일한 활성 정본
    assert stale_cache.read_text() == before


def test_remote_profile_disables_k8s_and_prometheus_despite_stale_cache(stale_cache):
    by_name = {t.name: t for t in _agent(remote_vm_profile()).llm.tool_executor.toolsets}

    assert by_name["kubernetes/logs"].enabled is False
    assert by_name["prometheus/metrics"].enabled is False
    assert "fetch_pod_logs" not in _tools(_agent(remote_vm_profile()))


def test_no_host_access_profile_exposes_only_allowed_tools(stale_cache):
    tools = _tools(_agent(no_host_access_profile()))

    # 셸·파일·웹·네트워크 프로브·k8s 도구가 하나도 없다(허용목록 방식 — holmes 상향으로 새 기본
    # toolset이 생기면 여기서 드러난다)
    assert tools <= NO_HOST_ACCESS_TOOLS, tools - NO_HOST_ACCESS_TOOLS


def test_empty_toolsets_is_not_empty(stale_cache):
    """`toolsets={}`는 "빈 toolset"이 아니다 — holmes 내장 기본 toolset이 켜진다(스모크 오해의 근원)."""
    assert "bash" in _tools(_agent({}))


def test_vm_profile_allowlist_reaches_system_prompt_at_creation(stale_cache):
    """캐시 히트 시 bash config 파싱이 첫 도구 사용까지 미뤄져 시스템 프롬프트에 허용목록이 없었다."""
    from holmes.core.prompt import build_initial_ask_messages
    from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig

    agent = _agent(vm_profile())
    bash = next(t for t in agent.llm.tool_executor.toolsets if t.name == "bash")
    assert isinstance(bash.config, BashExecutorConfig)

    system = build_initial_ask_messages(
        initial_user_prompt="q", file_paths=None,
        tool_executor=agent.llm.tool_executor, system_prompt_additions=None,
    )[0]["content"]
    assert "uptime" in system   # VM_DIAG_ALLOW가 첫 스텝부터 LLM에 보인다
