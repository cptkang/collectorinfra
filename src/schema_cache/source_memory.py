"""소스 선택 기억 — 사용자가 확인한 「질의 → 데이터 소스」 사례 (plans/132 W5 · §6.5 · G-10~G-12).

소스 선택 칩에서 고른 결과·「다른 소스로 보기」 정정·관리자 정리 사례를 저장해 두었다가, 소스가
모호한 질의(소유 소스 2개+ · 명시 없음)에서 **칩을 띄우기 전에** 참고한다. 판정 순서 안의 자리는
`명시 소스 > 단독 소유 > [기억] > 칩`이다 — 명시·단독 소유 판정은 뒤집지 않는다.

원칙(설계 근거 — `plans/132` §6.5):

- **쓰기 게이트**(오염 자기강화 차단 · D-151 C3 · D-133 「자동 편입 금지」): 출처는
  `user_choice`(칩 선택) · `feedback`(「다른 소스로 보기」 정정) · `curated`(관리자 정리·시드)
  셋뿐이다. 시스템이 스스로 고른 소스(LLM 판정·폴백)는 출처 어휘에 없어 `build_case`가 거부한다.
- **범위**(G-11): 개인(`user:<id>`) 기본 · 조직 공용(`org`)은 관리자 승격과 시드로만.
- **TTL**(sliding · D-151 선례): `ROUTER_SOURCE_MEMORY_TTL_DAYS` — **0이면 기능 전체 off**(읽기·
  쓰기 0 — 현행과 비트 동일 · 신규 `enable_*` 없음 · D-162). 사용(적용)이 곧 재확인이라 쓰일 때
  연장한다.
- **무효화**: 레지스트리 지문(소스 등록·영역 소유)이 바뀌면 그 전에 쓴 사례는 쓰지 않는다. 사용
  시점의 권한·활성 재필터는 호출부 몫(후보에 없는 소스를 가리키는 사례는 고르지 않는다).
- **검색 계단**(D-084 · D-133): ① 정규화 일치 → ② 어휘·퍼지(질의 이력과 같은 `query_similarity`).
  ③ 임베딩은 두지 않는다 — ①② 적중률을 잰 뒤 정한다(「측정 선행」). 임베딩 단독 확정 금지는
  구조상 지켜진다.
- **저장소**(G-10 (a)): Redis Hash `source_memory:{scope}`(필드 = 사례 id) + 인프로세스 검색.
  시드(`config/source_memory_seeds.yaml` · git 정본)는 기동 후 첫 사용 때 읽어 `org` 사례로 쓴다.

계층: infrastructure (`src.schema_cache`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Iterable, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from src.schema_cache.query_history import query_similarity
from src.security.pii_filter import scrub_pii

logger = logging.getLogger(__name__)

#: Redis 키 접두사 — 전체 키는 `source_memory:{scope}`(Hash: 사례 id → 사례 JSON).
SOURCE_MEMORY_KEY_PREFIX = "source_memory:"
#: 쓰기 허용 출처 — 시스템 판정 출처는 없다(쓰기 게이트).
ORIGINS: tuple[str, ...] = ("user_choice", "feedback", "curated")
SCOPE_ORG = "org"
#: 어휘·퍼지 단계 확정 임계(미만 = 기억 없음 → 칩). 질의 이력(0.35)보다 훨씬 높다 — 소스를 대신
#: 고르는 일이라 틀린 적중의 비용이 크다(K-6). 0.8이면 「인스턴스 목록 보여줘」가 시드 「WAS
#: 인스턴스 목록 보여줘」에 붙었다(F1 0.857 — 소스를 가르는 「WAS」가 빠진 질의). 운영값은 W5
#: 측정 뒤 정한다.
MIN_SCORE = 0.9
#: 시드 정본(git).
SEEDS_PATH = Path(__file__).resolve().parents[2] / "config" / "source_memory_seeds.yaml"
#: 식별자 일반화 자리 표시.
IDENTIFIER_SLOT = "<대상>"

_WS = re.compile(r"\s+")


def user_scope(user_id: str | None) -> str | None:
    """개인 범위 키(사용자 id가 없으면 None — 개인 기억 없음)."""
    return f"user:{user_id}" if user_id else None


def normalize_case_text(text: str, identifiers: Iterable[str] = ()) -> str:
    """사례 질의 정규화 — 민감값 마스킹(`scrub_pii`) · 입력 파서가 뽑은 식별자 자리 일반화 ·
    공백 정리."""
    out = scrub_pii(str(text or ""))
    values = {str(v) for v in identifiers if str(v or "").strip()}
    for value in sorted(values, key=len, reverse=True):
        out = out.replace(value, IDENTIFIER_SLOT)
    return _WS.sub(" ", out).strip()


def make_case_id(scope: str, text: str) -> str:
    """같은 범위·같은 정규화 질의는 같은 사례다(정정 = 갱신)."""
    return hashlib.sha256(f"{scope}\n{text}".encode()).hexdigest()[:16]


def build_case(
    text: str, sources: Sequence[str], *, origin: str, scope: str, registry_fp: str,
    areas: Sequence[str] = (), now: float | None = None,
) -> dict[str, Any]:
    """사례 1건 — 유일한 쓰기 형식(출처 게이트).

    Raises:
        ValueError: 출처가 허용 어휘 밖(시스템 판정 등) · 질의·소스·범위가 비었다
    """
    if origin not in ORIGINS:
        raise ValueError(
            f"소스 선택 기억 출처는 {ORIGINS} 만 허용한다(시스템 판정 저장 금지): {origin!r}")
    picked = [str(s) for s in dict.fromkeys(sources) if str(s or "").strip()]
    if not str(text or "").strip() or not picked or not scope:
        raise ValueError("소스 선택 기억 사례에는 질의·소스·범위가 필요하다")
    stamp = time.time() if now is None else now
    return {
        "case_id": make_case_id(scope, text), "text": text, "areas": list(areas),
        "sources": picked, "origin": origin, "scope": scope, "registry_fp": registry_fp,
        "created_at": stamp, "last_used_at": stamp, "use_count": 0,
    }


def registry_fingerprint(registry: Any) -> str:
    """소스 등록·영역 소유 지문 — 바뀌면 그 전에 쓴 사례를 쓰지 않는다(무효화)."""
    owners = sorted(
        (spec.code, ",".join(registry.capability_owners(spec.code)))
        for spec in registry.capability_specs()
    )
    systems = sorted({registry.system_of(d) or d for d in registry.db_ids()}
                     | {s.code for s in registry.non_db_systems()})
    payload = json.dumps({"owners": owners, "systems": systems}, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _alive(case: dict[str, Any], *, ttl_seconds: int, registry_fp: str, now: float) -> bool:
    if case.get("registry_fp") != registry_fp:
        return False
    if case.get("origin") == "curated" and case.get("scope") == SCOPE_ORG:
        return True  # 관리자 정리·시드는 만료하지 않는다(삭제로만 없앤다)
    return now - float(case.get("last_used_at") or 0) <= ttl_seconds


def search_cases(
    cases: Iterable[dict[str, Any]], text: str, *, candidates: Sequence[str],
    ttl_seconds: int, registry_fp: str, now: float | None = None,
) -> tuple[dict[str, Any], float, str] | None:
    """기억 사례 중 이 질의에 쓸 것 1건 — (사례, 점수, 단계) 또는 None(기억 없음 → 칩).

    후보(권한 안 활성 소유 소스) 밖 소스를 가리키는 사례·만료·지문 불일치 사례는 고르지 않는다.
    입력 순서가 우선순위다(호출부가 개인 → 조직 순으로 넘긴다). 단계 ① 정규화 일치가 ② 어휘·퍼지보다
    앞선다.
    """
    stamp = time.time() if now is None else now
    usable = [
        c for c in cases
        if _alive(c, ttl_seconds=ttl_seconds, registry_fp=registry_fp, now=stamp)
        and any(s in candidates for s in c.get("sources") or [])
    ]
    for case in usable:
        if case.get("text") == text:
            return case, 1.0, "exact"
    best: tuple[dict[str, Any], float, str] | None = None
    for case in usable:
        score = query_similarity(text, str(case.get("text") or ""))
        if score >= MIN_SCORE and (best is None or score > best[1]):
            best = (case, round(score, 4), "lexical")
    return best


def chosen_source(case: dict[str, Any], candidates: Sequence[str]) -> str | None:
    """사례가 가리키는 소스 중 후보 안 첫 소스."""
    return next((s for s in case.get("sources") or [] if s in candidates), None)


@lru_cache(maxsize=4)
def _seed_rows(path: str) -> tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    rows = []
    for item in raw.get("cases") or []:
        if isinstance(item, dict) and item.get("text") and item.get("sources"):
            rows.append((str(item["text"]), tuple(str(s) for s in item["sources"]),
                         tuple(str(a) for a in item.get("areas") or ())))
    return tuple(rows)


def seed_cases(registry: Any, path: Path = SEEDS_PATH) -> list[dict[str, Any]]:
    """시드 사례(`org` · `curated`) — 레지스트리에 없는 소스를 가리키는 시드는 버리고 경고한다."""
    if not path.is_file():
        return []
    known = {registry.system_of(d) or d for d in registry.db_ids()} | {
        s.code for s in registry.non_db_systems()}
    fp = registry_fingerprint(registry)
    out = []
    for text, sources, areas in _seed_rows(str(path)):
        if not set(sources) <= known:
            logger.warning("소스 선택 기억 시드 제외(미등록 소스 %s): %r", sources, text)
            continue
        out.append(build_case(normalize_case_text(text), sources, origin="curated",
                              scope=SCOPE_ORG, registry_fp=fp, areas=areas, now=0.0))
    return out


class SourceMemoryStore:
    """Redis Hash 저장소 — 연결은 스키마 캐시와 같은 Redis(`cache_manager`)를 쓴다."""

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    @staticmethod
    def key(scope: str) -> str:
        return f"{SOURCE_MEMORY_KEY_PREFIX}{scope}"

    async def load(self, scope: str) -> list[dict[str, Any]]:
        raw = await self._redis.hgetall(self.key(scope))
        out = []
        for value in (raw or {}).values():
            try:
                case = json.loads(value)
            except (TypeError, ValueError):
                continue
            if isinstance(case, dict) and case.get("case_id"):
                out.append(case)
        out.sort(key=lambda c: -float(c.get("last_used_at") or 0))
        return out

    async def save(self, case: dict[str, Any], *, ttl_seconds: int) -> None:
        """사례를 쓴다(같은 id면 덮어쓴다). 키 만료는 마지막 쓰기 + TTL — 쓰지 않는 범위는
        사라진다."""
        build_case(case["text"], case["sources"], origin=case["origin"], scope=case["scope"],
                   registry_fp=case["registry_fp"])  # 쓰기 지점 재검증(우회 조립 차단)
        key = self.key(case["scope"])
        await self._redis.hset(key, case["case_id"], json.dumps(case, ensure_ascii=False))
        if case["scope"] != SCOPE_ORG:
            await self._redis.expire(key, ttl_seconds)

    async def touch(self, case: dict[str, Any], *, ttl_seconds: int) -> None:
        """사용 = 재확인 — `last_used_at`·`use_count` 갱신(sliding)."""
        updated = {**case, "last_used_at": time.time(),
                   "use_count": int(case.get("use_count") or 0) + 1}
        await self.save(updated, ttl_seconds=ttl_seconds)

    async def delete(self, scope: str, case_id: str | None = None) -> int:
        """사례 1건(또는 범위 전체) 삭제 — 지운 건수."""
        if case_id is None:
            n = len(await self.load(scope))
            await self._redis.delete(self.key(scope))
            return n
        return int(await self._redis.hdel(self.key(scope), case_id) or 0)

    async def promote(self, scope: str, case_id: str, *, by: str) -> dict[str, Any] | None:
        """개인 사례를 조직 공용(`org` · `curated`)으로 승격한다(관리자 승인 — G-11)."""
        case = next((c for c in await self.load(scope) if c.get("case_id") == case_id), None)
        if case is None:
            return None
        org = build_case(case["text"], case["sources"], origin="curated", scope=SCOPE_ORG,
                         registry_fp=case["registry_fp"], areas=case.get("areas") or ())
        org["promoted_by"] = by
        await self.save(org, ttl_seconds=0)
        return org

    async def scopes(self) -> list[str]:
        """저장된 범위 목록(관리자 조회)."""
        keys = [k async for k in self._redis.scan_iter(match=f"{SOURCE_MEMORY_KEY_PREFIX}*")]
        return sorted(str(k)[len(SOURCE_MEMORY_KEY_PREFIX):] for k in keys)


async def open_store(app_config: Any) -> SourceMemoryStore | None:
    """연결된 저장소 또는 None(Redis 불가 — 기억 없음으로 강등 · 질의는 칩으로 간다)."""
    try:
        from src.schema_cache.cache_manager import get_cache_manager

        mgr = get_cache_manager(app_config)
        cache = mgr._redis_cache  # noqa: SLF001 — 양식 기억과 같은 연결
        if not await mgr.ensure_redis_connected() or cache is None:
            return None
        return SourceMemoryStore(cache._redis)  # noqa: SLF001
    except Exception as e:  # noqa: BLE001 — 기억 불가는 강등이지 실패가 아니다
        logger.debug("소스 선택 기억 Redis 접근 불가(강등): %s", e)
        return None


def ttl_seconds(app_config: Any) -> int:
    """기능 TTL(초) — 0이면 off."""
    days = getattr(getattr(app_config, "router", None), "source_memory_ttl_days", 0)
    return int(days) * 86400 if isinstance(days, int) and days > 0 else 0


__all__ = [
    "IDENTIFIER_SLOT",
    "MIN_SCORE",
    "ORIGINS",
    "SCOPE_ORG",
    "SourceMemoryStore",
    "build_case",
    "chosen_source",
    "make_case_id",
    "normalize_case_text",
    "open_store",
    "registry_fingerprint",
    "search_cases",
    "seed_cases",
    "ttl_seconds",
    "user_scope",
]
