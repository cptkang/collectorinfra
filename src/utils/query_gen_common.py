"""query_generator / multi_db_executor 공통 SQL 생성 헬퍼 (D-066).

단일 DB 경로(`query_generator` 노드)와 멀티 DB 경로(`multi_db_executor` 노드)가
갈라지면서 (1) few-shot 쿼리 예시 주입과 (2) "전체/모든" 조회 LIMIT 상향이 단일
경로에만 존재해, 멀티 DB 폼필(공동존=gp+yd 등)이 예시 없이·LIMIT 1000으로 열화됐다.
두 로직을 단일 출처로 공유하여 경로 간 SQL 품질 비대칭을 제거한다.
"""

from __future__ import annotations

import re
from datetime import date

_PREV_MONTH_SIGNALS: tuple[str, ...] = (
    "지난달", "지난 달", "전월", "저번달", "저번 달", "지난1개월", "지난 1개월",
    "지난달 1개월", "last month", "previous month",
)
_CUR_MONTH_SIGNALS: tuple[str, ...] = ("이번달", "이번 달", "당월", "금월", "this month", "current month")
# 절대 월 표현: "2026년 6월" / "2026-06" / "2026/6" (상대 표현보다 우선 — 더 명시적).
# '개월'(지난 3개월)은 매칭되지 않는다(년 접두 + 월 종결 요구).
_ABS_MONTH_RE = re.compile(r"(\d{4})\s*(?:년\s*|[-/])\s*(\d{1,2})\s*월?")
# "지난/최근/과거 N개월(간)" — 직전 완결 월부터 N개월 범위(진행 중인 달 제외, D-076 후속4 원칙 유지)
_N_MONTHS_RE = re.compile(r"(?:지난|최근|과거|last)\s*(\d{1,2})\s*(?:개\s*월|months?)")

# 기간 필터 표현: 단일 월 "YYYYMM" 또는 (시작월, 끝월) 범위. None이면 기간 표현 없음.
StatMonth = str | tuple[str, str] | None


def _month_shift(ref: date, delta: int) -> str:
    """ref 기준 delta개월 이동한 월을 YYYYMM 문자열로 반환한다(delta<0이면 과거)."""
    total = ref.year * 12 + (ref.month - 1) + delta
    return f"{total // 12}{total % 12 + 1:02d}"


def resolve_stat_month_range(
    user_query: str | None, today: date | None = None
) -> tuple[str, str] | None:
    """질의의 기간 표현을 사용률 통계 월 범위 (시작 YYYYMM, 끝 YYYYMM)로 해석한다(없으면 None).

    "지난/최근 N개월" → 직전 완결 월부터 과거 N개월 범위(진행 중인 달 제외),
    "지난달"/"전월" → 직전 월 단일, "이번달"/"당월" → 당월 단일. 그 외에는 None(전체 월 평균).
    폼필 SQL을 코드가 결정적으로 조립할 때 `s.stat_date` 필터에 사용한다(D-102).
    N=1이면 "지난달"과 동일한 (직전월, 직전월)이라 종전 동작과 호환된다.
    """
    text = user_query or ""
    ref = today or date.today()
    # 절대 월 표현이 있으면 최우선(가장 명시적) — 없으면 기존 상대 표현 해석으로 내려간다.
    # 과거에는 절대 월이 None을 반환해 결정적 조립 SQL에 기간 필터가 빠지고 전 기간 평균으로
    # 순위가 뒤집혔다(2026-07-20 라이브 실측 — D-099). 범위 반환(D-102) 계약에 맞춰 (월, 월).
    abs_m = _ABS_MONTH_RE.search(text)
    if abs_m:
        year, month = int(abs_m.group(1)), int(abs_m.group(2))
        if 1 <= month <= 12:
            ym = f"{year}{month:02d}"
            return (ym, ym)
    m = _N_MONTHS_RE.search(text)
    if m and int(m.group(1)) >= 1:
        n = int(m.group(1))
        return (_month_shift(ref, -n), _month_shift(ref, -1))
    if any(sig in text for sig in _PREV_MONTH_SIGNALS):
        prev = _month_shift(ref, -1)
        return (prev, prev)
    if any(sig in text for sig in _CUR_MONTH_SIGNALS):
        cur = _month_shift(ref, 0)
        return (cur, cur)
    return None


