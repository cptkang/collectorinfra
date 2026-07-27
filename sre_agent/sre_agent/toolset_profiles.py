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
