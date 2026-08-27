"""VM 진단용 toolset 프로파일 — holmesgpt 0.36.0 내장 toolset 실측(51개) 기준.

기본 활성 6개(bash, connectivity_check, core_investigation, internet,
kubernetes/logs, skills) 중 k8s 전용인 kubernetes/logs만 끄고,
bash allowlist를 VM 진단 명령으로 확장한다.

allow/deny는 prefix 매칭이므로, 같은 명령의 쓰기 형태가 허용 prefix에
포섭되지 않도록 읽기 전용 형태로 좁혀 등록한다
(예: "ip" 전체 허용 시 "ip link set …"까지 허용됨 → "ip addr show" 등으로 한정).
"""

# ══════════════════════════════════════════════════════════════════════
# 부하 가드 (Plan 78 W2-6·W3-6 · `docs/25_host_investigation_load_guard.md`)
# ══════════════════════════════════════════════════════════════════════
#
# **왜 필요한가**: 본 조사의 대표 시나리오가 *이미 포화된 서버*를 조사하는 것이다.
# 조사가 장애를 악화시키면 계획의 목적 자체가 무너진다(78 §4.4 갭 ③ · ETCLOVG E 계층).
#
# **왜 allowlist로 강제하는가**: `system_prompt_additions`(지침 주입)를 넘기는
# **프로덕션 호출부가 0건**이라(2026-08-27 실측) 지침만으로는 아무것도 강제되지 않는다.
# allow/deny는 **prefix 매칭**이므로, 무거운 명령을 *가드 형태로만* 등록하면
# 가드 없는 형태가 자동으로 거부된다 — 이것이 이 파일에서 얻을 수 있는 유일한 실효 강제다.
#
#   L-1 `nice` 우선순위 하향   — 포화된 CPU를 조사가 더 뺏지 않는다
#   L-2 명령 자체 timeout      — 중앙에서 끊어도 **대상 호스트의 프로세스는 계속 돈다**
#   L-3 반복 샘플링 1회 고정   — `top -b`는 `-n` 없으면 **무한 실행**된다
#
# 값은 상수로 노출한다 — 하드코딩된 숫자를 명령 문자열 안에 흩어 두면 조정이 불가능해진다.
LOAD_GUARD_TIMEOUT_SECONDS: int = 20
LOAD_GUARD_NICE: int = 10
LOAD_GUARD_PREFIX: str = f"timeout {LOAD_GUARD_TIMEOUT_SECONDS} nice -n {LOAD_GUARD_NICE} "


def guarded(command: str) -> str:
    """명령에 부하 가드 접두를 붙인다 (L-1 + L-2).

    allow/deny **양쪽에 같은 함수**를 쓴다 — deny를 bare 형태로만 두면
    `timeout … nice … journalctl --vacuum`이 deny prefix를 비껴가 **차단이 우회된다**.

    Args:
        command: 원 명령 prefix

    Returns:
        `timeout N nice -n P <command>` 형태
    """
    return LOAD_GUARD_PREFIX + command


# 가벼운 명령 — 즉시 끝나고 출력이 유계다. 래핑하지 않는다(조사 마찰 최소화).
LIGHT_DIAG_COMMANDS: tuple[str, ...] = (
    # CPU·메모리·프로세스
    "uptime", "ps", "free",
    # 디스크
    "lsblk",
    # 네트워크 (읽기 형태로 한정)
    "ss", "ping -c", "dig", "nslookup",
    "ip addr show", "ip link show", "ip route show", "ip -s link show", "ip neigh show",
    # 서비스·커널
    "systemctl status", "systemctl list-units", "systemctl is-active",
    "sysctl -a", "numastat", "last",
)

# 무거운 명령 — **반복 샘플링·대용량 스캔·장시간 실행**이 가능하다.
# 이들은 **가드 형태로만** allow에 오르므로, 가드 없는 형태는 거부된다.
#
# `top -b -n 1`에 주의: 종전 allow는 `"top -b"` 였다 — `-n`이 없으면 **무한 실행**되므로
# 포화된 호스트를 조사하다 조사 자체가 호스트를 붙드는 형태였다(L-3가 지목한 바로 그것).
HEAVY_DIAG_COMMANDS: tuple[str, ...] = (
    "top -b -n 1",                                   # L-3: 1회 스냅샷 고정
    "vmstat", "mpstat", "pidstat", "sar", "iostat",   # 간격 인자를 주면 무한 반복
    "smartctl -H",                                   # 디스크 접근
    "netstat", "traceroute",                         # 대량 연결·장시간
    "journalctl", "dmesg", "lsof",                   # 대용량 스캔
)

