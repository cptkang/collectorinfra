"""노출 형식(OpenMetrics 1.0 · Prometheus text 0.0.4) 파서·정규화 (plans/92 §4.2 · D-210).

트랙 A(exporter 직접 스크레이프)의 파싱·정규화 계층이다(순수 함수). HTTP·설정·MCP 도구 등록은 여기에
두지 않는다(도구 계층이 이 모듈을 부른다). 이 모듈은 ``mcp`` 패키지를 임포트하지 않는다 —
파서 테스트가 ``mcp`` 부재 skip으로 무력화되지 않게 하기 위해서다. 벤더 중립 모듈이라 특정
제품의 스키마 어휘를 쓰지 않는다(overfit 게이트 스캔 대상).

- ``parse_exposition``: 응답 Content-Type으로 파서를 고른다(OpenMetrics 1.0 strict / 0.0.4 관대).
  파서는 ``prometheus_client``에 위임하고, 예외는 ``ExpositionParseError``로 감싸 사유를 보존한다.
- ``to_instant_vector``: Prometheus ``/api/v1/query`` instant vector와 같은 모양으로 바꾼다.
  ``nodename``은 서버가 주입한다. 이미 있던 ``nodename``은 Prometheus ``honor_labels=false``와
  같게 ``exported_nodename``으로 옮긴다(plans/92 I-8 — 덮어쓰기 금지).
- ``to_catalog``: 패밀리 목록(name·type·unit·help·series).
- ``target_identity``: ``node_uname_info``의 OS 호스트명으로 스크레이프 타깃 신원을 판정한다
  (§4.2 [v3]).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from prometheus_client.metrics_core import Metric
from prometheus_client.openmetrics.parser import (
    text_string_to_metric_families as _parse_openmetrics,
)
from prometheus_client.parser import text_string_to_metric_families as _parse_text_004
from prometheus_client.samples import Sample

#: 스크레이프 요청 Accept 헤더 — Prometheus 서버가 보내는 값과 같다(plans/92 §2.2).
#: exporter 입장에서 우리 스크레이퍼가 Prometheus와 구별되지 않게 한다.
PROM_SCRAPE_ACCEPT: str = (
    "application/openmetrics-text;version=1.0.0,"
    "application/openmetrics-text;version=0.0.1;q=0.75,"
    "text/plain;version=0.0.4;q=0.5,"
    "*/*;q=0.1"
)

_OPENMETRICS_MEDIA_TYPE = "application/openmetrics-text"

# bare 메트릭 이름 — PromQL 도구의 ``_METRIC_NAME_RE``와 같은 패턴이다. 그 모듈은 ``mcp``를
# 임포트하므로 여기서 가져오지 않고 따로 정의한다. 두 패턴이 같음은 테스트가 고정한다.
_METRIC_NAME_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")

# 0.0.4 원문에서 counter로 선언된 이름을 찾는다(따옴표 UTF-8 이름 포함).
_TYPE_COUNTER_RE = re.compile(
    r'^[ \t]*#[ \t]+TYPE[ \t]+("(?:[^"\\]|\\.)*"|\S+)[ \t]+counter[ \t]*$',
    re.MULTILINE,
)
_NAME_ESCAPE_RE = re.compile(r"\\(.)")

# OpenMetrics에서 ``<family>_created`` 샘플을 싣는 타입(생성 시각 — 소비자에게는 노이즈).
_CREATED_TYPES = frozenset({"counter", "histogram", "summary"})

_NODENAME = "nodename"
_EXPORTED_PREFIX = "exported_"
_UNAME_SAMPLE = "node_uname_info"


class ExpositionParseError(ValueError):
    """노출 형식 파싱 실패. 메시지에 형식과 원 예외 사유를 담는다(침묵 폴백 금지 — plans/92 I-3)."""


@dataclass
class InstantVectorResult:
    """``to_instant_vector`` 반환값.

    Attributes:
        data: ``{"resultType": "vector", "result": [...]}`` — Prometheus instant 응답의
            ``data``와 같은 모양.
        truncated: ``max_series``로 잘랐으면 True(침묵 절단 금지).
        series_total: 필터 뒤·절단 전 시리즈 수.
        types: 반환한 시리즈의 ``__name__`` → 패밀리 type.
        uname_nodename: ``node_uname_info``의 원래 ``nodename`` 라벨 값(필터와 무관). 없으면 None.
    """

    data: dict[str, Any]
    truncated: bool
    series_total: int
    types: dict[str, str] = field(default_factory=dict)
    uname_nodename: str | None = None


# =====================================================================
# 파싱
# =====================================================================


def _is_openmetrics(content_type: str) -> bool:
    """Content-Type의 미디어 타입이 OpenMetrics인지 본다(대소문자·파라미터 무시)."""
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    return media_type == _OPENMETRICS_MEDIA_TYPE


def _unquote_name(raw: str) -> str:
    """``# TYPE`` 줄의 이름 토큰에서 따옴표와 이스케이프를 벗긴다."""
    if len(raw) >= 2 and raw.startswith('"') and raw.endswith('"'):
        return _NAME_ESCAPE_RE.sub(
            lambda m: "\n" if m.group(1) == "n" else m.group(1), raw[1:-1]
        )
    return raw


