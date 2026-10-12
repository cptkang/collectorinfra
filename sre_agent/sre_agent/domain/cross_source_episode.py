"""크로스소스 사건 스냅샷 해석 (plans/150 §4.4·§4.6 · D-323 ⑤⑨ · D-035).

noise_gate 사건 추적기가 트리거 페이로드 `meta.episode`에 실어 보낸
스냅샷을 **읽기만** 한다 — 사건을 여기서 재구성하지 않는다(G-5 (a)).
결합 판정(링크의 `missing`)·WAS kind(`was_kinds`)도 송신 측 값을
옮기기만 한다(D-195 ②). 계약은 `meta` 가산 키라 `contract_version`은
"1" 그대로다(D-118).

**순수 도메인 모듈**(stdlib + 같은 계층 `correlation`만). 질문·지침·
사전수집·브리핑·감사가 같은 해석을 쓰도록 여기 한 곳에 둔다
(application 간 직접 의존 회피 — `investigation_limits`와 같은 이유).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TypeVar

from sre_agent.domain.correlation import EpisodePoint, TimelineItem, merge_timeline

#: 사건 멤버 `source` 값(송신 측 닫힌 어휘).
SOURCE_INFRA = "polestar"
SOURCE_APM = "jennifer"

#: 송신 측 상한과 같은 값(멤버 20 · 링크 50 — plans/150 §4.4). 넘치면 최근 순 앞쪽만 쓴다.
EPISODE_MEMBERS_MAX = 20
EPISODE_LINKS_MAX = 50

#: 종합 조사 표지 `meta.investigation_kind`(plans/150 §4.5 — 사건당 1회 종합 조사).
INVESTIGATION_KIND_EPISODE = "episode"

#: 프롬프트에 싣는 외부 문자열(알람명·규칙 id 등) 상한 — `prompt_text`.
PROMPT_TEXT_MAX = 80
_PROMPT_UNSAFE = str.maketrans({"\r": " ", "\n": " ", "|": " ", "`": " "})

#: 링크 `missing` 값 중 호스트 키 weak 매칭(송신 측 감사 라벨 그대로).
MISSING_HOST_KEY_STRENGTH = "host_key_strength"

#: 타임라인·지침의 소스 라벨(브리핑 `APM_SOURCE_LABEL`·`INFRA_SOURCE_LABEL`과 같은 문자열).
INFRA_LABEL = "인프라"
APM_LABEL = "애플리케이션(APM)"
_SOURCE_NAMES = {SOURCE_INFRA: "인프라(폴스타)", SOURCE_APM: "애플리케이션(제니퍼)"}

T = TypeVar("T")

#: 타임라인 항목 kind — 인프라 멤버는 기존 `alarm`, 애플리케이션 멤버는 신규 `apm_event`.
KIND_INFRA_MEMBER = "alarm"
KIND_APM_MEMBER = "apm_event"

STRONG_ONLY_NOTE = (
    "같은 호스트 키 strong 매칭만 사건에 묶였음 — weak 매칭 알람은 사건 스냅샷에 없다"
)
APM_TOOLS_NOT_CONNECTED_LIMIT = (
    "애플리케이션(APM) 도구 미연결 — 인프라 근거와 사건 스냅샷만으로 판단"
)


@dataclass(frozen=True)
class EpisodeMember:
    """사건 멤버 한 건(송신 측 값 그대로 · `occurred_at`만 ISO로 정규화)."""

    alarm_id: str
    source: str
    db_id: str = ""
    alarm_name: str = ""
    kind: str = ""
    was_kinds: tuple[str, ...] = ()
    level: str = ""
    severity: int | None = None
    occurred_at: str | None = None   # ISO 8601(naive) — 해석 실패면 None
    tier: str | None = None
    resolved: bool = False

    @property
    def label(self) -> str:
        """표시 이름 — 알람명, 없으면 알람 id."""
        return self.alarm_name or self.alarm_id

    @property
    def trait(self) -> str:
        """인프라는 kind, 애플리케이션은 was_kind(쉼표 연결) — 없으면 빈 값."""
        return ",".join(self.was_kinds) if self.source == SOURCE_APM else self.kind


@dataclass(frozen=True)
class EpisodeLink:
    """결합 판정 한 건(원인 → 증상). `missing`이 비어 있어야 결합 성립."""

    rule_id: str
    cause_alarm_id: str
    effect_alarm_id: str
    lag_seconds: float | None = None   # 증상 − 원인(양수 = 원인 선행)
    action: str = ""
    missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class EpisodeRule:
    """사건에 걸린 규칙 한 행(선택자 dict는 송신 측 모양 그대로)."""

    id: str
    cause: dict
    effect: dict
    cause_before_seconds: int | None = None
    cause_after_seconds: int | None = None


@dataclass(frozen=True)
class EpisodeContext:
    """`meta.episode` 해석 결과. `notes`는 절단·해석 실패 등 결정적 한계 문구."""

    episode_id: str
    zone: str = ""
    host_key: str = ""
    members: tuple[EpisodeMember, ...] = ()
    links: tuple[EpisodeLink, ...] = ()
    rules: tuple[EpisodeRule, ...] = ()
    infra_db_ids: tuple[str, ...] = ()
    investigation_kind: str = ""
    reference_time: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def infra_members(self) -> tuple[EpisodeMember, ...]:
        return tuple(m for m in self.members if m.source == SOURCE_INFRA)

    @property
    def apm_members(self) -> tuple[EpisodeMember, ...]:
        return tuple(m for m in self.members if m.source == SOURCE_APM)

    @property
    def is_mixed(self) -> bool:
        """두 소스 멤버가 각 1건 이상 — 크로스소스 질문·지침·브리핑의 성립 조건."""
        return bool(self.infra_members) and bool(self.apm_members)

    @property
    def valid_links(self) -> tuple[EpisodeLink, ...]:
        """결합 조건이 모두 충족된 링크(`missing` 없음)만 — 규칙 가설은 이것만 만든다."""
        return tuple(lk for lk in self.links if not lk.missing)

    def member(self, alarm_id: str) -> EpisodeMember | None:
        return next((m for m in self.members if m.alarm_id == alarm_id), None)

    def link_label(self, alarm_id: str) -> str:
        """링크 끝점의 표시 이름 — 멤버에 없으면(절단) 알람 id 그대로."""
        m = self.member(alarm_id)
        return m.label if m else alarm_id

    def excluded_link_notes(self) -> list[str]:
        """가설에서 뺀 링크 사실(weak 매칭 · 그 밖 결합 조건 미충족) — 침묵 금지."""
        weak = [lk for lk in self.links if MISSING_HOST_KEY_STRENGTH in lk.missing]
        other = [
            lk for lk in self.links if lk.missing and MISSING_HOST_KEY_STRENGTH not in lk.missing
        ]
        out: list[str] = []
        if weak:
            out.append(f"호스트 키 weak 매칭 링크 {len(weak)}건은 규칙 가설에서 제외")
        if other:
            labels = "·".join(sorted({m for lk in other for m in lk.missing}))
            out.append(
                f"결합 조건 미충족 링크 {len(other)}건(누락: {labels})은 규칙 가설에서 제외"
            )
        return out


def _str(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _iso(value: object) -> str | None:
    """발생 시각 → naive ISO.

    epoch 초(송신 측 기본)는 로컬 시각으로 옮긴다(`evidence_prefetch._changes`와 같은 규약).
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value)).replace(tzinfo=None).isoformat()
        return datetime.fromisoformat(str(value).strip()).replace(tzinfo=None).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _int_or_none(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None


def _float_or_none(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _str_tuple(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(_str(v) for v in values if _str(v))


def _member(raw: dict) -> EpisodeMember | None:
    alarm_id = _str(raw.get("alarm_id"))
    source = _str(raw.get("source")).lower()
    if not alarm_id or not source:
        return None
    return EpisodeMember(
        alarm_id=alarm_id,
        source=source,
        db_id=_str(raw.get("db_id")),
        alarm_name=_str(raw.get("alarm_name")),
        kind=_str(raw.get("kind")),
        was_kinds=_str_tuple(raw.get("was_kinds")),
        level=_str(raw.get("level")),
        severity=_int_or_none(raw.get("severity")),
        occurred_at=_iso(raw.get("occurred_at")),
        tier=_str(raw.get("tier")) or None,
        resolved=bool(raw.get("resolved")),
    )


def _link(raw: dict) -> EpisodeLink | None:
    rule_id = _str(raw.get("rule_id"))
    if not rule_id:
        return None
    return EpisodeLink(
        rule_id=rule_id,
        cause_alarm_id=_str(raw.get("cause_alarm_id")),
        effect_alarm_id=_str(raw.get("effect_alarm_id")),
        lag_seconds=_float_or_none(raw.get("lag_seconds")),
        action=_str(raw.get("action")),
        missing=_str_tuple(raw.get("missing")),
    )


def _rule(raw: dict) -> EpisodeRule | None:
    rule_id = _str(raw.get("id"))
    if not rule_id:
        return None
    window = raw.get("window") if isinstance(raw.get("window"), dict) else {}
    return EpisodeRule(
        id=rule_id,
        cause=raw.get("cause") if isinstance(raw.get("cause"), dict) else {},
        effect=raw.get("effect") if isinstance(raw.get("effect"), dict) else {},
        cause_before_seconds=_int_or_none(window.get("cause_before_seconds")),
        cause_after_seconds=_int_or_none(window.get("cause_after_seconds")),
    )


def _parsed(
    items: object, parse: Callable[[dict], T | None], cap: int, what: str, notes: list[str]
) -> tuple[T, ...]:
    rows = [r for r in items if isinstance(r, dict)] if isinstance(items, (list, tuple)) else []
    if len(rows) > cap:
        notes.append(f"사건 {what} {len(rows)}건 중 최근 {cap}건만 사용(상한 절단)")
        rows = rows[:cap]
    elif what == "멤버" and len(rows) == cap:
        notes.append(
            f"사건 멤버가 상한 {cap}건에 도달 — 그보다 오래된 멤버는 스냅샷에서 잘렸을 수 있음"
        )
    return tuple(x for x in (parse(r) for r in rows) if x is not None)


def episode_from_payload(
    payload: object, reference_time: str | None = None
) -> EpisodeContext | None:
    """트리거 페이로드의 `meta.episode`를 해석한다. dict가 아니면 None(사건 맥락 없음)."""
    meta = payload.get("meta") if isinstance(payload, dict) else None
    raw = meta.get("episode") if isinstance(meta, dict) else None
    if not isinstance(raw, dict):
        return None
    notes: list[str] = []
    members = _parsed(raw.get("members"), _member, EPISODE_MEMBERS_MAX, "멤버", notes)
    links = _parsed(raw.get("links"), _link, EPISODE_LINKS_MAX, "링크", notes)
    rules = _parsed(raw.get("rules"), _rule, EPISODE_LINKS_MAX, "규칙", notes)
    return EpisodeContext(
        episode_id=_str(raw.get("episode_id")),
        zone=_str(raw.get("zone")),
        host_key=_str(raw.get("host_key")),
        members=members,
        links=links,
        rules=rules,
        infra_db_ids=_str_tuple(raw.get("infra_db_ids")),
        investigation_kind=_str(meta.get("investigation_kind")),
        reference_time=reference_time,
        notes=tuple(notes),
    )


def active_episode(
    payload: object, enabled: bool, reference_time: str | None = None
) -> EpisodeContext | None:
    """크로스소스 처리 대상 사건 — 플래그 on **이고** 두 소스 멤버가 모두 있을 때만.

    아니면 None(종전 경로).
    """
    if not enabled:
        return None
    ctx = episode_from_payload(payload, reference_time)
    return ctx if ctx is not None and ctx.is_mixed else None


def prompt_text(value: object, limit: int = PROMPT_TEXT_MAX) -> str:
    """조사 LLM 프롬프트에 싣는 외부 문자열의 결정적 정리.

    개행·`|`(표 구분자)·백틱을 공백으로 바꾸고 `limit`자 이내로 자른다(잘리면 끝이 `…`) —
    알람명·규칙 id는 외부 시스템이 만든 텍스트라 표 구조를 깨거나 지시처럼 읽히는 줄을 만들 수 있다.
    """
    text = _str(value).translate(_PROMPT_UNSAFE)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def is_episode_investigation(payload: object) -> bool:
    """종합 조사 잡인가 — `meta.investigation_kind == "episode"`."""
    meta = payload.get("meta") if isinstance(payload, dict) else None
    return isinstance(meta, dict) and meta.get("investigation_kind") == INVESTIGATION_KIND_EPISODE


def episode_reference_time(payload: object) -> str | None:
    """종합 조사의 기준시각 — 사건 멤버 중 가장 늦은 발생 시각(초 해상도 ISO).

    종합 조사는 대표(원인) 알람 event를 싣기 때문에 그 alarmTime을 기준으로 하면
    「기준시각 이전 구간」이 뒤에 온 증상 멤버를 담지 못한다. 종합 조사가 아니거나
    해석 가능한 멤버 시각이 없으면 None(호출자가 종전 기준시각을 쓴다).
    """
    if not is_episode_investigation(payload):
        return None
    ctx = episode_from_payload(payload)
    times = [m.occurred_at for m in ctx.members if m.occurred_at] if ctx else []
    if not times:
        return None
    latest = max(datetime.fromisoformat(t) for t in times)
    return latest.replace(microsecond=0).isoformat()


def source_name(source: str) -> str:
    """지침 표의 소스 이름(인프라(폴스타)·애플리케이션(제니퍼)) — 미지 값은 그대로."""
    return _SOURCE_NAMES.get(source, source)


def episode_points(ctx: EpisodeContext) -> tuple[list[EpisodePoint], list[str]]:
    """사건 멤버 → 타임라인 점(추가 호출 0). 시각 해석 실패 멤버는 건너뛰고 notes에 남긴다."""
    points: list[EpisodePoint] = []
    skipped = 0
    for m in ctx.members:
        if m.occurred_at is None:
            skipped += 1
            continue
        if m.source == SOURCE_APM:
            kind, label = KIND_APM_MEMBER, APM_LABEL
            trait = f" (was {m.trait})" if m.trait else ""
        else:
            kind, label = KIND_INFRA_MEMBER, INFRA_LABEL
            trait = f" ({m.trait})" if m.trait else ""
        points.append(EpisodePoint(m.occurred_at, kind, f"[사건 · {label}] {m.label}{trait}"))
    notes = [f"사건 멤버 {skipped}건 발생 시각 해석 실패(타임라인에서 제외)"] if skipped else []
    return points, notes


def episode_timeline(ctx: EpisodeContext) -> list[TimelineItem]:
    """사건 멤버만의 타임라인(기준시각 좌표계). 기준시각이 없으면 빈 목록."""
    if not ctx.reference_time:
        return []
    points, _ = episode_points(ctx)
    return merge_timeline(ctx.reference_time, [], {}, episode=points)


def selector_text(selector: dict) -> str:
    """규칙 선택자 `{source, kinds|was_kinds|alarm_names}` 한 줄 표기."""
    source = prompt_text(selector.get("source"))
    parts = [source_name(source)] if source else []
    for key in ("kinds", "was_kinds", "alarm_names"):
        values = selector.get(key)
        if isinstance(values, (list, tuple)) and values:
            parts.append(f"{key}={','.join(prompt_text(v) for v in values)}")
    return " ".join(parts) or "?"


__all__ = [
    "SOURCE_INFRA",
    "SOURCE_APM",
    "INVESTIGATION_KIND_EPISODE",
    "PROMPT_TEXT_MAX",
    "prompt_text",
    "is_episode_investigation",
    "episode_reference_time",
    "EPISODE_MEMBERS_MAX",
    "EPISODE_LINKS_MAX",
    "MISSING_HOST_KEY_STRENGTH",
    "INFRA_LABEL",
    "APM_LABEL",
    "KIND_INFRA_MEMBER",
    "KIND_APM_MEMBER",
    "STRONG_ONLY_NOTE",
    "APM_TOOLS_NOT_CONNECTED_LIMIT",
    "EpisodeMember",
    "EpisodeLink",
    "EpisodeRule",
    "EpisodeContext",
    "episode_from_payload",
    "active_episode",
    "source_name",
    "episode_points",
    "episode_timeline",
    "selector_text",
]
