"""조회 대상 도메인 ↔ 표면어 대응 표 로더 (plans/123 S-7(a) · 사용자 확정 2026-09-30).

**무엇을 하나.** 입력 파서가 낸 `query_targets`(닫힌 도메인 10종)가 사용자 원문에 어떤 말로
나타날 수 있는지를 선언 파일 `config/query_target_surfaces.yaml`에서 읽는다. 판정(원문에
있는가)은 소비처(`src/nodes/input_parser.py`)가 위치 표면어와 같은 공용 규칙으로 한다 —
이 모듈은 데이터만 준다.

**규칙은 코드가 아니라 선언 파일에 있다**(`src/domain/change_terms.py` 선례). 파일이 없으면
빈 표다 — 그러면 도메인 이름 자체만 표면어로 쓰인다(트리거가 넓게 켜질 뿐 응답은 바뀌지 않는다).

계층: domain — 순수 · 내부 모듈 의존 0.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_CONFIG = Path(__file__).resolve().parents[2] / "config" / "query_target_surfaces.yaml"


@lru_cache(maxsize=1)
def _raw_config() -> dict[str, Any]:
    try:
        return yaml.safe_load(_CONFIG.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}


@lru_cache(maxsize=1)
def surface_table() -> dict[str, tuple[str, ...]]:
    """도메인 → 표면어(도메인 이름 자체를 맨 앞에 둔다 · 선언 순서 · 중복 제거)."""
    table: dict[str, tuple[str, ...]] = {}
    for domain, surfaces in (_raw_config().get("domains") or {}).items():
        name = str(domain).strip()
        if not name:
            continue
        seen = [name]
        for surface in surfaces or []:
            text = str(surface).strip()
            if text and text not in seen:
                seen.append(text)
        table[name] = tuple(seen)
    return table


def surfaces_of(domain: str) -> tuple[str, ...]:
    """도메인의 표면어. 표에 없는 도메인은 그 이름 하나다(파서가 어휘 밖 값을 낸 경우)."""
    name = str(domain or "").strip()
    return surface_table().get(name, (name,) if name else ())


__all__ = ["surface_table", "surfaces_of"]
