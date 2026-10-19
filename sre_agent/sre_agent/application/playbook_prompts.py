"""플레이북 단계 소견 · 종합 프롬프트 템플릿 (plans/187 §5.4 · D-341 ③④).

LLM은 판정·판독·가설 순위를 **인용해 서술만** 한다(D-035). 판정을 바꾸지 말 것 · 입력에 없는 수치를
만들지 말 것 · 문장 수 상한 · 실시간 단면은 「현재값」 명시를 지시한다. 지시를 어겨도 결정적 가드
(`playbook_narrator`)가 후처리로 거른다 — 프롬프트는 1차 방어일 뿐이다.

렌더 결과는 OpenAI 호환 채팅 메시지 목록(`[{"role", "content"}]`)이다. 사용자 메시지에 실은 수치가
환각 수치 가드의 허용 집합이 된다(`playbook_narrator.source_numbers`). 입력 길이는
`max_input_chars`로 자른다(표 행을 줄이고, 그래도 넘으면 끝을 자른다). 템플릿 문자열은 줄을 나누면
프롬프트가 바뀌므로 E501을 문자열 단위로 면제한다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

STEP_SYSTEM = """너는 장애 조사 플레이북의 「단계 소견」 작성자다. 한 단계의 수집 결과를 운영자에게 해석해 준다.
규칙:
- 판정(verdict)은 코드가 이미 정했다. 판정을 바꾸거나 반대로 서술하지 마라. 판정이 normal이면 이상·지연·급증을 단정하지 말고, degraded이면 정상이라고 쓰지 마라. 판정이 없으면(null) 판정을 내리지 말고 관찰만 적어라.
- 수치는 「단계 데이터」·「판독」에 적힌 값만 그대로 쓴다. 배수·차이·비율 같은 새 수치를 계산해 만들지 마라.
- 원인을 확정하지 마라. 원인 가설과 순위는 종합 단계가 정한다.
- 「실시간 단면」 표시가 있으면 그 값이 알람 시점이 아니라 현재값임을 밝혀라.
- {max_sentences}문장 이하의 한국어 평서문만 쓴다. 목록·표·머리말 없이 문장만 쓴다."""  # noqa: E501

STEP_USER = """[단계] {step} · {title}
[판정] {verdict} (출처: {verdict_source})
[초점] {focus}{realtime}
[판독]
{readings}
[단계 데이터]
{data}"""

STEP_REALTIME = "\n[실시간 단면] 이 단계 값은 실시간 단면({badges})이다 — 알람 시점 값이 아니다."

SYNTH_SYSTEM = """너는 장애 조사 플레이북의 「종합 소견」 작성자다. 단계 판정·판독·결정적 가설 순위를 인용해 운영자에게 요약한다.
규칙:
- 원인 가설의 순위와 상태(지지·반박·판단 보류)는 코드가 정했다. 순위를 바꾸거나 다른 가설을 1순위로 쓰지 마라. 1순위를 언급하면 「가설 순위」의 1순위 제목을 그대로 쓴다. 「판단 보류」 가설은 확정처럼 쓰지 마라.
- 수치는 입력에 적힌 값만 그대로 쓴다. 새 수치를 계산해 만들지 마라.
- 변경 후보·연관 알람은 「상관 후보」로만 쓰고 인과로 단정하지 마라.
- 대응은 「대응 순서」의 제안 문구를 요약하고, 실행 명령을 만들지 마라.
- {max_sentences}문장 이하의 한국어 평서문만 쓴다. 순서: 1순위 가설과 근거 단계 → 대응 순서 요지 → 추가 확인 항목."""  # noqa: E501

SYNTH_USER = """[플레이북] {playbook}
[가설 순위]
{hypotheses}
[대응 순서]
{actions}
[추가 확인]
{followups}
[판독]
{readings}
[단계 요약]
{steps}
[연관 타임라인]
{timeline}
[변경 후보]
{changes}"""

_TRUNC = "…(길이 상한으로 생략)"


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _fit_table(section: Mapping[str, Any], budget: int) -> str:
    """단계 표를 JSON으로 싣되 길이 상한을 넘으면 뒤 행부터 줄인다(줄인 수를 적는다)."""
    table = section.get("table") or {}
    rows = list(table.get("rows") or [])
    extra = {k: section.get(k) for k in ("missing", "reason") if section.get(k)}
    keep = len(rows)
    while True:
        payload: dict[str, Any] = {
            "columns": list(table.get("columns") or []),
            "rows": rows[:keep],
            **extra,
        }
        if keep < len(rows):
            payload["rows_omitted"] = len(rows) - keep
        text = _dump(payload)
        if len(text) <= budget or keep == 0:
            break
        keep -= 1
    if len(text) > budget:
        text = text[: max(0, budget - len(_TRUNC))] + _TRUNC
    return text


def _readings_lines(readings: Mapping[str, Mapping[str, Any]]) -> str:
    if not readings:
        return "(없음)"
    lines = []
    for name, item in readings.items():
        lines.append(
            f"- {name}={item.get('value')} ({item.get('rule')})"
            + (f" 근거 {_dump(item.get('values'))}" if item.get("values") else "")
            + (f" 사유: {item.get('reason')}" if item.get("reason") else "")
        )
    return "\n".join(lines)


def render_step_messages(
    *,
    section: Mapping[str, Any],
    focus: str,
    max_sentences: int,
    readings: Mapping[str, Mapping[str, Any]],
    realtime_badges: Sequence[str] = (),
    max_input_chars: int = 6000,
) -> list[dict[str, str]]:
    """단계 소견 메시지. `section` = `StepResult.to_dict(include_data=False)` 모양."""
    head = STEP_USER.format(
        step=section.get("step"),
        title=section.get("title"),
        verdict=section.get("verdict"),
        verdict_source=section.get("verdict_source"),
        focus=focus,
        realtime=STEP_REALTIME.format(badges=" · ".join(realtime_badges))
        if realtime_badges
        else "",
        readings=_readings_lines(readings),
        data="",
    )
    data = _fit_table(section, max(200, max_input_chars - len(head)))
    return [
        {"role": "system", "content": STEP_SYSTEM.format(max_sentences=max_sentences)},
        {"role": "user", "content": head + data},
    ]


def _lines(items: Sequence[str]) -> str:
    return "\n".join(f"- {x}" for x in items) if items else "(없음)"


def render_synthesis_messages(
    *,
    playbook_id: str,
    hypotheses: Sequence[Mapping[str, Any]],
    actions: Sequence[Mapping[str, Any]],
    followups: Sequence[str],
    readings: Mapping[str, Any],
    steps: Sequence[Mapping[str, Any]],
    timeline: Sequence[Mapping[str, Any]],
    changes: Sequence[Mapping[str, Any]],
    max_sentences: int,
    max_input_chars: int = 8000,
) -> list[dict[str, str]]:
    """종합 메시지. 가설은 코드 순위 그대로 싣는다(rank·title·status_label·evidence_steps)."""
    hyp_lines = [
        f"{h.get('rank')}. {h.get('title')} [{h.get('status_label')}] "
        f"근거 {', '.join(h.get('evidence_steps') or [])}"
        for h in hypotheses
    ]
    act_lines = [f"{a.get('order')}. {a.get('text')}" for a in actions]
    step_lines = [
        f"{s.get('step')} {s.get('title')}: 판정 {s.get('verdict')}({s.get('verdict_source')}) · "
        f"상태 {s.get('status')}" + (f" · 소견: {s.get('opinion')}" if s.get("opinion") else "")
        for s in steps
    ]
    tl_lines = [f"{t.get('t_offset_min')}분 {t.get('kind')} {t.get('detail')}" for t in timeline]
    ch_lines = [
        f"알람 {c.get('minutes_before_alarm')}분 전 {c.get('source')} "
        f"{c.get('description') or ''}".rstrip()
        for c in changes
    ]
    content = SYNTH_USER.format(
        playbook=playbook_id,
        hypotheses=_lines(hyp_lines),
        actions=_lines(act_lines),
        followups=_lines(list(followups)),
        readings=_dump(dict(readings)),
        steps=_lines(step_lines),
        timeline=_lines(tl_lines),
        changes=_lines(ch_lines),
    )
    if len(content) > max_input_chars:
        content = content[: max(0, max_input_chars - len(_TRUNC))] + _TRUNC
    return [
        {"role": "system", "content": SYNTH_SYSTEM.format(max_sentences=max_sentences)},
        {"role": "user", "content": content},
    ]


__all__ = [
    "STEP_SYSTEM",
    "STEP_USER",
    "SYNTH_SYSTEM",
    "SYNTH_USER",
    "render_step_messages",
    "render_synthesis_messages",
]
