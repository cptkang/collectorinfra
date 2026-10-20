"""Prometheus PromQL 조사용 MCP 도구 (Plan 66 Wave 2-B′ · sre-agent/06 §3 · D-119).

Prometheus 접근을 본 서버로 일원화한다(holmesgpt 내장 ``prometheus/metrics`` toolset
직결 미채택 — D-119). 도구는 두 층으로 구성된다.

- **고수준(기본 노출)**: hostname 앵커. 서버가 ``hostname`` 인자에서 라벨 셀렉터를
  **결정적으로 조립**한다(Plan 06 §5-0 · D-119 — LLM이 라벨을 직접 다루지 않는다, D-035 3차 방어).
  ``prom_metric_instant`` / ``prom_metric_range``. metric은 bare 메트릭 이름만 허용해 임의
  PromQL을 막는다. 조립 방식은 ``[prometheus].anchor_mode``가 정한다(plans/189 · D-119 ③ 부기).

  - ``nodename``(기본): ``{nodename="<hostname>"}`` 고정 — 종전과 바이트 동일.
  - ``polestar_host``: 폴스타에서 hostname(등록명 또는 OS 호스트명)의 OS 호스트명·IP를 해석하고
    ``{host_label,ip_label}`` → ``{ip_label}`` → ``{host_label}`` 사다리를 ``up`` instant로 판정한다.
    어느 단이든 ``instance_label`` 값이 2개 이상이면 합치지 않고 「모호」로 반환한다. 0건은
    ``{error}``가 아니라 성공 응답 + ``reason``·``hint``다.
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
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import Context, FastMCP

from mcp_server.config import AppServerConfig, PrometheusConfig
from mcp_server.polestar_tools import (
    ANCHOR_AMBIGUOUS,
    ANCHOR_LOOKUP_ERROR,
    ANCHOR_NOT_FOUND,
    AnchorResolution,
    log_safe,
    resolve_polestar_anchor,
)
from mcp_server.tool_response import err as _err
from mcp_server.tool_response import now_iso as _now_iso

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

#: ``anchor_mode`` 값 (plans/189 §2.1).
ANCHOR_MODE_NODENAME = "nodename"
ANCHOR_MODE_POLESTAR_HOST = "polestar_host"

#: ``matched_by`` 어휘 — 사다리 단의 이름이다. 라벨 이름을 설정으로 바꿔도 어휘는 고정한다
#: (소비자 지침이 이 값으로 분기한다 · 실제 라벨은 ``tried``에 남는다).
MATCHED_BY_HOST_IP = "hostname+node_ip"
MATCHED_BY_IP = "node_ip"
MATCHED_BY_HOST = "hostname"
MATCHED_BY_HOST_UNVERIFIED = "hostname_unverified"

#: 0건 응답의 ``reason`` 어휘 (plans/189 §2.4).
REASON_NOT_MONITORED = "prometheus_not_monitored"
REASON_PROMETHEUS_AMBIGUOUS = "prometheus_ambiguous"
REASON_POLESTAR_AMBIGUOUS = "polestar_ambiguous"
REASON_UNSUPPORTED_ZONE = "prometheus_unsupported_zone"
REASON_POLESTAR_NOT_FOUND = "polestar_not_found"
REASON_ANCHOR_UNRESOLVED = "anchor_unresolved"
REASONS: tuple[str, ...] = (
    REASON_NOT_MONITORED, REASON_PROMETHEUS_AMBIGUOUS, REASON_POLESTAR_AMBIGUOUS,
    REASON_UNSUPPORTED_ZONE, REASON_POLESTAR_NOT_FOUND, REASON_ANCHOR_UNRESOLVED,
)

#: reason별 고정 힌트 문장 — LLM이 다음 행동을 결정적으로 알게 한다.
_REASON_HINTS: dict[str, str] = {
    REASON_NOT_MONITORED: "Prometheus 미수집 서버다. 폴스타 지표(polestar_metric_trend)로 확인하라",
    REASON_PROMETHEUS_AMBIGUOUS: (
        "같은 라벨에 Prometheus 인스턴스가 여럿이라 합치지 않았다. "
        "폴스타 지표(polestar_metric_trend)로 확인하라"
    ),
    REASON_POLESTAR_AMBIGUOUS: (
        "폴스타에 같은 이름의 서버가 여럿이다. 후보(candidates)의 source를 지정해 다시 불러라"
    ),
    REASON_UNSUPPORTED_ZONE: (
        "이 source는 Prometheus 수집 대상이 아니다. 폴스타 지표(polestar_metric_trend)로 확인하라"
    ),
    REASON_POLESTAR_NOT_FOUND: (
        "폴스타 등록명이나 OS 호스트명을 그대로 넘겨라. source를 지정했다면 빼고 다시 불러라. "
        "Prometheus 미적용 존의 서버라면 source에 알람 dbId를 넣어 다시 부르거나 "
        "폴스타 지표(polestar_metric_trend)로 확인하라"
    ),
    REASON_ANCHOR_UNRESOLVED: (
        "폴스타에서 서버 신원(OS 호스트명·IP)을 확인하지 못했고 Prometheus에서도 찾지 못했다. "
        "관제 미등록으로 단정하지 말라"
    ),
}

#: 폴스타 조회 오류로 hostname 단독(3단)만 시도할 때의 경고 문장(G-2 A안).
LOOKUP_ERROR_WARNING = "폴스타 조회 실패 — 서버 신원 미확인"
#: 폴스타 행은 있으나 OS 호스트명·IP가 모두 비어 hostname 단독(3단)만 시도할 때의 경고 문장.
BLANK_ANCHOR_WARNING = "폴스타 행에 OS 호스트명·IP 없음 — 서버 신원 미확인"

#: 앵커 모드 입력 상한(plans/189 보안 L-2) — 넘으면 조회 없이 호출 오류다. nodename 모드는 적용 안 함.
_ANCHOR_HOSTNAME_MAX = 255
_ANCHOR_SOURCE_MAX = 64

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
    return f"{validate_metric_name(metric)}{build_nodename_matcher(hostname)}"


def validate_metric_name(metric: str) -> str:
    """고수준 도구의 metric이 bare 메트릭 이름인지 검증하고 공백을 걷어 돌려준다.

    Raises:
        ValueError: metric이 bare 메트릭 이름이 아닐 때(임의 PromQL·라벨 셀렉터 차단).
    """
    m = str(metric).strip()
    if not _METRIC_NAME_RE.match(m):
        raise ValueError(
            f"고수준 도구의 metric은 bare 메트릭 이름만 허용: {metric!r} "
            "(임의 PromQL은 원시 도구를 사용)"
        )
    return m


def build_label_matcher(pairs: list[tuple[str, str]]) -> str:
    """``(라벨 이름, 값)`` 목록으로 ``{a="x",b="y"}`` 매처를 조립한다(완전 일치 ``=``만 · ``=~`` 금지).

    라벨 이름은 설정 검증(``validate_prometheus_anchor``)을 통과한 값만 들어온다. 값은
    ``_label_value_escape``로 이스케이프한다.
    """
    body = ",".join(f'{name}="{_label_value_escape(value)}"' for name, value in pairs)
    return f"{{{body}}}"


def build_anchor_ladder(
    cfg: PrometheusConfig, hostname: str, ipaddress: str
) -> list[tuple[str, str]]:
    """앵커 값으로 사다리 ``[(matched_by, 매처)]``를 만든다(plans/189 §2.3).

    1단 ``{host_label,ip_label}`` → 2단 ``{ip_label}`` → 3단 ``{host_label}``. 앵커 값이 빈 단은
    건너뛴다(IP 공백이면 1·2단 없음).
    """
    host = str(hostname or "").strip()
    ip = str(ipaddress or "").strip()
    steps: list[tuple[str, str]] = []
    if host and ip:
        steps.append((MATCHED_BY_HOST_IP,
                      build_label_matcher([(cfg.host_label, host), (cfg.ip_label, ip)])))
    if ip:
        steps.append((MATCHED_BY_IP, build_label_matcher([(cfg.ip_label, ip)])))
    if host:
        steps.append((MATCHED_BY_HOST, build_label_matcher([(cfg.host_label, host)])))
    return steps


def distinct_instances(series: list[dict[str, Any]], instance_label: str) -> list[str]:
    """시리즈들의 ``instance_label`` 서로 다른 값(정렬). 라벨이 없으면 빈 문자열로 센다."""
    return sorted({str((s.get("metric") or {}).get(instance_label, "")) for s in series})


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


def _audit(
    tool: str,
    query: str,
    elapsed_ms: float,
    count: Optional[int],
    error: Optional[str] = None,
    *,
    reason: Optional[str] = None,
    matched_by: Optional[str] = None,
) -> None:
    """도구 호출 감사 로그를 남긴다(도구명·쿼리·소요·행수 — 폴스타 도구와 동일 파이프).

    ``reason``·``matched_by``(plans/189 앵커 모드)는 주어졌을 때만 꼬리에 붙는다 — 기본 모드의
    감사 문자열은 종전과 같다.
    """
    if error is not None:
        logger.warning(
            "promql audit: tool=%s query=%s elapsed_ms=%.1f error=%s",
            tool, query, elapsed_ms, error,
        )
    elif reason is not None or matched_by is not None:
        logger.info(
            "promql audit: tool=%s query=%s elapsed_ms=%.1f rows=%s reason=%s matched_by=%s",
            tool, query, elapsed_ms, count, reason, matched_by,
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
    audit_matched_by: Optional[str] = None,
    **extra: Any,
) -> str:
    """Prometheus 읽기 엔드포인트를 GET 하고 반환·오류·감사 계약을 적용한다.

    ``client``가 주어지면 그대로 사용한다(테스트 주입용). 없으면 ``make_client``로
    timeout이 강제된 클라이언트를 생성한다. 예외는 전파하지 않고 ``{"error": ...}``로
    변환한다(침묵 폴백 금지 — 사유를 오류/감사로 노출). ``audit_matched_by``는 앵커 모드의
    성공 감사 꼬리다(미지정이면 감사 문자열이 종전과 같다).
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
        _audit(tool, query_desc, elapsed_ms, count, matched_by=audit_matched_by)
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


