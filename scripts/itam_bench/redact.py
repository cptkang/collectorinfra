"""로그 위생 계층 — 값 기록 정책 · SQL·문구 가림 · 사용자 정보 가림 · 누출 관문.

plans/135 §3.5 · W2 · D-301.

**허락된 것만 남긴다(기본 거부).** 「찾아서 가리기」가 아니다 — 운영 ITAM 은 108테이블이고 대부분의
컬럼 의미를 모른다. 그래서:

- 결과 값: 검토된 `general` 컬럼만 값(표본 5 · 범주형 상위 10)을 남기고, 사람 정보(`pii`)는 건수만,
  서술형·미분류는 건수·길이만, 금액은 합계·최소·최대만, IP 는 끝자리를 가린다. 해시도 남기지 않는다
  (이름은 경우의 수가 작아 역산된다). 결과 열의 원 컬럼은 **별칭 식이 먼저**이고 실행 SQL 전부의
  합집합에서 가장 엄격한 등급을 쓴다. 열 이름(별칭)도 값일 수 있어 근거가 있을 때만 남긴다.
- SQL: 주석·문자열·백틱을 한 번에 토큰으로 나눈다(주석 속 따옴표가 짝을 뒤집지 않게). 주석은 가리고,
  리터럴은 남길 근거(그 턴 프롬프트의 말 · 일반 컬럼 비교 · 날짜/짧은 수 · 날짜 서식 · 식별자
  따옴표)가 있고 같은 술어에 사람·서술형 컬럼이 없을 때만 남긴다. 따옴표 없는 비ASCII 낱말도
  프롬프트에 없으면 가린다. IP 는 어디서든 끝자리를 가린다. SQL 구조(테이블·컬럼·조건 형태)는
  그대로다.
- 결과에서 본 사람 값은 **메모리에만** 모아(`PiiVault`) 다른 칸·SQL·오류 문구에 나타나면 가린다.
- 누출 관문(`LeakGate`)이 산출물 전부를 디코드한 값 단위로 다시 훑는다 — 실패하면 산출물을 쓰지 않고
  위치만 적는다(값일 수 있는 키는 경로에 순번으로). 치환 코드값 파일(`code_samples.yaml`)에는 「원
  코드값 출현 0」 규칙(`code_original`)을 더한다 — 원 집합은 메모리에서만 넘긴다(plans/140 W2-5).

정규식은 중첩 수량자 없는 패턴만 쓴다(docs/18 2026-08-19 ReDoS 사례). `scan_pii`의 이메일 규칙 제곱
시간(20KB 한 줄 5.7초)은 제품 쪽에서 고쳤다(plans/135 v1.4 · `ChainStartSearch`).
"""

from __future__ import annotations

import bisect
import json
import os
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml

from .catalog import SCHEMA_QUERY_FORMS, ColumnPolicy, pii_suggestion, strictest

MASK = "<가림>"
MASK_PII = "<가림:pii>"
SAMPLE_ROWS = 5
TOP_VALUES = 10
ERROR_TEXT_MAX = 500
#: 실행 SQL 입력 상한(문자) — 넘는 꼬리는 기록하지 않는다.
INPUT_MAX = 20_000
#: 이 길이 미만 값은 수집·대조하지 않는다(한 글자 대조는 무관한 글자를 다 가린다).
MIN_VALUE_LEN = 2
#: 숫자만인 값은 더 길어야 수집한다(`1006` 이 run_id·`LIMIT 1000`과 겹친다).
MIN_DIGIT_VALUE_LEN = 5
#: 결과 열 해석 표지 — 원 컬럼을 모르는 식 · 컬럼 없는 계산식(COUNT(*) 등).
UNKNOWN = "?"
COMPUTED = "#computed"

_IPV4 = re.compile(r"(?<![\d.])(\d{1,3}\.\d{1,3}\.\d{1,3})\.\d{1,3}(?![\d.])")
#: SQL 토큰 — 주석 · 작은따옴표 · 큰따옴표(MariaDB 기본 모드에서는 문자열) · 백틱. 왼쪽부터 한 번에.
_SQL_TOKEN = re.compile(
    r"/\*.*?\*/|--[^\n]*|#[^\n]*|'(?:[^'\\]|\\.|'')*'|\"(?:[^\"\\]|\\.|\"\")*\"|`(?:[^`]|``)*`",
    re.S,
)
#: 술어 조각 경계(리터럴이 어느 컬럼과 같은 조건에 있는가를 볼 때).
_BOUNDARY = re.compile(r"(?i)\b(?:and|or|where|on|having|when|then|else|select|from|set|join)\b|;")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NON_ASCII_WORD = re.compile(r"[^\x00-\x7F\s]+")
#: 남겨도 되는 수·날짜 모양 — 짧은 수(4자리 이하)·날짜·연월·시각. 7자리 사번 모양은 남기지 않는다.
_SAFE_NUMBERISH = re.compile(
    r"^(?:\d{1,4}(?:\.\d+)?"
    r"|(?:19|20)\d{2}[-./]?(?:0[1-9]|1[0-2])(?:[-./]?(?:0[1-9]|[12]\d|3[01]))?"
    r"(?:[ T]\d{2}:\d{2}(?::\d{2})?)?"
    r"|\d{1,2}:\d{2}(?::\d{2})?)$"
)
#: 날짜 서식 — `%`+영문 지시자와 구분자만(40자 이하 · 선형).
_FORMAT = re.compile(r"^(?:%[A-Za-z]|[-/:. ]){1,40}$")
_IPISH = re.compile(r"^%?[0-9.]{1,15}%?$")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.")
_NEAR = re.compile(r"near '[^\n]*?' at line (\d+)|near '[^\n]*$")
_ERROR_QUOTED = re.compile(r"'([^'\n]{0,200})'")
_ASCII_LABEL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\([^()]{0,40}\))?$")
#: MariaDB 오류 문구의 고정 어구(따옴표 안이지만 값이 아니다).
_ERROR_PHRASES = frozenset(
    {
        "field list",
        "where clause",
        "order clause",
        "group statement",
        "on clause",
        "having clause",
        "from clause",
        "IN/ALL/ANY subquery",
    }
)
#: 결과 열 식에서 컬럼이 아닌 낱말(키워드). 함수는 뒤의 `(`로, 테이블 별칭은 뒤의 `.`으로 거른다.
_SQL_WORDS = frozenset(
    "select from where and or not as on join left right inner outer cross full group by order "
    "having limit offset distinct case when then else end null is in like between asc desc union "
    "all exists interval day month year quarter week hour minute second true false escape over "
    "partition rows separator unsigned signed char varchar decimal integer date datetime".split()
)

