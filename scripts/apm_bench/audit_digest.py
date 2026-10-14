"""폐쇄망 본체 감사 로그 → 화이트리스트 진단표 `audit_digest.json` (plans/164 §4.0 W0a).

벤치 1회차 산출물이 없을 때 본체 감사 줄(`logs/audit-*.jsonl`)만으로 시나리오 단위 원인 분포를
복원한다. `logs/`는 반출하지 않는다(D-323 부기 ⓑ) — 반출하는 것은 **코드가 어휘를 강제한 파생
요약 1개**다(D-219 ② 서버 측 축소와 같은 방향).

- 입력: `host_investigation`(backend `apm_gateway`) · `clarification_issued` 줄만 쓴다. 그 밖의
  줄(`query_execution` 등)·창 밖·다른 사용자·thread_id 없음은 **건수만** 머리에 낸다.
- 대응: 감사 줄을 thread_id로 묶어 첫 줄 시각 순으로 놓고, 기대 시나리오열과 순서 보존 정렬(DP)
  한다. 짝이 **모든 최적 정렬에 공통**이고 보기가 겹칠 때만 `exact` — 갈리면 `ambiguous`(추측 금지).
- 출력: 잎은 수·불·null 또는 아래 어휘의 문자열뿐이다. 쓰기 전에 키까지 같은 어휘로 다시 검사해
  하나라도 벗어나면 파일을 쓰지 않는다(exit 1). thread_id·타임스탬프 원값은 순서 번호로 대신한다.
- 집계는 대응된 스레드(`exact`·`ambiguous`)만 센다 — `unmatched`(혼입 의심)는 건수만.

**`apm_gateway`·본체를 import하지 않는다(D-274 · D-139)** — 게이트웨이 오류 코드는 `record.py`의
벤치 유일 사본(`GATEWAY_ERROR_CODES`)을 쓰고, 정본 대조 테스트는 그 사본 하나만 본다(plans/164 W2a).
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import REPO_ROOT
from .record import GATEWAY_ERROR_CODES
from .rundir import display
from .scenarios import Scenario

APM_BACKEND = "apm_gateway"
EV_INVESTIGATION = "host_investigation"
EV_CLARIFICATION = "clarification_issued"
OUTCOMES = ("ok", "partial", "failed", "denied", "timeout")
#: 본체 사유 어휘(`src/orchestration/investigation_audit.py` · `apm_query.py` `_refusal` 코드).
BODY_REASONS: frozenset[str] = frozenset({
    "apm_unresolved_condition", "apm_unresolved_selection", "apm_target_unresolved",
    "apm_calls_failed", "apm_not_queried", "apm_job_accepted", "apm_partial_sources",
    "access_denied", "target_unavailable", "source_unavailable", "host_unavailable",
    "inspect_failed", "no_result", "exception", "error",
})
#: 대상 텍스트 해석 실패 자유 문장(`src/orchestration/apm_query.py` `_named_unresolved`·대상 해석 —
#: 「대상 '<이름>' 해석 0건」·「대상 '<이름>' 이름 길이 초과」) → 코드. **전체 일치만** 인식하고
#: 따옴표 안 값은 싣지 않는다(잘려 문형이 깨지면 버린다).
TARGET_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"대상 '.+' 해석 0건", re.S), "target_text_unresolved"),
    (re.compile(r"대상 '.+' 이름 길이 초과", re.S), "target_name_too_long"),
)
REASON_CODES = BODY_REASONS | GATEWAY_ERROR_CODES | {c for _, c in TARGET_TEXT_PATTERNS}
#: 되묻기 어휘(`src/api/routes/query.py` `_audit_clarification` 호출부 · `apm_query.py`).
CLARIFY_KINDS = frozenset({"apm_source", "zone_select", "scope_select", "scope_reexpand",
                           "time_period", "peer"})
CLARIFY_AXES = frozenset({"apm_source", "zone_group"})
#: 기대 대상 종류(`Expect.target` 접두).
TARGET_KINDS = frozenset({"none", "untargeted", "host", "inst", "inst_w", "biz", "svc", "ref"})
MAPPINGS = ("exact", "ambiguous", "missing", "unmatched")
TARGET_FAIL_CODES = frozenset({"apm_target_unresolved", "instance_unresolved",
                               "target_unavailable", "target_text_unresolved",
                               "target_name_too_long"})
H2_H5_CODES = ("invalid_argument", "apm_api_error", "source_unavailable", "job_not_ready",
               "job_not_found")
VIEW_RE = re.compile(r"^apm\.[a-z_]+$")
REASON_RE = re.compile(r"^[a-z][a-z0-9_]+")
#: 정렬 비용 — 기대 건너뜀(이벤트 0) < 관측 건너뜀(혼입). 보기가 안 겹치는 짝은 둘 다 건너뛰는
#: 것보다 싸다(짝은 짓되 `ambiguous`로만 싣는다).
COST_SKIP_EXPECTED = 1
COST_SKIP_OBSERVED = 3
COST_MATCH_DISJOINT = 2
#: 정렬 모형(관측 건너뜀 비용 — 기대열 안쪽, 창 가장자리): 혼입 드묾 · 혼입 공짜 · 가장자리만 공짜.
ALIGN_MODELS: tuple[tuple[int, int], ...] = (
    (COST_SKIP_OBSERVED, COST_SKIP_OBSERVED), (0, 0), (COST_SKIP_OBSERVED, 0))
#: 출력 구조 키(어휘 검사 대상 — 여기 없는 키도 쓰기 전 검사에 걸린다).
STRUCT_KEYS = frozenset({
    "input", "files", "lines", "broken", "out_of_window", "other_user", "other_event",
    "no_thread", "used", "expected", "env", "scenarios", "light", "heavy", "heavy_off",
    "skipped", "skipped_turns", "turns", "mapping", "threads", "exact", "ambiguous", "missing",
    "unmatched", "downgraded_silent_neighbor", "downgraded_split_thread", "silent_candidates",
    "silent_lower_bound", "unresolved_scenarios",
    "dropped", "reasons", "views", "outcomes", "kinds",
    "axes", "seq", "scenario_id", "candidates", "split", "turn_count", "target_kinds",
    "expected_views", "observed_views", "events", "event", "outcome", "kind", "axis",
    "other_backend", "order", "expected_silent", "aggregates", "view_outcome", "reason_codes",
    "reason_dropped", "target_resolution", "failed", "rate", "clarifications",
    "zone_select_only_threads", "hypotheses", "T1_H1", "instance_unresolved_threads",
    "after_apm_source_clarification", "T3_T4_A4", "T2_T5", "observable", "H2_H5",
    "source_miss_G1", "missing_expected_apm", "window_estimated",
})
ENVS = ("e1", "e2")
#: 본체 감사 로그 위치(본체 cwd = 저장소 루트 · `src/security/audit_logger.py` `logs/audit-<로컬
#: 날짜>.jsonl` · 크기 로테이션 `audit-<날짜>.<n>.jsonl`).
AUDIT_LOG_DIR = REPO_ROOT / "logs"
#: 창 역전(`--since` > `--until`) 중단 문구 — 날짜 목록이 비어 「로그 없음」으로 오안내하지 않게.
WINDOW_REVERSED = "--since 가 --until 보다 늦다(창 역전) — 두 시각을 바로잡는다"
#: 실행 로그의 소요 문형(`report.md`·안전 요약 콘솔 「소요 N초」) — 이 문형만 읽는다.
ELAPSED_RE = re.compile(r"소요 ([0-9]+(?:\.[0-9]+)?)초")


def local_dates(since: datetime, until: datetime) -> list[date]:
    """창이 걸친 실행 머신 로컬 날짜 전부(자정 넘김 포함 · 감사 로그 파일 이름 = 로컬 날짜)."""
    first, last = since.astimezone().date(), until.astimezone().date()
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def audit_log_files(since: datetime, until: datetime, log_dir: Path) -> list[Path]:
    """창이 걸친 날짜의 감사 로그(로테이션 포함) — 이름 순."""
    found: set[Path] = set()
    for day in local_dates(since, until):
        stem = f"audit-{day.isoformat()}"
        found.update(p for p in (log_dir / f"{stem}.jsonl", *log_dir.glob(f"{stem}.*.jsonl"))
                     if p.is_file())
    return sorted(found)


def elapsed_from_log(text: str) -> float | None:
    """실행 로그 본문 → 마지막 「소요 N초」의 N(없으면 None)."""
    hits = ELAPSED_RE.findall(text)
    return float(hits[-1]) if hits else None


def parse_when(text: str) -> datetime | None:
    """ISO-8601 시각 — 오프셋이 없으면 None(현지 시각 가정 금지)."""
    try:
        when = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo is not None else None


@dataclass
class Thread:
    """관측 스레드 1개(내부 — `tid`·`first`는 출력하지 않는다)."""

    tid: str
    first: tuple[datetime, int]
    views: set[str] = field(default_factory=set)
    events: list[dict[str, Any]] = field(default_factory=list)
    other_backend: int = 0


def reason_code(text: Any, dropped: Counter[str]) -> str | None:
    """사유 원문 → 알려진 코드 접두 · 고정 문형 코드(자유 문장·호스트 이름은 버린다)."""
    if isinstance(text, str):
        for pattern, code in TARGET_TEXT_PATTERNS:
            if pattern.fullmatch(text):
                return code
    m = REASON_RE.match(text) if isinstance(text, str) else None
    if m and m.group() in REASON_CODES:
        return m.group()
    dropped["reasons"] += 1
    return None


def _keep(value: Any, vocab: Iterable[str], dropped: Counter[str], what: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value in vocab:
        return value
    dropped[what] += 1
    return None


def _event(row: Mapping[str, Any], dropped: Counter[str],
           view_ids: frozenset[str]) -> dict[str, Any] | None:
    """감사 줄 1개 → 화이트리스트 이벤트(None = 다른 백엔드 조사 줄)."""
    if row["event"] == EV_CLARIFICATION:
        return {"event": EV_CLARIFICATION,
                "kind": _keep(row.get("kind"), CLARIFY_KINDS, dropped, "kinds"),
                "axis": _keep(row.get("axis"), CLARIFY_AXES, dropped, "axes")}
    if row.get("backend") != APM_BACKEND:
        return None
    views = []
    for view in str(row.get("profile") or "").split(","):
        if view and VIEW_RE.match(view) and view in view_ids:
            views.append(view)
        elif view:
            dropped["views"] += 1
    degraded = row.get("degraded")
    items = degraded if isinstance(degraded, list) else []
    if degraded is not None and not isinstance(degraded, list):
        dropped["reasons"] += 1
    codes = []
    for item in items:
        if isinstance(item, dict):
            codes.append(reason_code(item.get("reason"), dropped))
        else:
            dropped["reasons"] += 1
    return {"event": EV_INVESTIGATION, "views": views,
            "outcome": _keep(row.get("outcome"), OUTCOMES, dropped, "outcomes"),
            "reasons": [c for c in codes if c]}


def read_threads(paths: Sequence[Path], *, since: datetime, until: datetime, user: str,
                 view_ids: frozenset[str]) -> tuple[list[Thread], dict[str, int], Counter[str]]:
    """감사 줄 → 스레드(첫 줄 시각 순) · 입력 건수 · 버린 문자열 건수.

    줄 하나를 다루다 난 예외(깊은 중첩 JSON의 RecursionError · 모양이 다른 칸의 TypeError 등)는
    그 줄을 `broken`으로 센다 — 예외 메시지는 줄 값을 담을 수 있어 내지 않는다.
    """
    counts = dict.fromkeys(("files", "lines", "broken", "out_of_window", "other_user",
                            "other_event", "no_thread", "used"), 0)
    dropped: Counter[str] = Counter()
    rows: list[tuple[datetime, int, str, dict[str, Any] | None]] = []
    for path in paths:
        counts["files"] += 1
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                counts["lines"] += 1
                try:
                    row = json.loads(line)
                    when = (parse_when(str(row.get("timestamp") or ""))
                            if isinstance(row, dict) else None)
                    if when is None:
                        verdict = "broken"
                    elif not since <= when <= until:
                        verdict = "out_of_window"
                    elif row.get("user_id") != user:
                        verdict = "other_user"
                    elif row.get("event") not in (EV_INVESTIGATION, EV_CLARIFICATION):
                        verdict = "other_event"
                    elif not row.get("thread_id"):
                        verdict = "no_thread"
                    else:
                        verdict = "used"
                        item = (when, counts["lines"], str(row["thread_id"]),
                                _event(row, dropped, view_ids))
                except Exception:  # noqa: BLE001 — 줄 단위 격리(메시지 미출력)
                    verdict = "broken"
                counts[verdict] += 1
                if verdict == "used":
                    rows.append(item)
    rows.sort(key=lambda r: (r[0], r[1]))
    threads: dict[str, Thread] = {}
    for when, idx, tid, event in rows:
        thread = threads.setdefault(tid, Thread(tid, (when, idx)))
        if event is None:
            thread.other_backend += 1
            continue
        thread.events.append(event)
        if event["event"] == EV_INVESTIGATION:
            thread.views.update(event["views"])
    ordered = sorted(threads.values(), key=lambda t: t.first)
    return ordered, counts, dropped


def expected_views(scenario: Scenario) -> frozenset[str]:
    """기대 보기 집합(턴 `expect.views` 합집합)."""
    return frozenset(v for turn in scenario.turns for v in turn.expect.views)


def _options(expected: Sequence[frozenset[str]], observed: Sequence[frozenset[str]],
             skip_inner: int, skip_ends: int,
             ) -> tuple[list[set[int | None]], list[set[int | None]]]:
    """비용 모형 하나의 최적 정렬 **전부**에서 각 원소가 가질 수 있는 짝(전방·후방 DP).

    관측 건너뜀 비용은 기대열 안쪽이 `skip_inner`, 기대열 앞·뒤 바깥(창 가장자리)이 `skip_ends`다.
    """
    n, m = len(expected), len(observed)

    def cost(i: int, j: int) -> int:
        return 0 if expected[i] & observed[j] else COST_MATCH_DISJOINT

    def skip_obs(i: int) -> int:
        return skip_ends if i in (0, n) else skip_inner

    inf = float("inf")
    fwd: list[list[float]] = [[inf] * (m + 1) for _ in range(n + 1)]
    fwd[0][0] = 0
    for i in range(n + 1):
        for j in range(m + 1):
            if i:
                fwd[i][j] = min(fwd[i][j], fwd[i - 1][j] + COST_SKIP_EXPECTED)
            if j:
                fwd[i][j] = min(fwd[i][j], fwd[i][j - 1] + skip_obs(i))
            if i and j:
                fwd[i][j] = min(fwd[i][j], fwd[i - 1][j - 1] + cost(i - 1, j - 1))
    bwd: list[list[float]] = [[inf] * (m + 1) for _ in range(n + 1)]
    bwd[n][m] = 0
    for i in range(n, -1, -1):
        for j in range(m, -1, -1):
            if i < n:
                bwd[i][j] = min(bwd[i][j], bwd[i + 1][j] + COST_SKIP_EXPECTED)
            if j < m:
                bwd[i][j] = min(bwd[i][j], bwd[i][j + 1] + skip_obs(i))
            if i < n and j < m:
                bwd[i][j] = min(bwd[i][j], bwd[i + 1][j + 1] + cost(i, j))
    best = fwd[n][m]
    exp_opts: list[set[int | None]] = [set() for _ in range(n)]
    obs_opts: list[set[int | None]] = [set() for _ in range(m)]
    for i in range(n + 1):
        for j in range(m + 1):
            if i < n and fwd[i][j] + COST_SKIP_EXPECTED + bwd[i + 1][j] == best:
                exp_opts[i].add(None)
            if j < m and fwd[i][j] + skip_obs(i) + bwd[i][j + 1] == best:
                obs_opts[j].add(None)
            if i < n and j < m and fwd[i][j] + cost(i, j) + bwd[i + 1][j + 1] == best:
                exp_opts[i].add(j)
                obs_opts[j].add(i)
    return exp_opts, obs_opts


def align(expected: Sequence[frozenset[str]], observed: Sequence[frozenset[str]],
          ) -> tuple[list[set[int | None]], list[set[int | None]]]:
    """순서 보존 정렬 — `ALIGN_MODELS` 각각의 최적 정렬 전부에 걸친 짝 합집합.

    짝이 하나뿐이려면 모든 모형의 최적 정렬 **모두**에 공통이어야 한다 — 갈리면 `ambiguous`(추측
    금지). 모형 하나로는 혼입 모양에 따라 틀린 짝이 유일해진다(외부망 정답 대조 — 창 앞의 시험
    run · 같은 시각 다른 run · 창 안의 여러 run).

    Returns:
        (기대 i → {짝 관측 j | None(건너뜀)}, 관측 j → {짝 기대 i | None})
    """
    exp_opts: list[set[int | None]] = [set() for _ in expected]
    obs_opts: list[set[int | None]] = [set() for _ in observed]
    for inner, ends in ALIGN_MODELS:
        exp_more, obs_more = _options(expected, observed, inner, ends)
        for mine, more in ((exp_opts, exp_more), (obs_opts, obs_more)):
            for opts, extra in zip(mine, more):
                opts |= extra
    return exp_opts, obs_opts


def label_threads(threads: Sequence[Thread], expected: Sequence[Scenario],
                  ) -> tuple[list[tuple[str, Scenario | None, list[Scenario]]],
                             list[Scenario], dict[str, int]]:
    """스레드별 (표지, exact 시나리오, 후보) · `missing` 시나리오(기대 순) · 강등 건수.

    exact 짝 (Ei, Oj)도 다음이면 `ambiguous`로 강등한다(추측 금지):
    - 무이벤트 이웃 — Ei-1 또는 Ei+1이 `missing`(후보 = Ei + 그 이웃). Oj가 실은 이웃 시나리오
      턴이 **틀린 보기**(Ei의 기대 보기)를 고른 것일 수 있다(E2 views 82.6%). 후보가 된 이웃은
      `missing`에서 빼고 `silent_candidates`로 센다. **1칸 밀림만 막는다** — 연속 두 턴이 각각
      앞 시나리오 보기를 고른 2연속 오보기 밀림은 한계다(plans/164 §4.0).
    - 갈린 스레드 — Ei-1이 멀티턴이고 Oj 보기가 Ei-1 기대 보기와도 겹침(후보 = Ei-1 · Ei).
      멀티턴 첫 턴이 done 없이 끝나면 다음 턴이 새 thread다(`driver.py` `thread_id = done.get(
      "thread_id") or thread_id`) — 시나리오 하나가 스레드 둘이 된다.
    """
    exp_sets = [expected_views(s) for s in expected]
    exp_opts, obs_opts = align(exp_sets, [frozenset(t.views) for t in threads])
    silent = {i for i, opts in enumerate(exp_opts) if opts == {None}}
    claimed: set[int] = set()
    labels: list[tuple[str, Scenario | None, list[Scenario]]] = []
    stats = dict.fromkeys(("downgraded_silent_neighbor", "downgraded_split_thread"), 0)
    for j, opts in enumerate(obs_opts):
        cands = sorted(i for i in opts if i is not None)
        if not cands:
            labels.append(("unmatched", None, []))
        elif opts == {cands[0]} and exp_sets[cands[0]] & threads[j].views:
            i = cands[0]
            near = [k for k in (i - 1, i + 1) if k in silent]
            split = (i > 0 and len(expected[i - 1].turns) > 1
                     and bool(exp_sets[i - 1] & threads[j].views))
            if near:
                stats["downgraded_silent_neighbor"] += 1
                claimed.update(near)
                labels.append(("ambiguous", None, [expected[k] for k in sorted([i, *near])]))
            elif split:
                stats["downgraded_split_thread"] += 1
                labels.append(("ambiguous", None, [expected[i - 1], expected[i]]))
            else:
                labels.append(("exact", expected[i], []))
        else:
            labels.append(("ambiguous", None, [expected[i] for i in cands]))
    stats["silent_candidates"] = len(claimed)
    return labels, [expected[i] for i in sorted(silent - claimed)], stats


def _target_kinds(scenario: Scenario) -> list[str]:
    kinds = {re.split(r"[^a-z_]", t.expect.target, maxsplit=1)[0] for t in scenario.turns}
    return sorted(k for k in kinds if k in TARGET_KINDS)


def _scenario_fields(s: Scenario) -> dict[str, Any]:
    return {"scenario_id": s.id, "split": s.split, "turn_count": len(s.turns),
            "target_kinds": _target_kinds(s), "expected_views": sorted(expected_views(s))}


def _silent(s: Scenario) -> bool:
    return all(t.expect.clarify or t.expect.refusal for t in s.turns)


def _codes(row: Mapping[str, Any]) -> list[str]:
    return [c for e in row["events"] if e["event"] == EV_INVESTIGATION for c in e["reasons"]]


def _zone_only(row: Mapping[str, Any]) -> bool:
    kinds = [e["kind"] for e in row["events"] if e["event"] == EV_CLARIFICATION]
    return (bool(kinds) and set(kinds) == {"zone_select"} and not row["other_backend"]
            and all(e["event"] == EV_CLARIFICATION for e in row["events"]))


def _after_source_ask(row: Mapping[str, Any]) -> bool:
    asked = False
    for e in row["events"]:
        if e["event"] == EV_CLARIFICATION and e["kind"] == "apm_source":
            asked = True
        elif asked and "instance_unresolved" in e.get("reasons", ()):
            return True
    return False


def aggregate(rows: Sequence[Mapping[str, Any]], missing: Sequence[Mapping[str, Any]],
              expected: Sequence[Scenario], dropped_reasons: int) -> dict[str, Any]:
    """집계 절 ①~⑥ — 대응된 스레드(`exact`·`ambiguous`)만 센다."""
    view_outcome: dict[str, Counter[str]] = defaultdict(Counter)
    reasons: Counter[str] = Counter()
    clar: Counter[str] = Counter()
    for row in rows:
        for e in row["events"]:
            if e["event"] == EV_INVESTIGATION:
                for v in e["views"]:
                    view_outcome[v][e["outcome"] or "unknown"] += 1
                reasons.update(e["reasons"])
            elif e["kind"]:
                clar[e["kind"]] += 1
    exact = [r for r in rows if r["mapping"] == "exact"]
    target: dict[str, dict[str, Any]] = {}
    for kind in sorted(TARGET_KINDS):
        total = sum(kind in _target_kinds(s) for s in expected)
        hit = [r for r in exact if kind in r["target_kinds"]]
        if not total:
            continue
        failed = sum(bool(TARGET_FAIL_CODES & set(_codes(r))) for r in hit)
        target[kind] = {"scenarios": total, "exact": len(hit), "failed": failed,
                        "rate": round(failed / len(hit), 3) if hit else None}
    splits: dict[str, dict[str, Any]] = {}
    for split in sorted({s.split for s in expected}):
        outs: Counter[str] = Counter(e["outcome"] or "unknown" for r in exact
                                     if r["split"] == split for e in r["events"]
                                     if e["event"] == EV_INVESTIGATION)
        splits[split] = {"scenarios": sum(s.split == split for s in expected),
                         "exact": sum(r["split"] == split for r in exact),
                         "missing": sum(m["split"] == split for m in missing),
                         "outcomes": dict(sorted(outs.items()))}
    zone_only = sum(_zone_only(r) for r in rows)
    return {
        "view_outcome": {v: dict(sorted(c.items())) for v, c in sorted(view_outcome.items())},
        "reason_codes": dict(sorted(reasons.items())), "reason_dropped": dropped_reasons,
        "target_resolution": target,
        "clarifications": {"kinds": dict(sorted(clar.items())),
                           "zone_select_only_threads": zone_only},
        "split": splits,
        "hypotheses": {
            "T1_H1": {"instance_unresolved": reasons["instance_unresolved"],
                      "instance_unresolved_threads":
                          sum("instance_unresolved" in _codes(r) for r in rows),
                      "after_apm_source_clarification": sum(_after_source_ask(r) for r in rows)},
            "T3_T4_A4": {k: v["rate"] for k, v in target.items()},
            "T2_T5": {"observable": False},
            "H2_H5": {c: reasons[c] for c in H2_H5_CODES},
            "source_miss_G1": {"missing_expected_apm": sum(not m["expected_silent"]
                                                           for m in missing),
                               "zone_select_only_threads": zone_only},
        },
    }


def build_digest(threads: Sequence[Thread], counts: Mapping[str, int], dropped: Counter[str],
                 expected: Sequence[Scenario], head: Mapping[str, Any]) -> dict[str, Any]:
    """진단표 객체(화이트리스트 값만)."""
    labels, missing_sc, stats = label_threads(threads, expected)
    rows: list[dict[str, Any]] = []
    for seq, (thread, (label, scenario, cands)) in enumerate(zip(threads, labels), 1):
        if label == "unmatched":
            continue
        row: dict[str, Any] = {"seq": seq, "mapping": label, "scenario_id": None}
        if scenario is not None:
            row.update(_scenario_fields(scenario))
        else:
            row["candidates"] = [s.id for s in cands]
        row.update({"observed_views": sorted(thread.views), "events": thread.events,
                    "other_backend": thread.other_backend})
        rows.append(row)
    order = {s.id: n for n, s in enumerate(expected, 1)}
    missing = [{"order": order[s.id], **_scenario_fields(s), "expected_silent": _silent(s)}
               for s in missing_sc]
    tally = Counter(label for label, _, _ in labels)
    mapping = {"expected": len(expected), "threads": len(threads), "exact": tally["exact"],
               "ambiguous": tally["ambiguous"], "missing": len(missing),
               "unmatched": tally["unmatched"], **stats,
               "silent_lower_bound": max(0, len(expected) - (len(threads) - tally["unmatched"])),
               "unresolved_scenarios": len(expected) - tally["exact"] - len(missing)}
    aggregates = aggregate(rows, missing, expected, dropped["reasons"])
    # 무이벤트 지표 — missing 은 과소(강등 후보로 빠진 이웃) · 하한과 후보 수를 함께 싣는다
    aggregates["hypotheses"]["source_miss_G1"].update(
        {k: mapping[k] for k in ("silent_lower_bound", "silent_candidates")})
    return {"input": dict(counts), "expected": dict(head), "mapping": mapping,
            "dropped": {k: dropped[k] for k in ("reasons", "views", "outcomes", "kinds", "axes")},
            "aggregates": aggregates, "threads": rows, "missing": missing}


def vocabulary(expected: Sequence[Scenario], view_ids: frozenset[str]) -> frozenset[str]:
    """출력에 허용하는 문자열 전부."""
    return (STRUCT_KEYS | REASON_CODES | CLARIFY_KINDS | CLARIFY_AXES | TARGET_KINDS
            | set(OUTCOMES) | set(MAPPINGS) | set(ENVS)
            | {"unknown", EV_INVESTIGATION, EV_CLARIFICATION}
            | {s.id for s in expected} | {s.split for s in expected}
            | {v for v in view_ids if VIEW_RE.match(v)})


def violations(obj: Any, allowed: frozenset[str]) -> int:
    """어휘 밖 문자열(키 포함) 수 — 값은 돌려주지 않는다(그 자체가 유출원일 수 있다)."""
    if isinstance(obj, str):
        return int(obj not in allowed)
    if isinstance(obj, Mapping):
        return sum(violations(k, allowed) + violations(v, allowed) for k, v in obj.items())
    if isinstance(obj, (list, tuple)):
        return sum(violations(v, allowed) for v in obj)
    return int(not (obj is None or isinstance(obj, (bool, int, float))))


def summary_lines(digest: Mapping[str, Any]) -> list[str]:
    """콘솔 요약 — 직렬화된 객체에서만 만든다."""
    agg = digest["aggregates"]
    lines = [f"{k:<11}: {json.dumps(digest[k], ensure_ascii=False)}"
             for k in ("input", "expected", "mapping", "dropped")]
    lines += [f"사유 코드  : {json.dumps(agg['reason_codes'], ensure_ascii=False)}",
              f"대상 해석  : {json.dumps(agg['target_resolution'], ensure_ascii=False)}",
              f"되묻기     : {json.dumps(agg['clarifications'], ensure_ascii=False)}",
              f"가설 지표  : {json.dumps(agg['hypotheses'], ensure_ascii=False)}"]
    return lines


def write_digest(digest: Mapping[str, Any], allowed: frozenset[str], out: Path,
                 say: Callable[[str], None] = print) -> int:
    """자기 점검 → `<out>/audit_digest.json` · 콘솔 요약. 점검 실패면 파일 미기록 · 1."""
    text = json.dumps(digest, ensure_ascii=False, indent=1)
    reread = json.loads(text)
    bad = violations(reread, allowed)
    if bad:
        say(f"[중단] 자기 점검 실패 — 어휘 밖 문자열 {bad}건 · 파일을 쓰지 않았다")
        return 1
    out.mkdir(parents=True, exist_ok=True)
    (out / "audit_digest.json").write_text(text + "\n", encoding="utf-8")
    for line in summary_lines(reread):
        say(line)
    say(f"산출물     : {display(out)}/audit_digest.json")  # 원경로(홈) 미출력
    return 0
