"""구조 프로필 필드 단위 병합 · 필드별 diff (plans/104 G-4 · Wave 2 계약 §2).

**무엇을 하나.** 관리자 구조 분석 초안(LLM이 만든 `patterns`·`query_guide`·코드값)을 현행 구조
프로필(`config/db_profiles/{db_id}.yaml`)에 **필드 단위로** 병합한다. LLM이 만드는 필드만 갱신하고
사람이 직접 쓴 필드(허용 테이블·질의 예시·알려진 속성·조인 금지 컬럼 등)는 보존한다. 승인 화면이
보여 줄 필드별 diff도 만든다.

**병합 규칙.**
1. 결과는 항상 `source: manual`이다 — 소비처(수동 프로필 로더)가 이 값만 인정하므로, 관리자 승인본도
   "사람이 확정한 구조 정본"으로 같은 경로·형식을 쓴다. `local_sandbox=True`면
   `environment: local_sandbox`를 붙이고, 아니면 `environment` 키를 없앤다.
2. base 최상위 키 중 `LLM_TOP_LEVEL_KEYS`·`FILL_IF_ABSENT_KEYS`·`METADATA_KEYS`가 아닌 것은 그대로
   보존한다(목록 밖 신규 수동 키도 자동 보존).
3. `query_guide`: base 값이 비어 있지 않으면 보존, 비었으면 초안 값.
4. `patterns`: `pattern_identity`로 base·초안 패턴을 짝짓는다.
   - 짝 있음: 스칼라 LLM 키는 초안 값이 비어 있지 않을 때만 갱신 · 리스트 LLM 키(`value_joins`)는
     식별 키 합집합(충돌 시 base 항목 유지) · 그 밖의 키는 보존.
   - base에만 있음: 보존(부분 범위 분석이 수동 패턴을 지우지 않는다) · 초안에만 있음: 추가.
5. `code_values`: 초안에 있으면 base와 컬럼 키 단위로 병합한다(초안 값으로 갱신).
6. 초안의 그 밖의 키(`samples` 등)는 프로필에 쓰지 않는다 — 실데이터 행이 git 추적 파일로 가는 것을
   막는다.

계층: domain — 순수 함수 · I/O·LLM 0 · 표준 라이브러리만 · 스키마 리터럴 0.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

from src.domain.schema_snapshot import bare_name

# 초안이 갱신하는 최상위 키
LLM_TOP_LEVEL_KEYS: tuple[str, ...] = ("patterns", "code_values")
# 기존 값이 비었을 때만 초안 값으로 채우는 최상위 키(수동 값 보존)
FILL_IF_ABSENT_KEYS: tuple[str, ...] = ("query_guide",)
# 병합이 직접 정하는 메타 키(base 값은 쓰지 않는다)
METADATA_KEYS: tuple[str, ...] = ("source", "environment")

MANUAL_SOURCE = "manual"
LOCAL_SANDBOX_ENVIRONMENT = "local_sandbox"

# 패턴 유형별로 LLM 구조 분석이 만드는 스칼라 키
LLM_PATTERN_SCALAR_KEYS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "eav": (
        "entity_table", "config_table", "join_condition", "attribute_column",
        "value_column", "lob_value_column", "lob_flag_column",
    ),
    "hierarchy": ("table", "id_column", "parent_column", "type_column", "name_column"),
})

# 패턴 유형별로 LLM 구조 분석이 만드는 리스트 키 → 항목 식별 키
LLM_PATTERN_LIST_KEYS: Mapping[str, Mapping[str, tuple[str, ...]]] = MappingProxyType({
    "eav": MappingProxyType({"value_joins": ("eav_attribute", "entity_column")}),
})


def _canonical_json(value: Any) -> str:
    """키 정렬 JSON 직렬화(식별·비교용)."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _is_blank(value: Any) -> bool:
    """None · 공백 문자열 · 빈 컬렉션이면 True."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def _as_list(value: Any) -> list[Any]:
    """리스트면 그대로, 아니면 빈 리스트."""
    return value if isinstance(value, list) else []


def pattern_identity(pattern: Mapping[str, Any]) -> tuple[str, ...]:
    """base·초안 패턴을 짝짓는 식별 튜플.

    - eav → ``("eav", entity_table, config_table)`` (스키마 접두 제거 · casefold)
    - hierarchy → ``("hierarchy", table)`` (스키마 접두 제거 · casefold)
    - 그 외 → ``("other", 정규 JSON)`` — 내용이 같을 때만 같은 패턴으로 본다

    Args:
        pattern: 구조 프로필의 패턴 항목

    Returns:
        식별 튜플
    """
    kind = pattern.get("type")
    if kind == "eav":
        return (
            "eav",
            bare_name(str(pattern.get("entity_table") or "")),
            bare_name(str(pattern.get("config_table") or "")),
        )
    if kind == "hierarchy":
        return ("hierarchy", bare_name(str(pattern.get("table") or "")))
    return ("other", _canonical_json(dict(pattern)))


def _identity(item: Any) -> tuple[str, ...]:
    """패턴 목록 항목의 식별 튜플(dict가 아닌 항목은 정규 JSON)."""
    if isinstance(item, Mapping):
        return pattern_identity(item)
    return ("other", _canonical_json(item))


_Identify = Callable[[Any], tuple[str, ...]]


def _pair_items(
    base: list[Any], draft: list[Any], identify: _Identify
) -> list[tuple[tuple[str, ...], int, Any, Any]]:
    """base·초안 항목을 식별 튜플로 짝짓는다.

    같은 식별 튜플이 여러 번 나오면 k번째끼리 짝짓는다. 순서는 base 순서 → 초안에만 있는 항목의
    초안 순서다.

    Returns:
        ``(식별 튜플, 출현 순번, base 항목|None, 초안 항목|None)`` 목록
    """
    draft_buckets: dict[tuple[str, ...], list[Any]] = {}
    for item in draft:
        draft_buckets.setdefault(identify(item), []).append(item)

    base_counts: dict[tuple[str, ...], int] = {}
    pairs: list[tuple[tuple[str, ...], int, Any, Any]] = []
    for item in base:
        ident = identify(item)
        occ = base_counts.get(ident, 0)
        base_counts[ident] = occ + 1
        bucket = draft_buckets.get(ident, [])
        pairs.append((ident, occ, item, bucket[occ] if occ < len(bucket) else None))

    draft_counts: dict[tuple[str, ...], int] = {}
    for item in draft:
        ident = identify(item)
        occ = draft_counts.get(ident, 0)
        draft_counts[ident] = occ + 1
        if occ >= base_counts.get(ident, 0):
            pairs.append((ident, occ, None, item))
    return pairs


def _list_item_identifier(keys: tuple[str, ...]) -> _Identify:
    """리스트 LLM 키 항목의 식별 함수(식별 키 값 원형 비교 · dict가 아닌 항목은 정규 JSON)."""

    def identify(item: Any) -> tuple[str, ...]:
        if isinstance(item, Mapping):
            return tuple("" if item.get(k) is None else str(item.get(k)) for k in keys)
        return ("__raw__", _canonical_json(item))

    return identify


def _merge_list_key(base_items: Any, draft_items: Any, keys: tuple[str, ...]) -> list[Any]:
    """식별 키 합집합 — base 항목은 전부 유지하고 초안에만 있는 항목을 뒤에 붙인다."""
    identify = _list_item_identifier(keys)
    merged = [copy.deepcopy(item) for item in _as_list(base_items)]
    seen = {identify(item) for item in merged}
    for item in _as_list(draft_items):
        ident = identify(item)
        if ident in seen:
            continue
        seen.add(ident)
        merged.append(copy.deepcopy(item))
    return merged


def _merge_pattern(base: Mapping[str, Any], draft: Mapping[str, Any]) -> dict[str, Any]:
    """짝지어진 패턴 1쌍을 병합한다(LLM 키만 갱신 · 그 밖의 키 보존)."""
    kind = str(base.get("type") or "")
    merged: dict[str, Any] = copy.deepcopy(dict(base))
    for key in LLM_PATTERN_SCALAR_KEYS.get(kind, ()):
        value = draft.get(key)
        if not _is_blank(value):
            merged[key] = copy.deepcopy(value)
    for key, ident_keys in LLM_PATTERN_LIST_KEYS.get(kind, {}).items():
        if _is_blank(draft.get(key)):
            continue
        merged[key] = _merge_list_key(base.get(key), draft.get(key), ident_keys)
    return merged


def _merge_patterns(base_patterns: Any, draft_patterns: Any) -> list[Any]:
    """패턴 목록을 병합한다(base에만 있으면 보존 · 초안에만 있으면 추가)."""
    result: list[Any] = []
    for _ident, _occ, base_item, draft_item in _pair_items(
        _as_list(base_patterns), _as_list(draft_patterns), _identity
    ):
        if base_item is None:
            result.append(copy.deepcopy(draft_item))
        elif draft_item is None or not (
            isinstance(base_item, Mapping) and isinstance(draft_item, Mapping)
        ):
            result.append(copy.deepcopy(base_item))
        else:
            result.append(_merge_pattern(base_item, draft_item))
    return result


def _merge_code_values(base_values: Any, draft_values: Any) -> Any:
    """코드값을 컬럼 키 단위로 병합한다(초안 값으로 갱신). 초안이 dict가 아니면 base 유지."""
    if not isinstance(draft_values, Mapping):
        return copy.deepcopy(base_values)
    merged: dict[str, Any] = (
        copy.deepcopy(dict(base_values)) if isinstance(base_values, Mapping) else {}
    )
    for key, value in draft_values.items():
        merged[key] = copy.deepcopy(value)
    return merged


def _merge_top_key(key: str, base: Mapping[str, Any], draft: Mapping[str, Any]) -> Any:
    """LLM·채움 대상 최상위 키 1개의 병합값."""
    if key == "patterns":
        return _merge_patterns(base.get(key), draft.get(key))
    if key == "code_values":
        return _merge_code_values(base.get(key), draft.get(key))
    # FILL_IF_ABSENT_KEYS
    if key in base and not _is_blank(base.get(key)):
        return copy.deepcopy(base.get(key))
    if key in draft:
        return copy.deepcopy(draft.get(key))
    return copy.deepcopy(base.get(key))


def merge_profile(
    base: Mapping[str, Any] | None,
    draft_meta: Mapping[str, Any],
    *,
    local_sandbox: bool,
) -> dict[str, Any]:
    """현행 프로필(base)에 구조 초안(draft_meta)을 필드 단위로 병합한다(모듈 docstring 규칙 1~6).

    입력은 변경하지 않는다(결과는 깊은 복사본). 키 순서: `source` → (`environment`) → base 키 순서 →
    base에 없던 LLM·채움 키(`patterns` → `code_values` → `query_guide`).

    Args:
        base: 현행 프로필 dict(없으면 None)
        draft_meta: 구조 분석 초안 meta(`patterns`·`query_guide`·`code_values`·`samples` 등)
        local_sandbox: 로컬 샌드박스 환경이면 True(`environment: local_sandbox` 표기)

    Returns:
        병합된 프로필 dict
    """
    base_map: Mapping[str, Any] = base if isinstance(base, Mapping) else {}
    merge_keys = (*LLM_TOP_LEVEL_KEYS, *FILL_IF_ABSENT_KEYS)

    result: dict[str, Any] = {"source": MANUAL_SOURCE}
    if local_sandbox:
        result["environment"] = LOCAL_SANDBOX_ENVIRONMENT

    for key, value in base_map.items():
        if key in METADATA_KEYS:
            continue
        if key in merge_keys:
            result[key] = _merge_top_key(key, base_map, draft_meta)
        else:
            result[key] = copy.deepcopy(value)

    for key in merge_keys:
        if key not in result and key in draft_meta:
            result[key] = _merge_top_key(key, base_map, draft_meta)
    return result


# --- 필드별 diff ---


def _pattern_label(identity: tuple[str, ...], occurrence: int) -> str:
    """diff 경로의 패턴 표지(``eav:<entity>/<config>`` · ``hierarchy:<table>`` · ``other:<해시>``).

    같은 표지가 여러 번 나오면 두 번째부터 ``#<순번>``을 붙인다.
    """
    if identity[0] == "eav":
        body = f"eav:{identity[1]}/{identity[2]}"
    elif identity[0] == "hierarchy":
        body = f"hierarchy:{identity[1]}"
    else:
        digest = hashlib.sha256(identity[-1].encode("utf-8")).hexdigest()[:12]
        body = f"other:{digest}"
    return body if occurrence == 0 else f"{body}#{occurrence}"


def _entry(
    path: str, before: Any, after: Any, *, present_before: bool, present_after: bool
) -> dict[str, Any]:
    """diff 항목 1건."""
    if not present_before:
        change = "added"
    elif not present_after:
        change = "removed"
    else:
        change = "changed"
    return {
        "path": path,
        "change": change,
        "before": copy.deepcopy(before) if present_before else None,
        "after": copy.deepcopy(after) if present_after else None,
    }


def _diff_list_key(
    path: str, before_items: list[Any], after_items: list[Any], keys: tuple[str, ...]
) -> list[dict[str, Any]]:
    """리스트 LLM 키의 항목 단위 diff(식별 키로 짝짓기)."""
    identify = _list_item_identifier(keys)
    out: list[dict[str, Any]] = []
    for ident, occ, b_item, a_item in _pair_items(before_items, after_items, identify):
        label = "/".join(ident) if ident[0] != "__raw__" else _pattern_label(("other", ident[1]), 0)
        if occ:
            label = f"{label}#{occ}"
        if b_item is not None and a_item is not None and b_item == a_item:
            continue
        out.append(_entry(
            f"{path}[{label}]", b_item, a_item,
            present_before=b_item is not None, present_after=a_item is not None,
        ))
    return out


def _diff_pattern(
    path: str, before: Mapping[str, Any], after: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """짝지어진 패턴 1쌍의 키 단위 diff."""
    kind = str(after.get("type") or before.get("type") or "")
    list_keys = LLM_PATTERN_LIST_KEYS.get(kind, {})
    out: list[dict[str, Any]] = []
    for key in [*before.keys(), *[k for k in after.keys() if k not in before]]:
        in_b, in_a = key in before, key in after
        b_val, a_val = before.get(key), after.get(key)
        if in_b and in_a and b_val == a_val:
            continue
        key_path = f"{path}.{key}"
        if key in list_keys and isinstance(b_val, list) and isinstance(a_val, list):
            out.extend(_diff_list_key(key_path, b_val, a_val, list_keys[key]))
        else:
            out.append(_entry(key_path, b_val, a_val, present_before=in_b, present_after=in_a))
    return out


def _diff_patterns(
    before_patterns: list[Any], after_patterns: list[Any]
) -> list[dict[str, Any]]:
    """패턴 목록 diff — 패턴 추가·삭제는 패턴 단위, 짝지어진 패턴은 키 단위."""
    out: list[dict[str, Any]] = []
    for ident, occ, b_item, a_item in _pair_items(before_patterns, after_patterns, _identity):
        path = f"patterns[{_pattern_label(ident, occ)}]"
        if b_item is None or a_item is None:
            out.append(_entry(
                path, b_item, a_item,
                present_before=b_item is not None, present_after=a_item is not None,
            ))
        elif b_item != a_item:
            if isinstance(b_item, Mapping) and isinstance(a_item, Mapping):
                out.extend(_diff_pattern(path, b_item, a_item))
            else:
                out.append(_entry(path, b_item, a_item, present_before=True, present_after=True))
    return out


def _diff_mapping_by_key(
    path: str, before: Mapping[str, Any], after: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """dict 값의 키 단위 diff(``path[키]``)."""
    out: list[dict[str, Any]] = []
    for key in [*before.keys(), *[k for k in after.keys() if k not in before]]:
        in_b, in_a = key in before, key in after
        if in_b and in_a and before[key] == after[key]:
            continue
        out.append(_entry(
            f"{path}[{key}]", before.get(key), after.get(key),
            present_before=in_b, present_after=in_a,
        ))
    return out


def profile_field_diff(
    before: Mapping[str, Any] | None, after: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """두 프로필의 필드별 차이를 결정적 순서(경로 사전순)로 돌려준다.

    경로 표기:
    - 최상위 키: ``query_guide`` · ``allowed_tables`` 등(값 전체 비교)
    - 패턴: ``patterns[eav:<entity>/<config>]``(추가·삭제) · ``patterns[...].<키>``(변경)
    - 리스트 LLM 키 항목: ``patterns[...].value_joins[<식별 키 값>/...]``
    - 코드값: ``code_values[<컬럼 키>]``

    `patterns`·`code_values`는 양쪽 값이 각각 리스트·dict일 때만 항목 단위로 내려가고, 아니면 값
    전체를 비교한다.

    Args:
        before: 적용 전 프로필(없으면 None — 모든 키가 추가)
        after: 적용 후 프로필

    Returns:
        ``[{"path", "change": "added"|"removed"|"changed", "before", "after"}]``
    """
    before_map: Mapping[str, Any] = before if isinstance(before, Mapping) else {}
    out: list[dict[str, Any]] = []
    keys = [*before_map.keys(), *[k for k in after.keys() if k not in before_map]]
    for key in keys:
        in_b, in_a = key in before_map, key in after
        b_val, a_val = before_map.get(key), after.get(key)
        if in_b and in_a and b_val == a_val:
            continue
        if key == "patterns" and isinstance(b_val, list) and isinstance(a_val, list):
            out.extend(_diff_patterns(b_val, a_val))
        elif key == "code_values" and isinstance(b_val, Mapping) and isinstance(a_val, Mapping):
            out.extend(_diff_mapping_by_key(key, b_val, a_val))
        else:
            out.append(_entry(key, b_val, a_val, present_before=in_b, present_after=in_a))
    return sorted(out, key=lambda e: e["path"])
