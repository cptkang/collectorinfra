"""실행 오케스트레이션 (plans/94 §4.2 · §4.3 · §7.1).

프로파일별로 서버를 한 번 띄우고, 그 아래에서 시나리오를 순차 실행하며, 건별 측정치를
JSONL 한 줄로 적재한다. **한 시나리오의 예외는 그 건만 ERROR 로 적재하고 다음으로 넘어간다** -
폐쇄망에서 전 스위트가 한 번에 끝나지 않는 것을 전제로 설계했다.
"""

from __future__ import annotations

import asyncio
import json
import platform
import signal
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator, Optional

import yaml

from . import REPO_ROOT, clarify, run_capture, utf8_open
from .assertions import Failure, Observation, Verdict, evaluate_turn
from .catalog import Catalog, Scenario, Turn
from .client import ClientConfig, ScenarioClient
from .server import ProfileStatus, ServerHandle, pick_port, platform_provenance

RESULTS_ROOT = REPO_ROOT / "results" / "scenario"

# --estimate 의 LLM 호출 가정치. 실측이 아니라 **가정**이므로 출력에 그 사실을 함께 적는다.
# 1단 deep_agent 경로의 턴당 호출 수는 아직 측정되지 않았다 - S5 이후 이 값을 실측으로 바꾼다.
ASSUMED_LLM_CALLS_PER_TURN = 6

#: 모든 프로파일에 **똑같이** 주입하는 격리 설정. 측정 축이 아니다.
#:
#: `ALARM_ENABLED=true` 인 환경(폐쇄망 운영 `.env`)에서 기동한 프로파일 서버는 in-process
#: 알람 워커를 띄워 운영 스트림 `alarm:raw` 를 **운영과 같은 consumer group**
#: (`alarm-workers`)으로 XREADGROUP 한다. 그룹 안에서 메시지는 소비자끼리 나눠 가지므로
#: 벤치마크가 도는 동안 운영 알람 일부를 벤치 서버가 가져간다(2026-09-14 폐쇄망 스위프
#: 기동 로그 실측: "알람 워커 시작 … group=alarm-workers").
#: 이 플래그는 서버 기동·설정 재적용·워커 자신에서만 읽혀 질의 경로에 닿지 않는다.
#: 프로파일이 같은 키를 명시하면 프로파일 값이 이긴다(알람 자체를 시험하는 프로파일).
ISOLATION_ENV: dict[str, str] = {"ALARM_ENABLED": "false"}

#: 인증이 켜진 서버에서 `--user`/`--password` 가 없을 때 쓰는 내장 테스트 계정(사용자 확정
#: 2026-09-15 · D-216 - D-215 의 "94 CLI 는 --user/--password 만 받는다"를 개정). 옵션 없이
#: 돌려도 폐쇄망에서 질의가 401 로 끝나지 않게 한다. 인증이 꺼진 서버에서는 로그인하지 않는다.
DEFAULT_USER_ID = "5488923"
DEFAULT_USER_PASSWORD = "54889230"

#: 로컬 도커 샌드박스 DB(testdata/pg/init). 존이 붙은 DB 없이 이 DB가 활성이면 sandbox 다.
SANDBOX_DB_ID = "polestar"
DB_REGISTRY_PATH = REPO_ROOT / "config" / "db_registry.yaml"

#: 실패 트레이스 위치(src/observability/trace_writer.py `logs/trace/YYYY-MM-DD/<request_id>.jsonl`).
#: 서버는 러너와 같은 cwd(REPO_ROOT)로 뜨므로 같은 경로를 본다.
TRACE_ROOT = REPO_ROOT / "logs" / "trace"
SEED_DIR = REPO_ROOT / "config" / "synonym_seeds"

#: 부하 묶음(K군)의 행 번호 간격. 참조 시나리오·동시 세션마다 turn 을 겹치지 않게 벌린다(재개 키).
BUNDLE_TURN_STRIDE = 100

#: 실행 환경이 시나리오 선언과 달라도 판정하는 단언(D-216). 데이터에 기대지 않는 배관·안전
#: 단언만 남긴다 - 행 수·존·SQL 모양·역질문 여부는 그 환경의 데이터와 존 구성이 정한다.
ENV_NEUTRAL_KEYS = frozenset({
    "http_status", "sse_events", "node_path",
    "sql_must_not_match", "response_must_not_contain", "column_must_not_map",
    "manual_review",
})


@dataclass
class RunConfig:
    """런 1회의 설정."""

    mode: str = "mock"                    # dry | mock | run
    env: Optional[str] = None             # closed | sandbox | None = 자동 판정·전 시나리오(D-216)
    repeat: int = 1
    groups: list[str] = field(default_factory=list)
    only: list[str] = field(default_factory=list)
    profiles: list[str] = field(default_factory=list)
    port: Optional[int] = None
    token: Optional[str] = None           # 질의용 사용자 토큰(직접 주입 시)
    admin_token: Optional[str] = None     # 설정 에코용 운영자 토큰(직접 주입 시)
    user_id: Optional[str] = None         # 없으면 토큰을 로그인으로 받는다
    user_password: Optional[str] = None
    admin_user: Optional[str] = None
    admin_password: Optional[str] = None
    timeout_sec: float = 360.0
    run_id: str = ""
    resume_from: Optional[str] = None

    def resolved_run_id(self) -> str:
        if self.resume_from:
            return self.resume_from
        if self.run_id:
            return self.run_id
        # 경로 길이 260자 제한(부록 A.2)을 고려해 짧게 유지한다.
        return datetime.now().strftime("%Y%m%d-%H%M%S")


def run_meta(config: RunConfig, catalog: Catalog) -> dict[str, Any]:
    """provenance. 커밋·dirty·환경·플랫폼 없이 나온 결과는 재현할 수 없다(§4.5)."""
    # git 출력도 콘솔 코드페이지다(파일명에 한글이 있으면 cp949). run_capture 가 방어한다.
    commit = run_capture(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], timeout=5
    ).strip() or None
    dirty: Optional[bool] = None
    status = run_capture(["git", "-C", str(REPO_ROOT), "status", "--porcelain"], timeout=5)
    if commit is not None:
        dirty = bool(status.strip())

    provider = "(설정 로드 실패)"
    try:
        from src.config import load_config

        provider = load_config().llm.provider
    except Exception as exc:
        provider = f"(설정 로드 실패: {type(exc).__name__})"

    return {
        "run_id": config.resolved_run_id(),
        "mode": config.mode,
        "env": config.env,
        "provider": "mock" if config.mode == "mock" else provider,
        "commit": commit,
        "dirty": dirty,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repeat": config.repeat,
        "scenario_total": len(catalog.scenarios),
        "platform": platform_provenance(),
        "host": platform.node(),
    }


