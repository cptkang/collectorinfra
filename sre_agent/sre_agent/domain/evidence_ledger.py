"""증거 원장과 서술 값 대조 (plans/198 I-4 · 154 H-4 · D-341 ④).

잡마다 원장 하나를 만든다. 행은 도구 호출 1건 — `{ev_id, tool, args, 수치·식별자 추출, 시각}`이다.
서술(HolmesGPT 답변) 한 줄의 수치·시각·호스트명 토큰을 결정적으로 뽑아 원장과 맞대고, 원장에 없는
값을 돌려준다. LLM 판정은 없다.

- 수치·날짜·시각: 공용 환각 수치 가드(`domain/value_guard` — 플레이북 소견기와 같은 규칙)로
  대조한다.
  정확 일치 · 그 수치의 소수 자릿수 반올림 · 근거 있는 칸의 단위 환산(ms↔초 · 비율↔%) 허용.
- 호스트명: 영문자로 시작하고 숫자가 섞인 4자 이상 토큰(밑줄이 붙은 식별자 · `p95`·`ev12` 같은 짧은
  표기 · 버전 `v1.2` · 상대시각 `T-15m`은 제외)을 호스트 후보로 본다. 소문자·FQDN 첫 레이블로
  정규화한 이름이 원장의 식별자이거나 원장 텍스트에 단어 경계로 나오면 일치다.
  정규화 규칙은 본체 `src/domain/host_names.normalize_hostname`의 최소 사본이다 — sre_agent는
  본체를 import하지 않는다(패키지 경계 · `tests/test_boundary.py`).
  원장에 없는 후보는 **원장에 같은 꼴의 이름이 있을 때만** 불일치로 센다 — 꼴(`host_stem`)은
  정규화 이름에서 영문자만 남긴 것(`web-07`·`web01` → `web`)이다. 같은 꼴이 없으면(`IPv4`·
  `TLSv1.2`·`G1GC`·`eth0`·`log4j`·`CVE-…` 같은 기술 용어) 대조 불가로 보고 대조 수에서 뺀다
  — 정상 서술이 기술 용어 때문에 [가설]로 강등되지 않게 하고, 원장 호스트와 같은 접두의 다른
  호스트명 환각은 계속 잡는다.

원장 행은 원시 텍스트를 들고 있지 않는다 — 대조용으로는 「단어 경계 이름」 색인(`boundary_names`)만
만들어 두고 집합 조회로 맞댄다(재검토 Minor4 — 대형 출력 × 토큰 정규식 탐색 제거). 브리핑 요약
(`ledger_summary`)은 `ev_id`·도구·인자(상한)만이다.

표준 라이브러리만 쓴다(domain 순수).
"""

from __future__ import annotations

import bisect
import ipaddress
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from sre_agent.domain.activity_log import clip_bytes
from sre_agent.domain.value_guard import (
    AllowedItem,
    SourceNumber,
    claim_number_tokens,
    source_numbers,
)

#: 호스트 후보 토큰 — 앞뒤가 영숫자·밑줄·하이픈·점이 아닌 자리의 `이름(-이름)*(.이름)*`.
_HOST = re.compile(
    r"(?<![A-Za-z0-9_.\-])[A-Za-z][A-Za-z0-9\-]*(?:\.[A-Za-z0-9\-]+)*(?![A-Za-z0-9_\-])"
)
#: 호스트로 보지 않는 짧은 식별자(`p95`·`S3`·`ev12`) · 버전(`v1.2`) · 상대시각(`T-15m`).
_NOT_HOST = re.compile(r"^(?:[A-Za-z]{1,2}\d+|v\d[\d.]*|T[-+]\d+m)$", re.IGNORECASE)
_HOST_MIN_LEN = 4
#: 서술 줄 앞 목록 머리표(`- `·`1. `)와 마크다운 제목·굵게 번호(`### 4.`·`**3)`) — 번호를 수치로
#: 세지 않는다.
_BULLET = re.compile(r"^\s*(?:#+\s*|\*\*)?(?:[-*•·]|\d+[.)])(?:\*\*)?\s+")
#: 단어 경계 색인 — 이름 문자 덩어리(소문자 원문의 `[a-z0-9.-]` 연속)와 그 안의 구분자.
_NAME_RUN = re.compile(r"[a-z0-9.\-]+")
_SEPARATORS = ".-"
_HAS_LETTER = re.compile(r"[a-z]")
#: 덩어리 하나에서 한 이름이 걸칠 수 있는 최대 조각 수(구분자 기준) — 비정상적으로 긴 덩어리
#: (`a-b-c-…`)의 색인이 제곱으로 커지지 않게 한다. 호스트명은 이보다 훨씬 짧다.
_MAX_NAME_PARTS = 32
#: 서술 줄의 수치 후보 — 숫자·쉼표·점 덩어리(그 안의 수치 부분 문자열을 모두 후보로 본다).
_NUMBER_RUN = re.compile(r"[\d.,]+")
_NUMBER_FULL = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
#: 덩어리가 이보다 길면 후보 열거를 그만두고 허용 집합 전체로 대조한다(결과는 같다 · 느릴 뿐).
_NUMBER_RUN_MAX = 40
#: 수치 대조 허용 오차의 상한(`value_guard.number_allowed` — 정수 토큰의 0.5 + 여유).
_TOL_MAX = 0.5 + 1e-6

