"""프로세스 조회 전용 노드 (Plan 48 §3.4·§5.3, application 계층).

특정 자원의 실시간 프로세스 목록/현황을 폴스타 프로세스 API로 조회하여
결정적 선별·집계·마스킹 후 LLM이 현황만 해석한다. 프로세스는 DB(EAV/메트릭)에
존재하지 않고 실시간 API에만 있으므로 SQL 파이프라인을 우회한다.

해석 순서 (Plan 48 §3.4):
    (0) 게이팅: process_query_enabled + base_url 매핑 존재
    (1) target 확정 (state["process_query_target"] 없으면 user_query에서 보강)
    (2) host 해석:
        ① resolver.resolve(active_db_id, identifier)
           - hostname 확정 → 사용
           - candidates 2건↑(모호) → 후보 안내·HITL 되물음으로 종료 (임의 선택 금지)
           - 빈(미해석) → ②
        ② identifier를 직접 hostname으로 보고 API 시도 → 0건이면 ③
        ③ 타 폴스타 db 재해석 (단 user_specified_db 세팅 시 ③ 스킵)
        끝내 미해석 → "hostname 특정 불가" 안내 종료 (추측 금지)
    (3) process_client.list_by_hostname (47-1 재사용, None/실패 graceful)
    (4) build_process_overview (결정적 선별·집계·마스킹)
    (5) LLM 현황 분석 (마스킹된 결정적 요약 텍스트만 주입)
    (6) State 반환 (output_format=xlsx/docx면 organized_data rows 구성)

제약: 모든 출력 경로(LLM·UI·엑셀)에는 **마스킹된 args만** 사용한다.
각 외부 호출(DB 해석, API)은 자체 try/except + 타임아웃으로 graceful 처리한다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from src.config import AppConfig, load_config
from src.domain.process import (
    ProcessInfo,
    ProcessOverview,
    build_process_overview,
    resolve_metric_from_text,
    select_top_processes,
)
from src.prompts.process_query import PROCESS_QUERY_SYSTEM_PROMPT
from src.routing.domain_config import get_all_db_ids, get_domain_by_id
from src.state import AgentState

logger = logging.getLogger(__name__)

# CSV/전체 목록 행 상한 — 프로젝트 제약(max rows 10,000) 준수.
# 실제 서버 프로세스 수는 수백 수준이라 사실상 전체가 포함된다.
_MAX_CSV_ROWS = 10000


def _extract_target(state: AgentState, app_config: AppConfig) -> dict[str, Any]:
    """process_query_target을 확정한다 (없으면 user_query에서 보강).

    1순위는 input_parser가 채운 state["process_query_target"]이며(Phase 3 배선),
    미존재 시 user_query에서 metric(resolve_metric_from_text)·top_n(config 기본)을 보강한다.
    identifier는 parsed_requirements/명시 target이 없으면 빈 문자열로 둔다(해석 단계에서 처리).
    """
    top_n_default = app_config.process_query.process_query_top_n
    target = state.get("process_query_target") or {}
    if not isinstance(target, dict):
        target = {}

    identifier = str(target.get("identifier") or "").strip()
    metric = target.get("metric")
    if metric not in ("cpu", "memory", "both"):
        metric = resolve_metric_from_text(state.get("user_query", ""))
    top_n = target.get("top_n")
    if not isinstance(top_n, int) or top_n <= 0:
        top_n = top_n_default

    return {"identifier": identifier, "metric": metric, "top_n": top_n}


async def _resolve_with_timeout(
    host_resolver, db_id: str, identifier: str, timeout_s: int
):  # noqa: ANN001
    """resolver.resolve를 타임아웃으로 감싼다 (실패 시 빈 결과 동등 처리).

    Returns:
        HostResolution 또는 None (타임아웃/예외 시 None — 호출부에서 미해석 처리).
    """
    try:
        return await asyncio.wait_for(
            host_resolver.resolve(db_id, identifier), timeout=max(1, timeout_s)
        )
    except asyncio.TimeoutError:
        logger.warning(
            "hostname 해석 타임아웃 — 폴백 진행: db_id=%s identifier=%r", db_id, identifier
        )
        return None
    except Exception as exc:  # noqa: BLE001 — graceful degradation
        logger.warning(
            "hostname 해석 예외 — 폴백 진행: db_id=%s identifier=%r err=%s",
            db_id,
            identifier,
            exc,
        )
        return None


def _proc_to_dict(proc: ProcessInfo) -> dict[str, Any]:
    """ProcessInfo(마스킹 완료)를 UI/엑셀/응답용 dict로 직렬화한다."""
    return {
        "name": proc.name,
        "pid": proc.pid,
        "ppid": proc.ppid,
        "user": proc.user,
        "p100cpu": proc.p100cpu,
        "pcpu": proc.pcpu,
        "pmem": proc.pmem,
        "rss": proc.rss,
        "args": proc.args,  # mask_args() 적용된 값만
    }


def _overview_to_dict(overview: ProcessOverview) -> dict[str, Any]:
    """ProcessOverview를 직렬화한다 (args는 이미 마스킹됨 — 평문 노출 없음)."""
    return {
        "source_host": overview.source_host,
        "captured_at": (
            overview.captured_at.isoformat() if overview.captured_at else None
        ),
        "total_count": overview.total_count,
        "metric": overview.metric,
        "top_by_cpu": [_proc_to_dict(p) for p in overview.top_by_cpu],
        "top_by_mem": [_proc_to_dict(p) for p in overview.top_by_mem],
        "cpu_top_share": round(overview.cpu_top_share, 2),
        "mem_top_share": round(overview.mem_top_share, 2),
        "distinct_users": overview.distinct_users,
        "top_user_by_count": (
            list(overview.top_user_by_count)
            if overview.top_user_by_count is not None
            else None
        ),
    }


def _build_summary_text(overview: ProcessOverview) -> str:
    """LLM에 주입할 결정적·마스킹 요약 텍스트를 만든다 (수치 재계산 방지).

    모든 args는 이미 mask_args()로 마스킹되어 있다 — 평문 비밀번호/토큰 비노출.
    """
    lines: list[str] = []
    lines.append(f"조회 hostname: {overview.source_host}")
    captured = (
        overview.captured_at.strftime("%Y-%m-%d %H:%M:%S")
        if overview.captured_at
        else "(시각 미상)"
    )
    lines.append(f"스냅샷 시각(현재 시점): {captured}")
    lines.append(f"전체 프로세스 수: {overview.total_count}")
    lines.append(f"정렬 기준(metric): {overview.metric}")
    lines.append(f"고유 실행 계정 수: {overview.distinct_users}")
    if overview.top_user_by_count is not None:
        user, count = overview.top_user_by_count
        lines.append(f"최다 프로세스 보유 계정: {user} ({count}개)")

    def _fmt(procs: list[ProcessInfo], metric_key: str) -> list[str]:
        out: list[str] = []
        for rank, p in enumerate(procs, start=1):
            val = p.p100cpu if metric_key == "cpu" else p.pmem
            unit = "CPU%(100기준)" if metric_key == "cpu" else "MEM%"
            # args는 mask_args() 적용분. 빈 값이면 "(없음)"으로 명시해 LLM이
            # "args 미전달"로 오해하지 않도록 한다 (§10.2).
            args = f" | args: {p.args}" if p.args else " | args: (없음)"
            out.append(
                f"  {rank}. {p.name} (pid={p.pid}, user={p.user}, "
                f"{unit}={val}){args}"
            )
        return out

    total = overview.total_count
    if overview.top_by_cpu:
        lines.append(
            f"CPU 상위 {len(overview.top_by_cpu)}개 (전체 {total}건 중, "
            f"상위 합 {round(overview.cpu_top_share, 2)} CPU%):"
        )
        lines.extend(_fmt(overview.top_by_cpu, "cpu"))
    if overview.top_by_mem:
        lines.append(
            f"메모리 상위 {len(overview.top_by_mem)}개 (전체 {total}건 중, "
            f"상위 합 {round(overview.mem_top_share, 2)} MEM%):"
        )
        lines.extend(_fmt(overview.top_by_mem, "memory"))

    if total == 0:
        lines.append("(프로세스 0건 — 조회 결과 없음)")
    else:
        # 상위 목록은 전체의 일부이며, 전체 목록은 CSV로 내려받을 수 있음을 안내(§10.2).
        lines.append(
            "참고: 위 상위 목록과 전체 건수·집계는 현황 분석에 충분한 데이터입니다. "
            "전체 프로세스 목록은 CSV 다운로드로 제공됩니다."
        )

    return "\n".join(lines)


def _full_process_rows(
    raw_processes: list[dict[str, Any]], metric: str
) -> list[dict[str, Any]]:
    """전체 프로세스를 기본 지표 내림차순으로 정렬·마스킹하여 CSV용 평면 행으로 만든다 (§10.3).

    - select_top_processes로 정렬·마스킹(args 포함)을 재사용하되 top_n=_MAX_CSV_ROWS로
      사실상 전체를 보존한다 (프로젝트 max rows 10,000 상한 준수).
    - metric="memory"면 pmem, 그 외(cpu/both)는 p100cpu(없으면 pcpu) 기준 정렬.
    - 모든 행 args는 mask_args() 적용분만 — 평문 비밀번호/토큰 비노출(§9 보안 제약).
    """
    sort_kind = "memory" if metric == "memory" else "cpu"
    ranked, _total = select_top_processes(raw_processes, sort_kind, _MAX_CSV_ROWS)
    return [_proc_to_dict(p) for p in ranked]


def _overview_rows(overview: ProcessOverview) -> list[dict[str, Any]]:
    """xlsx/docx 위임용 organized_data rows를 구성한다 (마스킹된 값만, 중복 제거)."""
    seen: set[int] = set()
    rows: list[dict[str, Any]] = []
    for proc in overview.top_by_cpu + overview.top_by_mem:
        if proc.pid in seen:
            continue
        seen.add(proc.pid)
        rows.append(_proc_to_dict(proc))
    return rows


def _text_only(response: str, target: dict[str, Any]) -> dict[str, Any]:
    """프로세스 표 없이 안내 텍스트만 반환하는 공통 State 업데이트."""
    return {
        "final_response": response,
        "process_overview": None,
        "routing_intent": "process_query",
        "current_node": "process_query",
        "process_query_target": target,
    }


async def _try_api(
    process_client, db_id: str, host: str  # noqa: ANN001
):
    """process_client.list_by_hostname을 graceful 호출한다 (실패/None 안전)."""
    try:
        return await process_client.list_by_hostname(db_id, host)
    except Exception as exc:  # noqa: BLE001 — graceful degradation
        logger.warning(
            "프로세스 API 조회 실패 — graceful 진행: db_id=%s host=%s err=%s",
            db_id,
            host,
            exc,
        )
        return None


async def process_query_node(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
    host_resolver=None,  # noqa: ANN001 — PolestarHostResolver (주입)
    process_client=None,  # noqa: ANN001 — PolestarProcessApiClient (주입)
) -> dict:
    """특정 자원의 실시간 프로세스 현황을 조회·분석한다 (Plan 48 §5.3).

    Args:
        state: 현재 에이전트 상태.
        llm: LLM 인스턴스 (외부 주입, 없으면 내부 생성).
        app_config: 앱 설정 (외부 주입, 없으면 내부 로드).
        host_resolver: PolestarHostResolver (Phase 3 graph 배선에서 partial 주입).
        process_client: PolestarProcessApiClient (동일 — 47-1 자산 재사용).

    Returns:
        업데이트할 State 필드 (process_overview / final_response /
        routing_intent="process_query" / current_node / process_query_target,
        output_format=xlsx/docx면 organized_data 포함).
    """
    if app_config is None:
        app_config = load_config()

    target = _extract_target(state, app_config)
    active_db_id = state.get("active_db_id")
    pq_cfg = app_config.process_query

    # (0) 게이팅 — 비활성/미설정/미주입 시 graceful 안내 종료
    if not pq_cfg.process_query_enabled:
        logger.info("process_query 비활성화 — 게이팅 종료")
        return _text_only(
            "현재 실시간 프로세스 조회 기능이 비활성화되어 있습니다.", target
        )
    if process_client is None or host_resolver is None:
        logger.warning("process_query 의존성 미주입 — graceful 종료")
        return _text_only(
            "실시간 프로세스 조회를 처리할 수 없습니다. 관리자에게 문의해 주세요.",
            target,
        )
    if not active_db_id or process_client.get_base_url(active_db_id) is None:
        logger.info("process_query base_url 미매핑 — 게이팅 종료: db_id=%s", active_db_id)
        return _text_only(
            "요청하신 시스템은 실시간 프로세스 조회를 지원하지 않습니다.", target
        )

    identifier = target["identifier"] or state.get("user_query", "").strip()
    timeout_s = pq_cfg.process_query_resolve_timeout_seconds

    # (2) host 해석
    resolved_host: Optional[str] = None
    resolved_db_id: str = active_db_id

    # ① 활성 db에서 DB 해석
    resolution = await _resolve_with_timeout(
        host_resolver, active_db_id, identifier, timeout_s
    )
    if resolution is not None and resolution.candidates:
        # 모호 — 임의 선택 금지, 후보 안내/HITL 되물음
        candidate_lines = "\n".join(f"- {c}" for c in resolution.candidates)
        logger.info("hostname 모호 — 후보 안내: %s", resolution.candidates)
        return _text_only(
            "입력하신 식별자에 해당하는 서버가 여러 대 조회되었습니다. "
            "어느 서버를 조회할지 hostname으로 알려주세요:\n" + candidate_lines,
            target,
        )
    if resolution is not None and resolution.hostname:
        resolved_host = resolution.hostname

    # ② 직접 hostname 폴백 — identifier를 그대로 hostname으로 보고 API 시도
    api_result = None
    if resolved_host is None and identifier:
        direct = await _try_api(process_client, active_db_id, identifier)
        if direct is not None and direct.processes:
            resolved_host = identifier
            api_result = direct

    # ③ 타 폴스타 db 재해석 (user_specified_db 세팅 시 스킵)
    if resolved_host is None and not state.get("user_specified_db"):
        for other_id in get_all_db_ids():
            if other_id == active_db_id:
                continue
            domain = get_domain_by_id(other_id)
            # 폴스타 + base_url 매핑된 db만 (불필요 외부 호출 방지)
            if domain is None or process_client.get_base_url(other_id) is None:
                continue
            other_res = await _resolve_with_timeout(
                host_resolver, other_id, identifier, timeout_s
            )
            if other_res is not None and other_res.hostname:
                resolved_host = other_res.hostname
                resolved_db_id = other_id
                break

    # 끝내 미해석 → 추측 금지, 안내 종료
    if resolved_host is None:
        logger.info("hostname 미해석 — 안내 종료: identifier=%r", identifier)
        return _text_only(
            "요청하신 자원의 hostname을 특정하지 못했습니다. "
            "서버명(또는 hostname)과 대상 시스템(DB)을 확인해 주세요.",
            target,
        )

    # (3) 프로세스 API 조회 (이미 ②에서 받았으면 재사용)
    if api_result is None:
        api_result = await _try_api(process_client, resolved_db_id, resolved_host)
    if api_result is None:
        logger.warning("프로세스 API 결과 없음 — graceful 안내: host=%s", resolved_host)
        return _text_only(
            f"'{resolved_host}' 의 실시간 프로세스 정보를 가져오지 못했습니다. "
            "잠시 후 다시 시도해 주세요.",
            target,
        )

    # (4) 결정적 선별·집계·마스킹
    overview = build_process_overview(
        api_result.processes,
        target["metric"],
        target["top_n"],
        source_host=resolved_host,
        captured_at=api_result.captured_at,
    )

    # (5) LLM 현황 분석 (마스킹된 결정적 요약만 주입)
    summary_text = _build_summary_text(overview)
    if llm is None:
        from src.llm import create_llm

        llm = create_llm(app_config)
    messages = [
        SystemMessage(content=PROCESS_QUERY_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"다음은 결정적으로 선별·집계·마스킹된 프로세스 현황 요약입니다. "
                f"이 요약만 인용·해석하여 현황을 설명해 주세요.\n\n{summary_text}"
            )
        ),
    ]
    try:
        response = await llm.ainvoke(messages)
        final_response = response.content
    except Exception as exc:  # noqa: BLE001 — graceful degradation
        logger.error("process_query LLM 호출 실패 — 결정적 요약으로 대체: %s", exc)
        final_response = (
            f"'{resolved_host}' 의 실시간 프로세스 현황입니다.\n\n{summary_text}"
        )

    # (6) State 반환
    #  - query_results: 전체 프로세스(마스킹 args 포함)를 평면 행으로 채워
    #    기존 CSV 다운로드(/query/{id}/download-csv)·UI CSV 버튼을 그대로 재사용한다(§10.3).
    #    LLM 주입 요약(top N)과 별개로 CSV는 결정적 전체 행.
    result: dict[str, Any] = {
        "process_overview": _overview_to_dict(overview),
        "final_response": final_response,
        "routing_intent": "process_query",
        "current_node": "process_query",
        "process_query_target": target,
        "query_results": _full_process_rows(api_result.processes, target["metric"]),
    }

    # output_format=xlsx/docx면 organized_data rows 구성 (output_generator 위임은 Phase 3)
    if state.get("file_type") in ("xlsx", "docx"):
        rows = _overview_rows(overview)
        result["organized_data"] = {
            "summary": f"{resolved_host} 실시간 프로세스 현황 (전체 {overview.total_count}건)",
            "rows": rows,
            "column_mapping": None,
            "resolved_mapping": None,
            "is_sufficient": bool(rows),
            "sheet_mappings": None,
        }

    logger.info(
        "process_query 완료: host=%s total=%d metric=%s",
        resolved_host,
        overview.total_count,
        target["metric"],
    )
    return result
