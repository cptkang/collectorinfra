"""옛 경로 shim — 정본은 ``src.pipeline.key_bridge``(plans/166 W2 N3 · D-337 ①).

``sys.modules`` 별칭이라 옛 경로 import·patch가 새 모듈 객체에 그대로 닿는다.
다음 계획에서 옛 경로 참조 0을 확인한 뒤 제거한다.
"""

import sys
from typing import TYPE_CHECKING

from src.pipeline import key_bridge as _impl

if TYPE_CHECKING:
    from src.pipeline.key_bridge import *  # noqa: F401,F403

sys.modules[__name__] = _impl
