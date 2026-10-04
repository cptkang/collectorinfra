"""plans/134 W0-B 수정 라운드 회귀 — 보안 감사(t1~t7) 재현 입력 · 성능 · 위생.

감사 보고 `w0b_security_audit.md`의 HIGH-1·2 · MEDIUM-1~5 · LOW-1~6 · INFO 재현 입력을 그대로
옮겼다. 카나리아 값은 도구 반환 · 스풀 파일 · 로그 · 오류 사유 어디에도 없어야 한다. 남긴 입력
(감사가 W10 녹화본 판단으로 미룬 모양 — `token C`·`password -> C`·URL 인코딩 키 등)은 여기서
단언하지 않는다.
외부 네트워크 0(MockTransport).
"""

from __future__ import annotations

import asyncio
import json
import logging
import stat
import time

import httpx
import pytest
from apm_gateway.adapters.jennifer.client import JenniferClient, _exception_message
from apm_gateway.application.jobs import JobManager
from apm_gateway.application.masking import mask_text
from apm_gateway.application.spool import Spool
from apm_gateway.domain import jobs as js
from apm_gateway.domain.credentials import MASK, is_secret_key, scrub, scrub_text
from apm_gateway.domain.errors import ApmError
from apm_gateway.interface.audit import audit
from apm_gateway.interface.server import audit_job_finished, create_server
from conftest import DOMAIN, NOW_MS, make_tools, synthetic_fixtures, synthetic_handler

from apm_gateway.config import JenniferApiConfig, JobConfig

C = "CANARY9x"


def _leaks(value) -> bool:
    return C in json.dumps(value, ensure_ascii=False)


# ── t1: 키 분류 (HIGH-2 · MEDIUM-2 · LOW-4) ───────────────────


@pytest.mark.parametrize(
    "key",
    [
        "PGPASSWORD",
        "DBPASSWORD",
        "dbpassword",
        "mysqlpassword",
        "dbpass",
        "adminpass",
        "ADMINPASS",
        "rootpw",
        "ROOTPW",
        "pgpass",
        "sslpassword",
        "cookie",
        "Cookie",
        "session_cookie",
        "jwt",
        "JWT_SIGNING",
        "encryptionKey",
        "hmacKey",
        "masterKey",
        "MASTER_KEY",
        "sshKey",
        "ssh_key",
        "privkey",
        "PRIVKEY",
        "signingKey",
        "JSESSIONID",
        "PHPSESSID",
        "x-auth-token",
        "ＰＡＳＳＷＯＲＤ",
        "pass\u0301word",
        "password\u200b",
        "pass\u00adword",
        "ＤＢ＿ＰＡＳＳＷＯＲＤ",
    ],
)
def test_secret_keys_are_masked_or_removed(key):
    out, _ = scrub({key: C})
    assert not _leaks(out), out


@pytest.mark.parametrize(
    ("key", "value", "kept"),
    [
        ("sessionId", 12345, True),  # 제니퍼 ActiveServiceData.sessionId(int32) — 상세 조회 인자
        ("sessionId", "12345", True),
        ("sessionId", "AB12CD34EF", False),
        ("SESSION", "s3cr3tcookie", False),
        ("connect.sid", "s%3Aabc.def", False),
        ("ORACLE_SID", "orcl", True),  # 인스턴스 이름(일반 설정값)
    ],
)
def test_session_identifiers_mask_only_non_numeric(key, value, kept):
    out, _ = scrub({key: value})
    assert (out[key] == value) is kept, out


def test_general_and_jennifer_fields_are_not_secret():
    for key in (
        "PATH",
        "JAVA_HOME",
        "java.vendor",
        "KEYBOARD_LAYOUT",
        "KEYSTORE_TYPE",
        "MONKEY",
        "AUTHOR",
        "javax.net.ssl.keyStore",
        "instanceId",
        "hostName",
        "errorType",
        "threadHash",
        "txid",
        "key",
    ):
        assert not is_secret_key(key), key


# ── t2: 값 형태 (HIGH-1 · MEDIUM-1·2·4 · LOW-3) ────────────────


