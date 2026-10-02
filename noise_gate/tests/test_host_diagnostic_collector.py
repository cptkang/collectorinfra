"""post-gate L3 수집기 어댑터 (plans/91 1-6 · Plan 60 §18 E8 (나)안 · D-189 경로 B).

Plan 60 §18.6 수용 기준 ④(변경명령 부재·허용목록 외 차단·마스킹·감사)·②(상태지문 escalate-only)를 고정한다.
전송은 페이크 runner — ssh 없이 결정적으로 검증한다.
"""

from __future__ import annotations

import asyncio

import pytest

from noise_gate.infrastructure.host_diagnostic_collector import (
    L3_PROFILES,
    LOAD_GUARD_PREFIX,
    collect,
    compare_fingerprints,
    derive_fingerprint,
    guarded,
    is_allowed,
    resolve_profile,
)


# ── ④ 허용목록 · 변경명령 부재 · 부하 가드 ────────────────────────────────

def test_profiles_contain_no_change_commands():
    for cmds in L3_PROFILES.values():
        for c in cmds:
            assert is_allowed(c), c
            assert not any(m in c for m in ("kill", "renice", "sysctl -w", "restart", "-C", "--vacuum"))


@pytest.mark.parametrize("cmd", [
    "kill -9 1", "renice -n 5 -p 1", "sysctl -w vm.drop_caches=3", "systemctl restart nginx",
    "dmesg -C", "journalctl --vacuum-time=1d", "free -m; rm -rf /", "free -m | sh", "cat /proc/meminfo && reboot",
    "free", "ps aux", "",
])
def test_non_profile_or_change_commands_are_rejected(cmd):
    assert not is_allowed(cmd)


def test_guard_prefix_is_timeout_nice_and_idempotent():
    assert LOAD_GUARD_PREFIX == "timeout 20 nice -n 10 "
    assert guarded("free -m") == "timeout 20 nice -n 10 free -m"
    assert guarded(guarded("free -m")) == guarded("free -m")


def test_resolve_profile_map_only_accepts_existing_profiles():
    assert resolve_profile("memory") == ("memory", L3_PROFILES["memory"])
    assert resolve_profile("memory", "memory=disk") == ("disk", L3_PROFILES["disk"])
    assert resolve_profile("memory", "memory=nope") == ("memory", L3_PROFILES["memory"])
    assert resolve_profile(None) == ("", ())


# ── 수집 · 마스킹 · 부분 실패 ────────────────────────────────────────────

FREE = "              total        used        free\nMem:          64000       60000        4000\nSwap:          8000        1200        6800\n"
MEMINFO = "MemTotal:       65536000 kB\nMemAvailable:    2621440 kB\n"
PS = "USER PID %CPU %MEM RSS COMMAND\njava 4242 10.0 55.0 30000000 java -Dpassword=hunter2 -jar app.jar\n"
DMESG = "[1.0] Out of memory: Killed process 4242 (java)\n"


def _runner(outputs: dict[str, tuple[int, str]], fail: set[str] = frozenset()):
    seen: list[str] = []

    async def _run(host, command):
        seen.append(command)
        bare = command[len(LOAD_GUARD_PREFIX):]
        if bare in fail:
            raise OSError("ssh down")
        return outputs.get(bare, (0, ""))

    _run.seen = seen  # type: ignore[attr-defined]
    return _run


def test_collect_runs_only_guarded_profile_commands_and_masks_secrets():
    run = _runner({"free -m": (0, FREE), "cat /proc/meminfo": (0, MEMINFO), "ps aux --sort=-%mem": (0, PS),
                   "dmesg": (0, DMESG), "vmstat 1 3": (1, "vmstat: not found")})
    diag = asyncio.run(collect("h1", "memory", run))
    assert all(c.startswith(LOAD_GUARD_PREFIX) for c in run.seen)
    assert [c[len(LOAD_GUARD_PREFIX):] for c in run.seen] == list(L3_PROFILES["memory"])
    assert diag.ok_count == 4 and next(c for c in diag.commands if c["command"] == "vmstat 1 3")["error"] == "rc=1"
    assert "hunter2" not in diag.outputs["ps aux --sort=-%mem"] and "password=" in diag.outputs["ps aux --sort=-%mem"]
    assert diag.fingerprint == {"top_rss_pid": 4242, "oom_flag": True, "swap_active": True, "sat_bucket": "critical"}
    assert diag.summary_lines[0].startswith("L3 진단(memory)") and any(ln.startswith("OOM 흔적 있음") for ln in diag.summary_lines)
    assert any("미수집: vmstat 1 3" in ln for ln in diag.summary_lines)


def test_collect_partial_failure_keeps_other_commands():
    run = _runner({"free -m": (0, FREE)}, fail={"dmesg", "cat /proc/meminfo"})
    diag = asyncio.run(collect("h1", "memory", run))
    errs = {c["command"]: c["error"] for c in diag.commands}
    assert errs["dmesg"] == "OSError" and errs["cat /proc/meminfo"] == "OSError" and errs["free -m"] is None
    assert diag.fingerprint["swap_active"] is True and diag.fingerprint["oom_flag"] is False


def test_collect_timeout_is_per_command():
    async def slow(host, command):
        await asyncio.sleep(0.2)
        return 0, ""

    diag = asyncio.run(collect("h1", "log", slow, timeout_seconds=0.01))
    assert diag.commands[0]["error"].startswith("timeout") and diag.ok_count == 0


# ── ② 상태지문 대조(escalate-only) ─────────────────────────────────────

def test_derive_fingerprint_disk_and_cpu_buckets():
    assert derive_fingerprint("disk", {"df -h": "/dev/sda1 100G 95G 5G 95% /"})["sat_bucket"] == "high"
    assert derive_fingerprint("disk", {"df -h": "/dev/sda1 100G 99G 1G 99% /"})["sat_bucket"] == "critical"
    assert derive_fingerprint("cpu", {"top -b -n1": "%Cpu(s): 92.0 us, 3.0 sy, 0.0 ni, 5.0 id"})["sat_bucket"] == "high"
    assert derive_fingerprint("network", {})["sat_bucket"] == "unknown"


@pytest.mark.parametrize("prev,cur,expected", [
    (None, {"sat_bucket": "high"}, "first"),
    ({"sat_bucket": "high", "oom_flag": False}, {"sat_bucket": "high", "oom_flag": True}, "worse"),
    ({"sat_bucket": "mid"}, {"sat_bucket": "critical"}, "worse"),
    ({"sat_bucket": "critical"}, {"sat_bucket": "mid"}, "improved"),
    ({"sat_bucket": "high", "swap_active": True}, {"sat_bucket": "high", "swap_active": True}, "same"),
    ({"sat_bucket": "unknown"}, {"sat_bucket": "unknown"}, "same"),
])
def test_compare_fingerprints_escalate_only(prev, cur, expected):
    assert compare_fingerprints(prev, cur) == expected
