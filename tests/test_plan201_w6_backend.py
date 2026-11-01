"""plans/201 W6 — 첫 화면 「지난 24시간 · 내 담당」 요약: 결정 기록 순회 · 집계 · 라우트 · `/auth/me` · 설정.

  1. `DecisionStore.iter_recent_decisions` — 비-decision `type`·손상 줄·창 밖 제외 · 파일 부재/비활성 = 빈 순회 ·
     파일 끝에서 블록 단위 역방향(최신부터) · 창 하한 − 여유보다 오래된 기록에서 멈춤 · 건너뛰는 줄에서도 중단 확인.
  2. 담당 겹침(`duty_overlap`) — 발생·단서·둘 다·없음 · `resource_type` 없는 레코드는 발생 없음.
  3. 라우트 — 존 밖 레코드 0건(부분 존·전 존 비관리자·관리자·개발 모드) · 비관리자 `suppress` 키 없음 + 미반영 ·
     확인 필요 상위 3(최신) · 반복 상위 1 · 사유(no_duty·no_alarm_zone) · 시간 초과/오류 구조화 사유 ·
     식별 필드는 SSE가 이미 싣는 것만(hostname·condition_log·analysis 없음).
  4. 플래그 — off면 라우트 404 · `/auth/me` 키 집합 동일 · 실효 AND · env 이름 하나 · 카탈로그·도움말·.env.example.
  5. 인가 비참조 — 존·SUPPRESS 판정 함수에 duty 참조 0.

레코드는 전부 `DecisionStore.record(...)`로 실제로 쓴 줄을 읽는다(손 dict 금지).
"""

from __future__ import annotations

import inspect
import json
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi.testclient import TestClient

from noise_gate.domain.notification_policy import NotificationDecision
from noise_gate.infrastructure.decision_store import DecisionStore
from src.api import duty_area_support
from src.api.duty_daily_summary import SummaryStopped, record_overlap, summarize_duty_records
from src.domain.duty_areas import DutyOverlap, duty_overlap, parse_vocabulary
from src.domain.user import User, UserRole, UserStatus

_ROOT = Path(__file__).resolve().parents[1]
_USER_SECRET = "u" * 40
_ADMIN_SECRET = "a" * 40
_URL = "/api/v1/auth/duty-areas/daily-summary"

_VOCAB = parse_vocabulary({
    "areas": [
        {"code": "alpha", "label": "알파", "resource_types": ["server", "서버"]},
        {"code": "beta", "label": "베타", "resource_types": ["database"]},
        {"code": "gamma", "label": "감마", "resource_types": ["network"]},
    ],
    "cause_hints": {
        "apm_event_types": {
            "BAD_RESPONSE_TIME_SQL": {"areas": ["beta"], "label": "SQL 응답 지연"},
            "SERVICE_QUEUING": {"areas": ["alpha", "gamma"], "label": "요청 대기"},
        },
    },
})

_B0 = "polestar_b0"        # 은행존
_GP = "polestar_cm_gp"     # 공동존


@pytest.fixture()
def vocab(monkeypatch):
    monkeypatch.setattr(duty_area_support, "load_duty_area_vocabulary", lambda: _VOCAB)


def _write(store: DecisionStore, *, tier: str, db_id: str, server: str, name: str = "알람",
           rt: str = "", apm: str | None = None, minutes_ago: float = 10.0, fp: str = "",
           hostname: str = "hidden-host", condition_log: str = "VAL=97.5") -> None:
    evidence = {"apm_policy": {"event_type_norm": apm, "policy_row": {"x": 1}}} if apm else None
    store.record(
        NotificationDecision(tier=tier, reason="r", priority=1, signals={}, fingerprint=fp or f"fp-{server}-{name}"),
        alarm_id=f"id-{server}-{minutes_ago}",
        alarm_name=name,
        server_name=server,
        ts=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        stage_evidence=evidence,
        db_id=db_id,
        condition_log=condition_log,
        analysis={"summary": "분석 본문", "failed": False},
        hostname=hostname,
        resource_type=rt,
    )