def _normalize_stat_month(stat_month: StatMonth) -> tuple[str, str] | None:
    """stat_month 인자(단일 월 문자열 또는 범위 튜플)를 (시작, 끝) 범위로 정규화한다."""
    if not stat_month:
        return None
    if isinstance(stat_month, str):
        return (stat_month, stat_month)
    start, end = stat_month
    return (start, end)

def build_stat_month_block(
    stat_month: StatMonth = None, metric_table: str = "cmm_metric_stat_m"
) -> str:
    """질의 기간 표현의 결정적 해석(YYYYMM 단일 월)을 LLM 폴백 프롬프트에 강제하는 블록.

    "지난 1개월/지난달" 질의에서 LLM이 시스템 템플릿의 일반 규칙("하드코딩 날짜 금지 —
    CURRENT_DATE 동적 계산")을 따라 `BETWEEN 직전월 AND 이번달`처럼 진행 중인 달까지 포함하는
    기간을 재계산하고 월별 GROUP BY로 서버를 중복 출력하는 실측 사례가 있었다(D-076 후속4).
    코드가 이미 해석한 월/범위(`resolve_stat_month_range`)를 등호·BETWEEN 필터로 강제해
    기간 해석을 결정화한다(N개월 범위는 D-102).
    단일 DB(query_generator)·멀티 DB(multi_db_executor) 폴백 경로가 공유한다(D-066 단일 출처).

    Args:
        stat_month: resolve_stat_month_range 결과 (시작, 끝) 범위 또는 단일 월 YYYYMM 문자열
            (None이면 기간 표현 없음 → 빈 문자열)
        metric_table: 월별 통계 테이블명

    Returns:
        프롬프트에 덧붙일 섹션 텍스트(선행 개행 없음). stat_month가 없으면 "".
    """
    rng = _normalize_stat_month(stat_month)
    if not rng:
        return ""
    start, end = rng
    if start == end:
        filter_line = f"`s.stat_date = '{start}'` (단일 월 등호 필터)"
    else:
        filter_line = f"`s.stat_date BETWEEN '{start}' AND '{end}'` (완결 월 범위 필터)"
    return (
        "## 기간 조건 (시스템이 결정적으로 해석 — 최우선 준수)\n"
        f"질의의 기간 표현은 이미 월 통계 기준으로 해석되었습니다. {metric_table} 조인에 반드시 "
        f"{filter_line}를 사용하세요.\n"
        "- 이 지시는 '하드코딩 날짜 금지·CURRENT_DATE 동적 계산' 일반 규칙보다 **우선**합니다"
        "(이 값은 시스템이 계산해 주입한 것으로 하드코딩이 아닙니다).\n"
        "- BETWEEN·INTERVAL 재계산으로 진행 중인 달을 포함하지 마세요(위 값 그대로 사용).\n"
        "- 시간별(_h)/일별(_d) 테이블로 대체하지 마세요."
    )


def build_generic_period_hint(stat_month: str | None) -> str:
    """무선언(프로필 없음) DB용 범용 기간 해석 힌트 — 특정 DB 스키마 리터럴 없음(Plan 63 P3, D-090).

    프로필/시맨틱 모델이 없는 DB는 통계 테이블명·기간 컬럼이 선언돼 있지 않다. 특정 DB의 통계 테이블·
    기간 컬럼 규약에 특화된 `build_stat_month_block`(폴스타 게이트)과 달리, 코드가 해석한 월(YYYYMM)만
    알려주고 **대상 스키마에 실제 존재하는 시간/날짜 컬럼**으로 매핑하도록 LLM에 위임한다(테이블/컬럼 지어내기 금지).
    `GENERIC_LLM_MAPPING` 옵트인(기본 OFF)일 때만 주입한다.
    """
    if not stat_month:
        return ""
    return (
        "## 기간 조건 해석 (참고)\n"
        f"질의의 기간 표현은 월(YYYYMM) '{stat_month}'로 해석되었습니다. 대상 스키마에 **실제 존재하는**"
        " 시간/날짜 컬럼에 이 월을 필터로 적용하세요.\n"
        "- 스키마에 없는 테이블/컬럼을 지어내지 마세요(환각 금지).\n"
        "- 진행 중인 달을 포함하는 BETWEEN·INTERVAL 재계산으로 서버가 중복되지 않게 하세요."
    )


