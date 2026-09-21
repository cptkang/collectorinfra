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
from scripts.bench import probe as probe_mod

BASELINE_ARM = "baseline"

#: 건전성 고지·구간 실패의 임계. `RunHealth.warnings()`(고지)와 `stop_reasons()`(캠페인 구간 실패)가
#: **같은 값을 쓴다** — 둘이 갈리면 화면에는 주의가 떴는데 구간은 완료로 기록되는 일이 생긴다.
GRAPH_ENTRY_WARN = 0.8      # 그래프 진입률이 이보다 낮으면
CLARIFY_WARN = 0.3          # 역질문 종료율이 이 이상이면
INVALID_STOP = 0.05         # 무효 턴 비율이 이를 넘으면 구간 실패(D-219 ③)

#: 러너 자신의 실패로 측정이 성립하지 않은 턴(94 `assertions.INVALID_VERDICT` · D-218).
#: 여기서 문자열로 두는 이유는 94 하네스가 없는 환경에서도 원시 로그를 읽을 수 있어야 하기
#: 때문이다(`read_observations` 는 `scenario_harness()` 를 부르지 않는다).
INVALID_VERDICT = "invalid"


def arm_of(row: dict[str, Any]) -> str:
    """행이 속한 arm. **원시 로그에서 arm 을 읽는 곳은 전부 이 함수를 쓴다.**

    36 세션과 확정한 `raw.jsonl` 계약(2026-09-21): 러너가 arm 덧씌우기를 하면 `arm`(덧씌운 arm id —
    `baseline`·`S2-…`)과 `base_profile`(시나리오 자신의 프로파일 — `optin_alarm` 등)을 따로 싣고,
    `profile` 에는 조합 이름(`optin_alarm+baseline` — 파일명이 되므로 ASCII `+`)을 둔다. 덧씌우기가
    없으면 `arm=null`·`profile=base_profile` 이다. 그 이전 행(run 20260914 등)에는 `arm` 칸이 없고
    `profile` 이 곧 arm 이다.
    """
    return str(row.get("arm") or row.get("profile"))


def sql_observed(row: dict[str, Any]) -> bool:
    """이 턴에 SQL 이 **관측**됐는가.

    `executed_sql`(done 페이로드 1건)만 보면 안 된다 — 오케스트레이션·멀티 DB 경로는 done 에
    SQL 이 실리지 않아 **구조적으로 항상 None** 이다(run 20260914-185540 의 6567턴 전건 실측).
    94 는 그래서 서버 감사 로그 `query_executed` 에서 모은 `executed_sqls` 를 따로 적재하는데
    (`runner.py:1615` · D-217), 93 이 그 칸을 읽지 않아 **SQL 생성률 신호가 영구 0** 이었다.
    """
    if str(row.get("executed_sql") or "").strip():
        return True
    entries = row.get("executed_sqls")
    if isinstance(entries, list):
        for entry in entries:
            text = entry.get("sql") if isinstance(entry, dict) else entry
            if str(text or "").strip():
                return True
    return False


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
    sql_generated: bool = False # SQL 이 실제로 관측됐는가(`executed_sql` 또는 `executed_sqls`)
    #: 측정이 성립하지 않은 턴이 섞였다(러너 인증 실패 등 · D-218 `invalid`).
    #: **기능 불합격이 아니다** — 정확도·완주 비교의 분모에서 뺀다.
    invalid: bool = False
    #: 그래프에 진입했는가(`node_path` 가 비지 않았는가). pre-gate 역질문·422 는 노드를
    #: 한 개도 밟지 않아 **설정 축이 작용할 여지가 없다** — 축 비교의 유효 표본이 아니다.
    entered_graph: bool = False