def _reference_epoch(reference_time: Optional[str]) -> float:
    """기준시각 → epoch 초. 미지정이면 now. 비 ISO 문자열은 거부한다(ValueError)."""
    if reference_time is None:
        return time.time()
    if not isinstance(reference_time, str) or not reference_time.strip():
        raise ValueError(f"reference_time은 ISO 8601 문자열이어야 함: {reference_time!r}")
    try:
        return datetime.fromisoformat(reference_time.strip()).timestamp()
    except ValueError as e:
        raise ValueError(f"reference_time은 ISO 8601 문자열이어야 함: {reference_time!r}") from e


# =====================================================================
# 라벨 앵커 사다리 (anchor_mode=polestar_host — plans/189 §2.2~§2.4)
# =====================================================================


@dataclass
class LadderProbe:
    """사다리 ``up`` 판정 결과.

    ``matcher``는 성립한 단(또는 「모호」가 난 단)의 매처, ``up_series``는 그 단의 ``up`` 시리즈다.
    ``error``가 있으면 Prometheus 호출 오류 JSON 문자열이다(그대로 도구 결과가 된다).
    """

    matched_by: Optional[str] = None
    matcher: Optional[str] = None
    up_series: list[dict[str, Any]] = field(default_factory=list)
    instances: list[str] = field(default_factory=list)
    tried: list[str] = field(default_factory=list)
    ambiguous: bool = False
    error: Optional[str] = None


