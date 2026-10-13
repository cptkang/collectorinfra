"""벤치 러너 — 프로세스 기동 → 세계별 k회 submit/poll → raw.jsonl (plans/151 §4.7 · §4.8 · §4.9).

한 번의 `--run`:
1. 명세 검사·과금 평면 차단·포트 확인(`preflight.static_checks`) — `--dry-run`은 여기서 계획만 출력.
2. `<run>/` 생성 → (가짜 LLM) → `/v1/models`·tool-calling 왕복(`preflight.live_checks`) → 세계 서버
   → 실험군마다 sre_agent 1개(`_serve.py` · cwd = 빈 `<run>/arm-<name>/` · 환경변수만으로 설정).
   세계 서버는 sre_agent보다 먼저 뜬다(holmes가 첫 조사에서 MCP를 1회 등록 — W0 실측).
3. 세계마다 실험군 순서를 회전(interleave)하며 k회: `/_bench/active` → `sre_investigate_alarm`
   (`wait_seconds=0`) → `sre_get_investigation` 폴링 → 구간 닫기 → 감사·호출 로그로 raw 1행.
4. 자기가 띄운 PID만 내린다(`pids.json`) → manifest → `judge.rejudge`(채점+보고서 · W3a).

무효표(F-INFRA · `infra_error`): `rejected`·`stub`·호출 예외·폴링 시간 초과. 설정 에코가 없는
실험군은 돌리지 않고 `settings_echo_missing`으로 표기한다(판정 제외).

경계(D-139): sre_agent 패키지를 import하지 않는다 — MCP(SSE)로만 부른다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

import httpx
import yaml

from investigation_bench import preflight, schema
from investigation_bench.world_server import HOST_ARG_KEYS, TARGET_LIST_KEYS
from investigation_bench.worlds import World, load_worlds

TOP = Path(__file__).resolve().parents[2]  # sre_agent/
SCRIPTS_DIR = Path(__file__).resolve().parents[1]  # sre_agent/scripts
REPO_ROOT = TOP.parent
DEFAULT_OUT_ROOT = TOP / ".data" / "investigation_bench"
DEFAULT_WORLDS_DIR = "testdata/investigation_bench/worlds"
DEFAULT_SNAPSHOT = "testdata/investigation_bench/tool_schemas.json"
DEFAULT_SPLITS: tuple[str, ...] = ("dev", "holdout")
# 벤치 포트(§4.7 · §6.7) — 0이면 기동 때 빈 포트를 고른다(테스트 병렬 안전).
DEFAULT_PORTS: dict[str, int] = {"fake_llm": 19180, "world": 19190, "world_apm": 19191,
                                 "arm_base": 19200}

TERMINAL_STATUSES = frozenset({"done", "failed", "timeout", "stub", "rejected", "not_found"})
INFRA_TERMINAL = frozenset({"stub", "rejected", "not_found"})
TERMINAL_AUDIT_EVENTS = frozenset({"done", "timeout", "failed", "rejected", "stub", "terminal"})
PREFETCH_EVENTS = frozenset({"prefetch", "prefetch_failed"})
INSTRUMENTATION_KEYS: tuple[str, ...] = (
    "incomplete", "elapsed_s", "prefetch_s", "steps", "tool_calls_n", "model", "max_steps",
    "tool_calls",
)
ECHO_MISSING = "settings_echo_missing"
# sre_agent 조사 시간 초과 상태 · 폴링 초과 무효표 접두 — 이 둘로 끝나면 버려진 holmes 스레드가
# 남는다.
TIMEOUT_STATUS = "timeout"
POLL_TIMEOUT_PREFIX = "poll_timeout"
PROCESS_MARKER = "investigation_bench"

# 실험군 프로세스가 부모에게서 물려받는 키 — 이것뿐이다(`.env`·`.encenv`·Gemini 키가 섞이지 않게).
INHERITED_ENV_KEYS: tuple[str, ...] = (
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TZ", "SYSTEMROOT",
)
# 고정 키
# - `LITELLM_LOCAL_MODEL_COST_MAP` — litellm 비용표를 원격(GitHub)에서 받지 않고 내장본을 쓴다.
#   폐쇄망 운영은 원격 실패 → 내장본이므로 같은 조건이고, 외부망 실행도 바깥 호출 0 · 결정적이다.
# - `LITELLM_MODE=PRODUCTION` — litellm은 기본(DEV)이면 import 때 `load_dotenv()`로 **설치 위치에서
#   위로** `.env`를 찾아 os.environ에 싣는다(빈 cwd로는 못 막는다 — verify 2026-10-13).
FIXED_ENV: dict[str, str] = {"LITELLM_LOCAL_MODEL_COST_MAP": "True", "LITELLM_MODE": "PRODUCTION",
                             "PYTHONUNBUFFERED": "1"}
# 명세 `base_env`·`arms`가 덮을 수 없는 키(러너가 정한다) · 아예 넣지 않는 키(§4.7 표).
PROTECTED_ENV_KEYS = frozenset({
    "MODEL", "API_BASE", "API_KEY", "INVESTIGATION_LLM_ENABLED", "GEMINI_API_KEY",
    "LLM_GEMINI_API_KEY", "POLESTAR_MCP_URL", "APM_MCP_URL", "INVESTIGATION_MAX_CONCURRENT",
    "INVESTIGATION_AUDIT_PATH", "PYTHONPATH", *FIXED_ENV,
})
NEVER_SET_ENV_KEYS = frozenset({"INVESTIGATION_DEDUP_TTL_SECONDS", "INVESTIGATION_HOURLY_BUDGET"})
# 러너가 env로 정하는 설정 필드 — 에코에서 기본값과 달라도 된다(나머지는 명세 키만 허용).
RUNNER_SET_FIELDS = frozenset({
    "model", "api_base", "api_key", "investigation_llm_enabled", "gemini_api_key",
    "polestar_mcp_url", "apm_mcp_url", "investigation_max_concurrent", "investigation_audit_path",
})
REDACTED_SET, REDACTED_UNSET = "<set>", "<unset>"
SECRET_MARKERS: tuple[str, ...] = ("KEY", "TOKEN", "SECRET", "PASSWORD")

ToolCaller = Callable[[str, str, dict[str, Any]], dict[str, Any]]


class RunnerError(Exception):
    """러너 중단 — `exit_code`(2 = 명세·과금 거부 · 1 = 그 밖의 실패)."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


# ─────────────────────────────── 공용 ───────────────────────────────


def now_iso() -> str:
    """현재 UTC 시각(ISO · 밀리초)."""
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _epoch(ts: Any) -> float | None:
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        return datetime.fromisoformat(str(ts)).timestamp()
    except ValueError:
        return None


def resolve_path(value: str | Path) -> Path:
    """명세의 경로 — 절대 경로는 그대로, 상대 경로는 `sre_agent/` 기준."""
    p = Path(value).expanduser()
    return p if p.is_absolute() else TOP / p


