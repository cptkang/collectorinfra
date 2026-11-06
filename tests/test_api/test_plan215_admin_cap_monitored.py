"""plans/215 잔여 ④ — 감시 대상(`monitored`) 수 상한을 관리 API가 지킨다.

꺼짐→켜짐(POST `/{id}/monitored` · PATCH · 켠 채 생성)에서 `monitored=true` 서비스가 이미
`NOISE_BIZ_MONITOR_MAX_SERVICES` 이상이면 409 `monitor_cap_reached` + 거부 감사(사유 코드만).
이미 켜진 서비스의 재저장·끄기는 막지 않는다. 인가·감사는 W4 라우트 테스트 대역을 쓴다.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_api.test_plan210_w6_api import ADMIN_BG
from tests.test_api.test_plan215_w4_admin_biz_service import BASE, SECRET_TEXT, _audits
from tests.test_api.test_plan215_w4_admin_biz_service import _app as _w4_app
from tests.test_api.test_plan215_w4_admin_biz_service import _Repo as _W4Repo

REASON = "감시 대상 상한 2개에 도달했습니다 — 다른 서비스의 감시를 끈 뒤 다시 켜세요"


class _Repo(_W4Repo):
    """W4 가짜 + `monitored` 필터(실 저장소 `_service_where`처럼 상태와 무관하게 센다)."""

    def __init__(self) -> None:
        super().__init__()
        self.count_fail = False

    async def list_services(self, **kw: Any) -> tuple[list[dict[str, Any]], int]:
        if self.count_fail:
            raise RuntimeError(f"pg down {SECRET_TEXT}")
        rows = [dict(s) for _, s in sorted(self.services.items())
                if kw.get("monitored") is None or s["monitored"] is kw["monitored"]]
        return rows[kw["offset"]:kw["offset"] + kw["limit"]], len(rows)


def _app(cap: int | None = 2) -> tuple[TestClient, FastAPI, _Repo]:
    repo = _Repo()
    client, app, _ = _w4_app(repo=repo)
    if cap is not None:
        app.state.config.noise_gate = SimpleNamespace(biz_service_active=True,
                                                      biz_monitor_max_services=cap)
    return client, app, repo


def _seed_full(repo: _Repo) -> tuple[int, int, int]:
    """켜진 2개(하나는 비활성 — 다시 켜면 곧바로 수집 대상) + 꺼진 1개."""
    a = repo.seed("a", monitored=True)["id"]
    b = repo.seed("b", monitored=True, state="disabled")["id"]
    c = repo.seed("c")["id"]
    return a, b, c


def _denied(app: FastAPI) -> dict[str, Any]:
    audit = _audits(app)[-1]
    assert audit["outcome"] == "denied" and audit["reason"] == "monitor_cap_reached"
    return audit


def test_toggle_on_at_cap_409_with_reason_and_denied_audit() -> None:
    client, app, repo = _app()
    _, _, c = _seed_full(repo)
    res = client.post(f"{BASE}/{c}/monitored", json={"version": 1, "monitored": True},
                      headers=ADMIN_BG)
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail == {"code": "monitor_cap_reached", "reason": REASON, "max_services": 2}
    assert repo.writes == 0 and repo.services[c]["monitored"] is False
    audit = _denied(app)
    assert audit["action"] == "biz_service_monitored" and audit["before"]["id"] == c


def test_patch_on_at_cap_409_and_create_monitored_409() -> None:
    client, app, repo = _app()
    _, _, c = _seed_full(repo)
    res = client.patch(f"{BASE}/{c}", json={"version": 1, "monitored": True, "tier": 1},
                       headers=ADMIN_BG)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "monitor_cap_reached"
    assert repo.services[c]["tier"] is None  # 함께 보낸 다른 칸도 저장하지 않는다
    assert _denied(app)["action"] == "biz_service_update"
    res = client.post(BASE, json={"service_key": "new", "display_name": "N", "monitored": True},
                      headers=ADMIN_BG)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "monitor_cap_reached"
    assert _denied(app)["action"] == "biz_service_create" and repo.writes == 0
    ok = client.post(BASE, json={"service_key": "new", "display_name": "N"}, headers=ADMIN_BG)
    assert ok.status_code == 201, ok.text  # 끈 채 만들기는 상한과 무관


def test_already_on_resave_and_turn_off_then_on_pass() -> None:
    client, _, repo = _app()
    a, _, c = _seed_full(repo)
    # 이미 켜진 서비스의 재저장(편집 화면은 monitored를 늘 보낸다)은 막지 않는다
    res = client.patch(f"{BASE}/{a}", json={"version": 1, "monitored": True, "tier": 2},
                       headers=ADMIN_BG)
    assert res.status_code == 200, res.text
    off = client.post(f"{BASE}/{a}/monitored", json={"version": 2, "monitored": False},
                      headers=ADMIN_BG)
    assert off.status_code == 200 and off.json()["service"]["monitored"] is False
    on = client.post(f"{BASE}/{c}/monitored", json={"version": 1, "monitored": True},
                     headers=ADMIN_BG)
    assert on.status_code == 200 and on.json()["service"]["monitored"] is True


def test_version_conflict_reported_before_cap() -> None:
    client, _, repo = _app()
    _, _, c = _seed_full(repo)
    res = client.post(f"{BASE}/{c}/monitored", json={"version": 9, "monitored": True},
                      headers=ADMIN_BG)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "version_conflict"


def test_count_failure_503_without_exception_text() -> None:
    client, app, repo = _app()
    _, _, c = _seed_full(repo)
    repo.count_fail = True
    res = client.post(f"{BASE}/{c}/monitored", json={"version": 1, "monitored": True},
                      headers=ADMIN_BG)
    assert res.status_code == 503 and res.json()["detail"]["code"] == "biz_service_store_failed"
    assert SECRET_TEXT not in res.text and repo.writes == 0
    audit = _audits(app)[-1]
    assert audit["outcome"] == "failed" and SECRET_TEXT not in str(audit)


def test_default_cap_20_when_setting_absent() -> None:
    client, _, repo = _app(cap=None)  # W4 설정 대역에는 상한 칸이 없다 → 기본 20
    for i in range(19):
        repo.seed(f"on{i}", monitored=True)
    last = repo.seed("x")["id"]
    nxt = repo.seed("y")["id"]
    res = client.post(f"{BASE}/{last}/monitored", json={"version": 1, "monitored": True},
                      headers=ADMIN_BG)
    assert res.status_code == 200, res.text
    res = client.post(f"{BASE}/{nxt}/monitored", json={"version": 1, "monitored": True},
                      headers=ADMIN_BG)
    assert res.status_code == 409 and res.json()["detail"]["max_services"] == 20
