"""실행기반 후보 선택 (Plan 61 트랙 A / E4 / D-074) — 핵심.

선택 파이프라인(D-035 결정적 우선):
  ① 규칙 사전필터 — 경로별 validator를 후보에 적용(통과분만 잔류).
  ② 읽기전용 실행 — 잔류 후보를 LIMIT·타임아웃 하에 실행(D-003 부작용 없음).
  ③ 결과 일관성 투표 — 결과집합이 최다 일치하는 후보 선택(execution-based self-consistency).
  ④ 동수/전패 폴백 — LLM 쌍대비교(hybrid/llm)로 최적안 판정. 전 후보 실행 실패 시
     `all_failed=True`로 호출부가 기존 에러기반 재시도 루프로 강등.

**경로 독립성(§2.1 / D-066 계열)**: validator·executor를 **주입**받아 경로 (A/B)단일 DB
(query_validator/query_executor)와 (C)멀티 DB(_validate_sql_simple/execute_sql) 비대칭을
원천 차단한다. 이 모듈은 db·config를 직접 import하지 않는다(순수 선택 로직).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# 결과 시그니처 계산 시 행 수 상한(투표 비용 통제 — 대량 결과도 앞부분으로 동치 판정).
_SIGNATURE_ROW_CAP = 200


def _round_val(v: Any, float_tol: int = 6) -> Any:
    if isinstance(v, float):
        return round(v, float_tol)
    return v


def _result_signature(rows: Optional[list[dict]]) -> str:
    """결과집합을 순서 무관·부동소수 안정 정규화하여 시그니처 문자열로 만든다.

    행을 (정렬된 항목 튜플)로 캐논화하고 정렬 후 상한만큼 취해 해시 가능한 문자열 생성.
    빈 결과([])와 None(미실행)은 서로 다른 시그니처(빈결과도 유효 답일 수 있어 구분).
    """
    if rows is None:
        return "\x00none"
    canon = []
    for row in rows[:_SIGNATURE_ROW_CAP]:
        if isinstance(row, dict):
            items = tuple(sorted((str(k), str(_round_val(v))) for k, v in row.items()))
        else:
            items = (("_", str(row)),)
        canon.append(items)
    canon.sort()
    return json.dumps(canon, ensure_ascii=False, sort_keys=True)


def _build_candidates_block(entries: list[dict]) -> str:
    """LLM 쌍대비교용 후보 텍스트 블록(번호·SQL·결과 샘플)."""
    lines: list[str] = []
    for e in entries:
        idx = e["index"]
        sql = e["sql"]
        res = e.get("result") or {}
        rows = res.get("rows")
        if res.get("error"):
            sample = f"실행 오류: {res['error']}"
        elif rows is None:
            sample = "실행 안 됨"
        else:
            sample = f"{len(rows)}행. 샘플: {json.dumps(rows[:3], ensure_ascii=False, default=str)}"
        lines.append(f"[후보 {idx}]\nSQL: {sql}\n결과: {sample}")
    return "\n\n".join(lines)


async def _llm_pairwise_choice(
    llm: Any,
    user_query: str,
    entries: list[dict],
    *,
    is_kbgenai: bool,
) -> Optional[int]:
    """LLM에게 후보 중 최적안 번호를 묻는다. 실패·파싱불가 시 None."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    from src.prompts.candidate_strategies import (
        SELECTOR_SYSTEM_TEMPLATE,
        SELECTOR_USER_TEMPLATE,
    )

    if llm is None:
        return None
    user = SELECTOR_USER_TEMPLATE.format(
        user_query=user_query, candidates_block=_build_candidates_block(entries)
    )
    messages = [
        SystemMessage(content=SELECTOR_SYSTEM_TEMPLATE),
        AIMessage(content="") if is_kbgenai else None,
        HumanMessage(content=user),
    ]
    messages = [m for m in messages if m is not None]
    try:
        response = await llm.ainvoke(messages)
    except Exception as e:  # noqa: BLE001 — 심판 실패는 결과일관성으로 강등
        logger.warning("후보 선택 LLM 판정 실패(결과일관성 폴백): %s", e)
        return None

    content = getattr(response, "content", "") or ""
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        choice = int(data.get("choice"))
    except (ValueError, TypeError):
        return None
    valid = {e["index"] for e in entries}
    return choice if choice in valid else None


