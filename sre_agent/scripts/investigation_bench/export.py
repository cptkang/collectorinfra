"""반출 묶음 `<run_id>.bundle/` (plans/151 §6.5 · D-324 ⑥).

내부망 회차 산출을 외부망 검토용으로 묶는다. 브리핑 전문·호출 기록·점수는 그대로 두고 **내부 주소·
계정·호스트명·내부 경로만** 가린다. 가린 뒤 잔존 검사(원값 · 사설 IP 대역 · 홈 경로 패턴)에 하나라도
걸리면 임시 디렉터리를 지우고 `ExportLeakError`를 낸다 — 묶음을 만들지 않는다(D-301 「누출 관문
실패 = 미기록」 준용).

- 싣는 것: `manifest.json`·`world_calls.jsonl`·`raw.jsonl`·`scores.jsonl`·`report.md`·
  `gpu_samples.jsonl`(있을 때)·`arm-<name>/settings_echo.json`·
  `arm-<name>/stderr.log`(마지막 200줄). 실험군 감사 파일은 `raw.jsonl`의 `audit[]`에 이미 있어
  싣지 않는다. 세계 YAML은 저장소에 있으므로 싣지 않고 manifest의 커밋 해시로 가리킨다.
- 가리는 것: manifest·설정 에코의 `api_base` 호스트(루프백 제외 — 외부망 MLX 기록은 남긴다) ·
  이름에 key·token·secret·password가 든 칸의 값(자리표시 제외) · 실행 OS 계정 · 실행 호스트명
  (+ manifest 호스트 정보 · 4자 미만은 건너뜀) · 홈·저장소·run·venv 절대 경로와 경로 칸(`*_path`·
  `*_dir` 등)의 절대 경로(표준 시스템 디렉터리 제외) · 사설 IP 대역(IPv4 사설·CGNAT · IPv6 ULA·
  링크로컬) · `nvidia-smi` 프로세스 목록의 사용자명·경로 · 호출자가 준 `extra_secrets`.
  묶음 manifest의 `export`에 표식별 치환 건수를 남긴다.

경계(D-139 · §9.5): `sre_agent` 패키지를 import하지 않는다.
"""

from __future__ import annotations

import getpass
import json
import re
import shutil
import socket
import sys
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

BUNDLE_SUFFIX = ".bundle"
TOP_FILES: tuple[str, ...] = (
    "manifest.json", "world_calls.jsonl", "raw.jsonl", "scores.jsonl", "report.md",
    "gpu_samples.jsonl",
)
ARM_DIR_PREFIX = "arm-"
ARM_SETTINGS = "settings_echo.json"
ARM_STDERR = "stderr.log"
STDERR_TAIL_LINES = 200

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0"})
# manifest 호스트 정보·프로세스 목록에서 가릴 값의 키(대소문자 무시).
HOST_KEYS = frozenset({"hostname", "host_name", "fqdn", "nodename", "node"})
USER_KEYS = frozenset({"user", "username", "user_name", "owner", "login"})
PATH_KEYS = frozenset({"process_name", "path", "cmd", "command", "cmdline", "exe", "cwd"})
# 이 접미사로 끝나는 키의 절대 경로 값도 가린다
# (`spec_path`·`worlds_dir`·`venv` 등 저장소 밖 작업 폴더).
PATH_KEY_SUFFIXES: tuple[str, ...] = ("path", "dir", "venv", "prefix", "root")
# 표준 시스템 디렉터리 자체는 가리지 않는다 — 가리면 브리핑의 `/var/log` 같은 구성요소가 지워진다.
SYSTEM_PATHS = frozenset({
    "/", "/bin", "/sbin", "/usr", "/usr/bin", "/usr/local", "/usr/local/bin", "/usr/lib", "/opt",
    "/var", "/var/log", "/var/lib", "/var/tmp", "/tmp", "/etc", "/home", "/root", "/srv", "/data",
})
# 이름 조각(`_`·`-`·`.` 구분)에 이것이 있는 키의 값은 비밀이다(방어 2선 — 러너가 manifest에서 빼도).
SECRET_KEY_PARTS = frozenset({
    "key", "apikey", "token", "secret", "password", "passwd", "pwd", "credential", "credentials",
})
# 비밀 칸의 자리표시 값 — 가리지 않는다(가리면 `dummy` 같은 낱말이 다 지워진다).
SECRET_PLACEHOLDERS = frozenset({
    "dummy", "<set>", "<unset>", "set", "unset", "none", "null", "true", "false", "***", "redacted",
})
SECRET_MIN_LEN = 4
# 개인을 가리키지 않는 시스템 계정 — 가리면 브리핑의 「root cause」 같은 낱말까지 지워진다.
GENERIC_USERS = frozenset({"root"})
# 이보다 짧은 OS 계정·호스트명은 가리지 않는다 — 단어 경계 매칭이어도 `ops`·`db` 같은 짧은 이름은
# 브리핑 낱말과 겹친다. 건너뛴 수는 묶음 manifest `export.skipped_short_identifiers`에 남긴다.
MIN_IDENT_LEN = 4

