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

모드(plans/121 TP-0.2·TP-0.6 — 기존 두 모드의 판정·출력은 그대로다):
    (기본)
        정적 core 점수 — `_llm_classify` 단독 · 등록 DB 전부 · DB 설명 없음.
        `suite`가 없는(= core) 항목만 채점한다(core26).
    --decomposition
        분해 함수 단위 — `_llm_decompose`만 부른다(사전 처리 계층 A 우회).
    --decomposition --node
        분해 **노드 단위** — `intent_planner` 노드 함수를 통째로 부른다(사전 처리 계층 A 포함 ·
        골드 `parsed`로 파서 출력 동결 · `state` 주입). 지표: node/edge F1 · 형식 오류율(구조
        오답과 분리) · pass^k · 작업 유형(채점 불가 표기).
    --decomposition --node --type-experiment
        작업 유형 분류 **사전 실험**(plans/121 TP-0.8 · G-31 입력) — 노드 모드와 같은 입력으로
        분해 함수만 **초안 1회 호출**로 바꿔 끼운다(현행 분해 프롬프트 + 유형 카탈로그 12종 +
        확장 출력 스키마 · 스크립트 실험 상수 · 제품 코드 변경 0). 지표: 다단계 3종 정밀도 ·
        12종 혼동 행렬 · 확장 출력 형식 오류율(현행 무효·실패 정의 병기) · tasks node/edge F1 ·
        pass^k. 회귀 게이트가 아니다 — 종료 코드는 측정 성립 여부만 본다.
        `--type-draft`로 초안을 고른다 — 기본 `tp08-draft-2`(확장 4키를 「출력 형식」 골격에) ·
        `tp08-draft-1`은 비교용 재현(「출력 형식」 앞 절 — MLX 21/21 누락 · plans/121 §14.3).
    --runtime-prompt-conditions
        라우팅 골드를 **런타임 프롬프트 조건**으로 채점 — 활성 DB 집합(설정 해석값 또는
        `--active-dbs` 주입) · DB 설명 스냅샷(`--db-descriptions`) · 플래그 상태 기록.
        **완전한 런타임이 아니다**(`_RUNTIME_CAVEAT`).
    --repeat N
        신규 두 모드에서 같은 골드를 N회 돌려 pass^N(모든 회차 통과)을 센다.

    DB 설명 스냅샷 만들기(로컬 값만 · 운영 설명 반입 금지):
        .venv/bin/python scripts/schema_cache_cli.py db-description
        # 출력을 {db_id: 설명} YAML로 옮긴다
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import importlib
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
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


#: 골드 항목 묶음(plans/121 TP-0.2·TP-0.6). `suite`가 없으면 core다.
#: 기존 두 모드(정적 라우팅 · 분해 함수 단위)는 **core만** 읽는다 — 정적 core 점수(core26)와
#: 분해 core 5건의 판정·요약이 새 항목 때문에 바뀌지 않게 한다.
_CORE_SUITE = "core"
_ROUTING_SUITES = frozenset({_CORE_SUITE, "system"})   # system = 시스템 수준 소스 판단(TP-0.6)
_DECOMP_SUITES = frozenset({_CORE_SUITE, "node"})      # node = 노드 모드 전용(사전 처리·상태 주입)


def load_gold(path: Path = _GOLD, *, suites: tuple[str, ...] | None = (_CORE_SUITE,)) -> list[dict]:
    """골드 항목을 읽는다. 기본은 core 묶음만 — `suites=None`이면 전부."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    items = data["items"]
    if suites is None:
        return items
    return [it for it in items if it.get("suite", _CORE_SUITE) in suites]


def _validate_suite_fields(it: dict, iid: str, allowed: frozenset[str]) -> list[str]:
    """`suite`·`unscored`(채점 불가 선언) 검사 — 두 골드 공용."""
    errs: list[str] = []
    suite = it.get("suite", _CORE_SUITE)
    if suite not in allowed:
        errs.append(f"{iid}: 알 수 없는 suite {suite!r}(허용: {', '.join(sorted(allowed))})")
    if "unscored" in it:
        reason = it.get("unscored")
        if not (isinstance(reason, str) and reason.strip()):
            errs.append(f"{iid}: unscored는 채점 불가 사유 문자열이어야 한다")
        elif suite == _CORE_SUITE:
            errs.append(f"{iid}: core 항목은 unscored를 둘 수 없다 — 정적 core 점수에 섞이지 않게 "
                        "suite를 지정하라")
    return errs


def validate_gold(items: list[dict]) -> list[str]:
    """골든셋 자체의 정합성 — 실행 전에 잡는다.

    채점 불가 선언(`unscored`) 항목은 `expect`를 비워 둘 수 있다(출력 칸이 아직 없는 기대값을
    지금 어휘로 적으면 틀린 기대가 된다). `expect`가 있으면 똑같이 검사한다.
    """
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
        if it.get("unscored") and "expect" not in it:
            if not it.get("query"):
                errs.append(f"{iid}: query 없음")
            errs.extend(_validate_suite_fields(it, iid, _ROUTING_SUITES))
            continue
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
        errs.extend(_validate_suite_fields(it, iid, _ROUTING_SUITES))
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

#: 노드 모드 상태 주입(`state`)에서 막는 키 — 골드의 전용 필드(`query`·`parsed`)로 준다.
_STATE_KEYS_VIA_OWN_FIELD = frozenset({"user_query", "parsed_requirements"})


def validate_decomposition_gold(items: list[dict]) -> list[str]:
    """분해 골든셋 정합성 — 실행 전에 잡는다.

    노드 모드 필드(plans/121 TP-0.2)도 여기서 본다 — 형태의 정본은 런타임 코드다(사본 금지).
    - `parsed`: 파서 출력 동결값 — 키는 `ParsedRequirements` 필드이고 값은 그 모델 검증을
      통과해야 한다.
    - `state`: 노드 입력 상태 주입 — 키는 `AgentState` 키(`user_query`·`parsed_requirements` 제외 —
      각각 `query`·`parsed`로 준다). `selected_db_ids`·`mapped_db_ids`는 등록 DB id여야 한다.
    - `expect.request_type`: 작업 유형(TP-2 전이라 **채점 불가**로만 표기). 열거형 정본은 TP-2
      랜딩 때 생긴다 — 지금은 문자열 여부만 본다(초안 코드 목록을 여기 베끼지 않는다).
    """
    from pydantic import ValidationError

    from src.nodes.schemas import ParsedRequirements
    from src.orchestration.schemas import allowed_agents
    from src.routing.domain_config import DB_DOMAINS
    from src.state import AgentState

    known = allowed_agents()
    known_dbs = {d.db_id for d in DB_DOMAINS}
    parsed_fields = set(ParsedRequirements.model_fields)
    state_keys = set(AgentState.__annotations__) - _STATE_KEYS_VIA_OWN_FIELD
    errs: list[str] = []
    seen: set[str] = set()
    for it in items:
        iid = it.get("id", "?")
        if iid in seen:
            errs.append(f"{iid}: 중복 id")
        seen.add(iid)
        errs.extend(_validate_suite_fields(it, iid, _DECOMP_SUITES))
        if "parsed" in it:
            parsed = it.get("parsed")
            if not isinstance(parsed, dict):
                errs.append(f"{iid}: parsed는 파서 출력 매핑이어야 한다")
            else:
                extra = sorted(set(parsed) - parsed_fields)
                if extra:
                    errs.append(f"{iid}: parsed에 ParsedRequirements 밖 키 {extra}")
                else:
                    try:
                        ParsedRequirements.model_validate(parsed)
                    except ValidationError as e:
                        errs.append(f"{iid}: parsed 형식 오류 — {e.errors()[0].get('msg')}")
        if "state" in it:
            injected = it.get("state")
            if not isinstance(injected, dict):
                errs.append(f"{iid}: state는 AgentState 키 매핑이어야 한다")
            else:
                bad = sorted(set(injected) - state_keys)
                if bad:
                    errs.append(f"{iid}: state에 주입할 수 없는 키 {bad}"
                                "(AgentState 밖이거나 query·parsed로 줄 값)")
                for key in ("selected_db_ids", "mapped_db_ids"):
                    unknown = [d for d in (injected.get(key) or []) if d not in known_dbs]
                    if unknown:
                        errs.append(f"{iid}: state.{key}에 등록되지 않은 db_id {unknown}")
        if not it.get("query"):
            errs.append(f"{iid}: query 없음")
        if it.get("unscored") and "expect" not in it:
            continue
        exp = it.get("expect") or {}
        if int(exp.get("min_tasks", 0)) < 1:
            errs.append(f"{iid}: min_tasks는 1 이상")
        for a in exp.get("agents") or []:
            if a not in known:
                errs.append(f"{iid}: 알 수 없는 agent {a!r}")
        for edge in exp.get("edges") or []:
            if not (isinstance(edge, list) and len(edge) == 2):
                errs.append(f"{iid}: edge는 [from_agent, to_agent] 쌍")
        if "request_type" in exp and not (
            isinstance(exp.get("request_type"), str) and exp["request_type"].strip()
        ):
            errs.append(f"{iid}: expect.request_type은 작업 유형 코드 문자열")
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


# ──────────────────────────────────────────────
# 분해 골드 — 노드 단위 평가 (plans/121 TP-0.2)
# ──────────────────────────────────────────────
#
# 함수 단위 모드(`run_decomposition`)는 `_llm_decompose`만 불러 사전 처리 계층 A(존 재진입 ②.5 ·
# 양식 ③ · mapped_db_ids · 사용법 ③.8 …)를 우회한다. 노드 모드는 `intent_planner` 노드 함수를 통째로
# 부른다 — 상태는 `create_initial_state`(런타임 shape)로 만들고, 파서 출력은 골드 `parsed`로
# 동결한다.
#
# ⚠ **형식 오류는 노드 출력만으로 보이지 않는다**(실측 2026-09-28 · `intent_planner.py:1009-1018`).
#   기본 설정(`structured_output_backend=none`)의 JSON 경로는 파싱 실패·무효 출력·호출 예외를
#   전부 **사유 없이 단일 data_query 폴백**으로 돌려준다(로그만 남고 `degraded`·`dependency_notes`
#   는 없다). 그래서 단일이 정답인 케이스는 LLM이 고장 나도 **거짓 통과**한다 — 함수 단위 모드
#   목업 실측: 라우터 대본 응답에 5건 전부 폴백인데 `passed 2`·`errors 0`, 호출 오류
#   (`error_status`)도 `errors 0`. 노드 모드는 LLM 시도 단위로 분해 로거를 관찰해 형식 오류·
#   호출 오류를 구조 오답과 분리한다.

#: 분해 로거 메시지 접두 → 시도 분류. 정본은 `src/orchestration/intent_planner.py`의 로그 문구다.
#: 문구가 바뀌면 관찰이 조용히 무력화되므로 테스트가 접두의 존재를 소스에서 확인한다.
_PLANNER_LOG_MARKERS: tuple[tuple[str, str], ...] = (
    ("intent_planner LLM 분해 실패", "call_error"),        # llm.ainvoke 예외 — 측정 무효
    ("intent_planner 분해 결과 없음/무효", "parse"),        # JSON 파싱 불가 · tasks 없음
    ("intent_planner 유효 task 없음", "parse"),             # tasks에 dict 항목 없음
    ("intent_planner 구조화 분해 실패", "schema"),          # 구조화 출력 검증 실패
    ("intent_planner 구조화 분해 결과가 비었음", "schema"),  # 구조화 출력 task 0개
)
#: 최종 계획을 폴백으로 바꾼 **무효 분해** 사유(`degraded` → `dependency_notes`) — 시도 뒤에 붙는다.
_INVALID_PLAN_REASONS = frozenset({"plan_dag_invalid", "task_frame_contract_violation"})
_FORMAT_CATEGORIES = frozenset({"parse", "schema"})

_REQUEST_TYPE_UNSCORED_REASON = "TP-2.2 전 — 분해 출력에 request_type 칸 없음(채점 불가)"

#: 노드 모드 결과 판정(outcome) — passed·failed만 구조 채점(node/edge F1)에 들어간다.
_OUTCOME_PASSED = "passed"
_OUTCOME_FAILED = "failed"
_OUTCOME_FORMAT = "format_error"
_OUTCOME_ERROR = "error"
_OUTCOME_UNSCORED = "unscored"


class _RecordList(logging.Handler):
    """WARNING 이상 로그 레코드를 모은다(관찰 전용 — 출력하지 않는다)."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class _PlannerObservation:
    """노드 1회 호출의 LLM 분해 시도 관찰 — 시도마다 분류(None=정상)를 남긴다."""

    def __init__(self) -> None:
        self.attempts: list[str | None] = []


