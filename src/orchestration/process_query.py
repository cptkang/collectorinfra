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

서버명 → 호스트명 해소 (D-046):
- 프로세스 API의 조회 키는 **hostname**이지만 사용자는 보통 서버명(cmm_resource.name)으로 질의한다.
  공동존 폴스타(gp/yd)는 name ≠ hostname 이므로 서버명을 그대로 hostname으로 보내면 0건 → 환각.
- `PolestarHostnameResolver`(infrastructure)로 입력 값을 정규 hostname으로 해소한 뒤 API를 호출한다.
  해소 실패/0건이면 원시 입력 값을 그대로 사용(이미 hostname인 경우 호환).

base_url 매핑(`AlarmConfig.get_process_api_base_url`)이 없는 db_id는 graceful degradation(안내 메시지).

계층: 본 모듈은 orchestration. polestar_process_api/polestar_hostname_resolver는 infrastructure,
process_rank는 domain → orchestration → {infrastructure, domain} 의존은 정합(arch_check 통과).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel

from src.alarm.domain.process_rank import select_top_processes
from src.alarm.infrastructure.polestar_process_api import PolestarProcessApiClient
from src.config import AppConfig
from src.routing.registry import get_registry

logger = logging.getLogger(__name__)

# 프로세스 정렬 기준 — 일반 "현재 프로세스 리스트"는 CPU 점유 내림차순으로 본다(메모리 의도 키워드 시 memory).
_MEMORY_QUERY_HINTS = ("메모리", "memory", "mem ", "ram")
# hostname으로 인정할 filter_conditions field / previous_entities field.
# 영문 컬럼명뿐 아니라 LLM이 한글 필드명으로 추출하는 경우(서버명/장비명/호스트명 등)도 방어적으로 인정한다
# (input_parser는 hostname으로 정규화하도록 유도하지만 LLM 출력은 비결정적이므로 한글 변형까지 수용).
_HOST_FIELDS = (
    "hostname", "host_name", "server_name", "name", "host", "device_name",
    "서버명", "서버이름", "장비명", "호스트명", "서버", "장비",
)
# 지시어(demonstrative) 접두 — 실제 서버명이 아니라 직전 대상을 가리키는 표현.
_DEMONSTRATIVE_PREFIXES = ("해당", "그", "이", "저", "위", "방금", "앞서", "직전", "이전")
# 지시어 뒤에 붙는 일반 명사 (식별자 아님).
_DEMONSTRATIVE_NOUNS = ("서버", "장비", "호스트명", "호스트", "인스턴스", "노드", "머신", "시스템")
# 위치 → db_id 매핑 신호. 첫 턴(task.db_ids/previous_db_ids 공백)에 질의 텍스트로
# db_id를 재도출하는 결정적 폴백. 단일 DB를 **배타적으로** 지목하는 위치 표면어만 담긴다
# (여러 DB를 포괄하는 존 표면어 "공동존"은 대상 DB를 좁히지 못하므로 제외).
# 정본은 `config/db_registry.yaml`의 locations 선언 — Plan 67 R2, 사본 금지.
_LOCATION_DB_HINTS: dict[str, tuple[str, ...]] = get_registry().location_db_hints()


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
    hint_matches = [
        did for did, hints in _LOCATION_DB_HINTS.items()
        if any(h in location_text for h in hints)
    ]
    # base_url 매핑이 있는 매치를 우선하되, 없더라도 위치 신호가 명확하면 해당 db_id를 반환한다
    # (db_id=None으로 빠져 "위치 미식별"이라는 잘못된 안내가 나가는 것을 막고, base_url 게이트가
    #  "API 미연결" 같은 정확한 원인을 알리도록 한다).
    for did in hint_matches:
        if _has_base_url(did):
            return did
    if hint_matches:
        return hint_matches[0]

    # 후보가 있으나 base_url이 없으면 첫 후보 반환(호출부에서 graceful 안내)
    return candidates[0] if candidates else None


