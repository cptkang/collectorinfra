"""S9 결정적 가설 순위 규칙표 · 대응 순서 선택 (plans/187 §4.2 마지막 문단 · §5.4 · §4.1 · D-341
③④).

판독 enum(R-A~R-H)과 보조 사실(게이트웨이 판정 인용)의 조합으로 원인 가설과 순위를 정한다. LLM은
이 순위를 바꾸지 않고 서술만 한다(G-5 · D-035).

규칙 1행 = `trigger`(이 가설을 후보로 세우는 1차 신호 — 참이어야 목록에 오른다) + `conditions`
(판별 조건). 평가는 `playbook_expr` 3값이다.

- 상태: 조건 전부 참 = 지지(supported) · 하나라도 거짓 = 반박(refuted) · 그 밖(결손) = 판단 보류
  (inconclusive — Datadog Bits AI inconclusive와 같은 뜻).
- 순위: (상태 지지 < 판단 보류 < 반박, 규칙표 순서). 규칙표 순서는 「여러 판독을 엮은 구체 원인 →
  단일 신호 원인」이다.
- 문맥: 판독 이름(`Readings.context()` — `db_split`의 `n/a`는 결손) + 스텝 ID 아래 `verdict`·보조
사실
  (`S2.pool`·`S2.gc`·`S7.change`). 스텝 ID는 §5.3 스텝 라이브러리 관례(S1 골든 시그널 · S2 런타임·풀
  · S7 변경)를 따른다.

대응 순서(`select_actions`)는 플레이북 `response_hints` 키와 런북 `mitigation[].when` 평가로만
고른다.

순수 도메인 — 표준 라이브러리와 같은 domain 패키지만 의존한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from sre_agent.domain.playbook import MitigationItem
from sre_agent.domain.playbook_expr import Expr, Tri, compile_expr, eval_all
from sre_agent.domain.playbook_readings import Readings

SUPPORTED = "supported"
REFUTED = "refuted"
INCONCLUSIVE = "inconclusive"

#: 상태 표시어(§5.4 [제안] 어휘).
STATUS_LABELS = {SUPPORTED: "지지", INCONCLUSIVE: "판단 보류", REFUTED: "반박"}
_STATUS_ORDER = {SUPPORTED: 0, INCONCLUSIVE: 1, REFUTED: 2}

#: 대응 순서 맨 앞에 두는 응답 힌트 키(SRE Triage — 완화 먼저 · §4.1 was.latency 대응 제안).
TRIAGE_HINT = "triage_first"


@dataclass(frozen=True)
class HypothesisRule:
    """가설 규칙 1행.

    trigger:        후보로 세우는 1차 신호(참일 때만 목록에 오른다).
    conditions:     판별 조건(전부 참 = 지지 · 하나라도 거짓 = 반박 · 그 밖 = 판단 보류).
    evidence_steps: 근거 스텝 ID(§5.3 관례 — 실행에 없는 스텝은 호출자가 거른다).
    hints:          대응 제안 키(플레이북 `response_hints`).
    basis:          근거 절.
    """

    id: str
    title: str
    trigger: str
    conditions: tuple[str, ...] = ()
    evidence_steps: tuple[str, ...] = ()
    hints: tuple[str, ...] = ()
    basis: str = ""


#: 가설 규칙표(순서 = 같은 상태 안의 순위). §4.2 마지막 문단 예 2건 · §5.4 예 1건 · §4.1 was.latency
#: 「흔한 원인」 열(DB 슬로우 쿼리·락 · 커넥션 획득 지연 · 외부 호출 · GC 과다 · 배포 회귀 · 트래픽
#: 급증)
#: + R-E·R-F·R-G 판독 원인.
HYPOTHESIS_RULES: tuple[HypothesisRule, ...] = (
    HypothesisRule(
        "H-DB-POOL-CHAIN",
        "DB 지연 → 풀 고갈 → 응답 지연",
        "dominant == 'sql'",
        ("S2.pool == 'exhausted'",),
        ("S3", "S2"),
        ("sql_dominant", "connection_acquire"),
        "§5.4 예(S3 SQL 지배 ∧ S2 풀 고갈)",
    ),
    HypothesisRule(
        "H-POOL-INSTANCE",
        "인스턴스 커넥션 풀 설정·점유",
        "db_split == 'acquire' or S2.pool == 'exhausted'",
        ("db_split == 'acquire'", "scope == 'instance'"),
        ("S3", "S2", "S0"),
        ("connection_acquire",),
        "§4.2 예(db_split=acquire ∧ scope=instance) · §4.1 커넥션 획득 지연",
    ),
    HypothesisRule(
        "H-DB-SHARED",
        "업무 DB 지연(공통 의존)",
        "dominant == 'sql'",
        ("db_split == 'execute'", "scope == 'shared_dependency'"),
        ("S3", "S0"),
        ("sql_dominant",),
        "§4.2 예(dominant=sql ∧ db_split=execute ∧ scope=shared_dependency)",
    ),
    HypothesisRule(
        "H-DB-INSTANCE",
        "인스턴스 SQL 지연(슬로우 쿼리·락)",
        "dominant == 'sql'",
        ("db_split == 'execute'", "scope != 'shared_dependency'"),
        ("S3", "S0"),
        ("sql_dominant",),
        "§4.1 DB 슬로우 쿼리·락",
    ),
    HypothesisRule(
        "H-EXT-SHARED",
        "외부 연계 지연(공통 의존)",
        "dominant == 'external'",
        ("scope == 'shared_dependency'",),
        ("S3", "S0"),
        ("external_dominant",),
        "§4.1 외부 호출 지연(제니퍼 LDAP 사례)",
    ),
    HypothesisRule(
        "H-EXT-INSTANCE-SINGLE",
        "외부 연계 지연(인스턴스 · 단일 서비스)",
        "dominant == 'external'",
        ("scope != 'shared_dependency'", "service_spread == 'single'"),
        ("S3", "S0"),
        ("external_dominant",),
        "§4.1 외부 호출 지연 · R-A 서비스 전용 자원",
    ),
    HypothesisRule(
        "H-EXT-INSTANCE-MANY",
        "외부 연계 지연(인스턴스 · 여러 서비스)",
        "dominant == 'external'",
        ("scope != 'shared_dependency'", "service_spread == 'many'"),
        ("S3", "S0"),
        ("external_dominant",),
        "§4.1 외부 호출 지연 · R-A 공유 자원",
    ),
    HypothesisRule(
        "H-GC-LEAK",
        "JVM 힙 누수 의심 → GC 과다 → 응답 지연",
        "heap_shape == 'leak_suspect'",
        ("S2.gc == 'pressure'", "S1.verdict == 'degraded'"),
        ("S2", "S1"),
        ("gc_dominant",),
        "§4.1 GC 과다 · R-F 누수 의심",
    ),
    HypothesisRule(
        "H-HEAP-PERIODIC",
        "주기성 적재(주기적 힙 급증)",
        "heap_shape == 'periodic'",
        ("S2.gc == 'pressure'",),
        ("S2",),
        ("gc_dominant",),
        "R-F 주기성 적재(제니퍼 OOM 사례)",
    ),
    HypothesisRule(
        "H-HEAP-UNDERSIZED",
        "힙 과소(트래픽 비례 적재)",
        "heap_shape == 'load_proportional'",
        ("load_mode == 'load_increase'",),
        ("S2", "S1"),
        ("gc_dominant",),
        "R-F 힙 과소",
    ),
    HypothesisRule(
        "H-CPU-LOGIC-CHANGE",
        "특정 서비스 로직 CPU 과다(배포 근접 — 상관 후보)",
        "cpu_owner == 'was_logic'",
        ("S7.change == 'near'",),
        ("S2", "S4", "S6", "S3", "S7"),
        ("recent_change",),
        "R-E 특정 서비스 로직(제니퍼 CPU 사례) · R-H 변경 근접",
    ),
    HypothesisRule(
        "H-CPU-LOGIC",
        "특정 서비스 로직 CPU 과다",
        "cpu_owner == 'was_logic'",
        ("S7.change != 'near'",),
        ("S2", "S4", "S6", "S3"),
        (),
        "R-E 특정 서비스 로직",
    ),
    HypothesisRule(
        "H-CPU-LOAD",
        "트래픽 증가에 따른 WAS CPU 부하",
        "cpu_owner == 'was_load'",
        ("load_mode == 'load_increase'",),
        ("S2", "S4", "S1"),
        (),
        "R-E WAS 부하 · §4.1 트래픽 급증",
    ),
    HypothesisRule(
        "H-CPU-OTHER",
        "WAS 밖 프로세스 CPU 점유",
        "cpu_owner == 'other_process'",
        (),
        ("S4", "S2", "S6"),
        (),
        "R-E WAS 밖 프로세스(제니퍼 외부 호출 사례)",
    ),
    HypothesisRule(
        "H-LOAD",
        "트래픽 급증(부하 증가형)",
        "load_mode == 'load_increase'",
        (),
        ("S1",),
        (),
        "§4.1 트래픽 급증 · R-B 부하 증가형",
    ),
    HypothesisRule(
        "H-HOST-CPU",
        "호스트 CPU 포화",
        "host_saturation == 'cpu'",
        (),
        ("S4", "S6"),
        (),
        "R-G CPU 포화",
    ),
    HypothesisRule(
        "H-HOST-MEMORY",
        "호스트 메모리 포화",
        "host_saturation == 'memory'",
        (),
        ("S4",),
        (),
        "R-G 메모리 포화",
    ),
    HypothesisRule(
        "H-HOST-FS",
        "파일시스템 포화",
        "host_saturation == 'fs'",
        (),
        ("S4",),
        (),
        "R-G 파일시스템 포화",
    ),
    HypothesisRule(
        "H-HOST-SCOPE",
        "호스트 자원 문제(같은 호스트 전 인스턴스 지연)",
        "scope == 'host'",
        (),
        ("S0", "S4"),
        (),
        "R-A 호스트 자원",
    ),
    HypothesisRule(
        "H-CHANGE",
        "최근 변경 근접(배포 회귀 — 상관 후보)",
        "S7.change == 'near'",
        ("S1.verdict == 'degraded'",),
        ("S7", "S1"),
        ("recent_change",),
        "§4.1 배포 회귀 · R-H(상관 ≠ 인과)",
    ),
)


@dataclass(frozen=True)
class _Compiled:
    rule: HypothesisRule
    trigger: Expr
    conditions: tuple[Expr, ...]


def compile_rules(rules: Sequence[HypothesisRule]) -> tuple[_Compiled, ...]:
    """규칙표를 컴파일한다(구문 오류는 `ExprSyntaxError`)."""
    return tuple(
        _Compiled(r, compile_expr(r.trigger), tuple(compile_expr(c) for c in r.conditions))
        for r in rules
    )


_DEFAULT_COMPILED = compile_rules(HYPOTHESIS_RULES)


@dataclass(frozen=True)
class Hypothesis:
    """가설 1건 — 순위·상태는 코드값이다(LLM이 바꾸지 않는다)."""

    rank: int
    rule_id: str
    title: str
    status: str
    evidence_steps: tuple[str, ...]
    met: tuple[str, ...] = ()
    unmet: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    hints: tuple[str, ...] = ()

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "title": self.title,
            "status": self.status,
            "status_label": self.status_label,
            "evidence_steps": list(self.evidence_steps),
            "rule_id": self.rule_id,
            "conditions": {
                "met": list(self.met),
                "unmet": list(self.unmet),
                "unknown": list(self.unknown),
            },
        }


def hypothesis_context(
    readings: Readings, step_verdicts: Mapping[str, str | None]
) -> dict[str, Any]:
    """가설·런북 `when` 평가 문맥 — 판독 값 + 스텝 ID 아래 `verdict`·보조 사실."""
    ctx: dict[str, Any] = {sid: {"verdict": v} for sid, v in step_verdicts.items()}
    for sid, facts in readings.facts.items():
        ctx[sid] = {**ctx.get(sid, {}), **facts}
    ctx.update(readings.context())
    return ctx


def rank_hypotheses(
    context: Mapping[str, Any],
    *,
    present_steps: Sequence[str] | None = None,
    rules: Sequence[HypothesisRule] | None = None,
) -> list[Hypothesis]:
    """규칙표를 평가해 가설 순위를 낸다. `present_steps`가 있으면 근거 스텝을 그 안으로 거른다."""
    compiled = _DEFAULT_COMPILED if rules is None else compile_rules(rules)
    present = set(present_steps) if present_steps is not None else None
    found: list[tuple[int, Hypothesis]] = []
    for priority, c in enumerate(compiled):
        if c.trigger.evaluate(context) is not Tri.TRUE:
            continue
        verdicts = [(e.source, e.evaluate(context)) for e in c.conditions]
        overall = eval_all(c.conditions, context)
        status = {Tri.TRUE: SUPPORTED, Tri.FALSE: REFUTED}.get(overall, INCONCLUSIVE)
        steps = tuple(s for s in c.rule.evidence_steps if present is None or s in present)
        found.append(
            (
                priority,
                Hypothesis(
                    rank=0,
                    rule_id=c.rule.id,
                    title=c.rule.title,
                    status=status,
                    evidence_steps=steps,
                    met=tuple(s for s, t in verdicts if t is Tri.TRUE),
                    unmet=tuple(s for s, t in verdicts if t is Tri.FALSE),
                    unknown=tuple(s for s, t in verdicts if t is Tri.UNKNOWN),
                    hints=c.rule.hints,
                ),
            )
        )
    found.sort(key=lambda ph: (_STATUS_ORDER[ph[1].status], ph[0]))
    return [replace(h, rank=i) for i, (_, h) in enumerate(found, start=1)]


def pin_hypotheses(
    pinned: Sequence[Hypothesis], hypotheses: Sequence[Hypothesis]
) -> list[Hypothesis]:
    """고정 가설(validated 알려진 오류 완전 일치 · W8)을 맨 앞에 두고 순위를 다시 매긴다.

    규칙표 가설의 상대 순서는 바꾸지 않는다. `pinned`가 비면 입력과 같은 순위다.
    """
    if not pinned:
        return list(hypotheses)
    ordered = [*pinned, *hypotheses]
    return [replace(h, rank=i) for i, h in enumerate(ordered, start=1)]


def is_top_inconclusive(hypotheses: Sequence[Hypothesis], *, alarm_degraded: bool = False) -> bool:
    """1순위 가설이 「판단 보류」인가(W4 `conditional` 심층 조사 조건 — §5.0 G-22).

    가설이 하나도 없으면 `alarm_degraded`(대상 판정이 지연인데 설명할 가설이 없음 = 원인 미정)를
    따른다.
    """
    if not hypotheses:
        return alarm_degraded
    return hypotheses[0].status == INCONCLUSIVE


@dataclass
class Action:
    """대응 제안 1건(실행 아님 · 사람 승인)."""

    order: int
    source: str  # known_error | response_hint | runbook
    text: str
    key: str | None = None
    hypothesis: str | None = None
    when: str | None = None
    risk: str | None = None
    verification: str | None = None
    rollback: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if v is not None and k != "extra"}
        out.update(self.extra)
        return out


def select_actions(
    hypotheses: Sequence[Hypothesis],
    response_hints: Mapping[str, str],
    mitigations: Sequence[MitigationItem],
    context: Mapping[str, Any],
    *,
    alarm_degraded: bool = False,
    known_error_actions: Sequence[Action] = (),
) -> list[Action]:
    """대응 순서(결정적): ⓪ validated 알려진 오류 일치의 우회(`known_error_actions` · W8 —
    KE 우회 > 런북 완화 > 일반 조치 후보 §5.7.3) ① `triage_first` 힌트(반박 아닌 가설이 있거나 대상
    판정이 지연일 때만) ② 런북 mitigation 중 `when`이 참(없으면 무조건)인 항목(YAML 순서) ③ 반박이
    아닌 가설 순위대로 그 가설의 힌트 키(중복 제외)."""
    actions: list[Action] = list(known_error_actions)
    seen: set[str] = set()
    live = any(h.status != REFUTED for h in hypotheses)
    if TRIAGE_HINT in response_hints and (live or alarm_degraded):
        actions.append(Action(0, "response_hint", response_hints[TRIAGE_HINT], key=TRIAGE_HINT))
        seen.add(TRIAGE_HINT)
    for m in mitigations:
        verdict = m.when_expr.evaluate(context) if m.when_expr is not None else Tri.TRUE
        if verdict is Tri.TRUE:
            actions.append(
                Action(
                    0,
                    "runbook",
                    m.action,
                    when=m.when,
                    risk=m.risk,
                    verification=m.verification,
                    rollback=m.rollback,
                )
            )
    for h in hypotheses:
        if h.status == REFUTED:
            continue
        for key in h.hints:
            if key in response_hints and key not in seen:
                seen.add(key)
                actions.append(
                    Action(0, "response_hint", response_hints[key], key=key, hypothesis=h.rule_id)
                )
    for i, a in enumerate(actions, start=1):
        a.order = i
    return actions


def build_followups(hypotheses: Sequence[Hypothesis], readings: Readings) -> list[str]:
    """추가 확인 필요 항목 — 판단 보류 가설의 결손 조건(판독 사유 인용). 중복 제외 · 순위 순서."""
    out: list[str] = []
    for h in hypotheses:
        if h.status != INCONCLUSIVE:
            continue
        for cond in h.unknown:
            path = cond.split()[0]
            ev = readings.evidence.get(path) or readings.fact_evidence.get(path)
            why = f" — {ev.reason}" if ev is not None and ev.reason else ""
            line = f"{h.title}: `{cond}` 판정 불가{why}"
            if line not in out:
                out.append(line)
    return out


__all__ = [
    "Action",
    "HYPOTHESIS_RULES",
    "Hypothesis",
    "HypothesisRule",
    "INCONCLUSIVE",
    "REFUTED",
    "STATUS_LABELS",
    "SUPPORTED",
    "TRIAGE_HINT",
    "build_followups",
    "compile_rules",
    "hypothesis_context",
    "is_top_inconclusive",
    "pin_hypotheses",
    "rank_hypotheses",
    "select_actions",
]
