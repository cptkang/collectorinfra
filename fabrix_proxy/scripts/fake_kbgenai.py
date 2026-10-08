"""가짜 FabriX KBGenAI 서버(대본 모드) — 계약·종단 테스트와 러너 `--dry-run`·리허설용 (plans/148).

실 LLM을 부르지 않는다. 받은 KBGenAI 페이로드(`systemPrompt`·`contents`)를 대본 규칙과 맞춰
정해진 KBGenAI 응답 dict를 돌려준다.

CLI::

    python scripts/fake_kbgenai.py --host 127.0.0.1 --port <N> --script <대본.json> \
        [--record-dir <DIR>]

- `--port 0`이면 빈 포트를 고른다. 포트 지정 여부와 무관하게 리슨 직후 stdout 첫 줄에
  `FAKE_KBGENAI_READY port=<N>`을 출력한다.
- `POST /`·`POST /{path}` 모두 받는다(엔드포인트 URL 경로 무관). `GET /health`는 생존 확인.

대본 JSON::

    {"rules": [{"when": {"system_contains": str?, "last_contains": str?,
                         "any_contains": str?, "not_contains": str?},
                "respond": {...KBGenAI 응답 dict...}?, "http_status": int?,
                "delay_s": float?, "times": int?}],
     "default": {...}}

- 위에서부터 첫 매치를 쓴다. `when`의 조건은 모두 AND다(빈 `when`은 항상 매치).
  `system_contains`는 systemPrompt, `last_contains`는 contents 마지막 원소,
  `any_contains`·`not_contains`는 systemPrompt + contents 전체를 본다.
- `times`가 있으면 그 횟수만큼 매치한 뒤 소진된다.
- `respond`의 문자열 값 안 `{{SEQ}}`는 요청 일련번호(1부터)로 치환한다.
- 매치가 없고 `default`도 없으면 `{"status": "SUCCESS", "content": ""}`.
- `--record-dir`를 주면 요청마다 `req_<seq>.json`(경로 · 헤더 **이름**만 · 페이로드)을 남긴다.
  헤더 값(자격증명)은 저장하지 않는다.

`src`·`noise_gate` 등 본체 패키지를 import하지 않는다(`tests/test_boundary.py`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

READY_PREFIX = "FAKE_KBGENAI_READY"
SEQ_TOKEN = "{{SEQ}}"
EMPTY_RESPONSE: dict[str, Any] = {"status": "SUCCESS", "content": ""}


def _substitute(value: Any, seq: int) -> Any:
    """응답 값 안의 `{{SEQ}}`를 일련번호로 바꾼다(중첩 dict·list 포함)."""
    if isinstance(value, str):
        return value.replace(SEQ_TOKEN, str(seq))
    if isinstance(value, dict):
        return {k: _substitute(v, seq) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, seq) for v in value]
    return value


def _texts(payload: Any) -> tuple[str, list[str]]:
    """페이로드에서 (systemPrompt, contents 문자열 목록)을 꺼낸다. 형식이 틀리면 빈 값."""
    if not isinstance(payload, dict):
        return "", []
    system = payload.get("systemPrompt")
    contents = payload.get("contents")
    items = [str(c) for c in contents] if isinstance(contents, list) else []
    return (system if isinstance(system, str) else ""), items


def _matches(when: dict[str, Any], system: str, contents: list[str]) -> bool:
    """규칙 조건(모두 AND)을 판정한다."""
    everything = system + "\n" + "\n".join(contents)
    last = contents[-1] if contents else ""
    checks = (
        ("system_contains", lambda s: s in system),
        ("last_contains", lambda s: s in last),
        ("any_contains", lambda s: s in everything),
        ("not_contains", lambda s: s not in everything),
    )
    for key, check in checks:
        needle = when.get(key)
        if needle is not None and not check(str(needle)):
            return False
    return True


def validate_script(script: Any) -> dict[str, Any]:
    """대본 형식을 확인한다. 틀리면 ValueError."""
    if not isinstance(script, dict):
        raise ValueError("대본은 JSON 객체여야 한다")
    rules = script.get("rules", [])
    if not isinstance(rules, list):
        raise ValueError("rules는 배열이어야 한다")
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError(f"rules[{i}]는 객체여야 한다")
        if not isinstance(rule.get("when", {}), dict):
            raise ValueError(f"rules[{i}].when은 객체여야 한다")
        if "respond" not in rule and "http_status" not in rule:
            raise ValueError(f"rules[{i}]에 respond 또는 http_status가 필요하다")
    default = script.get("default")
    if default is not None and not isinstance(default, dict):
        raise ValueError("default는 객체여야 한다")
    return script


def load_script(path: Path) -> dict[str, Any]:
    """대본 파일을 읽는다."""
    return validate_script(json.loads(path.read_text(encoding="utf-8")))


def create_fake_app(script: dict[str, Any], record_dir: Path | None = None) -> FastAPI:
    """대본으로 응답하는 가짜 KBGenAI 앱을 만든다(테스트 in-process 용으로도 공개).

    Args:
        script: 대본(`rules`·`default`).
        record_dir: 요청 기록 디렉터리. None이면 기록하지 않는다.
    """
    validate_script(script)
    rules: list[dict[str, Any]] = [dict(r) for r in script.get("rules", [])]
    remaining: list[int | None] = [r.get("times") for r in rules]
    default: dict[str, Any] = script.get("default") or EMPTY_RESPONSE
    state = {"seq": 0}
    if record_dir is not None:
        record_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def pick(system: str, contents: list[str]) -> dict[str, Any] | None:
        for i, rule in enumerate(rules):
            if remaining[i] is not None and remaining[i] <= 0:  # type: ignore[operator]
                continue
            if _matches(rule.get("when") or {}, system, contents):
                if remaining[i] is not None:
                    remaining[i] -= 1  # type: ignore[operator]
                return rule
        return None

    async def handle(request: Request) -> JSONResponse:
        state["seq"] += 1
        seq = state["seq"]
        raw = await request.body()
        try:
            payload: Any = json.loads(raw)
        except ValueError:
            payload = {"_raw": raw.decode("utf-8", errors="replace")}
        if record_dir is not None:
            record = {
                "seq": seq,
                "path": request.url.path,
                "header_names": sorted(request.headers.keys()),
                "payload": payload,
            }
            (record_dir / f"req_{seq}.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        system, contents = _texts(payload)
        rule = pick(system, contents)
        if rule is None:
            return JSONResponse(_substitute(default, seq))
        delay = float(rule.get("delay_s") or 0)
        if delay > 0:
            await asyncio.sleep(delay)
        status = int(rule.get("http_status") or 200)
        body = rule.get("respond")
        if body is None:
            body = {"status": "ERROR", "message": f"fake http {status}"}
        return JSONResponse(_substitute(body, seq), status_code=status)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/")
    async def root(request: Request) -> JSONResponse:
        return await handle(request)

    @app.post("/{path:path}")
    async def any_path(path: str, request: Request) -> JSONResponse:
        return await handle(request)

    return app


def main(argv: list[str] | None = None) -> int:
    """가짜 서버를 기동한다. 리슨을 연 직후 READY 줄을 stdout에 낸다."""
    parser = argparse.ArgumentParser(description="가짜 FabriX KBGenAI 서버(대본 모드)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0이면 빈 포트")
    parser.add_argument("--script", type=Path, required=True, help="대본 JSON")
    parser.add_argument("--record-dir", type=Path, default=None, help="요청 기록 디렉터리")
    args = parser.parse_args(argv)

    try:
        script = load_script(args.script)
    except (OSError, ValueError) as exc:
        print(f"대본 로드 실패: {exc}", file=sys.stderr)
        return 2
    app = create_fake_app(script, args.record_dir)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.host, args.port))
    sock.listen(128)
    port = sock.getsockname()[1]
    print(f"{READY_PREFIX} port={port}", flush=True)
    config = uvicorn.Config(app, log_level="warning", access_log=False)
    uvicorn.Server(config).run(sockets=[sock])
    return 0


if __name__ == "__main__":
    sys.exit(main())
