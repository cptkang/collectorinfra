"""큰 JSON 응답 파일의 점진 디코드 (plans/134 W0-B N-18 · SPEC-apm-question-coverage §3.5).

메모리 임계를 넘어 임시 파일로 받은 응답 본문을 원문 전체를 메모리에 올리지 않고 읽는다. 최상위가
객체이면 키마다 값을 읽되 **배열 값은 원소 단위로**(`{"result": [...]}`), 최상위가 배열이면 그
원소 단위로 `json.JSONDecoder.raw_decode`한다(표준 라이브러리만 — 외부 패키지 없음). 그 밖 모양은
값 하나를 통째로 디코드한다. 파싱 결과(원소 목록)는 메모리에 둔다 — 아끼는 것은 원문 텍스트
사본이다.

형식 오류는 `ValueError`(호출자가 `apm_api_error`로 바꾼다).
"""

from __future__ import annotations

import codecs
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO

_DECODER = json.JSONDecoder()
_WS = re.compile(r"[ \t\n\r]*")
CHUNK_BYTES = 1 << 20


class _Reader:
    def __init__(self, fh: BinaryIO, encoding: str, chunk_bytes: int) -> None:
        self._fh = fh
        self._decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
        self._chunk = chunk_bytes
        self.buf = ""
        self.pos = 0
        self.eof = False

    def fill(self, *, grow: bool = False) -> bool:
        """조각을 더 읽는다(읽은 앞부분은 버린다). 더 읽을 것이 없으면 False."""
        if self.eof:
            return False
        size = max(self._chunk, len(self.buf) - self.pos) if grow else self._chunk
        data = self._fh.read(size)
        rest = self.buf[self.pos :]
        self.pos = 0
        if not data:
            self.eof = True
            self.buf = rest + self._decoder.decode(b"", final=True)
            return True
        self.buf = rest + self._decoder.decode(data)
        return True

    def peek(self) -> str:
        while True:
            m = _WS.match(self.buf, self.pos)
            self.pos = m.end() if m else self.pos
            if self.pos < len(self.buf):
                return self.buf[self.pos]
            if not self.fill():
                return ""

    def expect(self, char: str) -> None:
        if self.peek() != char:
            raise ValueError(f"JSON 형식 오류: {char!r} 기대")
        self.pos += 1

    def value(self) -> Any:
        if not self.peek():
            raise ValueError("JSON 형식 오류: 값 없음")
        while True:
            try:
                obj, end = _DECODER.raw_decode(self.buf, self.pos)
            except json.JSONDecodeError:
                if self.fill(grow=True):
                    continue
                raise
            if end == len(self.buf) and not self.eof:
                # 숫자·리터럴이 조각 경계에서 끊겼을 수 있다 — 더 읽고 다시 디코드한다.
                self.fill(grow=True)
                continue
            self.pos = end
            return obj


def _array(reader: _Reader) -> Iterator[Any]:
    reader.expect("[")
    if reader.peek() == "]":
        reader.pos += 1
        return
    while True:
        yield reader.value()
        sep = reader.peek()
        reader.pos += 1
        if sep == "]":
            return
        if sep != ",":
            raise ValueError("JSON 형식 오류: 배열 구분자")


def _object(reader: _Reader) -> dict[str, Any]:
    reader.expect("{")
    out: dict[str, Any] = {}
    if reader.peek() == "}":
        reader.pos += 1
        return out
    while True:
        key = reader.value()
        if not isinstance(key, str):
            raise ValueError("JSON 형식 오류: 객체 키")
        reader.expect(":")
        out[key] = list(_array(reader)) if reader.peek() == "[" else reader.value()
        sep = reader.peek()
        reader.pos += 1
        if sep == "}":
            return out
        if sep != ",":
            raise ValueError("JSON 형식 오류: 객체 구분자")


def load_json_file(path: Path, encoding: str = "utf-8", *, chunk_bytes: int = CHUNK_BYTES) -> Any:
    """파일의 JSON 값을 돌려준다(위 모양은 원소 단위 점진 디코드)."""
    with Path(path).open("rb") as fh:
        reader = _Reader(fh, encoding, chunk_bytes)
        first = reader.peek()
        if first == "{":
            result: Any = _object(reader)
        elif first == "[":
            result = list(_array(reader))
        else:
            result = reader.value()
        if reader.peek():
            raise ValueError("JSON 형식 오류: 값 뒤에 남은 내용")
        return result
