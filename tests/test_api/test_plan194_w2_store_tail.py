"""plans/194 W2 — JSONL 끝 N줄 증분 캐시(`jsonl_tail`)와 저장소 2종의 반환 의미 고정.

- 반환 의미가 종전 구현(`deque(텍스트 파일, maxlen)` → strip → json.loads → dict만)과 비트 동일
  (빈 줄·깨진 줄·비-dict·CRLF·홀로 CR·U+2028·끝 개행 없는 줄 포함)
- 증분 append는 새로 붙은 바이트만 읽는다 · 무변화 호출은 본문 0바이트
- 부분 줄(쓰는 중)은 캐시에 넣지 않고 완결되면 한 번만 실린다
- 회전(rename)·truncate·제자리 재작성·삭제 후 재생성 → 처음부터 다시 읽는다
- 두 경로 격리 · 같은 경로 다른 N
- `InvestigationStore`·`DecisionStore`가 캐시를 써도 기록 직후 조회가 새 행을 본다
"""

from __future__ import annotations

import json
import os
import random
import time
from collections import deque
from pathlib import Path
from typing import Any

import pytest

from noise_gate.infrastructure import jsonl_tail
from noise_gate.infrastructure.decision_store import DecisionStore
from noise_gate.infrastructure.investigation_store import InvestigationStore


@pytest.fixture(autouse=True)
def _clear_cache():
    jsonl_tail.clear()
    yield
    jsonl_tail.clear()


