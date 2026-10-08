"""DB 레지스트리 정본 로더 (`config/db_registry.yaml`) — Plan 67 R2.

신규 DB 편입 시 수정 지점을 **레지스트리 + `.env` 2곳**으로 줄이기 위해, 등록 정보와
라우팅 어휘(위치·존·제품 표면어)를 이 모듈이 단일 출처로 제공한다. 기존에 모듈마다
사본으로 존재하던 위치 키워드 튜플 6곳(§1.2-②)은 전부 이 모듈의 파생 API를 소비한다.

주의 — 이름이 비슷한 `src/routing/db_registry.py`는 **연결 관리 레지스트리**(`DBRegistry`,
MCP 클라이언트 생성)로 별개 모듈이다. 이 모듈은 선언 정본(YAML)의 로더다.

경계:
    - **D-004**: 여기서 제공하는 위치 표면어는 라우팅 **의도 분류**에 쓰지 않는다.
      사용처는 사용자 명시 힌트 보강·멀티턴 승계 신호·프롬프트 렌더뿐이며, 키워드
      기반 사전 분류(폐기된 v1 라우팅)의 재도입이 아니다.
    - **D-089**: 설정 일원화이지 어댑터 훅 확장이 아니다. DB별 SQL 특화 로직은 계속
      `src/db_adapters/{db}/`에만 둔다.

계층: infrastructure (routing).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "db_registry.yaml"

# 소스 id 형식(plans/87 M-2 · 게이트웨이 `JENNIFER_SOURCES`와 같은 규칙)과 예약어.
_SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,15}$")
_RESERVED_SOURCE_IDS: frozenset[str] = frozenset({"default", "api"})
# 레지스트리에 없는 소스 dbId 경고를 id별 1회로 줄인다(plans/87 J8 M-4 — 알람마다 반복 방지).
_WARNED_UNKNOWN_SOURCES: set[str] = set()


class RegistryError(Exception):
    """레지스트리 정본 로드/검증 실패."""


@dataclass(frozen=True)
class ZoneSpec:
    """존(알림 지역 스코프) 선언."""

    code: str
    label: str = ""


@dataclass(frozen=True)
class FamilySpec:
    """제품군 선언.

    Attributes:
        product_terms: 제품명 단독 토큰(지역 변별력 없음).
        signal_terms: 사용자가 이번 턴에 DB를 새로 지목했다고 볼 표면어.
    """

    name: str
    product_terms: tuple[str, ...] = ()
    signal_terms: tuple[str, ...] = ()


#: 보기 창 의미(plans/134 M-2 · SPEC-apm-question-coverage §6.1) — 상한이 아니다.
#: current = 현재값만 · range = 요청 기간을 그대로 넘김 · hourly = 시 단위 통계 ·
#: none = 시간 무관(목록) · compare = 두 기간 비교(기준·비교 구간을 코드가 정해 넘김 — plans/134
#: W6 A-1).
VIEW_WINDOWS: frozenset[str] = frozenset({"current", "range", "hourly", "none", "compare"})
#: 보기 선택 조건 형식(§6.1 `ViewArgSpec.type` + `text` — 식별자가 아닌 자유 문자열(예 URL 이름 ·
#: plans/134 W2 확장)).
VIEW_ARG_TYPES: frozenset[str] = frozenset(
    {"int", "bool", "enum", "str", "str_list", "catalog", "text",
     # plans/134 W5·W7 — 게이트웨이 형식을 그대로 옮긴 값 형식(본체가 통과시킨 값이 게이트웨이에서
     # 보기 전체를 실패시키지 않게 — W2 검증 B4): `opaque` = 공백·제어 문자 없는 1~256자(GUID 등
     # 형식 미공개 값) · `account` = 계정 ID 형식 `[A-Za-z0-9._@-]{1,64}` · `token` = 대문자 토큰
     # `[A-Z0-9_]{1,64}`(값은 대문자로 맞춘다 — 경로 변수 `errorType`)
     "opaque", "account", "token"}
)
#: 보기 대상 표현(plans/134 W5·W7 · 계약 §4.1) — 비면 종전(필수 대상 `required_input` · 첫 홉
#: `first_hop`이 정한다 · 기존 12개 보기). `optional` = 이번 턴 대상이 있으면 대상별 · 없으면
#: hostname 없이 1회(첫 홉 삽입 없음) · `reference` = 앞 결과 행의 참조 칸(`ViewSpec.reference`)
#: · `none` = 대상을 쓰지 않는 보기(대상 해석을 하지 않는다) · `named` = 이번 task 대상
#: 텍스트(서비스·업무 이름)를 도구 인자 `target_arg`에 목록으로 싣는 보기 — 없으면 전체 1회
#: (plans/134 W3·W4 M-5).
VIEW_TARGETS: frozenset[str] = frozenset({"", "optional", "reference", "none", "named"})
#: 참조 보기가 고르는 행 칸(게이트웨이 도구 계약 이름 — 벤더 중립).
VIEW_REFERENCES: frozenset[str] = frozenset({"profile_ref", "active_ref", "guid"})
#: `named` 보기가 이름 목록을 싣는 도구 인자(게이트웨이 도구 계약 이름 — 이름 해석은 게이트웨이).
VIEW_TARGET_ARGS: frozenset[str] = frozenset({"service", "business"})


@dataclass(frozen=True)
class ViewArgSpec:
    """보기 하나가 받는 선택 조건 1건(plans/134 M-3 · SPEC-apm-question-coverage §6.1).

    계획 LLM 이 `view_args`로 값을 고르고 코드가 이 선언으로 형·범위·선택지를 검증한다
    (`apm_query.validate_view_args`). 벤더 중립 어휘만 둔다(D-274 ③).

    Attributes:
        name: 조건 이름(분해 `view_args`의 키)
        type: `int`·`bool`·`enum`·`str`(식별자 형식)·`str_list`·`catalog`(지표 군 이름 — W2)·
            `text`·`opaque`·`account`·`token`(W5·W7)
        choices: `enum` 선택지
        min: `int` 하한
        catalog: `catalog` 형식의 지표 군 이름
        tool_arg: 도구 인자 이름(없으면 `name`)
        label: 분해 프롬프트에 렌더하는 짧은 설명(SPEC 표 밖 확장 — 계획 LLM 재료)
        required: 필수 조건(plans/134 W7) — 없거나 무효면 그 보기는 조회하지 않고 되묻는다
            (선택 조건 무효는 버리고 고지한 채 조회 — W1 검증 H-2)
        default: 미지정일 때 도구에 싣는 값(`enum`은 선택지 중 하나)
        targeted_choices: `enum` 값 중 **대상별로 부르는** 값(대상 표현 `optional` 보기 — 그 밖 값은
            대상과 무관한 소스 범위라 hostname 없이 1회). 비면 늘 대상별이다
        verified_values: `int` 조건의 **검증된 원천 허용값**(오름차순 · plans/134 W6 M-7) — 사용자가
            값을 말하지 않았을 때 처리기가 조회 구간 길이로 이 중 하나를 고른다. 비면 자동 선택하지
            않는다(허용값 미확인 — W10). 사용자가 말한 값을 이 목록으로 거르지 않는다
    """

    name: str
    type: str
    choices: tuple[str, ...] = ()
    min: int | None = None
    catalog: str | None = None
    tool_arg: str | None = None
    label: str = ""
    required: bool = False
    default: Any = None
    targeted_choices: tuple[str, ...] = ()
    verified_values: tuple[int, ...] = ()


@dataclass(frozen=True)
class ViewSpec:
    """비SQL 처리기의 고정 보기 1건(plans/125 §4.2 · G-4 (a)) — LLM 이 고르는 유일한 비SQL 인자.

    Attributes:
        id: 보기 id(벤더 중립 · 예 `apm.app_health`)
        capability: 이 보기가 답하는 답변 영역
        tool: 게이트웨이 MCP 도구 이름
        required_input: 반드시 있어야 하는 대상 패싯(예 `hostname`) — 비면 첫 홉 가능
        first_hop: 대상 없이 부를 수 있는가(전체 목록 보기)
        limit: 사용자 고지용 설명(기본 구간 등 사실 — 상한 표기 아님)
        label: 분해 프롬프트·요약에 쓰는 보기 설명(비면 답변 영역 설명)
        window: 창 의미(`VIEW_WINDOWS` — plans/134 M-2 · 종전 `window_max_minutes` 상한 의미 폐지)
        fixed_args: 도구 고정 인자(예 `kind`)
        args: 허용 선택 조건(`view_args`)
        examples: 계획 LLM 에 렌더하는 예문
        notices: 결과가 있으면 붙이는 고지 kind(예 `apm_change_detection` — plans/134 W2)
        target: 대상 표현(`VIEW_TARGETS` — plans/134 W5·W7 · 비면 종전 규칙)
        reference: 참조 보기(`target: reference`)가 앞 결과 행에서 고르는 칸(`VIEW_REFERENCES`)
        target_arg: 이름 보기(`target: named`)가 대상 텍스트 목록을 싣는 도구 인자
            (`VIEW_TARGET_ARGS` — plans/134 W3·W4)
    """

    id: str
    label: str = ""
    capability: str = ""
    tool: str = ""
    required_input: str = ""
    first_hop: bool = False
    limit: str = ""
    window: str = "current"
    fixed_args: dict[str, Any] = field(default_factory=dict, hash=False)
    args: tuple[ViewArgSpec, ...] = ()
    examples: tuple[str, ...] = ()
    notices: tuple[str, ...] = ()
    target: str = ""
    reference: str = ""
    target_arg: str = ""


@dataclass(frozen=True)
class SourceSpec:
    """솔루션 아래 소스 1건(plans/87 J8 · D-287 ④) — 제니퍼 소스(뷰 서버 하나) ↔ 존 정본.

    Attributes:
        id: 소스 id(소문자 슬러그 · 게이트웨이 `JENNIFER_SOURCES`의 id와 같다 · 알람 dbId는
            `{family}_{id}`)
        label: 사용자 표시 이름
        zone: 소스가 속한 존 코드 — 비면 존 없는 소스(전 존 구독자·관리자만)
        terms: 이 소스를 고르는 사전 정의 단어(plans/147 · D-322) — APM task 안에서만 쓴다.
            시스템 유사어(`source_alias_terms`)·위치 힌트가 아니다(D-293). 여러 소스가 같은 단어를
            가질 수 있다(걸리면 되묻기).
    """

    id: str
    label: str = ""
    zone: str = ""
    terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class EdgeSpec:
    """교차 계층 엔터티 간선 1건(plans/125 §4.5 · E-2) — 패싯 → 패싯 결정적 대응.

    Attributes:
        id: 간선 id(E1 …)
        from_facet · to_facet: 옮기는 식별 패싯(hostname · server_name · apm_instance · asset_key ·
            nodename)
        owner: 대응을 주는 시스템(소유자 — 본체는 재구현하지 않는다)
        via: 수단(도구 · 어댑터 고정 조회 · 키 브리지 · 셀렉터)
        grades: 판정 등급 어휘
    """

    id: str
    from_facet: str
    to_facet: str
    owner: str
    via: str = ""
    grades: tuple[str, ...] = ()


@dataclass(frozen=True)
class SolutionSpec:
    """관측 솔루션 선언 — 실행 그룹의 1차 축 (D-176 · plans/82 §4.2).

    Attributes:
        code: 솔루션 식별자(polestar·apm·dpm …).
        order: 솔루션 간 조회 순서. **선언 순서가 아니라 이 값이 정본**이다.
        backend: 그룹 실행 주체 선택 키(sql·rest·mcp).
        capabilities: 이 솔루션이 답할 수 있는 관측 능력.
        requires: 이 솔루션을 쓰기 전에 해소돼야 하는 능력(예: apm → host_location).
        views: 비SQL 처리기의 고정 보기 표(plans/125 §4.2) — SQL 솔루션은 비어 있다.
        sources: 소스 ↔ 존 표(plans/87 J8 · D-287 ④ — 제니퍼 뷰 서버 N개) — 선언 순서.
        aliases: 사용자가 이 시스템을 부르는 이름·유사어(plans/132 N-1) — 명시 소스 인식 전용.
            DB 시스템의 유사어는 `databases[].aliases`가 정본이라 여기에는 비DB 시스템만 둔다.
    """

    code: str
    label: str = ""
    order: int = 0
    backend: str = "sql"
    family: str = ""
    capabilities: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    views: tuple[ViewSpec, ...] = ()
    sources: tuple[SourceSpec, ...] = ()
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapabilitySpec:
    """답변 영역(capability) 설명 — 소유표 렌더 재료 (plans/102 §3.1 · D-224 ①).

    **소유는 담지 않는다.** 어느 시스템이 정본인지는 `SolutionSpec.capabilities`(다중 존
    시스템)와 `DBEntry.capabilities`(존 없는 단일 DB 시스템)가 정한다 — 여기에 두면 두 번째
    출처가 된다(D-053).

    `ambiguous_owner`는 "어느 시스템이 소유하는가"가 아니라 **그 영역 자체의 성질**이다
    (두 시스템이 같은 것을 답할 수 있어 정본 선언이 기본값일 뿐이라는 표시 — G-1). 소유 선언과
    경쟁하지 않으므로 두 번째 출처가 아니다.
    """

    code: str
    label: str = ""
    #: 소유가 모호한 영역인가 — 소재 프로브 결정표의 "소유 모호" 행 판정 입력(G-1).
    ambiguous_owner: bool = False
    #: 소유 시스템이 활성일 때만 분해 프롬프트 영역 카탈로그에 렌더한다(plans/134 W2 — 비활성 배포
    #: 바이트 불변). 종전 영역은 False(늘 렌더 · 종전 바이트 그대로).
    active_only: bool = False


@dataclass(frozen=True)
class ZoneGroupSpec:
    """존 그룹 선언 — 솔루션 내부의 2차 축 (D-176).

    `zones` 선언 순서(=알림 RBAC 선택지 순서)와 **별개 축**이다. 조회 순서는
    `query_order`가 정본이며, 두 순서를 겹치면 한쪽 요구가 다른 쪽을 흔든다.
    """

    code: str
    label: str = ""
    solution: str = ""
    zones: tuple[str, ...] = ()
    query_order: int = 0


@dataclass(frozen=True)
class LocationSpec:
    """위치 표면어 → db_id 매핑 선언."""

    term: str
    db_ids: tuple[str, ...] = ()

    @property
    def is_exclusive(self) -> bool:
        """이 표면어가 단일 DB를 배타적으로 지목하는지 여부."""
        return len(self.db_ids) == 1


@dataclass(frozen=True)
class DBEntry:
    """레지스트리에 등록된 DB 1건."""

    db_id: str
    display_name: str = ""
    description: str = ""
    aliases: tuple[str, ...] = ()
    env_connection_key: str = ""
    env_type_key: str = ""
    engine: str = "postgresql"
    db_schema: str = ""
    family: str = ""
    zone: str = ""
    signal_terms: tuple[str, ...] = ()
    enabled: bool = True
    #: 존 없는 단일 DB 시스템의 답변 영역 소유 선언(plans/102 §3.1). 비어 있으면 `family`로 찾은
    #: 솔루션의 capabilities를 쓴다.
    capabilities: tuple[str, ...] = ()
    #: true면 라우터 DB 목록에 스키마 캐시의 LLM 생성 「상세」 설명을 덧붙이지 않는다
    #: (plans/102 X-T4). 생성 설명이 다른 시스템 소유 영역 어휘를 되살려 경계를 무너뜨릴 때 끈다.
    #: 기본 false = 현행.
    description_locked: bool = False
    #: true면 SQL 구조 영역(따옴표 밖)의 한글 식별자를 허용한다 — 물리 컬럼명이 한글인 DB만 켠다
    #: (plans/137). 허용해도 스키마에 실재하는 이름·SQL 안에서 선언한 별칭만 통과한다. 기본 false = 현행.
    allow_hangul_identifiers: bool = False
    #: true면 집계(GROUP BY) 결과 표에서 묶음 기준 값이 NULL인 칸을 「(값 없음)」으로 표시한다
    #: (plans/137 W13). 화면 결과 표에만 적용 — 저장 결과·CSV·양식 채우기는 그대로다. 기본 false = 현행.
    label_null_group_keys: bool = False


@dataclass(frozen=True)
class DBRegistry:
    """레지스트리 정본 전체와 파생 조회 API."""

    version: int = 1
    zones: tuple[ZoneSpec, ...] = ()
    solutions_: tuple[SolutionSpec, ...] = ()
    zone_groups_: tuple[ZoneGroupSpec, ...] = ()
    families: tuple[FamilySpec, ...] = ()
    environment_terms: tuple[str, ...] = ()
    locations: tuple[LocationSpec, ...] = ()
    databases: tuple[DBEntry, ...] = field(default_factory=tuple)
    capabilities_: tuple[CapabilitySpec, ...] = ()
    entity_edges_: tuple[EdgeSpec, ...] = ()

    def entity_edges(self) -> tuple[EdgeSpec, ...]:
        """교차 계층 엔터티 간선 표(선언 순서 · plans/125 E-2)."""
        return self.entity_edges_

    # ── DB 조회 ────────────────────────────────────────────
    def get(self, db_id: str) -> DBEntry | None:
        """db_id로 등록 항목을 조회한다(미등록이면 None)."""
        for entry in self.databases:
            if entry.db_id == db_id:
                return entry
        return None

    def db_ids(self) -> tuple[str, ...]:
        """등록된 모든 db_id를 선언 순서로 반환한다."""
        return tuple(e.db_id for e in self.databases)

    # ── 존 ────────────────────────────────────────────────
    def zone_codes(self) -> tuple[str, ...]:
        """선언된 존 코드를 선언 순서로 반환한다."""
        return tuple(z.code for z in self.zones)

    def zone_to_db_ids(self) -> dict[str, tuple[str, ...]]:
        """존 코드 → 그 존에 속한 db_id 목록."""
        out: dict[str, list[str]] = {z.code: [] for z in self.zones}
        for entry in self.databases:
            if entry.zone:
                out.setdefault(entry.zone, []).append(entry.db_id)
        return {code: tuple(ids) for code, ids in out.items()}

    # ── 실행 그룹 축 (D-176 · plans/82 §4.2) ───────────────
    def solutions(self) -> tuple[SolutionSpec, ...]:
        """등록된 관측 솔루션을 `order` 순으로 반환한다.

        선언 순서가 아니라 `order`가 정본이다 — 조회 순서를 YAML 줄 위치에 의존시키면
        무관한 편집이 실행 순서를 바꾼다.
        """
        return tuple(sorted(self.solutions_, key=lambda s: (s.order, s.code)))

    def zone_groups(self) -> tuple[ZoneGroupSpec, ...]:
        """존 그룹을 `query_order` 순으로 반환한다(은행존 → 공동존).

        `zone_codes()`(알림 RBAC 선택지 순서)와 **다른 축**이다.
        """
        return tuple(sorted(self.zone_groups_, key=lambda g: (g.query_order, g.code)))

    def zone_group_of(self, db_id: str) -> str | None:
        """db_id가 속한 존 그룹 코드를 반환한다(매핑 없으면 None)."""
        entry = self.get(db_id)
        if not entry or not entry.zone:
            return None
        for group in self.zone_groups_:
            if entry.zone in group.zones:
                return group.code
        return None

    def capability_providers(self, capability: str) -> tuple[str, ...]:
        """해당 관측 능력을 제공하는 솔루션 코드를 `order` 순으로 반환한다."""
        return tuple(
            s.code for s in self.solutions() if capability in s.capabilities
        )

    # ── 답변 영역 소유 (plans/102 §3.1 · D-224 ①) ─────────
    # "시스템"은 소유 판정의 단위다 — 다중 존 시스템은 솔루션 코드(폴스타 = DB 여러 개),
    # 존 없는 단일 DB 시스템은 db_id(자산관리 = DB 하나)다.
    def capability_specs(self) -> tuple[CapabilitySpec, ...]:
        """답변 영역 설명을 선언 순서로 반환한다(소유표 렌더 순서)."""
        return self.capabilities_

    def ambiguous_capabilities(self) -> frozenset[str]:
        """소유가 모호하다고 **레지스트리가 선언한** 답변 영역 코드 (G-1 · plans/102 §3.4).

        소재 프로브가 "소유 모호" 행을 타는 조건이다. 코드 상수로 두면 레지스트리 데이터와
        두 번째 출처가 되므로 선언에서만 읽는다(D-053).
        """
        return frozenset(s.code for s in self.capabilities_ if s.ambiguous_owner)

    def _solution_of_family(self, family: str) -> SolutionSpec | None:
        if not family:
            return None
        for spec in self.solutions():
            if spec.family == family:
                return spec
        return None

    def system_of(self, db_id: str) -> str | None:
        """db_id가 속한 소유 시스템 키. 답변 영역 선언이 없는 DB면 None."""
        entry = self.get(db_id)
        if entry is None:
            return None
        if entry.capabilities:
            return entry.db_id
        solution = self._solution_of_family(entry.family)
        return solution.code if solution and solution.capabilities else None

    def systems_of(self, db_ids: Iterable[str]) -> frozenset[str]:
        """db_id들의 소유 시스템 묶음 — 선언 없는·미등록 DB는 세지 않는다(`system_of` None 제외)."""
        return frozenset(s for s in (self.system_of(d) for d in db_ids) if s)

    def capabilities_of(self, db_id: str) -> tuple[str, ...]:
        """db_id가 답할 수 있는 답변 영역(DB 항목 선언 우선, 없으면 솔루션 선언)."""
        entry = self.get(db_id)
        if entry is None:
            return ()
        if entry.capabilities:
            return entry.capabilities
        solution = self._solution_of_family(entry.family)
        return solution.capabilities if solution else ()

    def capability_owners(self, capability: str) -> tuple[str, ...]:
        """답변 영역을 소유한 시스템 키(선언 순서 · 중복 제거). 정상 구성이면 0~1개다.

        DB 항목의 소유가 먼저이고, **DB 항목이 없는 솔루션(비DB 시스템 — plans/125 A-1 · 121
        TP-9.2)**의 선언이 뒤따른다. 비DB 시스템은 종전에 소유자가 될 수 없었다(B-9) — 기존 영역
        코드에는 비DB 시스템 선언이 없어 결과가 바뀌지 않는다.
        """
        owners: list[str] = []
        for entry in self.databases:
            system = self.system_of(entry.db_id)
            if system and system not in owners and capability in self.capabilities_of(entry.db_id):
                owners.append(system)
        for spec in self.non_db_systems():
            if spec.code not in owners and capability in spec.capabilities:
                owners.append(spec.code)
        return tuple(owners)

    # ── 비DB 시스템 (plans/125 A-1 · 121 TP-9.2) ─────────────
    def non_db_systems(self) -> tuple[SolutionSpec, ...]:
        """DB 항목이 없는 솔루션(`order` 순) — MCP·REST 로만 닿는 관측 시스템(예: APM 게이트웨이).

        활성 여부는 여기서 정하지 않는다(레지스트리는 설정을 모른다) — 엔드포인트 설정이 정한다.
        """
        families = {e.family for e in self.databases if e.family}
        return tuple(
            s for s in self.solutions()
            if s.backend != "sql" and not (s.family and s.family in families)
        )

    def is_non_db_system(self, system: str) -> bool:
        """비DB 시스템 키인가."""
        return any(s.code == system for s in self.non_db_systems())

    def is_zoned_system(self, system: str) -> bool:
        """존 그룹을 가진 다중 존 시스템인가(존 순회 조회를 쓴다) — 폴스타."""
        return any(g.solution == system for g in self.zone_groups_)

    def views_of(self, system: str) -> tuple[ViewSpec, ...]:
        """시스템의 고정 보기 표(선언 순서). SQL 시스템·미등록은 빈 튜플."""
        for spec in self.solutions_:
            if spec.code == system:
                return spec.views
        return ()

    # ── 소스 ↔ 존 (plans/87 J8 · D-287 ④) ─────────────────
    def sources_of(self, system: str) -> tuple[SourceSpec, ...]:
        """시스템의 소스 표(선언 순서). 미등록·소스 없음은 빈 튜플."""
        for spec in self.solutions_:
            if spec.code == system:
                return spec.sources
        return ()

    def alarm_source(self, db_id: str) -> tuple[str, SourceSpec] | None:
        """알람 dbId `{family}_{소스 id}` → (시스템 코드, 소스). 못 풀면 None(= 존 없음).

        family·sources가 있는 솔루션만 본다 — 벤더 문자열이 아니라 레지스트리 family로 판정한다.
        `db_id == family`(게이트웨이 단일 설정 `default`)는 경고 없이 None이다. family 접두인데 표에
        없는 소스 id면 id별 경고 1회 후 None — 조용히 버리지 않고 존 없음으로 다룬다(M-4).
        """
        if not db_id or self.get(db_id) is not None:
            return None
        for spec in self.solutions_:
            if not spec.family or not spec.sources:
                continue
            prefix = f"{spec.family}_"
            if not db_id.startswith(prefix):
                continue
            for source in spec.sources:
                if db_id == prefix + source.id:
                    return spec.code, source
            if db_id not in _WARNED_UNKNOWN_SOURCES:
                _WARNED_UNKNOWN_SOURCES.add(db_id)
                logger.warning(
                    "레지스트리에 없는 %s 소스 — 존 없음으로 다룬다"
                    "(solutions[%s].sources 확인): db_id=%s",
                    spec.code, spec.code, db_id,
                )
            return None
        return None

    def system_db_ids(self, system: str) -> tuple[str, ...]:
        """소유 시스템에 속한 등록 db_id(레지스트리 선언 순서)."""
        return tuple(e.db_id for e in self.databases if self.system_of(e.db_id) == system)

    def system_label(self, system: str) -> str:
        """소유 시스템의 사용자 표시 이름(솔루션 라벨 · DB 표시명 · 없으면 키)."""
        for spec in self.solutions_:
            if spec.code == system:
                return spec.label or system
        entry = self.get(system)
        return (entry.display_name or system) if entry else system

    # ── 위치·제품 어휘 ─────────────────────────────────────
    def location_terms(self) -> tuple[str, ...]:
        """모든 위치 표면어를 선언 순서로 반환한다."""
        return tuple(loc.term for loc in self.locations)

    def location_db_hints(self) -> dict[str, tuple[str, ...]]:
        """db_id → 그 DB를 **배타적으로** 지목하는 위치 표면어 목록.

        여러 DB를 포괄하는 존 표면어(예: "공동존")는 특정 DB를 지목하지 않으므로
        제외된다. 위치 신호만으로 대상 DB를 결정적으로 고르는 경로가 소비한다.
        """
        out: dict[str, list[str]] = {}
        for loc in self.locations:
            if not loc.is_exclusive:
                continue
            out.setdefault(loc.db_ids[0], []).append(loc.term)
        return {db_id: tuple(terms) for db_id, terms in out.items()}

    def excluding_region_terms(self) -> dict[str, tuple[str, ...]]:
        """db_id → 그 DB를 "배제"하는 경쟁 지역 표면어 목록.

        같은 제품군(family)의 **다른** DB를 배타적으로 지목하는 표면어들이다. 어떤
        힌트가 이 표면어를 포함하면 그 힌트는 해당 db_id를 가리키지 않는다. 제품군이
        없는(단독) DB는 경쟁 상대가 없어 빈 목록이 되고, 소비처는 배제를 적용하지 않는다.
        """
        exclusive = self.location_db_hints()
        out: dict[str, tuple[str, ...]] = {}
        for entry in self.databases:
            if not entry.family:
                continue
            siblings = [
                e.db_id for e in self.databases
                if e.family == entry.family and e.db_id != entry.db_id
            ]
            terms: list[str] = []
            for sib in siblings:
                for term in exclusive.get(sib, ()):
                    if term not in terms:
                        terms.append(term)
            out[entry.db_id] = tuple(terms)
        return out

    def product_terms(self) -> tuple[str, ...]:
        """제품명 단독 토큰(지역 변별력 없음)을 제품군 선언 순서로 반환한다."""
        terms: list[str] = []
        for fam in self.families:
            for term in fam.product_terms:
                if term not in terms:
                    terms.append(term)
        return tuple(terms)

    def db_signal_terms(self) -> tuple[str, ...]:
        """"이번 턴에 DB를 새로 지목했다"고 볼 제품/DB 표면어.

        제품군 signal_terms + DB별 signal_terms(제품군이 없는 단독 DB용)를 합친다.
        """
        terms: list[str] = []
        for fam in self.families:
            for term in fam.signal_terms:
                if term not in terms:
                    terms.append(term)
        for entry in self.databases:
            for term in entry.signal_terms:
                if term not in terms:
                    terms.append(term)
        return tuple(terms)

    def location_signal_terms(self) -> tuple[str, ...]:
        """위치 + 환경 표면어(DB 식별 신호 승계용). 둘에 모두 있는 표면어(개발 등 · D-271)는 한 번만."""
        return tuple(dict.fromkeys(self.location_terms() + tuple(self.environment_terms)))

    def new_db_signal_terms(self) -> tuple[str, ...]:
        """위치 + 환경 + 제품/DB 표면어 + 데이터 소스 유사어 — 직전 DB 승계를 차단할 신호 전체.

        소스 유사어(plans/132 N-2 · 예: 「제니퍼」)를 쓴 턴은 다른 소스를 새로 지목한 것이라 직전
        폴스타 턴의 DB를 이어받지 않는다(S-9 ④).
        """
        terms = self.location_signal_terms() + self.db_signal_terms()
        return terms + tuple(t for t in self.source_alias_terms() if t not in terms)

    # ── 데이터 소스 유사어 (plans/132 N-1 · D-293) ─────────────
    def solution_aliases(self, system: str) -> tuple[str, ...]:
        """비DB 시스템의 이름·유사어(코드 포함 · 선언 순서). 미등록은 빈 튜플."""
        for spec in self.non_db_systems():
            if spec.code == system:
                return tuple(dict.fromkeys((spec.code, *spec.aliases)))
        return ()

    def source_alias_terms(self) -> tuple[str, ...]:
        """원문에서 결정적으로 찾을 **데이터 소스 이름·유사어**(선언 순서 · 중복 제거).

        비DB 시스템의 `aliases` + 존·제품군이 없는 단독 DB 시스템(자산관리 등)의 `aliases`다.
        폴스타처럼 존·제품군이 있는 DB의 별칭은 위치·제품 표면어(`locations`·`families`)가 이미
        다루므로 넣지 않는다(위치어 보강 D-065와 겹치지 않게). D-004 경계: 등록된 이름의 인식이지
        의도 분류가 아니다(D-004 부기 · D-281 ⑦).
        """
        terms: list[str] = []
        for spec in self.non_db_systems():
            terms.extend(spec.aliases)
        for entry in self.databases:
            if not entry.zone and not entry.family:
                terms.extend(entry.aliases)
        reserved = set(self.location_signal_terms()) | set(self.product_terms())
        return tuple(t for t in dict.fromkeys(terms) if t and t not in reserved)


# ──────────────────────────────────────────────
# 로드
# ──────────────────────────────────────────────

def _as_str_tuple(value: Any) -> tuple[str, ...]:
    """YAML 값에서 문자열 튜플을 만든다(스칼라·None 허용)."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value)


