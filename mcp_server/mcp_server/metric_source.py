"""PromQL ↔ OpenMetrics 병행 — 소스 사다리·가용 소스·교차 검증 (plans/92 §4.8 · O2b · D-210).

두 채널을 함께 쓸 때 **소스 선택은 서버의 결정적 코드**가 한다(D-035). LLM은 결과의
``source_kind``·``fallback_reason``·``cross_check.verdict``를 서술만 한다.

- 사다리(§4.8.3): ``prom_metric_instant(source="auto")``가 PromQL을 먼저 보고, 정책
  (``fallback_policy``)이 허락할 때만 허용목록 exporter로 **호출당 최대 1회** 강등한다.
  강등 여부와 사유는 반환 최상위 ``fallback_reason``에 항상 싣는다
  (강등 없음 = null · 침묵 폴백 금지).
- 가용 소스(§4.8.4): Prometheus ``nodename`` 라벨 값 집합(모듈 수준 지연 캐시 + TTL —
  세션 간 공유)과 exporter 허용목록으로 호스트별 가용 소스를 판정한다.
- 교차 검증(§4.8.5 [v3]): 두 소스를 같이 읽어 6판정(``label_mismatch``·``not_scraped``·``stale``·
  ``scrape_down``·``value_drift``·``consistent``)을 낸다. 왕복은 최대 4회
  (PromQL 본 조회 · exporter · ``up`` 또는 ``timestamp()`` · 커버리지)다. 판정 근거가 없으면
  판정을 비우고 사유를 싣는다.

비트 동일(I-6): ``source`` 미지정(``auto``) + ``fallback_policy="off"`` + ``cross_check=false``는
종전 ``run_metric_instant`` 결과를 그대로 돌려준다. 확장 시그니처 자체도 OpenMetrics 도구가 켜지고
``PROMETHEUS_URL``이 있을 때만 등록된다(``register_source_ladder_tools``).

판정 규칙은 순수 함수(``plan_auto_source``·``fallback_reason_for``·``judge_cross_check`` 등)에 두고,
HTTP는 기존 ``promql_tools``·``openmetrics_tools`` 코어를 그대로 재사용한다.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import httpx
from mcp.server.fastmcp import Context, FastMCP

from mcp_server import openmetrics as om
from mcp_server import openmetrics_tools as omt
from mcp_server import promql_tools as pq
from mcp_server.config import AppServerConfig, OpenMetricsConfig, PrometheusConfig

logger = logging.getLogger(__name__)

SOURCE_AUTO = "auto"
SOURCE_PROMETHEUS = "prometheus"
SOURCE_EXPORTER = "exporter"
SOURCES: tuple[str, ...] = (SOURCE_AUTO, SOURCE_PROMETHEUS, SOURCE_EXPORTER)

#: ``fallback_reason`` 어휘(§4.8.3). 강등이 없으면 None(JSON null).
FALLBACK_PROMETHEUS_ERROR = "prometheus_error"
FALLBACK_PROMETHEUS_EMPTY = "prometheus_empty"
FALLBACK_PROMETHEUS_UNAVAILABLE = "prometheus_unavailable"
FALLBACK_HOST_NOT_COVERED = "host_not_covered"

#: 교차 검증 6판정(§4.8.5 [v3]).
VERDICT_LABEL_MISMATCH = "label_mismatch"
VERDICT_NOT_SCRAPED = "not_scraped"
VERDICT_STALE = "stale"
VERDICT_SCRAPE_DOWN = "scrape_down"
VERDICT_VALUE_DRIFT = "value_drift"
VERDICT_CONSISTENT = "consistent"
VERDICTS: tuple[str, ...] = (
    VERDICT_LABEL_MISMATCH, VERDICT_NOT_SCRAPED, VERDICT_STALE,
    VERDICT_SCRAPE_DOWN, VERDICT_VALUE_DRIFT, VERDICT_CONSISTENT,
)

#: 판정을 내릴 근거가 없을 때의 사유(판정은 None).
INCONCLUSIVE_PROMETHEUS_ERROR = "prometheus_error"
INCONCLUSIVE_EXPORTER_ERROR = "exporter_error"
INCONCLUSIVE_NO_COMMON_SERIES = "no_common_series"

_DIAGNOSIS: dict[str, str] = {
    VERDICT_LABEL_MISMATCH: "스크레이프 설정 nodename 정규화 필요 — 후보: {candidates}",
    VERDICT_NOT_SCRAPED: (
        "Prometheus가 이 호스트의 시리즈를 수집하지 않는다 — 타깃 등록 필요(인프라 협의)"
    ),
    VERDICT_STALE: "Prometheus 샘플이 오래됐다 — Prometheus 타깃 상태 확인(up)",
    VERDICT_SCRAPE_DOWN: "Prometheus 스크레이프 경로 확인(방화벽·타깃 주소) — 호스트는 응답함",
    VERDICT_VALUE_DRIFT: "두 소스 값이 허용 오차를 넘게 다르다 — 수치 그대로 보고(판단 유보)",
    VERDICT_CONSISTENT: "두 소스가 일치한다",
}

#: 시리즈 대조에서 뺄 라벨 — 스크레이프 설정이 붙이는 라벨이라 exporter 응답에는 없다(§4.8.1).
_SCRAPE_LABELS = frozenset({"job", "instance"})
#: ``stale`` 기준 = ``scrape_interval_hint`` × 이 배수(§4.8.5).
_STALE_INTERVALS = 3
#: ``value_drift`` 절대 하한 — 상대 오차가 이보다 작은 차이는 부동소수 잡음으로 본다.
_DRIFT_EPSILON = 1e-9

_EP_NODENAME_VALUES = "/api/v1/label/nodename/values"
_TOOL = "prom_metric_instant"

#: Prometheus 커버리지 캐시 — ``url → (만료 monotonic 시각, nodename 값 집합)``.
#: 실패는 캐시하지 않는다.
_COVERAGE_CACHE: dict[str, tuple[float, frozenset[str]]] = {}


# =====================================================================
# 사다리 규칙 (순수 함수)
# =====================================================================


def is_legacy_call(policy: str, source: str, cross_check: bool) -> bool:
    """종전 PromQL 경로를 그대로 타야 하는 호출인지 판정한다(I-6 — 요청·응답 바이트 동일)."""
    return source == SOURCE_AUTO and not cross_check and policy == "off"


def plan_auto_source(
    *, prometheus_configured: bool, has_target: bool, coverage: frozenset[str] | None, hostname: str
) -> tuple[str, str | None]:
    """``source="auto"``의 첫 소스를 정한다(R1·R2). ``(소스, 강등 사유)``를 돌려준다.

    - Prometheus 미설정 → exporter(``prometheus_unavailable``). 타깃이 없으면 R4는
      호출부가 판정한다.
    - 커버리지가 확인됐고 호스트가 없으며 exporter 타깃이 있으면 → exporter(``host_not_covered``).
    - 그 밖(커버리지 미상·포함·타깃 없음) → PromQL을 먼저 시도한다. 커버리지는 캐시라
      늦을 수 있어서, 대안이 없을 때는 PromQL 결과가 정본이다.
    """
    if not prometheus_configured:
        return SOURCE_EXPORTER, FALLBACK_PROMETHEUS_UNAVAILABLE
    if has_target and coverage is not None and hostname not in coverage:
        return SOURCE_EXPORTER, FALLBACK_HOST_NOT_COVERED
    return SOURCE_PROMETHEUS, None


def fallback_reason_for(policy: str, outcome: str) -> str | None:
    """PromQL 결과(``ok``·``empty``·``error``)와 정책으로 강등 사유를 정한다(R1).

    강등이 없으면 None이다.
    """
    if outcome == "error" and policy in ("on_unavailable", "on_empty"):
        return FALLBACK_PROMETHEUS_ERROR
    if outcome == "empty" and policy == "on_empty":
        return FALLBACK_PROMETHEUS_EMPTY
    return None


def classify_payload(payload: dict[str, Any]) -> str:
    """도구 결과 dict를 ``error``·``empty``·``ok``로 분류한다."""
    if "error" in payload:
        return "error"
    data = payload.get("data")
    rows = data.get("result") if isinstance(data, dict) else None
    return "ok" if rows else "empty"


def label_candidates(coverage: Iterable[str], hostname: str) -> list[str]:
    """커버리지에서 ``hostname``과 정규화 후 같지만 원문은 다른 값(FQDN↔단축명 등)을 찾는다.

    정규화는 타깃 신원 확인과 같은 ``openmetrics.normalize_hostname``이다(§4.8.5 [v3] — 규칙 한 곳).
    """
    wanted = om.normalize_hostname(hostname)
    return sorted(v for v in coverage if v != hostname and om.normalize_hostname(v) == wanted)


def availability(
    *, prometheus_configured: bool, has_target: bool, coverage: frozenset[str] | None, hostname: str
) -> dict[str, Any]:
    """호스트별 가용 소스(§4.8.4).

    ``prometheus_coverage``는 covered·not_covered·unknown·unconfigured 중 하나다.
    """
    if not prometheus_configured:
        state = "unconfigured"
    elif coverage is None:
        state = "unknown"
    else:
        state = "covered" if hostname in coverage else "not_covered"
    sources: list[str] = []
    if state in ("covered", "unknown"):
        sources.append(SOURCE_PROMETHEUS)
    if has_target:
        sources.append(SOURCE_EXPORTER)
    return {"sources_available": sources, "prometheus_coverage": state}


# =====================================================================
# 교차 검증 판정 (순수 함수)
# =====================================================================


def _num(value: Any, index: int = 1) -> float | None:
    """instant 샘플 ``[ts, "v"]``의 값(``index=1``)·시각(``index=0``)을 float로. 실패는 None."""
    try:
        return float(value[index])
    except (TypeError, ValueError, IndexError):
        return None


def _labels_key(labels: dict[str, Any], drop: Iterable[str]) -> frozenset[tuple[str, str]]:
    dropped = set(drop)
    return frozenset((k, str(v)) for k, v in labels.items() if k not in dropped)


def _pairs(
    prom_series: list[dict[str, Any]], om_series: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """exporter 시리즈마다 라벨(``job``·``instance`` 제외)이 모두 같은 PromQL 시리즈를 짝짓는다.

    PromQL 쪽은 스크레이프 설정 라벨이 더 붙을 수 있어 exporter 라벨의 상위집합이면 짝으로 본다.
    """
    pairs = []
    for o in om_series:
        want = _labels_key(o.get("metric", {}), _SCRAPE_LABELS)
        for p in prom_series:
            have = _labels_key(p.get("metric", {}), ())
            if want <= have:
                pairs.append((o, p))
    return pairs


def _drift(a: float, b: float, tolerance: float) -> bool:
    diff = abs(a - b)
    return diff > _DRIFT_EPSILON and diff > tolerance * max(abs(a), abs(b))


def judge_cross_check(
    *,
    prom_series: list[dict[str, Any]],
    om_series: list[dict[str, Any]],
    om_types: dict[str, str],
    now: float,
    tolerance: float,
    interval: float,
    up_series: list[dict[str, Any]] | None = None,
    ts_series: list[dict[str, Any]] | None = None,
    candidates: list[str] | None = None,
) -> dict[str, Any]:
    """두 소스의 instant 결과로 6판정 중 하나를 낸다(§4.8.5 [v3] · 결정적).

    PromQL이 비었을 때: ``up``=0 시리즈가 있으면 ``scrape_down`` → exporter에 데이터가 있고 정규화
    후보가 있으면 ``label_mismatch`` → 후보가 없으면 ``not_scraped``. PromQL이 있을 때:
    ``timestamp()`` 샘플 시각이 ``now − interval×3``보다 오래된 시리즈가 있으면 ``stale`` →
    짝지은 gauge 시리즈의 값 차가 허용 오차를 넘고 두 샘플 시각 차가 ``interval`` 미만이면
    ``value_drift`` → ``consistent``.
    대조할 시리즈가 없으면 판정 없이 ``inconclusive_reason="no_common_series"``를 돌려준다.

    Returns:
        ``{verdict, inconclusive_reason, compared_series, evidence}``.
    """
    evidence: dict[str, Any] = {
        "prometheus_series": len(prom_series),
        "openmetrics_series": len(om_series),
    }

    def result(verdict: str | None, compared: int = 0, reason: str | None = None) -> dict[str, Any]:
        return {
            "verdict": verdict,
            "inconclusive_reason": reason,
            "compared_series": compared,
            "evidence": evidence,
        }

    if not prom_series:
        down = [
            {k: v for k, v in s.get("metric", {}).items() if k in _SCRAPE_LABELS}
            for s in up_series or []
            if _num(s.get("value")) == 0
        ]
        if up_series is None:
            evidence["up_unavailable"] = True
        else:
            evidence["up_series"] = len(up_series)
        if down:
            evidence["down_targets"] = down
            return result(VERDICT_SCRAPE_DOWN)
        if not om_series:
            return result(None, reason=INCONCLUSIVE_NO_COMMON_SERIES)
        if candidates:
            evidence["candidates"] = list(candidates)
            return result(VERDICT_LABEL_MISMATCH)
        return result(VERDICT_NOT_SCRAPED)

    if ts_series is None:
        evidence["timestamp_unavailable"] = True  # stale·value_drift를 판정할 샘플 시각이 없다
    sample_time: dict[frozenset[tuple[str, str]], float] = {}
    for s in ts_series or []:
        t = _num(s.get("value"))
        if t is not None:
            sample_time[_labels_key(s.get("metric", {}), ())] = t
    threshold = now - interval * _STALE_INTERVALS
    stale = [
        {"labels": dict(key), "age_seconds": round(now - t, 3)}
        for key, t in sample_time.items()
        if t < threshold
    ]
    if stale:
        evidence["stale_series"] = stale
        return result(VERDICT_STALE)

    pairs = _pairs(prom_series, om_series)
    if not pairs:
        return result(None, reason=INCONCLUSIVE_NO_COMMON_SERIES)
    drifts = []
    for o, p in pairs:
        name = o.get("metric", {}).get("__name__")
        a, b = _num(p.get("value")), _num(o.get("value"))
        prom_t = sample_time.get(_labels_key(p.get("metric", {}), ("__name__",)))
        om_t = _num(o.get("value"), 0)
        if om_types.get(name) != "gauge" or a is None or b is None:
            continue
        if prom_t is None or om_t is None:
            continue
        if abs(prom_t - om_t) < interval and _drift(a, b, tolerance):
            drifts.append({
                "labels": o.get("metric", {}),
                "prometheus": {"value": a, "sample_time": prom_t},
                "openmetrics": {"value": b, "sample_time": om_t},
            })
    if drifts:
        evidence["drift_series"] = drifts
        return result(VERDICT_VALUE_DRIFT, compared=len(pairs))
    return result(VERDICT_CONSISTENT, compared=len(pairs))


def diagnosis_for(verdict: str | None, evidence: dict[str, Any]) -> str | None:
    """판정의 조치 안내 문구(§4.8.5 표). 판정이 없으면 None."""
    if verdict is None:
        return None
    return _DIAGNOSIS[verdict].format(candidates=", ".join(evidence.get("candidates", [])))


# =====================================================================
# I/O — 커버리지 캐시 · 보조 PromQL
# =====================================================================


def reset_coverage_cache() -> None:
    """커버리지 캐시를 비운다(테스트·설정 변경용)."""
    _COVERAGE_CACHE.clear()


async def _coverage(
    cfg: PrometheusConfig, ttl: int, client: httpx.AsyncClient | None
) -> tuple[frozenset[str] | None, bool]:
    """Prometheus ``nodename`` 라벨 값 집합과 이번에 HTTP를 쳤는지를 돌려준다.

    실패는 ``(None, True)``다.
    """
    cached = _COVERAGE_CACHE.get(cfg.url)
    if cached is not None and cached[0] > time.monotonic():
        return cached[1], False
    payload = json.loads(
        await pq._prom_get(
            cfg, _EP_NODENAME_VALUES, {}, "metric_source.coverage", "label/nodename/values",
            client=client,
        )
    )
    values = payload.get("data")
    if "error" in payload or not isinstance(values, list):
        return None, True
    coverage = frozenset(str(v) for v in values)
    _COVERAGE_CACHE[cfg.url] = (time.monotonic() + max(int(ttl), 0), coverage)
    return coverage, True


async def prometheus_coverage(
    cfg: PrometheusConfig, ttl: int, *, client: httpx.AsyncClient | None = None
) -> frozenset[str] | None:
    """Prometheus 커버리지(§4.8.4) — TTL 동안 캐시한다. 미설정·실패면 None(미상)."""
    if not cfg.url:
        return None
    coverage, _ = await _coverage(cfg, ttl, client)
    return coverage


async def _prom_series(
    cfg: PrometheusConfig, query: str, tool: str, client: httpx.AsyncClient | None
) -> list[dict[str, Any]] | None:
    """보조 PromQL(``up``·``timestamp()``) instant 결과 시리즈. 오류면 None."""
    payload = json.loads(await pq._prom_get(cfg, pq._EP_QUERY, {"query": query}, tool, query,
                                             client=client))
    if "error" in payload:
        return None
    return list((payload.get("data") or {}).get("result") or [])


# =====================================================================
# 도구 코어
# =====================================================================


def _audit(hostname: str, source: str, payload: dict[str, Any], reason: str | None) -> None:
    """사다리 감사 로그 — 강등 빈도가 곧 Prometheus 연동 건강도다(§4.8.3)."""
    logger.info(
        "metric source audit: tool=%s hostname=%s source=%s source_kind=%s fallback_reason=%s "
        "error=%s",
        _TOOL, hostname, source, payload.get("source_kind"), reason, "error" in payload,
    )


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _finish(
    hostname: str, source: str, result: str, reason: str | None, detail: str | None = None
) -> str:
    """결과에 ``fallback_reason``(항상)과 강등 상세(있을 때만)를 붙이고 감사 로그를 남긴다."""
    payload = json.loads(result)
    payload["fallback_reason"] = reason
    if detail is not None:
        payload["fallback_detail"] = detail
    _audit(hostname, source, payload, reason)
    return _dump(payload)


def _no_source(hostname: str, reason: str | None, tried: list[dict[str, Any]]) -> str:
    """R4 — 두 소스 모두 불가(침묵 폴백 금지 · I-3)."""
    payload = {"error": "조회 가능한 소스 없음", "tried": tried, "fallback_reason": reason}
    _audit(hostname, SOURCE_AUTO, payload, reason)
    return _dump(payload)


async def run_metric_instant_with_source(
    prom_cfg: PrometheusConfig,
    om_cfg: OpenMetricsConfig,
    hostname: str,
    metric: str,
    source: str = SOURCE_AUTO,
    cross_check: bool = False,
    *,
    prom_client: httpx.AsyncClient | None = None,
    om_client: httpx.AsyncClient | None = None,
) -> str:
    """소스 사다리를 적용한 instant 조회(§4.8.3). ``cross_check``면 교차 검증 결과를 돌려준다.

    ``source="auto"`` + ``fallback_policy="off"`` + ``cross_check=False``는 종전 결과와
    바이트 동일이다.
    """
    if source not in SOURCES:
        return pq._err(f"source는 {', '.join(SOURCES)} 중 하나여야 함: {source!r}")
    if is_legacy_call(om_cfg.fallback_policy, source, cross_check):
        return await pq.run_metric_instant(prom_cfg, hostname, metric, client=prom_client)
    if not hostname or not str(hostname).strip():
        return pq._err("hostname이 비어 있음")
    host = str(hostname).strip()
    try:
        pq.build_high_level_selector(metric, host)
    except ValueError as e:
        return pq._err(str(e))

    if cross_check:
        return _dump(await run_cross_check(
            prom_cfg, om_cfg, host, metric, prom_client=prom_client, om_client=om_client
        ))
    if source == SOURCE_PROMETHEUS:
        result = await pq.run_metric_instant(prom_cfg, host, metric, client=prom_client)
        return _finish(host, source, result, None)
    if source == SOURCE_EXPORTER:
        result = await omt.run_om_metric_instant(om_cfg, host, metric, client=om_client)
        return _finish(host, source, result, None)

    has_target = omt.resolve_target(om_cfg, host) is not None
    coverage = None
    if prom_cfg.url and has_target:  # 커버리지는 대안(exporter)이 있을 때만 결정을 바꾼다
        coverage = await prometheus_coverage(
            prom_cfg, om_cfg.coverage_ttl_seconds, client=prom_client
        )
    first, reason = plan_auto_source(
        prometheus_configured=bool(prom_cfg.url), has_target=has_target,
        coverage=coverage, hostname=host,
    )
    tried: list[dict[str, Any]] = []
    detail: str | None = None
    if first == SOURCE_PROMETHEUS:
        prom_result = await pq.run_metric_instant(prom_cfg, host, metric, client=prom_client)
        prom_payload = json.loads(prom_result)
        outcome = classify_payload(prom_payload)
        reason = fallback_reason_for(om_cfg.fallback_policy, outcome)
        # 강등 사유가 없거나, 빈 결과인데 대안이 없으면 PromQL 결과가 정본이다.
        if reason is None or (outcome == "empty" and not has_target):
            return _finish(host, source, prom_result, None)
        detail = prom_payload.get("error")
        tried.append({"source": SOURCE_PROMETHEUS, "outcome": outcome, "error": detail})
    else:
        tried.append({"source": SOURCE_PROMETHEUS, "outcome": reason})
    if not has_target:
        tried.append({"source": SOURCE_EXPORTER, "outcome": "error",
                      "error": "스크레이프 타깃 미등록"})
        return _no_source(host, reason, tried)
    om_result = await omt.run_om_metric_instant(om_cfg, host, metric, client=om_client)
    om_payload = json.loads(om_result)
    if "error" in om_payload:
        tried.append({"source": SOURCE_EXPORTER, "outcome": "error", "error": om_payload["error"]})
        return _no_source(host, reason, tried)
    return _finish(host, source, om_result, reason, detail)


async def run_cross_check(
    prom_cfg: PrometheusConfig,
    om_cfg: OpenMetricsConfig,
    hostname: str,
    metric: str,
    *,
    prom_client: httpx.AsyncClient | None = None,
    om_client: httpx.AsyncClient | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """한 호스트·메트릭을 두 소스로 읽어 교차 검증한다(§4.8.5 [v3] · 왕복 ≤ 4).

    전제 오류(URL 미설정·타깃 미등록·metric 형식)는 HTTP 없이 ``{"error"}``다.

    Returns:
        ``{cross_check: {verdict, diagnosis, inconclusive_reason, compared_series, round_trips,
        evidence}, prometheus, openmetrics, fallback_reason: None, queried_at}``.
    """
    host = str(hostname).strip()
    if not prom_cfg.url:
        return {"error": "교차 검증 불가 — PROMETHEUS_URL 미설정", "fallback_reason": None}
    if omt.resolve_target(om_cfg, host) is None:
        return {"error": f"교차 검증 불가 — 스크레이프 타깃 미등록: {host}",
                "fallback_reason": None}
    try:
        selector = pq.build_high_level_selector(metric, host)
    except ValueError as e:
        return {"error": str(e), "fallback_reason": None}

    prom = json.loads(await pq.run_metric_instant(prom_cfg, host, metric, client=prom_client))
    omr = json.loads(await omt.run_om_metric_instant(om_cfg, host, metric, client=om_client))
    round_trips = 2
    judged: dict[str, Any]
    if "error" in prom or "error" in omr:
        reason = INCONCLUSIVE_PROMETHEUS_ERROR if "error" in prom else INCONCLUSIVE_EXPORTER_ERROR
        judged = {"verdict": None, "inconclusive_reason": reason, "compared_series": 0,
                  "evidence": {}}
    else:
        prom_series = list(prom["data"].get("result") or [])
        om_series = list(omr["data"].get("result") or [])
        up_series = ts_series = None
        candidates: list[str] = []
        if prom_series:
            ts_series = await _prom_series(prom_cfg, f"timestamp({selector})",
                                           "metric_source.cross_check.timestamp", prom_client)
            round_trips += 1
        else:
            up_series = await _prom_series(prom_cfg, f"up{pq.build_nodename_matcher(host)}",
                                           "metric_source.cross_check.up", prom_client)
            round_trips += 1
            has_down = any(_num(s.get("value")) == 0 for s in up_series or [])
            if om_series and not has_down:
                coverage, fetched = await _coverage(prom_cfg, om_cfg.coverage_ttl_seconds,
                                                    prom_client)
                round_trips += int(fetched)
                candidates = label_candidates(coverage or (), host)
        judged = judge_cross_check(
            prom_series=prom_series, om_series=om_series, om_types=omr.get("types") or {},
            now=time.time() if now is None else now,
            tolerance=om_cfg.cross_check_tolerance, interval=float(om_cfg.scrape_interval_hint),
            up_series=up_series, ts_series=ts_series, candidates=candidates,
        )
    judged["diagnosis"] = diagnosis_for(judged["verdict"], judged["evidence"])
    judged["round_trips"] = round_trips
    logger.info(
        "metric source audit: tool=%s hostname=%s cross_check verdict=%s inconclusive=%s "
        "round_trips=%d",
        _TOOL, host, judged["verdict"], judged["inconclusive_reason"], round_trips,
    )
    return {
        "cross_check": judged,
        "prometheus": prom,
        "openmetrics": omr,
        "fallback_reason": None,
        "queried_at": datetime.now(UTC).isoformat(),
    }


async def run_catalog_with_sources(
    prom_cfg: PrometheusConfig,
    om_cfg: OpenMetricsConfig,
    hostname: str,
    prefix: str | None = None,
    *,
    prom_client: httpx.AsyncClient | None = None,
    om_client: httpx.AsyncClient | None = None,
) -> str:
    """``om_metric_catalog`` 결과에 호스트별 가용 소스(§4.8.4)를 더한다. 오류는 그대로 돌려준다."""
    result = await omt.run_om_metric_catalog(om_cfg, hostname, prefix, client=om_client)
    payload = json.loads(result)
    if "error" in payload:
        return result
    host = str(hostname).strip()
    coverage = await prometheus_coverage(prom_cfg, om_cfg.coverage_ttl_seconds, client=prom_client)
    payload.update(availability(
        prometheus_configured=bool(prom_cfg.url), has_target=True, coverage=coverage, hostname=host
    ))
    return _dump(payload)


# =====================================================================
# 도구 등록 — (γ) 확장 시그니처 (OpenMetrics 도구 on + PROMETHEUS_URL 설정일 때만)
# =====================================================================


def _app_config(ctx: Context) -> AppServerConfig:
    """컨텍스트에서 서버 전체 설정을 가져온다(세션 시작 시 해석된 값 — §4.8.3 [v3])."""
    config: AppServerConfig = ctx.request_context.lifespan_context["config"]
    return config


def register_source_ladder_tools(mcp: FastMCP) -> None:
    """확장 ``prom_metric_instant``(``source``·``cross_check``)와 가용 소스 동반
    ``om_metric_catalog``를 등록한다.

    호출부(``create_server``)가 같은 이름의 종전 도구를 등록하지 않을 때만 부른다(분기 등록 — I-6).
    """

    @mcp.tool()
    async def prom_metric_instant(
        hostname: str,
        metric: str,
        source: str = SOURCE_AUTO,
        cross_check: bool = False,
        ctx: Context | None = None,
    ) -> str:
        """서버(hostname)의 메트릭 순간값을 조회한다(Prometheus instant · exporter 강등 지원).

        서버가 ``{nodename="<hostname>"}`` 필터를 결정적으로 조립한다(§5-0). metric은
        bare 메트릭 이름만 허용한다(임의 PromQL은 원시 도구 사용). 소스는 서버 정책이 고른다 —
        결과의 ``source_kind``가 ``openmetrics``면 exporter를 지금 직접 읽은 현재값이며 이력·rate는
        없다. ``fallback_reason``(강등 사유 · 없으면 null)을 그대로 언급하라.

        Args:
            hostname: 등록 서버명(server_name = Prometheus nodename 라벨).
            metric: bare 메트릭 이름(예: node_load1).
            source: auto(서버 정책 · 기본) | prometheus | exporter.
            cross_check: true면 두 소스를 함께 읽어 판정(verdict)을 돌려준다(source는 무시).
            ctx: MCP 컨텍스트.

        Returns:
            JSON 문자열 {data, queried_at, source_kind, fallback_reason, ...} ·
            cross_check면 {cross_check: {verdict, diagnosis, ...}, prometheus, openmetrics} ·
            또는 {error}.
        """
        config = _app_config(ctx)
        result = await run_metric_instant_with_source(
            config.prometheus, config.openmetrics, hostname, metric, source, cross_check
        )
        return pq._with_openmetrics_hint(result)

    @mcp.tool()
    async def om_metric_catalog(
        hostname: str,
        prefix: str | None = None,
        ctx: Context | None = None,
    ) -> str:
        """서버(hostname)의 exporter가 지금 내는 메트릭 목록과 호스트의 가용 소스를 조회한다.

        각 항목은 name·type·unit·help·series(시리즈 수)다. om_metric_instant·prom_metric_instant에
        넘길 메트릭 이름과 타입(counter는 누적값)을 여기서 확인하라. ``sources_available``은 이
        호스트를 읽을 수 있는 소스(prometheus·exporter)다 — 추측하지 말고 이 값을 따르라.

        Args:
            hostname: 등록 서버명(server_name). 허용목록에 등록된 호스트만 조회된다.
            prefix: 메트릭 이름 접두 필터(예: node_). 생략하면 전체.
            ctx: MCP 컨텍스트.

        Returns:
            JSON 문자열 {data: [{name, type, unit, help, series}], families, observed_at,
            sources_available, prometheus_coverage, ...} 또는 {error}.
        """
        config = _app_config(ctx)
        return await run_catalog_with_sources(
            config.prometheus, config.openmetrics, hostname, prefix
        )

    logger.info("PromQL↔OpenMetrics 소스 사다리 노출됨 — prom_metric_instant(source·cross_check)")
