"""벤치마크 단일 진입 (plans/93 §0.4 · 실행 가이드).

개발자가 외울 것은 두 줄이다.

    python -m scripts.bench                 # 설정 전수 점검
    python -m scripts.bench --sweep         # 성능 스위프까지 (후속 Wave)

축 선별·재개·통계 해석·결론 작성은 **전부 코드가 진다**. 개발자는 실행하고 결과 폴더를
전달한다. 그것이 역할의 전부다.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.bench import axes as axes_mod  # noqa: E402
from scripts.bench import catalog, compare, optimize, probe  # noqa: E402
from scripts.bench import report as report_mod, sweep as sweep_mod, validate  # noqa: E402
from scripts.bench import campaign as campaign_mod  # noqa: E402
from scripts.scenario.preflight import external_planes, mlx_run_blockers  # noqa: E402


def say(message: str = "") -> None:
    """진행 로그. **즉시 내보낸다.**

    10~30시간짜리 스위프를 `> log.txt`로 넘기면 기본 블록 버퍼링 때문에 끝날 때까지
    파일이 비어 있다. 실행자가 살아 있는지 확인할 수 없으면 중단할지 판단할 수 없다.
    """
    print(message, flush=True)

_RESULTS_DIR = _ROOT / "results" / "bench"
#: 94 러너 `RESULTS_ROOT` 와 같은 위치 — 구간 폴더(`results/scenario/<run_id>`)를 기록된
#: 절대 경로 대신 찾을 때 쓴다(109·CS-18). 러너를 임포트하지 않는다 — 트랙 T 는 94 없이도 돈다.
_SCENARIO_RESULTS_DIR = _ROOT / "results" / "scenario"


def _providers_of(echo: probe.EchoResult) -> tuple[str, str]:
    """(워커, 오케스트레이터) 프로바이더. 에코가 설정 전체를 평탄화하므로 둘 다 실려 있다."""
    return (str(echo.value_of("llm.provider") or "unknown"),
            str(echo.value_of("orchestrator.provider") or "unknown"))


def approval_policy(worker: str, orchestrator: str) -> tuple[bool, str]:
    """실 LLM 실행에 승인이 필요한가.

    내부망 면제는 **내부망에만** 적용된다 — 외부 프로바이더에서는 D-127이 그대로 산다.
    워커와 오케스트레이터를 **둘 다** 본다(D-222) — 판정 정의는 시나리오 하네스와 같은
    `scripts.scenario.preflight.external_planes` 한 곳이다(plans/93 §4.4 · 사용자 확정 2026-09-11).

    Returns:
        (승인 필요 여부, 사람이 읽을 사유)
    """
    external = external_planes(worker, orchestrator)
    if not external:
        return False, (f"내부망/로컬 프로바이더(워커 {worker}, 오케스트레이터 {orchestrator}) — "
                       "승인 없이 진행합니다")
    return True, (f"외부 프로바이더({', '.join(external)}) — "
                  "실 LLM 호출에는 승인이 필요합니다(D-127)")


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
    need_approval, reason = approval_policy(*_providers_of(echo))

    say(f"[OK]   venv          python {sys.version.split()[0]}")
    say(f"[OK]   config        {len(knobs)} 필드 로드 · 에코 {len(echo.config)} 경로")
    say(f"[OK]   provider      {reason}")

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
        # 트랙 T 는 **이어 돌지 않는다** — 매번 새 폴더에서 처음부터다. 종전 문구("다음 실행에서
        # 이어집니다")는 사실이 아니었다(plans/109 CS-04).
        say(f"[INFO] 이전 실행     {unfinished.name} 에 SUMMARY.md 가 없다(중단됨) — "
            "트랙 T 는 이어 돌지 않고 다음 실행이 새 폴더에서 처음부터 돈다")
    for name, record in _interrupted_segments():
        say(f"[INFO] 구간 캠페인   `{name}` 의 구간 {record.segment_id} 이 끊겨 있다 — "
            f"`--segment next` 가 run {record.run_id} 을 이어 돈다(성공한 턴은 건너뜀)")

    say("→ 실행 가능합니다.  python -m scripts.bench")
    return 0


def _free_gb(path: Path) -> float:
    import shutil
    return shutil.disk_usage(path).free / (1024 ** 3)


def _unfinished_run() -> Optional[Path]:
    """`SUMMARY.md` 가 없는 **트랙 T** 실행 폴더(`YYYYMMDD-NN`). 캠페인·반출 폴더는 보지 않는다."""
    if not _RESULTS_DIR.exists():
        return None
    for run in sorted(_RESULTS_DIR.iterdir(), reverse=True):
        if (run.is_dir() and re.fullmatch(r"\d{8}-\d{2}", run.name)
                and not (run / "SUMMARY.md").exists()):
            return run
    return None


def _interrupted_segments() -> list:
    """끊긴(진행 기록이 남았는데 그 프로세스가 없는) 캠페인 구간 — `(캠페인 이름, 기록)`."""
    found = []
    for path in sorted((_RESULTS_DIR / "campaigns").glob("*/campaign.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for raw in data.get("records", []):
            record = campaign_mod.SegmentRecord(**raw)
            if record.status == campaign_mod.RUNNING and not campaign_mod.pid_alive(record.pid):
                found.append((data.get("name", path.parent.name), record))
    return found


def cmd_show_env(args: argparse.Namespace) -> int:
    """지금 어떤 설정으로 도는지 — 값이 아니라 출처를 본다."""
    echo = probe.echo_config()
    if not echo.ok:
        say(f"설정을 읽지 못했습니다: {echo.error_type}: {echo.error}")
        return 1
    worker, orchestrator = _providers_of(echo)
    need, reason = approval_policy(worker, orchestrator)
    say(f"프로바이더   : 워커 {worker} / 오케스트레이터 {orchestrator}")
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


def _sweep_proposals(
    optima: list[compare.AxisOptimum],
    snapshot: sweep_mod.ConfigSnapshot,
    out_dir: Path,
) -> dict[str, Path]:
    """축 최적 레벨을 §6.5.2 처분 절차에 넣고 제안 문서를 쓴다.

    **스위프가 닿은 축만** 대상이다 — 전 335필드 처분은 `--propose`(트랙 C)의 몫이고,
    여기서는 *"이번에 잰 축의 최적값이 무엇으로 이어지는가"* 만 낸다.
    스냅샷이 없으면 `recommended.env.diff` 의 「현행값」 칸이 비지만 제안 자체는 나온다.
    """
    if not optima:
        return {}
    try:
        verdicts, recommended = optimize.axis_disposition_inputs(optima)
        by_key = {k.env_key: k for k in catalog.load_knobs()}
        targets = [by_key[axis] for axis in sorted(verdicts) if axis in by_key]
        # **조용히 빠뜨리지 않는다.** 카탈로그에 없는 축은 그 사이 키가 삭제·개명된 것이고
        # (실측: run 20260914-185540 의 `SCHEMA_CACHE_AUTO_GENERATE_DESCRIPTIONS`),
        # 그 사실이 안 보이면 "처분이 27건인데 축은 28개"가 원인 불명으로 남는다.
        dropped = [axis for axis in sorted(verdicts) if axis not in by_key]
        if dropped:
            say(f"  ※ 카탈로그에 없는 축 {len(dropped)}건은 처분 대상에서 뺐습니다 "
                f"(키 삭제·개명 추정): {', '.join(dropped)}")
        if not targets:
            return {}
        dispositions = optimize.build_from_validation(
            targets, verdicts=verdicts, recommended=recommended)
        deletions = [d for d in dispositions
                     if d.action in (optimize.PROPOSE_DELETE, optimize.PROPOSE_CONSTANT)]
        evidences = [optimize.collect_evidence(d.env_key, by_key[d.env_key])
                     for d in deletions]
        pin = {d.env_key: (by_key[d.env_key].default or "")
               for d in dispositions if d.action == optimize.CHANGE_DEFAULT}
        return optimize.write_proposals(
            dispositions, evidences, pin, out_dir,
            optima=optima, baseline_effective=snapshot.baseline_env_values())
    except Exception as exc:       # 제안은 부가 산출이다 — 여기서 런을 죽이지 않는다
        say(f"  처분 제안 생성 실패: {type(exc).__name__}: {exc}")
        return {}


@dataclass
class SweepOutcome:
    """스위프 1회의 결과 — `cmd_sweep` 은 `rc` 만 쓰고, 구간 캠페인은 전부 쓴다."""

    rc: int
    ran: bool = False                     # 서버를 띄워 실행까지 갔는가(환경 관문에서 멈추면 False)
    out_dir: Optional[Path] = None
    health: Optional[sweep_mod.RunHealth] = None
    optima: list = field(default_factory=list)
    substituted: list = field(default_factory=list)   # 실행 생략 arm id
    all_unjudged: bool = False
    discordant: int = 0
    elapsed_sec: float = 0.0
    #: 이 구간 arm 중 축 도달 불가(plans/114 M-3)로 판정된 arm id — 후단 관문(M-2 ③)이 쓴다.
    unreachable: list[str] = field(default_factory=list)
    #: 구간 간 비교(plans/114 M-2 ①b)가 캠페인 상태에 남길 것 — 이 구간 기준선의 사다리 단 ·
    #: 실효 설정 지문(비결정 키 제외) · 판(커밋·작업 트리). 앞 구간과 다르면 구간 사이의
    #: 기준선 반복(D-239 노이즈 바닥)이 같은 조건의 반복이 아니다.
    baseline_tier: Optional[str] = None
    config_fingerprint: Optional[str] = None
    commit: Optional[str] = None
    dirty: Optional[bool] = None


def cmd_sweep(args: argparse.Namespace) -> int:
    """트랙 A~B — 축 스위프와 판정. 94의 실행 원자 위에 선다(§1.5)."""
    try:
        arms = sweep_mod.build_arms(limit=4 if args.scale == "smoke" else None)
    except Exception as exc:
        say(f"축 전개 실패: {type(exc).__name__}: {exc}")
        return 1
    return run_sweep(args, arms, label=f"규모 {args.scale}").rc


def run_sweep(args: argparse.Namespace, arms: list, *, label: str,
              snapshot: Optional[sweep_mod.ConfigSnapshot] = None,
              substituted: Sequence = (), run_id: str = "",
              resume_from: Optional[str] = None,
              on_start: Optional[Callable[[], None]] = None,
              tier_caveat: Optional[str] = None) -> SweepOutcome:
    """arm 목록 하나를 스위프한다.

    `cmd_sweep`(전 축·smoke)과 구간 캠페인(`cmd_segment`)이 같이 쓴다.

    **arm 목록을 받는 것 말고는 종전 `cmd_sweep` 본문과 같다** — 구간 실행이 판정 경로를 따로 두면
    한쪽만 낡는다.

    `substituted` 는 **실행하지 않는** arm 이다 — 실효 설정 지문이 기준선과 같아(A/A 반복) 그 레벨의
    관측을 **같은 실행의 기준선 관측**으로 대신한다. 같은 구간·같은 시간대라 쌍체 비교가 성립한다.
    `snapshot` 을 주면 설정 에코를 다시 뜨지 않는다(캠페인이 계획 때 뜬 것을 넘긴다).
    `on_start` 는 **러너를 부르기 직전**에 한 번 불린다 — 환경 관문(계정·프로바이더·MLX)을 다
    통과해 실제로 돌기 시작한다는 뜻이다. 캠페인이 여기서 「진행」 기록을 남긴다.
    `resume_from` 은 그 run 을 이어 돈다(94 러너 이어쓰기 · 성공한 턴은 건너뛴다).
    `tier_caveat` 는 캠페인이 넘기는 **조건부 고지**다 — 단 축 판정이 「판정 불가」로 끝나 승자
    없이 도는 구간이면(plans/114 M-0 · D-250 ②) 판정문 머리말과 화면에 그 사실을 싣는다.
    """
    say(f"축 스위프 — arm {len(arms)}개 (기준선 포함) · {label} · 모드 {args.mode}")

    # 규모를 먼저 말한다. arm 1개 = 서버 기동 1회이므로 arm 수가 곧 시간이다(§4.4).
    # 승인용이 아니라 **운영 시간대를 피할 판단 재료**다 — 내부망은 승인 없이 진행한다.
    env, env_reason = sweep_mod.resolve_env(args.env)
    say(f"  환경: {env} — {env_reason}")

    workload = None
    if args.mode != "dry":
        try:
            workload = sweep_mod.load_normal_catalog(env=env)
            scenarios = len(workload.scenarios)
            # **무엇을 재는지 화면에 적는다.** 종전에는 기본값이 sandbox 라 유사어 32건만
            # 돌고 있었는데 출력 어디에도 그 사실이 없었다(실측 2026-09-14).
            say(f"  워크로드: {sweep_mod.workload_summary(workload)}")
        except Exception as exc:
            scenarios = 0
            say(f"  워크로드: 확인 실패 ({type(exc).__name__}: {exc})")
        # **턴 수에 비례시킨다.** 종전에는 arm 수만 곱해서, 워크로드가 유사어군만이든
        # 전 그룹이든 같은 시간을 예고했다(실측 2026-09-14: 3배 차이인데 같은 숫자였다).
        # 건수는 위 `workload_summary` 가 동적으로 센다 — 여기 적으면 카탈로그가 늘 때 낡는다.
        try:
            turns_per_arm = sweep_mod.planned_turns_per_arm(workload)
        except Exception:
            turns_per_arm = scenarios
        # 턴당 속도는 **실측**이다(run 20260914 51.2초 · mock 0.32초). 종전 20초 가정은 약 2.5배
        # 짧았다 — 그 런의 예고는 37.4시간, 실측은 93.4시간이었다.
        per_turn_sec = (campaign_mod.MOCK_SEC_PER_TURN if args.mode == "mock"
                        else campaign_mod.DEFAULT_SEC_PER_TURN)
        rate = campaign_mod.RateModel(sec_per_turn=per_turn_sec, turns_per_arm=turns_per_arm,
                                      source=f"{args.mode} 실측")
        turns = turns_per_arm * max(1, args.repeat) * len(arms)
        hours = rate.segment_hours(len(arms), args.repeat)
        say(f"  규모: 시나리오 {scenarios}건(턴 {turns_per_arm}) × arm {len(arms)} "
            f"× 반복 {args.repeat} = 턴 {turns}회")
        say(f"  예상: 약 {hours:.1f}시간 (기동 {len(arms)}회 × {rate.boot_sec:.0f}초 + "
            f"턴 {turns}회 × {per_turn_sec:g}초 · {rate.source})")
        if hours > campaign_mod.DEFAULT_MAX_HOURS:
            say(f"  ※ 약 {hours:.0f}시간입니다. 스위프는 **중단되면 이어지지 않는다** — "
                f"`--segment next` 로 설정 카테고리별 "
                f"{campaign_mod.DEFAULT_MAX_HOURS:g}시간 이내 구간으로 나눠 돌리세요.")

    creds = sweep_mod.resolve_credentials(
        user_id=args.user or os.environ.get("BENCH_USER_ID"),
        user_password=args.password or os.environ.get("BENCH_USER_PASSWORD"),
        admin_user=args.admin_user,
        admin_password=args.admin_password,
    )
    if args.mode != "dry":
        # 접속 방식을 **먼저** 말한다. 전건 401 은 한 시간을 태운 뒤에야 드러났다.
        auth_on = sweep_mod.server_auth_enabled() if args.mode == "run" else False
        if args.mode == "mock":
            say("  접속: 모의 서버 — 인증 없음")
        elif creds.can_login:
            say(f"  접속: 벤치 계정 `{creds.user_id}` 로 로그인")
        elif auth_on is False:
            say("  접속: 인증이 꺼진 서버(AUTH_ENABLED=false) — 계정 없이 진행")
        else:
            # 인증을 끄고 재지 않는다(plans/94 G-3 · 사용자 확정 2026-09-15) — 인증 미들웨어와
            # 사용자별 DB 범위가 빠지면 운영과 다른 경로를 잰다. 서버를 띄우기 **전에** 멈춘다.
            if auth_on:
                say("  접속: 인증이 켜진 서버(AUTH_ENABLED=true)인데 벤치 계정이 없습니다 — 시작하지 않습니다.")
            else:
                say("  접속: 서버 인증 설정을 읽지 못했고 벤치 계정도 없습니다 — 시작하지 않습니다.")
            say("     전용 계정을 주세요: --user <ID> --password <PW>")
            say("     또는 OS 환경변수 BENCH_USER_ID / BENCH_USER_PASSWORD "
                "(.env·.encenv 에 적으면 읽히지 않습니다)")
            return SweepOutcome(rc=2)
        # 러너가 모든 프로파일에 주입한다(runner.ISOLATION_ENV). 화면에도 적는다 —
        # 운영 스트림을 건드리지 않는다는 사실은 실행하는 사람이 알아야 한다.
        say("  격리: ALARM_ENABLED=false 를 모든 arm에 동일 주입 — 벤치 서버가 운영 알람 "
            "스트림을 같은 consumer group으로 나눠 소비하지 않게 한다.")
        if auth_on and not (creds.admin_user and creds.admin_password):
            say("  ※ 운영자 크레덴셜을 찾지 못했습니다(ADMIN_USERNAME/ADMIN_PASSWORD). "
                "설정 에코 검증이 실패해 arm이 전부 INVALID 가 됩니다.")

    if args.mode == "run":
        echo = probe.echo_config()
        providers = _providers_of(echo)
        need, reason = approval_policy(*providers)
        say(f"  프로바이더: {reason}")
        if need and not args.yes:
            say("  → 외부 프로바이더입니다. 승인 없이 실 호출하지 않습니다(D-127).")
            say("     내부망에서 실행하거나, 승인을 받았다면 --yes를 붙이세요.")
            return SweepOutcome(rc=2)
        if "mlx" in providers:
            # arm 마다 서버를 띄우기 전에 로컬 MLX 가 실제로 생성하는지 본다 — 죽은 서버로
            # 스위프를 돌리면 arm 전부가 1단 강등(INVALID)이거나 LLM 오류로 끝난다.
            blockers = mlx_run_blockers()
            if blockers:
                say("  → MLX 로컬 서버가 준비되지 않았습니다 — arm 서버를 띄우기 전에 멈춥니다.")
                for check in blockers:
                    say(f"     {check.key}: {check.observed} — {check.action}")
                return SweepOutcome(rc=2)

    if args.mode == "dry":
        for arm in arms:
            say(f"  {arm.arm_id:52s} {arm.env or '(기준선)'}")
        return SweepOutcome(rc=0)

    # **서버를 띄우기 전에** arm 실효 설정을 찍는다(§4.5). 주입값이 기준선 실효값과 같은
    # arm(대조군)은 판정표가 축 효과로 렌더링하면 안 되는데, 그 판정에 이 스냅샷이 필요하다.
    snapshot = snapshot or sweep_mod.capture_config_snapshot(arms)
    controls = snapshot.control_arms()
    #: 설정은 다르지만 축이 이 단의 그래프에 닿지 않는 arm(plans/114 M-3) — 델타는 노이즈다.
    unreachable = snapshot.unreachable_arms()
    sub_ids = {a.arm_id for a in substituted}
    #: 노이즈 바닥은 **실제로 돈** 대조군으로만 잰다 — 생략한 arm 의 관측은 기준선 복사본이라
    #: 델타가 0이다.
    run_controls = [a for a in controls if a not in sub_ids]
    if snapshot.unavailable:
        say(f"  설정 스냅샷: {snapshot.unavailable}")
    else:
        failed = [a for a in snapshot.arms.values() if not a.ok]
        say(f"  설정 스냅샷: arm {len(snapshot.arms)}개 실효값 확인 · "
            f"대조군(기준선과 동일 설정) {len(controls)}개"
            + (f" · 확인 실패 {len(failed)}개" if failed else ""))
        for arm_id in controls:
            arm = snapshot.arms[arm_id]
            skip = " → 실행 생략 · 기준선 관측 사용" if arm_id in sub_ids else ""
            say(f"     대조군 {arm_id} — 주입 {arm.injected} 이 기준선 실효값과 같다{skip}")
        for arm_id, reason in unreachable.items():
            skip = " → 실행 생략 · 기준선 관측 사용" if arm_id in sub_ids else ""
            say(f"     {arm_id} — {reason}{skip}")

    # **기대 단은 캠페인 상태가 안다**(plans/114 M-0 · D-250 ③) — 사람에게 묻지 않는다.
    # 단 축 구간이면 arm 마다 자기 레벨의 단, 승자 주입 뒤 구간이면 전 arm 이 기준선의 단이다.
    tier_axis, expected_tiers = sweep_mod.tier_context(list(arms) + list(substituted), snapshot)
    if tier_axis:
        listing = " · ".join(f"{a}={t}" for a, t in sorted(expected_tiers.items()))
        say(f"  사다리 단 축 구간 — 기대 단: {listing or '(그래프를 못 떠 기대 없음)'}")
        say(f"     {sweep_mod.TIER_AXIS_CAVEAT}")
    elif expected_tiers:
        say(f"  기대 단: `{sorted(set(expected_tiers.values()))[0]}` "
            "(단 축 구간이 정한 승자 주입) — 실제와 다르면 차단한다")
    if tier_caveat:
        say(f"  {tier_caveat}")

    if resume_from:
        say(f"  재개: run {resume_from} 을 잇는다 — 성공한 턴은 건너뛰고 무효 턴은 다시 돈다")
    if on_start:
        on_start()
    started = time.time()
    try:
        result = sweep_mod.run_arms(
            arms, mode=args.mode, env=env, repeat=args.repeat, credentials=creds,
            run_id=run_id, **({"resume_from": resume_from} if resume_from else {}))
    except sweep_mod.SweepUnavailable as exc:
        say(f"스위프를 돌릴 수 없습니다: {exc}")
        return SweepOutcome(rc=2)
    elapsed = time.time() - started

    out_dir = Path(result.get("out_dir", "")) if isinstance(result, dict) else None
    raw = (out_dir / "raw.jsonl") if out_dir else None
    if not raw or not raw.exists():
        say("원시 로그를 찾지 못했습니다. 94 러너 산출을 확인하세요.")
        return SweepOutcome(rc=1, ran=True, out_dir=out_dir, elapsed_sec=elapsed)

    # **94 분석기가 읽을 수 있는 폴더로 만든다**(D-237 · `plans/97` §5 Tier A).
    # 종전에는 `--sweep` 이 `scripts.scenario.report.write_report` 를 부르지 않아
    # `summary.json`·`report.md` 가 없었고, 그래서 스위프 산출물은 **94 분석기
    # (`analyze.py:476` 이 `summary.json` 을 요구한다)에 입력 자체가 불가능**했다.
    # 순수 후처리(입력은 `raw.jsonl`)라 비용이 없고, 실패해도 축 판정은 계속한다.
    try:
        from scripts.scenario.report import write_report as _write_scenario_report
        # 카탈로그를 넘긴다 — 없으면 3절 `목표(ms)`가 전 칸 공란이고(run 20260922-112010),
        # 2단 알람 고지가 D군 밖의 알람 시나리오를 고르지 못한다(plans/114 M-7).
        sc_paths = _write_scenario_report(out_dir, workload)
        say(f"  94 리포트: {', '.join(sorted(p.name for p in sc_paths.values()))}")
    except Exception as exc:
        say(f"  94 리포트 생성 실패({type(exc).__name__}: {exc}) — "
            f"`summary.json` 이 없어 94 분석기(`scripts.scenario --analyze`)에는 넣을 수 없습니다.")

    health = sweep_mod.scan_health(result, raw, tier_axis=tier_axis,
                                   expected_tiers=expected_tiers)
    say()
    launched = len(result.get("profiles") or []) or len(arms)   # arm × 시나리오 자기 프로파일
    say(f"건전성 — 유효 프로파일 {health.valid_profiles}/{launched} · 턴 {health.turns}건 "
        f"· 판정 {dict(sorted(health.verdicts.items()))}")
    say(f"  워크로드 도달 — SQL 관측 {health.sql_rate:.0%} · 그래프 진입 "
        f"{health.graph_entry_rate:.0%} · 역질문 종료 {health.clarify_rate:.0%}")
    say(f"  {_unevaluated_line(health.unevaluated)}")
    for name, reason in health.invalid_profiles:
        say(f"  INVALID {name}: {reason}")
    for mark, count in health.evidence:
        say(f"  오류 표지 {count}건: {mark}")
    for note in health.warnings():
        say(f"  주의: {note}")

    blocking = health.blocking_reason()
    if blocking:
        # 여기서 멈추지 않으면 사고가 "판정 불가"라는 정상 얼굴의 리포트로 나간다.
        say()
        say(f"판정을 내지 않습니다 — {blocking}")
        say("  원인을 고친 뒤 다시 실행하세요. 위 INVALID 사유·오류 표지가 출발점입니다.")
        return SweepOutcome(rc=1, ran=True, out_dir=out_dir, health=health, elapsed_sec=elapsed)

    observations = sweep_mod.read_observations(raw)
    grouped = sweep_mod.group_by_arm(observations)
    baseline = grouped.get(sweep_mod.BASELINE_ARM, [])
    if not baseline:
        say("기준선 arm 결과가 없습니다 — 비교 기준이 없어 판정을 내지 않습니다.")
        return SweepOutcome(rc=1, ran=True, out_dir=out_dir, health=health, elapsed_sec=elapsed)
    for arm in substituted:
        grouped[arm.arm_id] = sweep_mod.baseline_as(baseline, arm.arm_id)

    # **지연 노이즈 바닥을 실측한다.** 대조군 arm 은 설정이 기준선과 같으므로 그 델타가
    # 곧 바닥이다 — `--repeat 1` 이라 반복이 없어도 바닥을 잴 수 있는 유일한 경로다.
    lat_floor = compare.latency_noise_floor(
        baseline, [grouped.get(a, []) for a in run_controls]) if run_controls else None
    floor_pp = compare.noise_floor([baseline] + [grouped.get(a, []) for a in run_controls])

    verdicts = []
    for arm in arms:
        if arm.arm_id == sweep_mod.BASELINE_ARM:
            continue
        verdicts.append(compare.judge(
            arm.arm_id, arm.axis, arm.level, baseline, grouped.get(arm.arm_id, []),
            noise_floor_pp=floor_pp))

    say()
    if lat_floor is not None or floor_pp:
        acc_txt = f"정확도 {floor_pp:.1f}%p" if floor_pp else "정확도 미측정"
        lat_txt = f"지연 ±{lat_floor:.0f}ms" if lat_floor is not None else "지연 미측정"
        say(f"노이즈 바닥(대조군 {len(run_controls)}개 실측) — {acc_txt} · {lat_txt}")
    elif sub_ids:
        say("노이즈 바닥 — 이 실행 안에서는 재지 않는다"
            "(대조군을 돌리지 않고 기준선 관측으로 대신했다). "
            "구간 캠페인의 합산 리포트가 구간 간 기준선 반복으로 잰다.")
    say(f"{'arm':52s} {'판정':10s} 문장")
    for v in verdicts:
        mark = (" [대조군]" if v.arm_id in set(controls)
                else " [도달 불가]" if v.arm_id in unreachable else "")
        say(f"  {v.arm_id:50s} {v.verdict:10s}{mark} {v.sentence}")
    for arm in substituted:
        why = "도달 불가" if arm.arm_id in unreachable else "기준선과 동일 설정"
        say(f"  {arm.arm_id:50s} {'—':10s} {why} → 기준선 관측 사용(실행 생략)")

    # **축 단위 판정 — 레벨 간 직접 비교(W-1).** 위 표는 전부 「arm vs 기준선」이라
    # "켰을 때 vs 껐을 때"가 어디에도 없다. 벤치마크의 목적이 그것이므로 따로 낸다.
    optima = [sweep_mod.note_substitution(o, substituted, unreachable)
              for o in compare.optima(grouped, list(arms) + list(substituted),
                                      control_arms=list(controls)
                                      + sorted(sub_ids - set(controls)))]
    say()
    say(f"{'축':46s} {'판정':14s} 최적 레벨 · 근거")
    for opt in optima:
        best = f"`{opt.best_level}`" if opt.best_level else "—"
        say(f"  {opt.axis:44s} {opt.verdict:14s} {best} {opt.sentence}")

    # **후단 관문 — 판정표가 아무것도 판정하지 못했으면 그것도 사고다.**
    # 전단 관문(`blocking_reason`)은 판정 이전의 신호만 본다. 그런데 run 20260914-185540 은
    # 그 관문을 통과하고도 62 arm 전부 「판정 불가」를 냈다 — 불일치 쌍 0건이 61 arm 이었다.
    # 그 표는 형식상 정상이라 처분 파이프라인(§6.5.2 7행)으로 흘러 들어가 "보류"를 62건
    # 만들어 낸다. 전건 판정 불가는 축의 성질이 아니라 **워크로드가 축을 흔들지 못했다**는
    # 뜻이므로, 표를 쓰되 머리말에 무효를 박고 exit 1 한다.
    unjudged = [v for v in verdicts if v.verdict == compare.UNDERPOWERED]
    all_unjudged = bool(verdicts) and len(unjudged) == len(verdicts)
    discordant = sum((v.accuracy.discordant if v.accuracy else 0)
                     + (v.completion.discordant if v.completion else 0)
                     + (v.sql_rate.discordant if v.sql_rate else 0) for v in verdicts)

    lines = ["# 축 스위프 판정", ""]
    # **조건부 고지가 맨 위다** — 승자 없이 돈 구간의 결과를 다른 단으로 옮겨 읽지 않게.
    if tier_caveat:
        lines += [f"> {tier_caveat}", ""]
    if tier_axis:
        lines += [f"> {sweep_mod.TIER_AXIS_CAVEAT}", "",
                  f"> {sweep_mod.TIER_AXIS_ATTRIBUTION}", ""]
    lines += [f"> {_unevaluated_line(health.unevaluated)}", ""]
    if all_unjudged:
        lines += [
            f"> **이 판정표는 무효다.** arm {len(verdicts)}개가 전부 "
            f"「{compare.UNDERPOWERED}」이고 "
            f"모든 신호(정확도·완주율·SQL 생성률)의 불일치 쌍 합이 {discordant}건이다 — "
            "설정 축이 결과를 흔들지 못한 것이 아니라 **워크로드가 축에 닿지 못했다**. "
            "처분 규칙(`plans/93` §6.5.2)의 입력으로 쓰지 말 것.",
            "",
            f"> 도달 지표 — SQL 관측 {health.sql_rate:.0%} · 그래프 진입 "
            f"{health.graph_entry_rate:.0%} · 역질문 종료 {health.clarify_rate:.0%}",
            "",
        ]
    # **축 단위 표를 먼저 낸다** — "이 축의 최적 레벨"이 읽는 사람의 질문이고,
    # arm 표는 그 근거(기준선 대비 절대 위치)다.
    lines += ["## 축 최적 레벨 — 레벨 간 직접 비교", ""]
    if snapshot.unavailable:
        lines += [f"> 대조군 판정 불가: {snapshot.unavailable}", ""]
    elif controls:
        lines += [f"> 대조군(기준선과 실효 설정 동일) {len(controls)}개: "
                  + " · ".join(f"`{a}`" for a in controls)
                  + " — 이 arm 의 델타는 축 효과가 아니라 **노이즈 바닥**이다.", ""]
    if unreachable:
        lines += [f"> **도달 불가 arm {len(unreachable)}개**(plans/114 M-3) — 설정은 다르지만 축이 "
                  "이 단의 그래프에 닿지 않아 델타는 축 효과가 아니라 노이즈다: "
                  + " · ".join(f"`{a}` ({reason})" for a, reason in unreachable.items()), ""]
    if lat_floor is not None:
        lines += [f"> 실측 노이즈 바닥 — 정확도 {floor_pp:.1f}%p · 지연 ±{lat_floor:.0f}ms", ""]
    lines += ["| 축 | 레벨 | 판정 | 최적 | 근거 |", "|---|---|---|---|---|"]
    lines += [f"| `{o.axis}` | {' · '.join(o.levels)} | **{o.verdict}** | "
              f"{('`' + o.best_level + '`') if o.best_level else '—'} | {o.sentence} |"
              for o in optima]

    lines += ["", "## arm 판정 — 기준선 대비", "",
              "| arm | 축 | 값 | 판정 | 근거 |", "|---|---|---|---|---|"]
    lines += [f"| `{v.arm_id}`{' **(대조군)**' if v.arm_id in set(controls) else ''}"
              f"{' **(도달 불가)**' if v.arm_id in unreachable else ''} "
              f"| {v.axis or '—'} | {v.level or '—'} | **{v.verdict}** | {v.sentence} |"
              for v in verdicts]
    lines += [(f"| `{a.arm_id}` **(도달 불가)** | {a.axis} | {a.level} | **실행 생략** | "
               f"기준선 관측 사용 — {unreachable[a.arm_id]} |")
              if a.arm_id in unreachable else
              (f"| `{a.arm_id}` **(기준선과 동일 설정)** | {a.axis} | {a.level} | **실행 생략** | "
               "기준선 관측 사용 — 실효 설정 지문이 기준선과 같아 A/A 반복이다 |")
              for a in substituted]
    if out_dir:
        (out_dir / "axis_verdicts.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        snap_path = sweep_mod.write_config_snapshot(snapshot, out_dir)
        say(f"\n산출: {out_dir}")
        say(f"  설정 스냅샷: {snap_path.name}")
        # **축 최적 레벨이 설정 제안으로 이어지는 지점**(§6.5 · D-237). 스위프가 판정만
        # 내고 끝나면 "어느 값이 최적인가"에 답해 놓고도 그 답이 아무 데도 가지 않는다.
        proposals = _sweep_proposals(optima, snapshot, out_dir / "proposals")
        if proposals:
            say(f"  처분 제안: {', '.join(sorted(p.name for p in proposals.values()))}")
    if all_unjudged:
        say()
        say(f"판정표를 무효로 표시했습니다 — arm {len(verdicts)}개 전부 「{compare.UNDERPOWERED}」 "
            f"· 불일치 쌍 합 {discordant}건.")
        say("  축이 아니라 워크로드를 먼저 고치세요(위 도달 지표 참조).")
    meta = (result.get("meta") or {}) if isinstance(result, dict) else {}
    return SweepOutcome(rc=1 if all_unjudged else 0, ran=True, out_dir=out_dir, health=health,
                        optima=optima, substituted=sorted(sub_ids), all_unjudged=all_unjudged,
                        discordant=discordant, elapsed_sec=elapsed,
                        unreachable=sorted(a.arm_id for a in list(arms) + list(substituted)
                                           if a.arm_id in unreachable),
                        baseline_tier=health.baseline_tier(),
                        config_fingerprint=snapshot.fingerprint(sweep_mod.BASELINE_ARM),
                        commit=meta.get("commit"), dirty=meta.get("dirty"))


# ── 구간 캠페인 (사용자 지시 2026-09-21 · 1회 구동 ≤ 10시간) ─────────────────


def _ladder_axis() -> tuple[Optional[str], Optional[str]]:
    """`(축 id, 못 쓰는 사유)` — 구조 축을 쓸 수 있는가(plans/114 M-0).

    정의를 못 읽으면 **조용히 없던 일로 하지 않는다** — 사유를 들고 다니다 계획 표에 찍는다.
    그 상태에서는 종전처럼 `.env` 가 확정한 단으로 돌게 되므로, 그 사실이 보여야 한다.
    """
    _, decisions = axes_mod.structural_axes()
    bad = next((d for d in decisions if not d.included), None)
    return (None, bad.reason) if bad else (axes_mod.LADDER_AXIS, None)


def _decide_tier_winner(
    campaign: campaign_mod.Campaign,
    segment: campaign_mod.Segment,
    outcome: SweepOutcome,
) -> dict[str, Any]:
    """단 축 구간이 끝났다 — **이긴 단**을 정한다(plans/114 M-0 · D-250 ②).

    판정은 레벨 간 직접 비교(`compare.optima` · D-237)를 그대로 쓴다. 규칙 셋뿐이다.

    - `최적 레벨` — 그 레벨이 승자다.
    - `레벨 간 차이 없음` — **기준 경로 3단**(`semantic_router` · D-225)을 쓴다. 차이가 없으면
      기준을 따르는 것이 기본값 정책이다.
    - `판정 불가` — 차이를 **보이지 못한 것**이므로 「차이 없음」과 같이 기준 경로 3단으로
      고정하고, `caveat` 를 남겨 이후 전 구간 판정문에 고지한다(멈추지 않는다 — d9 2026-09-23).
      기준 경로로 확정된 레벨이 관측에 없을 때만 `blocked` 다.

    돌려주는 것은 `campaign.tier_decision` 에 그대로 들어갈 딕셔너리다.
    """
    base = {"segment_id": segment.segment_id, "axis": axes_mod.LADDER_AXIS,
            "decided_at": datetime.now().isoformat(timespec="seconds")}
    optimum = next((o for o in (outcome.optima or []) if o.axis == axes_mod.LADDER_AXIS), None)
    if optimum is None:
        return {**base, "blocked": "단 축 판정이 산출되지 않았다 — 구간 산출물에 이 축의 "
                                   "레벨 비교가 없다(arm 이 실행되지 않았거나 관측이 비었다)"}
    axis = axes_mod.find_axis(axes_mod.LADDER_AXIS)
    if axis is None:
        return {**base, "verdict": optimum.verdict,
                "blocked": "구조 축 정의를 다시 읽지 못해 주입할 env 를 만들 수 없다"}

    if optimum.verdict == compare.BEST_LEVEL and optimum.best_level:
        level, why = optimum.best_level, f"레벨 간 비교에서 우세 — {optimum.sentence}"
    elif optimum.verdict == compare.LEVELS_TIED:
        canonical = sweep_mod.canonical_tier()
        measured = outcome.health.tier_by_arm() if outcome.health else {}
        level = next((lv for lv in axis.levels
                      if measured.get(f"S2-{axes_mod.LADDER_AXIS}-{lv}") == canonical), None)
        if level is None:
            return {**base, "verdict": optimum.verdict,
                    "blocked": (f"「{compare.LEVELS_TIED}」이라 기준 경로 `{canonical}`(D-225)를 "
                                "써야 하는데, 그 단으로 확정된 레벨이 이 구간 관측에 없다 — "
                                "어느 레벨이 기준 경로인지 확인하고 다시 판정한다")}
        why = (f"「{compare.LEVELS_TIED}」 — 기준 경로 `{canonical}`(D-225)를 쓴다. "
               f"{optimum.sentence}")
    else:
        # 「판정 불가」(검정력 부족)도 **차이를 보이지 못한 것**이다 — 「차이 없음」과 같은 처분을
        # 쓴다: 기준 경로 3단(D-225)으로 고정하고, 그 사실을 남은 전 구간에 고지로 싣는다.
        # 멈추지 않는 이유(collectorinfra-d9 2026-09-23 지적): 반복 1회 설계에서 판정 불가는
        # 흔하고(run 20260914 는 전 축이 그랬다), 멈추면 캠페인 전체가 진행되지 못한다. 반대로
        # `.env` 가 우연히 준 단으로 계속 가는 것도 D-250 이 고치려던 바로 그 상태다.
        canonical = sweep_mod.canonical_tier()
        measured = outcome.health.tier_by_arm() if outcome.health else {}
        level = next((lv for lv in axis.levels
                      if measured.get(f"S2-{axes_mod.LADDER_AXIS}-{lv}") == canonical), None)
        if level is None:
            return {**base, "verdict": optimum.verdict,
                    "blocked": (f"단 축 판정이 「{optimum.verdict}」인데 기준 경로 "
                                f"`{canonical}`(D-225)로 확정된 레벨이 이 구간 관측에 없다 — "
                                f"{optimum.sentence}")}
        why = (f"「{optimum.verdict}」 — 차이를 보이지 못해 기준 경로 `{canonical}`(D-225)로 "
               f"고정한다. {optimum.sentence}")
        caveat = (f"사다리 단 축이 「{optimum.verdict}」다 — 승자를 재서 고른 것이 아니라 기준 "
                  f"경로 `{canonical}`(D-225)로 고정했다. 이후 구간의 축 결과는 그 단에 조건부다")
        measured_tier = measured.get(f"S2-{axes_mod.LADDER_AXIS}-{level}")
        return {**base, "verdict": optimum.verdict, "level": level, "tier": measured_tier,
                "env": axis.env_for(level), "sentence": why, "caveat": caveat,
                **_winner_rate(outcome, level), **_timeout_note(outcome)}

    measured = outcome.health.tier_by_arm() if outcome.health else {}
    tier = measured.get(f"S2-{axes_mod.LADDER_AXIS}-{level}")
    return {**base, "verdict": optimum.verdict, "level": level, "tier": tier,
            "env": axis.env_for(level), "sentence": why,
            **_winner_rate(outcome, level), **_timeout_note(outcome)}


def _winner_rate(outcome: SweepOutcome, level: str) -> dict[str, Any]:
    """남은 구간 재계획에 쓸 **승자 arm 의** 속도(벤치 소유 검토 ②).

    단 축 구간에는 속도가 다른 두 단이 섞여 있고 남은 구간은 이긴 단으로만 돈다 — 구간 평균을
    쓰면 지지 않은 단의 속도가 섞인다. 못 재면 아무것도 싣지 않는다(`Campaign.rate` 가 섞인
    평균 대신 기본값으로 내려간다).
    """
    if outcome.out_dir is None:
        return {}
    got = sweep_mod.arm_rate(outcome.out_dir / "raw.jsonl",
                             f"S2-{axes_mod.LADDER_AXIS}-{level}", outcome.elapsed_sec)
    if got is None:
        return {}
    sec_per_turn, turns = got
    return {"winner_sec_per_turn": round(sec_per_turn, 3), "winner_turns_per_arm": turns}


def _timeout_note(outcome: SweepOutcome) -> dict[str, Any]:
    """단 축 판정에 **타임아웃을 반드시 싣는다**(벤치 소유 검토 ①).

    단 축 구간에서는 타임아웃률이 실패 사유가 아니라 **측정 결과**다 — 단이 지연을 바꾸므로
    한쪽 단의 타임아웃이 많은 것 자체가 그 단의 성적이다. 판정문에서 빠지면 "왜 이 단이
    이겼는가"의 절반이 사라진다(타임아웃 턴은 기능 분모에서 빠져 완주율·지연으로만 남는다).
    """
    health = outcome.health
    if health is None or not health.turns:
        return {}
    return {"timeout_turns": health.timeout_turns, "turns": health.turns,
            "timeout_rate": round(health.timeout_rate, 4)}


def _tier_gate(
    campaign: campaign_mod.Campaign,
    target: campaign_mod.Segment,
    plan: campaign_mod.Plan,
) -> Optional[str]:
    """이 구간을 지금 돌아도 되는가 — 멈출 사유, 없으면 None(plans/114 M-0 · D-250 ①).

    **구조 축 구간이 끝나기 전에는 다른 구간을 돌지 않는다.** 단은 노드 집합을 바꿔 다른 축의
    효과를 조건부로 만들기 때문이다 — 2단에서 잰 축 결과는 3단으로 옮겨지지 않는다.

    구조 축을 잴 수 없는 캠페인(축 정의를 못 읽거나 전 레벨이 기준선과 같은 설정)에서는
    막지 않는다. 그 사실은 계획 표의 「사다리 단」 줄이 드러낸다 — 조용히 통과시키는 것이
    아니라 **막을 근거가 없는 것**이다.
    """
    if target.structural:
        if target.over_budget:
            return (f"구조 축 구간 `{target.segment_id}` 의 추정 {target.est_hours:.1f}시간이 "
                    f"채움 상한 {plan.budget_hours:.1f}시간(1회 구동 {plan.max_hours:g}시간 · "
                    "D-239)을 넘습니다. 이 구간은 **빼면 캠페인이 성립하지 않으므로** 구간 불가로 "
                    "떨구지 않았습니다 — 사람이 정하세요: `--max-hours` 를 "
                    f"{math.ceil(target.est_hours / (1 - campaign_mod.SAFETY_MARGIN))} 이상으로 "
                    "올리거나, `--groups` 로 워크로드를 줄여 다시 실행합니다.")
        return None

    if campaign.structural_done() is not None:
        # 끝났으면 승자 유무와 무관하게 통과다 — **판정 불가는 멈춤 사유가 아니다**
        # (벤치 소유 검토 ④). 대신 `campaign.tier_caveat()` 가 판정문·리포트에 조건부 고지를
        # 싣고, 승자가 없으면 주입도 없어 서버 `.env` 의 단으로 돈다.
        return None
    waiting = (any(s.structural for s in plan.segments)
               or any(r.structural and r.status != campaign_mod.DONE
                      for r in campaign.records.values()))
    if waiting:
        return ("사다리 단 축 구간이 아직 끝나지 않았습니다 — D-250 ①: *\"사다리 단은 벤치 "
                "캠페인 첫 구간의 다중 키 축으로 잰다\"*, ②: *\"구조 축을 먼저 잰다. 단은 노드 "
                "집합을 바꿔 다른 축의 효과를 조건부로 만든다\"*. 지금 다른 구간을 돌리면 그 "
                f"결과는 `.env` 가 우연히 확정한 단에 조건부입니다"
                f"(구간 `{target.segment_id}`). "
                "먼저 `--segment next` 로 단 축 구간을 돌리세요.")
    return None


def _campaign_context(args: argparse.Namespace, snapshot=None):
    """캠페인 상태·전 축 arm·카테고리·계획을 한 번에 만든다. 실행과 계획 보기가 같은 값을 본다.

    **계획 전에 남은 arm 의 실효 설정 지문을 뜬다**(`capture_config_snapshot` · 65 arm 약 36초
    · LLM 0). 지문이 기준선과 같은 arm 은 실행 목록에서 빠진다. 스냅샷을 못 뜨면 빼지 않는다
    (추정 금지).

    **단 축이 이미 이긴 단을 정했으면 그 3키를 전 arm 에 주입한다**(plans/114 M-0 · D-250 ②).
    기준선까지 포함해 주입하는 이유는 94 러너가 arm 마다 프로파일을 따로 띄우기 때문이다 —
    기준선에만 넣으면 변이 arm 은 `.env` 의 단으로 돈다. arm 자신의 축 값이 우선한다.
    """
    env, env_reason = sweep_mod.resolve_env(args.env)
    name = args.campaign or campaign_mod.default_name(args.mode, env)
    path = campaign_mod.campaign_path(_RESULTS_DIR, name)
    campaign = campaign_mod.Campaign.load_or_new(
        path, name=name, env=env, mode="run" if args.mode == "dry" else args.mode,
        repeat=args.repeat, max_hours=args.max_hours)
    all_arms = sweep_mod.build_arms()
    winner_env = campaign.tier_winner_env()
    if winner_env:
        all_arms = [replace(a, env={**winner_env, **a.env}) for a in all_arms]
    categories = sweep_mod.axis_categories()
    turns_per_arm = sweep_mod.planned_turns_per_arm(sweep_mod.load_normal_catalog(env=env))
    rate = campaign.rate(turns_per_arm)
    frozen = campaign.frozen_axes()
    pending_arms = [a for a in all_arms if a.axis and a.axis not in frozen]
    if snapshot is None and pending_arms:
        baseline = [a for a in all_arms if a.arm_id == sweep_mod.BASELINE_ARM]
        snapshot = sweep_mod.capture_config_snapshot(baseline + pending_arms)
    controls = (frozenset(snapshot.control_arms())
                if snapshot is not None and not snapshot.unavailable else frozenset())
    # 축 도달 불가 arm 도 돌리지 않는다 — 설정은 달라도 실효는 A/A 다(plans/114 M-3).
    unreachable = snapshot.unreachable_arms() if snapshot is not None else {}
    ladder, _ = _ladder_axis()
    plan = campaign_mod.plan_segments(
        pending_arms, categories, rate=rate, max_hours=args.max_hours,
        repeat=args.repeat, used_ids=campaign.used_ids(),
        controls=controls | frozenset(unreachable), reasons=unreachable,
        # 첫 구간은 보정용으로 가장 작게 — 아직 아무 구간도 돈 적이 없을 때만.
        calibrate=not campaign.records,
        # 구조 축(사다리 단)이 그보다도 앞이다(plans/114 M-0 · D-250 ①).
        first_axis=ladder)
    return env, env_reason, campaign, all_arms, categories, plan, snapshot


def _campaign_guard(args: argparse.Namespace) -> Optional[str]:
    """재개 조건이 캠페인과 같은지 — 돌리기 전에 멈출 사유, 없으면 None (109·CS-19 ①②).

    ① 기존 캠페인과 `--repeat`·`--mode` 가 다르면 멈춘다. 상태 파일은 처음 값을 들고 있는데 계획·
       실행은 인자를 쓴다 — 대조하지 않으면 `--campaign run-closed --mode mock` 의 mock 구간이 실
       캠페인에 「완료」로 기록된다. `--mode dry` 는 계획 보기라 모드는 대조하지 않는다.
    ② `--campaign` 없이 기본 이름(`<mode>-<env>`)으로 **새** 캠페인을 열려는데 같은 모드의 다른
       캠페인에 끊긴 구간이 있으면 멈춘다 — `--env auto` 판정이 바뀌어 이름이 달라진 것이다.
       끊긴 run 을 잇지 않고 처음부터 새 캠페인을 돌면 그 run 은 버려진다.
    """
    env, _ = sweep_mod.resolve_env(args.env)
    name = args.campaign or campaign_mod.default_name(args.mode, env)
    path = campaign_mod.campaign_path(_RESULTS_DIR, name)
    mode = "run" if args.mode == "dry" else args.mode
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        diffs = []
        if int(data.get("repeat", args.repeat)) != args.repeat:
            diffs.append(f"반복 — 상태 파일 {data.get('repeat')} · 인자 --repeat {args.repeat}")
        if args.mode != "dry" and data.get("mode", mode) != args.mode:
            diffs.append(f"모드 — 상태 파일 {data.get('mode')} · 인자 --mode {args.mode}")
        if diffs:
            return (f"캠페인 `{name}` 과 실행 조건이 다릅니다({' / '.join(diffs)}). "
                    f"한 캠페인의 구간은 같은 조건이어야 합산됩니다 — 처음 옵션"
                    f"(--mode {data.get('mode')} "
                    f"--repeat {data.get('repeat')})으로 다시 실행하세요.")
        return None
    if args.campaign:
        return None
    for other, record in _interrupted_segments():
        if other == name or record.mode != mode:
            continue
        try:
            other_env = json.loads(campaign_mod.campaign_path(_RESULTS_DIR, other)
                                   .read_text(encoding="utf-8")).get("env", "?")
        except (OSError, json.JSONDecodeError):
            other_env = "?"
        return (f"캠페인 `{other}`(환경 {other_env})의 구간 {record.segment_id} 이 끊겨 "
                f"있는데, 이번 실행은 새 캠페인 `{name}`(환경 {env})을 엽니다 — "
                f"`--env {args.env}` 판정이 바뀐 것입니다. 이으려면 `--env {other_env}` 또는 "
                f"`--campaign {other}` 로 다시 실행하세요. 새 캠페인이 맞다면 "
                f"`--campaign {name}` 으로 명시하세요.")
    return None


def cmd_segment(args: argparse.Namespace) -> int:
    """구간 캠페인 — `--segment next` 를 반복해서 치면 다음 미완 구간이 돈다.

    - `--mode dry` 는 **계획 표만** 찍는다(아무것도 돌리지 않는다).
    - **실패 구간이 있으면 `next` 는 멈춘다.** 건너뛰면 같은 원인으로 다음 구간도 버린다.
      원인을 고친 뒤 `--segment <구간id>` 로 그 구간을 다시 돈다.
    - 구간 판정은 그 구간 기준선으로만 한다. 합산 리포트는 끝난 구간을 모은 것이다.
    - 재개 조건(모드·반복·환경)이 캠페인과 다르면 **돌리기 전에** 멈춘다(`_campaign_guard`).
    """
    try:
        problem = _campaign_guard(args)
    except Exception as exc:
        say(f"캠페인 상태 확인 실패: {type(exc).__name__}: {exc}")
        return 1
    if problem:
        say(f"멈춥니다 — {problem}")
        return 1
    try:
        env, env_reason, campaign, all_arms, categories, plan, snapshot = _campaign_context(args)
    except Exception as exc:
        say(f"캠페인 계획 실패: {type(exc).__name__}: {exc}")
        return 1
    say(f"  환경: {env} — {env_reason}")
    for line in campaign_mod.render_plan(campaign, plan):
        say(line)
    if snapshot is None or snapshot.unavailable:
        say("  실행 생략: 없음 — 설정 스냅샷을 뜨지 못했다. "
            "기준선과 같은 arm 도 전부 돈다(추정 금지)."
            + (f" 사유: {snapshot.unavailable}" if snapshot is not None else ""))
    else:
        skipped = sum(len(s.substituted) for s in plan.segments)
        say(f"  실행 생략: 미완 구간의 arm {skipped}개 — 실효 설정 지문이 기준선과 같다(A/A 반복). "
            "그 레벨은 같은 구간의 기준선 관측을 쓴다. 노이즈 바닥은 구간 간 기준선 반복으로 잰다.")
        for arm_id, reason in snapshot.unreachable_arms().items():
            say(f"  실행 생략(M-3): {arm_id} — {reason}")

    if args.mode == "dry":
        return 0

    target_id, retry, resume = args.segment, None, None
    running = campaign.running()
    if target_id == "next" and running:
        target_id = running[0].segment_id       # 끊긴 구간이 먼저다 — 새 구간을 열지 않는다
    if target_id == "next":
        failed = campaign.failed()
        if failed:
            say()
            say(f"멈춥니다 — 실패한 구간이 있습니다: {', '.join(r.segment_id for r in failed)}")
            for record in failed:
                for reason in record.stop_reasons:
                    say(f"  {record.segment_id}: {reason}")
            say(f"  원인을 고친 뒤 `--segment {failed[0].segment_id}` 로 그 구간을 다시 돌리세요.")
            return 1
        if not plan.segments:
            say()
            say("캠페인의 모든 구간이 끝났습니다."
                + (f" 예산 초과로 구간을 만들 수 없는 축 {len(plan.unplaceable)}개는 "
                   "미측정으로 남습니다." if plan.unplaceable else ""))
            _write_campaign_report(campaign, plan, categories, all_arms)
            return 0
        target = plan.segments[0]
    else:
        record = campaign.records.get(target_id)
        if record and record.status == campaign_mod.DONE:
            say(f"\n구간 {target_id} 은 이미 완료됐습니다. 다음은 `--segment next`.")
            return 0
        if record and record.status == campaign_mod.RUNNING:
            if campaign_mod.pid_alive(record.pid):
                say(f"\n멈춥니다 — 구간 {target_id} 을 다른 프로세스(pid {record.pid})가 "
                    "돌고 있습니다. 같은 run 에 두 프로세스가 쓰면 원시 로그가 섞입니다.")
                say(f"  그 프로세스가 캠페인이 아니라면 `{campaign.path}` 에서 이 구간의 `pid` 를 "
                    "지우고 다시 실행하세요.")
                return 1
            resume = record
            say(f"\n구간 {target_id} 이 끊겨 있습니다 — run {record.run_id} 을 이어 돕니다.")
        if record:                      # 실패 구간 재시도·끊긴 구간 재개 — 같은 축·같은 생략
            retry = record
            executed = tuple(a for a in record.arm_ids if a not in set(record.substituted))
            target = campaign_mod.Segment(
                segment_id=record.segment_id, category=record.category, axes=tuple(record.axes),
                arm_ids=executed, substituted=tuple(record.substituted),
                est_hours=plan.rate.segment_hours(1 + len(executed), args.repeat),
                # 구조 축 구간을 다시 돌 때도 구조 축이다 — 관문·판정이 그 사실에 달렸다.
                structural=record.structural)
            if (target.est_hours > plan.budget_hours and not resume
                    and not target.structural):
                say(f"\n구간 {target_id} 의 현재 추정 {target.est_hours:.1f}시간이 채움 상한 "
                    f"{plan.budget_hours:.1f}시간을 넘습니다 — 재시도하지 않습니다.")
                return 1
        else:
            found = [s for s in plan.segments if s.segment_id == target_id]
            if not found:
                say(f"\n구간 `{target_id}` 을 찾지 못했습니다 — 위 표의 구간 id 를 쓰세요.")
                return 1
            target = found[0]

    blocked = _tier_gate(campaign, target, plan)
    if blocked:
        say()
        say(f"멈춥니다 — {blocked}")
        return 1

    seg_arms = campaign_mod.segment_arms(all_arms, target.axes, sweep_mod.BASELINE_ARM)
    skip_ids = set(target.substituted)
    arms = [a for a in seg_arms if a.arm_id not in skip_ids]
    substituted = [a for a in seg_arms if a.arm_id in skip_ids]
    say()
    say(f"구간 {target.segment_id} — 카테고리 {target.category} · 축 {len(target.axes)}개 · "
        f"arm {len(arms)}개 실행" + (f"(생략 {len(substituted)}개)" if substituted else "")
        + f" · 추정 {target.est_hours:.1f}시간")
    started = (resume.started_at if resume and resume.started_at
               else datetime.now().isoformat(timespec="seconds"))
    run_id = resume.run_id if resume and resume.run_id else datetime.now().strftime("%Y%m%d-%H%M%S")
    attempts = (retry.attempts + 1) if retry else 1
    seg_snapshot = (snapshot.subset([a.arm_id for a in seg_arms if a.axis])
                    if snapshot is not None else None)

    def mark_running() -> None:
        # **돌기 전에 run_id 를 남긴다** — 끊기면(kill·전원·세션 종료) 이 기록이 재개점이다.
        campaign.records[target.segment_id] = campaign_mod.SegmentRecord(
            segment_id=target.segment_id, category=target.category, axes=list(target.axes),
            arm_ids=list(target.arm_ids) + list(target.substituted),
            substituted=list(target.substituted), status=campaign_mod.RUNNING, mode=args.mode,
            run_id=run_id, started_at=started, est_hours=round(target.est_hours, 2),
            attempts=attempts, pid=os.getpid(), resumed=bool(resume))
        campaign.save()

    outcome = run_sweep(args, arms, label=f"구간 {target.segment_id}", snapshot=seg_snapshot,
                        substituted=substituted, run_id=run_id,
                        resume_from=resume.run_id if resume else None, on_start=mark_running,
                        # 승자를 못 정한 채 도는 구간이면 판정문에 조건부 고지를 싣는다(D-250 ②).
                        tier_caveat=campaign.tier_caveat())
    if not outcome.ran:
        # 환경 관문(계정·프로바이더·MLX)에서 멈췄다 — 구간을 돈 것이 아니므로 기록하지 않는다.
        say(f"\n구간 {target.segment_id} 을 시작하지 못했습니다 — 상태 파일은 바꾸지 않습니다.")
        return outcome.rc

    reasons = list(outcome.health.stop_reasons()) if outcome.health else ["건전성 판정 없음"]
    # 후단 관문: 설정을 바꿔도 결과가 한 건도 안 바뀌었다. **mock 은 제외한다** — 모의 응답은 arm 간
    # 같아 불일치 쌍이 늘 0이다(설계상). mock 에서 이걸 실패로 세면 리허설이 항상 멈춘다.
    if args.mode == "run" and outcome.all_unjudged:
        # plans/114 M-2 ③: 판정표 무효인데 측정 자격 문제(단·타임아웃·도달 불가)가 겹치면
        # 불일치 쌍이 있어도 그것은 축 효과가 아니다 — run 20260922-112010 은 불일치 17건
        # (A/A 노이즈)으로 「완료」가 됐고, 콘솔은 "워크로드를 먼저 고치세요"라고 말하면서
        # 다음 구간이 돌았다.
        kinds = ([] if not outcome.health else
                 (["사다리 단"] if outcome.health.tier_problems() else [])
                 + (["타임아웃률"] if outcome.health.timeout_problem() else []))
        kinds += ["도달 불가 arm"] if outcome.unreachable else []
        if outcome.discordant == 0:
            reasons.append("전 arm 판정 불가 · 불일치 쌍 합 0건 — 워크로드가 축에 닿지 않았다")
        elif kinds:
            reasons.append(
                f"전 arm 판정 불가(판정표 무효) · 불일치 쌍 합 {outcome.discordant}건 — 측정 자격 "
                f"문제({' · '.join(kinds)})가 겹쳐 불일치 쌍을 축 효과로 읽을 수 없다")
        else:
            say("\n판정표는 무효지만 측정 자격 문제(단·타임아웃·도달 불가)가 없다 — "
                "「완료 · 검정력 부족」으로 기록한다(1축 구간의 작은 효과는 판정 불가가 정상이다).")
    health = outcome.health
    record = campaign_mod.SegmentRecord(
        segment_id=target.segment_id, category=target.category, axes=list(target.axes),
        arm_ids=[], status="", mode=args.mode, structural=target.structural,
        baseline_tier=outcome.baseline_tier, config_fingerprint=outcome.config_fingerprint,
        commit=outcome.commit, dirty=outcome.dirty)
    # **구간 간 판 비교**(plans/114 M-2 ①b) — 단이 바뀌면 멈추고, 설정 지문·커밋 차이는 고지한다.
    # 앞 구간과 판이 다르면 구간 간 기준선 반복(D-239 노이즈 바닥)이 같은 조건의 반복이 아니다.
    seg_stop, seg_notes = campaign_mod.continuity(
        campaign.last_finished(exclude=target.segment_id), record)
    reasons += seg_stop
    for note in seg_notes:
        say(f"  구간 간 주의: {note}")
    campaign.records[target.segment_id] = campaign_mod.SegmentRecord(
        segment_id=target.segment_id, category=target.category, axes=list(target.axes),
        arm_ids=list(target.arm_ids) + list(target.substituted),
        substituted=list(target.substituted),
        status=campaign_mod.FAILED if reasons else campaign_mod.DONE,
        mode=args.mode, run_id=outcome.out_dir.name if outcome.out_dir else None,
        out_dir=str(outcome.out_dir) if outcome.out_dir else None, started_at=started,
        finished_at=datetime.now().isoformat(timespec="seconds"),
        elapsed_sec=round(outcome.elapsed_sec, 1), turns=health.turns if health else 0,
        est_hours=round(target.est_hours, 2), attempts=attempts,
        stop_reasons=reasons, resumed=bool(resume), structural=target.structural,
        baseline_tier=outcome.baseline_tier, config_fingerprint=outcome.config_fingerprint,
        commit=outcome.commit, dirty=outcome.dirty,
        health={} if not health else {
            "sql_rate": round(health.sql_rate, 4),
            "graph_entry_rate": round(health.graph_entry_rate, 4),
            "clarify_rate": round(health.clarify_rate, 4),
            "invalid_rate": round(health.invalid_rate, 4),
            "auto_answered_turns": health.auto_answered_turns,
            "baseline_order": list(health.baseline_order) if health.baseline_order else None,
            "verdicts": health.verdicts,
            "unevaluated": dict(health.unevaluated),
        })
    # **단 축 구간이 끝나면 이긴 단을 정한다**(plans/114 M-0 · D-250 ②) — 남은 구간의 기준선에
    # 주입될 값이다. 구간이 실패면 정하지 않는다(실패한 측정으로 기준선을 바꾸지 않는다).
    if target.structural:
        if campaign.records[target.segment_id].status == campaign_mod.DONE:
            campaign.tier_decision = _decide_tier_winner(campaign, target, outcome)
        else:
            campaign.tier_decision = {
                "segment_id": target.segment_id, "axis": axes_mod.LADDER_AXIS,
                "decided_at": datetime.now().isoformat(timespec="seconds"),
                "blocked": f"단 축 구간이 실패로 끝났다 — {' / '.join(reasons) or '사유 미기록'}"}
        for line in campaign_mod.render_tier_decision(campaign):
            say(line)
    path = campaign.save()

    # 상태가 바뀌었으니 남은 계획을 다시 짜서 합산 리포트를 낸다(실측 속도가 반영된다).
    # 스냅샷은 방금 뜬 것을 다시 쓴다 — 같은 호출 안에서 설정은 바뀌지 않았다.
    _, _, campaign, all_arms, categories, plan, _ = _campaign_context(args, snapshot=snapshot)
    report = _write_campaign_report(campaign, plan, categories, all_arms)
    record = campaign.records[target.segment_id]
    say()
    say(f"구간 {target.segment_id}: **{record.status}** · 실측 {record.elapsed_sec / 3600:.2f}시간"
        f" · 턴 {record.turns}건")
    for reason in record.stop_reasons:
        say(f"  실패 사유: {reason}")
    say(f"  상태 파일: {path}")
    if report:
        say(f"  합산 리포트: {report}")
    if record.status == campaign_mod.DONE:
        say(f"  남은 구간 {len(plan.segments)}개 · 추정 {plan.total_hours:.1f}시간 — "
            "다음은 `--segment next`.")
        return 0
    say("  다음 `--segment next` 는 이 구간에서 멈춥니다 — "
        "원인을 고친 뒤 이 구간 id 로 다시 돌리세요.")
    return 1


def _campaign_tier_lines(campaign: campaign_mod.Campaign) -> list[str]:
    """합산 리포트의 **단·판 고지**(plans/114 M-2 ①b·①c · D-250 ③).

    셋을 낸다.
      0. 단 축 판정 — **이긴 단과 주입 여부**(M-0 · D-250 ②). 아직 안 쟀거나 판정 불가면
         그 사실을 적는다.
      1. 끝난 구간의 기준선 단이 기준 경로(D-225)가 아니면 — 그 단에 **조건부**라는 고지.
         멈추지 않는다: 어느 단이 이기는지는 사다리 단 축(M-0)이 재야 할 결과다.
      2. 구간 사이에 단·설정 지문·커밋이 바뀌었으면 — 구간 간 기준선 반복이 같은 조건의
         반복이 아니라는 고지(노이즈 바닥 해석 조건).
    """
    finished = sorted((r for r in campaign.done() if r.finished_at),
                      key=lambda r: r.finished_at or "")
    if not finished:
        return []
    lines: list[str] = []
    decision = campaign.tier_decision
    blocked = campaign.tier_blocked()
    if decision and not blocked and decision.get("level"):
        env = " · ".join(f"`{k}={v}`" for k, v in sorted((decision.get("env") or {}).items()))
        tier = decision.get("tier") or "?"
        lines += [f"> **사다리 단 축 판정 — `{decision['level']}`(단 `{tier}`) 승**"
                  f"(구간 `{decision.get('segment_id')}` · 판정 「{decision.get('verdict')}」). "
                  f"{decision.get('sentence', '')} **남은 구간 기준선에 주입했다**: {env}.", "",
                  f"> {sweep_mod.TIER_AXIS_CAVEAT}", ""]
        # 재서 고른 승자가 아니라 기준 경로 고정이면(판정 불가) 그 조건을 합산 리포트에 싣는다.
        caveat = campaign.tier_caveat()
        if caveat:
            lines += [f"> ⚠ **{caveat}.**", ""]
    elif blocked:
        lines += [f"> **사다리 단 축 판정 불가 — 승자를 정하지 않았다.** {blocked} "
                  "남은 구간은 돌지 않는다(D-250 ② · 사람이 본다).", ""]
    canonical = sweep_mod.canonical_tier()
    off = [r for r in finished if r.baseline_tier and r.baseline_tier != canonical
           and not r.structural]
    if off:
        listing = " · ".join(f"`{r.segment_id}`={r.baseline_tier}" for r in off)
        lines += [f"> **사다리 단 축(plans/114 M-0 · D-250) 미측정** — 기준선 단이 기준 경로 "
                  f"`{canonical}`(D-225)가 아닌 구간: {listing}. 이 구간들의 축 결과는 **그 단에 "
                  "조건부**다(다른 단으로 옮겨지지 않는다). 실패는 아니다 — 어느 단이 이기는지는 "
                  "단 축 구간이 재야 할 값이다.", ""]
    notes: list[str] = []
    for previous, current in zip(finished, finished[1:]):
        stop, seen = campaign_mod.continuity(previous, current)
        notes += [f"`{current.segment_id}`: {note}" for note in stop + seen]
    if notes:
        lines += ["> **구간 사이에 판이 바뀌었다** — 구간 간 기준선 반복을 노이즈 바닥으로 읽을 때 "
                  "이 차이를 함께 본다: " + " · ".join(notes), ""]
    return lines


def _segment_row(record: campaign_mod.SegmentRecord, pass_rate: str) -> str:
    """합산 리포트 「구간」 표의 한 줄 — **기준선의 판**(단·설정 지문·커밋)을 함께 싣는다.

    구간 간 기준선 반복이 노이즈 바닥이 되려면 이 셋이 같아야 한다(plans/114 M-2 ①b). 옛 기록에는
    칸이 없으므로 `-` 로 둔다 — 「없음」과 「안 잼」을 구별한다.
    """
    dirty = " (dirty)" if record.dirty else ""
    return (f"| `{record.segment_id}` | {record.category} | {len(record.axes)} | "
            f"{record.elapsed_sec / 3600:.2f} | {pass_rate} | {record.baseline_tier or '-'} | "
            f"{record.config_fingerprint or '-'} | "
            f"{(record.commit[:8] + dirty) if record.commit else '-'} | `{record.run_id}` |")


def _campaign_optima(campaign, all_arms):
    """끝난 구간의 축 최적값을 모은다 — 합산 리포트와 `--propose` 가 같은 값을 본다.

    돌려주는 것: `[(구간 id, AxisOptimum)]` · 첫 구간 스냅샷 · `[(구간 id, 기준선 관측)]` ·
    구간 표 행 · 측정된 축 집합 · `{축: 미측정 사유}`(구간 폴더를 못 찾은 축). 실행을 생략한 레벨은
    같은 구간의 기준선 관측으로 채운다.

    구간 폴더는 기록된 절대 경로(`out_dir` — 실행 기계 기준)에서 먼저 찾고, 없으면 94 결과
    폴더의 `run_id` 로 찾는다(109·CS-18). 둘 다 없으면 그 구간의 축을 **측정된 축에서 빼고**
    사유를 돌려준다 — 종전에는 빈 관측이 「판정 불가」로 들어가 측정된 축으로 세졌다.
    """
    optima: list = []
    baselines: list = []
    seg_rows: list[str] = []
    first_snapshot = None
    measured: set[str] = set()
    missing: dict[str, str] = {}
    for record in campaign.done():
        tried = [Path(record.out_dir)] if record.out_dir else []
        if record.run_id:
            tried.append(_SCENARIO_RESULTS_DIR / record.run_id)
        run_dir = next((p for p in tried if (p / "raw.jsonl").exists()), None)
        if run_dir is None:
            reason = (f"미측정(구간 {record.segment_id} 폴더 없음 — "
                      f"{' · '.join(str(p) for p in tried) or '경로 기록 없음'})")
            for axis in record.axes:
                missing[axis] = reason
            seg_rows.append(_segment_row(record, "폴더 없음"))
            continue
        raw = run_dir / "raw.jsonl"
        grouped = sweep_mod.group_by_arm(sweep_mod.read_observations(raw))
        snapshot = sweep_mod.load_config_snapshot(run_dir / "config_snapshot.json")
        first_snapshot = first_snapshot or snapshot
        controls = snapshot.control_arms() if snapshot and not snapshot.unavailable else []
        unreachable = snapshot.unreachable_arms() if snapshot else {}
        arms = campaign_mod.segment_arms(all_arms, record.axes, sweep_mod.BASELINE_ARM)
        base = grouped.get(sweep_mod.BASELINE_ARM, [])
        substituted = [a for a in arms if a.arm_id in set(record.substituted)]
        for arm in substituted:        # 실행 생략 레벨 = 같은 구간의 기준선 관측
            grouped[arm.arm_id] = sweep_mod.baseline_as(base, arm.arm_id)
        for opt in compare.optima(grouped, arms,
                                  control_arms=sorted(set(controls) | set(record.substituted))):
            optima.append((record.segment_id,
                           sweep_mod.note_substitution(opt, substituted, unreachable)))
            measured.add(opt.axis)
        # **단 축 구간은 노이즈 바닥에 기여하지 않는다**(plans/114 M-0 · 벤치 소유 검토 ③).
        # 승자 주입으로 그 뒤 구간의 기준선 설정이 바뀌므로, 단 축 구간의 기준선 관측은 다른
        # 구간의 기준선 반복과 **같은 조건의 반복이 아니다**. 조용히 빼지 않고 리포트가 적는다.
        if base and not record.structural:
            baselines.append((record.segment_id, base))
        rate = sum(1 for o in base if o.passed) / len(base) * 100.0 if base else 0.0
        seg_rows.append(_segment_row(record, f"{rate:.1f}%"))
    return optima, first_snapshot, baselines, seg_rows, measured, missing


def _unevaluated_line(counts) -> str:
    """단언 미평가 턴의 사유별 건수(D-241). 0 도 적는다 — 「없음」과 「안 셌음」은 다르다."""
    counts = counts or {}
    return ("기능 분모 제외(단언 미평가 · D-241) — "
            f"무효 {counts.get('invalid', 0)} · 타임아웃 {counts.get('timeout', 0)} · "
            f"역질문 차단 {counts.get('clarify_blocked', 0)}턴. 완주율·지연 신호에는 남는다")


def _write_campaign_report(campaign, plan, categories, all_arms) -> Optional[Path]:
    """캠페인 합산 리포트 — 끝난 구간의 축 판정을 모은다. 미완·실패 축은 **미측정으로 남긴다.**

    축은 한 구간 안에서만 판정된다(레벨이 구간 사이로 갈리지 않으므로). 그래서 구간별 `optima` 를
    모으면 된다. 구간을 넘는 것은 **기준선 반복**뿐이고, 그것은 노이즈 바닥으로만 쓴다.
    """
    out_dir = campaign.path.parent
    optima, first_snapshot, baselines, seg_rows, measured, missing = _campaign_optima(
        campaign, all_arms)

    floor_pp = compare.noise_floor([b for _, b in baselines])
    drift = []
    if len(baselines) >= 2:
        ref_id, ref = baselines[0]
        ref_walls = [o.wall_ms for o in ref if o.wall_ms]
        ref_mean = (sum(ref_walls) / len(ref_walls)) if ref_walls else 0.0
        for seg_id, other in baselines[1:]:
            metric = compare.paired_metric(ref, other, "wall_ms")
            # 유의(CI 가 0 을 안 지남) **그리고** 의미 있는 크기일 때만 고지한다.
            if (metric is not None and not metric.crosses_zero and ref_mean
                    and abs(metric.mean_delta) >= campaign_mod.DRIFT_MIN_RATIO * ref_mean):
                drift.append(f"`{ref_id}`↔`{seg_id}` 기준선 지연 {metric.mean_delta:+.0f}ms "
                             f"(CI [{metric.ci_low:+.0f}, {metric.ci_high:+.0f}])")

    all_axes = sorted({a.axis for a in all_arms if a.axis})
    levels = {axis: tuple(sorted(a.level for a in all_arms if a.axis == axis)) for axis in all_axes}
    rows: list[str] = []
    everything: list = [opt for _, opt in optima]
    # **구조 축이 표의 맨 위다** — 다른 축의 결과가 그 단에 조건부이므로 먼저 읽어야 한다.
    for seg_id, opt in sorted(optima, key=lambda x: (x[1].axis != axes_mod.LADDER_AXIS,
                                                     x[1].axis)):
        best = f"`{opt.best_level}`" if opt.best_level else "—"
        sentence = opt.sentence
        if opt.axis == axes_mod.LADDER_AXIS:
            # D-250 주의 ① — 단 축 판정은 103 잔여와 대조해서 읽는다.
            # 벤치 소유 검토 ⑤ — 묶음 축의 효과 귀속을 같은 줄에 못 박는다.
            injected = campaign.tier_winner_env()
            sentence = (f"{sentence} · **주입**: "
                        + (" · ".join(f"`{k}={v}`" for k, v in sorted(injected.items()))
                           if injected else "없음(승자 미확정)")
                        + f" · ⚠ {sweep_mod.TIER_AXIS_ATTRIBUTION}"
                        + f" · ⚠ {sweep_mod.TIER_AXIS_CAVEAT}")
        rows.append(f"| `{opt.axis}` | {categories.get(opt.axis, '?')} | `{seg_id}` | "
                    f"{' · '.join(opt.levels)} | **{opt.verdict}** | {best} | {sentence} |")
    for axis in all_axes:
        if axis in measured:
            continue
        reason = (missing.get(axis) or campaign_mod.unmeasured_reason(axis, campaign, plan)
                  or "미측정")
        rows.append(f"| `{axis}` | {categories.get(axis, '?')} | — | {' · '.join(levels[axis])} | "
                    f"**{reason}** | — | 이 축은 아직 판정되지 않았다 — 표에서 빼지 않는다 |")
        everything.append(compare.AxisOptimum(
            axis=axis, levels=levels[axis], verdict=compare.UNDERPOWERED, best_level=None,
            signal="없음", sentence=reason))

    lines = [f"# 캠페인 합산 판정 — `{campaign.name}`", "",
             f"> 환경 {campaign.env} · 모드 {campaign.mode} · 완료 {len(campaign.done())}구간 · "
             f"실패 {len(campaign.failed())}구간 · 미완 {len(plan.segments)}구간 · "
             f"측정된 축 {len(measured)}/{len(all_axes)}", ""]
    if campaign.mode == "mock":
        lines += ["> **모의 캠페인이다.** 배관 리허설이며 설정 판단의 근거가 아니다.", ""]
    totals = {reason: 0 for reason in sweep_mod.UNEVALUATED_REASONS}
    for record in campaign.done():
        for reason, count in (record.health.get("unevaluated") or {}).items():
            totals[reason] = totals.get(reason, 0) + int(count)
    lines += [f"> {_unevaluated_line(totals)} — 끝난 구간 합", ""]
    skipped_total = sum(len(r.substituted) for r in campaign.done())
    if skipped_total:
        lines += [f"> 실행 생략 {skipped_total}개 arm — 실효 설정 지문이 기준선과 같아"
                  "(A/A 반복) 그 레벨은 **같은 구간의 기준선 관측**을 썼다. "
                  "구간 안의 A/A 노이즈 표본은 없고, 노이즈 바닥은 "
                  "아래 **구간 간 기준선 반복**으로 잰다.", ""]
    structural_done = [r for r in campaign.done() if r.structural]
    if structural_done:
        lines += ["> **단 축 구간은 노이즈 바닥에 기여하지 않는다**(plans/114 M-0) — "
                  + " · ".join(f"`{r.segment_id}`" for r in structural_done)
                  + ". 승자 주입으로 그 뒤 구간의 기준선 설정이 바뀌므로 같은 조건의 반복이 "
                  "아니다. 아래 반복 횟수가 적은 것은 그 때문이다.", ""]
    if len(baselines) >= 2:
        lines += [f"> 기준선 반복 {len(baselines)}회(구간마다 1회 · 단 축 구간 제외) — "
                  f"정확도 노이즈 바닥 **{floor_pp:.1f}%p**", ""]
        if floor_pp >= compare.MIN_MEANINGFUL_PP or drift:
            lines += ["> **구간 간 시간 교란이 크다** — "
                      "같은 설정(기준선)이 구간마다 다르게 나왔다. "
                      "구간이 다른 축끼리의 효과 크기는 비교하지 말 것."
                      + (" " + " · ".join(drift) if drift else ""), ""]
    lines += _campaign_tier_lines(campaign)
    lines += ["## 축 최적 레벨 — 구간별 판정 모음", "",
              "| 축 | 카테고리 | 구간 | 레벨 | 판정 | 최적 | 근거 |",
              "|---|---|---|---|---|---|---|"]
    lines += rows
    lines += ["", "## 구간", "",
              "| 구간 | 카테고리 | 축 | 실측 시간 | 기준선 통과율 | 기준선 단 | 설정 지문 | "
              "커밋 | run_id |",
              "|---|---|---:|---:|---:|---|---|---|---|"] + seg_rows
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "campaign_verdicts.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if first_snapshot is not None:
        _sweep_proposals(everything, first_snapshot, out_dir / "proposals")
    else:
        # 끝난 구간이 없어도 권고 파일은 낸다 — 전 축 「미측정」으로.
        optimize.write_proposals([], [], {}, out_dir / "proposals", optima=everything)
    return path


def _propose_campaign_inputs(args: argparse.Namespace):
    """`--propose` 가 읽을 캠페인 — 기본은 실 캠페인(`run-<env>`)이다. mock 리허설은 기본에서 뺀다.

    돌려주는 것: (캠페인 이름, 측정된 축의 `AxisOptimum` 목록, 첫 구간 스냅샷). 캠페인이 없거나
    끝난 구간이 없으면 목록이 비고, 처분은 종전처럼 트랙 T 신호만으로 나간다.
    """
    env, _ = sweep_mod.resolve_env(args.env)
    name = args.campaign or campaign_mod.default_name("run", env)
    path = campaign_mod.campaign_path(_RESULTS_DIR, name)
    if not path.exists():
        return name, [], None
    campaign = campaign_mod.Campaign.load_or_new(
        path, name=name, env=env, mode="run", repeat=args.repeat, max_hours=args.max_hours)
    if campaign.mode == "mock":
        say(f"      ※ `{name}` 은 모의 캠페인이다 — 배관 확인용이며 처분 근거가 아니다")
    try:
        optima, snapshot, *_, missing = _campaign_optima(campaign, sweep_mod.build_arms())
    except sweep_mod.SweepUnavailable as exc:
        say(f"      캠페인 합산을 읽지 못했다 — {exc}")
        return name, [], None
    for reason in sorted(set(missing.values())):
        # 109·CS-18: 폴더를 못 찾은 구간의 축은 처분 입력에서 빠진다 — 조용히 빼지 않는다.
        say(f"      ※ {reason} — 그 구간의 축은 처분 입력에서 빠진다")
    return name, [opt for _, opt in optima], snapshot


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
    name, camp_optima, camp_snapshot = _propose_campaign_inputs(args)
    verdicts = recommended = None
    if camp_optima:
        # **캠페인이 잰 축은 그 최적 레벨이 처분 입력이다**(D-238 번역 · D-239 구간 합산).
        verdicts, recommended = optimize.axis_disposition_inputs(camp_optima)
        say(f"      캠페인 `{name}` 합산 — 측정된 축 {len(camp_optima)}개의 "
            "최적 레벨을 처분에 쓴다")
    else:
        say(f"      캠페인 `{name}` 에 끝난 구간이 없다 — 트랙 T 신호만으로 처분한다")
    dispositions = optimize.build_from_validation(
        targets, integrity=integrity, shadowed=[], boot=[], consumption=[],
        verdicts=verdicts, recommended=recommended)

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
    paths = optimize.write_proposals(
        dispositions, evidences, pin, out_dir, optima=camp_optima,
        baseline_effective=camp_snapshot.baseline_env_values() if camp_snapshot else None)
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
    parser.add_argument("--env", choices=("auto", "closed", "sandbox"), default="auto",
                        help="워크로드 환경 (기본 auto — 활성 DB로 판정)")
    parser.add_argument("--user", help="질의용 벤치 계정 ID (인증이 켜진 서버에서 필수 · OS 환경변수 BENCH_USER_ID 로도 준다)")
    parser.add_argument("--password", help="질의용 벤치 계정 비밀번호 (OS 환경변수 BENCH_USER_PASSWORD 로도 준다)")
    parser.add_argument("--admin-user", help="운영자 ID (미지정 시 설정에서 읽는다)")
    parser.add_argument("--admin-password", help="운영자 비밀번호 (미지정 시 설정에서 읽는다)")
    parser.add_argument("--segment", metavar="next|<구간id>",
                        help="구간 캠페인 — 설정 카테고리별로 1회 --max-hours 이내 구간을 "
                             "하나 돈다. next=다음 미완 구간 · <구간id>=그 구간(실패 구간 "
                             "재시도). --mode dry 면 계획 표만")
    parser.add_argument("--campaign",
                        help="캠페인 이름 (기본: <mode>-<env> — mock 리허설과 실 캠페인이 "
                             "섞이지 않는다)")
    parser.add_argument("--max-hours", type=float, default=campaign_mod.DEFAULT_MAX_HOURS,
                        help="구간 1회 구동 상한(시간 · 기본 10). "
                             "계획은 안전 여유 10%%를 뺀 값까지 채운다")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.preflight:
        return cmd_preflight(args)
    if args.show_env:
        return cmd_show_env(args)
    if args.segment:
        return cmd_segment(args)
    if args.sweep:
        return cmd_sweep(args)
    if args.propose:
        return cmd_propose(args)
    return cmd_validate(args)


if __name__ == "__main__":
    raise SystemExit(main())