# "전체/모든/모두" 조회는 기본 LIMIT(default_limit)로 절단하면 안 되므로 상향한다.
_ALL_QUERY_KEYWORDS: tuple[str, ...] = ("모든", "전체", "모두")
_ALL_QUERY_LIMIT: int = 100_000

# 명시 건수 표현("100건", "상위 10개") — "건"은 레코드 수 전용 조사라 안전. 단독 "개"는
# "개월"·"4개인 서버" 등 수량 한정과 혼동되므로 "상위 N(개)" 꼴에서만 인정한다.
_EXPLICIT_COUNT_RE = re.compile(r"(\d{1,6})\s*건")
_TOP_N_RE = re.compile(r"상위\s*(\d{1,6})")


def resolve_query_limit(user_query: str | None, default_limit: int) -> int:
    """질의의 명시 건수("100건"/"상위 10")를 최우선 반영하고, "전체/모든/모두"면 상향, 아니면 기본값.

    단일 DB 경로(query_generator)와 동일한 규칙을 멀티 DB 경로에도 적용하기 위한 공용 함수.
    실측(2026-07-21 yd-006): "…100건 조회해줘"가 기본 LIMIT(1000)로 나가 골드(100행)와 행수
    불일치 — 명시 건수는 결정적으로 파싱한다.

    Args:
        user_query: 사용자 원문 질의
        default_limit: 일반 조회 기본 LIMIT

    Returns:
        적용할 LIMIT 값
    """
    text = user_query or ""
    m = _EXPLICIT_COUNT_RE.search(text) or _TOP_N_RE.search(text)
    if m:
        n = int(m.group(1))
        if n > 0:
            return min(n, _ALL_QUERY_LIMIT)
    if any(k in text for k in _ALL_QUERY_KEYWORDS):
        return _ALL_QUERY_LIMIT
    return default_limit


# 헤더/필드명이 사용률 지표(명사+집계어)인지 **표면어로만** 판정한다 — DB 스키마 리터럴
# (server.* resource_type 등)을 쓰지 않아 공용 계층 과적합 가드(D-088)를 통과한다.
# 문서 계층(document.excel_writer, infrastructure)이 지표 컬럼 서식 힌트로 사용한다.
# resource_type/집계함수/값컬럼 매핑이 필요한 결정적 조립은 어댑터의 `classify_metric_field`
# (폴스타 특화 리터럴 포함, db_adapters — 가드 스캔 제외)를 쓴다. infra→application 역방향
# 금지 때문에 문서 계층은 이 스키마-무관 헬퍼만 참조한다(2026-07-22 머지 정리).
_METRIC_NOUN_TERMS: tuple[str, ...] = ("cpu", "메모리", "mem", "디스크", "disk")
_METRIC_AGG_TERMS: tuple[str, ...] = ("평균", "최고", "최대", "최소", "avg", "max", "min")


def is_metric_field_name(field: str) -> bool:
    """헤더/필드명이 사용률 지표(명사+집계어)인지 판정한다(스키마 리터럴 불사용).

    예: "CPU 평균"·"메모리 최고" → True, "메모리 용량"(집계어 없음)·"서버 이름" → False.
    metric 명사와 집계어가 **둘 다** 있어야 인정한다(어댑터 `classify_metric_field`와 동일 규칙).
    """
    low = (field or "").lower()
    has_noun = any(n in low for n in _METRIC_NOUN_TERMS)
    has_agg = any(a in low for a in _METRIC_AGG_TERMS)
    return has_noun and has_agg


