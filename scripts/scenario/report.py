"""산출물 B - 리포트 생성기 (plans/94 §5).

**없는 통계를 만들지 않는다.** p95 는 표본 20건 이상일 때만 내고, 반복 1회 결과에는
통계 표기를 붙이지 않는다(§5.3). 수동 검토·제외 목록은 **비우지 않는다** - 판정하지
못한 것을 리포트에서 지우면 커버리지가 부풀려진다.

섹션 순서는 고정이다(11개). 순서가 흔들리면 사람이 매번 리포트 구조를 다시 익혀야 한다.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from . import utf8_open
from .catalog import Catalog

# 표본이 이보다 적으면 p95 를 내지 않는다 (§5.3 · group_metrics.py 와 같은 철학).
P95_MIN_SAMPLE = 20

_VERDICT_ORDER = ("fail", "error", "manual", "pass")


def load_rows(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "raw.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with utf8_open(path, "r") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def load_run_meta(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "run.json"
    if not path.exists():
        return {"meta": {}, "profiles": [], "skipped": []}
    with utf8_open(path, "r") as handle:
        return json.load(handle)


def _worst(verdicts: list[str]) -> str:
    for candidate in _VERDICT_ORDER:
        if candidate in verdicts:
            return candidate
    return "skipped"


def scenario_verdicts(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """시나리오별 판정. 반복 간 결과가 갈리면 `불안정` 이다(§2-4).

    불안정은 합격으로도 불합격으로도 세지 않는다 - 갈리는 것 자체가 보고할 사실이다.
    """
    per_repeat: dict[tuple[str, int], list[str]] = defaultdict(list)
    info: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = (row["scenario_id"], int(row.get("repeat", 0)))
        per_repeat[key].append(row.get("func_verdict", "error"))
        info.setdefault(
            row["scenario_id"],
            {
                "group": row.get("group"),
                "plans": row.get("plans", []),
                "kind": row.get("kind"),
                "pair_id": row.get("pair_id"),
                "profile": row.get("profile"),
            },
        )

    by_scenario: dict[str, list[str]] = defaultdict(list)
    for (scenario_id, _repeat), verdicts in per_repeat.items():
        by_scenario[scenario_id].append(_worst(verdicts))

    result: dict[str, dict[str, Any]] = {}
    for scenario_id, verdicts in by_scenario.items():
        distinct = set(verdicts)
        flaky = len(distinct) > 1 and "pass" in distinct and {"fail", "error"} & distinct
        result[scenario_id] = {
            **info[scenario_id],
            "verdict": "flaky" if flaky else _worst(verdicts),
            "repeats": len(verdicts),
            "distinct": sorted(distinct),
        }
    return result


def _latency_stats(values: list[float]) -> dict[str, Any]:
    """지연 통계. 표본이 부족하면 수치를 만들지 않는다."""
    clean = [v for v in values if v is not None]
    if not clean:
        return {"n": 0, "p50": None, "p95": None, "max": None, "note": "표본 없음"}
    clean.sort()
    stats: dict[str, Any] = {
        "n": len(clean),
        "p50": round(statistics.median(clean), 1),
        "max": round(clean[-1], 1),
        "p95": None,
        "note": "",
    }
    if len(clean) >= P95_MIN_SAMPLE:
        index = max(0, int(round(0.95 * len(clean))) - 1)
        stats["p95"] = round(clean[index], 1)
    else:
        stats["note"] = f"표본 부족 (n={len(clean)} < {P95_MIN_SAMPLE}) - p95 생략"
    return stats


def build_summary(
    run_dir: Path, catalog: Optional[Catalog] = None
) -> dict[str, Any]:
    """summary.json - 분석기와의 계약 (§7.2)."""
    rows = load_rows(run_dir)
    run = load_run_meta(run_dir)
    verdicts = scenario_verdicts(rows)

    groups: dict[str, dict[str, Any]] = {}
    for group_id in sorted({v["group"] for v in verdicts.values() if v.get("group")}):
        members = [v for v in verdicts.values() if v.get("group") == group_id]
        counts = Counter(v["verdict"] for v in members)
        latencies = [
            row.get("processing_time_ms")
            for row in rows
            if row.get("group") == group_id and row.get("processing_time_ms") is not None
        ]
        perf = Counter(
            row.get("perf_verdict") for row in rows if row.get("group") == group_id
        )
        target = (
            catalog.groups[group_id].latency_target_ms
            if catalog and group_id in catalog.groups
            else None
        )
        groups[group_id] = {
            "total": len(members),
            "pass": counts.get("pass", 0),
            "fail": counts.get("fail", 0),
            "error": counts.get("error", 0),
            "manual": counts.get("manual", 0),
            "flaky": counts.get("flaky", 0),
            "target_ms": target,
            "latency": _latency_stats([float(v) for v in latencies]),
            "perf_pass": perf.get("pass", 0),
            "perf_fail": perf.get("fail", 0),
            "perf_na": perf.get("n/a", 0),
        }

    plans_coverage: dict[str, dict[str, Any]] = {}
    if catalog:
        for plan, scenario_ids in sorted(catalog.plans_index().items()):
            executed = [verdicts[s] for s in scenario_ids if s in verdicts]
            plans_coverage[str(plan)] = {
                "scenarios": len(scenario_ids),
                "executed": len(executed),
                "pass": sum(1 for v in executed if v["verdict"] == "pass"),
                "scenario_ids": scenario_ids,
            }

    misuse: dict[str, dict[str, Any]] = {}
    for kind_group in ("R1", "R2", "R3", "R4"):
        members = [
            row for row in rows if str(row.get("group", "")).upper() == kind_group
        ]
        if not members:
            continue
        modes = Counter(row.get("response_mode") for row in members)
        forbidden = Counter(
            row["forbidden_mode"] for row in members if row.get("forbidden_mode")
        )
        pairs_broken = _broken_pairs(verdicts, kind_group)
        misuse[kind_group] = {
            "total": len({row["scenario_id"] for row in members}),
            "mode_dist": dict(modes),
            "forbidden": dict(forbidden),
            "control_broken": len(pairs_broken),
            "control_broken_pairs": pairs_broken,
        }

    failures = [
        {
            "scenario_id": row["scenario_id"],
            "turn": row.get("turn"),
            "kind": classify_failure(row),
            "plans": row.get("plans", []),
            "failed_assertions": row.get("failed_assertions", []),
            "forbidden_mode": row.get("forbidden_mode"),
        }
        for row in rows
        if row.get("func_verdict") in ("fail", "error")
    ]

    silent_wrong = sum(
        1 for row in rows if row.get("forbidden_mode") == "silent_wrong"
    )

    return {
        "meta": run.get("meta", {}),
        "profiles": run.get("profiles", []),
        "groups": groups,
        "plans_coverage": plans_coverage,
        "misuse": misuse,
        "failures": failures,
        "skipped": run.get("skipped", []),
        "silent_wrong_total": silent_wrong,
        "scenario_verdicts": verdicts,
    }


def _broken_pairs(verdicts: dict[str, dict[str, Any]], group_id: str) -> list[list[str]]:
    """대조군이 함께 깨진 쌍 - 가드가 정상 동작까지 막았다는 뜻이다(§5.2-5 · R12)."""
    broken: list[list[str]] = []
    for scenario_id, info in verdicts.items():
        if str(info.get("group", "")).upper() != group_id:
            continue
        pair = info.get("pair_id")
        if not pair or pair not in verdicts:
            continue
        if info["verdict"] in ("fail", "error") and verdicts[pair]["verdict"] in ("fail", "error"):
            pair_key = sorted([scenario_id, pair])
            if pair_key not in broken:
                broken.append(pair_key)
    return broken


def classify_failure(row: dict[str, Any]) -> str:
    """실패 분류 10규칙 (§6.2). 위에서부터 먼저 맞는 것을 적용한다."""
    keys = {f.get("key") for f in row.get("failed_assertions", [])}
    status_error = row.get("error") or row.get("forbidden_mode") in ("crash",)

    if {"intent", "db_ids"} & keys:
        return "routing"
    if not row.get("executed_sql") and status_error:
        return "generation"
    if {"sql_must_not_match", "column_must_not_map", "response_must_not_contain"} & keys:
        return "guard"
    if row.get("forbidden_mode") == "hang" or "timeout" in str(row.get("error") or "").lower():
        return "timeout"
    if row.get("row_count") == 0 and "row_count.min" in keys:
        # 0건은 데이터 부재일 수도 SQL 오류일 수도 있다. 섞으면 엉뚱한 곳을 고친다.
        return "empty_result"
    if {"file.columns", "file.sheets", "file.filled_rows.min", "has_file"} & keys:
        return "document"
    if "retries.max" in keys:
        return "retry_exhaustion"
    if "sql_must_match" in keys:
        return "semantics"
    if status_error:
        return "execution"
    return "unclassified"


# --- Markdown 렌더 ------------------------------------------------------


def _table(header: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join(["---"] * len(header)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join("" if c is None else str(c) for c in row) + " |")
    return "\n".join(lines)


def render_markdown(summary: dict[str, Any], run_dir: Path, catalog: Optional[Catalog]) -> str:
    meta = summary.get("meta", {})
    out: list[str] = []
    add = out.append

    add(f"# 시나리오 실행 리포트 - {meta.get('run_id', '?')}")
    add("")
    if meta.get("mode") == "mock":
        add(
            "> **모의 실행(--mock)이다.** LLM도 DB도 호출하지 않았다. 기능 판정은 러너·단언기·"
            "리포트 배관이 도는지를 본 것이고 **시스템 품질의 근거가 아니다.**"
        )
        add("")
    if summary.get("silent_wrong_total"):
        add(
            f"> **[최우선] 조용한 오답(silent_wrong) {summary['silent_wrong_total']}건 관측.** "
            "거부와 되묻기는 사용자가 알아차리지만 조용한 오답은 알아차리지 못한다. "
            "5절·6절에서 원인을 먼저 본다."
        )
        add("")

    # 1
    add("## 1. 실행 요약")
    add("")
    add(_table(
        ["항목", "값"],
        [
            ["run_id", meta.get("run_id")],
            ["실행 성격", meta.get("mode")],
            ["대상 환경", meta.get("env")],
            ["LLM 프로바이더", meta.get("provider")],
            ["커밋", meta.get("commit")],
            ["작업 트리 dirty", meta.get("dirty")],
            ["시작 시각", meta.get("started_at")],
            ["반복", meta.get("repeat")],
            ["플랫폼", (meta.get("platform") or {}).get("os")],
            ["콘솔 인코딩", (meta.get("platform") or {}).get("encoding")],
            ["PYTHONUTF8", (meta.get("platform") or {}).get("pythonutf8")],
        ],
    ))
    add("")
    add("### 프로파일별 기동 결과")
    add("")
    add(_table(
        ["프로파일", "포트", "유효", "사다리 단", "설정 에코", "사유"],
        [
            [
                p.get("name"), p.get("port"), "O" if p.get("valid") else "X",
                p.get("tier"),
                {True: "일치", False: "불일치", None: "미확인"}.get(p.get("echo_ok")),
                "; ".join(p.get("reasons") or []) or "-",
            ]
            for p in summary.get("profiles", [])
        ] or [["(없음)", "-", "-", "-", "-", "기동된 프로파일이 없다"]],
    ))
    add("")

    # 2
    add("## 2. 기능 판정 요약")
    add("")
    add("한 칸도 비우지 않는다. `불안정`은 합격으로도 불합격으로도 세지 않는다.")
    add("")
    add(_table(
        ["군", "합격", "불합격", "오류", "수동 검토", "불안정", "계"],
        [
            [g, v["pass"], v["fail"], v["error"], v["manual"], v["flaky"], v["total"]]
            for g, v in sorted(summary.get("groups", {}).items())
        ] or [["(없음)", 0, 0, 0, 0, 0, 0]],
    ))
    add("")

    # 3
    add("## 3. 성능 목표 대조")
    add("")
    add(_table(
        ["군", "목표(ms)", "n", "p50", "p95", "최댓값", "성능 합격", "성능 불합격", "비고"],
        [
            [
                g, v.get("target_ms"), v["latency"]["n"], v["latency"]["p50"],
                v["latency"]["p95"] if v["latency"]["p95"] is not None else "표본 부족",
                v["latency"]["max"], v["perf_pass"], v["perf_fail"],
                v["latency"]["note"] or "-",
            ]
            for g, v in sorted(summary.get("groups", {}).items())
        ] or [["(없음)", "-", 0, "-", "-", "-", 0, 0, "-"]],
    ))
    add("")

    # 4
    add("## 4. 계획서 커버리지")
    add("")
    coverage = summary.get("plans_coverage", {})
    add(_table(
        ["계획서", "시나리오 수", "실행", "합격"],
        [[f"plans/{k}", v["scenarios"], v["executed"], v["pass"]]
         for k, v in sorted(coverage.items(), key=lambda kv: int(kv[0]))]
        or [["(없음)", 0, 0, 0]],
    ))
    add("")
    add("시나리오 0건인 구현 기능은 `coverage_gap.md`(분석기 산출)에 사유와 함께 나온다.")
    add("")

    # 5
    add("## 5. 오용·실수·착각 대응(R군)")
    add("")
    misuse = summary.get("misuse", {})
    if not misuse:
        add("R군 실행 결과가 없다. (선택 범위에 R군이 포함되지 않았다)")
    else:
        add(_table(
            ["하위군", "시나리오", "대응 등급 분포", "금지 등급", "대조군 동반 실패"],
            [
                [
                    key, v["total"],
                    ", ".join(f"{m}:{c}" for m, c in sorted(v["mode_dist"].items())),
                    ", ".join(f"{m}:{c}" for m, c in sorted(v["forbidden"].items())) or "0",
                    v["control_broken"],
                ]
                for key, v in sorted(misuse.items())
            ],
        ))
        add("")
        for key, v in sorted(misuse.items()):
            for pair in v.get("control_broken_pairs", []):
                add(f"- **과잉 거부 의심** {key}: `{pair[0]}` 와 대조군 `{pair[1]}` 가 함께 깨졌다.")
    add("")

    # 6
    add("## 6. 불합격 상세")
    add("")
    failures = summary.get("failures", [])
    if not failures:
        add("불합격 0건.")
    else:
        for item in failures:
            add(f"### {item['scenario_id']} (턴 {item.get('turn')}) - `{item['kind']}`")
            add("")
            if item.get("forbidden_mode"):
                add(f"- **금지 등급**: `{item['forbidden_mode']}`")
            for failed in item.get("failed_assertions", []):
                add(
                    f"- 단언 `{failed.get('key')}` 기대 `{failed.get('expected')}` "
                    f"실제 `{failed.get('actual')}`"
                )
            add(f"- 관련 계획서: {', '.join(f'plans/{p}' for p in item.get('plans', [])) or '-'}")
            add(f"- 재현: `python -m scripts.scenario --only {item['scenario_id']} --mock`")
            add("")

    # 7
    add("## 7. 노드별 지연 분해")
    add("")
    node_totals: dict[str, list[float]] = defaultdict(list)
    for row in load_rows(run_dir):
        for node, elapsed in (row.get("node_elapsed_ms") or {}).items():
            node_totals[node].append(float(elapsed))
    ranked = sorted(
        node_totals.items(), key=lambda kv: statistics.median(kv[1]), reverse=True
    )
    add(_table(
        ["노드", "표본", "중앙값(ms)", "합계(ms)"],
        [[node, len(vals), round(statistics.median(vals), 1), round(sum(vals), 1)]
         for node, vals in ranked[:15]] or [["(없음)", 0, "-", "-"]],
    ))
    add("")
    add("기전 설명 없는 지연은 신뢰하지 않는다. 표본 5건 미만 노드는 분석기가 순위에 올리지 않는다.")
    add("")

    # 8
    add("## 8. 직전 run 대비 회귀")
    add("")
    add(_regression_section(run_dir, summary))
    add("")

    # 9
    add("## 9. 수동 검토 목록")
    add("")
    manual_rows = [
        [row["scenario_id"], row.get("turn"), note]
        for row in load_rows(run_dir)
        for note in (row.get("manual_notes") or [])
    ]
    add(_table(["시나리오", "턴", "무엇을 눈으로 봐야 하는가"], manual_rows)
        if manual_rows else "수동 검토 항목 없음.")
    add("")

    # 10
    add("## 10. 제외·무효 목록")
    add("")
    skipped = summary.get("skipped", [])
    add(_table(
        ["시나리오", "턴", "사유"],
        [[s.get("scenario_id"), s.get("turn", "-"), s.get("reason")] for s in skipped],
    ) if skipped else "제외 0건.")
    add("")

    # 11
    add("## 11. 재현 명령")
    add("")
    add("```bash")
    add(f"python -m scripts.scenario --report {meta.get('run_id')}   # 이 리포트 재생성 (무과금)")
    add(f"python -m scripts.scenario --analyze {meta.get('run_id')}  # 분석·대안 수립 (무과금)")
    add("```")
    add("")
    return "\n".join(out) + "\n"


def _regression_section(run_dir: Path, summary: dict[str, Any]) -> str:
    """직전 run 과 비교한다. **같은 프로파일·같은 환경**하고만 비교한다(§5.3)."""
    parent = run_dir.parent
    others = sorted(
        (p for p in parent.iterdir() if p.is_dir() and p.name < run_dir.name), reverse=True
    )
    meta = summary.get("meta", {})
    for candidate in others:
        prev = build_summary(candidate, None)
        prev_meta = prev.get("meta", {})
        if prev_meta.get("env") != meta.get("env") or prev_meta.get("mode") != meta.get("mode"):
            continue
        lines = [f"직전 비교 대상: `{candidate.name}` (같은 환경 `{meta.get('env')}` · 같은 성격 `{meta.get('mode')}`)", ""]
        current = summary.get("scenario_verdicts", {})
        previous = prev.get("scenario_verdicts", {})
        rows = [
            [sid, previous[sid]["verdict"], info["verdict"]]
            for sid, info in sorted(current.items())
            if sid in previous and previous[sid]["verdict"] != info["verdict"]
        ]
        if not rows:
            lines.append("판정이 바뀐 시나리오 없음.")
        else:
            lines.append(_table(["시나리오", "직전", "이번"], rows))
        if meta.get("repeat", 1) < 3:
            lines.append("")
            lines.append(
                "지연 회귀는 판정하지 않았다 - 반복 3회 미만이라 편차와 구별되지 않는다(`판정 불가`)."
            )
        return "\n".join(lines)
    return "비교 가능한 직전 run 이 없다 (같은 환경·같은 성격의 run 필요)."


def write_report(run_dir: Path, catalog: Optional[Catalog] = None) -> dict[str, Path]:
    """report.md 와 summary.json 을 쓴다."""
    summary = build_summary(run_dir, catalog)
    summary_path = run_dir / "summary.json"
    with utf8_open(summary_path, "w") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    report_path = run_dir / "report.md"
    with utf8_open(report_path, "w") as handle:
        handle.write(render_markdown(summary, run_dir, catalog))
    return {"summary": summary_path, "report": report_path}
