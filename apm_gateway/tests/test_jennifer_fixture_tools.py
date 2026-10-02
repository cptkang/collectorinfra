"""제니퍼 J0 검증 도구(녹화 하네스·마스킹·목 서버) 회귀 테스트 — plans/87 §0.8 · §0.10.

루트 pytest 수집 경로 밖이다: `.venv/bin/python -m pytest apm_gateway/tests -q`
네트워크는 127.0.0.1의 임시 포트 목 서버만 쓴다(실 제니퍼 호출 없음).
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "testdata" / "jennifer" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from jennifer_catalog import ALLOWED, fixture_stem, match_template  # noqa: E402
from masking import Masker  # noqa: E402
from mock_openapi import make_server  # noqa: E402
from record_openapi import NotAllowedError, Recorder  # noqa: E402

TOKEN = "t-test"


def _fixture(template: str, status: int, body, variant: str, label: str = "") -> dict:
    return {
        "fixture_version": 1,
        "source": "local-docker",
        "jennifer_version": "test",
        "request": {"method": "GET", "template": template, "path": template, "query": {}},
        "response": {"status": status, "content_type": "application/json", "body_json": body},
        "variant": variant,
        "label": label,
    }


@pytest.fixture
def mock(tmp_path):
    fx = tmp_path / "fx"
    fx.mkdir()
    (fx / "a.json").write_text(
        json.dumps(_fixture("/api/domain", 200, {"result": [{"id": 1000}]}, "ok"))
    )
    (fx / "b.json").write_text(
        json.dumps(
            _fixture(
                "/api/instance",
                500,
                {"exception": {"message": "1000 Domain is not connected"}},
                "domain_not_connected",
            )
        )
    )
    server, state = make_server(fx, TOKEN)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield base, state
    server.shutdown()


def _call(url: str, method: str = "GET", headers: dict | None = None, data: bytes | None = None):
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _bearer(extra: dict | None = None) -> dict:
    return {"Authorization": f"Bearer {TOKEN}", **(extra or {})}


# --- 카탈로그 ---------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/api/domain", "/api/domain"),
        ("/api-v2/deploy/1000", "/api-v2/deploy/{domainId}"),
        ("/api/domain.xml", None),
        ("/api/transaction/time/../../auth/userlist", None),
        ("/api//domain", None),
        ("/api/auth/userlist", None),
        ("/api/transaction/%2e%2e/x", None),
    ],
)
def test_match_template_is_exact(path, expected):
    assert match_template(path) == expected


def test_fixture_stem():
    assert fixture_stem("/api-v2/deploy/{domainId}") == "GET_api_v2_deploy_domainId"


# --- 녹화 하네스 가드 ---------------------------------------------------------


@pytest.mark.parametrize(
    "template,params,path_vars",
    [
        ("/api/auth/userlist", None, None),
        ("/api/domain", {"token": "x"}, None),
        ("/api/instance", {"domain_id": 1, "evil": 1}, None),
        ("/api-v2/deploy/{domainId}", {"startTime": 1, "endTime": 2}, None),
    ],
)
def test_recorder_refuses_before_network(tmp_path, template, params, path_vars):
    rec = Recorder("http://127.0.0.1:1", TOKEN, "local-docker", tmp_path)
    with pytest.raises(NotAllowedError):
        rec.build_request(template, params, path_vars)


def test_recorder_request_shape(tmp_path):
    rec = Recorder("http://h", TOKEN, "local-docker", tmp_path)
    req = rec.build_request("/api/transaction/profile.txt", {"domain_id": 1, "txid": 2, "time": 3})
    assert req.get_method() == "GET"
    assert req.get_header("Accept") == "text/plain"
    assert req.get_header("Authorization") == f"Bearer {TOKEN}"
    assert "token=" not in req.full_url


# --- 마스킹 -----------------------------------------------------------------


def test_masking_keys_values_sql_url():
    m = Masker()
    out = m.mask(
        {
            "email": "a@b.com",
            "password": "",
            "message": "call 010-1234-5678 or x@y.io",
            "sql": "select * from t where id = 42 and name = 'kim'",
            "applicationName": "/order?user=kim&id=7",
            "value": 3,
        }
    )
    assert out["email"] == "<masked>"
    assert out["password"] == ""
    assert out["message"] == "call <phone> or <email>"
    assert out["sql"] == "select * from t where id = ? and name = ?"
    assert out["applicationName"] == "/order?user=<v>&id=<v>"
    assert out["value"] == 3


def test_profile_text_keeps_timing_numbers():
    m = Masker()
    text = "[0001] 12:00:00 120ms START\n[0002] select a from b where c = 'x' and d = 5\n"
    out = m.mask_profile_text(text)
    assert "12:00:00 120ms" in out
    assert "c = ? and d = ?" in out


def test_ops_masked_pseudonyms_are_stable_and_local_is_untouched():
    ops = Masker(pseudonymize=True)
    a = ops.mask({"hostName": "fgtsidd0", "ipAddress": "10.1.2.3"})
    b = ops.mask({"hostName": "fgtsidd0", "ipAddress": "10.1.2.3"})
    c = ops.mask({"hostName": "other"})
    assert a == b and a["hostName"].startswith("host-") and a["hostName"] != c["hostName"]
    assert a["ipAddress"] != "10.1.2.3"
    assert Masker().mask({"hostName": "fgtsidd0"}) == {"hostName": "fgtsidd0"}


# --- 목 서버 ----------------------------------------------------------------


def test_mock_auth_and_query_token_is_flagged(mock):
    base, state = mock
    assert _call(base + "/api-v2/auth-test")[0] == 401
    assert _call(base + "/api-v2/auth-test", headers=_bearer()) == (200, '"OK"')
    assert _call(base + f"/api/domain?token={TOKEN}")[0] == 200
    hit = state.hits[-1]
    assert hit["query_token"] is True and hit["allowlisted"] is False


def test_mock_error_model_matches_real(mock):
    base, _ = mock
    status, body = _call(base + "/api/dbsearch/event", headers=_bearer())
    assert status == 500
    assert "Required request parameter 'domain_id'" in json.loads(body)["exception"]["message"]
    status, body = _call(base + "/api/instance?domain_id=1000", headers=_bearer())
    assert (status, json.loads(body)["exception"]["message"]) == (
        500,
        "1000 Domain is not connected",
    )
    q = "?domain_id=1&txid=1&time=1"
    assert (
        _call(
            base + "/api/transaction/profile.txt" + q,
            headers=_bearer({"Accept": "application/json"}),
        )[0]
        == 404
    )
    assert (
        _call(base + "/api/transaction/profile.txt" + q, headers=_bearer({"Accept": "text/plain"}))[
            0
        ]
        != 404
    )


def test_mock_disconnected_mode(mock):
    base, state = mock
    state.mode = "disconnected"
    assert _call(base + "/api/realtime/instance?domain_id=1000", headers=_bearer()) == (
        200,
        '{"result": []}',
    )
    status, body = _call(base + "/api-v2/deploy/1000?startTime=1&endTime=2", headers=_bearer())
    assert status == 500 and "1000 Domain is not connected" in body


def test_mock_non_allowlisted_access_is_recorded(mock):
    base, state = mock
    assert _call(base + "/api/auth/userlist", headers=_bearer())[0] == 200
    assert _call(base + "/api-v2/manage/data-server/control", headers=_bearer())[0] == 400
    assert _call(base + "/api-v2/manage/data-server/control", "POST", _bearer(), b"{}")[0] == 403
    assert _call(base + "/api/domain", "POST", _bearer())[0] == 200
    outside = [h for h in state.hits if not h["allowlisted"]]
    assert [h["path"] for h in outside] == [
        "/api/auth/userlist",
        "/api-v2/manage/data-server/control",
        "/api-v2/manage/data-server/control",
        "/api/domain",
    ]
    assert state.usage == 4


def test_mock_event_injection_window(mock):
    base, state = mock
    state.mode = "connected"
    events = [
        {
            "time": 1000,
            "domainId": 1000,
            "errorType": "",
            "metricsName": "heap_usage",
            "eventLevel": "WARNING",
        },
        {
            "time": 5000,
            "domainId": 1000,
            "errorType": "ERROR_X",
            "metricsName": "",
            "eventLevel": "FATAL",
        },
    ]
    assert (
        _call(
            base + "/__mock/events",
            "POST",
            {"Content-Type": "application/json"},
            json.dumps(events).encode(),
        )[0]
        == 200
    )
    status, body = _call(
        base + "/api/dbsearch/event?domain_id=1000&start_time=0&end_time=2000", headers=_bearer()
    )
    assert status == 200 and [e["metricsName"] for e in json.loads(body)["result"]] == [
        "heap_usage"
    ]
    bad = [{"time": 1, "eventId": 9}]
    assert _call(base + "/__mock/events", "POST", {}, json.dumps(bad).encode())[0] == 400


# --- 녹화 → 목 재생 왕복 -------------------------------------------------------


def test_record_against_mock_then_replay(mock, tmp_path):
    base, state = mock
    state.mode = "connected"
    _call(
        base + "/__mock/events",
        "POST",
        {},
        json.dumps([{"time": 10**13 - 1, "message": "user kim@corp.example failed"}]).encode(),
    )
    out_root = tmp_path / "rec"
    rec = Recorder(base, TOKEN, "local-docker", out_root, clock=lambda: 10**10)
    index = rec.run(window_minutes=2, metrics=("heap_used",), max_tx=1)
    assert not [h for h in state.hits if not h["allowlisted"]], "하네스가 허용목록 밖을 불렀다"
    files = {row["file"] for row in index}
    assert "GET_api_domain__ok.json" in files
    manifest = json.loads((out_root / "local-docker" / "index.json").read_text())
    assert manifest["source"] == "local-docker" and len(manifest["fixtures"]) == len(index)
    assert set(ALLOWED) >= {row["template"] for row in index}
    event_fx = json.loads(
        (out_root / "local-docker" / "GET_api_dbsearch_event__ok.json").read_text()
    )
    assert event_fx["response"]["body_json"]["result"][0]["message"] == "user <email> failed"
    assert (
        "kim@corp.example"
        not in (out_root / "local-docker" / "GET_api_dbsearch_event__ok.json").read_text()
    )

    server2, _ = make_server(out_root / "local-docker", TOKEN)
    threading.Thread(target=server2.serve_forever, daemon=True).start()
    try:
        base2 = f"http://127.0.0.1:{server2.server_address[1]}"
        assert _call(base2 + "/api/domain", headers=_bearer()) == (
            200,
            '{"result": [{"id": 1000}]}',
        )
    finally:
        server2.shutdown()
