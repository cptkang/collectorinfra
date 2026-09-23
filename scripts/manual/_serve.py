"""스냅샷 cwd 에서 본체 앱을 기동한다 (plans/116 §4.4).

``MANUAL_MODE=record`` → 실 그래프를 녹화 프록시로 감싼다.
``MANUAL_MODE=replay`` → 실 그래프를 **빌드하지 않고** 재생 그래프로 대체한다(LLM 호출 0).

반드시 스냅샷 디렉터리(``build/manual_capture/app``)를 cwd 로 실행한다 — 설정·정적 자산·
`.env` 가 전부 cwd·패키지 위치 기준이다. 저장소 쪽 `src` 가 잡히면 즉시 중단한다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    import src

    here = Path.cwd().resolve()
    if Path(src.__file__).resolve().parent.parent != here:
        sys.exit(f"스냅샷 밖의 src 가 import 됐다: {src.__file__} (cwd={here})")

    import uvicorn

    import src.api.server as server
    from scripts.manual.replay import ReplayGraph, wrap_for_mode

    real_build = server.build_graph
    if os.environ.get("MANUAL_MODE", "replay") == "record":
        server.build_graph = lambda *a, **k: wrap_for_mode(real_build(*a, **k))
    else:
        server.build_graph = lambda *a, **k: ReplayGraph(Path(os.environ["MANUAL_FIXTURES_DIR"]))

    app = server.create_app()
    port = int(os.environ.get("MANUAL_PORT", "18981"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
