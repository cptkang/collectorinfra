"""양식 필드 매핑 2·2.5단계 — 유사어 사전 정확·퍼지·임베딩 매칭과 EAV 속성 유사어 매칭.

`src.document.field_mapper`(3단계 매핑 Facade)에서 옮겼다(plans/166 I2). 종전 경로는 Facade가
옛 이름(`_` 접두 포함)으로 재노출한다(D-337 ①·③).
"""

from __future__ import annotations

import logging

from src.document.mapping_steps import MappingResult
from src.utils.query_gen_common import is_servername_to_hostname

# 로거 이름은 이동 전 경로 고정 — 로그 %(name)s 불변(plans/166 I2)
logger = logging.getLogger("src.document.field_mapper")

def _scope_synonyms(
    all_db_synonyms: dict[str, dict[str, list[str]]],
    sub_table_scope: dict[str, frozenset[str]] | None,
) -> dict[str, dict[str, list[str]]]:
    """제한 대상 DB의 유사어에서 선언 밖 테이블 컬럼을 뺀 사본을 만든다(plans/120 F-2).

    제한 대상이 없으면 입력을 그대로 돌려준다(종전 동작과 같은 객체).
    """
    if not sub_table_scope:
        return all_db_synonyms
    scoped: dict[str, dict[str, list[str]]] = {}
    for db_id, synonyms in all_db_synonyms.items():
        tables = sub_table_scope.get(db_id)
        if tables is None:
            scoped[db_id] = synonyms
            continue
        scoped[db_id] = {
            col: words for col, words in synonyms.items()
            if _synonym_key_table(col) in tables
        }
    return scoped


def _synonym_key_table(col: str) -> str:
    """유사어 키(``[schema.]table.column``)의 테이블명(소문자) — Pass 1·2와 같은 추출 규칙."""
    parts = col.split(".")
    return parts[-2].lower() if len(parts) >= 2 else parts[0].lower()


# === Step 2: Synonym Mapping ===


