#!/usr/bin/env python3
"""FabriX 직접 탐침 — F1 네이티브 엔드포인트 · F2 contents 규칙 (plans/148 W3b).

프록시를 거치지 않고 FabriX를 직접 부른다(설정은 `ProxySettings`).

    python scripts/probe_fabrix.py native [--url URL …] [--out DIR] [--dry-run]
    python scripts/probe_fabrix.py contents [--repeat N] [--out DIR] [--dry-run]

- `native`(F1): `FABRIX_NATIVE_URL`(있으면) + `--url` 후보마다 OpenAI 형식 `/chat/completions` +
  tools 1개 요청을 1회씩 보내 HTTP 상태와 응답의 `tool_calls` 유무만 판정한다. 인증 헤더는 KBGenAI와
  같은 헤더로 **추정**한다. 결과 `<out>/probe_native.json`(판정값만 · 후보는 출처 이름으로만 적고
  URL은 쓰지 않는다).
- `contents`(F2): `KBGenAIUpstream`으로 (a) contents 2·3·4원소 · 빈 첫 원소 유무별 역할 인식
  (응답에 표지 값이 있으면 인식) (b) 같은 3턴 도구 대화를 `turns`·`transcript`로 직렬화해(프록시
  `convert`·`tool_protocol` 재사용) 기대한 다음 호출을 고르는 비율(`parse_response` 채점)을 잰다
  (각 `--repeat`회, 기본 3). 결과 `<out>/probe_contents.json` + 권장 `CONTENTS_MODE`
  (높은 쪽 · 같으면 turns).
- 두 결과 모두 `poc_run.py`와 같은 자기 검사를 거치며, 끝나면 그 디렉터리의 `summary.md`·
  `recommend.env`를 다시 만든다.
- `--dry-run`: 가짜 KBGenAI를 하위 프로세스로 띄워 같은 흐름을 돈다(실 LLM 0건).

종료 코드는 `poc_run.py`와 같다(0 · 1 · 2 · 3).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import poc_run as common  # noqa: E402
from fabrix_proxy.config import ProxySettings, resolve_path  # noqa: E402
from fabrix_proxy.convert import (  # noqa: E402
    EmulationOptions,
    PreparedRequest,
    build_llm_config,
    build_payload,
    parse_chat_request,
    prepare_request,
)
from fabrix_proxy.fabrix_client import KBGenAIUpstream  # noqa: E402

from fabrix_proxy import tool_protocol as tp  # noqa: E402

F2_FILE = common.SCENARIO_DIR / "poc_f2_contents.json"
NATIVE_PROMPT = "What is the weather in Springfield right now?"
NATIVE_TOOL = {
    "type": "function",
    "function": {
        "name": "lookup_weather",
        "description": "Look up the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}
AUTH_NOTE = "KBGenAI와 같은 헤더(x-openapi-token · x-generative-ai-client) — 추정"
_MISSING_ENDPOINT = (404, 405, 501)


def _has_tool_calls(body: Any) -> bool:
    try:
        calls = body["choices"][0]["message"].get("tool_calls")
    except (KeyError, IndexError, TypeError, AttributeError):
        return False
    return isinstance(calls, list) and bool(calls)


async def _native_once(
    settings: ProxySettings, url: str, guard: common.ExportGuard
) -> dict[str, Any]:
    """후보 URL 1개 — HTTP 상태 · tool_calls 유무."""
    body = {
        "model": settings.fabrix_native_model or settings.fabrix_model,
        "messages": [{"role": "user", "content": NATIVE_PROMPT}],
        "tools": [NATIVE_TOOL],
        "tool_choice": "auto",
        "stream": False,
    }
    headers = KBGenAIUpstream(settings)._headers()
    try:
        async with httpx.AsyncClient(verify=settings.fabrix_verify_ssl) as client:
            resp = await client.post(url, json=body, headers=headers,
                                     timeout=settings.fabrix_timeout)
    except Exception as exc:  # noqa: BLE001 — 연결 실패는 코드로만 남긴다
        return {"http_status": None, "error": common.upstream_error_code(exc),
                "endpoint_exists": False, "tool_calls": False}
    try:
        data = resp.json()
    except ValueError:
        data = None
    guard.note_response(resp.text[:200])
    return {
        "http_status": resp.status_code,
        "error": None if resp.status_code < 400 else f"http_{resp.status_code}",
        "endpoint_exists": resp.status_code not in _MISSING_ENDPOINT,
        "tool_calls": resp.status_code < 400 and _has_tool_calls(data),
    }


async def probe_native(
    settings: ProxySettings, out: Path, guard: common.ExportGuard,
    extra: list[tuple[str, str]],
) -> dict[str, Any]:
    """F1 — `FABRIX_NATIVE_URL` + 추가 후보(출처 이름, URL)."""
    candidates: list[tuple[str, str]] = []
    if settings.fabrix_native_url:
        candidates.append(("FABRIX_NATIVE_URL", settings.fabrix_native_url))
    candidates += extra
    rows = []
    for source, url in candidates:
        row = await _native_once(settings, url, guard)
        rows.append({"source": source, **row})
    result = {
        "probe": "F1",
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "auth_header": AUTH_NOTE,
        "candidates": rows,
    }
    guard.write(out / common.PROBE_NATIVE, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"[probe native] 후보 {len(rows)}개 — tool_calls 있음 "
          f"{sum(1 for r in rows if r['tool_calls'])}개", flush=True)
    return result


def _emulation_options(settings: ProxySettings, mode: str) -> EmulationOptions:
    """F2 (b) 직렬화 옵션 — contents 규칙만 바꾸고 나머지는 설정값."""
    lang = settings.fabrix_proxy_protocol_lang
    pfile = settings.fabrix_proxy_protocol_file
    ffile = settings.fabrix_proxy_fewshot_file
    return EmulationOptions(
        contents_mode=mode,
        protocol_lang=lang,
        protocol_template=tp.load_protocol_template(lang, resolve_path(pfile) if pfile else None),
        fewshot=settings.fabrix_proxy_fewshot,
        fewshot_placement=settings.fabrix_proxy_fewshot_placement,
        fewshot_data=tp.load_fewshot(resolve_path(ffile) if ffile else None),
        repair_max=0,
        passthrough=False,
    )


async def _complete(
    upstream: KBGenAIUpstream, payload: dict[str, Any], guard: common.ExportGuard
) -> tuple[str | None, str | None]:
    """(응답 텍스트, 오류 코드)."""
    try:
        result = await upstream.complete(payload)
    except Exception as exc:  # noqa: BLE001 — 오류는 코드로만 남긴다
        return None, common.upstream_error_code(exc)
    guard.note_response(result.text)
    return result.text, None


async def probe_contents(
    settings: ProxySettings, out: Path, guard: common.ExportGuard, repeat: int = 3
) -> dict[str, Any]:
    """F2 — (a) 원소 역할 인식 (b) turns·transcript 다음 행동 정답률."""
    data = json.loads(F2_FILE.read_text(encoding="utf-8"))
    upstream = KBGenAIUpstream(settings)
    llm_config = build_llm_config(settings.fabrix_llm_config, None, None)
    roles: dict[str, Any] = {}
    for case in data["role_cases"]:
        row: dict[str, Any] = {"contents_len": len(case["contents"]),
                               "empty_first": case["contents"][0] == "",
                               "n": repeat, "recognized": 0, "errors": {}}
        for _ in range(repeat):
            payload = build_payload(
                PreparedRequest(system_prompt=data["role_system_prompt"], turns=[]),
                model_id=settings.fabrix_model, contents_mode="turns", llm_config=llm_config,
            )
            payload["contents"] = list(case["contents"])
            text, error = await _complete(upstream, payload, guard)
            if error:
                row["errors"][error] = row["errors"].get(error, 0) + 1
            elif text and case["expect"].lower() in text.lower():
                row["recognized"] += 1
        roles[case["id"]] = row

    conv = data["tool_conversation"]
    tools = common.load_tools()
    req = parse_chat_request(
        {"model": "probe", "messages": conv["messages"],
         "tools": [tools[n] for n in conv["tools"]]},
        ["probe"], False,
    )
    expect = conv["expect_call"]
    history: dict[str, Any] = {}
    for mode in ("turns", "transcript"):
        prepared = prepare_request(req, _emulation_options(settings, mode))
        payload = build_payload(prepared, model_id=settings.fabrix_model, contents_mode=mode,
                                llm_config=llm_config)
        row = {"n": repeat, "ok": 0, "kinds": {}, "errors": {}}
        for _ in range(repeat):
            text, error = await _complete(upstream, payload, guard)
            if error:
                row["errors"][error] = row["errors"].get(error, 0) + 1
                continue
            parsed = tp.parse_response(text or "", req.tools, req.tool_choice,
                                       req.parallel_tool_calls, prepared.example_tool_names)
            row["kinds"][parsed.kind] = row["kinds"].get(parsed.kind, 0) + 1
            if parsed.kind == "tool_calls" and any(
                c.name == expect["name"] and common.args_match(c.arguments, expect["args"])
                for c in parsed.tool_calls
            ):
                row["ok"] += 1
        history[mode] = row
    recommend = "transcript" if history["transcript"]["ok"] > history["turns"]["ok"] else "turns"
    result = {
        "probe": "F2",
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "repeat": repeat,
        "roles": roles,
        "tool_history": history,
        "recommend_contents_mode": recommend,
    }
    guard.write(out / common.PROBE_CONTENTS,
                json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"[probe contents] turns {history['turns']['ok']}/{repeat} · transcript "
          f"{history['transcript']['ok']}/{repeat} → CONTENTS_MODE={recommend}", flush=True)
    return result


async def _run(args: argparse.Namespace, settings: ProxySettings, out: Path,
               guard: common.ExportGuard, extra: list[tuple[str, str]]) -> None:
    if args.mode == "native":
        await probe_native(settings, out, guard, extra)
    else:
        await probe_contents(settings, out, guard, repeat=args.repeat)


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점."""
    parser = argparse.ArgumentParser(
        prog="probe_fabrix.py", description="FabriX 직접 탐침 F1·F2 (plans/148)"
    )
    parser.add_argument("mode", choices=("native", "contents"),
                        help="native = F1 네이티브 엔드포인트 · contents = F2 contents 규칙")
    parser.add_argument("--out", default=None,
                        help="결과 디렉터리(기본 <repo>/logs/fabrix_proxy_poc/<시각>/ · "
                             "poc_run.py와 같은 값을 주면 summary에 반영된다)")
    parser.add_argument("--repeat", type=int, default=3, help="contents 사례별 반복(기본 3)")
    parser.add_argument("--url", action="append", default=[],
                        help="native 추가 후보 URL(여러 번 줄 수 있다)")
    parser.add_argument("--dry-run", action="store_true",
                        help="가짜 KBGenAI를 하위 프로세스로 띄워 돈다(실 LLM 0건)")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat는 1 이상이어야 한다")
    extra = [(f"--url#{i}", url) for i, url in enumerate(args.url, 1)]
    try:
        out = common.resolve_out(args.out)
        if args.dry_run:
            with common.dry_stack(with_proxy=False) as stack:
                guard = common.build_guard([stack.settings])
                extra.append(("dry-run#closed-port", common._closed_url()))
                asyncio.run(_run(args, stack.settings, out, guard, extra))
                common.rebuild_reports(out, guard)
        else:
            settings = common.load_settings()
            guard = common.build_guard([settings])
            for _, url in extra:  # --url 후보 호스트도 반출물 검사 대상
                host = urlparse(url).hostname
                if host:
                    guard.hosts[f"--url#{len(guard.hosts)}"] = host
            asyncio.run(_run(args, settings, out, guard, extra))
            common.rebuild_reports(out, guard)
    except common.ExportCheckError as exc:
        print(f"반출물 자기 검사 실패 — {exc.path.name}: 규칙 {', '.join(exc.rules)}"
              " (파일을 쓰지 않았다)", file=sys.stderr)
        return 3
    except common.UsageError as exc:
        print(f"사용법·설정 오류: {exc}", file=sys.stderr)
        return 2
    except common.RunError as exc:
        print(f"실행 실패: {exc}", file=sys.stderr)
        return 1
    print(f"[probe] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