@dataclass(frozen=True)
class Credentials:
    """스위프가 서버에 붙는 방법.

    인증이 켜진 서버는 **전용 벤치 계정으로 로그인해야만** 잰다(plans/94 G-3 · 사용자 확정
    2026-09-15). 인증을 끄고 재면 인증 미들웨어와 사용자별 DB 범위가 빠져 운영과 다른 경로를
    잰다. **아무것도 하지 않으면 전건 401 이다**(2026-09-14 실측: 1984건 전량).
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
    크레덴셜은 설정에 없다(인증 DB 소관) — 인증이 켜진 서버라면 `--user`/`--password`
    또는 OS 환경변수 `BENCH_USER_ID`/`BENCH_USER_PASSWORD` 로 줘야 하고, 없으면
    스위프는 서버를 띄우기 전에 멈춘다(G-3).
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


def server_auth_enabled() -> Optional[bool]:
    """프로파일 서버가 인증을 켠 채로 뜨는가. 읽지 못하면 None.

    프로파일 서버는 이 프로세스의 OS 환경변수를 물려받고 같은 `.env`·`.encenv` 를 읽으며,
    arm 은 `AUTH_*` 를 주입하지 않는다(축 선정 결과 0개 · 격리 설정은 `ALARM_ENABLED` 뿐) —
    그래서 여기서 읽은 값이 곧 서버의 값이다. **서버를 띄우기 전에** 알아야 계정 없는 런을
    62 arm 기동 전에 멈출 수 있다.
    """
    try:
        from src.config import load_config

        return bool(load_config().auth.enabled)
    except Exception:
        return None


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

    **기본값이 틀리면 벤치마크가 통째로 빗나간다.** 94 카탈로그의 정상군은 `env` 로
    갈려 있다 — `closed` 에 그룹 A~K(실 질의 워크로드)가, `sandbox` 에 그룹 L(유사어·용어)이
    들어 있다. 종전 기본값이 `sandbox` 여서 실 스위프가 **유사어 시나리오만 재고 실 질의
    워크로드를 한 건도 건드리지 않았다**(실측 2026-09-14).

    **건수는 여기 적지 않는다.** 카탈로그는 바뀐다(`closed` 2026-09-14 107건 → 2026-09-21
    103건 — 그룹별 D 6→8 · F 8→9 · K 10→3). 산문에 박은 수치는 반드시 낡으므로 정본은
    `workload_summary()`·`judgeable_count()` 의 **동적 계산**이고 산문은 파생이다 —
    같은 드리프트가 `plans/93` v9→v10에서 이미 한 번 정정됐다.

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
    # 러너 동작 시나리오(D-217)는 워크로드가 아니다 - 부하 묶음(K-01·K-06 등)은 arm 마다 수십 턴을
    # 다시 돌리고, 시드 재적재(SYN-F-05)·고의 오매핑 유사어(K-10)는 공유 Redis 에 쓴다.
    normal = [
        s for s in catalog.select(kinds=["normal"], env=env)
        if not (s.is_bundle or s.action or s.setup)
    ]
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


def planned_turns_per_arm(catalog) -> int:
    """arm 1개가 돌릴 **턴 수** — 소요 시간 추정의 입력.

    시나리오 수가 아니라 턴 수를 센다. 멀티턴 시나리오가 있어 둘이 다르다
    (2026-09-21: 103건 · 129턴 — 모의 스위프 실측 1,161턴 ÷ 9 arm = 129 와 일치).
    실행 대상 판정은 `judgeable_count` 와 같은 러너 규칙이다(`prompt_authored` · teardown 지원).
    """
    _, sc_runner = scenario_harness()
    return sum(len(s.turns) for s in catalog.scenarios
               if s.prompt_authored and not sc_runner._teardown(s))


def axis_categories(tier: str = "primary") -> dict[str, str]:
    """축 id(env 키) → **설정 카테고리**(`KnobSpec.group_key`). 구간 분할의 기준이다."""
    found, _ = axes_mod.select_axes()
    return {a.env_key: a.group_key for a in found if tier == "all" or a.tier == tier}


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


# ── arm 실효 설정 스냅샷 (plans/93 §4.5) ────────────────────────────
#
# §4.5는 arm 마다 *"실효 설정 전체 스냅샷 — 우리가 주입했다고 믿는 값이 아니라 **자식이
# 실제로 읽은 값**"* 을 남기라고 정해 두었는데, `run.json` 의 arm 레코드는
# `echo_ok`·`echo_mismatch`(불일치만)까지만 담는다(실측 2026-09-21: 키 9개).
# 그래서 *"이 arm 의 주입값이 기준선 실효값과 같은가"* — 즉 **대조군인가** — 를 사후에
# 판정할 수 없었다. 여기서 93 자신이 그 스냅샷을 만든다.


@dataclass(frozen=True)
class ArmConfig:
    """arm 1개가 **실제로 읽게 될** 설정. 서버를 띄우기 전에 자식 프로세스로 확인한다."""

    arm_id: str
    axis: Optional[str]
    level: Optional[str]
    injected: dict[str, str]
    effective: dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    error: Optional[str] = None


@dataclass(frozen=True)
class ConfigSnapshot:
    """런 1회의 설정 provenance. `config_snapshot.json` 으로 산출물에 남긴다."""

    baseline: ArmConfig
    arms: dict[str, ArmConfig] = field(default_factory=dict)
    nondeterministic: frozenset[str] = frozenset()
    #: 스냅샷을 못 찍었을 때의 사유. 있으면 대조군 판정은 **하지 않는다**(추정 금지).
    unavailable: Optional[str] = None

    def fingerprint(self, arm_id: str) -> Optional[str]:
        """비결정 필드를 뺀 실효 설정 지문. 스냅샷이 없으면 None."""
        arm = self.baseline if arm_id == BASELINE_ARM else self.arms.get(arm_id)
        if arm is None or not arm.ok:
            return None
        return probe_mod.config_fingerprint(
            probe_mod.EchoResult(ok=True, config=arm.effective),
            exclude_keys=self.nondeterministic)

    def subset(self, arm_ids: Sequence[str]) -> ConfigSnapshot:
        """이 구간이 쓰는 arm 만 남긴 스냅샷 — 캠페인이 계획 때 뜬 것을 구간 실행에 넘길 때 쓴다
        (같은 호출 안에서 자식 에코를 두 번 뜨지 않는다)."""
        wanted = set(arm_ids)
        return ConfigSnapshot(baseline=self.baseline,
                              arms={k: v for k, v in self.arms.items() if k in wanted},
                              nondeterministic=self.nondeterministic, unavailable=self.unavailable)

    def baseline_env_values(self) -> dict[str, str]:
        """기준선 실효값을 **env 키로** 돌려준다 — `recommended.env.diff` 의 「현행값」.

        에코는 설정 경로(`composite.availability_precheck_enabled`)를 키로 쓰므로 카탈로그의
        `group_key`·`field_name` 으로 되짚는다. 접두 문자열을 깎으면 빗나가는 경우가 실재해
        (`API_PORT` → `server.port`) **`validate._dotted_of` 와 같은 규칙**을 쓴다.
        """
        if not self.baseline.ok:
            return {}
        try:
            from scripts.bench import catalog as cat_mod
            knobs = cat_mod.load_knobs()
        except Exception:
            return {}
        out: dict[str, str] = {}
        for knob in knobs:
            dotted = (knob.field_name if knob.group_key == "general"
                      else f"{knob.group_key}.{knob.field_name}")
            if dotted in self.baseline.effective:
                out[knob.env_key] = str(self.baseline.effective[dotted])
        return out

    def control_arms(self) -> list[str]:
        """**기준선과 실효 설정이 글자 그대로 같은 arm** — 주입이 아무것도 바꾸지 않았다.

        그 arm 이 내는 차이는 정의상 축 효과가 아니라 노이즈다. 판정표가 이것을 축 효과로
        렌더링하면 사람이 없는 효과를 읽는다(run 20260914-185540 에서 3건 실측).
        """
        base = self.fingerprint(BASELINE_ARM)
        if base is None:
            return []
        return sorted(arm_id for arm_id in self.arms
                      if self.fingerprint(arm_id) == base)

    def as_dict(self) -> dict[str, Any]:
        return {
            "baseline": {"effective": self.baseline.effective, "ok": self.baseline.ok,
                         "error": self.baseline.error},
            "arms": {
                arm_id: {"axis": arm.axis, "level": arm.level, "injected": arm.injected,
                         "ok": arm.ok, "error": arm.error, "effective": arm.effective}
                for arm_id, arm in sorted(self.arms.items())
            },
            "nondeterministic": sorted(self.nondeterministic),
            "control_arms": self.control_arms(),
            "unavailable": self.unavailable,
        }


def isolation_env() -> dict[str, str]:
    """러너가 모든 프로파일에 동일 주입하는 격리 설정(`runner.ISOLATION_ENV`).

    스냅샷을 이것 없이 찍으면 기준선 실효값이 실제 서버와 달라진다 — 94 정본을 읽어 쓴다.
    """
    try:
        _, sc_runner = scenario_harness()
        return dict(getattr(sc_runner, "ISOLATION_ENV", {}) or {})
    except Exception:
        return {}


def capture_config_snapshot(
    arms: Sequence[ArmSpec],
    *,
    base_env: Optional[dict[str, str]] = None,
    echo: Any = None,
    detect_nd: Any = None,
) -> ConfigSnapshot:
    """arm 마다 자식 파이썬을 띄워 **자식이 실제로 읽는 설정**을 모은다.

    서버를 띄우기 **전에** 돌린다(arm 당 1~2초 · 62 arm 이면 약 2분). 실패는 예외가 아니라
    데이터다 — 못 찍은 arm 은 `ok=False` 로 남기고, 기준선을 못 찍으면 전체를
    `unavailable` 로 표시해 **대조군 판정을 아예 하지 않는다**(추정 금지).
    """
    run_echo = echo or probe_mod.echo_config
    detect = detect_nd or probe_mod.detect_nondeterministic_keys
    shared = {**(base_env or {}), **isolation_env()}

    def _echo(overrides: Optional[dict[str, str]]) -> tuple[dict[str, Any], bool, Optional[str]]:
        """에코 1회. **실패도 예외도 데이터로 돌려준다** — 스냅샷은 provenance이지
        측정 자체가 아니므로, 여기서 죽으면 런이 통째로 날아간다(probe.py 와 같은 원칙)."""
        try:
            res = run_echo(overrides, base_env=shared) if overrides else run_echo(base_env=shared)
            if not getattr(res, "ok", False):
                return {}, False, f"{getattr(res, 'error_type', '?')}: {getattr(res, 'error', '')}"
            return dict(getattr(res, "config", {}) or {}), True, None
        except Exception as exc:
            return {}, False, f"{type(exc).__name__}: {str(exc)[:300]}"

    effective, ok, error = _echo(None)
    baseline = ArmConfig(arm_id=BASELINE_ARM, axis=None, level=None, injected={},
                         effective=effective, ok=ok, error=error)
    if not ok:
        return ConfigSnapshot(baseline=baseline, unavailable=(
            f"기준선 설정 에코 실패 ({error}) — 대조군 판정을 하지 않는다"))

    captured: dict[str, ArmConfig] = {}
    for arm in arms:
        if arm.arm_id == BASELINE_ARM:
            continue
        arm_effective, arm_ok, arm_error = _echo(dict(arm.env))
        captured[arm.arm_id] = ArmConfig(
            arm_id=arm.arm_id, axis=arm.axis, level=arm.level, injected=dict(arm.env),
            effective=arm_effective, ok=arm_ok, error=arm_error)

    try:
        nd = detect(base_env=shared)
    except Exception:
        nd = frozenset()
    return ConfigSnapshot(baseline=baseline, arms=captured, nondeterministic=nd)


def load_config_snapshot(path: Path) -> Optional[ConfigSnapshot]:
    """`config_snapshot.json` 을 되읽는다 — 캠페인 합산 리포트가 구간별 대조군·기준선 실효값을 쓴다.
    파일이 없거나 깨졌으면 None(추정하지 않는다)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    base = data.get("baseline") or {}
    baseline = ArmConfig(arm_id=BASELINE_ARM, axis=None, level=None, injected={},
                         effective=dict(base.get("effective") or {}), ok=bool(base.get("ok")),
                         error=base.get("error"))
    arms = {
        arm_id: ArmConfig(arm_id=arm_id, axis=info.get("axis"), level=info.get("level"),
                          injected=dict(info.get("injected") or {}),
                          effective=dict(info.get("effective") or {}), ok=bool(info.get("ok")),
                          error=info.get("error"))
        for arm_id, info in (data.get("arms") or {}).items()
    }
    return ConfigSnapshot(baseline=baseline, arms=arms,
                          nondeterministic=frozenset(data.get("nondeterministic") or ()),
                          unavailable=data.get("unavailable"))


