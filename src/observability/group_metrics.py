"""실행 그룹 소요 계측 (D-176 · plans/82 §5.5 S-A·S-B · SPEC-group-runner T14).

**왜 이것이 먼저인가 (P13)**: 하네스 문헌의 Tier 규율은 *"Tier 1의 추적·귀책 능력이
없으면 Tier 2의 최적화가 이득인지 손실인지 판별할 방법이 없다"* 고 못박는다. `plans/82`의
범위 사전 선택(§5)은 "예상 N초"라는 임계로 발동하는데, 그 숫자의 출처가 없으면
`MIN_RELEVANCE_SCORE=0.3`이 근거 없이 무기한 실동작한 전례(D-174 ②)를 반복한다.

이 모듈은 그룹 실행 소요를 유형별로 모아 **p50/p90과 표본 수**를 낸다. 표본이 부족하면
호출부가 **예상 시간 문구를 생략**해야 한다(§5.5 S-C — 근거 없는 숫자를 보여주지 않는다).

계측은 질문 기능과 **무관하게 단독으로 유용**하다 — 어느 그룹이 느린지 모르면 지연 대응도
추정이 된다. 그래서 범위 선택(Wave 6.5)보다 앞선 Wave 3에 둔다.

메모리 상한: 유형당 최근 `_MAX_SAMPLES`개만 보관한다(무한 성장 차단).
프로세스 로컬 인메모리다 — 재기동 시 초기화되며 영속 저장은 범위 밖이다.

계층: infrastructure (observability).
"""

from __future__ import annotations

import threading
from typing import Any

#: 유형당 보관 표본 상한(링 버퍼). 분포 추정에 충분하고 메모리는 유계다.
_MAX_SAMPLES = 200

#: 시간 문구를 사용자에게 보여도 되는 최소 표본 수(§5.5 S-C).
#: 미만이면 호출부는 그룹 수만 표기하고 **초 단위 추정치를 만들지 않는다**.
MIN_SAMPLES_FOR_ESTIMATE = 20

_lock = threading.Lock()
_samples: dict[tuple[str, str, str, str], list[float]] = {}


def _key(group: dict) -> tuple[str, str, str, str]:
    """계측 키 — (solution, zone_group, kind, backend).

    존별이 아니라 **유형별**로 모은다. 같은 유형이면 소요 특성이 비슷하고, 존을 하나
    추가했을 때의 비용을 이 분포로 추정할 수 있다.
    """
    return (
        str(group.get("solution", "")),
        str(group.get("zone_group", "")),
        str(group.get("kind", "peer")),
        str(group.get("backend", "sql")),
    )


def record_group(group: dict, elapsed_ms: float) -> None:
    """그룹 실행 1건의 소요를 기록한다(음수·비수치는 무시)."""
    try:
        value = float(elapsed_ms)
    except (TypeError, ValueError):
        return
    if value < 0:
        return
    with _lock:
        bucket = _samples.setdefault(_key(group), [])
        bucket.append(value)
        if len(bucket) > _MAX_SAMPLES:
            del bucket[: len(bucket) - _MAX_SAMPLES]


def _percentile(sorted_values: list[float], pct: float) -> float:
    """가장 가까운 순위(nearest-rank) 백분위. 표본이 적어도 정의된다."""
    if not sorted_values:
        return 0.0
    idx = max(0, min(len(sorted_values) - 1, int(round(pct * (len(sorted_values) - 1)))))
    return sorted_values[idx]


def group_stats(group: dict) -> dict[str, Any]:
    """한 그룹 유형의 분포를 반환한다.

    Returns:
        `{sample_size, p50_ms, p90_ms, estimate_ready}`.
        `estimate_ready`가 False면 **호출부는 시간 문구를 만들지 않는다**(§5.5 S-C).
    """
    with _lock:
        values = sorted(_samples.get(_key(group), []))
    return {
        "sample_size": len(values),
        "p50_ms": _percentile(values, 0.5),
        "p90_ms": _percentile(values, 0.9),
        "estimate_ready": len(values) >= MIN_SAMPLES_FOR_ESTIMATE,
    }


def estimate_seconds(groups: list[dict]) -> dict[str, Any]:
    """그룹 목록의 예상 소요를 낸다 — **표본이 부족하면 숫자를 만들지 않는다**.

    Returns:
        `{ready, groups, seconds_lo, seconds_hi}`. `ready=False`면 `seconds_*`는 None이며
        호출부는 그룹 수만 사용자에게 보여준다(환각 금지 — plans/82 §5.5 S-C).
    """
    billable = [g for g in (groups or []) if g.get("kind", "peer") != "discovery"]
    if not billable:
        return {"ready": False, "groups": 0, "seconds_lo": None, "seconds_hi": None}
    lo = hi = 0.0
    for group in billable:
        stats = group_stats(group)
        if not stats["estimate_ready"]:
            return {
                "ready": False,
                "groups": len(billable),
                "seconds_lo": None,
                "seconds_hi": None,
            }
        lo += stats["p50_ms"]
        hi += stats["p90_ms"]
    return {
        "ready": True,
        "groups": len(billable),
        "seconds_lo": round(lo / 1000, 1),
        "seconds_hi": round(hi / 1000, 1),
    }


def snapshot() -> dict[str, dict[str, Any]]:
    """유형별 분포 전체를 반환한다(운영 조회·디버깅용)."""
    with _lock:
        keys = list(_samples)
    return {
        "|".join(k): group_stats(
            {"solution": k[0], "zone_group": k[1], "kind": k[2], "backend": k[3]}
        )
        for k in keys
    }


def reset() -> None:
    """수집 상태를 비운다(테스트 전용)."""
    with _lock:
        _samples.clear()
