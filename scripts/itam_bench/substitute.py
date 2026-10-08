"""형식 보존 가짜 값 생성기 `FakeValues` · 관계 등가류 유효 정책 (plans/145 W1 · D-321 · D-311 ②).

반출물에서 가리던 값을 **run 안에서 일관된 형식 보존 가짜 값**으로 바꾼다. 외부망 테스트 전용이다.

- **난수만** — 후보 글자는 `secrets`로만 뽑는다. 원값의 **내용**(글자 값·해시·키 기반 변환)에서
  파생하지 않고 **모양**만 본다: 위치별 문자 군 · 길이 · 여러 자리 숫자열 첫 자리 0 아님 ·
  날짜 모양. 문자 군은 숫자 · 라틴 · 한글 음절 · 한글 자모 · 한자 · 히라가나 · 가타카나(전각·
  반각) · 키릴·그리스(대소문자 유지)이고, 그 밖 글자(유니코드 L*)는 ASCII 소문자, ASCII 밖
  숫자(N*)는 ASCII 숫자로 뽑는다. 구두점·공백·기호·결합 부호(P*·Z*·S*·M*·C*)는 구조라 그대로
  둔다. 시드·주입 난수원은 없다.
- **대응은 메모리에만** — 원값 → 가짜 값 메모는 이 객체 안에만 있다. 피클 거부 · `repr`은 건수만 ·
  예외 문구에 값 없음 · 이 모듈은 파일·로그에 쓰지 않는다 · run 이 끝나면 객체째 버린다.
- **메모 키** — 끝 공백(` `)을 떼고 ASCII A-Z 만 소문자로 접은 글(길이 불변). 가짜 값은 키 기준으로
  한 번 뽑고, 호출마다 원값의 위치별 ASCII 대소문자·끝 공백을 다시 입힌다 — MariaDB 기본
  콜레이션(대소문자 무시 · PAD SPACE)에서 같은 값끼리 같은 가짜 값이라 조인이 유지된다. 정수 값의
  0 소수(`1500.0`)는 정수부(`1500`)의 가짜 값에 원값의 소수 모양을 다시 입힌다.
- **IPv4 계층 일관**(값 전체든 글 안이든 같은 단일 경로) — 원 첫 옥텟 → 가짜 첫 옥텟,
  원 `a.b` → 가짜 `A.B`, 원 `a.b.c` → 가짜 `A.B.C`(같은 서브넷 원값은 같은 가짜 서브넷)이고
  끝 옥텟은 그 /24 안 난수(1~254)다. 첫 옥텟 1~223(127 제외)·원 첫 옥텟과 다름 · 가능하면
  원값 접두와 겹치지 않는 접두 · 등록 원 IP 와 다름. 1~3 옥텟 접두 리터럴(`10.0.1.%`)도 같은
  메모다. 문자 군 치환은 IP 를 건드리지 않는다.
- **2단계**(run 경로) — `collecting()` 안에서는 `fake` 계열이 원값을 **등록만** 하고 발급하지 않는다
  (`register`로 결과 셀·SQL 리터럴·사용자 정보도 등록). 발급은 그 뒤라 run 의 원값이 전부 알려진
  상태에서 뽑으므로 「앞서 낸 가짜 값 = 나중에 본 원값」(관문 ①)이 구조상 생기지 않는다.
- **후보 거절**(재추첨 `MAX_DRAWS`) — ①자기 원값(키) ②알려진 원값(`CodeOriginals` · 이 생성기에
  등록된 원값 — NFKC+casefold 같음 · 치환한 원값은 부분 문자열로도(3자 이상 · 숫자만이면 5자 이상) ·
  `known` 판정) ③이미 낸 가짜 값(키) ④카탈로그 식별자(casefold) ⑤`reject`(누출 관문 규칙).
- **긴 값·정규식 PII 모양**(감사 MEDIUM-3) — `LONG_VALUE`자를 넘거나 원값이 정규식 PII 모양을 품으면
  재추첨 없이 한 번 뽑고 한 번 검사한다(긴 값은 부분 문자열 대조 생략 · PII 모양은 뽑은 가짜 값에
  현행 모양 가림 `_scrub_free_text`를 입힌다).
- **작은 공간 하한**(D-311 감사 M-1 일반화) — 값의 치환 공간에서 그 공간에 드는 알려진 원값 수를
  뺀 남은 공간이 `MIN_SPACE_FACTOR × (그 공간에 드는 이미 낸 가짜 값 수 + 1)` 미만이면 치환하지
  않는다(치환값의 여집합이 원 집합을 드러낸다). 낸 가짜 값은 패턴이 아니라 공간 일치로 센다(겹치는
  공간 중복 계산 없음 · 감사 LOW-1).
- **치환 불가** — 바꿀 글자 없음 · 공간 부족 · 재추첨 소진이면 `fake()`는 가림 표지(`redact.MASK`),
  `fake_or_none()`은 None. 사유별 **수만** 센다(`FALLBACK_REASONS`).
- **관계 등가류** — `effective_policy()`: 카탈로그 관계·동일 키 군으로 이어진 컬럼은 류 안 가장
  엄격한 등급으로 올린 정책 사본(내리기 없음 · 치환 여부만 맞춘다). 아는 컬럼 목록은 원 정책
  그대로다.
"""

