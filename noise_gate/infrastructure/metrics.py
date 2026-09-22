"""노이즈 게이트 결정 카운터 — Prometheus 노출용 증가형 집계 (plans/92 트랙 B-1 · O4).

`noise_gate_decisions_total{stage,decision}`을 pip 패키지 `prometheus_client`의 **프로세스 전역
`REGISTRY`**에 정의한다. 본체 `/metrics`(`src/api/routes/metrics.py`)가 같은 전역 레지스트리를
직렬화하므로 이 모듈과 `src` 사이 import는 0이다(D-139). 증가는 `DecisionStore.record()`가 한다.

**두 집계가 병존한다.** 결정 퍼널의 정본은 JSONL을 다시 읽어 세는 `DecisionStore.funnel()`이다
(관제 API `/api/v1/admin/noise/*`·`/api/v1/noise/*`가 쓴다). 이 카운터는 프로세스 수명 동안만
누적되고(재기동 시 0) 파일 쓰기 실패 건도 세며, 퍼널은 조회 창·`max_lines` 안에서 다시 센다.
**두 값이 다르면 파일 퍼널이 정본이다.**

플래그와 무관하게 증가한다. 값은 이 프로세스의 메모리에만 있고, 밖으로 내보내는 곳은
`OBS_METRICS_ENDPOINT_ENABLED`가 켜졌을 때만 등록되는 본체 `/metrics` 하나뿐이다 — 꺼져 있으면
관찰 가능한 동작(판정·통보·감사 파일)이 바뀌지 않는다.

라벨 값은 유한 집합으로 묶는다 — `stage` ∈ `STAGE_ORDER` ∪ {`unknown`, `other`},
`decision`(= 티어) ∈ {page, ticket, dashboard, suppress, `other`}. 단계가 비면 퍼널과 같은 규칙
(`stage_from_reason`)으로 사유 접두에서 되짚는다.

이름 주의: 같은 패키지의 `noise_gate/infrastructure/prometheus_client.py`는 PromQL 조회 클라이언트로
이 모듈과 무관하다. 아래 `import prometheus_client`는 절대 import라 pip 패키지로 해석된다.
"""

from __future__ import annotations

from prometheus_client import Counter

from noise_gate.domain.notification_policy import (
    STAGE_ORDER,
    STAGE_UNKNOWN,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
    stage_from_reason,
)

#: 정의된 어휘 밖의 값을 묶는 라벨 값.
OTHER_LABEL = "other"

_KNOWN_STAGES: frozenset[str] = frozenset(STAGE_ORDER) | {STAGE_UNKNOWN}
_KNOWN_DECISIONS: frozenset[str] = frozenset(
    {TIER_PAGE, TIER_TICKET, TIER_DASHBOARD, TIER_SUPPRESS}
)

NOISE_GATE_DECISIONS_TOTAL = Counter(
    "noise_gate_decisions",
    "노이즈 게이트 발송 판단 수(결정 단계·티어별). 정본은 결정 감사 파일 퍼널이다.",
    ("stage", "decision"),
)


def record_decision(*, stage: str, reason: str, tier: str) -> None:
    """발송 판단 1건을 센다.

    Args:
        stage: 결정 단계(비면 사유 접두로 되짚는다 — 퍼널 `_stage_of`와 같은 규칙)
        reason: 결정 사유(단계가 빌 때만 쓴다)
        tier: 결정 티어
    """
    resolved = stage or stage_from_reason(reason)
    NOISE_GATE_DECISIONS_TOTAL.labels(
        stage=resolved if resolved in _KNOWN_STAGES else OTHER_LABEL,
        decision=tier if tier in _KNOWN_DECISIONS else OTHER_LABEL,
    ).inc()
