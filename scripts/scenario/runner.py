"""실행 오케스트레이션 (plans/94 §4.2 · §4.3 · §7.1).

프로파일별로 서버를 한 번 띄우고, 그 아래에서 시나리오를 순차 실행하며, 건별 측정치를
JSONL 한 줄로 적재한다. **한 시나리오의 예외는 그 건만 ERROR 로 적재하고 다음으로 넘어간다** -
폐쇄망에서 전 스위트가 한 번에 끝나지 않는 것을 전제로 설계했다.
"""

from __future__ import annotations

import json
import platform
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from . import REPO_ROOT, run_capture, utf8_open
from .assertions import Observation, Verdict, evaluate_turn
from .catalog import Catalog, Scenario
from .client import ClientConfig, ScenarioClient
from .server import ProfileStatus, ServerHandle, pick_port, platform_provenance

RESULTS_ROOT = REPO_ROOT / "results" / "scenario"

# --estimate 의 LLM 호출 가정치. 실측이 아니라 **가정**이므로 출력에 그 사실을 함께 적는다.
# 1단 deep_agent 경로의 턴당 호출 수는 아직 측정되지 않았다 - S5 이후 이 값을 실측으로 바꾼다.
ASSUMED_LLM_CALLS_PER_TURN = 6


@dataclass
class RunConfig:
    """런 1회의 설정."""

    mode: str = "mock"                    # dry | mock | run
    env: str = "sandbox"                  # closed | sandbox
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


def estimate(catalog: Catalog, config: RunConfig) -> dict[str, Any]:
    """예상치를 낸다. 이 출력이 D-127 승인 요청의 근거다(§4.4 · G-4)."""
    selected = catalog.select(config.groups, config.only, config.env)
    per_group: dict[str, dict[str, Any]] = {}
    total_turns = 0
    total_ms = 0.0
    for scenario in selected:
        repeat = scenario.repeat or (3 if scenario.is_r_group else config.repeat)
        turns = len(scenario.turns) * repeat
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
        with utf8_open(self.path, "a") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
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
        "node_path": obs.node_path,
        "sse_events": sorted(set(obs.sse_events)),
        "progress_events": len(obs.progress_events),
        "executed_sql": obs.executed_sql,
        "row_count": obs.row_count,
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


def _teardown(scenario: Scenario) -> list[str]:
    """teardown 을 수행한다. **수행하지 못한 것을 조용히 넘기지 않는다**(§2-3).

    drop_thread 는 실행마다 새 thread_id 를 쓰는 것으로 이미 보장된다. 그 밖의 정리
    (유사어 등록 해제·스키마 캐시·폼필 기억)는 관리자 엔드포인트가 필요하고 실 서버에서
    검증하지 않았으므로 **미지원으로 기록**한다 - 리포트 10절에 사유와 함께 남는다.
    """
    unsupported: list[str] = []
    for action in scenario.teardown:
        if action == "drop_thread":
            continue
        unsupported.append(action)
    return unsupported


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
        # cold 캐시 시나리오를 군 맨 앞에 둔다(§2-3) - 순서가 결과를 바꾼다.
        ordered = sorted(
            grouped[profile], key=lambda s: (s.group, s.cache_state != "cold", s.id)
        )
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


