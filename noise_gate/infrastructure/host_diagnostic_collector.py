"""허용목록 명령 실행 채널 어댑터 — post-gate L3 보강 (plans/91 1-6 · Plan 60 §18 E8 (나)안 · D-189 경로 B).

게이트가 PAGE를 결정한 **뒤**에만(비차단 · fire-and-forget) 대상 호스트에서 kind별 read-only 진단 명령을 실행해
결정적 요지·상태지문을 만든다. 게이트 판정 경로(<10s 예산)에는 개입하지 않는다 — 동기 경계 probe는 폐기됐다(U-H).

원칙(Plan 60 §18.5 · docs/25):
- **구조적 허용목록**: 프로파일에 적힌 명령 문자열만 실행한다(자유 문자열 실행 경로 없음). 변경 명령(kill·renice·sysctl -w·
  systemctl restart·dmesg -C·journalctl --vacuum)은 수집기에 **부재**하며, 방어적으로 deny 마커로도 차단한다.
- **부하 가드**: 모든 명령에 `timeout 20 nice -n 10` 접두(docs/25 · sre_agent `LOAD_GUARD_PREFIX`와 같은 값 — 패키지 경계상
  import하지 않고 §18.2 설계 정본에서 재정의한다).
- **마스킹·절단**: 출력은 `process_rank.mask_args`로 비밀값을 가리고 길이를 제한한다.
- **부분 반환**: 명령별 개별 try — 한 명령 실패가 나머지 수집을 막지 않는다. 실패는 결과에 남는다(침묵 폴백 금지).

계층: infrastructure(domain만 import). 전송은 `Runner` 콜러블 주입 — 기본 구현은 ssh(BatchMode) 서브프로세스.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from noise_gate.domain.process_rank import mask_args

logger = logging.getLogger(__name__)

#: (host, full_command) → (returncode, output). 예외는 호출부가 명령 단위로 삼킨다.
Runner = Callable[[str, str], Awaitable[tuple[int, str]]]

LOAD_GUARD_TIMEOUT_SECONDS = 20
LOAD_GUARD_NICE = 10
LOAD_GUARD_PREFIX = f"timeout {LOAD_GUARD_TIMEOUT_SECONDS} nice -n {LOAD_GUARD_NICE} "

#: kind별 L3 프로파일 — Plan 60 §18.2 허용목록에서 뽑은 read-only 명령(정본은 §18.2). 여기 없는 명령은 실행되지 않는다.
L3_PROFILES: dict[str, tuple[str, ...]] = {
    "cpu": ("uptime", "cat /proc/loadavg", "top -b -n1", "mpstat -P ALL 1 1", "ps aux --sort=-%cpu"),
    "memory": ("free -m", "cat /proc/meminfo", "vmstat 1 3", "ps aux --sort=-%mem", "dmesg"),
    "disk": ("df -h", "df -i", "iostat -xz 1 2", "dmesg"),
    "network": ("ss -s", "sar -n DEV 1 1", "dmesg"),
    "process": ("ps aux --sort=-%cpu", "journalctl -p err --since -1h", "dmesg"),
    "log": ("journalctl -p err --since -1h",),
}

#: 변경·파괴 명령의 방어적 차단 마커(수집기 프로파일에는 애초에 없다 — 이중 방어).
_DENY_MARKERS: tuple[str, ...] = (
    "kill", "renice", "sysctl -w", "systemctl restart", "systemctl stop", "systemctl start", "reboot", "shutdown",
    "dmesg -C", "dmesg -c", "dmesg --clear", "--vacuum", "--rotate", "--flush", "rm ", ">", "|", ";", "&&", "$(", "`",
)

_ALLOWED: frozenset[str] = frozenset(c for cmds in L3_PROFILES.values() for c in cmds)
_OUTPUT_MAX_CHARS = 4000
_SAT_RANK = {"low": 0, "mid": 1, "high": 2, "critical": 3}


def is_allowed(command: str) -> bool:
    """프로파일 명령 문자열과 **정확히** 일치하고 deny 마커가 없을 때만 True(구조적 허용목록)."""
    cmd = (command or "").strip()
    if not cmd or cmd not in _ALLOWED:
        return False
    return not any(m in cmd for m in _DENY_MARKERS)


def guarded(command: str) -> str:
    """부하 가드 접두를 붙인다(이미 붙어 있으면 그대로)."""
    return command if command.startswith(LOAD_GUARD_PREFIX) else LOAD_GUARD_PREFIX + command


def resolve_profile(kind: Optional[str], profile_map_csv: str = "") -> tuple[str, tuple[str, ...]]:
    """kind → 프로파일 이름·명령 목록. csv 오버라이드("memory=memory_deep")는 **존재하는** 프로파일명만 받는다."""
    if not kind:
        return "", ()
    name = kind
    for item in (profile_map_csv or "").split(","):
        if "=" in item:
            k, v = (x.strip() for x in item.split("=", 1))
            if k == kind and v in L3_PROFILES:
                name = v
    return name, L3_PROFILES.get(name, ())


def _mask_output(text: str) -> str:
    lines = [mask_args(ln, max_len=400) for ln in (text or "").splitlines()]
    out = "\n".join(lines)
    return out[:_OUTPUT_MAX_CHARS] + ("…" if len(out) > _OUTPUT_MAX_CHARS else "")


@dataclass
class HostDiagnostic:
    """한 호스트의 L3 수집 결과 — 명령별 상태 · 마스킹된 출력 · 상태지문 · 결정적 요지."""

    host: str
    kind: str
    profile: str
    commands: list[dict] = field(default_factory=list)       # {command, rc, ok, error}
    outputs: dict[str, str] = field(default_factory=dict)    # command → masked output
    fingerprint: dict = field(default_factory=dict)          # {top_rss_pid, oom_flag, swap_active, sat_bucket}
    summary_lines: list[str] = field(default_factory=list)
    collected_at: str = ""

    @property
    def ok_count(self) -> int:
        return sum(1 for c in self.commands if c.get("ok"))

    def to_dict(self) -> dict:
        return {
            "host": self.host, "kind": self.kind, "profile": self.profile, "commands": list(self.commands),
            "fingerprint": dict(self.fingerprint), "summary_lines": list(self.summary_lines),
            "collected_at": self.collected_at, "ok_count": self.ok_count,
        }


# ── 상태지문(§18.4 ②): {top_rss_pid, oom_flag, swap_active, sat_bucket} — 전부 결정적 파싱 ──

_OOM_RE = re.compile(r"out of memory|oom-kill|oom_reaper|killed process", re.I)
_PCT_RE = re.compile(r"(\d{1,3})%")


def _bucket_from_free_ratio(avail: float, total: float) -> str:
    if total <= 0:
        return "unknown"
    r = avail / total
    return "low" if r > 0.30 else "mid" if r > 0.15 else "high" if r > 0.05 else "critical"


def _bucket_from_use_pct(pct: float) -> str:
    return "low" if pct < 80 else "mid" if pct < 90 else "high" if pct < 97 else "critical"


def derive_fingerprint(kind: str, outputs: dict[str, str]) -> dict:
    """마스킹된 출력에서 상태지문을 뽑는다. 못 뽑는 값은 None/"unknown"(추정하지 않는다)."""
    fp: dict[str, Any] = {"top_rss_pid": None, "oom_flag": False, "swap_active": False, "sat_bucket": "unknown"}
    ps = outputs.get("ps aux --sort=-%mem") or ""
    for ln in ps.splitlines()[1:2]:
        cols = ln.split()
        if len(cols) > 1 and cols[1].isdigit():
            fp["top_rss_pid"] = int(cols[1])
    for key in ("dmesg", "journalctl -p err --since -1h"):
        if _OOM_RE.search(outputs.get(key) or ""):
            fp["oom_flag"] = True
    free = outputs.get("free -m") or ""
    for ln in free.splitlines():
        if ln.lower().startswith("swap:"):
            cols = ln.split()
            if len(cols) >= 3 and cols[2].isdigit() and int(cols[2]) > 0:
                fp["swap_active"] = True
    meminfo = outputs.get("cat /proc/meminfo") or ""
    if kind == "memory" and meminfo:
        vals: dict[str, float] = {}
        for ln in meminfo.splitlines():
            parts = ln.replace(":", "").split()
            if len(parts) >= 2 and parts[0] in ("MemTotal", "MemAvailable") and parts[1].isdigit():
                vals[parts[0]] = float(parts[1])
        if "MemTotal" in vals and "MemAvailable" in vals:
            fp["sat_bucket"] = _bucket_from_free_ratio(vals["MemAvailable"], vals["MemTotal"])
    elif kind == "disk":
        pcts = [int(m) for m in _PCT_RE.findall(outputs.get("df -h") or "") if int(m) <= 100]
        if pcts:
            fp["sat_bucket"] = _bucket_from_use_pct(max(pcts))
    elif kind == "cpu":
        top = outputs.get("top -b -n1") or ""
        m = re.search(r"(\d+(?:\.\d+)?)\s*id", top)
        if m:
            fp["sat_bucket"] = _bucket_from_use_pct(100.0 - float(m.group(1)))
    return fp


def compare_fingerprints(prev: Optional[dict], cur: dict) -> str:
    """재발 대조(§18.4 ② escalate-only): "first" | "worse" | "improved" | "same"."""
    if not prev:
        return "first"
    if (not prev.get("oom_flag") and cur.get("oom_flag")) or (not prev.get("swap_active") and cur.get("swap_active")):
        return "worse"
    pr, cr = _SAT_RANK.get(str(prev.get("sat_bucket")), -1), _SAT_RANK.get(str(cur.get("sat_bucket")), -1)
    if cr > pr and cr >= 0 and pr >= 0:
        return "worse"
    if cr < pr and cr >= 0 and pr >= 0:
        return "improved"
    return "same"


def summary_lines(kind: str, fp: dict, diag_commands: list[dict]) -> list[str]:
    """결정적 요지 — 수치·플래그만 서술하고 원인 추정은 하지 않는다(D-035 · 판단은 코드, 서술은 조사 LLM)."""
    lines = [f"L3 진단({kind}) — 허용목록 명령 {sum(1 for c in diag_commands if c.get('ok'))}/{len(diag_commands)}건 성공"]
    if fp.get("sat_bucket") not in (None, "unknown"):
        lines.append(f"포화 구간: {fp['sat_bucket']}")
    if fp.get("oom_flag"):
        lines.append("OOM 흔적 있음(dmesg/journal)")
    if fp.get("swap_active"):
        lines.append("swap 사용 중")
    if fp.get("top_rss_pid") is not None:
        lines.append(f"최대 RSS PID {fp['top_rss_pid']}")
    failed = [c["command"] for c in diag_commands if not c.get("ok")]
    if failed:
        lines.append("미수집: " + ", ".join(failed))
    return lines


async def collect(
    host: str, kind: str, runner: Runner, *, profile_map_csv: str = "", timeout_seconds: float = 20.0,
) -> HostDiagnostic:
    """kind 프로파일의 명령을 하나씩(개별 try) 실행해 진단 결과를 만든다. 허용목록 밖 명령은 실행되지 않는다."""
    profile, commands = resolve_profile(kind, profile_map_csv)
    diag = HostDiagnostic(host=host, kind=kind or "", profile=profile,
                          collected_at=datetime.now(timezone.utc).isoformat())
    for cmd in commands:
        entry: dict[str, Any] = {"command": cmd, "rc": None, "ok": False, "error": None}
        if not is_allowed(cmd):
            entry["error"] = "허용목록 밖 명령(차단)"
            diag.commands.append(entry)
            continue
        try:
            rc, out = await asyncio.wait_for(runner(host, guarded(cmd)), timeout=timeout_seconds)
            entry["rc"] = rc
            entry["ok"] = rc == 0
            diag.outputs[cmd] = _mask_output(out)
            if rc != 0:
                entry["error"] = f"rc={rc}"
        except asyncio.TimeoutError:
            entry["error"] = f"timeout({timeout_seconds:.0f}s)"
        except Exception as exc:  # noqa: BLE001 — 명령 단위 부분 실패(나머지 계속)
            entry["error"] = type(exc).__name__
        diag.commands.append(entry)
    diag.fingerprint = derive_fingerprint(diag.kind, diag.outputs)
    diag.summary_lines = summary_lines(diag.kind, diag.fingerprint, diag.commands)
    return diag


def build_ssh_runner(user: str = "", connect_timeout: int = 5) -> Runner:
    """기본 실행 채널(allowlist_exec): ssh BatchMode 서브프로세스. 명령은 이미 가드·허용목록을 거친 문자열만 온다."""

    async def _run(host: str, command: str) -> tuple[int, str]:
        target = f"{user}@{host}" if user else host
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={int(connect_timeout)}", "-o", "StrictHostKeyChecking=accept-new",
            target, "--", *shlex.split(command),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        return int(proc.returncode or 0), out.decode("utf-8", errors="replace")

    return _run


__all__ = [
    "L3_PROFILES", "LOAD_GUARD_PREFIX", "HostDiagnostic", "Runner", "is_allowed", "guarded", "resolve_profile",
    "derive_fingerprint", "compare_fingerprints", "summary_lines", "collect", "build_ssh_runner",
]