#: 개인정보 규칙 9종(`PII_RULES`)은 전부 숫자 또는 `@`를 요구한다 — 둘 다 없으면 검사할 것이 없다.
_PII_PREFILTER = re.compile(r"[0-9@]")
_SAFE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: 키 자체가 값인 사전(경로에 키를 쓰지 않는다).
_VALUE_KEYED = frozenset({"top_values"})


# --- 사용자 정보 가림 (§3.5.4) ---------------------------------------------------


def mask_identifier(value: object) -> str:
    """사람·계정 식별자 — 앞 1자 + `***`(2자 이하는 `***` · 빈 값은 빈 문자열).

    D-300 ④와 같은 규칙. `apm_gateway`(별도 패키지 · 양방향 import 0 — D-274)의 같은 이름 함수를
    옮겨 둔다.
    """
    text = "" if value is None else str(value)
    if not text:
        return text
    return "***" if len(text) <= 2 else text[0] + "***"


def _userinfo(dsn: str) -> tuple[str, str | None, str] | None:
    """(scheme://, 사용자 정보 또는 None, 호스트 이후).

    `://` 뒤 **마지막 `@`**로 자른다 — 인코딩 안 된 `/ # ?`가 비밀번호에 있어도 새지 않게.
    """
    match = re.match(r"^([A-Za-z][A-Za-z0-9+.\-]*://)(.*)$", dsn or "", re.S)
    if not match:
        return None
    rest = match.group(2)
    if "@" not in rest:
        return match.group(1), None, rest
    userinfo, _, host = rest.rpartition("@")
    return match.group(1), userinfo, host


def mask_dsn(dsn: str) -> str:
    """접속 문자열 — 계정·비밀번호를 **둘 다** 가린다.

    `scripts/itam_erd.py` `mask_dsn`은 비밀번호만 가린다.
    """
    parts = _userinfo(str(dsn or ""))
    if parts is None:
        return str(dsn or "")
    scheme, userinfo, host = parts
    if userinfo is None:
        return str(dsn or "")
    return f"{scheme}{'***:***' if ':' in userinfo else '***'}@{host}"


def dsn_scheme(dsn: str | None) -> str | None:
    """접속 문자열의 스킴만 — run.json 기록용.

    호스트·포트·DB 이름은 남기지 않는다. ITAM은 자산 대장이라 결과 칼럼에서 수집한
    값(호스트명·DB명)이 그 토큰과 겹쳐 누출 관문 `pii_value`가 산출물 전체를 막는다.
    """
    parts = _userinfo(str(dsn or ""))
    return parts[0].removesuffix("://") if parts else None


def dsn_credentials(dsn: str | None) -> tuple[str | None, str | None]:
    """누출 관문 대조용 (계정, 비밀번호) — 산출물에는 쓰지 않는다."""
    parts = _userinfo(str(dsn or ""))
    if parts is None or parts[1] is None:
        return None, None
    user, _, password = parts[1].partition(":")
    return (unquote(user) or None), (unquote(password) or None)


def display_path(path: Path | str, *, repo_root: Path, home: Path) -> str:
    """경로 — 저장소 기준 상대 경로, 저장소 밖이면 홈을 `~`로."""
    target = Path(path)
    for base, prefix in ((repo_root, ""), (home, "~/")):
        try:
            return prefix + str(target.resolve().relative_to(Path(base).resolve()))
        except ValueError:
            continue
    return str(target)


def mask_ip(value: str) -> str:
    """IPv4 끝자리를 가린다(`10.0.1.4` → `10.0.1.***`). 한 칸의 여러 IP 도 각각."""
    return _IPV4.sub(lambda m: f"{m.group(1)}.***", str(value))


# --- 개인정보 정규식 검사 · 가림 ------------------------------------------------------


def _pii_regex_hit(text: str) -> bool:
    """연락처·이메일·주민번호·계좌·카드 규칙(`scan_pii`)에 걸리는가. 길이를 자르지 않는다.

    `scan_pii`는 2026-10-06(plans/135 v1.4) 이메일 규칙 탐색을 선형으로 고쳐 긴 연속열에서도 바로 쓸
    수 있다 — 벤치 쪽 우회(연속열 접기)는 그 수정과 함께 걷어냈다(검출 의미를 제품과 같게).
    """
    from src.security.pii_filter import scan_pii

    if not text or not _PII_PREFILTER.search(text):
        return False
    return bool(scan_pii(text, max_per_rule=1, unmask=False))


def _scrub_free_text(text: str) -> str:
    """정규식 규칙에 걸리는 부분을 형태만 남기고 가린다. 걸릴 것이 없으면 그대로 돌려준다."""
    from src.security.pii_filter import scrub_pii

    return scrub_pii(text) if _pii_regex_hit(text) else text


# --- 수집한 사람 값 (§3.5.2) -------------------------------------------------------


def _value_pattern(value: str) -> str:
    """ASCII 값은 단어 경계로만 맞춘다(`kim` ≠ `kimchi`) — 한글 이름은 조사가 붙으므로 그대로."""
    escaped = re.escape(value)
    if value.isascii():
        return rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])"
    return escaped