def write_config_snapshot(snapshot: ConfigSnapshot, out_dir: Path) -> Path:
    """산출물에 남긴다. `raw.jsonl` 과 같은 폴더 — 사후 재분석의 입력이다."""
    path = out_dir / "config_snapshot.json"
    path.write_text(json.dumps(snapshot.as_dict(), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


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

    catalog = load_normal_catalog(env=env)
    # 인증 설정은 arm 에 싣지 않는다 — 인증이 켜진 서버는 벤치 계정으로 로그인한다(G-3).
    catalog.profiles = {arm.arm_id: dict(arm.env) for arm in arms}
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
        key = (arm_of(row), str(row.get("scenario_id")), int(row.get("repeat", 0)))
        cell = folded.setdefault(key, {"passed": True, "manual": False, "wall_ms": 0.0,
                                       "llm_calls": 0, "tokens": 0, "retries": 0, "seen": 0,
                                       "node_count": 0,
                                       "completed": True, "sql_generated": False,
                                       "invalid": False, "entered_graph": False})
        cell["seen"] += 1
        verdict = str(row.get("func_verdict"))
        if verdict == INVALID_VERDICT:
            # 측정이 성립하지 않은 턴(D-218). 불합격으로 세면 러너 결함이 기능 결함으로
            # 집계된다 — run 20260915-131903 에서 401 구간 31건이 그렇게 잡혔다.
            cell["invalid"] = True
        elif verdict == "manual":
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
        if sql_observed(row):
            cell["sql_generated"] = True
        if row.get("node_path"):
            cell["entered_graph"] = True
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
            invalid=bool(cell["invalid"]),
            entered_graph=bool(cell["entered_graph"]),
        )
        for (arm, scenario, repeat), cell in sorted(folded.items())
        if not cell["invalid"]
    ]