def _attempt_category(records: list[logging.LogRecord]) -> str | None:
    """한 시도 동안 남은 분해 로그로 시도를 분류한다(없으면 None = 형식 정상)."""
    for rec in records:
        msg = str(rec.msg)
        for prefix, category in _PLANNER_LOG_MARKERS:
            if msg.startswith(prefix):
                return category
    return None


@contextmanager
def _observe_planner(ip_module: Any) -> Iterator[_PlannerObservation]:
    """`_decompose_once`(LLM 1회 분해)를 감싸 시도 수·시도별 형식 분류를 관찰한다.

    제품 코드는 바꾸지 않는다 — 모듈 속성을 호출 동안만 감싸고 되돌린다. 되먹임 재요청
    (`_enforce_plan_contract`)도 같은 모듈 전역을 부르므로 시도로 잡힌다. 사전 처리 단락이면 0회다.
    """
    obs = _PlannerObservation()
    handler = _RecordList()
    logger = logging.getLogger(ip_module.__name__)
    original = ip_module._decompose_once
    saved_level = logger.level
    if logger.getEffectiveLevel() > logging.WARNING:
        logger.setLevel(logging.WARNING)

    async def observed(*args: Any, **kwargs: Any) -> Any:
        start = len(handler.records)
        try:
            return await original(*args, **kwargs)
        finally:
            obs.attempts.append(_attempt_category(handler.records[start:]))

    logger.addHandler(handler)
    ip_module._decompose_once = observed
    try:
        yield obs
    finally:
        ip_module._decompose_once = original
        logger.removeHandler(handler)
        logger.setLevel(saved_level)


def build_node_state(item: dict) -> dict:
    """골드 항목 → `intent_planner` 입력 상태(런타임 shape).

    `create_initial_state`로 만들고 `parsed_requirements`는 input_parser 출력 계약대로 채운다 —
    `ParsedRequirements` 기본값 위에 골드 `parsed`(동결값)를 덮고 `original_query`를 둔다.
    골드에 `parsed`가 없으면 "파서가 아무것도 뽑지 못한" 출력과 같다(동결 아님 — 결과에 표기).
    """
    from src.nodes.schemas import ParsedRequirements
    from src.state import create_initial_state

    query = item["query"]
    state = create_initial_state(query)
    parsed = {**ParsedRequirements().model_dump(), **copy.deepcopy(item.get("parsed") or {})}
    parsed["original_query"] = query
    state["parsed_requirements"] = parsed
    for key, value in (item.get("state") or {}).items():
        state[key] = copy.deepcopy(value)  # type: ignore[literal-required]
    return state


def _counts(expected: list, got: list) -> dict[str, int]:
    """다중집합 대조 — tp·fp·fn."""
    exp_c, got_c = Counter(expected), Counter(got)
    tp = sum((exp_c & got_c).values())
    return {"tp": tp, "fp": sum(got_c.values()) - tp, "fn": sum(exp_c.values()) - tp}


def _prf(counts: dict[str, int]) -> dict[str, Any]:
    """micro 정밀도·재현율·F1. 분모 0이면 None(해당 없음 — 0점으로 세지 않는다)."""
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    return {
        **counts,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "f1": round(2 * tp / (2 * tp + fp + fn), 4) if (2 * tp + fp + fn) else None,
    }


def _plan_edges(tasks: list[dict]) -> list[tuple[str, str]]:
    """`input_from` 간선의 (선행 agent, 후행 agent) 쌍. 없는 task를 가리키면 선행을 "?"로 둔다."""
    by_id = {t.get("task_id"): t for t in tasks}
    edges: list[tuple[str, str]] = []
    for t in tasks:
        for src in t.get("input_from") or []:
            edges.append((str(by_id.get(src, {}).get("agent") or "?"), str(t.get("agent"))))
    return edges


def judge_decomposition_node(
    item: dict, out: dict, attempts: list[str | None], *, run: int = 1,
) -> dict:
    """노드 출력 1건 판정.

    함수 단위 판정(`judge_decomposition`) 위에 형식 오류·node/edge F1·채점 불가 표기를 더한다.
    """
    from src.utils.prior_dependency import NOTE_DECOMPOSE

    exp = item.get("expect") or {}
    tasks = [t for t in (out.get("task_plan") or []) if isinstance(t, dict)]
    notes = [n for n in (out.get("dependency_notes") or [])
             if isinstance(n, dict) and n.get("kind") == NOTE_DECOMPOSE]
    invalid = sorted({str(n.get("reason")) for n in notes
                      if n.get("reason") in _INVALID_PLAN_REASONS})
    common = {
        "id": item.get("id"), "query": item.get("query"), "critical": item.get("critical"),
        "suite": item.get("suite", _CORE_SUITE), "run": run,
        "llm_attempts": len(attempts),
        "preprocessed": not attempts,  # 사전 처리 계층 A 단락(LLM 0회)
        "format_events": [a for a in attempts if a in _FORMAT_CATEGORIES] + invalid,
        "call_errors": sum(1 for a in attempts if a == "call_error"),
        "parsed_frozen": "parsed" in item,
        "state_keys": sorted((item.get("state") or {}).keys()),
    }
    if item.get("unscored"):
        return {
            **common, "outcome": _OUTCOME_UNSCORED, "passed": None, "unscored": item["unscored"],
            "observed": {"agents": [t.get("agent") for t in tasks], "task_count": len(tasks),
                         "edges": [list(e) for e in _plan_edges(tasks)]},
        }

    base = judge_decomposition(item, {"tasks": tasks, "degraded": notes})
    final_format = bool(invalid) or bool(attempts and attempts[-1] in _FORMAT_CATEGORIES)
    if common["call_errors"]:
        outcome = _OUTCOME_ERROR          # 호출 실패 — 폴백 결과로 채점하면 측정이 무효다
    elif final_format:
        outcome = _OUTCOME_FORMAT         # 최종 계획이 형식 폴백 — 구조 오답과 따로 센다
    else:
        outcome = _OUTCOME_PASSED if base["passed"] else _OUTCOME_FAILED
    result = {**base, **common, "outcome": outcome, "passed": outcome == _OUTCOME_PASSED}
    if outcome == _OUTCOME_ERROR:
        result["error"] = "LLM 호출 실패(분해 로그 — 노드가 단일 폴백으로 삼킴)"
    if outcome in (_OUTCOME_PASSED, _OUTCOME_FAILED):
        if "agents" in exp:
            result["node"] = _prf(_counts(list(exp.get("agents") or []),
                                          [str(t.get("agent")) for t in tasks]))
        if "edges" in exp:
            result["edge"] = _prf(_counts([tuple(e) for e in exp.get("edges") or []],
                                          _plan_edges(tasks)))
    if "request_type" in exp:
        result["request_type_expected"] = exp["request_type"]
        result["request_type_scored"] = False
        result["request_type_unscored_reason"] = _REQUEST_TYPE_UNSCORED_REASON
    return result


async def run_decomposition_node(
    items: list[dict], *, llm: Any = None, cfg: Any = None, repeat: int = 1,
) -> list[dict]:
    """`intent_planner` 노드 단위 평가(사전 처리 포함). 채점 불가 선언 항목도 관찰로 돌린다."""
    from src.config import load_config
    from src.llm import create_llm

    ip = importlib.import_module("src.orchestration.intent_planner")
    if cfg is None:
        cfg = load_config()
    if llm is None:
        llm = create_llm(cfg)
    results: list[dict] = []
    for run_no in range(1, repeat + 1):
        for it in items:
            state = build_node_state(it)
            with _observe_planner(ip) as obs:
                try:
                    out = await ip.intent_planner(state, llm=llm, app_config=cfg)
                except Exception as e:  # noqa: BLE001 — 개별 실패가 전체를 막지 않는다
                    results.append({
                        "id": it.get("id"), "query": it.get("query"),
                        "critical": it.get("critical"),
                        "suite": it.get("suite", _CORE_SUITE), "run": run_no,
                        "outcome": _OUTCOME_ERROR, "error": f"{type(e).__name__}: {e}",
                        "passed": False,
                    })
                    continue
            results.append(judge_decomposition_node(it, out, obs.attempts, run=run_no))
    return results


def pass_hat_k(results: list[dict], k: int) -> dict[str, Any]:
    """pass^k — 채점 대상 항목이 k회 **모두** 통과했는가(채점 불가 제외)."""
    runs: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        if r.get("unscored"):
            continue
        runs[str(r.get("id"))].append(bool(r.get("passed")))
    passed_all = sum(1 for v in runs.values() if len(v) == k and all(v))
    return {
        "k": k, "items": len(runs), "passed_all_runs": passed_all,
        "rate": round(passed_all / len(runs), 4) if runs else None,
    }


