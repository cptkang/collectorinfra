"""SMQ 중간표현(IR) 모델 — gold_smq 계약과 일치 (Plan 61 트랙 C / D-076).

``src/nodes/semantic_compiler.py``에서 분리했다(Plan 69 P5-1) — 상태·설정·LLM에 결합하지
않는 순수 데이터 모델이라 nodes 밖에 두어 ``src.tools``가 nodes를 거치지 않고 참조하게
한다(순환 해소).
"""

from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# 집계 함수 — count/sum은 Plan 67 S-IR1 확장(전역 집계·건수 수요). 값 컬럼은 모델
# pattern_b.value_columns에 avg/max/min만 있으므로 count/sum은 기본 값 컬럼으로 떨어진다.
_AGG_FN = {"avg": "AVG", "max": "MAX", "min": "MIN", "count": "COUNT", "sum": "SUM"}

# SMQ 필터 op → SQL 연산자. IR 수준 지식이라 컴파일러가 매핑하고, 리터럴 조립은 조립기가 한다.
_FILTER_SQL_OPS = {"eq": "=", "ne": "<>", "gte": ">=", "lte": "<=", "like": "LIKE", "in": "IN"}
# 측정치 임계(HAVING)로 표현 가능한 op — 집계값 비교이므로 like/in은 제외한다.
_MEASURE_FILTER_OPS = {"eq", "ne", "gte", "lte"}
# IR limit 허용 상한 — resolve_query_limit의 "전체" 상향값과 같은 자리수로 맞춘다(과대 요청 차단).
_MAX_IR_LIMIT = 100_000
# 패턴 C 건수 집계 alias (S-IR5).
_ALARM_COUNT_ALIAS = "alarm_count"
# IR 기간 값 형식(YYYYMM) — 통계 기간 컬럼과 알람 기간 창이 공유한다. 월 범위(01~12)까지
# 검증한다: '999999' 같은 값이 통과하면 알람 기간 창이 잘못된 날짜 리터럴로 조립된다.
_YYYYMM_RE = re.compile(r"\d{4}(?:0[1-9]|1[0-2])")


class SMQMeasure(BaseModel):
    """성능지표 measure (패턴 B). gold_smq measure dict와 동일 필드."""

    agg: str                        # avg | max | min | count | sum (count/sum은 S-IR1 확장)
    definition_name: str            # Utilization | MaxIORate
    resource_type: str              # server.Cpus 등

    @field_validator("agg")
    @classmethod
    def _normalize_agg(cls, value: str) -> str:
        """집계 표기의 대소문자·공백 흔들림을 결정적으로 흡수한다.

        LLM이 ``"AVG"``·``"COUNT"``로 내면 커버리지 판정(``agg not in _AGG_FN``, 대소문자
        구분)이 "미지원 집계"로 돌려 정확한 선택이 통째로 폴백됐다. 표기만 정규화하고
        **유효값 검증은 그대로** 커버리지 판정에 남긴다(미지원 집계는 여전히 폴백).
        definition_name·resource_type은 카탈로그의 정확한 이름이어야 하므로(Model vs MODEL)
        정규화 대상이 아니다.
        """
        return str(value).strip().lower()

    def as_dict(self) -> dict:
        return {"agg": self.agg, "definition_name": self.definition_name,
                "resource_type": self.resource_type}

    @property
    def alias(self) -> str:
        """SELECT/ORDER BY/HAVING이 공유하는 measure alias(기존 조립 규칙과 동일)."""
        return f"{self.resource_type.split('.')[-1].lower()}_{self.agg.lower()}"


class SMQOrderBy(BaseModel):
    """정렬 지정 (Plan 67 S-IR3) — 표면어(`_RANK_*_MARKERS`) 대신 IR로 받는다.

    ``field``는 measure alias(예: cpus_avg)·measure resource_type(server.Cpus)·dimension
    이름 중 하나이며, 컴파일러가 카탈로그·선택된 measure로 해소한다(해소 불가는 커버리지 밖).
    """

    field: str
    direction: str = "desc"         # asc | desc

    def as_dict(self) -> dict:
        return {"field": self.field, "direction": self.direction}


#: LLM이 order_by 대상 키를 흔들어 쓰는 표기들(field 외). 값 자체는 카탈로그로 검증되므로
#: 키 표기 차이만 결정적으로 흡수한다 — 흡수하지 못하면 정렬 지정이 통째로 폴백으로 새 나간다.
_ORDER_FIELD_KEYS = ("field", "measure", "column", "alias", "name")
_ORDER_DIRECTION_KEYS = ("direction", "dir", "order", "sort")