@pytest.mark.parametrize(
    "text",
    [
        f"Authorization: Basic {C}==",
        f'Authorization: Digest username="u", response="{C}"',
        f"Authorization: NTLM TlRMTVNTUAAB{C}",
        f"X-Auth-Token: Token {C}",
        f"authorization=Basic {C}",
        f"Proxy-Authorization: Basic {C}",
        f"GET /x HTTP/1.1\r\nHost: h\r\nAuthorization: Basic {C}\r\nAccept: */*\r\n",
        f"password: correct horse battery {C}",
        f"Cookie: JSESSIONID={C}; other=1",
        f"Set-Cookie: SESSION={C}; Path=/",
        f"curl -H 'Authorization: Basic {C}' http://h",
        f"jdbc:mysql://u:{C}@h:3306/db?user=a&password={C}",
        f"jdbc:oracle:thin:u/{C}@h:1521:orcl",
        f"jdbc:oracle:thin:u/{C}@//h:1521/svc",
        f"jdbc:oracle:thin:@(DESCRIPTION=(ADDRESS=(HOST=h)))?user=u&password={C}",
        f"jdbc:sqlserver://h:1433;databaseName=d;user=u;password={C};",
        f"jdbc:sqlserver://h;user=u;Password={{ab;{C}}};",
        f"jdbc:sqlserver://h;password={{ab;{C}brace}};",
        f"jdbc:db2://h:50000/DB:user=u;password={C};",
        f"mongodb+srv://u:{C}@cluster.example.net/db",
        f"mongodb://u:{C}@h1:27017,h2:27017/db?replicaSet=rs",
        f"postgresql://u:{C}@h:5432/db?sslpassword={C}",
        f"redis://:{C}@h:6379/0",
        f"amqp://u:{C}@h/vhost",
        f"https://u:{C}@h/path",
        f"jdbc:postgresql://app:pa/{C}@db:5432/x",
        f"jdbc:mysql://app:p#{C}@db:3306/x",
        f"jdbc:mysql://app:p@{C}@db:3306/x",
        f"jdbc:mysql://u:{C}",  # 잘린 사유 — `@` 없는 꼬리
        f"Server=h;Uid=a;Pwd={C};",
        f"User Id=a;Password={C};Data Source=h",
        f"java -Djavax.net.ssl.keyStorePassword={C} -jar x",
        f'java -Djavax.net.ssl.keyStorePassword="{C} two" -jar x',
        f"java -Ddb.pass={C}",
        f"java -DPGPASSWORD={C}",
        f"java -Dspring.datasource.url=jdbc:mysql://u:{C}@h/db",
        f"--pass {C}",
        f"--db-password {C}",
        f"--token={C}",
        f"PGPASSWORD={C} psql -h h",
        f"MYSQL_PWD={C} mysql",
        f"DBPASSWORD={C}",
        f"mysql -u root -p{C} db",
        f"mysql -uroot -p{C}",
        f"sshpass -p {C} ssh h",
        f"curl -u admin:{C} http://h",
        f"curl --user admin:{C} http://h",
        f"curl -u 'admin:{C}' http://h",
        f'curl -H "X-Api-Key: {C}" http://h',
        f"sqlplus scott/{C}@orcl @/opt/a.sql",
        f"expdp system/{C}@db dumpfile=x",
        f"password = '{C}'",
        f"pass={C}",
        f"pw={C}",
        f"secret: {C}",
        f"<password>{C}</password>",
        f'<property name="password" value="{C}"/>',
        f'<Resource name="jdbc/x" username="u" password="{C}"/>',
        f'{{"password": "{C}"}}',
        f'{{\\"password\\":\\"{C}\\"}}',
        f"{{'password': '{C}'}}",
        f'{{"dbpassword":"{C}"}}',
        f'{{"pwd":"{C}"}}',
        f"ＰＡＳＳＷＯＲＤ={C}",
        f"password＝{C}",
        f"export DB_PASSWORD={C}",
        f"set PASSWORD={C}",
    ],
)
def test_text_forms_are_masked(text):
    assert C not in scrub_text(text), scrub_text(text)


def test_json_number_value_is_masked():
    assert "13572468" not in scrub_text('{"password": 13572468}')