def summarize_decomposition_node(results: list[dict], *, repeat: int = 1) -> dict:
    """노드 모드 요약. 합계는 **run 합산**이고 신뢰도는 `pass_hat_k`로 본다."""
    scored = [r for r in results if r.get("outcome") != _OUTCOME_UNSCORED]
    structural = [r for r in scored if r.get("outcome") in (_OUTCOME_PASSED, _OUTCOME_FAILED)]
    llm_runs = [r for r in scored if r.get("llm_attempts")]
    unscored = [r for r in results if r.get("outcome") == _OUTCOME_UNSCORED]
    seq = [r for r in scored if r.get("critical") == "sequential"]
    node_c = {"tp": 0, "fp": 0, "fn": 0}
    edge_c = {"tp": 0, "fp": 0, "fn": 0}
    for r in structural:
        for key, acc in (("node", node_c), ("edge", edge_c)):
            for c in acc:
                acc[c] += (r.get(key) or {}).get(c, 0)
    by_outcome = Counter(r.get("outcome") for r in scored)
    format_runs = by_outcome[_OUTCOME_FORMAT]
    rt_declared = sorted({str(r["id"]) for r in scored if "request_type_expected" in r})
    return {
        "mode": "node",
        "items": len({r.get("id") for r in results}),
        "runs": repeat,
        "total": len(scored),
        "passed": by_outcome[_OUTCOME_PASSED],
        "failed": by_outcome[_OUTCOME_FAILED],
        "format_errors": format_runs,
        "errors": by_outcome[_OUTCOME_ERROR],
        "unscored": {"runs": len(unscored), "items": len({r.get("id") for r in unscored})},
        "sequential_cases": len(seq),
        "sequential_preserved": sum(
            1 for r in seq
            if r.get("edge_ok") and r.get("outcome") in (_OUTCOME_PASSED, _OUTCOME_FAILED)
        ),
        "single_false_split": sum(
            1 for r in structural if r.get("critical") == "single" and not r.get("single_ok")
        ),
        "preprocessed_runs": sum(1 for r in scored if r.get("preprocessed")),
        "llm_runs": len(llm_runs),
        "format_error_rate": round(format_runs / len(llm_runs), 4) if llm_runs else None,
        "llm_attempts": sum(int(r.get("llm_attempts") or 0) for r in scored),
        "attempt_format_errors": sum(
            1 for r in scored for e in r.get("format_events") or [] if e in _FORMAT_CATEGORIES
        ),
        "node": _prf(node_c),
        "edge": _prf(edge_c),
        "request_type": {"declared_items": len(rt_declared), "scored": 0,
                         "reason": _REQUEST_TYPE_UNSCORED_REASON},
        "pass_hat_k": pass_hat_k(scored, repeat),
    }


def _verdict_decomposition_node(summary: dict, *, tolerate: int = 0) -> int:
    """노드 모드 종료 판정 — 호출 실패 · 순차 배선 유실 · 실패(형식 오류 포함) 순으로 본다."""
    fails: list[str] = []
    if summary["errors"]:
        fails.append(f"LLM 호출 실패 {summary['errors']}건 — 측정 무효")
    if summary["sequential_preserved"] < summary["sequential_cases"]:
        lost = summary["sequential_cases"] - summary["sequential_preserved"]
        fails.append(f"순차 배선 유실 {lost}건(형식 오류 폴백 포함)")
    missed = summary["total"] - summary["passed"]
    if missed > tolerate:
        fails.append(f"실패 {missed}건(허용 {tolerate}) — 구조 오답 {summary['failed']} · "
                     f"형식 오류 {summary['format_errors']} · 호출 실패 {summary['errors']}")
    if fails:
        print("회귀 판정:", file=sys.stderr)
        for f in fails:
            print(f"  - {f}", file=sys.stderr)
        return 1
    return 0


# ──────────────────────────────────────────────
# 작업 유형 분류 사전 실험 (plans/121 TP-0.8 · G-31 입력)
# ──────────────────────────────────────────────
#
# 노드 모드를 확장한다 — `intent_planner` 노드를 통째로 부르되(사전 처리 계층 A · 상태 주입 ·
# 담당 교정 · 출구 정규화 그대로) 분해 함수 `_llm_decompose`만 **초안 1회 호출**로 바꿔 끼운다.
# 초안 = 현행 분해 시스템 프롬프트(설정 해석값 — 노드와 같다)에 유형 카탈로그와 확장 출력 스키마를
# **삽입만** 한 것. 입력 = 원문 앞에 동결 `parsed_requirements` 블록(TP-2.2 입력안).
#
# - 초안 1(`tp08-draft-1`): 「출력 형식」 **앞**에 절 하나(자체 JSON 예시 포함). MLX 1회 실험에서
#   확장 4필드가 21/21 누락됐다 — 모델이 뒤쪽 「출력 형식」 골격만 따랐다(plans/121 §14.3).
#   비교용으로 렌더를 그대로 재현한다(`--type-draft tp08-draft-1`).
# - 초안 2(`tp08-draft-2` · 기본 · plans/121 §14.2 재개 조건): 확장 4키를 「출력 형식」 **골격(JSON
#   예시) 자체**에 넣는다. 앞 절은 카탈로그·키 설명만 싣고 별도 JSON 예시를 두지 않는다(골격 하나).
#   「출력 형식」 주의 목록에 "예시에 없어도 네 키는 항상" 한 줄을 더한다 — 뒤쪽 예시 5종이
#   `tasks` 모양만 보여 주기 때문이다(예시 자체는 고치지 않는다 — 유형 라벨 경계가 사용자 확정 전).
#
# - 제품 코드 변경 0: 초안은 이 스크립트의 실험 상수다(`src/prompts/*` 미변경). 모듈 속성은 호출
#   동안만 바꿔 끼우고 되돌린다(`_observe_planner`와 같은 방식).
# - LLM 1회: 계약 되먹임 재요청(`_enforce_plan_contract`)을 뺀다 — G-21 ⓐ는 불합격이면 같은 출력의
#   `tasks`로 가고 재호출하지 않는다. 나머지 후처리(닫힌 어휘 · 담당 교정 · 소유 정제 · task 프레임
#   검증)는 제품 함수 그대로다.
# - JSON 경로 고정: 확장 스키마의 구조화 모델이 없으므로 `structured_output_backend`와 무관하다.
# - 사전 처리 단락 항목은 유형 LLM을 부르지 않는다(§4.1 — 유형 호출은 계층 A 뒤) → 채점 불가.

#: 초안 식별자(plans/121 TP-0.8). 기본은 최신 초안 — 이전 초안은 비교용으로 렌더를 재현한다.
TYPE_EXPERIMENT_DRAFT_1 = "tp08-draft-1"
TYPE_EXPERIMENT_DRAFT_2 = "tp08-draft-2"
TYPE_EXPERIMENT_DRAFT_ID = TYPE_EXPERIMENT_DRAFT_2
TYPE_EXPERIMENT_DRAFTS = (TYPE_EXPERIMENT_DRAFT_1, TYPE_EXPERIMENT_DRAFT_2)

#: 작업 유형 12종(§4.2 · G-2 초안 · D-270 ②) — (코드, 카탈로그 한 줄). 유형은 **구조**를 말하고
#: 소스를 말하지 않는다. 열거형 정본은 TP-2.2 랜딩 때 생긴다 — 이 표는 실험 상수다.
_TYPE_CATALOG: tuple[tuple[str, str], ...] = (
    ("lookup", "목록·상세 조회 — 1단계"),
    ("rank", "상위·하위 N — 1단계 · 정렬 기준과 개수 필수"),
    ("aggregate", "집계·통계 — 1단계 · 집계 필수"),
    ("trend", "기간 추이 — 1단계 · 기간 필수"),
    ("compare", "비교(존·DB·기간·시스템) — 비교 축마다 1단계씩 병렬로 조회해 병합"),
    ("select_then_detail",
     "선별 → 상세 — 1단계에서 대상을 선별하고 2단계에서 선별된 대상의 상세를 조회"),
    ("cross_domain",
     "교차 도메인 — 1단계에서 한 영역으로 대상을 정하고 2단계에서 다른 영역을 조회"),
    ("realtime_inspect", "실시간·호스트 점검 — 1단계"),
    ("document_fill", "양식 산출 — 업로드한 양식을 채운다"),
    ("admin_op", "캐시·유사어 관리 — 1단계"),
    ("usage_help", "사용법·일반 — 1단계"),
    ("free_composite", "위 유형에 맞지 않는 복합(3단계 이상 등)"),
)
_TYPE_CODES = frozenset(code for code, _ in _TYPE_CATALOG)
#: 루틴이 단계를 만드는 다단계 3종 — 틀린 루틴 적용이 해로워(A-3) 정밀도를 본다(§13.4).
_MULTI_STEP_TYPES = ("compare", "select_then_detail", "cross_domain")
#: `step_slots` 값 어휘 — 입력 파서 구조화 슬롯(`ParsedRequirements` 필드 · 테스트가 대조한다).
_TYPE_EXP_SLOTS = ("query_targets", "filter_conditions", "time_range", "aggregation", "limit")
#: 확장 출력 필드(§4.1 · TP-2.2). `tasks`는 종전 판정 그대로다(G-21 ⓐ — 항상 포함).
_TYPE_EXP_FIELDS = ("request_type", "required_areas", "step_slots", "requested_source")
#: 삽입 앵커 — 제품 소유·task 프레임 절과 같은 자리(「출력 형식」 앞).
_TYPE_EXP_ANCHOR = "## 출력 형식\n"
#: (초안 2) 「출력 형식」 골격 JSON의 여는 줄 — 확장 4키를 이 뒤(`clarification_needed` 앞)에
#: 넣는다.
_TYPE_EXP2_SKELETON_ANCHOR = (
    "반드시 아래 JSON 형식으로만 응답하세요. 추가 설명은 불필요합니다.\n\n```json\n{{\n"
)
#: (초안 2) 「출력 형식」 주의 목록의 마지막 줄 — 이 뒤에 확장 키 필수 한 줄을 넣는다.
_TYPE_EXP2_RULE_ANCHOR = "- `tasks`는 최소 1개 이상이어야 합니다.\n"
_TYPE_EXP2_RULE = (
    "- `request_type`·`required_areas`·`step_slots`·`requested_source` 네 키는 **항상** "
    "적습니다 — 아래 예시는 `tasks` 분해만 보여 줍니다.\n"
)
_TYPE_EXP_PARSED_HEADING = "## 입력 해석(parsed_requirements)"
#: 혼동 행렬의 무효 열 — 유형 칸 누락·열거 밖 값·파싱 실패.
_TYPE_INVALID = "(무효)"
#: 현행 LLM 분해 무효·실패 기준선(§12.3 (b) · §6 v6) — 현행 스키마 · 2단 · 운영 run.
_FORMAT_BASELINE: dict[str, Any] = {
    "run": "20260923-103638", "invalid": 23, "failed": 3, "llm_turns": 130, "rate": 0.2,
    "definition": "무효(파싱 불가·tasks 없음/무효) + 실패(호출 예외) / LLM 분해 턴",
}
_TYPE_EXPERIMENT_CAVEAT = (
    "작업 유형 사전 실험(plans/121 TP-0.8) — ①초안 프롬프트는 스크립트 실험 상수다(현행 분해 "
    "프롬프트에 삽입만 · 제품 프롬프트 아님) ②LLM 1회 — 계약 되먹임 재요청 없음(G-21 ⓐ) · JSON "
    "경로 고정 ③사전 처리 단락 항목은 유형 LLM을 부르지 않는다(채점 불가) ④골드 유형 라벨은 사용자 "
    "검수 전이다 ⑤형식 기준선 20%(26/130)는 운영 run 턴 단위라 모집단이 다르다"
)
_OUTCOME_PREPROCESSED = "preprocessed"
#: 유형 채점 불가 종류 → 사유.
_TYPE_UNSCORED_REASONS = {
    "preprocessed": "사전 처리 단락 — 유형 LLM 미호출(§4.1 유형 호출은 계층 A 뒤)",
    "declared_unscored": "채점 불가 선언 항목",
    "no_expected_type": "골드에 기대 유형 없음",
    "call_error": "LLM 호출 실패 — 측정 무효",
}


