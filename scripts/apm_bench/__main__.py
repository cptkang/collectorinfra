"""`python -m scripts.apm_bench` — 제니퍼 APM 조회 벤치 CLI (plans/152 §3 · D-323).

    --dry-run       시나리오 린트(R2) · 레지스트리 정합(D-053) · 분할 검사 (LLM·네트워크 0)
                    위반 0 이면 exit 0, 아니면 exit 1
    --coverage      표 A 경로 · 표 B 선택 인자 · 레지스트리 보기 · 보기 인자 커버리지 →
                    `<out>/coverage.md` (기본 results/apm_bench/coverage/) ·
                    미커버·분할 위반이 있으면 exit 1
    --run           U층 드라이버 → 판정 → (P층) → 치환 → 근거 묶음 → 관문 → 산출물 6종
                    (`trace.jsonl`·`run.json`·`probe.jsonl`·`coverage.md`·`report.md`·
                    `leak_check.json`) → `<out>`(기본 results/apm_bench/<run_id>/)
                    --env e1(목 Open API · 기본) | e2(내부망 게이트웨이 · 본체 LLM 설정)
                    --llm fake(가짜 LLM · 기본) | mlx(E1 전용 · 127.0.0.1 MLX — 두 평면 mlx ·
                    루프백 · MLX 점검 통과가 아니면 exit 2 · `--only`로 대표 턴만)
                    --mlx-url · --mlx-model · --probe(P층 — 빈 결과 판별 · 허용값 탐침)
                    --placeholders <파일>(E1 기본 testdata/apm_bench/placeholders.e1.yaml ·
                    E2 필수 · 저장소 밖 로컬 전용) · --only <id…> · --limit N ·
                    --debug-raw <dir>(E1 전용 원본)
                    · --view-server-version(E2 필수 · 5.6.2.18 미만이면 exit 2)
                    · --skip-heavy(무거운 등급 끄기 — 건너뜀 `heavy_off` · 첫 내부망 회차 선택지 §7)
                    · --term-policy <파일>(일반 단어 허용 목록 · 기본 testdata/apm_bench/
                    term_policy.yaml = 빈 목록 · 내부망 검토본은 저장소 밖 로컬 사본)
                    exit 1: run 무효(APM 관측 0) · 관문 실패(산출물 미기록) · 근거 필수 칸 누락
    --fewshot-check 예문 과적합(시나리오 원문 n-gram) · APM 절 크기 상한 — 위반 시 exit 1 ·
                    다음에 돌릴 게이트 명령 출력
    --compare A B   두 run 산출물 대조(holdout 턴 정확도 · 대상 ID · 악화 · 승격) → markdown
                    (`--out` 이 있으면 그 디렉터리에 compare.md)
    --replay DIR    dev 실패 턴(또는 --turns id#n … · holdout 지정은 경고)의 분해·재선택만 실 MLX 로
                    재현 — 두 평면 mlx · 루프백 · MLX 점검 통과가 아니면 exit 2 · 결과는 방향 확인용

기본 동작(인자 없음)은 `--dry-run` 이다 — 모르고 실행해도 LLM·네트워크를 부르지 않는다.
모드는 `MODES`(인자 이름 → 처리 함수)에 한 줄씩 더한다.
"""

from __future__ import annotations

import argparse
import getpass
import json
import socket
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from . import CAPABILITY_MAP_PATH, COVERAGE_DIR, REPO_ROOT, RESULTS_ROOT, SCENARIOS_PATH
from . import scenarios as sc


def say(text: str = "") -> None:
    print(text, flush=True)


def _load(args: argparse.Namespace) -> tuple[list[sc.Scenario], list[sc.Issue]] | None:
    """시나리오(구조 위반 시나리오 제외)와 구조 위반을 읽는다 — 파일 자체를 못 읽으면 None."""
    import yaml

    path = Path(args.scenarios)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        say(f"[중단] 시나리오 파일을 읽지 못했다: {path} — {exc}")
        return None
    return sc.parse_scenarios(data)


def collect_issues(
    scenarios: Sequence[sc.Scenario],
    structure: Sequence[sc.Issue],
    cap: sc.CapabilityMap,
    registry: sc.RegistryInfo,
) -> tuple[list[sc.Issue], sc.SplitSummary]:
    """구조 · covers · 린트 · 레지스트리 정합 · 분할 검사 결과를 모은다."""
    issues = list(structure)
    issues += sc.check_covers(scenarios, cap)
    issues += sc.lint_scenarios(scenarios, registry.arg_keys)
    issues += sc.check_registry(scenarios, registry)
    split = sc.check_split(scenarios, cap.path_ids)
    issues += split.issues
    return issues, split