def _parse_view_args(value: Any, view_id: str) -> tuple[ViewArgSpec, ...]:
    """보기 `args:` 목록 → ViewArgSpec 튜플(plans/134 M-3). 형식이 틀리면 레지스트리 오류다."""
    specs: list[ViewArgSpec] = []
    for raw in value or []:
        if not isinstance(raw, dict) or not raw.get("name"):
            raise ValueError(f"보기 {view_id}: args 항목에 name이 없다: {raw!r}")
        kind = str(raw.get("type") or "")
        if kind not in VIEW_ARG_TYPES:
            raise ValueError(f"보기 {view_id}: 조건 {raw['name']} 형식 {kind!r} — "
                             f"{sorted(VIEW_ARG_TYPES)} 중 하나여야 한다")
        choices = tuple(str(c) for c in raw.get("choices") or ())
        if kind == "enum" and not choices:
            raise ValueError(f"보기 {view_id}: enum 조건 {raw['name']}에 choices가 없다")
        low = raw.get("min")
        default = raw.get("default")
        if default is not None and (not isinstance(default, (str, int, bool))
                                    or (kind == "enum" and str(default) not in choices)):
            raise ValueError(f"보기 {view_id}: 조건 {raw['name']} 기본값 {default!r}이"
                             " 형식·선택지 밖이다")
        targeted = tuple(str(c) for c in raw.get("targeted_choices") or ())
        if targeted and (kind != "enum" or not set(targeted) <= set(choices)):
            raise ValueError(f"보기 {view_id}: 조건 {raw['name']} targeted_choices는 enum 선택지의"
                             " 부분집합이어야 한다")
        verified = tuple(raw.get("verified_values") or ())
        if verified and (kind != "int" or not all(
                isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in verified)
                or list(verified) != sorted(set(verified))):
            raise ValueError(f"보기 {view_id}: 조건 {raw['name']} verified_values는 int 조건의"
                             " 양의 정수 오름차순 목록이어야 한다")
        specs.append(ViewArgSpec(
            name=str(raw["name"]),
            type=kind,
            choices=choices,
            min=int(low) if low is not None else None,
            catalog=str(raw["catalog"]) if raw.get("catalog") else None,
            tool_arg=str(raw["tool_arg"]) if raw.get("tool_arg") else None,
            label=str(raw.get("label", "")),
            required=bool(raw.get("required", False)),
            default=default,
            targeted_choices=targeted,
            verified_values=verified,
        ))
    return tuple(specs)


