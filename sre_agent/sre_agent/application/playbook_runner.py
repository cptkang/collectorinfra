"""플레이북 실행기 (plans/187 §5.1 · §5.3 · §5.5 · §4.0 · D-341).

알람 페이로드(잡) · 선택된 `Playbook` · 축별 배치 콜러블(폴스타 · APM — 주입) · 시계(주입) ·
예산으로
스텝을 결정적으로 실행해 `PlaybookRun`을 낸다. 수집 단계는 LLM을 부르지 않는다 — S9(종합)는 자리만
둔다(`status: pending_synthesis`). 실행 후 처리(`finalize_playbook_run` · W3)가 판독 → 가설 → 단계
소견
→ 종합으로 S9를 채운다(`run_playbook(..., synthesize=True | narrator=…)`로도 켠다 — 기본은 꺼짐).
dispatcher 배선은 W4다(이 모듈만으로는 기존 경로 무변경).

- 기준 시각 = 알람 시각(`alarm_time_to_iso(event.alarmTime)` — 잡 `reference_time`과 같은 계약).
  사건창 = 알람 −before ~ +after, 도구 기준 시각 = 사건창 끝, lookback = before + after.
  기준선 창 = 사건창을 하루·이레 앞으로 옮긴 창(자정·주 경계는 `timedelta`로 넘는다).
- 스텝은 YAML 순서대로 「단계(stage)」로 묶어 실행한다. 대상 확정 액션(S0·S0′)은 단독 단계이고,
  `when`이 같은 단계의 스텝을 참조하면 새 단계가 된다. 같은 단계의 스텝은 서로 독립이라
  스레드풀로 병렬 실행하며, 결과는 YAML 순서로 모은다(결정적).
- 판독(R-A~R-H)은 단계마다 다시 계산해 `when` 문맥에 넣는다(판독 이름 · 스텝 ID 아래 보조 사실).
- `when` 3값: TRUE 실행 · FALSE `skipped(when_false)` · UNKNOWN은 **실행**하고 사유를 남긴다.
- 스텝마다 개별 try/except · 스텝 타임아웃 · 결손은 `missing` + 사유 · 남은 예산이 없으면 이후 스텝
  `skipped(budget)` + 사유(침묵 금지).
- 도구 호출은 잡 단위 캐시(`ToolResultCache` · G-26)를 거친다. 캐시를 주입받지 않으면 실행기가
  만들고
  끝나면 버린다.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sre_agent.application.investigation_guidance import classify_alarm_kind
from sre_agent.application.playbook_narrator import (
    NOT_CONFIGURED,
    Opinion,
    PlaybookNarrator,
    StepNarration,
)
from sre_agent.application.playbook_steps import (
    ACTION_TITLES,
    SCOPE_ACTIONS,
    STEP_LIBRARY,
    BaselineWindow,
    RunScope,
    RunWindows,
    StepContext,
    StepFn,
    StepResult,
)
from sre_agent.domain.activity_log import ActivityLog, safe_append
from sre_agent.domain.correlation import correlate
from sre_agent.domain.cross_source_episode import episode_from_payload
from sre_agent.domain.incident_scope import alarm_time_to_iso, normalize_reference_time
from sre_agent.domain.investigation_limits import is_apm_trigger
from sre_agent.domain.known_error import (
    KE_RULE_PREFIX,
    KnownError,
    KnownErrorEvaluation,
    evaluate_known_errors,
    is_pinned,
    known_error_briefing,
    pinned_hypothesis,
    runbook_briefing,
    workaround_items,
)
from sre_agent.domain.playbook import AlarmFacts, Playbook, StepSpec, WindowSpec
from sre_agent.domain.playbook_expr import READING_NAMES, Tri
from sre_agent.domain.playbook_hypotheses import (
    REFUTED,
    Action,
    Hypothesis,
    build_followups,
    hypothesis_context,
    is_top_inconclusive,
    pin_hypotheses,
    rank_hypotheses,
    select_actions,
)
from sre_agent.domain.playbook_readings import (
    READING_ACTIONS,
    Readings,
    compute_readings,
    enabled_readings,
    reading_params,
)
from sre_agent.domain.step_findings import (
    BUDGET_SKIP,
    default_because,
    skip_because,
    summarize,
    when_because,
)
from sre_agent.infrastructure.tool_result_cache import BatchCaller, ToolResultCache

logger = logging.getLogger(__name__)

#: 플레이북 수집 예산(§8 수용 기준 6). 120 → 240(plans/187 W22 · D-341 부기 12 — 설정
#: `sre_playbook_budget_s` 기본값과 같다 · 조사 전체 600s 안).
DEFAULT_BUDGET_S = 240.0
#: 스텝 1개 대기 기본 상한. 30 → 90(plans/187 W22 · D-341 부기 12) — 도구 1건 상한(60s)보다 커야
#: 도구 하나가 늦어도 스텝이 결과를 받는다. 대상 확정 스텝은 YAML `timeout_s`(120s)를 쓴다.
DEFAULT_STEP_TIMEOUT_S = 90.0
DEFAULT_MAX_WORKERS = 4

_BASELINE_DAYS = {"prev_day": 1, "prev_week": 7}
_SYNTHESIZE = "synthesize"

# ── 알람 → AlarmFacts 정규화 ────────────────────────────────────────────────

#: 응답시간·지연 이벤트 유형(제니퍼 원문 — `was_signals`에 지연 kind가 없어 이벤트로 매칭한다 ·
#: §2.3).
LATENCY_EVENT_TYPES = frozenset({"TX_BAD_RESPONSE"})
_LATENCY_WORDS = (
    "응답시간",
    "응답 시간",
    "응답지연",
    "응답 지연",
    "response time",
    "response_time",
    "latency",
)
_DISK_IO_WORDS = (
    "disk_io",
    "disk io",
    "diskio",
    "i/o",
    "iops",
    "iowait",
    "io wait",
    "busy",
    "await",
)
_FS_WORDS = ("filesystem", "file system", "파일시스템", "volume", "inode", "mount", "용량")

#: 이벤트 유형(접두 `ERROR_`·`WARNING_` 제거 후 대문자) → 게이트웨이 시그니처 kind.
#: **리터럴 사본** — 출처 `apm_gateway/apm_gateway/adapters/jennifer/fields.py` `EVENT_TYPE_SIGNALS`
#: (kind 열만). sre_agent는 게이트웨이를 import하지 않는다(D-274 · 양방향 import 0) — 동기화는
#: `tests/test_plan187_w4_dispatch.py`가 두 파일의 리터럴을 대조해 지킨다.
GATEWAY_EVENT_SIGNAL_KINDS: dict[str, str] = {
    "SERVICE_QUEUING": "was_service_queuing",
    "PLC_REJECTED": "was_service_queuing",
    "HIGH_RATE_REJECT": "was_service_queuing",
    "JDBC_CONNECTION_FAIL": "was_db_pool_exhaustion",
    "DB_CONNECTION_FAIL": "was_db_pool_exhaustion",
    "DB_CONN_UNCLOSED": "was_db_pool_exhaustion",
    "MAYBE_GC_TIME_DELAY": "was_gc_stall",
    "OUTOFMEMORY": "was_heap_pressure",
    "JVM_HEAP_MEM_HIGH": "was_heap_pressure",
    "HTTP_IO_EXCEPTION": "was_external_call_delay",
    "HIGH_RATE_FAIL": "was_error_burst",
}


def normalize_event_type(raw: object) -> str:
    """이벤트 유형을 대문자로 바꾸고 `ERROR_`·`WARNING_` 접두를 뗀다.

    게이트웨이 `normalize_event_type`과 같은 규칙(사본)이다.
    """
    text = str(raw or "").strip().upper()
    for prefix in ("ERROR_", "WARNING_"):
        if text.startswith(prefix):
            return text[len(prefix) :]
    return text


def _gateway_signal_kinds(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """게이트웨이가 이 알람에 준 시그니처 kind — `meta.episode` 멤버 중 이 알람(`event.alarmId`)의
    `was_kinds`(송신 측 값 그대로). 없으면 빈 튜플."""
    alarm_id = str(_event(payload).get("alarmId") or "").strip()
    if not alarm_id:
        return ()
    episode = episode_from_payload(payload)
    member = episode.member(alarm_id) if episode is not None else None
    return tuple(k for k in (member.was_kinds if member is not None else ()) if k)


def _event(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    ev = payload.get("event") if isinstance(payload, Mapping) else None
    return ev if isinstance(ev, Mapping) else {}


def _hints(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    meta = payload.get("meta") if isinstance(payload, Mapping) else None
    hints = meta.get("hints") if isinstance(meta, Mapping) else None
    return hints if isinstance(hints, Mapping) else {}


def alarm_facts_from_payload(payload: Mapping[str, Any]) -> AlarmFacts:
    """알람 페이로드 → 플레이북 선택 입력(결정적).

    - `alarm_kind` = `classify_alarm_kind(resourceType, alarmName)`.
    - kinds: 이벤트 유형(`meta.hints.event_type` · 알람명)이 응답시간·지연이면
      `latency`·`response_time`.
      디스크 알람은 I/O 표면어면 `disk_io`, 파일시스템 표면어면 `filesystem`으로 가른다.
    - WAS 시그니처 kind(W4): 게이트웨이가 준 kind(`meta.episode` 멤버의 `was_kinds`)가
      있으면 그것을, 없으면 이벤트 유형(`meta.hints.event_type` → 알람명)을 게이트웨이
      매핑 사본(`GATEWAY_EVENT_SIGNAL_KINDS`)으로 정규화한 kind를 더한다.
    """
    event = _event(payload)
    resource_type = str(event.get("resourceType") or "") or None
    alarm_name = str(event.get("alarmName") or "") or None
    alarm_kind = classify_alarm_kind(resource_type, alarm_name)
    event_type = str(_hints(payload).get("event_type") or "").strip().upper()
    haystack = f"{event_type} {alarm_name or ''}".lower()
    kinds: list[str] = list(_gateway_signal_kinds(payload))
    if not kinds:
        for raw in (event_type, alarm_name):
            kind = GATEWAY_EVENT_SIGNAL_KINDS.get(normalize_event_type(raw))
            if kind:
                kinds.append(kind)
                break
    if (
        event_type in LATENCY_EVENT_TYPES
        or (alarm_name or "").strip().upper() in LATENCY_EVENT_TYPES
        or any(w in haystack for w in _LATENCY_WORDS)
    ):
        kinds += ["latency", "response_time"]
    if alarm_kind == "disk":
        if any(w in haystack for w in _DISK_IO_WORDS):
            kinds.append("disk_io")
        elif any(w in haystack for w in _FS_WORDS):
            kinds.append("filesystem")
    return AlarmFacts(resource_type=resource_type, alarm_kind=alarm_kind, kinds=tuple(kinds))


# ── 시간창 · 범위 ───────────────────────────────────────────────────────────


def compute_windows(alarm_time: str, spec: WindowSpec) -> RunWindows:
    """알람 시각과 창 명세로 사건창·도구 기준 시각·기준선 창(전일·전주 동시간)을 계산한다."""
    alarm = datetime.fromisoformat(alarm_time).replace(tzinfo=None)
    start = alarm - timedelta(minutes=spec.before_min)
    end = alarm + timedelta(minutes=spec.after_min)
    baselines = tuple(
        BaselineWindow(
            name=name,
            start=(start - timedelta(days=_BASELINE_DAYS[name])).isoformat(),
            end=(end - timedelta(days=_BASELINE_DAYS[name])).isoformat(),
            reference_time=(end - timedelta(days=_BASELINE_DAYS[name])).isoformat(),
        )
        for name in spec.baseline
    )
    return RunWindows(
        alarm_time=alarm.isoformat(),
        reference_time=end.isoformat(),
        lookback_minutes=max(1, spec.before_min + spec.after_min),
        incident_start=start.isoformat(),
        incident_end=end.isoformat(),
        baselines=baselines,
    )


#: 폴스타 소스를 정할 수 없는 사유(§8 위험 1 · plans/165 F3).
POLESTAR_BRIDGE_MISSING = (
    "APM 알람 페이로드에 폴스타 소스(meta.episode.infra_db_ids)가 없고 설정된 폴스타 소스도 없다 — "
    "APM 호스트 → 폴스타 서버 브리지 미구현(plans/165 F3)"
)


def resolve_polestar_source(
    payload: Mapping[str, Any],
    configured: str | None = None,
) -> tuple[str | None, str | None]:
    """폴스타 소스(db_id)와 미확정 사유. 결정적으로 정할 수 없으면 (None, 사유).

    APM 트리거: `meta.episode.infra_db_ids[0]` → 호출자가 명시한 `configured` → 미확정.
    폴스타 알람: `event.dbId`(또는 pull 진단 `db_id`) → `configured` → 미확정.
    """
    if is_apm_trigger(payload):
        episode = episode_from_payload(payload)
        if episode is not None and episode.infra_db_ids:
            return episode.infra_db_ids[0], None
        if configured:
            return configured, None
        return None, POLESTAR_BRIDGE_MISSING
    source = str(_event(payload).get("dbId") or payload.get("db_id") or "").strip()
    if source:
        return source, None
    if configured:
        return configured, None
    return None, "페이로드에 폴스타 소스(event.dbId) 없음"


#: 설정 매핑으로 폴스타 소스를 대체했을 때의 근거 문장(plans/187 W21 P6 — 결정적).
POLESTAR_MAPPED_BASIS = (
    "설정 매핑으로 대체({source_id}→{db_id}) — 존이 다른 알람이면 다른 서버를 조회할 수 있음"
)


def apm_source_id(payload: Mapping[str, Any]) -> str | None:
    """APM 알람의 소스 ID — 트리거 힌트 `meta.hints.source_id`(게이트웨이 `apm.source_id` 원문).

    APM 트리거가 아니거나 값이 없으면 None.
    """
    if not is_apm_trigger(payload):
        return None
    return str(_hints(payload).get("source_id") or "").strip() or None


def mapped_polestar_source(
    payload: Mapping[str, Any], mapping: Mapping[str, str] | None
) -> tuple[str | None, str | None]:
    """설정 매핑(APM 소스 ID → 폴스타 db_id)으로 정한 폴스타 소스와 근거 문장.

    APM 알람이 아니거나 소스 ID·매핑 항목이 없으면 (None, None). 페이로드의
    `meta.episode.infra_db_ids`가 우선인지는 `resolve_polestar_source`가 정한다(이 값은 대체값).
    """
    sid = apm_source_id(payload)
    # 키도 앞뒤 공백을 걷어 비교한다(대소문자는 그대로 — 소스 ID 규칙이 소문자 고정)
    table = {str(k).strip(): v for k, v in (mapping or {}).items()}
    db_id = str(table.get(sid) or "").strip() if sid else ""
    if not sid or not db_id:
        return None, None
    return db_id, POLESTAR_MAPPED_BASIS.format(source_id=sid, db_id=db_id)


def initial_scope(
    payload: Mapping[str, Any], polestar_source: str | None, reason: str | None
) -> RunScope:
    """S0 전의 범위 — 페이로드 사실만. APM 알람이면 알람 인스턴스 단독 대상."""
    event = _event(payload)
    apm = is_apm_trigger(payload)
    instance = str(event.get("resourceName") or "").strip() or None if apm else None
    host = (
        str(
            event.get("hostname") or event.get("serverName") or payload.get("server_name") or ""
        ).strip()
        or None
    )
    server = (
        host
        if apm
        else (str(event.get("serverName") or payload.get("server_name") or "").strip() or None)
    )
    return RunScope(
        alarm_instance=instance,
        host=host,
        polestar_source=polestar_source,
        polestar_server=server,
        polestar_reason=reason,
        alarm_name=str(event.get("alarmName") or "").strip() or None,
        targets=[
            {"instance": instance, "verdict": None, "verdict_source": None, "confidence": None}
        ]
        if instance
        else [],
    )


_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_ROOT_NAME = re.compile(r"(?<![.\w])([A-Za-z_]\w*)")


def _reading_refs(when: str | None) -> frozenset[str]:
    """`when`이 경로 루트로 참조하는 판독 이름(따옴표 문자열 · `S2.scope` 같은 하위 경로 제외)."""
    if not when:
        return frozenset()
    return frozenset(_ROOT_NAME.findall(_QUOTED.sub(" ", when))) & READING_NAMES


def plan_stages(playbook: Playbook) -> list[list[StepSpec]]:
    """실행 단계 — YAML 순서를 지키며 서로 독립인 연속 스텝을 한 단계로 묶는다(S9 제외).

    새 단계를 여는 조건: 대상 확정 액션(뒤 스텝 전부가 그 범위에 의존) · 직전 단계가 대상 확정 ·
    `when`이 현재 단계의 스텝을 참조 · `when`이 현재 단계 스텝이 담당하는 판독을 참조.
    """
    stages: list[list[StepSpec]] = []
    cur: list[StepSpec] = []
    for step in playbook.steps:
        if step.action == _SYNTHESIZE:
            continue
        refs = step.when_expr.step_refs() if step.when_expr is not None else frozenset()
        reading_homes = {a for r in _reading_refs(step.when) for a in READING_ACTIONS[r]}
        barrier = (
            step.action in SCOPE_ACTIONS
            or any(s.action in SCOPE_ACTIONS for s in cur)
            or bool(refs & {s.id for s in cur})
            or any(s.action in reading_homes for s in cur)
        )
        if cur and barrier:
            stages.append(cur)
            cur = []
        cur.append(step)
    if cur:
        stages.append(cur)
    return stages


def _when_context(
    results: Mapping[str, StepResult], readings: Readings | None = None
) -> dict[str, Any]:
    """`when` 평가 문맥 — 스텝 결과 + (있으면) 보조 사실(`S2.pool` 등)·판독 이름."""
    ctx: dict[str, Any] = {
        sid: {
            "status": r.status,
            "verdict": r.verdict,
            "verdict_source": r.verdict_source,
            "data": r.data,
        }
        for sid, r in results.items()
    }
    if readings is not None:
        for sid, facts in readings.facts.items():
            if sid in ctx:
                ctx[sid].update(facts)
        ctx.update(readings.context())
    return ctx


def _readings_of(playbook: Playbook, results: Mapping[str, StepResult]) -> Readings:
    """지금까지의 스텝 결과로 판독을 계산한다(플레이북의 판독 범위·임계 인자 반영)."""
    params, param_notes = reading_params(playbook)
    steps = {
        s.id: results[s.id].to_dict()
        for s in playbook.steps
        if s.id in results and s.action != _SYNTHESIZE
    }
    readings = compute_readings(steps, enabled=enabled_readings(playbook), params=params)
    readings.notes.extend(param_notes)
    return readings


# ── 결과 ────────────────────────────────────────────────────────────────────


@dataclass
class PlaybookRun:
    """플레이북 실행 결과.

    sections:     YAML 순서의 `StepResult`(S9는 `pending_synthesis`).
    tool_outputs: 실제 응답 원문(캐시 적중 포함 · 섹션 순서) — 중요도 판정 입력(G-25).
    tool_calls:   `{"step", "tool", "args"}` — 스텝이 요청한 호출(캐시 적중 포함).
    correlation:  S4 지표 + S5 알람·이벤트로 다시 계산한 `CorrelationResult.to_dict()`
                  (플레이북 모드에서 `job.correlation` 대체 — G-24 · W4 배선). 둘 다 없으면 None.
    readings:     수집 끝의 판독(R-A~R-H · 결정적).
    opinions:     단계 소견(스텝 ID → `Opinion` · S9는 종합 서술) — 실행 후 처리가 채운다.
    synthesis:    종합(가설 순위·대응·추가 확인·서술) — 실행 후 처리 전에는 None.
    runbook:      브리핑 `playbook.runbook`(W8 · §5.7.2) — 실행 후 처리 전에는 None.
    known_errors: 브리핑 `playbook.known_errors[]`(W8 · §5.7.6 — 표시 상태 · 불일치 제외).
    """

    playbook_id: str
    version: int
    matched_by: str
    scope: dict[str, Any]
    windows: dict[str, Any] | None
    sections: list[StepResult]
    timings: dict[str, Any]
    tool_outputs: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    correlation: dict[str, Any] | None = None
    readings: Readings | None = None
    opinions: dict[str, Opinion] = field(default_factory=dict)
    synthesis: dict[str, Any] | None = None
    runbook: dict[str, Any] | None = None
    known_errors: list[dict[str, Any]] = field(default_factory=list)

    def section(self, step_id: str) -> StepResult | None:
        return next((s for s in self.sections if s.step == step_id), None)

    def to_dict(self) -> dict[str, Any]:
        """전체(판독 원자료 `data` 포함) 직렬화."""
        return {
            "id": self.playbook_id,
            "version": self.version,
            "matched_by": self.matched_by,
            "scope": self.scope,
            "windows": self.windows,
            "sections": [s.to_dict() for s in self.sections],
            "timings": self.timings,
            "notes": list(self.notes),
            "correlation": self.correlation,
            "tool_calls": list(self.tool_calls),
            "readings": self.readings.to_dict() if self.readings is not None else None,
            "synthesis": self.synthesis,
        }

    def to_briefing_dict(self) -> dict[str, Any]:
        """§5.5 브리핑 `playbook` 키.

        실행 후 처리 전이면 소견(`opinion`)·종합(`synthesis`)은 빈 값이다.
        """

        def section(s: StepResult) -> dict[str, Any]:
            op = self.opinions.get(s.step)
            return {
                **s.to_dict(include_data=False),
                "opinion": op.text if op else "",
                "opinion_dropped_reason": op.dropped_reason if op else None,
                "opinion_guard": list(op.guard) if op else [],
            }

        return {
            "id": self.playbook_id,
            "version": self.version,
            "matched_by": self.matched_by,
            "scope": self.scope,
            "windows": self.windows,
            "readings": self.readings.to_dict() if self.readings is not None else None,
            "sections": [section(s) for s in self.sections],
            "synthesis": self.synthesis
            if self.synthesis is not None
            else {"hypotheses": [], "actions": [], "followups": []},
            "timings": self.timings,
            "notes": list(self.notes),
        }


# ── 실행 ────────────────────────────────────────────────────────────────────


def _resolve_alarm_time(payload: Mapping[str, Any], reference_time: str | None) -> str | None:
    for candidate in (
        reference_time,
        alarm_time_to_iso(_event(payload).get("alarmTime")),
        payload.get("reference_time") if isinstance(payload, Mapping) else None,
    ):
        if candidate:
            try:
                return normalize_reference_time(candidate)
            except ValueError:
                continue
    return None


def _join(*parts: str | None) -> str | None:
    text = " · ".join(p for p in parts if p)
    return text or None


def run_playbook(
    payload: Mapping[str, Any],
    playbook: Playbook,
    *,
    matched_by: str,
    polestar: BatchCaller | None,
    apm: BatchCaller | None,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = datetime.now,
    budget_s: float = DEFAULT_BUDGET_S,
    step_timeout_s: float = DEFAULT_STEP_TIMEOUT_S,
    max_workers: int = DEFAULT_MAX_WORKERS,
    for_each_max: int | None = None,
    polestar_source: str | None = None,
    polestar_source_basis: str | None = None,
    reference_time: str | None = None,
    cache: ToolResultCache | None = None,
    steps: Mapping[str, StepFn] | None = None,
    synthesize: bool = False,
    narrator: PlaybookNarrator | None = None,
    activity: ActivityLog | None = None,
    on_skip: Callable[[StepResult, str], None] | None = None,
    retry_timed_out: bool = False,
) -> PlaybookRun:
    """플레이북을 실행한다. 예외를 내지 않는다 — 실패는 섹션의 상태·사유로 남긴다.

    polestar·apm:    축별 배치 콜러블(None이면 그 축 결손).
    clock:           예산·소요 측정용 단조 시계(초). now: 실시간 배지의 수집 시각 대체값.
    budget_s:        전체 예산(기본 240s). 단계 시작 시 남은 예산이 없으면 이후 스텝
    skipped(budget).
    step_timeout_s:  스텝 1개 대기 기본 상한 — YAML `timeout_s`가 있으면 그 값(어느 쪽이든 남은
                     예산보다 길지 않고, 단계 제출 시각부터 잰다 · W21 P3). 타임아웃 스텝은
                     중단 신호(이후 새 호출 0)와 함께 missing + 진행 계수 사유(W21 P4).
    max_workers:     같은 단계 병렬 스레드 수(1이면 YAML 순서대로 하나씩 — 호출 순서 결정적).
    for_each_max:    `for_each: targets` 스텝이 도는 대상 수 상한(None = S0 대상 전원).
    polestar_source: 페이로드에 없을 때 쓸 폴스타 소스(호출자가 결정적으로 아는 경우만 — 없으면
    미확정).
    polestar_source_basis: `polestar_source`를 실제로 썼을 때(페이로드에 소스 없음) run notes와
                     S0·S4 사유에 남길 근거 문장(설정 매핑 대체 — W21 P6). 없으면 무동작.
    reference_time:  기준 시각 재정의(없으면 `event.alarmTime`).
    cache:           잡 단위 캐시(두 엔진 공용 — 주입 시 닫지 않는다).
    synthesize:      참이면 실행 후 처리(`finalize_playbook_run`)까지 한다.
                     `narrator`를 주면 자동 참.
                     소견기 없이 켜면 가설·대응·추가 확인만 채우고 소견은 「소견기 미구성」.
    narrator:        단계 소견기·종합기(LLM 주입 — W4 배선). 기본 None = 기존 동작 그대로.
    activity:        실행 기록(plans/187 W20) — `when` 거짓·예산 소진으로 건너뛴 스텝마다
                     `step_skip` 1건(사유 문장 · 조건식 원문 없음). None이면 무동작.
    on_skip:         건너뜀 통지 `(자리표시 StepResult, 사유 문장)` — 부분 브리핑용.
                     None이면 무동작.
    retry_timed_out: 참이면 수집 단계 뒤 남은 예산 안에서 「스텝 타임아웃」 결손 스텝만 YAML 순서로
                     1회씩 다시 돈다(plans/187 W22 · `retry_timed_out_steps`).
                     기본 거짓 = 종전 동일.
    """
    started = clock()
    library = STEP_LIBRARY if steps is None else steps
    own_cache = cache is None
    job_cache = ToolResultCache() if cache is None else cache
    try:
        run = _run(
            payload,
            playbook,
            matched_by=matched_by,
            polestar=job_cache.wrap(polestar) if polestar is not None else None,
            apm=job_cache.wrap(apm) if apm is not None else None,
            clock=clock,
            now=now,
            budget_s=budget_s,
            step_timeout_s=step_timeout_s,
            max_workers=max(1, int(max_workers)),
            for_each_max=for_each_max,
            polestar_source=polestar_source,
            polestar_source_basis=polestar_source_basis,
            reference_time=reference_time,
            library=library,
            started=started,
            activity=activity,
            on_skip=on_skip,
            retry_timed_out=retry_timed_out,
        )
    finally:
        if own_cache:
            job_cache.close()
    if synthesize or narrator is not None:
        finalize_playbook_run(run, playbook, narrator=narrator, clock=clock)
    return run


def _placeholder(step: StepSpec, status: str, reason: str | None) -> StepResult:
    return StepResult(
        step=step.id,
        action=step.action,
        title=ACTION_TITLES.get(step.action, step.action),
        status=status,
        reason=reason,
    )  # type: ignore[arg-type]


def _run(
    payload: Mapping[str, Any],
    playbook: Playbook,
    *,
    matched_by: str,
    polestar: BatchCaller | None,
    apm: BatchCaller | None,
    clock: Callable[[], float],
    now: Callable[[], datetime],
    budget_s: float,
    step_timeout_s: float,
    max_workers: int,
    for_each_max: int | None,
    polestar_source: str | None,
    reference_time: str | None,
    library: Mapping[str, StepFn],
    started: float,
    polestar_source_basis: str | None = None,
    activity: ActivityLog | None = None,
    on_skip: Callable[[StepResult, str], None] | None = None,
    retry_timed_out: bool = False,
) -> PlaybookRun:
    notes: list[str] = []
    source, source_reason = resolve_polestar_source(payload, polestar_source)
    scope = initial_scope(payload, source, source_reason)
    if source is None:
        notes.append(f"폴스타 서버 미확정 — {source_reason}")
    elif polestar_source_basis and resolve_polestar_source(payload)[0] is None:
        # 페이로드에 소스가 없어 호출자 대체값을 썼다 — 근거를 남긴다(W21 P6 · 침묵 대체 금지)
        scope.polestar_basis = polestar_source_basis
        notes.append(polestar_source_basis)
    alarm_time = _resolve_alarm_time(payload, reference_time)
    results: dict[str, StepResult] = {}
    contexts: dict[str, StepContext] = {}
    #: 「스텝 타임아웃」으로 결손된 스텝 → (첫 시도 사유, when 메모) — 재시도 대상(W22).
    timed_out: dict[str, tuple[str, str | None]] = {}

    if alarm_time is None:
        for step in playbook.steps:
            status = "pending_synthesis" if step.action == _SYNTHESIZE else "missing"
            results[step.id] = _placeholder(
                step, status, "알람 시각 미상(event.alarmTime) — 시간창 계산 불가"
            )
        return _finish(
            playbook, matched_by, scope, None, results, contexts, notes, clock, started, budget_s
        )

    windows = compute_windows(alarm_time, playbook.window)
    # 실행기는 단계마다 새로 만든다(W21 교정) — 타임아웃으로 중단된 스텝 스레드가 워커를 붙잡아
    # 다음 단계 스텝이 늦게 시작하지 않게 한다(중단된 스레드는 새 호출을 내지 않는다).
    executor: ThreadPoolExecutor | None = None
    try:
        for stage in plan_stages(playbook):
            remaining = budget_s - (clock() - started)
            if remaining <= 0:
                for step in stage:
                    results[step.id] = _placeholder(
                        step, "skipped", f"budget — 남은 예산 없음(예산 {budget_s:.0f}s 소진)"
                    )
                    _notify_skip(results[step.id], BUDGET_SKIP, activity, on_skip)
                continue
            when_ctx = _when_context(results, _readings_of(playbook, results))
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
            executor = ThreadPoolExecutor(
                max_workers=max_workers, thread_name_prefix="playbook-step"
            )
            # 실제 대기는 실시간 단조 시계로 잰다(`clock`은 주입된 가짜일 수 있다).
            budget_end = time.monotonic() + remaining
            # (스텝, future, 메모, 대기 상한) — 상한은 스텝이 실제로 시작한 시각부터(W21 P3)
            submitted: list[tuple[StepSpec, Future[StepResult], str | None, float]] = []
            for step in stage:
                verdict = (
                    step.when_expr.evaluate(when_ctx) if step.when_expr is not None else Tri.TRUE
                )
                if verdict is Tri.FALSE:
                    results[step.id] = _placeholder(step, "skipped", f"when_false — {step.when}")
                    if activity is not None or on_skip is not None:
                        assert step.when_expr is not None
                        because = skip_because(step.when_expr, when_ctx, _titles(playbook))
                        _notify_skip(results[step.id], because, activity, on_skip)
                    continue
                # UNKNOWN(앞 스텝 결손으로 조건을 판정할 수 없음)은 실행한다 — 결손이 진단 단계를
                # 침묵 생략시키면 운영자는 「조사했는데 이상 없음」과 「조사하지 못함」을 구분할
                # 수 없다.
                note = (
                    f"when 판정 불가(결손) — 조건 `{step.when}` 무시하고 실행"
                    if verdict is Tri.UNKNOWN
                    else None
                )
                ctx = StepContext(
                    payload=payload,
                    windows=windows,
                    scope=scope,
                    args=step.args,
                    targets=_step_targets(step, scope, for_each_max),
                    polestar=polestar,
                    apm=apm,
                    now=now,
                    prior=dict(results),
                )
                contexts[step.id] = ctx
                limit = step_wait_s(step, step_timeout_s, remaining)
                submitted.append(
                    (step, executor.submit(_exec_step, step, ctx, library, clock), note, limit)
                )
            for step, fut, note, limit in submitted:
                # 큐 대기(단계 스텝 수 > 워커 수)는 상한에 넣지 않는다 — 시작할 때까지는 남은
                # 예산 안에서 기다리고, 상한은 시작 시각부터 잰다. 앞 스텝을 기다린 시간이 뒤 스텝
                # 상한에 누적되지도, 그만큼 잘리지도 않는다.
                ctx = contexts[step.id]
                ctx.started.wait(max(0.0, budget_end - time.monotonic()))
                end = budget_end
                if ctx.started_at is not None:
                    end = min(ctx.started_at + limit, budget_end)
                try:
                    res = fut.result(timeout=max(0.0, end - time.monotonic()))
                except FutureTimeout:
                    res = _timed_out(step, fut, ctx, limit)
                    if (ctx.abort_reason or "").startswith(STEP_TIMEOUT_PREFIX):
                        timed_out[step.id] = (ctx.abort_reason or "", note)
                res.reason = _join(note, res.reason)
                results[step.id] = res
                new_scope = res.artifacts.get("scope") if step.action in SCOPE_ACTIONS else None
                if isinstance(new_scope, RunScope):
                    scope = new_scope
        if retry_timed_out and timed_out:
            for step in playbook.steps:
                if step.id not in timed_out:
                    continue
                remaining = budget_s - (clock() - started)
                if remaining <= 0:
                    break  # 남은 예산 없음 — 첫 시도 사유 그대로(재시도 0)
                first, note = timed_out[step.id]
                ctx = StepContext(
                    payload=payload, windows=windows, scope=scope, args=step.args,
                    targets=_step_targets(step, scope, for_each_max),
                    polestar=polestar, apm=apm, now=now, prior=dict(results),
                )
                ctx.step_id, ctx.retry_of = step.id, first
                # 재시도마다 새 실행기 — 앞 재시도가 다시 타임아웃돼 중단된 스레드가 워커를
                # 붙잡아도 뒤 재시도가 「시작하지 못함」으로 굶지 않게 한다
                # (단계 실행기와 같은 이유 · W21 교정).
                if executor is not None:
                    executor.shutdown(wait=False, cancel_futures=True)
                executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="playbook-retry")
                res = _retry_step(step, ctx, first, executor, library, clock,
                                  step_wait_s(step, step_timeout_s, remaining))
                res.reason = _join(note, res.reason)
                results[step.id] = res
                # 다시 타임아웃이면 받은 결과가 더 많은 쪽 문맥을 남긴다(부분 결과 보존 · W21 P4)
                if res.status != "missing" or len(ctx.outputs) >= len(contexts[step.id].outputs):
                    contexts[step.id] = ctx
    finally:
        # 타임아웃으로 남은 스레드는 기다리지 않는다(잡 캐시가 닫히면 그 호출은 오류로 끝난다).
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    for step in playbook.steps:
        if step.action == _SYNTHESIZE:
            results[step.id] = _placeholder(
                step, "pending_synthesis", "종합은 단계 소견기·종합기(W3) 담당"
            )
    return _finish(
        playbook, matched_by, scope, windows, results, contexts, notes, clock, started, budget_s
    )


def _step_targets(step: StepSpec, scope: RunScope, for_each_max: int | None) -> list[str]:
    """스텝이 도는 대상 이름 — `for_each`(targets · host_was)면 상한까지, 아니면 첫 대상."""
    names = scope.target_names()
    if step.for_each == "host_was":
        names = list(scope.host_was)
    if step.for_each in ("targets", "host_was"):
        return names if for_each_max is None else names[: max(1, for_each_max)]
    return names[:1]


#: 결손 스텝 재시도 사유(plans/187 W22 · 결정적).
RETRY_OK = "재시도 1회 — 성공(첫 시도: {first})"
RETRY_DONE = "재시도 1회 — 상태 {status}(첫 시도: {first})"
RETRY_SCOPE_KEPT = "범위는 초기 범위 유지(뒤 단계는 이미 실행됨)"
RETRY_TIMED_OUT = "{first} · 재시도 1회 — 다시 타임아웃 {limit} — {progress}"
RETRY_NOT_STARTED = "{first} · 재시도 1회 — 시작하지 못함(상한 {limit})"


def _retry_step(
    step: StepSpec,
    ctx: StepContext,
    first: str,
    executor: ThreadPoolExecutor,
    library: Mapping[str, StepFn],
    clock: Callable[[], float],
    limit: float,
) -> StepResult:
    """「스텝 타임아웃」 결손 스텝을 1회 다시 돈다(plans/187 W22 · 결정적 · LLM 0).

    대기 상한 = `limit`(스텝 상한과 남은 예산 중 작은 값 · 시작 시각부터). 잡 도구 캐시를 거친
    콜러블을 그대로 써 이미 받은(또는 첫 시도 스레드가 아직 기다리는) 호출은 다시 부르지 않는다.
    다시 타임아웃이면 첫 시도처럼 중단 신호를 세우고 두 시도의 사유를 잇는다. 대상 확정 스텝이
    성공해도 범위는 바꾸지 않는다(뒤 단계는 이미 초기 범위로 실행됐다).
    """
    fut = executor.submit(_exec_step, step, ctx, library, clock)
    deadline = time.monotonic() + limit
    ctx.started.wait(max(0.0, deadline - time.monotonic()))
    end = deadline if ctx.started_at is None else min(ctx.started_at + limit, deadline)
    try:
        res = fut.result(timeout=max(0.0, end - time.monotonic()))
    except FutureTimeout:
        if fut.done():
            return _retry_step_result(step, fut.result(), first)
        if fut.cancel():
            reason = RETRY_NOT_STARTED.format(first=first, limit=_secs(limit))
        else:
            reason = RETRY_TIMED_OUT.format(
                first=first, limit=_secs(limit), progress=ctx.progress_text()
            )
        ctx.abort(reason)
        res = _placeholder(step, "missing", reason)
        res.tools = list(ctx.tools)
        res.elapsed_ms = int(limit * 1000)
        return res
    return _retry_step_result(step, res, first)


def retry_reason(action: str, status: str, first: str, reason: str | None) -> str:
    """끝난 재시도의 사유 — 재시도 머리(성공 · 상태) + (대상 확정 스텝이면) 범위 유지 + 스텝 사유.

    실행기(최종 섹션)와 dispatcher(부분 브리핑)가 같은 문장을 쓴다(W22 · 결정적).
    """
    head = (
        RETRY_OK.format(first=first)
        if status == "ok"
        else RETRY_DONE.format(status=status, first=first)
    )
    if action in SCOPE_ACTIONS:
        head = f"{head} · {RETRY_SCOPE_KEPT}"
    return f"{head} · {reason}" if reason else head


def _retry_step_result(step: StepSpec, res: StepResult, first: str) -> StepResult:
    """재시도가 끝난 결과에 재시도 사유를 앞에 붙인다(`retry_reason`)."""
    res.reason = retry_reason(step.action, str(res.status), first, res.reason)
    return res


def step_wait_s(step: StepSpec, default_s: float, remaining_s: float) -> float:
    """스텝 대기 상한 — `min(스텝 timeout_s 또는 기본값, 남은 예산)`(plans/187 W21 P3)."""
    return max(0.0, min(step.timeout_s or default_s, remaining_s))


#: 스텝 타임아웃 사유의 머리(재시도 대상 판정 — W22).
STEP_TIMEOUT_PREFIX = "스텝 타임아웃"
#: 남은 예산 안에 시작하지 못한 스텝의 사유(큐 대기 · W21 교정).
STEP_NOT_STARTED = "스텝 미시작 — 남은 예산 안에 실행 자리가 나지 않음(상한 {limit})"


def _secs(value: float) -> str:
    return f"{value:.3g}s"


def _timed_out(
    step: StepSpec, fut: Future[StepResult], ctx: StepContext, limit: float
) -> StepResult:
    """스텝 타임아웃 — 중단 신호를 세우고(이후 새 호출 0) 진행 계수를 사유에 담는다(W21 P4).

    그 사이 끝났으면 그 결과를 쓴다. 끝난 호출의 도구 목록(`ctx.tools`)은 싣는다. 대상 확정
    스텝이면 범위는 바꾸지 않는다(부분 결과로 비교 집합을 판정하지 않는다 — 초기 범위로 강등).
    """
    if fut.done():
        try:
            return fut.result()
        except Exception:  # noqa: BLE001 — `_exec_step`은 예외를 내지 않는다(방어)
            pass
    if fut.cancel():  # 시작도 못 함 — 남은 예산 안에 실행 자리가 나지 않았다
        reason = STEP_NOT_STARTED.format(limit=_secs(limit))
    else:
        reason = f"{STEP_TIMEOUT_PREFIX} {_secs(limit)} — {ctx.progress_text()}"
    ctx.abort(reason)
    res = _placeholder(step, "missing", reason)
    res.tools = list(ctx.tools)
    res.elapsed_ms = int(limit * 1000)
    return res


def _titles(playbook: Playbook) -> dict[str, str]:
    """스텝 ID → 제목(액션 제목)."""
    return {st.id: ACTION_TITLES.get(st.action, st.action) for st in playbook.steps}


def _notify_skip(
    res: StepResult,
    because: str,
    activity: ActivityLog | None,
    on_skip: Callable[[StepResult, str], None] | None,
) -> None:
    """건너뜀 1건 — `step_skip` 기록 + 통지(둘 다 best-effort · 없으면 무동작)."""
    safe_append(activity, "step_skip", engine="playbook", step=res.step,
                label=f"{res.step} {res.title}", status="skipped", because=because)
    if on_skip is None:
        return
    try:
        on_skip(res, because)
    except Exception as e:  # noqa: BLE001 — 통지(부분 브리핑) 실패가 실행을 막지 않는다
        logger.warning("건너뜀 통지 실패(무시) %s: %s: %s", res.step, type(e).__name__, e)


# ── 조사 흐름(plans/187 W20 · §5.6.4) — 단계 시작·끝의 결정적 문장 · 단계 끝 해석 요청 ──


def flow_readings(playbook: Playbook, finished: Mapping[str, StepResult]) -> Readings:
    """그 시점까지 끝난 스텝만으로 판독을 계산한다(C1 — 뒤 단계 결과 없음)."""
    return _readings_of(playbook, finished)


def step_because(
    playbook: Playbook,
    step: StepSpec,
    finished: Mapping[str, StepResult],
    findings: Mapping[str, str],
    *,
    alarm_name: str | None = None,
) -> str:
    """단계 시작의 「왜」(결정적 · LLM 0).

    조건 없는 단계 = `narrate.why` → 「{focus}를 확인」 → 「{제목}을 확인」 · 첫 단계는 알람 요약을
    앞에 붙인다. `when`으로 실행된 단계 = 참이 된 조건 원소 문장 + 근거 단계 결과 한 줄 인용
    (판정 불가로 실행됐으면 판정 못 한 조건과 사유만 — 작성자 문장은 붙이지 않는다).
    """
    titles = _titles(playbook)
    narrate = step.narrate
    tail = default_because(
        narrate.why if narrate else None, narrate.focus if narrate else None, titles[step.id]
    )
    if step.when_expr is not None:
        readings = _readings_of(playbook, finished)
        reading_steps = {n: list(ev.steps) for n, ev in readings.evidence.items() if ev.steps}
        reasons = {n: ev.reason for n, ev in readings.evidence.items() if ev.reason}
        return when_because(
            step.when_expr, _when_context(finished, readings), titles=titles,
            findings=findings, tail=tail, reading_steps=reading_steps, reading_reasons=reasons,
        )
    if alarm_name and playbook.steps and playbook.steps[0].id == step.id:
        return f"알람 「{alarm_name}」 — {tail}"
    return tail


def step_finding(res: StepResult, readings: Readings) -> str | None:
    """단계 끝의 결과 한 줄(결정적 · 표 밖 수치 0)."""
    return summarize(res.action, res.to_dict(), readings.values)


def step_narration_request(
    step: StepSpec, res: StepResult, readings: Readings, prior_findings: Sequence[str]
) -> StepNarration | None:
    """단계 끝 해석 요청 1건 — 소견 대상(`narrate` 있음 · 상태 ok)이 아니면 None.

    판독은 그 시점까지 끝난 스텝으로 계산한 것(C1), `prior_findings`는 앞 단계 결과 한 줄들이다.
    """
    if step.narrate is None or step.action == _SYNTHESIZE or res.status != "ok":
        return None
    return StepNarration(
        step=step.id,
        section=res.to_dict(include_data=False),
        focus=step.narrate.focus,
        max_sentences=step.narrate.max_sentences,
        readings=_step_readings(step.action, readings),
        realtime_badges=tuple(b for b in res.badges if b.startswith("현재값")),
        prior_findings=tuple(prior_findings),
    )


def _exec_step(
    step: StepSpec, ctx: StepContext, library: Mapping[str, StepFn], clock: Callable[[], float]
) -> StepResult:
    """스텝 1개 실행 — 개별 try/except(독립 신호 · 한 스텝 실패가 다른 스텝을 막지 않는다).

    시작 시각을 문맥에 남긴다(실행기 대기 상한의 기준 — W21).
    """
    ctx.started_at = time.monotonic()
    ctx.started.set()
    t0 = clock()
    fn = library.get(step.action)
    if fn is None:
        res = StepResult(status="missing", reason=f"스텝 라이브러리에 없는 액션 {step.action!r}")
    else:
        try:
            res = fn(ctx)
        except Exception as e:  # noqa: BLE001 — 스텝 단위 격리(사유는 섹션에 노출)
            logger.warning("플레이북 스텝 실패 %s(%s): %s", step.id, step.action, e, exc_info=True)
            res = StepResult(status="missing", reason=f"스텝 실패 — {type(e).__name__}: {e}")
    res.step = step.id
    res.action = step.action
    res.title = ACTION_TITLES.get(step.action, step.action)
    res.tools = list(ctx.tools)
    res.elapsed_ms = int((clock() - t0) * 1000)
    return res


def _correlation(results: Sequence[StepResult], alarm_time: str | None) -> dict[str, Any] | None:
    """S4 지표 계열 + S5 알람·이벤트로 상관을 다시 계산한다(`correlate` 재사용 · G-24)."""
    if alarm_time is None:
        return None
    series, alarms, points, alarm_axis = [], [], [], False
    found = False
    for r in results:
        if r.status != "ok":
            continue
        if r.action == "host.usage_trend":
            series = list(r.artifacts.get("series") or [])
            found = True
        elif r.action == "xsource.related_alarms":
            alarms = list(r.artifacts.get("alarms") or [])
            points = list(r.artifacts.get("episode_points") or [])
            alarm_axis = "polestar_incident_alarms" not in r.missing
            found = True
    if not found:
        return None
    return correlate(
        alarm_time, alarms, series, [], episode_points=points or None, alarm_axis=alarm_axis
    ).to_dict()


def _finish(
    playbook: Playbook,
    matched_by: str,
    scope: RunScope,
    windows: RunWindows | None,
    results: Mapping[str, StepResult],
    contexts: Mapping[str, StepContext],
    notes: list[str],
    clock: Callable[[], float],
    started: float,
    budget_s: float,
) -> PlaybookRun:
    sections = [results[s.id] for s in playbook.steps if s.id in results]
    readings = _readings_of(playbook, results)
    outputs: list[str] = []
    calls: list[dict[str, Any]] = []
    for s in sections:
        ctx = contexts.get(s.step)
        if ctx is None:
            continue
        outputs.extend(ctx.outputs)
        calls.extend({"step": s.step, "tool": t, "args": a} for t, a in ctx.calls)
    return PlaybookRun(
        playbook_id=playbook.id,
        version=playbook.version,
        matched_by=matched_by,
        scope=scope.to_dict(),
        windows=windows.to_dict() if windows else None,
        sections=sections,
        timings={
            "total_ms": int((clock() - started) * 1000),
            "budget_s": budget_s,
            "budget_exceeded": any(
                s.status == "skipped" and (s.reason or "").startswith("budget") for s in sections
            ),
            "steps": {s.step: s.elapsed_ms for s in sections},
        },
        tool_outputs=outputs,
        tool_calls=calls,
        notes=notes,
        correlation=_correlation(sections, windows.alarm_time if windows else None),
        readings=readings,
    )


# ── 실행 후 처리 — 판독 → 가설 → 단계 소견 → 종합 (W3) ──────────────────────

STEP_STATUS_NO_OPINION = "단계 상태 {status} — 소견 없음"
OVER_CALL_LIMIT = "소견 호출 상한 {limit}회 초과 — 결정적 우선순위 밖"
_HYP_COLUMNS = ["순위", "가설", "상태", "근거 스텝"]


def _alarm_degraded(scope: Mapping[str, Any]) -> bool:
    """알람 대상(인스턴스)의 판정이 degraded인가 — 대상 명단에서 알람 인스턴스, 없으면 첫 대상."""
    targets = [t for t in scope.get("targets") or [] if isinstance(t, Mapping)]
    inst = scope.get("alarm_instance")
    own = next((t for t in targets if t.get("instance") == inst), targets[0] if targets else None)
    return bool(own and own.get("verdict") == "degraded")


def _step_readings(action: str, readings: Readings) -> dict[str, dict[str, Any]]:
    """스텝 소견에 실을 판독 — 그 액션이 담당하는 판독만(값·규칙·근거 수치·사유)."""
    out: dict[str, dict[str, Any]] = {}
    for name, actions in READING_ACTIONS.items():
        if action not in actions:
            continue
        ev = readings.evidence.get(name)
        out[name] = {
            "value": readings.values.get(name),
            "rule": ev.rule if ev else None,
            "values": ev.values if ev else {},
            "reason": ev.reason if ev else None,
        }
    return out


def _narration_plan(
    playbook: Playbook,
    sections: Mapping[str, StepResult],
    hypotheses: Sequence[Hypothesis],
    limit: int,
) -> tuple[list[StepSpec], dict[str, str]]:
    """소견을 부를 스텝(우선순위 순)과 부르지 않는 스텝의 사유.

    대상: `narrate` 지시가 있고 상태가 ok인 스텝. 우선순위(결정적): 반박이 아닌 가설의 근거 스텝 ·
    판정 degraded 스텝 먼저, 나머지 — 각 묶음 안은 YAML 순서. 상한(`limit`) 밖은 사유를 남긴다.
    """
    live = {s for h in hypotheses if h.status != REFUTED for s in h.evidence_steps}
    eligible: list[StepSpec] = []
    skipped: dict[str, str] = {}
    for step in playbook.steps:
        if step.narrate is None or step.action == _SYNTHESIZE:
            continue
        sec = sections.get(step.id)
        if sec is None or sec.status != "ok":
            status = sec.status if sec is not None else "missing"
            skipped[step.id] = STEP_STATUS_NO_OPINION.format(status=status)
            continue
        eligible.append(step)
    ordered = sorted(
        eligible,
        key=lambda st: 0 if st.id in live or sections[st.id].verdict == "degraded" else 1,
    )
    for step in ordered[limit:]:
        skipped[step.id] = OVER_CALL_LIMIT.format(limit=limit)
    return ordered[:limit], skipped


_SLOW_TX_ACTION = "apm.slow_tx_breakdown"
#: 알려진 오류 평가 문맥 `S3.top1` — 1위 느린 거래의 시간 분해 수치(표 열)와 프로파일 구조화 필드.
_TOP1_ROW_FIELDS = ("response_time_ms", "sql_ms", "fetch_ms", "external_ms", "cpu_ms", "network_ms")
_TOP1_PROFILE_FIELDS = ("acquire_ms", "execute_ms")


def _is_num(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _top1_facts(collected: Mapping[str, StepResult]) -> dict[str, dict[str, Any]]:
    """느린 거래 분해 스텝 ID → `{"top1": {...}}` — 수치 칸만(없는 칸은 결손).

    커넥션 획득(`acquire_ms`)은 프로파일 **구조화** 필드가 있을 때만 들어간다. 현 게이트웨이는 이
    필드를 주지 않으므로 이를 참조하는 `cause_test`는 결손 → 「판단 보류」가 정상이다
    (W0 실측 · R-D).
    """
    out: dict[str, dict[str, Any]] = {}
    for sid, sec in collected.items():
        if sec.action != _SLOW_TX_ACTION or sec.status != "ok" or not isinstance(sec.data, Mapping):
            continue
        rows = sec.data.get("rows")
        row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], Mapping) else {}
        top1 = {k: row[k] for k in _TOP1_ROW_FIELDS if _is_num(row.get(k))}
        profile = sec.data.get("profile")
        if isinstance(profile, Mapping):
            top1.update({k: profile[k] for k in _TOP1_PROFILE_FIELDS if _is_num(profile.get(k))})
        out[sid] = {"top1": top1}
    return out


def _reading_steps(playbook: Playbook, readings: Readings) -> dict[str, list[str]]:
    """판독 이름 → 담당 스텝 ID(근거 스텝 · 없으면 그 판독 액션을 가진 플레이북 스텝)."""
    out: dict[str, list[str]] = {}
    for name, actions in READING_ACTIONS.items():
        ev = readings.evidence.get(name)
        steps = list(ev.steps) if ev is not None and ev.steps else [
            st.id for st in playbook.steps if st.action in actions
        ]
        out[name] = steps
    return out


def _known_error_actions(
    pinned: Sequence[tuple[KnownError, KnownErrorEvaluation]],
) -> list[Action]:
    """validated 일치 레코드의 우회 → 대응 제안(맨 앞 · remediation 형식 한 줄 `line` 동봉)."""
    out: list[Action] = []
    for ke, ev in pinned:
        for w in workaround_items(ke, ev):
            out.append(
                Action(
                    0,
                    "known_error",
                    w["action"],
                    hypothesis=f"{KE_RULE_PREFIX}{ke.id}",
                    risk=w["risk"],
                    verification=w["verification"],
                    rollback=w["rollback"],
                    extra={
                        "known_error": ke.id,
                        "confidence": w["confidence"],
                        "review_only": w["review_only"],
                        "line": w["line"],
                    },
                )
            )
    return out


def finalize_playbook_run(
    run: PlaybookRun,
    playbook: Playbook,
    *,
    narrator: PlaybookNarrator | None = None,
    clock: Callable[[], float] = time.monotonic,
    known_errors: Sequence[KnownError] = (),
    payload: Mapping[str, Any] | None = None,
    today: date | None = None,
    step_opinions: Mapping[str, Opinion] | None = None,
) -> PlaybookRun:
    """실행 후 처리 — 판독 → (알려진 오류 일치) → 가설 순위 → 대응·추가 확인 → 단계 소견 → 종합으로
    S9를 채운다.

    가설 순위·상태·대응 순서는 코드값이다(D-035) — 소견기(LLM)는 `opinions`·`synthesis.narrative`만
    채우고, 무엇을 쓰든 순위는 바뀌지 않는다. `narrator`가 None이면 소견은 비우고 사유
    「소견기 미구성」. LLM 호출은 소견 대상 단계 + 종합 1
    (`limits.max_step_calls`를 주면 그 안). `run`을 고쳐 돌려준다.

    W8: `known_errors`(기동 시 적재)를 결정적으로 판정해 validated 완전 일치만 가설 1순위에 고정하고
    그 우회를 대응 맨 앞에 둔다(not_validated는 표시만). `payload`는 환경 조건의 `solution`
    (`meta.hints.solution`) 출처, `today`는 런북·KE 검토 기한 판정일(기본 오늘).

    W20(G-47): `step_opinions`(단계 끝 해석 — 스텝 ID → `Opinion`)를 주면 소견 대상 스텝 중
    거기 있는 것은 **다시 부르지 않고** 그것을 쓴다(없는 스텝만 소견기 호출). None이면 종전 동작.
    """
    t0 = clock()
    today = today or date.today()
    sections = {s.step: s for s in run.sections}
    collected = {sid: s for sid, s in sections.items() if s.action != _SYNTHESIZE}
    readings = _readings_of(playbook, collected)
    run.readings = readings
    verdicts = {sid: s.verdict for sid, s in collected.items()}
    ctx = hypothesis_context(readings, verdicts)
    hyps = rank_hypotheses(ctx, present_steps=list(collected))

    # 알려진 오류(W8) — 평가 문맥 = 가설 문맥 + 1위 느린 거래 수치(`S3.top1`)
    ke_ctx: dict[str, Any] = dict(ctx)
    for sid, extra in _top1_facts(collected).items():
        base = ke_ctx.get(sid)
        ke_ctx[sid] = {**(base if isinstance(base, Mapping) else {}), **extra}
    reading_steps = _reading_steps(playbook, readings)
    evaluations = evaluate_known_errors(
        known_errors,
        playbook_id=playbook.id,
        scope={
            "instance": run.scope.get("alarm_instance") or run.scope.get("host"),
            "solution": _hints(payload or {}).get("solution"),
        },
        ctx=ke_ctx,
        alarm_time=(run.windows or {}).get("alarm_time"),
        reading_steps=reading_steps,
    )
    pinned = [(ke, ev) for ke, ev in evaluations if is_pinned(ke, ev)]
    hyps = pin_hypotheses(
        [
            pinned_hypothesis(
                ke, ev, present_steps=list(collected), reading_steps=reading_steps
            )
            for ke, ev in pinned
        ],
        hyps,
    )
    run.runbook = runbook_briefing(playbook.runbook, today)
    run.known_errors = [known_error_briefing(ke, ev, today) for ke, ev in evaluations]

    degraded = _alarm_degraded(run.scope)
    actions = select_actions(
        hyps, playbook.response_hints, playbook.runbook.mitigation, ctx, alarm_degraded=degraded,
        known_error_actions=_known_error_actions(pinned),
    )
    followups = build_followups(hyps, readings)
    for s in collected.values():
        if s.status == "missing":
            line = f"{s.step} {s.title}: 수집 결손" + (f" — {s.reason}" if s.reason else "")
            if line not in followups:
                followups.append(line)
    top_inconclusive = is_top_inconclusive(hyps, alarm_degraded=degraded)

    # 단계 소견
    cap = narrator.limits.max_step_calls if narrator is not None else None
    limit = len(playbook.steps) if cap is None else cap
    planned, skipped = _narration_plan(playbook, collected, hyps, limit)
    opinions: dict[str, Opinion] = {sid: Opinion(dropped_reason=r) for sid, r in skipped.items()}
    if step_opinions is not None:
        for step in planned:
            if step.id in step_opinions:
                opinions[step.id] = step_opinions[step.id]
        planned = [step for step in planned if step.id not in step_opinions]
    if narrator is None:
        for step in planned:
            opinions[step.id] = Opinion(dropped_reason=NOT_CONFIGURED)
    else:
        requests = []
        for step in planned:
            assert step.narrate is not None
            sec = collected[step.id]
            requests.append(
                StepNarration(
                    step=step.id,
                    section=sec.to_dict(include_data=False),
                    focus=step.narrate.focus,
                    max_sentences=step.narrate.max_sentences,
                    readings=_step_readings(step.action, readings),
                    realtime_badges=tuple(b for b in sec.badges if b.startswith("현재값")),
                )
            )
        opinions.update(narrator.narrate_steps(requests))

    hyp_dicts = [h.to_dict() for h in hyps]
    action_dicts = [a.to_dict() for a in actions]
    top_title = hyps[0].title if hyps else None
    if narrator is None:
        narrative = Opinion(dropped_reason=NOT_CONFIGURED)
    else:
        step_lines = [
            {**s.to_dict(include_data=False), "opinion": opinions[s.step].text}
            if s.step in opinions
            else s.to_dict(include_data=False)
            for s in collected.values()
        ]
        values = readings.values
        narrative = narrator.synthesize(
            top_title=top_title,
            playbook_id=playbook.id,
            hypotheses=hyp_dicts,
            actions=action_dicts,
            followups=followups,
            readings=values,
            steps=step_lines,
            timeline=(run.correlation or {}).get("timeline") or [],
            changes=values.get("change_candidates") or [],
        )

    for step in playbook.steps:
        if step.action != _SYNTHESIZE or step.id not in sections:
            continue
        s9 = sections[step.id]
        s9.status = "ok"
        s9.reason = None
        s9.table = {
            "columns": list(_HYP_COLUMNS),
            "rows": [[h.rank, h.title, h.status_label, ", ".join(h.evidence_steps)] for h in hyps],
        }
        s9.data = {"hypotheses": hyp_dicts, "top_inconclusive": top_inconclusive}
        opinions[step.id] = narrative

    run.opinions = opinions
    run.synthesis = {
        "hypotheses": [
            {
                k: d[k]
                for k in ("rank", "title", "status", "status_label", "evidence_steps", "rule_id")
            }
            for d in hyp_dicts
        ],
        "actions": action_dicts,
        "followups": followups,
        "narrative": narrative.text,
        "narrative_dropped_reason": narrative.dropped_reason,
        "narrative_guard": list(narrative.guard),
        "top_inconclusive": top_inconclusive,
    }
    run.timings["synthesis_ms"] = int((clock() - t0) * 1000)
    run.timings["llm_calls"] = narrator.calls if narrator is not None else 0
    return run


__all__ = [
    "DEFAULT_BUDGET_S",
    "DEFAULT_MAX_WORKERS",
    "DEFAULT_STEP_TIMEOUT_S",
    "GATEWAY_EVENT_SIGNAL_KINDS",
    "LATENCY_EVENT_TYPES",
    "POLESTAR_BRIDGE_MISSING",
    "PlaybookRun",
    "alarm_facts_from_payload",
    "compute_windows",
    "finalize_playbook_run",
    "flow_readings",
    "initial_scope",
    "normalize_event_type",
    "plan_stages",
    "resolve_polestar_source",
    "run_playbook",
    "step_because",
    "step_finding",
    "step_narration_request",
]
