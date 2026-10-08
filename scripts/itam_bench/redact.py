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
  따옴표)가 있고 같은 술어에 사람·서술형 컬럼이 없을 때만 남긴다. 테이블·컬럼 식별자(백틱 ·
  `AS` 뒤 따옴표 별칭 · 따옴표 없는 비ASCII 낱말)는 카탈로그 실존과 무관하게 이름으로 남긴다(D-301
  부기 2026-10-07 — 데이터만 가린다). 다만 비한정·비카탈로그 낱말이 식별자 모양이 아니거나, 값
  자리(비교 연산자·LIKE·BETWEEN·THEN·ELSE 뒤 · IN 목록)에 있거나, 사람·서술형·미검토 카탈로그
  컬럼과 같은 술어에 있으면 값으로 보고 가린다. 별칭은 가린 리터럴과 겹치거나 상수 항목일 때만
  가린다. IP 는 어디서든 끝자리를 가린다. SQL 구조(테이블·컬럼·조건 형태)는 그대로다.
- 결과에서 본 사람 값은 **메모리에만** 모아(`PiiVault`) 다른 칸·SQL·오류 문구에 나타나면 가린다.
- 누출 관문(`LeakGate`)이 산출물 전부를 디코드한 값 단위로 다시 훑는다 — 실패하면 산출물을 쓰지 않고
  위치만 적는다(값일 수 있는 키는 경로에 순번으로). 치환 코드값 파일(`code_samples.yaml`)에는 「원
  코드값 출현 0」 규칙(`code_original`)을 더한다 — 원 집합은 메모리에서만 넘긴다(plans/140 W2-5).
- **가짜 값 치환**(plans/145 W2 · D-321) — `PiiVault`가 run 의 형식 보존 가짜 값 생성기
  (`substitute.FakeValues`)를 품으면 가리던 자리(SQL 리터럴·주석·식별자 자리 값·사람 값·오류 문구
  조각·비일반 결과 열 표본·IP 끝자리)에 표지 대신 가짜 값을 낸다. 생성기가 없으면 현행 표지
  그대로다. 정규식 PII(`_scrub_free_text`)는 현행 모양 가림이다(U-1). 관문 `substitution` 규칙이
  충돌·원값 통과·치환값 목록(`substitutions.yaml`)을 본다.

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
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

import yaml

from .catalog import SCHEMA_QUERY_FORMS, ColumnPolicy, pii_suggestion, strictest

if TYPE_CHECKING:  # `substitute`가 이 모듈을 import 한다 — 실행 시 import 는 순환이다
    from .substitute import FakeValues

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

#: 점으로 나뉜 네 덩이 — 앞이 숫자이거나 「숫자.」면(긴 점 연쇄의 중간) 시작하지 않는다. 문장 끝
#: 마침표(`… 172.31.45.67.`)는 IP 뒤 구두점으로 본다(감사 M-A — 예전 `(?<![\d.])`·`(?![\d.])`는
#: 마침표 하나로 IP 전체를 놓쳤다). 다섯 덩이 이상 점 연쇄(`IP.port` — netstat·tcpdump
#: 표기 `172.31.45.67.8080` · 버전 `1.2.3.4.5.6` · OID)는 **앞 네 덩이만** 바꾸거나 가린다
#: (교정 3차 M-A2 — 연쇄를 통째로 놓치면 관문까지 함께 놓쳤다). 연쇄 길이 상한은 두지 않는다 —
#: 상한 밖 연쇄는 관문(`_IP_LEFT`)에 막혀 run 이 서므로, 가림 쪽이 넓은 편이 가용성에 낫다.
#: 구분자는 ASCII 점과 전각 점(`．` U+FF0E · `。` U+3002 · `｡` U+FF61)이다 — 전각 점 IP 셀
#: 하나가 관문에서 run 전체를 세우지 않게 가림·치환도 같은 구분자를 본다(교정 3차b). 가린 꼴은
#: `a．b．c.***`, 가짜 값은 ASCII 점이다(`ip_key`로 접어 같은 IP 와 메모를 함께 쓴다).
#: 잔여(교정 3차d): 점으로 이어진 IP 두 개(`a.b.c.d.e.f.g.h`)는 앞 네 덩이만 바뀌고 뒤 네
#: 덩이가 원문으로 남아 관문(`_IP_LEFT`)이 run 을 막는다 — 누출은 아니고 가용성 잔여다.
#: 고정 폭 뒤보기 둘로 쓴다.
_IP_DOTS = "[.\uff0e\u3002\uff61]"
_IPV4 = re.compile(
    rf"(?<!\d)(?<!\d{_IP_DOTS})(\d{{1,3}}{_IP_DOTS}\d{{1,3}}{_IP_DOTS}\d{{1,3}}){_IP_DOTS}\d{{1,3}}"
    r"(?!\d)"
)
#: 관문 전용 남은 IPv4 — 가림(`_IPV4`)보다 느슨하다: 점 연쇄 중간에서도 시작한다(앞이 숫자만
#: 아니면 · 교정 3차 M-A2). 잡힌 네 덩이가 이 run 가짜 값이 아니면 위반이다(가짜 IP 는 ASCII
#: 점이라 전각 점 꼴이 남았으면 가림이 빠진 것 — 닫힌 쪽).
_IP_LEFT = re.compile(
    rf"(?<!\d)\d{{1,3}}{_IP_DOTS}\d{{1,3}}{_IP_DOTS}\d{{1,3}}{_IP_DOTS}\d{{1,3}}(?!\d)"
)
#: 전각 점 → ASCII 점(IP 메모 키 · `ip_key`).
_IP_DOT_FOLD = str.maketrans({"\uff0e": ".", "\u3002": ".", "\uff61": "."})


def ip_key(text: str) -> str:
    """IP 글의 전각 점을 ASCII 점으로 접는다(가짜 IP 메모 키 — 같은 IP 는 같은 가짜 값)."""
    return text.translate(_IP_DOT_FOLD)
