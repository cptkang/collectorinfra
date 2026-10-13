"""반출 위생 — 토큰 단위 형식 보존 치환 · 민감 모양만 · 근거 묶음 · 누출 관문.

plans/152 §3.8·§3.10 · W4 · D-323 · D-321(형식 보존 치환) · D-301(ITAM 위생 재사용) ·
D-300 ④(계정 가림) · D-274(게이트웨이 import 0).

순수 로직이다 — 네트워크·LLM 0. 디스크 쓰기는 `write_outputs` 하나뿐이고, 관문을 통과할 때만
산출물을 쓴다(실패하면 `leak_check.json`만 쓰고 호출 측이 exit 1).

ITAM 벤치 부품을 **고치지 않고** 그대로 쓴다: `substitute.FakeValues`(run 스코프 가짜 값 ·
2단계 수집) ·
`redact.PiiVault`·`redact_text`·`scrub_tree`·`mask_identifier`·`LeakGate`·`write_gated` ·
`catalog.ColumnPolicy`(APM은 컬럼이 없어 빈 테이블 + 카나리아만).

**이름 토큰화 규칙**(`split_name`) — 이름(인스턴스·호스트·업무·서비스·URL)을 다음에서 자른다.
  - 구분자: 글자(유니코드 L*)·숫자(N*)가 아닌 글자 전부(`-` `_` `.` `/` `:` `@` 공백 …) —
    원문 그대로 둔다
  - 숫자 ↔ 숫자 아닌 글자 경계(`was01` → `was`|`01`)
  - 소문자 → 대문자 경계(`ordWas` → `ord`|`Was`) · 대문자 연쇄 뒤 대문자+소문자(`HTTPServer` →
    `HTTP`|`Server`)
  - 문자 체계 경계(한글 ↔ 라틴 ↔ 그 밖 글자 — `주문WAS` → `주문`|`WAS`)
  - IPv4는 토큰화하지 않고 `FakeValues.fake_ip`(계층 일관)로 바꾼다
  토큰마다 `FakeValues.fake`(형식 보존 · 메모 키가 ASCII 대소문자를 접으므로 `ORD`·`ord`는 같은 가짜
  토큰에 원 대소문자를 다시 입힌 값)로 바꿔 재조립한다. 허용 목록(`term_policy.yaml`) 토큰은 원문
  유지 — 대조는 NFKC + casefold 같음.

**토큰 사전**(`name_tokens`) — 치환으로 바뀌는 이름의 토큰 중 허용 목록·숫자만·1자를 뺀 전부
  (ASCII 2자 토큰 포함 — 공통 2자 단어도 허용 목록으로만 살린다 · 기본 거부)와, 대소문자 경계로만
  갈린 라틴 토큰 연쇄의 결합 소문자형(`OrdPay01` → `ordpay`). 토큰 사전은 레코드·P층·run 메타의
  **문자열 값 전부**에 적용한다(키는 원이름·ID·계정만 — 코드 어휘 칸 `SKIP_KEYS`와 시나리오 파일
  어휘 `expect`는 제외). 문장 칸(`SENTENCE_KEYS` · 관측 자유 문장 포함)은 이어서 `redact_text`로,
  그 밖 값은 `scrub_tree`로 넘긴다. 관문은 같은 범위의 잎을 토큰 사전으로 다시 대조한다
  (`pii_value` · `.md`는 원이름·ID·카나리아만).

**GUID·txid 규칙** — 키 이름(`guid`·`txid`)으로 모은 값과 GUID 모양(8-4-4-4-12 16진 · 하이픈 없는
  16진 32자리)은 형식 보존 치환한다. GUID 모양은 위치마다 16진 숫자 난수(하이픈 유지 ·
  원값에 대문자만 있으면 대문자) — `FakeValues`의 라틴 군 치환은 16진 형식을 깨므로 별도
  생성기(`_HexIds`)를 쓴다.
  그 밖 모양(부호 있는 정수 txid · `guid-0000` 같은 합성 GUID)은 `FakeValues.fake`(숫자→숫자 · 글자→
  글자 · 구조 유지)다. 둘 다 run 메모 · 2단계 등록을 따른다.

**run 단위 2단계**(Known Mistake 2026-10-08) — `RunSanitizer.sanitize`가 ①원이름·ID·계정을 전부
  모아 `FakeValues.collecting()` 안에서 같은 치환 경로를 한 번 돌려 원값만 등록하고 ②그 뒤
  같은 경로로 발급한다. 「이미 가짜 값이면 통과」 단락은 없다 — 다시 바꾸지 않는 것은
  **이번 호출이 만든 구간**(사전 일치 구간)뿐이고, 그 밖 구간만 `redact_text`·`scrub_tree`로 넘긴다.

**근거 묶음 「없음」 규칙** — 관측상 없음(드라이버가 칸을 채웠는데 빈 목록·None·빈 문자열)은 표지
  `NONE`(`"none"`)으로, 관측 누락(드라이버 레코드에 칸 자체가 없음)은 `null`로 적는다.
  `check_evidence`는 실패 턴의 필수 칸이 `null`·빈 문자열·부재이면 누락으로 센다(`"none"`·False·0은
  값이다).
"""

from __future__ import annotations

import contextlib
import copy
import json
import re
import secrets
import unicodedata
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

import yaml

from scripts.itam_bench.catalog import ColumnPolicy
from scripts.itam_bench.redact import (
    _IPV4,
    ERROR_TEXT_MAX,
    MASK,
    RUN_USER_FIELDS,
    LeakGate,
    PiiVault,
    _pii_regex_hit,
    mask_identifier,
    redact_text,
    scrub_tree,
    write_gated,
)
from scripts.itam_bench.substitute import FakeValues

from . import REPO_ROOT
from .record import TurnRecord

TERM_POLICY_PATH = REPO_ROOT / "testdata" / "apm_bench" / "term_policy.yaml"

#: 근거 묶음 — 관측상 없음 표지(`null`은 관측 누락).
NONE = "none"
#: 민감 시나리오 응답 본문 자리 표지(모양만 — 길이는 `response_len`).
SENSITIVE_RESPONSE = "<민감:모양만>"
#: SQL 본문 가림 표지(첫 키워드는 남긴다).
SQL_MASK = "<가림:sql>"

