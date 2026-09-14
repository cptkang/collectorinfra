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
    """
    return [o for o in observations if not o.manual]


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

    lat_txt = f"지연 {latency.mean_delta:+.0f}ms" if latency else "지연 미측정"
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


def noise_floor(baseline_repeats: Sequence[Sequence[Observation]]) -> float:
    """기준선 반복 간 정확도 편차 = **이 벤치마크가 구분할 수 있는 최소 차이**.

    이 값 없이 축의 차이를 해석하면 노이즈를 효과로 읽는다(§3.3 S1을 건너뛰지 않는 이유).
    """
    rates = []
    for run in baseline_repeats:
        if not run:
            continue
        rates.append(sum(1 for o in run if o.passed) / len(run) * 100.0)
    if len(rates) < 2:
        return 0.0
    return round(max(rates) - min(rates), 2)


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
