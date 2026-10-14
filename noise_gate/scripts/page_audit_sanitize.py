"""분류 정책 분석 로그의 비식별 변환 (plans/162 §10.4 · D-330 ⑥).

`page_audit`이 만든 알람 단위 맥락을 **허용 목록** 방식으로 출력 행으로 옮긴다 — 내보낼 필드를
하나씩 정해 두고 그 밖의 필드는 전부 버린다. 원문 필드가 새로 생겨도 자동으로 새어 나가지 않는다.
허용 필드 안의 문자열 값도 **값 단위 허용 목록**을 거친다 — 열거형은 코드가 정의한 어휘만, 수치는
int/float만, 코드형 값(db_id·규칙 id 등)은 `[A-Za-z0-9_.-]{1,40}`만 통과하고 나머지는 `<other>`다.

- 가명: 실행 단위 무작위 키(`secrets.token_bytes(32)`)의 HMAC-SHA256 앞 8바이트 hex에 접두
  (`H-` 호스트 · `R-` 자원 · `F-` 지문 · `N-` 알람명 원문). 키·대응표는 어디에도 쓰지 않는다
  (D-321 전례) — 실행이 끝나면 가명을 되돌릴 수 없다.
- 이름 정규화: NFKC 뒤 주소·FQDN·경로·계정 값·식별자·식별자형 토큰·긴 숫자를 자리표시자로 바꾸고
  길이를 자른다.
- 누출 검사: 출력 전체(NFKC + casefold)에 느슨한 주소형 정규식과 이번 실행에서 본 원문 식별자
  일치를 건다(긴 값은 부분 일치 · 짧은 값은 단어 경계 완전 일치). 결과에는 걸린 파일·종류·건수만
  남기고 값은 남기지 않는다.
- 정책 스냅샷: 설정 그룹에서 bool·int·float·Literal과 지정 열거 문자열만 싣는다. 민감한 이름의
  필드는 값 없이 이름만 「제외」 목록에 둔다.

stdlib와 도메인 상수(티어·단계·어휘)·호스트 키 정규화만 쓴다(설정 객체는 덕 타이핑으로 읽는다).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sys
import typing
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from noise_gate.domain.alarm import normalize_host_key
from noise_gate.domain.notification_policy import (
    _NON_ALARM_MARKERS,
    _PERSISTENCE_UNMET,
    _VALID_IMPORTANCE,
    STAGE_ORDER,
    STAGE_UNKNOWN,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
)
from noise_gate.domain.severity_signatures import _SIGNATURE_RULES

PREFIX_HOST = "H"
PREFIX_RESOURCE = "R"
PREFIX_FP = "F"
PREFIX_NAME = "N"

# 가명 길이 — HMAC-SHA256 앞 8바이트(16 hex).
_PSEUDONYM_BYTES = 8
# 출력 안의 가명 — 누출 검사 전에 지운다(HMAC 출력이라 원문을 담지 않고, 16 hex가 짧은 식별자와
# 우연히 겹치는 것을 막는다).
_PSEUDONYM_RE = re.compile(r"(?<![0-9a-z])[hrfn]-[0-9a-f]{16}(?![0-9a-z])")

# 식별자 종류(누출 검사 결과의 `kind` 어휘 — 값이 아니라 종류만 남긴다).
IDENT_HOST = "host"
IDENT_IP = "ip"
IDENT_RESOURCE = "resource"
IDENT_ALARM_ID = "alarm_id"

# 부분 일치 검사의 최소 길이 — 자원명은 4자 이상(계획서 §10.4). 이보다 짧은 값은 일반 문자열과
# 부분 일치로 겹치므로 **단어 경계 완전 일치**로만 검사한다(건너뛰지 않는다).
_MIN_IDENT_LEN: dict[str, int] = {
    IDENT_HOST: 3,
    IDENT_IP: 1,
    IDENT_RESOURCE: 4,
    IDENT_ALARM_ID: 4,
}
_MAX_IDENT_LEN = 256

ALARM_NAME_MAX = 60
CONDITIONS_MAX = 80
RESOURCE_TYPE_MAX = 40
_EVIDENCE_LIST_MAX = 10


# 서식 문자(유니코드 Cf — ZWSP·ZWJ·BOM·방향 표지 등) — 눈에 안 보여 식별자를 쪼개는 데 쓰인다.
_FORMAT_CHARS: dict[int, None] = {
    c: None for c in range(sys.maxunicode + 1) if unicodedata.category(chr(c)) == "Cf"
}


def strip_format(text: str) -> str:
    """NFKC 뒤 서식 문자(Cf)를 지운다 — 정규화·누출 검사 공통 첫 단계."""
    return unicodedata.normalize("NFKC", text).translate(_FORMAT_CHARS)


def fold(text: str) -> str:
    """누출 검사용 정규형 — NFKC(전각·호환 문자 정리) · 서식 문자 제거 뒤 casefold."""
    return strip_format(text).casefold()


# 식별자 구분자 — `-`·`_`·`.`·공백을 같은 것으로 본다
# (`fake-web-01` = `FAKE_WEB_01` = `fake.web 01`).
_IDENT_SEP_RE = re.compile(r"[-_. ]")
_IDENT_SEP = "-"
_IDENT_SEP_CLASS = r"[-_. ]"


def _sep_fold(text: str) -> str:
    """식별자 구분자를 `-` 하나로 접는다."""
    return _IDENT_SEP_RE.sub(_IDENT_SEP, text)


# ── 정규식 ───────────────────────────────────────────────────────────────────
_URL_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://\S+|\bwww\.\S+", re.IGNORECASE)
# 계정 값 — `user=`·`id=`·`계정=` 등의 값만 지운다(키 이름은 남긴다).
_ACCOUNT_RE = re.compile(
    r"(?<![A-Za-z0-9_])(user(?:name|id|_id)?|uid|id|login|account|계정|사용자)(\s*[=:]\s*)"
    r"[^\s,;)\]}]+",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_MAC_RE = re.compile(r"(?<![0-9A-Fa-f:-])[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}(?![0-9A-Fa-f:-])")
# Cisco 표기(aabb.ccdd.eeff) — 문장 끝 점은 경계로 본다.
_MAC_DOT_RE = re.compile(
    r"(?<![0-9A-Fa-f])(?<![0-9A-Fa-f]\.)[0-9A-Fa-f]{4}(?:\.[0-9A-Fa-f]{4}){2}(?!\.?[0-9A-Fa-f])"
)
# IPv4 사상 IPv6(::ffff:10.1.2.3) — IPv6보다 먼저 지운다(그렇지 않으면 IPv4 꼬리가 남는다).
_IPV6_V4_RE = re.compile(
    r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,6}(?:\d{1,3}\.){3}\d{1,3}(?!\.?\d)"
)
# 4묶음 이상(시각 HH:MM:SS와 겹치지 않게) 또는 `::` 축약형.
_IPV6_RE = re.compile(
    r"(?<![0-9A-Fa-f:])(?:"
    r"(?:[0-9A-Fa-f]{1,4}:){3,7}[0-9A-Fa-f]{1,4}"
    r"|(?:[0-9A-Fa-f]{1,4}:)+:(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4})*)?"
    r"|::(?:[0-9A-Fa-f]{1,4}:)*[0-9A-Fa-f]{1,4}"
    r")(?![0-9A-Fa-f:])"
)
# 정규화용 IPv4 — 「숫자가 이어지는 점」만 경계로 본다
# (문장 끝 점은 경계 아님 · 5묶음 이상도 통째로).
_IPV4_RE = re.compile(r"(?<!\d)(?<!\d\.)\d{1,3}(?:\.\d{1,3}){3,}(?!\.?\d)")
# 누출 검사용 느슨한 IPv4 — 숫자 3묶음 이상이 `.`·`-`·전각/한자 점으로 이어진 형태를 모두 의심한다.
_NUM_SEP = r"[.\-．。]"
_LOOSE_IPV4_RE = re.compile(
    rf"(?<!\d)(?<!\d{_NUM_SEP})\d{{1,3}}(?:{_NUM_SEP}\d{{1,3}}){{2,}}(?!\d)(?!{_NUM_SEP}\d)"
)
# 정규화용 숫자 묶음 — 첫 덩어리 길이를 묶지 않아(0192.0.2.1 · 날짜) 누출 검사 패턴보다 넓다.
_NUM_GROUPS_RE = re.compile(
    rf"(?<!\d)(?<!\d{_NUM_SEP})\d+(?:{_NUM_SEP}\d{{1,3}}){{2,}}(?!\d)(?!{_NUM_SEP}\d)"
)
# 넓은 구분자(`_`·공백·`,`·가운뎃점류)로 나눈 IPv4 — 오탐(임계 `90 80 70` · 날짜+시각
# `2026-10-13 09` 등)을 줄이려 같은 구분자로 이어진 4묶음 이상만 본다. 정규화는 첫 덩어리 길이를
# 묶지 않고 `<IP>`로, 누출 검사는 1~3자리 4묶음 이상을 의심한다.
_WIDE_SEP = r"[.\-．。_ ,·‧•∙]"
_WIDE_IPV4_RE = re.compile(
    rf"(?<!\d)(?<!\d{_WIDE_SEP})\d+({_WIDE_SEP})\d{{1,3}}(?:\1\d{{1,3}}){{2,}}"
    rf"(?!\d)(?!{_WIDE_SEP}\d)"
)
_LOOSE_WIDE_IPV4_RE = re.compile(
    rf"(?<!\d)(?<!\d{_WIDE_SEP})\d{{1,3}}({_WIDE_SEP})\d{{1,3}}(?:\1\d{{1,3}}){{2,}}"
    rf"(?!\d)(?!{_WIDE_SEP}\d)"
)
# 사내·공용 도메인 이름(FQDN) — 끝 라벨이 아래 어휘인 점 구분 이름.
_FQDN_RE = re.compile(
    r"(?<![A-Za-z0-9.-])(?:[A-Za-z0-9][A-Za-z0-9-]{0,62}\.)+"
    r"(?:localdomain|local|internal|intranet|intra|corp|lan|home|com|net|org|io|kr|biz|info)"
    r"(?![A-Za-z0-9-])",
    re.IGNORECASE,
)
# 경로 판별 — 글자만의 짝(I/O · 읽기/쓰기)과 수치 분수·CIDR 꼬리는 경로가 아니다.
_WORD_PAIR_RE = re.compile(r"[^\W\d_]{1,12}/[^\W\d_]{1,12}")
_NUM_PAIR_RE = re.compile(r"(?:[\d.]+|<IP>)/[\d.]+%?")
_TOKEN_EDGE = "()[]{}'\",;"
# 식별자형 토큰 — 글자+숫자 혼합 · 5자 이상 · `-`/`.`/`_` 포함 가능.
_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9](?![A-Za-z0-9])")
_LONG_NUMBER_RE = re.compile(r"\d{3,}")
_SPACES_RE = re.compile(r"\s+")

# 누출 검사의 주소형 정규식(이름 → 패턴). 입력은 NFKC 뒤 문자열이다.
LEAK_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "ipv4": (_LOOSE_IPV4_RE, _LOOSE_WIDE_IPV4_RE),
    "ipv6": (_IPV6_V4_RE, _IPV6_RE),
    "mac": (_MAC_RE, _MAC_DOT_RE),
    "email": (_EMAIL_RE,),
}

# ── 값 단위 허용 목록 ─────────────────────────────────────────────────────────
OTHER = "<other>"
UNKNOWN_KEY = "<unknown_key>"
_NUM = "num"
_BOOL = "bool"
_CODE = "code"
# 운영자·외부 원천이 정하는 이름(코드 열거가 없는 값) — 원문 대신 `N-` 가명으로 싣는다.
_PSEUDO_NAME = "pseudo_name"
_CODE_VALUE_RE = re.compile(r"[A-Za-z0-9_.-]{1,40}")
_KEY_NAME_RE = re.compile(r"[A-Za-z0-9_.]{1,40}")
ValueSpec = str | frozenset[str] | re.Pattern[str]

TIERS = frozenset({TIER_PAGE, TIER_TICKET, TIER_DASHBOARD, TIER_SUPPRESS})
STAGES = frozenset({*STAGE_ORDER, STAGE_UNKNOWN})
# 이력 사전분류·LLM 패턴 어휘(`noise_gate/domain/alarm.py` pattern_type·pre_classification 주석).
PATTERN_TYPES = frozenset({"첫 발생", "주기적", "급증", "산발적", ""})
NOTI_POLICIES = frozenset({"notify", "suppress"})
ACTIONABILITY = frozenset({"actionable", "noise"})
DEPENDENCY_MODES = frozenset({"multi_hop", "one_hop"})
# 매트릭스 보조 조정 사유(`notification_policy._matrix_decision`의 promote/demote 문자열).
PROMOTE_REASONS = frozenset({
    "폴스타 통보 정책(notify)", "LLM 액션가능성(피드백)", "변경 근접(원인성)",
})
DEMOTE_REASONS = frozenset({
    "폴스타 비통보 정책(suppress)", "일상 반복 패턴(is_routine)", "LLM 노이즈 판단(피드백)",
    _PERSISTENCE_UNMET,
})
SIGNATURE_LABELS = frozenset(label for _, label, _ in _SIGNATURE_RULES)
BOOST_ORIGINS = frozenset({"unknown", "llm_or_anomaly"})
_SIGNATURE_PREFIX = "signature:"


def allow_value(value: Any, spec: ValueSpec) -> Any:
    """값 하나를 허용 목록으로 거른다.

    `_NUM`은 int/float만(그 밖은 None) · `_BOOL`은 bool만 ·
    `_CODE`는 `[A-Za-z0-9_.-]{1,40}` 문자열만 ·
    집합은 그 원소만 · 정규식은 완전 일치만 통과한다. 문자열형 규칙에서 벗어난 값은 `<other>`다.
    """
    if value is None:
        return None
    if spec == _NUM:
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    if spec == _BOOL:
        return value if isinstance(value, bool) else None
    if not isinstance(value, str):
        return OTHER
    if spec == _CODE:
        return value if _CODE_VALUE_RE.fullmatch(value) else OTHER
    if isinstance(spec, re.Pattern):
        return value if spec.fullmatch(value) else OTHER
    return value if isinstance(spec, frozenset) and value in spec else OTHER


def _allow_list(value: Any, spec: ValueSpec) -> list[Any] | None:
    """목록 값 — 앞 10개를 원소 단위로 거른다(목록이 아니면 None)."""
    if not isinstance(value, (list, tuple)):
        return None
    return [allow_value(v, spec) for v in list(value)[:_EVIDENCE_LIST_MAX]]


def code_value(value: object) -> str | None:
    """db_id·존·규칙 id 같은 코드형 값(빈 값은 None)."""
    if value in (None, ""):
        return None
    allowed = allow_value(value, _CODE)
    return str(allowed) if allowed is not None else None


def boost_label(value: object) -> str | None:
    """sev3 상향 출처 라벨 — 고정 어휘 · `signature:<코드 정의 라벨>`만."""
    if value is None:
        return None
    text = str(value)
    if text.startswith(_SIGNATURE_PREFIX):
        label = text[len(_SIGNATURE_PREFIX):]
        return text if label in SIGNATURE_LABELS else _SIGNATURE_PREFIX + OTHER
    return text if text in BOOST_ORIGINS else OTHER


# ── 허용 목록 ─────────────────────────────────────────────────────────────────
# 판정 신호(동결 스키마) — 키별 값 규칙. root_resource는 가명으로 바꾼다.
_SIGNAL_SPECS: dict[str, ValueSpec] = {
    "severity": _NUM,
    "ai_severity": _NUM,
    "effective_severity": _NUM,
    "importance": frozenset(_VALID_IMPORTANCE),
    "maintenance": _BOOL,
    "parent_avail_status": _NUM,
    "pattern": PATTERN_TYPES,
    "is_routine": _BOOL,
    "noti_policy": NOTI_POLICIES,
    "flapping": _BOOL,
    "self_heal": _BOOL,
    "storm": _BOOL,
    "llm_actionability": ACTIONABILITY,
    "cascaded": _BOOL,
    "correlated": _BOOL,
}
# 결정 단계 근거 — 최상위 허용 키와 값 규칙(목록 값은 `_EVIDENCE_LIST_KEYS`).
_EVIDENCE_SPECS: dict[str, ValueSpec] = {
    "base_tier": TIERS,
    "promote": PROMOTE_REASONS,
    "demote": DEMOTE_REASONS,
    "app_impact_fatal_events": _NUM,
    "mode": DEPENDENCY_MODES,
    "root_notified": _BOOL,
    "heal_seconds": _NUM,
    "fired_severity": _NUM,
    "markers": _NON_ALARM_MARKERS,
    "capped_from": TIERS,
    "flap_percent": _NUM,
    "high": _NUM,
    "low": _NUM,
    "samples": _NUM,
    "window_count": _NUM,
    "threshold": _NUM,
    "window_seconds": _NUM,
    "age_seconds": _NUM,
    "inhibitor_severity": _NUM,
    "would_demote": _BOOL,
    "lag_seconds": _NUM,
}
_EVIDENCE_LIST_KEYS = frozenset({"promote", "demote", "markers"})
# 중첩 근거 — 하위 허용 키와 값 규칙.
# event_type_norm은 제니퍼 유형명 또는 알람명 정규화값(`apm_noise_context.event_type_norm`)이고
# rule_id는 운영자 규칙 파일 값이라 코드 열거가 없다 — 같은 실행 안 묶음만 보이게 가명으로 싣는다.
_EVIDENCE_NESTED: dict[str, dict[str, ValueSpec]] = {
    "apm_policy": {"event_type_norm": _PSEUDO_NAME},
    "cross_source": {"rule_id": _PSEUDO_NAME, "lag_seconds": _NUM, "applied": _BOOL},
}
_PERSISTENCE_SPECS: dict[str, ValueSpec] = {
    "count": _NUM, "min_count": _NUM, "window_seconds": _NUM, "met": _BOOL, "reevaluated": _BOOL,
}
# 버린 근거 키 중 이름을 그대로 보여 줄 알려진 키(제품 코드가 싣는 키) — 그 밖은 `<unknown_key>`.
_KNOWN_DROPPED_KEYS = frozenset({
    "inhibitor", "root_resource_name", "corroborated_by", "analysis",
    "app_impact_event_types", "app_impact_was_signals", "app_impact_source", "app_impact_error",
    "page_review", "original_stage", "original_reason", "collection_failed",
    "collection_failed_sev3_only", "rule_id", "matcher_summary", "expires_at", "created_by",
    "apm_policy.policy_row", "apm_policy.policy_error", "apm_policy.escalated_from",
    "cross_source.episode_id", "cross_source.mode", "cross_source.host_key",
    "cross_source.cause_alarm_id", "cross_source.action", "cross_source.enforce",
    "cross_source.missing",
})
# 감사 파일의 알려진 기타 레코드 유형(`decision_store`) — 그 밖은 `<unknown_key>`.
_KNOWN_RECORD_TYPES = frozenset({"investigation", "l3_state"})


def key_label(name: str) -> str:
    """버린 근거 키 이름 — 알려진 키만 이름으로, 나머지는 `<unknown_key>`."""
    return name if name in _KNOWN_DROPPED_KEYS and _KEY_NAME_RE.fullmatch(name) else UNKNOWN_KEY


def record_type_label(kind: object) -> str:
    """기타 레코드 유형 이름 — 알려진 유형만 이름으로, 나머지는 `<unknown_key>`."""
    name = str(kind)
    return name if name in _KNOWN_RECORD_TYPES and _KEY_NAME_RE.fullmatch(name) else UNKNOWN_KEY


# 정책 스냅샷 — 민감 이름 조각(부분 일치) · 민감 토큰(`_` 구분 토큰 일치 — 부분 일치면
# importance(port)·bypass(pass) 같은 이름이 걸린다) · 값을 싣는 열거 문자열 · 코드→라벨 맵.
_SENSITIVE_NAME_PARTS = ("url", "token", "secret", "key", "user", "path", "pwd", "cred", "dsn",
                         "email")
_SENSITIVE_NAME_TOKENS = frozenset({
    "pass", "passwd", "password", "host", "hosts", "hostname", "addr", "address", "port", "ports",
})
_SNAPSHOT_ENUM_FIELDS = frozenset({
    "cross_source_mode",
    "enrichment_min_tier",
    "investigation_trigger_min_tier",
    "agentic_enricher_fallback",
})
_IMPORTANCE_MAP_FIELD = "importance_value_map_csv"


def new_run_key() -> bytes:
    """실행 단위 가명 키 — 32바이트 `secrets` 난수. 호출자는 이 값을 어디에도 쓰지 않는다."""
    return secrets.token_bytes(32)


class Pseudonymizer:
    """실행 단위 키로 HMAC-SHA256 가명을 만든다. 같은 실행 안에서 같은 값은 같은 가명이다."""

    __slots__ = ("__key",)

    def __init__(self, key: bytes) -> None:
        """키를 받는다(속성 이름을 감춰 직렬화·repr에 실리지 않게 한다)."""
        self.__key = key

    def __repr__(self) -> str:
        """키를 드러내지 않는 표현."""
        return "Pseudonymizer(<run key>)"

    def __call__(self, prefix: str, value: str | None) -> str | None:
        """`<접두>-<16 hex>` 가명. 빈 값·None은 None."""
        if value is None:
            return None
        text = str(value)
        if not text:
            return None
        digest = hmac.new(self.__key, text.encode("utf-8"), hashlib.sha256).digest()
        return f"{prefix}-{digest[:_PSEUDONYM_BYTES].hex()}"


class IdentifierSet:
    """이번 실행에서 본 원문 식별자 — 이름 정규화의 치환 대상이자 누출 검사의 일치 대상.

    값은 NFKC + casefold로 접어 둔다. 종류별 최소 길이 이상은 부분 일치, 미만은 단어 경계 완전
    일치로 찾는다. 숫자로만 된 값(폴스타 alarmId 등)은 출력의 수치와 겹쳐 검사가 늘 닫히므로 일치
    대상에서 빼고 수만 센다(3자리 이상 숫자는 정규화가 `<N>`으로 지운다). 글자·숫자가 없는 값
    (자원명 `/` 등)도 같은 이유로 뺀다. 코드가 내는 고정 어휘와 같은 값은
    `exclude_vocabulary`로 뺀다.
    """

    def __init__(self) -> None:
        """종류별 빈 집합으로 시작한다."""
        self._long: dict[str, set[str]] = {kind: set() for kind in _MIN_IDENT_LEN}
        self._short: dict[str, set[str]] = {kind: set() for kind in _MIN_IDENT_LEN}
        self._numeric: set[tuple[str, str]] = set()
        self._symbolic: set[tuple[str, str]] = set()
        self._vocabulary: set[tuple[str, str]] = set()
        self._patterns: dict[tuple[str, ...], re.Pattern[str] | None] = {}

    def add(self, kind: str, value: object) -> None:
        """식별자 하나를 더한다(빈 값은 무시 · 구분자 `-_. `는 하나로 접는다)."""
        text = _sep_fold(fold(str(value or "")).strip()[:_MAX_IDENT_LEN])
        if not text:
            return
        if not any(c.isalnum() for c in text):
            self._symbolic.add((kind, text))
        elif text.isdigit():
            self._numeric.add((kind, text))
        elif len(text) < _MIN_IDENT_LEN[kind]:
            self._short[kind].add(text)
        else:
            self._long[kind].add(text)
        self._patterns.clear()

    def exclude_vocabulary(self, vocabulary: str) -> None:
        """코드가 내는 고정 어휘(빈 실행 산출물·라벨)에 들어 있는 값을 일치 대상에서 뺀다.

        예: 자원명 `Memory`·`CPU`는 알람 종류 라벨 `memory`·`cpu`와 구별할 수 없다 — 그대로 두면
        모든 실행이 닫힌다. 뺀 수는 `leak_check.json`에 센다.
        """
        text = _sep_fold(fold(vocabulary))
        for kind in _MIN_IDENT_LEN:
            for value in list(self._long[kind]):
                if value in text:
                    self._long[kind].discard(value)
                    self._vocabulary.add((kind, value))
            for value in list(self._short[kind]):
                if re.search(rf"(?<!\w){_ident_escape(value)}(?!\w)", text):
                    self._short[kind].discard(value)
                    self._vocabulary.add((kind, value))
        self._patterns.clear()

    @property
    def total(self) -> int:
        """일치 검사 대상 식별자 수."""
        return sum(len(v) for v in self._long.values()) + self.short

    @property
    def short(self) -> int:
        """단어 경계 완전 일치로 검사하는 짧은 식별자 수."""
        return sum(len(v) for v in self._short.values())

    @property
    def numeric(self) -> int:
        """숫자로만 돼 일치 대상에서 뺀 식별자 수."""
        return len(self._numeric)

    @property
    def symbolic(self) -> int:
        """글자·숫자가 없어 뺀 식별자 수."""
        return len(self._symbolic)

    @property
    def vocabulary(self) -> int:
        """코드 고정 어휘와 같아 뺀 식별자 수."""
        return len(self._vocabulary)

    def _regex_body(self, kinds: Iterable[str]) -> str | None:
        """긴 값(부분 일치) | 짧은 값(단어 경계) 트라이 정규식 본문. 대상이 없으면 None."""
        kinds = tuple(kinds)
        long_values = {v for kind in kinds for v in self._long[kind]}
        short_values = {v for kind in kinds for v in self._short[kind]}
        parts = []
        if long_values:
            parts.append(_trie_regex(long_values))
        if short_values:
            parts.append(rf"(?<!\w)(?:{_trie_regex(short_values)})(?!\w)")
        return "|".join(f"(?:{p})" for p in parts) if parts else None

    def pattern(self, kinds: tuple[str, ...]) -> re.Pattern[str] | None:
        """여러 종류를 한 번에 찾는 대소문자 무시 치환 패턴(트라이 — 긴 값 우선). 없으면 None."""
        if kinds not in self._patterns:
            body = self._regex_body(kinds)
            self._patterns[kinds] = re.compile(body, re.IGNORECASE) if body else None
        return self._patterns[kinds]

    def count_hits(self, text: str) -> Counter[str]:
        """출력 문자열 안의 원문 식별자 일치 건수(종류별).

        출력·식별자를 모두 NFKC + casefold로 접어 비교한다. 가명(`H-<16 hex>` 등)은 먼저 지운다.
        패턴은 하나라 출력이 커도 한 번만 훑는다.
        """
        hits: Counter[str] = Counter()
        body = self._regex_body(_MIN_IDENT_LEN)
        if body is None:
            return hits
        kind_of: dict[str, str] = {}
        for kind in _MIN_IDENT_LEN:
            for value in (*self._long[kind], *self._short[kind]):
                kind_of.setdefault(value, kind)
        haystack = _PSEUDONYM_RE.sub(" ", fold(text))
        for m in re.finditer(body, haystack):
            hits[kind_of.get(_sep_fold(m.group(0)), IDENT_HOST)] += 1
        return hits


def _ident_escape(value: str) -> str:
    """접힌 식별자 → 정규식(구분자 `-`는 `[-_. ]` 하나로 찾는다)."""
    return "".join(_IDENT_SEP_CLASS if ch == _IDENT_SEP else re.escape(ch) for ch in value)


def _trie_regex(words: Iterable[str]) -> str:
    """접힌 식별자 집합 → 접두 트라이 정규식.

    교대 분기가 글자 단위라 식별자가 많아도 한 번에 훑는다.

    구분자 `-`는 `[-_. ]`로 찾는다(`_ident_escape`와 같은 규칙).
    """
    trie: dict[str, Any] = {}
    for word in words:
        node = trie
        for ch in word:
            node = node.setdefault(ch, {})
        node[""] = {}

    def build(node: dict[str, Any]) -> str:
        branches = [_ident_escape(ch) + build(child) for ch, child in sorted(node.items()) if ch]
        if not branches:
            return ""
        body = branches[0] if len(branches) == 1 else "(?:" + "|".join(branches) + ")"
        return f"(?:{body})?" if "" in node else body

    return build(trie)


# 이번 실행에서 본 식별자 → 자리표시자(정규화 단계 — 다른 알람의 식별자가 이름에 섞인 경우).
_IDENT_PLACEHOLDERS: tuple[tuple[tuple[str, ...], str], ...] = (
    ((IDENT_IP,), "<IP>"),
    ((IDENT_HOST,), "<HOST>"),
    ((IDENT_RESOURCE,), "<RES>"),
    ((IDENT_ALARM_ID,), "<ID>"),
)
# 코드가 내는 자리표시자(누출 검사 고정 어휘에 넣는다).
PLACEHOLDERS: tuple[str, ...] = (
    "<URL>", "<USER>", "<EMAIL>", "<MAC>", "<IP>", "<HOST>", "<PATH>", "<RES>", "<ID>", "<N>",
    OTHER, UNKNOWN_KEY,
)


def normalize_text(
    text: object,
    *,
    own_hosts: Iterable[str] = (),
    own_resources: Iterable[str] = (),
    identifiers: IdentifierSet | None = None,
    max_len: int = ALARM_NAME_MAX,
) -> str:
    """자유 문자열(알람명·임계 조건·자원 유형)을 정규화한다.

    순서: NFKC · 서식 문자(Cf) 제거 → URL → 계정 값(`user=`·`id=`·`계정=`) → 이메일 →
    MAC(콜론·하이픈·Cisco 점) → IPv4 사상 IPv6 → IPv6 → IPv4(4묶음 이상) → 넓은 구분자
    IPv4(`_`·공백·`,`·`·` · 같은 구분자 4묶음 이상) → 숫자 묶음(`.`·`-`·전각 점 · 4묶음 `<IP>` ·
    3묶음 `<N>`) → FQDN → 경로 토큰 → 그 알람 자신의 호스트·자원 문자열 → 이번 실행에서 본 다른
    식별자 → 식별자형 토큰(`<ID>`) → 3자리 이상 숫자(`<N>`) · 공백 정리 · 길이 절단.
    2자리 이하 숫자(임계값 90 등)는 남는다.
    """
    out = strip_format(str(text or ""))
    if not out:
        return ""
    out = _URL_RE.sub("<URL>", out)
    out = _ACCOUNT_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}<USER>", out)
    out = _EMAIL_RE.sub("<EMAIL>", out)
    out = _MAC_RE.sub("<MAC>", out)
    out = _MAC_DOT_RE.sub("<MAC>", out)
    out = _IPV6_V4_RE.sub("<IP>", out)
    out = _IPV6_RE.sub("<IP>", out)
    out = _IPV4_RE.sub("<IP>", out)
    out = _WIDE_IPV4_RE.sub("<IP>", out)
    out = _NUM_GROUPS_RE.sub(_mask_number_groups, out)
    out = _FQDN_RE.sub("<HOST>", out)
    out = re.sub(r"\S+", _mask_path, out)
    out = _replace_literals(out, own_hosts, "<HOST>")
    out = _replace_literals(out, own_resources, "<RES>")
    if identifiers is not None:
        for kinds, placeholder in _IDENT_PLACEHOLDERS:
            pattern = identifiers.pattern(kinds)
            if pattern is not None:
                out = pattern.sub(placeholder, out)
    out = _TOKEN_RE.sub(_mask_token, out)
    out = _LONG_NUMBER_RE.sub("<N>", out)
    out = _SPACES_RE.sub(" ", out).strip()
    return out[:max_len]


def _mask_number_groups(match: re.Match[str]) -> str:
    """구분자로 이어진 숫자 묶음 — 4묶음 이상은 주소(`<IP>`), 3묶음은 수(`<N>`)."""
    groups = len(re.split(_NUM_SEP, match.group(0)))
    return "<IP>" if groups >= 4 else "<N>"


def _mask_path(match: re.Match[str]) -> str:
    """`\\`가 있거나 `/`가 있는 토큰은 경로로 본다 — 글자만의 짝(I/O)·수치 분수·CIDR은 남긴다."""
    token = match.group(0)
    if "\\" in token:
        return "<PATH>"
    if "/" not in token:
        return token
    core = token.strip(_TOKEN_EDGE)
    if _WORD_PAIR_RE.fullmatch(core) or _NUM_PAIR_RE.fullmatch(core):
        return token
    return "<PATH>"


def _replace_literals(text: str, literals: Iterable[str], placeholder: str) -> str:
    """주어진 문자열들을 대소문자 무시로 자리표시자로 바꾼다(긴 값 우선 · NFKC 기준)."""
    unique = {
        strip_format(str(v)).strip() for v in literals if str(v or "").strip()
    }
    for lit in sorted(unique, key=len, reverse=True):
        text = re.sub(re.escape(lit), placeholder, text, flags=re.IGNORECASE)
    return text


def _mask_token(match: re.Match[str]) -> str:
    """글자+숫자 혼합 5자 이상 토큰만 `<ID>`로 바꾼다."""
    token = match.group(0)
    if len(token) >= 5 and any(c.isalpha() for c in token) and any(c.isdigit() for c in token):
        return "<ID>"
    return token


def sanitize_signals(signals: object, pseudo: Pseudonymizer) -> dict[str, Any] | None:
    """판정 신호를 허용 키·값 규칙으로 옮긴다. root_resource는 `R-` 가명으로 바꾼다."""
    if not isinstance(signals, Mapping):
        return None
    out: dict[str, Any] = {
        k: allow_value(signals.get(k), spec) for k, spec in _SIGNAL_SPECS.items() if k in signals
    }
    if "root_resource" in signals:
        root = signals.get("root_resource")
        out["root_resource"] = pseudo(PREFIX_RESOURCE, str(root)) if root else None
    return out


def _evidence_value(name: str, value: Any, spec: ValueSpec) -> Any:
    """근거 값 하나 — 목록 키는 원소 단위, 그 밖은 스칼라 규칙."""
    if name in _EVIDENCE_LIST_KEYS:
        return _allow_list(value, spec)
    return allow_value(value, spec)


def sanitize_evidence(
    evidence: object, dropped: Counter[str], pseudo: Pseudonymizer
) -> dict[str, Any]:
    """결정 단계 근거를 허용 키·값 규칙으로 옮긴다.

    버린 키는 이름(알려진 키만 · 그 밖은 `<unknown_key>`)을 `dropped`에 센다. 코드 열거가 없는
    이름 값(`event_type_norm`·`rule_id`)은 `N-` 가명이다(문자열이 아니면 None).
    """
    if not isinstance(evidence, Mapping):
        return {}
    out: dict[str, Any] = {}
    for key, value in evidence.items():
        name = str(key)
        if name in _EVIDENCE_SPECS:
            out[name] = _evidence_value(name, value, _EVIDENCE_SPECS[name])
        elif name in _EVIDENCE_NESTED and isinstance(value, Mapping):
            specs = _EVIDENCE_NESTED[name]
            nested: dict[str, Any] = {}
            for sub_key, sub_value in value.items():
                sub = str(sub_key)
                if sub == "persistence" and name == "apm_policy":
                    nested[sub] = (
                        {p: allow_value(sub_value.get(p), s) for p, s in _PERSISTENCE_SPECS.items()
                         if p in sub_value}
                        if isinstance(sub_value, Mapping) else None
                    )
                elif specs.get(sub) == _PSEUDO_NAME:
                    nested[sub] = (
                        pseudo(PREFIX_NAME, sub_value) if isinstance(sub_value, str) else None
                    )
                elif sub in specs:
                    nested[sub] = allow_value(sub_value, specs[sub])
                else:
                    dropped[key_label(f"{name}.{sub}")] += 1
            out[name] = nested
        else:
            dropped[key_label(name)] += 1
    return out


def sanitize_recurrence(recurrence: object) -> dict[str, Any] | None:
    """재발 메타 → 횟수·창(초)만(수치 아닌 값은 None)."""
    if not isinstance(recurrence, Mapping):
        return None
    first, last = recurrence.get("first_seen"), recurrence.get("last_seen")
    first_n, last_n = allow_value(first, _NUM), allow_value(last, _NUM)
    span = (
        round(float(last_n) - float(first_n), 3)
        if first_n is not None and last_n is not None
        else None
    )
    return {"count": allow_value(recurrence.get("count"), _NUM), "span_s": span}


def sanitize_correlation(meta: object, pseudo: Pseudonymizer) -> dict[str, Any] | None:
    """상관 메타 → 멤버 순번·유사도(수치만)·대표 지문 가명만."""
    if not isinstance(meta, Mapping):
        return None
    rep = meta.get("representative_fp")
    return {
        "representative_fp": pseudo(PREFIX_FP, str(rep)) if rep else None,
        "member_seq": allow_value(meta.get("member_seq"), _NUM),
        "similarity": allow_value(meta.get("similarity"), _NUM),
    }


# ── 알람 단위 행 ──────────────────────────────────────────────────────────────
KST = timezone(timedelta(hours=9))

# 파생 특징 — 수치·bool만이라 그대로 옮긴다(page_audit이 계산한다).
DERIVED_KEYS: tuple[str, ...] = (
    "fp_page_seq",
    "since_prev_fp_page_s",
    "since_prev_host_page_s",
    "fanout_hosts_10m",
    "clear_after_s",
    "fp_page_days_7d",
    "within_dedup_window_repage",
)
# what-if 판정 키(값은 고정 어휘 `keep`·`demote`·`n/a`·`undecidable`·`demote:<규칙>`).
WHATIF_KEYS: tuple[str, ...] = ("R1", "R2", "R3", "R4", "R5", "R6", "cumulative")
_WHATIF_VALUE_RE = re.compile(r"(keep|demote|n/a|undecidable)(:R\d)?")
# 알람 종류 — 자원 유형·정규화 알람명의 고정 어휘 부분 일치(첫 일치).
_ALARM_KINDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cpu", ("cpu",)),
    ("memory", ("memory", "메모리", "swap")),
    ("disk", ("disk", "filesystem", "파일시스템", "디스크")),
    ("network", ("network", "네트워크", "interface")),
    ("process", ("process", "프로세스")),
    ("log", ("log", "로그")),
    ("apm", ("apm", "jennifer")),
)
_COND_LEN_BUCKETS: tuple[tuple[int, str], ...] = (
    (1, "0"), (32, "1-31"), (128, "32-127"), (512, "128-511"),
)
_ANCESTRY_SPLIT_RE = re.compile(r"[>/\\|]+")


@dataclass
class AlarmContext:
    """알람 1건의 원문 맥락(내부 전용 — 출력되지 않는다). `Sanitizer.row`가 허용 목록으로 옮긴다."""

    seq: int
    recv_ts: datetime | None = None
    decision_ts: datetime | None = None
    db_id: str = ""
    zone: str = ""
    source: str = ""
    server_name: str = ""
    hostname: str = ""
    ip_address: str = ""
    resource_name: str = ""
    resource_type: str = ""
    resource_ancestry: str = ""
    alarm_name: str = ""
    conditions: str = ""
    severity: int | None = None
    is_clear: bool | None = None
    sig_label: str | None = None
    is_message: bool | None = None
    condition_log_len: int | None = None
    disposition: str = ""
    tier: str | None = None
    stage: str | None = None
    signals: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    noise_ctx_source: str | None = None
    fingerprint: str = ""
    recurrence: dict[str, Any] | None = None
    correlation_meta: dict[str, Any] | None = None
    boost_origin: str | None = None
    in_hours_window: bool = True
    derived: dict[str, Any] = field(default_factory=dict)
    whatif: dict[str, str] | None = None

    @property
    def host_raw(self) -> str:
        """가명 원천 — 등록 서버명, 없으면 hostname."""
        return self.server_name or self.hostname


def _kst(ts: datetime | None) -> str | None:
    """초 단위 KST ISO 문자열."""
    if ts is None:
        return None
    return ts.astimezone(KST).replace(microsecond=0).isoformat()


def _cond_len_bucket(n: int | None) -> str | None:
    """조건 로그 길이 구간."""
    if n is None:
        return None
    for upper, label in _COND_LEN_BUCKETS:
        if n < upper:
            return label
    return "512+"


def _alarm_kind(resource_type: str, alarm_name_norm: str) -> str:
    """고정 어휘로 알람 종류를 고른다(없으면 other)."""
    haystack = f"{resource_type} {alarm_name_norm}".lower()
    for kind, tokens in _ALARM_KINDS:
        if any(t in haystack for t in tokens):
            return kind
    return "other"


def _ancestry_depth(ancestry: str) -> int | None:
    """자원 계보 문자열의 단계 수(원문은 버린다)."""
    if not ancestry:
        return None
    return len([p for p in _ANCESTRY_SPLIT_RE.split(ancestry) if p.strip()])


class Sanitizer:
    """허용 목록 변환기 — `AlarmContext` → 출력 행. 목록에 없는 필드는 출력에 오지 않는다."""

    def __init__(self, pseudo: Pseudonymizer, identifiers: IdentifierSet) -> None:
        """가명기·이번 실행 식별자 집합을 받는다. 버린 근거 키 이름은 `dropped_evidence_keys`에."""
        self.pseudo = pseudo
        self.identifiers = identifiers
        self.dropped_evidence_keys: Counter[str] = Counter()

    def _norm(self, ctx: AlarmContext, text: str, max_len: int) -> str:
        """그 알람 자신의 식별자를 먼저 지우고 정규화한다."""
        return normalize_text(
            text,
            own_hosts=(ctx.server_name, ctx.hostname, ctx.ip_address),
            own_resources=(ctx.resource_name,),
            identifiers=self.identifiers,
            max_len=max_len,
        )

    def row(self, ctx: AlarmContext) -> dict[str, Any]:
        """알람 단위 분석 로그 한 줄(§10.5)."""
        p = self.pseudo
        lag_ms = (
            int((ctx.decision_ts - ctx.recv_ts).total_seconds() * 1000)
            if ctx.decision_ts is not None and ctx.recv_ts is not None
            else None
        )
        alarm_name_norm = self._norm(ctx, ctx.alarm_name, ALARM_NAME_MAX)
        resource_type = self._norm(ctx, ctx.resource_type, RESOURCE_TYPE_MAX)
        whatif = None
        if ctx.whatif is not None:
            whatif = {
                k: v
                for k, v in ctx.whatif.items()
                if k in WHATIF_KEYS and isinstance(v, str) and _WHATIF_VALUE_RE.fullmatch(v)
            }
        out: dict[str, Any] = {
            "seq": ctx.seq,
            "recv_ts": _kst(ctx.recv_ts),
            "decision_ts": _kst(ctx.decision_ts),
            "lag_ms": lag_ms,
            "in_hours_window": bool(ctx.in_hours_window),
            "db_id": code_value(ctx.db_id),
            "zone": code_value(ctx.zone),
            "source": ctx.source or None,
            "host": p(PREFIX_HOST, normalize_host_key(ctx.host_raw)),
            "resource": p(PREFIX_RESOURCE, ctx.resource_name),
            "resource_type": resource_type or None,
            "ancestry_depth": _ancestry_depth(ctx.resource_ancestry),
            "alarm_name_norm": alarm_name_norm or None,
            "alarm_def": p(PREFIX_NAME, ctx.alarm_name),
            "alarm_kind": _alarm_kind(resource_type, alarm_name_norm),
            "conditions_norm": self._norm(ctx, ctx.conditions, CONDITIONS_MAX) or None,
            "severity": ctx.severity,
            "is_clear": ctx.is_clear,
            "sig_label": allow_value(ctx.sig_label, SIGNATURE_LABELS),
            "is_message": ctx.is_message,
            "cond_len_bucket": _cond_len_bucket(ctx.condition_log_len),
            "disposition": ctx.disposition,
            "tier": allow_value(ctx.tier, TIERS),
            "stage": allow_value(ctx.stage, STAGES),
            "signals": sanitize_signals(ctx.signals, p),
            "noise_ctx_source": ctx.noise_ctx_source,
            "evidence": sanitize_evidence(ctx.evidence, self.dropped_evidence_keys, p),
            "fp": p(PREFIX_FP, ctx.fingerprint),
            "recurrence": sanitize_recurrence(ctx.recurrence),
            "correlation": sanitize_correlation(ctx.correlation_meta, p),
            "boost_origin": boost_label(ctx.boost_origin),
        }
        for key in DERIVED_KEYS:
            value = ctx.derived.get(key)
            out[key] = allow_value(value, _BOOL if isinstance(value, bool) else _NUM)
        out["whatif"] = whatif
        return out


# ── 누출 검사 ─────────────────────────────────────────────────────────────────
def leak_check(files: Mapping[str, str], identifiers: IdentifierSet) -> dict[str, Any]:
    """출력 파일 전체 문자열을 검사한다 — 느슨한 주소형 정규식 + 원문 식별자 일치.

    출력은 NFKC · 서식 문자 제거로 접은 뒤 검사한다(전각 숫자·점 · ZWSP 우회 차단).
    식별자는 구분자(`-_. `)를 같은 것으로 본다. 결과에는 파일 이름·종류·건수만
    담는다(걸린 값은 담지 않는다). `passed`가 False면 호출자는 분석 파일을 하나도 쓰지 않는다.
    """
    hits: list[dict[str, Any]] = []
    for name in sorted(files):
        text = strip_format(files[name])
        for kind, patterns in LEAK_PATTERNS.items():
            n = _span_count(text, patterns)
            if n:
                hits.append({"file": name, "kind": kind, "count": n})
        for kind, n in sorted(identifiers.count_hits(text).items()):
            hits.append({"file": name, "kind": f"identifier:{kind}", "count": n})
    return {
        "passed": not hits,
        "files_checked": sorted(files),
        "identifiers_checked": identifiers.total,
        "short_identifiers_exact_match": identifiers.short,
        "numeric_identifiers_excluded": identifiers.numeric,
        "symbol_identifiers_excluded": identifiers.symbolic,
        "vocabulary_identifiers_excluded": identifiers.vocabulary,
        "hits": hits,
    }


def _span_count(text: str, patterns: Iterable[re.Pattern[str]]) -> int:
    """여러 패턴의 일치 구간을 합쳐 센다(같은 주소를 두 패턴이 잡아도 1건)."""
    spans = sorted(m.span() for pattern in patterns for m in pattern.finditer(text))
    count, end = 0, -1
    for start, stop in spans:
        if start >= end:
            count += 1
        end = max(end, stop)
    return count


def vocabulary_words() -> list[str]:
    """코드가 출력에 내는 고정 어휘(라벨·열거·자리표시자).

    `IdentifierSet.exclude_vocabulary` 재료다.
    """
    words: list[str] = [*PLACEHOLDERS, *TIERS, *STAGES, *PATTERN_TYPES, *NOTI_POLICIES,
                        *ACTIONABILITY, *DEPENDENCY_MODES, *PROMOTE_REASONS, *DEMOTE_REASONS,
                        *SIGNATURE_LABELS, *BOOST_ORIGINS, *_SIGNAL_SPECS, *_EVIDENCE_SPECS,
                        *_KNOWN_DROPPED_KEYS, *_KNOWN_RECORD_TYPES, *DERIVED_KEYS, *WHATIF_KEYS,
                        *_VALID_IMPORTANCE, "keep", "demote", "n/a", "undecidable"]
    for kind, tokens in _ALARM_KINDS:
        words += [kind, *tokens]
    words += [label for _, label in _COND_LEN_BUCKETS]
    return words


# ── 정책 스냅샷 ───────────────────────────────────────────────────────────────
def _is_literal(annotation: Any) -> bool:
    """필드 주석이 `Literal[...]`인가."""
    return typing.get_origin(annotation) is typing.Literal


def _csv_items(value: object) -> int:
    """CSV 문자열의 비지 않은 항목 수."""
    return len([p for p in str(value or "").split(",") if p.strip()])


def _importance_map(value: object) -> dict[str, str]:
    """중요도 코드→라벨 CSV를 맵으로 푼다(식별 정보 아님 — 그대로 싣는다)."""
    out: dict[str, str] = {}
    for pair in str(value or "").split(","):
        code, sep, label = pair.partition("=")
        if sep and code.strip():
            out[code_value(code.strip()) or OTHER] = allow_value(label.strip(), _VALID_IMPORTANCE)
    return out


def _sensitive_name(name: str) -> bool:
    """민감 이름 — 조각 부분 일치 또는 `_` 구분 토큰 일치."""
    lowered = name.lower()
    if any(part in lowered for part in _SENSITIVE_NAME_PARTS):
        return True
    return any(token in _SENSITIVE_NAME_TOKENS for token in lowered.split("_"))


def snapshot_group(group: object) -> tuple[dict[str, Any], list[str], list[str]]:
    """설정 그룹 하나 → (실은 값, 민감 이름이라 뺀 필드, 허용 형이 아니라 뺀 필드).

    pydantic 모델이면 클래스의 `model_fields` 순서·주석을 쓰고, 아니면 공개 속성을 읽는다.
    """
    fields = getattr(type(group), "model_fields", None)
    if isinstance(fields, Mapping):
        items = [(name, getattr(info, "annotation", None)) for name, info in fields.items()]
    else:
        items = [(name, None) for name in sorted(vars(group)) if not name.startswith("_")]
    values: dict[str, Any] = {}
    excluded: list[str] = []
    omitted: list[str] = []
    for name, annotation in items:
        value = getattr(group, name, None)
        # bool은 이름이 민감해도 값이 식별 정보를 담을 수 없어 싣는다(단계 활성 상태 판독에 쓴다).
        if _sensitive_name(name) and not isinstance(value, bool):
            excluded.append(name)
            continue
        lowered = name.lower()
        if name == _IMPORTANCE_MAP_FIELD:
            values[name] = _importance_map(value)
        elif lowered.endswith("_csv"):
            values[name] = f"설정됨: {_csv_items(value)}항목"
        elif isinstance(value, (bool, int, float)):
            values[name] = value
        elif (_is_literal(annotation) or name in _SNAPSHOT_ENUM_FIELDS) and isinstance(value, str):
            values[name] = code_value(value)
        else:
            omitted.append(name)
    return values, excluded, omitted


def policy_snapshot(config: object) -> dict[str, Any]:
    """`config.noise_gate`·`config.alarm`의 허용 목록 스냅샷.

    D-127 — 호출자는 코드 기본값이 아니라 `AppConfig()` 실제 해석값을 준다.
    """
    out: dict[str, Any] = {"groups": {}, "excluded": [], "omitted": []}
    for group_name in ("noise_gate", "alarm"):
        group = getattr(config, group_name, None)
        if group is None:
            continue
        values, excluded, omitted = snapshot_group(group)
        out["groups"][group_name] = values
        out["excluded"].extend(f"{group_name}.{n}" for n in excluded)
        out["omitted"].extend(f"{group_name}.{n}" for n in omitted)
    return out
