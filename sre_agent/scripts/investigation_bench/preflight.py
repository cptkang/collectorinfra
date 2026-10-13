"""사전 점검 — 과금 평면 차단 · LLM 엔드포인트 · 포트 · 버전 (plans/151 §6.4 · §6.7 · D-324 ⑤).

판정 입력은 **실행 명세와 sre_agent 설정 필드 이름**뿐이다. 명세 `llm.model`·`llm.api_base`가 실험군
환경변수 `MODEL`·`API_BASE`가 되고 sre_agent가 그것을 `AgentSettings.model`·`.api_base` 필드로
읽는다
(실 소비 필드 — `investigation_llm_model`이 아니다). 프로세스 환경변수는 읽지 않는다(`.env`는
프로세스 환경에 주입되지 않는다 — 2026-08-28 실수 이력). 실험군이 뜬 뒤에는 러너가 실효 설정
에코(`settings_echo.json`)의 `model`·`api_base`가 여기서 판정한 값과 같은지 다시 본다.

과금 판정(§6.4):
- `openai/…` + `api_base`가 127.0.0.1/localhost → 허용. `--site local`이면 백엔드를 `mlx`로
  기록하고,
  `closed`면 명세 `llm.backend`(vllm·fabrix_proxy)를 기록한다.
- 사설 주소·내부 호스트 → 명세 `run.site`와 CLI `--site`가 **둘 다 closed**일 때만 허용.
- `gemini/…`·`anthropic/…`(그 밖의 비 `openai` 프로바이더) · `api_base` 없음
  (프로바이더 기본 = SaaS) · 공용 API 도메인 · 공인 IP → 거부(exit 2).
  `--allow-billed "<승인 문구>"`가 있으면 통과시키고 문구를
  결과(→ manifest)에 남긴다(D-127 · D-240 — 건별 승인).
"""

from __future__ import annotations

import importlib.metadata
import ipaddress
import os
import platform
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from urllib.parse import urlparse

import httpx

EXIT_REJECT = 2
EXIT_FAIL = 1

BACKENDS: tuple[str, ...] = ("fake", "mlx", "vllm", "fabrix_proxy")
SITES: tuple[str, ...] = ("local", "closed")
ALLOWED_PROVIDER = "openai"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
# 공용 LLM API 도메인(접미 일치) — 사설·내부 호스트 판정보다 먼저 거부한다.
PUBLIC_API_DOMAINS: tuple[str, ...] = (
    "googleapis.com", "google.com", "anthropic.com", "claude.ai", "openai.com",
    "azure.com", "openrouter.ai", "mistral.ai", "cohere.ai", "cohere.com", "groq.com",
    "together.xyz", "deepseek.com", "x.ai",
)
BENCH_PORT_MIN, BENCH_PORT_MAX = 19180, 19299
FAKE_LLM_PORT = 19180
GPU_NOT_COLLECTED = "미수집"
VERSION_PACKAGES: tuple[str, ...] = ("holmesgpt", "litellm", "mcp")
ROUNDTRIP_TOOL = {
    "type": "function",
    "function": {
        "name": "bench_ping",
        "description": "Benchmark preflight probe. Call it with value 'ok'.",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    },
}


class PreflightError(Exception):
    """사전 점검 실패 — `exit_code`(2 = 과금·명세 거부 · 1 = 그 밖의 실패)."""

    def __init__(self, message: str, exit_code: int = EXIT_FAIL) -> None:
        super().__init__(message)
        self.exit_code = exit_code


# ─────────────────────────────── 명세 · 과금 ───────────────────────────────


def check_spec(spec: Mapping[str, Any], *, cli_site: str, test: bool) -> None:
    """명세 기본 검사 — `llm.backend` 필수 · `--site` 일치 · `fake`는 `--test`만."""
    llm = spec.get("llm") or {}
    backend = llm.get("backend")
    if backend not in BACKENDS:
        raise PreflightError(
            f"명세 llm.backend가 없거나 모르는 값이다: {backend!r} (허용: {', '.join(BACKENDS)})",
            EXIT_REJECT)
    spec_site = (spec.get("run") or {}).get("site", "local")
    if spec_site not in SITES or cli_site not in SITES:
        raise PreflightError(f"site는 local|closed: 명세 {spec_site!r} · CLI {cli_site!r}",
                             EXIT_REJECT)
    if spec_site != cli_site:
        raise PreflightError(
            f"--site({cli_site})가 명세 run.site({spec_site})와 다르다 — 둘을 맞춰 다시 실행",
            EXIT_REJECT)
    if backend == "fake" and not test:
        raise PreflightError("backend=fake(가짜 LLM)는 --test 없이 쓸 수 없다(테스트 전용)",
                             EXIT_REJECT)


