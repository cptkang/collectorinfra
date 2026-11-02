"""동시 조사 측정 (plans/207 A0-a) — `--run --concurrency K[,K…]`.

서로 다른 호스트 조사 K건을 **같은 실험군 프로세스 하나에 동시에 접수**하고 각각 폴링해, 같은 세계를
혼자 돌렸을 때(K=1) 대비 소요 배율과 감속 원인 단서를 낸다.

- 세계 고르기: 명세가 고른 세계를 순서대로 보며 호스트 집합(`World.host_set`)이 앞서 고른 것과
  겹치지 않는 것만 max(K)개 고른다(같은 호스트는 조사 서비스 L-4 가드가 거부한다).
- 실행 순서: 고른 세계마다 혼자(K=1 · 기준) → K마다 앞 K개 세계를 동시에. K=1을 지정하지 않아도
  기준은 돈다. 그룹마다 세계 서버·가짜 LLM에 활성 세계 목록(`{worlds: […]}`)을 주면 두 서버가
  호스트로 세계를 가른다(호출 로그 행의 `world_id`가 그 세계) — 그래서 근거 귀속은 오프셋이
  아니라 행의 세계로 한다.
- 계측: 실험군에 `SRE_INVESTIGATION_METRICS_ENABLED=true`를 주고 감사 `investigation_metrics`
  이벤트(조사별 LLM 호출 수·지연 · 전역 자리 대기 · 도구 실행 · CPU)를 모은다. 프로세스 CPU% =
  Δprocess_time / Δ벽시계(그룹 구간 · 100% = 1코어 포화).
- 결과 동일(계획 수용 기준 5): 판독(`severity`)·가설(`root_cause_hypotheses`의 순위·신뢰도·상태 —
  LLM 서술 문장 제외)·근거(그 세계로 간 도구 호출의 도구·매처·응답 해시)·종결 상태를 K=1과 비교한다.
  동시 세계의 호스트가 브리핑에 나오면(`cross_host_mentions`) 섞임이다.
- 품질 채점(`judge`)은 하지 않는다 — 같은 세계가 K마다 반복돼 raw 1행 = 실행 1건 전제가 깨진다.
- 산출: `<run>/concurrency.jsonl`(조사별 행) · `<run>/concurrency.md`(K별 표).

경계(D-139): sre_agent 패키지를 import하지 않는다(이벤트 이름은 사본 상수).
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from investigation_bench import runner
from investigation_bench.worlds import World

CONCURRENCY_JSONL = "concurrency.jsonl"
CONCURRENCY_MD = "concurrency.md"
# sre_agent `infrastructure/investigation_metrics.METRICS_EVENT`의 사본(D-139 — import 0).
METRICS_EVENT = "investigation_metrics"
METRICS_FIELD = "sre_investigation_metrics_enabled"
SIG_PARTS: tuple[str, ...] = ("status", "severity", "hypotheses", "evidence")
TARGET_RATIO, PASS_RATIO = 2.0, 3.0
CPU_SATURATED_PCT = 85.0  # 프로세스 CPU% 이 이상 = 1코어 포화(R3)
R2_SHARE = 0.3            # 늘어난 소요 중 전역 자리 대기 비중이 이 이상이면 R2
GROWTH_SHARE = 0.5        # 늘어난 소요 중 LLM·도구 구간 증가 비중이 이 이상이면 그 구간
NEGLIGIBLE_RATIO = 1.2
METRICS_WAIT_S = 15.0     # 종결 뒤 계측 이벤트(워커 끝 — 호스트 키 반환 뒤)를 기다리는 상한
# 워커가 돈 종결 상태 — 이것만 계측 이벤트를 기다린다(stub·rejected·not_found는 워커가 없다).
WORKER_STATUSES = frozenset({"done", "failed", "timeout"})


def parse_ks(text: str) -> tuple[int, ...]:
    """`1,2,5` → (1, 2, 5)(중복 제거 · 오름차순). 1 미만·정수 아님이면 ValueError."""
    out = set()
    for part in str(text).split(","):
        part = part.strip()
        if not part:
            continue
        k = int(part)
        if k < 1:
            raise ValueError(f"동시 수는 1 이상이어야 한다: {k}")
        out.add(k)
    if not out:
        raise ValueError("동시 수가 비었다")
    return tuple(sorted(out))


def pick_disjoint_worlds(worlds: Mapping[str, World], n: int) -> list[str]:
    """명세 순서로 호스트 집합이 서로 겹치지 않는 세계 n개. 모자라면 RunnerError(2)."""
    picked: list[str] = []
    used: set[str] = set()
    for wid, world in worlds.items():
        hosts = world.host_set
        if hosts & used:
            continue
        picked.append(wid)
        used |= hosts
        if len(picked) == n:
            return picked
    raise runner.RunnerError(
        f"호스트가 서로 겹치지 않는 세계가 {n}개 필요한데 {len(picked)}개뿐이다: {picked}", 2)


def plan_groups(world_ids: Sequence[str], ks: Iterable[int]) -> list[dict[str, Any]]:
    """실행 그룹 — 세계마다 혼자(K=1 · 기준) 다음 K≥2마다 앞 K개 세계를 동시에."""
    out = [{"k": 1, "worlds": [w]} for w in world_ids]
    out += [{"k": k, "worlds": list(world_ids[:k])} for k in sorted(set(ks)) if k > 1]
    return out


def group_fingerprint(run_id: str, arm: str, world_id: str, k: int, group: int) -> str:
    """동시 측정 실행 지문 — 그룹마다 고유(dedup·예산 가드에 막히지 않게)."""
    return f"{run_id}:{arm}:{world_id}:c{k}-g{group}"


# ─────────────────────────────── 결과 비교 ───────────────────────────────


#: holmes MCP 툴셋 헬스체크 도구(`sre_agent.interface.mcp_service._build_mcp_servers`) — 조사 근거가
#: 아니라 프로세스 첫 조사의 agent 생성 때 한 번 불린다(혼자 1번째에만 보이고 K≥2에선 호스트가 없어
#: 귀속 불가) → 근거 서명에서 뺀다.
HEALTH_CHECK_TOOLS = frozenset({"list_sources", "gateway_health"})


def evidence_signature(calls: Iterable[Mapping[str, Any]]) -> list[list[Any]]:
    """그 세계로 간 도구 호출 → 정렬한 `[도구, 매처, 인자 오류, 응답 해시]` 목록(호출 순서 무관).

    헬스체크 도구(`HEALTH_CHECK_TOOLS`)는 뺀다.
    """
    sig = [[str(c.get("tool")), c.get("matcher"), c.get("arg_error"), c.get("reply_hash")]
           for c in calls if c.get("tool") not in HEALTH_CHECK_TOOLS]
    return sorted(sig, key=lambda x: json.dumps(x, default=str))


def result_signature(job: Mapping[str, Any] | None,
                     evidence: list[list[Any]]) -> dict[str, Any]:
    """비교용 결과 서명 — 판독·가설(순위·신뢰도·상태)·근거·종결 상태(LLM 서술 문장 제외)."""
    job = job or {}
    briefing = job.get("briefing") if isinstance(job.get("briefing"), Mapping) else {}
    hyps = briefing.get("root_cause_hypotheses") if isinstance(briefing, Mapping) else None
    return {
        "status": job.get("status"),
        "severity": briefing.get("severity") if isinstance(briefing, Mapping) else None,
        "hypotheses": [[h.get("rank"), h.get("confidence"), h.get("status")]
                       for h in hyps or [] if isinstance(h, Mapping)],
        "evidence": evidence,
    }


def compare_signatures(solo: Mapping[str, Any] | None,
                       other: Mapping[str, Any]) -> dict[str, bool]:
    """부분별 일치(기준이 없으면 전부 False)."""
    if solo is None:
        return {part: False for part in SIG_PARTS}
    return {part: solo.get(part) == other.get(part) for part in SIG_PARTS}


def cross_host_mentions(briefing: Any, foreign_hosts: Iterable[str]) -> int:
    """브리핑에 같은 그룹 다른 세계의 호스트가 나온 횟수(섞임 단서)."""
    text = json.dumps(briefing, ensure_ascii=False, default=str).casefold()
    return sum(len(re.findall(rf"(?<![\w.-]){re.escape(h)}(?![\w.-])", text))
               for h in foreign_hosts if h)


# ─────────────────────────────── 실행 ───────────────────────────────


def _blank_row(arm: str, k: int, group: int, world: World, fp: str) -> dict[str, Any]:
    return {"arm": arm, "k": k, "group": group, "world_id": world.id, "fingerprint": fp,
            "investigation_id": None, "submitted_at": None, "finished_at": None, "wall_s": None,
            "status": None, "infra_error": None, "elapsed_s": None, "tool_calls_n": None,
            "poll_latency_s": [], "world_calls_n": 0, "metrics": None}


def _investigate(ctx: runner.RunContext, arm_url: str, world: World, row: dict[str, Any],
                 start: threading.Barrier, jobs: dict[str, Any]) -> None:
    """조사 1건 — 그룹이 모두 준비되면 접수하고 종결까지 폴링한다(조회 지연을 잰다)."""
    try:
        start.wait(timeout=60)
    except threading.BrokenBarrierError:
        pass
    row["submitted_at"] = runner.now_iso()
    t0 = ctx.clock()
    job: dict[str, Any] | None = None
    try:
        sub: dict[str, Any] | None = ctx.call(
            arm_url, "sre_investigate_alarm",
            {"payload": runner.build_payload(world, row["fingerprint"]), "wait_seconds": 0})
    except Exception as e:
        row["infra_error"] = f"exception:submit: {type(e).__name__}: {e}"[:500]
        sub = None
    if sub is not None:
        row["investigation_id"] = sub.get("investigation_id")
        if sub.get("status") != "accepted":
            row["status"] = sub.get("status")
            reason = sub.get("reason") or ""
            row["infra_error"] = f"{sub.get('status')}: {reason}".rstrip(": ")[:500]
        else:
            last_error = ""
            while True:
                q0 = ctx.clock()
                try:
                    job = ctx.call(arm_url, "sre_get_investigation",
                                   {"investigation_id": row["investigation_id"]})
                    last_error = ""
                except Exception as e:
                    last_error = f"{type(e).__name__}: {e}"
                row["poll_latency_s"].append(round(ctx.clock() - q0, 4))
                if job is not None and job.get("status") in runner.TERMINAL_STATUSES:
                    break
                if ctx.clock() - t0 >= ctx.per_run_timeout_s:
                    tail = f" (마지막 오류 {last_error})" if last_error else ""
                    row["infra_error"] = (f"{runner.POLL_TIMEOUT_PREFIX}: {ctx.per_run_timeout_s}s "
                                          f"안에 종결되지 않음{tail}")[:500]
                    break
                ctx.sleep(ctx.poll_interval_s)
    row["finished_at"] = runner.now_iso()
    row["wall_s"] = round(ctx.clock() - t0, 3)
    if job is not None:
        row["status"] = job.get("status")
        row["elapsed_s"] = job.get("elapsed_s")
        row["tool_calls_n"] = job.get("tool_calls_n")
        if row["infra_error"] is None and job.get("status") in runner.INFRA_TERMINAL:
            detail = job.get("reason") or job.get("verdict") or job.get("error") or ""
            row["infra_error"] = f"{job.get('status')}: {detail}"[:500]
        jobs[world.id] = job


def _metrics_events(audit_path: Path, ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    wanted = set(ids)
    out: dict[str, dict[str, Any]] = {}
    for e in runner.read_jsonl(audit_path):
        if e.get("event") == METRICS_EVENT and e.get("investigation_id") in wanted:
            out[str(e["investigation_id"])] = {
                k: v for k, v in e.items() if k not in ("event", "investigation_id", "ts")}
    return out


def execute_group(ctx: runner.RunContext, *, arm: str, k: int, group: int,
                  world_ids: Sequence[str], worlds: Mapping[str, World], arm_url: str,
                  audit_path: Path, script: str = "model",
                  metrics_wait_s: float = METRICS_WAIT_S) -> list[dict[str, Any]]:
    """그룹 1개(세계 K개 동시 · K=1이면 혼자) → 조사별 행(결과 서명·계측 포함 · 비교는 뒤에서)."""
    rows = {w: _blank_row(arm, k, group, worlds[w],
                          group_fingerprint(ctx.run_id, arm, w, k, group)) for w in world_ids}
    entries = [{"world_id": w, "trial": group, "arm": arm, "script": script} for w in world_ids]
    offset = runner.world_calls_offset(ctx.world_calls_path)
    try:
        ctx.activate({"worlds": entries})
    except Exception as e:
        for row in rows.values():
            row["infra_error"] = f"exception:activate: {type(e).__name__}: {e}"[:500]
        return list(rows.values())
    jobs: dict[str, Any] = {}
    start = threading.Barrier(len(world_ids))
    threads = [threading.Thread(target=_investigate, daemon=True, name=f"bench-conc-{w}",
                                args=(ctx, arm_url, worlds[w], rows[w], start, jobs))
               for w in world_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 계측 이벤트는 워커 끝(호스트 키 반환 뒤)에 쓰인다 — 다음 그룹이 같은 호스트로 거부되지 않게
    # 기다린다.
    ids = [r["investigation_id"] for r in rows.values()
           if r["investigation_id"] and r["status"] in WORKER_STATUSES]
    deadline = ctx.clock() + metrics_wait_s
    events = _metrics_events(audit_path, ids)
    while len(events) < len(ids) and ctx.clock() < deadline:
        ctx.sleep(0.1)
        events = _metrics_events(audit_path, ids)
    try:
        ctx.activate({"world_id": None, "trial": None, "arm": None})  # 구간 닫기
    except Exception:  # 닫기 실패는 다음 그룹의 활성화가 덮는다
        pass
    segment = runner.read_segment(ctx.world_calls_path, offset)
    unrouted = sum(1 for c in segment if c.get("route"))
    for w, row in rows.items():
        calls = [c for c in segment if c.get("world_id") == w]
        row["world_calls_n"] = len(calls)
        row["unrouted_calls_in_group"] = unrouted
        row["metrics"] = events.get(str(row["investigation_id"]))
        job = jobs.get(w)
        row["signature"] = result_signature(job, evidence_signature(calls))
        briefing = (job or {}).get("briefing")
        row["briefing_hash"] = (hashlib.sha256(json.dumps(briefing, ensure_ascii=False,
                                                          sort_keys=True, default=str)
                                               .encode("utf-8")).hexdigest()[:16]
                                if briefing is not None else None)
        others = [worlds[o].host_set for o in world_ids if o != w]
        foreign = set().union(*others) - worlds[w].host_set
        row["cross_host_mentions"] = cross_host_mentions(briefing, sorted(foreign))
    return [rows[w] for w in world_ids]


def compare_with_solo(rows: Sequence[dict[str, Any]],
                      solos: Mapping[str, Mapping[str, Any]]) -> None:
    """행마다 배율(소요_K / 소요_혼자)과 결과 일치를 단다(제자리)."""
    for row in rows:
        solo = solos.get(str(row["world_id"]))
        solo_wall = (solo or {}).get("wall_s")
        row["solo_wall_s"] = solo_wall
        row["ratio"] = (round(float(row["wall_s"]) / float(solo_wall), 3)
                        if row.get("wall_s") is not None and solo_wall else None)
        match = compare_signatures((solo or {}).get("signature"), row.get("signature") or {})
        row["match"] = match
        row["result_match"] = all(match.values())


def run_concurrency(
    ctx: runner.RunContext, *, run_dir: Path, arms: Sequence[str], excluded: Mapping[str, str],
    arm_urls: Mapping[str, str], worlds: Mapping[str, World], groups: Sequence[Mapping[str, Any]],
    script: str, restart: Callable[[str], float], meta: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """실험군마다 그룹을 차례로 돌려 `concurrency.jsonl`에 덧붙이고 끝에 `concurrency.md`를 쓴다."""
    out_path = Path(run_dir) / CONCURRENCY_JSONL
    every: list[dict[str, Any]] = []
    total = sum(len(g["worlds"]) for g in groups) * sum(1 for a in arms if a not in excluded)
    done = 0
    for arm in arms:
        if arm in excluded:
            print(f"  실험군 {arm} 제외({excluded[arm]}) — 동시 측정 생략")
            continue
        solos: dict[str, dict[str, Any]] = {}
        audit = Path(run_dir) / f"arm-{arm}" / "audit.jsonl"
        for gi, g in enumerate(groups, 1):
            rows = execute_group(ctx, arm=arm, k=int(g["k"]), group=gi, world_ids=g["worlds"],
                                 worlds=worlds, arm_url=arm_urls[arm], audit_path=audit,
                                 script=script)
            if int(g["k"]) == 1:
                solos.update({str(r["world_id"]): r for r in rows})
            compare_with_solo(rows, solos)
            if any(runner.needs_restart(r) for r in rows):
                took = restart(arm)
                for r in rows:
                    r["arm_restarted_after"] = True
                print(f"  실험군 {arm} 재기동({took}s) — 시간 초과 뒤 남은 조사 스레드 정리")
            for r in rows:
                runner.append_jsonl(out_path, r)
                done += 1
                err = r.get("infra_error")
                mark = f"무효({str(err)[:60]})" if err else r.get("status")
                print(f"[{done}/{total}] {arm} · K={g['k']} · {r['world_id']} → {mark}"
                      f" · {r.get('wall_s')}s · 배율 {r.get('ratio')}")
            every += rows
    write_report(Path(run_dir) / CONCURRENCY_MD, summarize(every), meta)
    return every


# ─────────────────────────────── 집계 ───────────────────────────────


def _mean(values: Iterable[Any]) -> float | None:
    vals = [float(v) for v in values if isinstance(v, (int, float))]
    return round(statistics.fmean(vals), 3) if vals else None


def _max(values: Iterable[Any]) -> float | None:
    vals = [float(v) for v in values if isinstance(v, (int, float))]
    return round(max(vals), 3) if vals else None


def percentile(values: Sequence[float], q: float) -> float | None:
    """최근접 순위 백분위(q는 0~100)."""
    vals = sorted(float(v) for v in values)
    if not vals:
        return None
    rank = max(1, int(-(-q * len(vals) // 100)))  # ceil(q/100 × n)
    return vals[min(rank, len(vals)) - 1]


def group_cpu_pct(metrics: Sequence[Mapping[str, Any]]) -> float | None:
    """그룹 구간 프로세스 CPU% = Δprocess_time / Δ벽시계 × 100(100% = 1코어)."""
    ms = [m for m in metrics if m and all(isinstance(m.get(k), (int, float)) for k in (
        "process_cpu_start", "process_cpu_end", "wall_start", "wall_end"))]
    if not ms:
        return None
    wall = max(m["wall_end"] for m in ms) - min(m["wall_start"] for m in ms)
    cpu = max(m["process_cpu_end"] for m in ms) - min(m["process_cpu_start"] for m in ms)
    return round(cpu / wall * 100, 1) if wall > 0 else None


def _m(row: Mapping[str, Any], key: str) -> Any:
    return (row.get("metrics") or {}).get(key)


def _axis_sums(sel: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """축별 자리 대기 합(초) — `polestar`·`apm`(배치 경로) · `holmes_mcp`(HolmesGPT MCP 잠금)."""
    out: dict[str, float] = {}
    for r in sel:
        axes = _m(r, "slot_wait_s")
        for axis, sec in (axes.items() if isinstance(axes, Mapping) else ()):
            out[str(axis)] = round(out.get(str(axis), 0.0) + float(sec or 0), 3)
    return dict(sorted(out.items()))


def _axes_text(axes: Mapping[str, float] | None) -> str:
    shown = [f"{a} {_fmt(v)}" for a, v in (axes or {}).items() if v]
    return f" ({' · '.join(shown)})" if shown else ""


def summarize(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """(실험군, K)별 요약 — 표 1행."""
    solo_of: dict[tuple[str, str], Mapping[str, Any]] = {
        (str(r["arm"]), str(r["world_id"])): r for r in rows if r["k"] == 1}
    out = []
    for arm, k in sorted({(str(r["arm"]), int(r["k"])) for r in rows}, key=lambda x: (x[0], x[1])):
        sel = [r for r in rows if r["arm"] == arm and r["k"] == k]
        groups: dict[int, list[Mapping[str, Any]]] = {}
        for r in sel:
            groups.setdefault(int(r["group"]), []).append(r)
        cpus = [c for g in groups.values()
                if (c := group_cpu_pct([r.get("metrics") or {} for r in g])) is not None]
        lat = [x for r in sel for x in r.get("poll_latency_s") or []]

        def delta(key: str, sel: list[Mapping[str, Any]] = sel) -> float | None:
            ds = []
            for r in sel:
                solo = solo_of.get((arm, str(r["world_id"])))
                a, b = _m(r, key), _m(solo or {}, key) if solo else None
                if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    ds.append(a - b)
            return _mean(ds)

        extra = [float(r["wall_s"]) - float(r["solo_wall_s"]) for r in sel
                 if r.get("wall_s") is not None and r.get("solo_wall_s")]
        p50, p95 = percentile(lat, 50), percentile(lat, 95)
        out.append({
            "arm": arm, "k": k, "n": len(sel),
            "invalid": sum(1 for r in sel if r.get("infra_error")),
            "wall_mean": _mean(r.get("wall_s") for r in sel),
            "wall_max": _max(r.get("wall_s") for r in sel),
            "ratio_mean": _mean(r.get("ratio") for r in sel),
            "ratio_max": _max(r.get("ratio") for r in sel),
            "llm_calls_mean": _mean(_m(r, "llm_calls") for r in sel),
            "llm_s_mean": _mean(_m(r, "llm_s_total") for r in sel),
            "llm_s_max": _max(_m(r, "llm_s_max") for r in sel),
            "slot_wait_s_sum": round(sum(float(_m(r, "slot_wait_s_total") or 0) for r in sel), 3),
            "slot_wait_axes": _axis_sums(sel),
            "tool_exec_s_sum": round(sum(float(_m(r, "tool_exec_s") or 0) for r in sel), 3),
            "cpu_pct": _mean(cpus),
            "status_p50_ms": round(p50 * 1000) if p50 is not None else None,
            "status_p95_ms": round(p95 * 1000) if p95 is not None else None,
            "match_n": sum(1 for r in sel if r.get("result_match")),
            "mismatch_parts": sorted({p for r in sel for p, ok in (r.get("match") or {}).items()
                                      if not ok}),
            "cross_host_mentions": sum(int(r.get("cross_host_mentions") or 0) for r in sel),
            "unrouted_calls": max((int(r.get("unrouted_calls_in_group") or 0) for r in sel),
                                  default=0),
            "metrics_n": sum(1 for r in sel if r.get("metrics")),
            "extra_s_mean": _mean(extra),
            "d_llm_s_mean": delta("llm_s_total"),
            "d_tool_s_mean": delta("tool_exec_s"),
            "d_slot_s_mean": delta("slot_wait_s_total"),
        })
    return out


def hint(s: Mapping[str, Any], *, fake_llm: bool) -> str:
    """판정 힌트 — 배율 등급 + 감속 원인 단서(R2 자리 대기 · R3 프로세스 CPU/GIL · R1 LLM 뒤단).

    가짜 LLM·세계 서버 지연은 K와 무관하게 고정이므로, 가짜 LLM에서 LLM·도구 구간이 늘면 그것도
    프로세스 안 경합(R3) 단서다.
    """
    if s["k"] == 1:
        return "기준(혼자)"
    ratio = s.get("ratio_max")
    if ratio is None:
        return "판정 불가(배율 없음)"
    grade = ("목표(×2 이하)" if ratio <= TARGET_RATIO else
             "합격(×3 이하)" if ratio <= PASS_RATIO else "불합격(×3 초과)")
    causes: list[str] = []
    cpu = s.get("cpu_pct")
    if cpu is not None and cpu >= CPU_SATURATED_PCT:
        causes.append(f"R3(프로세스 CPU {cpu:.0f}% — 1코어 포화)")
    extra = s.get("extra_s_mean") or 0.0
    if extra > 0:
        slot = (s.get("d_slot_s_mean") or 0.0) / extra
        llm = (s.get("d_llm_s_mean") or 0.0) / extra
        tool = (s.get("d_tool_s_mean") or 0.0) / extra
        if slot >= R2_SHARE:
            causes.append(f"R2(전역 자리 대기 = 증가분의 {slot:.0%})")
        if llm >= GROWTH_SHARE:
            causes.append(f"R3 의심(가짜 LLM 고정 지연인데 LLM 구간 증가 = 증가분의 {llm:.0%})"
                          if fake_llm else f"R1(LLM 구간 증가 = 증가분의 {llm:.0%})")
        if tool >= GROWTH_SHARE:
            causes.append(f"R3 의심(도구 고정 지연인데 도구 구간 증가 = 증가분의 {tool:.0%})"
                          if fake_llm else f"도구 대상 지연(도구 구간 증가 = 증가분의 {tool:.0%})")
    if not causes:
        causes.append("감속 미미" if ratio <= NEGLIGIBLE_RATIO else "미분류(구간 증가 단서 없음)")
    return f"{grade} · " + " · ".join(causes)


def _fmt(v: Any, nd: int = 2) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def write_report(path: Path, summary: Sequence[Mapping[str, Any]],
                 meta: Mapping[str, Any]) -> Path:
    """K별 표 `concurrency.md`."""
    fake = bool(meta.get("fake_llm"))
    lines = [
        f"# 동시 조사 측정 — {meta.get('run_id', '')}",
        "",
        f"- 백엔드 {meta.get('backend')} · model {meta.get('model')} · 가짜 LLM 지연 "
        f"{meta.get('fake_delay_s', 0)}s · 세계 도구 지연 {meta.get('tool_delay_s', 0)}s",
        f"- 동시 수 {list(meta.get('ks') or [])} · 세계 {list(meta.get('worlds') or [])} · "
        f"조사 서비스 INVESTIGATION_MAX_CONCURRENT={meta.get('max_concurrent')}",
        "- 품질 채점: **생략**(동시 모드 — 같은 세계가 K마다 반복돼 raw 1행 = 실행 1건 전제가 "
        "깨진다)",
        "- 근거 귀속: 세계 서버가 호출의 호스트 인자로 세계를 갈라 로그 행에 싣는다(오프셋 미사용)",
        "",
        "| 실험군 | K | 조사 | 무효 | 소요 평균/최대(s) | 배율 평균/최대 | LLM 호출(평균) | "
        "LLM 지연 합 평균/1회 최대(s) | 자리 대기 합(s) | 도구 실행 합(s) | 프로세스 CPU% | "
        "status p50/p95(ms) | 결과 일치 | 섞임 | 판정 힌트 |",
        "|---|---:|---:|---:|---|---|---:|---|---:|---:|---:|---|---|---:|---|",
    ]
    for s in summary:
        mism = f" (불일치: {', '.join(s['mismatch_parts'])})" if s["mismatch_parts"] else ""
        lines.append(
            f"| {s['arm']} | {s['k']} | {s['n']} | {s['invalid']} | "
            f"{_fmt(s['wall_mean'])} / {_fmt(s['wall_max'])} | "
            f"{_fmt(s['ratio_mean'])} / {_fmt(s['ratio_max'])} | {_fmt(s['llm_calls_mean'], 1)} | "
            f"{_fmt(s['llm_s_mean'])} / {_fmt(s['llm_s_max'])} | "
            f"{_fmt(s['slot_wait_s_sum'])}{_axes_text(s.get('slot_wait_axes'))} | "
            f"{_fmt(s['tool_exec_s_sum'])} | {_fmt(s['cpu_pct'], 1)} | "
            f"{_fmt(s['status_p50_ms'])} / {_fmt(s['status_p95_ms'])} | "
            f"{s['match_n']}/{s['n']}{mism} | {s['cross_host_mentions']} | "
            f"{hint(s, fake_llm=fake)} |")
    unrouted = max((int(s.get("unrouted_calls") or 0) for s in summary), default=0)
    no_metrics = sum(int(s["n"]) - int(s["metrics_n"]) for s in summary)
    lines += [
        "",
        "## 읽는 법",
        "",
        "- 배율 = 조사별 소요(접수→종결 관측 · 폴링 간격만큼 오차) ÷ 같은 세계 혼자(K=1) 소요. "
        f"합격 ×{PASS_RATIO:g} · 목표 ×{TARGET_RATIO:g}.",
        "- LLM·자리 대기·도구·CPU는 조사 서비스 감사 `investigation_metrics`(조사별 · "
        "`SRE_INVESTIGATION_METRICS_ENABLED`)다. 자리 대기 = 전역 도구 동시 상한 대기"
        "(`polestar`·`apm` — 사전수집·플레이북 배치 경로) + HolmesGPT MCP 서버 잠금 대기"
        "(`holmes_mcp` — holmes가 같은 MCP URL 호출을 프로세스 전역으로 직렬화). 도구 실행 = "
        "HolmesGPT 도구 + 배치 도구의 실행 시간 합(자리 대기 제외).",
        "- 프로세스 CPU% = 그룹 구간 Δprocess_time ÷ Δ벽시계 × 100 — 100%면 1코어 포화(GIL 단일 "
        "프로세스의 상한).",
        f"- 판정 힌트: CPU ≥ {CPU_SATURATED_PCT:g}% → R3 · 늘어난 소요 중 자리 대기 증가 ≥ "
        f"{R2_SHARE:.0%} → R2 · LLM 구간 증가 ≥ {GROWTH_SHARE:.0%} → 실 LLM이면 R1, 가짜 LLM(고정 "
        "지연)이면 R3 의심 · 도구 구간 증가 ≥ 50%도 가짜 세계(고정 지연)면 R3 의심. 구간은 서로 "
        "겹치므로 비중은 단서다.",
        "- 결과 일치 = 판독(severity)·가설(순위·신뢰도·상태)·근거(그 세계로 간 도구·매처·"
        "응답 해시)·종결 상태가 K=1과 같음. LLM 서술 문장은 비교하지 않는다. 실 LLM은 도구 선택이 "
        "흔들려 근거가 달라질 수 있다 — 섞임(동시 세계 호스트가 브리핑에 나온 횟수)이 0이면 "
        "동시성 오염은 아니다.",
    ]
    if unrouted:
        lines.append(f"- 주의: 세계를 못 가른 도구 호출이 그룹에서 최대 {unrouted}건 — 호스트 인자 "
                     "없는 호출(세계 미지정 응답)이라 근거 비교에 영향이 있다.")
    if no_metrics:
        lines.append(f"- 주의: 계측 이벤트가 없는 조사 {no_metrics}건(무효·거부이거나 계측 off).")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return Path(path)


__all__ = [
    "CONCURRENCY_JSONL", "CONCURRENCY_MD", "METRICS_EVENT", "METRICS_FIELD", "parse_ks",
    "pick_disjoint_worlds", "plan_groups", "group_fingerprint", "evidence_signature",
    "result_signature", "compare_signatures", "cross_host_mentions", "execute_group",
    "compare_with_solo", "run_concurrency", "summarize", "hint", "percentile", "group_cpu_pct",
    "write_report",
]
