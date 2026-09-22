"""벤더 중립 OpenMetrics 노출 기계 (plans/92 §4.5 [v3] · §0.0.4 — plans/87 J7 공유).

원천이 Prometheus 형식이 아닌 데이터(SQL·벤더 API)를 ``custom_route`` 엔드포인트로 내보내는
브리지들이 **함께 쓰는** 부품만 둔다. 원천별 SQL·패밀리 정의는 각 브리지 모듈의 몫이고,
이 모듈에는 벤더·스키마 어휘가 없다(overfit 스캔 대상).

부품:
    - 패밀리 생성 헬퍼 — ``gauge_family``·``info_family``·``flag_gauge``. 표현은
      ``prometheus_client`` core 패밀리를 그대로 쓴다(인코더가 직접 소비하므로 변환 층이 없고,
      info 패밀리의 형식별 렌더 차이 — 1.0 ``info`` / 0.0.4 ``_info`` gauge — 를 인코더가 맡는다).
    - ``render_exposition`` — 요청마다 새 ``CollectorRegistry`` + 1회용 collector로 직렬화하고
      ``Accept``로 OpenMetrics 1.0 / text 0.0.4를 협상한다. 전역 ``REGISTRY``는 쓰지 않는다.
    - ``ExpositionCache`` — 수집 결과 TTL 캐시 + single-flight(동시 스크레이프에도 수집 1회).
    - ``make_exposition_endpoint`` — Starlette ``Request → Response`` 핸들러 팩토리.
    - ``register_shutdown``·``install_shutdown_hooks`` — 브리지 전용 자원(지연 DB 풀 등)을
      ASGI 앱 종료 시 정리하는 배선.

불변식 — **샘플에 명시 타임스탬프를 달지 않는다**(§4.5 [v3] 3). 명시 타임스탬프는 Prometheus
staleness 처리를 받지 못하고, 헤드보다 오래되면 out-of-bounds로 거부될 수 있다. 원천 값이 몇 시
기준인지는 별도 gauge(예: ``<ns>_…_timestamp_seconds``)로 낸다. ``render_exposition``이 위반을
``ValueError``로 막는다.
"""

from __future__ import annotations

import asyncio
import logging
import time
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

from prometheus_client.core import GaugeMetricFamily, InfoMetricFamily, Metric
from prometheus_client.exposition import choose_encoder
from prometheus_client.registry import CollectorRegistry
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

logger = logging.getLogger(__name__)

#: 수집 함수 — 노출할 패밀리 목록을 돌려준다. 부분 실패는 예외가 아니라 상태 gauge로 표현하고,
#: 수집 자체가 불가능할 때만 예외를 올린다(엔드포인트가 503으로 드러낸다).
Collect = Callable[[], Awaitable[list[Metric]]]
#: 종료 정리 함수(인자 없는 코루틴 함수).
Closer = Callable[[], Awaitable[None]]


# =====================================================================
# 패밀리 생성 헬퍼 — 타임스탬프 인자를 아예 받지 않는다
# =====================================================================


def gauge_family(
    name: str,
    documentation: str,
    label_names: Sequence[str],
    samples: Iterable[tuple[Sequence[str], float]],
    *,
    unit: str = "",
) -> GaugeMetricFamily:
    """gauge 패밀리를 만든다.

    Args:
        name: 패밀리 이름. ``unit``을 주면 이름이 ``_<unit>``으로 끝나야 한다(OpenMetrics 규칙).
        documentation: HELP 문구.
        label_names: 라벨 이름 순서.
        samples: ``(라벨 값 순서열, 값)`` 목록. 라벨 값 개수는 ``label_names``와 같아야 한다.
        unit: OpenMetrics ``# UNIT`` 메타(선택). text 0.0.4에서는 렌더되지 않는다.

    Returns:
        샘플이 채워진 ``GaugeMetricFamily`` (샘플 타임스탬프 없음).
    """
    family = GaugeMetricFamily(name, documentation, labels=list(label_names), unit=unit)
    for label_values, value in samples:
        family.add_metric([str(v) for v in label_values], float(value))
    return family


