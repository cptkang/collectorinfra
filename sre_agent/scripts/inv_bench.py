"""장애 조사 품질 벤치마크 실행 진입 (plans/151 §4.1 · §9.3).

`sre_agent/scripts`에는 `__init__.py`가 없어 패키지로 import되지 않는다. 이 파일이 자기 디렉터리를
import 경로에 올리고 `investigation_bench.__main__.main`을 부른다.

    cd sre_agent && .venv/bin/python scripts/inv_bench.py --schema-snapshot \\
        --polestar-url http://127.0.0.1:19197/sse --apm-url http://127.0.0.1:19196/sse
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = str(Path(__file__).resolve().parent)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from investigation_bench.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
