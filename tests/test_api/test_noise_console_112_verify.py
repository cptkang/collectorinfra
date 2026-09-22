"""plans/112 독립 검증 — API 계약 경계 보강 (verifier 추가분 · 구현 테스트와 별개).

구현 쪽 테스트(`test_noise_dashboard_api.py`)가 덮지 않은 계약 경계를 운영자·사용자 두 경로에서
같은 케이스로 찌른다. 기존 테스트 헬퍼를 재사용하며 실 LLM·서버 기동은 없다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from src.api.routes import noise_dashboard as noise_routes
from tests.test_api.test_noise_dashboard_api import (
    _decision_record,
    _make_client,
    _make_config,
    _make_user_client,
    _seed_decisions,
)

MODES = ["admin", "user"]
SECRET = "sk-abcdefghijklmnopqrstuvwxyz012345"


def _client(mode: str, config):
    if mode == "admin":
        return _make_client(config), "/api/v1/admin/noise"
    return _make_user_client(config), "/api/v1/noise"


def _records() -> list[dict]:
    now = datetime.now(UTC)

    def at(minutes: int) -> str:
        return (now - timedelta(minutes=120 - minutes)).isoformat()

    return [
        _decision_record(alarm_id="p1", tier="page", stage="severity3", ts=at(1)),
        _decision_record(alarm_id="p2", tier="page", stage="matrix", ts=at(2), server_name="DB-01"),
        _decision_record(alarm_id="t1", tier="ticket", stage="matrix", ts=at(3)),
        _decision_record(alarm_id="d1", tier="dashboard", stage="dependency", ts=at(4)),
        _decision_record(alarm_id="s1", tier="suppress", stage="flapping", ts=at(5)),
        _decision_record(alarm_id="s2", tier="suppress", stage="storm", ts=at(6),
                         server_name="DB-01"),
        _decision_record(alarm_id="s3", tier="suppress", stage="matrix", ts=at(7)),
        _decision_record(alarm_id="d2", tier="dashboard", stage="matrix", ts=at(8),
                         server_name="DB-01"),
    ]


# ─── tier 파싱 경계 ──────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("value,expected", [
    ("page,,ticket", 3),   # 빈 토큰은 무시(관대)
    (",", 8),              # 토큰이 전부 비면 필터 없음
    (" ", 8),
    ("suppress", 3),
])
def test_tier_token_edges(tmp_path, mode, value, expected):
    _seed_decisions(tmp_path, _records())
    client, base = _client(mode, _make_config(tmp_path))
    resp = client.get(f"{base}/decisions", params={"tier": value})
    assert resp.status_code == 200
    assert resp.json()["total"] == expected


# ─── facets 항등식 — 다른 필터와 조합 ────────────────────────────────────


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("params", [
    {},
    {"q": "DB-01"},
    {"tier": "page,dashboard"},
    {"stage": "matrix"},
    {"stage": "matrix", "tier": "suppress,dashboard"},
    {"stage": "matrix", "q": "DB-01"},
    {"tier": "suppress", "q": "WEB"},
    {"alarm_name": "CPU 사용률 임계 초과", "stage": "matrix"},
])
def test_facet_identities_under_filter_combinations(tmp_path, mode, params):
    _seed_decisions(tmp_path, _records())
    client, base = _client(mode, _make_config(tmp_path))
    body = client.get(f"{base}/decisions", params=params).json()
    tiers, stages, total = body["facets"]["tiers"], body["facets"]["stages"], body["total"]
    assert set(tiers) == {"page", "ticket", "dashboard", "suppress"}
    assert all(v > 0 for v in stages.values())
    tier_filter = [t for t in params.get("tier", "").split(",") if t]
    if tier_filter:
        assert sum(tiers[t] for t in tier_filter) == total
    else:
        assert sum(tiers.values()) == total
    if "stage" in params:
        assert stages.get(params["stage"], 0) == total
    else:
        assert sum(stages.values()) == total
    # tiers facet은 tier 필터만 뺀 모집단 — tier 필터 없이 같은 조건으로 조회한 total과 같다.
    no_tier = {k: v for k, v in params.items() if k != "tier"}
    assert sum(tiers.values()) == client.get(f"{base}/decisions", params=no_tier).json()["total"]
    no_stage = {k: v for k, v in params.items() if k != "stage"}
    assert sum(stages.values()) == client.get(f"{base}/decisions", params=no_stage).json()["total"]


@pytest.mark.parametrize("mode", MODES)
def test_zero_count_stage_filter_has_no_facet_key(tmp_path, mode):
    # 계약: stages는 n>0 키만 — 0건 단계를 거르면 facets.stages에 그 키가 없다
    # (화면은 0으로 봐야 한다).
    _seed_decisions(tmp_path, _records())
    client, base = _client(mode, _make_config(tmp_path))
    body = client.get(f"{base}/decisions", params={"stage": "silence"}).json()
    assert body["total"] == 0
    assert "silence" not in body["facets"]["stages"]


# ─── since / until 경계 ──────────────────────────────────────────────────


@pytest.mark.parametrize("mode", MODES)
def test_since_after_until_is_empty_not_error(tmp_path, mode):
    records = _records()
    _seed_decisions(tmp_path, records)
    client, base = _client(mode, _make_config(tmp_path))
    resp = client.get(f"{base}/decisions",
                      params={"since": records[5]["ts"], "until": records[1]["ts"]})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.parametrize("mode", MODES)
def test_since_accepts_z_suffix_and_foreign_offset(tmp_path, mode):
    records = _records()
    _seed_decisions(tmp_path, records)
    client, base = _client(mode, _make_config(tmp_path))
    pivot = datetime.fromisoformat(records[3]["ts"])  # d1 (포함)
    z = pivot.astimezone(UTC).isoformat().replace("+00:00", "Z")
    kst = pivot.astimezone(timezone(timedelta(hours=9))).isoformat()
    assert client.get(f"{base}/decisions", params={"since": z}).json()["total"] == 5
    assert client.get(f"{base}/decisions", params={"since": kst}).json()["total"] == 5


@pytest.mark.parametrize("mode", MODES)
def test_interval_is_clamped_by_range_window(tmp_path, mode):
    old = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    _seed_decisions(tmp_path, [
        _decision_record(alarm_id="old", tier="page", stage="matrix", ts=old),
        *_records(),
    ])
    client, base = _client(mode, _make_config(tmp_path))
    since = (datetime.now(UTC) - timedelta(hours=6)).isoformat()
    assert client.get(f"{base}/decisions",
                      params={"range": "1h", "since": since}).json()["total"] == 0
    assert client.get(f"{base}/decisions",
                      params={"range": "24h", "since": since}).json()["total"] == 9


# ─── related 경계 ────────────────────────────────────────────────────────


def _related_records() -> list[dict]:
    now = datetime.now(UTC)
    same = (now - timedelta(minutes=30)).isoformat()
    return [
        # 대표가 멤버와 **같은 시각** — 계약상 ts ≤ 항목 ts이므로 찾아야 한다.
        _decision_record(alarm_id="rep", tier="page", stage="matrix", ts=same,
                         fingerprint="fp-rep", alarm_name=SECRET, server_name=SECRET,
                         condition_log="CPU 99%"),
        _decision_record(alarm_id="m1", tier="suppress", stage="correlation", ts=same,
                         fingerprint="fp-m1",
                         correlation_meta={"representative_fp": "fp-rep", "member_seq": 2,
                                           "similarity": 0.9}),
        # 대표 지문이 빈 상관 행 — found=false.
        _decision_record(alarm_id="m2", tier="suppress", stage="correlation",
                         ts=(now - timedelta(minutes=20)).isoformat(), fingerprint="fp-m2",
                         correlation_meta={"representative_fp": "", "member_seq": 3}),
        # 자가복구 — 같은 시각의 발생은 원 발생이 아니다(ts < 항목 ts).
        _decision_record(alarm_id="f-same", tier="ticket", stage="matrix",
                         ts=(now - timedelta(minutes=10)).isoformat(), fingerprint="fp-h",
                         signals={"severity": 2}),
        _decision_record(alarm_id="heal", tier="suppress", stage="self_heal",
                         ts=(now - timedelta(minutes=10)).isoformat(), fingerprint="fp-h",
                         signals={"severity": 0}),
    ]


@pytest.mark.parametrize("mode", MODES)
def test_related_same_ts_rules_and_masking(tmp_path, mode):
    _seed_decisions(tmp_path, _related_records())
    client, base = _client(mode, _make_config(tmp_path))
    items = {i["alarm_id"]: i for i in
             client.get(f"{base}/decisions", params={"related": "true"}).json()["items"]}
    rep = items["m1"]["related"]
    assert rep["found"] is True and rep["alarm_id"] == "rep"
    assert rep["alarm_name"] == "***" and rep["server_name"] == "***"  # 목록과 같은 마스킹
    assert "condition_log" not in rep  # related는 조회 키만 — 실측값을 싣지 않는다
    assert items["m2"]["related"] == {"kind": "representative", "found": False}
    assert items["heal"]["related"] == {"kind": "origin", "found": False}


@pytest.mark.parametrize("mode", MODES)
def test_related_resolves_on_later_pages(tmp_path, mode):
    _seed_decisions(tmp_path, _related_records())
    client, base = _client(mode, _make_config(tmp_path))
    # 최신순 5건 중 m1은 4번째 — size=1 page=4에서도 색인은 tail 전체로 만든다.
    body = client.get(f"{base}/decisions",
                      params={"related": "true", "size": 1, "page": 4}).json()
    assert body["items"][0]["alarm_id"] == "m1"
    assert body["items"][0]["related"]["found"] is True


# ─── summary 활성 여부 — 실제 설정 클래스 기준 ───────────────────────────


def test_stage_enable_fields_are_real_noise_gate_config_fields():
    # getattr(ng, field, False)는 오타를 "꺼짐"으로 삼킨다 — 필드 실재를 설정 클래스로 고정한다.
    from src.config import NoiseGateConfig

    fields = set(NoiseGateConfig.model_fields)
    assert set(noise_routes._STAGE_ENABLE_FIELDS.values()) <= fields
    assert set(noise_routes._POLICY_FIELDS) <= fields


@pytest.mark.parametrize("mode", MODES)
def test_summary_enabled_with_real_config_defaults(tmp_path, mode, monkeypatch):
    from src.config import NoiseGateConfig

    for key in list(__import__("os").environ):
        if key.startswith("NOISE_"):
            monkeypatch.delenv(key, raising=False)
    config = _make_config(tmp_path)
    real = NoiseGateConfig(_env_file=None, enable_noise_gate=True,
                           decision_store_path=config.noise_gate.decision_store_path)
    config.noise_gate = real
    client, base = _client(mode, config)
    stages = {s["stage"]: s for s in client.get(f"{base}/summary").json()["stages"]}
    for stage, field in noise_routes._STAGE_ENABLE_FIELDS.items():
        assert stages[stage]["enabled"] is bool(getattr(real, field)), stage
    for stage in ("severity3", "self_heal", "resolved", "collection_failed", "maintenance",
                  "matrix"):
        assert stages[stage]["enabled"] is True and stages[stage]["enable_key"] is None


# ─── timeseries 로컬 격자 경계 ───────────────────────────────────────────

_FROZEN = datetime(2026, 9, 22, 4, 37, 12, 345678, tzinfo=UTC)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: ANN001, ANN206
        return _FROZEN if tz is not None else _FROZEN.replace(tzinfo=None)


@pytest.fixture()
def frozen(monkeypatch):
    import noise_gate.infrastructure.decision_store as decision_store_module

    monkeypatch.setattr(decision_store_module, "datetime", _FrozenDatetime)


def _points(client, base, **params):
    resp = client.get(f"{base}/timeseries", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()["points"]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("offset,range_,bucket,seconds", [
    (540, "7d", "1d", 86400),
    (540, "24h", "2h", 7200),
    (330, "24h", "2h", 7200),   # 비정수 시간 오프셋(IST)
    (330, "24h", "1h", 3600),
    (345, "1h", "5m", 300),     # 네팔 +5:45
    (-300, "7d", "1d", 86400),  # 음수(EST)
    (-720, "24h", "6h", 21600),
    (840, "30d", "1d", 86400),
])
def test_local_grid_alignment(tmp_path, mode, frozen, offset, range_, bucket, seconds):
    client, base = _client(mode, _make_config(tmp_path))
    points = _points(client, base, range=range_, bucket=bucket, tz_offset_minutes=offset)
    local_tz = timezone(timedelta(minutes=offset))
    assert points
    for p in points:
        boundary = datetime.fromisoformat(p["bucket_ts"])
        assert boundary.utcoffset() == timedelta(0)  # 표기는 UTC
        assert (boundary.timestamp() + offset * 60) % seconds == 0
        local = boundary.astimezone(local_tz)
        if seconds == 86400:
            assert (local.hour, local.minute) == (0, 0)
        elif seconds >= 3600:
            assert local.minute == 0 and local.hour % (seconds // 3600) == 0
    # 연속 격자 · 창 덮기(마지막 경계 ≤ now < 마지막 경계 + bucket).
    ts = [datetime.fromisoformat(p["bucket_ts"]).timestamp() for p in points]
    assert all(b - a == seconds for a, b in zip(ts, ts[1:]))
    assert ts[-1] <= _FROZEN.timestamp() < ts[-1] + seconds


@pytest.mark.parametrize("mode", MODES)
def test_offset_does_not_lose_interior_records(tmp_path, mode, frozen):
    # 창 안쪽 판단은 격자 정렬과 무관하게 정확히 한 번 센다.
    recs = [
        _decision_record(alarm_id=f"x{i}", tier="ticket",
                         ts=(_FROZEN - timedelta(hours=3, minutes=7 * i)).isoformat())
        for i in range(10)
    ]
    _seed_decisions(tmp_path, recs)
    client, base = _client(mode, _make_config(tmp_path))
    for offset in (None, 0, 540, 330, -300):
        params = {"range": "24h", "bucket": "2h"}
        if offset is not None:
            params["tz_offset_minutes"] = offset
        assert sum(p["ticket"] for p in _points(client, base, **params)) == 10, offset


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("value,status", [("-720", 200), ("840", 200), ("5.5", 422),
                                          ("abc", 422), ("", 422)])
def test_offset_parse_edges(tmp_path, mode, value, status):
    client, base = _client(mode, _make_config(tmp_path))
    resp = client.get(f"{base}/timeseries?tz_offset_minutes={value}")
    assert resp.status_code == status


# ─── condition_log — 사용자 경로 어디에도 없다 ───────────────────────────


def _s6(alarm_id: str = "e1", **over) -> dict:
    rec = _decision_record(
        alarm_id=alarm_id, stage="correlation", tier="suppress", fingerprint=f"fp-{alarm_id}",
        db_id="db1", resource_name=SECRET, condition_log="LEAKCHECK 97% > 90%",
        stage_evidence={"inhibitor": SECRET, "markers": [SECRET, "ok"], "n": 3},
        correlation_meta={"representative_fp": "fp-rep"},
    )
    rec.update(over)
    return rec


@pytest.mark.parametrize("mode", MODES)
def test_condition_log_absent_everywhere_on_user_path(tmp_path, mode):
    now = datetime.now(UTC)
    _seed_decisions(tmp_path, [
        _s6("rep", stage="matrix", fingerprint="fp-rep",
            ts=(now - timedelta(minutes=5)).isoformat()),
        _s6("e1", ts=now.isoformat()),
    ])
    client, base = _client(mode, _make_config(tmp_path))
    bodies = [
        client.get(f"{base}/decisions").json(),
        client.get(f"{base}/decisions", params={"related": "true"}).json(),
        client.get(f"{base}/decisions/e1").json(),
        client.get(f"{base}/decisions/rep").json(),
    ]
    text = json.dumps(bodies, ensure_ascii=False)
    if mode == "user":
        assert "condition_log" not in text and "LEAKCHECK" not in text
        # q로 실측값을 역추적할 수 없다(검색 대상 밖).
        assert client.get(f"{base}/decisions", params={"q": "LEAKCHECK"}).json()["total"] == 0
    else:
        # 목록 2 · related 목록 2 · 추적 2 — 레코드 본문에만 있고 related 객체에는 없다.
        assert text.count("LEAKCHECK") == 6
    # 새 문자열 필드는 두 경로 모두 마스킹된다.
    assert SECRET not in text


@pytest.mark.parametrize("mode", MODES)
def test_db_id_is_not_masked_and_numbers_survive(tmp_path, mode):
    _seed_decisions(tmp_path, [_s6("e1")])
    client, base = _client(mode, _make_config(tmp_path))
    item = client.get(f"{base}/decisions").json()["items"][0]
    assert item["db_id"] == "db1"
    assert item["stage_evidence"] == {"inhibitor": "***", "markers": ["***", "ok"], "n": 3}
