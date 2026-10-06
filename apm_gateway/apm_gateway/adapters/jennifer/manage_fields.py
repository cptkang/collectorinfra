"""제니퍼 관리·민감 조회 응답 → 벤더 중립 레코드 (plans/134 W7 · N-15·N-16 ·
`spec/CAPABILITY-MAP-134.md` 표 C).

- v2 응답은 `{result: …}` 봉투가 아니라 **맨 배열·객체·불리언**이다(COV E-18). 경로마다 파서를 두고,
  모양이 다르면 None을 돌려준다 — 호출자(`manage_api`)가 `apm_api_error`로 올린다(빈 결과로 강등하지
  않는다).
- 표 C에 있는 필드는 중립 키로 옮기고, **표에 없는 키는 버리지 않고 `extra`에 원형으로** 둔다.
  형이 맞지 않아 옮기지 못한 값도 `extra`에 원래 이름으로 남긴다(조용히 잃지 않는다).
- 자격증명 제거는 이 앞(클라이언트 — `domain/credentials.py`)에서 끝났다. 개인정보 가림은
  애플리케이션 계층이 행을 만들 때 한다(여기서는 값을 옮길 뿐이다 — `fields.py`와 같은 분담).
- 원천은 v2 매뉴얼 응답 예와 스펙 5.6.4 인라인 스키마다. **실응답 모양은 미검증**(W10)이다.
"""

from __future__ import annotations

from typing import Any

from apm_gateway.adapters.jennifer.fields import to_bool, to_float, to_int

EXTRA = "extra"
# 칸 형 — str(문자열·숫자를 문자열로) · int · float · bool · strs(문자열 목록)
_Spec = dict[str, tuple[str, str]]


def _convert(value: Any, kind: str) -> tuple[Any, bool]:
    """(변환 값, 성공 여부) — None은 성공(값 없음)이다."""
    if value is None:
        return None, True
    if kind == "str":
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            return None, False
        return str(value), True
    if kind == "int":
        number = to_int(value)
        return number, number is not None
    if kind == "float":
        real = to_float(value)
        return real, real is not None
    if kind == "bool":
        flag = to_bool(value)
        return flag, flag is not None
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        return list(value), True
    return None, False


def _pick(raw: dict[str, Any], spec: _Spec, *, skip: tuple[str, ...] = ()) -> dict[str, Any]:
    """`spec`(중립 키 → (벤더 키, 형))대로 옮기고 나머지 키·변환 실패 값은 `extra`로."""
    out: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    known = {field for field, _ in spec.values()} | set(skip)
    for neutral, (field, kind) in spec.items():
        value, ok = _convert(raw.get(field), kind)
        out[neutral] = value
        if not ok:
            extra[field] = raw.get(field)
    extra.update({k: v for k, v in raw.items() if k not in known})
    out[EXTRA] = extra
    return out


def _dicts(body: Any) -> list[dict[str, Any]] | None:
    """맨 배열이고 항목이 모두 객체면 그 목록(아니면 None)."""
    if isinstance(body, list) and all(isinstance(r, dict) for r in body):
        return body
    return None


# ── 사용자 · 계정 ────────────────────────────────────────────

USER_FIELDS: _Spec = {
    "user_id": ("id", "str"),
    "user_name": ("name", "str"),
    "email": ("email", "str"),
    "phone_number": ("phoneNumber", "str"),
}
# `password`는 자격증명 경계가 키째 지웠다(D-296 ③) — 남아 있어도 옮기지 않는다
ACCOUNT_FIELDS: _Spec = {
    "user_id": ("id", "str"),
    "user_name": ("name", "str"),
    "group": ("group", "str"),
    "allow_ip": ("allowIp", "str"),
}


def parse_user_list(body: Any) -> list[dict[str, Any]] | None:
    """`UserListSet` — `{"result": [User…]}`(v1 봉투)."""
    result = body.get("result") if isinstance(body, dict) else None
    rows = _dicts(result)
    return None if rows is None else [_pick(r, USER_FIELDS, skip=("password",)) for r in rows]


def parse_accounts(body: Any) -> list[dict[str, Any]] | None:
    """`/restapi/users` — `UserRes` 맨 배열."""
    rows = _dicts(body)
    return None if rows is None else [_pick(r, ACCOUNT_FIELDS, skip=("password",)) for r in rows]


