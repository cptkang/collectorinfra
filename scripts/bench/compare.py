"""축별 쌍체 비교와 판정 (plans/93 §5.3·§5.5).

세 가지 원칙만 지킨다.

  **쌍체(paired)**  arm 비교는 항상 같은 시나리오 id 단위로 한다. 집계 평균 비교보다
                   분산이 훨씬 작다 — LLM 비결정성 아래에서 이것 없이는 아무것도 안 보인다.
  **없는 유의성을 만들지 않는다**  표본이 부족하면 p값 대신 `판정 불가`를 낸다.
  **효과 크기를 함께 적는다**  "유의하지만 0.5%p"는 운영 판단에서 무의미하다.

외부 의존 0 — `scipy` 없이 이항검정(McNemar)과 부트스트랩을 직접 쓴다.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from math import comb
from typing import Optional, Sequence

from scripts.bench.sweep import Observation

#: 판정 어휘 5종. 리포트는 이 밖의 말을 쓰지 않는다(§5.5).
ADOPT = "채택 권고"
CONDITIONAL = "조건부 권고"
REJECT = "기각"
NO_DIFFERENCE = "차이 없음"
UNDERPOWERED = "판정 불가"

#: **축 단위** 판정 어휘. arm 단위 5어휘와 섞지 않는다 — arm 판정은 "기준선보다 나은가"이고
#: 축 판정은 "이 축의 어느 레벨이 최적인가"라 묻는 것이 다르다(§5.5 어휘 폐쇄성 유지).
BEST_LEVEL = "최적 레벨"
LEVELS_TIED = "레벨 간 차이 없음"

#: 이 아래 차이는 운영 판단을 바꾸지 못한다고 본다(§2-② 검정력 한계).
MIN_MEANINGFUL_PP = 5.0

#: 불일치 쌍이 이보다 적으면 판정하지 않는다. 표본이 작으면 한 건이 큰 효과로 보인다.
MIN_DISCORDANT = 5


@dataclass(frozen=True)
class PairedAccuracy:
    """쌍체 정확도 비교 — McNemar."""

    n_pairs: int
    only_baseline_passed: int   # b
    only_variant_passed: int    # c
    delta_pp: float
    p_value: Optional[float]

    @property
    def discordant(self) -> int:
        return self.only_baseline_passed + self.only_variant_passed


@dataclass(frozen=True)
class PairedMetric:
    """쌍체 수치 비교 — 부트스트랩 신뢰구간."""

    n_pairs: int
    mean_delta: float
    ci_low: float
    ci_high: float

    @property
    def crosses_zero(self) -> bool:
        return self.ci_low <= 0.0 <= self.ci_high


@dataclass(frozen=True)
class AxisVerdict:
    """축 1개(정확히는 arm 1개)에 대한 최종 판정."""

    arm_id: str
    axis: Optional[str]
    level: Optional[str]
    verdict: str
    accuracy: Optional[PairedAccuracy]
    latency: Optional[PairedMetric]
    llm_calls: Optional[PairedMetric]
    sentence: str
    signal: str = "정확도"                        # 판정의 근거가 된 이진 신호
    completion: Optional[PairedAccuracy] = None   # 완주율 쌍체
    sql_rate: Optional[PairedAccuracy] = None     # SQL 생성률 쌍체


def _index(observations: Sequence[Observation]) -> dict[tuple[str, int], Observation]:
    return {(o.scenario_id, o.repeat): o for o in observations}


def _scored(observations: Sequence[Observation]) -> list[Observation]:
    """정확도 비교에 쓸 수 있는 관측치만.

    mock 모드의 `manual` 판정은 "아직 사람이 안 봤다"는 뜻이지 합격도 불합격도 아니다.
    이것을 통과로 세면 모든 arm이 100%가 되어 비교가 무의미해진다.

    **단언이 평가되지 않은 시나리오도 뺀다**(D-241) — 타임아웃·역질문 차단으로 끝난 턴의
    「불합격」은 기능이 틀렸다는 뜻이 아니다. 완주율·SQL 생성·지연 비교는 이 함수를 거치지
    않으므로(`drop_manual=False`) 그 신호에는 남는다.
    """
    return [o for o in observations if not o.manual and not o.unevaluated]


def paired_binary(
    baseline: Sequence[Observation],
    variant: Sequence[Observation],
    signal: str,
    *,
    drop_manual: bool = True,
) -> PairedAccuracy:
    """임의의 이진 신호에 대한 McNemar — 불일치 쌍만 본다.

    `signal` 은 관측치의 불리언 속성 이름이다(`passed`·`completed`·`sql_generated`).
    `drop_manual` 은 **정확도에만** 해당한다 — 완주·SQL 생성은 사람의 판정과 무관하게
    원시 로그에서 바로 읽히므로 보류 건도 그대로 센다.
    """
    pool_b = _scored(baseline) if drop_manual else list(baseline)
    pool_v = _scored(variant) if drop_manual else list(variant)
    base_idx, var_idx = _index(pool_b), _index(pool_v)
    keys = sorted(set(base_idx) & set(var_idx))
    b = sum(1 for k in keys
            if getattr(base_idx[k], signal) and not getattr(var_idx[k], signal))
    c = sum(1 for k in keys
            if not getattr(base_idx[k], signal) and getattr(var_idx[k], signal))
    delta = ((c - b) / len(keys) * 100.0) if keys else 0.0
    return PairedAccuracy(
        n_pairs=len(keys), only_baseline_passed=b, only_variant_passed=c,
        delta_pp=round(delta, 2), p_value=_exact_binomial_p(b, c),
    )


def paired_accuracy(
    baseline: Sequence[Observation], variant: Sequence[Observation]
) -> PairedAccuracy:
    """쌍체 정확도 — 기계 단언이 옮겨진 시나리오만 대상이다."""
    return paired_binary(baseline, variant, "passed")


def _exact_binomial_p(b: int, c: int) -> Optional[float]:
    """불일치 쌍에 대한 정확 이항검정(양측, p=0.5).

    근사(카이제곱)를 쓰지 않는 이유는 우리 표본에서 불일치 쌍이 한 자리 수인 경우가
    흔하기 때문이다 — 그 구간에서 근사는 p를 낙관적으로 만든다.
    """
    n = b + c
    if n == 0:
        return None
    k = min(b, c)
    tail = sum(comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2.0 * tail)


def paired_metric(
    baseline: Sequence[Observation],
    variant: Sequence[Observation],
    attr: str,
    *,
    iterations: int = 2000,
    seed: int = 93,
) -> Optional[PairedMetric]:
    """쌍별 차이의 부트스트랩 신뢰구간.

    지연 분포는 치우쳐 있어 평균 t검정이 맞지 않는다. 재표본으로 구간을 낸다.
    """
    base_idx, var_idx = _index(baseline), _index(variant)
    deltas = [
        float(getattr(var_idx[k], attr) or 0) - float(getattr(base_idx[k], attr) or 0)
        for k in sorted(set(base_idx) & set(var_idx))
        if getattr(base_idx[k], attr) is not None and getattr(var_idx[k], attr) is not None
    ]
    if len(deltas) < 2:
        return None

    rng = random.Random(seed)
    means = []
    for _ in range(iterations):
        sample = [deltas[rng.randrange(len(deltas))] for _ in deltas]
        means.append(sum(sample) / len(sample))
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[min(len(means) - 1, int(0.975 * len(means)))]
    return PairedMetric(
        n_pairs=len(deltas),
        mean_delta=round(sum(deltas) / len(deltas), 2),
        ci_low=round(lo, 2), ci_high=round(hi, 2),
    )


def finished_latency_spread(
    baseline: Sequence[Observation], variant: Sequence[Observation]
) -> Optional[tuple[int, float]]:
    """**둘 다 완주한 쌍**의 지연 차 표준편차 `(쌍 수, ms)`(plans/118 B-6). 2쌍 미만이면 None.

    완주 = 타임아웃·무효가 아니고 지연이 기록됐다. 타임아웃 턴은 상한에서 잘린(검열된) 값이라
    넣으면 분산이 상한에 묶인다 — run-closed 에서 60초 상한 6.5초 · 180초 상한 38.7초로 6배
    갈렸다(118 §2.4). 새 통계 기법이 아니라 표본 표준편차 하나다.
    """
    import statistics

    def _ok(o: Observation) -> bool:
        return not o.invalid and o.unevaluated != "timeout" and o.wall_ms is not None

    base_idx, var_idx = _index(baseline), _index(variant)
    deltas = [float(var_idx[k].wall_ms or 0) - float(base_idx[k].wall_ms or 0)
              for k in sorted(set(base_idx) & set(var_idx))
              if _ok(base_idx[k]) and _ok(var_idx[k])]
    if len(deltas) < 2:
        return None
    return len(deltas), round(statistics.stdev(deltas), 1)


def latency_power_note(effect_ms: float, spread: Optional[tuple[int, float]]) -> str:
    """지연 행 꼬리 — 그 구간의 지연 노이즈 크기와, 효과가 그보다 작으면 「검정력 부족」 표기."""
    if spread is None:
        return ""
    n, sd = spread
    weak = " — **지연 신호 검정력 부족**" if abs(effect_ms) < sd else ""
    return f"(둘 다 완주 {n}쌍 지연 차 표준편차 {sd:.0f}ms{weak})"


def judge(
    arm_id: str,
    axis: Optional[str],
    level: Optional[str],
    baseline: Sequence[Observation],
    variant: Sequence[Observation],
    *,
    noise_floor_pp: float = 0.0,
    alpha: float = 0.05,
) -> AxisVerdict:
    """판정 5어휘 중 하나와 **문장**을 낸다. 개발자가 통계를 읽지 않게 하는 지점이다."""
    acc = paired_accuracy(baseline, variant)
    latency = paired_metric(baseline, variant, "wall_ms")
    # 비용 축은 **잴 수 있는 것으로** 잰다. LLM 호출 수는 `done` 페이로드에 없어
    # 구조적으로 측정 불가이므로, 없으면 노드 실행 수(파이프라인이 한 일의 양)로 대신한다.
    calls = paired_metric(baseline, variant, "llm_calls")
    cost_label = "LLM 호출"
    if calls is None:
        calls = paired_metric(baseline, variant, "node_count")
        cost_label = "노드 수"
    run = paired_binary(baseline, variant, "completed", drop_manual=False)
    sql = paired_binary(baseline, variant, "sql_generated", drop_manual=False)

    # **판정 신호를 고른다.** 정확도가 1순위지만, 정상군 시나리오에 기계 단언이 하나도
    # 옮겨져 있지 않으면(실측 2026-09-14: 32건 전부 `manual_review` 뿐) 정확도 쌍은 항상
    # 0쌍이다. 그 상태에서 UNDERPOWERED 만 내면 성공한 런과 전건 401 인 런이 **같은 문장**
    # 으로 나온다. 완주 여부는 사람이 옮겨 적지 않아도 원시 로그에 있고 설정 축이 실제로
    # 흔드는 값이므로, 정확도가 없을 때의 대체 신호로 쓴다 — 이름을 바꿔 적어 혼동을 막는다.
    if acc.n_pairs > 0:
        primary, label = acc, "정확도"
    elif run.n_pairs > 0:
        primary, label = run, "완주율"
    else:
        detail = "비교할 쌍이 없다 — arm이 무효이거나 워크로드가 겹치지 않는다."
        return AxisVerdict(arm_id, axis, level, UNDERPOWERED, acc, latency, calls, detail,
                           signal="없음", completion=run, sql_rate=sql)

    acc_unavailable = (
        "정확도 미측정(시나리오에 기계 단언 없음) · " if label == "완주율" else ""
    )
    sql_txt = f" · SQL 생성률 {sql.delta_pp:+.1f}%p" if sql.n_pairs else ""

    threshold = max(MIN_MEANINGFUL_PP, noise_floor_pp)
    significant = primary.p_value is not None and primary.p_value < alpha
    meaningful = abs(primary.delta_pp) >= threshold

    # ★ 불일치 쌍이 적으면 **효과가 커 보여도** 판정하지 않는다.
    #   20건 표본에서 1건 차이는 5%p다 — 임계를 넘지만 정보는 1건뿐이다.
    #   이 가드가 없으면 작은 표본에서 큰 효과가 만들어진다(§2-②의 정확한 실패 유형).
    def _verdict(kind: str, detail: str) -> AxisVerdict:
        return AxisVerdict(arm_id, axis, level, kind, acc, latency, calls, detail,
                           signal=label, completion=run, sql_rate=sql)

    lat_txt = (f"지연 {latency.mean_delta:+.0f}ms"
               + latency_power_note(latency.mean_delta,
                                    finished_latency_spread(baseline, variant))
               if latency else "지연 미측정")
    call_txt = f" · {cost_label} {calls.mean_delta:+.2f}" if calls else ""
    main_txt = f"{acc_unavailable}{label} {primary.delta_pp:+.1f}%p"

    if primary.discordant < MIN_DISCORDANT and not significant:
        return _verdict(UNDERPOWERED,
                        f"불일치 쌍 {primary.discordant}건 — 차이를 판정할 표본이 부족하다"
                        f"({main_txt}는 우연과 구분되지 않는다){sql_txt} · {lat_txt}{call_txt}. "
                        f"성능 근거 없이 정적 규칙으로 판정한다.")

    if not meaningful and not significant:
        return _verdict(NO_DIFFERENCE,
                        f"{main_txt} · {lat_txt}{sql_txt} — 노이즈 상한 이하다. 처분 규칙 6조 대상.")

    if primary.delta_pp < 0 and significant:
        return _verdict(REJECT,
                        f"{main_txt}로 나빠진다 · {lat_txt}{sql_txt}. "
                        f"기본값을 유지하고 '권장하지 않음'을 적는다.")

    worse_latency = latency is not None and latency.ci_low > 0
    if primary.delta_pp > 0 and worse_latency:
        return _verdict(CONDITIONAL,
                        f"{main_txt} 좋아지지만 {lat_txt} 느려진다 — {label}와 지연을 맞바꾼다. "
                        f"기본값은 바꾸지 않고 권고값만 병기한다.")

    if primary.delta_pp > 0:
        return _verdict(ADOPT,
                        f"{main_txt} · {lat_txt}{sql_txt} — 기준선보다 낫고 부작용이 없다. 채택 권고.")

    return _verdict(NO_DIFFERENCE, f"{main_txt} · {lat_txt}{sql_txt} — 방향이 뚜렷하지 않다.")


# ── 레벨 간 직접 비교 — "켰을 때 vs 껐을 때" ────────────────────────
#
# `judge()` 는 **모든 arm 을 기준선하고만** 비교한다. 불린 축이면 `-true`·`-false` 두 줄이
# 각각 기준선과 비교돼 나오는데, 기준선의 실효값이 `true` 면 `-true` 줄은 대조군(순수 노이즈)
# 이고 `-false` 줄만 진짜 대비다. 그 상태로는 **"이 축의 최적 레벨은 무엇인가"가 어디에도
# 계산되지 않는다** — 벤치마크의 목적이 그것인데도. 아래가 그 빈자리를 메운다.


@dataclass(frozen=True)
class LevelPair:
    """같은 축의 레벨 둘을 직접 쌍체 비교한 결과. `a` 를 기준으로 `b` 의 델타를 적는다."""

    axis: str
    level_a: str
    level_b: str
    signal: str                       # 정확도 | 완주율 | SQL 생성률
    binary: PairedAccuracy            # b - a (delta_pp 부호는 b 가 나은 쪽이 +)
    latency: Optional[PairedMetric]
    significant: bool = False         # 다중비교 보정 **후**

    @property
    def better(self) -> Optional[str]:
        """유의하고 의미 있는 차이가 있을 때만 이긴 레벨. 아니면 None."""
        if not self.significant or abs(self.binary.delta_pp) < MIN_MEANINGFUL_PP:
            return None
        return self.level_b if self.binary.delta_pp > 0 else self.level_a


@dataclass(frozen=True)
class AxisOptimum:
    """축 1개에 대한 **최종 한 줄** — 이 축의 최적 레벨은 무엇인가."""

    axis: str
    levels: tuple[str, ...]
    verdict: str                      # BEST_LEVEL | LEVELS_TIED | UNDERPOWERED
    best_level: Optional[str]
    signal: str
    sentence: str
    pairs: tuple[LevelPair, ...] = ()
    control_levels: tuple[str, ...] = ()   # 기준선과 설정이 같은 레벨(대조군)


def _level_signal(level_obs: dict[str, Sequence[Observation]]) -> str:
    """레벨 비교에 쓸 이진 신호를 고른다 — `judge()` 와 같은 2단 규칙이다.

    정확도(기계 단언)가 1순위고, 어느 쌍에서도 정확도 쌍이 0이면 완주율로 내려간다.
    이름을 바꿔 적어 무엇으로 판정했는지를 문장이 숨기지 않게 한다(§4.3.1).
    """
    levels = sorted(level_obs)
    for i in range(len(levels)):
        for j in range(i + 1, len(levels)):
            if paired_accuracy(level_obs[levels[i]], level_obs[levels[j]]).n_pairs > 0:
                return "passed"
    return "completed"


def compare_levels(
    axis: str,
    level_obs: dict[str, Sequence[Observation]],
    *,
    control_levels: Sequence[str] = (),
    alpha: float = 0.05,
) -> AxisOptimum:
    """축 하나의 레벨들을 **서로** 비교해 최적 레벨 한 줄을 낸다.

    - 레벨이 3개 이상이면 쌍이 여러 개다. 그대로 두면 우연히 유의한 쌍이 나오므로
      `benjamini_hochberg` 로 보정한다(§5.3 — 축 사이 보정과 같은 장치를 축 **안**에서 쓴다).
    - `control_levels` 는 기준선과 설정이 동일해 대조군으로 판정된 레벨이다.
      **비교에서 빼지 않는다** — 대조군 vs 다른 레벨이야말로 가장 깨끗한 대비다.
      다만 문장에 그 사실을 적어 "기준선 대비"와 혼동하지 않게 한다.
    """
    levels = tuple(sorted(level_obs))
    signal = _level_signal(level_obs)
    label = {"passed": "정확도", "completed": "완주율"}[signal]

    raw: list[LevelPair] = []
    for i in range(len(levels)):
        for j in range(i + 1, len(levels)):
            a, b = levels[i], levels[j]
            raw.append(LevelPair(
                axis=axis, level_a=a, level_b=b, signal=label,
                binary=paired_binary(level_obs[a], level_obs[b], signal,
                                     drop_manual=(signal == "passed")),
                latency=paired_metric(level_obs[a], level_obs[b], "wall_ms"),
            ))

    keep = benjamini_hochberg([p.binary.p_value for p in raw], alpha=alpha)
    pairs = tuple(
        LevelPair(axis=p.axis, level_a=p.level_a, level_b=p.level_b, signal=p.signal,
                  binary=p.binary, latency=p.latency,
                  significant=bool(flag) and p.binary.discordant >= MIN_DISCORDANT)
        for p, flag in zip(raw, keep)
    )

    ctrl = tuple(lv for lv in levels if lv in set(control_levels))
    ctrl_txt = (f" · 대조군 레벨 {'·'.join(ctrl)}(기준선과 동일 설정)" if ctrl else "")
    unavailable = "정확도 미측정(시나리오에 기계 단언 없음) · " if label == "완주율" else ""

    def _out(verdict: str, best: Optional[str], sentence: str) -> AxisOptimum:
        return AxisOptimum(axis=axis, levels=levels, verdict=verdict, best_level=best,
                           signal=label, sentence=sentence, pairs=pairs, control_levels=ctrl)

    if len(levels) < 2:
        return _out(UNDERPOWERED, None,
                    f"레벨이 {len(levels)}개뿐이라 비교할 대상이 없다{ctrl_txt}.")

    discordant = sum(p.binary.discordant for p in pairs)
    if discordant == 0:
        return _out(UNDERPOWERED, None,
                    f"{unavailable}레벨 {len(levels)}개 · 쌍 {len(pairs)}건 — "
                    f"**불일치 쌍이 전부 0건**이다. 레벨을 바꿔도 결과가 한 건도 달라지지 "
                    f"않았다 — 축이 무효인 것이 아니라 워크로드가 축에 닿지 않은 것일 수 "
                    f"있다{ctrl_txt}.")

    winners = [p.better for p in pairs if p.better]
    if not winners:
        lat = [p for p in pairs if p.latency and not p.latency.crosses_zero]
        lat_txt = ""
        if lat:
            fastest = min(lat, key=lambda p: p.latency.mean_delta)
            faster = (fastest.level_b if fastest.latency.mean_delta < 0 else fastest.level_a)
            spread = finished_latency_spread(level_obs[fastest.level_a],
                                             level_obs[fastest.level_b])
            note = latency_power_note(getattr(fastest.latency, "mean_delta", 0.0), spread)
            lat_txt = (f" 다만 지연은 `{faster}` 가 "
                       f"{abs(fastest.latency.mean_delta):.0f}ms 빠르다(CI가 0을 지나지 않음)"
                       f"{note}.")
        return _out(LEVELS_TIED, None,
                    f"{unavailable}{label} 기준 레벨 간 유의차 없음 — 불일치 쌍 {discordant}건, "
                    f"보정 후 유의한 쌍 0건{ctrl_txt}.{lat_txt} 기본값을 바꿀 근거가 없다.")

    tally = {level: winners.count(level) for level in set(winners)}
    top = max(tally.values())
    best = sorted(lv for lv, count in tally.items() if count == top)
    if len(best) > 1:
        return _out(UNDERPOWERED, None,
                    f"{unavailable}{label} 기준 우세 레벨이 {len(best)}개({'·'.join(best)})로 "
                    f"갈린다 — 순위가 일관되지 않아 최적을 정하지 않는다{ctrl_txt}.")

    winner = best[0]
    deltas = [f"{p.level_a}↔{p.level_b} {p.binary.delta_pp:+.1f}%p"
              for p in pairs if p.better == winner]
    return _out(BEST_LEVEL, winner,
                f"{unavailable}**`{winner}`** 가 {label} 기준 우세하다 — "
                f"{' · '.join(deltas)}(BH 보정 후 유의 · 불일치 쌍 {discordant}건){ctrl_txt}.")


def optima(
    observations_by_arm: dict[str, Sequence[Observation]],
    arms: Sequence[object],
    *,
    control_arms: Sequence[str] = (),
    alpha: float = 0.05,
) -> list[AxisOptimum]:
    """arm 목록을 축 단위로 묶어 `compare_levels` 를 돌린다.

    `arms` 원소는 `axis`·`level`·`arm_id` 속성만 있으면 된다(`sweep.ArmSpec`).
    """
    grouped: dict[str, dict[str, Sequence[Observation]]] = {}
    controls: dict[str, list[str]] = {}
    for arm in arms:
        axis = getattr(arm, "axis", None)
        level = getattr(arm, "level", None)
        arm_id = getattr(arm, "arm_id", "")
        if not axis or level is None:
            continue
        grouped.setdefault(axis, {})[level] = observations_by_arm.get(arm_id, [])
        if arm_id in set(control_arms):
            controls.setdefault(axis, []).append(level)
    return [compare_levels(axis, levels, control_levels=controls.get(axis, ()), alpha=alpha)
            for axis, levels in sorted(grouped.items())]


def noise_floor(baseline_repeats: Sequence[Sequence[Observation]]) -> float:
    """기준선 반복 간 정확도 편차 = **이 벤치마크가 구분할 수 있는 최소 차이**.

    이 값 없이 축의 차이를 해석하면 노이즈를 효과로 읽는다(§3.3 S1을 건너뛰지 않는 이유).

    **대조군 arm 도 반복이다.** 주입값이 기준선 실효값과 같은 arm(`sweep.control_arms`)은
    설정이 기준선과 동일하므로, 그 관측치를 기준선의 또 한 번의 실행으로 넣으면
    `--repeat 1` 런에서도 바닥을 실측할 수 있다(2026-09-21 실측: run 20260914-185540 의
    대조군 3개가 쌍체 지연 -2.1초·+2.1초·+5.8초를 냈다 — 축 효과 보고값과 같은 크기대).
    """
    rates = []
    for run in baseline_repeats:
        if not run:
            continue
        rates.append(sum(1 for o in run if o.passed) / len(run) * 100.0)
    if len(rates) < 2:
        return 0.0
    return round(max(rates) - min(rates), 2)


def latency_noise_floor(
    baseline: Sequence[Observation], controls: Sequence[Sequence[Observation]]
) -> Optional[float]:
    """대조군 arm 들의 **쌍체 지연 델타 절댓값 최댓값** = 지연의 노이즈 바닥(ms).

    설정이 기준선과 같은 arm 이 내는 델타는 정의상 순수 노이즈다. 이 값을 넘지 못하는
    지연 차이는 축 효과로 읽지 않는다.
    """
    deltas = []
    for run in controls:
        metric = paired_metric(baseline, run, "wall_ms")
        if metric is not None:
            deltas.append(abs(metric.mean_delta))
    return round(max(deltas), 1) if deltas else None


def benjamini_hochberg(p_values: Sequence[Optional[float]], *, alpha: float = 0.05) -> list[bool]:
    """다중비교 보정 — 축이 12~18개면 우연히 유의한 것이 나온다.

    보정 전/후를 둘 다 리포트에 적는다(§5.3).
    """
    indexed = [(i, p) for i, p in enumerate(p_values) if p is not None]
    if not indexed:
        return [False] * len(p_values)
    indexed.sort(key=lambda t: t[1])
    m = len(indexed)
    keep = [False] * len(p_values)
    cutoff = 0
    for rank, (_, p) in enumerate(indexed, start=1):
        if p <= alpha * rank / m:
            cutoff = rank
    for rank, (idx, _) in enumerate(indexed, start=1):
        if rank <= cutoff:
            keep[idx] = True
    return keep