@dataclass(frozen=True)
class RunHealth:
    """런이 **판정할 자격이 있는가**. 통계 이전에 답해야 하는 질문이다."""

    valid_profiles: int
    invalid_profiles: list[tuple[str, str]]
    turns: int
    verdicts: dict[str, int]
    evidence: list[tuple[str, int]]
    #: SQL 이 관측된 턴 수(`executed_sql` 또는 `executed_sqls`).
    sql_turns: int = 0
    #: 그래프에 진입한 턴 수(`node_path` 비어 있지 않음).
    graph_turns: int = 0
    #: 역질문으로 끝난 턴 수(`response_mode == "clarify"`).
    clarify_turns: int = 0
    #: 측정이 성립하지 않은 턴 수(`func_verdict == "invalid"` · D-218).
    invalid_turns: int = 0
    #: 하네스가 역질문에 **대신 답한** 턴 수(`auto_answers` · D-216).
    #:
    #: 자동응답은 판정을 가능하게 하지만 **판정 대상을 일부 대체한다** — 존 선택은 전건
    #: 선호 존(기본 김포)을 고르고, 폼필은 전 필드 `blank`, 승인은 전건 `승인`이다.
    #: 그 턴의 라우팅·매핑·거부 판정은 제품이 아니라 하네스가 정한 값 위에서 내려진다.
    auto_answered_turns: int = 0
    #: 기준선 arm 이 몇 번째로 실행됐는가 / 전체 arm 수. 못 찾으면 None.
    baseline_order: Optional[tuple[int, int]] = None

    @property
    def error_turns(self) -> int:
        return self.verdicts.get("error", 0)

    @property
    def error_rate(self) -> float:
        return (self.error_turns / self.turns) if self.turns else 0.0

    @property
    def sql_rate(self) -> float:
        return (self.sql_turns / self.turns) if self.turns else 0.0

    @property
    def graph_entry_rate(self) -> float:
        return (self.graph_turns / self.turns) if self.turns else 0.0

    @property
    def clarify_rate(self) -> float:
        return (self.clarify_turns / self.turns) if self.turns else 0.0

    @property
    def invalid_rate(self) -> float:
        return (self.invalid_turns / self.turns) if self.turns else 0.0

    def warnings(self) -> list[str]:
        """판정을 막지는 않지만 **판정문에 함께 실려야 하는** 사실들.

        차단하지 않는 이유: 임계값이 워크로드 구성의 함수라 하나로 못 박으면 정상 런을
        막는다. 다만 **침묵은 금지**다 — 이 줄이 없으면 개발자는 34%가 그래프에 진입조차
        못 한 런과 정상 런을 구별할 수 없다(run 20260914-185540 실측).
        """
        notes: list[str] = []
        if self.turns and self.graph_entry_rate < GRAPH_ENTRY_WARN:
            notes.append(
                f"그래프 진입률 {self.graph_entry_rate:.0%} — 턴 "
                f"{self.turns - self.graph_turns}건이 노드를 하나도 밟지 않았다"
                f"(pre-gate 역질문·요청 거부). 그 턴에는 설정 축이 작용할 여지가 없다")
        if self.turns and self.clarify_rate >= CLARIFY_WARN:
            notes.append(
                f"역질문 종료율 {self.clarify_rate:.0%} — 워크로드가 역질문에서 멈췄다면 "
                f"축이 아니라 역질문 응답 경로를 재고 있다")
        if self.invalid_turns:
            notes.append(
                f"무효 턴 {self.invalid_turns}건({self.invalid_rate:.0%}) — 측정이 성립하지 "
                f"않은 턴이다. 비교의 분모에서 뺐다(D-218)")
        if self.turns and self.auto_answered_turns:
            rate = self.auto_answered_turns / self.turns
            notes.append(
                f"자동응답 {self.auto_answered_turns}건({rate:.0%}) — 하네스가 역질문에 "
                f"대신 답했다(존=선호 존 · 폼필=전 필드 공란 · 승인=승인). **그 턴의 라우팅·"
                f"매핑·거부 판정은 제품이 아니라 하네스가 정한 값 위에서 내려진다**")
        if self.baseline_order:
            position, total = self.baseline_order
            if total > 1 and position > 1:
                notes.append(
                    f"기준선 arm 이 {total}개 중 {position}번째로 실행됐다 — 쌍체 지연 비교가 "
                    f"실행 시각과 교란된다. 장시간 런일수록 지연 델타를 효과로 읽지 말 것")
        return notes

    def stop_reasons(self) -> list[str]:
        """캠페인 구간을 **「실패」로 기록할 사유** — 차단 + 멈춤 주의.

        실행 가이드 4단계 「멈춤」 표와 같은 목록이다. 자동응답 주의는 넣지 않는다 —
        역질문 자동응답은 이 하네스가 판정을 가능하게 하려고 넣은 것이라 실 실행에서는
        거의 항상 뜬다(해석 조건이지 멈춤 사유가 아니다). 전 arm 판정 불가(후단 관문)는
        판정 결과가 필요해 호출부가 따로 더한다.
        """
        reasons: list[str] = []
        blocking = self.blocking_reason()
        if blocking:
            reasons.append(f"차단 — {blocking}")
        if self.turns and self.graph_entry_rate < GRAPH_ENTRY_WARN:
            reasons.append(f"그래프 진입률 {self.graph_entry_rate:.0%} < {GRAPH_ENTRY_WARN:.0%}")
        if self.turns and self.clarify_rate >= CLARIFY_WARN:
            reasons.append(f"역질문 종료율 {self.clarify_rate:.0%} ≥ {CLARIFY_WARN:.0%}")
        if self.turns and self.invalid_rate > INVALID_STOP:
            reasons.append(f"무효 턴 {self.invalid_rate:.0%} > {INVALID_STOP:.0%}")
        if self.baseline_order and self.baseline_order[0] != 1:
            position, total = self.baseline_order
            reasons.append(f"기준선이 {total}개 중 {position}번째로 실행됐다")
        return reasons

    def blocking_reason(self) -> Optional[str]:
        """판정을 내면 안 되는 사유. 없으면 None."""
        if self.valid_profiles == 0:
            return "유효한 프로파일이 0개다 — 어떤 arm도 검증을 통과하지 못했다"
        if self.turns == 0:
            return "실행된 턴이 0건이다"
        if self.turns and self.invalid_turns >= self.turns:
            return (f"턴 {self.turns}건이 전부 무효다(D-218) — 측정이 성립하지 않았다")
        if self.error_rate >= 0.5:
            return (f"턴 {self.turns}건 중 {self.error_turns}건"
                    f"({self.error_rate:.0%})이 오류다 — 측정이 아니라 사고다")
        if self.turns and self.sql_turns == 0:
            # **임계값이 아니라 0이다.** 질의 벤치마크에서 SQL 이 한 건도 관측되지 않았다면
            # 파이프라인이 안 돌았거나 하네스가 SQL 을 못 읽은 것이고, 어느 쪽이든 축 비교의
            # 2순위 신호(SQL 생성률)가 전 arm 0 으로 고정돼 「차이 없음」을 만들어 낸다
            # (run 20260914-185540: 6567턴 전건 `executed_sql=null` · SQL 생성률 +0.0%p 62줄).
            return (f"턴 {self.turns}건 중 SQL 이 관측된 턴이 0건이다 — 파이프라인이 SQL 에 "
                    "닿지 못했거나 하네스가 실행 SQL 을 적재하지 못했다. 어느 쪽이든 SQL "
                    "생성률은 신호가 아니라 상수다")
        return None