#: 브리핑 요약 상한 — 행 수 · 인자 1건 바이트(noise_gate 저장 상한 32KB 안에 들게).
SUMMARY_MAX_ROWS = 30
SUMMARY_MAX_ARGS_BYTES = 160


def normalize_host(value: str) -> str:
    """소문자 · 끝 점 제거 · FQDN이면 첫 레이블(IPv4는 그대로).

    `src/domain/host_names.normalize_hostname`의 최소 사본(패키지 경계로 import 불가).
    """
    text = str(value or "").strip().lower().rstrip(".")
    if not text:
        return text
    try:
        ipaddress.IPv4Address(text)
        return text
    except ValueError:
        return text.split(".", 1)[0]


def host_stem(value: str) -> str:
    """호스트 꼴 — 정규화 이름에서 영문자만(`web-01.corp` → `web` · 숫자·구분자 제거)."""
    return re.sub(r"[^a-z]", "", normalize_host(value))


def host_tokens(text: str) -> list[str]:
    """텍스트의 호스트 후보 토큰(원문 표기 · 등장 순서 · 중복 제거)."""
    out: dict[str, None] = {}
    for m in _HOST.finditer(text or ""):
        token = m.group()
        if (
            token in out
            or len(token) < _HOST_MIN_LEN
            or not any(c.isdigit() for c in token)
            or _NOT_HOST.match(token)
        ):
            continue
        out[token] = None
    return list(out)


def boundary_names(text: str) -> frozenset[str]:
    """소문자 텍스트에서 앞뒤가 영숫자가 아닌 자리에 놓인, 영문자로 시작하는 모든 부분 문자열.

    `name in boundary_names(t)`는 `re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", t)`와
    같은 판정이다(`name`이 `[a-z0-9.-]`로만 되고 영문자로 시작할 때 — 호스트 후보의 정규화 이름).
    이름은 한 덩어리(`[a-z0-9.-]` 연속) 안에만 놓이므로, 덩어리 처음 또는 구분자 바로 뒤에서 시작해
    덩어리 끝 또는 구분자 바로 앞에서 끝나는 조각을 모으면 된다(조각 수 `_MAX_NAME_PARTS` 상한).
    """
    out: set[str] = set()
    for run in set(_NAME_RUN.findall(text)):   # 같은 덩어리(JSON 키 등)는 한 번만
        if not _HAS_LETTER.search(run):
            continue
        n = len(run)
        starts = [
            i for i in range(n)
            if (i == 0 or run[i - 1] in _SEPARATORS) and "a" <= run[i] <= "z"
        ]
        ends = [j for j in range(1, n + 1) if j == n or run[j] in _SEPARATORS]
        for i in starts:
            parts = 0
            for j in ends:
                if j <= i:
                    continue
                out.add(run[i:j])
                parts += 1
                if parts >= _MAX_NAME_PARTS:
                    break
    return frozenset(out)


def _args_text(args: Any) -> str:
    if isinstance(args, Mapping):
        return json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    return str(args or "")


