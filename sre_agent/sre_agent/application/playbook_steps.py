"""플레이북 스텝 라이브러리 — 액션 ID → 구현 (plans/187 §5.3 · §4.1 · D-341).

플레이북 YAML은 액션 ID만 쓰고, 관측 제품 어휘(도구명·응답 키)는 이 모듈까지만 둔다(§5.2 — 벤더
중립).
스텝은 결정적이다 — LLM을 부르지 않고, 판정은 게이트웨이 `was_signals`를 인용하거나(D-274 ⑤)
아래에 적은 결정적 규칙만 쓴다. 판독 규칙 R-A~R-H(enum)는 W3가 `data`(원자료)로 계산한다.

- 스텝 함수 시그니처: `StepFn = (StepContext) -> StepResult`. 실행기(`playbook_runner`)가
  `step`·`action`·`title`·`tools`·`elapsed_ms`를 채운다.
- 도구 호출은 `StepContext.call(axis, calls)`로만 한다 — 축(apm·polestar)별 배치 콜러블(잡 단위
  캐시를 거친 것)을 쓰고, 호출한 도구명·원시 출력을 기록한다(G-25 중요도 판정 입력).
- 결손은 `missing`(도구명 또는 지표 종류)과 `reason`으로 남긴다(침묵 금지). 폴스타 소스를 결정적으로
  정할 수 없으면 폴스타 축을 호출하지 않고 「폴스타 서버 미확정」으로 남긴다(§8 위험 1 ·
  plans/165 F3).
- S4·S5·S7(폴스타 변경)은 사전수집 `evidence_prefetch`의 같은 함수(`build_calls`·`_series`·
  `_alarms`·`_changes`)와 `correlation.correlate`를 재사용한다(G-24).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from statistics import mean
from typing import Any, Literal

from sre_agent.application.evidence_prefetch import (
    METRIC_KINDS,
    TOOL_ALARMS,
    TOOL_CHANGES,
    TOOL_METRIC,
    EvidenceScope,
    _alarms,
    _changes,
    _series,
    build_calls,
)
from sre_agent.domain.activity_log import ActivityLog, clip_bytes, safe_append
from sre_agent.domain.correlation import EpisodePoint, MetricSeries, correlate
from sre_agent.domain.playbook_readings import was_cpu_top_instance
from sre_agent.domain.source_adapter import (
    DEFAULT_REGISTRY,
    DPM,
    ITSM,
    NOT_CONNECTED,
    AdapterRegistry,
    fetch_or_not_connected,
)
from sre_agent.domain.step_findings import step_status_label, tool_phrase
from sre_agent.infrastructure.log_masking import mask_secrets

logger = logging.getLogger(__name__)

BatchCaller = Callable[[Sequence[tuple[str, dict]]], list[str]]
Axis = Literal["apm", "polestar"]
StepStatus = Literal["ok", "missing", "skipped", "pending_synthesis"]

# ── 도구명 (제품 어휘는 여기에만) ────────────────────────────────────────────
APM_INSTANCE_MAP = "apm_instance_map"
APM_APP_HEALTH = "apm_app_health"
APM_PERIOD_COMPARE = "apm_period_compare"
APM_RUNTIME_HEALTH = "apm_runtime_health"
APM_RESOURCE_POOL = "apm_resource_pool"
APM_SLOW_TX = "apm_slow_transactions"
APM_TX_PROFILE = "apm_transaction_profile"
APM_EVENTS = "apm_events"
APM_ACTIVE_SERVICES = "apm_active_services"
APM_ACTIVE_DETAIL = "apm_active_detail"
APM_SOURCE_CHANGES = "apm_source_changes"
APM_CHANGE_IMPACT = "apm_change_impact"
POLESTAR_PROCESS = "polestar_process_snapshot"
POLESTAR_RESOURCE_STATUS = "polestar_resource_status"
POLESTAR_TOPOLOGY = "polestar_topology"
POLESTAR_ALARM_HISTORY = "polestar_alarm_history"

#: 폴스타 소스 미확정 표기(§8 위험 1) — 폴스타 축 결손 사유의 머리말.
POLESTAR_UNRESOLVED = "폴스타 서버 미확정"
#: S5 고정 문구 — 시간 근접은 원인 근거가 아니다(SRE 12장).
CORRELATION_NOT_CAUSATION = (
    "상관 ≠ 인과 — 시간상 가까운 알람·이벤트는 원인 후보일 뿐 원인 근거가 아니다"
)
#: S7 고정 문구.
CHANGE_CANDIDATE_NOTE = "변경은 「상관 후보」로만 표기한다 — 원인 확정이 아니다"
# 미연동 외부 소스 사유 `NOT_CONNECTED`는 `domain.source_adapter`가 정본이다(G-6 · §6).

#: 판정 출처 — 게이트웨이 `was_signals` 인용 / 이 모듈의 결정적 규칙.
VERDICT_SOURCE_GATEWAY = "apm_gateway"
VERDICT_SOURCE_RULE = "playbook_rule"
#: 역방향 대상 확정(`scope.host_to_was`)에서 이 호스트에 WAS가 없다는 판정(W4 후속 ③).
NO_WAS_VERDICT = "no_was"
#: [제안] p95가 기준선(전일·전주 평균)의 이 배수 이상이면 degraded — 스텝 인자 `p95_ratio`로 바꾼다.
DEFAULT_P95_RATIO = 2.0
#: 정합 신뢰도 중 「대상 확정 실패」로 보는 값(게이트웨이 어휘 high·medium·none + 방어적 low).
LOW_CONFIDENCE = frozenset({"", "none", "low"})

# ── APM 호출 인자 규칙(plans/190 W1·W2 · D-344) ─────────────────────────────
#: `source_ids`를 받는 게이트웨이 도구(W0 실측 — `apm_gateway/interface/server.py` 시그니처 ·
#: D-274 ③ 사본). `apm_transaction_profile`은 단수 `source_id`(profile_ref 그대로)라 없다.
APM_SOURCE_IDS_TOOLS = frozenset(
    {
        APM_INSTANCE_MAP,
        APM_APP_HEALTH,
        APM_RUNTIME_HEALTH,
        APM_RESOURCE_POOL,
        APM_SLOW_TX,
        APM_ACTIVE_SERVICES,
        APM_EVENTS,
        "apm_status_stats",
        "apm_metrics",
        APM_SOURCE_CHANGES,
        "apm_transaction_trace",
        APM_CHANGE_IMPACT,
        APM_PERIOD_COMPARE,
    }
)
#: 옵트인 `xview_confirm_over`를 받는 도구(W0 실측 — `apm_app_health`뿐).
APM_XVIEW_CONFIRM_TOOLS = frozenset({APM_APP_HEALTH})
#: 게이트웨이 사전 확인 봉투의 `status`(apm_gateway `CONFIRM_REQUIRED` 사본 · D-274 ③).
XVIEW_CONFIRM_REQUIRED = "confirm_required"
#: 브리핑 한계 — 대상의 APM 소스를 정하지 못해 소스를 좁히지 않고 불렀다(W1).
APM_SOURCE_UNRESOLVED_LIMIT = "APM 소스 미확정 — 전 소스 조회"
#: 브리핑 한계 — X-View 사전 확인 초과로 구간 조회를 시 단위 통계로 대체했다(W2).
XVIEW_CONFIRM_LIMIT = (
    "X-View 사전 확인 초과(예상 {n}회 > 상한 {m}) — 구간 p95·에러율 생략, 시 단위 통계로 대체"
)

ACTION_TITLES: dict[str, str] = {
    "scope.resolve_was_targets": "대상 확정 · 범위 비교",
    "scope.host_to_was": "호스트의 WAS 인스턴스 영향",
    "apm.golden_signals": "WAS 골든 시그널",
    "apm.runtime_and_pool": "JVM 런타임 · DB 커넥션 풀",
    "apm.slow_tx_breakdown": "느린 트랜잭션 Top 시간 분해",
    "host.usage_trend": "호스트 자원 시간대별 추이",
    "xsource.related_alarms": "전후 연관 알람",
    "host.process_top": "프로세스 CPU 상위",
    "change.recent": "최근 변경 대조",
    "dpm.db_health": "DB 상태(DPM)",
    "synthesize": "종합",
    "apm.events_errors": "오류 유형 상위",
    "apm.active_services": "액티브 서비스",
    "apm.active_sql": "실행 중 SQL",
    "host.resource_status": "자원 가용 상태",
    "host.topology": "토폴로지",
    "host.alarm_history": "같은 알람 재발 이력",
}


# ── 실행 문맥 · 결과 ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BaselineWindow:
    """기준선 창 1개(전일·전주 동시간)."""

    name: str  # prev_day | prev_week
    start: str  # ISO(naive)
    end: str
    reference_time: str  # 그 창의 도구 기준 시각(= end)


@dataclass(frozen=True)
class RunWindows:
    """시간창 — 기준 시각 = 알람 시각, 사건창 = 알람 −before ~ +after(§4.0)."""

    alarm_time: str
    reference_time: str  # 도구 기준 시각 = 사건창 끝
    lookback_minutes: int  # before + after
    incident_start: str
    incident_end: str
    baselines: tuple[BaselineWindow, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunScope:
    """조사 범위 — S0(또는 페이로드)가 정한 대상. 이후 스텝의 입력이다."""

    alarm_instance: str | None = None
    host: str | None = None
    polestar_source: str | None = None
    polestar_server: str | None = None
    polestar_reason: str | None = None  # 폴스타 소스 미확정 사유(확정이면 None)
    alarm_name: str | None = None
    targets: list[dict[str, Any]] = field(default_factory=list)
    #: 이 호스트의 WAS 인스턴스 중 판정이 난 것 전부(역방향 S0′ 순서 · `for_each: host_was` 입력 ·
    #: plans/187 W20b). 대상(`targets`)과 따로 둔다 — 대상 소비 스텝(S5·S7) 동작 비트 동일.
    #: 브리핑 범위(`to_dict`)에는 싣지 않는다.
    host_was: list[str] = field(default_factory=list)
    #: 폴스타 소스를 설정 매핑으로 대체했을 때의 근거 문장(plans/187 W21 P6). None이면 브리핑
    #: 범위(`to_dict`)에 키 자체를 싣지 않는다(종전 모양 그대로).
    polestar_basis: str | None = None
    #: 폴스타 미확정 사유의 운영자 상세(필드명·계획 번호 — plans/196 D6). `polestar_reason`은
    #: 사용자 문장이다. None이면 브리핑 범위에 키를 싣지 않는다.
    polestar_reason_detail: str | None = None
    #: 페이로드 `meta.hints.polestar_link`를 실제로 쓴 상태(`linked`·`absent`·`unresolved` —
    #: plans/196 §5.2). None이면 힌트 미사용(종전 동작)이고 브리핑 범위에 키를 싣지 않는다.
    polestar_link: str | None = None

    def target_names(self) -> list[str]:
        return [name for name, _ in self.target_entries()]

    def target_entries(self) -> list[tuple[str, str | None]]:
        """대상 `(인스턴스명, APM 소스 id)` — 소스는 대상 확정 결과 행의 `source_id`(없으면 None
        · plans/190 W1). 대상이 없으면 알람 인스턴스 단독(소스 미확정)."""
        entries = [
            (str(t["instance"]), str(t.get("source_id") or "") or None)
            for t in self.targets
            if t.get("instance")
        ]
        if not entries and self.alarm_instance:
            entries = [(self.alarm_instance, None)]
        return entries

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out.pop("host_was", None)
        # 운영자 상세는 직렬화하지 않는다(D6 — 섹션 `reason_detail`·서버 로그로만)
        out.pop("polestar_reason_detail", None)
        for key in ("polestar_basis", "polestar_link"):
            if out.get(key) is None:
                out.pop(key, None)
        return out


@dataclass
class StepResult:
    """스텝 결과 1건(§5.5 `sections[]`).

    status:   ok(부분 성공 포함 — 결손은 `missing`) · missing · skipped · pending_synthesis(S9
    자리).
    verdict:  degraded·normal·None(판정 불가) — 출처는 `verdict_source`.
    table:    표시용 `{"columns": [...], "rows": [[...]]}`.
    data:     판독 규칙(W3) 입력 원자료 — JSON 직렬화 가능한 값만.
    missing:  결손된 도구명(S4는 지표 종류).
    """

    step: str = ""
    action: str = ""
    title: str = ""
    status: StepStatus = "ok"
    verdict: str | None = None
    verdict_source: str | None = None
    table: dict[str, Any] | None = None
    data: dict[str, Any] = field(default_factory=dict)
    badges: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    reason: str | None = None
    elapsed_ms: int = 0
    #: 직렬화하지 않는 객체(상관 계산 재료 — MetricSeries·AlarmPoint 등).
    artifacts: dict[str, Any] = field(default_factory=dict, repr=False)
    #: 사유의 운영자 상세(필드명·계획 번호 — plans/196 D6 · §5.3). `reason`은 사용자 문장이다.
    #: None이면 직렬화에 키를 싣지 않는다(가산 키).
    reason_detail: str | None = None
    #: 재조회(plans/196 §5.2 `rerun`)에서 부모 조사 결과를 그대로 쓴 섹션의 부모 조사 id.
    #: None이면 직렬화에 키를 싣지 않는다(가산 키).
    reused_from: str | None = None
    #: 축별 상태 `{축: 상태}`를 그대로 쓸 값(재조회 재사용 섹션이 부모 값을 옮긴다 — plans/196 D7).
    #: None이면 직렬화가 액션·`data["axes"]`로 계산한다(`section_axis_status`).
    axis_status: dict[str, str] | None = None

    def to_dict(self, *, include_data: bool = True) -> dict[str, Any]:
        out = {
            "step": self.step,
            "action": self.action,
            "title": self.title,
            "status": self.status,
            "verdict": self.verdict,
            "verdict_source": self.verdict_source,
            "table": self.table,
            "badges": list(self.badges),
            "tools": list(self.tools),
            "missing": list(self.missing),
            "reason": self.reason,
            "elapsed_ms": self.elapsed_ms,
        }
        if self.reason_detail is not None:
            out["reason_detail"] = self.reason_detail
        if self.reused_from is not None:
            out["reused_from"] = self.reused_from
        axis_status = (
            self.axis_status
            if self.axis_status is not None
            else section_axis_status(self.action, self.status, self.data)
        )
        if axis_status:
            out["axis_status"] = dict(axis_status)
        if include_data:
            out["data"] = self.data
        return out


@dataclass(frozen=True)
class ToolResponse:
    """도구 응답 1건 — 성공이면 `data`, 실패면 `error`(사유 문장)."""

    tool: str
    args: dict[str, Any]
    data: dict[str, Any] | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.data is not None


def parse_response(tool: str, args: dict, raw: str) -> ToolResponse:
    """원시 JSON → `ToolResponse`. 게이트웨이 오류는 `코드: 사유`, 폴스타 오류는 문장 그대로."""
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return ToolResponse(tool, args, None, "응답 JSON 파싱 실패")
    if not isinstance(obj, dict):
        return ToolResponse(tool, args, None, "응답 형식 오류")
    if obj.get("error"):
        reason = obj.get("reason")
        msg = f"{obj['error']}: {reason}" if reason else str(obj["error"])
        return ToolResponse(tool, args, None, msg)
    return ToolResponse(tool, args, obj, None)


#: 스텝 타임아웃으로 중단된 뒤 스텝이 내려던 호출의 오류(호출하지 않음 · plans/187 W21 P4).
STEP_ABORTED = "스텝 타임아웃으로 중단"


class StepContext:
    """스텝 1회 실행 문맥. 스텝마다 새로 만든다(호출 기록이 스텝별).

    중단(plans/187 W21 P4): 실행기가 스텝 타임아웃에 `abort(사유)`를 부르면 `cancel`이 서고, 이후
    `call()`은 도구를 부르지 않고 `STEP_ABORTED` 오류 응답을 돌려준다(타임아웃 스텝이 다음 단계와
    게이트웨이를 경합하지 않게). 진행 계수(요청·완료 건수 · 대기 중 도구)는 사유 문장 재료다.
    """

    def __init__(
        self,
        *,
        payload: Mapping[str, Any],
        windows: RunWindows,
        scope: RunScope,
        args: Mapping[str, Any],
        targets: Sequence[str],
        polestar: BatchCaller | None,
        apm: BatchCaller | None,
        now: Callable[[], datetime],
        prior: Mapping[str, StepResult],
        adapters: AdapterRegistry | None = None,
        target_sources: Sequence[str | None] | None = None,
        xview_confirm_over: int | None = None,
    ) -> None:
        self.payload = payload
        self.windows = windows
        self.scope = scope
        self.args = dict(args)
        self.targets = list(targets)
        #: `targets`와 같은 순서의 APM 소스 id(plans/190 W1 — 미주입·None = 소스 미확정).
        sources = list(target_sources or [])
        self.target_sources: list[str | None] = (
            sources + [None] * (len(self.targets) - len(sources))
        )[: len(self.targets)]
        #: 기간 모드 `apm_app_health`의 X-View 사전 확인 상한(W2 — None·0 이하 = 인자 미전송).
        self.xview_confirm_over = (
            int(xview_confirm_over)
            if xview_confirm_over is not None and int(xview_confirm_over) > 0
            else None
        )
        #: 브리핑 한계 문장(실행기가 `PlaybookRun.notes`로 모은다 · 순서 보존 · 중복 제거).
        self.limits: list[str] = []
        self.polestar = polestar
        self.apm = apm
        self.now = now
        self.prior = prior
        #: 외부 소스 어댑터(ITSM·DPM — §6). 미주입이면 전부 미연동 스텁.
        self.adapters = DEFAULT_REGISTRY if adapters is None else adapters
        self.tools: list[str] = []
        self.calls: list[tuple[str, dict]] = []
        self.outputs: list[str] = []
        #: 실행 기록(plans/187 W13) — dispatcher가 스텝 실행 직전에 단다. None이면 기록 0.
        self.activity: ActivityLog | None = None
        self.step_id: str | None = None
        #: 중단 신호 · 사유 · 중단 통지(dispatcher가 단다 — 실행 기록 `step_end`용 · None이면
        #: 무동작).
        self.cancel = threading.Event()
        self.abort_reason: str | None = None
        #: 실행 시작 신호·시각(실행기가 스텝 대기 상한을 시작 시각부터 잰다 — W21).
        self.started = threading.Event()
        self.started_at: float | None = None
        self.on_abort: Callable[[str], None] | None = None
        #: 결손 스텝 재시도(plans/187 W22)의 첫 시도 사유 — 재시도 문맥이면 실행기가
        #: `step_id`와 함께 미리 단다(dispatcher가 sid를 다시 배정하지 않고 재시도 표시로
        #: 단계 시작·끝을 낸다).
        self.retry_of: str | None = None
        self._progress_lock = threading.Lock()
        self.requested = 0
        self.completed = 0
        self.inflight: list[str] = []

    def progress_text(self) -> str:
        """진행 계수 문장 — 「요청 N건 중 M건 완료 · 대기 도구…」(대기 없으면 뒤를 뺀다)."""
        with self._progress_lock:
            text = f"요청 {self.requested}건 중 {self.completed}건 완료"
            waiting = list(dict.fromkeys(self.inflight))
        return f"{text} · 대기 {', '.join(waiting)}" if waiting else text

    def abort(self, reason: str) -> None:
        """스텝을 중단한다 — 사유를 남기고 신호를 세운 뒤 중단 통지를 부른다(통지 실패는 무시)."""
        self.abort_reason = reason
        self.cancel.set()
        if self.on_abort is None:
            return
        try:
            self.on_abort(reason)
        except Exception as e:  # noqa: BLE001 — 통지(실행 기록) 실패가 실행기를 막지 않는다
            logger.warning(
                "스텝 중단 통지 실패(무시) %s: %s: %s", self.step_id, type(e).__name__, e
            )

    @property
    def event(self) -> Mapping[str, Any]:
        ev = self.payload.get("event") if isinstance(self.payload, Mapping) else None
        return ev if isinstance(ev, Mapping) else {}

    def call(self, axis: Axis, calls: Sequence[tuple[str, dict]]) -> list[ToolResponse]:
        """축의 배치 콜러블로 calls를 부른다. 콜러블이 없으면 전부 오류 응답(호출하지 않음).

        실행 기록이 달려 있으면(plans/187 W13) 호출 전 `tool_start`, 후 `tool_end`(상태 ·
        배치 소요 · 행 수 · 발췌)를 남긴다. 잡 캐시 적중은 `status=cached`다.
        중단(`cancel`)됐으면 부르지도 기록하지도 않고 `STEP_ABORTED` 오류 응답만 돌려준다(W21 P4).
        """
        if not calls:
            return []
        if self.cancel.is_set():
            return [ToolResponse(t, dict(a), None, STEP_ABORTED) for t, a in calls]
        caller = self.apm if axis == "apm" else self.polestar
        if caller is None:
            msg = "APM 게이트웨이 미구성" if axis == "apm" else "폴스타 MCP 미구성"
            out_missing = [ToolResponse(t, dict(a), None, msg) for t, a in calls]
            for r in out_missing:
                self._record_end(r, None, "error", None, msg)
            return out_missing
        cached = self._record_start(caller, calls)
        names = [t for t, _ in calls]
        with self._progress_lock:
            self.requested += len(calls)
            self.inflight.extend(names)
        t0 = time.monotonic()
        try:
            raws = list(caller(list(calls)))
        finally:
            with self._progress_lock:
                for name in names:
                    self.inflight.remove(name)
        with self._progress_lock:
            self.completed += len(calls)
        if self.cancel.is_set():
            # 중단 뒤 돌아온 배치 — 계수만 갱신하고 기록·적재는 하지 않는다(W21 교정)
            return [ToolResponse(t, dict(a), None, STEP_ABORTED) for t, a in calls]
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        if len(raws) < len(calls):
            raws += [json.dumps({"error": "응답 누락"})] * (len(calls) - len(raws))
        out: list[ToolResponse] = []
        for i, ((tool, args), raw) in enumerate(zip(calls, raws)):
            if tool not in self.tools:
                self.tools.append(tool)
            self.calls.append((tool, dict(args)))
            self.outputs.append(raw)
            resp = parse_response(tool, dict(args), raw)
            out.append(resp)
            if self.activity is not None:
                status = "cached" if cached[i] else ("success" if resp.ok else "error")
                self._record_end(resp, elapsed_ms, status, _rows(resp.data), raw)
        return out

    def _record_start(self, caller: BatchCaller, calls: Sequence[tuple[str, dict]]) -> list[bool]:
        """`tool_start`를 남기고 호출 전 캐시 적중 여부를 돌려준다(기록이 없으면 전부 False)."""
        if self.activity is None:
            return [False] * len(calls)
        probe = getattr(caller, "cached", None)
        hits: list[bool] = []
        for tool, args in calls:
            try:
                hits.append(bool(probe(tool, args)) if callable(probe) else False)
                label = tool_label(tool, args)
            except Exception as exc:  # noqa: BLE001 — 기록은 best-effort
                logger.warning("실행 기록 실패(무시) tool=%s: %s: %s",
                               tool, type(exc).__name__, exc)
                hits.append(False)
                continue
            safe_append(self.activity, "tool_start", engine="playbook", step=self.step_id,
                        tool=tool, label=label, phrase=tool_phrase(tool, args))
        return hits

    def _record_end(self, resp: ToolResponse, elapsed_ms: int | None, status: str,
                    rows: int | None, excerpt: str) -> None:
        if self.activity is None:
            return
        try:
            label = tool_label(resp.tool, resp.args)
        except Exception as exc:  # noqa: BLE001 — 기록은 best-effort
            logger.warning("실행 기록 실패(무시) tool=%s: %s: %s",
                           resp.tool, type(exc).__name__, exc)
            return
        if resp.tool == APM_ACTIVE_DETAIL:
            excerpt = _mask_detail_excerpt(resp, excerpt)
        safe_append(
            self.activity, "tool_end", engine="playbook", step=self.step_id, tool=resp.tool,
            label=label, status=status, elapsed_ms=elapsed_ms, rows=rows, excerpt=excerpt,
            phrase=tool_phrase(resp.tool, resp.args),
        )

    def call_one(self, axis: Axis, tool: str, args: dict) -> ToolResponse:
        return self.call(axis, [(tool, args)])[0]

    def target_items(self) -> list[tuple[str, str | None]]:
        """`(대상 인스턴스명, APM 소스 id)` — `targets`·`target_sources` 짝."""
        return list(zip(self.targets, self.target_sources))

    def add_limit(self, text: str) -> None:
        if text not in self.limits:
            self.limits.append(text)


StepFn = Callable[[StepContext], StepResult]


def _mask_sql_values(value: Any, key: str = "") -> Any:
    """응답의 SQL 값(키 이름에 `sql`)만 `mask_active_sql`로 가린다(나머지 값·키는 그대로)."""
    if isinstance(value, Mapping):
        return {k: _mask_sql_values(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_sql_values(v, key) for v in value]
    if isinstance(value, str) and "sql" in key.lower():
        return mask_active_sql(value)
    return value


def _mask_detail_excerpt(resp: ToolResponse, raw: str) -> str:
    """S3b 실행 중 SQL 발췌 — 게이트웨이 가림과 별개로 SQL 리터럴을 다시 가린다(계약 §3).

    응답을 해석하지 못했으면 발췌 전체를 가린다(노출보다 과가림).
    """
    if isinstance(resp.data, Mapping):
        return json.dumps(_mask_sql_values(resp.data), ensure_ascii=False, default=str)
    return mask_active_sql(raw)


def tool_label(tool: str, args: Mapping[str, Any]) -> str:
    """실행 기록 한 줄 — `도구 {인자 JSON}`(인자가 없으면 도구명만 · plans/187 W13)."""
    if not args:
        return tool
    return f"{tool} {json.dumps(dict(args), ensure_ascii=False, sort_keys=True, default=str)}"


def _rows(data: Mapping[str, Any] | None) -> int | None:
    """응답의 행 수 — 최상위에서 처음 만나는 목록 값의 길이(목록이 없으면 None)."""
    if not isinstance(data, Mapping):
        return None
    for value in data.values():
        if isinstance(value, list):
            return len(value)
    return None


# ── 공용 도우미 ─────────────────────────────────────────────────────────────


def naive_time(value: object) -> datetime | None:
    """ISO 문자열(`T`·공백 구분 · 시간대 표기 허용) → naive datetime. 해석 불가면 None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip()).replace(tzinfo=None)
    except ValueError:
        return None