def scan_health(result: dict[str, Any], raw_path: Path) -> RunHealth:
    """원시 로그와 프로파일 상태를 읽어 런의 건전성을 낸다.

    93이 이것을 먼저 보지 않으면, 전건 401 같은 사고가 "판정 불가 62건"이라는
    **정상처럼 보이는 리포트**로 나온다(2026-09-14 실측). 오류율은 통계의 입력이
    아니라 통계를 낼지 말지를 정하는 관문이다.

    **오류율만으로는 부족하다**(run 20260914-185540 실측): 그 런은 오류율 0.8%로 관문을
    통과했지만 SQL 관측 0건·그래프 미진입 34%·역질문 56%였고, 62 arm 전부 「판정 불가」가
    나왔다. 그래서 여기서 SQL 관측률·그래프 진입률·역질문률·무효율·기준선 실행 순서를
    함께 센다 — 차단은 SQL 0건과 전건 무효만, 나머지는 `warnings()` 로 고지한다.
    """
    profiles = result.get("profiles") or []
    valid = sum(1 for p in profiles if p.get("valid"))
    invalid = [(str(p.get("name")), "; ".join(p.get("reasons") or []) or "(사유 없음)")
               for p in profiles if not p.get("valid")]

    verdicts: dict[str, int] = {}
    evidence: dict[str, int] = {}
    turns = sql_turns = graph_turns = clarify_turns = auto_turns = 0
    arm_order: list[str] = []
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
            if sql_observed(row):
                sql_turns += 1
            if row.get("node_path"):
                graph_turns += 1
            if row.get("response_mode") == "clarify":
                clarify_turns += 1
            if row.get("auto_answers"):
                auto_turns += 1
            # 적재 순서가 곧 실행 순서다 — 러너는 프로파일 하나를 끝내고 다음으로 간다.
            profile = arm_of(row)
            if not arm_order or arm_order[-1] != profile:
                if profile not in arm_order:
                    arm_order.append(profile)

    baseline_order = ((arm_order.index(BASELINE_ARM) + 1, len(arm_order))
                      if BASELINE_ARM in arm_order else None)
    top = sorted(evidence.items(), key=lambda kv: -kv[1])[:5]
    return RunHealth(valid_profiles=valid, invalid_profiles=invalid[:5],
                     turns=turns, verdicts=verdicts, evidence=top,
                     sql_turns=sql_turns, graph_turns=graph_turns,
                     clarify_turns=clarify_turns,
                     invalid_turns=verdicts.get(INVALID_VERDICT, 0),
                     auto_answered_turns=auto_turns,
                     baseline_order=baseline_order)


