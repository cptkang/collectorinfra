"""plans/195 W2 — 조사 id 조회 응답의 「질의응답에서 확인할 질의」(`requery`).

- 플래그 off: 응답에 `requery` 키 없음(비트 동일)
- 플래그 on: 제니퍼 플레이북 브리핑 → 항목 · 존/소유자 인가 거부는 종전 403
- 운영자 뷰도 가린 사용자 뷰에서 만든다(원문 비밀값이 prompt에 없음)
- 폴스타 축: `polestar_source`가 사용자 조회 범위 밖이면 `disabled_reason`
- 제니퍼 축: 관측 소스 `apm` 권한이 없으면 `disabled_reason`
- 질의 생성 예외: 빈 목록(조사 응답은 깨지지 않음)
- by-alarm 경로는 가산하지 않는다

DB·Redis·조사 서비스는 띄우지 않는다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.domain.investigation_requery as requery_mod
from src.routing.zones import ZONE_BANKJON, ZONE_GONGJON
from tests.test_api.test_plan187_w11_manual import (
    _client,
    _config,
    _reset_poll_state,  # noqa: F401 — autouse 픽스처 재사용
    _store,
)

GONGJON_DB = "polestar_cm_gp"
ADMIN = {"sub": "admin1", "role": "admin"}
GONGJON_USER = {"sub": "u1", "role": "user", "alarm_zones": [ZONE_GONGJON]}
BANKJON_USER = {"sub": "u2", "role": "user", "alarm_zones": [ZONE_BANKJON]}
SECRET = "abcdefghijklmnopqrstuvwxyz123456"
CLEAN_INST = "was01"
SECRET_INST = f"was02 Bearer {SECRET}"


def _playbook(**scope_over) -> dict:
    scope = {
        "host": "kbl-host-01",
        "targets": [{"instance": CLEAN_INST}, {"instance": SECRET_INST}],
        "incident_window": {"start": "2026-10-26T19:10:00", "end": "2026-10-26T19:40:00"},
        "polestar_server": "KBL-HOST-01",
        "polestar_source": GONGJON_DB,
        "polestar_link": "linked",
    }
    scope.update(scope_over)
    return {
        "scope": scope,
        "sections": [
            {"step": "S2", "title": "골든 시그널", "status": "ok", "action": "apm.golden_signals"},
        ],
    }


def _seed(tmp_path, **scope_over) -> None:  # noqa: ANN001
    _store(tmp_path).record(
        investigation_id="inv-1", alarm_ids=["a1"], status="done", source="inline",
        briefing={"playbook": _playbook(**scope_over)}, alarm_db_ids={"a1": GONGJON_DB},
    )


def _requery_config(tmp_path, *, enabled: bool = True):  # noqa: ANN001, ANN202
    config = _config(tmp_path, investigation_requery_enabled=enabled)
    config.multi_db = SimpleNamespace(
        get_active_db_ids=lambda: ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]
    )
    return config


def _get(config, user, path: str = "/api/v1/alarm/investigations/inv-1"):  # noqa: ANN001, ANN202
    return _client(config, user).get(path)


@pytest.fixture
def polestar_catalog(monkeypatch):  # noqa: ANN001, ANN201
    """카탈로그에 폴스타 축 항목 1건을 주입한다(현 카탈로그는 제니퍼 축만 — 골드 대기)."""
    tpl = requery_mod.RequeryTemplate(
        "apm.golden_signals", "polestar", "server", "incident", "text2sql", "", "TEST-PS-1",
        False, "{target} 서버 {window} CPU 사용률 보여줘",
    )
    monkeypatch.setattr(requery_mod, "REQUERY_CATALOG", (*requery_mod.REQUERY_CATALOG, tpl))


def test_flag_off_has_no_requery_key(tmp_path) -> None:  # noqa: ANN001
    _seed(tmp_path)
    off = _get(_requery_config(tmp_path, enabled=False), GONGJON_USER)
    assert off.status_code == 200
    assert "requery" not in off.json()


def test_flag_off_matches_response_without_flag_field(tmp_path) -> None:  # noqa: ANN001
    """플래그 off 응답 = 플래그 필드가 없던 종전 설정의 응답(비트 동일)."""
    _seed(tmp_path)
    legacy = _requery_config(tmp_path, enabled=False)
    del legacy.noise_gate.investigation_requery_enabled
    off = _get(_requery_config(tmp_path, enabled=False), GONGJON_USER).json()
    assert off == _get(legacy, GONGJON_USER).json()


def test_flag_on_builds_items_from_apm_playbook(tmp_path) -> None:  # noqa: ANN001
    _seed(tmp_path)
    res = _get(_requery_config(tmp_path), GONGJON_USER)
    assert res.status_code == 200
    items = res.json()["requery"]
    assert items, "제니퍼 플레이북에서 질의가 만들어져야 한다"
    assert {i["axis"] for i in items} == {"apm"}
    prompts = [i["prompt"] for i in items]
    assert any(
        p.startswith(CLEAN_INST) and "2026년 10월 26일 오후 7시 10분부터 오후 7시 40분까지" in p
        for p in prompts
    )
    for item in items:
        assert {"step", "title", "prompt", "path", "source_ref", "realtime", "axis"} <= set(item)
        assert "disabled_reason" not in item


def test_by_alarm_route_does_not_attach(tmp_path) -> None:  # noqa: ANN001
    _seed(tmp_path)
    from tests.test_api.test_plan187_w11_manual import _seed_alarm

    _seed_alarm(tmp_path)
    res = _get(_requery_config(tmp_path), GONGJON_USER, "/api/v1/alarm/investigations/by-alarm/a1")
    assert res.status_code == 200
    assert "requery" not in res.json()


def test_zone_denied_stays_403(tmp_path) -> None:  # noqa: ANN001
    _seed(tmp_path)
    assert _get(_requery_config(tmp_path), BANKJON_USER).status_code == 403


def test_unbound_request_non_owner_stays_403(tmp_path) -> None:  # noqa: ANN001
    """알람 없는 사용자 요청 조사 — 비소유자는 종전대로 403(인가 재사용)."""
    _store(tmp_path).record(
        investigation_id="inv-1", alarm_ids=[], kind="manual", status="done", source="manual",
        owner_sub="someone-else", briefing={"playbook": _playbook()},
    )
    assert _get(_requery_config(tmp_path), GONGJON_USER).status_code == 403


@pytest.mark.parametrize("user", [ADMIN, GONGJON_USER])
def test_operator_and_user_get_same_masked_items(tmp_path, user) -> None:  # noqa: ANN001
    _seed(tmp_path)
    res = _get(_requery_config(tmp_path), user)
    items = res.json()["requery"]
    assert items
    assert all(SECRET not in i["prompt"] for i in items)
    assert all(not i["prompt"].startswith("was02") for i in items)


def test_operator_and_user_requery_identical(tmp_path) -> None:  # noqa: ANN001
    _seed(tmp_path)
    config = _requery_config(tmp_path)
    admin = _get(config, ADMIN).json()
    user = _get(config, GONGJON_USER).json()
    assert SECRET in str(admin["briefing"])  # 운영자 뷰 원문에는 비밀값이 있다
    assert admin["requery"] == user["requery"]


def test_polestar_item_in_scope_has_no_reason(tmp_path, polestar_catalog) -> None:  # noqa: ANN001
    _seed(tmp_path)
    user = {**GONGJON_USER, "allowed_db_ids": [GONGJON_DB]}
    items = [i for i in _get(_requery_config(tmp_path), user).json()["requery"]
             if i["axis"] == "polestar"]
    assert len(items) == 1
    assert items[0]["polestar_source"] == GONGJON_DB
    assert items[0]["prompt"].startswith("KBL-HOST-01 서버")
    assert "disabled_reason" not in items[0]


def test_polestar_item_none_allowed_means_all(tmp_path, polestar_catalog) -> None:  # noqa: ANN001
    """`allowed_db_ids` 미설정(None) = 전체 허용(`authorized_db_ids` 관례)."""
    _seed(tmp_path)
    items = [i for i in _get(_requery_config(tmp_path), GONGJON_USER).json()["requery"]
             if i["axis"] == "polestar"]
    assert items and "disabled_reason" not in items[0]


def test_polestar_item_out_of_scope_disabled(tmp_path, polestar_catalog) -> None:  # noqa: ANN001
    _seed(tmp_path)
    user = {**GONGJON_USER, "allowed_db_ids": ["polestar_b0"]}
    items = _get(_requery_config(tmp_path), user).json()["requery"]
    polestar = [i for i in items if i["axis"] == "polestar"]
    assert len(polestar) == 1
    assert polestar[0]["disabled_reason"] == alarm_reason("_REQUERY_DB_DENIED")
    # 제니퍼 축은 DB 범위와 무관하다
    assert all("disabled_reason" not in i for i in items if i["axis"] == "apm")


def test_polestar_item_inactive_db_disabled(tmp_path, polestar_catalog) -> None:  # noqa: ANN001
    """활성 DB가 아니면 질의응답이 조회하지 못한다 — 범위 밖과 같은 사유."""
    _seed(tmp_path, polestar_source="polestar")
    items = [i for i in _get(_requery_config(tmp_path), ADMIN).json()["requery"]
             if i["axis"] == "polestar"]
    assert items[0]["disabled_reason"] == alarm_reason("_REQUERY_DB_DENIED")


def test_polestar_item_without_source_disabled(tmp_path, polestar_catalog) -> None:  # noqa: ANN001
    _seed(tmp_path, polestar_source=None)
    items = [i for i in _get(_requery_config(tmp_path), ADMIN).json()["requery"]
             if i["axis"] == "polestar"]
    assert items[0]["disabled_reason"] == alarm_reason("_REQUERY_DB_UNKNOWN")


def test_apm_items_disabled_without_apm_source(tmp_path) -> None:  # noqa: ANN001
    _seed(tmp_path)
    user = {**GONGJON_USER, "allowed_sources": ["doc"]}
    items = _get(_requery_config(tmp_path), user).json()["requery"]
    assert items
    assert all(i["disabled_reason"] == alarm_reason("_REQUERY_APM_DENIED") for i in items)


def test_generation_error_yields_empty_list(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    _seed(tmp_path)

    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("boom")

    monkeypatch.setattr(requery_mod, "build_requery", _boom)
    res = _get(_requery_config(tmp_path), GONGJON_USER)
    assert res.status_code == 200
    body = res.json()
    assert body["requery"] == []
    assert body["status"] == "done"


def test_non_playbook_briefing_yields_empty_list(tmp_path) -> None:  # noqa: ANN001
    _store(tmp_path).record(
        investigation_id="inv-1", alarm_ids=["a1"], status="done", source="inline",
        briefing={"summary": "HolmesGPT 단독 조사"}, alarm_db_ids={"a1": GONGJON_DB},
    )
    assert _get(_requery_config(tmp_path), GONGJON_USER).json()["requery"] == []


@pytest.mark.parametrize("user", [ADMIN, GONGJON_USER])
def test_partial_mask_dropped_with_production_mask_pattern(tmp_path, user) -> None:  # noqa: ANN001
    """운영 기본 `mask_pattern="***MASKED***"`에서도 부분 가림(`mask_secrets` 표지) 대상은 생략."""
    _seed(tmp_path)
    config = _requery_config(tmp_path)
    config.security.mask_pattern = "***MASKED***"
    items = _get(config, user).json()["requery"]
    assert items, "깨끗한 인스턴스 질의는 남아야 한다"
    assert all("***" not in i["prompt"] and SECRET not in i["prompt"] for i in items)
    assert all(not i["prompt"].startswith("was02") for i in items)


def alarm_reason(name: str) -> str:
    from src.api.routes import alarm as alarm_routes

    return str(getattr(alarm_routes, name))
