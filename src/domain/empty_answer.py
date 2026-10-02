"""0건 응답 원인 진단 — 조건 퍼널 · MFS/XSS 판정 (Plan 82 Wave 8 · D-176 후속1).

**무엇을 하나.** *"조건에 해당하는 데이터가 없습니다"* 하나로 끝나던 0건 응답을
**어느 조건에서 끊겼는지**로 바꾼다. 단계별 잔존 건수를 받아 XSS(결과가 남는 최대 조건
부분집합)와 MFS(이미 0건이 되는 최소 조건 부분집합)를 판정하고 사람이 읽을 표로 낸다.

**차용한 것이지 발명한 것이 아니다** — 협조적 응답(cooperative answering)은 30년 된
영역이다(Godfrey, IJCIS 6(2) 1997 `EMPTY-MFS-01` · Fokou et al., KAIS 50(1) 2016
`EMPTY-DIAG-01`). 조건이 N개면 후속 질의 N회로 MFS/XSS를 찾는 단순 순차 알고리즘이
존재하고, 모든 MFS 열거는 NP-hard지만 **K를 고정하면 다항**이라 프로브 상한은 정당하다.

**여기는 판정만 한다.** SQL 수술·프로브 실행은 `src/nodes/condition_probe.py` 소관이고,
이 모듈은 **입력만으로 결정되며 부작용이 없다**(순수 — I/O·LLM·전역 상태 0).

**식별자 존재 확인(plans/123 S-4a).** 퍼널은 수치 비교만 탐침하므로 조건이 서버 식별자 등호뿐인
0건에는 할 말이 없었다. 그 경우 대상 DB마다 존재 확인을 1회 하고(조회는 어댑터 · 실행은
`result_organizer`), 결과(`EntityCheck`)를 여기서 문장으로 만든다 — 없는 대상에게 「임계값을
낮춰 보라」고 하지 않는다(S-4b).

계층: domain (`scripts/arch_check.py` `src.domain` 매핑).
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from src.domain.change_terms import ChangeTerms, matched_spike_term

#: 단일 그룹(존 분해 없음)일 때 쓰는 그룹 키.
SINGLE_GROUP = ""

#: 대상을 지목하는 연산자 — 부정·범위·부분 일치는 「그 서버」를 지목하지 않는다
#: (`src/orchestration/entity_locator.py` `_ANCHOR_OPS`와 같은 집합).
_ANCHOR_OPS: frozenset[str] = frozenset({"", "=", "==", "in"})


@dataclass(frozen=True)
class FunnelStage:
    """퍼널 한 단계. counts의 키는 그룹 키(단일 그룹이면 "")."""

    label: str
    counts: dict[str, Optional[int]]   # None = 미측정(프로브 실패·상한 절단)
    source: str                        # "probe" | "group_results" | "task_results"


@dataclass(frozen=True)
class Breakpoint:
    """그룹 하나의 끊긴 지점. 인덱스는 `stages`의 위치다(미판정이면 None)."""

    group: str
    xss_index: Optional[int]   # 결과가 남은 마지막 단계 (maXimal Succeeding Subquery)
    mfs_index: Optional[int]   # 처음 0이 된 단계 (Minimal Failing Subquery)


@dataclass(frozen=True)
class EntityCheck:
    """식별자 존재 확인 결과(plans/123 S-4a). 대상 DB **전부**를 확인했을 때만 만든다.

    `groups`는 확인한 DB 표시명(단일 DB면 1개), `found`는 식별자 → 등록이 확인된 표시명이다.
    확인하지 못한 DB가 하나라도 있으면 만들지 않는다 — 「확인하지 못함」을 「없음」과 합치면
    있는 서버를 없다고 말하게 된다.
    """

    values: tuple[str, ...]
    groups: tuple[str, ...]
    found: dict[str, tuple[str, ...]]

    @property
    def all_missing(self) -> bool:
        """확인한 어느 DB에도 없는 식별자뿐인가 — 그러면 0건의 원인이 확정된다."""
        return bool(self.values) and not any(self.found.get(v) for v in self.values)


@dataclass(frozen=True)
class EmptyDiagnosis:
    """0건 진단 결과 전체. 렌더 전 단계의 구조화 산출물이다."""

    stages: tuple[FunnelStage, ...]
    breakpoints: tuple[Breakpoint, ...]
    unexpressed: tuple[str, ...]
    notes: tuple[str, ...]
    regenerable: bool
    #: 식별자 존재 확인에서 말할 것이 있을 때만(없거나 일부 DB에만 있는 식별자) 싣는다(S-4a).
    entity: EntityCheck | None = None
    #: 파서 조건이 식별자 등호뿐이다 — 완화 제안에 임계값·기간을 들지 않는다(S-4b).
    identifier_only: bool = False


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def identifier_only_values(
    filter_conditions: Sequence[Any] | None,
    *,
    identity_fields: Collection[str],
    is_placeholder: Callable[[Any], bool],
) -> tuple[str, ...]:
    """파서 조건이 **식별자 등호뿐**이면 그 값들을, 아니면 빈 튜플을 돌려준다(S-4a · S-4b).

    식별자 등호 = 연산자가 등호·IN이고 ①필드가 서버 식별 필드(`identity_fields`)이거나 ②값이
    IP 구문이다(필드명 무관 — `entity_locator`의 값 기반 판정과 같다). 조건이 하나라도 이 모양이
    아니면(수치 비교 · OS · 상태 · 자연어 문자열) 0건 원인을 식별자로 좁힐 수 없다.

    Args:
        filter_conditions: `parsed_requirements["filter_conditions"]`
        identity_fields: 서버 식별 필드명(소문자) — 호출부가 식별 필드 어휘 정본을 준다
        is_placeholder: 값이 실제 이름이 아니라 지시어·플레이스홀더인지 판정하는 함수
    """
    values: list[str] = []
    for cond in filter_conditions or []:
        if not isinstance(cond, Mapping):
            return ()
        if str(cond.get("op") or "").strip().lower() not in _ANCHOR_OPS:
            return ()
        identity_field = str(cond.get("field", "")).strip().lower() in identity_fields
        raw = cond.get("value")
        for item in list(raw) if isinstance(raw, (list, tuple, set)) else [raw]:
            if isinstance(item, bool) or is_placeholder(item):
                return ()
            text = str(item).strip()
            if not (identity_field or _is_ip(text)):
                return ()
            if text not in values:
                values.append(text)
    return tuple(values)


def detect_unexpressed_conditions(
    user_query: str | None,
    filter_conditions: Sequence[str] | None,
    terms: Optional[ChangeTerms] = None,
) -> list[str]:
    """원문에 있는데 조회 조건으로 **표현되지 못한** 축을 찾는다(G-4).

    MFS/XSS는 *SQL로 표현된* 조건들 사이에서 끊긴 지점을 찾으므로, *"갑자기 상승"* 처럼
    애초에 표현되지 못한 조건은 그 틀로 진단되지 않는다 — 그런데 사용자에게는 이쪽이
    더 중요하다. 못 하는 것보다 **말하지 않는 것이 나쁘다**: 침묵하면 사용자가 틀린 답을
    믿지만, 말하면 다른 방법을 찾는다.

    판정은 결정적이다 — 원문에 변화 어휘가 있는데 `filter_conditions` 어디에도 그 어휘가
    없으면 미반영으로 본다. LLM에 묻지 않는다(D-035).

    Args:
        user_query: 사용자 원문 질의
        filter_conditions: `parsed_requirements["filter_conditions"]`(자연어 서술)
        terms: 선언 규칙. 미지정 시 선언 파일에서 읽는다.

    Returns:
        사용자에게 보일 미반영 사유 문구 목록(없으면 빈 리스트).
    """
    term = matched_spike_term(user_query, terms)
    if not term:
        return []

    for cond in filter_conditions or []:
        if matched_spike_term(str(cond), terms):
            return []

    return [
        f'"{term}"(급증) 조건은 조회 조건으로 표현되지 않아 **반영되지 않았습니다** — '
        "위 결과는 급증 여부를 따지지 않은 것입니다."
    ]


def _group_keys(stages: Sequence[FunnelStage]) -> list[str]:
    """등장 순서를 유지한 그룹 키 목록(단계마다 키가 달라도 합집합을 만든다)."""
    keys: list[str] = []
    for stage in stages:
        for key in stage.counts:
            if key not in keys:
                keys.append(key)
    return keys


def _breakpoint(stages: Sequence[FunnelStage], group: str) -> Breakpoint:
    xss: Optional[int] = None
    mfs: Optional[int] = None
    for idx, stage in enumerate(stages):
        value = stage.counts.get(group)
        if value is None:
            continue
        if value > 0:
            xss = idx
        elif mfs is None:
            mfs = idx
    return Breakpoint(group=group, xss_index=xss, mfs_index=mfs)


def build_diagnosis(
    *,
    parsed: dict,
    stage_counts: Sequence[FunnelStage],
    unexpressed: Sequence[str],
    notes: Sequence[str],
    entity: EntityCheck | None = None,
    identifier_only: bool = False,
) -> EmptyDiagnosis:
    """퍼널 단계에서 XSS/MFS를 판정한다. 입력만으로 결정되며 부작용이 없다.

    `regenerable`은 0건 재생성 루프를 끊을지의 판정이다(G-5):

    - P0(조건 0개)마저 0이면 데이터 부재가 아니라 **스코프·SQL 오류 신호**다
      (기간·존·테이블이 틀렸다) → 재생성이 정당하다.
    - 어느 그룹이든 P0>0이면 SQL은 정상 동작했고 데이터가 없을 뿐이다 → 재생성은
      토큰만 쓴다.
    - P0을 **측정하지 못했으면**(프로브 실패·미실행) 판정하지 않고 현행 동작을
      유지한다(재생성 허용) — 진단 실패가 기존 경로를 바꾸면 안 된다.
    - 식별자가 확인한 어느 DB에도 없으면(S-4a) 어떤 SQL을 다시 만들어도 행이 나오지 않는다
      → 재생성하지 않는다.

    Args:
        parsed: 파싱된 요구사항(현재는 렌더 문맥용 — 판정에는 쓰지 않는다)
        stage_counts: 0단계(P0)부터 순서대로 쌓인 퍼널 단계
        unexpressed: `detect_unexpressed_conditions` 산출물
        notes: 절단·실패 사유 등 사용자에게 노출할 부가 문구
        entity: 식별자 존재 확인 결과(S-4a — 말할 것이 있을 때만)
        identifier_only: 파서 조건이 식별자 등호뿐인가(S-4b 완화 제안 문구)
    """
    stages = tuple(stage_counts)
    breakpoints = tuple(_breakpoint(stages, key) for key in _group_keys(stages))

    measured_p0 = (
        [v for v in stages[0].counts.values() if v is not None] if stages else []
    )
    regenerable = True if not measured_p0 else all(v == 0 for v in measured_p0)

    extra_notes = list(notes)
    if measured_p0 and regenerable:
        extra_notes.append(
            "대상 자체가 0건입니다 — 데이터 부재가 아니라 조회 범위(기간·존·테이블) 문제일 수 있습니다."
        )
    if entity is not None and entity.all_missing:
        regenerable = False

    return EmptyDiagnosis(
        stages=stages,
        breakpoints=breakpoints,
        unexpressed=tuple(unexpressed),
        notes=tuple(extra_notes),
        regenerable=regenerable,
        entity=entity,
        identifier_only=identifier_only,
    )


def _fmt_count(value: Optional[int]) -> str:
    return "—" if value is None else f"{value:,}"


def entity_lines(check: EntityCheck | None) -> list[str]:
    """식별자 존재 확인 결과 문장(S-4a) — 본문과 고지(`entity_not_found`)가 같은 문장을 쓴다.

    - 확인한 어느 DB에도 없는 식별자 → 「'x'은(는) 등록된 서버가 아닙니다」(멀티 DB면 확인한 곳).
    - 일부 DB에만 있는 식별자 → 그 사실(어디에 있고 어디에 없는지).
    - 모두 있으면 문장이 없다 — 대상은 있는데 0건이면 종전 퍼널·문구가 말한다.
    """
    if check is None:
        return []
    lines: list[str] = []
    missing = [v for v in check.values if not check.found.get(v)]
    if missing:
        names = ", ".join(f"'{v}'" for v in missing)
        where = f"(확인한 곳: {' · '.join(check.groups)})" if len(check.groups) > 1 else ""
        lines.append(
            f"{names}은(는) 등록된 서버가 아닙니다{where} — 서버 이름(호스트명·IP)을 확인해 주세요."
        )
    for value in check.values:
        hit = check.found.get(value) or ()
        absent = [g for g in check.groups if g not in hit]
        if hit and absent:
            lines.append(
                f"'{value}'은(는) {' · '.join(hit)}에만 등록된 서버입니다 — "
                f"{' · '.join(absent)}에는 없습니다."
            )
    return lines


def render_diagnosis(diagnosis: EmptyDiagnosis) -> str:
    """진단을 사용자 응답에 덧붙일 텍스트로 렌더한다(선행 개행 없음).

    단계가 없으면 빈 문자열을 돌려준다 — 표만 있고 내용이 없는 응답을 만들지 않는다.
    식별자 존재 확인 문장(S-4a)이 있으면 맨 앞에 둔다 — 0건의 원인이 거기서 확정된다.
    """
    parts: list[str] = entity_lines(diagnosis.entity)
    groups = _group_keys(diagnosis.stages)
    bp_by_group = {bp.group: bp for bp in diagnosis.breakpoints}

    if diagnosis.stages:
        multi = groups != [SINGLE_GROUP]
        headers = ["단계", "조건"] + ([g or "전체" for g in groups] if multi else ["잔존"])
        parts.append(("\n" if parts else "") + "단계별로 확인한 결과는 다음과 같습니다.\n")
        parts.append("| " + " | ".join(headers) + " |")
        parts.append("|" + "|".join(["---"] * len(headers)) + "|")

        for idx, stage in enumerate(diagnosis.stages):
            cells = [str(idx), stage.label] + [
                _fmt_count(stage.counts.get(g)) for g in groups
            ]
            row = "| " + " | ".join(cells) + " |"
            if not multi:
                single_bp = bp_by_group.get(SINGLE_GROUP)
                if single_bp and single_bp.mfs_index == idx:
                    row += "  ← 여기서 끊겼습니다"
            parts.append(row)

        if multi:
            for group in groups:
                bp = bp_by_group.get(group)
                if bp and bp.mfs_index is not None:
                    label = diagnosis.stages[bp.mfs_index].label
                    parts.append(f"\n- {group or '전체'}: {bp.mfs_index}단계({label})에서 끊겼습니다.")

    for warning in diagnosis.unexpressed:
        parts.append(f"\n⚠ {warning}")

    for note in diagnosis.notes:
        parts.append(f"\n- {note}")

    hint = _relaxation_hint(diagnosis, bp_by_group)
    if hint:
        parts.append(f"\n{hint}")

    return "\n".join(parts).strip()


def _relaxation_hint(
    diagnosis: EmptyDiagnosis, bp_by_group: dict[str, Breakpoint]
) -> str:
    """끊긴 단계를 지목한 완화 제안. **제안까지만** 한다 — 임의 완화 후 재조회는 하지 않는다.

    자동으로 조건을 풀어 다시 조회하면 사용자가 묻지 않은 답을 주게 된다(U16 확정).
    질문의 조건이 식별자뿐이면(S-4b) 사용자는 임계값·기간을 말한 적이 없다 — 임계값을 낮추라고
    하지 않고 대상 이름과 그 단계의 조건을 확인하라고 한다.
    """
    breaks = [bp.mfs_index for bp in bp_by_group.values() if bp.mfs_index]
    if not breaks:
        return ""
    idx = min(breaks)
    label = diagnosis.stages[idx].label
    if diagnosis.identifier_only:
        return (
            f"→ {idx}단계({label})에서 끊겼습니다 — 질문에 적은 조건은 대상 이름뿐입니다. "
            "대상 이름(서버명·호스트명·IP)과 그 단계의 조건부터 확인하세요."
        )
    return f"→ {idx}단계({label})를 완화해 보세요 — 임계값을 낮추거나 기간을 넓히면 결과가 나올 수 있습니다."


def as_payload(diagnosis: EmptyDiagnosis) -> dict:
    """진단을 State에 실을 수 있는 순수 dict로 바꾼다.

    LangGraph 체크포인터가 직렬화하므로 dataclass를 그대로 실을 수 없다(D-176 계열의
    `prior_targets`가 같은 이유로 `model_dump()` 목록을 싣는다).

    S-4a 키(`entity_check` · `identifier_only`)는 값이 있을 때만 싣는다 — 종전 진단의 페이로드
    모양은 그대로다.
    """
    payload: dict[str, Any] = {
        "stages": [
            {"label": s.label, "counts": dict(s.counts), "source": s.source}
            for s in diagnosis.stages
        ],
        "unexpressed": list(diagnosis.unexpressed),
        "notes": list(diagnosis.notes),
        "regenerable": diagnosis.regenerable,
    }
    if diagnosis.entity is not None:
        payload["entity_check"] = {
            "values": list(diagnosis.entity.values),
            "groups": list(diagnosis.entity.groups),
            "found": {v: list(g) for v, g in diagnosis.entity.found.items()},
        }
    if diagnosis.identifier_only:
        payload["identifier_only"] = True
    return payload


def _entity_from_payload(raw: Any) -> EntityCheck | None:
    if not isinstance(raw, dict) or not raw.get("values"):
        return None
    found_raw = raw.get("found")
    found = found_raw if isinstance(found_raw, dict) else {}
    values = tuple(str(v) for v in raw["values"])
    return EntityCheck(
        values=values,
        groups=tuple(str(g) for g in raw.get("groups") or []),
        found={v: tuple(str(g) for g in (found.get(v) or [])) for v in values},
    )


def from_payload(payload: dict | None) -> Optional[EmptyDiagnosis]:
    """`as_payload` 산출물을 되살린다(형식이 아니면 None — 렌더를 건너뛴다).

    퍼널 단계가 없어도 식별자 존재 확인 결과(S-4a)가 있으면 되살린다.
    """
    if not isinstance(payload, dict):
        return None
    stages = tuple(
        FunnelStage(
            label=str(s.get("label", "")),
            counts=dict(s.get("counts") or {}),
            source=str(s.get("source", "probe")),
        )
        for s in payload.get("stages") or []
        if isinstance(s, dict)
    )
    entity = _entity_from_payload(payload.get("entity_check"))
    if not stages and entity is None:
        return None
    return EmptyDiagnosis(
        stages=stages,
        breakpoints=tuple(_breakpoint(stages, key) for key in _group_keys(stages)),
        unexpressed=tuple(payload.get("unexpressed") or []),
        notes=tuple(payload.get("notes") or []),
        regenerable=bool(payload.get("regenerable", True)),
        entity=entity,
        identifier_only=bool(payload.get("identifier_only")),
    )