from __future__ import annotations

import bisect
import contextlib
import dataclasses
import re
import secrets
import string
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from datetime import date, timedelta
from typing import Any

from .catalog import GRADE_RANK, ColumnPolicy, strictest
from .redact import (
    _IPV4,
    CODE_ORIGINAL_MIN_SUBSTRING,
    MASK,
    MIN_DIGIT_VALUE_LEN,
    MIN_VALUE_LEN,
    CodeOriginals,
    _fold,
    _pii_regex_hit,
    _scrub_free_text,
    ip_key,
    ip_prefix,
)

#: 값 하나당 재추첨 상한.
MAX_DRAWS = 32
#: 남은 치환 공간 하한 배수(감사 M-1).
MIN_SPACE_FACTOR = 2
#: 치환 불가 사유 — 바꿀 글자 없음 · 공간 부족 · 재추첨 소진.
FALLBACK_REASONS: tuple[str, ...] = ("no_change", "space", "draws")
#: 부분 문자열 대조에 쓰는 이 생성기 원값의 길이 상한 — 더 긴 원값은 같음만 본다(무작위 글자가 긴
#: 원값을 통째로 품을 확률은 무시할 만큼 작고, 20KB 입력에서 창 대조 비용을 묶는다).
SUBSTRING_MAX = 32
#: 이보다 긴 값은 재추첨 없이 한 번 뽑고 한 번 검사한다(부분 문자열 대조 생략 · 감사 MEDIUM-3).
LONG_VALUE = 256
#: 날짜 가짜 값 구간(양 끝 포함).
DATE_FIRST, DATE_LAST = date(2000, 1, 1), date(2030, 12, 31)
_DATE_DAYS = (DATE_LAST - DATE_FIRST).days + 1
#: 치환 공간 상한 — 이보다 크면 「충분히 크다」로 본다(긴 값의 큰 정수 곱 비용을 묶는다).
_SPACE_CAP = 1 << 64
_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)
#: 날짜 모양(키 기준 — `T`는 `t`로 접혀 있다). 중첩 수량자 없음.
_DATE = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})"
    r"(?:([ t])([0-9]{2}):([0-9]{2})(?::([0-9]{2})(?:\.([0-9]+))?)?)?"
)
#: 정수 값의 0 소수(`1500.0`) — 정수부를 키로 치환하고 소수 모양을 다시 입힌다(verifier Low-3).
_ZERO_FRACTION = re.compile(r"(-?[0-9]+)(\.0+)")
#: 가짜 IP 옥텟 후보 — 첫 옥텟(127 제외) · 가운데 · 끝(/24 안 호스트).
_IP_FIRST_POOL = tuple(str(n) for n in range(1, 224) if n != 127)
_IP_MID_POOL = tuple(str(n) for n in range(256))
_IP_LAST_POOL = tuple(str(n) for n in range(1, 255))


def _alphabet(first: int, last: int, *skip: int) -> str:
    return "".join(chr(code) for code in range(first, last + 1) if code not in skip)


#: 문자 군 — (뽑을 글자들, 원값 대조 정규식 조각(NFKC+casefold 한 글 기준)).
_C_HANGUL = (_alphabet(0xAC00, 0xD7A3), "[가-힣]")
_C_JAMO_COMPAT = (_alphabet(0x3131, 0x318E), "[ᄀ-ᇿ]")
_C_JAMO = (_alphabet(0x1100, 0x11FF), "[ᄀ-ᇿ]")
_C_CJK = (_alphabet(0x4E00, 0x9FFF), "[\u3400-\u9fff\U00020000-\U000323af]")
_C_HIRAGANA = (_alphabet(0x3041, 0x3096), "[\u3041-\u309f]")
_C_KATAKANA = (_alphabet(0x30A1, 0x30FA), "[\u30a0-\u30ff\u31f0-\u31ff]")
_C_KATAKANA_HALF = (_alphabet(0xFF66, 0xFF9D), "[\u30a0-\u30ff\u31f0-\u31ff]")
_C_CYRILLIC_UPPER = (_alphabet(0x0410, 0x042F), "[а-я]")
_C_CYRILLIC_LOWER = (_alphabet(0x0430, 0x044F), "[а-я]")
_C_GREEK_UPPER = (_alphabet(0x0391, 0x03A9, 0x03A2), "[α-ω]")
_C_GREEK_LOWER = (_alphabet(0x03B1, 0x03C9, 0x03C2), "[α-ω]")
_C_LATIN = (string.ascii_lowercase, "[a-z]")
_C_DIGIT = (string.digits, "[0-9]")


_DIGITS = string.digits
_LOWER = string.ascii_lowercase


def _is_digit(ch: str) -> bool:
    return "0" <= ch <= "9"


