"""plans/134 W5·W7 재감사(audit-gw · 2026-10-06) — gw-fix 수정본을 새 우회 입력으로 다시 공격한다.

제품 코드는 고치지 않았다. 지금 새는 재현은 `xfail(strict=True, reason="REAUDIT-n")`로 둔다.
2026-10-06 gw-fix 2차: REAUDIT-1·2·4·7을 고쳐 표지를 지웠다. REAUDIT-3·5·6은 팀 리드 처분으로
고치지 않고 스펙 잔여로 문서화한다(표지 유지 · 사유에 처분 표기). 표지가
없는 테스트는 재감사가 확인한 성질(통과해야 정상)이다 — 과잉 가림이 일반 진단 값을 훼손하지 않는지와
새 정규식 비용. 외부 네트워크 0 — `httpx.MockTransport`와 합성 본문만 쓴다.

「가린다」 단언은 `[가림]` 표지가 아니라 **카나리아 원값이 출력에 없는지**로 한다(W0-B).
"""

from __future__ import annotations

import json
import logging
import time

import httpx
import pytest
from apm_gateway.application.manage_tools import ManageTools
from apm_gateway.application.masking import mask_query, mask_sql
from apm_gateway.domain.credentials import scrub_detail, scrub_text
from conftest import make_tools
from mock_openapi import W7Mock
from test_plan134_w7_tools import w7_handler

C = "CANARY-reaudit-Vn8"

_ENV = "/api-v2/environment-variable/{domainId}"


def _manage(mock: W7Mock) -> ManageTools:
    tools, _ = make_tools(
        "http://apm.test", transport=httpx.MockTransport(w7_handler(mock))
    )
    return ManageTools(tools)


def _dump(out: object) -> str:
    return json.dumps(out, ensure_ascii=False)


async def _env_value(name: str, value: str) -> dict:
    mock = W7Mock(secret=C)
    mock.bodies[_ENV] = (200, {"1001": {"SYSTEM": {name: value}}})
    return await _manage(mock).apm_environment(hostname="was-host01")


# ── REAUDIT-1 (Medium) — 명령줄 값이 중첩 객체/단일 토큰이면 첫 토큰 규칙을 비껴간다 ──────


@pytest.mark.parametrize(
    "obj",
    [
        {"autoScriptCommand": {"exe": "/opt/x.sh", "arg": C}},
        {"command": {"args": C}},
        {"cmd": {"0": "/opt/x.sh", "1": C}},
    ],
    ids=["nested-arg", "args-object", "numbered-map"],
)
def test_reaudit1_command_value_as_object_is_masked(obj):
    out, _, _ = scrub_detail(obj)
    assert C not in _dump(out)


# ── REAUDIT-2 (Medium) — 명령 키가 아닌 칸의 셸 명령은 -P·-a·DB2 using을 못 잡는다 ──────


@pytest.mark.parametrize(
    "command",
    [
        f"db2 connect to POLESTAR user db2inst1 using {C}",
        f"sqlcmd -S db01 -U sa -P {C} -Q x",
        f"redis-cli -h cache01 -a {C} ping",
        f"isql -U sa -P{C} -S db01",
        f"ldapsearch -x -D cn=admin -w {C} -b dc=corp",
    ],
    ids=["db2-using", "sqlcmd-P", "redis-a", "isql-P", "ldap-w"],
)
@pytest.mark.asyncio
async def test_reaudit2_shell_command_under_plain_key(command):
    out = await _env_value("STARTUP_HOOK", command)
    assert "error" not in out, out.get("reason")
    assert C not in _dump(out)


# ── REAUDIT-3 (Low · gw-fix가 밝힌 잔여) — 접속 문자열이 값 전체가 아니면 안 가린다 ──────


@pytest.mark.xfail(
    strict=True,
    reason="REAUDIT-3: _mask_connect_string은 값 전체 일치만 — 문장 안이나 `url=` 접두가 붙은"
    " 접속 문자열(scott/pw@db)은 남는다(경로 오탐 방지로 좁힌 쪽) — 잔여(문서화) — 팀 리드 처분",
)
@pytest.mark.parametrize(
    "value",
    [
        f"connect as scott/{C}@ORCL then run",
        f"endpoints: scott/{C}@a, tiger/{C}@b",
        f"primary=scott/{C}@db",
    ],
    ids=["sentence", "list", "prefixed"],
)
@pytest.mark.asyncio
async def test_reaudit3_connect_string_not_whole_value(value):
    out = await _env_value("DB_ENDPOINTS", value)
    assert C not in _dump(out)


# ── REAUDIT-4 (Low) — 키 이름 어휘의 남은 구멍 ─────────────────────────────────


@pytest.mark.parametrize("name", ["x509Key", "cred", "시크릿", "clientAssertion"])
@pytest.mark.asyncio
async def test_reaudit4_key_vocabulary_gap(name):
    out = await _env_value(name, C)
    assert C not in _dump(out)


# ── REAUDIT-5 (Low · gw-fix가 밝힌 잔여) — 줄 중간 따옴표 없는 비밀에 공백이 있으면 꼬리 ──


@pytest.mark.xfail(
    strict=True,
    reason="REAUDIT-5: 줄 머리가 아닌 KV는 공백까지만 가린다(처분) — 비밀 값에 공백이 있으면"
    " 두 번째 토큰부터 남는다 — 잔여(문서화) — 팀 리드 처분",
)
def test_reaudit5_midline_unquoted_secret_with_space():
    out = scrub_text(f"start app with login password=my {C} and go")
    assert C not in out