def _report_issues(issues: Sequence[sc.Issue]) -> tuple[int, int]:
    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level != "error"]
    if errors:
        by_rule = dict(sorted(Counter(i.rule for i in errors).items()))
        say(f"위반 {len(errors)}건 (규칙별 {by_rule}):")
        for issue in errors:
            say(f"  - {issue.line()}")
    if warnings:
        say(f"경고 {len(warnings)}건:")
        for issue in warnings:
            say(f"  - {issue.line()}")
    return len(errors), len(warnings)


def cmd_dry_run(args: argparse.Namespace) -> int:
    loaded = _load(args)
    if loaded is None:
        return 1
    scenarios, structure = loaded
    cap = sc.parse_capability_map(Path(args.cap_map))
    registry = sc.load_registry_info()
    issues, split = collect_issues(scenarios, structure, cap, registry)
    turns = [t for s in scenarios for t in s.turns]
    say(
        f"시나리오   : {len(scenarios)}건 · 턴 {len(turns)} · dev {split.total - split.holdout} / "
        f"holdout {split.holdout} ({split.ratio:.1%})"
    )
    say(
        f"플래그     : heavy {sum(s.heavy for s in scenarios)} · was_touch "
        f"{sum(s.was_touch for s in scenarios)} (e2_skip "
        f"{sum(1 for s in scenarios if s.was_touch and s.e2_skip)}) · sensitive "
        f"{sum(s.sensitive for s in scenarios)}"
    )
    say(
        f"정답 유형  : 거절 {sum(1 for t in turns if t.expect.refusal)} · 되묻기 "
        f"{dict(sorted(Counter(t.expect.clarify for t in turns if t.expect.clarify).items()))} · "
        f"교차 처리기 {sum(1 for t in turns if set(t.expect.agents) - {'apm_query'})} · 멀티턴 "
        f"{sum(1 for s in scenarios if len(s.turns) > 1)}"
    )
    say(
        f"정합 원천  : 표 A {len(cap.paths)}경로 · 표 B 선택 인자 {len(cap.selected_args)} · "
        f"레지스트리 보기 {len(registry.views)} · 보기 인자 "
        f"{sum(len(v.args) for v in registry.views.values())}"
    )
    errors, _warnings = _report_issues(issues)
    say("결과       : " + ("통과" if errors == 0 else f"실패 — 위반 {errors}건"))
    return 0 if errors == 0 else 1


def cmd_coverage(args: argparse.Namespace) -> int:
    loaded = _load(args)
    if loaded is None:
        return 1
    scenarios, structure = loaded
    if structure:
        _report_issues(structure)
        say("[중단] 구조 위반이 있어 커버리지를 세지 않는다 — --dry-run 으로 먼저 고친다")
        return 1
    cap = sc.parse_capability_map(Path(args.cap_map))
    registry = sc.load_registry_info()
    report = sc.build_coverage(scenarios, cap, registry)
    out_dir = Path(args.out) if args.out else COVERAGE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "coverage.md"
    out.write_text(sc.render_coverage_md(report), encoding="utf-8")
    for name, items in report.groups():
        missing = [item.key for item in items if not item.covered]
        say(
            f"{name:<12}: {len(items) - len(missing)}/{len(items)}"
            + (f" · 미커버 {', '.join(missing)}" if missing else "")
        )
    say(f"표 B 제외   : {len(report.excluded_args)}행(사유는 coverage.md)")
    split_errors, _ = _report_issues(report.split.issues)
    say(f"산출물     : {out}")
    failed = report.uncovered > 0 or split_errors > 0
    say(
        "결과       : "
        + ("통과" if not failed else f"실패 — 미커버 {report.uncovered} · 분할 위반 {split_errors}")
    )
    return 1 if failed else 0


PLACEHOLDERS_E1 = SCENARIOS_PATH.parent / "placeholders.e1.yaml"
#: `--skip-heavy`로 돌지 않은 무거운 등급 시나리오의 건너뜀 사유(`verdict.skipped`).
HEAVY_OFF = "heavy_off"


def select_scenarios(
    scenarios: Sequence[sc.Scenario], only: Sequence[str] | None, limit: int | None
) -> tuple[list[sc.Scenario], list[str]]:
    """`--only`(입력 순서 유지) · `--limit` 적용 → (선택, 모르는 id)."""
    picked = list(scenarios)
    unknown: list[str] = []
    if only:
        known = {s.id for s in scenarios}
        unknown = [i for i in only if i not in known]
        wanted = set(only)
        picked = [s for s in picked if s.id in wanted]
    if limit is not None:
        picked = picked[: max(limit, 0)]
    return picked, unknown


