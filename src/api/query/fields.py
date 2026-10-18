"""응답 칸 헬퍼 — 노트·고지·계획 요약·시간 해석·역질문·부분 결과 칸.

plans/179 W2 — `src/api/routes/query.py`에서 순수 이동(본문·주석·평가 순서 불변).
옛 경로 `src.api.routes.query`가 같은 이름으로 재노출한다(D-337 ① shim).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from langchain_core.messages import HumanMessage

from src.domain.partial_result import PARTIAL_STATUS, PartialAnswer, render_partial_text
from src.pipeline.turn.zone import build_scope_reexpand


def _count_human_messages(messages: list[Any]) -> int:
    """메시지 목록에서 HumanMessage 수를 반환한다."""
    return len([m for m in messages if isinstance(m, HumanMessage)])


def _dependency_notes_field(state: dict[str, Any]) -> dict[str, Any]:
    """순차 처리 경과 노트를 스트림 응답에 싣는 필드 — 노트가 없으면 키를 싣지 않는다(바이트 불변).

    비스트림 두 경로와 스트림 폴백(`ainvoke`)의 응답 조립에만 있던 키를 스트림 `done`과 astream
    `response_data`에도 싣는다(plans/121 TP-11.8 · 4경로 대칭 D-205·D-066) — 측정 정본인 스트림
    하네스가 게이트·브리지 경과를 구조로 받는다. astream 입력은 종료 노드 델타가 아니라 누적 상태다.
    """
    notes = state.get("dependency_notes")
    return {"dependency_notes": notes} if notes else {}


def _disclosures_field(state: dict[str, Any]) -> dict[str, Any]:
    """결정적 고지의 구조화본(plans/123 W-8) — 네 진입점 응답·`done`에 같은 모양으로 싣는다.

    고지가 없으면 키를 싣지 않는다(바이트 불변). astream 입력은 누적 상태(`_scope_state`)다.
    """
    items = state.get("disclosures")
    return {"disclosures": items} if items else {}


def _apm_source_fields(state: Mapping[str, Any]) -> dict[str, Any]:
    """제니퍼 소스 되묻기·승계(plans/147) — 네 응답 지점(JSON · 스트림 응답·done · 폴백 응답·done)에
    같은 모양으로 싣는다. 값이 없으면 키를 싣지 않는다(소스 <2 배포 스트림 바이트 불변).

    스트림 입력은 누적 상태(`_scope_state`)다 — 승계 값은 체크포인트에서, 되묻기는 집계기 델타에서
    온다. 승계 값의 `last_targets`(plans/157 W3 — 인스턴스 이름)는 다음 턴 처리기만 읽는 내부
    승계 재료라 빼고 싣는다(체크포인트 값은 그대로).
    """
    out: dict[str, Any] = {}
    for key in ("apm_source_clarification", "apm_source_scope"):
        value = state.get(key)
        if value and key == "apm_source_scope" and isinstance(value, dict):
            value = {k: v for k, v in value.items() if k != "last_targets"}
        if value:
            out[key] = value
    return out


def _time_resolution_field(state: Mapping[str, Any]) -> dict[str, Any]:
    """요청 시간 해석(plans/122 T-4 · D-309) — 네 진입점 응답·`done`에 같은 모양으로 싣는다.

    값은 state `time_resolution` 그대로(`QueryTime.to_state()` 모양 — 하네스가 실행 SQL과 대조할
    기준 `[start, end)`). 없으면(플래그 off · 해석 전 종료) 키를 싣지 않는다(바이트 불변).
    astream 입력은 누적 상태(`_scope_state`)다 — 해석은 `input_parser` 델타에만 있다.
    """
    value = state.get("time_resolution")
    return {"time_resolution": value} if isinstance(value, dict) else {}


#: 기간 되묻기 역질문 kind — 존 역질문(`zone_select`)과 같은 채널이되 선택지가 없다(문구만).
TIME_CLARIFY_KIND = "time_period"


def _time_clarification(state: Mapping[str, Any]) -> dict[str, Any] | None:
    """기간 해석 불가로 끝난 턴의 역질문 페이로드 — 아니면 None (plans/122 T-3·T-4 · D-309).

    `input_parser`는 해석 불가(`time_resolution.clarify`)면 그래프를 끝내고 `final_response`에
    되묻기 문구를 싣는다. 라우트는 이 턴을 존 역질문과 같은 채널(status="clarification" +
    `clarification`)로 낸다 — 스트림 `done`에는 status 키가 없어 하네스·화면이 `clarification`
    키로 역질문을 가린다. 선택지가 없어(`options=[]`) 화면은 체크박스 없이 문구만 보인다.
    """
    value = state.get("time_resolution")
    if not isinstance(value, dict) or not value.get("clarify"):
        return None
    return {
        "kind": TIME_CLARIFY_KIND,
        "question": str(state.get("final_response") or ""),
        "options": [],
        "original_query": str(state.get("user_query") or ""),
        "reason": str(value["clarify"]),
    }


def _clarify_turn_fields(clarification: Mapping[str, Any] | None) -> dict[str, Any]:
    """기간 되묻기 턴 응답의 결과 칸 — 조회하지 않은 턴이다(plans/122 T-4 · D5).

    후속 턴 입력(`create_followup_input`)은 지시어 승계용으로 `generated_sql`·`query_results`·
    `output_file`을 비우지 않는다. 그래프가 `input_parser`에서 끝나면 `ainvoke` 결과에 직전 턴
    값이 그대로 남아, 되묻기 응답에 직전 턴 SQL·행 수·파일이 실렸다. 되묻기 턴이면 그 칸을
    비운 값으로 덮는다(응답 조립본 끝에 펼친다). 아니면 빈 dict(종전 그대로).
    """
    if not clarification:
        return {}
    return {
        "executed_sql": None, "row_count": 0, "has_file": False, "file_name": None,
        "has_mapping_report": False,
    }


def _clarify_store_fields(clarification: Mapping[str, Any] | None) -> dict[str, Any]:
    """기간 되묻기 턴의 결과 저장 칸(D5) — 직전 턴 행·파일을 `/query/{id}/…`로 내주지 않는다."""
    if not clarification:
        return {}
    return {"query_results": [], "output_file": None, "mapping_report_md": None}


def _scope_reexpand_field(state: dict[str, Any]) -> dict[str, Any]:
    """범위 재확장 패널을 스트림 `done`에도 싣는다(plans/123 W-2 ② — 비스트림과 대칭).

    응답 조립본(`response_data`)에 이미 있으면 그것을, 없으면 누적 상태의 좁힌 범위 기록으로
    만든다. 좁히지 않은 턴은 키를 싣지 않는다(바이트 불변).
    """
    panel = state.get("scope_reexpand") or build_scope_reexpand(
        state.get("scope_narrowed"), state.get("user_query", "")
    )
    return {"scope_reexpand": panel} if panel else {}


def _nonsql_task_summary(task: dict[str, Any], res: dict[str, Any]) -> dict[str, Any]:
    """비SQL 처리기 task 의 계획 요약 칸(plans/125 M-4) — 코드·개수만 · 없으면 키를 싣지 않는다.

    보기(`views`) · 삽입한 단계(첫 홉 보기 id · 변환 간선 id) · 연결 장부 패싯별 건수 · 소스
    상태 코드. SQL 처리기 task 는 이 칸이 없다(바이트 불변).
    """
    out: dict[str, Any] = {}
    if task.get("views"):
        out["views"] = [str(v) for v in task["views"]]
    meta = res.get("apm_query") if isinstance(res.get("apm_query"), dict) else None
    if meta:
        steps = [s.get("edge") or s.get("view") for s in meta.get("inserted_steps") or []
                 if isinstance(s, dict)]
        if steps:
            out["inserted_steps"] = steps
        if meta.get("link_summary"):
            out["link"] = meta["link_summary"]
    # 시스템 행만 — 제니퍼 소스별 행(plans/147 §4.5 · `source_id` 있음)은 요약 코드에 싣지 않는다
    statuses = [s for s in res.get("source_status") or []
                if isinstance(s, dict) and "source_id" not in s]
    if statuses:
        out["source_status"] = [f"{s.get('system')}:{s.get('status')}" for s in statuses]
    return out


def _plan_summary_field(state: dict[str, Any]) -> dict[str, Any]:
    """2단 계획 요약(plans/121 TP-0.1) — 계획이 없는 단(1·3단)은 키를 싣지 않는다(바이트 불변).

    **개수·코드만** 싣는다(값·문장 없음 · D-219 반출): 계획 경로 코드 · task별 담당·상태·간선·
    조회 DB · 재계획 횟수 · 닫힌 어휘 폴백 건수(TP-1.7) · 경과 노트 종류별 건수 · 키 브리지
    등급 건수 · 분해 폴백 원인 코드(TP-1.6 — 폴백한 턴만). 상태는 이번 턴 값이다 — 후속 턴 입력이
    계획 키를 비운다(TP-1.1).
    """
    from src.orchestration.intent_planner import DECOMPOSE_FALLBACK_KEY, PLAN_PATH_KEY
    from src.orchestration.schemas import AGENT_FALLBACK_KEY

    path = state.get(PLAN_PATH_KEY)
    plan = [t for t in (state.get("task_plan") or []) if isinstance(t, dict)]
    if not path or not plan:
        return {}
    results = state.get("task_results") or {}
    notes = list(state.get("dependency_notes") or [])
    tasks = []
    for task in sorted(plan, key=lambda t: t.get("order", 0)):
        res = results.get(task.get("task_id")) if isinstance(results, dict) else None
        res = res if isinstance(res, dict) else {}
        notes.extend(res.get("dependency_notes") or [])
        tasks.append({
            "id": task.get("task_id"),
            "agent": task.get("agent"),
            "status": task.get("status"),
            "depends_on": list(task.get("depends_on") or []),
            "input_from": list(task.get("input_from") or []),
            "db_ids": list(res.get("target_db_ids") or []),
            "db_origin": res.get("db_origin"),
            **_nonsql_task_summary(task, res),
        })
    seen: set[tuple[Any, ...]] = set()
    kinds: dict[str, int] = {}
    bridge: list[dict[str, Any]] = []
    for note in notes:
        if not isinstance(note, dict):
            continue
        key = (note.get("kind"), note.get("task_id"), note.get("detail"))
        if key in seen:
            continue
        seen.add(key)
        kind = str(note.get("kind"))
        kinds[kind] = kinds.get(kind, 0) + 1
        if kind == "bridge" and isinstance(note.get("counts"), dict):
            bridge.append({"task_id": note.get("task_id"), **note["counts"]})
    summary: dict[str, Any] = {
        "plan_path": path,
        "task_count": len(plan),
        "tasks": tasks,
        "replan_count": int(state.get("replan_count") or 0),
        "agent_fallback": sum(1 for t in plan if t.get(AGENT_FALLBACK_KEY)),
        "note_kinds": kinds,
    }
    if bridge:
        summary["bridge"] = bridge
    decompose_fallback = state.get(DECOMPOSE_FALLBACK_KEY)
    if decompose_fallback:
        summary["decompose_fallback"] = str(decompose_fallback)
    return {"plan_summary": summary}


def _plan_summary_carry(response_data: dict[str, Any]) -> dict[str, Any]:
    """응답 본문에 실린 계획 요약을 `done` 이벤트로 옮긴다 — 없으면 키 없음(TP-0.1)."""
    summary = response_data.get("plan_summary")
    return {"plan_summary": summary} if summary else {}


def _pre_gate_plan_summary() -> dict[str, Any]:
    """그래프 밖 사전 게이트(존 역질문 등)로 끝난 턴의 계획 요약 — 2단 확정일 때만(TP-0.1)."""
    from src.observability.ladder import current_ladder

    if (current_ladder() or {}).get("tier") != "intent_orchestration":
        return {}
    return {"plan_summary": {"plan_path": "pre_gate", "task_count": 0}}


def _executed_sql(state: dict[str, Any]) -> str | None:
    """「실행된 SQL 보기」에 보일 SQL 문자열 (plans/116 §10.3).

    출처 우선순위: ①top-level `generated_sql`(3단 단일 DB) ②`task_results`의 작업별
    `generated_sql`(2단 — top-level 은 빈 문자열이다) ③`db_executed_sqls`(멀티 DB).
    프론트(app.js)는 문자열 하나를 `<pre>`로 그리므로 여러 건은 머리 주석을 달아 잇는다.
    """
    sql = state.get("generated_sql")
    if sql:
        return sql  # type: ignore[no-any-return]
    parts: list[tuple[str, str]] = []
    results = state.get("task_results")
    if isinstance(results, dict) and results:
        plan = state.get("task_plan") or []
        order = [t.get("task_id") for t in sorted(plan, key=lambda t: t.get("order", 0))]
        order += [tid for tid in results if tid not in order]
        for tid in order:
            res = results.get(tid)
            if isinstance(res, dict) and res.get("generated_sql"):
                parts.append((str(tid), res["generated_sql"]))
    if not parts:
        db_sqls = state.get("db_executed_sqls")
        if isinstance(db_sqls, dict):
            parts = [(str(k), v) for k, v in db_sqls.items() if v]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0][1]
    return "\n\n".join(f"-- [{label}]\n{body}" for label, body in parts)


def _rewrite_trace_fields(thread_id: str | None) -> dict[str, Any]:
    """완료 ``done`` 이벤트에 붙일 재작성 감사(plans/107 §4.9 → plans/94 §19 O-e).

    이번 턴에 SQL 생성 노드들이 남긴 레코드를 꺼낸다(1·2단 격리 파이프라인 것도 포함).
    레코드가 없으면(기능 꺼짐 포함) **키 자체를 싣지 않는다** — done 페이로드 바이트 불변.
    """
    from src.nodes.intent_frame_builder import pop_rewrite_traces

    traces = pop_rewrite_traces(thread_id)
    return {"rewrite_trace": traces} if traces else {}


#: 처리 상한 부분 결과 `done`을 정상 `done`과 같은 키 집합으로 맞추는 빈 값(plans/163 D6) — 값은
#: 비스트림 부분 결과 응답(`QueryResponse` 기본값)과 같다. 화면은 이 키들을 참·거짓으로만 읽어
#: (`if (!ctx) return` 등) 없는 키와 null이 같게 동작한다.
_PARTIAL_DONE_DEFAULTS: dict[str, Any] = {
    "executed_sql": None, "has_file": False, "file_name": None, "awaiting_approval": False,
    "turn_count": None, "has_mapping_report": False, "form_fill_clarification": None,
    "form_memory_panel": None, "db_scope": None, "clarification": None, "source_switch": None,
}


def _partial_response_data(
    answer: PartialAnswer,
    *,
    query_id: str,
    thread_id: str | None,
    start_time: float,
    limit_sec: float,
) -> dict[str, Any]:
    """부분 결과 응답 본문. 비스트림 반환과 스트림 `done` 이 **같은 값**을 쓴다(D-066)."""
    return {
        "query_id": query_id,
        "status": PARTIAL_STATUS,
        "response": render_partial_text(answer, limit_sec=limit_sec),
        "thread_id": thread_id,
        "row_count": len(answer.rows),
        "processing_time_ms": (time.time() - start_time) * 1000,
    }