def is_anchor_mode(cfg: PrometheusConfig) -> bool:
    """``anchor_mode=polestar_host``인지 판정한다(그 밖은 nodename 현행 경로)."""
    return str(cfg.anchor_mode).strip().lower() == ANCHOR_MODE_POLESTAR_HOST


def zone_blocked(cfg: PrometheusConfig, source: Optional[str]) -> bool:
    """지정 ``source``가 수집 source 목록(``cfg.sources``) 밖인지. 목록이 비면 제한 없음."""
    return bool(source) and bool(cfg.sources) and source not in cfg.sources


async def probe_anchor_ladder(
    cfg: PrometheusConfig,
    steps: list[tuple[str, str]],
    *,
    tool: str,
    client: Optional[httpx.AsyncClient] = None,
    at: Optional[float] = None,
) -> LadderProbe:
    """사다리 단마다 ``up{매처}`` instant 1회로 성립을 판정한다(plans/189 §2.3).

    결과 ≥1 · 서로 다른 ``instance_label`` 값 1개면 그 단에서 성립한다. 2개 이상이면 합치지 않고
    「모호」로 즉시 끝낸다. 0건이면 다음 단으로 간다. ``at``(epoch 초)이 있으면 그 시각으로 판정한다.
    """
    probe = LadderProbe()
    for matched_by, matcher in steps:
        selector = f"up{matcher}"
        probe.tried.append(selector)
        params: dict[str, Any] = {"query": selector}
        if at is not None:
            params["time"] = f"{at:.3f}"
        raw = await _prom_get(cfg, _EP_QUERY, params, f"{tool}.anchor_probe", selector,
                              client=client)
        payload = json.loads(raw)
        if "error" in payload:
            probe.error = raw
            return probe
        series = list((payload.get("data") or {}).get("result") or [])
        if not series:
            continue
        probe.matched_by, probe.matcher, probe.up_series = matched_by, matcher, series
        probe.instances = distinct_instances(series, cfg.instance_label)
        probe.ambiguous = len(probe.instances) > 1
        return probe
    return probe


