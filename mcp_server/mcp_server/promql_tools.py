"""Prometheus PromQL 조사용 MCP 도구 (Plan 66 Wave 2-B′ · sre-agent/06 §3 · D-119).

Prometheus 접근을 본 서버로 일원화한다(holmesgpt 내장 ``prometheus/metrics`` toolset
직결 미채택 — D-119). 도구는 두 층으로 구성된다.

- **고수준(기본 노출)**: hostname 앵커. 서버가 ``hostname(=폴스타 server_name)`` 인자에서
  ``{nodename="<hostname>"}`` 라벨 셀렉터를 **결정적으로 조립**한다(Plan 06 §5-0 · D-119 —
  LLM이 라벨을 직접 다루지 않는다, D-035 3차 방어). ``prom_metric_instant`` /
  ``prom_metric_range``. metric은 bare 메트릭 이름만 허용해 임의 PromQL을 막는다.
- **원시(옵트인 ``expose_raw_promql=true``)**: instant/range/labels/metadata/series
  패스스루 — ``execute_sql`` 기본 숨김 전례. Prometheus HTTP API는 태생 읽기 전용이라
  SQL형 검증 계층은 불요하나, **쿼리 timeout을 서버가 강제**한다(``make_client``).

반환·오류 계약(§3):
    - 정상: JSON 문자열 ``{"data": <prometheus data>, "queried_at": ISO,
      "source_kind": "prometheus", ...}``.
    - 오류: JSON 문자열 ``{"error": ...}`` (예외를 전파하지 않음 — RemoteMCPTool이
      ERROR로 변환).

감사(§6-5): 도구 호출·쿼리·소요시간·행수·오류를 폴스타 도구와 동일 파이프로 로깅한다.

읽기 전용(D-003): Prometheus 읽기 API(instant/range/labels/metadata/series)만 노출한다.
쓰기 API(admin/tsdb 등)는 노출하지 않는다.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import Context, FastMCP

from mcp_server.config import AppServerConfig, PrometheusConfig

logger = logging.getLogger(__name__)

# Prometheus HTTP API 읽기 전용 엔드포인트 (쓰기 API 미노출 — D-003).
_EP_QUERY = "/api/v1/query"
_EP_QUERY_RANGE = "/api/v1/query_range"
_EP_LABELS = "/api/v1/labels"
_EP_METADATA = "/api/v1/metadata"
_EP_SERIES = "/api/v1/series"

_SOURCE_KIND = "prometheus"

# 고수준 도구의 metric은 bare 메트릭 이름만 허용한다(서버가 라벨을 조립하므로 임의
# PromQL 표현식·라벨 셀렉터를 차단 — Plan 06 §5-0 결정적 조립).
_METRIC_NAME_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")

# duration 파싱 (Prometheus duration 형식 부분집합 — window/step 고수준 인자).
_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$")
_DURATION_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


# =====================================================================
# PromQL 조립 (순수 함수 — 단위 테스트 대상, 서버측 결정적 조립 §5-0)
# =====================================================================


def _label_value_escape(value: str) -> str:
    """PromQL 라벨 값 리터럴에 안전하도록 백슬래시·따옴표를 이스케이프하고 개행을 제거한다."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "")
        .replace("\r", "")
    )


def build_nodename_matcher(hostname: str) -> str:
    """hostname(=폴스타 server_name)에서 ``{nodename="<hostname>"}`` 셀렉터를 조립한다.

    서버측 결정적 조립(Plan 06 §5-0 · D-119) — LLM이 라벨을 직접 다루지 않는다.
    """
    return f'{{nodename="{_label_value_escape(hostname)}"}}'


def build_high_level_selector(metric: str, hostname: str) -> str:
    """고수준 도구의 PromQL 셀렉터 ``metric{nodename="hostname"}``를 조립한다.

    Raises:
        ValueError: metric이 bare 메트릭 이름이 아닐 때(임의 PromQL·라벨 셀렉터 차단).
    """
    m = str(metric).strip()
    if not _METRIC_NAME_RE.match(m):
        raise ValueError(
            f"고수준 도구의 metric은 bare 메트릭 이름만 허용: {metric!r} "
            "(임의 PromQL은 원시 도구를 사용)"
        )
    return f"{m}{build_nodename_matcher(hostname)}"


def parse_duration_seconds(text: str) -> int:
    """duration 문자열('30s'·'15m'·'1h'·'7d'·'2w')을 초로 변환한다.

    Raises:
        ValueError: 지원하지 않는 형식이거나 0 이하일 때.
    """
    m = _DURATION_RE.match(str(text))
    if not m:
        raise ValueError(
            f"지원하지 않는 duration 형식: {text!r} (예: 30s, 15m, 1h, 7d, 2w)"
        )
    n = int(m.group(1))
    if n <= 0:
        raise ValueError(f"duration은 양수여야 함: {text!r}")
    return n * _DURATION_UNIT_SECONDS[m.group(2)]


