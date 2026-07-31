"""폴스타 다중 리소스 피벗 SQL 결정적 조립기 (Plan 63 P2, D-089).

query_gen_common.py에서 분리 이동한 폴스타 EAV/피벗 특화 조립 로직(동작 불변, D-068 계열).
공용 코어는 어댑터 모듈을 직접 임포트한다(application→application). 호출부: query_generator·
multi_db_executor·semantic_compiler(모두 application).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field as dc_field
from datetime import date

# 기간 범위/값 타당성 게이트는 공용 코어(utils)에서 가져온다(application→config/utils 허용).
from src.utils.query_gen_common import (
    StatMonth,
    _normalize_stat_month,
    _utilization_guard,
    drop_entries_missing_columns,
    resolve_stat_month_range,
)


def decimal_cast_example(db_engine: str | None) -> str:
    """엔진별 '소수 보존 사용률 집계' 예시 SQL 스니펫을 반환한다(미매핑 alias 안내용).

    PostgreSQL은 `AVG(...)::numeric`으로 소수를 보존하지만, DB2는 `AVG()`가 정수 컬럼을 정수로
    집계하므로 **집계 전** 캐스트가 필요하다(`::numeric`은 DB2 문법 오류). 캐스트는 DOUBLE —
    고정 정밀도 DECIMAL(15,4)는 범위 밖 쓰레기 값(실측 5.5e13)에서 SQL0413N 변환 오버플로로
    쿼리 전체가 죽는다(D-103). 값 타당성 게이트(BETWEEN)도 예시에 포함해 LLM 경로도 오염을 거른다.
    """
    guard = _utilization_guard("avg_val", "Utilization")
    if (db_engine or "").lower() == "db2":
        return (
            "CAST(ROUND(AVG(CASE WHEN r.resource_type = 'server.Cpus' "
            f"AND s.definition_name = 'Utilization'{guard} "
            'THEN CAST(s.avg_val AS DOUBLE) END), 2) AS DECIMAL(31,2)) AS "CPU 평균"'
        )
    return (
        "ROUND(AVG(CASE WHEN r.resource_type = 'server.Cpus' "
        f"AND s.definition_name = 'Utilization'{guard} "
        'THEN s.avg_val END)::numeric, 2) AS "CPU 평균"'
    )


logger = logging.getLogger(__name__)

_RESOURCE_TYPE_RE = re.compile(r"\[resource_type:\s*([^\]/\s]+)")
_SERVER_RESOURCE_TYPE = "server.Server"

# 사용률 통계(metric) 필드 분류 — 명사→resource_type, 집계어→(집계함수, 값컬럼).
# 폴스타 resource_type(server.*) 리터럴을 담으므로 어댑터 계층에 둔다(공용 계층 과적합 가드
# D-088 준수 — 문서 계층은 스키마-무관 `is_metric_field_name`을 쓴다, 2026-07-22 머지 정리).
_METRIC_NOUN_RT: tuple[tuple[str, str], ...] = (
    ("cpu", "server.Cpus"),
    ("메모리", "server.Memory"),
    ("mem", "server.Memory"),
    ("디스크", "server.Disks"),
    ("disk", "server.Disks"),
)
_METRIC_AGG: tuple[tuple[str, str, str], ...] = (
    ("평균", "AVG", "avg_val"),
    ("최고", "MAX", "max_val"),
    ("최대", "MAX", "max_val"),
    ("최소", "MIN", "min_val"),
    ("avg", "AVG", "avg_val"),
    ("max", "MAX", "max_val"),
    ("min", "MIN", "min_val"),
)


def classify_metric_field(field: str) -> tuple[str, str, str] | None:
    """사용률 필드를 (resource_type, 집계함수, 값컬럼)으로 분류한다(아니면 None).

    예: "CPU 평균" → ("server.Cpus", "AVG", "avg_val"),
        "메모리 최고" → ("server.Memory", "MAX", "max_val").
    metric 명사와 집계어가 **둘 다** 있어야 metric으로 인정한다('메모리 용량'은 집계어 없어 제외).
    """
    low = (field or "").lower()
    rt = next((r for noun, r in _METRIC_NOUN_RT if noun in low), None)
    if rt is None:
        return None
    agg = next(((fn, col) for term, fn, col in _METRIC_AGG if term in low), None)
    if agg is None:
        return None
    return rt, agg[0], agg[1]


# ── 월 시리즈(가로 6개월 등) 양식 인식기 (D-113/D-115, plans/67 §2.3) ─────────────
#
# 2단 병합 헤더 결합(D-112)이 만든 복합 필드명 "그룹라벨|서브"에서
# "사용률+집계어 그룹 | M+k(또는 절대월) 서브" **구조 패턴**만 인식한다.
# 기관명·시트제목·칼럼순서 하드코딩 금지(과적합 가드 — plans/67 §8 R3의 경계 지표).
# 판정 불가 시 None을 반환해 기존 경로로 폴백한다(오동작이 아니라 미발동으로 실패).

# 리소스 판정용 문맥 명사(양식 제목·질의에서 탐색). _METRIC_NOUN_RT에 관용 표현 추가.
_CONTEXT_NOUN_RT: tuple[tuple[str, str], ...] = _METRIC_NOUN_RT + (
    ("주기억장치", "server.Memory"),
)

# peak 판정을 평균보다 먼저 — "Peak시 사용률"류에 '평균'이 공존할 일은 없으나 순서 명시.
_MONTH_GROUP_AGG: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("peak", "피크", "최고", "최대"), "max_val", "peak"),
    (("평균", "avg"), "avg_val", "avg"),
)

_REL_MONTH_RE = re.compile(r"^m(?:\s*\+\s*(\d{1,2}))?$", re.IGNORECASE)
_ABS_YM_RE = re.compile(r"^(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*월?$")
_ABS_M_ONLY_RE = re.compile(r"^(\d{1,2})\s*월$")

# PostgreSQL 식별자 63바이트 한도 — 초과 alias는 조용히 잘려 월 서픽스가 소실·충돌하므로
# 그 양식은 인식 대상에서 제외한다(폴백). DB2는 128바이트라 PG 기준이 보수적 상한.
_MAX_ALIAS_BYTES = 62


def _ym_add(yyyymm: str, delta: int) -> str:
    """YYYYMM에 delta개월을 더한다."""
    y, m = int(yyyymm[:4]), int(yyyymm[4:6])
    total = y * 12 + (m - 1) + delta
    return f"{total // 12}{total % 12 + 1:02d}"


def _last_complete_month(today: date | None = None) -> str:
    """실행일 기준 마지막 완결 월(=지난달)을 YYYYMM으로 반환한다(Q3 확정 기본값)."""
    ref = today or date.today()
    return _ym_add(f"{ref.year}{ref.month:02d}", -1)


@dataclass(frozen=True)
class MonthSeries:
    """월 시리즈 양식 인식 결과.

    measures의 alias는 **복합 필드명 그대로**다 — 기존 결정적 피벗이 SELECT alias로
    양식 필드명을 써서 결과 행 키 = 양식 헤더가 되는 아키텍처(writer 필드명 매칭,
    resolved_mapping Layer 1)에 그대로 얹힌다(매핑 상태 갱신 불요).
    """

    measures: list[tuple[str, str, str, str]]  # (alias=필드명, resource_type, val_col, YYYYMM)
    fields: list[str] = dc_field(default_factory=list)  # 인식된 복합 필드명 목록
    anchor: tuple[str, str] = ("", "")  # (M, M+max) — 응답 명시용(§2.4)
    resource_type: str = ""
    month_by_field: dict[str, str] = dc_field(default_factory=dict)  # 표시용 {필드명: YYYYMM}


def _parse_month_sub(sub: str) -> tuple[str, int | str] | None:
    """서브 헤더를 ('rel', k) 또는 ('abs', 'YYYYMM'|'MM')로 해석한다(아니면 None)."""
    s = sub.strip()
    m = _REL_MONTH_RE.match(s)
    if m:
        return ("rel", int(m.group(1) or 0))
    m = _ABS_YM_RE.match(s)
    if m:
        month = int(m.group(2))
        if 1 <= month <= 12:
            return ("abs", f"{int(m.group(1))}{month:02d}")
        return None
    m = _ABS_M_ONLY_RE.match(s)
    if m:
        month = int(m.group(1))
        if 1 <= month <= 12:
            return ("abs", f"{month:02d}")  # 연도 미상 — 앵커 해석 시 보정
        return None
    return None


def recognize_month_series(
    column_mapping: dict[str, str | None] | None,
    context_text: str = "",
    user_query: str = "",
    today: date | None = None,
) -> MonthSeries | None:
    """복합 필드명에서 월 시리즈(사용률 가로 전개) 패턴을 결정적으로 인식한다(D-113).

    인식 조건(모두 충족해야 발동 — 미충족 시 None 폴백):
    - 미매핑(None 또는 cmm_metric_stat 오매핑) 필드명이 "그룹|서브" 구조이고,
      그룹에 '사용률'과 집계어(평균/peak류)가 있으며 서브가 M+k 또는 절대월
    - 리소스 명사(cpu/메모리/주기억장치/디스크)가 context_text(양식 제목 등)·user_query·
      필드명 어디선가 발견됨 (없으면 판정 불가 → 폴백)
    - 상대(M+k)·절대월 표기가 한 양식에 혼재하지 않음
    - alias(=필드명) UTF-8 길이가 PG 식별자 한도 이내

    기준월(Q3 확정): 사용자 질의에 기간이 있으면 그 **끝 월**이 M+max_k,
    없으면 실행일 기준 지난달(마지막 완결 월)이 M+max_k.

    Args:
        column_mapping: field_mapper 산출 {필드명: 컬럼 또는 None}
        context_text: 양식 제목(title_text)·파일명 등 리소스 판정 문맥
        user_query: 사용자 질의(기간 해석용)
        today: 기준일(테스트 주입용, None이면 오늘)

    Returns:
        MonthSeries 또는 None(패턴 아님 — 기존 경로 유지)
    """
    if not column_mapping:
        return None

    parsed: list[tuple[str, str, tuple[str, int | str]]] = []  # (field, val_col, sub해석)
    for fname, col in column_mapping.items():
        if col is not None and "cmm_metric_stat" not in str(col).lower():
            continue
        if "|" not in fname:
            continue
        group, _, sub = fname.rpartition("|")
        low = group.lower()
        if "사용률" not in low:
            continue
        agg = next(
            (vc for terms, vc, _sfx in _MONTH_GROUP_AGG if any(t in low for t in terms)),
            None,
        )
        if agg is None:
            continue
        sub_parsed = _parse_month_sub(sub)
        if sub_parsed is None:
            continue
        if len(fname.encode("utf-8")) > _MAX_ALIAS_BYTES:
            # alias 잘림 → 월 서픽스 소실·충돌 위험. 양식 전체 폴백.
            logger.info(
                "월 시리즈 미발동(D-113): 필드명 %d바이트 > %d(PG 식별자 한도) — %r",
                len(fname.encode("utf-8")), _MAX_ALIAS_BYTES, fname[:40],
            )
            return None
        parsed.append((fname, agg, sub_parsed))

    if not parsed:
        # 침묵 금지(Known Mistakes): 후보에 근접한 필드가 있으면 사유를 남긴다.
        near = [f for f in column_mapping if "|" in f and "사용률" in f]
        if near:
            logger.info(
                "월 시리즈 미발동(D-113): '그룹|서브' 사용률 필드 %d개가 있으나 "
                "집계어/서브(M+k·절대월) 패턴 불충족 — 예: %r",
                len(near), near[0],
            )
        return None

    kinds = {p[2][0] for p in parsed}
    if len(kinds) != 1:
        logger.info(
            "월 시리즈 미발동(D-113): 상대(M+k)·절대월 표기 혼재 %d필드 — 결정적 해석 불가",
            len(parsed),
        )
        return None  # 상대·절대 혼재 — 결정적 해석 불가

    # 리소스 판정: 문맥(제목 우선) → 질의 → 필드명 순으로 명사 탐색
    search_text = " ".join(
        t for t in (context_text, user_query, " ".join(column_mapping.keys())) if t
    ).lower()
    rt = next((r for noun, r in _CONTEXT_NOUN_RT if noun in search_text), None)
    if rt is None:
        logger.info(
            "월 시리즈 미발동(D-113): 월 필드 %d개 인식했으나 리소스 명사(cpu/메모리/"
            "주기억장치/디스크)를 문맥에서 못 찾음 — context_text=%r, user_query=%r",
            len(parsed), (context_text or "")[:80], (user_query or "")[:80],
        )
        return None

    last_month = _last_complete_month(today)
    month_by_field: dict[str, str] = {}
    if kinds == {"rel"}:
        ks = [p[2][1] for p in parsed]
        max_k = max(ks)  # type: ignore[type-var]
        rng = resolve_stat_month_range(user_query, today)
        anchor_end = rng[1] if rng else last_month
        base = _ym_add(anchor_end, -int(max_k))
        for fname, _vc, (_kind, k) in parsed:
            month_by_field[fname] = _ym_add(base, int(k))
    else:
        for fname, _vc, (_kind, val) in parsed:
            ym = str(val)
            if len(ym) == 2:  # 연도 미상(N월) — 마지막 완결 월 이하의 가장 최근 발생으로 보정
                candidate = f"{last_month[:4]}{ym}"
                if candidate > last_month:
                    candidate = _ym_add(candidate, -12)
                ym = candidate
            month_by_field[fname] = ym

    measures = [
        (fname, rt, vc, month_by_field[fname]) for fname, vc, _sub in parsed
    ]
    months_sorted = sorted(month_by_field.values())
    return MonthSeries(
        measures=measures,
        fields=[p[0] for p in parsed],
        anchor=(months_sorted[0], months_sorted[-1]),
        resource_type=rt,
        month_by_field=month_by_field,
    )


# 폴스타 entity(cmm_resource)의 검증된 직접 컬럼 — 스키마에 칼럼 목록이 없어 검증
# 불가할 때 결정적 피벗에 허용하는 안전 화이트리스트(어댑터 지식, D-089 계층).
_ENTITY_SAFE_DIRECT_COLUMNS = frozenset({"id", "name", "hostname", "ipaddress", "description"})


def filter_pivot_regular_entries(
    regular_entries: list[tuple[str, str]],
    schema_info: dict | None,
    entity_table: str,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """결정적 피벗의 직접 컬럼 항목을 스키마 실측 + 안전 화이트리스트로 거른다.

    1차: **entity 외 테이블의 직접 칼럼 제외** — 조립기는 regular 항목의 테이블명을 떼고
    entity 별칭 `c.`에 붙이므로, 다른 테이블의 유효 매핑(라이브 실측 2026-07-30:
    `구분→cmm_resource_type.category` — category는 그 테이블에 **실존**해 칼럼 검증을
    전부 통과)이 `c.category`로 재작성되어 쿼리 전체가 죽는다. 피벗의 직접 칼럼은
    entity 테이블 소속만 유효하다.
    2차: `drop_entries_missing_columns` — 스키마에 칼럼 목록이 있으면 부재 칼럼 제외.
    3차: entity 테이블인데 스키마에 칼럼 목록이 **없어 검증 불가**하면(폐쇄망 캐시 스키마가
    요약형인 경우) 안전 화이트리스트 외 칼럼을 제외한다(환각 칼럼 차단).

    Returns:
        (유지 항목, 제외 항목) — 제외는 호출부가 경고 로그로 가시화한다.
    """
    ent0 = (entity_table or "").lower()
    on_entity: list[tuple[str, str]] = []
    dropped_foreign: list[tuple[str, str]] = []
    for field, col in regular_entries:
        parts = str(col).split(".")
        # 테이블 한정이 있고 entity가 아니면 제외("db.table.column" 3단계는 가운데가 테이블)
        if len(parts) >= 2 and parts[-2].lower() != ent0:
            dropped_foreign.append((field, col))
        else:
            on_entity.append((field, col))
    kept0, dropped = drop_entries_missing_columns(on_entity, schema_info)
    dropped = dropped_foreign + dropped
    verifiable: set[str] = set()
    for tname, tinfo in ((schema_info or {}).get("tables") or {}).items():
        has_cols = any(
            (c.get("name") if isinstance(c, dict) else c)
            for c in (tinfo or {}).get("columns", [])
        )
        if has_cols:
            verifiable.add(tname.lower())
            if "." in tname:
                verifiable.add(tname.rsplit(".", 1)[-1].lower())
    ent = (entity_table or "").lower()
    kept: list[tuple[str, str]] = []
    for field, col in kept0:
        parts = str(col).split(".")
        if (
            len(parts) >= 2
            and parts[-2].lower() == ent
            and ent not in verifiable
            and parts[-1].lower() not in _ENTITY_SAFE_DIRECT_COLUMNS
        ):
            dropped.append((field, col))
        else:
            kept.append((field, col))
    return kept, dropped


def build_form_fill_candidates(
    schema_info: dict | None,
    eav_pattern: dict | None,
) -> list[dict]:
    """역질문 드롭다운 후보를 스키마 실측으로 산출한다(D-118 — LLM 불개입).

    후보 = entity 테이블 직접 칼럼(스키마에 칼럼 목록이 있으면 실측, 없으면 안전
    화이트리스트) + EAV known_attributes(설명을 한글 라벨로 병기). 조립기가 실을 수
    있는 것만 후보가 된다(엔진/지식 분리 — 후보에 있으면 반드시 조립 가능).

    Returns:
        [{"value": "column:name"|"eav:Vendor", "label": 표시명, "kind": "column"|"eav"}]
    """
    entity = (eav_pattern or {}).get("entity_table", "cmm_resource")
    out: list[dict] = []
    cols: list[str] = []
    for tname, tinfo in ((schema_info or {}).get("tables") or {}).items():
        bare = tname.rsplit(".", 1)[-1].lower()
        if bare != entity.lower():
            continue
        cols = [
            (c.get("name") if isinstance(c, dict) else str(c))
            for c in (tinfo or {}).get("columns", [])
        ]
        break
    if not cols:
        cols = sorted(_ENTITY_SAFE_DIRECT_COLUMNS)
    for col in cols:
        if not col:
            continue
        out.append({"value": f"column:{col}", "label": f"{entity}.{col}", "kind": "column"})
    for pattern in ((schema_info or {}).get("_structure_meta") or {}).get("patterns", []):
        if pattern.get("type") != "eav":
            continue
        attrs = pattern.get("known_attributes_detail") or pattern.get("known_attributes", [])
        for attr in attrs:
            if not isinstance(attr, dict):
                continue
            name = (attr.get("name") or "").strip()
            if not name:
                continue
            desc = (attr.get("description") or "").split("[resource_type:")[0].strip()
            label = f"{name}" + (f" — {desc}" if desc else "")
            out.append({"value": f"eav:{name}", "label": label, "kind": "eav"})
    return out


def resolve_form_fill_answers(
    answers: dict[str, dict] | None,
    schema_info: dict | None,
    eav_pattern: dict | None,
    *,
    protected_fields: set[str] | None = None,
) -> tuple[dict[str, dict], dict[str, str], dict[str, str]]:
    """역질문 답변을 존재성 검증 후 오버라이드로 확정한다(D-118 — 사용자 층 최우선).

    액션 어휘는 조립기 기존 능력의 부분집합(C5): blank / column(entity 직접 칼럼) /
    eav(server EAV 속성) / literal(writer 상수 기입). 검증 탈락은 침묵 없이 사유를
    남긴다(applied=False + reason — 응답 노출·재역질문).

    Args:
        answers: {field: {"action": str, "value": str|None}} (라우트가 주입한 구조화 답변)
        protected_fields: 답변으로 덮을 수 없는 필드(월 시리즈 등 구조 채움 영역)

    Returns:
        (overrides, mapping_updates, literals)
        - overrides: {field: {action, value, applied, reason}} — 사유 노출용 전량
        - mapping_updates: {field: "entity.col"|"EAV:Attr"|None} — 매핑 주입분(blank 포함)
        - literals: {field: value} — writer 상수 기입분
    """
    overrides: dict[str, dict] = {}
    mapping_updates: dict[str, str | None] = {}
    literals: dict[str, str] = {}
    if not answers:
        return overrides, mapping_updates, literals
    entity = (eav_pattern or {}).get("entity_table", "cmm_resource")
    protected = protected_fields or set()
    valid = {c["value"] for c in build_form_fill_candidates(schema_info, eav_pattern)}

    for field, ans in answers.items():
        if not isinstance(ans, dict):
            continue
        action = str(ans.get("action") or "").strip().lower()
        value = ans.get("value")
        entry = {"action": action, "value": value, "applied": False, "reason": None}
        overrides[field] = entry
        if field in protected:
            entry["reason"] = "월 시리즈 등 구조 채움 필드는 답변으로 변경할 수 없습니다"
            continue
        if action == "blank":
            mapping_updates[field] = None
            entry["applied"] = True
        elif action == "literal":
            if value is None or str(value) == "":
                entry["reason"] = "직접 입력 값이 비어 있습니다"
                continue
            literals[field] = str(value)
            mapping_updates[field] = None  # SQL 제외 — writer가 상수 기입
            entry["applied"] = True
        elif action in ("column", "eav"):
            token = f"{action}:{value}"
            if not value or token not in valid:
                entry["reason"] = (
                    f"'{value}'은(는) 조회 가능한 항목이 아닙니다(존재성 검증 실패)"
                )
                continue
            mapping_updates[field] = (
                f"{entity}.{value}" if action == "column" else f"EAV:{value}"
            )
            entry["applied"] = True
        else:
            entry["reason"] = f"알 수 없는 답변 유형: {action or '(없음)'}"
    return overrides, mapping_updates, literals


def build_month_series_block(month_series: MonthSeries | None) -> str:
    """LLM 폴백 프롬프트용 월 리터럴 강제 블록(D-113 폴백 안전망).

    결정적 조립이 스킵되는 경로(재시도 턴 등)에서 LLM이 `CURRENT_DATE - k MONTH`류
    동적 계산으로 월 방향을 뒤집는 실측 사례(2026-07-29: M=지난달·M+5=6개월 전 역순)가
    있어, 인식기가 확정한 필드↔YYYYMM 매핑을 리터럴로 강제한다.
    """
    if not month_series:
        return ""
    lines = "\n".join(
        f'- "{fname}" ← s.stat_date = \'{ym}\''
        for fname, ym in month_series.month_by_field.items()
    )
    return (
        "## 월별 칼럼 매핑 강제 (아래 리터럴 월을 그대로 사용 — CURRENT_DATE 동적 계산 금지)\n"
        f"{lines}\n"
        "각 필드는 위 월의 값만 담아야 하며, alias는 왼쪽 필드명 그대로 사용하세요."
    )


def apply_remark_server_name_rule(
    column_mapping: dict[str, str | None],
    entity_table: str,
) -> dict[str, str]:
    """'비고' 필드를 서버 등록명(cmm_resource.name)으로 채우는 요청 스코프 규칙(D-115).

    사용자 확정(2026-07-28): 공동존·은행존 모두 폴스타 UI 정합성 확인에 등록명이 필요
    (등록명에 업무 등 정보성 텍스트 포함). field_mapper가 다른 매핑(예: b0 비고→description)을
    만들었어도 이 규칙이 우선한다(D-114 "임의 기재 금지"의 사용자 지정 예외).

    Returns:
        {비고 필드명: "<entity>.name"} 갱신분(비고 필드 없으면 빈 dict).
    """
    updates: dict[str, str] = {}
    for fname in column_mapping:
        if fname.strip() == "비고":
            updates[fname] = f"{entity_table}.name"
    return updates


def find_vendor_model_concat(
    schema_info: dict | None,
    column_mapping: dict[str, str | None],
) -> list[tuple[str, str, str]]:
    """'제조사(모델명)'류 필드를 서버 Vendor+Model 결합으로 채우는 요청 스코프 규칙(D-115).

    라이브 실측(2026-07-28): LLM/field_mapper는 이 필드를 Vendor 또는 Model **한쪽**으로만
    매핑해 반쪽 값이 채워졌다. 프로필의 server.Server 태그 속성에서 Vendor·Model의
    **정확한 대소문자 이름**을 찾아(주의: server.Cpus에도 VENDOR/MODEL이 있어
    `eav_attr_resource_types`의 upper 키는 충돌함) 결합 대상으로 지정한다.
    둘 다 프로필에 없으면 발동하지 않는다(프로필 게이트).

    Returns:
        [(필드명, Vendor속성명, Model속성명)] — 조립기 concat_eav 인자로 전달.
    """
    vendor_attr = model_attr = None
    for pattern in ((schema_info or {}).get("_structure_meta") or {}).get("patterns", []):
        if pattern.get("type") != "eav":
            continue
        attrs = pattern.get("known_attributes_detail") or pattern.get("known_attributes", [])
        for attr in attrs:
            if not isinstance(attr, dict):
                continue
            name = (attr.get("name") or "").strip()
            m = _RESOURCE_TYPE_RE.search(attr.get("description") or "")
            if not name or not m or m.group(1).strip() != _SERVER_RESOURCE_TYPE:
                continue
            if name.upper() == "VENDOR":
                vendor_attr = name
            elif name.upper() == "MODEL":
                model_attr = name
    if not vendor_attr or not model_attr:
        return []
    out: list[tuple[str, str, str]] = []
    for fname in column_mapping:
        low = fname.lower().replace(" ", "")
        if "제조사" in low and "모델" in low:
            out.append((fname, vendor_attr, model_attr))
    return out


def apply_capacity_scope_rule(
    column_mapping: dict[str, str | None],
    attr_rt: dict[str, str],
    resource_type: str,
) -> dict[str, str | None]:
    """'처리능력' 필드의 요청 스코프 규칙 — GB+메모리 문맥만 용량 매핑, 그 외는 강제 공란.

    Q1 확정(2026-07-27): 유사어 등록 없이 **단위 (GB) + 메모리 문맥 + 프로필에 TotalSize
    존재**의 3중 문맥으로만 용량(EAV TotalSize)을 매핑한다(D-115 — 전역 오염 차단).

    그 외 처리능력 필드(예: CPU 양식 '(TPMC)')는 **강제 None** — 라이브 실측(2026-07-28
    4차): field_mapper의 학습/캐시 매핑('처리능력'→TotalSize)이 CPU 양식에 유입되어
    TPMC 칼럼에 메모리 용량이 채워졌다. 미지원 단위는 매핑이 있어도 결정적으로 차단해
    공란을 보장한다(D-114 임의 기재 금지).

    Returns:
        {필드명: "EAV:TotalSize" 또는 None} 갱신분. 호출부가 로컬 매핑(None→파티션 제외)과
        state 매핑(writer 조회 경로) 양쪽에 merge한다.
    """
    updates: dict[str, str | None] = {}
    gb_ok = resource_type == "server.Memory" and "TOTALSIZE" in attr_rt
    for fname in column_mapping:
        low = fname.lower().replace(" ", "")
        if "처리능력" not in low:
            continue
        if gb_ok and "(gb)" in low:
            updates[fname] = "EAV:TotalSize"
        else:
            updates[fname] = None
    return updates


def eav_attr_resource_types(schema_info: dict | None) -> dict[str, str]:
    """EAV known_attributes 설명에서 `속성명(대문자) → resource_type` 맵을 추출한다.

    프로필 known_attributes의 description 끝에 `[resource_type: server.Cpus]` 형식으로
    각 속성이 어느 리소스 행에 붙는지 표기돼 있다(예: LOGICALCORE→server.Cpus,
    TotalSize→server.Memory). CPU 코어 수·메모리 용량 같은 자식 리소스 속성은
    server.Server 행이 아니라 자식 행(platform_resource_id로 연결)에 있으므로,
    강제 SELECT 블록이 올바른 resource_type 구분 피벗을 생성하도록 이 맵을 사용한다.

    TotalSize처럼 설명에 복수 resource_type이 있으면 **첫 번째**만 사용한다
    (양식 '메모리 용량'은 server.Memory가 정답).

    Args:
        schema_info: `_structure_meta`를 포함할 수 있는 스키마 정보 딕셔너리

    Returns:
        {속성명 대문자: resource_type} 맵. 정보가 없으면 빈 딕셔너리.
    """
    out: dict[str, str] = {}
    if not schema_info:
        return out
    structure_meta = schema_info.get("_structure_meta")
    if not structure_meta:
        return out
    for pattern in structure_meta.get("patterns", []):
        if pattern.get("type") != "eav":
            continue
        # `_load_manual_profile`은 known_attributes를 문자열 리스트로 평탄화하고 원본 객체
        # (name/description/synonyms)를 known_attributes_detail에 보존한다. resource_type 태그는
        # description에 있으므로 detail(객체 리스트)을 우선 읽는다. detail이 없으면(이미 dict인
        # 원시 구조 메타) known_attributes를 사용한다. 이 폴백이 없으면 실 런타임(수동 프로필 로드)에서
        # attr_rt가 항상 비어 폼필 결정적 피벗이 발동하지 않고 LLM 폴백으로 떨어진다(단일·멀티 공통).
        attrs = pattern.get("known_attributes_detail") or pattern.get("known_attributes", [])
        for attr in attrs:
            if not isinstance(attr, dict):
                continue
            name = (attr.get("name") or "").strip()
            desc = attr.get("description") or ""
            m = _RESOURCE_TYPE_RE.search(desc)
            if name and m:
                out[name.upper()] = m.group(1).strip()
    return out

def _metric_select_line(
    field: str,
    rt: str,
    agg_fn: str,
    val_col: str,
    db_engine: str | None,
    definition_name: str = "Utilization",
    stat_date: str | None = None,
) -> str:
    """단일 사용률/지표 필드의 SELECT 라인(엔진별 소수 보존 캐스트 포함).

    definition_name 기본값은 'Utilization'(사용률)이며, 폼필 경로는 이 값만 쓴다. 시맨틱
    컴파일러(트랙 C 패턴 B)는 'MaxIORate'(디스크 IO) 등 다른 지표도 지정할 수 있어 인자로 노출한다.

    stat_date를 주면 CASE 조건에 `AND s.stat_date='YYYYMM'`을 넣어 **특정 월의 값만** 뽑는다
    (월별 가로 피벗 — D-113). 이때 월 통계 테이블은 (resource, definition, 월)당 1행이므로
    집계는 행 복제(config×metric 이중 조인) 제거용 MAX면 충분하며 GROUP BY는 불변이다.

    Utilization에는 값 타당성 게이트(BETWEEN 0 AND 1000)를 CASE 조건에 넣어, 범위 밖 쓰레기
    행(실측 avg=1.2e9/max=5.5e13, 음수)을 필드 단위로 집계에서 제외한다(D-103). 게이트는
    definition_name='Utilization'일 때만 — MaxIORate 등엔 0~1000 의미가 없다.
    """
    guard = _utilization_guard(val_col, definition_name)
    if stat_date:
        guard = f" AND s.stat_date='{stat_date}'{guard}"
    if (db_engine or "").lower() == "db2":
        # DB2: 집계 함수 내부에서 캐스트(정수 truncate 방지). ::numeric은 문법 오류.
        # 캐스트는 DOUBLE — 고정 정밀도 DECIMAL(15,4)는 범위 밖 값(실측 5.5e13 ≥ 1e11)에서
        # SQL0413N 변환 오버플로로 쿼리 전체가 죽는다(D-103; DOUBLE은 ~1e308이라 변환 오버플로
        # 원리적 불가 — 게이트 없는 지표(MaxIORate)까지 덮는 심층 방어).
        # 또한 DB2 집계는 스케일을 크게 확장(예: scale 18)하여 ROUND(x,2)로 값은 2자리로
        # 반올림돼도 **타입 스케일이 남아** 결과가 6.51000000000000000000처럼 trailing zero로 직렬화된다
        # (엑셀 제로필). 최종을 `CAST(... AS DECIMAL(31,2))`로 감싸 스케일을 2로 고정한다(D-068 후속;
        # 정밀도는 15→31로 확장해 대형 정상값(IO rate 등)의 최종 캐스트 오버플로 여지 제거 — D-103).
        inner = f"CAST(s.{val_col} AS DOUBLE)"
        return (
            f"  CAST(ROUND({agg_fn}(CASE WHEN c.resource_type='{rt}' "
            f"AND s.definition_name='{definition_name}'{guard} THEN {inner} END), 2) AS DECIMAL(31,2)) AS \"{field}\""
        )
    return (
        f"  ROUND({agg_fn}(CASE WHEN c.resource_type='{rt}' "
        f"AND s.definition_name='{definition_name}'{guard} THEN s.{val_col} END)::numeric, 2) AS \"{field}\""
    )


def _pivot_select_parts(
    regular_entries: list[tuple[str, str]],
    server_eav: list[tuple[str, str]],
    child_eav: list[tuple[str, str, str]],
    metric_fields: list[str] | None,
    attr_col: str,
    val_col: str,
    db_engine: str | None,
    explicit_measures: list[tuple[str, str, str, str, str]] | None = None,
    month_measures: list[tuple[str, str, str, str]] | None = None,
    concat_eav: list[tuple[str, str, str]] | None = None,
) -> tuple[list[str], set[str], bool]:
    """피벗 SELECT 라인 목록·필요 resource_type 집합·metric 유무를 계산한다(블록/SQL 공용).

    metric_fields는 폼필 경로가 쓰는 한글 라벨(예: "CPU 평균")로, `classify_metric_field`로
    (resource_type, 집계함수, 값컬럼)을 추론한다. explicit_measures는 시맨틱 컴파일러(트랙 C)가
    쓰는 명시 지정으로, 라벨 분류에 의존하지 않고 (alias, resource_type, agg_fn, val_col,
    definition_name)을 직접 전달한다(MaxIORate 등 Utilization 외 지표 지원). 둘 다 주면 합쳐 넣는다.

    month_measures는 월별 가로 피벗(D-113) 명시 지정 — (alias, resource_type, 값컬럼,
    YYYYMM) 항목당 해당 월의 값을 뽑는 SELECT 라인 1개를 만든다(집계는 MAX 고정 — 월 통계는
    월당 1행이라 값 선택이며, stat_date는 GROUP BY에 넣지 않아 서버당 1행 불변식 유지).
    alias는 호출부(결정적 인식기)가 부여하며 DB2 결과 칼럼 소문자화 대응을 위해 라틴 소문자를
    권장한다(예: cpu_m0_avg).
    """
    lines: list[str] = []
    rtset: set[str] = {_SERVER_RESOURCE_TYPE}
    for field, col in regular_entries:
        bare = col.split(".")[-1]
        lines.append(
            f"  MAX(CASE WHEN c.resource_type='{_SERVER_RESOURCE_TYPE}' "
            f'THEN c.{bare} END) AS "{field}"'
        )
    for field, attr in server_eav:
        lines.append(
            f"  MAX(CASE WHEN c.resource_type='{_SERVER_RESOURCE_TYPE}' "
            f"AND cc.{attr_col}='{attr}' THEN cc.{val_col} END) AS \"{field}\""
        )
    for field, attr, rt in child_eav:
        rtset.add(rt)
        lines.append(
            f"  MAX(CASE WHEN c.resource_type='{rt}' "
            f"AND cc.{attr_col}='{attr}' THEN cc.{val_col} END) AS \"{field}\""
        )
    # 두 서버 EAV 속성 결합 — "Vendor(Model)" 형태(D-115 제조사(모델명) 규칙).
    # `||`·CASE·NULLIF는 PostgreSQL/DB2 공통 문법. 둘 다 NULL이면 NULL(공란 유지).
    for field, attr_a, attr_b in concat_eav or []:
        a = (
            f"MAX(CASE WHEN c.resource_type='{_SERVER_RESOURCE_TYPE}' "
            f"AND cc.{attr_col}='{attr_a}' THEN cc.{val_col} END)"
        )
        b = (
            f"MAX(CASE WHEN c.resource_type='{_SERVER_RESOURCE_TYPE}' "
            f"AND cc.{attr_col}='{attr_b}' THEN cc.{val_col} END)"
        )
        lines.append(
            f"  NULLIF(COALESCE({a}, '') || CASE WHEN {b} IS NOT NULL "
            f"THEN '(' || {b} || ')' ELSE '' END, '') AS \"{field}\""
        )
    has_metric = False
    for field in metric_fields or []:
        cls = classify_metric_field(field)
        if not cls:
            continue
        rt, agg_fn, mval = cls
        rtset.add(rt)
        has_metric = True
        lines.append(_metric_select_line(field, rt, agg_fn, mval, db_engine))
    for alias, rt, agg_fn, mval, defn in explicit_measures or []:
        rtset.add(rt)
        has_metric = True
        lines.append(_metric_select_line(alias, rt, agg_fn, mval, db_engine, defn))
    for alias, rt, mval, month in month_measures or []:
        if not re.fullmatch(r"\d{6}", month):
            raise ValueError(f"month_measures 월 형식 오류(YYYYMM 아님): {month!r}")
        rtset.add(rt)
        has_metric = True
        lines.append(
            _metric_select_line(alias, rt, "MAX", mval, db_engine, stat_date=month)
        )
    return lines, rtset, has_metric


def _eav_pattern_parts(eav_pattern: dict) -> tuple[str, str, str, str, str, str]:
    """eav_pattern에서 (entity, config, attr_col, val_col, ent_join, cfg_join)을 뽑는다."""
    entity = eav_pattern.get("entity_table", "cmm_resource")
    config = eav_pattern.get("config_table", "core_config_prop")
    attr_col = eav_pattern.get("attribute_column", "name")
    val_col = eav_pattern.get("value_column", "stringvalue_short")
    direct_join = eav_pattern.get("direct_join", {}) or {}
    ent_join = direct_join.get("entity_column", "resource_conf_id")
    cfg_join = direct_join.get("config_column", "configuration_id")
    return entity, config, attr_col, val_col, ent_join, cfg_join


def build_multi_resource_pivot_sql(
    regular_entries: list[tuple[str, str]],
    server_eav: list[tuple[str, str]],
    child_eav: list[tuple[str, str, str]],
    eav_pattern: dict,
    metric_fields: list[str] | None = None,
    db_engine: str | None = None,
    db_schema: str | None = None,
    limit: int | None = None,
    stat_month: StatMonth = None,
    metric_table: str = "cmm_metric_stat_m",
    explicit_measures: list[tuple[str, str, str, str, str]] | None = None,
    month_measures: list[tuple[str, str, str, str]] | None = None,
    concat_eav: list[tuple[str, str, str]] | None = None,
    server_scope: tuple[str, list[str]] | None = None,
    order_by: tuple[str, str] | None = None,
) -> str:
    """폼필/시맨틱 다중 리소스 피벗을 **runnable SQL로 결정적 조립**한다(LLM 우회, D-068 2차).

    프롬프트로 스켈레톤을 "제안"하면 LLM이 프로필 few-shot 예시(월별 GROUP BY 등)와 경쟁해
    무시·변형(서버 중복·config 누락)한다. 이 well-defined 쿼리는 코드가 직접 조립하여 LLM
    변동성을 제거한다. 조인 패턴은 프로필의 검증된 예시와 동일하되, 사용률까지 **단일 GROUP BY
    스코프**에 합친다(config·metric 이중 조인은 집계값에 불변). 시맨틱 컴파일러(트랙 C, D-076)가
    이 함수를 패턴 A(서버설정)+B(성능지표) 조립 엔진으로 **재사용**한다(이중 조립 엔진 금지 — D-067).

    Args:
        regular_entries/server_eav/child_eav/eav_pattern/metric_fields/db_engine: 피벗 구성요소
        db_schema: 스키마 한정자(polestar 등, DB2는 대문자 POLESTAR — D-057). 비면 무한정.
        limit: 결과 상한(엔진별 LIMIT/FETCH FIRST). None이면 미적용.
        stat_month: 사용률 기간 필터 — 단일 월 YYYYMM(예: '202506') 또는 (시작, 끝) 범위
            (예: ('202504', '202506') → BETWEEN, D-102). None이면 전체 월 평균.
        metric_table: 월별 통계 테이블명(폴스타 기본 cmm_metric_stat_m).
        explicit_measures: 시맨틱 컴파일러용 명시 measure (alias, resource_type, agg_fn,
            val_col, definition_name). metric_fields의 한글라벨 분류 대신 직접 지정(패턴 B).
        month_measures: 월별 가로 피벗 measure (alias, resource_type, val_col, YYYYMM) —
            항목당 해당 월 값 1칼럼(D-113, 금감원 M~M+5 양식 등). stat_date는 SELECT의
            CASE 피벗으로만 쓰고 GROUP BY는 불변(서버당 1행 계약 유지). 조인 월 필터는
            항목들의 (최소, 최대) 월 범위로 자동 산출하며 stat_month보다 우선한다.
        server_scope: 선행 결과 서버 한정 (식별컬럼, 값목록) — HAVING의 집계 CASE WHEN으로
            적용한다(WHERE에 두면 자식 리소스 행이 탈락해 0건 — D-096). None이면 미적용.
        order_by: 순위 정렬 (SELECT alias, "DESC"|"ASC"). NULL이 1위를 차지하지 않도록
            NULLS LAST를 항상 부여한다(D-098 — PostgreSQL DESC 기본은 NULLS FIRST).

    Returns:
        실행 가능한 SQL 문자열(세미콜론 종결).
    """
    entity, config, attr_col, val_col, ent_join, cfg_join = _eav_pattern_parts(eav_pattern)
    lines, rtset, has_metric = _pivot_select_parts(
        regular_entries, server_eav, child_eav, metric_fields, attr_col, val_col, db_engine,
        explicit_measures=explicit_measures, month_measures=month_measures,
        concat_eav=concat_eav,
    )

    def q(table: str) -> str:
        return f"{db_schema}.{table}" if db_schema else table

    metric_join = ""
    if has_metric:
        if month_measures:
            # 월별 가로 피벗: 조인 필터는 measure들의 월 범위로 산출(진행 중 달 등
            # 범위 밖 행을 조인에서 제외 — SELECT의 stat_date CASE 피벗과 이중 안전).
            months = sorted({m[3] for m in month_measures})
            month_rng = (months[0], months[-1])
        else:
            month_rng = _normalize_stat_month(stat_month)
        if not month_rng:
            month_cond = ""
        elif month_rng[0] == month_rng[1]:
            month_cond = f" AND s.stat_date = '{month_rng[0]}'"
        else:
            month_cond = f" AND s.stat_date BETWEEN '{month_rng[0]}' AND '{month_rng[1]}'"
        # 폼필 경로(metric_fields)는 Utilization만 쓰므로 단일 동등 필터를 유지(기존 출력 보존).
        # 시맨틱 패턴 B가 여러 definition_name(Utilization+MaxIORate)을 쓰면 IN 필터로 확장한다.
        defs = sorted({m[4] for m in (explicit_measures or [])}) or ["Utilization"]
        if len(defs) == 1:
            def_cond = f"s.definition_name = '{defs[0]}'"
        else:
            def_cond = "s.definition_name IN (" + ", ".join(f"'{d}'" for d in defs) + ")"
        metric_join = (
            f"\nLEFT JOIN {q(metric_table)} s ON s.resource_id = c.id "
            f"AND {def_cond}{month_cond}"
        )

    rt_in = ", ".join(f"'{r}'" for r in sorted(rtset))
    select_block = ",\n".join(lines)
    sql = (
        "SELECT\n"
        f"{select_block}\n"
        f"FROM {q(entity)} c\n"
        f"LEFT JOIN {q(config)} cc ON cc.{cfg_join} = c.{ent_join}"
        f"{metric_join}\n"
        f"WHERE c.resource_type IN ({rt_in})\n"
        "  AND c.dtime IS NULL\n"
        "GROUP BY COALESCE(c.platform_resource_id, c.id)"
    )
    if server_scope:
        scope_col, scope_values = server_scope
        if scope_values:
            quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in scope_values)
            sql += (
                f"\nHAVING MAX(CASE WHEN c.resource_type='{_SERVER_RESOURCE_TYPE}' "
                f"THEN c.{scope_col} END) IN ({quoted})"
            )
    if order_by:
        alias, direction = order_by
        dir_kw = "DESC" if str(direction).upper() != "ASC" else "ASC"
        # NULLS LAST 필수: 값이 없는 서버가 정렬 선두를 차지해 임의 서버가 1위로 뽑히는 것을 방지(D-098).
        sql += f'\nORDER BY "{alias}" {dir_kw} NULLS LAST'
    if limit:
        if (db_engine or "").lower() == "db2":
            sql += f"\nFETCH FIRST {limit} ROWS ONLY"
        else:
            sql += f"\nLIMIT {limit}"
    return sql + ";"


def build_multi_resource_pivot_block(
    regular_entries: list[tuple[str, str]],
    server_eav: list[tuple[str, str]],
    child_eav: list[tuple[str, str, str]],
    eav_pattern: dict,
    metric_fields: list[str] | None = None,
    db_engine: str | None = None,
    metric_table: str = "cmm_metric_stat_m",
) -> str:
    """서버 + 자식 리소스(server.Cpus/Memory) 속성 + 사용률 통계를 **한 쿼리**로 피벗하는 결정적 지침.

    LLM 프롬프트용 텍스트 버전(결정적 SQL 조립이 불가한 경로의 폴백). 실제 폼필 멀티 경로는
    `build_multi_resource_pivot_sql`로 SQL을 직접 조립한다(D-068 2차). 자식 리소스 속성이 하나라도
    있을 때만 호출한다.
    """
    entity, config, attr_col, val_col, ent_join, cfg_join = _eav_pattern_parts(eav_pattern)
    lines, rtset, has_metric = _pivot_select_parts(
        regular_entries, server_eav, child_eav, metric_fields, attr_col, val_col, db_engine
    )
    rt_in = ", ".join(f"'{r}'" for r in sorted(rtset))
    select_block = ",\n".join(lines)

    metric_join = ""
    metric_note = ""
    if has_metric:
        metric_join = (
            f"\nLEFT JOIN {metric_table} s ON s.resource_id = c.id "
            "AND s.definition_name = 'Utilization'"
        )
        metric_note = (
            "\n- 사용률(CPU/메모리 평균·최고)은 위 `s` 조인으로 같은 GROUP BY에서 집계했습니다. "
            "기간 조건(예: '지난달 1개월', '지난 3개월')은 **s.stat_date**(YYYYMM 문자열)에 적용하세요"
            "(단일 월: AND s.stat_date = '지난달YYYYMM' / N개월: AND s.stat_date BETWEEN '시작YYYYMM' "
            f"AND '직전월YYYYMM' — 진행 중인 달 제외). 통계 테이블은 반드시 월별 {metric_table}만 "
            "사용하고 _h/_d는 쓰지 마세요."
        )

    return (
        "## 서버 종합 정보 + 사용률 통합 피벗 (반드시 이 하나의 쿼리 형식 그대로)\n"
        "양식의 서버/CPU/메모리/OS 속성은 한 서버 안에서 **여러 resource_type 행**"
        "(server.Server, server.Cpus, server.Memory 등)에 분산돼 있고, 사용률 통계도 자식 리소스에 "
        f"붙습니다. 같은 서버의 자식 리소스는 {entity}.platform_resource_id로 묶입니다. 서버 행에만 "
        "조인하면 CPU 코어 수·메모리 용량·사용률이 전부 NULL이 되므로, 반드시 아래처럼 resource_type "
        "구분 CASE WHEN 피벗 + 단일 GROUP BY로 **하나의 쿼리**로 작성하세요(별도 블록으로 쪼개지 마세요):\n\n"
        "```sql\n"
        "SELECT\n"
        f"{select_block}\n"
        f"FROM {entity} c\n"
        f"LEFT JOIN {config} cc ON cc.{cfg_join} = c.{ent_join}"
        f"{metric_join}\n"
        f"WHERE c.resource_type IN ({rt_in})\n"
        f"GROUP BY COALESCE(c.platform_resource_id, c.id)\n"
        "```\n"
        "- 결과 alias는 반드시 위 양식 필드명(한글, 따옴표 포함) 그대로 — 임의 영문 alias 금지.\n"
        "- 모든 비집계 컬럼은 위처럼 MAX(CASE ...)로 감싸세요. **집계 밖의 맨 컬럼(r.name 등) 금지** "
        "— GROUP BY 위반이 됩니다.\n"
        "- WHERE에 c.name='...' 등 서버 필터를 직접 두지 마세요(자식 행이 GROUP BY 전에 걸러져 "
        "NULL). 특정 서버 한정은 HAVING을 사용하세요.\n"
        f"- {val_col}를 다른 서버 행 config에 브릿지 조인하지 마세요."
        f"{metric_note}"
    )
