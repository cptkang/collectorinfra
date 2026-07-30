"""시맨틱 모델 기반 결정적 SQL 컴파일러 (Plan 61 트랙 C / E6 / D-076).

LLM이 SQL을 직접 쓰지 않고 검증된 dimension/measure/entity를 **선택(SMQ)**하면, 이 모듈이
결정적으로 SQL을 조립한다 — 잘못된 조인·집계·미존재 컬럼(구조적 환각)이 원천 불가능해진다
(D-035 결정적=판단 정합). 커버리지 내 정형 질의(서버 설정·성능지표·알람)의 1차 방어선.

구성:
    - ``SMQ`` (Pydantic): 폴스타판 Semantic Model Query 중간표현. gold_smq 계약과 일치
      (pattern/resource_types/dimensions/measures/filters/time_grain/active_only/entities).
    - ``load_semantic_model(db_id)``: ``config/semantic_models/{db_id}.yaml`` 로드
      (db_profiles 불변 — 입력 소스로만 참조). db_engine/db_schema는 여기 저장하지 않고
      ``get_domain_by_id``에서 결정적으로 주입한다(D-066 후속6 단일 출처).
    - ``check_coverage(smq, model, value_index)``: 질의가 시맨틱 모델로 결정적 처리 가능한지 판정.
    - ``compile_smq(smq, db_id)``: SMQ → 방언별 SQL. 패턴 A(서버설정)+B(성능지표)는 기존 결정적
      조립 엔진 ``build_multi_resource_pivot_sql``(D-068)을 **재사용**한다(이중 조립 엔진 금지 —
      D-067 단일 출처). 패턴 C(알람)는 정규화 조인 전용 조립.

리터럴 정확성(E5-2 연결): 컴파일러가 emit하는 리터럴(resource_type·EAV 속성명·severity)은 전부
**시맨틱 모델이 정의한 검증된 값**이라 구조적으로 환각이 불가능하다. SMQ 필터가 모델 밖의 실측
값을 참조하면 ``check_coverage``가 값 인덱스(E5-2)로 검증하고, 미검증이면 커버리지 밖으로 돌린다.

계층: application(nodes) — utils.query_gen_common·routing.db_schema/domain_config·config 참조.
활성화: ``cfg.text2sql.semantic_compose`` 플래그(기본 OFF). OFF 시 호출부가 미진입한다.
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING, Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from src.routing.db_schema import get_schema_prefix
from src.routing.domain_config import get_domain_by_id
from src.utils.json_extract import extract_json_from_response
from src.utils.query_gen_common import (
    StatMonth,
    resolve_query_limit,
    resolve_stat_month_range,
)
# 폴스타 피벗 조립기는 어댑터로 이동(Plan 63 P2, D-089) — application 직접 임포트(D-067 재사용).
from src.db_adapters.polestar.assembler import build_multi_resource_pivot_sql

if TYPE_CHECKING:  # 타입 표기 전용 — 런타임 임포트는 진입 분기 안에서 지연 수행한다.
    from src.config import AppConfig
    from src.nodes.column_deriver import StepwiseDeps

logger = logging.getLogger(__name__)

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


# ──────────────────────────────────────────────
# 교정 가드·게이트 발동 계측 (Plan 67 R4)
# ──────────────────────────────────────────────

#: 이름 → 누적 발동 횟수. LLM 비결정 교정 가드와 표면어 폴백이 실제로 얼마나 발동하는지
#: 계측해 stepwise ON/OFF 발동률을 비교하는 재료다(계획서 §3.2-R4). **계측만** 하고 가드는
#: 삭제하지 않는다 — 발동 0이 실증된 것부터 단계 축소한다.
_GUARD_COUNTERS: dict[str, int] = {}

GUARD_PHYSICALCORE_DROP = "normalize.physicalcore_drop"       # 동시 선택 시 PHYSICALCORE 제거
GUARD_PHYSICALCORE_SWAP = "normalize.physicalcore_swap"       # 단독 선택 시 LOGICALCORE 치환
GUARD_CAPACITY_INJECT = "normalize.capacity_inject"           # 명시 용량 차원 누락 보정
GUARD_TIME_FILTER_PROMOTE = "normalize.time_filter_promote"   # 기간 표현 필터 → time_range 승격
GUARD_TIME_RANGE_OVERRIDE = "normalize.time_range_override"   # LLM 기간값을 결정적 해석으로 교정
GUARD_BREAKDOWN_PROMOTE = "normalize.breakdown_promote"       # 월별 표면어 → time_breakdown 승격
GUARD_MONTHLY_GATE = "gate.monthly_breakdown_fallback"        # 월별 폴백 게이트(승격 불가 시)
GUARD_RANKING_SURFACE = "ranking.surface_fallback"            # 표면어 기반 정렬 결정(IR 부재 폴백)
GUARD_SCOPE_FILTER_STRIP = "scope.identity_filter_strip"      # 선행 스코프 우선 — SMQ 식별 필터 제거
GUARD_SCOPE_GLOBAL_DROP = "scope.global_aggregate_drop"       # 선행 스코프 우선 — 전역 집계 해제
GUARD_RESOURCE_TYPE_FILTER_IGNORED = "compile.resource_type_filter_ignored"
GUARD_IR_ORDER_BY = "ir.order_by"                             # IR 정렬 사용(표면어 미의존)
GUARD_IR_LIMIT = "ir.limit"                                   # IR 상한 사용
GUARD_IR_TIME_RANGE = "ir.time_range"                         # IR 기간 사용(호출부 미지정 시)
GUARD_HYPERNYM_EXPAND = "taxonomy.hypernym_expand"            # 상위어 단독 질의 → 하위 전부 제시


def note_guard(name: str, detail: str = "") -> None:
    """교정 가드·폴백 게이트의 발동을 계측한다(R4 — 발동률 비교 재료).

    Args:
        name: 가드 식별자(``GUARD_*`` 상수)
        detail: 로그에 남길 부가 사유(선택)
    """
    _GUARD_COUNTERS[name] = _GUARD_COUNTERS.get(name, 0) + 1
    logger.info(
        "[가드계측] %s 발동(누적 %d)%s",
        name, _GUARD_COUNTERS[name], f" — {detail}" if detail else "",
    )


def guard_counters() -> dict[str, int]:
    """가드 발동 누적 카운터의 사본을 반환한다."""
    return dict(_GUARD_COUNTERS)


def reset_guard_counters() -> None:
    """가드 발동 카운터를 초기화한다(계측 구간 분리·테스트용)."""
    _GUARD_COUNTERS.clear()


def _guard_delta(before: dict[str, int]) -> dict[str, int]:
    """스냅샷 이후 발동한 가드만 골라 {이름: 증분}으로 만든다(질의 단위 귀속)."""
    return {
        name: count - before.get(name, 0)
        for name, count in _GUARD_COUNTERS.items()
        if count - before.get(name, 0) > 0
    }


# ──────────────────────────────────────────────
# SMQ 중간표현 (gold_smq 계약과 일치)
# ──────────────────────────────────────────────

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


# ──────────────────────────────────────────────
# 시맨틱 모델 로더
# ──────────────────────────────────────────────

_MODEL_CACHE: dict[str, Optional[dict]] = {}


def load_semantic_model(db_id: str, *, use_cache: bool = True) -> Optional[dict]:
    """시맨틱 모델(dimension/measure 카탈로그)을 지식 정본에서 만들어 반환한다(없으면 None).

    원천 우선순위(Plan 67 R1 — 지식 정본 일원화):
        1. **정본**: 구조 선언 ``config/db_profiles/{db_id}.yaml`` + 큐레이션
           ``config/knowledge/{db_id}/catalog.yaml`` → ``build_catalog``로 생성.
           큐레이션이 없는 DB는 카탈로그를 만들지 않는다(선별되지 않은 컬럼이 결정적 조립
           대상으로 새어 들어가는 것을 막는다).
        2. **사본 폴백**: 기존 ``config/semantic_models/{db_id}.yaml``. 정본 생성 실패 시에만 쓴다.

    두 원천의 산출물이 동등함은 ``scripts/catalog_diff.py``가 실측한다(전환 시점 차이 0).
    db_engine/db_schema는 모델에 없고 domain_config에서 주입한다(D-066 후속6 단일 출처).
    """
    if use_cache and db_id in _MODEL_CACHE:
        return _MODEL_CACHE[db_id]

    from src.schema_cache.catalog_builder import (
        build_catalog,
        load_knowledge_overrides,
        load_structure_profile,
    )

    model: Optional[dict] = None
    overrides = load_knowledge_overrides(db_id)
    if overrides:
        structure = load_structure_profile(overrides.get("structure_from") or db_id)
        if structure:
            model = build_catalog(structure, db_id=db_id, overrides=overrides)
            logger.debug("시맨틱 모델 원천=지식 정본 (db_id=%s)", db_id)

    if model is None:
        path = os.path.join("config", "semantic_models", f"{db_id}.yaml")
        if os.path.exists(path):
            try:
                import yaml
                with open(path, "r", encoding="utf-8") as f:
                    model = yaml.safe_load(f)
                # 정본에서 생성하지 못하고 사본으로 강등된 상태 — 침묵 폴백 금지(사유 가시화).
                logger.warning(
                    "시맨틱 모델을 지식 정본에서 생성하지 못해 사본으로 강등 (db_id=%s, 사본=%s) "
                    "— config/db_profiles·config/knowledge 확인 필요",
                    db_id, path,
                )
            except Exception as e:  # noqa: BLE001 — 로드 실패는 커버리지 밖으로 graceful 강등
                logger.warning("시맨틱 모델 로드 실패 (%s): %s", path, e)
                model = None

    if use_cache:
        _MODEL_CACHE[db_id] = model
    return model


def _dimension_index(pattern_a: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    """패턴 A dimension 인덱스를 (정확이름, 소문자별칭) 두 맵으로 만든다.

    ``Model``(server.Server)과 ``MODEL``(server.Cpus)처럼 대소문자만 다른 속성이 공존하므로,
    소문자 단일 인덱스로 합치면 충돌한다. SMQ dimension 토큰은 카탈로그의 정확한 이름을 쓰므로
    **정확이름(대소문자 구분) 우선** 매칭하고, 소문자 별칭 맵은 관용 폴백으로만 쓴다.

    계층 taxonomy(N4/D-133): **형제 둘 이상이 부모(상위어) 이름을 자기 별칭으로 주장하면 그
    별칭을 등록하지 않는다.** 선착순(``setdefault``)으로 하나가 조용히 이기면 평면 동의어
    사전의 precision 붕괴가 그대로 재현되기 때문이다 — 등록하지 않으면 상위어 토큰이 모호한
    채로 남아 모호성 처리(``_expand_hypernym_ambiguity``)나 커버리지 사유로 드러난다.
    주장하는 형제가 하나뿐이면 큐레이션이 그 하나로 결속한 것으로 보고 유지한다(결속 해제는
    기존 ``alias_deny`` 수단 — 현행 카탈로그는 이 경우뿐이라 규칙 발동 0건).
    """
    dims = pattern_a.get("dimensions", []) or []
    contested = _contested_parent_aliases(dims)
    by_name: dict[str, dict] = {}
    by_alias: dict[str, dict] = {}
    for dim in dims:
        name = dim.get("name")
        if not name:
            continue
        parent = str(dim.get("parent") or "").strip().lower()
        by_name[name] = dim
        by_alias.setdefault(name.lower(), dim)
        for alias in dim.get("aliases", []) or []:
            if parent in contested and str(alias).strip().lower() == parent:
                continue
            by_alias.setdefault(alias.lower(), dim)
    return by_name, by_alias


def _contested_parent_aliases(dimensions: list) -> set[str]:
    """둘 이상의 형제가 자기 별칭으로 주장하는 상위어 이름 집합(소문자)."""
    claims: dict[str, int] = {}
    for dim in dimensions:
        parent = str(dim.get("parent") or "").strip().lower()
        if not parent:
            continue
        if any(str(a).strip().lower() == parent for a in dim.get("aliases") or []):
            claims[parent] = claims.get(parent, 0) + 1
    return {parent for parent, count in claims.items() if count > 1}


def _resolve_dim(token: str, index: tuple[dict[str, dict], dict[str, dict]]) -> Optional[dict]:
    """dimension 토큰을 정확이름(대소문자 구분) → 소문자 별칭 순으로 해소한다."""
    by_name, by_alias = index
    if token in by_name:
        return by_name[token]
    return by_alias.get(str(token).lower())


# 서버 식별 dimension으로 인정하는 direct 컬럼 — 이 중 하나가 SELECT에 있어야 리스트 행을
# 사람이 식별할 수 있다(avail_status 등 direct라도 식별자가 아닌 컬럼은 제외).
_IDENTITY_COLUMNS = {"name", "hostname", "ipaddress"}


def _has_identity_dim(
    dimensions: list, index: tuple[dict[str, dict], dict[str, dict]]
) -> bool:
    """선택된 dimension 중 서버 식별 direct 컬럼이 있는지 판정한다."""
    for d in dimensions:
        entry = _resolve_dim(str(d), index)
        if entry and entry.get("source") == "direct" and entry.get("column") in _IDENTITY_COLUMNS:
            return True
    return False


def _measure_combos(pattern_b: dict) -> set[tuple[str, str]]:
    """패턴 B에서 허용된 (resource_type, definition_name) 조합 집합."""
    return {
        (m.get("resource_type"), m.get("definition_name"))
        for m in pattern_b.get("measures", []) or []
    }


# ──────────────────────────────────────────────
# 커버리지 판정 (E6-3 결정적 판정 + E5-2 리터럴 검증)
# ──────────────────────────────────────────────

# 패턴 A/B에서 컴파일러가 안전하게 처리 가능한 필터(그 외는 커버리지 밖 — HAVING/동적날짜 등 미지원).
_PATTERN_AB_SAFE_FILTER_FIELDS = {"resource_type"}
# 패턴 C에서 처리 가능한 필터.
_PATTERN_C_SAFE_FILTER_FIELDS = {"ALARMSEVERITY"}


def check_coverage(
    smq: SMQ,
    model: dict,
    *,
    value_index: Optional[dict[str, list[str]]] = None,
) -> CoverageResult:
    """SMQ가 시맨틱 모델로 결정적 처리 가능한지 판정한다.

    보수적 판정(과설계 방지) — 커버리지 내는 컴파일러가 **정확히** 조립할 수 있는 형태로만 한정하고,
    나머지(HAVING 서버필터·동적 날짜·집계 over 알람·LOB 속성)는 밖으로 돌려 폴백(현행 LLM)에 맡긴다.
    커버리지는 dimension 카탈로그를 사람 승인 루프(D-012)로 점진 확장한다.

    Args:
        smq: 판정 대상 SMQ
        model: 시맨틱 모델 dict
        value_index: E5-2 값 인덱스(있으면 필터 리터럴을 실측 검증)

    Returns:
        CoverageResult(covered, reason)
    """
    if not model:
        return CoverageResult(covered=False, reason="시맨틱 모델 없음")

    if smq.pattern in ("A", "B"):
        return _coverage_ab(smq, model, value_index)
    if smq.pattern == "C":
        return _coverage_c(smq, model, value_index)
    return CoverageResult(covered=False, reason=f"미지원 패턴: {smq.pattern}")


def _coverage_ab(smq: SMQ, model: dict, value_index: Optional[dict]) -> CoverageResult:
    pattern_a = model.get("pattern_a") or {}
    dim_index = _dimension_index(pattern_a)
    for dim in smq.dimensions:
        entry = _resolve_dim(str(dim), dim_index)
        if entry is None:
            return CoverageResult(covered=False, reason=f"미정의 dimension: {dim}")
        if entry.get("lob"):
            # LOB 속성(OSParameter)은 COALESCE(stringvalue, stringvalue_short)가 필요해
            # 단일 val_col 피벗으로 표현 불가 — 폴백에 위임(Known Mistakes 2026-06-10).
            return CoverageResult(covered=False, reason=f"LOB dimension 미지원: {dim}")
    combos = _measure_combos(model.get("pattern_b") or {})
    for m in smq.measures:
        if (m.resource_type, m.definition_name) not in combos:
            return CoverageResult(
                covered=False,
                reason=f"미정의 measure: {m.resource_type}/{m.definition_name}",
            )
        if m.agg not in _AGG_FN:
            return CoverageResult(covered=False, reason=f"미지원 집계: {m.agg}")
    for f in smq.filters:
        reason = _filter_reason_ab(f, smq, model, dim_index)
        if reason:
            return CoverageResult(covered=False, reason=reason)
    shape = _shape_reason_ab(smq, model, dim_index)
    if shape:
        return CoverageResult(covered=False, reason=shape)
    lit = _validate_literals(smq, model, value_index)
    if lit:
        return CoverageResult(covered=False, reason=lit)
    if not smq.dimensions and not smq.measures and not smq.entity_count:
        return CoverageResult(covered=False, reason="dimension/measure 없음")
    ir = _ir_common_reason(smq, lambda: _resolve_ir_order_by_ab(smq, dim_index))
    if ir:
        return CoverageResult(covered=False, reason=ir)
    return CoverageResult(covered=True)


def _safe_filter_fields_ab(model: dict) -> set[str]:
    """패턴 A/B에서 필터로 허용할 필드 집합 — 카탈로그 ``filterable`` 선언을 정본으로 쓴다.

    S-IR4: 코드 상수 1개(resource_type)와 YAML 선언 5개의 불일치가 "선언 커버리지 76.9% vs
    런타임 판정 34.6%" 격차의 구조적 원인이었다(계획서 §2.5 한계 3). 선언을 정본으로 삼되,
    선언이 없는 모델은 기존 상수로 폴백한다. 선언돼 있어도 **컴파일러가 실제로 조립할 수
    있는 형태**(direct 컬럼 + 지원 op)만 통과한다 — 통과시키고 조립에서 빠뜨리면 조건 없는
    SQL이 나가므로, 조립 불가는 반드시 커버리지 밖이다.
    """
    declared = {str(f) for f in ((model.get("pattern_a") or {}).get("filterable") or [])}
    return (declared | _PATTERN_AB_SAFE_FILTER_FIELDS) if declared else set(
        _PATTERN_AB_SAFE_FILTER_FIELDS
    )


def _filter_reason_ab(
    f: SMQFilter, smq: SMQ, model: dict, dim_index: tuple[dict, dict]
) -> Optional[str]:
    """패턴 A/B 필터 1건이 조립 불가인 사유를 돌려준다(조립 가능하면 None)."""
    kind = _classify_filter_ab(f, smq, model, dim_index)
    if kind:
        return None
    if _measure_by_ref(smq, f.field) is not None:
        return f"미지원 측정치 임계 op(집계 비교만 가능): {f.field} {f.op}"
    if f.field in _safe_filter_fields_ab(model):
        return f"미지원 필터 형태(direct 컬럼·지원 op만): {f.field} {f.op}"
    return f"미지원 필터(서버필터/동적조건은 폴백): {f.field}"


def _classify_filter_ab(
    f: SMQFilter, smq: SMQ, model: dict, dim_index: tuple[dict, dict]
) -> str:
    """필터를 컴파일 가능한 종류로 분류한다 — resource_type|measure|direct, 불가하면 빈 문자열."""
    if f.field == "resource_type":
        # 대상 resource_type은 dimension·measure 선택에서 결정적으로 도출되므로 조립에 쓰지
        # 않는다(기존 동작 유지 — 조립 시 무시 사실을 계측·로그로 가시화한다).
        return "resource_type"
    if _measure_by_ref(smq, f.field) is not None:
        return "measure" if str(f.op).lower() in _MEASURE_FILTER_OPS else ""
    if f.field not in _safe_filter_fields_ab(model):
        return ""
    entry = _resolve_dim(str(f.field), dim_index)
    if entry is None or entry.get("source") != "direct":
        # EAV 속성 필터는 피벗 후 HAVING 대상이 아니라 값 컬럼 조건이라 폴백에 위임.
        return ""
    op = str(f.op).lower()
    if op not in _FILTER_SQL_OPS:
        return ""
    if op == "in" and not isinstance(f.value, (list, tuple)):
        return ""
    if op != "in" and isinstance(f.value, (list, tuple, dict)):
        return ""
    return "direct"


def _measure_by_ref(smq: SMQ, ref: str) -> Optional[SMQMeasure]:
    """참조 문자열(measure alias 또는 resource_type)로 선택된 measure를 찾는다."""
    token = str(ref or "").strip().lower()
    if not token:
        return None
    for m in smq.measures:
        if token in (m.alias.lower(), m.resource_type.lower()):
            return m
    return None


def _shape_reason_ab(smq: SMQ, model: dict, dim_index: tuple[dict, dict]) -> Optional[str]:
    """S-IR1/2 형태 확장(전역 집계·기간별 분해)의 조립 가능 조건을 판정한다."""
    if smq.global_aggregate:
        if smq.time_breakdown:
            return "전역 집계와 기간별 분해는 동시 지원 불가"
        if smq.dimensions:
            return "전역 집계는 dimension 불가(단일 값 집계)"
        if not smq.measures and not smq.entity_count:
            return "전역 집계에 집계 대상(measure/entity_count) 없음"
    elif smq.entity_count:
        return "엔티티 수 집계는 전역 집계(global_aggregate)에서만 지원"
    if smq.entity_count:
        # 세는 대상은 엔티티(서버) 행뿐이다 — 자식 리소스 수를 요청했는데 엔티티 수를 돌려주면
        # 조용한 오답이 된다.
        entity_rt = _entity_resource_type(model.get("pattern_a") or {})
        others = [
            rt for rt in smq.resource_types
            if entity_rt and str(rt).lower() != entity_rt.lower()
        ]
        if others:
            return f"엔티티 외 리소스 수 집계 미지원: {', '.join(map(str, others))}"
    if smq.time_range and not smq.measures:
        # 기간을 적용할 통계 조인이 없으면 조건이 사라진다 — 기간 질의는 폴백에 맡긴다.
        return "기간 조건을 적용할 measure 없음(설정 조회에 기간 미지원)"
    if smq.time_breakdown:
        if not smq.measures:
            return "기간별 행 분해는 measure 필요(통계 기간 기준 분해)"
        for dim in smq.dimensions:
            entry = _resolve_dim(str(dim), dim_index)
            if entry is None or entry.get("source") != "direct":
                # 기간이 GROUP BY에 들어가면 EAV 속성 행이 다른 그룹으로 갈려 NULL이 된다.
                return f"기간별 분해는 EAV 속성 dimension 미지원: {dim}"
    return None


def _ir_common_reason(smq: SMQ, resolve_order) -> Optional[str]:
    """패턴 공통 IR 확장(order_by·limit·time_range)의 형식·해소 가능성을 판정한다."""
    if smq.limit is not None and not (0 < int(smq.limit) <= _MAX_IR_LIMIT):
        return f"IR limit 범위 밖(1~{_MAX_IR_LIMIT}): {smq.limit}"
    if smq.time_range is not None:
        if not smq.time_range or len(smq.time_range) > 2:
            return f"IR time_range 형식 오류(YYYYMM 1~2개): {smq.time_range}"
        for ym in smq.time_range:
            if not _YYYYMM_RE.fullmatch(str(ym)):
                return f"IR time_range 형식 오류(YYYYMM 아님): {ym}"
    if smq.order_by is not None:
        if str(smq.order_by.direction).lower() not in ("asc", "desc"):
            return f"IR order_by 방향 미지원: {smq.order_by.direction}"
        if resolve_order() is None:
            return f"IR order_by 대상 미해소(카탈로그·선택 밖): {smq.order_by.field}"
    return None


def _coverage_c(smq: SMQ, model: dict, value_index: Optional[dict]) -> CoverageResult:
    pattern_c = model.get("pattern_c") or {}
    known_entities = {e.upper() for e in (pattern_c.get("entities") or {}).keys()}
    for ent in smq.entities:
        if str(ent).upper() not in known_entities:
            return CoverageResult(covered=False, reason=f"미정의 알람 엔터티: {ent}")
    dim_map = {k.upper(): v for k, v in (pattern_c.get("dimensions") or {}).items()}
    for dim in smq.dimensions:
        if str(dim).upper() not in dim_map:
            return CoverageResult(covered=False, reason=f"미정의 알람 dimension: {dim}")
    for f in smq.filters:
        if f.field not in _PATTERN_C_SAFE_FILTER_FIELDS:
            # 기간은 IR time_range로 승격된 것만 처리한다(원시 CTIME 조건은 폴백).
            return CoverageResult(
                covered=False,
                reason=f"미지원 알람 필터(기간/집계는 폴백): {f.field}",
            )
    if smq.global_aggregate or smq.time_breakdown:
        return CoverageResult(
            covered=False, reason="알람은 전역 집계·기간별 분해 형태 미지원",
        )
    if smq.time_range and "CTIME" not in dim_map:
        # 시각 컬럼 표현이 카탈로그에 없으면 기간 조건을 조립할 수 없다(조건 누락 SQL 금지).
        return CoverageResult(
            covered=False, reason="알람 기간 조건 조립 불가(카탈로그에 CTIME 없음)",
        )
    ir = _ir_common_reason(smq, lambda: _resolve_ir_order_by_c(smq, dim_map))
    if ir:
        return CoverageResult(covered=False, reason=ir)
    return CoverageResult(covered=True)


def _validate_literals(
    smq: SMQ, model: dict, value_index: Optional[dict]
) -> Optional[str]:
    """E5-2 값 검색 연결 — 필터 리터럴이 실측 값집합에 존재하는지 검증한다(있을 때만).

    컴파일러가 emit하는 리터럴은 전부 시맨틱 모델 정의값이라 구조적 환각은 불가능하지만,
    SMQ 필터가 특정 실측 값(예: resource_type='server.Xyz')을 참조하면 value_index로 검증해
    미검증 값은 커버리지 밖으로 돌린다(리터럴 환각(Plan 25 유형)의 컴파일러 우회 방지).
    value_index가 없으면(플래그 OFF) 검증을 건너뛴다.

    Returns:
        검증 실패 사유 문자열, 통과 시 None.
    """
    if not value_index:
        return None
    for f in smq.filters:
        if f.op in ("like",):
            continue
        # 값 인덱스가 그 필드를 실제로 수집했을 때만 검증한다("가용 시" 게이트 — 미수집 필드를
        # 미검증으로 몰면 정상 필터가 전부 폴백으로 새 나간다).
        candidates = value_index.get(f.field) or []
        if not candidates:
            continue
        values = f.value if isinstance(f.value, (list, tuple)) else [f.value]
        for val in values:
            if isinstance(val, str) and val not in candidates:
                return f"미검증 리터럴(value_index 부재): {f.field}={val}"
    return None


# ──────────────────────────────────────────────
# 결정적 컴파일 (SMQ → SQL)
# ──────────────────────────────────────────────

def compile_smq(
    smq: SMQ,
    db_id: str,
    model: Optional[dict] = None,
    *,
    user_query: str = "",
    default_limit: int = 100,
    stat_month: StatMonth = None,
    server_scope: Optional[tuple[str, list[str]]] = None,
) -> str:
    """SMQ를 방언별 SQL로 결정적 컴파일한다(패턴 A/B는 기존 엔진 재사용, C는 알람 조립).

    Args:
        smq: 컴파일 대상 SMQ(커버리지 내로 판정된 것)
        db_id: 대상 DB 식별자(엔진·스키마 결정용)
        model: 시맨틱 모델(없으면 로드)
        user_query: 원문 질의("전체/모든" LIMIT 상향 판단용)
        default_limit: 기본 LIMIT
        stat_month: 성능지표 기간 필터 — 단일 월 YYYYMM 또는 (시작, 끝) 범위(패턴 B, D-102)
        server_scope: 선행 task 결과 서버 한정 (식별컬럼, 값목록) — 패턴 A/B에 HAVING으로
            결정적 적용(D-099). None이면 미적용.

    Returns:
        실행 가능한 SQL 문자열(세미콜론 종결)
    """
    if model is None:
        model = load_semantic_model(db_id)
    if not model:
        raise ValueError(f"시맨틱 모델 없음: {db_id}")

    domain = get_domain_by_id(db_id)
    db_engine = domain.db_engine if domain else "postgresql"
    db_schema = domain.db_schema if domain else ""
    # IR limit(S-IR3)이 있으면 표면어 해석(resolve_query_limit)보다 우선한다 — 표면어 파싱은
    # IR 부재 시의 폴백으로 남긴다(R3 원칙: 정규식 제거가 아니라 강등).
    if smq.limit:
        limit = int(smq.limit)
        note_guard(GUARD_IR_LIMIT, f"limit={limit}")
    else:
        limit = resolve_query_limit(user_query, default_limit)
    # 호출부가 결정적으로 해석한 기간이 우선이고, 없을 때만 IR 기간을 쓴다(D-035 결정적 우선).
    if stat_month is None and smq.time_range:
        stat_month = _stat_month_from_ir(smq.time_range)
        note_guard(GUARD_IR_TIME_RANGE, f"time_range={smq.time_range}")

    if smq.pattern in ("A", "B"):
        return _compile_ab(
            smq, model, db_engine, db_schema, limit, stat_month,
            server_scope=server_scope, user_query=user_query,
        )
    if smq.pattern == "C":
        return _compile_c(smq, model, db_id, limit, db_engine=db_engine)
    raise ValueError(f"미지원 패턴: {smq.pattern}")


def _stat_month_from_ir(time_range: list[str]) -> StatMonth:
    """IR time_range([YYYYMM] 또는 [시작, 끝])를 조립기 stat_month 형식으로 바꾼다."""
    months = [str(m) for m in time_range if _YYYYMM_RE.fullmatch(str(m))]
    if not months:
        return None
    if len(months) == 1:
        return months[0]
    return (min(months), max(months))


def _compile_ab(
    smq: SMQ,
    model: dict,
    db_engine: str,
    db_schema: str,
    limit: int,
    stat_month: StatMonth,
    *,
    server_scope: Optional[tuple[str, list[str]]] = None,
    user_query: str = "",
) -> str:
    """패턴 A(서버설정)+B(성능지표)를 build_multi_resource_pivot_sql로 조립한다(D-067 재사용).

    dimension을 direct(cmm_resource 컬럼)/server_eav/child_eav로 나누고, measure를
    explicit_measures로 넘긴다 — resource_type 구분 CASE WHEN + 단일 GROUP BY(서버당 1행).
    """
    pattern_a = model.get("pattern_a") or {}
    pattern_b = model.get("pattern_b") or {}
    eav_pattern = pattern_a.get("eav") or {}
    dim_index = _dimension_index(pattern_a)

    # measure가 있는데 서버 식별 dimension(name/hostname/ipaddress direct 컬럼)이 하나도 없으면
    # 결과 행을 식별할 수 없다 — 실측상 LLM SMQ가 자주 범하는 선택 누락으로, dimension이 완전히
    # 빈 경우뿐 아니라 속성(용량 등)만 고른 경우에도 발생한다(2026-07-21 yd-004: 서버명 없는
    # 사용률 리스트). 프롬프트 유도 대신 모델 pattern_b.default_dimensions를 결정적으로
    # 앞에 주입한다(D-035, D-076 후속).
    dimensions = list(smq.dimensions)
    # 전역 집계(S-IR1)는 식별 컬럼 자체가 없어야 단일 값이 나오므로 주입 대상이 아니다.
    if smq.measures and not smq.global_aggregate and not _has_identity_dim(dimensions, dim_index):
        chosen = {
            e["name"] for d in dimensions
            if (e := _resolve_dim(str(d), dim_index)) is not None
        }
        defaults = [
            d for d in (pattern_b.get("default_dimensions") or [])
            if (e := _resolve_dim(str(d), dim_index)) is not None and e["name"] not in chosen
        ]
        dimensions = defaults + dimensions
    # 선행 스코프가 있으면 그 식별 컬럼(name/hostname)을 SELECT에 결정적으로 포함한다 —
    # 없으면 결과 행이 어느 서버의 값인지 알 수 없다(D-097).
    if server_scope and server_scope[1]:
        scope_col = server_scope[0]
        if not any(
            (_resolve_dim(str(d), dim_index) or {}).get("column") == scope_col
            for d in dimensions
        ):
            if _resolve_dim(scope_col, dim_index) is not None:
                dimensions = [scope_col] + dimensions

    regular_entries: list[tuple[str, str]] = []
    server_eav: list[tuple[str, str]] = []
    child_eav: list[tuple[str, str, str]] = []
    entity_table = eav_pattern.get("entity_table", "cmm_resource")
    for dim in dimensions:
        entry = _resolve_dim(str(dim), dim_index)
        name = entry["name"]
        if entry.get("source") == "direct":
            regular_entries.append((name, f"{entity_table}.{entry['column']}"))
        elif (entry.get("resource_type") or "server.Server") == "server.Server":
            server_eav.append((name, entry["attribute"]))
        else:
            child_eav.append((name, entry["attribute"], entry["resource_type"]))

    value_columns = pattern_b.get("value_columns") or {"avg": "avg_val", "max": "max_val", "min": "min_val"}
    explicit_measures: list[tuple[str, str, str, str, str]] = []
    for m in smq.measures:
        val_col = value_columns.get(m.agg.lower(), "avg_val")
        explicit_measures.append(
            (m.alias, m.resource_type, _AGG_FN[m.agg.lower()], val_col, m.definition_name)
        )

    metric_tables = pattern_b.get("metric_tables") or {}
    grain = smq.time_grain or pattern_b.get("default_time_grain", "month")
    metric_table = metric_tables.get(grain, "cmm_metric_stat_m")

    # 정렬은 IR(S-IR3) 우선, 없으면 표면어("가장 높은/최고") 폴백(NULLS LAST는 조립기 — D-098).
    order_by = _resolve_ir_order_by_ab(smq, dim_index) if smq.order_by else None
    if order_by is not None:
        note_guard(GUARD_IR_ORDER_BY, f"{order_by[0]} {order_by[1]}")
        # 최상급 어휘가 있고 상한을 지정하지 않았으면 상위 1건 유지(D-100).
        if smq.limit is None and _is_superlative(user_query):
            limit = 1
    else:
        order_by = _resolve_ranking(user_query, explicit_measures)
        # 최상급 순위("가장 높은/낮은")는 상위 1건만 — 결정적 조립이 default_limit로 전체를 반환하면
        # 병합 시 "가장 높은 서버"가 아닌 대상 전체가 남는다(D-100 실측: 2건 반환).
        if order_by:
            note_guard(GUARD_RANKING_SURFACE, f"{order_by[0]} {order_by[1]}")
            if smq.limit is None:
                limit = 1

    direct_having, measure_having = _compile_filters_ab(smq, model, dim_index)

    return build_multi_resource_pivot_sql(
        regular_entries, server_eav, child_eav, eav_pattern,
        db_engine=db_engine, db_schema=db_schema, limit=limit,
        stat_month=stat_month, metric_table=metric_table,
        explicit_measures=explicit_measures or None,
        server_scope=server_scope,
        order_by=order_by,
        time_breakdown=smq.time_breakdown,
        global_aggregate=smq.global_aggregate,
        entity_count_alias=_entity_count_alias(pattern_a) if smq.entity_count else None,
        direct_having=direct_having or None,
        measure_having=measure_having or None,
    )


def _entity_resource_type(pattern_a: dict) -> str:
    """카탈로그가 말하는 엔티티(서버) resource_type을 얻는다(없으면 빈 문자열).

    선언(``entity_resource_type``)이 우선이고, 없는 카탈로그는 direct dimension에 찍힌
    resource_type에서 읽는다 — 컴파일러에 엔티티 이름을 하드코딩하지 않기 위함(D-088).
    """
    rt = str(pattern_a.get("entity_resource_type") or "")
    if rt:
        return rt
    return next(
        (
            str(d.get("resource_type") or "")
            for d in (pattern_a.get("dimensions") or [])
            if d.get("source") == "direct" and d.get("resource_type")
        ),
        "",
    )


def _entity_count_alias(pattern_a: dict) -> str:
    """엔티티 수 집계 컬럼 alias를 엔티티 resource_type에서 만든다(예: server.Server → server_count)."""
    short = _entity_resource_type(pattern_a).split(".")[-1].lower()
    return f"{short}_count" if short else "entity_count"


def _compile_filters_ab(
    smq: SMQ, model: dict, dim_index: tuple[dict, dict]
) -> tuple[list[tuple[str, str, Any]], list[tuple[str, str, Any]]]:
    """패턴 A/B 필터를 조립기 HAVING 인자로 변환한다 (S-IR4).

    서버 식별·상태 direct 컬럼은 집계 후 HAVING으로(WHERE에 두면 자식 리소스 행이 GROUP BY
    전에 탈락 — D-096), 측정치 임계는 SELECT와 동일한 집계식으로 건다.

    Returns:
        (direct_having, measure_having) — 각각 [(컬럼|alias, SQL 연산자, 값)]
    """
    direct_having: list[tuple[str, str, Any]] = []
    measure_having: list[tuple[str, str, Any]] = []
    for f in smq.filters:
        kind = _classify_filter_ab(f, smq, model, dim_index)
        op = _FILTER_SQL_OPS.get(str(f.op).lower(), "=")
        if kind == "measure":
            measure = _measure_by_ref(smq, f.field)
            if measure is not None:
                measure_having.append((measure.alias, op, f.value))
        elif kind == "direct":
            entry = _resolve_dim(str(f.field), dim_index) or {}
            direct_having.append((entry.get("column") or str(f.field), op, f.value))
        elif kind == "resource_type":
            # 조립 대상 resource_type은 선택(dimension/measure)에서 도출하므로 이 필터는 쓰지
            # 않는다. 무시 사실을 계측해 R4 축소 판단 재료로 남긴다(침묵 무시 금지).
            note_guard(GUARD_RESOURCE_TYPE_FILTER_IGNORED, f"{f.field} {f.op} {f.value}")
    return direct_having, measure_having


# 순위(최상급) 어휘 — 방향별. 결정적 정렬 판단용(D-099).
_RANK_DESC_MARKERS = ("가장 높", "가장 많", "최고", "최대", "제일 높", "highest", "top")
_RANK_ASC_MARKERS = ("가장 낮", "가장 적", "최저", "최소", "제일 낮", "lowest")


def _resolve_ranking(
    user_query: str,
    explicit_measures: list[tuple[str, str, str, str, str]],
) -> Optional[tuple[str, str]]:
    """질의의 최상급 어휘로 정렬 대상(measure alias)과 방향을 결정한다 (D-099).

    measure가 없으면(순위 기준 없음) None. 최상급 어휘가 없어도 None(기존 무정렬 유지).

    Args:
        user_query: 원문 질의
        explicit_measures: (alias, resource_type, agg_fn, val_col, definition_name) 목록

    Returns:
        (alias, "DESC"|"ASC") 또는 None
    """
    if not explicit_measures:
        return None
    low = (user_query or "").lower()
    if any(m in low for m in _RANK_DESC_MARKERS):
        return explicit_measures[0][0], "DESC"
    if any(m in low for m in _RANK_ASC_MARKERS):
        return explicit_measures[0][0], "ASC"
    return None


def _is_superlative(user_query: str) -> bool:
    """질의에 최상급 어휘가 있는지 판정한다(상위 1건 축약 판단 — D-100)."""
    low = (user_query or "").lower()
    return any(m in low for m in _RANK_DESC_MARKERS + _RANK_ASC_MARKERS)


def _order_direction(order_by: SMQOrderBy) -> str:
    """IR 정렬 방향을 SQL 키워드로 바꾼다(기본 DESC)."""
    return "ASC" if str(order_by.direction).lower() == "asc" else "DESC"


def _resolve_ir_order_by_ab(
    smq: SMQ, dim_index: tuple[dict, dict]
) -> Optional[tuple[str, str]]:
    """패턴 A/B의 IR order_by를 (SELECT alias, 방향)으로 해소한다 (S-IR3).

    해소 순서는 measure(alias·resource_type) → dimension 카탈로그 이름이다. 어느 것과도
    맞지 않으면 None — 커버리지 판정이 이를 밖으로 돌린다(임의 컬럼 정렬 금지).
    엔티티 수 집계는 전역 단일 행이라 정렬 대상이 아니다.
    """
    if smq.order_by is None:
        return None
    direction = _order_direction(smq.order_by)
    field = str(smq.order_by.field).strip()
    measure = _measure_by_ref(smq, field)
    if measure is not None:
        return measure.alias, direction
    entry = _resolve_dim(field, dim_index)
    if entry is not None:
        return entry["name"], direction
    return None


def _resolve_ir_order_by_c(
    smq: SMQ, dim_map: dict[str, str]
) -> Optional[tuple[str, str]]:
    """패턴 C의 IR order_by를 (SELECT alias, 방향)으로 해소한다 (S-IR5).

    건수 집계 alias와 카탈로그 알람 dimension(그 SELECT alias)만 허용한다.
    """
    if smq.order_by is None:
        return None
    direction = _order_direction(smq.order_by)
    field = str(smq.order_by.field).strip()
    if smq.entity_count and field.lower() == _ALARM_COUNT_ALIAS:
        return _ALARM_COUNT_ALIAS, direction
    key = field.upper()
    if key in dim_map:
        return _ALARM_DIM_ALIAS.get(key, field.lower()), direction
    # SELECT alias(예: server_name)로 지정한 경우도 받아들인다.
    for dim_key in dim_map:
        if _ALARM_DIM_ALIAS.get(dim_key, dim_key.lower()) == field.lower():
            return _ALARM_DIM_ALIAS.get(dim_key, dim_key.lower()), direction
    return None


# 알람 조회에 항상 포함할 선별 근거 dimension (카탈로그 키, D-100).
# 서버 식별(병합 키) + 알람명 + 심각도 — "심각 알람이 있는 서버" 선별 조건을 결과에 표시.
_ALARM_CONTEXT_DIMS = ("server_name", "NAME", "ALARMSEVERITY")
# 표시·병합 친화 alias (기본 lower() 대신 명시). server_name은 병합 canonical 키와 일치.
_ALARM_DIM_ALIAS = {
    "SERVER_NAME": "server_name",
    "NAME": "alarm_name",
    "ALARMSEVERITY": "severity",
}


def _compile_c(
    smq: SMQ, model: dict, db_id: str, limit: int, *, db_engine: str = ""
) -> str:
    """패턴 C(알람)를 정규화 조인으로 결정적 조립한다.

    CMM_ALARM(CA)↔CMM_ALARM_DEF(D)↔CMM_ALARM_ACTIVE(A)↔CMM_RESOURCE(CR). 활성 알람은 A 조인 +
    severity IN (1,2,3), 이력은 A 미조인 + IN (0,1,2,3). severity 명시 필터가 있으면 그 값 사용.
    조인 조건·컬럼은 시맨틱 모델(검증된 골드 SQL 유래)에서만 가져와 환각 불가.

    ``entity_count``가 켜지면 알람 건수 집계 형태로 조립한다(S-IR5) — 요청 dimension만
    GROUP BY하고 COUNT(*)를 얹으며, 정렬은 IR order_by(없으면 건수 내림차순)로 결정한다.
    """
    pattern_c = model.get("pattern_c") or {}
    prefix = get_schema_prefix(db_id)
    dim_map = {k.upper(): v for k, v in (pattern_c.get("dimensions") or {}).items()}
    engine = db_engine or (
        get_domain_by_id(db_id).db_engine if get_domain_by_id(db_id) else "postgresql"
    )
    counting = bool(smq.entity_count)

    # SELECT — 알람 선별 근거(서버명·알람명·심각도)를 결정적으로 앞에 포함하고, 그 뒤 요청
    # dimension을 잇는다(D-100). "심각 알람이 있는 서버" 같은 선별 조건이 최종 결과 표에
    # 함께 나타나도록 하기 위함 — 오케스트레이터가 sub_query를 "서버 목록 조회"로 좁혀
    # dimensions=[server_name]만 골라도 알람명·심각도가 소실되지 않는다. 카탈로그에 정의된
    # dimension만 사용하므로 환각 불가. alias는 표시·병합 친화적으로 명시 지정한다.
    if counting:
        # 건수 집계에서는 선별 근거를 덧붙이면 그룹이 쪼개져 "서버별 건수"가 아니게 된다 —
        # 요청 dimension만 GROUP BY 키로 쓴다.
        ordered_dims = [str(d) for d in smq.dimensions]
    else:
        base_context = [d for d in _ALARM_CONTEXT_DIMS if d.upper() in dim_map]
        base_keys = {d.upper() for d in base_context}
        ordered_dims = base_context + [
            str(d) for d in smq.dimensions if str(d).upper() not in base_keys
        ]
    select_lines: list[str] = []
    group_exprs: list[str] = []
    seen_dims: set[str] = set()
    for d in ordered_dims:
        key = str(d).upper()
        if key not in dim_map or key in seen_dims:
            continue
        seen_dims.add(key)
        alias = _ALARM_DIM_ALIAS.get(key, str(d).lower())
        select_lines.append(f"  {dim_map[key]} AS {alias}")
        group_exprs.append(dim_map[key])
    if counting:
        select_lines.append(f"  COUNT(*) AS {_ALARM_COUNT_ALIAS}")
    if not select_lines:
        select_lines = ["  CA.ALARMSEVERITY AS severity", "  CA.CTIME AS ctime"]

    base = pattern_c.get("base_table") or {"table": "cmm_resource", "alias": "CR"}
    from_clause = f"FROM {prefix}{base['table']} {base['alias']}"

    join_lines: list[str] = []
    joins = pattern_c.get("joins") or {}
    for jsql in joins.values():
        join_lines.append(jsql.format(p=prefix))
    if smq.active_only and pattern_c.get("active_join"):
        join_lines.append(pattern_c["active_join"].format(p=prefix))

    where_parts = list(pattern_c.get("base_where") or [])
    sev_filter = next((f for f in smq.filters if f.field == "ALARMSEVERITY"), None)
    if sev_filter is not None:
        if sev_filter.op == "in" and isinstance(sev_filter.value, (list, tuple)):
            vals = ", ".join(str(int(v)) for v in sev_filter.value)
            where_parts.append(f"CA.ALARMSEVERITY IN ({vals})")
        else:
            where_parts.append(f"CA.ALARMSEVERITY = {int(sev_filter.value)}")
    elif smq.active_only:
        where_parts.append("CA.ALARMSEVERITY IN (1, 2, 3)")
    else:
        where_parts.append("CA.ALARMSEVERITY IN (0, 1, 2, 3)")
    # 기간은 IR time_range로 승격된 것만 결정적 창으로 적용한다(S-IR4/5).
    where_parts.extend(_alarm_time_where(smq.time_range, dim_map))

    sql = "SELECT\n" + ",\n".join(select_lines) + "\n" + from_clause
    if join_lines:
        sql += "\n" + "\n".join(join_lines)
    sql += "\nWHERE " + "\n  AND ".join(where_parts)
    if counting and group_exprs:
        sql += "\nGROUP BY " + ", ".join(group_exprs)
    order_by = _order_clause_c(smq, dim_map, counting) or pattern_c.get("order_by")
    if order_by:
        sql += f"\nORDER BY {order_by}"
    if limit:
        if (engine or "").lower() == "db2":
            sql += f"\nFETCH FIRST {limit} ROWS ONLY"
        else:
            sql += f"\nLIMIT {limit}"
    return sql + ";"


def _order_clause_c(
    smq: SMQ, dim_map: dict[str, str], counting: bool
) -> Optional[str]:
    """패턴 C의 ORDER BY 절을 IR·집계 형태로 결정한다(없으면 None → 모델 기본 정렬)."""
    # NULLS LAST는 항상 부여한다 — 값 없는 행이 정렬 선두를 차지하는 것을 막고(D-098),
    # 어댑터 검증(집계 내림차순 + 행 제한 시 NULLS LAST 요구)도 이 규칙을 강제한다.
    ir = _resolve_ir_order_by_c(smq, dim_map)
    if ir is not None:
        note_guard(GUARD_IR_ORDER_BY, f"{ir[0]} {ir[1]}")
        return f"{ir[0]} {ir[1]} NULLS LAST"
    if counting:
        # 건수 집계는 모델 기본 정렬(발생시각)이 GROUP BY 밖 컬럼이라 쓸 수 없다 — 건수 내림차순.
        return f"{_ALARM_COUNT_ALIAS} DESC NULLS LAST"
    return None


def _alarm_time_where(
    time_range: Optional[list[str]], dim_map: dict[str, str]
) -> list[str]:
    """IR 기간(YYYYMM)을 알람 발생시각 범위 조건으로 만든다(없으면 빈 목록).

    시각 컬럼 표현은 카탈로그(``CTIME`` dimension)에서 가져오고, 경계는 엔진 공통
    ``DATE('YYYY-MM-DD')`` 리터럴로 쓴다(PostgreSQL·DB2 공통 문법). 끝월의 **다음 달 1일
    미만**으로 닫아 월 경계 누락·중복을 없앤다.
    """
    if not time_range:
        return []
    col = dim_map.get("CTIME")
    if not col:
        return []
    months = sorted(str(m) for m in time_range if _YYYYMM_RE.fullmatch(str(m)))
    if not months:
        return []
    return [
        f"{col} >= DATE('{_month_first_day(months[0])}')",
        f"{col} < DATE('{_month_first_day(_next_month(months[-1]))}')",
    ]


def _month_first_day(ym: str) -> str:
    """YYYYMM을 그 달 1일의 ISO 날짜 문자열로 만든다."""
    return f"{ym[:4]}-{ym[4:6]}-01"


def _next_month(ym: str) -> str:
    """YYYYMM의 다음 달을 YYYYMM으로 만든다(연 경계 처리)."""
    year, month = int(ym[:4]), int(ym[4:6])
    return f"{year + 1}01" if month == 12 else f"{year}{month + 1:02d}"


# ──────────────────────────────────────────────
# 자연어 → SMQ (LLM 선택) + coverage_router 진입점
# ──────────────────────────────────────────────

def render_catalog(model: dict) -> str:
    """시맨틱 모델을 NL→SMQ 프롬프트용 카탈로그 텍스트로 렌더한다(선택 가능 항목만 제시)."""
    lines: list[str] = []
    pattern_a = model.get("pattern_a") or {}
    dims = pattern_a.get("dimensions") or []
    if dims:
        lines.append("■ 패턴 A 서버설정 dimensions (name — resource_type — 별칭):")
        for d in dims:
            aliases = ", ".join(d.get("aliases", []) or [])
            lob = " (LOB — 미지원)" if d.get("lob") else ""
            lines.append(f"  - {d.get('name')} [{d.get('resource_type')}]{lob}"
                         + (f" ← {aliases}" if aliases else ""))
    pattern_b = model.get("pattern_b") or {}
    measures = pattern_b.get("measures") or []
    if measures:
        lines.append("■ 패턴 B 성능지표 measures (resource_type/definition_name — 별칭):")
        for m in measures:
            aliases = ", ".join(m.get("aliases", []) or [])
            lines.append(f"  - {m.get('resource_type')} / {m.get('definition_name')}"
                         + (f" ← {aliases}" if aliases else ""))
        grains = ", ".join((pattern_b.get("metric_tables") or {}).keys())
        lines.append(f"  time_grain 옵션: {grains} (기본 month)")
        lines.append("  집계(agg): avg, max, min")
    pattern_c = model.get("pattern_c") or {}
    ents = pattern_c.get("entities") or {}
    if ents:
        lines.append("■ 패턴 C 알람 엔터티: " + ", ".join(ents.keys()))
        cdims = pattern_c.get("dimensions") or {}
        lines.append("  알람 dimensions: " + ", ".join(cdims.keys()))
        sev = pattern_c.get("severity_map") or {}
        lines.append("  severity: " + ", ".join(f"{k}={v}" for k, v in sev.items()))
    return "\n".join(lines)


# "월별/월간" 분해 질의 검출 — "3개월간"의 '월간'은 기간 표현이므로 제외(부정 후방탐색).
_MONTHLY_BREAKDOWN_RE = re.compile(r"월별|(?<!개)월간")

_PHYSICAL_HINTS = ("물리", "physical")

# 질의의 용량 표현 검출 — SMQ가 명시 요청 차원을 누락하는 비결정 보정용(2026-07-21 gp-009).
# "CPU 용량"/"CPU코어수" 직접형 + "CPU, 메모리 용량" 열거형(용량이 CPU에도 걸림)을 커버.
_CPU_CAPACITY_RE = re.compile(r"CPU\s*(?:용량|코어)|CPU\s*[,·와과및]\s*메모리\s*용량", re.IGNORECASE)
_MEM_CAPACITY_RE = re.compile(r"메모리\s*(?:용량|크기)")

#: 기간을 필터로 표현했을 때 LLM이 쓰는 필드명(실측 2026-07-30 라이브: `time` between
#: ['202606','202606']). IR에 기간 필드(time_range)가 생겼으므로 이 표기들은 결정적으로
#: 승격한다 — 프롬프트 지시만으로는 표기가 계속 흔들린다(LLM 비결정성 원칙). DB별 실제
#: 기간 컬럼명은 여기 열거하지 않고(공용 계층 무지 유지 — D-088) 값 형태로 판정한다.
_TIME_FILTER_FIELDS = {
    "time", "times", "period", "date", "dates", "month", "months",
    "stat_month", "time_range", "ctime", "occurred_at",
    "기간", "월", "날짜",
}


# ──────────────────────────────────────────────
# 계층 taxonomy — 상위어 단독 질의의 모호성 처리 (Plan 67 N4 / D-133)
# ──────────────────────────────────────────────

def _taxonomy(model: Optional[dict]) -> dict:
    """카탈로그의 상위어 블록을 얻는다(선언·정본 생성이 없으면 빈 dict)."""
    tax = (model or {}).get("taxonomy")
    return tax if isinstance(tax, dict) else {}


def _hypernym_surfaces(term: str, node: dict) -> list[str]:
    """상위어 자신과 그 표면 변형(taxonomy aliases) 목록."""
    return [str(term)] + [str(a) for a in (node.get("aliases") or [])]


def _squash(text: str) -> str:
    """비교용 정규화 — 소문자화 + 공백 제거.

    카탈로그 별칭은 띄어쓰기 변형을 다 담지 않는다(오히려 ``alias_deny``로 걷어낸다 — "물리
    코어"는 빠져 있고 "물리코어"만 남는다). 질의의 띄어쓰기는 자유로우므로 공백을 접어야
    "물리 코어"가 하위어 명시로 인식된다(미접으면 상위어 단독으로 오판해 확장이 튄다).
    """
    return re.sub(r"\s+", "", (text or "").lower())


def _mentions_any(query_squashed: str, terms: list[str]) -> bool:
    """질의에 주어진 표면어 중 하나라도 나타나는지 본다(한국어는 어절 경계가 없어 부분 문자열)."""
    return any(squashed in query_squashed for t in terms if (squashed := _squash(t)))


def _child_dim_entries(node: dict, dim_index: tuple[dict, dict]) -> list[dict]:
    """상위어의 하위 dimension 카탈로그 항목들(수록분만)."""
    return [
        entry for name in (node.get("dimensions") or [])
        if (entry := _resolve_dim(str(name), dim_index)) is not None
    ]


def _child_measure_specs(node: dict, model: dict) -> list[dict]:
    """상위어의 하위 measure 카탈로그 정의들(수록분만, 카탈로그 순서)."""
    wanted = {
        (str(c.get("resource_type")), str(c.get("definition_name")))
        for c in (node.get("measures") or []) if isinstance(c, dict)
    }
    return [
        m for m in ((model.get("pattern_b") or {}).get("measures") or [])
        if isinstance(m, dict)
        and (str(m.get("resource_type")), str(m.get("definition_name"))) in wanted
    ]


def _child_discriminators(
    term: str, node: dict, model: dict, dim_index: tuple[dict, dict]
) -> list[str]:
    """하위 항목을 구분하는 표면어(이름·별칭) 목록 — 상위어 표면어 자신은 제외한다.

    질의에 이 중 하나라도 있으면 사용자가 어느 하위어인지 이미 지목한 것이므로 모호하지
    않다(**하위어 명시 질의 동작 불변**의 판정 근거).
    """
    surfaces = {_squash(s) for s in _hypernym_surfaces(term, node)}
    out: list[str] = []
    for entry in _child_dim_entries(node, dim_index):
        out.append(str(entry.get("name") or ""))
        out.extend(str(a) for a in (entry.get("aliases") or []))
    for spec in _child_measure_specs(node, model):
        out.append(str(spec.get("definition_name") or ""))
        out.append(str(spec.get("resource_type") or ""))
        out.extend(str(a) for a in (spec.get("aliases") or []))
    return [t for t in out if t and _squash(t) not in surfaces]


def _missing_child_dims(
    smq: SMQ, node: dict, dim_index: tuple[dict, dict]
) -> list[str]:
    """선택된 하위 dimension의 빠진 형제들을 찾는다(하나도 안 골랐으면 빈 목록).

    상위어의 하위를 **하나라도** 골랐을 때만 나머지를 채운다 — 그 선택이 곧 "LLM이 모호한
    표면어를 임의의 한 갈래로 좁혔다"는 신호다. 하나도 안 골랐으면 다른 읽기(예 상위어가
    측정치를 가리킴)이므로 손대지 않는다.
    """
    children = _child_dim_entries(node, dim_index)
    if not children:
        return []
    selected = {
        entry["name"] for d in smq.dimensions
        if (entry := _resolve_dim(str(d), dim_index)) is not None
    }
    child_names = [str(e.get("name")) for e in children]
    if not (selected & set(child_names)):
        return []
    return [
        str(e.get("name")) for e in children
        # LOB 속성은 컴파일 불가라 채우면 질의 전체가 커버리지 밖으로 밀린다.
        if str(e.get("name")) not in selected and not e.get("lob")
    ]


def _missing_child_measures(smq: SMQ, node: dict, model: dict) -> list[SMQMeasure]:
    """선택된 하위 measure의 빠진 형제들을 만든다(집계는 선택된 형제와 동일하게).

    "CPU 사용률 평균·최대"를 골랐다면 형제도 평균·최대로 채운다 — 집계가 달라지면 같은
    표에 뜻이 다른 컬럼이 섞인다.
    """
    children = _child_measure_specs(node, model)
    if not children:
        return []
    child_keys = {
        (str(m.get("resource_type")), str(m.get("definition_name"))) for m in children
    }
    selected = [
        m for m in smq.measures if (m.resource_type, m.definition_name) in child_keys
    ]
    if not selected:
        return []
    aggs: list[str] = []
    for m in selected:
        if m.agg not in aggs:
            aggs.append(m.agg)
    chosen = {(m.resource_type, m.definition_name, m.agg) for m in smq.measures}
    out: list[SMQMeasure] = []
    for spec in children:
        rt = str(spec.get("resource_type"))
        dn = str(spec.get("definition_name"))
        for agg in aggs:
            if (rt, dn, agg) in chosen:
                continue
            out.append(SMQMeasure(agg=agg, definition_name=dn, resource_type=rt))
    return out


def _expand_hypernym_ambiguity(
    smq: SMQ, user_query: str, model: Optional[dict]
) -> SMQ:
    """상위어만 언급한 질의를 하위 항목 전부로 확장한다 (N4/D-133, 옵트인).

    "사용률 보여줘"처럼 상위어 단독 언급이면 어느 자원의 지표인지 결정 불가인데, 1방 선택은
    하위 하나를 임의로 골라 **조용한 오답**이 된다. 하위 전부를 제시해 모호성을 결과에
    드러낸다(계획서 §3.3-N4의 "전체 제시"). 하위어를 명시한 질의는 판정에서 걸러져 **동작
    불변**이다.

    되묻기(covered=False + 후보 나열) 대신 전체 제시를 택한 근거: ``compile_from_nl``의
    커버리지 사유는 호출부(``query_generator``)가 소비하지 않아(폴백 진입 여부만 본다)
    사용자에게 도달하지 않고, 결정적 컴파일(구조 환각 0)을 버리고 LLM 자유생성으로
    떨어뜨리게 된다. 확장 발동은 가드 카운터로 계측해 감사·평가에 남긴다.

    확장은 기존 교정 가드보다 **먼저** 돌린다 — PHYSICALCORE 선택 교정처럼 실측 운영 관행이
    확립된 가드가 최종 중재자가 되게 한다(예 "코어" 확장 후 '물리' 신호가 없으면 가드가
    PHYSICALCORE를 다시 뺀다).
    """
    taxonomy = _taxonomy(model)
    if not taxonomy or smq.pattern not in ("A", "B"):
        return smq
    query_squashed = _squash(user_query)
    dim_index = _dimension_index((model or {}).get("pattern_a") or {})
    # dimension 확장 금지 형태 — 전역 집계는 단일 값(dimension 불가), 기간별 분해는 EAV 속성
    # dimension 불가라, 채우면 질의 전체가 커버리지 밖으로 밀린다.
    dims_allowed = not smq.global_aggregate and not smq.time_breakdown

    added_dims: list[str] = []
    added_measures: list[SMQMeasure] = []
    fired: list[str] = []
    for term, node in taxonomy.items():
        if not isinstance(node, dict):
            continue
        if not _mentions_any(query_squashed, _hypernym_surfaces(str(term), node)):
            continue
        if _mentions_any(
            query_squashed, _child_discriminators(str(term), node, model or {}, dim_index)
        ):
            continue
        new_dims = _missing_child_dims(smq, node, dim_index) if dims_allowed else []
        new_measures = _missing_child_measures(smq, node, model or {})
        if not new_dims and not new_measures:
            continue
        added_dims.extend(d for d in new_dims if d not in added_dims)
        added_measures.extend(new_measures)
        fired.append(
            f"{term} ⊃ "
            + ", ".join(new_dims + [f"{m.resource_type}/{m.agg}" for m in new_measures])
        )

    if not added_dims and not added_measures:
        return smq
    note_guard(GUARD_HYPERNYM_EXPAND, "; ".join(fired))
    update: dict[str, Any] = {}
    if added_dims:
        update["dimensions"] = list(smq.dimensions) + added_dims
    if added_measures:
        update["measures"] = list(smq.measures) + added_measures
    return smq.model_copy(update=update)


def normalize_smq(
    smq: SMQ,
    user_query: str,
    model: Optional[dict] = None,
    *,
    hypernym_ambiguity: bool = False,
) -> SMQ:
    """LLM SMQ 선택의 알려진 비결정 오류를 결정적으로 교정한다(D-076 후속).

    실측(2026-07-21 yd-004): "CPU 용량"에 LOGICALCORE·PHYSICALCORE를 동시 선택. 실측
    (2026-07-21 gp-009 회귀): 같은 질의에서 PHYSICALCORE **단독** 선택으로도 흔들림 —
    운영 관행은 VM 위주라 CPU 용량/코어수=LOGICALCORE. 질의가 '물리'를 명시하지 않으면
    PHYSICALCORE를 제거(동시 선택 시)하거나 LOGICALCORE로 치환(단독 선택 시)한다.
    '물리' 신호가 있으면 선택을 존중해 불변.

    Plan 67 S3에서 두 교정을 추가했다 — 둘 다 표기·형태의 흔들림을 IR로 흡수하는 승격이다:
        - 기간 필터(`time` between …) → ``time_range``(+ 질의의 결정적 해석으로 값 교정)
        - "월별/월간" 분해 질의 → ``time_breakdown``(구 폴백 강제 게이트의 해소)
    모든 교정은 발동 카운터를 남긴다(R4 — 계측 후 축소 판단).

    Args:
        smq: LLM이 선택한 SMQ
        user_query: 원문 질의
        model: 시맨틱 모델(카탈로그) — 없으면 카탈로그 의존 교정을 건너뛴다
        hypernym_ambiguity: 상위어 단독 질의를 하위 전부로 확장할지(N4/D-133, 기본 OFF)
    """
    if hypernym_ambiguity:
        # 실측 관행이 확립된 아래 교정 가드들이 최종 중재자가 되도록 **가장 먼저** 돌린다.
        smq = _expand_hypernym_ambiguity(smq, user_query, model)
    names = {str(d).upper() for d in smq.dimensions}
    if "PHYSICALCORE" in names:
        q = (user_query or "").lower()
        if not any(h in q for h in _PHYSICAL_HINTS):
            if "LOGICALCORE" in names:
                dims = [d for d in smq.dimensions if str(d).upper() != "PHYSICALCORE"]
                note_guard(GUARD_PHYSICALCORE_DROP)
            else:
                dims = [
                    "LOGICALCORE" if str(d).upper() == "PHYSICALCORE" else d
                    for d in smq.dimensions
                ]
                note_guard(GUARD_PHYSICALCORE_SWAP)
            smq = smq.model_copy(update={"dimensions": dims})
            names = {str(d).upper() for d in dims}

    # 명시 요청 용량 차원 누락 보정 — 질의가 "CPU/메모리 용량"을 명시했는데 SMQ가 해당
    # dimension을 통째로 빠뜨리는 비결정(실측 2026-07-21 gp-009 2차 회귀: 같은 질의에서
    # 동시선택→단독선택→미선택으로 흔들림). 패턴 C(알람)는 무관하므로 A/B만.
    if smq.pattern in ("A", "B"):
        added: list[str] = []
        if _CPU_CAPACITY_RE.search(user_query or "") and not names & {"LOGICALCORE", "PHYSICALCORE"}:
            added.append("LOGICALCORE")
        if _MEM_CAPACITY_RE.search(user_query or "") and "TOTALSIZE" not in names:
            added.append("TotalSize")
        if added:
            note_guard(GUARD_CAPACITY_INJECT, ",".join(added))
            smq = smq.model_copy(update={"dimensions": list(smq.dimensions) + added})

    smq = _promote_time_filters(smq, user_query, model)
    smq = _promote_time_breakdown(smq, user_query)
    return smq


def _is_time_filter(f: SMQFilter, model: Optional[dict]) -> bool:
    """필터가 기간 표현인지 판정한다 — 필드명(표기 흔들림) 또는 값 형태(YYYYMM)로 본다.

    DB별 기간 컬럼명(profile 소관)을 공용 계층에 열거하지 않으려고 값 형태 판정을 함께 쓴다.
    카탈로그에 정의된 dimension이면 값이 우연히 6자리여도 기간으로 보지 않는다.
    """
    if str(f.field).lower() in _TIME_FILTER_FIELDS:
        return True
    if model and _resolve_dim(
        str(f.field), _dimension_index(model.get("pattern_a") or {})
    ) is not None:
        return False
    return bool(_months_from_filters([f]))


def _promote_time_filters(
    smq: SMQ, user_query: str, model: Optional[dict] = None
) -> SMQ:
    """기간을 필터로 표현한 SMQ를 ``time_range`` IR로 승격한다 (S-IR4).

    실측(2026-07-30 라이브 스모크): 선택은 정확한데 기간만 `{'field': 'time', 'op':
    'between', 'value': ['202606','202606']}` 필터로 나와 "미지원 필터"로 전량 폴백했다.
    필터를 IR 기간으로 옮기고, 질의에서 기간을 결정적으로 해석할 수 있으면 **그 값으로
    교정**한다(LLM이 계산한 월보다 결정적 파서를 신뢰 — D-035).
    """
    time_filters = [f for f in smq.filters if _is_time_filter(f, model)]
    if not time_filters and not smq.time_range:
        return smq

    resolved = resolve_stat_month_range(user_query)
    deterministic = (
        ([resolved[0]] if resolved[0] == resolved[1] else list(resolved))
        if resolved else []
    )
    llm_months = _months_from_filters(time_filters) or list(smq.time_range or [])
    months = deterministic or llm_months
    if time_filters and not months:
        # 기간 값을 결정적으로 뽑지 못했다("지난주" 등) — 필터를 그대로 남겨 커버리지가
        # 폴백으로 돌리게 한다. 조건을 조용히 버리고 전체 기간으로 집계하면 오답이 된다.
        return smq

    update: dict[str, Any] = {}
    if time_filters:
        update["filters"] = [f for f in smq.filters if f not in time_filters]
        note_guard(
            GUARD_TIME_FILTER_PROMOTE,
            "; ".join(f"{f.field} {f.op} {f.value}" for f in time_filters),
        )
    if deterministic and llm_months and deterministic != llm_months:
        note_guard(GUARD_TIME_RANGE_OVERRIDE, f"{llm_months} → {deterministic}")
    if months != list(smq.time_range or []):
        update["time_range"] = months
    return smq.model_copy(update=update) if update else smq


def _months_from_filters(time_filters: list[SMQFilter]) -> list[str]:
    """기간 필터 값에서 YYYYMM 목록을 뽑는다(YYYY-MM·YYYY년 M월 표기 포함)."""
    months: list[str] = []
    for f in time_filters:
        values = f.value if isinstance(f.value, (list, tuple)) else [f.value]
        for raw in values:
            text = str(raw)
            if _YYYYMM_RE.fullmatch(text):
                months.append(text)
                continue
            m = re.fullmatch(r"(\d{4})\D{0,2}(\d{1,2})\D?", text)
            if m and 1 <= int(m.group(2)) <= 12:
                months.append(f"{m.group(1)}{int(m.group(2)):02d}")
    # 중복 제거 + 정렬(범위 양끝만 의미가 있다).
    ordered = sorted(set(months))
    return [ordered[0], ordered[-1]] if len(ordered) > 1 else ordered


def _promote_time_breakdown(smq: SMQ, user_query: str) -> SMQ:
    """"월별/월간" 분해 질의를 ``time_breakdown`` IR로 승격한다 (S-IR2).

    종전에는 이 표면어를 만나면 컴파일을 포기하고 LLM 폴백을 강제했다(서버당 1행 집계로는
    표현 불가했기 때문). 컴파일러가 기간별 행 분해를 지원하게 됐으므로 **폴백 강제 대신
    승격**한다. 표면어 정규식은 유지하고 발동만 계측한다(R4 — 가드 삭제 금지).
    """
    if smq.pattern != "B" or smq.time_breakdown or not smq.measures:
        return smq
    if not _MONTHLY_BREAKDOWN_RE.search(user_query or ""):
        return smq
    note_guard(GUARD_BREAKDOWN_PROMOTE)
    return smq.model_copy(update={"time_breakdown": True})


def parse_smq_response(content: str) -> Optional[SMQ]:
    """LLM 응답에서 SMQ JSON을 파싱한다(코드펜스 허용). 밖/파싱실패는 None."""
    data = extract_json_from_response((content or "").strip())
    if not isinstance(data, dict):
        return None
    if str(data.get("pattern", "")).upper() not in ("A", "B", "C"):
        return None  # {"pattern": "none"} 등 커버리지 밖 신호
    try:
        return SMQ.from_dict(data)
    except Exception as e:  # noqa: BLE001 — 스키마 불일치는 폴백으로 강등
        logger.debug("SMQ 파싱 실패(스키마 불일치): %s", e)
        return None


async def _select_smq_one_shot(
    llm: Any,
    user_query: str,
    model: dict,
    server_scope: Optional[tuple[str, list[str]]],
) -> Optional[SMQ]:
    """현행 1방 SMQ 선택 — 카탈로그를 프롬프트에 실어 LLM 1회 호출로 선택받는다.

    프롬프트·메시지 구성은 무변경이다(플래그 OFF 경로의 바이트 동일성 근거).

    Args:
        llm: LLM 인스턴스
        user_query: 사용자 원문 질의
        model: 시맨틱 모델(카탈로그)
        server_scope: 선행 task 결과 서버 스코프(있으면 예외 블록 주입, D-099)

    Returns:
        파싱된 SMQ, 또는 None(LLM 실패·커버리지 밖 신호·파싱 실패)
    """
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    from src.prompts.semantic_compiler import (
        SEMANTIC_SMQ_SCOPE_NOTE,
        SEMANTIC_SMQ_SYSTEM_TEMPLATE,
        SEMANTIC_SMQ_USER_TEMPLATE,
    )

    system = SEMANTIC_SMQ_SYSTEM_TEMPLATE.format(catalog=render_catalog(model))
    user = SEMANTIC_SMQ_USER_TEMPLATE.format(user_query=user_query)
    # 선행 스코프가 있으면 "특정 서버 지목·정렬 상위" 커버리지 밖 조건을 해제한다(D-099) —
    # 이 둘은 컴파일러가 결정적으로 처리하므로 그것을 이유로 none을 받으면 조립이 무산된다.
    if server_scope and server_scope[1]:
        user += SEMANTIC_SMQ_SCOPE_NOTE
    messages = [SystemMessage(content=system)]
    # KBGenAIChat 등은 SystemMessage 다음 AIMessage 순서를 요구 — 클래스명으로 감지(선택 의존).
    if type(llm).__name__ == "KBGenAIChat":
        messages.append(AIMessage(content=""))
    messages.append(HumanMessage(content=user))

    try:
        response = await llm.ainvoke(messages)
    except Exception as e:  # noqa: BLE001 — LLM 실패는 폴백으로 강등(회귀 0)
        logger.warning("NL→SMQ 생성 실패(폴백): %s", e)
        return None

    return parse_smq_response(getattr(response, "content", "") or "")


def _stepwise_enabled(app_config: Optional["AppConfig"]) -> bool:
    """단계적 도출 루프(S2/D-128) 진입 여부를 판정한다.

    ``app_config``가 없으면 **OFF**로 본다 — 설정을 여기서 로드하면 호출마다 ``.env``를
    다시 읽게 되고(load_config는 싱글톤이 아니다) 테스트가 환경에 좌우된다. 실 경로
    (query_generator·multi_db_executor)는 항상 설정을 넘긴다.
    """
    text2sql = getattr(app_config, "text2sql", None)
    return bool(getattr(text2sql, "stepwise_derivation", False))


def _hypernym_ambiguity_enabled(app_config: Optional["AppConfig"]) -> bool:
    """상위어 모호성 처리(N4/D-133) 진입 여부를 판정한다(설정 미주입이면 OFF).

    ``_stepwise_enabled``와 같은 규칙이다 — 여기서 설정을 로드하면 호출마다 ``.env``를 다시
    읽어 테스트가 환경에 좌우된다.
    """
    text2sql = getattr(app_config, "text2sql", None)
    return bool(getattr(text2sql, "hypernym_ambiguity", False))


async def _select_smq_stepwise(
    llm: Any,
    user_query: str,
    db_id: str,
    model: dict,
    app_config: "AppConfig",
    stepwise_deps: Optional["StepwiseDeps"],
    derivation_sink: Optional[list[dict]],
    server_scope: Optional[tuple[str, list[str]]] = None,
) -> tuple[Optional[SMQ], Optional[CoverageResult]]:
    """단계적 컬럼 도출 루프(S2/D-128)로 SMQ를 도출한다.

    루프 산출물은 기존 ``SMQ.from_dict`` 경로로 통과시켜 검증한다(새 검증 경로 신설 금지 —
    D-067). 미해결 필드가 남거나 도출 실패면 **사유를 담은 CoverageResult**를 돌려 호출부가
    3단 폴백으로 넘기게 한다(침묵 폴백 금지).

    Args:
        llm: tool-calling 가능한 LLM
        user_query: 사용자 원문 질의
        db_id: 대상 DB 식별자
        model: 시맨틱 모델(카탈로그)
        app_config: 앱 설정(상한 산출용)
        stepwise_deps: 경로별 도구 주입 재료(StepwiseDeps 또는 None)
        derivation_sink: 관측 레코드 적재 리스트(state 노출용, 선택)

    Returns:
        (smq, cov) — smq가 None이면 cov.reason이 폴백 사유다.
    """
    from src.nodes.column_deriver import StepwiseDeps, StepwiseLimits, derive_smq
    from src.prompts.semantic_compiler import SEMANTIC_SMQ_SCOPE_NOTE

    deps = stepwise_deps if stepwise_deps is not None else StepwiseDeps()
    # 선행 스코프 예외 블록 — 1방 경로의 SCOPE_NOTE 주입과 대칭(D-099 ⑤, Plan 69 P0-⑩).
    # 스코프가 없으면 빈 문자열이라 도출 프롬프트 바이트 불변.
    scope_note = (
        SEMANTIC_SMQ_SCOPE_NOTE.format() if server_scope and server_scope[1] else ""
    )
    record = await derive_smq(
        llm, user_query, db_id, model,
        deps=deps,
        limits=StepwiseLimits.from_config(app_config.text2sql),
        scope_note=scope_note,
    )
    if derivation_sink is not None:
        derivation_sink.append(record)

    unresolved = record.get("unresolved") or []
    raw_smq = record.get("smq")
    if unresolved or not isinstance(raw_smq, dict):
        reason = _derivation_failure_reason(record)
        logger.info("단계적 도출 미완(폴백): %s", reason)
        return None, CoverageResult(covered=False, reason=reason)

    try:
        smq = SMQ.from_dict(raw_smq)
    except Exception as e:  # noqa: BLE001 — 스키마 불일치는 사유와 함께 폴백
        reason = f"단계적 도출 SMQ 형식 오류: {e}"
        logger.info("%s", reason)
        record["stopped_reason"] = "schema_error"
        return None, CoverageResult(covered=False, reason=reason)
    logger.info(
        "단계적 도출 SMQ 확정(패턴 %s, 라운드 %s, tool %s)",
        smq.pattern, record.get("rounds"), record.get("tool_calls"),
    )
    return smq, None


def _stamp_coverage(derivation_sink: Optional[list[dict]], covered: Optional[bool]) -> None:
    """직전 도출 레코드에 커버리지 판정 결과를 기록한다(관측 — 1방 경로는 sink 없음)."""
    if derivation_sink:
        derivation_sink[-1]["covered"] = covered


def _stamp_guards(derivation_sink: Optional[list[dict]], guards: dict[str, int]) -> None:
    """질의 1건에서 발동한 가드를 기록·로그한다 (R4 — stepwise ON/OFF 발동률 비교 재료).

    stepwise ON에서는 도출 레코드(state 노출)에도 남기고, OFF 경로는 로그만 남는다.
    """
    if not guards:
        return
    logger.info("[가드계측] 질의 단위 발동: %s", guards)
    if derivation_sink:
        derivation_sink[-1]["guards"] = guards


def _derivation_failure_reason(record: dict) -> str:
    """도출 실패·미해결을 사용자·감사 노출용 단일 사유 문자열로 만든다."""
    parts = [f"단계적 도출 미완({record.get('stopped_reason')})"]
    unresolved = record.get("unresolved") or []
    if unresolved:
        parts.append(
            "미해결: " + "; ".join(
                f"{u.get('field')}({u.get('reason')})" for u in unresolved[:5]
            )
        )
    elif not record.get("smq"):
        parts.append("SMQ 미도출")
    return " — ".join(parts)


async def compile_from_nl(
    llm: Any,
    user_query: str,
    db_id: str,
    *,
    default_limit: int = 100,
    stat_month: StatMonth = None,
    value_index: Optional[dict[str, list[str]]] = None,
    server_scope: Optional[tuple[str, list[str]]] = None,
    app_config: Optional["AppConfig"] = None,
    stepwise_deps: Optional["StepwiseDeps"] = None,
    derivation_sink: Optional[list[dict]] = None,
) -> tuple[Optional[str], Optional[SMQ], Optional[CoverageResult]]:
    """coverage_router: 자연어 → (LLM)SMQ → 커버리지 판정 → 결정적 컴파일.

    SMQ 선택 방식만 두 갈래다 — 기본은 현행 1방 선택이고, ``TEXT2SQL_STEPWISE_DERIVATION``이
    ON이면 도구 기반 단계적 도출 루프(S2/D-128)가 SMQ를 만든다. **커버리지 판정·컴파일은
    어느 쪽이든 동일한 결정적 경로**를 통과한다(D-076·D-067). SQL 생성 4경로가 모두 이
    함수를 지나므로 여기가 대칭 주입 지점이다(D-066).

    Args:
        llm: LLM 인스턴스
        user_query: 사용자 원문 질의
        db_id: 대상 DB 식별자
        default_limit: 기본 LIMIT
        stat_month: 결정적으로 해석된 통계 월(범위)
        value_index: E5-2 값 인덱스(있으면 필터 리터럴 실측 검증)
        server_scope: 선행 task 결과 서버 스코프(D-099)
        app_config: 앱 설정 — 단계적 도출 플래그·상한 판정용(없으면 1방 경로)
        stepwise_deps: 경로별 도구 주입 재료(``column_deriver.StepwiseDeps``)
        derivation_sink: 단계적 도출 관측 레코드 적재 리스트(state 노출용)

    반환:
        (sql, smq, cov) — sql이 있으면 커버리지 내 결정적 조립 성공(LLM SQL 생성 우회).
        sql이 None이면 커버리지 밖/파싱실패 → 호출부가 현행 폴백(LLM 자유생성)으로 진행.
    """
    model = load_semantic_model(db_id)
    if not model:
        return None, None, None
    guards_before = guard_counters()

    if _stepwise_enabled(app_config):
        smq, derive_cov = await _select_smq_stepwise(
            llm, user_query, db_id, model, app_config, stepwise_deps, derivation_sink,
            server_scope=server_scope,
        )
        if smq is None:
            return None, None, derive_cov
    else:
        smq = await _select_smq_one_shot(llm, user_query, model, server_scope)
        if smq is None:
            return None, None, None
    smq = normalize_smq(
        smq, user_query, model,
        hypernym_ambiguity=_hypernym_ambiguity_enabled(app_config),
    )

    # 월별 행 분해("월간/월별 통계·추이") 질의는 normalize가 time_breakdown으로 승격한다
    # (S-IR2). 승격 조건에 걸리지 않는 형태(measure 없는 패턴 B 등)는 여전히 컴파일 불가라
    # 폴백을 강제한다 — 게이트는 남기고 발동만 계측한다(R4).
    if (
        smq.pattern == "B"
        and not smq.time_breakdown
        and _MONTHLY_BREAKDOWN_RE.search(user_query or "")
    ):
        cov = CoverageResult(
            covered=False,
            reason="월별 분해(월간/월별) 질의는 컴파일 미지원 - LLM 폴백",
        )
        note_guard(GUARD_MONTHLY_GATE, cov.reason)
        logger.info("시맨틱 커버리지 밖(폴백): %s", cov.reason)
        _stamp_coverage(derivation_sink, False)
        _stamp_guards(derivation_sink, _guard_delta(guards_before))
        return None, smq, cov

    # 선행 스코프가 결정적으로 주어지면 SMQ의 서버 식별 필터는 중복이다. 그대로 두면
    # 커버리지 밖(패턴 A/B 안전 필터는 resource_type뿐)으로 밀려 결정적 조립이 발동하지
    # 못하고 LLM 폴백으로 떨어진다 — 스코프가 더 신뢰도 높은 출처이므로 제거한다(D-099).
    if server_scope and server_scope[1]:
        identity_fields = {"name", "hostname", str(server_scope[0]).lower()}
        kept = [f for f in smq.filters if str(f.field).lower() not in identity_fields]
        if len(kept) != len(smq.filters):
            note_guard(
                GUARD_SCOPE_FILTER_STRIP, f"{len(smq.filters) - len(kept)}건",
            )
            logger.info(
                "선행 스코프 우선 — SMQ 서버 식별 필터 %d건 제거(결정적 HAVING으로 대체)",
                len(smq.filters) - len(kept),
            )
            smq = smq.model_copy(update={"filters": kept})
        if smq.global_aggregate:
            # 스코프는 "이 서버들"이라는 결정적 한정이므로 전역 단일 값 집계와 양립하지
            # 않는다(HAVING이 전역 집계 1행을 지워 결과가 비어버린다) — 스코프를 우선한다.
            note_guard(GUARD_SCOPE_GLOBAL_DROP)
            smq = smq.model_copy(update={"global_aggregate": False, "entity_count": False})

    cov = check_coverage(smq, model, value_index=value_index)
    _stamp_coverage(derivation_sink, cov.covered)
    if not cov.covered:
        logger.info("시맨틱 커버리지 밖(폴백): %s", cov.reason)
        _stamp_guards(derivation_sink, _guard_delta(guards_before))
        return None, smq, cov
    sql = compile_smq(
        smq, db_id, model, user_query=user_query,
        default_limit=default_limit, stat_month=stat_month,
        server_scope=server_scope,
    )
    logger.info("시맨틱 결정적 컴파일 성공(패턴 %s): %s", smq.pattern, sql[:200])
    _stamp_guards(derivation_sink, _guard_delta(guards_before))
    return sql, smq, cov
