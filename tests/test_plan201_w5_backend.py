"""plans/201 W5 — 조치 지원 · 카드 재배치: 정본 로더 · `/auth/me` · `/alarm/capabilities` · 설정.

  1. 로더(`parse_requery_axes` · `parse_checklists`) — 어휘 밖 영역·형식 밖 id·빈/긴 문장은 제외 + 경고, 상한 8.
  2. 선별(`select_checklists`) — 담당 영역만(선언 순서) · 미지정 = 공통 1벌 · 공통도 없으면 키 없음.
  3. 정본 `config/duty_areas.yaml` — 점검 순서 키가 실제 플레이북 id(`sre_agent/config/playbooks/*.yaml`)이고
     축 → 영역 표가 `build_requery` 축 값(`Axis`)과 맞는다.
  4. `/auth/me` — 플래그 off면 응답 키 집합 그대로(실효 AND) · on이면 `duty_action_assist` shape(실 라우트).
  5. `/alarm/capabilities` — `card_flow_enabled`는 켜졌을 때만 실린다(off = 키 부재 · 담당 업무와 독립).
  6. 설정 — env 이름 하나씩 · 실효 AND · 카탈로그 · 도움말 · `.env.example` · 기동 시 1회 해석 · 인가 모듈 비참조.
"""

from __future__ import annotations

import logging
import re
import typing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
import yaml
from fastapi.testclient import TestClient

from src.api import duty_area_support
from src.domain.duty_areas import (
    MAX_CHECKLIST_STEP_LENGTH,
    MAX_CHECKLIST_STEPS,
    parse_checklists,
    parse_requery_axes,
    parse_vocabulary,
)
from src.domain.user import User, UserRole, UserStatus
from src.infrastructure.duty_area_vocabulary import read_duty_area_vocabulary

_ROOT = Path(__file__).resolve().parents[1]
_USER_SECRET = "u" * 40
_ADMIN_SECRET = "a" * 40
_KNOWN = ("alpha", "beta", "gamma")


# ─── 1. 로더 ─────────────────────────────────────────────────────────────────


def test_parse_requery_axes_skips_unknown(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="src.domain.duty_areas"):
        out = parse_requery_axes({"apm": "beta", "polestar": "outsider", "": "alpha", "x": 3}, _KNOWN)
    assert out == {"apm": "beta"}
    assert caplog.text.count("requery_axes") == 3
    assert parse_requery_axes(None, _KNOWN) == {}
    assert parse_requery_axes(["apm"], _KNOWN) == {}


def test_parse_checklists_validation_and_caps(caplog) -> None:
    long_line = "가" * (MAX_CHECKLIST_STEP_LENGTH + 1)
    raw = {
        "host.cpu": {
            "common": ["공통 1"],
            "beta": ["  베타 1  ", "", 3, long_line, "베타 2"],
            "outsider": ["어휘 밖"],
            "alpha": [f"알파 {i}" for i in range(MAX_CHECKLIST_STEPS + 3)],
            "gamma": "목록 아님",
        },
        "Bad Id": {"alpha": ["x"]},
        "was.sql": ["매핑 아님"],
        "was.heap": {"alpha": []},
    }
    with caplog.at_level(logging.WARNING, logger="src.domain.duty_areas"):
        out = parse_checklists(raw, _KNOWN)
    assert list(out) == ["host.cpu"]
    cpu = out["host.cpu"]
    assert list(cpu) == ["alpha", "beta", "common"]                 # 선언 순서 · common은 끝
    assert cpu["beta"] == ("베타 1", "베타 2")
    assert len(cpu["alpha"]) == MAX_CHECKLIST_STEPS
    text = caplog.text
    for needle in ("outsider", "Bad Id", "was.sql", "목록이 아님", f"{MAX_CHECKLIST_STEP_LENGTH}자 초과",
                   f"앞 {MAX_CHECKLIST_STEPS}개만"):
        assert needle in text, needle
    assert long_line not in text                                    # 문장 원문은 로그에 없다
    assert parse_checklists(None, _KNOWN) == {} and parse_checklists("x", _KNOWN) == {}


# ─── 2. 선별 ─────────────────────────────────────────────────────────────────

_VOCAB = parse_vocabulary({
    "areas": [{"code": "alpha", "label": "알파"}, {"code": "beta", "label": "베타"}, {"code": "gamma", "label": "감마"}],
    "requery_axes": {"apm": "beta", "polestar": "alpha"},
    "checklists": {
        "host.cpu": {"beta": ["b1"], "alpha": ["a1", "a2"], "common": ["c1"]},
        "was.heap": {"common": ["c2"]},
        "was.sql": {"gamma": ["g1"]},
    },
})


