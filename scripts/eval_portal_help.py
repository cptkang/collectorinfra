#!/usr/bin/env python
"""포탈 도움말 라우팅 측정 M-2 (plans/142 §7 · W5).

골드 `testdata/routing_gold/portal_help.yaml`을 2단 `intent_planner` **노드 단위**로
돌린다 — 사전 처리 계층 A(③.8 사용법 단락 · ③.9 포탈 명령)와 분해 출구 G-5 되돌림
(`coerce_portal_redirect`)이 모두 들어간다. 노드 호출·상태 조립·분해 시도 관찰·과금 게이트는
`scripts/eval_routing.py`(`--decomposition --node`)의 함수를 그대로 쓴다. 그 모드에 얹지 않은
이유: 채점 모양이 다르다(양성 → `portal_help` 비율 · 음성 오탐률 — node/edge F1 아님) · 골드
검증이 `SUBAGENT_REGISTRY` 어휘만 받는다(`portal_help`는 조건부 처리기라 밖이다) · 교정 뒤
실패 문항만 다시 돌리는 `--ids`가 필요하다.

판정:
    양성  계획이 `portal_help` 단일 task면 통과 — 기준 ≥ 85%
    음성  계획에 `portal_help`가 하나라도 있으면 오탐 — 기준 ≤ 10%
    (참고) 음성의 종전 담당 일치(`expect.agents` ⊇ 계획 담당)는 오탐 판정과 무관하게 따로 센다.
2턴 항목(`prior`)은 `conversation_context`(turn_count 2 · 앞 턴 요약)를 주입한다.

⚠ 과금 게이트(D-127 · D-240): 두 평면이 로컬 MLX 루프백일 때만 `RUN_E2E` 없이 돈다
(`eval_routing._require_optin`). 먼저 `--dry-run`.

사용:
    ENABLE_DEEPAGENTS_PACKAGE=false .venv/bin/python scripts/eval_portal_help.py --dry-run
    ENABLE_DEEPAGENTS_PACKAGE=false .venv/bin/python scripts/eval_portal_help.py --out <경로>
    ... --ids pu-03,pu-14        # 실패 문항만 다시
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from scripts.eval_routing import (  # noqa: E402 — 경로 주입 뒤
    _observe_planner,
    _require_optin,
    build_node_state,
)

GOLD = _REPO_ROOT / "testdata" / "routing_gold" / "portal_help.yaml"
PORTAL_AGENT = "portal_help"
#: M-2 기준(계획서 §7).
POSITIVE_MIN = 0.85
NEGATIVE_FP_MAX = 0.10


def load_gold(path: Path = GOLD) -> list[dict[str, Any]]:
    """골드 항목을 읽는다."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))["items"]


def validate_gold(items: list[dict[str, Any]]) -> list[str]:
    """실행 전 정합성 — id 중복 · polarity · 양성 기대 절 · 음성 종전 담당."""
    errs: list[str] = []
    seen: set[str] = set()
    for it in items:
        iid = str(it.get("id") or "?")
        if iid in seen:
            errs.append(f"{iid}: 중복 id")
        seen.add(iid)
        if not it.get("query"):
            errs.append(f"{iid}: query 없음")
        polarity = it.get("polarity")
        exp = it.get("expect") or {}
        if polarity == "positive":
            if not exp.get("sections"):
                errs.append(f"{iid}: 양성에 expect.sections 없음")
            if it.get("role") not in ("user", "admin"):
                errs.append(f"{iid}: role은 user|admin")
        elif polarity == "negative":
            if not exp.get("agents"):
                errs.append(f"{iid}: 음성에 expect.agents(종전 담당) 없음")
        else:
            errs.append(f"{iid}: polarity는 positive|negative")
        prior = it.get("prior")
        if prior is not None and not (isinstance(prior, dict) and prior.get("summary")):
            errs.append(f"{iid}: prior는 query·summary 매핑")
    return errs


def node_item(item: dict[str, Any]) -> dict[str, Any]:
    """골드 항목 → `build_node_state` 입력(2턴이면 `conversation_context` 주입 · 골드 `role`은
    상태 `user_role`로 — 분해 출구 되돌림이 호출자 역할로 매칭한다)."""
    out: dict[str, Any] = {"query": item["query"]}
    state: dict[str, Any] = {}
    if item.get("role"):
        state["user_role"] = item["role"]
    prior = item.get("prior")
    if prior:
        state["conversation_context"] = {
            "turn_count": 2,
            "previous_results_summary": prior["summary"],
        }
    if state:
        out["state"] = state
    return out


def judge(item: dict[str, Any], out: dict[str, Any], attempts: list[str | None]) -> dict[str, Any]:
    """노드 출력 1건 판정."""
    tasks = [t for t in (out.get("task_plan") or []) if isinstance(t, dict)]
    agents = [str(t.get("agent")) for t in tasks]
    portal_single = agents == [PORTAL_AGENT]
    row: dict[str, Any] = {
        "id": item["id"], "polarity": item["polarity"], "query": item["query"],
        "agents": agents, "plan_path": out.get("plan_path"),
        "sub_queries": [t.get("sub_query") for t in tasks],
        "llm_attempts": len(attempts), "attempt_events": [a for a in attempts if a],
    }
    if item["polarity"] == "positive":
        row["passed"] = portal_single
    else:
        row["false_positive"] = PORTAL_AGENT in agents
        row["owner_match"] = bool(agents) and set(agents) <= set(item["expect"]["agents"])
        row["passed"] = not row["false_positive"]
    return row