def apply_synonym_mapping(
    remaining: set[str],
    all_db_synonyms: dict[str, dict[str, list[str]]],
    priority_db_ids: list[str],
    result: MappingResult,
    *,
    fuzzy: bool = False,
    min_score: float = 0.85,
    semantic: bool = False,
    semantic_min: float = 0.65,
    core_tables: set[str] | None = None,
    sub_table_scope: dict[str, frozenset[str]] | None = None,
) -> None:
    """Redis synonyms 기반 매핑을 수행한다.

    priority_db_ids를 먼저 검색하여 우선순위를 부여한다.

    Plan 61 트랙 B(E5-1): ``fuzzy=True``이면 정확 동등 매칭(Pass 1/2)이 모두 실패한
    잔여 필드에 대해 **유연 근사 매칭 폴백(Pass 3)**을 수행한다. 신뢰도가 ``min_score``
    이상이면 확정 매핑(source="synonym")으로 채택하고, 미만이면 채택하지 않아 다운스트림
    LLM 유사어 발견(2.8)·통합 추론(3) 경로가 그대로 ``pending_synonym_registrations``
    승인 루프로 회부한다("임계 이하는 확정 아닌 후보 제시"의 결정적 실현). ``fuzzy=False``
    (기본)이면 Pass 3은 실행되지 않아 기존 동작과 **바이트 단위 동일**하다(회귀 0).

    Plan 61 트랙 B(E5-4, D-084): ``semantic=True``이면 정확·퍼지 계단이 모두 실패한
    잔여 필드에 **임베딩 의미 매칭 폴백(Pass 4)**을 계단 마지막 단으로 수행한다.
    코사인이 ``semantic_min`` 이상이면 확정 채택, 미만이면 동일하게 LLM 경로에 위임한다.
    ``semantic=False``(기본)이면 Pass 4는 실행되지 않는다(회귀 0).

    Args:
        remaining: 매핑되지 않은 필드 set
        all_db_synonyms: DB별 synonyms {db_id: {table.column: [words]}}
        priority_db_ids: 우선순위 DB 목록
        result: 매핑 결과 객체
        fuzzy: 유연 근사 매칭 폴백 활성화 여부(플래그, 기본 OFF)
        min_score: 유연 매칭 확정 신뢰도 임계(fuzzy=True일 때만 사용)
        semantic: 임베딩 의미 매칭 폴백 활성화 여부(플래그, 기본 OFF)
        semantic_min: 임베딩 코사인 확정 임계(semantic=True일 때만 사용)
        core_tables: 먼저 매칭할 핵심 엔터티 테이블명(소문자). 구조 선언에서 도출하며
            (`_core_entity_tables`), 비어 있으면 우선순위 구분 없이 전체 테이블을
            한 번에 매칭한다(Pass 1이 공집합이 되어 Pass 2가 전부 담당).
        sub_table_scope: {db_id: 구조 선언 테이블} — 이 DB들은 Pass 2~4 후보를 선언 테이블로
            한정한다(`_sub_table_scope` · plans/120 F-2). 선언 밖 정확 매칭은 거부 로그만 남기고
            다음 단계(LLM · 역질문)로 넘긴다. 없거나 빈 dict면 종전 동작.
    """
    if priority_db_ids:
        ordered_db_ids = priority_db_ids
    else:
        ordered_db_ids = list(all_db_synonyms.keys())

    from src.utils.schema_utils import normalize_field_name

    CORE_TABLES = core_tables or set()  # noqa: N806
    scope = sub_table_scope or {}

    for field in list(remaining):
        field_lower = normalize_field_name(field).lower()
        matched = False

        # Pass 1: 핵심 테이블(cmm_resource 등)에 속한 컬럼 유의어를 우선 매칭
        for db_id in ordered_db_ids:
            synonyms = all_db_synonyms.get(db_id, {})
            core_syns = {}
            for col, words in synonyms.items():
                parts = col.split(".")
                table_name = parts[-2].lower() if len(parts) >= 2 else parts[0].lower()
                if table_name in CORE_TABLES:
                    core_syns[col] = words
            matched_column = synonym_match(field_lower, core_syns)
            if matched_column:
                result.db_column_mapping.setdefault(db_id, {})[field] = matched_column
                result.mapping_sources[field] = "synonym"
                remaining.discard(field)
                matched = True
                logger.info(
                    "[동의어] 정확 매칭 확정(핵심 테이블): '%s' -> %s (db=%s)",
                    field, matched_column, db_id,
                )
                break

        if matched:
            continue

        # Pass 2: 핵심 테이블에서 발견되지 않은 필드에 대해 기타 서브 테이블 컬럼 매칭 시도
        # (EAV 피벗 DB는 구조 선언 테이블 안에서만 — plans/120 F-2)
        for db_id in ordered_db_ids:
            synonyms = all_db_synonyms.get(db_id, {})
            declared = scope.get(db_id)
            non_core_syns = {}
            outside_syns = {}
            for col, words in synonyms.items():
                parts = col.split(".")
                table_name = parts[-2].lower() if len(parts) >= 2 else parts[0].lower()
                if table_name not in CORE_TABLES:
                    if declared is not None and table_name not in declared:
                        outside_syns[col] = words
                    else:
                        non_core_syns[col] = words
            matched_column = synonym_match(field_lower, non_core_syns)
            if matched_column:
                result.db_column_mapping.setdefault(db_id, {})[field] = matched_column
                result.mapping_sources[field] = "synonym"
                remaining.discard(field)
                logger.info(
                    "[동의어] 정확 매칭 확정(서브 테이블): '%s' -> %s (db=%s)",
                    field, matched_column, db_id,
                )
                break
            rejected_column = synonym_match(field_lower, outside_syns) if outside_syns else None
            if rejected_column:
                logger.info(
                    "[동의어] 서브 테이블 매칭 거부(구조 선언 밖 — plans/120 F-2): "
                    "'%s' -> %s (db=%s) → 다음 단계로",
                    field, rejected_column, db_id,
                )

    # Pass 3 (E5-1 유연 매칭 폴백): 플래그 ON일 때만, 정확 매칭이 실패한 잔여 필드에
    # 유연 근사 매칭을 시도한다. 임계 이상만 확정 채택하고, 미만은 다운스트림 LLM 경로에
    # 위임(후보 제시)한다. fuzzy=False면 이 블록은 진입하지 않아 회귀가 없다.
    # Pass 3·4도 Pass 2와 같은 후보 한정을 받는다 — 선언 밖 정확 일치 단어는 근사 점수 1.0이라
    # 한정하지 않으면 Pass 2에서 거부한 매핑이 그대로 되살아난다(plans/120 F-2).
    if fuzzy and remaining:
        _apply_fuzzy_synonym_fallback(
            remaining, _scope_synonyms(all_db_synonyms, scope), ordered_db_ids, result, min_score
        )

    # Pass 4 (E5-4 임베딩 의미 매칭 폴백, D-084): 정확·퍼지 계단이 모두 실패한 잔여
    # 필드에 임베딩 의미 매칭을 마지막 보루로 시도한다. 임계 이상만 확정 채택하고,
    # 미만은 다운스트림 LLM 경로에 위임(후보 제시)한다. semantic=False면 미진입(회귀 0).
    if semantic and remaining:
        _apply_semantic_synonym_fallback(
            remaining, _scope_synonyms(all_db_synonyms, scope), ordered_db_ids, result,
            semantic_min,
        )


