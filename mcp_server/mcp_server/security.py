"""읽기 전용 SQL 가드.

MCP 서버 레벨에서 SQL의 읽기 전용 여부를 검증한다.
src/security/sql_guard.py와 독립적으로 자체 구현하여
이중 방어(클라이언트 + 서버)를 구성한다.
"""

from __future__ import annotations

import re

import sqlparse

# 금지 키워드 (대문자) - DML, DDL, DCL, 관리 명령 포함
FORBIDDEN_KEYWORDS: frozenset[str] = frozenset({
    # DML
    "INSERT", "UPDATE", "DELETE", "MERGE", "REPLACE",
    # DDL
    "CREATE", "ALTER", "DROP", "TRUNCATE", "RENAME",
    # DCL
    "GRANT", "REVOKE",
    # 프로시저/함수
    "EXEC", "EXECUTE", "CALL",
    # 관리 명령
    "SHUTDOWN", "KILL",
})


# 폴스타 도메인 deny — execute_sql 옵트인 노출 시에만 추가 검증한다 (§6, D-022/D-028).
# 데이터는 core_config_prop EAV(Vendor/OSType/OSParameter)에 있으므로 아래 lookup
# 테이블 직접 참조와 RESOURCE_CONF_ID↔CONFIGURATION_ID 조인은 잘못된 결과를 낳는다.
POLESTAR_FORBIDDEN_TABLES: frozenset[str] = frozenset({
    "CMM_VENDOR",   # D-028: vendor_id lookup 금지
    "CMM_OS",       # D-028: os_id lookup 금지
    "CMM_OS_PARAM", # D-028: os_param_id lookup 금지
})

# (구) RESOURCE_CONF_ID=CONFIGURATION_ID 조인 금지 정규식은 2026-07-30 제거 —
# 현행 정본이 사용하는 정상 조인을 전건 차단하던 stale 규칙(D-022 재검토 참조).


class ReadOnlyViolationError(Exception):
    """읽기 전용 정책 위반 시 발생하는 예외."""

    def __init__(self, reason: str, sql: str = "") -> None:
        self.reason = reason
        self.sql = sql
        super().__init__(f"읽기 전용 위반: {reason}")


class PolestarDomainViolationError(Exception):
    """폴스타 도메인 금지 패턴(D-022/D-028) 위반 시 발생하는 예외."""

    def __init__(self, reason: str, sql: str = "") -> None:
        self.reason = reason
        self.sql = sql
        super().__init__(f"폴스타 도메인 위반: {reason}")


def _clean_sql(sql: str) -> str:
    """주석을 제거하고 문자열 리터럴을 마스킹한다(내부 키워드 오탐 방지)."""
    # 주석 제거
    sql_clean = sqlparse.format(sql, strip_comments=True)
    # 문자열 리터럴 제거 (내부 키워드 오탐 방지)
    return re.sub(r"'[^']*'", "''", sql_clean)


# 허용 문장 유형 — 여는 괄호 뒤 SELECT 또는 WITH(CTE 본문의 쓰기는 FORBIDDEN_KEYWORDS가 막는다).
_ALLOWED_START = re.compile(r"[\s(]*(SELECT|WITH)\b", re.IGNORECASE)

# SELECT … INTO(PG 테이블 생성 · MariaDB INTO OUTFILE/DUMPFILE · 변수 대입).
_INTO = re.compile(r"\bINTO\b", re.IGNORECASE)

# 행 잠금 절(PG FOR UPDATE/SHARE/NO KEY UPDATE/KEY SHARE · MariaDB LOCK IN SHARE MODE).
_LOCKING_CLAUSE = re.compile(
    r"\bFOR\s+(UPDATE|SHARE|NO\s+KEY\s+UPDATE|KEY\s+SHARE)\b"
    r"|\bLOCK\s+IN\s+SHARE\s+MODE\b",
    re.IGNORECASE,
)