def realtime_badge(captured: object, alarm_time: str, now: Callable[[], datetime]) -> str:
    """실시간 전용 도구의 배지(§4.0) — 「현재값 · 알람 후 N분」. 수집 시각이 없으면 실행 시각."""
    at = naive_time(captured) or now().replace(tzinfo=None)
    alarm = naive_time(alarm_time)
    if alarm is None:
        return "현재값"
    minutes = int((at - alarm).total_seconds() // 60)
    return f"현재값 · 알람 후 {minutes}분" if minutes >= 0 else f"현재값 · 알람 {-minutes}분 전"


def _missing(tools: Sequence[str], reason: str) -> StepResult:
    return StepResult(status="missing", missing=list(dict.fromkeys(tools)), reason=reason)


def _add_missing(missing: list[str], name: str) -> None:
    if name not in missing:
        missing.append(name)


def _reasons(resps: Sequence[ToolResponse]) -> str:
    return " · ".join(f"{r.tool}: {r.error}" for r in resps if not r.ok)


#: 폴스타 축을 「생략」(결손 아님)으로 두는 연결 상태(plans/196 §5.2 — 부정 연결·선택 대기).
POLESTAR_LINK_SKIP = frozenset({"absent", "unresolved"})

#: 폴스타 축을 부르는 액션 — 전용(`host.*`)과 혼합(폴스타 축 + 제니퍼 축) 단계(plans/196 §5.2
#: 재조회 대상). 혼합 단계는 다시 돌면 제니퍼 축도 다시 부른다.
POLESTAR_AXIS_ACTIONS = frozenset({
    "host.usage_trend",
    "host.process_top",
    "host.resource_status",
    "host.topology",
    "host.alarm_history",
    "xsource.related_alarms",
    "change.recent",
})

#: 단일 축 액션 → 그 액션이 부르는 축(plans/196 D7 — 화면 「판정 근거 부족」 축별 집계).
#: 혼합 단계(`MIXED_AXIS_ACTIONS`)는 `data["axes"]`의 축별 상태를 쓴다. 표에 없는 액션
#: (`dpm.*`·`synthesize`)은 축 상태를 싣지 않는다.
ACTION_AXIS: dict[str, Axis] = {
    "scope.resolve_was_targets": "apm",
    "scope.host_to_was": "apm",
    "apm.golden_signals": "apm",
    "apm.runtime_and_pool": "apm",
    "apm.slow_tx_breakdown": "apm",
    "apm.events_errors": "apm",
    "apm.active_services": "apm",
    "apm.active_sql": "apm",
    "host.usage_trend": "polestar",
    "host.process_top": "polestar",
    "host.resource_status": "polestar",
    "host.topology": "polestar",
    "host.alarm_history": "polestar",
}
#: 폴스타 축 + 다른 축을 함께 부르는 단계 — 축별 상태가 `data["axes"]`에 있다.
MIXED_AXIS_ACTIONS = frozenset({"xsource.related_alarms", "change.recent"})


def section_axis_status(action: str, status: str, data: Mapping[str, Any]) -> dict[str, str]:
    """섹션의 축별 상태 `{축: 상태}`(결정적 — plans/196 D7). 값이 없으면 빈 dict.

    단일 축 액션은 섹션 상태 그대로, 혼합 단계는 `data["axes"]`의 축별 상태(화면 축 라벨
    `AXIS_LABELS`가 있는 축만)를 쓴다. 미연동(`NOT_CONNECTED`) 축은 뺀다 — 판정 근거 부족의 원인이
    아니다(S7 축 요약이 미연동 ITSM을 숨기는 규칙과 대칭)."""
    if action in MIXED_AXIS_ACTIONS:
        axes = data.get("axes") if isinstance(data, Mapping) else None
        if not isinstance(axes, Mapping):
            return {}
        return {
            k: str(v["status"])
            for k, v in axes.items()
            if k in AXIS_LABELS
            and isinstance(v, Mapping)
            and v.get("status")
            and v.get("reason") != NOT_CONNECTED
        }
    axis = ACTION_AXIS.get(action)
    return {axis: status} if axis and status else {}


def _polestar_skipped(ctx: StepContext) -> bool:
    """폴스타 축이 연결 힌트(부정 연결·선택 대기)로 생략 대상인가 — 결손(missing)이 아니다."""
    return not ctx.scope.polestar_source and ctx.scope.polestar_link in POLESTAR_LINK_SKIP


def _polestar_reason(ctx: StepContext) -> str | None:
    """폴스타 축을 부를 수 없으면 사유 문장(미확정·미구성·생략), 부를 수 있으면 None."""
    if not ctx.scope.polestar_source or not ctx.scope.polestar_server:
        if _polestar_skipped(ctx) and ctx.scope.polestar_reason:
            return ctx.scope.polestar_reason
        detail = ctx.scope.polestar_reason or "폴스타 소스·서버명을 결정적으로 정할 수 없다"
        return f"{POLESTAR_UNRESOLVED} — {detail}"
    if ctx.polestar is None:
        return "폴스타 MCP 미구성"
    return None


def _polestar_blocked(ctx: StepContext, tools: Sequence[str], reason: str) -> StepResult:
    """폴스타 전용 스텝을 부를 수 없을 때의 결과 — 연결 생략이면 skipped(결손 아님 · plans/196
    §5.2), 아니면 missing. 운영자 상세 사유(`reason_detail`)를 함께 단다(D6)."""
    res = (
        StepResult(status="skipped", reason=reason)
        if _polestar_skipped(ctx)
        else _missing(tools, reason)
    )
    if not ctx.scope.polestar_source:
        res.reason_detail = ctx.scope.polestar_reason_detail
    return res


def _evidence_scope(ctx: StepContext, granularity: str = "h") -> EvidenceScope:
    return EvidenceScope(
        source=str(ctx.scope.polestar_source),
        server_name=str(ctx.scope.polestar_server),
        reference_time=ctx.windows.reference_time,
        lookback_minutes=ctx.windows.lookback_minutes,
        granularity=granularity,
    )


def _signals(data: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    items = (data or {}).get("was_signals") or []
    return [s for s in items if isinstance(s, dict)]


def _first_row(data: Mapping[str, Any] | None) -> dict[str, Any] | None:
    rows = (data or {}).get("rows") or []
    return rows[0] if rows and isinstance(rows[0], dict) else None


def _window_stats(data: Mapping[str, Any] | None, lookback_minutes: int) -> dict[str, Any] | None:
    """`apm_app_health`(과거 모드) 첫 행 window + TPS(= calls / 창 초)."""
    row = _first_row(data)
    win = (row or {}).get("window") if row else None
    if not isinstance(win, dict):
        return None
    calls = win.get("calls")
    tps = (
        round(float(calls) / (lookback_minutes * 60), 3)
        if isinstance(calls, (int, float)) and lookback_minutes > 0
        else None
    )
    return {
        "calls": calls,
        "errors": win.get("errors"),
        "error_rate": win.get("error_rate"),
        "p50_ms": win.get("response_time_p50_ms"),
        "p95_ms": win.get("response_time_p95_ms"),
        "max_ms": win.get("response_time_max_ms"),
        "tps": tps,
    }


def _baseline_p95(stats: Sequence[dict[str, Any] | None]) -> float | None:
    vals = [float(s["p95_ms"]) for s in stats if s and isinstance(s.get("p95_ms"), (int, float))]
    return round(mean(vals), 3) if vals else None


def target_verdict(
    signals: Sequence[Mapping[str, Any]],
    p95: object,
    baseline_p95: float | None,
    ratio: float,
) -> tuple[str | None, str | None]:
    """인스턴스 판정 — 게이트웨이 `was_signals`가 있으면 degraded(인용 · 재판정 없음).

    없으면 결정적 규칙: 사건창 p95 ≥ ratio × 기준선 p95 → degraded, 아니면 normal(출처
    playbook_rule).
    p95·기준선이 없으면 (None, None) — 판정 불가(결손을 normal로 단정하지 않는다).
    """
    if signals:
        return "degraded", VERDICT_SOURCE_GATEWAY
    if not isinstance(p95, (int, float)) or not baseline_p95:
        return None, None
    return ("degraded" if float(p95) >= ratio * baseline_p95 else "normal"), VERDICT_SOURCE_RULE


def _worst(verdicts: Sequence[tuple[str | None, str | None]]) -> tuple[str | None, str | None]:
    """대상 여럿의 대표 판정 — degraded 하나라도면 degraded, 전부 normal이면 normal, 아니면 판정
    불가."""
    for v, s in verdicts:
        if v == "degraded":
            return v, s
    if verdicts and all(v == "normal" for v, _ in verdicts):
        return "normal", verdicts[0][1]
    return None, None


def _pct(cur: object, base: object) -> float | None:
    if not isinstance(cur, (int, float)) or not isinstance(base, (int, float)) or not base:
        return None
    return round((float(cur) - float(base)) / float(base) * 100, 1)


def _shift(iso: str, days: int) -> str:
    return (datetime.fromisoformat(iso) - timedelta(days=days)).isoformat()


def _apm_args(
    ctx: StepContext,
    tool: str,
    target_source: str | None,
    /,
    *,
    discovery: bool = False,
    **kw: Any,
) -> dict[str, Any]:
    """APM 도구 인자 조립 — 모든 플레이북 APM 호출의 단일 지점(plans/190 W1·W2 · D-344).

    - `target_source`(대상의 APM 소스 id)가 있고 도구가 `APM_SOURCE_IDS_TOOLS`면
      `source_ids=[target_source]`를 싣는다
      (게이트웨이가 고른 소스 전부에서 동명 인스턴스를 찾지 않게 — 다른 존 서버 혼입 방지).
    - 소스가 없으면 종전 인자 그대로 두고 한계 「APM 소스 미확정 — 전 소스 조회」를 남긴다.
      `discovery`(대상 확정 전 탐색 호출 — 소스를 아직 모르는 것이 정상)는 한계를 남기지 않는다.
    - 기간 모드(`reference_time` 있음)의 `APM_XVIEW_CONFIRM_TOOLS`에는 상한이 설정돼 있으면
      `xview_confirm_over`를 싣는다(None·0 이하면 미전송 = 종전 비트 동일).
    """
    args = dict(kw)
    if tool in APM_SOURCE_IDS_TOOLS:
        if target_source:
            args["source_ids"] = [target_source]
        elif not discovery:
            ctx.add_limit(APM_SOURCE_UNRESOLVED_LIMIT)
    if (
        tool in APM_XVIEW_CONFIRM_TOOLS
        and args.get("reference_time")
        and ctx.xview_confirm_over is not None
    ):
        args["xview_confirm_over"] = ctx.xview_confirm_over
    return args


def _confirm_required(resp: ToolResponse) -> bool:
    """게이트웨이 X-View 사전 확인 봉투(`status = confirm_required` · 데이터 없음)인가."""
    return resp.ok and (resp.data or {}).get("status") == XVIEW_CONFIRM_REQUIRED


def _note_confirm(ctx: StepContext, resp: ToolResponse) -> None:
    """사전 확인 초과 한계 1줄 — 예상 호출 수는 봉투의 `expected_xview_calls`(없으면 ?)."""
    data = resp.data or {}

    def _count(v: object) -> object:
        return v if isinstance(v, int) and not isinstance(v, bool) else "?"

    ctx.add_limit(
        XVIEW_CONFIRM_LIMIT.format(
            n=_count(data.get("expected_xview_calls")),
            m=_count(data.get("xview_confirm_over", ctx.xview_confirm_over)),
        )
    )


def _prior_window_compare_call(
    ctx: StepContext, instance: str, source_id: str | None
) -> tuple[str, dict]:
    """사전 확인 초과 대체(§3.2 ②) — 사건창 vs 같은 길이 직전 구간의 시 단위 통계(X-View 없음).

    게이트웨이는 두 구간을 시 경계로 넓힌다(`hour_floor`·`hour_ceil`). 직전 구간을 분 단위로
    잡으면 정시를 걸친 사건창과 겹치므로, 사건창을 시 경계로 넓힌 [⌊시작⌋, ⌈끝⌉) 길이 H시간을
    구하고 직전 구간 = [⌊시작⌋ − H, ⌊시작⌋)로 둔다. 사건창 인자는 요청 그대로 보낸다(게이트웨이가
    같은 규칙으로 넓히고 「요청 → 조회」 한계를 단다).
    """
    w = ctx.windows
    start, end = datetime.fromisoformat(w.incident_start), datetime.fromisoformat(w.incident_end)
    floor = start.replace(minute=0, second=0, microsecond=0)
    ceil = end.replace(minute=0, second=0, microsecond=0)
    if ceil < end:
        ceil += timedelta(hours=1)
    prev_end = floor.isoformat()
    prev_start = (floor - (ceil - floor)).isoformat()
    return (
        APM_PERIOD_COMPARE,
        _apm_args(
            ctx,
            APM_PERIOD_COMPARE,
            source_id,
            instance_name=instance,
            current_start=w.incident_start,
            current_end=w.incident_end,
            baseline_start=prev_start,
            baseline_end=prev_end,
        ),
    )


def _confirm_fallback(
    ctx: StepContext,
    entries: Sequence[tuple[str, str | None]],
    resps: Sequence[ToolResponse],
    missing: list[str],
    reasons: list[str],
) -> dict[int, dict[str, Any] | None]:
    """사건창 `apm_app_health`가 사전 확인 초과인 대상마다 대체 비교를 1회 부른다(§3.2 ②).

    돌려주는 값은 대상 순번 → 시 단위 비교 첫 행(대체 실패면 None — 그 신호는 결손 · §3.2 ③).
    X-View는 다시 부르지 않는다. 판정은 하지 않는다(p95 근거 없음 = 판정 불가).
    """
    idx = [i for i, r in enumerate(resps) if _confirm_required(r)]
    if not idx:
        return {}
    for i in idx:
        _note_confirm(ctx, resps[i])
    calls = [_prior_window_compare_call(ctx, *entries[i]) for i in idx]
    out: dict[int, dict[str, Any] | None] = {}
    for i, r in zip(idx, ctx.call("apm", calls)):
        if r.ok:
            out[i] = _first_row(r.data)
        else:
            out[i] = None
            _add_missing(missing, APM_APP_HEALTH)
            _add_missing(missing, APM_PERIOD_COMPARE)
            reasons.append(f"{entries[i][0]}: 사전 확인 초과 대체 실패 — {r.tool}: {r.error}")
    return out


def _target_labels(items: Sequence[tuple[str, str | None]]) -> list[str]:
    """표·대상별 키 이름 — 같은 이름이 둘 이상이면(다른 APM 소스) 두 번째부터 `이름@소스`.

    첫 행(알람 인스턴스)은 이름 그대로라 판독의 이름 조회가 종전과 같다(plans/190 W1).
    """
    seen: set[str] = set()
    out: list[str] = []
    for name, src in items:
        out.append(f"{name}@{src or '?'}" if name in seen else name)
        seen.add(name)
    return out


def _baseline_calls(
    ctx: StepContext, instance: str, source_id: str | None = None
) -> list[tuple[str, tuple[str, dict]]]:
    return [
        (
            b.name,
            (
                APM_APP_HEALTH,
                _apm_args(
                    ctx,
                    APM_APP_HEALTH,
                    source_id,
                    instance_name=instance,
                    reference_time=b.reference_time,
                    lookback_minutes=ctx.windows.lookback_minutes,
                ),
            ),
        )
        for b in ctx.windows.baselines
    ]


def _incident_health_call(
    ctx: StepContext, instance: str, source_id: str | None = None
) -> tuple[str, dict]:
    return (
        APM_APP_HEALTH,
        _apm_args(
            ctx,
            APM_APP_HEALTH,
            source_id,
            instance_name=instance,
            reference_time=ctx.windows.reference_time,
            lookback_minutes=ctx.windows.lookback_minutes,
        ),
    )


def _series_summary(points: object) -> dict[str, Any] | None:
    vals = (
        [
            float(p["value"])
            for p in points or []
            if isinstance(p, dict) and isinstance(p.get("value"), (int, float))
        ]
        if isinstance(points, list)
        else []
    )
    if not vals:
        return None
    return {
        "first": vals[0],
        "last": vals[-1],
        "min": min(vals),
        "max": max(vals),
        "avg": round(mean(vals), 3),
        "points": len(vals),
    }


def _epoch_ms_iso(value: object) -> str | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000).replace(tzinfo=None).isoformat()  # type: ignore[arg-type]
    except (TypeError, ValueError, OSError, OverflowError):
        return None


# ── S0 · S0r 대상 확정 ──────────────────────────────────────────────────────


def _business_term(
    ctx: StepContext, row: Mapping[str, Any] | None
) -> tuple[str | None, str | None]:
    """R-A 같은 업무 조회어와 출처. 페이로드 힌트 → 인스턴스 행 업무명 → 인스턴스 설명 첫 낱말."""
    meta = ctx.payload.get("meta") if isinstance(ctx.payload, Mapping) else None
    hints = meta.get("hints") if isinstance(meta, Mapping) else None
    if isinstance(hints, Mapping):
        for key in ("business_name", "business"):
            val = str(hints.get(key) or "").strip()
            if val:
                return val, f"hints.{key}"
    if row:
        names = row.get("business_names")
        if isinstance(names, list) and names and str(names[0]).strip():
            return str(names[0]).strip(), "instance.business_names"
        desc = str(row.get("description") or "").split()
        if desc:
            return desc[0], "instance.description"
    return None, None


#: 소스 id로 인정하는 최대 길이(형식 가드 — 넘으면 소스 미확정으로 본다).
_SOURCE_ID_MAX_LEN = 64


def _row_source(row: Mapping[str, Any] | None) -> str | None:
    """인스턴스 행의 APM 소스 id — 문자열이고 strip 뒤 1~64자일 때만(아니면 None = 소스 미확정)."""
    value = (row or {}).get("source_id")
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if 0 < len(value) <= _SOURCE_ID_MAX_LEN else None


def _instance_rows(resp: ToolResponse) -> list[dict[str, Any]]:
    return [
        r
        for r in (resp.data or {}).get("rows") or []
        if isinstance(r, dict) and r.get("instance_name")
    ]


def resolve_was_targets(ctx: StepContext) -> StepResult:
    """S0 — 알람 인스턴스 → 호스트 → 그 호스트 인스턴스 + 같은 업무 인스턴스 → 판정 → 대상 선별.

    인자: `max_instances`(대상 상한 · 기본 3) · `max_compare`(범위 비교 app_health 상한 · 기본 8) ·
    `p95_ratio`(판정 배수 · 기본 2.0) · `pick`(degraded만 지원 — 그 밖은 비교 전원).
    정합 신뢰도 낮음/0건이면 「대상 확정 실패」 — 이후 APM 스텝은 알람 인스턴스 단독으로 강등한다.
    """
    alarm_instance = ctx.scope.alarm_instance
    if not alarm_instance:
        return _missing([], "알람 인스턴스명 없음(event.resourceName) — 대상 확정 불가")
    if ctx.apm is None:
        return _missing([], "APM 게이트웨이 미구성 — 대상 확정 불가")
    max_instances = int(ctx.args.get("max_instances", 3))
    max_compare = int(ctx.args.get("max_compare", 8))
    ratio = float(ctx.args.get("p95_ratio", DEFAULT_P95_RATIO))
    missing: list[str] = []
    reasons: list[str] = []

    q = ctx.call_one(
        "apm",
        APM_INSTANCE_MAP,
        _apm_args(ctx, APM_INSTANCE_MAP, None, discovery=True, query=alarm_instance),
    )
    q_rows = _instance_rows(q)
    alarm_row = next(
        (r for r in q_rows if r.get("instance_name") == alarm_instance),
        q_rows[0] if q_rows else None,
    )
    confidence = str(
        (alarm_row or {}).get("search_confidence")
        or (alarm_row or {}).get("match_confidence")
        or ""
    )
    resolved = alarm_row is not None and confidence.lower() not in LOW_CONFIDENCE
    if not q.ok:
        _add_missing(missing, APM_INSTANCE_MAP)
    host = ctx.scope.host
    business: str | None = None
    business_source: str | None = None
    #: 알람 인스턴스의 APM 소스 — 대상 확정 행의 `source_id`(plans/190 W1 · 실패면 None).
    alarm_src = _row_source(alarm_row) if resolved else None
    # (인스턴스, 호스트, 소스) — 같은 이름이라도 소스가 다르면 다른 서버라 행을 나눈다.
    compare: list[tuple[str, str | None, str | None]] = [(alarm_instance, None, alarm_src)]
    if resolved:
        host = str(alarm_row.get("hostname") or alarm_row.get("host_name") or host or "") or None  # type: ignore[union-attr]
        business, business_source = _business_term(ctx, alarm_row)
        calls: list[tuple[str, dict]] = []
        if host:
            # 호스트는 알람 인스턴스의 서버다 — 그 소스로 좁힌다(소스 미확정이면 탐색 호출).
            calls.append(
                (
                    APM_INSTANCE_MAP,
                    _apm_args(ctx, APM_INSTANCE_MAP, alarm_src, discovery=True, hostname=host),
                )
            )
        if business:
            # 업무 동료 탐색은 소스를 좁히지 않는다 — 나온 행마다 자기 `source_id`로 이후 호출한다.
            calls.append(
                (
                    APM_INSTANCE_MAP,
                    _apm_args(ctx, APM_INSTANCE_MAP, None, discovery=True, business=business),
                )
            )
        for resp in ctx.call("apm", calls):
            if not resp.ok:
                _add_missing(missing, APM_INSTANCE_MAP)
                reasons.append(f"{resp.tool}({next(iter(resp.args))}): {resp.error}")
            for r in _instance_rows(resp):
                name, src = str(r["instance_name"]), _row_source(r)
                if name == alarm_instance and (
                    alarm_src is None or src is None or src == alarm_src
                ):
                    continue  # 알람 인스턴스 자신(어느 쪽이든 소스 미상이면 종전처럼 이름으로 합친다)
                compare.append(
                    (name, str(r.get("hostname") or r.get("host_name") or "") or None, src)
                )
        compare[0] = (alarm_instance, host, alarm_src)
    else:
        why = q.error or ("0건" if alarm_row is None else f"정합 신뢰도 {confidence or '없음'}")
        reasons.append(f"대상 확정 실패({why}) — 이후 APM 스텝은 알람 인스턴스 단독")
    seen: dict[tuple[str, str | None], str | None] = {}
    for name, h, src in compare:
        seen.setdefault((name, src), h)
    entries = list(seen)[: max(1, max_compare)]
    if len(seen) > len(entries):
        reasons.append(
            f"범위 비교 상한 {max_compare} — {len(seen) - len(entries)}개 인스턴스 생략"
        )

    health_calls = [_incident_health_call(ctx, n, src) for n, src in entries]
    base_calls = _baseline_calls(ctx, alarm_instance, alarm_src)
    resps = ctx.call("apm", health_calls + [c for _, c in base_calls])
    cur_resps, base_resps = resps[: len(entries)], resps[len(entries) :]
    lb = ctx.windows.lookback_minutes
    base_stats = {bn: _window_stats(r.data, lb) for (bn, _), r in zip(base_calls, base_resps)}
    base_p95 = _baseline_p95(list(base_stats.values()))
    if any(not r.ok for r in resps):
        _add_missing(missing, APM_APP_HEALTH)
        reasons.append(_reasons(resps))
    for r in base_resps:
        if _confirm_required(r):
            _note_confirm(ctx, r)
    hourly = _confirm_fallback(ctx, entries, cur_resps, missing, reasons)
    labels = _target_labels(entries)
    instances: list[dict[str, Any]] = []
    for idx, ((name, src), resp) in enumerate(zip(entries, cur_resps)):
        stats = _window_stats(resp.data, lb)
        verdict, source = (
            target_verdict(_signals(resp.data), (stats or {}).get("p95_ms"), base_p95, ratio)
            if resp.ok
            else (None, None)
        )
        inst: dict[str, Any] = {
            "instance": name,
            "host": seen.get((name, src)),
            "source_id": src,
            "stats": stats,
            "verdict": verdict,
            "verdict_source": source,
            "was_signals": _signals(resp.data),
            "error": resp.error,
        }
        if idx in hourly:
            inst["xview_confirm_required"] = True
            inst["period_compare"] = hourly[idx]
        instances.append(inst)
    pick = str(ctx.args.get("pick", "degraded"))
    chosen = [instances[0]] + [
        i for i in instances[1:] if pick != "degraded" or i["verdict"] == "degraded"
    ]
    chosen = chosen[: max(1, max_instances)] if resolved else [instances[0]]
    targets = [
        {
            "instance": i["instance"],
            "verdict": i["verdict"],
            "verdict_source": i["verdict_source"],
            "confidence": confidence or None,
            "source_id": i["source_id"],
        }
        for i in chosen
    ]
    scope = RunScope(
        alarm_instance=alarm_instance,
        host=host,
        polestar_source=ctx.scope.polestar_source,
        # 확정 연결(plans/196 §5.2)의 폴스타 등록명은 제니퍼 hostname으로 덮지 않는다
        polestar_server=ctx.scope.polestar_server
        if ctx.scope.polestar_link == "linked"
        else host or ctx.scope.polestar_server,
        polestar_reason=ctx.scope.polestar_reason,
        alarm_name=ctx.scope.alarm_name,
        targets=targets,
        polestar_basis=ctx.scope.polestar_basis,
        polestar_reason_detail=ctx.scope.polestar_reason_detail,
        polestar_link=ctx.scope.polestar_link,
    )
    polestar_reason = scope_polestar_reason(scope)
    if polestar_reason:
        reasons.append(polestar_reason)
    table = {
        "columns": [
            "instance",
            "host",
            "tps",
            "p95_ms",
            "baseline_p95_ms",
            "error_rate",
            "verdict",
            "verdict_source",
        ],
        "rows": [
            [
                label,
                i["host"],
                (i["stats"] or {}).get("tps"),
                (i["stats"] or {}).get("p95_ms"),
                base_p95 if idx == 0 else None,
                (i["stats"] or {}).get("error_rate"),
                i["verdict"],
                i["verdict_source"],
            ]
            for idx, (label, i) in enumerate(zip(labels, instances))
        ],
    }
    head = instances[0]
    return StepResult(
        status="ok",
        verdict=head["verdict"],
        verdict_source=head["verdict_source"],
        table=table,
        data={
            "scope": scope.to_dict(),
            "resolution": "resolved" if resolved else "failed",
            "confidence": confidence or None,
            "business": business,
            "business_source": business_source,
            "baseline_p95_ms": base_p95,
            "baseline_stats": base_stats,
            "instances": instances,
        },
        missing=missing,
        reason=" · ".join(r for r in reasons if r) or None,
        reason_detail=None if scope.polestar_source else scope.polestar_reason_detail,
        artifacts={"scope": scope},
    )


def scope_polestar_reason(scope: RunScope) -> str | None:
    """S0 사유의 폴스타 토막 — 미확정이면 그 사유, 대체·연결로 정했으면 근거(없으면 None)."""
    if not scope.polestar_source:
        return f"{POLESTAR_UNRESOLVED} — {scope.polestar_reason or '폴스타 소스 결정 불가'}"
    return scope.polestar_basis


def host_to_was(ctx: StepContext) -> StepResult:
    """S0′ — 호스트 알람의 역방향: 호스트의 WAS 인스턴스 → app_health(사건창) 판정.

    판정은 게이트웨이 `was_signals` 유무만 쓴다(기준선 조회 없음). 제니퍼 미구성이면 생략(결손
    아님).
    """
    if ctx.apm is None:
        return StepResult(status="skipped", reason="제니퍼 미구성 — 생략(결손 아님)")
    host = ctx.scope.polestar_server or ctx.scope.host
    if not host:
        return _missing([], "호스트명 없음 — 역방향 대상 확정 불가")
    max_compare = int(ctx.args.get("max_compare", 8))
    m = ctx.call_one(
        "apm",
        APM_INSTANCE_MAP,
        _apm_args(ctx, APM_INSTANCE_MAP, None, discovery=True, hostname=host),
    )
    if not m.ok:
        return _missing([APM_INSTANCE_MAP], f"{APM_INSTANCE_MAP}: {m.error}")
    # (인스턴스, 소스) — 같은 이름이라도 소스가 다르면 행을 나눈다(plans/190 W1).
    entries = list(
        dict.fromkeys((str(r["instance_name"]), _row_source(r)) for r in _instance_rows(m))
    )[:max_compare]
    if not entries:
        # 명시 판정(W4 후속 ③) — None이면 역방향 `when`(S0r.verdict == 'degraded')이 판정 불가
        # (UNKNOWN)로 실행돼 버린다. 「WAS 없음」은 확인된 사실이라 결정적으로 거짓이 되게 한다.
        return StepResult(
            status="ok",
            verdict=NO_WAS_VERDICT,
            verdict_source=VERDICT_SOURCE_RULE,
            reason="이 호스트에 WAS 인스턴스 없음",
            data={"host": host, "instances": []},
        )
    resps = ctx.call("apm", [_incident_health_call(ctx, n, src) for n, src in entries])
    missing = [APM_APP_HEALTH] if any(not r.ok for r in resps) else []
    reasons = [_reasons(resps)]
    hourly = _confirm_fallback(ctx, entries, resps, missing, reasons)
    labels = _target_labels(entries)
    instances = []
    for idx, ((name, src), resp) in enumerate(zip(entries, resps)):
        sig = _signals(resp.data)
        # 사전 확인 초과는 게이트웨이가 판정하지 않은 응답이다 — 「정상」이 아니라 판정 불가.
        judged = resp.ok and idx not in hourly
        verdict = None if not judged else ("degraded" if sig else "normal")
        inst: dict[str, Any] = {
            "instance": name,
            "source_id": src,
            "stats": _window_stats(resp.data, ctx.windows.lookback_minutes),
            "verdict": verdict,
            "verdict_source": VERDICT_SOURCE_GATEWAY if judged else None,
            "was_signals": sig,
            "error": resp.error,
        }
        if idx in hourly:
            inst["xview_confirm_required"] = True
            inst["period_compare"] = hourly[idx]
        instances.append(inst)
    targets = [
        {
            "instance": i["instance"],
            "verdict": i["verdict"],
            "verdict_source": i["verdict_source"],
            "confidence": None,
            "source_id": i["source_id"],
        }
        for i in instances
        if i["verdict"] == "degraded"
    ]
    scope = RunScope(
        alarm_instance=ctx.scope.alarm_instance,
        host=host,
        polestar_source=ctx.scope.polestar_source,
        polestar_server=ctx.scope.polestar_server,
        polestar_reason=ctx.scope.polestar_reason,
        alarm_name=ctx.scope.alarm_name,
        targets=targets,
        host_was=[str(i["instance"]) for i in instances if i["verdict"] is not None],
        polestar_basis=ctx.scope.polestar_basis,
        polestar_reason_detail=ctx.scope.polestar_reason_detail,
        polestar_link=ctx.scope.polestar_link,
    )
    v, s = _worst([(i["verdict"], i["verdict_source"]) for i in instances])
    return StepResult(
        status="ok",
        verdict=v,
        verdict_source=s,
        table={
            "columns": ["instance", "tps", "p95_ms", "verdict"],
            "rows": [
                [
                    label,
                    (i["stats"] or {}).get("tps"),
                    (i["stats"] or {}).get("p95_ms"),
                    i["verdict"],
                ]
                for label, i in zip(labels, instances)
            ],
        },
        data={"scope": scope.to_dict(), "host": host, "instances": instances},
        missing=missing,
        reason=" · ".join(r for r in reasons if r) or None,
        artifacts={"scope": scope},
    )


# ── S1 · S2 · S3 APM ────────────────────────────────────────────────────────


def _need_apm(ctx: StepContext, tools: Sequence[str]) -> StepResult | None:
    if ctx.apm is None:
        return _missing(tools, "APM 게이트웨이 미구성")
    if not ctx.targets:
        return _missing(tools, "대상 인스턴스 없음")
    return None


def golden_signals(ctx: StepContext) -> StepResult:
    """S1 — 대상별 p95·TPS·에러율 + 기준선(전일·전주 동시간) · 게이트웨이 판정 인용.

    호출: 사건창 `apm_app_health` + 기준 시각을 옮긴 `apm_app_health`(기준선 p95 — period_compare에
    p95가 없다) + `apm_period_compare`(사건창 vs 각 기준선 창). TPS = calls / 창 초.
    """
    blocked = _need_apm(ctx, [APM_APP_HEALTH])
    if blocked:
        return blocked
    ratio = float(ctx.args.get("p95_ratio", DEFAULT_P95_RATIO))
    lb = ctx.windows.lookback_minutes
    w = ctx.windows
    per_target: dict[str, dict[str, Any]] = {}
    rows: list[list[Any]] = []
    missing: list[str] = []
    reasons: list[str] = []
    verdicts: list[tuple[str | None, str | None]] = []
    all_ok_incident = True
    items = ctx.target_items()
    for (inst_name, src), name in zip(items, _target_labels(items)):
        base_calls = _baseline_calls(ctx, inst_name, src)
        cmp_calls = [
            (
                b.name,
                (
                    APM_PERIOD_COMPARE,
                    _apm_args(
                        ctx,
                        APM_PERIOD_COMPARE,
                        src,
                        instance_name=inst_name,
                        current_start=w.incident_start,
                        current_end=w.incident_end,
                        baseline_start=b.start,
                        baseline_end=b.end,
                    ),
                ),
            )
            for b in w.baselines
        ]
        resps = ctx.call(
            "apm",
            [_incident_health_call(ctx, inst_name, src)]
            + [c for _, c in base_calls]
            + [c for _, c in cmp_calls],
        )
        inc = resps[0]
        base = {
            bn: _window_stats(r.data, lb)
            for (bn, _), r in zip(base_calls, resps[1 : 1 + len(base_calls)])
        }
        cmp = {
            bn: _first_row(r.data) for (bn, _), r in zip(cmp_calls, resps[1 + len(base_calls) :])
        }
        for r in resps:
            if not r.ok:
                _add_missing(missing, r.tool)
        if any(not r.ok for r in resps):
            reasons.append(f"{name}: {_reasons(resps)}")
        # 사전 확인 초과(§3.2 ①) — 이 스텝은 이미 시 단위 비교를 부른다: p95는 N/A(판정 불가),
        # 시 단위 통계(`period_compare`)는 그대로 싣는다. X-View는 다시 부르지 않는다.
        for r in resps[: 1 + len(base_calls)]:
            if _confirm_required(r):
                _note_confirm(ctx, r)
        inc_confirm = _confirm_required(inc)
        if inc_confirm and not any(cmp.values()):
            _add_missing(missing, APM_APP_HEALTH)  # 대체할 시 단위 통계도 없음(§3.2 ③)
            reasons.append(f"{name}: X-View 사전 확인 초과 — 시 단위 통계도 없음")
        stats = _window_stats(inc.data, lb)
        all_ok_incident = all_ok_incident and inc.ok
        base_p95 = _baseline_p95(list(base.values()))
        verdict, source = (
            target_verdict(_signals(inc.data), (stats or {}).get("p95_ms"), base_p95, ratio)
            if inc.ok
            else (None, None)
        )
        verdicts.append((verdict, source))
        per_target[name] = {
            "incident": stats,
            "baselines": base,
            "baseline_p95_ms": base_p95,
            "period_compare": cmp,
            "was_signals": _signals(inc.data),
            "verdict": verdict,
            "verdict_source": source,
        }
        if src is not None:
            per_target[name]["source_id"] = src
        if inc_confirm:
            per_target[name]["xview_confirm_required"] = True
        for metric in ("p95_ms", "tps", "error_rate", "calls"):
            cur = (stats or {}).get(metric)
            vals = [(base.get(b.name) or {}).get(metric) for b in w.baselines]
            rows.append([name, metric, cur, *vals, *[_pct(cur, v) for v in vals]])
        cur_avg = (
            next(((c or {}).get("current") or {}).get("avg_response_ms") for c in cmp.values() if c)
            if any(cmp.values())
            else None
        )
        avg_vals = [
            ((cmp.get(b.name) or {}).get("baseline") or {}).get("avg_response_ms")
            for b in w.baselines
        ]
        rows.append(
            [name, "avg_response_ms", cur_avg, *avg_vals, *[_pct(cur_avg, v) for v in avg_vals]]
        )
    names = [b.name for b in w.baselines]
    table = {
        "columns": ["instance", "metric", "incident", *names, *[f"change_pct_{n}" for n in names]],
        "rows": rows,
    }
    v, s = _worst(verdicts)
    if not all_ok_incident and all(v is None for v, _ in verdicts):
        return StepResult(
            status="missing",
            table=table,
            data={"targets": per_target},
            missing=missing,
            reason=" · ".join(reasons) or "사건창 조회 실패",
        )
    return StepResult(
        status="ok",
        verdict=v,
        verdict_source=s,
        table=table,
        data={"targets": per_target, "p95_ratio": ratio},
        missing=missing,
        reason=" · ".join(reasons) or None,
    )


def runtime_and_pool(ctx: StepContext) -> StepResult:
    """S2 — 힙·GC%(5분 추세) · 프로세스 CPU·액티브 서비스 추세 · DB 커넥션 풀(현재값 — 배지).

    인자: `lookback_minutes`(추세 창 재정의 — 예 힙 주기 판정) · `process_trend`(기본 true —
    false면 프로세스 CPU·액티브 추세 호출 생략).
    """
    blocked = _need_apm(ctx, [APM_RUNTIME_HEALTH, APM_RESOURCE_POOL])
    if blocked:
        return blocked
    lb = int(ctx.args.get("lookback_minutes", ctx.windows.lookback_minutes))
    with_proc = bool(ctx.args.get("process_trend", True))
    per_target: dict[str, dict[str, Any]] = {}
    rows: list[list[Any]] = []
    badges: list[str] = []
    missing: list[str] = []
    reasons: list[str] = []
    signals: list[dict[str, Any]] = []
    any_ok = False
    items = ctx.target_items()
    for (inst_name, src), name in zip(items, _target_labels(items)):
        base = _apm_args(
            ctx,
            APM_RUNTIME_HEALTH,
            src,
            instance_name=inst_name,
            reference_time=ctx.windows.reference_time,
            lookback_minutes=lb,
        )
        calls = [(APM_RUNTIME_HEALTH, dict(base))]
        if with_proc:
            calls.append((APM_RUNTIME_HEALTH, {**base, "metrics": ["proc_cpu", "active_service"]}))
        calls.append(
            (APM_RESOURCE_POOL, _apm_args(ctx, APM_RESOURCE_POOL, src, instance_name=inst_name))
        )
        resps = ctx.call("apm", calls)
        trend: dict[str, Any] = {}
        for r in resps[:-1]:
            row = _first_row(r.data)
            if isinstance((row or {}).get("trend"), dict):
                trend.update(row["trend"])  # type: ignore[index]
        pool_resp = resps[-1]
        pool = _first_row(pool_resp.data)
        badge = (
            realtime_badge(
                (pool_resp.data or {}).get("queried_at"), ctx.windows.alarm_time, ctx.now
            )
            if pool_resp.ok
            else None
        )
        if badge and badge not in badges:
            badges.append(badge)
        for r in resps:
            if not r.ok:
                _add_missing(missing, r.tool)
            else:
                any_ok = True
            signals.extend(_signals(r.data))
        if any(not r.ok for r in resps):
            reasons.append(f"{name}: {_reasons(resps)}")
        summaries = {k: _series_summary(v) for k, v in trend.items()}
        per_target[name] = {
            "trend": trend,
            "trend_summary": summaries,
            "pool": pool,
            "pool_badge": badge,
            "was_signals": [s for r in resps for s in _signals(r.data)],
        }
        for key, summ in summaries.items():
            if summ:
                rows.append([name, key, summ["min"], summ["max"], summ["last"], "추세"])
        for key in (
            "db_pool_active",
            "db_pool_configured_avg",
            "db_pool_idle_avg",
            "db_pool_usage_ratio",
            "active_services",
            "thread_current",
        ):
            if pool and key in pool:
                rows.append([name, key, None, None, pool[key], badge])
    if not any_ok:
        return StepResult(status="missing", missing=missing, reason=" · ".join(reasons))
    verdict = "degraded" if signals else "normal"
    return StepResult(
        status="ok",
        verdict=verdict,
        verdict_source=VERDICT_SOURCE_GATEWAY,
        table={"columns": ["instance", "metric", "min", "max", "last", "note"], "rows": rows},
        data={"targets": per_target, "lookback_minutes": lb, "was_signals": signals},
        badges=badges,
        missing=missing,
        reason=" · ".join(reasons) or None,
    )


_TX_COLUMNS = (
    "application",
    "response_time_ms",
    "sql_ms",
    "fetch_ms",
    "external_ms",
    "cpu_ms",
    "network_ms",
    "txid",
)


def slow_tx_breakdown(ctx: StepContext) -> StepResult:
    """S3 — 느린 트랜잭션 Top N(기본 5) 시간 분해 + 1위 건 프로파일(예산 1 — R-D 원자료).

    대상은 알람 인스턴스(첫 대상). 0건이면 결손. 프로파일 원본(`profile_excerpt`·`sqls`)은 해석하지
    않고 `data.profile`에 그대로 싣는다 — 커넥션 획득/SQL 실행 분리(R-D)는 W3(현 결론 n/a).
    """
    blocked = _need_apm(ctx, [APM_SLOW_TX])
    if blocked:
        return blocked
    name, src = ctx.target_items()[0]
    n = int(ctx.args.get("n", 5))
    profile_top = min(int(ctx.args.get("profile_top", 1)), 1)  # 프로파일 예산 1(§8)
    resp = ctx.call_one(
        "apm",
        APM_SLOW_TX,
        _apm_args(
            ctx,
            APM_SLOW_TX,
            src,
            instance_name=name,
            reference_time=ctx.windows.reference_time,
            lookback_minutes=ctx.windows.lookback_minutes,
            n=n,
        ),
    )
    if not resp.ok:
        return _missing([APM_SLOW_TX], f"{APM_SLOW_TX}: {resp.error}")
    rows = [r for r in (resp.data or {}).get("rows") or [] if isinstance(r, dict)]
    if not rows:
        return StepResult(
            status="missing",
            missing=[APM_SLOW_TX],
            reason="사건창 느린 거래 0건",
            data={"instance": name, "summary": (resp.data or {}).get("summary")},
        )
    profile: dict[str, Any] | None = None
    missing: list[str] = []
    reason = None
    ref = rows[0].get("profile_ref")
    if profile_top and isinstance(ref, dict):
        # 프로파일은 단수 `source_id`(profile_ref 그대로)를 받는다 — `source_ids` 비대상(W0).
        p = ctx.call_one(
            "apm",
            APM_TX_PROFILE,
            _apm_args(
                ctx,
                APM_TX_PROFILE,
                src,
                instance_name=name,
                source_id=ref.get("source_id"),
                domain_id=ref.get("domain_id"),
                txid=ref.get("txid"),
                time_ms=ref.get("time_ms"),
            ),
        )
        if p.ok:
            prow = _first_row(p.data) or {}
            profile = {
                k: prow.get(k)
                for k in ("transaction", "profile_excerpt", "profile_truncated", "sqls")
            }
            profile["limits"] = (p.data or {}).get("limits") or []
        else:
            missing.append(APM_TX_PROFILE)
            reason = f"{APM_TX_PROFILE}: {p.error}"
    signals = _signals(resp.data)
    return StepResult(
        status="ok",
        verdict="degraded" if signals else "normal",
        verdict_source=VERDICT_SOURCE_GATEWAY,
        table={
            "columns": list(_TX_COLUMNS),
            "rows": [[r.get(c) for c in _TX_COLUMNS] for r in rows],
        },
        data={
            "instance": name,
            "rows": [
                {c: r.get(c) for c in (*_TX_COLUMNS, "profile_ref", "business_names")} for r in rows
            ],
            "summary": (resp.data or {}).get("summary"),
            "profile": profile,
            "was_signals": signals,
        },
        missing=missing,
        reason=reason,
    )


# ── S4 · S5 · S6 · S7 호스트·교차 소스 ───────────────────────────────────────


def usage_trend(ctx: StepContext) -> StepResult:
    """S4 — 폴스타 시간대별 지표(`build_calls`의 지표 호출 · `_series` 해석 · `correlate` 이상
    판정 재사용).

    인자: `kinds`(기본 cpu·memory·filesystem·disk_io) · `granularity`(기본 h). 결손 단위는 지표
    종류다.
    """
    thresholds = dict(METRIC_KINDS)
    kinds = [k for k in (ctx.args.get("kinds") or list(thresholds)) if k in thresholds]
    blocked = _polestar_reason(ctx)
    if blocked:
        return _polestar_blocked(ctx, kinds, blocked)
    scope = _evidence_scope(ctx, str(ctx.args.get("granularity", "h")))
    calls = [c for c in build_calls(scope) if c[0] == TOOL_METRIC and c[1].get("kind") in kinds]
    notes: list[str] = []
    series: list[MetricSeries] = []
    missing: list[str] = []
    for (_, args), resp in zip(calls, ctx.call("polestar", calls)):
        kind = str(args["kind"])
        if resp.data is None:
            missing.append(kind)
            notes.append(f"{kind} 축 결손: {resp.error}")
            continue
        series.append(_series(kind, thresholds[kind], resp.data, scope, notes))
    # 설정 매핑으로 정한 소스면 근거를 사유 끝에 남긴다(W21 P6 · 없으면 종전 사유 그대로).
    basis = [ctx.scope.polestar_basis] if ctx.scope.polestar_basis else []
    if not series:
        return StepResult(status="missing", missing=missing, reason=" · ".join(notes + basis))
    result = correlate(ctx.windows.alarm_time, [], series, notes, alarm_axis=False)
    findings = result.metric_findings
    anomalous = [k for k, f in findings.items() if f.get("is_anomalous")]
    rows = [
        [
            s.name,
            findings[s.name].get("peak_value"),
            findings[s.name].get("baseline_mean"),
            findings[s.name].get("z_score"),
            findings[s.name].get("is_anomalous"),
            findings[s.name].get("kind"),
        ]
        for s in series
    ]
    return StepResult(
        status="ok",
        verdict="degraded" if anomalous else "normal",
        verdict_source=VERDICT_SOURCE_RULE,
        table={
            "columns": ["kind", "peak", "baseline_mean", "z_score", "is_anomalous", "anomaly_kind"],
            "rows": rows,
        },
        data={
            "findings": findings,
            "anomalous": anomalous,
            "notes": list(result.notes),
            "series": {
                s.name: {
                    "points": [list(p) for p in s.points],
                    "baseline": list(s.baseline),
                    "granularity_minutes": s.granularity_minutes,
                }
                for s in series
            },
            "source": scope.source,
            "server_name": scope.server_name,
        },
        missing=missing,
        reason=" · ".join([n for n in notes if "결손" in n] + basis) or None,
        artifacts={"series": series},
    )


#: 축 표시 이름(축별 결정적 요약 — plans/196 D4).
AXIS_LABELS: dict[str, str] = {"apm": "제니퍼", "polestar": "폴스타", "itsm": "ITSM"}


def _axis_failed(label: str, reason: str | None, blocked: bool) -> str:
    """조회하지 못한 축의 요약 한 토막.

    부를 수 없었으면 사유 그대로, 호출 실패면 「조회 못 함」."""
    if blocked:
        return f"{label}: {reason or '조회 불가'}"
    return f"{label}: 조회 못 함" + (f" — {reason}" if reason else "")


def _axis_count(label: str, n: int, empty: str, unit: str) -> str:
    """조회 성공 축의 요약 한 토막 — 0건은 「없음」의 근거로 적는다(D4 · 「수집 못 함」과 구분)."""
    return f"{label}: 조회 성공 0건 = {empty}" if n == 0 else f"{label}: {unit} {n}건"


def related_alarms(ctx: StepContext) -> StepResult:
    """S5 — 알람 전후 폴스타 알람(`build_calls` 알람 호출 · `_alarms`) + 제니퍼 이벤트 →
    `correlate()` 타임라인.

    「상관 ≠ 인과」 문구를 결과에 고정한다. 사유(`reason`)는 축별 결정적 요약이다(plans/196 D4 —
    조회 성공 0건 = 없음 · 조회 못 함 구분). 제니퍼 이벤트 행 수와 타임라인 반영 수가 다르면 그
    차이와 사유를 요약에 적는다(D3). 폴스타 축이 연결 힌트로 생략이면 결손으로 세지 않는다.
    """
    notes: list[str] = []
    missing: list[str] = []
    axes: dict[str, dict[str, Any]] = {}
    alarms = []
    polestar_ok = False
    blocked = _polestar_reason(ctx)
    pol_part: str
    if blocked:
        skipped = _polestar_skipped(ctx)
        if not skipped:
            missing.append(TOOL_ALARMS)
        axes["polestar"] = {"status": "skipped" if skipped else "missing", "reason": blocked}
        pol_part = _axis_failed(AXIS_LABELS["polestar"], blocked, True)
    else:
        call = build_calls(_evidence_scope(ctx))[0]
        resp = ctx.call_one("polestar", *call)
        if resp.ok:
            polestar_ok = True
            alarms = _alarms(resp.data or {}, notes)
            axes["polestar"] = {"status": "ok", "reason": None}
            pol_part = _axis_count(AXIS_LABELS["polestar"], len(alarms), "연관 알람 없음", "알람")
        else:
            missing.append(TOOL_ALARMS)
            axes["polestar"] = {"status": "missing", "reason": f"{TOOL_ALARMS}: {resp.error}"}
            pol_part = _axis_failed(AXIS_LABELS["polestar"], resp.error, False)
    events: list[dict[str, Any]] = []
    points: list[EpisodePoint] = []
    apm_ok = False
    instance, src = ctx.target_items()[0] if ctx.targets else (ctx.scope.alarm_instance, None)
    if ctx.apm is None or not instance:
        missing.append(APM_EVENTS)
        why = "APM 게이트웨이 미구성" if ctx.apm is None else "대상 인스턴스 없음"
        axes["apm"] = {"status": "missing", "reason": why}
        apm_part = _axis_failed(AXIS_LABELS["apm"], why, True)
    else:
        ev = ctx.call_one(
            "apm",
            APM_EVENTS,
            _apm_args(
                ctx,
                APM_EVENTS,
                src,
                instance_name=instance,
                reference_time=ctx.windows.reference_time,
                lookback_minutes=ctx.windows.lookback_minutes,
            ),
        )
        if ev.ok:
            apm_ok = True
            for row in (ev.data or {}).get("rows") or []:
                if not isinstance(row, dict):
                    continue
                when = _epoch_ms_iso(row.get("time_ms"))
                item = {
                    k: row.get(k)
                    for k in (
                        "time_ms",
                        "level",
                        "event_type",
                        "instance_name",
                        "application",
                        "message",
                    )
                }
                item["time"] = when
                events.append(item)
                if when:
                    points.append(
                        EpisodePoint(
                            time=when,
                            kind="apm_event",
                            detail=f"{row.get('event_type') or '?'} "
                            f"{row.get('instance_name') or ''}".strip(),
                        )
                    )
            axes["apm"] = {"status": "ok", "reason": None}
            apm_part = _axis_count(AXIS_LABELS["apm"], len(events), "이벤트 없음", "이벤트")
            if len(points) != len(events):
                # 조회 행 수 ≠ 타임라인 행 수 — 차이와 사유를 결정적으로 적는다(D3)
                apm_part += (
                    f" 중 타임라인 반영 {len(points)}건 — 발생 시각을 읽지 못한 이벤트 "
                    f"{len(events) - len(points)}건"
                )
                logger.info(
                    "S5 제니퍼 이벤트 %d건 중 time_ms 해석 불가 %d건(타임라인 제외)",
                    len(events), len(events) - len(points),
                )
        else:
            missing.append(APM_EVENTS)
            axes["apm"] = {"status": "missing", "reason": f"{APM_EVENTS}: {ev.error}"}
            apm_part = _axis_failed(AXIS_LABELS["apm"], ev.error, False)
    summary = " · ".join([apm_part, pol_part])
    detail = ctx.scope.polestar_reason_detail if blocked and not ctx.scope.polestar_source else None
    if not polestar_ok and not apm_ok:
        return StepResult(
            status="missing",
            missing=missing,
            data={"axes": axes, "axes_summary": summary},
            reason=summary,
            reason_detail=detail,
        )
    result = correlate(
        ctx.windows.alarm_time,
        alarms,
        [],
        notes,
        episode_points=points or None,
        alarm_axis=polestar_ok,
    )
    timeline = [
        {"t_offset_min": t.t_offset_min, "kind": t.kind, "detail": t.detail}
        for t in result.timeline
    ]
    return StepResult(
        status="ok",
        verdict=None,
        verdict_source=None,
        table={
            "columns": ["t_offset_min", "kind", "detail"],
            "rows": [[t["t_offset_min"], t["kind"], t["detail"]] for t in timeline],
        },
        data={
            "timeline": timeline,
            "alarm_summary": result.alarm_summary,
            "apm_events": events,
            "notes": list(result.notes),
            "caveat": CORRELATION_NOT_CAUSATION,
            "axes": axes,
            "axes_summary": summary,
        },
        badges=[CORRELATION_NOT_CAUSATION],
        missing=missing,
        reason=summary,
        reason_detail=detail,
        artifacts={"alarms": alarms, "episode_points": points},
    )


def process_top(ctx: StepContext) -> StepResult:
    """S6 — 폴스타 프로세스 상위(실시간 단면 — `captured_at` 배지 · 판정 없음)."""
    blocked = _polestar_reason(ctx)
    if blocked:
        return _polestar_blocked(ctx, [POLESTAR_PROCESS], blocked)
    resp = ctx.call_one(
        "polestar",
        POLESTAR_PROCESS,
        {
            "hostname": ctx.scope.polestar_server,
            "top_n": int(ctx.args.get("top_n", 10)),
            "sort": str(ctx.args.get("sort", "cpu")),
        },
    )
    if not resp.ok:
        # 실시간 조회를 시도한 스텝은 실패해도 배지를 단다(조회 시점 = 실행 시각 · 픽스처
        # partial_missing)
        res = _missing([POLESTAR_PROCESS], f"{POLESTAR_PROCESS}: {resp.error}")
        res.badges.append(realtime_badge(None, ctx.windows.alarm_time, ctx.now))
        return res
    data = resp.data or {}
    cols = ("name", "pid", "user", "pcpu", "pmem", "rss", "args")
    rows = [r for r in data.get("rows") or [] if isinstance(r, dict)]
    badge = realtime_badge(
        data.get("captured_at") or data.get("queried_at"), ctx.windows.alarm_time, ctx.now
    )
    return StepResult(
        status="ok",
        table={"columns": list(cols), "rows": [[r.get(c) for c in cols] for r in rows]},
        data={
            "rows": rows,
            "total_processes": data.get("total_processes"),
            "captured_at": data.get("captured_at"),
        },
        badges=[badge],
    )


def recent_changes(ctx: StepContext) -> StepResult:
    """S7 — 변경 대조: 제니퍼 소스 변경·변경 영향(reference_time만) · 폴스타 변경 이력(PG만) ·
    ITSM(미연동).

    인자: `lookback_hours`(기본 24). 후보는 알람 시각까지의 시간 근접순이며 「상관 후보」로만
    표기한다.
    """
    hours = int(ctx.args.get("lookback_hours", 24))
    alarm = datetime.fromisoformat(ctx.windows.alarm_time)
    axes: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    candidates: list[dict[str, Any]] = []
    impact: list[dict[str, Any]] = []
    instance, src = ctx.target_items()[0] if ctx.targets else (ctx.scope.alarm_instance, None)

    def _candidate(source: str, at: datetime | None, desc: str) -> None:
        if at is None:
            return
        before = int((alarm - at).total_seconds() // 60)
        if 0 <= before <= hours * 60:
            candidates.append(
                {
                    "source": source,
                    "at": at.isoformat(),
                    "minutes_before_alarm": before,
                    "description": desc,
                }
            )

    parts: dict[str, str] = {}  # 축별 결정적 요약(plans/196 D4)
    if ctx.apm is None or not instance:
        axes["apm"] = {
            "status": "missing",
            "reason": "APM 게이트웨이 미구성" if ctx.apm is None else "대상 인스턴스 없음",
        }
        missing += [APM_SOURCE_CHANGES, APM_CHANGE_IMPACT]
        parts["apm"] = _axis_failed(AXIS_LABELS["apm"], axes["apm"]["reason"], True)
    else:
        args = {"instance_name": instance, "reference_time": ctx.windows.reference_time}
        sc, ci = ctx.call(
            "apm",
            [
                (APM_SOURCE_CHANGES, _apm_args(ctx, APM_SOURCE_CHANGES, src, **args)),
                (APM_CHANGE_IMPACT, _apm_args(ctx, APM_CHANGE_IMPACT, src, **args)),
            ],
        )
        for r in (sc, ci):
            if not r.ok:
                missing.append(r.tool)
        axes["apm"] = {
            "status": "ok" if sc.ok or ci.ok else "missing",
            "reason": _reasons([sc, ci]) or None,
        }
        for row in (sc.data or {}).get("rows") or []:
            if isinstance(row, dict):
                _candidate(
                    APM_SOURCE_CHANGES,
                    naive_time(row.get("change_detected_at")),
                    f"{row.get('instance_name') or instance} 소스 변경 감지",
                )
        impact = [r for r in (ci.data or {}).get("rows") or [] if isinstance(r, dict)]
        if sc.ok or ci.ok:
            n_apm = sum(
                1 for r in (sc.data or {}).get("rows") or [] if isinstance(r, dict)
            ) + len(impact)
            parts["apm"] = _axis_count(AXIS_LABELS["apm"], n_apm, "변경 없음", "변경")
            if axes["apm"]["reason"]:
                parts["apm"] += f"(일부 조회 못 함 — {axes['apm']['reason']})"
        else:
            parts["apm"] = _axis_failed(AXIS_LABELS["apm"], axes["apm"]["reason"], False)
    blocked = _polestar_reason(ctx)
    link_skipped = bool(blocked) and _polestar_skipped(ctx)
    if blocked:
        if link_skipped:  # 연결 힌트로 생략 — 결손이 아니다(plans/196 §5.2)
            axes["polestar"] = {"status": "skipped", "reason": blocked}
        else:
            axes["polestar"] = {"status": "missing", "reason": blocked}
            missing.append(TOOL_CHANGES)
        parts["polestar"] = _axis_failed(AXIS_LABELS["polestar"], blocked, True)
    else:
        scope = _evidence_scope(ctx)
        pc = ctx.call_one(
            "polestar",
            TOOL_CHANGES,
            {
                "source": scope.source,
                "server_name": scope.server_name,
                "reference_time": ctx.windows.reference_time,
                "lookback_minutes": hours * 60,
            },
        )
        if not pc.ok:
            axes["polestar"] = {"status": "missing", "reason": pc.error}
            missing.append(TOOL_CHANGES)
            parts["polestar"] = _axis_failed(AXIS_LABELS["polestar"], pc.error, False)
        else:
            notes: list[str] = []
            changes = _changes(pc.data or {}, scope, notes)
            if changes is None:
                axes["polestar"] = {
                    "status": "skipped",
                    "reason": " · ".join(notes) or "unsupported",
                }
                skip_reason = axes["polestar"]["reason"]
                parts["polestar"] = f"{AXIS_LABELS['polestar']}: 생략 — {skip_reason}"
            else:
                axes["polestar"] = {"status": "ok", "reason": " · ".join(notes) or None}
                for c in changes:
                    _candidate(TOOL_CHANGES, naive_time(c.time), c.description)
                parts["polestar"] = _axis_count(
                    AXIS_LABELS["polestar"], len(changes), "변경 없음", "변경"
                )
    itsm = _adapter_fetch(ctx, ITSM, "S7")
    axes["itsm"] = {"status": itsm.get("status", "skipped"), "reason": itsm.get("reason")}
    # ITSM 미연동은 상시 상태라 해석 근거(요약)에서 빼고 결과 줄 꼬리로만 둔다(D4 ③)
    if axes["itsm"]["reason"] != NOT_CONNECTED:
        parts["itsm"] = f"{AXIS_LABELS['itsm']}: {step_status_label(axes['itsm']['status'])}" + (
            f" — {axes['itsm']['reason']}" if axes["itsm"]["reason"] else ""
        )
    summary = " · ".join(parts.values())
    detail = ctx.scope.polestar_reason_detail if blocked and not ctx.scope.polestar_source else None
    candidates.sort(key=lambda c: (c["minutes_before_alarm"], c["source"]))
    queried = [
        a for k, a in axes.items() if k != "itsm" and not (k == "polestar" and link_skipped)
    ]
    if not any(a["status"] in ("ok", "skipped") for a in queried):
        return StepResult(
            status="missing",
            missing=missing,
            data={"axes": axes, "axes_summary": summary},
            reason=summary,
            reason_detail=detail,
        )
    return StepResult(
        status="ok",
        table={
            "columns": ["source", "at", "minutes_before_alarm", "description"],
            "rows": [
                [c["source"], c["at"], c["minutes_before_alarm"], c["description"]]
                for c in candidates
            ],
        },
        data={
            "candidates": candidates,
            "change_impact": impact,
            "axes": axes,
            "lookback_hours": hours,
            "caveat": CHANGE_CANDIDATE_NOTE,
            "axes_summary": summary,
        },
        badges=[CHANGE_CANDIDATE_NOTE],
        missing=missing,
        reason=summary or None,
        reason_detail=detail,
    )


def _adapter_fetch(ctx: StepContext, name: str, step_id: str) -> dict[str, Any]:
    """외부 소스 어댑터 호출 — 미연동이면 `skipped: not_connected`(§6 · 침묵 생략 금지)."""
    return fetch_or_not_connected(
        ctx.adapters.get(name), step_id, ctx.scope.to_dict(), ctx.windows.to_dict()
    )


def dpm_db_health(ctx: StepContext) -> StepResult:
    """S8 — DPM 어댑터(G-6 · §6). 기본 레지스트리는 미연동 스텁이라 skipped(not_connected)."""
    res = _adapter_fetch(ctx, DPM, "S8")
    return StepResult(
        status=res.get("status", "skipped"),
        reason=res.get("reason"),
        data=dict(res.get("data") or {}),
        missing=list(res.get("missing") or []),
    )


# ── §4.1 보조 액션 (호출 + 표 정리만) ────────────────────────────────────────


def _simple_table(rows: Sequence[Mapping[str, Any]], cols: Sequence[str]) -> dict[str, Any]:
    return {"columns": list(cols), "rows": [[r.get(c) for c in cols] for r in rows]}


def events_errors(ctx: StepContext) -> StepResult:
    """was.error — 사건창 오류 레코드의 유형 상위(`apm_events(record=error)`)."""
    blocked = _need_apm(ctx, [APM_EVENTS])
    if blocked:
        return blocked
    name, src = ctx.target_items()[0]
    resp = ctx.call_one(
        "apm",
        APM_EVENTS,
        _apm_args(
            ctx,
            APM_EVENTS,
            src,
            instance_name=name,
            reference_time=ctx.windows.reference_time,
            lookback_minutes=ctx.windows.lookback_minutes,
            record="error",
            n=int(ctx.args.get("n", 10)),
        ),
    )
    if not resp.ok:
        return _missing([APM_EVENTS], f"{APM_EVENTS}: {resp.error}")
    by_type = [r for r in (resp.data or {}).get("errors_by_type") or [] if isinstance(r, dict)]
    cols = list(by_type[0]) if by_type else ["error_type", "count"]
    return StepResult(
        status="ok",
        table=_simple_table(by_type, cols),
        data={"errors_by_type": by_type, "rows": (resp.data or {}).get("rows") or []},
    )


def _was_cpu_top(ctx: StepContext) -> str | None:
    """앞 단계 `apm.runtime_and_pool` 결과에서 R-E가 보는 인스턴스(프로세스 CPU 최대 — 판독과
    같은 함수)."""
    s2 = next(
        (r for r in ctx.prior.values() if r.action == "apm.runtime_and_pool" and r.status == "ok"),
        None,
    )
    return was_cpu_top_instance(s2.data) if s2 is not None else None


def active_services(ctx: StepContext) -> StepResult:
    """was.active — 액티브 서비스(실시간 단면 · 배지).

    인자 `target: was_cpu_top`(plans/187 W20b)이면 대상 = 앞 S2의 프로세스 CPU 최대 인스턴스(R-E
    `cpu_owner`가 본 인스턴스) — 없으면 범위 첫 대상.
    """
    picked = _was_cpu_top(ctx) if ctx.args.get("target") == "was_cpu_top" else None
    if picked is not None:
        # 소스는 같은 이름의 범위 대상 행을 따른다(plans/190 W1 — 없으면 None = 소스 미확정)
        ctx.target_sources = [dict(ctx.scope.target_entries()).get(picked)]
        ctx.targets = [picked]
    blocked = _need_apm(ctx, [APM_ACTIVE_SERVICES])
    if blocked:
        return blocked
    name, src = ctx.target_items()[0]
    resp = ctx.call_one(
        "apm",
        APM_ACTIVE_SERVICES,
        _apm_args(ctx, APM_ACTIVE_SERVICES, src, instance_name=name, n=int(ctx.args.get("n", 10))),
    )
    if not resp.ok:
        return _missing([APM_ACTIVE_SERVICES], f"{APM_ACTIVE_SERVICES}: {resp.error}")
    data = resp.data or {}
    rows = [r for r in data.get("rows") or [] if isinstance(r, dict)]
    return StepResult(
        status="ok",
        table=_simple_table(
            rows, ("application", "elapsed_ms", "cpu_ms", "running_mode", "running_text")
        ),
        data={"rows": rows, "summary": data.get("summary")},
        badges=[realtime_badge(data.get("queried_at"), ctx.windows.alarm_time, ctx.now)],
    )


#: S3b — 앞 단계 실행 중 요청 중 CPU 시간 상위 몇 건의 SQL 문을 보나 · SQL 문 바이트 상한(§5.6.4 ⓓ).
ACTIVE_SQL_TOP = 3
ACTIVE_SQL_MAX_BYTES = 1024
#: SQL 리터럴 — 인용 리터럴(PG 달러 인용 `$$…$$`·`$tag$…$tag$` · 작은따옴표(`''`·`\'` 이스케이프) ·
#: 큰따옴표(MariaDB 문자열 — 따옴표 식별자도 가려지나 노출보다 과가림 쪽)) → 16진(`0x…`) →
#: 식별자에 붙지 않은 수치(음수·`.5`·지수 `1e10`). `col1`·`t2.x`·자리표시 `$1`은 건드리지 않는다.
_SQL_QUOTED_LITERAL = re.compile(
    r"\$(?P<tag>(?:[A-Za-z_]\w*)?)\$.*?\$(?P=tag)\$"
    r"|'(?:[^'\\]|\\.|'')*'"
    r'|"(?:[^"\\]|\\.|"")*"',
    re.S,
)
_SQL_HEX_LITERAL = re.compile(r"(?<![\w.$])0[xX][0-9A-Fa-f]+(?![\w.])")
_SQL_NUM_LITERAL = re.compile(
    r"(?<![\w.$])-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?![\w.])"
)


def mask_active_sql(text: str) -> str:
    """실행 중 SQL 문 표시값 — 리터럴(문자열·숫자) → `?` · 비밀값 꼴 가림 · 1024바이트 상한(「…」).

    게이트웨이가 이미 리터럴을 가렸어도 다시 가린다(패키지 경계 — 게이트웨이 함수는 import하지
    않는다 · D-274).
    """
    out = _SQL_QUOTED_LITERAL.sub("?", text)
    out = _SQL_NUM_LITERAL.sub("?", _SQL_HEX_LITERAL.sub("?", out))
    return clip_bytes(mask_secrets(out), ACTIVE_SQL_MAX_BYTES)


def _cpu_ms(row: Mapping[str, Any]) -> float:
    value = row.get("cpu_ms")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return -1.0
    return float(value)


def active_sql(ctx: StepContext) -> StepResult:
    """S3b — 앞 단계 실행 중 요청(`apm.active_services`) 중 CPU 시간 상위 3건의 실행 중 SQL 문.

    각 행의 `active_ref`를 그대로 `apm_active_detail`에 넘긴다(현재값 — 배지). SQL 문은
    `mask_active_sql`을 거친다(리터럴 원문 노출 금지).
    """
    if ctx.apm is None:
        return _missing([APM_ACTIVE_DETAIL], "APM 게이트웨이 미구성")
    prior = next(
        (r for r in ctx.prior.values() if r.action == "apm.active_services" and r.status == "ok"),
        None,
    )
    if prior is None:
        return _missing([], "앞 단계 실행 중 요청(apm.active_services) 결과 없음")
    rows = [r for r in prior.data.get("rows") or [] if isinstance(r, dict)]
    cols = ("instance", "application", "cpu_ms", "elapsed_ms", "sql")
    if not rows:
        return StepResult(
            status="ok",
            table=_simple_table([], cols),
            data={"rows": [], "queried_at": None},
            reason="실행 중 요청 없음 — 상세 조회 대상 없음",
        )
    picked = sorted(
        (r for r in rows if isinstance(r.get("active_ref"), dict)), key=_cpu_ms, reverse=True
    )[:ACTIVE_SQL_TOP]
    if not picked:
        return _missing([], "실행 중 요청에 상세 참조(active_ref) 없음 — 상세 조회 불가")
    calls = [
        (APM_ACTIVE_DETAIL, {k: v for k, v in r["active_ref"].items() if v is not None})
        for r in picked
    ]
    resps = ctx.call("apm", calls)
    out: list[dict[str, Any]] = []
    queried_at: str | None = None
    for row, resp in zip(picked, resps):
        if not resp.ok:
            continue
        detail = _first_row(resp.data) or {}
        if queried_at is None and isinstance((resp.data or {}).get("queried_at"), str):
            queried_at = str((resp.data or {})["queried_at"])
        sql = detail.get("sql")
        out.append(
            {
                "instance": row.get("instance_name"),
                "application": row.get("application"),
                "txid": row["active_ref"].get("txid"),
                "cpu_ms": row.get("cpu_ms"),
                "elapsed_ms": row.get("elapsed_ms"),
                "sql": mask_active_sql(str(sql)) if sql is not None else None,
            }
        )
    missing = [APM_ACTIVE_DETAIL] if any(not r.ok for r in resps) else []
    if not out:
        return StepResult(status="missing", missing=missing, reason=_reasons(resps))
    return StepResult(
        status="ok",
        table=_simple_table(out, cols),
        data={"rows": out, "queried_at": queried_at},
        badges=[realtime_badge(queried_at, ctx.windows.alarm_time, ctx.now)],
        missing=missing,
        reason=_reasons(resps) or None,
    )


def _polestar_simple(
    ctx: StepContext, tool: str, extra: dict[str, Any], cols: Sequence[str]
) -> StepResult:
    blocked = _polestar_reason(ctx)
    if blocked:
        return _polestar_blocked(ctx, [tool], blocked)
    resp = ctx.call_one(
        "polestar",
        tool,
        {"source": ctx.scope.polestar_source, "server_name": ctx.scope.polestar_server, **extra},
    )
    if not resp.ok:
        return _missing([tool], f"{tool}: {resp.error}")
    rows = [r for r in (resp.data or {}).get("rows") or [] if isinstance(r, dict)]
    return StepResult(
        status="ok", table=_simple_table(rows, list(rows[0]) if rows else cols), data={"rows": rows}
    )


def resource_status(ctx: StepContext) -> StepResult:
    """host.process·host.network — 서버·서브리소스 가용 상태."""
    return _polestar_simple(ctx, POLESTAR_RESOURCE_STATUS, {}, ("name", "status"))


def topology(ctx: StepContext) -> StepResult:
    """host.network — 가용성 의존 토폴로지(인자 `max_hops` 기본 3)."""
    return _polestar_simple(
        ctx, POLESTAR_TOPOLOGY, {"max_hops": int(ctx.args.get("max_hops", 3))}, ("name", "status")
    )


def alarm_history(ctx: StepContext) -> StepResult:
    """재발 확인 — 같은 (서버, 알람명) 최근 이력(인자 `lookback_days` 기본 20 · §4.0)."""
    if not ctx.scope.alarm_name:
        return _missing([POLESTAR_ALARM_HISTORY], "알람명 없음 — 재발 조회 불가")
    return _polestar_simple(
        ctx,
        POLESTAR_ALARM_HISTORY,
        {"alarm_name": ctx.scope.alarm_name, "hours": int(ctx.args.get("lookback_days", 20)) * 24},
        ("alarm_time", "severity", "alarm_name"),
    )


#: 액션 ID → 스텝 함수. `synthesize`(S9)는 W3 담당이라 없다 — 실행기가 자리만 만든다.
STEP_LIBRARY: dict[str, StepFn] = {
    "scope.resolve_was_targets": resolve_was_targets,
    "scope.host_to_was": host_to_was,
    "apm.golden_signals": golden_signals,
    "apm.runtime_and_pool": runtime_and_pool,
    "apm.slow_tx_breakdown": slow_tx_breakdown,
    "host.usage_trend": usage_trend,
    "xsource.related_alarms": related_alarms,
    "host.process_top": process_top,
    "change.recent": recent_changes,
    "dpm.db_health": dpm_db_health,
    "apm.events_errors": events_errors,
    "apm.active_services": active_services,
    "apm.active_sql": active_sql,
    "host.resource_status": resource_status,
    "host.topology": topology,
    "host.alarm_history": alarm_history,
}

#: 대상 확정 액션 — 실행기가 결과의 `artifacts["scope"]`로 범위를 갱신한다.
SCOPE_ACTIONS = frozenset({"scope.resolve_was_targets", "scope.host_to_was"})


__all__ = [
    "ACTION_AXIS",
    "ACTION_TITLES",
    "APM_SOURCE_IDS_TOOLS",
    "APM_SOURCE_UNRESOLVED_LIMIT",
    "APM_XVIEW_CONFIRM_TOOLS",
    "XVIEW_CONFIRM_LIMIT",
    "XVIEW_CONFIRM_REQUIRED",
    "BaselineWindow",
    "CORRELATION_NOT_CAUSATION",
    "CHANGE_CANDIDATE_NOTE",
    "DEFAULT_P95_RATIO",
    "NOT_CONNECTED",
    "NO_WAS_VERDICT",
    "AXIS_LABELS",
    "MIXED_AXIS_ACTIONS",
    "POLESTAR_AXIS_ACTIONS",
    "POLESTAR_LINK_SKIP",
    "POLESTAR_UNRESOLVED",
    "RunScope",
    "RunWindows",
    "SCOPE_ACTIONS",
    "scope_polestar_reason",
    "section_axis_status",
    "STEP_ABORTED",
    "STEP_LIBRARY",
    "StepContext",
    "StepFn",
    "StepResult",
    "ToolResponse",
    "VERDICT_SOURCE_GATEWAY",
    "VERDICT_SOURCE_RULE",
    "naive_time",
    "parse_response",
    "realtime_badge",
    "target_verdict",
    "tool_label",
]
