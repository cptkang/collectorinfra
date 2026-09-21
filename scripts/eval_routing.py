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
    **예외 — 로컬 MLX 모드(D-240 부기)**: 워커·오케스트레이터 두 평면이 모두 `mlx` 이고 둘 다
    루프백이면 비과금이라 `RUN_E2E` 없이 돈다. 하나라도 아니면 종전대로 `RUN_E2E=1` 이 필요하다.

사용:
    RUN_E2E=1 .venv/bin/python scripts/eval_routing.py --out reports/routing_s1.json
    .venv/bin/python scripts/eval_routing.py --dry-run     # 호출 없이 골든셋·설정만 점검
    ROUTER_CAPABILITY_OWNERSHIP_ENABLED=true .venv/bin/python scripts/eval_routing.py --mock
        # 교차 체인(chain) 채점까지(목업 · plans/102 H-1)
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


def local_mlx_mode(cfg: Any = None) -> tuple[bool, str]:
    """두 평면이 모두 **로컬 MLX 루프백**인가 → (RUN_E2E 없이 돌아도 되는가, 사유). D-240 부기.

    과금 판정은 D-222 정본 `scripts.scenario.preflight.external_planes` 한 곳을 그대로 쓰고,
    그 위에 **더 좁게** 두 평면 `mlx` + 루프백을 요구한다 — 내부망 FabriX·vLLM 은 비과금이지만
    이 모드의 대상이 아니다(`RUN_E2E=1` 경로 그대로). 설정을 못 읽으면 열지 않는다.
    """
    from scripts.scenario.preflight import (
        _is_loopback,
        _mlx_planes,
        _plane_provider,
        external_planes,
    )

    try:
        if cfg is None:
            from src.config import load_config

            cfg = load_config()
        worker = _plane_provider(cfg.llm)
        orchestrator = _plane_provider(cfg.orchestrator)
    except Exception as exc:
        return False, f"설정을 읽지 못했다({type(exc).__name__}) — 과금 게이트를 열지 않는다"
    planes = f"워커 {worker or '?'}, 오케스트레이터 {orchestrator or '?'}"
    if external_planes(worker, orchestrator):
        return False, f"과금 평면이 있다({planes})"
    if (worker, orchestrator) != ("mlx", "mlx"):
        return False, f"두 평면이 모두 mlx 가 아니다({planes})"
    remote = [f"{plane} {url or '(base_url 미설정)'}" for plane, url, _ in _mlx_planes(cfg)
              if not _is_loopback(url)]
    if remote:
        return False, f"루프백이 아닌 MLX 평면이 있다: {', '.join(remote)}"
    return True, f"로컬 MLX 루프백({planes}) — 비과금"


