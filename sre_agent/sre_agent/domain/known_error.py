"""장애 대응 지식 읽기 측 — 알려진 오류(KEDB) 스키마 · 결정적 일치 판정 · 런북 브리핑
(plans/187 W8 §5.7.1~5.7.3 · §5.7.6 · D-341 ⑨⑩).

알려진 오류 레코드는 KCS 구조(issue · environment · cause · cause_test · workaround)의 선언형 YAML
(`sre_agent/config/known_errors/*.yaml`)이다. 이 모듈은 그 dict를 pydantic으로 검증하고
(`parse_known_error`), 플레이북 실행 결과(판정 · 판독 enum · 스코프 · 알람 시각)만으로 일치를
판정한다(`evaluate_known_error`). 자유 텍스트·임베딩 유사도는 쓰지 않는다(D-035).

- 판정: `environment` 충족 ∧ `match` 충족 → 후보(candidate). 여기에 `cause_test`까지 충족 → 일치
  (match). 어느 단계든 거짓이면 불일치(no_match — environment·match) 또는 후보에 머문다
  (cause_test 거짓). 결손(UNKNOWN)이 끼면 「판단 보류」(inconclusive)와 결손 스텝 —
  침묵 불일치 금지.
- 상태: validated = 표시 + (완전 일치만) 가설 1순위 고정 · not_validated = 「미검증 후보」 배지로만
  표시(순위 영향 0) · wip·archived = 표시 안 함(평가도 하지 않는다).
- 쓰기 지점 차단(§5.7.5 2): `state: validated` 레코드에 `owner`·`last_reviewed`가 없으면 거부한다.
- 우회(workaround)는 `remediation.py` 규칙 그대로의 「제안」이다 — 명령 리터럴 금지 · 위험도 ·
  검증 · 롤백 필수 · 고위험 × 저신뢰 = 「검토 필요」.

**순수 도메인 모듈** — 파일 IO는 `infrastructure/known_error_loader.py`가 한다. 외부 의존은
pydantic뿐이다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from fnmatch import fnmatchcase
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StringConstraints,
    model_validator,
)

from sre_agent.domain.playbook import RunbookBlock
from sre_agent.domain.playbook_expr import (
    READING_NAMES,
    STEP_ID_PATTERN,
    Expr,
    ListCondition,
    Tri,
    compile_list_condition,
)
from sre_agent.domain.playbook_hypotheses import SUPPORTED, Hypothesis
from sre_agent.domain.remediation import Remediation, find_command_literal, is_review_only

STATE_WIP = "wip"
STATE_NOT_VALIDATED = "not_validated"
STATE_VALIDATED = "validated"
STATE_ARCHIVED = "archived"

#: 브리핑에 보이는 상태(§5.7.3 — wip는 관리자 화면만 · archived는 이력 열람만).
DISPLAY_STATES: frozenset[str] = frozenset({STATE_VALIDATED, STATE_NOT_VALIDATED})

RESULT_MATCH = "match"
RESULT_CANDIDATE = "candidate"
RESULT_INCONCLUSIVE = "inconclusive"
RESULT_NO_MATCH = "no_match"

#: 결과 표시어(§5.7.3 · §5.7.6). no_match는 브리핑에 싣지 않는다.
RESULT_LABELS: dict[str, str] = {
    RESULT_MATCH: "알려진 문제 일치",
    RESULT_CANDIDATE: "알려진 문제 후보",
    RESULT_INCONCLUSIVE: "판단 보류",
    RESULT_NO_MATCH: "불일치",
}
#: not_validated 레코드의 배지(순위 미반영 — 표시만).
NOT_VALIDATED_BADGE = "미검증 후보"
#: 가설 1순위 고정 시 규칙 ID 접두(`rule_id: KE:<id>`).
KE_RULE_PREFIX = "KE:"
#: 알려진 오류 검토 주기 기본값(D-341 ⑩ [제안] — 런북 180일 · KE 90일).
DEFAULT_KE_REVIEW_DAYS = 90

_KE_ID_PATTERN = r"^KE-[A-Z0-9][A-Z0-9-]*$"
_PLAYBOOK_ID_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$"
_HOURS_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")
_WEEKDAY_RANGE_RE = re.compile(r"^([1-7])-([1-7])$")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_ROOT_NAME = re.compile(r"(?<![.\w])([A-Za-z_]\w*)")

_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

_STRICT = ConfigDict(extra="forbid", frozen=True)


class WorkaroundItem(BaseModel):
    """우회 제안 1건 — 실행 명령이 아니다(remediation.py 형식 · 명령 리터럴 금지)."""

    model_config = _STRICT

    action: _Text
    risk: Literal["low", "medium", "high"]
    verification: _Text
    rollback: _Text

    @model_validator(mode="after")
    def _no_command_literal(self) -> WorkaroundItem:
        for name in ("action", "verification", "rollback"):
            hit = find_command_literal(getattr(self, name))
            if hit is not None:
                raise ValueError(f"우회 {name}에 변경 명령 형태 금지({hit}) — 조치 서술로 쓴다")
        return self


class TargetSpec(BaseModel):
    """대상 조건 — 값을 둔 칸만 평가한다(AND).

    solution:      알람 `meta.hints.solution` 값 중 하나(대소문자 무시 동등 비교).
    instance_glob: 알람 대상 이름(APM 인스턴스 · 없으면 호스트)의 `fnmatch` 패턴 중 하나
                   (대소문자 구분).
    """

    model_config = _STRICT

    solution: list[_Text] = Field(default_factory=list)
    instance_glob: list[_Text] = Field(default_factory=list)


class TimeWindowSpec(BaseModel):
    """알람 시각 기준 시간대 조건 — 값을 둔 칸만 평가한다(AND).

    weekday: ISO 요일(1=월 … 7=일) 정수 또는 `"1-5"` 범위 문자열
             (YAML `[1-5]`는 문자열 1개로 읽힌다).
    hours:   `"HH:MM-HH:MM"`(시작 포함 · 끝 제외 · 끝 ≤ 시작이면 자정을 넘는 창) 중 하나.
    """

    model_config = _STRICT

    weekday: list[int | str] = Field(default_factory=list)
    hours: list[_Text] = Field(default_factory=list)

    _days: frozenset[int] = PrivateAttr(default=frozenset())
    _spans: tuple[tuple[int, int], ...] = PrivateAttr(default=())

    @model_validator(mode="after")
    def _compile(self) -> TimeWindowSpec:
        days: set[int] = set()
        for item in self.weekday:
            if isinstance(item, int) and not isinstance(item, bool) and 1 <= item <= 7:
                days.add(item)
                continue
            m = _WEEKDAY_RANGE_RE.match(str(item).strip()) if isinstance(item, str) else None
            if m is None or int(m.group(1)) > int(m.group(2)):
                raise ValueError(f"요일 {item!r} — 1~7 정수 또는 'a-b'(a ≤ b) 범위")
            days.update(range(int(m.group(1)), int(m.group(2)) + 1))
        spans: list[tuple[int, int]] = []
        for text in self.hours:
            m = _HOURS_RE.match(text)
            if m is None:
                raise ValueError(f"시간대 {text!r} — 'HH:MM-HH:MM'")
            h1, m1, h2, m2 = (int(g) for g in m.groups())
            spans.append((h1 * 60 + m1, h2 * 60 + m2))
        self._days = frozenset(days)
        self._spans = tuple(spans)
        return self

    def weekday_ok(self, when: datetime) -> bool:
        """알람 시각의 ISO 요일이 조건 안인가."""
        return when.isoweekday() in self._days

    def hours_ok(self, when: datetime) -> bool:
        """알람 시각(분 단위)이 시간대 중 하나 안인가."""
        minute = when.hour * 60 + when.minute
        for start, end in self._spans:
            if start < end and start <= minute < end:
                return True
            if start >= end and (minute >= start or minute < end):
                return True
        return False


class EnvironmentSpec(BaseModel):
    """해결 후에도 참인 조건(KCS Environment) — 같은 증상·다른 원인을 가르는 칸."""

    model_config = _STRICT

    playbook: str = Field(pattern=_PLAYBOOK_ID_PATTERN)
    targets: TargetSpec = Field(default_factory=TargetSpec)
    time_window: TimeWindowSpec | None = None


class PermanentFix(BaseModel):
    """영구 해결 추적(ITIL — 영구 해결은 변경 관리 · 해결되면 사람이 archived로 옮긴다)."""

    model_config = _STRICT

    status: _Text
    problem_ref: _Text | None = None


class KnownError(BaseModel):
    """알려진 오류 레코드 1건(§5.7.3)."""

    model_config = _STRICT

    id: str = Field(pattern=_KE_ID_PATTERN)
    title: _Text
    state: Literal["wip", "not_validated", "validated", "archived"]
    governance: Literal["experience", "compliance"] = "experience"
    issue: _Text
    environment: EnvironmentSpec
    match: dict[str, Any]
    cause: _Text
    cause_test: dict[str, Any]
    workaround: list[WorkaroundItem] = Field(default_factory=list)
    permanent_fix: PermanentFix | None = None
    owner: _Text | None = None
    last_reviewed: date | None = None
    review_every_days: int = Field(default=DEFAULT_KE_REVIEW_DAYS, gt=0)

    _match: ListCondition = PrivateAttr()
    _cause_test: ListCondition = PrivateAttr()

    @model_validator(mode="after")
    def _check(self) -> KnownError:
        # ExprSyntaxError는 ValueError 하위 — 검증 실패로 그 레코드를 거부한다.
        self._match = compile_list_condition(self.match)
        self._cause_test = compile_list_condition(self.cause_test)
        if self.state == STATE_VALIDATED:
            lacking = [k for k in ("owner", "last_reviewed") if getattr(self, k) is None]
            if lacking:
                raise ValueError(f"validated 레코드는 {lacking}가 필수다(쓰기 지점 차단)")
        return self

    @property
    def match_condition(self) -> ListCondition:
        """컴파일된 `match`."""
        return self._match

    @property
    def cause_condition(self) -> ListCondition:
        """컴파일된 `cause_test`."""
        return self._cause_test

    def is_stale(self, today: date) -> bool:
        """`last_reviewed + review_every_days`가 지났으면 참(검토일이 없으면 거짓)."""
        if self.last_reviewed is None:
            return False
        return today > self.last_reviewed + timedelta(days=self.review_every_days)


def parse_known_error(data: Mapping[str, Any]) -> KnownError:
    """YAML에서 읽은 dict를 검증해 `KnownError`를 만든다. 실패는 `pydantic.ValidationError`."""
    return KnownError.model_validate(dict(data))


# ── 일치 판정 ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class KnownErrorEvaluation:
    """일치 판정 결과 — 조건 문자열은 YAML 원문(또는 environment 표기) 그대로다."""

    result: str
    satisfied: tuple[str, ...] = ()
    unsatisfied: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    missing_steps: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "satisfied": list(self.satisfied),
            "unsatisfied": list(self.unsatisfied),
            "unknown": list(self.unknown),
            "missing_steps": list(self.missing_steps),
        }


def _kleene_and(values: Iterable[Tri]) -> Tri:
    vals = list(values)
    if Tri.FALSE in vals:
        return Tri.FALSE
    return Tri.UNKNOWN if Tri.UNKNOWN in vals else Tri.TRUE


def _parse_time(alarm_time: str | datetime | None) -> datetime | None:
    if isinstance(alarm_time, datetime):
        return alarm_time
    if not alarm_time:
        return None
    try:
        return datetime.fromisoformat(str(alarm_time))
    except ValueError:
        return None


def _environment_checks(
    env: EnvironmentSpec, scope: Mapping[str, Any], alarm_time: str | datetime | None
) -> list[tuple[str, Tri]]:
    checks: list[tuple[str, Tri]] = []
    if env.targets.solution:
        label = f"environment.targets.solution in {list(env.targets.solution)}"
        sol = str(scope.get("solution") or "").strip()
        if not sol:
            checks.append((label, Tri.UNKNOWN))
        else:
            wanted = {s.casefold() for s in env.targets.solution}
            checks.append((label, Tri.TRUE if sol.casefold() in wanted else Tri.FALSE))
    if env.targets.instance_glob:
        label = f"environment.targets.instance_glob {list(env.targets.instance_glob)}"
        inst = str(scope.get("instance") or "").strip()
        if not inst:
            checks.append((label, Tri.UNKNOWN))
        else:
            hit = any(fnmatchcase(inst, g) for g in env.targets.instance_glob)
            checks.append((label, Tri.TRUE if hit else Tri.FALSE))
    tw = env.time_window
    if tw is not None:
        when = _parse_time(alarm_time)
        if tw.weekday:
            label = f"environment.time_window.weekday {list(tw.weekday)}"
            ok = None if when is None else tw.weekday_ok(when)
            checks.append((label, Tri.UNKNOWN if ok is None else (Tri.TRUE if ok else Tri.FALSE)))
        if tw.hours:
            label = f"environment.time_window.hours {list(tw.hours)}"
            ok = None if when is None else tw.hours_ok(when)
            checks.append((label, Tri.UNKNOWN if ok is None else (Tri.TRUE if ok else Tri.FALSE)))
    return checks


def _expr_items(cond: ListCondition) -> tuple[Expr, ...]:
    return (*cond.all_of, *cond.any_of)


def expr_steps(expr: Expr, reading_steps: Mapping[str, Sequence[str]] | None = None) -> list[str]:
    """식이 기대는 스텝 ID — 스텝 루트 + 판독 이름 루트(`reading_steps`로 담당 스텝에 잇는다)."""
    out: list[str] = []
    for root in _ROOT_NAME.findall(_QUOTED.sub("", expr.source)):
        if STEP_ID_PATTERN.match(root):
            steps: Sequence[str] = (root,)
        elif root in READING_NAMES:
            steps = (reading_steps or {}).get(root, ())
        else:
            continue
        out.extend(s for s in steps if s not in out)
    return out


def evaluate_known_error(
    ke: KnownError,
    *,
    playbook_id: str,
    scope: Mapping[str, Any],
    ctx: Mapping[str, Any],
    alarm_time: str | datetime | None,
    reading_steps: Mapping[str, Sequence[str]] | None = None,
) -> KnownErrorEvaluation:
    """알려진 오류 1건을 결정적으로 판정한다. 예외를 내지 않는다.

    scope:         `{"instance": 알람 대상 이름, "solution": 알람 hints.solution}`(없으면 결손).
    ctx:           플레이북 평가 문맥(스텝 ID·판독 이름 → 값 · `playbook_expr`).
    alarm_time:    알람 시각(ISO · 시간대 조건 기준).
    reading_steps: 판독 이름 → 담당 스텝 ID(결손 스텝 표기용).
    """
    if ke.environment.playbook != playbook_id:
        return KnownErrorEvaluation(
            RESULT_NO_MATCH,
            unsatisfied=(f"environment.playbook == '{ke.environment.playbook}'",),
        )
    satisfied = [f"environment.playbook == '{ke.environment.playbook}'"]
    unsatisfied: list[str] = []
    unknown: list[str] = []
    missing: list[str] = []

    def record(label: str, tri: Tri, steps: Sequence[str] = ()) -> None:
        if tri is Tri.TRUE:
            satisfied.append(label)
        elif tri is Tri.FALSE:
            unsatisfied.append(label)
        else:
            unknown.append(label)
            missing.extend(s for s in steps if s not in missing)

    env_checks = _environment_checks(ke.environment, scope, alarm_time)
    for label, tri in env_checks:
        record(label, tri)
    env = _kleene_and(t for _, t in env_checks)
    for expr in _expr_items(ke.match_condition):
        record(expr.source, expr.evaluate(ctx), expr_steps(expr, reading_steps))
    matched = ke.match_condition.evaluate(ctx)
    if Tri.FALSE in (env, matched):
        return KnownErrorEvaluation(
            RESULT_NO_MATCH, tuple(satisfied), tuple(unsatisfied), tuple(unknown), tuple(missing)
        )
    for expr in _expr_items(ke.cause_condition):
        record(expr.source, expr.evaluate(ctx), expr_steps(expr, reading_steps))
    cause = ke.cause_condition.evaluate(ctx)
    if Tri.UNKNOWN in (env, matched) or cause is Tri.UNKNOWN:
        result = RESULT_INCONCLUSIVE
    elif cause is Tri.TRUE:
        result = RESULT_MATCH
    else:
        result = RESULT_CANDIDATE
    return KnownErrorEvaluation(
        result, tuple(satisfied), tuple(unsatisfied), tuple(unknown), tuple(missing)
    )


def evaluate_known_errors(
    known_errors: Sequence[KnownError],
    *,
    playbook_id: str,
    scope: Mapping[str, Any],
    ctx: Mapping[str, Any],
    alarm_time: str | datetime | None,
    reading_steps: Mapping[str, Sequence[str]] | None = None,
) -> list[tuple[KnownError, KnownErrorEvaluation]]:
    """표시 상태(validated·not_validated) 레코드만 판정해 불일치가 아닌 것을 목록 순서로 돌려준다.

    wip·archived는 평가하지 않는다(§5.7.3 — 브리핑에 보이지 않는다).
    """
    out: list[tuple[KnownError, KnownErrorEvaluation]] = []
    for ke in known_errors:
        if ke.state not in DISPLAY_STATES:
            continue
        ev = evaluate_known_error(
            ke, playbook_id=playbook_id, scope=scope, ctx=ctx, alarm_time=alarm_time,
            reading_steps=reading_steps,
        )
        if ev.result != RESULT_NO_MATCH:
            out.append((ke, ev))
    return out


def is_pinned(ke: KnownError, ev: KnownErrorEvaluation) -> bool:
    """가설 1순위 고정 대상인가 — validated ∧ 완전 일치만(D-341 ⑩)."""
    return ke.state == STATE_VALIDATED and ev.result == RESULT_MATCH


def pinned_hypothesis(
    ke: KnownError,
    ev: KnownErrorEvaluation,
    *,
    present_steps: Sequence[str] | None = None,
    reading_steps: Mapping[str, Sequence[str]] | None = None,
) -> Hypothesis:
    """validated 일치 레코드의 가설(지지 · 근거 = KE id · 충족 조건 · `rule_id: KE:<id>`)."""
    present = set(present_steps) if present_steps is not None else None
    steps: list[str] = []
    for expr in (*_expr_items(ke.match_condition), *_expr_items(ke.cause_condition)):
        steps.extend(
            s for s in expr_steps(expr, reading_steps)
            if s not in steps and (present is None or s in present)
        )
    return Hypothesis(
        rank=0,
        rule_id=f"{KE_RULE_PREFIX}{ke.id}",
        title=f"알려진 오류 {ke.id} 일치 — {ke.title}",
        status=SUPPORTED,
        evidence_steps=tuple(steps),
        met=ev.satisfied,
    )


# ── 브리핑 조각(§5.7.6) ────────────────────────────────────────────────────


def workaround_confidence(ke: KnownError, ev: KnownErrorEvaluation) -> str:
    """우회 신뢰도 — validated 완전 일치만 high, 나머지는 medium(고위험이면 「검토 필요」)."""
    return "high" if is_pinned(ke, ev) else "medium"


def workaround_items(ke: KnownError, ev: KnownErrorEvaluation) -> list[dict[str, Any]]:
    """우회 제안 dict 목록 — remediation 형식 한 줄(`line`)과 「검토 필요」 판정을 함께 싣는다."""
    confidence = workaround_confidence(ke, ev)
    out: list[dict[str, Any]] = []
    for w in ke.workaround:
        review_only = is_review_only(w.risk, confidence)
        line = Remediation(
            action=w.action,
            risk=w.risk,
            confidence=confidence,
            rationale=f"알려진 오류 {ke.id}({RESULT_LABELS[ev.result]})",
            review_only=review_only,
            verification=w.verification,
            rollback=w.rollback,
        ).to_line()
        out.append({
            "action": w.action,
            "risk": w.risk,
            "verification": w.verification,
            "rollback": w.rollback,
            "confidence": confidence,
            "review_only": review_only,
            "line": line,
        })
    return out


def known_error_briefing(ke: KnownError, ev: KnownErrorEvaluation, today: date) -> dict[str, Any]:
    """브리핑 `playbook.known_errors[]` 항목. `recurrence`는 본체가 덧붙인다(D-118)."""
    return {
        "id": ke.id,
        "title": ke.title,
        "state": ke.state,
        "badge": NOT_VALIDATED_BADGE if ke.state == STATE_NOT_VALIDATED else None,
        "result": ev.result,
        "result_label": RESULT_LABELS[ev.result],
        "pinned": is_pinned(ke, ev),
        "issue": ke.issue,
        "cause": ke.cause,
        "satisfied": list(ev.satisfied),
        "unsatisfied": list(ev.unsatisfied),
        "unknown": list(ev.unknown),
        "missing_steps": list(ev.missing_steps),
        "workaround": workaround_items(ke, ev),
        "permanent_fix": ke.permanent_fix.model_dump() if ke.permanent_fix else None,
        "owner": ke.owner,
        "last_reviewed": ke.last_reviewed.isoformat() if ke.last_reviewed else None,
        "review_every_days": ke.review_every_days,
        "stale": ke.is_stale(today),
    }


def runbook_briefing(runbook: RunbookBlock, today: date) -> dict[str, Any]:
    """브리핑 `playbook.runbook` — 낡아도 내용은 숨기지 않고 `stale`만 켠다(§5.7.2)."""
    return {
        "meaning": runbook.meaning,
        "impact": runbook.impact,
        "mitigation": [
            {
                "when": m.when,
                "action": m.action,
                "risk": m.risk,
                "verification": m.verification,
                "rollback": m.rollback,
            }
            for m in runbook.mitigation
        ],
        "escalation": runbook.escalation,
        "references": list(runbook.references),
        "owner": runbook.owner,
        "last_reviewed": runbook.last_reviewed.isoformat(),
        "review_every_days": runbook.review_every_days,
        "stale": runbook.is_stale(today),
    }


__all__ = [
    "DEFAULT_KE_REVIEW_DAYS",
    "DISPLAY_STATES",
    "KE_RULE_PREFIX",
    "KnownError",
    "KnownErrorEvaluation",
    "NOT_VALIDATED_BADGE",
    "RESULT_CANDIDATE",
    "RESULT_INCONCLUSIVE",
    "RESULT_LABELS",
    "RESULT_MATCH",
    "RESULT_NO_MATCH",
    "STATE_ARCHIVED",
    "STATE_NOT_VALIDATED",
    "STATE_VALIDATED",
    "STATE_WIP",
    "WorkaroundItem",
    "evaluate_known_error",
    "evaluate_known_errors",
    "expr_steps",
    "is_pinned",
    "known_error_briefing",
    "parse_known_error",
    "pinned_hypothesis",
    "runbook_briefing",
    "workaround_confidence",
    "workaround_items",
]
