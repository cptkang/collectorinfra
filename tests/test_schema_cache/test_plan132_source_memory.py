"""plans/132 W5 — 소스 선택 기억 저장소·검색(LLM 0 · Redis 대역).

- 쓰기 게이트: 출처는 칩 선택·정정·관리자 정리 셋뿐 — **시스템 판정 저장 0**
- 정규화: 민감값 마스킹 · 입력 파서 식별자 자리 일반화
- 검색 계단: 정규화 일치 → 어휘·퍼지(임계) · 후보(권한·활성) 밖 소스 사례 제외 · 만료·지문 무효화
- 시드: git 정본 · 미등록 소스 행 제외
- 저장소: 범위별 Hash · 덮어쓰기(정정 = 갱신) · 삭제 · 승격(조직 공용 = 관리자)
"""

from __future__ import annotations

import fnmatch
import time
from types import SimpleNamespace
from typing import Any

import pytest

from src.routing.registry import get_registry
from src.schema_cache import source_memory as sm


class FakeRedis:
    """hset/hgetall/hdel/delete/expire/scan_iter 대역."""

    def __init__(self) -> None:
        self.data: dict[str, dict[str, str]] = {}
        self.ttl: dict[str, int] = {}

    async def hset(self, key: str, field: str, value: str) -> None:
        self.data.setdefault(key, {})[field] = value

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.data.get(key, {}))

    async def hdel(self, key: str, field: str) -> int:
        return 1 if self.data.get(key, {}).pop(field, None) is not None else 0

    async def delete(self, key: str) -> int:
        return 1 if self.data.pop(key, None) is not None else 0

    async def expire(self, key: str, seconds: int) -> None:
        self.ttl[key] = seconds

    async def scan_iter(self, match: str):
        for key in list(self.data):
            if fnmatch.fnmatch(key, match):
                yield key


FP = sm.registry_fingerprint(get_registry())


def _case(text: str, sources: list[str], **kw: Any) -> dict[str, Any]:
    return sm.build_case(text, sources, origin=kw.pop("origin", "user_choice"),
                         scope=kw.pop("scope", "user:u1"), registry_fp=kw.pop("fp", FP), **kw)


# ── 쓰기 게이트 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("origin", ["llm", "system", "fallback", "classified", ""])
def test_system_judgement_is_never_stored(origin: str) -> None:
    """오염 자기강화 차단 — 시스템이 고른 소스는 출처 어휘에 없어 쓰기 자체가 거부된다."""
    with pytest.raises(ValueError, match="시스템 판정 저장 금지"):
        sm.build_case("WAS 인스턴스 목록", ["apm"], origin=origin, scope="user:u1",
                      registry_fp=FP)


@pytest.mark.asyncio
async def test_store_revalidates_at_write_point() -> None:
    store = sm.SourceMemoryStore(FakeRedis())
    forged = {**_case("q", ["apm"]), "origin": "llm"}
    with pytest.raises(ValueError):
        await store.save(forged, ttl_seconds=60)


def test_normalize_masks_and_generalizes_identifiers() -> None:
    text = sm.normalize_case_text("web01  서버의  WAS 인스턴스", identifiers=["web01"])
    assert text == f"{sm.IDENTIFIER_SLOT} 서버의 WAS 인스턴스"
    assert sm.make_case_id("user:u1", text) == sm.make_case_id("user:u1", text)
    assert sm.make_case_id("user:u1", text) != sm.make_case_id("org", text)


# ── 검색 계단 ─────────────────────────────────────────────────────────────────

def test_search_exact_then_lexical_within_candidates() -> None:
    cases = [_case("WAS 인스턴스 목록 보여줘", ["apm"]), _case("자산 담당자 목록", ["itam"])]
    hit = sm.search_cases(cases, "WAS 인스턴스 목록 보여줘", candidates=["apm", "polestar"],
                          ttl_seconds=86400, registry_fp=FP)
    assert hit is not None and hit[2] == "exact" and sm.chosen_source(hit[0], ["apm"]) == "apm"
    near = sm.search_cases(cases, "WAS 인스턴스 목록 보여줘요", candidates=["apm", "polestar"],
                           ttl_seconds=86400, registry_fp=FP)
    assert near is not None and near[2] == "lexical" and near[1] >= sm.MIN_SCORE
    assert sm.search_cases(cases, "CPU 사용률 상위 10", candidates=["apm", "polestar"],
                           ttl_seconds=86400, registry_fp=FP) is None, "임계 미만 = 기억 없음(칩)"