def parse_account(raw: dict[str, Any]) -> dict[str, Any]:
    return _pick(raw, ACCOUNT_FIELDS, skip=("password",))


# ── 데이터 서버 ──────────────────────────────────────────────

RESOURCE_CPU_FIELDS: _Spec = {
    "cpu_core": ("core", "int"),
    "cpu_system_pct": ("system", "float"),
    "cpu_process_pct": ("process", "float"),
    "cpu_steal_pct": ("steal", "float"),
}
LOAD_AVERAGE_FIELDS: _Spec = {
    "load_average_1m": ("1m", "float"),
    "load_average_5m": ("5m", "float"),
    "load_average_15m": ("15m", "float"),
}
SYSTEM_PROPERTY_FIELDS: _Spec = {
    "keep_alive_timeout_ms": ("keepAliveTimeout", "int"),
    "db_path": ("dbPath", "str"),
    "log_path": ("logPath", "str"),
    "listen_address": ("listenAddress", "str"),
    "listen_port": ("listenPort", "int"),
    "backup_path": ("backupPath", "str"),
    "warning_usable_size_mb": ("warningUsableSizeInMB", "int"),
    "memory_lock": ("memoryLock", "bool"),
    "bootstrap_check": ("bootstrapCheck", "bool"),
    "otel_port": ("otelPort", "int"),
    "otel_protocol": ("otelProtocol", "str"),
}


def parse_data_server_domains(body: Any) -> tuple[int | None, list[dict[str, Any]]] | None:
    """`{count, list: [{address, domain: [{id, name}]}]}` → (데이터 서버 수, 행 `{server, domain_id,
    domain_name, extra}`). 도메인이 없는 데이터 서버도 1행(도메인 칸 None)."""
    if not isinstance(body, dict):
        return None
    servers = _dicts(body.get("list"))
    if servers is None:
        return None
    rows: list[dict[str, Any]] = []
    for item in servers:
        domains = item.get("domain")
        if domains is None:
            domains = []
        if _dicts(domains) is None:
            return None
        base = _pick(item, {"server": ("address", "str")}, skip=("domain",))
        for d in domains or [{}]:
            entry = _pick(d, {"domain_id": ("id", "int"), "domain_name": ("name", "str")})
            extra = {**base[EXTRA], **({"domain": entry[EXTRA]} if entry[EXTRA] else {})}
            rows.append(
                {
                    "server": base["server"],
                    "domain_id": entry["domain_id"],
                    "domain_name": entry["domain_name"],
                    EXTRA: extra,
                }
            )
    count, ok = _convert(body.get("count"), "int")
    return (count if ok else None), rows


def _per_server(body: Any) -> list[tuple[str, dict[str, Any]]] | None:
    """`{<데이터 서버 주소>: {…}}`(additionalProperties) → [(주소, 객체)]."""
    if not isinstance(body, dict) or not all(isinstance(v, dict) for v in body.values()):
        return None
    return [(str(k), v) for k, v in body.items()]


def parse_data_server_resource(body: Any) -> list[dict[str, Any]] | None:
    """데이터 서버별 CPU(스펙 5.6.4 인라인 — `cpu`만 선언 · 메모리·디스크가 오면 `extra` ·
    COV E-04)."""
    servers = _per_server(body)
    if servers is None:
        return None
    rows = []
    for address, raw in servers:
        extra = {k: v for k, v in raw.items() if k != "cpu"}
        cpu_raw = raw.get("cpu")
        cpu = cpu_raw if isinstance(cpu_raw, dict) else {}
        if cpu_raw is not None and not isinstance(cpu_raw, dict):
            extra["cpu"] = cpu_raw
        load_raw = cpu.get("loadAverage")
        main = _pick(cpu, RESOURCE_CPU_FIELDS, skip=("loadAverage",))
        avg = _pick(load_raw if isinstance(load_raw, dict) else {}, LOAD_AVERAGE_FIELDS)
        cpu_extra = main.pop(EXTRA)
        if load_raw is not None and not isinstance(load_raw, dict):
            cpu_extra["loadAverage"] = load_raw
        elif avg[EXTRA]:
            cpu_extra["loadAverage"] = avg[EXTRA]
        if cpu_extra:
            extra["cpu"] = cpu_extra
        rows.append(
            {
                "server": address,
                **main,
                **{k: avg[k] for k in LOAD_AVERAGE_FIELDS},
                EXTRA: extra,
            }
        )
    return rows


