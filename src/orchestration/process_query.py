"""실시간 프로세스 조회 subagent (Plan 50 M4).

"현재/실시간 프로세스 리스트" 류 질의를 DB 조회가 아닌 폴스타 **실시간 프로세스 API**로
처리한다. 없는 테이블(`SDQ000.MON_CF_WAIT_TIME`) 조회로 인한 `SQL0204N` 재발을 막는다.

재사용 (신규 비즈니스 로직 없음):
- 조회: `noise_gate.infrastructure.polestar_process_api.PolestarProcessApiClient` (Plan 47-1, infrastructure).
- 선별·마스킹: `noise_gate.domain.process_rank.select_top_processes` (Plan 47-1, domain — 결정적 처리).
  → 마스킹·상위 N 선별은 결정적으로 수행하고 LLM에 원시 주입하지 않는다(D-047-1 / Known Mistakes 정합).

대상 결정:
- db_id: task.db_ids(승계/고정) → conversation_context.previous_db_ids → 위치 기반 분류(classify_dbs) 순.
- hostname: 이번 턴 filter_conditions 식별 키 → conversation_context.previous_entities 순 ("해당 서버" 해소, M3).

서버명 → 호스트명 해소 (D-046):
- 프로세스 API의 조회 키는 **hostname**이지만 사용자는 보통 서버명(cmm_resource.name)으로 질의한다.
  공동존 폴스타(gp/yd)는 name ≠ hostname 이므로 서버명을 그대로 hostname으로 보내면 0건 → 환각.
- `PolestarHostnameResolver`(infrastructure)로 입력 값을 정규 hostname으로 해소한 뒤 API를 호출한다.
  해소 실패/0건이면 원시 입력 값을 그대로 사용(이미 hostname인 경우 호환).

base_url 매핑(`AlarmConfig.get_process_api_base_url`)이 없는 db_id는 graceful degradation(안내 메시지).

계층: 본 모듈은 orchestration. polestar_process_api/polestar_hostname_resolver는 infrastructure,
process_rank는 domain → orchestration → {infrastructure, domain} 의존은 정합(arch_check 통과).
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel

from noise_gate.domain.process_rank import select_top_processes
from noise_gate.infrastructure.polestar_process_api import PolestarProcessApiClient
from src.config import AppConfig, load_config
from src.domain.host_availability import (
    HostAvailability,
    describe as describe_availability,
)
from src.observability.investigation_metrics import record_compaction
from src.orchestration.investigation_cache import (
    InvestigationCache,
    freshness_note,
)
from src.routing.registry import get_registry
from src.utils.prior_targets import (
    TargetResolution,
    build_prior_targets,
    resolve_targets,
)

logger = logging.getLogger(__name__)

# 프로세스 정렬 기준 — 일반 "현재 프로세스 리스트"는 CPU 점유 내림차순으로 본다(메모리 의도 키워드 시 memory).
_MEMORY_QUERY_HINTS = ("메모리", "memory", "mem ", "ram")
# hostname으로 인정할 filter_conditions field / previous_entities field.
# 영문 컬럼명뿐 아니라 LLM이 한글 필드명으로 추출하는 경우(서버명/장비명/호스트명 등)도 방어적으로 인정한다
# (input_parser는 hostname으로 정규화하도록 유도하지만 LLM 출력은 비결정적이므로 한글 변형까지 수용).
# canonical은 utils.query_gen_common(D-153 — 존 역질문 후단 게이트가 routing 계층에서도
# 사용, 계층 규칙상 utils로 이동). 여기서는 기존 이름으로 re-export만 유지(사본 금지, D-053).
from src.utils.query_gen_common import (
    DEMONSTRATIVE_NOUNS as _DEMONSTRATIVE_NOUNS,
    DEMONSTRATIVE_PREFIXES as _DEMONSTRATIVE_PREFIXES,
    HOST_IDENTIFIER_FIELDS as _HOST_FIELDS,
)
# 위치 → db_id 매핑 신호. 첫 턴(task.db_ids/previous_db_ids 공백)에 질의 텍스트로
# db_id를 재도출하는 결정적 폴백. 단일 DB를 **배타적으로** 지목하는 위치 표면어만 담긴다
# (여러 DB를 포괄하는 존 표면어 "공동존"은 대상 DB를 좁히지 못하므로 제외).
# 정본은 `config/db_registry.yaml`의 locations 선언 — Plan 67 R2, 사본 금지.
_LOCATION_DB_HINTS: dict[str, tuple[str, ...]] = get_registry().location_db_hints()


def _infer_alarm_kind(sub_query: str) -> str:
    """질의에서 정렬 기준(cpu/memory)을 추론한다 (기본 cpu)."""
    lowered = (sub_query or "").lower()
    if any(h in lowered for h in _MEMORY_QUERY_HINTS):
        return "memory"
    return "cpu"


def _resolve_db_id(
    task: dict,
    isolated: dict,
    sub_query: str,
    app_config: AppConfig,
) -> Optional[str]:
    """프로세스 API 대상 db_id를 결정한다.

    우선순위: ① task.db_ids(UI 확정/고정) > ② **prior_targets(이번 턴 선행 결과)** >
    ③ previous_db_ids(멀티턴 승계) > ④ 위치 신호 매칭.
    base_url 매핑이 있는 db_id를 우선 선택한다(조회 가능한 대상으로 좁힘).

    ②는 D-176 신설이다 — 선행 조회가 "이 서버는 공동존에 있다"를 확정해도 그 값이 여기로
    흐르지 않아 후속 프로세스 조회가 엉뚱한 존을 쳤다(plans/82 §2.3). 이번 턴 결과가
    직전 턴 승계(③)보다 강하다(요청 스코프 우선). 다만 **①보다는 뒤**다 — 존 선택 UI에서
    사용자가 확정한 값은 어떤 추론보다 우선한다는 기존 원칙을 지킨다(D-143).

    Args:
        task: 현재 TaskSpec
        isolated: 격리 입력(conversation_context 포함)
        sub_query: 이번 턴 질의
        app_config: 앱 설정

    Returns:
        대상 db_id 또는 None
    """
    alarm_cfg = app_config.alarm

    def _has_base_url(did: str) -> bool:
        return bool(did) and bool(alarm_cfg.get_process_api_base_url(did))

    candidates: list[str] = []

    # ① task 고정/승계 db_ids
    for did in task.get("db_ids") or []:
        if isinstance(did, str) and did not in candidates:
            candidates.append(did)

    # ② 이번 턴 선행 결과가 확정한 존 (D-176) — prior_targets는 행별 `_source_db`에서
    #    해소된 대상이라 "어느 존에서 찾았는가"를 이미 담고 있다.
    for _t in isolated.get("prior_targets") or []:
        if not isinstance(_t, dict):
            continue
        did = _t.get("db_id")
        if isinstance(did, str) and did and did not in candidates:
            candidates.append(did)

    # ③ 멀티턴 승계 previous_db_ids
    ctx = isolated.get("conversation_context") or {}
    for did in ctx.get("previous_db_ids") or []:
        if isinstance(did, str) and did not in candidates:
            candidates.append(did)

    # base_url 매핑이 있는 후보 우선
    for did in candidates:
        if _has_base_url(did):
            return did

    # ④ 위치 신호 매칭 (sub_query + previous_location)
    location_text = f"{sub_query} {ctx.get('previous_location', '')}"
    hint_matches = [
        did for did, hints in _LOCATION_DB_HINTS.items()
        if any(h in location_text for h in hints)
    ]
    # base_url 매핑이 있는 매치를 우선하되, 없더라도 위치 신호가 명확하면 해당 db_id를 반환한다
    # (db_id=None으로 빠져 "위치 미식별"이라는 잘못된 안내가 나가는 것을 막고, base_url 게이트가
    #  "API 미연결" 같은 정확한 원인을 알리도록 한다).
    for did in hint_matches:
        if _has_base_url(did):
            return did
    if hint_matches:
        return hint_matches[0]

    # 후보가 있으나 base_url이 없으면 첫 후보 반환(호출부에서 graceful 안내)
    return candidates[0] if candidates else None


def _is_demonstrative_value(value: object) -> bool:
    """서버 식별자 값이 실제 이름이 아니라 지시어/플레이스홀더인지 판정한다.

    후속 턴에서 "해당 서버/그 장비/위 서버" 같은 지시어가 input_parser에 의해 hostname
    필터로 그대로 추출되거나, LLM이 "previous_server" 같은 플레이스홀더를 지어내는 경우가
    있다. 이런 값이 이번 턴 filter로 들어오면 previous_entities(직전 실제 서버)보다
    우선순위가 높아 잘못된 hostname으로 API를 호출(0건→환각)하게 된다. 이를 결정적으로
    걸러 previous_entities 폴백으로 넘긴다(Known Mistakes: LLM 의존은 결정적 가드로 교정).

    Args:
        value: filter_conditions/previous_entities의 식별자 값

    Returns:
        지시어/플레이스홀더면 True (실제 서버명이면 False)
    """
    # 구현은 utils.query_gen_common으로 이동(D-153 단일 출처) — 동작 동일.
    from src.utils.query_gen_common import is_demonstrative_identifier

    return is_demonstrative_identifier(value)


def resolve_investigation_targets(
    isolated: dict, *, db_id: Optional[str] = None
) -> TargetResolution:
    """조사 대상 집합을 **공통 모듈**로 해소한다 (Plan 78 W1-4 · G2 + G5).

    세 진입 경로(`process_query`·`fault_diagnosis`·`investigation_trigger`)가 같은 함수를 쓴다 —
    각자 구현하면 개선이 한쪽에만 들어간다(§2.2 G5 실측).

    선행 결과는 두 형태로 도착할 수 있다:
    - `prior_targets` — `_make_isolated_input`이 이미 해소해 둔 대상 dict 목록
    - `prior_rows` — 아직 해소되지 않은 선행 결과 식별 행(도구 경로 등)

    후자는 여기서 `build_prior_targets`로 해소한다. **플래그가 off면 둘 다 보지 않는다** —
    미설정 시 현행 동작과 동일해야 한다(Plan 80 §5.4-③).

    Args:
        isolated: 격리 입력(parsed_requirements/conversation_context/prior_* 포함)
        db_id: 대상이 속한 DB(각 TargetRef에 실린다)

    Returns:
        `TargetResolution` — 대상·출처·절단·탈락 사유
    """
    cfg = load_config().composite
    parsed = isolated.get("parsed_requirements") or {}
    ctx = isolated.get("conversation_context") or {}

    prior_targets = None
    if cfg.prior_targets_enabled:
        prior_targets = isolated.get("prior_targets") or None
        if not prior_targets:
            prior_targets = _targets_from_prior_rows(
                isolated.get("prior_rows"), db_id=db_id, max_targets=cfg.max_targets
            )

    return resolve_targets(
        filter_conditions=parsed.get("filter_conditions"),
        prior_targets=prior_targets,
        previous_entities=ctx.get("previous_entities"),
        db_id=db_id,
        max_targets=cfg.max_targets,
    )


def _targets_from_prior_rows(
    prior_rows: object, *, db_id: Optional[str], max_targets: int
) -> Optional[list[dict]]:
    """`prior_rows`({task_id: [행, ...]})에서 조사 대상을 해소한다.

    해석 3단 중 **1·2단만** 쓴다 — LLM 컬럼 지목(3단)은 `COMPOSITE_TARGET_COLUMN_LLM_ENABLED`
    소관이고 이 경로는 결정적이어야 한다(호출부가 콜러블을 주입하지 않는다).
    """
    if not isinstance(prior_rows, dict):
        return None
    for rows in prior_rows.values():
        resolution = build_prior_targets(rows, db_id=db_id, max_targets=max_targets)
        if resolution.resolved:
            return resolution.as_state_value()
    return None


def _resolve_hostname(isolated: dict) -> Optional[str]:
    """대상 hostname을 결정한다 (단일 대상 호환 경로).

    Plan 78 W1-4 이후 해소 본체는 `resolve_investigation_targets`가 담당하고, 이 함수는
    **첫 대상 하나**를 종전 반환형(`Optional[str]`)으로 돌려주는 얇은 래퍼다.
    N-대상 fan-out은 W2가 `resolve_investigation_targets`를 직접 쓴다.

    Args:
        isolated: 격리 입력(parsed_requirements/conversation_context 포함)

    Returns:
        hostname 또는 서버명 문자열, 없으면 None
    """
    resolution = resolve_investigation_targets(isolated)
    if not resolution.resolved:
        return None
    first = resolution.targets[0]
    # 종전 순서 유지: 처음 매칭된 조건 종류를 우선한다 — `[{server_name}, {hostname}]`이
    # 들어오면 종전에도 server_name을 돌려줬다(첫 조건 우선).
    if resolution.column and resolution.column not in ("hostname", "host_name", "호스트명"):
        return first.server_name or first.hostname or first.ip
    return first.hostname or first.server_name or first.ip


async def _resolve_target_lookup(db_id: str, value: str, app_config: AppConfig):
    """입력 식별자를 정규 hostname으로 해소하고 **가용성 판정을 함께** 얻는다 (D-046 · Plan 81).

    판정 컬럼이 해소 SELECT에 함께 실리므로 **DB 왕복이 늘지 않는다**. 구현은
    `noise_gate.infrastructure.polestar_hostname_resolver.lookup_host` — 세 진입 경로가
    같은 함수를 쓴다(사본 금지 · D-171 G5 선례). 여기서는 이름만 유지한다.
    """
    from noise_gate.infrastructure.polestar_hostname_resolver import lookup_host

    return await lookup_host(app_config, db_id, value)


async def _lookup_targets(
    db_id: str, values: list[str], app_config: AppConfig
) -> dict:
    """다대상 가용성·hostname을 **1쿼리**로 조회한다 (Plan 81 fan-out 공용 함수 위임)."""
    from noise_gate.infrastructure.polestar_hostname_resolver import lookup_hosts

    return await lookup_hosts(app_config, db_id, values)


def _discovered_zone_label(discovery: Optional[dict]) -> str:
    """탐색이 확정한 존의 라벨(탐색 미발동·미확정이면 빈 문자열)."""
    if not discovery or discovery.get("state") != "resolved":
        return ""
    hits = (discovery.get("trace") or {}).get("hits") or []
    return str(hits[0].get("zone_label", "")) if hits else ""


async def _discover_db_id(
    identifier: str, isolated: dict, app_config: AppConfig
) -> tuple[Optional[str], Optional[dict]]:
    """인가된 존을 순회해 대상 존을 확정한다 (D-176 후속3 · `_resolve_db_id` ⑤).

    ①~④가 전부 실패했을 때만 호출된다 — 앞 순위가 하나라도 성립하면 진입하지 않는다.
    탐색은 **경과이지 결과가 아니므로** LLM 합성을 추가하지 않는다(U7) — 확정된 존으로
    본 조회가 이어지고, 기존 단일 합성이 그 결과를 서술한다.

    Args:
        identifier: 서버명 또는 호스트명
        isolated: 격리 입력(`allowed_db_ids` 포함)
        app_config: 앱 설정

    Returns:
        `(확정 db_id 또는 None, 안내 페이로드 또는 None)`.
        안내 페이로드는 **되묻거나 못 찾았을 때만** 채워진다.
    """
    cfg = app_config.composite
    if not cfg.host_discovery_enabled:
        return None, None

    from src.domain.host_discovery import (
        AMBIGUOUS,
        NOT_FOUND,
        RESOLVED,
        classify,
        render_ambiguous,
        render_not_found,
        trace_payload,
    )
    from src.orchestration.host_sweep import authorized_zones, sweep_zones

    # 순회할 존이 없으면 탐색이 아무것도 더하지 못한다 — 단일 DB 배포·존 미배정
    # 인스턴스(`ACTIVE_DB_IDS=polestar`)가 여기 해당한다. 이때는 **현행 안내 문구를
    # 그대로** 두는 것이 맞다(0개 존을 순회했다는 메시지는 소음이다).
    if not authorized_zones(
        app_config.multi_db.get_active_db_ids(), isolated.get("allowed_db_ids")
    ):
        logger.info("탐색 미진입 — 순회 가능한 폴스타 존 0개")
        return None, None

    async def _lookup(db_id: str, value: str):
        return await _resolve_target_lookup(db_id, value, app_config)

    outcome = await sweep_zones(
        identifier,
        app_config.multi_db.get_active_db_ids(),
        lookup=_lookup,
        allowed_db_ids=isolated.get("allowed_db_ids"),
        early_exit=cfg.discovery_early_exit,
        ttl_seconds=cfg.discovery_cache_ttl_seconds,
    )
    verdict = classify(outcome)
    trace = trace_payload(verdict)

    if verdict.state == RESOLVED:
        return verdict.db_id, {"state": RESOLVED, "trace": trace}
    if verdict.state == AMBIGUOUS:
        # 임의 선택하지 않는다 — 잘못 고르면 **오답이 정답처럼** 나간다(U5).
        return None, {
            "state": AMBIGUOUS, "trace": trace, "message": render_ambiguous(verdict),
        }
    return None, {
        "state": NOT_FOUND, "trace": trace, "message": render_not_found(outcome),
    }


def _process_to_dict(p) -> dict[str, Any]:  # noqa: ANN001 — ProcessInfo
    """ProcessInfo(마스킹 완료)를 결과 행 dict로 변환한다(args는 이미 마스킹됨)."""
    return {
        "name": p.name,
        "pid": p.pid,
        "user": p.user,
        "cpu_pct": p.p100cpu,
        "mem_pct": p.pmem,
        "rss": p.rss,
        "args": p.args,
    }


# ──────────────────────────────────────────────
# N-대상 fan-out (Plan 78 W2 · G3 해소)
# ──────────────────────────────────────────────

#: 같은 호스트에 대한 **동시 조사 금지** 락 (W2-6 · 부하 가드).
#: 대표 시나리오가 *이미 포화된 서버*를 조사하는 것이다 — 중복 조사가 장애를 악화시키면
#: 계획의 목적 자체가 무너진다. 키는 `(db_id, hostname)`.
_inflight_locks: dict[tuple[str, str], asyncio.Lock] = {}
#: 락 dict의 키 상한 — in-memory dict는 값 bound뿐 아니라 키 상한도 필요하다(Known Mistakes).
_MAX_INFLIGHT_KEYS = 512


def _inflight_lock(db_id: str, hostname: str) -> asyncio.Lock:
    """`(db_id, hostname)` 단위 직렬화 락을 얻는다.

    상한을 넘으면 **사용 중이 아닌** 락부터 버린다 — 사용 중인 락을 버리면 직렬화가 깨진다.
    """
    key = (db_id or "", hostname or "")
    lock = _inflight_locks.get(key)
    if lock is None:
        if len(_inflight_locks) >= _MAX_INFLIGHT_KEYS:
            for k, v in list(_inflight_locks.items()):
                if not v.locked():
                    _inflight_locks.pop(k, None)
                if len(_inflight_locks) < _MAX_INFLIGHT_KEYS:
                    break
        lock = asyncio.Lock()
        _inflight_locks[key] = lock
    return lock


#: 프로세스 스냅샷 캐시. TTL이 바뀌면 새로 만든다 — 플래그는 기동 시 1회 해석이지만
#: 테스트가 TTL을 바꿔가며 검증할 수 있어야 한다.
_snapshot_cache_instance: Optional[InvestigationCache] = None
_snapshot_cache_ttl: Optional[float] = None


def _snapshot_cache() -> Optional[InvestigationCache]:
    """설정된 TTL의 조사 캐시를 얻는다. TTL이 0 이하면 캐시를 쓰지 않는다."""
    global _snapshot_cache_instance, _snapshot_cache_ttl
    ttl = float(load_config().composite.snapshot_ttl_seconds or 0)
    if ttl <= 0:
        return None
    if _snapshot_cache_instance is None or _snapshot_cache_ttl != ttl:
        _snapshot_cache_instance = InvestigationCache(ttl_seconds=ttl)
        _snapshot_cache_ttl = ttl
    return _snapshot_cache_instance


#: 가용성 사전 판정으로 수집을 생략했을 때의 실패 사유 코드(Plan 81).
REASON_HOST_UNAVAILABLE = "host_unavailable"


async def _collect_one_target(
    db_id: str,
    identifier: str,
    alarm_kind: str,
    app_config: AppConfig,
    lookup=None,  # noqa: ANN001 — HostLookup (fan-out이 배치 조회 결과를 넘긴다)
) -> dict:
    """대상 1건의 프로세스 스냅샷을 수집한다 (단일·다중 경로 공용).

    단일 대상 경로가 종전과 **비트 동일**해야 하므로, 여기 로직은 v1 `run_process_query`의
    수집부를 그대로 옮긴 것이다(사본이 아니라 이동 — 두 경로가 이 함수 하나를 쓴다).

    **가용성 사전 판정**(Plan 81): 대상이 `unavailable`이면 프로세스 API를 **호출하지 않고**
    사유를 담아 반환한다. 판정이 꺼져 있으면(`availability_precheck_enabled=false`) 결과
    dict에 관련 키가 붙지 않아 종전과 비트 동일하다.

    Args:
        db_id: 대상 DB
        identifier: 서버명 또는 호스트명
        alarm_kind: 정렬 기준(cpu|memory)
        app_config: 앱 설정
        lookup: 이미 조회된 `HostLookup`(fan-out 배치 경로). None이면 여기서 조회한다

    Returns:
        `{ok, identifier, hostname, server_label, rows, total, captured_at, error}`
        (+ 판정 활성 시 `availability`·`notice`, 차단 시 `reason`)
    """
    if lookup is None:
        lookup = await _resolve_target_lookup(db_id, identifier, app_config)
    hostname = lookup.hostname or identifier
    server_label = identifier if hostname == identifier else f"{identifier}(호스트명 {hostname})"

    cfg = load_config().composite
    availability: HostAvailability = lookup.availability
    precheck_on = bool(cfg.availability_precheck_enabled)
    notice = describe_availability(availability, server_label) if precheck_on else ""
    # 판정 메타는 **활성일 때만** 실는다 — off 경로의 반환 dict를 종전과 비트 동일하게 유지한다.
    meta = {"availability": availability.to_dict(), "notice": notice} if precheck_on else {}

    if precheck_on and cfg.availability_block_on_unavailable and availability.blocks_collection:
        logger.info(
            "process_query 수집 생략(가용성 비정상): db_id=%s identifier=%s hostname=%s reason=%s",
            db_id, identifier, hostname, availability.reason,
        )
        return {
            "ok": False,
            "identifier": identifier,
            "hostname": hostname,
            "server_label": server_label,
            "rows": [],
            "total": 0,
            "captured_at": None,
            "error": "가용성 비정상(중지/통신이상) — 수집 생략",
            "reason": REASON_HOST_UNAVAILABLE,
            **meta,
        }

    # 단기 조사 캐시(W2-8 · Tier 2) — TTL 내 재조회는 **수집기를 부르지 않는다**.
    # 히트 사실과 나이는 결과에 실어 사용자에게 드러낸다(실시간 오인 방지 · 침묵 금지).
    cache = _snapshot_cache()
    # `is not None`으로 본다 — `InvestigationCache`는 `__len__`을 가지므로 **빈 캐시가
    # falsy**다. `if cache:`로 쓰면 첫 저장이 영원히 일어나지 않는다(실측 2026-08-27).
    cached = cache.get(db_id, hostname, alarm_kind) if cache is not None else None
    if cached is not None:
        rows, total, captured_at = cached.value
        return {
            "ok": True, "identifier": identifier, "hostname": hostname,
            "server_label": server_label, "rows": rows, "total": total,
            "captured_at": captured_at, "error": None,
            "cache": {"hit": True, "age_seconds": int(cached.age_seconds()),
                      "note": freshness_note(cached)},
            **meta,
        }

    async with _inflight_lock(db_id, hostname):
        client = PolestarProcessApiClient(app_config.alarm)
        result = await client.list_by_hostname(db_id, hostname)

    if result is None:
        return {
            "ok": False,
            "identifier": identifier,
            "hostname": hostname,
            "server_label": server_label,
            "rows": [],
            "total": 0,
            "captured_at": None,
            "error": "프로세스 API 미응답/타임아웃",
            **meta,
        }

    ranked_all, total = select_top_processes(
        result.processes, alarm_kind, len(result.processes or [])
    )
    rows = [_process_to_dict(p) for p in ranked_all]
    captured_at = str(result.captured_at) if result.captured_at else None
    if cache is not None:
        cache.put(db_id, hostname, alarm_kind, (rows, total, captured_at),
                  captured_at=captured_at)
    return {
        "ok": True,
        "identifier": identifier,
        "hostname": hostname,
        "server_label": server_label,
        "rows": rows,
        "total": total,
        "captured_at": captured_at,
        "error": None,
        "cache": {"hit": False, "age_seconds": 0},
        **meta,
    }


async def _fanout(
    db_id: str,
    identifiers: list[str],
    alarm_kind: str,
    app_config: AppConfig,
) -> tuple[list[dict], list[dict]]:
    """N개 대상을 동시 수집한다 (W2-1·2·3).

    - `Semaphore`로 동시 수 제한, 대상별 `wait_for`로 per-target 타임아웃
    - **대상별 개별 try/except** — 하나가 죽어도 나머지가 반환된다(Known Mistakes:
      "독립 신호 수집은 개별 try/except로 부분 반환 보장")
    - fan-out **전체** 타임아웃은 호출부가 씌운다 — per-call 타임아웃만으로는 무력화된다

    Returns:
        (성공 결과 목록, 실패 `{target, error}` 목록)
    """
    cfg = load_config().composite
    sem = asyncio.Semaphore(max(1, cfg.fanout_concurrency))

    # 가용성 판정을 **배치 1쿼리**로 선취한다(Plan 81) — 대상마다 조회하면 왕복이 N배가 된다.
    # 판정이 꺼져 있으면 배치를 돌리지 않는다: 대상별 해소가 종전 경로 그대로 동작해야 한다.
    lookups: dict = {}
    if cfg.availability_precheck_enabled:
        lookups = await _lookup_targets(db_id, identifiers, app_config)

    async def _one(identifier: str) -> dict:
        async with sem:
            try:
                return await asyncio.wait_for(
                    _collect_one_target(
                        db_id, identifier, alarm_kind, app_config,
                        lookup=lookups.get(identifier),
                    ),
                    timeout=cfg.target_timeout_seconds,
                )
            except asyncio.TimeoutError:
                return {"ok": False, "identifier": identifier, "hostname": identifier,
                        "server_label": identifier, "rows": [], "total": 0,
                        "captured_at": None, "error": "대상 타임아웃"}
            except Exception as exc:  # noqa: BLE001 — 대상 하나의 실패가 전체를 막지 않는다
                logger.warning("process_query 대상 실패: %s — %s", identifier, exc)
                return {"ok": False, "identifier": identifier, "hostname": identifier,
                        "server_label": identifier, "rows": [], "total": 0,
                        "captured_at": None, "error": str(exc)}

    outcomes = await asyncio.gather(*(_one(i) for i in identifiers))
    succeeded = [o for o in outcomes if o.get("ok")]
    # 실패에 **사유 코드**를 함께 싣는다 — "재시도하면 되는 실패"와 "재시도해도 같은 실패"를
    # 사용자가 구분할 수 있어야 한다(Plan 81 §1.1 ③).
    failed = [
        {
            "target": o["identifier"],
            "error": o["error"],
            "reason": o.get("reason"),
            "availability": o.get("availability"),
        }
        for o in outcomes if not o.get("ok")
    ]
    return succeeded, failed


#: 실패 사유 코드 → 사용자 라벨. **재시도 가치가 드러나야 한다** — 이것이 분해의 목적이다.
_FAILURE_REASON_LABELS: dict[str, str] = {
    REASON_HOST_UNAVAILABLE: "가용성 비정상(재시도해도 동일)",
    "collect_error": "조회 실패(재시도 가능)",
}


def _failure_breakdown(failed: list[dict]) -> str:
    """실패 대상을 **사유별로 집계**한다 (Plan 81 §1.1 ③).

    종전에는 실패가 "대상 타임아웃" 한 덩어리로 보여 재시도 가치가 있는 실패와 없는 실패를
    구분할 수 없었다.

    Args:
        failed: `{target, error, reason}` 목록

    Returns:
        `"가용성 비정상(재시도해도 동일) 2건 / 조회 실패(재시도 가능) 1건"` 형태. 없으면 빈 문자열
    """
    if not failed:
        return ""
    counts = Counter(f.get("reason") or "collect_error" for f in failed)
    return " / ".join(
        f"{_FAILURE_REASON_LABELS.get(code, code)} {n}건" for code, n in counts.most_common()
    )


def _compact_per_host_limit(base_top_n: int, host_count: int) -> int:
    """호스트 수에 따라 **호스트당 노출 행 수**를 결정적으로 축소한다 (W2-7 2단).

    호스트가 늘수록 표 전체가 선형으로 커진다 — 대상 10개 × 상위 10행이면 100행이다.
    총 노출 행을 대략 `base_top_n × 3` 안으로 묶되, 호스트당 최소 1행은 남긴다
    (0으로 줄이면 그 호스트가 표에서 사라져 "조사 안 됨"으로 오인된다).

    LLM 압축은 쓰지 않는다(§4.5-④ 미채택) — 결정적이어야 재현·감사가 된다.
    """
    if host_count <= 1 or base_top_n <= 0:
        return base_top_n
    return max(1, min(base_top_n, (base_top_n * 3) // host_count))


def _reduce_fanout(
    db_id: str,
    succeeded: list[dict],
    failed: list[dict],
    resolution: TargetResolution,
    alarm_kind: str,
    app_config: AppConfig,
) -> dict:
    """대상별 결과를 **결정적으로** 표 하나로 병합한다 (W2-4).

    응답에 반드시 포함한다: 조사 대상 수 / 성공 / 실패 / **절단 여부와 절단된 수**.
    부분 결과를 전체로 오인시키지 않기 위한 최소 조건이다(침묵 폴백 금지).

    표시·다운로드는 D-047 규약 계승 — 채팅은 호스트당 상위 N, CSV는 전량.
    """
    # 결정적 2단 축약(W2-7 · Tier 2):
    #   1단 — 호스트별 상위 N(이미 select_top_processes가 정렬·마스킹 완료)
    #   2단 — 호스트 수에 따라 **호스트당 노출 행 수를 동적 축소**
    # **원문 전량 보존이 필수 조건**(§3.4.3-⑤): 상위 N 선별은 정밀도 우선이라 문서의
    # 재현율 우선 권고와 어긋난다. 그 편차를 상쇄하는 조건이 `query_results`에 전량을
    # 남겨 손실을 복구 가능하게 두는 것이다 — 보존 없이 상위 N만 남기면 규칙 위반이 된다.
    base_top_n = max(0, app_config.alarm.process_top_n)
    per_host_n = _compact_per_host_limit(base_top_n, len(succeeded))
    chat_rows: list[dict] = []
    full_rows: list[dict] = []
    per_host_truncated: dict[str, int] = {}
    for item in succeeded:
        for row in item["rows"]:
            full_rows.append({"server": item["server_label"], "hostname": item["hostname"], **row})
        shown = item["rows"][:per_host_n]
        dropped = max(0, len(item["rows"]) - len(shown))
        if dropped:
            per_host_truncated[item["hostname"]] = dropped
            record_compaction(host=item["hostname"], rows_truncated=dropped)
        for row in shown:
            chat_rows.append({"server": item["server_label"], "hostname": item["hostname"], **row})
    top_n = per_host_n

    target_count = len(succeeded) + len(failed)
    metric_label = "메모리" if alarm_kind == "memory" else "CPU"
    parts = [
        f"대상 {target_count}건 중 {len(succeeded)}건 조사 완료"
        f"(실패 {len(failed)}건) — 호스트별 {metric_label} 점유 상위 {top_n}건을 표시합니다."
    ]
    if resolution.truncated:
        parts.append(
            f"⚠ 조사 대상이 상한({len(resolution.targets)}건)을 넘어 "
            f"{resolution.truncated_count}건이 제외됐습니다."
        )
    if failed:
        parts.append(
            "실패 대상: " + ", ".join(f"{f['target']}({f['error']})" for f in failed)
        )
        breakdown = _failure_breakdown(failed)
        if breakdown:
            parts.append(f"실패 사유: {breakdown}.")
    # 수집은 됐지만 알릴 만한 판정(점검·알 수 없음)이 있으면 함께 알린다(침묵 금지).
    notices = [n for n in (o.get("notice") for o in succeeded) if n]
    if notices:
        parts.append(" ".join(notices))
    if full_rows:
        parts.append(f"전체 {len(full_rows)}건은 'CSV 다운로드'로 받을 수 있습니다.")

    return {
        "organized_data": {
            "summary": " ".join(parts),
            "rows": chat_rows,
            "column_mapping": None,
            "resolved_mapping": None,
            "is_sufficient": bool(succeeded),
            "sheet_mappings": None,
        },
        "query_results": full_rows,
        "source": [{"db_id": db_id, "reason": "실시간 프로세스 API (Plan 78 W2 fan-out)"}],
        "target_db_ids": [db_id],
        "process_query": {
            "db_id": db_id,
            "target_count": target_count,
            "succeeded_count": len(succeeded),
            "failed_count": len(failed),
            "failed": failed,
            "truncated": resolution.truncated,
            "truncated_count": resolution.truncated_count,
            "total_count": sum(i["total"] for i in succeeded),
            "shown_count": len(chat_rows),
            "metric": alarm_kind,
            "targets": [
                {"server_name": t.server_name, "hostname": t.hostname, "db_id": t.db_id}
                for t in resolution.targets
            ],
            "captured_at": {i["hostname"]: i["captured_at"] for i in succeeded},
            # 압축 손실을 결과에 실는다(ETCLOVG 체크리스트 ③ — 조용한 절단 금지).
            "compaction": {
                "per_host_shown": per_host_n,
                "per_host_truncated": per_host_truncated,
                "rows_preserved": len(full_rows),
            },
            "cache": {
                i["hostname"]: (i.get("cache") or {}).get("hit", False) for i in succeeded
            },
            # 대상별 가용성 판정(판정 비활성이면 빈 dict — 종전과 동일한 소비 형태).
            # 실패 항목은 hostname 대신 target 키를 쓴다(`_fanout` 반환 형태).
            "availability": {
                **{i["hostname"]: i["availability"] for i in succeeded if i.get("availability")},
                **{f["target"]: f["availability"] for f in failed if f.get("availability")},
            },
        },
    }


async def run_process_query(
    task: dict,
    isolated: dict,
    *,
    llm: BaseChatModel,
    app_config: AppConfig,
) -> dict:
    """실시간 프로세스 리스트를 폴스타 프로세스 API로 조회한다 (Plan 50 M4 subagent handler).

    SUBAGENT_REGISTRY handler 규약(task, isolated, *, llm, app_config)을 따른다.
    llm은 사용하지 않는다(결정적 조회·선별 — 마스킹된 상위 N만 반환).

    **N-대상 fan-out**(Plan 78 W2 · G3): 대상이 여럿이면 동시 수집 후 결정적으로 병합한다.
    대상이 **하나면 종전 경로 그대로**다(회귀 0) — 요약 문구·반환 키가 비트 동일해야
    기존 소비처(output_generator·CSV)가 변하지 않는다.

    Args:
        task: 현재 TaskSpec (db_ids 승계/고정 가능)
        isolated: 격리 입력 (user_query=task["sub_query"], conversation_context 포함)
        llm: LLM 인스턴스 (미사용 — 시그니처 호환)
        app_config: 앱 설정

    Returns:
        {organized_data, query_results, source, (error)} 형태 dict
        (정렬·마스킹은 select_top_processes가 결정적으로 수행 — D-047-1 정합)
    """
    sub_query = task.get("sub_query", isolated.get("user_query", ""))
    db_id = _resolve_db_id(task, isolated, sub_query, app_config)
    resolution = resolve_investigation_targets(isolated, db_id=db_id)
    identifier = _resolve_hostname(isolated)
    base_url_present = bool(db_id and app_config.alarm.get_process_api_base_url(db_id))
    # 진입 진단(early-return으로 hostname 해소·API 로그가 안 남는 경우를 식별하기 위함):
    # 어느 게이트(db_id/identifier/base_url)에서 0건으로 빠지는지 한 줄로 드러낸다.
    logger.info(
        "process_query 진입: db_id=%s identifier=%s targets=%d base_url=%s sub_query=%r",
        db_id, identifier, len(resolution.targets),
        "있음" if base_url_present else "없음", (sub_query or "")[:120],
    )

    # ⑤ 존 순회 탐색 (D-176 후속3) — ①~④가 전부 실패했을 때만 진입한다.
    # 현행은 여기서 *"위치를 지정해 주세요"* 라는 막다른 안내로 끝났는데, 운영 `.env`는
    # 세 존 전부 프로세스 API가 매핑돼 있어 **순회하면 찾을 수 있었다**(plans/82 §1.3).
    discovery: Optional[dict] = None
    if not db_id and identifier:
        db_id, discovery = await _discover_db_id(identifier, isolated, app_config)
        if discovery and discovery.get("state") in ("ambiguous", "not_found"):
            return _empty_result(
                discovery["message"], None, identifier,
                reason=discovery["state"], discovery=discovery["trace"],
            )
        if db_id:
            # 탐색이 존을 확정했으므로 대상 해소를 다시 돌린다(선행 스코프 배관 재사용).
            resolution = resolve_investigation_targets(isolated, db_id=db_id)
            logger.info("탐색으로 대상 존 확정: db_id=%s identifier=%s", db_id, identifier)

    # 대상 미식별 → graceful 안내 (없는 테이블 조회로 폴백하지 않음 — SQL0204N 방지)
    if not db_id:
        logger.warning("process_query 0건: db_id 미식별 (위치 신호 부족)")
        msg = "프로세스 조회 대상 DB(위치)를 식별하지 못했습니다. 위치(예: 김포/여의도)를 지정해 주세요."
        return _empty_result(msg, db_id, identifier)
    if not identifier:
        logger.warning("process_query 0건: identifier(서버명) 미식별 db_id=%s", db_id)
        msg = "프로세스 조회 대상 서버(서버명)를 식별하지 못했습니다. 서버명을 지정해 주세요."
        return _empty_result(msg, db_id, identifier)

    base_url = app_config.alarm.get_process_api_base_url(db_id)
    if not base_url:
        logger.warning("process_query 0건: base_url 미매핑 db_id=%s (런타임 .env 확인 필요)", db_id)
        msg = (
            f"'{db_id}'는 실시간 프로세스 API가 연결되지 않은 DB입니다. "
            "프로세스 API가 매핑된 폴스타(예: 김포/여의도)에서만 실시간 프로세스 조회가 가능합니다."
        )
        return _empty_result(msg, db_id, identifier)

    alarm_kind = _infer_alarm_kind(sub_query)
    identifiers = [
        t.hostname or t.server_name or t.ip for t in resolution.targets
    ]
    identifiers = [i for i in identifiers if i]

    # N-대상 경로 (W2). 전체 타임아웃 가드는 fan-out **전체**에 씌운다 —
    # per-call 타임아웃만으로는 무력화된다(Known Mistakes).
    if len(identifiers) > 1:
        cfg = load_config().composite
        try:
            succeeded, failed = await asyncio.wait_for(
                _fanout(db_id, identifiers, alarm_kind, app_config),
                timeout=cfg.total_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "process_query fan-out 전체 타임아웃(%.1fs 초과): 대상 %d건",
                cfg.total_timeout_seconds, len(identifiers),
            )
            succeeded, failed = [], [
                {"target": i, "error": f"전체 타임아웃({cfg.total_timeout_seconds:.0f}s 초과)"}
                for i in identifiers
            ]
        return _reduce_fanout(
            db_id, succeeded, failed, resolution, alarm_kind, app_config
        )

    # 단일 대상 경로 — 종전과 동일(회귀 0).
    # 서버명 → 호스트명 해소 (D-046): 프로세스 API 조회 키는 hostname이므로,
    # 입력이 서버명이면 cmm_resource에서 hostname을 찾아 사용한다. 해소 실패 시 원시 값 사용.
    outcome = await _collect_one_target(db_id, identifier, alarm_kind, app_config)
    hostname = outcome["hostname"]
    server_label = outcome["server_label"]
    if not outcome["ok"]:
        if outcome.get("reason") == REASON_HOST_UNAVAILABLE:
            # 가용성 비정상은 **재시도 유도를 붙이지 않는다** — 재시도해도 결과가 같다(§1.1 ①).
            msg = (
                f"{outcome.get('notice', '')} "
                "실시간 프로세스 조회를 수행하지 않았습니다. "
                "서버 상태를 확인한 뒤 다시 요청해 주세요."
            ).strip()
        else:
            msg = (
                f"서버 '{server_label}'의 실시간 프로세스를 조회하지 못했습니다 "
                "(프로세스 API 미응답/타임아웃). 잠시 후 다시 시도해 주세요."
            )
        return _empty_result(
            msg, db_id, hostname,
            availability=outcome.get("availability"),
            reason=outcome.get("reason"),
        )

    full_rows = outcome["rows"]
    total = outcome["total"]
    top_n = max(0, app_config.alarm.process_top_n)
    rows = full_rows[:top_n]  # 채팅 표시용 상위 N

    metric_label = "메모리" if alarm_kind == "memory" else "CPU"
    if total > len(rows):
        summary = (
            f"서버 '{server_label}'의 현재 실행 중 프로세스 {total}건 중 {metric_label} 점유 상위 "
            f"{len(rows)}건을 표시합니다 (스냅샷 시각: {outcome['captured_at'] or '미상'}). "
            f"전체 {total}건은 'CSV 다운로드'로 받을 수 있습니다."
        )
    else:
        summary = (
            f"서버 '{server_label}'의 현재 실행 중 프로세스 {total}건 "
            f"(스냅샷 시각: {outcome['captured_at'] or '미상'})."
        )
    # 캐시 히트를 **드러낸다**(W2-8 · 침묵 금지) — 60초 전 스냅샷을 "현재"로 보여주면
    # 사용자는 실시간 값으로 오인한다. 캐시가 꺼져 있으면(기본) 이 문구는 붙지 않는다.
    cache_meta = outcome.get("cache") or {}
    if cache_meta.get("hit"):
        summary = f"{summary} {cache_meta.get('note', '')}".strip()
    # 판정 문구는 **맨 앞**에 둔다 — 사용자는 표보다 이 문장을 먼저 읽는다(점검·알 수 없음 등).
    notice = outcome.get("notice")
    if notice:
        summary = f"{notice} {summary}".strip()
    # 탐색이 존을 확정했으면 **어디서 찾았는지 밝힌다**(D-176 후속3). 사용자는 위치를
    # 말하지 않았으므로, 밝히지 않으면 **어느 존의 서버를 보고 있는지 모른 채** 결과를
    # 읽는다 — 동명 호스트가 있는 환경에서 이것은 조용한 오답과 같다.
    _zone_label = _discovered_zone_label(discovery)
    if _zone_label:
        summary = f"{summary} (대상 존: {_zone_label} — 존 순회 탐색으로 확정)"

    logger.info(
        "process_query: db_id=%s identifier=%s hostname=%s total=%d shown=%d kind=%s",
        db_id, identifier, hostname, total, len(rows), alarm_kind,
    )

    return {
        "organized_data": {
            "summary": summary,
            "rows": rows,  # 채팅 표시: 상위 N (output_generator가 이 rows로 표 생성)
            "column_mapping": None,
            "resolved_mapping": None,
            "is_sufficient": True,
            "sheet_mappings": None,
        },
        "query_results": full_rows,  # CSV 다운로드·row_count: 전체 프로세스
        "source": [{"db_id": db_id, "reason": "실시간 프로세스 API (Plan 47-1 재사용)"}],
        "target_db_ids": [db_id],
        "process_query": {
            "db_id": db_id,
            "server_name": identifier,
            "hostname": hostname,
            "total_count": total,
            "shown_count": len(rows),
            "captured_at": outcome["captured_at"],
            "metric": alarm_kind,
            **({"availability": outcome["availability"]} if outcome.get("availability") else {}),
            **({"discovery": discovery["trace"]} if discovery else {}),
        },
    }


def _empty_result(
    message: str,
    db_id: Optional[str],
    hostname: Optional[str],
    *,
    availability: Optional[dict] = None,
    reason: Optional[str] = None,
    discovery: Optional[dict] = None,
) -> dict:
    """조회 불가 시 graceful 빈 결과(안내 요약)를 반환한다.

    `availability`/`reason`/`discovery`는 **있을 때만** 실린다 — 판정·탐색이 꺼져 있으면
    반환 dict가 종전과 비트 동일해야 한다(Plan 81·D-176 후속3 회귀 안전).
    """
    meta: dict[str, Any] = {"db_id": db_id, "hostname": hostname, "total_count": 0}
    if availability:
        meta["availability"] = availability
    if reason:
        meta["reason"] = reason
    if discovery:
        # 어느 존을 돌았고 어디가 실패했는지 — 0건 진단(D-176 후속1)과 같은 원칙:
        # **끊긴 지점이 특정되어야 한다.**
        meta["discovery"] = discovery
    return {
        "organized_data": {
            "summary": message,
            "rows": [],
            "column_mapping": None,
            "resolved_mapping": None,
            "is_sufficient": False,
            "sheet_mappings": None,
        },
        "query_results": [],
        "source": [{"db_id": db_id} if db_id else {}],
        "target_db_ids": [db_id] if db_id else [],
        "process_query": meta,
    }
