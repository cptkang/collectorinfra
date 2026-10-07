"""`python -m scripts.itam_bench` — ITAM 질의 벤치 CLI (plans/135 §3.1).

    --dry-run       시나리오·프롬프트 린트·실행 계획만 (LLM·DB 0)
    --check-oracle  정답 SQL만 읽기 전용으로 실행 (LLM 0 · MCP itam 소스 필요)
    --run           벤치 서버 기동 → 로그인 → 시나리오 → 산출물 (두 평면 비과금일 때만 ·
    D-127·D-240)
    --compare A B   두 run 의 시나리오별 전이 (LLM·DB 0)
    --build-assets RUN  반출 run → itam 프로필·유사어 시드·시드 스키마 캐시 직접 쓰기
                    (LLM·DB 0 · D-311 ③ · plans/140 W3)

기본 동작(인자 없음)은 `--dry-run` 이다 — 모르고 실행해도 LLM·DB 를 부르지 않는다.

`--run` 흐름: 사전 점검(과금 평면 0 · MLX 생성 가능 · itam 활성 · 오라클 경로) → 벤치 서버(프로파일
주입 · 측정 수신기 설치 · 사다리 확정 단 대조 · 설정 에코) → 로그인 · 계정 인가 → 시나리오(새
스레드) → 턴마다 결과· 실행 SQL·스키마 맥락 수집 → 오라클 대조 → 분석·분류 → 위생 → 누출 관문 →
`results/itam_bench/<run_id>/`. 서버 원시 로그·체크포인트·측정 수신 파일·오라클 로그는 세션 임시
디렉터리에 두고 끝나면 지운다(D-301
④).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import shutil
import socket
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

import yaml

from . import (
    CLOSED_POLICY_PATH,
    CLOSED_SCENARIOS_PATH,
    DB_ID,
    POLICY_PATH,
    REPO_ROOT,
    RESULTS_ROOT,
    SCENARIOS_PATH,
    TRANSCRIPT_PATH,
)
from . import catalog as cat
from . import judge as jd
from . import redact as rd

KST = timezone(timedelta(hours=9))
#: 프로파일 → 확정돼야 할 사다리 단(D-251 기준 2단 · 3단은 비교 arm).
EXPECTED_TIER = {"tier2_intent": "intent_orchestration", "tier3_router": "semantic_router"}
ENTRY_MODULE = "scripts.itam_bench._serve"


def say(text: str = "") -> None:
    print(text, flush=True)


def _now() -> datetime:
    return datetime.now(KST).replace(microsecond=0)


def _split(raw: str | None) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def _load(args: argparse.Namespace) -> tuple[cat.ColumnPolicy, list[cat.Scenario]] | None:
    try:
        policy = cat.load_policy(Path(args.policy))
        scenarios = cat.load_scenarios(Path(args.scenarios), policy)
        return policy, cat.select(scenarios, env=args.env, only=_split(args.only))
    except cat.CatalogError as exc:
        say(f"[중단] 시나리오·정책 검증 실패 {len(exc.errors)}건:")
        for error in exc.errors:
            say(f"  - {error}")
    except ValueError as exc:
        say(f"[중단] {exc}")
    return None


def _oracle_turns(
    scenarios: list[cat.Scenario],
) -> list[tuple[cat.Scenario, cat.Turn, dict[str, Any]]]:
    return [(s, t, t.oracle) for s in scenarios for t in s.turns if t.oracle is not None]


# --- --dry-run · --check-oracle (W0) -------------------------------------------------


def cmd_dry_run(args: argparse.Namespace) -> int:
    loaded = _load(args)
    if loaded is None:
        return 1
    policy, scenarios = loaded
    oracle_turns = _oracle_turns(scenarios)
    observe_turns = [(s, t) for s in scenarios for t in s.turns if t.observe is not None]
    say(
        f"시나리오   : {len(scenarios)}건 (env={args.env}) · "
        f"턴 {sum(len(s.turns) for s in scenarios)}"
    )
    say(f"판정       : 오라클 {len(oracle_turns)}턴 · 관측(observe) {len(observe_turns)}턴")
    say(f"오라클 정본: {', '.join(sorted({spec['id'] for _s, _t, spec in oracle_turns}))}")
    say(f"범주       : {dict(sorted(Counter(s.category for s in scenarios).items()))}")
    say(f"함정       : {dict(sorted(Counter(t for s in scenarios for t in s.traps).items()))}")
    say(
        f"컬럼 정책  : {policy.scope} · {sum(len(c) for c in policy.tables.values())}항목 · "
        f"카나리아 {len(policy.canary_literals)}+{len(policy.canary_patterns)}패턴"
    )
    say("프롬프트 린트: 통과(테이블·컬럼·SQL 용어·사람 정보 0)")
    say(
        f"실행 계획  : 벤치 서버(프로파일 {args.profile} · "
        f"{EXPECTED_TIER.get(args.profile)} 확정 확인) → "
        "로그인 → 시나리오별 새 스레드 → 결과·실행 SQL·스키마 맥락 → 오라클 대조 → "
        "위생 → 누출 관문 → "
        "results/itam_bench/<run_id>/"
    )
    return 0


def cmd_check_oracle(args: argparse.Namespace) -> int:
    """정답 SQL 만 읽기 전용으로 돌려 오라클이 서는지 본다. 행 원문은 출력하지 않는다(건수만)."""
    loaded = _load(args)
    if loaded is None:
        return 1
    _policy, scenarios = loaded
    from scripts.scenario.oracle import run_oracle
    from src.config import load_config

    cfg = load_config()
    if getattr(cfg, "db_backend", None) == "direct":
        say(
            "[중단] DB_BACKEND=direct — 오라클은 MCP readonly 경로에서만 돈다"
            "(D-003 · plans/122 G-9)"
        )
        return 1
    now = _now()
    anchor = now.isoformat()
    run_id = f"check-{now.strftime('%Y%m%d-%H%M%S')}"
    seen: dict[str, tuple[str, int]] = {}
    failed = 0
    with tempfile.TemporaryDirectory(prefix="itam-bench-oracle-") as tmp:
        log_path = Path(tmp) / "oracle_log.jsonl"
        for scenario, turn, spec in _oracle_turns(scenarios):
            oracle_id = str(spec["id"])
            if oracle_id in seen:
                continue
            outcome = run_oracle(
                spec,
                db_ids=list(spec.get("db_ids") or [DB_ID]),
                anchor_at=anchor,
                run_id=run_id,
                scenario_id=f"{scenario.id}-t{turn.index}",
                log_path=log_path,
                cfg=cfg,
            )
            rows = sum(len(r) for r in (outcome.get("rows_by_db") or {}).values())
            if outcome.get("status") != "ok":
                # 사유(DB 오류 문구)에 계정명·값 조각이 실릴 수 있다 — 화면에도 가린 문구만
                reason = rd.redact_text(
                    str(outcome.get("reason") or "사유 없음"), vault=rd.PiiVault()
                )
                verdict = f"불가 — {reason}"
                failed += 1
            elif rows == 0:
                verdict = "0행 — 정답을 구하지 못한다(시드·날짜 경계 확인)"
                failed += 1
            else:
                verdict = "ok"
            seen[oracle_id] = (verdict, rows)
            say(
                f"  {oracle_id:<8} {verdict:<6} 행 {rows:>3} · "
                f"{outcome.get('elapsed_ms', 0):.0f}ms "
                f"({scenario.id} 턴{turn.index})"
            )
    say(f"오라클 {len(seen)}건 중 실패·0행 {failed}건 (앵커 {anchor})")
    return 1 if failed else 0


# --- 감사 로그 · 측정 수신 꼬리 (W4) ---------------------------------------------------


def _audit_tail_class() -> type:
    from scripts.scenario.runner import SqlAuditTail

    class AuditTail(SqlAuditTail):
        """하네스 `SqlAuditTail` + 실패 사유(`error`). 사용자 칸(`user_id` 등)은 읽지 않는다.

        하네스 산출물(`raw.jsonl`)을 바꾸지 않으려고 하네스 쪽이 아니라 여기서 넓힌다(plans/135
        §2.1).
        """

        def collect(self, since: int, thread_id: str) -> list[dict[str, Any]]:
            if not self.path.exists():
                return []
            self._wait_flushed()
            with open(self.path, "rb") as handle:
                handle.seek(since)
                data = handle.read()
            entries: list[dict[str, Any]] = []
            for line in data.decode("utf-8", errors="replace").splitlines():
                line = line.strip()
                if (
                    not line.startswith("{")
                    or thread_id not in line
                    or "query_executed" not in line
                ):
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("event") != "query_executed" or record.get("thread_id") != thread_id:
                    continue
                entries.append(
                    {
                        key: record.get(name)
                        for key, name in (
                            ("sql", "sql"),
                            ("source", "source_name"),
                            ("row_count", "row_count"),
                            ("success", "success"),
                            ("retry_attempt", "retry_attempt"),
                            ("error", "error"),
                        )
                    }
                )
            return entries

    return AuditTail


class CaptureTail:
    """측정 수신 파일(JSONL)에서 턴 1회의 레코드를 모은다.

    턴 시작 시점 크기부터 읽고 thread_id 로 거른다.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def mark(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def collect(self, since: int, thread_id: str) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with open(self.path, "rb") as handle:
            handle.seek(since)
            data = handle.read()
        records = []
        for line in data.decode("utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("thread_id") == thread_id:
                records.append(record)
        return records


# --- 턴 루프 (W4) -------------------------------------------------------------------


class Client(Protocol):
    def send(self, endpoint: str, payload: dict[str, Any]) -> Any: ...
    def download_csv(self, query_id: str) -> dict[str, Any]: ...


class Tail(Protocol):
    def mark(self) -> int: ...
    def collect(self, since: int, thread_id: str) -> list[dict[str, Any]]: ...


#: (명세, 앵커, 오라클 태그용 시나리오 id) → `run_oracle` 결과.
OracleRunner = Callable[[Mapping[str, Any], str, str], dict[str, Any]]


@dataclass
class RunContext:
    run_id: str
    tier: str | None
    policy: cat.ColumnPolicy
    catalog: jd.CatalogFacts
    vault: rd.PiiVault
    oracle: OracleRunner
    repeat: int = 1
    denied_messages: tuple[str, ...] = ()
    counters: Counter[str] = field(default_factory=Counter)


def _value_grade(spec: Mapping[str, Any], policy: cat.ColumnPolicy) -> str | None:
    refs = spec.get("value")
    names = refs if isinstance(refs, list) else ([refs] if refs else [])
    known = [policy.grade(n) for n in names if policy.grade(n) != "unclassified"]
    return cat.strictest(known) if known else None


def _redacted_sqls(
    executed: list[dict[str, Any]], ctx: RunContext, prompt: str, labels: Iterable[str] = ()
) -> list[dict[str, Any]]:
    out = []
    for entry in executed:
        sql = str(entry.get("sql") or "")
        out.append(
            {
                "sql": rd.redact_sql(
                    sql,
                    policy=ctx.policy,
                    vault=ctx.vault,
                    prompt=prompt,
                    allowed_words=labels,
                    catalog_columns=ctx.catalog.columns,
                ),
                "source": entry.get("source"),
                "success": entry.get("success"),
                "row_count": entry.get("row_count"),
                "retry_attempt": entry.get("retry_attempt"),
                "error": (
                    rd.redact_text(str(entry["error"]), vault=ctx.vault, sql=sql, prompt=prompt)
                    if entry.get("error")
                    else None
                ),
            }
        )
    return out


def _oracle_record(
    raw: tuple[dict[str, Any], dict[str, Any] | None, Any, str, str],
    names: list[str],
    summary: Mapping[str, Any],
    sqls: list[str],
    turn: cat.Turn,
    ctx: RunContext,
) -> dict[str, Any]:
    """오라클 판정 → 기록용 칸.

    시스템 쪽 키 열이 일반 컬럼이 아니거나 강등됐으면 키 값은 건수로만 남긴다.
    """
    from scripts.scenario.oracle import _resolve

    spec, outcome, detail, mode, verdict = raw
    entries = dict(zip(names, summary.get("columns") or [], strict=False))
    keys_allowed = True
    for ref in spec.get("key") or []:
        column = _resolve(ref, names)
        entry = entries.get(column) if column else None
        if entry is not None and (entry.get("log_policy") != "general" or entry.get("demoted")):
            keys_allowed = False
    clean = jd.sanitize_detail(
        detail,
        value_grade=_value_grade(spec, ctx.policy),
        keys_allowed=keys_allowed,
        labels={name: entry.get("name") for name, entry in entries.items()},
    )
    if isinstance(clean, dict) and clean.get("reason"):
        clean["reason"] = rd.redact_text(
            str(clean["reason"]), vault=ctx.vault, sql=sqls, prompt=turn.query
        )
    return {
        "id": spec["id"],
        "compare": spec.get("compare"),
        "verdict": verdict,
        "mode": mode,
        "keys_recorded": keys_allowed,
        "oracle_status": (outcome or {}).get("status"),
        "oracle_rows": sum(len(r) for r in ((outcome or {}).get("rows_by_db") or {}).values()),
        "detail": rd.scrub_tree(clean, ctx.vault),
    }


#: 되물음 기록(plans/139 W6-e)에 옮기는 낱말 모양 — 종류는 코드 열거, 후보는 소스·DB id 만.
_CLARIFY_KIND = re.compile(r"^[a-z][a-z_]{0,39}$")
_CLARIFY_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def clarification_record(payload: Any, status: str) -> dict[str, Any] | None:
    """되물음 응답 → 종류 · 칩 표시 여부 · 선택지 수 · 후보 소스 id(plans/139 W6-e).

    칩은 선택지(`options`)가 있을 때만 그려진다(웹 UI `renderZoneClarification`). 후보 id 는
    선택지의 `source`(소스 코드), 없으면 `db_id`·`db_ids`다. 질문 문구·선택지 라벨·원 질의는
    옮기지 않는다. 되물음이 아니면 None.
    """
    if status != "clarification":
        return None
    body = payload if isinstance(payload, Mapping) else {}
    kind = body.get("kind")
    options = [o for o in body.get("options") or [] if isinstance(o, Mapping)]
    candidates: set[str] = set()
    for option in options:
        ids = (
            [option["source"]]
            if option.get("source")
            else [option.get("db_id"), *(option.get("db_ids") or [])]
        )
        candidates |= {i for i in ids if isinstance(i, str) and _CLARIFY_ID.match(i)}
    return {
        "kind": kind if isinstance(kind, str) and _CLARIFY_KIND.match(kind) else None,
        "chips": bool(options),
        "options": len(options),
        "candidate_sources": sorted(candidates),
    }


def run_turn(
    scenario: cat.Scenario,
    turn: cat.Turn,
    repeat: int,
    thread_id: str,
    *,
    client: Client,
    audit: Tail,
    capture: Tail,
    ctx: RunContext,
) -> dict[str, Any]:
    """턴 1회 — 보내고 · 모으고 · 대조하고 · 분석하고 · **가린 레코드**를 돌려준다.

    원값(결과 행·SQL·응답 문장)은 여기서 버린다.
    """
    from scripts.scenario.client import result_unavailable

    audit_mark, capture_mark = audit.mark(), capture.mark()
    anchor = _now().isoformat()
    obs = client.send("stream", {**dict(turn.send), "thread_id": thread_id})
    status = str(getattr(obs, "status", "") or "unknown")
    query_id = getattr(obs, "query_id", None)
    result = (
        client.download_csv(query_id)
        if query_id and status != "clarification"
        else result_unavailable("결과 없음(되묻기·오류·query_id 없음)")
    )
    executed = audit.collect(audit_mark, thread_id)
    captured = capture.collect(capture_mark, thread_id)
    key_refs = scenario.key_columns_for(turn)
    gold = scenario.gold_tables_for(turn)
    context = jd.schema_context(captured, db_id=DB_ID, gold_tables=gold, key_refs=key_refs)

    verdict: str | None = None
    raw_oracle: tuple[dict[str, Any], dict[str, Any] | None, Any, str, str] | None = None
    if turn.oracle is not None:
        spec = turn.oracle
        outcome = ctx.oracle(spec, anchor, f"{scenario.id}-t{turn.index}-r{repeat}")
        verdict, detail, mode = jd.evaluate(
            spec, outcome, result, count_rows_ok=bool(turn.expect.get("count_rows_ok"))
        )
        raw_oracle = (spec, outcome, detail, mode, verdict)

    response = str(getattr(obs, "response", "") or "")
    next_turn = scenario.turns[turn.index] if turn.index < len(scenario.turns) else None
    facts = jd.TurnFacts(
        status=status,
        expected_db_ids=list(turn.expect.get("db_ids") or []),
        observed_db_ids=list(getattr(obs, "db_ids", []) or []),
        executed=executed,
        response=response,
        result=result,
        verdict=verdict,
        observe=turn.observe,
        reply_declared=bool(next_turn and next_turn.reply_to == "clarification"),
        key_refs=key_refs,
        gold_tables=gold,
        schema_context=context,
        access_denied=any(message in response for message in ctx.denied_messages),
    )
    analysis = jd.analyze_sql(facts, ctx.catalog, db_id=DB_ID)
    labels = jd.classify(facts, analysis, db_id=DB_ID)

    # ── 위생: 사람 값 수집(결과 요약)이 SQL·오류 가림보다 먼저다 ── 관문 시험 성립 확인용 — 결과
    # 원문에 카나리아가 몇 번 나왔는가(건수만 · 값은 버린다)
    ctx.counters["canary_in_results"] += ctx.policy.canary_hits(
        json.dumps(result.get("rows") or [], ensure_ascii=False)
    )
    sqls = [str(e.get("sql") or "") for e in executed]
    names = rd.result_column_names(result)
    sources = rd.resolve_result_columns(names, sqls, ctx.policy.column_names())
    summary = rd.summarize_result(
        result, sources=sources, policy=ctx.policy, vault=ctx.vault, prompt=turn.query
    )
    oracle_part = (
        None if raw_oracle is None else _oracle_record(raw_oracle, names, summary, sqls, turn, ctx)
    )
    record = {
        "run_id": ctx.run_id,
        "id": scenario.id,
        "turn": turn.index,
        "repeat": repeat,
        "category": scenario.category,
        "traps": list(scenario.traps),
        "prompt": turn.query or None,
        "reply_to": turn.reply_to,
        "send_keys": sorted(turn.send),
        "expected": {
            "db_ids": list(turn.expect.get("db_ids") or []),
            "key_columns": key_refs,
            "gold_tables": gold,
        },
        "tier": ctx.tier,
        "status": status,
        "http_status": getattr(obs, "http_status", None),
        "db_ids": list(getattr(obs, "db_ids", []) or []),
        "disclosure_kinds": sorted(
            {
                str(d.get("kind"))
                for d in getattr(obs, "disclosures", None) or []
                if isinstance(d, dict) and d.get("kind")
            }
        ),
        "clarification": clarification_record(getattr(obs, "clarification", None), status),
        "executed_sqls": _redacted_sqls(
            executed,
            ctx,
            turn.query,
            [c["name"] for c in summary["columns"] if not str(c["name"]).startswith("열#")],
        ),
        "result": summary,
        "oracle": oracle_part,
        "observe": None
        if turn.observe is None
        else {
            "what": turn.observe.get("what"),
            "no_data": bool(turn.observe.get("no_data")),
            "asked_back": status == "clarification",
            "fabricated": "fabricated" in labels,
        },
        "taxonomy": labels,
        "sql_analysis": analysis,
        "schema_context": context,
        "retries": getattr(obs, "retries", None),
        "response_chars": len(response),
        "latency_ms": round(float(getattr(obs, "wall_ms", 0.0) or 0.0), 1),
        "error": (
            rd.redact_text(str(obs.error), vault=ctx.vault, sql=sqls, prompt=turn.query)
            if getattr(obs, "error", None)
            else None
        ),
    }
    ctx.counters["turns"] += 1
    ctx.counters["sql_observed_turns"] += bool(executed)
    return record


def run_scenarios(
    scenarios: list[cat.Scenario],
    *,
    client: Client,
    audit: Tail,
    capture: Tail,
    ctx: RunContext,
    progress: Callable[[str], None] = say,
) -> list[dict[str, Any]]:
    """시나리오 × 반복 — 시나리오마다 새 스레드.

    선언 없는 되묻기면 그 시나리오의 남은 턴을 보내지 않는다.
    """
    records: list[dict[str, Any]] = []
    for scenario in scenarios:
        for repeat in range(ctx.repeat):
            thread_id = f"itam-bench-{scenario.id}-{secrets.token_hex(3)}"
            for turn in scenario.turns:
                record = run_turn(
                    scenario,
                    turn,
                    repeat,
                    thread_id,
                    client=client,
                    audit=audit,
                    capture=capture,
                    ctx=ctx,
                )
                records.append(record)
                verdict = (record.get("oracle") or {}).get("verdict") or "observe"
                progress(
                    f"  {scenario.id} 턴{turn.index} r{repeat} · {record['status']} · {verdict}"
                    f"{' · ' + ','.join(record['taxonomy']) if record['taxonomy'] else ''}"
                )
                upcoming = scenario.turns[turn.index] if turn.index < len(scenario.turns) else None
                if record["status"] == "clarification" and not (
                    upcoming and upcoming.reply_to == "clarification"
                ):
                    break
    return records


# --- 사전 점검 (W4) ---------------------------------------------------------------


def preflight(cfg: Any, *, check_mlx: bool = True) -> list[str]:
    """실행 전에 멈출 사유 목록(빈 목록이면 진행). LLM 호출 0 — MLX 면 1토큰 생성 점검 1건뿐."""
    from scripts.scenario.preflight import external_planes, mlx_run_blockers

    reasons: list[str] = []
    worker = str(getattr(getattr(cfg, "llm", None), "provider", "") or "").strip().lower()
    orchestrator = (
        str(getattr(getattr(cfg, "orchestrator", None), "provider", "") or "").strip().lower()
    )
    planes = external_planes(worker or "unknown()", orchestrator or "unknown()")
    if planes and os.environ.get("RUN_E2E") != "1":
        # 키 존재로 열지 않는다 — 과금 평면은 사용자 건별 승인 뒤 RUN_E2E=1 로만(D-127·D-240).
        reasons.append(
            f"과금 평면 {planes} — 실행하지 않는다. 로컬 MLX(두 평면 mlx)로 돌리거나 "
            "사용자 승인 뒤 RUN_E2E=1 로 연다(D-127)"
        )
    if check_mlx and "mlx" in (worker, orchestrator):
        reasons += [f"MLX: {c.key} {c.observed} — {c.action}" for c in mlx_run_blockers(cfg)]
    active = list(cfg.multi_db.get_active_db_ids()) if getattr(cfg, "multi_db", None) else []
    if DB_ID not in active:
        reasons.append(
            f"ACTIVE_DB_IDS 에 {DB_ID} 가 없다(활성 {active}) — "
            "소스가 없으면 전 턴이 routing_miss 다"
        )
    if getattr(cfg, "db_backend", None) == "direct":
        reasons.append(
            "DB_BACKEND=direct — 오라클이 비활성이라 대조할 수 없다(D-003 · plans/122 G-9)"
        )
    return reasons


# --- 산출물 (W5) ------------------------------------------------------------------


def _git_provenance() -> dict[str, Any]:
    from scripts.scenario import run_capture

    sha = run_capture(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"]).strip()
    dirty = run_capture(["git", "-C", str(REPO_ROOT), "status", "--porcelain"]).strip()
    return {"sha": sha[:12] or None, "dirty": bool(dirty)}


def build_artifacts(
    *,
    run_meta: Mapping[str, Any],
    catalog_doc: Mapping[str, Any],
    records: list[dict[str, Any]],
    code_samples: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """메모리의 산출물 4종(+ P1 근거가 있으면 치환 코드값 `code_samples.yaml` · 관문 전).

    `leak_check.json`은 관문이 쓴다.
    """
    from .report import render_report

    staged = {
        "run.json": json.dumps(run_meta, ensure_ascii=False, indent=2) + "\n",
        "schema_catalog.yaml": yaml.safe_dump(
            dict(catalog_doc), allow_unicode=True, sort_keys=False
        ),
        "trace.jsonl": "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
        "report.md": render_report(run_meta, catalog_doc, records, code_samples=code_samples),
    }
    if code_samples is not None:
        staged[rd.CODE_SAMPLES_FILE] = yaml.safe_dump(
            dict(code_samples), allow_unicode=True, sort_keys=False
        )
    return staged


def stage_gated(
    *,
    run_meta: Mapping[str, Any],
    catalog_doc: Mapping[str, Any],
    records: list[dict[str, Any]],
    policy: cat.ColumnPolicy,
    vault: rd.PiiVault,
    user_values: Mapping[str, str | None],
    p1_draft: Mapping[str, Any] | None,
) -> tuple[dict[str, str], rd.LeakGate]:
    """관문을 만들고 관문 규칙으로 미리 거른 산출물을 짠다(plans/140 W2-3·4·5).

    - 승인 프로필 테이블 정의: 관문 규칙에 걸린 행은 빼고 수만(`gate_table_definitions`)
    - P1 근거가 있으면 치환 코드값(`code_samples.yaml`) — 후보가 관문 규칙(코드값 대조 포함)에
      걸리면 재추첨. 원 코드값·라벨 집합은 이 함수와 관문 안(메모리)에만 있다.
    """
    from . import code_samples as cs

    originals = rd.CodeOriginals(cs.original_values(p1_draft)) if p1_draft else None
    gate = rd.LeakGate(
        policy=policy, vault=vault, user_values=user_values, code_originals=originals
    )
    catalog_doc = cat.gate_table_definitions(
        catalog_doc, lambda text: bool(gate.rules(text, schema_section=True))
    )
    samples = None
    if p1_draft and originals is not None:
        samples = cs.build_code_samples(
            p1_draft,
            policy,
            db_id=DB_ID,
            run_id=str(run_meta.get("run_id")),
            comments=cs.column_comments(catalog_doc),
            originals=originals,
            reject=lambda text: bool(gate.rules(text, schema_section=False)),
        )
    staged = build_artifacts(
        run_meta=run_meta, catalog_doc=catalog_doc, records=records, code_samples=samples
    )
    return staged, gate


def user_values(
    *, login_id: str | None, dsn: str | None, password: str | None = None
) -> dict[str, str | None]:
    """누출 관문 ⑤의 원값 목록 — 가린 형태만 산출물에 허용한다."""
    dsn_user, dsn_password = rd.dsn_credentials(dsn)
    return {
        "login_id": login_id,
        "login_password": password,
        "os_user": getpass.getuser(),
        "host": socket.gethostname(),
        "home": str(Path.home()),
        "dsn_user": dsn_user,
        "dsn_password": dsn_password,
    }


def _itam_dsn() -> str | None:
    """MCP 서버 쪽 ITAM 접속 문자열(관문 대조용 — 산출물에는 쓰지 않는다). 없으면 None."""
    try:
        from scripts.itam_erd import DEFAULT_ENV_FILE, ENV_KEY, read_env_value

        return read_env_value(DEFAULT_ENV_FILE, ENV_KEY) or None
    except Exception:  # noqa: BLE001 — 대조 재료가 없을 뿐이다
        return None


# --- --run (W4·W5) ----------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    loaded = _load(args)
    if loaded is None:
        return 1
    policy, scenarios = loaded
    if not scenarios:
        say("[중단] 선택된 시나리오가 0건이다")
        return 1
    from src.config import load_config

    cfg = load_config()
    stops = preflight(cfg)
    if stops:
        say("[중단] 사전 점검:")
        for reason in stops:
            say(f"  - {reason}")
        return 1
    try:
        schema = cat.load_schema_source(
            args.schema_source,
            path=Path(args.schema_snapshot) if args.schema_snapshot else None,
            cfg=cfg,
        )
    except (FileNotFoundError, ValueError) as exc:
        if args.schema_source not in ("schema_cache", "structure_store") or args.env != "sandbox":
            say(f"[중단] 스키마 입력: {exc}")
            return 1
        say(f"[주의] 파일 스키마 캐시 없음 — 샌드박스 전사본으로 카탈로그를 만든다 ({exc})")
        schema = cat.load_schema_source("transcript")
        if args.schema_source == "structure_store":
            schema["p1_fallback"] = "스냅샷·스키마 캐시 없음"
    if schema.get("p1_fallback"):
        say(f"[주의] {cat.P1_MISSING_WARNING} — {schema['p1_fallback']}")
    for warning in schema.get("p1_warnings") or []:
        say(f"[주의] {warning}")
    p1_draft = schema.get("_p1_draft")
    # 설명·유사어는 서버와 같은 순서(Redis → 파일)로 다시 읽는다(plans/139 W6-a · 조회문 0)
    cat.apply_server_annotations(schema, cfg=cfg)
    if schema["annotation_sources"]["redis"] == "unavailable":
        say(
            "[주의] Redis 연결 불가 — 설명·유사어를 파일 캐시에서 읽었다"
            "(annotation_sources 에 기록)"
        )
    profile = cat.load_profile()
    assets = cat.asset_fingerprints(descriptions=len(schema.get("descriptions") or {}))
    assets["p1"] = cat.p1_asset(p1_draft)
    catalog_doc = cat.build_schema_catalog(schema, policy, assets=assets, profile=profile)
    # 실행 환경의 카탈로그(운영이면 108테이블)로 프롬프트를 한 번 더 거른다 — 로더 린트는 정책에
    # 적힌 이름만 안다. 걸린 낱말은 출력하지 않는다(시나리오 id 만). 짧은 이름(`IP`·`OS` 같은
    # 컬럼)은 사용자 말과 겹치므로 5자 이상만 본다.
    identifiers = {
        name
        for name in set(catalog_doc["tables"])
        | {c["name"] for t in catalog_doc["tables"].values() for c in t["columns"]}
        if len(name) >= 5
    }
    flagged = sorted(
        {
            s.id
            for s in scenarios
            for t in s.turns
            if t.query and cat.lint_prompt(t.query, identifiers=identifiers, policy=policy)
        }
    )
    if flagged:
        say(
            f"[중단] 실행 카탈로그 기준 프롬프트 린트 위반: {', '.join(flagged)} — "
            "사용자 말로 고친다"
        )
        return 1
    facts = jd.CatalogFacts.from_catalog(
        catalog_doc, code_values=(profile or {}).get("code_values")
    )

    session = Path(tempfile.mkdtemp(prefix="itam-bench-"))
    try:
        return _run_with_server(
            args, cfg, policy, scenarios, catalog_doc, facts, session, p1_draft=p1_draft
        )
    finally:
        # 서버 원시 로그(감사 줄의 사용자 칸)·체크포인트(응답 원문)·측정 수신·오라클 로그 — 산출물이
        # 아니다.
        shutil.rmtree(session, ignore_errors=True)


def _run_with_server(
    args: argparse.Namespace,
    cfg: Any,
    policy: cat.ColumnPolicy,
    scenarios: list[cat.Scenario],
    catalog_doc: Mapping[str, Any],
    facts: jd.CatalogFacts,
    session: Path,
    *,
    p1_draft: Mapping[str, Any] | None = None,
) -> int:
    import httpx

    from scripts.scenario.catalog import load_profiles
    from scripts.scenario.client import ClientConfig, ScenarioClient
    from scripts.scenario.oracle import run_oracle
    from scripts.scenario.runner import (
        DEFAULT_USER_ID,
        DEFAULT_USER_PASSWORD,
        ISOLATION_ENV,
        TokenSource,
        jwt_lifetime_sec,
        resolve_admin_credentials,
        user_relogin,
    )
    from scripts.scenario.server import ServerHandle, pick_port, verify_profile
    from src.routing.db_authz import (
        ACCESS_DENIED_MESSAGE,
        SELECTION_DENIED_MESSAGE,
        authorized_db_ids,
    )

    from ._serve import CAPTURE_ENV

    started = _now()
    run_id = started.strftime("%Y%m%d-%H%M%S")
    profiles = load_profiles()
    if args.profile not in profiles:
        say(f"[중단] 프로파일 {args.profile} 정의 없음(config/scenarios/profiles.yaml)")
        return 1
    expected = {**ISOLATION_ENV, **profiles[args.profile]}
    overrides = {
        **expected,
        "CHECKPOINT_DB_URL": str(session / "checkpoints.db"),
        CAPTURE_ENV: str(session / "capture.jsonl"),
    }
    port = pick_port(args.port)
    handle = ServerHandle(
        profile=args.profile,
        env_overrides=overrides,
        port=port,
        log_path=session / "server.log",
        entry_module=ENTRY_MODULE,
    )
    say(f"[1/4] 벤치 서버 기동 — 프로파일 {args.profile} · 포트 {port}")
    handle.start()
    try:
        healthy, detail = handle.wait_healthy()
        if not healthy:
            say(f"[중단] 헬스 실패: {detail}")
            return 1
        admin_user, admin_password = resolve_admin_credentials(None, None)
        probe = ScenarioClient(ClientConfig(port=port))
        try:
            admin_token, admin_error = (
                probe.admin_login(admin_user, admin_password)
                if admin_user and admin_password
                else (None, "운영자 계정 없음")
            )
        finally:
            probe.close()
        status = verify_profile(
            handle, expected, admin_token, expected_tier=EXPECTED_TIER.get(args.profile)
        )
        if admin_error:
            status.reasons.append(admin_error)
        if not status.valid:
            say("[중단] 기동 검증 실패: " + "; ".join(status.reasons))
            return 1
        say(f"      사다리 확정: {status.tier} (사유 {status.degraded_reason})")

        user_id = args.user or DEFAULT_USER_ID
        password = args.password or DEFAULT_USER_PASSWORD
        token: str | None = None
        if status.auth_enabled:
            login = ScenarioClient(ClientConfig(port=port))
            try:
                token, error = login.login(user_id, password)
            finally:
                login.close()
            if not token:
                # 로그인 실패 사유 문구에 계정 ID 가 실릴 수 있다 — 출력에는 가린 형태만
                say(
                    f"[중단] 로그인 실패({rd.mask_identifier(user_id)}): "
                    f"{(error or '').replace(user_id, rd.mask_identifier(user_id))[:200]}"
                )
                return 1
            me = httpx.get(
                f"http://127.0.0.1:{port}/api/v1/auth/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=20.0,
            )
            body = me.json() if me.status_code == 200 else {}
            if not authorized_db_ids([DB_ID], body.get("allowed_db_ids"), body.get("role")):
                say(
                    f"[중단] 계정({rd.mask_identifier(user_id)})에 {DB_ID} 조회 권한이 없다"
                    "(D-232) — "
                    "관리자 화면에서 권한을 준 계정으로 다시 돈다"
                )
                return 1
        say(
            f"[2/4] 로그인 "
            f"{'— ' + rd.mask_identifier(user_id) if status.auth_enabled else '생략(인증 꺼짐)'}"
            " · "
            f"{DB_ID} 조회 인가 확인"
        )

        source = TokenSource(
            token=token,
            relogin=user_relogin(port, user_id, password) if status.auth_enabled else None,
            lifetime_sec=jwt_lifetime_sec(),
            issued_at=time.monotonic(),
        )
        client = ScenarioClient(
            ClientConfig(port=port, token_source=source, server_timeouts=status.server_timeouts)
        )
        vault = rd.PiiVault.from_policy(policy)
        oracle_log = session / "oracle_log.jsonl"

        def oracle(spec: Mapping[str, Any], anchor: str, tag: str) -> dict[str, Any]:
            return run_oracle(
                dict(spec),
                db_ids=list(spec.get("db_ids") or [DB_ID]),
                anchor_at=anchor,
                run_id=run_id,
                scenario_id=tag,
                log_path=oracle_log,
                cfg=cfg,
            )

        ctx = RunContext(
            run_id=run_id,
            tier=status.tier,
            policy=policy,
            catalog=facts,
            vault=vault,
            oracle=oracle,
            repeat=args.repeat,
            denied_messages=(ACCESS_DENIED_MESSAGE, SELECTION_DENIED_MESSAGE),
        )
        say(f"[3/4] 시나리오 {len(scenarios)}건 × 반복 {args.repeat}")
        try:
            records = run_scenarios(
                scenarios,
                client=client,
                audit=_audit_tail_class()(handle.log_path),
                capture=CaptureTail(session / "capture.jsonl"),
                ctx=ctx,
            )
        finally:
            client.close()
    finally:
        handle.stop()
        handle.port_released()

    run_meta = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "finished_at": _now().isoformat(),
        "git": _git_provenance(),
        "env": args.env,
        "profile": args.profile,
        "tier": status.tier,
        "degraded_reason": status.degraded_reason,
        "planes": {
            "worker": str(getattr(cfg.llm, "provider", "")),
            "orchestrator": str(getattr(cfg.orchestrator, "provider", "")),
        },
        "login_user": rd.mask_identifier(user_id) if status.auth_enabled else None,
        "operator": rd.mask_identifier(getpass.getuser()),
        "host": rd.mask_identifier(socket.gethostname()),
        "db_backend": getattr(cfg, "db_backend", None),
        "mcp_endpoint": rd.mask_dsn(
            str(getattr(getattr(cfg, "dbhub", None), "server_url", "") or "")
        ),
        "itam_dsn": rd.mask_dsn(_itam_dsn() or "") or None,
        "scenarios": len(scenarios),
        "repeat": args.repeat,
        "turns": ctx.counters["turns"],
        "sql_observed_turns": ctx.counters["sql_observed_turns"],
        "judged": ctx.counters["sql_observed_turns"] > 0,
        "scenario_file": Path(args.scenarios).name,
        "policy_scope": policy.scope,
        "schema_source": catalog_doc.get("source"),
        "assets": catalog_doc.get("assets"),
        "vault_values": len(vault),
        "canary_in_results": ctx.counters["canary_in_results"],
        "results_dir": rd.display_path(
            RESULTS_ROOT / run_id, repo_root=REPO_ROOT, home=Path.home()
        ),
    }
    staged, gate = stage_gated(
        run_meta=run_meta,
        catalog_doc=catalog_doc,
        records=records,
        policy=policy,
        vault=vault,
        user_values=user_values(
            login_id=user_id if status.auth_enabled else None,
            dsn=_itam_dsn(),
            password=password if status.auth_enabled else None,
        ),
        p1_draft=p1_draft,
    )
    ok, violations = rd.write_gated(RESULTS_ROOT / run_id, staged, gate)
    say(f"[4/4] 산출물 {'기록' if ok else '미기록(누출 관문 실패)'} — {run_meta['results_dir']}")
    if not ok:
        for violation in violations[:20]:
            say(
                f"  - {violation['file']} 레코드 {violation['record']} · {violation['field']} · "
                f"{violation['rule']}"
            )
        return 1
    if not run_meta["judged"]:
        say("[실패] SQL 관측 턴 0 — 판정하지 않는다(D-219 ③). 감사 로그 수집 경로를 확인한다")
        return 1
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    from .report import compare_runs

    try:
        text = compare_runs(RESULTS_ROOT / args.compare[0], RESULTS_ROOT / args.compare[1])
    except FileNotFoundError as exc:
        say(f"[중단] {exc}")
        return 1
    say(text)
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    """반출 후 싱크 보조 — 반출 카탈로그와 로컬 전사본·컬럼 정책의 차이.

    LLM·DB 0 · 파일을 쓰지 않는다.
    """
    from .report import sync_report

    run_dir = Path(args.sync)
    if not run_dir.is_absolute() and not run_dir.exists():
        run_dir = RESULTS_ROOT / args.sync
    try:
        policy = cat.load_policy(Path(args.policy))
        say(sync_report(run_dir, transcript_path=TRANSCRIPT_PATH, policy=policy))
    except (FileNotFoundError, cat.CatalogError) as exc:
        say(f"[중단] {exc}")
        return 1
    return 0


def cmd_build_assets(args: argparse.Namespace, cfg: Any = None) -> int:
    """반출 run 으로 외부망 자산을 만들어 쓴다(D-311 ③) — 기본 LLM·DB 0.

    `--p2`면 쿼리 예시·DB 전용 규칙 섹션 LLM 초안을 `itam` 소스(모의 DB)로 검증해 함께 쓴다
    (plans/140 W5). 두 LLM 평면 중 과금 평면이 있으면 실행하지 않는다(종료 1 · D-127).
    """
    from .build_assets import EXIT_REFUSED, default_p2_deps, p2_billing_refusal, run_build

    run_dir = Path(args.build_assets)
    if not run_dir.is_absolute() and not run_dir.exists():
        run_dir = RESULTS_ROOT / args.build_assets
    deps = None
    if args.p2:
        if cfg is None:
            from src.config import load_config

            cfg = load_config()
        refusal = p2_billing_refusal(cfg)
        if refusal:
            say(f"[build-assets] 거부: {refusal}")
            return EXIT_REFUSED
        deps = default_p2_deps(cfg)
    return run_build(
        run_dir, install_cache=args.install_cache, keep_excluded=args.keep_excluded,
        p2=args.p2, p2_deps=deps,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.itam_bench",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="시나리오·린트·실행 계획만(기본)")
    mode.add_argument("--check-oracle", action="store_true", help="정답 SQL 만 읽기 전용 실행")
    mode.add_argument("--run", action="store_true", help="벤치 서버로 사용자 경로 실행")
    mode.add_argument("--compare", nargs=2, metavar=("RUN_A", "RUN_B"), help="두 run 비교")
    mode.add_argument("--sync", metavar="RUN", help="반출 run 카탈로그 ↔ 로컬 전사본·정책 차이")
    mode.add_argument(
        "--build-assets", metavar="RUN", help="반출 run → itam 프로필·시드 직접 쓰기(D-311 ③)"
    )
    parser.add_argument("--env", default="sandbox", choices=sorted(cat.ENVS), help="실행 환경")
    parser.add_argument("--only", default=None, help="시나리오 ID 쉼표 목록")
    parser.add_argument(
        "--profile",
        default="tier2_intent",
        choices=sorted(EXPECTED_TIER),
        help="사다리 단 프로파일(기준 2단 · 3단은 비교 arm)",
    )
    parser.add_argument("--repeat", type=int, default=1, help="반복 수(결정성)")
    parser.add_argument("--user", default=None, help="로그인 계정(없으면 내장 테스트 계정 · D-216)")
    parser.add_argument("--password", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=None, help="벤치 서버 포트(기본 빈 포트)")
    parser.add_argument(
        "--schema-source",
        default=None,
        choices=("schema_cache", "snapshot", "transcript", "structure_store"),
        help="카탈로그 입력(기본: sandbox=schema_cache · closed=structure_store — 「DB 구조」 탭 "
        "스냅샷·P1 초안 · plans/140 W2)",
    )
    parser.add_argument(
        "--schema-snapshot", default=None, help="itam_erd 스냅숏 JSON(--schema-source snapshot)"
    )
    parser.add_argument("--scenarios", default=None, help="시나리오 YAML(기본: 환경별)")
    parser.add_argument("--policy", default=None, help="컬럼 기록 정책 YAML(기본: 환경별)")
    parser.add_argument(
        "--install-cache",
        action="store_true",
        help="--build-assets: .cache/schema/itam_schema.json 에도 쓴다(기존 파일 백업)",
    )
    parser.add_argument(
        "--keep-excluded",
        action="store_true",
        help="--build-assets: 기본 제외 테이블을 조회 대상에 남긴다",
    )
    parser.add_argument(
        "--p2",
        action="store_true",
        help="--build-assets: P2 LLM 초안(쿼리 예시·DB 전용 규칙 섹션)을 itam 소스(모의 DB)로 "
        "검증해 함께 쓴다 — 두 LLM 평면이 비과금일 때만(plans/140 W5)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    closed = args.env == "closed"
    if args.scenarios is None:
        args.scenarios = str(CLOSED_SCENARIOS_PATH if closed else SCENARIOS_PATH)
    if args.policy is None:
        args.policy = str(CLOSED_POLICY_PATH if closed else POLICY_PATH)
    if args.schema_source is None:
        args.schema_source = "structure_store" if closed else "schema_cache"
    if args.repeat < 1:
        say("[중단] --repeat 는 1 이상")
        return 1
    if args.check_oracle:
        return cmd_check_oracle(args)
    if args.run:
        return cmd_run(args)
    if args.compare:
        return cmd_compare(args)
    if args.sync:
        return cmd_sync(args)
    if args.build_assets:
        return cmd_build_assets(args)
    if args.p2:
        say("[중단] --p2 는 --build-assets 와 함께 쓴다")
        return 1
    return cmd_dry_run(args)


if __name__ == "__main__":
    sys.exit(main())