def resolve_host(host: str) -> list[str]:
    """이름 → IP 목록(DNS·hosts 파일). 실패하면 빈 목록."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError):
        return []
    return sorted({str(info[4][0]).split("%", 1)[0] for info in infos})


def _ip_kind(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    if ip.is_loopback:
        return "loopback"
    if ip.is_private or ip.is_link_local:
        return "private"
    return "public_ip"


def _host_kind(host: str, resolve: Callable[[str], list[str]] | None = None) -> str:
    """`loopback` · `private` · `public_ip` · `public_api` · `internal` · `unresolved`.

    - FQDN 끝 점(`….com.`)은 떼고 본다(공용 도메인 목록 우회 방지).
    - 점 없는 단일 이름 = `internal`(사내 호스트).
    - 점 있는 이름은 `resolve`가 주어지면(closed site) 해석해 **모든** 주소가 사설·루프백일 때만
      `internal`이다 — 공인 IP가 섞이면 `public_ip`, 해석 실패면 `unresolved`(둘 다 거부).
      `resolve`가 없으면 `internal`로 두지만 local site는 어차피 `internal`을 거부한다.
    """
    h = host.strip("[]").lower().rstrip(".")
    if h in LOOPBACK_HOSTS:
        return "loopback"
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        if any(h == d or h.endswith("." + d) for d in PUBLIC_API_DOMAINS):
            return "public_api"
        if "." not in h or resolve is None:
            return "internal"
        kinds = set()
        for addr in resolve(h):
            try:
                kinds.add(_ip_kind(ipaddress.ip_address(addr)))
            except ValueError:
                kinds.add("public_ip")
        if not kinds:
            return "unresolved"
        return "public_ip" if "public_ip" in kinds else "internal"
    return _ip_kind(ip)


def check_llm_plane(
    *, model: str, api_base: str | None, backend: str, spec_site: str, cli_site: str,
    test: bool = False, allow_billed: str | None = None,
    resolve: Callable[[str], list[str]] = resolve_host,
) -> dict[str, Any]:
    """과금 평면 판정(§6.4). 통과면 기록 dict, 거부면 `PreflightError(exit 2)`.

    Args:
        model: sre_agent `model` 필드가 될 값(명세 `llm.model`)
        api_base: sre_agent `api_base` 필드가 될 값(명세 `llm.api_base` · fake는 러너가 정한 값)
        backend: 명세 `llm.backend`
        spec_site: 명세 `run.site`
        cli_site: CLI `--site`
        test: `--test`(가짜 LLM 허용)
        allow_billed: `--allow-billed` 승인 문구(없으면 None)
        resolve: 점 있는 호스트 이름 해석(closed site에서만 부른다 · 테스트 주입용)

    Returns:
        `{"model", "api_base", "provider", "host_kind", "backend_recorded", "allow_billed"}`
    """
    provider = model.split("/", 1)[0] if "/" in model else ""
    parsed = urlparse(api_base or "")
    host = parsed.hostname or ""
    closed = spec_site == "closed" and cli_site == "closed"
    kind = _host_kind(host, resolve if closed else None) if host else "none"
    reasons: list[str] = []
    if provider != ALLOWED_PROVIDER:
        reasons.append(f"프로바이더 {provider or '(없음)'!r} — openai 호환(openai/…)만 허용")
    if kind == "none":
        reasons.append("api_base 없음 — 프로바이더 기본 경로(SaaS)로 간다")
    elif kind in ("public_api", "public_ip"):
        reasons.append(f"공용 API 호스트 {host!r}")
    elif kind == "unresolved":
        reasons.append(f"호스트 {host!r}를 해석할 수 없다 — 사설 주소로 확인될 때만 허용")
    elif kind in ("private", "internal") and not closed:
        reasons.append(f"사설·내부 호스트 {host!r}는 run.site와 --site가 둘 다 closed일 때만 허용")
    port = parsed.port
    if not test and (backend == "fake" or (kind == "loopback" and port == FAKE_LLM_PORT)):
        reasons.append(f"가짜 LLM(backend=fake · 포트 {FAKE_LLM_PORT})은 --test 없이 거부")
        # 가짜 LLM 거부는 승인 문구로 풀리지 않는다(측정 오염 방지).
        raise PreflightError("; ".join(reasons), EXIT_REJECT)
    if reasons and not allow_billed:
        raise PreflightError("과금 평면 차단 — " + "; ".join(reasons), EXIT_REJECT)
    if backend == "fake":
        recorded = "fake"
    elif kind == "loopback" and cli_site == "local":
        recorded = "mlx"
    else:
        recorded = backend
    return {
        "model": model,
        "api_base": api_base,
        "provider": provider,
        "host_kind": kind,
        "backend_recorded": recorded,
        "allow_billed": allow_billed or None,
        "billed_override": bool(reasons),
    }


# ─────────────────────────────── 포트 ───────────────────────────────


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """그 포트에 바인딩할 수 없으면 True.

    uvicorn처럼 `SO_REUSEADDR`를 켜고 본다 — 직전 회차 연결의 TIME_WAIT만 남은 포트를 사용 중으로
    오판하지 않는다(macOS는 이 옵션 없이 TIME_WAIT 포트 바인딩을 거부한다).
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return True
    return False