#: 결론 금지 머리 표시(E1·MLX — D-323 주의 ① · D-240).
DIRECTIONAL_HEAD = "방향 확인용 — 결론 금지"
#: run 메타에서 산출물에 싣지 않는 칸(관문 원이름 대조 집합 · 프로세스 정보).
RUN_META_DROP: frozenset[str] = frozenset({"inventory_names", "gateway_pid"})


def _git(*argv: str) -> str:
    try:
        out = subprocess.run(["git", *argv], cwd=REPO_ROOT, capture_output=True, text=True,
                             timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def code_version() -> tuple[str, bool]:
    """(HEAD SHA · 작업 트리 변경 있음)."""
    return _git("rev-parse", "HEAD") or "unknown", bool(_git("status", "--porcelain"))


def make_run_id(env: str, sha: str, now: datetime | None = None) -> str:
    from .driver import KST

    stamp = (now or datetime.now(KST)).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{env}-{sha[:7]}"


def run_user_values() -> dict[str, str | None]:
    """누출 관문 사용자 정보 원값(로그인 계정 · OS 사용자 · 호스트 · 홈)."""
    from scripts.scenario.runner import DEFAULT_USER_ID

    return {"login_id": DEFAULT_USER_ID, "os_user": getpass.getuser(),
            "host": socket.gethostname(), "home": str(Path.home())}


def build_run_meta(meta: Mapping[str, Any], *, run_id: str, sha: str, dirty: bool,
                   versions: Mapping[str, Any], summary: Mapping[str, Any],
                   probe_rows: Sequence[Mapping[str, Any]], evidence_missing: int,
                   ) -> dict[str, Any]:
    """`run.json`(치환 전) — 드라이버 메타에서 원이름 집합을 빼고 판정 요약 수치를 더한다."""
    out = {"run_id": run_id, "sha": sha, "dirty": dirty}
    out.update({k: v for k, v in meta.items() if k not in RUN_META_DROP})
    out.update({
        "abort_reason": meta.get("aborted"),
        "budget": {k: (meta.get("guard") or {}).get(k)
                   for k in ("total_calls", "calls_by_source", "timeouts", "quota_hits")},
        "fingerprints": dict(versions.get("fingerprints") or {}),
        "apm_section": {k: v for k, v in (versions.get("apm_section") or {}).items()
                        if k != "view_rows"} | {"view_rows": len(
                            (versions.get("apm_section") or {}).get("view_rows") or [])},
        "counts": dict(summary.get("counts") or {}),
        "turn_accuracy": dict(summary.get("turn") or {}),
        "run_invalid": bool(summary.get("run_invalid")),
        "probe_rows": len(probe_rows),
        "evidence_missing": evidence_missing,
    })
    return out


def is_directional(meta: Mapping[str, Any]) -> bool:
    from .loop import directional_reasons

    return bool(directional_reasons(meta))


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _axis_lines(axes: Mapping[str, Mapping[str, Any]]) -> list[str]:
    lines = ["| 축 | 통과 | 실패 | na | 정확도 |", "|---|---:|---:|---:|---:|"]
    for axis, row in axes.items():
        lines.append(f"| {axis} | {row['pass']} | {row['fail']} | {row['na']} | "
                     f"{_pct(row['accuracy'])} |")
    return lines


def render_report(records: Sequence[Mapping[str, Any]], run_meta: Mapping[str, Any], *,
                  term_lines: Sequence[str], missing: Sequence[tuple[str, int, str]],
                  probe_rows: Sequence[Mapping[str, Any]]) -> str:
    """`report.md`(§3.10) — **치환된** 레코드와 run 메타로 만든다.

    holdout 실패는 건수만 싣는다(수정 대상으로 내놓지 않는다 · 권고 ③) — §3 few-shot 위치 · §4 코드
    교정 · §5 예문 복사 · §6 되묻기·거절 실패·예상 밖 목록 · §7 수정 후보는 dev 턴만 나열한다.
    """
    from .judge import summarize
    from .loop import directional_reasons, fix_candidates

    out = summarize(records)
    dev_out = summarize([r for r in records if r.get("split") == "dev"])
    hold_out = summarize([r for r in records if r.get("split") == "holdout"])

    def held_line(n: int) -> list[str]:
        return [f"- holdout {n}건(상세는 수정 대상이 아니라 싣지 않는다)"] if n else []
    reasons = directional_reasons(run_meta)
    lines = [f"# APM 조회 벤치 — {run_meta.get('run_id')}", ""]
    if reasons:
        lines += [f"> **{DIRECTIONAL_HEAD}** ({' · '.join(reasons)})", ""]
    lines += [
        f"- SHA `{run_meta.get('sha')}`" + (" (작업 트리 변경 있음)" if run_meta.get("dirty")
                                             else "")
        + f" · env {run_meta.get('env')} · LLM {run_meta.get('llm')} · 평면 "
        f"{run_meta.get('planes')} · 사다리 {run_meta.get('tier')}",
        f"- 뷰 서버 {run_meta.get('view_server_version')} · 소요 {run_meta.get('elapsed_sec')}초"
        f" · 중단 {run_meta.get('aborted') or '없음'} · 예산 {run_meta.get('budget')}",
        "",
        "## 0. 요약 — 턴 정확도(일반 턴)",
        f"- 전체 {_pct(out['turn']['accuracy'])} ({out['turn']['pass']}/{out['turn']['total']})",
    ]
    for split, row in out["by_split"].items():
        lines.append(f"- {split} {_pct(row['turn']['accuracy'])} "
                     f"({row['turn']['pass']}/{row['turn']['total']})")
    c = out["counts"]
    lines += [f"- 레코드 {c['records']} · 일반 {c['normal']} · 되묻기 정답 {c['clarify']} · "
              f"거절 정답 {c['refusal']} · 판정 불가 {c['unjudged']} · 건너뜀 {c['skipped']}",
              f"- run 무효(APM 관측 0): {'예' if out['run_invalid'] else '아니오'}",
              "", "## 1. 축별 정확도", "", "### 전체", "", *_axis_lines(out["axes"])]
    for split, row in out["by_split"].items():
        lines += ["", f"### {split}", "", *_axis_lines(row["axes"])]
    lines += ["", "## 2. 분류 상위", "", "| 분류 | 대표 | 전체 |", "|---|---:|---:|"]
    for cat, n in out["categories"].items():
        lines.append(f"| {cat} | {out['primary_categories'].get(cat, 0)} | {n} |")
    if not out["categories"]:
        lines.append("| 없음 | 0 | 0 |")
    lines += ["", "## 3. few-shot 위치별 실패 (dev 만)", ""]
    lines += ([f"- `{loc}` {n}" for loc, n in dev_out["fewshot_locus"].items()] or ["- 없음"])
    lines += held_line(sum(hold_out["fewshot_locus"].values()))
    lines += ["", "## 4. 코드 교정 목록 (few-shot 으로 못 고침)", ""]
    dev_ids = {f"{r.get('scenario_id')}#{r.get('turn')}" for r in records
               if r.get("split") == "dev"}
    code_dev = [f for f in out["code_fixes"] if f["turn"] in dev_ids]
    lines += ([f"- {f['turn']} · {f['category']} · {', '.join(f['locus'])}" for f in code_dev]
              or ["- 없음"])
    lines += held_line(len(out["code_fixes"]) - len(code_dev))
    lines += ["", "## 5. 예문 복사(`fewshot_copy` · dev 만)", ""]
    lines += ([f"- {cp['turn']} · `{cp['view']}` 예문 #{cp['example_index']} · 부착 "
               f"{cp.get('attached')}" for cp in dev_out["fewshot_copies"]] or ["- 없음"])
    lines += held_line(len(hold_out["fewshot_copies"]))
    lines += ["", "## 6. 되묻기·거절 (정답 유형 · 별도 집계 · 실패 목록은 dev 만)", ""]
    for name, key in (("되묻기", "clarify"), ("거절", "refusal")):
        side, failed = out[key], dev_out[key]["failed"]
        held = len(hold_out[key]["failed"])
        lines.append(f"- {name} {_pct(side['accuracy'])} ({side['pass']}/{side['total']})"
                     + (f" · 실패 {', '.join(failed)}" if failed else "")
                     + (f" · holdout 실패 {held}건" if held else ""))
    lines += ["", "### 예상 밖 되묻기·거절 (dev 만)", ""]
    lines += ([f"- {u['turn']} · {u['kind']}" for u in dev_out["unexpected"]] or ["- 없음"])
    lines += held_line(len(hold_out["unexpected"]))
    lines += ["", "## 7. few-shot 수정 후보 (dev 만)", ""]
    lines += ([f"- {f['id']} · {', '.join(f['categories'])} · "
               f"{', '.join(f['locus']) or '-'}" for f in fix_candidates(records)] or ["- 없음"])
    lines += ["", "## 8. 판정 불가 · 건너뜀", ""]
    lines += [f"- 판정 불가 {u['turn']} · {u['reason']}" for u in out["unjudged"]]
    skipped = Counter(str(s["reason"]).split(":", 1)[0] for s in out["skipped"])
    lines += [f"- 건너뜀 {dict(skipped) if skipped else '없음'}"]
    if probe_rows:
        lines += ["", "## 9. P층", ""]
        kinds = Counter(f"{r.get('kind')}:{r.get('status')}" for r in probe_rows)
        lines += [f"- {k} {n}" for k, n in sorted(kinds.items())]
    lines += ["", "## 10. 근거 묶음 필수 칸", ""]
    lines += ([f"- 누락 {sid}#{turn} · {path}" for sid, turn, path in missing]
              or ["- 누락 0"])
    lines += ["", "## 11. 위생 주의", "", *[f"- {t}" for t in term_lines]]
    return "\n".join(lines) + "\n"


def make_probe_fn(registry: sc.RegistryInfo, values: Mapping[str, str],
                  rows: list[dict[str, Any]]) -> Callable[..., None]:
    """P층 훅 — 빈 결과 판별 + 허용값 탐침(대상 = 자리표 `inst_1` · 없으면 탐침 생략)."""
    from . import probe as pb

    def probe_fn(call_tool: Any, budget_ok: Callable[[], bool], records: Any) -> None:
        rows.extend(pb.empty_result_probes(records, call_tool, budget_ok=budget_ok))
        if values.get("inst_1"):
            rows.extend(pb.allowed_value_probes(call_tool, registry,
                                                {"instance_name": values["inst_1"]},
                                                budget_ok=budget_ok))

    return probe_fn


def cmd_run(args: argparse.Namespace, *, judge_fn: Callable[..., object] | None = None,
            driver_run: Callable[..., Any] | None = None) -> int:
    """U층 드라이버 → 판정 → (P층) → 재판정 → 치환 → 근거 → 관문 → 산출물.

    Args:
        args: CLI 인자
        judge_fn: 드라이버 턴 판정 훅(없으면 run 뒤 `judge_records` 일괄)
        driver_run: `driver.run` 대역(테스트)
    """
    from . import driver
    from . import guard as gd
    from . import hygiene as hy
    from . import judge as jd
    from . import probe as pb
    from .fingerprints import apm_section_evidence, fewshot_fingerprints

    ok, why = gd.check_view_server_version(args.env, args.view_server_version)
    if not ok:
        say(f"[중단] {why}")
        return 2
    if args.env == "e2" and not args.placeholders:
        say("[중단] E2 는 --placeholders(저장소 밖 로컬 전용 자리표 파일)가 필수다")
        return 2
    if args.env == "e2" and args.debug_raw:
        say("[중단] --debug-raw 는 E1 전용이다(E2 원본은 디스크에 쓰지 않는다)")
        return 2
    if args.env == "e2" and args.llm != "fake":
        say("[중단] --llm mlx 는 E1 전용이다(E2 는 본체 LLM 설정 그대로)")
        return 2
    loaded = _load(args)
    if loaded is None:
        return 1
    scenarios, structure = loaded
    if structure:
        _report_issues(structure)
        say("[중단] 구조 위반이 있다 — --dry-run 으로 먼저 고친다")
        return 1
    picked, unknown = select_scenarios(scenarios, args.only, args.limit)
    if unknown:
        say(f"[중단] 모르는 시나리오 id: {', '.join(unknown)}")
        return 1
    if not picked:
        say("[중단] 선택된 시나리오가 없다")
        return 1
    import yaml

    try:  # 사전 점검 — 본체·게이트웨이를 띄운 뒤가 아니라 지금 실패한다
        placeholders = driver.load_placeholders(Path(args.placeholders or PLACEHOLDERS_E1))
        policy = (hy.load_term_policy(Path(args.term_policy)) if args.term_policy
                  else hy.load_term_policy())
    except (OSError, ValueError, yaml.YAMLError) as exc:
        say(f"[중단] 자리표·허용 목록 파일을 읽지 못했다 — {exc}")
        return 2
    heavy_off = [s for s in picked if s.heavy] if args.skip_heavy else []
    to_drive = [s for s in picked if s not in heavy_off]
    if not to_drive:
        say("[중단] 무거운 등급을 빼면 돌 시나리오가 없다(--skip-heavy)")
        return 1
    registry = sc.load_registry_info()
    probe_rows: list[dict[str, Any]] = []
    probe_fn = make_probe_fn(registry, placeholders.values, probe_rows) if args.probe else None
    try:
        records, meta = (driver_run or driver.run)(
            to_drive, env=args.env, placeholders=placeholders,
            out_dir=Path(args.debug_raw) if args.debug_raw else None,
            view_server_version=args.view_server_version, judge_fn=judge_fn,
            probe_fn=probe_fn, llm=args.llm, mlx_url=args.mlx_url, mlx_model=args.mlx_model,
            say=say)
    except driver.LLMBlockedError as exc:
        say(f"[중단] LLM 사전 점검 실패(--llm mlx 점검 · E2 과금 평면) — {exc}")
        return 2
    except (ValueError, RuntimeError) as exc:
        say(f"[중단] {exc}")
        return 1
    for scenario in heavy_off:
        records += driver._skipped(scenario, HEAVY_OFF, placeholders.values)
    meta["skip_heavy"] = bool(args.skip_heavy)
    if args.debug_raw:
        raw = Path(args.debug_raw)
        raw.mkdir(parents=True, exist_ok=True)
        with open(raw / "records.jsonl", "w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        (raw / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2,
                                                  default=str), encoding="utf-8")
        say(f"원본       : {raw} (E1 디버그 — 합성 이름뿐 · records.jsonl · meta.json · 로그)")

    # 판정 → P층 대조 → 재판정(빈 결과 판별이 `empty_result`를 바꾼다)
    jd.judge_records(picked, records, registry)
    if probe_rows:
        pb.attach(records, probe_rows)
        jd.judge_records(picked, records, registry)
    summary = jd.summarize(records)

    sha, dirty = code_version()
    run_id = make_run_id(args.env, sha)
    versions = {"code_sha": sha, "fingerprints": fewshot_fingerprints(registry),
                "apm_section": apm_section_evidence()}
    names = list(meta.get("inventory_names") or [])
    sanitizer = hy.RunSanitizer(term_policy=policy, canaries=placeholders.canaries)
    run_meta = build_run_meta(meta, run_id=run_id, sha=sha, dirty=dirty, versions=versions,
                              summary=summary, probe_rows=probe_rows, evidence_missing=0)
    clean = sanitizer.sanitize(records, inventory_names=names, probe_rows=probe_rows,
                               run_meta=run_meta, placeholders=placeholders.values)
    for record in clean.records:
        record["evidence"] = hy.build_evidence(record, versions=versions)
    missing = hy.check_evidence(clean.records)
    clean.run_meta["evidence_missing"] = len(missing)
    report_md = render_report(clean.records, clean.run_meta, term_lines=hy.term_risk_lines(policy),
                              missing=missing, probe_rows=clean.probe_rows)
    coverage_md = sc.render_coverage_md(
        sc.build_coverage(scenarios, sc.parse_capability_map(Path(args.cap_map)), registry),
        extra_sections=(jd.make_example_section(clean.records, registry),))
    staged = hy.stage_outputs(clean.records, clean.run_meta, clean.probe_rows, coverage_md,
                              report_md)
    gate = hy.make_gate(inventory_names=names, placeholders=placeholders.values,
                        user_values=run_user_values(), canaries=placeholders.canaries,
                        sanitizer=sanitizer)
    out_dir = Path(args.out) if args.out else RESULTS_ROOT / run_id
    gate_ok, violations = hy.write_outputs(out_dir, staged, gate)

    skipped = Counter(str(r["verdict"].get("skipped")).split(":", 1)[0]
                      for r in records if r["verdict"].get("skipped"))
    if is_directional(clean.run_meta):
        say(f"[{DIRECTIONAL_HEAD}] env {args.env} · 평면 {meta.get('planes')}")
    say(f"사다리     : {meta.get('tier')} (사유 {meta.get('degraded_reason')})")
    say(f"턴         : 실행 {meta.get('turns')} · APM 관측 {meta.get('apm_observed_turns')} · "
        f"건너뜀 {dict(skipped)}")
    guard_meta = meta.get("guard") or {}
    say(f"호출       : 소스별 {guard_meta.get('calls_by_source')} · 타임아웃 "
        f"{guard_meta.get('timeouts')} · 429 {guard_meta.get('quota_hits')} · 기준선 "
        f"{guard_meta.get('baseline_ms')}ms · 인벤토리 {meta.get('inventory')} · P층 "
        f"{meta.get('probe_calls', 0)}회")
    say(f"중단 사유  : {meta.get('aborted') or '없음'} · 소요 {meta.get('elapsed_sec')}초 · "
        f"뷰 서버 {meta.get('view_server_version')}")
    turn = summary["turn"]
    split_text = " · ".join(f"{k} {_pct(v['turn']['accuracy'])} "
                            f"({v['turn']['pass']}/{v['turn']['total']})"
                            for k, v in summary["by_split"].items())
    say(f"턴 정확도  : {_pct(turn['accuracy'])} ({turn['pass']}/{turn['total']}) · {split_text}")
    say("축         : " + " · ".join(f"{a} {_pct(r['accuracy'])}"
                                     for a, r in summary["axes"].items()))
    say(f"대표 분류  : {summary['primary_categories'] or '없음'}")
    say(f"근거 누락  : {len(missing)}" + (f" — {missing[:5]}" if missing else ""))
    if not meta.get("gateway_var_absent", True):
        say("[경고] apm_gateway/var 가 남았다 — 스풀 경로 주입을 확인한다")
    if gate_ok:
        say(f"산출물     : {out_dir} (관문 통과)")
    else:
        rules = Counter(str(v.get("rule")) for v in violations)
        say(f"[실패] 누출 관문 — 위반 {len(violations)}건 {dict(rules)} · 산출물 미기록"
            f"(위치만 {out_dir / 'leak_check.json'})")
    failed = []
    if summary["run_invalid"] or not meta.get("apm_observed_turns"):
        failed.append("run 무효 — APM 관측 턴 0(D-219 ③ · 관측 0은 측정 성립이 아니다)")
    if not gate_ok:
        failed.append("누출 관문 실패")
    if missing:
        failed.append(f"근거 필수 칸 누락 {len(missing)}")
    if failed:
        say("결과       : 실패 — " + " · ".join(failed))
        return 1
    say("결과       : 완료" + (f" — 중단({meta['aborted']})" if meta.get("aborted") else ""))
    return 0


