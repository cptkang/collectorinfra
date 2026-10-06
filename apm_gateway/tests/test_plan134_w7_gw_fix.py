"""plans/134 W7 결함 수정(gw-fix · 2026-10-06) — 처분 경계·선형성·운영 엔트리 로그 고정.

감사(`test_plan134_w7_security_audit.py`)·검증(`test_plan134_w567_verify.py`) 재현은 그 파일에서
xfail 표지를 지워 통과시켰다. 여기는 처분을 구현하며 정한 경계(무엇을 남기고 무엇을 가리는가)와
새 정규식의 1MB 공격 문자열 비용, 실프로세스 INFO 로그(VG-7)를 고정한다.

「가린다」 단언은 `[가림]` 표지가 아니라 카나리아 원값 부재로 한다(2026-10-02 실수 이력 W0-B).
외부 네트워크 0 — `httpx.MockTransport`와 127.0.0.1 임시 포트만 쓴다.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time

import httpx
import pytest
from apm_gateway.adapters.jennifer.client import JenniferClient
from apm_gateway.adapters.jennifer.fields import to_int
from apm_gateway.adapters.jennifer.manage_fields import parse_user_list
from apm_gateway.application.manage_tools import ManageTools, _is_person_key
from apm_gateway.application.masking import mask_query, mask_sql
from apm_gateway.domain.credentials import MASK, scrub, scrub_text
from apm_gateway.domain.errors import INVALID_ARGUMENT, SOURCE_UNAVAILABLE, ApmError
from conftest import DOMAIN, GATEWAY_ROOT, TOKEN, make_tools, synthetic_handler
from mock_openapi import W7Mock
from test_plan134_w7_tools import w7_handler

from apm_gateway.config import JenniferApiConfig

C = "CANARY-gwfix-4Rt8"


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


# ── AUDIT-1 — 명령줄 문맥: 첫 토큰(실행 파일)만 ─────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/opt/jennifer/restart.sh", "/opt/jennifer/restart.sh"),  # 인자 없음 — 그대로
        ("/opt/jennifer/restart.sh \n", "/opt/jennifer/restart.sh \n"),
        (f"/opt/jennifer/restart.sh -P {C}", "/opt/jennifer/restart.sh [가림]"),
        (f"db2 connect to SAMPLE user db2inst1 using {C}", "db2 [가림]"),
        (
            f'"C:\\Program Files\\app\\run.bat" /user:{C}',
            '"C:\\Program Files\\app\\run.bat" [가림]',
        ),
        (f"PGPASSWORD={C} /opt/x.sh", "PGPASSWORD=[가림] [가림]"),  # 환경변수 대입 접두
        ("LANG=C /opt/x.sh", "LANG=[가림] [가림]"),
        (f"https://ops:{C}@hook01/run", "https://ops:[가림]@hook01/run"),  # 첫 토큰도 일반 규칙
        (f"\n  /opt/x.sh {C}", "\n  /opt/x.sh [가림]"),
        (f'"unterminated {C}', MASK),  # 첫 토큰을 못 고르면 통째로
    ],
)
def test_command_context_keeps_only_the_executable(text, expected):
    assert scrub_text(text, command=True) == expected


def test_command_context_structures():
    out, _ = scrub(
        {
            "autoScriptCommand": ["/opt/x.sh", "-a", C],
            "command": json.dumps(["/opt/x.sh", "-w", C]),  # JSON으로 디코드하지 않는다
            "sun.java.command": f"org.example.Main --db.password={C}",
            "scriptPath": "/opt/scripts/restart.sh",
            "rule": {"name": "command", "value": f"/opt/x.sh -u deploy,{C}"},
        }
    )
    assert C not in _dump(out)
    assert out["autoScriptCommand"] == ["/opt/x.sh", MASK, MASK]
    assert out["sun.java.command"] == "org.example.Main [가림]"
    assert out["scriptPath"] == "/opt/scripts/restart.sh"
    assert out["rule"]["value"] == "/opt/x.sh [가림]"


def test_non_command_keys_keep_general_values():
    """명령줄 문맥 밖 일반 값은 그대로(팀 리드 확인 목록)."""
    body = {
        "PATH": "/usr/local/bin:/usr/bin",
        "JAVA_HOME": "/opt/java/openjdk",
        "java.vendor": "Eclipse Adoptium",
        "JAVA_OPTS": "-Xmx1g -Dfile.encoding=UTF-8 -Dport=8080",
    }
    assert scrub(body)[0] == body


# ── REAUDIT-1·2 (2차) — 명령 키 아래 객체 · 비명령 칸의 도구별 비밀번호 표기 ────────────


@pytest.mark.parametrize(
    "obj",
    [
        {"autoScriptCommand": {"exe": "/opt/x.sh", "arg": C}},
        {"command": {"args": C, "env": {"PGPASSWORD": C}}},
        {"cmd": [{"exe": "/opt/x.sh", "arg": C}]},  # 배열 첫 원소가 객체여도 통째
    ],
)
def test_command_value_object_is_masked_whole(obj):
    out, _ = scrub(obj)
    assert C not in _dump(out)


def test_command_object_keeps_non_secret_scalars_shape():
    out, _ = scrub({"autoScriptEnabled": True, "autoScriptCommand": {"exe": "/x.sh", "n": None}})
    assert out == {"autoScriptEnabled": True, "autoScriptCommand": {"exe": MASK, "n": None}}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            f"db2 connect to SAMPLE user db2inst1 using {C}",
            "db2 connect to SAMPLE user db2inst1 using [가림]",
        ),
        (f"db2 attach to inst1 user u1 using '{C}'", "db2 attach to inst1 user u1 using [가림]"),
        (f"sqlcmd -S db01 -U sa -P {C} -Q x", "sqlcmd -S db01 -U sa -P [가림] -Q x"),
        (f"isql -U sa -P{C} -S db01", "isql -U sa -P[가림] -S db01"),
        (f"redis-cli -h cache01 -a {C} ping", "redis-cli -h cache01 -a [가림] ping"),
        (f"ldapsearch -x -w {C} -b dc=corp", "ldapsearch -x -w [가림] -b dc=corp"),
        (f"lftp -u deploy,{C} sftp://files01", "lftp -u deploy,[가림] sftp://files01"),
        (f"smbclient //nas01/s -U deploy%{C}", "smbclient //nas01/s -U deploy%[가림]"),
    ],
)
def test_tool_password_forms_outside_command_keys(text, expected):
    assert scrub_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "ssh -p 22 host01",
        "-Xmx4g -Xms2g -XX:+UseG1GC",
        "-Dserver.port=8080 -Dfile.encoding=UTF-8",
        "-agentlib:jdwp=transport=dt_socket,server=y",
        "-agentpath:/opt/jennifer/agent/libjvmti.so",
        "ls -al /tmp",
        "select a from t join u using (id)",
        "user guide using tips",  # connect·attach 없는 문장
        "a - b · x-a y",
    ],
)
def test_tool_password_forms_keep_general_values(text):
    assert scrub_text(text) == text


# ── REAUDIT-4 (2차) — 키 어휘 · 일반 단어 ─────────────────────────────────


@pytest.mark.parametrize(
    ("key", "secret"),
    [
        ("x509Key", True),
        ("gpgKey", True),
        ("cred", True),
        ("dbCred2", True),
        ("시크릿", True),
        ("clientAssertion", True),
        ("INCREDIBLE", False),
        ("CREDIT", False),
        ("creditLimit", False),
        ("assertionConsumerServiceUrl", False),
        ("SunX509", False),
        ("javax.net.ssl.keyStore", False),
    ],
)
def test_key_vocabulary_second_round(key, secret):
    out, _ = scrub({key: "v1"})
    assert (out[key] == MASK) is secret


def test_quiet_loggers_cover_http_and_mcp_transport():
    from apm_gateway.__main__ import _QUIET_HTTP_LOGGERS, quiet_http_loggers

    # 부모 `mcp`를 올려 MCP 전송 하위 로거(수준 미설정 — 상속)를 덮는다(REAUDIT-7)
    watched = (
        *_QUIET_HTTP_LOGGERS,
        "mcp.server.sse",
        "mcp.server.lowlevel.server",
        "mcp.server.fastmcp",
    )
    root = logging.getLogger()
    saved_root = root.level
    saved = {name: logging.getLogger(name).level for name in watched}
    try:
        root.setLevel(logging.DEBUG)
        for name in saved:
            logging.getLogger(name).setLevel(logging.NOTSET)
        quiet_http_loggers()
        assert all(logging.getLogger(n).getEffectiveLevel() >= logging.WARNING for n in watched)
        # 게이트웨이 자기 로그는 그대로 둔다
        assert logging.getLogger("apm_gateway").getEffectiveLevel() == logging.DEBUG
    finally:
        root.setLevel(saved_root)
        for name, level in saved.items():
            logging.getLogger(name).setLevel(level)


# ── I-1 — POSIX 작업 디렉터리(PWD·OLDPWD) ────────────────────────────────


def test_posix_working_directory_is_kept():
    out, _ = scrub({"SYSTEM": {"PWD": "/home/app", "OLDPWD": "/tmp", "pwd": C, "Pwd": C}})
    assert out == {"SYSTEM": {"PWD": "/home/app", "OLDPWD": "/tmp"}}  # 소문자 계정 필드는 키째 제거
    assert scrub_text("PWD=/home/app\nOLDPWD=/srv") == "PWD=/home/app\nOLDPWD=/srv"
    assert scrub_text('{"OLDPWD": "/srv/x"}') == '{"OLDPWD": "/srv/x"}'


@pytest.mark.parametrize(
    "value",
    [
        {"PWD": C},  # 경로가 아니면 종전대로 비밀번호 필드(키째 제거)
        {"OLDPWD": C},
        {"pwd": "/home/app"},  # 소문자는 계정 필드 — 경로여도 제거
        f"DRIVER={{x}};SERVER=db01;UID=sa;PWD={C};",  # ODBC 연결 문자열
        f'{{"PWD": "{C}"}}',
    ],
)
def test_pwd_that_is_not_a_directory_stays_secret(value):
    out = scrub_text(value) if isinstance(value, str) else scrub(value)[0]
    assert C not in _dump(out) and "/home/app" not in _dump(out)


# ── AUDIT-3 — 값 단독 접속 문자열 · 콜론 없는 사용자 정보 ─────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"scott/{C}@ORCL", "scott/[가림]@ORCL"),
        (f"scott/p@{C}@//db01:1521/ORCL", "scott/[가림]@//db01:1521/ORCL"),  # 마지막 `@`까지
        ("/opt/app/a@b", "/opt/app/a@b"),  # 경로 — 전체 일치가 아니다
        (f"https://{C}@git01/x.git", "https://[가림]@git01/x.git"),
        ("ssh://deploy@host01/repo", "ssh://[가림]@host01/repo"),  # 사용자 이름도 가린다(처분)
        (f"https://{'t' * 600}{C}@git01/x", "https://[가림]@git01/x"),  # 긴 토큰
        ("http://host:8080/a?email=x@y.com", "http://host:8080/a?email=x@y.com"),
    ],
)
def test_userinfo_and_connect_string_values(text, expected):
    assert scrub_text(text) == expected


# ── AUDIT-4 — 따옴표 없는 비밀 값의 끝 ───────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 연결 문자열(`k=v;k=v`) — `;`까지(공백 포함)
        (f"Server=db;Password=my {C};Encrypt=true", "Server=db;Password=[가림];Encrypt=true"),
        (f"Server=db;Password=my {C}", "Server=db;Password=[가림]"),
        # 줄 머리 키(설정 파일 줄) — 줄 끝까지
        (f"db.password=my {C}", "db.password=[가림]"),
        (f"a=1\n  db.password = my {C}\nb=2", "a=1\n  db.password = [가림]\nb=2"),
        # 줄 중간 — 공백까지(`,` `&` `;`가 섞여도 꼬리를 남기지 않는다)
        (f"login password=x,{C} user=kim", "login password=[가림] user=kim"),
        (f"{{db.password=x;{C}, db.user=kim}}", "{db.password=[가림] db.user=kim}"),
        (f'cfg password="a\\"b\\"{C}" next=1', "cfg password=[가림] next=1"),
        (f"-Ddb.password=a,b;{C} -Dport=8080", "-Ddb.password=[가림] -Dport=8080"),
    ],
)
def test_secret_value_end(text, expected):
    assert scrub_text(text) == expected


# ── AUDIT-2 — 구분자를 걷은 전체 키 판정의 일반 키 ──────────────────────────


@pytest.mark.parametrize(
    "key",
    ["java.class.path", "user.home", "CLASSPATH", "KEYBOARD", "javax.net.ssl.keyStore", "monkey"],
)
def test_flat_key_rule_keeps_general_keys(key):
    assert scrub({key: "v1"})[0] == {key: "v1"}


# ── AUDIT-8 — mask_sql 달러 따옴표 · mask_query 맨 항목 ────────────────────


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("select $$a$$, $fn$b 'c'$fn$ from t", "select ?, ? from t"),
        ("select * from V$SESSION where sid = 1", "select * from V$SESSION where sid = ?"),
        ("select $x$ unterminated", "select ?"),  # 닫는 표지가 없으면 끝까지
        (
            'select "ID" from "ORDERS" where mail = "kim@corp.example"',
            'select "ID" from "ORDERS" where mail = "<email>"',
        ),
    ],
)
def test_mask_sql_literals(sql, expected):
    assert mask_sql(sql) == expected


def test_mask_query_bare_items():
    assert mask_query("a=1&flag&010-1234-5678&홍길동&&x.y") == "a=<v>&flag&<v>&<v>&&x.y"


# ── AUDIT-7 — 식별자형 extra 키 ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "person"),
    [
        ("clientId", True),
        ("USER_ID", True),
        ("userName", True),
        ("nickname", True),
        ("empNo", True),
        ("guid", False),
        ("valid", False),
        ("elapsedTime", False),
    ],
)
def test_person_key(key, person):
    assert _is_person_key(key) is person


@pytest.mark.asyncio
async def test_active_detail_nested_extra_identifiers_are_masked():
    mock = W7Mock(secret=C)
    mock.bodies["/api-v2/active-service/detail/{domainId}/{txid}"] = (
        200,
        {"userId": "kimcs01", "guid": "g", "client": {"id": "dev-778"}, "tags": ["platform"]},
    )
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(w7_handler(mock)))
    out = await ManageTools(tools).apm_active_detail(domain_id=DOMAIN, txid="1")
    extra = out["rows"][0]["extra"]
    assert extra == {"client": {"id": "d***"}, "tags": ["platform"]}


# ── O-5 · I-3 — 어댑터 이중 방어 · 숫자 칸 ──────────────────────────────────


def test_user_list_parser_does_not_carry_password():
    rows = parse_user_list({"result": [{"id": "kim", "password": C}]})
    assert rows is not None and C not in _dump(rows)


@pytest.mark.parametrize("raw", ["²", "Infinity", "NaN", "1e400", float("inf"), float("nan"), "x"])
def test_to_int_bad_source_number_is_none(raw):
    assert to_int(raw) is None


@pytest.mark.parametrize(
    ("raw", "number"), [("42", 42), ("-7", -7), (" 12 ", 12), (3.9, 3), ("1.5", 1)]
)
def test_to_int_keeps_numbers(raw, number):
    assert to_int(raw) == number


@pytest.mark.asyncio
async def test_bad_numeric_field_does_not_break_the_tool():
    mock = W7Mock(secret=C)
    mock.bodies["/api-v2/manage/rule/event/error/{domainId}"] = (
        200,
        [{"errorType": "OUTOFMEMORY", "level": "FATAL", "checkTimeRange": "²"}],
    )
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(w7_handler(mock)))
    out = await ManageTools(tools).apm_config(
        kind="event_rules", hostname="was-host01", rule_type="error"
    )
    assert "error" not in out
    row = out["rows"][0]
    assert row["check_time_range_ms"] is None and row["extra"]["checkTimeRange"] == "²"


# ── I-2 — 기간 비교 시각 범위 ─────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kw",
    [
        {"current_end": "9999-12-31T20:00:00"},
        {"baseline_start": "1969-12-31T00:00:00+00:00"},
        {"current_start": "0001-01-01T00:00:00+09:00"},
    ],
)
async def test_period_compare_out_of_range_time_is_invalid_argument(kw):
    calls: list[str] = []

    def count(request):
        calls.append(request.url.path)
        return synthetic_handler()(request)

    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(count))
    args = {
        "current_start": "2026-09-29T08:00:00",
        "current_end": "2026-09-29T09:00:00",
        "baseline_start": "2026-09-22T08:00:00",
        "baseline_end": "2026-09-22T09:00:00",
        **kw,
    }
    with pytest.raises(ApmError) as exc:
        await tools.apm_period_compare("was-host01", **args)
    assert exc.value.code == INVALID_ARGUMENT and "범위" in exc.value.reason and calls == []


# ── AUDIT-10 · VG-5 — 계정 ID는 사유·로그에 원값으로 남지 않는다 ───────────────


@pytest.mark.asyncio
async def test_account_timeout_reason_uses_template(caplog):
    caplog.set_level(logging.DEBUG, logger="apm_gateway")
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(_timeout_handler()))
    with pytest.raises(ApmError) as exc:
        await ManageTools(tools).apm_users(user_id="rawacct55")
    assert exc.value.code == SOURCE_UNAVAILABLE
    assert "rawacct55" not in exc.value.reason and "/restapi/user/{id}" in exc.value.reason
    assert "r***" in exc.value.reason
    assert "rawacct55" not in caplog.text


def _timeout_handler():
    inner = w7_handler()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/restapi/user/"):
            raise httpx.ConnectTimeout("timed out", request=request)
        return await inner(request)

    return handler


@pytest.mark.parametrize(
    ("message", "uid"),
    [
        ("user a.b-c@d not found", "a.b-c@d"),
        ("x" * 300 + " kim01: denied", "kim01"),
        ("kim01kim01 kim01", "kim01"),
    ],
)
def test_classify_hides_echoed_account_id_before_truncation(message, uid):
    client = JenniferClient(JenniferApiConfig(url="http://apm.test", token=TOKEN))
    body = json.dumps({"exception": {"message": message}})
    err = client._classify(500, body, "/restapi/user/{id}", [(uid, "{id}")])
    assert uid not in err.reason.replace("kim01kim01", "")
    assert len(err.reason) <= 240


# ── 1MB 공격 문자열 비용(새 정규식 — 2026-10-02 실수 이력 W0-B) ────────────────


_MB = 1_000_000


@pytest.mark.parametrize(
    ("fn", "attack"),
    [
        (lambda t: scrub_text(t, command=True), "a " * (_MB // 2)),
        (lambda t: scrub_text(t, command=True), "a" * _MB),
        (scrub_text, "a/" + "x@" * (_MB // 2)),  # 접속 문자열 전체 일치
        (scrub_text, "a/" + "@" * _MB),
        (scrub_text, "password=a " * (_MB // 11)),  # 연결 문자열 문맥 미리보기
        (scrub_text, "password=a;" * (_MB // 11)),
        (scrub_text, "x password=" + "a" * _MB),
        (scrub_text, 'password="' + '\\"' * (_MB // 2)),  # 이스케이프 따옴표(닫힘 없음)
        (scrub_text, "-Ddb.password=" + "a," * (_MB // 2)),
        (scrub_text, "a://" + "b" * _MB),
        (scrub_text, "a://b@" * (_MB // 6)),
        (scrub_text, "a://b:12/" + "@" * _MB),
        (lambda t: scrub({t: "v"}), "x" * _MB),  # 긴 키 — 구분자를 걷은 전체 키 판정
        (lambda t: scrub({t: "v"}), "_" * _MB),
        (mask_sql, "$$ " * (_MB // 3)),
        (mask_sql, "$ab$" + "x" * _MB),
        (mask_sql, " $a" * (_MB // 3)),
        (mask_query, "x&" * (_MB // 2)),
        (mask_query, "가" * _MB),
        (_is_person_key, "I" * _MB),
        (_is_person_key, "_i" * (_MB // 2)),
        (scrub_text, "connect " * (_MB // 8)),  # 2차 — DB2 using 미리보기
        (scrub_text, "connect user a using " * (_MB // 21)),
        (scrub_text, "-P " * (_MB // 3)),
        (scrub_text, "-a -a " * (_MB // 6)),
        (scrub_text, "-u a," * (_MB // 5)),
        (scrub_text, "-U " + "x" * _MB),
    ],
    ids=[
        "cmd-spaces",
        "cmd-one-token",
        "connect-at",
        "connect-ats",
        "kv-conn-lookahead",
        "kv-conn-many",
        "kv-word",
        "quote-escapes",
        "jvm-commas",
        "url-long-user",
        "url-many-userinfo",
        "url-port-ats",
        "flat-key",
        "flat-key-separators",
        "sql-dollar-pairs",
        "sql-dollar-unclosed",
        "sql-dollar-lookbehind",
        "query-bare",
        "query-hangul",
        "person-key-ids",
        "person-key-suffix",
        "db2-connect",
        "db2-using",
        "flag-P",
        "flag-a",
        "user-sep",
        "user-sep-long",
    ],
)
def test_new_rules_are_linear_on_1mb(fn, attack):
    started = time.monotonic()
    fn(attack)
    assert time.monotonic() - started < 2.0


def test_classify_hidden_replacement_is_linear_on_1mb():
    client = JenniferClient(JenniferApiConfig(url="http://apm.test", token=TOKEN))
    body = json.dumps({"exception": {"message": "kim01" * (_MB // 5)}})
    started = time.monotonic()
    client._classify(500, body, "/restapi/user/{id}", [("kim01", "{id}")])
    assert time.monotonic() - started < 2.0


# ── VG-7 · REAUDIT-7 — 실프로세스: INFO·DEBUG 로그에 계정 ID·쿼리 값 원값이 없다 ────────


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _sse_call(url: str, name: str, args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    async with sse_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, args)
            return json.loads(result.content[0].text)


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["INFO", "DEBUG"])  # 운영 기본 · 진단(REAUDIT-7 — MCP 전송 로거)
async def test_real_process_log_has_no_account_id(
    tmp_path, mock_server_factory, synthetic_dir, level
):
    base, state = mock_server_factory(synthetic_dir, "connected")
    port = _free_port()
    log_path = tmp_path / "gateway.log"
    env = {
        **os.environ,
        "JENNIFER_SOURCES": "",
        "JENNIFER_API_URL": base,
        "JENNIFER_API_TOKEN": TOKEN,
        "JENNIFER_DOMAIN_IDS": "[]",
        "APM_GATEWAY_HOST": "127.0.0.1",
        "APM_GATEWAY_PORT": str(port),
        "APM_GATEWAY_BEARER_TOKEN": "",
        "APM_GATEWAY_BEARER_TOKENS": "",
        "APM_EVENT_POLLER_ENABLED": "false",
        "APM_SPOOL_DIR": str(tmp_path / "spool"),
        "APM_GATEWAY_LOG_LEVEL": level,
    }
    with log_path.open("wb") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "apm_gateway"],
            cwd=GATEWAY_ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            for _ in range(150):
                with contextlib.suppress(OSError):
                    socket.create_connection(("127.0.0.1", port), 0.1).close()
                    break
                await asyncio.sleep(0.1)
            url = f"http://127.0.0.1:{port}/sse"
            await _sse_call(url, "apm_users", {"user_id": "rawacct77"})
            await _sse_call(
                url,
                "apm_config",
                {"kind": "loaded_classes", "hostname": "was-host01", "search": "OrderSecretSearch"},
            )
            proc.send_signal(signal.SIGTERM)
            await asyncio.to_thread(proc.wait, 20)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    text = log_path.read_text(encoding="utf-8", errors="replace")
    assert "APM 게이트웨이 시작" in text  # INFO가 실제로 찍히는 설정이다
    if level == "DEBUG":
        assert "apm http: path=/restapi/user/{id}" in text  # DEBUG가 실제로 찍힌다(템플릿)
    assert any(h["path"] == "/restapi/user/rawacct77" for h in state.hits)  # 요청은 나갔다
    assert "rawacct77" not in text
    assert "OrderSecretSearch" not in text  # 쿼리 값(`search`)도 httpx 줄에 실리지 않는다
    assert "HTTP Request:" not in text
    assert "Received JSON" not in text and "Received message" not in text  # MCP 전송 본문