# SELECT 안에서 부작용을 내는 함수(세션 설정 변경 · 백엔드 종료 · 서버 파일 읽기/쓰기 ·
# 시퀀스 전진 · 원격 실행 · 잠금 · 임의 질의 실행 · 통계 초기화 · 트랜잭션 ID 할당 · 인덱스 정비).
# `txid_current`·`pg_current_xact_id`는 바로 뒤가 `(`일 때만 걸려 `_if_assigned`·`_snapshot`
# 같은 읽기 함수는 통과한다. 리터럴·주석을 지운 본문에서 `이름(` 호출
# 형태만 본다.
_SIDE_EFFECT_FUNCTION = re.compile(
    r"\b("
    r"set_config|pg_terminate_backend|pg_cancel_backend|pg_reload_conf|pg_rotate_logfile"
    r"|pg_read_file|pg_read_binary_file|pg_ls_\w+|pg_stat_file|pg_file_write"
    r"|lo_\w+|loread|lowrite|pg_sleep\w*|pg_stat_reset\w*|pg_log_backend_memory_contexts"
    r"|nextval|setval|dblink\w*|pg_advisory\w*|pg_try_advisory\w*|pg_notify"
    r"|pg_switch_wal|pg_create_restore_point|pg_promote|pg_logical_emit_message"
    r"|pg_wal_replay_\w+|pg_backup_\w+|pg_start_backup|pg_stop_backup"
    r"|pg_create_\w*replication_slot|pg_drop_replication_slot|pg_copy_\w*replication_slot"
    r"|pg_replication_origin_\w+|pg_replication_slot_advance|pg_logical_slot_get_\w+"
    r"|\w*_to_xml\w*"
    r"|pg_stat_statements_reset|txid_current|pg_current_xact_id"
    r"|brin_summarize_new_values|brin_summarize_range|brin_desummarize_range"
    r"|gin_clean_pending_list"
    r"|get_lock|release_lock|release_all_locks|load_file|sys_exec|sys_eval|master_pos_wait"
    r"|sleep|benchmark"
    r")\s*\(",
    re.IGNORECASE,
)

# 함수 호출 형태가 아닌 부작용 구문 — MariaDB 사용자 변수 대입(`:=`) · 시퀀스 전진(MariaDB·DB2
# `NEXT VALUE FOR` · DB2 `NEXTVAL FOR`) · DB2 관리 프로시저·파일 패키지(`SYSPROC.` · `ADMIN_CMD` ·
# `UTL_FILE`).
_SIDE_EFFECT_SYNTAX = re.compile(
    r":=|\bNEXT\s+VALUE\s+FOR\b|\bNEXTVAL\s+FOR\b|\bSYSPROC\s*\.|\bADMIN_CMD\b|\bUTL_FILE\b",
    re.IGNORECASE,
)

# PG 유니코드 이스케이프(`U&"…"` · `U&'…'`) 자리 표지 — 이스케이프로 함수 이름을 숨길 수 있어
# 세 엔진 판독 모두에서 거부한다. 원문의 널 문자도 같은 표지로 보여 함께 거부된다.
_UESCAPE_MARK = "\x00"

# 엔진별 주석·리터럴 해석. 엔진마다 다르게 읽히는 구문(`#` · 공백 없는 `--` · `/*! */` ·
# 백슬래시 이스케이프 · `$$`)으로 검사기에서 코드를 숨기지 못하게, 세 엔진 규칙으로 각각 읽은
# 본문을 모두 검사한다.
_DIALECTS: tuple[str, ...] = ("postgresql", "db2", "mariadb")
_IDENT_CHARS = re.compile(r"[\w$]")
_PG_DOLLAR_TAG = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")
_MARIADB_EXEC_COMMENT = re.compile(r"/\*M?!\d*")


def _quoted_end(sql: str, start: int, quote: str, backslash: bool) -> int:
    """`start`(여는 따옴표) 다음부터 닫는 따옴표 바로 뒤 위치를 돌려준다. 미종결이면 끝."""
    i = start + 1
    n = len(sql)
    while i < n:
        ch = sql[i]
        if backslash and ch == "\\":
            i += 2
            continue
        if ch == quote:
            if i + 1 < n and sql[i + 1] == quote:
                i += 2
                continue
            return i + 1
        i += 1
    return n


