"""플레이북 결정적 경로 오프라인 평가 — W0 녹화 픽스처 사례별 1순위 가설 적중표.

plans/187 W7. 합성 세계(plans/154)의 도구 응답은 HolmesGPT(LLM)가 읽도록 단순화된
모양이라(예: 폴스타 `polestar_metric_trend` 행이 `ts`·`value` — 실 서버는
`stat_date`·`avg_val`+`window`) 플레이북의 결정적 판독기가 관측을 얻지 못한다
(W7 실측 — 24세계 전부 가설 0건). 그래서 플레이북 경로의 원인 적중은 **실 응답 모양
그대로 녹화한** W0 픽스처(`tests/fixtures/plan187/*.json`)로 따로 잰다.

- 경로: 실 `InvestigationDispatcher` + `PlaybookWiring`(녹화 조회 콜러블 · 가짜 소견기 LLM)
  · 심층 모드 `off` — 운영 잡 경로와 같다(`create_service`가 만드는 배선에서 MCP·LLM만
  녹화·가짜로 바꾼 것).
- 정답: 픽스처 `expected.top_hypothesis`(README의 기대 원인). `null`이면 「1순위 확정 가설
  없음」이 정답이다(가설 0건). 문헌 결론 `expected.case_conclusion`(R-D `db_split` 등)은
  따로 대조한다.
- HolmesGPT 단독은 이 경로에 세계·대본이 없어 **비교 불가**다(D-219 — 무효표로 표기).
- 실 LLM·MCP·DB 호출 0. 출력 `<out>/playbook_fixture_eval.{json,md}`.

경계: `_serve.py`와 같이 sre_agent 패키지를 import하는 벤치 파일이다(sre_agent venv에서만
돈다 · 러너·채점기·보고서는 여전히 import 0).
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

TOP = Path(__file__).resolve().parents[2]
FIXTURE_DIR = TOP / "tests" / "fixtures" / "plan187"
JSON_NAME = "playbook_fixture_eval.json"
MD_NAME = "playbook_fixture_eval.md"
OPINION = "수집된 표의 값을 확인했다."
HOLMES_NOT_COMPARABLE = "비교 불가(D-219 — 픽스처 경로에 HolmesGPT 세계·대본 없음 · 무효표)"
UNRECORDED = {"error": "미녹화"}


def _ensure_import_path() -> None:
    if str(TOP) not in sys.path:
        sys.path.insert(0, str(TOP))


class RecordedCaller:
    """녹화 조회 배치 콜러블(README 규칙).

    녹화 args가 호출 args의 부분집합인 것 중 가장 구체적인(키가 많은) 응답을 돌려준다.
    """

    def __init__(self, fixture: Mapping[str, Any]) -> None:
        self.fixture = fixture
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.unrecorded: list[tuple[str, dict[str, Any]]] = []
        self._lock = threading.Lock()

    def lookup(self, tool: str, args: Mapping[str, Any]) -> Any:
        best: Mapping[str, Any] | None = None
        for entry in (self.fixture.get("tool_responses") or {}).get(tool, []):
            if all(args.get(k) == v for k, v in entry["args"].items()):
                if best is None or len(entry["args"]) > len(best["args"]):
                    best = entry
        return None if best is None else best["response"]

    def __call__(self, calls: Sequence[tuple[str, Mapping[str, Any]]]) -> list[str]:
        out = []
        for tool, args in calls:
            resp = self.lookup(tool, args)
            with self._lock:
                self.calls.append((tool, dict(args)))
                if resp is None:
                    self.unrecorded.append((tool, dict(args)))
            out.append(json.dumps(UNRECORDED if resp is None else resp, ensure_ascii=False))
        return out


class CountingLLM:
    """가짜 소견기 LLM — 고정 문장 · 호출 수만 센다."""

    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, messages: list[dict[str, str]]) -> str:
        with self._lock:
            self.calls += 1
        return OPINION


def fixture_alarm(fx: Mapping[str, Any]) -> dict[str, Any]:
    """픽스처 알람 — 폴스타 소스는 `meta.episode.infra_db_ids`로 결정적으로 준다.

    W4 테스트(`test_plan187_w4_dispatch.alarm_of`)와 같은 관례다.
    """
    alarm = copy.deepcopy(dict(fx["alarm"]))
    if not (fx.get("expected") or {}).get("polestar_unresolved"):
        alarm.setdefault("meta", {})["episode"] = {"episode_id": "ep", "infra_db_ids": ["polestar"],
                                                   "members": []}
    return alarm


def load_fixtures(directory: Path = FIXTURE_DIR) -> dict[str, dict[str, Any]]:
    """픽스처 JSON 전부(시나리오 이름 → 내용)."""
    return {p.stem: json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(Path(directory).glob("*.json"))}


def title_hit(expected_top: str | None, hyps: Sequence[Mapping[str, Any]]) -> bool:
    """1순위 제목 일치 — 기대가 없으면 가설 0건이어야 맞다."""
    if expected_top is None:
        return not hyps
    return bool(hyps) and hyps[0].get("title") == expected_top


def top_hit(expected_top: str | None, hyps: Sequence[Mapping[str, Any]],
            top_inconclusive: bool) -> bool:
    """확정 적중 — 제목 일치 + (기대가 있으면) 1순위가 판단 보류가 아님."""
    if expected_top is None:
        return title_hit(None, hyps)
    return title_hit(expected_top, hyps) and not top_inconclusive


def reading_equal(expected: Any, actual: Any) -> bool:
    """판독 값 대조 — 목록의 dict 항목은 기대 키만 본다(실측이 서술 키를 더 실을 수 있다)."""
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(
            all(a.get(k) == v for k, v in e.items()) if isinstance(e, Mapping)
            and isinstance(a, Mapping) else e == a
            for e, a in zip(expected, actual))
    return bool(expected == actual)


def evaluate_one(name: str, fx: Mapping[str, Any], settings: Any, playbooks: Sequence[Any],
                 audit_path: Path) -> dict[str, Any]:
    """픽스처 1건 → 평가 1행(실 dispatcher · 심층 off)."""
    _ensure_import_path()
    from sre_agent.application.briefing_builder import build_briefing
    from sre_agent.application.investigation_dispatcher import (
        InvestigationDispatcher,
        PlaybookWiring,
    )
    from sre_agent.application.investigation_jobs import InvestigationJob
    from sre_agent.diagnosis import DiagnosisResult

    deep_calls: list[Any] = []

    def deep(job: Any) -> DiagnosisResult:
        deep_calls.append(job)
        return DiagnosisResult(answer="", tool_calls=[], tool_outputs=[], total_tokens=0,
                               total_cost=0.0)

    caller, llm = RecordedCaller(fx), CountingLLM()
    d = InvestigationDispatcher(
        settings, diagnose_fn=deep, briefing_fn=build_briefing, audit_path=audit_path,
        playbook=PlaybookWiring(playbooks=tuple(playbooks), polestar=caller, apm=caller, llm=llm),
    )
    now = time.time()
    job = InvestigationJob(investigation_id=f"fx-{name}", kind="alarm", status="accepted",
                           created_at=now, updated_at=now, payload=fixture_alarm(fx))
    t0 = time.monotonic()
    d(job)
    d.wait_workers(60)
    elapsed = round(time.monotonic() - t0, 3)
    done: dict[str, Any] = {}
    for line in audit_path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("event") == "done" and rec.get("investigation_id") == job.investigation_id:
            done = rec
    briefing = job.briefing if isinstance(job.briefing, Mapping) else {}
    pb = briefing.get("playbook") or {}
    synthesis = pb.get("synthesis") or {}
    hyps = list(synthesis.get("hypotheses") or [])
    readings = pb.get("readings") or {}
    expected = fx.get("expected") or {}
    exp_readings = expected.get("readings") or {}
    case = expected.get("case_conclusion") or {}
    top_inconclusive = bool(synthesis.get("top_inconclusive"))
    return {
        "scenario": name, "status": job.status,
        "playbook_id": pb.get("id"), "expected_playbook_id": expected.get("playbook_id"),
        "top1": hyps[0].get("title") if hyps else None,
        "top1_status": hyps[0].get("status") if hyps else None,
        "expected_top": expected.get("top_hypothesis"),
        "top_inconclusive": top_inconclusive,
        "rule_id": hyps[0].get("rule_id") if hyps else None,
        "title_hit": title_hit(expected.get("top_hypothesis"), hyps),
        "hit": top_hit(expected.get("top_hypothesis"), hyps, top_inconclusive),
        "hypotheses_n": len(hyps),
        "readings_match": sum(1 for k, v in exp_readings.items()
                              if reading_equal(v, readings.get(k))),
        "readings_total": len(exp_readings),
        "readings_diff": {k: {"expected": v, "actual": readings.get(k)}
                          for k, v in exp_readings.items()
                          if not reading_equal(v, readings.get(k))},
        "case_conclusion": case or None,
        "case_match": {k: readings.get(k) == v for k, v in case.items()} or None,
        "db_split": readings.get("db_split"),
        "elapsed_s": elapsed,
        "llm_calls": llm.calls,
        "deep_calls": len(deep_calls),
        "deep_dive_status": done.get("deep_dive_status"),
        "tool_calls": len(caller.calls),
        "unrecorded_calls": len(caller.unrecorded),
        "unrecorded_tools": sorted({t for t, _ in caller.unrecorded}),
        "cache_real_calls": done.get("cache_real_calls"),
        "cache_hits": done.get("cache_hits"),
        "holmesgpt": HOLMES_NOT_COMPARABLE,
    }


def offline_settings() -> Any:
    """평가용 설정 — `.env` 무시 · 플레이북 on · 심층 off · 과금 키 공란 · 부가 플래그 off.

    `investigation_llm_enabled=True`는 조사 게이트를 명시로 여는 것뿐이다 — 소견기·심층은
    가짜 콜러블이라 실 LLM 호출 경로가 없다.
    """
    _ensure_import_path()
    from sre_agent.settings import AgentSettings

    return AgentSettings(  # type: ignore[call-arg]
        _env_file=None, model="bench/offline", api_key=None, gemini_api_key=None,
        investigation_llm_enabled=True,
        service_bearer_token=None, investigation_timeout_seconds=300,
        investigation_dedup_ttl_seconds=None, investigation_max_concurrent=1,
        investigation_hourly_budget=None, severity_judge_enabled=False,
        cross_source_investigation_enabled=False, apm_signatures_enabled=False,
        apm_guidance_enabled=False, remediation_recommender_enabled=False,
        sre_playbook_enabled=True, sre_deep_dive_mode="off",
    )


def evaluate(fixtures: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """픽스처 전부 → `{rows, summary}`."""
    _ensure_import_path()
    from sre_agent.infrastructure.playbook_loader import load_playbooks

    fixtures = load_fixtures() if fixtures is None else fixtures
    settings = offline_settings()
    playbooks = load_playbooks().playbooks
    rows = []
    with tempfile.TemporaryDirectory(prefix="pb-fx-eval-") as tmp:
        for name, fx in fixtures.items():
            rows.append(evaluate_one(name, fx, settings, playbooks, Path(tmp) / f"{name}.jsonl"))
    n = len(rows)
    case_rows = [r for r in rows if r["case_match"]]
    summary = {
        "fixtures": n,
        "hit": sum(1 for r in rows if r["hit"]),
        "hit_rate": round(sum(1 for r in rows if r["hit"]) / n, 4) if n else None,
        "title_hit": sum(1 for r in rows if r["title_hit"]),
        "playbook_id_match": sum(1 for r in rows if r["playbook_id"] == r["expected_playbook_id"]),
        "top_inconclusive": sum(1 for r in rows if r["top_inconclusive"]),
        "readings_match": sum(r["readings_match"] for r in rows),
        "readings_total": sum(r["readings_total"] for r in rows),
        "case_conclusion_match": sum(1 for r in case_rows if all(r["case_match"].values())),
        "case_conclusion_total": len(case_rows),
        "llm_calls_mean": round(sum(r["llm_calls"] for r in rows) / n, 3) if n else None,
        "tool_calls_mean": round(sum(r["tool_calls"] for r in rows) / n, 3) if n else None,
        "cache_hits_sum": sum(r["cache_hits"] or 0 for r in rows),
        "unrecorded_calls_sum": sum(r["unrecorded_calls"] for r in rows),
        "holmesgpt": HOLMES_NOT_COMPARABLE,
    }
    return {"rows": rows, "summary": summary}


def _c(v: Any) -> str:
    return "-" if v is None else str(v).replace("|", "\\|").replace("\n", " ")


def render_md(result: Mapping[str, Any]) -> str:
    """평가 결과 → 마크다운."""
    s = result["summary"]
    n = s["fixtures"]
    lines = [
        "# 플레이북 픽스처 평가 — plans/187 W7 (오프라인 · 실 LLM·MCP 0)", "",
        f"- 1순위 확정 적중: {s['hit']}/{n} · 제목 일치 {s['title_hit']}/{n}"
        f" · 플레이북 선택 일치 {s['playbook_id_match']}/{n}"
        f" · 1순위 판단 보류 {s['top_inconclusive']}"
        f" · 판독 enum 일치 {s['readings_match']}/{s['readings_total']}"
        f" · 문헌 결론(case_conclusion) 일치"
        f" {s['case_conclusion_match']}/{s['case_conclusion_total']}",
        f"- LLM 호출 평균 {s['llm_calls_mean']} · 도구 호출 평균 {s['tool_calls_mean']}"
        f" · 캐시 적중 합 {s['cache_hits_sum']} · 미녹화 호출 합 {s['unrecorded_calls_sum']}",
        f"- HolmesGPT 단독: {s['holmesgpt']}", "",
        "- 확정 적중 = 1순위 제목이 기대와 같고 판단 보류가 아님(기대 없음 = 가설 0건)"
        " · 제목 일치 = 보류 여부 무관 · 기대 문구가 사례 특정(예: 「LDAP」·「3시간 주기」)이면"
        " 규칙 일반형 제목과 다를 수 있다 — 규칙 ID를 함께 본다.",
        "",
        "| 시나리오 | 플레이북 | 1순위(실측) | 규칙 | 기대 | 확정 적중 | 제목 일치 | "
        "1순위 보류 | 판독 일치 | db_split | 문헌 결론 | LLM | 도구 | 캐시 실호출/적중 | 소요 s |",
        "|" + "---|" * 15,
    ]
    for r in result["rows"]:
        case = ", ".join(f"{k}={v}" for k, v in (r["case_conclusion"] or {}).items()) or None
        lines.append("| " + " | ".join(_c(x) for x in (
            r["scenario"], r["playbook_id"], r["top1"], r["rule_id"], r["expected_top"],
            "O" if r["hit"] else "X", "O" if r["title_hit"] else "X", r["top_inconclusive"],
            f"{r['readings_match']}/{r['readings_total']}", r["db_split"], case,
            r["llm_calls"], r["tool_calls"], f"{r['cache_real_calls']}/{r['cache_hits']}",
            r["elapsed_s"])) + " |")
    diffs = [r for r in result["rows"] if r["readings_diff"]]
    if diffs:
        lines += ["", "## 판독 enum 불일치", ""]
        for r in diffs:
            for k, v in r["readings_diff"].items():
                lines.append(f"- {r['scenario']} · {k}: "
                             f"기대 {v['expected']!r} · 실측 {v['actual']!r}")
    return "\n".join(lines) + "\n"


def write(out_dir: Path, result: Mapping[str, Any] | None = None) -> tuple[Path, Path]:
    """`<out>/playbook_fixture_eval.{json,md}`를 쓴다."""
    result = evaluate() if result is None else result
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jp, mp = out / JSON_NAME, out / MD_NAME
    jp.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                  encoding="utf-8")
    mp.write_text(render_md(result), encoding="utf-8")
    return jp, mp
