"""호스트명 정규형 비교 (plans/211 §3.1·§3.2 · D-363) — 게이트웨이 사본.

정본은 본체 `src/domain/host_names.py`다. 게이트웨이는 본체를 import하지 않으므로(D-274 —
`tests/test_boundary.py`) 같은 규칙을 여기 사본으로 둔다. 두 구현의 동등성은 계획서 §5 벡터 표를
각 패키지 테스트가 그대로 단언하는 것으로 확인한다(`tests/test_plan211_host_canonical.py`).

- `split_business_suffix`: 「호스트(업무명)」 꼴에서 반각 괄호 앞을 호스트 부분으로 뗀다.
- `canonical_host_key`: 소문자 · 끝 점 제거 · 레이블마다 `-`·`_` 제거. IP는 표준 표기. 빈 레이블이나
  `[a-z0-9.]` 밖 문자가 있으면 빈 문자열(키 아님 — 비교하지 않는다).
- `host_keys_match`: IP는 정확 일치 · 둘 다 FQDN이면 정확 일치(도메인이 다르면 다른 호스트 ·
  D-224) · 그 밖은 첫 레이블 일치.
"""

from __future__ import annotations

import ipaddress
import re

_KEY = re.compile(r"[a-z0-9.]+")


def split_business_suffix(value: str | None) -> tuple[str, str]:
    """「호스트(업무명)」 → (호스트 부분, 업무명). 괄호가 없으면 업무명은 빈 문자열."""
    text = str(value or "").strip()
    i = text.find("(")
    if i < 0:
        return text, ""
    host = text[:i].rstrip()
    biz = text[i + 1 :].strip()
    if biz.endswith(")"):
        biz = biz[:-1].strip()
    return host, biz


def _ip_key(text: str) -> str | None:
    """IP로 읽히면 표준 표기(IPv6 압축), 아니면 None."""
    try:
        return ipaddress.ip_address(text).compressed
    except ValueError:
        return None


def canonical_host_key(value: str | None) -> str:
    """비교용 정규형 키. 빈 문자열이면 키가 아니다(비교하지 않는다)."""
    host, _ = split_business_suffix(value)
    text = host.lower().rstrip(".")
    if not text:
        return ""
    ip = _ip_key(text)
    if ip is not None:
        return ip
    labels = [lab.replace("-", "").replace("_", "") for lab in text.split(".")]
    if any(not lab for lab in labels):
        return ""
    key = ".".join(labels)
    return key if _KEY.fullmatch(key) else ""


def host_keys_match(a: str | None, b: str | None) -> bool:
    """두 호스트 표기가 같은 호스트를 가리키는가(§3.2)."""
    ka, kb = canonical_host_key(a), canonical_host_key(b)
    if not ka or not kb:
        return False
    if _ip_key(ka) is not None or _ip_key(kb) is not None:
        return ka == kb
    if "." in ka and "." in kb:
        return ka == kb
    return ka.split(".")[0] == kb.split(".")[0]