# 읽기 전용 VM 진단 명령 prefix (builtin extended 리스트에 추가됨)
VM_DIAG_ALLOW: list[str] = [
    *LIGHT_DIAG_COMMANDS,
    *(guarded(c) for c in HEAVY_DIAG_COMMANDS),
]

# 허용 prefix에 포섭되는 쓰기·삭제 형태를 명시적으로 차단.
# **가드 형태도 함께 차단한다** — 가드 접두가 붙으면 bare deny prefix를 비껴가기 때문이다.
_DESTRUCTIVE_FORMS: tuple[str, ...] = (
    "dmesg -C", "dmesg --clear",
    "journalctl --vacuum", "journalctl --rotate", "journalctl --flush",
)
VM_DIAG_DENY: list[str] = [
    *_DESTRUCTIVE_FORMS,
    *(guarded(c) for c in _DESTRUCTIVE_FORMS),
]

# 부하 가드 지침 — allowlist가 강제하는 형태를 LLM에게 **그대로** 알려준다.
# 강제만 있고 안내가 없으면 무거운 명령이 전부 거부되어 조사가 무력화된다.
LOAD_GUARD_NOTE: str = (
    "부하 가드(필수): 조사 대상은 이미 포화 상태일 수 있다. **조사가 장애를 악화시켜서는 안 된다.**\n"
    f"무거운 명령({', '.join(HEAVY_DIAG_COMMANDS)})은 반드시 아래 형태로만 실행할 것 — "
    "다른 형태는 거부된다.\n"
    f"    {LOAD_GUARD_PREFIX}<명령>\n"
    f"예) {guarded('journalctl -u nginx --since \'10 min ago\'')}\n"
    f"    {guarded('top -b -n 1')}\n"
    "가벼운 명령(uptime·ps·free·ss·ip … show·systemctl status 등)은 접두 없이 그대로 쓴다.\n"
    "`top`은 반드시 `-n 1`로 **1회 스냅샷**만 뜬다 — 생략하면 무한 실행된다.\n"
)

# 원격 VM 진단(bastion에서 실행) 시 옵트인으로 extra_allow에 넘길 것
REMOTE_SSH_ALLOW: list[str] = ["ssh"]

# 원격 VM 진단(중앙 실행 — 대상 VM에 SSH/에이전트 미배포) 시 로컬 셸이
# 진단 대상 VM이 아님을 LLM에 명시하는 지침. system_prompt_additions로 주입한다
# (Plan 06 §6 — "로컬 셸은 대상 VM 아님" 지침 병기).
REMOTE_VM_SHELL_NOTE: str = (
    "이 에이전트는 중앙 호스트에서 실행되며, 로컬 셸(bash)은 진단 대상 VM이 아니다. "
    "로컬에서 실행되는 ps·free 등의 출력은 대상 VM의 정보가 아니므로 진단 근거로 삼지 말 것. "
    "대상 VM의 메트릭·자원·프로세스·구성은 등록된 MCP 도구(폴스타 · PromQL)로만 조회한다."
)


# 미들웨어 조사 초점 지침 (Plan 78 W7-1 · D-168). system_prompt_additions로 주입한다.
#
# **프로파일의 실체는 새 명령이 아니라 기존 명령의 조합·해석이다.** allowlist를 확장하지 않는
# 이유가 여기 있다 — 필요한 신호는 이미 VM_DIAG_ALLOW로 전부 얻어지고, 명령을 새로 열면
# 그만큼 공격 표면이 늘어난다.
MIDDLEWARE_FOCUS_NOTE: str = (
    "이 조사는 대상 호스트의 **미들웨어**(WAS·웹서버·캐시·메시징 등)를 OS 레벨 신호로 파악하는 것이 목적이다.\n"
    "\n"
    "수집 순서를 지킬 것:\n"
    "1. `ps`로 전체 프로세스와 기동 인자를 먼저 확보한다. 여기서 대상을 좁힌다.\n"
    "2. 좁혀진 **pid에 한해** `pidstat`(자원 점유)과 `ss`(리스닝 포트·연결 수)를 본다.\n"
    "3. 필요할 때만 `journalctl`·`systemctl status`로 최근 로그·서비스 상태를 확인한다.\n"
    "   호스트 전체 로그를 처음부터 긁지 말 것 — 컨텍스트가 폭증하고 신호가 묻힌다.\n"
    "\n"
    # 부하 가드 문구는 **단일 출처**(LOAD_GUARD_NOTE)를 참조한다 — 사본을 두면
    # allowlist가 강제하는 형태와 지침이 조용히 어긋난다(D-053).
    + LOAD_GUARD_NOTE
    + "\n"
    "판정 위임: 미들웨어의 **종류를 스스로 추정하지 말 것**. `ps` 출력(pid와 전체 cmdline)을 "
    "그대로 실어 보고하면 후단의 결정적 매처가 선언 규칙으로 판정한다. "
    "추정은 같은 입력에 다른 답을 낼 수 있어 조사 근거로 쓸 수 없다."
)


