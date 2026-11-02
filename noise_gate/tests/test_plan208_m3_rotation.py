"""plans/208 트랙 C M-3 (b) — 판정 기록 파일 회전 · 조각을 가로지르는 읽기.

- 기본(회전 없음): 조각이 생기지 않고 파일 내용·읽기 결과가 종전과 같다.
- 크기·날짜 회전: 현재 파일 이름만 `<이름>.<회전 시각 UTC>`로 바꾸고 지우지 않는다
  — 조각 이름순 = 시간순.
- 모든 리더(집계·메타경보·관제 조회·추적·복원·값 집계·24시간 요약·L3 상태·스크립트)가
  조각을 이어 읽어 「회전하지 않은 한 파일」과 같은 결과를 낸다.
- 회전과 겹친 읽기(목록 읽기와 현재 파일 열기 사이 회전 · 연 뒤 회전)에서 줄을 잃거나
  두 번 세지 않는다.
- 쓰기 주체 여럿(스레드·프로세스)이 같은 파일을 회전시켜도 줄이 빠지거나 깨지지 않는다(W5 대비).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from noise_gate.domain.notification_policy import NotificationDecision
from noise_gate.infrastructure import decision_store as ds
from noise_gate.infrastructure import jsonl_tail
from noise_gate.infrastructure.decision_store import (
    DecisionStore,
    decision_cursor,
    decision_file_chain,
    decision_fragments,
    iter_decision_lines,
    iter_decision_lines_since,
    open_decision_chain,
)

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean_tail_cache():
    jsonl_tail.clear()
    yield
    jsonl_tail.clear()


def _iso(seconds_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat()


def _records(n: int = 60) -> list[dict[str, Any]]:
    """시간순 결정·비결정 레코드 — 창 안·밖, 상관·자가복구·PAGE 묶음·L3 상태가 섞인다."""
    out: list[dict[str, Any]] = []
    tiers = ("page", "ticket", "dashboard", "suppress")
    for i in range(n):
        ago = (n - i) * 120  # 2분 간격 · 맨 앞은 2시간 전
        rec: dict[str, Any] = {
            "ts": _iso(ago),
            "alarm_id": f"a{i}",
            "tier": tiers[i % 4],
            "reason": f"r{i}",
            "fingerprint": f"fp{i % 7}",
            "signals": {"severity": 1 + i % 3},
            "alarm_name": f"알람{i % 5}",
            "server_name": f"srv{i % 3}",
            "resource_type": f"rt{i % 2}",
            "stage": ("flapping", "correlation", "self_heal", "severity3")[i % 4],
        }
        if i % 4 == 1:
            rec["correlation_meta"] = {"representative_fp": f"fp{(i + 1) % 7}"}
        if i % 9 == 0:
            rec["stage_evidence"] = {
                "collection_failed": True,
                "page_review": {"would_demote": True, "page_alarm_id": "a0"},
            }
        out.append(rec)
        if i % 6 == 0:
            out.append({"type": "resolution", "fingerprint": f"fp{i % 7}",
                        "duration_seconds": 30 + i, "ts": _iso(ago)})
        if i % 10 == 0:
            out.append({"type": "l3_state", "fingerprint": "fpL", "state_fingerprint": {"v": i},
                        "ts": _iso(ago)})
        if i % 8 == 0:
            out.append({"type": "recurrence", "fingerprint": f"fp{i % 7}", "count": 2,
                        "alarm_id": f"a{i}", "ts": _iso(ago)})
    return out


def _fill(store: DecisionStore, records: list[dict[str, Any]]) -> None:
    for rec in records:
        store._append_line(json.dumps(rec, ensure_ascii=False))


def _pair(tmp_path: Path, max_bytes: int = 1500) -> tuple[DecisionStore, DecisionStore]:
    """같은 레코드를 한 파일(회전 없음)과 회전 조각들로 나눠 쓴 두 저장소."""
    recs = _records()
    plain = DecisionStore(str(tmp_path / "plain" / "d.jsonl"), max_lines=40)
    rotated = DecisionStore(
        str(tmp_path / "rot" / "d.jsonl"), max_lines=40, rotate_max_bytes=max_bytes
    )
    for store in (plain, rotated):
        store.path.parent.mkdir(parents=True)
        _fill(store, recs)
    assert len(decision_fragments(rotated.path)) >= 5
    assert decision_fragments(plain.path) == []
    return plain, rotated


def _strip_age(agg: dict[str, Any]) -> dict[str, Any]:
    out = dict(agg)
    out.pop("last_event_age_seconds")
    return out


# ── 잠금 미지원 플랫폼(Windows) ─────────────────────────────────────────────────


def test_module_imports_without_fcntl(tmp_path: Path) -> None:
    """fcntl이 없는 플랫폼에서도 판정 저장소 모듈이 임포트된다(off = 종전과 같다)."""
    code = (
        "import sys; sys.modules['fcntl'] = None\n"
        "from noise_gate.infrastructure import decision_store as ds\n"
        "assert ds.fcntl is None\n"
        "s = ds.DecisionStore(sys.argv[1])\n"
        "s._append_line('{\"ts\": \"x\"}')\n"
        "print(len(list(ds.iter_decision_lines(s.path))))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "d.jsonl")],
        cwd=REPO, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "1"


def test_without_fcntl_rotation_is_disabled_with_warning_and_reads_still_span_fragments(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    plain, rotated = _pair(tmp_path)
    before = decision_fragments(rotated.path)
    monkeypatch.setattr(ds, "fcntl", None)
    with caplog.at_level("WARNING", logger=ds.__name__):
        store = DecisionStore(
            str(rotated.path), max_lines=40, recent_scan=True, rotate_max_bytes=10,
            rotate_daily=True,
        )
    assert (store.rotate_max_bytes, store.rotate_daily, store.recent_scan) == (0, False, True)
    assert sum("회전 비활성" in r.getMessage() for r in caplog.records) == 1
    # 회전을 요청하지 않은 저장소는 경고하지 않는다
    caplog.clear()
    DecisionStore(str(rotated.path))
    assert not caplog.records
    # 읽기는 기존 조각을 가로지른다(역방향 집계 포함)
    for window in (None, 3600):
        assert _strip_age(store.aggregate(window_seconds=window)) == _strip_age(
            plain.aggregate(window_seconds=window)
        )
    assert store.funnel(window_seconds=3600) == plain.funnel(window_seconds=3600)
    # 크기 상한을 넘겨 써도 새 조각이 생기지 않는다
    size = rotated.path.stat().st_size
    _fill(store, _records(20))
    assert decision_fragments(rotated.path) == before
    assert rotated.path.stat().st_size > size


# ── 기본값 · 회전 없음 ────────────────────────────────────────────────────────


def test_defaults_are_off_and_mock_values_do_not_enable(tmp_path: Path) -> None:
    store = DecisionStore(str(tmp_path / "d.jsonl"))
    assert (store.recent_scan, store.rotate_max_bytes, store.rotate_daily) == (False, 0, False)
    mocked = DecisionStore(
        str(tmp_path / "d.jsonl"),
        recent_scan=MagicMock(),
        rotate_max_bytes=MagicMock(),
        rotate_daily=MagicMock(),
    )
    assert (mocked.recent_scan, mocked.rotate_max_bytes, mocked.rotate_daily) == (False, 0, False)
    assert DecisionStore(str(tmp_path / "d.jsonl"), rotate_max_bytes=True).rotate_max_bytes == 0
    assert DecisionStore(str(tmp_path / "d.jsonl"), rotate_max_bytes=-5).rotate_max_bytes == 0


def test_rotation_off_writes_single_file_byte_identical(tmp_path: Path) -> None:
    store = DecisionStore(str(tmp_path / "d.jsonl"))
    recs = _records(30)
    _fill(store, recs)
    expected = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs)
    assert store.path.read_text(encoding="utf-8") == expected
    assert decision_fragments(store.path) == []
    assert decision_file_chain(store.path) == [store.path]


def test_record_methods_rotate_through_common_append(tmp_path: Path) -> None:
    store = DecisionStore(str(tmp_path / "d.jsonl"), rotate_max_bytes=200)
    decision = NotificationDecision(tier="page", reason="x", priority=1, signals={})
    store.record(decision, alarm_id="a1")
    store.record(decision, alarm_id="a2")
    store.record_resolution(fingerprint="f", duration_seconds=1.0)
    store.record_recurrence(fingerprint="f", count=2)
    store.record_investigation(alarm_id="a1", status="done")
    store.record_late_promotion(alarm_id="a1")
    store.record_l3_state(fingerprint="f", state_fingerprint={"k": 1})
    lines = list(iter_decision_lines(store.path))
    assert len(lines) == 7
    assert len(decision_fragments(store.path)) >= 2
    assert store.last_l3_state("f") == {"k": 1}


# ── 회전 동작 ─────────────────────────────────────────────────────────────────


def test_size_rotation_keeps_every_fragment_in_time_order(tmp_path: Path) -> None:
    store = DecisionStore(str(tmp_path / "d.jsonl"), rotate_max_bytes=500)
    recs = _records(40)
    _fill(store, recs)
    frags = decision_fragments(store.path)
    assert len(frags) >= 3
    for frag in frags:
        assert ds._fragment_key(store.path, frag.name) is not None
        assert frag.stat().st_size >= 500  # 넘은 다음 기록에서 나눈다
    assert frags == sorted(frags, key=lambda p: ds._fragment_key(store.path, p.name) or ("", 0))
    joined = [json.loads(line) for line in iter_decision_lines(store.path)]
    assert joined == recs  # 잃은 줄·중복 0 · 순서 보존


def test_same_or_backward_clock_stamp_gets_sequence_suffix(tmp_path: Path, monkeypatch) -> None:
    store = DecisionStore(str(tmp_path / "d.jsonl"), rotate_max_bytes=10)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN206
            return datetime(2026, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(ds, "datetime", _Frozen)
    for i in range(4):
        store._append_line(json.dumps({"i": i, "pad": "x" * 20}))
    names = [p.name for p in decision_fragments(store.path)]
    assert names == [
        "d.jsonl.20260101T000000000000Z",
        "d.jsonl.20260101T000000000000Z-1",
        "d.jsonl.20260101T000000000000Z-2",
    ]
    assert [json.loads(x)["i"] for x in iter_decision_lines(store.path)] == [0, 1, 2, 3]


def test_daily_rotation_on_date_change_only(tmp_path: Path) -> None:
    store = DecisionStore(str(tmp_path / "d.jsonl"), rotate_daily=True)
    store._append_line(json.dumps({"i": 0}))
    store._append_line(json.dumps({"i": 1}))
    assert decision_fragments(store.path) == []  # 같은 날은 나누지 않는다
    yesterday = (datetime.now() - timedelta(days=1)).timestamp()
    os.utime(store.path, (yesterday, yesterday))
    store._append_line(json.dumps({"i": 2}))
    frags = decision_fragments(store.path)
    assert len(frags) == 1
    assert [json.loads(x)["i"] for x in frags[0].read_text().splitlines()] == [0, 1]
    assert [json.loads(x)["i"] for x in store.path.read_text().splitlines()] == [2]


def test_unrelated_names_are_not_fragments(tmp_path: Path) -> None:
    base = tmp_path / "d.jsonl"
    base.write_text('{"i": 0}\n')
    for name in ("d.jsonl.bak", "d.jsonl.lock", "d.jsonl.20260101T000000Z",
                 "x.jsonl.20260101T000000000000Z", "d.jsonl.20260101T000000000000Z.gz"):
        (tmp_path / name).write_text('{"bogus": 1}\n')
    assert decision_fragments(base) == []
    assert list(iter_decision_lines(base)) == ['{"i": 0}\n']


# ── 조각을 가로지르는 리더 = 한 파일과 같은 결과 ─────────────────────────────


def test_readers_across_fragments_match_single_file(tmp_path: Path) -> None:
    plain, rotated = _pair(tmp_path)
    for window in (None, 3600, 600):
        assert _strip_age(rotated.aggregate(window_seconds=window)) == _strip_age(
            plain.aggregate(window_seconds=window)
        )
    kwargs = dict(window_seconds=3600, suppress_ratio_threshold=0.1, min_events=500,
                  collection_failed_ratio_threshold=0.01)
    assert rotated.meta_alerts(**kwargs) == plain.meta_alerts(**kwargs)
    snap_r, snap_p = rotated.health_snapshot(**kwargs), plain.health_snapshot(**kwargs)
    assert (_strip_age(snap_r[0]), snap_r[1]) == (_strip_age(snap_p[0]), snap_p[1])
    assert rotated.funnel(window_seconds=3600) == plain.funnel(window_seconds=3600)
    assert rotated.top_suppressed(window_seconds=None) == plain.top_suppressed(window_seconds=None)
    assert rotated.timeseries(window_seconds=7200, bucket_seconds=600) == plain.timeseries(
        window_seconds=7200, bucket_seconds=600
    )
    assert rotated.list_decisions(related=True, size=100) == plain.list_decisions(
        related=True, size=100
    )
    for alarm_id in ("a0", "a5", "a30", "a59", "nope"):
        assert rotated.get_decision(alarm_id) == plain.get_decision(alarm_id)
    assert rotated.recent_decisions(3600, include_recurrence=True) == plain.recent_decisions(
        3600, include_recurrence=True
    )
    assert rotated.count_recent_values(7200, "resource_type") == plain.count_recent_values(
        7200, "resource_type"
    )
    assert list(rotated.iter_recent_decisions(3600)) == list(plain.iter_recent_decisions(3600))
    assert rotated.last_l3_state("fpL") == plain.last_l3_state("fpL") == {"v": 50}


def test_tail_chain_matches_legacy_deque_over_concatenation(tmp_path: Path) -> None:
    store = DecisionStore(str(tmp_path / "d.jsonl"), max_lines=25, rotate_max_bytes=300)
    lines: list[str] = []
    for i in range(80):
        line = "" if i % 13 == 0 else ("{bad" if i % 17 == 0 else json.dumps({"i": i}))
        lines.append(line)
        store._append_line(line)
        legacy = [json.loads(x) for x in deque(lines, maxlen=25) if x and x != "{bad"]
        assert store._tail_chain() == legacy, i


def test_tail_chain_discards_fragment_cache_when_current_file_refills(tmp_path: Path) -> None:
    store = DecisionStore(str(tmp_path / "d.jsonl"), max_lines=5, rotate_max_bytes=120)
    for i in range(6):
        store._append_line(json.dumps({"i": i, "pad": "y" * 30}))
    frags = decision_fragments(store.path)
    assert frags
    store._tail_chain()
    assert any(jsonl_tail.stats(f) is not None for f in frags)  # 현재 파일이 모자라 조각을 읽었다
    big = DecisionStore(str(tmp_path / "d.jsonl"), max_lines=5)  # 회전 없이 현재 파일만 채운다
    for i in range(10):
        big._append_line(json.dumps({"j": i}))
    assert [r["j"] for r in big._tail_chain()] == [5, 6, 7, 8, 9]
    assert all(jsonl_tail.stats(f) is None for f in frags)


def test_reader_sees_fragments_while_current_file_is_momentarily_missing(tmp_path: Path) -> None:
    plain, rotated = _pair(tmp_path)
    # 회전 직후 · 다음 append 전 — 현재 파일이 없다
    rotated.path.rename(rotated.path.with_name("d.jsonl.29990101T000000000000Z"))
    assert not rotated.path.exists()
    assert rotated.list_decisions(size=100)["total"] == plain.list_decisions(size=100)["total"]
    assert _strip_age(rotated.aggregate(window_seconds=None)) == _strip_age(
        plain.aggregate(window_seconds=None)
    )


# ── 회전과 겹친 읽기 ─────────────────────────────────────────────────────────


def test_rotation_between_listing_and_open_retries(tmp_path: Path, monkeypatch) -> None:
    store = DecisionStore(str(tmp_path / "d.jsonl"))
    _fill(store, [{"i": i} for i in range(5)])
    real = ds.decision_fragments
    calls = {"n": 0}

    def _rotating(path):  # noqa: ANN001, ANN202
        calls["n"] += 1
        listed = real(path)
        if calls["n"] == 1:  # 목록을 읽은 직후 · 현재 파일을 열기 전에 회전 + 새 기록
            Path(path).rename(Path(path).with_name("d.jsonl.20260101T000000000000Z"))
            Path(path).write_text(json.dumps({"i": 5}) + "\n")
        return listed

    monkeypatch.setattr(ds, "decision_fragments", _rotating)
    got = [json.loads(x)["i"] for x in iter_decision_lines(store.path)]
    assert got == [0, 1, 2, 3, 4, 5]
    assert calls["n"] >= 4  # 첫 시도는 목록이 달라 다시 했다


def test_rotation_after_open_neither_drops_nor_duplicates(tmp_path: Path) -> None:
    store = DecisionStore(str(tmp_path / "d.jsonl"), rotate_max_bytes=10_000)
    _fill(store, [{"i": i} for i in range(5)])
    with open_decision_chain(store.path, binary=False) as (frags, active):
        assert frags == [] and active is not None
        # 연 뒤 회전 — 연 파일은 이름이 바뀌어도 그대로 끝까지 읽힌다 · 새 조각은 이번 목록에 없다
        store.path.rename(store.path.with_name("d.jsonl.20260101T000000000000Z"))
        store._append_line(json.dumps({"i": 99}))
        got = [json.loads(x)["i"] for x in active]
    assert got == [0, 1, 2, 3, 4]
    assert [json.loads(x)["i"] for x in iter_decision_lines(store.path)] == [0, 1, 2, 3, 4, 99]


def test_tail_cache_reloads_on_inode_change_without_double_count(tmp_path: Path) -> None:
    store = DecisionStore(str(tmp_path / "d.jsonl"), max_lines=50, rotate_max_bytes=250)
    seen: list[int] = []
    for i in range(40):
        store._append_line(json.dumps({"i": i, "p": "z" * 10}))
        seen.append(i)
        got = [r["i"] for r in store._tail_chain()]
        assert got == seen[-50:], i


# ── 쓰기 주체 여럿 ───────────────────────────────────────────────────────────


def test_concurrent_writers_threads_lose_no_line(tmp_path: Path) -> None:
    path = str(tmp_path / "d.jsonl")
    stores = [DecisionStore(path, rotate_max_bytes=400) for _ in range(4)]

    def _write(k: int) -> None:
        for i in range(150):
            stores[k]._append_line(json.dumps({"w": k, "i": i}))

    threads = [threading.Thread(target=_write, args=(k,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = [json.loads(x) for x in iter_decision_lines(path)]
    assert len(rows) == 600
    assert {(r["w"], r["i"]) for r in rows} == {(k, i) for k in range(4) for i in range(150)}
    assert len(decision_fragments(path)) >= 2


def test_concurrent_writer_processes_lose_no_line(tmp_path: Path) -> None:
    path = tmp_path / "d.jsonl"
    code = (
        "import json, sys\n"
        "from noise_gate.infrastructure.decision_store import DecisionStore\n"
        "s = DecisionStore(sys.argv[1], rotate_max_bytes=600)\n"
        "for i in range(200):\n"
        "    s._append_line(json.dumps({'w': int(sys.argv[2]), 'i': i}))\n"
    )
    procs = [
        subprocess.Popen([sys.executable, "-c", code, str(path), str(k)], cwd=REPO)
        for k in range(3)
    ]
    for proc in procs:
        assert proc.wait(timeout=60) == 0
    rows = [json.loads(x) for x in iter_decision_lines(path)]
    assert len(rows) == 600
    assert {(r["w"], r["i"]) for r in rows} == {(k, i) for k in range(3) for i in range(200)}
    for frag in decision_fragments(path):
        assert frag.stat().st_size >= 600  # 다른 주체가 막 회전한 새 파일을 다시 회전하지 않았다


# ── 기준점 이후 줄(시나리오 도구) ────────────────────────────────────────────


def test_lines_since_cursor_follow_rotation(tmp_path: Path) -> None:
    path = tmp_path / "d.jsonl"
    assert decision_cursor(path) == (0, 0, 0)
    store = DecisionStore(str(path), rotate_max_bytes=100)
    store._append_line(json.dumps({"i": 0}))
    cursor = decision_cursor(path)
    for i in range(1, 12):
        store._append_line(json.dumps({"i": i, "pad": "q" * 20}))
    assert decision_fragments(path)
    assert [json.loads(x)["i"] for x in iter_decision_lines_since(path, cursor)] == list(
        range(1, 12)
    )
    # 파일이 없던 시점의 기준점이면 전부 새 줄이다
    assert len(list(iter_decision_lines_since(path, (0, 0, 0)))) == 12


def test_lines_since_without_rotation_matches_seek(tmp_path: Path) -> None:
    path = tmp_path / "d.jsonl"
    path.write_text('{"i": 0}\n{"i": 1}\n')
    cursor = decision_cursor(path)
    with path.open("a") as fh:
        fh.write('{"i": 2}\n')
    with path.open(encoding="utf-8") as fh:
        fh.seek(cursor[2])
        legacy = list(fh)
    assert list(iter_decision_lines_since(path, cursor)) == legacy == ['{"i": 2}\n']


# ── 스크립트 ─────────────────────────────────────────────────────────────────


def test_page_audit_read_audit_spans_fragments(tmp_path: Path) -> None:
    from noise_gate.scripts.page_audit import read_audit

    plain, rotated = _pair(tmp_path)
    start, end = datetime.now(UTC) - timedelta(days=1), datetime.now(UTC) + timedelta(hours=1)
    a, b = read_audit(plain.path, start, end), read_audit(rotated.path, start, end)
    assert (a.lines, a.bad_lines, a.first_ts, len(a.decisions), len(a.recurrences),
            len(a.resolutions), dict(a.other_types)) == (
        b.lines, b.bad_lines, b.first_ts, len(b.decisions), len(b.recurrences),
        len(b.resolutions), dict(b.other_types))
    assert [d.rec for d in a.decisions] == [d.rec for d in b.decisions]


def _replay_load(paths: list[str]) -> list[dict[str, Any]]:
    from noise_gate.scripts.replay_noise import expand_decision_inputs, load_jsonl

    return [rec for p in expand_decision_inputs(paths) for rec in load_jsonl(p)]


def test_replay_inputs_span_fragments(tmp_path: Path) -> None:
    plain, rotated = _pair(tmp_path)
    assert _replay_load([str(rotated.path)]) == _replay_load([str(plain.path)])


def test_replay_explicit_fragments_read_once_in_time_order(tmp_path: Path, capsys) -> None:
    """`alarm_decisions.jsonl*` 글롭(현재 파일이 앞)·조각 먼저 둘 다 각 레코드를 한 번 · 시간순."""
    plain, rotated = _pair(tmp_path)
    expected = _replay_load([str(plain.path)])
    frags = [str(f) for f in decision_fragments(rotated.path)]
    for inputs in ([str(rotated.path), *frags], [*frags, str(rotated.path)]):
        assert _replay_load(inputs) == expected
        assert capsys.readouterr().err.count("경고") == 1
    # 조각이 없는 파일은 종전처럼 넘긴 대로 읽는다(같은 파일 두 번이면 두 번)
    assert len(_replay_load([str(plain.path), str(plain.path)])) == 2 * len(expected)
    assert capsys.readouterr().err == ""


def test_replay_in_rotation_gap_reads_fragments_only(tmp_path: Path) -> None:
    plain, rotated = _pair(tmp_path)
    rotated.path.unlink()  # 회전 직후 다음 기록 전
    frags = decision_fragments(rotated.path)
    want = [rec for f in frags for rec in map(json.loads, f.read_text("utf-8").splitlines())]
    assert _replay_load([str(rotated.path)]) == want
    with pytest.raises(FileNotFoundError):  # 조각도 없으면 종전처럼 오류
        _replay_load([str(tmp_path / "none.jsonl")])


def test_cursor_in_rotation_gap_points_to_latest_fragment_end(tmp_path: Path) -> None:
    path = tmp_path / "d.jsonl"
    store = DecisionStore(str(path), rotate_max_bytes=10**6)
    for i in range(5):
        store._append_line(json.dumps({"i": i}))
    store.rotate_max_bytes = 1
    store._rotate_if_due()
    latest = decision_fragments(path)[-1]
    st = latest.stat()
    assert decision_cursor(path) == (st.st_dev, st.st_ino, st.st_size)
    assert decision_cursor(tmp_path / "other.jsonl") == (0, 0, 0)


@pytest.mark.skipif(
    sys.platform.startswith("win") or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="POSIX 권한 비트 · root는 권한을 무시한다",
)
def test_unlistable_folder_reads_current_file_and_warns_once(tmp_path: Path, caplog) -> None:
    folder = tmp_path / "logs"
    path = folder / "d.jsonl"
    folder.mkdir()
    path.write_text("".join(json.dumps({"ts": _iso(5), "tier": "page"}) + "\n" for _ in range(3)))
    os.chmod(folder, 0o300)
    try:
        with caplog.at_level("WARNING", logger=ds.__name__):
            store = DecisionStore(str(path))
            assert store.aggregate(window_seconds=3600)["total"] == 3
            assert store.aggregate(window_seconds=3600)["total"] == 3
        assert sum("조각 목록을 읽지 못해" in r.getMessage() for r in caplog.records) == 1
    finally:
        os.chmod(folder, 0o755)
        ds._LISTING_WARNED.discard(str(folder))


def test_manual_recording_joins_fragments_with_line_boundaries(tmp_path: Path) -> None:
    pytest.importorskip("redis")
    from scripts.manual import alarms

    path = tmp_path / "logs" / "alarm_decisions.jsonl"
    path.parent.mkdir()
    (path.parent / (path.name + ".20260101T000000000000Z")).write_text('{"i": 0}\n{"i": 1}')
    path.write_text('{"i": 2}\n')
    dst = tmp_path / "out.jsonl"
    assert alarms.copy_decisions(path, dst) is True
    assert [json.loads(x)["i"] for x in dst.read_text("utf-8").splitlines()] == [0, 1, 2]
    # 조각이 없으면 종전처럼 그대로 복사한다
    plain = tmp_path / "plain.jsonl"
    plain.write_bytes(b'{"i": 9}')
    assert alarms.copy_decisions(plain, dst) is True
    assert dst.read_bytes() == b'{"i": 9}'
    assert alarms.copy_decisions(tmp_path / "none.jsonl", dst) is False


def test_mock_judge_waits_across_rotation(tmp_path: Path) -> None:
    from noise_gate.scripts.mock_polestar_events import Judge

    path = tmp_path / "d.jsonl"
    store = DecisionStore(str(path), rotate_max_bytes=80)
    store._append_line(json.dumps({"alarm_id": "old", "type": "investigation",
                                   "investigation_id": "inv-old"}))
    judge = Judge(str(path), poll_interval=0.01)
    offset = judge.current_offset()
    store._append_line(json.dumps({"alarm_id": "new", "tier": "page", "pad": "w" * 60}))
    store._append_line(json.dumps({"alarm_id": "other", "tier": "page", "pad": "w" * 60}))
    store._append_line(json.dumps({"alarm_id": "new", "type": "investigation",
                                   "investigation_id": "inv-new"}))
    assert decision_fragments(path)
    assert judge.wait_for("new", offset, 1.0)["tier"] == "page"
    assert judge.wait_for("old", offset, 0.05) is None  # 기준점 앞 줄은 보지 않는다
    assert judge.wait_for_investigation("new", offset, 1.0)["investigation_id"] == "inv-new"
    assert judge.known_investigation_ids() == {"inv-old", "inv-new"}