def zoned_db_ids(path: Path = DB_REGISTRY_PATH) -> set[str]:
    """레지스트리에서 존(zone)이 붙은 DB. 존은 운영 폴스타(은행존·공동존)에만 있다."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return set()
    found: set[str] = set()
    stack: list[Any] = [raw]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("db_id") and node.get("zone"):
                found.add(str(node["db_id"]))
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return found


def detect_env(active_db_ids: list[str], zoned: set[str]) -> Optional[str]:
    """활성 DB 로 실행 환경을 판정한다. 판정할 수 없으면 None 이다."""
    active = set(active_db_ids)
    if active & zoned:
        return "closed"
    if SANDBOX_DB_ID in active:
        return "sandbox"
    return None


def resolve_env(config: RunConfig) -> tuple[Optional[str], str]:
    """(실행 환경, 판정 출처). `--env` 명시가 이긴다.

    2026-09-14 폐쇄망 런은 `--env` 기본값 `sandbox` 로 돌아 closed 전용 158건이 빠지고,
    샌드박스 기대값(L군)이 운영 DB 에 적용됐다. 기본값을 없애고 서버가 읽는 설정으로 판정한다 -
    자식 서버는 이 프로세스와 같은 `.env`/`.encenv` 를 읽는다. 모의 실행은 데이터가 없어 판정하지 않는다.
    """
    if config.env:
        return config.env, "cli"
    if config.mode == "mock":
        return None, "mock"
    try:
        from src.config import load_config

        active = load_config().multi_db.get_active_db_ids()
    except Exception as exc:  # 판정 실패는 치명상이 아니다 - 환경 의존 단언을 보류할 뿐이다
        return None, f"판정 불가: 설정 로드 실패 ({type(exc).__name__})"
    env = detect_env(active, zoned_db_ids())
    return env, ("auto" if env else f"판정 불가: 활성 DB {active}")


def planned_turns(catalog: Catalog, scenario: Scenario, config: RunConfig) -> int:
    """예상치용 턴 수. 부하 묶음은 참조 시나리오 턴을 반복·세션 수만큼, 러너 동작은 1회로 센다."""
    if scenario.action:
        return 1
    spec = scenario.replay or scenario.concurrent
    refs = [ref for ref in (catalog.by_id(str(i)) for i in (spec.get("scenarios") or [])) if ref]
    if scenario.replay:
        return sum(len(ref.turns) for ref in refs) * int(scenario.replay.get("repeat") or 1)
    if scenario.concurrent and refs:
        return sum(
            len(refs[slot % len(refs)].turns)
            for sessions in scenario.concurrent.get("sessions") or []
            for slot in range(int(sessions))
        )
    return len(scenario.turns) * (scenario.repeat or (3 if scenario.is_r_group else config.repeat))


def estimate(catalog: Catalog, config: RunConfig) -> dict[str, Any]:
    """예상치를 낸다. 이 출력이 D-127 승인 요청의 근거다(§4.4 · G-4)."""
    selected = catalog.select(config.groups, config.only, config.env)
    per_group: dict[str, dict[str, Any]] = {}
    total_turns = 0
    total_ms = 0.0
    for scenario in selected:
        turns = planned_turns(catalog, scenario, config)
        total_turns += turns
        group = catalog.groups.get(scenario.group)
        target = scenario.target_ms or (group.latency_target_ms if group else 0)
        total_ms += turns * (target or 0)
        bucket = per_group.setdefault(
            scenario.group, {"scenarios": 0, "turns": 0, "r_group": 0}
        )
        bucket["scenarios"] += 1
        bucket["turns"] += turns
        if scenario.is_r_group:
            bucket["r_group"] += 1

    r_turns = sum(
        len(s.turns) * (s.repeat or 3) for s in selected if s.is_r_group
    )
    return {
        "scenarios": len(selected),
        "turns": total_turns,
        "r_group_turns": r_turns,
        "assumed_llm_calls_per_turn": ASSUMED_LLM_CALLS_PER_TURN,
        "estimated_llm_calls": total_turns * ASSUMED_LLM_CALLS_PER_TURN,
        "estimated_wall_sec_upper": round(total_ms / 1000.0, 1),
        "per_group": per_group,
        "profiles": sorted({s.profile for s in selected}),
        "note": (
            "LLM 호출 수는 실측이 아니라 가정치(턴당 "
            f"{ASSUMED_LLM_CALLS_PER_TURN}회)다. 소요 시간은 군 목표치의 합이라 상한에 가깝다. "
            "실측은 S5 이후 이 값을 대체한다."
        ),
    }


class RawLog:
    """raw.jsonl 적재기. 재개(resume)의 원본이다."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._done: set[tuple[str, str, int, int]] = set()
        # 동시 부하(K-06·K-07)의 작업 스레드가 함께 적재한다 - 한 줄이 섞이면 재개 원본이 깨진다.
        self._lock = threading.Lock()
        if path.exists():
            with utf8_open(path, "r") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self._done.add(
                        (
                            str(row.get("profile")),
                            str(row.get("scenario_id")),
                            int(row.get("turn", 0)),
                            int(row.get("repeat", 0)),
                        )
                    )

    def already(self, profile: str, scenario_id: str, turn: int, repeat: int) -> bool:
        return (profile, scenario_id, turn, repeat) in self._done

    def append(self, row: dict[str, Any]) -> None:
        line = json.dumps(row, ensure_ascii=False) + "\n"
        with self._lock:
            with utf8_open(self.path, "a") as handle:
                handle.write(line)
            self._done.add(
                (str(row["profile"]), str(row["scenario_id"]), int(row["turn"]), int(row["repeat"]))
            )


def _row(
    meta: dict[str, Any],
    profile: str,
    scenario: Scenario,
    turn_index: int,
    repeat: int,
    obs: Observation,
    verdict: Verdict,
    extras: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "run_id": meta["run_id"],
        "profile": profile,
        "env": meta["env"],
        "mode": meta["mode"],
        "repeat": repeat,
        "group": scenario.group,
        "scenario_id": scenario.id,
        "turn": turn_index,
        "plans": scenario.plans,
        "kind": scenario.kind,
        "pair_id": scenario.pair_with,
        "func_verdict": verdict.func,
        "perf_verdict": verdict.perf,
        "response_mode": verdict.response_mode,
        "forbidden_mode": verdict.forbidden_mode,
        "mode_evidence": verdict.mode_evidence,
        "failed_assertions": [f.as_dict() for f in verdict.failures],
        "manual_notes": verdict.manual_notes,
        "wall_ms": round(obs.wall_ms, 1),
        "processing_time_ms": obs.processing_time_ms,
        "ttfb_ms": obs.ttfb_ms,
        "max_event_gap_ms": obs.max_event_gap_ms,
        "node_elapsed_ms": obs.node_elapsed_ms,
        "node_calls": obs.node_calls,
        "node_path": obs.node_path,
        "sse_events": sorted(set(obs.sse_events)),
        "progress_events": len(obs.progress_events),
        "executed_sql": obs.executed_sql,
        "row_count": obs.row_count,
        # db_ids 단언이 실제로 무엇과 대조됐는지 원시 로그에 남긴다 — 없으면 판정을 검증할 수 없다
        # (2026-09-15: 이 칸이 없어 "db_ids 가 비었다"는 오판을 원시 로그로 반박하지 못했다).
        "db_ids": obs.db_ids,
        "llm_calls": obs.llm_calls,
        "tokens": obs.tokens,
        "retries": obs.retries,
        "node_count": obs.node_count,
        "artifacts": obs.artifacts,
        "error": obs.error,
    }
    if extras:
        row.update(extras)
    return row