#: 자리표 종류(`{{kind_N}}`의 kind) → 처리. `pid`·`domain_id`는 사람·조직을 식별하지 않아
#: 그대로 둔다.
NAME_PLACEHOLDER_KINDS: frozenset[str] = frozenset({"inst", "host", "biz", "svc", "url"})
ID_PLACEHOLDER_KINDS: frozenset[str] = frozenset({"guid"})
ACCOUNT_PLACEHOLDER_KINDS: frozenset[str] = frozenset({"user"})
#: 레코드 어디서든 이 키의 값은 원이름으로 모은다(게이트웨이 도구 인자 이름 · 처리기 provenance).
NAME_KEYS: frozenset[str] = frozenset(
    {"hostname", "instance_name", "business", "business_name", "service", "service_name",
     "application_name", "url"}
)
#: 이 키의 값은 GUID·txid 원값으로 모은다.
ID_KEYS: frozenset[str] = frozenset({"guid", "txid"})
#: 이 키의 값은 계정(D-300 ④ `mask_identifier`)으로 모은다.
ACCOUNT_KEYS: frozenset[str] = frozenset({"user_id", "userId", "login", "login_id", "os_user"})
#: 치환하지 않는 코드 어휘 칸(시나리오·보기·도구·분류 id — 이름 사전이 우연히 겹쳐도 깨지 않게).
SKIP_KEYS: frozenset[str] = frozenset(
    {"v", "scenario_id", "split", "covers", "sent_at", "tool", "view", "views", "agent", "agents",
     "task_id", "categories", "locus", "axes", "clarify_kind", "result_views", "parsed_views",
     "kind", "field", "sources", "areas", "tools", "kinds", "view_rows"}
)
#: 시나리오 파일 어휘 칸(자리표 미충전 정답 — 저장소 공개 값) — 원이름·ID·계정만 바꾸고 토큰 사전은
#: 적용하지 않는다(관문 토큰 대조도 제외).
SCENARIO_KEYS: frozenset[str] = frozenset({"expect"})
#: 자유 문장 칸(관측) — 이름 토큰까지 사전 치환 + `redact_text`.
OBSERVED_FREE_TEXT: frozenset[str] = frozenset({"response", "clarify_text"})
#: 구조 칸 중 문장 값 칸(어느 깊이든) — 자유 문장과 같이 사전 치환 + `redact_text`.
SENTENCE_KEYS: frozenset[str] = frozenset(
    {"prompt", "original_query", "question", "clarification", "degraded_reason", "message",
     "reason", "text", "summary"}
)

#: 한↔영 약어 관계 경고(`report.md`) — 운영 이름이 새지 않게 예시 단어를 넣지 않는다.
TERM_RISK_NOTE = (
    "이름 토큰은 일반 단어 허용 목록(term_policy.yaml)에 없으면 형식 보존 가짜 값으로 바뀐다. "
    "한글 업무어와 그 영문 약어 토큰이 한쪽만 허용 목록에 있으면 둘의 대응 관계가 반출본에서 "
    "사라진다 — 업무어↔약어 대조가 필요한 실패는 내부망 원본으로 확인한다."
)

_UUID = re.compile(
    r"(?<![0-9A-Fa-f-])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{12}(?![0-9A-Fa-f-])|(?<![0-9A-Fa-f])[0-9A-Fa-f]{32}(?![0-9A-Fa-f])"
)
_JWT = r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"
_BEARER = r"\bbearer[ \t]+[A-Za-z0-9._~+/=-]{8,}"
_BEARER_RE = re.compile(_BEARER, re.IGNORECASE)
_SQL = (
    r"\b(?:select|insert|update|delete|merge)\b[^\n]{0,4000}?\b(?:from|into|set)\b[^\n]*"
)
_ASCII_ALNUM = "A-Za-z0-9"
#: 민감 응답 줄 — `키 = 값`·`키: 값`이면 키만 남긴다.
_KEY_LINE = re.compile(r"^\s*([^\s=:]{1,64})\s*[=:]\s*\S")
_MAX_DRAWS = 32


# --- 허용 목록 · 토큰화 -------------------------------------------------------------------


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _char_kind(ch: str) -> str:
    """토큰 글자 종류 — `d` 숫자 · `U`/`l` 대·소문자 · `h` 한글 · `o` 그 밖 글자 · `""` 구분자."""
    category = unicodedata.category(ch)
    if category[0] == "N":
        return "d"
    if category[0] != "L":
        return ""
    code = ord(ch)
    if (
        0xAC00 <= code <= 0xD7A3
        or 0x1100 <= code <= 0x11FF
        or 0x3130 <= code <= 0x318F
        or 0xA960 <= code <= 0xA97F
        or 0xD7B0 <= code <= 0xD7FF
    ):
        return "h"
    if ch.isupper():
        return "U"
    if ch.islower():
        return "l"
    return "o"


def _boundary(prev: str, kind: str, text: str, i: int) -> bool:
    if prev in ("U", "l") and kind in ("U", "l"):
        if prev == "l" and kind == "U":
            return True
        if prev == "U" and kind == "U" and i + 1 < len(text):
            return _char_kind(text[i + 1]) == "l"
        return False
    return prev != kind


def split_name(name: str) -> list[tuple[str, bool]]:
    """이름 → 조각 ``(글, 토큰인가)`` — 구분자 조각은 False(원문 유지). 규칙은 모듈 독스트링."""
    pieces: list[tuple[str, bool]] = []
    buf, prev = "", ""
    for i, ch in enumerate(name):
        kind = _char_kind(ch)
        if not kind:
            if buf:
                pieces.append((buf, True))
                buf = ""
            if pieces and not pieces[-1][1]:
                pieces[-1] = (pieces[-1][0] + ch, False)
            else:
                pieces.append((ch, False))
            prev = ""
            continue
        if buf and _boundary(prev, kind, name, i):
            pieces.append((buf, True))
            buf = ""
        buf += ch
        prev = kind
    if buf:
        pieces.append((buf, True))
    return pieces


@dataclass(frozen=True)
class TermPolicy:
    """일반 단어 허용 목록(기본 거부) — 대조는 NFKC + casefold 같음."""

    terms: frozenset[str] = frozenset()

    def allows(self, token: str) -> bool:
        return _fold(token) in self.terms

    def changes(self, name: str) -> bool:
        """치환하면 이름이 바뀌는가(허용 밖 토큰이나 IP가 하나라도 있으면 True)."""
        if _IPV4.search(name):
            return True
        return any(is_token and not self.allows(p) for p, is_token in split_name(name))


