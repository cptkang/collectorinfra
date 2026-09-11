"""벤치마크 단일 진입 (plans/93 §0.4 · 실행 가이드).

개발자가 외울 것은 두 줄이다.

    python -m scripts.bench                 # 설정 전수 점검
    python -m scripts.bench --sweep         # 성능 스위프까지 (후속 Wave)

축 선별·재개·통계 해석·결론 작성은 **전부 코드가 진다**. 개발자는 실행하고 결과 폴더를
전달한다. 그것이 역할의 전부다.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.bench import axes as axes_mod  # noqa: E402
from scripts.bench import catalog, compare, optimize, probe  # noqa: E402
from scripts.bench import report as report_mod, sweep as sweep_mod, validate  # noqa: E402


def say(message: str = "") -> None:
    """진행 로그. **즉시 내보낸다.**

    10~30시간짜리 스위프를 `> log.txt`로 넘기면 기본 블록 버퍼링 때문에 끝날 때까지
    파일이 비어 있다. 실행자가 살아 있는지 확인할 수 없으면 중단할지 판단할 수 없다.
    """
    print(message, flush=True)

_RESULTS_DIR = _ROOT / "results" / "bench"

#: 내부망 프로바이더 — 승인·옵트인 없이 실행한다(plans/93 §4.4 · 사용자 확정 2026-09-11).
_INTERNAL_PROVIDERS = frozenset({"fabrix", "ollama"})


def _provider_of(echo: probe.EchoResult) -> str:
    return str(echo.value_of("llm.provider") or "unknown")


def approval_policy(provider: str) -> tuple[bool, str]:
    """실 LLM 실행에 승인이 필요한가.

    내부망 면제는 **내부망에만** 적용된다 — 외부 프로바이더에서는 D-127이 그대로 산다.
    개발자가 구분할 필요는 없고 여기가 대신 판정한다.

    Returns:
        (승인 필요 여부, 사람이 읽을 사유)
    """
    if provider in _INTERNAL_PROVIDERS:
        return False, f"내부망 프로바이더({provider}) — 승인 없이 진행합니다"
    return True, f"외부 프로바이더({provider}) — 실 LLM 호출에는 승인이 필요합니다(D-127)"


def _run_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d")
    existing = sorted(_RESULTS_DIR.glob(f"{stamp}-*")) if _RESULTS_DIR.exists() else []
    return f"{stamp}-{len(existing) + 1:02d}"


def cmd_preflight(args: argparse.Namespace) -> int:
    """환경 사전점검 — 개발자 대신 진단한다."""
    say("[..]   config        읽는 중...")
    echo = probe.echo_config()
    if not echo.ok:
        say(f"[FAIL] config        {echo.error_type}: {(echo.error or '')[:200]}")
        say("→ 설정을 먼저 고쳐야 합니다. `.env`의 값 형식을 확인하세요.")
        return 1

    knobs = catalog.load_knobs()
    provider = _provider_of(echo)
    need_approval, reason = approval_policy(provider)

    say(f"[OK]   venv          python {sys.version.split()[0]}")
    say(f"[OK]   config        {len(knobs)} 필드 로드 · 에코 {len(echo.config)} 경로")
    say(f"[OK]   provider      {provider} — {reason}")

    nd = probe.detect_nondeterministic_keys()
    if nd:
        say(f"[INFO] 비결정 필드    {len(nd)}건 자동 제외 ({', '.join(sorted(nd)[:3])})")

    try:
        import ibm_db  # noqa: F401
        say("[OK]   DB2 드라이버   ibm-db 설치됨")
    except Exception:
        say("[WARN] DB2 드라이버   ibm-db 미설치 — 은행존(b0) 대상 항목은 건너뜁니다")

    free_gb = _free_gb(_ROOT)
    say(f"[OK]   디스크        여유 {free_gb:.0f}GB")

    unfinished = _unfinished_run()
    if unfinished:
        say(f"[INFO] 이전 실행     {unfinished.name} 미완 — 다음 실행에서 이어집니다")

    say("→ 실행 가능합니다.  python -m scripts.bench")
    return 0


def _free_gb(path: Path) -> float:
    import shutil
    return shutil.disk_usage(path).free / (1024 ** 3)


def _unfinished_run() -> Optional[Path]:
    if not _RESULTS_DIR.exists():
        return None
    for run in sorted(_RESULTS_DIR.iterdir(), reverse=True):
        if run.is_dir() and not (run / "SUMMARY.md").exists():
            return run
    return None


def cmd_show_env(args: argparse.Namespace) -> int:
    """지금 어떤 설정으로 도는지 — 값이 아니라 출처를 본다."""
    echo = probe.echo_config()
    if not echo.ok:
        say(f"설정을 읽지 못했습니다: {echo.error_type}: {echo.error}")
        return 1
    provider = _provider_of(echo)
    need, reason = approval_policy(provider)
    say(f"프로바이더   : {provider}")
    say(f"승인 정책    : {'필요' if need else '불요'} — {reason}")
    say(f"활성 DB      : {echo.value_of('multi_db.active_db_ids_csv')}")
    say(f"DB 백엔드    : {echo.value_of('db_backend')}")
    say(f"카탈로그     : {len(catalog.load_knobs())} 필드")
    say(f"민감 경로    : {len(probe.sensitive_config_paths())} 건(값은 해시로만 기록)")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """트랙 T — 전수 설정 검증. 기본 동작이다."""
    started = time.time()
    run_id = _run_id()
    out_dir = _RESULTS_DIR / run_id

    knobs = catalog.load_knobs()
    base_env = dict(os.environ)

    say(f"[1/5] L1 카탈로그 정합 — {len(knobs)}필드")
    integrity = catalog.check_integrity(knobs=knobs)
    say(f"      고아 {sum(1 for f in integrity if f.kind == 'orphan')} · "
          f"누락 {sum(1 for f in integrity if f.kind == 'missing_example')} · "
          f"설명부재 {sum(1 for f in integrity if f.kind == 'undocumented')}")

    targets = knobs if args.all_groups else [k for k in knobs if catalog.f1_exclusion_reason(k) is None]
    if args.limit:
        targets = targets[: args.limit]

    shadowed: list[validate.ShadowedKey] = []
    boot: list[validate.BootFinding] = []
    consumption: list[validate.ConsumptionFinding] = []
    unconsumed_cmp = None

    if args.quick:
        say("[2/5] L2·L4 건너뜀 (--quick)")
        say(f"[3/5] L3 주입 실효성 — {len(targets)}건")
        shadowed = validate.check_injection(targets, base_env=base_env)
        say(f"      가림 {len(shadowed)}건")
    else:
        nd = probe.detect_nondeterministic_keys()
        baseline = probe.echo_config(base_env=base_env)
        say(f"[2/5] L2 기동 안전성 — {len(targets)}건")
        boot = validate.check_boot(targets, exhaustive=args.exhaustive, base_env=base_env)
        say(f"      거부 {sum(1 for b in boot if not b.ok)}건")
        say(f"[3/5] L3 주입 실효성 — {len(targets)}건")
        shadowed = validate.check_injection(targets, base_env=base_env)
        say(f"      가림 {len(shadowed)}건")
        say(f"[4/5] L4 소비 실증 — {len(targets)}건")
        consumption = validate.check_consumption(
            targets, baseline=baseline, nondeterministic=nd, base_env=base_env)
        unconsumed_cmp = validate.compare_with_unconsumed(consumption)
        say(f"      변함 {sum(1 for c in consumption if c.verdict == 'changed')} · "
              f"불변 {sum(1 for c in consumption if c.verdict == 'unchanged')} · "
              f"목록 낡음 {len(unconsumed_cmp.list_stale)}")

    say("[5/5] 리포트 생성")
    ledger = report_mod.build_ledger(
        knobs, integrity=integrity, shadowed=shadowed, boot=boot,
        consumption=consumption,
        env_keys=frozenset(catalog.sc._parse_env_keys(_ROOT / ".env")),
        injection_checked=frozenset(k.env_key for k in targets),
    )
    health = report_mod.HealthReport(
        run_id=run_id, integrity=integrity, shadowed=shadowed, boot=boot,
        consumption=consumption, unconsumed_cmp=unconsumed_cmp, ledger=ledger,
        notes={"실행 모드": "--quick(L1·L3)" if args.quick else "L1~L4 전층",
               "대상": f"{len(targets)}건 / 전체 {len(knobs)}건"},
    )
    paths = report_mod.write_report(health, out_dir)

    elapsed = time.time() - started
    say()
    say(report_mod.render_summary(health))
    say(f"소요 {elapsed:.0f}초 · 산출 폴더: {paths['summary'].parent}")

    if args.ci:
        blocking = health.immediate_actions
        if blocking:
            say(f"\n[CI] 즉시 조치 {len(blocking)}건 — 실패로 처리합니다.")
            return 1
    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    """트랙 A~B — 축 스위프와 판정. 94의 실행 원자 위에 선다(§1.5)."""
    try:
        arms = sweep_mod.build_arms(limit=4 if args.scale == "smoke" else None)
    except Exception as exc:
        say(f"축 전개 실패: {type(exc).__name__}: {exc}")
        return 1

    say(f"축 스위프 — arm {len(arms)}개 (기준선 포함) · 규모 {args.scale} · 모드 {args.mode}")

    # 규모를 먼저 말한다. arm 1개 = 서버 기동 1회이므로 arm 수가 곧 시간이다(§4.4).
    # 승인용이 아니라 **운영 시간대를 피할 판단 재료**다 — 내부망은 승인 없이 진행한다.
    if args.mode != "dry":
        try:
            scenarios = len(sweep_mod.load_normal_catalog().scenarios)
        except Exception:
            scenarios = 0
        runs = len(arms) * scenarios * max(1, args.repeat)
        per_arm_min = 1.0 if args.mode == "mock" else 12.0
        say(f"  규모: 시나리오 {scenarios}건 × arm {len(arms)} × 반복 {args.repeat} = {runs}회 실행")
        say(f"  예상: 약 {len(arms) * per_arm_min / 60:.1f}시간 "
              f"(arm 1개 = 서버 기동 1회 · {args.mode} 기준)")
        if len(arms) > 30:
            say(f"  ※ arm이 {len(arms)}개입니다. 먼저 --scale smoke로 파이프라인을 확인하는 것을 권합니다.")

    if args.mode == "run":
        echo = probe.echo_config()
        provider = _provider_of(echo)
        need, reason = approval_policy(provider)
        say(f"  프로바이더: {reason}")
        if need and not args.yes:
            say("  → 외부 프로바이더입니다. 승인 없이 실 호출하지 않습니다(D-127).")
            say("     내부망에서 실행하거나, 승인을 받았다면 --yes를 붙이세요.")
            return 2

    if args.mode == "dry":
        for arm in arms:
            say(f"  {arm.arm_id:52s} {arm.env or '(기준선)'}")
        return 0

    try:
        result = sweep_mod.run_arms(arms, mode=args.mode, repeat=args.repeat)
    except sweep_mod.SweepUnavailable as exc:
        say(f"스위프를 돌릴 수 없습니다: {exc}")
        return 2

    out_dir = Path(result.get("out_dir", "")) if isinstance(result, dict) else None
    raw = (out_dir / "raw.jsonl") if out_dir else None
    if not raw or not raw.exists():
        say("원시 로그를 찾지 못했습니다. 94 러너 산출을 확인하세요.")
        return 1

    observations = sweep_mod.read_observations(raw)
    grouped = sweep_mod.group_by_arm(observations)
    baseline = grouped.get(sweep_mod.BASELINE_ARM, [])
    if not baseline:
        say("기준선 arm 결과가 없습니다 — 비교 기준이 없어 판정을 내지 않습니다.")
        return 1

    verdicts = []
    for arm in arms:
        if arm.arm_id == sweep_mod.BASELINE_ARM:
            continue
        verdicts.append(compare.judge(
            arm.arm_id, arm.axis, arm.level, baseline, grouped.get(arm.arm_id, [])))

    say()
    say(f"{'arm':52s} {'판정':10s} 문장")
    for v in verdicts:
        say(f"  {v.arm_id:50s} {v.verdict:10s} {v.sentence}")

    lines = ["# 축 스위프 판정", "", "| arm | 축 | 값 | 판정 | 근거 |", "|---|---|---|---|---|"]
    lines += [f"| `{v.arm_id}` | {v.axis or '—'} | {v.level or '—'} | **{v.verdict}** | {v.sentence} |"
              for v in verdicts]
    if out_dir:
        (out_dir / "axis_verdicts.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        say(f"\n산출: {out_dir}")
    return 0


def cmd_propose(args: argparse.Namespace) -> int:
    """트랙 C — 처분 제안서. **파일을 수정하지 않는다**(§6.1)."""
    knobs = catalog.load_knobs()
    base_env = dict(os.environ)
    say(f"[1/3] 검증 재료 수집 — {len(knobs)}필드")
    integrity = catalog.check_integrity(knobs=knobs)

    targets = [k for k in knobs if catalog.f1_exclusion_reason(k) is None]
    if args.limit:
        targets = targets[: args.limit]
    say(f"[2/3] 처분 결정 — 대상 {len(targets)}건(참조 수 집계 포함)")
    dispositions = optimize.build_from_validation(
        targets, integrity=integrity, shadowed=[], boot=[], consumption=[])

    deletions = [d for d in dispositions
                 if d.action in (optimize.PROPOSE_DELETE, optimize.PROPOSE_CONSTANT)]
    by_key = {k.env_key: k for k in targets}
    evidences = [optimize.collect_evidence(d.env_key, by_key[d.env_key]) for d in deletions]

    echo = probe.echo_config(base_env=base_env)
    pin = {}
    for d in dispositions:
        if d.action == optimize.CHANGE_DEFAULT:
            knob = by_key.get(d.env_key)
            if knob and knob.default is not None:
                pin[d.env_key] = knob.default

    say("[3/3] 제안서 생성")
    out_dir = _RESULTS_DIR / _run_id() / "proposals"
    paths = optimize.write_proposals(dispositions, evidences, pin, out_dir)
    from collections import Counter
    counts = Counter(d.action for d in dispositions)
    say()
    for action, n in counts.most_common():
        say(f"  {action:16s} {n:4d}건  (단계 {optimize.ROLLOUT.get(action, '—')})")
    say(f"\n산출: {paths['disposition'].parent}")
    say("→ 제안일 뿐입니다. 반영은 R0~R2부터 사람이 판단합니다(plans/93 §6.6).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.bench",
        description="설정 전수 검증·벤치마크 (plans/93)",
    )
    parser.add_argument("--preflight", action="store_true", help="환경 사전점검만")
    parser.add_argument("--show-env", action="store_true", help="현재 프로바이더·설정 출처")
    parser.add_argument("--quick", action="store_true", help="L1·L3만 (빠름)")
    parser.add_argument("--ci", action="store_true", help="즉시 조치가 있으면 exit 1")
    parser.add_argument("--exhaustive", action="store_true", help="L2에서 경계값 전수 시도")
    parser.add_argument("--all-groups", action="store_true",
                        help="F1 제외분까지 전부 대상(알람·크레덴셜 포함)")
    parser.add_argument("--limit", type=int, default=None, help="대상 상한(디버깅용)")
    parser.add_argument("--yes", action="store_true", help="승인 확인을 건너뛴다(사전 승인 시)")
    parser.add_argument("--sweep", action="store_true", help="성능 스위프 (94 하네스 위에서 실행)")
    parser.add_argument("--propose", action="store_true",
                        help="검증 결과로 노브 처분 제안서를 만든다(파일 수정 없음)")
    parser.add_argument("--scale", choices=("smoke", "full"), default="smoke",
                        help="스위프 규모 — smoke: 축 3~4개 · full: 1차 축 전부")
    parser.add_argument("--mode", choices=("dry", "mock", "run"), default="mock",
                        help="dry: 전개만 · mock: 무과금 · run: 실 LLM")
    parser.add_argument("--repeat", type=int, default=1, help="시나리오 반복 수")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.preflight:
        return cmd_preflight(args)
    if args.show_env:
        return cmd_show_env(args)
    if args.sweep:
        return cmd_sweep(args)
    if args.propose:
        return cmd_propose(args)
    return cmd_validate(args)


if __name__ == "__main__":
    raise SystemExit(main())