#: IPv4 접두 리터럴(`10.0.1.%` · `%10.0.`) — 1~3 옥텟 + 끝 점 + 선택 `%`(전체 일치로 쓴다).
#: SQL 토큰 — 주석 · 작은따옴표 · 큰따옴표(MariaDB 기본 모드에서는 문자열) · 백틱. 왼쪽부터 한 번에.
_SQL_TOKEN = re.compile(
    r"/\*.*?\*/|--[^\n]*|#[^\n]*|'(?:[^'\\]|\\.|'')*'|\"(?:[^\"\\]|\\.|\"\")*\"|`(?:[^`]|``)*`",
    re.S,
)
#: 술어 조각 경계(리터럴이 어느 컬럼과 같은 조건에 있는가를 볼 때).
_BOUNDARY = re.compile(r"(?i)\b(?:and|or|where|on|having|when|then|else|select|from|set|join)\b|;")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
#: 식별자 자리 값 판정용 낱말(한글 포함) — 리터럴 처분은 `_IDENT`(ASCII) 그대로(리터럴 규칙 불변).
_IDENT_ANY = re.compile(r"[^\W\d]\w*")
_AS_BEFORE = re.compile(r"(?i)\bAS\s*$")
#: 식별자 모양 — 숫자로 시작하지 않고 공백·특수문자 없음(아니면 식별자 자리의 값으로 본다).
_IDENT_SHAPE = re.compile(r"^[^\W\d]\w*$")
#: 값 자리 — 바로 앞이 LIKE·BETWEEN·WHEN·THEN·ELSE 또는 BETWEEN 뒤의 AND(창 안에서만 본다).
#: `WHEN` 직후는 단순 CASE 의 비교값이다(검색형 CASE 의 칼럼은 카탈로그·정책 이름으로 이미 남는다).
_VALUE_WORD_BEFORE = re.compile(
    r"(?is)(?:\b(?:LIKE|BETWEEN|WHEN|THEN|ELSE)|\bBETWEEN\b(?:(?!\bAND\b).){0,200}\bAND)\s*$"
)
_IN_BEFORE = re.compile(r"(?i)\bIN\s*$")
#: 비교 자리 수(감사 M-B) — 앞 낱말이 LIKE·BETWEEN·WHEN(단순 CASE 비교값)이거나, AND 앞 창
#: 안에 짝 BETWEEN 이 있다(위쪽 경계). 공백·부호는 부르는 쪽이 건너뛴 뒤 끝에서 본다.
_COMPARE_WORD_BEFORE = re.compile(r"(?i)\b(?:LIKE|BETWEEN|WHEN|AND)$")
_BETWEEN_AND_BEFORE = re.compile(r"(?is)\bBETWEEN\b(?:(?!\bAND\b).){0,200}\bAND$")
#: 가짜 값으로 바꾸는 비교 자리 수의 술어 등급 — None 은 근거 없음(가장 엄격).
_COMPARE_FAKE_GRADES = frozenset({"pii", "free_text", "unclassified", "amount", None})
#: 비교 자리를 감싸는 호출(교정 3차) — `= CAST(N AS …)`의 첫 인자 · `= COALESCE(x, N)`의 인자.
_WRAP_CALL_BEFORE = re.compile(r"(?i)\b(CAST|COALESCE)\s*$")
#: 값 하위 질의 — `(` 바로 앞이 비교 연산자·IN·ANY·ALL·SOME 이고 `(` 바로 뒤가 SELECT.
_VALUE_OPEN_BEFORE = re.compile(r"(?i)(?:[=<>]|\b(?:IN|ANY|ALL|SOME))\s*$")
_SELECT_HEAD = re.compile(r"(?i)\s*select\b")
#: 테이블 자리 — `FROM`·`JOIN` 바로 뒤(교정 1차 F9)
_TABLE_KEYWORD_BEFORE = re.compile(r"(?i)\b(?:from|join)\s*$")
_FROM_WORD = re.compile(r"(?i)\bfrom\b")
#: SELECT 목록에서 정의한 별칭(`AS` 뒤 백틱·따옴표·낱말).
_ALIAS_DEF = re.compile(
    r"(?i)\bAS\s*(`(?:[^`]|``)*`|\"(?:[^\"\\]|\\.|\"\")*\"|'(?:[^'\\]|\\.|'')*'|[^\W\d]\w*)"
)
#: SELECT 항목 경계(별칭의 항목 — 깊이 0 쉼표와 함께 쓴다).
_ITEM_BOUNDARY = re.compile(r"(?i)\b(?:select|from)\b|;")
#: 칼럼 하나만인 SELECT 항목(한정 · 백틱 · DISTINCT · 앞뒤 괄호 허용 · 함수 없음) — `AS` 앞까지.
_SINGLE_COLUMN_ITEM = re.compile(
    r"(?i)[(\s]*(?:distinct\s+)?(?:(?:`[^`]*`|[^\W\d]\w*)\.)?(?:`([^`]*)`|([^\W\d]\w*))[)\s]*"
)
#: 상수만인 SELECT 항목(숫자 · 따옴표 리터럴 하나 · 앞뒤 괄호만) — `AS` 앞까지.
_CONST_ITEM = re.compile(
    r"[(\s]*(?:[-+]?\d+(?:\.\d+)?|'(?:[^'\\]|\\.|'')*'|\"(?:[^\"\\]|\\.|\"\")*\")[)\s]*"
)
_NON_ASCII_WORD = re.compile(r"[^\x00-\x7F\s]+")
#: SQL 본문의 따옴표 없는 비ASCII 낱말 — 붙은 ASCII 영숫자·`_`까지 한 낱말(`박서준1234567`).
#: 앞의 `\b`로 시도 위치를 낱말 머리로 묶어 선형이다. 비ASCII 기호 연속은 예전처럼 따로 잡는다.
_PLAIN_WORD = re.compile(r"\b\w*[^\W\x00-\x7F]\w*|[^\x00-\x7F\s\w]+")
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
_NEAR = re.compile(r"near '(?P<body>[^\n]*?)' at line (?P<line>\d+)|near '(?P<tail>[^\n]*)$")
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
#: 오류 문구 속 키 이름 고정 어구 — 가짜 값 경로에서만 남긴다(생성기 없는 경로는 기준선 그대로
#: 가림 · verifier Low-4).
_ERROR_KEY_NAMES = frozenset({"PRIMARY"})
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