def baseline_as(baseline: Sequence[Observation], arm_id: str) -> list[Observation]:
    """기준선 관측을 다른 arm 의 관측으로 **복사해 쓴다** — 실행을 생략한 대조군 레벨의 대체값.

    실효 설정 지문이 기준선과 같은 arm 은 기준선을 다시 도는 A/A 반복이다. 그 레벨과 다른 레벨의
    비교는 곧 「기준선 대 그 레벨」이고, 같은 구간·같은 시간대의 기준선이라 쌍체가 성립한다.
    잃는 것은 구간 안의 A/A 노이즈 표본 하나뿐이다 — 노이즈 바닥은 구간 간 기준선 반복으로
    잰다(캠페인 합산 리포트).
    """
    import dataclasses

    return [dataclasses.replace(o, arm_id=arm_id) for o in baseline]


def note_substitution(optimum: Any, substituted: Sequence[ArmSpec]) -> Any:
    """축 판정 문장에 **실행을 생략한 레벨**을 적는다 — 조용히 빼지 않는다."""
    import dataclasses

    levels = [a.level for a in substituted if a.axis == optimum.axis]
    if not levels:
        return optimum
    note = " · ".join(f"레벨 `{lv}` = 기준선과 동일 설정 → 기준선 관측 사용(실행 생략)"
                      for lv in levels)
    return dataclasses.replace(optimum, sentence=f"{optimum.sentence} · {note}")


def group_by_arm(observations: Iterable[Observation]) -> dict[str, list[Observation]]:
    grouped: dict[str, list[Observation]] = {}
    for obs in observations:
        grouped.setdefault(obs.arm_id, []).append(obs)
    return grouped