def _rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(TOP))
    except ValueError:
        return str(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSONL 파일 → 행 목록(없으면 빈 목록 · 깨진 줄은 건너뜀)."""
    if not Path(path).exists():
        return []
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    """JSONL 한 줄 덧붙이기."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def write_json(path: Path, obj: Any) -> None:
    """JSON 파일 쓰기(임시 파일 → 교체)."""
    tmp = Path(path).with_suffix(Path(path).suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def git_commit() -> tuple[str | None, bool | None]:
    """저장소 HEAD 커밋 · 작업 트리 변경 여부(git이 없으면 None)."""
    try:
        head = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
                               capture_output=True, text=True, timeout=30, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None, None
    return head or None, bool(dirty.strip())


# ─────────────────────────────── 명세 ───────────────────────────────


def load_spec(path: Path) -> tuple[dict[str, Any], str]:
    """실행 명세 YAML → (dict, 원문)."""
    text = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise RunnerError(f"명세 최상위가 매핑이 아니다: {path}", 2)
    for key in ("run", "llm", "arms"):
        if not isinstance(data.get(key), dict):
            raise RunnerError(f"명세에 {key}: 매핑이 없다: {path}", 2)
    if not data["arms"]:
        raise RunnerError("명세 arms가 비었다 — 대조군 하나는 있어야 한다", 2)
    order = data["run"].get("order", "interleave")
    if order != "interleave":
        raise RunnerError(f"run.order는 interleave만 지원한다: {order!r}", 2)
    return data, text


def spec_hash(text: str) -> str:
    """명세 원문 해시(sha256)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def worlds_dir_of(spec: Mapping[str, Any]) -> Path:
    """명세의 세계 루트(`run.worlds_dir` · 기본 `testdata/investigation_bench/worlds`)."""
    return resolve_path((spec.get("run") or {}).get("worlds_dir") or DEFAULT_WORLDS_DIR)


def snapshot_path_of(spec: Mapping[str, Any]) -> Path:
    """명세의 도구 스키마 스냅샷 경로(`run.snapshot` · 기본 실 스냅샷)."""
    return resolve_path((spec.get("run") or {}).get("snapshot") or DEFAULT_SNAPSHOT)


def select_worlds(spec: Mapping[str, Any]) -> dict[str, World]:
    """명세의 `splits`·`worlds`로 세계를 고른다(id 순). 없거나 모르는 id면 RunnerError."""
    run = spec.get("run") or {}
    root = worlds_dir_of(spec)
    if not root.is_dir():
        raise RunnerError(f"세계 디렉터리가 없다: {root}")
    loaded = load_worlds([root])
    splits = set(run.get("splits") or DEFAULT_SPLITS)
    pool = {wid: w for wid, w in sorted(loaded.items()) if w.split in splits}
    wanted = run.get("worlds", "all")
    if wanted != "all":
        missing = [w for w in wanted if w not in pool]
        if missing:
            raise RunnerError(f"명세 worlds에 없는 세계: {missing} "
                              f"(루트 {root} · splits {sorted(splits)})")
        pool = {w: pool[w] for w in wanted}
    if not pool:
        raise RunnerError(f"고른 세계가 없다(루트 {root} · splits {sorted(splits)})")
    return pool


def needs_apm(worlds: Mapping[str, World], snapshot: schema.Snapshot) -> bool:
    """고른 세계가 APM 도구(`tools:` 규칙·대본 호출)를 쓰면 True — APM 엔드포인트를 띄운다."""
    apm = {t["name"] for t in snapshot.get("apm") or []}
    for w in worlds.values():
        if apm & set(w.tools):
            return True
        script = w.data.get("script") or {}
        lines = list(script.get("model") or [])
        for steps in (script.get("neg") or {}).values():
            lines += list(steps or [])
        if any(isinstance(s, Mapping) and s.get("call") in apm for s in lines):
            return True
    return False


# ─────────────────────────────── 실험군 환경 ───────────────────────────────


def _env_str(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def build_arm_env(
    spec: Mapping[str, Any], arm: str, *, run_dir: Path, polestar_url: str,
    apm_url: str | None, api_base: str, parent_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """실험군 하나의 sre_agent 환경변수(§4.7 표) — 이것이 그 프로세스의 환경 전부다.

    부모 환경에서는 `INHERITED_ENV_KEYS`만 물려받는다. 명세 `base_env`·`arms.<arm>`이 러너 고정 키나
    `INVESTIGATION_DEDUP_TTL_SECONDS`·`INVESTIGATION_HOURLY_BUDGET`을 쓰면 RunnerError.
    """
    parent = os.environ if parent_env is None else parent_env
    llm = spec.get("llm") or {}
    layers = [("base_env", spec.get("base_env") or {}),
              (f"arms.{arm}", (spec.get("arms") or {})[arm] or {})]
    for where, values in layers:
        bad = sorted(set(map(str, values)) & (PROTECTED_ENV_KEYS | NEVER_SET_ENV_KEYS))
        if bad:
            raise RunnerError(f"명세 {where}가 러너 고정·금지 키를 쓴다: {bad}", 2)
    env = {k: parent[k] for k in INHERITED_ENV_KEYS if k in parent}
    for _, values in layers:
        env.update({str(k): _env_str(v) for k, v in values.items()})
    env.update(FIXED_ENV)
    env.update({
        "PYTHONPATH": str(SCRIPTS_DIR),
        "MODEL": str(llm.get("model") or ""),
        "API_BASE": api_base,
        "API_KEY": str(llm.get("api_key") or "dummy"),
        "INVESTIGATION_LLM_ENABLED": "true",
        "GEMINI_API_KEY": "",
        "LLM_GEMINI_API_KEY": "",
        "POLESTAR_MCP_URL": polestar_url,
        "APM_MCP_URL": apm_url or "",
        "INVESTIGATION_MAX_CONCURRENT": "1",
        "INVESTIGATION_AUDIT_PATH": str(Path(run_dir) / f"arm-{arm}" / "audit.jsonl"),
    })
    return env


def _is_secret_name(name: str) -> bool:
    return any(m in name.upper() for m in SECRET_MARKERS)


def redact_spec(obj: Any) -> Any:
    """manifest용 명세 사본 — 이름에 KEY·TOKEN·SECRET·PASSWORD가 든 칸의 값을
    `<set>`/`<unset>`로 바꾼다."""
    if isinstance(obj, Mapping):
        return {k: ((REDACTED_SET if v not in (None, "") else REDACTED_UNSET)
                    if _is_secret_name(str(k)) and not isinstance(v, (Mapping, list))
                    else redact_spec(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_spec(v) for v in obj]
    return obj


_SECRET_YAML_RE = re.compile(
    r"(?P<head>(?<![\w-])[\w-]*(?:key|token|secret|password)[\w-]*\s*:[ \t]?)"
    r"(?P<value>[^,}\n#]*?)(?P<tail>[ \t]*(?:[,}#\n]|$))", re.IGNORECASE)


def _secret_values(obj: Any) -> set[str]:
    if isinstance(obj, Mapping):
        out: set[str] = set()
        for k, v in obj.items():
            if _is_secret_name(str(k)) and isinstance(v, (str, int, float)) and str(v):
                out.add(str(v))
            else:
                out |= _secret_values(v)
        return out
    if isinstance(obj, list):
        return set().union(*(_secret_values(v) for v in obj)) if obj else set()
    return set()


def redact_spec_text(text: str, spec: Any = None) -> str:
    """명세 원문의 비밀 칸 값을 `<set>`/`<unset>`로(블록·흐름 표기 모두 — 주석은 남긴다).

    `spec`(파싱본)을 주면 그 비밀 값을 원문에서 먼저 지운다(쉼표 든 값 등 정규식이 못 자르는 꼴).
    """
    def sub(m: re.Match[str]) -> str:
        value = m.group("value").strip().strip("'\"")
        return m.group("head") + (REDACTED_SET if value else REDACTED_UNSET) + m.group("tail")
    for value in sorted(_secret_values(spec), key=len, reverse=True):
        text = text.replace(value, REDACTED_SET)
    return _SECRET_YAML_RE.sub(sub, text)


def mask_env(env: Mapping[str, str]) -> dict[str, str]:
    """출력용 — 비밀 키(KEY·TOKEN·SECRET·PASSWORD)의 값은 가린다(빈 값은 공란 그대로)."""
    out = {}
    for k, v in sorted(env.items()):
        secret = _is_secret_name(k)
        out[k] = ("***" if v else "") if secret else v
    return out


# ─────────────────────────────── 실행 계획 ───────────────────────────────


def plan_runs(world_ids: Sequence[str], arms: Sequence[str], k: int) -> list[dict[str, Any]]:
    """실행 순서 — 반복(trial) 바깥 · 세계 안쪽.

    세계마다 실험군 순서를 한 칸씩 회전한다(interleave).
    """
    out = []
    n = len(arms)
    for trial in range(1, k + 1):
        for i, wid in enumerate(world_ids):
            off = (i + trial - 1) % n
            for arm in list(arms[off:]) + list(arms[:off]):
                out.append({"world_id": wid, "trial": trial, "arm": arm})
    return out


def run_key(row: Mapping[str, Any]) -> tuple[str, str, int]:
    """실행 단위 키 `(arm, world_id, trial)`."""
    return str(row["arm"]), str(row["world_id"]), int(row["trial"])


def pending_runs(plan: Iterable[Mapping[str, Any]],
                 raw_rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """raw에 이미 있는 `arm × world × trial`을 뺀 나머지(`--resume`)."""
    done = {run_key(r) for r in raw_rows}
    return [dict(p) for p in plan if run_key(p) not in done]


def fingerprint(run_id: str, arm: str, world_id: str, trial: int) -> str:
    """실행마다 고유한 지문 — dedup·예산 가드에 막히지 않게(§4.1)."""
    return f"{run_id}:{arm}:{world_id}:{trial}"


def build_payload(world: World, fp: str) -> dict[str, Any]:
    """`sre_investigate_alarm` 페이로드(계약 "1")."""
    return {
        "contract_version": "1",
        "event": world.event,
        "decision": {"fingerprint": fp},
        "meta": dict(world.data.get("meta") or {}),
    }


def free_port() -> int:
    """127.0.0.1의 빈 포트 하나."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def plan_ports(spec: Mapping[str, Any], arms: Sequence[str], *, use_fake: bool,
               use_apm: bool) -> dict[str, Any]:
    """쓸 포트 계획(0 = 기동 때 빈 포트). `run.ports`가 기본값을 덮는다."""
    cfg = {**DEFAULT_PORTS, **((spec.get("run") or {}).get("ports") or {})}
    base = int(cfg["arm_base"])
    return {
        "fake_llm": int(cfg["fake_llm"]) if use_fake else None,
        "world": int(cfg["world"]),
        "world_apm": int(cfg["world_apm"]) if use_apm else None,
        "arms": {arm: (base + i if base else 0) for i, arm in enumerate(arms)},
    }


def planned_port_list(ports: Mapping[str, Any]) -> list[int]:
    """계획의 포트 전부(None 제외 · 0 포함)."""
    out = [ports[k] for k in ("fake_llm", "world", "world_apm") if ports.get(k) is not None]
    return out + list(ports["arms"].values())


def materialize_ports(ports: Mapping[str, Any]) -> dict[str, Any]:
    """0인 포트를 실제 빈 포트로 바꾼다."""
    def pick(p: int | None) -> int | None:
        return None if p is None else (p or free_port())
    return {
        "fake_llm": pick(ports.get("fake_llm")),
        "world": pick(ports["world"]),
        "world_apm": pick(ports.get("world_apm")),
        "arms": {a: pick(p) for a, p in ports["arms"].items()},
    }


# ─────────────────────────────── 프로세스 ───────────────────────────────


@dataclass
class _Proc:
    name: str
    popen: subprocess.Popen[bytes]
    port: int | None
    started_at: str
    cmd: list[str]
    log: IO[bytes]


@dataclass
class ProcessGroup:
    """벤치가 띄운 프로세스 묶음 — `pids.json`에 PID·포트·시작 시각을 적고, 끝나면 이것만 내린다."""

    pids_path: Path
    procs: list[_Proc] = field(default_factory=list)

    def launch(self, name: str, cmd: list[str], *, cwd: Path, env: Mapping[str, str],
               port: int | None, log_path: Path) -> subprocess.Popen[bytes]:
        """프로세스 하나를 띄우고 `pids.json`을 갱신한다(출력은 `log_path`에 덧붙임)."""
        log = Path(log_path).open("ab")
        popen = subprocess.Popen(cmd, cwd=str(cwd), env=dict(env), stdout=log,
                                 stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 start_new_session=True)
        self.procs.append(_Proc(name, popen, port, now_iso(), cmd, log))
        self._write()
        return popen

    def _write(self) -> None:
        write_json(self.pids_path, {"processes": [
            {"name": p.name, "pid": p.popen.pid, "port": p.port, "started_at": p.started_at,
             "cmd": p.cmd, "returncode": p.popen.poll()} for p in self.procs]})

    @staticmethod
    def _signal(popen: subprocess.Popen[bytes], sig: int) -> None:
        """프로세스 그룹째 신호.

        `start_new_session`으로 띄워 그룹 id = 자기 PID일 때만 그룹에 보낸다.
        """
        try:
            if os.getpgid(popen.pid) == popen.pid:
                os.killpg(popen.pid, sig)
            else:
                popen.send_signal(sig)
        except ProcessLookupError:
            pass

    def stop(self, name: str, grace: float = 10.0) -> None:
        """이름이 `name`인 자기 프로세스만 종료하고 `pids.json`에서 뺀다(실험군 재기동용)."""
        keep: list[_Proc] = []
        for p in self.procs:
            if p.name != name:
                keep.append(p)
                continue
            if p.popen.poll() is None:
                self._signal(p.popen, signal.SIGTERM)
                try:
                    p.popen.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    self._signal(p.popen, signal.SIGKILL)
                    p.popen.wait(timeout=5)
            p.log.close()
        self.procs = keep
        self._write()

    def stop_all(self, grace: float = 10.0) -> None:
        """자기가 띄운 프로세스만 종료(SIGTERM → 유예 → SIGKILL)."""
        for p in reversed(self.procs):
            if p.popen.poll() is None:
                self._signal(p.popen, signal.SIGTERM)
        deadline = time.monotonic() + grace
        for p in reversed(self.procs):
            try:
                p.popen.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                self._signal(p.popen, signal.SIGKILL)
                p.popen.wait(timeout=5)
            p.log.close()
        if self.procs:
            self._write()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _cmdline(pid: int) -> str:
    try:
        return subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True,
                              text=True, timeout=5, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def cleanup(run_dir: Path, grace: float = 5.0) -> list[int]:
    """`<run>/pids.json`에 적힌 자기 프로세스만 종료한다 → 종료한 PID 목록.

    PID 재사용으로 남의 프로세스를 죽이지 않도록 명령줄에 `investigation_bench`가 있는 것만 내린다.
    """
    path = Path(run_dir) / "pids.json"
    if not path.exists():
        raise RunnerError(f"pids.json 없음: {path}")
    entries = json.loads(path.read_text(encoding="utf-8")).get("processes") or []
    targets = [int(e["pid"]) for e in entries
               if _alive(int(e["pid"])) and PROCESS_MARKER in _cmdline(int(e["pid"]))]
    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
    deadline = time.monotonic() + grace
    for pid in targets:
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        if _alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    return targets


def _http_client() -> httpx.Client:
    return httpx.Client(timeout=5.0, trust_env=False)


def wait_http(url: str, popen: subprocess.Popen[bytes] | None, timeout: float = 30.0) -> None:
    """GET이 200을 줄 때까지 기다린다(프로세스가 죽으면 즉시 실패)."""
    deadline = time.monotonic() + timeout
    with _http_client() as c:
        while True:
            if popen is not None and popen.poll() is not None:
                raise RunnerError(f"프로세스가 기동 중 종료됐다({url} · rc={popen.returncode})")
            try:
                if c.get(url).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise RunnerError(f"기동 대기 시간 초과: {url}")
            time.sleep(0.2)


def post_active(url: str, body: Mapping[str, Any]) -> dict[str, Any]:
    """제어 경로 `/_bench/active` POST."""
    with _http_client() as c:
        resp = c.post(url, json=dict(body))
        resp.raise_for_status()
        out: dict[str, Any] = resp.json()
        return out


async def _mcp_call(url: str, name: str, args: dict[str, Any], timeout: float) -> dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    async with sse_client(url, timeout=timeout) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, args)
    text = "".join(getattr(c, "text", "") for c in result.content)
    if result.isError:
        raise RunnerError(f"{name} 도구 오류: {text[:300]}")
    out: dict[str, Any] = json.loads(text)
    return out