@dataclass(frozen=True)
class LedgerRow:
    """원장 행 1건 = 도구 호출 1건(또는 코드가 주입한 근거 1건 — 사전수집 상관·알람)."""

    ev_id: str
    tool: str
    args: Any
    numbers: tuple[AllowedItem, ...]
    identifiers: frozenset[str]
    at: float | None = None
    #: 대조용 단어 경계 이름 색인(`boundary_names` — 출력 + 인자) — 브리핑에 싣지 않는다.
    names: frozenset[str] = frozenset()


def make_row(ev_id: str, tool: str, args: Any, output: str, at: float | None = None) -> LedgerRow:
    """출력·인자에서 수치·식별자를 결정적으로 추출해 행을 만든다."""
    body = f"{output or ''}\n{_args_text(args)}"
    idents = frozenset(
        name for token in host_tokens(body) for name in (token.lower(), normalize_host(token))
    )
    return LedgerRow(
        ev_id=ev_id, tool=tool, args=args, numbers=tuple(source_numbers(body)),
        identifiers=idents, at=at, names=boundary_names(body.lower()),
    )


@dataclass(frozen=True)
class EvidenceLedger:
    """잡 1건의 증거 원장 — 행 순서는 호출 순서, `ev_id`는 `ev1`부터."""

    rows: tuple[LedgerRow, ...] = ()

    @cached_property
    def allowed(self) -> list[AllowedItem]:
        """전 행의 허용 수치 합집합(공용 가드 입력 · 중복 제거 · 처음 1회만 계산)."""
        return list(dict.fromkeys(n for r in self.rows for n in r.numbers))

    @cached_property
    def _number_index(self) -> tuple[list[float], list[AllowedItem], list[AllowedItem]]:
        """(정렬한 비교 후보 절댓값, 그 원소, 문자열 원소) — 줄마다 근처 원소만 골라 대조한다."""
        pairs: list[tuple[float, int]] = []
        items = self.allowed
        texts: list[AllowedItem] = []
        for k, item in enumerate(items):
            if isinstance(item, str):
                texts.append(item)
                continue
            if isinstance(item, SourceNumber):
                cands: tuple[float, ...] = (item.value, *(item.value * f for f in item.factors))
            else:
                cands = (float(item),)
            pairs.extend((abs(c), k) for c in cands)
        pairs.sort()
        return [v for v, _ in pairs], [items[k] for _, k in pairs], texts

    def allowed_for(self, line: str) -> list[AllowedItem]:
        """서술 한 줄 대조에 필요한 허용 원소만(문자열 전부 + 줄의 수치 후보 근처 수치).

        `claim_number_tokens(line, ledger.allowed_for(line))`는 전체 허용 집합으로 대조한 결과와
        같다 — 수치 판정은 오차(≤ 0.5) 안의 원소만 보고, 줄의 대조 토큰은 모두 후보에 든다.
        """
        cands: set[float] = set()
        for m in _NUMBER_RUN.finditer(line):
            run = m.group()
            if len(run) > _NUMBER_RUN_MAX:
                return self.allowed
            for i in range(len(run)):
                if not run[i].isdigit():
                    continue
                for j in range(i + 1, len(run) + 1):
                    if _NUMBER_FULL.fullmatch(run, i, j):
                        cands.add(float(run[i:j].replace(",", "")))
        values, owners, texts = self._number_index
        picked: dict[int, AllowedItem] = {}
        for x in cands:
            lo = bisect.bisect_left(values, x - _TOL_MAX)
            hi = bisect.bisect_right(values, x + _TOL_MAX)
            for k in range(lo, hi):
                picked[id(owners[k])] = owners[k]
        return [*texts, *picked.values()]

    @cached_property
    def _known_names(self) -> frozenset[str]:
        """전 행의 식별자 ∪ 단어 경계 이름 색인(처음 1회만 계산)."""
        return frozenset().union(*(r.identifiers | r.names for r in self.rows))

    @cached_property
    def _stems(self) -> frozenset[str]:
        return frozenset(host_stem(i) for r in self.rows for i in r.identifiers)

    def host_stems(self) -> frozenset[str]:
        """원장 식별자의 호스트 꼴 집합(`host_stem`)."""
        return self._stems

    def host_known(self, token: str) -> bool:
        """호스트 후보가 원장의 식별자이거나 원장 텍스트에 단어 경계로 나오는가(원문·짧은 이름)."""
        names = {token.lower(), normalize_host(token)} - {""}
        return bool(names & self._known_names)