# ─── 1. 결정 기록 순회 ─────────────────────────────────────────────────────


def test_iter_recent_decisions_rules(tmp_path) -> None:
    path = tmp_path / "d.jsonl"
    store = DecisionStore(str(path))
    assert list(store.iter_recent_decisions(3600)) == []                       # 파일 부재
    _write(store, tier="ticket", db_id=_B0, server="old", rt="server", minutes_ago=25 * 60)   # 기록은 시각 순
    _write(store, tier="page", db_id=_B0, server="s1", rt="server")
    store.record_resolution(fingerprint="fp-s1-알람", duration_seconds=12.0)       # type=resolution 보조 레코드
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{손상된 줄\n")
        fh.write(json.dumps({"type": "investigation", "ts": datetime.now(UTC).isoformat()}) + "\n")
        fh.write("\n")
    got = list(store.iter_recent_decisions(24 * 3600))
    assert [r["server_name"] for r in got] == ["s1"]
    assert got[0]["resource_type"] == "server" and got[0]["db_id"] == _B0       # 실 레코드 키 = SSE 키
    assert got[0]["tier"] == "page"
    assert inspect.isgeneratorfunction(DecisionStore.iter_recent_decisions)    # 쌓지 않는다
    assert list(DecisionStore(str(path), enabled=False).iter_recent_decisions(3600)) == []


def test_iter_recent_decisions_read_failure_warns_once(tmp_path, monkeypatch, caplog) -> None:
    path = tmp_path / "d.jsonl"
    store = DecisionStore(str(path))
    _write(store, tier="page", db_id=_B0, server="s1", rt="server")

    def _boom(*a, **k):
        raise OSError("disk")

    monkeypatch.setattr(Path, "open", _boom)
    with caplog.at_level(logging.WARNING, logger="noise_gate.infrastructure.decision_store"):
        assert list(store.iter_recent_decisions(3600)) == []
    assert caplog.text.count("결정 기록 순회 읽기 실패") == 1


def test_iter_recent_decisions_reads_backward_across_blocks_and_stops_past_window(tmp_path, monkeypatch) -> None:
    from noise_gate.infrastructure import decision_store as ds

    monkeypatch.setattr(ds, "_REVERSE_SCAN_BLOCK_BYTES", 97)                   # 줄이 블록 경계에 걸리게
    path = tmp_path / "d.jsonl"
    store = DecisionStore(str(path))
    for i in range(30):                                                       # 창 밖 오래된 기록(48h~)
        _write(store, tier="dashboard", db_id=_B0, server=f"old{i}", rt="server", minutes_ago=48 * 60 - i)
    _write(store, tier="page", db_id=_B0, server="edge", rt="server", minutes_ago=24 * 60 + 5)   # 여유 안 창 밖
    _write(store, tier="page", db_id=_B0, server="late", rt="server", minutes_ago=24 * 60 - 1)   # 창 안(늦게 append)
    for i in range(5):
        _write(store, tier="ticket", db_id=_B0, server=f"서버{i}", rt="server", minutes_ago=50 - i)
    calls = {"n": 0}
    orig = DecisionStore._within_window

    def _count(rec, cutoff):  # noqa: ANN001
        calls["n"] += 1
        return orig(rec, cutoff)

    monkeypatch.setattr(DecisionStore, "_within_window", staticmethod(_count))
    got = [r["server_name"] for r in store.iter_recent_decisions(24 * 3600)]
    assert got == ["서버4", "서버3", "서버2", "서버1", "서버0", "late"]            # 최신부터 · 한글 줄 경계 안전
    assert calls["n"] == 8                                                     # 창 밖 첫 오래된 기록에서 멈춘다


def test_iter_recent_decisions_slack_keeps_out_of_order_append(tmp_path) -> None:
    path = tmp_path / "d.jsonl"
    store = DecisionStore(str(path))
    _write(store, tier="page", db_id=_B0, server="in1", rt="server", minutes_ago=60)
    _write(store, tier="page", db_id=_B0, server="skew", rt="server", minutes_ago=24 * 60 + 5)  # 하한 −5분
    _write(store, tier="page", db_id=_B0, server="in2", rt="server", minutes_ago=30)
    assert [r["server_name"] for r in store.iter_recent_decisions(24 * 3600)] == ["in2", "in1"]


def test_iter_recent_decisions_stop_checked_on_skipped_lines(tmp_path) -> None:
    from noise_gate.infrastructure import decision_store as ds

    path = tmp_path / "d.jsonl"
    store = DecisionStore(str(path))
    _write(store, tier="page", db_id=_B0, server="s1", rt="server")
    now = datetime.now(UTC).isoformat()
    with path.open("a", encoding="utf-8") as fh:                              # 창 안 보조 레코드 다수(건너뜀)
        for _ in range(ds._SCAN_STOP_CHECK_EVERY * 4):
            fh.write(json.dumps({"type": "resolution", "ts": now}) + "\n")
    polls = {"n": 0}

    def _stop() -> bool:
        polls["n"] += 1
        return True

    assert list(store.iter_recent_decisions(3600, should_stop=_stop)) == []  # 내준 것 없이 멈춘다
    assert polls["n"] == 1
    store.scan_should_stop = _stop                                           # 인스턴스 기본 신호도 같다
    assert list(store.iter_recent_decisions(3600)) == [] and polls["n"] == 2
    store.scan_should_stop = None
    assert [r["server_name"] for r in store.iter_recent_decisions(3600)] == ["s1"]


# ─── 2. 담당 겹침 ─────────────────────────────────────────────────────────


def test_duty_overlap_cases() -> None:
    occ = duty_overlap(_VOCAB, ["alpha"], resource_type="SERVER")
    assert occ == DutyOverlap(occurrence=True) and occ.mine
    hint = duty_overlap(_VOCAB, ["beta"], resource_type="server", event_type="ERROR_BAD RESPONSE_TIME_SQL")
    assert hint == DutyOverlap(occurrence=False, hint=True, hint_label="SQL 응답 지연")
    both = duty_overlap(_VOCAB, ["alpha"], resource_type="서버", event_type="SERVICE_QUEUING")
    assert both == DutyOverlap(occurrence=True, hint=True, hint_label="요청 대기")
    none = duty_overlap(_VOCAB, ["gamma"], resource_type="server", event_type="BAD_RESPONSE_TIME_SQL")
    assert none == DutyOverlap() and not none.mine
    assert duty_overlap(_VOCAB, ["alpha"], resource_type=None, event_type=None) == DutyOverlap()
    assert duty_overlap(_VOCAB, ["alpha"], resource_type=3, event_type=["x"]) == DutyOverlap()


def test_record_overlap_reads_real_record_shape(tmp_path) -> None:
    store = DecisionStore(str(tmp_path / "d.jsonl"))
    _write(store, tier="page", db_id=_B0, server="s1", apm="BAD_RESPONSE_TIME_SQL")      # resource_type 없음
    _write(store, tier="page", db_id=_B0, server="s2", rt="database")
    by_server = {r["server_name"]: r for r in store.iter_recent_decisions(3600)}
    first, second = by_server["s1"], by_server["s2"]
    assert "resource_type" not in first                                           # 수동 조사 off 환경 꼴
    assert record_overlap(first, _VOCAB, ["beta"]) == DutyOverlap(hint=True, hint_label="SQL 응답 지연")
    assert record_overlap(first, _VOCAB, ["alpha"]) == DutyOverlap()               # 발생 영역 없음
    assert record_overlap(second, _VOCAB, ["beta"]) == DutyOverlap(occurrence=True)


def test_summarize_top3_newest_and_top_repeat() -> None:
    recs = []
    for i, tier in enumerate(["page", "ticket", "dashboard", "page", "ticket", "page"]):
        recs.append({"ts": (datetime(2026, 10, 30, 8, 0, tzinfo=UTC) + timedelta(minutes=i)).isoformat(),
                     "tier": tier, "resource_type": "server", "server_name": f"s{i}", "alarm_name": f"a{i}",
                     "fingerprint": "rep" if i in (1, 2, 4) else f"f{i}"})
    recs.append({"ts": "", "tier": "page", "resource_type": "server", "server_name": "nots",
                 "alarm_name": "nots", "fingerprint": "f9"})
    out = summarize_duty_records(recs, vocab=_VOCAB, user_areas=["alpha"], visible=lambda r: True,
                                 include_suppress=False)
    assert out["tiers"] == {"page": 4, "ticket": 2, "dashboard": 1}
    assert [it["server_name"] for it in out["needs_attention"]] == ["s5", "s4", "s3"]   # 최신 3 · 시각 내림차순
    assert out["top_repeat"] == {"alarm_name": "a4", "count": 3}                      # 마지막 이름
    rev = summarize_duty_records(list(reversed(recs)), vocab=_VOCAB, user_areas=["alpha"],
                                 visible=lambda r: True, include_suppress=False, newest_first=True)
    assert rev == out                                                                 # 입력 방향과 무관
    flag = {"stop": False}

    def _stopping():
        for rec in recs:
            yield rec
        flag["stop"] = True                                                           # 순회가 조용히 끝난 경우

    with pytest.raises(SummaryStopped):
        summarize_duty_records(_stopping(), vocab=_VOCAB, user_areas=["alpha"], visible=lambda r: True,
                               include_suppress=False, should_stop=lambda: flag["stop"])
    single = summarize_duty_records(recs[:1], vocab=_VOCAB, user_areas=["alpha"], visible=lambda r: True,
                                    include_suppress=True)
    assert single["top_repeat"] is None and single["tiers"]["suppress"] == 0


# ─── 3. 라우트 ────────────────────────────────────────────────────────────


def _user(user_id: str, *, role: UserRole = UserRole.USER, **kw) -> User:
    return User(user_id=user_id, username=user_id, hashed_password="x", role=role,
                status=UserStatus.ACTIVE, **kw)


_USERS = {
    "alpha_b0": _user("alpha_b0", duty_areas=["alpha"], alarm_zones=["bankjon"]),
    "beta_all": _user("beta_all", duty_areas=["beta"], alarm_zones=["bankjon", "gongjon"]),
    "admin": _user("admin", role=UserRole.ADMIN, duty_areas=["alpha"]),
    "none": _user("none", alarm_zones=["bankjon"]),
    "nozone": _user("nozone", duty_areas=["alpha"]),
}


def _hdr(sub: str) -> dict[str, str]:
    tok = jwt.encode({"sub": sub, "type": "user", "exp": datetime.now(UTC) + timedelta(hours=1)},
                     _USER_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


def _app(path: Path, *, duty: bool = True, daily: bool = True, auth: bool = True):
    from src.api.server import create_app
    from src.config import AdminConfig, AppConfig, AuthConfig, DutyAreaConfig

    cfg = AppConfig(
        auth=AuthConfig(enabled=auth, jwt_secret=_USER_SECRET),
        admin=AdminConfig(jwt_secret=_ADMIN_SECRET),
        duty_area=DutyAreaConfig(_env_file=None, enabled=duty, daily_summary_enabled=daily),
    )
    cfg.noise_gate.decision_store_path = str(path)
    cfg.noise_gate.decision_store_enabled = True
    app = create_app(cfg)
    app.state.user_repo = SimpleNamespace(get_by_user_id=AsyncMock(side_effect=lambda uid: _USERS.get(uid)))
    return app


def _seed(path: Path) -> DecisionStore:
    """은행존·공동존에 같은 꼴의 레코드를 깐다 — 서버 이름 접두로 존을 구별한다.

    운영 기록처럼 **시각 순(오래된 것부터)으로** 쓴다 — 순회는 파일 끝에서 거꾸로 읽다 창 밖에서 멈춘다.
    """
    store = DecisionStore(str(path))
    rows: list[dict] = []
    for zone, db_id in (("bk", _B0), ("gj", _GP)):
        rows.append(dict(tier="page", db_id=db_id, server=f"{zone}-p1", rt="server", minutes_ago=50))
        rows.append(dict(tier="ticket", db_id=db_id, server=f"{zone}-t1", rt="server", minutes_ago=40))
        rows.append(dict(tier="dashboard", db_id=db_id, server=f"{zone}-d1", rt="server", minutes_ago=30))
        rows.append(dict(tier="ticket", db_id=db_id, server=f"{zone}-sql", rt="server",
                         apm="BAD_RESPONSE_TIME_SQL", minutes_ago=20))
        rows.append(dict(tier="dashboard", db_id=db_id, server=f"{zone}-db", rt="database", minutes_ago=15))
        # 억제 5건 — 같은 지문(세면 반복 상위가 된다)
        for i in range(5):
            rows.append(dict(tier="suppress", db_id=db_id, server=f"{zone}-sup", name="억제 반복", rt="server",
                             fp=f"sup-{zone}", minutes_ago=5 + i * 0.1))
        rows.append(dict(tier="page", db_id=db_id, server=f"{zone}-old", rt="server", minutes_ago=25 * 60))
    # 존 매핑 없는 db_id — 전 존(관리자·개발 모드)만 본다
    rows.append(dict(tier="page", db_id="", server="nz-p1", rt="server", minutes_ago=1))
    for row in sorted(rows, key=lambda r: -r["minutes_ago"]):
        _write(store, **row)
    return store


def _get(app, sub: str | None):
    headers = _hdr(sub) if sub else {}
    return TestClient(app).get(_URL, headers=headers)


def test_partial_zone_user_sees_only_own_zone(tmp_path, vocab) -> None:
    path = tmp_path / "d.jsonl"
    _seed(path)
    resp = _get(_app(path), "alpha_b0")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["eligible"] is True and body["window_hours"] == 24
    assert body["areas"] == [{"code": "alpha", "label": "알파"}]
    assert body["tiers"] == {"page": 1, "ticket": 2, "dashboard": 1}            # 억제 키 없음 · 창 밖 제외
    assert [it["server_name"] for it in body["needs_attention"]] == ["bk-sql", "bk-t1", "bk-p1"]
    assert body["top_repeat"] is None                                           # 억제 반복은 세지 않는다
    text = resp.text
    assert "gj-" not in text and "nz-" not in text                              # 존 밖 0건
    for leaked in ("hidden-host", "VAL=97.5", "분석 본문", "policy_row", "condition_log", "analysis", "hostname"):
        assert leaked not in text, leaked
    assert set(body) == {"eligible", "window_hours", "areas", "generated_at", "tiers", "needs_attention",
                         "top_repeat"}
    assert set(body["needs_attention"][0]) == {"ts", "tier", "server_name", "alarm_name", "resource_type",
                                               "occurrence", "hint", "hint_label"}


def test_all_zone_non_admin_no_suppress(tmp_path, vocab) -> None:
    path = tmp_path / "d.jsonl"
    _seed(path)
    body = _get(_app(path), "beta_all").json()
    # beta = 발생(database) 또는 단서(SQL) — 두 존 모두(전 존 비관리자 = deliver_all) · 존 없는 db_id도 전 존이면 통과
    assert body["tiers"] == {"page": 0, "ticket": 2, "dashboard": 2}
    assert "suppress" not in body["tiers"]
    sql = [it for it in body["needs_attention"] if it["hint"]]
    assert {it["server_name"] for it in sql} == {"bk-sql", "gj-sql"}
    assert all(it["hint_label"] == "SQL 응답 지연" and it["occurrence"] is False for it in sql)
    assert "억제 반복" not in json.dumps(body, ensure_ascii=False) and "-sup" not in json.dumps(body)


def test_admin_gets_suppress_and_all_zones(tmp_path, vocab) -> None:
    path = tmp_path / "d.jsonl"
    _seed(path)
    body = _get(_app(path), "admin").json()
    assert body["tiers"] == {"page": 3, "ticket": 4, "dashboard": 2, "suppress": 10}
    assert body["needs_attention"][0]["server_name"] == "nz-p1"
    assert body["top_repeat"]["alarm_name"] == "억제 반복" and body["top_repeat"]["count"] == 5


def test_dev_mode_matches_sse_rule(tmp_path, vocab, monkeypatch) -> None:
    from src.api.routes import duty_areas

    path = tmp_path / "d.jsonl"
    _seed(path)
    app = _app(path, auth=False)
    anon = _get(app, None).json()                                              # anonymous = 담당 미지정
    assert anon["eligible"] is False and anon["reason"] == "no_duty"
    monkeypatch.setattr(duty_areas, "_stored_duty_areas", AsyncMock(return_value=["alpha"]))
    body = _get(app, None).json()
    assert body["eligible"] is True and body["tiers"]["suppress"] == 10      # 개발 모드 = SSE와 같이 억제 수신


def test_reasons_skip_aggregation(tmp_path, vocab, monkeypatch) -> None:
    path = tmp_path / "d.jsonl"
    _seed(path)
    app = _app(path)
    calls = []
    monkeypatch.setattr(DecisionStore, "iter_recent_decisions", lambda self, w: calls.append(w) or iter(()))
    none = _get(app, "none").json()
    assert none["eligible"] is False and none["reason"] == "no_duty" and "tiers" not in none
    nozone = _get(app, "nozone").json()
    assert nozone["eligible"] is False and nozone["reason"] == "no_alarm_zone" and "tiers" not in nozone
    assert calls == []


def test_timeout_and_error_are_structured(tmp_path, vocab, monkeypatch, caplog) -> None:
    from src.api.routes import duty_areas

    path = tmp_path / "d.jsonl"
    _seed(path)
    app = _app(path)
    seen: list[int] = []

    def _slow(self, window):
        for rec in DecisionStore.__dict__["_orig_iter"](self, window):
            seen.append(1)
            time.sleep(0.05)
            yield rec

    monkeypatch.setattr(DecisionStore, "_orig_iter", DecisionStore.iter_recent_decisions, raising=False)
    monkeypatch.setattr(DecisionStore, "iter_recent_decisions", _slow)
    monkeypatch.setattr(duty_areas, "DAILY_SUMMARY_TIMEOUT_SECONDS", 0.1)
    with caplog.at_level(logging.WARNING, logger="src.api.routes.duty_areas"):
        body = _get(app, "alpha_b0").json()
    assert body["eligible"] is True and body["unavailable"] == "timeout" and "tiers" not in body
    time.sleep(0.3)
    stopped_at = len(seen)
    time.sleep(0.3)
    assert len(seen) == stopped_at                                             # 시간 초과 뒤 스레드가 멈췄다

    def _boom(self, window):
        raise RuntimeError("bk-p1 10.0.0.1")
        yield  # pragma: no cover

    monkeypatch.setattr(DecisionStore, "iter_recent_decisions", _boom)
    with caplog.at_level(logging.WARNING, logger="src.api.routes.duty_areas"):
        body = _get(app, "alpha_b0").json()
    assert body["eligible"] is True and body["unavailable"] == "error"
    assert "시간 초과" in caplog.text and "RuntimeError" in caplog.text
    assert "bk-p1" not in caplog.text and "10.0.0.1" not in caplog.text        # 로그에는 사유만


# ─── 4. 플래그 · /auth/me · 설정 ──────────────────────────────────────────

_BASE_USER_KEYS = ["user_id", "username", "role", "department", "allowed_db_ids",
                   "alarm_zones", "is_protected", "status", "last_login_at"]


def _me(app, sub: str) -> dict:
    resp = TestClient(app).get("/api/v1/auth/me", headers=_hdr(sub))
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_route_404_when_off(tmp_path, vocab) -> None:
    path = tmp_path / "d.jsonl"
    assert _get(_app(path, daily=False), "alpha_b0").status_code == 404
    assert _get(_app(path, duty=False, daily=True), "alpha_b0").status_code == 404
    assert _get(_app(path), "alpha_b0").status_code == 200
    assert TestClient(_app(path)).get(_URL).status_code == 401                 # require_user


def test_me_key_only_when_effective(tmp_path, vocab) -> None:
    path = tmp_path / "d.jsonl"
    assert list(_me(_app(path, duty=False, daily=True), "alpha_b0")) == _BASE_USER_KEYS
    assert list(_me(_app(path, daily=False), "alpha_b0")) == _BASE_USER_KEYS + ["duty_areas", "duty_area_options"]
    on = _app(path)
    body = _me(on, "alpha_b0")
    assert list(body) == _BASE_USER_KEYS + ["duty_areas", "duty_area_options", "duty_daily_summary"]
    assert body["duty_daily_summary"] is True
    assert _me(on, "admin")["duty_daily_summary"] is True
    assert _me(on, "none")["duty_daily_summary"] is False                      # 담당 미지정
    assert _me(on, "nozone")["duty_daily_summary"] is False                    # 알람 존 없음


def test_login_schema_omits_key() -> None:
    from src.api.schemas import UserInfoResponse

    assert "duty_daily_summary" not in UserInfoResponse(user_id="u", username="u", role="user").model_dump()
    assert UserInfoResponse(user_id="u", username="u", role="user",
                            duty_daily_summary=False).model_dump()["duty_daily_summary"] is False


def test_create_app_resolves_flag_once(tmp_path) -> None:
    path = tmp_path / "d.jsonl"
    assert _app(path).state.duty_daily_summary_enabled is True
    assert _app(path, daily=False).state.duty_daily_summary_enabled is False
    assert _app(path, duty=False).state.duty_daily_summary_enabled is False


def test_env_name_one_and_effective_and(monkeypatch) -> None:
    from src.config import DutyAreaConfig

    for name in ("DUTY_DAILY_SUMMARY_ENABLED", "DAILY_SUMMARY_ENABLED", "DUTY_AREA_DAILY_SUMMARY_ENABLED",
                 "DUTY_AREA_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    assert DutyAreaConfig(_env_file=None).daily_summary_enabled is False
    monkeypatch.setenv("DAILY_SUMMARY_ENABLED", "true")
    monkeypatch.setenv("DUTY_AREA_DAILY_SUMMARY_ENABLED", "true")
    assert DutyAreaConfig(_env_file=None).daily_summary_enabled is False
    monkeypatch.setenv("DUTY_DAILY_SUMMARY_ENABLED", "true")
    cfg = DutyAreaConfig(_env_file=None)
    assert cfg.daily_summary_enabled is True and cfg.daily_summary_effective is False
    monkeypatch.setenv("DUTY_AREA_ENABLED", "true")
    assert DutyAreaConfig(_env_file=None).daily_summary_effective is True
    assert DutyAreaConfig(_env_file=None, enabled=True, daily_summary_enabled=False).daily_summary_effective is False


def test_settings_catalog_help_and_env_example() -> None:
    import yaml

    from src.api.settings_catalog import field_index

    assert field_index()["DUTY_DAILY_SUMMARY_ENABLED"].group_key == "duty_area"
    duty_help = yaml.safe_load((_ROOT / "config/settings_help/duty_area.yaml").read_text(encoding="utf-8"))
    assert "DUTY_DAILY_SUMMARY_ENABLED" in duty_help["settings"]
    example = (_ROOT / ".env.example").read_text(encoding="utf-8")
    assert len(re.findall(r"^#?\s*DUTY_DAILY_SUMMARY_ENABLED=false$", example, re.M)) == 1


# ─── 5. 인가 비참조 ────────────────────────────────────────────────────────


def test_visibility_and_authz_functions_do_not_read_duty() -> None:
    from src.api import dependencies
    from src.api.routes import alarm

    for fn in (alarm.event_visible_to, dependencies.alarm_zones_for_user, dependencies.is_admin,
               dependencies.require_user):
        assert "duty" not in inspect.getsource(fn), fn.__name__
    assert "duty" not in (_ROOT / "src/api/dependencies.py").read_text(encoding="utf-8")


def test_route_uses_shared_visibility_function_not_a_copy() -> None:
    src = (_ROOT / "src/api/routes/duty_areas.py").read_text(encoding="utf-8")
    assert "from src.api.routes.alarm import event_visible_to" in src
    assert "db_id_to_zone" not in src and "db_id_to_zone" not in (_ROOT / "src/api/duty_daily_summary.py").read_text(
        encoding="utf-8")
