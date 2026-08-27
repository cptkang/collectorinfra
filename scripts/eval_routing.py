#!/usr/bin/env python
"""라우팅 골든셋 평가 하네스 (Plan 80 WU-05·WU-06 / S-1·S-2).

**무엇을 판정하나**
    S-1  트랙 A가 라우팅 회귀를 냈는가 — 특히 **멀티 DB 축소**(plans/79 §1.1 불변식 · §8 ⑩).
    S-2  `relevance_score` 분포 — A-1(규칙 5 제거)로 저신뢰 후보가 **실제로 출력되는지**,
         그래서 `MIN_RELEVANCE_SCORE=0.3` 게이트가 처음 실동작하는지.

**⚠ D-127 과금 게이트**
    실 LLM을 호출한다. `RUN_E2E=1` **없이는 실행되지 않는다**(아래 하드 게이트).
    키 존재만으로 실행되게 하지 않는다 — 키는 `.encenv`에 상존한다는 전제이기 때문이다.
    승인은 **실행 건마다** 받는다(포괄 승인 없음).

사용:
    RUN_E2E=1 .venv/bin/python scripts/eval_routing.py --out reports/routing_s1.json
    .venv/bin/python scripts/eval_routing.py --dry-run     # 호출 없이 골든셋·설정만 점검
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

_GOLD = _REPO_ROOT / "testdata" / "routing_gold" / "routing.yaml"

# 분포 대역 — 프롬프트 규칙 4의 신뢰도 3대역과 같은 경계를 쓴다.
_BANDS = [
    ("<0.3 (게이트 탈락)", 0.0, 0.3),
    ("0.3~0.5 (약함)", 0.3, 0.5),
    ("0.5~0.8 (가능)", 0.5, 0.8),
    ("0.8~1.0 (확실)", 0.8, 1.0001),
]


def _require_optin() -> None:
    """D-127 하드 게이트. 옵트인 없이는 어떤 실 호출도 하지 않는다."""
    if os.getenv("RUN_E2E") != "1":
        print(
            "거부: 실 LLM 호출은 D-127 건별 사용자 승인 대상입니다.\n"
            "  승인 후에만 RUN_E2E=1 을 설정해 재실행하세요.\n"
            "  (호출 없이 점검만 하려면 --dry-run)",
            file=sys.stderr,
        )
        raise SystemExit(2)


def load_gold(path: Path = _GOLD) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data["items"]


def validate_gold(items: list[dict]) -> list[str]:
    """골든셋 자체의 정합성 — 실행 전에 잡는다."""
    from src.routing.domain_config import DB_DOMAINS
    from src.prompts.semantic_router import allowed_intents

    known_dbs = {d.db_id for d in DB_DOMAINS}
    known_intents = allowed_intents(fault_diagnosis_enabled=True)
    errs: list[str] = []
    seen: set[str] = set()
    for it in items:
        iid = it.get("id", "?")
        if iid in seen:
            errs.append(f"{iid}: 중복 id")
        seen.add(iid)
        exp = it.get("expect") or {}
        if exp.get("intent") not in known_intents:
            errs.append(f"{iid}: 알 수 없는 intent {exp.get('intent')!r}")
        for db in exp.get("databases") or []:
            if db not in known_dbs:
                errs.append(f"{iid}: 알 수 없는 db_id {db!r}")
        if not it.get("query"):
            errs.append(f"{iid}: query 없음")
    return errs


def _band(score: float) -> str:
    for name, lo, hi in _BANDS:
        if lo <= score < hi:
            return name
    return "범위 밖"


def judge(item: dict, got: dict) -> dict:
    """한 건의 판정. 축소 감지가 핵심이다."""
    exp = item.get("expect") or {}
    got_ids = [d["db_id"] for d in got.get("databases", [])]
    exp_ids = set(exp.get("databases") or [])
    min_db = int(exp.get("min_databases", 0))

    intent_ok = got.get("intent") == exp.get("intent")
    recall_ok = exp_ids.issubset(set(got_ids)) if exp_ids else True
    multi_ok = len(got_ids) >= min_db

    return {
        "id": item.get("id"),
        "query": item.get("query"),
        "critical": item.get("critical"),
        "intent_expected": exp.get("intent"),
        "intent_got": got.get("intent"),
        "intent_match": intent_ok,
        "db_expected": sorted(exp_ids),
        "db_got": got_ids,
        "db_recall": recall_ok,
        "min_databases": min_db,
        "multi_preserved": multi_ok,
        "scores": [d.get("relevance_score") for d in got.get("databases", [])],
        "dropped": got.get("dropped") or [],
        "passed": intent_ok and recall_ok and multi_ok,
    }


async def run(items: list[dict], *, llm=None) -> list[dict]:
    from src.config import load_config
    from src.llm import create_llm
    from src.routing.domain_config import DB_DOMAINS
    import importlib

    sr = importlib.import_module("src.routing.semantic_router")
    if llm is None:
        cfg = load_config()
        llm = create_llm(cfg)
    results: list[dict] = []
    for it in items:
        try:
            got = await sr._llm_classify(llm, it["query"], DB_DOMAINS)
        except Exception as e:  # noqa: BLE001 — 개별 실패가 전체를 막지 않는다
            results.append({
                "id": it.get("id"), "query": it.get("query"),
                "error": f"{type(e).__name__}: {e}", "passed": False,
            })
            continue
        results.append(judge(it, got))
    return results


def summarize(results: list[dict]) -> dict:
    scores = [s for r in results for s in (r.get("scores") or []) if isinstance(s, (int, float))]
    dist = Counter(_band(float(s)) for s in scores)
    crit_multi = [r for r in results if r.get("critical") == "multi_db"]
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r.get("passed")),
        "intent_match": sum(1 for r in results if r.get("intent_match")),
        "multi_db_cases": len(crit_multi),
        "multi_db_preserved": sum(1 for r in crit_multi if r.get("multi_preserved")),
        "score_count": len(scores),
        "score_distribution": dict(dist),
        "below_gate": sum(1 for s in scores if float(s) < 0.3),
        "dropped_total": sum(len(r.get("dropped") or []) for r in results),
        "errors": sum(1 for r in results if r.get("error")),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="라우팅 골든셋 평가 (S-1·S-2)")
    ap.add_argument("--dry-run", action="store_true",
                    help="실 호출 없이 골든셋·설정만 점검한다(게이트 무관)")
    ap.add_argument("--mock", metavar="FAULT", nargs="?", const="none", default=None,
                    help="FabriX KBGenAI 목업으로 실행한다(실 호출 0 · 과금 0 · 게이트 무관). "
                         "FAULT: none|collapse_multi|bad_intent|bad_score|malformed|error_status")
    ap.add_argument("--out", help="결과 JSON 저장 경로")
    ap.add_argument("--tolerate", type=int, default=0,
                    help="허용 실패 건수(LLM 비결정성 대비). 기본 0=엄격. "
                         "멀티 DB 축소와 호출 실패는 이 값과 무관하게 항상 회귀다.")
    args = ap.parse_args()

    items = load_gold()
    errs = validate_gold(items)
    if errs:
        print("골든셋 정합성 오류:", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"골든셋 {len(items)}건 정합성 OK "
          f"(멀티 DB {sum(1 for i in items if i.get('critical') == 'multi_db')}건 포함)")

    if args.dry_run:
        from src.config import load_config
        cfg = load_config()
        print(f"[dry-run] 실 호출 없음. provider={cfg.llm.provider} "
              f"structured_backend={getattr(cfg, 'structured_output_backend', 'none')}")
        return 0

    if args.mock is not None:
        # 목업 경로 — 실 호출이 없으므로 D-127 게이트를 타지 않는다.
        # 실제 KBGenAIChat 클래스를 쓰고 HTTP 경계만 갈아끼운다.
        from tests.mocks.fabrix_kbgenai_mock import make_llm, mock_kbgenai

        print(f"[mock] FabriX KBGenAI 목업 · fault={args.mock} · 실 호출 0건")
        with mock_kbgenai(fault=args.mock):
            results = asyncio.run(run(items, llm=make_llm()))
    else:
        _require_optin()
        results = asyncio.run(run(items))
    summary = summarize(results)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    for r in results:
        if not r.get("passed"):
            print(f"  ✗ {r.get('id')}: {r.get('error') or r}", file=sys.stderr)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps({"summary": summary, "results": results},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"저장: {args.out}")

    return _verdict(summary, tolerate=args.tolerate)


def _verdict(summary: dict, *, tolerate: int = 0) -> int:
    """종료 판정.

    ⚠ **목업 결함 주입으로 잡은 실제 결함**(2026-08-27): 종전에는 멀티 DB 축소만 보고 종료 코드를
    정해서, ①의도 오분류(`bad_intent`)와 ②LLM 전면 실패(`error_status`)가 **exit 0으로 통과**했다.
    회귀 게이트가 거짓 통과를 내면 승인·과금을 쓰고도 아무것도 보장하지 못한다.
    판정은 **세 축을 모두** 본다.
    """
    fails: list[str] = []

    # ① 호출 실패 — 측정 자체가 성립하지 않는다. 가장 먼저 잡는다.
    if summary["errors"]:
        fails.append(f"LLM 호출 실패 {summary['errors']}건 — 측정 무효")

    # ② 멀티 DB 축소 — plans/79 §1.1 불변식 · §8 ⑩
    if summary["multi_db_preserved"] < summary["multi_db_cases"]:
        fails.append(
            f"멀티 DB 축소 {summary['multi_db_cases'] - summary['multi_db_preserved']}건 "
            "— 단일 선택으로 조용히 줄었다(불변식 위배)"
        )

    # ③ 케이스 실패 — 의도 오분류·DB 리콜 누락 포함
    missed = summary["total"] - summary["passed"]
    if missed > tolerate:
        fails.append(
            f"실패 {missed}건(허용 {tolerate}) — "
            f"intent 일치 {summary['intent_match']}/{summary['total']}"
        )

    if fails:
        print("회귀 판정:", file=sys.stderr)
        for f in fails:
            print(f"  - {f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