def _reason_reply(
    tool: str,
    endpoint: str,
    reason: str,
    *,
    host: str,
    metric: str,
    query: str = "",
    tried: Optional[list[str]] = None,
    anchor: Optional[dict[str, str]] = None,
    matched_by: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
    **more: Any,
) -> str:
    """0건 성공 응답(``reason``·``hint`` 동반)을 만들고 감사 로그를 남긴다(``{error}`` 아님)."""
    result_type = "matrix" if endpoint == _EP_QUERY_RANGE else "vector"
    _audit(tool, query or f"(미조회) hostname={log_safe(host)} metric={metric}", 0.0, 0,
           reason=reason, matched_by=matched_by)
    payload: dict[str, Any] = dict(extra or {})
    payload.update(
        matched_by=matched_by,
        tried=list(tried or []),
        anchor=anchor,
        reason=reason,
        hint=_REASON_HINTS[reason],
    )
    payload.update({k: v for k, v in more.items() if v is not None})
    return _ok({"resultType": result_type, "result": []}, query, endpoint, **payload)


async def _run_anchored(
    cfg: PrometheusConfig,
    host: str,
    metric: str,
    *,
    tool: str,
    endpoint: str,
    params: dict[str, Any],
    extra: dict[str, Any],
    client: Optional[httpx.AsyncClient],
    pool: Any,
    source: Optional[str],
    resolution: Optional[AnchorResolution],
    probe_time: Optional[float],
) -> tuple[str, Optional[LadderProbe]]:
    """``anchor_mode=polestar_host`` 조회 — 존 차단 → 폴스타 해석 → 사다리 판정 → 본 조회 1회.

    최대 HTTP는 ``up`` 3회 + 본 조회 1회다. 존 차단·폴스타 0행·폴스타 다중 행은 HTTP 없이
    끝난다. ``resolution``이 주어지면 폴스타를 다시 조회하지 않는다(호출부가 이미 해석한 경우).
    hostname이 255자, source가 64자를 넘으면 조회 없이 호출 오류다.
    """
    src = str(source).strip() if source else None
    if len(host) > _ANCHOR_HOSTNAME_MAX:
        return _err(f"hostname이 너무 김(최대 {_ANCHOR_HOSTNAME_MAX}자)"), None
    if src and len(src) > _ANCHOR_SOURCE_MAX:
        return _err(f"source가 너무 김(최대 {_ANCHOR_SOURCE_MAX}자)"), None
    if not cfg.url:
        return _err("PROMETHEUS_URL 미설정 — PromQL 조회 불가"), None
    ctx = {"host": host, "metric": metric, "extra": extra}
    if zone_blocked(cfg, src):
        return _reason_reply(tool, endpoint, REASON_UNSUPPORTED_ZONE, **ctx), None
    if resolution is None:
        if pool is None:
            return _err("폴스타 DB 풀 없음 — anchor_mode=polestar_host 앵커 해석 불가"), None
        try:
            resolution = await resolve_polestar_anchor(pool, host, src, cfg.sources)
        except ValueError as e:
            return _err(str(e)), None
    if resolution.status == ANCHOR_NOT_FOUND:
        return _reason_reply(tool, endpoint, REASON_POLESTAR_NOT_FOUND, **ctx,
                             warning=resolution.warning), None
    if resolution.status == ANCHOR_AMBIGUOUS:
        candidates = [{"source": c["source"], "name": c["name"]} for c in resolution.candidates]
        return _reason_reply(tool, endpoint, REASON_POLESTAR_AMBIGUOUS, **ctx,
                             candidates=candidates, warning=resolution.warning), None

    anchor: Optional[dict[str, str]] = None
    warnings: list[str] = []
    if resolution.status == ANCHOR_LOOKUP_ERROR:
        # G-2 A안 — 신원 미확인으로 hostname 단독(3단)만 시도한다(「모호」 판정은 유지).
        steps = [(MATCHED_BY_HOST_UNVERIFIED, build_label_matcher([(cfg.host_label, host)]))]
        warnings.append(LOOKUP_ERROR_WARNING)
        fixed_matched_by: Optional[str] = MATCHED_BY_HOST_UNVERIFIED
        empty_reason = REASON_ANCHOR_UNRESOLVED
    else:
        anchor = dict(resolution.anchor or {})
        steps = build_anchor_ladder(cfg, anchor.get("hostname", ""), anchor.get("ipaddress", ""))
        fixed_matched_by = None
        empty_reason = REASON_NOT_MONITORED
        if not steps:
            # 행은 있으나 OS 호스트명·IP가 모두 비었다 — 신원 미확인으로 입력값 단독(3단)만 시도한다
            # (조회 오류와 같은 강등 · anchor는 그 행을 그대로 싣는다).
            steps = [(MATCHED_BY_HOST_UNVERIFIED,
                      build_label_matcher([(cfg.host_label, host)]))]
            warnings.append(BLANK_ANCHOR_WARNING)
            fixed_matched_by = MATCHED_BY_HOST_UNVERIFIED
            empty_reason = REASON_ANCHOR_UNRESOLVED
    if resolution.warning:
        warnings.append(resolution.warning)
    warning = " · ".join(warnings) or None

    owns_client = client is None
    if owns_client:
        client = make_client(cfg)
    try:
        probe = await probe_anchor_ladder(cfg, steps, tool=tool, client=client, at=probe_time)
        if probe.error is not None:
            return probe.error, probe
        last = probe.tried[-1] if probe.tried else ""
        if probe.ambiguous:
            return _reason_reply(tool, endpoint, REASON_PROMETHEUS_AMBIGUOUS, **ctx, query=last,
                                 tried=probe.tried, anchor=anchor, matched_by=fixed_matched_by,
                                 instances=probe.instances, warning=warning), probe
        if probe.matcher is None:
            return _reason_reply(tool, endpoint, empty_reason, **ctx, query=last,
                                 tried=probe.tried, anchor=anchor, matched_by=fixed_matched_by,
                                 warning=warning), probe
        matched_by = fixed_matched_by or probe.matched_by
        selector = f"{metric}{probe.matcher}"
        reply: dict[str, Any] = dict(extra)
        reply.update(matched_by=matched_by, tried=probe.tried, anchor=anchor)
        if warning:
            reply["warning"] = warning
        result = await _prom_get(
            cfg, endpoint, {"query": selector, **params}, tool, selector,
            client=client, audit_matched_by=matched_by, **reply,
        )
        return result, probe
    finally:
        if owns_client and client is not None:
            await client.aclose()


