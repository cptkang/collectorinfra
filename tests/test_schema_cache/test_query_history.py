"""질의 이력 저장소·검색 단위 테스트 (Plan 67 트랙 N / N2, D-133).

실제 Redis 없이 최소 Fake(Hash 연산만)로 검증한다. 검증 축:
    1. 쓰기 게이트 — 자동 편입 금지(운영 이력은 사람 확인 없이 거부), 읽기전용 SQL만.
    2. 저장/로드 라운드트립 — 동일 질의-SQL 쌍 재적재가 중복을 만들지 않음.
    3. 검색 순위 — 어휘·퍼지 유사도 정렬·임계·top_k·동점 결정성(임베딩 미사용).
    4. IP-4 계측 — 호출/적중/채택 수와 상위 유사도 누적.
"""

from __future__ import annotations

import json

import pytest

from src.schema_cache.query_history import (
    ALLOWED_SOURCES,
    QUERY_HISTORY_KEY_PREFIX,
    QueryHistoryStore,
    build_entry,
    make_entry_id,
    query_similarity,
    rank_history_entries,
    record_adoption,
    reset_search_metrics,
    search_metrics_snapshot,
    search_query_history,
    to_query_examples,
    tokenize_query,
)


class _FakeRedis:
    """Hash 연산만 지원하는 최소 async Redis 대역(fakeredis 미반입 환경)."""

    def __init__(self) -> None:
        self.store: dict[str, dict[str, str]] = {}

    async def ping(self) -> bool:
        return True

    async def hset(self, key: str, field=None, value=None, *, mapping=None) -> int:
        bucket = self.store.setdefault(key, {})
        if mapping:
            bucket.update(mapping)
            return len(mapping)
        bucket[field] = value
        return 1

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.store.get(key, {}))

    async def hlen(self, key: str) -> int:
        return len(self.store.get(key, {}))

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if self.store.pop(key, None) is not None:
                removed += 1
        return removed

    async def aclose(self) -> None:
        return None


@pytest.fixture
def store() -> QueryHistoryStore:
    """Fake Redis에 연결된 저장소."""
    return QueryHistoryStore(redis_config=None, redis_client=_FakeRedis())


@pytest.fixture(autouse=True)
def _clean_metrics():
    """계측 누적이 테스트 간 새지 않도록 초기화한다."""
    reset_search_metrics()
    yield
    reset_search_metrics()


_SELECT = "SELECT hostname FROM t WHERE flag IS NULL"


# ──────────────────────────────────────────────
# 1. 쓰기 게이트
# ──────────────────────────────────────────────

def test_build_entry_normalizes_verified_pair():
    """정상 항목은 지정 스키마(query/sql/source/verified_at)로 만들어진다."""
    entry = build_entry(
        "  서버 목록 조회  ", f"  {_SELECT}  ",
        source="gold", smq={"pattern": "A"}, verified_at="2026-07-29T00:00:00+0900",
    )
    assert entry == {
        "query": "서버 목록 조회",
        "sql": _SELECT,
        "source": "gold",
        "verified_at": "2026-07-29T00:00:00+0900",
        "smq": {"pattern": "A"},
    }


def test_build_entry_generates_verified_at_when_absent():
    """verified_at 미지정 시 현재 시각이 채워진다(빈 값 저장 금지)."""
    entry = build_entry("질의", _SELECT, source="profile_example")
    assert entry["verified_at"]


@pytest.mark.parametrize("source", ALLOWED_SOURCES)
def test_allowed_sources_are_three(source: str):
    """허용 출처는 gold/profile_example/verified_runtime 3종뿐이다."""
    assert source in ("gold", "profile_example", "verified_runtime")


@pytest.mark.parametrize(
    ("query", "sql", "source"),
    [
        ("", _SELECT, "gold"),                       # 질의 없음
        ("질의", "", "gold"),                         # SQL 없음
        ("질의", _SELECT, "runtime_success"),         # 미허용 출처
        ("질의", "UPDATE t SET a=1", "gold"),         # 쓰기 SQL
        ("질의", "DELETE FROM t", "gold"),
        ("질의", "SELECT 1; DROP TABLE t", "gold"),   # 다중문 위장
        ("질의", "-- SELECT\nINSERT INTO t VALUES(1)", "gold"),  # 주석 위장
    ],
)
def test_build_entry_rejects_invalid(query: str, sql: str, source: str):
    """빈 값·미허용 출처·쓰기 SQL은 저장 이전에 거부된다."""
    with pytest.raises(ValueError):
        build_entry(query, sql, source=source)


def test_runtime_source_requires_human_confirmation():
    """운영 실행 이력은 사람 확인 표식 없이 편입할 수 없다(자동 편입 금지)."""
    with pytest.raises(ValueError, match="사람 확인"):
        build_entry("질의", _SELECT, source="verified_runtime")

    entry = build_entry(
        "질의", _SELECT, source="verified_runtime", confirmed_by="operator-01",
    )
    assert entry["confirmed_by"] == "operator-01"


