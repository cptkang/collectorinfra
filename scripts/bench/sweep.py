"""설정 축 스위프 — 94의 실행 원자 위에서 arm을 돌리고 축별로 비교한다 (plans/93 §4 · §1.5).

**러너를 다시 만들지 않는다.** 서버 기동·주입 에코 검증·시나리오 실행·원시 적재는
`scripts/scenario/`(plans/94 · D-212)가 이미 한다. 93이 더하는 것은 셋뿐이다.

    1. 축 → arm 전개      (`axes.expand_ofat`)
    2. arm을 프로파일로 주입 (94 `Catalog.profiles`는 이름→env 딕셔너리다 — arm과 같은 모양)
    3. 축별 **쌍체 비교**   (같은 시나리오 id 단위로 baseline과 대조 — `compare.py`)

경계(§1.5): 시나리오 카탈로그·실행 원자·판정기는 **94 소유**이고 93은 소비한다.
R군(복합·오용·실수·착각)은 쓰지 않는다 — 대응 등급은 설정의 함수가 아니라 프롬프트·가드의
함수라 arm 간 비교의 신호가 되지 못한다.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.bench import axes as axes_mod

BASELINE_ARM = "baseline"


class SweepUnavailable(RuntimeError):
    """94 하네스가 없어 스위프를 돌릴 수 없다."""


@dataclass(frozen=True)
class ArmSpec:
    """arm 1개 = 프로파일 1개 = 서버 기동 1회."""

    arm_id: str
    axis: Optional[str]
    level: Optional[str]
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    """시나리오 1건 실행 1회의 관측치 — 축 비교에 필요한 것만 추린다."""

    arm_id: str
    scenario_id: str
    repeat: int
    passed: bool
    wall_ms: Optional[float]
    llm_calls: Optional[int]
    tokens: Optional[int]
    retries: Optional[int]
    node_count: Optional[int] = None  # 실행 노드 수 — 비용 대리 지표(LLM 호출 수는 못 잰다)
    manual: bool = False        # 판정 보류 — 정확도 비교에서 제외한다
    completed: bool = True      # 오류·크래시·행 없이 끝났는가
    sql_generated: bool = False # executed_sql 이 실제로 나왔는가


@dataclass(frozen=True)
class Credentials:
    """스위프가 서버에 붙는 방법.

    둘 중 하나로만 성립한다 — 로그인하거나, 인증을 끄거나.
    **아무것도 하지 않으면 전건 401 이다**(2026-09-14 실측: 1984건 전량).
    """

    user_id: Optional[str] = None
    user_password: Optional[str] = None
    admin_user: Optional[str] = None
    admin_password: Optional[str] = None

    @property
    def can_login(self) -> bool:
        return bool(self.user_id and self.user_password)


def resolve_credentials(
    user_id: Optional[str] = None,
    user_password: Optional[str] = None,
    admin_user: Optional[str] = None,
    admin_password: Optional[str] = None,
) -> Credentials:
    """크레덴셜을 모은다. 개발자가 아무것도 타이핑하지 않아도 되는 것이 기본이다(§0.4).

    운영자 크레덴셜은 `.env`/`.encenv` 에 이미 있으므로 설정에서 읽는다. 사용자
    크레덴셜은 설정에 없다(인증 DB 소관) — 그래서 사용자가 명시하지 않으면 스위프는
    로그인 대신 **인증 우회 주입**(`auth_bypass_env`)으로 간다.
    """
    _, runner_mod = scenario_harness()
    admin_user, admin_password = runner_mod.resolve_admin_credentials(
        admin_user, admin_password
    )
    return Credentials(
        user_id=user_id,
        user_password=user_password,
        admin_user=admin_user,
        admin_password=admin_password,
    )


def auth_bypass_env() -> dict[str, str]:
    """로그인하지 않을 때 모든 arm에 **똑같이** 주입하는 값.

    인증은 측정 축이 아니고(축 선정 결과 50개 중 `AUTH_*` 0개) arm 전체에 동일하게
    걸리므로 비교의 신호를 흔들지 않는다. 다만 **조용히 끄지는 않는다** — arm env에
    실어 두면 94의 에코 검증이 이 주입까지 대조하므로, 꺼졌다는 사실이 리포트에 남는다.
    """
    return {"AUTH_ENABLED": "false"}


def scenario_harness():
    """94 하네스를 늦게 임포트한다.

    93은 94 없이도 **트랙 T(설정 검증)가 완결**되므로, 임포트 실패를 모듈 로드 시점의
    치명상으로 만들지 않는다. 스위프를 실제로 부를 때만 필요하다.
    """
    try:
        from scripts.scenario import catalog as sc_catalog
        from scripts.scenario import runner as sc_runner
    except Exception as exc:  # pragma: no cover - 환경 의존
        raise SweepUnavailable(
            "성능 스위프는 `scripts/scenario/`(plans/94)의 실행 원자 위에 섭니다. "
            f"임포트 실패: {type(exc).__name__}: {exc}"
        ) from exc
    return sc_catalog, sc_runner


def build_arms(
    selected_axes: Optional[Sequence[axes_mod.AxisCandidate]] = None,
    *,
    tier: str = "primary",
    limit: Optional[int] = None,
) -> list[ArmSpec]:
    """축에서 arm을 전개한다. 기본은 1차 축(동작 모드)만."""
    if selected_axes is None:
        found, _ = axes_mod.select_axes()
        selected_axes = [a for a in found if tier == "all" or a.tier == tier]
    if limit:
        selected_axes = list(selected_axes)[:limit]
    return [
        ArmSpec(arm_id=raw["arm_id"], axis=raw["axis"], level=raw["level"], env=dict(raw["env"]))
        for raw in axes_mod.expand_ofat(list(selected_axes), baseline_label=BASELINE_ARM)
    ]


#: 로컬 도커 샌드박스의 db_id. 이것 말고 다른 id가 활성이면 실 관측 DB를 보고 있다는 뜻이다.
SANDBOX_DB_IDS = frozenset({"polestar"})


def resolve_env(explicit: Optional[str] = None) -> tuple[str, str]:
    """워크로드 환경을 정한다. (env, 사유)를 돌려준다.

    **기본값이 틀리면 벤치마크가 통째로 빗나간다.** 94 카탈로그의 정상군 139건은
    `env` 로 갈려 있다 — `closed` 에 그룹 A~K 107건(실 질의 워크로드)이, `sandbox` 에
    그룹 L 32건(유사어·용어)이 들어 있다. 종전 기본값이 `sandbox` 여서 실 스위프가
    **유사어 32건만 재고 실 질의 워크로드를 한 건도 건드리지 않았다**(실측 2026-09-14).

    자동 판정은 활성 DB로 한다 — 로컬 샌드박스(`polestar`)만 붙어 있으면 `sandbox`,
    폴스타 실 DB가 하나라도 활성이면 `closed`. 근거가 없으면 추측하지 않고 사유를 적는다.
    """
    if explicit and explicit != "auto":
        return explicit, f"--env {explicit} 로 지정됨"
    try:
        from src.config import load_config

        active = [d for d in load_config().multi_db.get_active_db_ids() if d]
    except Exception as exc:
        return "closed", f"활성 DB를 읽지 못했다({type(exc).__name__}) — 폐쇄망 전제로 closed"
    if not active:
        return "closed", "ACTIVE_DB_IDS 미설정 — 폐쇄망 전제로 closed(§1.4)"
    real = [d for d in active if d not in SANDBOX_DB_IDS]
    if real:
        return "closed", f"실 관측 DB 활성({', '.join(real)}) — closed"
    return "sandbox", f"로컬 샌드박스만 활성({', '.join(active)}) — sandbox"


def load_normal_catalog(*, env: str = "closed"):
    """94 카탈로그에서 **정상 군만** 고른 사본을 만든다.

    R군을 빼는 것이 계약이다(§1.5) — 그것은 94가 답하는 질문이지 93이 답하는 질문이 아니다.
    """
    sc_catalog, _ = scenario_harness()
    catalog = sc_catalog.load_catalog()
    normal = catalog.select(kinds=["normal"], env=env)
    return sc_catalog.Catalog(
        groups=dict(catalog.groups),
        scenarios=list(normal),
        profiles={},   # arm으로 채운다
    )


def judgeable_count(catalog) -> tuple[int, int]:
    """(기계 단언이 있는 시나리오 수, 실행 대상 수).

    `manual_review` 뿐인 시나리오는 아무리 돌려도 **정확도 판정이 나오지 않는다** -
    판정기가 보류로 남기고 93이 정확도 비교에서 제외하기 때문이다. 이 비율이 낮으면
    스위프는 완주율·지연만 재는 것이고, 그 사실이 실행 전에 보여야 한다.
    """
    # 러너가 건너뛰는 것은 실행 대상이 아니다 — 프롬프트 미작성과 teardown 미지원(상태 오염).
    # 종전에는 앞의 것만 빼서 A-05·A-10(캐시 갱신·유사어 등록)을 실행 대상으로 셌다
    # (2026-09-14 모의 스위프: 두 건은 매 arm 에서 teardown 사유로 건너뛰었다).
    # 판정은 러너의 `_teardown` 을 그대로 쓴다 — 여기서 규칙을 따로 두면 한쪽만 낡는다.
    _, sc_runner = scenario_harness()
    runnable = [s for s in catalog.scenarios
                if s.prompt_authored and not sc_runner._teardown(s)]
    judged = sum(
        1 for s in runnable
        if any(set(t.expect) - {"manual_review"} for t in s.turns)
    )
    return judged, len(runnable)


def workload_summary(catalog) -> str:
    """돌릴 워크로드를 한 줄로 적는다. **무엇을 재는지 보이지 않으면 빗나가도 모른다.**"""
    import collections

    groups = collections.Counter(s.group for s in catalog.scenarios)
    names = ", ".join(
        f"{key}({count})" for key, count in sorted(groups.items())
    ) or "없음"
    judged, runnable = judgeable_count(catalog)
    pct = (judged / runnable * 100.0) if runnable else 0.0
    skipped = len(catalog.scenarios) - runnable
    tail = (f" · {skipped}건은 러너가 건너뛴다(프롬프트 미작성·teardown 미지원)"
            if skipped else "")
    return (f"시나리오 {len(catalog.scenarios)}건 — 그룹 {names}{tail}\n"
            f"            정확도 판정 가능 {judged}/{runnable}건({pct:.0f}%) "
            f"— 나머지는 완주율·지연만 잰다")


def fanout_scenarios(catalog, arms: Sequence[ArmSpec]):
    """**같은 시나리오를 모든 arm에 복제**한다 — 쌍체 비교의 전제다.

    94는 시나리오가 자기 프로파일을 지정한다(`Scenario.profile`, 기본 `baseline`).
    그 설계는 94의 질문(*"이 기능이 이 설정에서 동작하나"*)에 맞지만, 93의 질문
    (*"같은 질의가 설정 A와 B에서 어떻게 다른가"*)에는 맞지 않는다 — 그대로 두면
    baseline arm만 돌고 나머지는 대상 시나리오가 0건이 된다(2026-09-11 실측).

    그래서 시나리오를 arm 수만큼 복제하며 `profile`만 갈아끼운다. 시나리오 id는 유지해야
    쌍이 맺어지므로 **id는 건드리지 않는다**.
    """
    import dataclasses

    expanded = []
    for arm in arms:
        for scenario in catalog.scenarios:
            expanded.append(dataclasses.replace(scenario, profile=arm.arm_id))
    return expanded


def run_arms(
    arms: Sequence[ArmSpec],
    *,
    mode: str = "mock",
    env: str = "closed",
    repeat: int = 1,
    run_id: str = "",
    groups: Optional[list[str]] = None,
    credentials: Optional[Credentials] = None,
) -> dict[str, Any]:
    """arm 전체를 94 러너로 돌린다. 산출은 94 형식 그대로다(재분석 호환)."""
    sc_catalog, sc_runner = scenario_harness()
    creds = credentials or Credentials()
    extra = {} if creds.can_login else auth_bypass_env()

    catalog = load_normal_catalog(env=env)
    catalog.profiles = {arm.arm_id: {**extra, **arm.env} for arm in arms}
    catalog.scenarios = fanout_scenarios(catalog, arms)

    config = sc_runner.RunConfig(
        mode=mode,
        env=env,
        repeat=repeat,
        groups=list(groups or []),
        profiles=[arm.arm_id for arm in arms],
        run_id=run_id,
        user_id=creds.user_id,
        user_password=creds.user_password,
        admin_user=creds.admin_user,
        admin_password=creds.admin_password,
    )
    return sc_runner.execute(catalog, config)


def read_observations(raw_path: Path) -> list[Observation]:
    """94의 `raw.jsonl`을 축 비교용 관측치로 옮긴다.

    **턴 단위가 아니라 시나리오 단위**로 접는다 — 멀티턴 시나리오는 턴마다 행이 생기는데
    축 비교의 단위는 "이 시나리오가 통과했는가"다. 한 턴이라도 실패하면 실패로 본다.
    """
    folded: dict[tuple[str, str, int], dict[str, Any]] = {}
    if not raw_path.exists():
        return []
    for line in raw_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = (str(row.get("profile")), str(row.get("scenario_id")), int(row.get("repeat", 0)))
        cell = folded.setdefault(key, {"passed": True, "manual": False, "wall_ms": 0.0,
                                       "llm_calls": 0, "tokens": 0, "retries": 0, "seen": 0,
                                       "node_count": 0,
                                       "completed": True, "sql_generated": False})
        cell["seen"] += 1
        verdict = str(row.get("func_verdict"))
        if verdict == "manual":
            # 판정을 사람에게 남긴 턴 — 정확도 비교의 재료가 아니다.
            cell["manual"] = True
        elif verdict != "pass":
            cell["passed"] = False

        # **정확도와 별개로 항상 잴 수 있는 두 신호.**
        # 정상군 시나리오 32건은 전부 `manual_review` 뿐이라(실측 2026-09-14) 기계 단언이
        # 0개다 — 그 상태로는 정확도 쌍이 언제나 0쌍이고, 성공한 런조차 "판정 불가"만 낸다.
        # 완주 여부와 SQL 생성 여부는 사람이 옮겨 적지 않아도 원시 로그에 이미 있고,
        # **설정 축이 실제로 흔드는 것**이다(재시도 폭주·생성 실패·조기 종료).
        if verdict in ("error", "fail") or row.get("response_mode") in ("error", "crash", "hang"):
            cell["completed"] = False
        if str(row.get("executed_sql") or "").strip():
            cell["sql_generated"] = True
        for src, dst in (("wall_ms", "wall_ms"), ("llm_calls", "llm_calls"),
                         ("tokens", "tokens"), ("retries", "retries"),
                         ("node_count", "node_count")):
            value = row.get(src)
            if isinstance(value, (int, float)):
                cell[dst] += value

    return [
        Observation(
            arm_id=arm, scenario_id=scenario, repeat=repeat,
            passed=bool(cell["passed"]),
            manual=bool(cell["manual"]),
            wall_ms=cell["wall_ms"] or None,
            llm_calls=int(cell["llm_calls"]) or None,
            tokens=int(cell["tokens"]) or None,
            retries=int(cell["retries"]) or None,
            node_count=int(cell["node_count"]) or None,
            completed=bool(cell["completed"]),
            sql_generated=bool(cell["sql_generated"]),
        )
        for (arm, scenario, repeat), cell in sorted(folded.items())
    ]


@dataclass(frozen=True)
class RunHealth:
    """런이 **판정할 자격이 있는가**. 통계 이전에 답해야 하는 질문이다."""

    valid_profiles: int
    invalid_profiles: list[tuple[str, str]]
    turns: int
    verdicts: dict[str, int]
    evidence: list[tuple[str, int]]

    @property
    def error_turns(self) -> int:
        return self.verdicts.get("error", 0)

    @property
    def error_rate(self) -> float:
        return (self.error_turns / self.turns) if self.turns else 0.0

    def blocking_reason(self) -> Optional[str]:
        """판정을 내면 안 되는 사유. 없으면 None."""
        if self.valid_profiles == 0:
            return "유효한 프로파일이 0개다 — 어떤 arm도 검증을 통과하지 못했다"
        if self.turns == 0:
            return "실행된 턴이 0건이다"
        if self.error_rate >= 0.5:
            return (f"턴 {self.turns}건 중 {self.error_turns}건"
                    f"({self.error_rate:.0%})이 오류다 — 측정이 아니라 사고다")
        return None


def scan_health(result: dict[str, Any], raw_path: Path) -> RunHealth:
    """원시 로그와 프로파일 상태를 읽어 런의 건전성을 낸다.

    93이 이것을 먼저 보지 않으면, 전건 401 같은 사고가 "판정 불가 62건"이라는
    **정상처럼 보이는 리포트**로 나온다(2026-09-14 실측). 오류율은 통계의 입력이
    아니라 통계를 낼지 말지를 정하는 관문이다.
    """
    profiles = result.get("profiles") or []
    valid = sum(1 for p in profiles if p.get("valid"))
    invalid = [(str(p.get("name")), "; ".join(p.get("reasons") or []) or "(사유 없음)")
               for p in profiles if not p.get("valid")]

    verdicts: dict[str, int] = {}
    evidence: dict[str, int] = {}
    turns = 0
    if raw_path.exists():
        for line in raw_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            turns += 1
            verdicts[str(row.get("func_verdict"))] = verdicts.get(
                str(row.get("func_verdict")), 0) + 1
            mark = str(row.get("mode_evidence") or row.get("error") or "")[:80]
            if row.get("response_mode") in ("error", "crash", "hang") and mark:
                evidence[mark] = evidence.get(mark, 0) + 1

    top = sorted(evidence.items(), key=lambda kv: -kv[1])[:5]
    return RunHealth(valid_profiles=valid, invalid_profiles=invalid[:5],
                     turns=turns, verdicts=verdicts, evidence=top)


def group_by_arm(observations: Iterable[Observation]) -> dict[str, list[Observation]]:
    grouped: dict[str, list[Observation]] = {}
    for obs in observations:
        grouped.setdefault(obs.arm_id, []).append(obs)
    return grouped
