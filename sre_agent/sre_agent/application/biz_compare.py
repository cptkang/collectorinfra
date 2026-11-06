"""서비스 심층 조사(D3)의 업무 서비스 구성원 비교 사전수집 (plans/215 §4.8·§4.9 · D-368).

진단 잡은 플레이북을 타지 않는다(D-341 — `_playbook_enabled_for`는 알람 잡만). 그래서 payload
`meta.biz_service`가 있는 진단 잡(D3)에서만, HolmesGPT 조사 전에 코드가 같은 업무 서비스 구성원을
결정적으로 비교 수집한다. 호출 조립·판독은 플레이북 스텝의 함수를 그대로 쓴다(새 판정 규칙 없음).

- 폴스타: `sms_members` 앞에서부터(생산자가 D1 이상 구성원을 앞에 둔다) 대표 서버를 뺀 ≤3대 —
  S4와 같은 지표 4종 호출(`build_calls`)과 판독(`_trend_result`). 상한은 D2와 같다
  (`BIZ_COMPARE_SMS_MAX` · `POLESTAR_LINK_TARGETS_MAX` 공유).
- APM: `apm_members` ≤8(S0 `max_compare` 기본) — 사건창 `apm_app_health`(`_incident_health_call`)와
  구성원 자신의 전일 동시간 기준선 1건(`_baseline_calls`) · 판정 `target_verdict`(게이트웨이 신호 →
  이상 · 사건창 p95 ≥ 2배 기준선 → 이상 · 근거 없음 → 판정 불가). D3에는 알람 인스턴스가 없어 S0처럼
  알람 인스턴스 기준선을 빌릴 수 없으므로 구성원마다 자기 기준선과 비교한다.

호출은 주입된 배치 콜러블로만 한다(D-118 MCP 계약 · 축 전역 상한·조사별 owner는 콜러블 안 — D-347·
D-359). 두 축은 따로 돌고 따로 실패한다 — 한 축 결손이 다른 축을 막지 않고, 사유는 행 `reason`·
`error`와 결과 `notes`에 남는다(침묵 폴백 없음). LLM 0 · DB 접근 0.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from sre_agent.application.evidence_prefetch import TOOL_METRIC, EvidenceScope, build_calls
from sre_agent.application.investigation_guidance import (
    biz_apm_state,
    biz_sms_state,
    sanitize_inline,
)
from sre_agent.application.playbook_runner import (
    BIZ_COMPARE_SMS_MAX,
    POLESTAR_LINK_TARGETS_MAX,
    biz_service_context,
    compute_windows,
)
from sre_agent.application.playbook_steps import (
    DEFAULT_P95_RATIO,
    RunScope,
    StepContext,
    _baseline_calls,
    _baseline_p95,
    _confirm_required,
    _incident_health_call,
    _note_confirm,
    _signals,
    _trend_result,
    _window_stats,
    target_verdict,
)
from sre_agent.domain.incident_scope import DEFAULT_LOOKBACK_MINUTES
from sre_agent.domain.playbook import WindowSpec

logger = logging.getLogger(__name__)

BatchCaller = Callable[[Sequence[tuple[str, dict]]], list[str]]

#: APM 구성원 비교 상한 — S0 `max_compare` 기본값과 같다.
BIZ_COMPARE_APM_MAX = 8
#: 축 스레드가 끝나야 하는 시점의 비율(바깥 타임박스보다 먼저 끊어 끝난 축 결과를 살린다 —
#: `mcp_service.NEIGHBOR_BUDGET_SHARE`와 같은 값).
BIZ_COMPARE_AXIS_SHARE = 0.8

#: 생략·결손 사유(사용자 문장).
BIZ_COMPARE_NO_REFERENCE = (
    "기준 시각이 없어 구성원 비교를 하지 않음(서비스 진단에 최초 이상 시각 없음)"
)
BIZ_COMPARE_BUDGET_EXHAUSTED = "사전수집 시간을 다 써서 구성원 비교를 하지 않음"
BIZ_COMPARE_TIMEOUT = "시간 상한 {s:g}s 안에 끝나지 않음"
BIZ_COMPARE_APM_TRUNCATED = "APM 구성원 비교 상한 {cap} — {n}개 인스턴스 생략"
BIZ_COMPARE_SMS_TRUNCATED = "서버 구성원 비교 상한 {cap} — {n}대 생략"
APM_CONFIRM_UNJUDGED = "X-View 사전 확인 상한 초과 — 판정 불가"
APM_BASELINE_MISSING = "전일 동시간 기준선 응답 시간 없음 — 판정 불가"
#: 브리핑 한계 줄 머리.
BIZ_COMPARE_LIMIT_HEAD = "업무 서비스 구성원 비교"

def _members(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """payload `meta.biz_service` 정규화 값(D2와 같은 정화 — `biz_service_context`)."""
    return biz_service_context(payload) if isinstance(payload, Mapping) else None


def compare_targets(
    payload: Mapping[str, Any] | None,
) -> tuple[list[dict[str, str]], list[tuple[str, str | None]], list[str]]:
    """비교 대상 `(서버 구성원, APM 구성원 (인스턴스, 소스), 상한 노트)`.

    서버 구성원은 대표 서버(payload `db_id` + `server_name`/`hostname`)를 빼고 앞에서부터 상한까지,
    APM 구성원은 앞에서부터 `BIZ_COMPARE_APM_MAX`까지(같은 (인스턴스, 소스)는 한 번).
    """
    biz = _members(payload)
    if biz is None:
        return [], [], []
    rep_db = str((payload or {}).get("db_id") or "").strip()
    rep_names = {
        str((payload or {}).get(k) or "").strip() for k in ("server_name", "hostname")
    } - {""}
    sms = [
        t for t in biz["sms_members"]
        if not (t["db_id"] == rep_db and ({t["server_name"], t.get("hostname", "")} & rep_names))
    ]
    cap_sms = min(BIZ_COMPARE_SMS_MAX, POLESTAR_LINK_TARGETS_MAX)
    apm: list[tuple[str, str | None]] = []
    for m in biz["apm_members"]:
        key = (m["instance_name"], m.get("source_id") or None)
        if key not in apm:
            apm.append(key)
    notes: list[str] = []
    if len(sms) > cap_sms:
        notes.append(BIZ_COMPARE_SMS_TRUNCATED.format(cap=cap_sms, n=len(sms) - cap_sms))
    if len(apm) > BIZ_COMPARE_APM_MAX:
        notes.append(BIZ_COMPARE_APM_TRUNCATED.format(
            cap=BIZ_COMPARE_APM_MAX, n=len(apm) - BIZ_COMPARE_APM_MAX))
    targets = [
        {k: t[k] for k in ("db_id", "server_name", "hostname") if t.get(k)} for t in sms[:cap_sms]
    ]
    return targets, apm[:BIZ_COMPARE_APM_MAX], notes


def _sms_missing(targets: Sequence[Mapping[str, str]], reason: str) -> list[dict[str, Any]]:
    return [
        {"db_id": t["db_id"], "server_name": t["server_name"], "status": "missing",
         "verdict": None, "anomalous": [], "missing": [], "reason": reason}
        for t in targets
    ]


def _apm_missing(entries: Sequence[tuple[str, str | None]], reason: str) -> list[dict[str, Any]]:
    return [
        {"instance_name": n, "source_id": s, "verdict": None, "verdict_source": None,
         "p95_ms": None, "baseline_p95_ms": None, "error": reason}
        for n, s in entries
    ]


def skipped_compare(payload: Mapping[str, Any] | None, reason: str) -> dict[str, Any]:
    """비교를 하지 못한 결과 — 대상 구성원 전부를 결손(`reason`)으로 싣는다(호출 0)."""
    targets, apm, notes = compare_targets(payload)
    return {
        "compared_sms": _sms_missing(targets, reason),
        "compared_apm": _apm_missing(apm, reason),
        "notes": [reason, *notes],
        "tool_calls": 0,
    }


def _context(
    payload: Mapping[str, Any], reference_time: str, lookback: int,
    caller: BatchCaller | None, axis: str, xview_confirm_over: int | None,
) -> StepContext:
    """축 1개의 스텝 문맥 — 사건창 = 기준 시각 −lookback ~ 기준 시각 · 기준선 = 전일 동시간."""
    windows = compute_windows(
        reference_time, WindowSpec(before_min=lookback, after_min=0, baseline=["prev_day"]))
    return StepContext(
        payload=payload, windows=windows, scope=RunScope(), args={}, targets=[],
        polestar=caller if axis == "polestar" else None,
        apm=caller if axis == "apm" else None,
        now=datetime.now,
        prior={}, xview_confirm_over=xview_confirm_over,
    )


def _sms_axis(ctx: StepContext, targets: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    """서버 구성원 ≤3대의 S4 지표 판독(배치 1회 · 결손은 대상·지표별)."""
    plans = []
    for t in targets:
        sc = EvidenceScope(
            source=t["db_id"], server_name=t["server_name"],
            reference_time=ctx.windows.reference_time,
            lookback_minutes=ctx.windows.lookback_minutes, granularity="h",
        )
        plans.append((sc, [c for c in build_calls(sc) if c[0] == TOOL_METRIC]))
    resps = ctx.call("polestar", [c for _, cs in plans for c in cs])
    rows: list[dict[str, Any]] = []
    at = 0
    for sc, cs in plans:
        r = _trend_result(ctx, sc, cs, resps[at:at + len(cs)], [])
        at += len(cs)
        rows.append({
            "db_id": sc.source, "server_name": sc.server_name, "status": r.status,
            "verdict": r.verdict, "anomalous": list(r.data.get("anomalous") or []),
            "missing": list(r.missing), "reason": r.reason,
        })
    return rows


def _apm_axis(ctx: StepContext, entries: Sequence[tuple[str, str | None]]) -> list[dict[str, Any]]:
    """APM 구성원 ≤8의 사건창 app_health + 자기 기준선 판정(배치 1회 · 결손은 구성원별)."""
    health = [_incident_health_call(ctx, n, s) for n, s in entries]
    nb = len(ctx.windows.baselines)
    base = [c for n, s in entries for _, c in _baseline_calls(ctx, n, s)]
    resps = ctx.call("apm", health + base)
    cur, bases = resps[:len(entries)], resps[len(entries):]
    lb = ctx.windows.lookback_minutes
    rows: list[dict[str, Any]] = []
    for i, ((name, src), resp) in enumerate(zip(entries, cur)):
        own = bases[i * nb:(i + 1) * nb]
        for br in own:
            if _confirm_required(br):
                _note_confirm(ctx, br)
        base_stats = [
            _window_stats(br.data, lb) for br in own if br.ok and not _confirm_required(br)
        ]
        base_p95 = _baseline_p95(base_stats)
        stats = _window_stats(resp.data, lb) if resp.ok else None
        error = resp.error
        verdict: str | None = None
        source: str | None = None
        if resp.ok and _confirm_required(resp):
            _note_confirm(ctx, resp)
            error = APM_CONFIRM_UNJUDGED
        elif resp.ok:
            verdict, source = target_verdict(
                _signals(resp.data), (stats or {}).get("p95_ms"), base_p95, DEFAULT_P95_RATIO)
            if verdict is None:
                base_err = next((br.error for br in own if br.error), None)
                error = APM_BASELINE_MISSING + (f"({base_err})" if base_err else "")
        rows.append({
            "instance_name": name, "source_id": src, "verdict": verdict,
            "verdict_source": source, "p95_ms": (stats or {}).get("p95_ms"),
            "baseline_p95_ms": base_p95, "error": error,
        })
    return rows


def _run_axis(
    fn: Callable[[], list[dict[str, Any]]], name: str,
) -> tuple[threading.Thread, dict[str, Any]]:
    """축 1개를 데몬 스레드로 시작한다 — 결과·예외는 상자에 담는다(조사 컨텍스트는 스레드 상속)."""
    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["rows"] = fn()
        except BaseException as exc:  # noqa: BLE001 — 축 격리(다른 축은 계속 · 사유는 행에)
            box["error"] = exc

    worker = threading.Thread(target=_target, name=name, daemon=True)
    worker.start()
    return worker, box


def collect_biz_compare(
    payload: Mapping[str, Any] | None,
    reference_time: str | None,
    lookback_minutes: int | None,
    *,
    polestar: BatchCaller | None,
    apm: BatchCaller | None,
    budget_s: float,
    xview_confirm_over: int | None = None,
) -> dict[str, Any]:
    """업무 서비스 구성원 비교를 수집한다(D3 전용 · 결정적).

    Args:
        payload: 진단 잡 payload(`meta.biz_service` · 대표 서버 `db_id`·`server_name`·`hostname`)
        reference_time: 사건 기준 시각(ISO) — 없으면 호출 없이 전 구성원 결손
            (`BIZ_COMPARE_NO_REFERENCE`)
        lookback_minutes: 사건창 길이(분 · 없으면 기본값)
        polestar: 폴스타 배치 콜러블(None이면 서버 구성원은 「폴스타 MCP 미구성」 결손)
        apm: APM 게이트웨이 배치 콜러블(None이면 APM 구성원은 「APM 게이트웨이 미구성」 결손)
        budget_s: 두 축이 끝나야 하는 시간(초) — 넘긴 축은 구성원 전부 결손(시간 상한)
        xview_confirm_over: 기간 모드 app_health의 X-View 사전 확인 상한(None = 미전송)

    Returns:
        `{compared_sms:[{db_id, server_name, status, verdict, anomalous, missing, reason}],
        compared_apm:[{instance_name, source_id, verdict, verdict_source, p95_ms, baseline_p95_ms,
        error}], notes:[…], tool_calls}`
    """
    if not reference_time:
        return skipped_compare(payload, BIZ_COMPARE_NO_REFERENCE)
    targets, entries, notes = compare_targets(payload)
    lookback = int(lookback_minutes or DEFAULT_LOOKBACK_MINUTES)
    data = dict(payload or {})
    ctxs = {
        "polestar": _context(data, reference_time, lookback, polestar, "polestar", None),
        "apm": _context(data, reference_time, lookback, apm, "apm", xview_confirm_over),
    }
    deadline = time.monotonic() + max(0.0, float(budget_s))
    axes: dict[str, tuple[threading.Thread, dict[str, Any]]] = {}
    if targets:
        axes["polestar"] = _run_axis(lambda: _sms_axis(ctxs["polestar"], targets), "sre-biz-sms")
    if entries:
        axes["apm"] = _run_axis(lambda: _apm_axis(ctxs["apm"], entries), "sre-biz-apm")
    results: dict[str, list[dict[str, Any]]] = {}
    for axis, (worker, box) in axes.items():
        worker.join(max(0.0, deadline - time.monotonic()))
        if worker.is_alive():
            reason = BIZ_COMPARE_TIMEOUT.format(s=float(budget_s))
        elif "error" in box:
            exc = box["error"]
            reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            logger.warning("업무 서비스 구성원 비교 %s 축 실패: %s", axis, reason)
        else:
            results[axis] = box["rows"]
            continue
        results[axis] = (_sms_missing(targets, reason) if axis == "polestar"
                         else _apm_missing(entries, reason))
    for ctx in ctxs.values():
        notes.extend(x for x in ctx.limits if x not in notes)
    return {
        "compared_sms": results.get("polestar", []),
        "compared_apm": results.get("apm", []),
        "notes": notes,
        "tool_calls": sum(len(c.calls) for c in ctxs.values()),
    }


def biz_compare_brief(
    payload: Mapping[str, Any] | None, compare: Mapping[str, Any],
) -> dict[str, Any] | None:
    """브리핑 선택 키 `biz_service` — 알람 경로(`playbook_runner.biz_service_brief`)와 같은 모양.

    `{service_id, display_name, target_inferred, compared_sms:[{db_id, server_name, status, verdict,
    anomalous, inferred}], compared_apm:[{instance_name, source_id, verdict, inferred}], truncated,
    d1_verdict?}`. 맥락이 없으면 None(키 없음).
    """
    biz = _members(payload)
    if biz is None:
        return None
    sms_conf = {(t["db_id"], t["server_name"]): t.get("confidence") for t in biz["sms_members"]}
    apm_conf = {
        (t["instance_name"], t.get("source_id") or None): t.get("confidence")
        for t in biz["apm_members"]
    }
    out: dict[str, Any] = {
        "service_id": biz["service_id"],
        "display_name": biz["display_name"],
        "target_inferred": biz.get("confidence") == "inferred",
        "compared_sms": [
            {
                "db_id": r["db_id"], "server_name": r["server_name"], "status": r.get("status"),
                "verdict": r.get("verdict"), "anomalous": list(r.get("anomalous") or []),
                "inferred": sms_conf.get((r["db_id"], r["server_name"])) == "inferred",
            }
            for r in compare.get("compared_sms") or []
        ],
        "compared_apm": [
            {
                "instance_name": r["instance_name"], "source_id": r.get("source_id"),
                "verdict": r.get("verdict"),
                "inferred": apm_conf.get((r["instance_name"], r.get("source_id"))) == "inferred",
            }
            for r in compare.get("compared_apm") or []
        ],
        "truncated": bool(biz.get("truncated")),
    }
    if biz.get("d1_verdict"):
        out["d1_verdict"] = biz["d1_verdict"]
    return out


def biz_compare_limitations(compare: Mapping[str, Any] | None) -> list[str]:
    """브리핑 한계 줄 — 생략·상한 노트와 결손 구성원(사유 포함)만. 결손이 없으면 빈 목록."""
    if not isinstance(compare, Mapping):
        return []
    out = [f"{BIZ_COMPARE_LIMIT_HEAD}: {sanitize_inline(n)}" for n in compare.get("notes") or []]
    lost = [
        f"서버 {sanitize_inline(r.get('server_name'))} {biz_sms_state(r)}"
        for r in compare.get("compared_sms") or [] if r.get("status") != "ok"
    ] + [
        f"APM {sanitize_inline(r.get('instance_name'))} {biz_apm_state(r)}"
        for r in compare.get("compared_apm") or [] if not r.get("verdict")
    ]
    if lost:
        out.append(f"{BIZ_COMPARE_LIMIT_HEAD} 결손 — " + " · ".join(lost))
    return list(dict.fromkeys(out))


__all__ = [
    "BIZ_COMPARE_APM_MAX", "BIZ_COMPARE_AXIS_SHARE", "BIZ_COMPARE_NO_REFERENCE",
    "BIZ_COMPARE_BUDGET_EXHAUSTED", "BIZ_COMPARE_TIMEOUT", "BIZ_COMPARE_LIMIT_HEAD",
    "compare_targets", "skipped_compare", "collect_biz_compare", "biz_compare_brief",
    "biz_compare_limitations",
]
