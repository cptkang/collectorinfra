"""폴스타↔APM 상관 진단 (plans/162 §11 W0-X) — `page_audit`의 S11~S14 · 행 `xs`.

「폴스타와 APM이 같은 서버의 같은 사건으로 묶여야 했던 건이 몇 건이고, 각각 A 설정 · B 이름 정합 ·
C 구조 어느 지점에서 끊겼는가」를 같은 실행 안에서 숫자로 가른다. `page_audit`이 맞대기 결과를
`XsItem`으로 넘기면 이 모듈이 행별 `xs`(비식별 · `page_audit_sanitize.sanitize_xs`)와 집계
S11~S14·판독 결과를 돌려준다. 새 입력원·CLI 플래그는 없다.

- **규칙 정의는 도메인 하나에서만 온다** — 사건·결합 판정은 `EpisodeTracker`·`in_window`를 부르고,
  이벤트 → 사건 알람 조립은 `replay_noise._cross_source_signal`을 그대로 부른다(추적기 대신 받아
  두기만 하는 어댑터를 넘겨 조립 결과를 얻는다). 규칙 표는 `load_rules`로 읽는다.
- **시뮬레이션 V0~V3** — 판정 레코드 알람을 판정 시각 순으로 재생한다. 시계(`now` — 사건 종료
  sweep)는 워커처럼 판정 시각이고 창은 발생 시각으로 잰다. 원인 티어는 판정 레코드 티어로
  `record_tier`한다. 모드는 shadow(운영 모드와 무관 — 「켰다면」의 결과).
  V1은 `type=recurrence` 시각마다 같은 존·키의 열린 사건 `last_seen`을 갱신하고(`episode_for`로
  얻은 사건만 만진다), V2는 규칙 `cause_before_seconds`를 dedup 창으로 넓힌 사본이다. V3 = V1 + V2.
- **`replay_noise --cross-source-mode shadow`와의 차이(시계 정의)** — 리플레이는 입력 순서대로
  발생 시각을 시계로 쓰고, 폴스타 원문 페이로드는 기록된 noise_ctx가 없어 티어를 다시 판정한다
  (수집 실패 → 통보 티어). V0은 판정 시각을 시계로 쓰고 **기록된 티어**를 원인 티어로 쓴다. 그래서
  원인이 DASHBOARD로 기록된 결합은 V0에서 `link:cause_tier`, 리플레이에서 `demote`가 된다(사건 수·
  멤버 수는 같다). 판정 시각과 발생 시각의 차이가 사건 종료 경계(`episode_idle_seconds`)를 넘나들면
  사건 수도 갈릴 수 있다.
- **한계** — 폴스타 서버 식별 모호(`ambiguous`)는 오프라인에서 알 수 없어 원문 hostname 그대로
  (strong) 둔다. 호스트 우주는 창 안에 알람을 낸 서버뿐이다(게이트웨이 인벤토리를 읽지 않는다).
  `--no-stream`이면 정합 사유·인스턴스가 없어 S12는 「모름」, S13은 실제 기록만, S14는 판정
  레코드에 기록된 호스트 키와 판정 시각으로만 센다.

출력에는 가명(`K-`·`I-`·`E-`·`S-`)·고정 어휘·수치만 나간다. 원문 식별자(인스턴스명·제니퍼
도메인명·URL·정규화 전후 호스트 키)는 `collect_identifiers`가 누출 검사 대상에 더한다.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from urllib.parse import urlsplit

import yaml  # type: ignore[import-untyped]
from noise_gate.application.server_identity import zone_labels_for
from noise_gate.domain.alarm import HOST_KEY_NONE, AlarmEvent, normalize_host_key
from noise_gate.domain.cross_source import (
    ACTION_DEMOTE,
    MISSING_CAUSE_TIER,
    MISSING_WINDOW,
    MODE_OFF,
    MODE_SHADOW,
    SOURCE_JENNIFER,
    SOURCE_POLESTAR,
    CrossSourceRule,
    EpisodeAlarm,
    EpisodeTracker,
    in_window,
)
from noise_gate.infrastructure.apm_noise_context import apm_payload
from noise_gate.infrastructure.cross_source_rules import load_rules, resolve_rules_path
from noise_gate.scripts.page_audit_sanitize import (
    APM_DB_PREFIX,
    APM_SOURCE_DEFAULT,
    APP_IMPACT_INSTANCE_UNRESOLVED,
    CROSS_SOURCE_MODES,
    IDENT_ALARM_ID,
    IDENT_APM_DOMAIN,
    IDENT_APM_SOURCE,
    IDENT_HOST,
    IDENT_INSTANCE,
    IDENT_URL,
    MATCH_CONFIDENCES,
    MATCH_REASONS,
    MISSING_LABELS,
    SIM_VARIANTS,
    TIERS,
    WAS_KINDS,
    XS_ACTIONS,
    IdentifierSet,
    Pseudonymizer,
    allow_value,
    apm_source_label,
    app_impact_code,
    code_value,
    normalize_text,
    sanitize_xs,
    strip_format,
)
from noise_gate.scripts.replay_noise import (
    DEFAULT_CROSS_SOURCE_RULES,
    DEFAULT_EPISODE_IDLE_SECONDS,
    _cross_source_signal,
    summarize_cross_source,
)

APM_SOLUTION = "apm"
# 정규화 전후 호스트 키를 누출 검사 식별자로 더하는 최소 길이 — `IDENT_HOST` 최소 길이와 같다
# (§11.4 · 그보다 짧은 값도 단어 경계 완전 일치로 검사된다).
HOST_KEY_IDENT_MIN = 3
# `application` URL의 호스트 라벨·경로 조각을 식별자로 더하는 최소 길이.
URL_PART_IDENT_MIN = 4
# 근접 불일치 「포함 관계」의 짧은 쪽 최소 길이 — 너무 짧은 키는 아무 키에나 들어 있다.
CONTAINS_MIN = 4
# 지연 구간(초 · 절댓값) — S14.
LAG_BUCKETS: tuple[tuple[float, str], ...] = (
    (60, "<1분"), (300, "<5분"), (600, "<10분"), (1800, "<30분"), (3600, "<1시간"),
    (14400, "<4시간"),
)
LAG_BUCKET_LAST = "그 이상"
TOP_KIND_PAIRS = 10
# 판독표 문턱(§11.7 — 「높다·많다·크게」를 고정 수치로 읽는다 · notes에 같이 싣는다).
B1_UNRESOLVED_RATIO = 0.3
B2_PAIR_RATIO = 0.5
C_INCREASE_RATIO = 0.5
C2_LATE_SHARE = 0.5
C3_SHARE = 0.5
S11_NOTES: tuple[str, ...] = (
    "S11~S14는 긴 창 전체 행 기준이다(짧은 창으로 자르지 않는다 — 짝 비율은 긴 창이 있어야 의미가 "
    "있다).",
    "APM 소스: 레지스트리 등록 id는 그대로, 미등록은 S- 가명, 단일 설정(dbId jennifer)은 default.",
    "앱 영향 조회 결과는 폴스타 판정 레코드 근거 기준 — instance_unresolved는 S12에 센다.",
    "행 db_id: 레지스트리 등록 db_id·단일 설정 jennifer·등록 소스 jennifer_<id>만 원문, 미등록 "
    "jennifer_<id>는 jennifer_<S- 가명>, 그 밖 미등록 값은 <other>.",
)
S13_NOTES: tuple[str, ...] = (
    "시뮬레이션은 운영 모드와 무관하게 shadow로 재생한다 — 시계는 판정 시각, 창은 발생 시각, "
    "원인 티어는 판정 레코드 티어.",
    "V1 = 반복(type=recurrence)이 사건을 살린다 · V2 = 원인 창을 dedup 창으로 넓힌다 · V3 = V1+V2.",
    "폴스타 서버 식별 모호는 오프라인에서 알 수 없어 원문 hostname 그대로 둔다(한계).",
    "replay_noise와의 차이: 리플레이는 발생 시각을 시계로 쓰고 폴스타 티어를 다시 판정한다 — "
    "원인이 "
    "DASHBOARD로 기록된 결합은 V0만 link:cause_tier다.",
    "「V0 ≠ 실제 기록」 경고는 거짓 양성일 수 있다 — V0 시계는 판정 시각이라 판정 순서가 "
    "발생 순서와 뒤바뀐 건, 창 시작 전에 워커에서 열린 사건(재생에는 없다), 워커 재기동(실제 "
    "추적기 상태 유실)이 모두 불일치로 센다.",
)
READOUT_NOTES: tuple[str, ...] = (
    f"B1: 미정합 비율 ≥{B1_UNRESOLVED_RATIO} 그리고 토큰 후보 ≥1.",
    f"B2: 정합 APM 키의 폴스타 짝 비율 <{B2_PAIR_RATIO} 그리고 근접 불일치 ≥1.",
    f"C1: 짝 비율 ≥{B2_PAIR_RATIO} 그리고 V1 결합(link+demote)이 V0보다 max(1, "
    f"{int(C_INCREASE_RATIO * 100)}%) 이상 증가.",
    f"C2: V0→V2 window 누락 감소 ≥max(1, {int(C_INCREASE_RATIO * 100)}%) 그리고 폴스타 선행 "
    f"최근접 지연이 규칙 원인 창(적재된 규칙 cause_before_seconds 최댓값)을 넘는 비율 "
    f"≥{C2_LATE_SHARE}.",
    f"C3: missing=cause_tier ≥1 그리고 V0 결합의 {int(C3_SHARE * 100)}% 이상.",
    "C4: V0 결합 ≥1 · 했을 강등 ≥1 · 실제 적용 0 · enforce 규칙 0.",
    "문턱은 계획의 「높다·많다·크게」를 고정 상수로 읽은 해석값이다(이 모듈 상단 상수).",
    "판독은 실행 전체 데이터를 한 번에 본다 — 원인 시나리오가 섞이면 비율 문턱이 희석돼 걸리지 "
    "않을 수 있다(기간·존을 좁혀 다시 돌리면 갈린다).",
)


# ── 입력 ──────────────────────────────────────────────────────────────────────
@dataclass
class XsItem:
    """`page_audit` 행 1건의 상관 진단 입력(원문 — 출력되지 않는다). 순서는 행 순서와 같다."""

    event: AlarmEvent | None            # 스트림 원문 이벤트(없으면 None)
    rec: Mapping[str, Any]              # 판정 레코드(없으면 빈 dict)
    ts: datetime                        # 판정 시각(판정 없으면 수신·재발 시각)
    decided: bool


@dataclass
class _Node:
    """행 1건의 내부 맥락."""

    pos: int
    item: XsItem
    source: str
    alarm: EpisodeAlarm | None = None
    clear: bool = False
    hk_reason: str | None = None
    apm: dict[str, Any] | None = None
    tier: str | None = None
    db_zone: str = ""                   # dbId로 푼 존(원문·호스트 키와 무관 — A2 판독)
    from_record: bool = False           # 원문 없이 기록된 호스트 키로 만든 행(신뢰도 모름)
    actual: dict[str, Any] | None = None
    app_impact: dict[str, Any] | None = None
    raw_instance: str = ""

    @property
    def host_key(self) -> str:
        """사건 호스트 키(없으면 "")."""
        return self.alarm.host_key if self.alarm is not None else ""

    @property
    def zone(self) -> str:
        """존(없으면 "")."""
        return self.alarm.zone if self.alarm is not None else ""

    @property
    def occurred(self) -> float:
        """발생 시각(epoch 초) — 원문 없으면 판정 시각."""
        return self.alarm.occurred_at if self.alarm is not None else self.item.ts.timestamp()


@dataclass
class XsResult:
    """진단 결과 — 행별 `xs`(비식별) · 집계 섹션 · 정책 스냅샷 · V0≠실제 기록 수."""

    rows: list[dict[str, Any] | None]
    sections: dict[str, Any]
    snapshot: dict[str, Any]
    v0_actual_mismatch: int = 0


class _Capture:
    """`_cross_source_signal`에 추적기 대신 넘겨 조립된 사건 알람만 받아 두는 어댑터."""

    def __init__(self) -> None:
        self.alarm: EpisodeAlarm | None = None
        self.clear = False

    def observe(self, alarm: EpisodeAlarm, now: float) -> None:
        """비해소 알람 — 받아 두기만 한다."""
        self.alarm, self.clear = alarm, False

    def resolve(self, alarm: EpisodeAlarm, now: float) -> None:
        """해소 알람 — 받아 두기만 한다."""
        self.alarm, self.clear = alarm, True


def episode_input(event: AlarmEvent) -> tuple[EpisodeAlarm, bool]:
    """이벤트 → (사건 알람, 해소 여부) — `replay_noise._cross_source_signal`과 같은 조립.

    원 이벤트는 바꾸지 않는다(`server_identity`를 채우는 쪽은 사본이다).
    """
    cap = _Capture()
    _cross_source_signal(cast(EpisodeTracker, cap), dataclasses.replace(event))
    assert cap.alarm is not None  # noqa: S101 — 조립 함수는 observe·resolve 중 하나를 반드시 부른다
    return cap.alarm, cap.clear


def _evidence(rec: Mapping[str, Any]) -> dict[str, Any]:
    ev = rec.get("stage_evidence")
    return ev if isinstance(ev, dict) else {}


def _recorded_cs(rec: Mapping[str, Any]) -> dict[str, Any] | None:
    cs = _evidence(rec).get("cross_source")
    return cs if isinstance(cs, dict) else None


def _episode_id(rec: Mapping[str, Any]) -> str | None:
    """사건 id — 최상위 `episode_id`(계획 §11.3) 또는 `stage_evidence.episode_id`(실 레코드)."""
    for value in (rec.get("episode_id"), _evidence(rec).get("episode_id")):
        if isinstance(value, str) and value:
            return value
    return None


def _severity(rec: Mapping[str, Any]) -> int:
    sig = rec.get("signals")
    value = sig.get("severity") if isinstance(sig, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _fallback_event(rec: Mapping[str, Any], host_key: str, ts: datetime) -> AlarmEvent:
    """원문 없는 판정 — 기록된 호스트 키·판정 시각으로 최소 이벤트를 만든다(S14 시각 관계 전용)."""
    sev = _severity(rec)
    return AlarmEvent(
        db_id=str(rec.get("db_id") or ""), server_name=str(rec.get("server_name") or ""),
        hostname=host_key, ip_address="", resource_ancestry="",
        alarm_id=str(rec.get("alarm_id") or ""), severity=sev, alarm_status="",
        resource_type="", resource_name=str(rec.get("resource_name") or ""),
        alarm_name=str(rec.get("alarm_name") or ""), alarm_time=ts, conditions="",
        condition_log="", is_clear=(sev == 0),
    )


def _source_id(db_id: str, apm: Mapping[str, Any]) -> str:
    sid = apm.get("source_id")
    if isinstance(sid, str) and sid:
        return sid
    return db_id[len(APM_DB_PREFIX):] if db_id.startswith(APM_DB_PREFIX) else ""


def _node(pos: int, item: XsItem, zones: dict[str, str]) -> _Node:
    rec = item.rec
    db_id = str(rec.get("db_id") or (item.event.db_id if item.event is not None else ""))
    # 원문 없는 행의 소스는 dbId로만 판별한다. 같은 판별을 하는 `server_identity.is_apm_source`는
    # 이벤트(원문 `source` 포함)를 받는 응용 계층 함수이고 db_id만 받는 도메인 함수는 없다 — 원문이
    # 있는 행은 아래에서 조립 결과(`EpisodeAlarm.source`)로 덮어쓴다.
    source = SOURCE_JENNIFER if (db_id == SOURCE_JENNIFER or db_id.startswith(APM_DB_PREFIX)) \
        else SOURCE_POLESTAR
    node = _Node(pos=pos, item=item, source=source,
                 tier=str(rec.get("tier") or "") or None if item.decided else None)
    if db_id not in zones:
        # 레지스트리 존(`_cross_source_signal`과 같은 함수) — 실행 안에서만 캐시한다.
        zones[db_id] = zone_labels_for(db_id)[0] if db_id else ""
    node.db_zone = zones[db_id]
    cs = _recorded_cs(rec)
    if item.event is not None:
        node.alarm, node.clear = episode_input(item.event)
        node.source = node.alarm.source
        if not node.alarm.host_key:
            node.hk_reason = "unresolved" if node.source == SOURCE_JENNIFER else "no_hostname"
    else:
        recorded = cs.get("host_key") if cs is not None else None
        if isinstance(recorded, str) and recorded:
            node.alarm, node.clear = episode_input(_fallback_event(rec, recorded, item.ts))
            node.from_record = True
        else:
            node.hk_reason = "no_raw"
    if node.source == SOURCE_JENNIFER:
        apm = apm_payload(item.event) if item.event is not None else {}
        inst = apm.get("instance_name")
        node.raw_instance = inst if isinstance(inst, str) else ""
        node.apm = {
            "source_id": _source_id(db_id, apm), "db_id": db_id,
            "match_reason": apm.get("match_reason"), "match_conf": apm.get("match_confidence"),
            "instance_name": node.raw_instance or None,
            "was_kinds": list(node.alarm.was_kinds) if node.alarm is not None else [],
        }
    episode = _episode_id(rec)
    if cs is not None or episode is not None:
        node.actual = {**(cs or {}), "episode_id": episode}
    ev = _evidence(rec)
    if any(k in ev for k in ("app_impact_error", "app_impact_fatal_events", "app_impact_source")):
        node.app_impact = {"error": ev.get("app_impact_error"),
                           "fatal_events": ev.get("app_impact_fatal_events"),
                           "source": ev.get("app_impact_source")}
    return node


# ── 식별자 ────────────────────────────────────────────────────────────────────
def _add_url(idents: IdentifierSet, value: object) -> None:
    """`application` URL — 원문 · 호스트(`IDENT_HOST`) · 호스트 라벨 · 경로 조각(4자 이상).

    원문·호스트는 고정 어휘 제외를 받지 않는다. 라벨·경로 조각은 최상위 도메인(`test`)·일반
    낱말(`health`)일 수 있어 고정 어휘와 **단어 경계 완전 일치**할 때만 제외를 받는다.
    """
    if not isinstance(value, str) or not value.strip():
        return
    idents.add(IDENT_URL, value)
    try:
        parts = urlsplit(strip_format(value).strip())
        host = parts.hostname or ""
    except ValueError:
        return
    if host:
        idents.add(IDENT_HOST, host, pin=True)
        for label in host.split("."):
            if len(label) >= URL_PART_IDENT_MIN:
                idents.add(IDENT_HOST, label, pin=False)
    for piece in parts.path.split("/"):
        if len(piece) >= URL_PART_IDENT_MIN:
            idents.add(IDENT_URL, piece, pin=False)


def _add_source_id(idents: IdentifierSet, db_id: object, source_id: object,
                   registered: frozenset[str]) -> None:
    """미등록 APM 소스 id(dbId 접미 · 원문 `apm.source_id`) — 출력은 `S-` 가명이라 원문이 새면
    막는다.

    단일 설정(dbId `jennifer` · 소스 id `default`)은 출력 라벨이 `default` 그대로라 더하지 않는다.
    """
    text = str(db_id or "")
    if text == SOURCE_JENNIFER:
        return
    candidates = [text[len(APM_DB_PREFIX):] if text.startswith(APM_DB_PREFIX) else "",
                  source_id if isinstance(source_id, str) else ""]
    for sid in candidates:
        if sid and sid != APM_SOURCE_DEFAULT and sid not in registered:
            idents.add(IDENT_APM_SOURCE, sid)


def collect_identifiers(
    idents: IdentifierSet, payloads: Iterable[Mapping[str, Any] | None],
    records: Iterable[Mapping[str, Any]], *, apm_sources: frozenset[str] = frozenset(),
) -> None:
    """§11.4 누출 검사 식별자를 더한다.

    인스턴스명 · 제니퍼 도메인명 · `application` URL(원문 · 호스트 · 라벨 · 경로 조각) · 정규화
    전후 호스트 키(3자 이상 · 고정 어휘 제외를 받지 않는다) · 판정 근거에 기록된 호스트 키·원인
    alarm_id · 레지스트리 미등록 APM 소스 id(`apm_sources` = 등록 소스 id).
    """
    for p in payloads:
        if not p:
            continue
        for name in (p.get("hostname"), p.get("serverName")):
            key = normalize_host_key(str(name or ""))
            if len(key) >= HOST_KEY_IDENT_MIN:
                idents.add(IDENT_HOST, key, pin=True)
        apm = p.get("apm")
        apm_map: Mapping[str, Any] = apm if isinstance(apm, Mapping) else {}
        if apm_map:
            idents.add(IDENT_INSTANCE, apm_map.get("instance_name"))
            idents.add(IDENT_APM_DOMAIN, apm_map.get("domain_name"))
            _add_url(idents, apm_map.get("application"))
        _add_source_id(idents, p.get("dbId"), apm_map.get("source_id"), apm_sources)
    for rec in records:
        _add_source_id(idents, rec.get("db_id"), None, apm_sources)
        cs = _recorded_cs(rec)
        if cs is None:
            continue
        raw = str(cs.get("host_key") or "")
        for key in (raw, normalize_host_key(raw)):
            if len(key) >= HOST_KEY_IDENT_MIN:
                idents.add(IDENT_HOST, key, pin=True)
        idents.add(IDENT_ALARM_ID, cs.get("cause_alarm_id"))


# ── 설정 판독 재료 ────────────────────────────────────────────────────────────
def _registry_sources() -> tuple[list[dict[str, Any]], str | None]:
    """레지스트리 `solutions[apm].sources` → ([{id, zone}], 실패 시 예외 종류 이름)."""
    try:
        from src.routing.registry import get_registry

        specs = get_registry().sources_of(APM_SOLUTION)
    except Exception as exc:  # noqa: BLE001 — 레지스트리 실패는 진단 결과로 싣는다
        return [], code_value(type(exc).__name__)
    return [{"id": code_value(s.id), "zone": code_value(s.zone)} for s in specs], None


def registry_ids() -> tuple[frozenset[str], frozenset[str]]:
    """레지스트리 등록 (db_id, APM 소스 id) — 행 `db_id` 출력 판정용.

    실패하면 둘 다 빈 집합이다(모든 값을 미등록으로 다뤄 원문을 싣지 않는다 · 실패 사유는 S11
    `registry_error`가 따로 싣는다).
    """
    try:
        from src.routing.registry import get_registry

        registry = get_registry()
        return (frozenset(registry.db_ids()),
                frozenset(s.id for s in registry.sources_of(APM_SOLUTION)))
    except Exception:  # noqa: BLE001 — 실패는 S11 registry_error로 드러난다
        return frozenset(), frozenset()


def _side_counts(side: Any) -> dict[str, Any]:
    if not isinstance(side, Mapping):
        return {}
    names = side.get("alarm_names")
    return {
        "source": code_value(side.get("source")),
        "kinds": len(side.get("kinds") or []),
        "was_kinds": len(side.get("was_kinds") or []),
        "alarm_names": [normalize_text(n) for n in names] if isinstance(names, list) else [],
    }


def rules_table(path: str, *, loaded: bool) -> dict[str, Any]:
    """규칙 표 판독 — 적재 성공 여부(`load_rules` 결과) · 파일 해시 · 행별 id·enabled·enforce·
    action·창·선택자 수(비활성 행 포함 — 파일을 따로 읽는다 · 알람명은 정규화)."""
    target = resolve_rules_path(path)
    out: dict[str, Any] = {"loaded": loaded, "sha256": None, "rows": []}
    try:
        data = target.read_bytes()
    except OSError:
        return out
    out["sha256"] = hashlib.sha256(data).hexdigest()
    try:
        table = yaml.safe_load(data.decode("utf-8"))
    except Exception:  # noqa: BLE001 — 깨진 표는 loaded=false로 이미 드러난다
        return out
    rows = table.get("rules") if isinstance(table, Mapping) else None
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        win = row.get("window")
        window: Mapping[str, Any] = win if isinstance(win, Mapping) else {}
        out["rows"].append({
            "id": code_value(row.get("id")),
            "enabled": allow_value(row.get("enabled", True), "bool"),
            "enforce": allow_value(row.get("enforce", False), "bool"),
            "action": code_value(row.get("action")),
            "cause_before_seconds": allow_value(window.get("cause_before_seconds"), "num"),
            "cause_after_seconds": allow_value(window.get("cause_after_seconds"), "num"),
            "cause": _side_counts(row.get("cause")),
            "effect": _side_counts(row.get("effect")),
        })
    return out


_RULES_HASH_KEYS = frozenset({"sha256", "rules_sha256"})


def without_rules_hash(obj: Any) -> Any:
    """규칙 표 해시 값을 None으로 바꾼 사본 — 누출 검사 고정 어휘 재료용(해시 안에 짧은 식별자가
    우연히 들어 있으면 그 식별자가 검사에서 빠진다)."""
    if isinstance(obj, Mapping):
        return {k: None if k in _RULES_HASH_KEYS else without_rules_hash(v)
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [without_rules_hash(v) for v in obj]
    return obj


# ── 시뮬레이션 ────────────────────────────────────────────────────────────────
def _label(signal: Mapping[str, Any] | None) -> str:
    """사건 신호 → 행 라벨 `<action>[:<missing>,…]` · `member` · `no_episode`."""
    if signal is None:
        return "no_episode"
    if not signal.get("rule_id"):
        return "member"
    missing = list(signal.get("missing") or [])
    return str(signal.get("action")) + (":" + ",".join(missing) if missing else "")


def _actual_label(actual: Mapping[str, Any]) -> str:
    """실제 기록 → 같은 라벨(기록이 있으면 사건 멤버다)."""
    return _label(actual) if actual.get("rule_id") else "member"


def simulate(
    nodes: Sequence[_Node],
    rules: Sequence[CrossSourceRule],
    *,
    idle_seconds: float,
    recurrences: Sequence[tuple[float, str, str]] = (),
) -> tuple[dict[int, str], list[dict[str, Any] | None], list[_Node]]:
    """판정 레코드 알람을 판정 시각 순으로 도메인 추적기에 재생한다.

    Args:
        nodes: 판정이 있고 원문 이벤트가 있는 행.
        rules: 규칙 표(V2·V3는 창을 넓힌 사본).
        idle_seconds: 사건 종료 idle.
        recurrences: (시각, 존, 호스트 키) — 주면 그 시각에 열린 사건 `last_seen`을 갱신한다(V1).

    Returns:
        (행 위치 → 라벨, 재생 순 신호, 재생 순 행).
    """
    tracker = EpisodeTracker(rules, mode=MODE_SHADOW, idle_seconds=idle_seconds)
    steps: list[tuple[float, int, int, _Node | None, tuple[str, str]]] = []
    for n in nodes:
        steps.append((n.item.ts.timestamp(), 1, n.pos, n, ("", "")))
    for i, (t, zone, hk) in enumerate(recurrences):
        steps.append((t, 0, i, None, (zone, hk)))
    steps.sort(key=lambda s: (s[0], s[1], s[2]))
    labels: dict[int, str] = {}
    signals: list[dict[str, Any] | None] = []
    order: list[_Node] = []
    for now, _, _, node, (zone, hk) in steps:
        if node is None:
            tracker.sweep(now)
            ep = tracker.episode_for(zone, hk)
            if ep is not None:
                ep.last_seen = max(ep.last_seen, now)
            continue
        alarm = node.alarm
        assert alarm is not None  # noqa: S101 — 호출자가 원문 있는 행만 넘긴다
        if node.clear:
            tracker.resolve(alarm, now)
            labels[node.pos] = "clear"
            continue
        signal = tracker.observe(alarm, now)
        if signal is not None:
            tracker.record_tier(signal["episode_id"], alarm.alarm_id, node.tier)
        labels[node.pos] = _label(signal)
        signals.append(signal)
        order.append(node)
    return labels, signals, order


def _variant_summary(signals: list[dict[str, Any] | None], order: list[_Node]) -> dict[str, Any]:
    """변형 1종 요약 — `replay_noise.summarize_cross_source` + 두 소스 사건 · sev3 강등 후보."""
    summary = summarize_cross_source([{"cross_source": s, "stage": ""} for s in signals])
    # 시뮬레이션에는 적용(enforce)·조사 발주가 없다 — 늘 0인 키는 싣지 않는다.
    summary.pop("applied", None)
    summary.pop("episode_investigations", None)
    sources: dict[str, set[str]] = defaultdict(set)
    demote_sev3 = 0
    for s, n in zip(signals, order, strict=True):
        if s is None:
            continue
        sources[s["episode_id"]].add(n.source)
        if s.get("action") == ACTION_DEMOTE and s.get("rule_id") and (
            n.alarm is not None and n.alarm.severity >= 3
        ):
            demote_sev3 += 1
    summary["missing"] = dict(sorted(summary["missing"].items()))
    summary["by_rule"] = {code_value(k) or "": v for k, v in sorted(summary["by_rule"].items())}
    summary["two_source_episodes"] = sum(1 for v in sources.values() if len(v) >= 2)
    summary["demote_sev3"] = demote_sev3
    summary["judged"] = summary["link"] + summary["would_demote"]
    return summary


# ── 집계 ──────────────────────────────────────────────────────────────────────
def _loose(key: str) -> str:
    return re.sub(r"[\W_]+", "", strip_format(key).casefold())


def _bucket(lag: float) -> str:
    a = abs(lag)
    for upper, label in LAG_BUCKETS:
        if a < upper:
            return label
    return LAG_BUCKET_LAST


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 3) if den else None


def _counter(c: Counter[str]) -> dict[str, int]:
    return {k: v for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))}


def analyze(
    items: Sequence[XsItem],
    *,
    config: Any,
    pseudo: Pseudonymizer,
    stream_read: bool,
    recurrences: Sequence[tuple[datetime, Mapping[str, Any]]] = (),
    late_promotions: int = 0,
    window_seconds: float = 0.0,
    dedup_window_seconds: float = 0.0,
) -> XsResult:
    """행별 `xs`와 S11~S14·판독 결과를 만든다.

    Args:
        items: 행 순서의 입력.
        config: `noise_gate` 설정 그룹(덕 타이핑 — 없는 필드는 기본값).
        pseudo: 실행 단위 가명기(`page_audit`과 같은 키 — 같은 서버 = 같은 가명).
        stream_read: 스트림을 읽었는가(`--no-stream`이면 False).
        recurrences: 판정 감사 `type=recurrence` (시각, 레코드) — V1·V3 재료.
        late_promotions: `type=late_promotion` 건수.
        window_seconds: 긴 창 길이(초) — lift 기준 비율.
        dedup_window_seconds: dedup 창(초) — V2·V3 원인 창.
    """
    mode = getattr(config, "cross_source_mode", None)
    idle_cfg = getattr(config, "episode_idle_seconds", None)
    idle = float(idle_cfg) if isinstance(idle_cfg, (int, float)) else DEFAULT_EPISODE_IDLE_SECONDS
    rules_path = str(getattr(config, "cross_source_rules_path", None) or DEFAULT_CROSS_SOURCE_RULES)
    rules = load_rules(rules_path)
    table = rules_table(rules_path, loaded=rules is not None)
    registry, registry_error = _registry_sources()
    registered = frozenset(str(r["id"]) for r in registry if r.get("id"))
    rule_ids = frozenset(str(r["id"]) for r in table["rows"] if r.get("id"))
    policy = {
        "cross_source_mode": allow_value(mode, CROSS_SOURCE_MODES) if isinstance(mode, str)
        else None,
        "app_impact_enabled": allow_value(getattr(config, "app_impact_enabled", None), "bool"),
        "app_impact_window_minutes": allow_value(
            getattr(config, "app_impact_window_minutes", None), "num"),
        "app_impact_late_promotion_enabled": allow_value(
            getattr(config, "app_impact_late_promotion_enabled", None), "bool"),
        "episode_idle_seconds": allow_value(idle_cfg, "num"),
        "episode_idle_seconds_used": idle,
    }
    snapshot = {**policy, "rules": table, "apm_sources": registry,
                "registry_error": registry_error}

    zones: dict[str, str] = {}
    nodes = [_node(i, it, zones) for i, it in enumerate(items)]
    keyed = [n for n in nodes if n.host_key]
    keys: dict[str, set[str]] = {SOURCE_POLESTAR: set(), SOURCE_JENNIFER: set()}
    for n in keyed:
        keys[n.source].add(n.host_key)
    loose: dict[str, dict[str, str]] = {s: {k: _loose(k) for k in v} for s, v in keys.items()}
    loose_sets = {s: set(v.values()) - {""} for s, v in loose.items()}
    peer_cache: dict[tuple[str, str], tuple[bool, bool, bool]] = {}

    def peers(n: _Node) -> tuple[bool, bool, bool]:
        other = SOURCE_JENNIFER if n.source == SOURCE_POLESTAR else SOURCE_POLESTAR
        ck = (n.source, n.host_key)
        if ck not in peer_cache:
            exact = n.host_key in keys[other]
            lk = loose[n.source][n.host_key]
            lp = not exact and bool(lk) and lk in loose_sets[other]
            cp = not exact and not lp and len(lk) >= CONTAINS_MIN and any(
                len(o) >= CONTAINS_MIN and (lk in o or o in lk) for o in loose_sets[other]
            )
            peer_cache[ck] = (exact, lp, cp)
        return peer_cache[ck]

    decided = [n for n in nodes if n.item.decided and n.alarm is not None]
    by_host: dict[tuple[str, str], list[_Node]] = defaultdict(list)
    for n in sorted(decided, key=lambda x: (x.occurred, x.pos)):
        if n.source == SOURCE_POLESTAR and n.host_key:
            by_host[(n.zone, n.host_key)].append(n)

    def _fp(n: _Node) -> str:
        return n.alarm.fingerprint if n.alarm is not None else ""

    def preceding(n: _Node) -> tuple[_Node, bool] | None:
        """같은 존·키의 직전 폴스타 알람(규칙 무관)과 그 시점 미해소 여부."""
        cands = by_host.get((n.zone, n.host_key), [])
        cause = None
        for c in cands:
            if c.clear or c.occurred > n.occurred:
                continue
            cause = c
        if cause is None:
            return None
        resolved = any(
            c.clear and _fp(c) == _fp(cause) and cause.occurred <= c.occurred <= n.occurred
            for c in cands
        )
        return cause, not resolved

    def nearest(n: _Node) -> _Node | None:
        cands = [c for c in by_host.get((n.zone, n.host_key), []) if not c.clear]
        return min(cands, key=lambda c: (abs(n.occurred - c.occurred), c.pos)) if cands else None

    # ── 시뮬레이션 V0~V3 (스트림 · 규칙 적재 성공일 때만)
    sim_nodes = [n for n in decided if n.item.event is not None]
    variants: dict[str, dict[str, Any]] = {}
    labels: dict[str, dict[int, str]] = {}
    sim_reason = None if stream_read and rules is not None else (
        "no_stream" if not stream_read else "rules_load_failed")
    if sim_reason is None and rules is not None:
        hk_of: dict[str, tuple[str, str]] = {}
        for n in nodes:
            if n.alarm is None or not n.host_key:
                continue
            fp = str(n.item.rec.get("fingerprint") or "")
            for k in (fp, n.alarm.fingerprint, n.alarm.alarm_id):
                if k:
                    hk_of.setdefault(k, (n.zone, n.host_key))
        recs: list[tuple[float, str, str]] = []
        for ts, rec in recurrences:
            for k in (str(rec.get("fingerprint") or ""), str(rec.get("alarm_id") or "")):
                if k and k in hk_of:
                    recs.append((ts.timestamp(), *hk_of[k]))
                    break
        dedup = int(dedup_window_seconds)
        wide = [dataclasses.replace(r, cause_before_seconds=max(r.cause_before_seconds, dedup))
                for r in rules]
        for name, rs, rec_in in (("V0", rules, ()), ("V1", rules, recs), ("V2", wide, ()),
                                 ("V3", wide, recs)):
            lab, sig, order = simulate(sim_nodes, rs, idle_seconds=idle, recurrences=rec_in)
            labels[name] = lab
            variants[name] = _variant_summary(sig, order)

    # ── 행별 xs
    v0_mismatch = 0
    xs_rows: list[dict[str, Any] | None] = []
    for n in nodes:
        raw: dict[str, Any] = {"host_key": n.host_key or None, "hk_reason": n.hk_reason,
                               "zone": n.zone, "apm": n.apm, "app_impact": n.app_impact}
        if n.host_key and n.alarm is not None:
            # 원문 없이 기록된 키로 만든 행은 신뢰도를 모른다(조립 결과는 가짜 이벤트의 값이다).
            raw["hk_strength"] = None if n.from_record else n.alarm.host_key_strength
            raw["peer_7d"], raw["loose_peer"], raw["contains_peer"] = peers(n)
            if n.source == SOURCE_JENNIFER and not n.clear:
                pre = preceding(n) if n.item.decided else None
                if pre is not None:
                    cause, is_open = pre
                    raw["nearest_cause"] = {
                        "lag_s": round(n.occurred - cause.occurred),
                        "kind": (cause.alarm.kind if cause.alarm is not None else "") or "none",
                        "tier": cause.tier, "open": is_open,
                    }
            raw["actual"] = n.actual
            sim = {v: labels[v][n.pos] for v in SIM_VARIANTS if n.pos in labels.get(v, {})}
            raw["sim"] = sim or None
        if labels and n.actual is not None and n.pos in labels["V0"]:
            if _actual_label(n.actual) != labels["V0"][n.pos]:
                v0_mismatch += 1
        xs_rows.append(sanitize_xs(raw, pseudo, registered=registered, rule_ids=rule_ids))

    # ── S11 설정 판독
    apm_nodes = [n for n in nodes if n.source == SOURCE_JENNIFER]
    src_count: Counter[tuple[str, bool]] = Counter()
    zone_empty = 0
    for n in apm_nodes:
        apm = n.apm or {}
        label, reg = apm_source_label(str(apm.get("source_id") or ""), str(apm.get("db_id") or ""),
                                      registered, pseudo)
        src_count[(str(label), reg)] += 1
        if not n.db_zone:
            zone_empty += 1
    app_dist: Counter[str] = Counter()
    unresolved_keys: set[str] = set()
    unresolved_alarms_pol = 0
    for n in nodes:
        if n.source != SOURCE_POLESTAR or not n.item.decided:
            continue
        app = n.app_impact
        code = app_impact_code(app.get("error")) if app else None
        if app is None:
            app_dist["not_queried"] += 1
        elif code == APP_IMPACT_INSTANCE_UNRESOLVED:
            unresolved_alarms_pol += 1
            if n.host_key:
                unresolved_keys.add(n.host_key)
        elif code is not None:
            app_dist[f"error:{code}"] += 1
        elif isinstance(app.get("fatal_events"), int) and app["fatal_events"] > 0:
            app_dist["fatal"] += 1
        else:
            app_dist["fatal_zero"] += 1
    s11 = {
        **policy,
        "rules_loaded": table["loaded"],
        "rules_sha256": table["sha256"],
        "rules": table["rows"],
        "registry_sources": registry,
        "registry_error": registry_error,
        "apm_alarms": len(apm_nodes),
        "apm_sources": [{"source": s, "registered": r, "alarms": c}
                        for (s, r), c in sorted(src_count.items())],
        "unregistered_apm_alarms": sum(c for (_, r), c in src_count.items() if not r),
        "zone_empty_apm_alarms": zone_empty,
        "app_impact": _counter(app_dist),
        "late_promotions": late_promotions,
        "apm_received_zero": not apm_nodes,
        "notes": list(S11_NOTES),
    }

    # ── S12 이름 정합
    if stream_read:
        apm_raw = [n for n in apm_nodes if n.item.event is not None]
        reasons = Counter(str(allow_value((n.apm or {}).get("match_reason") or None,
                                          MATCH_REASONS)) for n in apm_raw)
        confs = Counter(str(allow_value((n.apm or {}).get("match_conf") or None,
                                        MATCH_CONFIDENCES)) for n in apm_raw)
        strengths = Counter(n.alarm.host_key_strength if n.alarm is not None else HOST_KEY_NONE
                            for n in apm_raw)
        inst_reasons: dict[str, set[str]] = defaultdict(set)
        unresolved_inst: dict[str, int] = Counter()
        for n in apm_raw:
            inst = n.raw_instance
            if not inst:
                continue
            inst_reasons[str(allow_value((n.apm or {}).get("match_reason") or None,
                                         MATCH_REASONS))].add(inst)
            if not n.host_key:
                unresolved_inst[inst] += 1
        pol_keys = {k for k in keys[SOURCE_POLESTAR] if len(k) >= 3}
        token_hits = 0
        for inst in unresolved_inst:
            folded = strip_format(inst).casefold()
            if any(re.search(rf"(?<![0-9a-z]){re.escape(k)}(?![0-9a-z])", folded)
                   for k in pol_keys):
                token_hits += 1
        apm_keys = {n.host_key for n in apm_raw if n.host_key}
        paired = apm_keys & keys[SOURCE_POLESTAR]
        loose_only = contains_only = 0
        for k in apm_keys - paired:
            _, lp, cp = peers(next(n for n in apm_raw if n.host_key == k))
            loose_only += lp
            contains_only += cp
        unresolved_alarms = sum(1 for n in apm_raw if not n.host_key)
        unresolved_inst_alarms = sum(unresolved_inst.values())
        s12: dict[str, Any] = {
            "known": True,
            "apm_alarms": len(apm_raw),
            "match_reason_alarms": _counter(reasons),
            "match_reason_instances": {k: len(v) for k, v in sorted(inst_reasons.items())},
            "match_conf": _counter(confs),
            "hk_strength": _counter(strengths),
            "unresolved_instances": len(unresolved_inst),
            "unresolved_inst_alarms": unresolved_inst_alarms,
            "unresolved_alarms": unresolved_alarms,
            "unresolved_ratio": _ratio(unresolved_alarms, len(apm_raw)),
            "apm_keys": len(apm_keys),
            "polestar_keys": len(keys[SOURCE_POLESTAR]),
            "apm_keys_with_polestar_ratio": _ratio(len(paired), len(apm_keys)),
            "polestar_keys_with_apm_ratio": _ratio(len(paired), len(keys[SOURCE_POLESTAR])),
            "near_mismatch_loose": loose_only,
            "near_mismatch_contains": contains_only,
            "token_candidate_instances": token_hits,
            "instance_unresolved_polestar_keys": len(unresolved_keys),
            "instance_unresolved_polestar_alarms": unresolved_alarms_pol,
        }
    else:
        s12 = {"known": False, "note": "모름 — 스트림 미독(--no-stream): 정합 사유·인스턴스는 "
                                       "원문에만 있다",
               "instance_unresolved_polestar_keys": len(unresolved_keys),
               "instance_unresolved_polestar_alarms": unresolved_alarms_pol}

    # ── S13 사건·결합
    actual_nodes = [n for n in nodes if n.actual is not None and n.item.decided]
    act_eps: dict[str, set[str]] = defaultdict(set)
    act_action: Counter[str] = Counter()
    act_missing: Counter[str] = Counter()
    act_modes: Counter[str] = Counter()
    applied = 0
    for n in actual_nodes:
        a = n.actual or {}
        if a.get("episode_id"):
            act_eps[str(a["episode_id"])].add(n.source)
        if a.get("mode"):
            act_modes[str(allow_value(a.get("mode"), CROSS_SOURCE_MODES))] += 1
        if a.get("rule_id"):
            act_action[str(allow_value(a.get("action"), XS_ACTIONS))] += 1
            act_missing.update(str(allow_value(m, MISSING_LABELS)) for m in a.get("missing") or [])
        if a.get("applied") is True:
            applied += 1
    s13 = {
        "actual": {
            "known": bool(actual_nodes),
            "modes": _counter(act_modes),
            "episodes": len(act_eps),
            "two_source_episodes": sum(1 for v in act_eps.values() if len(v) >= 2),
            "by_action": _counter(act_action),
            "missing": _counter(act_missing),
            "applied": applied,
        },
        "sim": {"known": sim_reason is None, "reason": sim_reason, "variants": variants,
                "idle_seconds": idle, "dedup_window_seconds": int(dedup_window_seconds),
                "v0_actual_mismatch": v0_mismatch if labels else None},
        "notes": list(S13_NOTES),
    }

    # ── S14 시간 관계
    first: Counter[str] = Counter()
    later: Counter[str] = Counter()
    pair_lags: list[float] = []
    kind_pairs: Counter[str] = Counter()
    cause_tiers: Counter[str] = Counter()
    cause_open = cause_total = 0
    for n in decided:
        if n.source != SOURCE_JENNIFER or n.clear or not n.host_key:
            continue
        near = nearest(n)
        if near is not None:
            lag = n.occurred - near.occurred
            pair_lags.append(lag)
            (first if lag >= 0 else later)[_bucket(lag)] += 1
        pre = preceding(n)
        if pre is not None:
            cause, is_open = pre
            cause_total += 1
            cause_open += is_open
            cause_tiers[str(allow_value(cause.tier, TIERS))] += 1
            kind = (cause.alarm.kind if cause.alarm is not None else "") or "none"
            for wk in (n.alarm.was_kinds if n.alarm is not None else ()) or ("none",):
                wk_label = wk if wk == "none" else str(allow_value(wk, WAS_KINDS))
                kind_pairs[f"{kind}×{wk_label}"] += 1
    pol_first = [lag for lag in pair_lags if lag >= 0]
    # 「늦은 폴스타 선행」 문턱 = 적재된(enabled) 규칙 원인 창의 최댓값 — 규칙 표가 단일 출처다.
    # 이 값을 넘는 지연은 어느 규칙 창에도 들지 않는다(창 경계는 포함 — `in_window`).
    late_lag = max((r.cause_before_seconds for r in rules or ()), default=None)
    lifts: dict[str, Any] = {}
    for rule in rules or ():
        cause_side = [n for n in decided if not n.clear and n.alarm is not None
                      and n.host_key and rule.cause.matches(n.alarm)]
        effect_side = [n for n in decided if not n.clear and n.alarm is not None
                       and n.host_key and rule.effect.matches(n.alarm)]
        hosts = {(n.zone, n.host_key) for n in decided
                 if n.source == rule.cause.source and n.host_key}
        hits = sum(1 for c in cause_side if any(
            e.zone == c.zone and e.host_key == c.host_key and c.alarm is not None
            and e.alarm is not None and in_window(rule, c.alarm, e.alarm)
            for e in effect_side))
        span = rule.cause_before_seconds + rule.cause_after_seconds
        base = (min(1.0, len(effect_side) * span / (window_seconds * len(hosts)))
                if window_seconds > 0 and hosts else 0.0)
        p_after = hits / len(cause_side) if cause_side else None
        lifts[str(code_value(rule.id))] = {
            "causes": len(cause_side), "effects": len(effect_side),
            "p_after": round(p_after, 3) if p_after is not None else None,
            "base": round(base, 4),
            "lift": round(p_after / base, 2) if p_after is not None and base > 0 else None,
        }
    s14 = {
        "basis": "occurred" if stream_read else "decision_ts",
        "pairs": len(pair_lags),
        "polestar_first": {label: first.get(label, 0)
                           for label in [*(b for _, b in LAG_BUCKETS), LAG_BUCKET_LAST]},
        "apm_first": {label: later.get(label, 0)
                      for label in [*(b for _, b in LAG_BUCKETS), LAG_BUCKET_LAST]},
        "late_lag_s": late_lag,
        "polestar_first_beyond_window_ratio": _ratio(
            sum(1 for lag in pol_first if lag > late_lag), len(pol_first))
        if late_lag is not None else None,
        "kind_pairs_top": dict(list(_counter(kind_pairs).items())[:TOP_KIND_PAIRS]),
        "cause_open_ratio": _ratio(cause_open, cause_total),
        "cause_tiers": _counter(cause_tiers),
        "lift": lifts,
    }
    if not stream_read:
        s14["note"] = "판정 레코드에 기록된 호스트 키·판정 시각으로만 센다(WAS kind 없음)"

    sections = {"S11": s11, "S12": s12, "S13": s13, "S14": s14}
    sections["xs_readout"] = readout(sections, rules)
    return XsResult(rows=xs_rows, sections=sections, snapshot=snapshot,
                    v0_actual_mismatch=v0_mismatch)


# ── 판독표 (§11.7) ────────────────────────────────────────────────────────────
READOUT_ACTIONS: dict[str, str] = {
    "A1": "운영 .env 모드·규칙 표 경로 정정(결정 불필요)",
    "A2": "게이트웨이 JENNIFER_SOURCES id 또는 레지스트리 sources 정정(결정 불필요)",
    "B1": "게이트웨이 정합 파일 정규식·overrides 보강(결정 불필요)",
    "B2": "호스트 키 정규화 보강 — G-7",
    "C1": "dedup 반복이 사건을 살리게 — G-8",
    "C2": "원인 미해소 기준 또는 창 보정 — G-9(plans/144 W6)",
    "C3": "원인 DASHBOARD 강등 조건 재검토 — G-9에 포함",
    "C4": "규칙 행 enforce 전환 — plans/144 G-7 기준",
}


def _grew(before: int, after: int) -> bool:
    return after - before >= max(1, C_INCREASE_RATIO * before)


def readout(sections: Mapping[str, Any], rules: Sequence[CrossSourceRule] | None) -> dict[str, Any]:
    """§11.7 판독표 — 걸린 원인 코드 목록과 근거 수치(문턱은 notes)."""
    s11, s12, s13, s14 = (sections[k] for k in ("S11", "S12", "S13", "S14"))
    hits: list[dict[str, Any]] = []

    def add(code: str, **evidence: Any) -> None:
        hits.append({"cause": code, "evidence": evidence, "action": READOUT_ACTIONS[code]})

    if s11["cross_source_mode"] == MODE_OFF or not s11["rules_loaded"]:
        add("A1", mode=s11["cross_source_mode"], rules_loaded=s11["rules_loaded"])
    if s11["unregistered_apm_alarms"] >= 1 and s11["zone_empty_apm_alarms"] > 0:
        add("A2", unregistered=s11["unregistered_apm_alarms"],
            zone_empty=s11["zone_empty_apm_alarms"])
    if s12.get("known"):
        ratio = s12["unresolved_ratio"]
        if ratio is not None and ratio >= B1_UNRESOLVED_RATIO and \
                s12["token_candidate_instances"] >= 1:
            add("B1", unresolved_ratio=ratio, token_candidates=s12["token_candidate_instances"])
        pair = s12["apm_keys_with_polestar_ratio"]
        near = s12["near_mismatch_loose"] + s12["near_mismatch_contains"]
        if pair is not None and pair < B2_PAIR_RATIO and near >= 1:
            add("B2", pair_ratio=pair, near_mismatch=near)
    sim = s13["sim"]
    v = sim["variants"]
    if sim["known"]:
        pair = s12.get("apm_keys_with_polestar_ratio") if s12.get("known") else None
        if pair is not None and pair >= B2_PAIR_RATIO and _grew(v["V0"]["judged"],
                                                              v["V1"]["judged"]):
            add("C1", pair_ratio=pair, v0=v["V0"]["judged"], v1=v["V1"]["judged"])
        w0 = v["V0"]["missing"].get(MISSING_WINDOW, 0)
        w2 = v["V2"]["missing"].get(MISSING_WINDOW, 0)
        late = s14["polestar_first_beyond_window_ratio"]
        if w0 - w2 >= max(1, C_INCREASE_RATIO * w0) and late is not None and \
                late >= C2_LATE_SHARE:
            add("C2", v0_window=w0, v2_window=w2, late_ratio=late)
    if sim["known"]:
        judged = v["V0"]["judged"]
        tier_missing = v["V0"]["missing"].get(MISSING_CAUSE_TIER, 0)
        source = "V0"
    else:
        judged = sum(s13["actual"]["by_action"].values())
        tier_missing = s13["actual"]["missing"].get(MISSING_CAUSE_TIER, 0)
        source = "actual"
    if tier_missing >= 1 and tier_missing >= C3_SHARE * judged:
        add("C3", cause_tier=tier_missing, judged=judged, basis=source)
    if sim["known"]:
        enforced = sum(1 for r in rules or () if r.enforce)
        if v["V0"]["judged"] >= 1 and v["V0"]["would_demote"] >= 1 and \
                s13["actual"]["applied"] == 0 and enforced == 0:
            add("C4", judged=v["V0"]["judged"], would_demote=v["V0"]["would_demote"],
                applied=s13["actual"]["applied"])
    return {"causes": [h["cause"] for h in hits], "rows": hits, "notes": list(READOUT_NOTES)}


# ── summary.md ────────────────────────────────────────────────────────────────
def _table(headers: list[str], rows: Iterable[Iterable[Any]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for row in rows:
        out.append("| " + " | ".join("-" if v is None else str(v) for v in row) + " |")
    return out


def _pairs(title: str, mapping: Mapping[str, Any]) -> list[str]:
    if not mapping:
        return [f"- {title}: 없음", ""]
    return [f"**{title}**", "", *_table(["항목", "값"], mapping.items()), ""]


def render_md(summary: Mapping[str, Any]) -> list[str]:
    """S11~S14 · 판독 결과 마크다운 줄."""
    s11, s12, s13, s14 = (summary[k] for k in ("S11", "S12", "S13", "S14"))
    lines = ["## S11 설정 판독 (폴스타↔APM)", ""]
    lines += _table(["항목", "값"], [
        ("사건 상관 모드", s11["cross_source_mode"]),
        ("앱 영향 조회", s11["app_impact_enabled"]),
        ("앱 영향 창(분)", s11["app_impact_window_minutes"]),
        ("사후 승격", s11["app_impact_late_promotion_enabled"]),
        ("사건 idle(초)", s11["episode_idle_seconds"]),
        ("규칙 표 적재", s11["rules_loaded"]),
        ("규칙 표 해시", s11["rules_sha256"]),
        ("레지스트리 오류", s11["registry_error"]),
        ("APM 수신", s11["apm_alarms"]),
        ("미등록·단일 설정 소스 알람", s11["unregistered_apm_alarms"]),
        ("존 빈 값 APM 알람", s11["zone_empty_apm_alarms"]),
        ("사후 승격 건수", s11["late_promotions"]),
    ]) + [""]
    if s11["apm_received_zero"]:
        lines += ["- APM 수신 0건", ""]
    lines += _table(["규칙", "enabled", "enforce", "action", "원인 앞(초)", "원인 뒤(초)"], [
        (r["id"], r["enabled"], r["enforce"], r["action"], r["cause_before_seconds"],
         r["cause_after_seconds"]) for r in s11["rules"]
    ]) + [""]
    lines += _table(["APM 소스", "레지스트리 등록", "알람"], [
        (r["source"], r["registered"], r["alarms"]) for r in s11["apm_sources"]
    ]) + [""]
    lines += _pairs("앱 영향 조회 결과(폴스타 판정)", s11["app_impact"])
    lines += [f"- 주: {note}" for note in s11["notes"]] + [""]
    lines += ["## S12 이름 정합", ""]
    if not s12["known"]:
        lines += [f"- {s12['note']}",
                  "- 앱 영향 instance_unresolved 폴스타 키 "
                  f"{s12['instance_unresolved_polestar_keys']}"
                  f" · 알람 {s12['instance_unresolved_polestar_alarms']}", ""]
    else:
        lines += _table(["항목", "값"], [
            (k, s12[k]) for k in (
                "apm_alarms", "unresolved_instances", "unresolved_inst_alarms",
                "unresolved_alarms", "unresolved_ratio",
                "apm_keys", "polestar_keys", "apm_keys_with_polestar_ratio",
                "polestar_keys_with_apm_ratio", "near_mismatch_loose", "near_mismatch_contains",
                "token_candidate_instances", "instance_unresolved_polestar_keys",
                "instance_unresolved_polestar_alarms")
        ]) + [""]
        lines += _pairs("정합 사유(알람)", s12["match_reason_alarms"])
        lines += _pairs("정합 사유(인스턴스)", s12["match_reason_instances"])
        lines += _pairs("정합 신뢰도", s12["match_conf"])
        lines += _pairs("호스트 키 신뢰도", s12["hk_strength"])
    lines += ["## S13 사건·결합", ""]
    act = s13["actual"]
    if act["known"]:
        lines += [f"- 실제 기록: 사건 {act['episodes']} · 두 소스 사건 "
                  f"{act['two_source_episodes']} · "
                  f"적용 {act['applied']}", ""]
        lines += _pairs("실제 링크 action", act["by_action"])
        lines += _pairs("실제 빠진 조건", act["missing"])
    else:
        lines += ["- 실제 기록 없음(모드 off 또는 기록 전)", ""]
    sim = s13["sim"]
    if not sim["known"]:
        lines += [f"- 시뮬레이션 모름: {sim['reason']}", ""]
    else:
        lines += _table(["변형", "사건", "두 소스 사건", "link", "demote 후보", "sev3 강등 후보",
                         "빠진 조건"], [
            (name, v["episodes"], v["two_source_episodes"], v["link"], v["would_demote"],
             v["demote_sev3"], ", ".join(f"{k}:{n}" for k, n in v["missing"].items()) or "-")
            for name, v in sim["variants"].items()
        ]) + [""]
    lines += [f"- 주: {note}" for note in s13["notes"]] + [""]
    lines += ["## S14 시간 관계", "", f"- 기준 시각 {s14['basis']} · 최근접 쌍 {s14['pairs']} · "
              f"폴스타 선행 >규칙 원인 창({s14['late_lag_s']}초) 비율 "
              f"{s14['polestar_first_beyond_window_ratio']} · 원인 미해소 비율 "
              f"{s14['cause_open_ratio']}", ""]
    lines += _table(["지연 구간", "폴스타 먼저", "APM 먼저"], [
        (label, s14["polestar_first"][label], s14["apm_first"][label])
        for label in s14["polestar_first"]
    ]) + [""]
    lines += _pairs("원인 kind × WAS kind", s14["kind_pairs_top"])
    lines += _pairs("원인 최종 티어", s14["cause_tiers"])
    lines += _table(["규칙", "원인", "증상", "창 안 비율", "기준 비율", "lift"], [
        (rid, v["causes"], v["effects"], v["p_after"], v["base"], v["lift"])
        for rid, v in s14["lift"].items()
    ]) + [""]
    ro = summary["xs_readout"]
    lines += ["## 판독 결과 (§11.7)", ""]
    if ro["rows"]:
        lines += _table(["원인", "근거", "조치"], [
            (r["cause"], ", ".join(f"{k}={v}" for k, v in r["evidence"].items()), r["action"])
            for r in ro["rows"]
        ]) + [""]
    else:
        lines += ["- 걸린 원인 없음", ""]
    lines += [f"- 문턱: {note}" for note in ro["notes"]] + [""]
    return lines