# =====================================================================
# 반환 · 감사 헬퍼
# =====================================================================


def _now_iso() -> str:
    """queried_at용 UTC ISO 타임스탬프를 반환한다."""
    return datetime.now(timezone.utc).isoformat()


def _result_count(data: Any) -> int:
    """Prometheus data에서 행/시리즈 수를 최선 추정한다(감사·result_count용)."""
    if isinstance(data, dict) and isinstance(data.get("result"), list):
        return len(data["result"])
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        return len(data)
    return 0


def _ok(data: Any, query: str, endpoint: str, **extra: Any) -> str:
    """정상 반환 JSON 문자열을 만든다({data, queried_at, source_kind, ...})."""
    payload: dict[str, Any] = {
        "data": data,
        "queried_at": _now_iso(),
        "source_kind": _SOURCE_KIND,
        "query": query,
        "endpoint": endpoint,
        "result_count": _result_count(data),
    }
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False, default=str)


def _err(message: str) -> str:
    """오류 반환 JSON 문자열을 만든다({error})."""
    return json.dumps({"error": message}, ensure_ascii=False)


def _audit(
    tool: str,
    query: str,
    elapsed_ms: float,
    count: Optional[int],
    error: Optional[str] = None,
) -> None:
    """도구 호출 감사 로그를 남긴다(도구명·쿼리·소요·행수 — 폴스타 도구와 동일 파이프)."""
    if error is not None:
        logger.warning(
            "promql audit: tool=%s query=%s elapsed_ms=%.1f error=%s",
            tool, query, elapsed_ms, error,
        )
    else:
        logger.info(
            "promql audit: tool=%s query=%s elapsed_ms=%.1f rows=%s",
            tool, query, elapsed_ms, count,
        )


# =====================================================================
# HTTP 계층 (timeout 서버 강제 — 단위 테스트는 httpx MockTransport로 고정)
# =====================================================================


def _auth_headers(cfg: PrometheusConfig) -> dict[str, str]:
    """설정된 인증 헤더를 Authorization 헤더로 변환한다(없으면 빈 dict)."""
    if cfg.auth_header:
        return {"Authorization": cfg.auth_header}
    return {}


def make_client(cfg: PrometheusConfig) -> httpx.AsyncClient:
    """설정 URL·인증 헤더·**강제 timeout**으로 httpx 클라이언트를 만든다.

    쿼리 timeout은 소비자가 우회할 수 없도록 서버가 클라이언트 생성 시 강제한다(§3 —
    원시 패스스루 포함 모든 PromQL HTTP 호출에 동일 적용).
    """
    return httpx.AsyncClient(
        base_url=cfg.url.rstrip("/"),
        headers=_auth_headers(cfg),
        timeout=float(cfg.query_timeout),
    )


async def _prom_get(
    cfg: PrometheusConfig,
    endpoint: str,
    params: dict[str, Any],
    tool: str,
    query_desc: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    **extra: Any,
) -> str:
    """Prometheus 읽기 엔드포인트를 GET 하고 반환·오류·감사 계약을 적용한다.

    ``client``가 주어지면 그대로 사용한다(테스트 주입용). 없으면 ``make_client``로
    timeout이 강제된 클라이언트를 생성한다. 예외는 전파하지 않고 ``{"error": ...}``로
    변환한다(침묵 폴백 금지 — 사유를 오류/감사로 노출).
    """
    if not cfg.url:
        return _err("PROMETHEUS_URL 미설정 — PromQL 조회 불가")

    owns_client = client is None
    if owns_client:
        client = make_client(cfg)

    start = time.time()
    try:
        resp = await client.get(endpoint, params=params)
        elapsed_ms = (time.time() - start) * 1000
        if resp.status_code != 200:
            _audit(tool, query_desc, elapsed_ms, None, error=f"HTTP {resp.status_code}")
            return _err(f"Prometheus 비200 응답: status={resp.status_code}")
        payload = resp.json()
        if not isinstance(payload, dict) or payload.get("status") != "success":
            reason = (
                payload.get("error") if isinstance(payload, dict) else "형식 오류"
            )
            _audit(tool, query_desc, elapsed_ms, None, error=str(reason))
            return _err(f"Prometheus 오류 응답: {reason}")
        data = payload.get("data")
        count = _result_count(data)
        _audit(tool, query_desc, elapsed_ms, count)
        return _ok(data, query_desc, endpoint, **extra)
    except Exception as e:
        elapsed_ms = (time.time() - start) * 1000
        _audit(tool, query_desc, elapsed_ms, None, error=str(e))
        logger.warning("%s 실패: %s", tool, e)
        return _err(str(e))
    finally:
        if owns_client:
            await client.aclose()