def info_family(
    name: str,
    documentation: str,
    label_names: Sequence[str],
    rows: Iterable[Sequence[str]],
) -> InfoMetricFamily:
    """info 패밀리를 만든다(샘플 값은 항상 1).

    OpenMetrics 1.0에서는 ``# TYPE <name> info`` + 샘플 ``<name>_info{…} 1.0``으로,
    text 0.0.4에서는 ``# TYPE <name>_info gauge``로 렌더된다(인코더 담당).

    Args:
        name: 패밀리 이름(``_info`` 접미사 없이).
        documentation: HELP 문구.
        label_names: 라벨 이름 순서.
        rows: 라벨 값 순서열 목록.

    Returns:
        샘플이 채워진 ``InfoMetricFamily`` (샘플 타임스탬프 없음).
    """
    family = InfoMetricFamily(name, documentation, labels=list(label_names))
    for label_values in rows:
        family.add_metric([str(v) for v in label_values], {})
    return family


def flag_gauge(
    name: str,
    documentation: str,
    label_name: str,
    flags: Mapping[str, bool],
) -> GaugeMetricFamily:
    """라벨 값별 0/1 상태 gauge를 만든다 — 절단·원천 가용 같은 브리지 상태 표시용.

    브리지는 상한에 걸린 결과를 **잘라서 조용히 내지 않고** 이 gauge로 알린다
    (예: ``<ns>_bridge_truncated{<label>}`` 1). 원천 조회 실패도 빼지 않고
    ``<ns>_bridge_source_up{<label>}`` 0으로 알린다(침묵 폴백 금지).

    Args:
        name: 패밀리 이름.
        documentation: HELP 문구.
        label_name: 원천을 구분하는 라벨 이름 1개.
        flags: ``{라벨 값: 참/거짓}`` — 참이면 1, 거짓이면 0. 입력 순서대로 샘플을 낸다.

    Returns:
        ``GaugeMetricFamily``.
    """
    return gauge_family(
        name,
        documentation,
        [label_name],
        (([key], 1.0 if flag else 0.0) for key, flag in flags.items()),
    )


# =====================================================================
# 직렬화 · Accept 협상
# =====================================================================


class _OneShotCollector:
    """이미 만들어진 패밀리 목록을 1회 돌려주는 collector(요청 스코프 레지스트리 전용)."""

    def __init__(self, families: Sequence[Metric]) -> None:
        self._families = list(families)

    def collect(self) -> Iterable[Metric]:
        """등록된 패밀리를 그대로 돌려준다."""
        return iter(self._families)


def _reject_explicit_timestamps(families: Sequence[Metric]) -> None:
    """샘플에 명시 타임스탬프가 있으면 ``ValueError``를 올린다(모듈 불변식)."""
    for family in families:
        for sample in family.samples:
            if sample.timestamp is not None:
                raise ValueError(
                    f"명시 타임스탬프 금지 — {sample.name}: 브리지 샘플은 타임스탬프 없이 노출한다 "
                    "(staleness 미적용·out-of-bounds 거부 위험)"
                )


def render_exposition(
    families: Sequence[Metric], accept_header: str | None
) -> tuple[bytes, str]:
    """패밀리 목록을 협상된 노출 형식으로 직렬화한다.

    요청마다 새 ``CollectorRegistry``에 1회용 collector를 등록해 렌더한다 — 프로세스 전역
    ``REGISTRY``와 섞이지 않는다. 형식은 ``prometheus_client.exposition.choose_encoder``가
    ``Accept``로 고른다: ``application/openmetrics-text``(version ≥ 1.0.0)면 OpenMetrics 1.0
    (``# EOF`` 종결), 그 외(빈 값·``*/*`` 포함)는 text 0.0.4.

    Args:
        families: 노출할 패밀리 목록.
        accept_header: 요청 ``Accept`` 헤더(없으면 None 또는 빈 문자열).

    Returns:
        ``(본문 바이트, Content-Type)``.

    Raises:
        ValueError: 어느 샘플이든 명시 타임스탬프를 가진 경우.
    """
    _reject_explicit_timestamps(families)
    registry = CollectorRegistry(auto_describe=False)
    registry.register(_OneShotCollector(families))
    encoder, content_type = choose_encoder(accept_header or "")
    return encoder(registry), content_type


# =====================================================================
# 응답 캐시 — TTL + single-flight
# =====================================================================