def parse_term_policy(doc: Any) -> TermPolicy:
    """`term_policy.yaml` 문서 → `TermPolicy`. 형식이 틀리면 `ValueError`(닫힌 쪽)."""
    if not isinstance(doc, Mapping) or not isinstance(doc.get("terms"), list):
        raise ValueError("term_policy: 최상위 `terms` 목록이 필요하다")
    terms: set[str] = set()
    for index, term in enumerate(doc["terms"]):
        if not isinstance(term, str) or not term.strip():
            raise ValueError(f"term_policy: terms[{index}] 빈 값·문자열 아님")
        pieces = split_name(term)
        if len(pieces) != 1 or not pieces[0][1]:
            raise ValueError(f"term_policy: terms[{index}] 토큰 하나가 아니다")
        if term.isdigit():
            raise ValueError(f"term_policy: terms[{index}] 숫자만인 항목은 받지 않는다")
        terms.add(_fold(term))
    return TermPolicy(frozenset(terms))


def load_term_policy(path: Path = TERM_POLICY_PATH) -> TermPolicy:
    """허용 목록 파일을 읽는다(없거나 형식이 틀리면 예외 — 닫힌 쪽)."""
    return parse_term_policy(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def term_risk_lines(policy: TermPolicy) -> list[str]:
    """`report.md`에 넣을 위험 표시 줄(허용 단어는 수만 — 단어 자체는 쓰지 않는다)."""
    return [
        TERM_RISK_NOTE,
        f"일반 단어 허용 목록 {len(policy.terms)}개 — 그 밖 이름 토큰은 전부 치환(기본 거부).",
    ]


def name_tokens(names: Iterable[str], policy: TermPolicy) -> set[str]:
    """치환 대상 이름들의 토큰 사전(소문자) — 모듈 독스트링 「토큰 사전」."""
    tokens: set[str] = set()
    for name in names:
        if len(name.strip()) < 2 or not policy.changes(name):
            continue
        run: list[str] = []  # 구분자 없이 대소문자 경계로만 이어진 라틴 토큰 연쇄
        for piece, is_token in [*split_name(name), ("", False)]:
            latin = is_token and piece.isascii() and piece.isalpha()
            if latin:
                run.append(piece)
            else:
                if len(run) >= 2:
                    tokens.add("".join(run).lower())
                run = []
            if not is_token or policy.allows(piece) or piece.isdigit() or len(piece) < 2:
                continue
            tokens.add(piece.lower())
    return tokens


def placeholder_kind(name: str) -> str:
    """자리표 이름(`inst_1`·`domain_id_1`) → 종류(`inst`·`domain_id`)."""
    head, _, tail = name.rpartition("_")
    return head if head and (tail.isdigit() or len(tail) == 1) else name


# --- 사전 정규식 ------------------------------------------------------------------------


def _trie_pattern(words: Iterable[str]) -> str:
    """낱말 목록 → 접두 트리 정규식(긴 쪽 우선 · 백트래킹으로 짧은 쪽도 시도)."""
    trie: dict[str, Any] = {}
    for word in words:
        node = trie
        for ch in word:
            node = node.setdefault(ch, {})
        node[""] = {}

    def build(node: dict[str, Any]) -> str:
        branches = [re.escape(ch) + build(child) for ch, child in sorted(node.items()) if ch]
        if not branches:
            return ""
        body = branches[0] if len(branches) == 1 else "(?:" + "|".join(branches) + ")"
        return f"(?:{body})?" if "" in node else body

    return build(trie)


def _dictionary_pattern(words: Iterable[str]) -> str | None:
    """사전 낱말(소문자) → 정규식 조각 — ASCII 낱말은 영숫자 경계, 그 밖(한글 등)은 경계 없음."""
    ascii_words = sorted({w for w in words if w.isascii()})
    other_words = sorted({w for w in words if not w.isascii()})
    parts = []
    if ascii_words:
        parts.append(
            rf"(?<![{_ASCII_ALNUM}])(?:{_trie_pattern(ascii_words)})(?![{_ASCII_ALNUM}])"
        )
    if other_words:
        parts.append(f"(?:{_trie_pattern(other_words)})")
    return "|".join(parts) if parts else None


def _names_regex(words: Iterable[str]) -> re.Pattern[str] | None:
    pattern = _dictionary_pattern(w.lower() for w in words if w)
    return re.compile(pattern, re.IGNORECASE) if pattern else None


# --- GUID 모양 생성기 ---------------------------------------------------------------------


class _HexIds:
    """GUID 모양 값의 16진 형식 보존 가짜 값(run 메모리 전용 · 2단계)."""

    def __init__(self) -> None:
        self.collecting = False
        self._originals: set[str] = set()
        self._memo: dict[str, str | None] = {}
        self._issued: set[str] = set()

    def __repr__(self) -> str:
        return f"<_HexIds 가짜 {len(self._issued)}건>"

    def fake(self, text: str) -> str:
        key = text.lower()
        if self.collecting:
            self._originals.add(key)
            return MASK
        if key not in self._memo:
            self._memo[key] = self._draw(key)
        out = self._memo[key]
        if out is None:
            return MASK
        return out.upper() if text.isupper() else out

    def _draw(self, key: str) -> str | None:
        self._originals.add(key)
        for _ in range(_MAX_DRAWS):
            candidate = "".join(
                ch if ch == "-" else secrets.choice("0123456789abcdef") for ch in key
            )
            if (
                candidate not in self._originals
                and candidate not in self._issued
                and not _pii_regex_hit(candidate)  # 관문 `pii_regex`에 걸릴 모양은 재추첨
            ):
                self._issued.add(candidate)
                return candidate
        return None

    def is_fake(self, text: str) -> bool:
        return text.lower() in self._issued


# --- 치환 ---------------------------------------------------------------------------------


class SanitizedRun(NamedTuple):
    """치환된 run — 원값은 없다(가짜 값 · 가림 표지만)."""

    records: list[TurnRecord]
    probe_rows: list[dict[str, Any]]
    run_meta: dict[str, Any]


@dataclass
class _Originals:
    names: set[str]
    ids: set[str]
    accounts: set[str]


def _strings(value: Any) -> list[str]:
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, (str, int)):
        text = str(value)
        return [text] if text.strip() and "{{" not in text and text != MASK else []
    if isinstance(value, (list, tuple)):
        return [s for item in value for s in _strings(item)]
    return []


def _target_names(value: Any) -> list[str]:
    """`targets`(문자열 목록 또는 `{text, candidates, selected}` 목록) → 이름."""
    out: list[str] = []
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, Mapping):
            for key in ("text", "name", "candidates", "selected"):
                out += _strings(item.get(key))
        else:
            out += _strings(item)
    return out


def _scan(node: Any, found: _Originals) -> None:
    """키 이름으로 원값을 모은다(재귀)."""
    if isinstance(node, Mapping):
        if node.get("field") == "targets":  # 정제 변경(SanitizeChange)의 대상 칸
            for side in ("before", "after"):
                found.names.update(_target_names(node.get(side)))
        for key, value in node.items():
            if key == "targets":
                found.names.update(_target_names(value))
            elif key in NAME_KEYS:
                found.names.update(_strings(value))
            elif key in ID_KEYS:
                found.ids.update(_strings(value))
            elif key in ACCOUNT_KEYS:
                found.accounts.update(_strings(value))
            _scan(value, found)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _scan(item, found)


