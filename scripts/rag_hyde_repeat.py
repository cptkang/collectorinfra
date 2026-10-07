"""문서 RAG 반복 호출 진단·측정 — 같은 요청에 플랫폼이 같은 결과를 주는가 · 맞는 문서가 나오는가.

plans/138 §6-a(반복 진단) → plans/141 W4(기준 질의 적중률 · 재시도 포함 0건률 · 교체 전·후 비교).

- **채팅 문장 모드(기본)**: 시험 문장을 채팅과 같은 코드(`parse_doc_command`)로 해석해 같은 질의를
  N회 반복한다 — 배포본이 저장소와 같은지도 화면의 「보낸 질의」로 확인된다.
- **기준 질의 모드(`--gold`)**: `testdata/rag_gold/rag_gold_v1.yaml`의 질의를 문서군에 그대로 보내
  **적중률**(기대 문서가 결과에 든 회차 비율)을 잰다. 적중은 결과 문서의 제목·소제목·파일명에
  기대 문서가 **공백·대소문자 무시 부분 일치**로 들어 있는지로 본다.
- **`--with-retry`**: 엔진의 전체 0건 재시도(plans/141 W2 · D-314)를 흉내 내 「재시도 포함 0건률」도
  잰다(회차의 모든 대상 문서군이 0건이면 그 회차를 1회 다시 호출).
- **`--compare 전.json 후.json`**: 리트리벌 교체 전·후 결과 파일을 대조해 문서군별 지표와 채택
  판정 코드를 출력한다(검색 호출 0 · 설정 불필요).
- 우리 쪽 LLM 호출 0 · 검색 호출만 한다(플랫폼 내부 HyDE는 플랫폼이 수행한다). 토큰은 출력하지
  않는다. 화면에는 **옮겨 적을 요약 줄**만, HyDE 전문·문서 제목은 결과 JSON에 남긴다(문서 본문 없음).

실행 (프로젝트 루트 /fsapp/infra-collector-agent · 작업 폴더 /fsapp/temp):

    cd /fsapp/temp
    PY=/fsapp/infra-collector-agent/.venv/bin/python
    S=/fsapp/infra-collector-agent/scripts/rag_hyde_repeat.py
    G=/fsapp/infra-collector-agent/testdata/rag_gold/rag_gold_v1.yaml

    $PY $S                                         # 채팅 시험 문장 7건 × 5회
    $PY $S --gold $G --with-retry --out before.json  # 교체 전 측정
    $PY $S --gold $G --with-retry --out after.json   # 교체 후 측정(같은 명령)
    $PY $S --compare before.json after.json          # 비교 · 채택 판정

이 파일을 /fsapp/temp로 복사해 실행해도 된다 — 루트는 `--root` > 환경변수 `INFRA_AGENT_ROOT` >
이 파일 위치(…/scripts의 상위) > `/fsapp/infra-collector-agent` 순으로 찾는다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

DEFAULT_ROOT = "/fsapp/infra-collector-agent"

#: 기본 시험 문장(plans/138 §6 · 사용자 시험 2026-10-06과 같은 문장).
DEFAULT_CASES: list[tuple[str, str]] = [
    ("A1", "본부 매뉴얼에서 서버 관리자의 역할을 검색해줘"),
    ("A2", "본부 매뉴얼에서 백업 담당자의 역할을 찾아줘"),
    ("A3", "전산관리매뉴얼에서 인프라변경관리를 알려줘"),
    ("A4", "아키텍처 문서에서 서버 구성 단위를 찾아줘"),
    ("A5", "아키텍처 설계문서에서 운영시스템과 개발시스템 구분 기준을 조회해줘"),
    ("B1", "RAG에서 서버 관리자의 역할을 검색해줘"),
    ("B2", "RAG에서 백업 담당자의 역할을 찾아줘"),
]

#: 판정 코드 — 옮겨 적은 코드만으로 판단할 수 있게 뜻을 고정한다(재시도 전 첫 호출 기준).
VERDICTS: dict[str, str] = {
    "S0": "안정 — 매회 결과 있음 · 같은 문서",
    "S1": "결과는 매회 있으나 문서 묶음이 회마다 다름(플랫폼 비결정 · 0건까지는 아님)",
    "P1": "간헐 0건 — 0건 회차에도 HyDE 있음(같은 요청에 검색 단계가 다른 결과)",
    "P2": "간헐 0건 — 0건 회차에 HyDE 없음(HyDE 생성 실패·생략 시 0건)",
    "Z0": "항상 0건 — HyDE는 생성됨(문서에 없거나 질의·색인 문제)",
    "Z1": "항상 0건 — HyDE도 없음(플랫폼 HyDE 단계 실패 지속 의심)",
    "E": "오류·타임아웃·자산 폐기 포함(접속·플랫폼 상태 먼저 확인)",
}

#: 교체 전·후 채택 판정(plans/141 §6.3 초안 기준).
ADOPT_CODES: dict[str, str] = {
    "A0": "채택 — 0건률 ≤ 5% · 적중률 유지 이상 · 지연 1.5배 이내",
    "A1": "보류(0건) — 재시도 없는 0건률이 5% 초과 → 다음 후보 R3(HyDE off)",
    "A2": "보류(적중) — 적중률이 5%p 넘게 하락 → 다음 후보 R4(Top K 5 · 후보 30) 또는 되돌림",
    "A3": "보류(지연) — 중앙 지연이 1.5배 초과",
    "AX": "판정 불가 — 같은 리트리벌 ID(교체 전·후가 아님) 또는 측정 없음",
}
EMPTY_RATE_MAX = 0.05
HIT_DROP_TOL = 0.05
LATENCY_RATIO_MAX = 1.5


# ── 루트 찾기 ─────────────────────────────────────────────────────────────────

def _is_root(path: Path) -> bool:
    return (path / "src" / "config.py").is_file() and (path / "config").is_dir()


def resolve_root(cli_root: str | None, *, script_path: Path | None = None,
                 env: dict[str, str] | None = None) -> Path:
    """프로젝트 루트 — `--root` > `INFRA_AGENT_ROOT` > 스크립트 위치 > 기본 경로."""
    env = os.environ if env is None else env
    script_path = script_path or Path(__file__).resolve()
    candidates = [cli_root, env.get("INFRA_AGENT_ROOT"), str(script_path.parent.parent),
                  DEFAULT_ROOT]
    for raw in candidates:
        if raw and _is_root(Path(raw)):
            return Path(raw).resolve()
    tried = ", ".join(str(c) for c in candidates if c)
    raise SystemExit(f"프로젝트 루트를 찾지 못했습니다(src/config.py 기준) — 시도: {tried}\n"
                     f"--root /fsapp/infra-collector-agent 처럼 지정하세요.")


# ── 회차 기록 · 요약 · 판정(순수 함수 — 단위 테스트 대상) ─────────────────────

@dataclass
class Run:
    """한 회 호출 결과(문서군 하나 · 재시도 전 첫 호출)."""

    status: str
    hits: int
    score_min: float | None
    score_max: float | None
    elapsed_ms: int
    hyde: str = ""                       # 가상 답변(없으면 빈 문자열)
    doc_keys: tuple[str, ...] = ()        # 문서 식별(doc_id 또는 제목·소제목)
    titles: tuple[str, ...] = ()
    reason: str = ""
    names: tuple[str, ...] = ()           # 적중 판정용 제목·소제목·파일명
    hit: bool | None = None               # 기대 문서 적중(기대 문서가 없으면 None)
    retried: bool = False                 # 이 회차를 다시 호출했는가(--with-retry)
    retry_hits: int | None = None         # 재시도 결과 건수
    retry_hit: bool | None = None         # 재시도 결과의 적중


@dataclass
class CaseSummary:
    label: str
    collection: str
    runs: list[Run] = field(default_factory=list)
    hyde_labels: list[str] = field(default_factory=list)   # 회차별 HyDE 지문(A,B,… / -)
    doc_labels: list[str] = field(default_factory=list)    # 회차별 문서 묶음(1,2,… / -)
    verdict: str = ""


_ERROR_STATUSES = frozenset({"error", "timeout", "stale_id", "blocked_pii", "disabled"})


def _labeler(prefix_letters: bool):
    seen: dict[Any, str] = {}

    def label(key: Any) -> str:
        if key not in seen:
            n = len(seen)
            seen[key] = chr(ord("A") + n) if prefix_letters else str(n + 1)
        return seen[key]
    return label, seen


def summarize(label: str, collection: str, runs: Sequence[Run]) -> CaseSummary:
    """회차 목록 → HyDE·문서 지문 라벨과 판정 코드."""
    out = CaseSummary(label, collection, list(runs))
    hyde_label, _ = _labeler(True)
    doc_label, doc_seen = _labeler(False)
    for run in runs:
        text = " ".join(run.hyde.split())
        out.hyde_labels.append(hyde_label(text) if text else "-")
        out.doc_labels.append(doc_label(run.doc_keys) if run.doc_keys else "-")
    out.verdict = verdict(runs, distinct_docs=len(doc_seen))
    return out


def verdict(runs: Sequence[Run], *, distinct_docs: int) -> str:
    if not runs:
        return "E"
    if any(r.status in _ERROR_STATUSES for r in runs):
        return "E"
    empty = [r for r in runs if r.hits == 0]
    if not empty:
        return "S0" if distinct_docs <= 1 else "S1"
    if len(empty) == len(runs):
        return "Z0" if any(r.hyde.strip() for r in empty) else "Z1"
    return "P1" if any(r.hyde.strip() for r in empty) else "P2"


def overall(summaries: Sequence[CaseSummary]) -> tuple[str, str]:
    """종합 판정(코드 · 문장)."""
    codes = {s.verdict for s in summaries}
    if "E" in codes:
        return "E", "오류가 섞였습니다 — 접속 정보·플랫폼 상태를 먼저 확인한 뒤 다시 실행하세요."
    if codes & {"P1", "P2"}:
        hyde_varies = any(len({h for h in s.hyde_labels if h != "-"}) > 1 for s in summaries)
        tail = " HyDE 문장도 호출마다 달라졌습니다." if hyde_varies else ""
        return "H1", ("같은 요청에 플랫폼이 다른 결과(간헐 0건)를 줍니다 — 채팅·시험 화면 차이는 "
                      "경로가 아니라 플랫폼 비결정입니다." + tail + " 대응 후보: 0건이면 검색 1회 재시도.")
    if codes <= {"S0", "S1"}:
        return "H0", ("이 시점에는 매회 결과가 있습니다 — 플랫폼 비결정이 재현되지 않았습니다. "
                      "채팅에서 다시 0건이 나오면 그 시각의 서버 로그(질의·status·ms)를 함께 보내 주세요.")
    return "H3", ("항상 0건인 질의가 있습니다(Z0/Z1) — 그 질의는 문서·색인·자산 쪽을 확인해야 합니다. "
                  "관리자 시험 화면에서 같은 질의를 지금 다시 돌려 비교하세요.")


def _norm(text: str) -> str:
    return "".join(str(text).split()).lower()


def is_hit(names: Sequence[str], expected: Sequence[str]) -> bool | None:
    """기대 문서 적중 — 제목·소제목·파일명에 기대 문자열이 공백·대소문자 무시로 들어 있는가."""
    wanted = [_norm(e) for e in expected if str(e).strip()]
    if not wanted:
        return None
    haystack = [_norm(n) for n in names if n]
    return any(w in h for w in wanted for h in haystack)


def collection_metrics(cases: Sequence[dict]) -> dict[str, dict[str, Any]]:
    """결과 JSON의 cases → 문서군별 집계(0건률 · 재시도 후 0건률 · 적중률 · 문서 묶음 · 지연)."""
    acc: dict[str, dict[str, Any]] = {}
    for case in cases:
        col = case["collection"]
        a = acc.setdefault(col, {"runs": 0, "empty": 0, "empty_after": 0, "retried": 0,
                                 "hit_n": 0, "hit": 0, "hit_after": 0, "doc_kinds": [],
                                 "ms": [], "retrieval_id_tail": case.get("retrieval_id_tail", "")})
        runs = case.get("runs") or []
        a["doc_kinds"].append(len({r["doc_label"] for r in runs if r.get("doc_label") not in (None, "-")}))
        for r in runs:
            a["runs"] += 1
            empty = r["hits"] == 0
            a["empty"] += empty
            if r.get("retried"):
                a["retried"] += 1
            after_hits = r["retry_hits"] if r.get("retried") and r.get("retry_hits") is not None else r["hits"]
            a["empty_after"] += after_hits == 0
            if r.get("hit") is not None:
                a["hit_n"] += 1
                a["hit"] += bool(r["hit"])
                after_hit = r["retry_hit"] if r.get("retried") and r.get("retry_hit") is not None else r["hit"]
                a["hit_after"] += bool(after_hit)
            if r.get("elapsed_ms"):
                a["ms"].append(r["elapsed_ms"])
    out: dict[str, dict[str, Any]] = {}
    for col, a in acc.items():
        n = a["runs"] or 1
        out[col] = {
            "runs": a["runs"],
            "empty_rate": a["empty"] / n,
            "empty_after_retry_rate": a["empty_after"] / n,
            "retried": a["retried"],
            "hit_rate": (a["hit"] / a["hit_n"]) if a["hit_n"] else None,
            "hit_after_retry_rate": (a["hit_after"] / a["hit_n"]) if a["hit_n"] else None,
            "doc_kinds_avg": statistics.mean(a["doc_kinds"]) if a["doc_kinds"] else 0.0,
            "median_ms": statistics.median(a["ms"]) if a["ms"] else None,
            "retrieval_id_tail": a["retrieval_id_tail"],
        }
    return out


def adopt_verdict(before: dict[str, Any] | None, after: dict[str, Any] | None) -> str:
    """문서군 하나의 교체 전·후 채택 판정 코드(plans/141 §6.3)."""
    if not before or not after:
        return "AX"
    if before.get("retrieval_id_tail") and before.get("retrieval_id_tail") == after.get("retrieval_id_tail"):
        return "AX"
    if after["empty_rate"] > EMPTY_RATE_MAX:
        return "A1"
    hb, ha = before.get("hit_rate"), after.get("hit_rate")
    if hb is not None and ha is not None and ha < hb - HIT_DROP_TOL:
        return "A2"
    mb, ma = before.get("median_ms"), after.get("median_ms")
    if mb and ma and ma > mb * LATENCY_RATIO_MAX:
        return "A3"
    return "A0"


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{v * 100:.0f}%"


def compare_reports(before: dict, after: dict) -> list[str]:
    """교체 전·후 결과 JSON → 출력 줄(옮겨 적을 줄 포함)."""
    mb = before.get("metrics") or collection_metrics(before.get("cases") or [])
    ma = after.get("metrics") or collection_metrics(after.get("cases") or [])
    lines = [
        f"전: {before.get('started_at', '?')} · 코드 {before.get('code', '-')} · 반복 {before.get('repeat')}회",
        f"후: {after.get('started_at', '?')} · 코드 {after.get('code', '-')} · 반복 {after.get('repeat')}회",
        "",
        "문서군        id(전→후)      0건률(전→후)  재시도후 0건률  적중률(전→후)  문서묶음 평균  중앙ms(전→후)",
    ]
    codes: dict[str, str] = {}
    for col in sorted(set(mb) | set(ma)):
        b, a = mb.get(col), ma.get(col)
        code = adopt_verdict(b, a)
        codes[col] = code
        if not b or not a:
            lines.append(f"{col:<13} (한쪽 측정 없음)")
            continue
        ids = f"…{b['retrieval_id_tail']}→…{a['retrieval_id_tail']}"
        lines.append(
            f"{col:<13} {ids:<14} {_pct(b['empty_rate'])}→{_pct(a['empty_rate']):<8} "
            f"{_pct(b['empty_after_retry_rate'])}→{_pct(a['empty_after_retry_rate']):<9} "
            f"{_pct(b['hit_rate'])}→{_pct(a['hit_rate']):<9} "
            f"{b['doc_kinds_avg']:.1f}→{a['doc_kinds_avg']:.1f}{'':<8} "
            f"{b['median_ms'] or '-'}→{a['median_ms'] or '-'}"
        )
    # 질의별 적중 하락(원인 추적용)
    def per_case(rep: dict) -> dict[str, float]:
        out = {}
        for c in rep.get("cases") or []:
            flags = [r["hit"] for r in c.get("runs") or [] if r.get("hit") is not None]
            if flags:
                out[f"{c['label']}/{c['collection']}"] = sum(flags) / len(flags)
        return out
    pb, pa = per_case(before), per_case(after)
    drops = [k for k in pb if k in pa and pa[k] < pb[k]]
    lines.append("")
    if drops:
        lines.append("적중 하락 질의: " + " · ".join(f"{k} {_pct(pb[k])}→{_pct(pa[k])}" for k in drops))
    else:
        lines.append("적중 하락 질의: 없음")
    lines.append("")
    lines.append("옮겨 적을 줄: 위 표 전체 + 아래 「■ 비교 판정」 줄")
    unchanged = [c for c, code in codes.items() if code == "AX"]
    for col, code in codes.items():
        if code != "AX":
            lines.append(f"■ 비교 판정 {col}: {code} — {ADOPT_CODES[code]}")
    for col in unchanged:
        lines.append(f"■ 대조군 {col}: 리트리벌 ID 같음 — 교체하지 않은 문서군(지표 변화는 시간 차 영향 참고)")
    lines.append("판정 코드: " + " / ".join(f"{k}={v}" for k, v in ADOPT_CODES.items()))
    return lines


# ── 실행 ──────────────────────────────────────────────────────────────────────

def _git_head(root: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or "-"
    except Exception:  # noqa: BLE001 — 폐쇄망 배포본은 git이 없을 수 있다
        return "-"


def _run_from_outcome(outcome: Any, query: str) -> Run:
    raw = outcome.raw if isinstance(getattr(outcome, "raw", None), dict) else {}
    hyde = str(getattr(outcome, "hyde_query", "") or raw.get("hyde_query") or "")
    executed = str(getattr(outcome, "executed_query", "") or "")
    if not hyde and executed and executed != query:
        hyde = executed
    scores = [h.rank_score for h in outcome.hits]
    keys = tuple(sorted(h.doc_id or f"{h.title}|{h.subtitle}" for h in outcome.hits))
    titles = tuple(f"{h.title}{(' / ' + h.subtitle) if h.subtitle else ''} ({h.rank_score:.2f})"
                   for h in outcome.hits)
    names = tuple(n for h in outcome.hits for n in (h.title, h.subtitle, h.filename) if n)
    return Run(
        status=outcome.status, hits=len(outcome.hits),
        score_min=min(scores) if scores else None, score_max=max(scores) if scores else None,
        elapsed_ms=int(getattr(outcome, "elapsed_ms", 0) or 0), hyde=hyde,
        doc_keys=keys, titles=titles, reason=str(getattr(outcome, "reason", "") or ""),
        names=names,
    )


def _fmt_scores(run: Run) -> str:
    if run.score_min is None:
        return "-"
    return f"{run.score_min:.2f}~{run.score_max:.2f}"


def _tail4(value: str) -> str:
    return f"…{value[-4:]}" if value else "(없음)"


@dataclass
class Case:
    label: str
    sentence: str | None
    query: str
    collections: list
    expected: list[str] = field(default_factory=list)


async def _probe_case(case: Case, args: argparse.Namespace, read_to: float, total_to: float,
                      detail: list[dict], *, show_rows: bool) -> list[CaseSummary]:
    """회차마다 대상 문서군 전부를 호출한다 — 전체 0건이면 엔진처럼 1회 다시 호출(--with-retry)."""
    from src.clients.fabrix_retrieval import retrieve_one

    print(f"\n[{case.label}] {case.sentence or case.query}")
    print(f"  보낸 질의: {case.query!r} → " + " · ".join(
        f"{c.id}({_tail4(c.retrieval_id)})" for c in case.collections))
    per_col: dict[str, list[Run]] = {c.id: [] for c in case.collections}
    for i in range(args.repeat):
        round_runs: dict[str, Run] = {}
        for col in case.collections:
            outcome = await retrieve_one(col, case.query, timeout_sec=read_to,
                                         total_timeout_sec=total_to, include_raw=True)
            run = _run_from_outcome(outcome, case.query)
            run.hit = is_hit(run.names, case.expected)
            round_runs[col.id] = run
        if args.with_retry and all(r.status == "empty" for r in round_runs.values()):
            for col in case.collections:
                again = _run_from_outcome(await retrieve_one(
                    col, case.query, timeout_sec=read_to, total_timeout_sec=total_to,
                    include_raw=True), case.query)
                run = round_runs[col.id]
                run.retried, run.retry_hits = True, again.hits
                run.retry_hit = is_hit(again.names, case.expected)
        for cid, run in round_runs.items():
            per_col[cid].append(run)
        if args.interval > 0 and i + 1 < args.repeat:
            await asyncio.sleep(args.interval)

    summaries: list[CaseSummary] = []
    for col in case.collections:
        runs = per_col[col.id]
        s = summarize(case.label, col.id, runs)
        summaries.append(s)
        if show_rows:
            print(f"  ─ {col.id}")
            print("    회 상태   건수 점수        ms     HyDE        문서 적중 재시도")
            for i, run in enumerate(runs, 1):
                hyde = f"{s.hyde_labels[i-1]}({len(run.hyde)}자)" if run.hyde else "-"
                hit = "-" if run.hit is None else ("O" if run.hit else "X")
                retry = "-" if not run.retried else f"{run.retry_hits}건"
                print(f"    {i:<2} {run.status:<6} {run.hits:<4} {_fmt_scores(run):<11} "
                      f"{run.elapsed_ms:<6} {hyde:<11} {s.doc_labels[i-1]:<4} {hit:<4} {retry}")
        empty = sum(1 for r in runs if r.hits == 0)
        hyde_kinds = len({h for h in s.hyde_labels if h != "-"})
        no_hyde = sum(1 for h in s.hyde_labels if h == "-")
        doc_kinds = len({d for d in s.doc_labels if d != "-"})
        parts = [f"0건 {empty}/{len(runs)}"]
        if args.with_retry:
            after = sum(1 for r in runs if (r.retry_hits if r.retried else r.hits) == 0)
            parts.append(f"재시도후 {after}/{len(runs)}")
        flags = [r.hit for r in runs if r.hit is not None]
        if flags:
            parts.append(f"적중 {sum(flags)}/{len(flags)}")
        parts += [f"HyDE {hyde_kinds}종(없음 {no_hyde})", f"문서묶음 {doc_kinds}종", f"판정 {s.verdict}"]
        print(f"  ▶ 요약 {case.label}/{col.id}: " + " · ".join(parts))
        detail.append({
            "label": case.label, "sentence": case.sentence, "query": case.query,
            "collection": col.id, "expected": list(case.expected),
            "retrieval_id_tail": col.retrieval_id[-4:] if col.retrieval_id else "",
            "verdict": s.verdict,
            "runs": [{
                "n": i, "status": r.status, "hits": r.hits, "score_min": r.score_min,
                "score_max": r.score_max, "elapsed_ms": r.elapsed_ms,
                "hyde_label": s.hyde_labels[i-1], "hyde": r.hyde,
                "doc_label": s.doc_labels[i-1], "titles": list(r.titles), "reason": r.reason,
                "hit": r.hit, "retried": r.retried, "retry_hits": r.retry_hits,
                "retry_hit": r.retry_hit,
            } for i, r in enumerate(runs, 1)],
        })
    return summaries


def load_gold(path: Path) -> list[dict]:
    import yaml

    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = doc.get("queries") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        raise SystemExit(f"{path}: `queries` 목록이 없습니다")
    out = []
    for item in items:
        if not isinstance(item, dict) or not item.get("query") or not item.get("collection"):
            continue
        out.append({"id": str(item.get("id") or f"Q{len(out) + 1}"),
                    "collection": str(item["collection"]), "query": str(item["query"]).strip(),
                    "expected": [str(e) for e in (item.get("expected") or []) if str(e).strip()]})
    return out


def _build_cases(args: argparse.Namespace, config: Any, all_cols: dict, gold: list[dict] | None,
                 ) -> tuple[list[Case], list[str]]:
    """실행할 시험 목록과 건너뛴 사유."""
    from src.infrastructure.doc_sources import routing_collections
    from src.orchestration.doc_command import parse_doc_command
    from src.orchestration.doc_query import max_views

    rag = config.rag
    cases: list[Case] = []
    skipped: list[str] = []

    def cols_of(ids: list[str]) -> list:
        if not ids:
            ids = [c.id for c in routing_collections(rag)][:max_views(config)]
        return [all_cols[i] for i in ids if i in all_cols]

    if gold is not None:
        for g in gold:
            cases.append(Case(g["id"], None, g["query"], cols_of([g["collection"]]), g["expected"]))
    elif args.query:
        cases.append(Case("Q", None, args.query, cols_of(list(args.collection))))
    else:
        if args.chat:
            sentences = [(f"C{i}", s) for i, s in enumerate(args.chat, 1)]
        else:
            only = {x.strip().upper() for x in (args.only or "").split(",") if x.strip()}
            sentences = [(lab, s) for lab, s in DEFAULT_CASES if not only or lab in only]
        for label, sentence in sentences:
            cmd = parse_doc_command(sentence, config)
            if cmd is None:
                skipped.append(f"[{label}] {sentence} — 문서 검색 명령으로 인식되지 않음(배포본·별칭 확인)")
                continue
            ids = list(args.collection) or [v.split(".", 1)[1] for v in cmd.views]
            cases.append(Case(label, sentence, cmd.query, cols_of(ids)))
    if args.only and gold is not None:
        only = {x.strip().upper() for x in args.only.split(",") if x.strip()}
        cases = [c for c in cases if c.label.upper() in only]
    kept = []
    for c in cases:
        bad = [col.id for col in c.collections if not col.usable]
        if bad or not c.collections:
            skipped.append(f"[{c.label}] 사용할 수 없는 문서군 {bad or '(없음)'} — 접속 정보 확인")
        else:
            kept.append(c)
    return kept, skipped


async def main_async(args: argparse.Namespace, root: Path) -> int:
    from src.clients.fabrix_retrieval import timeouts_from_config
    from src.config import load_config
    from src.infrastructure.doc_sources import resolve_collections
    from src.orchestration.doc_query import doc_active

    config = load_config()
    rag = getattr(config, "rag", None)
    if rag is None or not getattr(rag, "enabled", False):
        print("RAG_ENABLED=false — 문서 검색이 꺼져 있어 진단할 수 없습니다.")
        return 5
    all_cols = {c.id: c for c in resolve_collections(rag)}
    read_to, total_to = timeouts_from_config(rag)
    gold = load_gold(Path(args.gold)) if args.gold else None
    cases, skipped = _build_cases(args, config, all_cols, gold)
    started_at = datetime.now()

    print("=" * 72)
    mode = "기준 질의" if gold is not None else ("직접 질의" if args.query else "채팅 문장")
    print(f"RAG 반복 호출 진단 · {started_at:%Y-%m-%d %H:%M:%S} · 코드 {_git_head(root)} · {mode} "
          f"{len(cases)}건 × {args.repeat}회 · 간격 {args.interval:g}초"
          + (" · 재시도 흉내 on" if args.with_retry else ""))
    print(f"채팅 문서 라우팅 활성={doc_active(config)} · 문서군 " + " · ".join(
        f"{c.id}(사용={c.usable} · id {_tail4(c.retrieval_id)})" for c in all_cols.values()))
    for line in skipped:
        print("  ✗ " + line)

    detail: list[dict] = []
    summaries: list[CaseSummary] = []
    show_rows = args.verbose or gold is None
    for case in cases:
        summaries += await _probe_case(case, args, read_to, total_to, detail, show_rows=show_rows)

    metrics = collection_metrics(detail)
    code, text = overall(summaries)
    print("\n" + "=" * 72)
    for col, m in metrics.items():
        print(f"■ 집계 {col}(id …{m['retrieval_id_tail']}): 회차 {m['runs']} · 0건률 {_pct(m['empty_rate'])}"
              + (f" · 재시도후 0건률 {_pct(m['empty_after_retry_rate'])}" if args.with_retry else "")
              + (f" · 적중률 {_pct(m['hit_rate'])}" if m["hit_rate"] is not None else "")
              + (f" · 재시도후 적중률 {_pct(m['hit_after_retry_rate'])}"
                 if args.with_retry and m["hit_rate"] is not None else "")
              + f" · 문서묶음 평균 {m['doc_kinds_avg']:.1f} · 중앙 {m['median_ms'] or '-'}ms")
    print(f"■ 종합 판정 {code}: {text}")
    print("옮겨 적을 줄: 「■」로 시작하는 줄 전부"
          + ("" if gold is not None else " + 위의 「▶ 요약」 줄"))
    print("판정 코드: " + " / ".join(f"{k}={v}" for k, v in VERDICTS.items()))

    name = args.out or f"rag_hyde_repeat_{started_at:%Y%m%d_%H%M%S}.json"
    out_path = Path(args.out_dir) / name
    payload = {"root": str(root), "code": _git_head(root), "repeat": args.repeat,
               "mode": mode, "with_retry": bool(args.with_retry),
               "started_at": f"{started_at:%Y-%m-%d %H:%M:%S}",
               "overall": {"code": code, "text": text}, "metrics": metrics, "cases": detail}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"결과 파일(비교·상세 — HyDE 전문·문서 제목): {out_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="문서 RAG 반복 호출 진단·측정 — 같은 요청에 같은 결과인가 · 맞는 문서가 나오는가",
    )
    p.add_argument("--root", help=f"프로젝트 루트(기본: INFRA_AGENT_ROOT · 스크립트 위치 · {DEFAULT_ROOT})")
    p.add_argument("-n", "--repeat", type=int, default=5, help="반복 횟수(기본 5)")
    p.add_argument("--interval", type=float, default=1.0, help="회차 간격 초(기본 1)")
    p.add_argument("--only", help="일부만(채팅 문장 라벨 A1,B1 또는 기준 질의 id H01,A03)")
    p.add_argument("--chat", action="append", help="임의 채팅 문장(여러 번 지정 가능)")
    p.add_argument("-c", "--collection", action="append", default=[],
                   help="문서군 id 직접 지정(-q와 함께 · 또는 --chat 문서군 덮어쓰기)")
    p.add_argument("-q", "--query", help="검색 질의 직접 지정(채팅 해석을 건너뜀)")
    p.add_argument("--gold", help="기준 질의 세트 YAML(적중률 측정 — testdata/rag_gold/rag_gold_v1.yaml)")
    p.add_argument("--with-retry", action="store_true",
                   help="회차의 모든 대상 문서군이 0건이면 1회 다시 호출(엔진 재시도 흉내 · D-314)")
    p.add_argument("--verbose", action="store_true", help="기준 질의 모드에서도 회차별 행을 출력")
    p.add_argument("--out", help="결과 파일 이름(기본: rag_hyde_repeat_<시각>.json)")
    p.add_argument("--out-dir", default=".", help="결과 파일 폴더(기본: 현재 폴더)")
    p.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"),
                   help="교체 전·후 결과 파일 비교(검색 호출 없음)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.compare:
        before, after = (json.loads(Path(p).read_text(encoding="utf-8")) for p in args.compare)
        print("=" * 72)
        print("RAG 리트리벌 교체 전·후 비교")
        for line in compare_reports(before, after):
            print(line)
        return 0
    if args.query and not args.collection:
        print("-q 를 쓰면 -c 로 문서군을 지정하세요(예: -c hq_manual).")
        return 6
    if args.repeat < 1:
        print("-n 은 1 이상이어야 합니다.")
        return 6
    args.out_dir = str(Path(args.out_dir).resolve())  # 루트로 이동하기 전 작업 폴더 기준으로 고정
    if args.gold:
        args.gold = str(Path(args.gold).resolve())
    root = resolve_root(args.root)
    # 설정 파일(.env·.encenv)은 작업 폴더 기준으로 읽힌다 — 루트로 이동한 뒤 읽는다.
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    logging.basicConfig(level=logging.WARNING)
    started = time.monotonic()
    code = asyncio.run(main_async(args, root))
    print(f"소요 {time.monotonic() - started:.0f}초")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
