"""크로스소스 사건 상관 규칙 표 적재 · 이벤트 → 사건 알람 변환 (plans/144 §5.1 · W3).

규칙 표(`config/cross_source_rules.yaml`)를 읽어 검증한 뒤 도메인 `CrossSourceRule` 튜플로 바꾼다.
워커가 `NOISE_CROSS_SOURCE_MODE≠off`일 때만 기동 시 1회 부른다. 파일 없음·YAML 오류·스키마
위반이면 경고 1줄 후 None — 호출부는 사건 상관 전체를 끈다(off와 같은 판정).

적재 규칙:
    - `enabled: false` 행은 건너뛴다(Q-5 ① 비활성 행).
    - 증상(effect) 소스가 polestar인 역방향 행의 `demote_effect`는 `link`로 바꾸고 경고 1줄
      (G-3 (a)).
    - `kinds`는 인프라 알람 kind 어휘(`cross_source.POLESTAR_KINDS`)만, `was_kinds`는 게이트웨이
      kind 모양(`was_*` 또는 `*`)만 받는다 — 게이트웨이 패키지를 import하지 않는 느슨한 검증이다.

`episode_alarm`은 워커·리플레이 하네스가 같은 규칙으로 이벤트를 도메인 값으로 옮기게 한다.

계층: infrastructure — domain(`cross_source`·`process_rank`·`alarm`) + 같은 계층 읽기 헬퍼만 쓴다.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from noise_gate.domain.alarm import AlarmEvent
from noise_gate.domain.cross_source import (
    POLESTAR_KINDS,
    RULE_ACTION_DEMOTE,
    RULE_ACTION_LINK,
    SOURCE_JENNIFER,
    SOURCE_POLESTAR,
    WILDCARD,
    CrossSourceRule,
    EpisodeAlarm,
    SideSpec,
)
from noise_gate.domain.process_rank import classify_alarm_kind
from noise_gate.infrastructure.apm_noise_context import apm_payload, was_signal_kinds

logger = logging.getLogger(__name__)

#: 저장소 루트 — 상대 경로 설정값(`config/cross_source_rules.yaml`)의 기준.
_REPO_ROOT = Path(__file__).resolve().parents[2]

_WAS_KIND_RE = re.compile(r"was_[a-z0-9_]+")
_EQUAL_KEYS = frozenset({"zone", "host_key"})

_Source = Literal["polestar", "jennifer"]


class _Side(BaseModel):
    """규칙의 원인·증상 한쪽."""

    model_config = ConfigDict(extra="forbid")

    source: _Source
    kinds: list[str] = Field(default_factory=list)
    was_kinds: list[str] = Field(default_factory=list)
    alarm_names: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_selectors(self) -> _Side:
        """선택자 1개 이상 · 소스별 어휘 검사."""
        if not (self.kinds or self.was_kinds or self.alarm_names):
            raise ValueError("kinds·was_kinds·alarm_names 중 하나는 있어야 한다")
        if self.kinds:
            if self.source != SOURCE_POLESTAR:
                raise ValueError("kinds는 source=polestar에만 쓴다")
            unknown = sorted(set(self.kinds) - POLESTAR_KINDS)
            if unknown:
                raise ValueError(f"kinds 어휘 밖 값 {unknown} (허용: {sorted(POLESTAR_KINDS)})")
        if self.was_kinds:
            if self.source != SOURCE_JENNIFER:
                raise ValueError("was_kinds는 source=jennifer에만 쓴다")
            bad = sorted(
                k for k in self.was_kinds if k != WILDCARD and not _WAS_KIND_RE.fullmatch(k)
            )
            if bad:
                raise ValueError(f"was_kinds는 was_* 또는 '*'만 받는다: {bad}")
        return self

    def to_domain(self) -> SideSpec:
        """도메인 값으로 바꾼다."""
        return SideSpec(
            source=self.source,
            kinds=frozenset(self.kinds),
            was_kinds=frozenset(self.was_kinds),
            alarm_names=frozenset(n for n in self.alarm_names if n),
        )


class _Window(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cause_before_seconds: int = Field(ge=0)
    cause_after_seconds: int = Field(ge=0)


class _Rule(BaseModel):
    """규칙 표 한 행."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    enabled: bool = True
    enforce: bool = False
    cause: _Side
    effect: _Side
    equal: list[str]
    window: _Window
    action: Literal["link", "demote_effect"]

    @field_validator("equal")
    @classmethod
    def _check_equal(cls, value: list[str]) -> list[str]:
        """사건 키와 같은 [zone, host_key]만 받는다."""
        if set(value) != _EQUAL_KEYS or len(value) != len(_EQUAL_KEYS):
            raise ValueError("equal은 [zone, host_key]만 지원한다")
        return value

    @model_validator(mode="after")
    def _check_sources(self) -> _Rule:
        """원인과 증상은 서로 다른 소스(크로스소스)."""
        if self.cause.source == self.effect.source:
            raise ValueError("cause.source와 effect.source가 같다 — 크로스소스 규칙이 아니다")
        return self


