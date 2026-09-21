"""원격 VM 프로파일·mcp_servers 확장 검증 (Plan 66 Wave 2-B′ R-A · Plan 06 §6·§94).

- remote_vm_profile 형태: bash **비활성**(2026-09-21 D-233 — 종전 내장 core)·prometheus/metrics 비활성·
  kubernetes/logs 비활성·나머지 유지.
- 로컬 VM 진단 명령(ps 등)이 원격 bash allowlist에 없음을 실 런타임 effective 리스트로 고정
  (수용 기준 #1 — 로컬/원격 프로파일 비대칭이 의도임).
- vm_profile(로컬) 불변 회귀.
- DiagnosisAgent가 mcp_servers를 holmes Config에 반영(Config.mcp_servers) +
  실 런타임 tool executor에 RemoteMCPToolset으로 등록(PrerequisiteCacheMode.DISABLED —
  캐시 히트 시 파싱 생략 함정 회피, mock 통과 ≠ 프로덕션).
- AgentSettings 신규 필드(polestar_mcp_url·polestar_mcp_token SecretStr·.env 누수 차단).
"""

from pydantic import SecretStr

from sre_agent.diagnosis import DiagnosisAgent
from sre_agent.settings import AgentSettings
from sre_agent.toolset_profiles import (
    REMOTE_VM_SHELL_NOTE,
    VM_DIAG_ALLOW,
    remote_vm_profile,
    vm_profile,
)


def make_settings(**overrides) -> AgentSettings:
    # 검증 대상 필드를 명시해 .env / 환경변수 누수를 차단한다
    defaults = {
        "model": "test/model",
        "api_key": None,
        "max_steps": 3,
        "polestar_mcp_url": "http://localhost:9099/sse",
        "polestar_mcp_token": None,
    }
    return AgentSettings(_env_file=None, **{**defaults, **overrides})


# 등록 검증용 샘플 폴스타 mcp_servers dict (Plan 04 §7.2 구조 — R-A는 이 dict를
# settings로부터 조립하지 않는다; 조립은 R-C 소관, 여기선 인자 배선만 고정한다).
SAMPLE_MCP_SERVERS: dict[str, dict] = {
    "polestar": {
        "config": {
            "mode": "sse",
            "url": "http://localhost:9099/sse",
            "headers": {"Authorization": "Bearer test-token"},
            "health_check_tool": "list_sources",
        },
    },
}


# ── remote_vm_profile 형태 ──────────────────────────────────────────


def test_remote_profile_shape():
    profile = remote_vm_profile()
    assert profile["kubernetes/logs"]["enabled"] is False
    # (D-119) 내장 prometheus/metrics는 비활성 유지 — PromQL은 mcp_server 도구로 소비
    assert profile["prometheus/metrics"]["enabled"] is False
    # bash는 아예 끈다 (2026-09-21 · D-233) — 종전 `builtin_allowlist="core"`에서 교체
    assert profile["bash"]["enabled"] is False
    assert "config" not in profile["bash"]


def test_remote_profile_bash_has_no_vm_diag_commands():
    # 로컬 VM 진단 명령이 원격 프로파일에 실릴 여지 자체가 없다(bash off)
    assert remote_vm_profile()["bash"] == {"enabled": False}
    assert VM_DIAG_ALLOW  # 로컬 상수는 그대로 존재(로컬 프로파일 전용)


def test_remote_shell_note_exists():
    # "로컬 셸은 대상 VM 아님" 지침 병기 (Plan 06 §6)
    assert "대상 VM" in REMOTE_VM_SHELL_NOTE
    assert "MCP" in REMOTE_VM_SHELL_NOTE


# ── vm_profile(로컬) 불변 회귀 ──────────────────────────────────────


def test_vm_profile_unchanged_by_remote_addition():
    # 로컬 프로파일은 여전히 extended + VM_DIAG_ALLOW를 사용한다
    local = vm_profile()
    assert local["bash"]["config"]["builtin_allowlist"] == "extended"
    assert "ps" in local["bash"]["config"]["allow"]
    # 로컬 프로파일에는 prometheus/metrics 키가 없다(원격에서만 명시 비활성)
    assert "prometheus/metrics" not in local


