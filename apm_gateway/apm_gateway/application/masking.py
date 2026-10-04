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

    값 없는 항목(`flag`)은 그대로 둔다. 키는 남긴다.
    """
    text = str(query or "")
    lead = "?" if text.startswith("?") else ""
    parts = re.split(r"([&;])", text[len(lead) :])
    out = [
        part if part in ("&", ";") or "=" not in part else part.split("=", 1)[0] + "=<v>"
        for part in parts
    ]
    return lead + "".join(out)


def mask_identifier(value: object) -> str:
    """사람·계정 식별자 — 앞 1자 + `***`(2자 이하는 `***` · 빈 값은 그대로)."""
    text = "" if value is None else str(value)
    if not text:
        return text
    return "***" if len(text) <= 2 else text[0] + "***"


def mask_sql(text: str) -> str:
    """SQL 문자열·숫자 리터럴을 `?`로 바꾼다(바인드 값 노출 방지)."""
    return _SQL_NUM.sub("?", _SQL_STR.sub("?", str(text or "")))


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