def _is_demonstrative_value(value: object) -> bool:
    """서버 식별자 값이 실제 이름이 아니라 지시어/플레이스홀더인지 판정한다.

    후속 턴에서 "해당 서버/그 장비/위 서버" 같은 지시어가 input_parser에 의해 hostname
    필터로 그대로 추출되거나, LLM이 "previous_server" 같은 플레이스홀더를 지어내는 경우가
    있다. 이런 값이 이번 턴 filter로 들어오면 previous_entities(직전 실제 서버)보다
    우선순위가 높아 잘못된 hostname으로 API를 호출(0건→환각)하게 된다. 이를 결정적으로
    걸러 previous_entities 폴백으로 넘긴다(Known Mistakes: LLM 의존은 결정적 가드로 교정).

    Args:
        value: filter_conditions/previous_entities의 식별자 값

    Returns:
        지시어/플레이스홀더면 True (실제 서버명이면 False)
    """
    if value is None:
        return True
    v = str(value).strip()
    if not v:
        return True
    # 영문 플레이스홀더: previous/prev + server/host (예: previous_server, prev_host)
    compact = v.lower().replace(" ", "").replace("_", "").replace("-", "")
    if ("previous" in compact or compact.startswith("prev")) and (
        "server" in compact or "host" in compact
    ):
        return True
    # 한글 지시어(+ 일반 명사): "해당", "해당 서버", "그 장비", "위 서버" 등
    body = v
    for noun in _DEMONSTRATIVE_NOUNS:
        if body.endswith(noun):
            body = body[: -len(noun)].strip()
            break
    return body in _DEMONSTRATIVE_PREFIXES


def _resolve_hostname(isolated: dict) -> Optional[str]:
    """대상 hostname을 결정한다 (이번 턴 filter → previous_entities, M3).

    Args:
        isolated: 격리 입력(parsed_requirements/conversation_context 포함)

    Returns:
        hostname 문자열 또는 None
    """
    # ① 이번 턴 filter_conditions의 식별 키 (지시어/플레이스홀더는 제외 — ②로 폴백)
    parsed = isolated.get("parsed_requirements") or {}
    for cond in parsed.get("filter_conditions", []) or []:
        if not isinstance(cond, dict):
            continue
        if str(cond.get("field", "")).lower() in _HOST_FIELDS:
            value = cond.get("value")
            if value and not _is_demonstrative_value(value):
                return str(value)

    # ② 직전 턴 식별 엔티티 (M3 — "해당 서버" 해소)
    ctx = isolated.get("conversation_context") or {}
    for ent in ctx.get("previous_entities") or []:
        if not isinstance(ent, dict):
            continue
        if str(ent.get("field", "")).lower() in _HOST_FIELDS:
            value = ent.get("value")
            if value and not _is_demonstrative_value(value):
                return str(value)

    return None


