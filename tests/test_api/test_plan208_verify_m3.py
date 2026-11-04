"""plans/208 트랙 C M-3 독립 검증 — 판정 파일 역방향 집계 · 회전 · 조각 횡단 읽기.

구현자 테스트(`noise_gate/tests/test_plan208_m3_rotation.py`·`test_plan208_m3_decision_scan.py`)와
겹치지 않는 축만 본다.

1. 플래그 off = 기준 SHA(`afc5626c`)와 비트 동일 — 기준 SHA의 `decision_store.py`를 git에서 꺼내
   별도 모듈로 적재해 같은 파일에서 리더 전부·쓰기 바이트를 직접 대조한다(차등 테스트).
2. 스크립트 기준점 int → (dev, ino, size) — 회전 없음에서 종전 정수 오프셋 읽기와 같다.
3. 다중 프로세스 동시 날짜 회전 — rename 1회(조각 1개)·줄 유실 0.
4. 별도 프로세스 writer가 회전하는 동안의 읽기 — 체인 순회·끝 N줄·커서가 접두 연속(유실·중복 0).
5. 부분 순회 뒤 파일 디스크립터 누수 없음.
6. 확인된 결함 재현(xfail strict) — 회전 틈 커서 · 읽기 권한 없는 상위 폴더에서 off 회귀.
"""

from __future__ import annotations

import importlib.util
import json
import os
import random
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from noise_gate.domain.notification_policy import NotificationDecision
from noise_gate.infrastructure import jsonl_tail
from noise_gate.infrastructure.decision_store import (
    DecisionStore,
    decision_cursor,
    decision_fragments,
    iter_decision_lines,
    iter_decision_lines_since,
)

REPO = Path(__file__).resolve().parents[2]
BASE_SHA = "afc5626c"


@pytest.fixture(autouse=True)
def _clean_tail_cache():
    jsonl_tail.clear()
    yield
    jsonl_tail.clear()


