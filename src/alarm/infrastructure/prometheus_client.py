"""Prometheus HTTP Query API 클라이언트 (node_exporter 메트릭 조회 — 읽기 전용).

node_exporter는 `/metrics`만 노출하는 스크레이프 타깃이므로, 에이전트는 node_exporter에
직접 붙지 않고 **앞단 Prometheus의 HTTP Query API**를 조회한다. 순간값(`/api/v1/query`)과
구간 시계열(`/api/v1/query_range`, Plan 60 E3 Holt-Winters baseline용)을 read-only GET으로 얻는다.

핵심 규칙 (Plan 60 §5.2, Plan 64 §4.5, docs/aiops_benchmark/l3_host_collection_mechanism.md §4A·§9):
    - **읽기 전용(D-003)** — Query API만 사용. remote-write 등 쓰기 경로 없음.
    - db_id(존)→base_url 매핑은 프로세스 API와 동일한 CSV 패턴(alarm_cfg.get_prometheus_base_url).
    - PromQL은 코드가 조립하는 고정 템플릿(사용자 입력 아님) — 그래도 quote로 인코딩(인젝션 방지).
    - 비200/파싱 실패/타임아웃/네트워크 오류 → None (호출부 graceful degradation).
    - 자격증명/인증은 config(서버측)에만 — 에이전트는 base_url만 보유(폴스타 프로세스 API 계승).
    - 기준 시각은 호출부가 사건 시각으로 산정해 start/end로 넘긴다(now() 금지 — Plan 50 §3.4).
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

_INSTANT_PATH = "/api/v1/query"
_RANGE_PATH = "/api/v1/query_range"


class PrometheusClient:
    """Prometheus HTTP Query API 클라이언트 (httpx, 읽기 전용 GET)."""

    def __init__(self, alarm_cfg) -> None:  # noqa: ANN001 — AlarmConfig
        """클라이언트를 초기화한다.

        Args:
            alarm_cfg: AlarmConfig — get_prometheus_base_url(db_id)/
                prometheus_timeout_seconds 를 사용한다.
        """
        self._alarm_cfg = alarm_cfg

    def get_base_url(self, db_id: str) -> Optional[str]:
        """db_id(존)에 매핑된 Prometheus base_url을 반환한다 (없으면 None)."""
        return self._alarm_cfg.get_prometheus_base_url(db_id)

    async def query(self, db_id: str, promql: str) -> Optional[list[dict[str, Any]]]:
        """순간값 조회 (`/api/v1/query`).

        Returns:
            data.result (instant vector: ``[{"metric": {...}, "value": [ts, "val"]}]``)
            리스트. base_url 미매핑·빈 PromQL·조회 실패 시 None.
        """
        base_url = self.get_base_url(db_id)
        if not base_url or not promql:
            return None
        url = f"{base_url.rstrip('/')}{_INSTANT_PATH}?query={quote(promql, safe='')}"
        return await self._get_result(url, db_id, promql)

    async def query_range(
        self, db_id: str, promql: str, start: float, end: float, step: str = "60s"
    ) -> Optional[list[dict[str, Any]]]:
        """구간 시계열 조회 (`/api/v1/query_range`, baseline용).

        Args:
            start, end: Unix 타임스탬프(초). 사건 기준 시각을 호출부가 산정(now() 금지).
            step: 해상도 스텝(예: "60s").

        Returns:
            data.result (range matrix: ``[{"metric": {...}, "values": [[ts, "val"], ...]}]``)
            리스트. 실패 시 None.
        """
        base_url = self.get_base_url(db_id)
        if not base_url or not promql:
            return None
        url = (
            f"{base_url.rstrip('/')}{_RANGE_PATH}?query={quote(promql, safe='')}"
            f"&start={start}&end={end}&step={quote(step, safe='')}"
        )
        return await self._get_result(url, db_id, promql)

    async def query_scalar(self, db_id: str, promql: str) -> Optional[float]:
        """순간 단일 스칼라(첫 result의 value)를 float으로 반환한다 (없으면 None).

        임계 비교·병목 판정 등 단일값 소비용. 여러 시계열이 반환되면 첫 항목만 사용한다.
        """
        result = await self.query(db_id, promql)
        if not result:
            return None
        try:
            return float(result[0]["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            logger.warning("prometheus 스칼라 파싱 실패: db_id=%s promql=%s", db_id, promql)
            return None

    async def query_series(
        self, db_id: str, promql: str, start: float, end: float, step: str = "60s"
    ) -> Optional[list[float]]:
        """구간 시계열의 첫 result 값열을 list[float]로 반환한다 (Holt-Winters 입력, Plan 60 E3).

        base_url 미매핑·조회 실패·빈 결과 시 None → 호출부는 baseline 없이 진행(상향 없음).
        """
        result = await self.query_range(db_id, promql, start, end, step)
        if not result:
            return None
        try:
            return [float(v) for _, v in result[0]["values"]]
        except (KeyError, IndexError, TypeError, ValueError):
            logger.warning("prometheus 시계열 파싱 실패: db_id=%s promql=%s", db_id, promql)
            return None

    async def _get_result(
        self, url: str, db_id: str, promql: str
    ) -> Optional[list[dict[str, Any]]]:
        """공통 GET + 파싱. 비200/오류/status!=success/형식불일치 → None."""
        timeout = self._alarm_cfg.prometheus_timeout_seconds
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning(
                    "prometheus 비200 — 조회 생략: db_id=%s status=%s",
                    db_id,
                    resp.status_code,
                )
                return None
            payload = resp.json()
        except Exception as exc:
            logger.warning(
                "prometheus 조회 실패 — baseline 없이 진행: db_id=%s promql=%s err=%s",
                db_id,
                promql,
                exc,
            )
            return None

        if not isinstance(payload, dict) or payload.get("status") != "success":
            logger.warning(
                "prometheus status!=success — 조회 생략: db_id=%s promql=%s", db_id, promql
            )
            return None
        data = payload.get("data")
        result = data.get("result") if isinstance(data, dict) else None
        return result if isinstance(result, list) else None