def execute(catalog: Catalog, config: RunConfig) -> dict[str, Any]:
    """런 1회를 수행하고 산출 디렉터리 경로·요약을 돌려준다."""
    meta = run_meta(config, catalog)
    out_dir = RESULTS_ROOT / meta["run_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "logs").mkdir(exist_ok=True)
    (out_dir / "artifacts").mkdir(exist_ok=True)

    raw = RawLog(out_dir / "raw.jsonl")
    statuses: list[ProfileStatus] = []
    skipped: list[dict[str, Any]] = []
    executed = 0

    for profile, scenarios in iter_executions(catalog, config):
        overrides = dict(catalog.profiles.get(profile, {}))
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

            user_token, admin_token, login_reasons = (
                (None, None, []) if handle.mock else acquire_tokens(port, config)
            )
            status = verify_profile(handle, catalog.profiles.get(profile, {}), admin_token)
            status.reasons.extend(login_reasons)
            if status.auth_enabled and not user_token:
                # 여기서 끊지 않으면 시나리오 전건이 401 을 받아 "실행됐지만 전부 오류"인
                # 원시 로그가 쌓인다 - 실행되지 않은 것과 구별되지 않아 리포트가 거짓이 된다.
                status.valid = False
                status.reasons.append(
                    "AUTH_ENABLED=true 인데 질의용 사용자 토큰이 없다 - "
                    "/query 요청이 전건 401 로 끝난다. 크레덴셜을 넘기거나 "
                    "이 런에 AUTH_ENABLED=false 를 주입할 것"
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
    with ScenarioClient(client_config) as client:
        for scenario in scenarios:
            if not scenario.prompt_authored:
                # 원문이 산문이라 보낼 프롬프트가 없다. 실행하면 무의미한 결과에 돈만 나간다.
                skipped.append({
                    "scenario_id": scenario.id,
                    "reason": "프롬프트 미작성 - 원문이 산문(시나리오/방법 서술)이라 "
                              "사람이 실제 프롬프트로 고쳐 써야 한다",
                })
                continue
            repeats = scenario.repeat or (3 if scenario.is_r_group else config.repeat)
            for repeat in range(repeats):
                executed += _run_once(
                    catalog, config, meta, profile, scenario, repeat, client, raw, out_dir, skipped
                )
    return executed


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
) -> int:
    group = catalog.groups[scenario.group]
    # 실행마다 새 스레드를 쓴다 - drop_thread teardown 이 이것으로 보장된다.
    thread_id = f"scn-{scenario.id}-{repeat}-{uuid.uuid4().hex[:6]}"
    unsupported = _teardown(scenario)
    executed = 0

    for index, turn in enumerate(scenario.turns, start=1):
        if raw.already(profile, scenario.id, index, repeat):
            continue
        payload = dict(turn.send)
        payload["thread_id"] = thread_id
        endpoint = turn.endpoint or scenario.endpoint
        # 파일은 업로드 엔드포인트일 때만 싣는다. 답변 턴(JSON)에 파일을 다시 붙이면
        # 체크포인터에 복원된 양식 대신 새 업로드로 취급돼 역질문 상태가 끊긴다.
        upload = (
            (REPO_ROOT / scenario.upload)
            if (scenario.upload and endpoint in ("file", "file_stream"))
            else None
        )
        started = time.perf_counter()
        try:
            obs = client.send(endpoint, payload, upload)
        except Exception as exc:  # 한 건의 예외가 스위트를 멈추지 않는다
            obs = Observation(
                status="error",
                wall_ms=(time.perf_counter() - started) * 1000,
                error=f"러너 예외: {type(exc).__name__}: {exc}",
            )

        if obs.has_file and obs.file_name:
            if obs.query_id:
                saved = client.download(
                    obs.query_id,
                    out_dir / "artifacts",
                    f"{scenario.id}-{repeat}-{obs.file_name}",
                )
                if saved:
                    obs.artifacts.append(str(saved))

        verdict = evaluate_turn(scenario, index, turn, obs, group,
                                mock=(config.mode == "mock"))
        extras: dict[str, Any] = {}
        if unsupported:
            extras["teardown_unsupported"] = unsupported
        raw.append(_row(meta, profile, scenario, index, repeat, obs, verdict, extras))
        executed += 1

        if verdict.func in ("fail", "error") and index < len(scenario.turns):
            # 앞 턴이 깨지면 뒤 턴의 판정은 의미가 없다. 건너뛴 사실을 남긴다.
            for remaining in range(index + 1, len(scenario.turns) + 1):
                skipped.append(
                    {
                        "scenario_id": scenario.id,
                        "turn": remaining,
                        "reason": f"선행 턴 {index} 이 {verdict.func} - 후속 턴 판정 불가",
                    }
                )
            break

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