async def run(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """노드를 문항마다 1회 부른다(순차 — 공유 MLX 서버 동시 요청 금지)."""
    from src.config import load_config
    from src.llm import create_llm

    ip = importlib.import_module("src.orchestration.intent_planner")
    cfg = load_config()
    llm = create_llm(cfg)
    rows: list[dict[str, Any]] = []
    for it in items:
        state = build_node_state(node_item(it))
        with _observe_planner(ip) as obs:
            try:
                out = await ip.intent_planner(state, llm=llm, app_config=cfg)
            except Exception as e:  # noqa: BLE001 — 개별 실패가 전체를 막지 않는다
                rows.append({"id": it["id"], "polarity": it["polarity"], "query": it["query"],
                             "error": f"{type(e).__name__}: {e}", "passed": False})
                continue
        rows.append(judge(it, out, obs.attempts))
        print(f"  {rows[-1]['id']}: {rows[-1].get('agents')} path={rows[-1].get('plan_path')}",
              file=sys.stderr)
    return rows


def _rate(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """M-2 요약 — 양성 → portal_help 비율 · 음성 오탐률 · 경로 분포."""
    pos = [r for r in rows if r["polarity"] == "positive"]
    neg = [r for r in rows if r["polarity"] == "negative"]
    pos_ok = sum(1 for r in pos if r.get("passed"))
    neg_fp = sum(1 for r in neg if r.get("false_positive"))
    paths: dict[str, int] = {}
    for r in pos:
        if r.get("passed"):
            key = str(r.get("plan_path") or "llm_decompose")
            paths[key] = paths.get(key, 0) + 1
    return {
        "positive": {"passed": pos_ok, "total": len(pos), "rate": _rate(pos_ok, len(pos)),
                     "min": POSITIVE_MIN, "passed_by_path": paths},
        "negative": {"false_positive": neg_fp, "total": len(neg),
                     "rate": _rate(neg_fp, len(neg)), "max": NEGATIVE_FP_MAX,
                     "owner_match": sum(1 for r in neg if r.get("owner_match"))},
        "errors": sum(1 for r in rows if "error" in r),
        "failed_ids": [r["id"] for r in rows if not r.get("passed")],
    }


def verdict(summary: dict[str, Any]) -> int:
    """기준 충족 0 · 미달·호출 실패 1. 부분 실행(--ids)은 기준 대신 실패 유무만 본다."""
    pos, neg = summary["positive"], summary["negative"]
    if summary["errors"]:
        return 1
    pos_ok = pos["rate"] is None or pos["rate"] >= POSITIVE_MIN
    neg_ok = neg["rate"] is None or neg["rate"] <= NEGATIVE_FP_MAX
    return 0 if pos_ok and neg_ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="포탈 도움말 라우팅 측정 M-2 (plans/142 §7)")
    ap.add_argument("--gold", default=str(GOLD), help="골드 경로")
    ap.add_argument("--ids", help="쉼표 구분 id 부분 집합(교정 뒤 실패 문항 재측정)")
    ap.add_argument("--dry-run", action="store_true", help="호출 없이 골드·설정만 점검")
    ap.add_argument("--out", help="결과 JSON 저장 경로")
    args = ap.parse_args()

    items = load_gold(Path(args.gold))
    errs = validate_gold(items)
    if errs:
        print("골드 정합성 오류:", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1
    if args.ids:
        wanted = {s.strip() for s in args.ids.split(",") if s.strip()}
        unknown = sorted(wanted - {it["id"] for it in items})
        if unknown:
            print(f"알 수 없는 id: {unknown}", file=sys.stderr)
            return 1
        items = [it for it in items if it["id"] in wanted]

    from src.config import load_config
    from src.orchestration.portal_help import portal_active

    cfg = load_config()
    active = portal_active(cfg)
    print(f"골드 {len(items)}건(양성 {sum(1 for i in items if i['polarity'] == 'positive')} · "
          f"음성 {sum(1 for i in items if i['polarity'] == 'negative')}) · "
          f"provider={cfg.llm.provider} · portal_help 활성={active}")
    if not active:
        print("포탈 도움말이 비활성이다 — 측정이 뜻이 없다(PORTAL_HELP_ENABLED·색인 확인)",
              file=sys.stderr)
        return 1
    if args.dry_run:
        print("[dry-run] 실 호출 없음.")
        return 0
    _require_optin()
    rows = asyncio.run(run(items))
    summary = summarize(rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps({"summary": summary, "results": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"저장: {args.out}")
    return verdict(summary)


if __name__ == "__main__":
    raise SystemExit(main())