# IPv4 사설(10/8 · 172.16/12 · 192.168/16) · 링크로컬(169.254/16) · CGNAT(100.64/10) +
# IPv6 ULA(fc00::/7) · 링크로컬(fe80::/10).
PRIVATE_IP_RE = re.compile(
    r"(?<![\d.])(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}|169\.254\.\d{1,3}\.\d{1,3}"
    r"|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3})(?!\.?\d)"
    r"|(?<![0-9A-Za-z:])(?:f[cd][0-9a-f]{0,2}|fe[89ab][0-9a-f]?)(?::[0-9a-f]{0,4}){2,7}"
    r"(?![0-9A-Za-z:])",
    re.IGNORECASE,
)
HOME_RE = re.compile(r"/home/[^/\s\"'<>]+|/Users/[^/\s\"'<>]+|/root(?=[/\s\"'<>]|$)")
# 가림 표식 — 잔존 검사 전에 지운다(표식 안의 글자가 원값과 겹쳐 오탐하지 않도록).
TOKEN_RE = re.compile(r"<(?:redacted(?::[a-z-]+)?|home|repo|run|private-ip)>")

# 저장소 루트(…/sre_agent/scripts/investigation_bench/export.py 기준 세 단계 위).
_REPO_ROOT = Path(__file__).resolve().parents[3]


class ExportLeakError(Exception):
    """가린 뒤에도 내부 값이 남았다 — 묶음을 만들지 않았다."""


@dataclass(frozen=True)
class Secret:
    """가릴 원값 1건."""

    value: str
    token: str
    ignore_case: bool = False


# ─────────────────────────────── 원값 수집 ───────────────────────────────


def _walk(node: Any, key: str | None = None) -> Iterable[tuple[str | None, Any]]:
    if isinstance(node, Mapping):
        for k, v in node.items():
            yield from _walk(v, str(k))
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v, key)
    else:
        yield key, node


def _url_host(value: str) -> str | None:
    try:
        host = urlsplit(value if "//" in value else f"//{value}").hostname
    except ValueError:
        return None
    return host if host and host.casefold() not in LOOPBACK_HOSTS else None


def _is_secret_key(k: str) -> bool:
    return bool(SECRET_KEY_PARTS.intersection(re.split(r"[_\-.\s]+", k)))


def _is_internal_path(k: str, v: str) -> bool:
    return (v.startswith("/") and v.rstrip("/") not in SYSTEM_PATHS
            and (k in PATH_KEYS or k.endswith(PATH_KEY_SUFFIXES)))


@dataclass
class SecretSet:
    """가릴 원값 목록과 길이 가드로 건너뛴 짧은 계정·호스트명 수."""

    secrets: list[Secret]
    skipped_short: int = 0