def _placeholder_originals(placeholders: Mapping[str, Any], found: _Originals) -> None:
    for name, value in placeholders.items():
        kind = placeholder_kind(str(name))
        if kind in NAME_PLACEHOLDER_KINDS:
            found.names.update(_strings(value))
        elif kind in ID_PLACEHOLDER_KINDS:
            found.ids.update(_strings(value))
        elif kind in ACCOUNT_PLACEHOLDER_KINDS:
            found.accounts.update(_strings(value))


def collect_originals(
    records: Sequence[Mapping[str, Any]],
    *,
    inventory_names: Iterable[str],
    probe_rows: Sequence[Mapping[str, Any]] = (),
    placeholders: Mapping[str, Any] | None = None,
) -> tuple[set[str], set[str], set[str]]:
    """run 원값 전수 — (이름, GUID·txid, 계정).

    이름: 자리표(이름 종류) 값 · `observed.targets[].text/candidates/selected` ·
    `calls[].hostname` ·
    인자 이름 칸(`NAME_KEYS`) · `inventory_names`(드라이버가 넘기는 run 인벤토리 이름 전부).
    `placeholders`(자리표 파일 전체 — 관문과 같은 원값)를 주면 이번 run 레코드가 쓰지 않은 자리표
    값도 등록한다 — 다른 턴 값이 응답에만 나와도(예 목 프로파일 행의 URL) 치환된다.
    """
    found = _Originals(set(_strings(list(inventory_names))), set(), set())
    _placeholder_originals(placeholders or {}, found)
    for record in records:
        _placeholder_originals(record.get("placeholders") or {}, found)
        _scan({k: v for k, v in record.items() if k != "placeholders"}, found)
    for row in probe_rows:
        _scan(row, found)
    return found.names, found.ids, found.accounts


