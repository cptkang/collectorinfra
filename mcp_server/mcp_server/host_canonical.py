"""솔루션 사이 호스트 정규형 — 본체 정본의 사본 (plans/211 §3 · D-363).

정본은 본체 ``src/domain/host_names.py``(``split_business_suffix``·``canonical_host_key``·
``host_keys_match``·``build_canonical_host_condition``)다. mcp_server는 본체와 양방향 import 0이라
(D-139) 같은 동작을 여기에 사본으로 둔다. 동등성은 ``tests/test_plan211_host_canonical.py``의
§5 벡터·SQL 문자열 단언으로 고정한다 — 정본을 고치면 이 사본과 벡터 테스트를 함께 고친다.

괄호 업무명 꼬리(``webap01(인터넷뱅킹)``)를 떼고 대소문자·끝 점·``-``·``_``를 접은 키와 그 비교,
같은 정규형을 DB 쪽에서 맞추는 엔진 공통 SQL 조건을 둔다. 컬럼 식은 호출부가 넘긴다 — 여기에는
스키마 이름이 없다. 표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable

#: 정규형 키가 될 수 있는 문자 — 와일드카드(`%`·`_`)·따옴표·공백·한글은 키가 아니다.
_CANONICAL_KEY = re.compile(r"[a-z0-9.]+")


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def split_business_suffix(value: str | None) -> tuple[str, str]:
    """`호스트(업무명)` → (호스트 부분, 업무명). 괄호가 없으면 업무명은 빈 문자열.

    반각 `(`만 본다. 괄호 앞 공백은 호스트 부분에서 지우고, 업무명 끝의 `)`는 뗀다.
    """
    text = str(value or "").strip()
    i = text.find("(")
    if i < 0:
        return text, ""
    host = text[:i].rstrip()
    biz = text[i + 1:].strip()
    if biz.endswith(")"):
        biz = biz[:-1].strip()
    return host, biz


def canonical_host_key(value: str | None) -> str:
    """솔루션 사이 비교용 호스트 정규형. 빈 문자열이면 키가 아니다(비교·SQL 조립 안 함).

    업무명 꼬리를 떼고 소문자·끝 점 제거. IP면 `ipaddress` 압축 표기, 아니면 레이블마다
    `-`·`_`를 지운다. 빈 레이블이 있거나 `[a-z0-9.]` 밖의 문자가 남으면 키가 아니다.
    """
    host, _ = split_business_suffix(value)
    text = host.lower().rstrip(".")
    if not text:
        return ""
    try:
        return ipaddress.ip_address(text).compressed
    except ValueError:
        pass
    labels = [lab.replace("-", "").replace("_", "") for lab in text.split(".")]
    if any(not lab for lab in labels):
        return ""
    key = ".".join(labels)
    return key if _CANONICAL_KEY.fullmatch(key) else ""


def host_keys_match(a: str | None, b: str | None) -> bool:
    """두 호스트 표기가 같은 호스트인가(정규형 기준).

    IP가 끼면 정규형 완전 일치. 양쪽 모두 FQDN이면 완전 일치(도메인이 다르면 다른 호스트 —
    D-224). 그 밖(한쪽 이상 단일 레이블)은 첫 레이블끼리 비교한다.
    """
    ka, kb = canonical_host_key(a), canonical_host_key(b)
    if not ka or not kb:
        return False
    if _is_ip(ka) or _is_ip(kb):
        return ka == kb
    if "." in ka and "." in kb:
        return ka == kb
    return ka.split(".", 1)[0] == kb.split(".", 1)[0]


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_canonical_host_condition(
    column_expr: str, values: Iterable[str], *, business_suffix: bool,
) -> str | None:
    """컬럼 식을 정규형으로 접어 입력 호스트들과 맞추는 SQL 조건(엔진 공통 함수만).

    `N = REPLACE(REPLACE(LOWER(TRIM(col)), '-', ''), '_', '')`, `n` = 입력 정규형, `s` = 첫 레이블.

    - 단일 레이블: `(N = 's' OR N LIKE 's.%')`
    - FQDN: `(N IN ('n', 'n.', 's', 's.'))` — 끝 점이 붙어 저장된 값(`web.corp.`)도 맞춘다
    - `business_suffix=True`면 둘 다 `OR N LIKE 's(%' OR N LIKE 's (%'`를 덧붙인다
      (괄호 업무명 꼬리를 저장하는 쪽만 — D-363 부기 1).

    정규형이 빈 값이거나 IP인 입력은 조건을 만들지 않는다. 값이 여러 개면 값별 조건을 OR로
    묶고, 유효 값이 하나도 없으면 None. 정규형은 `[a-z0-9.]`뿐이라 LIKE 와일드카드가 섞이지 않는다.
    """
    norm = f"REPLACE(REPLACE(LOWER(TRIM({column_expr})), '-', ''), '_', '')"
    conds: list[str] = []
    seen: set[str] = set()
    for value in values:
        n = canonical_host_key(value)
        if not n or n in seen or _is_ip(n):
            continue
        seen.add(n)
        s = n.split(".", 1)[0]
        if "." in n:
            keys = ", ".join(_sql_literal(k) for k in (n, n + ".", s, s + "."))
            parts = [f"{norm} IN ({keys})"]
        else:
            parts = [f"{norm} = {_sql_literal(s)}", f"{norm} LIKE {_sql_literal(s + '.%')}"]
        if business_suffix:
            parts += [
                f"{norm} LIKE {_sql_literal(s + '(%')}",
                f"{norm} LIKE {_sql_literal(s + ' (%')}",
            ]
        conds.append("(" + " OR ".join(parts) + ")")
    if not conds:
        return None
    return conds[0] if len(conds) == 1 else "(" + " OR ".join(conds) + ")"


__all__ = [
    "build_canonical_host_condition",
    "canonical_host_key",
    "host_keys_match",
    "split_business_suffix",
]