def parse_data_server_properties(body: Any) -> list[dict[str, Any]] | None:
    """데이터 서버별 시스템 속성 설정(스펙 밖 키는 `extra` — 매뉴얼 *설정에 비밀 값 가능*)."""
    servers = _per_server(body)
    if servers is None:
        return None
    return [{"server": a, **_pick(raw, SYSTEM_PROPERTY_FIELDS)} for a, raw in servers]


# ── 룰 · 색상 경계 ───────────────────────────────────────────

RULE_ERROR_FIELDS: _Spec = {
    "error_type": ("errorType", "str"),
    "level": ("level", "str"),
    "applied": ("applied", "bool"),
    "check_time_range_ms": ("checkTimeRange", "int"),
    "threshold_error_count": ("thresholdErrorCount", "int"),
    "icon_recovery_time_ms": ("iconRecoveryTime", "int"),
    "custom_message": ("customMessage", "str"),
    "auto_script_command": ("autoScriptCommand", "str"),
}
RULE_METRIC_FIELDS: _Spec = {
    "metric_id": ("metricId", "str"),
    "level": ("level", "str"),
    "applied": ("applied", "bool"),
    "expression": ("expression", "str"),
    "check_time_range_ms": ("checkTimeRange", "int"),
    "threshold_error_count": ("thresholdErrorCount", "int"),
    "icon_recovery_time_ms": ("iconRecoveryTime", "int"),
    "custom_message": ("customMessage", "str"),
    "auto_script_command": ("autoScriptCommand", "str"),
}
RULE_COMPARE_FIELDS: _Spec = {
    "metric_id": ("metricId", "str"),
    "level": ("level", "str"),
    "applied": ("applied", "bool"),
    "icon_recovery_time_ms": ("iconRecoveryTime", "int"),
}
COMPARE_TARGET_FIELDS: _Spec = {
    "target_operator": ("operator", "str"),
    "target_period": ("period", "str"),
    "target_ratio_pct": ("ratioInPercent", "float"),
}
COMPARE_FILTER_FIELDS: _Spec = {
    "filter_metric_id": ("metricId", "str"),
    "filter_minimum_value": ("minimumValue", "float"),
}


def _nested(raw: dict[str, Any], key: str, spec: _Spec, extra: dict[str, Any]) -> dict[str, Any]:
    """중첩 객체 칸(`target`·`filter` — 미활성이면 null) → 중립 칸. 모르는 하위 키는
    `extra[key]`."""
    value = raw.get(key)
    if value is not None and not isinstance(value, dict):
        extra[key] = value
        value = None
    picked = _pick(value or {}, spec)
    if picked[EXTRA]:
        extra[key] = picked.pop(EXTRA)
    else:
        picked.pop(EXTRA)
    return picked


def parse_error_rules(body: Any) -> list[dict[str, Any]] | None:
    rows = _dicts(body)
    return None if rows is None else [_pick(r, RULE_ERROR_FIELDS) for r in rows]


def parse_metric_rules(body: Any) -> list[dict[str, Any]] | None:
    rows = _dicts(body)
    return None if rows is None else [_pick(r, RULE_METRIC_FIELDS) for r in rows]


def parse_compare_rules(body: Any) -> list[dict[str, Any]] | None:
    rows = _dicts(body)
    if rows is None:
        return None
    out = []
    for raw in rows:
        rec = _pick(raw, RULE_COMPARE_FIELDS, skip=("target", "filter"))
        extra = rec.pop(EXTRA)
        rec.update(_nested(raw, "target", COMPARE_TARGET_FIELDS, extra))
        rec.update(_nested(raw, "filter", COMPARE_FILTER_FIELDS, extra))
        rec[EXTRA] = extra
        out.append(rec)
    return out


def parse_flag(body: Any) -> bool | None:
    """불리언 맨 본문(`true`/`false` — 봉투 없음)."""
    return to_bool(body)