def _counter_names_without_total(text: str) -> set[str]:
    """0.0.4 원문에서 ``_total`` 없이 counter로 선언된 이름을 모은다."""
    names: set[str] = set()
    for m in _TYPE_COUNTER_RE.finditer(text):
        name = _unquote_name(m.group(1))
        if not name.endswith("_total"):
            names.add(name)
    return names


def _restore_counter_sample_names(families: list[Metric], text: str) -> None:
    """0.0.4 파서가 붙인 counter 샘플명 ``_total``을 떼어 노출 이름으로 되돌린다.

    ``prometheus_client`` 0.0.4 파서는 ``# TYPE c counter`` + ``c 5``를 샘플명 ``c_total``로
    바꿔 돌려준다. Prometheus는 ``c`` 그대로 저장하므로 되돌려야 ``__name__``이 같아진다.
    같은 텍스트에 ``c``와 ``c_total``이 둘 다 counter로 선언된 경우(비정상 노출)는 구별하지 않는다.
    """
    plain = _counter_names_without_total(text)
    if not plain:
        return
    for fam in families:
        if fam.type != "counter" or fam.name not in plain:
            continue
        munged = fam.name + "_total"
        fam.samples = [
            s._replace(name=fam.name) if s.name == munged else s for s in fam.samples
        ]


def parse_exposition(text: str, content_type: str) -> list[Metric]:
    """노출 텍스트를 메트릭 패밀리 목록으로 파싱한다.

    Content-Type이 ``application/openmetrics-text``면 OpenMetrics 1.0 strict 파서(``# EOF`` 필수),
    그 외(``text/plain; version=0.0.4``·빈 값 포함)는 0.0.4 관대 파서를 쓴다.
    파싱 전에 ``\\r\\n``을 ``\\n``으로 바꾼다. OpenMetrics 스펙은 LF만 허용하지만, Windows 체크아웃
    픽스처와 CRLF를 내는 일부 exporter를 받기 위해 스펙 이탈을 허용한다.

    Args:
        text: 응답 본문(디코드된 문자열).
        content_type: 응답 ``Content-Type`` 헤더 값.

    Returns:
        ``prometheus_client`` ``Metric`` 목록(노출 순서). 타임스탬프는 초 단위다
        (0.0.4의 밀리초는 파서가 초로 바꾼다).

    Raises:
        ExpositionParseError: 파서가 거부했을 때(``# EOF`` 부재·문법 오류 등).
            원 예외는 ``__cause__``에 남는다.
    """
    normalized = text.replace("\r\n", "\n")
    is_om = _is_openmetrics(content_type)
    label = "OpenMetrics 1.0" if is_om else "Prometheus text 0.0.4"
    parser = _parse_openmetrics if is_om else _parse_text_004
    try:
        families = list(parser(normalized))
    except Exception as exc:  # 신뢰할 수 없는 입력의 파서 경계 — 어떤 예외든 사유를 보존해 감싼다
        raise ExpositionParseError(
            f"{label} 파싱 실패({type(exc).__name__}): {exc}"
        ) from exc
    if not is_om:
        _restore_counter_sample_names(families, normalized)
    return families


# =====================================================================
# 정규화 — 값·호스트명
# =====================================================================


def normalize_hostname(name: str) -> str:
    """호스트명 비교용 정규화 — 소문자화하고 첫 ``.`` 뒤(도메인)를 버린다(FQDN ↔ 단축명).

    타깃 신원 확인(§4.2 [v3])과 교차 검증 ``label_mismatch``(§4.8.5)가 함께 쓰는 단일 규칙이다.
    """
    return name.strip().lower().partition(".")[0].strip()