def _coerce_order_by(value: Any) -> Optional[SMQOrderBy]:
    """LLM/골드 산출물의 order_by 표기를 ``SMQOrderBy``로 정규화한다(불가하면 None)."""
    if value is None or isinstance(value, SMQOrderBy):
        return value
    if isinstance(value, str):
        return SMQOrderBy(field=value)
    if not isinstance(value, dict):
        return None
    field = next((str(value[k]) for k in _ORDER_FIELD_KEYS if value.get(k)), "")
    if not field:
        return None
    direction = next(
        (str(value[k]) for k in _ORDER_DIRECTION_KEYS if value.get(k)), "desc"
    )
    return SMQOrderBy(field=field, direction=direction)


class SMQFilter(BaseModel):
    """WHERE/HAVING 필터 (field, op, value). gold_smq filter dict와 동일 필드."""

    field: str
    op: str                         # eq | ne | in | like | gte | lte
    value: Any

    def as_dict(self) -> dict:
        return {"field": self.field, "op": self.op, "value": self.value}


class SMQ(BaseModel):
    """폴스타판 Semantic Model Query — LLM이 선택하고 컴파일러가 결정적으로 조립한다."""

    pattern: Literal["A", "B", "C"]
    resource_types: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)          # 패턴 C
    dimensions: list[str] = Field(default_factory=list)
    measures: list[SMQMeasure] = Field(default_factory=list)
    filters: list[SMQFilter] = Field(default_factory=list)
    time_grain: Optional[str] = None                            # hour | day | month | None
    active_only: bool = False                                   # 패턴 C

    # === Plan 67 S3 IR 확장 (S-IR1~5) ===
    # 신 필드가 없는 SMQ(gold_smq·1방 선택 산출물)는 기존 경로와 완전히 동일하게 컴파일된다.
    global_aggregate: bool = False       # S-IR1 전역 단일 행 집계(GROUP BY 생략)
    entity_count: bool = False           # S-IR1/5 엔티티 수 집계(서버 수·알람 건수)
    time_breakdown: bool = False         # S-IR2 통계 기간별 행 분해(월별/일별)
    order_by: Optional[SMQOrderBy] = None                       # S-IR3
    limit: Optional[int] = None                                 # S-IR3
    time_range: Optional[list[str]] = None                      # S-IR4 기간 [YYYYMM] | [시작, 끝]

    @classmethod
    def from_dict(cls, data: dict) -> "SMQ":
        """gold_smq/LLM 산출 dict에서 SMQ를 만든다(measures/filters는 dict 리스트).

        신 필드(S-IR 확장)가 없는 dict도 그대로 수용한다 — gold_smq 계약 호환 유지.
        """
        d = dict(data or {})
        d["measures"] = [SMQMeasure(**m) if isinstance(m, dict) else m
                         for m in d.get("measures", []) or []]
        d["filters"] = [SMQFilter(**f) if isinstance(f, dict) else f
                        for f in d.get("filters", []) or []]
        if "order_by" in d:
            d["order_by"] = _coerce_order_by(d.get("order_by"))
        if isinstance(d.get("time_range"), str):
            d["time_range"] = [d["time_range"]]
        return cls(**d)

    def to_match_dict(self) -> dict:
        """E1 하네스 ``smq_match``가 채점하는 dict 표현(순서 무관 비교 대상).

        확장 필드도 함께 실어 라운드트립(``from_dict(to_match_dict())``)을 보존한다 —
        ``smq_match``는 채점 키만 비교하므로 골드 SMQ와의 대조에는 영향이 없다.
        """
        return {
            "pattern": self.pattern,
            "resource_types": list(self.resource_types),
            "entities": list(self.entities),
            "dimensions": list(self.dimensions),
            "measures": [m.as_dict() for m in self.measures],
            "filters": [f.as_dict() for f in self.filters],
            "time_grain": self.time_grain,
            "active_only": self.active_only,
            "global_aggregate": self.global_aggregate,
            "entity_count": self.entity_count,
            "time_breakdown": self.time_breakdown,
            "order_by": self.order_by.as_dict() if self.order_by else None,
            "limit": self.limit,
            "time_range": list(self.time_range) if self.time_range else None,
        }


class CoverageResult(BaseModel):
    """커버리지 판정 결과 — 내부(covered=True)면 컴파일, 밖이면 reason과 함께 폴백."""

    covered: bool
    reason: str = ""
