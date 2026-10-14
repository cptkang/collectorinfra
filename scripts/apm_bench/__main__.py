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
                    --mlx-url · --mlx-model · --probe/--no-probe(P층 — 빈 결과 판별 · 허용값
                    탐침 · 기본 E2 켬 · E1 끔)
                    --placeholders <파일>(E1 기본 testdata/apm_bench/placeholders.e1.yaml ·
                    E2 기본 testdata/apm_bench/local/placeholders.e2.yaml · 로컬 전용 ·
                    커밋·반출 금지) ·
                    --only <id…> · --smoke(plans/164 §5 스모크 10개 · --only 와 함께 못 씀) ·
                    --limit N · --debug-raw <dir>(E1 전용 원본)
                    · --view-server-version(E2 필수 · 기본 testdata/apm_bench/local/bench.local.yaml
                    `view_server_version` · 5.6.2.18 미만이면 exit 2)
                    · --skip-heavy/--no-skip-heavy(무거운 등급 끄기 — 건너뜀 `heavy_off` · 기본
                    E2 켬 · E1 끔)
                    · --term-policy <파일>(일반 단어 허용 목록 · E1 기본 testdata/apm_bench/
                    term_policy.yaml = 빈 목록 · E2 기본
                    testdata/apm_bench/local/term_policy.local.yaml)
                    · --bg(분리 실행 — run id · `--status` 명령을 찍고 바로 exit 0)
                    E2 로컬 파일이 없으면 둘 위치를 안내하고 exit 2(plans/164 W2b) — 자동으로
                    채운 값은 「자동: …」 한 줄씩(출처만) 찍는다
                    · --quarantine-dir <기준>(관문 실패 격리 경로 · 기본 ~/apm_bench_quarantine)
                    exit 1: run 무효(APM 관측 0) · 관문 실패(산출물 미기록) · 근거 필수 칸 누락
                    run 디렉터리(plans/164 W2a): 실행 로그 `run.log`(콘솔 = 로그 · 끝에 관문 재검사)
                    · 화이트리스트 메타 `run_meta.json`(시작 running → 끝 갱신 · 관문 산출물
                    `run.json`과 다름) · `run.pid`(끝나면 삭제) · `summary_safe.json`(안전 요약 ·
                    관문 성패와 무관) · `manifest.sha256`(반출 허용 파일 목록) · `scenarios.yaml`
                    (저장소 안 시나리오 사본). 관문 실패면 치환 전 재료를 저장소 밖 격리 경로에
                    700/600 으로 보관한다(반출 금지 · 14일 보존)
    --init-local    E2 로컬 파일 양식 3개를 testdata/apm_bench/local/ 에 만든다(700/600 ·
                    있는 파일은 그대로 · 바꿀 칸은 값 CHANGE_ME — 남으면 E2 --run 이 exit 2)
    --status [RUN_ID] run 상태(run 메타 · PID 생존 · 경과 · run.log 마지막 줄 · 생략 = 최근 run)
    --regate RUN    격리본 → 치환 → 근거 → 관문 → 산출물 재기록(실행 없음 · run id · 격리 디렉터리 ·
                    run 디렉터리) — 격리본이 현재 계정 소유 · 700 이 아니면 exit 2
    --purge [RUN_ID] 격리본 삭제(run id 지정 · 생략 = 14일 지난 것 전부) · 남은 run id ·
                    경과 일 출력
    --fewshot-check 예문 과적합(시나리오 원문 n-gram) · APM 절 크기 상한 — 위반 시 exit 1 ·
                    다음에 돌릴 게이트 명령 출력
    --compare A B   두 run 산출물 대조(run id · 경로 · holdout 턴 정확도 · 대상 ID · 악화 · 승격)
                    → markdown
                    (`--out` 이 있으면 그 디렉터리에 compare.md)
    --replay RUN    (run id · 경로) dev 실패 턴(또는 --turns id#n … · holdout 지정은 경고)의
                    분해·재선택만 실 MLX 로
                    재현 — 두 평면 mlx · 루프백 · MLX 점검 통과가 아니면 exit 2 · 결과는 방향 확인용
    --audit-digest [RUN_ID | LOG...]
                    본체 감사 로그 → 화이트리스트 진단표 `audit_digest.json`(plans/164 W0a ·
                    LLM·네트워크 0). run id 모드(인자 없음 = 최근 run · run id 하나 — 구 방식
                    `YYYYmmdd_HHMM` 포함): 창 = run 메타 시작·끝 ± 5분 · 로그 = 창이 걸친 로컬
                    날짜의 logs/audit-<날짜>*.jsonl(로테이션 포함) · 시나리오 = run 사본 ·
                    --env·--skip-heavy·--only·--limit = run 메타 · 출력 = run 디렉터리(없으면
                    results/apm_bench/<run_id>_audit/). 메타 없는 run 은 run id 시각 + 「소요 N초」
                    (--duration 또는 실행 로그 run_<id>.log)로 창을 추정한다(window_estimated).
                    로그 경로 모드: --since/--until(ISO-8601 · 오프셋 필수) · --out 필수.
                    명시 인자가 늘 우선 · --bench-user(기본 anonymous) · exit 1 자기 점검 실패
                    (파일 미기록) · exit 2 인자 오류

기본 동작(인자 없음)은 `--dry-run` 이다 — 모르고 실행해도 LLM·네트워크를 부르지 않는다.
모드는 `MODES`(인자 이름 → 처리 함수)에 한 줄씩 더한다.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import getpass
import hashlib
import json
import os
import socket
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import IO, Any

from . import CAPABILITY_MAP_PATH, COVERAGE_DIR, REPO_ROOT, RESULTS_ROOT, SCENARIOS_PATH
from . import scenarios as sc

#: 실행 로그 손잡이(`run.log` tee — 콘솔 = 로그 · plans/164 W2a).
_LOG: list[IO[str]] = []


def say(text: str = "") -> None:
    print(text, flush=True)
    for handle in _LOG:
        handle.write(text + "\n")
        handle.flush()


@contextlib.contextmanager
def _tee(handle: IO[str]) -> Iterator[None]:
    """이 블록의 `say` 줄을 `handle`(run.log)에도 쓴다."""
    _LOG.append(handle)
    try:
        yield
    finally:
        _LOG.remove(handle)


def _load(args: argparse.Namespace) -> tuple[list[sc.Scenario], list[sc.Issue]] | None:
    """시나리오(구조 위반 시나리오 제외)와 구조 위반을 읽는다 — 파일 자체를 못 읽으면 None."""
    import yaml

    path = Path(args.scenarios or SCENARIOS_PATH)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:  # 경로·원문 미출력(홈 경로 = 사용자 정보)
        say(f"[중단] 시나리오 파일을 읽지 못했다 — {type(exc).__name__}")
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


def make_run_id(env: str, sha: str, now: datetime | None = None, *, smoke: bool = False) -> str:
    """`YYYYmmdd-HHMMSS-<env>-<SHA 7자>`(KST) · `--smoke`면 끝에 `-smoke`
    (`safe_summary.RUN_ID_RE`)."""
    from .driver import KST

    stamp = (now or datetime.now(KST)).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{env}-{sha[:7]}" + ("-smoke" if smoke else "")


#: 로컬 전용 파일 디렉터리(`testdata/apm_bench/local/` — `.gitignore` 보호선 · 커밋·반출 금지 ·
#: plans/164 W2b · D-323 부기 2026-10-14 — 종전 `~/apm_bench_local/`).
LOCAL_DIR = SCENARIOS_PATH.parent / "local"
LOCAL_LABEL = "testdata/apm_bench/local"
LOCAL_PLACEHOLDERS = "placeholders.e2.yaml"
LOCAL_TERM_POLICY = "term_policy.local.yaml"
#: 작은 로컬 설정(키 화이트리스트 `BENCH_LOCAL_KEYS` — 모르는 키는 경고 후 무시).
LOCAL_BENCH = "bench.local.yaml"
BENCH_LOCAL_KEYS: frozenset[str] = frozenset({"view_server_version"})
#: `--init-local` 양식(저장소 — 운영 값 없음). 바꿀 칸은 값이 `UNFILLED`로 시작한다.
LOCAL_TEMPLATES = SCENARIOS_PATH.parent / "local_templates"
UNFILLED = "CHANGE_ME"
#: `--smoke` 내장 목록(plans/164 §5 1단계 — 앞 6개 = 1회차 위반 턴 · 뒤 4개 = 대상 종류별 대표).
SMOKE_IDS: tuple[str, ...] = (
    "APM-BIZTREND-interval", "APM-PROC-pid-host", "APM-JSERVER-dbpath",
    "APM-JSERVER-dbpath-host", "APM-SRC-legacy", "APM-SRC-none-all", "APM-INST-host",
    "APM-HEALTH-inst", "APM-BIZ-name", "APM-SERVICE-name",
)


def local_path(name: str) -> Path:
    """`testdata/apm_bench/local/<name>`."""
    return LOCAL_DIR / name


def local_label(name: str) -> str:
    """콘솔용 로컬 파일 표기(저장소 상대 경로 — 절대 경로에는 홈 = 사용자 정보가 섞인다)."""
    return f"{LOCAL_LABEL}/{name}"


#: 로컬 파일 신뢰 조건 안내(위반 시 고정 문구 — 경로는 `~` 표기 · 값 미출력).
TRUST_HINT = "현재 계정 소유 · 심볼릭 링크 아님 · 그룹·기타 쓰기 없음(chmod go-w)"


def untrusted_reason(path: Path, *, directory: bool = False) -> str | None:
    """E2 로컬 입력 파일·디렉터리 신뢰 검사(plans/164 W2b 감사 M1) → 위반 사유(없으면 None).

    없는 경로는 None — 「없음」은 호출 측이 따로 안내한다. 심볼릭 링크는 따라가지 않는다(lstat).
    """
    import stat

    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"상태를 읽지 못했다({type(exc).__name__})"
    if stat.S_ISLNK(info.st_mode):
        return "심볼릭 링크다"
    if directory != stat.S_ISDIR(info.st_mode) or not (directory or stat.S_ISREG(info.st_mode)):
        return "디렉터리가 아니다" if directory else "일반 파일이 아니다"
    if info.st_uid != os.geteuid():
        return "현재 계정 소유가 아니다"
    if info.st_mode & 0o022:
        return "그룹·기타 쓰기 권한이 있다"
    return None


def local_untrusted(name: str) -> str | None:
    """`testdata/apm_bench/local/`과 그 안 `name`의 신뢰 검사 → 중단 문구(없으면 None)."""
    why = untrusted_reason(LOCAL_DIR, directory=True)
    if why:
        return f"{LOCAL_LABEL}/ 를 신뢰할 수 없다 — {why} ({TRUST_HINT})"
    why = untrusted_reason(local_path(name))
    return f"{local_label(name)} 를 신뢰할 수 없다 — {why} ({TRUST_HINT})" if why else None


def read_bench_local() -> tuple[dict[str, str], str | None]:
    """`testdata/apm_bench/local/bench.local.yaml` → (화이트리스트 키 값, 못 읽은 사유).
    없으면 ({}, None).

    값은 문자열로 바꾼다(YAML 이 `5.6`을 실수로 읽는다). 모르는 키는 이름 없이 건수만 경고한다.
    """
    import yaml

    path = local_path(LOCAL_BENCH)
    untrusted = local_untrusted(LOCAL_BENCH)
    if untrusted:
        return {}, untrusted
    if not path.is_file():
        return {}, None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:  # 원문·경로 미출력
        return {}, f"{local_label(LOCAL_BENCH)} 를 읽지 못했다 — {type(exc).__name__}"
    if data is None:
        return {}, None
    if not isinstance(data, dict):
        return {}, f"{local_label(LOCAL_BENCH)} 는 `키: 값` 매핑이어야 한다"
    unknown = [k for k in data if k not in BENCH_LOCAL_KEYS]
    if unknown:
        say(f"[경고] {local_label(LOCAL_BENCH)} 모르는 키 {len(unknown)}개 무시 — 아는 키: "
            f"{', '.join(sorted(BENCH_LOCAL_KEYS))}")
    return {k: str(v) for k, v in data.items()
            if k in BENCH_LOCAL_KEYS and isinstance(v, (str, int, float))
            and not isinstance(v, bool)}, None


def unfilled_count(path: Path) -> int:
    """YAML 파일에서 값이 `UNFILLED`로 시작하는 칸 수(주석은 세지 않는다 · 못 읽으면 0 — 읽기
    오류는 뒤 사전 점검이 따로 낸다)."""
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return 0

    def walk(node: object) -> int:
        if isinstance(node, dict):
            return sum(walk(v) for v in node.values())
        if isinstance(node, list):
            return sum(walk(v) for v in node)
        return int(isinstance(node, str) and node.strip().startswith(UNFILLED))

    return walk(data)


def cmd_init_local(args: argparse.Namespace) -> int:
    """`testdata/apm_bench/local/`에 E2 로컬 파일 3개 양식을 만든다(디렉터리 700 · 파일 600 ·
    있는 파일은 덮어쓰지 않는다). 자리표 카나리아는 임의 토큰으로 채운다. 바꿀 칸 수와 다음 명령을
    찍는다."""
    import secrets

    from .hygiene import TERM_POLICY_PATH

    base = LOCAL_DIR
    why = untrusted_reason(base, directory=True)
    if why:
        say(f"[중단] {LOCAL_LABEL}/ 를 신뢰할 수 없다 — {why} ({TRUST_HINT})")
        return 2
    base.mkdir(mode=0o700, exist_ok=True)
    base.chmod(0o700)
    for name, src in ((LOCAL_PLACEHOLDERS, LOCAL_TEMPLATES / LOCAL_PLACEHOLDERS),
                      (LOCAL_TERM_POLICY, TERM_POLICY_PATH),
                      (LOCAL_BENCH, LOCAL_TEMPLATES / LOCAL_BENCH)):
        dest = local_path(name)
        if dest.exists() or dest.is_symlink():
            say(f"있음(그대로 둠): {local_label(name)}")
        else:
            text = src.read_text(encoding="utf-8")
            for slot in ("__CANARY_1__", "__CANARY_2__"):
                text = text.replace(slot, f"cnry-{secrets.token_hex(8)}")
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            say(f"만듦        : {local_label(name)}")
        count = unfilled_count(dest)
        if count:
            say(f"  → 바꿀 칸 {count}개(값이 {UNFILLED}) — 파일 머리 주석의 「바꾸는 법」을 따른다")
    say("다음: 값을 채운 뒤 python -m scripts.apm_bench --run --env e2 --smoke")
    return 0


def apply_run_defaults(args: argparse.Namespace) -> dict[str, str | None] | int:
    """`--run` 자동값(plans/164 W2b) — 명시 인자가 늘 우선이고, 채운 값은 「자동: …」 한 줄씩 출처만
    찍는다(경로는 `~` 표기 · 값 미출력). → 출처 표 또는 exit 코드(2 = 로컬 파일 없음 · 인자 충돌 ·
    신뢰 조건 위반 — E2 로컬 디렉터리·파일과 명시 `--placeholders`·`--term-policy` 파일은 현재 계정
    소유 · 심볼릭 링크 아님 · 그룹·기타 쓰기 없음이어야 한다 · `untrusted_reason`).

    - `--smoke` → `--only SMOKE_IDS`(`--only`와 함께면 2)
    - E2: `--placeholders`·`--term-policy` 미지정 → `testdata/apm_bench/local/` 기본 파일 ·
      뷰 서버 버전 미지정 → `bench.local.yaml` `view_server_version` ·
      `--skip-heavy`·`--probe` 미지정 → 켬
    - E1: 종전 그대로(저장소 testdata 자리표 · 허용 목록 · 두 플래그 끔)
    """
    args.env = args.env or "e1"
    if getattr(args, "smoke", False):
        if args.only:
            say("[중단] --smoke 와 --only 는 함께 쓰지 않는다(--smoke = 내장 목록 "
                f"{len(SMOKE_IDS)}개)")
            return 2
        args.only = list(SMOKE_IDS)
        say(f"자동: --only — 스모크 목록 {len(SMOKE_IDS)}개(plans/164 §5)")
    sources: dict[str, str | None] = {
        "placeholders": "explicit" if args.placeholders else "e1_default",
        "term_policy": "explicit" if args.term_policy else "default",
        "view_server_version": "explicit" if args.view_server_version else None,
    }
    if args.env != "e2":
        args.skip_heavy = bool(args.skip_heavy)
        args.probe = bool(args.probe)
        return sources
    missing: list[str] = []
    for attr, name, what in (("placeholders", LOCAL_PLACEHOLDERS, "자리표"),
                             ("term_policy", LOCAL_TERM_POLICY, "일반 단어 허용 목록")):
        flag = f"--{attr.replace('_', '-')}"
        if getattr(args, attr):  # 명시 파일도 같은 신뢰 조건(파일만 · 경로 미출력)
            why = untrusted_reason(Path(getattr(args, attr)).expanduser())
            if why:
                say(f"[중단] {flag} 파일을 신뢰할 수 없다 — {why} ({TRUST_HINT})")
                return 2
            continue
        untrusted = local_untrusted(name)
        if untrusted:
            say(f"[중단] {untrusted}")
            return 2
        if local_path(name).is_file():
            setattr(args, attr, str(local_path(name)))
            sources[attr] = "local_default"
            say(f"자동: {what} — {local_label(name)}")
        else:
            missing.append(f"  {local_label(name)} — {what}(또는 {flag} <파일>)")
    if not args.view_server_version:
        values, why = read_bench_local()
        if why:
            say(f"[중단] {why}")
            return 2
        if values.get("view_server_version"):
            args.view_server_version = values["view_server_version"]
            sources["view_server_version"] = "bench_local"
            say(f"자동: 뷰 서버 버전 — {local_label(LOCAL_BENCH)}")
        else:
            missing.append(f"  {local_label(LOCAL_BENCH)} — "
                           "`view_server_version: <운영자 확인 값>` 한 줄"
                           "(도구로 얻을 수 없다 · 또는 --view-server-version)")
    if missing:
        say("[중단] E2 로컬 파일이 없다 — 아래 위치에 두고 다시 실행한다(저장소 밖 · 반출 금지):")
        for line in missing:
            say(line)
        say("  (양식 만들기: python -m scripts.apm_bench --init-local)")
        return 2
    unfilled: list[str] = []
    count = unfilled_count(Path(args.placeholders).expanduser())
    if count:
        label = (local_label(LOCAL_PLACEHOLDERS) if sources["placeholders"] == "local_default"
                 else "--placeholders 파일")
        unfilled.append(f"  {label} — {count}칸")
    if (sources["view_server_version"] == "bench_local"
            and str(args.view_server_version).strip().startswith(UNFILLED)):
        unfilled.append(f"  {local_label(LOCAL_BENCH)} — view_server_version")
    if unfilled:
        say(f"[중단] E2 로컬 파일에 바꾸지 않은 칸(값 {UNFILLED})이 남았다 — 운영 값으로 바꾼다"
            "(파일 머리 주석 「바꾸는 법」):")
        for line in unfilled:
            say(line)
        return 2
    for attr, flag in (("skip_heavy", "--skip-heavy"), ("probe", "--probe")):
        if getattr(args, attr) is None:
            setattr(args, attr, True)
            say(f"자동: {flag} 켬(E2 기본 · 끄기 --no-{flag[2:]})")
    return sources


#: `--user` 계정 비밀번호 환경변수 — 명령줄 인자로 받지 않는다(셸 이력·프로세스 목록 노출).
PASSWORD_ENV = "APM_BENCH_PASSWORD"


def run_user_values(login_id: str | None = None) -> dict[str, str | None]:
    """누출 관문 사용자 정보 원값(로그인 계정 · OS 사용자 · 호스트 · 호스트 짧은 이름 · 홈).

    호스트가 FQDN 이면 첫 라벨(짧은 이름)도 `host_short`로 싣는다 — 치환기는 긴 값부터 바꾸므로
    FQDN·짧은 이름 둘 다 가린다(plans/164 W1 교정 L-4).
    """
    from scripts.scenario.runner import DEFAULT_USER_ID

    host = socket.gethostname()
    values: dict[str, str | None] = {"login_id": login_id or DEFAULT_USER_ID,
                                     "os_user": getpass.getuser(), "host": host}
    short = host.split(".", 1)[0]
    if short and short != host:
        values["host_short"] = short
    values["home"] = str(Path.home())
    return values


def login_password(user: str | None) -> tuple[str | None, str | None]:
    """(`--user` 비밀번호, 막는 사유) — 환경변수 → 대화형 프롬프트 순. 계정이 없으면 (None, None)."""
    if not user:
        return None, None
    password = os.environ.pop(PASSWORD_ENV, None)  # 본체·게이트웨이 자식 env 로 넘기지 않는다
    if password:
        return password, None
    if sys.stdin is not None and sys.stdin.isatty():
        password = getpass.getpass(f"{user} 비밀번호: ")
        if password:
            return password, None
    return None, f"--user {user} 의 비밀번호가 없다 — 환경변수 {PASSWORD_ENV} 또는 대화형 입력"


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
    lines += ["", "## 2. 분류 상위", "",
              "| 분류 | 대표(실패 턴) | 대표(통과 턴 포함) | 전체 |", "|---|---:|---:|---:|"]
    for cat, n in out["categories"].items():
        lines.append(f"| {cat} | {out['primary_failure_categories'].get(cat, 0)} | "
                     f"{out['primary_categories'].get(cat, 0)} | {n} |")
    if not out["categories"]:
        lines.append("| 없음 | 0 | 0 | 0 |")
    reasons_t = out["target_fail_reasons"]
    lines += ["", f"- target 실패 하위 사유: {reasons_t if reasons_t else '없음'}",
              f"- 대상 해석 단계 instance_unresolved(`resolve_miss` · gw_error 아님): 턴 "
              f"{out['resolve_miss']['turns']} · 호출 {out['resolve_miss']['calls']}",
              "", "### 오류 호출 (도구 × 오류 코드 × 단계)", ""]
    lines += ([f"- {e['tool']} · {e['code']} · {e['stage']} {e['count']}"
               for e in out["error_calls"]] or ["- 없음"])
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
    replaced = run_meta.get("user_info_replaced")
    lines.append("- 사용자 정보 치환(자유 문장 · 값 없이 종류별 건수): "
                 + (", ".join(f"{k} {n}" for k, n in sorted(replaced.items()))
                    if isinstance(replaced, Mapping) and replaced else "대상 없음"))
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


@dataclass
class RunInputs:
    """산출 파이프라인 재료 — 치환 전 원값을 담는다(`cmd_run`·`--regate` 공유 · 격리본의 내용).

    Attributes:
        run_id · env · sha · dirty: run 식별(시작 시각 기준)
        scenarios: 로드된 시나리오 전부(커버리지 · 안전 요약 어휘)
        records: 판정이 붙은 턴 레코드(치환 전)
        meta: 드라이버 run 메타(인벤토리 이름 포함)
        placeholders · policy · user_values: 치환·관문 재료
        probe_rows: P층 행
        versions: 근거 묶음 버전(SHA · 지문 · APM 절)
        cap_map: 대응표 경로
    """

    run_id: str
    env: str
    sha: str
    dirty: bool
    scenarios: list[sc.Scenario]
    records: list[Any]
    meta: dict[str, Any]
    placeholders: Any
    policy: Any
    user_values: dict[str, str | None]
    probe_rows: list[dict[str, Any]]
    versions: dict[str, Any]
    cap_map: Path


@dataclass
class Produced:
    """산출 결과 — 관문 · 근거 누락 · 안전 요약 기록 여부(값 없이 규칙별 건수)."""

    gate_ok: bool
    violations: list[dict[str, Any]]
    missing: list[tuple[str, int, str]]
    summary: dict[str, Any]
    safe_written: bool
    safe_rules: dict[str, int]
    console: list[str]
    gate: Any


def _gate_rules(violations: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """위반 → 규칙별 건수(규칙 이름은 관문 어휘 · 밖은 `other`)."""
    from .safe_summary import GATE_RULES

    rules = Counter(str(v.get("rule")) if v.get("rule") in GATE_RULES else "other"
                    for v in violations)
    return dict(sorted(rules.items()))


def _user_info_by_turn(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """치환된 레코드 → `<id>#<턴>` 별 사용자 정보 표지 수."""
    from .hygiene import USER_INFO_MARK

    out: dict[str, int] = {}
    for record in records:
        hits = json.dumps(record, ensure_ascii=False, default=str).count(USER_INFO_MARK)
        if hits:
            out[f"{record.get('scenario_id')}#{record.get('turn')}"] = hits
    return out


def produce_outputs(
    inputs: RunInputs, out_dir: Path, registry: sc.RegistryInfo, *,
    on_gate_fail: Callable[[Sequence[Mapping[str, Any]]], None] | None = None,
) -> Produced:
    """치환 → 근거 → 산출물 → 관문 → (실패면 `on_gate_fail` = 격리) → 안전 요약(이중 관문) —
    `cmd_run`·`--regate` 공유.

    치환·관문 단계 예외는 관문 실패와 같게 다룬다(산출물 미기록 · 예외 클래스 이름만 출력).
    격리는 안전 요약보다 먼저다 — 안전 요약 생성이 죽어도 진단 재료는 남는다(V-5). 안전 요약은
    관문 성패와 무관하게 만들고, 이중 관문을 통과할 때만 쓴다(생성 예외는 클래스 이름만).
    """
    from . import hygiene as hy
    from . import judge as jd
    from . import safe_summary as ss

    summary = jd.summarize(inputs.records)
    # 레지스트리 밖 소스 id(E2 게이트웨이 `.env` 소스)는 원이름처럼 치환·대조한다(plans/164 W1 N-1)
    names = [*(inputs.meta.get("inventory_names") or []),
             *hy.unregistered_sources(inputs.records, inputs.meta, registry.source_ids)]
    values = inputs.placeholders.values
    code = hy.build_code_vocab(registry)
    try:  # 안전 요약 어휘 낱말은 토큰 사전에서 뺀다(관문도 같은 낱말 · plans/164 W1 4라운드)
        safe = ss.build_vocab(inputs.scenarios, code)
        protected: frozenset[str] = safe.literals | safe.fields
    except Exception as exc:  # noqa: BLE001 — 실패하면 코드 어휘만 뺀다(닫힌 쪽)
        protected = frozenset()
        say(f"[위생] 안전 요약 어휘 생성 실패 — {type(exc).__name__} · 코드 어휘만 보호")
    sanitizer = hy.RunSanitizer(term_policy=inputs.policy, canaries=inputs.placeholders.canaries,
                                user_values=inputs.user_values, vocab=code,
                                protected_words=protected)
    gate: Any = None
    gate_ok = False
    violations: list[dict[str, Any]] = []
    missing: list[tuple[str, int, str]] = []
    by_turn: dict[str, int] = {}
    try:
        run_meta = build_run_meta(inputs.meta, run_id=inputs.run_id, sha=inputs.sha,
                                  dirty=inputs.dirty, versions=inputs.versions, summary=summary,
                                  probe_rows=inputs.probe_rows, evidence_missing=0)
        clean = sanitizer.sanitize(inputs.records, inventory_names=names,
                                   probe_rows=inputs.probe_rows, run_meta=run_meta,
                                   placeholders=values)
        for record in clean.records:
            record["evidence"] = hy.build_evidence(record, versions=inputs.versions)
        missing = hy.check_evidence(clean.records)
        clean.run_meta["evidence_missing"] = len(missing)
        clean.run_meta["user_info_replaced"] = dict(sanitizer.user_info_counts)  # 값 없이 건수만
        by_turn = _user_info_by_turn(clean.records)
        report_md = render_report(clean.records, clean.run_meta,
                                  term_lines=hy.term_risk_lines(inputs.policy), missing=missing,
                                  probe_rows=clean.probe_rows)
        coverage_md = sc.render_coverage_md(
            sc.build_coverage(inputs.scenarios, sc.parse_capability_map(inputs.cap_map),
                              registry),
            extra_sections=(jd.make_example_section(clean.records, registry),))
        staged = hy.stage_outputs(clean.records, clean.run_meta, clean.probe_rows, coverage_md,
                                  report_md)
        gate = hy.make_gate(inventory_names=names, placeholders=values,
                            user_values=inputs.user_values,
                            canaries=inputs.placeholders.canaries, sanitizer=sanitizer)
        gate_ok, violations = hy.write_outputs(out_dir, staged, gate)
    except Exception as exc:  # noqa: BLE001 — 원문 미출력 · 관문 실패와 같게(격리 대상)
        say(f"[실패] 치환·관문 단계 예외 — {type(exc).__name__} · 산출물 미기록")
        gate_ok, violations = False, []
    if gate is None:
        gate = hy.make_gate(inventory_names=names, placeholders=values,
                            user_values=inputs.user_values, canaries=inputs.placeholders.canaries)
    if not gate_ok and on_gate_fail is not None:
        on_gate_fail(violations)
    text: str | None = None
    doc: dict[str, Any] = {}
    try:
        vocab = ss.build_vocab(inputs.scenarios, hy.build_code_vocab(registry))
        doc = ss.build_safe_summary(
            inputs.records, inputs.meta, run_id=inputs.run_id, env=inputs.env, summary=summary,
            vocab=vocab, registry=registry, directional=is_directional(inputs.meta),
            gate_passed=gate_ok, violations=violations,
            user_info_counts=dict(sanitizer.user_info_counts), user_info_by_turn=by_turn,
            evidence_missing=missing, probe_rows=len(inputs.probe_rows))
        text, safe_rules = ss.check_safe_summary(doc, vocab, gate)
    except Exception as exc:  # noqa: BLE001 — 원문 미출력 · 격리는 이미 끝났다
        safe_rules = {"build_error": 1}
        say(f"[위생] 안전 요약 생성 실패 — {type(exc).__name__}")
    target = out_dir / ss.SAFE_SUMMARY_FILE
    out_dir.mkdir(parents=True, exist_ok=True)
    if text is not None:
        tmp = out_dir / f".{ss.SAFE_SUMMARY_FILE}.tmp"
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, target)
        console = ss.render_console(doc, directional_head=DIRECTIONAL_HEAD)
    else:
        target.unlink(missing_ok=True)  # 지난 회차(재관문 전) 사본을 남기지 않는다
        console = [f"[위생] 로그 출력 억제 — 안전 요약이 관문에 걸렸다(규칙 {safe_rules}) · "
                   f"{ss.SAFE_SUMMARY_FILE} 미기록"]
    return Produced(gate_ok=gate_ok, violations=violations, missing=missing, summary=summary,
                    safe_written=text is not None, safe_rules=safe_rules, console=console,
                    gate=gate)


def _finish_lines(produced: Produced, meta: Mapping[str, Any], out_dir: Path,
                  registry: sc.RegistryInfo) -> int:
    """run 끝 줄(요약 · 관문 · 결과) → exit 코드."""
    from .rundir import display

    for line in produced.console:
        say(line)
    if produced.gate_ok:
        say(f"산출물     : {display(out_dir)} (관문 통과)")
    else:
        say(f"[실패] 누출 관문 — 위반 {len(produced.violations)}건 "
            f"{_gate_rules(produced.violations)} · 산출물 미기록(위치만 "
            f"{display(out_dir)}/leak_check.json)")
    failed = []
    if produced.summary["run_invalid"] or not meta.get("apm_observed_turns"):
        failed.append("run 무효 — APM 관측 턴 0(D-219 ③ · 관측 0은 측정 성립이 아니다)")
    if not produced.gate_ok:
        failed.append("누출 관문 실패")
    if produced.missing:
        failed.append(f"근거 필수 칸 누락 {len(produced.missing)}")
    if failed:
        say("결과       : 실패 — " + " · ".join(failed))
        return 1
    from .driver import safe_abort_reason

    aborted = safe_abort_reason(meta.get("aborted"), registry.source_ids)
    say("결과       : 완료" + (f" — 중단({aborted})" if aborted else ""))
    return 0


def _quarantine(inputs: RunInputs, *, base: Path, explicit: bool, out_dir: Path,
                context: Mapping[str, Any], scenario_text: str) -> str:
    """관문 실패 run 재료 → 격리 보관 · run 메타 `quarantine` 값(경로·내용 미출력)."""
    from . import rundir as rd

    payload = {
        "context.json": dict(context),
        "records.json": inputs.records,
        "meta.json": inputs.meta,
        "probe_rows.json": inputs.probe_rows,
        "placeholders.json": dataclasses.asdict(inputs.placeholders),
        "term_policy.json": sorted(inputs.policy.terms),
        "versions.json": inputs.versions,
    }
    try:
        rd.write_quarantine(base, out_dir, inputs.run_id, payload, scenario_text)
    except rd.QuarantineError as exc:
        say(f"[경고] 격리 보관 실패 — {exc}")
        return "none"
    except OSError as exc:
        say(f"[경고] 격리 보관 실패 — {type(exc).__name__}")
        return "none"
    kind = "explicit" if explicit else "default_local"
    say(f"격리 보관 : 있음({'지정 위치' if explicit else '기본 위치'}) · "
        f"{rd.QUARANTINE_DAYS}일 내 --purge · 재관문은 --regate {inputs.run_id}")
    return kind


def _close_run_dir(out_dir: Path, head: str, gate: Any, raw_meta: dict[str, Any], *,
                   scenario_ids: Sequence[str], gate_passed: bool) -> None:
    """run 끝 정리 — 실행 로그 2차 관문 → run 메타 → 매니페스트(마지막 · run 메타 포함)."""
    from . import rundir as rd
    from . import safe_summary as ss

    log_path = out_dir / rd.LOG_FILE
    if gate is not None and log_path.is_file() and not log_path.is_symlink():
        # 이 파일 내용 위반만 — 파일 무관 위반(가짜 값 충돌 등)은 산출물 관문이 판정했다(V-4)
        found = [v for v in gate.check({rd.LOG_FILE: log_path.read_text(encoding="utf-8")})
                 if v.get("file") == rd.LOG_FILE]
        if found:
            rd.suppress_log(out_dir, head, found)
            print(f"[위생] 실행 로그 억제 — 위반 {len(found)}건 {_gate_rules(found)}",
                  flush=True)
    ss.write_run_meta(out_dir, raw_meta, scenario_ids=scenario_ids)
    names = rd.write_manifest(out_dir, gate_passed=gate_passed)
    print(f"반출 목록 : {rd.MANIFEST_FILE} ({len(names)}개 — 이 목록 밖 파일은 반출하지 않는다)",
          flush=True)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class RunPrep:
    """`--run` 사전 점검 결과(run 디렉터리를 만들기 전 · `--bg` 부모도 같은 점검을 한다)."""

    started: datetime
    sha: str
    dirty: bool
    run_id: str
    scenarios: list[sc.Scenario]
    picked: list[sc.Scenario]
    heavy_off: list[sc.Scenario]
    to_drive: list[sc.Scenario]
    placeholders: Any
    policy: Any
    out_dir: Path
    q_base: Path
    explicit_q: bool
    user: str | None
    password: str | None
    sources: dict[str, str | None]


def _prepare_run(args: argparse.Namespace) -> RunPrep | int:
    """자동값 → 인자·시나리오·자리표·비밀번호·격리 경로 사전 점검 → `RunPrep` 또는 exit 코드.

    run id 는 **시작 시각**에 정한다 — `--run-id`(내부 · `--bg` 부모가 정해 넘김)가 있으면 그 값.
    """
    from . import driver
    from . import guard as gd
    from . import hygiene as hy
    from . import rundir as rd
    from .kit import ROOT_REFUSAL, is_root
    from .safe_summary import RUN_ID_RE

    if (args.env or "e1") == "e2" and is_root():  # G-3 (a) — 로컬 파일 탐색(홈)보다 먼저
        say(f"[중단] {ROOT_REFUSAL}")
        return 2
    sources = apply_run_defaults(args)
    if isinstance(sources, int):
        return sources
    started = datetime.now().astimezone()
    sha, dirty = code_version()
    given = getattr(args, "run_id", None)
    if given is not None and not RUN_ID_RE.fullmatch(given):
        say("[중단] --run-id 형식이 아니다(내부 인자 — --bg 가 넘긴다)")
        return 2
    run_id = given or make_run_id(args.env, sha, started.astimezone(driver.KST),
                                  smoke=bool(getattr(args, "smoke", False)))
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
    except (OSError, ValueError, yaml.YAMLError) as exc:  # 원문·경로 미출력
        say(f"[중단] 자리표·허용 목록 파일을 읽지 못했다 — {type(exc).__name__}")
        return 2
    heavy_off = [s for s in picked if s.heavy] if args.skip_heavy else []
    to_drive = [s for s in picked if s not in heavy_off]
    if not to_drive:
        say("[중단] 무거운 등급을 빼면 돌 시나리오가 없다(--skip-heavy)")
        return 1
    out_dir = Path(args.out) if args.out else RESULTS_ROOT / run_id
    if rd.pid_alive(rd.read_pid(out_dir)) is not False:
        say(RUN_DIR_BUSY)
        return 2
    explicit_q = bool(getattr(args, "quarantine_dir", None))
    q_base = Path(args.quarantine_dir) if explicit_q else rd.default_quarantine_base()
    try:
        rd.check_quarantine_base(q_base, out_dir)
    except rd.QuarantineError as exc:
        say(f"[중단] {exc} (--quarantine-dir)")
        return 2
    user = getattr(args, "user", None)
    password, blocked = login_password(user)
    if blocked:
        say(f"[중단] {blocked}")
        return 2
    return RunPrep(started=started, sha=sha, dirty=dirty, run_id=run_id, scenarios=scenarios,
                   picked=picked, heavy_off=heavy_off, to_drive=to_drive,
                   placeholders=placeholders, policy=policy, out_dir=out_dir, q_base=q_base,
                   explicit_q=explicit_q, user=user, password=password, sources=sources)


#: 살아 있는(또는 확인 불가한) `run.pid`가 있는 run 디렉터리 재사용 거부 문구(V-5 · 경로 미출력).
RUN_DIR_BUSY = ("[중단] run 디렉터리에 실행 중인 run 이 있다(run.pid — 살아 있거나 다른 계정 "
                "PID) — 다른 --out 을 주거나 그 run 이 끝난 뒤 다시 실행한다")
#: `--bg` 부모가 자식의 `run.pid`(또는 즉시 종료)를 기다리는 최대 초.
BG_WAIT_SEC = 5.0
#: `--bg` 자식 인자(JSON 목록)를 넘기는 환경변수 — 자식 명령줄은 `--run-id`뿐이라 프로세스
#: 목록(ps)에 로그인 계정 · 홈 경로가 보이지 않는다(plans/164 W2b 감사 L1).
BG_ARGV_ENV = "APM_BENCH_BG_ARGV"


def bg_child_env(base: Mapping[str, str]) -> dict[str, str]:
    """`--bg` 자식 env — 부모 env 그대로에서 비밀 값(`PASSWORD_ENV`)·내부 인자(`BG_ARGV_ENV`)만
    뺀다.

    허용 목록으로 줄이지 않는다 — 셸에서 export 한 설정(`LLM_PROVIDER`·`ORCHESTRATOR_PROVIDER` 등)이
    `--bg` 여부로 달라지면 같은 명령이 다른 LLM 평면(과금 게이트)을 쓸 수 있다
    (plans/164 W2b 교정 2).
    """
    return {k: v for k, v in base.items() if k not in (PASSWORD_ENV, BG_ARGV_ENV)}


def _spawn_bg(args: argparse.Namespace, prep: RunPrep,
              popen: Callable[..., Any] | None = None) -> int:
    """`--run`을 분리 프로세스로 띄운다(새 세션 · 표준 입출력 /dev/null) → run id·위치·확인 명령.

    분리 프로세스의 표준 출력을 `run.log`로 돌리지 않는다 — `run.log`는 `say` 직렬화본만 담는다
    (화이트리스트 원칙 · plans/164 W2a). 자식은 부모가 정한 run id 를 `--run-id`로, 나머지 인자를
    환경변수 `BG_ARGV_ENV`(JSON)로 받는다(ps 미노출) · env 는 부모 그대로(`bg_child_env` — 비밀만
    뺀다).
    부모는 `run.pid`의 PID 가 자식 PID 와 같아질 때까지(또는 자식 종료 · `BG_WAIT_SEC`) 기다린다.
    """
    import time

    from . import rundir as rd

    raw = getattr(args, "raw_argv", None)
    argv = [a for a in (sys.argv[1:] if raw is None else raw) if a != "--bg"]
    env = bg_child_env(os.environ)
    env[BG_ARGV_ENV] = json.dumps(argv, ensure_ascii=False)
    if prep.password:
        env[PASSWORD_ENV] = prep.password  # 부모는 이미 꺼냈다 — 자식에게만 넘긴다
    proc = (popen or subprocess.Popen)(
        [sys.executable, "-m", "scripts.apm_bench", "--run-id", prep.run_id],
        cwd=os.getcwd(), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + BG_WAIT_SEC
    while (time.monotonic() < deadline and proc.poll() is None
           and rd.read_pid(prep.out_dir) != proc.pid):
        time.sleep(0.1)
    code = proc.poll()
    say(f"분리 실행 : run_id {prep.run_id} · PID {proc.pid}")
    say(f"run 위치  : {rd.display(prep.out_dir)}")
    say("진행 확인 : python -m scripts.apm_bench --status "
        + (rd.display(prep.out_dir) if args.out else prep.run_id))  # 원경로 미출력
    if code not in (None, 0):
        say(f"[실패] 분리 실행이 바로 끝났다 — exit {code} · --bg 없이 다시 실행해 원인을 본다")
        return int(code)
    return 0


def cmd_run(args: argparse.Namespace, *, judge_fn: Callable[..., object] | None = None,
            driver_run: Callable[..., Any] | None = None,
            popen: Callable[..., Any] | None = None) -> int:
    """U층 드라이버 → 판정 → (P층) → 재판정 → 치환 → 근거 → 관문 → 산출물 · 안전 요약.

    run 디렉터리(`results/apm_bench/<run_id>/` · `--out`)에 실행 로그 `run.log`·화이트리스트 메타
    `run_meta.json`·`run.pid`·`manifest.sha256`를 함께 둔다(plans/164 W2a). 사전 점검(`_prepare_run`
    — 자동값 · 인자 · 시나리오 · 자리표 · 비밀번호 · 격리 경로)은 디렉터리를 만들기 전이다(점검
    실패가 빈 run 디렉터리를 남기지 않게). `--bg`면 점검 뒤 분리 프로세스로 넘긴다(W2b). 관문
    실패면 치환 전 재료를 저장소 밖 격리 경로에 보관한다(`rundir`).

    Args:
        args: CLI 인자
        judge_fn: 드라이버 턴 판정 훅(없으면 run 뒤 `judge_records` 일괄)
        driver_run: `driver.run` 대역(테스트)
        popen: `--bg` 분리 실행 `subprocess.Popen` 대역(테스트)
    """
    from . import hygiene as hy
    from . import rundir as rd
    from .kit import is_root, kit_meta

    bg = bool(getattr(args, "bg", False)) and not getattr(args, "run_id", None)
    if bg and getattr(args, "user", None) and not os.environ.get(PASSWORD_ENV):
        say(f"[중단] --bg 는 --user 비밀번호를 환경변수 {PASSWORD_ENV} 로만 받는다"
            "(대화형 입력 불가)")
        return 2
    prep = _prepare_run(args)
    if isinstance(prep, int):
        return prep
    if bg:
        return _spawn_bg(args, prep, popen)
    run_id, started, scenarios = prep.run_id, prep.started, prep.scenarios
    out_dir, placeholders, user = prep.out_dir, prep.placeholders, prep.user
    scen_path = Path(args.scenarios or SCENARIOS_PATH)
    scenario_bytes = scen_path.read_bytes()
    scenario_text = scenario_bytes.decode("utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    if not rd.claim_pid(out_dir, run_id, started.timestamp()):  # 점검 뒤 동시 시작 경합(V-5)
        say(RUN_DIR_BUSY)
        return 2
    copied = rd.inside(scen_path.resolve(), REPO_ROOT.resolve())
    if copied:
        (out_dir / rd.SCENARIO_COPY).write_bytes(scenario_bytes)
    ids = [s.id for s in scenarios]
    raw_meta: dict[str, Any] = {
        "run_id": run_id, "env": args.env, "llm": "body" if args.env == "e2" else args.llm,
        "status": "running", "exit_code": None, "started_at": started.isoformat(),
        "ended_at": None, "elapsed_sec": None, "sha": prep.sha, "dirty": prep.dirty,
        "scenarios": {"source": "explicit" if args.scenarios else "default",
                      "sha256": hashlib.sha256(scenario_bytes).hexdigest(),
                      "copied": copied, "copy": rd.SCENARIO_COPY if copied else None},
        "flags": {"probe": bool(args.probe), "skip_heavy": bool(args.skip_heavy),
                  "only": list(args.only) if args.only else None,
                  "smoke": bool(getattr(args, "smoke", False)), "limit": args.limit,
                  "debug_raw": bool(args.debug_raw), "user": bool(user),
                  "term_policy": prep.sources["term_policy"],
                  "placeholders": prep.sources["placeholders"],
                  "view_server_version": args.view_server_version,
                  "view_server_version_source": prep.sources["view_server_version"]},
        "bench_user": "login_user" if user else "anonymous",
        "gate": {"passed": None, "violations": 0, "rules": {}},
        "safe_summary_written": False, "quarantine": "none", "regated_at": None,
        "regate_sha": None, "kit": kit_meta(None, None, root=is_root()),
    }
    from . import safe_summary as ss

    ss.write_run_meta(out_dir, raw_meta, scenario_ids=ids)
    head = f"# apm_bench run.log — run_id {run_id} · 시작 {started.isoformat(timespec='seconds')}"
    user_values = run_user_values(user)
    state: dict[str, Any] = {"gate": None, "names": [], "status": "failed", "code": None}
    try:
        with rd.opened_log(out_dir) as log, _tee(log):
            say(head)
            code, status = _run_body(
                args, judge_fn=judge_fn, driver_run=driver_run, run_id=run_id, sha=prep.sha,
                dirty=prep.dirty, scenarios=scenarios, picked=prep.picked,
                heavy_off=prep.heavy_off, to_drive=prep.to_drive, placeholders=placeholders,
                policy=prep.policy, user=user, password=prep.password, user_values=user_values,
                out_dir=out_dir, q_base=prep.q_base, explicit_q=prep.explicit_q,
                scenario_text=scenario_text, raw_meta=raw_meta, state=state)
        state.update(code=code, status=status)
        return code
    except rd.QuarantineError as exc:  # run.log 가 심볼릭 링크 등(O_NOFOLLOW) — 고정 문구만
        say(f"[중단] {exc}")
        state.update(code=2, status="aborted")
        return 2
    except KeyboardInterrupt:
        state["status"] = "aborted"
        raise
    finally:
        gate = state["gate"] or hy.make_gate(
            inventory_names=state["names"], placeholders=placeholders.values,
            user_values=user_values, canaries=placeholders.canaries)
        ended = datetime.now().astimezone()
        raw_meta.update(status=state["status"], exit_code=state["code"],
                        ended_at=ended.isoformat(),
                        elapsed_sec=round((ended - started).total_seconds(), 1))
        _close_run_dir(out_dir, head, gate, raw_meta, scenario_ids=ids,
                       gate_passed=raw_meta["gate"]["passed"] is True)
        rd.remove_pid(out_dir)


def _run_body(args: argparse.Namespace, *, judge_fn: Callable[..., object] | None,
              driver_run: Callable[..., Any] | None, run_id: str, sha: str, dirty: bool,
              scenarios: list[sc.Scenario], picked: list[sc.Scenario],
              heavy_off: list[sc.Scenario], to_drive: list[sc.Scenario], placeholders: Any,
              policy: Any, user: str | None, password: str | None,
              user_values: dict[str, str | None], out_dir: Path, q_base: Path,
              explicit_q: bool, scenario_text: str, raw_meta: dict[str, Any],
              state: dict[str, Any]) -> tuple[int, str]:
    """`cmd_run` 본문(run 디렉터리 · 실행 로그 tee 안) → (exit 코드, run 메타 status).

    예외 줄은 예외 클래스 이름 · 고정 문구 · 오류 코드만 찍는다(원문 = 운영 이름일 수 있다).
    """
    from . import driver, kit
    from . import hygiene as hy
    from . import judge as jd
    from . import probe as pb
    from .fingerprints import apm_section_evidence, fewshot_fingerprints
    from .rundir import display

    registry = sc.load_registry_info()
    probe_rows: list[dict[str, Any]] = []
    probe_fn = make_probe_fn(registry, placeholders.values, probe_rows) if args.probe else None
    login: dict[str, Any] = {"user_id": user, "password": password} if user else {}

    def check_inventory(names: list[str]) -> None:
        """인벤토리 수집 직후(턴 송신 전) — 사용자 정보가 인벤토리 이름·토큰이나 코드 어휘·산출물
        키 이름과 겹치면 E2 는 멈추고 E1 은 경고만(E1 인벤토리는 합성 목 이름 · plans/164 W1)."""
        from . import safe_summary as ss

        state["names"] = list(names)
        safe = ss.build_vocab(scenarios, hy.build_code_vocab(registry))
        hits = ((hy.CONFLICT_INVENTORY, hy.user_info_conflicts(user_values, names, policy)),
                (hy.CONFLICT_VOCAB,
                 hy.user_info_vocab_conflicts(user_values, safe.literals | safe.fields)))
        kinds = sorted({kind for _, found in hits for kind in found})
        if not kinds:
            return
        error = hy.UserInfoConflictError(kinds, [label for label, found in hits if found])
        if args.env == "e2":
            raise error
        say(f"      [경고] {error} — E1 목 인벤토리라 계속한다")

    kit_state: dict[str, Any] = {"report": None, "var": None, "inv": None}

    def check_kit(names: list[str], name_sources: dict[str, set[str]],
                  errors: Sequence[str] = ()) -> None:
        """인벤토리 수집 직후(턴 송신 전) — 자리표 실재 · 소스 대응 범위(plans/164 §4.2 (d) ·
        값·소스 id 는 출력하지 않는다 · 멈추지 않는다). 실패한 인벤토리 도구가 내는 종류는
        「검사 불가」(V-1)."""
        kit_state["inv"] = (list(names), name_sources, list(errors))
        report = kit.check_kit(to_drive, placeholders, names, name_sources, errors)
        kit_state["report"] = report
        for line in kit.kit_lines(report):
            say(line)

    def record_kit() -> None:
        raw_meta["kit"] = kit.kit_meta(kit_state["report"], kit_state["var"],
                                       root=kit.is_root())

    var_before = kit.var_snapshot()
    try:
        records, meta = (driver_run or driver.run)(
            to_drive, env=args.env, placeholders=placeholders,
            out_dir=Path(args.debug_raw) if args.debug_raw else None,
            view_server_version=args.view_server_version, judge_fn=judge_fn,
            probe_fn=probe_fn, llm=args.llm, mlx_url=args.mlx_url, mlx_model=args.mlx_model,
            say=say, inventory_check=check_inventory, kit_check=check_kit, **login)
    except driver.LLMBlockedError as exc:  # 사유 원문 미출력 — 진단은 `scripts.bench --preflight`
        say(f"[중단] LLM 사전 점검 실패({type(exc).__name__} — --llm mlx 점검 · E2 과금 평면) · "
            "진단: python -m scripts.bench --preflight")
        return 2, "aborted"
    except hy.UserInfoConflictError as exc:
        say(f"[중단] {exc} — 턴 송신 전에 멈췄다(값은 출력하지 않는다)")  # 종류·고정 문구만
        return 2, "aborted"
    except driver.RunAbortError as exc:
        say(f"[중단] {exc.phrase}" + (f" — 오류 코드 {', '.join(exc.codes)}" if exc.codes else ""))
        return 1, "aborted"
    except (ValueError, RuntimeError) as exc:
        say(f"[중단] 드라이버 실패 — {type(exc).__name__}")
        return 1, "aborted"
    finally:  # 게이트웨이를 내린 뒤 — var/ 파일 목록 비교(이름 없이 건수만 · W4-1)
        kit_state["var"] = kit.var_changes(var_before, kit.var_snapshot())
        for line in kit.var_lines(kit_state["var"]):
            say(line)
        record_kit()
    state["names"] = list(meta.get("inventory_names") or [])
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
        say(f"원본       : {display(raw)} (E1 디버그 — 합성 이름뿐 · records.jsonl · meta.json · "
            "로그)")

    meta["gateway_var"] = dict(kit_state["var"] or {})  # 안전 요약 · run.json(건수만 · W4-1)
    report = kit_state["report"]
    if report is not None:
        # run 메타의 수집 실패 사유가 훅이 본 것보다 많으면(정본 = 드라이버 메타) 다시 판정한다
        names, name_sources, seen = kit_state["inv"]
        errors = [*seen, *((meta.get("inventory") or {}).get("errors") or [])]
        if kit.unchecked_kinds(errors) - kit.unchecked_kinds(seen):
            report = kit.check_kit(to_drive, placeholders, names, name_sources, errors)
            kit_state["report"] = report
            for line in kit.kit_lines(report)[:1]:
                say(line + " (수집 실패 반영 재판정)")
            record_kit()
        # G-4 (a) — 「없음」 자리표를 쓴 일반 턴은 판정 불가로 분리(「검사 불가」·「여럿」은 남긴다)
        kit.mark_absent(records, report.names(kit.ABSENT))
    # 판정 → P층 대조 → 재판정(빈 결과 판별이 `empty_result`를 바꾼다)
    jd.judge_records(picked, records, registry)
    if probe_rows:
        pb.attach(records, probe_rows)
        jd.judge_records(picked, records, registry)

    versions = {"code_sha": sha, "fingerprints": fewshot_fingerprints(registry),
                "apm_section": apm_section_evidence()}
    inputs = RunInputs(run_id=run_id, env=args.env, sha=sha, dirty=dirty,
                       scenarios=list(scenarios), records=records, meta=meta,
                       placeholders=placeholders, policy=policy, user_values=user_values,
                       probe_rows=probe_rows, versions=versions, cap_map=Path(args.cap_map))

    def quarantine(violations: Sequence[Mapping[str, Any]]) -> None:
        """관문 실패 — 안전 요약을 만들기 전에 격리 보관한다(V-5)."""
        raw_meta["gate"] = {"passed": False, "violations": len(violations),
                            "rules": _gate_rules(violations)}
        context = {"run_id": run_id, "env": args.env, "sha": sha, "dirty": dirty, "user": user,
                   "cap_map": str(args.cap_map), "run_meta": dict(raw_meta)}
        raw_meta["quarantine"] = _quarantine(inputs, base=q_base, explicit=explicit_q,
                                             out_dir=out_dir, context=context,
                                             scenario_text=scenario_text)

    produced = produce_outputs(inputs, out_dir, registry, on_gate_fail=quarantine)
    state["gate"] = produced.gate
    raw_meta["gate"] = {"passed": produced.gate_ok, "violations": len(produced.violations),
                        "rules": _gate_rules(produced.violations)}
    raw_meta["safe_summary_written"] = produced.safe_written
    code = _finish_lines(produced, meta, out_dir, registry)
    return code, "done" if code == 0 else "failed"


def _resolve_regate(target: str, args: argparse.Namespace) -> tuple[Path, Path] | None:
    """`--regate` 인자(run id · 격리 디렉터리 · run 디렉터리) → (격리 디렉터리, run 디렉터리)."""
    from . import rundir as rd
    from .safe_summary import RUN_ID_RE, RUN_META_FILE

    base = (Path(args.quarantine_dir) if getattr(args, "quarantine_dir", None)
            else rd.default_quarantine_base())
    if RUN_ID_RE.fullmatch(target):
        return base / target, Path(args.out) if args.out else RESULTS_ROOT / target
    path = Path(target)
    if (path / "context.json").is_file():
        try:
            run_id = json.loads((path / "context.json").read_text(encoding="utf-8"))["run_id"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
            return None
        return path, Path(args.out) if args.out else RESULTS_ROOT / run_id
    if (path / RUN_META_FILE).is_file():
        try:
            run_id = json.loads((path / RUN_META_FILE).read_text(encoding="utf-8"))["run_id"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
            return None
        return base / run_id, path
    return None


def cmd_regate(args: argparse.Namespace) -> int:
    """격리본 → 치환 → 근거 → 관문 → 산출물 재기록(실행 없음 · plans/164 W2a).

    위생 규칙을 고친 뒤 같은 run 재료로 산출물만 다시 만든다. 격리 디렉터리가 현재 계정 소유 ·
    권한 700 이 아니면 거부한다. run 디렉터리의 `summary_safe.json`을 다시 만들고 `run_meta.json`에
    `regated_at`·`regate_sha`를 남기며 `run.log`에 재관문 줄을 덧붙인다.
    """
    from . import driver
    from . import hygiene as hy
    from . import rundir as rd
    from . import safe_summary as ss

    resolved = _resolve_regate(str(args.regate), args)
    if resolved is None:
        say("[중단] --regate 대상을 찾지 못했다(run id · 격리 디렉터리 · run 디렉터리)")
        return 2
    q_dir, out_dir = resolved
    try:
        rd.check_quarantine_base(q_dir.parent, out_dir)
        data, scenario_text = rd.read_quarantine(q_dir)
    except rd.QuarantineError as exc:
        say(f"[중단] {exc}")
        return 2
    import yaml

    context = data["context.json"]
    try:
        scenarios, _ = sc.parse_scenarios(yaml.safe_load(scenario_text))
        holder = dict(data["placeholders.json"])
        holder["canaries"] = tuple(holder.get("canaries") or ())
        placeholders = driver.Placeholders(**holder)
        policy = hy.TermPolicy(frozenset(data["term_policy.json"]))
    except (TypeError, ValueError, yaml.YAMLError) as exc:
        say(f"[중단] 격리본 재료를 해석하지 못했다 — {type(exc).__name__}")
        return 2
    run_id = str(context["run_id"])
    sha_now, _ = code_version()
    registry = sc.load_registry_info()
    inputs = RunInputs(run_id=run_id, env=str(context["env"]), sha=str(context["sha"]),
                       dirty=bool(context["dirty"]), scenarios=scenarios,
                       records=data["records.json"], meta=data["meta.json"],
                       placeholders=placeholders, policy=policy,
                       user_values=run_user_values(context.get("user")),
                       probe_rows=data["probe_rows.json"], versions=data["versions.json"],
                       cap_map=Path(args.cap_map))
    out_dir.mkdir(parents=True, exist_ok=True)
    ids = [s.id for s in scenarios]
    try:
        raw_meta: dict[str, Any] = json.loads(
            (out_dir / ss.RUN_META_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw_meta = dict(context.get("run_meta") or {})
    head = f"# apm_bench run.log — run_id {run_id} · 재관문"
    with contextlib.ExitStack() as stack:
        try:  # run.log 덧붙이기 — 심볼릭 링크면 거부(O_NOFOLLOW)
            log = stack.enter_context(rd.opened_log(out_dir, append=True))
        except rd.QuarantineError as exc:
            say(f"[중단] {exc}")
            return 2
        stack.enter_context(_tee(log))
        say(f"# regate — {_now_iso()} · sha {sha_now}")
        produced = produce_outputs(inputs, out_dir, registry)
        code = _finish_lines(produced, inputs.meta, out_dir, registry)
        if produced.gate_ok:
            say(f"반출 확인 후 격리본을 지운다: python -m scripts.apm_bench --purge {run_id}")
    raw_meta.update(regated_at=_now_iso(), regate_sha=sha_now, safe_summary_written=(
        produced.safe_written), gate={"passed": produced.gate_ok,
                                      "violations": len(produced.violations),
                                      "rules": _gate_rules(produced.violations)})
    _close_run_dir(out_dir, head, produced.gate, raw_meta, scenario_ids=ids,
                   gate_passed=produced.gate_ok)
    return code


def cmd_purge(args: argparse.Namespace) -> int:
    """격리본 삭제 — run id 지정이면 그것만, 없으면 보존 기한(14일) 지난 것 전부. 남은 목록은
    run id · 경과 일만 찍는다(경로 미출력)."""
    from . import rundir as rd
    from .safe_summary import RUN_ID_RE

    run_id = args.purge or None
    if run_id is not None and not RUN_ID_RE.fullmatch(run_id):
        say("[중단] --purge 인자는 run id 형식이다(YYYYmmdd-HHMMSS-<env>-<SHA 7자>[-smoke])")
        return 2
    base = (Path(args.quarantine_dir) if getattr(args, "quarantine_dir", None)
            else rd.default_quarantine_base())
    try:
        removed, kept, other = rd.purge(base, run_id)
    except rd.QuarantineError as exc:
        say(f"[중단] {exc} (--quarantine-dir)")
        return 2
    say(f"격리본 삭제: {len(removed)}건" + (f" — {', '.join(removed)}" if removed else ""))
    for kept_id, age in kept:
        say(f"남음       : {kept_id} · {age}일 경과")
    if other:
        say(f"격리본 아님(건드리지 않음): {other}건")
    if run_id is not None and not removed:
        say("[실패] 지정한 격리본이 없다(현재 계정 소유만 지운다)")
        return 1
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
    from .rundir import display, resolve_run

    # run id(→ results/apm_bench/<run_id>) · 경로 · results/apm_bench 아래 이름(plans/164 W2b)
    run_a, run_b = (resolve_run(p, RESULTS_ROOT) or Path(p) for p in args.compare)
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
        say(f"산출물     : {display(out)}/compare.md")  # 원경로(홈) 미출력
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
    from .rundir import display, resolve_run

    run_dir = resolve_run(args.replay, RESULTS_ROOT) or Path(args.replay)
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
        say(f"산출물     : {display(out)}/replay.md")  # 원경로(홈) 미출력
    return 0


def cmd_audit_digest(args: argparse.Namespace) -> int:
    """본체 감사 로그 → 진단표(plans/164 §4.0) — 기대 순서는 `cmd_run`·`driver.run`과 같다.

    인자가 없거나 run id 하나면 run id 모드(`_audit_digest_run` — 창·로그·시나리오·옵션 자동),
    아니면 종전처럼 감사 로그 경로 목록(`--since`·`--until`·`--out` 필수).
    """
    from .rundir import LEGACY_RUN_ID_RE
    from .safe_summary import RUN_ID_RE

    targets = list(args.audit_digest or [])
    if not targets or (len(targets) == 1 and (RUN_ID_RE.fullmatch(targets[0])
                                               or LEGACY_RUN_ID_RE.fullmatch(targets[0]))):
        return _audit_digest_run(args, targets[0] if targets else None)
    from . import audit_digest as ad

    args.env = args.env or "e1"
    since, until = ad.parse_when(args.since or ""), ad.parse_when(args.until or "")
    if since is None or until is None:
        say("[중단] --since·--until 은 오프셋이 있는 ISO-8601 이어야 한다"
            "(예 2026-10-13T09:00+09:00)")
        return 2
    if since > until:
        say(f"[중단] {ad.WINDOW_REVERSED}")
        return 2
    if not args.out:
        say("[중단] --audit-digest 는 --out 이 필수다")
        return 2
    paths = [Path(p) for p in targets]
    absent = [p for p in paths if not p.is_file()]
    if absent:
        say(f"[중단] 감사 로그 파일이 없다: {len(absent)}개")
        return 2
    return _digest(args, paths, since, until, Path(args.out), {})


def _digest(args: argparse.Namespace, paths: Sequence[Path], since: datetime, until: datetime,
            out: Path, extra_head: Mapping[str, Any]) -> int:
    """기대 시나리오열(`--env`·`--only`·`--limit`·`--skip-heavy`·`--scenarios`) → 진단표 기록."""
    from . import audit_digest as ad
    from . import guard as gd

    loaded = _load(args)
    if loaded is None:
        return 2
    scenarios, structure = loaded
    if structure:
        _report_issues(structure)
        say("[중단] 구조 위반이 있다 — --dry-run 으로 먼저 고친다")
        return 1
    picked, unknown = select_scenarios(scenarios, args.only, args.limit)
    if unknown:
        say(f"[중단] 모르는 시나리오 id: {', '.join(unknown)}")
        return 2
    heavy_off = [s for s in picked if s.heavy] if args.skip_heavy else []
    plan = gd.schedule([s for s in picked if s not in heavy_off], env=args.env)
    expected = [*plan.light, *plan.heavy]
    skipped = [s for s in picked if s.id in plan.skipped]
    head = {"env": args.env, "scenarios": len(picked), "light": len(plan.light),
            "heavy": len(plan.heavy), "heavy_off": len(heavy_off), "skipped": len(skipped),
            "skipped_turns": sum(len(s.turns) for s in skipped),
            "turns": sum(len(s.turns) for s in expected), **extra_head}
    view_ids = frozenset(sc.load_registry_info().views)
    threads, counts, dropped = ad.read_threads(paths, since=since, until=until,
                                               user=args.bench_user or "anonymous",
                                               view_ids=view_ids)
    digest = ad.build_digest(threads, counts, dropped, expected, head)
    return ad.write_digest(digest, ad.vocabulary(expected, view_ids), out, say)


#: `--audit-digest <run_id>` 창 여유(run 메타 시작·끝 앞뒤).
AUDIT_MARGIN = timedelta(minutes=5)
#: 시나리오 id 변경 예(run SHA 미상 경고 — HEAD 시나리오와 1회차 시나리오가 다를 수 있다).
SCENARIO_RENAME_NOTE = "205d904 에서 APM-SRC-common → APM-SRC-cm-prd"


def _legacy_start(run_id: str) -> datetime:
    """메타 없는 run 의 시작 시각 — 새 형식은 KST 스탬프, 구 형식은 실행 머신 로컬 시각."""
    from .driver import KST

    if "_" in run_id:
        fmt = "%Y%m%d_%H%M%S" if len(run_id) == 15 else "%Y%m%d_%H%M"
        return datetime.strptime(run_id, fmt).astimezone()
    return datetime.strptime(run_id[:15], "%Y%m%d-%H%M%S").replace(tzinfo=KST)


def _elapsed_from_logs(run_id: str, run_dir: Path) -> tuple[float, str] | None:
    """실행 로그 `run_<run_id>.log`(run 디렉터리 · 현재 디렉터리 · `~/apm_bench_out/`)의
    「소요 N초」 → (N, 출처 표기)."""
    from .audit_digest import elapsed_from_log

    name = f"run_{run_id}.log"
    for where, base in (("run 디렉터리", run_dir), ("현재 디렉터리", Path.cwd()),
                        ("~/apm_bench_out", Path.home() / "apm_bench_out")):
        path = base / name
        if not path.is_file():
            continue
        try:
            value = elapsed_from_log(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if value is not None:
            return value, f"{where}/{name}" if where != "run 디렉터리" else name
    return None


def _audit_digest_run(args: argparse.Namespace, run_id: str | None) -> int:
    """`--audit-digest [<run_id>]` — run 메타로 창·옵션·시나리오를, 창 날짜로 감사 로그를 채운다.

    명시 인자(`--since`·`--until`·`--env`·`--skip-heavy`·`--only`·`--limit`·`--scenarios`·
    `--bench-user`·`--out`)가 늘 우선이다. 메타 없는 run(구 방식 1회차)은 run id 시각 + 「소요
    N초」(`--duration` 또는 실행 로그)로 창을 추정하고 진단표 머리에 `window_estimated: true`를
    싣는다 — 둘 다 없으면 창을 넓히지 않고 exit 2.
    """
    import json as _json

    from . import audit_digest as ad
    from . import rundir as rd
    from . import safe_summary as ss

    if run_id is None:
        latest = rd.latest_run(RESULTS_ROOT)
        if latest is None:
            say("[중단] results/apm_bench 아래 run 이 없다 — run id 또는 감사 로그 경로를 준다")
            return 2
        run_dir, run_id = latest, latest.name
        say(f"자동: run — 최근 {run_id}")
    else:
        run_dir = RESULTS_ROOT / run_id
    scen_explicit = args.scenarios is not None  # 기본 경로와 같아도 명시면 우선(V-2)
    raw: dict[str, Any] | None = None
    try:
        loaded_raw = _json.loads((run_dir / ss.RUN_META_FILE).read_text(encoding="utf-8"))
        raw = loaded_raw if isinstance(loaded_raw, dict) else None
    except (OSError, ValueError):
        raw = None
    copy = run_dir / rd.SCENARIO_COPY
    if raw is not None and not scen_explicit and copy.is_file() and not copy.is_symlink():
        args.scenarios = str(copy)
        say(f"자동: 시나리오 — run 디렉터리 사본({rd.SCENARIO_COPY})")
    loaded = _load(args)
    if loaded is None:
        return 2
    ids = [s.id for s in loaded[0]]
    since_arg = ad.parse_when(args.since) if args.since else None
    until_arg = ad.parse_when(args.until) if args.until else None
    if (args.since and since_arg is None) or (args.until and until_arg is None):
        say("[중단] --since·--until 은 오프셋이 있는 ISO-8601 이어야 한다"
            "(예 2026-10-13T09:00+09:00)")
        return 2
    duration = getattr(args, "duration", None)
    estimated = False
    if raw is not None:
        meta = ss.run_meta_doc(raw, scenario_ids=ids)
        if not scen_explicit and not copy.is_file():
            scen_bytes = Path(args.scenarios or SCENARIOS_PATH).read_bytes()
            current = hashlib.sha256(scen_bytes).hexdigest()
            if meta["scenarios"]["sha256"] not in (None, current):
                say("[경고] run 시나리오 사본이 없고 지금 시나리오 파일과 해시가 다르다 — "
                    "그 run 의 시나리오를 --scenarios 로 준다")
        if args.env is None:
            if meta["env"] is None:
                say("[중단] run 메타에 env 가 없다 — --env 를 준다")
                return 2
            args.env = meta["env"]
            say(f"자동: --env {args.env} (run 메타)")
        if args.skip_heavy is None:
            args.skip_heavy = meta["flags"]["skip_heavy"]
            say(f"자동: --skip-heavy {'켬' if args.skip_heavy else '끔'} (run 메타)")
        if args.only is None and args.limit is None:
            args.only, args.limit = meta["flags"]["only"], meta["flags"]["limit"]
            raw_only = (raw.get("flags") or {}).get("only")
            if isinstance(raw_only, list) and len(raw_only) != len(args.only or []):
                say("[경고] run 메타 --only 의 일부 id 가 지금 시나리오 파일에 없다")
            if args.only or args.limit is not None:
                say(f"자동: 시나리오 선택 — run 메타(--only {len(args.only or [])}개 · "
                    f"--limit {args.limit})")
        if args.bench_user is None and meta["bench_user"] == "login_user":
            say("[중단] --user 계정으로 돈 run 이다 — --bench-user 로 그 계정을 준다")
            return 2
        started = ad.parse_when(meta["started_at"] or "")
        ended = ad.parse_when(meta["ended_at"] or "")
        if started is None:
            say("[중단] run 메타에 시작 시각이 없다 — --since·--until 을 준다")
            return 2
        if ended is None and until_arg is None:
            if duration is not None:
                ended, estimated = started + timedelta(seconds=duration), True
            elif rd.pid_state(run_dir, run_id=meta["run_id"],
                              started_at=meta["started_at"])[0] == "alive":
                ended = datetime.now().astimezone()
                say("[경고] run 이 아직 돈다 — 창 끝 = 지금")
            else:
                say("[중단] run 메타에 끝 시각이 없다(강제 종료 추정) — --until 또는 "
                    "--duration <초> 를 준다")
                return 2
    else:
        started = _legacy_start(run_id)
        say(f"자동: 시작 시각 — run id({'로컬' if '_' in run_id else 'KST'})")
        if args.env is None:
            args.env = run_id.split("-")[2] if "-" in run_id else "e2"
            say(f"자동: --env {args.env} (run id"
                + ("" if "-" in run_id else " — 구 방식 run 은 내부망 1회차 형식") + ")")
        args.skip_heavy = bool(args.skip_heavy)
        ended = None
        if until_arg is None:
            if duration is None:
                found = _elapsed_from_logs(run_id, run_dir)
                if found is not None:
                    duration = found[0]
                    say(f"자동: 소요 — 실행 로그 {found[1]}")
            if duration is None:
                say("[중단] run 메타가 없어 끝 시각을 모른다 — --duration <초>(실행 로그 "
                    f"「소요 N초」) 또는 실행 로그 run_{run_id}.log 를 run 디렉터리 · "
                    "현재 디렉터리 · ~/apm_bench_out 에 둔다(창을 임의로 넓히지 않는다)")
                return 2
            ended, estimated = started + timedelta(seconds=duration), True
        if not scen_explicit:
            say("[경고] run SHA 미상 — HEAD 시나리오 사용")
            say(f"[경고] 시나리오 id 가 바뀌었을 수 있다(예 {SCENARIO_RENAME_NOTE}) — "
                "그 run SHA 의 사본은 --scenarios 로 준다")
    since = since_arg or started - AUDIT_MARGIN
    until = until_arg or (ended or started) + AUDIT_MARGIN
    if since > until:  # 로그 없음으로 오안내하지 않는다(V-4)
        say(f"[중단] {ad.WINDOW_REVERSED}")
        return 2
    say(f"자동: 창 — {since.isoformat(timespec='seconds')} ~ "
        f"{until.isoformat(timespec='seconds')}" + (" (끝 추정)" if estimated else ""))
    paths = ad.audit_log_files(since, until, ad.AUDIT_LOG_DIR)
    if not paths:
        days = ", ".join(d.isoformat() for d in ad.local_dates(since, until))
        say(f"[중단] 감사 로그가 없다 — logs/audit-<날짜>*.jsonl(날짜 {days}) · 다른 위치면 경로를 "
            "직접 준다(--since·--until·--out)")
        return 2
    say(f"자동: 감사 로그 — {len(paths)}개({', '.join(p.name for p in paths)})")
    if args.out:
        out = Path(args.out)
    else:
        out = run_dir if run_dir.is_dir() else RESULTS_ROOT / f"{run_id}_audit"
        say(f"자동: 출력 — {rd.display(out)}/audit_digest.json")
    return _digest(args, paths, since, until, out, {"window_estimated": estimated})


def cmd_status(args: argparse.Namespace) -> int:
    """run 상태 — run 메타 상태 · `run.pid` 생존 · 경과 · `run.log` 마지막 줄.

    생존은 `rundir.pid_state`(다른 계정 PID = 「확인 불가」 · run id·시작 시각이 다른 기록 = 낡은
    파일) · 마지막 줄은 `rundir.last_log_line`(직렬화 문형 화이트리스트 틀로 다시 쓴 줄만 · 그 밖은
    「(검증 불가)」 · plans/164 W2b 감사 H1).
    """
    from . import rundir as rd
    from . import safe_summary as ss

    target = args.status or None
    run_dir = (rd.resolve_run(target, RESULTS_ROOT) if target
               else rd.latest_run(RESULTS_ROOT))
    if run_dir is None or not run_dir.is_dir():
        say("[중단] run 을 찾지 못했다 — run id(results/apm_bench/<run_id>) 또는 run 디렉터리")
        return 2
    if target is None:
        say(f"자동: run — 최근 {run_dir.name}")
    meta = ss.read_run_meta(run_dir, scenario_ids=())
    state, pid = rd.pid_state(run_dir, run_id=(meta or {}).get("run_id"),
                              started_at=(meta or {}).get("started_at"))
    say(f"run        : {(meta or {}).get('run_id') or run_dir.name} · {rd.display(run_dir)}")
    if meta is None:
        say("상태       : run 메타 없음(W2a 전 산출 또는 시작 전)"
            + (f" · PID {pid} 살아 있음" if state == "alive" else ""))
    else:
        status = meta["status"]
        if status == "running":
            note = {
                "alive": f"실행 중(PID {pid})",
                "unknown": f"확인 불가(PID {pid} — 다른 계정 프로세스 · 낡은 run.pid 의 PID "
                           "재사용 추정)",
                "stale": "낡은 run.pid(다른 run 의 기록) — 강제 종료·크래시 추정(run 메타 미갱신)",
            }.get(state, "프로세스 없음 — 강제 종료·크래시 추정(run 메타 미갱신)")
        else:
            note = f"exit {meta['exit_code']}"
        say(f"상태       : {status} · {note}")
        started = meta["started_at"]
        if status == "running" and started:
            elapsed: Any = round((datetime.now().astimezone()
                                  - datetime.fromisoformat(started)).total_seconds(), 1)
        else:
            elapsed = meta["elapsed_sec"]
        say(f"시작       : {started} · 경과 {elapsed}초")
        flags = meta["flags"]
        say(f"옵션       : env {meta['env']} · llm {meta['llm']} · skip_heavy "
            f"{flags['skip_heavy']} · probe {flags['probe']} · smoke {flags['smoke']}"
            + (f" · only {len(flags['only'])}개" if flags["only"] else ""))
        if meta["gate"]["passed"] is not None:
            say(f"관문       : {'통과' if meta['gate']['passed'] else '실패'} · 위반 "
                f"{meta['gate']['violations']}")
    say(f"마지막 줄  : {rd.last_log_line(run_dir, _status_ids(run_dir)) or '없음'}")
    return 0


def _status_ids(run_dir: Path) -> frozenset[str]:
    """`--status` 마지막 줄 대조용 시나리오 id — run 사본(일반 파일일 때) 또는 기본 시나리오 파일.
    못 읽으면 빈 집합(시나리오 id 줄은 「검증 불가」로 — 닫힌 쪽 실패)."""
    import yaml

    from . import rundir as rd

    copy = run_dir / rd.SCENARIO_COPY
    path = copy if copy.is_file() and not copy.is_symlink() else SCENARIOS_PATH
    try:
        return frozenset(s.id for s in sc.load_scenarios(path))
    except (OSError, ValueError, yaml.YAMLError):  # ScenarioError ⊂ ValueError
        return frozenset()


#: 모드 인자 dest → 처리 함수(첫 번째가 기본 모드).
MODES: dict[str, Callable[[argparse.Namespace], int]] = {
    "dry_run": cmd_dry_run,
    "coverage": cmd_coverage,
    "run": cmd_run,
    "fewshot_check": cmd_fewshot_check,
    "compare": cmd_compare,
    "replay": cmd_replay,
    "audit_digest": cmd_audit_digest,
    "regate": cmd_regate,
    "purge": cmd_purge,
    "status": cmd_status,
    "init_local": cmd_init_local,
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
    mode.add_argument("--audit-digest", nargs="*", default=None, metavar="RUN_ID|LOG",
                      help="본체 감사 로그 → 화이트리스트 진단표(plans/164 W0a) — 생략 = 최근 "
                           "run · run id 하나 = 그 run(창·로그·옵션 자동) · 그 밖 = 감사 로그 경로")
    mode.add_argument("--regate", default=None, metavar="RUN",
                      help="격리본 재관문(run id · 격리 디렉터리 · run 디렉터리 — 실행 없음)")
    mode.add_argument("--purge", nargs="?", const="", default=None, metavar="RUN_ID",
                      help="격리본 삭제(run id · 생략 = 14일 지난 것 전부)")
    mode.add_argument("--status", nargs="?", const="", default=None, metavar="RUN_ID",
                      help="run 상태(run id · run 디렉터리 · 생략 = 최근 run)")
    mode.add_argument("--init-local", action="store_true",
                      help="E2 로컬 파일 양식 3개 → testdata/apm_bench/local/(있으면 그대로 둠)")
    parser.add_argument("--scenarios", default=None,  # None = 기본 경로(명시 여부 판별용)
                        help=f"시나리오 YAML(기본 {SCENARIOS_PATH.relative_to(REPO_ROOT)})")
    parser.add_argument(
        "--cap-map", default=str(CAPABILITY_MAP_PATH), help="대응표(표 A·B) markdown"
    )
    parser.add_argument("--out", default=None,
                        help="출력 디렉터리(--coverage · --run(기본 results/apm_bench/<run_id>) · "
                             "--compare · --replay · --audit-digest(로그 경로 모드는 필수 · run id "
                             "모드 기본 = run 디렉터리))")
    parser.add_argument("--env", choices=("e1", "e2"), default=None,
                        help="--run 환경(기본 e1 · --audit-digest run id 모드는 run 메타 값)")
    parser.add_argument("--llm", choices=LLM_CHOICES, default="fake",
                        help="--run E1 LLM(fake | mlx — mlx 는 --only 대표 턴과 함께)")
    parser.add_argument("--mlx-url", default=MLX_DEFAULT_URL, help="--llm mlx 서버 /v1 주소")
    parser.add_argument("--mlx-model", default=MLX_DEFAULT_MODEL, help="--llm mlx 모델 ID")
    parser.add_argument("--probe", action=argparse.BooleanOptionalAction, default=None,
                        help="--run P층(빈 결과 판별 · 허용값 탐침 · 기본 E2 켬 · E1 끔)")
    parser.add_argument("--placeholders", default=None,
                        help="--run 자리표 파일(E1 기본 testdata · E2 기본 "
                             f"{local_label(LOCAL_PLACEHOLDERS)})")
    parser.add_argument("--only", nargs="+", default=None, metavar="ID", help="--run 시나리오 id")
    parser.add_argument("--smoke", action="store_true",
                        help=f"--run 스모크 목록 {len(SMOKE_IDS)}개(plans/164 §5 · --only 와 함께 "
                             "못 씀 · run id 끝 -smoke)")
    parser.add_argument("--limit", type=int, default=None, help="--run 앞 N개 시나리오")
    parser.add_argument("--debug-raw", default=None, help="--run E1 원본 디렉터리")
    parser.add_argument("--view-server-version", default=None,
                        help="--run E2 뷰 서버 버전(운영자 입력 · 5.6.2.18 이상 · 기본 "
                             f"{local_label(LOCAL_BENCH)} view_server_version)")
    parser.add_argument("--skip-heavy", action=argparse.BooleanOptionalAction, default=None,
                        help="--run 무거운 등급 시나리오를 돌지 않는다(건너뜀 heavy_off · 기본 "
                             "E2 켬 · E1 끔)")
    parser.add_argument("--user", default=None,
                        help="--run 특정 채팅 계정 권한으로 잴 때만(기본 = 벤치 본체 인증 끔 · 127.0.0.1) · "
                             f"비밀번호는 환경변수 {PASSWORD_ENV} 또는 프롬프트")
    parser.add_argument("--term-policy", default=None,
                        help="--run 일반 단어 허용 목록(E1 기본 testdata/apm_bench/"
                             f"term_policy.yaml · E2 기본 {local_label(LOCAL_TERM_POLICY)})")
    parser.add_argument("--since", default=None,
                        help="--audit-digest 창 시작(ISO-8601 · 오프셋 필수)")
    parser.add_argument("--until", default=None,
                        help="--audit-digest 창 끝(ISO-8601 · 오프셋 필수)")
    parser.add_argument("--bench-user", default=None,
                        help="--audit-digest 벤치 본체 사용자(기본 anonymous · 값은 출력하지 않음)")
    parser.add_argument("--duration", type=float, default=None,
                        help="--audit-digest 메타 없는 run 의 소요 초(실행 로그 「소요 N초」)")
    parser.add_argument("--bg", action="store_true",
                        help="--run 을 분리 실행하고 바로 끝난다(run id · --status 명령 출력)")
    parser.add_argument("--run-id", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--turns", nargs="+", default=None, metavar="ID#N",
                        help="--replay 재현할 턴(기본 dev 실패 턴 전부 · holdout 지정은 경고)")
    parser.add_argument("--quarantine-dir", default=None,
                        help="--run·--regate·--purge 격리 기준 경로(기본 ~/apm_bench_quarantine · "
                             "저장소·run 디렉터리 밖만)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    bg_argv = os.environ.pop(BG_ARGV_ENV, None)
    if bg_argv is not None and "--run-id" in raw:  # `--bg` 자식 — 인자는 env 로 받았다
        try:
            given = json.loads(bg_argv)
        except ValueError:
            given = None
        if not (isinstance(given, list) and all(isinstance(a, str) for a in given)):
            say(f"[중단] {BG_ARGV_ENV} 형식이 아니다(내부 — --bg 가 넘긴다)")
            return 2
        raw = [*given, *raw]
    args = build_parser().parse_args(raw)
    args.raw_argv = raw  # `--bg` 자식 명령줄
    if args.bg and not args.run:
        say("[중단] --bg 는 --run 과 함께 쓴다")
        return 2
    if args.smoke and not args.run:
        say("[중단] --smoke 는 --run 과 함께 쓴다")
        return 2
    for dest, handler in MODES.items():
        if getattr(args, dest, None) not in (None, False):  # `--purge` 인자 없음 = ""
            return handler(args)
    return next(iter(MODES.values()))(args)


if __name__ == "__main__":
    sys.exit(main())
