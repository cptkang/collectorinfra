"""plans/134 W5·W7 병합 뒤 팀 리드 보강 — 설정 값·SQL 응답의 개인정보 가림(G-11 미결 동안).

- 환경변수 값·데이터 서버 설정 값: 이메일·주민번호·휴대폰만 가린다(`mask_pii`). 일반 설정
  (`-Dport=8080`·경로·서버 IP)은 그대로다 — `mask_text`를 쓰면 설정이 훼손된다(gw-w7 보고 §8 ①).
- 프로파일 SQL 응답: 모양이 미공개라(COV E-11) SQL 칸으로 모은 문자열에 바인드 값이 섞일 수 있다
  — SQL 문 칸(출처 칸 이름)이 아닌 문자열은 앞 1자만 남긴다(gw-w5w6 보고 「남은 위험」 · 2026-10-06
  gw-fix: 종전 키워드 판정 `looks_like_sql`은 AUDIT-8·VG-1로 지웠다 — 칸 이름 판정은 어댑터
  `fields.is_sql_statement_key`).
"""

from __future__ import annotations

import json
import time

import httpx
import pytest
from apm_gateway.adapters.jennifer.fields import is_sql_statement_key
from apm_gateway.application.masking import mask_pii
from conftest import DOMAIN, make_tools, synthetic_handler
from mock_openapi import W7Mock
from test_plan134_w5_profile import _profile
from test_plan134_w7_tools import manage, w7_handler

EMAIL = "hong.gildong@example.com"
PHONE = "010-1234-5678"
RRN = "900101-1234567"


def test_mask_pii_hides_people_data_and_keeps_settings():
    text = f"ADMIN_MAIL={EMAIL} SMS={PHONE} ID={RRN}"
    out = mask_pii(text)
    assert EMAIL not in out and PHONE not in out and RRN not in out
    assert out == "ADMIN_MAIL=<email> SMS=<phone> ID=<rrn>"
    for kept in ("-Dport=8080", "/opt/java/openjdk", "10.20.30.40", "-Xmx1g", "select 1 from dual"):
        assert mask_pii(kept) == kept


def test_mask_pii_is_linear_on_one_megabyte():
    attack = ("a" * 63 + "@") * 16_000  # 이메일 앞부분처럼 보이는 긴 묶음
    started = time.perf_counter()
    mask_pii(attack)
    assert time.perf_counter() - started < 2.0


def test_sql_statement_key_names():
    for key in ("sql", "SQL", "sqlText", "sql_text", "statement", "query", "queryText", "sqls"):
        assert is_sql_statement_key(key), key
    for key in ("sqlParams", "paramText", "bindText", "text", "sqlHash", "params", ""):
        assert not is_sql_statement_key(key), key


@pytest.mark.asyncio
async def test_environment_value_people_data_masked_and_disclosed():
    mock = W7Mock()
    mock.bodies["/api-v2/environment-variable/{domainId}"] = (
        200,
        {
            "1001": {
                "SYSTEM": {"ADMIN_MAIL": EMAIL, "PATH": "/bin", "LISTEN": "10.20.30.40:8080"},
                "JAVA": {"java.opts": f"-Dport=8080 -Downer.phone={PHONE}"},
            }
        },
    )
    out = await manage(w7_handler(mock)).apm_environment(hostname="was-host01")
    got = {(r["scope"], r["name"]): r["value"] for r in out["rows"]}
    assert got[("SYSTEM", "ADMIN_MAIL")] == "<email>"
    assert got[("SYSTEM", "PATH")] == "/bin"
    assert got[("SYSTEM", "LISTEN")] == "10.20.30.40:8080"  # 서버 IP는 인프라 정보 — 가리지 않음
    assert got[("JAVA", "java.opts")] == "-Dport=8080 -Downer.phone=<phone>"
    dump = json.dumps(out, ensure_ascii=False)
    assert EMAIL not in dump and PHONE not in dump
    assert out.get("_masked_fields") == ["value"]


@pytest.mark.asyncio
async def test_environment_without_people_data_has_no_masked_disclosure():
    mock = W7Mock()
    mock.bodies["/api-v2/environment-variable/{domainId}"] = (
        200,
        {"1001": {"SYSTEM": {"PATH": "/bin"}}},
    )
    out = await manage(w7_handler(mock)).apm_environment(hostname="was-host01")
    assert "_masked_fields" not in out


@pytest.mark.asyncio
async def test_profile_sql_bind_values_are_masked_but_sql_text_kept():
    body = {
        "result": [
            {
                "sql": "select name from users where id = ? and team = ?",
                "sqlParams": ["홍길동", "platform"],
            }
        ]
    }
    handler = synthetic_handler(override={"/api/transaction/sql": httpx.Response(200, json=body)})
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler))
    out = await _profile(tools, include_param_key=True)
    sqls = out["rows"][0]["sqls"]
    assert sqls[0] == "select name from users where id = ? and team = ?"
    assert "홍길동" not in json.dumps(out, ensure_ascii=False)
    assert sqls[1:] == ["홍***", "p***"]
    assert any("바인드 값일 수 있음" in x for x in out["limits"])
    assert "sqls" in out.get("_masked_fields", [])
    assert out["rows"][0]["domain_id"] == DOMAIN


@pytest.mark.asyncio
async def test_profile_plain_sql_response_has_no_bind_note():
    body = {"result": [{"sql": "select 1 from dual where x = 'a'"}]}
    handler = synthetic_handler(override={"/api/transaction/sql": httpx.Response(200, json=body)})
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler))
    out = await _profile(tools)
    assert out["rows"][0]["sqls"] == ["select ? from dual where x = ?"]
    assert not any("바인드 값일 수 있음" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_client_debug_log_hides_account_id(caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger="apm_gateway.adapters.jennifer.client")
    out = await manage(w7_handler()).apm_users(user_id="kimcs01")
    assert out["tool"] == "apm_users"
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "/restapi/user/{id}" in logged
    assert "kimcs01" not in logged
