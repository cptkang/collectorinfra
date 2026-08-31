"""존 순회 대상 소재 탐색 — 실행 (Plan 82 Wave 5 · D-176 후속3).

**무엇을 하나.** 인가된 폴스타 존을 `query_order` 순으로 돌며 서버 식별자의 소재를 찾는다.
판정은 하지 않는다 — 결과를 `SweepOutcome`으로 모으고 `src/domain/host_discovery.py`가 판정한다.

**text2sql 파이프라인이 아니다.** LLM을 쓰지 않고 존당 고정 조회 1회만 한다. 감사로그 918건
실측으로 존당 SQL p50 49~53ms이므로 3존 순회가 **약 150ms**다 — 스키마 분석(존당 ≤20s)·
LLM 생성(90k~136k tok) 대비 무시할 수준이라, 여기서 사용자에게 되묻는 것은 **순손실**이다.

**재사용하고 새로 만들지 않는다.** 조회는 `noise_gate.infrastructure.polestar_hostname_resolver`
의 `lookup_host`를 쓴다 — `process_query`·`fault_diagnosis`·`investigation_trigger`가 이미 쓰는
**같은 함수**이며, 그래야 fail-open 규약이 한쪽에만 들어가는 일이 없다(D-171 G5 선례).

## 세 가지 규약

1. **인가된 존만 순회한다.** 권한 밖 존을 돌면 *그 존에 그 서버가 있는지* 가 응답의 형태로
   새어나간다 — 결과를 감춰도 순회했다는 사실 자체가 정보다.
2. **전수 순회가 기본이다**(U4). 동명 호스트가 여러 존에 있을 수 있고, 첫 히트에서 끊으면
   그 사실이 은폐된다. 조기 종료는 옵트인 플래그로만 연다.
3. **0건은 캐시하지 않는다**(U12의 필수 가드). 캐시하면 방금 등록한 서버가 TTL 동안
   "없는 서버"가 된다 — 신규 등록 직후가 조회 수요가 가장 큰 시점이다.

계층: orchestration.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable, Optional, Sequence

from src.domain.host_availability import REASON_LOOKUP_FAILED
from src.domain.host_discovery import SweepOutcome, ZoneHit

logger = logging.getLogger(__name__)

#: 조회 대역 주입점 — `(db_id, identifier) -> HostLookup`. 미지정이면 운영 경로를 쓴다.
LookupFn = Callable[[str, str], Awaitable[Any]]

#: {(identifier, db_ids 튜플): (만료 시각, SweepOutcome)}. 프로세스 로컬·요청 간 공유.
_CACHE: dict[tuple[str, tuple[str, ...]], tuple[float, SweepOutcome]] = {}
#: 캐시 항목 상한 — 값 만료뿐 아니라 **키 증가**도 막는다(Known Mistakes: 데몬류
#: in-memory dict는 키 만료 sweep도 필요).
_CACHE_MAX = 512


def _zone_label(db_id: str) -> str:
    """db_id의 사용자 표시 라벨. 존 선택 UI와 **같은 문구**를 쓴다(사본 금지)."""
    from src.utils.query_gen_common import _ZONE_LABEL_BY_DB

    return _ZONE_LABEL_BY_DB.get(db_id, db_id)


def sweep_order(db_ids: Sequence[str] | None) -> list[str]:
    """순회 순서를 확정한다 — `query_order`(은행존 → 공동존) 정본.

    입력 배열 순서에 의존하지 않는다. 라우터의 `relevance_score` 정렬 결과가 호출마다
    달라지면 같은 대상 집합이 다른 순서로 순회돼 재현·비교가 불가능해진다(D-035).
    """
    from src.routing.execution_groups import partition_execution_groups

    ordered: list[str] = []
    for group in partition_execution_groups(list(db_ids or [])):
        for db_id in group.get("db_ids") or []:
            if db_id not in ordered:
                ordered.append(db_id)
    return ordered


def authorized_zones(
    db_ids: Sequence[str] | None, allowed_db_ids: Sequence[str] | None
) -> list[str]:
    """순회 대상을 **인가된 존으로 먼저 좁힌다**.

    `allowed_db_ids`가 None이면 전체 허용(기존 규약 — `src/state.py` 주석 참조).
    빈 리스트는 "허용 0건"이므로 전체 허용과 구분한다.
    """
    ordered = sweep_order(db_ids)
    if allowed_db_ids is None:
        return ordered
    allowed = set(allowed_db_ids)
    return [d for d in ordered if d in allowed]


def _cache_key(identifier: str, db_ids: Sequence[str]) -> tuple[str, tuple[str, ...]]:
    return (identifier, tuple(db_ids))


def _cache_get(key: tuple[str, tuple[str, ...]], now: float) -> Optional[SweepOutcome]:
    entry = _CACHE.get(key)
    if not entry:
        return None
    expires_at, outcome = entry
    if expires_at <= now:
        _CACHE.pop(key, None)
        return None
    return outcome


def _cache_put(
    key: tuple[str, tuple[str, ...]], outcome: SweepOutcome, ttl: float, now: float
) -> None:
    """히트가 있을 때만 캐시한다 — **0건·조회 실패는 캐시 금지**.

    0건을 캐시하면 신규 등록 서버가 TTL 동안 안 보이고, 조회 실패를 캐시하면 일시적
    장애가 TTL 동안 고정된 사실이 된다. 둘 다 사용자가 재시도로 회복할 수 없게 만든다.
    """
    if ttl <= 0 or not outcome.hits or outcome.errors:
        return
    if len(_CACHE) >= _CACHE_MAX:
        for stale_key, (expires_at, _) in list(_CACHE.items()):
            if expires_at <= now:
                _CACHE.pop(stale_key, None)
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)), None)
    _CACHE[key] = (now + ttl, outcome)


def clear_cache() -> None:
    """탐색 캐시를 비운다(테스트·운영 리셋용)."""
    _CACHE.clear()


async def sweep_zones(
    identifier: str,
    db_ids: Sequence[str] | None,
    *,
    lookup: LookupFn,
    allowed_db_ids: Sequence[str] | None = None,
    early_exit: bool = False,
    ttl_seconds: float = 60.0,
    now: Optional[float] = None,
) -> SweepOutcome:
    """인가된 존을 순회해 식별자의 소재를 찾는다.

    Args:
        identifier: 서버명 또는 호스트명
        db_ids: 후보 존(활성 폴스타 인스턴스)
        lookup: `(db_id, identifier) -> HostLookup` 조회 대역(주입점).
            운영 경로는 `noise_gate...lookup_host`를 부분 적용해 넘긴다 —
            **이 모듈은 app_config를 모른다**(테스트가 DB 없이 전량 검증된다).
        allowed_db_ids: 인가된 db_id. None이면 전체 허용
        early_exit: True면 첫 히트에서 중단(U4 — 기본은 전수 순회)
        ttl_seconds: 결과 캐시 TTL(U12 · 0 이하면 캐시 비활성)
        now: 시각 주입(테스트용)

    Returns:
        `SweepOutcome` — **조회 실패는 `errors`에 사유로 남고 순회는 계속된다**.
        한 존이 죽었다고 나머지를 포기하면 있는 서버도 못 찾는다.
    """
    clock = time.monotonic() if now is None else now
    targets = authorized_zones(db_ids, allowed_db_ids)
    key = _cache_key(identifier, targets)

    cached = _cache_get(key, clock)
    if cached is not None:
        logger.info("탐색 캐시 적중: identifier=%s zones=%d", identifier, len(targets))
        return cached

    hits: list[ZoneHit] = []
    errors: dict[str, str] = {}
    swept: list[str] = []

    for db_id in targets:
        label = _zone_label(db_id)
        swept.append(label)
        try:
            result = await lookup(db_id, identifier)
        except Exception as exc:  # noqa: BLE001 — 한 존의 실패가 순회를 멈추면 안 된다
            logger.warning("탐색 조회 예외: db_id=%s err=%s", db_id, exc)
            errors[label] = type(exc).__name__
            continue

        availability = getattr(result, "availability", None)
        if getattr(availability, "reason", None) == REASON_LOOKUP_FAILED:
            # ★ "없다"가 아니라 "확인하지 못했다" — 절대 합치지 않는다.
            errors[label] = "조회 실패"
            continue

        hostname = getattr(result, "hostname", None)
        server_name = getattr(result, "server_name", None)
        if not hostname and not server_name:
            continue  # 이 존에는 없다(정상 결과)

        hits.append(ZoneHit(
            db_id=db_id,
            zone_label=label,
            hostname=hostname or "",
            server_name=server_name or "",
            availability=availability,
        ))
        if early_exit:
            break

    outcome = SweepOutcome(
        identifier=identifier,
        swept=tuple(swept),
        hits=tuple(hits),
        errors=errors,
    )
    logger.info(
        "탐색 완료: identifier=%s 순회=%d 히트=%d 실패=%d",
        identifier, len(swept), len(hits), len(errors),
    )
    _cache_put(key, outcome, ttl_seconds, clock)
    return outcome
