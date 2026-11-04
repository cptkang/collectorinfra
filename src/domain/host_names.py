"""호스트 이름 정규화 — 대소문자·FQDN·K8s 워크로드 이름 (plans/196 §5.1 · 165 F3 공용).

제니퍼가 보낸 hostname과 폴스타 등록 이름을 맞대기 전에 쓰는 **순수 함수**다. 판정(일치·유력·
유사)은 호출부가 하고, 여기서는 비교 재료만 만든다. 표준 라이브러리만 쓴다 — noise_gate가
역방향으로 import할 수 있는 범위다(D-139 부기).

솔루션 사이 호스트 정규형(plans/211 §3 · D-363): 괄호 업무명 꼬리(`webap01(인터넷뱅킹)`)를 떼고
대소문자·끝 점·`-`·`_`를 접은 키(`canonical_host_key`)와 그 비교(`host_keys_match`), 같은 정규형을
DB 쪽에서 맞추는 엔진 공통 SQL 조건(`build_canonical_host_condition`)을 둔다. 컬럼 식은 호출부가
넘긴다 — 여기에는 스키마 이름이 없다.

계층: domain.
"""

from __future__ import annotations

import difflib
import ipaddress
import re
from collections.abc import Iterable

#: 이름을 쪼개는 구분자.
_SEPARATORS = re.compile(r"[-_.]+")
#: ReplicaSet 해시(`-6dd49d5df6`) — 16진 8~10자.
_REPLICASET_HASH = re.compile(r"-[0-9a-f]{8,10}$")
#: 파드 접미(`-x7k2p`) — 영소문자·숫자 5자. ReplicaSet 해시 **뒤에** 올 때만 뗀다.
_POD_SUFFIX = re.compile(r"-[a-z0-9]{5}$")
#: 디플로이먼트 꼬리.
_DEPLOY_TAILS: tuple[str, ...] = ("-deployment", "-deploy")
#: 해시 모양 토큰 — 숫자가 섞인 16진 6자 이상(업무명 토큰과 구분한다).
_HASH_TOKEN = re.compile(r"^(?=[0-9a-f]*\d)[0-9a-f]{6,}$")


def _is_ipv4(value: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(value), ipaddress.IPv4Address)
    except ValueError:
        return False


def normalize_hostname(value: str | None) -> str:
    """앞뒤 공백을 지우고 소문자로 바꾼다. IPv4는 그대로, FQDN이면 첫 레이블(짧은 이름)."""
    text = str(value or "").strip().lower().rstrip(".")
    if not text or _is_ipv4(text):
        return text
    return text.split(".", 1)[0]


def k8s_workload_name(value: str | None) -> str | None:
    """파드·ReplicaSet 이름 → 워크로드 이름. 바뀐 것이 없으면 None.

    순서: 파드 접미(ReplicaSet 해시 뒤에 있을 때만) → ReplicaSet 해시 →
    `-deploy`·`-deployment` 꼬리.
    """
    original = normalize_hostname(value)
    if not original or _is_ipv4(original):
        return None
    name = original
    without_pod = _POD_SUFFIX.sub("", name)
    if without_pod != name and _REPLICASET_HASH.search(without_pod):
        name = without_pod
    name = _REPLICASET_HASH.sub("", name)
    for tail in _DEPLOY_TAILS:
        if name.endswith(tail) and len(name) > len(tail):
            name = name[: -len(tail)]
            break
    return name if name and name != original else None


def name_tokens(value: str | None, min_len: int = 3) -> list[str]:
    """정규화 이름을 `-`·`_`·`.`로 쪼갠 토큰.

    길이 ≥ `min_len` · 순서 유지 · 중복 제거 · 해시 모양 토큰 제외.
    """
    text = normalize_hostname(value)
    out: list[str] = []
    for token in _SEPARATORS.split(text):
        if len(token) < min_len or _HASH_TOKEN.match(token) or token in out:
            continue
        out.append(token)
    return out


def name_similarity(a: str | None, b: str | None) -> float:
    """정규화한 두 이름의 `difflib.SequenceMatcher` 비율(0.0~1.0)."""
    left, right = normalize_hostname(a), normalize_hostname(b)
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right).ratio()


# ──────────────────────────────────────────────
# 솔루션 사이 호스트 정규형 (plans/211 §3 · D-363)
# ──────────────────────────────────────────────

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
    - FQDN: `(N = 'n' OR N = 's')`
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
            parts = [f"{norm} = {_sql_literal(n)}", f"{norm} = {_sql_literal(s)}"]
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
    "k8s_workload_name",
    "name_similarity",
    "name_tokens",
    "normalize_hostname",
    "split_business_suffix",
]
