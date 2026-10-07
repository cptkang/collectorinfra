"""`report.md` · `--compare` (plans/135 §3.7 · W5·W6).

입력은 이미 위생을 거친 레코드(`trace.jsonl` 행)·`run.json`·카탈로그뿐이다 — 값이 새로 생기지
않는다. 숫자는 결정적으로 계산하고 문장은 고정 문구다(LLM 0). 지연은 참고값이다 — 두 평면이
mlx 면 성능 결론을 내지 않는다(D-240) · 그 밖의 평면은 평면 이름을 밝힌다(plans/139 W6-b).
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .judge import SEPARATE, TAXONOMY


def _pct(part: int, whole: int) -> str:
    return f"{part}/{whole} ({part / whole * 100:.0f}%)" if whole else "0/0 (—)"


def _cell(value: Any) -> str:
    text = ", ".join(str(v) for v in value) if isinstance(value, (list, tuple)) else str(value)
    return text.replace("|", "\\|").replace("\n", " ") or "—"


def _oracle_records(records: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [
        r
        for r in records
        if r.get("oracle") and "permission_denied" not in (r.get("taxonomy") or [])
    ]


def _verdicts(records: Iterable[Mapping[str, Any]]) -> Counter[str]:
    return Counter((r["oracle"] or {}).get("verdict") or "none" for r in records)


def _table(header: list[str], rows: Iterable[Iterable[Any]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return lines


def summarize(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """리포트·비교가 함께 쓰는 지표(§3.3 「지표」)."""
    oracle = _oracle_records(records)
    verdicts = _verdicts(oracle)
    contexts = [r for r in records if r.get("schema_context") is not None]
    gold_turns = [r for r in contexts if (r.get("expected") or {}).get("gold_tables")]
    meaning_turns = [
        r
        for r in contexts
        if (r["schema_context"] or {}).get("key_columns_presented")
        and (r["schema_context"] or {}).get("key_columns_with_meaning") is not None
    ]
    sql_entries = [e for r in records for e in r.get("executed_sqls") or []]
    expected_itam = [r for r in records if (r.get("expected") or {}).get("db_ids")]
    precision = recall = 0.0
    scored = 0
    for r in records:
        gold = {t.casefold() for t in (r.get("expected") or {}).get("gold_tables") or []}
        used = {t.casefold() for t in (r.get("sql_analysis") or {}).get("tables") or []}
        if gold and used:
            precision += len(gold & used) / len(used)
            recall += len(gold & used) / len(gold)
            scored += 1
    key_refs = key_hits = 0
    for r in records:
        if not (r.get("sql_analysis") or {}).get("sql_count"):
            continue
        used = {c.casefold() for c in (r.get("sql_analysis") or {}).get("key_columns_used") or []}
        for ref in (r.get("expected") or {}).get("key_columns") or []:
            key_refs += 1
            key_hits += any(str(a).casefold() in used for a in ref)
    return {
        "turns": len(records),
        "oracle_turns": len(oracle),
        "pass": verdicts.get("pass", 0),
        "fail": verdicts.get("fail", 0),
        "hold": verdicts.get("hold", 0),
        "labels": Counter(label for r in records for label in r.get("taxonomy") or []),
        "first_labels": Counter(
            (r.get("taxonomy") or [None])[0] for r in records if r.get("taxonomy")
        ),
        "routing_ok": sum(
            1 for r in expected_itam if "routing_miss" not in (r.get("taxonomy") or [])
        ),
        "routing_total": len(expected_itam),
        "asked_back": sum(1 for r in records if r.get("status") == "clarification"),
        "sql_ok": sum(1 for e in sql_entries if e.get("success") is not False),
        "sql_total": len(sql_entries),
        "no_sql_turns": sum(
            1 for r in records if not (r.get("sql_analysis") or {}).get("sql_count_all")
        ),
        "retries": [r["retries"] for r in records if isinstance(r.get("retries"), int)],
        "context_turns": len(contexts),
        "gold_presented": sum(
            1 for r in gold_turns if r["schema_context"].get("gold_tables_presented")
        ),
        "gold_turns": len(gold_turns),
        "key_meaning": sum(
            len(r["schema_context"]["key_columns_with_meaning"]) for r in meaning_turns
        ),
        "key_presented": sum(
            len(r["schema_context"]["key_columns_presented"]) for r in meaning_turns
        ),
        "sample_rows_turns": sum(
            1 for r in contexts if r["schema_context"].get("sample_rows_presented")
        ),
        "table_precision": precision / scored if scored else None,
        "table_recall": recall / scored if scored else None,
        "key_hits": key_hits,
        "key_refs": key_refs,
        "hazards": Counter(
            h for r in records for h in (r.get("sql_analysis") or {}).get("dialect_hazards") or []
        ),
        "latency": [
            r["latency_ms"] for r in records if isinstance(r.get("latency_ms"), (int, float))
        ],
    }


def _by_category(records: Sequence[Mapping[str, Any]]) -> list[list[Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in _oracle_records(records):
        groups[str(r.get("category"))].append(r)
    rows = []
    for category in sorted(groups):
        verdicts = _verdicts(groups[category])
        rows.append(
            [
                category,
                len(groups[category]),
                verdicts.get("pass", 0),
                verdicts.get("fail", 0),
                verdicts.get("hold", 0),
            ]
        )
    return rows


def _determinism(records: Sequence[Mapping[str, Any]]) -> list[list[Any]]:
    sqls: dict[tuple[str, int], set[str]] = defaultdict(set)
    repeats: Counter[tuple[str, int]] = Counter()
    for r in records:
        key = (str(r["id"]), int(r["turn"]))
        repeats[key] += 1
        last = [e["sql"] for e in r.get("executed_sqls") or [] if e.get("success") is not False]
        sqls[key].add(last[-1] if last else "")
    return [
        [sid, turn, repeats[(sid, turn)], len(sqls[(sid, turn)])]
        for (sid, turn) in sorted(sqls)
        if repeats[(sid, turn)] > 1
    ]


def _row_counts(record: Mapping[str, Any]) -> str:
    """실행 SQL 별 행 수(성공한 것) · 사용자가 받은 결과 행 수."""
    counts = [
        str(e.get("row_count"))
        for e in record.get("executed_sqls") or []
        if e.get("success") is not False and e.get("row_count") is not None
    ]
    total = (record.get("result") or {}).get("total_rows")
    return f"SQL {'/'.join(counts) or '—'} · 결과 {total if total is not None else '—'}"


def _latency_note(run: Mapping[str, Any]) -> str:
    """§9 지연 꼬리 문구 — run 메타의 두 평면을 보고 고른다(plans/139 W6-b).

    두 평면이 모두 mlx 일 때만 「로컬 MLX 값」이라 쓴다. 하나만 mlx 면 그 사실을 적고, 아니면
    평면 이름을 밝힌 중립 문구다.
    """
    planes = run.get("planes") or {}
    worker = str(planes.get("worker") or "").strip().lower()
    orchestrator = str(planes.get("orchestrator") or "").strip().lower()
    if worker == orchestrator == "mlx":
        return "로컬 MLX 값이라 성능 결론을 내지 않는다(D-240)."
    if "mlx" in (worker, orchestrator):
        return (
            f"평면 워커 `{worker or '—'}` / 오케스트레이터 `{orchestrator or '—'}` — 로컬 MLX 가 "
            "섞인 값이라 성능 결론을 내지 않는다(D-240)."
        )
    return f"평면 워커 `{worker or '—'}` / 오케스트레이터 `{orchestrator or '—'}`에서 잰 값(참고)."


def _p1_warnings(catalog: Mapping[str, Any]) -> list[str]:
    """첫머리 경고 — P1 근거 없음(폴백 사유) · 스냅샷 해시 불일치(plans/140 W2-7 · 고정 문구)."""
    lines = []
    if catalog.get("p1_fallback"):
        lines.append(
            f"> **P1 근거 없음 — 자산 없는 기준선** (사유: {catalog['p1_fallback']} · "
            f"스키마 입력 `{catalog.get('source')}`). 내부망에서 「DB 구조」 탭 자산 자동 생성 "
            "1단계(P1)를 먼저 돌린 뒤 다시 실행하면 근거가 실린다(D-311 ①)."
        )
    lines += [f"> 주의 — {warning}" for warning in catalog.get("p1_warnings") or []]
    return lines + [""] if lines else []


def _p1_section(catalog: Mapping[str, Any], code_samples: Mapping[str, Any] | None) -> list[str]:
    """P1 근거 요약 · 주석 열거 보유 컬럼 수 · 치환 코드값 요약(수만)."""
    from .code_samples import summary_line

    summary = catalog.get("summary") or {}
    p1 = catalog.get("p1")
    if not p1 and "comment_enum_columns" not in summary:
        return []
    rows: list[list[Any]] = []
    if p1:
        budget = p1.get("budget") or {}
        p1_rel = [
            r
            for t in (catalog.get("tables") or {}).values()
            for r in t.get("relations") or []
            if r.get("kind") == "p1"
        ]
        rows += [
            ["초안", f"{p1.get('draft_id')} · {p1.get('status')} · {p1.get('created_at')}"],
            ["엔진 · DB 미연결", f"{p1.get('engine')} · {'예' if p1.get('offline') else '아니오'}"],
            ["프로파일 컬럼", summary.get("p1_profiled_columns", 0)],
            ["코드 컬럼", summary.get("p1_code_columns", 0)],
            ["관계 후보 · 채택", f"{len(p1_rel)} · {sum(1 for r in p1_rel if r.get('accepted'))}"],
            [
                "조회 예산(사용/한도 · 요청 · 상한 · 생략)",
                f"{budget.get('used')}/{budget.get('limit')} · {budget.get('requested')} · "
                f"{budget.get('cap')} · {budget.get('skipped')}",
            ],
        ]
    rows.append(["주석 코드 열거 보유 컬럼", summary.get("comment_enum_columns", 0)])
    if p1:
        rows.append(["치환 코드값(code_samples.yaml)", summary_line(code_samples)])
    lines = ["## 0. P1 근거(「DB 구조」 탭 결과 읽기 · 값 0)", ""]
    lines += _table(["항목", "값"], rows)
    if summary.get("comment_enum_columns"):
        lines += [
            "",
            "> 주석에 코드 열거가 있는 컬럼은 원 코드가 주석(DB 정의)으로 반출된다 — 같은 컬럼의 "
            "치환값과 어긋난다(D-311 주의 ③).",
        ]
    return lines + [""]


def render_report(
    run: Mapping[str, Any],
    catalog: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    *,
    code_samples: Mapping[str, Any] | None = None,
) -> str:
    """`report.md` 본문. `code_samples`는 치환 코드값 파일 본문(요약 수만 옮긴다)."""
    s = summarize(records)
    lines = [f"# ITAM 질의 벤치 리포트 — {run.get('run_id')}", ""]
    lines += _p1_warnings(catalog)
    lines += [
        f"- 환경 `{run.get('env')}` · 프로파일 `{run.get('profile')}` · "
        f"확정 단 `{run.get('tier')}` · "
        f"평면 워커 `{(run.get('planes') or {}).get('worker')}` / 오케스트레이터 "
        f"`{(run.get('planes') or {}).get('orchestrator')}`",
        f"- 로그인 `{run.get('login_user')}` · 실행자 `{run.get('operator')}` · 커밋 "
        f"`{(run.get('git') or {}).get('sha')}`"
        f"{' (dirty)' if (run.get('git') or {}).get('dirty') else ''}",
        f"- 시나리오 {run.get('scenarios')}건 × 반복 {run.get('repeat')} · 턴 {s['turns']} · "
        f"SQL 관측 턴 {run.get('sql_observed_turns')}",
        f"- 스키마 입력 `{catalog.get('source')}` · "
        f"자산 지문 `{json.dumps(catalog.get('assets'), ensure_ascii=False)}`",
        "",
    ]
    if not run.get("judged", True):
        lines += [
            "> **판정하지 않음** — SQL 관측 턴이 0이다(D-219 ③). 감사 로그 수집 경로부터 확인한다.",
            "",
        ]
    if run.get("canary_in_results"):
        lines += [
            "> 누출 관문 시험 성립 — 사람 정보 카나리아가 "
            f"결과 원문에 {run['canary_in_results']}건 "
            "나타났고 산출물 기록 전 관문을 통과했다(`leak_check.json`).",
            "",
        ]
    elif run.get("policy_scope") == "closed":
        # closed 정책은 카나리아를 두지 않는다 — 시험 성립 여부를 따질 대상이 없다
        # (관문 판정은 그대로)
        lines += [
            "> 누출 관문 카나리아 시험 — **해당 없음**(closed 정책은 카나리아를 두지 않는다 · "
            "관문 판정은 그대로 했다 — `leak_check.json`).",
            "",
        ]
    elif run:
        lines += [
            "> 누출 관문 시험 **불성립** — 이번 run 결과 원문에 카나리아가 나타나지 않았다"
            "(담당자 질문이 사람 열을 조회하지 않았을 수 있다). "
            "관문 통과가 위생의 증거가 되지 못한다.",
            "",
        ]
    lines += _p1_section(catalog, code_samples)
    lines += ["## 1. 점수", ""]
    lines += _table(
        ["항목", "값"],
        [
            ["오라클 통과", _pct(s["pass"], s["oracle_turns"])],
            ["오라클 불합격 · 보류", f"{s['fail']} · {s['hold']}"],
            ["소스 선별(조회 DB 에 itam)", _pct(s["routing_ok"], s["routing_total"])],
            ["되물음 턴", s["asked_back"]],
            ["실행 성공(SQL 건)", _pct(s["sql_ok"], s["sql_total"])],
            ["SQL 없이 끝난 턴", s["no_sql_turns"]],
            ["재시도 평균", f"{statistics.mean(s['retries']):.2f}" if s["retries"] else "—"],
            [
                "테이블 정밀도 · 재현율",
                "—"
                if s["table_precision"] is None
                else f"{s['table_precision']:.2f} · {s['table_recall']:.2f}",
            ],
            ["핵심 컬럼 적중", _pct(s["key_hits"], s["key_refs"])],
        ],
    )
    lines += ["", "### 범주별", ""]
    lines += _table(["범주", "오라클 턴", "통과", "불합격", "보류"], _by_category(records))
    lines += [
        "",
        "## 2. 실패 분류와 고칠 곳",
        "",
        "소스 선별·되물음·권한은 ITAM 프롬프트 수치와 분리해 본다(§3.3).",
        "",
    ]
    lines += _table(
        ["분류", "턴(전체)", "첫 원인", "분리 집계", "고칠 곳"],
        [
            [
                label,
                s["labels"].get(label, 0),
                s["first_labels"].get(label, 0),
                "예" if label in SEPARATE else "",
                fix,
            ]
            for label, fix in TAXONOMY.items()
            if s["labels"].get(label)
        ]
        or [["(없음)", 0, 0, "", ""]],
    )
    lines += ["", "## 3. 스키마 맥락", ""]
    lines += _table(
        ["항목", "값"],
        [
            ["스키마 맥락을 잰 턴", _pct(s["context_turns"], s["turns"])],
            ["정답 테이블 제시율", _pct(s["gold_presented"], s["gold_turns"])],
            ["핵심 컬럼 의미 보유율", _pct(s["key_meaning"], s["key_presented"])],
            ["실 데이터 표본이 LLM 에 제시된 턴", s["sample_rows_turns"]],
        ],
    )
    if s["context_turns"] < s["turns"]:
        lines += [
            "",
            f"> 스키마 맥락이 없는 턴 {s['turns'] - s['context_turns']}개 — `schema_miss`·"
            "`meaning_absent`는 그 턴에서 판정하지 않았다(3단 기본 경로·조회 전 종료 · §2.3 M6).",
        ]
    lines += ["", "## 4. 방언 함정 검출", ""]
    lines += _table(["함정", "턴"], sorted(s["hazards"].items()) or [["(없음)", 0]])
    determinism = _determinism(records)
    if determinism:
        lines += ["", "## 5. 결정성(반복 실행)", ""]
        lines += _table(["시나리오", "턴", "반복", "서로 다른 SQL"], determinism)
    lines += ["", "## 6. 시나리오별", ""]
    lines += _table(
        ["시나리오", "턴", "반복", "프롬프트", "상태", "판정", "분류", "테이블", "함정"],
        [
            [
                r["id"],
                r["turn"],
                r["repeat"],
                r.get("prompt") or f"(응답 {', '.join(r.get('send_keys') or [])})",
                r.get("status"),
                (
                    f"{r['oracle']['verdict']}"
                    + (f" ({r['oracle']['mode']})" if r["oracle"].get("mode") != "as_is" else "")
                )
                if r.get("oracle")
                else "관측",
                r.get("taxonomy") or "",
                (r.get("sql_analysis") or {}).get("tables") or "",
                (r.get("sql_analysis") or {}).get("dialect_hazards") or "",
            ]
            for r in records
        ],
    )
    observes = [r for r in records if r.get("observe")]
    if observes:
        lines += ["", "## 7. 관측(observe) 턴", ""]
        lines += [
            "관찰 턴에서도 시스템이 조회한 테이블·컬럼·행 수가 남는다 — "
            "반출 뒤 서비스↔서버 연결 위치를"
            " 역추적하는 재료다(가린 SQL 원문은 `trace.jsonl` `executed_sqls`).",
            "",
        ]
        lines += _table(
            ["시나리오", "턴", "보는 것", "되물음", "지어냄", "테이블", "컬럼", "행 수"],
            [
                [
                    r["id"],
                    r["turn"],
                    r["observe"]["what"],
                    "예" if r["observe"]["asked_back"] else "",
                    "예" if r["observe"]["fabricated"] else "",
                    (r.get("sql_analysis") or {}).get("tables") or "",
                    (r.get("sql_analysis") or {}).get("columns") or "",
                    _row_counts(r),
                ]
                for r in observes
            ],
        )
    columns = [c for r in records for c in (r.get("result") or {}).get("columns") or []]
    unknown = sorted({name for c in columns for name in c.get("unknown_identifiers") or []})
    unresolved = sum(1 for c in columns if c.get("log_policy") == "unclassified")
    lines += [
        "",
        "## 8. 분류 필요 컬럼",
        "",
        "결과 열의 식에서 찾은 **정책 파일에 없는 원 컬럼**이다 — 정책 보강 대상"
        "(값은 남기지 않았다).",
        "",
    ]
    lines += [f"- `{name}`" for name in unknown] or ["- (없음)"]
    lines += [
        "",
        f"미분류로 값을 남기지 않은 결과 열: {unresolved}개(열 이름은 근거가 있을 때만 남긴다).",
    ]
    lines += ["", "## 9. 지연(참고)", ""]
    if s["latency"]:
        lines += [
            f"- 중앙값 {statistics.median(s['latency']) / 1000:.1f}s · "
            f"최대 {max(s['latency']) / 1000:.1f}s "
            f"— {_latency_note(run)}"
        ]
    else:
        lines += ["- (없음)"]
    return "\n".join(lines) + "\n"


# --- --compare (W6) ----------------------------------------------------------------


def _load_run(directory: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    run_path, trace_path = directory / "run.json", directory / "trace.jsonl"
    if not run_path.is_file() or not trace_path.is_file():
        raise FileNotFoundError(
            f"run 산출물이 없다(누출 관문 실패 run 은 비교할 수 없다) — {directory}"
        )
    run = json.loads(run_path.read_text(encoding="utf-8"))
    records = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return run, records


def _turn_outcomes(records: Sequence[Mapping[str, Any]]) -> dict[tuple[str, int], str]:
    """(시나리오, 턴) → 반복을 합친 결과.

    전부 통과 = pass · 하나라도 불합격 = fail · 그 밖 = hold/observe.
    """
    grouped: dict[tuple[str, int], list[str]] = defaultdict(list)
    for r in records:
        verdict = (r.get("oracle") or {}).get("verdict") if r.get("oracle") else "observe"
        grouped[(str(r["id"]), int(r["turn"]))].append(str(verdict))
    out = {}
    for key, verdicts in grouped.items():
        if all(v == "pass" for v in verdicts):
            out[key] = "pass"
        elif "fail" in verdicts:
            out[key] = "fail"
        else:
            out[key] = verdicts[0]
    return out


def compare_runs(dir_a: Path, dir_b: Path) -> str:
    """두 run 의 시나리오별 전이(통과↔실패) · 분류 증감 · 자산 지문 차이(LLM·DB 0)."""
    run_a, records_a = _load_run(Path(dir_a))
    run_b, records_b = _load_run(Path(dir_b))
    outcomes_a, outcomes_b = _turn_outcomes(records_a), _turn_outcomes(records_b)
    lines = [f"# 비교 — {run_a.get('run_id')} → {run_b.get('run_id')}", ""]
    if run_a.get("assets") == run_b.get("assets"):
        lines += [
            "> **프롬프트 자산 변화 없음** — 두 run 의 ITAM 자산 지문이 같다. 차이는 모델 비결정성·"
            "코드 변경 때문이다.",
            "",
        ]
    else:
        changed = sorted(
            k
            for k in set(run_a.get("assets") or {}) | set(run_b.get("assets") or {})
            if (run_a.get("assets") or {}).get(k) != (run_b.get("assets") or {}).get(k)
        )
        lines += [f"- 바뀐 자산: {', '.join(f'`{k}`' for k in changed)}", ""]
    ablation_a, ablation_b = run_a.get("asset_ablation"), run_b.get("asset_ablation")
    if ablation_a != ablation_b:
        lines += [
            f"- 끈 자산(run 단위 · plans/141 W8): `{ablation_a or '없음'}` → "
            f"`{ablation_b or '없음'}` — 자산 지문은 파일 기준이라 끈 자산을 반영하지 않는다",
            "",
        ]
    if (run_a.get("tier"), run_a.get("env")) != (run_b.get("tier"), run_b.get("env")):
        lines += [
            f"> 주의 — 확정 단·환경이 다르다({run_a.get('tier')}/{run_a.get('env')} → "
            f"{run_b.get('tier')}/{run_b.get('env')}). 같은 조건 비교가 아니다.",
            "",
        ]
    transitions: Counter[tuple[str, str]] = Counter()
    rows = []
    for key in sorted(set(outcomes_a) | set(outcomes_b)):
        before, after = outcomes_a.get(key, "—"), outcomes_b.get(key, "—")
        transitions[(before, after)] += 1
        if before != after:
            rows.append([key[0], key[1], before, after])
    lines += ["## 전이", ""]
    lines += _table(["전", "후", "턴"], [[a, b, n] for (a, b), n in sorted(transitions.items())])
    lines += ["", "### 바뀐 턴", ""]
    lines += _table(["시나리오", "턴", "전", "후"], rows) if rows else ["- (없음)"]
    labels_a = summarize(records_a)["labels"]
    labels_b = summarize(records_b)["labels"]
    lines += ["", "## 분류 증감", ""]
    lines += (
        _table(
            ["분류", "전", "후", "증감"],
            [
                [
                    label,
                    labels_a.get(label, 0),
                    labels_b.get(label, 0),
                    labels_b.get(label, 0) - labels_a.get(label, 0),
                ]
                for label in TAXONOMY
                if labels_a.get(label) or labels_b.get(label)
            ],
        )
        or []
    )
    # 한쪽만 자산을 껐으면 그 자산의 유지 판정을 덧붙인다(plans/141 §4.7)
    if bool(ablation_a) != bool(ablation_b):
        if ablation_b:
            lines += ["", *ablation_section(run_a, records_a, [(run_b, records_b)])]
        else:
            lines += ["", *ablation_section(run_b, records_b, [(run_a, records_a)])]
    return "\n".join(lines) + "\n"


# --- 자산별 켜고 끄기 (plans/141 W8 · §4.7) -----------------------------------------

KEEP = "유지"
WITHDRAW = "철회 후보"
UNDECIDED = "판정 불가"


def sql_turn_accuracy(records: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    """SQL 관측 턴 정답률 재료 → ``(통과, 분모)``. 분모 = 오라클 턴 중 실행 SQL이 잡힌 턴
    (권한 거부 제외)."""
    observed = [r for r in _oracle_records(records) if r.get("executed_sqls")]
    passed = sum(1 for r in observed if (r.get("oracle") or {}).get("verdict") == "pass")
    return passed, len(observed)


def failure_counts(records: Sequence[Mapping[str, Any]]) -> Counter[str]:
    """실패 분류 턴 수 — 프롬프트 밖 분리 집계(`SEPARATE`)는 뺀다."""
    return Counter(
        label for r in records for label in r.get("taxonomy") or [] if label not in SEPARATE
    )


def top_failures(counts: Counter[str], n: int = 2) -> list[str]:
    """상위 n종 — 턴 수 내림차순 · 같으면 이름순(결정적)."""
    return [k for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def ablation_verdict(
    on: Sequence[Mapping[str, Any]], off: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """유지 규칙 — 켠 쪽(`on`)이 SQL 관측 턴 정답률을 낮추지 않고, 끈 쪽(`off`) 실패 분류 상위 2종
    중 하나를 줄일 때만 `유지`. 아니면 `철회 후보`. 어느 쪽이든 분모가 0이면 `판정 불가`."""
    on_pass, on_total = sql_turn_accuracy(on)
    off_pass, off_total = sql_turn_accuracy(off)
    on_fail, off_fail = failure_counts(on), failure_counts(off)
    top = top_failures(off_fail)
    reduced = [label for label in top if on_fail.get(label, 0) < off_fail.get(label, 0)]
    if not on_total or not off_total:
        verdict = UNDECIDED
    elif on_pass * off_total >= off_pass * on_total and reduced:
        verdict = KEEP
    else:
        verdict = WITHDRAW
    return {
        "on": (on_pass, on_total),
        "off": (off_pass, off_total),
        "top": [(label, off_fail.get(label, 0), on_fail.get(label, 0)) for label in top],
        "reduced": reduced,
        "verdict": verdict,
    }


def _rate(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def ablation_section(
    base_run: Mapping[str, Any],
    base_records: Sequence[Mapping[str, Any]],
    ablations: Sequence[tuple[Mapping[str, Any], Sequence[Mapping[str, Any]]]],
) -> list[str]:
    """기준 run(전 자산 켬) ↔ 자산별 끈 run → 「자산별 효과」 절 줄 목록(LLM·DB 0)."""
    lines: list[str] = []
    rows = []
    notes = []
    for run, records in ablations:
        result = ablation_verdict(base_records, records)
        on_rate, off_rate = _rate(*result["on"]), _rate(*result["off"])
        diff = (
            "—" if on_rate is None or off_rate is None else f"{(on_rate - off_rate) * 100:+.0f}%p"
        )
        rows.append(
            [
                run.get("asset_ablation") or "(없음)",
                run.get("run_id"),
                _pct(*result["on"]),
                _pct(*result["off"]),
                diff,
                [f"{label} {off}→{on}" for label, off, on in result["top"]] or "(없음)",
                result["verdict"],
            ]
        )
        same = all(
            run.get(k) == base_run.get(k) for k in ("env", "tier", "scenario_file", "repeat")
        )
        if not same:
            notes.append(str(run.get("run_id")))
    lines += ["## 자산별 효과", ""]
    lines += _table(
        ["끈 자산", "run", "정답률(켬)", "정답률(끔)", "차이", "상위 실패 2종(끔→켬)", "판정"],
        rows or [["(없음)", "", "", "", "", "", ""]],
    )
    lines += [
        "",
        "유지 규칙(plans/141 §4.7): 켠 쪽이 SQL 관측 턴 정답률을 낮추지 않고, 끈 쪽 실패 분류 "
        "상위 2종(소스 선별·되물음·권한 제외) 중 하나를 줄일 때만 `유지`. 아니면 `철회 후보` — "
        "원천 항목 `withdrawn` → 빌더 재실행 → 커밋.",
    ]
    if notes:
        lines += [
            "",
            f"> 주의 — 환경·확정 단·시나리오·반복이 기준과 다른 run: {', '.join(notes)}. "
            "같은 조건 비교가 아니다.",
        ]
    return lines


def render_ablation(
    base_run: Mapping[str, Any],
    base_records: Sequence[Mapping[str, Any]],
    ablations: Sequence[tuple[Mapping[str, Any], Sequence[Mapping[str, Any]]]],
) -> str:
    """`--ablation-report` 본문 — 제목 + 「자산별 효과」 절."""
    lines = [f"# 자산별 효과 — 기준 {base_run.get('run_id')}", ""]
    return "\n".join(lines + ablation_section(base_run, base_records, ablations)) + "\n"


def ablation_report(base_dir: Path, ablation_dirs: Sequence[Path]) -> str:
    """`--ablation-report` — 기준 run 디렉터리와 자산별 끈 run 디렉터리들을 읽어 표를 낸다.

    Raises:
        FileNotFoundError: 산출물 없음
        ValueError: 기준 run이 자산을 껐거나 비교 run이 자산을 끄지 않음
    """
    base_run, base_records = _load_run(Path(base_dir))
    if base_run.get("asset_ablation"):
        raise ValueError(f"기준 run 이 자산을 껐다({base_run['asset_ablation']}) — {base_dir}")
    loaded = []
    for directory in ablation_dirs:
        run, records = _load_run(Path(directory))
        if not run.get("asset_ablation"):
            raise ValueError(f"자산을 끈 run 이 아니다(asset_ablation 없음) — {directory}")
        loaded.append((run, records))
    return render_ablation(base_run, base_records, loaded)


# --- --verify-assets (plans/141 W5) ------------------------------------------------


def render_verification_report(run: Mapping[str, Any], document: Mapping[str, Any]) -> str:
    """검증 모드 `report.md` — 항목 ID·종류·결과 범주·행 수 구간만(값·SQL 원문 없음)."""
    summary = document.get("summary") or {}
    lines = [
        f"# ITAM 지식 자산 검증 — {run.get('run_id')}",
        "",
        f"- 환경 `{run.get('env')}` · DB 백엔드 `{run.get('db_backend')}` · 커밋 "
        f"`{(run.get('git') or {}).get('sha')}`"
        f"{' (dirty)' if (run.get('git') or {}).get('dirty') else ''}",
        f"- 원천 지문 `{json.dumps(document.get('sources'), ensure_ascii=False)}`",
        f"- 반출 파일: {', '.join(f'`{f}`' for f in run.get('files') or [])}",
        "",
        "## 요약",
        "",
    ]
    lines += _table(
        ["항목", "값"],
        [
            ["전체", summary.get("total", 0)],
            ["성공", summary.get("ok", 0)],
            ["오류", summary.get("error", 0)],
            ["보류(code 슬롯 대표값 없음 등)", summary.get("pending", 0)],
            ["오류 범주", [f"{k} {v}" for k, v in (summary.get("by_error") or {}).items()] or "—"],
        ],
    )
    lines += ["", "## 항목", ""]
    lines += _table(
        ["ID", "종류", "결과", "오류 범주", "행 수 구간"],
        [
            [
                item.get("id"),
                item.get("kind"),
                "보류" if item.get("ok") is None else ("성공" if item.get("ok") else "오류"),
                item.get("error") or "",
                item.get("rows") or "",
            ]
            for item in document.get("items") or []
        ]
        or [["(없음)", "", "", "", ""]],
    )
    lines += [
        "",
        "오류 항목은 원천 파일에서 `withdrawn`(또는 삭제)하고 다음 사이클 빌더로 걷어낸다. "
        "행 수 구간 `0`은 조건이 맞는 행이 없다는 뜻이다 — 조건·날짜 경계를 확인한다.",
    ]
    return "\n".join(lines) + "\n"


# --- --sync (W8 · 반출 후 싱크 보조) ------------------------------------------------

#: 서비스↔서버 연결 후보를 찾을 때 보는 낱말(의미 문구·이름 토큰) — 후보일 뿐 사람이 확인한다(G-7).
_SERVICE_WORDS = ("서비스", "업무", "시스템명", "응용", "애플리케이션", "어플리케이션")
_SERVICE_TOKENS = frozenset({"svc", "service", "biz", "app", "appl", "sys", "system", "srvc"})


def sync_report(run_dir: Path, *, transcript_path: Path, policy: Any) -> str:
    """반출 run 의 카탈로그 ↔ 로컬 전사본·컬럼 정책 차이(마크다운 · 파일을 쓰지 않는다).

    ① 전사본에 없는 테이블·컬럼(새로 알게 된 구조) ② 전사본에만 있는 컬럼(이름이 다르면 G-4 식별자
    확인) ③ 정책 밖 컬럼(정책 보강 대상 · 사람 정보 휴리스틱 제안 포함) ④ 서비스↔서버 연결 후보 컬럼
    (의미·이름 낱말 — 후보일 뿐) ⑤ 관찰 턴이 실제로 조회한 테이블·컬럼·행 수.
    """
    import yaml

    from .catalog import name_tokens

    catalog_path, trace_path = Path(run_dir) / "schema_catalog.yaml", Path(run_dir) / "trace.jsonl"
    if not catalog_path.is_file():
        raise FileNotFoundError(f"반출 카탈로그가 없다 — {catalog_path}")
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    transcript = yaml.safe_load(Path(transcript_path).read_text(encoding="utf-8")) or {}
    local = {
        name: {c["var"] for c in spec.get("columns") or []}
        for name, spec in (transcript.get("tables") or {}).items()
    }
    exported = {
        name: {c["name"]: c for c in table.get("columns") or []}
        for name, table in (catalog.get("tables") or {}).items()
    }
    known_policy = {c.casefold() for c in policy.column_names()}
    lines = [
        f"# 싱크 대조 — {Path(run_dir).name}",
        "",
        f"- 반출 카탈로그: 테이블 {len(exported)} · 컬럼 {sum(len(c) for c in exported.values())}"
        f" · 입력 `{catalog.get('source')}` · 로컬 전사본 테이블 {len(local)}",
        "",
    ]
    new_tables = sorted(set(exported) - set(local))
    lines += ["## 1. 전사본에 없는 테이블", ""]
    lines += [f"- `{t}` — 컬럼 {len(exported[t])}" for t in new_tables] or ["- (없음)"]
    lines += ["", "## 2. 공통 테이블의 컬럼 차이", ""]
    rows = []
    for table in sorted(set(exported) & set(local)):
        added = sorted(set(exported[table]) - local[table])
        missing = sorted(local[table] - set(exported[table]))
        if added or missing:
            rows.append([table, added or "—", missing or "—"])
    lines += _table(["테이블", "반출에만", "전사본에만"], rows) if rows else ["- (차이 없음)"]
    lines += ["", "## 3. 정책 밖 컬럼(값을 남기지 않은 컬럼)", ""]
    unclassified = [
        (t, c)
        for t, cols in sorted(exported.items())
        for c in sorted(cols)
        if c.casefold() not in known_policy
    ]
    by_table = Counter(t for t, _c in unclassified)
    lines += [f"- 합계 {len(unclassified)}개 · 테이블 {len(by_table)}개"]
    suggested = [
        f"`{t}.{c}`" for t, c in unclassified if exported[t][c].get("policy_suggestion") == "pii"
    ]
    lines += [f"- 사람 정보 휴리스틱 제안(pii로 올림): {', '.join(suggested) or '(없음)'}", ""]
    lines += ["## 4. 서비스↔서버 연결 후보(G-7 — 사람 확인)", ""]
    candidates = []
    for table, cols in sorted(exported.items()):
        for name, column in sorted(cols.items()):
            meaning = str(column.get("meaning") or "")
            if any(w in meaning for w in _SERVICE_WORDS) or _SERVICE_TOKENS & set(
                name_tokens(name)
            ):
                candidates.append([table, name, meaning or "—"])
    lines += _table(["테이블", "컬럼", "의미"], candidates) if candidates else ["- (후보 없음)"]
    lines += ["", "## 5. 관찰 턴이 조회한 곳", ""]
    observed = []
    if trace_path.is_file():
        for line in trace_path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line) if line.strip() else {}
            if record.get("observe"):
                observed.append(
                    [
                        record["id"],
                        record["turn"],
                        (record.get("sql_analysis") or {}).get("tables") or "—",
                        (record.get("sql_analysis") or {}).get("columns") or "—",
                        _row_counts(record),
                    ]
                )
    lines += (
        _table(["시나리오", "턴", "테이블", "컬럼", "행 수"], observed)
        if observed
        else ["- (관찰 턴 없음)"]
    )
    return "\n".join(lines) + "\n"
