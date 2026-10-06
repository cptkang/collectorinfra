"""녹화 하네스·목 서버가 함께 쓰는 제니퍼 Open API 카탈로그 (plans/87 §5.2(e) · §0.10 실측).

허용목록 정본은 게이트웨이 코드 `apm_gateway/apm_gateway/adapters/jennifer/allowlist.py`다(J1 · 2026-09-29).
이 파일은 J0 검증 도구용 사본이며, `apm_gateway/tests/test_allowlist.py`가 두 목록이 같은지 대조한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Endpoint:
    template: str
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    accept: str = "application/json"
    needs_domain: bool = True  # 도메인 미연결이면 500 "<d> Domain is not connected"
    empty_when_disconnected: bool = False  # 미연결이어도 200 빈 결과(실측)
    param_types: dict = field(default_factory=dict)
    # (변수 이름, 형식) — 정본 allowlist.py와 같은 선언(SPEC-apm-question-coverage §2.4)
    path_vars: tuple = ()


_RANGE = ("domain_id", "start_time", "end_time")
_TX = ("domain_id", "txid", "time")
# `/api/status/{sql,external_call}` 선택 키(plans/134 N-8 · COV-STAT-SQL·EXT)
_STATUS_OPTIONAL = ("instance_id", "sort_by_metrics", "max_row")

# §5.2(e) 허용목록 — GET만 · 경로 템플릿 정확 일치
ALLOWED: dict[str, Endpoint] = {
    e.template: e
    for e in (
        Endpoint("/api/domain", needs_domain=False),
        Endpoint("/api/instance", ("domain_id",)),
        Endpoint(
            "/api/realtime/instance", ("domain_id",), ("instance_id",), empty_when_disconnected=True
        ),
        Endpoint(
            "/api/dbmetrics/instance",
            ("domain_id", "instance_id", "interval_minute", "metrics", "start_time", "end_time"),
        ),
        Endpoint("/api/metrics", needs_domain=False),
        Endpoint("/api/activeService/list", ("domain_id",), ("instance_id",)),
        Endpoint("/api/transaction/time", _RANGE, ("instance_id",), empty_when_disconnected=True),
        Endpoint("/api/transaction/txid", _TX, ("key",)),
        Endpoint("/api/transaction/profile.txt", _TX, ("key",), accept="text/plain"),
        Endpoint("/api/transaction/sql", _TX, ("profile_no", "key", "include_param_key")),
        # GUID 연계 거래(plans/134 W5 N-13 · COV-TX-GUID)
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
        # plans/134 W7 — 관리·민감 조회 GET(정본 allowlist.py와 같은 순서·선언)
        Endpoint("/api/auth/userlist", needs_domain=False),
        Endpoint("/restapi/users", needs_domain=False),
        Endpoint("/restapi/user/{id}", needs_domain=False, path_vars=(("id", "account"),)),
        Endpoint("/api-v2/manage/data-server/domains", needs_domain=False),
        Endpoint("/api-v2/manage/data-server/resource", needs_domain=False),
        Endpoint("/api-v2/manage/data-server/system-property-config", needs_domain=False),
        Endpoint("/api-v2/manage/rule/active-service-color-range-boundary", needs_domain=False),
        Endpoint(
            "/api-v2/active-service/detail/{domainId}/{txid}",
            optional=("sessionId", "threadHash"),
            path_vars=(("domainId", "int"), ("txid", "sint")),
        ),
        Endpoint("/api-v2/manage/db/path/{domainId}", path_vars=(("domainId", "int"),)),
        Endpoint("/api-v2/environment-variable/{domainId}", path_vars=(("domainId", "int"),)),
        Endpoint("/api-v2/manage/instance", ("processId",), ("hostname",), needs_domain=False),
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
        Endpoint("/api-v2/manual-rdb-export", needs_domain=False),
    )
}

# 실측한 "Required request parameter" 메시지의 타입 표기(없는 것은 String으로 둔다)
PARAM_TYPES = {"domain_id": "short", "instance_id": "short", "txid": "long", "time": "long"}

# EventData 13필드(정본 스펙 5.6.4 — [J-23] #32)
EVENT_FIELDS = (
    "applicationName",
    "domainId",
    "domainName",
    "errorType",
    "eventLevel",
    "instanceId",
    "instanceName",
    "instanceOid",
    "message",
    "metricsName",
    "time",
    "txid",
    "value",
)

# 허용목록 밖이지만 실측으로 존재가 확인된 경로.
# 목 서버가 같은 상태·모양으로 흉내 내고 접근을 기록한다.
# 개인정보 자리는 가짜(canary) 값이다.
CANARY_USER = {
    "id": "canary",
    "name": "CANARY",
    "email": "canary@example.invalid",
    "phoneNumber": "000-0000-0000",
}
SENSITIVE_GET = {
    "/api/auth/userlist": {"result": [CANARY_USER]},
    "/restapi/users": [
        {
            "id": "canary",
            "name": "CANARY",
            "group": "admin",
            "password": "",
            "allowIp": "",
            "creationTime": 0,
            "lastLoginTime": 0,
        }
    ],
    "/api-v2/manage/data-server/system-property-config": {},
}
# 도메인 단위 민감 GET — 도메인 미연결이면 500 + 빈 JSON 문자열(실측)
SENSITIVE_GET_DOMAIN_PREFIX = ("/api-v2/environment-variable/", "/api-v2/manage/rule/event/")
# 쓰기·관리 경로를 GET으로 불렀을 때의 실측 응답 (상태, 본문)
WRITE_PATHS_ON_GET = {
    "/api-v2/manage/data-server/control": (400, "400 bad request. message=content is not a json"),
    "/api-v2/manage/data-server/db/property/1000/copy": (
        400,
        "400 bad request. message=content is not a json",
    ),
    "/api-v2/configuration/rdb-export-password-override": (
        405,
        "405 method not allowed. method=GET",
    ),
    "/api-v2/manage/domain/put": (500, "500 500 java.lang.NullPointerException"),
    "/api-v2/manage/domain-group": (200, []),
    "/api-v2/manual-rdb-export": (200, []),
}

_TEMPLATE_RES = {
    # 이름 없는 묶음 — 경로 변수가 둘 이상인 템플릿(W7)에서 같은 이름 묶음이 겹치지 않게
    t: re.compile("^" + re.sub(r"\\\{[^}]+\\\}", r"[^/]+", re.escape(t)) + "$")
    for t in ALLOWED
}


def match_template(path: str) -> str | None:
    """구체 경로를 허용목록 템플릿에 정확 일치로 대응시킨다(정규화 뒤 · 변형 거부)."""
    if "//" in path or "/../" in path or path.endswith("/..") or "%" in path:
        return None
    if path.lower().endswith(".xml"):  # 정본과 같다(W7 — 계정 ID 형식이 `.`을 받는다)
        return None
    for template, rx in _TEMPLATE_RES.items():
        if rx.fullmatch(path):  # 정본과 같다(`$`는 끝 개행을 받는다 — AUDIT-11)
            return template
    return None


def missing_param_message(name: str) -> str:
    return (
        f"Required request parameter '{name}' for method parameter type "
        f"{PARAM_TYPES.get(name, 'String')} is not present"
    )


def fixture_stem(template: str) -> str:
    return "GET_" + re.sub(r"[^A-Za-z0-9]+", "_", template.strip("/")).strip("_")
