"""문서군 인가 (plans/126 §4.10 · G-2 · W5).

정책은 두 줄이다:

  - **비민감 문서군** — 조회 가능한 모든 사용자에게 열린다(사내 규정·설계문서는 공개 자료다).
  - **민감 문서군**(`sensitive: true`) — **명시 허용된 사용자만**. 관리자는 전체 허용이고,
    그 밖에는 설정(`RAG_SENSITIVE_ALLOWED_USERS`)에 이름이 있어야 한다.

왜 «관리자면 전부»인가: 1차의 모든 진입(운영자 API·관리자 화면·서버 셸 CLI)이 이미 관리자
권한을 요구한다. 열려 있는 유일한 비관리자 진입은 T-4 명시 접두(`/문서 …`, 기본 off)이고,
그 경로가 민감 문서군에 닿는 것을 막는 것이 이 모듈의 실질적 목적이다.

`has_permission=false` 결과 제외(플랫폼 권한)는 **클라이언트가 이미 한다**(§4.2) — 여기는
«어느 문서군을 열어 줄지»라는 우리 쪽 판정만 한다. 두 층이 함께 필요하다: 플랫폼은 문서 단위를,
우리는 문서군 단위를 본다.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from src.infrastructure.doc_sources import DocCollection

logger = logging.getLogger(__name__)

ADMIN_ROLES = frozenset({"admin", "operator"})


def _is_admin(user: Mapping[str, Any] | None) -> bool:
    if not user:
        return False
    role = str(user.get("role") or user.get("type") or "").lower()
    if role in ADMIN_ROLES:
        return True
    roles = user.get("roles")
    if isinstance(roles, (list, tuple, set)):
        return any(str(r).lower() in ADMIN_ROLES for r in roles)
    return bool(user.get("is_admin"))


def _username(user: Mapping[str, Any] | None) -> str:
    if not user:
        return ""
    return str(user.get("username") or user.get("sub") or user.get("user_id") or "").strip()


def parse_allowed_users(raw: str | None) -> tuple[str, ...]:
    """`RAG_SENSITIVE_ALLOWED_USERS` 파싱(쉼표 구분). 빈 값이면 **아무도 없다**.

    «빈 값 = 전체 허용»으로 두지 않는다 — 민감 자료의 기본값은 닫힘이어야 한다(D-232 정합).
    """
    if not raw:
        return ()
    return tuple(part.strip() for part in str(raw).split(",") if part.strip())


def allowed_collection_ids(
    collections: Sequence[DocCollection],
    *,
    user: Mapping[str, Any] | None,
    rag_config: Any = None,
) -> list[str]:
    """이 사용자가 열 수 있는 문서군 id 목록.

    Args:
        collections: 후보 문서군(정본 + 접속 정보 결합본).
        user: 호출자(`require_user`·`require_admin_user` 가 넘기는 dict). None 이면
            **비관리자**로 본다(서버 셸 CLI 는 이 함수를 쓰지 않고 전체를 본다).
        rag_config: `AppConfig.rag` — 민감 허용 목록을 읽는다.
    """
    if _is_admin(user):
        return [c.id for c in collections]

    allowed_users = parse_allowed_users(getattr(rag_config, "sensitive_allowed_users", ""))
    name = _username(user)
    out: list[str] = []
    blocked: list[str] = []
    for col in collections:
        if not col.sensitive:
            out.append(col.id)
        elif name and name in allowed_users:
            out.append(col.id)
        else:
            blocked.append(col.id)
    if blocked:
        logger.info(
            "민감 문서군 접근 차단 — user=%s 차단=%s (허용 목록 RAG_SENSITIVE_ALLOWED_USERS)",
            name or "(미식별)", blocked,
        )
    return out
