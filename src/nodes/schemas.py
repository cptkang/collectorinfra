"""요구사항 추출 출력 계약 (Plan 79 트랙 E-3c / D-169).

`input_parser`의 **두 함수가 공유**한다 — 이것이 종전 비대칭을 구조적으로 해소한다:
`_parse_natural_language`는 `synonym_registration` 기본값을 넣는데
`_parse_natural_language_with_csv`는 넣지 않았다(실측 · Known Mistakes "단일/멀티 경로 대칭").

중첩 항목(`filter_conditions` 등)은 **느슨하게** 둔다. 현재 자유 형식이라 엄격 모델을 씌우면
지금 통과하던 출력이 거부된다 — 이번 목표는 상위 필드의 존재·타입 계약이다.

계층: application.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ParsedRequirements(BaseModel):
    """자연어 질의에서 추출한 요구사항."""

    query_targets: list[Any] = Field(default_factory=list)
    output_format: str = "text"
    filter_conditions: list[Any] = Field(default_factory=list)
    time_range: Optional[Any] = None
    aggregation: Optional[Any] = None
    limit: Optional[int] = None
    field_mapping_hints: list[Any] = Field(default_factory=list)
    target_db_hints: list[Any] = Field(default_factory=list)
    synonym_registration: Optional[Any] = None