class RunSanitizer:
    """run 하나의 치환기 — 가짜 값 대응은 이 객체 메모리에만 있다(run 이 끝나면 버린다).

    Args:
        term_policy: 일반 단어 허용 목록(없으면 빈 목록 — 전부 치환)
        canaries: 카나리아 문자열 — 산출물에 나타나면 가짜 값으로 바꾸고(이중 방어) 생성기가 피한다
    """

    def __init__(
        self, *, term_policy: TermPolicy | None = None, canaries: Iterable[str] = ()
    ) -> None:
        self.policy = term_policy or TermPolicy()
        # 관문 `pii_regex`(연락처·계좌 모양 등)에 걸릴 가짜 값은 재추첨한다
        self.fakes = FakeValues(reject=_pii_regex_hit)
        self.vault = PiiVault(tuple(canaries), fakes=self.fakes)
        self.hex_ids = _HexIds()
        self.names: frozenset[str] = frozenset()
        self.ids: frozenset[str] = frozenset()
        self.accounts: frozenset[str] = frozenset()
        self.tokens: frozenset[str] = frozenset()
        self._kinds: dict[str, str] = {}
        self._struct_re: re.Pattern[str] = re.compile(r"(?!)")
        self._free_re: re.Pattern[str] = re.compile(r"(?!)")
        self._used = False

    def __repr__(self) -> str:
        return f"<RunSanitizer 이름 {len(self.names)}건 · ID {len(self.ids)}건>"

    # --- 진입 ---------------------------------------------------------------------------

    def sanitize(
        self,
        records: Sequence[TurnRecord],
        *,
        inventory_names: Iterable[str],
        probe_rows: Sequence[Mapping[str, Any]] = (),
        run_meta: Mapping[str, Any] | None = None,
        placeholders: Mapping[str, Any] | None = None,
    ) -> SanitizedRun:
        """run 전체를 2단계로 치환한다(입력은 바꾸지 않는다 · run 하나에 한 번만).

        ①원값 전수 수집 → ②`collecting()` 안에서 같은 경로를 돌려 원값만 등록 → ③발급.
        """
        if self._used:
            raise RuntimeError("RunSanitizer 는 run 하나에 한 번만 쓴다(2단계 등록 일관)")
        self._used = True
        names, ids, accounts = collect_originals(
            records, inventory_names=inventory_names, probe_rows=probe_rows,
            placeholders=placeholders,
        )
        self._index(names, ids, accounts)
        meta = dict(run_meta or {})
        with self._collecting():
            for name in sorted(names):
                self.fakes.fake(name)  # 이름 전체도 원값으로 등록(부분 문자열 대조)
                self._fake_name(name)
            for token in sorted(self.tokens):
                self.fakes.fake(token)  # 결합 소문자형(`ordpay`)도 원값 — 가짜 값이 피한다
            for value in sorted(ids):
                self._fake_id(value)
            self.fakes.register(sorted(accounts))
            self._run(records, probe_rows, meta)
        return SanitizedRun(*self._run(records, probe_rows, meta))

    @contextlib.contextmanager
    def _collecting(self) -> Iterator[None]:
        self.hex_ids.collecting = True
        try:
            with self.fakes.collecting():
                yield
        finally:
            self.hex_ids.collecting = False

    def _index(self, names: set[str], ids: set[str], accounts: set[str]) -> None:
        kinds: dict[str, str] = {}
        changed = {n for n in names if len(n.strip()) >= 2 and self.policy.changes(n)}
        tokens = name_tokens(changed, self.policy)
        self.tokens = frozenset(tokens)
        for word in tokens:
            kinds[word] = "token"
        for word in changed:
            kinds[word.lower()] = "name"
        for word in ids:
            kinds[word.lower()] = "id"
        for word in accounts:
            kinds[word.lower()] = "account"
        self._kinds = kinds
        self.names = frozenset(changed)
        self.ids = frozenset(ids)
        self.accounts = frozenset(accounts)
        struct = [w for w, k in kinds.items() if k != "token" and len(w) >= 2]
        self._struct_re = self._compile(struct)
        self._free_re = self._compile([w for w in kinds if len(w) >= 2])

    @staticmethod
    def _compile(words: list[str]) -> re.Pattern[str]:
        dictionary = _dictionary_pattern(words)
        parts = [
            f"(?P<secret>{_JWT}|{_BEARER})",
            f"(?P<sql>{_SQL})",
            f"(?P<uuid>{_UUID.pattern})",
        ]
        if dictionary:
            parts.append(f"(?P<word>{dictionary})")
        return re.compile("|".join(parts), re.IGNORECASE)

    # --- 값 단위 ------------------------------------------------------------------------

    def _fake_tokens(self, text: str) -> str:
        out = []
        for piece, is_token in split_name(text):
            if not is_token or self.policy.allows(piece):
                if is_token and self.hex_ids.collecting:
                    self.fakes.register([piece])  # 허용 단어도 원값 — 가짜 토큰이 피한다
                out.append(piece)
            else:
                out.append(self.fakes.fake(piece))
        return "".join(out)

    def _fake_name(self, text: str) -> str:
        """이름 → 토큰 단위 형식 보존 가짜 이름(IPv4는 계층 일관 가짜 IP)."""
        out, cursor = [], 0
        for match in _IPV4.finditer(text):
            out.append(self._fake_tokens(text[cursor : match.start()]))
            out.append(self.fakes.fake_ip(match.group(0)))
            cursor = match.end()
        out.append(self._fake_tokens(text[cursor:]))
        return "".join(out)

    def _fake_id(self, text: str) -> str:
        if _UUID.fullmatch(text):
            return self.hex_ids.fake(text)
        return self.fakes.fake(text)

    def _replace(self, match: re.Match[str]) -> str:
        text = match.group(0)
        if match.group("secret") is not None:
            return text.split()[0] + " " + MASK if text[:1] in "bB" else MASK
        if match.group("sql") is not None:
            return text.split()[0] + " " + SQL_MASK
        if match.group("uuid") is not None:
            return self.hex_ids.fake(text)
        kind = self._kinds.get(text.lower(), "name")
        if kind == "id":
            return self._fake_id(text)
        if kind == "account":
            return mask_identifier(text)
        return self._fake_name(text)

    def text(self, text: str, *, free: bool = False, prompt: str = "",
             tokens: bool | None = None) -> str:
        """문자열 하나 — 사전·비밀·SQL·GUID 구간은 바꾸고, 그 밖 구간만 ITAM 가림으로 넘긴다.

        `free`(자유 문장)면 나머지를 `redact_text`로, 아니면 `scrub_tree`로 넘긴다. `tokens`(기본 =
        `free`)면 이름 토큰까지 사전에 넣는다(아니면 원이름·ID·계정만). 이번 호출이 바꾼 구간은
        다시 처리하지 않는다(출처 기준).
        """
        regex = self._free_re if (free if tokens is None else tokens) else self._struct_re
        pieces, cursor = [], 0
        for match in regex.finditer(text):
            pieces.append(self._rest(text[cursor : match.start()], free, prompt))
            pieces.append(self._replace(match))
            cursor = match.end()
        pieces.append(self._rest(text[cursor:], free, prompt))
        return "".join(pieces)

    def _rest(self, segment: str, free: bool, prompt: str) -> str:
        if not segment:
            return segment
        if free:
            limit = len(segment) * 2 + ERROR_TEXT_MAX
            return redact_text(segment, vault=self.vault, prompt=prompt, limit=limit)
        return str(scrub_tree(segment, self.vault))

    def _prompt_basis(self, prompt: Any) -> str:
        """`redact_text` 프롬프트 근거 — 사전 구간을 지운 원 프롬프트(두 단계에서 같은 값)."""
        return self._free_re.sub(" ", str(prompt or ""))

    def tree(self, node: Any, *, prompt: str = "", tokens: bool = True,
             sentence: bool = False) -> Any:
        """중첩 구조 — 키는 원이름·ID·계정만, 문자열 값은 토큰 사전까지(`tokens`) 바꾼다. 문장 칸
        (`SENTENCE_KEYS`) 값은 `redact_text`까지 · 코드 어휘 칸(`SKIP_KEYS`)은 그대로 · 시나리오
        어휘 칸(`SCENARIO_KEYS`) 아래는 토큰 사전 없이."""
        if isinstance(node, Mapping):
            out: dict[Any, Any] = {}
            for key, value in node.items():
                new_key = self.text(key) if isinstance(key, str) else key
                if key in SKIP_KEYS:
                    out[new_key] = copy.deepcopy(value)
                    continue
                out[new_key] = self.tree(value, prompt=prompt,
                                         tokens=tokens and key not in SCENARIO_KEYS,
                                         sentence=key in SENTENCE_KEYS)
            return out
        if isinstance(node, (list, tuple)):
            return [self.tree(item, prompt=prompt, tokens=tokens, sentence=sentence)
                    for item in node]
        if isinstance(node, str):
            return self.text(node, free=sentence and tokens, prompt=prompt, tokens=tokens)
        return copy.deepcopy(node)

    # --- 레코드 -------------------------------------------------------------------------

    def _run(
        self,
        records: Sequence[TurnRecord],
        probe_rows: Sequence[Mapping[str, Any]],
        run_meta: Mapping[str, Any],
    ) -> tuple[list[TurnRecord], list[dict[str, Any]], dict[str, Any]]:
        out_records = [self._record(r) for r in records]
        out_probe = [self.tree(row) for row in probe_rows]
        meta: dict[str, Any] = {}
        for key, value in run_meta.items():
            if key in RUN_USER_FIELDS and value is not None:
                meta[key] = self.fakes.fake(str(value))  # 관문 `substitution`: 값 전체 가짜 값
            else:
                meta[key] = self.tree(value)
        return out_records, out_probe, meta

    def _record(self, record: TurnRecord) -> TurnRecord:
        prompt = self._prompt_basis(record.get("prompt"))
        out: dict[str, Any] = {}
        for key, value in record.items():
            if key in ("prompt", "prompt_template") and isinstance(value, str):
                out[key] = self.text(value, free=True, prompt=prompt)
            elif key == "observed" and isinstance(value, Mapping):
                out[key] = self._observed(value, bool(record.get("sensitive")), prompt)
            elif key in SKIP_KEYS:
                out[key] = copy.deepcopy(value)
            else:
                out[key] = self.tree(value, prompt=prompt, tokens=key not in SCENARIO_KEYS)
        return out  # type: ignore[return-value]

    def _observed(
        self, observed: Mapping[str, Any], sensitive: bool, prompt: str
    ) -> dict[str, Any]:
        source = dict(observed)
        if sensitive:
            source.update(sensitive_shape(source))
        out: dict[str, Any] = {}
        for key, value in source.items():
            if key in OBSERVED_FREE_TEXT and isinstance(value, str):
                out[key] = self.text(value, free=True, prompt=prompt)
            elif key == "answer_lines" and isinstance(value, list):
                out[key] = [self.text(str(line), free=True, prompt=prompt) for line in value]
            elif key == "disclosures" and isinstance(value, list):
                out[key] = [self._disclosure(item, prompt) for item in value]
            elif key in SKIP_KEYS:
                out[key] = copy.deepcopy(value)
            else:
                out[key] = self.tree(value, prompt=prompt)
        return out

    def _disclosure(self, item: Any, prompt: str) -> Any:
        if not isinstance(item, Mapping):
            return self.tree(item, prompt=prompt)
        return {
            key: self.text(value, free=True, prompt=prompt)
            if key == "text" and isinstance(value, str)
            else self.tree(value, prompt=prompt)
            for key, value in item.items()
        }


