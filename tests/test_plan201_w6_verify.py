"""plans/201 W6 독립 검증(verifier) — 첫 화면 「지난 24시간 · 내 담당」 요약.

구현 테스트(`test_plan201_w6_backend.py`·`test_plan201_w6_frontend.py`)가 덮지 않은 칸을 본다.

  V1. 존 누출 0 — 알람 SSE 판정(`event_visible_to`)을 오라클로 삼아 부분 존·전 존 비관리자·관리자·존 없음 사용자에
      대해 db_id 누락·미등록·존 미배정(`polestar`·`itam`) 레코드까지 `tiers`·`needs_attention`·`top_repeat` 전수 대조.
  V2. 반복 상위 — 같은 지문의 존 밖·억제·비담당·비-decision(`type`) 레코드가 건수에 섞이지 않는다.
  V3. 시간 초과 — 이벤트 루프를 막지 않고, 중단 신호 뒤 순회가 멈춘다(창 밖 줄 건너뛰기 구간 포함).
  V4. 토큰 `type` 클레임 — 운영자 종류 토큰·무토큰은 401.
  V5. 서버 `duty_overlap` ↔ 화면 `dutyBadgesOf` — 정본 어휘 전 자원 유형 × 전 단서 유형 × 담당 조합 전수.

레코드는 전부 `DecisionStore.record(...)`로 실제로 쓴 줄을 읽는다(손 dict 금지).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from noise_gate.infrastructure.decision_store import DecisionStore
from src.api.duty_daily_summary import SummaryStopped, record_overlap, summarize_duty_records
from src.api.routes.alarm import event_visible_to
from src.routing.zones import all_zones
from tests.test_plan201_w6_backend import (  # noqa: F401 — vocab는 pytest 픽스처
    _B0,
    _GP,
    _URL,
    _USER_SECRET,
    _USERS,
    _VOCAB,
    _app,
    _hdr,
    _write,
    vocab,
)

_ROOT = Path(__file__).resolve().parents[1]

#: 존 판정이 갈리는 db_id 전부 — 은행존 · 공동존 · 누락 · 미등록 · 존 미배정 등록 DB.
_DB_IDS = (_B0, _GP, "", "unknown_db_xyz", "polestar", "itam")
_TIERS = ("page", "ticket", "dashboard", "suppress")


def _seed_matrix(path: Path) -> DecisionStore:
    """db_id × 티어 × 자원 유형마다 서버 이름이 고유한 레코드를 깐다(이름 = 누출 탐지 표지)."""
    store = DecisionStore(str(path))
    minute = 1.0
    for idx, db_id in enumerate(_DB_IDS):
        for tier in _TIERS:
            for rt in ("server", "database"):
                _write(store, tier=tier, db_id=db_id, server=f"SRV{idx}-{tier}-{rt}", rt=rt,
                       name=f"알람{idx}-{tier}-{rt}", minutes_ago=minute,
                       fp=f"fp{idx}-{tier}-{rt}")
                minute += 1.0
    return store


def _oracle(store: DecisionStore, *, areas: list[str], zones: set[str], admin: bool) -> dict[str, int]:
    """알람 SSE 판정 그대로 — 보이는 레코드 중 담당 겹침만 티어별로 센다."""
    deliver_all = zones >= set(all_zones())
    counts: dict[str, int] = {"page": 0, "ticket": 0, "dashboard": 0}
    if admin:
        counts["suppress"] = 0
    for rec in store.iter_recent_decisions(24 * 3600):
        if not zones:
            continue
        if not event_visible_to(rec, allowed_zones=zones, deliver_all=deliver_all, is_admin=admin):
            continue
        if not record_overlap(rec, _VOCAB, areas).mine:
            continue
        counts[rec["tier"]] += 1
    return counts


def _invisible_names(store: DecisionStore, *, zones: set[str], admin: bool) -> set[str]:
    deliver_all = zones >= set(all_zones())
    return {
        rec["server_name"]
        for rec in store.iter_recent_decisions(24 * 3600)
        if not event_visible_to(rec, allowed_zones=zones, deliver_all=deliver_all, is_admin=admin)
    }


# ─── V1. 존 누출 0 — SSE 판정 오라클 대조 ─────────────────────────────────────


@pytest.mark.parametrize("sub,areas,zones,admin", [
    ("alpha_b0", ["alpha"], {"bankjon"}, False),                 # 부분 존
    ("beta_all", ["beta"], {"bankjon", "gongjon"}, False),       # 전 존 비관리자
    ("admin", ["alpha"], {"bankjon", "gongjon"}, True),          # 관리자
])
def test_counts_equal_sse_visibility_oracle(tmp_path, vocab, sub, areas, zones, admin) -> None:
    path = tmp_path / "d.jsonl"
    store = _seed_matrix(path)
    resp = TestClient(_app(path)).get(_URL, headers=_hdr(sub))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["eligible"] is True
    assert body["tiers"] == _oracle(store, areas=areas, zones=zones, admin=admin)
    hidden = _invisible_names(store, zones=zones, admin=admin)
    text = resp.text
    leaked = sorted(name for name in hidden if name in text)
    assert leaked == [], leaked
    for item in body["needs_attention"]:
        assert item["server_name"] not in hidden and item["tier"] in {"page", "ticket"}
    if not admin:
        assert "suppress" not in body["tiers"] and "suppress" not in text
    if sub == "alpha_b0":
        # 부분 존 사용자: 공동존·누락·미등록·존 미배정 db_id는 하나도 보이지 않는다(실제로 거른 것이 있다)
        assert {"SRV1-page-server", "SRV2-page-server", "SRV3-page-server", "SRV4-page-server",
                "SRV5-page-server"} <= hidden
        assert body["tiers"] == {"page": 1, "ticket": 1, "dashboard": 1}


def test_no_alarm_zone_user_gets_no_counts_even_with_records(tmp_path, vocab) -> None:
    path = tmp_path / "d.jsonl"
    _seed_matrix(path)
    body = TestClient(_app(path)).get(_URL, headers=_hdr("nozone")).json()
    assert body == {**body, "eligible": False, "reason": "no_alarm_zone"}
    assert not {"tiers", "needs_attention", "top_repeat"} & set(body)
    assert "SRV" not in json.dumps(body)


# ─── V2. 반복 상위 — 지문 집계 오염 0 ─────────────────────────────────────────


def test_top_repeat_ignores_out_of_zone_suppress_nonduty_and_typed_records(tmp_path, vocab) -> None:
    path = tmp_path / "d.jsonl"
    store = DecisionStore(str(path))
    # 지문 F: 은행존 담당 page 1건 + 같은 지문의 공동존 3건 · 은행존 억제 3건 · 은행존 비담당(database) 3건
    _write(store, tier="page", db_id=_B0, server="bk-F", name="F알람", rt="server", fp="F", minutes_ago=30)
    for i in range(3):
        _write(store, tier="page", db_id=_GP, server=f"gj-F{i}", name="F공동", rt="server", fp="F",
               minutes_ago=20 + i)
        _write(store, tier="suppress", db_id=_B0, server=f"bk-Fs{i}", name="F억제", rt="server", fp="F",
               minutes_ago=10 + i)
        _write(store, tier="dashboard", db_id=_B0, server=f"bk-Fdb{i}", name="F비담당", rt="database", fp="F",
               minutes_ago=5 + i)
        store.record_resolution(fingerprint="F", duration_seconds=1.0)          # type=resolution 보조 레코드
    body = TestClient(_app(path)).get(_URL, headers=_hdr("alpha_b0")).json()
    assert body["top_repeat"] is None, body["top_repeat"]
    assert body["tiers"] == {"page": 1, "ticket": 0, "dashboard": 0}
    # 같은 지문 은행존 담당 레코드가 하나 더 오면 그때 2건 — 이름은 보이는 레코드의 것
    _write(store, tier="ticket", db_id=_B0, server="bk-F2", name="F알람", rt="server", fp="F", minutes_ago=1)
    body = TestClient(_app(path)).get(_URL, headers=_hdr("alpha_b0")).json()
    assert body["top_repeat"] == {"alarm_name": "F알람", "count": 2}
    text = json.dumps(body, ensure_ascii=False)
    for name in ("F공동", "F억제", "F비담당", "gj-F", "bk-Fs", "bk-Fdb"):
        assert name not in text, name


# ─── V3. 시간 초과 · 중단 ──────────────────────────────────────────────────────


def test_stop_signal_halts_scan_even_while_skipping_out_of_window_lines(tmp_path, monkeypatch) -> None:
    """[Major M-1 대응 — 교정 전 실패] 결정 기록은 보존 상한 없이 쌓이고(append-only · 회전 없음) 24시간 창은
    파일 끝에 있다. 시간 초과 뒤 `stop.set()`을 해도 `should_stop`은 **내준 레코드마다**만 확인되므로, 창 밖
    줄을 건너뛰는 구간(파일 대부분)에서는 스레드가 끝까지 읽는다 — 요청마다 기본 실행기 스레드가 붙잡힌다."""
    path = tmp_path / "d.jsonl"
    store = DecisionStore(str(path))
    for i in range(400):
        _write(store, tier="dashboard", db_id=_B0, server=f"old{i}", rt="server", minutes_ago=48 * 60 + i)
    _write(store, tier="page", db_id=_B0, server="new", rt="server", minutes_ago=1)
    checked = {"n": 0}
    orig = DecisionStore._within_window

    def _counting(rec, cutoff):  # noqa: ANN001
        checked["n"] += 1
        return orig(rec, cutoff)

    monkeypatch.setattr(DecisionStore, "_within_window", staticmethod(_counting))
    with pytest.raises(SummaryStopped):
        summarize_duty_records(
            store.iter_recent_decisions(24 * 3600), vocab=_VOCAB, user_areas=["alpha"],
            visible=lambda rec: True, include_suppress=False, should_stop=lambda: True,
        )
    # 중단이 이미 요청된 상태 — 창 밖 400줄을 끝까지 판정하지 않아야 한다
    assert checked["n"] < 50, f"중단 요청 뒤에도 {checked['n']}줄을 읽었다"


def test_timeout_keeps_event_loop_responsive_and_stops_thread(tmp_path, vocab, monkeypatch) -> None:
    """창 안 레코드를 느리게 내는 순회 — 응답은 시간 초과 사유, 그동안 루프는 돈다, 그 뒤 순회가 멈춘다."""
    import httpx

    from src.api.routes import duty_areas

    path = tmp_path / "d.jsonl"
    _write(DecisionStore(str(path)), tier="page", db_id=_B0, server="s", rt="server")
    app = _app(path)
    monkeypatch.setattr(duty_areas, "DAILY_SUMMARY_TIMEOUT_SECONDS", 0.3)
    yields = {"n": 0}
    rec = next(DecisionStore(str(path)).iter_recent_decisions(3600))

    def _slow(self, window):  # noqa: ANN001
        while True:
            time.sleep(0.02)
            yields["n"] += 1
            yield dict(rec)

    monkeypatch.setattr(DecisionStore, "iter_recent_decisions", _slow)

    async def _main() -> tuple[dict, int]:
        ticks = 0
        done = asyncio.Event()

        async def _ticker() -> None:
            nonlocal ticks
            while not done.is_set():
                ticks += 1
                await asyncio.sleep(0.01)

        task = asyncio.create_task(_ticker())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.get(_URL, headers=_hdr("alpha_b0"))
        done.set()
        await task
        return resp.json(), ticks

    threads_before = threading.active_count()
    body, ticks = asyncio.run(_main())
    assert body["eligible"] is True and body["unavailable"] == "timeout"
    assert "tiers" not in body and "needs_attention" not in body                 # 없는 수치를 만들지 않는다
    assert ticks >= 10                                                            # 루프가 막히지 않았다
    time.sleep(0.2)
    settled = yields["n"]
    time.sleep(0.3)
    assert yields["n"] == settled                                                 # 중단 뒤 더 읽지 않는다
    assert threading.active_count() <= threads_before + 1


# ─── V4. 토큰 종류 ────────────────────────────────────────────────────────────


def test_admin_type_token_and_missing_token_are_rejected(tmp_path, vocab) -> None:
    path = tmp_path / "d.jsonl"
    _seed_matrix(path)
    client = TestClient(_app(path))
    wrong = jwt.encode({"sub": "admin", "type": "admin", "exp": datetime.now(UTC) + timedelta(hours=1)},
                       _USER_SECRET, algorithm="HS256")
    for headers in ({}, {"Authorization": f"Bearer {wrong}"}, {"Authorization": "Bearer junk"}):
        resp = client.get(_URL, headers=headers)
        assert resp.status_code == 401, (headers, resp.status_code)
        assert "SRV" not in resp.text
    assert _USERS["admin"].duty_areas == ["alpha"]                                 # 픽스처 전제


# ─── V5. 서버 ↔ 화면 담당 겹침 전수 ──────────────────────────────────────────


def test_server_overlap_matches_frontend_badges_exhaustively() -> None:
    from src.api import duty_area_support
    from src.domain.duty_areas import duty_overlap
    from src.infrastructure.duty_area_vocabulary import read_duty_area_vocabulary
    from tests.test_plan201_w6_frontend import _ME_BASE, _js, _node

    real = read_duty_area_vocabulary(_ROOT / "config" / "duty_areas.yaml")
    orig = duty_area_support.load_duty_area_vocabulary
    duty_area_support.load_duty_area_vocabulary = lambda: real          # type: ignore[assignment]
    try:
        meta = duty_area_support.duty_alarm_focus_meta()
    finally:
        duty_area_support.load_duty_area_vocabulary = orig               # type: ignore[assignment]
    rts: list[object] = [""]
    for area in real.areas:
        for rt in area.resource_types:
            rts += [rt, f"  {rt.upper()} "]
    rts += ["미매핑유형"]
    types: list[object] = ["", "NOPE"]
    for key in list(real.cause_hints.apm_event_types) + list(real.cause_hints.known_unmapped):
        types += [key, f"error_{key.lower()}", " WARNING_" + key.replace("_", " ") + " "]
    codes = [a.code for a in real.areas]
    area_sets = [list(c) for n in range(1, len(codes) + 1) for c in itertools.combinations(codes, n)]
    cases = [{"alarm": {"resource_type": rt, "apm_event_type": et}, "areas": areas}
             for rt in rts for et in types for areas in area_sets]
    out = _node(f"""
