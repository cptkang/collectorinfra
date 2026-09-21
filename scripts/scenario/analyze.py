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
from .report import build_summary, load_rows, valid_rows

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


#: `llm_calls`·`tokens` 표본이 0 일 때 **고정으로 싣는 사유**(O-b).
#:
#: 표본 0 을 그냥 두면 *"측정했는데 0"* 으로 읽힌다 - run 20260915-131903 리포트에서
#: 「호출 수와 지연의 분리」 절이 정확히 그렇게 비어 있었다. **왜 못 재는지**를 적는다.
#: 2026-09-16 실측으로 확인한 사슬이며, 하나라도 바뀌면 이 문구를 고쳐야 한다.
LLM_COST_UNMEASURABLE = """> **`llm_calls`·`tokens` 는 측정했는데 0 인 것이 아니라 «측정할 수 없다».**
> 표본 0 을 값 0 으로 읽지 말 것. 2026-09-16 실측 기준 수집 경로가 **네 지점 모두** 끊겨 있다:
>
> 1. **감사 로그에 LLM 이벤트가 없다** — `src/security/audit_logger.py` 가 내는 이벤트는
>    `query_execution`·`user_request`·`drm_decrypt`·`silence_change`·`host_investigation` 뿐이다.
>    D-217 이 `executed_sqls` 를 가져온 `query_executed` 와 같은 자리가 LLM 쪽에는 없다.
> 2. **`src/llm.py` 가 `usage_metadata` 를 수집하지 않는다** — 프로바이더 응답의 토큰 사용량이
>    어디에도 적재되지 않는다.
> 3. **`AgentState.llm_calls` 는 선언만 있고 쓰는 곳이 없다**(`src/state.py:58`). 초기화도
>    되지 않는다 — `column_deriver` 의 동명 필드는 그 노드 내부 집계라 상태로 올라오지 않는다.
> 4. **`done` SSE 페이로드에 호출 수·토큰 키가 없다** — 하네스가 읽을 표면 자체가 없다.
>
> 즉 이것은 하네스의 결함이 아니라 **제품의 관측성 갭**이고, 소유는 `plans/56`(LLM 관측성)이다.
> 그때까지 「느린 것」과 「여러 번 부르는 것」은 구별되지 않는다 — 대신 `retries`(재생성 회차)와
> `node_count`(실행 노드 회차)를 비용 대리 지표로 쓴다."""

#: 재작성 감사 레코드가 한 건도 없을 때 **고정으로 싣는 사유**(O-e · plans/94 §19.3 · V30).
#:
#: O-b 와 같은 구조다 — 제품이 싣지 않으면 하네스는 모른다. 레코드 0건을 "게이트 통과 0%" ·
#: "검증 실패 0건"으로 읽으면 측정하지 않은 것을 잰 것처럼 보고하게 된다.
REWRITE_TRACE_UNMEASURABLE = """> **재작성 게이트 통과 비율·검증 실패율은 «측정할 수 없다»**
> — 이 run 에 재작성 감사 레코드(`rewrite_trace`)가 한 건도 없다. 비율 칸을 0 으로 채우지 않는다.
>
> 레코드는 서버가 `INTENT_FRAME_ENABLED=true` 일 때만 생긴다(plans/107 — 기본 off).
> 검증 결과는 추가로 `REWRITE_VERIFY_MODE=shadow` 여야 한다.
> 둘 다 켠 프로파일로 다시 돌려야 이 절이 채워진다."""