def sensitive_shape(observed: Mapping[str, Any]) -> dict[str, Any]:
    """민감 시나리오(환경변수·로드된 클래스·사용자 목록 계열) — 응답은 모양만(키 이름·건수).

    `response` → 표지(길이는 `response_len`) · `answer_lines` → 줄 수 유지, `키=값`·`키: 값`
    줄은 키만(`키=<가림>`), 그 밖 줄은 `<가림>`. 호출 결과는 원래 행 수·오류 유형만 싣는다.
    """
    out: dict[str, Any] = {}
    if "response" in observed:
        response = str(observed.get("response") or "")
        out["response"] = SENSITIVE_RESPONSE
        if "response_len" not in observed:
            out["response_len"] = len(response)
    if isinstance(observed.get("answer_lines"), list):
        lines = []
        for line in observed["answer_lines"]:
            match = _KEY_LINE.match(str(line))
            lines.append(f"{match.group(1)}={MASK}" if match else MASK)
        out["answer_lines"] = lines
    return out


# --- 근거 묶음 ----------------------------------------------------------------------------


def _v(container: Any, key: str) -> Any:
    """관측 칸 → 근거 값(칸 없음 = None · 비었음 = `NONE`)."""
    if not isinstance(container, Mapping) or key not in container:
        return None
    value = container[key]
    if value is None or value == "" or value == [] or value == {}:
        return NONE
    return value


def _or_none(value: Any) -> Any:
    return NONE if value in (None, "", [], {}) else value


def _per_call(calls: Any, pick: Any) -> Any:
    if calls is None:
        return None
    if calls == NONE:
        return NONE
    return _or_none([pick(i, c) for i, c in enumerate(calls) if isinstance(c, Mapping)])


def _llm_raw(plan_raw: Any) -> Any:
    if plan_raw is None or plan_raw == NONE:
        return plan_raw
    keys = ("agent", "views", "view_args", "targets", "sources")
    return [{k: t.get(k) for k in keys} for t in plan_raw if isinstance(t, Mapping)]


def _fewshot_copy(value: Any) -> Any:
    """`fewshot_copy` — 보기·예문 번호·인자만(예문 원문은 반출하지 않는다)."""
    if not isinstance(value, Mapping):
        return value
    return {k: value.get(k) for k in ("view", "example_index", "args")}


#: 실패 턴 근거 묶음 필수 칸(점 경로).
REQUIRED_EVIDENCE: tuple[str, ...] = (
    "question.prompt",
    "question.expect",
    "question.covers",
    "question.split",
    "decompose.apm_section.fingerprint",
    "decompose.apm_section.chars",
    "decompose.view_rows",
    "decompose.llm_raw",
    "sanitize.changes",
    "reselect",
    "target.texts",
    "target.candidates",
    "target.selected",
    "target.not_found",
    "window.sent_at",
    "window.resolved",
    "window.tool_args",
    "execution.tools",
    "execution.rows",
    "execution.empty",
    "execution.errors",
    "execution.probe",
    "response.answer_lines",
    "response.disclosures",
    "response.clarify_text",
    "response.length",
    "classification.categories",
    "classification.fewshot_fixable",
    "classification.locus",
    "classification.fewshot_copy",
    "versions.code_sha",
    "versions.F1",
    "versions.F2",
    "versions.F3",
    "versions.F4",
    "versions.registry",
)
#: 판정 불가 턴(턴 오류·관측 0)은 관측 칸이 원래 없다 — 질문·분류 사유·버전만 본다.
REQUIRED_EVIDENCE_UNJUDGED: tuple[str, ...] = (
    *(p for p in REQUIRED_EVIDENCE if p.startswith(("question.", "versions."))),
    "classification.unjudged",
)


