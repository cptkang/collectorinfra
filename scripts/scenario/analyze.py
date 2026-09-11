"""산출물 D - 분석기 (plans/94 §6).

**제안까지만 하고 적용은 사람이 한다.** `src/`·`.env`·`plans/`·`docs/17` 을 수정하지
않는다(V10). 산출은 run 디렉터리 안의 제안 문서 6종뿐이다.

두 가지를 제안문에 못 박는다:
  - **1회 관측으로 처방을 제안하지 않는다.** 반복 3회 미만이면 전건 `불안정·보류`다(V18).
  - **대안이 새 플래그를 만들지 않게 한다.** 제안은 사전·프롬프트·결정적 가드·안내문 쪽으로
    유도하고, 신규 `enable_*` 가 불가피하면 그 사실 자체를 사람 판단 항목으로 올린다(D-162).
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from . import REPO_ROOT, utf8_open
from .catalog import Catalog
from .report import build_summary, load_rows

COVERAGE_DOC = REPO_ROOT / "docs" / "30_scenario_coverage.md"

# 순위에 올리기 위한 최소 표본 (§6.3). 이보다 적으면 `판정 불가` 다.
MIN_NODE_SAMPLE = 5
MIN_REPEAT_FOR_PRESCRIPTION = 3

# 관측 -> 처방 축 (§6.5). 결정적 규칙으로 지목만 하고 문구·임계값은 사람이 정한다.
_PRESCRIPTIONS: list[tuple[str, str, str, str]] = [
    ("column_must_not_map", "결정적 금지 매핑 + 사전",
     "config/synonym_seeds/ 보강 · 프로필 금지 매핑 · D-200형 의미 확정 결정 등재", "37 · 61 · 77"),
    ("empty_result", "0건 진단 퍼널",
     "'조건 과잉'과 '대상 부재'를 갈라 응답 문구를 다르게", "82"),
    ("clarify_missing", "역질문 트리거 조건",
     "스코프·존·기간 모호성 판정 확대", "75 · 82 · 90"),
    ("guide_missing", "안내문 카탈로그",
     "미지원 도메인의 결정적 안내 문구(LLM 생성 금지)", "87 · 92 · 55"),
    ("correct_missing", "유사어 퍼지/시맨틱 임계",
     "임계 조정 + 오매칭 부작용을 대조군으로 동시 측정", "61"),
    ("routing", "분해 골드셋 + 순차 계약",
     "decomposition.yaml 보강 · 게이트 조건 조정", "88 · 80"),
    ("partial_silent", "부분 반환 + 사유 구조화",
     "실패한 하위 작업을 응답에 명시", "Known Mistakes(침묵 폴백 금지)"),
    ("timeout", "타임아웃 가드", "전체 상한 · 검출/취소 분리", "D-198 계열"),
    ("control_broken", "금지 규칙 범위 축소",
     "규칙을 좁게 다시 못 박고 정상 동작 재확인", "Known Mistakes(부정 지시 범위)"),
]

# 처방 우선순위 (§6.5). 조용한 오답이 항상 맨 위다.
_PRIORITY = ["silent_wrong", "hang", "crash", "control_broken", "clarify_missing"]


def _table(header: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join(["---"] * len(header)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join("" if c is None else str(c) for c in row) + " |")
    return "\n".join(lines)


def parse_coverage_doc(path: Path = COVERAGE_DOC) -> list[dict[str, str]]:
    """docs/30 커버리지 매트릭스를 읽는다 (Wave S0 산출 · §6.4).

    표 형식: | 계획서 | 기능 | 프롬프트 트리거 | 상태 | 비고 |
    """
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| plans/"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        match = re.match(r"plans/(\d+)", cells[0])
        if not match:
            continue
        rows.append({
            "plan": match.group(1),
            "feature": cells[1],
            "trigger": cells[2],
            "status": cells[3],
            "note": cells[4] if len(cells) > 4 else "",
        })
    return rows


def bottleneck(run_dir: Path, rows: list[dict[str, Any]]) -> str:
    out = ["# 성능 병목 귀속", "",
           "표본 5건 미만인 노드는 순위에 올리지 않는다 - `판정 불가` 다(§6.3).", ""]
    per_node: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        for node, elapsed in (row.get("node_elapsed_ms") or {}).items():
            per_node[node].append(float(elapsed))

    ranked, excluded = [], []
    for node, values in per_node.items():
        entry = [node, len(values), round(statistics.median(values), 1),
                 round(sum(values), 1)]
        (ranked if len(values) >= MIN_NODE_SAMPLE else excluded).append(entry)
    ranked.sort(key=lambda r: r[3], reverse=True)

    out.append("## 기여도 상위 노드")
    out.append("")
    out.append(_table(["노드", "표본", "중앙값(ms)", "합계(ms)"], ranked[:10])
               if ranked else "순위에 올릴 수 있는 노드가 없다 (전건 표본 부족).")
    out.append("")
    if excluded:
        out.append("## 판정 불가 (표본 부족)")
        out.append("")
        out.append(_table(["노드", "표본", "중앙값(ms)", "합계(ms)"], excluded))
        out.append("")

    out.append("## 호출 수와 지연의 분리")
    out.append("")
    out.append('"느린 것"과 "여러 번 부르는 것"은 다른 처방이다.')
    out.append("")
    calls = [r.get("llm_calls") for r in rows if r.get("llm_calls") is not None]
    retries = [r.get("retries") for r in rows if r.get("retries") is not None]
    out.append(_table(
        ["지표", "표본", "합계", "비고"],
        [
            ["llm_calls", len(calls), sum(calls) if calls else 0,
             "-" if calls else "응답에 실리지 않는다 - 트레이스·감사 로그 대조가 필요하다"],
            ["retries", len(retries), sum(retries) if retries else 0,
             "-" if retries else "동일"],
        ],
    ))
    out.append("")

    cold = [r["processing_time_ms"] for r in rows
            if r.get("cache_state") == "cold" and r.get("processing_time_ms")]
    warm = [r["processing_time_ms"] for r in rows
            if r.get("cache_state") == "warm" and r.get("processing_time_ms")]
    out.append("## cold/warm 격차")
    out.append("")
    if cold and warm:
        out.append(f"- cold 중앙값 {statistics.median(cold):.1f}ms · warm 중앙값 {statistics.median(warm):.1f}ms")
    else:
        out.append("- 측정 불가: cold 또는 warm 표본이 없다.")
    out.append("")
    return "\n".join(out) + "\n"


def failure_taxonomy(summary: dict[str, Any]) -> str:
    out = ["# 실패 유형 분류", "",
           "결정적 규칙으로 분류한다(§6.2). `unclassified` 는 분류 체계의 갭이므로 숨기지 않는다.", ""]
    failures = summary.get("failures", [])
    counts = Counter(f["kind"] for f in failures)
    out.append(_table(
        ["유형", "건수", "대표 케이스", "관련 계획서"],
        [
            [
                kind, count,
                next(f["scenario_id"] for f in failures if f["kind"] == kind),
                ", ".join(sorted({
                    f"plans/{p}" for f in failures if f["kind"] == kind for p in f.get("plans", [])
                })) or "-",
            ]
            for kind, count in counts.most_common()
        ] or [["(없음)", 0, "-", "-"]],
    ))
    out.append("")
    if counts.get("empty_result"):
        out.append(
            "> `empty_result` 는 **데이터 부재와 SQL 오류를 구분하지 않은 상태**다. "
            "0건 진단 산출이 없으면 `구분 불가` 로 표기되며, 둘을 섞으면 엉뚱한 곳을 고친다."
        )
        out.append("")
    if counts.get("unclassified"):
        out.append(
            f"> `unclassified` {counts['unclassified']}건 - 분류 규칙 10개 중 어디에도 맞지 않았다. "
            "규칙을 늘릴 후보다."
        )
        out.append("")
    return "\n".join(out) + "\n"


def coverage_gap(summary: dict[str, Any], catalog: Optional[Catalog]) -> str:
    out = ["# 커버리지 갭", "",
           '미커버 사유는 반드시 분류한다. "그냥 없음"은 허용하지 않는다(§6.4).', "",
           "사유는 §6.4 의 네 가지(`프롬프트 트리거 아님` · `미구현` · `카탈로그 미작성` · "
           "`실행 환경 부재`)에 **`분류 미확정`을 더한 다섯 가지**다. docs/30 에서 아직 사람이 "
           "분류하지 않은 계획서를 `트리거 아님`으로 밀어 넣으면 분모에서 조용히 빠지기 때문이다.", ""]
    matrix = parse_coverage_doc()
    if not matrix:
        out.append(
            f"`{COVERAGE_DOC.relative_to(REPO_ROOT)}` 가 없거나 표를 읽지 못했다 - "
            "커버리지 분모를 만들 수 없다. Wave S0 산출물이 선행이다."
        )
        return "\n".join(out) + "\n"

    index = catalog.plans_index() if catalog else {}
    coverage = summary.get("plans_coverage", {})
    rows = []
    for item in matrix:
        plan = int(item["plan"])
        scenarios = index.get(plan, [])
        if item["status"] == "미구현":
            reason = "미구현"
        elif item["trigger"] == "미분류":
            # §6.4 의 네 가지에 없는 다섯 번째다. '트리거 아님'으로 밀어 넣으면 분모에서
            # 조용히 빠져 커버리지가 부풀려진다 - 분류하지 않았다는 사실을 그대로 남긴다.
            reason = "분류 미확정"
        elif item["trigger"] != "가능":
            reason = "프롬프트 트리거 아님"
        elif not scenarios:
            reason = "카탈로그 미작성"
        elif str(plan) not in coverage or coverage[str(plan)]["executed"] == 0:
            reason = "실행 환경 부재"
        else:
            continue
        rows.append([f"plans/{plan}", item["feature"], len(scenarios), reason, item["note"]])

    out.append(_table(["계획서", "기능", "시나리오 수", "미커버 사유", "비고"], rows)
               if rows else "미커버 항목 없음.")
    out.append("")
    return "\n".join(out) + "\n"


def regression(run_dir: Path, summary: dict[str, Any]) -> str:
    out = ["# 회귀", "",
           "같은 프로파일·같은 환경하고만 비교한다. 개발망 run 과 폐쇄망 run 은 비교하지 않는다(§5.3).", ""]
    meta = summary.get("meta", {})
    if int(meta.get("repeat") or 1) < MIN_REPEAT_FOR_PRESCRIPTION:
        out.append(
            f"지연 회귀 **판정 불가** - 반복 {meta.get('repeat')}회는 반복 간 편차와 구별되지 않는다."
        )
        out.append("")
    candidates = sorted(
        (p for p in run_dir.parent.iterdir() if p.is_dir() and p.name < run_dir.name),
        reverse=True,
    )
    for candidate in candidates:
        prev = build_summary(candidate, None)
        if prev.get("meta", {}).get("env") != meta.get("env"):
            continue
        current = summary.get("scenario_verdicts", {})
        previous = prev.get("scenario_verdicts", {})
        rows = [[sid, previous[sid]["verdict"], info["verdict"]]
                for sid, info in sorted(current.items())
                if sid in previous and previous[sid]["verdict"] != info["verdict"]]
        out.append(f"직전 비교 대상: `{candidate.name}`")
        out.append("")
        out.append(_table(["시나리오", "직전", "이번"], rows) if rows else "판정 전환 없음.")
        out.append("")
        return "\n".join(out) + "\n"
    out.append("비교 가능한 직전 run 이 없다.")
    out.append("")
    return "\n".join(out) + "\n"


def countermeasures(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """R군 관측을 처방 축으로 옮긴다 (§6.5)."""
    meta = summary.get("meta", {})
    repeat = int(meta.get("repeat") or 1)
    out = ["# 대안 수립 - 오용·실수·착각 처방 축", "",
           "관측을 처방 **축**으로 지목할 뿐이고 문구·임계값·플래그 결정은 사람이 한다.", ""]

    if repeat < MIN_REPEAT_FOR_PRESCRIPTION:
        out.append(
            f"> **전건 `불안정·보류`.** 반복 {repeat}회로는 LLM 흔들림과 설계 결함을 구별할 수 없다. "
            f"처방 제안은 반복 {MIN_REPEAT_FOR_PRESCRIPTION}회 이상에서만 낸다(§6.5 · V18)."
        )
        out.append("")

    observations: Counter[str] = Counter()
    for row in rows:
        if row.get("forbidden_mode"):
            observations[row["forbidden_mode"]] += 1
        for failed in row.get("failed_assertions", []):
            key = failed.get("key")
            if key == "column_must_not_map":
                observations["column_must_not_map"] += 1
            elif key == "response_modes":
                expected = failed.get("expected") or []
                actual = failed.get("actual")
                if "clarify" in expected and actual == "answer":
                    observations["clarify_missing"] += 1
                elif "guide" in expected and actual == "answer":
                    observations["guide_missing"] += 1
                elif "correct" in expected:
                    observations["correct_missing"] += 1
            elif key == "row_count.min" and row.get("row_count") == 0:
                observations["empty_result"] += 1
    for group in summary.get("misuse", {}).values():
        if group.get("control_broken"):
            observations["control_broken"] += group["control_broken"]

    out.append("## 관측 -> 처방 축")
    out.append("")
    prescribed = [
        [signal, observations[signal], axis, action, owner]
        for signal, axis, action, owner in _PRESCRIPTIONS
        if observations.get(signal)
    ]
    if repeat < MIN_REPEAT_FOR_PRESCRIPTION:
        prescribed = [[*row[:2], row[2], "보류 (반복 부족)", row[4]] for row in prescribed]
    out.append(_table(["관측 신호", "건수", "처방 축", "후보 조치", "소유 계획서"], prescribed)
               if prescribed else "처방 축으로 옮길 관측이 없다.")
    out.append("")

    out.append("## 처방 우선순위")
    out.append("")
    out.append("조용한 오답이 항상 맨 위다 - 사용자가 알아차릴 수 없는 실패이기 때문이다.")
    out.append("")
    ranked = [[i + 1, key, observations.get(key, 0)] for i, key in enumerate(_PRIORITY)]
    out.append(_table(["순위", "신호", "관측"], ranked))
    out.append("")
    out.append("## 제약")
    out.append("")
    out.append("- 1회 관측으로 처방하지 않는다. 3회 중 1회만 어긋난 건은 `불안정`으로 제외한다.")
    out.append("- 신규 `enable_*` 추가가 불가피하다고 판단되면 **그 사실 자체를 사람 판단 항목으로 올린다**(D-162).")
    out.append("")
    return "\n".join(out) + "\n"


def improvement_backlog(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """빈도 x 심각도 우선순위. docs/17 의 FI 후보이며 **자동 등재하지 않는다**(G-9)."""
    severity = {"silent_wrong": 5, "crash": 5, "hang": 4, "guard": 4, "routing": 3,
                "timeout": 3, "document": 3, "semantics": 3, "empty_result": 2,
                "generation": 2, "execution": 2, "retry_exhaustion": 2, "unclassified": 1}
    counts = Counter(f["kind"] for f in summary.get("failures", []))
    for row in rows:
        if row.get("forbidden_mode"):
            counts[row["forbidden_mode"]] += 1

    ranked = sorted(
        ((kind, count, severity.get(kind, 1), count * severity.get(kind, 1))
         for kind, count in counts.items()),
        key=lambda item: item[3], reverse=True,
    )
    out = ["# 개선 백로그 (FI 후보)", "",
           "분석기는 `docs/17_future_improvements.md` 에 **자동 등재하지 않는다** - "
           "FI 번호는 사람이 관리하는 정본이다(G-9).", ""]
    out.append(_table(
        ["순위", "유형", "빈도", "심각도", "점수", "관련 계획서"],
        [
            [
                index + 1, kind, count, sev, score,
                ", ".join(sorted({
                    f"plans/{p}" for f in summary.get("failures", [])
                    if f["kind"] == kind for p in f.get("plans", [])
                })) or "-",
            ]
            for index, (kind, count, sev, score) in enumerate(ranked)
        ] or [["-", "(없음)", 0, 0, 0, "-"]],
    ))
    out.append("")
    return "\n".join(out) + "\n"


def analyze(run_dir: Path, catalog: Optional[Catalog] = None) -> list[Path]:
    """제안 문서 6종을 run 디렉터리 안에만 쓴다."""
    rows = load_rows(run_dir)
    summary_path = run_dir / "summary.json"
    if summary_path.exists():
        with utf8_open(summary_path, "r") as handle:
            summary = json.load(handle)
    else:
        summary = build_summary(run_dir, catalog)

    documents = {
        "bottleneck.md": bottleneck(run_dir, rows),
        "failure_taxonomy.md": failure_taxonomy(summary),
        "coverage_gap.md": coverage_gap(summary, catalog),
        "regression.md": regression(run_dir, summary),
        "countermeasures.md": countermeasures(summary, rows),
        "improvement_backlog.md": improvement_backlog(summary, rows),
    }
    written: list[Path] = []
    for name, body in documents.items():
        path = run_dir / name
        with utf8_open(path, "w") as handle:
            handle.write(body)
        written.append(path)
    return written
