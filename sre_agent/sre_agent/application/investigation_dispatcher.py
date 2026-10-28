"""조사 dispatcher — 결정적 폭주 방지 가드 (Plan 02 §4·§10, D-035 계승).

HolmesGPT ReAct 조사 루프를 감싸되, **트리거·dedup·동시성·타임아웃·예산은 코드가 전담**한다
(LLM에 위임 금지). JobStore(2-C)가 남긴 executor 주입점에 배선되는 실 dispatcher다.

가드 6종:
0. **대상 가용성**(Plan 81 · `docs/25` L-5) — 호출자가 실은 `target_state`가 `unavailable`이면
   조사 전에 거부한다. 죽은 호스트에서는 도구가 **에러가 아니라 빈 데이터**를 돌려주므로
   ReAct 루프가 전체 타임아웃(600s)까지 돌며 근거 없는 서술을 만들 수 있다.
   **필드가 없으면 통과**한다(fail-open) — 이 가드는 보안 통제가 아니라 낭비 방지다.
1. fingerprint dedup TTL — 완료된 조사도 `investigation_dedup_ttl_seconds` 내 재조사 억제.
2. 동시 상한 — `investigation_max_concurrent`(기본 2) 세마포어 + 대기 상한
   `investigation_max_queued`(기본 4 · plans/194 W3 · D-347 — 넘치면 `rejected(queue_full)`).
   타임아웃으로 버린 조사 스레드는 끝날 때까지(최대 `investigation_abandon_grace_seconds`) 슬롯·
   L-4 호스트 키를 붙들고 협조적 중단 신호(`job.cancel_event`)를 받는다.
3. 전체 타임아웃 — `investigation_timeout_seconds`(기본 600s), **조사 1건 전체**에 asyncio.wait_for
   (per-call 아님). collectorinfra MCP 동기 타임아웃(60s)보다 길므로 조사는 백그라운드 워커에서
   실행하고 submit은 즉시 `running`을 남겨 반환한다(submit/poll 계약 성립).
4. 시간당 예산 — `investigation_hourly_budget` 초과 시 신규 조사 거부.
5. 토큰 비용 감사 — DiagnosisResult.total_tokens/total_cost를 잡·decision JSONL에 기록.

계측 가산 키(plans/154 W1 · D-324 ⑦ — 플래그 없이 상시 · 동작 무변경): 종결한 잡(`instrumentation`
→ `summary()`)과 `done` 감사에 `incomplete`·`elapsed_s`·`prefetch_s`·`steps`·`tool_calls_n`·`model`·
`max_steps`·`tool_calls[{name, args_digest, status}]`(원시 출력·인자 원문 제외)를 더한다. 타임아웃·
실패는 얻을 수 있는 `elapsed_s`·`model`·`max_steps`만. 미완주는 `status=done` 유지(D-123 어휘 불변).

in-memory 상태(dedup dict·budget window)는 **값 bound + 키 만료 sweep**을 동시 구현한다
(CLAUDE.md Known Mistakes — 데몬 dict는 값 bound뿐 아니라 키 sweep도).

계층 청결: job/DiagnosisResult 등 application 세부 타입은 **구조적 Protocol**로
디커플링하고, 실 조사 함수(diagnose_fn)·브리핑 함수(briefing_fn)는 **주입**받는다. LLM 키 부재 시
스텁을 유지하되 가드는 그대로 적용한다. 플레이북 배선(plans/187 W4)은 플레이북 엔진(runner·
narrator·steps)·브리핑 조립·지침을 직접 import한다(같은 application 계층 — arch_check 경고만).

플레이북 모드(plans/187 W4 · §5.0.1 · `SRE_PLAYBOOK_ENABLED` + `playbook` 배선 주입 시만):
알람 → 플레이북 선택 → 잡 단위 도구 결과 캐시 → **사전수집 생략**(G-24 — 상관은 S4·S5로 다시
계산) → 플레이북 실행·종합(≤ `sre_playbook_budget_s`) → 심층 조사(HolmesGPT) 모드
`off`·`conditional`·`parallel`(YAML `deep_dive.mode`가 전역 재정의 · fallback은 항상 심층) →
판정 = 두 엔진 도구 출력 합집합(G-25) → 6요소 = 플레이북 종합 + 선택 키 `playbook`·`deep_dive`·
`engine_agreement`. 진척은 `job.progress`·`job.partial_briefing`. 배선이 없거나 플래그 off면 이
경로의 설정(`sre_deep_dive_mode` 등)을 읽지도 않는다 — 종전과 비트 동일.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import functools
import hashlib
import json
import logging
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from sre_agent.application.briefing_builder import build_partial_briefing, build_playbook_briefing
from sre_agent.application.investigation_guidance import PROM_ANCHOR_REASONS, deep_dive_note
from sre_agent.application.playbook_narrator import (
    NOT_CONFIGURED,
    LlmCall,
    NarratorLimits,
    Opinion,
    PlaybookNarrator,
    StepOpinionStream,
)
from sre_agent.application.playbook_runner import (
    STEP_STATUS_NO_OPINION,
    PlaybookRun,
    alarm_facts_from_payload,
    finalize_playbook_run,
    flow_readings,
    mapped_polestar_source,
    rerun_hint,
    retry_reason,
    run_playbook,
    step_because,
    step_finding,
    step_narration_request,
)
from sre_agent.application.playbook_steps import (
    ACTION_TITLES,
    STEP_LIBRARY,
    StepContext,
    StepFn,
    StepResult,
    step_queries,
)
from sre_agent.domain.activity_log import ActivityLog, safe_append
from sre_agent.domain.answer_markup import (
    ANSWER_MALFORMED_TOOL_CALL,
    MALFORMED_ANSWER_CAUSE,
    MALFORMED_ANSWER_LIMITATION,
    MALFORMED_ANSWER_SUMMARY,
    has_tool_call_markup,
    markup_log_excerpt,
)
from sre_agent.domain.cross_source_episode import (
    INVESTIGATION_KIND_EPISODE,
    active_episode,
    episode_from_payload,
    is_episode_investigation,
)
from sre_agent.domain.investigation_limits import (
    apm_limitations,
    is_apm_trigger,
    repeated_calls,
    stall_limitation,
)
from sre_agent.domain.engine_agreement import compare_engines
from sre_agent.domain.evidence_ledger import build_ledger
from sre_agent.domain.failure_guards import (
    ExpectedSources,
    activity_tool_events,
    ledger_from_activity,
    unchecked_sources,
    unchecked_sources_limitation,
)
from sre_agent.domain.job_watchdog import STUCK_WATCHDOG_REASON, stuck_after_seconds
from sre_agent.domain.known_error import KnownError
from sre_agent.domain.playbook import APM_FALLBACK_ID, Playbook, select_playbook
from sre_agent.domain.remediation import recommend_lines
from sre_agent.domain.step_findings import step_status_label
from sre_agent.domain.severity_signatures import (
    LEVEL_NAMES,
    ImportanceVerdict,
    apm_payloads,
    clamp_level,
    judge,
    match_signatures,
    was_signals_from_outputs,
)
from sre_agent.infrastructure.activity_masking import mask_activity_text
from sre_agent.infrastructure.expected_sources_loader import load_expected_sources
from sre_agent.infrastructure.tool_result_cache import (
    BatchCaller,
    CacheClosedError,
    ToolResultCache,
)
from sre_agent.settings import AgentSettings

logger = logging.getLogger(__name__)

# dedup dict 방어적 하드 상한(ttl sweep에 더해 무한 성장 방지).
_MAX_DEDUP_KEYS = 4096

#: `done` 잡·감사의 계측 가산 키(plans/154 W1 · D-324 ⑦ — 상시). 감사 키 집합을 단언하는 테스트는
#: 이 키를 빼고 종전 키와 비교한다(키 이름의 단일 출처).
INSTRUMENTATION_KEYS: tuple[str, ...] = (
    "incomplete", "elapsed_s", "prefetch_s", "steps", "tool_calls_n", "model", "max_steps",
    "tool_calls",
)


#: 브리핑 인용 근거로 셀 도구 호출 status — plans/188 C3
#: (holmes 0.36.0 `StructuredToolResultStatus` 실측값).
#: `error`·`approval_required`·`frontend_pause`·`unknown`(결과 없음)은 근거가 아니다.
_CITABLE_TOOL_STATUSES: frozenset[str] = frozenset({"success", "no_data"})
_ERROR_TOOL_STATUS = "error"


def _tool_status(to: object) -> str:
    """도구 호출 레코드의 status 문자열(없으면 `unknown`)."""
    return str(getattr(to, "status", None) or "unknown")


def failed_tool_limitation(outputs: Sequence[object]) -> str | None:
    """`error` 호출을 「도구 호출 실패 N건: a, b」 한계 1줄로(plans/188 C3). 없으면 None.

    N은 실패 호출 수, 이름은 중복 제거·첫 등장 순서다.
    """
    failed = [getattr(to, "tool_name", "") for to in outputs
              if _tool_status(to) == _ERROR_TOOL_STATUS]
    if not failed:
        return None
    return f"도구 호출 실패 {len(failed)}건: {', '.join(dict.fromkeys(failed))}"


#: prom_* 0건 응답의 reason(plans/189 W5) — 성공 응답이지만 그 서버의 Prometheus 증거가 없다는 뜻이다.
_PROM_EMPTY_REASON_RE = re.compile(
    r'"reason"\s*:\s*"(' + "|".join(map(re.escape, PROM_ANCHOR_REASONS)) + r')"'
)


def prom_empty_reason(to: object) -> str | None:
    """prom_* 호출 결과가 reason 달린 0건이면 그 reason(plans/189 W5). 아니면 None.

    mcp_server는 0건일 때만 `reason`을 싣는다(plans/189 §2.4) — 이런 호출은 status가 success라
    C3 필터를 통과하므로 여기서 따로 걸러 브리핑 인용 근거에서 뺀다.
    """
    if not str(getattr(to, "tool_name", "") or "").startswith("prom_"):
        return None
    text = str(getattr(to, "output", "") or "")
    try:
        # 앞머리 JSON 값 하나만 읽는다 — holmes 출력은 본문 JSON 뒤에 structuredContent 꼬리가 붙는다.
        payload, _ = json.JSONDecoder().raw_decode(text.lstrip())
    except ValueError:
        m = _PROM_EMPTY_REASON_RE.search(text)  # JSON이 아닐 때만 정규식 폴백
        return m.group(1) if m else None
    if not isinstance(payload, dict):
        return None
    # 최상위 reason(prom_metric_*) 또는 교차 검증의 중첩 prometheus.reason만 본다 — 데이터 라벨의
    # reason(kube_*_waiting_reason 등)이나 다른 필드 문자열에는 반응하지 않는다.
    candidates = [payload.get("reason")]
    nested = payload.get("prometheus")
    if isinstance(nested, dict):
        candidates.append(nested.get("reason"))
    for reason in candidates:
        if isinstance(reason, str) and reason in PROM_ANCHOR_REASONS:
            return reason
    return None


def prom_empty_limitation(outputs: Sequence[object]) -> str | None:
    """reason 달린 prom_* 0건 호출을 「근거 아님」 한계 1줄로(plans/189 W5). 없으면 None."""
    reasons = [r for to in outputs if (r := prom_empty_reason(to))]
    if not reasons:
        return None
    return (f"Prometheus 0건 {len(reasons)}건(근거 아님 — 미수집·식별 불가): "
            f"{', '.join(dict.fromkeys(reasons))}")


def _deep_skip_reason(result: Any) -> str | None:
    """심층 결과를 서술로 쓰지 않을 사유(plans/188 A안·C7과 같은 기준 · 정상이면 None)."""
    tools_unavailable = getattr(result, "tools_unavailable", None)
    if tools_unavailable:
        return str(tools_unavailable)
    if has_tool_call_markup(getattr(result, "answer", None)):
        return MALFORMED_ANSWER_LIMITATION
    return None


def _clip_narrative(answer: str) -> str:
    """심층 답변 원문을 `DEEP_NARRATIVE_MAX_CHARS`로 자른다(잘랐으면 생략 표지를 붙인다)."""
    text = answer.strip()
    if len(text) <= DEEP_NARRATIVE_MAX_CHARS:
        return text
    return text[:DEEP_NARRATIVE_MAX_CHARS] + DEEP_NARRATIVE_CLIPPED.format(
        n=len(text), keep=DEEP_NARRATIVE_MAX_CHARS)


def _add_limitations(briefing_kwargs: dict[str, Any], lines: list[str]) -> None:
    """브리핑 `limitations` 인자에 줄을 합친다.

    줄이 없으면 인자를 건드리지 않는다(종전 호출 동일).
    """
    if lines:
        briefing_kwargs["limitations"] = [*briefing_kwargs.get("limitations", []), *lines]


#: 단계 끝 해석의 근거 표지(§5.6.4 ⓖ C1).
OPINION_BASIS = "이 단계까지의 근거"


@dataclass
class _FlowHooks:
    """조사 흐름 배선(plans/187 W20 · §5.6.4) — 실행 기록이 켜졌을 때만 만든다.

    꺼지면 None(비트 동일).

    stream:  단계 끝 해석 실행기(LLM 미배선이면 None — 종결 일괄 경로).
    on_skip: 건너뜀 통지 — `_progress_library`가 채우고 `run_playbook`에 넘긴다.
    no_stream_reason: stream이 None인 사유(단계 해석 「없음」 표기에 쓴다).
    """

    stream: StepOpinionStream | None = None
    on_skip: Callable[[StepResult, str], None] | None = None
    #: 실행기를 만들지 못한 실제 사유(예: 남은 시간 부족) — None이면 LLM 미배선(「소견기 미구성」).
    no_stream_reason: str | None = None


def _opinion_view(op: Opinion) -> dict[str, Any]:
    """부분 브리핑 `sections[].opinion` — ok(가드 통과 문단) / none(사유)."""
    if op.text and not op.dropped_reason:
        return {"status": "ok", "text": mask_activity_text(op.text), "basis": OPINION_BASIS}
    return {"status": "none", "reason": op.dropped_reason or "소견 없음", "basis": OPINION_BASIS}


def _activity(job: object) -> ActivityLog | None:
    """잡의 실행 기록(plans/187 W13).

    `SRE_ACTIVITY_LOG_ENABLED` off·구현체에 없으면 None(기록 0).
    """
    log = getattr(job, "activity", None)
    return log if isinstance(log, ActivityLog) else None


# ── 구조적 타입(Protocol) — application 세부에 직접 의존하지 않기 위함 ──


class ToolOutputLike(Protocol):
    tool_name: str
    description: str
    output: str
    error: str | None


class DiagnosisLike(Protocol):
    answer: str
    total_tokens: int
    total_cost: float
    tool_calls: list[str]
    tool_outputs: Sequence[ToolOutputLike]


class JobLike(Protocol):
    investigation_id: str
    kind: str
    status: str
    fingerprint: str | None
    payload: dict | None
    question: str | None
    verdict: str | None
    briefing: dict | None
    tool_calls_summary: list[str] | None
    tokens: int | None
    cost: float | None
    error: str | None
    reason: str | None
    updated_at: float
    # 사건 좌표계·상관(plans/50 A′-5·G4). 구현체에 없으면 getattr 기본값으로 읽는다.
    reference_time: str | None
    lookback_minutes: int | None
    correlation: dict | None
    # 계측 가산 키(plans/154 W1) — 종결 시 dispatcher가 채운다.
    instrumentation: dict | None
    # 플레이북 모드 진척(plans/187 W4) — 플레이북 경로에서만 채운다.
    progress: dict | None
    partial_briefing: dict | None
    # 동시 상한 대기 순번(plans/194 W3) — 대기 중에만 값이 있다(1부터).
    queue_position: int | None


@dataclass
class _AnswerOnly:
    """미완주 결과의 판정·브리핑 입력 — 답변만 남긴다(plans/154 W1).

    W1부터 미완주 결과도 상한 직전까지의 도구 기록을 싣지만(계측·벤치 입력), 판정·브리핑은 종전처럼
    도구 기록 없이 답변만 본다 — 동작 무변경(D-324 ⑦). 부분 기록으로 브리핑을 만드는 일은 별도
    플래그 개선 후보(plans/154 H-5) 몫이다.
    """

    answer: str
    total_tokens: int = 0
    total_cost: float = 0.0
    tool_calls: list[str] = field(default_factory=list)
    tool_outputs: Sequence[ToolOutputLike] = ()


DiagnoseFn = Callable[[JobLike], DiagnosisLike]
BriefingFn = Callable[..., dict]
#: 사전수집 콜러블 — 잡을 받아 CorrelationResult.to_dict()(또는 None)를 돌려준다. 예외를 던져도 된다(격리).
PrefetchFn = Callable[[JobLike], "dict | None"]


def _host_key(job: JobLike, episode_scoped: bool = False) -> tuple[str, ...] | None:
    """조사 대상 호스트의 in-flight 키 `(db_id, host)`를 만든다 (78 W2-6 L-4).

    **두 진입점의 payload 형태가 다르다**(실측 2026-08-27) — 한쪽만 보면 그 경로에서 가드가
    통째로 무력화된다(Known Mistakes: 단일/멀티 경로 비대칭):

        알람 트리거  `payload["event"]["dbId"|"hostname"|"serverName"]`
        pull 진단    `payload["db_id"|"hostname"|"server_name"]`

    **한계**: `hostname`이 없으면 `serverName`으로 대체한다. 폴스타는 server_name ≠ hostname
    이므로(D-046), 같은 호스트가 한 번은 hostname으로 한 번은 serverName으로 들어오면 **다른
    키가 되어 가드를 비껴간다.** 해소하려면 이름 해소가 필요한데 그건 본체(`cmm_resource`) 소관이라
    여기서 부를 수 없다(D-118 경계) — 완화가 아니라 **명시된 잔여 한계**다.

    **종합 조사**(plans/153 §4.5 · `episode_scoped` = `cross_source_investigation_enabled`):
    종합 조사 페이로드는 대표(원인) 알람 event를 실어 키가 그 알람의 단독 조사와 같다 —
    단독 조사가 도는 동안 제출되면 거부되고 사건당 1회라 재시도도 없다. 그래서 종합 조사는
    `(db_id, host, "episode")`로 따로 잡는다(같은 호스트 종합 조사끼리의 중복 차단은 유지).

    Args:
        job: 조사 잡
        episode_scoped: True면 종합 조사 잡에 kind 축을 붙인다(False면 종전 키 그대로)

    Returns:
        `(db_id, host[, "episode"])` 키. 대상을 식별할 수 없으면 None(부하 귀속이 불가하므로
        가드 대상 아님)
    """
    payload = getattr(job, "payload", None) or {}
    event = payload.get("event") or {}
    db_id = str(event.get("dbId") or payload.get("db_id") or "").strip()
    host = str(
        event.get("hostname")
        or event.get("serverName")
        or payload.get("hostname")
        or payload.get("server_name")
        or ""
    ).strip()
    if not host:
        return None
    if episode_scoped and is_episode_investigation(payload):
        return (db_id, host, INVESTIGATION_KIND_EPISODE)
    return (db_id, host)


#: 대상 가용성 가드의 거부 사유 코드(Plan 81).
GUARD_TARGET_UNAVAILABLE = "target_unavailable"
#: 대기 상한 거부 사유(plans/194 W3 · D-347) — 슬롯 대기가 `investigation_max_queued`에 닿음.
GUARD_QUEUE_FULL = "queue_full"
#: 같은 상한인데 슬롯 일부를 타임아웃 뒤 버려진 조사 스레드가 붙들고 있음(끝나지 않는 조사가 원인).
GUARD_SERVICE_OVERLOADED = "service_overloaded"


def _target_state(job: JobLike) -> dict | None:
    """잡에서 호출자가 실은 대상 가용성 판정을 꺼낸다 (Plan 81).

    **두 진입점의 payload 형태가 다르다**(`_host_key`와 같은 이유):

        알람 트리거  `payload["meta"]["target_state"]`
        pull 진단    `payload["target_state"]`

    한쪽만 보면 그 경로에서 가드가 통째로 무력화된다(Known Mistakes: 단일/멀티 경로 비대칭).

    Args:
        job: 조사 잡

    Returns:
        판정 dict 또는 None(호출자가 싣지 않았거나 형태가 다름 → 가드 통과)
    """
    payload = getattr(job, "payload", None) or {}
    if not isinstance(payload, dict):
        return None
    meta = payload.get("meta")
    for candidate in ((meta or {}).get("target_state"), payload.get("target_state")):
        if isinstance(candidate, dict) and candidate:
            return candidate
    return None


# 사전수집(prefetch) 타임박스 하한(초) — D-213 후속.
# **per-call 타임아웃이 아니다**: 도구 1건당 상한은 `evidence_prefetch_timeout_seconds`(기본 60s)가
# `mcp_tool_client`에서 이미 강제한다. 이쪽은 배치 전체(알람 1 + 지표 4 + 선택 항목)가 통째로
# 매달렸을 때를 끊는 wedge 네트라, 정상 배치(도구 수 × per-call)를 자르지 않도록 넉넉해야 한다.
# 조사 본예산의 절반을 상한으로 삼아 조사 쪽 예산을 최소 절반 남긴다.
PREFETCH_TIMEBOX_FLOOR_SECONDS: float = 60.0


# ── 플레이북 모드(plans/187 W4) ──────────────────────────────────────────────

#: 심층 조사 결과 키의 표시 라벨(§5.0 — 가설 표기).
DEEP_DIVE_LABEL = "심층 조사(HolmesGPT · 가설)"
#: 심층 답변 원문(`deep_dive.narrative` — 화면 [서술 보기]) 상한(글자). 넘으면 앞부분 + 생략 표지.
DEEP_NARRATIVE_MAX_CHARS = 8000
DEEP_NARRATIVE_CLIPPED = "\n…(이하 생략 — 원문 {n}자 중 앞 {keep}자)"
#: 심층 생략·강등 사유(침묵 생략 금지 — 브리핑 `deep_dive.reason`·한계에 그대로 싣는다).
DEEP_SKIP_MODE_OFF = "심층 모드 off — 플레이북만 실행"
DEEP_SKIP_NOT_TRIGGERED = (
    "conditional 트리거 미충족 — 1순위 가설 판단 보류 아님 · 결손 스텝 {missing}개(< 2) · "
    "fallback 아님"
)
DEEP_DEGRADE_BUDGET = (
    "심층 생략 — 예산(시간당 조사 예산 {budget}건 · 최근 1시간 조사 {runs}건 + 심층 {deep}건) — "
    "parallel → conditional 강등"
)
DEEP_SKIP_KNOWN_ERROR = (
    "심층 생략 — 검증된 알려진 오류 일치({ids}) — 1순위 가설 고정(conditional · §5.0)"
)
DEEP_SKIP_TIME = "심층 생략 — 시간 예산(남은 {remaining:.0f}s < 하한 {floor:.0f}s)"
DEEP_NOT_COMPARABLE_PENDING = "심층 조사 진행 중"
#: 플레이북 이후 심층 조사를 시작하는 최소 잔여 시간(초) — 이보다 짧으면 사유와 함께 생략한다.
DEEP_MIN_REMAINING_S = 30.0
#: 소견기(LLM)를 부르는 최소 잔여 시간(초) — 이보다 짧으면 소견기 없이 종합한다(사유 노출).
NARRATOR_MIN_REMAINING_S = 2.0
NARRATOR_SKIPPED_NOTE = "소견 생략 — 수집 후 남은 시간 {remaining:.1f}s(전체 예산 소진)"
NO_PLAYBOOK_LIMIT = "플레이북 미선택(일치 없음 · fallback 미적재) — 종전 HolmesGPT 조사로 진행"
#: (plans/187 W17) 실행 기록 `purpose` — 플레이북 선택 근거(`matched_by`)를 사람이 읽는 말로.
MATCH_CONDITION_LABELS: dict[str, str] = {
    "resource_type": "자원 유형", "alarm_kind": "알람 종류", "any_kind": "알람 키워드",
}
PLAYBOOK_FALLBACK_PURPOSE = (
    "맞는 유형 플레이북이 없어 공통 점검(사용량 추이·관련 알람·재발·최근 변경)을 돈다"
)
#: APM 공통 점검(`was.fallback` · plans/196 D2 ②) 선택 근거.
APM_FALLBACK_PURPOSE = (
    "맞는 유형 플레이북이 없어 WAS 공통 점검(골든 시그널·제니퍼 이벤트·연관 알람·최근 변경)을 돈다"
)
#: 실행 기록 라벨의 원시 키 → 사용자 말(plans/196 D6) — 플레이북 ID·심층 모드.
PLAYBOOK_LABELS: dict[str, str] = {"fallback": "공통 점검", APM_FALLBACK_ID: "WAS 공통 점검"}
DEEP_MODE_LABELS: dict[str, str] = {"parallel": "병렬", "conditional": "조건부", "off": "끔"}
#: 재조회(plans/196 §5.2 `rerun`)는 심층 조사를 돌지 않는다 — 폴스타 축 재조회 + 종합만.
DEEP_SKIP_RERUN = "심층 생략 — 재조회(폴스타 축 단계만 다시 조회 · 종합만 다시 수행)"
HOLMES_PURPOSE = "정해진 절차가 없어 HolmesGPT가 도구를 직접 골라 알람 원인을 찾는다"
DEEP_PURPOSES: dict[str, str] = {
    "parallel": "플레이북과 따로 두 번째 의견을 낸다(플레이북 결과를 넘기지 않음)",
    "conditional": "플레이북 판정 요약을 넘겨 원인을 더 깊이 확인한다",
}


def playbook_purpose(matched_by: str) -> str:
    """플레이북 선택 근거 한 줄 — `match:a+b` → 「알람의 자원 유형·알람 종류가 … 일치」."""
    if matched_by.startswith("match:"):
        names = [MATCH_CONDITION_LABELS.get(c, c) for c in matched_by[6:].split("+") if c]
        if names:
            return "알람의 " + "·".join(names) + "가 이 플레이북 조건과 일치"
    if matched_by == "fallback":
        return PLAYBOOK_FALLBACK_PURPOSE
    return matched_by
#: 기동 적재에서 거부된 설정 파일(플레이북·KE) — 브리핑 `playbook.notes` 1줄(거부 0이면 문구 없음).
REJECTED_FILES_NOTE = "설정 파일 거부 {n}건({names}) — 관리자 확인 필요"
#: 잡이 끝난(예산 초과·예외) 뒤 수집기가 시작하려던 스텝의 사유.
STEP_STOPPED_REASON = "수집 중단 — 잡 종료(예산 초과·예외) 뒤 시작하지 않음"
#: 결손 스텝 재시도(plans/187 W22)의 단계 시작 「왜」 — 첫 시도 사유를 그대로 인용한다.
RETRY_BECAUSE = "첫 시도가 시간 안에 끝나지 않아 1회 다시 확인(첫 시도: {first})"
#: 잡 종료로 닫힌 캐시에 호출한 스텝의 사유(이후 스텝은 시작하지 않는다).
STEP_CACHE_CLOSED_REASON = "수집 중단 — 잡 종료로 도구 캐시가 닫힘({error})"


@dataclass(frozen=True)
class PlaybookWiring:
    """플레이북 모드 배선(plans/187 W4) — 기동 시 1회 만든다(`mcp_service`).

    playbooks: 기동 시 적재한 플레이북(fallback 포함 가능).
    polestar·apm: 축별 배치 콜러블(None이면 그 축 결손) — 잡마다 잡 단위 캐시로 감싼다.
    llm: 소견기 LLM 어댑터(messages → 텍스트). None이면 소견 없이 결정적 종합만(「소견기 미구성」).
    narrator_limits: 소견기 상한 — 잡마다 남은 시간으로 타임아웃만 줄인다.
    known_errors: 기동 시 적재한 알려진 오류 레코드(W8 · 읽기 전용 — 비면 KE 판정 없음).
    rejected_files: 기동 적재에서 거부된 플레이북·KE 파일명 — 브리핑 notes에 1줄(비면 문구 없음).
    """

    playbooks: tuple[Playbook, ...]
    polestar: BatchCaller | None = None
    apm: BatchCaller | None = None
    llm: LlmCall | None = None
    narrator_limits: NarratorLimits = field(default_factory=NarratorLimits)
    known_errors: tuple[KnownError, ...] = ()
    rejected_files: tuple[str, ...] = ()


def _queries_entry(ctx: StepContext) -> dict[str, Any]:
    """부분 브리핑 섹션의 `queries` 키(plans/195 W4 — 최종 섹션과 같은 규칙 · 비면 키 없음)."""
    queries = step_queries(ctx.calls)
    return {"queries": queries} if queries else {}


@dataclass
class _ToolText:
    """판정 입력용 도구 출력(플레이북 도구 출력을 `ToolOutputLike` 모양으로)."""

    tool_name: str
    output: str
    description: str = ""
    error: str | None = None


class _Background:
    """데몬 스레드 1개로 `fn(arg)`를 돌리고 결과·예외를 담는다(`_join_timebox`의 비차단판)."""

    def __init__(self, fn: Callable[[Any], Any], arg: Any, name: str) -> None:
        self.result: Any = None
        self.error: BaseException | None = None
        self.finished = threading.Event()

        def _target() -> None:
            try:
                self.result = fn(arg)
            except BaseException as exc:  # noqa: BLE001 — 호출 스레드로 전달
                self.error = exc
            finally:
                self.finished.set()

        threading.Thread(target=_target, daemon=True, name=name).start()

    def wait(self, timeout: float) -> bool:
        return self.finished.wait(timeout=max(0.0, timeout))


class InvestigationDispatcher:
    """JobStore executor로 주입되는 결정적 dispatcher.

    __call__(job)이 executor 인터페이스다. dedup·예산 가드는 동기로 판정하고(위반 시 즉시
    terminal 확정), 통과하면 조사를 **백그라운드 워커**에 넘기고 job을 `running`으로 남긴다
    (JobStore는 running을 진행 중으로 간주). 워커가 동시 상한·전체 타임아웃 하에 조사·후처리를
    수행하고 job을 terminal로 확정한다.
    """

    def __init__(
        self,
        settings: AgentSettings,
        diagnose_fn: DiagnoseFn | None = None,
        briefing_fn: BriefingFn | None = None,
        *,
        prefetch_fn: PrefetchFn | None = None,
        remote: bool = False,
        timeout_seconds: float | None = None,
        prefetch_timeout_seconds: float | None = None,
        audit_path: str | Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        playbook: PlaybookWiring | None = None,
        loaded_tools_fn: Callable[[], Iterable[str] | None] | None = None,
    ) -> None:
        self._settings = settings
        # 조사 에이전트에 로드된 도구(plans/198 재검토 Minor5) — 미연결 소스를
        # 「확인하지 않은 소스」에서 뺀다. None(미주입·agent 미생성)이면 종전대로
        # 기대 소스 전부를 본다.
        self._loaded_tools_fn = loaded_tools_fn
        # 플레이북 배선(plans/187 W4) — None이면 플레이북 설정을 읽지 않는다(종전 동작).
        self._playbook = playbook
        self._diagnose_fn = diagnose_fn
        self._briefing_fn = briefing_fn
        self._prefetch_fn = prefetch_fn
        self._remote = remote
        self._timeout = timeout_seconds if timeout_seconds is not None else float(settings.investigation_timeout_seconds)
        self._prefetch_timeout = (
            prefetch_timeout_seconds
            if prefetch_timeout_seconds is not None
            else max(PREFETCH_TIMEBOX_FLOOR_SECONDS, self._timeout / 2)
        )
        self._audit_path = Path(audit_path) if audit_path is not None else None
        self._clock = clock
        self._wall_clock = wall_clock

        self._semaphore = threading.BoundedSemaphore(max(1, settings.investigation_max_concurrent))
        self._lock = threading.Lock()
        self._dedup: dict[str, float] = {}          # fingerprint -> 마지막 조사 시작 시각(clock)
        # L-4 대상 호스트 부하 가드 — (db_id, host) -> (investigation_id, 획득 시각).
        # **fingerprint dedup과 목적이 다르다**: dedup은 *같은 알람*의 재조사를 TTL로 억제하고,
        # 이쪽은 *서로 다른 알람이라도 같은 호스트*의 **동시** 조사를 막는다. 부하는 곱해지므로
        # 이미 포화된 대상에 조사를 겹쳐 걸면 조사가 장애를 악화시킨다(78 W2-6 · docs/25 L-4).
        self._inflight_hosts: dict[tuple[str, ...], tuple[str, float]] = {}
        self._budget_window: deque[float] = deque()  # 최근 1시간 조사 시작 시각들
        # 플레이북 모드 심층 조사 시작 시각들(최근 1시간) — 예산 강등 판정(plans/187 W4).
        self._deep_window: deque[float] = deque()
        self._workers: set[threading.Thread] = set()
        # 동시 상한 대기 중인 잡(대기 순번 — plans/187 W13 · plans/194 W3에서 기록 유무와 무관하게
        # 모든 대기 잡으로 넓혔다 — `job.queue_position`은 상시, 기록이 있으면 `phase`도).
        self._waiting: list[JobLike] = []
        # 부하 계수(plans/194 W3 · D-347 — lock 보유 중에만 바꾼다).
        # queued: 워커를 만들었고 아직 슬롯을 못 얻은 잡 수(대기 상한 판정 — 가드에서 미리 센다).
        # holding: 슬롯을 쥔 수(살아 있는 워커 + 버려진 조사 스레드를 기다리는 반환 대기).
        # zombie_holding: 그중 버려진 조사 스레드 때문에 붙든 슬롯 수.
        self._queued = 0
        self._holding = 0
        self._zombie_holding = 0
        # 잡별 보조 스레드 종료 신호(조사·사전수집·플레이북 수집기·심층) — 워커 끝에 아직
        # 안 끝난 것이 버려진 스레드다. 키는 investigation_id(심층 잡 사본도 같은 id).
        self._helpers: dict[str, list[threading.Event]] = {}
        # 버려진 뒤 아직 안 끝난 스레드의 종료 신호(게이지 `abandoned_threads` — 끝난 것은
        # 조회 때 정리).
        self._abandoned: list[threading.Event] = []
        # kind별 기대 소스(plans/198 I-10) — 미조회 소스 표기가 켜졌을 때 처음 쓸 때 1회 읽는다.
        self._expected: ExpectedSources | None = None
        self._expected_loaded = False

    # ── executor 진입점 ────────────────────────────────────────

    def __call__(self, job: JobLike) -> None:
        """JobStore가 호출하는 executor. 동기 가드 → (통과 시) 백그라운드 조사 위임."""
        # 스텁 경로(조사 불가): 조사 LLM 게이트 차단(D-230) 또는 조사함수 미주입 → 동기 스텁 확정.
        # 스텁도 폭주 가드(dedup·예산)는 그대로 통과시켜야 하므로 가드 판정을 먼저 한다.
        # 대기 상한(plans/194 W3)은 워커를 만드는 실 조사에만 건다 — 스텁은 슬롯을 쓰지 않는다.
        stub = (self._diagnose_fn is None
                or self._settings.investigation_llm_stub_reason() is not None)
        blocked = self._apply_sync_guards(job, admit=not stub)
        if blocked is not None:
            self._reject(job, blocked)
            return

        if stub:
            self._finalize_stub(job)
            return

        # 실 조사: running 유지 + 백그라운드 워커 위임(submit 즉시 반환).
        # 대기 자리(`_queued`)는 가드가 이미 잡았다 — 워커가 슬롯을 얻거나 끝날 때 돌려준다.
        job.status = "running"
        job.updated_at = self._wall_clock()
        worker = threading.Thread(target=self._worker, args=(job,), daemon=True)
        with self._lock:
            self._workers.add(worker)
        try:
            worker.start()
        except RuntimeError:
            # 스레드 자원 고갈(`can't start new thread`) — 가드가 잡은 대기 자리·호스트 키와 워커
            # 등록을 되돌리지 않으면 대기 상한이 영구히 줄고 그 호스트가 조사 불가로 남는다.
            logger.exception("조사 워커 시작 실패: investigation_id=%s", job.investigation_id)
            with self._lock:
                self._workers.discard(worker)
                self._queued -= 1
            self._release_host(job)
            self._reject(job, GUARD_SERVICE_OVERLOADED)

    def _reject(self, job: JobLike, blocked: str) -> None:
        """잡을 거부(`rejected`)로 확정하고 감사를 남긴다."""
        job.status, job.reason = "rejected", blocked
        job.verdict = self._blocked_verdict(job, blocked)
        job.briefing = {"stub": True, "message": job.verdict, "elements": None}
        job.tool_calls_summary, job.tokens, job.cost = [], 0, 0.0
        job.updated_at = self._wall_clock()
        self._audit({"event": "rejected", "investigation_id": job.investigation_id,
                     "reason": blocked, **self._episode_audit(job)})

    @staticmethod
    def _blocked_verdict(job: JobLike, blocked: str) -> str:
        """거부 사유를 사람이 읽을 문구로 만든다.

        가용성 거부만 **사실을 담아** 돌려준다(G-2: 거부 + 사실 브리핑) — 호출자가 그대로
        사용자에게 보이므로 "왜 조사하지 않았는지"가 문구에 있어야 한다. 다른 사유는
        종전 문구를 그대로 유지한다(회귀 0).
        """
        if blocked != GUARD_TARGET_UNAVAILABLE:
            return f"조사 거부 — {blocked}"
        state = _target_state(job) or {}
        at = f"(확인 시각 {state['as_of']}) " if state.get("as_of") else ""
        return (
            f"조사 거부 — 대상 호스트가 가용하지 않습니다 {at}"
            f"[판정 {state.get('reason') or 'unknown'}]. "
            "가용성 회복 후 다시 요청하십시오."
        ).replace("  ", " ")

    # ── 동기 가드(대상 가용성 · dedup TTL · 시간당 예산) + sweep ──────────────

    def _apply_sync_guards(self, job: JobLike, admit: bool = True) -> str | None:
        """dedup TTL·예산 가드를 판정한다. 통과면 None, 차단이면 사유 문자열.

        통과 시 dedup·예산 상태를 기록한다. in-memory 상태는 값 bound + 키 만료 sweep한다.
        `admit`(실 조사 — 워커를 만든다)이면 대기 상한(plans/194 W3)도 보고, 통과 시 대기 자리를
        같은 lock 안에서 미리 센다(판정과 계수 사이 경합 0).
        """
        now = self._clock()
        with self._lock:
            self._sweep(now)

            # 가용성 가드는 **가장 먼저** 본다 — 거부될 조사가 dedup·예산·in-flight 슬롯을
            # 잡으면 정작 필요한 조사가 그 슬롯에서 밀린다.
            state = _target_state(job)
            if state and str(state.get("state") or "").strip() == "unavailable":
                return GUARD_TARGET_UNAVAILABLE

            fp = job.fingerprint
            ttl = self._settings.investigation_dedup_ttl_seconds
            if fp and ttl is not None:
                last = self._dedup.get(fp)
                if last is not None and (now - last) < ttl:
                    return "dedup_ttl_active"

            budget = self._settings.investigation_hourly_budget
            if budget is not None and len(self._budget_window) >= budget:
                return "hourly_budget_exceeded"

            # L-4 — 같은 호스트를 조사 중이면 거부한다. 직렬화(대기)하지 않는 이유:
            # 조사는 분 단위로 길어(실측 161s) submit을 붙들면 MCP 동기 타임아웃(60s)을 넘긴다.
            # 거부하고 사유를 남기면 호출자가 진행 중인 조사의 브리핑을 받아 쓸 수 있다.
            host_key = _host_key(job, self._settings.cross_source_investigation_enabled)
            if host_key is not None and host_key in self._inflight_hosts:
                return "host_investigation_in_flight"

            if admit:
                full = self._queue_full_reason()
                if full is not None:
                    return full
                self._queued += 1

            # 통과 — 상태 기록.
            if fp and ttl is not None:
                self._dedup[fp] = now
            if budget is not None:
                self._budget_window.append(now)
            if host_key is not None:
                self._inflight_hosts[host_key] = (job.investigation_id, now)
            return None

    def _queue_full_reason(self) -> str | None:
        """대기 상한(plans/194 W3 · D-347) 판정 — 넘치면 사유, 아니면 None(lock 보유 전제).

        슬롯을 쥔 수 + 슬롯 대기 수가 동시 상한 + 대기 상한에 닿으면 새 잡도 대기 줄 끝에
        서야 하므로 거부한다. 슬롯 일부를 버려진 조사 스레드가 붙들고 있으면
        `service_overloaded` — 대기가 많아서가 아니라 끝나지 않는 조사가 자리를 차지한 것이라
        사유를 나눈다.
        """
        limit = self._settings.investigation_max_queued
        if limit is None:
            return None
        capacity = max(1, self._settings.investigation_max_concurrent) + max(0, limit)
        if self._holding + self._queued < capacity:
            return None
        return GUARD_SERVICE_OVERLOADED if self._zombie_holding > 0 else GUARD_QUEUE_FULL

    def _release_host(self, job: JobLike) -> None:
        """L-4 in-flight 키를 해제한다.

        **자기 조사가 잡은 키만 푼다** — 방어적 축출 뒤 다른 조사가 같은 키를 잡았을 수 있고,
        그때 무조건 pop하면 남의 가드를 풀어 버린다.
        """
        key = _host_key(job, self._settings.cross_source_investigation_enabled)
        if key is None:
            return
        with self._lock:
            held = self._inflight_hosts.get(key)
            if held is not None and held[0] == job.investigation_id:
                self._inflight_hosts.pop(key, None)

    def _sweep(self, now: float) -> None:
        """dedup dict(ttl 만료·하드 상한)·budget window(1시간 만료)·끝난 버려진 스레드를 정리한다
        (lock 보유 전제)."""
        ttl = self._settings.investigation_dedup_ttl_seconds
        if ttl is not None:
            expired = [fp for fp, ts in self._dedup.items() if (now - ts) > ttl]
            for fp in expired:
                self._dedup.pop(fp, None)
        # 방어적 하드 상한(가장 오래된 것부터 축출).
        if len(self._dedup) > _MAX_DEDUP_KEYS:
            for fp in sorted(self._dedup, key=self._dedup.get)[: len(self._dedup) - _MAX_DEDUP_KEYS]:
                self._dedup.pop(fp, None)
        # budget window: 1시간 초과분 제거.
        while self._budget_window and (now - self._budget_window[0]) > 3600.0:
            self._budget_window.popleft()
        while self._deep_window and (now - self._deep_window[0]) > 3600.0:
            self._deep_window.popleft()
        self._prune_abandoned()
        # in-flight 호스트: 해제는 워커·스텁 경로가 명시로 하지만, 워커가 죽으면 키가 남는다.
        # 조사 전체 타임아웃의 2배가 지난 항목은 방어적으로 축출한다 — 없으면 그 호스트가
        # **영구히 조사 불가**가 된다(가드가 장애가 되는 형태).
        stale_after = self._timeout * 2
        for key, (_iid, ts) in list(self._inflight_hosts.items()):
            if (now - ts) > stale_after:
                logger.warning("in-flight 호스트 키 방어적 축출(워커 유실 의심): %s", key)
                self._inflight_hosts.pop(key, None)

    # ── 백그라운드 워커(동시 상한 · 전체 타임아웃 · 후처리) ─────

    def _worker(self, job: JobLike) -> None:
        started: float | None = None
        # 슬롯·호스트 키를 버려진 조사 스레드의 반환 대기로 넘겼는가(plans/194 W3) — 넘겼으면 반환은
        # 그쪽(`_hold_for_abandoned`)이 한다.
        handed_off = False
        # 협조적 중단 신호(plans/194 W3 · D-347) — 조사 함수가 잡에서 읽는다
        # (심층 잡 사본도 같은 객체).
        job.cancel_event = threading.Event()  # type: ignore[attr-defined]
        try:
            self._acquire_slot(job)  # 동시 상한
            try:
                if self._skip_if_stale(job):
                    return
                # 계측 기준점 — 동시 상한 대기는 빼고 조사(사전수집 포함)부터 잰다.
                started = self._clock()
                log = _activity(job)
                if log is not None:
                    log.set_phase("collecting")
                self._run_and_postprocess(job, started)
            finally:
                handed_off = self._release_slot(job)
        except BaseException as exc:  # noqa: BLE001 — Exception만 잡으면 잡이 영구 running으로 남는다
            # ★ `Exception`이 아니라 `BaseException`이다 (D-213 근본원인 · 2026-09-11 스택 덤프 확정).
            # `asyncio.CancelledError`는 3.8+에서 **BaseException 파생**이라 `except Exception`을
            # 그대로 통과한다. MCP SSE 상대가 죽으면(mcp_server 종료) anyio 취소 스코프가 이 예외를
            # 올리고, 그러면 워커 스레드가 **감사도 상태 전이도 없이 조용히 죽어** 잡이 영원히
            # running으로 남는다(폐쇄망 실측: 스택 덤프에 조사 스레드가 아예 없었다 — 매달린 게
            # 아니라 죽은 것). 여기는 코루틴이 아니라 평범한 워커 스레드라 취소 의미론을 전파할
            # 상대가 없다 — 잡을 failed로 확정하는 것이 유일하게 옳은 처리다.
            self._log_finalize(job, started, "failed")
            job.status = "failed"
            job.error = f"dispatcher 워커 예외: {type(exc).__name__}: {exc}"
            job.instrumentation = self._run_meta(started)
            job.updated_at = self._wall_clock()
            logger.exception("dispatcher 워커 예외: investigation_id=%s", job.investigation_id)
            self._audit({"event": "failed", "investigation_id": job.investigation_id,
                         "error": job.error, **job.instrumentation, **self._episode_audit(job)})
        finally:
            if not handed_off:
                self._release_host(job)
            with self._lock:
                self._workers.discard(threading.current_thread())

    # ── 버려진 조사 스레드 계상(plans/194 W3 · D-347) ──────────────────────────

    def _track(self, job: JobLike, finished: threading.Event) -> None:
        """잡의 보조 스레드(조사·사전수집·플레이북 수집기·심층) 종료 신호를 등록한다."""
        with self._lock:
            self._helpers.setdefault(job.investigation_id, []).append(finished)

    def _free_slot(self) -> None:
        """동시 상한 슬롯 1개를 돌려준다."""
        with self._lock:
            self._holding -= 1
        self._semaphore.release()

    def _release_slot(self, job: JobLike) -> bool:
        """워커 끝 — 슬롯을 돌려주거나, 아직 안 끝난 보조 스레드가 있으면 반환 대기로 넘긴다.

        타임아웃으로 버린 스레드도 LLM·도구를 계속 부르므로(실부하) 끝날 때까지 슬롯과 L-4 호스트
        키를 붙든다 — 바로 돌려주면 동시 상한을 넘는 조사가 돌고 같은 호스트 새 조사가 통과한다.
        잡 상태는 이미 확정됐다(호출자에게 보이는 상태·시각 불변). 넘기기 전에 협조적 중단 신호를
        세운다(HolmesGPT 스트림이 다음 확인 지점에서 멈춘다). 넘겼으면 True.
        """
        with self._lock:
            events = self._helpers.pop(job.investigation_id, [])
        pending = [ev for ev in events if not ev.is_set()]
        if not pending:
            self._free_slot()
            return False
        cancel = getattr(job, "cancel_event", None)
        if isinstance(cancel, threading.Event):
            cancel.set()
        grace = float(self._settings.investigation_abandon_grace_seconds)
        with self._lock:
            self._zombie_holding += 1
            self._abandoned.extend(pending)
        logger.warning("버려진 조사 스레드 %d개 — 끝날 때까지(최대 %.0fs) 슬롯·호스트 키 유지: "
                       "investigation_id=%s", len(pending), grace, job.investigation_id)
        threading.Thread(
            target=self._hold_for_abandoned, args=(job, pending, grace), daemon=True,
            name=f"sre-abandoned-{str(job.investigation_id)[:8]}",
        ).start()
        return True

    def _hold_for_abandoned(self, job: JobLike, pending: list[threading.Event],
                            grace: float) -> None:
        """버려진 스레드가 끝나거나 `grace`초가 지나면 슬롯·호스트 키를 돌려준다.

        grace를 넘기면 강제 반환하고 감사 `zombie_released`를 남긴다 — 스레드는 데몬이라 계속 돌 수
        있다(게이지 `abandoned_threads`에 남는다). 무한 정체(슬롯 영구 점유) 방지가 목적이다.
        """
        deadline = time.monotonic() + grace
        try:
            for ev in pending:
                ev.wait(max(0.0, deadline - time.monotonic()))
            left = sum(1 for ev in pending if not ev.is_set())
            if left:
                logger.warning("버려진 조사 스레드 %d개가 %.0fs 안에 끝나지 않음 — "
                               "슬롯·호스트 키 강제 반환: investigation_id=%s",
                               left, grace, job.investigation_id)
                self._audit({"event": "zombie_released", "investigation_id": job.investigation_id,
                             "threads": left, "grace_s": grace, **self._episode_audit(job)})
        finally:
            with self._lock:
                self._zombie_holding -= 1
            self._free_slot()
            self._release_host(job)

    def _prune_abandoned(self) -> None:
        """버려진 스레드 목록에서 이미 끝난 것을 뺀다(lock 보유 전제 — 가드 진입·게이지 조회)."""
        self._abandoned = [ev for ev in self._abandoned if not ev.is_set()]

    def load_snapshot(self) -> dict[str, Any]:
        """조사 서비스 부하 게이지(plans/194 W3 — `sre_health` 가산 키 · 관리자 화면 표시용).

        running = 살아 있는 워커가 쥔 슬롯 · queued = 슬롯 대기 잡 · abandoned_slots = 버려진
        스레드 때문에 붙든 슬롯 · abandoned_threads = 버려진 뒤 아직 안 끝난 스레드(강제 반환 뒤에도
        도는 것 포함)와 상한 값들. hourly_budget(plans/194 W5)은 시간당 조사 상한
        (None = 가드 off)이다.
        """
        with self._lock:
            self._prune_abandoned()
            return {
                "running": self._holding - self._zombie_holding,
                "queued": len(self._waiting),
                "abandoned_slots": self._zombie_holding,
                "abandoned_threads": len(self._abandoned),
                "max_concurrent": max(1, self._settings.investigation_max_concurrent),
                "max_queued": self._settings.investigation_max_queued,
                "abandon_grace_seconds": float(self._settings.investigation_abandon_grace_seconds),
                "tool_global_concurrency": self._settings.sre_tool_global_concurrency,
                "hourly_budget": self._settings.investigation_hourly_budget,
            }

    # ── 실행 기록(plans/187 W13 — 잡에 기록이 없으면 전부 무동작) ──────────────

    def _acquire_slot(self, job: JobLike) -> None:
        """동시 상한 슬롯을 얻는다. 기록이 있으면 대기·획득을 `queued`·`started`로 남긴다.

        대기 순번은 대기 잡 사이의 순서(1부터)다 — 세마포어는 엄밀한 FIFO가 아니라 근사다.
        plans/194 W3: 기록 유무와 무관하게 대기 중이면 `job.queue_position`을 단다(조회 응답 상시
        노출). 가드가 미리 센 대기 자리(`_queued`)는 슬롯을 얻든 못 얻든 여기서 돌려준다.
        """
        try:
            self._wait_for_slot(job)
        finally:
            with self._lock:
                self._queued -= 1

    def _wait_for_slot(self, job: JobLike) -> None:
        log = _activity(job)
        if self._semaphore.acquire(blocking=False):
            self._count_slot()
            if log is not None:
                self._after_acquire(
                    lambda: log.append("started", engine="system", label="조사 시작")
                )
            return
        with self._lock:
            self._waiting.append(job)
            self._renumber_waiting()
        try:
            if log is not None:
                safe_append(log, "queued", engine="system", status="waiting",
                            label=f"동시 조사 상한 대기 — 대기 순번 {log.queue_position}")
            waited_from = self._clock()
            self._semaphore.acquire()
            self._count_slot()
        finally:
            with self._lock:
                self._waiting.remove(job)
                job.queue_position = None
                self._renumber_waiting()
        if log is None:
            return
        waited = self._clock() - waited_from
        self._after_acquire(lambda: log.append(
            "started", engine="system", elapsed_ms=int(waited * 1000),
            label=f"동시 조사 상한 대기 종료(대기 {waited:.0f}초)",
        ))

    def _count_slot(self) -> None:
        """슬롯 획득 계수(세마포어 acquire 직후)."""
        with self._lock:
            self._holding += 1

    def _skip_if_stale(self, job: JobLike) -> bool:
        """슬롯을 얻은 직후 — 대기 중 이미 종결됐거나 워치독 임계를 넘긴 잡은 조사하지 않는다.

        동시 상한 대기는 무기한이라, 과부하면 JobStore 워치독이 대기 중인 잡을 `failed(stuck_watchdog)`로
        확정한다. 그 뒤 슬롯을 얻은 워커가 조사를 그대로 돌리면 결과를 받을 쪽이 없고(호출자는 종결을
        보고 떠났다) 종결 상태가 뒤늦게 `done`/`timeout`으로 뒤집히며, 그 동안 뒤의 새 잡이 슬롯을
        기다리다 다시 워치독에 걸린다. 워치독은 조회(submit/get/list)에 편승하므로 조회가 없어 아직
        확정되지 않았으면 같은 임계(`stuck_after_seconds`)로 여기서 확정한다 — 사유·상태 어휘는 워치독과
        같다(D-123 불변). 감사에 `status`를 실어 재기동 복구가 이 잡을 미종결로 오인하지 않게 한다.
        """
        now = self._wall_clock()
        stuck_after = stuck_after_seconds(self._timeout)
        if job.status == "running":
            if (now - job.updated_at) <= stuck_after:
                return False
            job.status = "failed"
            job.reason = STUCK_WATCHDOG_REASON
            job.error = (
                f"워치독 확정: active {stuck_after:.0f}s 초과 — "
                "동시 조사 상한 대기 중 임계 도달(조사 미착수)"
            )
            job.updated_at = now
        logger.warning("대기 중 종결된 잡 조사 생략: investigation_id=%s status=%s reason=%s",
                       job.investigation_id, job.status, job.reason)
        safe_append(_activity(job), "finalize", engine="system", status=job.status,
                    label=f"동시 조사 상한 대기 중 종결({job.status}) — 조사 생략")
        self._audit({"event": "skipped_stale", "investigation_id": job.investigation_id,
                     "status": job.status, "reason": job.reason, "stuck_after_s": stuck_after,
                     **self._episode_audit(job)})
        return True

    def _after_acquire(self, record: Callable[[], object]) -> None:
        """슬롯 획득 직후 기록 — 예외면 슬롯을 돌려주고 다시 올린다(슬롯 누수 방지).

        `_worker`의 release `try/finally`는 `_acquire_slot`이 반환된 뒤에 시작하므로, 그 사이의
        기록 예외는 여기서 반환해야 한다(plans/187 v2 검증 Medium 1).
        """
        try:
            record()
        except BaseException:
            self._free_slot()
            raise

    def _renumber_waiting(self) -> None:
        """대기 순번을 다시 매긴다(lock 보유 전제).

        잡 `queue_position`은 상시, 기록이 있으면 phase도 단다(plans/194 W3).
        """
        for i, waiting in enumerate(self._waiting, 1):
            waiting.queue_position = i
            log = _activity(waiting)
            if log is not None:
                log.set_phase("queued", i)

    def _log_finalize(self, job: JobLike, started: float | None, status: str) -> None:
        """종결 이벤트(`finalize` — 곧 대입할 종결 상태·조사 소요).

        종결 상태 대입 **직전**에 부른다 — 종결을 본 직후 1회 읽기에 `finalize`가 실려야 한다
        (plans/187 v2 검증 Low 2). 종결 경로라 best-effort다(기록 예외로 잡이 running에 남지 않게).
        """
        log = _activity(job)
        if log is None:
            return
        elapsed = int((self._clock() - started) * 1000) if started is not None else None
        safe_append(log, "finalize", engine="system", status=status, elapsed_ms=elapsed,
                    label=f"브리핑 조립 — 종결({status})")

    def _diagnose_for(self, job: JobLike) -> DiagnoseFn:
        """HolmesGPT 조사 함수 — 기록이 있으면 선택 인자 `activity`를 묶는다(없으면 종전 호출)."""
        assert self._diagnose_fn is not None
        log = _activity(job)
        if log is None:
            return self._diagnose_fn
        return functools.partial(self._diagnose_fn, activity=log)

    def _run_and_postprocess(self, job: JobLike, started: float) -> None:
        """전체 타임아웃 하에 조사를 실행하고 severity_judge·briefing으로 후처리한다.

        조사 전에 결정적 사전수집(plans/50 G4)을 돈다 — 결과는 `job.correlation`에 실려 조사 지침
        (`build_guidance`)과 브리핑이 읽는다. 사전수집은 조사를 막지 않는다(실패 → None + 감사).
        `started`는 계측 기준 시각(`self._clock`)이다(plans/154 W1).

        플레이북 배선이 있고 `sre_playbook_enabled`인 알람 잡은 플레이북 경로로 간다(plans/187 W4).
        플레이북을 고르지 못하면(fallback 미적재) 사유를 한계에 싣고 종전 경로로 간다.
        """
        extra_limits: list[str] = []
        if self._playbook_enabled_for(job):
            pb, matched_by = select_playbook(
                alarm_facts_from_payload(job.payload or {}), self._playbook.playbooks  # type: ignore[union-attr]
            )
            if pb is not None:
                self._run_playbook_job(job, started, pb, matched_by)
                return
            extra_limits.append(NO_PLAYBOOK_LIMIT)
            self._audit({"event": "playbook_unmatched", "investigation_id": job.investigation_id})
        prefetch_started = self._clock()
        job.correlation = self._prefetch(job)
        prefetch_s = round(self._clock() - prefetch_started, 3)
        log = _activity(job)
        if log is not None:
            log.set_phase("deep_dive")
            why = HOLMES_PURPOSE + (" — " + NO_PLAYBOOK_LIMIT if extra_limits else "")
            safe_append(log, "deep_start", engine="system", label="HolmesGPT 조사 시작",
                        purpose=why)
        investigate_started = self._clock()
        try:
            result = self._investigate_with_timeout(job)
        except asyncio.TimeoutError:
            # 전체 타임아웃 — 부분 결과 없이 사유를 구조화해 전달(침묵 실패 금지, §12-④).
            safe_append(log, "deep_end", engine="system", status="timeout",
                        elapsed_ms=int((self._clock() - investigate_started) * 1000),
                        label="HolmesGPT 조사 타임아웃")
            self._log_finalize(job, started, "timeout")
            job.status = "timeout"
            job.reason = "investigation_timeout"
            job.verdict = f"조사 타임아웃(미완주) — investigation_timeout_seconds({self._timeout}s) 초과"
            job.briefing = {"stub": True, "message": job.verdict, "elements": None}
            timeout_extra: dict[str, Any] = {}
            if self._settings.partial_briefing_on_incomplete:
                # plans/198 I-10 — stub 대신 실행 기록이 이미 모은 도구 결과로 부분 브리핑.
                # best-effort — 실패하면 stub을 두고 경고만(타임아웃 확정·감사는 그대로).
                try:
                    job.briefing = self._timeout_partial_briefing(job, log)
                    timeout_extra["partial_briefing"] = "timeout"
                except Exception as exc:  # noqa: BLE001 — 부분 브리핑 실패가 timeout을 failed로 바꾸지 않게
                    logger.warning("타임아웃 부분 브리핑 조립 실패 — stub 유지: %s", exc)
            job.tool_calls_summary, job.tokens, job.cost = [], 0, 0.0
            # 조사 스레드는 버려져 부분 토큰·도구 기록은 얻을 수 없다
            # — 얻을 수 있는 키만 싣는다(plans/154 W1).
            job.instrumentation = self._run_meta(started)
            job.updated_at = self._wall_clock()
            self._audit({"event": "timeout", "investigation_id": job.investigation_id,
                         "timeout_s": self._timeout, **job.instrumentation,
                         **self._episode_audit(job), **timeout_extra})
            return

        incomplete = bool(getattr(result, "incomplete", False))
        if log is not None:
            safe_append(log, "deep_end", engine="system",
                        status="incomplete" if incomplete else "done",
                        elapsed_ms=int((self._clock() - investigate_started) * 1000),
                        label="HolmesGPT 조사 종료" + (" — 미완주" if incomplete else ""))
            log.set_phase("finalizing")
        # 미완주면 판정·브리핑 입력은 답변만(종전 동작) — 부분 도구 기록은 계측 키에만 쓴다.
        basis: DiagnosisLike = _AnswerOnly(result.answer) if incomplete else result
        gate_severity, gate_tier = self._gate_context(job)
        verdict = self._run_severity_judge(gate_severity, basis)
        briefing_kwargs: dict = {}
        if getattr(job, "correlation", None):
            briefing_kwargs["correlation"] = job.correlation   # 없을 땐 인자 자체를 넘기지 않는다(종전 호출 동일)
        stalls: list[tuple[str, str, int]] = []
        # plans/188 C2(Q1 A안) — 관측 도구 연결 실패로 LLM 조사를 생략한 결과의 사유 1줄
        # (정상이면 None).
        tools_unavailable = getattr(result, "tools_unavailable", None)
        # plans/188 C7 — 답변에 도구 호출 마크업이 섞이면(vLLM tool-call-parser 불일치)
        # 서술로 쓰지 않고 A안처럼 원문 없이 미결 종결한다. 판정 입력 `basis`는 불변.
        malformed = not tools_unavailable and has_tool_call_markup(result.answer)
        if malformed:
            logger.warning("조사 LLM 답변에 도구 호출 마크업 — 원문 없이 미결 종결: "
                           "investigation_id=%s 앞부분=%r",
                           job.investigation_id, markup_log_excerpt(result.answer))
        if self._settings.apm_guidance_enabled and (tools_unavailable or malformed):
            # A안 — 조사가 없었으므로 APM 대체·정체 가드 한계는 싣지 않는다(소스 라벨만 유지).
            briefing_kwargs["source_labels"] = True
        elif self._settings.apm_guidance_enabled:
            # plans/87 J3 — APM 한계·소스 라벨·정체 가드(사후 판정). off면 인자를 넘기지 않는다(종전 호출 동일).
            stalls = repeated_calls(self._call_keys(basis))
            briefing_kwargs["limitations"] = apm_limitations(
                apm_payloads(self._tool_texts(basis)),
                apm_configured=bool(self._settings.apm_mcp_url),
                apm_incident=is_apm_trigger(job.payload),
                apm_called=any(to.tool_name.startswith("apm_") for to in basis.tool_outputs),
            ) + [stall_limitation(*s) for s in stalls]
            briefing_kwargs["source_labels"] = True
            briefing_kwargs["unresolved"] = bool(stalls)
        _add_limitations(briefing_kwargs, extra_limits)
        # plans/198 I-10 — 정체 중단(가드 on일 때만 값) · 미완주 부분 브리핑 · 미조회 소스.
        stalled = getattr(result, "stalled", None)
        if stalled:
            # 사유는 diagnosis가 가림 콜백으로 조립한다 — 한계·감사로 내보내기 전에 한 번 더(멱등).
            stalled = mask_activity_text(stalled)
            _add_limitations(briefing_kwargs, [stalled])
            briefing_kwargs["unresolved"] = True
        partial_mode = (self._settings.partial_briefing_on_incomplete and incomplete
                        and not tools_unavailable and not malformed)
        # 부분 브리핑은 미완주의 회수 기록을 근거로 쓴다(판정 입력 `basis`는 불변).
        evidence = result.tool_outputs if partial_mode else basis.tool_outputs
        unchecked: list[str] = []
        if not (tools_unavailable or malformed):
            unchecked = self._unchecked_sources(job, [to.tool_name for to in result.tool_outputs])
            line = unchecked_sources_limitation(unchecked)
            _add_limitations(briefing_kwargs, [line] if line else [])
        # plans/188 C3 — 실패 호출은 인용 근거에서 빼고 한계 1줄로 알린다(판정 입력 `basis`는 불변).
        failed_line = failed_tool_limitation(evidence)
        _add_limitations(briefing_kwargs, [failed_line] if failed_line else [])
        # plans/189 W5 — reason 달린 prom_* 0건(성공 응답)도 인용 근거에서 빼고 한계 1줄로 알린다.
        prom_empty_line = prom_empty_limitation(evidence)
        _add_limitations(briefing_kwargs, [prom_empty_line] if prom_empty_line else [])
        if tools_unavailable:
            # A안 브리핑: 요약 `[미결] 조사 불가 …` · 원인 고정 문구 · 사유는 한계 1줄에만.
            briefing_kwargs["investigation_skipped"] = True
            _add_limitations(briefing_kwargs, [tools_unavailable])
        elif malformed:
            # C7 — 요약은 고정 문구(answer 자리), 원인은 전용 문구, 사유는 한계 1줄.
            briefing_kwargs["investigation_skipped"] = True
            briefing_kwargs["skipped_cause"] = MALFORMED_ANSWER_CAUSE
            _add_limitations(briefing_kwargs, [MALFORMED_ANSWER_LIMITATION])
        xs = active_episode(job.payload, self._settings.cross_source_investigation_enabled,
                            getattr(job, "reference_time", None))
        if xs is not None:
            # plans/153 §4.6·§4.7 — 두 소스 멤버가 있는 사건만.
            # off·한쪽 소스면 인자를 넘기지 않는다(종전 호출 동일).
            briefing_kwargs["cross_source"] = xs
            briefing_kwargs["apm_tools_connected"] = bool(self._settings.apm_mcp_url)
        citable = [to for to in evidence
                   if _tool_status(to) in _CITABLE_TOOL_STATUSES and prom_empty_reason(to) is None]
        if self._settings.citation_value_check_enabled and not briefing_kwargs.get(
                "investigation_skipped"):
            # plans/198 I-4 — 인용 근거와 같은 호출만 원장 행으로. 사전수집 상관·알람 페이로드는
            # 서술이 인용할 수 있는 결정적 입력이라 함께 싣는다.
            # off면 인자를 넘기지 않는다(종전 호출 동일).
            extra: list[tuple[str, Any]] = (
                [("alarm.payload", job.payload)] if getattr(job, "payload", None) else [])
            if getattr(job, "correlation", None):
                extra.append(("prefetch.correlation", job.correlation))
            briefing_kwargs["evidence_ledger"] = build_ledger(citable, extra=extra)
            briefing_kwargs["ledger_masker"] = mask_activity_text
        if partial_mode:
            briefing = build_partial_briefing(
                reason="stalled" if stalled else "incomplete",
                verdict=verdict,
                ledger=build_ledger(citable),
                gate_tier=gate_tier,
                remediation=self._recommend_remediation(verdict),
                limitations=briefing_kwargs.get("limitations"),
                correlation=briefing_kwargs.get("correlation"),
                steps=getattr(result, "steps", None),
                ledger_masker=mask_activity_text,
                # 재검토 Minor7 — 크로스소스 블록·소스 라벨도 본 브리핑과 같이 싣는다.
                cross_source=briefing_kwargs.get("cross_source"),
                apm_tools_connected=bool(briefing_kwargs.get("apm_tools_connected")),
                source_labels=bool(briefing_kwargs.get("source_labels")),
            )
        else:
            briefing = self._briefing_fn(
                answer=MALFORMED_ANSWER_SUMMARY if malformed else result.answer,
                verdict=verdict,
                tool_names=[to.tool_name for to in citable],
                gate_tier=gate_tier,
                remediation=self._recommend_remediation(verdict),
                **briefing_kwargs,
            )

        self._log_finalize(job, started, "done")
        job.status = "done"
        job.verdict = f"{verdict.level}(신뢰도 {verdict.confidence}) escalate={verdict.escalate}"
        job.briefing = briefing
        job.tool_calls_summary = list(result.tool_calls)
        job.tokens = result.total_tokens
        job.cost = result.total_cost
        job.instrumentation = self._instrumentation(result, incomplete, started, prefetch_s)
        job.updated_at = self._wall_clock()
        record = {
            "event": "done",
            "investigation_id": job.investigation_id,
            "level": verdict.level,
            "confidence": verdict.confidence,
            "escalate": verdict.escalate,
            "signals": [s.name for s in verdict.signals],
            "tokens": result.total_tokens,
            "cost": result.total_cost,
        }
        if stalls:
            record["unresolved"] = [f"{name} x{n}" for name, _args, n in stalls]  # 정체 가드(미결)만 추가 키
        if tools_unavailable:
            record["tools_unavailable"] = tools_unavailable   # A안만 추가 키(plans/188)
        if malformed:
            record["answer_malformed"] = ANSWER_MALFORMED_TOOL_CALL   # C7만 추가 키(plans/188)
        # plans/198 I-10 — 값이 있을 때만 추가 키(플래그 off면 늘 없다).
        if stalled:
            record["stalled"] = stalled
        if partial_mode:
            record["partial_briefing"] = "stalled" if stalled else "incomplete"
        if unchecked:
            record["unchecked_sources"] = unchecked
        record.update(job.instrumentation)
        record.update(self._episode_audit(job))
        self._audit(record)

    # ── 실패 모드 가드(plans/198 I-10) ────────────────────────────────────────

    def _unchecked_sources(self, job: JobLike, tool_names: Sequence[object]) -> list[str]:
        """kind별 기대 소스 중 호출 0인 소스 — 플래그 off·kind 없음·기대 소스 미적재면 빈 목록.

        kind는 알람 잡만 정한다 — APM 사건(`is_apm_trigger`)이면 `was`, 그 밖은 `host`.
        로드된 도구를 알면 연결되지 않은 소스(도구 0개)는 빼고 본다 — 「조회 안 함」만 남긴다.
        """
        if not self._settings.unchecked_source_limitation_enabled or job.kind != "alarm":
            return []
        if not self._expected_loaded:
            # 적재가 끝난 뒤에 플래그를 세운다 — 동시 첫 잡은 각자 적재한다(멱등 · 1회성 중복).
            # 먼저 세우면 적재 중인 동안 다른 잡이 미적재(None)로 보고 표기를 놓친다.
            try:
                self._expected = load_expected_sources()
            except Exception as exc:  # noqa: BLE001 — 파일·형식·YAML 구문 오류(표기만 생략 · 조사 무영향)
                logger.warning("기대 소스 YAML 적재 실패 — 미조회 소스 표기 생략: %s", exc)
            self._expected_loaded = True
        if self._expected is None:
            return []
        kind = "was" if is_apm_trigger(job.payload) else "host"
        loaded: Iterable[str] | None = None
        if self._loaded_tools_fn is not None:
            try:
                loaded = self._loaded_tools_fn()
            except Exception as exc:  # noqa: BLE001 — 표기만 종전으로(조사 무영향)
                logger.warning("로드된 도구 조회 실패 — 기대 소스 전부로 판정: %s", exc)
        names = [str(n or "") for n in tool_names]
        return unchecked_sources(kind, names, self._expected, loaded=loaded)

    def _timeout_partial_briefing(self, job: JobLike, log: ActivityLog | None) -> dict:
        """타임아웃 잡의 부분 브리핑 — 실행 기록의 HolmesGPT 도구 결과(가림·발췌 상한분)로 원장.

        조사 스레드는 버려져 결과가 돌아오지 않는다. 기록이 없으면 원장 0행이다. 판정은 답변·도구
        기록 없는 입력(게이트 승계)이다 — 부분 기록은 상향 근거로 쓰지 않는다(§12-④와 같다).
        """
        events = log.since(0)["events"] if log is not None else []
        gate_severity, gate_tier = self._gate_context(job)
        verdict = self._run_severity_judge(gate_severity, _AnswerOnly(""))
        line = unchecked_sources_limitation(self._unchecked_sources(
            job, [e.get("tool") for e in activity_tool_events(events)]))
        xs = active_episode(job.payload, self._settings.cross_source_investigation_enabled,
                            getattr(job, "reference_time", None))
        return build_partial_briefing(
            reason="timeout",
            verdict=verdict,
            ledger=ledger_from_activity(events),
            gate_tier=gate_tier,
            remediation=self._recommend_remediation(verdict),
            limitations=[line] if line else [],
            correlation=getattr(job, "correlation", None),
            # 재검토 Minor7 — 본 브리핑과 같은 조건(사건·apm_guidance_enabled)으로 싣는다.
            cross_source=xs,
            apm_tools_connected=xs is not None and bool(self._settings.apm_mcp_url),
            source_labels=bool(self._settings.apm_guidance_enabled),
        )

    # ── 플레이북 모드(plans/187 W4 · §5.0.1) ──────────────────────────────────

    def _playbook_enabled_for(self, job: JobLike) -> bool:
        """플레이북 경로 여부 — 배선이 없으면 설정을 읽지도 않는다(off = 종전 비트 동일)."""
        if self._playbook is None or job.kind != "alarm":
            return False
        return bool(self._settings.sre_playbook_enabled)

    def _deep_mode(self, pb: Playbook) -> tuple[str, str | None]:
        """심층 모드와 강등 사유. YAML `deep_dive.mode`가 전역 `sre_deep_dive_mode`를 재정의한다.

        `parallel`인데 시간당 예산(`investigation_hourly_budget`)을 넘기면 — 최근 1시간 조사 수 +
        심층 수가 예산 초과(심층 1건 = 예산 1단위) — `conditional`로 강등하고 사유를 돌려준다.
        """
        mode = pb.deep_dive.mode or self._settings.sre_deep_dive_mode
        budget = self._settings.investigation_hourly_budget
        if mode != "parallel" or budget is None:
            return mode, None
        with self._lock:
            self._sweep(self._clock())
            runs, deep = len(self._budget_window), len(self._deep_window)
        if runs + deep > budget:
            return "conditional", DEEP_DEGRADE_BUDGET.format(budget=budget, runs=runs, deep=deep)
        return mode, None

    def _start_deep(self, job: JobLike, deep_job: JobLike, mode: str) -> _Background:
        assert self._diagnose_fn is not None
        with self._lock:
            self._deep_window.append(self._clock())
        log = _activity(job)
        if log is not None:
            safe_append(log, "deep_start", engine="system",
                        label=f"심층 조사(HolmesGPT) 시작 — {DEEP_MODE_LABELS.get(mode, mode)}",
                        purpose=DEEP_PURPOSES.get(mode))
        deep = _Background(
            self._diagnose_for(deep_job), deep_job, f"sre-deep-{str(job.investigation_id)[:8]}"
        )
        self._track(job, deep.finished)
        return deep

    def _sop_anchor(self) -> bool:
        """SOP 앵커 플래그(plans/198 I-9) — 설정 대역에 필드가 없으면 off(종전 그대로)."""
        return bool(getattr(self._settings, "sop_anchor_enabled", False))

    @staticmethod
    def _deep_job(job: JobLike, pb: Playbook, prior_findings: dict | None,
                  cache: ToolResultCache, *, sop_anchor: bool = False) -> JobLike:
        """심층 조사 입력 — 잡 사본에 정적 지시(+ conditional이면 판정 요약)만 싣는다.

        `sop_anchor`(plans/198 I-9)면 플레이북 스텝 순서를 조사 계획 앵커로 더한다(정적 지시만).

        `correlation`은 비운다 — 플레이북 결과(S4·S5 상관)는 `prior_findings` 요약으로만 넘기고,
        `parallel`이면 아무 결과도 넘기지 않는다(독립된 두 번째 의견 · §5.0.1 2번).
        잡 단위 도구 캐시는 사본의 `tool_cache`로 실어 HolmesGPT 도구 경로와 공용한다(G-26).
        """
        deep_job = copy.copy(job)
        deep_job.correlation = None
        deep_job.tool_cache = cache  # type: ignore[attr-defined]
        deep_job.deep_dive_guidance = deep_dive_note(  # type: ignore[attr-defined]
            pb.id,
            meaning=pb.runbook.meaning,
            impact=pb.runbook.impact,
            focus=pb.deep_dive.focus,
            prior_findings=prior_findings,
            sop_steps=pb.steps if sop_anchor else None,
        )
        return deep_job

    @staticmethod
    def _prior_findings(run: PlaybookRun, block: dict) -> dict:
        """conditional 심층 입력 — `prior_findings` 형식 판정 요약.

        서술은 넣지 않고, 지침이 같은 화이트리스트로 다시 거른다.
        """
        windows = run.windows or {}
        out: dict = {"version": 1, "source": "playbook", "playbook_id": run.playbook_id}
        if windows.get("reference_time"):
            out["window"] = {
                "reference_time": windows["reference_time"],
                "lookback_minutes": windows.get("lookback_minutes"),
            }
        out["readings"] = dict(block.get("readings") or {})
        out["sections"] = [
            {k: s.get(k) for k in ("step", "status", "verdict", "verdict_source", "tools")}
            for s in block.get("sections") or []
        ]
        out["tools"] = list(dict.fromkeys(c["tool"] for c in run.tool_calls if c.get("tool")))
        return out

    @staticmethod
    def _playbook_block(run: PlaybookRun) -> dict:
        """브리핑 `playbook` 키 — `to_briefing_dict()` + 평탄 판독 + 사건창 + 런북 자리.

        `readings`는 판독 이름 → enum 평탄 맵이다(본체 후속 조사가 그대로 옮긴다 · W10 계약).
        근거·보조 사실은 `readings_detail`로 옮긴다. `scope.incident_window{start, end, minutes}`는
        사건창, `runbook`·`known_errors`는 W8 런북 블록·알려진 오류 판정(§5.7.6).
        """
        block = run.to_briefing_dict()
        detail = block.get("readings") or {}
        block["readings"] = dict(detail.get("values") or {}) if isinstance(detail, dict) else {}
        block["readings_detail"] = detail
        scope = dict(block.get("scope") or {})
        if run.windows:
            scope["incident_window"] = {
                "start": run.windows.get("incident_start"),
                "end": run.windows.get("incident_end"),
                "minutes": run.windows.get("lookback_minutes"),
            }
        block["scope"] = scope
        block["runbook"] = run.runbook
        block["known_errors"] = list(run.known_errors)
        return block

    def _progress_library(self, job: JobLike, pb: Playbook, matched_by: str,
                          deep_state: dict, stop: threading.Event,
                          hooks: _FlowHooks | None = None) -> dict[str, StepFn]:
        """스텝 라이브러리를 감싸 진척(`job.progress`)·부분 브리핑(`job.partial_briefing`)을 남긴다.

        스텝이 끝날 때마다 새 dict로 바꿔 단다(제자리 수정 없음 — 조회 스레드와 경합하지 않는다).
        `stop`이 서면(잡 종료) 남은 스텝은 시작하지 않고 사유만 남긴다. 닫힌 캐시 호출
        (`CacheClosedError`)은 스텝 사유로 남기고 `stop`을 세운다(이후 스텝 시작 0).

        `hooks`(plans/187 W20 — 실행 기록이 켜졌을 때만): `step_start.because`·`step_end.finding`
        (결정적 문장) · 부분 브리핑 `sections[].finding`·`opinion`·건너뜀 항목 · 단계 끝 해석 제출
        (`hooks.stream`). 해석이 도착하면 부분 브리핑을 새 dict로 다시 단다.
        """
        lock = threading.Lock()
        pending: dict[str, list[str]] = {}
        for st in pb.steps:
            pending.setdefault(st.action, []).append(st.id)
        total = len(pb.steps)
        done: dict[str, dict] = {}
        order = [st.id for st in pb.steps]
        focus = {st.id: st.narrate.focus for st in pb.steps if st.narrate is not None}
        log = _activity(job)
        flow = hooks if log is not None else None
        specs = {st.id: st for st in pb.steps}
        finished: dict[str, StepResult] = {}
        findings: dict[str, str] = {}
        # 단계 끝을 이미 낸 (스텝, 시도) — 타임아웃 중단과 늦은 완료 중 먼저 온 것만.
        # 시도는 1(첫 시도)·2(결손 스텝 재시도 · W22)로 나눠, 재시도가 단계 끝을 다시 내도
        # 첫 시도의 늦은 결과는 0건이다.
        ended: set[tuple[str, int]] = set()
        last: dict[str, str | None] = {"current": None}
        event = job.payload.get("event") if isinstance(job.payload, Mapping) else None
        alarm_name = (
            str(event.get("alarmName") or "").strip() or None
            if isinstance(event, Mapping) else None
        )

        def publish(current: str | None) -> None:
            last["current"] = current
            # 판별 스텝(플레이북 밖 ID)은 진척 분자에 넣지 않는다 — 분모가 플레이북 스텝 수다.
            job.progress = {"done": sum(1 for sid in order if sid in done), "total": total,
                            "current_step": current}
            job.partial_briefing = {
                "partial": True,
                "playbook": {
                    "id": pb.id,
                    "version": pb.version,
                    "matched_by": matched_by,
                    "sections": [done[sid] for sid in order if sid in done],
                },
                "deep_dive": dict(deep_state),
            }
            job.updated_at = self._wall_clock()

        def wrap(action: str, fn: StepFn) -> StepFn:
            def run(ctx: StepContext) -> StepResult:
                if stop.is_set():
                    return StepResult(status="missing", reason=STEP_STOPPED_REASON)
                retry_of = getattr(ctx, "retry_of", None)
                with lock:
                    preset = getattr(ctx, "step_id", None)
                    if preset:
                        # 재시도(W22)·판별 스텝(plans/198 재검토 Minor6) — 실행기가 단 스텝 ID
                        # (sid 재배정 없음 · 판별 스텝은 플레이북 밖 ID — 액션 이름으로 뭉개지 않게)
                        sid = preset
                    else:
                        ids = pending.get(action) or [action]
                        sid = ids.pop(0) if len(ids) > 1 else ids[0]
                    publish(sid)
                retry = retry_of is not None
                attempt = 2 if retry else 1
                label = f"{sid} {ACTION_TITLES.get(action, action)}"
                t0 = self._clock()
                if log is not None:
                    ctx.activity, ctx.step_id = log, sid
                    because = (
                        RETRY_BECAUSE.format(first=retry_of)
                        if retry_of is not None else because_of(sid)
                    )
                    safe_append(log, "step_start", engine="playbook", step=sid,
                                label=f"{label} (재시도)" if retry_of is not None else label,
                                purpose=focus.get(sid), because=because)

                def step_end(status: str, finding: str | None = None) -> None:
                    if log is not None:
                        safe_append(log, "step_end", engine="playbook", step=sid, label=label,
                                    status=status, elapsed_ms=int((self._clock() - t0) * 1000),
                                    purpose=focus.get(sid), finding=finding)

                def claim() -> bool:
                    """단계 끝을 낼 차례인가 — 스텝마다 한 번만(중단 통지 · 완료 중 먼저)."""
                    with lock:
                        if (sid, attempt) in ended:
                            return False
                        ended.add((sid, attempt))
                        return True

                def on_abort(reason: str) -> None:
                    """스텝 타임아웃 중단(W21 P4) — 실행기 사유를 단계 끝·부분 브리핑에 싣는다."""
                    if not claim():
                        return
                    out = StepResult(status="missing", reason=reason)
                    finding = finish(sid, action, out, retry)[0] if flow else None
                    step_end("missing", finding)
                    with lock:
                        entry = {
                            "step": sid, "action": action,
                            "title": ACTION_TITLES.get(action, action), "status": "missing",
                            "reason": reason, "tools": list(ctx.tools),
                            **_queries_entry(ctx),
                        }
                        done[sid] = add_flow(sid, entry, finding, "missing")
                        publish(sid)

                ctx.on_abort = on_abort
                cancel = getattr(ctx, "cancel", None)
                if cancel is not None and cancel.is_set():  # 통지 전에 이미 중단 — 그 사유로 끝
                    reason = ctx.abort_reason or STEP_STOPPED_REASON
                    on_abort(reason)
                    return StepResult(status="missing", reason=reason)
                try:
                    res = fn(ctx)
                except CacheClosedError as e:
                    stop.set()
                    logger.info("플레이북 수집 중단 %s(%s): %s", sid, action, e)
                    out = StepResult(
                        status="missing", reason=STEP_CACHE_CLOSED_REASON.format(error=e)
                    )
                    if claim():
                        step_end("missing", finish(sid, action, out, retry)[0] if flow else None)
                    return out
                except Exception as e:  # noqa: BLE001 — 진척 기록 후 원예외 그대로(실행기가 격리)
                    if not claim():
                        raise
                    reason = f"스텝 실패 — {type(e).__name__}: {e}"
                    finding = (
                        finish(sid, action, StepResult(status="missing", reason=reason), retry)[0]
                        if flow else None
                    )
                    step_end("missing", finding)
                    with lock:
                        entry = {
                            "step": sid, "action": action,
                            "title": ACTION_TITLES.get(action, action), "status": "missing",
                            # 재시도 실패는 최종 섹션과 같은 사유(W22 — 실행기 `retry_reason`)
                            "reason": reason if retry_of is None
                            else retry_reason(action, "missing", retry_of, reason),
                            "tools": list(ctx.tools),
                            **_queries_entry(ctx),
                        }
                        done[sid] = add_flow(sid, entry, finding, "missing")
                        publish(sid)
                    raise
                if not claim():
                    return res  # 타임아웃으로 이미 끝난 스텝 — 늦은 결과는 기록·해석하지 않는다
                finding, request = finish(sid, action, res, retry) if flow else (None, None)
                step_end(str(res.status), finding)
                with lock:
                    entry = {
                        **res.to_dict(include_data=False), "step": sid, "action": action,
                        "title": ACTION_TITLES.get(action, action), "tools": list(ctx.tools),
                        **_queries_entry(ctx),
                    }
                    if retry_of is not None:  # 재시도 결과 — 최종 섹션과 같은 사유(W22)
                        entry["reason"] = retry_reason(action, str(res.status), retry_of,
                                                       res.reason)
                    done[sid] = add_flow(sid, entry, finding, str(res.status))
                    publish(sid)
                # 단계 끝 해석 — 락 밖에서 제출한다(통지가 이 락을 잡는다 · 다음 단계를 막지 않음)
                if flow is not None and flow.stream is not None and request is not None:
                    flow.stream.submit(request, functools.partial(on_opinion, sid))
                return res

            return run

        def because_of(sid: str) -> str | None:
            """단계 시작의 「왜」(결정적) — 실패는 문장 없음(기록은 best-effort)."""
            spec = specs.get(sid)
            if flow is None or spec is None:
                return None
            with lock:
                snap, quotes = dict(finished), dict(findings)
            try:
                return step_because(pb, spec, snap, quotes, alarm_name=alarm_name)
            except Exception as e:  # noqa: BLE001 — 문장 실패가 스텝을 막지 않는다
                logger.warning("조사 흐름 「왜」 실패(무시) %s: %s: %s", sid, type(e).__name__, e)
                return None

        def finish(sid: str, action: str, res: StepResult,
                   retry: bool = False) -> tuple[str | None, Any]:
            """단계 끝 — 결과 한 줄 + (소견 대상이면) 해석 요청. 판독은 끝난 스텝만으로(C1).

            `retry`(W22 결손 스텝 재시도)면 그 사이 끝난 뒤 단계 결과가 섞이지 않게 YAML 순서상
            이 스텝 앞의 결과만 판독·앞 단계 결과 한 줄에 넣는다(「이 단계까지의 근거」).
            """
            snapshot = dataclasses.replace(
                res, step=sid, action=action, title=ACTION_TITLES.get(action, action)
            )
            before = set(order[: order.index(sid)]) if retry and sid in order else None
            with lock:
                finished[sid] = snapshot
                snap = dict(finished) if before is None else {
                    s: r for s, r in finished.items() if s in before or s == sid
                }
                prior = [f"{s} {ACTION_TITLES.get(specs[s].action, s)}: {findings[s]}"
                         for s in order if s in findings and s != sid and s in specs
                         and (before is None or s in before)]
            try:
                readings = flow_readings(pb, snap)
                finding = step_finding(snapshot, readings)
                spec = specs.get(sid)
                request = (
                    step_narration_request(spec, snapshot, readings, prior)
                    if spec is not None and flow is not None and flow.stream is not None
                    else None
                )
            except Exception as e:  # noqa: BLE001 — 문장 실패가 스텝을 막지 않는다
                logger.warning("조사 흐름 결과 한 줄 실패(무시) %s: %s: %s",
                               sid, type(e).__name__, e)
                return None, None
            if finding:
                with lock:
                    findings[sid] = finding
            return finding, request

        def add_flow(sid: str, entry: dict[str, Any], finding: str | None,
                     status: str) -> dict[str, Any]:
            """부분 브리핑 항목에 `finding`·`opinion`을 가산한다(기록이 꺼졌으면 그대로)."""
            if flow is None:
                return entry
            if finding:
                entry["finding"] = mask_activity_text(finding)
            spec = specs.get(sid)
            if spec is None or spec.narrate is None:
                return entry  # 소견 대상 아님 — opinion 키 없음
            if status != "ok":
                reason = STEP_STATUS_NO_OPINION.format(status=step_status_label(status))
            elif flow.stream is None:
                reason = flow.no_stream_reason or NOT_CONFIGURED
            else:
                entry["opinion"] = {"status": "pending", "basis": OPINION_BASIS}
                return entry
            entry["opinion"] = {"status": "none", "reason": reason, "basis": OPINION_BASIS}
            return entry

        def on_opinion(sid: str, op: Opinion) -> None:
            with lock:
                if sid not in done:
                    return
                done[sid] = {**done[sid], "opinion": _opinion_view(op)}
                publish(last["current"])

        def on_skip(res: StepResult, because: str) -> None:
            with lock:
                done[res.step] = {
                    "step": res.step, "action": res.action, "title": res.title,
                    "status": "skipped", "because": mask_activity_text(because),
                }
                publish(last["current"])

        if flow is not None:
            flow.on_skip = on_skip
        with lock:
            publish(None)
        return {action: wrap(action, fn) for action, fn in STEP_LIBRARY.items()}

    def _narrator(
        self, remaining: float, activity: ActivityLog | None = None
    ) -> tuple[PlaybookNarrator | None, str | None]:
        """남은 시간으로 소견기 타임아웃을 줄인다. LLM 미배선이면 None(「소견기 미구성」).

        `activity`(plans/187 W13)는 소견기에 넘겨 LLM 호출·소견 초안을 기록하게 한다.
        """
        wiring = self._playbook
        assert wiring is not None
        if wiring.llm is None:
            return None, None
        if remaining <= NARRATOR_MIN_REMAINING_S:
            return None, NARRATOR_SKIPPED_NOTE.format(remaining=max(0.0, remaining))
        base = wiring.narrator_limits
        if remaining < base.step_timeout_s + base.synthesis_timeout_s:
            limits = dataclasses.replace(
                base, step_timeout_s=remaining * 0.4, synthesis_timeout_s=remaining * 0.6
            )
        else:
            limits = base
        return PlaybookNarrator(wiring.llm, limits, activity=activity), None

    def _collect_playbook(self, job: JobLike, pb: Playbook, matched_by: str,
                          cache: ToolResultCache, deadline: float,
                          library: Mapping[str, StepFn], stop: threading.Event,
                          hooks: _FlowHooks | None = None) -> PlaybookRun:
        """플레이북 수집(≤ `sre_playbook_budget_s`) → 남은 시간으로 소견·종합.

        `stop`이 서 있으면(잡 종료 뒤) 소견기·종합을 부르지 않고 수집 결과만 돌려준다.
        `hooks.stream`(W20 단계 끝 해석)이 있으면 마지막 단계 해석까지 기다린 뒤 그 소견을
        그대로 쓰고(재호출 0) 종합만 1회 부른다.
        """
        wiring = self._playbook
        assert wiring is not None
        remaining = max(0.0, deadline - self._clock())
        budget = min(float(self._settings.sre_playbook_budget_s), remaining)
        flow_log = _activity(job) if hooks is not None else None
        # 폴스타 소스 대체 — APM 소스 ID 설정 매핑(W21 P6). 페이로드 infra_db_ids가 우선이고
        # 빈 매핑이면 종전과 같다.
        mapped, basis = mapped_polestar_source(
            job.payload or {}, self._settings.sre_playbook_polestar_source_map
        )
        run = run_playbook(
            job.payload or {}, pb, matched_by=matched_by,
            polestar=wiring.polestar, apm=wiring.apm, clock=self._clock,
            budget_s=budget, cache=cache, steps=library,
            xview_confirm_over=self._settings.apm_xview_confirm_over,  # plans/190 W2
            activity=flow_log, on_skip=hooks.on_skip if hooks is not None else None,
            polestar_source=mapped, polestar_source_basis=basis,
            retry_timed_out=bool(self._settings.sre_playbook_retry_timed_out_steps),
            discriminate_top_n=(  # plans/198 I-2 — off면 0(종전 동일)
                int(self._settings.sre_playbook_discriminator_top_n)
                if self._settings.sre_playbook_discriminator_enabled
                else 0
            ),
        )
        stream = hooks.stream if hooks is not None else None
        if wiring.rejected_files:
            run.notes.append(REJECTED_FILES_NOTE.format(
                n=len(wiring.rejected_files), names=", ".join(wiring.rejected_files)
            ))
        if stop.is_set():
            if stream is not None:
                stream.close()
            return run
        log = _activity(job)
        if log is not None:
            log.set_phase("narrating")
        step_opinions = stream.drain() if stream is not None else None
        narrator, note = self._narrator(deadline - self._clock(), log)
        if note:
            run.notes.append(note)
        if stream is not None and narrator is not None:
            # 같은 소견기로 종합 — 호출 수(`llm_calls`)가 단계 끝 해석까지 센다.
            stream.narrator.limits = narrator.limits
            narrator = stream.narrator
        return finalize_playbook_run(
            run, pb, narrator=narrator, clock=self._clock,
            known_errors=wiring.known_errors, payload=job.payload or {},
            step_opinions=step_opinions,
        )

    @staticmethod
    def _playbook_tool_outputs(run: PlaybookRun) -> list[_ToolText]:
        calls = run.tool_calls
        return [
            _ToolText(tool_name=str(calls[i].get("tool")) if i < len(calls) else "playbook",
                      output=text)
            for i, text in enumerate(run.tool_outputs)
        ]

    def _deep_section(self, mode: str, status: str, reason: str | None, result: Any,
                      verdict: ImportanceVerdict, gate_tier: str | None) -> dict:
        """브리핑 `deep_dive` 키 — 상태 · 모드 · 사유 · (완료 시) 6요소(중요도 제외)."""
        out: dict = {"status": status, "mode": mode, "reason": reason, "label": DEEP_DIVE_LABEL}
        if status != "done" or result is None:
            return out
        skipped = _deep_skip_reason(result)
        if skipped:
            # plans/188 — 툴셋 연결 실패·도구 호출 마크업 답은 서술로 쓰지 않는다(원문 없이 생략 표시).
            out.update({"status": "skipped", "reason": skipped})
            return out
        incomplete = bool(getattr(result, "incomplete", False))
        basis: DiagnosisLike = _AnswerOnly(result.answer) if incomplete else result
        assert self._briefing_fn is not None
        brief = self._briefing_fn(
            answer=result.answer,
            verdict=verdict,
            tool_names=[to.tool_name for to in basis.tool_outputs],
            gate_tier=gate_tier,
            remediation=None,
        )
        out.update({k: v for k, v in (brief or {}).items() if k != "severity"})
        # summary는 답변 첫 줄뿐이라(마크다운 제목이면 제목 한 줄) [서술 보기]용 원문을 따로 싣는다.
        out["narrative"] = _clip_narrative(str(result.answer or ""))
        out["tool_calls"] = list(result.tool_calls)
        out["incomplete"] = incomplete
        return out

    @staticmethod
    def _deep_evidence(result: Any) -> tuple[list[str], list[str]]:
        """일치 표시 입력 — 심층 도구 출력의 매칭 시그니처 이름 · 서술이 인용한 도구."""
        if result is None or bool(getattr(result, "incomplete", False)) or _deep_skip_reason(result):
            return [], []
        texts = [to.output for to in result.tool_outputs] + [
            to.error for to in result.tool_outputs if to.error
        ]
        signals = [s.name for s in match_signatures(texts)]
        signals += [s.name for s in was_signals_from_outputs(texts)]
        answer = str(result.answer or "")
        cited = list(dict.fromkeys(to.tool_name for to in result.tool_outputs
                                   if to.tool_name and to.tool_name in answer))
        return signals, cited

    def _run_playbook_job(
        self, job: JobLike, started: float, pb: Playbook, matched_by: str
    ) -> None:
        """플레이북 모드 조사 1건 — 사전수집 생략 · 두 엔진 · 판정 합집합 · 플레이북 브리핑.

        전체 예산은 종전과 같은 `investigation_timeout_seconds`(기본 600s)이고 플레이북 수집은
        `sre_playbook_budget_s` 상한이다. 심층 조사는 정해진 모드대로 같은 예산 안에서 돈다.
        잡은 두 엔진이 모두 끝날 때까지 `running`이며(소비자 폴링 계약 유지), 그동안 플레이북 결과는
        `partial_briefing`으로 먼저 보인다.
        """
        deadline = started + self._timeout
        # (plans/187 W17) 어떤 플레이북을 왜 골랐는지 — 실행 기록 맨 앞(스텝·심층 시작보다 먼저)
        safe_append(_activity(job), "started", engine="playbook",
                    label=f"플레이북 {PLAYBOOK_LABELS.get(pb.id, pb.id)} 선택",
                    purpose=APM_FALLBACK_PURPOSE if pb.id == APM_FALLBACK_ID
                    else playbook_purpose(matched_by))
        mode, degrade = self._deep_mode(pb)
        # fallback 플레이북(호스트·APM 공통 점검)은 모드와 무관하게 심층을 돈다
        forced = pb.is_fallback or pb.id == APM_FALLBACK_ID
        if rerun_hint(job.payload or {}) is not None:
            mode, degrade, forced = "off", DEEP_SKIP_RERUN, False
        deep_state: dict = {"status": "pending", "mode": mode, "reason": degrade,
                            "label": DEEP_DIVE_LABEL}
        deep: _Background | None = None
        # 잡 단위 캐시 — 플레이북·심층 조사 공용(G-26). 진행 중 호출 대기 상한은 조사 전체 상한에
        # 묶는다(plans/187 W22 · D-341 부기 13 — 모듈 기본값과 설정이 갈라져도 잡보다 오래
        # 기다리지 않는다).
        cache = ToolResultCache(wait_timeout_s=self._timeout)
        if mode == "parallel":
            deep = self._start_deep(job, self._deep_job(
                job, pb, None, cache, sop_anchor=self._sop_anchor()), mode)
            deep_state["status"] = "running"
        stop = threading.Event()
        hooks = self._flow_hooks(job, deadline)
        library = self._progress_library(job, pb, matched_by, deep_state, stop, hooks=hooks)
        try:
            self._playbook_then_deep(job, started, pb, matched_by, deadline, mode, degrade,
                                     forced, deep, deep_state, library, cache, stop, hooks=hooks)
        finally:
            # 잡 종료(정상·예산 초과·예외) — 남은 수집기 스텝을 시작하지 않게 신호부터 세운다.
            stop.set()
            if hooks is not None and hooks.stream is not None:
                hooks.stream.close()  # 종결 뒤 해석 통지 0
            cache.close()  # 잡 단위 캐시 — 잡이 끝나면 버린다(잡 간 누수 0)

    def _flow_hooks(self, job: JobLike, deadline: float) -> _FlowHooks | None:
        """조사 흐름 배선(W20) — 실행 기록이 꺼졌으면 None(종전 경로 비트 동일).

        LLM이 배선돼 있으면 단계 끝 해석 실행기를 단다(없으면 결정적 문장만).
        """
        log = _activity(job)
        if log is None:
            return None
        narrator, note = self._narrator(deadline - self._clock(), log)
        return _FlowHooks(
            stream=StepOpinionStream(narrator) if narrator is not None else None,
            no_stream_reason=note,
        )

    def _playbook_then_deep(self, job: JobLike, started: float, pb: Playbook, matched_by: str,
                            deadline: float, mode: str, degrade: str | None, forced: bool,
                            deep: _Background | None, deep_state: dict,
                            library: Mapping[str, StepFn], cache: ToolResultCache,
                            stop: threading.Event, hooks: _FlowHooks | None = None) -> None:
        """플레이북 수집·종합 → (모드별) 심층 대기·시작 → 종결."""
        collector = _Background(
            lambda j: self._collect_playbook(j, pb, matched_by, cache, deadline, library, stop,
                                             hooks=hooks),
            job, f"sre-playbook-{str(job.investigation_id)[:8]}",
        )
        self._track(job, collector.finished)
        if not collector.wait(deadline - self._clock()):
            stop.set()
            self._finish_timeout(job, started)
            return
        if collector.error is not None:
            raise collector.error
        run: PlaybookRun = collector.result
        job.correlation = run.correlation
        block = self._playbook_block(run)
        gate_severity, gate_tier = self._gate_context(job)
        pb_outputs = self._playbook_tool_outputs(run)

        # 심층 조사 — 모드별 결정
        status, reason = deep_state["status"], degrade
        if deep is None:
            missing = sum(1 for s in run.sections if s.status == "missing")
            synth = run.synthesis or {}
            triggered = forced or bool(synth.get("top_inconclusive")) or missing >= 2
            pinned_ke = [k["id"] for k in run.known_errors if k.get("pinned")]
            if mode == "off" and not forced:
                status, reason = "skipped", degrade or DEEP_SKIP_MODE_OFF
            elif pinned_ke and not forced:
                status = "skipped"
                ke_skip = DEEP_SKIP_KNOWN_ERROR.format(ids=", ".join(pinned_ke))
                reason = f"{degrade} · {ke_skip}" if degrade else ke_skip
            elif not triggered:
                status = "skipped"
                not_triggered = DEEP_SKIP_NOT_TRIGGERED.format(missing=missing)
                reason = f"{degrade} · {not_triggered}" if degrade else not_triggered
            elif deadline - self._clock() < DEEP_MIN_REMAINING_S:
                status = "skipped"
                reason = DEEP_SKIP_TIME.format(remaining=max(0.0, deadline - self._clock()),
                                               floor=DEEP_MIN_REMAINING_S)
            else:
                deep = self._start_deep(
                    job, self._deep_job(job, pb, self._prior_findings(run, block), cache,
                                        sop_anchor=self._sop_anchor()), mode
                )
                status = "running"
        if deep is not None and status == "running":
            # 플레이북이 먼저 끝났다 — 심층이 끝나기 전까지 플레이북 브리핑을 부분 결과로 보인다.
            pending_verdict = self._run_severity_judge(
                gate_severity, _AnswerOnly("", tool_outputs=pb_outputs)
            )
            job.partial_briefing = {
                "partial": True,
                **build_playbook_briefing(
                    playbook=block, verdict=pending_verdict, gate_tier=gate_tier,
                    remediation=self._recommend_remediation(pending_verdict),
                    correlation=run.correlation,
                    deep_dive={"status": "running", "mode": mode, "reason": reason,
                               "label": DEEP_DIVE_LABEL},
                    engine_agreement=compare_engines(
                        block["readings"], unavailable_reason=DEEP_NOT_COMPARABLE_PENDING
                    ),
                ),
            }
            job.updated_at = self._wall_clock()
            log = _activity(job)
            if log is not None:
                log.set_phase("deep_dive")
            if not deep.wait(deadline - self._clock()):
                status, reason = "timeout", (
                    "심층 조사 타임아웃 — 전체 예산 "
                    f"investigation_timeout_seconds({self._timeout}s) 소진"
                )
            elif deep.error is not None:
                status = "failed"
                reason = f"심층 조사 실패 — {type(deep.error).__name__}: {deep.error}"
            else:
                status = "done"
            if log is not None:
                safe_append(log, "deep_end", engine="system", status=status,
                            label=f"심층 조사(HolmesGPT) 종료 — {step_status_label(status)}")
        result = deep.result if (deep is not None and status == "done") else None
        self._finish_playbook(job, started, run, block, pb, mode, status, reason,
                              degrade, result, pb_outputs, cache, gate_severity, gate_tier)

    def _finish_playbook(self, job: JobLike, started: float, run: PlaybookRun, block: dict,
                         pb: Playbook, mode: str, status: str, reason: str | None,
                         degrade: str | None, result: Any, pb_outputs: list[_ToolText],
                         cache: ToolResultCache, gate_severity: int,
                         gate_tier: str | None) -> None:
        """판정(두 엔진 도구 출력 합집합 · G-25) → 플레이북 브리핑 → 잡 종결 · 감사."""
        log = _activity(job)
        if log is not None:
            log.set_phase("finalizing")
        incomplete = bool(getattr(result, "incomplete", False)) if result is not None else False
        deep_outputs = list(result.tool_outputs) if (result is not None and not incomplete) else []
        verdict = self._run_severity_judge(
            gate_severity, _AnswerOnly("", tool_outputs=[*pb_outputs, *deep_outputs])
        )
        deep_dive = self._deep_section(mode, status, reason, result, verdict, gate_tier)
        signals, cited = self._deep_evidence(result)
        agreement = compare_engines(
            block["readings"], signals, cited,
            unavailable_reason=None if result is not None and not incomplete
            else f"심층 조사 {status}" + (f" — {reason}" if reason else ""),
        )
        limits = [degrade] if degrade and status != "skipped" else []
        xs = active_episode(job.payload, self._settings.cross_source_investigation_enabled,
                            getattr(job, "reference_time", None))
        tool_names = [t.tool_name for t in pb_outputs] + [to.tool_name for to in deep_outputs]
        briefing = build_playbook_briefing(
            playbook=block, verdict=verdict, gate_tier=gate_tier,
            remediation=self._recommend_remediation(verdict), limitations=limits,
            correlation=run.correlation, deep_dive=deep_dive, engine_agreement=agreement,
            cross_source=xs, apm_tools_connected=bool(self._settings.apm_mcp_url),
            tool_names=tool_names,
        )
        self._log_finalize(job, started, "done")
        job.status = "done"
        job.verdict = f"{verdict.level}(신뢰도 {verdict.confidence}) escalate={verdict.escalate}"
        job.briefing = briefing
        job.tool_calls_summary = [str(c.get("tool")) for c in run.tool_calls] + (
            list(result.tool_calls) if result is not None else []
        )
        job.tokens = int(getattr(result, "total_tokens", 0) or 0) if result is not None else 0
        job.cost = float(getattr(result, "total_cost", 0.0) or 0.0) if result is not None else 0.0
        if result is not None:
            job.instrumentation = self._instrumentation(result, incomplete, started, 0.0)
        else:
            job.instrumentation = {
                "incomplete": False, **self._run_meta(started), "prefetch_s": 0.0, "steps": None,
                "tool_calls_n": 0, "tool_calls": [],
            }
        job.progress = {"done": len(pb.steps), "total": len(pb.steps), "current_step": None}
        job.partial_briefing = None
        job.updated_at = self._wall_clock()
        record = {
            "event": "done",
            "investigation_id": job.investigation_id,
            "level": verdict.level,
            "confidence": verdict.confidence,
            "escalate": verdict.escalate,
            "signals": [s.name for s in verdict.signals],
            "tokens": job.tokens,
            "cost": job.cost,
            **job.instrumentation,
            # 플레이북 모드 가산 키(plans/187 W4 — on 경로에서만)
            "playbook_id": run.playbook_id,
            "playbook_matched_by": run.matched_by,
            "playbook_llm_calls": run.timings.get("llm_calls", 0),
            "playbook_tool_calls": len(run.tool_calls),
            "deep_dive_mode": mode,
            "deep_dive_status": status,
            "deep_dive_reason": reason,
            "engine_agreement": agreement["result"],
            "cache_real_calls": cache.real_calls,
            "cache_hits": cache.hits,
        }
        record.update(self._episode_audit(job))
        self._audit(record)

    def _finish_timeout(self, job: JobLike, started: float) -> None:
        """전체 타임아웃 확정(종전 경로와 같은 문구·감사)."""
        self._log_finalize(job, started, "timeout")
        job.status = "timeout"
        job.reason = "investigation_timeout"
        job.verdict = (
            f"조사 타임아웃(미완주) — investigation_timeout_seconds({self._timeout}s) 초과"
        )
        job.briefing = {"stub": True, "message": job.verdict, "elements": None}
        job.tool_calls_summary, job.tokens, job.cost = [], 0, 0.0
        job.instrumentation = self._run_meta(started)
        job.partial_briefing = None
        job.updated_at = self._wall_clock()
        self._audit({"event": "timeout", "investigation_id": job.investigation_id,
                     "timeout_s": self._timeout, **job.instrumentation,
                     **self._episode_audit(job)})

    def _run_meta(self, started: float | None) -> dict:
        """종결 공통 계측 키 — 경과 초·조사 모델·step 상한(plans/154 W1).

        `elapsed_s`는 동시 상한 통과 시각부터다(사전수집 포함). 기준점이 없으면 None.
        """
        return {
            "elapsed_s": round(self._clock() - started, 3) if started is not None else None,
            "model": self._settings.model,
            "max_steps": self._settings.max_steps,
        }

    def _instrumentation(
        self, result: DiagnosisLike, incomplete: bool, started: float, prefetch_s: float
    ) -> dict:
        """`done` 계측 가산 키(plans/154 W1 · M-1·M-4·M-5).

        `tool_calls`는 `{name, args_digest, status}`만 싣는다 — 원시 출력·오류 본문·인자 원문은
        크기·민감도 때문에 넣지 않는다. `args_digest`는 정체 가드와 같은 인자 표기
        (키 정렬 JSON — 없으면 holmes 호출 요약)의 sha256 앞 12자라, 같은 인자면 같고 다르면 다르다.
        `steps`는 결과가 실을 때만 값이 있다(없으면 None).
        """
        calls = [
            {
                "name": name,
                "args_digest": hashlib.sha256(args.encode("utf-8")).hexdigest()[:12],
                "status": str(getattr(to, "status", None) or "unknown"),
            }
            for (name, args), to in zip(self._call_keys(result), result.tool_outputs)
        ]
        return {
            "incomplete": incomplete,
            **self._run_meta(started),
            "prefetch_s": prefetch_s,
            "steps": getattr(result, "steps", None),
            "tool_calls_n": len(result.tool_outputs),
            "tool_calls": calls,
        }

    def _episode_audit(self, job: JobLike) -> dict:
        """plans/153 §4.6 감사 키 `episode_id`·`kind`·`cross_source_links`.

        플래그 on이고 `meta.episode`가 있을 때만 붙는다 — off면 빈 dict라 감사 레코드가
        종전과 비트 동일하다. `kind`는 `meta.investigation_kind`(종합 조사 "episode"),
        없으면 잡 종류다.
        """
        if not self._settings.cross_source_investigation_enabled:
            return {}
        ctx = episode_from_payload(getattr(job, "payload", None))
        if ctx is None:
            return {}
        return {
            "episode_id": ctx.episode_id,
            "kind": ctx.investigation_kind or job.kind,
            "cross_source_links": len(ctx.links),
        }

    def _prefetch(self, job: JobLike) -> dict | None:
        """사전수집 콜러블을 격리 실행한다. 미주입·기준시각 없음이면 None(호출 자체를 하지 않는다)."""
        if self._prefetch_fn is None or not getattr(job, "reference_time", None):
            return None
        try:
            # D-213 후속: 사전수집도 타임박스로 감싼다 — 조사 타임박스 **앞의 무가드 구간**이라
            # 죽은 MCP read에 매달리면 전체 타임아웃에 도달조차 못 한다(조사와 같은 wedge 계열).
            correlation = self._join_timebox(
                self._prefetch_fn, job, self._prefetch_timeout, "sre-prefetch"
            )
        except TimeoutError as e:
            logger.warning("증거 사전수집 타임아웃 (%s): %s", job.investigation_id, e)
            self._audit({"event": "prefetch_failed", "investigation_id": job.investigation_id, "error": str(e)})
            return None
        except BaseException as e:  # noqa: BLE001 — CancelledError 포함(D-213: MCP 상대 사망 시 발생)
            logger.warning("증거 사전수집 실패 (%s): %s: %s", job.investigation_id, type(e).__name__, e)
            self._audit({"event": "prefetch_failed", "investigation_id": job.investigation_id, "error": str(e)})
            return None
        if correlation is None:
            return None
        findings = correlation.get("metric_findings") or {}
        self._audit(
            {
                "event": "prefetch",
                "investigation_id": job.investigation_id,
                "leading_signal": correlation.get("leading_signal"),
                "anomalies": sorted(k for k, f in findings.items() if isinstance(f, dict) and f.get("is_anomalous")),
                "alarms": (correlation.get("alarm_summary") or {}).get("count"),
                "notes": len(correlation.get("notes") or []),
            }
        )
        return correlation

    def _join_timebox(self, fn, job: JobLike, timeout: float, name_prefix: str):
        """`fn(job)`을 데몬 스레드에서 실행하고 join 타임박스로 감싼다(D-213 공용).

        만료 즉시 `TimeoutError`(3.11+에서 `asyncio.TimeoutError`와 동일 객체)를 올리고,
        매달린 스레드는 데몬으로 버려진다(결과 폐기). `asyncio.run` 기반과 달리
        정리 단계가 스레드 종료를 기다리지 않는다 — 그 '대기'가 폐쇄망 wedge의 원인이었다.
        plans/194 W3: 버린 스레드는 잡 보조 스레드로 등록돼, 워커가 끝나도 그 스레드가 끝날
        때까지(최대 `investigation_abandon_grace_seconds`) 슬롯·호스트 키를 붙들고 협조적 중단
        신호를 받는다.
        """
        box: dict = {}
        finished = threading.Event()

        def _target() -> None:
            try:
                box["result"] = fn(job)
            except BaseException as exc:  # noqa: BLE001 — 원예외를 호출 스레드로 그대로 전달
                box["error"] = exc
            finally:
                finished.set()

        worker = threading.Thread(
            target=_target,
            daemon=True,
            name=f"{name_prefix}-{str(job.investigation_id)[:8]}",
        )
        worker.start()
        self._track(job, finished)
        if not finished.wait(timeout=timeout):
            raise TimeoutError(f"{name_prefix} 타임박스({timeout}s) 만료 — 스레드 미종료(결과 폐기)")
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def _investigate_with_timeout(self, job: JobLike) -> DiagnosisLike:
        """조사 **전체**를 스레드 join 타임박스로 실행한다(per-call 타임아웃 아님).

        D-213: 종전 `asyncio.run(wait_for(run_in_executor(...)))`는 타임아웃 발화 후
        `asyncio.run`의 정리 단계(`shutdown_default_executor`)가 executor 스레드 종료를
        **무기한 대기**했다 — 조사 스레드가 무한 read(죽은 MCP SSE 등)에 매달리면
        TimeoutError가 밖으로 나오지 못해 timeout 감사도 없이 잡이 영원히 running으로
        남는다(2026-09-09 폐쇄망 실측: 300s 상한에 960s+ running·mcp_server 종료가 원인).
        """
        assert self._diagnose_fn is not None
        return self._join_timebox(self._diagnose_for(job), job, self._timeout, "sre-investigate")

    @staticmethod
    def _tool_texts(result: DiagnosisLike) -> list[str]:
        """판정 입력 — 도구 원시 출력 + 오류 문자열(severity_judge·APM 한계 공용)."""
        texts: list[str] = [to.output for to in result.tool_outputs]
        texts += [to.error for to in result.tool_outputs if to.error]
        return texts

    @staticmethod
    def _call_keys(result: DiagnosisLike) -> list[tuple[str, str]]:
        """정체 가드 입력 `(도구명, 인자 표기)` — 인자는 키 정렬 JSON, 인자가 없으면 holmes 호출 요약(description)."""
        keys: list[tuple[str, str]] = []
        for to in result.tool_outputs:
            params = getattr(to, "params", None)
            if isinstance(params, dict):
                args = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
            else:
                args = str(getattr(to, "description", "") or "")
            keys.append((to.tool_name, args))
        return keys

    def _run_severity_judge(self, gate_severity: int, result: DiagnosisLike) -> ImportanceVerdict:
        """severity_judge_enabled면 도구 원시 출력 시그니처 매칭, 아니면 게이트 승계(상향 없음).

        `apm_signatures_enabled`(plans/87 J3)면 게이트웨이 `was_signals`를 신호로 옮겨 같은 판정에 합친다 —
        WAS 규칙은 재구현하지 않는다(D-274 ⑤). off면 호출이 종전과 같다.
        """
        if not self._settings.severity_judge_enabled:
            return ImportanceVerdict(
                level=LEVEL_NAMES[clamp_level(gate_severity)],
                confidence="none",
                escalate=False,
            )
        texts = self._tool_texts(result)
        if self._settings.apm_signatures_enabled:
            return judge(gate_severity, texts, remote=self._remote,
                         extra_signals=was_signals_from_outputs(texts))
        return judge(gate_severity, texts, remote=self._remote)

    def _recommend_remediation(self, verdict: ImportanceVerdict) -> list[str] | None:
        """remediation_recommender_enabled면 매칭 시그니처에서 조치 후보를 도출한다(제시 전용).

        off면 None을 반환해 briefing_builder의 기존 문구를 유지한다(회귀 0). 켜져 있어도
        매칭 시그니처가 없으면 None — 근거 없는 권고를 만들지 않는다(D-035·§9).
        **실행 경로 없음**: 반환값은 브리핑에 실릴 문자열일 뿐이다(D-003·D-011).
        """
        if not self._settings.remediation_recommender_enabled:
            return None
        lines = recommend_lines(verdict.signals)
        return lines or None

    # ── 스텁(조사 불가) ─────────────────────────────────────────

    def _finalize_stub(self, job: JobLike) -> None:
        """조사 LLM 게이트 차단(플래그 off·키 부재) 또는 조사함수 미주입 시 명시적 스텁 확정(침묵 금지)."""
        message = self._settings.investigation_llm_stub_reason() or "조사 미실행 — dispatcher 조사함수 미주입(스텁)"
        job.status = "stub"
        job.verdict = message
        job.briefing = {"stub": True, "message": message, "elements": None}
        job.tool_calls_summary, job.tokens, job.cost, job.error = [], 0, 0.0, None
        job.updated_at = self._wall_clock()
        self._audit({"event": "stub", "investigation_id": job.investigation_id, "message": message,
                     **self._episode_audit(job)})
        # 스텁 경로는 워커가 돌지 않는다 — 여기서 풀지 않으면 그 호스트가 영구히 조사 불가가 된다.
        self._release_host(job)

    # ── 게이트 컨텍스트 추출 ────────────────────────────────────

    @staticmethod
    def _gate_context(job: JobLike) -> tuple[int, str | None]:
        """잡에서 게이트 severity·tier를 추출한다(alarm만 게이트 존재, diagnosis는 baseline 0)."""
        payload = job.payload or {}
        if job.kind == "alarm":
            event = payload.get("event") or {}
            raw = event.get("severity")
            try:
                severity = int(raw)
            except (TypeError, ValueError):
                severity = 0
            tier = (payload.get("decision") or {}).get("tier")
            return severity, tier
        return 0, None

    # ── 감사(decision JSONL) ───────────────────────────────────

    def _audit(self, record: dict) -> None:
        """토큰 비용·판정 등 decision 레코드를 감사한다(경로 미설정 시 로그로만)."""
        record = {"ts": self._wall_clock(), **record}
        if self._audit_path is None:
            logger.info("dispatcher decision: %s", record)
            return
        try:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self._audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.error("decision JSONL 기록 실패: %s (%s)", self._audit_path, exc)

    # ── 테스트/종료 지원 ───────────────────────────────────────

    def wait_workers(self, timeout: float | None = None) -> None:
        """진행 중인 백그라운드 워커가 모두 끝날 때까지 대기한다(테스트·graceful 종료용)."""
        with self._lock:
            workers = list(self._workers)
        for worker in workers:
            worker.join(timeout)


__all__ = [
    "InvestigationDispatcher",
    "PlaybookWiring",
    "DEEP_DIVE_LABEL",
    "INSTRUMENTATION_KEYS",
    "GUARD_TARGET_UNAVAILABLE",
    "GUARD_QUEUE_FULL",
    "GUARD_SERVICE_OVERLOADED",
    "ToolOutputLike",
    "DiagnosisLike",
    "JobLike",
    "DiagnoseFn",
    "BriefingFn",
]
