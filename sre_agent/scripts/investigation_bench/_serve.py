"""벤치 전용 sre_agent 진입점 (plans/151 §4.7).

운영 진입점 `run_service.py`는 포트가 상수(`DEFAULT_PORT = 9098`)라 벤치 포트를 줄 수 없다.
이 파일은
`create_service(settings)` → `build_asgi_app(mcp, None)` → `uvicorn.run(127.0.0.1, --port)`만 하고
제품 코드는 바꾸지 않는다(`scripts/itam_bench/_serve.py` 전례).

- 기동 직후 **실효 설정 에코**(비밀 필드 값 제외)를 `<cwd>/settings_echo.json`에 쓴다(D-219 ④ ·
  D-238 ⑤ — 에코 없는 실험군은 판정에서 제외). 비밀 필드는 값 대신 「비어 있지 않음」 여부만 싣는다.
- 설정은 OS 환경변수로만 받는다. 러너가 빈 디렉터리를 cwd로 주므로 설정의 `.env`·`.encenv`가 없고,
  `LITELLM_MODE=PRODUCTION`으로 litellm의 상위 `.env` 적재도 끈다. 그래도 섞였는지는 에코의
  `non_default_fields`(기본값과 다른 필드 이름)를 러너가 명세와 대조해 잡는다.
- 이 파일은 sre_agent 패키지를 import하는 **유일한** 벤치 파일이다(실험군 프로세스 안에서만 돈다).
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import uvicorn
from pydantic import SecretStr
from sre_agent.interface.mcp_service import DEFAULT_HOST, build_asgi_app, create_service
from sre_agent.settings import AgentSettings

ECHO_FILE = "settings_echo.json"


def settings_echo(settings: AgentSettings) -> dict[str, Any]:
    """실효 설정 → 에코 dict. 비밀 필드(SecretStr)는 값을 빼고 `secret_fields_set`에 여부만 둔다."""
    echo: dict[str, Any] = {}
    secrets: dict[str, bool] = {}
    for name in type(settings).model_fields:
        value = getattr(settings, name)
        if isinstance(value, SecretStr) or name.endswith(("_key", "_token")):
            secrets[name] = bool(value.get_secret_value()) if isinstance(value, SecretStr) \
                else bool(value)
            continue
        echo[name] = value
    echo["secret_fields_set"] = secrets
    echo["non_default_fields"] = non_default_fields(settings)
    return echo


def non_default_fields(settings: AgentSettings) -> list[str]:
    """필드 기본값과 다른 실효 값의 필드 이름(값은 싣지 않는다 — 비밀 필드 포함)."""
    out = []
    for name, info in type(settings).model_fields.items():
        value = getattr(settings, name)
        default = info.get_default(call_default_factory=True)
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if isinstance(default, SecretStr):
            default = default.get_secret_value()
        if value != default:
            out.append(name)
    return sorted(out)


def main(argv: Sequence[str] | None = None) -> int:
    """실험군 sre_agent 하나를 벤치 포트로 띄운다."""
    p = argparse.ArgumentParser(prog="investigation_bench._serve")
    p.add_argument("--port", type=int, required=True)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    settings = AgentSettings()
    mcp = create_service(settings)
    app = build_asgi_app(mcp, None)
    Path.cwd().joinpath(ECHO_FILE).write_text(
        json.dumps(settings_echo(settings), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    uvicorn.run(app, host=DEFAULT_HOST, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
