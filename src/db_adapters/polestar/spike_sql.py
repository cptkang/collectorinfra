"""폴스타 급증(기간 대비 상승) 비교 SQL 결정적 조립기 (Plan 82 Wave 9 · D-176 후속2).

**왜 코드가 조립하나.** ①조인 구조가 완전히 고정돼 있다(`cmm_resource` ×2 + `cmm_metric_stat_m`)
②프롬프트로 시키면 `build_stat_month_block`의 **단일 기간 강제와 경쟁**한다 — 한 문서가 서로
다른 기간 규칙을 동시에 지시하는 상태가 된다(§6.9 ④-3). Known Mistakes: *"스키마·조인이 고정된
쿼리는 코드가 runnable SQL을 직접 조립"*.

**조립 형태는 조건부 집계다** — self-join 없이 1회 스캔으로 기준·비교 기간을 나란히 낸다.

**왜 `assembler.py`에 넣지 않나.** 저쪽은 폼필 피벗 전용(1,000행 규모)이고 급증은 별 관심사다.
형제 모듈로 둔다(단일 책임).

## 조작적 정의 — 사용자 확정을 코드로 못 박은 지점 3곳

- `HAVING` **2항 병행**(차분 %p AND 절대 임계): 비율이면 5→10%(2배)가 75→85%를 이겨
  저사용 파일시스템이 상위를 점령한다. 사용률은 이미 퍼센트라 차분이 맞다(§6.10 ① · U18).
- `GROUP BY … r.name`(**파일시스템 단위 행 유지**): 서버 AVG로 접으면 `/var` 30→90%가
  서버 51%로 눌려 절대 임계 80%에 미달해 **놓친다**(§6.10 ③ · U19).
- **단일 직전 기간** 기준: 분포 기준선(평균±편차)은 창 크기·편차 배수라는 비결정 지점을
  둘 더 만든다. *"7월 62% → 8월 85%, +23%p"* 는 사용자가 그대로 검산할 수 있다(§6.10 ② · U15).

계층: application (`src.db_adapters`) — 순수 문자열 조립이라 DB 없이 전량 검증된다.
"""

from __future__ import annotations

from src.utils.query_gen_common import utilization_guard as _utilization_guard
from src.utils.sql_dialect import is_db2, row_limit_clause

#: 리소스 마스터 테이블(자기참조로 부모 서버를 붙인다).
_RESOURCE_TABLE = "cmm_resource"
#: 월별 통계 테이블 — 급증 비교는 **월 단위만** 열려 있다(U15=(b) · 주 단위는 보존기간 미확인).
_METRIC_TABLE = "cmm_metric_stat_m"
_FILESYSTEM_TYPE = "server.FileSystems"
_SERVER_TYPE = "server.Server"
_DEFINITION_NAME = "Utilization"
#: 사용률 값 컬럼 — 급증은 피크 기준이 맞다(평균은 순간 상승을 눌러 없앤다).
_VALUE_COLUMN = "max_val"

#: 응답에 반드시 표기할 한계(§6.12). 못 하는 것보다 **말하지 않는 것이 나쁘다**.
CAPACITY_CHANGE_NOTE = (
    "용량 변경 여부는 대조하지 않았습니다 — 과거 시점 용량이 저장돼 있지 않아, "
    "증설은 급증을 상쇄해 누락시키고 축소는 오탐을 만들 수 있습니다."
)


def _num(value: float) -> str:
    """임계값을 SQL 리터럴로 — 정수면 소수점을 붙이지 않는다(SQL 가독성)."""
    return str(int(value)) if float(value).is_integer() else str(value)


