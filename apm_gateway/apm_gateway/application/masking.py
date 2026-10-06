"""반환 전 마스킹 (plans/87 §5.2(c) · §8.2·§8.3 · plans/134 §4.3).

APM 응답의 실행 텍스트·메시지·SQL·URL은 **불신 데이터**이고 개인정보가 섞일 수 있다. 도구 반환과
감사에는 마스킹본만 남긴다. 규칙은 운영 샘플(U-6 · J0-O)로 확정하기 전까지 보수적으로 넓게 잡는다.
자격증명 제거는 이 단계 앞(어댑터 — `domain.credentials`)에서 이미 끝났다.

- 식별자 필드(`userId`·`clientId`·계정 `id`·사람 이름)는 `mask_identifier`(앞 1자 + `***`)로
  가린다 — 필드별 적용은 W1이다(G-11 미결 동안 원값을 내보내지 않는다).
- HTTP query 문자열(`a=1&b=2`)은 `mask_query`로 **첫 값까지** 가린다. `mask_url`도 앞 구분자 없이
  시작하는 첫 쌍을 가린다(COV E-20 — 종전 `mask_url('a=1&b=2')`가 `a=1`을 남겼다).
"""

from __future__ import annotations

import re

# 앞쪽 고정(같은 글자 묶음의 중간에서 다시 시작하지 않는다)·길이 상한 — 긴 단어 묶음에서 제곱 시간이
# 되지 않게(plans/134 W0-B MEDIUM-5 · 30KB 메시지에서 0.6초 정체 실측).
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24}"
)
_RRN = re.compile(r"\b\d{6}[- ]?[1-4]\d{6}\b")
_PHONE = re.compile(r"\b01[016789][- ]?\d{3,4}[- ]?\d{4}\b")
_IPV4 = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b")
_SQL_STR = re.compile(r"'(?:[^']|'')*'")
_SQL_NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])")
# 키 글자에서 구분자(`?`·`;`)를 뺀다 — 구분자가 많은 긴 문자열(`?a?a…`)에서 시작점마다 끝까지
# 다시 훑는 제곱 시간이 되지 않게(plans/134 W2 — 전문 마스킹 · 100KB 33초 실측).
# 빈 키(`?=v`)도 값을 가린다.
_URL_QUERY = re.compile(r"(^|[?&;])([^=&#;?\s]*)=([^&#\s]*)")
_SQL_HINT = re.compile(r"\b(select|insert|update|delete|merge|where|values|from)\b", re.IGNORECASE)
# PG 달러 따옴표 여는 표지(`$$`·`$tag$`) — 식별자 안의 `$`(`V$SESSION`·`a$b$c`)는 아니다
_DOLLAR_OPEN = re.compile(r"(?<![\w$])\$(?:[A-Za-z_][A-Za-z0-9_]{0,62})?\$")
# HTTP query의 값 없는 맨 항목 중 남기는 꼴(플래그 이름)
_QUERY_FLAG = re.compile(r"[A-Za-z_][\w.\-]{0,63}", re.ASCII)

TEXT_MAX = 300
# 길이 상한(`limit`)이 있으면 그 몇 배까지만 처리한다 — 처리하지 않은 원문은 출력에 나오지 않는다
# (출력은 처리한 앞부분에서만 자른다). `limit=None`(행 텍스트 전문 — plans/134 W2)은 전체를
# 처리하고, 규칙 정규식이 모두 선형이라 처리 시간은 길이에 비례한다
# (`tests/test_w2_coverage.py` 실측 단언).
_TEXT_SCAN_FACTOR = 8
_TEXT_SCAN_MIN = 4096


def mask_ip(value: str) -> str:
    """IPv4 마지막 두 옥텟을 가린다(대역만 남긴다)."""
    return _IPV4.sub(lambda m: f"{m.group(1)}.{m.group(2)}.*.*", str(value or ""))


def mask_url(text: str) -> str:
    """URL 쿼리 문자열 값만 가리고 키는 남긴다(앞 구분자 없이 시작하는 첫 쌍 포함)."""
    return _URL_QUERY.sub(lambda m: f"{m.group(1)}{m.group(2)}=<v>", str(text or ""))


