"""알람 유형별 조사 플레이북 — 스키마 · 선택기 (plans/187 §5.2 · §4.1 · §5.7.2 · D-341).

플레이북은 선언형 YAML(`sre_agent/config/playbooks/*.yaml`)이다. 이 모듈은 그 dict를
pydantic으로 검증하고(`parse_playbook`), 알람 사실(`AlarmFacts`)에 맞는 플레이북을 결정적으로
고른다(`select_playbook`). 판정은 결정적이고 LLM은 서술만 한다(D-035). 플레이북은 액션 ID만
쓰며 도구명은 스텝 라이브러리(application)에만 둔다(§5.2 — 벤더 중립).

**순수 도메인 모듈** — 파일 IO는 `infrastructure/playbook_loader.py`가 한다.
외부 의존은 pydantic뿐이다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StringConstraints,
    field_validator,
    model_validator,
)

from sre_agent.domain.playbook_expr import STEP_ID_PATTERN, Expr, compile_expr

#: fallback 플레이북 ID — 어떤 유형에도 맞지 않으면 이것을 고른다(§4.1 · G-3).
FALLBACK_ID = "fallback"

#: 스텝 액션 화이트리스트 — §5.3 스텝 라이브러리 S0~S9 + §4.1 표의 보조 액션
#: (액션 ID만 · 도구명 없음).
STEP_ACTIONS: frozenset[str] = frozenset({
    "scope.resolve_was_targets",  # S0
    "scope.host_to_was",          # S0′(역방향)
    "apm.golden_signals",         # S1
    "apm.runtime_and_pool",       # S2
    "apm.slow_tx_breakdown",      # S3
    "host.usage_trend",           # S4
    "xsource.related_alarms",     # S5
    "host.process_top",           # S6
    "change.recent",              # S7
    "dpm.db_health",              # S8
    "synthesize",                 # S9
    # §4.1 보조 액션
    "apm.events_errors",          # was.error 오류 유형 상위
    "apm.active_services",        # was.active 장시간 실행 서비스
    "apm.active_sql",             # host.cpu 실행 중 SQL(S3b · W20b)
    "host.resource_status",       # host.process·host.network 생존 상태
    "host.topology",              # host.network 같은 구간 여부
    "host.alarm_history",         # host.process·fallback 재발 확인
})

#: 단계 소견 문장 상한(§4.0 — 단계당 ≤ 4문장).
MAX_NARRATE_SENTENCES = 4

_PLAYBOOK_ID_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$"

_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

_STRICT = ConfigDict(extra="forbid", frozen=True)


def _compile_when(value: str | None) -> Expr | None:
    """`when` 문자열을 컴파일한다. 구문 오류는 `ExprSyntaxError`(ValueError)로 검증 실패가 된다."""
    return None if value is None else compile_expr(value)


class MatchSpec(BaseModel):
    """결정적 매칭 조건 — 지정한 조건이 모두 맞아야 일치한다(AND).

    resource_type: 알람 resourceType(대소문자 무시 동등 비교).
    alarm_kind:    `classify_alarm_kind` 결과(cpu·memory·disk·network·process·log·apm).
    any_kind:      알람 사실의 정규화 kind 목록(`was_signals` kind 또는 이벤트 유형
                   정규화 값)과 하나라도 겹치면 일치.
    surface_terms: 채팅 증상 질의 표면어(§5.6.2 ③ · W12) — 알람 매칭에는 쓰지 않는다.
    priority:      선택 순서(작을수록 먼저 · 같으면 목록 순서).
    """

    model_config = _STRICT

    resource_type: _Text | None = None
    alarm_kind: _Text | None = None
    any_kind: list[_Text] = Field(default_factory=list)
    surface_terms: list[_Text] = Field(default_factory=list)
    priority: int = 100

    def is_empty(self) -> bool:
        """알람 매칭 조건(resource_type·alarm_kind·any_kind)이 하나도 없으면 참."""
        return self.resource_type is None and self.alarm_kind is None and not self.any_kind

    def matches(self, facts: AlarmFacts) -> tuple[str, ...] | None:
        """일치하면 충족한 조건 이름 튜플, 아니면 None. 빈 조건은 일치하지 않는다."""
        if self.is_empty():
            return None
        hit: list[str] = []
        if self.resource_type is not None:
            if _norm(facts.resource_type) != _norm(self.resource_type):
                return None
            hit.append("resource_type")
        if self.alarm_kind is not None:
            if _norm(facts.alarm_kind) != _norm(self.alarm_kind):
                return None
            hit.append("alarm_kind")
        if self.any_kind:
            wanted = {_norm(k) for k in self.any_kind}
            if not wanted & {_norm(k) for k in facts.kinds}:
                return None
            hit.append("any_kind")
        return tuple(hit)


class WindowSpec(BaseModel):
    """사건창·기준선(§4.0 — 알람 시각 −30분 ~ +10분 · 전일·전주 동시간)."""

    model_config = _STRICT

    before_min: int = Field(default=30, ge=0)
    after_min: int = Field(default=10, ge=0)
    baseline: list[Literal["prev_day", "prev_week"]] = Field(
        default_factory=lambda: ["prev_day", "prev_week"]
    )


class NarrateSpec(BaseModel):
    """단계 소견 지시 — LLM 서술의 초점과 문장 상한(§4.0 ≤ 4문장).

    `why`(선택 · plans/187 W20 §5.6.4 ⓒ2): 조건 없는 단계의 「왜 이 단계를 보나」 작성자 한 문장
    (결정적 — LLM 0). 없으면 「{focus}를 확인」으로 대신한다.
    """

    model_config = _STRICT

    focus: _Text
    why: _Text | None = None
    max_sentences: int = Field(default=MAX_NARRATE_SENTENCES, ge=1, le=MAX_NARRATE_SENTENCES)


class StepSpec(BaseModel):
    """플레이북 스텝 1개.

    id:       S0~S9 + 선택 접미 소문자 1자(역방향 S0′ = `S0r` · 보조 스텝 = `S5a` 등).
    action:   `STEP_ACTIONS` 중 하나.
    args:     액션 인자(임계값 등 — 실행기가 해석).
    for_each: `targets`면 S0이 확정한 대상마다 실행 · `host_was`면 역방향 S0′이 찾은 이 호스트 WAS
              전부(판정 난 것 · W20b).
    narrate:  단계 소견 지시(없으면 소견 없음).
    when:     실행 조건식(`playbook_expr` 문법). UNKNOWN 처리는 실행기가 정한다.
    timeout_s: 이 스텝의 대기 상한(초 · 선택 — plans/187 W21 P3). 없으면 실행기 기본값이고, 어느
              쪽이든 남은 예산보다 길지 않다.
    """

    model_config = _STRICT

    id: str = Field(pattern=STEP_ID_PATTERN.pattern)
    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    for_each: Literal["targets", "host_was"] | None = None
    narrate: NarrateSpec | None = None
    when: str | None = None
    timeout_s: float | None = Field(default=None, gt=0)

    _when_expr: Expr | None = PrivateAttr(default=None)

    @field_validator("action")
    @classmethod
    def _known_action(cls, value: str) -> str:
        if value not in STEP_ACTIONS:
            raise ValueError(f"미지 액션 {value!r}")
        return value

    @model_validator(mode="after")
    def _compile(self) -> StepSpec:
        self._when_expr = _compile_when(self.when)
        return self

    @property
    def when_expr(self) -> Expr | None:
        """컴파일된 `when`(없으면 None)."""
        return self._when_expr


class MitigationItem(BaseModel):
    """런북 완화 제안 1건 — 실행 명령이 아니다(remediation.py 형식과 같은 칸)."""

    model_config = _STRICT

    when: str | None = None
    action: _Text
    risk: Literal["low", "medium", "high"]
    verification: _Text
    rollback: _Text

    _when_expr: Expr | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def _compile(self) -> MitigationItem:
        self._when_expr = _compile_when(self.when)
        return self

    @property
    def when_expr(self) -> Expr | None:
        """컴파일된 `when`(없으면 None)."""
        return self._when_expr


class RunbookBlock(BaseModel):
    """런북 블록(§5.7.2) — meaning·impact·owner·last_reviewed는 필수(커버리지 게이트)."""

    model_config = _STRICT

    meaning: _Text
    impact: _Text
    mitigation: list[MitigationItem] = Field(default_factory=list)
    escalation: _Text | None = None
    references: list[_Text] = Field(default_factory=list)
    owner: _Text
    last_reviewed: date
    review_every_days: int = Field(default=180, gt=0)

    def is_stale(self, today: date) -> bool:
        """`last_reviewed + review_every_days`가 지났으면 참.

        「검토 기한 경과」 배지 판정이다 — 내용은 숨기지 않는다.
        """
        return today > self.last_reviewed + timedelta(days=self.review_every_days)


class DeepDiveSpec(BaseModel):
    """HolmesGPT 심층 조사 지시(§5.0.1). mode 미지정이면 전역 설정을 따른다."""

    model_config = _STRICT

    mode: Literal["off", "conditional", "parallel"] | None = None
    focus: _Text | None = None

    @field_validator("mode", mode="before")
    @classmethod
    def _yaml_off(cls, value: object) -> object:
        # YAML 1.1은 따옴표 없는 `off`를 False로 읽는다.
        return "off" if value is False else value


class Playbook(BaseModel):
    """플레이북 1개(§5.2)."""

    model_config = _STRICT

    id: str = Field(pattern=_PLAYBOOK_ID_PATTERN)
    version: int = Field(ge=1)
    match: MatchSpec = Field(default_factory=MatchSpec)
    window: WindowSpec = Field(default_factory=WindowSpec)
    steps: list[StepSpec] = Field(min_length=1)
    response_hints: dict[str, _Text] = Field(default_factory=dict)
    runbook: RunbookBlock
    deep_dive: DeepDiveSpec = Field(default_factory=DeepDiveSpec)

    @model_validator(mode="after")
    def _check(self) -> Playbook:
        if self.id == FALLBACK_ID:
            if not self.match.is_empty():
                raise ValueError("fallback 플레이북은 match 조건을 두지 않는다")
        elif self.match.is_empty():
            raise ValueError("match 조건(resource_type·alarm_kind·any_kind)이 하나 이상 필요하다")
        seen: set[str] = set()
        for step in self.steps:
            if step.id in seen:
                raise ValueError(f"스텝 ID 중복 {step.id!r}")
            if step.when_expr is not None:
                unknown = step.when_expr.step_refs() - seen
                if unknown:
                    raise ValueError(
                        f"스텝 {step.id} when이 앞선 스텝이 아닌 {sorted(unknown)}를 참조한다"
                    )
            seen.add(step.id)
        return self

    @property
    def is_fallback(self) -> bool:
        """fallback 플레이북이면 참."""
        return self.id == FALLBACK_ID


def parse_playbook(data: Mapping[str, Any]) -> Playbook:
    """YAML에서 읽은 dict를 검증해 `Playbook`을 만든다.

    실패는 `pydantic.ValidationError`(ValueError 하위)다.
    """
    return Playbook.model_validate(dict(data))


@dataclass(frozen=True)
class AlarmFacts:
    """플레이북 선택 입력 — 알람에서 결정적으로 뽑은 사실.

    resource_type: 알람 resourceType.
    alarm_kind:    `classify_alarm_kind(resource_type, alarm_name)` 결과.
    kinds:         정규화 kind 목록(`was_signals` kind · 이벤트 유형 정규화 값 ·
                   디스크 FS/IO 구분 등).
    """

    resource_type: str | None = None
    alarm_kind: str | None = None
    kinds: tuple[str, ...] = ()


def _norm(value: str | None) -> str:
    return re.sub(r"\s+", "", (value or "")).lower()


def select_playbook(
    facts: AlarmFacts, playbooks: Sequence[Playbook]
) -> tuple[Playbook | None, str]:
    """알람에 맞는 플레이북을 결정적으로 고른다.

    fallback이 아닌 플레이북을 (priority, 목록 순서)로 정렬해 첫 일치를 고른다. 반환 `matched_by`는
    `"match:<조건>+<조건>"`. 일치가 없으면 fallback과 `"fallback"`,
    fallback도 없으면 `(None, "none")`.
    """
    ordered = sorted(
        (pb for pb in playbooks if not pb.is_fallback),
        key=lambda pb: pb.match.priority,
    )
    for pb in ordered:
        hit = pb.match.matches(facts)
        if hit is not None:
            return pb, "match:" + "+".join(hit)
    for pb in playbooks:
        if pb.is_fallback:
            return pb, "fallback"
    return None, "none"
