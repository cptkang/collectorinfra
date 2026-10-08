#!/usr/bin/env python3
"""fabrix_proxy 1단계 PoC 시나리오 러너 — 내부망 실 FabriX 판정용 (plans/148 W3b).

하위 명령::

    python scripts/poc_run.py env-check [--proxy-url URL] [--out DIR] [--dry-run]
    python scripts/poc_run.py run [--only S1,S4,…] [--repeat N | --repeat S1=10,S4=10] \
        [--contents-mode turns|transcript] [--protocol-lang en|ko] [--protocol-file PATH] \
        [--fewshot none|static|dynamic] [--fewshot-placement system|contents] \
        [--fewshot-file PATH] [--repair-max N] [--passthrough] [--concurrency N] [--sleep SEC] \
        [--out DIR] [--proxy-url URL] [--dry-run]

- 설정(프록시 토큰 · FabriX 접속값)은 `ProxySettings`(`fabrix_proxy/.env`·`.encenv` + OS 환경변수)로
  읽는다. 토큰을 CLI 인자로 받지 않는다.
- 측정 선택지는 요청마다 `fabrix_proxy_options`로 보낸다(프록시 `FABRIX_PROXY_POC_MODE=true` 필요).
  파일 옵션은 러너가 읽어 인라인(`protocol_text`·`fewshot_examples`)으로 싣는다.
- 결과(`--out`, 기본 `<repo>/logs/fabrix_proxy_poc/<YYYYmmdd-HHMMSS>/`):
  `results.jsonl`(호출/케이스별 판정값) · `summary.md` · `recommend.env` · `env_check.md` —
  같은 `--out`을 다시 주면 결과를 덧붙이고 summary·recommend를 디렉터리 전체로 다시 만든다.
  `failures/`(실패 건 원출력 앞 500자)는 내부망 보관용이며 **반출하지 않는다**.
- 반출물(`summary.md`·`results.jsonl`·`env_check.md` 등)은 쓸 때마다 자기 검사한다 — URL ·
  인증 헤더 표지 · 설정 자격증명 값 · 업스트림 호스트 · 시나리오 본문 표지(`leak_markers`) ·
  응답 원문이 걸리면 그 파일을 쓰지 않고(지우고) 종료 코드 3으로 끝낸다(걸린 규칙만 출력).
- `--dry-run`은 가짜 KBGenAI(`testdata/scenarios/poc_dryrun_script.json`) + 프록시(POC_MODE ·
  임시 토큰 · 빈 포트)를 하위 프로세스로 띄워 같은 흐름을 돈다(실 LLM 0건). `run --dry-run`에
  `--only`가 없으면 전체 순서(env-check → F1 → F2 → ⑤a → ⑤b → ⑥)를 한 디렉터리에 돈다.

종료 코드: 0 정상 · 1 실행 실패(프록시 미기동·POC_MODE 아님 · 프록시가 측정 선택지를 400
`invalid_request`로 거부 등) · 2 사용법·설정 오류 · 3 반출물 자기 검사 실패.

경계: `src`·`noise_gate`·`sre_agent`·`mcp_server`·`apm_gateway`를 import하지 않는다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import secrets
import select
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

SCRIPT_DIR = Path(__file__).resolve().parent
PROXY_ROOT = SCRIPT_DIR.parent  # fabrix_proxy/
REPO_ROOT = PROXY_ROOT.parent
for _path in (PROXY_ROOT, SCRIPT_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
if __name__ == "__main__":
    # probe_fabrix가 `import poc_run`할 때 이 모듈을 다시 싣지 않게 한다.
    sys.modules.setdefault("poc_run", sys.modules[__name__])

from fabrix_proxy.config import ProxySettings, resolve_path  # noqa: E402
from fabrix_proxy.convert import PreparedRequest, build_llm_config, build_payload  # noqa: E402
from fabrix_proxy.fabrix_client import (  # noqa: E402
    ContentFilterError,
    KBGenAIUpstream,
    UpstreamError,
)
from pydantic import ValidationError  # noqa: E402

from fabrix_proxy import tool_protocol as tp  # noqa: E402

SCENARIO_DIR = PROXY_ROOT / "testdata" / "scenarios"
DRYRUN_SCRIPT = SCENARIO_DIR / "poc_dryrun_script.json"
FAKE_SCRIPT = SCRIPT_DIR / "fake_kbgenai.py"
RECORD_SCRIPT = SCRIPT_DIR / "record_consumer_requests.py"
BASELINE_FIELDS = PROXY_ROOT / "testdata" / "consumer_requests" / "fields_summary.json"
POC_VERSION_FILE = PROXY_ROOT / "POC_VERSION"
DEFAULT_OUT_ROOT = REPO_ROOT / "logs" / "fabrix_proxy_poc"
DEFAULT_PROXY_URL = "http://127.0.0.1:9095"

RESULTS = "results.jsonl"
SUMMARY = "summary.md"
ENV_CHECK = "env_check.md"
RECOMMEND = "recommend.env"
PROBE_NATIVE = "probe_native.json"
PROBE_CONTENTS = "probe_contents.json"
FAILURES = "failures"

DIAG_FIELD = "fabrix_proxy_diag"
OPTIONS_FIELD = "fabrix_proxy_options"
RAW_HEAD_CHARS = 500

SCENARIOS = ("S1", "S2", "S3", "S4", "S5", "S6", "F5")
DEFAULT_REPEAT = {"S1": 20, "S2": 20, "S3": 10, "S4": 20, "S5": 10, "S6": 10, "F5": 3}
SCENARIO_FILES = {
    "S1": "poc_s1_single.json",
    "S2": "poc_s2_parallel.json",
    "S3": "poc_s3_choice.json",
    "S4": "poc_s4_no_tool.json",
    "S5": "poc_s5_react.json",
    "S6": "poc_s6_deepagents.json",
    "F5": "poc_f5_usage_pii.json",
}
FEWSHOT_MODES = ("none", "static", "dynamic")
LANGS = ("en", "ko")
DEEPAGENTS_BUILTINS = frozenset(
    {"write_todos", "ls", "read_file", "write_file", "edit_file", "glob", "grep", "task"}
)
VERSION_DISTS = (
    "langchain-core", "langchain-openai", "openai", "deepagents", "fastapi", "uvicorn",
    "httpx", "jsonschema", "pydantic", "pydantic-settings", "pytest",
)
# env-check 녹화 대조 제외 — 본체 도구 덤프·트랙 B는 개발 맥 전용(--include-body-tools)
FIELD_COMPARE_EXCLUDE = ("noise_track_b_bind_tools", "body_tools_")

# 합격선(판정 초안 — 참고용)
PASS_FORMAT = 0.95
PASS_SELECT = 0.90
PASS_FALSE_POSITIVE = 0.05
PASS_COMPLETE = 0.90
PASS_REFLECT = 0.90
PASS_REPAIR_DEP = 0.15
FEWSHOT_LOW = 0.90
TIE_MARGIN = 0.02 + 1e-9

FAILURE_LABELS = {
    "format": "형식",
    "tool_selection": "도구 선택",
    "history": "이력 해석",
    "limit_block": "한도·차단",
}
_URL_RE = re.compile(r"https?://", re.I)
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
# 400 invalid_request 메시지에서 뽑아 출력해도 되는 식별자(옵션·필드·설정 키 이름) — 그 밖은 버린다
INVALID_REQUEST_HINTS = frozenset({
    "contents_mode", "protocol_lang", "protocol_text", "fewshot", "fewshot_placement",
    "fewshot_examples", "repair_max", "passthrough", "FABRIX_NATIVE_URL", OPTIONS_FIELD,
    "model", "messages", "tools", "tool_choice", "parallel_tool_calls", "stream",
    "temperature", "top_p", "Content-Length",
})
_HINT_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")


class UsageError(Exception):
    """사용법·설정 오류(종료 코드 2). 메시지에 자격증명 값을 싣지 않는다."""


class RunError(Exception):
    """실행 실패(종료 코드 1) — 프록시 미기동·POC_MODE 아님 등."""


class ExportCheckError(Exception):
    """반출물 자기 검사 실패(종료 코드 3). 걸린 규칙 이름만 싣는다."""

    def __init__(self, path: Path, rules: list[str]) -> None:
        super().__init__(f"{path.name}: {', '.join(rules)}")
        self.path = path
        self.rules = rules


# ─────────────────────────────── 반출물 자기 검사 ───────────────────────────────


def _secret_needle(value: str) -> str | None:
    """자격증명 값의 검사 조각 — 6자 미만은 건너뛰고, 앞 8자(6~7자면 전체)를 쓴다."""
    value = value.strip()
    if len(value) < 6:
        return None
    return value[:8]


@dataclass
class ExportGuard:
    """반출 파일 쓰기 전 검사기. 걸리면 파일을 쓰지 않고(지우고) `ExportCheckError`."""

    secret_needles: dict[str, str] = field(default_factory=dict)
    secret_values: dict[str, str] = field(default_factory=dict)
    hosts: dict[str, str] = field(default_factory=dict)
    markers: list[str] = field(default_factory=list)
    responses: set[str] = field(default_factory=set)
    tripped: bool = False

    @classmethod
    def build(cls, settings_list: Iterable[ProxySettings], markers: Iterable[str]) -> ExportGuard:
        guard = cls(markers=sorted({m for m in markers if m}))
        for settings in settings_list:
            for name, value in (
                ("FABRIX_API_KEY", settings.fabrix_api_key),
                ("FABRIX_CLIENT_KEY", settings.fabrix_client_key),
                ("FABRIX_PROXY_TOKEN", settings.fabrix_proxy_token),
            ):
                needle = _secret_needle(value)
                if needle:
                    key = f"{name}#{len(guard.secret_needles)}"
                    guard.secret_needles[key] = needle
                    guard.secret_values[key] = value.strip()
            for name, url in (
                ("FABRIX_BASE_URL", settings.fabrix_base_url),
                ("FABRIX_NATIVE_URL", settings.fabrix_native_url),
            ):
                host = urlparse(url).hostname if url else None
                if host:
                    guard.hosts[f"{name}#{len(guard.hosts)}"] = host
        return guard

    def note_response(self, text: str | None) -> None:
        """응답 원문 조각을 기억한다(16자 이상만 · 앞 32자)."""
        if not text:
            return
        clean = text.strip()
        if len(clean) >= 16:
            self.responses.add(clean[:32])

    def violations(self, text: str) -> list[str]:
        """걸린 규칙 이름 목록(값은 싣지 않는다)."""
        rules: list[str] = []
        if _URL_RE.search(text):
            rules.append("url")
        if "bearer" in text.lower():
            rules.append("auth_header")
        for name, needle in self.secret_needles.items():
            # 앞 8자 조각 + 전체 값 — 둘 중 하나라도 걸리면 같은 규칙
            if needle in text or self.secret_values.get(name, needle) in text:
                rules.append("secret:" + name.split("#", 1)[0])
        for name, host in self.hosts.items():
            if host in text:
                rules.append("host:" + name.split("#", 1)[0])
        if any(marker in text for marker in self.markers):
            rules.append("leak_marker")
        if any(snippet in text for snippet in self.responses):
            rules.append("response_text")
        return list(dict.fromkeys(rules))

    def _refuse(self, path: Path, rules: list[str]) -> None:
        self.tripped = True
        path.unlink(missing_ok=True)
        raise ExportCheckError(path, rules)

    def write(self, path: Path, text: str) -> None:
        """파일 전체를 검사 후 쓴다."""
        if self.tripped:
            raise ExportCheckError(path, ["guard_tripped"])
        rules = self.violations(text)
        if rules:
            self._refuse(path, rules)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def append(self, path: Path, text: str) -> None:
        """한 줄을 검사 후 덧붙인다. 걸리면 파일 전체를 지운다."""
        if self.tripped:
            raise ExportCheckError(path, ["guard_tripped"])
        rules = self.violations(text)
        if rules:
            self._refuse(path, rules)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(text)


def load_leak_markers() -> list[str]:
    """시나리오·대본 파일의 `leak_markers`를 모은다."""
    markers: list[str] = []
    for path in sorted(SCENARIO_DIR.glob("poc_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        markers += [str(m) for m in data.get("leak_markers") or []]
    return markers


def build_guard(settings_list: Iterable[ProxySettings]) -> ExportGuard:
    """설정 자격증명·호스트와 시나리오 표지로 검사기를 만든다."""
    return ExportGuard.build(settings_list, load_leak_markers())


# ─────────────────────────────── 설정 · 시나리오 ───────────────────────────────


def load_settings() -> ProxySettings:
    """`ProxySettings`를 읽는다. 검증 실패는 필드 이름만 담은 `UsageError`."""
    try:
        return ProxySettings()
    except ValidationError as exc:
        fields = ", ".join(
            ".".join(str(p) for p in err["loc"]) for err in exc.errors(include_input=False)
        )
        raise UsageError(f"fabrix_proxy 설정 오류: {fields}") from exc


def poc_version() -> str:
    """`fabrix_proxy/POC_VERSION` 첫 줄 — 없으면 unknown."""
    try:
        lines = POC_VERSION_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "unknown"
    return lines[0].strip() if lines and lines[0].strip() else "unknown"


def load_tools() -> dict[str, dict[str, Any]]:
    """공용 합성 도구 정의(이름 → OpenAI 도구 JSON)."""
    data = json.loads((SCENARIO_DIR / "poc_tools.json").read_text(encoding="utf-8"))
    tools: dict[str, dict[str, Any]] = data["tools"]
    return tools


def load_scenarios() -> dict[str, dict[str, Any]]:
    """S1~S6·F5 시나리오를 읽고 `tool_defs`(도구 정의 목록)를 붙인다."""
    tools = load_tools()
    scenarios: dict[str, dict[str, Any]] = {}
    for key, name in SCENARIO_FILES.items():
        data = json.loads((SCENARIO_DIR / name).read_text(encoding="utf-8"))
        data["tool_defs"] = [tools[n] for n in data["tools"]]
        scenarios[key] = data
    return scenarios


def _known_names() -> tuple[frozenset[str], frozenset[str]]:
    """결과에 남겨도 되는 도구 이름·인자 이름(그 밖은 모델 출력 조각이므로 가린다)."""
    tools = load_tools()
    names = set(tools) | set(DEEPAGENTS_BUILTINS)
    args: set[str] = {"todos"}
    for tool in tools.values():
        args |= set((tool["function"].get("parameters") or {}).get("properties") or {})
    for name in tp.tool_names(tp.load_fewshot()["tools"]):
        names |= {name, name + tp.SAMPLE_SUFFIX}
    return frozenset(names), frozenset(args)


KNOWN_TOOL_NAMES, KNOWN_ARG_NAMES = _known_names()


def safe_name(name: Any) -> str:
    """도구 이름 — 알려진 합성 도구·예시 도구·deepagents 내장만 그대로, 나머지는 `<unknown>`."""
    return str(name) if str(name) in KNOWN_TOOL_NAMES else "<unknown>"


def safe_reason(code: Any) -> str:
    """사유 코드 — `schema_error:<인자>`의 인자가 모르는 이름이면 `<other>`로 가린다."""
    text = str(code)
    if text.startswith("schema_error:"):
        arg = text.split(":", 1)[1]
        return "schema_error:" + (arg if arg in KNOWN_ARG_NAMES or arg == "*" else "<other>")
    return text if _CODE_RE.match(text) else "<other>"


def safe_code(code: Any) -> str | None:
    """오류 코드 — 소문자 식별자만 그대로."""
    if code is None:
        return None
    text = str(code)
    return text if _CODE_RE.match(text) else "other"


# ─────────────────────────────── 측정 선택지 ───────────────────────────────


@dataclass(frozen=True)
class RunOptions:
    """한 run 라벨의 측정 선택지. 파일 옵션은 내용(인라인)과 파일 이름만 든다."""

    contents_mode: str
    protocol_lang: str
    fewshot: str
    fewshot_placement: str
    repair_max: int
    passthrough: bool
    protocol_text: str | None = None
    protocol_file: str | None = None
    fewshot_examples: dict[str, Any] | None = None
    fewshot_file: str | None = None

    def label(self) -> str:
        """run 라벨 — 옵션 조합 문자열."""
        parts = [
            f"contents={self.contents_mode}",
            f"fewshot={self.fewshot}",
            f"placement={self.fewshot_placement}",
            f"lang={self.protocol_lang}",
            f"repair={self.repair_max}",
            f"passthrough={str(self.passthrough).lower()}",
        ]
        if self.protocol_file:
            parts.append(f"protocol_file={self.protocol_file}")
        if self.fewshot_file:
            parts.append(f"fewshot_file={self.fewshot_file}")
        return ",".join(parts)

    def proxy_options(self) -> dict[str, Any]:
        """요청 바디의 `fabrix_proxy_options`."""
        options: dict[str, Any] = {
            "contents_mode": self.contents_mode,
            "protocol_lang": self.protocol_lang,
            "fewshot": self.fewshot,
            "fewshot_placement": self.fewshot_placement,
            "repair_max": self.repair_max,
            "passthrough": self.passthrough,
        }
        if self.protocol_text is not None:
            options["protocol_text"] = self.protocol_text
        if self.fewshot_examples is not None:
            options["fewshot_examples"] = self.fewshot_examples
        return options

    def as_record(self) -> dict[str, Any]:
        """결과 줄에 남길 옵션(본문 없이 이름만)."""
        return {
            "contents_mode": self.contents_mode,
            "protocol_lang": self.protocol_lang,
            "fewshot": self.fewshot,
            "fewshot_placement": self.fewshot_placement,
            "repair_max": self.repair_max,
            "passthrough": self.passthrough,
            "protocol_file": self.protocol_file,
            "fewshot_file": self.fewshot_file,
        }


def resolve_run_options(args: argparse.Namespace, settings: ProxySettings) -> RunOptions:
    """CLI 값(없으면 설정 기본값)으로 선택지를 만든다. 파일은 읽어 검증한다(실패 → UsageError)."""
    lang = args.protocol_lang or settings.fabrix_proxy_protocol_lang
    protocol_path = args.protocol_file or (
        resolve_path(settings.fabrix_proxy_protocol_file)
        if settings.fabrix_proxy_protocol_file
        else None
    )
    fewshot_path = args.fewshot_file or (
        resolve_path(settings.fabrix_proxy_fewshot_file)
        if settings.fabrix_proxy_fewshot_file
        else None
    )
    protocol_text = None
    if protocol_path:
        try:
            protocol_text = tp.load_protocol_template(lang, Path(protocol_path))
        except (OSError, ValueError) as exc:
            raise UsageError(f"--protocol-file 검증 실패: {type(exc).__name__}: {exc}") from exc
    fewshot_examples = None
    if fewshot_path:
        try:
            fewshot_examples = tp.load_fewshot(Path(fewshot_path))
        except (OSError, ValueError) as exc:
            raise UsageError(f"--fewshot-file 검증 실패: {type(exc).__name__}: {exc}") from exc
    repair = settings.fabrix_proxy_repair_max if args.repair_max is None else args.repair_max
    if repair < 0:
        raise UsageError("--repair-max는 0 이상이어야 한다")
    return RunOptions(
        contents_mode=args.contents_mode or settings.fabrix_proxy_contents_mode,
        protocol_lang=lang,
        fewshot=args.fewshot or settings.fabrix_proxy_fewshot,
        fewshot_placement=args.fewshot_placement or settings.fabrix_proxy_fewshot_placement,
        repair_max=repair,
        passthrough=bool(args.passthrough or settings.fabrix_proxy_passthrough),
        protocol_text=protocol_text,
        protocol_file=Path(protocol_path).name if protocol_path else None,
        fewshot_examples=fewshot_examples,
        fewshot_file=Path(fewshot_path).name if fewshot_path else None,
    )


def parse_repeat(raw: str | None) -> dict[str, int]:
    """`--repeat N`(전 시나리오) 또는 `--repeat S1=10,S4=10`(지정분만 · 나머지 기본)."""
    repeat = dict(DEFAULT_REPEAT)
    if raw is None or not raw.strip():
        return repeat
    text = raw.strip()
    if text.isdigit():
        n = int(text)
        if n < 1:
            raise UsageError("--repeat는 1 이상이어야 한다")
        return dict.fromkeys(repeat, n)
    for part in text.split(","):
        key, sep, value = part.partition("=")
        key = key.strip().upper()
        if not sep or key not in repeat or not value.strip().isdigit() or int(value) < 1:
            raise UsageError("--repeat 형식: N 또는 S1=10,S4=10 (시나리오 S1~S6·F5, 값 ≥1)")
        repeat[key] = int(value)
    return repeat


def parse_only(raw: str | None) -> list[str] | None:
    """`--only S1,S4` → 시나리오 목록. 없으면 None(전부)."""
    if raw is None or not raw.strip():
        return None
    picked = [s.strip().upper() for s in raw.split(",") if s.strip()]
    unknown = [s for s in picked if s not in SCENARIOS]
    if unknown:
        raise UsageError(f"--only: 모르는 시나리오 {unknown} (S1~S6·F5)")
    return list(dict.fromkeys(picked))


def resolve_out(raw: str | None) -> Path:
    """결과 디렉터리 — 기본 `<repo>/logs/fabrix_proxy_poc/<YYYYmmdd-HHMMSS>/`."""
    out = Path(raw).expanduser() if raw else DEFAULT_OUT_ROOT / datetime.now().strftime(
        "%Y%m%d-%H%M%S"
    )
    out.mkdir(parents=True, exist_ok=True)
    return out.resolve()


# ─────────────────────────────── 드라이런 스택 ───────────────────────────────


@dataclass
class Stack:
    """측정 대상 접속 정보(드라이런이면 하위 프로세스 묶음)."""

    proxy_url: str
    token: str
    settings: ProxySettings
    procs: list[subprocess.Popen[Any]] = field(default_factory=list)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _child_env(extra: dict[str, str]) -> dict[str, str]:
    """부모 환경의 `FABRIX_*`를 걷어 내고(누수 방지) 지정 값만 싣는다."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("FABRIX_")}
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost"
    env.update(extra)
    return env