@pytest.fixture(scope="module")
def base_mod(tmp_path_factory: pytest.TempPathFactory) -> ModuleType:
    """기준 SHA의 `decision_store.py`를 별도 이름 모듈로 적재한다(없으면 skip)."""
    try:
        src = subprocess.run(
            ["git", "show", f"{BASE_SHA}:noise_gate/infrastructure/decision_store.py"],
            cwd=REPO, capture_output=True, text=True, check=True, timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip("기준 SHA를 git에서 꺼낼 수 없다")
    path = tmp_path_factory.mktemp("base") / "decision_store_base.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("decision_store_base_m3", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _iso(seconds_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat()


def _mixed_lines(seed: int) -> list[str]:
    """창 경계에서 7초 이상 떨어진 시각의 혼합 레코드 줄 — 손상·빈 줄·시각 없음 포함."""
    rng = random.Random(seed)
    tiers = ("page", "ticket", "dashboard", "suppress")
    lines: list[str] = []
    n = 400
    for i in range(n):
        ago = (n - i) * 300 + 7  # 300초 간격 → 창 하한과 항상 7초 이상 떨어진다
        rec: dict[str, Any] = {
            "ts": _iso(ago),
            "alarm_id": f"a{i}",
            "tier": rng.choice(tiers),
            "reason": f"사유{i}",
            "fingerprint": f"fp{rng.randrange(9)}",
            "signals": {"severity": rng.randrange(1, 4)},
            "alarm_name": f"알람{rng.randrange(6)}",
            "server_name": f"srv{rng.randrange(4)}",
            "stage": rng.choice(("flapping", "correlation", "self_heal", "severity3", "")),
        }
        if rng.random() < 0.1:
            rec["stage_evidence"] = {"collection_failed": True}
        lines.append(json.dumps(rec, ensure_ascii=False))
        roll = rng.random()
        if roll < 0.08:
            lines.append(json.dumps({"type": "resolution", "fingerprint": rec["fingerprint"],
                                     "duration_seconds": rng.randrange(10, 900), "ts": _iso(ago)}))
        elif roll < 0.14:
            lines.append(json.dumps({"type": "recurrence", "fingerprint": rec["fingerprint"],
                                     "count": 2, "alarm_id": rec["alarm_id"], "ts": _iso(ago)}))
        elif roll < 0.18:
            lines.append(json.dumps({"type": "l3_state", "fingerprint": "fpL",
                                     "state_fingerprint": {"v": i}, "ts": _iso(ago)}))
        elif roll < 0.20:
            lines.append("{broken json")
        elif roll < 0.22:
            lines.append("")
        elif roll < 0.23:
            lines.append(json.dumps({"tier": "page", "alarm_id": f"nots{i}", "reason": "x"}))
    return lines


def _write(path: Path, lines: list[str], *, crlf_tail: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(line + "\n" for line in lines)
    if crlf_tail:
        body += json.dumps({"ts": _iso(5), "tier": "page", "alarm_id": "crlf"}) + "\r\n"
    path.write_bytes(body.encode("utf-8"))


def _strip_age(agg: dict[str, Any]) -> dict[str, Any]:
    out = dict(agg)
    out.pop("last_event_age_seconds", None)
    return out


def _snapshot(store: Any) -> dict[str, Any]:
    """기준·현재 양쪽에 있는 공개 리더 결과 묶음(시각 경과에 흔들리는 나이 값은 뺀다)."""
    return {
        "agg_none": _strip_age(store.aggregate()),
        "agg_1h": _strip_age(store.aggregate(window_seconds=3600)),
        "agg_1d": _strip_age(store.aggregate(window_seconds=86400)),
        "meta": store.meta_alerts(window_seconds=3600, suppress_ratio_threshold=0.2,
                                  min_events=3),
        "meta_cf": store.meta_alerts(window_seconds=7200, suppress_ratio_threshold=0.9,
                                     min_events=1, collection_failed_ratio_threshold=0.05),
        "funnel": store.funnel(window_seconds=7200),
        "funnel_all": store.funnel(),
        "top": store.top_suppressed(window_seconds=86400, limit=5),
        "list": store.list_decisions(window_seconds=86400, page=1, size=30),
        "get": store.get_decision("a399"),
        "get_old": store.get_decision("a10"),
        "recent": store.recent_decisions(7200),
        "recent_rec": store.recent_decisions(7200, include_recurrence=True),
        "count": store.count_recent_values(86400, "tier"),
        "iter": list(store.iter_recent_decisions(3600)),
        "l3": store.last_l3_state("fpL"),
        "l3_none": store.last_l3_state("nope"),
    }


# ── 1. off = 기준 SHA와 비트 동일 ──────────────────────────────────────────────


@pytest.mark.parametrize("seed", [1, 7, 42])
@pytest.mark.parametrize("max_lines", [50, 20000])
def test_off_readers_match_base_sha(
    tmp_path: Path, base_mod: ModuleType, seed: int, max_lines: int
) -> None:
    path = tmp_path / "logs" / "alarm_decisions.jsonl"
    _write(path, _mixed_lines(seed), crlf_tail=True)
    base = base_mod.DecisionStore(str(path), max_lines=max_lines)
    jsonl_tail.clear()
    expected = _snapshot(base)
    jsonl_tail.clear()
    new = DecisionStore(str(path), max_lines=max_lines)  # 세 플래그 기본 off
    got = _snapshot(new)
    assert got == expected
    assert decision_fragments(path) == []


def test_off_readers_match_base_when_file_absent(tmp_path: Path, base_mod: ModuleType) -> None:
    path = tmp_path / "none" / "alarm_decisions.jsonl"
    assert _snapshot(DecisionStore(str(path))) == _snapshot(base_mod.DecisionStore(str(path)))


def test_off_non_object_json_line_behaves_like_base(tmp_path: Path, base_mod: ModuleType) -> None:
    """JSON 배열 줄 — 종전 forward 순회는 AttributeError를 던졌다(off에서도 같아야 한다)."""
    path = tmp_path / "d.jsonl"
    _write(path, [json.dumps({"ts": _iso(10), "tier": "page"}), "[1, 2]"])

    def _outcome(store: Any) -> Any:
        try:
            return _strip_age(store.aggregate(window_seconds=3600))
        except Exception as exc:  # noqa: BLE001 — 예외 종류 대조
            return type(exc).__name__

    assert _outcome(DecisionStore(str(path))) == _outcome(base_mod.DecisionStore(str(path)))


def test_off_write_bytes_match_base_sha(tmp_path: Path, base_mod: ModuleType) -> None:
    ts = datetime(2026, 11, 2, 1, 2, 3, 456789, tzinfo=UTC)
    decision = NotificationDecision(
        tier="page", reason="사유 — 한글", priority=3, signals={"severity": 1},
        fingerprint="fp1", stage="severity3",
    )

    def _drive(store: Any) -> None:
        store.record(decision, alarm_id="a1", alarm_name="알람", server_name="srv", ts=ts,
                     recurrence={"count": 2}, stage_evidence={"k": "v"}, db_id="polestar",
                     resource_name="cpu", condition_log="x" * 300, hostname="h")
        store.record_resolution(fingerprint="fp1", duration_seconds=12.5, ts=ts)
        store.record_recurrence(fingerprint="fp1", count=3, first_seen_ts=1.0, alarm_id="a1",
                                ts=ts, annotation={"a": 1})
        store.record_investigation(alarm_id="a1", fingerprint="fp1", investigation_id="i1",
                                   status="done", verdict="ok", ts=ts, episode_id="e1")
        store.record_late_promotion(alarm_id="a1", fingerprint="fp1", reason="late",
                                    sent={"mail": True}, ts=ts)
        store.record_l3_state(fingerprint="fp1", alarm_id="a1", host="h", kind="cpu",
                              state_fingerprint={"s": 1}, transition="new", sent=True,
                              commands=["uptime"], ts=ts)

    base_path = tmp_path / "base" / "d.jsonl"
    new_path = tmp_path / "new" / "d.jsonl"
    _drive(base_mod.DecisionStore(str(base_path)))
    _drive(DecisionStore(str(new_path)))
    assert new_path.read_bytes() == base_path.read_bytes()
    assert sorted(os.listdir(new_path.parent)) == ["d.jsonl"]


# ── 2. 스크립트 기준점(int → tuple) — 회전 없음에서 종전 정수 오프셋과 같다 ─────────────


def _legacy_lines_since(path: Path, offset: int) -> list[str]:
    """기준 SHA의 Judge.wait_for·wait_decision 읽기 — 있으면 열어 `seek(offset)` 뒤 줄 전부."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        fh.seek(offset)
        return list(fh)


def test_tuple_cursor_matches_legacy_int_offset_without_rotation(tmp_path: Path) -> None:
    path = tmp_path / "d.jsonl"
    # 파일이 없을 때 잡은 기준점 → 나중에 생긴 파일 전체가 새 줄(종전 offset 0과 같다)
    cur0 = decision_cursor(path)
    legacy0 = path.stat().st_size if path.exists() else 0
    store = DecisionStore(str(path))
    for i in range(3):
        store._append_line(json.dumps({"alarm_id": f"x{i}", "tier": "page"}))
    assert list(iter_decision_lines_since(path, cur0)) == _legacy_lines_since(path, legacy0)
    # 파일이 있을 때 잡은 기준점 → 뒤에 붙은 줄만
    cur1 = decision_cursor(path)
    legacy1 = path.stat().st_size
    for i in range(3, 6):
        store._append_line(json.dumps({"alarm_id": f"x{i}", "tier": "page"}, ensure_ascii=False))
    assert list(iter_decision_lines_since(path, cur1)) == _legacy_lines_since(path, legacy1)
    # 정수 기준점(종전 호출 형태)도 같은 결과
    assert list(iter_decision_lines_since(path, legacy1)) == _legacy_lines_since(path, legacy1)


def test_scripts_offset_consumers_accept_new_cursor(tmp_path: Path) -> None:
    """반환형이 바뀐 두 함수의 소비처가 off(회전 없음)에서 같은 레코드를 찾는다."""
    from noise_gate.scripts import noise_gate_scenario_test as ngst
    from noise_gate.scripts.mock_polestar_events import Judge

    path = tmp_path / "d.jsonl"
    store = DecisionStore(str(path))
    store._append_line(json.dumps({"alarm_id": "old", "tier": "ticket"}))
    judge = Judge(str(path), poll_interval=0.01)
    off = judge.current_offset()
    ctx = {"decision_log": str(path)}
    off2 = ngst.log_offset(ctx)
    assert off == off2 == decision_cursor(path)
    store._append_line(json.dumps({"alarm_id": "new", "tier": "page"}))
    store._append_line(json.dumps({"type": "investigation", "alarm_id": "new",
                                   "investigation_id": "inv1"}))
    assert judge.wait_for("new", off, 1.0)["tier"] == "page"
    assert judge.wait_for("old", off, 0.05) is None
    assert judge.wait_for_investigation("new", off, 1.0)["investigation_id"] == "inv1"
    assert ngst.wait_decision(ctx, "new", off2, 1.0)["tier"] == "page"
    # 종전 정수 오프셋 호출도 그대로 동작한다(테스트·외부 호출 호환)
    assert judge.wait_for("old", 0, 0.5)["tier"] == "ticket"


# ── 3. 다중 프로세스 동시 날짜 회전 — rename 1회 ──────────────────────────────────


@pytest.mark.skipif(sys.platform.startswith("win"), reason="fcntl 전용")
def test_concurrent_processes_daily_rotation_renames_once(tmp_path: Path) -> None:
    path = tmp_path / "d.jsonl"
    old = [json.dumps({"old": i}) for i in range(20)]
    _write(path, old)
    yesterday = time.time() - 2 * 86400
    os.utime(path, (yesterday, yesterday))
    start_at = time.time() + 1.5
    code = (
        "import json, sys, time\n"
        "from noise_gate.infrastructure.decision_store import DecisionStore\n"
        "s = DecisionStore(sys.argv[1], rotate_daily=True)\n"
        "t = float(sys.argv[3])\n"
        "while time.time() < t:\n"
        "    pass\n"
        "s._append_line(json.dumps({'w': int(sys.argv[2])}))\n"
    )
    n = 6
    procs = [
        subprocess.Popen([sys.executable, "-c", code, str(path), str(k), str(start_at)],
                         cwd=REPO)
        for k in range(n)
    ]
    for proc in procs:
        assert proc.wait(timeout=60) == 0
    frags = decision_fragments(path)
    assert len(frags) == 1, frags  # 같은 날짜 조건을 여럿이 봐도 회전은 1회
    assert frags[0].read_text(encoding="utf-8") == "".join(x + "\n" for x in old)
    current = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert sorted(r["w"] for r in current) == list(range(n))


# ── 4. 별도 프로세스 writer가 회전하는 동안의 읽기 ─────────────────────────────────


def _ids(lines: list[str]) -> list[int]:
    out: list[int] = []
    for i, line in enumerate(lines):
        try:
            out.append(int(json.loads(line)["i"]))
        except (json.JSONDecodeError, KeyError):
            # 쓰는 중인 마지막 줄(개행 없음)만 허용한다
            assert i == len(lines) - 1 and not line.endswith("\n"), line
    return out


@pytest.mark.skipif(sys.platform.startswith("win"), reason="fcntl 전용")
def test_reads_during_rotating_writer_are_contiguous_prefix(tmp_path: Path) -> None:
    path = tmp_path / "d.jsonl"
    total = 4000
    code = (
        "import json, sys\n"
        "from noise_gate.infrastructure.decision_store import DecisionStore\n"
        "s = DecisionStore(sys.argv[1], rotate_max_bytes=3000)\n"
        "for i in range(int(sys.argv[2])):\n"
        "    s._append_line(json.dumps({'i': i, 'pad': 'p' * 40}))\n"
    )
    store = DecisionStore(str(path), max_lines=120)
    start_cursor = decision_cursor(path)
    proc = subprocess.Popen([sys.executable, "-c", code, str(path), str(total)], cwd=REPO)
    last_len = 0
    rounds = 0
    try:
        while proc.poll() is None or rounds < 3:
            rounds += 1
            ids = _ids(list(iter_decision_lines(path)))
            assert ids == list(range(len(ids)))  # 접두 연속 — 유실·중복 0
            assert len(ids) >= last_len  # 회전과 겹쳐도 뒤로 가지 않는다
            last_len = len(ids)
            tail = [int(r["i"]) for r in store._tail_chain()]
            if tail:
                assert tail == list(range(tail[0], tail[0] + len(tail)))
                assert len(tail) <= 120
            if proc.poll() is not None and rounds >= 3:
                break
    finally:
        proc.wait(timeout=60)
    assert proc.returncode == 0
    assert len(decision_fragments(path)) >= 10
    final = _ids(list(iter_decision_lines(path)))
    assert final == list(range(total))
    assert _ids(list(iter_decision_lines_since(path, start_cursor))) == list(range(total))
    tail = [int(r["i"]) for r in store._tail_chain()]
    assert tail == list(range(total - 120, total))


@pytest.mark.skipif(sys.platform.startswith("win"), reason="fcntl 전용")
def test_cursor_mid_stream_follows_many_rotations(tmp_path: Path) -> None:
    path = tmp_path / "d.jsonl"
    store = DecisionStore(str(path), rotate_max_bytes=200)
    for i in range(30):
        store._append_line(json.dumps({"i": i}))
    cursor = decision_cursor(path)
    for i in range(30, 400):
        store._append_line(json.dumps({"i": i}))
    assert len(decision_fragments(path)) >= 15
    assert _ids(list(iter_decision_lines_since(path, cursor))) == list(range(30, 400))


# ── 5. 파일 디스크립터 누수 ───────────────────────────────────────────────────


def _open_fds() -> int | None:
    for d in ("/dev/fd", "/proc/self/fd"):
        try:
            return len(os.listdir(d))
        except OSError:
            continue
    return None


def test_partial_and_stopped_scans_do_not_leak_fds(tmp_path: Path) -> None:
    if _open_fds() is None:
        pytest.skip("fd 목록을 볼 수 없는 플랫폼")
    path = tmp_path / "d.jsonl"
    store = DecisionStore(str(path), recent_scan=True, rotate_max_bytes=2000, max_lines=30)
    for i in range(600):
        store._append_line(json.dumps({"ts": _iso(600 - i), "tier": "page", "alarm_id": f"a{i}"}))
    assert len(decision_fragments(path)) > 5
    before = _open_fds()
    for _ in range(50):
        gen = store.iter_recent_decisions(3600)
        next(gen)
        del gen  # 소비처가 중간에 버린 순회
        store.aggregate(window_seconds=60)  # 창 밖에서 일찍 멈추는 역방향 집계
        store.last_l3_state("absent")  # 조각 전부를 훑는 경로
        list(iter_decision_lines_since(path, (0, 1, 0)))
        store.funnel(window_seconds=3600)
    calls = 0

    def _stop() -> bool:
        nonlocal calls
        calls += 1
        return True

    for _ in range(20):
        list(store.iter_recent_decisions(10**6, should_stop=_stop))
    assert _open_fds() == before


# ── 6. 확인된 결함 재현 (xfail strict — 고쳐지면 XPASS로 알린다) ─────────────────────


def test_cursor_taken_in_rotation_gap_does_not_reread_history(tmp_path: Path) -> None:
    path = tmp_path / "d.jsonl"
    store = DecisionStore(str(path), rotate_max_bytes=10**6)
    for i in range(10):
        store._append_line(json.dumps({"i": i}))
    store.rotate_max_bytes = 1
    store._rotate_if_due()  # 회전만 일어나고 다음 append 전(현재 파일 없음)
    assert not path.exists() and decision_fragments(path)
    cursor = decision_cursor(path)
    store._append_line(json.dumps({"i": "new"}))
    assert [json.loads(x)["i"] for x in iter_decision_lines_since(path, cursor)] == ["new"]


@pytest.mark.skipif(
    sys.platform.startswith("win") or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="POSIX 권한 비트 · root는 권한을 무시한다",
)
def test_off_aggregate_with_unlistable_parent_matches_base(
    tmp_path: Path, base_mod: ModuleType
) -> None:
    folder = tmp_path / "logs"
    path = folder / "d.jsonl"
    _write(path, [json.dumps({"ts": _iso(10 * i + 5), "tier": "page"}) for i in range(5)])
    os.chmod(folder, 0o300)
    try:
        expected = _strip_age(base_mod.DecisionStore(str(path)).aggregate(window_seconds=3600))
        got = _strip_age(DecisionStore(str(path)).aggregate(window_seconds=3600))
    finally:
        os.chmod(folder, 0o755)
    assert expected["total"] == 5
    assert got == expected


def test_replay_inputs_with_explicit_fragments_count_each_record_once(tmp_path: Path) -> None:
    from noise_gate.scripts.replay_noise import load_jsonl

    path = tmp_path / "alarm_decisions.jsonl"
    store = DecisionStore(str(path), rotate_max_bytes=60)
    for i in range(10):
        store._append_line(json.dumps({"i": i, "tier": "page"}))
    inputs = [*decision_fragments(path), path]  # 셸 글롭 `alarm_decisions.jsonl*`과 같은 목록
    assert len(inputs) > 1
    loaded = [rec for p in inputs for rec in load_jsonl(str(p))]
    assert sorted(r["i"] for r in loaded) == list(range(10))