def ip_prefix(text: str) -> tuple[str, list[str], str] | None:
    """IP 접두 리터럴(`10.0.1.%` · `%10.0.` — 옥텟 1~3개 + 끝 `.`) → (앞 `%`, 옥텟, 뒤 `%`).

    아니면 None(정규식 대신 쪼개 본다 — 중첩 수량자 없음).
    """
    head = "%" if text.startswith("%") else ""
    body = text[len(head) :]
    tail = "%" if body.endswith("%") else ""
    body = body[: len(body) - len(tail)]
    if not body.endswith("."):
        return None
    octets = body[:-1].split(".")
    if not 1 <= len(octets) <= 3:
        return None
    if not all(1 <= len(o) <= 3 and o.isascii() and o.isdigit() for o in octets):
        return None
    return head, octets, tail


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

    `fakes`(run 의 가짜 값 생성기 · plans/145)를 품으면 가리던 자리에 표지 대신 가짜 값을 낸다
    (`substitute`·`mask_ip`·`scrub`). 생성기의 「알려진 값」 판정을 이 vault 의 수집 값·카나리아
    대조(`hits`)로 잇는다. 없으면(근거 묶음·검증 모드) 현행 표지 그대로다.
    """

    def __init__(
        self,
        canary_literals: Iterable[str] = (),
        canary_patterns: Iterable[re.Pattern[str]] = (),
        *,
        fakes: FakeValues | None = None,
    ) -> None:
        self._canaries = {str(v) for v in canary_literals if len(str(v)) >= MIN_VALUE_LEN}
        self._patterns = tuple(canary_patterns)
        self._values: set[str] = set()
        self._regex: re.Pattern[str] | None = None
        self._harvested_regex: re.Pattern[str] | None = None
        self._canary_regex = self._compile(self._canaries)
        self._fakes = fakes
        if fakes is not None:
            fakes.set_known(lambda text: self.hits(text) > 0)

    @classmethod
    def from_policy(cls, policy: ColumnPolicy, *, fakes: FakeValues | None = None) -> PiiVault:
        return cls(policy.canary_literals, policy.canary_patterns, fakes=fakes)

    @property
    def fakes(self) -> FakeValues | None:
        """run 의 가짜 값 생성기(없으면 None — 현행 표지)."""
        return self._fakes

    def substitute(self, text: str, marker: str = MASK) -> str:
        """가릴 값 → 가짜 값. 생성기가 없거나 치환 불가면 `marker`."""
        if self._fakes is None:
            return marker
        out = self._fakes.fake_or_none(text)
        return marker if out is None else out

    def is_fake(self, text: str) -> bool:
        """이 run 생성기가 낸 가짜 값인가(생성기 없으면 False)."""
        return self._fakes is not None and self._fakes.is_fake(text)

    def mask_ip(self, text: str) -> str:
        """IPv4 — 생성기가 있으면 계층 일관 가짜 IP(`FakeValues.fake_ip`), 없으면 끝자리 `***`.

        원값 글에만 쓴다 — 이번 호출이 이미 만든 가짜 값 구간은 부르는 쪽이 빼고 넘긴다.
        """
        if self._fakes is None:
            return mask_ip(text)
        return self._fakes.fake_ip(str(text))

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
        replace: str | Callable[[re.Match[str]], str] = MASK_PII
        if self._fakes is not None:
            replace = lambda m: self.substitute(m.group(0), MASK_PII)  # noqa: E731
        out = self._regex.sub(replace, text) if self._regex else text
        for pattern in self._patterns:
            out = pattern.sub(replace, out)
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
        return _scrub_free_text(vault.scrub(vault.mask_ip(node)))
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


def _expression_sources(
    expression: str, folded: Mapping[str, str], catalog: frozenset[str] = frozenset()
) -> list[str]:
    """식 → 원 컬럼(정책 이름) · 모르는 낱말(`?:이름`·`?`) · 컬럼 없는 계산(`#computed`).

    낱말은 유니코드 단위로 끊는다(`IP주소내용`을 `IP`로 자르지 않는다 · 교정 1차 F12). 정책에 없는
    낱말은 ASCII 식별자이거나 카탈로그 컬럼 이름일 때만 이름을 남기고(`?:이름`), 그 밖의 비ASCII
    낱말은 값일 수 있어 이름 없이 `?`다.
    """
    out: list[str] = []
    has_literal = "''" in expression
    anonymous = False
    for match in _IDENT_ANY.finditer(expression):
        word = match.group(0)
        tail = expression[match.end() :].lstrip()
        if tail.startswith("(") or tail.startswith(".") or word.casefold() in _SQL_WORDS:
            continue
        canonical = folded.get(word.casefold())
        if canonical:
            item = canonical
        elif word.isascii() or word.casefold() in catalog:
            item = f"{UNKNOWN}:{word}"
        else:
            anonymous = True
            continue
        if item not in out:
            out.append(item)
    if anonymous or has_literal:
        out.append(UNKNOWN)
    return out or [COMPUTED]


def resolve_result_columns(
    columns: Iterable[str],
    sqls: Iterable[str],
    known_columns: Iterable[str],
    catalog_columns: Iterable[str] = (),
) -> dict[str, list[str]]:
    """결과(CSV) 열 이름 → 원 컬럼 목록. **별칭 식이 먼저**이고 실행 SQL 전부의 합집합이다.

    ①실행 SQL 어디서든 `<식> AS <별칭>`(AS 생략 포함)이면 그 식들의 컬럼 합집합(실패한 시도 포함 —
    가장 엄격한 쪽이 이긴다) ②별칭이 아니고 카탈로그 이름과 같으면 그 컬럼 ③못 찾으면 빈 목록.
    정책에 없는 낱말·문자열 리터럴이 섞인 식은 `?`(미분류)를 함께 싣는다. `catalog_columns`(카탈로그
    컬럼 이름)에 든 정책 밖 이름은 비ASCII 라도 `?:이름`으로 남는다(값이 아니라 이름이다).
    """
    folded = {str(name).casefold(): str(name) for name in known_columns}
    catalog = frozenset(str(name).casefold() for name in catalog_columns)
    skeletons = [_skeleton(str(sql)[:INPUT_MAX], set(folded)) for sql in sqls if sql]
    out: dict[str, list[str]] = {}
    for column in columns:
        name = str(column)
        found: list[str] = []
        for skeleton in skeletons:
            for expression in _alias_expressions(skeleton, name):
                for item in _expression_sources(expression, folded, catalog):
                    if item not in found:
                        found.append(item)
        if not found and name.casefold() in folded:
            found = [folded[name.casefold()]]
        elif not found and name.casefold() in catalog:
            # 정책 밖 카탈로그 컬럼 — 「분류 필요 컬럼」에 이름을 싣는다
            found = [f"{UNKNOWN}:{name}"]
        out[name] = found
    return out


def _column_grade(name: str, sources: Sequence[str], policy: ColumnPolicy) -> str:
    if sources:
        grades = []
        for source in sources:
            if source == COMPUTED:
                grades.append("general")
            elif source.startswith(f"{UNKNOWN}:"):
                # 정책 밖 컬럼 — 원 정책은 `unclassified`, 유효 정책은 관계로 올린 등급(plans/145)
                grades.append(policy.grade(source[len(UNKNOWN) + 1 :]))
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
    if any(  # 정책 밖 카탈로그 컬럼 이름 그대로(`resolve_result_columns`의 `?:이름` · 교정 1차 F12)
        s.startswith(f"{UNKNOWN}:") and s.split(":", 1)[1].casefold() == name.casefold()
        for s in sources
    ):
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


def _fill_substituted(entry: dict[str, Any], present: Sequence[str], fakes: FakeValues) -> None:
    """비일반 열(+강등 열) — `general`과 같은 모양(표본 5 · 범주형 상위 10)으로 가짜 값을 채운다.

    빈도는 원값 기준 수다(같은 원값 → 같은 가짜 값). 보여 줄 원값만 생성기에 넣는다.
    """
    counts = Counter(present)
    if counts and len(counts) <= TOP_VALUES and len(present) > len(counts):
        top: Counter[str] = Counter()
        for value, number in counts.items():
            top[fakes.fake(value)] += number
        entry["top_values"] = dict(sorted(top.items(), key=lambda kv: (-kv[1], kv[0])))
    else:
        shown = [fakes.fake(value) for value in list(counts)[:SAMPLE_ROWS]]
        entry["sample"] = list(dict.fromkeys(shown))
    entry["substituted"] = True


def summarize_result(
    result: Mapping[str, Any] | None,
    *,
    sources: Mapping[str, list[str]],
    policy: ColumnPolicy,
    vault: PiiVault,
    prompt: str = "",
) -> dict[str, Any]:
    """사용자가 받은 결과(`download-csv`) → 등급별 요약. 사람 값은 먼저 vault 로 모은다(메모리).

    vault 가 가짜 값 생성기를 품으면 비일반 등급·강등 열에도 가짜 값 표본을 싣고
    `substituted: true`를 단다(기존 통계 칸은 그대로 · plans/145 L7).
    """
    fakes = vault.fakes
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
                if fakes is not None:
                    _fill_substituted(entry, present, fakes)
            else:
                shown = [vault.mask_ip(v) for v in present]
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
            if fakes is not None:
                _fill_substituted(entry, present, fakes)
        elif grade == "pii":
            entry["distinct"] = len(set(present))
            if fakes is not None:
                _fill_substituted(entry, present, fakes)
        else:  # free_text · unclassified
            if present:
                lengths = [len(v) for v in present]
                entry["length"] = [min(lengths), max(lengths)]
            if fakes is not None:
                _fill_substituted(entry, present, fakes)
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


def _paren_marks(plain: str) -> tuple[list[int], set[int], bytearray]:
    """(괄호 깊이 0 쉼표, `IN (` 목록의 여는 괄호·목록 쉼표, 값 하위 질의 SELECT 목록 표지).

    `plain`은 토큰(문자열·주석·백틱)을 같은 길이 공백으로 지운 SQL 이다 — 토큰 안 괄호·쉼표는
    세지 않는다. 함수 인자 쉼표는 어느 쪽에도 들지 않는다. 표지는 `= ANY (SELECT 박서준 FROM u)`·
    `IN (SELECT 박서준)`처럼 값 자리 하위 질의의 SELECT 목록(같은 깊이의 FROM 또는 닫는 괄호까지)
    위치에 1을 둔다.
    """
    # 여는 괄호마다 (`IN (` 목록인가, 값 하위 질의 목록 시작 | -1)
    stack: list[tuple[bool, int]] = []
    depths: list[int] = []
    commas: list[int] = []
    in_list: set[int] = set()
    ranges: list[tuple[int, int, int]] = []
    for index, char in enumerate(plain):
        depths.append(len(stack))
        if char == "(":
            window = plain[max(0, index - 16) : index]
            is_in = bool(_IN_BEFORE.search(window))
            head = _SELECT_HEAD.match(plain, index + 1)
            value_start = head.end() if head and _VALUE_OPEN_BEFORE.search(window) else -1
            stack.append((is_in, value_start))
            if is_in:
                in_list.add(index)
        elif char == ")":
            if stack:
                _is_in, value_start = stack.pop()
                if value_start >= 0:
                    ranges.append((value_start, index, len(stack) + 1))
        elif char == ",":
            if not stack:
                commas.append(index)
            elif stack[-1][0]:
                in_list.add(index)
    marks = bytearray(len(plain))
    for begin, close, depth in ranges:
        stop = close
        for match in _FROM_WORD.finditer(plain, begin, close):
            if depths[match.start()] == depth:
                stop = match.start()
                break
        marks[begin:stop] = b"\x01" * (stop - begin)
    return commas, in_list, marks


def _spans(
    boundaries: Iterable[re.Match[str]], commas: Sequence[int], length: int
) -> tuple[list[int], list[int]]:
    """조각 경계 → (조각 시작 후보, 조각 끝 후보) — `bisect`로 위치의 조각을 찾는다."""
    found = list(boundaries)
    bounds = sorted([0] + [m.end() for m in found] + [c + 1 for c in commas] + [length])
    starts = sorted([m.start() for m in found] + list(commas) + [length])
    return bounds, starts


def _identifier_judge(
    sql: str,
    *,
    policy: ColumnPolicy | None,
    catalog: frozenset[str] = frozenset(),
    masked_literals: Iterable[str] = (),
    prompt: str = "",
) -> Callable[[int, int, str], bool]:
    """식별자 자리(백틱 · `AS` 뒤 따옴표 별칭 · 따옴표 없는 비ASCII 낱말)에 쓴 **값**인가.

    True 면 가린다.

    D-301 부기(2026-10-07) — 식별자는 이름으로 남긴다. 프롬프트 말 여부는 호출부가 먼저 본다.

    - 남김: 정책 컬럼·테이블·카탈로그 컬럼 이름 · 한정(바로 앞이나 뒤에 `.`) · 테이블 자리(`FROM`·
      `JOIN` 바로 뒤 · `FROM` 쉼표 조인 목록 — 교정 1차 F9: 술어 조각 위험을 보지 않는다).
    - A 모양: 식별자 모양(`_IDENT_SHAPE`)이 아니면 가린다.
    - 별칭(`AS` 뒤): 같은 SQL 에서 가린 리터럴(`masked_literals`)과 내용이 겹치거나(2자 이상 ·
      casefold · 같음/포함/피포함 — ASCII 리터럴의 포함은 낱말 경계로), 그 SELECT 항목이 상수
      하나뿐이거나 사람·서술형 칼럼 하나뿐이면 가린다. 그 밖의 별칭은 남긴다(`SELECT 서버명 AS
      박서준`은 수용 잔여).
    - 그 밖: 값 자리(비교 연산자·LIKE·BETWEEN·BETWEEN 뒤 AND·WHEN·THEN·ELSE 뒤 · `IN (` 목록 ·
      값 하위 질의의 SELECT 목록)면 가린다. 아니면 이 SQL 에서 정의해 남긴 별칭의 참조(ORDER BY·
      GROUP BY·HAVING)는 남기고, 같은 술어 조각(`_BOUNDARY` + 괄호 깊이 0 쉼표)에 사람·서술형
      컬럼(정책 밖은 사람 정보 휴리스틱) 또는 정책 밖 카탈로그 컬럼이 있으면(자기 자신 제외) 가린다.

    조각마다 위험 낱말 수를 누적합으로 한 번 계산하고, 낱말 등급은 지역 사전에 담는다(선형).
    """
    identifiers: set[str] = set()
    if policy is not None:
        identifiers = {n.casefold() for n in policy.column_names() | policy.table_names()}
    names = identifiers | set(catalog)
    skeleton = _same_length_skeleton(sql, identifiers)
    plain = _SQL_TOKEN.sub(lambda m: " " * len(m.group(0)), sql)
    commas, in_list, value_selects = _paren_marks(plain)
    seg_bounds, seg_starts = _spans(_BOUNDARY.finditer(skeleton), commas, len(sql))
    item_bounds, item_starts = _spans(_ITEM_BOUNDARY.finditer(skeleton), commas, len(sql))
    literals = [
        (v, re.compile(_value_pattern(v)) if v.isascii() else None)
        for v in {str(v).casefold() for v in masked_literals}
        if len(v) >= MIN_VALUE_LEN
    ]

    # 정책 등급 색인 — `ColumnPolicy.grade`(테이블 모름 = 가장 엄격)와 같은 값을 한 번만 만든다
    folded_grades: dict[str, str] = {}
    for columns in (policy.tables.values() if policy is not None else ()):
        for column, column_grade in columns.items():
            key = column.casefold()
            folded_grades[key] = strictest([folded_grades.get(key, ""), column_grade])
    grades: dict[str, str] = {}

    def grade_of(word: str) -> str:
        key = word.casefold()
        if key not in grades:
            grade = folded_grades.get(key, "unclassified")
            if grade == "unclassified" and pii_suggestion(word, word):
                grade = "pii"
            grades[key] = grade
        return grades[key]

    def risky(word: str) -> bool:
        grade = grade_of(word)
        return grade in ("pii", "free_text") or (
            grade == "unclassified" and word.casefold() in catalog
        )

    words = list(_IDENT_ANY.finditer(skeleton))
    word_starts = [m.start() for m in words]
    word_ends = [m.end() for m in words]
    # 별칭 자리(`AS` 뒤) 낱말은 칼럼이 아니다 — 조각 위험에 세지 않는다(「서버이름」 별칭이 「이름」
    # 휴리스틱으로 같은 항목의 칼럼을 가리지 않게)
    word_risk = [
        not _AS_BEFORE.search(skeleton[max(0, m.start() - 64) : m.start()]) and risky(m.group(0))
        for m in words
    ]
    prefix = [0]
    for flag in word_risk:
        prefix.append(prefix[-1] + flag)

    def segment(bounds: list[int], starts: list[int], start: int, end: int) -> tuple[int, int]:
        left = bounds[bisect.bisect_right(bounds, start) - 1]
        return left, starts[bisect.bisect_left(starts, end)]

    def value_slot(start: int) -> bool:
        index = start - 1
        while index >= 0 and skeleton[index].isspace():
            index -= 1
        if index < 0:
            return False
        if skeleton[index] in "=<>":
            return True
        if value_selects[start] if start < len(value_selects) else False:
            return True
        if skeleton[index] in "(,":
            return index in in_list
        window = skeleton[max(0, index - 255) : index + 1]
        return bool(_VALUE_WORD_BEFORE.search(window))

    def table_slot(start: int) -> bool:
        """테이블 이름 자리 — `FROM`·`JOIN` 바로 뒤, 또는 `FROM` 쉼표 조인 목록의 쉼표 뒤.

        목록 항목은 `이름` · `이름 별칭` · `이름 AS 별칭`(괄호·다른 낱말이 끼면 목록이 아니다).
        """
        index = start - 1
        while index >= 0 and skeleton[index].isspace():
            index -= 1
        if index < 0:
            return False
        if _TABLE_KEYWORD_BEFORE.search(skeleton[max(0, index - 63) : index + 1]):
            return True
        if skeleton[index] != "," or index in in_list:
            return False
        window = skeleton[max(0, index - 1023) : index]
        froms = list(_FROM_WORD.finditer(window))
        if not froms:
            return False
        between = window[froms[-1].end() :]
        if "(" in between or ")" in between:
            return False
        for item in between.split(","):
            parts = item.split()
            if not 1 <= len(parts) <= 3 or not _IDENT_SHAPE.match(parts[0].replace(".", "_")):
                return False
            if len(parts) == 3 and parts[1].casefold() != "as":
                return False
        return True

    def alias_masked(start: int, content: str) -> bool:
        folded = content.casefold()
        if len(folded) >= MIN_VALUE_LEN and literals:
            inner = re.compile(_value_pattern(folded)) if folded.isascii() else None
            for text, pattern in literals:
                if (pattern.search(folded) if pattern else text in folded) or (
                    inner.search(text) if inner else folded in text
                ):
                    return True
        left, _right = segment(item_bounds, item_starts, start, start)
        if start - left > 300:  # 상수 하나짜리 항목은 짧다
            return False
        head = _AS_BEFORE.sub("", sql[left:start].rstrip())
        if _CONST_ITEM.fullmatch(head):
            return True
        single = _SINGLE_COLUMN_ITEM.fullmatch(head)  # 사람·서술형 칼럼 하나만인 항목
        column = (single.group(1) or single.group(2)) if single else None
        return bool(column) and grade_of(str(column)) in ("pii", "free_text")

    def common_kept(start: int, end: int, content: str) -> bool:
        qualified = sql[start - 1 : start] == "." or sql[end : end + 1] == "."
        return qualified or content.casefold() in names

    # 이 SQL 에서 정의해 남긴 별칭 이름 — ORDER BY·GROUP BY·HAVING 참조를 남긴다(값 자리는 제외)
    defined_aliases: set[str] = set()
    for match in _ALIAS_DEF.finditer(sql):
        if not plain[match.start() : match.start() + 2].isalpha():
            continue  # 문자열·주석 안의 AS
        token = match.group(1)
        content = token[1:-1] if token[0] in "`\"'" else token
        begin, finish = match.start(1), match.end(1)
        if _prompt_word(content, prompt) or (
            common_kept(begin, finish, content)
            or (_IDENT_SHAPE.match(content) and not alias_masked(begin, content))
        ):
            defined_aliases.add(content.casefold())

    def masked(start: int, end: int, content: str) -> bool:
        if common_kept(start, end, content):
            return False
        if not _IDENT_SHAPE.match(content):
            return True
        if _AS_BEFORE.search(skeleton[max(0, start - 64) : start]):
            return alias_masked(start, content)
        if value_slot(start):
            return True
        if table_slot(start) or content.casefold() in defined_aliases:
            return False
        left, right = segment(seg_bounds, seg_starts, start, end)
        total = prefix[bisect.bisect_left(word_starts, right)] - prefix[
            bisect.bisect_left(word_starts, left)
        ]
        own = 0
        index = bisect.bisect_left(word_starts, end) - 1
        while index >= 0 and word_ends[index] > start:
            own += word_risk[index]
            index -= 1
        return total - own > 0

    return masked


def _literal_plan(
    sql: str,
    *,
    policy: ColumnPolicy | None,
    prompt: str,
    catalog: frozenset[str] = frozenset(),
    identifier_mode: bool = False,
) -> list[tuple[re.Match[str], str]]:
    """토큰마다 처분 — `comment`·`keep`·`mask`. 근거 없는 리터럴은 전부 `mask`.

    `identifier_mode`(`redact_sql`만 켠다)면 백틱·`AS` 뒤 따옴표 별칭은 `ident`로 두고 처분을
    `_identifier_judge`에 맡긴다. 끄면(`redact_text`) 예전처럼 백틱은 ASCII 이름·프롬프트 말만
    남긴다.
    """
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
        elif identifier_mode and (
            token[0] == "`"
            or _AS_BEFORE.search(skeleton[max(0, match.start() - 64) : match.start()])
        ):
            decision = "ident"
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


def _redact_plain(
    text: str,
    offset: int,
    prompt: str,
    allowed: frozenset[str],
    in_identifier_slot: Callable[[int, int, str], bool],
    replace: Callable[[str], str] = lambda _word: MASK,
) -> str:
    """토큰 사이 SQL 본문 — 따옴표 없는 비ASCII 낱말은 식별자로 남기고 식별자 자리의 값만 가린다.

    그 턴 프롬프트에 있는 말 · 결과 요약이 열 이름으로 남긴 별칭(`allowed`)은 판정 없이 남긴다.
    `offset`은 `text`가 원 SQL 에서 시작하는 위치다. 가린 낱말 자리는 `replace(낱말)`(기본 표지 ·
    생성기가 있으면 가짜 값)이다.
    """

    def keep(match: re.Match[str]) -> str:
        word = match.group(0)
        if word in (prompt or "") or word in allowed:
            return word
        start = offset + match.start()
        return replace(word) if in_identifier_slot(start, start + len(word), word) else word

    return _PLAIN_WORD.sub(keep, text)


def _operator_before(plain: str, index: int) -> bool:
    """`index` 앞(공백 건너뜀)이 비교 연산자 글자인가."""
    while index and plain[index - 1].isspace():
        index -= 1
    return bool(index) and plain[index - 1] in "=<>"


def _wrapped_compare(plain: str, paren: int | None, *, first: bool) -> bool:
    """여는 괄호 `paren` 안의 수가 비교 자리인가 — `= (N)` · `= CAST(N …)` · `= COALESCE(…, N)`.

    `first`는 수가 괄호 바로 뒤(첫 인자)인가다. 그 밖의 함수 인자(`ROUND(x, 2)`)는 구조다.
    """
    if paren is None:
        return False
    call = _WRAP_CALL_BEFORE.search(plain, max(0, paren - 16), paren)
    if call is None:
        return first and _operator_before(plain, paren)
    if call.group(1).upper() == "CAST" and not first:
        return False
    return _operator_before(plain, call.start())


def _compare_numbers(
    sql: str, policy: ColumnPolicy | None, catalog: frozenset[str]
) -> list[tuple[int, int]]:
    """따옴표 없는 수 중 가짜 값으로 바꿀 비교 자리 구간(감사 M-B · 생성기 경로만).

    비교 자리 = 공백·부호를 건너뛴 바로 앞이 비교 연산자(`= <> != < > <= >=`) · `IN (` 목록의 여는
    괄호나 목록 쉼표 · LIKE · BETWEEN · BETWEEN 짝 AND · WHEN(단순 CASE 비교값). 그 술어 조각의
    가장 엄격한 등급(`_segment_grades` — BETWEEN 위쪽 경계는 BETWEEN 자리 조각)이 pii·free_text·
    unclassified·amount 이거나 근거 없음(None)이면 바꾼다.

    경계 근거 — 비교 자리 수는 조회 조건으로 쓴 **데이터 값**(사번·금액)이라 결과 셀과 같은 값이다.
    그 밖의 수는 구조다: LIMIT·OFFSET·`LIMIT n, m`·FETCH FIRST n·TOP n·INTERVAL n(앞이 키워드),
    함수 인자(`ROUND(x, 2)`·`SUBSTRING(x, 1, 3)` — 여는 괄호·쉼표가 `IN (` 목록이 아님),
    산술 피연산자(`b * 100`)·ORDER/GROUP BY 순번·THEN/ELSE 결과값. 등급이 general·network 인
    조각의 비교값(`use_yn = 1`)도 진단용으로 남긴다(network 는 끝의 IP 패스가 가린다).

    비교 연산자 바로 뒤 괄호 한 겹(`= (N)`)과 그 자리의 `CAST(N AS …)` 첫 인자 ·
    `COALESCE(x, N)` 인자도 비교 자리다(교정 3차). 남긴 잔여(감사 재확인 2차 — 값싼 판정 밖):
    - 산술 둘째 이후 피연산자(`= 1 + N`)
    - 왼쪽 리터럴(`N = col`)
    - `IS NOT DISTINCT FROM N`
    - 값 하위 질의 목록(`= ANY (SELECT N)`)
    - THEN/ELSE 결과값(`CASE … THEN N`)
    - 지수·16진 꼴(`1e5` · `0x1F` — `_SQL_NUMBER`가 잡지 않는다)
    - 두 겹 이상 감싼 꼴(`= (CAST(N AS CHAR))`)

    자릿수 하한은 두지 않는다 — 1~2자리 값도 등급 규칙대로 바꾸고, 공간이 모자라면 `<가림>`
    표지로 내린다(A8 설계 · 유용성 손실이지 누출이 아니다). 교정 3차b 에서 1~2자리를 남겼다가
    3차e 에 철회했다(verifier Minor-D — SQL `= 12` 원문과 같은 레코드 표본의 가짜 값 `78`이 짝을
    이뤄 run 전체의 `78`이 실제 `12`로 풀렸다).
    """
    identifiers: set[str] = set()
    if policy is not None:
        identifiers = {n.casefold() for n in policy.column_names() | policy.table_names()}
    grade_at = _segment_grades(_same_length_skeleton(sql, identifiers), policy, catalog)
    plain = _SQL_TOKEN.sub(lambda m: " " * len(m.group(0)), sql)
    in_list = _paren_marks(plain)[1]
    # 쉼표 → 그 쉼표를 감싼 여는 괄호(`COALESCE(x, N)` 판정용)
    owner: dict[int, int] = {}
    stack: list[int] = []
    for index, char in enumerate(plain):
        if char == "(":
            stack.append(index)
        elif char == ")" and stack:
            stack.pop()
        elif char == "," and stack:
            owner[index] = stack[-1]
    spans: list[tuple[int, int]] = []
    for match in _SQL_NUMBER.finditer(plain):
        start, end = match.span()
        cut = start
        while cut and plain[cut - 1].isspace():
            cut -= 1
        if cut and plain[cut - 1] in "+-":
            cut -= 1
            while cut and plain[cut - 1].isspace():
                cut -= 1
        if not cut:
            continue
        before = plain[cut - 1]
        anchor = start
        if before in "=<>" or (before in "(," and cut - 1 in in_list):
            pass
        elif before in "(," and _wrapped_compare(
            plain, cut - 1 if before == "(" else owner.get(cut - 1), first=before == "("
        ):
            pass
        elif word := _COMPARE_WORD_BEFORE.search(plain, max(0, cut - 8), cut):
            if word.group(0).upper() == "AND":
                pair = _BETWEEN_AND_BEFORE.search(plain, max(0, cut - 220), cut)
                if pair is None:
                    continue
                anchor = pair.start()
        else:
            continue
        if grade_at(anchor, anchor + 1 if anchor != start else end) in _COMPARE_FAKE_GRADES:
            spans.append((start, end))
    return spans


def redact_sql(
    sql: str,
    *,
    policy: ColumnPolicy | None,
    vault: PiiVault,
    prompt: str = "",
    allowed_words: Iterable[str] = (),
    catalog_columns: Iterable[str] = (),
) -> str:
    """실행 SQL — 구조와 식별자는 남기고 리터럴·주석은 근거 있을 때만 남긴다.

    식별자(백틱 · `AS` 뒤 따옴표 별칭 · 따옴표 없는 비ASCII 낱말)는 카탈로그 실존과 무관하게
    이름으로 남긴다 — 지어낸 칼럼명도 진단에 보여야 한다(D-301 부기 2026-10-07). 사람·서술형
    값 자리·사람 컬럼 곁·값 리터럴과 겹치는 별칭처럼 값 근거가 있는 비한정·비카탈로그 낱말만 가린다
    (`_identifier_judge`).
    남긴 리터럴·식별자도 사람 값·카나리아·정규식과 대조하고, IP 는 어디서든 끝자리를 가린다.
    `allowed_words`는 결과 요약이 근거를 확인해 남긴 열 이름(별칭)이고, `catalog_columns`는 스키마
    카탈로그의 컬럼 이름이다(정책에 없는 컬럼과 같은 술어의 리터럴은 프롬프트 말이 아니면 가린다).
    vault 가 가짜 값 생성기를 품으면 가린 리터럴·주석 내용·식별자 자리 값은 가짜 값(리터럴은 내용
    전체가 키)이고, 사람 값이 든 남긴 리터럴은 그 값 자리만 가짜 값이다(정규식 PII 는 현행 가림).
    """
    text = str(sql or "")[:INPUT_MAX]
    allowed = frozenset(word for word in allowed_words if word and not _value_unsafe(word, vault))
    catalog = frozenset(c.casefold() for c in catalog_columns)
    plan = _literal_plan(
        text, policy=policy, prompt=prompt, catalog=catalog, identifier_mode=True
    )
    in_slot = _identifier_judge(
        text,
        policy=policy,
        catalog=catalog,
        masked_literals=[m.group(0)[1:-1] for m, decision in plan if decision == "mask"],
        prompt=prompt,
    )
    plan = [
        (
            match,
            (
                "mask"
                if not _prompt_word(match.group(0)[1:-1], prompt)
                and in_slot(match.start(), match.end(), match.group(0)[1:-1])
                else "keep"
            )
            if decision == "ident"
            else decision,
        )
        for match, decision in plan
    ]
    kept = [m.group(0)[1:-1] for m, decision in plan if decision == "keep"]
    unsafe = dict(zip(kept, _unsafe_flags(kept, vault), strict=True))
    generating = vault.fakes is not None
    #: (글, 이번 호출이 만든 가짜 값인가) — 끝의 IP 패스는 만든 조각을 다시 바꾸지 않는다(HIGH-1)
    pieces: list[tuple[str, bool]] = []
    #: 비교 자리 데이터 수(감사 M-B) — 생성기 경로만 가짜 값(생성기 없는 경로는 현행 유지)
    numbers = _compare_numbers(text, policy, catalog) if generating else []

    def add_plain(start: int, stop: int) -> None:
        index = bisect.bisect_left(numbers, (start, start))
        while index < len(numbers) and numbers[index][0] < stop:
            number_start, number_end = numbers[index]
            pieces.append(
                (
                    _redact_plain(
                        text[start:number_start], start, prompt, allowed, in_slot, vault.substitute
                    ),
                    False,
                )
            )
            pieces.append((vault.substitute(text[number_start:number_end]), True))
            start = number_end
            index += 1
        rest = _redact_plain(text[start:stop], start, prompt, allowed, in_slot, vault.substitute)
        pieces.append((rest, False))

    cursor = 0
    for match, decision in plan:
        token = match.group(0)
        add_plain(cursor, match.start())
        made = generating
        if decision == "comment":
            opener = "/*" if token.startswith("/*") else ("--" if token.startswith("--") else "#")
            inner = vault.substitute(_token_content(token))
            replacement = f"/*{inner}*/" if opener == "/*" else f"{opener} {inner}"
        elif decision == "mask":
            replacement = f"{token[0]}{vault.substitute(token[1:-1])}{token[-1]}"
        elif unsafe.get(token[1:-1]):
            inner = (
                MASK_PII
                if not generating
                else _scrub_free_text(vault.scrub(vault.mask_ip(token[1:-1])))
            )
            replacement = f"{token[0]}{inner}{token[-1]}"
        elif (
            generating
            and ip_prefix(token[1:-1]) is not None
            and not _prompt_word(token[1:-1], prompt)
        ):
            # IP 접두 리터럴(`10.0.1.%`)은 가짜 IP 와 같은 계층 메모로 바꾼다(실 접두 짝 노출 방지)
            replacement = f"{token[0]}{vault.substitute(token[1:-1])}{token[-1]}"
        else:
            replacement, made = token, False
        pieces.append((replacement, made))
        cursor = match.end()
    add_plain(cursor, len(text))
    if not generating:
        return _scrub_free_text(vault.scrub(vault.mask_ip("".join(p for p, _ in pieces))))
    joined = "".join(piece if made else vault.mask_ip(piece) for piece, made in pieces)
    return _scrub_free_text(vault.scrub(joined))


_SQL_NUMBER = re.compile(r"(?<![\w.])[0-9][0-9.]*(?<!\.)(?![\w.])")


def sql_literal_contents(sql: str) -> list[str]:
    """SQL 의 문자열 리터럴 내용과 수 리터럴(2단계 1차 등록용 원값 — 산출물에 쓰지 않는다)."""
    out: list[str] = []
    last = 0
    for match in _SQL_TOKEN.finditer(sql):
        out += _SQL_NUMBER.findall(sql, last, match.start())
        token = match.group(0)
        if token[0] in "'\"":
            out.append(token[1:-1].replace(token[0] * 2, token[0]))
        last = match.end()
    out += _SQL_NUMBER.findall(sql, last)
    return out


def _token_content(token: str) -> str:
    if token.startswith("/*"):
        return token[2:-2].strip()
    if token.startswith("--"):
        return token[2:].strip()
    if token.startswith("#"):
        return token[1:].strip()
    return token[1:-1]


def _overlaps(spans: Sequence[tuple[int, int]], start: int, end: int) -> bool:
    """정렬된 서로 겹치지 않는 구간 목록에 [start, end)와 겹치는 구간이 있는가."""
    index = bisect.bisect_left(spans, (end, -1)) - 1
    return index >= 0 and spans[index][1] > start


def _sub_outside(
    pattern: re.Pattern[str],
    text: str,
    spans: list[tuple[int, int]],
    replace: Callable[[re.Match[str]], tuple[str, bool]],
) -> tuple[str, list[tuple[int, int]]]:
    """`re.sub`과 같되 이번 호출이 만든 가짜 값 구간(`spans`)과 겹치는 매치는 그대로 둔다.

    `replace(match)` → ``(바꾼 글, 만든 가짜 값 구간인가)``. 구간은 새 글 기준으로 다시 잡는다.
    """
    edits = [(start, end, text[start:end], True) for start, end in spans]
    for match in pattern.finditer(text):
        if spans and _overlaps(spans, match.start(), match.end()):
            continue
        replacement, made = replace(match)
        edits.append((match.start(), match.end(), replacement, made))
    if len(edits) == len(spans):
        return text, spans
    edits.sort(key=lambda edit: edit[0])
    pieces: list[str] = []
    out_spans: list[tuple[int, int]] = []
    cursor = position = 0
    for start, end, replacement, made in edits:
        pieces.append(text[cursor:start])
        position += start - cursor
        if made and replacement:
            out_spans.append((position, position + len(replacement)))
        pieces.append(replacement)
        position += len(replacement)
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces), out_spans


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

    vault 가 가짜 값 생성기를 품으면 ①~④의 가린 자리는 가짜 값이다 — ②는 내용이 키라 같은 턴
    SQL 의 가린 리터럴과 같은 가짜 값이다. 뒤 단계는 **이 호출이 앞 단계에서 만든 가짜 값 구간**만
    다시 바꾸지 않는다(전역 가짜 값 등록부로 통과시키지 않는다 — 원값이 우연히 가짜 값과 같아도 원값
    등록 경로를 거친다 · 감사 HIGH-1). 키 이름 `PRIMARY`는 고정 어구로 남긴다(verifier Low-4).
    """
    # 남길 앞부분 + 여유만 본다 — 가림은 자르기 전에 끝내므로 경계에 걸린 값 조각은 버려지는 쪽에
    # 있다.
    out = str(text or "")[: limit + 1000]
    generating = vault.fakes is not None
    phrases = _ERROR_PHRASES | _ERROR_KEY_NAMES if generating else _ERROR_PHRASES
    spans: list[tuple[int, int]] = []

    def near(match: re.Match[str]) -> tuple[str, bool]:
        body = match.group("body")
        content = body if body is not None else match.group("tail")
        line = match.group("line")
        inner = vault.substitute(content)
        return f"near '{inner}'" + (f" at line {line}" if line else ""), generating

    out, spans = _sub_outside(_NEAR, out, spans, near)
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
            if content not in out:
                continue  # 문구에 없는 내용은 생성기에 넣지 않는다
            replacement: list[str] = []

            def literal(_match: re.Match[str], content: str = content) -> tuple[str, bool]:
                if not replacement:  # 문구에 실제로 남아 있을 때만 한 번 치환한다
                    replacement.append(vault.substitute(content))
                return replacement[0], generating

            out, spans = _sub_outside(re.compile(re.escape(content)), out, spans, literal)
        sql_idents |= set(_IDENT.findall(_skeleton(str(one)[:INPUT_MAX])))

    def quoted(match: re.Match[str]) -> tuple[str, bool]:
        content = match.group(1)
        if (
            content in sql_idents
            or content in phrases
            or content == MASK
            or _trivially_safe(content, prompt)
        ):
            return match.group(0), False
        return f"'{vault.substitute(content)}'", generating

    out, spans = _sub_outside(_ERROR_QUOTED, out, spans, quoted)
    if generating:
        out, spans = _sub_outside(_IPV4, out, spans, lambda m: (vault.mask_ip(m.group(0)), True))
        out = _scrub_free_text(vault.scrub(out))
    else:
        out = _scrub_free_text(vault.scrub(vault.mask_ip(out)))
    return out if len(out) <= limit else out[:limit] + "…"