var cases = {_js(cases)};
var meta = {_js(meta)};
out = cases.map(function (c) {{ var b = APP.badges(c.alarm, meta, c.areas); return [b.occurrence, b.hint]; }});
""", me=_ME_BASE)
    server = []
    for c in cases:
        o = duty_overlap(real, c["areas"], resource_type=c["alarm"]["resource_type"],
                         event_type=c["alarm"]["apm_event_type"])
        server.append([o.occurrence, o.hint])
    mismatches = [(cases[i], out[i], server[i]) for i in range(len(cases)) if out[i] != server[i]]
    assert mismatches == [], mismatches[:5]
    assert any(o for o, _ in server) and any(h for _, h in server)


# ─── (재검증 라운드) iter_recent_decisions 역방향 읽기 정확성 ───

import noise_gate.infrastructure.decision_store as _ds_mod  # noqa: E402


def _forward_oracle(path: Path, window_seconds: int) -> list[dict]:
    """정방향 기준: 전 파일을 읽어 같은 제외 규칙으로 창 안 결정 레코드를 최신부터 돌려준다."""
    cutoff = datetime.now(UTC).timestamp() - window_seconds
    out: list[dict] = []
    for raw in path.read_bytes().split(b"\n"):
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(rec, dict) or rec.get("type"):
            continue
        if not DecisionStore._within_window(rec, cutoff):
            continue
        out.append(rec)
    return list(reversed(out))


def _rec(i: int, when: datetime, name: str = "") -> dict:
    return {"tier": "page", "alarm_id": f"a{i}", "alarm_name": name or f"디스크 사용률 경보 {i}",
            "ts": when.isoformat()}


@pytest.mark.parametrize("block", [1, 3, 7, 64, 4096])
@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("trailing", [True, False])
def test_reverse_scan_matches_forward_oracle(tmp_path, monkeypatch, block, newline, trailing) -> None:
    """블록 경계·한글 멀티바이트·CRLF·끝 개행 유무·손상 줄·type 레코드에서 정방향 결과와 같다."""
    monkeypatch.setattr(_ds_mod, "_REVERSE_SCAN_BLOCK_BYTES", block)
    now = datetime.now(UTC)
    lines: list[bytes] = []
    for i in range(30):
        when = now - timedelta(hours=30) + timedelta(hours=i)  # 창 밖 6건 + 창 안 24건
        lines.append(json.dumps(_rec(i, when), ensure_ascii=False).encode("utf-8"))
        if i % 5 == 0:
            lines.append(b"{broken \xea\xb0")  # 손상 줄(잘린 한글)
        if i % 7 == 0:
            lines.append(json.dumps({"type": "resolution", "ts": now.isoformat()}).encode())
        if i % 9 == 0:
            lines.append(b"")
    lines.append(json.dumps({"tier": "info", "alarm_id": "nots", "alarm_name": "ts 없음"},
                            ensure_ascii=False).encode())
    path = tmp_path / "d.jsonl"
    path.write_bytes(newline.join(lines) + (newline if trailing else b""))
    store = DecisionStore(str(path), True, 5)
    got = list(store.iter_recent_decisions(24 * 3600))
    assert got == _forward_oracle(path, 24 * 3600)
    assert any(r["alarm_id"] == "nots" for r in got)
    assert all("디스크" in r["alarm_name"] or r["alarm_id"] == "nots" for r in got)


def test_reverse_scan_empty_and_single_line(tmp_path) -> None:
    empty = tmp_path / "e.jsonl"
    empty.write_bytes(b"")
    assert list(DecisionStore(str(empty)).iter_recent_decisions(3600)) == []
    one = tmp_path / "o.jsonl"
    rec = _rec(1, datetime.now(UTC))
    one.write_bytes(json.dumps(rec, ensure_ascii=False).encode())
    assert list(DecisionStore(str(one)).iter_recent_decisions(3600)) == [rec]
    missing = tmp_path / "missing.jsonl"
    assert list(DecisionStore(str(missing)).iter_recent_decisions(3600)) == []


def test_reverse_scan_tolerates_out_of_order_within_slack(tmp_path) -> None:
    """창 하한보다 조금(여유 600초 안) 오래된 레코드가 끼어 있어도 그 앞 창 안 레코드를 놓치지 않는다."""
    now = datetime.now(UTC)
    window = 3600
    recs = [
        _rec(1, now - timedelta(seconds=window - 100)),       # 창 안(앞쪽)
        _rec(2, now - timedelta(seconds=window + 300)),       # 창 밖이지만 여유 안 → 계속
        _rec(3, now - timedelta(seconds=10)),
    ]
    path = tmp_path / "d.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    got = [r["alarm_id"] for r in DecisionStore(str(path)).iter_recent_decisions(window)]
    assert got == ["a3", "a1"]


def test_reverse_scan_stops_beyond_slack(tmp_path) -> None:
    """여유를 넘는 오래된 레코드 뒤(더 앞쪽)는 읽지 않는다 — 종료 조건 문서화된 한계."""
    now = datetime.now(UTC)
    window = 3600
    recs = [
        _rec(1, now - timedelta(seconds=60)),                  # 앞쪽 창 안(어긋난 append 가정)
        _rec(2, now - timedelta(seconds=window + 601 + 5)),    # 여유 초과 → 정지
        _rec(3, now - timedelta(seconds=10)),
    ]
    path = tmp_path / "d.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    got = [r["alarm_id"] for r in DecisionStore(str(path)).iter_recent_decisions(window)]
    assert got == ["a3"]


def test_decision_store_is_fresh_per_request_so_scan_should_stop_is_not_shared(tmp_path) -> None:
    from types import SimpleNamespace

    from src.api.routes.noise_dashboard import _decision_store

    ng = SimpleNamespace(decision_store_path=str(tmp_path / "d.jsonl"),
                         decision_store_enabled=True, decision_store_max_lines=10)
    cfg = SimpleNamespace(noise_gate=ng)
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=cfg)))
    try:
        a = _decision_store(req)
        b = _decision_store(req)
    except Exception as exc:  # pragma: no cover — 설정 접근 경로가 다르면 실측 실패로 드러낸다
        pytest.fail(f"_decision_store 구성 실패: {exc!r}")
    assert a is not b
    a.scan_should_stop = lambda: True
    assert b.scan_should_stop is None
    assert DecisionStore.scan_should_stop is None


def test_reverse_scan_cost_scales_with_window_not_prefix(tmp_path) -> None:
    """창 밖 접두 20만 줄 + 창 안 50건: 읽는 줄 수가 창 안 크기에 비례(접두 대부분 미독)."""
    now = datetime.now(UTC)
    old = json.dumps(_rec(0, now - timedelta(days=30)), ensure_ascii=False).encode() + b"\n"
    path = tmp_path / "d.jsonl"
    with path.open("wb") as fh:
        fh.write(old * 200_000)
        for i in range(50):
            fh.write(json.dumps(_rec(i, now - timedelta(minutes=50 - i)), ensure_ascii=False)
                     .encode() + b"\n")
    store = DecisionStore(str(path))
    calls = {"n": 0}

    def _stop() -> bool:
        calls["n"] += 1
        return False

    t0 = time.perf_counter()
    got = list(store.iter_recent_decisions(24 * 3600, should_stop=_stop))
    elapsed = time.perf_counter() - t0
    assert len(got) == 50
    # 정지 확인은 256줄마다 — 블록 하나(64KB ≈ 수백 줄) 안에서 끝나야 한다
    assert calls["n"] <= 4
    assert elapsed < 0.5