class _RuleTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    rules: list[_Rule] = Field(default_factory=list)

    @field_validator("rules")
    @classmethod
    def _unique_ids(cls, value: list[_Rule]) -> list[_Rule]:
        """규칙 id 중복 금지(감사 키)."""
        seen: set[str] = set()
        for rule in value:
            if rule.id in seen:
                raise ValueError(f"규칙 id 중복: {rule.id}")
            seen.add(rule.id)
        return value


def resolve_rules_path(path: str | Path) -> Path:
    """설정 경로 → 절대 경로(상대 경로는 저장소 루트 기준)."""
    p = Path(path)
    return p if p.is_absolute() else _REPO_ROOT / p


def parse_rules(data: Any) -> tuple[CrossSourceRule, ...]:
    """YAML에서 읽은 객체를 검증해 활성 규칙 튜플로 바꾼다.

    Raises:
        pydantic.ValidationError — 스키마 위반(호출부가 적재 실패로 처리).
    """
    table = _RuleTable.model_validate(data if data is not None else {})
    rules: list[CrossSourceRule] = []
    for row in table.rules:
        if not row.enabled:
            continue
        action: str = row.action
        if row.effect.source == SOURCE_POLESTAR and action == RULE_ACTION_DEMOTE:
            logger.warning(
                "크로스소스 규칙 %s: 역방향(증상=polestar) demote_effect는 link로 적용한다(G-3)",
                row.id,
            )
            action = RULE_ACTION_LINK
        rules.append(
            CrossSourceRule(
                id=row.id,
                cause=row.cause.to_domain(),
                effect=row.effect.to_domain(),
                cause_before_seconds=row.window.cause_before_seconds,
                cause_after_seconds=row.window.cause_after_seconds,
                action=action,
                enforce=row.enforce,
            )
        )
    return tuple(rules)


def load_rules(path: str | Path) -> tuple[CrossSourceRule, ...] | None:
    """규칙 표를 1회 적재한다. 실패하면 경고 1줄 후 None(사건 상관 off와 같은 판정)."""
    target = resolve_rules_path(path)
    try:
        return parse_rules(yaml.safe_load(target.read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001 — 파일 없음·YAML 오류·스키마 위반 모두 off로
        reason = f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
        logger.warning(
            "크로스소스 규칙 표 적재 실패 — 사건 상관을 끈다(off와 같은 판정): path=%s 사유=%s",
            target,
            reason,
        )
        return None


def episode_alarm(event: AlarmEvent, *, fingerprint: str, is_apm: bool) -> EpisodeAlarm:
    """이벤트 → 사건 알람(도메인 값).

    존은 `server_identity.zone`(레지스트리 · 제니퍼는 소스↔존 D-287 ④), 발생 시각은
    `alarm_time`(naive면 로컬 시각으로 epoch 변환 — 두 소스가 같은 규칙), APM 알람의 WAS kind는
    게이트웨이가 실어 보낸 값 그대로다. (W4) APM 알람의 레벨은 `raw.apm.level`(소문자) — 앱 영향
    판정이 게이트웨이 조회 행의 `level`과 같은 기준을 쓴다.
    """
    identity = event.server_identity
    return EpisodeAlarm(
        alarm_id=str(event.alarm_id),
        source=SOURCE_JENNIFER if is_apm else SOURCE_POLESTAR,
        zone=(identity.zone if identity is not None else "") or "",
        host_key=event.host_key,
        host_key_strength=event.host_key_strength,
        db_id=event.db_id or "",
        occurred_at=event.alarm_time.timestamp(),
        severity=int(event.severity),
        kind="" if is_apm else (classify_alarm_kind(event) or ""),
        was_kinds=was_signal_kinds(event) if is_apm else (),
        alarm_name=event.alarm_name or "",
        fingerprint=fingerprint,
        level=str(apm_payload(event).get("level") or "").strip().lower() if is_apm else "",
    )
