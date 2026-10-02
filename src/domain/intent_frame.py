"""확정된 의도의 구조 정본 — IntentFrame (plans/107 W1).

재작성문은 언제나 이 프레임에서 렌더된다(P-1 "정본은 구조, 텍스트는 파생"). 이 모듈은
**순수**하다 — 파서 산출(``ParsedRequirements``·application)이나 레지스트리(infrastructure)를
import하지 않고, application 빌더(``src.nodes.intent_frame_builder``)가 plain 값으로 변환해
넣는다(§4.10 계층 주의 — ``src.domain``은 ``src.domain.*`` 외 내부 import 0).

- ``merge_frame`` — 발화·되묻기 답변·직전 턴 승계·기본값·레지스트리의 **우선순위를 코드로**(§4.3)
- ``rewrite_needed`` — 건드릴 이유가 있을 때만 재작성·병기한다(§4.3.1 · P-9)
- ``verify_rewrite`` — LLM 재작성문(R1·R2·R6)을 프레임과 대조한다(§4.7 · 섀도 전용)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

#: 슬롯 값의 출처. 우선순위는 이 순서가 아니라 ``merge_frame``의 규칙이 정한다.
SOURCE_UTTERANCE = "utterance"                  # 이번 턴 발화
SOURCE_ANSWER = "clarification_answer"          # 되묻기 구조화 답변(존 선택 등)
SOURCE_INHERITED = "context_inherited"          # 직전 턴 승계
SOURCE_DEFAULT = "default"                      # 기본값 정책(가정 표시 대상)
SOURCE_REGISTRY = "registry"                    # 레지스트리·결정표 파생

SLOT_SOURCES: tuple[str, ...] = (
    SOURCE_UTTERANCE, SOURCE_ANSWER, SOURCE_INHERITED, SOURCE_DEFAULT, SOURCE_REGISTRY,
)

#: 위치/DB 성격의 대상 슬롯 — 이번 발화가 비었을 때만 승계한다(규칙 3).
LOCATION_TARGET_KEYS: tuple[str, ...] = ("zone", "db_ids")
#: 엔티티(서버·장비) 성격의 대상 슬롯 — 지시 참조가 있을 때만 승계한다(규칙 3′).
ENTITY_TARGET_KEYS: tuple[str, ...] = ("hosts",)

#: ``frame`` 수준 스칼라 슬롯. 승계 대상이 아니다(§4.3은 대상만 승계를 규정한다).
SCALAR_SLOTS: tuple[str, ...] = ("metrics", "time_range", "aggregation", "limit", "output")

GATE_PASS_THROUGH = "pass_through"
GATE_NON_UTTERANCE = "non_utterance_source"
GATE_UNRESOLVED = "unresolved_slot"

VERIFY_PASS = "pass"


@dataclass(frozen=True)
class SlotValue:
    """슬롯 하나의 값과 출처.

    Attributes:
        value: 슬롯 값(원형 유지 — 자유 형식 입력을 느슨하게 받는다, E-3c D3)
        source: ``SLOT_SOURCES`` 중 하나
        evidence: 출처의 근거(예: ``"selected_db_ids"`` · ``"previous_entities"``)
        spec_uncertain: 사용자가 지정하지 않은 값인가(되묻기 후보·가정 표시 대상)
    """

    value: Any
    source: str
    evidence: str | None = None
    spec_uncertain: bool = False

    def __post_init__(self) -> None:
        if self.source not in SLOT_SOURCES:
            raise ValueError(f"알 수 없는 슬롯 출처: {self.source}")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"value": self.value, "source": self.source}
        if self.evidence:
            out["evidence"] = self.evidence
        if self.spec_uncertain:
            out["spec_uncertain"] = True
        return out


@dataclass
class IntentFrame:
    """병합이 끝난 확정 의도. ``original_query``는 원문 그대로(불변)다."""

    original_query: str
    intent: str = ""
    targets: dict[str, SlotValue] = field(default_factory=dict)
    metrics: SlotValue | None = None
    time_range: SlotValue | None = None
    aggregation: SlotValue | None = None
    limit: SlotValue | None = None
    output: SlotValue | None = None
    filters: list[dict[str, Any]] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    frame_version: int = 1

    def all_slots(self) -> list[tuple[str, SlotValue]]:
        """값이 있는 슬롯을 (이름, 값) 목록으로 — 대상은 ``targets.<키>``로 표기한다."""
        slots: list[tuple[str, SlotValue]] = [
            (f"targets.{key}", sv) for key, sv in sorted(self.targets.items())
        ]
        for name in SCALAR_SLOTS:
            sv = getattr(self, name)
            if sv is not None:
                slots.append((name, sv))
        return slots

    def slot_dict(self) -> dict[str, dict[str, Any]]:
        """감사·표시용 슬롯 사전 — 값과 출처만(원문 전문은 싣지 않는다, D-183)."""
        return {name: sv.to_dict() for name, sv in self.all_slots()}

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_version": self.frame_version,
            "intent": self.intent,
            "slots": self.slot_dict(),
            "filters": list(self.filters),
            "depends_on": list(self.depends_on),
            "unresolved": list(self.unresolved),
        }

    def frame_hash(self) -> str:
        """슬롯 구조의 결정적 해시 — 같은 해석이면 같은 값(턴별 감사 대조용)."""
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _present(value: Any) -> bool:
    """슬롯 값이 실제로 지정됐는가 — None·빈 문자열·빈 컬렉션은 미지정이다."""
    if value is None:
        return False
    if isinstance(value, (str, list, tuple, dict, set)):
        return len(value) > 0
    return True


def merge_frame(
    *,
    original_query: str,
    intent: str = "",
    utterance: dict[str, Any] | None = None,
    answers: dict[str, Any] | None = None,
    inherited: dict[str, Any] | None = None,
    demonstrative: bool = False,
    defaults: dict[str, Any] | None = None,
    registry: dict[str, Any] | None = None,
    filters: list[dict[str, Any]] | None = None,
    depends_on: list[str] | None = None,
    unresolved: list[dict[str, Any]] | None = None,
) -> IntentFrame:
    """발화·답변·승계·기본값·레지스트리를 우선순위대로 병합한다(§4.3).

    입력 사전의 키는 슬롯 이름이다 — 대상은 ``targets.zone``·``targets.db_ids``·``targets.hosts``,
    나머지는 ``metrics``·``time_range``·``aggregation``·``limit``·``output``.

    | 순위 | 출처 | 규칙 |
    |---:|---|---|
    | 1 | 되묻기 답변 | 답한 슬롯은 무조건 확정 |
    | 2 | 이번 발화 | 발화에 있는 값은 승계값을 **대체**(병합 금지 — 2026-07-16 사례) |
    | 3 | 승계 — 위치/DB | 이번 발화에 그 슬롯이 **비었을 때만** |
    | 3′ | 승계 — 엔티티 | ``demonstrative``(지시 참조)일 때만 — 상한 샘플을 스코프로 쓰지 않는다 |
    | 4 | 기본값 | ``spec_uncertain=True`` |
    | 5 | 레지스트리 파생 | 앞 단계가 모두 비었을 때만 |

    Args:
        original_query: 원문(불변)
        intent: 라우터 허용 집합의 의도 라벨
        utterance: 이번 발화에서 파생한 슬롯
        answers: 되묻기 구조화 답변 슬롯
        inherited: 직전 턴 승계 후보 슬롯
        demonstrative: 지시 참조("해당/그 서버") 판정 결과 — 판정 방식은 호출부 소유
        defaults: 기본값 정책 슬롯
        registry: 레지스트리 파생 슬롯
        filters: 파서 ``filter_conditions``(자유 형식 유지)
        depends_on: 순차 의존 선행 task 식별자
        unresolved: 병합하지 못한 슬롯 ``[{slot, reason}]``

    Returns:
        병합된 ``IntentFrame``
    """
    utterance = utterance or {}
    answers = answers or {}
    inherited = inherited or {}
    defaults = defaults or {}
    registry = registry or {}

    names = {*utterance, *answers, *inherited, *defaults, *registry}
    targets: dict[str, SlotValue] = {}
    scalars: dict[str, SlotValue] = {}

    for name in sorted(names):
        chosen = _choose(name, utterance, answers, inherited, defaults, registry, demonstrative)
        if chosen is None:
            continue
        if name.startswith("targets."):
            targets[name.split(".", 1)[1]] = chosen
        elif name in SCALAR_SLOTS:
            scalars[name] = chosen

    return IntentFrame(
        original_query=original_query,
        intent=intent,
        targets=targets,
        metrics=scalars.get("metrics"),
        time_range=scalars.get("time_range"),
        aggregation=scalars.get("aggregation"),
        limit=scalars.get("limit"),
        output=scalars.get("output"),
        filters=list(filters or []),
        depends_on=list(depends_on or []),
        unresolved=list(unresolved or []),
    )


def _choose(
    name: str,
    utterance: dict[str, Any],
    answers: dict[str, Any],
    inherited: dict[str, Any],
    defaults: dict[str, Any],
    registry: dict[str, Any],
    demonstrative: bool,
) -> SlotValue | None:
    """슬롯 하나의 값을 우선순위대로 고른다."""
    if _present(answers.get(name)):
        return SlotValue(answers[name], SOURCE_ANSWER)
    if _present(utterance.get(name)):
        return SlotValue(utterance[name], SOURCE_UTTERANCE)
    key = name.split(".", 1)[1] if name.startswith("targets.") else ""
    if _present(inherited.get(name)):
        if key in LOCATION_TARGET_KEYS:
            return SlotValue(inherited[name], SOURCE_INHERITED)
        if key in ENTITY_TARGET_KEYS and demonstrative:
            return SlotValue(inherited[name], SOURCE_INHERITED, evidence="demonstrative")
    if _present(defaults.get(name)):
        return SlotValue(defaults[name], SOURCE_DEFAULT, spec_uncertain=True)
    if _present(registry.get(name)):
        return SlotValue(registry[name], SOURCE_REGISTRY)
    return None


def rewrite_needed(frame: IntentFrame) -> tuple[bool, str]:
    """재작성·병기가 필요한지 결정적으로 판정한다(§4.3.1 · P-9).

    전부 이번 발화에서 나왔고 미해결이 없으면 원문을 건드리지 않는다 — 모호하지 않은 질의를
    재작성해 오염 위험만 지는 것을 막는다(§2.4의 무조건 재작성 오염 3건).

    Returns:
        (필요 여부, 사유) — 사유는 ``GATE_*`` 상수
    """
    if frame.unresolved:
        return True, GATE_UNRESOLVED
    if any(sv.source != SOURCE_UTTERANCE for _, sv in frame.all_slots()):
        return True, GATE_NON_UTTERANCE
    return False, GATE_PASS_THROUGH


def _norm(text: str) -> str:
    return "".join(str(text).split()).lower()


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if str(v).strip()]
    text = str(value).strip()
    return [text] if text else []


def verify_rewrite(
    frame: IntentFrame,
    rewritten: str | None,
    *,
    foreign_location_terms: list[str] | None = None,
    prior_entities: list[str] | None = None,
) -> str:
    """LLM 재작성문을 프레임과 대조한다(§4.7). **라우팅 결정은 바꾸지 않는다**(D-004).

    검사 순서는 사례의 무게 순이다 — 앞 검사가 실패하면 그 사유를 돌려준다.

    1. ``slot_missing`` — 프레임의 엔티티 식별자가 재작성문에 없다(정규화 일치)
    2. ``location_leak`` — 대상 밖 위치어가 재작성문에 있다(미선택 존 누출 2026-08-05 ·
       명시 위치와 직전 위치의 병합 2026-07-16)
    3. ``scope_shrink`` — 프레임에 없는 직전 엔티티가 재작성문에 들어왔다(상한 샘플을 스코프로
       오인 2026-08-04)

    식별자 외 슬롯(지표·기간)은 대조하지 않는다 — 표기 변형이 많아 정규화 사전 없이 비교하면
    섀도 실패율이 거짓으로 부푼다.

    Args:
        frame: 확정 프레임
        rewritten: 대조할 재작성문(None이면 대상 없음 → pass)
        foreign_location_terms: 이번 대상 **밖**의 위치 표면어(호출부가 레지스트리에서 해소)
        prior_entities: 직전 턴 엔티티 식별자

    Returns:
        ``"pass"`` 또는 ``"fail:<사유>"``
    """
    if not rewritten:
        return VERIFY_PASS
    text = _norm(rewritten)
    hosts_sv = frame.targets.get("hosts")
    frame_hosts = _as_list(hosts_sv.value) if hosts_sv else []

    if any(_norm(h) not in text for h in frame_hosts):
        return "fail:slot_missing"
    if any(_norm(t) and _norm(t) in text for t in (foreign_location_terms or [])):
        return "fail:location_leak"
    frame_host_set = {_norm(h) for h in frame_hosts}
    for entity in prior_entities or []:
        key = _norm(entity)
        if key and key in text and key not in frame_host_set:
            return "fail:scope_shrink"
    return VERIFY_PASS