def mask_query(query: str) -> str:
    """HTTP query 문자열 전용 — `?`를 떼고 `&`·`;`로 나눈 쌍의 값을 **첫 값까지** 모두 가린다.

    값 없는 항목은 플래그 이름 꼴(`flag` — 영문자로 시작하는 64자 이하 식별자)만 남기고 그 밖
    (`010-1234-5678`·한글 이름)은 `<v>`로 바꾼다(plans/134 W7 AUDIT-8). 키는 남긴다.
    """
    text = str(query or "")
    lead = "?" if text.startswith("?") else ""
    parts = re.split(r"([&;])", text[len(lead) :])
    out = [
        part
        if part in ("&", ";", "") or ("=" not in part and _QUERY_FLAG.fullmatch(part))
        else (part.split("=", 1)[0] + "=<v>" if "=" in part else "<v>")
        for part in parts
    ]
    return lead + "".join(out)


def mask_identifier(value: object) -> str:
    """사람·계정 식별자 — 앞 1자 + `***`(2자 이하는 `***` · 빈 값은 그대로)."""
    text = "" if value is None else str(value)
    if not text:
        return text
    return "***" if len(text) <= 2 else text[0] + "***"


def mask_pii(text: str) -> str:
    """설정 값 전용 개인정보 가림 — 이메일·주민번호·휴대폰만(plans/134 W7 · G-11 미결 동안).

    환경변수·JVM 시스템 속성·데이터 서버 설정 값은 `k=v`·숫자·SQL 단어가 섞인 설정 문자열이라
    `mask_text`(SQL 리터럴·URL 쿼리 값까지 가림)를 쓰면 `-Dport=8080` 같은 일반 설정이 훼손된다.
    서버 IP는 인프라 정보라 가리지 않는다(인스턴스 목록 `ip_address`와 같은 처분).
    """
    out = _EMAIL.sub("<email>", str(text or ""))
    out = _RRN.sub("<rrn>", out)
    return _PHONE.sub("<phone>", out)


def _mask_dollar_quotes(text: str) -> str:
    """PG 달러 따옴표 리터럴(`$$…$$`·`$tag$…$tag$`)을 `?`로 — 닫는 표지가 없으면 끝까지 가린다.
    여는 표지를 찾은 자리부터만 닫는 표지를 찾아 선형이다."""
    out: list[str] = []
    pos = 0
    while True:
        opener = _DOLLAR_OPEN.search(text, pos)
        if opener is None:
            break
        close = text.find(opener.group(0), opener.end())
        out.append(text[pos : opener.start()])
        out.append("?")
        if close < 0:
            pos = len(text)
            break
        pos = close + len(opener.group(0))
    if not out:
        return text
    out.append(text[pos:])
    return "".join(out)


def mask_sql(text: str) -> str:
    """SQL 리터럴을 `?`로 바꾼다(바인드 값 노출 방지) — PG 달러 따옴표 · 작은따옴표 문자열 · 숫자.
    큰따옴표는 DB2·PG 식별자(`"SCHEMA"."TABLE"`)와 겹쳐 가리지 않는 대신, 리터럴 밖에 남은
    이메일·주민번호·휴대폰을 `mask_pii`로 한 번 더 가린다(MySQL 큰따옴표 문자열 · plans/134 W7
    AUDIT-8)."""
    out = str(text or "")
    if "$" in out:
        out = _mask_dollar_quotes(out)
    return mask_pii(_SQL_NUM.sub("?", _SQL_STR.sub("?", out)))


def mask_text(text: str, limit: int | None = TEXT_MAX) -> str:
    """자유 텍스트 — 이메일·주민번호·휴대폰·IP · SQL처럼 보이면 리터럴 · URL 쿼리 값을 가리고
    `limit`자로 자른다. `limit=None`이면 자르지 않는다(마스킹한 전문 — 행 텍스트 칸)."""
    out = str(text or "")
    if limit is not None:
        out = out[: max(limit * _TEXT_SCAN_FACTOR, _TEXT_SCAN_MIN)]
    if _SQL_HINT.search(out):
        out = mask_sql(out)
    out = mask_url(out)
    out = _EMAIL.sub("<email>", out)
    out = _RRN.sub("<rrn>", out)
    out = _PHONE.sub("<phone>", out)
    out = mask_ip(out)
    return out if limit is None else out[:limit]
