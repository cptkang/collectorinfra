"""시맨틱 카탈로그 탐색·커버리지 판정 도구 (Plan 67 Phase S1 §4.2).

단계적 컬럼 도출 루프가 "선택 가능한 dimension/measure가 무엇인지" 확인하고, 누적한
SMQ가 결정적 컴파일 가능한지 중간 판정하는 데 쓰는 순수 함수 계층이다.

카탈로그(시맨틱 모델 dict)는 **인자로 주입**한다 — 로더를 내장하지 않으므로 현행
`load_semantic_model` 산출물이든 자동 생성 카탈로그(R1)든 동일하게 받는다.
"""

from __future__ import annotations

from typing import Any, Optional

from src.nodes.semantic_compiler import SMQ, check_coverage
from src.utils.flex_match import flex_match_score

# 카탈로그 항목 종류 — 시맨틱 모델의 패턴별 선택지.
KIND_DIMENSION = "dimension"
KIND_MEASURE = "measure"
KIND_ALARM_ENTITY = "alarm_entity"
KIND_ALARM_DIMENSION = "alarm_dimension"


def catalog_entries(model: dict) -> list[dict]:
    """시맨틱 모델의 선택 가능 항목을 평탄한 목록으로 만든다.

    항목에 상위어(``parent``)가 선언돼 있으면 함께 싣는다(N4/D-133) — 검색이 상위어를
    하위 항목의 정확 일치로 오인하지 않도록 채점에서 쓰고, 호출부가 계층을 볼 수 있게 한다.

    Args:
        model: 시맨틱 모델 dict(pattern_a/pattern_b/pattern_c)

    Returns:
        [{kind, name, resource_type, aliases, unsupported, parent?}] 목록(모델이 없으면 빈 목록)
    """
    if not model:
        return []
    entries: list[dict] = []

    pattern_a = model.get("pattern_a") or {}
    for dim in pattern_a.get("dimensions") or []:
        name = dim.get("name")
        if not name:
            continue
        entry = {
            "kind": KIND_DIMENSION,
            "name": name,
            "resource_type": dim.get("resource_type"),
            "aliases": [str(a) for a in (dim.get("aliases") or [])],
            # LOB 속성은 컴파일러가 조립할 수 없어 커버리지 밖으로 떨어진다.
            "unsupported": bool(dim.get("lob")),
        }
        if dim.get("parent"):
            entry["parent"] = str(dim["parent"])
        entries.append(entry)

    pattern_b = model.get("pattern_b") or {}
    for measure in pattern_b.get("measures") or []:
        name = measure.get("definition_name")
        if not name:
            continue
        entry = {
            "kind": KIND_MEASURE,
            "name": name,
            "resource_type": measure.get("resource_type"),
            "aliases": [str(a) for a in (measure.get("aliases") or [])],
            "unsupported": False,
        }
        if measure.get("parent"):
            entry["parent"] = str(measure["parent"])
        entries.append(entry)

    pattern_c = model.get("pattern_c") or {}
    for name in (pattern_c.get("entities") or {}):
        entries.append({
            "kind": KIND_ALARM_ENTITY, "name": name,
            "resource_type": None, "aliases": [], "unsupported": False,
        })
    for name in (pattern_c.get("dimensions") or {}):
        entries.append({
            "kind": KIND_ALARM_DIMENSION, "name": name,
            "resource_type": None, "aliases": [], "unsupported": False,
        })
    return entries


def _entry_score(term: str, entry: dict) -> float:
    """용어와 카탈로그 항목(이름·별칭)의 최고 매칭 점수를 구한다(정확 → 부분어 → 퍼지).

    항목의 상위어(``parent``)와 같은 표면어는 정확 일치로 인정하지 않는다 — 상위어는 형제
    전부를 가리키므로 하나만 1.0으로 끌어올리면 나머지가 후보에서 밀린다(N4/D-133). 부분어
    점수는 그대로 남아 형제들이 **동일 점수로 함께** 후보에 오른다.
    """
    term_low = term.lower()
    parent_low = str(entry.get("parent") or "").strip().lower()
    best = 0.0
    for candidate in [str(entry.get("name") or "")] + list(entry.get("aliases") or []):
        cand_low = candidate.lower()
        if not cand_low:
            continue
        if parent_low and cand_low == parent_low:
            continue
        if cand_low == term_low:
            return 1.0
        if term_low in cand_low or cand_low in term_low:
            best = max(best, 0.9)
            continue
        best = max(best, flex_match_score(term_low, cand_low))
    return best


def search_catalog(
    term: str,
    model: dict,
    *,
    limit: int = 8,
    min_score: float = 0.6,
) -> list[dict]:
    """용어와 관련된 카탈로그 항목(dimension/measure/알람 엔터티)을 검색한다.

    이름·별칭을 정확 일치 → 부분어 → 유연(자모·편집거리) 순으로 채점하고 임계 이상만
    점수 내림차순으로 반환한다. LLM이 카탈로그 밖 컬럼을 지어내지 않도록, 반환 항목은
    전부 시맨틱 모델이 정의한 검증된 선택지다.

    Args:
        term: 사용자 질의에서 뽑은 용어
        model: 시맨틱 모델 dict
        limit: 반환 상한(토큰 폭증 방지)
        min_score: 후보로 인정할 최소 점수

    Returns:
        [{kind, name, resource_type, aliases, unsupported, score}] 점수 내림차순
    """
    if not term or not term.strip():
        return []
    scored: list[dict] = []
    for entry in catalog_entries(model):
        score = _entry_score(term.strip(), entry)
        if score >= min_score:
            scored.append({**entry, "score": round(score, 3)})
    scored.sort(key=lambda e: (-e["score"], str(e["name"])))
    return scored[:limit]


def check_smq_coverage(
    smq: Any,
    model: dict,
    *,
    value_index: Optional[dict[str, list[str]]] = None,
) -> dict:
    """누적된 SMQ가 결정적으로 컴파일 가능한지 판정한다(기존 커버리지 판정 재사용).

    Args:
        smq: SMQ dict 또는 SMQ 인스턴스
        model: 시맨틱 모델 dict
        value_index: 값 인덱스(있으면 필터 리터럴을 실측 검증)

    Returns:
        {"covered": bool, "reason": str} — covered=False면 reason이 폴백 사유
    """
    try:
        parsed = smq if isinstance(smq, SMQ) else SMQ.from_dict(smq or {})
    except Exception as e:  # noqa: BLE001 — 스키마 불일치는 커버리지 밖 사유로 노출
        return {"covered": False, "reason": f"SMQ 형식 오류: {e}"}
    result = check_coverage(parsed, model, value_index=value_index)
    return {"covered": result.covered, "reason": result.reason}
