"""run 디렉터리 · 격리 보관 (plans/164 W2a · §4.2(b) · G-2).

run 디렉터리(`results/apm_bench/<run_id>/` 또는 `--out`) 한 곳에 벤치가 직접 모은다.

    run.pid           실행 중 `<PID> <run_id> <시작 epoch>` — 정상·예외 종료 모두 지운다(강제 종료면
                      남는다 → 낡은 PID 판별 · 살아 있는 PID 가 있으면 그 디렉터리 재사용 거부)
    run_meta.json     화이트리스트 run 메타(`safe_summary.run_meta_doc` — 관문 산출물
                      `run.json`과 다름)
    run.log           이번 run 콘솔 줄 tee(콘솔 = 로그) · run 끝 관문 재검사(2차 방어)
    scenarios.yaml    쓴 시나리오 파일이 저장소 안일 때만 사본(밖이면 해시만)
    산출물 6종 또는 leak_check.json · summary_safe.json · manifest.sha256(반출 허용 파일만)

**격리 보관**(관문 실패 run 한정 — E2 「원본 디스크 미기록」 규칙의 개정 · G-2): 치환 전 재료를
**저장소 밖 고정 경로** `~/apm_bench_quarantine/<run_id>/`(`--quarantine-dir <기준>`이면
`<기준>/<run_id>/`)에 남긴다 — 디렉터리 700 · 파일 600 · 이미 있으면 거부 · 저장소·run 디렉터리
하위면 거부(심볼릭 링크 해석 뒤) · 최대 14일 보존(`--purge`). `--regate`가 읽을 때는 현재 uid 소유 ·
권한 700 이 아니면 거부한다(신뢰 경계 = 같은 머신·같은 계정). 운영 이름 원문을 담으므로 반출
금지다 — 콘솔·실행 로그에는 경로·내용 없이 보관 여부만 찍는다.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import time
from collections.abc import Collection, Iterator, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import IO, Any

from . import REPO_ROOT

PID_FILE = "run.pid"
LOG_FILE = "run.log"
MANIFEST_FILE = "manifest.sha256"
SCENARIO_COPY = "scenarios.yaml"
#: run 메타 파일 이름(정본 — `safe_summary.RUN_META_FILE`이 이 값을 쓴다).
RUN_META = "run_meta.json"
#: 격리 기본 기준 디렉터리 이름(`Path.home()` 아래 — 저장소 밖).
QUARANTINE_DIRNAME = "apm_bench_quarantine"
#: 격리본 최대 보존 일수(`--purge` 인자 없음 = 이보다 오래된 격리본 삭제).
QUARANTINE_DAYS = 14
#: 격리본 파일(`write_quarantine` — `--regate`가 읽는다).
QUARANTINE_FILES: tuple[str, ...] = (
    "context.json", "records.json", "meta.json", "probe_rows.json", "placeholders.json",
    "term_policy.json", "versions.json", SCENARIO_COPY,
)
#: 반출 허용 파일(매니페스트) — 관문 통과 · 실패. `run.pid`·시나리오 사본은 넣지 않는다.
EXPORT_PASSED: tuple[str, ...] = (
    "trace.jsonl", "run.json", "probe.jsonl", "coverage.md", "report.md", "leak_check.json",
    "summary_safe.json", LOG_FILE, "run_meta.json",
)
EXPORT_FAILED: tuple[str, ...] = ("leak_check.json", "summary_safe.json", LOG_FILE,
                                  "run_meta.json")


#: Windows 에는 POSIX 소유자(uid)·권한 비트가 없다(NTFS ACL — `os.geteuid` 없음 · `st_mode`
#: 권한은 읽기 전용 여부만 반영). 소유·권한 검사는 POSIX 에서만 하고, 심볼릭 링크 거부는 둘 다 한다.
IS_WINDOWS = os.name == "nt"
#: Windows 에 없는 열기 플래그는 0(심볼릭 링크 거부는 열기 전 `is_symlink` 검사가 대신한다).
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


def owned_by_me(info: os.stat_result) -> bool:
    """현재 계정 소유인가 — Windows 는 판정하지 않는다(True)."""
    return IS_WINDOWS or info.st_uid == os.getuid()


def private_bits(info: os.stat_result) -> int:
    """그룹·기타 권한 비트(`0o077`) — Windows 는 0(권한 비트 없음)."""
    return 0 if IS_WINDOWS else stat.S_IMODE(info.st_mode) & 0o077


class QuarantineError(ValueError):
    """격리 경로·권한 거부(고정 문구 — 경로를 싣지 않는다)."""


def default_quarantine_base() -> Path:
    return Path.home() / QUARANTINE_DIRNAME


def inside(path: Path, root: Path) -> bool:
    """`path`가 `root` 자신이거나 그 아래인가(둘 다 해석된 경로로 넘긴다).

    문자열 대조에 더해 `path`의 존재하는 가장 가까운 상위 경로부터 `os.path.samefile`로 `root`와
    대조한다 — 대소문자 무시 파일 시스템(APFS)의 대소문자 변형 경로까지 잡는다(V-3).
    """
    try:
        path.relative_to(root)
    except ValueError:
        pass
    else:
        return True
    if not root.exists():
        return False
    anchor = path
    while not anchor.exists() and anchor != anchor.parent:
        anchor = anchor.parent
    for candidate in (anchor, *anchor.parents):
        try:
            if os.path.samefile(candidate, root):
                return True
        except OSError:
            continue
    return False


def _check_private_dir(path: Path) -> None:
    """이미 있는 격리 기준 디렉터리 — 심볼릭 링크 · 다른 계정 소유 · 그룹·기타 권한이면 거부."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if (stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode)
            or not owned_by_me(info) or private_bits(info)):
        raise QuarantineError("격리 기준 경로가 심볼릭 링크이거나 현재 계정 전용(700)이 아니다 — "
                              "전용 디렉터리를 지정하거나 chmod 700 한다")


