"""제니퍼 소스 식별자 (plans/87 J8 §0.13 M-2·M-7 · D-287 ②).

소스 = APM 뷰 서버 하나(Open API 기준 URL + 토큰 1쌍). 인스턴스 식별은
(`source_id`, `domain_id`, `instance_id`)이다 — 도메인 id는 서버마다 따로 매겨 겹칠 수 있다(S-4).
단일 설정(`JENNIFER_API_URL`만)은 소스 `default` 하나이고, 알람 `dbId`·`resourceAncestry`는 v4와
같게 둔다(존 없는 소스).
"""

from __future__ import annotations

import re

DEFAULT_SOURCE_ID = "default"
# `default`는 단일 설정 소스 · `api`는 접두 키 `JENNIFER_API_*`(전역 키)와 헷갈린다.
RESERVED_SOURCE_IDS: frozenset[str] = frozenset({DEFAULT_SOURCE_ID, "api"})
_SOURCE_ID = re.compile(r"^[a-z][a-z0-9_]{0,15}$")


def source_id_error(source_id: str) -> str | None:
    """설정에 적은 소스 id의 형식 오류 사유(정상이면 None)."""
    if not _SOURCE_ID.match(source_id):
        return f"소스 id {source_id!r}는 소문자 슬러그 [a-z][a-z0-9_]{{0,15}}여야 한다"
    if source_id in RESERVED_SOURCE_IDS:
        return f"소스 id {source_id!r}는 예약어다({sorted(RESERVED_SOURCE_IDS)})"
    return None


def alarm_db_id(source: str, source_id: str) -> str:
    """알람 `dbId` — `<source>_<source_id>` · 단일 설정 `default`는 `<source>`(v4 그대로)."""
    return source if source_id == DEFAULT_SOURCE_ID else f"{source}_{source_id}"