def parse_color_boundaries(body: Any) -> list[int | float] | None:
    """경과 시간 경계 3개(작은 순 — 파랑/연두 · 연두/주황 · 주황/빨강)의 맨 숫자 배열."""
    if not isinstance(body, list) or len(body) != 3:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in body):
        return None
    return list(body)


# ── 실행 중 요청 · 경로 · 환경 · 프로세스 · 클래스 · RDB Export ─────────

ACTIVE_DETAIL_FIELDS: _Spec = {
    "user_id": ("userId", "str"),
    "guid": ("guid", "str"),
    "sql": ("sql", "str"),
}
HTTP_FIELDS: _Spec = {"http_method": ("method", "str"), "http_query": ("query", "str")}
DB_PATH_FIELDS: _Spec = {"db_main_path": ("main", "str"), "db_backup_path": ("backup", "str")}
LOADED_CLASS_FIELDS: _Spec = {
    "class_name": ("className", "str"),
    "super_class_name": ("superClassName", "str"),
    "interface_class_names": ("interfaceClassNames", "strs"),
    "class_loader_name": ("classLoaderName", "str"),
}
RDB_EXPORT_FIELDS: _Spec = {
    "export_id": ("id", "str"),
    "export_date": ("date", "str"),
    "status": ("statusDescription", "str"),
}
# 환경변수 응답의 정해진 묶음(그 밖 키는 미공개 — COV E-13 · 원형 보존)
ENV_SCOPES: tuple[str, ...] = ("SYSTEM", "JAVA")


def parse_active_detail(body: Any) -> dict[str, Any] | None:
    """실행 중 요청 상세(객체) — `http{method, query}`는 평탄화한다."""
    if not isinstance(body, dict):
        return None
    rec = _pick(body, ACTIVE_DETAIL_FIELDS, skip=("http",))
    extra = rec.pop(EXTRA)
    rec.update(_nested(body, "http", HTTP_FIELDS, extra))
    rec[EXTRA] = extra
    return rec


def parse_db_path(body: Any) -> dict[str, Any] | None:
    return _pick(body, DB_PATH_FIELDS) if isinstance(body, dict) else None


def parse_environment(body: Any) -> list[dict[str, Any]] | None:
    """`{<instanceId>: {SYSTEM: {키: 값}, JAVA: {키: 값}, …}}` → 긴 형식 `{instance_id, scope,
    name, value}`. 키를 골라 줄이지 않는다(D-299 ⑦) — 묶음 값이 객체가 아니면 `name` None
    1행으로 원형을 싣는다."""
    if not isinstance(body, dict):
        return None
    rows: list[dict[str, Any]] = []
    for raw_id, scopes in body.items():
        instance_id = to_int(raw_id)
        if instance_id is None or not isinstance(scopes, dict):
            return None
        for scope, entries in scopes.items():
            base = {"instance_id": instance_id, "scope": str(scope)}
            if isinstance(entries, dict):
                rows += [{**base, "name": str(k), "value": v} for k, v in entries.items()]
            else:
                rows.append({**base, "name": None, "value": entries})
    return rows


def parse_instances_by_process(body: Any) -> list[dict[str, Any]] | None:
    """`{<domainId>: {<instanceId>: {hostname}}}`(없으면 `{}`) → `{domain_id, instance_id, hostname,
    extra}`."""
    if not isinstance(body, dict):
        return None
    rows: list[dict[str, Any]] = []
    for raw_domain, instances in body.items():
        domain_id = to_int(raw_domain)
        if domain_id is None or not isinstance(instances, dict):
            return None
        for raw_instance, info in instances.items():
            instance_id = to_int(raw_instance)
            if instance_id is None or not isinstance(info, dict):
                return None
            rec = _pick(info, {"hostname": ("hostname", "str")})
            rows.append({"domain_id": domain_id, "instance_id": instance_id, **rec})
    return rows


def parse_loaded_classes(body: Any) -> list[dict[str, Any]] | None:
    rows = _dicts(body)
    return None if rows is None else [_pick(r, LOADED_CLASS_FIELDS) for r in rows]


def parse_rdb_exports(body: Any) -> list[dict[str, Any]] | None:
    rows = _dicts(body)
    return None if rows is None else [_pick(r, RDB_EXPORT_FIELDS) for r in rows]