def build_evidence(record: Mapping[str, Any], *, versions: Mapping[str, Any]) -> dict[str, Any]:
    """턴 하나의 근거 묶음(§3.8) — **치환을 거친 레코드**로 만든다.

    실패 턴은 전체(`level: full`), 통과 턴은 요약(`summary`), 건너뛴 턴은 사유만(`skipped`).

    Args:
        record: 치환된 `TurnRecord`(판정 `verdict`가 붙은 것)
        versions: `{"code_sha": SHA, "fingerprints": fingerprints.fewshot_fingerprints() 결과,
            "apm_section": {"fingerprint", "chars", "view_rows"}}`
    """
    verdict = record.get("verdict") or {}
    observed = record.get("observed") or {}
    prints = versions.get("fingerprints") or {}
    if verdict.get("skipped"):
        return {"level": "skipped", "reason": verdict.get("skipped")}
    calls = _v(observed, "calls")
    if verdict.get("turn_pass") is True:
        return {
            "level": "summary",
            "axes": verdict.get("axes"),
            "tools": _per_call(calls, lambda _i, c: c.get("tool")),
            "response_len": observed.get("response_len"),
            "elapsed_ms": observed.get("elapsed_ms"),
            "code_sha": versions.get("code_sha"),
        }
    expect = record.get("expect") or {}
    targets = _v(observed, "targets")
    target_list = targets if isinstance(targets, list) else []

    def per_target(key: str) -> Any:
        if targets is None or targets == NONE:
            return targets
        return [t.get(key) for t in target_list if isinstance(t, Mapping)]

    reselect = _v(observed, "reselect")
    if isinstance(reselect, Mapping):
        reselect = {
            "happened": reselect.get("happened"),
            "candidates": _or_none(reselect.get("candidates")),
            "parsed_views": _or_none(reselect.get("parsed_views")),
            "parsed_view_args": _or_none(reselect.get("parsed_view_args")),
            "output": _or_none(reselect.get("result_views")),
            "reason": _or_none(reselect.get("reason")),
        }
    disclosures = _v(observed, "disclosures")
    section = versions.get("apm_section") or {}
    answer_lines = observed.get("answer_lines")
    return {
        "level": "full",
        "question": {
            "prompt": record.get("prompt") or None,
            "expect": {
                "source": {"agents": expect.get("agents"),
                           "apm_sources": expect.get("apm_sources")},
                "views": expect.get("views"),
                "target": expect.get("target"),
                "window": expect.get("window"),
                "args": expect.get("args"),
                "clarify": expect.get("clarify"),
                "refusal": expect.get("refusal"),
            } if expect else None,
            "covers": _v(record, "covers"),
            "split": record.get("split") or None,
        },
        "decompose": {
            "apm_section": {"fingerprint": _v(section, "fingerprint"),
                            "chars": _v(section, "chars")},
            "view_rows": _v(section, "view_rows"),
            "llm_raw": _llm_raw(_v(observed, "plan_raw")),
            "agents": _v(observed, "agents"),
        },
        "sanitize": {"changes": _v(observed, "sanitize_changes")},
        "reselect": reselect,
        "target": {
            "texts": per_target("text"),
            "kinds": per_target("kind"),
            "candidates": per_target("candidates"),
            "selected": per_target("selected"),
            "not_found": None if targets is None else _or_none(
                [t.get("text") for t in target_list
                 if isinstance(t, Mapping) and t.get("resolved") is False]
            ),
            "untargeted": observed.get("untargeted"),
        },
        "window": {
            "sent_at": record.get("sent_at") or None,
            "resolved": _v(observed, "time_resolution"),
            "call_windows": _per_call(calls, lambda _i, c: c.get("window")),
            "tool_args": _per_call(calls, lambda _i, c: c.get("args")),
        },
        "execution": {
            "tools": _per_call(calls, lambda _i, c: c.get("tool")),
            "rows": _per_call(calls, lambda _i, c: c.get("rows")),
            "empty": None if calls is None else _or_none(
                [i for i, c in enumerate(calls if isinstance(calls, list) else [])
                 if isinstance(c, Mapping) and c.get("rows") == 0 and not c.get("error")]
            ),
            "errors": _per_call(calls, lambda _i, c: c.get("error") or NONE),
            "probe": _v(record, "probe"),
            "apm_called": observed.get("apm_called"),
            "turn_error": observed.get("error"),
        },
        "response": {
            "answer_lines": None if "answer_lines" not in observed else bool(answer_lines),
            "answer_line_count": len(answer_lines) if isinstance(answer_lines, list) else None,
            "disclosures": disclosures if not isinstance(disclosures, list) else [
                d.get("text") if isinstance(d, Mapping) else d for d in disclosures
            ],
            "clarify_kind": _v(observed, "clarify_kind"),
            "clarify_text": _v(observed, "clarify_text"),
            "refused": observed.get("refused"),
            "length": observed.get("response_len"),
            "degraded_reason": _v(observed, "degraded_reason"),
        },
        "classification": {
            "categories": _v(verdict, "categories"),
            "fewshot_fixable": _v(verdict, "fewshot_fixable"),
            "locus": _v(verdict, "locus"),
            "fewshot_copy": _fewshot_copy(_v(verdict, "fewshot_copy")),
            "axes": verdict.get("axes"),
            "unjudged": _v(verdict, "unjudged"),
        },
        "versions": {
            "code_sha": versions.get("code_sha") or None,
            **{key: _v(prints, key) for key in ("F1", "F2", "F3", "F4", "registry")},
        },
    }