def test_search_refilters_by_candidates_ttl_and_registry() -> None:
    old = _case("WAS 인스턴스 목록", ["apm"], now=time.time() - 10 * 86400)
    assert sm.search_cases([old], "WAS 인스턴스 목록", candidates=["apm"], ttl_seconds=86400,
                           registry_fp=FP) is None, "만료(sliding TTL)"
    fresh = _case("WAS 인스턴스 목록", ["apm"])
    assert sm.search_cases([fresh], "WAS 인스턴스 목록", candidates=["polestar"],
                           ttl_seconds=86400, registry_fp=FP) is None, "권한·활성 밖 소스 제외"
    assert sm.search_cases([fresh], "WAS 인스턴스 목록", candidates=["apm"], ttl_seconds=86400,
                           registry_fp="other") is None, "레지스트리 지문이 바뀌면 무효"
    curated = _case("WAS 인스턴스 목록", ["apm"], origin="curated", scope=sm.SCOPE_ORG, now=0.0)
    assert sm.search_cases([curated], "WAS 인스턴스 목록", candidates=["apm"], ttl_seconds=1,
                           registry_fp=FP) is not None, "조직 정리 사례는 만료하지 않는다"


def test_personal_cases_win_over_org_by_order() -> None:
    mine = _case("WAS 인스턴스 목록", ["polestar"])
    org = _case("WAS 인스턴스 목록", ["apm"], origin="curated", scope=sm.SCOPE_ORG)
    hit = sm.search_cases([mine, org], "WAS 인스턴스 목록", candidates=["apm", "polestar"],
                          ttl_seconds=86400, registry_fp=FP)
    assert hit is not None and hit[0]["sources"] == ["polestar"]


# ── 시드 ──────────────────────────────────────────────────────────────────────

def test_seed_file_cases_are_curated_org_and_registered(tmp_path) -> None:
    seeds = sm.seed_cases(get_registry())
    assert seeds and all(c["origin"] == "curated" and c["scope"] == sm.SCOPE_ORG for c in seeds)
    bad = tmp_path / "seeds.yaml"
    bad.write_text("cases:\n  - {text: '없는 소스', sources: [nope]}\n"
                   "  - {text: 'WAS 목록', sources: [apm]}\n", encoding="utf-8")
    assert [c["sources"] for c in sm.seed_cases(get_registry(), bad)] == [["apm"]]


def test_ttl_zero_is_off() -> None:
    assert sm.ttl_seconds(SimpleNamespace(router=SimpleNamespace(source_memory_ttl_days=0))) == 0
    assert sm.ttl_seconds(SimpleNamespace()) == 0
    two_days = SimpleNamespace(router=SimpleNamespace(source_memory_ttl_days=2))
    assert sm.ttl_seconds(two_days) == 172800


# ── 저장소 ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_store_roundtrip_overwrite_delete_promote() -> None:
    redis = FakeRedis()
    store = sm.SourceMemoryStore(redis)
    await store.save(_case("WAS 인스턴스 목록", ["polestar"]), ttl_seconds=60)
    await store.save(_case("WAS 인스턴스 목록", ["apm"], origin="feedback"), ttl_seconds=60)
    [case] = await store.load("user:u1")
    assert case["sources"] == ["apm"] and case["origin"] == "feedback", "정정 = 같은 사례 갱신"
    assert redis.ttl["source_memory:user:u1"] == 60
    await store.touch(case, ttl_seconds=60)
    assert (await store.load("user:u1"))[0]["use_count"] == 1
    org = await store.promote("user:u1", case["case_id"], by="admin1")
    assert org is not None and org["scope"] == sm.SCOPE_ORG and org["origin"] == "curated"
    assert "source_memory:org" not in redis.ttl, "조직 공용은 키 만료를 두지 않는다"
    assert await store.scopes() == ["org", "user:u1"]
    assert await store.delete("user:u1", case["case_id"]) == 1
    assert await store.load("user:u1") == []
    assert await store.delete(sm.SCOPE_ORG) == 1
