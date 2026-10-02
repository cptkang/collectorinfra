"""값 기반 키 브리지 — 선행 키 선택 · 대상 스코프 조립 · 매칭 판정 · 보고.

plans/102 §3.3 ②~⑤ · D-224 ③④.

**무엇을 하나.** 순차 의존(`input_from`) 후속 조회에서 선행 결과의 서버 키를 **값으로**
판정하고(`src.domain.entity_key`), 대상 DB의 키 매니페스트(프로필 `entity_keys` 블록)로
대상 컬럼·비교 방식을 **코드가 확정**해 스코프 블록을 조립한다. 후속 결과는 대상 키 컬럼
값으로 엔터티를 묶어 일치 등급을 매기고, 일치·가능한 일치만 남긴 뒤 경과 노트 1건으로 보고한다.

| 단계 | 함수 | 소비처 |
|---|---|---|
| ② 선택(게이트) | `resolve_gate_identity` | `agent_orchestrator._gate_level` |
| ② 선택(주입 행) | `identity_columns` | `subagents._extract_identity_rows` |
| ③ 조립 | `bridge_prior_rows_block` | `query_generator` · `multi_db_executor._prior_for_db` |
| ④⑤ 판정·보고 | `apply_bridge_postcheck` | `agent_orchestrator` 사후 대조 지점 |

**플래그.** `CROSS_SYSTEM_KEY_BRIDGE_ENABLED`(기본 off). off면 호출부가 이 모듈을
부르지 않는다 — 종전 컬럼명 판정(D-100)·위임 문구·사후 대조 그대로(비트 동일).

**왜 application인가.** 판정은 domain 순수 함수, 매니페스트 로드는 infrastructure,
블록 렌더·게이트는 utils에 있다. utils는 domain을 import할 수 없어(계층 방향) 여기서 값을
계산해 utils 함수에 **인자로** 넘긴다. 소비처는 노드와 orchestration이다.

**이름 힌트만 있는 선행 결과**(값 판정으로 키가 잡히지 않는 등록명 등)는 종전 경로를 그대로 탄다
(plans/102 §3.2 — 이름 힌트만이면 현행 동작 유지).

계층: application — LLM 0 · 스키마 리터럴 0(대상 테이블·컬럼은 매니페스트 데이터에서만 온다).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.domain.entity_key import (
    FAMILY_HOSTNAME,
    FAMILY_IP,
    KEY_FAMILIES,
    EntityKeyManifest,
    KeyColumn,
    ManifestKey,
    TargetEntity,
    TypedKey,
    collect_family_values,
    detect_key_columns,
    grade_matches,
    render_match_note,
    short_name,
    typed_tokens,
)
from src.routing.domain_config import get_domain_by_id
from src.schema_cache.entity_key_manifest import load_entity_key_manifest
from src.utils.prior_dependency import NOTE_BRIDGE, DependencyVerdict, extract_result_rows
from src.utils.query_gen_common import (
    _MAX_PRIOR_SCOPE_VALUES,
    _PRIOR_HOSTNAME_HINTS,
    PRIOR_SOURCE_DB_KEY,
    build_prior_rows_block,
    is_server_identity_col,
)

logger = logging.getLogger(__name__)

#: 경과 노트 사유 — 등급 판정 완료 / 결과 행에서 대상 키를 찾지 못해 판정 불가(행 유지).
REASON_BRIDGE_MATCH = "bridge_match"
REASON_BRIDGE_UNJUDGED = "bridge_unjudged"
#: 결정적 컴파일(D-099)을 건너뛰고 브리지 스코프가 실리는 일반 경로로 보냈다(권고 H).
REASON_BRIDGE_COMPILE_SKIPPED = "bridge_compile_skipped"


def compile_skip_note() -> dict[str, Any]:
    """결정적 컴파일을 건너뛴 사유 노트(권고 H) — 침묵 금지.

    종전 스코프 판정(컬럼 **이름**)이 선행 결과의 서버 키를 인정하지 못한 상황이다. 그대로
    컴파일하면 스코프 없이 조립돼 LIMIT 절단으로 대상이 빠지므로, 키 브리지가 확정한 대상
    조건을 프롬프트에 실어 보낸다.
    """
    return {
        "kind": NOTE_BRIDGE,
        "task_id": None,
        "reason": REASON_BRIDGE_COMPILE_SKIPPED,
        "detail": (
            "선행 결과의 서버 키를 컬럼 이름으로는 인식하지 못해 결정적 SQL 조립을 건너뛰고, "
            "값으로 판정한 키 브리지 조건으로 조회했습니다."
        ),
    }


def key_bridge_enabled(app_config: object) -> bool:
    """`CROSS_SYSTEM_KEY_BRIDGE_ENABLED` — `is True` 비교로 설정 대역(MagicMock) 오발동을 막는다."""
    return getattr(app_config, "cross_system_key_bridge_enabled", False) is True


# ──────────────────────────────────────────────
# ② 선택
# ──────────────────────────────────────────────

@dataclass(frozen=True)
class PriorKeySelection:
    """선행 결과에서 고른 키 — 계열·원천 컬럼·키 토큰(정규형 중복 제거 · 행 순서 · 상한 적용 전)."""

    family: str
    column: str
    keys: tuple[TypedKey, ...]

    @property
    def normalized(self) -> list[str]:
        return [k.normalized for k in self.keys]


@dataclass(frozen=True)
class BridgeContext:
    """게이트에서 확정한 키 브리지 문맥 — 사후 판정(④)이 같은 계열로 대조한다."""

    family: str
    source_col: str


@dataclass(frozen=True)
class BridgeGate:
    """게이트 입력 — `assess_prior_dependency(identity=…, no_identity_hint=…)`에 그대로 넘긴다."""

    identity: tuple[str, list[str]] | None
    no_identity_hint: str = ""
    context: BridgeContext | None = None


def _dict_rows(rows: Sequence[Any]) -> list[Mapping[str, Any]]:
    return [r for r in (rows or []) if isinstance(r, Mapping)]


def _flatten_prior_rows(prior_rows: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    out: list[Mapping[str, Any]] = []
    for rows in (prior_rows or {}).values():
        out.extend(_dict_rows(rows or []))
    return out


def _hostname_named(column: str) -> bool:
    return any(h in str(column).lower() for h in _PRIOR_HOSTNAME_HINTS)


def declared_key_columns(
    rows: Sequence[Mapping[str, Any]], default_db_ids: Sequence[str] = (),
) -> set[str]:
    """선행 행의 **출처 DB 매니페스트가 키로 선언한** 컬럼(소문자 · 권고 G).

    출처는 행의 `_source_db` 태그가 우선이고, 태그가 하나도 없으면 호출부가 아는 기본 DB
    (선행 결과의 `target_db_ids` 등)를 쓴다. 매니페스트가 없으면 빈 집합 — 그때만 값·이름
    휴리스틱이 판정한다.
    """
    db_ids: list[str] = []
    for row in _dict_rows(rows):
        tag = row.get(PRIOR_SOURCE_DB_KEY)
        if tag and str(tag) not in db_ids:
            db_ids.append(str(tag))
    if not db_ids:
        db_ids = [str(d) for d in default_db_ids if d]
    out: set[str] = set()
    for db_id in db_ids:
        manifest = load_entity_key_manifest(db_id)
        if manifest is not None:
            out.update(key.column.lower() for key in manifest.keys)
    return out


def ordered_key_columns(
    rows: Sequence[Mapping[str, Any]], *, declared: Iterable[str] = (),
) -> list[KeyColumn]:
    """값으로 판정한 키 컬럼을 우선순으로 — 도메인 순서에 **호스트명류 이름 우선**만 얹는다.

    호스트명류 우선은 종전 컬럼명 판정의 규칙이다(D-061). 출처 DB가 선언한 키 컬럼은
    그보다 앞선다(권고 G) — 선언이 없을 때만 값·이름 휴리스틱으로 내려간다.

    등록명류(`name`)와 호스트명류가 같은 값·같은 개수를 가져도 호스트명류가 먼저다(안정 정렬).
    출처 태그(`_source_db`)는 키가 아니다.
    """
    found = detect_key_columns(
        _dict_rows(rows), name_hint=is_server_identity_col, exclude=(PRIOR_SOURCE_DB_KEY,),
        declared=declared,
    )
    return sorted(
        found, key=lambda kc: (not kc.declared, 0 if _hostname_named(kc.column) else 1)
    )


def identity_columns(
    rows: Sequence[Mapping[str, Any]], default_db_ids: Sequence[str] = (),
) -> list[str]:
    """후속 task에 넘길 식별 컬럼 — 값 판정 키 컬럼 ∪ 이름 판정 식별 컬럼(행에 나온 순서).

    이름 판정 컬럼을 함께 남기는 이유: 값으로 키가 안 잡히는 등록명류는 종전 경로가 소비한다(§3.2).
    """
    dict_rows = _dict_rows(rows)
    declared = declared_key_columns(dict_rows, default_db_ids)
    value_cols = {kc.column for kc in ordered_key_columns(dict_rows, declared=declared)}
    ordered: list[str] = []
    for row in dict_rows:
        for col in row.keys():
            if col == PRIOR_SOURCE_DB_KEY or col in ordered:
                continue
            if str(col) in value_cols or is_server_identity_col(col):
                ordered.append(col)
    return ordered


def select_prior_key(
    rows: Sequence[Mapping[str, Any]],
    families: Sequence[str],
    *,
    source_db_ids: Sequence[str] = (),
) -> tuple[PriorKeySelection | None, list[KeyColumn]]:
    """계열별 최우선 키 컬럼을 찾고, 대상이 받는 계열 우선순으로 **값이 있는** 첫 계열을 고른다(P4).

    Args:
        rows: 선행 결과 행
        families: 대상 DB가 받는 키 계열(우선순)
        source_db_ids: 행에 출처 태그가 없을 때 쓸 **선행** DB — 그 매니페스트 선언 컬럼이
            값·이름 휴리스틱보다 앞선다(권고 G)

    Returns:
        (선택 또는 None, 값으로 찾은 키 컬럼 전부 — 미선택 사유 서술용)
    """
    dict_rows = _dict_rows(rows)
    found = ordered_key_columns(
        dict_rows, declared=declared_key_columns(dict_rows, source_db_ids)
    )
    best: dict[str, KeyColumn] = {}
    for column in found:
        best.setdefault(column.family, column)
    for family in families:
        chosen = best.get(family)
        if chosen is None:
            continue
        keys = collect_family_values(dict_rows, chosen.column, family)
        if keys:
            return PriorKeySelection(family=family, column=chosen.column, keys=tuple(keys)), found
    return None, found


def describe_key_mismatch(
    found: Sequence[KeyColumn], families: Sequence[str], target_label: str = "",
) -> str:
    """미선택 사유 — "값으로 찾은 키 타입 · 대상이 받는 키 타입"."""
    seen: dict[str, str] = {}
    for kc in found:
        seen.setdefault(kc.family, kc.column)
    got = " · ".join(f"{fam}(`{col}`)" for fam, col in seen.items()) or "없음"
    want = ", ".join(families) or "없음"
    target = f"대상({target_label})" if target_label else "대상"
    return f"값으로 찾은 키 타입: {got} / {target}이 받는 키 타입: {want}."


def _db_label(db_id: str) -> str:
    domain = get_domain_by_id(db_id)
    return (getattr(domain, "display_name", "") or db_id) if domain else db_id


def _task_db_ids(task: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    for item in task.get("db_ids") or []:
        db_id = item.get("db_id") if isinstance(item, Mapping) else item
        if db_id and str(db_id) not in out:
            out.append(str(db_id))
    return out


def _accepted_families(db_ids: Sequence[str]) -> tuple[tuple[str, ...], str]:
    """대상 DB 매니페스트가 받는 계열(우선순 · 합집합)과 표기. 매니페스트가 없으면 전 계열."""
    families: list[str] = []
    labels: list[str] = []
    for db_id in db_ids:
        manifest = load_entity_key_manifest(db_id)
        if manifest is None:
            continue
        labels.append(_db_label(db_id))
        for family in manifest.families():
            if family not in families:
                families.append(family)
    if not families:
        return KEY_FAMILIES, ""
    return tuple(families), ", ".join(labels)


def resolve_gate_identity(
    task: Mapping[str, Any], prior: Mapping[str, Any] | None,
) -> BridgeGate:
    """게이트(`assess_prior_dependency`)에 넘길 키를 값으로 고른다.

    대상 DB는 게이트 시점에 대개 미정이다(분류는 후속 task 실행 안에서 한다) — task에
    `db_ids`가 박혀 있으면 그 매니페스트가 받는 계열로, 아니면 전 계열로 고른다.
    선행 실패·0건 판정은 `assess_prior_dependency`가 먼저 하므로 여기서는 성공 결과 행만 본다.
    값으로 키가 안 잡히면 `identity=None` — 종전 컬럼명 판정이 그대로 돈다(§3.2).
    """
    rows: list[Mapping[str, Any]] = []
    source_db_ids: list[str] = []
    for tid in [str(t) for t in (task.get("input_from") or []) if t]:
        res = (prior or {}).get(tid)
        if isinstance(res, dict) and not res.get("error"):
            rows.extend(extract_result_rows(res))
            # 출처 태그가 없는 선행 결과의 매니페스트 판정 근거(권고 G).
            for db_id in res.get("target_db_ids") or []:
                if db_id and str(db_id) not in source_db_ids:
                    source_db_ids.append(str(db_id))
    families, label = _accepted_families(_task_db_ids(task))
    selection, found = select_prior_key(rows, families, source_db_ids=source_db_ids)
    if selection is None:
        hint = describe_key_mismatch(found, families, label)
        return BridgeGate(identity=None, no_identity_hint=hint)
    return BridgeGate(
        identity=(selection.column, selection.normalized),
        context=BridgeContext(family=selection.family, source_col=selection.column),
    )


# ──────────────────────────────────────────────
# ③ 조립
# ──────────────────────────────────────────────

def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))


def build_scope_condition(key: ManifestKey, keys: Sequence[TypedKey]) -> str:
    """대상 키 컬럼의 SQL 한정 조건 — `LOWER`·`LIKE`·`IN`만 써서 PG·DB2·MariaDB 공통이다.

    - 호스트명(casefold): `LOWER(col) IN (정규형 ∪ FQDN 단축명)` + 단일 레이블 원천값마다
      `LOWER(col) LIKE '<값>.%'`(대상 FQDN ↔ 원천 단축명 — G-2 가능한 일치 후보).
    - 호스트명(exact): `col IN (원문)`.
    - IP: `col IN (...)`. 다중값 컬럼이면 토큰 `LIKE '%<ip>%'`로 넓게 좁히고 코드가
      토큰 완전 일치로 확정한다(`apply_bridge_postcheck` — SQL이 좁히고 코드가 확인한다).
    식별자는 인용하지 않는다(엔진마다 인용 규칙이 달라 인용이 문자열 리터럴로 해석될 수 있다).
    """
    col = key.column
    if key.family == FAMILY_IP:
        values = _unique([k.normalized for k in keys])
        if key.multi_value:
            parts = [f"{col} LIKE {_sql_literal('%' + v + '%')}" for v in values]
            return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"
        return f"{col} IN ({', '.join(_sql_literal(v) for v in values)})"
    if key.compare == "exact":
        return f"{col} IN ({', '.join(_sql_literal(v) for v in _unique([k.raw for k in keys]))})"
    in_values: list[str] = []
    likes: list[str] = []
    for k in keys:
        in_values.append(k.normalized)
        if "." in k.normalized:
            in_values.append(short_name(k.normalized))
        else:
            likes.append(f"LOWER({col}) LIKE {_sql_literal(k.normalized + '.%')}")
    head = f"LOWER({col}) IN ({', '.join(_sql_literal(v) for v in _unique(in_values))})"
    return head if not likes else "(" + " OR ".join([head, *likes]) + ")"


def plan_target_scope(
    prior_rows: Mapping[str, Any] | None,
    db_id: str | None,
    *,
    max_values: int = _MAX_PRIOR_SCOPE_VALUES,
) -> dict[str, Any] | None:
    """대상 DB 한 곳에 넣을 스코프(`build_prior_rows_block(target_scope=…)` 입력)를 계산한다.

    Returns:
        - 값으로 키가 안 잡히면 None → 호출부가 종전 블록(컬럼명 판정)을 쓴다.
        - 대상 매니페스트가 받는 계열이 있으면 `condition`·`target_col`·`target_table`까지 채운다.
        - 매니페스트가 없거나 받는 계열의 값이 없으면 대상 컬럼 미확정 스코프(값만) —
          종전 위임 문구로 렌더된다. 선행 키가 있는데 스코프 블록이 비어
          **무스코프로 조회되는 것**을 막는다.
    """
    rows = _flatten_prior_rows(prior_rows)
    if not rows:
        return None
    manifest: EntityKeyManifest | None = load_entity_key_manifest(db_id) if db_id else None
    key: ManifestKey | None = None
    selection: PriorKeySelection | None = None
    if manifest is not None:
        selection, _found = select_prior_key(rows, manifest.families())
        if selection is not None:
            key = manifest.key_for(selection.family)
    if selection is None:
        selection, _found = select_prior_key(rows, KEY_FAMILIES)
    if selection is None:
        return None
    kept = selection.keys[:max_values]
    truncated = len(selection.keys) - len(kept)
    scope: dict[str, Any] = {
        "source_col": selection.column,
        "key_type": selection.family,
        "values": [k.normalized for k in kept],
        "total": len(selection.keys),
        "truncated_count": truncated,
    }
    if manifest is not None and key is not None:
        scope.update(
            target_col=key.column, target_table=manifest.table,
            condition=build_scope_condition(key, kept),
        )
    else:
        logger.info(
            "키 브리지: db=%s 매니페스트에 받는 키 없음 — 대상 컬럼 미확정 스코프(key=%s)",
            db_id, selection.family,
        )
    if truncated:
        logger.info(
            "키 브리지 스코프 절단: db=%s %d개 중 %d개(상한 %d)",
            db_id, len(selection.keys), len(kept), max_values,
        )
    return scope


def bridge_prior_rows_block(prior_rows: Mapping[str, Any] | None, db_id: str | None) -> str:
    """키 브리지 on일 때의 선행 스코프 블록. 값으로 키가 안 잡히면 종전 블록과 같다."""
    scope = plan_target_scope(prior_rows, db_id)
    if scope is None:
        return build_prior_rows_block(dict(prior_rows) if prior_rows else None)
    return build_prior_rows_block(dict(prior_rows) if prior_rows else None, target_scope=scope)


# ──────────────────────────────────────────────
# ④ 판정 · ⑤ 보고
# ──────────────────────────────────────────────

def _find_column(row: Mapping[str, Any], column: str) -> str | None:
    """결과 행에서 매니페스트 컬럼을 찾는다 — 엔진이 결과 컬럼 대소문자를 바꿀 수 있다."""
    if column in row:
        return column
    low = column.lower()
    for col in row.keys():
        if str(col).lower() == low:
            return str(col)
    return None


def _row_manifests(
    row: Mapping[str, Any], default_db_ids: Sequence[str],
) -> tuple[list[EntityKeyManifest], list[str]]:
    tagged = row.get(PRIOR_SOURCE_DB_KEY)
    db_ids = [str(tagged)] if tagged else list(default_db_ids)
    manifests: list[EntityKeyManifest] = []
    used: list[str] = []
    for db_id in db_ids:
        manifest = load_entity_key_manifest(db_id)
        if manifest is not None:
            manifests.append(manifest)
            used.append(db_id)
    return manifests, used


def _row_entity(
    row: Mapping[str, Any], manifests: Sequence[EntityKeyManifest], family: str,
) -> tuple[str, frozenset[str], str] | None:
    """결과 행 하나의 (엔터티 id, 해당 계열 키 정규형, 매니페스트 db_id). 판정 불가면 None.

    엔터티 id는 매니페스트의 호스트명 키 컬럼 값이다 — 한 서버가 IP마다 행을 갖는 대상
    (`row_multiplicity: per_ip`)도, 한 서버의 시계열 행이 여럿인 결과도 한 엔터티로 묶인다.
    호스트명 키 컬럼이 결과에 없으면 대조 컬럼의 토큰 집합을 id로 쓴다.
    """
    for manifest in manifests:
        key = manifest.key_for(family)
        col = _find_column(row, key.column) if key is not None else None
        if col is None:
            continue
        tokens = frozenset(
            tk.normalized for tk in typed_tokens(row.get(col)) if tk.family == family
        )
        if not tokens:
            return None
        entity = ""
        host_key = manifest.key_for(FAMILY_HOSTNAME)
        host_col = _find_column(row, host_key.column) if host_key is not None else None
        if host_col is not None:
            hosts = [
                tk.normalized for tk in typed_tokens(row.get(host_col))
                if tk.family == FAMILY_HOSTNAME
            ]
            entity = hosts[0] if hosts else ""
        return (entity or ",".join(sorted(tokens))), tokens, manifest.db_id
    return None


def _entity_key(entity: str, db_id: str, cross_db: frozenset[str]) -> str:
    """대조용 엔터티 id — **다른 DB(존)에 같은 이름이 또 있으면** DB로 한정한다(권고 F).

    현행 엔터티 id는 DB 무관 정규형이라 두 존의 동명 호스트가 한 엔터티로 합쳐졌다. 한정하면
    같은 키가 서로 다른 엔터티 2개에 걸려 `grade_matches`가 `ambiguous`로 판정한다 —
    모호 일치를 추측으로 메우지 않는다(D-224 ④). 같은 DB 안의 `per_ip` 다중 행 묶음은
    한정 대상이 아니라 종전대로 한 엔터티다.
    """
    return f"{db_id}::{entity}" if entity in cross_db else entity


def _cross_db_entities(grouped: Mapping[tuple[str, str], set[str]]) -> dict[str, tuple[str, ...]]:
    """(db_id, 엔터티) 묶음에서 **DB가 둘 이상인** 엔터티 → 그 DB들."""
    dbs_of: dict[str, list[str]] = {}
    for db_id, entity in grouped:
        seen = dbs_of.setdefault(entity, [])
        if db_id not in seen:
            seen.append(db_id)
    return {e: tuple(sorted(dbs)) for e, dbs in dbs_of.items() if len(dbs) > 1}


def _filter_rows(
    rows: list[Any],
    included: set[str],
    default_db_ids: Sequence[str],
    family: str,
    cross_db: frozenset[str],
) -> tuple[list[Any], int]:
    """포함 엔터티(link·possible)의 행만 남긴다. 판정 불가 행은 유지한다(오제거보다 미제거)."""
    kept: list[Any] = []
    removed = 0
    for row in rows:
        if isinstance(row, Mapping):
            manifests, _ = _row_manifests(row, default_db_ids)
            entity = _row_entity(row, manifests, family)
            if entity is not None and _entity_key(entity[0], entity[2], cross_db) not in included:
                removed += 1
                continue
        kept.append(row)
    return kept, removed


def apply_bridge_postcheck(
    context: BridgeContext | None,
    verdict: DependencyVerdict | None,
    result: Any,
    task_id: str,
) -> Any:
    """후속 결과를 선행 키와 대조해 등급을 매기고, 일치·가능한 일치만 남긴 뒤 노트 1건을 싣는다.

    - 선행 키 N = `verdict.scope_values`(상한 적용 정규형). 등급 합계는 항상 N이다(D5).
    - 대상 엔터티는 결과 행의 대상 키 컬럼으로 만든다 — 매니페스트는 행의 `_source_db`,
      없으면 결과 `target_db_ids`로 고른다.
    - **서로 다른 DB(존)에 같은 호스트명이 있으면 별개 엔터티로 본다**(권고 F) — 그 키는
      `ambiguous`가 되어 결과에 넣지 않고 사유를 노트에 싣는다.
    - `ambiguous`·스코프 밖 엔터티의 행은 제거한다. 결과 행은 있는데 대상 키 컬럼 값을
      하나도 못 찾으면 판정하지 않고 행을 그대로 둔 채 `bridge_unjudged` 노트를 남긴다(침묵 금지).
    입력은 변경하지 않는다.
    """
    if context is None or verdict is None or not verdict.ok:
        return result
    if not isinstance(result, dict) or result.get("error"):
        return result
    family = context.family
    default_db_ids = [str(d) for d in (result.get("target_db_ids") or []) if d]
    rows = extract_result_rows(result)
    grouped: dict[tuple[str, str], set[str]] = {}
    judged_dbs: list[str] = []
    for row in rows:
        manifests, _ = _row_manifests(row, default_db_ids)
        entity = _row_entity(row, manifests, family)
        if entity is None:
            continue
        grouped.setdefault((entity[2], entity[0]), set()).update(entity[1])
        if entity[2] not in judged_dbs:
            judged_dbs.append(entity[2])
    label = ", ".join(_db_label(d) for d in (judged_dbs or default_db_ids)) or "대상"
    out = dict(result)
    n_source = len(verdict.scope_values)

    if rows and not grouped:
        detail = (
            f"선행 {n_source}대 → {label}: 결과 행에서 대상 키({family}) 값을 찾지 못해 "
            f"매칭 판정을 하지 못했습니다(행 {len(rows)}건 유지)."
        )
        logger.warning("키 브리지 판정 불가 task=%s key=%s rows=%d", task_id, family, len(rows))
        out["dependency_notes"] = list(out.get("dependency_notes") or []) + [{
            "kind": NOTE_BRIDGE, "task_id": task_id,
            "reason": REASON_BRIDGE_UNJUDGED, "detail": detail,
        }]
        return out

    cross_db_map = _cross_db_entities(grouped)
    cross_db = frozenset(cross_db_map)
    targets = [
        TargetEntity(entity_id=_entity_key(eid, db_id, cross_db), keys=frozenset(keys))
        for (db_id, eid), keys in grouped.items()
    ]
    report = grade_matches(verdict.scope_values, targets, family=family)
    included = set(report.included_entities(include_possible=True))
    removed = 0
    for key in ("query_results", "rows"):
        if isinstance(out.get(key), list):
            out[key], n = _filter_rows(out[key], included, default_db_ids, family, cross_db)
            removed = max(removed, n)
    organized = out.get("organized_data")
    if isinstance(organized, dict) and isinstance(organized.get("rows"), list):
        filtered, n = _filter_rows(organized["rows"], included, default_db_ids, family, cross_db)
        out["organized_data"] = {**organized, "rows": filtered}
        removed = max(removed, n)

    detail = render_match_note(report, target_label=label, key_label=family)
    if removed:
        detail += f" · 모호·스코프 밖 행 {removed}건 제외"
    if cross_db_map:
        # 침묵 금지: "왜 빠졌는가"가 모호 건수만으로는 읽히지 않는다 — 어느 이름이 어느 DB에
        # 중복됐는지 매칭 보고에 적는다.
        detail += " · 동명 호스트가 여러 DB(존)에 있어 모호 처리: " + ", ".join(
            f"{eid}({', '.join(_db_label(d) for d in dbs)})"
            for eid, dbs in list(cross_db_map.items())[:10]
        )
    note: dict[str, Any] = {
        "kind": NOTE_BRIDGE,
        "task_id": task_id,
        "reason": REASON_BRIDGE_MATCH,
        "detail": detail,
        "counts": {
            "total": report.total,
            "link": len(report.link),
            "possible": len(report.possible),
            "non_link": len(report.non_link),
            "ambiguous": len(report.ambiguous),
        },
        "coverage": round(report.coverage, 4),
    }
    if removed:
        note["removed_rows"] = removed
    if cross_db_map:
        note["cross_db_entities"] = {eid: list(dbs) for eid, dbs in cross_db_map.items()}
    logger.info(
        "키 브리지 판정 task=%s target=%s key=%s N=%d link=%d possible=%d "
        "non_link=%d ambiguous=%d coverage=%.2f removed_rows=%d",
        task_id, label, family, report.total, len(report.link), len(report.possible),
        len(report.non_link), len(report.ambiguous), report.coverage, removed,
    )
    out["dependency_notes"] = list(out.get("dependency_notes") or []) + [note]
    return out
