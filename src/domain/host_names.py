"""호스트 이름 정규화 — 대소문자·FQDN·K8s 워크로드 이름 (plans/196 §5.1 · 165 F3 공용).

제니퍼가 보낸 hostname과 폴스타 등록 이름을 맞대기 전에 쓰는 **순수 함수**다. 판정(일치·유력·
유사)은 호출부가 하고, 여기서는 비교 재료만 만든다. 표준 라이브러리만 쓴다 — noise_gate가
역방향으로 import할 수 있는 범위다(D-139 부기).

계층: domain.
"""

from __future__ import annotations

import difflib
import ipaddress
import re

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


__all__ = ["k8s_workload_name", "name_similarity", "name_tokens", "normalize_hostname"]
