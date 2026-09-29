"""제니퍼 Open API 허용목록 **정본** (plans/87 §5.2(e) · §8.1 · D-195 ① 정정 부기).

1차 통제 = **메서드 + 경로 템플릿 정확 일치 + 경로별 쿼리 키** — 그 밖은 전부 네트워크 호출 전에
거부한다. 같은 토큰에 쓰기·제어 API와 민감 GET(`/api/auth/userlist` 등)이 함께 열려 있고 서버가 막아
주지 않는다(§0.10 #9·#14·#15 실측). 그래서:

- GET만 통과한다(v1 조회 API의 POST 변형도 거부).
- 경로는 템플릿과 **정확히** 일치해야 한다 — 와일드카드 없음 · `.xml` 변형 없음 · `..`·`//`·`%`·`\\`
  거부.
- 쿼리 키는 경로별 허용 키만 · `token` 키는 항상 거부(URL·접근 로그에 토큰이 남는다) · 필수 키 누락
  거부.
- 목록은 코드 상수다. 설정으로 넓히지 않는다.

`apm_gateway/testdata/jennifer/scripts/jennifer_catalog.py`는 J0 도구용 **사본**이다. 두 목록이
같은지 `tests/test_allowlist.py`가 대조한다(한쪽만 바뀌면 실패).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

ACCEPT_JSON = "application/json"
ACCEPT_TEXT = "text/plain"


class NotAllowedError(ValueError):
    """허용목록 밖 요청 — 네트워크 호출 전에 올린다."""


@dataclass(frozen=True)
class Endpoint:
    template: str
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    accept: str = ACCEPT_JSON

    @property
    def query_keys(self) -> frozenset[str]:
        return frozenset(self.required) | frozenset(self.optional)


_RANGE = ("domain_id", "start_time", "end_time")
_TX = ("domain_id", "txid", "time")

ALLOWED: dict[str, Endpoint] = {
    e.template: e
    for e in (
        Endpoint("/api/domain"),
        Endpoint("/api/instance", ("domain_id",)),
        Endpoint("/api/realtime/instance", ("domain_id",), ("instance_id",)),
        Endpoint(
            "/api/dbmetrics/instance",
            ("domain_id", "instance_id", "interval_minute", "metrics", "start_time", "end_time"),
        ),
        Endpoint("/api/metrics"),
        Endpoint("/api/activeService/list", ("domain_id",), ("instance_id",)),
        Endpoint("/api/transaction/time", _RANGE, ("instance_id",)),
        Endpoint("/api/transaction/txid", _TX),
        Endpoint("/api/transaction/profile.txt", _TX, ("key",), accept=ACCEPT_TEXT),
        Endpoint("/api/transaction/sql", _TX),
        Endpoint("/api/dbsearch/event", _RANGE, ("level", "instance_id")),
        Endpoint("/api/dbsearch/error", _RANGE, ("instance_id",)),
        Endpoint("/api/status/application", _RANGE, ("instance_id", "max_row")),
        Endpoint("/api/status/sql", _RANGE),
        Endpoint("/api/status/external_call", _RANGE),
        Endpoint("/api-v2/deploy/{domainId}", ("startTime", "endTime")),
    )
}

# 경로 변수는 숫자만 받는다(도메인 ID).
_TEMPLATE_RES: dict[str, re.Pattern[str]] = {
    t: re.compile("^" + re.sub(r"\\\{[^}]+\\\}", r"[0-9]+", re.escape(t)) + "$") for t in ALLOWED
}
_FORBIDDEN_FRAGMENTS = ("//", "/../", "/./", "%", "\\", "?", "#", "://")


def match_template(path: str) -> str | None:
    """구체 경로를 허용 템플릿에 정확 일치로 대응시킨다. 변형·우회 표기는 None."""
    if not isinstance(path, str) or not path.startswith("/"):
        return None
    if any(frag in path for frag in _FORBIDDEN_FRAGMENTS):
        return None
    if path.endswith(("/..", "/.")):
        return None
    for template, rx in _TEMPLATE_RES.items():
        if rx.match(path):
            return template
    return None


def build_path(template: str, path_vars: dict[str, Any] | None = None) -> str:
    """템플릿에 경로 변수를 채운다(숫자 문자열만 — 그 밖은 거부)."""
    path = template
    for name, value in (path_vars or {}).items():
        text = str(value)
        if not text.isdigit():
            raise NotAllowedError(f"경로 변수 {name}는 숫자여야 한다")
        path = path.replace("{" + name + "}", text)
    if "{" in path:
        raise NotAllowedError(f"경로 변수 누락: {template}")
    return path


def check_request(method: str, path: str, params: dict[str, Any] | None) -> Endpoint:
    """요청을 허용목록으로 검사하고 해당 엔드포인트를 돌려준다. 어긋나면 `NotAllowedError`.

    HTTP를 보내기 **전에** 부른다 — 거부된 요청은 네트워크에 나가지 않는다.
    """
    if str(method).upper() != "GET":
        raise NotAllowedError(f"GET만 허용: {method}")
    template = match_template(path)
    if template is None:
        raise NotAllowedError(f"허용목록 밖 경로: {path}")
    endpoint = ALLOWED[template]
    keys = dict(params or {})
    for key, value in keys.items():
        if str(key).lower() == "token":
            raise NotAllowedError("쿼리 token 키는 허용하지 않는다(Authorization 헤더만)")
        if key not in endpoint.query_keys:
            raise NotAllowedError(f"허용 밖 쿼리 키: {key} ({template})")
        if isinstance(value, (list, tuple, set, dict)) or value is None:
            raise NotAllowedError(f"쿼리 값은 단일 스칼라여야 한다: {key}")
    missing = [name for name in endpoint.required if name not in keys]
    if missing:
        raise NotAllowedError(f"필수 쿼리 키 누락: {', '.join(missing)} ({template})")
    return endpoint
