"""reload 없는 서버 기동 진입점 (plans/94 §4.2 "기동 경로 정정").

러너는 `python -m src.main --server` 를 쓰지 않는다. `src/main.py:106` 이 reload=True 를
하드코딩해 **리로더 부모 + 워커 자식 2프로세스**로 뜨기 때문이다. 그러면

  - 사다리 확정 로그와 실효 설정이 **자식** 것이고,
  - 부모만 종료하면 **자식이 고아로 남아 포트를 붙들며**(Windows에서 정리 비용이 더 크다),
  - 리로더가 런 중간에 재기동할 여지가 있다.

셋 다 측정을 조용히 망가뜨린다. `scripts/diag_server.py` 가 같은 이유로 이미 있다(D-198).
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def main() -> None:
    import uvicorn

    from src.api.server import app
    from src.config import load_config

    config = load_config()
    # 관측 대상 단일화 - reload=False 이므로 app 객체를 직접 넘긴다.
    uvicorn.run(app, host=config.server.host, port=config.server.port, reload=False)


if __name__ == "__main__":
    main()