def _require_optin() -> None:
    """D-127 하드 게이트. 옵트인 없이는 어떤 실 호출도 하지 않는다(로컬 MLX 모드만 예외)."""
    if os.getenv("RUN_E2E") == "1":
        return
    local, reason = local_mlx_mode()
    if local:
        print(f"[local-mlx] {reason} — RUN_E2E 없이 진행합니다(D-240)", file=sys.stderr)
        return
    print(
        "거부: 실 LLM 호출은 D-127 건별 사용자 승인 대상입니다.\n"
        f"  로컬 MLX 모드 아님: {reason}\n"
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
    from src.domain.entity_key import KEY_FQDN, KEY_HOSTNAME, KEY_IPV4, KEY_IPV6
    from src.prompts.semantic_router import allowed_intents
    from src.routing.capability_ownership import known_capability_codes
    from src.routing.domain_config import DB_DOMAINS

    known_dbs = {d.db_id for d in DB_DOMAINS}
    known_intents = allowed_intents(fault_diagnosis_enabled=True)
    # 교차 시스템 판정 필드(plans/102 H-1) — 코드·키 타입의 정본은 레지스트리 카탈로그·
    # entity_key다(사본 금지)
    known_capabilities = known_capability_codes()
    known_key_types = {KEY_HOSTNAME, KEY_FQDN, KEY_IPV4, KEY_IPV6}
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
        for db in (exp.get("databases") or []) + (exp.get("forbid_databases") or []):
            if db not in known_dbs:
                errs.append(f"{iid}: 알 수 없는 db_id {db!r}")
        if "chain" in exp:
            chain = exp.get("chain")
            if not isinstance(chain, list):
                errs.append(f"{iid}: expect.chain은 답변 영역 코드 목록이어야 한다")
            else:
                for code in chain:
                    if code not in known_capabilities:
                        errs.append(f"{iid}: 알 수 없는 답변 영역 {code!r}(expect.chain)")
        if "key_type" in exp and exp.get("key_type") not in known_key_types:
            errs.append(f"{iid}: 알 수 없는 key_type {exp.get('key_type')!r}")
        if "probe" in exp and not isinstance(exp.get("probe"), bool):
            errs.append(f"{iid}: expect.probe는 true/false")
        if not it.get("query"):
            errs.append(f"{iid}: query 없음")
    return errs


#: 라우터 출력으로 채점할 수 없는 판정 필드(plans/102 H-1) — 값 유효성만 검증하고 결과에 표기한다.
#: `key_type`(값 기반 키 판정)·`probe`(식별자 소재 프로브 발동)는 라우터 뒤 단계의 산출물이라
#: `_llm_classify` 출력에 없다. 여기서 통과로 세면 거짓 통과다 — 채점은 시나리오 하네스(H-2) 소관.
_ROUTER_UNSCORED_FIELDS = ("key_type", "probe")
_ROUTER_UNSCORED_REASON = "라우터 단계 채점 불가(H-2 소관)"
_CHAIN_UNSCORED_REASON = (
    "라우터 출력에 chain 없음 — ROUTER_CAPABILITY_OWNERSHIP_ENABLED off(채점 불가)"
)


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
    # 답변 영역이 겹치는 DB의 오선택(plans/95 T5) — 리콜로는 잡히지 않는다
    # (기대 DB와 함께 골라도 통과하므로)
    forbidden = sorted(set(exp.get("forbid_databases") or []) & set(got_ids))
    # 교차 체인 순서(plans/102 H-1 · D8) — 라우터 `chain`은 소유 플래그 on일 때만 출력된다.
    # 출력에 키가 없으면 **채점하지 않는다**(통과로 세지 않고 결과·요약에 채점 불가로 남긴다).
    chain_scored = "chain" in exp and "chain" in got
    chain_ok: bool | None = (
        list(got.get("chain") or []) == list(exp.get("chain") or []) if chain_scored else None
    )

    result = {
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
        "db_forbidden": forbidden,
        "scores": [d.get("relevance_score") for d in got.get("databases", [])],
        "dropped": got.get("dropped") or [],
        "passed": (
            intent_ok and recall_ok and multi_ok and not forbidden and chain_ok is not False
        ),
    }
    if "chain" in exp:
        result["chain_expected"] = list(exp.get("chain") or [])
        result["chain_got"] = list(got.get("chain") or []) if chain_scored else None
        result["chain_scored"] = chain_scored
        result["chain_match"] = chain_ok
        if not chain_scored:
            result["chain_unscored_reason"] = _CHAIN_UNSCORED_REASON
    unscored = {k: exp[k] for k in _ROUTER_UNSCORED_FIELDS if k in exp}
    if unscored:
        result["router_unscored"] = {**unscored, "reason": _ROUTER_UNSCORED_REASON}
    return result


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


# ──────────────────────────────────────────────
# 분해(의도 계획) 골든셋 — D-203 · plans/88 §4.6
# ──────────────────────────────────────────────

_DECOMP_GOLD = _GOLD.parent / "decomposition.yaml"


def validate_decomposition_gold(items: list[dict]) -> list[str]:
    """분해 골든셋 정합성 — 실행 전에 잡는다."""
    from src.orchestration.schemas import allowed_agents

    known = allowed_agents()
    errs: list[str] = []
    seen: set[str] = set()
    for it in items:
        iid = it.get("id", "?")
        if iid in seen:
            errs.append(f"{iid}: 중복 id")
        seen.add(iid)
        exp = it.get("expect") or {}
        if not it.get("query"):
            errs.append(f"{iid}: query 없음")
        if int(exp.get("min_tasks", 0)) < 1:
            errs.append(f"{iid}: min_tasks는 1 이상")
        for a in exp.get("agents") or []:
            if a not in known:
                errs.append(f"{iid}: 알 수 없는 agent {a!r}")
        for edge in exp.get("edges") or []:
            if not (isinstance(edge, list) and len(edge) == 2):
                errs.append(f"{iid}: edge는 [from_agent, to_agent] 쌍")
    return errs


def judge_decomposition(item: dict, plan: dict) -> dict:
    """한 건의 분해 판정 — task 수 · input_from 엣지 · agent 집합."""
    exp = item.get("expect") or {}
    tasks = [t for t in (plan.get("tasks") or []) if isinstance(t, dict)]
    by_id = {t.get("task_id"): t for t in tasks}
    min_tasks = int(exp.get("min_tasks", 1))
    got_agents = [t.get("agent") for t in tasks]

    count_ok = len(tasks) >= min_tasks
    single_ok = len(tasks) == 1 if min_tasks == 1 else True
    agents_ok = all(a in got_agents for a in (exp.get("agents") or []))
    edge_results: list[bool] = []
    for a_from, a_to in (exp.get("edges") or []):
        hit = False
        for t in tasks:
            if t.get("agent") != a_to:
                continue
            for src in t.get("input_from") or []:
                if by_id.get(src, {}).get("agent") == a_from:
                    hit = True
        edge_results.append(hit)
    edge_ok = all(edge_results)
    return {
        "id": item.get("id"), "query": item.get("query"), "critical": item.get("critical"),
        "task_count": len(tasks), "min_tasks": min_tasks, "task_count_ok": count_ok,
        "single_ok": single_ok, "agents_got": got_agents, "agents_ok": agents_ok,
        "edge_ok": edge_ok, "degraded": plan.get("degraded") or [],
        "passed": count_ok and single_ok and agents_ok and edge_ok,
    }


async def run_decomposition(items: list[dict], *, llm=None) -> list[dict]:
    from src.config import load_config
    from src.llm import create_llm
    import importlib

    ip = importlib.import_module("src.orchestration.intent_planner")
    cfg = load_config()
    if llm is None:
        llm = create_llm(cfg)
    results: list[dict] = []
    for it in items:
        try:
            plan = await ip._llm_decompose(llm, it["query"], cfg)
        except Exception as e:  # noqa: BLE001
            results.append({"id": it.get("id"), "query": it.get("query"),
                            "error": f"{type(e).__name__}: {e}", "passed": False})
            continue
        results.append(judge_decomposition(it, plan))
    return results


def summarize_decomposition(results: list[dict]) -> dict:
    seq = [r for r in results if r.get("critical") == "sequential"]
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r.get("passed")),
        "sequential_cases": len(seq),
        "sequential_preserved": sum(1 for r in seq if r.get("edge_ok")),
        "single_false_split": sum(1 for r in results if r.get("critical") == "single" and not r.get("single_ok")),
        "degraded": sum(1 for r in results if r.get("degraded")),
        "errors": sum(1 for r in results if r.get("error")),
    }


