"""처리 현황 — 작업 요약·SQL 추적·노드별 진행 요약 표.

plans/179 W2 — `src/api/routes/query.py`에서 순수 이동(본문·주석·평가 순서 불변).
옛 경로 `src.api.routes.query`가 같은 이름으로 재노출한다(D-337 ① shim).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from src.config import SecurityConfig
from src.security.data_masker import DataMasker
from src.utils.user_error import db_error_text, user_error_text, validation_reason_text

# 테스트·운영 로그가 이 이름으로 잡는다(이동 전 로거 이름 유지 — plans/179 W2)
logger = logging.getLogger("src.api.routes.query")


def _summarize_tasks(
    tasks: list[dict[str, Any]], results: dict[Any, Any] | None = None
) -> list[dict[str, Any]]:
    """task_plan을 처리 현황 표시용 경량 항목으로 요약한다 (order 정렬, 표시 필드만).

    results(task_results)가 주어지면 task별 생성 SQL·대상 DB·행수·DB 에러를 함께
    포함한다. orchestration 경로에서는 query_generator가 그래프 노드가 아니어서
    생성 SQL이 따로 노출되지 않으므로, 어떤 SQL이 어느 DB로 실행됐는지 보이게 한다.
    """
    ordered = sorted(tasks, key=lambda t: t.get("order", 0))
    summarized: list[dict[str, Any]] = []
    for i, t in enumerate(ordered):
        item: dict[str, Any] = {
            "order": t.get("order", i + 1),
            "agent": t.get("agent", ""),
            "sub_query": t.get("sub_query", ""),
            "status": t.get("status", "pending"),
        }
        res = results.get(t.get("task_id")) if isinstance(results, dict) else None
        if isinstance(res, dict):
            # 실행 경로 표식(Plan 71): realtime_api면 처리 현황 라벨을 "실시간 API 조회"로
            # 표시한다 — data_query 고정 라벨("DB 조회")이 실제 경로와 다르게 보이는 문제.
            if res.get("source"):
                item["source"] = res.get("source")
            sql = res.get("generated_sql")
            if sql:
                item["generated_sql"] = sql
            # 존 순차 실행 경과(D-206) — 그룹별 행수·소요를 처리현황에 그대로 노출
            if res.get("group_results"):
                item["group_results"] = res.get("group_results")
            db_ids = res.get("target_db_ids")
            if db_ids:
                item["target_db_ids"] = db_ids
            rows = res.get("query_results")
            if isinstance(rows, list):
                item["row_count"] = len(rows)
            # 실패 사유는 화면(파이프라인 상세)에 고정 사유만 보인다 — 예외 원문은 고정 문구로
            # 바꾼다(plans/163 S3+). 원문은 task 진행 이벤트(`emit_task_progress`)가 로그로 남긴다.
            # DB별 사유는 예외 보간부만 떼고 산출 발췌(D-153 후속2)·마감 문구는 남긴다
            # (원문은 실행기 로그).
            db_errors = res.get("db_errors")
            if db_errors:
                item["db_errors"] = (
                    {k: db_error_text(v) for k, v in db_errors.items()}
                    if isinstance(db_errors, dict) else db_error_text(db_errors)
                )
            elif res.get("error"):
                item["error"] = user_error_text(res.get("error"), result=res)
            # 제니퍼 소스 되묻기(plans/147)는 오류가 아니다 — `error`는 재계획 종결 판정용으로
            # 남기고 처리 현황은 이 표지로 「확인 필요」를 보인다(미연결 안내는 표지 없음 —
            # 종전 표시)
            if res.get("apm_source_clarification") or res.get("apm_xview_confirmation"):
                item["clarification"] = True
        summarized.append(item)
    return summarized


_SQL_STATE_KEYS = ("generated_sql", "task_plan", "task_results", "db_executed_sqls")


def _track_sql_state(tracked: dict[str, Any], output: Any) -> None:
    """루트 직속 노드 출력에서 실행 SQL 출처 키를 누적한다 (plans/116 §10.3).

    스트림은 `final_response`를 낸 첫 노드 출력(2단 result_aggregator · 3단 output_generator)에서
    닫히는데 그 출력에는 SQL이 없다. 앞 노드들이 낸 값을 모아 두었다가 `_executed_sql`로 조립한다.
    빈 값은 덮어쓰지 않는다.
    """
    if not isinstance(output, dict):
        return
    for key in _SQL_STATE_KEYS:
        if output.get(key):
            tracked[key] = output[key]


def _merge_node_delta(state: dict[str, Any], output: Any) -> None:
    """루트 직속 노드 델타를 스트림의 누적 상태에 덮어쓴다 (plans/120 S-1 · D-205 4경로 대칭).

    스트림은 `final_response`를 낸 첫 노드에서 닫혀 그 노드의 델타만 손에 쥔다. 비스트림 `ainvoke`는
    체크포인트 복원값 + 입력 + 전 노드 델타가 병합된 전체 상태를 돌려준다. 3단 `output_generator`
    델타에는 대상 DB 키가 없어 스트림 `done.db_scope`만 비었다. 리듀서 채널(messages 등)은
    덮어쓰기로 근사되므로 이 누적본은 `build_db_scope` 입력으로만 쓴다.
    """
    if isinstance(output, dict):
        state.update(output)


def _progress_input_parser(
    output: dict[str, Any], security: SecurityConfig
) -> dict[str, Any] | None:
    """`input_parser` 진행 요약 — 파싱 요구사항·양식 구조."""
    parsed = output.get("parsed_requirements", {})
    template = output.get("template_structure")
    data: dict[str, Any] = {}
    if parsed:
        data["parsed_requirements"] = parsed
    if template:
        data["template_structure"] = template
    return data if data else None


def _progress_schema_analyzer(
    output: dict[str, Any], security: SecurityConfig
) -> dict[str, Any] | None:
    """`schema_analyzer` 진행 요약 — 스키마 캐시 출처·관련 테이블."""
    tables = output.get("relevant_tables", [])
    data: dict[str, Any] = {}
    cache_source = output.get("schema_cache_source")
    if cache_source:
        data["cache_source"] = cache_source
    if tables:
        data["relevant_tables"] = tables
    return data if data else None


def _progress_query_generator(
    output: dict[str, Any], security: SecurityConfig
) -> dict[str, Any] | None:
    """`query_generator` 진행 요약 — 생성 SQL·유사어 사용 내역."""
    sql = output.get("generated_sql", "")
    data: dict[str, Any] = {}
    if sql:
        data["generated_sql"] = sql
    usage = output.get("synonym_usage")
    if isinstance(usage, dict) and (
        usage.get("mappings") or usage.get("unregistered")
    ):
        data["synonym_usage"] = usage
    return data if data else None


def _progress_query_validator(
    output: dict[str, Any], security: SecurityConfig
) -> dict[str, Any] | None:
    """`query_validator` 진행 요약 — 통과 여부·사유."""
    result = output.get("validation_result", {})
    return {
        "passed": result.get("passed", False),
        # 파서 예외·백엔드 응답 원문은 떼고 보인다(plans/163 S3+ · 원문은 노드 로그)
        "reason": validation_reason_text(result.get("reason", "")),
    }


def _progress_query_executor(
    output: dict[str, Any], security: SecurityConfig
) -> dict[str, Any] | None:
    """`query_executor` 진행 요약 — 행 수·마스킹한 미리보기 행·에러 머리말."""
    results = output.get("query_results", [])
    error = output.get("error_message")
    data = {
        "row_count": len(results),
        "preview_rows": DataMasker(security).mask_rows(
            [r for r in results[:10] if isinstance(r, dict)]
        ),
    }
    if error:
        # 실행 에러 원문(「DB 연결 에러: {e}」)은 머리말만 보인다(plans/163 S3+ · 원문은
        # 노드 로그와 스트림 단계 기록에 남는다)
        data["error"] = user_error_text(error)
    return data


def _progress_result_organizer(
    output: dict[str, Any], security: SecurityConfig
) -> dict[str, Any] | None:
    """`result_organizer` 진행 요약 — 요약·충분성·행 수·컬럼 매핑."""
    organized = output.get("organized_data", {})
    data: dict[str, Any] = {}
    if organized:
        data["summary"] = organized.get("summary", "")
        data["is_sufficient"] = organized.get("is_sufficient", False)
        rows = organized.get("rows", [])
        data["row_count"] = len(rows)
        mapping = organized.get("column_mapping")
        if mapping:
            data["column_mapping"] = mapping
    return data if data else None


def _progress_answer_done(
    output: dict[str, Any], security: SecurityConfig
) -> dict[str, Any] | None:
    """`output_generator`·`general_inference` 진행 요약 — 응답 생성 완료."""
    return {"status": "응답 생성 완료"}


def _progress_error_response(
    output: dict[str, Any], security: SecurityConfig
) -> dict[str, Any] | None:
    """`error_response` 진행 요약 — 최종 응답 문구."""
    return {"error": output.get("final_response", "")}


def _progress_context_resolver(
    output: dict[str, Any], security: SecurityConfig
) -> dict[str, Any] | None:
    """`context_resolver` 진행 요약 — 대화 턴 번호."""
    ctx = output.get("conversation_context")
    turn = ctx.get("turn_count", 1) if ctx else 1
    return {"turn": turn}


def _progress_field_mapper(
    output: dict[str, Any], security: SecurityConfig
) -> dict[str, Any] | None:
    """`field_mapper` 진행 요약 — 매핑 수·출처별 건수(매핑이 없으면 건너뜀 표시)."""
    if "column_mapping" not in output:
        return {"skipped": True}
    mapping = output.get("column_mapping") or {}
    sources = output.get("mapping_sources") or {}
    has_report = output.get("mapping_report_md") is not None
    data: dict[str, Any] = {
        "mapped_count": sum(1 for v in mapping.values() if v is not None),
        "total_count": len(mapping),
        "has_mapping_report": has_report,
    }
    if sources:
        data["sources"] = {
            "hint": sum(1 for s in sources.values() if s == "hint"),
            "synonym": sum(1 for s in sources.values() if s == "synonym"),
            "eav_synonym": sum(1 for s in sources.values() if s == "eav_synonym"),
            "llm_inferred": sum(1 for s in sources.values() if s == "llm_inferred"),
        }
    return data


def _progress_semantic_router(
    output: dict[str, Any], security: SecurityConfig
) -> dict[str, Any] | None:
    """`semantic_router` 진행 요약 — 라우팅 의도·대상 DB(최대 3건)."""
    intent = output.get("routing_intent", "")
    active_db = output.get("active_db_id")
    is_multi = output.get("is_multi_db", False)
    targets = output.get("target_databases", [])
    data = {"routing_intent": intent}
    if active_db:
        data["active_db_id"] = active_db
    if is_multi:
        data["is_multi_db"] = True
    if targets:
        data["targets"] = [
            {"db_id": t.get("db_id"), "reason": t.get("reason", "")}
            for t in targets[:3]
        ]
    return data


def _progress_approval_gate(
    output: dict[str, Any], security: SecurityConfig
) -> dict[str, Any] | None:
    """`approval_gate` 진행 요약 — 승인 대기일 때만 SQL."""
    if output.get("awaiting_approval"):
        return {"awaiting_approval": True, "sql": output.get("approval_context", {}).get("sql", "")}
    return None


def _progress_intent_planner(
    output: dict[str, Any], security: SecurityConfig
) -> dict[str, Any] | None:
    """`intent_planner`·`plan`·`normalize` 진행 요약 — 작업 계획."""
    tasks = output.get("task_plan", [])
    if not tasks:
        return None
    return {
        "task_count": len(tasks),
        "is_composite": output.get("is_composite", False),
        "tasks": _summarize_tasks(tasks),
    }


def _progress_agent_orchestrator(
    output: dict[str, Any], security: SecurityConfig
) -> dict[str, Any] | None:
    """`agent_orchestrator`·`join` 진행 요약 — 작업별 결과."""
    tasks = output.get("task_plan", [])
    if not tasks:
        return None
    return {
        "task_count": len(tasks),
        "tasks": _summarize_tasks(tasks, results=output.get("task_results")),
    }


def _progress_replanner(
    output: dict[str, Any], security: SecurityConfig
) -> dict[str, Any] | None:
    """`replanner`·`replan` 진행 요약 — 재계획 여부·이력·횟수."""
    needs = output.get("needs_replan", False)
    history = output.get("replan_history") or []
    data: dict[str, Any] = {"needs_replan": needs}
    if history:
        data["replan_history"] = history
    if needs:
        data["replan_count"] = output.get("replan_count", 0)
    return data


def _progress_aggregate_done(
    output: dict[str, Any], security: SecurityConfig
) -> dict[str, Any] | None:
    """`result_aggregator`·`finalize` 진행 요약 — 응답 통합 완료."""
    return {"status": "응답 통합 완료"}


def _progress_deep_agent(
    output: dict[str, Any], security: SecurityConfig
) -> dict[str, Any] | None:
    """`deep_agent` 진행 요약 — plans/89 T1: 1단(부가 경로) 노드.

    도구 단위 진행은 progress 이벤트가 나른다.
    """
    return {"status": "에이전트 실행 완료"}


# 노드명 → 진행 요약 함수 (plans/179 W1b — 옛 if/elif 사슬의 표 주도 dispatch).
# 노드명은 서로 겹치지 않아 조회 순서가 결과를 바꾸지 않는다. 표에 없는 노드는 None.
_NODE_PROGRESS_SUMMARIZERS: dict[
    str, Callable[[dict[str, Any], SecurityConfig], dict[str, Any] | None]
] = {
    "input_parser": _progress_input_parser,
    "schema_analyzer": _progress_schema_analyzer,
    "query_generator": _progress_query_generator,
    "query_validator": _progress_query_validator,
    "query_executor": _progress_query_executor,
    "result_organizer": _progress_result_organizer,
    "output_generator": _progress_answer_done,
    "error_response": _progress_error_response,
    "context_resolver": _progress_context_resolver,
    "field_mapper": _progress_field_mapper,
    "semantic_router": _progress_semantic_router,
    "general_inference": _progress_answer_done,
    "approval_gate": _progress_approval_gate,
    "intent_planner": _progress_intent_planner,
    "plan": _progress_intent_planner,
    "normalize": _progress_intent_planner,
    "agent_orchestrator": _progress_agent_orchestrator,
    "join": _progress_agent_orchestrator,
    "replanner": _progress_replanner,
    "replan": _progress_replanner,
    "result_aggregator": _progress_aggregate_done,
    "finalize": _progress_aggregate_done,
    "deep_agent": _progress_deep_agent,
}


def _extract_node_progress(
    node_name: str, output: dict[str, Any], *, security: SecurityConfig
) -> dict[str, Any] | None:
    """노드 완료 시 오른쪽 패널에 표시할 진행 데이터를 추출한다.

    ``security``는 미리보기 행을 화면 답·CSV와 같은 `DataMasker` 규칙으로 가리는 데 쓴다 —
    가리기 전 행을 브라우저로 보내지 않는다(D-249 원칙).
    노드별 요약은 `_NODE_PROGRESS_SUMMARIZERS` 표가 고른다.
    """
    try:
        summarizer = _NODE_PROGRESS_SUMMARIZERS.get(node_name)
        if summarizer is not None:
            return summarizer(output, security)
    except Exception as e:
        logger.debug(f"노드 진행 데이터 추출 실패 ({node_name}): {e}")
    return None
