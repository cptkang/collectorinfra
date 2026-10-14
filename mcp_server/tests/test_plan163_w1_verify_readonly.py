"""plans/163 W1 독립 검증(verifier) — S1 검사기의 3방언 동시 판독 오거부.

`validate_readonly`는 소스 엔진을 모른 채 PG·DB2·MariaDB 세 규칙으로 리터럴을 지운 본문을
모두 검사한다. 그래서 PostgreSQL·DB2에서 정상인 SELECT가 MariaDB 판독(백슬래시 = 이스케이프)
때문에 거부된다. `xfail(strict=True)` — 소스 방언을 넘기도록 고치면 XPASS로 알려 준다.

통과 테스트: 같은 문자열이 MariaDB 판독에서는 실제 탈출이므로 거부되는 것이 맞다는 점과
일반 SELECT 허용을 함께 고정한다. DB 연결 0.
"""

from __future__ import annotations

import pytest

from mcp_server.security import ReadOnlyViolationError, validate_readonly

# PG·DB2에서는 리터럴 'C:\Windows\' 뒤에 정상 조건이 이어지는 SELECT다.
PG_OK_SQL = (
    "SELECT id FROM cmm_alarm WHERE path = 'C:\\Windows\\' "
    "AND descr = 'auto delete temp' FETCH FIRST 10 ROWS ONLY"
)


def test_plain_select_allowed() -> None:
    validate_readonly("SELECT id FROM cmm_alarm WHERE descr = 'delete me' LIMIT 10")


@pytest.mark.xfail(strict=True, raises=ReadOnlyViolationError, reason=(
    "3방언 동시 판독 — MariaDB 규칙으로는 \\' 가 리터럴을 탈출해 `delete`가 본문 키워드로 보인다. "
    "PG·DB2 소스의 정상 SELECT가 거부된다(_guard_readonly가 소스 엔진을 넘기지 않음)"))
def test_pg_backslash_literal_select_not_rejected() -> None:
    validate_readonly(PG_OK_SQL)


@pytest.mark.xfail(strict=True, raises=ReadOnlyViolationError, reason=(
    "따옴표 식별자 \"into\"도 INTO 규칙으로 거부된다(식별자 판독 없음)"))
def test_quoted_identifier_into_not_rejected() -> None:
    validate_readonly('SELECT "into" FROM t')