#: 러너가 수행하는 teardown. `drop_thread` 는 실행마다 새 thread_id 로, `unregister_synonym` 은 턴 전후
#: 유사어 사전 스냅샷의 차이 - **이 시나리오가 더한 단어** - 만 지우는 것으로 보장한다(D-217).
SUPPORTED_TEARDOWN = frozenset({"drop_thread", "unregister_synonym"})


def _teardown(scenario: Scenario) -> list[str]:
    """teardown 을 수행한다. **수행하지 못한 것을 조용히 넘기지 않는다**(§2-3).

    drop_thread 는 실행마다 새 thread_id 를 쓰는 것으로 이미 보장된다. 그 밖의 정리
    (유사어 등록 해제·스키마 캐시·폼필 기억)는 관리자 엔드포인트가 필요하고 실 서버에서
    검증하지 않았으므로 **미지원으로 기록**한다 - 리포트 10절에 사유와 함께 남는다.
    """
    return [action for action in scenario.teardown if action not in SUPPORTED_TEARDOWN]


def _execution_order(scenario: Scenario) -> tuple[int, str, str]:
    """cold 캐시 시나리오를 프로파일 **맨 앞**에 둔다(§2-3) - 순서가 결과를 바꾼다.

    서버 기동 직후 첫 요청만 프로세스 메모리 캐시가 비어 있다. K-02(cold 첫 호출 근사, D-217)가
    그 자리를 차지해야 하므로 cold 부하 묶음이 가장 먼저이고, 나머지 cold 가 뒤를 잇는다.
    """
    cold = scenario.cache_state == "cold"
    rank = 0 if (cold and scenario.is_bundle) else 1 if cold else 2
    return rank, scenario.group, scenario.id


def iter_executions(
    catalog: Catalog, config: RunConfig
) -> Iterator[tuple[str, list[Scenario]]]:
    """프로파일별로 시나리오를 묶는다. 프로파일 1개 = 서버 기동 1회다(§3.5)."""
    selected = catalog.select(config.groups, config.only, config.env)
    if config.profiles:
        wanted = set(config.profiles)
        selected = [s for s in selected if s.profile in wanted]
    grouped: dict[str, list[Scenario]] = {}
    for scenario in selected:
        grouped.setdefault(scenario.profile, []).append(scenario)
    for profile in sorted(grouped):
        ordered = sorted(grouped[profile], key=_execution_order)
        yield profile, ordered


