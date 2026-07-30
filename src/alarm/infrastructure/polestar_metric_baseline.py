"""동적 baseline 이상탐지 데이터 어댑터 (Plan 60 E3 · D-079 — 읽기전용 인프라).

`cmm_metric_stat_h`(시간별 사용률) 고정 SQL로 서버의 CPU/메모리 시계열을 조회해 순수 Python
Holt-Winters(domain `anomaly.py`)로 적합하고, baseline 파라미터(HW 상태·잔차 σ)를 Redis
`alarm:baseline:{db_id}:{server}:{kind}`(TTL `anomaly_baseline_cache_ttl_seconds`)에 캐시한다.
캐시 히트 시 이벤트 시점 연산은 조회 + z-score뿐이다(적합 재수행 없음). 캐시 실패는 무시한다
(순수 최적화 — enrich 캐시 전례, §5.2).

알람→메트릭 게이팅(Known Mistakes 2026-06-29 E2 필수 준수, §5.2):
    `process_rank.classify_alarm_kind`를 재사용하되 **반드시 매핑(`self._metric_source_map`)
    화이트리스트로 게이팅**한다(`kind is not None` 금지 — E6가 disk/network/process/log도
    반환하므로 의미가 변질된다). 매핑 부재 kind(disk/network/log 등)·kind None·해소 이벤트는
    계산 skip→None(상향 없음). 매핑은 `DEFAULT_METRIC_SOURCE_BY_KIND`(폴스타 실측 기본) 또는
    설정 `anomaly_metric_source_map_csv`/생성자 주입값이다(Plan 67 R3-(v) 인접 — domain
    벤더 중립화).

안전 제약:
    - 읽기 전용 SELECT 단일문만. DML/DDL 금지. 외부 입력(server_name)은 `_sql_literal`로 이스케이프.
    - PostgreSQL 방언(`polestar.` 스키마 한정)은 기존 polestar_noise_context 패턴 재사용.
      b0(DB2)/비PostgreSQL·조회 실패·히스토리 부족 → None(계산 skip·상향 없음, graceful).
    - DBHub(MCP) 실행 경로는 polestar_noise_context.fetch와 동일 패턴을 재사용한다
      (`registry.get_client(db_id)` async context → execute_sql → rows). `noise_context_timeout_seconds`
      (3s)로 조회를 상한한다.
    - 폐쇄망 로컬. prometheus_client(폴백 채널·preparatory)는 §5.2 확정 설계상 배선하지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from src.alarm.domain.anomaly import (
    HWState,
    anomaly_score,
    holt_winters_fit,
    residual_sigma,
    severity_from_anomaly,
)
from src.alarm.domain.process_rank import classify_alarm_kind

# 스키마 한정·리터럴 이스케이프·행 조회는 노이즈 컨텍스트와 단일 출처 공유(동일 infra 계층).
from src.alarm.infrastructure.polestar_noise_context import (
    _row_value,
    _sql_literal,
    _table,
)

logger = logging.getLogger(__name__)

# 알람 kind → (resource_type, definition_name) 결정 매핑표 (§5.2 게이팅).
# **실측 확정(2026-07-22)**: cmm_metric_stat_h/d/m는 `resource_type`(server.Cpus/server.Memory)
# + `definition_name='Utilization'` + `avg_val` 피벗으로 사용률을 담는다
# (src/db_adapters/polestar/assembler.py·semantic_compiler.py 실측 — 계획 예시값과 일치).
# 1차 범위 = CPU·메모리만. disk/network/log 등 매핑 부재 kind는 화이트리스트에서 skip.
# (Plan 67 R3-(v) 인접 · 편향 검토 §2-9) 이 상수는 폴스타 스키마 지식이므로 domain(anomaly.py)이
# 아니라 이 어댑터에 둔다. 다른 벤더·스키마는 설정(anomaly_metric_source_map_csv) 또는 생성자
# 주입으로 대체한다 — domain은 매핑을 알지 않는다.
DEFAULT_METRIC_SOURCE_BY_KIND: dict[str, tuple[str, str]] = {
    "cpu": ("server.Cpus", "Utilization"),
    "memory": ("server.Memory", "Utilization"),
}

# 시간별(_h) 데이터의 계절 주기 = 일간 24h. 주간(168h) 계절은 정확도 요구 확인 시 2차 강화.
_SEASONAL_PERIOD_HOURS = 24
# 조회 히스토리 상한(주기 수) = min_periods + 버퍼. 계절 추정 안정성과 조회 비용의 절충.
_HISTORY_BUFFER_PERIODS = 4


def parse_metric_source_map(csv: str) -> Optional[dict[str, tuple[str, str]]]:
    """설정 CSV를 kind → (resource_type, definition_name) 매핑으로 파싱한다.

    형식: `"kind=resource_type:definition_name,…"`
    (예: `"cpu=server.Cpus:Utilization,memory=server.Memory:Utilization"`).

    빈 문자열·전부 형식 위반이면 None을 반환해 호출부가 어댑터 기본 매핑을 쓰게 한다
    (설정 오타로 이상탐지가 조용히 전면 비활성되는 것을 막는다). 형식 위반 항목만 있는
    경우는 경고를 남긴다.

    Args:
        csv: `anomaly_metric_source_map_csv` 설정값.

    Returns:
        파싱된 매핑 또는 None(미설정·전부 무효 → 기본 매핑 사용).
    """
    if not csv or not csv.strip():
        return None
    mapping: dict[str, tuple[str, str]] = {}
    for item in csv.split(","):
        entry = item.strip()
        if not entry:
            continue
        kind, sep, source = entry.partition("=")
        resource_type, colon, definition_name = source.partition(":")
        if not (sep and colon and kind.strip() and resource_type.strip()):
            logger.warning(
                "anomaly_metric_source_map_csv 항목 형식 위반 — 무시: %r", entry
            )
            continue
        mapping[kind.strip().lower()] = (
            resource_type.strip(),
            definition_name.strip(),
        )
    if not mapping:
        logger.warning(
            "anomaly_metric_source_map_csv 유효 항목 없음 — 기본 매핑으로 진행: %r", csv
        )
        return None
    return mapping


def build_metric_series_sql(
    db_id: str,
    server_name: str,
    resource_type: str,
    definition_name: str,
    limit: int,
) -> str:
    """서버의 시간별 사용률 시계열(avg_val) 조회 SQL을 조립한다(읽기 전용 SELECT 단일문).

    조인 체인(실측 — assembler.py `build_multi_resource_pivot_sql`):
        server.Server(svr, NAME=server_name) ─ platform_resource_id ─ 자식 리소스(child,
        server.Cpus/server.Memory) ─ cmm_metric_stat_h(s, s.resource_id = child.id,
        definition_name='Utilization'). `stat_date`는 YYYYMMDDHH 시간 키(가장 최근 우선).

    최신 limit개를 DESC로 가져온다(호출부가 오름차순으로 뒤집어 적합). server_name만 외부
    입력이라 `_sql_literal`로 이스케이프하고, resource_type/definition_name은 고정 매핑값이다.
    RESOURCE_CONF_ID JOIN 미포함(D-022).
    """
    t_resource = _table(db_id, "cmm_resource")
    t_metric = _table(db_id, "cmm_metric_stat_h")
    return (
        "SELECT\n"
        "    s.stat_date AS stat_date,\n"
        "    s.avg_val AS avg_val\n"
        f"FROM {t_resource} svr\n"
        f"JOIN {t_resource} child ON child.platform_resource_id = svr.id\n"
        f"JOIN {t_metric} s ON s.resource_id = child.id\n"
        "WHERE svr.resource_type = 'server.Server'\n"
        f"  AND svr.name = {_sql_literal(server_name)}\n"
        "  AND svr.dtime IS NULL\n"
        f"  AND child.resource_type = {_sql_literal(resource_type)}\n"
        "  AND child.dtime IS NULL\n"
        f"  AND s.definition_name = {_sql_literal(definition_name)}\n"
        "ORDER BY s.stat_date DESC\n"
        f"LIMIT {int(limit)}"
    )


class PolestarMetricBaselineAdapter:
    """폴스타 DB에서 사용률 시계열을 조회·적합해 baseline 이상탐지 심각도를 산출한다.

    repo가 인스턴스 하나를 보유·재사용한다(단일 워커). 캐시는 Redis(주입)이며, 캐시 부재·
    실패는 무시하고 매번 적합한다(순수 최적화).
    """

    def __init__(
        self,
        registry,  # noqa: ANN001
        alarm_cfg,  # noqa: ANN001
        *,
        metric_source_map: Optional[dict[str, tuple[str, str]]] = None,
    ) -> None:
        """어댑터를 초기화한다.

        Args:
            registry: DBRegistry (get_client/is_registered 사용).
            alarm_cfg: AlarmConfig (인터페이스 일관성용 — 본 어댑터 SQL은 미사용).
            metric_source_map: 알람 kind → (resource_type, definition_name) 매핑 오버라이드
                (Plan 67 R3-(v) 인접). 미지정이면 `DEFAULT_METRIC_SOURCE_BY_KIND`를 쓴다.
                이 매핑이 화이트리스트 역할을 겸한다 — 부재 kind는 계산 skip(§5.2).
        """
        self._registry = registry
        self._alarm_cfg = alarm_cfg
        self._metric_source_map = dict(
            metric_source_map or DEFAULT_METRIC_SOURCE_BY_KIND
        )

    def is_db_registered(self, db_id: str) -> bool:
        """event.db_id가 DB 레지스트리에 등록되어 있는지 확인한다."""
        return bool(self._registry.is_registered(db_id))

    async def compute_severity(
        self,
        event,  # noqa: ANN001 — AlarmEvent
        noise_cfg,  # noqa: ANN001 — NoiseGateConfig
        redis_client=None,  # noqa: ANN001 — redis.asyncio.Redis | None
    ) -> Optional[int]:
        """알람 시점 사용률의 baseline 이탈로 **상향 후보 심각도**를 산출한다(없으면 None).

        게이팅(어느 하나라도 미충족 시 None): 해소 이벤트 / kind ∉ METRIC_SOURCE_BY_KIND
        (화이트리스트 — Known Mistakes #2) / 미등록 db_id / 시계열 조회 실패·부족.

        절차: 시계열 조회(DESC→오름차순) → 최신 관측값을 홀드아웃, 그 이전을 baseline으로
        Holt-Winters 적합(캐시 히트 시 재적합 skip) → 최신 관측의 잔차 z-score → 상향 후보.
        전 과정 예외는 잡아 None(graceful — 상향 없음).
        """
        if getattr(event, "is_clear", False):
            return None
        kind = classify_alarm_kind(event)
        # ★ 반드시 화이트리스트 게이팅 — kind is not None 금지(E6 disk/network/log 변질 방지).
        if kind not in self._metric_source_map:
            return None
        if not self.is_db_registered(event.db_id):
            return None

        resource_type, definition_name = self._metric_source_map[kind]
        z_high = float(getattr(noise_cfg, "anomaly_z_high", 3.0))
        min_periods = int(getattr(noise_cfg, "anomaly_min_periods", 3))
        cache_ttl = int(getattr(noise_cfg, "anomaly_baseline_cache_ttl_seconds", 3600))
        timeout = float(getattr(noise_cfg, "noise_context_timeout_seconds", 3.0))
        period = _SEASONAL_PERIOD_HOURS
        limit = period * (min_periods + _HISTORY_BUFFER_PERIODS)

        try:
            series = await self._fetch_series(
                event, resource_type, definition_name, limit, timeout
            )
        except Exception:
            logger.exception(
                "baseline 시계열 조회 실패 — 이상탐지 skip: alarm_id=%s",
                getattr(event, "alarm_id", "?"),
            )
            return None

        # baseline = 최신 관측 제외(홀드아웃), 그 이전이 min_periods*period 이상이어야 적합.
        if series is None or len(series) < min_periods * period + 1:
            return None
        observed = series[-1]
        baseline = series[:-1]

        # STL 2차 강화(Plan 60 E3 · D-113, opt-in): robust STL로 계절 피크 오탐↓. 전체 series
        # (관측 포함)를 분해해 잔차 z-score만 얻고, 심각도 매핑은 domain severity_from_anomaly로
        # (상향 전용 계약 불변·캐시 미사용). statsmodels 미설치·fit 실패·데이터부족·σ0이면 None →
        # 아래 순수 Python HW 경로(캐시·holdout)로 graceful 폴백(침묵 강등 아님 — helper가 로그).
        if getattr(noise_cfg, "anomaly_stl_enabled", False):
            from src.alarm.infrastructure.metric_stl import stl_anomaly_score

            stl_score = stl_anomaly_score(series, period, min_periods=min_periods)
            if stl_score is not None:
                return severity_from_anomaly(stl_score, z_high)
            logger.debug(
                "STL 이상탐지 폴백 — HW로 진행: alarm_id=%s",
                getattr(event, "alarm_id", "?"),
            )

        cache_key = f"alarm:baseline:{event.db_id}:{event.server_name}:{kind}"
        cached = await self._load_baseline(redis_client, cache_key, period)
        if cached is not None:
            state, sigma = cached
        else:
            state = holt_winters_fit(baseline, period, min_periods=min_periods)
            if state is None:
                return None
            sigma = residual_sigma(baseline, state)
            await self._store_baseline(redis_client, cache_key, state, sigma, cache_ttl)

        score = anomaly_score(state, sigma, observed)
        return severity_from_anomaly(score, z_high)

    async def _fetch_series(
        self,
        event,  # noqa: ANN001 — AlarmEvent
        resource_type: str,
        definition_name: str,
        limit: int,
        timeout: float,
    ) -> Optional[list[float]]:
        """시간별 사용률 시계열을 조회해 **오름차순(오래된→최신) float 리스트**로 반환한다.

        조회는 stat_date DESC이므로 파싱 후 뒤집어 오름차순으로 만든다(series[-1]=최신). 파싱
        불가 값은 건너뛴다. 조회 실패·타임아웃은 상위에서 None 처리(graceful).
        """
        sql = build_metric_series_sql(
            event.db_id, event.server_name, resource_type, definition_name, limit
        )
        async with self._registry.get_client(event.db_id) as client:
            result = await asyncio.wait_for(client.execute_sql(sql), timeout=timeout)
        rows = list(getattr(result, "rows", []) or [])
        values: list[float] = []
        for row in rows:
            raw = _row_value(row, "avg_val")
            if raw is None or raw == "":
                continue
            try:
                values.append(float(raw))
            except (TypeError, ValueError):
                continue
        values.reverse()  # DESC(최신 우선) → 오름차순(오래된→최신)
        return values

    async def _load_baseline(
        self,
        redis_client,  # noqa: ANN001
        cache_key: str,
        period: int,
    ) -> Optional[tuple[HWState, float]]:
        """Redis에서 캐시된 baseline 파라미터(HW 상태 + σ)를 복원한다(부재·실패 시 None).

        캐시는 σ만 별도 저장하므로 잔차는 복원하지 않는다(anomaly_score는 잔차 불요). period
        불일치(설정 변경) 캐시는 무시하고 재적합한다. 캐시 실패는 무시(순수 최적화).
        """
        if redis_client is None:
            return None
        try:
            raw = await redis_client.get(cache_key)
        except Exception as e:  # noqa: BLE001 — 캐시 실패는 무시하고 재적합
            logger.debug("baseline 캐시 조회 실패 — 무시하고 재적합: %s", e)
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
            if int(data["period"]) != period:
                return None
            state = HWState(
                level=float(data["level"]),
                trend=float(data["trend"]),
                seasonals=[float(v) for v in data["seasonals"]],
                next_idx=int(data["next_idx"]),
                period=int(data["period"]),
            )
            return state, float(data["sigma"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as e:
            logger.debug("baseline 캐시 파싱 실패 — 무시하고 재적합: %s", e)
            return None

    async def _store_baseline(
        self,
        redis_client,  # noqa: ANN001
        cache_key: str,
        state: HWState,
        sigma: float,
        cache_ttl: int,
    ) -> None:
        """baseline 파라미터(HW 상태 + σ)를 Redis에 적재한다(실패는 무시 — 순수 최적화).

        잔차(residuals)는 직렬화하지 않는다(anomaly_score는 forecast + σ만 필요). ttl<=0이면
        적재하지 않는다(캐시 비활성).
        """
        if redis_client is None or cache_ttl <= 0:
            return
        payload = json.dumps(
            {
                "level": state.level,
                "trend": state.trend,
                "seasonals": state.seasonals,
                "next_idx": state.next_idx,
                "period": state.period,
                "sigma": sigma,
            },
            ensure_ascii=False,
        )
        try:
            await redis_client.setex(cache_key, cache_ttl, payload)
        except Exception as e:  # noqa: BLE001 — 적재 실패는 무시
            logger.debug("baseline 캐시 적재 실패 — 무시: %s", e)