def check_quarantine_base(base: Path, run_dir: Path | None = None) -> Path:
    """격리 기준 경로 검사 → 심볼릭 링크를 푼 경로.

    Raises:
        QuarantineError: 저장소 하위 · run 디렉터리와 서로 포함 · 이미 있는데 심볼릭 링크 ·
            다른 계정 소유 · 그룹·기타 권한 있음
    """
    given = base.expanduser()
    resolved = given.resolve()
    if inside(resolved, REPO_ROOT.resolve()):
        raise QuarantineError("격리 경로가 저장소 하위다 — 저장소 밖 경로만 쓴다")
    if run_dir is not None:
        run_resolved = run_dir.expanduser().resolve()
        if inside(resolved, run_resolved) or inside(run_resolved, resolved):
            raise QuarantineError("격리 경로와 run 디렉터리가 서로 포함된다 — 따로 둔다")
    _check_private_dir(given)
    return resolved


def _make_private_dirs(path: Path) -> None:
    """없는 상위 경로까지 700 으로 만든다(`mkdir(parents=True)`는 중간 디렉터리에 mode 를
    적용하지 않는다)."""
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        os.chmod(directory, 0o700)


def _write_private(dir_fd: int | None, name: str, body: str) -> None:
    """디렉터리 핸들 아래 새 파일을 600 으로 만든다(이미 있거나 심볼릭 링크면 실패). Windows 는
    핸들 대신 경로(`dir_fd` None → `name`이 전체 경로)로 만든다 — `dir_fd`·`fchmod` 미지원."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW
    fd = os.open(name, flags, 0o600) if dir_fd is None else os.open(name, flags, 0o600,
                                                                   dir_fd=dir_fd)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        if not IS_WINDOWS:
            os.fchmod(handle.fileno(), 0o600)
        handle.write(body)


def write_quarantine(base: Path, run_dir: Path, run_id: str,
                     payload: Mapping[str, Any], scenario_text: str) -> Path:
    """치환 전 재료를 `<기준>/<run_id>/`에 700/600 으로 쓴다.

    Args:
        base: 격리 기준 경로(`check_quarantine_base` 통과)
        run_dir: run 디렉터리(포함 관계 재검사용)
        run_id: run id(하위 디렉터리 이름)
        payload: `QUARANTINE_FILES`의 JSON 파일 이름(확장자 포함) → 값
        scenario_text: 쓴 시나리오 파일 본문

    Raises:
        QuarantineError: 경로 거부 · 이미 있음
    """
    root = check_quarantine_base(base, run_dir)
    _make_private_dirs(root)
    _check_private_dir(root)  # 검사와 생성 사이에 바뀌지 않았는지 다시 본다
    final = root / run_id
    try:
        final.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise QuarantineError("격리본이 이미 있다 — 덮어쓰지 않는다") from exc
    if IS_WINDOWS:
        for name, value in payload.items():
            _write_private(None, str(final / name),
                           json.dumps(value, ensure_ascii=False, default=str))
        _write_private(None, str(final / SCENARIO_COPY), scenario_text)
        return final
    dir_fd = os.open(final, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fchmod(dir_fd, 0o700)
        for name, value in payload.items():
            _write_private(dir_fd, name, json.dumps(value, ensure_ascii=False, default=str))
        _write_private(dir_fd, SCENARIO_COPY, scenario_text)
    finally:
        os.close(dir_fd)
    return final


def check_trusted(path: Path) -> Path:
    """격리 디렉터리 신뢰 검사 — 심볼릭 링크 아님 · 현재 uid 소유 · 권한 700 · 파일도 같은 uid ·
    그룹·기타 권한 없음.

    Raises:
        QuarantineError: 없음 · 디렉터리 아님(심볼릭 링크 포함) · 소유자·권한 불일치
    """
    given = path.expanduser()
    try:
        info = os.lstat(given)
    except OSError as exc:
        raise QuarantineError("격리본이 없다") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise QuarantineError("격리본이 디렉터리가 아니다")
    resolved = given.resolve()
    if not IS_WINDOWS and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
        raise QuarantineError("격리본 소유자·권한이 맞지 않다(현재 계정 소유 · 700 만 읽는다)")
    for child in resolved.iterdir():
        child_info = child.lstat()
        if (not stat.S_ISREG(child_info.st_mode) or not owned_by_me(child_info)
                or private_bits(child_info)):
            raise QuarantineError("격리본 파일 소유자·권한이 맞지 않다(현재 계정 소유 · 600)")
    return resolved


def read_quarantine(path: Path) -> tuple[dict[str, Any], str]:
    """신뢰 검사 뒤 격리본 → (JSON 파일 이름 → 값, 시나리오 본문)."""
    root = check_trusted(path)
    data: dict[str, Any] = {}
    for name in QUARANTINE_FILES:
        if name == SCENARIO_COPY:
            continue
        try:
            data[name] = json.loads((root / name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise QuarantineError("격리본 파일을 읽지 못했다") from exc
    try:
        scenario_text = (root / SCENARIO_COPY).read_text(encoding="utf-8")
    except OSError as exc:
        raise QuarantineError("격리본 시나리오 사본을 읽지 못했다") from exc
    return data, scenario_text


def is_quarantine(child: Path) -> bool:
    """격리본으로 보는가 — 이름이 run id 형식 · 실 디렉터리(심볼릭 링크 아님) · `context.json`
    있음 · 신뢰 검사(`check_trusted`) 통과. 그 밖은 `--purge`가 건드리지 않는다."""
    from .safe_summary import RUN_ID_RE

    if not RUN_ID_RE.fullmatch(child.name):
        return False
    try:
        info = os.lstat(child)
    except OSError:
        return False
    if not stat.S_ISDIR(info.st_mode) or not (child / "context.json").is_file():
        return False
    try:
        check_trusted(child)
    except QuarantineError:
        return False
    return True


def purge(base: Path, run_id: str | None, *, now: float | None = None,
          days: int = QUARANTINE_DAYS) -> tuple[list[str], list[tuple[str, int]], int]:
    """격리본 삭제 → (지운 run id, 남은 (run id, 경과 일), 격리본이 아니라 건드리지 않은 항목 수).

    `run_id`가 있으면 그것만 · 없으면 `days`일 지난 것 전부. 대상은 `is_quarantine` 통과분뿐이다
    (기준 경로 아래 다른 디렉터리·파일은 이름 무관하게 남긴다 · V-1). 기준 경로는 먼저
    `check_quarantine_base`로 검사한다(V-2).

    Raises:
        QuarantineError: 기준 경로 거부(저장소 하위 · 심볼릭 링크 · 권한)
    """
    root = check_quarantine_base(base)
    if not root.is_dir():
        return [], [], 0
    clock = time.time() if now is None else now
    removed: list[str] = []
    kept: list[tuple[str, int]] = []
    other = 0
    for child in sorted(root.iterdir()):
        if not is_quarantine(child):
            other += 1
            continue
        age = int((clock - os.lstat(child).st_mtime) // 86400)
        if (run_id is not None and child.name == run_id) or (run_id is None and age >= days):
            shutil.rmtree(child)
            removed.append(child.name)
        else:
            kept.append((child.name, age))
    return removed, kept, other


def claim_pid(run_dir: Path, run_id: str, started: float) -> bool:
    """`run.pid`(`<PID> <run_id> <시작 epoch>`)를 새로 만든다 — 살아 있거나 확인 불가한 PID 가 이미
    있으면 False(같은 run 디렉터리 동시 사용 거부 · 낡은 파일은 지우고 다시 만든다 · `O_EXCL`)."""
    path = run_dir / PID_FILE
    if path.exists() or path.is_symlink():
        if pid_alive(read_pid(run_dir)) is not False:
            return False
        path.unlink()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"{os.getpid()} {run_id} {started:.0f}\n")
    return True


def remove_pid(run_dir: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        (run_dir / PID_FILE).unlink()


@contextlib.contextmanager
def opened_log(run_dir: Path, *, append: bool = False) -> Iterator[IO[str]]:
    """`run.log` 손잡이(덧붙이기 · 새로 쓰기) — 심볼릭 링크면 거부(`O_NOFOLLOW`)."""
    flags = os.O_WRONLY | os.O_CREAT | O_NOFOLLOW | (os.O_APPEND if append else os.O_TRUNC)
    if (run_dir / LOG_FILE).is_symlink():  # O_NOFOLLOW 없는 플랫폼(Windows) 대비
        raise QuarantineError("run.log 를 열지 못했다(심볼릭 링크 거부)")
    try:
        fd = os.open(run_dir / LOG_FILE, flags, 0o644)
    except OSError as exc:  # 심볼릭 링크(ELOOP) 등 — 경로 미출력
        raise QuarantineError("run.log 를 열지 못했다(심볼릭 링크 거부)") from exc
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        yield handle


def suppress_log(run_dir: Path, head: str, violations: Sequence[Mapping[str, Any]]) -> None:
    """`run.log`를 머리 + 억제 줄로 갈아쓴다(규칙별 건수만 — 위치·값 없음)."""
    rules: dict[str, int] = {}
    for v in violations:
        rules[str(v.get("rule"))] = rules.get(str(v.get("rule")), 0) + 1
    (run_dir / LOG_FILE).write_text(
        f"{head}\n[위생] 로그 출력 억제 — 위반 {len(violations)}건"
        f"({dict(sorted(rules.items()))})\n",
        encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def write_manifest(run_dir: Path, *, gate_passed: bool) -> list[str]:
    """반출 허용 파일만 `sha256sum` 형식으로 → `manifest.sha256` · 실은 파일 이름 목록."""
    names = [n for n in (EXPORT_PASSED if gate_passed else EXPORT_FAILED)
             if (run_dir / n).is_file()]
    body = "".join(f"{sha256_file(run_dir / n)}  {n}\n" for n in names)
    (run_dir / MANIFEST_FILE).write_text(body, encoding="utf-8")
    return names


#: 구 방식 run id(W2a 전 — 운영자가 실행 로그 이름 `run_<id>.log`에 붙인 `YYYYmmdd_HHMM[SS]` ·
#: run 메타 없음). `--audit-digest`만 받는다(plans/164 W2b).
LEGACY_RUN_ID_RE = re.compile(r"\d{8}_\d{4}(?:\d{2})?")


def _started_ts(run_dir: Path) -> float:
    """run 메타 `started_at` → epoch 초(없거나 못 읽으면 0)."""
    try:
        raw = json.loads((run_dir / RUN_META).read_text(encoding="utf-8"))
        return datetime.fromisoformat(str(raw["started_at"])).timestamp()
    except (OSError, ValueError, KeyError, TypeError):
        return 0.0


def latest_run(root: Path) -> Path | None:
    """`root` 아래 가장 최근 run 디렉터리 — run 메타 있는 것 우선(시작 시각 · 이름 순), 없으면
    run id 형식 디렉터리 이름 최신(심볼릭 링크는 보지 않는다)."""
    from .safe_summary import RUN_ID_RE

    if not root.is_dir():
        return None
    with_meta: list[tuple[float, str, Path]] = []
    plain: list[Path] = []
    for child in root.iterdir():
        if child.is_symlink() or not child.is_dir():
            continue
        if (child / RUN_META).is_file():
            with_meta.append((_started_ts(child), child.name, child))
        elif RUN_ID_RE.fullmatch(child.name):
            plain.append(child)
    if with_meta:
        return max(with_meta)[2]
    return max(plain, key=lambda p: p.name) if plain else None


def resolve_run(arg: str, root: Path) -> Path | None:
    """run 인자 → run 디렉터리. run id 형식이면 `root/<run_id>`(없어도 그 경로), 있는 경로면 그대로,
    `/` 없는 이름이 `root` 아래 디렉터리면 그것 · 그 밖은 None."""
    from .safe_summary import RUN_ID_RE

    if RUN_ID_RE.fullmatch(arg):
        return root / arg
    path = Path(arg).expanduser()
    if path.exists():
        return path
    if "/" not in arg and arg not in ("", ".", "..") and (root / arg).is_dir():
        return root / arg
    return None


def read_pid_record(run_dir: Path) -> tuple[int | None, str | None, float | None]:
    """`run.pid` → (PID, run id, 시작 epoch). 구 형식(PID 한 줄)이면 뒤 둘은 None."""
    try:
        parts = (run_dir / PID_FILE).read_text(encoding="utf-8").split()
        pid = int(parts[0])
    except (OSError, ValueError, IndexError):
        return None, None, None
    run_id = parts[1] if len(parts) > 1 else None
    try:
        started = float(parts[2]) if len(parts) > 2 else None
    except ValueError:
        started = None
    return pid, run_id, started


def read_pid(run_dir: Path) -> int | None:
    return read_pid_record(run_dir)[0]


def pid_alive(pid: int | None) -> bool | None:
    """프로세스가 살아 있는가(신호 0) — 다른 계정 프로세스(EPERM)면 None(확인 불가 · 낡은
    `run.pid`가 재사용된 PID 를 가리킬 수 있다). Windows 는 `os.kill`이 프로세스를 끝내므로 쓰지
    않고 프로세스 핸들로 묻는다(`_windows_pid_alive`)."""
    if pid is None or pid <= 0:
        return False
    if IS_WINDOWS:
        return _windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return None
    return True


def _windows_pid_alive(pid: int) -> bool | None:
    """Windows 프로세스 생존 — `OpenProcess`(조회 권한) → `GetExitCodeProcess` == STILL_ACTIVE.

    접근 거부(다른 계정 · 권한 상승 프로세스)면 None(확인 불가), 없는 PID 면 False.
    """
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None if ctypes.get_last_error() == 5 else False  # type: ignore[attr-defined]
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return None
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


#: `pid_state` 값 — 파일 없음 · 실행 중 · 프로세스 없음 · 확인 불가(다른 계정) · 다른 run 의 PID.
PID_STATES: tuple[str, ...] = ("none", "alive", "dead", "unknown", "stale")
#: `run.pid` 시작 epoch 와 run 메타 `started_at` 허용 차(초 — 같은 run 이면 같은 시각을 쓴다).
PID_START_TOLERANCE_SEC = 2.0


def pid_state(run_dir: Path, *, run_id: str | None = None,
              started_at: str | None = None) -> tuple[str, int | None]:
    """`run.pid` 상태(`PID_STATES`) · PID — `run.pid`에 적힌 run id·시작 epoch 가 run 메타와 다르면
    다른 run 이 남긴 낡은 파일(`stale`)로 본다(구 형식 PID 한 줄은 대조하지 않는다)."""
    pid, pid_run, pid_started = read_pid_record(run_dir)
    if pid is None:
        return "none", None
    if run_id and pid_run and pid_run != run_id:
        return "stale", pid
    if started_at and pid_started is not None:
        try:
            meta_started = datetime.fromisoformat(started_at).timestamp()
        except ValueError:
            meta_started = None
        if meta_started is not None and abs(meta_started - pid_started) > PID_START_TOLERANCE_SEC:
            return "stale", pid
    alive = pid_alive(pid)
    return ("alive" if alive else "unknown" if alive is None else "dead"), pid


#: `--status` 마지막 줄 — 읽는 꼬리 바이트 · 줄 길이 상한 · 검증 실패 표기.
LOG_TAIL_BYTES = 4096
LOG_LINE_MAX = 300
LOG_UNVERIFIED = "(검증 불가)"
_SCENARIO_ID = r"(APM-[A-Za-z0-9-]{1,60})"
#: 문형 조각 — 자리표 이름(`host_1`) · 상태 표지 · 코드(소문자·밑줄) · 수 · 백분율 · 버전 · 상위 표.
_PH = r"[a-z]{1,20}_\d{1,3}"
_PH_STATUS = r"(?:있음|없음|여럿\(부분 일치 \d+\)|검사 불가|검사 제외)"
_CODE = r"[a-z][a-z_]{0,59}"
_N = r"\d+|None"
_F = r"\d+(?:\.\d+)?|None"
_PCT = r"(?:\d{1,3}\.\d%|—)"
_RATE = rf"{_PCT} \(\d+/\d+\)"
_VER = r"\d+(?:\.\d+){0,3}|해당 없음|e1-mock\(해당 없음\)"
_TOP = rf"없음|{_CODE} \d+(?:, {_CODE} \d+)*"
#: `run.log` 직렬화 문형 화이트리스트 — (정규식 fullmatch, 출력 틀). 모양이 맞아도 원문을 그대로
#: 내지 않고 **틀에 안전한 조각(숫자 · 고정 어휘 · 시나리오 id)만** 다시 채운다(ASCII 호스트 이름이
#: 문자 화이트리스트를 통과하는 문제 · plans/164 W2b 감사 H1). 시나리오 id(틀의 마지막 조각)는
#: `known_ids`가 있으면 그 안에 있어야 한다.
_LOG_SHAPES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"# apm_bench run\.log — run_id (\d{8}-\d{6}-e[12]-(?:[0-9a-f]{7}|unknown)"
                r"(?:-smoke)?) · 시작 (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d)"),
     "# apm_bench run.log — run_id {0} · 시작 {1}"),
    (re.compile(r"\[1/4\] 게이트웨이 기동 — (e[12]) · .*"), "[1/4] 게이트웨이 기동 — {0}"),
    (re.compile(r"인벤토리 이름 (\d+)개\(도구 (\d+)회.*"), "인벤토리 이름 {0}개(도구 {1}회)"),
    (re.compile(r"\[2/4\] 본체 기동 — .*"), "[2/4] 본체 기동"),
    (re.compile(r"사다리 확정: ([a-z_]{1,40}) .*"), "사다리 확정: {0}"),
    (re.compile(r"\[3/4\] 로그인 (완료|생략\(인증 꺼짐\))"), "[3/4] 로그인 {0}"),
    (re.compile(r"\[4/4\] 시나리오 (\d+) \+ 무거운 등급 (\d+)"),
     "[4/4] 시나리오 {0} + 무거운 등급 {1}"),
    (re.compile(rf"(\d+)/(\d+) {_SCENARIO_ID}"), "{0}/{1} {2}"),
    (re.compile(rf"무거운 등급 {_SCENARIO_ID}"), "무거운 등급 {0}"),
    (re.compile(r"\[P층\] 빈 결과 판별 · 허용값 탐침"), "[P층] 빈 결과 판별 · 허용값 탐침"),
    (re.compile(r"\[중단\] .*"), "[중단] (사유는 run.log)"),
    (re.compile(r"\[실패\] 누출 관문 — 위반 (\d+)건.*"), "[실패] 누출 관문 — 위반 {0}건"),
    (re.compile(r"\[위생\] 로그 출력 억제 — 위반 (\d+)건.*"), "[위생] 로그 출력 억제 — 위반 {0}건"),
    (re.compile(r"산출물 +: .* \(관문 통과\)"), "산출물     : (관문 통과)"),
    (re.compile(r"격리 보관 : 있음.*"), "격리 보관 : 있음"),
    (re.compile(r"결과 +: (완료|실패)(?: — .*)?"), "결과       : {0}"),
    # 키트 검사 줄(`kit.kit_lines` · `kit.var_lines`) — 자리표 이름(`host_1`)·숫자·고정 문구만
    (re.compile(r"키트 검사: 자리표 있음 (\d+) · 없음 (\d+) · 여럿 (\d+) · 검사 불가 (\d+) · "
                r"검사 제외 (\d+) · 소스 대응 없음 (\d+) · 소스 불일치 (\d+)"),
     "키트 검사: 자리표 있음 {0} · 없음 {1} · 여럿 {2} · 검사 불가 {3} · 검사 제외 {4} · "
     "소스 대응 없음 {5} · 소스 불일치 {6}"),
    (re.compile(rf"자리표 실재: ({_PH} {_PH_STATUS}(?:, {_PH} {_PH_STATUS})*)"),
     "자리표 실재: {0}"),
    (re.compile(rf"\[경고\] (자리표 없음|자리표 검사 불가|자리표 여럿|소스 대응 없음|소스 불일치) "
                rf"({_PH}(?:, {_PH})*) — .*"), "[경고] {0} {1}"),
    (re.compile(r"\[경고\] apm_gateway/var — 이번 run 에서 새 파일 (\d+) · 바뀐 파일 (\d+).*"),
     "[경고] apm_gateway/var — 이번 run 에서 새 파일 {0} · 바뀐 파일 {1}"),
    (re.compile(r"\[정보\] apm_gateway/var 이전부터 있음 — .*"),
     "[정보] apm_gateway/var 이전부터 있음"),
    (re.compile(r"\[경고\] 지연 기준선 측정 실패: ([A-Za-z_][A-Za-z0-9_]{0,60})"),
     "[경고] 지연 기준선 측정 실패: {0}"),
    (re.compile(r"\[경고\] 격리 보관 실패 — .*"), "[경고] 격리 보관 실패"),
    (re.compile(r"\[경고\] .* — E1 목 인벤토리라 계속한다"),
     "[경고] (사유는 run.log) — E1 목 인벤토리라 계속한다"),
    (re.compile(r"\[경고\] .*"), "[경고] (내용은 run.log)"),
    # 끝 요약 줄(`safe_summary.render_console`) — 코드 · 숫자 · 백분율 · 고정 문구만
    (re.compile(r"\[방향 확인용 — 결론 금지\] env (e[12]) · .*"),
     "[방향 확인용 — 결론 금지] env {0}"),
    (re.compile(rf"사다리 +: ({_CODE}|None) \(사유 ({_CODE}|None)\)"),
     "사다리     : {0} (사유 {1})"),
    (re.compile(rf"턴 +: 실행 ({_N}) · APM 관측 ({_N}) · 건너뜀 ({_N}|없음) · "
                rf"판정 불가 ({_N}|없음)"),
     "턴         : 실행 {0} · APM 관측 {1} · 건너뜀 {2} · 판정 불가 {3}"),
    (re.compile(rf"호출 +: 소스별 .* · 타임아웃 ({_N}) · 429 ({_N}) · 기준선 ({_F})ms · "
                rf"인벤토리 도구 ({_N})회 · 인스턴스 ({_N})(?: · .*)? · P층 ({_N})회"),
     "호출       : 타임아웃 {0} · 429 {1} · 기준선 {2}ms · 인벤토리 도구 {3}회 · 인스턴스 {4} · "
     "P층 {5}회"),
    (re.compile(rf"중단 사유 +: ({_CODE}|없음) · 소요 ({_F})초 · 뷰 서버 ({_VER})"),
     "중단 사유  : {0} · 소요 {1}초 · 뷰 서버 {2}"),
    (re.compile(rf"중단 사유 +: .* · 소요 ({_F})초 · 뷰 서버 ({_VER})"),
     "중단 사유  : (사유는 run.log) · 소요 {0}초 · 뷰 서버 {1}"),
    (re.compile(rf"턴 정확도 +: ({_RATE}(?: · {_CODE} {_RATE})*)"), "턴 정확도  : {0}"),
    (re.compile(rf"축 +: ({_CODE} {_PCT}(?: · {_CODE} {_PCT})*)"), "축         : {0}"),
    (re.compile(rf"대표 분류 +: 실패 턴 ({_TOP})"), "대표 분류  : 실패 턴 {0}"),
    (re.compile(rf"\(통과 턴 포함\) ({_TOP})"), "(통과 턴 포함) {0}"),
    (re.compile(rf"오류 호출 +: 없음 · resolve_miss 턴 ({_N}) · 호출 ({_N})"),
     "오류 호출  : 없음 · resolve_miss 턴 {0} · 호출 {1}"),
    (re.compile(rf"오류 호출 +: .* · resolve_miss 턴 ({_N}) · 호출 ({_N})"),
     "오류 호출  : (목록은 run.log) · resolve_miss 턴 {0} · 호출 {1}"),
    (re.compile(rf"target 사유: ({_TOP})"), "target 사유: {0}"),
    (re.compile(rf"locus 상위 : ({_TOP})"), "locus 상위 : {0}"),
    (re.compile(rf"근거 누락 +: ({_N})(?: — .*)?"), "근거 누락  : {0}"),
    (re.compile(r"사용자 정보: 치환 .*"), "사용자 정보: 치환 있음"),
    (re.compile(r"어휘 밖 +: 버림 .*"), "어휘 밖    : 버림 있음"),
)


def safe_log_line(line: str, known_ids: Collection[str] | None = None) -> str | None:
    """`run.log` 한 줄 → 화이트리스트 틀로 다시 쓴 줄(모양이 안 맞으면 None)."""
    text = line.strip()
    if len(text) > LOG_LINE_MAX:
        return None
    for pattern, template in _LOG_SHAPES:
        match = pattern.fullmatch(text)
        if match is None:
            continue
        groups = match.groups()
        if (known_ids is not None and _SCENARIO_ID in pattern.pattern
                and groups[-1] not in known_ids):
            return None
        return template.format(*groups)
    return None


def last_log_line(run_dir: Path, known_ids: Collection[str] | None = None) -> str | None:
    """`run.log` 마지막 비지 않은 줄의 화이트리스트 본(`safe_log_line`) — 파일·줄이 없으면 None,
    심볼릭 링크 · 일반 파일 아님 · 현재 계정 소유 아님 · 문형 밖이면 `LOG_UNVERIFIED`.

    끝 `LOG_TAIL_BYTES`만 읽는다(`O_NOFOLLOW` · 큰 파일·FIFO 를 통째로 읽지 않는다).
    """
    path = run_dir / LOG_FILE
    if path.is_symlink():  # O_NOFOLLOW 없는 플랫폼(Windows) 대비
        return LOG_UNVERIFIED
    try:
        fd = os.open(path, os.O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError:  # 심볼릭 링크(ELOOP) 등
        return LOG_UNVERIFIED
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or not (IS_WINDOWS or info.st_uid == os.geteuid()):
            return LOG_UNVERIFIED
        start = max(0, info.st_size - LOG_TAIL_BYTES)
        handle.seek(start)
        tail = handle.read(LOG_TAIL_BYTES)
    lines = tail.decode("utf-8", errors="replace").splitlines()
    if start > 0 and lines:
        lines = lines[1:]  # 잘린 첫 줄
    lines = [line for line in lines if line.strip()]
    if not lines:
        return None
    return safe_log_line(lines[-1], known_ids) or LOG_UNVERIFIED


def display(path: Path) -> str:
    """콘솔용 경로 — 저장소 안이면 저장소 기준 상대 경로, 밖이면 마지막 이름만(홈 경로 = 사용자
    정보가 실행 로그에 남지 않게)."""
    resolved = path.expanduser().resolve()
    if inside(resolved, REPO_ROOT.resolve()):
        return str(resolved.relative_to(REPO_ROOT.resolve()))
    return f"<--out>/{resolved.name}" if resolved.name else "<--out>"


__all__ = [
    "EXPORT_FAILED",
    "EXPORT_PASSED",
    "LEGACY_RUN_ID_RE",
    "LOG_FILE",
    "LOG_UNVERIFIED",
    "MANIFEST_FILE",
    "PID_FILE",
    "PID_STATES",
    "QUARANTINE_DAYS",
    "QUARANTINE_FILES",
    "QuarantineError",
    "SCENARIO_COPY",
    "check_quarantine_base",
    "claim_pid",
    "check_trusted",
    "default_quarantine_base",
    "display",
    "inside",
    "is_quarantine",
    "last_log_line",
    "latest_run",
    "opened_log",
    "pid_alive",
    "pid_state",
    "purge",
    "read_pid",
    "read_pid_record",
    "read_quarantine",
    "remove_pid",
    "resolve_run",
    "safe_log_line",
    "sha256_file",
    "suppress_log",
    "write_manifest",
    "write_quarantine",
]