def _char_class(ch: str) -> tuple[str, str] | None:
    """ASCII 숫자·라틴 밖 글자의 문자 군(None = 구조라 그대로 둔다 · 감사 MEDIUM-2)."""
    code = ord(ch)
    if 0xAC00 <= code <= 0xD7A3:
        return _C_HANGUL
    category = unicodedata.category(ch)
    if category[0] == "N":
        return _C_DIGIT
    if category[0] != "L":
        return None
    if 0x3131 <= code <= 0x318E:
        return _C_JAMO_COMPAT
    if 0x1100 <= code <= 0x11FF or 0xA960 <= code <= 0xA97F or 0xD7B0 <= code <= 0xD7FF:
        return _C_JAMO
    if (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x323AF
    ):
        return _C_CJK
    if 0x3041 <= code <= 0x309F:
        return _C_HIRAGANA
    if 0x30A0 <= code <= 0x30FF or 0x31F0 <= code <= 0x31FF:
        return _C_KATAKANA
    if 0xFF66 <= code <= 0xFF9D:
        return _C_KATAKANA_HALF
    if 0x0400 <= code <= 0x052F:
        return _C_CYRILLIC_UPPER if ch.isupper() else _C_CYRILLIC_LOWER
    if 0x0370 <= code <= 0x03FF or 0x1F00 <= code <= 0x1FFF:
        return _C_GREEK_UPPER if ch.isupper() else _C_GREEK_LOWER
    return _C_LATIN


def _nonzero_lead(value: str, i: int) -> bool:
    """여러 자리 숫자열의 첫 자리이고 0이 아니면 True — 치환도 0이 아닌 숫자(1~9)만 뽑는다."""
    run_start = i == 0 or not _is_digit(value[i - 1])
    run_multi = i + 1 < len(value) and _is_digit(value[i + 1])
    return run_start and run_multi and value[i] != "0"


def _space(value: str) -> int:
    """값 조각 하나의 치환 공간 크기 — 문자 군 치환으로 뽑을 수 있는 값 전체.

    `_SPACE_CAP`에서 멈춘다.

    숫자 10(여러 자리 첫 자리는 1~9) · 라틴 26(대소문자는 원값 대조가 casefold 라 한 군) · 그 밖
    문자 군은 그 군 크기 · 구조 글자 고정.
    """
    size = 1
    for i, ch in enumerate(value):
        if _is_digit(ch):
            size *= 9 if _nonzero_lead(value, i) else 10
        elif "a" <= ch <= "z":
            size *= 26
        else:
            group = _char_class(ch)
            if group is not None:
                size *= len(group[0])
        if size >= _SPACE_CAP:
            return _SPACE_CAP
    return size


def _pattern(value: str) -> str:
    """값 조각 하나의 치환 공간 정규식(원값 대조용 · NFKC+casefold 한 글 기준 · 고정 길이)."""
    parts: list[str] = []
    for i, ch in enumerate(value):
        if _is_digit(ch):
            parts.append("[1-9]" if _nonzero_lead(value, i) else "[0-9]")
        elif "a" <= ch <= "z":
            parts.append("[a-z]")
        else:
            group = _char_class(ch)
            parts.append(re.escape(ch.casefold()) if group is None else group[1])
    return "".join(parts)


def _date_space(match: re.Match[str]) -> tuple[int, str]:
    """날짜 모양 키의 치환 공간(구간 안 날짜·시각 수)과 그 모양 정규식."""
    size = _DATE_DAYS
    pattern = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
    if match.group(4) is not None:
        size *= 24 * 60
        pattern += re.escape(match.group(4)) + r"[0-9]{2}:[0-9]{2}"
    if match.group(7) is not None:
        size *= 60
        pattern += r":[0-9]{2}"
    if match.group(8) is not None:
        size = min(size * 10 ** len(match.group(8)), _SPACE_CAP)
        pattern += r"\.[0-9]{" + str(len(match.group(8))) + "}"
    return size, pattern


def _draw_date(match: re.Match[str]) -> str:
    day = DATE_FIRST + timedelta(days=secrets.randbelow(_DATE_DAYS))
    out = day.isoformat()
    if match.group(4) is not None:
        out += f"{match.group(4)}{secrets.randbelow(24):02d}:{secrets.randbelow(60):02d}"
    if match.group(7) is not None:
        out += f":{secrets.randbelow(60):02d}"
    if match.group(8) is not None:
        out += "." + "".join(str(secrets.randbelow(10)) for _ in match.group(8))
    return out