def legacy_tail(path: Path, max_lines: int) -> list[dict[str, Any]]:
    """종전 `_tail` 구현(비교 기준)."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        lines = deque(fh, maxlen=max_lines)
    out: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8", newline="") as fh:
        fh.write(text)


def line(i: int, **extra: Any) -> str:
    return json.dumps({"i": i, **extra}, ensure_ascii=False) + "\n"


# ── 반환 의미 비트 동일 ──────────────────────────────────────────────────────


def test_matches_legacy_over_random_appends(tmp_path):  # noqa: ANN001
    path = tmp_path / "a.jsonl"
    rng = random.Random(194)
    pieces = [
        lambda i: line(i),
        lambda i: line(i, text="한글 줄 분리 문자"),
        lambda i: "\n",
        lambda i: "   \n",
        lambda i: "{깨진 줄\n",
        lambda i: "[1, 2]\n",
        lambda i: json.dumps({"i": i}) + "\r\n",
        lambda i: json.dumps({"i": i}) + "\r" + json.dumps({"j": i}) + "\n",
        lambda i: json.dumps({"i": i, "cut": True})[:-3],  # 쓰는 중인 부분 줄
    ]
    for step in range(60):
        chunk = "".join(rng.choice(pieces)(step * 10 + k) for k in range(rng.randint(0, 5)))
        append(path, chunk)
        for n in (1, 3, 7, 50):
            assert jsonl_tail.tail_records(path, n) == legacy_tail(path, n), (step, n)


def test_missing_file_is_empty(tmp_path):  # noqa: ANN001
    assert jsonl_tail.tail_records(tmp_path / "none.jsonl", 10) == []
    assert jsonl_tail.tail_records(tmp_path / "no_dir" / "x.jsonl", 10) == []


def test_complete_json_without_newline_is_returned_like_legacy(tmp_path):  # noqa: ANN001
    path = tmp_path / "a.jsonl"
    path.write_text(line(1) + json.dumps({"i": 2}), encoding="utf-8")
    assert jsonl_tail.tail_records(path, 10) == [{"i": 1}, {"i": 2}] == legacy_tail(path, 10)


# ── 증분 · 부분 줄 · max_lines ────────────────────────────────────────────────


def test_incremental_append_reads_only_new_bytes(tmp_path):  # noqa: ANN001
    path = tmp_path / "a.jsonl"
    path.write_text("".join(line(i) for i in range(5)), encoding="utf-8")
    first = jsonl_tail.tail_records(path, 100)
    assert [r["i"] for r in first] == list(range(5))
    before = jsonl_tail.stats(path)
    assert before is not None and before["bytes_read"] == path.stat().st_size

    jsonl_tail.tail_records(path, 100)  # 무변화 — 본문 0바이트
    assert jsonl_tail.stats(path)["bytes_read"] == before["bytes_read"]

    added = line(5) + line(6)
    append(path, added)
    assert [r["i"] for r in jsonl_tail.tail_records(path, 100)] == list(range(7))
    after = jsonl_tail.stats(path)
    assert after["bytes_read"] - before["bytes_read"] == len(added.encode("utf-8"))
    assert after["reloads"] == 0


def test_partial_line_completed_appears_once(tmp_path):  # noqa: ANN001
    path = tmp_path / "a.jsonl"
    full = json.dumps({"i": 2, "text": "부분"}, ensure_ascii=False)
    path.write_text(line(1) + full[:7], encoding="utf-8")
    assert jsonl_tail.tail_records(path, 10) == [{"i": 1}]
    assert jsonl_tail.stats(path)["offset"] == len(line(1).encode("utf-8"))
    append(path, full[7:] + "\n")
    assert jsonl_tail.tail_records(path, 10) == [{"i": 1}, {"i": 2, "text": "부분"}]
    assert jsonl_tail.tail_records(path, 10) == legacy_tail(path, 10)


def test_max_lines_counts_blank_and_broken_lines(tmp_path):  # noqa: ANN001
    path = tmp_path / "a.jsonl"
    path.write_text(line(1) + "\n" + "{x\n" + line(2) + line(3), encoding="utf-8")
    assert jsonl_tail.tail_records(path, 3) == [{"i": 2}, {"i": 3}] == legacy_tail(path, 3)
    assert jsonl_tail.tail_records(path, 2) == [{"i": 2}, {"i": 3}]
    assert jsonl_tail.tail_records(path, 1) == [{"i": 3}]


def test_same_path_different_max_lines(tmp_path):  # noqa: ANN001
    path = tmp_path / "a.jsonl"
    path.write_text("".join(line(i) for i in range(20)), encoding="utf-8")
    assert [r["i"] for r in jsonl_tail.tail_records(path, 3)] == [17, 18, 19]
    # 큰 N이 오면 다시 읽어 넓힌다 — 그 뒤 작은 N은 끝을 잘라 준다
    assert [r["i"] for r in jsonl_tail.tail_records(path, 10)] == list(range(10, 20))
    assert [r["i"] for r in jsonl_tail.tail_records(path, 3)] == [17, 18, 19]
    append(path, line(20))
    assert [r["i"] for r in jsonl_tail.tail_records(path, 3)] == [18, 19, 20]
    assert [r["i"] for r in jsonl_tail.tail_records(path, 10)] == list(range(11, 21))


def test_cache_keeps_only_max_lines_records(tmp_path):  # noqa: ANN001
    path = tmp_path / "a.jsonl"
    path.write_text("".join(line(i) for i in range(50)), encoding="utf-8")
    jsonl_tail.tail_records(path, 5)
    append(path, "".join(line(i) for i in range(50, 100)))
    jsonl_tail.tail_records(path, 5)
    entry = jsonl_tail._CACHE[os.path.realpath(path)]
    assert len(entry.records) == 5


# ── 파일 교체 ────────────────────────────────────────────────────────────────


def test_rotation_by_rename_reloads(tmp_path):  # noqa: ANN001
    path = tmp_path / "a.jsonl"
    path.write_text(line(1) + line(2), encoding="utf-8")
    jsonl_tail.tail_records(path, 10)
    path.rename(tmp_path / "a.jsonl.1")
    path.write_text(line(9) + line(10) + line(11), encoding="utf-8")  # 새 inode · 더 큼
    assert [r["i"] for r in jsonl_tail.tail_records(path, 10)] == [9, 10, 11]
    assert jsonl_tail.stats(path)["reloads"] == 1


def test_truncate_smaller_reloads(tmp_path):  # noqa: ANN001
    path = tmp_path / "a.jsonl"
    path.write_text("".join(line(i) for i in range(10)), encoding="utf-8")
    jsonl_tail.tail_records(path, 100)
    path.write_text(line(42), encoding="utf-8")  # 같은 inode · 더 작음
    assert jsonl_tail.tail_records(path, 100) == [{"i": 42}]


def test_rewrite_in_place_larger_reloads(tmp_path):  # noqa: ANN001
    path = tmp_path / "a.jsonl"
    path.write_text(line(1, s="a") + line(2, s="a"), encoding="utf-8")
    jsonl_tail.tail_records(path, 100)
    ino = path.stat().st_ino
    path.write_text(line(1, s="b") + line(2, s="b") + line(3, s="b"), encoding="utf-8")
    assert path.stat().st_ino == ino  # 같은 파일을 제자리에서 다시 썼다
    assert [r["s"] for r in jsonl_tail.tail_records(path, 100)] == ["b", "b", "b"]
    assert jsonl_tail.tail_records(path, 100) == legacy_tail(path, 100)


def test_delete_then_recreate(tmp_path):  # noqa: ANN001
    path = tmp_path / "a.jsonl"
    path.write_text(line(1), encoding="utf-8")
    assert jsonl_tail.tail_records(path, 10) == [{"i": 1}]
    path.unlink()
    assert jsonl_tail.tail_records(path, 10) == []
    assert jsonl_tail.stats(path) is None
    path.write_text(line(7) + line(8), encoding="utf-8")
    assert jsonl_tail.tail_records(path, 10) == [{"i": 7}, {"i": 8}]


def test_two_paths_isolated(tmp_path):  # noqa: ANN001
    a, b = tmp_path / "a.jsonl", tmp_path / "b" / "a.jsonl"
    b.parent.mkdir()
    a.write_text(line(1), encoding="utf-8")
    b.write_text(line(2) + line(3), encoding="utf-8")
    assert jsonl_tail.tail_records(a, 10) == [{"i": 1}]
    assert jsonl_tail.tail_records(b, 10) == [{"i": 2}, {"i": 3}]
    append(a, line(4))
    assert jsonl_tail.tail_records(a, 10) == [{"i": 1}, {"i": 4}]
    assert jsonl_tail.tail_records(b, 10) == [{"i": 2}, {"i": 3}]


def test_returned_list_is_fresh(tmp_path):  # noqa: ANN001
    path = tmp_path / "a.jsonl"
    path.write_text(line(1) + line(2), encoding="utf-8")
    rows = jsonl_tail.tail_records(path, 10)
    rows.clear()
    assert len(jsonl_tail.tail_records(path, 10)) == 2


# ── 성능(읽은 바이트 · 느슨한 시간) ──────────────────────────────────────────


def test_second_call_on_large_file_is_cheap(tmp_path):  # noqa: ANN001
    path = tmp_path / "big.jsonl"
    blob = "x" * 400
    path.write_text("".join(line(i, blob=blob) for i in range(10000)), encoding="utf-8")
    t0 = time.perf_counter()
    first = jsonl_tail.tail_records(path, 2000)
    t1 = time.perf_counter()
    read_after_first = jsonl_tail.stats(path)["bytes_read"]
    append(path, line(10000, blob=blob))
    second = jsonl_tail.tail_records(path, 2000)
    t2 = time.perf_counter()
    assert len(first) == len(second) == 2000
    assert second[-1]["i"] == 10000 and second[0]["i"] == 8001
    assert jsonl_tail.stats(path)["bytes_read"] - read_after_first == len(
        line(10000, blob=blob).encode("utf-8")
    )
    assert (t2 - t1) < (t1 - t0)  # 느슨한 단언 — 두 번째는 새 줄 1개만 파싱한다


# ── 저장소 2종 ───────────────────────────────────────────────────────────────


def test_investigation_store_record_then_read_sees_new_row(tmp_path):  # noqa: ANN001
    path = str(tmp_path / "inv.jsonl")
    InvestigationStore(path).record(
        investigation_id="inv-1", alarm_ids=["a1"], status="submitted", source="t"
    )
    assert InvestigationStore(path).get_by_investigation("inv-1")["status"] == "submitted"
    InvestigationStore(path).record(
        investigation_id="inv-1", alarm_ids=["a1"], status="done", source="t"
    )
    assert InvestigationStore(path).get_by_investigation("inv-1")["status"] == "done"
    assert InvestigationStore(path, max_lines=1).list_by_alarm("a1")[0]["status"] == "done"


def test_investigation_store_unreadable_path_is_empty(tmp_path):  # noqa: ANN001
    (tmp_path / "dir.jsonl").mkdir()
    assert InvestigationStore(str(tmp_path / "dir.jsonl")).latest_records() == []


def test_decision_store_tail_matches_legacy_filter(tmp_path):  # noqa: ANN001
    path = tmp_path / "decisions.jsonl"
    rows = [
        {"alarm_id": "a1", "tier": "PAGE", "ts": "2026-10-26T00:00:00+00:00"},
        {"type": "l3_state", "fingerprint": "f"},
        {"alarm_id": "a2", "tier": "SUPPRESS", "ts": "2026-10-26T00:00:01+00:00"},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows) + "{broken\n", encoding="utf-8")
    store = DecisionStore(str(path), True, 3)
    expected = [r for r in legacy_tail(path, 3) if not r.get("type")]
    assert store._tail_records(None) == expected
    assert [r["alarm_id"] for r in expected] == ["a2"]
    append(path, json.dumps({"alarm_id": "a3", "tier": "PAGE"}) + "\n")
    assert store.get_decision("a3") is not None
    assert DecisionStore(str(path), False, 3)._tail_records(None) == []