class PiiVault:
    """턴 처리 중 `pii` 열에서 본 값 + 정책 카나리아.

    **메모리 전용** — 파일에 쓰지 않고 run 종료 시 버린다.
    """

    def __init__(
        self, canary_literals: Iterable[str] = (), canary_patterns: Iterable[re.Pattern[str]] = ()
    ) -> None:
        self._canaries = {str(v) for v in canary_literals if len(str(v)) >= MIN_VALUE_LEN}
        self._patterns = tuple(canary_patterns)
        self._values: set[str] = set()
        self._regex: re.Pattern[str] | None = None
        self._harvested_regex: re.Pattern[str] | None = None
        self._canary_regex = self._compile(self._canaries)

    @classmethod
    def from_policy(cls, policy: ColumnPolicy) -> PiiVault:
        return cls(policy.canary_literals, policy.canary_patterns)

    def __len__(self) -> int:
        return len(self._values)

    def add(self, value: object) -> None:
        text = str(value or "").strip()
        minimum = MIN_DIGIT_VALUE_LEN if text.isdigit() else MIN_VALUE_LEN
        if len(text) >= minimum and text not in self._values:
            self._values.add(text)
            self._regex = self._harvested_regex = None

    @staticmethod
    def _compile(values: Iterable[str]) -> re.Pattern[str] | None:
        ordered = sorted(set(values), key=len, reverse=True)
        return re.compile("|".join(_value_pattern(v) for v in ordered)) if ordered else None

    def harvested_hits(self, text: str) -> int:
        """수집 값(카나리아 제외)만의 건수 — 누출 관문 ②."""
        if self._harvested_regex is None:
            self._harvested_regex = self._compile(self._values)
        regex = self._harvested_regex
        return len(regex.findall(text)) if regex and text else 0

    def canary_hits(self, text: str) -> int:
        if not text:
            return 0
        found = len(self._canary_regex.findall(text)) if self._canary_regex else 0
        return found + sum(len(p.findall(text)) for p in self._patterns)

    def hits(self, text: str) -> int:
        """수집 값 + 카나리아 건수(값은 돌려주지 않는다)."""
        return self.harvested_hits(text) + self.canary_hits(text)

    def scrub(self, text: str) -> str:
        if not text:
            return text
        if self._regex is None:
            self._regex = self._compile(self._values | self._canaries)
        out = self._regex.sub(MASK_PII, text) if self._regex else text
        for pattern in self._patterns:
            out = pattern.sub(MASK_PII, out)
        return out


def _value_unsafe(text: str, vault: PiiVault) -> bool:
    return vault.hits(text) > 0 or _pii_regex_hit(text)


def _unsafe_flags(values: list[str], vault: PiiVault) -> list[bool]:
    """값 목록의 위험 여부.

    합친 텍스트로 먼저 한 번 보고, 걸릴 때만 값마다 본다(정규식 호출 수 상한 · 자르지 않는다).
    """
    joined = "\n".join(values)
    if not values or not _value_unsafe(joined, vault):
        return [False] * len(values)
    return [_value_unsafe(value, vault) for value in values]


def scrub_tree(node: Any, vault: PiiVault) -> Any:
    """중첩 구조의 문자열 잎을 전부 가린다(수집 값·카나리아 · 정규식 · IP 끝자리)."""
    if isinstance(node, Mapping):
        return {key: scrub_tree(value, vault) for key, value in node.items()}
    if isinstance(node, list):
        return [scrub_tree(value, vault) for value in node]
    if isinstance(node, str):
        return _scrub_free_text(vault.scrub(mask_ip(node)))
    return node


# --- SQL 골격 · 결과 열 → 원 컬럼 (§3.5.1) ---------------------------------------------


def _skeleton(sql: str, identifiers: set[str] | None = None) -> str:
    """별칭·식 분석용 골격.

    주석은 공백, 백틱·식별자 따옴표·`AS` 뒤 따옴표 별칭은 안의 이름으로, 문자열은 `''`(날짜·짧은 수·
    서식처럼 무해하면 `'0'`).
    """

    def keep(match: re.Match[str]) -> str:
        token = match.group(0)
        if token.startswith(("/*", "--", "#")):
            return " "
        content = token[1:-1]
        if token.startswith("`"):
            return content
        if token.startswith('"') and identifiers and content.casefold() in identifiers:
            return content
        if re.search(r"(?i)\bAS\s*$", sql[max(0, match.start() - 16) : match.start()]):
            return content
        return "'0'" if (not content or _trivially_safe(content, "")) else "''"

    return _SQL_TOKEN.sub(keep, sql)


def _same_length_skeleton(sql: str, identifiers: set[str]) -> str:
    """위치를 유지하는 골격(술어 조각 판정용).

    토큰은 같은 길이의 공백, 식별자 따옴표는 이름만 남긴다.
    """

    def blank(match: re.Match[str]) -> str:
        token = match.group(0)
        content = token[1:-1]
        if token[0] in '"`' and (content.casefold() in identifiers or token[0] == "`"):
            return f" {content} "
        return " " * len(token)

    return _SQL_TOKEN.sub(blank, sql)


def _alias_expressions(skeleton: str, alias: str) -> list[str]:
    """`<식> AS <별칭>`·`<식> <별칭>`(AS 생략)의 식 목록.

    별칭이 아니라 그냥 컬럼이면 빼고 돌려준다.
    """
    pattern = re.compile(
        rf"(?<![\w.]){re.escape(alias)}(?![\w])(?=\s*(?:,|\bFROM\b|\)|$))", re.I | re.M
    )
    expressions = []
    for match in pattern.finditer(skeleton):
        before = skeleton[: match.start()].rstrip()
        as_match = re.search(r"(?i)\bAS$", before)
        if as_match:
            end = as_match.start()
        elif before and (before[-1].isalnum() or before[-1] in "_)'"):
            if re.search(r"(?i)\b(?:SELECT|DISTINCT)$", before):
                continue
            end = len(before)
        else:
            continue
        depth, index = 0, end - 1
        while index >= 0:
            char = skeleton[index]
            if char == ")":
                depth += 1
            elif char == "(":
                if depth == 0:
                    break
                depth -= 1
            elif char == "," and depth == 0:
                break
            index -= 1
        expression = re.split(r"(?i)\bSELECT\b(?:\s+DISTINCT\b)?", skeleton[index + 1 : end])[-1]
        if expression.strip():
            expressions.append(expression)
    return expressions