def check_ports(
    ports: Iterable[int], in_use: Callable[[int], bool] = port_in_use
) -> dict[str, Any]:
    """쓸 포트(0 = 임의 빈 포트 — 검사 생략)가 비어 있는지. 하나라도 사용 중이면 중단."""
    planned = sorted({int(p) for p in ports if int(p)})
    busy = [p for p in planned if in_use(p)]
    outside = [p for p in planned if not BENCH_PORT_MIN <= p <= BENCH_PORT_MAX]
    if busy:
        raise PreflightError(f"벤치 포트 사용 중: {busy} — 남은 프로세스는 --cleanup <run_dir>")
    return {"planned": planned, "busy": busy, "outside_bench_range": outside}


# ─────────────────────────────── LLM 엔드포인트 ───────────────────────────────


def _client(client: httpx.Client | None, timeout: float) -> httpx.Client:
    # 루프백·사내 주소로 가는 요청이 셸 프록시를 타지 않게 한다(curl --noproxy '*'와 같은 뜻).
    return client or httpx.Client(timeout=timeout, trust_env=False)


def _headers(api_key: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key or 'dummy'}"}


def fetch_models(api_base: str, api_key: str | None = None,
                 client: httpx.Client | None = None, timeout: float = 10.0) -> dict[str, Any]:
    """`GET <api_base>/models` — 서빙 정보(vLLM이면 `max_model_len`)."""
    c = _client(client, timeout)
    try:
        resp = c.get(api_base.rstrip("/") + "/models", headers=_headers(api_key))
        resp.raise_for_status()
        body = resp.json()
    except Exception as e:
        raise PreflightError(f"/v1/models 실패: {type(e).__name__}: {e}") from e
    finally:
        if client is None:
            c.close()
    data = body.get("data") if isinstance(body, Mapping) else None
    entries = data if isinstance(data, list) else []
    return {
        "models": [e.get("id") for e in entries if isinstance(e, Mapping)],
        "max_model_len": next((e.get("max_model_len") for e in entries
                               if isinstance(e, Mapping) and e.get("max_model_len")), None),
        "raw": body,
    }