def _type_exp_common_lines(specs: Any) -> list[str]:
    """두 초안 공통 줄 — 유형 지시 · 유형 카탈로그 한 줄씩 · 답변 영역 목록 · 파서 블록 안내.

    답변 영역은 레지스트리 카탈로그(`capability_specs`)에서 렌더한다(사본 금지 D-053).
    """
    return [
        "질의 전체를 보고 **작업 유형 하나**를 아래 목록에서 골라 `request_type`에 코드 그대로 "
        "적으세요.",
        "유형은 작업의 **구조(단계 모양)**입니다. 어느 시스템·DB를 조회하는지는 유형이 아닙니다.",
        "",
        *[f"- `{code}`: {line}" for code, line in _TYPE_CATALOG],
        "",
        "`required_areas`에는 아래 답변 영역 코드만 씁니다.",
        "",
        *[f"- `{s.code}`: {s.label or s.code}" for s in specs],
        "",
        f"사용자 질의 앞의 「{_TYPE_EXP_PARSED_HEADING}」 블록은 입력 파서가 뽑은 구조화 "
        "값입니다(참고용 — 위 분해 규칙은 그대로입니다).",
        "",
    ]


def _type_exp_key_lines() -> list[str]:
    """두 초안 공통 줄 — 확장 4키의 뜻(`_TYPE_EXP_FIELDS` 순서)."""
    slots = "·".join(f"`{s}`" for s in _TYPE_EXP_SLOTS)
    return [
        "- `request_type`: 위 유형 코드 하나",
        "- `required_areas`: 답에 필요한 답변 영역 코드 목록(맞는 영역이 없으면 빈 배열)",
        "- `step_slots`: task별로 그 task가 쓰는 입력 해석 슬롯 이름 목록 — `tasks`의 task_id를 "
        f"키로 둡니다. 슬롯 이름은 {slots} 중에서만 고릅니다.",
        "- `requested_source`: 사용자가 조회할 시스템을 **직접 지정**했으면 질의에 적힌 그 이름, "
        "아니면 null",
    ]


def _type_exp_key_json_lines(first_area: str) -> list[str]:
    """확장 4키의 JSON 줄 — 제품 골격과 같은 들여쓰기·`{{` 표기(그 프롬프트는 `.format()`을
    거치지 않는다). 초안 1은 자기 예시 JSON에, 초안 2는 「출력 형식」 골격에 넣는다."""
    return [
        '    "request_type": "lookup",',
        f'    "required_areas": ["{first_area}"],',
        '    "step_slots": {{"t1": ["filter_conditions"]}},',
        '    "requested_source": null,',
    ]


def _type_experiment_section() -> str:
    """(초안 1 — 비교용 재현) 「출력 형식」 앞 삽입 절 — 카탈로그 + 확장 출력 스키마 예시 JSON.

    MLX 1회 실험에서 확장 4필드가 21/21 누락됐다(plans/121 §14.3) — 뒤쪽 골격에 밀린다.
    """
    from src.routing.registry import get_registry

    specs = get_registry().capability_specs()
    first_area = specs[0].code if specs else ""
    lines = [
        "## 작업 유형(request_type)과 확장 출력 — 이 절이 아래 「출력 형식」보다 우선합니다",
        "",
        *_type_exp_common_lines(specs),
        "아래 「출력 형식」의 JSON 객체에 다음 네 키를 **모두** 더하세요. `tasks`는 종전 규칙대로 "
        "**항상** 적습니다.",
        *_type_exp_key_lines(),
        "",
        "형식(키와 값의 모양):",
        "```json",
        "{{",
        *_type_exp_key_json_lines(first_area),
        '    "clarification_needed": null,',
        '    "tasks": [',
        '        {{"task_id": "t1", "agent": "data_query", '
        '"sub_query": "이 작업이 처리할 자연어 지시",',
        '         "depends_on": [], "input_from": [], "order": 1}}',
        "    ]",
        "}}",
        "```",
        "",
        "",
    ]
    return "\n".join(lines)


def _type_experiment_section_v2() -> str:
    """(초안 2 · plans/121 TP-0.8) 「출력 형식」 앞 삽입 절 — 카탈로그·키 설명만(JSON 예시 없음).

    키의 모양은 「출력 형식」 골격 하나에만 둔다(`_type_exp2_skeleton_keys`).
    """
    from src.routing.registry import get_registry

    lines = [
        "## 작업 유형(request_type)과 확장 출력",
        "",
        *_type_exp_common_lines(get_registry().capability_specs()),
        "아래 「출력 형식」의 JSON 객체에 있는 네 키는 이렇게 채웁니다. `tasks`는 종전 규칙대로 "
        "**항상** 적습니다.",
        *_type_exp_key_lines(),
        "",
        "",
    ]
    return "\n".join(lines)


def _type_exp2_skeleton_keys() -> str:
    """(초안 2) 「출력 형식」 골격 JSON의 여는 줄 뒤(`clarification_needed` 앞)에 넣을 확장 4키."""
    from src.routing.registry import get_registry

    specs = get_registry().capability_specs()
    return "".join(f"{line}\n" for line in _type_exp_key_json_lines(specs[0].code if specs else ""))


def _insert_after_once(text: str, anchor: str, insert: str) -> str:
    """``anchor`` 바로 뒤에 ``insert``를 넣는다(삽입만).

    Raises:
        RuntimeError: 앵커가 정확히 1회 나타나지 않는다(현행 프롬프트 구조가 바뀜)
    """
    if text.count(anchor) != 1:
        raise RuntimeError(f"분해 프롬프트 삽입 앵커가 1회가 아니다: {anchor!r}")
    head, sep, tail = text.partition(anchor)
    return head + sep + insert + tail


def render_type_experiment_prompt(cfg: Any, draft: str = TYPE_EXPERIMENT_DRAFT_ID) -> str:
    """초안 시스템 프롬프트 — 현행 분해 프롬프트(설정 해석값)에 실험 절을 **삽입만** 한다.

    - 초안 1: 「출력 형식」 앞에 절 하나(자체 JSON 예시 포함)
    - 초안 2: 「출력 형식」 앞에 카탈로그·키 설명 절 + 「출력 형식」 골격 JSON에 확장 4키 +
      「출력 형식」 주의 목록에 "네 키는 항상" 한 줄

    Raises:
        ValueError: 모르는 초안 식별자
        RuntimeError: 앵커가 정확히 1회 나타나지 않는다(현행 프롬프트 구조가 바뀜)
    """
    if draft not in TYPE_EXPERIMENT_DRAFTS:
        raise ValueError(
            f"모르는 초안 식별자: {draft!r} (가능: {', '.join(TYPE_EXPERIMENT_DRAFTS)})"
        )
    ip = importlib.import_module("src.orchestration.intent_planner")
    base = ip._planner_system_prompt(cfg)
    if base.count(_TYPE_EXP_ANCHOR) != 1:
        raise RuntimeError(f"분해 프롬프트 삽입 앵커가 1회가 아니다: {_TYPE_EXP_ANCHOR!r}")
    head, sep, tail = base.partition(_TYPE_EXP_ANCHOR)
    if draft == TYPE_EXPERIMENT_DRAFT_1:
        return str(head + _type_experiment_section() + sep + tail)
    tail = _insert_after_once(tail, _TYPE_EXP2_SKELETON_ANCHOR, _type_exp2_skeleton_keys())
    tail = _insert_after_once(tail, _TYPE_EXP2_RULE_ANCHOR, _TYPE_EXP2_RULE)
    return str(head + _type_experiment_section_v2() + sep + tail)


def type_experiment_prompt_meta(cfg: Any, draft: str = TYPE_EXPERIMENT_DRAFT_ID) -> dict[str, Any]:
    """초안 프롬프트 식별 정보 — 크기·증가량·지문(본문은 싣지 않는다)."""
    from src.routing.registry import get_registry

    ip = importlib.import_module("src.orchestration.intent_planner")
    base = str(ip._planner_system_prompt(cfg))
    rendered = render_type_experiment_prompt(cfg, draft)
    added = len(rendered) - len(base)
    return {
        "draft": draft,
        "chars": len(rendered), "base_chars": len(base), "added_chars": added,
        "added_pct": round(100 * added / len(base), 1) if base else None,
        "sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16],
        "types": len(_TYPE_CATALOG),
        "areas": len(get_registry().capability_specs()),
        "slots": list(_TYPE_EXP_SLOTS),
    }


def type_experiment_human_content(
    user_query: str, parsed: dict[str, Any], context_block: str = "",
) -> str:
    """초안 사람 메시지 — 제품 입력(맥락 블록 + 원문) 사이에 동결 파서 출력 블록을 넣는다.

    블록에는 `ParsedRequirements` 기본값과 다른 필드만 싣는다(`original_query` 제외 — 원문이 따로
    온다). 원문은 마지막 줄이다(제품과 같은 순서).
    """
    from src.nodes.schemas import ParsedRequirements

    defaults = ParsedRequirements().model_dump()
    shown = {k: parsed[k] for k in defaults
             if k != "original_query" and k in parsed and parsed[k] != defaults[k]}
    block = (f"{_TYPE_EXP_PARSED_HEADING}\n```json\n"
             f"{json.dumps(shown, ensure_ascii=False)}\n```\n\n")
    return f"{context_block}{block}{user_query}"


def type_experiment_messages(llm: Any, system_prompt: str, human: str) -> list[Any]:
    """초안 메시지 — `_llm_decompose`와 같은 규약(KBGenAI면 빈 assistant 턴을 끼운다)."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    from src.clients.fabrix_kbgenai import KBGenAIChat

    messages: list[Any] = [SystemMessage(content=system_prompt)]
    if isinstance(llm, KBGenAIChat):
        messages.append(AIMessage(content=""))
    messages.append(HumanMessage(content=human))
    return messages


class _CaptureLLM:
    """초안 호출의 원문 응답을 붙잡는다(관찰 전용 — 응답은 그대로 돌려준다)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.content: Any = None

    async def ainvoke(self, messages: Any, *args: Any, **kwargs: Any) -> Any:
        response = await self._inner.ainvoke(messages, *args, **kwargs)
        self.content = getattr(response, "content", None)
        return response