def test_select_checklists_user_areas_declared_order() -> None:
    out = _VOCAB.select_checklists(["beta", "alpha", "nope"])
    assert out["host.cpu"] == [
        {"area": "alpha", "area_label": "알파", "steps": ["a1", "a2"]},
        {"area": "beta", "area_label": "베타", "steps": ["b1"]},
    ]
    assert out["was.heap"] == [{"area": "common", "area_label": "공통", "steps": ["c2"]}]
    assert "was.sql" not in out                                     # 담당 문안 없음 · 공통도 없음 → 키 없음


def test_select_checklists_unassigned_common_only() -> None:
    for areas in (None, [], ["nope"]):
        out = _VOCAB.select_checklists(areas)
        assert out == {"host.cpu": [{"area": "common", "area_label": "공통", "steps": ["c1"]}],
                       "was.heap": [{"area": "common", "area_label": "공통", "steps": ["c2"]}]}
    assert _VOCAB.requery_axes_meta() == {"apm": {"area": "beta", "label": "베타"},
                                          "polestar": {"area": "alpha", "label": "알파"}}


def test_vocabulary_without_w5_sections_is_empty() -> None:
    v = parse_vocabulary({"areas": [{"code": "alpha", "label": "알파"}]})
    assert v.requery_axes_meta() == {} and v.select_checklists(["alpha"]) == {}


# ─── 3. 정본 ─────────────────────────────────────────────────────────────────


def _real_vocab():
    return read_duty_area_vocabulary(_ROOT / "config" / "duty_areas.yaml")


def test_real_checklist_keys_are_real_playbook_ids(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="src.domain.duty_areas"):
        vocab = _real_vocab()
    assert "점검 순서" not in caplog.text and "requery_axes" not in caplog.text   # 정본은 경고 0
    playbooks = {p.stem for p in (_ROOT / "sre_agent" / "config" / "playbooks").glob("*.yaml")}
    assert vocab.checklists and set(vocab.checklists) <= playbooks
    raw = yaml.safe_load((_ROOT / "config" / "duty_areas.yaml").read_text(encoding="utf-8"))
    assert set(raw["checklists"]) == set(vocab.checklists)          # 정본 문안은 전부 로드된다
    for by_area in vocab.checklists.values():
        assert "common" in by_area                                  # 미지정 사용자도 볼 것이 있다


def test_real_requery_axes_match_requery_axis_type() -> None:
    from src.domain.investigation_requery import REQUERY_CATALOG, Axis

    vocab = _real_vocab()
    axes = set(typing.get_args(Axis))
    assert set(vocab.requery_axes) == axes
    assert {t.axis for t in REQUERY_CATALOG} <= set(vocab.requery_axes)
    assert vocab.requery_axes_meta()["apm"]["area"] == "was_mw"


def test_real_checklist_text_is_human_gated_and_has_no_literals() -> None:
    text = (_ROOT / "config" / "duty_areas.yaml").read_text(encoding="utf-8")
    seg = text[text.index("checklists:"):]
    assert "초안 · 피드백으로 교정" in text
    for bad in ("SELECT ", "UPDATE ", "DELETE ", "kill -9", "재기동하세요", "polestar_", "POLESTAR"):
        assert bad not in seg, bad
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", seg)       # IP 꼴 없음


# ─── 4. /auth/me ─────────────────────────────────────────────────────────────

_BASE_USER_KEYS = ["user_id", "username", "role", "department", "allowed_db_ids",
                   "alarm_zones", "is_protected", "status", "last_login_at"]


@pytest.fixture()
def vocab(monkeypatch):
    monkeypatch.setattr(duty_area_support, "load_duty_area_vocabulary", lambda: _VOCAB)


def _user(user_id: str, **kw) -> User:
    return User(user_id=user_id, username=user_id, hashed_password="x", role=UserRole.USER,
                status=UserStatus.ACTIVE, **kw)