def tool_call_roundtrip(api_base: str, model: str, api_key: str | None = None,
                        client: httpx.Client | None = None,
                        timeout: float = 60.0) -> dict[str, Any]:
    """도구 하나를 준 chat 요청 1회 → `tool_calls`가 와야 통과(실패 = 실행 전 중단)."""
    served = model.split("/", 1)[1] if model.startswith(ALLOWED_PROVIDER + "/") else model
    body = {
        "model": served,
        "messages": [{"role": "user", "content": "Call the bench_ping tool with value 'ok'."}],
        "tools": [ROUNDTRIP_TOOL],
        "tool_choice": "auto",
        "max_tokens": 256,
        "temperature": 0,
    }
    c = _client(client, timeout)
    try:
        resp = c.post(api_base.rstrip("/") + "/chat/completions", json=body,
                      headers=_headers(api_key))
        resp.raise_for_status()
        out = resp.json()
    except Exception as e:
        raise PreflightError(f"tool-calling 왕복 실패: {type(e).__name__}: {e}") from e
    finally:
        if client is None:
            c.close()
    try:
        message = out["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise PreflightError(f"tool-calling 왕복 응답 모양 오류: {out!r:.300}") from e
    calls = message.get("tool_calls") or []
    if not calls:
        raise PreflightError(
            "tool-calling 왕복 실패 — 백엔드가 tool_calls를 돌려주지 않았다"
            f"(content={str(message.get('content'))[:200]!r})"
            " · vLLM이면 --enable-auto-tool-choice 확인")
    return {"ok": True, "tool": calls[0].get("function", {}).get("name"),
            "usage": out.get("usage")}


def expected_calls(n_worlds: int, k: int, n_arms: int, max_steps: int) -> int:
    """예상 LLM 호출 상한 — 세계 × k × 실험군 × MAX_STEPS(§6.7 ④)."""
    return n_worlds * k * n_arms * max_steps


# ─────────────────────────────── 호스트 · 버전 ───────────────────────────────


def _nvidia(args: list[str], run: Callable[..., Any]) -> list[list[str]]:
    out = run(["nvidia-smi", *args, "--format=csv,noheader,nounits"],
              capture_output=True, text=True, timeout=15, check=False)
    return [[c.strip() for c in line.split(",")]
            for line in (out.stdout or "").splitlines() if line.strip()]


def gpu_info(which: Callable[[str], str | None] = shutil.which,
             run: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """GPU 모델·장수·드라이버·사용 프로세스. `nvidia-smi`가 없으면 `{"status": "미수집"}`."""
    if which("nvidia-smi") is None:
        return {"status": GPU_NOT_COLLECTED}
    try:
        gpus = _nvidia(["--query-gpu=index,name,driver_version,memory.total"], run)
        procs = _nvidia(["--query-compute-apps=pid,process_name,used_memory"], run)
    except Exception as e:  # nvidia-smi 실행 실패 — 진행은 막지 않는다
        return {"status": GPU_NOT_COLLECTED, "error": f"{type(e).__name__}: {e}"}
    return {
        "status": "ok",
        "gpus": [{"index": g[0], "name": g[1], "driver": g[2], "memory_total_mib": g[3]}
                 for g in gpus if len(g) >= 4],
        "processes": [{"pid": p[0], "name": p[1], "used_memory_mib": p[2]}
                      for p in procs if len(p) >= 3],
    }


def gpu_processes(which: Callable[[str], str | None] = shutil.which,
                  run: Callable[..., Any] = subprocess.run) -> list[dict[str, Any]] | str:
    """GPU 사용 프로세스 목록(실행 전후 기록용) — 없으면 「미수집」."""
    info = gpu_info(which, run)
    return info["processes"] if info.get("status") == "ok" else GPU_NOT_COLLECTED


def versions() -> dict[str, Any]:
    """venv·파이썬·holmesgpt/litellm/mcp 버전."""
    out: dict[str, Any] = {"python": sys.version.split()[0], "venv": sys.prefix}
    for pkg in VERSION_PACKAGES:
        try:
            out[pkg] = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            out[pkg] = None
    return out


def host_info(which: Callable[[str], str | None] = shutil.which,
              run: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """실행 호스트 정보(OS · CPU 수 · 호스트명 · GPU) — 호스트명·사용자는 반출 시 가린다."""
    return {
        "os": platform.platform(),
        "hostname": platform.node(),
        "cpu_count": os.cpu_count(),
        "gpu": gpu_info(which, run),
    }


# ─────────────────────────────── 묶음 ───────────────────────────────


def static_checks(
    spec: Mapping[str, Any], *, cli_site: str, test: bool, allow_billed: str | None,
    api_base: str | None, ports: Iterable[int], n_worlds: int,
    in_use: Callable[[int], bool] = port_in_use,
) -> dict[str, Any]:
    """프로세스를 띄우기 전 점검 — 명세 · 과금 평면 · 포트 · 예상 호출 수.

    실패는 `PreflightError`.
    """
    check_spec(spec, cli_site=cli_site, test=test)
    run = spec.get("run") or {}
    llm = spec.get("llm") or {}
    plane = check_llm_plane(
        model=str(llm.get("model") or ""), api_base=api_base, backend=str(llm["backend"]),
        spec_site=str(run.get("site", "local")), cli_site=cli_site, test=test,
        allow_billed=allow_billed,
    )
    port_result = check_ports(ports, in_use)
    max_steps = int((spec.get("base_env") or {}).get("MAX_STEPS", 40))
    n_arms = len(spec.get("arms") or {})
    calls = expected_calls(n_worlds, int(run.get("k", 1)), n_arms, max_steps)
    return {
        "llm_plane": plane,
        "ports": port_result,
        "expected_llm_calls": calls,
        "expected_llm_calls_basis": f"세계 {n_worlds} × k {run.get('k', 1)} × 실험군 {n_arms}"
                                    f" × MAX_STEPS {max_steps}",
    }


def live_checks(
    *, api_base: str, model: str, api_key: str | None, snapshot: Any,
    live_urls: Mapping[str, str | None], client: httpx.Client | None = None,
    which: Callable[[str], str | None] = shutil.which, run: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """LLM 엔드포인트가 뜬 뒤의 점검 — `/v1/models` · tool-calling 왕복 · 스키마 · 호스트 · 버전.

    `/v1/models`·왕복 실패는 `PreflightError`(실행 전 중단). 스키마 대조는 URL이 없으면 「미대조」.
    """
    from investigation_bench import schema

    serving = fetch_models(api_base, api_key, client)
    roundtrip = tool_call_roundtrip(api_base, model, api_key, client)
    return {
        "serving": serving,
        "tool_call_roundtrip": roundtrip,
        "schema": schema.compare_with_live(snapshot, dict(live_urls)),
        "host": host_info(which, run),
        "versions": versions(),
    }