async def run_metric_instant_detail(
    cfg: PrometheusConfig,
    hostname: str,
    metric: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    pool: Any = None,
    source: Optional[str] = None,
    resolution: Optional[AnchorResolution] = None,
) -> tuple[str, Optional[LadderProbe]]:
    """``run_metric_instant``와 같고, 앵커 모드면 사다리 판정 결과(``LadderProbe``)도 돌려준다.

    교차 검증(``metric_source``)이 ``up`` 시리즈를 다시 묻지 않도록 판정 결과를 재사용한다.
    nodename 모드는 판정이 없어 ``None``이다.
    """
    if not is_anchor_mode(cfg):
        return await _run_metric_instant_nodename(cfg, hostname, metric, client=client), None
    if not hostname or not str(hostname).strip():
        return _err("hostname이 비어 있음"), None
    try:
        name = validate_metric_name(metric)
    except ValueError as e:
        return _err(str(e)), None
    return await _run_anchored(
        cfg, str(hostname).strip(), name, tool="prom_metric_instant", endpoint=_EP_QUERY,
        params={}, extra={}, client=client, pool=pool, source=source, resolution=resolution,
        probe_time=None,
    )


async def run_metric_instant(
    cfg: PrometheusConfig,
    hostname: str,
    metric: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    pool: Any = None,
    source: Optional[str] = None,
    resolution: Optional[AnchorResolution] = None,
) -> str:
    """고수준 instant 조회(§3·§5-0).

    ``anchor_mode=nodename``(기본)은 ``metric{nodename="hostname"}`` — ``pool``·``source``를 쓰지
    않으며 종전과 바이트 동일하다. ``polestar_host``는 폴스타 앵커 사다리(plans/189)다.
    """
    result, _ = await run_metric_instant_detail(
        cfg, hostname, metric, client=client, pool=pool, source=source, resolution=resolution
    )
    return result