def _at(node: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def check_evidence(records: Iterable[Mapping[str, Any]]) -> list[tuple[str, int, str]]:
    """실패 턴(통과·건너뜀 제외) 근거 묶음의 필수 칸 누락 — ``(scenario_id, turn, 칸)`` 목록.

    누락 = 칸 없음 · `null` · 빈 문자열(`"none"`·False·0은 값). 묶음이 `full`이 아니면 `level` 누락.
    """
    out: list[tuple[str, int, str]] = []
    for record in records:
        verdict = record.get("verdict") or {}
        if verdict.get("skipped") or verdict.get("turn_pass") is True:
            continue
        sid, turn = str(record.get("scenario_id")), int(record.get("turn") or 0)
        evidence = record.get("evidence") or {}
        if evidence.get("level") != "full":
            out.append((sid, turn, "level"))
            continue
        required = REQUIRED_EVIDENCE_UNJUDGED if verdict.get("unjudged") else REQUIRED_EVIDENCE
        for path in required:
            value = _at(evidence, path)
            if value is None or value == "":
                out.append((sid, turn, path))
    return out


# --- 관문 · 산출 --------------------------------------------------------------------------


class ApmLeakGate(LeakGate):
    """ITAM `LeakGate` + APM 규칙 — 같은 규칙 이름으로 보고한다(`GATE_RULES` 그대로).

    - `pii_value`: run 원이름(인벤토리·자리표 · 치환으로 바뀌는 것)·GUID/txid 원값(대소문자 무시 ·
      ASCII 는 영숫자 경계)
    - `user_info`: Bearer 토큰(JWT·계정은 기존 규칙)
    - `pii_regex`: 이 run 가짜 값이 아닌 GUID 모양(16진 8-4-4-4-12 · 32자리)
    - `pii_value`(토큰): `trace.jsonl`·`probe.jsonl`·`run.json`의 문자열 **값** 잎에 남은 이름 토큰
      (`name_tokens` — ASCII 는 영숫자 경계). 치환기와 같은 범위만 본다 — 키 · 코드 어휘 칸
      (`SKIP_KEYS`) · 시나리오 어휘 칸(`SCENARIO_KEYS`) 아래는 제외. `.md`는 원이름·ID·카나리아만
      대조한다(저장소 어휘 — 보기 id·경로 — 가 일반 토큰과 겹친다 · 본문은 치환된 레코드에서 만든다)
    """

    #: 토큰 대조 대상 파일(구조 파일 — 값 잎 단위).
    TOKEN_FILES: tuple[str, ...] = ("trace.jsonl", "probe.jsonl", "run.json")

    def __init__(
        self,
        *,
        policy: ColumnPolicy,
        vault: PiiVault,
        user_values: Mapping[str, str | None],
        fakes: FakeValues | None,
        originals: Iterable[str],
        hex_ids: _HexIds | None,
        tokens: Iterable[str] = (),
    ) -> None:
        super().__init__(policy=policy, vault=vault, user_values=user_values, fakes=fakes)
        self._originals = _names_regex(o for o in originals if len(o.strip()) >= 2)
        self._hex_ids = hex_ids
        self._tokens = _names_regex(tokens)

    def _token_leaves(self, node: Any, path: str = "", parent: str = ""
                      ) -> Iterable[str]:
        """토큰 대조 대상 값 잎의 경로(값은 돌려주지 않는다)."""
        if isinstance(node, Mapping):
            for position, (key, value) in enumerate(node.items()):
                if key in SKIP_KEYS or key in SCENARIO_KEYS:
                    continue
                label = self._label(str(key), position, parent)
                yield from self._token_leaves(value, f"{path}.{label}" if path else label,
                                              str(key))
        elif isinstance(node, (list, tuple)):
            for position, value in enumerate(node):
                yield from self._token_leaves(value, f"{path}[{position}]", parent)
        elif isinstance(node, str) and self._tokens is not None and self._tokens.search(node):
            yield path

    def check(self, files: Mapping[str, str]) -> list[dict[str, Any]]:
        violations = super().check(files)
        if self._tokens is None:
            return violations
        for name in self.TOKEN_FILES:
            text = files.get(name)
            if not text:
                continue
            if name.endswith(".jsonl"):
                units = [(n, line) for n, line in enumerate(text.splitlines(), start=1)
                         if line.strip()]
            else:
                units = [(None, text)]
            for number, body in units:
                try:
                    parsed = json.loads(body)
                except json.JSONDecodeError:
                    continue  # 형식 위반은 기존 규칙이 본다
                violations += [{"file": name, "record": number, "field": field or None,
                                "rule": "pii_value"} for field in self._token_leaves(parsed)]
        return violations

    def rules(self, text: str, *, schema_section: bool) -> list[str]:
        found = super().rules(text, schema_section=schema_section)
        if "pii_value" not in found and self._originals is not None and self._originals.search(
            text
        ):
            found.append("pii_value")
        if "user_info" not in found and _BEARER_RE.search(text):
            found.append("user_info")
        return found

    def leaf_rules(self, text: str, *, schema_section: bool) -> list[str]:
        found = super().leaf_rules(text, schema_section=schema_section)
        hex_ids = self._hex_ids
        if "pii_regex" not in found and any(
            hex_ids is None or not hex_ids.is_fake(m.group(0)) for m in _UUID.finditer(text)
        ):
            found.append("pii_regex")
        return found


def make_gate(
    *,
    inventory_names: Iterable[str],
    placeholders: Mapping[str, Any],
    user_values: Mapping[str, str | None],
    canaries: Iterable[str],
    sanitizer: RunSanitizer | None = None,
) -> ApmLeakGate:
    """누출 관문 — 카나리아 · run 인벤토리 원이름 전부·그 토큰 · 정규식 · 사용자 정보 대조.

    Args:
        inventory_names: run 인벤토리 이름 전부(드라이버가 게이트웨이 목록에서 모은 것)
        placeholders: 자리표 이름 → 원값(이름 종류 → 원이름 · `guid` → ID · `user` → 계정)
        user_values: 사용자 정보(로그인 계정·OS 사용자 등 — 3자 이상 부분 문자열 대조)
        canaries: 카나리아 문자열
        sanitizer: 이 run 치환기 — 가짜 값 등록부(IP·GUID 통과 판정 · 충돌 검사)와 치환기가 모은
            원값·허용 목록을 쓴다. 없으면 남은 IP·GUID 모양은 전부 위반(닫힌 쪽)
    """
    term_policy = sanitizer.policy if sanitizer is not None else TermPolicy()
    found = _Originals(set(_strings(list(inventory_names))), set(), set())
    _placeholder_originals(placeholders, found)
    if sanitizer is not None:
        found.names |= sanitizer.names
        found.ids |= sanitizer.ids
        found.accounts |= sanitizer.accounts
    tokens = name_tokens(found.names, term_policy)
    if sanitizer is not None:
        tokens |= sanitizer.tokens
    users: dict[str, str | None] = dict(user_values)
    for index, account in enumerate(sorted(found.accounts)):
        users[f"account#{index}"] = account
    canary_list = tuple(str(c) for c in canaries if str(c))
    return ApmLeakGate(
        policy=ColumnPolicy(db_id="apm", scope="apm_bench", tables={}, canary_literals=canary_list),
        vault=sanitizer.vault if sanitizer is not None else PiiVault(canary_list),
        user_values=users,
        fakes=sanitizer.fakes if sanitizer is not None else None,
        originals=[n for n in found.names if term_policy.changes(n)] + sorted(found.ids),
        hex_ids=sanitizer.hex_ids if sanitizer is not None else None,
        tokens=sorted(tokens),
    )


#: 산출 파일(관문 대상) — `leak_check.json`은 `write_outputs`가 쓴다.
OUTPUT_FILES: tuple[str, ...] = (
    "trace.jsonl", "run.json", "probe.jsonl", "coverage.md", "report.md",
)


def stage_outputs(
    records: Sequence[Mapping[str, Any]],
    run_meta: Mapping[str, Any],
    probe_rows: Sequence[Mapping[str, Any]],
    coverage_md: str,
    report_md: str,
) -> dict[str, str]:
    """치환된 산출물 → 파일 이름 → 본문(메모리 — 디스크에 닿기 전 관문이 본다)."""

    def jsonl(rows: Sequence[Mapping[str, Any]]) -> str:
        return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)

    return {
        "trace.jsonl": jsonl(records),
        "run.json": json.dumps(run_meta, ensure_ascii=False, indent=2) + "\n",
        "probe.jsonl": jsonl(probe_rows),
        "coverage.md": coverage_md,
        "report.md": report_md,
    }


def write_outputs(
    out_dir: Path, staged: Mapping[str, str], gate: LeakGate
) -> tuple[bool, list[dict[str, Any]]]:
    """관문 통과면 전부 + `leak_check.json`(통과 기록), 실패면 `leak_check.json`(위치만)만 쓴다.

    실패(False)면 호출 측이 exit 1 한다.
    """
    if "leak_check.json" in staged:
        raise ValueError("leak_check.json 은 관문이 쓴다")
    return write_gated(Path(out_dir), staged, gate)


__all__ = [
    "ACCOUNT_KEYS",
    "ApmLeakGate",
    "ID_KEYS",
    "NAME_KEYS",
    "NONE",
    "OUTPUT_FILES",
    "REQUIRED_EVIDENCE",
    "REQUIRED_EVIDENCE_UNJUDGED",
    "RunSanitizer",
    "SCENARIO_KEYS",
    "SENTENCE_KEYS",
    "SENSITIVE_RESPONSE",
    "SQL_MASK",
    "SanitizedRun",
    "TERM_POLICY_PATH",
    "TERM_RISK_NOTE",
    "TermPolicy",
    "build_evidence",
    "check_evidence",
    "collect_originals",
    "load_term_policy",
    "make_gate",
    "name_tokens",
    "parse_term_policy",
    "placeholder_kind",
    "sensitive_shape",
    "split_name",
    "stage_outputs",
    "term_risk_lines",
    "write_outputs",
]
