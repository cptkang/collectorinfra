"""Redis 스키마 캐시 키 공간 스냅샷 — 전 키·값·TTL (plans/166 W0 · 스멜 I1).

plans/166 W0 특성화 스냅샷 — 단계 2(plans/163)만 갱신 가능, 단계 3 이후 갱신 금지
(갱신이 필요하면 리팩터가 아니다).

`SchemaCacheManager`(backend="redis") Facade · `RedisSchemaCache` 직접 호출(Facade 우회 지점 —
`schema_analyzer`·`multi_db_executor`·`excel_csv_converter`가 `_redis_cache`로 부르는 메서드) ·
`StructureStore`(같은 `schema:{db_id}:{suffix}` 키 규칙을 따로 가진다)로 대표 쓰기 시퀀스를 돌린 뒤
인메모리 페이크(`tests/mocks/async_redis.py` — fakeredis 미설치)의 **전 키**를 덤프해 고정한다.

- 값: String은 JSON이면 파싱, Hash는 필드별로 JSON이면 파싱, Set은 정렬 목록.
- TTL: 값과 분리해 키별 「없음(-1) / 초」로만 고정한다.
- 비결정 값(시각·draft_id)은 키 이름 기준으로 형식 표지(`<volatile:iso>` 등)로 바꾼다 — 값은
  버리고 「있다·형식」만 고정한다. 시계 patch 대신 정규화를 쓰는 이유: 키 공간 하위 객체로
  쪼개도(Composition) 모듈별 시계 참조 위치와 무관하게 같은 스냅샷이 나와야 한다.
- 유사어 governance 스위치는 `src.config.load_config` 대역으로 고정한다(`.env` 누수 차단).

단계: 쓰기 직후 전체 덤프 → 읽기 결과(Facade 우회 읽기 포함) → `invalidate(db)` 뒤 키 목록 →
`invalidate_all()` 뒤 키 목록. governance ON 시나리오는 유사어 Hash만 따로 고정한다.

재생성: ``UPDATE_PLAN162_SNAPSHOT=1 python -m pytest <이 파일>``
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import src.config as config_mod
from src.schema_cache.cache_manager import SchemaCacheManager
from tests.mocks.async_redis import FakeAsyncRedis, attach_fake_redis

SNAPSHOT_PATH = (
    Path(__file__).resolve().parent.parent
    / "snapshots" / "plan166_w0" / "redis_keyspace.json"
)

_DB_A = "db_a"
_DB_B = "db_b"
_FIXED_NOW = 1_790_000_000.0

# 값 안에서 시각·임의 ID를 담는 필드 이름(키 이름 기준 정규화)
_VOLATILE_FIELDS = frozenset({
    "cached_at", "created_at", "at", "last_used_ts", "draft_id",
})
# 값 전체가 시각인 String 키 접미사
_VOLATILE_KEY_SUFFIXES = ("fingerprint_checked_at",)

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_HEX12_RE = re.compile(r"^[0-9a-f]{12}$")

_SCHEMA = {
    "tables": {
        "cmm_resource": {
            "columns": [
                {"name": "name", "type": "varchar"},
                {"name": "hostname", "type": "varchar"},
            ],
            "description": "자원",
        },
        "cmm_alarm": {"columns": [{"name": "id", "type": "integer"}]},
    },
    "relationships": [
        {"from": "cmm_alarm.resource_id", "to": "cmm_resource.id"},
    ],
}

_STRUCTURE_META = {
    "patterns": [{"type": "eav", "entity_table": "cmm_resource"}],
    "query_guide": "가이드",
}


def _volatile_marker(value: Any) -> str:
    """비결정 값을 형식 표지로 바꾼다(값은 버리고 형식만 남긴다)."""
    if isinstance(value, (int, float)):
        return "<volatile:epoch>"
    if isinstance(value, str):
        if _ISO_RE.match(value):
            return "<volatile:iso>"
        if _HEX12_RE.match(value):
            return "<volatile:hex12>"
        try:
            float(value)
            return "<volatile:epoch>"
        except ValueError:
            pass
    return f"<volatile:{type(value).__name__}>"


def _scrub(value: Any) -> Any:
    """파싱된 값에서 비결정 필드를 표지로 바꾼다."""
    if isinstance(value, dict):
        return {
            k: (_volatile_marker(v) if k in _VOLATILE_FIELDS and v is not None else _scrub(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _decode(raw: str) -> Any:
    try:
        return {"json": _scrub(json.loads(raw))}
    except ValueError:
        return {"raw": raw}


def _normalize_entry(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, set):
        return {"type": "set", "members": sorted(value)}
    if isinstance(value, dict):
        return {
            "type": "hash",
            "fields": {
                f: ({"raw": _volatile_marker(v)} if f in _VOLATILE_FIELDS else _decode(v))
                for f, v in sorted(value.items())
            },
        }
    if key.endswith(_VOLATILE_KEY_SUFFIXES):
        return {"type": "string", "raw": _volatile_marker(value)}
    return {"type": "string", **_decode(value)}


async def _dump(fake: FakeAsyncRedis) -> dict[str, Any]:
    """전 키의 값·TTL을 정규화한 dict."""
    data = fake.dump()
    out: dict[str, Any] = {}
    for key in sorted(data):
        entry = _normalize_entry(key, data[key])
        entry["ttl"] = await fake.ttl(key)
        out[key] = entry
    return out


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """저장소 파일(프로필·지식·글로벌 유사어)을 읽거나 쓰지 않게 tmp로 옮긴다."""
    monkeypatch.chdir(tmp_path)


def _set_governance(monkeypatch, enabled: bool) -> None:
    """`RedisSchemaCache._governance_enabled`가 읽는 설정을 고정한다(`.env` 누수 차단)."""
    stub = SimpleNamespace(synonym=SimpleNamespace(governance=enabled))
    monkeypatch.setattr(config_mod, "load_config", lambda *a, **k: stub)


def _manager(tmp_path: Path) -> tuple[SchemaCacheManager, FakeAsyncRedis]:
    config = MagicMock()
    config.schema_cache.backend = "redis"
    config.schema_cache.cache_dir = str(tmp_path / ".cache" / "schema")
    config.schema_cache.enabled = True
    mgr = SchemaCacheManager(config)
    mgr._knowledge_root = tmp_path / "knowledge"
    assert mgr._redis_cache is not None
    fake = attach_fake_redis(mgr._redis_cache)
    return mgr, fake


async def _write_sequence(mgr: SchemaCacheManager) -> None:
    """키 공간마다 1회 이상 쓰는 대표 시퀀스(순서도 스냅샷의 일부다)."""
    rc = mgr._redis_cache
    assert rc is not None

    # --- Facade: 스키마·fingerprint·구조 메타 ---
    assert await mgr.save_schema(_DB_A, _SCHEMA, fingerprint="fp-001")
    await mgr.refresh_fingerprint_ttl(_DB_A)
    assert await mgr.save_structure_meta(_DB_A, _STRUCTURE_META)

    # --- Facade: 컬럼 설명 · 지식 적재(HSETNX + knowledge_seeded 집합) ---
    assert await mgr.save_descriptions(_DB_A, {
        "cmm_resource.name": "서버 등록명",
        "cmm_resource.hostname": "호스트명",
        "invalid_key": "무시",
    })
    await rc.add_missing_descriptions(_DB_A, {
        "cmm_resource.name": "덮지 않음",
        "cmm_alarm.id": "알람 ID",
    })

    # --- Facade: DB 설명(수동 · LLM — LLM은 수동을 덮지 못한다) ---
    assert await mgr.save_db_description(_DB_A, "테스트 DB A", origin="manual")
    assert not await mgr.save_db_description(_DB_A, "LLM 재생성", origin="llm")
    assert await mgr.save_db_description(_DB_B, "LLM 설명 B", origin="llm")

    # --- Facade: DB별 유사어(llm 저장 → operator 추가 → llm 재저장 병합 → 삭제) ---
    assert await mgr.save_synonyms(_DB_A, {
        "cmm_resource.name": ["서버명", "장비명"],
        "cmm_resource.hostname": ["호스트"],
    }, source="llm")
    assert await mgr.add_synonyms(_DB_A, "cmm_resource.name", ["등록명"], source="operator")
    assert await mgr.save_synonyms(_DB_A, {"cmm_resource.name": ["서버이름"]}, source="llm")
    assert await mgr.add_synonyms(_DB_A, "cmm_alarm.id", ["알람번호", "알람ID"])
    await mgr.remove_synonyms(_DB_A, "cmm_alarm.id", ["알람ID"])

    # --- Facade: 글로벌 유사어 · 컬럼값 유사어 ---
    assert await mgr.save_global_synonyms({
        "hostname": ["호스트명"],
        "name": {"words": ["이름"], "description": "등록명"},
    })
    assert await mgr.add_global_synonym("hostname", ["서버 호스트"])
    assert await mgr.update_global_description("hostname", "호스트 이름")
    assert await mgr.save_column_value_synonyms({
        "avail_status": {"비정상": {"op": "!=", "value": 0}},
    })

    # --- Facade 우회 지점이 쓰는 RedisSchemaCache 메서드 ---
    assert await rc.save_resource_type_synonyms({"server.Cpus": ["CPU", "씨피유"]})
    assert await rc.save_eav_name_synonyms({"OSType": ["운영체제"]})
    assert await rc.sync_known_attributes_to_eav_synonyms([
        {"name": "OSType", "synonyms": ["OS"]},
        {"name": "Vendor", "description": "제조사", "synonyms": ["제조사"]},
    ]) == 2
    await rc.save_csv_cache("a" * 64, {"Sheet1": {"sheet_name": "Sheet1", "csv_text": "a,b"}})
    assert await rc.save_column_value_index(_DB_A, {"resource_type": ["server.Cpus"]})
    assert await rc.save_form_memory("sig-1", {"answers": {"구분": "직접입력"}}, ttl_seconds=600)
    await rc.touch_form_memory("sig-1", 900)

    # --- StructureStore: 관리자 자산 · 적용본(structure_meta 키 공유 · 값 인덱스 삭제) ---
    store = mgr.structure_store
    await store.save_snapshot(_DB_A, {"snapshot": {"tables": 2}, "hash": "h1",
                                      "taken_at": "T0", "env": "local", "by": "admin"})
    await store.save_check_result(_DB_A, {"changed": False, "summary": "변경 없음"})
    draft = await store.add_draft(_DB_A, {"profile": {"tables": {}}, "by": "admin"})
    await store.update_draft(_DB_A, draft["draft_id"], status="approved")
    await store.record_step(_DB_A, "snapshot", status="done", count=2, by="admin", env="local")
    await store.add_description_draft(_DB_A, {"descriptions": {"cmm_resource.name": "초안"}})
    await store.add_asset_draft(_DB_A, {"kind": "catalog"})
    await store.save_ddl_comments(_DB_A, {"cmm_resource": "자원 테이블"})
    await store.apply_profile(
        _DB_A, {"source": "manual", "patterns": [{"type": "eav"}]},
        by="admin", reason="승인", env="local", draft_id=draft["draft_id"], field_diff=[],
    )
    # 적용본 갱신이 지운 값 인덱스와 달리 다른 DB의 TTL 키는 남는다
    assert await rc.save_column_value_index(_DB_B, {"eav_name": ["OSType"]}, ttl=120)


async def _read_sequence(mgr: SchemaCacheManager) -> dict[str, Any]:
    """쓰기 결과를 소비처 경로로 다시 읽는다(Facade 우회 읽기 포함)."""
    rc = mgr._redis_cache
    assert rc is not None
    return {
        "mgr.get_fingerprint": await mgr.get_fingerprint(_DB_A),
        "mgr.get_structure_meta": await mgr.get_structure_meta(_DB_A),
        "mgr.get_descriptions": await mgr.get_descriptions(_DB_A),
        "mgr.get_db_descriptions": await mgr.get_db_descriptions(),
        "mgr.get_db_description_origin": await mgr.get_db_description_origin(_DB_A),
        "mgr.get_synonyms": await mgr.get_synonyms(_DB_A),
        "rc.load_synonyms_with_sources": await rc.load_synonyms_with_sources(_DB_A),
        "rc.load_resource_type_synonyms": await rc.load_resource_type_synonyms(),
        "rc.load_eav_name_synonyms": await rc.load_eav_name_synonyms(),
        "rc.load_global_synonyms": await rc.load_global_synonyms(),
        "rc.load_csv_cache": await rc.load_csv_cache("a" * 64),
        "rc.load_column_value_index.db_a": await rc.load_column_value_index(_DB_A),
        "rc.load_column_value_index.db_b": await rc.load_column_value_index(_DB_B),
        "rc.load_form_memory": await rc.load_form_memory("sig-1"),
    }


async def _collect_default(tmp_path: Path) -> dict[str, Any]:
    mgr, fake = _manager(tmp_path)
    await _write_sequence(mgr)
    after_writes = await _dump(fake)
    reads = _scrub(await _read_sequence(mgr))
    await mgr.invalidate(_DB_A)
    after_invalidate = sorted(fake.dump())
    await mgr.invalidate_all()
    after_invalidate_all = sorted(fake.dump())
    return {
        "after_writes": after_writes,
        "reads": reads,
        "keys_after_invalidate_db_a": after_invalidate,
        "keys_after_invalidate_all": after_invalidate_all,
    }


async def _collect_governance(tmp_path: Path) -> dict[str, Any]:
    mgr, fake = _manager(tmp_path)
    rc = mgr._redis_cache
    assert rc is not None
    assert await mgr.save_synonyms(_DB_A, {"cmm_resource.name": ["서버명"]}, source="llm")
    assert await mgr.add_synonyms(_DB_A, "cmm_resource.name", ["등록명"], source="operator")
    assert await rc.increment_synonym_usage(
        _DB_A, "cmm_resource.name", ["서버명"], now=_FIXED_NOW, confidence=0.8,
    )
    after_increment = await _dump(fake)
    assert await mgr.save_synonyms(_DB_A, {"cmm_resource.name": ["서버이름"]}, source="llm")
    return {"after_increment": after_increment, "after_writes": await _dump(fake)}


def _dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


async def _collect(tmp_path: Path, monkeypatch) -> dict[str, Any]:
    _set_governance(monkeypatch, False)
    default = await _collect_default(tmp_path / "default")
    _set_governance(monkeypatch, True)
    governance = await _collect_governance(tmp_path / "governance")
    # JSON 왕복으로 tuple 등 표현 차이를 없앤다
    return json.loads(_dumps({"default": default, "governance_on": governance}))


async def test_redis_keyspace_matches_snapshot(tmp_path, monkeypatch):
    """대표 쓰기 시퀀스 뒤 Redis 전 키·값·TTL이 스냅샷과 같다."""
    current = await _collect(tmp_path, monkeypatch)

    if os.environ.get("UPDATE_PLAN162_SNAPSHOT") == "1":
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(_dumps(current), encoding="utf-8")
        pytest.skip(f"스냅샷을 갱신했다: {SNAPSHOT_PATH}")

    assert SNAPSHOT_PATH.exists(), (
        f"스냅샷이 없다: {SNAPSHOT_PATH} — UPDATE_PLAN162_SNAPSHOT=1로 최초 채록하라"
    )
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    for scenario in ("default", "governance_on"):
        cur, exp = current[scenario], expected[scenario]
        assert sorted(cur["after_writes"]) == sorted(exp["after_writes"]), (
            f"[{scenario}] 키 집합이 달라졌다 "
            f"(신규: {sorted(set(cur['after_writes']) - set(exp['after_writes']))}, "
            f"소실: {sorted(set(exp['after_writes']) - set(cur['after_writes']))})"
        )
        changed = sorted(
            k for k in exp["after_writes"] if cur["after_writes"][k] != exp["after_writes"][k]
        )
        assert not changed, f"[{scenario}] 값·TTL이 달라진 키: {changed}"
    assert current == expected
