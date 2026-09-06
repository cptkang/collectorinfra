"""조사 브리핑 수신 계약 — 공용 렌더러 (plans/50 G6 · SPEC-briefing-contract · D-194).

`sre_agent` briefing_builder.build_briefing()이 내는 dict가 **정본**이다. 이 모듈은 그 dict를
표현 매체(챗 평문 · WorkB HTML)에 무관한 `[(라벨, 평문 값), ...]`로 편다. 두 소비자
(`src.nodes.fault_diagnosis` · `noise_gate.application.nodes.alarm_notifier`)가 이 한 함수를 쓴다 —
각자 렌더하던 동안 키 오기(`evidence`·`limitation`)로 `[한계]`·`[가설]`·`[중요도]`가 사용자에게
도달하지 않았고 list·dict가 Python repr로 새어 나갔다.

`investigation_payload.py`(송신 계약)의 대칭 짝이다. domain 계층이므로 표준 라이브러리만 의존하고,
HTML 이스케이프·줄바꿈 변환은 소비자가 한다(domain은 표현 매체를 모른다).
"""

from __future__ import annotations

#: 생산자 산출 키 전체. `tests/test_briefing_contract.py`와 `sre_agent/tests/test_briefing_builder.py`가
#: 같은 리터럴로 양쪽에서 단언한다(양방향 import 0 — D-118).
BRIEFING_CONTRACT_KEYS: frozenset[str] = frozenset({
    "severity", "summary", "timeline", "bottleneck", "cause", "root_cause_hypotheses",
    "recommendation", "limitations", "citations_verified", "hypotheses",
})

#: 렌더 순서와 라벨. 순서 목록에 없는 키는 말미에 원 키명으로 렌더한다(침묵 누락 방지 —
#: 생산자가 필드를 늘려도 소비자가 모른 채 버리지 않는다).
_ORDERED: tuple[tuple[str, str], ...] = (
    ("severity", "중요도"),
    ("summary", "요약"),
    ("timeline", "타임라인"),
    ("bottleneck", "병목"),
    ("cause", "원인"),
    ("root_cause_hypotheses", "원인 가설"),
    ("recommendation", "권고"),
    ("limitations", "한계"),
    ("hypotheses", "가설"),
)

#: 메타데이터라 출력하지 않는 키. `citations_verified`가 False면 생산자가 이미 `limitations`에
#: 사유를 넣으므로 중복이다. `stub`·`elements`는 스텁 경로 표지.
_SUPPRESSED: frozenset[str] = frozenset({"citations_verified", "stub", "elements"})


def _is_empty(val: object) -> bool:
    return val is None or val is False or val == "" or val == [] or val == {}


def _render_severity(sev: dict) -> str:
    """중요도 헤더 한 줄 — `심각(신뢰도 high) — 게이트 PAGE · 조사 상향 · 시그니처 oom_kill`."""
    level = sev.get("level") or "미상"
    conf = sev.get("confidence")
    head = f"{level}(신뢰도 {conf})" if conf else str(level)
    parts: list[str] = []
    if sev.get("gate_tier"):
        parts.append(f"게이트 {sev['gate_tier']}")
    if sev.get("escalate"):
        parts.append("조사 상향")
    if sev.get("signals"):
        parts.append("시그니처 " + ", ".join(str(s) for s in sev["signals"]))
    if sev.get("evidence_insufficient"):
        parts.append("증거 불충분")
    return head + (" — " + " · ".join(parts) if parts else "")


def _render_hypothesis(h: dict) -> str:
    """가설 dict 한 줄 — `1) (high) 원인 — 근거: a; b`(plans/50 §7.2 · §9.1)."""
    rank = h.get("rank")
    conf = h.get("confidence")
    head = f"{rank}) " if rank is not None else ""
    head += f"({conf}) " if conf else ""
    line = head + str(h.get("cause") or "")
    evidence = [str(e) for e in (h.get("evidence") or []) if not _is_empty(e)]
    if evidence:
        line += " — 근거: " + "; ".join(evidence)
    return line


def _render_item(item: object) -> str:
    if isinstance(item, dict):
        if "cause" in item:
            return _render_hypothesis(item)
        return " · ".join(f"{k}: {v}" for k, v in item.items() if not _is_empty(v))
    return str(item)


def _render_value(key: str, val: object) -> str:
    """값을 평문으로 편다. 복수 항목은 줄바꿈으로 나열한다(repr 금지)."""
    if key == "severity" and isinstance(val, dict):
        return _render_severity(val)
    if isinstance(val, dict):
        if "items" in val:  # 권고 형태 {"items": [...], "note": "..."}
            lines = [str(i) for i in (val.get("items") or []) if not _is_empty(i)]
            if not _is_empty(val.get("note")):
                lines.append(str(val["note"]))
            return "\n".join(lines)
        return "\n".join(f"{k}: {v}" for k, v in val.items() if not _is_empty(v))
    if isinstance(val, (list, tuple)):
        return "\n".join(_render_item(i) for i in val if not _is_empty(i))
    return str(val)


def render_briefing_lines(briefing: dict) -> list[tuple[str, str]]:
    """브리핑 dict → `[(라벨, 평문 값), ...]` 순서 고정. 표현 매체 비의존(순수 함수).

    - 순서 8키는 `_ORDERED` 순, 그 외 키는 원 키명으로 말미에 정렬 렌더한다.
    - 빈 값(`None`·`""`·`[]`·`{}`·`False`)은 라벨째 생략한다.
    - `_SUPPRESSED`는 출력하지 않는다. 스텁 판정은 호출자 몫이다.
    """
    out: list[tuple[str, str]] = []
    for key, label in _ORDERED:
        val = briefing.get(key)
        if _is_empty(val):
            continue
        rendered = _render_value(key, val)
        if rendered.strip():
            out.append((label, rendered))
    known = {k for k, _ in _ORDERED} | _SUPPRESSED
    for key in sorted(k for k in briefing if k not in known):
        val = briefing[key]
        if _is_empty(val):
            continue
        rendered = _render_value(str(key), val)
        if rendered.strip():
            out.append((str(key), rendered))
    return out


__all__ = ["BRIEFING_CONTRACT_KEYS", "render_briefing_lines"]
