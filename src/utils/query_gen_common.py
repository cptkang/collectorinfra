"""query_generator / multi_db_executor 공통 SQL 생성 헬퍼 (D-066).

단일 DB 경로(`query_generator` 노드)와 멀티 DB 경로(`multi_db_executor` 노드)가
갈라지면서 (1) few-shot 쿼리 예시 주입과 (2) "전체/모든" 조회 LIMIT 상향이 단일
경로에만 존재해, 멀티 DB 폼필(공동존=gp+yd 등)이 예시 없이·LIMIT 1000으로 열화됐다.
두 로직을 단일 출처로 공유하여 경로 간 SQL 품질 비대칭을 제거한다.
"""

from __future__ import annotations

from datetime import date

_PREV_MONTH_SIGNALS: tuple[str, ...] = (
    "지난달", "지난 달", "전월", "저번달", "저번 달", "지난1개월", "지난 1개월",
    "지난달 1개월", "last month", "previous month",
)
_CUR_MONTH_SIGNALS: tuple[str, ...] = ("이번달", "이번 달", "당월", "금월", "this month", "current month")


def resolve_stat_month(user_query: str | None, today: date | None = None) -> str | None:
    """질의의 기간 표현을 사용률 통계 월(YYYYMM 문자열)로 해석한다(없으면 None).

    "지난달"/"전월"/"지난 1개월" → 직전 월, "이번달"/"당월" → 당월. 그 외에는 None(전체 월 평균).
    폼필 SQL을 코드가 결정적으로 조립할 때 `s.stat_date` 필터에 사용한다.
    """
    text = user_query or ""
    ref = today or date.today()
    if any(sig in text for sig in _PREV_MONTH_SIGNALS):
        year, month = (ref.year - 1, 12) if ref.month == 1 else (ref.year, ref.month - 1)
        return f"{year}{month:02d}"
    if any(sig in text for sig in _CUR_MONTH_SIGNALS):
        return f"{ref.year}{ref.month:02d}"
    return None

def build_stat_month_block(
    stat_month: str | None, metric_table: str = "cmm_metric_stat_m"
) -> str:
    """질의 기간 표현의 결정적 해석(YYYYMM 단일 월)을 LLM 폴백 프롬프트에 강제하는 블록.

    "지난 1개월/지난달" 질의에서 LLM이 시스템 템플릿의 일반 규칙("하드코딩 날짜 금지 —
    CURRENT_DATE 동적 계산")을 따라 `BETWEEN 직전월 AND 이번달`처럼 진행 중인 달까지 포함하는
    기간을 재계산하고 월별 GROUP BY로 서버를 중복 출력하는 실측 사례가 있었다(D-076 후속4).
    코드가 이미 해석한 월(`resolve_stat_month`)을 등호 필터로 강제해 기간 해석을 결정화한다.
    단일 DB(query_generator)·멀티 DB(multi_db_executor) 폴백 경로가 공유한다(D-066 단일 출처).

    Args:
        stat_month: resolve_stat_month 결과 YYYYMM 문자열 (None이면 기간 표현 없음 → 빈 문자열)
        metric_table: 월별 통계 테이블명

    Returns:
        프롬프트에 덧붙일 섹션 텍스트(선행 개행 없음). stat_month가 없으면 "".
    """
    if not stat_month:
        return ""
    return (
        "## 기간 조건 (시스템이 결정적으로 해석 — 최우선 준수)\n"
        f"질의의 기간 표현은 이미 월 통계 기준으로 해석되었습니다. {metric_table} 조인에 반드시 "
        f"`s.stat_date = '{stat_month}'` (단일 월 등호 필터)를 사용하세요.\n"
        "- 이 지시는 '하드코딩 날짜 금지·CURRENT_DATE 동적 계산' 일반 규칙보다 **우선**합니다"
        "(이 값은 시스템이 계산해 주입한 것으로 하드코딩이 아닙니다).\n"
        "- BETWEEN·INTERVAL 재계산으로 진행 중인 달을 포함하지 마세요.\n"
        "- 시간별(_h)/일별(_d) 테이블로 대체하지 마세요."
    )


# "전체/모든/모두" 조회는 기본 LIMIT(default_limit)로 절단하면 안 되므로 상향한다.
_ALL_QUERY_KEYWORDS: tuple[str, ...] = ("모든", "전체", "모두")
_ALL_QUERY_LIMIT: int = 100_000


def resolve_query_limit(user_query: str | None, default_limit: int) -> int:
    """"전체/모든/모두" 조회면 LIMIT를 상향하고, 아니면 기본값을 반환한다.

    단일 DB 경로(query_generator)와 동일한 규칙을 멀티 DB 경로에도 적용하기 위한 공용 함수.

    Args:
        user_query: 사용자 원문 질의
        default_limit: 일반 조회 기본 LIMIT

    Returns:
        적용할 LIMIT 값
    """
    text = user_query or ""
    if any(k in text for k in _ALL_QUERY_KEYWORDS):
        return _ALL_QUERY_LIMIT
    return default_limit




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
                col_l = str(col).lower()
                text = str(val).strip()
                if any(h in col_l for h in _PRIOR_HOSTNAME_HINTS):
                    if text not in seen_h:
                        seen_h.add(text)
                        hostnames.append(text)
                elif any(n in col_l for n in _PRIOR_NAME_HINTS):
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
        "3. 위 목록 외의 서버가 결과에 포함되어서는 안 됩니다."
    )
