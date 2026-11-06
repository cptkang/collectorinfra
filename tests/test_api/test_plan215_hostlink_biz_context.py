"""plans/215 §4.8 D2 잔여 — 「이 서버로 다시 조회」(`post_host_link`) 재조회의 `meta.biz_service`.

- D2 플래그 on + 사본 있음: 재조회 페이로드에 `meta.biz_service`가 붙는다 — 비교 구성원은 요청자
  허용 존 안만, 사용자가 고른 서버(연계 대상)는 빠진다(수동 분석 경로와 같은 인자).
- 플래그 off(사본 있음) · 사본 없음(플래그 on): `build_manual_payload`에 업무 인자를 넘기지 않고
  (호출 비트 동일) 귀속 조회 0 · 키 없음.

업무 서비스 사본은 덕 타이핑 대역(`attribute`·`get_service`)이다.
DB·Redis·조사 서비스는 띄우지 않는다.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noise_gate.application.nodes import investigation_trigger as trig
from tests.test_api.test_plan187_w11_manual import _FakeClient
from tests.test_api.test_plan196_host_link import (
    ADMIN,
    GONGJON_USER,
    LINK,
    SELECT,
    _app,
    _host_config,
    _inject,
    _reset_poll_state,  # noqa: F401 — autouse 픽스처 재사용
    _seed,
    audits,  # noqa: F401 — 픽스처 재사용
    probes,  # noqa: F401 — 픽스처 재사용
)

pytestmark = pytest.mark.usefixtures("probes", "audits")

SERVICE = {
    "id": 7, "service_key": "kbland", "display_name": "KB부동산", "state": "active",
    "members": [
        # 사용자가 고른 서버 — 연계 대상이라 비교 구성원에서 빠진다
        {"kind": "sms_server", "member_key": "polestar_cm_gp|11", "db_id": "polestar_cm_gp",
         "resource_id": "11", "server_name": "KBL-KBLAND-ADMIN-PRICE",
         "hostname": "kbl-kbland-admin-price", "zone": "gongjon", "confidence": "confirmed"},
        {"kind": "sms_server", "member_key": "polestar_cm_gp|12", "db_id": "polestar_cm_gp",
         "resource_id": "12", "server_name": "KBL-KBLAND-WEB01", "hostname": "kbl-kbland-web01",
         "zone": "gongjon", "confidence": "confirmed"},
        {"kind": "sms_server", "member_key": "polestar_b0|21", "db_id": "polestar_b0",
         "resource_id": "21", "server_name": "KBL-KBLAND-DB01", "hostname": "kbl-kbland-db01",
         "zone": "bankjon", "confidence": "inferred"},
    ],
}


class _BizCache:
    """업무 서비스 사본 대역 — 귀속은 늘 서비스 7(호스트 근거)."""

    redis = None

    def __init__(self) -> None:
        self.attribute_calls: list[dict[str, Any]] = []

    async def attribute(self, **kwargs: Any) -> Any:
        self.attribute_calls.append(kwargs)
        return SimpleNamespace(service_id=7, basis="host")

    async def get_service(self, service_id: int) -> dict[str, Any] | None:
        return SERVICE if service_id == 7 else None


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """`build_manual_payload` 호출 인자 기록(실함수는 그대로 부른다)."""
    calls: list[dict[str, Any]] = []
    real = trig.build_manual_payload

    async def _wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return await real(*args, **kwargs)

    monkeypatch.setattr(trig, "build_manual_payload", _wrapped)
    return calls


def _run(tmp_path, monkeypatch, user, *, flag: bool, cache: _BizCache | None):  # noqa: ANN001, ANN202
    _seed(tmp_path)
    fake = _FakeClient()
    _inject(monkeypatch, fake)
    config = _host_config(tmp_path)
    ng = config.noise_gate
    ng.biz_investigation_context_active = flag
    ng.biz_investigation_max_sms = 10
    ng.biz_investigation_max_apm = 8
    app = _app(config, user)
    if cache is not None:
        app.state.biz_service_cache = cache
    res = TestClient(app).post(LINK, json=SELECT)
    assert res.status_code == 200, res.text
    (payload,) = fake.submit_calls
    return payload


def test_on_attaches_biz_service_in_requester_zone(tmp_path, monkeypatch, spy):
    cache = _BizCache()
    payload = _run(tmp_path, monkeypatch, GONGJON_USER, flag=True, cache=cache)
    biz = payload["meta"]["biz_service"]
    assert biz["service_id"] == 7 and biz["display_name"] == "KB부동산"
    names = [m["server_name"] for m in biz["sms_members"]]
    # 공동존 사용자 — 은행존 구성원 제외 · 고른 서버(연계 대상) 제외
    assert names == ["KBL-KBLAND-WEB01"]
    # 재조회 힌트는 그대로(`polestar_link`는 210 의미 — 건드리지 않는다)
    hints = payload["meta"]["hints"]
    assert hints["polestar_link"]["basis"] == "user_selected"
    assert hints["rerun"]["parent_investigation_id"] == "inv-1"
    assert len(cache.attribute_calls) == 1
    assert spy[0]["biz_service_cache"] is cache and spy[0]["biz_zones"] == {"gongjon"}


def test_on_all_zones_operator_sees_every_zone(tmp_path, monkeypatch, spy):
    payload = _run(tmp_path, monkeypatch, ADMIN, flag=True, cache=_BizCache())
    names = sorted(m["server_name"] for m in payload["meta"]["biz_service"]["sms_members"])
    assert names == ["KBL-KBLAND-DB01", "KBL-KBLAND-WEB01"]
    assert spy[0]["biz_zones"] is None


def test_flag_off_with_cache_is_bit_identical(tmp_path, monkeypatch, spy):
    cache = _BizCache()
    payload = _run(tmp_path, monkeypatch, GONGJON_USER, flag=False, cache=cache)
    assert "biz_service" not in payload["meta"]
    assert cache.attribute_calls == []
    assert not {"biz_service_cache", "biz_zones"} & set(spy[0])


def test_no_cache_with_flag_on_passes_nothing(tmp_path, monkeypatch, spy):
    payload = _run(tmp_path, monkeypatch, GONGJON_USER, flag=True, cache=None)
    assert "biz_service" not in payload["meta"]
    assert not {"biz_service_cache", "biz_zones"} & set(spy[0])


def test_off_payload_matches_no_flag_baseline(tmp_path, monkeypatch, spy):
    """플래그 off 페이로드 = 업무 기능이 없는 설정의 페이로드(무작위·시각 칸만 제외)."""

    def _norm(p: dict[str, Any]) -> dict[str, Any]:
        out = {**p, "decision": {**p["decision"], "fingerprint": ""}}
        hints = dict(out["meta"]["hints"])
        hints["polestar_link"] = {**hints["polestar_link"], "at": ""}
        out["meta"] = {**out["meta"], "hints": hints}
        return out

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    off = _run(tmp_path / "a", monkeypatch, GONGJON_USER, flag=False, cache=_BizCache())
    base = _run(tmp_path / "b", monkeypatch, GONGJON_USER, flag=False, cache=None)
    assert _norm(off) == _norm(base)