def _draw(segment: str) -> str:
    """키 조각 하나의 후보 — 위치별 문자 군 난수(구조 글자는 그대로).

    난수 바이트는 `secrets.token_bytes`로 한 번에 받는다(글자당 24비트 · 군 크기로 나눈 나머지 —
    치우침 0.13% 미만). 키는 ASCII 소문자로 접혀 있다(대문자는 `_render`가 다시 입힌다).
    """
    noise = secrets.token_bytes(3 * len(segment))
    rolls = [
        (a << 16) | (b << 8) | c
        for a, b, c in zip(noise[0::3], noise[1::3], noise[2::3], strict=True)
    ]
    chars: list[str] = []
    for i, ch in enumerate(segment):
        roll = rolls[i]
        if "0" <= ch <= "9":
            chars.append(_DIGITS[1 + roll % 9] if _nonzero_lead(segment, i) else _DIGITS[roll % 10])
        elif "a" <= ch <= "z":
            chars.append(_LOWER[roll % 26])
        else:
            group = _char_class(ch)
            chars.append(ch if group is None else group[0][roll % len(group[0])])
    return "".join(chars)


def _key(text: str) -> str:
    """메모 키 — 끝 공백 제거 + ASCII A-Z 만 소문자(길이 불변)."""
    return text.rstrip(" ").translate(_ASCII_LOWER)


def _render(fake_key: str, text: str) -> str:
    """키 기준 가짜 값에 원값의 위치별 ASCII 대소문자·끝 공백을 다시 입힌다.

    길이가 다르면(정규식 PII 모양 가림을 입힌 가짜 값) 대소문자 없이 끝 공백만 입힌다.
    """
    stripped = text.rstrip(" ")
    tail = text[len(stripped) :]
    if len(fake_key) != len(stripped):
        return fake_key + tail
    chars = [f.upper() if "A" <= o <= "Z" else f for f, o in zip(fake_key, stripped, strict=True)]
    return "".join(chars) + tail


def _substring_floor(folded: str) -> int:
    """부분 문자열 대조 하한 — 숫자만인 원값은 수집 값 하한(`MIN_DIGIT_VALUE_LEN`)과 같다."""
    return MIN_DIGIT_VALUE_LEN if folded.isdigit() else CODE_ORIGINAL_MIN_SUBSTRING