def rewrite_trace_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """재작성 감사 집계(O-e) — 레코드가 없으면 ``measured=False`` 이고 비율을 만들지 않는다.

    Returns:
        ``{measured, turns, traces, pass_through, pass_through_ratio, verified, verify_failures}``
        (측정되지 않았으면 비율 키는 None)
    """
    traces = [
        t for row in rows for t in (row.get("rewrite_trace") or []) if isinstance(t, dict)
    ]
    turns = sum(1 for row in rows if row.get("rewrite_trace"))
    if not traces:
        return {"measured": False, "turns": 0, "traces": 0, "pass_through": None,
                "pass_through_ratio": None, "verified": None, "verify_failures": None}
    passed = sum(1 for t in traces if (t.get("gate") or {}).get("reason") == "pass_through")
    results = [r for t in traces for r in (t.get("verify") or {}).values()]
    failures = Counter(r for r in results if r != "pass")
    return {
        "measured": True,
        "turns": turns,
        "traces": len(traces),
        "pass_through": passed,
        "pass_through_ratio": round(passed / len(traces), 4),
        "verified": len(results),
        "verify_failures": dict(failures),
    }


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
           "표본 5건 미만인 노드는 순위에 올리지 않는다 - `판정 불가` 다(§6.3).",
           "무효 턴(러너 인증 실패 · T-c)은 분모에서 빠져 있다 - 건수는 리포트 10절.", ""]
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
    tokens = [r.get("tokens") for r in rows if r.get("tokens") is not None]
    retries = [r.get("retries") for r in rows if r.get("retries") is not None]
    out.append(_table(
        ["지표", "표본", "합계", "비고"],
        [
            ["llm_calls", len(calls), sum(calls) if calls else 0,
             "-" if calls else "**측정 불가** - 사유는 아래"],
            ["tokens", len(tokens), sum(tokens) if tokens else 0,
             "-" if tokens else "**측정 불가** - 사유는 아래"],
            ["retries", len(retries), sum(retries) if retries else 0,
             "-" if retries else "회귀 노드가 상위 스트림에 보이지 않는 실행 단"],
        ],
    ))
    out.append("")
    if not calls or not tokens:
        out.append(LLM_COST_UNMEASURABLE)
        out.append("")

    out.append("## 재작성 게이트·검증 (plans/107 · O-e)")
    out.append("")
    rewrite = rewrite_trace_summary(rows)
    if not rewrite["measured"]:
        out.append(REWRITE_TRACE_UNMEASURABLE)
    else:
        failed = sum(rewrite["verify_failures"].values())
        out.append(_table(
            ["지표", "값", "비고"],
            [
                ["레코드(턴)", f"{rewrite['traces']} ({rewrite['turns']}턴)",
                 "SQL 생성 노드 통과 1회 = 1건"],
                ["게이트 통과(pass_through)", f"{rewrite['pass_through']} "
                 f"({rewrite['pass_through_ratio']:.1%})",
                 "높을수록 현행 무조건 재작성이 헛일하고 있었다는 뜻"],
                ["검증 대상 채널", rewrite["verified"],
                 "0 이면 REWRITE_VERIFY_MODE 가 off 였거나 재작성이 없던 턴뿐"],
                ["검증 실패", failed, ", ".join(
                    f"{k} {v}" for k, v in sorted(rewrite["verify_failures"].items())
                ) or "-"],
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


#: 회귀 비교 키의 축(순서 고정 · V29 — plans/110 `94·V29 기준선`). 축을 늘릴 때는 끝에 붙인다.
#: `arm` 은 사다리 단 비교 arm(110·N-1) — 덧씌운 arm 이 없던 옛 run 은 `None` 이다.
COMPARISON_AXES: tuple[str, ...] = ("env", "tier", "base_profile", "arm")


def comparison_keys(summary: dict[str, Any]) -> set[tuple[Any, ...]]:
    """프로파일별 비교 키 ``(env, tier, base_profile, arm)``.

    ``tier`` 가 없거나 ``mock`` 이면 None(미관측)이다 — 미관측은 강등으로 세지 않는다(O-c).
    ``base_profile`` 이 없는 옛 run 은 프로파일 이름을 쓴다(``row.get("arm") or row.get("profile")``
    규약과 같은 폴백).
    """
    env = (summary.get("meta") or {}).get("env")
    keys: set[tuple[Any, ...]] = set()
    for profile in summary.get("profiles") or []:
        base = profile.get("base_profile") or profile.get("name")
        if not base:
            continue
        tier = profile.get("tier")
        keys.add((env, tier if tier and tier != "mock" else None, str(base), profile.get("arm")))
    return keys


def _incompatible(now: set[tuple[Any, ...]], prev: set[tuple[Any, ...]]) -> Optional[str]:
    """두 run 을 비교할 수 없는 사유(비교 가능하면 None).

    이번 run 의 (base_profile, arm) 조합마다 직전 run 에 같은 조합이 있어야 하고, 둘 다 사다리
    단이 관측됐다면 같아야 한다. 이번 run 에 프로파일 기록이 없으면(모의·옛 형식) 제약하지 않는다.
    """
    prev_by_group: dict[tuple[Any, Any], set[Any]] = defaultdict(set)
    for _env, tier, base, arm in prev:
        prev_by_group[(base, arm)].add(tier)
    for _env, tier, base, arm in now:
        label = f"{base}+{arm}" if arm else base
        tiers = prev_by_group.get((base, arm))
        if tiers is None:
            return f"프로파일·arm 구성이 다르다({label} 없음)"
        observed = {t for t in tiers if t is not None}
        if tier is not None and observed and tier not in observed:
            return f"사다리 단이 다르다({label}: {sorted(observed)} → {tier})"
    return None


def select_baseline(
    run_dir: Path, summary: dict[str, Any],
) -> tuple[Optional[Path], dict[str, Any], dict[str, str]]:
    """회귀 기준선 run 을 고른다 — 같은 env · 무효율 상한 이내 · 비교 키가 맞는 가장 최근 run.

    Returns:
        (기준선 run 디렉터리 또는 None, 기준선 요약, {건너뛴 run: 사유}) — env 불일치·무효율
        초과는 종전처럼 사유 없이 건너뛴다(비교 대상이 될 수 없는 run 이다).
    """
    meta = summary.get("meta", {})
    now = comparison_keys(summary)
    skipped: dict[str, str] = {}
    candidates = sorted(
        (p for p in run_dir.parent.iterdir() if p.is_dir() and p.name < run_dir.name),
        reverse=True,
    )
    for candidate in candidates:
        prev = build_summary(candidate, None)
        if prev.get("meta", {}).get("env") != meta.get("env"):
            continue
        if (prev.get("invalid") or {}).get("over_threshold"):
            continue
        # V29: 2단 run(타임아웃 52%)과 3단 run 의 판정 차이는 코드 변화가 아니라 경로 차이다(O-c).
        reason = _incompatible(now, comparison_keys(prev)) if now else None
        if reason:
            skipped[candidate.name] = reason
            continue
        return candidate, prev, skipped
    return None, {}, skipped


def regression(run_dir: Path, summary: dict[str, Any]) -> str:
    out = ["# 회귀", "",
           "같은 프로파일·같은 환경하고만 비교한다. 개발망 run 과 폐쇄망 run 은 비교하지 않는다(§5.3).",
           f"비교 키: `({', '.join(COMPARISON_AXES)})` — "
           "사다리 단·arm 이 다른 run 과는 비교하지 않는다(V29).",
           ""]
    meta = summary.get("meta", {})
    invalid = summary.get("invalid") or {}
    if invalid.get("over_threshold"):
        # T-e: 무효율 5% 초과 run 은 회귀 기준선이 될 수 없다.
        out.append(
            f"회귀 비교 **제외** - 무효 턴 {invalid.get('count')}건"
            f"({float(invalid.get('ratio') or 0):.1%})으로 5% 상한을 넘겼다(T-e). "
            "판정이 바뀐 것인지 측정되지 않은 것인지 구별되지 않는다."
        )
        out.append("")
        return "\n".join(out) + "\n"
    if int(meta.get("repeat") or 1) < MIN_REPEAT_FOR_PRESCRIPTION:
        out.append(
            f"지연 회귀 **판정 불가** - 반복 {meta.get('repeat')}회는 반복 간 편차와 구별되지 않는다."
        )
        out.append("")
    baseline, prev, skipped = select_baseline(run_dir, summary)
    skipped_note = ", ".join(f"`{name}`({why})" for name, why in skipped.items())
    if baseline is None:
        out.append("비교 가능한 직전 run 이 없다.")
        if skipped_note:
            out.append(f"(비교 키가 달라 건너뛴 run: {skipped_note})")
        out.append("")
        return "\n".join(out) + "\n"
    current = summary.get("scenario_verdicts", {})
    previous = prev.get("scenario_verdicts", {})
    rows = [[sid, previous[sid]["verdict"], info["verdict"]]
            for sid, info in sorted(current.items())
            if sid in previous and previous[sid]["verdict"] != info["verdict"]]
    out.append(f"직전 비교 대상: `{baseline.name}`")
    out.append("")
    if skipped_note:
        out.append(f"비교 키가 달라 건너뛴 run: {skipped_note}")
        out.append("")
    out.append(_table(["시나리오", "직전", "이번"], rows) if rows else "판정 전환 없음.")
    out.append("")
    return "\n".join(out) + "\n"


def baseline_record(run_dir: Path, summary: dict[str, Any]) -> dict[str, Any]:
    """`summary.meta.regression_baseline` 에 남길 기록(V29) — 고른 기준선 run id·비교 키·건너뛴 run."""
    baseline, _prev, skipped = select_baseline(run_dir, summary)
    return {
        "run_id": baseline.name if baseline else None,
        "axes": list(COMPARISON_AXES),
        "keys": sorted(
            [list(k) for k in comparison_keys(summary)], key=lambda k: [str(v) for v in k]
        ),
        "skipped": skipped,
    }


def deterministic_failures(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """**시나리오 자체의 반복**이 ≥3 이고 전건 동일 실패인 것(Y-6).

    종전 규칙은 run 단위 `--repeat` 만 보고 전건을 `불안정·보류` 로 내렸다. 그래서
    K-01 **5/5** · K-04 **3/3** · R1-03 **3/3** 동일 실패가 전부 보류로 떨어졌다 -
    부하 묶음(`replay`, D-217)이 시나리오 안에서 이미 여러 번 돌았는데 그 반복을 보지 않았다.

    **같은 실패인가**는 깨진 단언 키 집합으로 본다. 회차마다 다른 곳이 깨지면 그것은
    흔들림이고, 같은 곳이 3회 이상 깨지면 설계 결함이다.
    """
    by_scenario: dict[str, dict[int, tuple[str, tuple[str, ...]]]] = defaultdict(dict)
    for row in rows:
        verdict = str(row.get("func_verdict"))
        signature = tuple(sorted(
            str(f.get("key")) for f in (row.get("failed_assertions") or [])
        ))
        repeat = int(row.get("repeat", 0))
        # 멀티턴은 한 반복에 여러 턴이다 - **깨진 턴**을 그 반복의 대표로 삼는다.
        if repeat not in by_scenario[str(row.get("scenario_id"))] or verdict in ("fail", "error"):
            by_scenario[str(row.get("scenario_id"))][repeat] = (verdict, signature)

    result: dict[str, dict[str, Any]] = {}
    for scenario_id, per_repeat in by_scenario.items():
        outcomes = list(per_repeat.values())
        if len(outcomes) < MIN_REPEAT_FOR_PRESCRIPTION:
            continue
        verdicts = {verdict for verdict, _sig in outcomes}
        signatures = {sig for _verdict, sig in outcomes}
        if verdicts <= {"fail", "error"} and len(signatures) == 1:
            result[scenario_id] = {
                "repeats": len(outcomes),
                "verdict": sorted(verdicts)[0],
                "keys": list(next(iter(signatures))),
            }
    return result


def countermeasures(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """R군 관측을 처방 축으로 옮긴다 (§6.5 · Y-6 개정)."""
    meta = summary.get("meta", {})
    repeat = int(meta.get("repeat") or 1)
    deterministic = deterministic_failures(rows)
    out = ["# 대안 수립 - 오용·실수·착각 처방 축", "",
           "관측을 처방 **축**으로 지목할 뿐이고 문구·임계값·플래그 결정은 사람이 한다.", ""]

    if repeat < MIN_REPEAT_FOR_PRESCRIPTION and not deterministic:
        out.append(
            f"> **전건 `불안정·보류`.** 반복 {repeat}회로는 LLM 흔들림과 설계 결함을 구별할 수 없다. "
            f"처방 제안은 반복 {MIN_REPEAT_FOR_PRESCRIPTION}회 이상에서만 낸다(§6.5 · V18)."
        )
        out.append("")

    observations: Counter[str] = Counter()
    #: 결정적으로 확정된 시나리오에서 나온 신호. 보류 대상이 아니다(Y-6).
    firm: set[str] = set()

    def observe(signal: str, row: dict[str, Any]) -> None:
        observations[signal] += 1
        if str(row.get("scenario_id")) in deterministic:
            firm.add(signal)

    for row in rows:
        if row.get("forbidden_mode"):
            observe(str(row["forbidden_mode"]), row)
        for failed in row.get("failed_assertions", []):
            key = failed.get("key")
            if key == "column_must_not_map":
                observe("column_must_not_map", row)
            elif key == "response_modes":
                expected = failed.get("expected") or []
                actual = failed.get("actual")
                if "clarify" in expected and actual == "answer":
                    observe("clarify_missing", row)
                elif "guide" in expected and actual == "answer":
                    observe("guide_missing", row)
                elif "correct" in expected:
                    observe("correct_missing", row)
            elif key == "row_count.min" and row.get("row_count") == 0:
                observe("empty_result", row)
    for group in summary.get("misuse", {}).values():
        if group.get("control_broken"):
            observations["control_broken"] += group["control_broken"]

    if deterministic:
        out.append("## 결정적으로 확정된 실패 (Y-6)")
        out.append("")
        out.append(
            f"시나리오 자체의 반복이 {MIN_REPEAT_FOR_PRESCRIPTION}회 이상이고 **전건 동일 실패**다 - "
            "LLM 흔들림과 구별된다. run 단위 `--repeat` 이 1회여도 보류하지 않는다."
        )
        out.append("")
        out.append(_table(
            ["시나리오", "반복", "판정", "깨진 단언"],
            [[sid, info["repeats"], info["verdict"], ", ".join(info["keys"]) or "-"]
             for sid, info in sorted(deterministic.items())],
        ))
        out.append("")

    out.append("## 관측 -> 처방 축")
    out.append("")
    prescribed = [
        [signal, observations[signal], axis, action, owner]
        for signal, axis, action, owner in _PRESCRIPTIONS
        if observations.get(signal)
    ]
    if repeat < MIN_REPEAT_FOR_PRESCRIPTION:
        prescribed = [
            row if row[0] in firm else [*row[:3], "보류 (반복 부족)", row[4]]
            for row in prescribed
        ]
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
    out.append(
        f"- **단, 시나리오 반복 {MIN_REPEAT_FOR_PRESCRIPTION}회 이상 전건 동일 실패는 결정적이다**(Y-6) - "
        "run 단위 반복이 1회여도 보류하지 않는다."
    )
    out.append("- 신규 `enable_*` 추가가 불가피하다고 판단되면 **그 사실 자체를 사람 판단 항목으로 올린다**(D-162).")
    out.append("")
    return "\n".join(out) + "\n"


def improvement_backlog(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """빈도 x 심각도 우선순위. docs/17 의 FI 후보이며 **자동 등재하지 않는다**(G-9)."""
    # Y-7 신설 3종: `volume` 은 3(D-05 의 22,813행처럼 상한 위반이 곧 가드 실패다),
    # `clarify`·`contract` 는 2(되묻기·안내 문구는 사용자가 알아차린다 - 조용한 오답이 아니다).
    severity = {"silent_wrong": 5, "crash": 5, "hang": 4, "guard": 4, "routing": 3,
                "timeout": 3, "document": 3, "semantics": 3, "volume": 3, "empty_result": 2,
                "generation": 2, "execution": 2, "retry_exhaustion": 2,
                "clarify": 2, "contract": 2, "rewrite": 3, "unclassified": 1}
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
    """제안 문서 6종을 run 디렉터리 안에만 쓴다.

    **분석의 분모는 유효 턴이다**(T-c). 무효 턴을 섞으면 병목·실패 분류·처방 축이 전부
    401 구간의 그림자를 센다 - run 20260915-131903 에서 `generation` 108건이 백로그
    1순위(점수 216)로 올라간 것이 그 예다.
    """
    rows = valid_rows(load_rows(run_dir))
    summary_path = run_dir / "summary.json"
    if summary_path.exists():
        with utf8_open(summary_path, "r") as handle:
            summary = json.load(handle)
    else:
        summary = build_summary(run_dir, catalog)

    # V29: 고른 회귀 기준선을 요약 메타에 남긴다(문서 6종과 별개 — summary.json 이 있을 때만 갱신).
    if not (summary.get("invalid") or {}).get("over_threshold"):
        summary.setdefault("meta", {})["regression_baseline"] = baseline_record(run_dir, summary)
        if summary_path.exists():
            with utf8_open(summary_path, "w") as handle:
                json.dump(summary, handle, ensure_ascii=False, indent=2)

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
