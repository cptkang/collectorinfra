"""로컬 bash 프로파일의 쓰기 형태 차단 (D-235) — 검증 API만 호출한다(명령 실행 0 · LLM 0).

holmes 0.36.0 실측: `BashExecutorConfig`에는 `allow`·`deny`·`builtin_allowlist`뿐이고 validation은
리다이렉션·`tee`를 **판정하지 않는다** — bashlex는 세그먼트 분해에만 쓰이고 리다이렉트는 세그먼트 문자열
안에 남아 명령 prefix로 매칭된다. 그래서 `uptime > /tmp/x`가 `uptime` 허용으로 통과했다.
`vm_profile()`의 bash는 로컬 배치의 **유일한 L3 수집 수단**이라 끌 수 없으므로(plans/sre-agent/02 §8),
경계 가드로 쓰기 형태만 거부한다.
"""

import pytest
from holmes.core.tools import StructuredToolResultStatus, ToolInvokeContext, ToolsetStatusEnum
from holmes.core.tools import PrerequisiteCacheMode
from holmes.plugins.toolsets.bash.validation import ValidationStatus

from sre_agent.diagnosis import DiagnosisAgent
from sre_agent.infrastructure.bash_write_guard import write_form_reason
from sre_agent.settings import AgentSettings
from sre_agent.toolset_profiles import VM_DIAG_ALLOW, guarded, vm_profile


def _agent(**overrides) -> DiagnosisAgent:
    # `agent.llm`이 프로바이더를 고르므로 openai/ 접두 — 호출은 하지 않는다.
    settings = AgentSettings(_env_file=None, model="openai/test-model", api_key="dummy",
                             api_base="http://127.0.0.1:9/v1", max_steps=3, gemini_api_key=None, **overrides)
    return DiagnosisAgent(settings=settings, toolsets=vm_profile())


def _bash_tool(agent: DiagnosisAgent):
    return agent.llm.tool_executor.tools_by_name["bash"]


def _judge(agent: DiagnosisAgent, command: str, *prefixes: str) -> ValidationStatus:
    """검증만 호출한다 — `_invoke`(실행)는 부르지 않는다."""
    tool = _bash_tool(agent)
    ctx = ToolInvokeContext(llm=agent.llm.llm, max_token_count=1000, tool_name="bash", tool_call_id="t")
    return tool._validate_command(command, list(prefixes), ctx).status


# ── ① 쓰기 형태는 거부된다 ─────────────────────────────────────────

WRITE_FORMS = [
    ("uptime > /tmp/holmes_probe", ("uptime",)),
    ("ps aux >> /tmp/holmes_probe", ("ps",)),
    ("free -m &> /tmp/holmes_probe", ("free",)),
    ("sort -o /tmp/holmes_probe /etc/hosts", ("sort",)),
    ("sort --output=/tmp/holmes_probe /etc/hosts", ("sort",)),
    ("uniq /etc/hosts /tmp/holmes_probe", ("uniq",)),
    ("ps aux | tee /tmp/holmes_probe", ("ps", "tee")),
    (f"{guarded('journalctl -u nginx')} > /tmp/holmes_probe", (guarded("journalctl"),)),
]


@pytest.mark.parametrize(("command", "prefixes"), WRITE_FORMS)
def test_write_forms_denied(command, prefixes):
    assert _judge(_agent(), command, *prefixes) is ValidationStatus.DENIED


def test_denied_reason_reaches_the_model():
    """조용히 막지 않는다 — 도구 결과 error에 사유가 실려야 모델이 같은 시도를 반복하지 않는다."""
    agent = _agent()
    tool = _bash_tool(agent)
    ctx = ToolInvokeContext(llm=agent.llm.llm, max_token_count=1000, tool_name="bash", tool_call_id="t")
    params = {"command": "uptime > /tmp/holmes_probe", "suggested_prefixes": ["uptime"]}

    # requires_approval은 거부 대상에 승인을 요구하지 않는다(승인으로 뚫리지 않는다).
    assert tool.requires_approval(params, ctx) is None

    result = tool.invoke(params, context=ctx)   # 검증에서 끊기므로 셸은 실행되지 않는다
    assert result.status is StructuredToolResultStatus.ERROR
    assert "쓰기" in (result.error or "") and "/tmp/holmes_probe" in (result.error or "")


# ── ② 읽기 조사 명령은 그대로 통과한다 ────────────────────────────

KEEP = [
    ("uptime", ("uptime",)),
    ("ps -ef", ("ps",)),
    ("free -m", ("free",)),
    ("ss -tnp", ("ss",)),
    ("ip addr show", ("ip addr show",)),
    ("systemctl status nginx", ("systemctl status",)),
    ("grep -i error /var/log/syslog 2>/dev/null", ("grep",)),
    ("ps aux | sort -k3 -r | head -20", ("ps", "sort", "head")),
    (guarded("top -b -n 1"), (guarded("top -b -n 1"),)),
    (guarded("journalctl -u nginx --since '10 min ago'"), (guarded("journalctl"),)),
    (guarded("vmstat 1 5"), (guarded("vmstat"),)),
]


