"""본체 자기 관측 메트릭 — HTTP RED (plans/92 트랙 B-1 · O4).

메트릭은 pip 패키지 `prometheus_client`의 **프로세스 전역 `REGISTRY`**에 정의한다. 노이즈 게이트는
자기 infrastructure 모듈(`noise_gate/infrastructure/metrics.py`)에서 같은 전역 레지스트리에
카운터를 정의하므로 `src` ↔ `noise_gate` 사이 import는 0이다(D-139). 노출은
`src/api/routes/metrics.py`가 `render_latest`로 하며, 그 라우트는 `OBS_METRICS_ENDPOINT_ENABLED`가
켜졌을 때만 등록된다 — 꺼져 있으면 여기 쌓인 값은 프로세스 밖으로 나가지 않는다.

정의는 모듈 최상위에서 한 번만 한다. 앱을 여러 번 조립해도(테스트) 모듈은 한 번만 임포트되므로
같은 이름이 레지스트리에 중복 등록되지 않는다.

라벨 통제(plans/92 §4.4): `route`는 경로 템플릿, `method`는 표준 메서드(밖은 `other`), `status`는
응답 코드다. 원 URL·query·hostname·thread_id·사용자 식별자는 라벨로 쓰지 않는다 — 라벨 값은
시계열 수를 곱으로 늘리고, 원 경로에는 식별자가 섞인다(PII·카디널리티).

본체는 단일 프로세스로 뜬다(`src/main.py` uvicorn workers 미지정 — plans/92 F-9). 그래서
multiprocess 모드(`PROMETHEUS_MULTIPROC_DIR`)는 쓰지 않는다.
"""

from __future__ import annotations

from prometheus_client import REGISTRY, Counter, Histogram
from prometheus_client.exposition import choose_encoder

#: 표준 HTTP 메서드. 메서드 문자열은 클라이언트가 임의로 보낼 수 있으므로 밖의 값은 하나로 묶는다.
_KNOWN_METHODS: frozenset[str] = frozenset(
    {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
)
#: 표준 메서드 밖의 값을 묶는 라벨 값.
OTHER_METHOD = "other"

#: 응답시간 목표(단순 <10s · 복합 <30s · 문서 생성 <60s — CLAUDE.md)가 구간 경계에 걸리도록 잡았다.
_DURATION_BUCKETS: tuple[float, ...] = (
    0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0,
)

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests",
    "처리한 HTTP 요청 수(라우트 템플릿·메서드·응답 코드별).",
    ("route", "method", "status"),
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP 요청 처리 시간(초) — 스트리밍 응답은 본문 전송 완료까지.",
    ("route", "method"),
    buckets=_DURATION_BUCKETS,
)


def observe_http_request(route: str, method: str, status: int, seconds: float) -> None:
    """요청 1건의 수·지연을 기록한다.

    Args:
        route: 경로 템플릿(미매칭은 호출자가 `unmatched`로 넘긴다)
        method: HTTP 메서드(표준 밖이면 `other`로 묶는다)
        status: 응답 상태 코드
        seconds: 처리 시간(초)
    """
    method_label = method.upper() if method.upper() in _KNOWN_METHODS else OTHER_METHOD
    HTTP_REQUESTS_TOTAL.labels(route=route, method=method_label, status=str(status)).inc()
    HTTP_REQUEST_DURATION_SECONDS.labels(route=route, method=method_label).observe(seconds)


def render_latest(accept_header: str | None) -> tuple[bytes, str]:
    """전역 레지스트리를 `Accept`에 맞는 노출 형식으로 직렬화한다.

    `application/openmetrics-text`를 받으면 OpenMetrics 1.0(`# EOF` 종결), 그 외(`*/*`·빈 값
    포함)는 text 0.0.4다 — 판정은 `prometheus_client.exposition.choose_encoder`가 한다.

    Args:
        accept_header: 요청의 `Accept` 헤더 값(없으면 None)

    Returns:
        (본문 바이트, Content-Type)
    """
    encoder, content_type = choose_encoder(accept_header or "")
    return encoder(REGISTRY), content_type
