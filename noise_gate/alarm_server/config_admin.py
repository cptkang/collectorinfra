"""컴포넌트 설정 관리 API(`/admin/config`) 공용 모듈 — 정본 (D-345 · D-129 ④ · D-339 ②).

이 파일은 여러 별도 프로세스 패키지에 **바이트 동일 사본**으로 복사된다. 그래서 stdlib와
starlette만 import하고, 컴포넌트마다 다른 것(필드 표·쓰기 대상·차단 판정·override 판정·
dry-run 로더·읽기 전용 항목·기동 명령·버전·감사 경로)은 `ComponentAdapter`로 주입받는다.

담당: `.env` 파싱·줄 보존 편집 · sanitize·타입 검증 · dry-run → `.bak`·임시 파일·`os.replace`
원자 교체 · 감사 JSONL 1줄 · 기동 스냅샷 기반 재기동 대기 판정 · Starlette 관리 앱(라우트 3종) ·
`/admin/config` 접두 HTTP만 관리 앱으로 보내는 경로 분기 ASGI 래퍼.
시크릿 값은 응답·감사로 나가지 않는다 — 오가는 것은 `is_set` 불리언뿐이다.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import stat
import tempfile
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

PREFIX = "/admin/config"
MASK = "********"
MAX_BODY_BYTES = 64 * 1024
FIELD_TYPES = frozenset(
    {"bool", "int", "float", "enum", "json_list", "json_object", "csv", "string", "secret"}
)
_BOOL_WORDS = frozenset({"true", "false", "1", "0", "yes", "no", "on", "off"})
_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
# 줄 경계는 이 셋만 인정한다 — `str.splitlines()`는 \x0b·\x0c·\x1c~\x1e·\x85·U+2028/2029도 끊는다.
_NEWLINE_RE = re.compile(r"\r\n|\n|\r")
# 값에 금지하는 문자: 주석 시작 `#` · 변수 확장 `$` · 명령 치환 백틱(로더마다 해석이 다르다).
_FORBIDDEN_CHARS = frozenset("#$`")
_USERINFO_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]*@")
_AUDIT_VALUE_MAX = 200

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FieldSpec:
    """필드 표 한 줄 — 본체 설정 항목 메타의 같은 이름 부분집합."""

    env_key: str
    group_key: str
    type: str
    default: Any = None
    description: str = ""
    enum_choices: tuple[str, ...] = ()
    is_secret: bool = False


@dataclass
class ComponentAdapter:
    """컴포넌트별로 다른 부분. 콜러블은 요청마다 불린다(파일·환경의 현재 상태 판정).

    `effective_values`는 기동 시 실제 값이다 — 시크릿 키는 값 대신 설정 여부(bool)만 넣는다.
    `override_for(env_key)`는 None · "os" · "encenv" · "dotenv" · "toml"을 돌려준다.
    `dry_run(후보 파일)`은 자기 설정 로더로 읽어 보고 실패 사유 목록을 돌려준다(값 미포함).
    """

    name: str
    version: str
    restart_hint: str
    fields: list[FieldSpec]
    write_target: Path
    audit_path: Path
    dry_run: Callable[[Path], list[str]]
    cwd: Path = field(default_factory=Path.cwd)
    group_titles: Mapping[str, str] = field(default_factory=dict)
    effective_values: Mapping[str, Any] = field(default_factory=dict)
    readonly_keys: frozenset[str] = frozenset()
    override_for: Callable[[str], str | None] = lambda _key: None
    write_block_reason: Callable[[], str | None] = lambda: None
    sources: Callable[[], list[dict[str, Any]]] = lambda: []


def parse_env_lines(lines: list[str]) -> dict[str, list[tuple[int, str]]]:
    """활성 `KEY=값` 줄을 키별 (줄 번호, 값) 목록으로 모은다(주석·빈 줄 제외 · 감싼 따옴표 제거)."""
    found: dict[str, list[tuple[int, str]]] = {}
    for index, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        match = _LINE_RE.match(line)
        if match is None:
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        found.setdefault(match.group(1), []).append((index, value))
    return found


def split_env_lines(text: str) -> list[str]:
    """`\r\n`·`\n`·`\r`만 줄 경계로 나눈다(마지막 개행 뒤 빈 조각은 버린다)."""
    lines = _NEWLINE_RE.split(text)
    return lines[:-1] if lines and lines[-1] == "" else lines


def read_env_lines(path: Path) -> list[str]:
    """파일 줄 목록. 파일이 없으면 빈 목록."""
    return split_env_lines(path.read_text(encoding="utf-8")) if path.is_file() else []


def read_env_values(path: Path) -> dict[str, str]:
    """파일의 키별 값(같은 키가 여럿이면 마지막 줄). 파일이 없으면 빈 dict."""
    parsed = parse_env_lines(read_env_lines(path))
    return {key: hits[-1][1] for key, hits in parsed.items()}


def render_env(lines: list[str], updates: Mapping[str, str], resets: set[str]) -> str:
    """기존 줄은 제자리 교체(주석·순서 보존), 새 키는 끝에 추가, reset 키는 활성 줄 삭제."""
    parsed = parse_env_lines(lines)
    replace = {hits[0][0]: key for key, hits in parsed.items() if key in updates}
    drop = {index for key in resets for index, _ in parsed.get(key, [])}
    out: list[str] = []
    for index, line in enumerate(lines):
        if index in drop:
            continue
        out.append(f"{replace[index]}={updates[replace[index]]}" if index in replace else line)
    out.extend(f"{key}={value}" for key, value in updates.items() if key not in parsed)
    return "\n".join(out) + "\n" if out else ""


def _to_text(value: Any) -> str | None:
    """요청 값을 파일 문자열로 바꾼다(지원하지 않는 형이면 None)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return str(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return None


def env_text(value: Any) -> str | None:
    """값을 `.env` 표기 문자열로 정규화한다(None 유지 · bool → true/false · list/dict → JSON)."""
    if value is None:
        return None
    text = _to_text(value)
    return str(value) if text is None else text


def validate_value(spec: FieldSpec, text: str) -> str | None:
    """sanitize + 타입 검증. 실패 사유(값 미포함) 또는 None."""
    for ch in text:
        if (
            ch in _FORBIDDEN_CHARS
            or unicodedata.category(ch)[0] == "C"
            or (unicodedata.category(ch) in ("Zl", "Zp"))
        ):
            return "제어·줄 구분 문자와 # $ ` 는 쓸 수 없다"
    if text != text.strip() or text[:1] in ("'", '"') or text[-1:] in ("'", '"'):
        return "앞뒤 공백·따옴표는 쓸 수 없다"
    try:
        if spec.type == "bool" and text.lower() not in _BOOL_WORDS:
            return "bool 값이 아니다"
        if spec.type == "int":
            int(text)
        elif spec.type == "float":
            float(text)
        elif spec.type == "enum" and text not in spec.enum_choices:
            return f"허용 값이 아니다: {list(spec.enum_choices)}"
        elif spec.type == "json_list" and not isinstance(json.loads(text), list):
            return "JSON 배열이 아니다"
        elif spec.type == "json_object" and not isinstance(json.loads(text), dict):
            return "JSON 객체가 아니다"
    except ValueError:
        return f"{spec.type} 형식이 아니다"
    return None


def atomic_write(target: Path, content: str) -> None:
    """원본을 `<파일>.bak`으로 복사 → 같은 디렉토리 임시 파일 · fsync · 권한 유지 · `os.replace`."""
    mode = 0o600
    if target.exists():
        mode = stat.S_IMODE(target.stat().st_mode)
        original = target.read_bytes()
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        # 기존 `.bak`이 심볼릭 링크면 O_NOFOLLOW가 거부한다(링크 너머 파일 덮어쓰기 방지).
        with os.fdopen(os.open(target.with_name(target.name + ".bak"), flags, 0o600), "wb") as bak:
            bak.write(original)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _audit_value(value: str | None) -> str | None:
    """감사용 값 — URL userinfo(`scheme://user:pw@`)를 가리고 200자로 자른다."""
    if value is None:
        return None
    return _USERINFO_RE.sub(r"\1***@", value)[:_AUDIT_VALUE_MAX]


def _audit(
    adapter: ComponentAdapter, request_id: str | None, result: str, changes: list[dict[str, Any]]
) -> bool:
    """감사 JSONL 1줄(시크릿 키는 애초에 저장 대상이 아니다). 쓰기 실패면 경고 후 False."""
    record = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "request_id": request_id,
        "component": adapter.name,
        "result": result,
        "changes": [
            {"key": c["key"], "old": _audit_value(c["old"]), "new": _audit_value(c["new"])}
            for c in changes
        ],
    }
    try:
        adapter.audit_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        with os.fdopen(os.open(adapter.audit_path, flags, 0o600), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("설정 관리 감사 기록 실패(result=%s): %s", result, type(exc).__name__)
        return False
    return True


class ConfigAdminService:
    """관리 앱의 판정·저장 로직. 생성 시점(=기동 시)의 쓰기 대상 파일을 스냅샷으로 잡는다."""

    def __init__(self, adapter: ComponentAdapter) -> None:
        self.adapter = adapter
        self.specs = {spec.env_key: spec for spec in adapter.fields}
        self.snapshot = read_env_values(adapter.write_target)
        self.component = {
            "name": adapter.name,
            "version": adapter.version,
            "pid": os.getpid(),
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "restart_hint": adapter.restart_hint,
        }

    def ignore_reason(self, spec: FieldSpec) -> str | None:
        """항목 단위 쓰기 불가 사유 어휘(secret · readonly · override_*) 또는 None."""
        if spec.is_secret:
            return "secret"
        if spec.env_key in self.adapter.readonly_keys:
            return "readonly"
        override = self.adapter.override_for(spec.env_key)
        if override == "toml":
            return "readonly"
        return f"override_{override}" if override else None

    def pending_keys(self) -> list[str]:
        """파일 값이 기동 스냅샷과 **값으로** 다른 키(override 걸린 키 제외)."""
        current = read_env_values(self.adapter.write_target)
        return [
            key
            for key in self.specs
            if current.get(key) != self.snapshot.get(key) and not self.adapter.override_for(key)
        ]

    def status(self) -> dict[str, Any]:
        """GET /admin/config/status 응답."""
        reason = self.adapter.write_block_reason()
        return {
            "component": self.component,
            "writable": reason is None,
            "write_block_reason": reason,
            "restart_pending_keys": self.pending_keys(),
        }

    def schema(self) -> dict[str, Any]:
        """GET /admin/config/schema 응답."""
        body = self.status()
        parsed = parse_env_lines(self._lines())
        file_values = {key: hits[-1][1] for key, hits in parsed.items()}
        duplicates = [key for key, hits in parsed.items() if len(hits) > 1]
        pending = set(body["restart_pending_keys"])
        groups: dict[str, dict[str, Any]] = {}
        for spec in self.adapter.fields:
            reason = self.ignore_reason(spec)
            if reason is None and not body["writable"]:
                reason = "write_blocked"
            effective = self.adapter.effective_values.get(spec.env_key)
            if spec.is_secret:
                is_set = bool(effective)
            else:
                empty = effective is None or effective in ("", [], {})
                is_set = spec.env_key in file_values or not empty
            item = {
                "env_key": spec.env_key,
                "group_key": spec.group_key,
                "type": spec.type,
                "enum_choices": list(spec.enum_choices),
                "default": None if spec.is_secret else env_text(spec.default),
                "description": spec.description,
                "file_value": None if spec.is_secret else file_values.get(spec.env_key),
                "effective_value": None if spec.is_secret else env_text(effective),
                "override": self.adapter.override_for(spec.env_key),
                "is_secret": spec.is_secret,
                "is_set": is_set,
                "writable": reason is None,
                "readonly_reason": reason,
                "apply_mode": "restart",
                "requires_restart": True,
                "restart_pending": spec.env_key in pending,
            }
            title = self.adapter.group_titles.get(spec.group_key, spec.group_key)
            group = groups.setdefault(
                spec.group_key, {"group_key": spec.group_key, "title": title, "settings": []}
            )
            group["settings"].append(item)
        body.update(
            cwd=str(self.adapter.cwd),
            sources=self.adapter.sources(),
            groups=list(groups.values()),
            warnings=[f"중복 활성 줄: {key}" for key in duplicates],
        )
        return body

    def _lines(self) -> list[str]:
        return read_env_lines(self.adapter.write_target)

    def update(self, payload: Any, request_id: str | None) -> tuple[int, dict[str, Any]]:
        """PUT /admin/config — 전부 통과하거나 파일 불변. (상태 코드, 응답 본문)."""
        reason = self.adapter.write_block_reason()
        if reason is not None:
            _audit(self.adapter, request_id, "rejected", [])
            return 409, {"error": "write_blocked", "reason": reason}
        errors: list[dict[str, str]] = []
        ignored: list[dict[str, str]] = []
        updates: dict[str, str] = {}
        resets: set[str] = set()
        if not isinstance(payload, dict):
            errors.append({"key": "", "message": "본문은 JSON 객체여야 한다"})
            payload = {}
        raw_settings = payload.get("settings") or {}
        raw_resets = payload.get("reset_keys") or []
        dry_run = payload.get("dry_run", False)
        if not isinstance(dry_run, bool):
            errors.append({"key": "", "message": "dry_run은 true/false여야 한다"})
            dry_run = False
        if not isinstance(raw_settings, dict) or not isinstance(raw_resets, list):
            errors.append({"key": "", "message": "settings는 객체, reset_keys는 배열이어야 한다"})
            raw_settings, raw_resets = {}, []
        lines = self._lines()
        parsed = parse_env_lines(lines)
        requests = [(k, v, False) for k, v in raw_settings.items()]
        requests += [(k, None, True) for k in raw_resets]
        for key, value, is_reset in requests:
            spec = self.specs.get(key) if isinstance(key, str) else None
            if spec is None:
                errors.append({"key": str(key), "message": "등록되지 않은 키"})
                continue
            reason = self.ignore_reason(spec)
            if reason is None and not is_reset and value == MASK:
                reason = "masked"
            if reason is not None:
                ignored.append({"key": key, "reason": reason})
                continue
            if len(parsed.get(key, [])) > 1:
                errors.append(
                    {"key": key, "message": "쓰기 대상 파일에 같은 키 활성 줄이 2개 이상"}
                )
                continue
            if is_reset:
                if key in updates:
                    errors.append({"key": key, "message": "settings와 reset_keys에 함께 있다"})
                resets.add(key)
                continue
            text = _to_text(value)
            problem = "지원하지 않는 값 형식" if text is None else validate_value(spec, text)
            if text is None or problem is not None:
                errors.append({"key": key, "message": problem or ""})
                continue
            updates[key] = text
        old = {key: hits[-1][1] for key, hits in parsed.items()}
        changes = [{"key": k, "old": old.get(k), "new": v} for k, v in updates.items()]
        changes += [{"key": k, "old": old.get(k), "new": None} for k in sorted(resets)]
        candidate = render_env(lines, updates, resets)
        if not errors and (updates or resets):
            if not _candidate_matches(parsed, candidate, updates, resets):
                errors.append({"key": "", "message": "후보 파일 재파싱 결과가 요청과 다르다"})
            else:
                errors.extend({"key": "", "message": msg} for msg in self._dry_run(candidate))
        body: dict[str, Any] = {
            "saved_keys": [],
            "ignored_keys": ignored,
            "errors": errors,
            "dry_run": dry_run,
            "restart_required": False,
            "restart_pending_keys": self.pending_keys(),
            "audit_logged": True,
        }
        if errors:
            body["audit_logged"] = _audit(self.adapter, request_id, "rejected", changes)
            return 400, body
        if dry_run:
            body["audit_logged"] = _audit(self.adapter, request_id, "dry_run", changes)
            return 200, body
        if updates or resets:
            try:
                atomic_write(self.adapter.write_target, candidate)
            except OSError as exc:
                errors.append({"key": "", "message": f"저장 실패: {type(exc).__name__}"})
                body["audit_logged"] = _audit(self.adapter, request_id, "rejected", changes)
                return 500, body
            body["audit_logged"] = _audit(self.adapter, request_id, "saved", changes)
        body["saved_keys"] = [*updates, *sorted(resets)]
        body["restart_required"] = bool(body["saved_keys"])
        body["restart_pending_keys"] = self.pending_keys()
        return 200, body

    def _dry_run(self, candidate: str) -> list[str]:
        """후보 내용을 임시 파일에 쓰고 컴포넌트 자기 로더로 읽어 본다."""
        fd, tmp = tempfile.mkstemp(prefix="config_admin_", suffix=".env")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(candidate)
            return self.adapter.dry_run(Path(tmp))
        finally:
            Path(tmp).unlink(missing_ok=True)


def _candidate_matches(
    before: dict[str, list[tuple[int, str]]],
    candidate: str,
    updates: Mapping[str, str],
    resets: set[str],
) -> bool:
    """방어 심화 — 후보를 다시 파싱해 요청한 키 밖의 키가 생기거나 값이 바뀌지 않았는지 본다."""
    after = {
        k: [v for _, v in hits] for k, hits in parse_env_lines(split_env_lines(candidate)).items()
    }
    expected = {k: [v for _, v in hits] for k, hits in before.items() if k not in resets}
    for key, value in updates.items():
        expected[key] = [value]
    return after == expected


async def _read_body(request: Request) -> bytes | None:
    """본문을 상한까지만 읽는다(초과면 None)."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def build_admin_app(adapter: ComponentAdapter, admin_token: str) -> Starlette:
    """관리 앱(라우트 3종). 모든 요청에 `Authorization: Bearer <관리 토큰>`을 상수 시간 비교한다."""
    service = ConfigAdminService(adapter)
    expected = f"Bearer {admin_token}".encode()

    def authorized(request: Request) -> bool:
        provided = request.headers.get("authorization", "").encode("latin-1", "replace")
        return hmac.compare_digest(provided, expected)

    unauthorized = {"error": "unauthorized"}

    async def schema(request: Request) -> JSONResponse:
        if not authorized(request):
            return JSONResponse(unauthorized, status_code=401)
        return JSONResponse(service.schema())

    async def status(request: Request) -> JSONResponse:
        if not authorized(request):
            return JSONResponse(unauthorized, status_code=401)
        return JSONResponse(service.status())

    async def update(request: Request) -> JSONResponse:
        if not authorized(request):
            return JSONResponse(unauthorized, status_code=401)
        raw = await _read_body(request)
        if raw is None:
            return JSONResponse({"error": "payload_too_large"}, status_code=413)
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            payload = None
        code, body = service.update(payload, request.headers.get("x-request-id"))
        return JSONResponse(body, status_code=code)

    return Starlette(
        routes=[
            Route(f"{PREFIX}/schema", schema, methods=["GET"]),
            Route(f"{PREFIX}/status", status, methods=["GET"]),
            Route(PREFIX, update, methods=["PUT"]),
        ]
    )


class AdminPathRouter:
    """`/admin/config` 접두 HTTP 요청만 관리 앱으로, 나머지(비 HTTP 포함)는 기존 앱으로 보낸다.

    scope는 고치지 않는다. 기존 앱의 미들웨어(서비스 Bearer 등)는 관리 경로에 걸리지 않는다.
    """

    def __init__(self, app: ASGIApp, admin_app: ASGIApp) -> None:
        self.app = app
        self.admin_app = admin_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if path == PREFIX or path.startswith(PREFIX + "/"):
            await self.admin_app(scope, receive, send)
            return
        await self.app(scope, receive, send)
