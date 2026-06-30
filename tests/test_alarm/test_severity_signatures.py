"""severity_signatures.scan_signature_severity 단위 테스트 (Plan 52 E3).

Plan 51 §A.1 OS 장애 시그니처 치트시트의 결정적 스캔을 검증한다:
심각도3/2 시그니처군 매칭, benign→None, 대소문자 무시, 다중매칭 최고값.
"""

from __future__ import annotations

import pytest

from src.alarm.domain.severity_signatures import scan_signature_severity

# (로그 텍스트, 기대 심각도, 기대 라벨) — 각 시그니처군 대표 1건씩
SEV3_CASES = [
    ("Out of memory: Killed process 1234 (java) total-vm:8G, anon-rss:6G", 3, "OOM 강제종료"),
    ("oom-kill:constraint=CONSTRAINT_NONE,global_oom,task=java", 3, "OOM 강제종료"),
    ("java invoked oom-killer: gfp_mask=0x100cca", 3, "OOM 강제종료"),
    ("watchdog: BUG: soft lockup - CPU#2 stuck for 23s! [kworker:91]", 3, "소프트 락업"),
    ("INFO: task nginx:9001 blocked for more than 120 seconds.", 3, "Hung task"),
    ("rcu: INFO: rcu_sched detected stalls on CPUs/tasks", 3, "RCU stall"),
    ("Kernel panic - not syncing: Fatal exception", 3, "커널 패닉"),
    ("EXT4-fs error (device sda1): ext4_find_entry:1463", 3, "FS/블록 오류"),
    ("blk_update_request: I/O error, dev sda, sector 12345", 3, "FS/블록 오류"),
    ("Buffer I/O error on device sdb, logical block 999", 3, "FS/블록 오류"),
    ("EXT4-fs (sda1): Remounting filesystem read-only", 3, "FS/블록 오류"),
    ("XFS (dm-0): Corruption detected. Unmount and run xfs_repair", 3, "FS/블록 오류"),
    ("XFS (dm-0): Shutting down filesystem", 3, "FS/블록 오류"),
    ("nginx[1234]: segfault at 0 ip 00007f sp 00007ff error 4 in libc.so", 3, "Segfault"),
    ("traps: app[321] general protection ip:4012 sp:7ff in app", 3, "GP fault"),
]

SEV2_CASES = [
    ("nf_conntrack: table full, dropping packet", 2, "conntrack 고갈"),
    ("possible SYN flooding on port 443. Sending cookies.", 2, "SYN 플러드"),
    ("connect: Cannot assign requested address", 2, "포트 고갈"),
    ("ping: local error: Message too long, mtu=1500", 2, "MTU 블랙홀"),
    ("accept: Too many open files", 2, "FD 고갈"),
    ("fork: retry: Resource temporarily unavailable", 2, "스레드/PID 고갈"),
    ("pthread_create failed: Resource temporarily unavailable", 2, "스레드/PID 고갈"),
    ("nginx.service: Start request repeated too quickly.", 2, "systemd 플래핑"),
    ("foo.service: start-limit-hit", 2, "systemd 플래핑"),
    ("Failed password for root from 1.2.3.4 port 22 ssh2", 2, "인증 브루트포스"),
    ("pam_unix(sshd:auth): authentication failure; rhost=1.2.3.4", 2, "인증 브루트포스"),
    ("nfs: server x not responding, Stale file handle", 2, "NFS Stale"),
]


@pytest.mark.parametrize("text,severity,label", SEV3_CASES)
def test_sev3_signatures(text, severity, label):
    result = scan_signature_severity(text)
    assert result == (severity, label), f"{text!r} → {result}"


@pytest.mark.parametrize("text,severity,label", SEV2_CASES)
def test_sev2_signatures(text, severity, label):
    result = scan_signature_severity(text)
    assert result == (severity, label), f"{text!r} → {result}"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Started Session 12 of user deploy.",
        "GET /health 200 OK",
        "CPU usage 45% within threshold",
        "user login successful from console",
        "backup completed in 12.3s",
    ],
)
def test_benign_returns_none(text):
    assert scan_signature_severity(text) is None


def test_case_insensitive():
    # 대문자/소문자 혼용·전부 대문자 모두 매칭되어야 한다
    assert scan_signature_severity("OUT OF MEMORY: KILLED PROCESS 1") == (3, "OOM 강제종료")
    assert scan_signature_severity("Possible Syn Flooding on port 80") == (2, "SYN 플러드")


def test_multi_match_returns_highest_severity():
    # sev2(SYN 플러드) + sev3(OOM)가 동시 등장 → 최고 심각도(3) 반환
    text = "possible SYN flooding on port 80 ... later Out of memory: Killed process 5"
    result = scan_signature_severity(text)
    assert result is not None and result[0] == 3
    assert result[1] == "OOM 강제종료"


def test_multi_match_two_sev2_returns_definition_order():
    # 동일 심각도(2) 다중 매칭 시 정의 순서가 앞선 규칙(conntrack 고갈)을 반환
    text = "nf_conntrack: table full ... and Too many open files"
    result = scan_signature_severity(text)
    assert result == (2, "conntrack 고갈")


def test_return_type_is_tuple_or_none():
    hit = scan_signature_severity("Kernel panic - not syncing")
    assert isinstance(hit, tuple) and len(hit) == 2
    assert isinstance(hit[0], int) and isinstance(hit[1], str)
    assert scan_signature_severity("nothing interesting here") is None