# =====================================================================
# 도구 코어 (async — 단위 테스트는 mock client 주입으로 고정)
# =====================================================================


async def run_metric_instant(
    cfg: PrometheusConfig,
    hostname: str,
    metric: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> str:
    """고수준 instant 조회: ``metric{nodename="hostname"}`` 순간값(§3·§5-0)."""
    if not hostname or not str(hostname).strip():
        return _err("hostname이 비어 있음")
    try:
        selector = build_high_level_selector(metric, hostname)
    except ValueError as e:
        return _err(str(e))
    return await _prom_get(
        cfg, _EP_QUERY, {"query": selector}, "prom_metric_instant", selector,
        client=client,
    )


async def run_metric_range(
    cfg: PrometheusConfig,
    hostname: str,
    metric: str,
    window: str,
    step: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> str:
    """고수준 range 조회: ``metric{nodename="hostname"}``의 최근 window 시계열(§3·§5-0)."""
    if not hostname or not str(hostname).strip():
        return _err("hostname이 비어 있음")
    try:
        selector = build_high_level_selector(metric, hostname)
        window_s = parse_duration_seconds(window)
        step_s = parse_duration_seconds(step)
    except ValueError as e:
        return _err(str(e))
    end = time.time()
    start = end - window_s
    params = {
        "query": selector,
        "start": f"{start:.3f}",
        "end": f"{end:.3f}",
        "step": str(step_s),
    }
    return await _prom_get(
        cfg, _EP_QUERY_RANGE, params, "prom_metric_range", selector,
        client=client, window=window, step=step,
    )


async def run_raw_query(
    cfg: PrometheusConfig,
    query: str,
    time_param: Optional[str] = None,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> str:
    """원시 instant 패스스루(임의 PromQL — 옵트인)."""
    params: dict[str, Any] = {"query": query}
    if time_param:
        params["time"] = str(time_param)
    return await _prom_get(
        cfg, _EP_QUERY, params, "prom_query", str(query), client=client
    )


async def run_raw_query_range(
    cfg: PrometheusConfig,
    query: str,
    start: str,
    end: str,
    step: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> str:
    """원시 range 패스스루(임의 PromQL — 옵트인). start/end/step은 Prometheus가 파싱한다."""
    params = {
        "query": query,
        "start": str(start),
        "end": str(end),
        "step": str(step),
    }
    return await _prom_get(
        cfg, _EP_QUERY_RANGE, params, "prom_query_range", str(query), client=client
    )


async def run_raw_labels(
    cfg: PrometheusConfig,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> str:
    """원시 라벨 이름 목록 조회(옵트인)."""
    return await _prom_get(
        cfg, _EP_LABELS, {}, "prom_labels", "labels", client=client
    )


async def run_raw_metadata(
    cfg: PrometheusConfig,
    metric: Optional[str] = None,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> str:
    """원시 메트릭 메타데이터 조회(옵트인). metric 지정 시 해당 메트릭만."""
    params: dict[str, Any] = {}
    if metric:
        params["metric"] = str(metric)
    return await _prom_get(
        cfg, _EP_METADATA, params, "prom_metadata", str(metric or "metadata"),
        client=client,
    )


async def run_raw_series(
    cfg: PrometheusConfig,
    match: Any,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> str:
    """원시 시리즈 조회(옵트인). match는 셀렉터 문자열 또는 문자열 리스트."""
    if not match:
        return _err("match 셀렉터가 비어 있음")
    matchers = match if isinstance(match, list) else [match]
    matchers = [str(m) for m in matchers]
    return await _prom_get(
        cfg, _EP_SERIES, {"match[]": matchers}, "prom_series",
        ", ".join(matchers), client=client,
    )


# =====================================================================
# 컨텍스트 접근 헬퍼
# =====================================================================


def _prom_config(ctx: Context) -> PrometheusConfig:
    """컨텍스트에서 Prometheus 설정을 가져온다."""
    config: AppServerConfig = ctx.request_context.lifespan_context["config"]
    return config.prometheus


# =====================================================================
# 도구 등록
# =====================================================================


def register_promql_tools(mcp: FastMCP, expose_raw_promql: bool = False) -> None:
    """PromQL 조사 도구를 MCP 서버에 등록한다.

    고수준(hostname 앵커) 2종은 항상 등록한다. 원시 패스스루 5종
    (instant/range/labels/metadata/series)은 ``expose_raw_promql=True``일 때만
    등록한다(execute_sql 기본 숨김 전례 — 계획 §3).

    Args:
        mcp: FastMCP 서버 인스턴스.
        expose_raw_promql: 원시 PromQL 패스스루 도구 노출 여부(기본 비노출).
    """

    @mcp.tool()
    async def prom_metric_instant(
        hostname: str,
        metric: str,
        ctx: Context | None = None,
    ) -> str:
        """서버(hostname)의 메트릭 순간값을 조회한다(Prometheus instant).

        서버가 ``{nodename="<hostname>"}`` 필터를 결정적으로 조립한다(§5-0). metric은
        bare 메트릭 이름만 허용한다(임의 PromQL은 원시 도구 사용).

        Args:
            hostname: 폴스타 등록 서버명(=Prometheus nodename 라벨).
            metric: bare 메트릭 이름(예: node_load1).
            ctx: MCP 컨텍스트.

        Returns:
            JSON 문자열 {data, queried_at, source_kind} 또는 {error}.
        """
        return await run_metric_instant(_prom_config(ctx), hostname, metric)

    @mcp.tool()
    async def prom_metric_range(
        hostname: str,
        metric: str,
        window: str = "1h",
        step: str = "60s",
        ctx: Context | None = None,
    ) -> str:
        """서버(hostname)의 메트릭 시계열(최근 window)을 조회한다(Prometheus range).

        서버가 ``{nodename="<hostname>"}`` 필터를 결정적으로 조립한다(§5-0). metric은
        bare 메트릭 이름만 허용한다.

        Args:
            hostname: 폴스타 등록 서버명(=Prometheus nodename 라벨).
            metric: bare 메트릭 이름(예: node_cpu_seconds_total).
            window: 조회 창(예: 1h, 30m, 15s). 기본 1h.
            step: 해상도 스텝(예: 60s, 15s). 기본 60s.
            ctx: MCP 컨텍스트.

        Returns:
            JSON 문자열 {data, queried_at, source_kind} 또는 {error}.
        """
        return await run_metric_range(
            _prom_config(ctx), hostname, metric, window, step
        )

    if not expose_raw_promql:
        logger.info("원시 PromQL 도구 비노출 (기본) — 고수준 hostname 앵커 도구만")
        return

    @mcp.tool()
    async def prom_query(
        query: str,
        time: str | None = None,
        ctx: Context | None = None,
    ) -> str:
        """임의 PromQL instant 쿼리(원시 패스스루 — 옵트인). timeout은 서버가 강제한다.

        Args:
            query: PromQL 표현식.
            time: 평가 시각(unix 초 또는 RFC3339). 생략 시 현재.
            ctx: MCP 컨텍스트.

        Returns:
            JSON 문자열 {data, queried_at, source_kind} 또는 {error}.
        """
        return await run_raw_query(_prom_config(ctx), query, time)

    @mcp.tool()
    async def prom_query_range(
        query: str,
        start: str,
        end: str,
        step: str,
        ctx: Context | None = None,
    ) -> str:
        """임의 PromQL range 쿼리(원시 패스스루 — 옵트인). timeout은 서버가 강제한다.

        Args:
            query: PromQL 표현식.
            start: 시작 시각(unix 초 또는 RFC3339).
            end: 종료 시각(unix 초 또는 RFC3339).
            step: 해상도 스텝(예: 15s 또는 초).
            ctx: MCP 컨텍스트.

        Returns:
            JSON 문자열 {data, queried_at, source_kind} 또는 {error}.
        """
        return await run_raw_query_range(
            _prom_config(ctx), query, start, end, step
        )

    @mcp.tool()
    async def prom_labels(ctx: Context | None = None) -> str:
        """라벨 이름 목록을 조회한다(원시 패스스루 — 옵트인).

        Returns:
            JSON 문자열 {data, queried_at, source_kind} 또는 {error}.
        """
        return await run_raw_labels(_prom_config(ctx))

    @mcp.tool()
    async def prom_metadata(
        metric: str | None = None,
        ctx: Context | None = None,
    ) -> str:
        """메트릭 메타데이터를 조회한다(원시 패스스루 — 옵트인).

        Args:
            metric: 특정 메트릭명(생략 시 전체).
            ctx: MCP 컨텍스트.

        Returns:
            JSON 문자열 {data, queried_at, source_kind} 또는 {error}.
        """
        return await run_raw_metadata(_prom_config(ctx), metric)

    @mcp.tool()
    async def prom_series(
        match: str,
        ctx: Context | None = None,
    ) -> str:
        """시리즈 셀렉터에 매칭되는 시리즈를 조회한다(원시 패스스루 — 옵트인).

        Args:
            match: 시리즈 셀렉터(예: node_cpu_seconds_total{nodename="web-01"}).
            ctx: MCP 컨텍스트.

        Returns:
            JSON 문자열 {data, queried_at, source_kind} 또는 {error}.
        """
        return await run_raw_series(_prom_config(ctx), match)

    logger.info(
        "원시 PromQL 도구 노출됨 (expose_raw_promql=True) — "
        "instant/range/labels/metadata/series"
    )