def _expression_sources(expression: str, folded: Mapping[str, str]) -> list[str]:
    """식 → 원 컬럼(카탈로그 이름) · 모르는 낱말(`?:이름`·`?`) · 컬럼 없는 계산(`#computed`)."""
    out: list[str] = []
    has_literal = "''" in expression
    for match in _IDENT.finditer(expression):
        word = match.group(0)
        tail = expression[match.end() :].lstrip()
        if tail.startswith("(") or tail.startswith(".") or word.casefold() in _SQL_WORDS:
            continue
        canonical = folded.get(word.casefold())
        item = canonical if canonical else f"{UNKNOWN}:{word}"
        if item not in out:
            out.append(item)
    if _NON_ASCII_WORD.search(expression):
        out.append(UNKNOWN)
    if has_literal:
        out.append(UNKNOWN)
    return out or [COMPUTED]


def resolve_result_columns(
    columns: Iterable[str], sqls: Iterable[str], known_columns: Iterable[str]
) -> dict[str, list[str]]:
    """결과(CSV) 열 이름 → 원 컬럼 목록. **별칭 식이 먼저**이고 실행 SQL 전부의 합집합이다.

    ①실행 SQL 어디서든 `<식> AS <별칭>`(AS 생략 포함)이면 그 식들의 컬럼 합집합(실패한 시도 포함 —
    가장 엄격한 쪽이 이긴다) ②별칭이 아니고 카탈로그 이름과 같으면 그 컬럼 ③못 찾으면 빈 목록.
    정책에 없는 낱말·문자열 리터럴이 섞인 식은 `?`(미분류)를 함께 싣는다.
    """
    folded = {str(name).casefold(): str(name) for name in known_columns}
    skeletons = [_skeleton(str(sql)[:INPUT_MAX], set(folded)) for sql in sqls if sql]
    out: dict[str, list[str]] = {}
    for column in columns:
        name = str(column)
        found: list[str] = []
        for skeleton in skeletons:
            for expression in _alias_expressions(skeleton, name):
                for item in _expression_sources(expression, folded):
                    if item not in found:
                        found.append(item)
        if not found and name.casefold() in folded:
            found = [folded[name.casefold()]]
        out[name] = found
    return out


def _column_grade(name: str, sources: Sequence[str], policy: ColumnPolicy) -> str:
    if sources:
        grades = []
        for source in sources:
            if source == COMPUTED:
                grades.append("general")
            elif source.startswith(UNKNOWN):
                grades.append("unclassified")
            else:
                grades.append(policy.grade(source))
        return strictest(grades)
    # 원 컬럼을 못 찾은 열 — 열 이름이 사람 정보를 가리키면(「담당자」) pii 로 올린다(올리기 전용)
    return "pii" if pii_suggestion(name, name) else "unclassified"


def safe_label(
    name: str,
    sources: Sequence[str],
    grade: str,
    *,
    policy: ColumnPolicy,
    prompt: str,
    vault: PiiVault,
    index: int,
) -> str:
    """결과 열 이름(시스템이 지은 별칭) — 값일 수 있으므로 근거가 있을 때만 그대로 남긴다.

    근거: 카탈로그 컬럼 이름 · 그 턴 프롬프트에 있는 말 · ASCII 식별자/함수 모양 · 일반·IP·금액
    컬럼만 가리키는 별칭. 어느 경우든 수집 값·카나리아·정규식에 걸리면 가린다.
    """
    if _value_unsafe(name, vault):
        return f"열#{index}"
    if name.casefold() in {c.casefold() for c in policy.column_names()}:
        return name
    if prompt and name.strip() and name.strip() in prompt:
        return name
    if _ASCII_LABEL.match(name):
        return name
    real = [s for s in sources if s != COMPUTED]
    if real and grade in ("general", "network", "amount") and not pii_suggestion(name, name):
        return name
    return f"열#{index}"


# --- 결과 요약 (§3.5.1 · §3.5.2) ---------------------------------------------------