def cmd_fewshot_check(args: argparse.Namespace) -> int:
    """예문 과적합 · APM 절 크기 — 위반이면 exit 1(다음 게이트 명령은 늘 출력)."""
    from .loop import fewshot_check, render_fewshot_check

    loaded = _load(args)
    if loaded is None:
        return 1
    scenarios, structure = loaded
    if structure:
        _report_issues(structure)
        say("[중단] 구조 위반이 있다 — --dry-run 으로 먼저 고친다")
        return 1
    violations = fewshot_check(scenarios, sc.load_registry_info())
    say(render_fewshot_check(violations).rstrip())
    say("결과       : " + ("통과" if not violations else f"실패 — 위반 {len(violations)}건"))
    return 1 if violations else 0


def cmd_compare(args: argparse.Namespace) -> int:
    """두 run 산출물 대조 → markdown(표준 출력 · `--out`이면 compare.md)."""
    from .loop import compare, load_log, load_run, render_compare

    run_a, run_b = (Path(p) for p in args.compare)
    for path in (run_a, run_b):
        if not (path / "run.json").exists() or not (path / "trace.jsonl").exists():
            say(f"[중단] run 산출물이 아니다(run.json·trace.jsonl 없음): {path}")
            return 1
    holdout = {f"{r.get('scenario_id')}#{r.get('turn')}" for path in (run_a, run_b)
               for r in load_run(path)["records"] if r.get("split") == "holdout"}
    try:
        log = load_log(holdout_ids=holdout)
    except ValueError as exc:
        say(f"[중단] 수정 이력 형식 위반 — {exc}")
        return 1
    text = render_compare(compare(run_a, run_b, log=log))
    say(text.rstrip())
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "compare.md").write_text(text, encoding="utf-8")
        say(f"산출물     : {out / 'compare.md'}")
    return 0


