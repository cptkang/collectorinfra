"""계층 taxonomy — 상위어 단독 질의의 모호성 처리 (Plan 67 N4 / D-133).

``src/nodes/semantic_compiler.py``에서 분리했다(Plan 69 P5-1) — SMQ·시맨틱 모델 dict·원문
질의만 보는 순수 확장이라 nodes 밖에 둔다(순환 해소).
"""

from __future__ import annotations

import re
from typing import Any, Optional

from src.semantic.coverage import (
    _dimension_index,
    _resolve_dim,
)
from src.semantic.guards import (
    GUARD_HYPERNYM_EXPAND,
    note_guard,
)
from src.semantic.ir import (
    SMQ,
    SMQMeasure,
)


def _taxonomy(model: Optional[dict]) -> dict:
    """카탈로그의 상위어 블록을 얻는다(선언·정본 생성이 없으면 빈 dict)."""
    tax = (model or {}).get("taxonomy")
    return tax if isinstance(tax, dict) else {}


def _hypernym_surfaces(term: str, node: dict) -> list[str]:
    """상위어 자신과 그 표면 변형(taxonomy aliases) 목록."""
    return [str(term)] + [str(a) for a in (node.get("aliases") or [])]


def _squash(text: str) -> str:
    """비교용 정규화 — 소문자화 + 공백 제거.

    카탈로그 별칭은 띄어쓰기 변형을 다 담지 않는다(오히려 ``alias_deny``로 걷어낸다 — "물리
    코어"는 빠져 있고 "물리코어"만 남는다). 질의의 띄어쓰기는 자유로우므로 공백을 접어야
    "물리 코어"가 하위어 명시로 인식된다(미접으면 상위어 단독으로 오판해 확장이 튄다).
    """
    return re.sub(r"\s+", "", (text or "").lower())


def _mentions_any(query_squashed: str, terms: list[str]) -> bool:
    """질의에 주어진 표면어 중 하나라도 나타나는지 본다(한국어는 어절 경계가 없어 부분 문자열)."""
    return any(squashed in query_squashed for t in terms if (squashed := _squash(t)))


def _child_dim_entries(node: dict, dim_index: tuple[dict, dict]) -> list[dict]:
    """상위어의 하위 dimension 카탈로그 항목들(수록분만)."""
    return [
        entry for name in (node.get("dimensions") or [])
        if (entry := _resolve_dim(str(name), dim_index)) is not None
    ]


def _child_measure_specs(node: dict, model: dict) -> list[dict]:
    """상위어의 하위 measure 카탈로그 정의들(수록분만, 카탈로그 순서)."""
    wanted = {
        (str(c.get("resource_type")), str(c.get("definition_name")))
        for c in (node.get("measures") or []) if isinstance(c, dict)
    }
    return [
        m for m in ((model.get("pattern_b") or {}).get("measures") or [])
        if isinstance(m, dict)
        and (str(m.get("resource_type")), str(m.get("definition_name"))) in wanted
    ]


def _child_discriminators(
    term: str, node: dict, model: dict, dim_index: tuple[dict, dict]
) -> list[str]:
    """하위 항목을 구분하는 표면어(이름·별칭) 목록 — 상위어 표면어 자신은 제외한다.

    질의에 이 중 하나라도 있으면 사용자가 어느 하위어인지 이미 지목한 것이므로 모호하지
    않다(**하위어 명시 질의 동작 불변**의 판정 근거).
    """
    surfaces = {_squash(s) for s in _hypernym_surfaces(term, node)}
    out: list[str] = []
    for entry in _child_dim_entries(node, dim_index):
        out.append(str(entry.get("name") or ""))
        out.extend(str(a) for a in (entry.get("aliases") or []))
    for spec in _child_measure_specs(node, model):
        out.append(str(spec.get("definition_name") or ""))
        out.append(str(spec.get("resource_type") or ""))
        out.extend(str(a) for a in (spec.get("aliases") or []))
    return [t for t in out if t and _squash(t) not in surfaces]


def _missing_child_dims(
    smq: SMQ, node: dict, dim_index: tuple[dict, dict]
) -> list[str]:
    """선택된 하위 dimension의 빠진 형제들을 찾는다(하나도 안 골랐으면 빈 목록).

    상위어의 하위를 **하나라도** 골랐을 때만 나머지를 채운다 — 그 선택이 곧 "LLM이 모호한
    표면어를 임의의 한 갈래로 좁혔다"는 신호다. 하나도 안 골랐으면 다른 읽기(예 상위어가
    측정치를 가리킴)이므로 손대지 않는다.
    """
    children = _child_dim_entries(node, dim_index)
    if not children:
        return []
    selected = {
        entry["name"] for d in smq.dimensions
        if (entry := _resolve_dim(str(d), dim_index)) is not None
    }
    child_names = [str(e.get("name")) for e in children]
    if not (selected & set(child_names)):
        return []
    return [
        str(e.get("name")) for e in children
        # LOB 속성은 컴파일 불가라 채우면 질의 전체가 커버리지 밖으로 밀린다.
        if str(e.get("name")) not in selected and not e.get("lob")
    ]


