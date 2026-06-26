"""실시간 프로세스 조회 subagent (Plan 50 M4).

"현재/실시간 프로세스 리스트" 류 질의를 DB 조회가 아닌 폴스타 **실시간 프로세스 API**로
처리한다. 없는 테이블(`SDQ000.MON_CF_WAIT_TIME`) 조회로 인한 `SQL0204N` 재발을 막는다.

재사용 (신규 비즈니스 로직 없음):
- 조회: `src.alarm.infrastructure.polestar_process_api.PolestarProcessApiClient` (Plan 47-1, infrastructure).
- 선별·마스킹: `src.alarm.domain.process_rank.select_top_processes` (Plan 47-1, domain — 결정적 처리).
  → 마스킹·상위 N 선별은 결정적으로 수행하고 LLM에 원시 주입하지 않는다(D-047-1 / Known Mistakes 정합).

대상 결정:
- db_id: task.db_ids(승계/고정) → conversation_context.previous_db_ids → 위치 기반 분류(classify_dbs) 순.
- hostname: 이번 턴 filter_conditions 식별 키 → conversation_context.previous_entities 순 ("해당 서버" 해소, M3).

base_url 매핑(`AlarmConfig.get_process_api_base_url`)이 없는 db_id는 graceful degradation(안내 메시지).

계층: 본 모듈은 orchestration. polestar_process_api는 infrastructure, process_rank는 domain
→ orchestration → {infrastructure, domain} 의존은 정합(arch_check 통과).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel

from src.alarm.domain.process_rank import select_top_processes
from src.alarm.infrastructure.polestar_process_api import PolestarProcessApiClient
from src.config import AppConfig

logger = logging.getLogger(__name__)

# 프로세스 정렬 기준 — 일반 "현재 프로세스 리스트"는 CPU 점유 내림차순으로 본다(메모리 의도 키워드 시 memory).
_MEMORY_QUERY_HINTS = ("메모리", "memory", "mem ", "ram")
# hostname으로 인정할 filter_conditions field / previous_entities field.
_HOST_FIELDS = ("hostname", "host_name", "server_name", "name")
# 위치 → 폴스타 db_id 매핑 신호 (base_url 매핑이 있는 db_id로만 좁힘).
_LOCATION_DB_HINTS: dict[str, tuple[str, ...]] = {
    "polestar_cm_gp": ("김포",),
    "polestar_cm_yd": ("여의도",),
}


def _infer_alarm_kind(sub_query: str) -> str:
    """질의에서 정렬 기준(cpu/memory)을 추론한다 (기본 cpu)."""
    lowered = (sub_query or "").lower()
    if any(h in lowered for h in _MEMORY_QUERY_HINTS):
        return "memory"
    return "cpu"


def _resolve_db_id(
    task: dict,
    isolated: dict,
    sub_query: str,
    app_config: AppConfig,
) -> Optional[str]:
    """프로세스 API 대상 db_id를 결정한다.

    우선순위: ① task.db_ids(승계/고정) > ② previous_db_ids(멀티턴) > ③ 위치 신호 매칭.
    base_url 매핑이 있는 db_id를 우선 선택한다(조회 가능한 대상으로 좁힘).

    Args:
        task: 현재 TaskSpec
        isolated: 격리 입력(conversation_context 포함)
        sub_query: 이번 턴 질의
        app_config: 앱 설정

    Returns:
        대상 db_id 또는 None
    """
    alarm_cfg = app_config.alarm

    def _has_base_url(did: str) -> bool:
        return bool(did) and bool(alarm_cfg.get_process_api_base_url(did))

    candidates: list[str] = []

    # ① task 고정/승계 db_ids
    for did in task.get("db_ids") or []:
        if isinstance(did, str) and did not in candidates:
            candidates.append(did)

    # ② 멀티턴 승계 previous_db_ids
    ctx = isolated.get("conversation_context") or {}
    for did in ctx.get("previous_db_ids") or []:
        if isinstance(did, str) and did not in candidates:
            candidates.append(did)

    # base_url 매핑이 있는 후보 우선
    for did in candidates:
        if _has_base_url(did):
            return did

    # ③ 위치 신호 매칭 (sub_query + previous_location)
    location_text = f"{sub_query} {ctx.get('previous_location', '')}"
    for did, hints in _LOCATION_DB_HINTS.items():
        if _has_base_url(did) and any(h in location_text for h in hints):
            return did

    # 후보가 있으나 base_url이 없으면 첫 후보 반환(호출부에서 graceful 안내)
    return candidates[0] if candidates else None


def _resolve_hostname(isolated: dict) -> Optional[str]:
    """대상 hostname을 결정한다 (이번 턴 filter → previous_entities, M3).

    Args:
        isolated: 격리 입력(parsed_requirements/conversation_context 포함)

    Returns:
        hostname 문자열 또는 None
    """
    # ① 이번 턴 filter_conditions의 식별 키
    parsed = isolated.get("parsed_requirements") or {}
    for cond in parsed.get("filter_conditions", []) or []:
        if not isinstance(cond, dict):
            continue
        if str(cond.get("field", "")).lower() in _HOST_FIELDS:
            value = cond.get("value")
            if value:
                return str(value)

    # ② 직전 턴 식별 엔티티 (M3 — "해당 서버" 해소)
    ctx = isolated.get("conversation_context") or {}
    for ent in ctx.get("previous_entities") or []:
        if not isinstance(ent, dict):
            continue
        if str(ent.get("field", "")).lower() in _HOST_FIELDS:
            value = ent.get("value")
            if value:
                return str(value)

    return None


def _process_to_dict(p) -> dict[str, Any]:  # noqa: ANN001 — ProcessInfo
    """ProcessInfo(마스킹 완료)를 결과 행 dict로 변환한다(args는 이미 마스킹됨)."""
    return {
        "name": p.name,
        "pid": p.pid,
        "user": p.user,
        "cpu_pct": p.p100cpu,
        "mem_pct": p.pmem,
        "rss": p.rss,
        "args": p.args,
    }


async def run_process_query(
    task: dict,
    isolated: dict,
    *,
    llm: BaseChatModel,
    app_config: AppConfig,
) -> dict:
    """실시간 프로세스 리스트를 폴스타 프로세스 API로 조회한다 (Plan 50 M4 subagent handler).

    SUBAGENT_REGISTRY handler 규약(task, isolated, *, llm, app_config)을 따른다.
    llm은 사용하지 않는다(결정적 조회·선별 — 마스킹된 상위 N만 반환).

    Args:
        task: 현재 TaskSpec (db_ids 승계/고정 가능)
        isolated: 격리 입력 (user_query=task["sub_query"], conversation_context 포함)
        llm: LLM 인스턴스 (미사용 — 시그니처 호환)
        app_config: 앱 설정

    Returns:
        {organized_data, query_results, source, (error)} 형태 dict
        (정렬·마스킹은 select_top_processes가 결정적으로 수행 — D-047-1 정합)
    """
    sub_query = task.get("sub_query", isolated.get("user_query", ""))
    db_id = _resolve_db_id(task, isolated, sub_query, app_config)
    hostname = _resolve_hostname(isolated)

    # 대상 미식별 → graceful 안내 (없는 테이블 조회로 폴백하지 않음 — SQL0204N 방지)
    if not db_id:
        msg = "프로세스 조회 대상 DB(위치)를 식별하지 못했습니다. 위치(예: 김포/여의도)를 지정해 주세요."
        return _empty_result(msg, db_id, hostname)
    if not hostname:
        msg = "프로세스 조회 대상 서버(hostname)를 식별하지 못했습니다. 서버명을 지정해 주세요."
        return _empty_result(msg, db_id, hostname)

    base_url = app_config.alarm.get_process_api_base_url(db_id)
    if not base_url:
        msg = (
            f"'{db_id}'는 실시간 프로세스 API가 연결되지 않은 DB입니다. "
            "프로세스 API가 매핑된 폴스타(예: 김포/여의도)에서만 실시간 프로세스 조회가 가능합니다."
        )
        return _empty_result(msg, db_id, hostname)

    client = PolestarProcessApiClient(app_config.alarm)
    result = await client.list_by_hostname(db_id, hostname)
    if result is None:
        msg = (
            f"서버 '{hostname}'의 실시간 프로세스를 조회하지 못했습니다 "
            "(프로세스 API 미응답/타임아웃). 잠시 후 다시 시도해 주세요."
        )
        return _empty_result(msg, db_id, hostname)

    alarm_kind = _infer_alarm_kind(sub_query)
    top, total = select_top_processes(
        result.processes, alarm_kind, app_config.alarm.process_top_n
    )
    rows = [_process_to_dict(p) for p in top]

    metric_label = "메모리" if alarm_kind == "memory" else "CPU"
    summary = (
        f"서버 '{hostname}'의 현재 실행 중 프로세스 {total}건 중 {metric_label} 점유 상위 "
        f"{len(rows)}건 (스냅샷 시각: {result.captured_at or '미상'})."
    )

    logger.info(
        "process_query: db_id=%s hostname=%s total=%d top=%d kind=%s",
        db_id, hostname, total, len(rows), alarm_kind,
    )

    return {
        "organized_data": {
            "summary": summary,
            "rows": rows,
            "column_mapping": None,
            "resolved_mapping": None,
            "is_sufficient": True,
            "sheet_mappings": None,
        },
        "query_results": rows,
        "source": [{"db_id": db_id, "reason": "실시간 프로세스 API (Plan 47-1 재사용)"}],
        "target_db_ids": [db_id],
        "process_query": {
            "db_id": db_id,
            "hostname": hostname,
            "total_count": total,
            "captured_at": str(result.captured_at) if result.captured_at else None,
            "metric": alarm_kind,
        },
    }


def _empty_result(message: str, db_id: Optional[str], hostname: Optional[str]) -> dict:
    """조회 불가 시 graceful 빈 결과(안내 요약)를 반환한다."""
    return {
        "organized_data": {
            "summary": message,
            "rows": [],
            "column_mapping": None,
            "resolved_mapping": None,
            "is_sufficient": False,
            "sheet_mappings": None,
        },
        "query_results": [],
        "source": [{"db_id": db_id} if db_id else {}],
        "target_db_ids": [db_id] if db_id else [],
        "process_query": {"db_id": db_id, "hostname": hostname, "total_count": 0},
    }