def summarize(results: list[dict]) -> dict:
    scores = [s for r in results for s in (r.get("scores") or []) if isinstance(s, (int, float))]
    dist = Counter(_band(float(s)) for s in scores)
    crit_multi = [r for r in results if r.get("critical") == "multi_db"]
    chain_cases = [r for r in results if "chain_expected" in r]
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
        # 교차 시스템(plans/102 H-1) — 채점 불가 건수를 숨기지 않는다(거짓 통과 금지).
        "chain_cases": len(chain_cases),
        "chain_scored": sum(1 for r in chain_cases if r.get("chain_scored")),
        "chain_matched": sum(1 for r in chain_cases if r.get("chain_match")),
        "router_unscored_cases": sum(1 for r in results if r.get("router_unscored")),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="라우팅 골든셋 평가 (S-1·S-2)")
    ap.add_argument("--dry-run", action="store_true",
                    help="실 호출 없이 골든셋·설정만 점검한다(게이트 무관)")
    ap.add_argument("--mock", metavar="FAULT", nargs="?", const="none", default=None,
                    help="FabriX KBGenAI 목업으로 실행한다(실 호출 0 · 과금 0 · 게이트 무관). "
                         "FAULT: none|collapse_multi|bad_intent|bad_score|malformed|error_status|"
                         "chain_reversed(소유 플래그 on에서만 의미)")
    ap.add_argument("--out", help="결과 JSON 저장 경로")
    ap.add_argument("--tolerate", type=int, default=0,
                    help="허용 실패 건수(LLM 비결정성 대비). 기본 0=엄격. "
                         "멀티 DB 축소와 호출 실패는 이 값과 무관하게 항상 회귀다.")
    ap.add_argument("--decomposition", action="store_true",
                    help="라우팅 대신 분해(의도 계획) 골든셋을 평가한다(D-203 · plans/88 W4). "
                         "판정: task 수 · input_from 엣지 · agent 집합 · 단일 오탐")
    args = ap.parse_args()

    if args.decomposition:
        return _main_decomposition(args)

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
    # 채점하지 못한 판정은 통과가 아니다 — 건수를 눈에 띄게 남긴다(plans/102 H-1).
    unscored_chain = summary["chain_cases"] - summary["chain_scored"]
    if unscored_chain:
        print(f"  ※ chain 판정 {unscored_chain}건 채점 불가 — {_CHAIN_UNSCORED_REASON}",
              file=sys.stderr)
    if summary["router_unscored_cases"]:
        print(f"  ※ key_type·probe 판정 {summary['router_unscored_cases']}건 — "
              f"{_ROUTER_UNSCORED_REASON}", file=sys.stderr)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps({"summary": summary, "results": results},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"저장: {args.out}")

    return _verdict(summary, tolerate=args.tolerate)