def missing_dtime_filter(sql: str) -> bool:
    """cmm_resource를 조회하면서 dtime IS NULL(삭제 리소스 제외) 필터가 전무한지 판정한다.

    폐쇄망 실측(2026-07-21 b0-005): LLM 폴백 SQL이 dtime 필터를 통째로 누락해 삭제된 서버
    약 99대가 결과에 섞임(rows 475 vs 골드 376). 규약상 cmm_resource 조회에는 최소 1회
    `dtime IS NULL`이 필요하다. 별칭(테이블 인스턴스) 단위 강제는 알람 표준 뷰의 부모 서버
    LEFT JOIN(SVR)·계층 조인(C2~C10)처럼 무필터가 정당한 사용에 오탐하므로, "SQL 전체에
    한 번도 없음"만 결정적으로 차단한다. 주석·문자열 리터럴 안의 표기는 제외하고 검사한다.
    단일(query_validator)·멀티(_validate_sql_simple) 검증 경로가 공유한다(D-066).
    """
    body = re.sub(r"--[^\n]*", " ", sql or "")
    body = re.sub(r"/\*.*?\*/", " ", body, flags=re.S)
    body = re.sub(r"'(?:[^']|'')*'", " ", body)
    if not re.search(r"\bcmm_resource\b", body, re.IGNORECASE):
        return False
    return not re.search(r"\bdtime\s+is\s+(?:not\s+)?null\b", body, re.IGNORECASE)


# cmm_resource 조회에 dtime 필터가 없을 때 재생성을 유도하는 검증 실패 메시지(ASCII 구두점 —
# 평가 하네스 스킵 사유로 cp949 콘솔 출력 가능, Known Mistakes 2026-07-16).
MISSING_DTIME_ERROR = (
    "cmm_resource 조회에 dtime IS NULL(삭제 리소스 제외) 필터가 없습니다 - "
    "삭제된 서버/리소스가 결과에 섞입니다. WHERE에 dtime IS NULL 조건을 추가해 다시 작성하세요."
)


# Utilization(사용률 %) 값의 타당성 게이트 범위(D-086). 실측(gp/yd): 만재 피크가 부동소수
# 슬롭·미세 초과로 100.0000…1~100.1까지 기록되므로 상한 100은 정상 만재 기록을 잘라낸다 —
# 여유를 둔 1000 채택(실측상 100.1과 쓰레기 5.5e13 사이에 값이 없어 그 사이 어떤 경계든 동작 동일).
# 하한 0은 b0 실측 음수(센티널 추정) 21행 차단. Utilization 외 지표(MaxIORate 등)엔 적용 금지
# (0~1000 의미가 없음 — `_metric_select_line`이 definition_name으로 게이팅).
UTILIZATION_VALID_RANGE: tuple[int, int] = (0, 1000)


def _utilization_guard(val_col: str, definition_name: str) -> str:
    """Utilization일 때만 값 타당성 게이트 조건(` AND s.<col> BETWEEN 0 AND 1000`)을 반환한다."""
    if definition_name != "Utilization":
        return ""
    lo, hi = UTILIZATION_VALID_RANGE
    return f" AND s.{val_col} BETWEEN {lo} AND {hi}"


# 서버 등록명(cmm_resource.name) 의미의 폼 필드 표면어(공백 제거·소문자 정규화 후 비교).
# 프로필이 명시하듯 이 표현들은 EAV Hostname(=호스트명)이 아니라 등록명 컬럼이다.
_SERVER_NAME_TERMS = frozenset({
    "서버명", "서버이름", "장비명", "장비이름", "리소스명", "등록명",
    "폴스타등록명", "장비식별명",
})