async def _resolve_canonical_hostname(
    db_id: str, value: str, app_config: AppConfig
) -> Optional[str]:
    """입력 식별자(서버명 또는 호스트명)를 정규 hostname으로 해소한다 (D-046).

    `PolestarHostnameResolver`로 폴스타 DB(cmm_resource)를 조회하여 hostname을 얻는다.
    어떤 단계든 실패하면 None을 반환하고, 호출부는 원시 입력 값을 그대로 사용한다
    (이미 hostname이거나 DB가 미연결인 경우에도 회귀 없이 동작).

    Args:
        db_id: 조회 대상 폴스타 인스턴스
        value: 사용자 입력 식별자(서버명/장비명 또는 호스트명)
        app_config: 앱 설정 (DBRegistry 생성용)

    Returns:
        해소된 hostname 또는 None(해소 불가)
    """
    try:
        from src.alarm.infrastructure.polestar_hostname_resolver import (
            PolestarHostnameResolver,
        )
        from src.routing.db_registry import DBRegistry

        registry = DBRegistry(app_config)
        resolver = PolestarHostnameResolver(registry)
        return await resolver.resolve(db_id, value)
    except Exception as exc:
        logger.warning(
            "hostname 해소 중 예외 — 원시 값 사용: db_id=%s value=%s err=%s",
            db_id, value, exc,
        )
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
    identifier = _resolve_hostname(isolated)
    base_url_present = bool(db_id and app_config.alarm.get_process_api_base_url(db_id))
    # 진입 진단(early-return으로 hostname 해소·API 로그가 안 남는 경우를 식별하기 위함):
    # 어느 게이트(db_id/identifier/base_url)에서 0건으로 빠지는지 한 줄로 드러낸다.
    logger.info(
        "process_query 진입: db_id=%s identifier=%s base_url=%s sub_query=%r",
        db_id, identifier, "있음" if base_url_present else "없음", (sub_query or "")[:120],
    )

    # 대상 미식별 → graceful 안내 (없는 테이블 조회로 폴백하지 않음 — SQL0204N 방지)
    if not db_id:
        logger.warning("process_query 0건: db_id 미식별 (위치 신호 부족)")
        msg = "프로세스 조회 대상 DB(위치)를 식별하지 못했습니다. 위치(예: 김포/여의도)를 지정해 주세요."
        return _empty_result(msg, db_id, identifier)
    if not identifier:
        logger.warning("process_query 0건: identifier(서버명) 미식별 db_id=%s", db_id)
        msg = "프로세스 조회 대상 서버(서버명)를 식별하지 못했습니다. 서버명을 지정해 주세요."
        return _empty_result(msg, db_id, identifier)

    base_url = app_config.alarm.get_process_api_base_url(db_id)
    if not base_url:
        logger.warning("process_query 0건: base_url 미매핑 db_id=%s (런타임 .env 확인 필요)", db_id)
        msg = (
            f"'{db_id}'는 실시간 프로세스 API가 연결되지 않은 DB입니다. "
            "프로세스 API가 매핑된 폴스타(예: 김포/여의도)에서만 실시간 프로세스 조회가 가능합니다."
        )
        return _empty_result(msg, db_id, identifier)

    # 서버명 → 호스트명 해소 (D-046): 프로세스 API 조회 키는 hostname이므로,
    # 입력이 서버명이면 cmm_resource에서 hostname을 찾아 사용한다. 해소 실패 시 원시 값 사용.
    resolved = await _resolve_canonical_hostname(db_id, identifier, app_config)
    hostname = resolved or identifier
    # 사용자에게 보일 서버 레이블: 입력 식별자(서버명)를 우선 표기하되, 호스트명이 다르면 함께 표기.
    server_label = identifier if hostname == identifier else f"{identifier}(호스트명 {hostname})"

    client = PolestarProcessApiClient(app_config.alarm)
    result = await client.list_by_hostname(db_id, hostname)
    if result is None:
        msg = (
            f"서버 '{server_label}'의 실시간 프로세스를 조회하지 못했습니다 "
            "(프로세스 API 미응답/타임아웃). 잠시 후 다시 시도해 주세요."
        )
        return _empty_result(msg, db_id, hostname)

    alarm_kind = _infer_alarm_kind(sub_query)
    # 전체를 한 번에 지표 내림차순 정렬·마스킹한 뒤(top_n=전체 건수),
    # 채팅 표시는 상위 N개로 제한하고 CSV 다운로드용 query_results에는 전체를 보존한다
    # (D-047 / 사용자 결정 2026-06-29: "채팅 상위N + CSV 전체").
    ranked_all, total = select_top_processes(
        result.processes, alarm_kind, len(result.processes or [])
    )
    full_rows = [_process_to_dict(p) for p in ranked_all]
    top_n = max(0, app_config.alarm.process_top_n)
    rows = full_rows[:top_n]  # 채팅 표시용 상위 N

    metric_label = "메모리" if alarm_kind == "memory" else "CPU"
    if total > len(rows):
        summary = (
            f"서버 '{server_label}'의 현재 실행 중 프로세스 {total}건 중 {metric_label} 점유 상위 "
            f"{len(rows)}건을 표시합니다 (스냅샷 시각: {result.captured_at or '미상'}). "
            f"전체 {total}건은 'CSV 다운로드'로 받을 수 있습니다."
        )
    else:
        summary = (
            f"서버 '{server_label}'의 현재 실행 중 프로세스 {total}건 "
            f"(스냅샷 시각: {result.captured_at or '미상'})."
        )

    logger.info(
        "process_query: db_id=%s identifier=%s hostname=%s total=%d shown=%d kind=%s",
        db_id, identifier, hostname, total, len(rows), alarm_kind,
    )

    return {
        "organized_data": {
            "summary": summary,
            "rows": rows,  # 채팅 표시: 상위 N (output_generator가 이 rows로 표 생성)
            "column_mapping": None,
            "resolved_mapping": None,
            "is_sufficient": True,
            "sheet_mappings": None,
        },
        "query_results": full_rows,  # CSV 다운로드·row_count: 전체 프로세스
        "source": [{"db_id": db_id, "reason": "실시간 프로세스 API (Plan 47-1 재사용)"}],
        "target_db_ids": [db_id],
        "process_query": {
            "db_id": db_id,
            "server_name": identifier,
            "hostname": hostname,
            "total_count": total,
            "shown_count": len(rows),
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
