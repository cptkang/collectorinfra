"""plans/194 W2 독립 검증 — `jsonl_tail.tail_records`가 종전 `deque`+`json.loads`와 같은가.

무작위 연산(완결 줄·부분 줄·빈 줄·깨진 줄·dict 아닌 JSON·CRLF·한글 · 같은 경로를 여러 N으로 읽기 ·
제자리 절단 재작성 · 삭제 후 재생성)마다 종전 구현과 결과를 대조한다. 동시 쓰기 중 읽기(부분 줄)도
본다.
"""

from __future__ import annotations

import json
import os
import random
import threading
from collections import deque
from pathlib import Path
from typing import Any

import pytest

from noise_gate.infrastructure import jsonl_tail


def _old_tail(path: Path, n: int) -> list[dict[str, Any]]:
    """종전 `InvestigationStore._tail` 본문(2f64aa9c) — 대조 기준."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        lines = deque(fh, maxlen=n)
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


@pytest.fixture(autouse=True)
def _clean_cache():
    jsonl_tail.clear()
    yield
    jsonl_tail.clear()


def _line(rng: random.Random, i: int) -> str:
    roll = rng.random()
    if roll < 0.08:
        return ""
    if roll < 0.12:
        return "   "
    if roll < 0.18:
        return '{"broken": '
    if roll < 0.22:
        return json.dumps([i, "list"])
    if roll < 0.25:
        return "42"
    return json.dumps({"i": i, "text": "조사 " * rng.randrange(4), "pad": "x" * rng.randrange(50)},
                      ensure_ascii=rng.random() < 0.5)


@pytest.mark.parametrize("seed", range(12))
def test_random_ops_match_old_semantics(tmp_path: Path, seed: int) -> None:
    rng = random.Random(seed)
    path = tmp_path / "s.jsonl"
    pending = ""  # 쓰는 중인 부분 줄
    counter = 0
    for step in range(300):
        op = rng.random()
        if op < 0.55:
            nl = "\r\n" if rng.random() < 0.1 else "\n"
            with path.open("a", encoding="utf-8", newline="") as fh:
                for _ in range(rng.randrange(1, 6)):
                    fh.write(pending + _line(rng, counter) + nl)
                    pending = ""
                    counter += 1
        elif op < 0.68:
            text = _line(rng, counter)
            cut = rng.randrange(len(text) + 1) if text else 0
            piece = text[:cut]
            with path.open("a", encoding="utf-8", newline="") as fh:
                fh.write(piece)
            pending = text[cut:]  # 다음 완결 쓰기에서 나머지를 붙인다(대조는 같은 바이트로 한다)
        elif op < 0.72 and path.exists():
            # 제자리 절단 뒤 재작성(같은 inode)
            keep = rng.randrange(3)
            body = "".join(_line(rng, counter + k) + "\n" for k in range(keep))
            counter += keep
            with path.open("w", encoding="utf-8", newline="") as fh:
                fh.write(body)
            pending = ""
        elif op < 0.75 and path.exists():
            os.remove(path)
            pending = ""
        n = rng.choice([1, 2, 3, 7, 20, 200])
        got = jsonl_tail.tail_records(path, n)
        want = _old_tail(path, n)
        assert got == want, f"seed={seed} step={step} n={n}"


def test_concurrent_writer_partial_lines_never_leak_garbage(tmp_path: Path) -> None:
    """쓰는 중 읽어도 결과는 종전 의미의 부분집합 꼴(완결 dict만) · 끝나면 종전과 같다."""
    path = tmp_path / "w.jsonl"
    path.write_text("")
    stop = threading.Event()
    errors: list[BaseException] = []

    def writer() -> None:
        with path.open("a", encoding="utf-8") as fh:
            for i in range(3000):
                rec = json.dumps({"i": i, "v": "가" * (i % 7)}, ensure_ascii=True)
                half = len(rec) // 2
                fh.write(rec[:half])
                fh.flush()
                fh.write(rec[half:] + "\n")
                fh.flush()
        stop.set()

    def reader() -> None:
        last = -1
        try:
            while not stop.is_set():
                rows = jsonl_tail.tail_records(path, 50)
                assert all(isinstance(r, dict) and "i" in r for r in rows)
                idx = [r["i"] for r in rows]
                assert idx == sorted(idx)
                if idx:
                    assert idx[-1] >= last, "되돌아가지 않는다"
                    last = idx[-1]
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t_w = threading.Thread(target=writer)
    readers = [threading.Thread(target=reader) for _ in range(3)]
    for t in readers:
        t.start()
    t_w.start()
    t_w.join(60)
    for t in readers:
        t.join(10)
    assert not errors, errors
    assert jsonl_tail.tail_records(path, 50) == _old_tail(path, 50)


def test_truncated_multibyte_partial_raises_like_old(tmp_path: Path) -> None:
    """끝 부분 줄이 UTF-8 문자 중간에서 잘렸을 때 — 종전과 같은 예외 계열인지(동작 기록)."""
    path = tmp_path / "m.jsonl"
    data = (json.dumps({"a": 1}) + "\n").encode() + '{"b": "가'.encode()[:-1]
    path.write_bytes(data)
    with pytest.raises(UnicodeDecodeError):
        _old_tail(path, 10)
    with pytest.raises(UnicodeDecodeError):
        jsonl_tail.tail_records(path, 10)