def collect_secrets(docs: Iterable[Any], extra: Iterable[str] = ()) -> SecretSet:
    """manifest·설정 에코·GPU 샘플과 실행 환경에서 가릴 원값을 모은다(긴 것 먼저 · 중복 제거)."""
    found: list[Secret] = []
    skipped = 0

    def _ident(value: str, token: str, ignore_case: bool) -> None:
        nonlocal skipped
        if len(value) < MIN_IDENT_LEN:
            skipped += 1
        else:
            found.append(Secret(value, token, ignore_case=ignore_case))

    for doc in docs:
        for key, value in _walk(doc):
            if not isinstance(value, str) or not value.strip() or key is None:
                continue
            k = key.casefold()
            v = value.strip()
            if k == "api_base" or k.endswith("api_base"):
                host = _url_host(v)
                if host:
                    found.append(Secret(host, "<redacted:api-host>", ignore_case=True))
            elif _is_secret_key(k):
                if v.casefold() not in SECRET_PLACEHOLDERS and len(v) >= SECRET_MIN_LEN:
                    found.append(Secret(v, "<redacted:secret>"))
            elif k in HOST_KEYS and v.casefold() not in LOOPBACK_HOSTS:
                _ident(v, "<redacted:host>", True)
            elif k in USER_KEYS and v not in GENERIC_USERS:
                _ident(v, "<redacted:user>", False)
            elif _is_internal_path(k, v):
                found.append(Secret(v.rstrip("/"), "<redacted:path>"))
    host = socket.gethostname()
    if host and host.casefold() not in LOOPBACK_HOSTS:
        _ident(host, "<redacted:host>", True)
        short = host.split(".", 1)[0]
        if short and short != host:
            _ident(short, "<redacted:host>", True)
    user = getpass.getuser()
    if user and user not in GENERIC_USERS:
        _ident(user, "<redacted:user>", False)
    found.append(Secret(str(Path.home()), "<home>"))
    found.append(Secret(str(_REPO_ROOT), "<repo>"))
    # 실행 인터프리터·venv 경로(저장소 밖에 둔 venv 포함)
    for prefix in (sys.prefix, sys.base_prefix):
        if prefix and prefix.rstrip("/") not in SYSTEM_PATHS:
            found.append(Secret(prefix.rstrip("/"), "<redacted:path>"))
    found.extend(Secret(s, "<redacted>") for s in extra if s and s.strip())
    uniq: dict[tuple[str, bool], Secret] = {}
    for s in found:
        uniq.setdefault((s.value.casefold() if s.ignore_case else s.value, s.ignore_case), s)
    return SecretSet(sorted(uniq.values(), key=lambda s: (-len(s.value), s.value)), skipped)


# ─────────────────────────────── 가림 · 잔존 검사 ───────────────────────────────


def _pattern(s: Secret, value: str | None = None) -> str:
    value = s.value if value is None else value
    body = re.escape(value)
    if re.match(r"\w", value[0]):
        body = r"(?<![A-Za-z0-9_])" + body
    if re.match(r"\w", value[-1]):
        body += r"(?![A-Za-z0-9_])"
    return f"(?i:{body})" if s.ignore_case else body


def _encoded_forms(value: str) -> list[str]:
    """잔존 검사가 더 보는 표기 — 원문 + 퍼센트 인코딩 + JSON ASCII 이스케이프.

    가림은 원문만 바꾼다. 인코딩된 표기로 남은 원값은 가림이 못 잡은 것이라 묶음을 거부한다.
    """
    return list(dict.fromkeys([value, quote(value, safe=""), json.dumps(value)[1:-1]]))


class Masker:
    """원값 목록으로 한 번에 가린다(긴 값 먼저 · 이미 넣은 표식을 다시 가리지 않는다)."""

    def __init__(self, secrets: list[Secret]) -> None:
        self.secrets = secrets
        alts = "|".join(f"(?P<s{i}>{_pattern(s)})" for i, s in enumerate(secrets))
        self._combined = re.compile(alts) if secrets else None
        self.counts: Counter[str] = Counter()

    def mask(self, text: str) -> str:
        """원값 → 표식, 그다음 홈 경로·사설 IP 패턴. 표식별 치환 건수를 `counts`에 더한다."""
        if self._combined is not None:
            secrets, counts = self.secrets, self.counts

            def _sub(m: re.Match[str]) -> str:
                token = secrets[int(str(m.lastgroup)[1:])].token
                counts[token] += 1
                return token

            text = self._combined.sub(_sub, text)
        text, n_home = HOME_RE.subn("<home>", text)
        text, n_ip = PRIVATE_IP_RE.subn("<private-ip>", text)
        self.counts["<home>"] += n_home
        self.counts["<private-ip>"] += n_ip
        return text

    def leaks(self, text: str) -> list[str]:
        """가린 글에 남은 것의 종류(값은 싣지 않는다)."""
        bare = TOKEN_RE.sub(" ", text)
        found = [s.token for s in self.secrets
                 if any(re.search(_pattern(s, f), bare) for f in _encoded_forms(s.value))]
        if PRIVATE_IP_RE.search(bare):
            found.append("사설 IP")
        if HOME_RE.search(bare):
            found.append("홈 경로")
        return list(dict.fromkeys(found))


