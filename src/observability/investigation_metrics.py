"""호스트 조사 관측 — Tier 2 판별 지표 (Plan 78 W6-3·4 · **Tier 1**).

> **이 모듈이 왜 먼저인가**(78 §4.6.2): W2-7·8(압축·캐시)과 W3-2·3(경로 선택·진입 게이트)은
> **Tier 2**이고, 여기 지표가 없으면 **그 최적화의 이득을 판정할 수 없다.** 하네스 문서의
> "자주 하는 실수" 1항이 *측정 없이 하네스를 쌓는다* 이다. 번호(W6)는 마지막이지만 순서는 앞이다.

감사(`audit_logger.log_investigation`)와 목적이 다르다 — 감사는 *"누가 무엇을 조사했는가"* 의
규정 준수 기록이고, 여기는 *"최적화를 착수해도 되는가"* 의 판정 재료다(`ObservabilityConfig`
docstring이 같은 구분을 이미 세워 두었다).

## 남기는 지표 4종 (78 W6-4)

| 축 | 지표 | 무엇을 판정하는가 |
|---|---|---|
| 압축 | 호스트당 절단 행 수 · 축약 전후 토큰 수 | W2-7 결정적 2단 축약의 손실 대비 이득 |
| 캐시 | 히트/미스 · 히트 시 데이터 나이 | W2-8 단기 캐시의 TTL이 적정한가 |
| 라우팅 | 조사 경로 진입 건수 · 게이트 거부 사유별 건수 | W3-2·3 경로 선택이 실제로 무엇을 바꾸는가 |
| 비용 귀속 | 조사 1건당 토큰·지연 | 문서 6.4가 지목한 생태계 공백 — 표준이 없어 직접 남긴다 |

## in-memory 상한

프로세스 수명 동안 누적되므로 **키 증가를 막는다**(Known Mistakes — in-memory dict는 값
bound뿐 아니라 키 상한도 필요하다). 사유별 카운터는 상한을 넘으면 새 키를 만들지 않고
`_OVERFLOW_KEY`에 합산한다 — 조용히 버리면 거부가 없었던 것처럼 보인다.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: 사유별 카운터의 서로 다른 키 상한. 거부 사유는 코드가 만드는 닫힌 집합이지만,
#: 외부 문자열이 섞여 들어올 여지를 남기지 않는다.
_MAX_REASON_KEYS = 64
_OVERFLOW_KEY = "_overflow"

_lock = threading.Lock()


def _empty() -> dict[str, Any]:
    """지표 4종의 초기 스냅샷."""
    return {
        "compaction": {
            "events": 0,
            "rows_truncated": 0,
            "tokens_before": 0,
            "tokens_after": 0,
            "per_host_truncated": {},
        },
        "cache": {"hits": 0, "misses": 0, "hit_age_seconds_total": 0.0},
        "routing": {"investigations": 0, "denied": 0, "denied_by_reason": {}},
        "cost": {"investigations": 0, "tokens_total": 0, "duration_ms_total": 0.0},
    }


_metrics: dict[str, Any] = _empty()


def _bump_reason(bucket: dict[str, int], reason: str) -> None:
    """사유별 카운터를 올린다. 키 상한을 넘으면 overflow로 합산한다(조용히 버리지 않는다)."""
    key = reason or "unknown"
    if key not in bucket and len(bucket) >= _MAX_REASON_KEYS:
        key = _OVERFLOW_KEY
    bucket[key] = bucket.get(key, 0) + 1


def record_compaction(
    *, host: str, rows_truncated: int, tokens_before: int = 0, tokens_after: int = 0
) -> None:
    """압축 지표를 남긴다 (W2-7 · 압축 손실 기록).

    Args:
        host: 대상 호스트
        rows_truncated: 그 호스트에서 절단된 행 수
        tokens_before: 축약 전 토큰 수(추정치여도 대조에 쓸 수 있다)
        tokens_after: 축약 후 토큰 수
    """
    with _lock:
        c = _metrics["compaction"]
        c["events"] += 1
        c["rows_truncated"] += max(0, rows_truncated)
        c["tokens_before"] += max(0, tokens_before)
        c["tokens_after"] += max(0, tokens_after)
        if rows_truncated > 0:
            per_host = c["per_host_truncated"]
            if host in per_host or len(per_host) < _MAX_REASON_KEYS:
                per_host[host] = per_host.get(host, 0) + rows_truncated
            else:
                per_host[_OVERFLOW_KEY] = per_host.get(_OVERFLOW_KEY, 0) + rows_truncated


def record_cache(*, hit: bool, age_seconds: float = 0.0) -> None:
    """캐시 지표를 남긴다 (W2-8).

    히트 시 **데이터 나이**를 함께 센다 — 히트율만 보면 "오래된 값을 재사용해 히트율이
    높은" 상태와 "TTL이 적정한" 상태가 구분되지 않는다.
    """
    with _lock:
        c = _metrics["cache"]
        if hit:
            c["hits"] += 1
            c["hit_age_seconds_total"] += max(0.0, age_seconds)
        else:
            c["misses"] += 1


def record_investigation(
    *, tokens: int = 0, duration_ms: float = 0.0, denied_reason: Optional[str] = None
) -> None:
    """조사 1건의 라우팅·비용 지표를 남긴다 (W6-4 라우팅 · 비용 귀속).

    Args:
        tokens: 조사에 쓰인 토큰(없으면 0)
        duration_ms: 조사 소요 시간
        denied_reason: 게이트 거부 사유. 주어지면 거부로 집계하고 진입으로 세지 않는다
    """
    with _lock:
        r = _metrics["routing"]
        if denied_reason:
            r["denied"] += 1
            _bump_reason(r["denied_by_reason"], denied_reason)
            return
        r["investigations"] += 1
        c = _metrics["cost"]
        c["investigations"] += 1
        c["tokens_total"] += max(0, tokens)
        c["duration_ms_total"] += max(0.0, duration_ms)


def snapshot() -> dict[str, Any]:
    """현재 지표를 **사본**으로 반환한다(호출부 변형이 내부를 오염시키지 않도록)."""
    with _lock:
        return {
            "compaction": {**_metrics["compaction"],
                           "per_host_truncated": dict(_metrics["compaction"]["per_host_truncated"])},
            "cache": dict(_metrics["cache"]),
            "routing": {**_metrics["routing"],
                        "denied_by_reason": dict(_metrics["routing"]["denied_by_reason"])},
            "cost": dict(_metrics["cost"]),
        }


def reset() -> None:
    """지표를 비운다 (테스트 격리용)."""
    global _metrics
    with _lock:
        _metrics = _empty()


def tier2_ready() -> tuple[bool, str]:
    """Tier 2(압축·캐시·경로 선택) 착수 가능 여부를 판정한다 (78 §4.6.2).

    *측정 없이 최적화를 쌓지 않는다.* 조사가 한 건도 관측되지 않았다면 압축·캐시의 이득을
    비교할 기준선이 없다.

    Returns:
        (착수 가능 여부, 사유)
    """
    snap = snapshot()
    observed = snap["routing"]["investigations"] + snap["routing"]["denied"]
    if observed == 0:
        return False, "조사 관측 0건 — 압축·캐시의 이득을 비교할 기준선이 없다(78 §4.6.2)"
    return True, f"조사 {observed}건 관측 — Tier 2 대조 기준선 확보"


def log_investigation_startup(app_config: Any) -> dict[str, Any]:
    """기동 시 조사 경로 활성 상태·플래그 확정값을 **1줄로** 남긴다 (W6-3).

    사다리 로그(`observability/ladder.py`) 전례를 따른다 — 플래그는 **기동 시 1회** 해석하고
    (78 P14 · 요청 시점 변경 금지), 확정값을 로그에 남겨 "무엇이 켜진 채 돌고 있는가"를
    나중에 재구성할 수 있게 한다.

    Args:
        app_config: 앱 설정

    Returns:
        기록한 확정값 dict(테스트·진단용)
    """
    composite = getattr(app_config, "composite", None)
    gate = getattr(app_config, "noise_gate", None)
    resolved = {
        "prior_targets_enabled": bool(getattr(composite, "prior_targets_enabled", False)),
        "target_column_llm_enabled": bool(
            getattr(composite, "target_column_llm_enabled", False)
        ),
        "investigation_enabled": bool(getattr(composite, "investigation_enabled", False)),
        "audit_enabled": bool(getattr(composite, "audit_enabled", True)),
        "max_targets": int(getattr(composite, "max_targets", 0) or 0),
        "fanout_concurrency": int(getattr(composite, "fanout_concurrency", 0) or 0),
        "host_authz_mode": str(
            getattr(getattr(app_config, "host_authz", None), "mode", "") or ""
        ),
        # 조사 경로의 실제 가용성 — 플래그가 켜져 있어도 이 둘이 off면 조사는 일어나지 않는다.
        "fault_diagnosis_enabled": bool(getattr(gate, "fault_diagnosis_enabled", False)),
        "investigation_trigger_enabled": bool(
            getattr(gate, "investigation_trigger_enabled", False)
        ),
    }
    logger.info(
        "호스트 조사 경로 확정: %s",
        " ".join(f"{k}={v}" for k, v in resolved.items()),
    )
    return resolved