def middleware_profile(extra_allow: list[str] | None = None) -> dict[str, dict]:
    """미들웨어 조사용 toolset 프로파일을 반환한다 (Plan 78 W7-1 · D-168).

    **`vm_profile`과 allowlist가 동일하다 — 확장이 0이다.** W7-1이 요구하는 신호
    (프로세스·기동 인자 / 리스닝 포트 / 자원 점유 / 시스템 로그)가 전부 VM_DIAG_ALLOW로
    이미 얻어지기 때문이며, 이것이 W7-1을 "선행조건 0"으로 만드는 근거다.

    그러면 왜 별도 프로파일인가 — **차이는 조사 초점**(MIDDLEWARE_FOCUS_NOTE)에 있다.
    수집 순서(ps로 좁힌 뒤 pid 단위로), 부하 가드(nice·timeout), 그리고 **종류 판정을
    LLM이 하지 않고 후단 결정적 매처에 넘긴다**는 규약이 프로파일의 실체다.

    호출자는 이 dict를 Config.toolsets에, MIDDLEWARE_FOCUS_NOTE를
    system_prompt_additions에 함께 넘겨야 의도한 동작이 된다.

    Args:
        extra_allow: 옵트인 추가 허용 명령(예: 원격 실행 시 REMOTE_SSH_ALLOW).
            기본값은 넓히지 않는다 — 호출자가 명시할 때만 확장된다.

    Returns:
        Config.toolsets에 넘길 프로파일 dict.
    """
    return vm_profile(extra_allow)


def vm_profile(extra_allow: list[str] | None = None) -> dict[str, dict]:
    """Config.toolsets에 넘길 VM 진단 프로파일을 반환한다."""
    return {
        "kubernetes/logs": {"enabled": False},
        "bash": {
            "enabled": True,
            "config": {
                "allow": VM_DIAG_ALLOW + (extra_allow or []),
                "deny": VM_DIAG_DENY,
                "builtin_allowlist": "extended",
            },
        },
    }


def remote_vm_profile() -> dict[str, dict]:
    """원격 VM 진단용 toolset 프로파일을 반환한다 (Plan 06 §6 · D-119).

    로컬 VM 진단 프로파일(vm_profile)과 달리 다음을 지킨다:

    - bash를 확장하지 않는다(내장 core 텍스트 유틸만 · builtin_allowlist="core").
      중앙 실행 호스트의 셸 출력은 대상 VM 정보가 아니므로 VM_DIAG_ALLOW를 붙이지
      않는다(로컬/원격 프로파일 비대칭은 의도 — LLM의 대상 오인 방지).
      "로컬 셸은 대상 VM 아님" 지침은 REMOTE_VM_SHELL_NOTE로 병기해
      system_prompt_additions로 주입한다.
    - kubernetes/logs 비활성(k8s 아님 — D-004).
    - prometheus/metrics 비활성 유지(D-119) — PromQL은 mcp_server 도구로 소비한다
      (내장 toolset A안 복귀 시에만 활성).
    - connectivity_check·core_investigation·internet·skills는 기본 활성 유지(대상 무관).

    대상 VM의 실 데이터(폴스타 메타·알람·PromQL 시계열)는 이 dict가 아니라
    Config.mcp_servers 등록으로 조회한다(DiagnosisAgent(mcp_servers=...)).
    """
    return {
        "kubernetes/logs": {"enabled": False},
        "prometheus/metrics": {"enabled": False},
        "bash": {
            "enabled": True,
            "config": {
                "allow": [],
                "builtin_allowlist": "core",
            },
        },
    }