def _tally(succeeded: list[dict]) -> tuple[str, list[dict], dict[str, int]]:
    """성공 후보를 결과 시그니처로 그룹화하고 최다 그룹을 반환한다.

    Returns:
        (winner_signature, winner_members, group_sizes)
    """
    groups: dict[str, list[dict]] = {}
    for e in succeeded:
        sig = e["signature"]
        groups.setdefault(sig, []).append(e)
    sizes = {sig: len(members) for sig, members in groups.items()}
    # 최다 그룹(동수면 사전 신뢰도 최고 멤버를 가진 그룹 → 안정적 tie-break)
    winner_sig = max(
        groups,
        key=lambda s: (len(groups[s]), max(m["confidence"] for m in groups[s])),
    )
    return winner_sig, groups[winner_sig], sizes


async def select_candidate(
    candidates: list[dict],
    *,
    validate: Callable[[str], Awaitable[Optional[str]]],
    execute: Callable[[str], Awaitable[dict]],
    selection: str = "hybrid",
    llm: Any = None,
    user_query: str = "",
    is_kbgenai: bool = False,
) -> dict:
    """후보 중 최적 SQL을 실행기반으로 선택한다.

    Args:
        candidates: [{"sql","strategy","confidence"}] (generate_candidates 결과).
        validate: SQL → 에러 문자열|None (경로별 validator 주입, async).
        execute: SQL → {"rows": list|None, "error": str|None} (읽기전용, 경로별 주입).
        selection: "consistency" | "llm" | "hybrid".
        llm/user_query/is_kbgenai: LLM 쌍대비교용(hybrid/llm).

    Returns:
        {"sql","strategy","confidence","all_failed","method","audit"}.
        all_failed=True면 전 후보 실행 실패 → 호출부가 에러 재시도로 강등.
    """
    audit: dict = {"n_candidates": len(candidates), "strategies": [c.get("strategy") for c in candidates]}

    if not candidates:
        return {"sql": "", "strategy": None, "confidence": 0.0,
                "all_failed": True, "method": "empty", "audit": audit}
    if len(candidates) == 1:
        c = candidates[0]
        return {"sql": c["sql"], "strategy": c.get("strategy"),
                "confidence": float(c.get("confidence", 1.0)), "all_failed": False,
                "method": "single", "audit": audit}

    # ① 규칙 사전필터
    filtered: list[dict] = []
    rule_report: list[dict] = []
    for c in candidates:
        err = None
        try:
            err = await validate(c["sql"])
        except Exception as e:  # noqa: BLE001 — validator 예외는 후보 탈락으로 간주
            err = f"validator 예외: {e}"
        rule_report.append({"strategy": c.get("strategy"), "rule_error": err})
        if err is None:
            filtered.append(c)
    audit["rule_filter"] = rule_report
    # 전부 규칙 탈락하면 필터하지 않고 전체로 실행(실행이 더 강한 판별자).
    for_exec = filtered or candidates
    audit["rule_passed"] = len(filtered)

    # ② 읽기전용 실행
    entries: list[dict] = []
    for i, c in enumerate(for_exec):
        try:
            res = await execute(c["sql"])
        except Exception as e:  # noqa: BLE001 — 실행 예외는 실패 후보로 기록
            res = {"rows": None, "error": str(e)}
        rows = res.get("rows")
        ok = res.get("error") is None and rows is not None
        entries.append({
            "index": i, "sql": c["sql"], "strategy": c.get("strategy"),
            "confidence": float(c.get("confidence", 1.0)),
            "result": res, "ok": ok,
            "signature": _result_signature(rows) if ok else None,
        })
    succeeded = [e for e in entries if e["ok"]]
    audit["executed"] = len(entries)
    audit["succeeded"] = len(succeeded)

    # 전 후보 실행 실패 → 에러 재시도 강등
    if not succeeded:
        first = entries[0]
        audit["exec_errors"] = [
            {"strategy": e["strategy"], "error": (e["result"] or {}).get("error")}
            for e in entries
        ]
        return {"sql": first["sql"], "strategy": first["strategy"], "confidence": 0.0,
                "all_failed": True, "method": "all_exec_failed", "audit": audit}

    # ③ 결과 일관성 투표
    winner_sig, members, sizes = _tally(succeeded)
    distinct_groups = len(sizes)
    consistency_conf = len(members) / len(succeeded)
    # 그룹 내 대표: 사전 신뢰도 최고(동수면 첫 항목)
    consistency_winner = max(members, key=lambda m: m["confidence"])
    audit["vote"] = {"distinct_groups": distinct_groups, "group_sizes": list(sizes.values()),
                     "winner_group_size": len(members)}

    if selection == "consistency" or distinct_groups == 1:
        # 만장일치이거나 순수 결과일관성 전략이면 투표 승자 확정.
        return {"sql": consistency_winner["sql"], "strategy": consistency_winner["strategy"],
                "confidence": consistency_conf, "all_failed": False,
                "method": "consistency", "audit": audit}

    # ④ LLM 쌍대비교(hybrid/llm) — 결과 그룹이 갈릴 때만 심판 투입(각 그룹 대표 1건).
    reps = [max(g, key=lambda m: m["confidence"])
            for g in _group_members(succeeded)]
    choice = await _llm_pairwise_choice(llm, user_query, reps, is_kbgenai=is_kbgenai)
    if choice is not None:
        picked = next(e for e in reps if e["index"] == choice)
        audit["llm_choice"] = choice
        # 신뢰도: LLM 판정이 결과일관성 승자와 일치하면 상향, 아니면 결과일관성값 유지.
        agree = picked["signature"] == winner_sig
        conf = max(consistency_conf, 0.75) if agree else consistency_conf
        return {"sql": picked["sql"], "strategy": picked["strategy"], "confidence": conf,
                "all_failed": False, "method": "llm_pairwise", "audit": audit}

    # LLM 실패 → 결과일관성 승자로 폴백
    audit["llm_choice"] = None
    return {"sql": consistency_winner["sql"], "strategy": consistency_winner["strategy"],
            "confidence": consistency_conf, "all_failed": False,
            "method": "consistency_fallback", "audit": audit}