def _hdr(sub: str) -> dict[str, str]:
    tok = jwt.encode({"sub": sub, "type": "user", "exp": datetime.now(UTC) + timedelta(hours=1)},
                     _USER_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


def _app(*, duty: bool = False, assist: bool = False, hints: bool = False, focus: bool = False,
         flow: bool = False):
    from src.api.server import create_app
    from src.config import AdminConfig, AlarmCardFlowConfig, AppConfig, AuthConfig, DutyAreaConfig

    cfg = AppConfig(
        auth=AuthConfig(enabled=True, jwt_secret=_USER_SECRET),
        admin=AdminConfig(jwt_secret=_ADMIN_SECRET),
        duty_area=DutyAreaConfig(_env_file=None, enabled=duty, action_assist_enabled=assist,
                                 welcome_hints_enabled=hints, alarm_focus_enabled=focus),
        alarm_card_flow=AlarmCardFlowConfig(_env_file=None, enabled=flow),
    )
    app = create_app(cfg)
    users = {"both": _user("both", duty_areas=["beta", "alpha"]), "none": _user("none")}
    app.state.user_repo = SimpleNamespace(get_by_user_id=AsyncMock(side_effect=lambda uid: users.get(uid)))
    return app


def _me(app, sub: str = "both") -> dict:
    resp = TestClient(app).get("/api/v1/auth/me", headers=_hdr(sub))
    assert resp.status_code == 200
    return resp.json()


def test_create_app_resolves_flags_once(vocab) -> None:
    assert _app(duty=True, assist=True).state.duty_action_assist_enabled is True
    assert _app(duty=True).state.duty_action_assist_enabled is False
    assert _app(assist=True).state.duty_action_assist_enabled is False
    assert _app(flow=True).state.alarm_card_flow_enabled is True
    assert _app().state.alarm_card_flow_enabled is False


def test_me_off_keeps_key_set(vocab) -> None:
    assert list(_me(_app())) == _BASE_USER_KEYS
    assert list(_me(_app(assist=True))) == _BASE_USER_KEYS          # 실효 AND — 담당 지정 off면 키 없음
    assert list(_me(_app(flow=True))) == _BASE_USER_KEYS            # 카드 재배치는 /auth/me에 싣지 않는다
    assert list(_me(_app(duty=True))) == _BASE_USER_KEYS + ["duty_areas", "duty_area_options"]


def test_me_duty_action_assist_shape(vocab) -> None:
    body = _me(_app(duty=True, assist=True))
    assert list(body) == _BASE_USER_KEYS + ["duty_areas", "duty_area_options", "duty_action_assist"]
    meta = body["duty_action_assist"]
    assert list(meta) == ["requery_axes", "checklists"]
    assert meta["requery_axes"] == {"apm": {"area": "beta", "label": "베타"},
                                    "polestar": {"area": "alpha", "label": "알파"}}
    assert meta["checklists"]["host.cpu"] == [
        {"area": "alpha", "area_label": "알파", "steps": ["a1", "a2"]},
        {"area": "beta", "area_label": "베타", "steps": ["b1"]},
    ]
    assert "was.sql" not in meta["checklists"]
    none = _me(_app(duty=True, assist=True), "none")["duty_action_assist"]
    assert none["checklists"]["host.cpu"] == [{"area": "common", "area_label": "공통", "steps": ["c1"]}]
    # 모든 W 플래그를 켜면 키 순서는 W1 → W3 → W2 → W5
    allkeys = list(_me(_app(duty=True, assist=True, hints=True, focus=True)))
    assert allkeys[-3:] == ["duty_alarm_focus", "duty_welcome_queries", "duty_action_assist"]


def test_login_response_never_carries_assist_key() -> None:
    from src.api.schemas import UserInfoResponse

    info = UserInfoResponse(user_id="u", username="u", role="user")
    assert "duty_action_assist" not in info.model_dump()


# ─── 5. /alarm/capabilities ──────────────────────────────────────────────────


def _caps(app) -> dict:
    resp = TestClient(app).get("/api/v1/alarm/capabilities", headers=_hdr("both"))
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_capabilities_card_flow_only_when_on(vocab) -> None:
    off = _caps(_app())
    assert "card_flow_enabled" not in off
    on = _caps(_app(flow=True))
    assert on["card_flow_enabled"] is True
    assert {k: v for k, v in on.items() if k != "card_flow_enabled"} == off   # 나머지 키·값 그대로
    # 담당 업무 플래그와 독립
    assert "card_flow_enabled" not in _caps(_app(duty=True, assist=True))
    assert _caps(_app(duty=False, flow=True))["card_flow_enabled"] is True


def test_capabilities_schema_omits_unset_key() -> None:
    from src.api.routes.alarm_schemas import AlarmCapabilitiesResponse

    base = dict(feedback_enabled=False, incident_tracking=False, sse_bridge=False,
                suppress_stream=False, suppress_max_severity=2)
    assert "card_flow_enabled" not in AlarmCapabilitiesResponse(**base).model_dump()
    assert AlarmCapabilitiesResponse(**base, card_flow_enabled=True).model_dump()["card_flow_enabled"] is True


# ─── 6. 설정 ──────────────────────────────────────────────────────────────────


def test_env_names_one_each(monkeypatch) -> None:
    from src.config import AlarmCardFlowConfig, DutyAreaConfig

    for name in ("DUTY_ACTION_ASSIST_ENABLED", "ACTION_ASSIST_ENABLED", "DUTY_AREA_ACTION_ASSIST_ENABLED",
                 "DUTY_AREA_ENABLED", "ALARM_CARD_FLOW_ENABLED", "CARD_FLOW_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    assert DutyAreaConfig(_env_file=None).action_assist_enabled is False
    assert AlarmCardFlowConfig(_env_file=None).enabled is False
    monkeypatch.setenv("ACTION_ASSIST_ENABLED", "true")
    monkeypatch.setenv("DUTY_AREA_ACTION_ASSIST_ENABLED", "true")
    monkeypatch.setenv("CARD_FLOW_ENABLED", "true")
    assert DutyAreaConfig(_env_file=None).action_assist_enabled is False
    assert AlarmCardFlowConfig(_env_file=None).enabled is False
    monkeypatch.setenv("DUTY_ACTION_ASSIST_ENABLED", "true")
    cfg = DutyAreaConfig(_env_file=None)
    assert cfg.action_assist_enabled is True and cfg.action_assist_effective is False
    monkeypatch.setenv("DUTY_AREA_ENABLED", "true")
    assert DutyAreaConfig(_env_file=None).action_assist_effective is True
    monkeypatch.setenv("ALARM_CARD_FLOW_ENABLED", "true")
    assert AlarmCardFlowConfig(_env_file=None).enabled is True


def test_constructor_field_names_and_effective_and() -> None:
    from src.config import AppConfig, DutyAreaConfig

    assert DutyAreaConfig(_env_file=None, enabled=True, action_assist_enabled=True).action_assist_effective is True
    assert DutyAreaConfig(_env_file=None, enabled=False, action_assist_enabled=True).action_assist_effective is False
    assert DutyAreaConfig(_env_file=None, enabled=True).action_assist_effective is False
    every = DutyAreaConfig(_env_file=None, enabled=True, alarm_focus_enabled=True, welcome_hints_enabled=True,
                           action_assist_enabled=True)
    assert every.alarm_focus_effective and every.welcome_hints_effective and every.action_assist_effective
    assert AppConfig().alarm_card_flow.__class__.__name__ == "AlarmCardFlowConfig"


def test_settings_catalog_help_and_env_example() -> None:
    from src.api.settings_catalog import GROUP_ORDER, GROUP_TITLES, field_index

    index = field_index()
    assert index["DUTY_ACTION_ASSIST_ENABLED"].group_key == "duty_area"
    assert index["ALARM_CARD_FLOW_ENABLED"].group_key == "alarm_card_flow"
    assert "alarm_card_flow" in GROUP_ORDER and GROUP_TITLES["alarm_card_flow"] == "알람 카드 읽는 순서"
    duty_help = (_ROOT / "config/settings_help/duty_area.yaml").read_text(encoding="utf-8")
    assert "\n  DUTY_ACTION_ASSIST_ENABLED:\n" in duty_help
    flow_help = yaml.safe_load((_ROOT / "config/settings_help/alarm_card_flow.yaml").read_text(encoding="utf-8"))
    assert flow_help["group"] == "alarm_card_flow" and "ALARM_CARD_FLOW_ENABLED" in flow_help["settings"]
    example = (_ROOT / ".env.example").read_text(encoding="utf-8")
    for key in ("DUTY_ACTION_ASSIST_ENABLED", "ALARM_CARD_FLOW_ENABLED"):
        assert len(re.findall(rf"^#?\s*{key}=false$", example, re.M)) == 1, key


def test_authz_modules_do_not_read_assist_values() -> None:
    for rel in ("src/api/dependencies.py", "src/api/routes/alarm.py"):
        text = (_ROOT / rel).read_text(encoding="utf-8")
        assert "duty_action_assist" not in text and "checklists" not in text, rel