# ── REAUDIT-6 (Low) — mask_sql의 남은 리터럴(큰따옴표·주석)에 비ASCII 이름 ──────────────


@pytest.mark.xfail(
    strict=True,
    reason="REAUDIT-6: mask_sql은 큰따옴표 문자열·주석을 가리지 않고 mask_pii(이메일·주민·휴대폰)"
    "로만 보완한다 — 사람 이름 같은 개인정보는 그 정규식 밖이라 남는다(G-11 이름 잔여)"
    " — 잔여(문서화) — 팀 리드 처분",
)
@pytest.mark.parametrize(
    "sql",
    [
        'select * from member where name = "홍길동"',
        "select id from member /* 담당: 홍길동 */",
    ],
    ids=["double-quote-name", "comment-name"],
)
def test_reaudit6_mask_sql_name_in_other_literals(sql):
    assert "홍길동" not in mask_sql(sql)


# ── REAUDIT-7 (Medium) — quiet_http_loggers가 MCP 전송 로거를 못 덮는다 ───────────────


def test_reaudit7_quiet_http_loggers_covers_mcp_transport():
    from apm_gateway.__main__ import quiet_http_loggers

    root = logging.getLogger()
    saved = root.level
    saved_levels = {
        name: logging.getLogger(name).level
        for name in ("httpx", "mcp.server.sse", "mcp.server.lowlevel.server")
    }
    try:
        root.setLevel(logging.DEBUG)
        for name in saved_levels:
            logging.getLogger(name).setLevel(logging.NOTSET)
        quiet_http_loggers()
        # httpx는 덮지만(기대 WARNING 이상) MCP 전송 로거는 DEBUG 본문을 그대로 찍는다
        assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
        assert logging.getLogger("mcp.server.sse").getEffectiveLevel() >= logging.WARNING
    finally:
        root.setLevel(saved)
        for name, level in saved_levels.items():
            logging.getLogger(name).setLevel(level)


# ── 과잉 가림 회귀 — 일반 진단 값이 훼손되지 않는가(통과해야 정상) ────────────────────


@pytest.mark.asyncio
async def test_general_config_values_are_preserved():
    """포트·JVM 메모리 옵션·plain URL·비밀 없는 JDBC·DB 경로·PWD 경로·버전은 그대로 나와야 한다."""
    keep = {
        "PATH": "/usr/local/bin:/usr/bin",
        "JAVA_HOME": "/opt/java/openjdk",
        "HEAP": "-Xmx4g -Xms2g -XX:+UseG1GC",
        "SERVER": "-Dserver.port=8080 -Dfile.encoding=UTF-8",
        "HEALTH_URL": "http://config01:8080/app/health",
        "JDBC_URL": "jdbc:postgresql://db01:5432/polestar?ssl=true",
        "DB_DIR": "/data/jennifer/db/main",
        "PWD": "/home/was/app",
        "OLDPWD": "/home/was",
        "VERSION": "5.6.0.21",
        "OTEL": "grpc",
        "CRON": "0 0 * * *",
    }
    out = await _env_value_many(keep)
    got = {(r["scope"], r["name"]): r["value"] for r in out["rows"]}
    for name, value in keep.items():
        assert got[("SYSTEM", name)] == value, name


async def _env_value_many(values: dict[str, str]) -> dict:
    mock = W7Mock(secret=C)
    mock.bodies[_ENV] = (200, {"1001": {"SYSTEM": dict(values)}})
    return await _manage(mock).apm_environment(hostname="was-host01")


def test_mask_sql_keeps_statement_and_identifiers():
    """통과 확인 — mask_sql은 리터럴만 바꾸고 문/식별자(큰따옴표 식별자 포함)는 남긴다."""
    assert mask_sql("select name, port from config where id = 1") == (
        "select name, port from config where id = ?"
    )
    assert mask_sql('select "COL" from "SCHEMA"."TAB"') == 'select "COL" from "SCHEMA"."TAB"'
    assert mask_sql("select count(*) from t") == "select count(*) from t"


def test_mask_query_keeps_flag_names_and_keys():
    """통과 확인 — mask_query는 키·플래그 이름을 남기고 값만 가린다."""
    assert mask_query("page=2&size=10") == "page=<v>&size=<v>"
    assert mask_query("debug&verbose") == "debug&verbose"


@pytest.mark.parametrize(
    ("label", "attack", "fn"),
    [
        ("command-head", "/x.sh " + "a " * 400_000, lambda s: scrub_text(s, command=True)),
        ("connect-fullmatch", "a/" + "x" * 900_000 + "@db", scrub_text),
        ("userinfo-dense", "a://b@" * 160_000, scrub_text),
        ("userinfo-port", "a://b:1/" * 120_000, scrub_text),
        ("kv-line", "password=" + "a " * 300_000, scrub_text),
        ("kv-conn", "x;password=" + "a;k=" * 200_000, scrub_text),
        ("jvm-comma", "-Ddb.password=" + "a," * 300_000, scrub_text),
        ("esc-quote-open", 'password="' + "a" * 1_000_000, scrub_text),
        ("flat-key", None, None),
        ("dollar-sql", "$a$" * 333_000, mask_sql),
    ],
)
def test_new_rules_are_linear_on_1mb(label, attack, fn):
    """통과 확인 — gw-fix가 새로 넣거나 넓힌 규칙의 1MB 공격 문자열 비용(선형 · 제곱 시간 회귀)."""
    started = time.monotonic()
    if label == "flat-key":
        scrub_detail({"K" * 1_000_000 + "PASSWORD": "v"})
    else:
        fn(attack)
    assert time.monotonic() - started < 2.5, f"{label}: {time.monotonic() - started:.2f}s"
