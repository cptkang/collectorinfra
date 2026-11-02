"""조사 실패 모드 가드 — 정체 · 미조회 소스 · 부분 브리핑 원장 (plans/198 I-10 · 154 H-5).

**순수 도메인 모듈**(stdlib + 같은 domain). 입력은 도구 호출 기록(도구명·인자·status)과 실행 기록
이벤트다 — LLM 서술은 입력이 아니다. 배선은 dispatcher·`diagnosis._StreamDrain`이 플래그 뒤에서
한다.

- **정체**(`StallTracker`): 같은 `(도구, 정규화 인자)`가 `repeat_threshold`회이거나, 새 근거
  (처음 보는 성공·0건 조회)가 없는 도구 step이 `idle_steps`회 이어지면 정체다.
  APM 정체 가드(plans/87 J3 —
  `investigation_limits.repeated_calls`, 조사 뒤 사후 판정)의 반복 집계를 그대로 써서 조사
  **진행 중**에 step 단위로 판정한다. 인자는 키 정렬 · 문자열 앞뒤 공백 제거 · None 값 제거로
  정규화한다(holmes 중복 거부는 완전 일치만 본다). 대소문자는 그대로 둔다 — PromQL 라벨 값·호스트
  같은 대소문자 구분 인자를 다른 호출로 센다. 사유에 싣는 인자 표기는 주입한 `masker`(가림 콜백 ·
  application이 넘긴다)를 거친다 — 이 모듈은 가림 규칙을 모른다.
- **미조회 소스**(`ExpectedSources`): kind별 기대 소스 중 도구 호출 0인 소스. kind는 `host.cpu`처럼
  점으로 나뉘며, 정확한 키가 없으면 앞 마디(`host`)를 본다.
- **활동 기록 원장**(`ledger_from_activity`): 타임아웃으로 조사 스레드가 버려지면 결과가 돌아오지
  않는다 — 실행 기록(`ActivityLog`)의 HolmesGPT `tool_end` 이벤트(가림·발췌 상한 적용분)로 원장을
  만든다.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sre_agent.domain.evidence_ledger import EvidenceLedger, make_row
from sre_agent.domain.investigation_limits import repeated_calls

#: 새 근거로 세는 도구 결과 status(브리핑 인용 근거와 같다 · 실행 기록의 캐시 적중 포함).
EVIDENCE_STATUSES: frozenset[str] = frozenset({"success", "no_data", "cached"})

UNCHECKED_SOURCES_LIMIT = "확인하지 않은 소스: {sources}"


def _norm(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _norm(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [_norm(v) for v in value]
    if isinstance(value, str):
        return value.strip()
    return value


def normalized_call_key(tool_name: str, params: object, description: str = "") -> tuple[str, str]:
    """정체 판정 키 `(도구명, 정규화 인자 표기)` — 인자가 dict가 아니면 호출 요약(description)."""
    if isinstance(params, Mapping):
        args = json.dumps(_norm(params), sort_keys=True, ensure_ascii=False, default=str)
    else:
        args = str(description or "").strip()
    return str(tool_name or ""), args


def _shown(args: str) -> str:
    return args if len(args) <= 120 else args[:120] + "…"


@dataclass
class StallTracker:
    """도구 step 단위 정체 판정기 — `observe`로 도구 결과를, step이 끝나면 `end_step`을 부른다.

    `masker`는 사유에 싣는 인자 표기의 가림 콜백이다(사유는 브리핑·감사·답변으로 나간다 —
    기본은 그대로).
    """

    repeat_threshold: int = 3
    idle_steps: int = 4
    masker: Callable[[str], str] = field(default=lambda s: s, repr=False, compare=False)
    _keys: list = field(default_factory=list)
    _seen_evidence: set = field(default_factory=set)
    _new_in_step: int = 0
    _idle: int = 0
    reason: str | None = None

    def observe(self, tool_name: str, params: object, description: str, status: str) -> None:
        """도구 결과 1건."""
        key = normalized_call_key(tool_name, params, description)
        self._keys.append(key)
        if str(status) in EVIDENCE_STATUSES and key not in self._seen_evidence:
            self._seen_evidence.add(key)
            self._new_in_step += 1

    def end_step(self) -> str | None:
        """도구 step 하나가 끝났다 — 정체면 사유 한 줄(한 번 정체면 계속 같은 사유)."""
        if self.reason is not None:
            return self.reason
        self._idle = 0 if self._new_in_step else self._idle + 1
        self._new_in_step = 0
        repeated = repeated_calls(self._keys, self.repeat_threshold)
        if repeated:
            name, args, n = repeated[0]
            shown = _shown(self.masker(args))
            self.reason = (f"조사 정체 — 같은 도구·같은 인자 호출 {n}회({name} {shown}) "
                           "→ 조사 중단")
        elif self._idle >= self.idle_steps:
            self.reason = f"조사 정체 — 새 근거 없는 도구 단계 {self._idle}회 연속 → 조사 중단"
        return self.reason


@dataclass(frozen=True)
class ExpectedSources:
    """kind별 기대 소스 — `sources`(소스 → 도구 이름 접두어) · `kinds`(kind → 소스 목록).

    `allowed`(plans/198 I-9 · 선택)는 kind → 허용 소스다 — 툴셋 좁히기가 읽는다. 기대 소스와
    따로 둔다(기대 ⊆ 허용 · 미조회 표기는 `kinds`만 본다).
    """

    sources: Mapping[str, tuple[str, ...]]
    kinds: Mapping[str, tuple[str, ...]]
    allowed: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @staticmethod
    def _lookup(table: Mapping[str, tuple[str, ...]], kind: str | None) -> tuple[str, ...]:
        if not kind:
            return ()
        if kind in table:
            return table[kind]
        return table.get(kind.split(".", 1)[0], ())

    def expected_for(self, kind: str | None) -> tuple[str, ...]:
        """kind의 기대 소스(정확한 키 → 앞 마디 → 없음)."""
        return self._lookup(self.kinds, kind)

    def allowed_for(self, kind: str | None) -> tuple[str, ...]:
        """kind의 허용 소스(정확한 키 → 앞 마디 → 없음 = 좁히지 않음)."""
        return self._lookup(self.allowed, kind)

    def source_of(self, tool_name: str) -> str | None:
        """도구 이름이 속한 소스(접두어 일치 · 없으면 None)."""
        for source, prefixes in self.sources.items():
            if any(tool_name.startswith(p) for p in prefixes):
                return source
        return None


def unchecked_sources(
    kind: str | None,
    tool_names: Iterable[str],
    spec: ExpectedSources,
    *,
    loaded: Iterable[str] | None = None,
) -> list[str]:
    """기대 소스 중 도구 호출이 한 건도 없는 소스(기대 순서).

    `loaded`(조사 에이전트에 로드된 도구 이름)를 주면 로드된 도구가 없는 소스(미연결)는 뺀다 —
    「연결 안 됨」을 「조회 안 함」으로 적지 않는다. None이면 기대 소스 전부를 본다.
    """
    called = {spec.source_of(str(n or "")) for n in tool_names}
    expected = spec.expected_for(kind)
    if loaded is not None:
        connected = {spec.source_of(str(n or "")) for n in loaded}
        expected = tuple(s for s in expected if s in connected)
    return [s for s in expected if s not in called]


def unchecked_sources_limitation(sources: Sequence[str]) -> str | None:
    """한계 한 줄(빈 목록이면 None)."""
    return UNCHECKED_SOURCES_LIMIT.format(sources=", ".join(sources)) if sources else None


def activity_tool_events(events: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """실행 기록 중 HolmesGPT 도구 결과(`tool_end`) 이벤트(기록 순서)."""
    return [e for e in events if e.get("engine") == "holmes" and e.get("kind") == "tool_end"]


def ledger_from_activity(events: Iterable[Mapping[str, Any]]) -> EvidenceLedger:
    """HolmesGPT `tool_end` 중 근거 status인 것으로 원장을 만든다(인자 자리 = 호출 요약 한 줄)."""
    rows = []
    for e in activity_tool_events(events):
        if str(e.get("status") or "") not in EVIDENCE_STATUSES:
            continue
        at = e.get("at")
        rows.append(make_row(
            f"ev{len(rows) + 1}", str(e.get("tool") or ""), str(e.get("label") or ""),
            str(e.get("excerpt") or ""), float(at) if isinstance(at, (int, float)) else None,
        ))
    return EvidenceLedger(tuple(rows))


__all__ = [
    "EVIDENCE_STATUSES",
    "UNCHECKED_SOURCES_LIMIT",
    "ExpectedSources",
    "StallTracker",
    "activity_tool_events",
    "ledger_from_activity",
    "normalized_call_key",
    "unchecked_sources",
    "unchecked_sources_limitation",
]