def _parse_view_notices(value: Any, view_id: str) -> tuple[str, ...]:
    """보기 `notices:` → 고지 kind 튜플.

    kind 는 고지 표(`src/domain/disclosure.KIND_TABLE`)에 있어야 한다.
    """
    from src.domain.disclosure import KIND_TABLE

    kinds = tuple(str(x) for x in value or ())
    unknown = [k for k in kinds if k not in KIND_TABLE]
    if unknown:
        raise ValueError(f"보기 {view_id}: 모르는 고지 kind {unknown}")
    return kinds


def _parse_views(value: Any) -> tuple[ViewSpec, ...]:
    """솔루션 `views:` 목록 → ViewSpec 튜플(id 없는 항목은 버린다)."""
    views: list[ViewSpec] = []
    for raw in value or []:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        view_id = str(raw["id"])
        window = str(raw.get("window") or "current")
        if window not in VIEW_WINDOWS:
            raise ValueError(f"보기 {view_id}: window {window!r} — {sorted(VIEW_WINDOWS)} 중 하나")
        fixed = raw.get("fixed_args") or {}
        if not isinstance(fixed, dict):
            raise ValueError(f"보기 {view_id}: fixed_args는 매핑이어야 한다")
        target = str(raw.get("target") or "")
        reference = str(raw.get("reference") or "")
        if target not in VIEW_TARGETS:
            raise ValueError(f"보기 {view_id}: target {target!r} — {sorted(VIEW_TARGETS)} 중 하나")
        if target and raw.get("required_input"):
            raise ValueError(f"보기 {view_id}: target과 required_input을 함께 쓸 수 없다")
        if (target == "reference") != bool(reference) or (
                reference and reference not in VIEW_REFERENCES):
            raise ValueError(f"보기 {view_id}: target reference에는 reference"
                             f"({sorted(VIEW_REFERENCES)} 중 하나)가 필요하다")
        target_arg = str(raw.get("target_arg") or "")
        if (target == "named") != bool(target_arg) or (
                target_arg and target_arg not in VIEW_TARGET_ARGS):
            raise ValueError(f"보기 {view_id}: target named에는 target_arg"
                             f"({sorted(VIEW_TARGET_ARGS)} 중 하나)가 필요하다")
        views.append(ViewSpec(
            id=view_id,
            label=str(raw.get("label", "")),
            capability=str(raw.get("capability", "")),
            tool=str(raw.get("tool", "")),
            required_input=str(raw.get("required_input", "")),
            first_hop=bool(raw.get("first_hop", False)),
            limit=str(raw.get("limit", "")),
            window=window,
            fixed_args=dict(fixed),
            args=_parse_view_args(raw.get("args"), view_id),
            examples=tuple(str(x) for x in raw.get("examples") or ()),
            notices=_parse_view_notices(raw.get("notices"), view_id),
            target=target,
            reference=reference,
            target_arg=target_arg,
        ))
    return tuple(views)