def _group_members(succeeded: list[dict]) -> list[list[dict]]:
    """성공 후보를 결과 시그니처별 그룹 리스트로 반환한다."""
    groups: dict[str, list[dict]] = {}
    for e in succeeded:
        groups.setdefault(e["signature"], []).append(e)
    return list(groups.values())


async def run_candidate_pipeline(
    llm: Any,
    base_system_prompt: str,
    base_user_prompt: str,
    *,
    count: int,
    strategies: str,
    selection: str,
    is_kbgenai: bool,
    extract_sql: Callable[[str], str],
    validate: Callable[[str], Awaitable[Optional[str]]],
    execute: Callable[[str], Awaitable[dict]],
    user_query: str,
) -> dict:
    """다중 후보 생성(E2) + 실행기반 선택(E4)을 합성하는 공유 헬퍼.

    경로 (A/B)query_generator·(C)multi_db_executor가 각자의 prompt·validator·executor를
    주입해 동일 로직을 공유한다(§3 삽입 지점 원칙).

    Returns:
        select_candidate 결과 + "sql_candidates"(생성 후보 원본) 키. 후보가 하나도
        생성되지 않으면 sql="" · method="empty"(호출부가 기존 단일 경로로 폴백).
    """
    from src.nodes.candidate_generator import generate_candidates

    candidates = await generate_candidates(
        llm, base_system_prompt, base_user_prompt,
        count=count, strategies=strategies, is_kbgenai=is_kbgenai,
        extract_sql=extract_sql,
    )
    result = await select_candidate(
        candidates, validate=validate, execute=execute,
        selection=selection, llm=llm, user_query=user_query, is_kbgenai=is_kbgenai,
    )
    result["sql_candidates"] = candidates
    return result
