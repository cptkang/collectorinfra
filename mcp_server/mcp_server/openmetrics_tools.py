"""OpenMetrics 스크레이프 MCP 도구 — exporter 노출 형식을 직접 읽는다 (plans/92 §4.3 · D-210).

Prometheus 서버 없이 exporter의 ``/metrics``(OpenMetrics 1.0 · text 0.0.4)를 GET 해서, PromQL
고수준 도구와 같은 반환 계약(``_ok``/``_err`` · instant vector)으로 돌려준다. 결과는
``source_kind="openmetrics"``로 구별한다. 파싱·정규화는 ``openmetrics`` 모듈(순수 함수)이 맡는다.
벤더 중립 모듈이다(overfit 게이트 스캔 대상).

안전 통제(plans/92 §7.1·§7.2):
- I-1: LLM은 URL을 넘기지 못한다. ``hostname``으로 서버 허용목록(``[[openmetrics.targets]]``)만
  찾는다. 타깃 0건·목록 밖·인자 오류는 HTTP 0회로 오류를 돌려준다.
- I-2: GET만 보낸다. 리다이렉트는 따라가지 않고 오류로 돌려준다(허용목록 우회 차단).
  timeout은 서버가 강제한다. 응답 크기는 Content-Length 선검사와 스트림 누적 검사(정본)로 막는다.
- I-3: 침묵 폴백 금지. 파싱 실패·절단은 오류·``truncated``로 드러내고, 예외는 전파하지 않는다.
- R-12: 현재값 전용이다. 반환에 ``observed_at``(스크레이프 시각)을 항상 싣는다. 이력·rate는 없다.

``kind="federate"`` 타깃은 Prometheus ``/federate?match[]={nodename="…"}``를 같은 파서로 읽는다
(§4.3 (c) — Prometheus가 생기면 설정만으로 전환).
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from mcp.server.fastmcp import Context, FastMCP

from mcp_server import openmetrics as om
from mcp_server.config import AppServerConfig, OpenMetricsConfig, OpenMetricsTarget
from mcp_server.promql_tools import _err, _label_value_escape, _ok, build_nodename_matcher

logger = logging.getLogger(__name__)

_SOURCE_KIND = "openmetrics"
_EP_METRICS = "/metrics"
_EP_FEDERATE = "/federate"
_KIND_FEDERATE = "federate"

_TARGETS_UNSET = "OpenMetrics 스크레이프 타깃 미설정"


class _ScrapeError(Exception):
    """스크레이프 실패(리다이렉트·비200·크기 상한·디코딩). 도구가 ``{"error"}``로 바꾼다."""


# =====================================================================
# 타깃 해석 · 요청 조립 (순수 함수)
# =====================================================================


def resolve_target(cfg: OpenMetricsConfig, hostname: str) -> OpenMetricsTarget | None:
    """허용목록에서 ``hostname``(앞뒤 공백 제거)과 정확히 같은 타깃을 찾는다. 없으면 None."""
    wanted = str(hostname).strip()
    for target in cfg.targets:
        if target.hostname == wanted:
            return target
    return None


def build_scrape_request(
    target: OpenMetricsTarget,
) -> tuple[str, dict[str, str] | None, str]:
    """타깃에서 ``(URL, 쿼리 파라미터, endpoint 표기)``를 만든다.

    ``exporter``는 등록 URL을 그대로 쓴다. ``federate``는 등록 URL을 Prometheus base로 보고
    ``/federate``에 ``match[]={nodename="<hostname>"}``를 서버가 조립해 붙인다(값 이스케이프 포함).
    """
    if target.kind == _KIND_FEDERATE:
        return (
            target.url.rstrip("/") + _EP_FEDERATE,
            {"match[]": build_nodename_matcher(target.hostname)},
            _EP_FEDERATE,
        )
    return target.url, None, _EP_METRICS


def make_scrape_client(cfg: OpenMetricsConfig) -> httpx.AsyncClient:
    """스크레이프 클라이언트를 만든다(timeout 서버 강제 · 리다이렉트 미추종 · Prometheus Accept)."""
    return httpx.AsyncClient(
        timeout=float(cfg.scrape_timeout),
        follow_redirects=False,
        headers={"Accept": om.PROM_SCRAPE_ACCEPT},
    )


def _describe_query(hostname: str, metric: str | None, prefix: str | None) -> str:
    """반환 ``query`` 필드용 PromQL 모양 표기(서버가 실제로 적용한 필터를 보여 준다)."""
    if prefix is None:
        return f"{metric or ''}{build_nodename_matcher(hostname)}"
    return (
        f'{metric or ""}{{__name__=~"{prefix}.*",'
        f'nodename="{_label_value_escape(hostname)}"}}'
    )


def _series_limit(cfg: OpenMetricsConfig, requested: int | None) -> int:
    """반환 시리즈 상한 — 요청값은 서버 상한 ``cfg.max_series``를 넘지 못한다."""
    limit = cfg.max_series if requested is None else min(int(requested), cfg.max_series)
    if limit < 1:
        raise ValueError(f"max_series는 1 이상이어야 한다: {limit}")
    return limit


def _require_target(cfg: OpenMetricsConfig, hostname: str) -> OpenMetricsTarget:
    """HTTP 전 타깃 검증 — 타깃 0건 · 빈 hostname · 목록 밖은 ValueError."""
    if not cfg.targets:
        raise ValueError(_TARGETS_UNSET)
    if not hostname or not str(hostname).strip():
        raise ValueError("hostname이 비어 있음")
    target = resolve_target(cfg, hostname)
    if target is None:
        raise ValueError(f"스크레이프 타깃 미등록: {str(hostname).strip()}")
    return target


# =====================================================================
# 감사 · HTTP
# =====================================================================


def _audit(
    tool: str,
    target: str,
    elapsed_ms: float,
    *,
    families: int | None = None,
    series: int | None = None,
    truncated: bool | None = None,
    error: str | None = None,
) -> None:
    """도구 호출 감사 로그(도구명·타깃·소요·패밀리 수·시리즈 수·절단). PromQL 도구와 같은 파이프."""
    if error is not None:
        logger.warning(
            "openmetrics audit: tool=%s target=%s elapsed_ms=%.1f error=%s",
            tool, target, elapsed_ms, error,
        )
    else:
        logger.info(
            "openmetrics audit: tool=%s target=%s elapsed_ms=%.1f families=%s series=%s "
            "truncated=%s",
            tool, target, elapsed_ms, families, series, truncated,
        )


def _fail(tool: str, target: str, start: float | None, exc: BaseException) -> str:
    """실패를 감사 로그에 남기고 ``{"error"}``를 돌려준다(예외 전파 금지)."""
    if isinstance(exc, (ValueError, _ScrapeError)):
        message = str(exc)
    else:
        message = f"스크레이프 실패({type(exc).__name__}): {exc}"
    elapsed_ms = 0.0 if start is None else (time.time() - start) * 1000
    _audit(tool, target, elapsed_ms, error=message)
    return _err(message)


async def _scrape(
    client: httpx.AsyncClient, cfg: OpenMetricsConfig, target: OpenMetricsTarget
) -> tuple[str, str]:
    """타깃을 GET 해서 ``(본문 텍스트, Content-Type)``을 돌려준다.

    Accept·리다이렉트 미추종·timeout은 요청마다 다시 지정한다(주입 클라이언트도 우회 불가).
    본문은 스트림으로 읽으며 누적 바이트가 ``max_body_bytes``를 넘는 즉시 중단한다.
    """
    url, params, _ = build_scrape_request(target)
    limit = cfg.max_body_bytes
    async with client.stream(
        "GET",
        url,
        params=params,
        headers={"Accept": om.PROM_SCRAPE_ACCEPT},
        follow_redirects=False,
        timeout=float(cfg.scrape_timeout),
    ) as resp:
        status = resp.status_code
        if 300 <= status < 400:
            raise _ScrapeError(
                f"리다이렉트 거부: status={status} (허용목록 우회 차단 — 최종 URL을 타깃으로 등록)"
            )
        if status != 200:
            raise _ScrapeError(f"exporter 비200 응답: status={status}")
        declared = resp.headers.get("content-length", "").strip()
        if declared.isdigit() and int(declared) > limit:
            raise _ScrapeError(
                f"응답 크기 상한 초과: Content-Length={declared} > max_body_bytes={limit}"
            )
        body = bytearray()
        async for chunk in resp.aiter_bytes():
            body.extend(chunk)
            if len(body) > limit:
                raise _ScrapeError(
                    f"응답 크기 상한 초과: 수신 {len(body)}바이트 > max_body_bytes={limit}"
                )
        content_type = resp.headers.get("content-type", "")
    try:
        return bytes(body).decode("utf-8"), content_type
    except UnicodeDecodeError as exc:
        raise _ScrapeError(f"응답 UTF-8 디코딩 실패: {exc}") from exc


async def _scrape_families(
    cfg: OpenMetricsConfig,
    target: OpenMetricsTarget,
    client: httpx.AsyncClient | None,
) -> tuple[list[Any], str]:
    """스크레이프 후 파싱한 ``(패밀리 목록, Content-Type)``. 클라이언트가 없으면 만들고 닫는다."""
    active = client if client is not None else make_scrape_client(cfg)
    try:
        text, content_type = await _scrape(active, cfg, target)
    finally:
        if client is None:
            await active.aclose()
    return om.parse_exposition(text, content_type), content_type


def _common_fields(
    target: OpenMetricsTarget, content_type: str, scraped_at: float, uname: str | None
) -> dict[str, Any]:
    """두 도구가 공유하는 반환 필드(출처·타깃·관측 시각·타깃 신원)."""
    fields: dict[str, Any] = {
        "source_kind": _SOURCE_KIND,
        "content_type": content_type,
        "target": target.hostname,
        "observed_at": datetime.fromtimestamp(scraped_at, UTC).isoformat(),
    }
    if uname:
        fields["uname_nodename"] = uname
    fields["target_identity"] = om.target_identity(
        uname, server_name=target.hostname, os_hostname=target.os_hostname
    )
    return fields


# =====================================================================
# 도구 코어 (async — 단위 테스트는 mock client 주입으로 고정)
# =====================================================================


async def run_om_metric_instant(
    cfg: OpenMetricsConfig,
    hostname: str,
    metric: str | None = None,
    prefix: str | None = None,
    max_series: int | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> str:
    """허용목록 타깃을 스크레이프해 instant vector로 돌려준다(plans/92 §4.3)."""
    tool = "om_metric_instant"
    try:
        target = _require_target(cfg, hostname)
        metric_name, prefix_name = om.validate_filters(metric, prefix)
        limit = _series_limit(cfg, max_series)
    except ValueError as exc:
        return _fail(tool, str(hostname).strip() or "-", None, exc)

    scraped_at = time.time()
    try:
        families, content_type = await _scrape_families(cfg, target, client)
        result = om.to_instant_vector(
            families,
            hostname=target.hostname,
            scraped_at=scraped_at,
            metric=metric_name,
            prefix=prefix_name,
            max_series=limit,
        )
    except Exception as exc:  # 스크레이프·파싱 경계 — 사유를 보존해 오류로 돌려준다
        return _fail(tool, target.hostname, scraped_at, exc)

    rows = result.data["result"]
    _, _, endpoint = build_scrape_request(target)
    _audit(
        tool, target.hostname, (time.time() - scraped_at) * 1000,
        families=len(families), series=len(rows), truncated=result.truncated,
    )
    return _ok(
        result.data,
        _describe_query(target.hostname, metric_name, prefix_name),
        endpoint,
        **_common_fields(target, content_type, scraped_at, result.uname_nodename),
        truncated=result.truncated,
        series_total=result.series_total,
        types=result.types,
    )


async def run_om_metric_catalog(
    cfg: OpenMetricsConfig,
    hostname: str,
    prefix: str | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> str:
    """허용목록 타깃을 스크레이프해 메트릭 패밀리 카탈로그를 돌려준다(plans/92 §4.3)."""
    tool = "om_metric_catalog"
    try:
        target = _require_target(cfg, hostname)
        prefix_name = (
            om.validate_metric_name("prefix", prefix) if prefix is not None else None
        )
    except ValueError as exc:
        return _fail(tool, str(hostname).strip() or "-", None, exc)

    scraped_at = time.time()
    try:
        families, content_type = await _scrape_families(cfg, target, client)
        rows = om.to_catalog(families, prefix=prefix_name)
    except Exception as exc:  # 스크레이프·파싱 경계 — 사유를 보존해 오류로 돌려준다
        return _fail(tool, target.hostname, scraped_at, exc)

    _, _, endpoint = build_scrape_request(target)
    _audit(
        tool, target.hostname, (time.time() - scraped_at) * 1000,
        families=len(families), series=sum(r["series"] for r in rows), truncated=False,
    )
    return _ok(
        rows,
        _describe_query(target.hostname, None, prefix_name),
        endpoint,
        **_common_fields(target, content_type, scraped_at, om.find_uname_nodename(families)),
        families=len(families),
    )


# =====================================================================
# 도구 등록
# =====================================================================


def _om_config(ctx: Context) -> OpenMetricsConfig:
    """컨텍스트에서 OpenMetrics 설정을 가져온다."""
    config: AppServerConfig = ctx.request_context.lifespan_context["config"]
    return config.openmetrics


def register_openmetrics_tools(
    mcp: FastMCP, expose: bool = False, source_ladder: bool = False
) -> None:
    """OpenMetrics 도구 2종을 등록한다. ``expose=False``면 아무것도 등록하지 않는다(비트 동일).

    Args:
        mcp: FastMCP 서버 인스턴스.
        expose: ``expose_openmetrics_tools`` 설정값(기본 비노출).
        source_ladder: True면 ``om_metric_catalog``를 여기서 등록하지 않는다 — 가용 소스를 싣는
            판을 ``metric_source.register_source_ladder_tools``가 대신 등록한다(plans/92 §4.8.4).
    """
    if not expose:
        logger.info("OpenMetrics 도구 비노출 (기본 — expose_openmetrics_tools=False)")
        return

    @mcp.tool()
    async def om_metric_instant(
        hostname: str,
        metric: str | None = None,
        prefix: str | None = None,
        max_series: int | None = None,
        ctx: Context | None = None,
    ) -> str:
        """서버(hostname)의 exporter를 지금 직접 읽어 메트릭 **현재값**을 조회한다(OpenMetrics).

        Prometheus를 거치지 않고, 서버 허용목록에 등록된 exporter의 /metrics를 이 호출 시점에
        스크레이프한 값이다. 이력·rate·집계는 없다(조회 시점의 현재 상태로만 서술할 것).
        counter는 누적값이다 — 반환 ``types``로 메트릭 타입을 확인하라. 결과는
        prom_metric_instant와 같은 vector 모양이며 ``source_kind="openmetrics"``와
        ``observed_at``(스크레이프 시각)이 붙는다. 메트릭 이름을 모르면 om_metric_catalog로
        먼저 확인하라(추측 금지).

        Args:
            hostname: 폴스타 등록 서버명(server_name). 허용목록에 등록된 호스트만 조회된다.
            metric: bare 메트릭 이름(예: node_load1). 패밀리 이름이면 _bucket/_sum/_count 전부.
            prefix: 메트릭 이름 접두(예: node_memory_). metric과 prefix 중 하나는 필수.
            max_series: 반환 시리즈 상한(서버 상한을 넘지 못한다). 넘치면 truncated=true.
            ctx: MCP 컨텍스트.

        Returns:
            JSON 문자열 {data, queried_at, source_kind, observed_at, types, truncated, ...}
            또는 {error}.
        """
        return await run_om_metric_instant(
            _om_config(ctx), hostname, metric, prefix, max_series
        )

    if source_ladder:
        logger.info("OpenMetrics 도구 노출됨 — instant (catalog은 소스 사다리 판으로 등록)")
        return

    @mcp.tool()
    async def om_metric_catalog(
        hostname: str,
        prefix: str | None = None,
        ctx: Context | None = None,
    ) -> str:
        """서버(hostname)의 exporter가 지금 내는 메트릭 목록을 조회한다(OpenMetrics).

        각 항목은 name·type·unit·help·series(시리즈 수)다. om_metric_instant에 넘길 메트릭
        이름과 타입(counter는 누적값)을 여기서 확인하라. 조회 시점의 현재 노출 내용이다.

        Args:
            hostname: 폴스타 등록 서버명(server_name). 허용목록에 등록된 호스트만 조회된다.
            prefix: 메트릭 이름 접두 필터(예: node_). 생략하면 전체.
            ctx: MCP 컨텍스트.

        Returns:
            JSON 문자열 {data: [{name, type, unit, help, series}], families, observed_at, ...}
            또는 {error}.
        """
        return await run_om_metric_catalog(_om_config(ctx), hostname, prefix)

    logger.info("OpenMetrics 도구 노출됨 (expose_openmetrics_tools=True) — instant/catalog")