async def _run_metric_instant_nodename(
    cfg: PrometheusConfig,
    hostname: str,
    metric: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> str:
    """nodename 모드 instant 조회: ``metric{nodename="hostname"}`` 순간값(종전 경로 그대로)."""
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
    reference_time: Optional[str] = None,
    pool: Any = None,
    source: Optional[str] = None,
) -> str:
    """고수준 range 조회: hostname 서버의 window 시계열(§3·§5-0).

    `reference_time`(ISO 8601)이 있으면 그 시각을 `end`로 앵커한다(plans/50 G1 — now 앵커
    금지). tz-aware면 그대로, naive면 서버 로컬 tz로 epoch를 계산한다.

    ``anchor_mode=nodename``(기본)은 ``metric{nodename="hostname"}`` — ``pool``·``source``를 쓰지
    않으며 종전과 바이트 동일하다. ``polestar_host``는 폴스타 앵커 사다리(plans/189)이며, 사다리
    ``up`` 판정은 ``reference_time``이 있으면 그 시각(창의 끝)으로 한다.
    """
    if not hostname or not str(hostname).strip():
        return _err("hostname이 비어 있음")
    anchored = is_anchor_mode(cfg)
    try:
        if anchored:
            name = validate_metric_name(metric)
            selector = ""
        else:
            selector = build_high_level_selector(metric, hostname)
        window_s = parse_duration_seconds(window)
        step_s = parse_duration_seconds(step)
        end = _reference_epoch(reference_time)
    except ValueError as e:
        return _err(str(e))
    start = end - window_s
    if anchored:
        result, _ = await _run_anchored(
            cfg, str(hostname).strip(), name, tool="prom_metric_range",
            endpoint=_EP_QUERY_RANGE,
            params={"start": f"{start:.3f}", "end": f"{end:.3f}", "step": str(step_s)},
            extra={"window": window, "step": step}, client=client, pool=pool, source=source,
            resolution=None, probe_time=end if reference_time is not None else None,
        )
        return result
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


def anchor_pool(ctx: Context, cfg: PrometheusConfig) -> Any:
    """앵커 모드일 때만 컨텍스트의 DB 풀(``pool_manager``)을 꺼낸다. nodename 모드는 None.

    nodename 모드는 컨텍스트를 더 읽지 않는다(종전 동작 그대로).
    """
    if not is_anchor_mode(cfg):
        return None
    return ctx.request_context.lifespan_context.get("pool_manager")


# ``_prom_get``의 URL 미설정 오류와 같은 문자열이어야 한다(테스트가 고정).
_URL_UNSET_ERROR = _err("PROMETHEUS_URL 미설정 — PromQL 조회 불가")
_OPENMETRICS_HINT_TOOL = "om_metric_instant"


def _with_openmetrics_hint(result: str) -> str:
    """결과가 정확히 URL 미설정 오류일 때만 ``"hint": "om_metric_instant"``를 더한다.

    plans/92 §4.8.2 [v3] ② — S0(Prometheus 없음)에서 LLM이 대체 도구를 결정적으로 알게 한다.
    그 밖의 결과는 그대로 돌려준다.
    """
    if result != _URL_UNSET_ERROR:
        return result
    payload = json.loads(result)
    payload["hint"] = _OPENMETRICS_HINT_TOOL
    return json.dumps(payload, ensure_ascii=False)


# =====================================================================
# 도구 등록
# =====================================================================