class ExpositionCache:
    """수집 결과(패밀리 목록)를 TTL 동안 재사용한다 — 원천 부하 가드.

    - **single-flight**: 수집은 ``asyncio.Lock`` 안에서만 돈다. 동시 스크레이프가 와도 수집 함수는
      1회 호출되고, 기다리던 요청은 방금 채워진 캐시를 받는다.
    - **실패**: 수집 함수의 예외는 삼키지 않고 호출자에게 전파한다. 실패 결과는 캐시하지 않으므로
      다음 요청이 다시 수집한다. 원천 일부 실패는 수집 함수가 상태 gauge로 표현하는 것이 계약이다.
    - **시계 주입**: ``clock``(기본 ``time.monotonic``)을 바꿔 TTL을 결정적으로 시험한다.

    Args:
        collect: 수집 코루틴 함수.
        ttl_seconds: 캐시 유효 시간(초). 0 이하면 매 요청 수집(single-flight는 유지).
        clock: 단조 시계 함수.
    """

    def __init__(
        self,
        collect: Collect,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._collect = collect
        self._ttl = float(ttl_seconds)
        self._clock = clock
        self._lock = asyncio.Lock()
        self._families: list[Metric] | None = None
        self._expires_at = 0.0

    async def get(self) -> list[Metric]:
        """캐시가 유효하면 그대로, 아니면 1회 수집해 채운 뒤 돌려준다.

        Raises:
            Exception: 수집 함수가 올린 예외(그대로 전파).
        """
        async with self._lock:
            if self._families is not None and self._clock() < self._expires_at:
                return self._families
            families = await self._collect()
            self._families = families
            self._expires_at = self._clock() + self._ttl
            return families


# =====================================================================
# Starlette 핸들러
# =====================================================================


def make_exposition_endpoint(
    cache: ExpositionCache,
) -> Callable[[Request], Awaitable[Response]]:
    """``custom_route``에 붙일 노출 핸들러를 만든다.

    정상: 200 + 협상된 Content-Type. 수집 불가(수집 함수 예외): 503 + 짧은 사유 — Prometheus에서는
    ``up`` 0으로 드러난다(침묵 폴백 금지). 예외 상세는 서버 로그에만 남긴다.

    Args:
        cache: 수집·캐시를 맡는 ``ExpositionCache``.

    Returns:
        ``async (Request) -> Response`` 핸들러.
    """

    async def endpoint(request: Request) -> Response:
        try:
            families = await cache.get()
        except Exception as e:
            logger.exception("OpenMetrics 노출 수집 실패 (%s): %s", request.url.path, e)
            return PlainTextResponse(
                f"exposition collect failed: {type(e).__name__}\n", status_code=503
            )
        body, content_type = render_exposition(families, request.headers.get("accept"))
        return Response(content=body, media_type=content_type)

    return endpoint


# =====================================================================
# 종료 정리 배선
# =====================================================================

# 소유자(FastMCP 인스턴스 등) → 종료 정리 함수 목록. 소유자가 사라지면 항목도 사라진다.
_SHUTDOWN_HOOKS: weakref.WeakKeyDictionary[Any, list[Closer]] = weakref.WeakKeyDictionary()


def register_shutdown(owner: Any, closer: Closer) -> None:
    """``owner``로 조립될 ASGI 앱이 종료될 때 부를 정리 함수를 등록한다.

    브리지 전용 자원(첫 스크레이프 때 여는 DB 풀 등)은 세션 lifespan 밖에 있으므로 앱 lifespan
    종료 시 따로 닫아야 한다. 실제 배선은 ``install_shutdown_hooks``가 한다.

    Args:
        owner: 앱을 만드는 서버 객체(약한 참조 키).
        closer: 인자 없는 정리 코루틴 함수.
    """
    _SHUTDOWN_HOOKS.setdefault(owner, []).append(closer)


def install_shutdown_hooks(app: Any, owner: Any) -> None:
    """``owner``에 등록된 정리 함수를 Starlette 앱 lifespan 종료에 건다.

    starlette 1.x에는 ``on_shutdown``·``add_event_handler``가 없다. 그래서 라우터의
    ``lifespan_context``를 감싸 기존 lifespan을 그대로 연 뒤, 종료 시 정리 함수를 등록 순서대로
    부른다(한 함수의 실패가 다른 정리를 막지 않는다 — 경고 로그). 등록된 함수가 없으면 앱을
    건드리지 않는다(비트 동일).

    Args:
        app: Starlette 앱(``router.lifespan_context`` 보유).
        owner: ``register_shutdown``에 쓴 소유자.
    """
    closers = list(_SHUTDOWN_HOOKS.get(owner, ()))
    if not closers:
        return
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan_with_shutdown(app_: Any) -> AsyncIterator[Any]:
        async with inner(app_) as state:
            try:
                yield state
            finally:
                for close in closers:
                    try:
                        await close()
                    except Exception as e:
                        logger.warning("노출 자원 종료 정리 실패: %s", e)

    app.router.lifespan_context = lifespan_with_shutdown
