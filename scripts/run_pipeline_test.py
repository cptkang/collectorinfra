"""파이프라인 통합 테스트 스크립트.

실제 DB + 실제 LLM을 사용하여 에이전트 파이프라인의 전체 프로세스를 검증한다.

프로세스 흐름:
  사용자 질문 → input_parser → schema_analyzer → query_generator
  → query_validator → query_executor → result_organizer → output_generator

사용법:
  python scripts/run_pipeline_test.py                       # 전체 시나리오 실행
  python scripts/run_pipeline_test.py --query "서버 목록"    # 단일 질의 테스트
  python scripts/run_pipeline_test.py --step-by-step        # 단계별 상세 출력
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.config import load_config
from src.db.client import get_postgres_client
from src.graph import (
    route_after_execution,
    route_after_organization,
    route_after_validation,
)
from src.llm import create_llm
from src.nodes.input_parser import input_parser
from src.nodes.output_generator import output_generator
from src.nodes.query_executor import query_executor
from src.nodes.query_generator import query_generator
from src.nodes.query_validator import query_validator
from src.nodes.result_organizer import result_organizer
from src.nodes.schema_analyzer import _schema_cache, schema_analyzer
from src.prompts.input_parser import INPUT_PARSER_SYSTEM_PROMPT
from src.prompts.output_generator import OUTPUT_GENERATOR_SYSTEM_PROMPT
from src.prompts.query_generator import QUERY_GENERATOR_SYSTEM_TEMPLATE
from src.state import create_initial_state


# ──────────────────────────────────────────────
# 출력 헬퍼
# ──────────────────────────────────────────────

BOLD = "\033[1m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"


def _header(step: int, title: str) -> None:
    print(f"\n{CYAN}{'─' * 60}")
    print(f"  Step {step}. {title}")
    print(f"{'─' * 60}{RESET}")


def _ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def _fail(msg: str) -> None:
    print(f"  {RED}✗{RESET} {msg}")


def _info(msg: str) -> None:
    print(f"  {YELLOW}→{RESET} {msg}")


def _detail(key: str, value: str, indent: int = 4) -> None:
    pad = " " * indent
    print(f"{pad}{BOLD}{key}:{RESET} {value}")


def _prompt_box(title: str, content: str, max_lines: int = 30) -> None:
    """프롬프트 내용을 박스 형태로 출력한다."""
    print(f"\n    {YELLOW}┌── {title} ──{RESET}")
    lines = content.strip().split("\n")
    for line in lines[:max_lines]:
        print(f"    {YELLOW}│{RESET} {line}")
    if len(lines) > max_lines:
        print(f"    {YELLOW}│{RESET} ... ({len(lines) - max_lines}줄 생략)")
    print(f"    {YELLOW}└{'─' * 40}{RESET}")


# ──────────────────────────────────────────────
# 파이프라인 실행
# ──────────────────────────────────────────────


async def run_pipeline(user_query: str, step_by_step: bool = False) -> dict:
    """사용자 질문을 받아 전체 파이프라인을 순차 실행한다.

    Args:
        user_query: 사용자 자연어 질의
        step_by_step: True면 각 단계마다 상세 출력

    Returns:
        최종 AgentState
    """
    config = load_config()
    llm = create_llm(config)
    state = create_initial_state(user_query=user_query)

    total_start = time.time()

    print(f"\n{BOLD}{'=' * 60}")
    print(f"  질의: {user_query}")
    print(f"{'=' * 60}{RESET}")

    # ── Step 1: input_parser ──
    _header(1, "input_parser — 자연어 질의 파싱")
    if step_by_step:
        _prompt_box("System Prompt → LLM", INPUT_PARSER_SYSTEM_PROMPT)
        _prompt_box("User Message → LLM", user_query)
    t0 = time.time()
    result = await input_parser(state, llm=llm, app_config=config)
    state.update(result)
    elapsed = time.time() - t0

    if state.get("error_message"):
        _fail(f"파싱 에러: {state['error_message']}")
    else:
        targets = state["parsed_requirements"].get("query_targets", [])
        filters = state["parsed_requirements"].get("filter_conditions", [])
        _ok(f"파싱 완료 ({elapsed:.1f}s)")
        _detail("조회 대상", ", ".join(targets) if targets else "(전체)")
        _detail("필터 조건", json.dumps(filters, ensure_ascii=False) if filters else "(없음)")
        if step_by_step:
            _prompt_box("LLM 응답 (파싱 결과)", json.dumps(state["parsed_requirements"], ensure_ascii=False, indent=2))

    # ── Step 2: schema_analyzer ──
    _header(2, "schema_analyzer — DB 스키마 조회 및 관련 테이블 식별")
    if step_by_step:
        _info(f"query_targets={targets} 기반으로 DB 스키마를 조회합니다")
        _info(f"키워드 매핑: 서버→server, CPU→cpu, 메모리→memory, 디스크→disk, 네트워크→network")
    _schema_cache.invalidate()
    t0 = time.time()
    result = await schema_analyzer(state, llm=llm, app_config=config)
    state.update(result)
    elapsed = time.time() - t0

    if state.get("error_message"):
        _fail(f"스키마 조회 실패: {state['error_message']}")
        return state
    else:
        tables = state["relevant_tables"]
        _ok(f"관련 테이블 {len(tables)}개 식별 ({elapsed:.1f}s)")
        _detail("테이블", ", ".join(tables))
        if step_by_step:
            for tname in tables:
                tinfo = state["schema_info"]["tables"].get(tname, {})
                cols = [c["name"] for c in tinfo.get("columns", [])]
                _detail(f"  {tname}", f"[{', '.join(cols)}]", indent=6)
            rels = state["schema_info"].get("relationships", [])
            if rels:
                _detail("FK 관계", "")
                for rel in rels:
                    print(f"        {rel['from']} → {rel['to']}")

    # ── Step 3~4: query_generator → query_validator (재시도 루프) ──
    max_retries = config.query.max_retry_count

    for attempt in range(max_retries + 1):
        # Step 3: query_generator
        _header(3, f"query_generator — SQL 생성 (시도 {attempt + 1}/{max_retries + 1})")
        if step_by_step:
            # 실제 LLM에 전달되는 프롬프트를 재현
            from src.nodes.query_generator import _format_schema_for_prompt, _build_user_prompt
            schema_text = _format_schema_for_prompt(state["schema_info"])
            sys_prompt = QUERY_GENERATOR_SYSTEM_TEMPLATE.format(
                schema=schema_text, default_limit=config.query.default_limit,
            )
            usr_prompt = _build_user_prompt(
                parsed_requirements=state["parsed_requirements"],
                template_structure=state.get("template_structure"),
                error_message=state.get("error_message") if state.get("error_message") else None,
                previous_sql=state.get("generated_sql") if state.get("error_message") else None,
            )
            _prompt_box("System Prompt → LLM (스키마 포함)", sys_prompt, max_lines=40)
            _prompt_box("User Prompt → LLM (요구사항 + 재시도 컨텍스트)", usr_prompt)
        t0 = time.time()
        result = await query_generator(state, llm=llm, app_config=config)
        state.update(result)
        elapsed = time.time() - t0

        sql = state["generated_sql"]
        _ok(f"SQL 생성 완료 ({elapsed:.1f}s)")
        _detail("SQL", "")
        for line in sql.strip().split("\n"):
            print(f"        {line}")

        # Step 4: query_validator
        _header(4, "query_validator — SQL 검증")
        if step_by_step:
            _info("검증 항목: 1)SQL파싱 2)SELECT전용 3)금지키워드 4)인젝션패턴 5)테이블존재 6)컬럼존재 7)LIMIT 8)성능위험")
        t0 = time.time()
        result = await query_validator(state, app_config=config)
        state.update(result)
        elapsed = time.time() - t0

        vr = state["validation_result"]
        if vr["passed"]:
            _ok(f"검증 통과 ({elapsed:.1f}s)")
            if vr.get("auto_fixed_sql"):
                _info("LIMIT 자동 추가됨")
                state["generated_sql"] = vr["auto_fixed_sql"]
            _detail("사유", vr["reason"])
            break
        else:
            _fail(f"검증 실패: {vr['reason']}")
            next_node = route_after_validation(state)
            if next_node == "error_response":
                _fail(f"최대 재시도 횟수({max_retries}) 초과 — 중단")
                state["final_response"] = f"SQL 검증 실패 (재시도 {max_retries}회 초과): {vr['reason']}"
                return state
            _info("query_generator로 회귀하여 SQL 재생성...")

    # ── Step 5: query_executor (재시도 루프) ──
    for attempt in range(max_retries + 1):
        _header(5, f"query_executor — SQL 실행 (시도 {attempt + 1}/{max_retries + 1})")
        t0 = time.time()
        result = await query_executor(state, app_config=config)
        state.update(result)
        elapsed = time.time() - t0

        if not state.get("error_message"):
            rows = state["query_results"]
            _ok(f"실행 성공: {len(rows)}건 ({elapsed:.1f}s)")
            if step_by_step and rows:
                _detail("상위 5건", "")
                for row in rows[:5]:
                    print(f"        {row}")
            break
        else:
            _fail(f"실행 에러: {state['error_message']}")
            next_node = route_after_execution(state)
            if next_node == "error_response":
                _fail(f"최대 재시도 횟수 초과 — 중단")
                state["final_response"] = f"SQL 실행 실패: {state['error_message']}"
                return state
            _info("query_generator로 회귀하여 SQL 재생성...")

            # 재생성 → 재검증 → 재실행
            result = await query_generator(state, llm=llm, app_config=config)
            state.update(result)
            result = await query_validator(state, app_config=config)
            state.update(result)
            if not state["validation_result"]["passed"]:
                _fail(f"재생성된 SQL도 검증 실패: {state['validation_result']['reason']}")
                continue

    # ── Step 6: result_organizer ──
    _header(6, "result_organizer — 결과 정리 및 포맷팅")
    t0 = time.time()
    result = await result_organizer(state, app_config=config)
    state.update(result)
    elapsed = time.time() - t0

    org = state["organized_data"]
    _ok(f"결과 정리 완료 ({elapsed:.1f}s)")
    _detail("요약", org["summary"])
    _detail("데이터 충분", "Yes" if org["is_sufficient"] else "No")

    if not org["is_sufficient"]:
        next_node = route_after_organization(state)
        if next_node == "query_generator":
            _info("데이터 부족 — 재시도 가능하지만 현재 테스트에서는 진행")

    if step_by_step and org["rows"]:
        _detail("정리된 데이터 (상위 5건)", "")
        for row in org["rows"][:5]:
            print(f"        {row}")

    # ── Step 7: output_generator ──
    _header(7, "output_generator — 자연어 응답 생성")
    if step_by_step:
        _prompt_box("System Prompt → LLM", OUTPUT_GENERATOR_SYSTEM_PROMPT)
        # User prompt 재현
        from src.nodes.output_generator import _build_response_prompt
        if org["rows"]:
            usr_prompt = _build_response_prompt(
                original_query=state["parsed_requirements"].get("original_query", ""),
                summary=org["summary"],
                rows=org["rows"],
                sql=state["generated_sql"],
            )
            _prompt_box("User Prompt → LLM (결과 데이터 포함)", usr_prompt, max_lines=40)
    t0 = time.time()
    result = await output_generator(state, llm=llm, app_config=config)
    state.update(result)
    elapsed = time.time() - t0

    _ok(f"응답 생성 완료 ({elapsed:.1f}s)")

    total_elapsed = time.time() - total_start

    # ── 최종 결과 ──
    print(f"\n{BOLD}{'=' * 60}")
    print(f"  최종 응답 (총 {total_elapsed:.1f}s)")
    print(f"{'=' * 60}{RESET}")
    print(f"\n{state['final_response']}\n")

    # 실행 이력 요약
    attempts = state.get("query_attempts", [])
    if attempts:
        print(f"{CYAN}── 실행 이력 ──{RESET}")
        for i, att in enumerate(attempts, 1):
            status = f"{GREEN}성공{RESET}" if att["success"] else f"{RED}실패{RESET}"
            print(f"  {i}. [{status}] {att['row_count']}건, {att['execution_time_ms']:.0f}ms")
            if att.get("error"):
                print(f"     에러: {att['error']}")

    return state


# ──────────────────────────────────────────────
# 시나리오 테스트
# ──────────────────────────────────────────────

# 테스트할 자연어 질의 목록
TEST_SCENARIOS = [
    "전체 서버 목록을 보여줘",
    "CPU 사용률이 70% 이상인 서버를 알려줘",
    "메모리 사용률이 80% 이상인 서버 목록",
    "디스크 사용률이 가장 높은 서버 10개",
    "네트워크 트래픽이 가장 많은 서버 Top 5",
]


async def run_all_scenarios(step_by_step: bool = False) -> None:
    """전체 테스트 시나리오를 실행한다."""
    config = load_config()
    print(f"{BOLD}파이프라인 통합 테스트{RESET}")
    print(f"  LLM: {config.llm.provider} / {config.llm.model}")
    print(f"  DB: {config.db_backend} / {config.db_connection_string[:50]}...")
    print()

    # DB 연결 사전 확인
    print(f"{CYAN}DB 연결 확인...{RESET}")
    try:
        async with get_postgres_client(config) as client:
            schema = await client.get_full_schema()
            table_names = list(schema.tables.keys())
            _ok(f"DB 연결 성공 — {len(table_names)}개 테이블: {', '.join(table_names)}")
    except Exception as e:
        _fail(f"DB 연결 실패: {e}")
        sys.exit(1)

    results: list[tuple[str, bool, float]] = []

    for i, query in enumerate(TEST_SCENARIOS, 1):
        print(f"\n\n{'#' * 60}")
        print(f"  시나리오 {i}/{len(TEST_SCENARIOS)}")
        print(f"{'#' * 60}")

        t0 = time.time()
        try:
            state = await run_pipeline(query, step_by_step=step_by_step)
            success = bool(state.get("final_response")) and "실패" not in state.get("final_response", "")
            elapsed = time.time() - t0
            results.append((query, success, elapsed))
        except Exception as e:
            elapsed = time.time() - t0
            _fail(f"예외 발생: {e}")
            results.append((query, False, elapsed))

    # ── 결과 요약 ──
    print(f"\n\n{BOLD}{'=' * 60}")
    print(f"  테스트 결과 요약")
    print(f"{'=' * 60}{RESET}\n")

    passed = sum(1 for _, s, _ in results if s)
    total = len(results)

    for query, success, elapsed in results:
        status = f"{GREEN}PASS{RESET}" if success else f"{RED}FAIL{RESET}"
        print(f"  [{status}] {query} ({elapsed:.1f}s)")

    print(f"\n  {BOLD}결과: {passed}/{total} 통과{RESET}")

    if passed == total:
        print(f"\n  {GREEN}모든 시나리오 통과!{RESET}")
    else:
        print(f"\n  {RED}{total - passed}개 시나리오 실패{RESET}")
        sys.exit(1)


# ──────────────────────────────────────────────
# 진입점
# ──────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="에이전트 파이프라인 통합 테스트",
    )
    parser.add_argument(
        "--query", "-q",
        type=str,
        help="단일 질의 테스트 (예: '서버 목록')",
    )
    parser.add_argument(
        "--step-by-step", "-s",
        action="store_true",
        help="각 단계마다 상세 출력",
    )
    args = parser.parse_args()

    if args.query:
        asyncio.run(run_pipeline(args.query, step_by_step=args.step_by_step))
    else:
        asyncio.run(run_all_scenarios(step_by_step=args.step_by_step))


if __name__ == "__main__":
    main()