def _code_text(sql: str, dialect: str) -> str:
    """`dialect` 규칙으로 읽은 코드 본문 — 주석은 공백, 문자열 리터럴은 `''`로 바꾼다.

    따옴표 식별자(PG·DB2 `"…"` · MariaDB 백틱)는 내용을 코드로 남긴다(`"nextval"(…)` 호출 검사).
    """
    out: list[str] = []
    i = 0
    n = len(sql)
    pg = dialect == "postgresql"
    maria = dialect == "mariadb"
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        prev_ident = i > 0 and bool(_IDENT_CHARS.match(sql[i - 1]))
        if (
            ch in "'\"`" and i >= 2 and sql[i - 1] == "&" and sql[i - 2] in "uU"
            and not (i >= 3 and _IDENT_CHARS.match(sql[i - 3]))
        ):
            out.append(_UESCAPE_MARK)
        if ch == "-" and nxt == "-" and (
            not maria or i + 2 >= n or sql[i + 2].isspace()
        ):
            j = sql.find("\n", i)
            i = n if j < 0 else j
            out.append(" ")
        elif maria and ch == "#":
            j = sql.find("\n", i)
            i = n if j < 0 else j
            out.append(" ")
        elif ch == "/" and nxt == "*":
            m = _MARIADB_EXEC_COMMENT.match(sql, i) if maria else None
            if m:
                # MariaDB 실행 주석 — 내용은 실행되는 코드다. 여는 표지만 지운다.
                out.append(" ")
                i = m.end()
                continue
            if pg:
                depth, j = 1, i + 2
                while j < n and depth:
                    if sql.startswith("/*", j):
                        depth, j = depth + 1, j + 2
                    elif sql.startswith("*/", j):
                        depth, j = depth - 1, j + 2
                    else:
                        j += 1
                i = j
            else:
                j = sql.find("*/", i + 2)
                i = n if j < 0 else j + 2
            out.append(" ")
        elif maria and ch == "*" and nxt == "/":
            # 실행 주석의 닫는 표지
            out.append(" ")
            i += 2
        elif ch == "'":
            escape = maria or (
                pg and i > 0 and sql[i - 1] in "eE"
                and not (i > 1 and _IDENT_CHARS.match(sql[i - 2]))
            )
            i = _quoted_end(sql, i, "'", escape)
            out.append("''")
        elif ch == '"' and maria:
            i = _quoted_end(sql, i, '"', True)
            out.append("''")
        elif ch == '"' or (maria and ch == "`"):
            end = _quoted_end(sql, i, ch, False)
            content = sql[i + 1:end - 1] if end <= n and sql[end - 1:end] == ch else sql[i + 1:]
            out.append(re.sub(r"[^\w$]", "_", content) or "_")
            i = end
        elif pg and ch == "$" and not prev_ident:
            m = _PG_DOLLAR_TAG.match(sql, i)
            if m:
                j = sql.find(m.group(), m.end())
                i = n if j < 0 else j + len(m.group())
                out.append("''")
            else:
                out.append(ch)
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def validate_readonly(sql: str) -> None:
    """SQL이 읽기 전용인지 검증한다. 위반 시 예외를 발생시킨다.

    문장 유형 허용 목록 방식이다(D-003 강화 · plans/163 S1). 주석·문자열 리터럴을 세 엔진
    (PostgreSQL·DB2·MariaDB) 규칙으로 각각 지운 본문 모두가 아래를 만족해야 통과한다.

    1. 금지 키워드 없음(DML·DDL·DCL·프로시저·관리 명령 — 데이터 변경 CTE 포함)
    2. 단일 문장(끝의 세미콜론만 허용)
    3. `SELECT` 또는 `WITH`로 시작(여는 괄호 허용)
    4. `INTO` 없음(`SELECT … INTO` 테이블 생성 · `INTO OUTFILE/DUMPFILE` · 변수 대입)
    5. 행 잠금 절 없음(`FOR UPDATE/SHARE` · `LOCK IN SHARE MODE`)
    6. 부작용 함수 호출 없음(`set_config`·`pg_terminate_backend`·`nextval`·`dblink`·`load_file`·
       `pg_sleep`·`sleep` 등)과 부작용 구문 없음(`:=` · `NEXT VALUE FOR` · `SYSPROC.` 등)
    7. PG 유니코드 이스케이프 식별자·문자열(`U&"…"` · `U&'…'`) 없음

    Args:
        sql: 검증할 SQL 문자열

    Raises:
        ReadOnlyViolationError: 읽기 전용 위반 시
    """
    if not sql or not sql.strip():
        raise ReadOnlyViolationError("빈 SQL", sql)

    bodies = [_code_text(sql, d) for d in _DIALECTS]

    for body in bodies:
        tokens = re.findall(r"\b([A-Z_]+)\b", body.upper())
        found = [t for t in tokens if t in FORBIDDEN_KEYWORDS]
        if found:
            raise ReadOnlyViolationError(
                f"금지된 키워드: {', '.join(set(found))}", sql
            )

    for body in bodies:
        head = re.sub(r"[;\s]+$", "", body)
        if ";" in head:
            count = len([s for s in head.split(";") if s.strip()])
            raise ReadOnlyViolationError(f"다중 SQL 문 감지 ({count}개)", sql)

    for body in bodies:
        if not _ALLOWED_START.match(body):
            first = (body.split() or ["?"])[0][:20]
            raise ReadOnlyViolationError(
                f"허용되지 않는 문장 유형: {first} (SELECT·WITH…SELECT만 허용)", sql
            )

    for body in bodies:
        if _INTO.search(body):
            raise ReadOnlyViolationError("SELECT … INTO 금지", sql)
        lock = _LOCKING_CLAUSE.search(body)
        if lock:
            raise ReadOnlyViolationError(f"행 잠금 절 금지: {lock.group()}", sql)
        func = _SIDE_EFFECT_FUNCTION.search(body)
        if func:
            raise ReadOnlyViolationError(
                f"부작용 함수 호출 금지: {func.group(1)}", sql
            )
        syntax = _SIDE_EFFECT_SYNTAX.search(body)
        if syntax:
            raise ReadOnlyViolationError(f"부작용 구문 금지: {syntax.group()}", sql)
        if _UESCAPE_MARK in body:
            raise ReadOnlyViolationError(
                "유니코드 이스케이프 식별자·문자열(U&) 또는 널 문자 금지", sql
            )


