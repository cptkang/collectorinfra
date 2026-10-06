"""제니퍼 Open API 허용목록 **정본** (plans/87 §5.2(e) · §8.1 · D-195 ① 정정 부기).

1차 통제 = **메서드 + 경로 템플릿 정확 일치 + 경로별 쿼리 키** — 그 밖은 전부 네트워크 호출 전에
거부한다. 같은 토큰에 쓰기·제어 API와 민감 GET(`/api/auth/userlist` 등)이 함께 열려 있고 서버가 막아
주지 않는다(§0.10 #9·#14·#15 실측). 그래서:

- GET만 통과한다(v1 조회 API의 POST 변형도 거부). plans/134 W7(D-296 ①)부터 민감·관리
  **조회** GET도 목록에 있다 — 같은 경로의 PUT·POST·DELETE와 시험 경로는 계속 거부한다.
  응답은 어댑터가 파싱한 직후 자격증명 경계(`domain/credentials.py`)를 지난다.
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


# 경로 변수 형식(SPEC-apm-question-coverage §2.4) — 형식 밖 값은 HTTP 0회로 거부한다. `enum:a|b`는
# 열거(값 그대로 일치)다.
PATH_VAR_FORMATS: dict[str, str] = {
    "int": r"[0-9]+",
    "token": r"[A-Z0-9_]{1,64}",
    "account": r"[A-Za-z0-9._@-]{1,64}",
    # 부호 있는 정수 — 실행 중 요청 txid는 음수일 수 있다(plans/134 W7 · COV-ACTIVE-DETAIL)
    "sint": r"-?[0-9]{1,20}",
}


def _var_pattern(fmt: str) -> str:
    if fmt.startswith("enum:"):
        return "(?:" + "|".join(re.escape(v) for v in fmt[len("enum:") :].split("|")) + ")"
    return PATH_VAR_FORMATS[fmt]


@dataclass(frozen=True)
class Endpoint:
    template: str
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    accept: str = ACCEPT_JSON
    # (변수 이름, 형식) — 템플릿의 `{변수}`마다 하나씩 선언한다(§2.4)
    path_vars: tuple[tuple[str, str], ...] = ()

    @property
    def query_keys(self) -> frozenset[str]:
        return frozenset(self.required) | frozenset(self.optional)


_RANGE = ("domain_id", "start_time", "end_time")
_TX = ("domain_id", "txid", "time")
# `/api/status/{sql,external_call}` 선택 키(plans/134 N-8 · COV-STAT-SQL·EXT)
_STATUS_OPTIONAL = ("instance_id", "sort_by_metrics", "max_row")

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
        Endpoint("/api/transaction/txid", _TX, ("key",)),
        Endpoint("/api/transaction/profile.txt", _TX, ("key",), accept=ACCEPT_TEXT),
        Endpoint("/api/transaction/sql", _TX, ("profile_no", "key", "include_param_key")),
        # GUID 연계 거래(plans/134 W5 N-13 · COV-TX-GUID) — `time_pattern`은 계속 거부(epoch ms만)
        Endpoint("/api/transaction/guid", ("domain_id", "guid", "start_time", "end_time")),
        Endpoint("/api/dbsearch/event", _RANGE, ("level", "instance_id")),
        Endpoint("/api/dbsearch/error", _RANGE, ("instance_id", "error_type")),
        Endpoint(
            "/api/status/application",
            _RANGE,
            ("instance_id", "sort_by_metrics", "max_row", "application_name"),
        ),
        Endpoint("/api/status/sql", _RANGE, _STATUS_OPTIONAL),
        Endpoint("/api/status/external_call", _RANGE, _STATUS_OPTIONAL),
        Endpoint(
            "/api-v2/deploy/{domainId}", ("startTime", "endTime"), path_vars=(("domainId", "int"),)
        ),
        # 업무 정의 목록(plans/130 W2 N-5 · D-290 ④ · COV-BUSINESS) — 업무명 해석 근거 B2
        Endpoint("/api/business", ("domain_id",)),
        # 서비스(도메인)·업무 현재값·시계열(plans/134 W3 N-9 · W4 N-12 · D-290 ④ 부기 ·
        # COV-RT-DOMAIN·COV-DBM-DOMAIN·COV-RT-BUSINESS·COV-DBM-BUSINESS) — `time_pattern`은 계속
        # 거부(epoch ms만)
        Endpoint("/api/realtime/domain", optional=("domain_id",)),
        Endpoint(
            "/api/dbmetrics/domain",
            ("domain_id", "interval_minute", "metrics", "start_time", "end_time"),
        ),
        Endpoint("/api/realtime/business", ("domain_id",), ("business_id",)),
        Endpoint(
            "/api/dbmetrics/business",
            ("domain_id", "business_id", "interval_minute", "metrics", "start_time", "end_time"),
        ),
        # plans/134 W7(N-15·N-16 · D-296 ① — 관리·민감 조회 GET · 응답은 자격증명 경계를
        # 지난다). `compare`·`comparing`은 같은 비교 룰의 두 표기다(COV E-01 — `compare` 먼저,
        # 404면 `comparing`). 실행 중 요청 상세의 `sessionId`·`threadHash`는 필수 여부
        # 미기재(E-15)라 선택이다.
        Endpoint("/api/auth/userlist"),
        Endpoint("/restapi/users"),
        Endpoint("/restapi/user/{id}", path_vars=(("id", "account"),)),
        Endpoint("/api-v2/manage/data-server/domains"),
        Endpoint("/api-v2/manage/data-server/resource"),
        Endpoint("/api-v2/manage/data-server/system-property-config"),
        Endpoint("/api-v2/manage/rule/active-service-color-range-boundary"),
        Endpoint(
            "/api-v2/active-service/detail/{domainId}/{txid}",
            optional=("sessionId", "threadHash"),
            path_vars=(("domainId", "int"), ("txid", "sint")),
        ),
        Endpoint("/api-v2/manage/db/path/{domainId}", path_vars=(("domainId", "int"),)),
        Endpoint("/api-v2/environment-variable/{domainId}", path_vars=(("domainId", "int"),)),
        Endpoint("/api-v2/manage/instance", ("processId",), ("hostname",)),
        Endpoint(
            "/api-v2/loaded-class/{domainId}/{instanceId}",
            optional=("search",),
            path_vars=(("domainId", "int"), ("instanceId", "int")),
        ),
        Endpoint("/api-v2/manage/rule/event/error/{domainId}", path_vars=(("domainId", "int"),)),
        Endpoint(
            "/api-v2/manage/rule/event/metric/{domainId}/{targetType}",
            path_vars=(("domainId", "int"), ("targetType", "enum:domain|instance|business")),
        ),
        Endpoint(
            "/api-v2/manage/rule/event/compare/{domainId}/{targetType}",
            path_vars=(("domainId", "int"), ("targetType", "enum:domain|instance")),
        ),
        Endpoint(
            "/api-v2/manage/rule/event/comparing/{domainId}/{targetType}",
            path_vars=(("domainId", "int"), ("targetType", "enum:domain|instance")),
        ),
        Endpoint(
            "/api-v2/manage/rule/event/error/{domainId}/{errorType}/applied",
            path_vars=(("domainId", "int"), ("errorType", "token")),
        ),
        Endpoint(
            "/api-v2/manage/rule/event/error/{domainId}/{errorType}"
            "/individual-setting/{instanceId}",
            path_vars=(("domainId", "int"), ("errorType", "token"), ("instanceId", "int")),
        ),
        Endpoint("/api-v2/manual-rdb-export"),
    )
}


def _template_re(ep: Endpoint) -> re.Pattern[str]:
    names = re.findall(r"\{([^}]+)\}", ep.template)
    if sorted(names) != sorted(n for n, _ in ep.path_vars):
        raise ValueError(f"경로 변수 형식 선언이 템플릿과 다르다: {ep.template}")
    pattern = re.escape(ep.template)
    for name, fmt in ep.path_vars:
        pattern = pattern.replace(re.escape("{" + name + "}"), _var_pattern(fmt))
    return re.compile("^" + pattern + "$")


# 경로 변수는 선언한 형식만 받는다(§2.4 — 도메인 ID는 숫자).
_TEMPLATE_RES: dict[str, re.Pattern[str]] = {t: _template_re(ep) for t, ep in ALLOWED.items()}
_FORBIDDEN_FRAGMENTS = ("//", "/../", "/./", "%", "\\", "?", "#", "://")


def match_template(path: str) -> str | None:
    """구체 경로를 허용 템플릿에 정확 일치로 대응시킨다. 변형·우회 표기는 None."""
    if not isinstance(path, str) or not path.startswith("/"):
        return None
    if any(frag in path for frag in _FORBIDDEN_FRAGMENTS):
        return None
    if path.endswith(("/..", "/.")):
        return None
    if path.lower().endswith(".xml"):  # 같은 데이터의 XML 변형(계정 ID 형식이 `.`을 받는다 — W7)
        return None
    for template, rx in _TEMPLATE_RES.items():
        # `$`는 끝 개행을 받는다 — 전체 일치(AUDIT-11 · `jobs.is_job_id` 전례)
        if rx.fullmatch(path):
            return template
    return None


def build_path(template: str, path_vars: dict[str, Any] | None = None) -> str:
    """템플릿에 경로 변수를 채운다(선언한 형식에 맞는 값만 — 그 밖은 거부 · §2.4)."""
    path = template
    endpoint = ALLOWED.get(template)
    for name, value in (path_vars or {}).items():
        text = str(value)
        formats = dict(endpoint.path_vars) if endpoint is not None else {}
        if name not in formats:
            raise NotAllowedError(f"선언되지 않은 경로 변수: {name} ({template})")
        if not re.fullmatch(_var_pattern(formats[name]), text):
            raise NotAllowedError(f"경로 변수 {name}가 형식({formats[name]})에 맞지 않는다")
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