def test_command_context_forms():
    for text in (
        f"/opt/x.sh -p {C}",
        f"mysql -u root -p {C} db",
        f"/opt/run.sh scott/{C}@orcl",
    ):
        assert C not in scrub_text(text, command=True), text
    assert scrub_text("ssh -p 22 host") == "ssh -p 22 host"  # 명령 문맥 밖 띄어 쓴 -p는 그대로


@pytest.mark.parametrize(
    "text",
    [
        "PATH=/usr/local/bin:/usr/bin",
        "JAVA_HOME=/opt/java/openjdk",
        "java.vendor=Eclipse Adoptium",
        "KEYBOARD_LAYOUT=us",
        "user=kim&page=2",
        "http://host:8080/path@x",
        "file://C:/temp/a.txt",
        "monkey=1 author=lee",
        "ORACLE_SID=orcl",
        "sessionId=12345",
        "select * from orders where user_email = 'kim@example.com' and id = 42",
    ],
)
def test_general_text_is_kept(text):
    assert scrub_text(text) == text


# ── t3: 구조 모양 (MEDIUM-3 · LOW-2 · LOW-3) ──────────────────


def _nest(n, leaf):
    for _ in range(n):
        leaf = [leaf]
    return leaf


@pytest.mark.parametrize(
    "obj",
    [
        [{"k": "DB_PASSWORD", "v": C}],
        [["DB_PASSWORD", C]],
        {"env": [["PASSWORD", C], ["PATH", "/bin"]]},
        [{"key": "DB_PASSWORD", "val": C}],
        [{"propertyName": "db.password", "propertyValue": C}],
        [{"key": "id1", "name": "DB_PASSWORD", "value": C}],
        [{"Name": "DB_PASSWORD", "Value": C}],
        [{"KEY": "DB_PASSWORD", "VALUE": C}],
        [{"name": "DB_PASSWORD", "values": [C]}],
        [{"label": "DB_PASSWORD", "value": C}],
        [{"id": "javax.net.ssl.keyStorePassword", "value": C}],
        [{"name": ["DB_PASSWORD"], "value": C}],
        {"keys": ["PATH", "DB_PASSWORD"], "values": ["/bin", C]},
        [{"name": "DB_PASSWORD", "value": {"raw": C}}],
        {"JAVA_OPTS": f"-DPGPASSWORD={C}"},
        {"cfg": json.dumps({"password": C})},
        {"cfg": json.dumps({"inner": json.dumps({"password": C})})},
        {"cfg": json.dumps({("x" * 120) + "_password": C})},
        {"cfg": json.dumps([{"name": "DB_PASSWORD", "value": C}])},
        {"headers": {"Authorization": f"Basic {C}"}},
        {"headers": f"Host: h\r\nAuthorization: Basic {C}\r\nCookie: JSESSIONID={C}"},
        {"headers": {"Cookie": f"JSESSIONID={C}"}},
        {"scriptPath": f"sqlplus scott/{C}@orcl @/opt/a.sql"},
        {"command": f"expdp system/{C}@db dumpfile=x"},
        {"autoScriptCommand": f"/opt/restart.sh --user admin:{C}"},
        {"autoScriptCommand": f"PGPASSWORD={C} /opt/x.sh"},
        [{"name": "command", "value": f"mysql -p{C}"}],
        {"a": _nest(40, f"jdbc:mysql://u:{C}@h/db")},
        {"a": _nest(40, [{"name": "DB_PASSWORD", "value": C}])},
        {"a": _nest(40, {"DB_PASSWORD": C})},
        {"DB_PASSWORD": _nest(40, C)},
        {"DB_PASSWORD": _nest(35, {"x": C})},
    ],
)
def test_structures_are_masked(obj):
    out, _ = scrub(obj)
    assert not _leaks(out), out


def test_numeric_secret_in_pair_and_deep_note():
    out, notes = scrub([{"name": "DB_PW", "value": 13572468}])
    assert "13572468" not in json.dumps(out)
    deep, notes = scrub({"a": _nest(40, {"plain": "ok", "DB_PASSWORD": C})})
    assert not _leaks(deep) and '"ok"' in json.dumps(deep)
    assert len(notes) == 1 and notes[0].startswith("[한계] 자격증명 검사: 예상 밖 응답 모양($.a")


def test_unchanged_json_string_is_kept_verbatim():
    raw = '{"a": 1,   "b": [1, 2]}'
    out, _ = scrub({"cfg": raw})
    assert out == {"cfg": raw}