def format_sample_value(v: float) -> str:
    """샘플 값을 Prometheus JSON API의 값 문자열 표기로 바꾼다.

    NaN → ``"NaN"``, +Inf → ``"+Inf"``, -Inf → ``"-Inf"``. 유한값은 지수 없는 최단 표기다
    (``97.5`` → ``"97.5"``, ``8589934592.0`` → ``"8589934592"``, ``1e-05`` → ``"0.00001"``).
    최단 왕복 자릿수(``repr``)를 소수 표기로 펼치므로 2^53을 넘는 정수도 Go
    ``strconv.FormatFloat(v, 'f', -1, 64)``와 같은 문자열이 된다.
    """
    f = float(v)
    if math.isnan(f):
        return "NaN"
    if math.isinf(f):
        return "+Inf" if f > 0 else "-Inf"
    text = format(Decimal(repr(f)), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


# =====================================================================
# instant vector · 카탈로그
# =====================================================================


def validate_metric_name(arg: str, value: str) -> str:
    """``metric``·``prefix`` 인자가 bare 메트릭 이름 형식인지 검증하고 공백을 벗겨 돌려준다.

    Raises:
        ValueError: 형식이 아닐 때. 메시지에 인자 이름(``arg``)과 패턴을 담는다.
    """
    name = str(value).strip()
    if not _METRIC_NAME_RE.match(name):
        raise ValueError(
            f"{arg}는 bare 메트릭 이름 형식만 허용: {value!r} (패턴 {_METRIC_NAME_RE.pattern})"
        )
    return name


def validate_filters(
    metric: str | None, prefix: str | None
) -> tuple[str | None, str | None]:
    """``to_instant_vector`` 필터 인자를 검증한다 — 도구가 스크레이프 **전에** 부른다(HTTP 0회).

    Raises:
        ValueError: 둘 다 없거나 이름 형식이 아닐 때.
    """
    if metric is None and prefix is None:
        raise ValueError("metric 또는 prefix 중 하나는 필요하다")
    return (
        validate_metric_name("metric", metric) if metric is not None else None,
        validate_metric_name("prefix", prefix) if prefix is not None else None,
    )


def _is_created_sample(family: Metric, sample: Sample) -> bool:
    """OpenMetrics counter·histogram·summary의 ``<family>_created`` 샘플인지 본다.

    이름이 우연히 ``_created``로 끝나는 gauge 패밀리는 여기에 걸리지 않는다.
    """
    return family.type in _CREATED_TYPES and sample.name == family.name + "_created"


def _series_labels(sample: Sample, hostname: str) -> dict[str, str]:
    """``__name__``·원 라벨·주입 ``nodename`` 순으로 시리즈 라벨을 만든다.

    원 라벨에 비어 있지 않은 ``nodename``이 있으면 Prometheus ``honor_labels=false``와 같게
    ``exported_nodename``으로 옮긴다. 그 이름도 차 있으면 ``exported_`` 접두를 빈 이름이
    나올 때까지 반복한다(Prometheus와 같이 빈 값은 없는 라벨로 본다).
    """
    labels: dict[str, str] = {"__name__": sample.name}
    labels.update((k, v) for k, v in sample.labels.items() if k != _NODENAME)
    existing = sample.labels.get(_NODENAME)
    if existing:
        moved = _NODENAME
        while True:
            moved = _EXPORTED_PREFIX + moved
            if not labels.get(moved):
                labels[moved] = existing
                break
    labels[_NODENAME] = hostname
    return labels


def find_uname_nodename(families: Iterable[Metric]) -> str | None:
    """전체 패밀리에서 ``node_uname_info``의 원래 ``nodename`` 라벨 값을 찾는다(없으면 None)."""
    for fam in families:
        for sample in fam.samples:
            if sample.name == _UNAME_SAMPLE and sample.labels.get(_NODENAME):
                return sample.labels[_NODENAME]
    return None


def to_instant_vector(
    families: Iterable[Metric],
    *,
    hostname: str,
    scraped_at: float,
    metric: str | None = None,
    prefix: str | None = None,
    max_series: int,
) -> InstantVectorResult:
    """파싱한 패밀리를 Prometheus instant vector 모양으로 바꾼다(plans/92 §4.2).

    결과 원소는 ``{"metric": {"__name__": 샘플명, ...라벨, "nodename": hostname},
    "value": [ts, "값"]}``이다. 샘플 타임스탬프가 없으면 ``scraped_at``(초)을 쓴다.
    counter·histogram·summary의 ``_created`` 샘플은 뺀다. info·stateset은 값 그대로 둔다.

    필터(둘 다 주면 AND):
        - ``metric``: 샘플 ``__name__`` 정확 일치 또는 패밀리 이름 일치
          (histogram 패밀리명이면 ``_bucket``·``_sum``·``_count`` 전부).
        - ``prefix``: 샘플명 접두 일치.

    Args:
        families: ``parse_exposition`` 결과.
        hostname: 주입할 ``nodename`` 값(도구 인자 — server_name).
        scraped_at: 스크레이프 시각(epoch 초).
        metric: bare 메트릭 이름.
        prefix: bare 메트릭 이름 접두.
        max_series: 반환 시리즈 상한(1 이상). 넘치면 노출 순서 앞에서 자르고 ``truncated=True``.

    Raises:
        ValueError: ``metric``·``prefix``가 둘 다 없거나, 이름 형식이 아니거나, ``max_series`` < 1.
    """
    metric_name, prefix_name = validate_filters(metric, prefix)
    if max_series < 1:
        raise ValueError(f"max_series는 1 이상이어야 한다: {max_series}")

    fams = list(families)
    matched: list[tuple[Sample, str]] = []
    for fam in fams:
        family_hit = metric_name is not None and fam.name == metric_name
        for sample in fam.samples:
            if _is_created_sample(fam, sample):
                continue
            if metric_name is not None and not (family_hit or sample.name == metric_name):
                continue
            if prefix_name is not None and not sample.name.startswith(prefix_name):
                continue
            matched.append((sample, fam.type))

    kept = matched[:max_series]
    result = [
        {
            "metric": _series_labels(sample, hostname),
            "value": [
                float(sample.timestamp) if sample.timestamp is not None else float(scraped_at),
                format_sample_value(sample.value),
            ],
        }
        for sample, _ in kept
    ]
    return InstantVectorResult(
        data={"resultType": "vector", "result": result},
        truncated=len(matched) > max_series,
        series_total=len(matched),
        types={sample.name: typ for sample, typ in kept},
        uname_nodename=find_uname_nodename(fams),
    )


def to_catalog(
    families: Iterable[Metric], *, prefix: str | None = None
) -> list[dict[str, Any]]:
    """패밀리 목록을 카탈로그 행으로 바꾼다 — ``{"name", "type", "unit", "help", "series"}``.

    ``series``는 ``_created`` 샘플을 뺀 샘플 수다. ``prefix``를 주면 패밀리 이름이나 샘플명 중
    하나라도 그 접두로 시작하는 패밀리만 남긴다(``to_instant_vector(prefix=)``가 내는 시리즈의
    패밀리는 빠짐없이 나온다). 이름순으로 정렬한다.

    Raises:
        ValueError: ``prefix``가 bare 메트릭 이름 형식이 아닐 때.
    """
    prefix_name = validate_metric_name("prefix", prefix) if prefix is not None else None
    rows: list[dict[str, Any]] = []
    for fam in families:
        samples = [s for s in fam.samples if not _is_created_sample(fam, s)]
        if prefix_name is not None and not (
            fam.name.startswith(prefix_name)
            or any(s.name.startswith(prefix_name) for s in samples)
        ):
            continue
        rows.append(
            {
                "name": fam.name,
                "type": fam.type,
                "unit": fam.unit,
                "help": fam.documentation,
                "series": len(samples),
            }
        )
    rows.sort(key=lambda row: row["name"])
    return rows


# =====================================================================
# 타깃 신원
# =====================================================================


def target_identity(
    uname_nodename: str | None, *, server_name: str, os_hostname: str = ""
) -> str:
    """스크레이프 응답의 OS 호스트명으로 타깃 신원을 판정한다(plans/92 §4.2 [v3]).

    ``uname_nodename``은 OS 호스트명이고 ``server_name``(도구 인자)과 다를 수 있다. 그래서
    허용목록의 ``os_hostname``이 있으면 그것을 기준으로 삼는다. 비교는
    ``normalize_hostname``으로 한다.

    Returns:
        - ``"unknown"``: ``node_uname_info``가 없다(``uname_nodename`` 없음).
        - ``"match"``: ``os_hostname``과 일치, 또는 ``os_hostname``이 없고 ``server_name``과 일치.
        - ``"mismatch"``: ``os_hostname``이 있는데 불일치 — 허용목록 오등록(URL이 다른 호스트).
        - ``"unverified"``: ``os_hostname``이 없고 ``server_name``과도 불일치.
    """
    observed = normalize_hostname(uname_nodename) if uname_nodename else ""
    if not observed:
        return "unknown"
    if os_hostname.strip():
        return "match" if observed == normalize_hostname(os_hostname) else "mismatch"
    return "match" if observed == normalize_hostname(server_name) else "unverified"
