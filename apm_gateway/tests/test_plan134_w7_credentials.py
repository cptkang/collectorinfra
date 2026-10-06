"""plans/134 W7 자격증명 카나리아 — 새 경로도 중앙 경계(`domain/credentials.py`)를 지난다
(N-17 · D-296 ③ · SPEC-apm-question-coverage §4.1·§4.2 · 수용 ⑥).

카나리아 값이 **도구 반환 · 스풀 파일 · 감사 로그(DEBUG 포함) · `limits` · 오류 사유** 어디에도
없음을 MCP 서버 종단으로 단언하고, 일반 값(`PATH`·`JAVA_HOME`·`java.vendor`)은 그대로임을 함께
단언한다. 2026-10-02 실수 이력(W0-B High 2)대로 명세 형태만이 아니라 **우회 입력**을 넣는다 — 붙여
쓴 비밀 단어 · 헤더 다단어 값 · 키-값 묶음 모양 · 정규화(전각·영폭 문자) · 가린 뒤 자르기 · 1MB
공격 문자열 비용.
패턴 테스트만으로 모든 비밀을 보장한다고 선언하지 않는다(운영 녹화본 대조는 W10).
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import time
from pathlib import Path

import httpx
import pytest
from apm_gateway.application.jobs import JobManager
from apm_gateway.application.manage_tools import ManageTools
from apm_gateway.application.spool import Spool
from apm_gateway.domain.credentials import MASK
from apm_gateway.interface.server import audit_job_finished, create_server
from conftest import DOMAIN, make_tools
from mock_openapi import W7Mock
from test_plan134_w7_tools import w7_handler

from apm_gateway.config import JobConfig

C = "CANARY-w7-Zk3p"  # 어디에도 나오면 안 되는 값
GATEWAY = Path(__file__).resolve().parents[1]


def canary_mock() -> W7Mock:
    """W7 합성 본문의 비밀 자리 + 우회 입력을 카나리아로 채운다."""
    mock = W7Mock(secret=C)
    mock.bodies["/api-v2/environment-variable/{domainId}"] = (
        200,
        {
            "1001": {
                "SYSTEM": {
                    "PATH": "/usr/local/bin:/usr/bin",
                    "JAVA_HOME": "/opt/java/openjdk",
                    "DB_PW2": C,
                    "PGPASSWORD": C,
                    "DBPASSWORD": C,  # 붙여 쓴 비밀 단어
                    "rootpw": C,
                    "MYSECRETKEY": C,
                    "ＰＡＳＳＷＯＲＤ": C,  # 전각(정규화)
                    "pass​word": C,  # 영폭 문자
                    "JAVA_OPTS": f"-Xmx1g -Ddb.password={C} -Dfile.encoding=UTF-8",
                    "CATALINA_OPTS": f"-Dspring.datasource.password='{C}' -Xms512m",
                    "LINE": f"export AWS_SECRET_ACCESS_KEY={C}",
                },
                "JAVA": {
                    "java.vendor": "Eclipse Adoptium",
                    "db.password": C,
                    "spring.datasource.url": f"jdbc:mysql://app:{C}@db.example:3306/x",
                    "oracle.url": f"jdbc:oracle:thin:scott/{C}@db.example:1521:orcl",
                },
                # 키-값 묶음 모양(미공개 묶음 — 이름 칸 하나라도 비밀이면 값 가림)
                "PROPS": [
                    {"name": "api.token", "value": C},
                    {"name": "java.vendor", "value": "Oracle"},
                    ["db.passwd", C],
                ],
            }
        },
    )
    mock.bodies["/api-v2/active-service/detail/{domainId}/{txid}"] = (
        200,
        {
            "userId": "kimcs01",
            "guid": "guid-1",
            "sql": f"update users set password = '{C}' where id = 42",
            "http": {"method": "POST", "query": f"user=kim&pwd={C}&x=1"},
            # 헤더 다단어 값 · JSON 문자열 속 비밀
            "headers": f"Authorization: Digest username=x, response={C}\nCookie: JSESSIONID={C}",
            "payload": json.dumps({"login": "kim", "password": C}),
        },
    )
    mock.bodies["/restapi/user/{id}"] = (
        200,
        {"id": "canary", "name": "CANARY", "group": "admin", "PassWord": C, "password": C},
    )
    return mock


def _server(tmp_path, handler, **job_cfg):
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler))
    cfg = JobConfig(spool_dir=tmp_path / "spool", **job_cfg)
    jobs = JobManager(
        Spool(cfg.spool_dir),
        cfg,
        envelope=tools.ok,
        error_envelope=tools.err,
        on_finish=audit_job_finished,
    )
    return create_server(tools, jobs=jobs), tools


async def _call(mcp, name: str, args: dict) -> dict:
    result = await mcp.call_tool(name, args)
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


async def _settle(mcp, job_id: str) -> dict:
    for _ in range(300):
        status = await _call(mcp, "apm_job_status", {"job_id": job_id, "owner": "o"})
        if status.get("job", {}).get("state") not in ("queued", "running"):
            return status
        await asyncio.sleep(0.01)
    raise AssertionError("작업이 끝나지 않았다")


@pytest.mark.asyncio
async def test_canary_absent_from_returns_spool_audit_limits_and_errors(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    mock = canary_mock()
    mcp, _ = _server(tmp_path, w7_handler(mock), inline_rows=1)  # 2행 이상 = 결과 파일
    calls = [
        ("apm_environment", {"hostname": "was-host01"}),
        ("apm_users", {}),
        ("apm_users", {"user_id": "canary"}),
        (
            "apm_active_detail",
            {"domain_id": DOMAIN, "txid": "-8001", "session_id": 1, "thread_hash": 2},
        ),
        ("apm_config", {"kind": "data_server"}),
        ("apm_config", {"kind": "event_rules", "hostname": "was-host01"}),
        ("apm_config", {"kind": "db_path"}),
    ]
    outputs = [await _call(mcp, name, {**args, "owner": "o"}) for name, args in calls]
    assert all("error" not in o for o in outputs), [o.get("reason") for o in outputs]
    # 승격된 작업(백그라운드) — 결과 파일·작업 기록·종료 감사까지
    handle = await _call(
        mcp, "apm_environment", {"hostname": "was-host01", "owner": "o", "wait_seconds": 0}
    )
    status = await _settle(mcp, handle["job"]["job_id"])
    read = await _call(mcp, "apm_job_read", {"job_id": handle["job"]["job_id"], "owner": "o"})
    outputs += [handle, status, read]
    assert status["job"]["state"] == "completed"

    # 오류 사유 경로 — 원천 오류 본문의 자격증명(긴 앞부분 뒤 · 가린 뒤 자른다)
    long_tail = "x" * 230 + f" jdbc:mysql://u:{C}@h password={C}"
    bad = w7_handler(
        mock,
        override={
            f"/api-v2/environment-variable/{DOMAIN}": httpx.Response(500, text=long_tail),
            "/api-v2/manual-rdb-export": httpx.Response(
                500, json={"exception": {"message": f"Required request parameter pwd={C}"}}
            ),
        },
    )
    bad_mcp, _ = _server(tmp_path / "bad", bad)
    errors = [
        await _call(bad_mcp, "apm_environment", {"hostname": "was-host01"}),
        await _call(bad_mcp, "apm_config", {"kind": "rdb_export"}),
    ]
    assert errors[0]["error"] == "apm_api_error" and errors[1]["error"] == "contract_violation"
    outputs += errors

    for out in outputs:
        text = json.dumps(out, ensure_ascii=False)
        assert C not in text, out.get("tool")
        assert all(C not in x for x in out.get("limits", []))
    spooled = [p for p in (tmp_path / "spool").rglob("*") if p.is_file()]
    assert spooled and all(C not in p.read_text(encoding="utf-8") for p in spooled)
    assert "apm audit" in caplog.text and C not in caplog.text

    # 일반 값은 그대로 · 비밀 값 칸은 키 이름을 남기고 [가림]
    env = outputs[0]
    full = read["rows"]  # 결과 파일(전체 행)
    values = {(r["scope"], r["name"]): r["value"] for r in full}
    assert values[("SYSTEM", "PATH")] == "/usr/local/bin:/usr/bin"
    assert values[("SYSTEM", "JAVA_HOME")] == "/opt/java/openjdk"
    assert values[("JAVA", "java.vendor")] == "Eclipse Adoptium"
    for key in ("DB_PW2", "PGPASSWORD", "DBPASSWORD", "rootpw", "MYSECRETKEY"):
        assert values[("SYSTEM", key)] == MASK, key
    assert "-Xmx1g" in values[("SYSTEM", "JAVA_OPTS")]
    assert "-Xms512m" in values[("SYSTEM", "CATALINA_OPTS")]
    props = values[("PROPS", None)]
    assert props[1] == {"name": "java.vendor", "value": "Oracle"}
    assert props[0] == {"name": "api.token", "value": MASK} and props[2] == ["db.passwd", MASK]
    # 가린 칸 고지(칸 이름만 · 값 없음) — 작업 관리자가 봉투 고지로 바꾼다
    kinds = {d["kind"]: d["text"] for d in env.get("disclosures", [])}
    assert "apm_masked_fields" in kinds and "DB_PW2" in kinds["apm_masked_fields"]
    users = outputs[2]
    assert users["rows"][0]["user_id"] == "c***"
    # 비밀번호 키는 키째 없다(고지에는 지운 칸 **이름**만 남는다)
    assert all("password" not in r and "PassWord" not in r["extra"] for r in users["rows"])
    masked_users = {d["kind"]: d["text"] for d in users["disclosures"]}["apm_masked_fields"]
    assert "password" in masked_users and C not in masked_users


@pytest.mark.parametrize(
    "attack",
    [
        "a=" * 500_000,
        "-p" * 500_000,
        '"password":' * 90_000,
        "DB_PW2=x\n" * 110_000,
        "--password " * 95_000,
        "?a" * 500_000,
        "Authorization: " * 70_000,
    ],
    ids=["kv", "dash-p", "json-key", "lines", "cli", "query", "header"],
)
@pytest.mark.asyncio
async def test_one_megabyte_attack_values_stay_linear_on_new_paths(attack):
    """새 경로의 값(환경변수 값 · 룰 스크립트 · 실행 중 요청 SQL·쿼리 · extra 문자열)에 1MB 공격
    문자열이 와도 경계 + 반환 마스킹이 몇 초 안에 끝난다(선형 — 제곱 시간 회귀 감시)."""
    mock = W7Mock(secret=C)
    mock.bodies["/api-v2/environment-variable/{domainId}"] = (
        200,
        {"1001": {"SYSTEM": {"X": attack + f" password={C}"}}},
    )
    mock.bodies["/api-v2/active-service/detail/{domainId}/{txid}"] = (
        200,
        {"sql": attack, "http": {"query": attack}, "note": attack + f" pwd={C}"},
    )
    manage = ManageTools(make_tools("http://apm.test", transport=httpx.MockTransport(
        w7_handler(mock)
    ))[0])
    started = time.monotonic()
    env = await manage.apm_environment(hostname="was-host01")
    detail = await manage.apm_active_detail(domain_id=DOMAIN, txid="1")
    elapsed = time.monotonic() - started
    assert elapsed < 10, f"{elapsed:.1f}s"
    assert C not in json.dumps(env) and C not in json.dumps(detail)


@pytest.mark.asyncio
async def test_client_boundary_covers_v2_bare_array_object_and_boolean():
    """v2 맨 배열·객체·불리언 본문이 클라이언트 출구에서 경계를 지난다(불리언은 그대로)."""
    from apm_gateway.adapters.jennifer.client import JenniferClient

    from apm_gateway.config import JenniferApiConfig

    bodies = {
        "/restapi/users": [{"id": "u", "password": C, "note": f"pwd={C}"}],
        "/api-v2/manage/db/path/1000": {"main": "/d", "backup": f"jdbc:x://u:{C}@h/db"},
        "/api-v2/manage/rule/event/error/1000/X/applied": True,
    }
    client = JenniferClient(
        JenniferApiConfig(url="http://apm.test", token="t", rate_limit_per_sec=0),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=bodies[r.url.path])),
    )
    users = await client.get_json("/restapi/users")
    path = await client.get_json("/api-v2/manage/db/path/{domainId}", path_vars={"domainId": 1000})
    flag = await client.get_json(
        "/api-v2/manage/rule/event/error/{domainId}/{errorType}/applied",
        path_vars={"domainId": 1000, "errorType": "X"},
    )
    await client.aclose()
    assert users == [{"id": "u", "note": f"pwd={MASK}"}]
    assert path == {"main": "/d", "backup": f"jdbc:x://u:{MASK}@h/db"}
    assert flag is True


def test_new_paths_reach_the_client_exits_only():
    """새 어댑터는 클라이언트 출구(`get_json`·`get_text`)로만 원천을 읽는다 — 경계 우회 경로 없음
    (httpx·클라이언트 내부 메서드를 직접 부르지 않는다)."""
    tree = ast.parse(
        (GATEWAY / "apm_gateway" / "adapters" / "jennifer" / "manage_api.py").read_text("utf-8")
    )
    attrs = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "client"
    }
    assert attrs == {"get_json"}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "httpx" not in names
    for module in ("manage_tools.py",):
        text = (GATEWAY / "apm_gateway" / "application" / module).read_text("utf-8")
        assert "httpx" not in text and "get_json" not in text and "_get(" not in text
