"""MCP 도구 응답 공통 헬퍼 (plans/166 M5).

폴스타·PromQL 도구 모듈에 같은 본문으로 복제돼 있던 응답 헬퍼를 한 곳에 둔다. 각 도구 모듈은
종전 이름(`_now_iso`·`_err`)으로 재노출한다 —
테스트의 `monkeypatch.setattr(<모듈>, "_now_iso", …)`가 그 모듈의 `_ok`에 그대로 닿는다.

합치지 않은 것: `_ok`(도구군별 필드 배치가 다름 — rows/row_count vs data/result_count)·
`_audit`(PromQL·OpenMetrics 로그 문구·인자가 다름)는 각 모듈에 남긴다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime


def now_iso() -> str:
    """queried_at용 UTC ISO 타임스탬프를 반환한다."""
    return datetime.now(UTC).isoformat()


def err(message: str) -> str:
    """오류 반환 JSON 문자열을 만든다({error})."""
    return json.dumps({"error": message}, ensure_ascii=False)
