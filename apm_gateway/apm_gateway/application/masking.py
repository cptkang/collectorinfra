"""반환 전 마스킹 (plans/87 §5.2(c) · §8.2·§8.3).

APM 응답의 실행 텍스트·메시지·SQL·URL은 **불신 데이터**이고 개인정보가 섞일 수 있다. 도구 반환과
감사에는 마스킹본만 남긴다. 규칙은 운영 샘플(U-6 · J0-O)로 확정하기 전까지 보수적으로 넓게 잡는다.
"""

from __future__ import annotations

import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RRN = re.compile(r"\b\d{6}[- ]?[1-4]\d{6}\b")
_PHONE = re.compile(r"\b01[016789][- ]?\d{3,4}[- ]?\d{4}\b")
_IPV4 = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b")
_SQL_STR = re.compile(r"'(?:[^']|'')*'")
_SQL_NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])")
_URL_QUERY = re.compile(r"([?&;])([^=&#\s]+)=([^&#\s]*)")
_SQL_HINT = re.compile(r"\b(select|insert|update|delete|merge|where|values|from)\b", re.IGNORECASE)

TEXT_MAX = 300


def mask_ip(value: str) -> str:
    """IPv4 마지막 두 옥텟을 가린다(대역만 남긴다)."""
    return _IPV4.sub(lambda m: f"{m.group(1)}.{m.group(2)}.*.*", str(value or ""))


def mask_url(text: str) -> str:
    """URL 쿼리 문자열 값만 가리고 키는 남긴다."""
    return _URL_QUERY.sub(lambda m: f"{m.group(1)}{m.group(2)}=<v>", str(text or ""))


def mask_sql(text: str) -> str:
    """SQL 문자열·숫자 리터럴을 `?`로 바꾼다(바인드 값 노출 방지)."""
    return _SQL_NUM.sub("?", _SQL_STR.sub("?", str(text or "")))


def mask_text(text: str, limit: int = TEXT_MAX) -> str:
    """자유 텍스트 — 이메일·주민번호·휴대폰·IP · SQL처럼 보이면 리터럴 · URL 쿼리 값을 가리고
    자른다."""
    out = str(text or "")
    if _SQL_HINT.search(out):
        out = mask_sql(out)
    out = mask_url(out)
    out = _EMAIL.sub("<email>", out)
    out = _RRN.sub("<rrn>", out)
    out = _PHONE.sub("<phone>", out)
    out = mask_ip(out)
    return out[:limit]