def render_replay(rows: Sequence[Mapping[str, Any]]) -> str:
    """`--replay` 결과 → markdown(머리에 방향 확인용 표시)."""
    from .loop import DIRECTIONAL_LABEL

    lines = ["# 분해·재선택 재현", "", f"> **{DIRECTIONAL_LABEL}** · {DIRECTIONAL_HEAD}", ""]
    if not rows:
        lines.append("- 재현할 턴 없음")
    for row in rows:
        before = row.get("before") or {}
        lines.append(f"## {row.get('id')} ({row.get('split')})")
        if row.get("error"):
            lines += [f"- 오류: {row['error']}", ""]
            continue
        after = row.get("after") or {}
        lines += [
            f"- 보기 축 {before.get('views')} → {after.get('views')} · "
            f"{before.get('observed_views')} → {after.get('observed_views')}",
            f"- 인자 축 {before.get('args')} → {after.get('args')} · "
            f"{before.get('observed_args')} → {after.get('observed_args')}",
            f"- 분류 {before.get('categories')} → {after.get('categories')} · 바뀐 축 "
            f"{', '.join(row.get('changed') or {}) or '없음'}",
            "",
        ]
    return "\n".join(lines) + "\n"


def cmd_replay(args: argparse.Namespace, *, llm_factory: Callable[[], Any] | None = None) -> int:
    """반출 run 의 dev 실패 턴(또는 `--turns` — holdout 이 섞이면 경고)을 실 MLX 로 재현 — 차단이면
    exit 2.

    Args:
        args: CLI 인자
        llm_factory: LLM 대역(테스트) · None 이면 `loop.make_mlx_llm`(두 평면 mlx · 루프백 ·
            MLX 점검 통과일 때만)
    """
    from .loop import ReplayBlockedError, load_run, make_mlx_llm, replay

    run_dir = Path(args.replay)
    if not (run_dir / "trace.jsonl").exists():
        say(f"[중단] run 산출물이 아니다(trace.jsonl 없음): {run_dir}")
        return 1
    try:
        llm = (llm_factory or make_mlx_llm)()
    except ReplayBlockedError as exc:
        say(f"[중단] 재현 차단 — {exc}")
        return 2
    records = load_run(run_dir)["records"]
    held = sorted(set(args.turns or ()) & {f"{r.get('scenario_id')}#{r.get('turn')}"
                                            for r in records if r.get("split") == "holdout"})
    if held:
        say(f"[경고] holdout 턴 지정 {len(held)}개 — 결과를 few-shot 수정 근거로 쓰지 않는다"
            "(권고 ③ · 실행은 한다)")
    rows = replay(records, llm=llm, registry=sc.load_registry_info(), turn_ids=args.turns)
    text = render_replay(rows)
    say(text.rstrip())
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "replay.md").write_text(text, encoding="utf-8")
        say(f"산출물     : {out / 'replay.md'}")
    return 0


