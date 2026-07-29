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
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "db_registry.yaml"


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


@dataclass(frozen=True)
class DBRegistry:
    """레지스트리 정본 전체와 파생 조회 API."""

    version: int = 1
    zones: tuple[ZoneSpec, ...] = ()
    families: tuple[FamilySpec, ...] = ()
    environment_terms: tuple[str, ...] = ()
    locations: tuple[LocationSpec, ...] = ()
    databases: tuple[DBEntry, ...] = field(default_factory=tuple)

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
        """위치 + 환경 표면어(DB 식별 신호 승계용)."""
        return self.location_terms() + tuple(self.environment_terms)

    def new_db_signal_terms(self) -> tuple[str, ...]:
        """위치 + 환경 + 제품/DB 표면어 — 직전 DB 승계를 차단할 신호 전체."""
        return self.location_signal_terms() + self.db_signal_terms()


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


def parse_registry(data: dict[str, Any]) -> DBRegistry:
    """레지스트리 dict(YAML 파싱 결과)를 DBRegistry로 변환한다.

    Args:
        data: `config/db_registry.yaml`을 파싱한 딕셔너리

    Returns:
        DBRegistry 인스턴스

    Raises:
        RegistryError: 필수 필드(db_id) 누락 등 구조 오류
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
            )
        )

    registered = {e.db_id for e in databases}
    for loc in locations:
        for db_id in loc.db_ids:
            if db_id not in registered:
                logger.warning(
                    "레지스트리 위치 '%s'가 미등록 db_id '%s'를 참조합니다.", loc.term, db_id
                )
    declared_zones = {z.code for z in zones}
    for entry in databases:
        if entry.zone and entry.zone not in declared_zones:
            logger.warning(
                "레지스트리 DB '%s'가 미선언 존 '%s'를 참조합니다(zones에 추가 필요).",
                entry.db_id, entry.zone,
            )

    return DBRegistry(
        version=int(data.get("version", 1)),
        zones=zones,
        families=families,
        environment_terms=_as_str_tuple(data.get("environment_terms")),
        locations=locations,
        databases=tuple(databases),
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
