"""IntentFrame 렌더러 — 한 프레임에서 병기 블록·정규 질의를 찍는다(plans/107 W2 · §4.5).

LLM을 쓰지 않는다(템플릿). 라벨·순서·출처 표기 문구의 정본은 이 모듈 하나다 — 해석 표시·실행
브리프·재작성문이 서로 사본이 되지 않게 같은 프레임에서 파생한다(D-053).

입력은 ``IntentFrame.slot_dict()``의 plain 사전이다 — prompts 계층은 utils만 참조할 수 있어
(``scripts/arch_check.py``) 도메인 타입을 받지 않는다. 출처 키 문자열은 도메인
``src.domain.intent_frame.SLOT_SOURCES``와 같아야 하며 테스트가 그 일치를 고정한다.

- ``render_canonical_block`` — LLM 프롬프트 병기용 구조 블록(원문을 함께 싣는다)
- ``render_canonical_query`` — 완결 자연문 1문장. ``target_db``를 주면 위치 라벨을 뺀다
  (대상 DB 고정 뒤 SQL 생성 입력 — R1 라우터 지시와 같은 방향)

**라우터 입력에는 쓰지 않는다**(D-004 — 라우팅은 원문 LLM 전용).
"""

from __future__ import annotations

from typing import Any

RENDERER_VERSION = "block_v1"

BLOCK_HEADER = "[확정된 해석]"
RAW_HEADER = "[사용자 원문]"
# 대상 줄은 라우팅 결과를 알려 주는 정보다 — SQL 조건으로 옮기면 위치어가 WHERE로 샌다(§4.5).
TARGET_RULE = "※ '대상' 줄은 이미 확정된 조회 대상이다. SQL의 WHERE 조건으로 쓰지 않는다."

#: 슬롯 라벨과 표시 순서(고정 — 프롬프트 바이트가 곧 회귀 판정 대상이다).
SLOT_LABELS: tuple[tuple[str, str], ...] = (
    ("targets.zone", "대상 영역"),
    ("targets.db_ids", "대상 DB"),
    ("targets.hosts", "대상 서버"),
    ("metrics", "조회 항목"),   # 파서 query_targets — 지표만이 아니라 조회 대상 항목 전체
    ("time_range", "기간"),
    ("aggregation", "집계"),
    ("limit", "건수"),
    ("output", "산출"),
)
#: 위치 성격의 라벨 — 대상 DB가 고정된 뒤의 정규 질의에서는 뺀다.
LOCATION_SLOTS: frozenset[str] = frozenset({"targets.zone", "targets.db_ids"})

#: 발화 밖 출처의 표시 문구(발화 출처는 표기하지 않는다). 키는 도메인 출처 문자열과 같다.
SOURCE_NOTES: dict[str, str] = {
    "context_inherited": "직전 대화에서 이어받음",
    "clarification_answer": "사용자 선택",
    "default": "기본값 — 사용자 미지정",
    "registry": "등록 정보에서 파생",
}


def _format_value(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in sorted(value.items()))
    return str(value)


def _line(label: str, slot: dict[str, Any]) -> str:
    note = SOURCE_NOTES.get(str(slot.get("source")))
    suffix = f" (출처: {note})" if note else ""
    return f"- {label}: {_format_value(slot.get('value'))}{suffix}"


def render_canonical_block(
    slots: dict[str, dict[str, Any]], *, intent: str = "", raw_query: str,
) -> str:
    """병기(augment)용 구조 블록. 원문은 버리지 않고 블록 아래에 그대로 싣는다(P-5).

    Args:
        slots: ``IntentFrame.slot_dict()`` — ``{슬롯: {"value", "source"}}``
        intent: 의도 라벨(없으면 줄을 싣지 않는다)
        raw_query: 사용자 원문 줄에 실을 텍스트

    Returns:
        블록 문자열(슬롯·의도가 하나도 없으면 원문만)
    """
    lines = [f"- 의도: {intent}"] if intent else []
    lines += [_line(label, slots[name]) for name, label in SLOT_LABELS if name in slots]
    if not lines:
        return f"{RAW_HEADER}\n{raw_query}"
    parts = [BLOCK_HEADER, *lines]
    if any(name in slots for name in LOCATION_SLOTS):
        parts.append(TARGET_RULE)
    parts += [RAW_HEADER, raw_query]
    return "\n".join(parts)


def render_canonical_query(
    slots: dict[str, dict[str, Any]], *, original_query: str, target_db: str | None = None,
) -> str:
    """완결 자연문 1문장(대체 모드·재작성 대조용). ``target_db``를 주면 위치 라벨을 뺀다.

    Args:
        slots: ``IntentFrame.slot_dict()``
        original_query: 슬롯이 없을 때 돌려줄 원문
        target_db: 대상 DB가 이미 고정됐으면 그 식별자(위치 누출 방지 — §4.5 변형 2종)

    Returns:
        "라벨 값, 라벨 값 … 조회" 형태의 한 문장. 슬롯이 없으면 원문
    """
    pieces = [
        f"{label} {_format_value(slots[name].get('value'))}"
        for name, label in SLOT_LABELS
        if name in slots and not (target_db and name in LOCATION_SLOTS)
    ]
    if not pieces:
        return original_query
    return ", ".join(pieces) + " 조회"
