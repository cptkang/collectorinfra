"""VM 진단용 toolset 프로파일 — holmesgpt 0.36.0 내장 toolset 실측(51개) 기준.

기본 활성 6개(bash, connectivity_check, core_investigation, internet,
kubernetes/logs, skills) 중 k8s 전용인 kubernetes/logs만 끄고,
bash allowlist를 VM 진단 명령으로 확장한다.

allow/deny는 prefix 매칭이므로, 같은 명령의 쓰기 형태가 허용 prefix에
포섭되지 않도록 읽기 전용 형태로 좁혀 등록한다
(예: "ip" 전체 허용 시 "ip link set …"까지 허용됨 → "ip addr show" 등으로 한정).
"""

# 읽기 전용 VM 진단 명령 prefix (builtin extended 리스트에 추가됨)
VM_DIAG_ALLOW: list[str] = [
    # CPU·메모리·프로세스
    "uptime", "ps", "top -b", "free", "vmstat", "mpstat", "pidstat", "sar",
    # 디스크·I/O
    "iostat", "lsblk", "smartctl -H",
    # 네트워크 (읽기 형태로 한정)
    "ss", "netstat", "ping -c", "dig", "nslookup", "traceroute",
    "ip addr show", "ip link show", "ip route show", "ip -s link show", "ip neigh show",
    # 로그·서비스·커널
    "journalctl", "systemctl status", "systemctl list-units", "systemctl is-active",
    "dmesg", "lsof", "sysctl -a", "numastat", "last",
]

# 허용 prefix에 포섭되는 쓰기·삭제 형태를 명시적으로 차단
VM_DIAG_DENY: list[str] = [
    "dmesg -C", "dmesg --clear",
    "journalctl --vacuum", "journalctl --rotate", "journalctl --flush",
]

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