class FakeValues:
    """run 스코프 형식 보존 가짜 값 생성기 — 원값·대응은 이 객체 안 메모리에만 있다(피클 거부 ·
    repr 은 건수만).

    Args:
        originals: P1 원 코드값·라벨 집합(없으면 None)
        identifiers: 카탈로그 테이블·컬럼 이름 — 이와 casefold 같은 후보는 재추첨
        reject: 누출 관문 규칙 — True 면 그 후보는 재추첨(`set_reject`로 바꿀 수 있다)
        known: 수집 값·카나리아 일치 판정 — True 면 그 후보는 재추첨(`set_known`으로 바꿀 수 있다)
    """

    __slots__ = (
        "_originals",
        "_identifiers",
        "_reject",
        "_known",
        "_collecting",
        "_memo",
        "_issued",
        "_issued_by_len",
        "_emitted",
        "_seen_equal",
        "_seen_by_len",
        "_seen_count",
        "_seen_long",
        "_seen_lengths",
        "_space_counts",
        "_ip_map",
        "_ip_used",
        "_ip_originals",
        "_fallback",
    )

    def __init__(
        self,
        *,
        originals: CodeOriginals | None = None,
        identifiers: Iterable[str] = (),
        reject: Callable[[str], bool] | None = None,
        known: Callable[[str], bool] | None = None,
    ) -> None:
        self._originals = originals
        self._identifiers = frozenset(str(i).casefold() for i in identifiers if str(i))
        self._reject = reject
        self._known = known
        self._collecting = False
        self._memo: dict[str, str | None] = {}
        self._issued: set[str] = set()
        #: 낸 가짜 값(정규화 글) 길이별 목록 — 공간 하한이 공간 일치로 센다
        self._issued_by_len: dict[int, list[str]] = {}
        self._emitted: set[str] = set()
        self._seen_equal: set[str] = set()
        self._seen_by_len: dict[int, list[str]] = {}
        self._seen_count = 0
        self._seen_long: set[str] = set()
        self._seen_lengths: list[int] = []
        #: 공간 패턴 → [정규식, CodeOriginals 일치 수, 원값 일치 수, 훑은 원값 수, 가짜 일치 수,
        #: 훑은 가짜 수]
        self._space_counts: dict[tuple[str, int], list[Any]] = {}
        #: 원 IP 접두(1~4 옥텟) → 같은 깊이 가짜 접두(치환 불가 None)
        self._ip_map: dict[str, str | None] = {}
        #: 가짜 부모 접두("" = 최상위) → 이미 쓴 자식 옥텟
        self._ip_used: dict[str, set[str]] = {}
        #: 깊이별(1~4) 원 IP 접두
        self._ip_originals: tuple[set[str], ...] = (set(), set(), set(), set())
        self._fallback: Counter[str] = Counter()

    def __repr__(self) -> str:
        return f"<FakeValues 가짜 {len(self._issued)}건 · 원값 {self._seen_count}건>"

    def __reduce__(self) -> Any:
        raise TypeError("FakeValues 는 직렬화할 수 없다(원값·대응 메모 보유 · 메모리 전용)")

    def __getstate__(self) -> Any:
        raise TypeError("FakeValues 는 직렬화할 수 없다(원값·대응 메모 보유 · 메모리 전용)")

    def set_reject(self, reject: Callable[[str], bool] | None) -> None:
        """관문 규칙 판정을 바꾼다(관문을 생성기보다 늦게 만들 때)."""
        self._reject = reject

    def set_known(self, known: Callable[[str], bool] | None) -> None:
        """수집 값·카나리아 일치 판정을 바꾼다(그 판정을 가진 객체가 생성기를 품을 때)."""
        self._known = known

    # --- 2단계 수집 ---------------------------------------------------------------------

    @contextlib.contextmanager
    def collecting(self) -> Iterator[None]:
        """2단계 1차(수집) — 이 안에서 `fake`·`fake_or_none`·`fake_ip`는 원값만 등록하고 표지
        (`fake_or_none`은 None)를 돌려준다. 발급·메모·치환 불가 집계는 없다."""
        self._collecting = True
        try:
            yield
        finally:
            self._collecting = False

    def register(self, values: Iterable[object]) -> None:
        """원값 등록만(발급 없음) — 결과 셀·SQL 리터럴·사용자 정보처럼 치환하지 않을 수도 있는 값.

        같음 대조·공간 계산·관문 ①에 든다. 부분 문자열 대조에는 넣지 않는다(치환한 원값만).
        앞뒤 공백을 다 걷은 꼴도 함께 등록한다 — 결과 요약 표본은 `strip()`한 셀을 생성기에 넣는다
        (교정 2차 Minor-1: 앞 공백 셀이 2차에서 처음 보는 원값이 되던 결함).
        """
        for value in values:
            if value is None or isinstance(value, bool):
                continue
            text = str(value)
            stripped = text.strip()
            if stripped:
                self._register_text(_key(text), substring=False)
                if stripped != text.rstrip(" "):
                    self._register_text(_key(stripped), substring=False)

    def _register_text(self, key: str, *, substring: bool) -> None:
        zero = _ZERO_FRACTION.fullmatch(key)
        if zero is not None:
            key = zero.group(1)
        self._note_original(key, substring=substring)
        for match in _IPV4.finditer(key):
            self._note_ip(ip_key(match.group(0)))
        prefix = ip_prefix(key)
        if prefix is not None:
            self._ip_register(prefix[1])

    # --- 가짜 값 -----------------------------------------------------------------------

    def fake(self, value: object) -> str:
        """원값 → 가짜 값(run 안 메모). 치환 불가면 가림 표지 `redact.MASK`."""
        out = self.fake_or_none(value)
        return MASK if out is None else out

    def fake_or_none(self, value: object) -> str | None:
        """원값 → 가짜 값(run 안 메모). 치환 불가면 None(코드값 파일의 `exhausted` 처분용)."""
        text = str(value)
        key = _key(text)
        zero = _ZERO_FRACTION.fullmatch(key)
        if zero is not None:
            base = self.fake_or_none(zero.group(1))
            if base is None:
                return None
            rendered = base + zero.group(2) + text[len(text.rstrip(" ")) :]
            self._emitted.add(rendered.rstrip(" "))
            return rendered
        if self._collecting:
            self._register_text(key, substring=True)
            return None
        if key in self._memo:
            canonical = self._memo[key]
        else:
            canonical = self._issue(key, text)
        if canonical is None:
            return None
        rendered = _render(canonical, text)
        self._emitted.add(rendered.rstrip(" "))
        return rendered

    def fake_ip(self, text: str) -> str:
        """글 안 IPv4 마다 계층 일관 가짜 IP(치환 불가면 가림 표지)."""
        return _IPV4.sub(self._ip_sub, str(text))

    def _issue(self, key: str, text: str) -> str | None:
        self._note_original(key)
        prefix = ip_prefix(key)
        reason: str | None
        if prefix is not None:
            result = self._ip_prefix_fake(prefix)
            reason = "space"
        else:
            result, reason = self._issue_text(key, text)
        if result is None:
            if reason is not None:
                self._fallback[reason] += 1
        else:
            self._add_issued(result)
        self._memo[key] = result
        return result

    def _segments(self, key: str) -> list[tuple[str, bool]] | None:
        """키 → 조각 ``(글, IP 가짜 값인가)`` — IP 는 계층 메모의 가짜 값으로 바꿔 둔다.

        IP 치환 불가면 None.
        """
        segments: list[tuple[str, bool]] = []
        cursor = 0
        for match in _IPV4.finditer(key):
            if match.start() > cursor:
                segments.append((key[cursor : match.start()], False))
            fake = self._ip_value(ip_key(match.group(0)))
            if fake is None:
                return None
            segments.append((fake, True))
            cursor = match.end()
        if cursor < len(key) or not segments:
            segments.append((key[cursor:], False))
        return segments

    def _issue_text(self, key: str, text: str) -> tuple[str | None, str | None]:
        segments = self._segments(key)
        if segments is None:
            return None, None  # IP 치환 불가는 `_ip_value`가 이미 셌다
        has_ip = any(is_ip for _, is_ip in segments)
        date_match = None if has_ip else _DATE.fullmatch(key)
        if date_match is not None:
            size, date_pattern = _date_space(date_match)
            pattern: Callable[[], str] = lambda: date_pattern  # noqa: E731
        else:
            size = 1
            for segment, is_ip in segments:
                if not is_ip:
                    size = min(size * _space(segment), _SPACE_CAP)
            pattern = lambda: "".join(  # noqa: E731
                re.escape(segment) if is_ip else _pattern(segment) for segment, is_ip in segments
            )
        if size <= 1:
            if not has_ip:
                return None, "no_change"
            return "".join(segment for segment, _ in segments), None  # IP 와 구조 글자뿐
        if not self._room(size, pattern, len(key)):
            return None, "space"
        long_value = len(key) > LONG_VALUE
        # 긴 값은 모양 검사 없이 뽑은 가짜 값에 모양 가림을 입힌다(정규식 훑기 한 번 절약)
        pii_shape = long_value or _pii_regex_hit(text)
        for _ in range(1 if long_value or pii_shape else MAX_DRAWS):
            if date_match is not None:
                candidate = _draw_date(date_match)
            else:
                candidate = "".join(
                    segment if is_ip else _draw(segment) for segment, is_ip in segments
                )
            if pii_shape:
                candidate = _scrub_free_text(candidate)
            if self._acceptable(candidate, key, text, substring=not long_value):
                return candidate, None
        return None, "draws"

    def _acceptable(self, candidate: str, key: str, text: str, *, substring: bool = True) -> bool:
        if candidate == key or self._is_known_original(candidate, substring=substring):
            return False
        if candidate in self._issued or candidate.casefold() in self._identifiers:
            return False
        rendered = _render(candidate, text)
        forms = (rendered,) if rendered == candidate else (rendered, candidate)
        if self._known is not None and any(self._known(form) for form in forms):
            return False
        return not (self._reject is not None and any(self._reject(form) for form in forms))

    def _add_issued(self, canonical: str) -> None:
        if canonical not in self._issued:
            self._issued.add(canonical)
            folded = _fold(canonical)
            self._issued_by_len.setdefault(len(folded), []).append(folded)

    # --- 알려진 원값 ---------------------------------------------------------------------

    def _note_original(self, key: str, *, substring: bool = True) -> None:
        folded = _fold(key)
        if not folded:
            return
        if folded not in self._seen_equal:
            self._seen_equal.add(folded)
            self._seen_by_len.setdefault(len(folded), []).append(folded)
            self._seen_count += 1
        if (
            substring
            and folded not in self._seen_long
            and _substring_floor(folded) <= len(folded) <= SUBSTRING_MAX
        ):
            self._seen_long.add(folded)
            index = bisect.bisect_left(self._seen_lengths, len(folded))
            if index == len(self._seen_lengths) or self._seen_lengths[index] != len(folded):
                self._seen_lengths.insert(index, len(folded))

    def _is_known_original(self, candidate: str, *, substring: bool = True) -> bool:
        """`CodeOriginals.hit`과 같은 규칙 — 같음 · 하한 이상 원값을 부분 문자열로 품음.

        `substring=False`(긴 값)면 같음만 본다.
        """
        folded = _fold(candidate)
        if self._originals is not None:
            if self._originals.hit(candidate) if substring else self._originals.equal(folded):
                return True
        if folded in self._seen_equal:
            return True
        if not substring:
            return False
        for length in self._seen_lengths:
            if length > len(folded):
                break
            for i in range(len(folded) - length + 1):
                if folded[i : i + length] in self._seen_long:
                    return True
        return False

    def _in_code_originals(self, folded: str) -> bool:
        return self._originals is not None and self._originals.equal(folded)

    def _room(self, size: int, pattern: Callable[[], str], length: int) -> bool:
        """남은 공간 = 공간 − 그 공간에 드는 알려진 원값 수 ≥ 하한이면 True(값 비노출).

        먼저 전체 수로 보수적으로 보고, 모자라면 그 공간(고정 길이 정규식)에 드는 원값·낸 가짜 값을
        길이 같은 것만 훑어 센다(증분).
        """
        code_total = len(self._originals) if self._originals is not None else 0
        need_total = MIN_SPACE_FACTOR * (len(self._issued) + 1)
        if size - code_total - self._seen_count >= need_total:
            return True
        source = pattern()
        entry = self._space_counts.get((source, length))
        if entry is None:
            compiled = re.compile(source)
            code = self._originals.count_matching(compiled) if self._originals is not None else 0
            entry = self._space_counts[(source, length)] = [compiled, code, 0, 0, 0, 0]
        regex: re.Pattern[str] = entry[0]
        seen = self._seen_by_len.get(length, [])
        for folded in seen[entry[3] :]:
            if regex.fullmatch(folded) and not self._in_code_originals(folded):
                entry[2] += 1
        entry[3] = len(seen)
        issued = self._issued_by_len.get(length, [])
        entry[4] += sum(1 for folded in issued[entry[5] :] if regex.fullmatch(folded))
        entry[5] = len(issued)
        return bool(size - entry[1] - entry[2] >= MIN_SPACE_FACTOR * (entry[4] + 1))

    # --- IPv4 계층 일관 -------------------------------------------------------------------

    def _ip_register(self, octets: list[str]) -> None:
        for depth in range(1, len(octets) + 1):
            self._ip_originals[depth - 1].add(".".join(octets[:depth]))

    def _note_ip(self, full: str) -> None:
        """원 IP 등록 — 같음 대조(부분 문자열 대조 제외)·관문 ①·계층 접두."""
        self._note_original(full, substring=False)
        self._ip_register(full.split("."))

    def _ip_sub(self, match: re.Match[str]) -> str:
        full = ip_key(match.group(0))
        if self._collecting:
            self._note_ip(full)
            return MASK
        fake = self._ip_value(full)
        if fake is None:
            return MASK
        self._emitted.add(fake)
        return fake

    def _ip_value(self, full: str) -> str | None:
        """원 IPv4 → 가짜 IPv4(메모 · 치환 불가 None — 처음 한 번 `space`로 센다)."""
        if full in self._ip_map:
            return self._ip_map[full]
        self._note_ip(full)
        fake = self._ip_fake(full)
        if fake is None:
            self._fallback["space"] += 1
        else:
            self._add_issued(fake)
        return fake

    def _ip_prefix_fake(self, prefix: tuple[str, list[str], str]) -> str | None:
        """접두 리터럴(`10.0.1.%`) → 같은 계층 메모의 가짜 접두(`37.201.4.%`)."""
        head, octets, tail = prefix
        self._ip_register(octets)
        fake = self._ip_fake(".".join(octets))
        return None if fake is None else f"{head}{fake}.{tail}"

    def _ip_fake(self, original: str) -> str | None:
        """원 접두(1~4 옥텟) → 같은 깊이 가짜 접두 — 부모 접두의 가짜 값 아래에서 뽑는다(메모)."""
        if original in self._ip_map:
            return self._ip_map[original]
        parts = original.split(".")
        parent = self._ip_fake(".".join(parts[:-1])) if len(parts) > 1 else ""
        result = None if parent is None else self._ip_child(parent, parts)
        self._ip_map[original] = result
        return result

    def _ip_child(self, parent: str, parts: list[str]) -> str | None:
        depth = len(parts)
        pool = _IP_FIRST_POOL if depth == 1 else (_IP_LAST_POOL if depth == 4 else _IP_MID_POOL)
        used = self._ip_used.setdefault(parent, set())
        taken = self._ip_originals[depth - 1]
        prefix = f"{parent}." if parent else ""
        free = [o for o in pool if o not in used and not (depth == 1 and o == parts[0])]
        preferred = [o for o in free if prefix + o not in taken]
        # 끝 옥텟은 등록 원 IP 와 같으면 안 된다 · 접두는 가능하면 원값 접두와 겹치지 않게
        candidates = preferred if (preferred or depth == 4) else free
        for _ in range(min(MAX_DRAWS, len(candidates))):
            octet = secrets.choice(candidates)
            value = prefix + octet
            if depth < 4 or self._ip_acceptable(value):
                used.add(octet)
                return value
        return None

    def _ip_acceptable(self, candidate: str) -> bool:
        """가짜 IP 후보 — 등록 원값·낸 가짜 값과 같지 않고 수집 값·관문 규칙에 걸리지 않음."""
        if _fold(candidate) in self._seen_equal or candidate in self._issued:
            return False
        if self._known is not None and self._known(candidate):
            return False
        return not (self._reject is not None and self._reject(candidate))

    # --- 등록부(관문용 · 값을 밖으로 복사하지 않는 조회 우선) ------------------------------

    def is_fake(self, text: object) -> bool:
        """이 생성기가 낸 가짜 값인가(키 기준 · 0 소수는 정수부 기준)."""
        key = _key(str(text))
        if key in self._issued:  # 소수 0 으로 끝나는 가짜 값 자체(`98.5` → `26.0`)
            return True
        zero = _ZERO_FRACTION.fullmatch(key)
        return zero is not None and zero.group(1) in self._issued

    def collisions(self) -> int:
        """낸 가짜 값 중 등록 원값(이 생성기에 들어온 원값)과 같거나 `known`에 걸리는 수.

        2단계 경로에서는 구조상 0이다 — 0이 아니면 관문이 닫힌 쪽으로 잡는다. 원값 길이 하한은
        `redact.MIN_VALUE_LEN`(숫자만이면 `MIN_DIGIT_VALUE_LEN`)이다.
        """
        count = 0
        for fake in self._issued:
            floor = MIN_DIGIT_VALUE_LEN if fake.isdigit() else MIN_VALUE_LEN
            if len(fake) < floor:
                continue
            if _fold(fake) in self._seen_equal or (self._known is not None and self._known(fake)):
                count += 1
        return count

    def fakes(self) -> list[str]:
        """이 run 에서 내보낸 가짜 값(대소문자 그대로 · 끝 공백 제외) 정렬 목록 — 원값 연결 없음."""
        return sorted(self._emitted)

    def fallback_counts(self) -> dict[str, int]:
        """치환 불가 사유 → 수(서로 다른 원값 기준)."""
        return {reason: self._fallback.get(reason, 0) for reason in FALLBACK_REASONS}