class _DraftCall:
    """노드 1회 호출 동안의 초안 원문 응답(호출 전·사전 처리 단락이면 None)."""

    def __init__(self) -> None:
        self.content: Any = None


@contextmanager
def _draft_decompose(
    ip_module: Any, state: dict[str, Any], system_prompt: str,
) -> Iterator[_DraftCall]:
    """`_llm_decompose`를 초안 1회 호출로 바꿔 끼운다(호출 동안만 — 되돌린다).

    파서 동결값은 노드 입력 상태(`build_node_state`)에서 읽는다. LLM 호출은 제품 `_decompose_once`
    (JSON 경로 · 닫힌 어휘)를 그대로 쓰고 원문 응답만 붙잡는다 — 형식 분류는 `_observe_planner`가
    분해 로그로 한다(노드 모드와 같은 정의).
    """
    call = _DraftCall()
    original = ip_module._llm_decompose
    parsed = dict(state.get("parsed_requirements") or {})

    async def draft(
        llm: Any, user_query: str, app_config: Any, *, conversation_context: Any = None,
    ) -> dict[str, Any]:
        context_block = ip_module._build_context_block(conversation_context, user_query)
        human = type_experiment_human_content(user_query, parsed, context_block)
        capture = _CaptureLLM(llm)
        fallback = {"tasks": ip_module._single_task_plan("data_query", user_query)["task_plan"],
                    "clarification_needed": None}
        json_cfg = app_config.model_copy(update={"structured_output_backend": "none"})
        try:
            result: dict[str, Any] = await ip_module._decompose_once(
                capture, type_experiment_messages(llm, system_prompt, human), user_query,
                json_cfg, fallback,
            )
        finally:
            call.content = capture.content
        # `_llm_decompose` 후처리 그대로 — 계약 되먹임 재요청(`_enforce_plan_contract`)만 뺀다.
        if ip_module._capability_ownership_on(app_config):
            ip_module._sanitize_task_capabilities(result)
        if ip_module._task_frame_on(app_config):
            result = ip_module._apply_task_frames(result, user_query, context_block, fallback)
        return result

    ip_module._llm_decompose = draft
    try:
        yield call
    finally:
        ip_module._llm_decompose = original


def _area_codes() -> frozenset[str]:
    from src.routing.registry import get_registry

    return frozenset(spec.code for spec in get_registry().capability_specs())


def _raw_task_ids(data: dict[str, Any]) -> set[str]:
    """출력 `tasks`의 task_id 집합 — 없는 id는 제품 JSON 경로처럼 순번(`t{i}`)으로 본다."""
    raw = data.get("tasks")
    if not isinstance(raw, list):
        return set()
    return {str(t.get("task_id", f"t{i}")) for i, t in enumerate(raw, 1) if isinstance(t, dict)}


def check_extended_fields(
    data: dict[str, Any], areas: frozenset[str],
) -> tuple[list[str], list[str]]:
    """확장 출력 필드 판정 → (누락 필드, 열거 밖·형식 오류 필드). 순서는 `_TYPE_EXP_FIELDS`."""
    missing = [f for f in _TYPE_EXP_FIELDS if f not in data]
    rt = data.get("request_type")
    areas_v = data.get("required_areas")
    slots_v = data.get("step_slots")
    src = data.get("requested_source")
    task_ids = _raw_task_ids(data)
    valid = {
        "request_type": isinstance(rt, str) and rt in _TYPE_CODES,
        "required_areas": isinstance(areas_v, list)
        and all(isinstance(a, str) and a in areas for a in areas_v),
        "step_slots": isinstance(slots_v, dict) and all(
            str(k) in task_ids and isinstance(v, list)
            and all(isinstance(s, str) and s in _TYPE_EXP_SLOTS for s in v)
            for k, v in slots_v.items()
        ),
        "requested_source": src is None or (isinstance(src, str) and bool(src.strip())),
    }
    bad = [f for f in _TYPE_EXP_FIELDS if f in data and not valid[f]]
    return missing, bad


def inspect_type_output(
    content: Any, category: str | None, areas: frozenset[str],
) -> dict[str, Any]:
    """초안 호출 1회의 확장 출력 판정.

    `category`는 분해 로그로 본 시도 분류(`_attempt_category`)다 — 파싱 성공 여부는 제품 파서
    판정을 따르고(`tasks` 무효도 파싱 실패), 성공일 때만 확장 필드를 본다.
    status: ok · field_error(누락·열거 밖) · parse_invalid · call_error.
    """
    from src.utils.json_extract import extract_json_from_response

    empty: dict[str, Any] = {"missing": [], "out_of_enum": []}
    if category == "call_error":
        return {"status": "call_error", **empty}
    if category in _FORMAT_CATEGORIES:
        return {"status": "parse_invalid", **empty}
    data = extract_json_from_response(content) if content is not None else None
    if not isinstance(data, dict):   # 제품 파서가 받은 출력이면 오지 않는다(방어)
        return {"status": "parse_invalid", **empty}
    missing, bad = check_extended_fields(data, areas)
    return {
        "status": "field_error" if (missing or bad) else "ok",
        "missing": missing, "out_of_enum": bad,
        **{f: data.get(f) for f in _TYPE_EXP_FIELDS},
    }


def judge_type_experiment(
    item: dict[str, Any], out: dict[str, Any], attempts: list[str | None], content: Any,
    *, areas: frozenset[str], run: int = 1,
) -> dict[str, Any]:
    """실험 1회 판정 — 노드 모드 판정(구조·형식) 위에 확장 출력·유형 채점을 더한다.

    run 합격 = 구조 합격 ∧ 확장 출력 형식 정상 ∧ (기대 유형이 있으면) 유형 일치.
    """
    result = judge_decomposition_node(item, out, attempts, run=run)
    # 노드 모드 표기(채점 불가)를 실험 판정으로 교체한다
    result.pop("request_type_scored", None)
    result.pop("request_type_unscored_reason", None)
    expected = (item.get("expect") or {}).get("request_type")
    if expected is not None:
        result["request_type_expected"] = expected
    if not attempts:
        kind = "preprocessed"
        result.update({"outcome": _OUTCOME_PREPROCESSED, "passed": None,
                       "unscored": _TYPE_UNSCORED_REASONS[kind],
                       "plan_path": out.get("plan_path")})
    else:
        output = inspect_type_output(content, attempts[-1], areas)
        raw = output.get("request_type")
        got = raw if isinstance(raw, str) and raw in _TYPE_CODES else None
        result.update({"type_output": output, "request_type_got": got})
        if item.get("unscored"):
            kind = "declared_unscored"
        elif output["status"] == "call_error":
            kind = "call_error"
        elif expected is None:
            kind = "no_expected_type"
        else:
            kind = ""
        if not item.get("unscored"):
            result["structure_passed"] = result.get("outcome") == _OUTCOME_PASSED
        if not kind:
            result["request_type_correct"] = got == expected
        if "structure_passed" in result:
            result["passed"] = (result["structure_passed"] and output["status"] == "ok"
                                and result.get("request_type_correct") is not False)
    result["request_type_scored"] = not kind
    if kind:
        result["request_type_unscored_kind"] = kind
    return result


async def run_type_experiment(
    items: list[dict[str, Any]], *, llm: Any = None, cfg: Any = None, repeat: int = 1,
    draft: str = TYPE_EXPERIMENT_DRAFT_ID,
) -> list[dict[str, Any]]:
    """작업 유형 사전 실험 — 노드를 통째로 부르되 분해만 초안 1회 호출로(TP-0.8)."""
    from src.config import load_config
    from src.llm import create_llm

    ip = importlib.import_module("src.orchestration.intent_planner")
    if cfg is None:
        cfg = load_config()
    if llm is None:
        llm = create_llm(cfg)
    system_prompt = render_type_experiment_prompt(cfg, draft)
    areas = _area_codes()
    results: list[dict[str, Any]] = []
    for run_no in range(1, repeat + 1):
        for it in items:
            state = build_node_state(it)
            with _observe_planner(ip) as obs, _draft_decompose(ip, state, system_prompt) as call:
                try:
                    out = await ip.intent_planner(state, llm=llm, app_config=cfg)
                except Exception as e:  # noqa: BLE001 — 개별 실패가 전체를 막지 않는다
                    results.append({
                        "id": it.get("id"), "query": it.get("query"),
                        "critical": it.get("critical"),
                        "suite": it.get("suite", _CORE_SUITE), "run": run_no,
                        "outcome": _OUTCOME_ERROR, "error": f"{type(e).__name__}: {e}",
                        "passed": False,
                    })
                    continue
            results.append(judge_type_experiment(it, out, obs.attempts, call.content,
                                                 areas=areas, run=run_no))
    return results