def validate_polestar_domain(sql: str) -> None:
    """폴스타 도메인 금지 패턴을 검증한다(D-028). 위반 시 예외를 발생시킨다.

    `execute_sql`을 옵트인(`expose_execute_sql=true`)으로 노출할 때만 추가로 적용한다.
    고수준 도구는 SQL을 받지 않으므로(값 인자만) 이 검증 대상이 아니다.

    검증 항목:
    1. `cmm_vendor`/`cmm_os`/`cmm_os_param` lookup 테이블 참조 (D-028 — EAV 속성 사용)

    ※ RESOURCE_CONF_ID=CONFIGURATION_ID 조인 금지(구 D-022) 규칙은 2026-07-30 제거 —
    현행 정본(D-076 시맨틱 모델 direct_join·골드셋·조립기)이 전부 이 조인을 사용하고
    실측(로컬 픽스처 2,202행 매칭·b0 폼필 라이브 완주·D-058/D-061)이 동작을 실증해,
    규칙을 유지하면 정상 SQL이 전건 차단된다(D-022 재검토 — docs/02_decision.md).

    Args:
        sql: 검증할 SQL 문자열

    Raises:
        PolestarDomainViolationError: 폴스타 금지 패턴 감지 시
    """
    if not sql or not sql.strip():
        return

    # 주석 제거 + 문자열 리터럴 마스킹 (리터럴 내 테이블명 오탐 방지)
    sql_clean = _clean_sql(sql)

    # 금지 lookup 테이블 참조 (D-028) — 단어 경계 매칭
    tokens = set(re.findall(r"\b([A-Z_][A-Z0-9_]*)\b", sql_clean.upper()))
    forbidden = tokens & POLESTAR_FORBIDDEN_TABLES
    if forbidden:
        raise PolestarDomainViolationError(
            f"금지된 lookup 테이블 참조: {', '.join(sorted(forbidden))} "
            "(D-028 — core_config_prop EAV 속성 사용)",
            sql,
        )