def _missing_child_measures(smq: SMQ, node: dict, model: dict) -> list[SMQMeasure]:
    """선택된 하위 measure의 빠진 형제들을 만든다(집계는 선택된 형제와 동일하게).

    "CPU 사용률 평균·최대"를 골랐다면 형제도 평균·최대로 채운다 — 집계가 달라지면 같은
    표에 뜻이 다른 컬럼이 섞인다.
    """
    children = _child_measure_specs(node, model)
    if not children:
        return []
    child_keys = {
        (str(m.get("resource_type")), str(m.get("definition_name"))) for m in children
    }
    selected = [
        m for m in smq.measures if (m.resource_type, m.definition_name) in child_keys
    ]
    if not selected:
        return []
    aggs: list[str] = []
    for m in selected:
        if m.agg not in aggs:
            aggs.append(m.agg)
    chosen = {(m.resource_type, m.definition_name, m.agg) for m in smq.measures}
    out: list[SMQMeasure] = []
    for spec in children:
        rt = str(spec.get("resource_type"))
        dn = str(spec.get("definition_name"))
        for agg in aggs:
            if (rt, dn, agg) in chosen:
                continue
            out.append(SMQMeasure(agg=agg, definition_name=dn, resource_type=rt))
    return out


def _expand_hypernym_ambiguity(
    smq: SMQ, user_query: str, model: Optional[dict]
) -> SMQ:
    """상위어만 언급한 질의를 하위 항목 전부로 확장한다 (N4/D-133, 옵트인).

    "사용률 보여줘"처럼 상위어 단독 언급이면 어느 자원의 지표인지 결정 불가인데, 1방 선택은
    하위 하나를 임의로 골라 **조용한 오답**이 된다. 하위 전부를 제시해 모호성을 결과에
    드러낸다(계획서 §3.3-N4의 "전체 제시"). 하위어를 명시한 질의는 판정에서 걸러져 **동작
    불변**이다.

    되묻기(covered=False + 후보 나열) 대신 전체 제시를 택한 근거: ``compile_from_nl``의
    커버리지 사유는 호출부(``query_generator``)가 소비하지 않아(폴백 진입 여부만 본다)
    사용자에게 도달하지 않고, 결정적 컴파일(구조 환각 0)을 버리고 LLM 자유생성으로
    떨어뜨리게 된다. 확장 발동은 가드 카운터로 계측해 감사·평가에 남긴다.

    확장은 기존 교정 가드보다 **먼저** 돌린다 — PHYSICALCORE 선택 교정처럼 실측 운영 관행이
    확립된 가드가 최종 중재자가 되게 한다(예 "코어" 확장 후 '물리' 신호가 없으면 가드가
    PHYSICALCORE를 다시 뺀다).
    """
    taxonomy = _taxonomy(model)
    if not taxonomy or smq.pattern not in ("A", "B"):
        return smq
    query_squashed = _squash(user_query)
    dim_index = _dimension_index((model or {}).get("pattern_a") or {})
    # dimension 확장 금지 형태 — 전역 집계는 단일 값(dimension 불가), 기간별 분해는 EAV 속성
    # dimension 불가라, 채우면 질의 전체가 커버리지 밖으로 밀린다.
    dims_allowed = not smq.global_aggregate and not smq.time_breakdown

    added_dims: list[str] = []
    added_measures: list[SMQMeasure] = []
    fired: list[str] = []
    for term, node in taxonomy.items():
        if not isinstance(node, dict):
            continue
        if not _mentions_any(query_squashed, _hypernym_surfaces(str(term), node)):
            continue
        if _mentions_any(
            query_squashed, _child_discriminators(str(term), node, model or {}, dim_index)
        ):
            continue
        new_dims = _missing_child_dims(smq, node, dim_index) if dims_allowed else []
        new_measures = _missing_child_measures(smq, node, model or {})
        if not new_dims and not new_measures:
            continue
        added_dims.extend(d for d in new_dims if d not in added_dims)
        added_measures.extend(new_measures)
        fired.append(
            f"{term} ⊃ "
            + ", ".join(new_dims + [f"{m.resource_type}/{m.agg}" for m in new_measures])
        )

    if not added_dims and not added_measures:
        return smq
    note_guard(GUARD_HYPERNYM_EXPAND, "; ".join(fired))
    update: dict[str, Any] = {}
    if added_dims:
        update["dimensions"] = list(smq.dimensions) + added_dims
    if added_measures:
        update["measures"] = list(smq.measures) + added_measures
    return smq.model_copy(update=update)
