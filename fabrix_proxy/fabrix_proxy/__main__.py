"""프록시 엔트리 — `python -m fabrix_proxy [--host H] [--port P]` (plans/148 §3.8).

설정 로드 → 토큰 확인(비면 종료코드 2) → 기동 로그 1줄(자격증명·업스트림 URL 미포함) → uvicorn.
"""

from __future__ import annotations

import argparse
import logging
import sys

import uvicorn
from pydantic import ValidationError

from fabrix_proxy.app import create_app
from fabrix_proxy.config import ConfigError, ProxySettings

logger = logging.getLogger("fabrix_proxy")


def main(argv: list[str] | None = None) -> int:
    """프록시를 기동한다. 설정 오류면 값 없이 사유만 출력하고 0이 아닌 코드로 끝낸다."""
    parser = argparse.ArgumentParser(prog="fabrix_proxy")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)

    try:
        settings = ProxySettings()
    except ValidationError as exc:
        fields = ", ".join(
            ".".join(str(p) for p in err["loc"]) for err in exc.errors(include_input=False)
        )
        print(f"fabrix_proxy 설정 오류: {fields}", file=sys.stderr)
        return 2
    level = getattr(logging, settings.fabrix_proxy_log_level.upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # httpx·httpcore 요청 URL 로그 억제는 create_app이 한다(fabrix_client.quiet_http_loggers).

    try:
        app = create_app(settings)
    except ConfigError as exc:
        print(f"fabrix_proxy 기동 거부: {exc}", file=sys.stderr)
        return 2

    host = args.host or settings.fabrix_proxy_host
    port = args.port or settings.fabrix_proxy_port
    logger.info(
        "fabrix_proxy start backend=fabrix host=%s port=%d aliases=%s contents_mode=%s "
        "fewshot=%s poc_mode=%s",
        host,
        port,
        ",".join(settings.fabrix_proxy_model_aliases),
        settings.fabrix_proxy_contents_mode,
        settings.fabrix_proxy_fewshot,
        settings.fabrix_proxy_poc_mode,
    )
    uvicorn.run(app, host=host, port=port, log_level=settings.fabrix_proxy_log_level.lower())
    return 0


if __name__ == "__main__":
    sys.exit(main())