def mcp_call(url: str, name: str, args: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
    """sre_agent MCP 도구 1회 호출(호출마다 새 SSE 세션) → JSON dict."""
    return asyncio.run(asyncio.wait_for(_mcp_call(url, name, args, timeout), timeout))


def wait_arm(url: str, popen: subprocess.Popen[bytes] | None, call: ToolCaller = mcp_call,
             timeout: float = 90.0) -> dict[str, Any]:
    """sre_agent `sre_health`가 응답할 때까지 기다린다."""
    deadline = time.monotonic() + timeout
    last = ""
    while True:
        if popen is not None and popen.poll() is not None:
            raise RunnerError(f"sre_agent가 기동 중 종료됐다({url} · rc={popen.returncode})"
                              " — stderr.log 확인")
        try:
            return call(url, "sre_health", {})
        except Exception as e:  # 아직 안 떴다
            last = f"{type(e).__name__}: {e}"
        if time.monotonic() > deadline:
            raise RunnerError(f"sre_agent 기동 대기 시간 초과: {url} ({last[:200]})")
        time.sleep(0.3)


def read_echo(arm_dir: Path) -> dict[str, Any] | None:
    """실험군의 실효 설정 에코(없으면 None)."""
    path = Path(arm_dir) / "settings_echo.json"
    if not path.exists():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def spec_fields(spec: Mapping[str, Any], arm: str) -> frozenset[str]:
    """명세 `base_env`·`arms.<arm>`이 정한 설정 필드 이름(env 키 소문자)."""
    keys = {*(spec.get("base_env") or {}), *((spec.get("arms") or {}).get(arm) or {})}
    return frozenset(str(k).lower() for k in keys)


def verify_echo(echo: Mapping[str, Any], *, model: str, api_base: str,
                allowed_fields: Iterable[str] | None = None) -> list[str]:
    """에코가 사전 점검이 판정한 평면·명세와 같은지(D-324 ⑤ 재확인) → 불일치 목록.

    `allowed_fields`(명세 키)가 주어지면 기본값과 다른 필드(`non_default_fields` — `_serve.py`)가
    러너·명세가 정한 것뿐인지도 본다 — `.env` 등 바깥에서 들어온 기능 플래그를 잡는다. 금지 키
    (`investigation_hourly_budget`·`investigation_dedup_ttl_seconds`)는 미설정(None)이어야 한다.
    """
    problems = []
    for name in sorted(k.lower() for k in NEVER_SET_ENV_KEYS):
        if echo.get(name) is not None:
            problems.append(f"{name}={echo.get(name)!r} — 벤치는 설정하지 않는다(None이어야 함)")
    if allowed_fields is not None:
        changed = echo.get("non_default_fields")
        if not isinstance(changed, list):
            problems.append("에코에 non_default_fields가 없다")
        else:
            stray = sorted(set(changed) - RUNNER_SET_FIELDS - set(allowed_fields))
            if stray:
                problems.append(f"명세에 없는 설정이 기본값과 다르다: {stray}")
    if echo.get("model") != model:
        problems.append(f"model {echo.get('model')!r} ≠ 판정값 {model!r}")
    if echo.get("api_base") != api_base:
        problems.append(f"api_base {echo.get('api_base')!r} ≠ 판정값 {api_base!r}")
    if (echo.get("secret_fields_set") or {}).get("gemini_api_key"):
        problems.append("gemini_api_key가 비어 있지 않다")
    return problems


# ─────────────────────────────── 한 실행 ───────────────────────────────


def world_calls_offset(path: Path) -> int:
    """호출 로그 현재 끝(바이트)."""
    return Path(path).stat().st_size if Path(path).exists() else 0


def read_segment(path: Path, offset: int) -> list[dict[str, Any]]:
    """호출 로그의 `offset` 이후 행."""
    if not Path(path).exists():
        return []
    with Path(path).open("rb") as f:
        f.seek(offset)
        data = f.read().decode("utf-8")
    return [json.loads(line) for line in data.splitlines() if line.strip()]


def audit_events(audit_path: Path, investigation_id: str | None) -> list[dict[str, Any]]:
    """실험군 감사 파일에서 그 조사의 이벤트."""
    if not investigation_id:
        return []
    return [r for r in read_jsonl(audit_path) if r.get("investigation_id") == investigation_id]


def world_host_set(world: World) -> frozenset[str]:
    """세계 호스트(`hosts` + 알람 호스트 · 소문자) — 떠돌이 호출 판별용."""
    ev = world.event
    extra = {str(h).strip().casefold() for h in (ev.get("hostname"), ev.get("serverName")) if h}
    return world.hosts | extra


def call_hosts(args: Any) -> set[str]:
    """호출 인자의 호스트 값(`hostname`·`server_name`·`targets[]` 항목 · 소문자)."""
    if not isinstance(args, Mapping):
        return set()
    found = [args.get(k) for k in HOST_ARG_KEYS]
    for k in TARGET_LIST_KEYS:
        items = args.get(k)
        for item in items if isinstance(items, list) else []:
            if isinstance(item, Mapping):
                found += [item.get(h) for h in HOST_ARG_KEYS]
    return {str(h).strip().casefold() for h in found if h}


def is_stray(args: Any, current_hosts: frozenset[str], foreign_hosts: frozenset[str]) -> bool:
    """다른 세계 호스트만 가리키는 호출인가 — 앞 실행의 버려진 holmes 스레드가 부른 것.

    호스트 인자가 없거나 현재 세계 호스트를 하나라도 가리키면 판별 불가·정상으로 둔다.
    """
    hosts = call_hosts(args)
    return bool(hosts & foreign_hosts) and not hosts & current_hosts


def build_tool_trace(
    segment: Sequence[Mapping[str, Any]], audit: Sequence[Mapping[str, Any]],
    seen_health: set[str], health_tools: Iterable[str], *,
    current_hosts: frozenset[str] = frozenset(), foreign_hosts: frozenset[str] = frozenset(),
) -> tuple[list[dict[str, Any]], bool, list[dict[str, Any]]]:
    """구간의 호출 로그 → `tool_trace[{seq, phase, tool, args, matcher, arg_error, reply_hash}]`.

    - 프로세스당 1회인 holmes MCP 등록 헬스체크(`list_sources`·`gateway_health` · 인자 없음)는 그
      실험군에서 처음 보일 때 한 번 뺀다(`seen_health`가 실험군별로 기억 — W0 실측).
    - 감사 `prefetch`·`prefetch_failed` 시각 이전 호출 = `phase=prefetch`(§4.8). 사전수집 타임박스
      만료(`prefetch_failed`)면 그 뒤 호출도 사전수집 스레드일 수 있어 두 번째 반환값이 True다.
    - 호스트 인자가 다른 세계 호스트(`foreign_hosts`)만 가리키는 호출은 떠돌이(`stray: true`)로
      tool_trace에서 빼 세 번째 반환값에 모은다(방어 2선 — 1선은 시간 초과 뒤 실험군 재기동).

    Returns:
        (tool_trace, phase_ambiguous, stray_calls)
    """
    health = set(health_tools)
    cut = None
    ambiguous = False
    for ev in audit:
        if ev.get("event") in PREFETCH_EVENTS:
            cut = _epoch(ev.get("ts"))
            ambiguous = ev.get("event") == "prefetch_failed"
            break
    trace: list[dict[str, Any]] = []
    strays: list[dict[str, Any]] = []
    for r in segment:
        tool = str(r.get("tool"))
        if tool in health and tool not in seen_health and not r.get("args"):
            seen_health.add(tool)
            continue
        if is_stray(r.get("args"), current_hosts, foreign_hosts):
            strays.append({"ts": r.get("ts"), "tool": tool, "args": r.get("args"),
                           "arg_error": r.get("arg_error"), "stray": True})
            continue
        ts = _epoch(r.get("ts"))
        phase = "prefetch" if cut is not None and ts is not None and ts <= cut else "investigation"
        trace.append({
            "seq": len(trace) + 1, "phase": phase, "tool": tool, "args": r.get("args"),
            "matcher": r.get("matcher"), "arg_error": r.get("arg_error"),
            "reply_hash": r.get("reply_hash"),
        })
    return trace, ambiguous, strays


def empty_raw_row(key: Mapping[str, Any], world: World, fp: str) -> dict[str, Any]:
    """raw 1행의 뼈대(§4.9 필드 전부)."""
    return {
        "arm": key["arm"], "world_id": key["world_id"], "split": world.split,
        "trial": key["trial"], "investigation_id": None, "fingerprint": fp,
        "submitted_at": None, "finished_at": None, "elapsed_s": None, "status": None,
        "incomplete": None, "verdict": None, "tokens": None, "steps": None,
        "tool_calls_n": None, "briefing": None, "tool_trace": [], "audit": [],
        "infra_error": None, "stray_calls_n": 0,
    }


@dataclass
class RunContext:
    """한 실행에 필요한 배선 — 테스트는 `call`·`activate`·`sleep`·`clock`을 바꿔 끼운다."""

    run_id: str
    world_calls_path: Path
    poll_interval_s: float
    per_run_timeout_s: float
    call: ToolCaller = mcp_call
    activate: Callable[[Mapping[str, Any]], None] = lambda body: None
    health_tools: tuple[str, ...] = ("list_sources", "gateway_health")
    seen_health: dict[str, set[str]] = field(default_factory=dict)
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    audit_wait_s: float = 3.0
    # 세계 id → 호스트(`world_host_set`) — 떠돌이 호출 판별. 비면 판별하지 않는다.
    world_hosts: Mapping[str, frozenset[str]] = field(default_factory=dict)


def execute_one(ctx: RunContext, key: Mapping[str, Any], world: World, arm_url: str,
                audit_path: Path, script: str = "model") -> dict[str, Any]:
    """실행 1건(§4.8) → raw 1행. 인프라 실패는 `infra_error`로 남기고 예외를 올리지 않는다."""
    arm, wid, trial = str(key["arm"]), str(key["world_id"]), int(key["trial"])
    fp = fingerprint(ctx.run_id, arm, wid, trial)
    row = empty_raw_row(key, world, fp)
    offset = world_calls_offset(ctx.world_calls_path)
    try:
        ctx.activate({"world_id": wid, "trial": trial, "arm": arm, "script": script})
    except Exception as e:
        row["infra_error"] = f"exception:activate: {type(e).__name__}: {e}"[:500]
        return row
    row["submitted_at"] = now_iso()
    t0 = ctx.clock()
    job: dict[str, Any] | None = None
    try:
        sub = ctx.call(arm_url, "sre_investigate_alarm",
                       {"payload": build_payload(world, fp), "wait_seconds": 0})
    except Exception as e:
        row["infra_error"] = f"exception:submit: {type(e).__name__}: {e}"[:500]
        sub = None
    if sub is not None:
        row["investigation_id"] = sub.get("investigation_id")
        status = sub.get("status")
        if status != "accepted":
            row["status"] = status
            row["infra_error"] = f"{status}: {sub.get('reason') or ''}".rstrip(": ")[:500]
        else:
            last_error = ""
            while True:
                try:
                    job = ctx.call(arm_url, "sre_get_investigation",
                                   {"investigation_id": row["investigation_id"]})
                    last_error = ""
                except Exception as e:
                    last_error = f"{type(e).__name__}: {e}"
                if job is not None and job.get("status") in TERMINAL_STATUSES:
                    break
                if ctx.clock() - t0 >= ctx.per_run_timeout_s:
                    tail = f" (마지막 오류 {last_error})" if last_error else ""
                    row["infra_error"] = (f"poll_timeout: {ctx.per_run_timeout_s}s 안에 종결되지 "
                                          f"않음{tail}")[:500]
                    break
                ctx.sleep(ctx.poll_interval_s)
    row["finished_at"] = now_iso()
    row["wall_s"] = round(ctx.clock() - t0, 3)
    if job is not None:
        row["status"] = job.get("status")
        row["verdict"] = job.get("verdict")
        row["tokens"] = job.get("tokens")
        row["briefing"] = job.get("briefing")
        row["error"] = job.get("error")
        row["reason"] = job.get("reason")
        for k in INSTRUMENTATION_KEYS:
            row[k] = job.get(k)
        if row["infra_error"] is None and job.get("status") in INFRA_TERMINAL:
            detail = job.get("reason") or job.get("verdict") or job.get("error") or ""
            row["infra_error"] = f"{job.get('status')}: {detail}"[:500]
    # 감사 종결 이벤트는 상태 전이 직후에 쓰인다 — 잠깐 기다려 같이 모은다.
    deadline = ctx.clock() + ctx.audit_wait_s
    events = audit_events(audit_path, row["investigation_id"])
    while row["investigation_id"] and not any(
            e.get("event") in TERMINAL_AUDIT_EVENTS for e in events) and ctx.clock() < deadline:
        ctx.sleep(0.1)
        events = audit_events(audit_path, row["investigation_id"])
    row["audit"] = events
    try:
        ctx.activate({"world_id": None, "trial": None, "arm": None})  # 구간 닫기
    except Exception:  # 닫기 실패는 다음 실행의 활성화가 덮는다
        pass
    seen = ctx.seen_health.setdefault(arm, set())
    current = ctx.world_hosts.get(wid, frozenset())
    foreign = frozenset().union(*(h for w, h in ctx.world_hosts.items() if w != wid)) - current
    trace, ambiguous, strays = build_tool_trace(
        read_segment(ctx.world_calls_path, offset), events, seen, ctx.health_tools,
        current_hosts=current, foreign_hosts=foreign)
    row["tool_trace"] = trace
    row["stray_calls_n"] = len(strays)
    if strays:
        row["stray_calls"] = strays
    if ambiguous:
        row["phase_ambiguous"] = True
    return row


def needs_restart(row: Mapping[str, Any]) -> bool:
    """sre_agent 시간 초과·폴링 초과로 끝났는가 — 버려진 holmes 스레드가 계속 도구를 부를 수 있어
    그 실험군 프로세스를 재기동해야 한다(W5 실측 2026-10-13)."""
    return (row.get("status") == TIMEOUT_STATUS
            or str(row.get("infra_error") or "").startswith(POLL_TIMEOUT_PREFIX))


# ─────────────────────────────── GPU 샘플 ───────────────────────────────


def _num(text: str) -> float | str:
    """`nvidia-smi` csv 칸 → 수(「[N/A]」 등은 원문 유지)."""
    try:
        return float(text)
    except ValueError:
        return text


class GpuSampler(threading.Thread):
    """회차 중 `nvidia-smi` 사용률 샘플 → `gpu_samples.jsonl`(§6.7)."""

    def __init__(self, path: Path, interval_s: float,
                 run: Callable[..., Any] = subprocess.run) -> None:
        super().__init__(daemon=True)
        self.path, self.interval_s, self._run = Path(path), interval_s, run
        self._stop_event = threading.Event()

    def sample(self) -> dict[str, Any]:
        """샘플 1건."""
        def q(arg: str) -> list[list[str]]:
            out = self._run(["nvidia-smi", arg, "--format=csv,noheader,nounits"],
                            capture_output=True, text=True, timeout=15, check=False)
            return [[c.strip() for c in ln.split(",")] for ln in (out.stdout or "").splitlines()
                    if ln.strip()]
        gpus = q("--query-gpu=index,utilization.gpu,memory.used,memory.total")
        procs = q("--query-compute-apps=pid")
        return {"ts": now_iso(),
                "gpus": [{"index": _num(g[0]), "util": _num(g[1]), "mem_used_mb": _num(g[2]),
                          "mem_total_mb": _num(g[3])} for g in gpus if len(g) >= 4],
                "procs_n": len(procs)}

    def run(self) -> None:
        while not self._stop_event.wait(self.interval_s):
            try:
                append_jsonl(self.path, self.sample())
            except Exception as e:  # 샘플 실패는 회차를 막지 않는다
                append_jsonl(self.path, {"ts": now_iso(), "error": f"{type(e).__name__}: {e}"})

    def stop(self) -> None:
        """샘플링을 멈춘다."""
        self._stop_event.set()
        self.join(timeout=20)


# ─────────────────────────────── 실행 ───────────────────────────────


@dataclass
class RunOptions:
    """`--run`·`--resume` 옵션."""

    spec_path: Path | None
    cli_site: str = "local"
    test: bool = False
    dry_run: bool = False
    allow_billed: str | None = None
    out_root: Path | None = None
    resume_dir: Path | None = None


def make_run_dir(out_root: Path, site: str) -> tuple[str, Path]:
    """`<out_root>/<YYYYMMDD-HHMMSS-site>/` 생성(같은 초에 겹치면 꼬리 번호)."""
    base = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{site}"
    run_id, n = base, 1
    while (Path(out_root) / run_id).exists():
        n += 1
        run_id = f"{base}-{n}"
    path = Path(out_root) / run_id
    path.mkdir(parents=True)
    return run_id, path


def prepare_arm_dir(run_dir: Path, arm: str, *, resume: bool = False) -> Path:
    """실험군 cwd `<run>/arm-<name>/` — 새 실행이면 비어 있어야 한다(`.env`가 섞이지 않게)."""
    path = Path(run_dir) / f"arm-{arm}"
    if path.exists() and any(path.iterdir()) and not resume:
        raise RunnerError(f"실험군 디렉터리가 비어 있지 않다: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def check_resume(manifest: Mapping[str, Any], *, spec_hash_now: str, commit: str | None,
                 cli_site: str) -> None:
    """`--resume` 조건 — manifest의 명세 해시·커밋·site가 지금과 같아야 한다."""
    problems = []
    if manifest.get("spec_hash") != spec_hash_now:
        problems.append("명세가 바뀌었다(spec_hash 불일치)")
    if manifest.get("commit") != commit:
        problems.append(f"커밋이 다르다({manifest.get('commit')} → {commit})")
    if manifest.get("site") != cli_site:
        problems.append(f"site가 다르다({manifest.get('site')} → {cli_site})")
    if problems:
        raise RunnerError("--resume 거부 — " + "; ".join(problems), 2)


def _worlds_manifest(worlds: Mapping[str, World]) -> dict[str, Any]:
    out = {}
    for wid, w in worlds.items():
        data = Path(w.path).read_bytes() if w.path else b""
        out[wid] = {"split": w.split, "hash": hashlib.sha256(data).hexdigest(),
                    "path": _rel(w.path) if w.path else None}
    return out


def _print_plan(spec: Mapping[str, Any], worlds: Mapping[str, World], plan: Sequence[Any],
                ports: Mapping[str, Any], static: Mapping[str, Any], api_base: str) -> None:
    arms = list(spec["arms"])
    k = int(spec["run"].get("k", 1))
    def p(v: Any) -> str:
        return "<auto>" if v == 0 else str(v)
    print(f"실행 계획: 세계 {len(worlds)} × 실험군 {len(arms)} × k {k} = {len(plan)}회")
    print(f"  세계: {', '.join(worlds)}")
    print(f"  실험군: {', '.join(arms)} (세계마다 순서 회전)")
    print(f"  백엔드: {static['llm_plane']['backend_recorded']} · model "
          f"{spec['llm'].get('model')} · api_base {api_base}")
    print(f"  포트: 세계 {p(ports['world'])}"
          + (f" · APM {p(ports['world_apm'])}" if ports.get("world_apm") is not None else "")
          + (f" · 가짜 LLM {p(ports['fake_llm'])}" if ports.get("fake_llm") is not None else "")
          + " · 실험군 " + ", ".join(f"{a}={p(v)}" for a, v in ports["arms"].items()))
    print(f"  예상 LLM 호출 상한: {static['expected_llm_calls']} "
          f"({static['expected_llm_calls_basis']})")
    for i, r in enumerate(plan[:12], 1):
        print(f"  {i:>3}. {r['world_id']} · trial {r['trial']} · {r['arm']}")
    if len(plan) > 12:
        print(f"  … 외 {len(plan) - 12}회")


def run(opts: RunOptions) -> int:
    """`--run`·`--resume` 본체 → 종료 코드."""
    try:
        return _run(opts)
    except RunnerError as e:
        print(f"중단: {e}", file=sys.stderr)
        return e.exit_code
    except preflight.PreflightError as e:
        print(f"사전 점검 실패: {e}", file=sys.stderr)
        return e.exit_code


def _run(opts: RunOptions) -> int:
    manifest: dict[str, Any] = {}
    if opts.resume_dir is not None:
        manifest = json.loads((Path(opts.resume_dir) / "manifest.json").read_text(encoding="utf-8"))
        spec_path = Path(opts.spec_path or manifest["spec_path"])
    elif opts.spec_path is None:
        raise RunnerError("--spec가 필요하다", 2)
    else:
        spec_path = Path(opts.spec_path)
    spec, text = load_spec(spec_path)
    commit, dirty = git_commit()
    if opts.resume_dir is not None:
        check_resume(manifest, spec_hash_now=spec_hash(text), commit=commit, cli_site=opts.cli_site)

    worlds = select_worlds(spec)
    snapshot_file = snapshot_path_of(spec)
    snapshot = schema.load_snapshot(snapshot_file)
    arms = list(spec["arms"])
    llm = spec["llm"]
    run_cfg = spec["run"]
    use_fake = llm.get("backend") == "fake"
    use_apm = needs_apm(worlds, snapshot)
    port_plan = plan_ports(spec, arms, use_fake=use_fake, use_apm=use_apm)
    api_base = (f"http://127.0.0.1:{port_plan['fake_llm']}/v1" if use_fake
                else str(llm.get("api_base") or ""))
    static = preflight.static_checks(
        spec, cli_site=opts.cli_site, test=opts.test, allow_billed=opts.allow_billed,
        api_base=api_base or None, ports=planned_port_list(port_plan), n_worlds=len(worlds))
    plan = plan_runs(list(worlds), arms, int(run_cfg.get("k", 1)))
    if llm.get("backend") == "fabrix_proxy":
        print(f"예상 FabriX 호출 상한: {static['expected_llm_calls']} "
              f"({static['expected_llm_calls_basis']}) — 규모는 명세 worlds·k로 정한다")
    if opts.dry_run:
        _print_plan(spec, worlds, plan, port_plan, static, api_base)
        return 0

    if opts.resume_dir is not None:
        run_dir = Path(opts.resume_dir)
        run_id = str(manifest["run_id"])
        manifest.setdefault("resumed_at", []).append(now_iso())
    else:
        run_id, run_dir = make_run_dir(
            Path(opts.out_root or run_cfg.get("out_root") or DEFAULT_OUT_ROOT), opts.cli_site)
        manifest = {
            "run_id": run_id, "site": opts.cli_site, "test_mode": opts.test, "status": "running",
            "commit": commit, "commit_dirty": dirty,
            "spec_path": str(Path(spec_path).resolve()), "spec_hash": spec_hash(text),
            "spec": redact_spec(spec), "spec_text": redact_spec_text(text, spec),
            "worlds_dir": _rel(worlds_dir_of(spec)), "worlds": _worlds_manifest(worlds),
            "snapshot_path": _rel(snapshot_file), "snapshot_hash": schema.snapshot_hash(snapshot),
            "backend": static["llm_plane"]["backend_recorded"], "model": llm.get("model"),
            "api_base": api_base, "allow_billed": opts.allow_billed or None,
            "planned_runs": len(plan), "started_at": now_iso(), "finished_at": None,
            "preflight": {"static": static}, "arms": {}, "excluded_arms": {},
            "invalid_count": 0, "judge_version": None,
        }
    manifest_path = run_dir / "manifest.json"
    write_json(manifest_path, manifest)
    raw_path = run_dir / "raw.jsonl"
    todo = pending_runs(plan, read_jsonl(raw_path))
    if opts.resume_dir is not None:
        print(f"이어서 실행: 남은 {len(todo)}/{len(plan)}회")
        if not todo:  # 다 끝난 회차 — 프로세스를 띄우지 않고 채점만 다시 한다
            return score(run_dir, manifest)

    procs = ProcessGroup(run_dir / "pids.json")
    sampler: GpuSampler | None = None
    session_walls: list[float] = []
    status = "aborted"
    try:
        ports = materialize_ports(port_plan)
        py = sys.executable
        # 세계 서버·가짜 LLM도 실험군과 같은 최소 환경만 물려받는다.
        base_env = {k: os.environ[k] for k in INHERITED_ENV_KEYS if k in os.environ}
        base_env["PYTHONPATH"] = str(SCRIPTS_DIR)
        worlds_dir = worlds_dir_of(spec)
        fake_url = None
        if use_fake:
            fp = procs.launch("fake_llm", [py, "-m", "investigation_bench.fake_llm", "--worlds",
                                           str(worlds_dir), "--port", str(ports["fake_llm"]),
                                           "--log", str(run_dir / "fake_llm.jsonl")],
                              cwd=run_dir, env=base_env, port=ports["fake_llm"],
                              log_path=run_dir / "fake_llm.log")
            api_base = f"http://127.0.0.1:{ports['fake_llm']}/v1"
            wait_http(f"{api_base}/models", fp)
            fake_url = f"http://127.0.0.1:{ports['fake_llm']}/_bench/active"
            manifest["api_base"] = api_base
        live_urls = run_cfg.get("schema_live_urls") or {}
        gpu_before = preflight.gpu_processes()
        try:
            live = preflight.live_checks(api_base=api_base, model=str(llm.get("model")),
                                         api_key=str(llm.get("api_key") or "dummy"),
                                         snapshot=snapshot, live_urls=live_urls)
        except preflight.PreflightError as e:
            manifest["status"] = "preflight_failed"
            manifest["preflight"]["error"] = str(e)
            write_json(manifest_path, manifest)
            raise
        manifest["preflight"]["live"] = live
        manifest["serving"] = live["serving"]
        manifest["versions"] = live["versions"]
        manifest["host"] = {**live["host"], "gpu_processes_before": gpu_before}
        write_json(manifest_path, manifest)

        world_cmd = [py, "-m", "investigation_bench.world_server", "--snapshot", str(snapshot_file),
                     "--worlds", str(worlds_dir), "--log", str(run_dir / "world_calls.jsonl"),
                     "--port", str(ports["world"])]
        if ports["world_apm"] is not None:
            world_cmd += ["--apm-port", str(ports["world_apm"])]
        wp = procs.launch("world_server", world_cmd, cwd=run_dir, env=base_env,
                          port=ports["world"], log_path=run_dir / "world_server.log")
        world_active = f"http://127.0.0.1:{ports['world']}/_bench/active"
        wait_http(world_active, wp)
        polestar_url = f"http://127.0.0.1:{ports['world']}/sse"
        apm_url = (f"http://127.0.0.1:{ports['world_apm']}/sse"
                   if ports["world_apm"] is not None else None)

        arm_popen: dict[str, subprocess.Popen[bytes]] = {}

        def launch_arm(arm: str) -> None:
            arm_dir = run_dir / f"arm-{arm}"
            env = build_arm_env(spec, arm, run_dir=run_dir, polestar_url=polestar_url,
                                apm_url=apm_url, api_base=api_base)
            arm_popen[arm] = procs.launch(
                f"arm-{arm}", [py, "-m", "investigation_bench._serve", "--port",
                               str(ports["arms"][arm])],
                cwd=arm_dir, env=env, port=ports["arms"][arm], log_path=arm_dir / "stderr.log")

        def restart_arm(arm: str) -> float:
            """그 실험군만 내리고 같은 포트·env·cwd로 다시 띄운다(세계 서버는 그대로) → 소요 초."""
            t0 = time.monotonic()
            procs.stop(f"arm-{arm}")
            (run_dir / f"arm-{arm}" / "settings_echo.json").unlink(missing_ok=True)
            launch_arm(arm)
            wait_arm(f"http://127.0.0.1:{ports['arms'][arm]}/sse", arm_popen[arm])
            echo = read_echo(run_dir / f"arm-{arm}")
            problems = (verify_echo(echo, model=str(llm.get("model")), api_base=api_base,
                                    allowed_fields=spec_fields(spec, arm))
                        if echo is not None else [ECHO_MISSING])
            if problems:
                raise RunnerError(f"실험군 {arm} 재기동 뒤 실효 설정 불일치: {problems}", 2)
            ctx.seen_health.pop(arm, None)  # 새 프로세스는 holmes 등록 헬스체크를 다시 한다
            return round(time.monotonic() - t0, 3)

        for arm in arms:
            prepare_arm_dir(run_dir, arm, resume=opts.resume_dir is not None)
            launch_arm(arm)
        excluded: dict[str, str] = dict(manifest.get("excluded_arms") or {})
        for arm in arms:
            url = f"http://127.0.0.1:{ports['arms'][arm]}/sse"
            wait_arm(url, arm_popen[arm])
            echo = read_echo(run_dir / f"arm-{arm}")
            prev = manifest["arms"].get(arm) or {}
            manifest["arms"][arm] = {"port": ports["arms"][arm], "settings_echo": echo,
                                     "restarts": int(prev.get("restarts") or 0),
                                     "restart_s": list(prev.get("restart_s") or [])}
            if echo is None:
                excluded[arm] = ECHO_MISSING
                continue
            problems = verify_echo(echo, model=str(llm.get("model")), api_base=api_base,
                                   allowed_fields=spec_fields(spec, arm))
            if problems:
                raise RunnerError(
                    f"실험군 {arm} 실효 설정이 사전 점검 판정과 다르다: {problems}", 2)
        manifest["excluded_arms"] = excluded
        write_json(manifest_path, manifest)

        interval = float((spec.get("host") or {}).get("gpu_sample_interval_s") or 0)
        if opts.cli_site == "closed" and interval > 0 and live["host"]["gpu"].get("status") == "ok":
            sampler = GpuSampler(run_dir / "gpu_samples.jsonl", interval)
            sampler.start()

        def activate(body: Mapping[str, Any]) -> None:
            post_active(world_active, {k: body.get(k) for k in ("world_id", "trial", "arm")})
            if fake_url is not None:
                post_active(fake_url, body)

        ctx = RunContext(
            run_id=run_id, world_calls_path=run_dir / "world_calls.jsonl",
            poll_interval_s=float(run_cfg.get("poll_interval_s", 2)),
            per_run_timeout_s=float(run_cfg.get("per_run_timeout_s", 420)),
            activate=activate,
            world_hosts={wid: world_host_set(w) for wid, w in worlds.items()},
        )
        script = str(llm.get("script") or "model")
        for i, key in enumerate(todo, 1):
            world = worlds[key["world_id"]]
            if key["arm"] in excluded:
                row = empty_raw_row(key, world, fingerprint(run_id, key["arm"], key["world_id"],
                                                            key["trial"]))
                row["infra_error"] = row["excluded"] = excluded[key["arm"]]
            else:
                url = f"http://127.0.0.1:{ports['arms'][key['arm']]}/sse"
                row = execute_one(ctx, key, world, url,
                                  run_dir / f"arm-{key['arm']}" / "audit.jsonl", script)
                session_walls.append(float(row.get("wall_s") or 0))
                if needs_restart(row):
                    try:
                        took = restart_arm(key["arm"])
                    except Exception:
                        append_jsonl(raw_path, row)
                        raise
                    row["arm_restarted_after"] = True
                    info = manifest["arms"][key["arm"]]
                    info["restarts"] += 1
                    info["restart_s"].append(took)
                    write_json(manifest_path, manifest)
                    print(f"  실험군 {key['arm']} 재기동({took}s)"
                          " — 시간 초과 뒤 남은 조사 스레드 정리")
            append_jsonl(raw_path, row)
            err = row.get("infra_error")
            mark = f"무효({err[:60]})" if err else row.get("status")
            print(f"[{i}/{len(todo)}] {key['world_id']} · trial {key['trial']} · "
                  f"{key['arm']} → {mark}")
        status = "finished"
    finally:
        if sampler is not None:
            sampler.stop()
        procs.stop_all()
        rows = read_jsonl(raw_path)
        if manifest.get("status") != "preflight_failed":
            manifest["status"] = status
        manifest["finished_at"] = now_iso()
        manifest["invalid_count"] = sum(1 for r in rows if r.get("infra_error"))
        manifest["avg_run_s"] = (round(sum(session_walls) / len(session_walls), 3)
                                 if session_walls else manifest.get("avg_run_s"))
        if "host" in manifest:
            manifest["host"]["gpu_processes_after"] = preflight.gpu_processes()
        write_json(manifest_path, manifest)

    return score(run_dir, manifest)


def score(run_dir: Path, manifest: dict[str, Any] | None = None) -> int:
    """raw를 다 쓴 뒤 채점+보고서 1회(`judge.rejudge` · W3a)."""
    try:
        from investigation_bench import judge
    except ImportError as e:
        print(f"채점 생략 — 채점 모듈을 불러올 수 없다: {e}", file=sys.stderr)
        return 1
    if manifest is not None:
        manifest["judge_version"] = judge.JUDGE_VERSION
        write_json(Path(run_dir) / "manifest.json", manifest)
    report = judge.rejudge(Path(run_dir), worlds_root(Path(run_dir)))
    print(f"채점·보고서: {report}")
    return 0


def worlds_root(run_dir: Path) -> Path:
    """재채점에 쓸 세계 루트 — manifest의 `worlds_dir`(저장소 상대) → 없으면 기본."""
    path = Path(run_dir) / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    candidate = resolve_path(manifest.get("worlds_dir") or DEFAULT_WORLDS_DIR)
    return candidate if candidate.is_dir() else resolve_path(DEFAULT_WORLDS_DIR)


# ─────────────────────────────── 보조 명령 ───────────────────────────────


def show_env(spec_path: Path, cli_site: str = "local") -> int:
    """실험군별로 조립될 sre_agent 환경변수(비밀 값은 가림)."""
    spec, _ = load_spec(spec_path)
    arms = list(spec["arms"])
    use_fake = spec["llm"].get("backend") == "fake"
    try:
        worlds = select_worlds(spec)
        use_apm = needs_apm(worlds, schema.load_snapshot(snapshot_path_of(spec)))
    except (RunnerError, OSError):
        use_apm = False
    ports = plan_ports(spec, arms, use_fake=use_fake, use_apm=use_apm)
    def p(v: int | None) -> str:
        return "<auto>" if v == 0 else str(v)
    api_base = (f"http://127.0.0.1:{p(ports['fake_llm'])}/v1" if use_fake
                else str(spec["llm"].get("api_base") or ""))
    print(f"site: 명세 {spec['run'].get('site', 'local')} · CLI {cli_site}")
    for arm in arms:
        env = build_arm_env(
            spec, arm, run_dir=Path("<run>"), polestar_url=f"http://127.0.0.1:{p(ports['world'])}/sse",
            apm_url=(f"http://127.0.0.1:{p(ports['world_apm'])}/sse" if use_apm else None),
            api_base=api_base)
        print(f"[arm {arm}] port {p(ports['arms'][arm])}")
        for k, v in mask_env(env).items():
            print(f"  {k}={v}")
    return 0


def run_preflight_only(spec_path: Path, *, cli_site: str, test: bool,
                       allow_billed: str | None) -> int:
    """`--preflight` — 사전 점검만(가짜 LLM은 점검 동안만 스레드로 띄운다). 통과 = 0."""
    try:
        spec, _ = load_spec(spec_path)
        worlds = select_worlds(spec)
        snapshot = schema.load_snapshot(snapshot_path_of(spec))
        llm = spec["llm"]
        use_fake = llm.get("backend") == "fake"
        ports = plan_ports(spec, list(spec["arms"]), use_fake=use_fake,
                           use_apm=needs_apm(worlds, snapshot))
        api_base = (f"http://127.0.0.1:{ports['fake_llm']}/v1" if use_fake
                    else str(llm.get("api_base") or ""))
        static = preflight.static_checks(
            spec, cli_site=cli_site, test=test, allow_billed=allow_billed,
            api_base=api_base or None, ports=planned_port_list(ports), n_worlds=len(worlds))
        fake = None
        if use_fake:
            from investigation_bench.fake_llm import FakeLLM
            fake = FakeLLM(worlds)
            api_base = f"http://127.0.0.1:{fake.start(ports['fake_llm'] or 0)}/v1"
        try:
            live = preflight.live_checks(
                api_base=api_base, model=str(llm.get("model")),
                api_key=str(llm.get("api_key") or "dummy"), snapshot=snapshot,
                live_urls=spec["run"].get("schema_live_urls") or {})
        finally:
            if fake is not None:
                fake.stop()
    except (RunnerError, preflight.PreflightError) as e:
        print(f"사전 점검 실패: {e}", file=sys.stderr)
        return e.exit_code
    plane = static["llm_plane"]
    print(f"과금 평면: 통과 · 백엔드 {plane['backend_recorded']} · model {plane['model']} · "
          f"api_base {plane['api_base']}" + (f" · 승인 문구 {plane['allow_billed']!r}"
                                            if plane["allow_billed"] else ""))
    print(f"포트: {static['ports']['planned'] or '자동'} 비어 있음")
    print(f"예상 LLM 호출 상한: {static['expected_llm_calls']} "
          f"({static['expected_llm_calls_basis']})")
    serving = live["serving"]
    print(f"서빙: 모델 {serving['models']} · max_model_len {serving['max_model_len']}")
    print(f"tool-calling 왕복: 통과({live['tool_call_roundtrip']['tool']})")
    print(f"스키마: {live['schema']['status']} · 해시 {live['schema']['snapshot_hash'][:12]}")
    gpu = live["host"]["gpu"]
    n_gpu = f" · {len(gpu.get('gpus') or [])}장" if gpu.get("gpus") else ""
    print(f"GPU: {gpu.get('status')}{n_gpu}")
    print(f"버전: {live['versions']}")
    return 0


def status(run_dir: Path) -> int:
    """`--status` — 진행 수 · 무효표 수 · 경과 시간 · 살아 있는 자기 프로세스."""
    manifest = json.loads((Path(run_dir) / "manifest.json").read_text(encoding="utf-8"))
    rows = read_jsonl(Path(run_dir) / "raw.jsonl")
    invalid = sum(1 for r in rows if r.get("infra_error"))
    started = _epoch(manifest.get("started_at"))
    end = _epoch(manifest.get("finished_at")) or time.time()
    elapsed = f"{(end - started) / 60:.1f}분" if started else "?"
    pids_path = Path(run_dir) / "pids.json"
    alive = 0
    if pids_path.exists():
        procs = json.loads(pids_path.read_text(encoding="utf-8")).get("processes") or []
        alive = sum(1 for p in procs if _alive(int(p["pid"]))
                    and PROCESS_MARKER in _cmdline(int(p["pid"])))
    print(f"{manifest.get('run_id')} · 상태 {manifest.get('status')} · "
          f"백엔드 {manifest.get('backend')}")
    print(f"진행 {len(rows)}/{manifest.get('planned_runs')} · 무효표 {invalid} · 경과 {elapsed}"
          f" · 살아 있는 벤치 프로세스 {alive}")
    return 0