def _norm_name_term(text: str) -> str:
    """폼 필드명을 공백 제거+소문자로 정규화한다('서버 이름'→'서버이름')."""
    return "".join((text or "").split()).lower()


def is_servername_field(field: str) -> bool:
    """필드명이 서버 등록명(cmm_resource.name) 의미의 표면어인지 판정한다."""
    return _norm_name_term(field) in _SERVER_NAME_TERMS


def is_hostname_target(column: str) -> bool:
    """매핑 대상 컬럼이 hostname(직접 컬럼 `*.hostname` 또는 EAV Hostname 속성)인지 판정한다.

    - EAV 매핑: `EAV:Hostname`
    - 직접 컬럼: `hostname` / `cmm_resource.hostname` / `polestar.cmm_resource.hostname` /
      `db_id:table.hostname` 등 — 표기·스키마 접두사·db_id 접두사 무관하게 bare 컬럼명으로 판정.
    """
    if not isinstance(column, str) or not column:
        return False
    if column.startswith("EAV:"):
        return column[4:].strip().lower() == "hostname"
    return column.rsplit(".", 1)[-1].strip().lower() == "hostname"


def is_servername_to_hostname(field: str, column: str) -> bool:
    """서버명/서버이름류 필드가 hostname(컬럼 or EAV)으로 (오)매핑되는지 판정한다.

    자동 유사어 등록 차단(재오염 방지)과 폼필 매핑 교정에서 공용으로 쓰는 결정적 판정.
    '호스트네임' 등 호스트명 표면어는 name-term이 아니므로 False.
    """
    return is_servername_field(field) and is_hostname_target(column)


def correct_servername_hostname_mapping(
    column_mapping: dict, entity_table: str
) -> None:
    """서버명/서버이름류 폼 필드가 hostname으로 오매핑되면 등록명 컬럼으로 교정한다(in-place).

    프로필(gp/yd/b0)이 명시적으로 규정하듯 '서버명/서버 이름'은 hostname(호스트명 값)이
    아니라 등록명 컬럼(`<entity>.name`, 예: cmm_resource.name)이다. 그러나 전역/EAV 유사어의
    `Hostname: [..., 서버명, ...]` 미끼 + 유사어/LLM 매핑의 비결정성으로 "서버 이름"이
    **EAV Hostname 또는 직접 `*.hostname` 컬럼**에 붙어 두 칼럼(서버 이름·호스트네임)이 모두
    hostname으로 채워지는 문제가 반복됐다. 프로필의 확정 규칙을 결정적 가드로 못박아,
    유사어/Redis 상태·LLM 변동과 무관하게 교정한다. EAV·직접 컬럼 **두 경로 모두** 처리한다.
    ('호스트네임' 등 호스트명 표면어는 name-term이 아니므로 건드리지 않는다.)
    """
    if not entity_table or not column_mapping:
        return
    for field, col in list(column_mapping.items()):
        if is_servername_to_hostname(field, col):
            column_mapping[field] = f"{entity_table}.name"




def build_query_examples_block(structure_meta: dict | None) -> str:
    """structure_meta.query_examples를 few-shot 블록 텍스트로 만든다(없으면 빈 문자열).

    프로필의 검증된 질문→SQL 패턴을 LLM에 그대로 제시하여 조인 환각을 줄인다.
    단일/멀티 DB 경로가 동일한 예시 블록을 사용하도록 단일 출처화한다.

    Args:
        structure_meta: 구조 분석 메타(수동 프로필 로드 결과, query_examples 포함 가능)

    Returns:
        시스템 프롬프트에 덧붙일 예시 블록 텍스트(없으면 "")
    """
    if not structure_meta:
        return ""
    query_examples = structure_meta.get("query_examples", [])
    if not query_examples:
        return ""

    block = "\n\n## 쿼리 예시 (반드시 이 패턴을 따르세요)"
    block += "\n아래 예시의 JOIN 패턴을 그대로 따라하세요. 임의로 다른 조인 조건을 만들지 마세요.\n"
    for i, ex in enumerate(query_examples, 1):
        question = ex.get("question", "")
        sql_example = ex.get("sql", "").rstrip()
        explanation = ex.get("explanation", "")
        block += f'\n### 예시 {i}: "{question}"'
        block += f"\n```sql\n{sql_example}\n```"
        if explanation:
            block += f"\n설명: {explanation}"
        block += "\n"
    return block