def _month_value_expr(month: str, db_engine: str | None) -> str:
    """해당 월의 사용률 피크 값 표현식.

    DB2는 `AVG`/`MAX`가 정수 컬럼을 정수로 집계하므로 **집계 내부**에서 캐스트해야 한다
    (`::numeric`은 DB2 문법 오류). 캐스트는 DOUBLE — 고정 정밀도 DECIMAL은 범위 밖
    쓰레기 값에서 변환 오버플로로 쿼리 전체를 죽인다(D-103).
    """
    value = (
        f"CAST(s.{_VALUE_COLUMN} AS DOUBLE)" if is_db2(db_engine) else f"s.{_VALUE_COLUMN}"
    )
    return f"MAX(CASE WHEN s.stat_date = '{month}' THEN {value} END)"


def _rounded(expr: str, db_engine: str | None, alias: str) -> str:
    """소수 2자리 보존 — 엔진별 캐스트 지점이 다르다."""
    if is_db2(db_engine):
        return f'CAST(ROUND({expr}, 2) AS DECIMAL(31,2)) AS "{alias}"'
    return f'ROUND(({expr})::numeric, 2) AS "{alias}"'


def build_spike_sql(
    *,
    db_engine: str | None,
    db_schema: str | None,
    base_month: str,          # YYYYMM — 기준(이전) 기간
    cur_month: str,           # YYYYMM — 비교(현재) 기간
    threshold_pct: float,     # 절대 임계 (80)
    delta_pp: float,          # 차분 임계 (20)
    limit: int,
) -> str:
    """기간 대비 급증 SQL을 조립한다. 엔진 방언 3지점을 분기한다.

    GROUP BY에 파일시스템 행(`r.name`)을 유지한다 — 서버 단위로 접으면 급증이 희석된다(U19).
    HAVING 두 항이 차분·절대 임계 병행 판정이다(U18).

    Args:
        db_engine: 레지스트리 engine 값("db2" | "postgresql" 등)
        db_schema: 스키마 한정자(DB2는 대문자 POLESTAR — D-057). 비면 무한정
        base_month: 기준 월 YYYYMM
        cur_month: 비교 월 YYYYMM
        threshold_pct: 절대 임계(현재 월이 이 값 이상이어야 한다)
        delta_pp: 차분 임계(%p — 현재 월이 기준 월보다 이만큼 이상 높아야 한다)
        limit: 행 제한

    Returns:
        runnable SELECT 문(읽기 전용).
    """
    def q(table: str) -> str:
        return f"{db_schema}.{table}" if db_schema else table

    cur_expr = _month_value_expr(cur_month, db_engine)
    base_expr = _month_value_expr(base_month, db_engine)
    delta_expr = f"{cur_expr} - {base_expr}"
    guard = _utilization_guard(_VALUE_COLUMN, _DEFINITION_NAME)

    select_lines = ",\n       ".join([
        'svr.name AS "서버명"',
        'r.name AS "파일시스템"',
        _rounded(base_expr, db_engine, "이전월(%)"),
        _rounded(cur_expr, db_engine, "현재월(%)"),
        _rounded(delta_expr, db_engine, "상승폭(%p)"),
    ])

    return (
        f"SELECT {select_lines}\n"
        f"  FROM {q(_RESOURCE_TABLE)} r\n"
        f"  JOIN {q(_RESOURCE_TABLE)} svr ON r.platform_resource_id = svr.id\n"
        f"   AND svr.resource_type = '{_SERVER_TYPE}'\n"
        f"  JOIN {q(_METRIC_TABLE)} s ON r.id = s.resource_id\n"
        f" WHERE r.resource_type = '{_FILESYSTEM_TYPE}'\n"
        f"   AND s.definition_name = '{_DEFINITION_NAME}'\n"
        f"   AND s.stat_date IN ('{base_month}', '{cur_month}')"
        f"{guard}\n"
        f"   AND r.dtime IS NULL\n"
        f"   AND svr.dtime IS NULL\n"
        f" GROUP BY svr.name, r.name\n"
        f"HAVING {cur_expr} >= {_num(threshold_pct)}\n"
        f"   AND {delta_expr} >= {_num(delta_pp)}\n"
        f' ORDER BY "상승폭(%p)" DESC\n'
        f" {row_limit_clause(db_engine, limit)}"
    )
