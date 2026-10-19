"""엔진 비교표 — 플레이북 모드 vs HolmesGPT 단독 (plans/187 W7 · plans/154 재사용).

입력은 run(또는 `.bundle`) 디렉터리의 `raw.jsonl`·`scores.jsonl`·`fake_llm.jsonl`
(가짜 LLM 회차만)뿐이다. 실험군은 명세 `arms`의 sre_agent 설정 오버라이드
(`SRE_PLAYBOOK_ENABLED`·`SRE_DEEP_DIVE_MODE`)로 갈린다 — 같은 잡 경로
(`create_service` → `PlaybookWiring`)를 탄다. 출력은 `<run>/engine_compare.{json,md}`.

실행 1건의 엔진 칸(감사 `done` 이벤트의 플레이북 가산 키 · 브리핑 `playbook` 키에서 읽는다):

- `engine`: `playbook`(감사에 `playbook_id`가 있음) · `holmesgpt`(없음).
- `llm_calls`: 플레이북 소견기 호출(`playbook_llm_calls`) + HolmesGPT step
  (`steps` — holmes `num_llm_calls`). `llm_requests_observed`: 가짜 LLM 로그에서
  그 (실험군, 세계, 반복) 구간에 들어온 요청 수(없으면 None).
- `tool_calls`: 세계 서버가 받은 호출(`tool_trace`) 중 헬스체크
  (`list_sources`·`gateway_health`) 제외 · `health_calls`는 따로 센다.
  `cache_real_calls`·`cache_hits`: 플레이북 잡 단위 캐시(감사).
- `top1`·`top_inconclusive`·`hypotheses_n`: 브리핑 1순위 원인 · 플레이북 종합의
  1순위 판단 보류 · 가설 수.
- 원인 적중(`rc1`·`rc3`)은 기존 채점기(`judge.py`) 결과를 그대로 쓴다.

**판정 불가 표기(D-219 — 관측 0 = 판정 불가)**: 플레이북 엔진 실행이 전부 가설 0건이면
그 실험군의 원인 적중은 「판정 불가」다 — 엔진이 틀린 것이 아니라 세계 응답이 결정적
판독기에 관측을 주지 못한 것일 수 있다.

경계(D-139 · §9.5): `sre_agent` 패키지를 import하지 않는다.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from investigation_bench.judge import read_jsonl
from investigation_bench.report import percentile

HEALTH_TOOLS = frozenset({"list_sources", "gateway_health"})
JSON_NAME = "engine_compare.json"
MD_NAME = "engine_compare.md"
UNDECIDABLE = "판정 불가(D-219 — 플레이북 가설 0건 · 관측 0)"

Row = dict[str, Any]


def _done_event(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    for e in raw.get("audit") or []:
        if isinstance(e, Mapping) and e.get("event") == "done":
            return e
    return {}


def _int(v: Any) -> int | None:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _requests_index(fake_rows: Iterable[Mapping[str, Any]]) -> Counter[tuple[str, str, int]]:
    """가짜 LLM 로그 → (실험군, 세계, 반복)별 요청 수."""
    out: Counter[tuple[str, str, int]] = Counter()
    for r in fake_rows:
        if r.get("arm") and r.get("world_id") and r.get("trial") is not None:
            out[(str(r["arm"]), str(r["world_id"]), int(r["trial"]))] += 1
    return out


def engine_row(raw: Mapping[str, Any], score: Mapping[str, Any] | None,
               requests: Mapping[tuple[str, str, int], int] | None = None) -> Row:
    """raw 1행(+채점 1행) → 엔진 비교 1행."""
    done = _done_event(raw)
    briefing = raw.get("briefing") if isinstance(raw.get("briefing"), Mapping) else {}
    pb = briefing.get("playbook") if isinstance(briefing.get("playbook"), Mapping) else {}
    synthesis = pb.get("synthesis") if isinstance(pb.get("synthesis"), Mapping) else {}
    hyps = synthesis.get("hypotheses") or []
    engine = "playbook" if done.get("playbook_id") else "holmesgpt"
    pb_llm = _int(done.get("playbook_llm_calls")) or 0
    steps = _int(raw.get("steps")) or 0
    trace = [t for t in raw.get("tool_trace") or [] if isinstance(t, Mapping)]
    key = (str(raw.get("arm")), str(raw.get("world_id")), int(raw.get("trial") or 0))
    readings = pb.get("readings") if isinstance(pb.get("readings"), Mapping) else {}
    top1 = (score or {}).get("top1") or {}
    return {
        "arm": raw.get("arm"), "world_id": raw.get("world_id"), "split": raw.get("split"),
        "trial": raw.get("trial"), "status": raw.get("status"),
        "valid": (score or {}).get("valid"), "rc1": (score or {}).get("rc1"),
        "rc3": (score or {}).get("rc3"), "failure_code": (score or {}).get("failure_code"),
        "engine": engine,
        "playbook_id": done.get("playbook_id"),
        "playbook_matched_by": done.get("playbook_matched_by"),
        "deep_dive_mode": done.get("deep_dive_mode"),
        "deep_dive_status": done.get("deep_dive_status"),
        "elapsed_s": raw.get("elapsed_s"),
        "llm_calls": pb_llm + steps,
        "playbook_llm_calls": pb_llm if engine == "playbook" else None,
        "holmes_steps": _int(raw.get("steps")),
        "llm_requests_observed": requests.get(key, 0) if requests is not None else None,
        "tool_calls": sum(1 for t in trace if t.get("tool") not in HEALTH_TOOLS),
        "health_calls": sum(1 for t in trace if t.get("tool") in HEALTH_TOOLS),
        "cache_real_calls": _int(done.get("cache_real_calls")),
        "cache_hits": _int(done.get("cache_hits")),
        "hypotheses_n": len(hyps) if engine == "playbook" else None,
        "top_inconclusive": synthesis.get("top_inconclusive") if engine == "playbook" else None,
        "db_split": readings.get("db_split") if engine == "playbook" else None,
        "top1": (top1.get("cause") if isinstance(top1, Mapping) else None),
    }


def _mean(values: Iterable[Any]) -> float | None:
    vs = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return round(sum(vs) / len(vs), 3) if vs else None


def _rate(values: Iterable[Any]) -> float | None:
    vs = [bool(v) for v in values if v is not None]
    return round(sum(vs) / len(vs), 4) if vs else None


def arm_summary(rows: Sequence[Row]) -> dict[str, Any]:
    """실험군 하나의 집계. 원인 적중은 유효표만 분모로 한다(무효표 = F-INFRA)."""
    valid = [r for r in rows if r.get("valid")]
    pb_rows = [r for r in valid if r.get("engine") == "playbook"]
    no_hyp = sum(1 for r in pb_rows if not r.get("hypotheses_n"))
    undecidable = bool(pb_rows) and no_hyp == len(pb_rows)
    return {
        "runs": len(rows), "valid": len(valid),
        "engines": dict(sorted(Counter(str(r.get("engine")) for r in rows).items())),
        "playbook_ids": dict(sorted(Counter(str(r["playbook_id"]) for r in rows
                                            if r.get("playbook_id")).items())),
        "rc1": None if undecidable else _rate(r.get("rc1") for r in valid),
        "rc3": None if undecidable else _rate(r.get("rc3") for r in valid),
        "rc_note": UNDECIDABLE if undecidable else None,
        "elapsed_p50": percentile((r.get("elapsed_s") for r in valid), 50),
        "elapsed_p90": percentile((r.get("elapsed_s") for r in valid), 90),
        "llm_calls_mean": _mean(r.get("llm_calls") for r in valid),
        "llm_requests_mean": _mean(r.get("llm_requests_observed") for r in valid),
        "tool_calls_mean": _mean(r.get("tool_calls") for r in valid),
        "cache_hits_sum": sum(r.get("cache_hits") or 0 for r in valid),
        "cache_real_calls_sum": sum(r.get("cache_real_calls") or 0 for r in valid),
        "deep_dive_status": dict(sorted(Counter(str(r["deep_dive_status"]) for r in pb_rows
                                                if r.get("deep_dive_status")).items())),
        "playbook_no_hypothesis": no_hyp if pb_rows else None,
        "top_inconclusive": sum(1 for r in pb_rows if r.get("top_inconclusive")) if pb_rows
        else None,
        "db_split": dict(sorted(Counter(str(r.get("db_split")) for r in pb_rows).items()))
        if pb_rows else None,
    }


def build(run_dir: Path) -> dict[str, Any]:
    """run 디렉터리 → 비교 결과(dict). 채점 파일이 없으면 적중 칸은 None."""
    run_dir = Path(run_dir)
    raws = read_jsonl(run_dir / "raw.jsonl")
    scores = {(s.get("arm"), s.get("world_id"), s.get("trial")): s
              for s in read_jsonl(run_dir / "scores.jsonl")}
    fake_path = run_dir / "fake_llm.jsonl"
    requests = _requests_index(read_jsonl(fake_path)) if fake_path.exists() else None
    rows = [engine_row(r, scores.get((r.get("arm"), r.get("world_id"), r.get("trial"))), requests)
            for r in raws]
    arms = list(dict.fromkeys(str(r["arm"]) for r in rows))
    splits = list(dict.fromkeys(str(r["split"]) for r in rows))
    summary = {arm: {"all": arm_summary([r for r in rows if r["arm"] == arm]),
                     **{sp: arm_summary([r for r in rows if r["arm"] == arm and r["split"] == sp])
                        for sp in splits}}
               for arm in arms}
    return {"run_id": run_dir.name, "arms": summary, "rows": rows}


def _c(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.3g}"
    if isinstance(v, dict):
        return ", ".join(f"{k} {n}" for k, n in v.items()) or "-"
    return str(v).replace("|", "\\|").replace("\n", " ")


def _pct(v: Any) -> str:
    return "-" if v is None else f"{v * 100:.1f}%"


def render_md(result: Mapping[str, Any]) -> str:
    """비교 결과 → 마크다운(같은 입력 = 같은 바이트)."""
    lines = [f"# 엔진 비교 — {result['run_id']} (plans/187 W7)", "",
             "원인 적중 = 기존 채점기 RC@1·RC@3(유효표 분모) · "
             "LLM 호출 = 플레이북 소견기 + HolmesGPT step · "
             "도구 호출 = 세계 서버 호출(헬스체크 제외) · "
             "캐시 = 플레이북 잡 단위 캐시(실호출/적중).", ""]
    head = ("| 실험군 | 분할 | 유효/전체 | 엔진 | RC@1 | RC@3 | 소요 p50 | 소요 p90 | "
            "LLM 호출 평균 | LLM 요청(관측) | 도구 호출 평균 | 캐시 실호출/적중 | "
            "가설 0건 | 1순위 보류 | 심층 | 비고 |")
    lines += [head, "|" + "---|" * 16]
    for arm, by_split in result["arms"].items():
        for split, s in by_split.items():
            lines.append("| " + " | ".join([
                arm, split, f"{s['valid']}/{s['runs']}", _c(s["engines"]), _pct(s["rc1"]),
                _pct(s["rc3"]), _c(s["elapsed_p50"]), _c(s["elapsed_p90"]), _c(s["llm_calls_mean"]),
                _c(s["llm_requests_mean"]), _c(s["tool_calls_mean"]),
                f"{s['cache_real_calls_sum']}/{s['cache_hits_sum']}",
                _c(s["playbook_no_hypothesis"]),
                _c(s["top_inconclusive"]), _c(s["deep_dive_status"]), _c(s["rc_note"]),
            ]) + " |")
    lines += ["", "## 실행별", "",
              "| 실험군 | 세계 | 엔진 | 플레이북 | RC@1 | 소요 | LLM | 도구 | 가설 수 | "
              "1순위 보류 | db_split | 1순위 원인 |",
              "|" + "---|" * 12]
    for r in result["rows"]:
        lines.append("| " + " | ".join(_c(x) for x in (
            r["arm"], r["world_id"], r["engine"], r["playbook_id"], r["rc1"], r["elapsed_s"],
            r["llm_calls"], r["tool_calls"], r["hypotheses_n"], r["top_inconclusive"],
            r["db_split"], (r["top1"] or "")[:60] or None)) + " |")
    return "\n".join(lines) + "\n"


def write(run_dir: Path) -> tuple[Path, Path]:
    """`<run>/engine_compare.json`·`.md`를 쓴다."""
    result = build(run_dir)
    jp, mp = Path(run_dir) / JSON_NAME, Path(run_dir) / MD_NAME
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                  encoding="utf-8")
    mp.write_text(render_md(result), encoding="utf-8")
    return jp, mp