def _rate(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


def _type_format_summary(llm_runs: list[dict[str, Any]]) -> dict[str, Any]:
    """확장 출력 형식 요약 — 현행 정의(무효·실패)와 확장 정의(+ 누락·열거 밖)를 함께 싣는다."""
    status = Counter(r["type_output"]["status"] for r in llm_runs)
    missing = Counter(f for r in llm_runs for f in r["type_output"]["missing"])
    bad = Counter(f for r in llm_runs for f in r["type_output"]["out_of_enum"])
    n = len(llm_runs)
    invalid = status["call_error"] + status["parse_invalid"]
    return {
        "runs": n,
        "call_errors": status["call_error"],
        "parse_invalid": status["parse_invalid"],
        "invalid_or_failed": invalid,
        "invalid_or_failed_rate": _rate(invalid, n),
        "missing_field": {f: missing[f] for f in _TYPE_EXP_FIELDS if missing[f]},
        "out_of_enum": {f: bad[f] for f in _TYPE_EXP_FIELDS if bad[f]},
        "field_error_runs": status["field_error"],
        "extended_errors": n - status["ok"],
        "extended_error_rate": _rate(n - status["ok"], n),
        # 현행 정의 밖 — task 프레임 검증 폴백(플래그 on에서만 생긴다)
        "plan_contract_invalid": sum(
            1 for r in llm_runs if set(r.get("format_events") or []) & _INVALID_PLAN_REASONS
        ),
        "baseline": _FORMAT_BASELINE,
    }


def _type_binary(scored: list[dict[str, Any]], positive: frozenset[str]) -> dict[str, Any]:
    """유형 집합 하나를 양성으로 본 정밀도·재현율(`_prf` — 분모 0이면 None)."""
    counts = {"tp": 0, "fp": 0, "fn": 0}
    for r in scored:
        got_pos = r.get("request_type_got") in positive
        exp_pos = r.get("request_type_expected") in positive
        counts["tp"] += got_pos and exp_pos
        counts["fp"] += got_pos and not exp_pos
        counts["fn"] += exp_pos and not got_pos
    return _prf(counts)


def _type_scoring_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """유형 채점 요약 — 다단계 3종 정밀도 · 12종 혼동 행렬 · 틀린 루틴 적용 · 채점 불가 종류."""
    scored = [r for r in results if r.get("request_type_scored")]
    order = [code for code, _ in _TYPE_CATALOG] + [_TYPE_INVALID]
    cells: dict[str, Counter[str]] = defaultdict(Counter)
    for r in scored:
        cells[str(r["request_type_expected"])][r.get("request_type_got") or _TYPE_INVALID] += 1
    confusion = {exp: {got: cells[exp][got] for got in order if cells[exp][got]}
                 for exp in order if exp in cells}
    observed = Counter(
        r.get("request_type_got") or _TYPE_INVALID for r in results
        if r.get("request_type_unscored_kind") in ("no_expected_type", "declared_unscored")
    )
    multi = frozenset(_MULTI_STEP_TYPES)
    correct = sum(1 for r in scored if r.get("request_type_correct"))
    return {
        "scored_runs": len(scored),
        "scored_items": len({r.get("id") for r in scored}),
        "correct": correct,
        "accuracy": _rate(correct, len(scored)),
        "multi_step": {code: _type_binary(scored, frozenset({code})) for code in _MULTI_STEP_TYPES},
        "multi_step_family": _type_binary(scored, multi),
        # 틀린 루틴 적용(A-3) — 다단계 유형을 냈는데 기대와 다르다(다단계끼리 혼동 포함)
        "wrong_routine": sum(
            1 for r in scored
            if r.get("request_type_got") in multi
            and r.get("request_type_got") != r.get("request_type_expected")
        ),
        "confusion": confusion,
        "unscored": dict(Counter(
            str(r["request_type_unscored_kind"]) for r in results
            if r.get("request_type_unscored_kind")
        )),
        "observed_unscored": {got: observed[got] for got in order if observed[got]},
    }


def summarize_type_experiment(
    results: list[dict[str, Any]], *, repeat: int = 1, prompt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """실험 요약. 합계는 run 합산이고 신뢰도는 pass^k로 본다.

    - format: LLM이 불린 run 전부(채점 불가 선언 포함 — 형식 판정은 기대값이 필요 없다)
    - tasks: 노드 모드 요약(`summarize_decomposition_node`)을 사전 처리 단락 run을 빼고 재사용
    """
    llm_runs = [r for r in results if r.get("llm_attempts") and "type_output" in r]
    pre = [r for r in results if r.get("outcome") == _OUTCOME_PREPROCESSED]
    node = summarize_decomposition_node(
        [r for r in results if r.get("outcome") != _OUTCOME_PREPROCESSED], repeat=repeat,
    )
    typed = [r for r in results if r.get("request_type_scored")]
    return {
        "mode": "type_experiment",
        "caveat": _TYPE_EXPERIMENT_CAVEAT,
        "prompt": prompt or {},
        "items": len({r.get("id") for r in results}),
        "runs": repeat,
        "llm_runs": len(llm_runs),
        "preprocessed": {
            "runs": len(pre),
            "items": len({r.get("id") for r in pre}),
            "plan_paths": dict(Counter(str(r.get("plan_path")) for r in pre)),
            "expected_types_not_scored": sorted(
                {str(r["request_type_expected"]) for r in pre if "request_type_expected" in r}
            ),
        },
        "format": _type_format_summary(llm_runs),
        "request_type": _type_scoring_summary(results),
        "tasks": {k: node[k] for k in (
            "total", "passed", "failed", "format_errors", "errors", "sequential_cases",
            "sequential_preserved", "single_false_split", "node", "edge",
        )},
        "pass_hat_k": pass_hat_k(results, repeat),
        "type_pass_hat_k": pass_hat_k(
            [{"id": r.get("id"), "passed": r.get("request_type_correct")} for r in typed], repeat,
        ),
    }


def validate_type_experiment_gold(items: list[dict[str, Any]]) -> list[str]:
    """분해 골드 정합성 + 기대 유형이 실험 카탈로그(§4.2 12종) 안인지."""
    errs = validate_decomposition_gold(items)
    for it in items:
        rt = (it.get("expect") or {}).get("request_type")
        if isinstance(rt, str) and rt.strip() and rt not in _TYPE_CODES:
            errs.append(f"{it.get('id', '?')}: expect.request_type {rt!r}가 실험 유형 카탈로그"
                        "(§4.2 12종) 밖이다")
    return errs


def _verdict_type_experiment(summary: dict[str, Any]) -> int:
    """실험 종료 판정 — 회귀 게이트가 아니다. 측정이 성립하지 않을 때만 1."""
    fails: list[str] = []
    if summary["tasks"]["errors"]:
        fails.append(f"LLM 호출 실패·노드 예외 {summary['tasks']['errors']}건 — 측정 무효")
    if not summary["llm_runs"]:
        fails.append("LLM 경로 run 0건 — 측정한 것이 없다")
    if fails:
        print("측정 불성립:", file=sys.stderr)
        for f in fails:
            print(f"  - {f}", file=sys.stderr)
        return 1
    return 0


# ──────────────────────────────────────────────
# 라우팅 골드 — 런타임 프롬프트 조건 채점 (plans/121 TP-0.6)
# ──────────────────────────────────────────────

#: 이 모드가 **런타임이 아닌** 부분(plans/121 §12.5 TP-0.2·0.6 행) — 출력마다 싣는다.
_RUNTIME_CAVEAT = (
    "런타임 프롬프트 조건 모드 — 완전한 런타임이 아니다: ①분해가 재작성한 task sub_query 대신 "
    "골드 원문을 분류한다 ②위치 힌트 고정·직전 턴 승계·존 역질문 등 분류 뒤 게이트를 적용하지 "
    "않는다 ③DB 설명은 실시간 Redis가 아니라 스냅샷이다(plans/121 §12.5)"
)
#: 2단(기준 경로) `classify_dbs`의 `_llm_classify` 호출 규약 — 장애 진단 절·계획 신호 없음.
_RUNTIME_CALL_CONVENTION = "subagents.classify_dbs 규약(fault_diagnosis 미전달 · 계획 신호 없음)"
_INACTIVE_EXPECTED_REASON = "기대 DB가 활성 집합 밖 — 이 조건에서 고를 수 없다"


def parse_active_dbs(raw: str) -> list[str]:
    """`--active-dbs` 값(쉼표 구분) → 등록 DB id 목록. 모르는 id·중복·빈 목록은 거부한다."""
    from src.routing.domain_config import DB_DOMAINS

    known = {d.db_id for d in DB_DOMAINS}
    ids = [p.strip() for p in (raw or "").split(",") if p.strip()]
    if not ids:
        raise ValueError("--active-dbs가 비었다")
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise ValueError(f"등록되지 않은 db_id {unknown} (등록: {', '.join(sorted(known))})")
    if len(set(ids)) != len(ids):
        raise ValueError(f"중복 db_id: {ids}")
    return ids


def load_db_descriptions(path: str | Path) -> dict[str, str]:
    """DB 설명 스냅샷 파일(YAML·JSON — `{db_id: 설명}`)을 읽는다. 로컬 값만 둔다(운영 설명 금지)."""
    from src.routing.domain_config import DB_DOMAINS

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("DB 설명 스냅샷은 {db_id: 설명} 매핑이어야 한다")
    known = {d.db_id for d in DB_DOMAINS}
    errs = [f"등록되지 않은 db_id {k!r}" for k in data if k not in known]
    errs += [f"{k}: 설명은 비어 있지 않은 문자열" for k, v in data.items()
             if not (isinstance(v, str) and v.strip())]
    if errs:
        raise ValueError("; ".join(errs))
    return {str(k): str(v) for k, v in data.items()}


def runtime_conditions(
    cfg: Any,
    *,
    active_dbs: list[str] | None = None,
    db_descriptions: dict[str, str] | None = None,
    descriptions_source: str | None = None,
) -> dict[str, Any]:
    """런타임 프롬프트 조건 — 활성 DB 집합 · DB 설명 스냅샷 · 플래그 상태.

    플래그는 `_llm_classify`가 `load_config()`에서 직접 읽는다(정적 모드도 같다) — 여기서는
    그 해석값을 기록한다. 활성 집합을 주입하지 않으면 설정 해석값(`ACTIVE_DB_IDS`)이다.
    """
    descs = dict(db_descriptions or {})
    digest = hashlib.sha256(
        json.dumps(descs, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    router = getattr(cfg, "router", None)
    return {
        "active_db_ids": list(active_dbs) if active_dbs is not None
        else list(cfg.multi_db.get_active_db_ids()),
        "active_db_source": "주입(--active-dbs · 존 id 주입)" if active_dbs is not None
        else "설정 해석값(ACTIVE_DB_IDS)",
        "db_descriptions": descs,
        "db_descriptions_source": descriptions_source or "없음(스냅샷 미지정)",
        "db_descriptions_sha256": digest[:16] if descs else None,
        "flags": {
            "router.capability_ownership_enabled":
                bool(getattr(router, "capability_ownership_enabled", False)),
            "router.two_stage_enabled": bool(getattr(router, "two_stage_enabled", False)),
            "router.unknown_enabled": bool(getattr(router, "unknown_enabled", False)),
            "structured_output_backend": getattr(cfg, "structured_output_backend", "none"),
        },
        "call": _RUNTIME_CALL_CONVENTION,
    }


def _conditions_record(conditions: dict[str, Any]) -> dict[str, Any]:
    """출력용 조건 — 설명 본문은 싣지 않는다(출처·건수·지문만)."""
    record = {k: v for k, v in conditions.items() if k != "db_descriptions"}
    record["db_descriptions_count"] = len(conditions.get("db_descriptions") or {})
    return record


#: 런타임 모드 채점 불가 종류 — 골드 선언 · 기대 DB 비활성(이 조건에서 고를 수 없다).
_UNSCORED_DECLARED = "declared"
_UNSCORED_INACTIVE = "inactive_expected_db"


def runtime_unscored_reason(item: dict, active_db_ids: list[str]) -> tuple[str, str] | None:
    """런타임 조건에서 이 항목을 채점할 수 없으면 (종류, 사유). 골드 선언이 우선이다."""
    if item.get("unscored"):
        return _UNSCORED_DECLARED, str(item["unscored"])
    missing = sorted(set((item.get("expect") or {}).get("databases") or []) - set(active_db_ids))
    if missing:
        return _UNSCORED_INACTIVE, f"{_INACTIVE_EXPECTED_REASON}: {', '.join(missing)}"
    return None


def _first_db_fallback(item: dict, got: dict, active_db_ids: list[str]) -> str | None:
    """런타임 `classify_dbs`라면 첫 활성 DB로 **조용히** 폴백했을 DB(관찰 · 채점과 무관).

    2단 `classify_dbs`는 관련도 게이트(`MIN_RELEVANCE_SCORE`)를 통과한 후보가 없으면 첫 활성 DB를
    쓴다(plans/121 F-11 침묵 대체). SQL 담당(data_query·alarm_query)일 때만 그 경로를 탄다.
    """
    from src.routing.semantic_router import MIN_RELEVANCE_SCORE

    intent = (item.get("expect") or {}).get("intent") or got.get("intent")
    if intent not in ("data_query", "alarm_query") or not active_db_ids:
        return None
    scores = [d.get("relevance_score") for d in got.get("databases") or []]
    if any(isinstance(s, (int, float)) and s >= MIN_RELEVANCE_SCORE for s in scores):
        return None
    return active_db_ids[0]


async def run_runtime(
    items: list[dict], *, conditions: dict[str, Any], llm: Any = None, repeat: int = 1,
) -> list[dict]:
    """라우팅 골드를 런타임 프롬프트 조건으로 분류·채점한다.

    채점 규칙은 정적 모드 `judge` 그대로다 — 두 모드의 점수 차이는 프롬프트 조건에서만 온다.
    채점 불가 항목도 분류는 돌려 `observed`로 남긴다(침묵 대체 관찰).
    """
    from src.config import load_config
    from src.llm import create_llm
    from src.routing.domain_config import DB_DOMAINS

    sr = importlib.import_module("src.routing.semantic_router")
    if llm is None:
        llm = create_llm(load_config())
    active = list(conditions["active_db_ids"])
    # 2단 `classify_dbs`와 같은 규칙 — 레지스트리 순서의 활성 도메인만 프롬프트에 싣는다.
    domains = [d for d in DB_DOMAINS if d.db_id in active]
    descriptions = conditions.get("db_descriptions") or {}
    results: list[dict] = []
    for run_no in range(1, repeat + 1):
        for it in items:
            tag = {"suite": it.get("suite", _CORE_SUITE), "run": run_no}
            try:
                got = await sr._llm_classify(
                    llm, it["query"], domains, db_descriptions=descriptions,
                )
            except Exception as e:  # noqa: BLE001 — 개별 실패가 전체를 막지 않는다
                results.append({
                    "id": it.get("id"), "query": it.get("query"), "critical": it.get("critical"),
                    **tag, "error": f"{type(e).__name__}: {e}", "passed": False,
                })
                continue
            fallback = _first_db_fallback(it, got, active)
            unscorable = runtime_unscored_reason(it, active)
            if unscorable:
                kind, reason = unscorable
                results.append({
                    "id": it.get("id"), "query": it.get("query"), "critical": it.get("critical"),
                    **tag, "unscored": reason, "unscored_kind": kind, "passed": None,
                    "first_db_fallback": fallback,
                    "observed": {"intent": got.get("intent"),
                                 "databases": [d.get("db_id") for d in got.get("databases") or []]},
                })
                continue
            results.append({**judge(it, got), **tag, "first_db_fallback": fallback})
    return results


def summarize_runtime(results: list[dict], *, conditions: dict[str, Any], repeat: int = 1) -> dict:
    """런타임 모드 요약 — core·system을 **따로** 요약하고 채점 불가를 별도로 센다(run 합산)."""
    scored = [r for r in results if not r.get("unscored")]
    unscored = [r for r in results if r.get("unscored")]
    declared = [r for r in unscored if r.get("unscored_kind") == _UNSCORED_DECLARED]
    return {
        "mode": "runtime_prompt_conditions",
        "caveat": _RUNTIME_CAVEAT,
        "conditions": _conditions_record(conditions),
        "runs": repeat,
        "core": summarize([r for r in scored if r.get("suite") == _CORE_SUITE]),
        "system": summarize([r for r in scored if r.get("suite") != _CORE_SUITE]),
        "unscored": {
            "runs": len(unscored),
            "items": len({r.get("id") for r in unscored}),
            "declared": len(declared),
            _UNSCORED_INACTIVE: len(unscored) - len(declared),
        },
        "first_db_fallback": sum(1 for r in results if r.get("first_db_fallback")),
        "pass_hat_k": pass_hat_k(scored, repeat),
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
    ap.add_argument("--node", action="store_true",
                    help="(--decomposition과 함께) intent_planner 노드 단위로 평가한다(plans/121 "
                         "TP-0.2) — 사전 처리 계층 A 포함 · 골드 parsed로 파서 출력 동결 · "
                         "node/edge F1 · 형식 오류율 분리 · 작업 유형 채점 불가 표기")
    ap.add_argument("--type-experiment", action="store_true",
                    help="(--decomposition --node와 함께) 작업 유형 분류 사전 실험(plans/121 "
                         "TP-0.8 · G-31 입력) — 분해만 초안 1회 호출(현행 분해 프롬프트 + 유형 "
                         "카탈로그 12종 + 확장 출력 스키마 · 스크립트 실험 상수). 다단계 3종 "
                         "정밀도 · 12종 혼동 행렬 · 확장 출력 형식 오류율 · tasks node/edge F1 · "
                         "pass^k. 회귀 게이트가 아니다 — 종료 코드는 측정 성립 여부만 본다")
    ap.add_argument("--type-draft", choices=TYPE_EXPERIMENT_DRAFTS, default=None,
                    help=f"(--type-experiment 전용) 초안 선택. 기본 {TYPE_EXPERIMENT_DRAFT_ID}"
                         f"(확장 4키를 「출력 형식」 골격에) · {TYPE_EXPERIMENT_DRAFT_1}은 비교용 "
                         "재현(「출력 형식」 앞 절 — plans/121 §14.3)")
    ap.add_argument("--runtime-prompt-conditions", action="store_true",
                    help="라우팅 골드를 런타임 프롬프트 조건(활성 DB 집합 · DB 설명 스냅샷 · "
                         "플래그 상태)으로 채점한다(plans/121 TP-0.6). 완전한 런타임이 "
                         "아니다 — 재작성 sub_query·위치 힌트 고정·승계·후단 게이트 미적용. "
                         "정적 core 점수는 기본 모드가 따로 유지한다")
    ap.add_argument("--active-dbs", metavar="IDS",
                    help="(런타임 프롬프트 조건 모드) 활성 DB 집합 주입(쉼표 구분 · 존 id 주입 — "
                         "예: 폐쇄망 존 DB를 로컬에서 흉내). 없으면 설정 해석값(ACTIVE_DB_IDS)")
    ap.add_argument("--db-descriptions", metavar="PATH",
                    help="(런타임 프롬프트 조건 모드) DB 설명 스냅샷 파일(YAML·JSON "
                         "{db_id: 설명} · 로컬 값만). 없으면 설명 없이 채점한다")
    ap.add_argument("--repeat", type=int, default=1,
                    help="(신규 모드 전용) 같은 골드를 N회 돌려 pass^N(모든 회차 통과)을 센다. "
                         "합계는 run 합산이고 --tolerate도 run 합산 실패에 적용된다")
    args = ap.parse_args()
    _check_mode_args(ap, args)

    if args.decomposition:
        if args.type_experiment:
            return _main_type_experiment(args)
        if args.node:
            return _main_decomposition_node(args)
        return _main_decomposition(args)
    if args.runtime_prompt_conditions:
        return _main_runtime(args)

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


def _check_mode_args(ap: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """신규 인자 조합 검사 — 신규 인자를 쓰지 않으면 아무것도 하지 않는다(기존 모드 불변)."""
    if args.node and not args.decomposition:
        ap.error("--node는 --decomposition과 함께 쓴다")
    if args.type_experiment and not (args.decomposition and args.node):
        ap.error("--type-experiment는 --decomposition --node와 함께 쓴다")
    if args.type_experiment and args.tolerate:
        ap.error("--tolerate는 --type-experiment에서 쓰지 않는다(회귀 게이트 아님 — 종료 코드는 "
                 "측정 성립 여부)")
    if args.type_draft is not None and not args.type_experiment:
        ap.error("--type-draft는 --type-experiment에서만 쓴다")
    if args.runtime_prompt_conditions and args.decomposition:
        ap.error("--runtime-prompt-conditions는 라우팅 골드 모드다"
                 "(--decomposition과 함께 쓸 수 없다)")
    if (args.active_dbs is not None or args.db_descriptions is not None) \
            and not args.runtime_prompt_conditions:
        ap.error("--active-dbs·--db-descriptions는 --runtime-prompt-conditions에서만 쓴다")
    if args.repeat < 1:
        ap.error("--repeat는 1 이상")
    if args.repeat > 1 and not (args.node or args.runtime_prompt_conditions):
        ap.error("--repeat는 신규 모드(--decomposition --node · --runtime-prompt-conditions)에서만 "
                 "쓴다 — 기존 모드 출력은 바꾸지 않는다")


def _write_out(path: str, summary: dict, results: list[dict]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"저장: {path}")


def _main_decomposition_node(args: argparse.Namespace) -> int:
    """`--decomposition --node` — 노드 단위 평가(plans/121 TP-0.2).

    게이트·목업 규약은 기존 모드와 같다.
    """
    from src.config import load_config

    items = load_gold(_DECOMP_GOLD, suites=None)
    errs = validate_decomposition_gold(items)
    if errs:
        print("분해 골든셋 정합성 오류:", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"분해 골든셋 {len(items)}건 정합성 OK "
          f"(core {sum(1 for i in items if i.get('suite', _CORE_SUITE) == _CORE_SUITE)} · "
          f"노드 전용 {sum(1 for i in items if i.get('suite') == 'node')} · "
          f"순차 {sum(1 for i in items if i.get('critical') == 'sequential')} · "
          f"채점 불가 선언 {sum(1 for i in items if i.get('unscored'))} · "
          f"파서 출력 동결 {sum(1 for i in items if 'parsed' in i)} · "
          f"상태 주입 {sum(1 for i in items if i.get('state'))})")
    cfg = load_config()
    composite = cfg.composite
    print(f"[node] intent_planner 노드 단위(사전 처리 계층 A 포함) · provider={cfg.llm.provider} "
          f"plan_dag_validation={composite.plan_dag_validation_enabled} "
          f"sequential_replan={composite.sequential_replan_enabled} "
          f"task_frame={composite.task_frame_enabled} "
          f"investigation={composite.investigation_enabled} "
          f"capability_ownership={cfg.router.capability_ownership_enabled} "
          f"structured_backend={cfg.structured_output_backend} · repeat={args.repeat}")
    if args.dry_run:
        print("[dry-run] 실 호출 없음.")
        return 0
    if args.mock is not None:
        print(f"[mock] FabriX KBGenAI 목업 · fault={args.mock} · 실 호출 0건 — 목업에 분해 대본이 "
              "없어 LLM 경로 항목은 형식 오류로 잡혀야 한다(형식 오류 검출 확인용)")
        from tests.mocks.fabrix_kbgenai_mock import make_llm, mock_kbgenai

        with mock_kbgenai(fault=args.mock):
            results = asyncio.run(run_decomposition_node(items, llm=make_llm(), cfg=cfg,
                                                         repeat=args.repeat))
    else:
        _require_optin()
        results = asyncio.run(run_decomposition_node(items, cfg=cfg, repeat=args.repeat))
    summary = summarize_decomposition_node(results, repeat=args.repeat)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for r in results:
        if r.get("outcome") in (_OUTCOME_FAILED, _OUTCOME_FORMAT, _OUTCOME_ERROR):
            print(f"  ✗ {r.get('id')}#{r.get('run')} [{r.get('outcome')}]: {r.get('error') or r}",
                  file=sys.stderr)
    # 채점하지 못한 판정은 통과가 아니다 — 건수를 눈에 띄게 남긴다.
    if summary["unscored"]["runs"]:
        u = summary["unscored"]
        print(f"  ※ 채점 불가 선언 {u['items']}건(run {u['runs']}) — 합격·불합격에 넣지 않았다",
              file=sys.stderr)
    if summary["request_type"]["declared_items"]:
        print(f"  ※ 작업 유형 {summary['request_type']['declared_items']}건 — "
              f"{_REQUEST_TYPE_UNSCORED_REASON}", file=sys.stderr)
    if args.out:
        _write_out(args.out, summary, results)
    return _verdict_decomposition_node(summary, tolerate=args.tolerate)


def _type_experiment_issues(r: dict[str, Any]) -> list[str]:
    """run 1건의 문제 목록(stderr 표기용) — 호출 실패 · 형식 · 유형 · 구조."""
    issues: list[str] = []
    out = r.get("type_output") or {}
    if r.get("error"):
        issues.append(str(r["error"]))
    elif out.get("status") == "parse_invalid":
        issues.append("형식: 파싱 불가·tasks 무효")
    elif out.get("status") == "field_error":
        issues.append(f"형식: 누락 {out.get('missing')} · 열거 밖 {out.get('out_of_enum')}")
    if r.get("request_type_correct") is False:
        issues.append(f"유형: 기대 {r.get('request_type_expected')} → "
                      f"출력 {r.get('request_type_got') or _TYPE_INVALID}")
    if r.get("outcome") in (_OUTCOME_FAILED, _OUTCOME_FORMAT):
        issues.append(f"구조: {r.get('outcome')}")
    return issues


def _main_type_experiment(args: argparse.Namespace) -> int:
    """`--decomposition --node --type-experiment` — 작업 유형 사전 실험(plans/121 TP-0.8).

    게이트·목업 규약은 기존 모드와 같다(실 호출은 `_require_optin` 뒤에만).
    """
    from src.config import load_config

    items = load_gold(_DECOMP_GOLD, suites=None)
    errs = validate_type_experiment_gold(items)
    if errs:
        print("분해 골든셋 정합성 오류:", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1
    typed = [i for i in items
             if (i.get("expect") or {}).get("request_type") and not i.get("unscored")]
    multi = [i for i in typed if i["expect"]["request_type"] in _MULTI_STEP_TYPES]
    print(f"분해 골든셋 {len(items)}건 정합성 OK (기대 유형 라벨 {len(typed)}건 — 다단계 "
          f"{len(multi)} · 사용자 검수 전 · 채점 불가 선언 "
          f"{sum(1 for i in items if i.get('unscored'))})")
    cfg = load_config()
    draft = args.type_draft or TYPE_EXPERIMENT_DRAFT_ID
    meta = type_experiment_prompt_meta(cfg, draft)
    composite = cfg.composite
    print(f"[type-experiment] 초안 {meta['draft']} · 시스템 프롬프트 {meta['chars']}자(현행 "
          f"{meta['base_chars']}자 +{meta['added_chars']}자 · +{meta['added_pct']}%) · sha256 "
          f"{meta['sha256']} · 유형 {meta['types']} · 답변 영역 {meta['areas']} · LLM 1회/항목 · "
          f"provider={cfg.llm.provider} task_frame={composite.task_frame_enabled} "
          f"investigation={composite.investigation_enabled} "
          f"capability_ownership={cfg.router.capability_ownership_enabled} · repeat={args.repeat}")
    if cfg.structured_output_backend != "none":
        print(f"  ※ structured_output_backend={cfg.structured_output_backend} — 실험은 JSON "
              "경로로 고정한다(확장 스키마 구조화 모델 없음)")
    print(f"  ⚠ {_TYPE_EXPERIMENT_CAVEAT}")
    if args.dry_run:
        print("[dry-run] 실 호출 없음.")
        return 0
    if args.mock is not None:
        print(f"[mock] FabriX KBGenAI 목업 · fault={args.mock} · 실 호출 0건 — 목업에 분해 대본이 "
              "없어 LLM 경로 항목은 파싱 불가로 잡혀야 한다(형식 오류 검출 확인용)")
        from tests.mocks.fabrix_kbgenai_mock import make_llm, mock_kbgenai

        with mock_kbgenai(fault=args.mock):
            llm = make_llm()  # type: ignore[no-untyped-call]
            results = asyncio.run(run_type_experiment(items, llm=llm, cfg=cfg,
                                                      repeat=args.repeat, draft=draft))
    else:
        _require_optin()
        results = asyncio.run(run_type_experiment(items, cfg=cfg, repeat=args.repeat,
                                                  draft=draft))
    summary = summarize_type_experiment(results, repeat=args.repeat, prompt=meta)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for r in results:
        issues = _type_experiment_issues(r)
        if issues:
            print(f"  ✗ {r.get('id')}#{r.get('run')}: " + " / ".join(issues), file=sys.stderr)
    rt = summary["request_type"]
    if rt["unscored"]:
        print(f"  ※ 유형 채점 불가 {sum(rt['unscored'].values())} run {rt['unscored']} — "
              "합격·불합격에 넣지 않았다", file=sys.stderr)
    print(f"  ※ 유형 채점 대상 {rt['scored_items']}항목(다단계 기대 {len(multi)}항목) — 유형 "
          "정확도·정밀도 판정에는 모자란 규모다(TP-0.2 골드 확충 선행)", file=sys.stderr)
    if args.out:
        _write_out(args.out, summary, results)
    return _verdict_type_experiment(summary)


def _main_runtime(args: argparse.Namespace) -> int:
    """`--runtime-prompt-conditions` — 라우팅 골드를 런타임 프롬프트 조건으로 채점.

    plans/121 TP-0.6. 게이트·목업 규약은 기존 모드와 같다.
    """
    from src.config import load_config

    items = load_gold(suites=None)
    errs = validate_gold(items)
    try:
        active = parse_active_dbs(args.active_dbs) if args.active_dbs is not None else None
    except ValueError as e:
        errs.append(f"--active-dbs: {e}")
        active = None
    descriptions: dict[str, str] = {}
    if args.db_descriptions is not None:
        try:
            descriptions = load_db_descriptions(args.db_descriptions)
        except (OSError, ValueError, yaml.YAMLError) as e:
            errs.append(f"--db-descriptions: {e}")
    if errs:
        print("골든셋·조건 정합성 오류:", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1
    cfg = load_config()
    conditions = runtime_conditions(cfg, active_dbs=active, db_descriptions=descriptions,
                                    descriptions_source=args.db_descriptions)
    active_ids = conditions["active_db_ids"]
    if not active_ids:
        # 런타임(`classify_dbs`)은 활성 DB가 없으면 분류하지 않고 레거시 단일 DB로 간다.
        print(
            "활성 DB가 없다 — 런타임은 분류를 부르지 않는다(--active-dbs로 주입)", file=sys.stderr
        )
        return 1
    unscorable = {str(i.get("id")): runtime_unscored_reason(i, active_ids) for i in items}
    n_core = sum(1 for i in items if i.get("suite", _CORE_SUITE) == _CORE_SUITE)
    print(f"라우팅 골든셋 {len(items)}건 정합성 OK (core {n_core} · system {len(items) - n_core} · "
          f"채점 불가 선언 {sum(1 for i in items if i.get('unscored'))})")
    print(f"[런타임 프롬프트 조건] 활성 DB={','.join(active_ids) or '(없음)'} "
          f"({conditions['active_db_source']}) · DB 설명 {len(descriptions)}건 "
          f"({conditions['db_descriptions_source']}) · 플래그 "
          + " ".join(f"{k}={v}" for k, v in conditions["flags"].items()))
    n_declared = sum(1 for i in items if i.get("unscored"))
    n_unscorable = sum(1 for v in unscorable.values() if v)
    print(f"  이 조건에서 채점 가능 {len(items) - n_unscorable}건 · 채점 불가 {n_unscorable}건"
          f"(선언 {n_declared} · 기대 DB 비활성 {n_unscorable - n_declared})")
    print(f"  ⚠ {_RUNTIME_CAVEAT}")
    if args.dry_run:
        print(f"[dry-run] 실 호출 없음. provider={cfg.llm.provider}")
        return 0
    if args.mock is not None:
        print(f"[mock] FabriX KBGenAI 목업 · fault={args.mock} · 실 호출 0건")
        from tests.mocks.fabrix_kbgenai_mock import make_llm, mock_kbgenai

        with mock_kbgenai(fault=args.mock):
            results = asyncio.run(run_runtime(items, conditions=conditions, llm=make_llm(),
                                              repeat=args.repeat))
    else:
        _require_optin()
        results = asyncio.run(run_runtime(items, conditions=conditions, repeat=args.repeat))
    summary = summarize_runtime(results, conditions=conditions, repeat=args.repeat)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for r in results:
        if not r.get("unscored") and not r.get("passed"):
            print(f"  ✗ {r.get('id')}#{r.get('run')}: {r.get('error') or r}", file=sys.stderr)
    if summary["unscored"]["runs"]:
        print(f"  ※ 채점 불가 {summary['unscored']['items']}건(run {summary['unscored']['runs']} — "
              f"선언 {summary['unscored']['declared']} · 기대 DB 비활성 "
              f"{summary['unscored']['inactive_expected_db']}) — 합격·불합격에 넣지 않았다",
              file=sys.stderr)
    if summary["first_db_fallback"]:
        print(f"  ※ 런타임이면 첫 활성 DB로 조용히 폴백했을 분류 {summary['first_db_fallback']}건"
              "(관련도 게이트 통과 후보 0 — 관찰)", file=sys.stderr)
    print(f"  ⚠ {_RUNTIME_CAVEAT}", file=sys.stderr)
    if args.out:
        _write_out(args.out, summary, results)
    scored = [r for r in results if not r.get("unscored")]
    return _verdict(summarize(scored), tolerate=args.tolerate)


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
