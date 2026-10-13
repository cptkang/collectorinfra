"""세계 YAML 로더 (plans/151 §4.2).

세계 하나 = 합성 장애 하나. 세계 서버(`world_server`)·스키마 검사(`schema.check_world`)·이후 Wave의
채점기·린트가 같은 로더를 쓴다. 원문 dict를 그대로 들고 다니고, 여러 곳이 쓰는 해석(호스트 집합 ·
알람 시각 · 사건 구간)만 속성으로 둔다.

시각은 모두 **벽시계(naive)**로 다룬다 — 폴스타 도구 설명의 `reference_time`이 「DB 벽시계」이고
알람 이벤트 `alarmTime`(`YYYYMMDDHHMMSS`)도 시간대가 없다. 시간대가 붙은 값은 시간대를 떼고 쓴다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

_OFFSET_RE = re.compile(r"^\s*([+-]?)\s*(\d+)\s*([smhd])\s*$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_offset(text: Any) -> timedelta:
    """`-45m`·`0m`·`2h`·`+30s` 같은 상대 시간을 timedelta로 바꾼다. 형식이 틀리면 ValueError."""
    m = _OFFSET_RE.match(str(text))
    if not m:
        raise ValueError(f"상대 시간 형식이 아니다(예: -45m · 2h): {text!r}")
    sign = -1 if m.group(1) == "-" else 1
    return timedelta(seconds=sign * int(m.group(2)) * _UNIT_SECONDS[m.group(3)])


def parse_wall_time(text: Any) -> datetime:
    """`YYYYMMDDHHMMSS` 또는 ISO 8601 → 벽시계 datetime(naive). 형식이 틀리면 ValueError."""
    s = str(text).strip()
    if s.isdigit() and len(s) == 14:
        return datetime.strptime(s, "%Y%m%d%H%M%S")
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt.replace(tzinfo=None)


@dataclass(frozen=True)
class World:
    """세계 YAML 1건. `data`가 원문 전체다."""

    id: str
    data: dict[str, Any]
    path: Path | None = None

    @property
    def split(self) -> str:
        """개발(`dev`)·홀드아웃(`holdout`) 구분."""
        return str(self.data.get("split") or "")

    @property
    def event(self) -> dict[str, Any]:
        """조사 입력 알람 이벤트."""
        return dict(self.data.get("event") or {})

    @property
    def tools(self) -> dict[str, Any]:
        """도구별 응답 규칙(`tools:`) — 키 `default`는 정의 없는·안 맞은 호출의 응답이다."""
        return dict(self.data.get("tools") or {})

    @property
    def hosts(self) -> frozenset[str]:
        """이 세계의 호스트 집합(비교용으로 소문자화)."""
        return frozenset(str(h).strip().casefold() for h in self.data.get("hosts") or [])

    @property
    def label(self) -> dict[str, Any]:
        """정답 라벨(`root_entity`·`layer`·`kind`·`aliases` — 채점기 입력)."""
        return dict(self.data.get("label") or {})

    @property
    def key_evidence(self) -> list[dict[str, Any]]:
        """핵심 증거 목록(ECR 분모) — `{tool, match?, cite[]}`."""
        return [dict(k) for k in self.data.get("key_evidence") or [] if isinstance(k, dict)]

    @property
    def distractors(self) -> list[dict[str, Any]]:
        """유인 목록(DIST 판정) — `{kind, aliases[]}`."""
        return [dict(d) for d in self.data.get("distractors") or [] if isinstance(d, dict)]

    @property
    def expect(self) -> dict[str, Any]:
        """기대 판정(`abstain` 등)."""
        return dict(self.data.get("expect") or {})

    @property
    def alarm_time(self) -> datetime | None:
        """알람 시각(`event.alarmTime`) — 없으면 None."""
        raw = self.event.get("alarmTime")
        return parse_wall_time(raw) if raw not in (None, "") else None

    def incident_range(self) -> tuple[datetime, datetime] | None:
        """사건 구간 `[start, end]`(알람 시각 기준 상대값을 펼친 것) — 정의가 없으면 None."""
        window = self.data.get("incident_window")
        anchor = self.alarm_time
        if not window or anchor is None:
            return None
        start = anchor + parse_offset(window.get("start", "0m"))
        end = anchor + parse_offset(window.get("end", "0m"))
        return (start, end) if start <= end else (end, start)


def load_world(path: Path) -> World:
    """세계 YAML 1건을 읽는다. 최상위가 매핑이 아니거나 `id`가 없으면 ValueError."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"세계 YAML 최상위가 매핑이 아니다: {path}")
    world_id = data.get("id")
    if not world_id:
        raise ValueError(f"세계 YAML에 id가 없다: {path}")
    return World(id=str(world_id), data=data, path=Path(path))


def load_worlds(paths: Iterable[Path]) -> dict[str, World]:
    """파일·디렉터리(하위 `*.yaml` 전부) 목록에서 세계를 읽는다. id가 겹치면 ValueError."""
    files: list[Path] = []
    for p in paths:
        p = Path(p)
        files.extend(sorted(p.rglob("*.yaml")) if p.is_dir() else [p])
    worlds: dict[str, World] = {}
    for f in files:
        world = load_world(f)
        if world.id in worlds:
            raise ValueError(f"세계 id 중복: {world.id} ({worlds[world.id].path} · {f})")
        worlds[world.id] = world
    return worlds
