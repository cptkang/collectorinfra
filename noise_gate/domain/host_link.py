"""제니퍼 hostname ↔ 관측 DB 등록 서버 연결 — 후보 등급·점수·사유·패턴 (plans/196 §1.1 · §5).

**순수 함수만 둔다**(LLM 0 · I/O 0). 후보 검색 SQL은 넓게 좁히기만 하고, 등급·점수·사유는 여기서
결정적으로 매긴다(D-224 ⑤와 같은 분담). 등급은 네 가지다.

| 등급 | 키 | 근거 |
|---|---|---|
| 확정 | `confirmed` | 저장된 확정 연결과 같은 서버 |
| 일치 | `exact` | 대소문자 무시 정확 일치(① a) |
| 유력 | `likely` | FQDN 짧은 이름(b) · IP 같음(c) · 이전 확정 패턴 적용(e) |
| 유사 | `similar` | 워크로드 이름 토큰 겹침 + 문자열 유사도(d) |

**패턴은 순위와 사유에만 쓴다**(Q7) — 패턴만으로 연결을 만들지 않는다. 패턴은 확정(confirmed)
연결에서만 뽑는다(`extract_patterns` — 호출부가 확정일 때만 부른다).

패턴 키는 `kind|from|to`다.
- `normalize||` — 대소문자·FQDN 접미 제거
- `k8s||` — K8s 해시·꼬리 제거
- `sep|a|b` — 구분자 a → b
- `prefix|A|B` — 접두 A → B (나머지 3자 이상 같음)
- `suffix|X|Y` — 접미 X → Y (앞부분 3자 이상 같음)

계층: domain — stdlib + `src.domain.host_names`(stdlib 순수 · D-139 부기)만 쓴다.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.domain.host_names import (
    k8s_workload_name,
    name_similarity,
    name_tokens,
    normalize_hostname,
)

GRADE_CONFIRMED = "confirmed"
GRADE_EXACT = "exact"
GRADE_LIKELY = "likely"
GRADE_SIMILAR = "similar"
#: 사용자가 직접 입력한 서버가 어느 넓힘에도 걸리지 않았을 때(선택 당시 등급 기록용).
GRADE_MANUAL = "manual"

#: 화면 등급 라벨.
GRADE_LABELS: dict[str, str] = {
    GRADE_CONFIRMED: "확정",
    GRADE_EXACT: "일치",
    GRADE_LIKELY: "유력",
    GRADE_SIMILAR: "유사",
    GRADE_MANUAL: "직접 입력",
}
_GRADE_RANK: dict[str, int] = {
    GRADE_CONFIRMED: 0, GRADE_EXACT: 1, GRADE_LIKELY: 2, GRADE_SIMILAR: 3, GRADE_MANUAL: 4,
}
_GRADE_SCORE: dict[str, float] = {GRADE_CONFIRMED: 1.0, GRADE_EXACT: 0.95, GRADE_LIKELY: 0.8}

#: 화면에 싣는 후보 수 상한.
MAX_CANDIDATES = 5
#: 후보 검색에 적용하는 학습 패턴 수 상한(건수 많은 순).
MAX_APPLIED_PATTERNS = 10

#: 연결 상태·종류(저장 레코드 · §5.4).
STATE_CONFIRMED = "confirmed"
STATE_PENDING = "pending"
KIND_LINK = "link"
KIND_ABSENT = "absent"

#: 페이로드 `meta.hints.polestar_link.basis` 값(§5.2).
BASIS_PREVIOUS = "previous_confirmed"
BASIS_USER = "user_selected"
#: (plans/199 · D-352) 제니퍼 파드의 노드 주소 정확 일치 1대 자동 연결 — 저장하지 않는다.
BASIS_NODE_ADDRESS = "node_address"

#: 노드 주소 연결 미확정 사유(plans/199 §2.2) — 「확인하지 못함」과 「없음」을 합치지 않는다.
NODE_NOT_REGISTERED = "node_not_registered"
NODE_AMBIGUOUS = "node_ambiguous"
NODE_UNCHECKED = "node_unchecked"

_PATTERN_LABELS: dict[str, str] = {
    "normalize": "대소문자·FQDN 접미 제거",
    "k8s": "K8s 해시·꼬리 제거",
    "sep": "구분자 정규화",
    "prefix": "접두 치환",
    "suffix": "접미 치환",
}
_SEPARATOR_CHARS = "-_."
#: SQL 토큰 넓힘에 쓰는 토큰 모양 — 영소문자·숫자만(LIKE 와일드카드·따옴표가 끼지 않게).
_SAFE_TOKEN = re.compile(r"^[a-z0-9]+$")
#: 치환 패턴의 공통부 최소 길이.
_AFFIX_COMMON_MIN = 3


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _lower(value: Any) -> str:
    return str(value or "").strip().lower()


def _base_name(hostname: str) -> str:
    """패턴 추출·적용의 기준 이름 — 워크로드 이름이 있으면 그것, 없으면 정규화 이름."""
    return k8s_workload_name(hostname) or normalize_hostname(hostname)


# ─── 패턴 ───────────────────────────────────────────────────────────


def pattern_label(key: str) -> str:
    """패턴 키 → 사람이 읽는 한 줄(예: 「접두 치환 bfm- → kbl-」)."""
    kind, _, rest = key.partition("|")
    src, _, dst = rest.partition("|")
    label = _PATTERN_LABELS.get(kind, kind)
    if kind in ("sep", "prefix", "suffix"):
        return f"{label} {src or '∅'} → {dst or '∅'}"
    return label


def _affix_pattern(base: str, target: str) -> str | None:
    """접두·접미 치환 패턴 1건 — 공통부가 긴 쪽(같으면 접두 치환)."""
    suffix_len = 0
    while (
        suffix_len < min(len(base), len(target))
        and base[-1 - suffix_len] == target[-1 - suffix_len]
    ):
        suffix_len += 1
    prefix_len = 0
    while prefix_len < min(len(base), len(target)) and base[prefix_len] == target[prefix_len]:
        prefix_len += 1
    best: str | None = None
    if suffix_len >= _AFFIX_COMMON_MIN and suffix_len >= prefix_len:
        best = f"prefix|{base[: len(base) - suffix_len]}|{target[: len(target) - suffix_len]}"
    elif prefix_len >= _AFFIX_COMMON_MIN:
        best = f"suffix|{base[prefix_len:]}|{target[prefix_len:]}"
    return best


def _sep_pattern(base: str, target: str) -> str | None:
    """같은 길이에서 구분자만 다르면 첫 차이의 `sep|a|b`."""
    if len(base) != len(target) or base == target:
        return None
    diffs = [(a, b) for a, b in zip(base, target, strict=True) if a != b]
    if all(a in _SEPARATOR_CHARS and b in _SEPARATOR_CHARS for a, b in diffs):
        a, b = diffs[0]
        return f"sep|{a}|{b}"
    return None


def extract_patterns(jennifer_hostname: str, targets: Sequence[str]) -> list[str]:
    """확정 연결 1건에서 이름 변환 패턴을 뽑는다(결정적 · 확정 연결에서만 부른다).

    Args:
        jennifer_hostname: 제니퍼가 보낸 hostname 원문
        targets: 연결된 서버의 등록 서버명·hostname(빈 값 무시)

    Returns:
        패턴 키 목록(중복 없음 · 원문과 대상이 같으면 빈 목록)
    """
    raw = _lower(jennifer_hostname)
    if not raw or "|" in raw:
        return []
    normalized = normalize_hostname(raw)
    workload = k8s_workload_name(raw)
    base = workload or normalized
    out: list[str] = []
    for target in dict.fromkeys(_lower(t) for t in targets):
        if not target or target == raw or "|" in target:
            continue
        key: str | None = None
        if normalized == target:
            key = "normalize||"
        elif workload and workload == target:
            key = "k8s||"
        elif base != target:
            key = _sep_pattern(base, target) or _affix_pattern(base, target)
        if key and key not in out:
            out.append(key)
    return out


def apply_pattern(key: str, hostname: str) -> str | None:
    """패턴을 hostname에 적용한 이름(적용되지 않으면 None · 결과가 원래 이름과 같아도 None)."""
    kind, _, rest = key.partition("|")
    src, _, dst = rest.partition("|")
    raw = _lower(hostname)
    if not raw:
        return None
    result: str | None
    if kind == "normalize":
        result = normalize_hostname(raw)
    elif kind == "k8s":
        result = k8s_workload_name(raw)
    else:
        base = _base_name(raw)
        if kind == "sep" and src and src in base:
            result = base.replace(src, dst)
        elif kind == "prefix" and base.startswith(src):
            result = dst + base[len(src):]
        elif kind == "suffix" and base.endswith(src):
            result = (base[: len(base) - len(src)] if src else base) + dst
        else:
            result = None
    return result if result and result != raw else None


# ─── 검색 계획 · 등급 ─────────────────────────────────────────────────


@dataclass(frozen=True)
class ProbePlan:
    """후보 검색 재료(① a~e) — SQL 조건과 등급 판정이 같은 값을 쓴다.

    Attributes:
        raw: 소문자 원문 hostname
        normalized: 정규화 이름(FQDN이면 짧은 이름)
        names: 대소문자 무시 정확 비교 이름(a · b의 짧은 이름 · e의 패턴 결과)
        prefixes: FQDN 후보를 찾을 단일 레이블(b)
        ips: IP 후보(c)
        workload: K8s 워크로드 이름(d · 없으면 None)
        tokens: 토큰 넓힘 재료(d · 영소문자·숫자만)
        pattern_names: 패턴 결과 이름 → 패턴 키(e)
        original: 앞뒤 공백만 지운 원문(「대소문자만 다름」 판정용)
    """

    raw: str
    normalized: str
    names: list[str]
    prefixes: list[str]
    ips: list[str]
    workload: str | None
    tokens: list[str]
    pattern_names: dict[str, str] = field(default_factory=dict)
    original: str = ""


def build_probe_plan(
    hostname: str,
    *,
    ip: str | None = None,
    patterns: Mapping[str, int] | None = None,
) -> ProbePlan:
    """hostname(+IP · 학습 패턴)으로 검색 계획을 만든다.

    Args:
        hostname: 제니퍼 hostname 원문
        ip: 인스턴스 IP(있으면 c)
        patterns: 학습 패턴 `{키: 건수}` — 건수 많은 순 상위 `MAX_APPLIED_PATTERNS`개만 적용한다
    """
    raw = _lower(hostname)
    normalized = normalize_hostname(raw)
    names: list[str] = [raw] if raw else []
    prefixes: list[str] = []
    ips: list[str] = []
    if raw and _is_ip(raw):
        ips.append(raw)
    elif raw:
        if normalized != raw:
            names.append(normalized)
        else:
            prefixes.append(normalized)
    ip_text = _lower(ip)
    if ip_text and _is_ip(ip_text) and ip_text not in ips:
        ips.append(ip_text)
    pattern_names: dict[str, str] = {}
    if raw and not _is_ip(raw):
        ordered = sorted((patterns or {}).items(), key=lambda kv: (-int(kv[1] or 0), kv[0]))
        for key, _count in ordered[:MAX_APPLIED_PATTERNS]:
            produced = apply_pattern(key, raw)
            if produced and produced not in names and produced not in pattern_names:
                pattern_names[produced] = key
                names.append(produced)
    workload = k8s_workload_name(raw) if raw and not _is_ip(raw) else None
    tokens = (
        [t for t in name_tokens(workload or normalized) if _SAFE_TOKEN.match(t)]
        if raw and not _is_ip(raw) else []
    )
    return ProbePlan(
        raw=raw, normalized=normalized, names=names, prefixes=prefixes, ips=ips,
        workload=workload, tokens=tokens, pattern_names=pattern_names,
        original=str(hostname or "").strip(),
    )


def _link_text(link: Mapping[str, Any]) -> str:
    by = str(link.get("approved_by") or link.get("by") or "")
    at = str(link.get("approved_at") or link.get("at") or "")
    return "·".join(p for p in (by, at) if p)


def is_confirmed_link(link: Mapping[str, Any] | None) -> bool:
    """확정(전역) 서버 연결인가 — 자동 사용은 이것만(Q5)."""
    return bool(
        isinstance(link, Mapping)
        and link.get("state") == STATE_CONFIRMED
        and link.get("kind") == KIND_LINK
        and link.get("db_id")
        and link.get("server_name")
    )


def grade_row(
    plan: ProbePlan,
    db_id: str,
    row: Mapping[str, Any],
    *,
    patterns: Mapping[str, int] | None = None,
    confirmed: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """조회 행 1건의 등급·점수·사유. 어느 근거에도 걸리지 않으면 None.

    Args:
        plan: `build_probe_plan` 결과
        db_id: 행이 나온 소스
        row: `{hostname, name, ipaddress}` 행
        patterns: 학습 패턴 건수(사유에 건수를 붙인다)
        confirmed: 이 hostname의 저장 연결(확정일 때만 확정 등급 근거)
    """
    hostname = str(row.get("hostname") or "").strip()
    name = str(row.get("name") or "").strip()
    ip = str(row.get("ipaddress") or "").strip()
    rh, rn = hostname.lower(), name.lower()
    grades: list[str] = []
    reasons: list[str] = []

    def _add(grade: str, reason: str) -> None:
        grades.append(grade)
        if reason not in reasons:
            reasons.append(reason)

    if (
        is_confirmed_link(confirmed)
        and confirmed is not None
        and str(confirmed.get("db_id")) == db_id
        and _lower(confirmed.get("server_name")) == rn
    ):
        text = _link_text(confirmed)
        _add(GRADE_CONFIRMED, f"이전 확정 — {text}" if text else "이전 확정")
    if plan.raw and plan.raw in (rh, rn):
        exact_case = (plan.original or plan.raw) in (hostname, name)
        _add(GRADE_EXACT, "이름 일치" if exact_case else "대소문자만 다름")
    if plan.normalized and plan.normalized != plan.raw and plan.normalized in (rh, rn):
        _add(GRADE_LIKELY, "FQDN 짧은 이름")
    if plan.prefixes and any(
        rh.startswith(p + ".") or normalize_hostname(rh) == p for p in plan.prefixes
    ) and plan.raw not in (rh, rn):
        _add(GRADE_LIKELY, "FQDN 짧은 이름")
    if ip and ip in plan.ips:
        _add(GRADE_LIKELY, "IP 같음")
    for produced, key in plan.pattern_names.items():
        if produced in (rh, rn):
            count = int((patterns or {}).get(key) or 0)
            suffix = f"({count}건)" if count else ""
            _add(GRADE_LIKELY, f"이전 확정 패턴 「{pattern_label(key)}」 적용{suffix}")

    similar_score = 0.0
    if plan.tokens:
        overlap = [t for t in plan.tokens if t in rh or t in rn]
        if overlap:
            base = plan.workload or plan.normalized
            ratio = len(overlap) / len(plan.tokens)
            sim = max(name_similarity(base, rn), name_similarity(base, rh))
            similar_score = round(0.6 * (0.5 * ratio + 0.5 * sim), 3)
            if not grades:
                head = "디플로이먼트 이름 토큰" if plan.workload else "이름 토큰"
                _add(GRADE_SIMILAR, f"{head} {'·'.join(overlap)} 일치")
                if plan.workload:
                    _add(GRADE_SIMILAR, "이름 토큰 유사 — 실행 노드 아님 가능")
    if not grades:
        return None
    grade = min(grades, key=lambda g: _GRADE_RANK[g])
    score = _GRADE_SCORE.get(grade, similar_score)
    return {
        "db_id": db_id,
        "server_name": name,
        "hostname": hostname,
        "ipaddress": ip,
        "grade": GRADE_LABELS[grade],
        "grade_key": grade,
        "score": score,
        "reasons": reasons,
    }


def rank_candidates(
    plan: ProbePlan,
    rows: Sequence[tuple[str, Mapping[str, Any]]],
    *,
    patterns: Mapping[str, int] | None = None,
    confirmed: Mapping[str, Any] | None = None,
    limit: int = MAX_CANDIDATES,
) -> list[dict[str, Any]]:
    """`(db_id, 행)` 목록 → 등급·점수 순 상위 후보(같은 서버는 한 건으로 합친다).

    정렬: 등급 → 점수(높은 순) → 소스 순서(입력 순) → 서버명. 결정적이다.
    """
    order: dict[str, int] = {}
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for db_id, row in rows:
        order.setdefault(db_id, len(order))
        graded = grade_row(plan, db_id, row, patterns=patterns, confirmed=confirmed)
        if graded is None:
            continue
        key = (db_id, graded["server_name"].lower(), graded["hostname"].lower())
        prev = merged.get(key)
        if prev is None or _GRADE_RANK[graded["grade_key"]] < _GRADE_RANK[prev["grade_key"]]:
            merged[key] = graded
    ranked = sorted(
        merged.values(),
        key=lambda c: (
            _GRADE_RANK[c["grade_key"]], -float(c["score"]), order[c["db_id"]], c["server_name"],
        ),
    )
    return ranked[: max(0, int(limit))]


def selection_grade(
    hostname: str,
    db_id: str,
    row: Mapping[str, Any],
    *,
    patterns: Mapping[str, int] | None = None,
) -> str:
    """사용자가 고른 서버의 선택 당시 등급 키(어느 근거에도 안 걸리면 `manual`)."""
    graded = grade_row(build_probe_plan(hostname, patterns=patterns), db_id, row, patterns=patterns)
    return str(graded["grade_key"]) if graded else GRADE_MANUAL


# ─── 저장 레코드 → 페이로드 힌트 (§5.2) ─────────────────────────────────


def absent_expired(link: Mapping[str, Any], now: datetime) -> bool:
    """부정 연결의 만료 여부(만료 시각이 없거나 못 읽으면 만료되지 않은 것으로 본다)."""
    raw = link.get("expires_at")
    if not raw:
        return False
    try:
        expires = datetime.fromisoformat(str(raw))
    except ValueError:
        return False
    if (expires.tzinfo is None) != (now.tzinfo is None):
        return False
    return expires <= now


def polestar_link_hint(link: Mapping[str, Any] | None, now: datetime) -> dict[str, Any]:
    """저장 연결 → `meta.hints.polestar_link`(자동 경로 — 확정만 쓴다 · Q5).

    - 확정 서버 연결 → `linked`(basis `previous_confirmed`)
    - 확정 부정 연결(만료 전) → `absent`
    - 그 밖(없음 · 확정 대기 · 만료) → `unresolved`
    """
    if isinstance(link, Mapping) and link.get("state") == STATE_CONFIRMED:
        by = str(link.get("approved_by") or link.get("by") or "")
        at = str(link.get("approved_at") or link.get("at") or "")
        if is_confirmed_link(link):
            return {
                "status": "linked",
                "db_id": str(link.get("db_id")),
                "server_name": str(link.get("server_name")),
                "hostname": str(link.get("polestar_hostname") or ""),
                "basis": BASIS_PREVIOUS,
                "by": by,
                "at": at,
            }
        if link.get("kind") == KIND_ABSENT and not absent_expired(link, now):
            return {"status": "absent", "by": by, "at": at}
    return {"status": "unresolved"}


def node_address_match(
    rows: Sequence[tuple[str, Mapping[str, Any]]],
    *,
    host_address: str | None = None,
    unchecked: bool = False,
) -> dict[str, Any]:
    """노드 주소 조회 행 → 연결 판정(plans/199 §2.2 · D-352 ②).

    Args:
        rows: `(db_id, 행)` 목록 — 행은 `{"hostname", "name", "ipaddress"}`(소스 전부의 합)
        host_address: 주면 행 IP가 이 값과 정확히 같은 행만 센다(조회가 넓게 걸러도 안전하게)
        unchecked: 조회하지 못한 소스가 하나라도 있으면 True

    Returns:
        - 서로 다른 서버(`db_id`·서버명 쌍) 정확히 1대 →
          `{"status": "linked", "db_id", "server_name", "hostname"}` — 실패 소스가 있어도 연결한다
          (D-352 부기 2 — 폴스타는 존 간 중복 등록이 거의 없다 · 실패 소스는 호출자가 근거에 적는다)
        - 2대 이상 → `{"status": "unresolved", "reason": NODE_AMBIGUOUS}`(실패 소스가 있어도
          이미 모호하다)
        - 0대 · 실패 소스 있음 → `NODE_UNCHECKED`(실패한 소스에 있을 수 있다)
        - 0대 → `NODE_NOT_REGISTERED`
    """
    wanted = _lower(host_address) if host_address is not None else None
    servers: dict[tuple[str, str], tuple[str, Mapping[str, Any]]] = {}
    for db_id, row in rows:
        if wanted is not None and _lower(row.get("ipaddress")) != wanted:
            continue
        name = _lower(row.get("name")) or _lower(row.get("hostname"))
        if not name:
            continue
        servers.setdefault((str(db_id), name), (str(db_id), row))
    if len(servers) >= 2:
        return {"status": "unresolved", "reason": NODE_AMBIGUOUS}
    if not servers:
        return {
            "status": "unresolved",
            "reason": NODE_UNCHECKED if unchecked else NODE_NOT_REGISTERED,
        }
    db_id, row = next(iter(servers.values()))
    return {
        "status": "linked",
        "db_id": db_id,
        "server_name": str(row.get("name") or row.get("hostname") or "").strip(),
        "hostname": str(row.get("hostname") or "").strip(),
    }


def user_selected_hint(
    *, db_id: str, server_name: str, polestar_hostname: str, by: str, at: str
) -> dict[str, Any]:
    """재조회 페이로드의 `polestar_link` — 사용자 선택(확정 대기여도 그 재조회에는 바로 쓴다)."""
    return {
        "status": "linked",
        "db_id": db_id,
        "server_name": server_name,
        "hostname": polestar_hostname,
        "basis": BASIS_USER,
        "by": by,
        "at": at,
    }


def apm_source_id(db_id: str, raw_source_id: Any = None) -> str:
    """연결 키의 제니퍼 소스 id — 알람 dbId(`jennifer_<소스 id>`)가 우선, 없으면 원문 값.

    수동 재분석 경로는 판정 기록에서 이벤트를 다시 만들어 원문 `apm` 객체가 없다 — dbId를 먼저
    써야 자동·수동 두 경로의 키가 같다.
    """
    text = str(db_id or "").strip()
    family, sep, rest = text.partition("_")
    if sep and rest and family:
        return rest
    raw = str(raw_source_id or "").strip()
    return raw or text


#: 사용자가 보낸 서버명·hostname의 허용 문자(프로브 전 화이트리스트 · 대소문자 무시).
_SERVER_TEXT = re.compile(r"^[a-z0-9._-]+$")
#: 일반 사용자에게 보이는 연결 레코드 키 — 누가·언제·어느 조사에서 골랐는지는 운영자에게만.
PUBLIC_LINK_KEYS: tuple[str, ...] = (
    "state", "kind", "db_id", "server_name", "polestar_hostname", "grade", "expires_at",
)


def valid_server_text(value: str) -> bool:
    """사용자 입력 서버명·hostname이 `[a-z0-9._-]`만으로 이루어졌는가(대소문자 무시)."""
    return bool(_SERVER_TEXT.match(str(value or "").strip().lower()))


def valid_link_key(source_id: str, hostname: str) -> bool:
    """연결 키로 쓸 수 있는가 — 구분자 `:`가 없고 정규화 hostname이 비지 않는다."""
    source = str(source_id or "").strip()
    host = str(hostname or "").strip()
    if not source or ":" in source or ":" in host:
        return False
    return bool(normalize_hostname(host))


def public_link_view(link: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """일반 사용자 뷰 연결 레코드(`PUBLIC_LINK_KEYS`만 · 없는 키는 싣지 않는다)."""
    if link is None:
        return None
    return {k: link[k] for k in PUBLIC_LINK_KEYS if k in link}


__all__ = [
    "BASIS_NODE_ADDRESS",
    "BASIS_PREVIOUS",
    "BASIS_USER",
    "GRADE_CONFIRMED",
    "GRADE_EXACT",
    "GRADE_LABELS",
    "GRADE_LIKELY",
    "GRADE_MANUAL",
    "GRADE_SIMILAR",
    "KIND_ABSENT",
    "KIND_LINK",
    "MAX_CANDIDATES",
    "NODE_AMBIGUOUS",
    "NODE_NOT_REGISTERED",
    "NODE_UNCHECKED",
    "PUBLIC_LINK_KEYS",
    "ProbePlan",
    "STATE_CONFIRMED",
    "STATE_PENDING",
    "absent_expired",
    "apm_source_id",
    "apply_pattern",
    "build_probe_plan",
    "extract_patterns",
    "grade_row",
    "is_confirmed_link",
    "node_address_match",
    "pattern_label",
    "polestar_link_hint",
    "public_link_view",
    "rank_candidates",
    "selection_grade",
    "user_selected_hint",
    "valid_link_key",
    "valid_server_text",
]
