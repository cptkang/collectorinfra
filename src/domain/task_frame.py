"""복합 질의 task 프레임 — 분해 결과의 결정적 검증과 task 질의 조립 (plans/111 C-1·C-2).

분해 LLM은 task마다 자유문 ``sub_query`` 대신 **원문 조각**(``spans``)만 고른다. 이 모듈은
그 조각으로 task 질의를 만들고, 계획이 원문을 벗어났는지 판정한다(plans/107 G-1.5 "자유 서술
재작성 신설 금지"의 복합 판).

- ``verify_task_frames`` — ①조각이 원문(또는 직전 맥락)의 부분 문자열인가(발명 금지)
  ②원문의 숫자를 task 조각 합집합이 덮는가(합집합 커버) ③조각에 SQL 문장이 없는가
- ``render_task_query`` — 조각을 원문 순서로 이어 task 질의를 만든다(원문에 없는 단어 0 —
  선행 결과를 받는 task만 고정 접두 한 줄이 붙는다)

**순수**하다 — LLM 0회 · ``src.domain.*`` 외 내부 import 0(``arch_check`` domain 규칙).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: 선행 결과를 받는 task의 질의 접두 — 시스템 고정 문구(LLM이 쓰지 않는다).
PRIOR_PREFIX = "선행 결과 대상 중 "

_WS = re.compile(r"\s+")
_NUM = re.compile(r"\d+(?:\.\d+)?")
# SQL 문장 형태(동사 + 절 키워드)만 잡는다 — 원문에 "select"라는 단어 하나가 있는 것은
# 문장이 아니다.
_SQL_STATEMENT = re.compile(
    r"\b(select|update|insert|delete|drop|alter|create|truncate|merge)\b[\s\S]*?"
    r"\b(from|set|into|table|where|values)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TaskFrameVerdict:
    """계획 검증 결과. ``violations``가 비어 있으면 통과다."""

    ok: bool
    violations: tuple[str, ...] = ()


def _squash(text: str) -> str:
    """공백 무시·소문자 비교용 정규형."""
    return _WS.sub("", text or "").lower()


def contains_sql_statement(text: str) -> bool:
    """SQL 문장 형태(동사 + 절 키워드)가 들어 있는가.

    분해 조각 검증과 3단 재계획 task 질의의 SQL 금지(plans/111 D-3)가 같은 판정을 쓴다.
    """
    return bool(_SQL_STATEMENT.search(text or ""))


def task_spans(task: Mapping[str, Any]) -> list[str]:
    """task의 원문 조각 목록(빈 조각 제거). 없거나 형식이 틀리면 빈 목록."""
    raw = task.get("spans")
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(s).strip() for s in raw if str(s).strip()]


def verify_task_frames(
    tasks: Sequence[Mapping[str, Any]], original: str, context: str = "",
) -> TaskFrameVerdict:
    """분해 계획이 원문을 벗어났는지 결정적으로 판정한다.

    Args:
        tasks: 분해 task 목록(각 task에 ``spans``)
        original: 사용자 원문
        context: 직전 대화 맥락 텍스트 — 후속 턴의 지시어 해소 조각은 여기서 와도 된다

    Returns:
        ``TaskFrameVerdict`` — 위반 사유를 사람이 읽는 문장으로 싣는다
    """
    violations: list[str] = []
    src, ctx = _squash(original), _squash(context)
    covered: set[str] = set()
    for task in tasks:
        tid = str(task.get("task_id") or "?")
        spans = task_spans(task)
        if not spans:
            violations.append(f"{tid}: 원문 조각(spans) 없음")
            continue
        for span in spans:
            squashed = _squash(span)
            if squashed not in src and not (ctx and squashed in ctx):
                violations.append(f"{tid}: 원문·직전 맥락에 없는 조각 {span!r}")
            if contains_sql_statement(span):
                violations.append(f"{tid}: SQL 문장 조각 {span!r}")
            covered.update(_NUM.findall(span))
    missing = [n for n in dict.fromkeys(_NUM.findall(original)) if n not in covered]
    if missing:
        violations.append(f"원문 숫자 {missing}를 덮는 task 조각 없음")
    return TaskFrameVerdict(ok=not violations, violations=tuple(violations))


def render_task_query(task: Mapping[str, Any], original: str) -> str:
    """조각을 원문에 나타난 순서로 이어 task 질의를 만든다.

    원문에서 위치를 찾지 못한 조각(직전 맥락에서 온 지시어 해소값)은 앞에 둔다 — 대상 식별자가
    조건보다 먼저 오게 하려는 것이다. 선행 결과를 받는 task(``input_from``)만 고정 접두가 붙는다.
    """
    spans = task_spans(task)
    squashed_original = _squash(original)

    def _position(span: str) -> int:
        idx = squashed_original.find(_squash(span))
        return idx if idx >= 0 else -1

    ordered = sorted(spans, key=_position)
    text = " ".join(dict.fromkeys(ordered))
    if task.get("input_from"):
        text = PRIOR_PREFIX + text
    return text