def _parse_sources(
    value: Any, *, solution: str, declared_zones: set[str]
) -> tuple[SourceSpec, ...]:
    """솔루션 `sources:` 목록 → SourceSpec 튜플(plans/87 J8 · D-287 ④).

    소스는 알람 존 판정(RBAC)의 정본이라 틀린 항목을 조용히 버리지 않는다 — 버리면 그 소스 알람이
    존 없음으로 떨어져 존 구독자에게 가지 않는다. zone 빈 값은 허용한다(존 없는 소스).
    `terms`(plans/147 · D-322)는 형식만 본다 — 같은 단어를 여러 소스에 두는 것은 정상이다.

    Raises:
        RegistryError: 목록·매핑 아님 · id 없음·형식 위반·예약어 · 같은 솔루션 안 id 중복 ·
            미선언 존 참조 · terms 형식 위반(목록 아님·문자열 아닌 값·빈 단어·같은 행 안 중복)
    """
    if value is None:
        return ()
    if not isinstance(value, list):
        raise RegistryError(f"solutions[{solution}].sources는 목록이어야 합니다.")
    sources: list[SourceSpec] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict) or not raw.get("id"):
            raise RegistryError(f"solutions[{solution}].sources 항목에 id가 없습니다: {raw!r}")
        sid = str(raw["id"])
        if not _SOURCE_ID_RE.match(sid):
            raise RegistryError(
                f"solutions[{solution}].sources id '{sid}' 형식 위반"
                "(소문자 슬러그 [a-z][a-z0-9_]{0,15})."
            )
        if sid in _RESERVED_SOURCE_IDS:
            raise RegistryError(f"solutions[{solution}].sources id '{sid}'는 예약어입니다.")
        if sid in seen:
            raise RegistryError(f"solutions[{solution}].sources id '{sid}'가 중복됐습니다.")
        seen.add(sid)
        zone = str(raw.get("zone") or "")
        if zone and zone not in declared_zones:
            raise RegistryError(
                f"solutions[{solution}].sources '{sid}'가 미선언 존 '{zone}'를 참조합니다."
            )
        where = f"solutions[{solution}].sources '{sid}'"
        terms = _parse_source_terms(raw.get("terms"), where=where)
        sources.append(
            SourceSpec(id=sid, label=str(raw.get("label", "")), zone=zone, terms=terms)
        )
    return tuple(sources)


