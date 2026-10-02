"""VM 진단 프로파일 검증 — Config 주입과 실 런타임(tool executor) 반영을 모두 확인한다."""

from sre_agent.diagnosis import DiagnosisAgent
from sre_agent.settings import AgentSettings
from sre_agent.toolset_profiles import VM_DIAG_ALLOW, VM_DIAG_DENY, vm_profile


def make_settings() -> AgentSettings:
    # 검증 대상 필드를 명시해 .env 누수를 차단한다
    return AgentSettings(_env_file=None, model="test/model", api_key=None, max_steps=3)


def test_vm_profile_shape():
    profile = vm_profile()
    assert profile["kubernetes/logs"]["enabled"] is False
    assert profile["bash"]["config"]["builtin_allowlist"] == "extended"
    assert "uptime" in profile["bash"]["config"]["allow"]


def test_vm_profile_extra_allow():
    profile = vm_profile(extra_allow=["ssh"])
    assert "ssh" in profile["bash"]["config"]["allow"]
    # 기본 allow는 변형되지 않아야 한다
    assert "ssh" not in VM_DIAG_ALLOW


def test_agent_uses_vm_profile_by_default():
    agent = DiagnosisAgent(settings=make_settings())
    assert agent._config.toolsets == vm_profile()


def test_vm_profile_applied_at_runtime():
    # mock이 아닌 실제 tool executor 생성으로 오버라이드 반영을 검증한다.
    # prerequisite 캐시 히트 시 config 파싱(prerequisites_callable)이 생략되어
    # dict로 남으므로, 캐시를 꺼서 결정적으로 검증한다.
    from holmes.core.tools import PrerequisiteCacheMode

    agent = DiagnosisAgent(settings=make_settings())
    executor = agent._config.create_tool_executor(
        enable_all_toolsets_possible=False,
        prerequisite_cache=PrerequisiteCacheMode.DISABLED,
    )
    by_name = {t.name: t for t in executor.toolsets}

    assert by_name["kubernetes/logs"].enabled is False
    bash = by_name["bash"]
    assert bash.enabled is True
    assert bash.status.value == "enabled"
    for prefix in VM_DIAG_ALLOW:
        assert prefix in bash.config.allow
    for prefix in VM_DIAG_DENY:
        assert prefix in bash.config.deny
