"""단일 진입점 (plans/94 「실행 가이드」 · §4.4).

    python -m scripts.scenario                  # (1) 사전 점검 - 무과금 · 기본
    python -m scripts.scenario --run            # (2) 실 실행   - 과금 · 승인 필요
    python -m scripts.scenario --report <run>   # (3) 리포트 재생성 - 무과금
    python -m scripts.scenario --analyze        # (4) 분석/대안 수립 - 무과금

**인자 없는 기본 동작이 무과금이다.** 카탈로그 검증 -> 모의 실행 -> 예상치를 차례로
수행한다. 아무것도 모르고 이 명령을 쳐도 돈이 나가지 않는 것이 D-127 을 코드로 지키는
방식이다. 실 LLM 경로는 `--run` + `RUN_E2E=1` 뒤에만 열린다.

콘솔 출력은 ASCII 구두점만 쓴다 - cp949 콘솔에서 em-dash 가 UnicodeEncodeError 로
런을 죽인다(docs/18:82 · 부록 A.1-3 · W5).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

from .analyze import analyze
from .catalog import Catalog, CatalogError, load_catalog
from .report import write_report
from .runner import RESULTS_ROOT, RunConfig, estimate, execute, latest_run


def _require_optin() -> None:
    """D-127 하드 게이트. 옵트인 없이는 어떤 실 호출도 하지 않는다.

    `scripts/eval_routing.py:46` 의 `_require_optin` 과 같은 패턴이다. 키가 존재한다는
    이유만으로 실행되는 게이팅은 금지다 - 키는 `.encenv` 에 상존한다는 전제이기 때문이다.
    """
    if os.getenv("RUN_E2E") != "1":
        print(
            "거부: 실 LLM 호출은 D-127 건별 사용자 승인 대상입니다.\n"
            "  승인 후에만 RUN_E2E=1 을 설정해 재실행하세요.\n"
            "  (호출 없이 점검만 하려면 인자 없이 실행: python -m scripts.scenario)",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _load(strict: bool = True) -> Optional[Catalog]:
    try:
        return load_catalog()
    except CatalogError as exc:
        print(f"[1단] 카탈로그 거부 {len(exc.errors)}건", file=sys.stderr)
        for error in exc.errors:
            print(f"  - {error}", file=sys.stderr)
        if strict:
            raise SystemExit(1)
        return None


def cmd_dry_run(args: argparse.Namespace) -> int:
    """1단 - 카탈로그만 검증한다. 서버를 띄우지 않는다."""
    catalog = _load()
    assert catalog is not None
    selected = catalog.select(args.group, args.only, args.env)
    print(f"[1단] 카탈로그 OK - 군 {len(catalog.groups)}개, 시나리오 {len(catalog.scenarios)}건")
    print(f"       선택: {len(selected)}건 (env={args.env})")
    groups: dict[str, int] = {}
    for scenario in selected:
        groups[scenario.group] = groups.get(scenario.group, 0) + 1
    for group_id in sorted(groups):
        header = catalog.groups[group_id]
        print(f"       {group_id:<5} {groups[group_id]:>4}건  목표 {header.latency_target_ms}ms"
              f"  정책확정={header.policy_confirmed}")
    covered = catalog.plans_index()
    print(f"       계획서 역추적: {len(covered)}개 계획서가 최소 1건의 시나리오를 가진다")
    return 0


def cmd_estimate(args: argparse.Namespace) -> int:
    """3단 - 예상치를 낸다. 이 출력이 D-127 승인 요청의 근거다."""
    catalog = _load()
    assert catalog is not None
    config = RunConfig(mode="run", env=args.env, repeat=args.repeat,
                       groups=args.group, only=args.only)
    result = estimate(catalog, config)
    print("[3단] 예상치 - 이 출력을 승인권자에게 제시한다")
    print(f"       시나리오      : {result['scenarios']}건")
    print(f"       실행 턴       : {result['turns']}회 (R군 {result['r_group_turns']}회 포함)")
    print(f"       예상 LLM 호출 : {result['estimated_llm_calls']}회"
          f" (가정: 턴당 {result['assumed_llm_calls_per_turn']}회)")
    print(f"       예상 소요 상한: {result['estimated_wall_sec_upper']:.0f}초")
    print(f"       프로파일      : {', '.join(result['profiles'])} (= 서버 기동 {len(result['profiles'])}회)")
    print(f"       주의: {result['note']}")
    return 0


def cmd_mock(args: argparse.Namespace) -> int:
    """2단 - MockGraph 서버로 전 경로를 돈다. LLM/DB 미호출."""
    problem = _check_resume(args)
    if problem:
        print(problem, file=sys.stderr)
        return 1
    catalog = _load()
    assert catalog is not None
    config = RunConfig(
        mode="mock", env=args.env, repeat=args.repeat, groups=args.group,
        only=args.only, profiles=args.profile, port=args.port,
        token=args.token, timeout_sec=args.timeout, resume_from=args.resume,
    )
    summary = execute(catalog, config)
    run_dir = Path(summary["out_dir"])
    paths = write_report(run_dir, catalog)
    print(f"[2단] 모의 실행 완료 - 턴 {summary['executed_turns']}회")
    print(f"       산출: {run_dir}")
    print(f"       리포트: {paths['report']}")
    if summary["skipped"]:
        print(f"       제외 {len(summary['skipped'])}건 - report.md 10절 참조")
    return 0


def _check_resume(args: argparse.Namespace) -> Optional[str]:
    """--resume 대상이 실제로 있는지 본다.

    없는 run_id 를 주면 이어붙이는 대신 **같은 이름의 새 런**이 생겨, 이어서 돌린 줄 알고
    처음부터 다시 도는 사고가 난다. 폐쇄망 장시간 실행에서 비용이 가장 큰 실수다.
    """
    if args.resume and not (RESULTS_ROOT / args.resume).exists():
        return f"--resume 대상이 없습니다: {RESULTS_ROOT / args.resume}"
    return None


def cmd_run(args: argparse.Namespace) -> int:
    """4단 - 실 LLM/실 DB. 옵트인 뒤에만 열린다."""
    _require_optin()
    problem = _check_resume(args)
    if problem:
        print(problem, file=sys.stderr)
        return 1
    catalog = _load()
    assert catalog is not None
    config = RunConfig(
        mode="run", env=args.env, repeat=args.repeat, groups=args.group,
        only=args.only, profiles=args.profile, port=args.port,
        token=args.token, timeout_sec=args.timeout, resume_from=args.resume,
    )
    result = estimate(catalog, config)
    print("[4단] 실 실행 - 과금 경로")
    print(f"       예상 LLM 호출 {result['estimated_llm_calls']}회 "
          f"(가정: 턴당 {result['assumed_llm_calls_per_turn']}회)")
    if not args.yes:
        try:
            answer = input("       이 스위트 1회 실행을 승인합니까? [y/N] ").strip().lower()
        except EOFError:
            # 비대화 환경(CI/파이프)에서는 승인을 받을 수 없다. 승인 없이 과금 경로를
            # 여는 것보다 멈추는 것이 맞다 - 명시 승인은 --yes 로만 준다(D-127).
            print("       중단했습니다: 비대화 환경입니다. 승인했다면 --yes 를 붙이세요.")
            return 130
        if answer != "y":
            print("       중단했습니다.")
            return 130
    summary = execute(catalog, config)
    run_dir = Path(summary["out_dir"])
    paths = write_report(run_dir, catalog)
    analyze(run_dir, catalog)
    print(f"       완료 - 턴 {summary['executed_turns']}회, 리포트 {paths['report']}")
    return 0


def _resolve_run_dir(name: Optional[str]) -> Optional[Path]:
    if name:
        path = RESULTS_ROOT / name
        return path if path.exists() else None
    return latest_run()


def cmd_report(args: argparse.Namespace) -> int:
    run_dir = _resolve_run_dir(args.report)
    if run_dir is None:
        print("리포트를 만들 run 이 없습니다.", file=sys.stderr)
        return 1
    catalog = _load(strict=False)
    paths = write_report(run_dir, catalog)
    print(f"리포트 재생성: {paths['report']}")
    print(f"요약(JSON)   : {paths['summary']}")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    run_dir = _resolve_run_dir(args.analyze if isinstance(args.analyze, str) else None)
    if run_dir is None:
        print("분석할 run 이 없습니다.", file=sys.stderr)
        return 1
    catalog = _load(strict=False)
    written = analyze(run_dir, catalog)
    print(f"분석 산출 {len(written)}종:")
    for path in written:
        print(f"  - {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.scenario",
        description="기능/성능 시나리오 자동 실행 하네스 (plans/94). 기본 동작은 무과금입니다.",
    )
    mode = parser.add_argument_group("동작 (미지정 시 무과금 기본: dry-run -> mock -> estimate)")
    mode.add_argument("--dry-run", action="store_true", help="1단 카탈로그만 검증 (무과금)")
    mode.add_argument("--mock", action="store_true", help="2단 모의 서버로 전 경로 (무과금)")
    mode.add_argument("--estimate", action="store_true", help="3단 예상치 출력 (무과금)")
    mode.add_argument("--run", action="store_true", help="4단 실 LLM 실행 (과금 · RUN_E2E=1 필요)")
    mode.add_argument("--report", nargs="?", const=True, metavar="RUN_ID",
                      help="리포트 재생성 (무과금)")
    mode.add_argument("--analyze", nargs="?", const=True, metavar="RUN_ID",
                      help="분석/대안 수립 (무과금)")

    select = parser.add_argument_group("선택")
    select.add_argument("--profile", action="append", default=[], help="플래그 프로파일 (반복 가능)")
    select.add_argument("--group", action="append", default=[], help="군 문자 (예: C · R4)")
    select.add_argument("--only", default=[], type=lambda v: v.split(","),
                        help="시나리오 ID 목록 (쉼표 구분)")
    select.add_argument("--repeat", type=int, default=1, help="반복 횟수 (R군은 기본 3)")
    select.add_argument("--env", choices=["closed", "sandbox"], default="sandbox",
                        help="대상 환경 선언")
    select.add_argument("--resume", metavar="RUN_ID", help="중단된 런을 이어서")
    select.add_argument("--port", type=int, help="자식 서버 포트 (미지정 시 자동)")
    select.add_argument("--token", help="관리자 토큰 (AUTH_ENABLED=true 인 환경)")
    select.add_argument("--timeout", type=float, default=360.0, help="시나리오당 상한(초)")
    select.add_argument("--yes", action="store_true", help="--run 의 승인 프롬프트 생략")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.report:
        args.report = None if args.report is True else args.report
        return cmd_report(args)
    if args.analyze:
        return cmd_analyze(args)
    if args.run:
        return cmd_run(args)
    if args.dry_run:
        return cmd_dry_run(args)
    if args.mock:
        return cmd_mock(args)
    if args.estimate:
        return cmd_estimate(args)

    # 인자 없음 = 무과금 기본 동작. 1단을 통과하지 못하면 2단은 시작되지 않는다.
    print("무과금 기본 동작: 1단 카탈로그 검증 -> 2단 모의 실행 -> 3단 예상치")
    print("실 LLM 실행은 --run (RUN_E2E=1 필요) 입니다.\n")
    code = cmd_dry_run(args)
    if code != 0:
        return code
    print()
    code = cmd_mock(args)
    if code != 0:
        return code
    print()
    return cmd_estimate(args)


if __name__ == "__main__":
    raise SystemExit(main())