def register_promql_tools(
    mcp: FastMCP,
    expose_raw_promql: bool = False,
    openmetrics_hint: bool = False,
    source_ladder: bool = False,
) -> None:
    """PromQL 조사 도구를 MCP 서버에 등록한다.

    고수준(hostname 앵커) 2종은 항상 등록한다. 원시 패스스루 5종
    (instant/range/labels/metadata/series)은 ``expose_raw_promql=True``일 때만
    등록한다(execute_sql 기본 숨김 전례 — 계획 §3).

    Args:
        mcp: FastMCP 서버 인스턴스.
        expose_raw_promql: 원시 PromQL 패스스루 도구 노출 여부(기본 비노출).
        openmetrics_hint: True면 고수준 2종의 URL 미설정 오류에 OpenMetrics 대체 도구 힌트를
            더한다(OM 도구 노출과 함께 켠다). False면 결과 문자열이 종전과 바이트 동일하다.
            도구 시그니처·설명은 어느 쪽이든 같다(``tools/list`` 불변 — plans/92 I-6).
        source_ladder: True면 ``prom_metric_instant``를 여기서 등록하지 않는다 — 확장 시그니처
            (``source``·``cross_check``)를 ``metric_source.register_source_ladder_tools``가 대신
            등록한다(plans/92 §4.8.2 [v3] 분기 등록). False면 종전 그대로다.
    """

    if not source_ladder:
        _register_legacy_instant(mcp, openmetrics_hint)
    _register_range_and_raw(mcp, expose_raw_promql, openmetrics_hint)


def _register_legacy_instant(mcp: FastMCP, openmetrics_hint: bool) -> None:
    """종전 ``prom_metric_instant``(hostname·metric)를 등록한다 — 스키마·설명 불변(I-6)."""

    @mcp.tool()
    async def prom_metric_instant(
        hostname: str,
        metric: str,
        source: str | None = None,
        ctx: Context | None = None,
    ) -> str:
        """서버(hostname)의 메트릭 순간값을 조회한다(Prometheus instant).

        서버가 hostname에서 라벨 필터를 결정적으로 조립한다(§5-0 — 라벨을 직접 쓰지 말라).
        metric은 bare 메트릭 이름만 허용한다(임의 PromQL은 원시 도구 사용). 결과가 0건이면
        ``reason``·``hint``가 붙는다 — 0건을 「정상」으로 해석하지 말고 hint를 따르라.

        Args:
            hostname: 폴스타 등록 서버명 또는 OS 호스트명(받은 값 그대로).
            metric: bare 메트릭 이름(예: node_load1).
            source: 서버가 속한 폴스타 source(알람의 dbId 그대로 · 선택). 모르면 생략한다.
            ctx: MCP 컨텍스트.

        Returns:
            JSON 문자열 {data, queried_at, source_kind, ...} 또는 {error}.
        """
        cfg = _prom_config(ctx)
        result = await run_metric_instant(
            cfg, hostname, metric, pool=anchor_pool(ctx, cfg), source=source
        )
        return _with_openmetrics_hint(result) if openmetrics_hint else result


def _register_range_and_raw(
    mcp: FastMCP, expose_raw_promql: bool, openmetrics_hint: bool
) -> None:
    """``prom_metric_range``와 (옵트인) 원시 패스스루 5종을 등록한다."""

    @mcp.tool()
    async def prom_metric_range(
        hostname: str,
        metric: str,
        window: str = "1h",
        step: str = "60s",
        reference_time: str | None = None,
        source: str | None = None,
        ctx: Context | None = None,
    ) -> str:
        """서버(hostname)의 메트릭 시계열(window)을 조회한다(Prometheus range).

        서버가 hostname에서 라벨 필터를 결정적으로 조립한다(§5-0 — 라벨을 직접 쓰지 말라).
        metric은 bare 메트릭 이름만 허용한다. 결과가 0건이면 ``reason``·``hint``가 붙는다 —
        0건을 「정상」으로 해석하지 말고 hint를 따르라.

        Args:
            hostname: 폴스타 등록 서버명 또는 OS 호스트명(받은 값 그대로).
            metric: bare 메트릭 이름(예: node_cpu_seconds_total).
            window: 조회 창(예: 1h, 30m, 15s). 기본 1h.
            step: 해상도 스텝(예: 60s, 15s). 기본 60s.
            reference_time: 사건 기준시각(ISO 8601). 지정하면 now 대신 이 시각이 창의 끝이다.
            source: 서버가 속한 폴스타 source(알람의 dbId 그대로 · 선택). 모르면 생략한다.
            ctx: MCP 컨텍스트.

        Returns:
            JSON 문자열 {data, queried_at, source_kind, ...} 또는 {error}.
        """
        cfg = _prom_config(ctx)
        result = await run_metric_range(
            cfg, hostname, metric, window, step, reference_time=reference_time,
            pool=anchor_pool(ctx, cfg), source=source,
        )
        return _with_openmetrics_hint(result) if openmetrics_hint else result

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
