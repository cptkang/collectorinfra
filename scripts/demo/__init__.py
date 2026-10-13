"""시연용 세션 실행기 — 떠 있는 웹 서버에 시연 세션을 질의해 웹 대화 이력으로 남긴다.

`scripts.scenario`(판정·측정용 · 자식 서버 기동 · 체크포인트 격리)와 별개다. 이 스크립트는
서버를 띄우지 않고 **이미 떠 있는 서버**에 질의하므로, 남은 대화를 웹에서 열어 그대로
이어 물을 수 있다. 판정은 하지 않는다 — 응답 요약·소요 시간만 남긴다.

사용법: `docs/35_demo_script_guide.md`
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = REPO_ROOT / "testdata" / "demo"