def _number(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _num_out(value: float) -> float | int:
    return int(value) if float(value).is_integer() else round(value, 4)


def _is_empty(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def result_column_names(result: Mapping[str, Any] | None) -> list[str]:
    """결과 열 이름(머리글 + 첫 행에만 있는 키) — 요약 순서와 같다."""
    result = result or {}
    names = [str(c) for c in result.get("columns") or []]
    for row in [r for r in result.get("rows") or [] if isinstance(r, dict)][:1]:
        names += [str(c) for c in row if str(c) not in names]
    return names


def summarize_result(
    result: Mapping[str, Any] | None,
    *,
    sources: Mapping[str, list[str]],
    policy: ColumnPolicy,
    vault: PiiVault,
    prompt: str = "",
) -> dict[str, Any]:
    """사용자가 받은 결과(`download-csv`) → 등급별 요약. 사람 값은 먼저 vault 로 모은다(메모리)."""
    result = dict(result or {})
    status = str(result.get("status") or "unavailable")
    out: dict[str, Any] = {
        "status": status,
        "total_rows": int(result.get("total_rows") or 0),
        "truncated": bool(result.get("truncated")),
        "columns": [],
    }
    if status not in ("ok", "empty"):
        out["reason"] = redact_text(str(result.get("reason") or ""), vault=vault, prompt=prompt)
        return out
    rows = [row for row in result.get("rows") or [] if isinstance(row, dict)]
    names = result_column_names(result)
    grades = {name: _column_grade(name, list(sources.get(name) or []), policy) for name in names}
    # ① 사람 값 수집이 먼저다 — 같은 결과의 일반 열에 그 값이 섞였는지 바로 대조해야 한다.
    for name, grade in grades.items():
        if grade == "pii":
            for row in rows:
                if not _is_empty(row.get(name)):
                    vault.add(row.get(name))
    for index, name in enumerate(names, start=1):
        grade = grades[name]
        source_list = list(sources.get(name) or [])
        values = [row.get(name) for row in rows]
        present = [str(v).strip() for v in values if not _is_empty(v)]
        label = safe_label(
            name, source_list, grade, policy=policy, prompt=prompt, vault=vault, index=index
        )
        entry: dict[str, Any] = {
            "name": label,
            "source": [s for s in source_list if not s.startswith(UNKNOWN) and s != COMPUTED],
            "log_policy": grade,
            "count": len(values),
            "nulls": len(values) - len(present),
        }
        unknown = sorted({s.split(":", 1)[1] for s in source_list if s.startswith(f"{UNKNOWN}:")})
        if unknown:
            entry["unknown_identifiers"] = unknown
        if grade in ("general", "network"):
            if any(_unsafe_flags(present, vault)):
                entry["demoted"] = "pii_value_match"
            else:
                shown = [mask_ip(v) for v in present]
                counts = Counter(shown)
                if counts and len(counts) <= TOP_VALUES and len(shown) > len(counts):
                    entry["top_values"] = dict(
                        sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
                    )
                else:
                    entry["sample"] = list(dict.fromkeys(shown))[:SAMPLE_ROWS]
        elif grade == "amount":
            numbers = [n for n in (_number(v) for v in present) if n is not None]
            if numbers:
                entry.update(
                    sum=_num_out(sum(numbers)),
                    min=_num_out(min(numbers)),
                    max=_num_out(max(numbers)),
                )
        elif grade == "pii":
            entry["distinct"] = len(set(present))
        else:  # free_text · unclassified
            if present:
                lengths = [len(v) for v in present]
                entry["length"] = [min(lengths), max(lengths)]
        out["columns"].append(entry)
    return out


def unclassified_columns(summary: Mapping[str, Any]) -> list[str]:
    """정책에 없는 원 컬럼 이름(식에서 찾은 ASCII 식별자) — 리포트 「분류 필요 컬럼」."""
    return sorted(
        {
            name
            for column in summary.get("columns") or []
            for name in column.get("unknown_identifiers") or []
        }
    )


# --- SQL·문구 가림 (§3.5.3) -------------------------------------------------------


def _prompt_word(content: str, prompt: str) -> bool:
    core = content.strip("%_ ").strip()
    return len(core) >= MIN_VALUE_LEN and core in (prompt or "")


def _trivially_safe(content: str, prompt: str) -> bool:
    return bool(
        _SAFE_NUMBERISH.match(content)
        or (_FORMAT.match(content) and "%" in content)
        or _prompt_word(content, prompt)
    )


def _segment_grades(
    skeleton: str, policy: ColumnPolicy | None, catalog: frozenset[str] = frozenset()
) -> Callable[[int, int], str | None]:
    """리터럴 위치 → 같은 술어 조각에 있는 컬럼의 가장 엄격한 등급(없으면 None).

    정책 컬럼은 그 등급, 카탈로그에는 있는데 정책에 없는 컬럼(`catalog` — 소문자)은 `unclassified`다
    (운영 108테이블 대부분 — 기본 거부).
    """
    bounds = [0] + [m.end() for m in _BOUNDARY.finditer(skeleton)] + [len(skeleton)]
    starts = [m.start() for m in _BOUNDARY.finditer(skeleton)] + [len(skeleton)]

    def grade_at(start: int, end: int) -> str | None:
        if policy is None:
            return None
        left = bounds[bisect.bisect_right(bounds, start) - 1]
        right = starts[bisect.bisect_left(starts, end)]
        found = []
        for word in _IDENT.findall(skeleton[left:right]):
            grade = policy.grade(word)
            if grade != "unclassified" or word.casefold() in catalog:
                found.append(grade)
        return strictest(found) if found else None

    return grade_at


def _literal_plan(
    sql: str,
    *,
    policy: ColumnPolicy | None,
    prompt: str,
    catalog: frozenset[str] = frozenset(),
) -> list[tuple[re.Match[str], str]]:
    """토큰마다 처분 — `comment`·`keep`·`mask`. 근거 없는 리터럴은 전부 `mask`."""
    from src.utils.synonym_usage import _extract_column_literals

    identifiers: set[str] = set()
    general: set[str] = set()
    if policy is not None:
        identifiers = {n.casefold() for n in policy.column_names() | policy.table_names()}
    skeleton = _same_length_skeleton(sql, identifiers)
    if policy is not None:
        present = {ident.casefold() for ident in _IDENT.findall(skeleton)}
        general_columns = sorted(
            {
                column
                for columns in policy.tables.values()
                for column, grade in columns.items()
                if grade == "general" and column.casefold() in present
            }
        )
        general = set(_extract_column_literals(sql, general_columns))
    grade_at = _segment_grades(skeleton, policy, catalog)
    plan = []
    for match in _SQL_TOKEN.finditer(sql):
        token = match.group(0)
        if token.startswith(("/*", "--", "#")):
            plan.append((match, "comment"))
            continue
        content = token[1:-1]
        if token[0] in '"`' and content.casefold() in identifiers:
            decision = "keep"
        elif token[0] == "`":
            decision = (
                "keep" if (_ASCII_LABEL.match(content) or _prompt_word(content, prompt)) else "mask"
            )
        elif _prompt_word(content, prompt):
            decision = "keep"
        elif (segment := grade_at(match.start(), match.end())) in (
            "pii",
            "free_text",
            "unclassified",
        ):
            decision = "mask"
        elif segment == "network" and _IPISH.match(content):
            decision = "keep"  # 끝자리는 마지막에 `mask_ip`가 가린다
        elif content in general or _trivially_safe(content, prompt):
            decision = "keep"
        else:
            decision = "mask"
        plan.append((match, decision))
    return plan


def _redact_plain(text: str, prompt: str, allowed: frozenset[str] = frozenset()) -> str:
    """토큰 사이 SQL 본문 — 따옴표 없는 비ASCII 낱말은 근거가 없으면 가린다(별칭·잘못 쓴 값).

    근거: 그 턴 프롬프트에 있는 말 · 결과 요약이 열 이름으로 남긴 별칭(`allowed`).
    """

    def keep(match: re.Match[str]) -> str:
        word = match.group(0)
        return word if (word in (prompt or "") or word in allowed) else MASK

    return _NON_ASCII_WORD.sub(keep, text)


def redact_sql(
    sql: str,
    *,
    policy: ColumnPolicy | None,
    vault: PiiVault,
    prompt: str = "",
    allowed_words: Iterable[str] = (),
    catalog_columns: Iterable[str] = (),
) -> str:
    """실행 SQL — 구조는 남기고 리터럴·주석·낱말은 근거 있을 때만 남긴다.

    남긴 리터럴도 사람 값·카나리아·정규식과 대조하고, IP 는 어디서든 끝자리를 가린다.
    `allowed_words`는 결과 요약이 근거를 확인해 남긴 열 이름(별칭)이고, `catalog_columns`는 스키마
    카탈로그의 컬럼 이름이다(정책에 없는 컬럼과 같은 술어의 리터럴은 프롬프트 말이 아니면 가린다).
    """
    text = str(sql or "")[:INPUT_MAX]
    allowed = frozenset(word for word in allowed_words if word and not _value_unsafe(word, vault))
    plan = _literal_plan(
        text, policy=policy, prompt=prompt, catalog=frozenset(c.casefold() for c in catalog_columns)
    )
    kept = [m.group(0)[1:-1] for m, decision in plan if decision == "keep"]
    unsafe = dict(zip(kept, _unsafe_flags(kept, vault), strict=True))
    pieces: list[str] = []
    cursor = 0
    for match, decision in plan:
        token = match.group(0)
        pieces.append(_redact_plain(text[cursor : match.start()], prompt, allowed))
        if decision == "comment":
            opener = "/*" if token.startswith("/*") else ("--" if token.startswith("--") else "#")
            replacement = f"/*{MASK}*/" if opener == "/*" else f"{opener} {MASK}"
        elif decision == "mask":
            replacement = f"{token[0]}{MASK}{token[-1]}"
        elif unsafe.get(token[1:-1]):
            replacement = f"{token[0]}{MASK_PII}{token[-1]}"
        else:
            replacement = token
        pieces.append(replacement)
        cursor = match.end()
    pieces.append(_redact_plain(text[cursor:], prompt, allowed))
    return _scrub_free_text(vault.scrub(mask_ip("".join(pieces))))


def _token_content(token: str) -> str:
    if token.startswith("/*"):
        return token[2:-2].strip()
    if token.startswith("--"):
        return token[2:].strip()
    if token.startswith("#"):
        return token[1:].strip()
    return token[1:-1]


def redact_text(
    text: str,
    *,
    vault: PiiVault,
    sql: str | Sequence[str] = "",
    prompt: str = "",
    limit: int = ERROR_TEXT_MAX,
) -> str:
    """오류 문구·사유 — MariaDB 오류는 문제 구문 일부(리터럴 포함)를 되돌려준다.

    ①`near '…' at line N` 조각은 통째로 가린다(따옴표 짝이 어긋나 와도) ②그 턴 SQL 들의 가린 리터럴
    내용을 문구 어디서든 지운다 ③남은 작은따옴표 조각은 SQL 식별자·오류 고정 어구·날짜/짧은 수·
    프롬프트 말일 때만 남긴다 ④IP·수집 값·카나리아·정규식 ⑤`limit`자 절단.
    """
    # 남길 앞부분 + 여유만 본다 — 가림은 자르기 전에 끝내므로 경계에 걸린 값 조각은 버려지는 쪽에
    # 있다.
    out = str(text or "")[: limit + 1000]
    out = _NEAR.sub(
        lambda m: f"near '{MASK}'" + (f" at line {m.group(1)}" if m.group(1) else ""), out
    )
    sqls = [sql] if isinstance(sql, str) else list(sql)
    sql_idents: set[str] = set()
    for one in sqls:
        if not one:
            continue
        masked = {
            _token_content(m.group(0))
            for m, decision in _literal_plan(str(one)[:INPUT_MAX], policy=None, prompt=prompt)
            if decision in ("mask", "comment")
        }
        for content in sorted(
            (c for c in masked if len(c) >= MIN_VALUE_LEN), key=len, reverse=True
        ):
            out = out.replace(content, MASK)
        sql_idents |= set(_IDENT.findall(_skeleton(str(one)[:INPUT_MAX])))

    def quoted(match: re.Match[str]) -> str:
        content = match.group(1)
        if (
            content in sql_idents
            or content in _ERROR_PHRASES
            or content == MASK
            or _trivially_safe(content, prompt)
        ):
            return match.group(0)
        return f"'{MASK}'"

    out = _ERROR_QUOTED.sub(quoted, out)
    out = _scrub_free_text(vault.scrub(mask_ip(out)))
    return out if len(out) <= limit else out[:limit] + "…"


# --- 누출 관문 (§3.5.5) -------------------------------------------------------------


#: 누출 관문 규칙 — 앞 5개는 모든 산출 파일 · `code_original`은 치환 코드값 파일만.
GATE_RULES: tuple[str, ...] = (
    "canary",
    "pii_value",
    "pii_regex",
    "schema_form",
    "user_info",
    "code_original",
)
CODE_SAMPLES_FILE = "code_samples.yaml"
#: 원값 부분 문자열 대조 하한(이보다 짧은 원값은 casefold 같음만 본다).
CODE_ORIGINAL_MIN_SUBSTRING = 3

# `code_samples.yaml` 형식 어휘 — 관문이 구조 칸의 **값 형태**를 검증하는 단일 출처다(감사 L-2).
# 생성기(`code_samples.py`)가 같은 상수를 쓴다.
#: 파일 머리 고정 문구
CODE_SAMPLES_NOTE = (
    "형식 보존 치환값 — 원값 아님 · 대응표 없음 · 외부망 쿼리 생성 테스트 전용(D-311 ②) · "
    "설정 파일에 넣지 않는다"
)
#: 치환 대상에서 빼는 정책 등급 · 제외 사유(등급 + 사람 정보 휴리스틱)
CODE_SAMPLES_EXCLUDED_GRADES: tuple[str, ...] = ("pii", "free_text", "amount", "network")
CODE_SAMPLES_EXCLUDE_REASONS: tuple[str, ...] = (*CODE_SAMPLES_EXCLUDED_GRADES, "person_hint")
#: 컬럼 `substitution` 판정 열거
CODE_SAMPLES_SUBSTITUTIONS: tuple[str, ...] = ("ok", "exhausted", "flag")
#: `summary` 칸(전부 정수) — `excluded`는 제외 사유별 정수 사전
CODE_SAMPLES_SUMMARY_KEYS: tuple[str, ...] = (
    "columns", "substituted", "exhausted", "flag", "excluded",
)
#: 머리 식별자 칸(`db_id`·`run_id`·`p1_draft_id`) 값 형태 — 원값 대조 대신 형식만 본다
_CODE_SAMPLES_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
#: 컬럼 키 `table.column` 식별자 형식(이 형식이면 원값 대조를 하지 않는다 — 스키마 이름은
#: 카탈로그에도 나간다)
_CODE_COLUMN_KEY = re.compile(
    r"^[A-Za-z_가-힣][A-Za-z0-9_$#가-힣]*\.[A-Za-z_가-힣][A-Za-z0-9_$#가-힣]*$"
)
#: 컬럼 항목의 생성기 칸 이름(키 대조 제외 · 값은 칸별로 검증)
_CODE_COLUMN_FIELDS = frozenset({"distinct", "substitution", "values", "labels"})


def _fold(text: object) -> str:
    """원값 대조 정규화 — NFKC(전각·호환 문자 접기) 뒤 casefold."""
    return unicodedata.normalize("NFKC", str(text)).casefold()


class CodeOriginals:
    """원 코드값·라벨 집합(**메모리 전용** — 파일·로그에 쓰지 않는다 · repr 에 값이 없다 ·
    피클 거부).

    `hit(text)`: NFKC + casefold 기준으로 원값과 같거나 3자 이상 원값을 부분 문자열로 품으면 True.
    """

    __slots__ = ("_equal", "_long", "_lengths")

    def __init__(self, values: Iterable[object]) -> None:
        folded = {_fold(v) for v in values if str(v or "").strip()}
        self._equal = frozenset(folded)
        self._long = frozenset(f for f in folded if len(f) >= CODE_ORIGINAL_MIN_SUBSTRING)
        self._lengths = tuple(sorted({len(f) for f in self._long}))

    def __len__(self) -> int:
        return len(self._equal)

    def __repr__(self) -> str:
        return f"<CodeOriginals {len(self._equal)}건>"

    def __reduce__(self) -> Any:
        raise TypeError("CodeOriginals 는 직렬화할 수 없다(원값 보유 · 메모리 전용)")

    def __getstate__(self) -> Any:
        raise TypeError("CodeOriginals 는 직렬화할 수 없다(원값 보유 · 메모리 전용)")

    def count_matching(self, pattern: re.Pattern[str]) -> int:
        """정규화한 원값 중 `pattern`에 전체 일치하는 수 — 치환 공간 계산용(값 비노출)."""
        return sum(1 for folded in self._equal if pattern.fullmatch(folded))

    def hit(self, text: object) -> bool:
        folded = _fold(text)
        if folded in self._equal:
            return True
        for length in self._lengths:
            if length > len(folded):
                break
            if any(folded[i : i + length] in self._long for i in range(len(folded) - length + 1)):
                return True
        return False


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _summary_shape_ok(summary: Any) -> bool:
    """`summary` 값 형태 — 고정 칸 · 정수 · `excluded`는 제외 사유별 정수."""
    if not isinstance(summary, Mapping) or not set(summary) <= set(CODE_SAMPLES_SUMMARY_KEYS):
        return False
    for key, value in summary.items():
        if key == "excluded":
            if not isinstance(value, Mapping) or not set(value) <= set(
                CODE_SAMPLES_EXCLUDE_REASONS
            ):
                return False
            if not all(_is_count(v) for v in value.values()):
                return False
        elif not _is_count(value):
            return False
    return True


def _head_shape_ok(key: str, value: Any) -> bool:
    """머리 칸 값 형태 — 고정 문구 · 식별자 형식(`p1_draft_id`는 None 허용)."""
    if key == "note":
        return value == CODE_SAMPLES_NOTE
    if key == "p1_draft_id" and value is None:
        return True
    return isinstance(value, str) and bool(_CODE_SAMPLES_ID.match(value))


def _code_sample_leaves(doc: Any) -> Iterable[tuple[str, str | None]]:
    """`code_samples.yaml`의 원값 대조 대상 — ``(경로, 대조할 글)``.

    대조할 글이 None 이면 **값 형태 위반**(대조 없이 위반)이다(감사 L-2).

    - 머리 칸(`db_id`·`run_id`·`p1_draft_id`·`note`)·`summary`·컬럼 `distinct`·`substitution`은
      원값 대조 대신 값 형태(고정 문구·식별자 형식·정수·고정 열거)를 본다 — 아니면 위반.
    - 그 밖의 잎과 **매핑 키**는 전부 대조한다. 컬럼 키는 `table.column` 식별자 형식이면 통과,
      아니면 대조한다. 생성기 칸 이름(`values`·`labels` 등)은 키 대조를 하지 않는다.
    - 경로에는 컬럼 이름·매핑 키 대신 순번(`[#n]`)을 쓴다 — 고정 칸 이름만 싣는다.
    """

    def leaves(node: Any, path: str) -> Iterable[tuple[str, str | None]]:
        if isinstance(node, Mapping):
            for position, (key, value) in enumerate(node.items()):
                label = f"{path}[#{position}]"  # 키는 값일 수 있다 — 경로에 싣지 않는다
                yield label, str(key)
                yield from leaves(value, label)
        elif isinstance(node, (list, tuple)):
            for position, value in enumerate(node):
                yield from leaves(value, f"{path}[{position}]")
        elif node is not None:
            yield path, str(node)

    if not isinstance(doc, Mapping):
        yield from leaves(doc, "")
        return
    for position, (key, value) in enumerate(doc.items()):
        if key in ("db_id", "run_id", "p1_draft_id", "note"):
            if not _head_shape_ok(key, value):
                yield str(key), None
            continue
        if key == "summary":
            if not _summary_shape_ok(value):
                yield "summary", None
            continue
        if key == "columns" and isinstance(value, Mapping):
            for number, (column_key, entry) in enumerate(value.items()):
                base = f"columns[#{number}]"
                if not _CODE_COLUMN_KEY.match(str(column_key)):
                    yield base, str(column_key)
                if not isinstance(entry, Mapping):
                    yield from leaves(entry, base)
                    continue
                for field_number, (field, item) in enumerate(entry.items()):
                    if field == "distinct":
                        if not _is_count(item):
                            yield f"{base}.distinct", None
                        continue
                    if field == "substitution":
                        if item not in CODE_SAMPLES_SUBSTITUTIONS:
                            yield f"{base}.substitution", None
                        continue
                    if field in _CODE_COLUMN_FIELDS:
                        field_path = f"{base}.{field}"
                    else:
                        field_path = f"{base}[#{field_number}]"
                        yield field_path, str(field)
                    yield from leaves(item, field_path)
            continue
        label = f"[#{position}]"
        yield label, str(key)
        yield from leaves(value, label)


class LeakGate:
    """산출 텍스트 전부를 기록 직전에 다시 훑는다. 걸리면 **위치만** 돌려준다(값 없음)."""

    def __init__(
        self,
        *,
        policy: ColumnPolicy,
        vault: PiiVault,
        user_values: Mapping[str, str | None],
        code_originals: CodeOriginals | None = None,
    ) -> None:
        self._policy = policy
        self._vault = vault
        self._code_originals = code_originals
        # 너무 짧은 값은 무관한 문자열과 겹친다 — 3자 이상만 원값 대조(가린 형태 `5***`는 원값이
        # 아니다)
        self._user_values = [str(v) for v in user_values.values() if v and len(str(v)) >= 3]

    def rules(self, text: str, *, schema_section: bool) -> list[str]:
        """텍스트 하나에 걸린 규칙(길이를 자르지 않는다)."""
        found = []
        if self._vault.canary_hits(text) or self._policy.canary_hits(text):
            found.append("canary")
        if self._vault.harvested_hits(text):
            found.append("pii_value")
        if _pii_regex_hit(text):
            found.append("pii_regex")
        if schema_section and SCHEMA_QUERY_FORMS.search(text):
            found.append("schema_form")
        if any(value in text for value in self._user_values) or _JWT.search(text):
            found.append("user_info")
        return found

    def _label(self, key: str, position: int, parent: str) -> str:
        if (
            parent in _VALUE_KEYED
            or not _SAFE_KEY.match(key)
            or self.rules(key, schema_section=False)
        ):
            return f"#{position}"
        return key

    def _leaves(self, node: Any, path: str = "", parent: str = "") -> Iterable[tuple[str, str]]:
        """(필드 경로, 디코드된 문자열) — 사전 키도 검사한다. 경로에는 값이 아닌 키만 쓴다."""
        if isinstance(node, Mapping):
            for position, (key, value) in enumerate(node.items()):
                label = self._label(str(key), position, parent)
                child = f"{path}.{label}" if path else label
                yield child, str(key)
                yield from self._leaves(value, child, str(key))
        elif isinstance(node, (list, tuple)):
            for position, value in enumerate(node):
                yield from self._leaves(value, f"{path}[{position}]", parent)
        elif node is not None:
            yield path, str(node)

    def _units(self, name: str, text: str) -> Iterable[tuple[int | None, str, str]]:
        suffix = Path(name).suffix
        if suffix == ".jsonl":
            for number, line in enumerate(text.splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    yield number, "", line
                    continue
                for field, leaf in self._leaves(parsed):
                    yield number, field, leaf
            return
        if suffix in (".json", ".yaml", ".yml"):
            try:
                parsed = json.loads(text) if suffix == ".json" else yaml.safe_load(text)
            except (json.JSONDecodeError, yaml.YAMLError):
                parsed = None
            if parsed is not None:
                for field, leaf in self._leaves(parsed):
                    yield None, field, leaf
                return
        for number, line in enumerate(text.splitlines(), start=1):
            yield number, "", line

    def code_original_hit(self, text: str) -> bool:
        """원 코드값·라벨 대조(`code_original`) — 원 집합이 없으면 닫힌 쪽(True)."""
        return self._code_originals is None or self._code_originals.hit(text)

    def _check_code_samples(self, name: str, text: str) -> list[dict[str, Any]]:
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError:
            doc = None
        if doc is None or self._code_originals is None:
            # 원 집합 없이 치환 파일을 쓰려 하거나 읽을 수 없으면 닫힌 쪽으로 실패한다
            return [{"file": name, "record": None, "field": None, "rule": "code_original"}]
        return [
            {"file": name, "record": None, "field": field or None, "rule": "code_original"}
            for field, leaf in _code_sample_leaves(doc)
            if leaf is None or self._code_originals.hit(leaf)
        ]

    def check(self, files: Mapping[str, str]) -> list[dict[str, Any]]:
        violations: list[dict[str, Any]] = []
        for name, text in files.items():
            if name == CODE_SAMPLES_FILE:
                violations += self._check_code_samples(name, text)
            whole_schema = name.startswith("schema_catalog")
            seen_rules: set[str] = set()
            decoded: list[str] = []
            for record, field, leaf in self._units(name, text):
                decoded.append(leaf)
                section = whole_schema or field.split(".")[0].split("[")[0] == "schema_context"
                for rule in self.rules(leaf, schema_section=section):
                    seen_rules.add(rule)
                    violations.append(
                        {"file": name, "record": record, "field": field or None, "rule": rule}
                    )
            # 닫힌 쪽으로 실패: 잎을 이어 붙였을 때만 걸리는 규칙(잎 경계에 걸친 값)도 위반이다.
            if len(decoded) < 2:
                continue
            for rule in self.rules("\n".join(decoded), schema_section=whole_schema):
                if rule not in seen_rules:
                    violations.append({"file": name, "record": None, "field": None, "rule": rule})
        return violations


def write_gated(
    out_dir: Path, staged: Mapping[str, str], gate: LeakGate
) -> tuple[bool, list[dict[str, Any]]]:
    """관문을 통과하면 전부 쓰고, 아니면 `leak_check.json`(위치만)만 쓴다.

    조용히 성공 처리하지 않는다. 산출물은 디스크에 닿기 전(메모리)에 검사한다 — 「임시 디렉터리에 쓴
    뒤 옮김」과 같은 효과이고 실패 시 지울 파일 자체가 없다.
    """
    violations = gate.check(staged)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "passed": not violations,
        "checked_files": sorted(staged),
        "violations": violations,
        "rules": list(GATE_RULES),
    }
    targets = {} if violations else dict(staged)
    targets["leak_check.json"] = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    for name, body in targets.items():
        final = out_dir / name
        tmp = out_dir / f".{name}.tmp"
        tmp.write_text(body, encoding="utf-8", newline="\n")
        os.replace(tmp, final)
    return not violations, violations