def build_value_index_block(matched: dict[str, list[str]] | None) -> str:
    """E5-2 값 검색 매칭 리터럴을 프롬프트 주입 블록으로 만든다(없으면 빈 문자열).

    질의 키워드로 검증된 **실측 리터럴**(예: `resource_type='server.Server'`,
    EAV `NAME='Hostname'`)만 제시하여 WHERE 리터럴 환각(Plan 25 유형)을 차단한다.
    단일/멀티 DB 경로가 동일 블록을 사용하도록 단일 출처화한다.

    Args:
        matched: {인덱스 키: [매칭 리터럴, ...]} (search_value_index 결과)

    Returns:
        프롬프트에 덧붙일 검증 리터럴 블록(없으면 "")
    """
    if not matched:
        return ""
    lines = [
        "\n\n## 검증된 값 리터럴 (WHERE 절에 이 실측 값만 사용)",
        "아래는 질의와 관련해 DB에서 실측 확인된 값입니다. WHERE 절 리터럴은 반드시 "
        "이 목록의 값만 사용하고, 목록에 없는 값을 임의로 지어내지 마세요.",
    ]
    for key, values in matched.items():
        if not values:
            continue
        vals = ", ".join(f"'{v}'" for v in values)
        lines.append(f"- {key}: {vals}")
    return "\n".join(lines) if len(lines) > 2 else ""


# 선행 task 결과(prior_rows) 서버 스코프 강제 (orchestration 데이터 의존 패턴 ②, D-086)
# hostname 힌트를 name 힌트보다 먼저 검사한다 — "hostname"은 "name"을 부분 문자열로 포함.
_PRIOR_HOSTNAME_HINTS: tuple[str, ...] = ("hostname", "host_name")
_PRIOR_NAME_HINTS: tuple[str, ...] = ("server_name", "name")
_MAX_PRIOR_SCOPE_VALUES: int = 100

# 서버 식별 컬럼 판정(D-100): 정확 매칭 집합 + server/host/os 계열 *_name/_id만 인정한다.
# 선행 조회가 서버명 외 컬럼(alarm_name/definition_name/severity 등)도 반환하게 되면서,
# "name" 부분매칭이 alarm_name을 서버 식별값으로 오수집해 스코프 HAVING이 오염됐다(실측).
_SERVER_ID_EXACT: frozenset[str] = frozenset(
    {"server_name", "hostname", "host_name", "os_hostname", "name", "id", "server_id"}
)
_SERVER_ID_PREFIXES: tuple[str, ...] = ("server", "host", "os")


def is_server_identity_col(col: str) -> bool:
    """컬럼명이 서버 식별 컬럼(병합 키·선행 스코프 키 후보)인지 엄격 판정한다 (D-100).

    정확 매칭(server_name/hostname/name/id 등)이거나 server/host/os로 시작하는 *_name/_id만
    서버 식별로 인정한다. alarm_name·definition_name·severity 등 비서버 컬럼은 제외한다.

    Args:
        col: 컬럼명

    Returns:
        서버 식별 컬럼이면 True
    """
    cl = str(col).strip().lower()
    if cl in _SERVER_ID_EXACT:
        return True
    if (cl.endswith("_name") or cl.endswith("_id")) and any(
        cl.startswith(p) for p in _SERVER_ID_PREFIXES
    ):
        return True
    return False