# ─────────────────────────────── 묶음 ───────────────────────────────


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[Any]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _tail(text: str, n: int = STDERR_TAIL_LINES) -> str:
    lines = text.splitlines()[-n:]
    return "\n".join(lines) + ("\n" if lines else "")


def _sources(run_dir: Path) -> list[tuple[Path, str]]:
    """(묶음 안 상대 경로, 원문) — 싣는 파일만."""
    out: list[tuple[Path, str]] = []
    for name in TOP_FILES:
        p = run_dir / name
        if p.is_file():
            out.append((Path(name), p.read_text(encoding="utf-8")))
    arm_dirs = (d for d in run_dir.iterdir() if d.is_dir() and d.name.startswith(ARM_DIR_PREFIX))
    for arm_dir in sorted(arm_dirs):
        settings = arm_dir / ARM_SETTINGS
        if settings.is_file():
            out.append((Path(arm_dir.name) / ARM_SETTINGS, settings.read_text(encoding="utf-8")))
        stderr = arm_dir / ARM_STDERR
        if stderr.is_file():
            text = stderr.read_text(encoding="utf-8", errors="replace")
            out.append((Path(arm_dir.name) / ARM_STDERR, _tail(text)))
    return out


def export_bundle(run_dir: Path, *, extra_secrets: Iterable[str] = ()) -> Path:
    """`<run_dir>.bundle/`을 만든다. 잔존 검사에 걸리면 `ExportLeakError`(묶음 미생성).

    이미 묶음이 있으면 새 묶음이 검사를 통과한 뒤에만 바꾼다. 반환 = 묶음 경로.
    """
    run_dir = Path(run_dir).resolve()
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest.json 없음: {run_dir}")
    docs: list[Any] = [_read_json(manifest_path)]
    docs += [_read_json(p) for p in sorted(run_dir.glob(f"{ARM_DIR_PREFIX}*/{ARM_SETTINGS}"))]
    gpu = run_dir / "gpu_samples.jsonl"
    if gpu.is_file():
        docs += _read_jsonl(gpu)
    collected = collect_secrets(docs, extra_secrets)
    secrets = collected.secrets
    secrets.append(Secret(str(run_dir), "<run>"))
    secrets.sort(key=lambda s: (-len(s.value), s.value))
    masker = Masker(secrets)

    target = run_dir.with_name(run_dir.name + BUNDLE_SUFFIX)
    tmp = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=run_dir.parent))
    try:
        leaks: list[str] = []
        for rel, text in _sources(run_dir):
            masked = masker.mask(text)
            leaks += [f"{rel}: {kind}" for kind in masker.leaks(masked)]
            dest = tmp / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(masked, encoding="utf-8")
        # 묶음 manifest에 가림 기록(표식별 치환 건수 · 길이 가드로 건너뛴 짧은 계정·호스트명 수)
        bundle_manifest = tmp / "manifest.json"
        doc = json.loads(bundle_manifest.read_text(encoding="utf-8"))
        doc["export"] = {
            "redactions": {k: masker.counts[k] for k in sorted(masker.counts) if masker.counts[k]},
            "skipped_short_identifiers": collected.skipped_short,
            "min_ident_len": MIN_IDENT_LEN,
        }
        text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
        leaks += [f"manifest.json: {kind}" for kind in masker.leaks(text)]
        bundle_manifest.write_text(text, encoding="utf-8")
        if leaks:
            raise ExportLeakError("가림 뒤 잔존 — 묶음을 만들지 않았다: " + "; ".join(leaks))
        if target.exists():
            shutil.rmtree(target)
        tmp.rename(target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return target
