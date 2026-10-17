"""게이트웨이 호출 실행 · 다건 대상 배치 · 거부 결과 조립.

plans/180 W1 — `src/orchestration/apm_query.py`에서 순수 이동(본문·주석·평가 순서 불변).
옛 경로 `src.orchestration.apm_query`가 같은 이름으로 재노출한다(D-337 ① shim).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from src.clients.source_mcp_client import SourceMcpError
from src.domain import disclosure as disc
from src.orchestration import apm_jobs as jobs
from src.orchestration.apm.common import APM_QUERY_AGENT, APM_SYSTEM, META_KEY
from src.orchestration.apm.window import WindowPlan
from src.routing.registry import ViewSpec
from src.utils.task_result_keys import DEGRADED_KEY

logger = logging.getLogger("src.orchestration.apm_query")


# ── 실행 ──────────────────────────────────────────────────────────────────────

@dataclass
class _Call:
    view: ViewSpec
    hostname: str | None
    args: dict[str, Any]
    envelope: dict[str, Any] | None = None
    error: str | None = None
    #: 게이트웨이 작업 핸들(최신 상태) — 승격됐거나 결과 파일이 있을 때(134 W0-B)
    job: dict[str, Any] | None = None
    #: 조회 마감까지 끝나지 않아 작업으로 접수됐다(데이터 아님)
    accepted: bool = False
    #: 장부 저장소 종류(`redis`·`memory`)
    ledger: str | None = None
    #: 앞 결과 행 참조로 부른 호출의 참조 경과(종류 · 출처 · 번호 — plans/134 M-6 · 감사 입력)
    reference: dict[str, Any] | None = None
    #: 대상 텍스트를 해석한 인스턴스로 부른 호출의 대상(이름 · 소스 · 도메인 · id — plans/130 M-2)
    instance: dict[str, Any] | None = None
    #: 다건 대상 배치(plans/134 M-5) — 이 호출이 `targets` 한 번으로 대신 부른 대상별 호출(선언 순서
    #: = `targets` 순번)
    members: list[_Call] | None = None
    #: 배치 봉투에서 복원한 대상별 가상 호출이면 그 배치 호출(작업·결과 파일 고지는 배치 단위)
    batch_of: _Call | None = None
    #: 이름 보기(`target: named`)에 실은 대상 이름 목록(서비스·업무 — 해석은 게이트웨이)
    named: list[str] | None = None
    #: hostname 대상의 소스(첫 홉 부하 순위 행의 소스 — 그 호스트의 다른 소스 인스턴스를 부르지
    #: 않게 · plans/134 W6 V6-3)
    host_source: str | None = None


@dataclass(frozen=True)
class _JobScope:
    """이번 처리기 호출의 작업 맥락 — 소유자 · 스레드 · 호출 상한 · 재확인 마감 · 고른 소스."""

    sub: Any
    owner: str
    thread_id: Any
    call_timeout: float
    poll_until: float
    app_config: Any
    #: 분해 `sources`를 검증한 소스 id — 비면 소스를 좁히지 않는다(plans/134 M-5 · 종전 인자 모양)
    source_ids: tuple[str, ...] = ()


def _envelope_error(envelope: dict[str, Any]) -> str | None:
    if envelope.get("error"):
        return f"{envelope.get('error')}: {str(envelope.get('reason') or '')[:160]}"
    return None


#: 호출 오류의 게이트웨이 오류 코드(`_envelope_error`·작업 상태 —
#: 「instance_unresolved: …」의 머리).
#: 「코드: 사유」 꼴만 코드로 본다 — 「postgresql://…」 같은 원문의 스킴을 코드로 읽지 않는다.
_GATEWAY_CODE_RE = re.compile(r"([a-z][a-z0-9_]*): ")
#: 예외 종류 이름 머리(「RuntimeError: …」 — `_lookup` 의 호출 예외) — 종류 이름만 보인다.
_EXC_TYPE_RE = re.compile(r"([A-Z][A-Za-z0-9]*(?:Error|Exception)): ")
#: 첫 홉 목록 작업이 마감을 넘긴 사유 — 코드가 지은 고정 문장이라 그대로 보인다.
_FIRST_HOP_TIMEOUT_ERROR = ("인스턴스 목록 조회가 처리 시간 안에 끝나지 않아 대상 서버를 고르지 "
                            "못했습니다(목록 작업은 취소했습니다)")
#: 소스 MCP 세션(`source_mcp_client`)이 원문 없이 지은 사유 — 그대로 보인다(시간 초과·세션 중단·
#: 연결 실패의 예외 종류 이름).
_CODE_PHRASE_RE = re.compile(
    r"[^\n:]{1,60} (?:도구 호출 시간 초과\([a-z][a-z0-9_]*, \d+(?:\.\d+)?초\)"
    r"|세션이 중단됐습니다|연결·호출 실패: [A-Za-z][A-Za-z0-9_]*)"
)


async def _run_calls(session: Any, calls: list[_Call], concurrency: int,
                     scope: _JobScope | None = None) -> None:
    sem = asyncio.Semaphore(concurrency)

    async def one(call: _Call) -> None:
        async with sem:
            if scope is not None:
                # 호출 직전에 잰다 — 앞 호출이 쓴 시간만큼 조회 마감이 가까워졌다
                call.args["wait_seconds"] = jobs.wait_seconds(scope.call_timeout)
            try:
                call.envelope = await session.call_tool(call.view.tool, call.args)
            except SourceMcpError as e:
                call.error = str(e)
                return
            call.error = _envelope_error(call.envelope)

    await asyncio.gather(*(one(c) for c in calls))


def _call_args(view: ViewSpec, hostname: str | None, window: WindowPlan,
               scope: _JobScope, view_args: dict[str, Any] | None = None) -> dict[str, Any]:
    """도구 인자 = 대상 + 창 + 고정 인자 + 검증된 선택 조건 + 고른 소스 + owner(SPEC §7.3 —
    wait_seconds는 호출 직전에 더한다). 소스를 고르지 않았으면 종전 모양 그대로다."""
    args: dict[str, Any] = {
        "hostname": hostname, "thread_id": str(scope.thread_id) if scope.thread_id else None,
    }
    args.update(window.args)
    args.update(view.fixed_args)
    args.update(view_args or {})
    if scope.source_ids:
        args["source_ids"] = list(scope.source_ids)
    args["owner"] = scope.owner
    return args


# ── 다건 대상 배치 (plans/134 M-5 · 계약 A-2) ─────────────────────────────────────

#: 대상별 호출에서 대상을 나타내는 인자 — 배치에서는 `targets` 항목으로 옮긴다.
_TARGET_ARGS = frozenset({"hostname", "instance_name", "instance_id", "source_ids"})
#: 대상별 가상 봉투·출처의 표지 — 그 대상의 행 중 결과 파일에만 있는(미리보기에 없는) 행 수(R34-1).
ROWS_IN_FILE_KEY = "rows_in_file"
#: 배치 봉투 `batch[i]`에서 하위 봉투로 옮기지 않는 칸(항목 메타) · 하위 봉투가 배치 봉투에서
#: 물려받는 칸.
_BATCH_ITEM_META = frozenset({"index", "target", "status"})
_BATCH_SHARED = ("tool", "queried_at", "source", "source_kind")


def _target_item(call: _Call) -> dict[str, Any] | None:
    """대상별 호출 1개 → 배치 `targets` 항목(hostname 또는 인스턴스 이름 + 소스 + id) — 대상 없는
    호출은 None."""
    if call.instance is not None:
        item: dict[str, Any] = {"instance_name": call.args.get("instance_name")}
        sources = call.args.get("source_ids") or []
        if sources:
            item["source_id"] = sources[0]
        if call.args.get("instance_id") is not None:
            item["instance_id"] = call.args["instance_id"]
        return item
    if call.hostname:
        if call.host_source:
            return {"hostname": call.hostname, "source_id": call.host_source}
        return {"hostname": call.hostname}
    return None


def _host_calls(view: ViewSpec, hostnames: list[str], plan: WindowPlan, scope: _JobScope,
                view_args: dict[str, Any] | None,
                host_sources: dict[str, list[str]]) -> list[_Call]:
    """hostname 대상별 호출 — 첫 홉 부하 순위가 그 호스트의 소스를 정했으면 (호스트 · 소스)마다
    1호출(`source_ids` = 그 소스 · 배치 항목 `source_id`)이다. 소스를 모르면 종전 그대로(V6-3)."""
    calls: list[_Call] = []
    for host in hostnames:
        sources: list[str | None] = [*host_sources.get(host, [])] or [None]
        for sid in sources:
            args = _call_args(view, host, plan, scope, view_args)
            if sid is not None:
                args["source_ids"] = [sid]
            calls.append(_Call(view, host, args, host_source=sid))
    return calls


def _batched(calls: list[_Call], scope: _JobScope) -> list[_Call]:
    """한 보기의 대상이 2개 이상이면 `targets` 배치 1호출로 묶는다(계약 A-2 · D-296 ④ 상한 없음).

    같은 보기·같은 공통 인자(창·조건·owner — 대상 인자 뺀 나머지)의 대상별 호출이 한 묶음이다. 대상
    1개는 종전 호출 그대로(바이트 동일)이고, 대상 없는 호출·이미 답이 있는 합성 호출은 묶지 않는다.
    고른 소스(`scope.source_ids`)는 배치 최상위 `source_ids`로 — 소스가 정해진 인스턴스 항목은
    항목의 `source_id`가 이긴다(계약 A-2).
    """
    groups: dict[tuple[str, str], list[_Call]] = {}
    order: list[_Call | tuple[str, str]] = []
    for call in calls:
        if call.envelope is not None or _target_item(call) is None:
            order.append(call)
            continue
        common = {k: v for k, v in call.args.items() if k not in _TARGET_ARGS}
        key = (call.view.id, json.dumps(common, sort_keys=True, default=str))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(call)
    wire: list[_Call] = []
    for entry in order:
        if isinstance(entry, _Call):
            wire.append(entry)
            continue
        members = groups[entry]
        if len(members) == 1:
            wire.append(members[0])
            continue
        head = members[0]
        args = {k: v for k, v in head.args.items() if k not in _TARGET_ARGS}
        args["targets"] = [_target_item(m) for m in members]
        if scope.source_ids:
            args["source_ids"] = list(scope.source_ids)
        wire.append(_Call(head.view, None, args, members=members))
    return wire


def _restore_members(call: _Call) -> None:
    """배치 봉투 → 대상별 가상 호출(계약 A-2) — `batch[i]`·`target_index == i` 행이 i번째 대상.

    하위 봉투는 단건 호출과 같은 칸이다(집계·판정·정합·부분 결과). 항목 결과가 없으면(배치 자체가
    실패했거나 계약과 다른 봉투) 그 대상은 실패다 — 행 0건으로 읽지 않는다.
    """
    env = call.envelope if isinstance(call.envelope, dict) else {}
    entries = {e["index"]: e for e in env.get("batch") or []
               if isinstance(e, dict) and isinstance(e.get("index"), int)
               and not isinstance(e.get("index"), bool)}
    rows: dict[int, list[dict[str, Any]]] = {}
    stray = 0
    for row in env.get("rows") or []:
        index = row.get("target_index") if isinstance(row, dict) else None
        if isinstance(index, int) and not isinstance(index, bool) and isinstance(row, dict):
            rows.setdefault(index, []).append({k: v for k, v in row.items()
                                               if k != "target_index"})
        else:
            stray += 1
    if stray:
        logger.warning("%s 배치 봉투에 대상 순번 없는 행 %d건 — 어느 대상인지 몰라 싣지 않음(%s)",
                       APM_QUERY_AGENT, stray, call.view.tool)
    shared = {k: env[k] for k in _BATCH_SHARED if k in env}
    missing = call.error or "batch_missing: 배치 결과에 이 대상의 항목이 없습니다"
    for i, member in enumerate(call.members or []):
        member.batch_of = call
        entry = entries.get(i)
        if entry is None:
            member.error = missing
            continue
        if entry.get("status") == "error" or entry.get("error"):
            member.error = _envelope_error({**entry, "error": entry.get("error") or "api_error"})
            continue
        sub = {k: v for k, v in entry.items() if k not in _BATCH_ITEM_META}
        member.envelope = {**shared, **sub, "rows": rows.get(i, [])}
        member.envelope.setdefault("row_count", len(rows.get(i, [])))
        count = member.envelope.get("row_count")
        if isinstance(count, int) and not isinstance(count, bool) and count > len(rows.get(i, [])):
            # 배치 결과가 결과 파일로 가 미리보기만 왔다 — 이 대상의 나머지 행은 결과 파일에만
            # 있다(행 0 · 건수 양수 모순을 표지로 · 0건으로 세지 않는다 — R34-1)
            member.envelope[ROWS_IN_FILE_KEY] = count - len(rows.get(i, []))


def _unbatched(wire: list[_Call], meta: dict[str, Any]) -> list[_Call]:
    """배치 호출을 대상별 가상 호출로 푼다 — 접수(작업 승격)된 배치는 작업 1건 그대로 둔다.

    배치 경과는 `meta["batches"]`(보기 · 도구 · 대상 수 · 실패 대상 수 · 작업)에 남긴다(배치가
    없으면 메타 모양이 종전과 같다).
    """
    out: list[_Call] = []
    batches: list[dict[str, Any]] = []
    for call in wire:
        if not call.members:
            out.append(call)
            continue
        info: dict[str, Any] = {"view": call.view.id, "tool": call.view.tool,
                                "targets": len(call.members)}
        if call.job:
            info["job_id"] = call.job.get("job_id")
        if call.accepted:  # 끝나면 작업 카드가 대상별 결과를 준다 — 대상별로 풀지 않는다
            info["accepted"] = True
            out.append(call)
        else:
            _restore_members(call)
            info["failed"] = sum(1 for m in call.members if m.error)
            out += call.members
        batches.append(info)
    if batches:
        meta["batches"] = batches
    return out


def _batch_parents(calls: list[_Call]) -> list[_Call]:
    """대상별 가상 호출의 배치 호출(처음 나온 순서 · 한 번씩)."""
    seen: dict[int, _Call] = {}
    for call in calls:
        if call.batch_of is not None:
            seen.setdefault(id(call.batch_of), call.batch_of)
    return list(seen.values())


def _refusal(message: str, reason: str, meta: dict[str, Any],
             disclosures: list[disc.Disclosure] | None = None) -> dict[str, Any]:
    """조회하지 못함 — 텍스트 결과(host_inspect 와 같은 규약 · plans/121 TP-1.5)."""
    out: dict[str, Any] = {"error": message, DEGRADED_KEY: reason, "final_response": message,
                           META_KEY: meta, "source_status": [meta["source_status"]]}
    if disclosures:
        out["disclosures"] = disclosures
    return out


def _blocked_refusal(label: str, blocked: dict[str, str], meta: dict[str, Any],
                     disclosures: list[disc.Disclosure]) -> dict[str, Any]:
    """필수 조건·앞 결과 참조를 되물었다 — 질문이 곧 답이다(다른 조회로 대신하지 않는다 · G-10)."""
    failed = "; ".join(f["reason"] for f in meta["failures"][:3])
    text = "\n".join([*blocked.values(),
                      *([f"{label}을(를) 조회하지 않았습니다 — {failed}"] if failed else [])])
    meta["source_status"] = _status(label, "not_queried", 0, "필수 조건·앞 결과 참조 미해결")
    return _refusal(text, "apm_unresolved_condition", meta, disclosures)


def _status(label: str, status: str, rows: int, reason: str = "") -> dict[str, Any]:
    return {"system": APM_SYSTEM, "label": label, "status": status, "rows": rows,
            "reason": reason}