async def test_save_entries_rejects_gate_bypassing_dict(store: QueryHistoryStore):
    """build_entry를 우회한 dict도 저장 직전 재검증에서 전건 거부된다."""
    bypass = {
        "query": "질의", "sql": _SELECT,
        "source": "verified_runtime", "verified_at": "2026-07-29T00:00:00+0900",
    }
    with pytest.raises(ValueError, match="사람 확인"):
        await store.save_entries("db1", [bypass])
    assert await store.count("db1") == 0


async def test_save_entries_is_all_or_nothing(store: QueryHistoryStore):
    """한 항목이 규칙을 위반하면 정상 항목도 저장되지 않는다(부분 저장 없음)."""
    good = build_entry("정상 질의", _SELECT, source="gold")
    bad = {"query": "나쁜 질의", "sql": "DROP TABLE t", "source": "gold"}
    with pytest.raises(ValueError):
        await store.save_entries("db1", [good, bad])
    assert await store.count("db1") == 0


async def test_save_entries_requires_connection():
    """미연결 저장소는 침묵하지 않고 RuntimeError를 낸다."""
    disconnected = QueryHistoryStore(redis_config=None)
    with pytest.raises(RuntimeError, match="연결"):
        await disconnected.save_entries("db1", [build_entry("q", _SELECT, source="gold")])


# ──────────────────────────────────────────────
# 2. 저장/로드
# ──────────────────────────────────────────────

def test_redis_key_naming():
    """키는 query_history:{db_id} 형식이다."""
    assert QueryHistoryStore.key("polestar_cm_gp") == "query_history:polestar_cm_gp"
    assert QUERY_HISTORY_KEY_PREFIX == "query_history:"


async def test_save_and_load_roundtrip(store: QueryHistoryStore):
    """저장한 항목이 그대로 로드된다(항목 id 부여 포함)."""
    entries = [
        build_entry("서버 목록 조회", _SELECT, source="gold"),
        build_entry("CPU 사용률 조회", "SELECT usage FROM m", source="profile_example"),
    ]
    assert await store.save_entries("db1", entries) == 2

    loaded = await store.load_entries("db1")
    assert len(loaded) == 2
    assert {e["query"] for e in loaded} == {"서버 목록 조회", "CPU 사용률 조회"}
    assert all(e["id"] == make_entry_id(e["query"], e["sql"]) for e in loaded)


async def test_reload_same_pair_is_idempotent(store: QueryHistoryStore):
    """동일 질의-SQL 쌍의 재적재는 중복 항목을 만들지 않는다."""
    entry = build_entry("서버 목록 조회", _SELECT, source="gold")
    await store.save_entries("db1", [entry])
    await store.save_entries("db1", [entry])
    assert await store.count("db1") == 1


async def test_replace_clears_previous_entries(store: QueryHistoryStore):
    """replace=True는 기존 이력을 지우고 새로 쓴다."""
    await store.save_entries("db1", [build_entry("옛 질의", _SELECT, source="gold")])
    await store.save_entries(
        "db1", [build_entry("새 질의", _SELECT, source="gold")], replace=True,
    )
    loaded = await store.load_entries("db1")
    assert [e["query"] for e in loaded] == ["새 질의"]


async def test_load_skips_corrupt_and_incomplete_records(store: QueryHistoryStore):
    """깨진 JSON·필수 필드 누락 항목은 건너뛰고 나머지를 반환한다."""
    key = QueryHistoryStore.key("db1")
    fake = store._redis  # noqa: SLF001 — Fake 주입 검증
    fake.store[key] = {
        "broken": "{not json",
        "partial": json.dumps({"query": "질의만 있음"}, ensure_ascii=False),
        "ok": json.dumps(
            build_entry("정상 질의", _SELECT, source="gold"), ensure_ascii=False,
        ),
    }
    loaded = await store.load_entries("db1")
    assert [e["query"] for e in loaded] == ["정상 질의"]


async def test_delete_removes_db_history(store: QueryHistoryStore):
    """명시 삭제는 해당 DB 이력만 제거한다."""
    await store.save_entries("db1", [build_entry("질의", _SELECT, source="gold")])
    await store.save_entries("db2", [build_entry("질의", _SELECT, source="gold")])
    assert await store.delete("db1") is True
    assert await store.count("db1") == 0
    assert await store.count("db2") == 1


# ──────────────────────────────────────────────
# 3. 어휘·퍼지 검색 순위
# ──────────────────────────────────────────────

def test_tokenize_drops_short_tokens_and_duplicates():
    """1글자 토큰("및"·"a")은 제외하고 중복은 접는다."""
    assert tokenize_query("서버 CPU, CPU 사용률 및 a") == ["서버", "CPU", "사용률"]


def test_similarity_identical_and_unrelated():
    """동일 질의는 1.0, 어휘가 겹치지 않으면 0.0이다."""
    assert query_similarity("서버 목록 조회", "서버 목록 조회") == pytest.approx(1.0)
    assert query_similarity("서버 목록 조회", "지난달 알람 건수") == 0.0