# ── t4 · t7: 성능 (MEDIUM-5) ─────────────────────────────────

_ATTACKS = {
    "url": "a" * 50_000 + "://x:" + "y" * 50_000 + " @",
    "url_many": "x://a:" * 20_000,
    "oracle_jdbc": "jdbc:oracle:thin:u/" + "p" * 100_000,
    "oracle_cli": "sqlplus u/" + "p" * 100_000,
    "json_open": '"password":"' + "x" * 100_000,
    "json_many": '"k":' * 25_000,
    "json_esc": '\\"password\\":\\"' + "x" * 100_000,
    "kv_long": "password=" + "x" * 100_000,
    "kv_many": "a=" * 50_000,
    "xml_open": "<password>" + "x" * 100_000,
    "xml_many": "<a " * 33_000,
    "cli_many": "-p" * 50_000,
    "cli_flag": "--password " + "x" * 100_000,
    "header": "Authorization: " + "x" * 100_000,
    "bearer": "Bearer " + "a" * 100_000,
    "user_pass": "-u " + "a" * 100_000,
    "mixed": ("a=b:c@d/e'f\"g<h>-p" * 6_000)[:100_000],
}


@pytest.mark.parametrize("name", sorted(_ATTACKS))
def test_100kb_attack_strings_finish_within_1s(name):
    started = time.perf_counter()
    scrub_text(_ATTACKS[name], command=True)
    mask_text(_ATTACKS[name])
    assert time.perf_counter() - started < 1.0, name


@pytest.mark.asyncio
async def test_hostile_event_message_does_not_stall_event_loop():
    """감사 t7 — 공백 없는 30KB 문자열이 이벤트 루프를 5초 넘게 멈췄다."""
    evil = "a" * 15_000 + "://x:" + "y" * 15_000 + "/@"
    fx = synthetic_fixtures()
    for f in fx:
        if f["request"]["template"] == "/api/dbsearch/event":
            for r in f["response"]["body_json"]["result"]:
                r["message"] = "NumberFormatException: For input string: " + evil
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(synthetic_handler(fx)))
    gaps: list[float] = []
    stop = False

    async def ticker() -> None:
        last = time.perf_counter()
        while not stop:
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    tick = asyncio.create_task(ticker())
    started = time.perf_counter()
    await tools.apm_events("was-host01", "2026-09-29T10:00:00", 30, None, None)
    elapsed = time.perf_counter() - started
    stop = True
    await tick
    assert elapsed < 1.0 and max(gaps) < 0.3, (elapsed, max(gaps))


# ── t5 · t6: 종단 카나리아 (HIGH-1·2 · MEDIUM-1·2·4 · LOW-1) ────


def _json(result) -> dict:
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