def _apply_fuzzy_synonym_fallback(
    remaining: set[str],
    all_db_synonyms: dict[str, dict[str, list[str]]],
    ordered_db_ids: list[str],
    result: MappingResult,
    min_score: float,
) -> None:
    """유연 근사 매칭으로 잔여 필드를 매핑한다(E5-1, 확정 임계 이상만 채택).

    각 DB의 컬럼 유사어 단어들과 필드명을 flex 매칭하여 최고 신뢰도 후보를 찾는다.
    신뢰도가 min_score 이상이면 확정 매핑(source="synonym")으로 채택한다. 미만이면
    채택하지 않아 이후 LLM 유사어 발견/추론 경로가 승인 루프로 회부한다.

    Args:
        remaining: 매핑되지 않은 필드 set
        all_db_synonyms: DB별 synonyms {db_id: {table.column: [words]}}
        ordered_db_ids: 우선순위 정렬된 DB 목록
        result: 매핑 결과 객체
        min_score: 확정 채택 최소 신뢰도
    """
    from src.utils.flex_match import flex_match_score
    from src.utils.schema_utils import normalize_field_name

    for field in list(remaining):
        field_lower = normalize_field_name(field).lower()
        if len(field_lower) < 2:
            continue

        best_db: str | None = None
        best_col: str | None = None
        best_score = 0.0

        for db_id in ordered_db_ids:
            synonyms = all_db_synonyms.get(db_id, {})
            for col_key, words in synonyms.items():
                # 서버명류 필드의 hostname 컬럼 근사 매칭 차단 — 전역/EAV 유사어의
                # `Hostname: [서버명, ...]` 미끼가 fuzzy 경로로 재유입되는 오염 방지
                # (D-068 5·6차 등록 가드와 동일 계열, 정확일치 경로와 동일 규칙).
                if is_servername_to_hostname(field, col_key):
                    continue
                # 등록 유사어 단어 + 컬럼명 자체를 후보로 삼는다.
                candidates = list(words or [])
                col_name = col_key.split(".", 1)[-1] if "." in col_key else col_key
                candidates.append(col_name)
                for cand in candidates:
                    if not cand or len(str(cand)) < 2:
                        continue
                    score = flex_match_score(field_lower, str(cand))
                    if score > best_score:
                        best_score = score
                        best_col = col_key
                        best_db = db_id

        if best_col and best_db and best_score >= min_score:
            result.db_column_mapping.setdefault(best_db, {})[field] = best_col
            result.mapping_sources[field] = "synonym"
            remaining.discard(field)
            logger.info(
                "[동의어] 유연(퍼지) 매칭 확정: '%s' -> %s (db=%s, 신뢰도=%.2f)",
                field, best_col, best_db, best_score,
            )
        elif best_col:
            logger.info(
                "[동의어] 유연(퍼지) 매칭 임계 미달(후보 제시로 위임): "
                "'%s' ~ %s (신뢰도=%.2f < %.2f)",
                field, best_col, best_score, min_score,
            )