def resolve_admin_credentials(
    user: Optional[str], password: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    """운영자 크레덴셜을 채운다 - 명시값이 없으면 설정에서 읽는다.

    `ADMIN_USERNAME`/`ADMIN_PASSWORD` 는 이미 `.env`/`.encenv` 에 있다(운영 모드에서는
    없으면 기동 자체가 거부된다 - `_validate_production_secrets`). 개발자가 다시 타이핑할
    이유가 없다. **여기 한 곳에만 둔다** - 진입점마다 따로 읽으면 한쪽만 낡는다.
    """
    if user and password:
        return user, password
    try:
        from src.config import load_config

        admin = load_config().admin
        return (user or (admin.username or None), password or (admin.password or None))
    except Exception:
        # 설정 로드 실패는 치명상이 아니다 - 인증이 꺼진 서버는 토큰 없이 성립한다.
        return user, password


def acquire_tokens(
    port: int, config: RunConfig
) -> tuple[Optional[str], Optional[str], list[str]]:
    """이 기동에 쓸 (사용자 토큰, 운영자 토큰, 진단 사유)를 만든다.

    **프로파일마다 다시 받는다.** `AUTH_JWT_SECRET`/`ADMIN_JWT_SECRET` 이 `.env` 에
    명시돼 있지 않으면 설정 객체가 기동마다 난수로 만들어내므로(config.py 의
    `model_post_init`), 앞 프로파일에서 받은 토큰은 다음 프로파일에서 401 이다.
    런 1회에 한 번만 받는 설계는 arm 62개 중 1개만 맞는다.

    두 토큰은 **다른 시크릿으로 서명된다**(D-070). 질의(`require_user`)와 설정
    에코(`require_admin_user`)는 각자의 토큰을 요구하므로 한쪽만 받아서는 안 된다.
    """
    reasons: list[str] = []
    user_token, admin_token = config.token, config.admin_token
    if user_token and admin_token:
        return user_token, admin_token, reasons

    admin_user, admin_password = resolve_admin_credentials(
        config.admin_user, config.admin_password
    )
    client = ScenarioClient(ClientConfig(port=port))
    try:
        if not admin_token and admin_user and admin_password:
            admin_token, error = client.admin_login(admin_user, admin_password)
            if error:
                reasons.append(error)
        if not user_token and config.user_id and config.user_password:
            user_token, error = client.login(config.user_id, config.user_password)
            if error:
                reasons.append(error)
    finally:
        client.close()
    return user_token, admin_token, reasons


def login_default_user(port: int) -> tuple[Optional[str], Optional[str]]:
    """내장 테스트 계정으로 로그인한다(D-216). (토큰, 실패 사유)."""
    client = ScenarioClient(ClientConfig(port=port))
    try:
        token, error = client.login(DEFAULT_USER_ID, DEFAULT_USER_PASSWORD)
    finally:
        client.close()
    return token, (f"내장 테스트 계정({DEFAULT_USER_ID}) 로그인 실패: {error}" if error else None)


@contextmanager
def _terminate_as_exit() -> Iterator[None]:
    """SIGTERM(·SIGBREAK)을 SystemExit 으로 바꿔 프로파일별 `finally` 가 돌게 한다.

    프로파일 서버는 `start_new_session=True` 로 **별도 세션**에 띄운다(Ctrl+C 가 자식에게
    번지지 않게). 그 대가로 부모가 SIGTERM 에 죽으면 — `kill <pid>`, `timeout`, 작업
    스케줄러 종료 — 파이썬 기본 동작은 `finally` 없이 즉시 종료라 **자식이 고아로 남는다**.
    Ctrl+C(SIGINT)는 KeyboardInterrupt 라 괜찮았지만, nohup 장시간 런을 멈추는 방법은 kill 이다.
    2026-09-14 실측: 강제 종료된 런들에서 모의 서버 7개가 ppid=1 로 남아 있었다. 실 모드였다면
    Redis·DB 연결을 쥔 앱 서버다.

    첫 신호를 받으면 같은 신호를 무시로 돌려 **정리 도중 두 번째 kill 에 끊기지 않게** 한다.
    """
    if threading.current_thread() is not threading.main_thread():
        # signal.signal 은 메인 스레드에서만 걸린다 - 다른 스레드에서는 종전 동작 그대로.
        yield
        return
    names = ["SIGTERM"] + (["SIGBREAK"] if hasattr(signal, "SIGBREAK") else [])
    previous: dict[int, Any] = {}

    def _raise(signum: int, _frame: Any) -> None:
        signal.signal(signum, signal.SIG_IGN)
        raise SystemExit(128 + signum)

    for name in names:
        sig = getattr(signal, name)
        previous[sig] = signal.signal(sig, _raise)
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def execute(catalog: Catalog, config: RunConfig) -> dict[str, Any]:
    """런 1회를 수행하고 산출 디렉터리 경로·요약을 돌려준다.

    kill 로 멈춰도 그때까지의 원시 로그는 남고(추가 기록식) 같은 명령으로 이어받는다.
    """
    with _terminate_as_exit():
        return _execute(catalog, config)


def _execute(catalog: Catalog, config: RunConfig) -> dict[str, Any]:
    meta = run_meta(config, catalog)
    meta["env"], meta["env_source"] = resolve_env(config)
    out_dir = RESULTS_ROOT / meta["run_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "logs").mkdir(exist_ok=True)
    (out_dir / "artifacts").mkdir(exist_ok=True)

    raw = RawLog(out_dir / "raw.jsonl")
    statuses: list[ProfileStatus] = []
    skipped: list[dict[str, Any]] = []
    executed = 0

    for profile, scenarios in iter_executions(catalog, config):
        # 주입하는 것은 전부 에코로 확인한다 - 격리 설정도 예외가 아니다(.encenv 우선순위로
        # 조용히 무시되면 격리한 줄 알고 운영 알람을 계속 나눠 가진다).
        expected = {**ISOLATION_ENV, **catalog.profiles.get(profile, {})}
        overrides = dict(expected)
        # 운영 checkpoints.db(실측 82MB) 오염 금지 - 런 전용 체크포인트로 격리한다(§2-3).
        overrides["CHECKPOINT_DB_URL"] = str(out_dir / f"checkpoints-{profile}.db")
        port = pick_port(config.port)
        handle = ServerHandle(
            profile=profile,
            env_overrides=overrides,
            port=port,
            log_path=out_dir / "logs" / f"server-{profile}.log",
            mock=(config.mode == "mock"),
        )
        status = ProfileStatus(name=profile, port=port)
        try:
            handle.start()
            from .server import verify_profile

            # 로그인은 서버가 뜬 뒤에만 성립한다. 기동 중인 포트에 붙으면 ConnectError
            # (WinError 10061)로 토큰을 못 받아 설정 에코 401 -> 프로파일 INVALID 가 된다
            # (2026-09-14 폐쇄망 런 20260914-150834). 헬스 실패면 로그인하지 않는다 -
            # 사유는 verify_profile 의 "헬스 실패" 하나만 남아야 원인이 가려지지 않는다.
            healthy = handle.mock or handle.wait_healthy()[0]
            user_token, admin_token, login_reasons = (
                acquire_tokens(port, config) if healthy and not handle.mock else (None, None, [])
            )
            status = verify_profile(handle, expected, admin_token)
            status.reasons.extend(login_reasons)
            if status.auth_enabled and not user_token:
                # 계정을 넘기지 않았으면 내장 테스트 계정으로 로그인한다(D-216).
                user_token, error = login_default_user(port)
                if error:
                    status.reasons.append(error)
            if status.auth_enabled and not user_token:
                # 여기서 끊지 않으면 시나리오 전건이 401 을 받아 "실행됐지만 전부 오류"인
                # 원시 로그가 쌓인다 - 실행되지 않은 것과 구별되지 않아 리포트가 거짓이 된다.
                status.valid = False
                status.reasons.append(
                    "AUTH_ENABLED=true 인데 질의용 사용자 토큰이 없다 - "
                    "/query 요청이 전건 401 로 끝난다. 전용 벤치 계정을 --user/--password "
                    "로 넘길 것(인증을 끄고 재지 않는다 - plans/94 G-3)"
                )
            if not status.valid:
                for scenario in scenarios:
                    skipped.append(
                        {
                            "scenario_id": scenario.id,
                            "reason": f"프로파일 {profile} INVALID: {'; '.join(status.reasons)}",
                        }
                    )
                continue
            executed += _run_profile(
                catalog, config, meta, profile, scenarios, port, raw, out_dir, skipped,
                token=user_token,
            )
        except Exception as exc:
            # 기동 자체가 실패하면 그 프로파일이 리포트에서 통째로 사라진다 -
            # 사라진 프로파일은 "돌지 않았다"가 아니라 "없었다"로 읽힌다.
            status.reasons.append(f"기동/실행 예외: {type(exc).__name__}: {exc}")
            for scenario in scenarios:
                skipped.append({
                    "scenario_id": scenario.id,
                    "reason": f"프로파일 {profile} 기동 예외: {type(exc).__name__}: {exc}",
                })
        finally:
            statuses.append(status)
            handle.stop()
            if not handle.port_released():
                status.reasons.append(f"포트 {port} 가 회수되지 않았다 - 고아 프로세스 확인")

    summary = {
        "meta": meta,
        "profiles": [s.as_dict() for s in statuses],
        "executed_turns": executed,
        "skipped": skipped,
        "raw_path": str(out_dir / "raw.jsonl"),
        "out_dir": str(out_dir),
    }
    with utf8_open(out_dir / "run.json", "w") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    return summary


class SqlAuditTail:
    """서버 로그에서 턴 1회의 실행 SQL 을 모은다(D-217).

    오케스트레이션·멀티 DB 경로는 done 에 SQL 을 싣지 않는다(run 20260914-154940 93턴 전부 None).
    서버가 실행한 SQL 은 감사 로그 `query_executed`(src/security/audit_logger.py) 한 줄씩
    thread_id·원본 DB·행 수·retry_attempt 와 함께 표준출력에 남고, 러너가 그 출력을 이 파일로 받는다.
    턴 시작 시점의 파일 크기부터 읽고 thread_id 로 거르므로 다른 턴·동시 세션의 SQL 이 섞이지 않는다.
    """

    def __init__(self, path: Path, settle_sec: float = 2.0) -> None:
        self.path = path
        self._settle_sec = settle_sec

    def mark(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def _wait_flushed(self) -> None:
        """로그 펌프 스레드가 마지막 줄을 쓸 틈을 준다 - done 이 로그 줄보다 먼저 도착할 수 있다."""
        deadline = time.monotonic() + self._settle_sec
        size = self.mark()
        while time.monotonic() < deadline:
            time.sleep(0.2)
            current = self.mark()
            if current == size:
                return
            size = current

    def collect(self, since: int, thread_id: str) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        self._wait_flushed()
        with open(self.path, "rb") as handle:
            handle.seek(since)
            data = handle.read()
        entries: list[dict[str, Any]] = []
        for line in data.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line.startswith("{") or thread_id not in line or "query_executed" not in line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("event") != "query_executed" or record.get("thread_id") != thread_id:
                continue
            entries.append({
                "sql": str(record.get("sql") or ""),
                "source": record.get("source_name"),
                "row_count": record.get("row_count"),
                "success": record.get("success"),
                "retry_attempt": record.get("retry_attempt"),
            })
        return entries


def _apply_sql_audit(obs: Observation, entries: list[dict[str, Any]]) -> None:
    """감사 로그 수집분을 관측치에 얹는다 - SQL 목록과 재시도 회차(retry_attempt 최댓값)."""
    if not entries:
        return
    obs.executed_sqls = [entry["sql"] for entry in entries if entry["sql"]]
    attempts = [e["retry_attempt"] for e in entries if isinstance(e.get("retry_attempt"), int)]
    if attempts:
        obs.retries = max(obs.retries or 0, max(attempts))


def _trace_files() -> set[str]:
    """지금 있는 실패 트레이스 파일. 턴 전후 차이가 그 턴이 남긴 트레이스다(동시 부하에서는 섞일 수 있다)."""
    if not TRACE_ROOT.exists():
        return set()
    return {path.relative_to(TRACE_ROOT).as_posix() for path in TRACE_ROOT.glob("*/*.jsonl")}


def _with_redis(work: Callable[[Any, Any], Awaitable[Any]]) -> Any:
    """러너 프로세스에서 Redis 작업 1건을 한다(D-217). 매번 새 연결 - 이벤트 루프를 넘기지 않는다."""

    async def run() -> Any:
        from src.config import load_config
        from src.schema_cache.redis_cache import RedisSchemaCache

        config = load_config()
        cache = RedisSchemaCache(config.redis, config.schema_cache)
        await cache.connect()
        if not getattr(cache, "_connected", False):
            raise RuntimeError("Redis 에 연결하지 못했다")
        try:
            return await work(cache, config)
        finally:
            await cache.disconnect()

    return asyncio.run(run())


def apply_synonym_setup(steps: list[dict[str, Any]], *, remove: bool) -> list[str]:
    """setup 의 synonym_add 를 적용하거나(remove=False) **그 단어만** 되돌린다(remove=True)."""

    async def work(cache: Any, _config: Any) -> list[str]:
        results: list[str] = []
        for step in steps:
            if step.get("kind") != "synonym_add":
                continue
            db_id, column = str(step["db_id"]), str(step["column"])
            words = [str(word) for word in step["words"]]
            if remove:
                ok = await cache.remove_synonyms(db_id, column, words)
                results.append(f"삭제 {db_id} {column} {words}: {'완료' if ok else '대상 없음'}")
            else:
                ok = await cache.add_synonyms(db_id, column, words, source="operator")
                results.append(f"등록 {db_id} {column} {words}: {'완료' if ok else '실패'}")
        return results

    return _with_redis(work)


async def _synonym_shot(cache: Any, db_ids: list[str]) -> dict[str, dict[str, list[str]]]:
    """유사어 사전 스냅샷 - 글로벌 사전 + DB별 사전. 영역 -> {키: 정렬된 단어}."""
    shot = {"global": {k: sorted(set(v)) for k, v in (await cache.load_global_synonyms() or {}).items()}}
    for db_id in db_ids:
        per_db = await cache.load_synonyms(db_id) or {}
        shot[f"per_db:{db_id}"] = {k: sorted(set(v)) for k, v in per_db.items()}
    return shot


def synonym_additions(
    before: dict[str, dict[str, list[str]]], after: dict[str, dict[str, list[str]]]
) -> dict[str, dict[str, list[str]]]:
    """after 에만 있는 단어 - 기준선 이후 더해진 것. 원래 있던 단어는 결과에 들어오지 않는다."""
    added: dict[str, dict[str, list[str]]] = {}
    for area, keys in after.items():
        for key, words in keys.items():
            new = sorted(set(words) - set((before.get(area) or {}).get(key, [])))
            if new:
                added.setdefault(area, {})[key] = new
    return added


def snapshot_synonyms() -> dict[str, dict[str, list[str]]]:
    """unregister_synonym teardown 의 기준선을 뜬다(글로벌 + 활성 DB별 유사어 사전)."""

    async def work(cache: Any, config: Any) -> dict[str, dict[str, list[str]]]:
        return await _synonym_shot(cache, list(config.multi_db.get_active_db_ids()))

    return _with_redis(work)


def remove_synonym_additions(before: dict[str, dict[str, list[str]]]) -> list[str]:
    """기준선 이후 더해진 단어만 지운다 - 시나리오가 등록한 동의어를 되돌린다(A-10)."""

    async def work(cache: Any, config: Any) -> list[str]:
        after = await _synonym_shot(cache, list(config.multi_db.get_active_db_ids()))
        results: list[str] = []
        for area, keys in synonym_additions(before, after).items():
            for key, words in keys.items():
                if area == "global":
                    ok = await cache.remove_global_synonym(key, words)
                else:
                    ok = await cache.remove_synonyms(area.split(":", 1)[1], key, words)
                results.append(f"삭제 {area} {key} {words}: {'완료' if ok else '실패'}")
        return results

    return _with_redis(work)


def _cleanup_leftover_setup(scenarios: list[Scenario], run_env: Optional[str]) -> list[str]:
    """강제 종료로 남았을 수 있는 setup 을 실행 시작 때 먼저 지운다(사용자 확정 2026-09-15)."""
    steps = [
        step for scenario in scenarios if scenario.env in ("both", run_env)
        for step in scenario.setup
    ]
    if not steps:
        return []
    try:
        return apply_synonym_setup(steps, remove=True)
    except Exception as exc:  # 정리 실패가 스위트를 멈추지 않는다 - 사실은 run.json 에 남긴다
        return [f"사전 정리 실패: {type(exc).__name__}: {exc}"]


def run_seed_reload_idempotency() -> dict[str, Any]:
    """활성 DB 시드를 두 번 적재하고 전·1회차·2회차 스냅샷을 돌려준다(SYN-F-05 · 사용자 확정)."""

    async def work(cache: Any, config: Any) -> dict[str, Any]:
        from src.schema_cache.synonym_loader import SynonymLoader

        active = list(config.multi_db.get_active_db_ids())
        seeds = [(db_id, SEED_DIR / f"{db_id}.yaml") for db_id in active
                 if (SEED_DIR / f"{db_id}.yaml").exists()]
        loader = SynonymLoader(redis_cache=cache)

        async def snapshot() -> dict[str, dict[str, list[str]]]:
            shot: dict[str, dict[str, list[str]]] = {}
            for db_id, _path in seeds:
                per_db = await cache.load_synonyms(db_id) or {}
                shot[f"per_db:{db_id}"] = {k: sorted(set(v)) for k, v in per_db.items()}
            shot["eav_names"] = {
                k: sorted(set(v)) for k, v in (await cache.load_eav_name_synonyms() or {}).items()
            }
            shot["global"] = {
                k: sorted(set(v)) for k, v in (await cache.load_global_synonyms() or {}).items()
            }
            shot["column_values"] = {
                k: sorted(v) for k, v in (await cache.load_column_value_synonyms() or {}).items()
            }
            return shot

        async def load_all() -> list[str]:
            errors: list[str] = []
            for db_id, path in seeds:
                result = await loader.load_seed_yaml(str(path))
                if result.status == "error":
                    errors.append(f"{db_id}: {'; '.join(result.errors) or 'error'}")
            return errors

        before = await snapshot()
        errors = await load_all()
        first = await snapshot()
        errors += await load_all()
        second = await snapshot()
        return {"active": active, "seeded": [db_id for db_id, _ in seeds], "before": before,
                "first": first, "second": second, "errors": errors}

    return _with_redis(work)


def judge_seed_reload(
    before: dict[str, dict[str, list[str]]],
    first: dict[str, dict[str, list[str]]],
    second: dict[str, dict[str, list[str]]],
    load_errors: list[str],
) -> list[Failure]:
    """멱등성(2회차 적재 뒤 변화 없음)·무손실(적재 전 단어가 1회차 뒤에도 있음)을 판정한다."""
    failures: list[Failure] = []
    if load_errors:
        failures.append(Failure("seed_reload.load", "적재 오류 없음", load_errors))
    changed = sorted(area for area in set(first) | set(second) if first.get(area) != second.get(area))
    if changed:
        failures.append(Failure("seed_reload.idempotent", "2회차 적재 뒤 변화 없음", changed))
    lost: dict[str, list[str]] = {}
    for area, keys in before.items():
        for key, words in keys.items():
            missing = sorted(set(words) - set((first.get(area) or {}).get(key, [])))
            if missing:
                lost[f"{area}:{key}"] = missing
    if lost:
        sample = dict(list(lost.items())[:20])
        failures.append(Failure("seed_reload.lossless", "적재 전 단어 보존", sample))
    return failures


def _word_count(shot: dict[str, dict[str, list[str]]]) -> int:
    return sum(len(words) for area in shot.values() for words in area.values())


def _run_action(
    config: RunConfig,
    meta: dict[str, Any],
    profile: str,
    scenario: Scenario,
    raw: RawLog,
    skipped: list[dict[str, Any]],
) -> int:
    """질의가 아닌 러너 동작 1건(D-217). 행 1개로 적재한다."""
    if raw.already(profile, scenario.id, 1, 0):
        return 0
    if config.mode == "mock":
        skipped.append({
            "scenario_id": scenario.id,
            "reason": "모의 실행 - 러너 동작(Redis 쓰기)은 실 모드에서만 수행한다",
        })
        return 0
    obs = Observation(status="completed")
    verdict = Verdict()
    extras: dict[str, Any] = {"action": scenario.action.get("kind")}
    started = time.perf_counter()
    try:
        result = run_seed_reload_idempotency()
        verdict.failures = judge_seed_reload(
            result["before"], result["first"], result["second"], result["errors"]
        )
        if not result["seeded"]:
            verdict.manual_notes.append(
                f"활성 DB {result['active']} 에 시드 파일이 없어 적재하지 않았다 - 판정 불가"
            )
        words = {name: _word_count(result[name]) for name in ("before", "first", "second")}
        extras["seed_reload"] = {"active": result["active"], "seeded": result["seeded"],
                                 "words": words, "errors": result["errors"]}
        obs.response = (
            f"시드 재적재 {result['seeded']}: 단어 수 {words['before']} -> "
            f"{words['first']} -> {words['second']}"
        )
    except Exception as exc:  # Redis 미연결 등 - 오류로 적재하고 다음으로 넘어간다
        obs.status = "error"
        obs.error = f"러너 동작 실패: {type(exc).__name__}: {exc}"
    obs.wall_ms = (time.perf_counter() - started) * 1000
    verdict.func = (
        "error" if obs.error else "fail" if verdict.failures
        else "manual" if verdict.manual_notes else "pass"
    )
    raw.append(_row(meta, profile, scenario, 1, 0, obs, verdict, extras))
    return 1


def _as_member(bundle: Scenario, ref: Scenario) -> Scenario:
    """부하 묶음이 도는 참조 시나리오 1건. 행은 묶음 ID 로 적재하고 판정은 참조의 단언으로 한다."""
    return replace(
        ref, id=bundle.id, group=bundle.group, plans=bundle.plans,
        perf=bundle.perf or ref.perf, pair_with=None, repeat=None,
    )


def _bundle_note(bundle: Scenario) -> str:
    return str((bundle.turns[0].expect.get("manual_review") if bundle.turns else "") or "")


def _run_replay(
    catalog: Catalog,
    config: RunConfig,
    meta: dict[str, Any],
    profile: str,
    bundle: Scenario,
    client: ScenarioClient,
    raw: RawLog,
    out_dir: Path,
    skipped: list[dict[str, Any]],
    **kwargs: Any,
) -> int:
    """반복 측정(K-01·K-02·K-03·K-04). 참조 시나리오를 회차마다 새 스레드로 차례로 돈다."""
    refs = [catalog.by_id(str(ref_id)) for ref_id in bundle.replay["scenarios"]]
    executed = 0
    for iteration in range(int(bundle.replay.get("repeat") or 1)):
        for position, ref in enumerate(refs):
            assert ref is not None  # 카탈로그 교차 검증이 보장한다
            executed += _run_once(
                catalog, config, meta, profile, _as_member(bundle, ref), iteration, client, raw,
                out_dir, skipped, turn_offset=position * BUNDLE_TURN_STRIDE,
                extras_base={"replay_of": ref.id, "bundle_note": _bundle_note(bundle)}, **kwargs,
            )
    return executed


def _run_concurrent(
    catalog: Catalog,
    config: RunConfig,
    meta: dict[str, Any],
    profile: str,
    bundle: Scenario,
    client_config: ClientConfig,
    raw: RawLog,
    out_dir: Path,
    skipped: list[dict[str, Any]],
    **kwargs: Any,
) -> int:
    """동시 부하(K-06·K-07). 세션 수만큼 작업 스레드를 띄워 참조 시나리오를 번갈아 배정한다."""
    refs = [catalog.by_id(str(ref_id)) for ref_id in bundle.concurrent["scenarios"]]
    executed = 0
    for wave, sessions in enumerate(bundle.concurrent["sessions"]):
        members = [refs[slot % len(refs)] for slot in range(int(sessions))]

        def work(slot: int, ref: Optional[Scenario], wave: int = wave, sessions: int = sessions) -> int:
            assert ref is not None
            # 세션마다 별도 HTTP 클라이언트·스레드(thread_id)다 - 한 연결을 나눠 쓰면 동시성이 아니다.
            with ScenarioClient(client_config) as worker:
                return _run_once(
                    catalog, config, meta, profile, _as_member(bundle, ref), wave, worker, raw,
                    out_dir, skipped, turn_offset=slot * BUNDLE_TURN_STRIDE,
                    extras_base={"concurrent_of": ref.id, "sessions": int(sessions),
                                 "bundle_note": _bundle_note(bundle)},
                    **kwargs,
                )

        with ThreadPoolExecutor(max_workers=int(sessions)) as pool:
            executed += sum(pool.map(work, range(len(members)), members))
    return executed


def _run_profile(
    catalog: Catalog,
    config: RunConfig,
    meta: dict[str, Any],
    profile: str,
    scenarios: list[Scenario],
    port: int,
    raw: RawLog,
    out_dir: Path,
    skipped: list[dict[str, Any]],
    token: Optional[str] = None,
) -> int:
    executed = 0
    client_config = ClientConfig(
        port=port,
        token=token or config.token,
        timeout_sec=config.timeout_sec,
        artifact_dir=out_dir / "artifacts",
    )
    live = config.mode != "mock"
    kwargs: dict[str, Any] = {
        "preference": clarify.load_zone_preference(),
        "sql_tail": SqlAuditTail(out_dir / "logs" / f"server-{profile}.log") if live else None,
    }
    if live:
        cleanup = _cleanup_leftover_setup(scenarios, meta.get("env"))
        if cleanup:
            meta.setdefault("setup_cleanup", []).extend(cleanup)
    with ScenarioClient(client_config) as client:
        for scenario in scenarios:
            if not scenario.prompt_authored:
                # 보낼 프롬프트가 없거나(원문이 산문) 러너가 그 흐름을 표현하지 못한다
                # (반복·동시성·쓰기 선행 조건). 원인을 여기서 단정하지 않는다 - 사유는
                # 시나리오 파일의 해당 항목 주석에 적는다(k_load.yaml 선례).
                skipped.append({
                    "scenario_id": scenario.id,
                    "reason": "prompt_authored: false - 실행 불가 사유는 시나리오 파일의 "
                              "해당 항목 주석에 있다",
                })
                continue
            if scenario.action:
                executed += _run_action(config, meta, profile, scenario, raw, skipped)
                continue
            if scenario.replay:
                executed += _run_replay(
                    catalog, config, meta, profile, scenario, client, raw, out_dir, skipped, **kwargs
                )
                continue
            if scenario.concurrent:
                executed += _run_concurrent(
                    catalog, config, meta, profile, scenario, client_config, raw, out_dir,
                    skipped, **kwargs,
                )
                continue
            repeats = scenario.repeat or (3 if scenario.is_r_group else config.repeat)
            for repeat in range(repeats):
                executed += _run_once(
                    catalog, config, meta, profile, scenario, repeat, client, raw, out_dir, skipped,
                    **kwargs,
                )
    return executed


def _send(
    client: ScenarioClient, endpoint: str, payload: dict[str, Any], upload: Optional[Path]
) -> Observation:
    started = time.perf_counter()
    try:
        return client.send(endpoint, payload, upload)
    except Exception as exc:  # 한 건의 예외가 스위트를 멈추지 않는다
        return Observation(
            status="error",
            wall_ms=(time.perf_counter() - started) * 1000,
            error=f"러너 예외: {type(exc).__name__}: {exc}",
        )


def _answer_questions(
    client: ScenarioClient,
    scenario: Scenario,
    endpoint: str,
    upload: Optional[Path],
    thread_id: str,
    query: str,
    obs: Observation,
    preference: list[str],
    log: list[dict[str, Any]],
) -> Observation:
    """역질문에 결정적으로 답하며 턴을 끝까지 진행한다(D-216). 답을 받은 뒤의 관측치를 돌려준다.

    2026-09-14 폐쇄망 런은 93턴 중 48턴이 존 선택 역질문에서 끝나 기능까지 가지 못했다.
    같은 질문이 되풀이되면(답이 먹지 않았다) 멈추고 그 역질문을 그대로 판정한다 -
    무한 왕복을 막고, 답이 거부됐다는 사실을 판정에서 숨기지 않는다.
    """
    asked: set[str] = set()
    for _ in range(clarify.MAX_AUTO_ANSWERS):
        question = clarify.pending_question(obs)
        if question is None or question.signature in asked:
            break
        body = clarify.build_answer(
            question, override=scenario.auto_answer, preference=preference, last_query=query
        )
        if body is None:
            break
        asked.add(question.signature)
        answer_endpoint, resend_file = clarify.answer_endpoint(endpoint, question)
        log.append({
            **clarify.summarize_answer(question, body),
            "endpoint": answer_endpoint,
            "question_status": obs.status,
            "question_wall_ms": round(obs.wall_ms, 1),
        })
        obs = _send(
            client, answer_endpoint, {**body, "thread_id": thread_id},
            upload if resend_file else None,
        )
    return obs


def _hold_for_env(turn: Turn, scenario: Scenario, run_env: Optional[str]) -> Turn:
    """환경이 다른 시나리오의 데이터 의존 단언을 보류한다(D-216 - 사용자 확정 "실행하고 판정 보류").

    기대값은 선언한 환경의 데이터(샌드박스 픽스처 서버·존 구성)로 쓰였다. 다른 환경에서 그대로
    판정하면 거짓 불합격이 된다. 판정기는 고치지 않고 `manual_review` 로 넘겨 합격으로 세지 않는다.
    """
    held = sorted(key for key in turn.expect if key not in ENV_NEUTRAL_KEYS)
    expect = {key: value for key, value in turn.expect.items() if key in ENV_NEUTRAL_KEYS}
    note = (
        f"환경 불일치 - 시나리오 env={scenario.env}, 실행 env={run_env or '판정 불가'}. "
        f"데이터 의존 단언 {len(held)}종 보류({', '.join(held) or '없음'})"
    )
    original = expect.get("manual_review")
    expect["manual_review"] = f"{original} / {note}" if original else note
    return Turn(send=turn.send, expect=expect, endpoint=turn.endpoint, auto_answer=turn.auto_answer)


def _run_once(
    catalog: Catalog,
    config: RunConfig,
    meta: dict[str, Any],
    profile: str,
    scenario: Scenario,
    repeat: int,
    client: ScenarioClient,
    raw: RawLog,
    out_dir: Path,
    skipped: list[dict[str, Any]],
    preference: Optional[list[str]] = None,
    sql_tail: Optional[SqlAuditTail] = None,
    turn_offset: int = 0,
    extras_base: Optional[dict[str, Any]] = None,
) -> int:
    group = catalog.groups[scenario.group]
    # 실행마다 새 스레드를 쓴다 - drop_thread teardown 이 이것으로 보장된다.
    thread_id = f"scn-{scenario.id}-{repeat}-{uuid.uuid4().hex[:6]}"
    unsupported = _teardown(scenario)
    executed = 0
    zone_preference = clarify.load_zone_preference() if preference is None else preference
    run_env = meta.get("env")
    live = config.mode != "mock"
    # 모의 실행은 판정기가 이미 내용 단언을 보류한다(assertions._MOCK_VERIFIABLE).
    env_mismatch = live and scenario.env not in ("both", run_env)
    base_extras: dict[str, Any] = dict(extras_base or {})

    # 유사어 쓰기 시나리오(A-10)의 기준선. 선행 상태보다 먼저 떠야 되돌릴 범위가 정확하다.
    synonym_baseline: Optional[dict[str, dict[str, list[str]]]] = None
    if "unregister_synonym" in scenario.teardown and live and not env_mismatch:
        try:
            synonym_baseline = snapshot_synonyms()
        except Exception as exc:
            skipped.append({
                "scenario_id": scenario.id,
                "reason": f"유사어 기준선을 뜨지 못해 쓰기 시나리오를 실행하지 않았다 - 되돌릴 수 없다: "
                          f"{type(exc).__name__}: {exc}",
            })
            return 0

    # 선행 상태(K-10 고의 오매핑 유사어). 만들지 못하면 전제가 없는 측정이라 실행하지 않는다.
    setup_live = bool(scenario.setup) and live and not env_mismatch
    if scenario.setup and not setup_live:
        base_extras["setup"] = ["모의 실행 - setup 미수행" if not live else "환경 불일치 - setup 미수행"]
    if setup_live:
        try:
            base_extras["setup"] = apply_synonym_setup(scenario.setup, remove=False)
        except Exception as exc:
            skipped.append({
                "scenario_id": scenario.id,
                "reason": f"setup 실패 - 선행 상태를 만들지 못해 실행하지 않았다: "
                          f"{type(exc).__name__}: {exc}",
            })
            return 0

    last_obs: Optional[Observation] = None
    last_query = ""
    try:
        for index, turn in enumerate(scenario.turns, start=1):
            turn_no = turn_offset + index
            if raw.already(profile, scenario.id, turn_no, repeat):
                continue
            payload = clarify.complete_payload(dict(turn.send), last_obs, last_query)
            payload["thread_id"] = thread_id
            endpoint = turn.endpoint or scenario.endpoint
            # 파일은 업로드 엔드포인트일 때만 싣는다. 답변 턴(JSON)에 파일을 다시 붙이면
            # 체크포인터에 복원된 양식 대신 새 업로드로 취급돼 역질문 상태가 끊긴다.
            upload = (
                (REPO_ROOT / scenario.upload)
                if (scenario.upload and endpoint in ("file", "file_stream"))
                else None
            )
            sql_since = sql_tail.mark() if sql_tail else 0
            traces_before = _trace_files() if live else set()
            obs = _send(client, endpoint, payload, upload)
            auto_answers: list[dict[str, Any]] = []
            if turn.auto_answer and not clarify.expects_question(turn.expect):
                obs = _answer_questions(
                    client, scenario, endpoint, upload, thread_id,
                    str(payload.get("query") or ""), obs, zone_preference, auto_answers,
                )
            sql_entries = sql_tail.collect(sql_since, thread_id) if sql_tail else []
            _apply_sql_audit(obs, sql_entries)
            last_obs = obs
            last_query = str(payload.get("query") or last_query)

            if obs.has_file and obs.file_name:
                if obs.query_id:
                    saved = client.download(
                        obs.query_id,
                        out_dir / "artifacts",
                        f"{scenario.id}-{repeat}-{turn_no}-{obs.file_name}",
                    )
                    if saved:
                        obs.artifacts.append(str(saved))

            judged = _hold_for_env(turn, scenario, run_env) if env_mismatch else turn
            verdict = evaluate_turn(scenario, index, judged, obs, group,
                                    mock=(config.mode == "mock"))
            extras: dict[str, Any] = dict(base_extras)
            if unsupported:
                extras["teardown_unsupported"] = unsupported
            if auto_answers:
                # 무엇에 어떻게 답했는지가 판정을 검증하는 재료다 - 역질문 자체가 회귀인지도 여기서 본다.
                extras["auto_answers"] = auto_answers
            if env_mismatch:
                extras["env_mismatch"] = {"scenario_env": scenario.env, "run_env": run_env}
            if sql_entries:
                extras["executed_sqls"] = sql_entries
            if live:
                new_traces = sorted(_trace_files() - traces_before)
                if new_traces:
                    extras["trace_files"] = new_traces
            raw.append(_row(meta, profile, scenario, turn_no, repeat, obs, verdict, extras))
            executed += 1

            if verdict.func in ("fail", "error") and index < len(scenario.turns):
                # 앞 턴이 깨지면 뒤 턴의 판정은 의미가 없다. 건너뛴 사실을 남긴다.
                for remaining in range(index + 1, len(scenario.turns) + 1):
                    skipped.append(
                        {
                            "scenario_id": scenario.id,
                            "turn": turn_offset + remaining,
                            "reason": f"선행 턴 {turn_no} 이 {verdict.func} - 후속 턴 판정 불가",
                        }
                    )
                break
    finally:
        if setup_live:
            try:
                apply_synonym_setup(scenario.setup, remove=True)
            except Exception as exc:
                skipped.append({
                    "scenario_id": scenario.id,
                    "reason": f"setup 되돌리기 실패 - 다음 실행 시작 때 다시 지운다: "
                              f"{type(exc).__name__}: {exc}",
                })
        if synonym_baseline is not None:
            try:
                meta.setdefault("teardown_log", []).append({
                    "scenario_id": scenario.id, "repeat": repeat,
                    "unregister_synonym": remove_synonym_additions(synonym_baseline),
                })
            except Exception as exc:
                skipped.append({
                    "scenario_id": scenario.id,
                    "reason": f"유사어 되돌리기 실패 - 유사어 사전을 수동으로 확인할 것: "
                              f"{type(exc).__name__}: {exc}",
                })

    if unsupported:
        skipped.append(
            {
                "scenario_id": scenario.id,
                "reason": f"teardown 미지원: {', '.join(unsupported)} - 상태 오염 가능",
            }
        )
    return executed


def next_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def latest_run() -> Optional[Path]:
    if not RESULTS_ROOT.exists():
        return None
    runs = sorted((p for p in RESULTS_ROOT.iterdir() if p.is_dir()), reverse=True)
    return runs[0] if runs else None
