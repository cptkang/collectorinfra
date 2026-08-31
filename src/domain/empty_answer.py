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

계층: domain (`scripts/arch_check.py` `src.domain` 매핑).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from src.domain.change_terms import ChangeTerms, matched_spike_term

#: 단일 그룹(존 분해 없음)일 때 쓰는 그룹 키.
SINGLE_GROUP = ""


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
class EmptyDiagnosis:
    """0건 진단 결과 전체. 렌더 전 단계의 구조화 산출물이다."""

    stages: tuple[FunnelStage, ...]
    breakpoints: tuple[Breakpoint, ...]
    unexpressed: tuple[str, ...]
    notes: tuple[str, ...]
    regenerable: bool


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
) -> EmptyDiagnosis:
    """퍼널 단계에서 XSS/MFS를 판정한다. 입력만으로 결정되며 부작용이 없다.

    `regenerable`은 0건 재생성 루프를 끊을지의 판정이다(G-5):

    - P0(조건 0개)마저 0이면 데이터 부재가 아니라 **스코프·SQL 오류 신호**다
      (기간·존·테이블이 틀렸다) → 재생성이 정당하다.
    - 어느 그룹이든 P0>0이면 SQL은 정상 동작했고 데이터가 없을 뿐이다 → 재생성은
      토큰만 쓴다.
    - P0을 **측정하지 못했으면**(프로브 실패·미실행) 판정하지 않고 현행 동작을
      유지한다(재생성 허용) — 진단 실패가 기존 경로를 바꾸면 안 된다.

    Args:
        parsed: 파싱된 요구사항(현재는 렌더 문맥용 — 판정에는 쓰지 않는다)
        stage_counts: 0단계(P0)부터 순서대로 쌓인 퍼널 단계
        unexpressed: `detect_unexpressed_conditions` 산출물
        notes: 절단·실패 사유 등 사용자에게 노출할 부가 문구
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

    return EmptyDiagnosis(
        stages=stages,
        breakpoints=breakpoints,
        unexpressed=tuple(unexpressed),
        notes=tuple(extra_notes),
        regenerable=regenerable,
    )


def _fmt_count(value: Optional[int]) -> str:
    return "—" if value is None else f"{value:,}"


def render_diagnosis(diagnosis: EmptyDiagnosis) -> str:
    """진단을 사용자 응답에 덧붙일 텍스트로 렌더한다(선행 개행 없음).

    단계가 없으면 빈 문자열을 돌려준다 — 표만 있고 내용이 없는 응답을 만들지 않는다.
    """
    parts: list[str] = []
    groups = _group_keys(diagnosis.stages)
    bp_by_group = {bp.group: bp for bp in diagnosis.breakpoints}

    if diagnosis.stages:
        multi = groups != [SINGLE_GROUP]
        headers = ["단계", "조건"] + ([g or "전체" for g in groups] if multi else ["잔존"])
        parts.append("단계별로 확인한 결과는 다음과 같습니다.\n")
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
    """
    breaks = [bp.mfs_index for bp in bp_by_group.values() if bp.mfs_index]
    if not breaks:
        return ""
    idx = min(breaks)
    label = diagnosis.stages[idx].label
    return f"→ {idx}단계({label})를 완화해 보세요 — 임계값을 낮추거나 기간을 넓히면 결과가 나올 수 있습니다."


def as_payload(diagnosis: EmptyDiagnosis) -> dict:
    """진단을 State에 실을 수 있는 순수 dict로 바꾼다.

    LangGraph 체크포인터가 직렬화하므로 dataclass를 그대로 실을 수 없다(D-176 계열의
    `prior_targets`가 같은 이유로 `model_dump()` 목록을 싣는다).
    """
    return {
        "stages": [
            {"label": s.label, "counts": dict(s.counts), "source": s.source}
            for s in diagnosis.stages
        ],
        "unexpressed": list(diagnosis.unexpressed),
        "notes": list(diagnosis.notes),
        "regenerable": diagnosis.regenerable,
    }


def from_payload(payload: dict | None) -> Optional[EmptyDiagnosis]:
    """`as_payload` 산출물을 되살린다(형식이 아니면 None — 렌더를 건너뛴다)."""
    if not isinstance(payload, dict) or not payload.get("stages"):
        return None
    stages = tuple(
        FunnelStage(
            label=str(s.get("label", "")),
            counts=dict(s.get("counts") or {}),
            source=str(s.get("source", "probe")),
        )
        for s in payload["stages"]
        if isinstance(s, dict)
    )
    if not stages:
        return None
    return EmptyDiagnosis(
        stages=stages,
        breakpoints=tuple(_breakpoint(stages, key) for key in _group_keys(stages)),
        unexpressed=tuple(payload.get("unexpressed") or []),
        notes=tuple(payload.get("notes") or []),
        regenerable=bool(payload.get("regenerable", True)),
    )
