"""plans/134 W5·W7 보안 감사(audit-gw · 2026-10-06) 재현 테스트.

제품 코드는 고치지 않았다. 지금 실패하는 재현은 `xfail(strict=True, reason="AUDIT-n")`로 두었다
(고치면 XPASS가 되어 strict로 실패하므로 그때 표지를 지운다). 표지가 없는 테스트는 감사가 확인한
성질(통과해야 정상)이다. 외부 네트워크 0 — `httpx.MockTransport`와 합성 본문만 쓴다.

2026-10-06 gw-fix: AUDIT-1~8·10~12를 고쳐 표지를 지웠다. AUDIT-9(예산 칸 위조)는 사용자 결정
사항이라 남는다. 단언을 바꾼 곳은 두 군데다 — AUDIT-8 큰따옴표 사례(처분: 큰따옴표는 식별자와 겹쳐
가리지 않고 `mask_pii`로 보완 → 이름 대신 이메일로 시험)와 1MB 비용 시험의 `looks_like_sql`(처분으로
지운 함수 → 달러 따옴표 공격 문자열로 바꿈).

「가린다」 단언은 `[가림]` 표지가 아니라 **카나리아 원값이 출력에 없는지**로 한다(2026-10-02 실수
이력 W0-B).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx
import pytest
from apm_gateway.adapters.jennifer.allowlist import (
    ALLOWED,
    NotAllowedError,
    build_path,
    match_template,
)
from apm_gateway.application.manage_tools import ManageTools
from apm_gateway.application.masking import mask_pii, mask_query, mask_sql
from apm_gateway.domain.errors import RATE_LIMITED, ApmError
from conftest import DOMAIN, NOW_MS, make_tools, synthetic_handler
from mock_openapi import W7Mock
from test_plan134_w7_tools import w7_handler

C = "CANARY-audit-7Hq2"  # 어디에도 나오면 안 되는 값

_ENV = "/api-v2/environment-variable/{domainId}"
_ERROR_RULES = "/api-v2/manage/rule/event/error/{domainId}"
_ACTIVE = "/api-v2/active-service/detail/{domainId}/{txid}"
_USERLIST = "/api/auth/userlist"


def _manage(mock: W7Mock, *, seen=None, override=None) -> ManageTools:
    tools, _ = make_tools(
        "http://apm.test",
        transport=httpx.MockTransport(w7_handler(mock, seen=seen, override=override)),
    )
    return ManageTools(tools)


def _dump(out: dict) -> str:
    return json.dumps(out, ensure_ascii=False)


def _error_rule(script: str) -> list[dict]:
    return [
        {
            "errorType": "OUTOFMEMORY",
            "level": "FATAL",
            "applied": True,
            "checkTimeRange": 60000,
            "thresholdErrorCount": 1,
            "iconRecoveryTime": 300000,
            "customMessage": "",
            "autoScriptCommand": script,
        }
    ]


async def _env_value(name: str, value: str) -> dict:
    mock = W7Mock(secret=C)
    mock.bodies[_ENV] = (200, {"1001": {"SYSTEM": {name: value}}})
    return await _manage(mock).apm_environment(hostname="was-host01")


# ── AUDIT-1 (High) — 룰 autoScriptCommand의 명령줄 비밀번호 표기 ──────────────


@pytest.mark.parametrize(
    "script",
    [
        f"db2 connect to POLESTAR user db2inst1 using {C}",
        f"sqlcmd -S dbhost -U sa -P {C} -Q \"select 1\"",
        f"isql -U sa -P{C} -S dbhost",
        f"redis-cli -h cache01 -a {C} ping",
        f"ldapsearch -x -D cn=admin,dc=corp -w {C} -b dc=corp",
        f"lftp -u deploy,{C} sftp://files01",
        f"smbclient //nas01/share -U deploy%{C}",
    ],
    ids=["db2-using", "sqlcmd-P", "isql-P", "redis-a", "ldap-w", "lftp-u", "smb-U"],
)
@pytest.mark.asyncio
async def test_audit1_rule_script_password_flags_are_masked(script):
    mock = W7Mock(secret=C)
    mock.bodies[_ERROR_RULES] = (200, _error_rule(script))
    out = await _manage(mock).apm_config(
        kind="event_rules", hostname="was-host01", rule_type="error"
    )
    assert "error" not in out, out.get("reason")
    assert C not in _dump(out)


@pytest.mark.asyncio
async def test_audit1_rule_script_password_reaches_spool_file(tmp_path):
    """같은 원값이 승격 작업의 결과 파일과 MCP 반환까지 간다(종단)."""
    from apm_gateway.application.jobs import JobManager
    from apm_gateway.application.spool import Spool
    from apm_gateway.interface.server import audit_job_finished, create_server

    from apm_gateway.config import JobConfig

    mock = W7Mock(secret=C)
    mock.bodies[_ERROR_RULES] = (
        200,
        _error_rule(f"db2 connect to POLESTAR user db2inst1 using {C}") * 2,
    )
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(w7_handler(mock)))
    cfg = JobConfig(spool_dir=tmp_path / "spool", inline_rows=1)
    jobs = JobManager(
        Spool(cfg.spool_dir),
        cfg,
        envelope=tools.ok,
        error_envelope=tools.err,
        on_finish=audit_job_finished,
    )
    mcp = create_server(tools, jobs=jobs)
    result = await mcp.call_tool(
        "apm_config",
        {
            "kind": "event_rules",
            "hostname": "was-host01",
            "rule_type": "error",
            "owner": "o",
        },
    )
    blocks = result[0] if isinstance(result, tuple) else result
    spooled = "".join(
        p.read_text(encoding="utf-8") for p in (tmp_path / "spool").rglob("*") if p.is_file()
    )
    assert spooled, "결과 파일이 만들어져야 한다(inline_rows=1)"
    assert C not in blocks[0].text and C not in spooled


# ── AUDIT-2 (Medium) — 비밀 키 이름 판정 우회 ─────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "googleAPIkey",
        "PRIVATEkey",
        "RSAprivateKey",
        "dbPASSword",
        "DB_CREDS",
        "PASSCODE",
        "비밀번호",
    ],
)
@pytest.mark.asyncio
async def test_audit2_secret_key_names_mask_values(name):
    out = await _env_value(name, C)
    assert C not in _dump(out)


@pytest.mark.asyncio
async def test_audit2_jvm_property_with_acronym_key_is_masked():
    out = await _env_value("JAVA_OPTS", f"-Xmx1g -Dgoogle.APIkey={C} -Dfile.encoding=UTF-8")
    assert C not in _dump(out)


# ── AUDIT-3 (Medium) — 키는 평범하고 값에만 자격증명 ─────────────────────────


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DB_CONN", f"scott/{C}@ORCL"),
        ("CONFIG_REPO", f"https://{C}@git01:8443/ops/config.git"),
        ("spring.datasource.url", f"jdbc:mysql://app:12/{C}@db01:3306/x"),
    ],
    ids=["oracle-connect", "token-userinfo", "port-like-password"],
)
@pytest.mark.asyncio
async def test_audit3_credentials_inside_plain_values(name, value):
    out = await _env_value(name, value)
    assert C not in _dump(out)


# ── AUDIT-4 (Medium) — `[가림]` 다음 원값(구분자에서 끊김) ─────────────────────


@pytest.mark.parametrize(
    "value",
    [
        f"-Xmx1g -Ddb.password=Ab1,{C}",
        f"-Ddb.password=Ab1;{C}",
        f"password=Ab1,{C}",
        f'password="Ab1\\"{C}"',
    ],
    ids=["jvm-comma", "jvm-semicolon", "kv-comma", "escaped-quote"],
)
@pytest.mark.asyncio
async def test_audit4_masked_value_does_not_leave_a_tail(value):
    out = await _env_value("JAVA_OPTS", value)
    assert C not in _dump(out)


# ── AUDIT-5 (Low) — 인식하지 못하는 이름/값 묶음 모양 ─────────────────────────


@pytest.mark.parametrize(
    "extra",
    [
        {"params": [{"paramName": "password", "paramValue": C}]},
        {"headers": [{"headerName": "Authorization", "headerValue": f"Basic {C}"}]},
        {"form": [{"field": "pwd", "value": C}]},
    ],
    ids=["paramName", "headerName", "field"],
)
@pytest.mark.asyncio
async def test_audit5_unrecognized_name_value_pairs_in_active_detail(extra):
    mock = W7Mock(secret=C)
    mock.bodies[_ACTIVE] = (200, {"userId": "kimcs01", "guid": "g", **extra})
    out = await _manage(mock).apm_active_detail(domain_id=DOMAIN, txid="1")
    assert C not in _dump(out)


# ── AUDIT-6 (Low) — 오류 본문의 구조 묶음은 텍스트로만 검사된다 ─────────────────


@pytest.mark.parametrize(
    "body",
    [
        {"error": "bad config", "detail": [{"name": "db.password", "value": C}]},
        {"error": "bad config", "detail": ["db.password", C]},
    ],
    ids=["name-value", "pair-array"],
)
@pytest.mark.asyncio
async def test_audit6_error_body_pairs_do_not_reach_reason(body):
    mock = W7Mock(secret=C)
    bad = {f"/api-v2/environment-variable/{DOMAIN}": httpx.Response(500, json=body)}
    with pytest.raises(ApmError) as exc:  # 유일한 도메인 실패 = 오류(도구 봉투의 reason이 된다)
        await _manage(mock, override=bad).apm_environment(hostname="was-host01")
    assert exc.value.code == "apm_api_error"
    assert C not in exc.value.reason


# ── AUDIT-7 (Medium) — 사용자 칸 개인정보: 정규식 밖 형식이 원값으로 ──────────────


@pytest.mark.parametrize(
    ("field", "raw"),
    [
        ("phoneNumber", "+82-10-1234-5678"),
        ("phoneNumber", "02-3456-7890"),
        ("phoneNumber", "010.1234.5678"),
        ("email", "홍길동@회사.한국"),
        ("email", "gildong.hong@corpmail"),
    ],
    ids=["intl", "landline", "dots", "idn", "no-tld"],
)
@pytest.mark.asyncio
async def test_audit7_user_pii_fields_are_masked_whatever_the_format(field, raw):
    mock = W7Mock(secret=C)
    user = {"id": "kim01", "name": "홍길동", "email": "a@b.example", "phoneNumber": "", field: raw}
    mock.bodies[_USERLIST] = (200, {"result": [user]})
    mock.bodies["/restapi/users"] = (200, [])
    out = await _manage(mock).apm_users()
    assert "error" not in out, out.get("reason")
    assert raw not in _dump(out)


@pytest.mark.asyncio
async def test_audit7_identifier_like_extra_keys_are_masked():
    mock = W7Mock(secret=C)
    mock.bodies[_ACTIVE] = (
        200,
        {"userId": "kimcs01", "guid": "g", "clientId": "kimcs01-device", "userName": "홍길동"},
    )
    out = await _manage(mock).apm_active_detail(domain_id=DOMAIN, txid="1")
    text = _dump(out)
    assert "kimcs01-device" not in text and "홍길동" not in text


# ── AUDIT-8 (Low~Medium) — 프로파일 SQL 「SQL로 보이지 않는 문자열」 규칙 우회 ───────


@pytest.mark.asyncio
async def test_audit8_bind_value_with_sql_keyword_is_not_passed_through():
    body = {"result": [{"sql": "select 1", "sqlParams": ["배송 from 홍길동 gildong@corp.example"]}]}
    handler = synthetic_handler(override={"/api/transaction/sql": httpx.Response(200, json=body)})
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler))
    out = await tools.apm_transaction_profile(
        "was-host01", DOMAIN, "9000", NOW_MS, include_param_key=True, principal="chat"
    )
    text = _dump(out)
    assert "홍길동" not in text and "gildong@corp.example" not in text


@pytest.mark.parametrize(
    ("sql", "raw"),
    [
        # 처분: 큰따옴표는 DB2·PG 식별자와 겹쳐 가리지 않는다 — 리터럴 밖 개인정보는 mask_pii로
        ('select * from member where email = "gildong@corp.example"', "gildong@corp.example"),
        ("select $$홍길동$$ as n", "홍길동"),
    ],
    ids=["double-quoted", "dollar-quoted"],
)
def test_audit8_mask_sql_covers_other_literal_quotes(sql, raw):
    assert raw not in mask_sql(sql)
    assert mask_sql('select a from "ORDERS" where b = 1') == 'select a from "ORDERS" where b = ?'


def test_audit8_mask_query_bare_items():
    assert "010-1234-5678" not in mask_query("a=1&010-1234-5678")


# ── AUDIT-9 (Medium · 수용된 잔여 위험 재확인) — 프로파일 예산 칸 고르기 ─────────


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-9: 예산 칸 = (주체, LLM 인자) — investigation_id를 매번 바꾸면 조사 주체도"
    " 무제한",
)
@pytest.mark.asyncio
async def test_audit9_rotating_investigation_id_does_not_reset_budget():
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(synthetic_handler()))
    limited = 0
    for i in range(12):
        try:
            await tools.apm_transaction_profile(
                "was-host01",
                DOMAIN,
                "9000",
                NOW_MS,
                principal="investigation",
                investigation_id=f"inv-{i}",
            )
        except ApmError as e:
            assert e.code == RATE_LIMITED
            limited += 1
    assert limited > 0  # 주체 단위 총량이 있으면 어딘가에서 막혀야 한다


@pytest.mark.asyncio
async def test_budget_concurrent_calls_on_one_cell_allow_exactly_five():
    """같은 칸 동시 6회 — 소비가 await 없이 원자적이라 정확히 1회만 `rate_limited`(통과 확인)."""
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(synthetic_handler()))

    async def one() -> str:
        try:
            await tools.apm_transaction_profile(
                "was-host01",
                DOMAIN,
                "9000",
                NOW_MS,
                principal="investigation",
                investigation_id="inv-race",
            )
        except ApmError as e:
            return e.code
        return "ok"

    results = await asyncio.gather(*(one() for _ in range(6)))
    assert sorted(results) == ["ok"] * 5 + [RATE_LIMITED]


# ── AUDIT-10 (Low) — 계정 ID 원값이 WARNING 로그에 ─────────────────────────────


@pytest.mark.asyncio
async def test_audit10_contract_violation_log_hides_account_id(caplog):
    caplog.set_level(logging.WARNING)
    mock = W7Mock(secret=C)
    body = {"exception": {"message": "Required request parameter 'x' is not present"}}
    bad = {"/restapi/user/gildong77": httpx.Response(500, json=body)}
    with pytest.raises(ApmError) as exc:
        await _manage(mock, override=bad).apm_users(user_id="gildong77")
    assert exc.value.code == "contract_violation"
    assert "gildong77" not in exc.value.reason  # 오류 사유는 가렸다(통과 부분)
    assert "gildong77" not in caplog.text


# ── AUDIT-11 (Info) — 허용목록 `$`는 끝 개행을 받는다 ─────────────────────────


def test_audit11_match_template_rejects_trailing_newline():
    assert match_template("/api/auth/userlist\n") is None


def test_build_path_blocks_newline_and_encoded_variants_in_new_vars():
    """통과 확인 — 경로 변수 형식이 fullmatch라 개행·인코딩·구분자를 경로로 넣을 수 없다."""
    for value in ("kim\n", "kim%0a", "kim;jsessionid=1", "kim/../users", "kim?x=1", "kim#a"):
        with pytest.raises(NotAllowedError):
            build_path("/restapi/user/{id}", {"id": value})
    for value in ("1\n", "-", "--1", "1e3", "１２"):  # 전각 숫자
        with pytest.raises(NotAllowedError):
            build_path(_ACTIVE, {"domainId": 1000, "txid": value})
    assert len(ALLOWED) == 41  # plans/134 W3·W4 +4


@pytest.mark.asyncio
async def test_query_values_cannot_smuggle_keys_or_token():
    """통과 확인 — LLM이 채우는 쿼리 값(`search`·`hostname`·`guid`)에 `&token=`을 넣어도 httpx가
    인코딩해 선언 키만 나간다."""
    seen: list[tuple[str, dict[str, str]]] = []
    manage = _manage(W7Mock(secret=C), seen=seen)
    await manage.apm_config(kind="loaded_classes", hostname="was-host01", search="Order&token=x")
    await manage.apm_config(kind="process_instance", process_id=4242, hostname="h&token=x&a=b")
    with pytest.raises(ApmError):  # 합성 픽스처에 GUID 응답이 없다 — 요청 모양만 본다
        await manage.core.apm_transaction_trace("g1&token=x", hostname="was-host01")
    w7 = [(p, q) for p, q in seen if p.startswith(("/api-v2/", "/api/transaction/guid"))]
    assert w7 and all("token" not in q for _, q in w7)
    queries = {p.split("/")[2] if p.startswith("/api-v2") else p: q for p, q in w7}
    assert queries["loaded-class"] == {"search": "Order&token=x"}
    assert queries["manage"] == {"processId": "4242", "hostname": "h&token=x&a=b"}
    assert queries["/api/transaction/guid"]["guid"] == "g1&token=x"


@pytest.mark.asyncio
async def test_compare_is_asked_again_only_on_404():
    """통과 확인 — `comparing` 재질의는 404일 때만(500·3xx는 다른 경로로 새지 않는다)."""
    for status in (500, 302):
        seen: list[tuple[str, dict[str, str]]] = []
        override = {
            f"/api-v2/manage/rule/event/compare/{DOMAIN}/{t}": httpx.Response(
                status, headers={"location": "/api-v2/manage/rule/event/comparing/1000/domain"}
            )
            for t in ("domain", "instance")
        }
        with pytest.raises(ApmError):  # 두 대상 모두 실패 = 오류(0건으로 강등하지 않는다)
            await _manage(W7Mock(secret=C), seen=seen, override=override).apm_config(
                kind="event_rules", hostname="was-host01", rule_type="compare"
            )
        assert not any("/comparing/" in p for p, _ in seen), status


@pytest.mark.parametrize(
    ("fn", "attack"),
    [
        (mask_pii, "a@" * 500_000),
        (mask_pii, "010-" * 250_000),
        (mask_pii, "a." * 500_000 + "@x"),
        (mask_sql, "$a$" * 333_000),
        (mask_sql, "'" + "a" * 1_000_000),
        (mask_query, "a=&" * 333_000),
    ],
    ids=["pii-at", "pii-phone", "pii-dots", "sql-dollar", "sql-open-quote", "query"],
)
def test_new_masking_functions_are_linear_on_1mb(fn, attack):
    """통과 확인 — W7에서 새로 쓰는 마스킹 함수의 1MB 공격 문자열 비용."""
    started = time.monotonic()
    fn(attack)
    assert time.monotonic() - started < 2.0


# ── AUDIT-12 (Low) — 소스 범위 일부 실패의 `sources[].reason`은 mask_text를 거치지 않는다 ──


@pytest.mark.asyncio
async def test_audit12_partial_source_reason_is_masked_like_limits():
    from test_plan134_w7_tools import _two_source_manage

    inner = w7_handler()
    echoed = "export denied for gildong.hong@corp.example tel 010-1234-5678"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "common.test" and request.url.path == "/api-v2/manual-rdb-export":
            return httpx.Response(500, text=echoed)
        return await inner(request)

    out = await _two_source_manage(handler).apm_config("rdb_export")
    assert out["partial"] is True
    assert all("gildong.hong@corp.example" not in x for x in out["limits"])  # limits는 가렸다
    assert "gildong.hong@corp.example" not in _dump(out["sources"])
