"""구간 캠페인 — 한 번 구동을 `--max-hours`(기본 10시간) 이내로 끊어 설정 카테고리별로 돈다.

사용자 지시(2026-09-21): *"1회 구동시 최대 10시간 이내에서 구간별로 테스트가 진행되도록 config의
카테고리별로 나눠서 단계적으로 테스트가 될 수 있도록"*.

전 축을 한 번에 돌리면 약 98시간이다(run 20260914-185540 실측 93.4시간 · 62 arm). 게다가 스위프는
중단되면 처음부터 다시 돈다(`plans/109` 실행 가이드 A.7). 그래서 축을 **구간**으로 나누고, 구간마다
**자기 기준선을 맨 앞에** 둔 독립 스위프로 돌린다. 개발자는 같은 명령(`--segment next`)을 반복해서
치고, 진행 상태는 이 모듈이 **캠페인 상태 파일**에 남긴다.

지키는 제약 셋:

  **한 축의 모든 레벨은 같은 구간에 둔다.** 레벨 간 비교(`compare.compare_levels`)는 같은 시간대·
  같은 기준선이어야 성립한다. 레벨이 구간 사이로 갈리면 W-1(D-237)에서 없앤 시간 교란이 되살아난다.

  **추정이 예산을 넘는 구간은 만들지 않는다.** 한 축만으로 예산을 넘으면 구간을 만들지 않고
  「예산 초과」로 남긴다(조용히 빠뜨리지 않는다).

  **미완 구간은 저장하지 않고 매번 다시 짠다.** 완료·실패·진행 구간만 상태 파일에 고정한다.
  그래서 첫 구간의 실측 속도가 기본값과 다르면 남은 구간이 그 속도로 다시 나뉜다 —
  기본값이 과소 추정이어도 다음 구간부터는 예산을 지킨다.

구간 **안**에서 끊기면 다음 실행이 **같은 run 을 잇는다.** 구간을 시작하기 전에 run_id 를 「진행」
기록으로 남기고, 다음 `--segment next` 가 그 기록을 만나면 94 러너의 이어쓰기(`resume_from` —
성공한 턴은 건너뛰고 무효 턴은 다시 돈다)로 넘긴다.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

#: 한 번 구동의 기본 상한(시간) — 사용자 지시.
DEFAULT_MAX_HOURS = 10.0

#: 계획 단계의 안전 여유. 10시간 예산이면 **9시간까지만 채운다.**
SAFETY_MARGIN = 0.10

#: arm 1개 = 서버 기동 1회. 기동·로그인·설정 에코 비용(초).
BOOT_SEC = 30.0

#: 턴당 소요(초) — **run 20260914-185540 의 그래프 진입 턴 평균 77.4초**(4,332턴 · 중앙 58.7초).
#:
#: 전체 평균(51.1초)을 쓰지 않는다 — 그 런은 턴의 34%(2,235턴)가 **0.01초 만에 끝난 pre-gate
#: 역질문**이라 평균을 끌어내렸다. 이제는 역질문 자동응답이 들어가 그 턴들이 실제로 그래프를 탄다.
#: 역질문으로 끝나지 않은 그래프 진입 턴만 보면 99.9초다 — 이 값은 과대일 수 있어 기본은 77.4초로
#: 두고, **첫 구간을 가장 작게 짜서 실측으로 보정한다**(아래 `plan_segments(calibrate=True)`). 종전
#: 스위프 예고는 20초를 가정해 약 2.5배 짧았다.
DEFAULT_SEC_PER_TURN = 77.4

#: 모의 실행의 턴당 소요(초) — 2026-09-21 로컬 실측: 6분 10초 ÷ 1,161턴 = 0.32초.
MOCK_SEC_PER_TURN = 0.32

#: 구간 간 기준선 지연 차이를 「시간 교란」으로 고지하는 최소 효과 크기(기준선 평균 대비 비율).
#: 유의성만으로 고지하면 거의 결정적인 모의 응답에서 3ms 차이도 걸린다(2026-09-21 mock 실측) —
#: 효과 크기를 함께 본다(§5.3 「유의하지만 0.5%p 는 무의미」와 같은 원칙).
DRIFT_MIN_RATIO = 0.05

DONE = "완료"
FAILED = "실패"
PENDING = "미완"
#: 시작했지만 끝을 기록하지 못한 구간 — 돌고 있거나(pid 생존) 끊겼다(재개 대상).
RUNNING = "진행"


@dataclass(frozen=True)
class RateModel:
    """소요 시간 추정의 근거. 계획 표에 **숫자와 출처를 그대로** 찍는다."""

    sec_per_turn: float
    source: str
    turns_per_arm: int
    boot_sec: float = BOOT_SEC

    def arm_hours(self, repeat: int = 1) -> float:
        return (self.turns_per_arm * max(1, repeat) * self.sec_per_turn + self.boot_sec) / 3600.0

    def segment_hours(self, n_arms: int, repeat: int = 1) -> float:
        return n_arms * self.arm_hours(repeat)

    def describe(self) -> str:
        return (f"턴당 {self.sec_per_turn:.1f}초({self.source}) × arm 당 {self.turns_per_arm}턴 "
                f"+ 기동 {self.boot_sec:.0f}초 = arm 당 {self.arm_hours():.2f}시간")


@dataclass(frozen=True)
class Segment:
    """구간 1개 = 기준선 + 이 구간 축들의 모든 레벨 arm = 스위프 1회."""

    segment_id: str
    category: str
    axes: tuple[str, ...]
    arm_ids: tuple[str, ...]          # **실행할** 변이 arm (기준선 제외)
    est_hours: float
    #: 기준선과 실효 설정이 같아 **실행하지 않는** 변이 arm. 그 레벨의 관측은 같은 구간의 기준선
    #: 관측을 쓴다.
    substituted: tuple[str, ...] = ()
    calibration: bool = False          # 첫 구간 — 속도 보정용으로 일부러 작게 짰다
    #: **구조 축 구간**(plans/114 M-0 · D-250 ①) — 사다리 단처럼 노드 집합 자체를 바꾸는 축이다.
    #: 캠페인의 맨 앞에 오고, 보정 구간보다도 앞이다.
    structural: bool = False
    #: 예산을 넘는데도 만든 구간 — 구조 축은 **빼면 캠페인이 성립하지 않으므로** 「구간 불가」로
    #: 조용히 떨구지 않고 사유와 함께 사람에게 넘긴다(D-250 주의 ②).
    over_budget: bool = False

    @property
    def n_arms(self) -> int:
        return 1 + len(self.arm_ids)   # 기준선 포함 · 실행하는 것만


@dataclass(frozen=True)
class Plan:
    segments: tuple[Segment, ...]
    unplaceable: tuple[tuple[str, str], ...]   # (축, 사유)
    max_arms: int                               # 기준선 포함
    budget_hours: float                         # 안전 여유를 뺀 채움 상한
    max_hours: float
    rate: RateModel

    @property
    def total_hours(self) -> float:
        return sum(s.est_hours for s in self.segments)


def usable_hours(max_hours: float) -> float:
    return max_hours * (1.0 - SAFETY_MARGIN)


def plan_segments(
    arms: Sequence[Any],
    categories: Mapping[str, str],
    *,
    rate: RateModel,
    max_hours: float = DEFAULT_MAX_HOURS,
    repeat: int = 1,
    used_ids: Optional[Mapping[str, set[int]]] = None,
    controls: frozenset[str] = frozenset(),
    calibrate: bool = False,
    reasons: Optional[Mapping[str, str]] = None,
    first_axis: Optional[str] = None,
) -> Plan:
    """축을 카테고리별 구간으로 나눈다.

    - 카테고리 하나가 예산 안이면 **구간 하나**다. 넘으면 `<카테고리>-1`·`-2` … 로 나눈다.
    - 카테고리를 섞어 묶지 않는다 — 구간 = 설정 카테고리가 원칙이다. 기본 예산
      (9시간 채움 · arm 4개)에서는 소형 카테고리 둘을 묶으면 변이 4개로 한도 3을 넘어
      **묶을 수도 없다**(2026-09-21 실측).
    - 카테고리 안에서는 **레벨 수가 큰 축부터** 빈 구간에 채운다(first-fit decreasing).
      축은 쪼개지 않는다.
    - `used_ids` 는 상태 파일에 이미 있는 구간 번호다. 새로 짜는 구간은
      **쓰이지 않은 가장 작은 번호**를 차례로 받는다 — 완료된 구간과 이름이 겹치지 않고,
      속도가 그대로면 남은 구간의 이름도 그대로다
      (`composite-2` 가 끝나도 `composite-3` 은 `composite-3` 이다).

    - `controls` 는 **실효 설정 지문이 기준선과 같은 arm**(`ConfigSnapshot.control_arms`)이다.
      그 arm 은 기준선을 다시 도는 A/A 반복이라 **실행하지 않고**, 그 레벨의 관측은 같은 구간의
      기준선 관측으로 대신한다. 그래서 구간 크기(예산)는 **실행하는 arm 만** 센다. 판정은
      지문으로만 한다 — 비어 있으면(스냅샷 실패) 전부 돈다. 축 도달 불가 arm(plans/114 M-3)도
      호출부가 여기 넣는다 — 설정은 달라도 실효는 A/A 다.
    - `reasons` 는 생략 arm 의 사유(arm id → 문장 · 현재는 도달 불가만). 축의 모든 레벨이 생략돼
      구간을 만들 수 없을 때 「구간 불가」 사유로 그 문장을 쓴다 — A/A 문구로 덮지 않는다.
    - `calibrate=True` 면 **첫 구간을 가장 작게**(기준선 + 축 1개) 따로 떼어 맨 앞에 둔다.
      기본 속도는 추정이라 첫 구간은 예산을 채우지 않고 보정에 쓴다. 그 뒤 구간은 첫 구간의
      실측 속도·턴 수로 다시 짜인다.
    - `first_axis` 는 **구조 축**이다(사다리 단 · plans/114 M-0 · D-250 ①). 그 축만의 구간을
      **보정 구간보다도 앞에** 따로 떼어 맨 앞에 둔다. 단은 노드 집합을 바꿔 다른 축의 효과를
      조건부로 만들기 때문에, 그 뒤 구간은 전부 이 구간이 정한 단 위에서 돈다.
      예산을 넘어도 **구간 불가로 떨구지 않는다** — 빼면 캠페인 자체가 성립하지 않으므로
      `over_budget` 표시를 달아 호출부가 사람에게 사유와 함께 넘기게 한다.

    `arms` 원소는 `arm_id`·`axis`·`level` 속성만 있으면 된다(`sweep.ArmSpec`). 기준선은 무시한다.
    """
    budget = usable_hours(max_hours)
    # 부동소수 나눗셈 오차로 경계에서 한 칸 모자라지 않게 아주 작은 여유를 준다(9.0 // 1.8 == 4.0).
    arm_h = rate.arm_hours(repeat)
    max_arms = int(budget / arm_h + 1e-9) if arm_h > 0 else 0
    max_variants = max_arms - 1

    by_axis: dict[str, list[str]] = {}          # 실행하는 arm
    subs: dict[str, list[str]] = {}             # 기준선 관측으로 대신하는 arm
    for arm in arms:
        axis = getattr(arm, "axis", None)
        if not axis:
            continue
        arm_id = getattr(arm, "arm_id")
        by_axis.setdefault(axis, [])
        (subs.setdefault(axis, []) if arm_id in controls else by_axis[axis]).append(arm_id)

    by_category: dict[str, list[str]] = {}
    for axis in by_axis:
        by_category.setdefault(categories.get(axis, "unknown"), []).append(axis)

    segments: list[Segment] = []
    unplaceable: list[tuple[str, str]] = []

    def _segment(category: str, k: int, group: Sequence[str], calibration: bool = False,
                 structural: bool = False) -> Segment:
        arm_ids = tuple(arm_id for a in group for arm_id in by_axis[a])
        hours = rate.segment_hours(1 + len(arm_ids), repeat)
        return Segment(segment_id=f"{category}-{k}", category=category, axes=tuple(group),
                       arm_ids=arm_ids, est_hours=hours,
                       substituted=tuple(s for a in group for s in subs.get(a, ())),
                       calibration=calibration, structural=structural,
                       over_budget=structural and hours > budget)

    taken_all = {c: set(v) for c, v in (used_ids or {}).items()}

    def _take(category: str) -> int:
        """이 카테고리에서 아직 쓰지 않은 가장 작은 구간 번호."""
        taken = taken_all.setdefault(category, set())
        k = 1
        while k in taken:
            k += 1
        taken.add(k)
        return k

    def _pull(axis: str) -> None:
        """이 축을 남은 카테고리 목록에서 뺀다 — 뒤 구간이 다시 담지 않게."""
        cat = categories.get(axis, "unknown")
        by_category[cat] = [a for a in by_category.get(cat, []) if a != axis]
        if not by_category.get(cat):
            by_category.pop(cat, None)

    # **구조 축이 맨 앞이다**(D-250 ①) — 보정 구간보다도 앞이다. 보정은 속도 추정을 고치는
    # 것이고 구조 축은 남은 구간 전부의 기준선을 정하므로, 순서가 뒤바뀌면 보정 구간이
    # 「어느 단인지 모르는 단」 위에서 돈다.
    if first_axis and by_axis.get(first_axis):
        cat = categories.get(first_axis, "unknown")
        segments.append(_segment(cat, _take(cat), [first_axis], structural=True))
        _pull(first_axis)

    if calibrate:
        # 보정 구간: 실행 arm 이 가장 적은 축 하나. 같은 폭이면 **축이 적은 카테고리**의 축을 골라
        # 그 카테고리가 한 구간으로 끝나게 한다(이름이 카테고리와 1:1 로 남는다).
        # 구조 축은 이미 자기 구간을 받았다 — 후보에서 뺀다(카테고리 목록에서도 빠져 있다).
        fitting = [a for a in by_axis
                   if a != first_axis and 0 < len(by_axis[a]) <= max_variants]
        if fitting:
            pick = min(fitting, key=lambda a: (len(by_axis[a]),
                                               len(by_category.get(categories.get(a, "unknown"),
                                                                   [])),
                                               categories.get(a, "unknown"), a))
            cat = categories.get(pick, "unknown")
            segments.append(_segment(cat, _take(cat), [pick], calibration=True))
            _pull(pick)

    for category in sorted(by_category):
        bins: list[list[str]] = []
        order = sorted(by_category[category], key=lambda a: (-len(by_axis[a]), a))
        for axis in order:
            width = len(by_axis[axis])
            if width == 0:
                # 모든 레벨이 기준선과 같은 설정이다 — 비교할 것이 없다. 조용히 빼지 않는다.
                known = reasons or {}
                why = sorted({known[a] for a in subs.get(axis, ()) if a in known})
                unplaceable.append((axis, " · ".join(why) if why else
                                    "모든 레벨의 실효 설정이 기준선과 같다 — 비교할 레벨이 없다"))
                continue
            if width > max_variants:
                need = rate.segment_hours(1 + width, repeat)
                unplaceable.append((axis, (
                    f"레벨 {width}개 + 기준선 = arm {1 + width}개 · 추정 {need:.1f}시간 — "
                    f"채움 상한 {budget:.1f}시간"
                    f"(예산 {max_hours:g}시간의 {1 - SAFETY_MARGIN:.0%})을 넘는다")))
                continue
            for group in bins:
                if sum(len(by_axis[a]) for a in group) + width <= max_variants:
                    group.append(axis)
                    break
            else:
                bins.append([axis])

        taken = taken_all.setdefault(category, set())
        k = 0
        for group in bins:
            k += 1
            while k in taken:
                k += 1
            taken.add(k)
            segments.append(_segment(category, k, group))

    # **가장 짧은 구간이 먼저 돈다** — 첫 구간이 곧 관문(종전 smoke 단계)이라, 관문에서 실패하면
    # 버리는 시간이 가장 적어야 한다. 같은 길이면 카테고리·번호순(자연수 정렬 — `-10` 이 `-2` 앞에
    # 오지 않게)으로 고정한다.
    # **구조 축 구간이 그보다도 앞이다**(D-250 ①) — 보정 구간이 있으면 그 다음이다.
    segments.sort(key=lambda s: (not s.structural, not s.calibration, round(s.est_hours, 6),
                                 s.category, int(s.segment_id.rpartition("-")[2])))
    return Plan(segments=tuple(segments), unplaceable=tuple(unplaceable), max_arms=max_arms,
                budget_hours=budget, max_hours=max_hours, rate=rate)


def segment_arms(
    all_arms: Sequence[Any], axes: Sequence[str], baseline_id: str = "baseline"
) -> list[Any]:
    """구간이 돌릴 arm — **기준선이 맨 앞**이고 그 뒤로 이 구간 축의 모든 레벨이다.

    기준선을 앞에 두는 이유는 두 가지다. 러너가 호출부 순서를 지키므로(D-238) 그대로
    실행 순서가 되고, 구간마다 자기 기준선이 있어야 arm↔기준선·대조군 판정이
    **같은 구간의 기준선으로만** 내려진다.
    """
    wanted = set(axes)
    baseline = [a for a in all_arms if getattr(a, "arm_id", None) == baseline_id]
    variants = [a for a in all_arms if getattr(a, "axis", None) in wanted]
    return baseline + variants


# ── 캠페인 상태 파일 ──────────────────────────────────────────────


@dataclass
class SegmentRecord:
    """실행된 구간 1건. 완료·실패·진행만 상태 파일에 남는다(미완은 매번 다시 짠다)."""

    segment_id: str
    category: str
    axes: list[str]
    arm_ids: list[str]
    status: str                        # 완료 | 실패 | 진행
    mode: str
    run_id: Optional[str] = None
    out_dir: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    elapsed_sec: float = 0.0
    turns: int = 0
    est_hours: float = 0.0
    attempts: int = 1
    stop_reasons: list[str] = field(default_factory=list)
    health: dict[str, Any] = field(default_factory=dict)
    #: 실행하지 않고 기준선 관측으로 대신한 arm(기준선과 실효 설정 동일 — 지문 판정).
    substituted: list[str] = field(default_factory=list)
    #: 이 구간을 돌고 있는(또는 돌다 끊긴) 프로세스 — 같은 run 에 두 프로세스가 쓰지 않게 본다.
    pid: Optional[int] = None
    #: 끊긴 run 을 이어 돌았다 — 경과 시간이 마지막 시도분뿐이라 속도 실측에 쓰지 않는다.
    resumed: bool = False
    #: 이 구간 **기준선**의 판(plans/114 M-2 ①b). 구간 간 기준선 반복이 노이즈 바닥이 되려면
    #: 이 셋이 같아야 한다 — 다르면 반복이 아니라 다른 조건의 두 측정이다.
    baseline_tier: Optional[str] = None        # 사다리 단
    config_fingerprint: Optional[str] = None   # 실효 설정 지문(비결정 키 제외)
    commit: Optional[str] = None               # 실행 시점 커밋
    dirty: Optional[bool] = None               # 미커밋 변경이 있었는가
    #: **구조 축 구간이었다**(사다리 단 · plans/114 M-0). 이 구간 뒤에는 기준선 단이 바뀌는 것이
    #: 정상이다 — `continuity` 가 그 전이만 멈춤에서 뺀다.
    structural: bool = False

    @property
    def executed_arms(self) -> int:
        """실제로 돈 arm 수(기준선 포함)."""
        return 1 + len([a for a in self.arm_ids if a not in set(self.substituted)])

    @property
    def sec_per_turn(self) -> Optional[float]:
        return (self.elapsed_sec / self.turns) if self.turns else None

    @property
    def turns_per_arm(self) -> Optional[int]:
        return round(self.turns / self.executed_arms) if self.turns else None


@dataclass
class Campaign:
    """캠페인 1개 = 같은 환경·같은 모드로 전 축을 구간별로 도는 단위."""

    name: str
    path: Path
    env: str
    mode: str
    repeat: int = 1
    max_hours: float = DEFAULT_MAX_HOURS
    created_at: str = ""
    records: dict[str, SegmentRecord] = field(default_factory=dict)
    #: **구조 축(사다리 단) 판정**(plans/114 M-0 · D-250 ②). 첫 구간이 끝나면 여기에 이긴 단이
    #: 남고, 남은 구간의 **기준선 환경**에 그 3키가 주입된다. 키:
    #: `segment_id`·`axis`·`verdict`·`level`·`tier`·`env`·`sentence`·`decided_at` ·
    #: 판정 불가면 `blocked`(사유 문자열)만 있고 `level` 은 없다.
    tier_decision: dict[str, Any] = field(default_factory=dict)

    # ── 입출력 ──
    @classmethod
    def load_or_new(cls, path: Path, *, name: str, env: str, mode: str, repeat: int,
                    max_hours: float) -> Campaign:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            records = {r["segment_id"]: SegmentRecord(**r) for r in data.get("records", [])}
            return cls(name=data.get("name", name), path=path, env=data.get("env", env),
                       mode=data.get("mode", mode), repeat=int(data.get("repeat", repeat)),
                       max_hours=float(max_hours), created_at=data.get("created_at", ""),
                       records=records,
                       tier_decision=dict(data.get("tier_decision") or {}))
        return cls(name=name, path=path, env=env, mode=mode, repeat=repeat, max_hours=max_hours,
                   created_at=datetime.now().isoformat(timespec="seconds"))

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "name": self.name, "env": self.env, "mode": self.mode, "repeat": self.repeat,
            "max_hours": self.max_hours, "created_at": self.created_at,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "tier_decision": self.tier_decision,
            "records": [asdict(r) for r in
                        sorted(self.records.values(), key=lambda r: r.segment_id)],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        return self.path

    # ── 구조 축(사다리 단) 판정 ──
    def tier_winner_env(self) -> dict[str, str]:
        """남은 구간의 **기준선에 주입할** 사다리 3키. 아직 못 정했으면 빈 딕셔너리다."""
        return dict(self.tier_decision.get("env") or {})

    def tier_blocked(self) -> Optional[str]:
        """단 축 구간은 끝났는데 승자를 못 정한 사유. 없으면 None.

        **사람이 봐야 한다** — 판정 불가인 채로 남은 구간을 돌면 어느 단으로 잰 것인지 모른 채
        캠페인 전체가 진행된다(D-250 ②).
        """
        blocked = self.tier_decision.get("blocked")
        return str(blocked) if blocked else None

    def structural_done(self) -> Optional[SegmentRecord]:
        """끝난 구조 축 구간. 없으면 None."""
        return next((r for r in self.done() if r.structural), None)

    # ── 판정 ──
    def frozen_axes(self) -> set[str]:
        return {axis for r in self.records.values() for axis in r.axes}

    def failed(self) -> list[SegmentRecord]:
        return sorted((r for r in self.records.values() if r.status == FAILED),
                      key=lambda r: r.segment_id)

    def done(self) -> list[SegmentRecord]:
        return sorted((r for r in self.records.values() if r.status == DONE),
                      key=lambda r: r.segment_id)

    def running(self) -> list[SegmentRecord]:
        return sorted((r for r in self.records.values() if r.status == RUNNING),
                      key=lambda r: r.segment_id)

    def last_finished(self, exclude: str = "") -> Optional[SegmentRecord]:
        """마지막으로 끝난 구간(완료 · `finished_at` 기준) — 구간 간 판 비교의 상대.

        plans/114 M-2 ①b.

        `segment_id` 정렬이 아니라 **끝난 시각** 순이다. 실패 구간을 다시 돌리면 번호 순서와
        실행 순서가 갈린다.
        """
        finished = [r for r in self.records.values()
                    if r.status == DONE and r.finished_at and r.segment_id != exclude]
        return max(finished, key=lambda r: r.finished_at or "") if finished else None

    def used_ids(self) -> dict[str, set[int]]:
        """카테고리별로 이미 쓴 구간 번호 — 새 구간 이름이 겹치지 않게."""
        out: dict[str, set[int]] = {}
        for record in self.records.values():
            head, _, tail = record.segment_id.rpartition("-")
            if head and tail.isdigit():
                out.setdefault(head, set()).add(int(tail))
        return out

    def rate(self, turns_per_arm: int) -> RateModel:
        """계획에 쓸 턴당 속도.

        **run 모드 구간의 실측만 반영한다** — 가장 최근에 끝난 run 구간의 `경과 ÷ 턴`이다.
        mock 구간은 반영하지 않는다(모의 속도로 짜면 남은 구간이 한 덩어리로 뭉쳐 run 계획과
        달라진다). 그래서 mock 캠페인은 **run 계획을 그대로 리허설한다.**
        """
        measured = [r for r in self.records.values()
                    if r.mode == "run" and r.sec_per_turn and r.finished_at and not r.resumed]
        if measured:
            last = max(measured, key=lambda r: r.finished_at or "")
            # **턴 수도 실측으로 바꾼다** — 역질문 자동응답이 답하는 턴이 늘면 arm 당 턴이
            # 카탈로그보다 많아진다.
            return RateModel(sec_per_turn=float(last.sec_per_turn),
                             turns_per_arm=int(last.turns_per_arm or turns_per_arm),
                             source=f"구간 {last.segment_id} 실측 — 턴당 초·arm 당 턴 모두")
        return RateModel(sec_per_turn=DEFAULT_SEC_PER_TURN, turns_per_arm=turns_per_arm,
                         source="기본값 — run 20260914 그래프 진입 턴 평균 · 턴 수는 카탈로그")


def pid_alive(pid: Optional[int]) -> bool:
    """그 프로세스가 아직 살아 있는가(자기 자신은 아니다).

    **신호를 보내지 않는다** — Windows 의 `os.kill` 은 신호 번호와 무관하게 대상 프로세스를
    종료한다(`TerminateProcess`). 그래서 Windows 는 핸들의 종료 코드로 본다.
    """
    if not pid or pid == os.getpid():
        return False
    if os.name == "nt":  # pragma: no cover - 플랫폼 의존
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            alive = bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code)))
            return alive and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def campaign_path(root: Path, name: str) -> Path:
    return root / "campaigns" / name / "campaign.json"


def default_name(mode: str, env: str) -> str:
    """캠페인 이름 기본값 — **모드별로 갈린다.** mock 리허설과 실 캠페인이 상태를 섞지 않는다.
    `--mode dry` 는 실 캠페인(`run-<env>`)의 계획을 보여준다."""
    return f"{'run' if mode == 'dry' else mode}-{env}"


def render_plan(campaign: Campaign, plan: Plan) -> list[str]:
    """계획 표 — 완료·실패 구간(고정)과 미완 구간(현재 속도로 다시 짠 것)을 한 표에."""
    lines = [
        f"캠페인 `{campaign.name}` — 환경 {campaign.env} · 모드 {campaign.mode} "
        f"· 반복 {campaign.repeat}",
        f"  상태 파일: {campaign.path}",
        f"  추정 근거: {plan.rate.describe()}",
        f"  예산: 한 번 구동 ≤ {plan.max_hours:g}시간 · 채움 상한 {plan.budget_hours:.1f}시간"
        f"(안전 여유 {SAFETY_MARGIN:.0%}) → 구간당 arm ≤ {plan.max_arms}개(기준선 포함)",
        "",
        f"  {'구간':22s} {'상태':4s} {'카테고리':14s} {'arm':>3s} {'추정h':>6s}  축",
    ]
    for record in sorted(campaign.records.values(), key=lambda r: r.segment_id):
        actual = (f"{record.elapsed_sec / 3600:.1f}실" if record.elapsed_sec
                  else f"{record.est_hours:.1f}")
        skip = f" · 생략 {len(record.substituted)}" if record.substituted else ""
        if record.status == RUNNING:
            skip += (f" (실행 중 · pid {record.pid})" if pid_alive(record.pid)
                     else f" (끊김 — 다음 실행이 run {record.run_id} 를 잇는다)")
        lines.append(f"  {record.segment_id:22s} {record.status:4s} {record.category:14s} "
                     f"{record.executed_arms:3d} {actual:>6s}  {', '.join(record.axes)}{skip}")
    for seg in plan.segments:
        note = (" (★ 구조 축 — 첫 구간 · D-250 ①)" if seg.structural
                else " (보정 구간)" if seg.calibration else "")
        if seg.over_budget:
            note += (f" ★ **예산 초과** — 추정 {seg.est_hours:.1f}시간 > 채움 상한 "
                     f"{plan.budget_hours:.1f}시간. 구조 축은 빼면 캠페인이 성립하지 않으므로 "
                     f"구간은 만들되 자동 실행하지 않는다 — `--max-hours` 를 "
                     f"{math.ceil(seg.est_hours / (1 - SAFETY_MARGIN))} 이상으로 올리거나 "
                     f"워크로드(`--groups`)를 줄여 사람이 판단한다")
        skip = f" · 생략 {len(seg.substituted)}" if seg.substituted else ""
        lines.append(f"  {seg.segment_id:22s} {PENDING:4s} {seg.category:14s} "
                     f"{seg.n_arms:3d} {seg.est_hours:6.1f}  {', '.join(seg.axes)}{skip}{note}")
    for axis, reason in plan.unplaceable:
        lines.append(f"  {'(구간 불가)':22s} {'—':4s} {'':14s} {'':3s} {'':6s}  {axis} — {reason}")
    remaining = len(plan.segments)
    lines += [
        "",
        f"  합계: 완료 {len(campaign.done())} · 실패 {len(campaign.failed())} "
        + (f"· 진행 {len(campaign.running())} " if campaign.running() else "")
        + f"· 미완 {remaining}구간"
        f" · 미완 추정 {plan.total_hours:.1f}시간"
        + (f" · 구간 불가 {len(plan.unplaceable)}축" if plan.unplaceable else ""),
    ]
    if remaining:
        lines.append(f"  달력: 하루 1구간이면 {remaining}일 · 하루 2구간(야간·주간)이면 "
                     f"{math.ceil(remaining / 2)}일")
    lines += render_tier_decision(campaign)
    return lines


def render_tier_decision(campaign: Campaign) -> list[str]:
    """사다리 단 축 판정 상태 한 줄 — **어느 단으로 돌고 있는지**가 계획 표에 보여야 한다."""
    decision = campaign.tier_decision
    if not decision:
        return ["  사다리 단: **미측정** — 첫 구간(구조 축)이 잰다. 그 전까지 남은 구간은 돌지 "
                "않는다(D-250 ①)."]
    blocked = campaign.tier_blocked()
    if blocked:
        return [f"  사다리 단: **판정 불가** — {blocked}",
                "    승자를 정하지 않았다. 남은 구간은 돌지 않는다 — 사람이 본다(D-250 ②)."]
    env = " · ".join(f"{k}={v}" for k, v in sorted((decision.get("env") or {}).items()))
    return [f"  사다리 단: **`{decision.get('level')}`**(단 `{decision.get('tier')}`) 승 — "
            f"구간 `{decision.get('segment_id')}` 판정 「{decision.get('verdict')}」",
            f"    남은 구간 기준선 주입: {env or '(없음)'}"]


def continuity(previous: Optional[SegmentRecord],
               current: SegmentRecord) -> tuple[list[str], list[str]]:
    """앞 구간과 판이 같은가 — `(멈춤 사유, 고지)`(plans/114 M-2 ①b · 팀 리드 정정 2026-09-22).

    캠페인은 **구간 간 기준선 반복**으로 노이즈 바닥을 잰다(D-239). 구간 사이에 단·설정·판이
    바뀌면 그 반복은 같은 조건의 반복이 아니다. 그런데 종전에는 그 셋을 아무 데도 남기지 않아
    사후에 확인할 수조차 없었다.

    **멈추는 것은 단이 바뀐 경우뿐이다** — 단은 노드 집합을 바꿔 다른 축의 효과를 조건부로
    만든다(D-250 ②). 설정 지문·커밋 차이는 고지한다(실행 중 코드를 고친 것이 곧 사고는 아니지만,
    구간 간 비교에는 실린다).

    **예외 하나**: 앞 구간이 **구조 축 구간**(사다리 단 · M-0)이면 단이 바뀌는 것이 정상이다 —
    그 구간이 이긴 단을 남은 구간 기준선에 주입하는 것이 설계다(D-250 ②). 멈추지 않고 고지한다.
    """
    if previous is None:
        return [], []
    stop: list[str] = []
    notes: list[str] = []
    if (previous.baseline_tier and current.baseline_tier
            and previous.baseline_tier != current.baseline_tier):
        moved = (f"앞 구간 `{previous.segment_id}` 의 기준선 단이 `{previous.baseline_tier}` 인데 "
                 f"이 구간은 `{current.baseline_tier}` 다")
        if previous.structural:
            notes.append(
                f"{moved} — 앞 구간이 **사다리 단 축 구간**이라 정상이다(D-250 ②: 이긴 단을 남은 "
                "구간 기준선에 주입한다). 단 축 구간의 기준선 관측은 노이즈 바닥 계산에서 다른 "
                "단의 반복과 섞어 읽지 말 것")
        else:
            stop.append(
                f"{moved} — 캠페인 도중 단이 바뀌면 구간 간 기준선 "
                "반복이 노이즈 바닥이 되지 못하고 축 효과가 단 차이와 교란된다")
    if (previous.config_fingerprint and current.config_fingerprint
            and previous.config_fingerprint != current.config_fingerprint):
        notes.append(
            f"기준선 실효 설정 지문이 앞 구간 `{previous.segment_id}` 와 다르다"
            f"({previous.config_fingerprint} → {current.config_fingerprint}) — 구간 간 기준선 "
            "반복을 노이즈 바닥으로 읽을 때 이 차이를 함께 본다")
    if previous.commit and current.commit and previous.commit != current.commit:
        notes.append(
            f"판이 바뀌었다 — 앞 구간 `{previous.segment_id}` 커밋 {previous.commit[:8]} → "
            f"이 구간 {current.commit[:8]}. 구간 사이의 차이에 코드 변경이 섞였다")
    elif current.dirty and previous.dirty is not None and not previous.dirty:
        notes.append("앞 구간은 깨끗한 작업 트리였는데 이 구간은 미커밋 변경이 있다 — "
                     "같은 커밋이어도 실행한 코드가 다르다")
    return stop, notes


def unmeasured_reason(axis: str, campaign: Campaign, plan: Plan) -> Optional[str]:
    """이 축이 합산 표에서 왜 비는가. 측정됐으면 None."""
    for record in campaign.records.values():
        if axis in record.axes:
            if record.status == DONE:
                return None
            state = "진행 중·끊김" if record.status == RUNNING else "실패"
            return f"미측정(구간 {record.segment_id} {state})"
    for seg in plan.segments:
        if axis in seg.axes:
            return f"미측정(구간 {seg.segment_id} 미완)"
    for unplaced, why in plan.unplaceable:
        if unplaced == axis:
            # 구간 불가 사유는 셋이다(예산 초과 · 전 레벨 A/A · 축 도달 불가) — 실제 사유를 싣는다.
            return f"미측정({why})"
    return "미측정(캠페인 계획에 없음)"