# ── 실 런타임: 원격 프로파일 bash effective allowlist에 VM 진단 명령 없음 ──


def test_remote_bash_effective_allow_excludes_vm_commands():
    # mock이 아닌 실 tool executor로 effective allowlist를 확인한다.
    # 수용 기준 #1: 원격 프로파일에서 ps 등 로컬 VM 진단 명령이 bash allowlist에 없음.
    from holmes.core.tools import PrerequisiteCacheMode

    agent = DiagnosisAgent(settings=make_settings(), toolsets=remote_vm_profile())
    executor = agent._config.create_tool_executor(
        enable_all_toolsets_possible=False,
        prerequisite_cache=PrerequisiteCacheMode.DISABLED,
    )
    by_name = {t.name: t for t in executor.toolsets}

    assert by_name["kubernetes/logs"].enabled is False
    assert by_name["prometheus/metrics"].enabled is False

    assert by_name["bash"].enabled is False

    # 대상 무관 toolset은 유지된다
    for name in ("connectivity_check", "core_investigation", "internet", "skills"):
        assert name in by_name


# ── DiagnosisAgent mcp_servers 배선 ────────────────────────────────


def test_agent_default_has_no_mcp_servers():
    # 기존 로컬 경로 불변: mcp_servers 미지정 시 None, toolsets는 vm_profile
    agent = DiagnosisAgent(settings=make_settings())
    assert agent._config.mcp_servers is None
    assert agent._config.toolsets == vm_profile()


def test_agent_reflects_mcp_servers_into_config():
    # holmes Config.mcp_servers 인자에 그대로 반영된다(실측 필드명·타입)
    agent = DiagnosisAgent(
        settings=make_settings(),
        toolsets=remote_vm_profile(),
        mcp_servers=SAMPLE_MCP_SERVERS,
    )
    assert agent._config.mcp_servers == SAMPLE_MCP_SERVERS


def test_agent_registers_mcp_server_at_runtime():
    # 실 런타임 tool executor에 RemoteMCPToolset으로 등록되고 접속 설정이 반영됨을
    # PrerequisiteCacheMode.DISABLED로 검증한다(라이브 서버 없이 등록·config 파싱만 확인).
    from holmes.core.tools import PrerequisiteCacheMode

    agent = DiagnosisAgent(
        settings=make_settings(),
        toolsets=remote_vm_profile(),
        mcp_servers=SAMPLE_MCP_SERVERS,
    )
    executor = agent._config.create_tool_executor(
        enable_all_toolsets_possible=False,
        prerequisite_cache=PrerequisiteCacheMode.DISABLED,
    )
    by_name = {t.name: t for t in executor.toolsets}
    assert "polestar" in by_name

    polestar = by_name["polestar"]
    assert type(polestar).__name__ == "RemoteMCPToolset"
    mcp_config = polestar._mcp_config
    assert mcp_config is not None
    assert str(mcp_config.url) == "http://localhost:9099/sse"
    assert mcp_config.mode.value == "sse"
    assert mcp_config.headers == {"Authorization": "Bearer test-token"}
    assert mcp_config.health_check_tool == "list_sources"


# ── AgentSettings 신규 필드 ────────────────────────────────────────


def test_settings_polestar_mcp_url_default():
    s = make_settings()
    assert s.polestar_mcp_url == "http://localhost:9099/sse"


def test_settings_polestar_mcp_token_is_secretstr():
    s = make_settings(polestar_mcp_token="secret-mcp-token")
    assert isinstance(s.polestar_mcp_token, SecretStr)
    assert s.polestar_mcp_token.get_secret_value() == "secret-mcp-token"
    # repr에 원문이 노출되지 않아야 한다
    assert "secret-mcp-token" not in repr(s)


def test_settings_polestar_mcp_token_default_none():
    assert make_settings().polestar_mcp_token is None