@pytest.mark.parametrize(("command", "prefixes"), KEEP)
def test_read_only_investigation_commands_still_allowed(command, prefixes):
    assert _judge(_agent(), command, *prefixes) is ValidationStatus.ALLOWED


def test_vm_diag_allow_is_untouched():
    # 프로파일 자체는 건드리지 않는다 — 가드는 실행 경계에만 있다.
    profile = vm_profile()
    assert profile["bash"]["enabled"] is True
    assert profile["bash"]["config"]["allow"] == VM_DIAG_ALLOW
    assert profile["bash"]["config"]["builtin_allowlist"] == "extended"


# ── ③ 변경 명령은 종전대로 승인 필요(비대화형=미실행) ─────────────


@pytest.mark.parametrize(("command", "prefix"), [
    ("kill -9 1234", "kill"),
    ("rm -rf /tmp/x", "rm"),
    ("systemctl restart nginx", "systemctl restart"),
    ("dmesg -C", "dmesg -C"),
])
def test_change_commands_still_need_approval(command, prefix):
    status = _judge(_agent(), command, prefix)
    assert status is not ValidationStatus.ALLOWED


# ── 가드 판정 함수 단위 (순수 함수 · 파싱만) ──────────────────────


@pytest.mark.parametrize("command", [c for c, _ in WRITE_FORMS])
def test_write_form_reason_detects(command):
    assert write_form_reason(command)


@pytest.mark.parametrize("command", [
    "uptime", "ps aux | grep java", "grep x /etc/hosts 2>/dev/null",
    "kubectl get pods 2>&1 | head", "sort /etc/hosts | uniq", "cat < /etc/hosts",
])
def test_write_form_reason_passes_read_only(command):
    assert write_form_reason(command) is None


def test_compound_and_substitution_forms():
    """복합문 안의 리다이렉트도 잡고(실측), 가드가 못 잡는 형태는 holmes 판정(승인 필요)에 맡긴다."""
    # for 루프 안의 `> 파일`도 bashlex 방문에서 잡힌다 → 거부
    assert write_form_reason("for f in a; do echo $f > /tmp/holmes_probe; done")
    # 명령 치환은 쓰기 형태가 아니다 → 가드 no-op, holmes가 승인 필요로 돌린다(비대화형=미실행)
    assert write_form_reason("echo $(rm -rf /tmp/holmes_probe)") is None
    assert _judge(_agent(), "echo $(rm -rf /tmp/holmes_probe)", "echo", "rm") is not ValidationStatus.ALLOWED


# ── 설치 경계: 가드는 단일 출처이고, 끄면 종전 동작 ───────────────


def test_flag_controls_installation(monkeypatch):
    """플래그는 **설치 여부**를 정한다(끄면 설치하지 않음 = 종전 동작).

    설치는 holmes 클래스 경계 교체라 **프로세스 전역·1회**다 — 같은 프로세스에서 한 에이전트라도 켜면
    그 뒤로 유지된다(`system_message_position_fix`와 같은 성격). 그래서 "이미 설치된 프로세스에서
    off 에이전트를 만들면 구멍이 되살아난다"를 단언하지 않는다(그건 사실이 아니다).
    """
    import sre_agent.diagnosis as diagnosis

    calls: list[bool] = []
    monkeypatch.setattr(diagnosis, "install_bash_write_guard", lambda: calls.append(True) or True)

    _agent(bash_write_guard_enabled=False)
    assert calls == []

    _agent(bash_write_guard_enabled=True)
    assert calls == [True]


def test_guard_is_idempotent_and_profile_agnostic():
    """여러 에이전트를 만들어도 가드 설치는 1회(멱등) — bash를 켜는 어느 프로파일에나 같게 적용된다."""
    from sre_agent.infrastructure.bash_write_guard import install_bash_write_guard
    from sre_agent.toolset_profiles import middleware_profile

    assert install_bash_write_guard() in (True, False)
    assert install_bash_write_guard() is False   # 이미 설치됨

    settings = AgentSettings(_env_file=None, model="openai/test-model", api_key="dummy",
                             api_base="http://127.0.0.1:9/v1", max_steps=3, gemini_api_key=None)
    agent = DiagnosisAgent(settings=settings, toolsets=middleware_profile())
    executor = agent._config.create_tool_executor(
        enable_all_toolsets_possible=False, prerequisite_cache=PrerequisiteCacheMode.DISABLED)
    by_name = {t.name: t for t in executor.toolsets}
    assert by_name["bash"].status is ToolsetStatusEnum.ENABLED
    ctx = ToolInvokeContext(llm=agent.llm.llm, max_token_count=1000, tool_name="bash", tool_call_id="t")
    tool = executor.tools_by_name["bash"]
    assert tool._validate_command("uptime > /tmp/holmes_probe", ["uptime"], ctx).status is ValidationStatus.DENIED