def _apply_semantic_synonym_fallback(
    remaining: set[str],
    all_db_synonyms: dict[str, dict[str, list[str]]],
    ordered_db_ids: list[str],
    result: MappingResult,
    min_score: float,
) -> None:
    """임베딩 의미 매칭으로 잔여 필드를 매핑한다(E5-4, 확정 임계 이상만 채택).

    각 DB의 컬럼 유사어 단어들과 필드명을 임베딩 코사인으로 비교해 최고 신뢰도
    후보를 찾는다. min_score 이상이면 확정 매핑(source="synonym")으로 채택하고,
    미만이면 채택하지 않아 이후 LLM 유사어 발견/추론 경로가 승인 루프로 회부한다.
    모델·의존성 미가용 시 semantic_match_candidates가 빈 리스트를 반환해 아무
    것도 채택하지 않는다(미가용 사유는 모듈에서 경고 1회 가시화).

    Args:
        remaining: 매핑되지 않은 필드 set
        all_db_synonyms: DB별 synonyms {db_id: {table.column: [words]}}
        ordered_db_ids: 우선순위 정렬된 DB 목록
        result: 매핑 결과 객체
        min_score: 확정 채택 최소 코사인 유사도
    """
    from src.schema_cache.synonym_semantic import semantic_match_candidates
    from src.utils.schema_utils import normalize_field_name

    for field in list(remaining):
        field_lower = normalize_field_name(field).lower()
        if len(field_lower) < 2:
            continue

        best_db: str | None = None
        best_col: str | None = None
        best_score = 0.0

        for db_id in ordered_db_ids:
            synonyms = all_db_synonyms.get(db_id, {})
            candidate_map: dict[str, list[str]] = {}
            for col_key, words in synonyms.items():
                # 서버명류 필드의 hostname 컬럼 근사 매칭 차단 — flex 폴백(Pass 3)과
                # 동일 가드(D-068 계열). 임베딩 유사도로도 재유입되지 않게 원천 제외.
                if is_servername_to_hostname(field, col_key):
                    continue
                # 등록 유사어 단어 + 컬럼명 자체를 후보로 삼는다(Pass 3과 동일 규칙).
                candidates = [str(w) for w in (words or []) if w and len(str(w)) >= 2]
                col_name = col_key.split(".", 1)[-1] if "." in col_key else col_key
                if col_name and len(col_name) >= 2:
                    candidates.append(col_name)
                if candidates:
                    candidate_map[col_key] = candidates

            ranked = semantic_match_candidates(field_lower, candidate_map)
            if ranked and ranked[0][1] > best_score:
                best_col, best_score = ranked[0]
                best_db = db_id

        if best_col and best_db and best_score >= min_score:
            result.db_column_mapping.setdefault(best_db, {})[field] = best_col
            result.mapping_sources[field] = "synonym"
            remaining.discard(field)
            logger.info(
                "[동의어] 의미(임베딩) 매칭 확정: '%s' -> %s (db=%s, 신뢰도=%.2f)",
                field, best_col, best_db, best_score,
            )
        elif best_col:
            logger.info(
                "[동의어] 의미(임베딩) 매칭 임계 미달(후보 제시로 위임): "
                "'%s' ~ %s (신뢰도=%.2f < %.2f)",
                field, best_col, best_score, min_score,
            )


