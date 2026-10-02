"""테스트 임포트 부트스트랩 — src/ 없는 nested 레이아웃에서 sre_agent 패키지와 scripts를 임포트 경로에 올린다.

collectorinfra 루트를 cwd로 pytest를 실행해도 `import sre_agent`가 최상위
`sre_agent/`(네임스페이스 디렉토리)가 아니라 그 아래 정규 패키지로 해소되도록,
`sre_agent/`(top)와 `sre_agent/scripts`를 sys.path 앞에 삽입한다.
"""

import sys
from pathlib import Path

_TOP = Path(__file__).resolve().parents[1]  # collectorinfra/sre_agent/ (최상위)

for _p in (_TOP, _TOP / "scripts"):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

# MVP 실행 기록기 — MVP 테스트가 어떤 방식으로 실행되든 대장(docs/24)에 기록이 남는다.
pytest_plugins = ["tests.mvp_record"]
