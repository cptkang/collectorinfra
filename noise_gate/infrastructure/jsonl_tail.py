"""JSONL 파일 끝 N줄의 증분 읽기 캐시 (plans/194 W2).

append 전용 JSONL 저장소(조사 저장소·결정 감사 기록)의 조회는 「파일 끝 `max_lines`줄을 읽어
레코드로 푼다」를 요청마다 되풀이했다 — 파일 전체를 순회하고 끝 N줄을 매번 다시 파싱했다.
여기서는 **경로 단위**로 마지막으로 읽은 바이트 위치와 파싱한 끝 N줄을 프로세스 안에 남기고,
다음 호출은 그 뒤에 붙은 바이트만 읽는다. 저장소 인스턴스를 요청마다 새로 만들어도 캐시가 산다.

반환 의미는 종전 `deque(텍스트 파일, maxlen=N)` → 줄마다 `strip` → `json.loads` → dict만 남김과
같다 — 순서(오래된 것 → 최신)·N줄 상한(빈 줄·깨진 줄도 한 줄로 센다)·깨진 줄 건너뜀·파일 없음
`[]`. 줄 나눔은 종전 텍스트 모드와 같은 universal newline이다.

- 끝의 개행 없는 줄(쓰는 중일 수 있다)은 캐시에 넣지 않고 호출마다 다시 읽어 결과에만 싣는다.
- 파일이 바뀌었으면(inode·장치 변경, 크기 < 읽은 위치, 파일 머리·읽은 끝부분 바이트 불일치 —
  회전·삭제 후 재생성·제자리 재작성) 처음부터 다시 읽는다.
- 같은 경로를 다른 N으로 읽으면 캐시는 큰 N을 보존하고 작은 N은 그 끝을 잘라 준다(레코드 중복 0).
- 반환 리스트는 매번 새 것이지만 **레코드 dict는 캐시와 공유한다** — 호출부는 레코드를 고치지 말고
  사본을 만들어 쓴다(파싱 비용을 아끼려는 것이라 깊은 복사를 하지 않는다).

계층: noise_gate infrastructure(stdlib 전용). 스레드 안전(모듈 잠금 1개).
"""

from __future__ import annotations

import io
import json
import os
import threading
from collections import deque
from dataclasses import dataclass
from itertools import islice
from typing import IO, Any

#: 파일 동일성 지문 길이(바이트) — 파일 머리와 이미 읽은 끝부분을 이만큼 대조한다.
ANCHOR_BYTES = 4096


@dataclass
class _Entry:
    """경로 1개의 캐시 상태."""

    dev: int
    ino: int
    maxlen: int
    #: 완결된 줄의 파싱 결과(dict 아니면 None — 빈 줄·깨진 줄도 한 줄로 센다).
    records: deque[dict[str, Any] | None]
    #: 완결된 줄 끝(다음 읽기 시작) 바이트 위치.
    offset: int = 0
    head: bytes = b""
    anchor: bytes = b""
    bytes_read: int = 0
    reloads: int = 0


_CACHE: dict[str, _Entry] = {}
_LOCK = threading.Lock()


def _parse(chunk: bytes) -> list[dict[str, Any] | None]:
    """바이트 덩어리를 종전 텍스트 모드와 같은 규칙으로 줄 단위 파싱한다."""
    if not chunk:
        return []
    out: list[dict[str, Any] | None] = []
    for line in io.TextIOWrapper(io.BytesIO(chunk), encoding="utf-8"):
        line = line.strip()
        if not line:
            out.append(None)
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            out.append(None)
            continue
        out.append(rec if isinstance(rec, dict) else None)
    return out


def _reusable(entry: _Entry, st: os.stat_result, fh: IO[bytes], max_lines: int) -> bool:
    """캐시를 이어 쓸 수 있는가 — 같은 파일이고 이미 읽은 부분이 그대로인가."""
    if max_lines > entry.maxlen or (st.st_dev, st.st_ino) != (entry.dev, entry.ino):
        return False
    if st.st_size < entry.offset:
        return False
    fh.seek(0)
    if fh.read(len(entry.head)) != entry.head:
        return False
    fh.seek(entry.offset - len(entry.anchor))
    return fh.read(len(entry.anchor)) == entry.anchor


def _consume(fh: IO[bytes], entry: _Entry) -> bytes:
    """`entry.offset`부터 끝까지 읽어 완결된 줄을 캐시에 더하고, 끝의 미완결 줄을 돌려준다."""
    start = entry.offset
    fh.seek(start)
    raw = deque(fh, maxlen=entry.maxlen + 1)
    end = fh.tell()
    partial = raw.pop() if raw and not raw[-1].endswith(b"\n") else b""
    parsed = _parse(b"".join(raw))
    entry.records.extend(parsed)
    entry.bytes_read += end - start
    entry.offset = end - len(partial)
    if entry.offset != start:
        fh.seek(0)
        entry.head = fh.read(min(entry.offset, ANCHOR_BYTES))
        anchor_start = max(0, entry.offset - ANCHOR_BYTES)
        fh.seek(anchor_start)
        entry.anchor = fh.read(entry.offset - anchor_start)
    return partial


def tail_records(path: str | os.PathLike[str], max_lines: int) -> list[dict[str, Any]]:
    """파일 끝 `max_lines`줄의 JSON 객체 레코드(오래된 것 → 최신 · 파일 없음 `[]`).

    Raises:
        OSError: 파일은 있으나 읽지 못함(호출부가 사유를 남기고 빈 목록으로 처리한다).
    """
    key = os.path.realpath(path)
    n = max(1, int(max_lines))
    with _LOCK:
        try:
            st = os.stat(key)
        except (FileNotFoundError, NotADirectoryError):
            _CACHE.pop(key, None)
            return []
        entry = _CACHE.get(key)
        with open(key, "rb") as fh:
            if entry is None or not _reusable(entry, st, fh, n):
                maxlen = max(n, entry.maxlen) if entry is not None else n
                entry = _Entry(
                    dev=st.st_dev,
                    ino=st.st_ino,
                    maxlen=maxlen,
                    records=deque(maxlen=maxlen),
                    bytes_read=entry.bytes_read if entry is not None else 0,
                    reloads=(entry.reloads + 1) if entry is not None else 0,
                )
                _CACHE.pop(key, None)
            partial = _consume(fh, entry)
        _CACHE[key] = entry
        tail = _parse(partial)[-n:]
        keep = n - len(tail)
        rows = list(islice(entry.records, max(0, len(entry.records) - keep), None)) if keep else []
    rows.extend(tail)
    return [rec for rec in rows if rec is not None]


def stats(path: str | os.PathLike[str]) -> dict[str, int] | None:
    """경로 캐시의 계측값(`offset`·`bytes_read` 누적·`reloads` 재적재 횟수 · 없으면 None)."""
    with _LOCK:
        entry = _CACHE.get(os.path.realpath(path))
        if entry is None:
            return None
        return {"offset": entry.offset, "bytes_read": entry.bytes_read, "reloads": entry.reloads}


def clear() -> None:
    """캐시를 모두 비운다(테스트 격리용)."""
    with _LOCK:
        _CACHE.clear()


__all__ = ["ANCHOR_BYTES", "clear", "stats", "tail_records"]