# --- 관계 등가류 ---------------------------------------------------------------------


def _bare(table: object) -> str:
    return str(table).rsplit(".", 1)[-1]


def relation_classes(catalog_doc: Mapping[str, Any]) -> list[list[tuple[str, str]]]:
    """카탈로그 관계(전 종류 `columns` 쌍) + `same_key_groups` → 컬럼 등가류(2개 이상만).

    원소는 ``(테이블 맨 이름, 컬럼)``(처음 본 표기) · 비교는 casefold 다.
    """
    parent: dict[tuple[str, str], tuple[str, str]] = {}
    spelled: dict[tuple[str, str], tuple[str, str]] = {}

    def node(table: object, column: object) -> tuple[str, str]:
        name = (_bare(table), str(column))
        folded = (name[0].casefold(), name[1].casefold())
        spelled.setdefault(folded, name)
        parent.setdefault(folded, folded)
        return folded

    def find(item: tuple[str, str]) -> tuple[str, str]:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(a: tuple[str, str], b: tuple[str, str]) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    tables: Mapping[str, Any] = catalog_doc.get("tables") or {}
    for spec in tables.values():
        for rel in (spec or {}).get("relations") or []:
            child, target = rel.get("from"), rel.get("to")
            if not child or not target:
                continue
            for pair in rel.get("columns") or []:
                if len(pair) != 2 or pair[0] is None or pair[1] is None:
                    continue
                union(node(child, pair[0]), node(target, pair[1]))
    for group in catalog_doc.get("same_key_groups") or []:
        anchors: dict[str, tuple[str, str]] = {}
        for table in group:
            for column in (tables.get(table) or {}).get("key") or []:
                item = node(table, column)
                union(anchors.setdefault(str(column).casefold(), item), item)
    classes: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for item in parent:
        classes.setdefault(find(item), []).append(spelled[item])
    return [sorted(members) for members in classes.values() if len(members) > 1]