def _parse_source_terms(value: Any, *, where: str) -> tuple[str, ...]:
    """소스 `terms:` 목록 → 단어 튜플(plans/147 · D-322). 없으면 빈 튜플(하위호환).

    Raises:
        RegistryError: 목록 아님 · 문자열 아닌 값 · 빈 단어(공백 제거 후) · 같은 행 안 중복 단어
    """
    if value is None:
        return ()
    if not isinstance(value, list):
        raise RegistryError(f"{where} terms는 목록이어야 합니다.")
    terms: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            raise RegistryError(f"{where} terms에 문자열이 아닌 값이 있습니다: {raw!r}")
        term = raw.strip()
        if not term:
            raise RegistryError(f"{where} terms에 빈 단어가 있습니다.")
        if term in terms:
            raise RegistryError(f"{where} terms에 '{term}'가 중복됐습니다.")
        terms.append(term)
    return tuple(terms)


def parse_registry(data: dict[str, Any]) -> DBRegistry:
    """레지스트리 dict(YAML 파싱 결과)를 DBRegistry로 변환한다.

    Args:
        data: `config/db_registry.yaml`을 파싱한 딕셔너리

    Returns:
        DBRegistry 인스턴스

    Raises:
        RegistryError: 필수 필드(db_id) 누락 · 솔루션 `sources` 검증 실패(plans/87 J8) 등 구조 오류
    """
    if not isinstance(data, dict):
        raise RegistryError("레지스트리 최상위 구조가 매핑이 아닙니다.")

    zones = tuple(
        ZoneSpec(code=str(z["code"]), label=str(z.get("label", "")))
        for z in (data.get("zones") or [])
        if isinstance(z, dict) and z.get("code")
    )
    families = tuple(
        FamilySpec(
            name=str(f["name"]),
            product_terms=_as_str_tuple(f.get("product_terms")),
            signal_terms=_as_str_tuple(f.get("signal_terms")),
        )
        for f in (data.get("families") or [])
        if isinstance(f, dict) and f.get("name")
    )
    locations = tuple(
        LocationSpec(term=str(loc["term"]), db_ids=_as_str_tuple(loc.get("db_ids")))
        for loc in (data.get("locations") or [])
        if isinstance(loc, dict) and loc.get("term")
    )

    zone_code_set = {z.code for z in zones}
    solutions = tuple(
        SolutionSpec(
            code=str(raw["code"]),
            label=str(raw.get("label", "")),
            order=int(raw.get("order", 0)),
            backend=str(raw.get("backend", "sql")),
            family=str(raw.get("family", "")),
            capabilities=_as_str_tuple(raw.get("capabilities")),
            requires=_as_str_tuple(raw.get("requires")),
            views=_parse_views(raw.get("views")),
            sources=_parse_sources(
                raw.get("sources"), solution=str(raw["code"]), declared_zones=zone_code_set
            ),
            aliases=_as_str_tuple(raw.get("aliases")),
        )
        for raw in data.get("solutions") or []
        if isinstance(raw, dict) and raw.get("code")
    )
    zone_groups = tuple(
        ZoneGroupSpec(
            code=str(raw["code"]),
            label=str(raw.get("label", "")),
            solution=str(raw.get("solution", "")),
            zones=_as_str_tuple(raw.get("zones")),
            query_order=int(raw.get("query_order", 0)),
        )
        for raw in data.get("zone_groups") or []
        if isinstance(raw, dict) and raw.get("code")
    )

    databases: list[DBEntry] = []
    for raw in data.get("databases") or []:
        if not isinstance(raw, dict):
            continue
        db_id = raw.get("db_id")
        if not db_id:
            raise RegistryError("databases 항목에 db_id가 없습니다.")
        if not raw.get("enabled", True):
            continue
        databases.append(
            DBEntry(
                db_id=str(db_id),
                display_name=str(raw.get("display_name", "")),
                description=str(raw.get("description", "")),
                aliases=_as_str_tuple(raw.get("aliases")),
                env_connection_key=str(raw.get("env_connection_key", "")),
                env_type_key=str(raw.get("env_type_key", "")),
                engine=str(raw.get("engine", "postgresql")),
                db_schema=str(raw.get("db_schema", "")),
                family=str(raw.get("family", "")),
                zone=str(raw.get("zone", "")),
                signal_terms=_as_str_tuple(raw.get("signal_terms")),
                capabilities=_as_str_tuple(raw.get("capabilities")),
                description_locked=bool(raw.get("description_locked", False)),
                allow_hangul_identifiers=bool(raw.get("allow_hangul_identifiers", False)),
                label_null_group_keys=bool(raw.get("label_null_group_keys", False)),
            )
        )

    registered = {e.db_id for e in databases}
    for loc in locations:
        for db_id in loc.db_ids:
            if db_id not in registered:
                logger.warning(
                    "레지스트리 위치 '%s'가 미등록 db_id '%s'를 참조합니다.", loc.term, db_id
                )
    declared_solutions = {s.code for s in solutions}
    for group in zone_groups:
        if group.solution and group.solution not in declared_solutions:
            logger.warning(
                "레지스트리 존 그룹 '%s'가 미선언 솔루션 '%s'를 참조합니다.",
                group.code, group.solution,
            )
        for zcode in group.zones:
            if zcode not in {z.code for z in zones}:
                logger.warning(
                    "레지스트리 존 그룹 '%s'가 미선언 존 '%s'를 참조합니다.",
                    group.code, zcode,
                )

    capability_specs = tuple(
        CapabilitySpec(
            code=str(raw["code"]),
            label=str(raw.get("label", "")),
            ambiguous_owner=bool(raw.get("ambiguous_owner", False)),
            active_only=bool(raw.get("active_only", False)),
        )
        for raw in data.get("capabilities") or []
        if isinstance(raw, dict) and raw.get("code")
    )

    declared_zones = {z.code for z in zones}
    for entry in databases:
        if entry.zone and entry.zone not in declared_zones:
            logger.warning(
                "레지스트리 DB '%s'가 미선언 존 '%s'를 참조합니다(zones에 추가 필요).",
                entry.db_id, entry.zone,
            )

    entity_edges = tuple(
        EdgeSpec(
            id=str(raw["id"]),
            from_facet=str(raw.get("from", "")),
            to_facet=str(raw.get("to", "")),
            owner=str(raw.get("owner", "")),
            via=str(raw.get("via", "")),
            grades=_as_str_tuple(raw.get("grades")),
        )
        for raw in data.get("entity_edges") or []
        if isinstance(raw, dict) and raw.get("id") and raw.get("from") and raw.get("to")
    )

    return DBRegistry(
        version=int(data.get("version", 1)),
        zones=zones,
        solutions_=solutions,
        zone_groups_=zone_groups,
        families=families,
        environment_terms=_as_str_tuple(data.get("environment_terms")),
        locations=locations,
        databases=tuple(databases),
        capabilities_=capability_specs,
        entity_edges_=entity_edges,
    )


