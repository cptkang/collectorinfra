"""WAS·미들웨어(APM) 1급 처리기 `apm_query` — plans/125 A-3 · 87 J5 본체 쪽 이관(D-281 ①).

**LLM 0회.** 분해 LLM 이 고른 보기(`views[]` — 레지스트리 `solutions[apm].views` 닫힌 어휘)를
게이트웨이 도구 고정 표로 바꾸고, 대상(hostname)·창을 코드가 정해 부른다(plans/125 §4.6 · Q-2).

- **활성일 때만 등록**: 엔드포인트(`MCP_SOURCE_ENDPOINTS` 의 `apm`)가 없으면 처리기도 분해 프롬프트
  줄도 없다 — 비활성 배포는 바이트 불변(신규 `enable_*` 0 · D-162 · D-251 ⑥). 고정 처리기 목록
  (`SUBAGENT_REGISTRY`)에는 넣지 않는다 — 활성일 때만 `active_extra_subagents`가 붙인다.
- **대상**: 선행 결과·이번 턴 식별자·직전 대상에서 hostname 을 고른다(`resolve_targets` 공용 규칙).
  hostname 이 필요한 보기인데 대상이 없으면 인스턴스 목록 보기(`apm.instances`)를 **코드가 먼저**
  부른다(첫 홉 삽입 — §4.2 · §4.3 ③).
- **창**: 파서의 기간(`time_range`)을 보기의 창 상한으로 자르고 고지한다. 창 밖(하루 넘게 지난
  기간)은 조회하지 않고 사유를 남긴다 — **폴스타 값으로 대신하지 않는다**(§4.2 · Q-9).
- **실패**: 게이트웨이 미가용·도구 오류는 조회 안 함/행 0 으로 끝내고 사유를 싣는다. 재계획하지
  않는다(121 §4.7 「소스 불가」).
- **경계**: 게이트웨이 패키지 import 0(D-274 ③) · 인스턴스 정합 재구현 0(D-274 ⑤ — 게이트웨이 도구를
  부른다) · 반환 봉투는 해석만 하고 바꾸지 않는다. 벤더 리터럴은 레지스트리 데이터에만 둔다.

계층: orchestration.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from langchain_core.language_models import BaseChatModel

from src.clients.source_mcp_client import SessionFactory, SourceMcpError, open_source_session
from src.config import AppConfig
from src.orchestration.db_access import access_denied_result
from src.orchestration.entity_link import (
    LINKED,
    UNLINKED,
    LinkEntry,
    ledger_line,
    ledger_summary,
    link_hostnames,
)
from src.orchestration.investigation_audit import BACKEND_APM, audited_investigation
from src.orchestration.subagents import SubAgentSpec
from src.routing.db_authz import SOURCE_ACCESS_DENIED_MESSAGE, is_source_allowed
from src.routing.registry import ViewSpec, get_registry
from src.utils.prior_targets import TargetRef, resolve_targets

logger = logging.getLogger(__name__)

#: 처리기 이름(분해 어휘 — 활성일 때만 렌더) · 시스템 코드(레지스트리 `solutions[apm]`).
APM_QUERY_AGENT = "apm_query"
APM_SYSTEM = "apm"
#: 보기를 고르지 않았을 때의 기본 보기(영역 기본 — was_performance).
DEFAULT_VIEW = "apm.app_health"
#: 대상 없이 부를 수 있는 목록 보기 — 대상 미지정이면 코드가 먼저 부른다.
INSTANCES_VIEW = "apm.instances"
#: task 하나가 싣는 보기 상한(프롬프트 지시 1~2개 · 넘으면 앞에서 자른다).
MAX_VIEWS = 3
#: 거부·실패 사유 키(host_inspect 와 같은 이름 — 감사가 같은 키를 읽는다).
DEGRADED_KEY = "degraded_reason"
#: 결과 메타(감사·계획 요약·후속 조합이 읽는다).
META_KEY = "apm_query"
#: 창 밖 판정 — 기간 끝이 지금보다 이만큼 이전이면 창 밖으로 본다(보존 기간 미확인 — plans/125 U-3).
_OUT_OF_WINDOW_AFTER = timedelta(days=1)

#: 테스트가 모의 세션 공장을 끼운다(이 호스트 루트 파이썬에는 `mcp` 가 없다).
_SESSION_FACTORY: SessionFactory | None = None


# ── 활성 · 어휘 ──────────────────────────────────────────────────────────────

def apm_endpoint(app_config: Any) -> tuple[str, str | None] | None:
    """(URL, 토큰) 또는 None(비활성). 설정 대역(MagicMock)은 비활성으로 읽는다."""
    getter = getattr(getattr(app_config, "dbhub", None), "source_endpoint", None)
    if not callable(getter):
        return None
    try:
        endpoint = getter(APM_SYSTEM)
    except Exception:  # noqa: BLE001 — 설정 대역·깨진 설정은 비활성
        return None
    if isinstance(endpoint, tuple) and endpoint and isinstance(endpoint[0], str) and endpoint[0]:
        return endpoint
    return None


def apm_active(app_config: Any) -> bool:
    """APM 시스템이 활성인가(레지스트리 등재 + 엔드포인트 설정)."""
    return bool(get_registry().views_of(APM_SYSTEM)) and apm_endpoint(app_config) is not None


def apm_views() -> tuple[ViewSpec, ...]:
    """레지스트리 보기 표(선언 순서)."""
    return get_registry().views_of(APM_SYSTEM)


def known_view_ids() -> frozenset[str]:
    return frozenset(v.id for v in apm_views())


def sanitize_views(raw: Any) -> list[str]:
    """LLM 이 낸 보기 목록 → 닫힌 어휘만(순서 유지 · 중복 제거 · 상한 `MAX_VIEWS`)."""
    items = [raw] if isinstance(raw, str) else (raw if isinstance(raw, (list, tuple)) else [])
    known = known_view_ids()
    kept: list[str] = []
    for item in items:
        code = str(item).strip() if isinstance(item, str) else ""
        if code in known and code not in kept:
            kept.append(code)
    return kept[:MAX_VIEWS]


def render_agent_line() -> str:
    """분해 프롬프트의 담당 목록 한 줄(활성일 때만 삽입)."""
    return f"- **{APM_QUERY_AGENT}**: {APM_QUERY_SPEC.purpose}"


def render_view_rows() -> str:
    """분해 프롬프트의 보기 표 — 레지스트리 파생(사본 금지 · D-053)."""
    reg = get_registry()
    labels = {c.code: c.label for c in reg.capability_specs()}
    lines = []
    for view in apm_views():
        need = "대상 서버 필요" if view.required_input else "대상 없이 전체 목록"
        limit = f" · {view.limit}" if view.limit else ""
        text = view.label or labels.get(view.capability, view.capability)
        lines.append(f"- `{view.id}`: {text} ({need}{limit})")
    return "\n".join(lines)


# ── 창 ────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WindowPlan:
    """보기 1개의 조회 창 — mode: current(현재·도구 기본) · window(구간) · out(조회 안 함)."""

    mode: str
    args: dict[str, Any] = field(default_factory=dict)
    note: str = ""


def _parse_bound(value: Any, *, end: bool) -> datetime | None:
    text = str(value or "").strip().replace("T", " ")
    if not text:
        return None
    try:
        if len(text) == 10:
            day = datetime.fromisoformat(text)
            return day + timedelta(days=1) - timedelta(seconds=1) if end else day
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError:
        return None


def plan_window(view: ViewSpec, time_range: Any, now: datetime) -> WindowPlan:
    """파서 기간(`time_range` {start, end})을 보기 창 상한으로 자른다.

    창 밖(기간 끝이 하루 넘게 지남)이면 조회하지 않는다.
    """
    if not isinstance(time_range, dict):
        return WindowPlan("current")
    start = _parse_bound(time_range.get("start"), end=False)
    end = _parse_bound(time_range.get("end"), end=True)
    if start is None and end is None:
        return WindowPlan("current")
    end = min(end or now, now)
    if now - end > _OUT_OF_WINDOW_AFTER:
        return WindowPlan("out", note=(
            f"요청 기간이 APM 조회 창({view.limit or '현재값'}) 밖이라 조회하지 않았습니다"
            " — 과거 기간은 보존 기간 확인이 필요합니다"))
    if view.window_max_minutes is None:
        return WindowPlan("current", note="현재값 기준입니다(기간 조회를 지원하지 않는 보기)")
    span = max(1, int(((end - (start or end)).total_seconds()) // 60))
    reference = None if now - end <= timedelta(minutes=1) else end.isoformat(timespec="seconds")
    if span <= view.window_max_minutes:
        return WindowPlan("window", {"reference_time": reference, "lookback_minutes": span})
    return WindowPlan(
        "window",
        {"reference_time": reference, "lookback_minutes": view.window_max_minutes},
        note=(f"요청 기간 {span}분 중 마지막 {view.window_max_minutes}분만 조회했습니다"
              f"(도구 상한 — {view.limit})"),
    )


# ── 대상 ──────────────────────────────────────────────────────────────────────

def _int_setting(app_config: Any, name: str, default: int) -> int:
    value = getattr(getattr(app_config, "composite", None), name, default)
    valid = isinstance(value, int) and not isinstance(value, bool) and value > 0
    return value if valid else default


def resolve_apm_targets(isolated: dict[str, Any], max_targets: int) -> list[TargetRef]:
    """선행 결과 → 이번 턴 식별자 → 직전 대상 순으로 대상을 고른다(공용 규칙 `resolve_targets`).

    선행 결과는 `prior_targets`(해소본) 또는 `prior_rows`(행)에서 온다 — 이 처리기는 신규라 조사
    대상 승계 플래그(`COMPOSITE_PRIOR_TARGETS_ENABLED`)와 무관하게 데이터 의존을 따른다
    (`input_from`).
    """
    from src.orchestration.process_query import _targets_from_prior_rows  # 지연 — 순환 방지

    parsed = isolated.get("parsed_requirements") or {}
    ctx = isolated.get("conversation_context") or {}
    prior = isolated.get("prior_targets") or _targets_from_prior_rows(
        isolated.get("prior_rows"), db_id=None, max_targets=max_targets,
    )
    resolution = resolve_targets(
        filter_conditions=parsed.get("filter_conditions"),
        prior_targets=prior,
        previous_entities=ctx.get("previous_entities"),
        db_id=None,
        max_targets=max_targets,
    )
    return list(resolution.targets)


# ── 실행 ──────────────────────────────────────────────────────────────────────

@dataclass
class _Call:
    view: ViewSpec
    hostname: str | None
    args: dict[str, Any]
    envelope: dict[str, Any] | None = None
    error: str | None = None


def _envelope_error(envelope: dict[str, Any]) -> str | None:
    if envelope.get("error"):
        return f"{envelope.get('error')}: {str(envelope.get('reason') or '')[:160]}"
    return None


async def _run_calls(session: Any, calls: list[_Call], concurrency: int) -> None:
    sem = asyncio.Semaphore(concurrency)

    async def one(call: _Call) -> None:
        async with sem:
            try:
                call.envelope = await session.call_tool(call.view.tool, call.args)
            except SourceMcpError as e:
                call.error = str(e)
                return
            call.error = _envelope_error(call.envelope)

    await asyncio.gather(*(one(c) for c in calls))


def _call_args(view: ViewSpec, hostname: str | None, window: WindowPlan,
               thread_id: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "hostname": hostname, "thread_id": str(thread_id) if thread_id else None,
    }
    args.update(window.args)
    return args


def _refusal(message: str, reason: str, meta: dict[str, Any]) -> dict[str, Any]:
    """조회하지 못함 — 텍스트 결과(host_inspect 와 같은 규약 · plans/121 TP-1.5)."""
    return {"error": message, DEGRADED_KEY: reason, "final_response": message, META_KEY: meta,
            "source_status": [meta["source_status"]]}


def _status(label: str, status: str, rows: int, reason: str = "") -> dict[str, Any]:
    return {"system": APM_SYSTEM, "label": label, "status": status, "rows": rows,
            "reason": reason}


async def run_apm_query(
    task: dict[str, Any],
    isolated: dict[str, Any],
    *,
    llm: BaseChatModel,
    app_config: AppConfig,
    now: datetime | None = None,
) -> dict[str, Any]:
    """APM 게이트웨이 보기를 부른다(handler 규약 · LLM 미사용).

    Returns:
        성공: `organized_data`·`query_results`(행) + `apm_query`(보기·대상·삽입 단계·출처·실패)
        + `source_status`. 실패: 텍스트 결과 `{error, degraded_reason, final_response}` + 메타.
    """
    del llm
    # 관측 소스 인가(plans/125 A-7 · D-272 ⑩) — 실행 경계에서 판정한다(2단 오케스트레이터 ·
    # 3단 계획 루프가 같은 처리기를 부른다). 거부 문구·결과에는 소스 이름을 싣지 않는다(D-264 ②).
    role = isolated.get("user_role")
    if not is_source_allowed(APM_SYSTEM, isolated.get("allowed_sources"), role):
        logger.info("%s 인가 거부: 관측 소스 권한 없음(역할=%s)", APM_QUERY_AGENT, role)
        return access_denied_result(SOURCE_ACCESS_DENIED_MESSAGE)
    now = now or datetime.now()
    label = get_registry().system_label(APM_SYSTEM)
    views = sanitize_views(task.get("views")) or [DEFAULT_VIEW]
    by_id = {v.id: v for v in apm_views()}
    meta: dict[str, Any] = {"views": views, "hostnames": [], "inserted_steps": [],
                            "provenance": [], "failures": [], "notes": []}
    endpoint = apm_endpoint(app_config)
    if endpoint is None:
        meta["source_status"] = _status(label, "unavailable", 0, "엔드포인트 미설정")
        return _refusal(f"{label}이(가) 연결되어 있지 않아 조회하지 않았습니다.",
                        "source_unavailable", meta)

    max_targets = _int_setting(app_config, "max_targets", 10)
    targets = resolve_apm_targets(isolated, max_targets)
    # 패싯 변환(plans/125 E-3) — hostname 없는 대상은 간선 표 경로(E2 등록명 → hostname)로 바꾼다.
    hostnames, ledger, link_steps = await link_hostnames(
        targets, consumer=APM_SYSTEM, app_config=app_config)
    meta["inserted_steps"] += link_steps

    parsed = isolated.get("parsed_requirements") or {}
    windows = {vid: plan_window(by_id[vid], parsed.get("time_range"), now) for vid in views}
    for vid, plan in windows.items():
        if plan.note:
            meta["notes"].append(f"{vid}: {plan.note}")
    thread_id = isolated.get("thread_id")
    concurrency = _int_setting(app_config, "fanout_concurrency", 3)
    timeout = getattr(getattr(app_config, "dbhub", None), "source_call_timeout", 10.0)
    call_timeout = float(timeout) if isinstance(timeout, (int, float)) else 10.0

    url, token = endpoint
    try:
        async with open_source_session(url, token, call_timeout=call_timeout, label=label,
                                       session_factory=_SESSION_FACTORY) as session:
            needs_host = [v for v in views if by_id[v].required_input and windows[v].mode != "out"]
            if needs_host and not hostnames:
                hostnames, step = await _insert_instances_step(
                    session, max_targets, thread_id, by_id.get(INSTANCES_VIEW))
                meta["inserted_steps"].append(step)
            calls: list[_Call] = []
            for vid in views:
                view, plan = by_id[vid], windows[vid]
                if plan.mode == "out":
                    meta["failures"].append({"view": vid, "hostname": None, "reason": plan.note})
                    continue
                if view.required_input:
                    calls += [_Call(view, h, _call_args(view, h, plan, thread_id))
                              for h in hostnames]
                elif hostnames:
                    calls += [_Call(view, h, _call_args(view, h, plan, thread_id))
                              for h in hostnames]
                else:
                    calls.append(_Call(view, None, _call_args(view, None, plan, thread_id)))
            await _run_calls(session, calls, concurrency)
    except SourceMcpError as e:
        meta["source_status"] = _status(label, "unavailable", 0, str(e))
        return _refusal(f"{label}에 연결하지 못해 조회하지 않았습니다({e}). 다른 소스의 값으로 대신"
                        " 답하지 않았습니다.", "source_unavailable", meta)

    meta["hostnames"] = hostnames
    rows = _collect(calls, meta)
    ledger += _apm_hop_ledger(calls)
    meta["link_ledger"] = [entry.as_dict() for entry in ledger]
    meta["link_summary"] = ledger_summary(ledger)
    line = ledger_line(ledger)
    if line:
        meta["notes"].insert(0, line)
    ok_calls = [c for c in calls if c.error is None]
    if calls and not ok_calls:
        meta["source_status"] = _status(label, "unavailable", 0,
                                        "; ".join(f["reason"] for f in meta["failures"][:3]))
        return _refusal(f"{label} 조회가 모두 실패했습니다 — " + meta["source_status"]["reason"],
                        "apm_calls_failed", meta)
    if not calls:
        reason = "; ".join(f["reason"] for f in meta["failures"][:3]) or "조회 대상이 없습니다"
        meta["source_status"] = _status(label, "not_queried", 0, reason)
        return _refusal(f"{label}을(를) 조회하지 않았습니다 — {reason}", "apm_not_queried", meta)
    status = "partial" if meta["failures"] else ("ok" if rows else "empty")
    meta["source_status"] = _status(
        label, status, len(rows), "; ".join(f["reason"] for f in meta["failures"][:3]))
    return {
        "organized_data": {
            "summary": _summary(label, views, by_id, meta, len(rows)),
            "rows": rows,
            "column_mapping": None,
            "resolved_mapping": None,
            "is_sufficient": bool(rows),
            "sheet_mappings": None,
        },
        "query_results": rows,
        META_KEY: meta,
        "source_status": [meta["source_status"]],
    }


async def _insert_instances_step(
    session: Any, max_targets: int, thread_id: Any, view: ViewSpec | None,
) -> tuple[list[str], dict[str, Any]]:
    """대상 미지정 — 인스턴스 목록 보기를 먼저 불러 hostname 을 고른다(첫 홉 삽입 · LLM 0)."""
    step: dict[str, Any] = {
        "view": INSTANCES_VIEW, "reason": "대상 서버 미지정 — 인스턴스 목록으로 선정",
    }
    if view is None:
        step["error"] = "인스턴스 목록 보기가 레지스트리에 없다"
        return [], step
    call = _Call(view, None, {"thread_id": str(thread_id) if thread_id else None})
    await _run_calls(session, [call], 1)
    if call.error:
        step["error"] = call.error
        return [], step
    hosts = list(dict.fromkeys(
        str(r.get("hostname")).strip() for r in (call.envelope or {}).get("rows") or []
        if isinstance(r, dict) and r.get("hostname") and r.get("match_confidence")
    ))
    step["hosts"] = len(hosts)
    if len(hosts) > max_targets:
        step["truncated"] = len(hosts) - max_targets
        hosts = hosts[:max_targets]
    return hosts, step


def _apm_hop_ledger(calls: list[_Call]) -> list[LinkEntry]:
    """hostname → WAS 인스턴스 정합(간선 E1r · 게이트웨이 소유) 결과를 장부에 옮긴다.

    호스트당 1행이다.
    """
    entries: dict[str, LinkEntry] = {}
    for call in calls:
        if not call.hostname or not call.view.required_input or call.hostname in entries:
            continue
        env = call.envelope or {}
        resolution = env.get("instance_resolution")
        if call.error is None and isinstance(resolution, dict):
            matched = bool(resolution.get("matched"))
            entries[call.hostname] = LinkEntry(
                call.hostname, "apm_instance", "E1r", LINKED if matched else UNLINKED,
                grade=str(resolution.get("confidence")) if matched else None,
                reason="" if matched else str(resolution.get("reason") or ""))
        elif call.error and "instance_unresolved" in call.error:
            entries[call.hostname] = LinkEntry(call.hostname, "apm_instance", "E1r", UNLINKED,
                                               reason=call.error[:120])
    return list(entries.values())


def _collect(calls: list[_Call], meta: dict[str, Any]) -> list[dict[str, Any]]:
    """봉투 → 행 · 출처(보기·도구·대상·기준 시각·창·정합) · 실패 사유."""
    rows: list[dict[str, Any]] = []
    limits: list[str] = []
    for call in calls:
        if call.error:
            meta["failures"].append({"view": call.view.id, "hostname": call.hostname,
                                     "reason": call.error})
            continue
        env = call.envelope or {}
        resolution = env.get("instance_resolution") if isinstance(env.get("instance_resolution"),
                                                                    dict) else None
        meta["provenance"].append({
            "view": call.view.id, "tool": env.get("tool") or call.view.tool,
            "hostname": call.hostname, "queried_at": env.get("queried_at"),
            "window": env.get("window"), "rows": env.get("row_count"),
            "confidence": (resolution or {}).get("confidence"),
        })
        for limit in env.get("limits") or []:
            if isinstance(limit, str) and limit not in limits:
                limits.append(limit)
        for row in env.get("rows") or []:
            if not isinstance(row, dict):
                continue
            out = dict(row)
            if call.hostname and "hostname" not in out:
                out["hostname"] = call.hostname
            rows.append(out)
    meta["limits"] = limits
    return rows


def _summary(label: str, views: list[str], by_id: dict[str, ViewSpec], meta: dict[str, Any],
             row_count: int) -> str:
    """결정적 요약(LLM 0) — 조회 범위 · 기준 시각 · 잘림 · 실패."""
    reg = get_registry()
    cap_labels = {c.code: c.label for c in reg.capability_specs()}
    view_text = ", ".join(
        f"{by_id[v].label or cap_labels.get(by_id[v].capability, v)}({v})" for v in views
    )
    queried = sorted({p["queried_at"] for p in meta["provenance"] if p.get("queried_at")})
    hosts = len(meta["hostnames"])
    parts = [f"{label} 조회 — {view_text}: 대상 {hosts}대 · {row_count}행"
             + (f"(기준 시각 {queried[-1]})." if queried else ".")]
    for step in meta["inserted_steps"]:
        if step.get("edge"):
            if step.get("error") or step.get("errors"):
                parts.append(f"{step['edge']} 변환 실패: "
                             + "; ".join([step.get("error") or ""] + (step.get("errors") or []))
                             .strip("; "))
            continue
        if step.get("hosts") is not None:
            tail = (f"(상한으로 {step['truncated']}대 제외 — 조회한 범위 안의 결과입니다)."
                    if step.get("truncated") else ".")
            parts.append(f"대상 서버를 지정하지 않아 인스턴스 목록에서 {step['hosts']}대를"
                         f" 골랐습니다{tail}")
        elif step.get("error"):
            parts.append(f"인스턴스 목록 조회 실패: {step['error']}")
    parts += meta["notes"][:3]
    parts += meta.get("limits", [])[:3]
    failed = [f for f in meta["failures"] if f.get("hostname")]
    if failed:
        shown = ", ".join(f"{f['hostname']}({f['reason'][:60]})" for f in failed[:3])
        parts.append(f"조회하지 못한 대상 {len(failed)}건: {shown}")
    return " ".join(parts)


APM_QUERY_SPEC = SubAgentSpec(
    APM_QUERY_AGENT,
    "WAS·미들웨어(APM 게이트웨이) 조회 — 인스턴스·응답시간·TPS·에러율·JVM 힙·GC·실행 중 서비스·"
    "느린 트랜잭션·WAS 이벤트",
    audited_investigation(run_apm_query, BACKEND_APM),
    purpose=(
        "WAS·미들웨어(APM) 조회 — WAS 인스턴스·응답시간·TPS·에러율·JVM 힙·GC·커넥션 풀·"
        "실행 중 서비스·느린 트랜잭션·**WAS 이벤트**(폴스타 서버 알람이 아님)"
    ),
    backend="mcp",
    input_slots=("entity_set", "time_window"),
    required_inputs=(),  # 대상이 없으면 인스턴스 목록 보기를 코드가 먼저 부른다(첫 홉)
    output_type="rows",
    key_facets_out=("hostname", "apm_instance_id", "apm_domain_id"),
    self_filters=("host", "time"),
    # 대상 상한·동시 호출은 설정값(`composite.max_targets`·`fanout_concurrency`) — 계약 값은 M-2
    # 픽스처 지연·J0-O 운영 실측 뒤(plans/125 §4.6)
    prerequisites=("source_endpoint",),
)


def active_extra_subagents(app_config: Any) -> dict[str, SubAgentSpec]:
    """활성인 조건부 처리기(엔드포인트가 설정된 비SQL 시스템) — 비활성이면 빈 dict."""
    return {APM_QUERY_AGENT: APM_QUERY_SPEC} if apm_active(app_config) else {}


__all__ = [
    "APM_QUERY_AGENT",
    "APM_QUERY_SPEC",
    "active_extra_subagents",
    "apm_active",
    "plan_window",
    "run_apm_query",
    "sanitize_views",
]