def _collect_prior_identity_values(prior_rows: dict) -> tuple[str, list[str]]:
    """prior_rows에서 서버 식별 컬럼 종류와 값 목록을 추출한다.

    hostname류 컬럼이 하나라도 있으면 hostname을 우선하고, 없으면 name류를 쓴다
    (폴스타는 name≠hostname — D-061 계열, 컬럼 종류를 섞으면 0건 위험).

    Args:
        prior_rows: {task_id: [식별 키 행, ...]}

    Returns:
        ("hostname" | "name" | "", 중복 제거된 값 목록[상한 적용])
    """
    hostnames: list[str] = []
    names: list[str] = []
    seen_h: set[str] = set()
    seen_n: set[str] = set()
    for rows in (prior_rows or {}).values():
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            for col, val in row.items():
                if val is None or str(val).strip() == "":
                    continue
                if not is_server_identity_col(col):
                    continue  # 서버 식별 컬럼만 스코프 키로 인정(alarm_name 등 오염 차단 — D-100)
                col_l = str(col).lower()
                text = str(val).strip()
                if any(h in col_l for h in _PRIOR_HOSTNAME_HINTS):
                    if text not in seen_h:
                        seen_h.add(text)
                        hostnames.append(text)
                else:
                    if text not in seen_n:
                        seen_n.add(text)
                        names.append(text)
    if hostnames:
        return "hostname", hostnames[:_MAX_PRIOR_SCOPE_VALUES]
    if names:
        return "name", names[:_MAX_PRIOR_SCOPE_VALUES]
    return "", []


def build_prior_rows_block(prior_rows: dict | None) -> str:
    """선행 task 결과 서버 목록을 SQL 스코프 강제 블록으로 렌더링한다(없으면 빈 문자열).

    orchestration 데이터 의존(input_from) 경로에서 선행 task가 선별한 서버들로
    이번 SQL의 대상을 결정적으로 한정한다. 선별 조건(알람 상태·심각도 등)을 LLM이
    재표현하다 환각(존재하지 않는 resource_type='alarm.Alarm' 등)으로 0건이 되는
    것을 차단한다(D-086). 단일/멀티 DB 경로가 동일 블록을 사용한다(D-066).

    Args:
        prior_rows: {task_id: [식별 키 행, ...]} (subagents._make_isolated_input 산출)

    Returns:
        프롬프트에 덧붙일 스코프 강제 블록(유효한 식별 값이 없으면 "")
    """
    if not prior_rows:
        return ""
    col, values = _collect_prior_identity_values(prior_rows)
    if not values:
        return ""
    quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in values)
    return (
        "## 선행 작업 결과 서버 스코프 (필수 준수)\n"
        "이번 조회 대상은 선행 작업에서 이미 선별된 아래 서버들로 **한정**합니다.\n"
        f"- 대상 서버 (선행 결과 {col} 컬럼 기준): {quoted}\n"
        "규칙:\n"
        f"1. SQL에 서버 한정 조건 `{col} IN ({quoted})` 을 반드시 포함하세요 "
        f"(위 값은 선행 결과의 식별 컬럼 값 목록입니다 — 대상 DB에서 서버를 식별하는 컬럼(`{col}` "
        "또는 동등한 식별 컬럼)에 적용하세요. 서버 엔터티 행 기준, GROUP BY 피벗 쿼리면 기존 규칙대로 "
        "HAVING의 집계 CASE WHEN으로 적용).\n"
        "2. 서버를 선별했던 조건(알람 발생·심각도·활성 상태 등)은 선행 작업에서 이미 처리 완료되었습니다 — "
        "선별에 사용한 테이블·컬럼·조건(알람/이벤트 등)을 이 SQL에서 다시 표현하지 마세요. "
        "대상 DB에 존재하지 않는 테이블/컬럼/값으로 선별 조건을 지어내지 마세요(환각 금지).\n"
        "3. 위 목록 외의 서버가 결과에 포함되어서는 안 됩니다.\n"
        "4. 결과의 각 행이 어느 서버의 값인지 알 수 있도록 서버 식별 컬럼(예: server_name)을 "
        "SELECT에 반드시 포함하세요 (GROUP BY 피벗 쿼리면 집계 CASE WHEN으로 포함)."
    )