def _main_decomposition(args) -> int:
    """`--decomposition` 모드 — 게이트·목업 규약은 라우팅 모드와 동일."""
    items = load_gold(_DECOMP_GOLD)
    errs = validate_decomposition_gold(items)
    if errs:
        print("분해 골든셋 정합성 오류:", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"분해 골든셋 {len(items)}건 정합성 OK "
          f"(순차 {sum(1 for i in items if i.get('critical') == 'sequential')}건 포함)")
    if args.dry_run:
        from src.config import load_config
        cfg = load_config()
        print(f"[dry-run] 실 호출 없음. provider={cfg.llm.provider} "
              f"plan_dag_validation={cfg.composite.plan_dag_validation_enabled} "
              f"sequential_replan={cfg.composite.sequential_replan_enabled}")
        return 0
    if args.mock is not None:
        from tests.mocks.fabrix_kbgenai_mock import make_llm, mock_kbgenai

        print(f"[mock] FabriX KBGenAI 목업 · fault={args.mock} · 실 호출 0건")
        with mock_kbgenai(fault=args.mock):
            results = asyncio.run(run_decomposition(items, llm=make_llm()))
    else:
        _require_optin()
        results = asyncio.run(run_decomposition(items))
    summary = summarize_decomposition(results)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for r in results:
        if not r.get("passed"):
            print(f"  ✗ {r.get('id')}: {r.get('error') or r}", file=sys.stderr)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"저장: {args.out}")
    if summary["errors"]:
        return 1
    # 순차 케이스 배선 유실은 항용 오차(tolerate)와 무관하게 회귀다 — 이 골든셋의 존재 이유.
    if summary["sequential_preserved"] < summary["sequential_cases"]:
        return 1
    return 0 if (summary["total"] - summary["passed"]) <= args.tolerate else 1


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
