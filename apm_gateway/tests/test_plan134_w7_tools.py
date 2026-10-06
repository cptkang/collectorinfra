"""plans/134 W7 관리·민감 조회 도구 4종 — 인자 전달 · 행 모양 · 마스킹 · 404 · 모양 위반 · 부분 실패
(N-15·N-16 · SPEC-apm-question-coverage §4·§5 · COV E-01·E-03·E-13·E-15·E-18·E-19·E-28).

응답 원천은 목 서버의 W7 합성 본문(`mock_openapi.w7_body` — 스펙·v2 매뉴얼 응답 예 기반)이다.
`httpx.MockTransport`로 같은 본문을 쓴다(실 제니퍼·외부 네트워크 0). 실응답 모양은 W10.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from apm_gateway.application.manage_tools import ManageTools
from apm_gateway.domain.credentials import MASK
from apm_gateway.domain.errors import ApmError
from conftest import DOMAIN, TOKEN, make_tools, synthetic_fixtures, synthetic_handler
from jennifer_catalog import match_template
from mock_openapi import W7_SECRET, W7_TEMPLATES, W7Mock, w7_response

S = W7_SECRET


def w7_handler(
    mock: W7Mock | None = None,
    *,
    seen: list[tuple[str, dict[str, str]]] | None = None,
    override: dict[str, Any] | None = None,
    fixtures: list[dict] | None = None,
):
    """W7 경로는 목 서버 합성 본문, 그 밖(도메인·인스턴스 명단)은 합성 픽스처로 답한다."""
    inner = synthetic_handler(fixtures)
    state = SimpleNamespace(w7=mock or W7Mock(), mode="connected")

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        query = dict(request.url.params)
        if seen is not None:
            seen.append((path, query))
        if override and path in override:
            resp = override[path]
            return resp(request) if callable(resp) else resp
        template = match_template(path)
        if template in W7_TEMPLATES:
            status, body, ctype = w7_response(state, request.method, template, query, path)
            if ctype == "text/html":
                return httpx.Response(status, text=body, headers={"content-type": ctype})
            if status == 200 and body is None:
                return httpx.Response(200)
            return httpx.Response(status, json=body)
        return await inner(request)

    return handler


def two_domain_fixtures() -> list[dict]:
    """도메인 1000·2000 — 인스턴스 명단은 도메인과 무관하게 같은 3개를 돌려준다(합성 핸들러)."""
    out = []
    for fx in synthetic_fixtures():
        fx = json.loads(json.dumps(fx))
        if fx["request"]["template"] == "/api/domain":
            fx["response"]["body_json"]["result"].append({"domainId": 2000, "name": "other"})
        out.append(fx)
    return out


def manage(handler, **kw) -> ManageTools:
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler), **kw)
    return ManageTools(tools)


def _paths(seen: list[tuple[str, dict[str, str]]], prefix: str) -> list[tuple[str, dict]]:
    return [(p, q) for p, q in seen if p.startswith(prefix)]


def _dump(out: dict) -> str:
    return json.dumps(out, ensure_ascii=False)


# ── apm_config kind=event_rules ──────────────────────────────


@pytest.mark.asyncio
async def test_event_rules_all_types_for_host_domain_args_and_masking():
    seen: list = []
    out = await manage(w7_handler(seen=seen)).apm_config("event_rules", hostname="was-host01")
    calls = [p for p, _ in _paths(seen, "/api-v2/manage/rule/")]
    assert calls == [
        f"/api-v2/manage/rule/event/error/{DOMAIN}",
        f"/api-v2/manage/rule/event/metric/{DOMAIN}/domain",
        f"/api-v2/manage/rule/event/metric/{DOMAIN}/instance",
        f"/api-v2/manage/rule/event/metric/{DOMAIN}/business",
        f"/api-v2/manage/rule/event/compare/{DOMAIN}/domain",
        f"/api-v2/manage/rule/event/compare/{DOMAIN}/instance",
    ]
    assert out["kind"] == "event_rules" and "partial" not in out
    by_type = [(r["rule_type"], r["target_type"]) for r in out["rows"]]
    assert by_type == [
        ("error", None),
        ("metric", "domain"),
        ("metric", "instance"),
        ("metric", "business"),
        ("compare", "domain"),
        ("compare", "instance"),
    ]
    err = out["rows"][0]
    assert err["error_type"] == "OUTOFMEMORY" and err["level"] == "FATAL" and err["applied"] is True
    assert err["check_time_range_ms"] == 60000 and err["threshold_error_count"] == 1
    assert err["custom_message"] == "OOM 담당 <email>"  # mask_text
    # 명령줄 문맥은 첫 토큰(실행 파일)만 남기고 인자를 통째로 가린다(plans/134 W7 AUDIT-1 (a))
    assert err["auto_script_command"] == "/opt/jennifer/restart.sh [가림]"
    assert err["domain_id"] == DOMAIN and err["domain_name"] == "demo-domain"
    metric = out["rows"][1]
    assert metric["metric_id"] == "heap_used" and metric["expression"] == "value>30"
    cmp = out["rows"][4]
    assert (cmp["target_operator"], cmp["target_period"], cmp["target_ratio_pct"]) == (
        ">",
        "PREVIOUS_WEEK",
        130.0,
    )
    assert (cmp["filter_metric_id"], cmp["filter_minimum_value"]) == ("service_count", 10.0)
    assert all(r["extra"] == {} for r in out["rows"])
    assert S not in _dump(out)


@pytest.mark.asyncio
async def test_event_rules_error_type_applied_and_individual_404_is_not_an_error():
    mock = W7Mock()
    mock.individual[f"{DOMAIN}/OUTOFMEMORY/1001"] = True  # 1002는 404 = 개별 설정 없음(E-19)
    seen: list = []
    out = await manage(w7_handler(mock, seen=seen)).apm_config(
        "event_rules", hostname="was-host01", rule_type="error", error_type="outofmemory"
    )
    rows = {(r["rule_type"], r.get("instance_id")): r for r in out["rows"]}
    assert rows[("error_applied", None)]["applied"] is True
    assert rows[("error_applied", None)]["error_type"] == "OUTOFMEMORY"  # 대문자로 맞춘다
    one, two = rows[("error_individual", 1001)], rows[("error_individual", 1002)]
    assert (one["individual_setting"], one["individual_setting_found"]) == (True, True)
    assert (two["individual_setting"], two["individual_setting_found"]) == (None, False)
    assert two["instance_name"] == "was01_b"
    assert "partial" not in out
    assert any("개별 설정 없음(HTTP 404)" in x for x in out["limits"])
    assert (
        f"/api-v2/manage/rule/event/error/{DOMAIN}/OUTOFMEMORY/individual-setting/1002",
        {},
    ) in seen


@pytest.mark.asyncio
async def test_event_rules_error_type_without_hostname_says_individual_needs_host():
    out = await manage(w7_handler()).apm_config("event_rules", error_type="OUTOFMEMORY")
    assert {r["rule_type"] for r in out["rows"]} >= {"error_applied"}
    assert not [r for r in out["rows"] if r["rule_type"] == "error_individual"]
    assert any("hostname을 줄 때만" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_compare_404_then_comparing_is_asked_and_spelling_noted():
    """COV E-01 확정 — `compare` 먼저, 404일 때만 `comparing`으로 다시 묻는다."""
    mock = W7Mock()
    mock.compare_404 = True
    seen: list = []
    out = await manage(w7_handler(mock, seen=seen)).apm_config(
        "event_rules", hostname="was-host01", rule_type="compare", target="instance"
    )
    assert [p for p, _ in _paths(seen, "/api-v2/manage/rule/")] == [
        f"/api-v2/manage/rule/event/compare/{DOMAIN}/instance",
        f"/api-v2/manage/rule/event/comparing/{DOMAIN}/instance",
    ]
    assert [r["metric_id"] for r in out["rows"]] == ["service_time"]
    assert any("comparing으로 다시 물어" in x and "E-01" in x for x in out["limits"])
    assert "partial" not in out


@pytest.mark.asyncio
async def test_compare_answered_first_try_does_not_ask_comparing():
    seen: list = []
    out = await manage(w7_handler(seen=seen)).apm_config(
        "event_rules", hostname="was-host01", rule_type="compare", target="domain"
    )
    assert [p for p, _ in _paths(seen, "/api-v2/manage/rule/")] == [
        f"/api-v2/manage/rule/event/compare/{DOMAIN}/domain"
    ]
    assert not any("comparing" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_both_compare_spellings_404_is_version_unsupported_partial():
    mock = W7Mock()
    mock.compare_404 = True
    mock.not_found.add("/api-v2/manage/rule/event/comparing/{domainId}/{targetType}")
    out = await manage(w7_handler(mock)).apm_config("event_rules", hostname="was-host01")
    assert out["partial"] is True
    assert {r["rule_type"] for r in out["rows"]} == {"error", "metric"}
    fails = [x for x in out["limits"] if "이벤트 룰 조회 실패" in x]
    assert len(fails) == 2 and all("HTTP 404" in x and "E-28" in x for x in fails)


@pytest.mark.asyncio
async def test_v2_404_everywhere_is_error_not_empty_result():
    mock = W7Mock()
    mock.not_found.add("/api-v2/manage/rule/event/error/{domainId}")
    with pytest.raises(ApmError) as exc:
        await manage(w7_handler(mock)).apm_config(
            "event_rules", hostname="was-host01", rule_type="error"
        )
    assert exc.value.code == "apm_api_error"


@pytest.mark.asyncio
async def test_event_rules_without_host_cover_all_domains_and_partial_on_one_failure():
    seen: list = []
    handler = w7_handler(
        seen=seen,
        fixtures=two_domain_fixtures(),
        override={
            "/api-v2/manage/rule/event/error/2000": httpx.Response(
                500, json={"exception": {"message": "boom"}}
            )
        },
    )
    out = await manage(handler).apm_config("event_rules", rule_type="error")
    assert [p for p, _ in _paths(seen, "/api-v2/manage/rule/")] == [
        "/api-v2/manage/rule/event/error/1000",
        "/api-v2/manage/rule/event/error/2000",
    ]
    assert [r["domain_id"] for r in out["rows"]] == [1000]
    assert out["partial"] is True
    assert any("도메인 2000" in x and "apm_api_error" in x for x in out["limits"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [
        {"rule_type": "bogus"},
        {"target": "cluster"},
        {"rule_type": "compare", "target": "business"},
        {"error_type": "bad-type"},
        {"error_type": "X" * 65},
    ],
)
async def test_event_rules_invalid_arguments_make_no_http(args):
    seen: list = []
    with pytest.raises(ApmError) as exc:
        await manage(w7_handler(seen=seen)).apm_config("event_rules", **args)
    assert exc.value.code == "invalid_argument"
    assert _paths(seen, "/api-v2/") == []


@pytest.mark.asyncio
async def test_business_target_skips_compare_with_note():
    out = await manage(w7_handler()).apm_config("event_rules", target="business")
    assert {(r["rule_type"], r["target_type"]) for r in out["rows"]} == {
        ("error", None),
        ("metric", "business"),
    }
    assert any("compare 룰에는 business 대상이 없어" in x for x in out["limits"])


# ── 그 밖 kind ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_kind_and_ignored_arguments():
    with pytest.raises(ApmError) as exc:
        await manage(w7_handler()).apm_config("rules")
    assert exc.value.code == "invalid_argument"
    out = await manage(w7_handler()).apm_config(
        "data_server", hostname="was-host01", rule_type="error", search="x"
    )
    assert any("hostname으로 좁히지 않았다" in x for x in out["limits"])
    assert any("인자 rule_type·search를 쓰지 않는다" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_color_boundary_four_ranges():
    out = await manage(w7_handler()).apm_config("color_boundary")
    assert [(r["color"], r["lower_bound"], r["upper_bound"]) for r in out["rows"]] == [
        ("blue", None, 3000),
        ("yellowgreen", 3000, 8000),
        ("orange", 8000, 15000),
        ("red", 15000, None),
    ]
    assert [r["color_label"] for r in out["rows"]] == ["파랑", "연두", "주황", "빨강"]
    assert out["sources"] == [{"source_id": "default", "status": "ok", "reason": ""}]
    assert any("단위는 매뉴얼에 없다" in x for x in out["limits"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("template", "body", "call"),
    [
        (
            "/api-v2/manage/rule/active-service-color-range-boundary",
            [3000, 8000],
            ("color_boundary", {}),
        ),
        (
            "/api-v2/manage/rule/active-service-color-range-boundary",
            {"a": 1},
            ("color_boundary", {}),
        ),
        ("/api-v2/manage/rule/event/error/{domainId}", {"result": []}, ("event_rules", {})),
        ("/api-v2/manage/rule/event/error/{domainId}", [1, 2], ("event_rules", {})),
        ("/api-v2/manage/db/path/{domainId}", ["/x"], ("db_path", {})),
        ("/api-v2/manual-rdb-export", {"id": "x"}, ("rdb_export", {})),
        ("/api-v2/manage/instance", [], ("process_instance", {"process_id": 4242})),
        ("/api-v2/manage/instance", {"x": {}}, ("process_instance", {"process_id": 4242})),
        ("/api-v2/manage/data-server/domains", {"count": 1}, ("data_server", {})),
    ],
    ids=[
        "color-2",
        "color-obj",
        "rule-v1-envelope",
        "rule-scalars",
        "dbpath-list",
        "rdb-obj",
        "proc-list",
        "proc-badkey",
        "ds-nolist",
    ],
)
async def test_v2_shape_violation_is_api_error_not_empty(template, body, call):
    mock = W7Mock()
    mock.bodies[template] = (200, body)
    kind, extra = call
    if kind == "event_rules":
        extra = {"rule_type": "error"}
    if kind == "data_server":  # 3호출 중 1개만 모양 위반 → 부분 실패
        out = await manage(w7_handler(mock)).apm_config(kind)
        assert out["partial"] is True
        assert any("모양이 예상과 다르다" in x for x in out["limits"])
        return
    with pytest.raises(ApmError) as exc:
        await manage(w7_handler(mock)).apm_config(kind, **extra)
    assert exc.value.code == "apm_api_error" and "모양" in exc.value.reason


@pytest.mark.asyncio
async def test_process_instance_args_rows_and_version_note():
    seen: list = []
    out = await manage(w7_handler(seen=seen)).apm_config(
        "process_instance", process_id=4242, hostname="was-host01"
    )
    assert _paths(seen, "/api-v2/manage/instance") == [
        ("/api-v2/manage/instance", {"processId": "4242", "hostname": "was-host01"})
    ]
    assert out["rows"] == [
        {
            "source_id": "default",
            "domain_id": DOMAIN,
            "instance_id": 1001,
            "hostname": "was-host01",
            "instance_name": "was01_a",
            "extra": {},
        }
    ]
    assert any("5.6.0.21" in x and "5.6.0.8" in x and "E-03" in x for x in out["limits"])
    empty = await manage(w7_handler()).apm_config("process_instance", process_id=7)
    assert empty["rows"] == [] and any("해당하는 인스턴스가 없다" in x for x in empty["limits"])


@pytest.mark.asyncio
@pytest.mark.parametrize("pid", [None, 0, -1, "abc", True, 1.5])
async def test_process_instance_requires_positive_process_id(pid):
    seen: list = []
    with pytest.raises(ApmError) as exc:
        await manage(w7_handler(seen=seen)).apm_config("process_instance", process_id=pid)
    assert exc.value.code == "invalid_argument" and _paths(seen, "/api-v2/") == []


@pytest.mark.asyncio
async def test_data_server_three_sections_extra_and_secret():
    out = await manage(w7_handler()).apm_config("data_server")
    sections = [r["section"] for r in out["rows"]]
    assert sections == ["domains", "resource", "system_properties"]
    dom, res, prop = out["rows"]
    assert (dom["server"], dom["domain_id"], dom["domain_name"]) == (
        "ds01:5555",
        1000,
        "demo-domain",
    )
    assert (res["cpu_core"], res["cpu_system_pct"], res["load_average_15m"]) == (8, 12.0, 3.0)
    assert res["extra"] == {"memory": {"total": 16384, "used": 4096}}  # 스펙 밖 항목(E-04) 보존
    assert prop["listen_port"] == 5555 and prop["memory_lock"] is False
    assert prop["db_path"] == "/data/jennifer/db"
    assert prop["extra"]["rdbExportPassword"] == MASK
    assert S not in prop["extra"]["rdbUrl"] and "rdb.example" in prop["extra"]["rdbUrl"]
    assert out["summary"] == {"data_server_count": {"default": 1}}
    note = next(x for x in out["limits"] if "스펙 표 밖 키" in x)
    assert "memory" in note and "rdbExportPassword" in note and S not in note
    assert S not in _dump(out)


@pytest.mark.asyncio
async def test_db_path_per_domain_and_jdbc_userinfo_masked():
    seen: list = []
    out = await manage(w7_handler(seen=seen)).apm_config("db_path", hostname="was-host01")
    assert [p for p, _ in _paths(seen, "/api-v2/manage/db/")] == [
        f"/api-v2/manage/db/path/{DOMAIN}"
    ]
    row = out["rows"][0]
    assert row["db_main_path"] == "/data/jennifer/db/main"
    assert S not in row["db_backup_path"] and MASK in row["db_backup_path"]


@pytest.mark.asyncio
async def test_loaded_classes_need_host_pass_search_and_rows_per_instance():
    with pytest.raises(ApmError) as exc:
        await manage(w7_handler()).apm_config("loaded_classes")
    assert exc.value.code == "invalid_argument"
    seen: list = []
    out = await manage(w7_handler(seen=seen)).apm_config(
        "loaded_classes", hostname="was-host01", search="Order"
    )
    assert _paths(seen, "/api-v2/loaded-class/") == [
        (f"/api-v2/loaded-class/{DOMAIN}/1001", {"search": "Order"}),
        (f"/api-v2/loaded-class/{DOMAIN}/1002", {"search": "Order"}),
    ]
    assert [(r["instance_id"], r["class_name"]) for r in out["rows"]] == [
        (1001, "com.example.order.OrderService"),
        (1002, "com.example.order.OrderService"),
    ]
    assert out["rows"][0]["interface_class_names"] == ["java.io.Serializable"]


@pytest.mark.asyncio
async def test_loaded_classes_server_refusal_mentions_60k_limit_but_disconnect_does_not():
    refuse = {
        f"/api-v2/loaded-class/{DOMAIN}/1001": httpx.Response(500, text="too many classes"),
        f"/api-v2/loaded-class/{DOMAIN}/1002": httpx.Response(500, text="too many classes"),
    }
    with pytest.raises(ApmError) as exc:
        await manage(w7_handler(override=refuse)).apm_config(
            "loaded_classes", hostname="was-host01"
        )
    assert exc.value.code == "apm_api_error" and "6만 개" in exc.value.reason
    partial = await manage(
        w7_handler(override={f"/api-v2/loaded-class/{DOMAIN}/1001": refuse[
            f"/api-v2/loaded-class/{DOMAIN}/1001"
        ]})
    ).apm_config("loaded_classes", hostname="was-host01")
    assert partial["partial"] is True and any("6만 개" in x for x in partial["limits"])
    down = httpx.Response(
        500, text="500 500 DataServerDownException: 1000 Domain is not connected"
    )
    with pytest.raises(ApmError) as exc:
        await manage(
            w7_handler(override={p: down for p in refuse})
        ).apm_config("loaded_classes", hostname="was-host01")
    assert exc.value.code == "source_unavailable" and "6만 개" not in exc.value.reason


@pytest.mark.asyncio
async def test_rdb_export_rows():
    out = await manage(w7_handler()).apm_config("rdb_export")
    assert out["rows"] == [
        {
            "source_id": "default",
            "export_id": "311d6aaa",
            "export_date": "2026-10-01",
            "status": "COMPLETED",
            "extra": {},
        }
    ]
    assert any("E-29" in x for x in out["limits"])


# ── apm_environment ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_environment_keeps_every_key_masks_only_secret_values():
    seen: list = []
    out = await manage(w7_handler(seen=seen)).apm_environment(hostname="was-host01")
    assert [p for p, _ in _paths(seen, "/api-v2/environment-variable/")] == [
        f"/api-v2/environment-variable/{DOMAIN}"
    ]
    got = {(r["instance_id"], r["scope"], r["name"]): r["value"] for r in out["rows"]}
    # 키를 골라 줄이지 않는다(D-299 ⑦) — 인스턴스 1001의 8개 · 1002의 2개 전부
    assert len(got) == 10
    assert got[(1001, "SYSTEM", "PATH")] == "/usr/local/bin:/usr/bin"
    assert got[(1001, "SYSTEM", "JAVA_HOME")] == "/opt/java/openjdk"
    assert got[(1001, "JAVA", "java.vendor")] == "Eclipse Adoptium"
    assert got[(1001, "SYSTEM", "DB_PW2")] == MASK
    assert got[(1001, "SYSTEM", "PGPASSWORD")] == MASK
    assert got[(1001, "JAVA", "db.password")] == MASK
    opts = got[(1001, "SYSTEM", "JAVA_OPTS")]
    assert S not in opts and "-Xmx1g" in opts and "-Dfile.encoding=UTF-8" in opts
    assert S not in got[(1001, "JAVA", "spring.datasource.url")]
    assert {r["instance_name"] for r in out["rows"]} == {"was01_a", "was01_b"}
    assert out["instance_resolution"]["matched"] is True
    assert S not in _dump(out)


@pytest.mark.asyncio
async def test_environment_scope_and_key_filters_are_local_and_case_insensitive():
    seen: list = []
    out = await manage(w7_handler(seen=seen)).apm_environment(
        hostname="was-host01", scope="java", key="VENDOR"
    )
    assert [(r["instance_id"], r["scope"], r["name"]) for r in out["rows"]] == [
        (1001, "JAVA", "java.vendor"),
        (1002, "JAVA", "java.vendor"),
    ]
    assert _paths(seen, "/api-v2/environment-variable/")[0][1] == {}  # 거르기는 로컬
    assert any("조건으로 거른 결과" in x for x in out["limits"])
    with pytest.raises(ApmError) as exc:
        await manage(w7_handler()).apm_environment(scope="ENV")
    assert exc.value.code == "invalid_argument"


@pytest.mark.asyncio
async def test_environment_host_filters_instances_and_reports_missing():
    mock = W7Mock()
    mock.bodies["/api-v2/environment-variable/{domainId}"] = (
        200,
        {
            "1001": {"SYSTEM": {"PATH": "/bin"}, "PROPS": {"x": "1"}, "note": "raw"},
            "1003": {"SYSTEM": {"PATH": "/other"}},  # 다른 호스트 인스턴스 — 걸러진다
        },
    )
    out = await manage(w7_handler(mock)).apm_environment(hostname="was-host01")
    assert {r["instance_id"] for r in out["rows"]} == {1001}
    assert ("note", None, "raw") in {(r["scope"], r["name"], r["value"]) for r in out["rows"]}
    assert "[한계] 환경변수 응답에 없는 인스턴스: 1002" in out["limits"]
    assert any("SYSTEM·JAVA 밖 묶음(PROPS, note)" in x and "E-13" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_environment_without_host_all_domains_partial_on_failure():
    handler = w7_handler(
        fixtures=two_domain_fixtures(),
        override={"/api-v2/environment-variable/2000": httpx.Response(500, text="boom")},
    )
    out = await manage(handler).apm_environment()
    assert {r["domain_id"] for r in out["rows"]} == {1000}
    assert out["partial"] is True and out["sources"][0]["status"] == "ok"
    assert any("환경변수 조회 실패(도메인 2000)" in x for x in out["limits"])


# ── apm_users ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_users_list_and_accounts_masked_password_removed():
    seen: list = []
    out = await manage(w7_handler(seen=seen)).apm_users()
    assert [p for p, _ in seen if p.startswith(("/api/auth", "/restapi"))] == [
        "/api/auth/userlist",
        "/restapi/users",
    ]
    assert [r["origin"] for r in out["rows"]] == ["user_list", "accounts", "accounts"]
    ul, acc, _ = out["rows"]
    # 이메일은 `@` 앞 앞 1자만 · 전화번호는 칸째(형식과 무관 — plans/134 W7 AUDIT-7 처분)
    assert (ul["user_id"], ul["user_name"], ul["email"], ul["phone_number"]) == (
        "c***",
        "C***",
        "c***@example.invalid",
        "<phone>",
    )
    assert (acc["user_id"], acc["group"], acc["allow_ip"]) == ("c***", "admin", "192.168.*.*")
    assert acc["extra"] == {"creationTime": 0, "lastLoginTime": 0}
    text = _dump(out)
    assert "password" not in text and S not in text and "canary" not in text.lower()
    assert set(out["_masked_fields"]) == {
        "user_id",
        "user_name",
        "email",
        "phone_number",
        "allow_ip",
    }
    assert any("G-11" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_single_account_and_missing_account_without_raw_id():
    seen: list = []
    out = await manage(w7_handler(seen=seen)).apm_users(user_id="canary")
    assert [p for p, _ in seen if p.startswith("/restapi")] == ["/restapi/user/canary"]
    assert [(r["origin"], r["user_id"], r["group"]) for r in out["rows"]] == [
        ("account", "c***", "admin")
    ]
    gone = await manage(w7_handler()).apm_users(user_id="nobody")
    assert gone["rows"] == [] and "partial" not in gone
    assert any("계정 n*** 없음" in x and "HTTP 404" in x for x in gone["limits"])
    assert "nobody" not in _dump(gone)


@pytest.mark.asyncio
async def test_account_error_reason_does_not_carry_raw_id():
    handler = w7_handler(override={"/restapi/user/kimcs01": httpx.Response(500, text="")})
    with pytest.raises(ApmError) as exc:
        await manage(handler).apm_users(user_id="kimcs01")
    assert "kimcs01" not in exc.value.reason and "k***" in exc.value.reason


@pytest.mark.asyncio
@pytest.mark.parametrize("uid", ["a b", "..", ".", "x.xml", "a/b", "%2e", "가나", "x" * 65])
async def test_bad_account_id_is_rejected_without_http(uid):
    seen: list = []
    with pytest.raises(ApmError) as exc:
        await manage(w7_handler(seen=seen)).apm_users(user_id=uid)
    assert exc.value.code == "invalid_argument"
    assert [p for p, _ in seen if p.startswith("/restapi")] == []


# ── apm_active_detail ────────────────────────────────────────


@pytest.mark.asyncio
async def test_active_detail_passes_active_ref_and_masks_fields():
    seen: list = []
    out = await manage(w7_handler(seen=seen)).apm_active_detail(
        domain_id=DOMAIN, txid="-8001", session_id=31001, thread_hash=-99001, hostname="was-host01"
    )
    assert _paths(seen, "/api-v2/active-service/") == [
        (
            f"/api-v2/active-service/detail/{DOMAIN}/-8001",
            {"sessionId": "31001", "threadHash": "-99001"},
        )
    ]
    row = out["rows"][0]
    assert (row["txid"], row["session_id"], row["thread_hash"]) == ("-8001", 31001, -99001)
    assert row["user_id"] == "k***" and row["guid"] == "guid-active-0001"
    assert S not in row["sql"] and "id = ?" in row["sql"]
    assert row["http_method"] == "POST"
    # 비밀 키 값은 공백까지 가린다(`&` 뒤까지 — plans/134 W7 AUDIT-4 처분 · 과잉 가림 허용)
    # → mask_query
    assert row["http_query"] == "user=<v>&password=<v>"
    assert row["extra"] == {"elapsedTime": 700000}
    assert out["limits"][0].startswith("[한계] 현재값 전용(실행 중 요청)")
    assert out["_masked_fields"] == ["user_id"]
    assert S not in _dump(out)


@pytest.mark.asyncio
async def test_active_detail_without_session_values_notes_e15_and_omits_keys():
    seen: list = []
    out = await manage(w7_handler(seen=seen)).apm_active_detail(domain_id=DOMAIN, txid=8001)
    assert _paths(seen, "/api-v2/active-service/")[0][1] == {}
    assert any("E-15" in x for x in out["limits"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [
        {"domain_id": None, "txid": "1"},
        {"domain_id": DOMAIN, "txid": None},
        {"domain_id": DOMAIN, "txid": "12a"},
        {"domain_id": DOMAIN, "txid": "1" * 21},
        {"domain_id": "-1", "txid": "1"},
        {"domain_id": DOMAIN, "txid": "1", "session_id": "x"},
    ],
)
async def test_active_detail_bad_ref_is_invalid_without_http(args):
    seen: list = []
    with pytest.raises(ApmError) as exc:
        await manage(w7_handler(seen=seen)).apm_active_detail(**args)
    assert exc.value.code == "invalid_argument" and _paths(seen, "/api-v2/") == []


@pytest.mark.asyncio
async def test_active_detail_domain_must_match_host():
    seen: list = []
    with pytest.raises(ApmError) as exc:
        await manage(w7_handler(seen=seen)).apm_active_detail(
            domain_id=2000, txid="1", hostname="was-host01"
        )
    assert exc.value.code == "profile_ref_mismatch" and _paths(seen, "/api-v2/") == []


@pytest.mark.asyncio
async def test_active_detail_404_is_error_with_finished_or_version_reason():
    mock = W7Mock()
    mock.not_found.add("/api-v2/active-service/detail/{domainId}/{txid}")
    with pytest.raises(ApmError) as exc:
        await manage(w7_handler(mock)).apm_active_detail(domain_id=DOMAIN, txid="1")
    assert exc.value.code == "apm_api_error"
    assert "이미 끝났거나" in exc.value.reason and "E-28" in exc.value.reason


# ── 다중 소스 ────────────────────────────────────────────────


def _two_source_manage(handler) -> ManageTools:
    from apm_gateway.application.sources import build_source_set
    from apm_gateway.application.tools import ApmTools

    from apm_gateway.config import load_config

    cfg = load_config(
        {
            "JENNIFER_SOURCES": '["bank", "common"]',
            "JENNIFER_BANK_API_URL": "http://bank.test",
            "JENNIFER_BANK_API_TOKEN": TOKEN,
            "JENNIFER_COMMON_API_URL": "http://common.test",
            "JENNIFER_COMMON_API_TOKEN": "tok-other",
            "JENNIFER_RATE_LIMIT_PER_SEC": "0",
        }
    )
    tools = ApmTools(build_source_set(cfg, transport=httpx.MockTransport(handler)), cfg)
    return ManageTools(tools)


@pytest.mark.asyncio
async def test_source_scope_one_source_down_is_partial_with_status():
    inner = w7_handler()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "common.test" and request.url.path == "/api-v2/manual-rdb-export":
            return httpx.Response(500, json={"exception": {"message": "down"}})
        return await inner(request)

    out = await _two_source_manage(handler).apm_config("rdb_export")
    assert [r["source_id"] for r in out["rows"]] == ["bank"]
    assert out["partial"] is True
    assert [(s["source_id"], s["status"]) for s in out["sources"]] == [
        ("bank", "ok"),
        ("common", "unavailable"),
    ]
    assert any("소스 common" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_active_detail_needs_source_id_with_two_sources():
    with pytest.raises(ApmError) as exc:
        await _two_source_manage(w7_handler()).apm_active_detail(domain_id=DOMAIN, txid="1")
    assert exc.value.code == "invalid_argument" and "source_id" in exc.value.reason
    out = await _two_source_manage(w7_handler()).apm_active_detail(
        domain_id=DOMAIN, txid="1", source_id="common"
    )
    assert out["rows"][0]["source_id"] == "common"
