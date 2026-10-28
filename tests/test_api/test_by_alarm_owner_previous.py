"""D-347 부기 — 수동 전용 모드에서 by-alarm 소유자 열람의 `previous`.

자동 조사가 꺼지면 알람의 대표 조사가 늘 내 수동 조사라 소유자 열람(존 검사 생략)이 된다. 화면은
`previous`를 다시 분석 켜짐 신호로 쓰므로, 소유자도 존 검사를 따로 통과하면 `previous`를 받는다.
존 밖 소유자는 종전대로 받지 않는다(접수도 403 — `test_plan187_w12_view_authz` 고정).
"""

from __future__ import annotations

from src.routing.zones import ZONE_BANKJON, ZONE_GONGJON
from tests.test_api.test_plan187_w12_view_authz import (
    GONGJON_DB,
    _config,
    _get,
    _reset_poll_state,  # noqa: F401 — autouse 픽스처
    _seed_manual,
    _store,
)

OWNER_IN_ZONE = {"sub": "u1", "role": "user", "alarm_zones": [ZONE_GONGJON]}
OTHER_IN_ZONE = {"sub": "u9", "role": "user", "alarm_zones": [ZONE_GONGJON]}


def _seed_older(tmp_path, inv: str, *, owner: str, alarm_ids=("a1",), db_ids=None) -> None:  # noqa: ANN001
    _store(tmp_path).record(
        investigation_id=inv, alarm_ids=list(alarm_ids), kind="manual", status="done",
        source="manual", alarm_db_ids=db_ids or {a: GONGJON_DB for a in alarm_ids},
        owner_sub=owner, origin="manual", parent_alarm_id="a1",
    )


def test_owner_in_zone_gets_previous(tmp_path):  # noqa: ANN001
    _seed_older(tmp_path, "man-0", owner="u9")
    _seed_manual(tmp_path)
    body = _get(_config(tmp_path), OWNER_IN_ZONE, "by-alarm/a1").json()
    assert body["investigation_id"] == "man-1"
    assert [p["investigation_id"] for p in body["previous"]] == ["man-0"]


def test_owner_in_zone_single_investigation_gets_empty_previous(tmp_path):  # noqa: ANN001
    """조사가 하나뿐이어도 빈 목록이 붙는다 — 버튼 신호는 목록 유무가 아니라 배열 존재다."""
    _seed_manual(tmp_path)
    body = _get(_config(tmp_path), OWNER_IN_ZONE, "by-alarm/a1").json()
    assert body["investigation_id"] == "man-1" and body["previous"] == []


def test_owner_same_as_non_owner_previous(tmp_path):  # noqa: ANN001
    """소유자가 받는 목록은 같은 존 비소유자가 받는 목록과 같은 규칙이다(고른 조사만 다르다)."""
    _seed_older(tmp_path, "man-0", owner="u9")
    _seed_manual(tmp_path)
    config = _config(tmp_path)
    owner = _get(config, OWNER_IN_ZONE, "by-alarm/a1").json()
    other = _get(config, OTHER_IN_ZONE, "by-alarm/a1").json()
    assert other["investigation_id"] == "man-1"
    assert owner["previous"] == other["previous"]


def test_owner_with_out_of_zone_member_alarm_gets_no_previous(tmp_path):  # noqa: ANN001
    """내 조사에 걸린 다른 알람이 존 밖이면 존 검사 실패 — 종전처럼 `previous` 없음."""
    _store(tmp_path).record(
        investigation_id="man-1", alarm_ids=["a1", "a2"], kind="manual", status="done",
        source="manual", alarm_db_ids={"a1": GONGJON_DB, "a2": "polestar_b0"},
        owner_sub="u1", origin="manual", parent_alarm_id="a1",
    )
    resp = _get(_config(tmp_path), OWNER_IN_ZONE, "by-alarm/a1")
    assert resp.status_code == 200 and "previous" not in resp.json()


def test_owner_outside_zone_still_no_previous(tmp_path):  # noqa: ANN001
    _seed_manual(tmp_path)
    owner_bank = {"sub": "u1", "role": "user", "alarm_zones": [ZONE_BANKJON]}
    resp = _get(_config(tmp_path), owner_bank, "by-alarm/a1")
    assert resp.status_code == 200 and "previous" not in resp.json()


def test_manual_flag_off_never_previous(tmp_path):  # noqa: ANN001
    _seed_manual(tmp_path)
    resp = _get(_config(tmp_path, manual=False), OWNER_IN_ZONE, "by-alarm/a1")
    assert "previous" not in resp.json()