@pytest.mark.asyncio
async def test_e2e_canaries_absent_from_returns_spool_and_logs(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    can = {k: f"CANARY_{k}{k}{k}" for k in "ABCDEF"}
    fx = synthetic_fixtures()
    for f in fx:
        tpl = f["request"]["template"]
        if tpl == "/api/transaction/profile.txt":
            f["response"]["body_text"] = (
                "START /order/list\n"
                f"  HTTP-HEADER Authorization: Basic {can['A']}\n"
                f"  EXEC PGPASSWORD={can['B']} psql -h db\n"
                "END\n"
            )
        if tpl == "/api/activeService/list":
            for r in f["response"]["body_json"]["result"]:
                r["runningFullText"] = (
                    f"GET http://api/x Cookie: JSESSIONID={can['C']} mysql -uroot -p{can['D']}"
                )
        if tpl == "/api/dbsearch/event":
            for r in f["response"]["body_json"]["result"]:
                r["message"] = (
                    f"login failed Authorization: Basic {can['E']} dbpassword={can['F']}"
                )
    spool = tmp_path / "spool"
    tools, _ = make_tools(
        "http://apm.test",
        transport=httpx.MockTransport(synthetic_handler(fx)),
        extra={"APM_SPOOL_DIR": str(spool)},
    )
    cfg = JobConfig(spool_dir=spool, inline_rows=1)
    jobs = JobManager(
        Spool(spool), cfg, envelope=tools.ok, error_envelope=tools.err, on_finish=audit_job_finished
    )
    mcp = create_server(tools, jobs=jobs)
    outs = [
        _json(
            await mcp.call_tool(
                "apm_transaction_profile",
                {"hostname": "was-host01", "domain_id": DOMAIN, "txid": "9000", "time_ms": NOW_MS},
            )
        ),
        _json(await mcp.call_tool("apm_active_services", {"hostname": "was-host01"})),
        _json(
            await mcp.call_tool(
                "apm_events",
                {
                    "hostname": "was-host01",
                    "reference_time": "2026-09-29T10:00:00",
                    "lookback_minutes": 30,
                },
            )
        ),
    ]
    assert outs[1]["artifact"]["total_rows"] == 4  # inline_rows=1 → 스풀됐다
    blob = json.dumps(outs, ensure_ascii=False)
    files = [p for p in spool.rglob("*") if p.is_file()]
    spooled = "".join(p.read_text(encoding="utf-8", errors="replace") for p in files)
    for value in can.values():
        assert value not in blob and value not in spooled and value not in caplog.text, value
    for path in [spool, *spool.rglob("*")]:  # LOW-1 — 디렉터리 0700 · 파일 0600
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == (0o700 if path.is_dir() else 0o600), (path, oct(mode))
    await jobs.aclose()


@pytest.mark.asyncio
async def test_error_reason_is_scrubbed_before_truncation(tmp_path, caplog):
    """감사 t6 — `@`가 240자 뒤에 오면 자른 뒤 가려서 비밀번호 앞부분이 사유·로그에 남았다."""
    caplog.set_level(logging.DEBUG)
    pw = "CANARYPASSWORD77-long-secret-value-0123456789"
    pre = "x" * (240 - len("jdbc:mysql://u:") - 30)
    message = "required request parameter " + pre[27:] + f"jdbc:mysql://u:{pw}@db:3306/x"
    handler = synthetic_handler(
        override={
            "/api/domain": lambda r: httpx.Response(500, json={"exception": {"message": message}})
        }
    )
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler))
    jobs = JobManager(
        Spool(tmp_path / "spool"),
        JobConfig(spool_dir=tmp_path / "spool"),
        envelope=tools.ok,
        error_envelope=tools.err,
    )
    mcp = create_server(tools, jobs=jobs)
    out = [
        _json(await mcp.call_tool("apm_instance_map", {})),
        _json(await mcp.call_tool("apm_app_health", {"hostname": "was-host01"})),
    ]
    assert out[0]["error"] == "contract_violation"
    assert "CANARYPA" not in json.dumps(out) and "CANARYPA" not in caplog.text
    assert _exception_message(json.dumps({"exception": {"message": "y" * 500}})) == "y" * 500
    await jobs.aclose()


# ── 위생 (LOW-6 · INFO) ──────────────────────────────────────


def test_audit_fields_escape_control_characters(caplog):
    caplog.set_level(logging.INFO, logger="apm_gateway.audit")
    forged = "was01\napm audit: tool=apm_job_read principal=chat"
    audit("apm_events", forged, 1.0, investigation_id="inv\r\n1", thread_id="t\u202e1")
    message = caplog.records[-1].getMessage()
    assert "\n" not in message and "\r" not in message and "\u202e" not in message
    assert "was01\\napm audit" in message


def test_job_id_and_part_name_reject_trailing_newline():
    assert not js.is_job_id("0" * 32 + "\n") and js.is_job_id("0" * 32)
    assert not js.is_part_name("abc\n") and js.is_part_name("abc")


@pytest.mark.asyncio
async def test_deeply_nested_json_is_api_error_not_internal_error():
    body = b"[" * 100_000 + b"]" * 100_000
    client = JenniferClient(
        JenniferApiConfig(url="http://apm.test", token="t", rate_limit_per_sec=0),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body)),
    )
    with pytest.raises(ApmError) as exc:
        await client.get_json("/api/domain")
    await client.aclose()
    assert exc.value.code == "apm_api_error"


def test_mask_text_scans_only_what_it_can_output():
    long = "user=kim@example.com " + "z" * 100_000 + " jdbc:x"
    assert mask_text(long, limit=50) == mask_text(long[:10_000], limit=50)
    assert MASK not in mask_text("plain")