# --- 누출 관문 (§3.5.5) -------------------------------------------------------------


#: 누출 관문 규칙 — 앞 5개는 모든 산출 파일 · `substitution`은 가짜 값 등록부 충돌·`trace.jsonl`
#: 치환 열 양성 검사·`substitutions.yaml`(plans/145) · `code_original`은 치환 코드값 파일만.
GATE_RULES: tuple[str, ...] = (
    "canary",
    "pii_value",
    "pii_regex",
    "schema_form",
    "user_info",
    "substitution",
    "code_original",
)
CODE_SAMPLES_FILE = "code_samples.yaml"
#: 가짜 값 목록 반출 파일(plans/145 §2.6 — 형식은 하류 차단(W3)이 읽는다 · 바꾸지 않는다)
SUBSTITUTIONS_FILE = "substitutions.yaml"
#: 가짜 값 반출 표기 고정 문구(`run.json` `substitution_note` · `report.md` 첫머리 ·
#: `substitutions.yaml` `note` — 관문이 값 형태로 검증한다)
SUBSTITUTION_NOTE = (
    "형식 보존 치환값 — 원값 아님 · 대응표 없음 · run마다 새 난수 · 외부망 테스트 전용 · "
    "설정 파일에 넣지 않는다"
)
#: `substitutions.yaml` 머리 칸
SUBSTITUTIONS_KEYS: tuple[str, ...] = ("db_id", "run_id", "note", "summary", "values")
#: `run.json` 사용자 정보 칸 — 생성기가 있으면 None 또는 이 run 가짜 값(관문 `substitution`)
RUN_USER_FIELDS: tuple[str, ...] = ("login_user", "operator", "host")
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

    def equal(self, folded: str) -> bool:
        """정규화한 글(`_fold`)이 원값과 같은가 — 부분 문자열은 보지 않는다(긴 값 대조용)."""
        return folded in self._equal

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


