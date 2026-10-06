"""자격증명 제거 — 규칙 단위 + 카나리아 종단 (plans/134 N-17 · D-296 ③ ·
SPEC-apm-question-coverage §4.1·§4.2 · 수용 ⑥).

카나리아 값이 도구 반환 · 스풀 파일 · 감사·로그 · `limits` · 오류 사유 어디에도 없음을 단언하고,
일반 설정값(`PATH`·`JAVA_HOME`·`java.vendor`·`KEYBOARD_LAYOUT`)은 그대로임을 함께 단언한다. 패턴
테스트만으로 모든 비밀을 보장한다고 선언하지 않는다(운영 마스킹 녹화본 대조는 W10).
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
from apm_gateway.adapters.jennifer.client import JenniferClient
from apm_gateway.domain.credentials import MASK, is_secret_key, scrub, scrub_text
from conftest import NOW_MS, make_tools, synthetic_fixtures, synthetic_handler

from apm_gateway.config import JenniferApiConfig

C = "CANARY-q7Z9x"  # 어디에도 나오면 안 되는 값


# ── 규칙 단위 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        f"JAVA_OPTS=-Ddb.password={C}",
        f"-Dspring.datasource.password={C} -Xmx1g",
        f"DB_PW2={C}",
        f"db_pw2 = {C}",
        f"jdbc:mysql://app:{C}@db.example:3306/x",
        f"jdbc:postgresql://app:{C}@db.example/x?ssl=true",
        f"jdbc:oracle:thin:scott/{C}@db.example:1521:orcl",
        f"Server=x;Database=y;User Id=u;Password={C};",
        f"url?user=kim&pwd={C}&x=1",
        f"/opt/restart.sh --password {C} --user admin",
        f"/opt/restart.sh --password={C}",
        f"/opt/x.sh --db-password '{C}'",
        f"Authorization: Bearer {C}",
        f'{{"apiKey": "{C}", "user": "kim"}}',
        f"OPTS=password={C}",
        f"SECRET_KEY: {C}",
        f"export AWS_SECRET_ACCESS_KEY={C}",
        f"PassWord={C}",
        f"x-api-key={C}",
    ],
)
def test_text_rules_mask_value_keep_key(text):
    out = scrub_text(text)
    assert C not in out and MASK in out


def test_short_p_only_in_command_context():
    assert C not in scrub_text(f"mysql -u root -p {C} db", command=True)
    assert C not in scrub_text(f"mysql -uroot -p{C} db", command=True)
    assert scrub_text("ssh -p 22 host") == "ssh -p 22 host"  # 명령줄 문맥이 아니면 그대로


@pytest.mark.parametrize(
    "text",
    [
        "PATH=/usr/local/bin:/usr/bin",
        "JAVA_HOME=/opt/java/openjdk",
        "java.vendor=Eclipse Adoptium",
        "KEYBOARD_LAYOUT=us",
        "KEYSTORE_TYPE=PKCS12",
        "user=kim&page=2",
        "select * from orders where user_email = 'kim@example.com' and id = 42",
        # `http://host:8080/path@x`는 2026-10-06 처분(AUDIT-3 (3))으로 가린다 — 아래 시험
        "http://host:8080/a?email=x@y.com",  # `?` 뒤의 `@`는 포트 판정에 쓰지 않는다
        "monkey=1 author=lee",
    ],
)
def test_text_rules_keep_general_values(text):
    assert scrub_text(text) == text


def test_port_like_prefix_with_at_sign_is_masked_as_password():
    """`?`·`#` 앞에 `@`가 있으면 숫자로 시작해도 포트가 아니라 비밀번호다(plans/134 W7 AUDIT-3 (3)
    · 팀 리드 처분). `scheme://user:12/pw@db`와 경로에 `@`가 든 URL은 문법상 구별되지 않아 경로
    쪽을 과잉 가림으로 받아들인다(덜 가리는 쪽으로 틀리지 않는다)."""
    assert scrub_text(f"jdbc:mysql://app:12/{C}@db01:3306/x") == "jdbc:mysql://app:[가림]@db01:3306/x"
    assert scrub_text("http://host:8080/path@x") == "http://host:[가림]@x"


@pytest.mark.parametrize(
    ("key", "secret"),
    [
        ("DB_PW2", True),
        ("dbPassword", True),
        ("PASSWD", True),
        ("apiKey", True),
        ("ACCESS_KEY", True),
        ("SECRETKEY", True),
        ("javax.net.ssl.keyStorePassword", True),
        ("OAUTH2_CLIENT_SECRET", True),
        ("PATH", False),
        ("JAVA_HOME", False),
        ("java.vendor", False),
        ("KEYBOARD_LAYOUT", False),
        ("KEYSTORE_TYPE", False),
        ("MONKEY", False),
        ("AUTHOR", False),
        ("javax.net.ssl.keyStore", False),
    ],
)
def test_secret_key_pattern(key, secret):
    assert is_secret_key(key) is secret


def test_structures_password_removed_values_masked_general_kept():
    body = {
        "result": [
            {
                "SYSTEM": {"PATH": "/bin", "DB_PASSWORD": C, "JAVA_HOME": "/opt/java"},
                "JAVA": [
                    {"key": "db.password", "value": C},
                    {"name": "java.vendor", "value": "Oracle"},
                    {"Name": "API_TOKEN", "Value": C},
                ],
                "user": {"id": "u1", "password": C, "PassWord": C, "PWD": C, "name": "kim"},
                "autoScriptCommand": f"/x.sh -p {C} --verbose",
                "credentials": {"user": "a", "nested": [C, {"x": C}]},
                "env_lines": [f"DB_PW2={C}", "PATH=/usr/bin"],
                "AUTH_ENABLED": True,
                "count": 3,
            }
        ]
    }
    out, notes = scrub(body)
    text = json.dumps(out, ensure_ascii=False)
    assert C not in text and notes == []
    row = out["result"][0]
    assert row["user"] == {"id": "u1", "name": "kim"}  # password 필드는 키째 제거
    assert row["SYSTEM"] == {"PATH": "/bin", "DB_PASSWORD": MASK, "JAVA_HOME": "/opt/java"}
    assert row["JAVA"][1] == {"name": "java.vendor", "value": "Oracle"}
    assert row["JAVA"][0] == {"key": "db.password", "value": MASK}
    assert row["env_lines"] == [f"DB_PW2={MASK}", "PATH=/usr/bin"]
    assert row["AUTH_ENABLED"] is True and row["count"] == 3
    assert body["result"][0]["user"]["password"] == C  # 입력은 바꾸지 않는다(순수 함수)


def test_unexpected_deep_shape_keeps_values_and_notes_path():
    deep: dict = {}
    cur = deep
    for _ in range(40):
        cur["n"] = {}
        cur = cur["n"]
    cur.update({"secret_token": C, "Password": C, "plain": "ok"})
    out, notes = scrub({"result": [deep]})
    assert C not in json.dumps(out) and "ok" in json.dumps(out)
    assert len(notes) == 1
    assert notes[0].startswith("[한계] 자격증명 검사: 예상 밖 응답 모양($.result[0].n")
    assert C not in notes[0]


@pytest.mark.asyncio
async def test_client_scrubs_json_text_and_error_reason():
    responses = {
        "/api/domain": httpx.Response(
            200, json={"result": [{"domainId": 1, "name": "d", "password": C, "x": f"pwd={C}"}]}
        ),
        "/api/transaction/profile.txt": httpx.Response(
            200,
            text=f"START\n  exec /x.sh --password {C}\nEND",
            headers={"content-type": "text/plain"},
        ),
        "/api/instance": httpx.Response(
            500, json={"exception": {"message": f"Required request parameter password={C}"}}
        ),
    }
    client = JenniferClient(
        JenniferApiConfig(url="http://apm.test", token="t", rate_limit_per_sec=0),
        transport=httpx.MockTransport(lambda r: responses[r.url.path]),
    )
    body = await client.get_json("/api/domain")
    text = await client.get_text(
        "/api/transaction/profile.txt", {"domain_id": 1, "txid": 2, "time": 3}
    )
    with pytest.raises(Exception) as exc:
        await client.get_json("/api/instance", {"domain_id": 1})
    await client.aclose()
    assert body == {"result": [{"domainId": 1, "name": "d", "x": f"pwd={MASK}"}]}
    assert C not in text and C not in str(exc.value)


# ── 카나리아 종단 — 도구 반환 · 스풀 · 감사 · limits · 오류 사유 ──


def _canary_fixtures() -> list[dict]:
    out = []
    for fx in synthetic_fixtures():
        fx = json.loads(json.dumps(fx))
        tpl = fx["request"]["template"]
        body = fx["response"].get("body_json")
        if tpl == "/api/activeService/list":
            for rec in body["result"]:
                rec["runningFullText"] = (
                    f"connect jdbc:mysql://app:{C}@db.example/x -Ddb.password={C} DB_PW2={C}"
                )
        if tpl == "/api/dbsearch/event":
            body["result"][0]["message"] = f"login failed --password {C} PATH=/usr/bin"
        if tpl == "/api/realtime/instance":
            deep: dict = {}
            cur = deep
            for _ in range(40):
                cur["n"] = {}
                cur = cur["n"]
            cur["secret"] = C
            body["result"][0]["extra"] = {
                "SYSTEM": {"DB_PASSWORD": C, "PATH": "/usr/bin"},
                "env": [{"key": "API_KEY", "value": C}],
                "deep": deep,
            }
        if tpl == "/api/instance":
            for rec in body["result"]:
                rec["password"] = C
        if tpl == "/api/transaction/profile.txt":
            fx["response"]["body_text"] += f"  exec /opt/x.sh --password {C}\n"
        out.append(fx)
    return out


@pytest.mark.asyncio
async def test_canary_absent_from_returns_spool_audit_limits_and_errors(tmp_path, caplog):
    from apm_gateway.application.jobs import JobManager
    from apm_gateway.application.spool import Spool
    from apm_gateway.interface.server import audit_job_finished, create_server

    from apm_gateway.config import JobConfig

    caplog.set_level(logging.DEBUG)
    handler = synthetic_handler(_canary_fixtures(), delay=0.005)
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler))
    job_cfg = JobConfig(spool_dir=tmp_path / "spool")
    jobs = JobManager(
        Spool(job_cfg.spool_dir),
        job_cfg,
        envelope=tools.ok,
        error_envelope=tools.err,
        on_finish=audit_job_finished,
    )
    mcp = create_server(tools, jobs=jobs)

    def call(name, args):
        async def run():
            result = await mcp.call_tool(name, args)
            blocks = result[0] if isinstance(result, tuple) else result
            return json.loads(blocks[0].text)

        return run()

    outputs = [
        await call("apm_active_services", {"hostname": "was-host01"}),
        await call("apm_runtime_health", {"hostname": "was-host01"}),
        await call("apm_instance_map", {}),
        await call(
            "apm_transaction_profile",
            {"hostname": "was-host01", "domain_id": 1000, "txid": "9000", "time_ms": NOW_MS},
        ),
    ]
    handle = await call("apm_events", {"hostname": "was-host01", "wait_seconds": 0, "owner": "o"})
    job_id = handle["job"]["job_id"]
    for _ in range(200):
        status = await call("apm_job_status", {"job_id": job_id, "owner": "o"})
        if status["job"]["state"] not in ("queued", "running"):
            break
        await asyncio.sleep(0.01)
    outputs += [status, await call("apm_job_read", {"job_id": job_id, "owner": "o"})]
    assert status["job"]["state"] == "completed" and status["rows"]

    # 오류 사유 경로 — 도메인 목록 오류 본문에 자격증명이 섞여도 사유에 남지 않는다
    bad = synthetic_handler(
        override={
            "/api/domain": httpx.Response(
                500,
                json={
                    "exception": {
                        "message": f"1000 Domain is not connected jdbc:oracle:thin:s/{C}@db"
                    }
                },
            )
        }
    )
    bad_tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(bad))
    bad_mcp = create_server(bad_tools, jobs=jobs)
    result = await bad_mcp.call_tool("apm_app_health", {"hostname": "was-host01"})
    err = json.loads((result[0] if isinstance(result, tuple) else result)[0].text)
    assert err["error"] == "source_unavailable" and "Domain is not connected" in err["reason"]
    outputs.append(err)

    for out in outputs:
        assert C not in json.dumps(out, ensure_ascii=False), out.get("tool")
    runtime = outputs[1]
    assert any("자격증명 검사: 예상 밖 응답 모양" in x for x in runtime["limits"])  # 메모 → limits
    spooled = [p for p in (tmp_path / "spool").rglob("*") if p.is_file()]
    assert spooled and all(C not in p.read_text(encoding="utf-8") for p in spooled)
    assert "apm audit" in caplog.text and C not in caplog.text
