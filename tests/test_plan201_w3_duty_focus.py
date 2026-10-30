"""plans/201 W3 — 알람 화면 담당 강조: 단서 표(`cause_hints`) · `/auth/me` 메타 · 관리자 현황 유형 섹션 · 설정.

  1. 정본 `config/duty_areas.yaml` `cause_hints` — 계획 표대로 매핑 · 외부 연계 고정 문구 · 알려진 미매핑.
  2. 로더 검증 — 어휘 밖 영역·근거 문구 없음·형식 오류는 경고 후 무시(앱은 계속).
  3. 대조 정규화 — strip·대문자·연속 공백→`_`·`ERROR_`/`WARNING_` 접두 1회(프론트와 같은 픽스처 ·
     `tests/test_plan201_w3_frontend.py`가 같은 `NORMALIZE_CASES`를 node로 돌린다).
  4. `/auth/me` — 플래그 유효 on일 때만 `duty_alarm_focus` 키 · off면 W1 응답 키 집합 그대로.
  5. 현황 API — on이면 `sections`에 제니퍼 유형 항목(미매핑 상위 5 · known_unmapped 접음 · 배지 제외) ·
     off·`mapping=false`는 W1 계약 그대로.
  6. 설정 — env 키 `DUTY_ALARM_FOCUS_ENABLED` · 카탈로그 · 도움말 · `.env.example`.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi.testclient import TestClient

from src.api import duty_area_support
from src.domain.duty_areas import (
    normalize_apm_event_type,
    parse_cause_hints,
    parse_vocabulary,
    unmapped_apm_event_types,
)
from src.domain.user import User, UserRole, UserStatus
from src.infrastructure.duty_area_vocabulary import read_duty_area_vocabulary

_ROOT = Path(__file__).resolve().parents[1]
_USER_SECRET = "u" * 40
_ADMIN_SECRET = "a" * 40

#: 대조 정규화 픽스처(입력 → 기대). 프론트 테스트가 같은 목록을 쓴다 — 두 구현의 규칙을 함께 고정한다.
NORMALIZE_CASES: list[tuple[object, str]] = [
    ("BAD_RESPONSE_TIME_SQL", "BAD_RESPONSE_TIME_SQL"),
    ("bad_response_time_sql", "BAD_RESPONSE_TIME_SQL"),
    ("  METRICS EVENT ", "METRICS_EVENT"),
    ("metrics   event", "METRICS_EVENT"),
    ("METRICS\tEVENT", "METRICS_EVENT"),
    ("ERROR_DB_CONNECTION_FAIL", "DB_CONNECTION_FAIL"),
    ("warning_jvm_heap_mem_high", "JVM_HEAP_MEM_HIGH"),
    ("ERROR_WARNING_X", "WARNING_X"),           # 접두는 1회만
    ("", ""),
    ("   ", ""),
    (None, ""),
    (17, ""),
]


@pytest.mark.parametrize(("raw", "expected"), NORMALIZE_CASES)
def test_normalize_apm_event_type(raw, expected) -> None:
    assert normalize_apm_event_type(raw) == expected


# ─── 1. 정본 파일 ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def real_vocab():
    return read_duty_area_vocabulary(_ROOT / "config" / "duty_areas.yaml")


def test_real_cause_hints_match_plan_table(real_vocab) -> None:
    h = real_vocab.cause_hints
    expect = {
        "BAD_RESPONSE_TIME_SQL": (("db",), "SQL 응답 지연"),
        "SQL_TOOMANY_FETCH": (("was_mw", "db"), "대량 조회"),
        "DB_CONN_UNCLOSED": (("was_mw",), "DB 연결 미반납"),
        "DB_CONNECTION_FAIL": (("was_mw", "db"), "DB 연결 실패"),
        "JDBC_CONNECTION_FAIL": (("was_mw", "db"), "DB 연결 실패"),
        "MAYBE_GC_TIME_DELAY": (("was_mw",), "GC·힙"),
        "OUTOFMEMORY": (("was_mw",), "GC·힙"),
        "JVM_HEAP_MEM_HIGH": (("was_mw",), "GC·힙"),
        "BAD_RESPONSE_TIME_EXTERNAL_CALL": (("was_mw",), "외부 연계 — 상대 시스템 확인 필요"),
        "HTTP_IO_EXCEPTION": (("was_mw",), "외부 연계 — 상대 시스템 확인 필요"),
        "SOCKET_EXCEPTION": (("was_mw",), "외부 연계 — 상대 시스템 확인 필요"),
    }
    got = {k: (v.areas, v.label) for k, v in h.apm_event_types.items()}
    assert got == expect
    assert set(h.known_unmapped) == {
        "BAD_RESPONSE_TIME_APPLICATION", "SERVICE_EXCEPTION", "AGENT_STOP", "METRICS_EVENT",
        "SERVICE_QUEUING", "PLC_REJECTED", "HIGH_RATE_REJECT", "HIGH_RATE_FAIL",
    }
    assert h.is_known_unmapped("METRICS EVENT")             # 폐쇄망 원값(공백)


def test_real_process_kinds(real_vocab) -> None:
    kinds = real_vocab.cause_hints.process_kinds
    was = {"tomcat", "jboss-wildfly", "weblogic", "websphere", "jeus", "nginx", "apache-httpd", "webtob"}
    db = {"db2", "mysql", "mariadb", "postgresql", "oracle", "tibero"}
    assert {k for k, v in kinds.items() if v.areas == ("was_mw",)} == was
    assert {k for k, v in kinds.items() if v.areas == ("db",)} == db
    assert set(kinds) == was | db                        # redis 등은 단서 없음


def test_process_kinds_exist_in_signature_file(real_vocab) -> None:
    from src.domain.middleware import load_signatures

    declared = {s.kind for s in load_signatures()}
    assert set(real_vocab.cause_hints.process_kinds) <= declared


def test_known_unmapped_and_mapped_disjoint(real_vocab) -> None:
    h = real_vocab.cause_hints
    assert not set(h.known_unmapped) & set(h.apm_event_types)


# ─── 2. 로더 검증 ─────────────────────────────────────────────────────────────


def test_parse_cause_hints_skips_bad_entries(caplog) -> None:
    caplog.set_level(logging.WARNING)
    raw = {
        "apm_event_types": {
            "GOOD": {"areas": ["alpha"], "label": "좋음"},
            "partial": {"areas": ["alpha", "nope"], "label": "부분"},   # 어휘 밖 코드만 빠진다
            "ALL_UNKNOWN": {"areas": ["nope"], "label": "x"},          # 남는 영역 없음 → 무시
            "NO_LABEL": {"areas": ["alpha"]},
            "NOT_MAP": "alpha",
            "good": {"areas": ["beta"], "label": "중복"},               # 정규화 중복 → 앞의 것
        },
        "known_unmapped": ["GOOD", " other type ", "", None, "OTHER_TYPE"],
        "process_kinds": {
            "alpha": {"label": "알파 프로세스", "kinds": ["Tomcat", "shared"]},
            "beta": {"label": "베타 프로세스", "kinds": ["shared"]},
            "nope": {"label": "x", "kinds": ["k"]},
            "gamma": "bad",
        },
    }
    h = parse_cause_hints(raw, ("alpha", "beta"))
    assert set(h.apm_event_types) == {"GOOD", "PARTIAL"}
    assert h.apm_event_types["PARTIAL"].areas == ("alpha",)
    assert h.apm_event_types["GOOD"].label == "좋음"
    assert h.known_unmapped == ("OTHER_TYPE",)          # 매핑된 GOOD은 빠지고 중복 제거
    assert h.process_kinds["tomcat"].areas == ("alpha",)
    assert h.process_kinds["shared"].areas == ("alpha", "beta")
    assert "k" not in h.process_kinds
    msgs = " ".join(r.message for r in caplog.records)
    for token in ("nope", "ALL_UNKNOWN", "NO_LABEL", "NOT_MAP", "known_unmapped"):
        assert token in msgs, token


@pytest.mark.parametrize("raw", [None, "x", ["a"], {"apm_event_types": ["x"], "known_unmapped": "x",
                                                     "process_kinds": ["x"]}])
def test_parse_cause_hints_bad_shapes_are_empty(raw) -> None:
    h = parse_cause_hints(raw, ("alpha",))
    assert not h.apm_event_types and not h.known_unmapped and not h.process_kinds


def test_vocabulary_without_section_keeps_w1_shape() -> None:
    v = parse_vocabulary({"areas": [{"code": "alpha", "label": "알파"}]})
    assert v.sections == {}
    assert not v.cause_hints.apm_event_types


def test_unmapped_apm_event_types_folds_known() -> None:
    v = parse_vocabulary({
        "areas": [{"code": "alpha", "label": "알파"}],
        "cause_hints": {
            "apm_event_types": {"MAPPED": {"areas": ["alpha"], "label": "m"}},
            "known_unmapped": ["METRICS_EVENT", "AGENT_STOP"],
        },
    })
    counts = {"MAPPED": 9, "METRICS EVENT": 3, "metrics_event": 1, "AGENT_STOP": 2,
              "NEW_ONE": 4, "NEW_TWO": 4, "A": 1, "B": 1, "C": 1, "D": 1}
    out = unmapped_apm_event_types(v, counts, empty_count=50, top_n=5)
    assert out["checked"] == 27 and out["empty_count"] == 50
    assert out["unmapped_distinct"] == 6 and out["unmapped_total"] == 12
    assert out["top"] == [{"event_type": "NEW_ONE", "count": 4}, {"event_type": "NEW_TWO", "count": 4},
                          {"event_type": "A", "count": 1}, {"event_type": "B", "count": 1},
                          {"event_type": "C", "count": 1}]
    assert out["known_unmapped"] == [{"event_type": "METRICS_EVENT", "count": 4},
                                     {"event_type": "AGENT_STOP", "count": 2}]
    assert out["known_unmapped_total"] == 6


# ─── 4·5. 실제 앱 조립 ────────────────────────────────────────────────────────

_VOCAB = parse_vocabulary({
    "areas": [
        {"code": "alpha", "label": "알파", "resource_types": ["kind.One", "표시 하나"]},
        {"code": "beta", "label": "베타", "resource_types": ["kind.Two"]},
    ],
    "cause_hints": {
        "apm_event_types": {"SLOW_SQL": {"areas": ["beta"], "label": "느린 SQL"}},
        "known_unmapped": ["METRICS_EVENT"],
        "process_kinds": {"beta": {"label": "엔진", "kinds": ["engine"]}},
    },
})
_BASE_USER_KEYS = ["user_id", "username", "role", "department", "allowed_db_ids",
                   "alarm_zones", "is_protected", "status", "last_login_at"]
_STATUS = "/api/v1/admin/duty-areas/status"


@pytest.fixture()
def vocab(monkeypatch):
    monkeypatch.setattr(duty_area_support, "load_duty_area_vocabulary", lambda: _VOCAB)


def _user(user_id: str, role: UserRole = UserRole.USER, **kw) -> User:
    return User(user_id=user_id, username=user_id, hashed_password="x", role=role,
                status=UserStatus.ACTIVE, **kw)


def _hdr(sub: str) -> dict[str, str]:
    tok = jwt.encode({"sub": sub, "type": "user", "exp": datetime.now(UTC) + timedelta(hours=1)},
                     _USER_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


def _app(tmp_path: Path, *, enabled: bool, focus: bool):
    from src.api.server import create_app
    from src.config import AdminConfig, AppConfig, AuthConfig, DutyAreaConfig, NoiseGateConfig

    store = tmp_path / "decisions.jsonl"
    recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    def apm(t: str) -> dict:
        return {"ts": recent, "resource_type": "kind.One",
                "stage_evidence": {"apm_policy": {"event_type_norm": t}}}

    rows = [apm("SLOW_SQL"), apm("METRICS EVENT"), apm("METRICS EVENT"), apm("<b>NEW</b>"),
            {"ts": recent, "resource_type": "unknown.Kind"}]
    store.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    cfg = AppConfig(
        auth=AuthConfig(enabled=True, jwt_secret=_USER_SECRET),
        admin=AdminConfig(jwt_secret=_ADMIN_SECRET),
        duty_area=DutyAreaConfig(enabled=enabled, alarm_focus_enabled=focus),
        noise_gate=NoiseGateConfig(decision_store_path=str(store), decision_store_enabled=True),
    )
    app = create_app(cfg)
    users = {"boss": _user("boss", role=UserRole.ADMIN), "plain": _user("plain", duty_areas=["beta"])}
    app.state.user_repo = SimpleNamespace(
        get_by_user_id=AsyncMock(side_effect=lambda uid: users.get(uid)),
        list_all=AsyncMock(side_effect=lambda: list(users.values())),
    )
    return app


def test_create_app_resolves_focus_once(tmp_path, vocab) -> None:
    assert _app(tmp_path, enabled=True, focus=True).state.duty_alarm_focus_enabled is True
    assert _app(tmp_path, enabled=True, focus=False).state.duty_alarm_focus_enabled is False
    assert _app(tmp_path, enabled=False, focus=True).state.duty_alarm_focus_enabled is False


def test_me_meta_only_when_focus_on(tmp_path, vocab) -> None:
    on = TestClient(_app(tmp_path, enabled=True, focus=True)).get("/api/v1/auth/me", headers=_hdr("plain"))
    body = on.json()
    assert list(body) == _BASE_USER_KEYS + ["duty_areas", "duty_area_options", "duty_alarm_focus"]
    meta = body["duty_alarm_focus"]
    assert set(meta) == {"areas", "cause_hints"}
    assert meta["areas"] == [{"code": "alpha", "resource_types": ["kind.One", "표시 하나"]},
                             {"code": "beta", "resource_types": ["kind.Two"]}]
    assert meta["cause_hints"] == {
        "apm_event_types": {"SLOW_SQL": {"areas": ["beta"], "label": "느린 SQL"}},
        "process_kinds": {"engine": {"areas": ["beta"], "label": "엔진"}},
    }
    # 담당 지정(W1)만 켜면 W1 응답 키 그대로
    w1 = TestClient(_app(tmp_path, enabled=True, focus=False)).get("/api/v1/auth/me", headers=_hdr("plain"))
    assert list(w1.json()) == _BASE_USER_KEYS + ["duty_areas", "duty_area_options"]
    # 둘 다 끄면(또는 담당 지정 off) 현행 그대로 — 강조 플래그만 켜도 키 없음
    off = TestClient(_app(tmp_path, enabled=False, focus=True)).get("/api/v1/auth/me", headers=_hdr("plain"))
    assert list(off.json()) == _BASE_USER_KEYS


def test_public_duty_areas_still_code_label_only(tmp_path, vocab) -> None:
    resp = TestClient(_app(tmp_path, enabled=True, focus=True)).get("/api/v1/auth/duty-areas")
    assert all(set(o) == {"code", "label"} for o in resp.json()["options"])
    assert "cause_hints" not in resp.text and "resource_types" not in resp.text


def test_status_apm_section_on(tmp_path, vocab) -> None:
    body = TestClient(_app(tmp_path, enabled=True, focus=True)).get(_STATUS, headers=_hdr("boss")).json()
    keys = [s["key"] for s in body["sections"]]
    assert keys == ["resource_type", "apm_event_type"]
    apm = body["sections"][1]
    assert apm["top"] == [{"event_type": "<B>NEW</B>", "count": 1}]       # 원문(정규화) — 화면은 textContent
    assert apm["unmapped_distinct"] == 1
    assert apm["known_unmapped"] == [{"event_type": "METRICS_EVENT", "count": 2}]
    assert apm["empty_count"] == 1                                         # 폴스타 알람(유형 없음)
    # 배지 = resource_type 미매핑(unknown.Kind) 1 + 유형 미매핑 1 — known_unmapped 제외
    assert body["badge_count"] == 2


def test_status_off_and_mapping_false_keep_w1(tmp_path, vocab) -> None:
    w1 = TestClient(_app(tmp_path, enabled=True, focus=False)).get(_STATUS, headers=_hdr("boss")).json()
    assert [s["key"] for s in w1["sections"]] == ["resource_type"]
    assert w1["badge_count"] == 1
    on = TestClient(_app(tmp_path, enabled=True, focus=True))
    lite = on.get(_STATUS + "?mapping=false", headers=_hdr("boss")).json()
    assert lite["sections"] is None and lite["badge_count"] is None


# ─── 6. 설정 ──────────────────────────────────────────────────────────────────


def test_settings_catalog_and_help() -> None:
    from src.api.settings_catalog import field_index

    item = field_index()["DUTY_ALARM_FOCUS_ENABLED"]
    assert item.group_key == "duty_area"
    help_text = (_ROOT / "config/settings_help/duty_area.yaml").read_text(encoding="utf-8")
    assert "\n  DUTY_ALARM_FOCUS_ENABLED:\n" in help_text
    example = (_ROOT / ".env.example").read_text(encoding="utf-8")
    assert len(re.findall(r"^#?\s*DUTY_ALARM_FOCUS_ENABLED=false$", example, re.M)) == 1


def test_authz_modules_do_not_read_focus() -> None:
    """인가·가시성 판정은 담당 강조를 읽지 않는다 — 존·티어 가시성(`event_visible_to`) 포함."""
    for rel in ("src/api/dependencies.py", "src/api/routes/alarm.py"):
        text = (_ROOT / rel).read_text(encoding="utf-8")
        assert "alarm_focus" not in text and "duty_" not in text, rel
