"""환각 수치 가드 — 서술 수치·날짜·시각을 근거 텍스트의 값과 대조한다(plans/198 I-4 · D-341 ④).

플레이북 소견기(`application/playbook_narrator.guard_text` ②)에 있던 결정적 가드를 그대로 옮긴
공용 모듈이다. 소견기와 HolmesGPT 브리핑의 값 대조(`domain/evidence_ledger`)가 같이 쓴다 —
규칙을 바꾸면 두 엔진이 함께 바뀐다.

- 허용 집합(`source_numbers`): 근거 텍스트의 모든 수(쉼표 제거) · 근거 있는 칸의 단위 환산 ·
  날짜·시각 문자열 전체.
- 대조(`unknown_numbers`): 같은 값을 그 수치의 소수 자릿수로 반올림한 값 ·
  천 단위 쉼표 · 부호 무시 · 「건·개·곳·대·위·순위·번째」가 붙은 10 이하 정수
  (개수·순위) 제외 · 식별자 일부(`p95`·`S3`)는 수치로 보지 않는다.
- 단위 환산은 근거가 있는 칸에만: `*_ms`(÷1000 ms→초) · `*_s`(×1000) ·
  `*ratio*`·`share`(×100 비율→%) · `*_pct`(÷100).
- 날짜·시각은 각 자리 숫자가 아니라 문자열 전체로만 허용한다
  (`2026-10-19`·`07:20` — 문장 쪽 `07시 20분` 포함).

표준 라이브러리만 쓴다(domain 순수).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: 허용 집합 쪽 수치 — 넓게 잡는다(`T07:20`의 07 등).
_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
#: 소견 문장 쪽 수치 토큰 — 식별자 일부(`p95`·`S3`)는 수치가 아니다.
_CLAIM_NUMBER = re.compile(r"(?<![A-Za-z_\d.])\d+(?:,\d{3})*(?:\.\d+)?")
_COUNT_SUFFIX = re.compile(r"\s*(건|개|곳|대|위|순위|번째)")
#: 날짜·시각 문자열(허용 집합 쪽 — 각 자리 숫자로 분해하지 않는다).
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TIME = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?::(\d{2}))?(?![\d:])")
#: 소견 문장 쪽 시각(`07:20` · `07시 20분`).
_CLAIM_TIME = re.compile(r"(?<![\d:])(\d{1,2})(?::|시\s*)(\d{2})(?::\d{2}|분)?(?![\d:])")
#: `이름 값`·`"이름":값` 꼴의 앞 이름(단위 환산 근거). 앞 lookbehind는 식별자 중간에서 다시 시작하지
#: 않게 하는 가속일 뿐 일치 집합은 같다 — 앞자리에서 실패한 이름은 그 뒤 꼬리에서도 실패한다.
_LABELED_NUMBER = re.compile(
    r"(?<![A-Za-z_])([A-Za-z_][A-Za-z0-9_]*)\"?\s*[:=]?\s*(-?\d+(?:,\d{3})*(?:\.\d+)?)(?![\d.])"
)


@dataclass(frozen=True)
class SourceNumber:
    """허용 수치 1개 — 단위 환산 근거가 있는 칸이면 `factors`(값에 곱해 허용할 배수)."""

    value: float
    factors: tuple[float, ...] = ()


#: 허용 집합 원소 — 수(정확 일치만) · `SourceNumber`(근거 있는 환산 포함) · 날짜·시각 문자열.
AllowedItem = float | SourceNumber | str


def _unit_factors(labels: Sequence[str]) -> tuple[float, ...]:
    """칸 이름들 → 허용 환산 배수(ms↔s · 비율↔%). 근거 없으면 빈 튜플."""
    out: set[float] = set()
    for raw in labels:
        label = raw.lower()
        if label == "ms" or label.endswith("_ms"):
            out.add(1 / 1000)
        if label.endswith(("_s", "_sec", "_seconds")):
            out.add(1000.0)
        if "ratio" in label or label == "share" or label.endswith("_share"):
            out.add(100.0)
        if label == "pct" or label.endswith("_pct") or "percent" in label:
            out.add(1 / 100)
    return tuple(sorted(out))


def _walk_json(value: Any, path: tuple[str, ...], out: list[AllowedItem]) -> None:
    """JSON 값의 수에 키 경로(표는 열 이름 + 같은 행의 문자열 칸)로 환산 근거를 붙인다."""
    if isinstance(value, Mapping):
        cols, rows = value.get("columns"), value.get("rows")
        if isinstance(cols, list) and isinstance(rows, list):
            for row in rows:
                if not isinstance(row, list):
                    continue
                names = [c for c in row if isinstance(c, str)]
                for col, cell in zip(cols, row):
                    _walk_json(cell, (*path, str(col), *names), out)
        for k, v in value.items():
            if k not in ("columns", "rows"):
                _walk_json(v, (*path, str(k)), out)
    elif isinstance(value, list):
        for v in value:
            _walk_json(v, path, out)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        factors = _unit_factors(path)
        if factors:
            out.append(SourceNumber(float(value), factors))


def _norm_time(h: str, m: str) -> str:
    return f"{int(h):02d}:{int(m):02d}"


def source_numbers(text: str) -> list[AllowedItem]:
    """허용 수치 집합 — 입력 텍스트의 모든 수(쉼표 제거) + 근거 있는 칸의 환산 + 날짜·시각 문자열.

    날짜·시각은 문자열 전체로만 싣고 그 자리 숫자는 수로 넣지 않는다(검증 지적 7).
    """
    raw = text or ""
    out: list[AllowedItem] = []

    def _date(m: re.Match[str]) -> str:
        out.append(m.group())
        return " " * len(m.group())

    def _time(m: re.Match[str]) -> str:
        out.append(_norm_time(m.group(1), m.group(2)))
        return " " * len(m.group())

    # 한 번에 치환한다(일치마다 문자열을 다시 만들면 대형 출력에서 제곱 시간 — 결과는 같다).
    blanked = _TIME.sub(_time, _DATE.sub(_date, raw))
    out.extend(float(m.group().replace(",", "")) for m in _NUMBER.finditer(blanked))
    for m in _LABELED_NUMBER.finditer(blanked):
        factors = _unit_factors([m.group(1)])
        if factors:
            out.append(SourceNumber(float(m.group(2).replace(",", "")), factors))
    decoder = json.JSONDecoder()
    pos = raw.find("{")
    while pos != -1:
        try:
            obj, end = decoder.raw_decode(raw, pos)
        except ValueError:
            pos = raw.find("{", pos + 1)
            continue
        _walk_json(obj, (), out)
        pos = raw.find("{", end)
    return out


def _decimals(token: str) -> int:
    return len(token.split(".")[1]) if "." in token else 0


def number_allowed(token: str, allowed: Sequence[AllowedItem]) -> bool:
    """수치 토큰 1개가 허용 집합의 값(반올림 · 근거 있는 칸의 단위 환산 포함)과 맞는가."""
    x = float(token.replace(",", ""))
    d = _decimals(token)
    tol = 0.5 * 10 ** (-d) + 1e-9
    for item in allowed:
        if isinstance(item, str):
            continue
        if isinstance(item, SourceNumber):
            cands: tuple[float, ...] = (item.value, *(item.value * f for f in item.factors))
        else:
            cands = (float(item),)
        if any(abs(abs(c) - x) <= tol for c in cands):
            return True
    return False


def _mask_known_times(sentence: str, allowed: Sequence[AllowedItem]) -> str:
    """허용 집합에 문자열 전체로 있는 날짜·시각은 수치 검사에서 뺀다(공백으로 가림)."""
    texts = {a for a in allowed if isinstance(a, str)}
    if not texts:
        return sentence
    out = sentence
    for m in _DATE.finditer(sentence):
        if m.group() in texts:
            out = out[: m.start()] + " " * len(m.group()) + out[m.end() :]
    for m in _CLAIM_TIME.finditer(out):
        if _norm_time(m.group(1), m.group(2)) in texts:
            out = out[: m.start()] + " " * len(m.group()) + out[m.end() :]
    return out


def claim_number_tokens(sentence: str, allowed: Sequence[AllowedItem]) -> list[tuple[str, bool]]:
    """문장의 대조 대상 토큰과 허용 여부 `(토큰, 허용됨)`.

    허용 집합에 있는 날짜·시각은 문자열 하나로 센다. 개수·순위 접미의 10 이하 정수는 대상이
    아니다. `unknown_numbers`와 같은 판정이다.
    """
    out: list[tuple[str, bool]] = []
    masked = _mask_known_times(sentence, allowed)
    texts = {a for a in allowed if isinstance(a, str)}
    out.extend((m.group(), True) for m in _DATE.finditer(sentence) if m.group() in texts)
    out.extend(
        (m.group(), True)
        for m in _CLAIM_TIME.finditer(sentence)
        if _norm_time(m.group(1), m.group(2)) in texts
    )
    for m in _CLAIM_NUMBER.finditer(masked):
        token = m.group()
        if (
            "." not in token
            and float(token.replace(",", "")) <= 10
            and _COUNT_SUFFIX.match(masked, m.end())
        ):
            continue
        out.append((token, number_allowed(token, allowed)))
    return out


def unknown_numbers(sentence: str, allowed: Sequence[AllowedItem]) -> list[str]:
    """문장에서 허용 집합에 없는 수치 토큰(개수·순위 접미의 10 이하 정수는 제외)."""
    return [token for token, ok in claim_number_tokens(sentence, allowed) if not ok]


__all__ = [
    "AllowedItem",
    "SourceNumber",
    "claim_number_tokens",
    "number_allowed",
    "source_numbers",
    "unknown_numbers",
]