@dataclasses.dataclass(frozen=True)
class EffectivePolicy(ColumnPolicy):
    """관계 등가류로 올린 유효 정책 — **등급**(`grade`)은 올린 값, **아는 컬럼**(`tables`·
    `column_names`)은 원 정책 컬럼뿐이다(verifier Low-1 — 정책 밖 컬럼은 올라가도 리포트 「분류 필요
    컬럼」에 남는다). 정책 안 컬럼의 올림은 `tables`에도 반영한다(리터럴 판정이 같은 등급을 본다).
    """

    #: 정책 밖 컬럼 올림까지 합친 테이블 → {컬럼: 등급}(`grade` 전용)
    merged: Mapping[str, Mapping[str, str]] = dataclasses.field(default_factory=dict)

    def grade(self, column: str, table: str | None = None) -> str:
        return ColumnPolicy(db_id=self.db_id, scope=self.scope, tables=self.merged).grade(
            column, table
        )


def effective_policy(policy: ColumnPolicy, catalog_doc: Mapping[str, Any]) -> ColumnPolicy:
    """관계 등가류 안 가장 엄격한 등급으로 올린 정책 **사본**(원본 불변 · 내리기 없음).

    정책 밖 컬럼의 기본 등급(`policy.grade` — 보통 `unclassified`)도 류 등급 계산에 든다 — 일반
    컬럼이 정책 밖 관계 컬럼과 이어지면 일반 컬럼도 올라간다. 올라간 정책 밖 컬럼은 등급에만 들고
    아는 컬럼 목록(`column_names()`)은 원 정책 그대로다(`EffectivePolicy`).
    """
    known = {table: dict(columns) for table, columns in policy.tables.items()}
    merged = {table: dict(columns) for table, columns in policy.tables.items()}
    table_names = {table.casefold(): table for table in merged}
    for members in relation_classes(catalog_doc):
        grades = {member: policy.grade(member[1], member[0]) for member in members}
        top = strictest(grades.values())
        for (table, column), grade in grades.items():
            if GRADE_RANK.get(grade, -1) >= GRADE_RANK[top]:
                continue
            name = table_names.setdefault(table.casefold(), table)
            columns = merged.setdefault(name, {})
            spelled = next((c for c in columns if c.casefold() == column.casefold()), column)
            columns[spelled] = top
            if spelled in known.get(name, {}):
                known[name][spelled] = top
    return EffectivePolicy(
        db_id=policy.db_id,
        scope=policy.scope,
        tables=known,
        canary_literals=policy.canary_literals,
        canary_patterns=policy.canary_patterns,
        merged=merged,
    )
