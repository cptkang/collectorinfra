"""폴스타 DB 어댑터 패키지 (Plan 63 P2, D-089).

임포트 시 폴스타 어댑터를 레지스트리에 등록한다(죽은 레지스트리 방지 — 배선 테스트로 고정).
"""

from src.db_adapters import register
from src.db_adapters.polestar.adapter import PolestarAdapter

register(PolestarAdapter())
