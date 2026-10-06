"""폴스타 서버 식별자 존재 확인 SQL 조립기 (plans/123 S-4a · D-089).

**왜 필요한가.** 조건이 서버 식별자 등호뿐인 조회가 0건이면 0건 퍼널(수치 비교만 탐침)은
할 말이 없고, 응답은 「임계값을 낮춰 보세요」로 끝났다 — 없는 서버를 물었는데 조건을 풀라고
했다. 대상이 등록돼 있는지 **고정 조회 1회**로 확인해야 「등록된 서버가 아닙니다」라고 말할 수 있다.

**넓게 찾는다.** 이 조회가 0행이면 호출부가 「등록된 서버가 아닙니다」라고 **단정**한다. 틀리게
「없다」고 하는 쪽이 틀리게 「있다」고 하는 쪽보다 해롭다(있다 → 종전 0건 경로). 그래서
대소문자를 무시하고(RFC 4343) 등록명·호스트명·IP 세 컬럼을 모두 대조하며, 단일 레이블 입력은
FQDN 저장값(`x.%`)까지, FQDN 입력은 단축명까지 본다 — `noise_gate` 소재 프로브
(`build_host_probe_sql`)와 같은 넓힘이다.

계층: application (`src.db_adapters`) — 순수 문자열 조립이라 DB 없이 전량 검증된다.
"""

from __future__ import annotations

import ipaddress

from src.utils.sql_dialect import row_limit_clause, sql_literal

#: 리소스 마스터 테이블 — 서버 행(`resource_type`)의 등록명·호스트명·IP가 직접 컬럼에 있다.
_RESOURCE_TABLE = "cmm_resource"
_SERVER_TYPE = "server.Server"


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def build_hostname_lookup_sql(
    names: list[str], *, db_engine: str | None, db_schema: str | None
) -> str:
    """등록 서버명 → OS hostname 고정 조회(plans/125 E-3 간선 E2 · LLM 0 · 읽기 전용).

    폴스타는 등록명(`name`)과 OS 호스트명(`hostname`)이 다를 수 있다(D-046 — 공동존). APM
    게이트웨이는 OS hostname 으로 정합하므로 서버명만 아는 대상은 이 조회로 hostname 을 얻는다.
    대소문자는 무시하고(RFC 4343) 행 상한은 이름 수의 5배다(한 이름이 여러 행이면 「모호」로
    판정 — 자동 결합 금지).

    Returns:
        `server_name`·`hostname` 두 열을 돌려주는 SELECT
    """
    table = f"{db_schema}.{_RESOURCE_TABLE}" if db_schema else _RESOURCE_TABLE
    lowered = list(dict.fromkeys(str(n).strip().lower() for n in names if str(n).strip()))
    literals = ", ".join(sql_literal(n) for n in lowered) or "''"
    return (
        "SELECT r.name AS server_name, r.hostname AS hostname\n"
        f"  FROM {table} r\n"
        f" WHERE r.resource_type = '{_SERVER_TYPE}'\n"
        "   AND r.dtime IS NULL\n"
        f"   AND LOWER(r.name) IN ({literals})\n"
        f" {row_limit_clause(db_engine, max(1, len(lowered)) * 5)}"
    )


#: 업무명 조회(간선 E6) 행 상한 — 시스템 기본 LIMIT 과 같은 값(D-296 ④ 대상 상한 제거 취지).
#: 상한에 닿으면 호출부가 「잘림」을 사유로 남긴다.
BUSINESS_LOOKUP_ROW_LIMIT = 1000
#: 업무명 검색어 최소 길이 — 1자 부분 일치(`%a%`)는 등록 서버 대부분에 걸려 결과가 의미를 잃는다.
BUSINESS_TERM_MIN_LEN = 2


def _like_contains(term: str) -> str:
    """부분 일치 LIKE 패턴 리터럴 — `!`·`%`·`_` 이스케이프 · 따옴표는 `sql_literal`."""
    escaped = term.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return sql_literal(f"%{escaped}%")