def load_registry(path: str | Path | None = None) -> DBRegistry:
    """레지스트리 YAML을 읽어 DBRegistry를 만든다(캐시 없음 — 테스트/재적재용).

    Args:
        path: 레지스트리 파일 경로(기본 `config/db_registry.yaml`)

    Returns:
        DBRegistry 인스턴스

    Raises:
        RegistryError: 파일 부재 또는 파싱 실패
    """
    target = Path(path) if path else REGISTRY_PATH
    if not target.exists():
        raise RegistryError(f"DB 레지스트리 파일이 없습니다: {target}")
    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise RegistryError(f"DB 레지스트리 파싱 실패({target}): {e}") from e
    return parse_registry(data or {})


@lru_cache(maxsize=1)
def get_registry() -> DBRegistry:
    """프로세스 단위로 캐시된 레지스트리 정본을 반환한다."""
    return load_registry()


def reload_registry() -> DBRegistry:
    """레지스트리 캐시를 비우고 다시 읽는다(운영 중 갱신·테스트용)."""
    get_registry.cache_clear()
    return get_registry()


def _entry_flag(db_id: str | None, attr: str, what: str) -> bool:
    """`db_id` 레지스트리 항목의 bool 필드 — 미등록·빈 값·적재 실패는 False(현행 동작)."""
    if not db_id:
        return False
    try:
        entry = get_registry().get(db_id)
    except RegistryError as e:
        logger.warning("%s 조회 실패(현행 동작 유지): db_id=%s · %s", what, db_id, e)
        return False
    return bool(entry is not None and getattr(entry, attr, False))


def hangul_identifiers_allowed(db_id: str | None) -> bool:
    """`db_id`의 레지스트리 항목이 한글 식별자를 허용하는지(plans/137).

    미등록·빈 값·레지스트리 적재 실패는 모두 False(= 현행 한글 가드)다 — 검증을 느슨하게 하는
    설정이라 확인할 수 없으면 닫힌 쪽으로 둔다. 적재 실패는 사유를 로그로 남긴다.
    """
    return _entry_flag(db_id, "allow_hangul_identifiers", "한글 식별자 정책")


def null_group_label_enabled(db_id: str | None) -> bool:
    """`db_id`의 집계 결과 표에서 NULL 묶음 기준을 「(값 없음)」으로 표시하는지(plans/137 W13).

    미등록·빈 값·적재 실패는 False(= 현행 빈칸 표시)다.
    """
    return _entry_flag(db_id, "label_null_group_keys", "NULL 묶음 표시 정책")