def build_ledger(
    records: Sequence[object],
    *,
    extra: Sequence[tuple[str, Any]] = (),
    times: Sequence[float | None] | None = None,
) -> EvidenceLedger:
    """도구 호출 기록으로 원장을 만든다(`ToolCallRecord` 꼴 — `tool_name`·`output`·`params`·
    `description`).

    `params`가 dict면 인자로, 없으면 `description`(holmes 호출 요약)을 인자 자리에 둔다.
    `extra`는 코드가 주입한 근거 `(이름, 값)` — 사전수집 상관·알람 페이로드처럼 서술이 인용할 수
    있는 결정적 입력이다(값은 JSON으로 펼쳐 추출). `times`는 행 시각(호출 순서 · 없으면 None).
    """
    rows: list[LedgerRow] = []
    for i, rec in enumerate(records):
        params = getattr(rec, "params", None)
        args = params if isinstance(params, dict) else str(getattr(rec, "description", "") or "")
        at = times[i] if times is not None and i < len(times) else None
        rows.append(make_row(f"ev{i + 1}", str(getattr(rec, "tool_name", "") or ""), args,
                             str(getattr(rec, "output", "") or ""), at))
    for name, value in extra:
        text = (value if isinstance(value, str)
                else json.dumps(value, ensure_ascii=False, default=str))
        rows.append(make_row(f"ev{len(rows) + 1}", name, {}, text))
    return EvidenceLedger(tuple(rows))


@dataclass(frozen=True)
class ClaimCheck:
    """서술 한 줄의 대조 결과 — `checked`(대조한 토큰 수) · `unmatched`(원장에 없는 값)."""

    checked: int
    unmatched: tuple[str, ...]

    @property
    def verified(self) -> bool:
        """값이 하나 이상 있고 모두 원장에 있으면 참."""
        return self.checked > 0 and not self.unmatched


def check_claim(line: str, ledger: EvidenceLedger) -> ClaimCheck:
    """서술 한 줄의 호스트·수치·시각 토큰을 원장과 대조한다(목록 머리표 제외).

    원장에 없고 원장에 같은 꼴의 이름도 없는 호스트 후보는 대조 불가로 대조 수에서 뺀다(기술 용어).
    """
    text = _BULLET.sub("", line or "")
    unmatched: list[str] = []
    checked = 0
    masked = text
    stems: frozenset[str] | None = None
    for token in host_tokens(text):
        if ledger.host_known(token):
            checked += 1
        else:
            if stems is None:
                stems = ledger.host_stems()
            if host_stem(token) in stems:
                checked += 1
                unmatched.append(token)
        # 호스트 안의 숫자(`web-01`의 01)를 수치로 다시 세지 않는다.
        masked = re.sub(rf"(?<![A-Za-z0-9_.\-]){re.escape(token)}(?![A-Za-z0-9_\-])",
                        " " * len(token), masked)
    for token, ok in claim_number_tokens(masked, ledger.allowed_for(masked)):
        checked += 1
        if not ok:
            unmatched.append(token)
    return ClaimCheck(checked, tuple(dict.fromkeys(unmatched)))


def ledger_summary(
    ledger: EvidenceLedger,
    *,
    masker: Callable[[str], str] = lambda s: s,
    max_rows: int = SUMMARY_MAX_ROWS,
    max_args_bytes: int = SUMMARY_MAX_ARGS_BYTES,
) -> list[dict[str, str]]:
    """브리핑 싣기용 요약 — `{ev_id, tool, args}`만(인자는 가림 뒤 바이트 상한 · 행 상한)."""
    return [
        {"ev_id": r.ev_id, "tool": r.tool,
         "args": clip_bytes(masker(_args_text(r.args)), max_args_bytes)}
        for r in ledger.rows[:max_rows]
    ]


__all__ = [
    "SUMMARY_MAX_ARGS_BYTES",
    "SUMMARY_MAX_ROWS",
    "ClaimCheck",
    "EvidenceLedger",
    "LedgerRow",
    "boundary_names",
    "build_ledger",
    "check_claim",
    "host_stem",
    "host_tokens",
    "ledger_summary",
    "make_row",
    "normalize_host",
]