def synonym_match(
    field_lower: str,
    synonyms: dict[str, list[str]],
) -> str | None:
    """필드명을 synonyms에서 매칭한다.

    정규화된 소문자 필드명과 synonym 단어들을 비교한다.
    공백을 제거한 형태(space-stripped)로도 비교하여 유연한 매칭을 지원한다.

    Args:
        field_lower: 정규화된 소문자 필드명
        synonyms: {table.column: [synonym_words]}

    Returns:
        매칭된 table.column 또는 None
    """
    from src.utils.schema_utils import normalize_field_name

    field_lower = field_lower.lower()

    field_no_space = field_lower.replace(" ", "")

    for col_key, words in synonyms.items():
        for word in words:
            word_norm = normalize_field_name(word).lower()
            if word_norm == field_lower or word_norm.replace(" ", "") == field_no_space:
                return col_key
        # 컬럼명 자체도 매칭 시도
        col_name = col_key.split(".", 1)[-1] if "." in col_key else col_key
        col_lower = col_name.lower()
        if col_lower == field_lower or col_lower.replace("_", "").replace(" ", "") == field_no_space:  # noqa: E501
            return col_key

    return None


# === Step 2.5: EAV Synonym Mapping ===


def apply_eav_synonym_mapping(
    remaining: set[str],
    eav_name_synonyms: dict[str, list[str]],
    result: MappingResult,
    eav_db_id: str = "_default",
    global_synonyms: dict[str, list[str]] | None = None,
) -> None:
    """EAV 속성 유사어로 매핑을 수행한다.

    EAV 속성명(OSType, Vendor 등)의 유사어에서 필드명이 매칭되면
    "EAV:속성명" 형식으로 매핑한다. global_synonyms에서도 병합하여 비교.
    공백을 제거한 형태(space-stripped)로도 비교하여 유연한 매칭을 지원한다.

    Args:
        remaining: 아직 매핑되지 않은 필드 set
        eav_name_synonyms: {eav_name: [유사어 목록]}
        result: 매핑 결과 객체
        eav_db_id: EAV 패턴이 존재하는 DB의 식별자
        global_synonyms: 글로벌 유사어 사전 (선택)
    """
    from src.utils.schema_utils import normalize_field_name

    for field in list(remaining):
        field_norm = normalize_field_name(field).lower()
        field_no_space = field_norm.replace(" ", "")
        for eav_name, words in eav_name_synonyms.items():
            # eav_names의 words + global에 같은 이름으로 등록된 words 병합
            combined_words = list(words)
            if global_synonyms and eav_name in global_synonyms:
                for gw in global_synonyms[eav_name]:
                    if gw not in combined_words:
                        combined_words.append(gw)

            matched = False
            matched_word = None
            for word in combined_words:
                word_norm = normalize_field_name(word).lower()
                if word_norm == field_norm or word_norm.replace(" ", "") == field_no_space:
                    matched = True
                    matched_word = word
                    break
            # EAV 속성명 자체도 매칭 시도
            if not matched:
                eav_norm = normalize_field_name(eav_name).lower()
                if eav_norm == field_norm or eav_norm.replace(" ", "") == field_no_space:
                    matched = True
                    matched_word = eav_name
            if matched:
                eav_key = f"EAV:{eav_name}"
                result.db_column_mapping.setdefault(eav_db_id, {})[field] = eav_key
                result.mapping_sources[field] = "eav_synonym"
                remaining.discard(field)
                logger.info(
                    "[동의어] EAV 유사어 매칭 확정: '%s' -> %s (단어='%s', db=%s)",
                    field, eav_key, matched_word, eav_db_id,
                )
                break