def build_business_lookup_sql(
    terms: list[str], *, db_engine: str | None, db_schema: str | None
) -> str:
    """업무명(사용자 텍스트) → 서버 행 고정 조회(plans/130 M-3 간선 E6 · LLM 0 · 읽기 전용).

    폴스타에는 업무 칼럼이 따로 없고 업무 표기가 등록명(`name` — 예 「<호스트명> (<설명>)」)이나
    비고(`description`)에 들어 있다. 그래서 두 칼럼을 대소문자 무시 **부분 일치**로 대조한다
    (`description`은 은행존·공동존·샌드박스 프로필 모두에 있다 — W0 실측). 어느 검색어에 걸렸는지는
    호출부가 파이썬에서 다시 판정한다(SQL은 단순하게 둔다).

    검색어의 `!`·`%`·`_`는 이스케이프하고(`ESCAPE '!'` — 역슬래시와 달리 PostgreSQL
    `standard_conforming_strings` 설정에 기대지 않는다 · DB2 공통) 리터럴은
    `sql_literal`을 거친다. 빈·공백 검색어와 2자 미만 검색어는 버린다 — 1자 부분 일치는 거의 모든
    서버에 걸려 「업무로 좁힌다」는 목적이 사라진다. 남는 검색어가 없으면 0행 SELECT(`1 = 0`)다.
    행 상한은 `BUSINESS_LOOKUP_ROW_LIMIT`(DB2 `FETCH FIRST` · 그 외 `LIMIT`).

    Args:
        terms: 업무명 검색어 목록
        db_engine: 레지스트리 engine 값("db2"면 `FETCH FIRST`, 그 외 `LIMIT`)
        db_schema: 스키마 한정자(DB2는 대문자 스키마 — D-057). 비면 무한정

    Returns:
        `server_name`·`hostname`·`description` 세 열을 돌려주는 SELECT
    """
    table = f"{db_schema}.{_RESOURCE_TABLE}" if db_schema else _RESOURCE_TABLE
    lowered = list(dict.fromkeys(
        t for t in (str(n).strip().lower() for n in terms) if len(t) >= BUSINESS_TERM_MIN_LEN
    ))
    matches = [
        f"LOWER(r.{col}) LIKE {_like_contains(t)} ESCAPE '!'"
        for t in lowered for col in ("name", "description")
    ]
    return (
        "SELECT r.name AS server_name, r.hostname AS hostname, r.description AS description\n"
        f"  FROM {table} r\n"
        f" WHERE r.resource_type = '{_SERVER_TYPE}'\n"
        "   AND r.dtime IS NULL\n"
        f"   AND ({' OR '.join(matches) or '1 = 0'})\n"
        f" {row_limit_clause(db_engine, BUSINESS_LOOKUP_ROW_LIMIT)}"
    )


def build_entity_probe_sql(
    value: str, *, db_engine: str | None, db_schema: str | None
) -> str:
    """서버 식별자 1개가 등록돼 있는지 확인하는 SELECT를 조립한다(1행 상한 · 읽기 전용).

    행이 하나라도 오면 「등록돼 있다」, 0행이면 「등록된 서버가 아니다」다. 삭제된 리소스
    (`dtime` 값 있음)는 등록으로 보지 않는다(다른 서버 조회 경로와 같은 규약).

    Args:
        value: 사용자가 적은 서버 식별자(등록명·호스트명·FQDN·IP)
        db_engine: 레지스트리 engine 값("db2"면 `FETCH FIRST`, 그 외 `LIMIT`)
        db_schema: 스키마 한정자(DB2는 대문자 스키마 — D-057). 비면 무한정

    Returns:
        runnable SELECT 문
    """
    table = f"{db_schema}.{_RESOURCE_TABLE}" if db_schema else _RESOURCE_TABLE
    text = str(value).strip()
    lowered = text.lower()
    is_ip = _is_ip(text)
    # FQDN 입력은 단축명 저장값도 같은 서버다 — IP의 점은 레이블 구분자가 아니다.
    names = [lowered] + ([lowered.split(".", 1)[0]] if "." in lowered and not is_ip else [])
    literals = ", ".join(sql_literal(n) for n in names)
    matches = [f"LOWER(r.name) IN ({literals})", f"LOWER(r.hostname) IN ({literals})"]
    if is_ip:
        matches.append(f"r.ipaddress = {sql_literal(text)}")
    elif "." not in lowered:
        # 단일 레이블 입력은 FQDN으로 저장된 호스트명(`x.도메인`)도 같은 서버다.
        matches.append(f"LOWER(r.hostname) LIKE {sql_literal(lowered + '.%')}")
    return (
        "SELECT 1 AS hit\n"
        f"  FROM {table} r\n"
        f" WHERE r.resource_type = '{_SERVER_TYPE}'\n"
        "   AND r.dtime IS NULL\n"
        f"   AND ({' OR '.join(matches)})\n"
        f" {row_limit_clause(db_engine, 1)}"
    )
