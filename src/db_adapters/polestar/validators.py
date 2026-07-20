"""폴스타 전용 SQL 검증 (Plan 63 P2, D-089).

query_validator.py의 `_check_routing_filter_misuse`를 분리 이동한 것(동작 불변). 공용
validator는 어댑터 `validator_checks()` 훅을 순회 실행하며, 담당 DB(폴스타)에서만 발동한다
(기존엔 전 DB에서 실행됐으나 폴스타 토큰 부재 시 무동작이라 동작 불변 — Plan 63 §1.1 L4).
"""

from __future__ import annotations

import re


def check_routing_filter_misuse(sql: str) -> list[str]:
    """라우팅 정보를 WHERE 조건에 사용한 패턴을 탐지한다.

    GROUP_PATH는 CMM_RESOURCE의 내부 계층 경로로, Polestar/위치 식별에 사용하면
    항상 0건 조회 또는 SQL 에러가 발생한다.
    Polestar 이름·위치명은 DB 라우팅 단계에서 이미 처리되므로 SQL에 포함되어선 안 된다.

    Args:
        sql: SQL 쿼리

    Returns:
        에러 메시지 목록
    """
    errors: list[str] = []

    # GROUP_PATH를 WHERE/AND/OR 조건에 사용한 패턴 (SELECT alias이므로 WHERE에서 사용 불가)
    if re.search(r"\bGROUP_PATH\s*(?:I?LIKE|=|!=|<>)", sql, re.IGNORECASE):
        errors.append(
            "GROUP_PATH은 SELECT 절의 계산된 별칭(alias)으로 WHERE 조건에서 사용할 수 없습니다. "
            "Polestar/위치 식별 정보는 DB 라우팅 단계에서 이미 처리되었습니다."
        )

    # Polestar 이름을 LIKE/ILIKE 필터로 사용하는 패턴 탐지
    routing_columns = [
        r"RESOURCE_NAME",
        r"CR\.NAME",
        r"A\.RESOURCE_NAME",
        r"AR\.RESOURCE_NAME",
    ]
    polestar_keywords = ["폴스타", "polestar"]
    for col_pat in routing_columns:
        for keyword in polestar_keywords:
            if re.search(
                rf"\b{col_pat}\s+I?LIKE\s+'%{keyword}%'",
                sql,
                re.IGNORECASE,
            ):
                errors.append(
                    f"라우팅 식별자 '{keyword}'를 WHERE 필터로 사용하면 0건 조회됩니다. "
                    "Polestar 이름은 DB 라우팅 단계에서 처리되므로 SQL 조건에 포함하지 마세요."
                )

    return errors