#: 모드 인자 dest → 처리 함수(첫 번째가 기본 모드).
MODES: dict[str, Callable[[argparse.Namespace], int]] = {
    "dry_run": cmd_dry_run,
    "coverage": cmd_coverage,
    "run": cmd_run,
    "fewshot_check": cmd_fewshot_check,
    "compare": cmd_compare,
    "replay": cmd_replay,
}


def build_parser() -> argparse.ArgumentParser:
    from .driver import LLM_CHOICES, MLX_DEFAULT_MODEL, MLX_DEFAULT_URL

    parser = argparse.ArgumentParser(
        prog="python -m scripts.apm_bench",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="린트·정합·분할 검사(기본)")
    mode.add_argument("--coverage", action="store_true", help="커버리지 → coverage.md")
    mode.add_argument("--run", action="store_true", help="벤치 실행 → 산출물 6종")
    mode.add_argument("--fewshot-check", action="store_true", help="예문 과적합 · 크기 상한")
    mode.add_argument("--compare", nargs=2, default=None, metavar=("RUN_A", "RUN_B"),
                      help="두 run 산출물 대조(A = 수정 전 · B = 수정 후)")
    mode.add_argument("--replay", default=None, metavar="RUN_DIR",
                      help="실패 턴 분해·재선택 재현(실 MLX · 방향 확인용)")
    parser.add_argument("--scenarios", default=str(SCENARIOS_PATH), help="시나리오 YAML")
    parser.add_argument(
        "--cap-map", default=str(CAPABILITY_MAP_PATH), help="대응표(표 A·B) markdown"
    )
    parser.add_argument("--out", default=None,
                        help="출력 디렉터리(--coverage · --run(기본 results/apm_bench/<run_id>) · "
                             "--compare · --replay)")
    parser.add_argument("--env", choices=("e1", "e2"), default="e1", help="--run 환경")
    parser.add_argument("--llm", choices=LLM_CHOICES, default="fake",
                        help="--run E1 LLM(fake | mlx — mlx 는 --only 대표 턴과 함께)")
    parser.add_argument("--mlx-url", default=MLX_DEFAULT_URL, help="--llm mlx 서버 /v1 주소")
    parser.add_argument("--mlx-model", default=MLX_DEFAULT_MODEL, help="--llm mlx 모델 ID")
    parser.add_argument("--probe", action="store_true",
                        help="--run P층(빈 결과 판별 · 허용값 탐침)")
    parser.add_argument("--placeholders", default=None, help="--run 자리표 파일")
    parser.add_argument("--only", nargs="+", default=None, metavar="ID", help="--run 시나리오 id")
    parser.add_argument("--limit", type=int, default=None, help="--run 앞 N개 시나리오")
    parser.add_argument("--debug-raw", default=None, help="--run E1 원본 디렉터리")
    parser.add_argument("--view-server-version", default=None,
                        help="--run E2 뷰 서버 버전(운영자 입력 · 5.6.2.18 이상)")
    parser.add_argument("--skip-heavy", action="store_true",
                        help="--run 무거운 등급 시나리오를 돌지 않는다(건너뜀 heavy_off)")
    parser.add_argument("--term-policy", default=None,
                        help="--run 일반 단어 허용 목록(기본 testdata/apm_bench/term_policy.yaml)")
    parser.add_argument("--turns", nargs="+", default=None, metavar="ID#N",
                        help="--replay 재현할 턴(기본 dev 실패 턴 전부 · holdout 지정은 경고)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for dest, handler in MODES.items():
        if getattr(args, dest, None):
            return handler(args)
    return next(iter(MODES.values()))(args)


if __name__ == "__main__":
    sys.exit(main())
