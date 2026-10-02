"""문서 RAG 프로브 CLI — T-1 시험 표면 (plans/126 §4.17 · W3).

**라우팅 없이** 엔진을 직접 부른다. 서버·그래프·웹 UI 를 띄우지 않으므로 «실제로 되는가»를
가장 적은 부품으로 확인하는 지점이다(계획서: 동작하는 최소 = W3).

    python scripts/rag_probe.py --list
    python scripts/rag_probe.py -c hq_manual -q "계정 신청은 누가 승인하나요?" --search-only
    python scripts/rag_probe.py -c hq_manual -q "계정 신청 절차"
    python scripts/rag_probe.py -c hq_manual -c arch_docs -q "…" --raw --json

규율:
  - **컬렉션 미지정은 오류**다(§4.6) — "지정 안 하면 다 찾는다"는 편의는 암묵적 라우팅이다.
  - `--search-only` 는 LLM 을 부르지 않는다 → «검색이 되는가»와 «서술이 되는가»를 분리 판정.
  - 토큰은 출력하지 않는다(끝 4자만). `--raw` 도 헤더는 찍지 않는다.
  - 종료 코드가 `docs/32` §6 오류 판독표와 1:1이다:
        0 정상 · 2 결과 0건 · 3 자산 폐기 · 4 오류·타임아웃 · 5 비활성/설정 부족 · 6 사용법 오류
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.clients.fabrix_retrieval import (  # noqa: E402
    STATUS_BLOCKED_PII,
    STATUS_DISABLED,
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_STALE_ID,
    STATUS_TIMEOUT,
)
from src.config import load_config  # noqa: E402
from src.doc_qa.service import DocAnswer, answer_from_documents  # noqa: E402
from src.infrastructure.doc_sources import resolve_collections  # noqa: E402

#: status → 종료 코드 (docs/32 §6 판독표와 1:1)
EXIT_CODES: dict[str, int] = {
    STATUS_OK: 0,
    STATUS_EMPTY: 2,
    STATUS_STALE_ID: 3,
    STATUS_ERROR: 4,
    STATUS_TIMEOUT: 4,
    STATUS_BLOCKED_PII: 4,
    STATUS_DISABLED: 5,
}

#: status → 사람이 읽는 다음 행동(판독표 요약). 진단의 절반은 "그래서 뭘 하나"다.
NEXT_STEP: dict[str, str] = {
    STATUS_OK: "정상.",
    STATUS_EMPTY: "접속은 정상이고 검색 결과가 0건이다. 문서 용어·조항 번호를 그대로 넣어 "
                  "질문을 바꿔 보거나 다른 문서군을 지정한다(플랫폼 임계는 우리가 못 바꾼다).",
    STATUS_STALE_ID: "자산 ID가 폐기됐다 → `python scripts/rag_conn.py set <문서군>` 으로 "
                     "접속 4종을 새로 받아 넣는다(docs/32 §3).",
    STATUS_ERROR: "호출이 실패했다. 인증(토큰·클라이언트 키가 같은 발급 건인지)·URL 전문을 "
                  "확인한다(docs/32 §6-B·C).",
    STATUS_TIMEOUT: "응답이 없다. 재시도 후 반복되면 플랫폼 상태를 확인한다.",
    STATUS_BLOCKED_PII: "질의가 개인정보 필터에 걸렸다. 표현을 바꾼다.",
    STATUS_DISABLED: "기능이 꺼졌거나 접속 4종이 비었다 → `python scripts/rag_conn.py show` 로 "
                     "무엇이 비었는지 본다.",
}


def cmd_list(config) -> int:
    # resolve 는 1회만 — 두 번 부르면 비활성 WARNING 이 두 벌 찍힌다.
    cols = resolve_collections(config.rag)
    usable = [c for c in cols if c.usable]
    print(f"문서 검색 컬렉션 {len(usable)}/{len(cols)} 사용 가능"
          + (f" (RAG_ENABLED={'true' if getattr(config.rag, 'enabled', False) else 'false'})"))
    print()
    for col in cols:
        state = "사용 가능" if col.usable else "비활성"
        print(f"[{col.id}] {col.title} — {state}")
        if col.disabled_reason:
            print(f"    사유: {col.disabled_reason}")
        masked = col.masked_connection()
        print(f"    엔드포인트: {masked['endpoint'] or '(비어 있음)'}")
        print(f"    토큰/클라이언트 키: {masked['token'] or '(비어 있음)'} / "
              f"{masked['client_key'] or '(비어 있음)'}")
        print(f"    자산 ID: {masked['retrieval_id'] or '(비어 있음)'}"
              + (f"  (기준일 {col.asset_recorded_at})" if col.asset_recorded_at else ""))
        if col.platform_params_snapshot:
            snap = col.platform_params_snapshot
            print(f"    플랫폼 설정 기록: top_k={snap.get('top_k')} · "
                  f"rerank={snap.get('rerank')} · hyde={snap.get('hyde')} "
                  f"(기록일 {snap.get('recorded_at')} · 값은 플랫폼 소유)")
    return 0


def _print_human(result: DocAnswer, *, show_raw: bool) -> None:
    d = result.diagnostics
    print(f"[status] {result.status}" + (f" — {result.reason}" if result.reason else ""))
    print(f"[다음]   {NEXT_STEP.get(result.status, '')}")

    counts = d.get("counts") or {}
    if counts:
        print("\n[근거]")
        for i, c in enumerate(result.citations, start=1):
            head = f"  {i}. {c.collection_title} — {c.title}"
            if c.subtitle:
                head += f" `{c.subtitle}`"
            if c.truncated:
                head += "  (본문 일부만)"
            print(head)
        print(f"  건수: {counts} · 점수 {d.get('score_min')}~{d.get('score_max')} · "
              f"HyDE 발동 {'y' if d.get('hyde_applied') else 'n'}")
        if d.get("cached_collections"):
            print(f"  캐시 재사용: {d['cached_collections']}")
        if d.get("truncated_docs") or d.get("dropped_docs"):
            print(f"  절단 {d.get('truncated_docs', 0)}건 · 예산 제외 {d.get('dropped_docs', 0)}건")

    print("\n[답변]")
    print(result.answer)

    print(f"\n[진단] LLM 호출 {d.get('llm_calls', 0)}회 · 검색 {d.get('search_ms', '-')}ms"
          f" · 전체 {d.get('total_ms', '-')}ms · 상태별 {d.get('statuses', {})}")
    if show_raw and result.raw:
        print("\n[원시 응답]")
        print(json.dumps(result.raw, ensure_ascii=False, indent=2)[:8000])


async def _run_query(args, config) -> DocAnswer:
    llm = None
    if not args.search_only:
        from src.llm import create_llm
        llm = create_llm(config, purpose="answer")
    return await answer_from_documents(
        args.query,
        args.collection,
        llm=llm,
        app_config=config,
        include_raw=args.raw,
        search_only=args.search_only,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="문서 RAG 엔진을 라우팅 없이 직접 호출한다(T-1 시험 표면)",
        epilog="오류 판독표·교체 절차: docs/32_rag_retrieval_runbook.md",
    )
    p.add_argument("--list", action="store_true",
                   help="등록 문서군과 접속 정보 상태(마스킹)만 보여준다")
    p.add_argument("-c", "--collection", action="append", default=[],
                   help="조회할 문서군 id(여러 번 지정 가능). **미지정은 오류**")
    p.add_argument("-q", "--query", help="질의 문장(용어·조항 번호를 그대로 넣는 것이 유리하다)")
    p.add_argument("--search-only", action="store_true",
                   help="검색만 하고 서술하지 않는다(LLM 호출 0회)")
    p.add_argument("--raw", action="store_true", help="원시 응답을 함께 보여준다(진단용)")
    p.add_argument("--json", action="store_true", help="기계 판독용 JSON 출력")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config()

    if args.list:
        return cmd_list(config)
    if not args.query:
        print("`--query` 가 필요합니다(또는 `--list`).", file=sys.stderr)
        return 6
    if not args.collection:
        print("`--collection` 을 지정하세요 — 미지정 시 전체 검색을 하지 않습니다.", file=sys.stderr)
        print("  등록 목록은 `--list` 로 봅니다(라우팅은 이 기능 범위가 아닙니다).", file=sys.stderr)
        return 6

    result = asyncio.run(_run_query(args, config))

    if args.json:
        print(json.dumps({
            "status": result.status,
            "reason": result.reason,
            "answer": result.answer,
            "citations": [
                {"collection": c.collection_title, "title": c.title,
                 "subtitle": c.subtitle, "url": c.url, "doc_id": c.doc_id,
                 "truncated": c.truncated}
                for c in result.citations
            ],
            "diagnostics": result.diagnostics,
            "raw": result.raw if args.raw else None,
        }, ensure_ascii=False, indent=2))
    else:
        _print_human(result, show_raw=args.raw)

    return EXIT_CODES.get(result.status, 4)


if __name__ == "__main__":
    raise SystemExit(main())