def test_similarity_absorbs_korean_particles():
    """조사 변형("사용률을"↔"사용률")은 부분어 단계가 흡수한다."""
    assert query_similarity("메모리 사용률 알려줘", "메모리 사용률을 알려줘") > 0.9


def test_rank_orders_by_similarity_and_applies_threshold():
    """유사도 내림차순 정렬 + 임계 미만 제외 + top_k 절단."""
    entries = [
        {"query": "지난달 알람 건수", "sql": _SELECT},
        {"query": "전체 서버 목록 조회", "sql": _SELECT},
        {"query": "서버 목록 조회해줘", "sql": _SELECT},
    ]
    ranked = rank_history_entries(entries, "서버 목록 조회", top_k=2, min_score=0.35)
    assert [e["query"] for e in ranked] == ["서버 목록 조회해줘", "전체 서버 목록 조회"]
    assert ranked[0]["score"] >= ranked[1]["score"]

    assert rank_history_entries(entries, "서버 목록 조회", top_k=0, min_score=0.0) == []
    assert rank_history_entries(entries, "완전 무관한 표현", top_k=3, min_score=0.35) == []


def test_rank_is_deterministic_on_ties():
    """동점 항목은 질의 문자열 순으로 고정된다(비결정 순서 금지)."""
    entries = [
        {"query": "나 서버 목록 조회", "sql": _SELECT},
        {"query": "가 서버 목록 조회", "sql": _SELECT},
    ]
    ranked = [e["query"] for e in rank_history_entries(
        entries, "서버 목록 조회", top_k=2, min_score=0.1,
    )]
    assert ranked == ["가 서버 목록 조회", "나 서버 목록 조회"]


def test_rank_skips_entries_missing_sql():
    """SQL 없는 항목은 예시가 될 수 없어 제외한다."""
    entries = [{"query": "서버 목록 조회", "sql": ""}]
    assert rank_history_entries(entries, "서버 목록 조회", top_k=3, min_score=0.1) == []


def test_to_query_examples_maps_to_fewshot_shape():
    """검색 결과가 few-shot 블록 입력(question/sql) 형태로 변환된다."""
    results = [{"query": "서버 목록", "sql": f"{_SELECT}\n", "score": 0.9}]
    assert to_query_examples(results) == [{"question": "서버 목록", "sql": _SELECT}]


async def test_search_query_history_returns_ranked(store: QueryHistoryStore):
    """저장소 검색이 유사 항목만 상위 순으로 돌려준다."""
    await store.save_entries("db1", [
        build_entry("서버 목록 조회해줘", _SELECT, source="gold"),
        build_entry("지난달 알람 건수", "SELECT count(*) FROM a", source="gold"),
    ])
    results = await search_query_history(
        "db1", "서버 목록 조회", top_k=3, min_score=0.35, store=store,
    )
    assert [r["query"] for r in results] == ["서버 목록 조회해줘"]


async def test_search_returns_empty_for_blank_inputs(store: QueryHistoryStore):
    """db_id·질의가 비면 검색하지 않는다."""
    assert await search_query_history("", "질의", top_k=3, min_score=0.1, store=store) == []
    assert await search_query_history("db1", "  ", top_k=3, min_score=0.1, store=store) == []


# ──────────────────────────────────────────────
# 4. IP-4 계측
# ──────────────────────────────────────────────

async def test_metrics_track_hit_rate_and_adoption(store: QueryHistoryStore):
    """검색 호출·적중·채택이 누적되어 임베딩 승격 판단 근거가 된다."""
    await store.save_entries("db1", [
        build_entry("서버 목록 조회해줘", _SELECT, source="gold"),
    ])
    hit = await search_query_history(
        "db1", "서버 목록 조회", top_k=3, min_score=0.35, store=store,
    )
    await search_query_history(
        "db1", "완전 무관한 표현", top_k=3, min_score=0.35, store=store,
    )
    record_adoption("db1", len(hit))

    snapshot = search_metrics_snapshot()
    assert snapshot["searches"] == 2
    assert snapshot["hits"] == 1
    assert snapshot["misses"] == 1
    assert snapshot["hit_rate"] == pytest.approx(0.5)
    assert snapshot["candidates"] == 2  # 후보 스캔 누적(1건 × 2회)
    assert snapshot["adoptions"] == 1
    assert snapshot["adopted_examples"] == 1
    assert 0.0 < snapshot["top_score_max"] <= 1.0
    assert snapshot["by_db"]["db1"] == {"searches": 2, "hits": 1, "adoptions": 1}


def test_metrics_reset_clears_accumulation():
    """계측 초기화가 누적치를 비운다(측정 구간 분리)."""
    record_adoption("db1", 2)
    assert search_metrics_snapshot()["adoptions"] == 1
    reset_search_metrics()
    snapshot = search_metrics_snapshot()
    assert snapshot["adoptions"] == 0 and snapshot["by_db"] == {}


def test_record_adoption_ignores_empty_injection():
    """주입 예시가 0건이면 채택으로 세지 않는다."""
    record_adoption("db1", 0)
    assert search_metrics_snapshot()["adoptions"] == 0