def test_settings_env_file_none_blocks_leak():
    # _env_file=None + 필드 명시로 .env 값이 새어들지 않는다
    s = make_settings(polestar_mcp_url="http://example.test:9099/sse")
    assert s.polestar_mcp_url == "http://example.test:9099/sse"


# ── 원격 bash 제거의 근거·효과 (2026-09-21 · D-233) — LLM 0회·명령 실행 0 ──


def _remote_tools() -> set[str]:
    """운영 경로(`DiagnosisAgent.llm`)가 실제로 LLM에 붙이는 도구 이름.

    `agent.llm`은 holmes가 모델 문자열로 프로바이더를 고르므로 `openai/` 접두를 준다(호출은 하지 않는다).
    """
    settings = make_settings(model="openai/test-model", api_key="dummy", api_base="http://127.0.0.1:9/v1")
    agent = DiagnosisAgent(settings=settings, toolsets=remote_vm_profile())
    return set(agent.llm.tool_executor.tools_by_name)


def test_remote_runtime_has_no_shell_or_local_file_tools():
    tools = _remote_tools()
    assert "bash" not in tools and "read_image_file" not in tools
    # 조사에 쓰는 대상 무관 도구는 남는다(MCP 미등록이라 폴스타 도구는 여기 없다)
    assert {"TodoWrite", "fetch_skill", "fetch_webpage", "tcp_check"} <= tools


# 종전 `core` 허용목록이 통과시키던 형태 — 제거 근거의 실측 고정.
# holmes가 이 목록을 바꾸면 이 테스트가 먼저 알려 준다(우리 판단의 전제가 흔들리는 지점).
WRITE_FORMS = (
    'echo hi > /tmp/holmes_probe',                 # 리다이렉트 생성
    'grep a /etc/hosts >> /tmp/holmes_probe',      # 리다이렉트 추가
    'sort -o /tmp/holmes_probe /etc/hosts',        # -o 출력 파일
    'uniq /etc/hosts /tmp/holmes_probe',           # 두 번째 위치 인자가 출력 파일
)


def test_core_allowlist_would_have_allowed_write_forms_and_kubectl():
    """왜 지웠는가 — `core`는 읽기 전용이 아니고 k8s 명령을 포함한다(명령은 실행하지 않는다)."""
    from holmes.plugins.toolsets.bash.validation import (
        CORE_ALLOW_LIST,
        ValidationStatus,
        validate_command,
    )

    for cmd in WRITE_FORMS:
        result = validate_command(cmd, [cmd.split()[0]], list(CORE_ALLOW_LIST), [])
        assert result.status is ValidationStatus.ALLOWED, f"전제 변화: {cmd!r}"
    assert validate_command("kubectl get pods", ["kubectl get"], list(CORE_ALLOW_LIST), []).status \
        is ValidationStatus.ALLOWED
    # 우리 원격 프로파일에는 그 목록을 쓰는 bash 자체가 없다
    assert "bash" not in _remote_tools()


def test_local_profile_still_refuses_change_commands_without_approval():
    """④ 유지 — 로컬 배치(bash 존재)에서 변경 명령은 허용목록 밖이라 승인 필요(비대화형=미실행).

    로컬 `vm_profile()`은 이번 축소 대상이 아니다(SREAgent D-004 범위). 그 경계가 유지되는지만 고정한다.
    """
    from holmes.plugins.toolsets.bash.validation import (
        ValidationStatus,
        get_effective_lists,
        validate_command,
    )
    from holmes.plugins.toolsets.bash.common.config import BashExecutorConfig

    allow, deny = get_effective_lists(BashExecutorConfig(**vm_profile()["bash"]["config"]))
    for cmd, prefix in (("kill -9 1234", "kill"), ("rm -rf /tmp/x", "rm"),
                        ("systemctl restart nginx", "systemctl restart"),
                        ("dmesg -C", "dmesg -C")):
        status = validate_command(cmd, [prefix], allow, deny).status
        assert status is not ValidationStatus.ALLOWED, f"{cmd!r} 가 허용됐다"