def substitutions_document(fakes: FakeValues, *, db_id: str, run_id: str) -> dict[str, Any]:
    """`substitutions.yaml` 본문 — 이 run 가짜 값 정렬 목록과 수만(원값·컬럼 연결 없음)."""
    values = fakes.fakes()
    return {
        "db_id": db_id,
        "run_id": run_id,
        "note": SUBSTITUTION_NOTE,
        "summary": {"values": len(values), "fallback": fakes.fallback_counts()},
        "values": values,
    }


def _substitutions_fields(doc: Any, is_fake: Callable[[str], bool]) -> Iterable[str]:
    """`substitutions.yaml` 형태 위반 칸(경로만 — 값 없음).

    머리 칸 정확히 5개 · 식별자 형식 · 고정 문구 · `summary` = 값 수(=목록 길이)·사유별 정수 ·
    `values`는 전부 이 run 생성기가 낸 가짜 값.
    """
    from .substitute import FALLBACK_REASONS

    if not isinstance(doc, Mapping) or set(doc) != set(SUBSTITUTIONS_KEYS):
        yield "keys"
        return
    for key in ("db_id", "run_id"):
        if not (isinstance(doc[key], str) and _CODE_SAMPLES_ID.match(doc[key])):
            yield key
    if doc["note"] != SUBSTITUTION_NOTE:
        yield "note"
    values = doc["values"]
    if not isinstance(values, list):
        yield "values"
        values = []
    summary = doc["summary"]
    fallback = summary.get("fallback") if isinstance(summary, Mapping) else None
    if (
        not isinstance(summary, Mapping)
        or set(summary) != {"values", "fallback"}
        or summary.get("values") != len(values)
        or not _is_count(summary.get("values"))
        or not isinstance(fallback, Mapping)
        or not set(fallback) <= set(FALLBACK_REASONS)
        or not all(_is_count(v) for v in fallback.values())
    ):
        yield "summary"
    for index, value in enumerate(values):
        if not (isinstance(value, str) and is_fake(value)):
            yield f"values[{index}]"


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