def _stop(proc: subprocess.Popen[Any]) -> None:
    """자기가 띄운 PID만 종료한다."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _read_ready(proc: subprocess.Popen[str], timeout: float = 30.0) -> int:
    assert proc.stdout is not None
    ready, _, _ = select.select([proc.stdout], [], [], timeout)
    if not ready:
        raise RunError("가짜 KBGenAI 기동 대기 시간 초과")
    line = proc.stdout.readline().strip()
    if not line.startswith("FAKE_KBGENAI_READY port="):
        raise RunError("가짜 KBGenAI 기동 실패")
    return int(line.split("=", 1)[1])


def _wait_health(url: str, proc: subprocess.Popen[Any], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    with httpx.Client(trust_env=False) as client:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RunError(f"드라이런 프록시가 기동 중 종료(rc={proc.returncode})")
            try:
                if client.get(url + "/health", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
    raise RunError("드라이런 프록시 /health 대기 시간 초과")


def dry_settings_values(fake_port: int, token: str, poc_mode: bool) -> dict[str, Any]:
    """드라이런 설정 값 — 가짜 KBGenAI를 가리키는 가짜 자격증명."""
    base = f"http://127.0.0.1:{fake_port}"
    return {
        "fabrix_proxy_token": token,
        "fabrix_proxy_host": "127.0.0.1",
        "fabrix_proxy_log_level": "WARNING",
        "fabrix_proxy_model_aliases": ["fabrix-tools"],
        "fabrix_base_url": f"{base}/kbgenai/v1/chat",
        "fabrix_api_key": "dryrun-apikey-7Q3",
        "fabrix_client_key": "dryrun-clientkey-5M1",
        "fabrix_model": "dryrun-asset-001",
        "fabrix_verify_ssl": False,
        "fabrix_timeout": 30.0,
        "fabrix_total_timeout": 30.0,
        "fabrix_llm_config": {},
        "fabrix_native_url": f"{base}/native/v1/chat/completions",
        "fabrix_native_model": "dryrun-native",
        "fabrix_proxy_contents_mode": "turns",
        "fabrix_proxy_protocol_lang": "en",
        "fabrix_proxy_protocol_file": "",
        "fabrix_proxy_fewshot": "static",
        "fabrix_proxy_fewshot_placement": "system",
        "fabrix_proxy_fewshot_file": "",
        "fabrix_proxy_repair_max": 1,
        "fabrix_proxy_passthrough": False,
        "fabrix_proxy_poc_mode": poc_mode,
    }


def _env_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)


@contextmanager
def dry_stack(*, with_proxy: bool = True, poc_mode: bool = True) -> Iterator[Stack]:
    """가짜 KBGenAI(+ 프록시)를 하위 프로세스로 띄운다. 끝나면 자기 PID만 종료한다."""
    procs: list[subprocess.Popen[Any]] = []
    workdir = Path(tempfile.mkdtemp(prefix="fabrix_poc_dry_"))
    try:
        fake = subprocess.Popen(
            [sys.executable, str(FAKE_SCRIPT), "--host", "127.0.0.1", "--port", "0",
             "--script", str(DRYRUN_SCRIPT)],
            cwd=PROXY_ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            env=_child_env({}),
        )
        procs.append(fake)
        fake_port = _read_ready(fake)
        token = "dry" + secrets.token_hex(12)
        values = dry_settings_values(fake_port, token, poc_mode)
        proxy_url = ""
        if with_proxy:
            port = _free_port()
            values["fabrix_proxy_port"] = port
            env = _child_env({k.upper(): _env_value(v) for k, v in values.items()})
            with (workdir / "proxy.log").open("w", encoding="utf-8") as log_fh:
                proxy = subprocess.Popen(
                    [sys.executable, "-m", "fabrix_proxy", "--host", "127.0.0.1",
                     "--port", str(port)],
                    cwd=PROXY_ROOT, stdout=log_fh, stderr=subprocess.STDOUT, env=env,
                )
            procs.append(proxy)
            proxy_url = f"http://127.0.0.1:{port}"
            _wait_health(proxy_url, proxy)
        settings = ProxySettings(_env_file=None, **values)  # type: ignore[call-arg]
        yield Stack(proxy_url=proxy_url, token=token, settings=settings, procs=procs)
    finally:
        for proc in reversed(procs):
            _stop(proc)
        for child in workdir.iterdir():
            child.unlink(missing_ok=True)
        workdir.rmdir()


# ─────────────────────────────── 프록시 호출 ───────────────────────────────


@dataclass
class CallOutcome:
    """프록시 호출 1건의 결과(메모리 안 — 본문은 결과 파일에 쓰지 않는다)."""

    status: int
    latency_ms: int
    error_code: str | None = None
    diag: dict[str, Any] | None = None
    message: dict[str, Any] | None = None
    usage: bool = False
    hints: tuple[str, ...] = ()  # 400 invalid_request 메시지의 옵션·필드 이름(식별자만)

    @property
    def measured(self) -> bool:
        """모델 출력 판정이 가능한 호출 — 200 또는 502 tool_call_invalid."""
        return self.status == 200 or self.error_code == "tool_call_invalid"

    def calls(self) -> list[tuple[str, dict[str, Any]]]:
        """최종 응답의 (도구 이름, 인자) 목록."""
        out: list[tuple[str, dict[str, Any]]] = []
        for call in (self.message or {}).get("tool_calls") or []:
            fn = call.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (TypeError, ValueError):
                args = {}
            out.append((str(fn.get("name") or ""), args if isinstance(args, dict) else {}))
        return out

    def raw_heads(self) -> list[str]:
        """실패 덤프용 원출력 앞부분 — 진단 raw_heads 우선, 없으면 최종 메시지."""
        heads = [str(h) for h in (self.diag or {}).get("raw_heads") or []]
        if heads:
            return heads
        if self.message:
            text = str(self.message.get("content") or "")
            if self.message.get("tool_calls"):
                text += json.dumps(self.message["tool_calls"], ensure_ascii=False)
            return [text[:RAW_HEAD_CHARS]] if text else []
        return []


def outcome_from_body(status: int, latency_ms: int, body: Any) -> CallOutcome:
    """응답 바디(dict)에서 결과를 만든다."""
    if not isinstance(body, dict):
        return CallOutcome(status=status, latency_ms=latency_ms, error_code="not_json")
    diag = body.get(DIAG_FIELD) if isinstance(body.get(DIAG_FIELD), dict) else None
    if status != 200:
        err = body.get("error") if isinstance(body.get("error"), dict) else {}
        code = safe_code(err.get("code") or "http_error")
        hints: tuple[str, ...] = ()
        if code == "invalid_request":
            tokens = set(_HINT_TOKEN_RE.findall(str(err.get("message") or "")))
            hints = tuple(sorted(tokens & INVALID_REQUEST_HINTS))
        return CallOutcome(status, latency_ms, code, diag, hints=hints)
    try:
        message = body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return CallOutcome(status, latency_ms, "bad_response", diag)
    return CallOutcome(
        status, latency_ms, None, diag,
        message if isinstance(message, dict) else None,
        usage=isinstance(body.get("usage"), dict),
    )


class ProxyClient:
    """프록시 `/v1/chat/completions` 호출자(재시도 없음 · 로컬 프록시는 환경 프록시 미사용)."""

    def __init__(self, url: str, token: str, guard: ExportGuard, timeout: float = 600.0) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.guard = guard
        self.http = httpx.AsyncClient(
            base_url=self.url, trust_env=False, timeout=timeout,
            headers={"Authorization": f"Bearer {token}"},
        )

    async def aclose(self) -> None:
        await self.http.aclose()

    async def alias(self) -> str:
        """`/v1/models` 첫 별칭."""
        try:
            resp = await self.http.get("/v1/models")
            return str(resp.json()["data"][0]["id"])
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise RunError(
                f"프록시 /v1/models 확인 실패({type(exc).__name__}) — 프록시 기동·--proxy-url 확인"
            ) from exc

    async def poc_mode(self, alias: str) -> bool:
        """POC_MODE 여부 — 모르는 옵션 키를 보내 400(옵션 검증)이면 on.

        on이면 업스트림을 부르지 않는다. off면 옵션이 무시돼 FabriX가 1회 불린다.
        """
        body = {
            "model": alias,
            "messages": [{"role": "user", "content": "ping"}],
            OPTIONS_FIELD: {"__poc_probe__": True},
        }
        resp = await self.http.post("/v1/chat/completions", json=body)
        if resp.status_code != 400:
            return False
        try:
            return OPTIONS_FIELD in str(resp.json()["error"]["message"])
        except (ValueError, KeyError, TypeError):
            return False

    async def chat(self, body: dict[str, Any]) -> CallOutcome:
        """채팅 1건. 응답 원문 조각은 검사기에 기억시킨다."""
        t0 = time.monotonic()
        try:
            resp = await self.http.post("/v1/chat/completions", json=body)
        except httpx.TimeoutException:
            return CallOutcome(0, int((time.monotonic() - t0) * 1000), "runner_timeout")
        except httpx.HTTPError:
            return CallOutcome(0, int((time.monotonic() - t0) * 1000), "runner_connect_error")
        latency = int((time.monotonic() - t0) * 1000)
        try:
            data = resp.json()
        except ValueError:
            data = None
        outcome = outcome_from_body(resp.status_code, latency, data)
        note_outcome(self.guard, outcome)
        return outcome


def stop_on_invalid_request(outcome: CallOutcome) -> None:
    """프록시가 요청을 400 `invalid_request`로 거부하면 측정을 즉시 멈춘다(`RunError`).

    측정 선택지·프록시 설정 오류(예: `FABRIX_NATIVE_URL` 없이 `--passthrough`)라 반복해도 같은
    결과다. 오류 코드와 메시지 속 옵션 이름(식별자)만 출력한다. `content_filter`는 측정 결과다.
    """
    if outcome.status == 400 and outcome.error_code == "invalid_request":
        hint = ", ".join(outcome.hints) or "없음"
        raise RunError(
            f"프록시가 요청을 거부했다(400 invalid_request · 관련 옵션: {hint}) — "
            "측정 선택지와 프록시 설정을 확인한다. 측정 중단."
        )


def note_outcome(guard: ExportGuard, outcome: CallOutcome) -> None:
    """응답 원문(최종 메시지·raw_heads)을 검사기에 기억시킨다."""
    for head in (outcome.diag or {}).get("raw_heads") or []:
        guard.note_response(str(head))
    if outcome.message:
        guard.note_response(str(outcome.message.get("content") or ""))


# ─────────────────────────────── 결과 기록 ───────────────────────────────


@dataclass
class RunContext:
    """run 1회(라벨 1개)의 공용 상태."""

    client: ProxyClient
    alias: str
    opts: RunOptions
    run_id: str
    out: Path
    guard: ExportGuard
    sem: asyncio.Semaphore
    sleep: float

    @property
    def label(self) -> str:
        return self.opts.label()


def build_call_record(
    ctx: RunContext, scenario: str, case: str, rep: int, outcome: CallOutcome,
    turn: int | None = None,
) -> dict[str, Any]:
    """호출 1건의 결과 줄(판정값만). 시나리오 판정 필드는 호출자가 채운다."""
    diag = outcome.diag or {}
    attempts = [
        {
            "kind": safe_code(a.get("kind")),
            "reasons": [safe_reason(r) for r in a.get("reasons") or []],
            "called_names": [safe_name(n) for n in a.get("called_names") or []],
            "example_tool_called": bool(a.get("example_tool_called")),
            "call_like_text": bool(a.get("call_like_text")),
        }
        for a in diag.get("attempts") or []
        if isinstance(a, dict)
    ]
    # 태그 없는 호출 모양 평문(200 평문으로 돌아온 마지막 시도) — 형식 실패로 센다
    call_like = outcome.status == 200 and bool(attempts and attempts[-1]["call_like_text"])
    return {
        "record": "call",
        "run_id": ctx.run_id,
        "label": ctx.label,
        "options": ctx.opts.as_record(),
        "scenario": scenario,
        "case": case,
        "rep": rep,
        "turn": turn,
        "http_status": outcome.status,
        "error_code": outcome.error_code,
        "measured": outcome.measured,
        "emulation": safe_code(diag.get("emulation")),
        "passthrough": bool(diag.get("passthrough")),
        "attempts": attempts,
        "called_names": [safe_name(n) for n, _ in outcome.calls()],
        "example_tool_called": bool(attempts and attempts[0]["example_tool_called"]),
        "call_like_text": call_like,
        "format_ok": (outcome.status == 200 and not call_like) if outcome.measured else None,
        "args_ok": None,
        "selection_ok": None,
        "false_positive": None,
        "usage_present": None,
        "content_filtered": None,
        "latency_ms": outcome.latency_ms,
        "payload_chars": int(diag.get("payload_chars") or 0),
        "payload_est_tokens": int(diag.get("payload_est_tokens") or 0),
        "contents_len": int(diag.get("contents_len") or 0),
        "failure_type": None if outcome.measured else "limit_block",
    }


def build_case_record(
    ctx: RunContext, scenario: str, case: str, rep: int, **fields: Any
) -> dict[str, Any]:
    """다턴 케이스(S5·S6) 요약 줄."""
    record: dict[str, Any] = {
        "record": "case",
        "run_id": ctx.run_id,
        "label": ctx.label,
        "options": ctx.opts.as_record(),
        "scenario": scenario,
        "case": case,
        "rep": rep,
        "completed": None,
        "reflected": None,
        "repeated_call": None,
        "steps": None,
        "latency_ms": None,
        "skipped": None,
        "failure_type": None,
    }
    record.update(fields)
    return record


def write_record(ctx: RunContext, record: dict[str, Any]) -> None:
    """결과 줄 1개를 검사 후 덧붙인다."""
    ctx.guard.append(ctx.out / RESULTS, json.dumps(record, ensure_ascii=False) + "\n")


def dump_failure(
    ctx: RunContext, case: str, rep: int, turn: int | None, heads: list[str]
) -> None:
    """실패 건 원출력 앞 500자를 `failures/<라벨>/`에 남긴다(내부망 보관 · 반출 제외)."""
    if not heads:
        return
    target = ctx.out / FAILURES / ctx.label.replace(",", "_")
    target.mkdir(parents=True, exist_ok=True)
    suffix = f"_t{turn}" if turn is not None else ""
    text = "\n\n".join(f"--- attempt {i}\n{h[:RAW_HEAD_CHARS]}" for i, h in enumerate(heads, 1))
    (target / f"{case}_r{rep}{suffix}.txt").write_text(text, encoding="utf-8")


def finish_call(
    ctx: RunContext, record: dict[str, Any], outcome: CallOutcome, case: str, rep: int,
    turn: int | None = None,
) -> None:
    """실패면 원출력을 덤프하고 결과 줄을 쓴다."""
    if record["failure_type"]:
        dump_failure(ctx, case, rep, turn, outcome.raw_heads())
    write_record(ctx, record)


def _value_matches(actual: Any, allowed: Any) -> bool:
    if isinstance(allowed, (int, float)) and not isinstance(allowed, bool):
        try:
            return float(actual) == float(allowed)
        except (TypeError, ValueError):
            return False
    return str(allowed).lower() in str(actual).lower()


def args_match(args: dict[str, Any], checks: dict[str, list[Any]]) -> bool:
    """인자 검사 — 이름별 허용 값 중 하나와 맞아야 한다(문자열은 대소문자 무시 부분 일치)."""
    for key, allowed in checks.items():
        if key not in args or not any(_value_matches(args[key], a) for a in allowed):
            return False
    return True


def judge_calls(
    calls: list[tuple[str, dict[str, Any]]], expect: list[dict[str, Any]], mode: str,
    force: str | None = None,
) -> tuple[bool, bool]:
    """(선택 정답, 인자 유효). mode: set(호출 이름 집합 = expect) · required(호출 ≥1) · named."""
    names = [n for n, _ in calls]
    if mode == "set":
        selection = bool(calls) and set(names) == {e["name"] for e in expect}
    elif mode == "named":
        selection = bool(calls) and all(n == force for n in names)
    else:
        selection = bool(calls)
    checks = {e["name"]: e.get("args") or {} for e in expect}
    args_ok = bool(calls) and all(args_match(a, checks.get(n, {})) for n, a in calls)
    return selection, args_ok


# ─────────────────────────────── 시나리오 실행 ───────────────────────────────


def _body(
    ctx: RunContext, tools: list[dict[str, Any]], messages: list[dict[str, Any]],
    tool_choice: Any = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": ctx.alias,
        "messages": messages,
        "tools": tools,
        OPTIONS_FIELD: ctx.opts.proxy_options(),
    }
    if tool_choice is not None:
        body["tool_choice"] = tool_choice
    return body


async def _call(ctx: RunContext, body: dict[str, Any]) -> CallOutcome:
    outcome = await ctx.client.chat(body)
    stop_on_invalid_request(outcome)
    if ctx.sleep > 0:
        await asyncio.sleep(ctx.sleep)
    return outcome


async def run_expect_case(
    ctx: RunContext, scen: dict[str, Any], case: dict[str, Any], rep: int, mode: str,
    tool_choice: Any,
) -> None:
    """S1·S2·S3 — 도구 호출이 기대되는 단발 호출."""
    messages = [{"role": "user", "content": case["query"]}]
    outcome = await _call(ctx, _body(ctx, scen["tool_defs"], messages, tool_choice))
    record = build_call_record(ctx, scen["scenario"], case["id"], rep, outcome)
    if outcome.measured:
        selection, args_ok = judge_calls(
            outcome.calls(), case.get("expect") or [], mode, case.get("force")
        )
        if not record["format_ok"]:  # 502 tool_call_invalid · 호출 모양 평문
            selection = args_ok = False
        record["selection_ok"], record["args_ok"] = selection, args_ok
        if not record["format_ok"]:
            record["failure_type"] = "format"
        elif not (selection and args_ok):
            record["failure_type"] = "tool_selection"
    finish_call(ctx, record, outcome, case["id"], rep)


async def run_no_tool_case(
    ctx: RunContext, scen: dict[str, Any], case: dict[str, Any], rep: int
) -> None:
    """S4 — 도구가 필요 없는 질문(평문이어야 한다)."""
    messages = [{"role": "user", "content": case["query"]}]
    outcome = await _call(ctx, _body(ctx, scen["tool_defs"], messages, scen.get("tool_choice")))
    record = build_call_record(ctx, "S4", case["id"], rep, outcome)
    if outcome.measured:
        # 502 tool_call_invalid·호출 모양 평문도 호출 시도이므로 오탐이다
        false_positive = bool(outcome.calls()) or not record["format_ok"]
        record["false_positive"] = false_positive
        record["selection_ok"] = not false_positive
        if not record["format_ok"]:
            record["failure_type"] = "format"
        elif false_positive:
            record["failure_type"] = "tool_selection"
    finish_call(ctx, record, outcome, case["id"], rep)


async def run_f5_case(
    ctx: RunContext, scen: dict[str, Any], case: dict[str, Any], rep: int, kind: str
) -> None:
    """F5 — usage 유무 · PII 필터 차단 여부."""
    outcome = await _call(ctx, _body(ctx, scen["tool_defs"], case["messages"]))
    record = build_call_record(ctx, "F5", case["id"], rep, outcome)
    if kind == "usage":
        record["usage_present"] = outcome.usage if outcome.status == 200 else None
    else:
        record["content_filtered"] = outcome.error_code == "content_filter"
    finish_call(ctx, record, outcome, case["id"], rep)


def tool_result(results: dict[str, Any], name: str, args: dict[str, Any]) -> dict[str, Any]:
    """합성 도구 결과 — 도구 → 인자 이름 → 값 → 결과. 없으면 not_found."""
    for arg, table in (results.get(name) or {}).items():
        value = str(args.get(arg, ""))
        if value in table:
            found: dict[str, Any] = table[value]
            return found
    return {"error": "not_found"}


async def run_react_case(
    ctx: RunContext, scen: dict[str, Any], case: dict[str, Any], rep: int
) -> None:
    """S5 — 러너가 합성 도구 결과를 돌려주는 다턴 ReAct."""
    messages: list[dict[str, Any]] = [{"role": "user", "content": case["query"]}]
    seen: set[str] = set()
    repeated = completed = False
    final_text = ""
    last_failure: str | None = None
    last: CallOutcome | None = None
    turns = 0
    t0 = time.monotonic()
    for turn in range(1, int(scen.get("max_turns", 5)) + 1):
        turns = turn
        outcome = await _call(ctx, _body(ctx, scen["tool_defs"], messages, scen.get("tool_choice")))
        last = outcome
        record = build_call_record(ctx, "S5", case["id"], rep, outcome, turn)
        if outcome.measured and not record["format_ok"]:
            record["failure_type"] = "format"
        finish_call(ctx, record, outcome, case["id"], rep, turn)
        if outcome.status != 200 or record["call_like_text"]:  # 호출 모양 평문은 최종 답이 아니다
            last_failure = record["failure_type"]
            break
        calls = outcome.calls()
        if not calls:
            completed = True
            final_text = str((outcome.message or {}).get("content") or "")
            break
        message = outcome.message or {}
        messages.append(
            {"role": "assistant", "content": message.get("content"),
             "tool_calls": message.get("tool_calls")}
        )
        for raw_call, (name, args) in zip(message.get("tool_calls") or [], calls, strict=False):
            key = name + json.dumps(args, sort_keys=True, ensure_ascii=False)
            repeated = repeated or key in seen
            seen.add(key)
            messages.append({
                "role": "tool",
                "tool_call_id": raw_call.get("id") or "",
                "name": name,
                "content": json.dumps(
                    tool_result(scen["tool_results"], name, args), ensure_ascii=False
                ),
            })
    reflected = completed and all(
        m.lower() in final_text.lower() for m in case.get("reflect") or []
    )
    failure = None
    if not completed:
        failure = last_failure or "history"
    elif not reflected:
        failure = "history"
    if failure == "history" and last is not None:
        dump_failure(ctx, case["id"], rep, None, last.raw_heads())
    write_record(ctx, build_case_record(
        ctx, "S5", case["id"], rep, completed=completed,
        reflected=reflected if completed else None, repeated_call=repeated, steps=turns,
        latency_ms=int((time.monotonic() - t0) * 1000), failure_type=failure,
    ))


async def run_agent_case(
    ctx: RunContext, scen: dict[str, Any], case: dict[str, Any], rep: int
) -> None:
    """S6 — deepagents 미니 에이전트(ChatOpenAI → 프록시). 진단은 httpx 이벤트 훅으로 읽는다."""
    try:
        from deepagents import create_deep_agent
        from langchain_core.messages import AIMessage
        from langchain_core.tools import StructuredTool
        from langchain_openai import ChatOpenAI
        from pydantic import SecretStr
    except ImportError as exc:
        write_record(ctx, build_case_record(
            ctx, "S6", case["id"], rep, skipped=f"missing:{exc.name or 'module'}",
        ))
        return

    outcomes: list[CallOutcome] = []
    starts: dict[int, float] = {}

    async def on_request(request: httpx.Request) -> None:
        starts[id(request)] = time.monotonic()

    async def on_response(response: httpx.Response) -> None:
        await response.aread()
        t0 = starts.pop(id(response.request), time.monotonic())
        try:
            body = response.json()
        except ValueError:
            body = None
        outcome = outcome_from_body(
            response.status_code, int((time.monotonic() - t0) * 1000), body
        )
        note_outcome(ctx.guard, outcome)
        outcomes.append(outcome)

    def make_tool(name: str) -> Any:
        definition = load_tools()[name]["function"]

        def run_tool(**kwargs: Any) -> str:
            return json.dumps(tool_result(scen["tool_results"], name, kwargs), ensure_ascii=False)

        return StructuredTool.from_function(
            func=run_tool, name=name, description=definition.get("description", ""),
            args_schema=definition.get("parameters") or {"type": "object", "properties": {}},
        )

    http = httpx.AsyncClient(
        trust_env=False, timeout=600.0,
        event_hooks={"request": [on_request], "response": [on_response]},
    )
    model = ChatOpenAI(
        base_url=ctx.client.url + "/v1",
        api_key=SecretStr(ctx.client.token),
        model=ctx.alias,
        max_retries=0,
        extra_body={OPTIONS_FIELD: ctx.opts.proxy_options()},
        http_async_client=http,
    )
    agent = create_deep_agent(
        model=model, tools=[make_tool(n) for n in scen["tools"]],
        system_prompt=scen.get("system_prompt"),
    )
    t0 = time.monotonic()
    error: str | None = None
    final_text = ""
    completed = False
    try:
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": case["query"]}]},
            config={"recursion_limit": int(scen.get("recursion_limit", 40))},
        )
        last = (result.get("messages") or [None])[-1]
        if isinstance(last, AIMessage) and not last.tool_calls:
            completed = True
            final_text = str(last.content)
    except Exception as exc:  # noqa: BLE001 — 에이전트 실패는 기록하고 계속한다
        error = type(exc).__name__
    finally:
        await http.aclose()
    elapsed = int((time.monotonic() - t0) * 1000)
    if ctx.sleep > 0:
        await asyncio.sleep(ctx.sleep)
    for outcome in outcomes:
        stop_on_invalid_request(outcome)
    last_failure: str | None = None
    for turn, outcome in enumerate(outcomes, 1):
        record = build_call_record(ctx, "S6", case["id"], rep, outcome, turn)
        if outcome.measured and not record["format_ok"]:
            record["failure_type"] = "format"
        last_failure = record["failure_type"] or last_failure
        if record["call_like_text"] and turn == len(outcomes):  # 호출 모양 평문은 최종 답이 아니다
            completed = False
        finish_call(ctx, record, outcome, case["id"], rep, turn)
    reflected = completed and all(
        m.lower() in final_text.lower() for m in case.get("reflect") or []
    )
    failure = None if completed else (last_failure or "history")
    if failure == "history" and outcomes:
        dump_failure(ctx, case["id"], rep, None, outcomes[-1].raw_heads())
    write_record(ctx, build_case_record(
        ctx, "S6", case["id"], rep, completed=completed,
        reflected=reflected if completed else None, steps=len(outcomes), latency_ms=elapsed,
        error_code=safe_code(error.lower()) if error else None, failure_type=failure,
    ))


def _cycle(cases: list[dict[str, Any]], n: int) -> Iterator[tuple[dict[str, Any], int]]:
    """케이스 변형을 돌려 가며 n건 — (케이스, 반복 번호)."""
    for i in range(n):
        yield cases[i % len(cases)], i // len(cases) + 1


def scenario_tasks(
    ctx: RunContext, scenarios: dict[str, dict[str, Any]], key: str, n: int
) -> list[Callable[[], Awaitable[None]]]:
    """시나리오 1개의 작업(코루틴 팩토리) 목록."""
    scen = scenarios[key]
    tasks: list[Callable[[], Awaitable[None]]] = []

    def add(fn: Callable[..., Awaitable[None]], *args: Any) -> None:
        tasks.append(lambda: fn(ctx, scen, *args))

    if key in ("S1", "S2"):
        for case, rep in _cycle(scen["cases"], n):
            add(run_expect_case, case, rep, "set", scen.get("tool_choice"))
    elif key == "S3":
        for case, rep in _cycle(scen["required_cases"], n):
            add(run_expect_case, case, rep, "required", "required")
        for case, rep in _cycle(scen["named_cases"], n):
            choice = {"type": "function", "function": {"name": case["force"]}}
            add(run_expect_case, case, rep, "named", choice)
    elif key == "S4":
        for case, rep in _cycle(scen["cases"], n):
            add(run_no_tool_case, case, rep)
    elif key == "S5":
        for case, rep in _cycle(scen["cases"], n):
            add(run_react_case, case, rep)
    elif key == "S6":
        for case, rep in _cycle(scen["cases"], n):
            add(run_agent_case, case, rep)
    elif key == "F5":
        for case, rep in _cycle(scen["usage_cases"], n):
            add(run_f5_case, case, rep, "usage")
        for case, rep in _cycle(scen["pii_cases"], n):
            add(run_f5_case, case, rep, "pii")
    return tasks


async def measure(
    client: ProxyClient, alias: str, opts: RunOptions, only: list[str], repeat: dict[str, int],
    out: Path, guard: ExportGuard, concurrency: int, sleep: float,
) -> None:
    """라벨 1개로 시나리오들을 돈다. 개별 실패는 기록하고 계속한다(자기 검사 실패만 중단)."""
    ctx = RunContext(
        client=client, alias=alias, opts=opts,
        run_id=datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2),
        out=out, guard=guard, sem=asyncio.Semaphore(max(1, concurrency)), sleep=sleep,
    )
    scenarios = load_scenarios()
    print(f"[run] {ctx.label} — {','.join(only)}", flush=True)

    async def guarded(factory: Callable[[], Awaitable[None]]) -> None:
        async with ctx.sem:
            try:
                await factory()
            except (ExportCheckError, RunError):
                raise
            except Exception as exc:  # noqa: BLE001 — 시나리오 실패는 기록하고 계속한다
                print(f"[run] 시나리오 작업 실패: {type(exc).__name__}", file=sys.stderr)

    for key in only:
        factories = scenario_tasks(ctx, scenarios, key, repeat[key])
        running = [asyncio.ensure_future(guarded(t)) for t in factories]
        try:
            await asyncio.gather(*running)
        except BaseException:  # 중단(자기 검사 실패·invalid_request) — 나머지 작업을 거둔다
            for task in running:
                task.cancel()
            await asyncio.gather(*running, return_exceptions=True)
            raise


async def connect(proxy_url: str, token: str, guard: ExportGuard) -> tuple[ProxyClient, str]:
    """프록시에 붙어 별칭을 얻고 POC_MODE를 확인한다(아니면 RunError)."""
    client = ProxyClient(proxy_url, token, guard)
    try:
        alias = await client.alias()
        if not await client.poc_mode(alias):
            raise RunError(
                "프록시가 POC_MODE가 아니다(fabrix_proxy_diag 없음) — "
                "FABRIX_PROXY_POC_MODE=true로 재기동한 뒤 다시 돌린다. 측정 중단."
            )
    except BaseException:
        await client.aclose()
        raise
    return client, alias


# ─────────────────────────────── 지표 ───────────────────────────────

Ratio = tuple[int, int]


def ratio(num: int, den: int) -> Ratio:
    return (num, den)


def rate(r: Ratio | None) -> float | None:
    if r is None or r[1] == 0:
        return None
    return r[0] / r[1]


def fmt_ratio(r: Ratio | None) -> str:
    value = rate(r)
    if r is None or value is None:
        return "—"
    return f"{value * 100:.1f}% ({r[0]}/{r[1]})"


def percentile(values: list[int], p: float) -> int | None:
    """최근접 순위 백분위."""
    if not values:
        return None
    ordered = sorted(values)
    k = max(0, math.ceil(p / 100 * len(ordered)) - 1)
    return ordered[k]


def load_results(out: Path) -> list[dict[str, Any]]:
    path = out / RESULTS
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _calls(records: list[dict[str, Any]], scenarios: Iterable[str]) -> list[dict[str, Any]]:
    keys = set(scenarios)
    return [r for r in records if r["record"] == "call" and r["scenario"] in keys]


def _cases(records: list[dict[str, Any]], scenario: str) -> list[dict[str, Any]]:
    return [r for r in records if r["record"] == "case" and r["scenario"] == scenario]


def format_ratio(records: list[dict[str, Any]], scenarios: Iterable[str]) -> Ratio:
    """형식 유효율 — 판정 가능 호출(200·502 tool_call_invalid) 중 200(교정 포함)."""
    measured = [c for c in _calls(records, scenarios) if c["measured"]]
    return ratio(sum(1 for c in measured if c["format_ok"]), len(measured))


def _flag_ratio(rows: list[dict[str, Any]], key: str) -> Ratio:
    known = [r for r in rows if r.get(key) is not None]
    return ratio(sum(1 for r in known if r[key]), len(known))


def turn_lengths(records: list[dict[str, Any]], scenario: str) -> dict[str, Any]:
    """턴별 입력 길이 평균(문자 · 추정 토큰)과 턴당 증가량."""
    by_turn: dict[int, list[dict[str, Any]]] = {}
    for c in _calls(records, [scenario]):
        if c.get("turn") and c.get("payload_chars"):
            by_turn.setdefault(int(c["turn"]), []).append(c)
    rows = []
    for turn in sorted(by_turn):
        group = by_turn[turn]
        rows.append({
            "turn": turn,
            "n": len(group),
            "chars": sum(c["payload_chars"] for c in group) / len(group),
            "tokens": sum(c["payload_est_tokens"] for c in group) / len(group),
        })
    growth = None
    if len(rows) >= 2:
        span = rows[-1]["turn"] - rows[0]["turn"]
        growth = {
            "chars": (rows[-1]["chars"] - rows[0]["chars"]) / span,
            "tokens": (rows[-1]["tokens"] - rows[0]["tokens"]) / span,
        }
    return {"rows": rows, "growth": growth}


def label_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """라벨 1개의 지표."""
    calls = [r for r in records if r["record"] == "call"]
    s123 = [c for c in _calls(records, ["S1", "S2", "S3"]) if c["measured"]]
    s1 = [c for c in _calls(records, ["S1"]) if c["measured"]]
    s4 = [c for c in _calls(records, ["S4"]) if c["measured"]]
    s5 = _cases(records, "S5")
    s6_all = _cases(records, "S6")
    s6 = [c for c in s6_all if not c.get("skipped")]
    ok200 = [c for c in calls if c["http_status"] == 200]
    latencies = [int(c["latency_ms"]) for c in calls if c["http_status"]]
    f5 = _calls(records, ["F5"])
    s6_lat = [int(c["latency_ms"]) for c in s6 if c.get("latency_ms") is not None]
    return {
        "n_calls": len(calls),
        "format_s123": format_ratio(records, ["S1", "S2", "S3"]),
        "args_s123": _flag_ratio(s123, "args_ok"),
        "select_s1": _flag_ratio(s1, "selection_ok"),
        "fp_s4": _flag_ratio(s4, "false_positive"),
        "complete_s5": _flag_ratio(s5, "completed"),
        "reflect_s5": _flag_ratio([c for c in s5 if c.get("completed")], "reflected"),
        "repeat_s5": sum(1 for c in s5 if c.get("repeated_call")),
        "complete_s6": _flag_ratio(s6, "completed"),
        "steps_s6": (sum(int(c.get("steps") or 0) for c in s6) / len(s6)) if s6 else None,
        "lat_s6_p50": percentile(s6_lat, 50),
        "lat_s6_p95": percentile(s6_lat, 95),
        "skip_s6": sorted({str(c["skipped"]) for c in s6_all if c.get("skipped")}),
        "repair_dep": ratio(sum(1 for c in ok200 if c.get("emulation") == "repaired"), len(ok200)),
        "example_miscall": sum(1 for c in calls if c.get("example_tool_called")),
        "call_like": sum(1 for c in calls if c.get("call_like_text")),
        "p50": percentile(latencies, 50),
        "p95": percentile(latencies, 95),
        "infra": sum(1 for c in calls if not c["measured"]),
        "usage_f5": _flag_ratio(f5, "usage_present"),
        "pii_f5": _flag_ratio(f5, "content_filtered"),
        "turns_s5": turn_lengths(records, "S5"),
        "turns_s6": turn_lengths(records, "S6"),
        # 다턴(S5·S6)은 케이스 줄로, 단발은 호출 줄로 센다(이중 계수 방지)
        "failures": {
            k: sum(
                1 for r in records
                if r.get("failure_type") == k
                and (r["record"] == "case" or r["scenario"] not in ("S5", "S6"))
            )
            for k in FAILURE_LABELS
        },
    }


def judgments(m: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    """판정 초안 — (지표, 값, 합격선, 충족)."""

    def check(r: Ratio | None, threshold: float, upper: bool = False) -> str:
        value = rate(r)
        if value is None:
            return "자료 없음"
        ok = value <= threshold if upper else value >= threshold
        return "충족" if ok else "미달"

    rows = [
        ("형식 유효율(S1~S3)", m["format_s123"], f"≥{PASS_FORMAT:.0%}", PASS_FORMAT, False),
        ("인자 유효율(S1~S3)", m["args_s123"], f"≥{PASS_FORMAT:.0%}", PASS_FORMAT, False),
        ("도구 선택 정답률(S1)", m["select_s1"], f"≥{PASS_SELECT:.0%}", PASS_SELECT, False),
        ("오탐률(S4)", m["fp_s4"], f"≤{PASS_FALSE_POSITIVE:.0%}", PASS_FALSE_POSITIVE, True),
        ("완주율(S5)", m["complete_s5"], f"≥{PASS_COMPLETE:.0%}", PASS_COMPLETE, False),
        ("완주율(S6)", m["complete_s6"], f"≥{PASS_COMPLETE:.0%}", PASS_COMPLETE, False),
        ("도구 결과 반영률(S5)", m["reflect_s5"], f"≥{PASS_REFLECT:.0%}", PASS_REFLECT, False),
        ("교정 의존률", m["repair_dep"], f"≤{PASS_REPAIR_DEP:.0%}", PASS_REPAIR_DEP, True),
    ]
    out = [(name, fmt_ratio(r), line, check(r, th, upper)) for name, r, line, th, upper in rows]
    miscall = m["example_miscall"]
    out.append(("예시 도구 오호출(교정 전 원응답)", f"{miscall}건", "0건",
                "충족" if miscall == 0 else "미달"))
    return out


# ─────────────────────────────── 권장값 ───────────────────────────────


RECOMMEND_KEYS = ("CONTENTS_MODE", "FEWSHOT", "FEWSHOT_PLACEMENT", "PROTOCOL_LANG")


@dataclass
class Recommendation:
    """권장값 · 권고 줄 · 근거."""

    values: dict[str, str] = field(default_factory=dict)
    advice: list[str] = field(default_factory=list)
    basis: list[str] = field(default_factory=list)

    @property
    def lines(self) -> list[str]:
        """summary 고정 형식 줄 — `RECOMMEND KEY=value`(고정 순서) 다음 `ADVISE …`."""
        recs = [f"RECOMMEND {k}={self.values[k]}" for k in RECOMMEND_KEYS if k in self.values]
        return recs + self.advice


def _group_key(options: dict[str, Any], drop: Iterable[str]) -> str:
    skip = set(drop)
    return json.dumps({k: v for k, v in sorted(options.items()) if k not in skip})


def _labels(records: list[dict[str, Any]]) -> dict[str, tuple[dict[str, Any], int]]:
    """라벨 → (옵션, 마지막 등장 순번)."""
    labels: dict[str, tuple[dict[str, Any], int]] = {}
    for i, r in enumerate(records):
        labels[r["label"]] = (r["options"], i)
    return labels


def _by_label(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        grouped.setdefault(r["label"], []).append(r)
    return grouped


def _has(records: list[dict[str, Any]], scenario: str) -> bool:
    return any(r["scenario"] == scenario for r in records)


def recommend_fewshot(records: list[dict[str, Any]], rec: Recommendation) -> None:
    """⑤a — fewshot 모드 비교(S1·S4·S5)."""
    labels = _labels(records)
    by_label = _by_label(records)
    groups: dict[str, dict[str, str]] = {}
    for label, (options, _) in labels.items():
        if _has(by_label[label], "S1"):
            key = _group_key(options, ["fewshot"])
            groups.setdefault(key, {})[options["fewshot"]] = label
    usable = {k: g for k, g in groups.items() if len(g) >= 2}
    if not usable:
        return
    def recency(k: str) -> tuple[int, int]:
        return len(usable[k]), max(labels[lb][1] for lb in usable[k].values())

    key = max(usable, key=recency)
    group = usable[key]
    main_placement = labels[next(iter(group.values()))][0]["fewshot_placement"]
    scen = ["S1", "S4", "S5"]
    candidates: list[tuple[str, str, float | None, int, Ratio]] = []
    for mode in FEWSHOT_MODES:
        if mode in group:
            rows = by_label[group[mode]]
            fr = format_ratio(rows, scen)
            mis = sum(1 for c in _calls(rows, scen) if c.get("example_tool_called"))
            candidates.append((mode, main_placement, rate(fr), mis, fr))
    main_rates = [c[2] for c in candidates if c[2] is not None]
    all_low = bool(main_rates) and all(r < FEWSHOT_LOW for r in main_rates)
    extra_key = _group_key(json.loads(key), ["fewshot_placement"])
    if main_placement != "contents":
        for label, (options, _) in labels.items():
            if (options["fewshot"] == "static" and options["fewshot_placement"] == "contents"
                    and _group_key(options, ["fewshot", "fewshot_placement"]) == extra_key
                    and _has(by_label[label], "S1")):
                rows = by_label[label]
                fr = format_ratio(rows, scen)
                mis = sum(1 for c in _calls(rows, scen) if c.get("example_tool_called"))
                candidates.append(("static", "contents", rate(fr), mis, fr))
                break
    for mode, placement, _, mis, fr in candidates:
        rec.basis.append(
            f"⑤a fewshot={mode} placement={placement}: 형식 유효율(S1·S4·S5) {fmt_ratio(fr)}"
            f" · 예시 도구 오호출 {mis}건"
        )
    has_contents = any(c[1] == "contents" for c in candidates) and main_placement != "contents"
    if all_low and not has_contents:
        rec.advice.append(
            "ADVISE FEWSHOT_RERUN=--fewshot static --fewshot-placement contents"
            " (⑤a 세 모드 모두 형식 유효율 <90% — ⑤a 1회 더)"
        )
    eligible = [c for c in candidates if c[3] == 0 and c[2] is not None]
    if not eligible:
        rec.advice.append("ADVISE FEWSHOT=undecided (모든 모드에 예시 도구 오호출 또는 자료 없음)")
        return
    best = max(c[2] for c in eligible if c[2] is not None)
    statics = [c for c in eligible if c[0] == "static" and best - (c[2] or 0.0) <= TIE_MARGIN]
    if statics:
        chosen = max(statics, key=lambda c: (c[2] or 0.0, c[1] == main_placement))
    else:
        order = {m: i for i, m in enumerate(FEWSHOT_MODES[::-1])}  # 동률이면 static>dynamic>none
        chosen = max(eligible, key=lambda c: (c[2] or 0.0, order.get(c[0], 0)))
    rec.values["FEWSHOT"] = chosen[0]
    rec.values["FEWSHOT_PLACEMENT"] = chosen[1]


def recommend_lang(records: list[dict[str, Any]], rec: Recommendation) -> None:
    """⑤b — 규약 언어 비교(S1·S5)."""
    labels = _labels(records)
    by_label = _by_label(records)
    groups: dict[str, dict[str, str]] = {}
    for label, (options, _) in labels.items():
        if _has(by_label[label], "S1"):
            groups.setdefault(_group_key(options, ["protocol_lang"]), {})[
                options["protocol_lang"]
            ] = label
    usable = {k: g for k, g in groups.items() if all(lang in g for lang in LANGS)}
    if not usable:
        return
    key = max(usable, key=lambda k: max(labels[lb][1] for lb in usable[k].values()))
    stats: dict[str, tuple[float, float, Ratio, Ratio]] = {}
    for lang in LANGS:
        rows = by_label[usable[key][lang]]
        fr = format_ratio(rows, ["S1", "S5"])
        cr = _flag_ratio(_cases(rows, "S5"), "completed")
        stats[lang] = (rate(fr) or 0.0, rate(cr) or 0.0, fr, cr)
        rec.basis.append(
            f"⑤b lang={lang}: 형식 유효율(S1·S5) {fmt_ratio(fr)} · S5 완주율 {fmt_ratio(cr)}"
        )
    en, ko = stats["en"], stats["ko"]
    if abs(en[0] - ko[0]) > TIE_MARGIN:
        chosen = "en" if en[0] > ko[0] else "ko"
    elif en[1] != ko[1]:
        chosen = "en" if en[1] > ko[1] else "ko"
    else:
        chosen = "en"
    rec.values["PROTOCOL_LANG"] = chosen


def recommend_contents(probe: dict[str, Any] | None, rec: Recommendation) -> None:
    """F2 탐침 결과 → CONTENTS_MODE."""
    if not probe or probe.get("recommend_contents_mode") not in ("turns", "transcript"):
        return
    rec.values["CONTENTS_MODE"] = str(probe["recommend_contents_mode"])
    for mode, row in (probe.get("tool_history") or {}).items():
        rec.basis.append(f"F2 {mode}: 다음 행동 정답 {row.get('ok', 0)}/{row.get('n', 0)}")


def recommend_passthrough(probe: dict[str, Any] | None, rec: Recommendation) -> None:
    """F1 탐침 — 네이티브 tool_calls가 있으면 passthrough 측정 추가 권고."""
    if probe and any(c.get("tool_calls") for c in probe.get("candidates") or []):
        rec.advice.append(
            "ADVISE PASSTHROUGH=⑥ 뒤 --passthrough 측정 추가 (F1 네이티브 응답에 tool_calls 있음)"
        )


def compute_recommendation(
    records: list[dict[str, Any]], probe_native: dict[str, Any] | None,
    probe_contents: dict[str, Any] | None,
) -> Recommendation:
    """디렉터리 결과(+탐침)로 권장값을 계산한다."""
    rec = Recommendation()
    recommend_contents(probe_contents, rec)
    recommend_fewshot(records, rec)
    recommend_lang(records, rec)
    recommend_passthrough(probe_native, rec)
    return rec


def update_recommend_env(path: Path, values: dict[str, str], guard: ExportGuard) -> None:
    """recommend.env — 근거 있는 키만 쓰고, 기존 키는 갱신(다른 키는 보존)."""
    existing: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() and not key.strip().startswith("#"):
                existing[key.strip()] = value.strip()
    existing.update(values)
    if not existing:
        return
    lines = ["# fabrix_proxy PoC 권장값 — scripts/poc_run.py 생성 (셸 source 가능)"]
    lines += [f"{k}={v}" for k, v in existing.items()]
    guard.write(path, "\n".join(lines) + "\n")


# ─────────────────────────────── summary ───────────────────────────────


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _num(value: float | int | None, digits: int = 0) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def render_summary(
    records: list[dict[str, Any]], probe_native: dict[str, Any] | None,
    probe_contents: dict[str, Any] | None, rec: Recommendation,
) -> str:
    """summary.md 본문 — 판정값·수치만."""
    by_label = _by_label(records)
    run_ids = sorted({r["run_id"] for r in records})
    lines = [
        "# fabrix_proxy PoC 요약 (plans/148 1단계)",
        "",
        f"- POC_VERSION: {poc_version()}",
        f"- 생성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 실행 run: {len(run_ids)}회 ({', '.join(run_ids) if run_ids else '없음'})",
        f"- 라벨 수: {len(by_label)}",
        "- 반출: `summary.md`·`results.jsonl`·`env_check.md` 3종만"
        "(`recommend.env`·`probe_*.json`도 내부망 보관)."
        " `failures/`는 내부망 보관(반출 제외).",
        "",
    ]
    metrics = {label: label_metrics(rows) for label, rows in by_label.items()}
    if metrics:
        lines += [
            "## 라벨별 지표",
            "",
            "단발 지표(S1~S4)는 판정 가능 호출(200·502 tool_call_invalid) 기준이며 인프라 실패"
            "(업스트림 오류·시간 초과·PII 차단)는 따로 센다."
            " 완주율(S5·S6)은 인프라 실패를 포함한다.",
            "",
            "| 라벨 | 호출 | 형식(S1~S3) | 인자(S1~S3) | 선택(S1) | 오탐(S4) | 완주(S5) | 반영(S5)"
            " | 완주(S6) | 교정 의존 | 예시 오호출 | 호출 모양 평문 | p50 ms | p95 ms"
            " | 인프라 실패 |",
            "|---|---:|---|---|---|---|---|---|---|---|---:|---:|---:|---:|---:|",
        ]
        for label, m in metrics.items():
            lines.append(
                f"| `{label}` | {m['n_calls']} | {fmt_ratio(m['format_s123'])} | "
                f"{fmt_ratio(m['args_s123'])} | {fmt_ratio(m['select_s1'])} | "
                f"{fmt_ratio(m['fp_s4'])} | {fmt_ratio(m['complete_s5'])} | "
                f"{fmt_ratio(m['reflect_s5'])} | {fmt_ratio(m['complete_s6'])} | "
                f"{fmt_ratio(m['repair_dep'])} | {m['example_miscall']} | {m['call_like']} | "
                f"{_num(m['p50'])} | "
                f"{_num(m['p95'])} | {m['infra']} |"
            )
        lines += ["", "## 실패 유형 · S5·S6 세부 · F5", ""]
        for label, m in metrics.items():
            f = m["failures"]
            lines += [
                f"### `{label}`",
                "",
                "- 실패 유형: " + " · ".join(f"{FAILURE_LABELS[k]} {f[k]}" for k in FAILURE_LABELS),
                "- 호출 모양 평문(태그 없는 호출 JSON — 형식 실패·S4 오탐으로 셈): "
                f"{m['call_like']}건",
                f"- S5 같은 호출 반복: {m['repeat_s5']}건",
                f"- S6 단계 수 평균: {_num(m['steps_s6'], 1)} · 실행 지연 p50/p95: "
                f"{_num(m['lat_s6_p50'])}/{_num(m['lat_s6_p95'])} ms"
                + (f" · skip: {', '.join(m['skip_s6'])}" if m["skip_s6"] else ""),
                f"- F5 usage 있음: {fmt_ratio(m['usage_f5'])} · PII 차단: {fmt_ratio(m['pii_f5'])}",
            ]
            for scen in ("S5", "S6"):
                tl = m[f"turns_{scen.lower()}"]
                if not tl["rows"]:
                    continue
                lines += [
                    "",
                    f"{scen} 턴별 입력 길이(평균):",
                    "",
                    "| 턴 | 호출 | 문자 | 추정 토큰 |",
                    "|---:|---:|---:|---:|",
                ]
                lines += [
                    f"| {row['turn']} | {row['n']} | {row['chars']:.0f} | {row['tokens']:.0f} |"
                    for row in tl["rows"]
                ]
                if tl["growth"]:
                    lines.append(
                        f"\n턴당 증가량: 문자 {tl['growth']['chars']:.0f} · "
                        f"추정 토큰 {tl['growth']['tokens']:.0f}"
                    )
            lines.append("")
    else:
        lines += ["## 라벨별 지표", "", "결과 없음", ""]

    lines += ["## 탐침", ""]
    if probe_native:
        lines += ["F1 네이티브 엔드포인트(인증 헤더는 KBGenAI와 같다고 추정):", "",
                  "| 후보 | HTTP | 오류 | 엔드포인트 | tool_calls |", "|---|---|---|---|---|"]
        for c in probe_native.get("candidates") or []:
            lines.append(
                f"| {c.get('source')} | {c.get('http_status') or '—'} | {c.get('error') or '—'} | "
                f"{'있음' if c.get('endpoint_exists') else '없음'} | "
                f"{'있음' if c.get('tool_calls') else '없음'} |"
            )
        lines.append("")
    else:
        lines += ["F1: 결과 없음", ""]
    if probe_contents:
        lines += ["F2 contents 규칙:", "", "| 사례 | 역할 인식 |", "|---|---|"]
        for case_id, row in (probe_contents.get("roles") or {}).items():
            lines.append(f"| {case_id} | {row.get('recognized', 0)}/{row.get('n', 0)} |")
        lines += ["", "| 직렬화 | 다음 행동 정답 |", "|---|---|"]
        for mode, row in (probe_contents.get("tool_history") or {}).items():
            lines.append(f"| {mode} | {row.get('ok', 0)}/{row.get('n', 0)} |")
        lines.append("")
    else:
        lines += ["F2: 결과 없음", ""]

    lines += ["## 판정 초안 (참고용 · 판정은 개발 쪽)", ""]
    for label, m in metrics.items():
        lines += [f"### `{label}`", "", "| 지표 | 값 | 합격선 | 충족 |", "|---|---|---|---|"]
        lines += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in judgments(m)]
        lines.append("")

    lines += ["## 권장값", "", "```"]
    lines += rec.lines or ["(근거 없음)"]
    lines.append("```")
    if rec.basis:
        lines += ["", "근거:", ""] + [f"- {b}" for b in rec.basis]
    lines.append("")
    return "\n".join(lines)


def rebuild_reports(out: Path, guard: ExportGuard) -> Recommendation:
    """디렉터리 전체 결과로 summary.md·recommend.env를 다시 만든다."""
    records = load_results(out)
    probe_native = _read_json(out / PROBE_NATIVE)
    probe_contents = _read_json(out / PROBE_CONTENTS)
    rec = compute_recommendation(records, probe_native, probe_contents)
    guard.write(out / SUMMARY, render_summary(records, probe_native, probe_contents, rec))
    update_recommend_env(out / RECOMMEND, rec.values, guard)
    return rec


# ─────────────────────────────── env-check ───────────────────────────────


def package_versions() -> dict[str, str]:
    versions = {"python": sys.version.split()[0]}
    for dist in VERSION_DISTS:
        try:
            versions[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            versions[dist] = "누락"
    return versions


def upstream_error_code(exc: BaseException) -> str:
    """업스트림 예외 → 코드(본문 없이)."""
    if isinstance(exc, ContentFilterError):
        return "content_filter"
    if isinstance(exc, UpstreamError):
        text = str(exc)
        match = re.search(r"HTTP (\d{3})", text)
        if match:
            return f"http_{match.group(1)}"
        if "미설정" in text:
            return "not_configured"
        if "status=" in text:
            return "status_not_success"
        return "upstream_error"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPError):
        return "connect_error"
    return "error_" + type(exc).__name__.lower()


async def fabrix_reach(settings: ProxySettings) -> str:
    """FabriX KBGenAI 짧은 호출 1회 — 결과 코드만."""
    if not settings.fabrix_base_url:
        return "not_configured"
    payload = build_payload(
        PreparedRequest(system_prompt="Reply with the single word OK.", turns=[("user", "ping")]),
        model_id=settings.fabrix_model, contents_mode="turns",
        llm_config=build_llm_config(settings.fabrix_llm_config, None, None),
    )
    try:
        await KBGenAIUpstream(settings).complete(payload)
    except Exception as exc:  # noqa: BLE001 — 도달 실패는 코드로만 남긴다
        return upstream_error_code(exc)
    return "ok"


def compare_fields(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """녹화 필드 서명 대조 — 차이 목록(소비자·요청 번호·키 이름만)."""
    diffs: list[str] = []
    base_consumers = baseline.get("consumers") or {}
    cur_consumers = current.get("consumers") or {}
    for name, base in base_consumers.items():
        if name.startswith(FIELD_COMPARE_EXCLUDE):
            continue
        cur = cur_consumers.get(name)
        if cur is None:
            diffs.append(f"{name}: 녹화 없음")
            continue
        if cur.get("status") != base.get("status"):
            diffs.append(f"{name}: 상태 {base.get('status')} → {cur.get('status')}")
            continue
        b_reqs, c_reqs = base.get("requests") or [], cur.get("requests") or []
        if len(b_reqs) != len(c_reqs):
            diffs.append(f"{name}: 요청 수 {len(b_reqs)} → {len(c_reqs)}")
        for i, (b, c) in enumerate(zip(b_reqs, c_reqs, strict=False), 1):
            bs, cs = b.get("signature") or {}, c.get("signature") or {}
            keys = sorted(k for k in set(bs) | set(cs) if bs.get(k) != cs.get(k))
            if b.get("case") != c.get("case"):
                keys.insert(0, "case")
            if keys:
                diffs.append(f"{name} 요청#{i}: {', '.join(keys)}")
    for name in cur_consumers:
        if name not in base_consumers and not name.startswith(FIELD_COMPARE_EXCLUDE):
            diffs.append(f"{name}: 기준에 없는 소비자")
    return diffs


def record_fields() -> tuple[int, dict[str, Any] | None]:
    """녹화 하네스를 임시 디렉터리로 다시 돌린다(외부 패키지 소비자만) → (종료 코드, 요약)."""
    with tempfile.TemporaryDirectory(prefix="fabrix_poc_rec_") as tmp:
        try:
            proc = subprocess.run(
                [sys.executable, str(RECORD_SCRIPT), "--out", tmp],
                cwd=PROXY_ROOT, capture_output=True, text=True, timeout=600, check=False,
                env=_child_env({}),
            )
        except subprocess.TimeoutExpired:
            return -1, None
        return proc.returncode, _read_json(Path(tmp) / "fields_summary.json")


async def proxy_status(proxy_url: str, token: str) -> dict[str, str]:
    """프록시 /health·/v1/models 상태와 POC_MODE 여부(on이면 FabriX 호출 없음)."""
    result = {"health": "—", "models": "—", "poc_mode": "판정 불가"}
    async with httpx.AsyncClient(base_url=proxy_url, trust_env=False, timeout=30) as http:
        for key, path in (("health", "/health"), ("models", "/v1/models")):
            try:
                result[key] = str((await http.get(path)).status_code)
            except httpx.HTTPError as exc:
                result[key] = upstream_error_code(exc)
    if result["models"] == "200" and token:
        client = ProxyClient(proxy_url, token, ExportGuard())
        try:
            result["poc_mode"] = "on" if await client.poc_mode(await client.alias()) else "off"
        except (RunError, httpx.HTTPError):
            pass
        finally:
            await client.aclose()
    return result


async def env_check(
    settings: ProxySettings, proxy_url: str, token: str, out: Path, guard: ExportGuard
) -> None:
    """env_check.md — 판정값만."""
    versions = package_versions()
    baseline = _read_json(BASELINE_FIELDS) or {}
    base_versions = baseline.get("versions") or {}
    reach = await fabrix_reach(settings)
    proxy = await proxy_status(proxy_url, token)
    code, current = await asyncio.to_thread(record_fields)
    lines = [
        "# fabrix_proxy PoC env-check",
        "",
        f"- POC_VERSION: {poc_version()}",
        f"- 생성 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 설치 버전",
        "",
        "| 패키지 | 설치 | 녹화 기준 |",
        "|---|---|---|",
    ]
    lines += [f"| {k} | {v} | {base_versions.get(k, '—')} |" for k, v in versions.items()]
    lines += [
        "",
        "## FabriX 도달 (KBGenAI 짧은 호출 1회)",
        "",
        f"- 결과: {reach}",
        "",
        "## 프록시",
        "",
        f"- /health: {proxy['health']}",
        f"- /v1/models: {proxy['models']}",
        f"- POC_MODE: {proxy['poc_mode']}",
        "",
        "## 녹화 필드 대조 (외부 패키지 소비자 · 기준 testdata/consumer_requests)",
        "",
        f"- 하네스 종료 코드: {code}",
    ]
    if current is None:
        lines.append("- 대조 불가(녹화 요약 없음)")
    else:
        diffs = compare_fields(baseline, current)
        lines.append(f"- 차이: {len(diffs)}건" + ("" if diffs else " (없음)"))
        lines += [f"  - {d}" for d in diffs]
    lines.append("")
    guard.write(out / ENV_CHECK, "\n".join(lines))
    print(f"[env-check] FabriX={reach} health={proxy['health']} models={proxy['models']} "
          f"poc_mode={proxy['poc_mode']} 녹화 하네스 rc={code}", flush=True)


# ─────────────────────────────── 하위 명령 ───────────────────────────────


def _token(settings: ProxySettings) -> str:
    token = settings.fabrix_proxy_token.strip()
    if not token:
        raise UsageError("FABRIX_PROXY_TOKEN이 비어 있다 — fabrix_proxy/.env 또는 환경변수로 준다")
    return token


def cmd_env_check(args: argparse.Namespace) -> int:
    out = resolve_out(args.out)
    if args.dry_run:
        with dry_stack() as stack:
            guard = build_guard([stack.settings])
            asyncio.run(env_check(stack.settings, stack.proxy_url, stack.token, out, guard))
    else:
        settings = load_settings()
        guard = build_guard([settings])
        asyncio.run(env_check(settings, args.proxy_url, settings.fabrix_proxy_token, out, guard))
    print(f"[env-check] {out / ENV_CHECK}")
    return 0


async def _run_measure(
    args: argparse.Namespace, stack: Stack, out: Path, guard: ExportGuard, only: list[str],
    repeat: dict[str, int],
) -> None:
    opts = resolve_run_options(args, stack.settings)
    client, alias = await connect(stack.proxy_url, stack.token, guard)
    try:
        await measure(client, alias, opts, only, repeat, out, guard, args.concurrency, args.sleep)
    finally:
        await client.aclose()
    rebuild_reports(out, guard)


async def _run_full_dry(
    args: argparse.Namespace, stack: Stack, out: Path, guard: ExportGuard,
    repeat: dict[str, int],
) -> None:
    """드라이런 전체 순서 — env-check → F1 → F2 → ⑤a → ⑤b → ⑥."""
    import probe_fabrix

    await env_check(stack.settings, stack.proxy_url, stack.token, out, guard)
    await probe_fabrix.probe_native(
        stack.settings, out, guard, [("dry-run#closed-port", _closed_url())]
    )
    await probe_fabrix.probe_contents(stack.settings, out, guard, repeat=min(3, repeat["S1"]))
    base = resolve_run_options(args, stack.settings)
    client, alias = await connect(stack.proxy_url, stack.token, guard)
    try:
        async def run(opts: RunOptions, only: list[str]) -> None:
            await measure(
                client, alias, opts, only, repeat, out, guard, args.concurrency, args.sleep
            )

        for mode in FEWSHOT_MODES:  # ⑤a
            await run(replace(base, fewshot=mode), ["S1", "S4", "S5"])
        rec = rebuild_reports(out, guard)
        if any(line.startswith("ADVISE FEWSHOT_RERUN") for line in rec.lines):
            await run(replace(base, fewshot="static", fewshot_placement="contents"),
                      ["S1", "S4", "S5"])
            rec = rebuild_reports(out, guard)
        chosen = replace(
            base,
            fewshot=rec.values.get("FEWSHOT", base.fewshot),
            fewshot_placement=rec.values.get("FEWSHOT_PLACEMENT", base.fewshot_placement),
        )
        for lang in LANGS:  # ⑤b
            await run(replace(chosen, protocol_lang=lang), ["S1", "S5"])
        rec = rebuild_reports(out, guard)
        final = replace(
            chosen,
            protocol_lang=rec.values.get("PROTOCOL_LANG", chosen.protocol_lang),
            contents_mode=rec.values.get("CONTENTS_MODE", chosen.contents_mode),
        )
        await run(final, list(SCENARIOS))  # ⑥
    finally:
        await client.aclose()
    rebuild_reports(out, guard)


def _closed_url() -> str:
    """닫힌 포트 URL — 드라이런 F1의 연결 실패 사례."""
    return f"http://127.0.0.1:{_free_port()}/v1/chat/completions"


def cmd_run(args: argparse.Namespace) -> int:
    repeat = parse_repeat(args.repeat)
    only = parse_only(args.only)
    if args.concurrency < 1:
        raise UsageError("--concurrency는 1 이상이어야 한다")
    out = resolve_out(args.out)
    started = time.monotonic()
    if args.dry_run:
        with dry_stack() as stack:
            guard = build_guard([stack.settings])
            if only is None:
                asyncio.run(_run_full_dry(args, stack, out, guard, repeat))
            else:
                asyncio.run(_run_measure(args, stack, out, guard, only, repeat))
    else:
        settings = load_settings()
        stack = Stack(proxy_url=args.proxy_url, token=_token(settings), settings=settings)
        guard = build_guard([settings])
        asyncio.run(_run_measure(args, stack, out, guard, only or list(SCENARIOS), repeat))
    print(f"[run] 완료 {time.monotonic() - started:.1f}s — {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="poc_run.py", description="fabrix_proxy 1단계 PoC 시나리오 러너 (plans/148)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--proxy-url", default=DEFAULT_PROXY_URL,
                        help=f"프록시 주소(기본 {DEFAULT_PROXY_URL}) — --dry-run이면 무시")
    common.add_argument("--out", default=None,
                        help="결과 디렉터리(기본 <repo>/logs/fabrix_proxy_poc/<시각>/ · "
                             "같은 값을 주면 덧붙인다)")
    common.add_argument("--dry-run", action="store_true",
                        help="가짜 KBGenAI + 프록시를 하위 프로세스로 띄워 돈다(실 LLM 0건)")

    env = sub.add_parser("env-check", parents=[common],
                         help="패키지 버전·FabriX 도달·프록시·녹화 필드 대조 → env_check.md")
    env.set_defaults(func=cmd_env_check)

    run = sub.add_parser("run", parents=[common], help="시나리오 측정 → results.jsonl·summary.md")
    run.add_argument("--only", default=None, help="쉼표 구분 시나리오(S1~S6·F5) — 기본 전부")
    run.add_argument("--repeat", default=None,
                     help="N(전 시나리오) 또는 S1=10,S4=10(지정분만) — 기본 S1 20·S2 20·"
                          "S3 10+10·S4 20·S5 10·S6 10·F5 각 3")
    run.add_argument("--contents-mode", choices=("turns", "transcript"), default=None)
    run.add_argument("--protocol-lang", choices=LANGS, default=None)
    run.add_argument("--protocol-file", default=None, help="규약 템플릿 파일(인라인 전송)")
    run.add_argument("--fewshot", choices=FEWSHOT_MODES, default=None)
    run.add_argument("--fewshot-placement", choices=("system", "contents"), default=None)
    run.add_argument("--fewshot-file", default=None, help="few-shot 데이터 파일(인라인 전송)")
    run.add_argument("--repair-max", type=int, default=None)
    run.add_argument("--passthrough", action="store_true",
                     help="네이티브 엔드포인트 passthrough(프록시 FABRIX_NATIVE_URL 필요)")
    run.add_argument("--concurrency", type=int, default=1, help="동시 케이스 수(기본 1)")
    run.add_argument("--sleep", type=float, default=0.0, help="호출 간 대기 초(기본 0)")
    run.set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점."""
    args = build_parser().parse_args(argv)
    try:
        code: int = args.func(args)
        return code
    except ExportCheckError as exc:
        print(f"반출물 자기 검사 실패 — {exc.path.name}: 규칙 {', '.join(exc.rules)}"
              " (파일을 쓰지 않았다)", file=sys.stderr)
        return 3
    except UsageError as exc:
        print(f"사용법·설정 오류: {exc}", file=sys.stderr)
        return 2
    except RunError as exc:
        print(f"실행 실패: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
