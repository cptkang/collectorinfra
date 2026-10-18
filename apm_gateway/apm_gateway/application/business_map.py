"""업무 매핑 — 정합 파일 `business_map` 해석 · 업무 id 비교 키 · 업무 표 캐시 수명 · 업무명 해석
항목(plans/130 N-2 · plans/178 W1에서 `tools.py`에서 옮김)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from apm_gateway.application.name_search import same_name
from apm_gateway.application.resolver import Inventory
from apm_gateway.application.sources import JenniferSource

# 옮기기 전 모듈의 로거 이름을 그대로 쓴다 — 경고 로그의 로거 이름 불변(plans/178 W1 · D-337)
logger = logging.getLogger("apm_gateway.application.tools")

# plans/130 N-2 — 업무 표 캐시 수명(초)
BUSINESS_CACHE_SECONDS = 600.0


@dataclass(frozen=True)
class BusinessMapping:
    """정합 파일 `business_map` 항목 하나 — 업무명·별칭 → 인스턴스 이름(plans/130 N-2 B0 · 수동)."""

    business: str
    aliases: tuple[str, ...]
    instances: tuple[str, ...]
    source_id: str | None

    def matches(self, text: str) -> bool:
        return any(same_name(n, text) for n in (self.business, *self.aliases))


def _names(value: Any) -> tuple[str, ...] | None:
    """비지 않은 문자열 목록 → 앞뒤 공백 제거 튜플. 형식이 다르면 None."""
    if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
        return None
    return tuple(x.strip() for x in value)


def _business_entry(item: Any) -> BusinessMapping | None:
    if not isinstance(item, dict):
        return None
    business, source_id = item.get("business"), item.get("source_id")
    aliases = _names(item.get("aliases") if item.get("aliases") is not None else [])
    instances = _names(item.get("instances"))
    if not isinstance(business, str) or not business.strip() or aliases is None or not instances:
        return None
    if source_id is not None and not (isinstance(source_id, str) and source_id.strip()):
        return None
    return BusinessMapping(
        business.strip(), aliases, instances, source_id.strip() if source_id else None
    )


def parse_business_map(instance_map: dict[str, Any]) -> list[BusinessMapping]:
    """정합 파일의 선택 키 `business_map` — `[{business, aliases?, instances, source_id?}]`. 형식이
    틀린 항목은 경고하고 뺀다(침묵 금지 · 로그에는 순번만)."""
    raw = instance_map.get("business_map")
    if raw is None:
        return []
    if not isinstance(raw, list):
        logger.warning("정합 파일 business_map은 목록이어야 한다 — 무시")
        return []
    out = []
    for idx, item in enumerate(raw):
        entry = _business_entry(item)
        if entry is None:
            logger.warning(
                "정합 파일 business_map[%d] 형식 오류 — 무시(business 문자열·instances 문자열 목록"
                " 필수 · aliases는 문자열 목록 · source_id는 문자열)",
                idx,
            )
            continue
        out.append(entry)
    return out


def business_key(value: Any) -> str:
    """업무 id 비교 키 — 업무 목록(정수)과 거래·액티브 서비스의 업무 id(원값)를 맞춘다."""
    return str(value).strip()


def cache_fresh(entry: tuple[float, Any] | None, now: float) -> bool:
    """업무 표 캐시 항목이 수명(`BUSINESS_CACHE_SECONDS`) 안인가."""
    return entry is not None and now - entry[0] < BUSINESS_CACHE_SECONDS


@dataclass
class BusinessHit:
    """업무명 해석의 인스턴스 하나 — 근거·맞은 업무 이름·이름 검색 단계를 모은다."""

    rank: int
    src: JenniferSource
    inv: Inventory
    inst: dict[str, Any]
    kinds: set[str]
    business_names: list[str]
    tier: str = ""