def _detail_leaves(node: Any) -> Iterable[Any]:
    """판정 상세 하위 트리의 잎(목록·사전 값을 따라 내려간다)."""
    if isinstance(node, Mapping):
        for value in node.values():
            yield from _detail_leaves(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _detail_leaves(value)
    else:
        yield node


class LeakGate:
    """산출 텍스트 전부를 기록 직전에 다시 훑는다. 걸리면 **위치만** 돌려준다(값 없음)."""

    def __init__(
        self,
        *,
        policy: ColumnPolicy,
        vault: PiiVault,
        user_values: Mapping[str, str | None],
        code_originals: CodeOriginals | None = None,
        fakes: FakeValues | None = None,
    ) -> None:
        self._policy = policy
        self._vault = vault
        self._code_originals = code_originals
        #: 가짜 값 등록부(`substitution` 규칙) — 없으면 vault 의 생성기
        self._fakes = fakes if fakes is not None else vault.fakes
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

    def _ip_left(self, text: str) -> bool:
        """IPv4 모양(`_IP_LEFT`)이 남았는데 이 run 가짜 값이 아닌가(생성기 없으면 모양만으로 — 그
        경로는 IP 를 `a.b.c.***`로 가리므로 네 덩이가 남으면 가림이 빠진 것이다 · 감사 M-A·M-A2).

        처분 — 다섯·여섯 덩이 연쇄(`172.31.45.67.8080` · 버전 `1.2.3.4.5.6`)는 가림이 앞 네 덩이를
        바꾸므로 통과하고, 원문 그대로 남았으면 막는다. 네 덩이 뒤에 다시 네 덩이가 남는 긴 연쇄
        (`1.3.6.1.4.1.9.9.1` 같은 OID)는 앞 네 덩이만 가려져 뒤 네 덩이가 남으므로 막는다(닫힌
        쪽 — 중간에 박힌 실 IP 와 구분할 근거가 없다). 세 자리 넘는 덩이(`10.0.19041.1`)는 IP
        모양이 아니다.
        전각 점 IP(`172．31．45．67`)는 가림·치환이 같은 구분자를 보므로 원문으로 남지 않아
        통과하고, 가림이 빠져 남았을 때만 막는다(교정 3차b).
        """
        fakes = self._fakes
        return any(
            fakes is None or not fakes.is_fake(match.group(0)) for match in _IP_LEFT.finditer(text)
        )

    def leaf_rules(self, text: str, *, schema_section: bool) -> list[str]:
        """산출물 잎 하나의 규칙 — `rules` + 남은 IPv4(`pii_regex` 확장).

        IP 검사는 생성기 거절 판정(`rules`)에 넣지 않는다 — 막 뽑은 가짜 IP 후보는 아직 이 run
        가짜 값이 아니라 전부 거절되기 때문이다.
        """
        found = self.rules(text, schema_section=schema_section)
        if "pii_regex" not in found and self._ip_left(text):
            found.append("pii_regex")
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

    def _substituted_ok(self, value: Any) -> bool:
        """치환 열 표본 원소 — 이 run 가짜 값 · 가림 표지만 통과(생성기 경로는 IP 도 가짜 값이라
        `a.b.c.***` 형태가 나오지 않는다)."""
        if not isinstance(value, str):
            return False
        if value in (MASK, MASK_PII):
            return True
        return self._fakes is not None and self._fakes.is_fake(value)

    def _detail_leaf_ok(self, value: Any) -> bool:
        """판정 상세 치환 칸의 잎 — 빈 값 · 표지 · 이 run 가짜 값(수는 글로 바꿔 본다)."""
        if value is None or value == "":
            return True
        if isinstance(value, bool):
            return False
        if isinstance(value, (int, float)):
            value = str(value)
        return self._substituted_ok(value)

    def _check_oracle(self, name: str, number: int, oracle: Any) -> list[dict[str, Any]]:
        """`substitution` ② — 판정 상세(`oracle.detail`)의 치환 칸(`oracle.substituted_fields`) 잎은
        전부 가짜 값·표지여야 한다. 키를 남기지 않는 기록(`keys_recorded: false`)이 키 목록을 싣는데
        치환 칸 표지가 없어도 위반이다. 생성기 없이 치환 칸 표지가 있으면 닫힌 쪽으로 실패한다.
        `key_tagged`(per_db) 기록은 키 맨 앞 태그가 **등록 DB id**(`judge.registered_db_ids`)일
        때만 치환 칸에서 뺀다 — 표지만 믿지 않고 `map_detail`이 태그마다 집합을 다시 본다. 등록
        밖 태그는 가짜 값이어야 하고, 태그도 일반 규칙(`leaf_rules`)은 그대로 받는다
        (교정 3차b·c)."""
        from .judge import DETAIL_FIELDS, detail_key_fields, map_detail

        if not isinstance(oracle, Mapping):
            return []
        fields = oracle.get("substituted_fields")
        detail = oracle.get("detail")

        def violation(field: str) -> dict[str, Any]:
            return {"file": name, "record": number, "field": field, "rule": "substitution"}

        if fields is None or fields == []:
            fields = []
        elif (
            self._fakes is None
            or not isinstance(fields, list)
            or not set(fields) <= set(DETAIL_FIELDS)
        ):
            return [violation("oracle.substituted_fields")]
        out: list[dict[str, Any]] = []
        if oracle.get("keys_recorded") is False:
            out += [
                violation(f"oracle.detail.{field.split('.')[0]}")
                for field in detail_key_fields(detail)
                if field not in fields
            ]
        if not isinstance(detail, Mapping):
            return out
        for key, node in detail.items():
            found: list[Any] = []

            def grab(part: Any, found: list[Any] = found) -> Any:
                found.append(part)
                return part

            map_detail(
                {key: node, "compare": detail.get("compare")},
                fields,
                substituted=grab,
                other=lambda part: part,
                key_tagged=oracle.get("key_tagged") is True,
            )
            leaves = (leaf for part in found for leaf in _detail_leaves(part))
            if not all(self._detail_leaf_ok(leaf) for leaf in leaves):
                out.append(violation(f"oracle.detail.{key}"))
        return out

    def _check_run(self, name: str, text: str) -> list[dict[str, Any]]:
        """`substitution` — `run.json` 사용자 정보 칸은 None 또는 이 run 가짜 값(생성기 있을 때)."""
        if self._fakes is None:
            return []
        try:
            meta = json.loads(text)
        except json.JSONDecodeError:
            meta = None
        if not isinstance(meta, Mapping):
            return [{"file": name, "record": None, "field": None, "rule": "substitution"}]
        return [
            {"file": name, "record": None, "field": field, "rule": "substitution"}
            for field in RUN_USER_FIELDS
            if meta.get(field) is not None and not self._substituted_ok(meta.get(field))
        ]

    def _check_trace(self, name: str, text: str) -> list[dict[str, Any]]:
        """`substitution` ② — 결과 열 표본·상위 값 양성 검사.

        `substituted: true` 열의 `sample` 원소·`top_values` 키는 전부 가짜 값(또는 표지)이어야 한다.
        비일반 등급·강등 열이 값을 싣는데 `substituted: true`가 아니어도 위반이다. 생성기 없이 치환
        열이 있으면 닫힌 쪽으로 실패한다.
        """
        violations: list[dict[str, Any]] = []
        for number, line in enumerate(text.splitlines(), start=1):
            try:
                record = json.loads(line) if line.strip() else None
            except json.JSONDecodeError:
                continue  # 줄 단위 규칙이 따로 본다
            if isinstance(record, Mapping):
                violations += self._check_oracle(name, number, record.get("oracle"))
            result = record.get("result") if isinstance(record, Mapping) else None
            columns = result.get("columns") if isinstance(result, Mapping) else None
            for index, column in enumerate(columns if isinstance(columns, list) else []):
                if not isinstance(column, Mapping):
                    continue
                substituted = column.get("substituted") is True
                plain = column.get("log_policy") in ("general", "network") and not column.get(
                    "demoted"
                )
                for key in ("sample", "top_values"):
                    if key not in column:
                        continue
                    items = column[key]
                    values = list(items) if isinstance(items, (list, Mapping)) else [items]
                    if substituted:
                        ok = self._fakes is not None and all(map(self._substituted_ok, values))
                    else:
                        ok = plain
                    if not ok:
                        violations.append(
                            {
                                "file": name,
                                "record": number,
                                "field": f"result.columns[{index}].{key}",
                                "rule": "substitution",
                            }
                        )
        return violations

    def _check_substitutions(self, name: str, text: str) -> list[dict[str, Any]]:
        """`substitution` ③ — 가짜 값 목록 파일 형태·값(생성기 없으면 닫힌 쪽 실패)."""
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError:
            doc = None
        fakes = self._fakes
        if fakes is None or doc is None:
            return [{"file": name, "record": None, "field": None, "rule": "substitution"}]
        return [
            {"file": name, "record": None, "field": field, "rule": "substitution"}
            for field in _substitutions_fields(doc, fakes.is_fake)
        ]

    def check(self, files: Mapping[str, str]) -> list[dict[str, Any]]:
        violations: list[dict[str, Any]] = []
        # `substitution` ① — 낸 가짜 값이 나중에 본 원값·수집 값과 같아졌으면 닫힌 쪽 실패(값 없음)
        if self._fakes is not None and self._fakes.collisions() > 0:
            violations.append({"file": None, "record": None, "field": None, "rule": "substitution"})
        for name, text in files.items():
            if name == CODE_SAMPLES_FILE:
                violations += self._check_code_samples(name, text)
            if name == SUBSTITUTIONS_FILE:
                violations += self._check_substitutions(name, text)
            if name == "trace.jsonl":
                violations += self._check_trace(name, text)
            if name == "run.json":
                violations += self._check_run(name, text)
            whole_schema = name.startswith("schema_catalog")
            seen_rules: set[str] = set()
            decoded: list[str] = []
            for record, field, leaf in self._units(name, text):
                decoded.append(leaf)
                section = whole_schema or field.split(".")[0].split("[")[0] == "schema_context"
                for rule in self.leaf_rules(leaf, schema_section=section):
                    seen_rules.add(rule)
                    violations.append(
                        {"file": name, "record": record, "field": field or None, "rule": rule}
                    )
            # 닫힌 쪽으로 실패: 잎을 이어 붙였을 때만 걸리는 규칙(잎 경계에 걸친 값)도 위반이다.
            if len(decoded) < 2:
                continue
            for rule in self.leaf_rules("\n".join(decoded), schema_section=whole_schema):
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
