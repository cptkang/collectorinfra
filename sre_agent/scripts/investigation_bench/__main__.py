"""벤치 CLI (plans/154 §9.3.0 — 옵션 계약의 정본은 계획서 표).

동작 하나 = `_ACTIONS`의 함수 하나 + 상호 배타 그룹의 옵션 하나. `--lint`는 W4(`lint`)다.
채점·보고서·반출(`--report`·`--rejudge`·`--export`)은 W3a 모듈(`judge`·`report`·`export`)에
위임한다.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from investigation_bench import runner, schema


def _schema_snapshot(args: argparse.Namespace) -> int:
    """실 서버 `list_tools` → 스냅샷 파일(§9.3.3)."""
    urls = {"polestar": args.polestar_url, "apm": args.apm_url}
    try:
        snap = schema.fetch_snapshot(urls)
    except Exception as e:
        print(f"스냅샷 실패 — 서버가 떠 있는지 확인: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    path = schema.write_snapshot(snap, args.out)
    for server in schema.SERVERS:
        note = "" if urls.get(server) else " (URL 미지정 — 빈 묶음)"
        print(f"{server}: 도구 {len(snap[server])}종{note}")
    print(f"해시 {schema.snapshot_hash(snap)}")
    print(f"저장 {path}")
    return 0


def _lint(args: argparse.Namespace) -> int:
    """세계 YAML 전건 린트 — 위반 목록 출력 · 오류가 있으면 1(§4.6)."""
    from investigation_bench import lint

    return lint.main(args.lint)


def _show_env(args: argparse.Namespace) -> int:
    return runner.show_env(args.spec, args.site)


def _preflight(args: argparse.Namespace) -> int:
    return runner.run_preflight_only(args.spec, cli_site=args.site, test=args.test,
                                     allow_billed=args.allow_billed)


def _run(args: argparse.Namespace) -> int:
    return runner.run(runner.RunOptions(
        spec_path=args.spec, cli_site=args.site, test=args.test, dry_run=args.dry_run,
        allow_billed=args.allow_billed, out_root=args.out_root))


def _resume(args: argparse.Namespace) -> int:
    return runner.run(runner.RunOptions(
        spec_path=args.spec, cli_site=args.site, test=args.test,
        allow_billed=args.allow_billed, resume_dir=args.resume))


def _status(args: argparse.Namespace) -> int:
    return runner.status(args.status)


def _cleanup(args: argparse.Namespace) -> int:
    try:
        killed = runner.cleanup(args.cleanup)
    except runner.RunnerError as e:
        print(f"정리 실패: {e}", file=sys.stderr)
        return e.exit_code
    print(f"종료한 벤치 프로세스: {killed or '없음'}")
    return 0


def _report(args: argparse.Namespace) -> int:
    from investigation_bench import report

    print(f"보고서: {report.write_report(args.report)}")
    return 0


def _rejudge(args: argparse.Namespace) -> int:
    from investigation_bench import judge

    print(f"재채점·보고서: {judge.rejudge(args.rejudge, runner.worlds_root(args.rejudge))}")
    return 0


def _export(args: argparse.Namespace) -> int:
    from investigation_bench import export

    try:
        bundle = export.export_bundle(args.export)
    except export.ExportLeakError as e:
        print(f"반출 묶음 미생성 — 잔존 검사 실패: {e}", file=sys.stderr)
        return 1
    print(f"반출 묶음: {bundle}")
    return 0


_ACTIONS: dict[str, Callable[[argparse.Namespace], int]] = {
    "schema_snapshot": _schema_snapshot,
    "lint": _lint,
    "show_env": _show_env,
    "preflight": _preflight,
    "run": _run,
    "resume": _resume,
    "status": _status,
    "cleanup": _cleanup,
    "report": _report,
    "rejudge": _rejudge,
    "export": _export,
}
_NEEDS_SPEC = ("show_env", "preflight", "run")
_DEFAULT_WORLDS_ROOT = runner.TOP / runner.DEFAULT_WORLDS_DIR


def build_parser() -> argparse.ArgumentParser:
    """CLI 파서."""
    p = argparse.ArgumentParser(prog="inv_bench", description="sre_agent 장애 조사 품질 벤치마크")
    action = p.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--schema-snapshot", action="store_true",
        help="실 mcp_server(+apm_gateway) list_tools → tool_schemas.json (실 LLM 0)",
    )
    action.add_argument(
        "--lint", nargs="?", type=Path, const=_DEFAULT_WORLDS_ROOT, metavar="WORLDS_DIR",
        help="세계 YAML 린트(라벨·누수·핵심 증거·스냅샷·문구 중복·홀드아웃 토큰 · 오류면 exit 1)",
    )
    action.add_argument("--show-env", action="store_true",
                        help="실험군별 sre_agent 환경변수 출력(비밀 값 가림 · 실 LLM 0)")
    action.add_argument("--preflight", action="store_true",
                        help="사전 점검만(과금 평면 · 포트 · /v1/models · tool-calling 왕복 1회)")
    action.add_argument("--run", action="store_true",
                        help="사전 점검 → 프로세스 기동 → 실행 → 채점 → 보고서")
    action.add_argument("--resume", type=Path, metavar="RUN_DIR",
                        help="끊긴 실행 이어서(끝난 arm×world×trial 건너뜀 · 명세·커밋 같을 때만)")
    action.add_argument("--status", type=Path, metavar="RUN_DIR", help="진행 수 · 무효표 수 · 경과")
    action.add_argument("--cleanup", type=Path, metavar="RUN_DIR",
                        help="pids.json에 적힌 자기 프로세스만 종료")
    action.add_argument("--report", type=Path, metavar="RUN_DIR", help="report.md 다시 만들기")
    action.add_argument("--rejudge", type=Path, metavar="RUN_DIR",
                        help="현재 세계 YAML·채점기로 재채점(이전 판 보존)")
    action.add_argument("--export", type=Path, metavar="RUN_DIR",
                        help="반출 묶음 <run_dir>.bundle/ (가림 + 잔존 검사)")
    p.add_argument("--spec", type=Path, help="실행 명세 YAML(testdata/investigation_bench/specs/)")
    p.add_argument("--site", choices=("local", "closed"), default="local",
                   help="실행 위치 — 명세 run.site와 같아야 한다(기본 local)")
    p.add_argument("--dry-run", action="store_true", help="--run: 실행 계획만 출력(프로세스 0)")
    p.add_argument("--test", action="store_true", help="가짜 LLM(backend=fake) 허용 — 테스트 전용")
    p.add_argument("--allow-billed", metavar="APPROVAL",
                   help="과금 평면 허용 — 사용자 승인 문구를 그대로 적는다"
                        "(manifest에 남는다 · D-127)")
    p.add_argument("--out-root", type=Path, default=None,
                   help="run 디렉터리 루트"
                        "(기본 명세 run.out_root → sre_agent/.data/investigation_bench)")
    p.add_argument("--polestar-url", help="mcp_server SSE URL (예: http://127.0.0.1:19197/sse)")
    p.add_argument("--apm-url", help="apm_gateway SSE URL (예: http://127.0.0.1:19196/sse)")
    p.add_argument(
        "--out", type=Path, default=schema.DEFAULT_SNAPSHOT_PATH,
        help="스냅샷 출력 경로 (기본 testdata/investigation_bench/tool_schemas.json)",
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 진입 — 종료 코드를 돌려준다."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.schema_snapshot and not args.polestar_url:
        parser.error("--schema-snapshot에는 --polestar-url이 필요하다")
    name = next(k for k in _ACTIONS if getattr(args, k, False))
    if name in _NEEDS_SPEC and args.spec is None:
        parser.error(f"--{name.replace('_', '-')}에는 --spec이 필요하다")
    if args.allow_billed is not None and not args.allow_billed.strip():
        parser.error("--allow-billed에는 사용자 승인 문구가 필요하다(빈 문구 불가)")
    return _ACTIONS[name](args)


if __name__ == "__main__":
    raise SystemExit(main())
