"""plans/120 F-5 — 유사어 오염 진단(`audit`)·정리(`prune`)·복원(`restore`).

실 Redis 0 — `RedisSchemaCache`에 인메모리 페이크(`tests/mocks/async_redis.py`)를 붙인다.
구조 선언은 저장소 실제 프로필(`config/db_profiles/*.yaml`)을 스크립트의 읽기 전용 경로로 읽는다.
판정은 런타임 쓰기 가드와 같은 함수(`synonym_registration_block_reason`)다.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.document.synonym_write_guard import (
    BLOCK_DATE_SUFFIX_TABLE,
    BLOCK_EAV_COMPOSITE_FIELD,
    BLOCK_MONTH_STRUCTURE_FIELD,
    BLOCK_OUTSIDE_DECLARED_TABLES,
)
from src.schema_cache.redis_cache import RedisSchemaCache
from tests.mocks.async_redis import attach_fake_redis

REPO = Path(__file__).resolve().parent.parent.parent
GP, B0 = "polestar_cm_gp", "polestar_b0"
AVG_M = "월중평균사용률(최근 6개월간)|M"
PEAK_M1 = "월중 Peak시 사용률(최근 6개월간)|M+1"
AVG_M2 = "월중평균사용률(최근 6개월간)|M+2"
TPMC = "처리능력|(TPMC)"  # D-148 반례 — 양식 복합 필드명이 EAV 유사어로 굳은 항목


@pytest.fixture
def seeds() -> Any:
    spec = importlib.util.spec_from_file_location(
        "synonym_seeds_plan120", REPO / "scripts" / "synonym_seeds.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["synonym_seeds_plan120"] = mod
    spec.loader.exec_module(mod)
    return mod


def _tagged(words: dict[str, str]) -> str:
    return json.dumps({"words": list(words), "sources": words}, ensure_ascii=False)


SEED: dict[str, dict[str, str]] = {
    f"schema:{GP}:synonyms": {
        "polestar.ip_info.description": _tagged({"비고": "llm", "설명": "operator"}),
        "polestar.cmm_resource.hostname": _tagged({"호스트명": "operator", AVG_M: "llm"}),
        "polestar.cmm_alarm.alarmseverity": _tagged({"심각도": "operator"}),
    },
    f"schema:{B0}:synonyms": {
        "MON_HW_20260806.MEM_RATIO": json.dumps([AVG_M], ensure_ascii=False),  # 레거시 목록형
        "IPAM_INFO.DESCRIPTION": _tagged({"비고": "llm_inferred"}),
    },
    "schema:itam:synonyms": {
        "asset.asset_no": _tagged({"자산번호": "llm"}),  # EAV 선언 없는 DB — 정상
        "snap_20250101.val": _tagged({"스냅샷값": "llm"}),
    },
    "synonyms:global": {
        "mem_ratio": json.dumps(
            {"words": [PEAK_M1, "메모리사용률"], "description": "설명"}, ensure_ascii=False
        ),
        "hostname": json.dumps({"words": ["호스트명"]}, ensure_ascii=False),
        # EAV 등록의 전역 사본(키 = EAV 속성명) — EAV 대상으로 판정한다
        "TotalSize": json.dumps({"words": [TPMC, "메모리 총량"]}, ensure_ascii=False),
        # 일반 컬럼 키의 복합명은 이번 범위 밖(월 구조 필드만) — 위반 아님
        "category": json.dumps({"words": ["구분|분류"]}, ensure_ascii=False),
    },
    "synonyms:eav_names": {
        "TotalSize": json.dumps(["메모리 용량", AVG_M2, TPMC], ensure_ascii=False),
    },
}


async def _cache() -> tuple[RedisSchemaCache, Any]:
    cache = RedisSchemaCache(MagicMock())
    fake = attach_fake_redis(cache)
    for key, mapping in SEED.items():
        await fake.hset(key, mapping=mapping)
    return cache, fake


def _rows(violations: list[Any]) -> set[tuple]:
    return {(v.store, v.db_id, v.key, v.word, v.source, v.reason) for v in violations}


EXPECTED = {
    ("db", GP, "polestar.ip_info.description", "비고", "llm", BLOCK_OUTSIDE_DECLARED_TABLES),
    ("db", GP, "polestar.ip_info.description", "설명", "operator", BLOCK_OUTSIDE_DECLARED_TABLES),
    ("db", GP, "polestar.cmm_resource.hostname", AVG_M, "llm", BLOCK_MONTH_STRUCTURE_FIELD),
    ("db", B0, "MON_HW_20260806.MEM_RATIO", AVG_M, None, BLOCK_MONTH_STRUCTURE_FIELD),
    ("db", B0, "IPAM_INFO.DESCRIPTION", "비고", "llm_inferred", BLOCK_OUTSIDE_DECLARED_TABLES),
    ("db", "itam", "snap_20250101.val", "스냅샷값", "llm", BLOCK_DATE_SUFFIX_TABLE),
    ("global", None, "mem_ratio", PEAK_M1, None, BLOCK_MONTH_STRUCTURE_FIELD),
    ("eav_names", None, "TotalSize", AVG_M2, None, BLOCK_MONTH_STRUCTURE_FIELD),
    ("eav_names", None, "TotalSize", TPMC, None, BLOCK_EAV_COMPOSITE_FIELD),
    ("global", None, "TotalSize", TPMC, None, BLOCK_EAV_COMPOSITE_FIELD),
}


class TestAudit:
    async def test_lists_violations_in_all_stores_read_only(self, seeds, tmp_path):
        cache, fake = await _cache()
        before = fake.dump()
        violations = await seeds.collect_violations(cache, backup_root=tmp_path)
        assert _rows(violations) == EXPECTED
        assert fake.dump() == before

    async def test_json_output(self, seeds, tmp_path, capsys):
        cache, _ = await _cache()
        assert await seeds.run_audit(cache, None, as_json=True, backup_root=tmp_path) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["total"] == len(EXPECTED)
        by_word = {(v["key"], v["word"]): v for v in payload["violations"]}
        assert by_word[("polestar.ip_info.description", "설명")]["action"] == "keep_protected"
        assert by_word[("mem_ratio", PEAK_M1)]["action"] == "keep_untagged"
        assert by_word[("IPAM_INFO.DESCRIPTION", "비고")]["reason_label"]

    async def test_db_filter_excludes_global_and_eav(self, seeds, tmp_path):
        cache, _ = await _cache()
        violations = await seeds.collect_violations(cache, [GP], backup_root=tmp_path)
        assert {v.db_id for v in violations} == {GP}
        assert all(v.store == "db" for v in violations)


class TestPrune:
    async def test_dry_run_changes_nothing(self, seeds, tmp_path, capsys):
        cache, fake = await _cache()
        before = fake.dump()
        rc = await seeds.run_prune(
            cache, None, apply=False, include_untagged=True,
            backup_dir=tmp_path / "bk", backup_root=tmp_path,
        )
        assert rc == 0
        assert fake.dump() == before
        assert not (tmp_path / "bk").exists()
        assert "dry-run" in capsys.readouterr().out

    async def test_apply_removes_auto_sources_only_and_backs_up_first(self, seeds, tmp_path):
        cache, fake = await _cache()
        before = fake.dump()
        rc = await seeds.run_prune(
            cache, None, apply=True, include_untagged=False,
            backup_dir=tmp_path / "bk", backup_root=tmp_path,
        )
        assert rc == 0
        after = fake.dump()

        gp = after[f"schema:{GP}:synonyms"]
        desc = json.loads(gp["polestar.ip_info.description"])
        # operator 불가침
        assert desc["words"] == ["설명"] and desc["sources"] == {"설명": "operator"}
        host = json.loads(gp["polestar.cmm_resource.hostname"])
        assert host["words"] == ["호스트명"] and host["sources"] == {"호스트명": "operator"}
        assert gp["polestar.cmm_alarm.alarmseverity"] == before[f"schema:{GP}:synonyms"][
            "polestar.cmm_alarm.alarmseverity"
        ]
        b0 = after[f"schema:{B0}:synonyms"]
        assert "IPAM_INFO.DESCRIPTION" not in b0  # 단어가 비면 필드 삭제
        assert b0["MON_HW_20260806.MEM_RATIO"] == before[f"schema:{B0}:synonyms"][
            "MON_HW_20260806.MEM_RATIO"
        ]  # 태그 없음 — 기본 보존
        itam = after["schema:itam:synonyms"]
        assert "snap_20250101.val" not in itam and "asset.asset_no" in itam
        assert after["synonyms:global"] == before["synonyms:global"]
        assert after["synonyms:eav_names"] == before["synonyms:eav_names"]

        (backup,) = list((tmp_path / "bk").glob("synonym_prune_backup_*.json"))
        data = json.loads(backup.read_text("utf-8"))
        assert data["kind"] == "synonym_prune_backup"
        backed = {(e["key"], e["field"]): e["value"] for e in data["entries"]}
        assert backed == {
            (f"schema:{GP}:synonyms", "polestar.ip_info.description"):
                before[f"schema:{GP}:synonyms"]["polestar.ip_info.description"],
            (f"schema:{GP}:synonyms", "polestar.cmm_resource.hostname"):
                before[f"schema:{GP}:synonyms"]["polestar.cmm_resource.hostname"],
            (f"schema:{B0}:synonyms", "IPAM_INFO.DESCRIPTION"):
                before[f"schema:{B0}:synonyms"]["IPAM_INFO.DESCRIPTION"],
            ("schema:itam:synonyms", "snap_20250101.val"):
                before["schema:itam:synonyms"]["snap_20250101.val"],
        }

    async def test_include_untagged_cleans_global_eav_legacy(self, seeds, tmp_path):
        cache, fake = await _cache()
        await seeds.run_prune(
            cache, None, apply=True, include_untagged=True,
            backup_dir=tmp_path / "bk", backup_root=tmp_path,
        )
        after = fake.dump()
        assert f"schema:{B0}:synonyms" not in after  # 두 필드가 모두 비어 키가 사라진다
        assert json.loads(after["synonyms:global"]["mem_ratio"]) == {
            "words": ["메모리사용률"], "description": "설명"
        }
        assert json.loads(after["synonyms:eav_names"]["TotalSize"]) == ["메모리 용량"]
        assert json.loads(after["synonyms:global"]["TotalSize"]) == {"words": ["메모리 총량"]}
        assert json.loads(after["synonyms:global"]["category"]) == {"words": ["구분|분류"]}
        # operator 단어는 옵션과 무관하게 남는다
        assert "설명" in after[f"schema:{GP}:synonyms"]["polestar.ip_info.description"]

    async def test_backup_failure_aborts_without_changes(self, seeds, tmp_path, monkeypatch):
        cache, fake = await _cache()
        before = fake.dump()

        def _fail(*_a: Any, **_k: Any) -> Path:
            raise OSError("disk full")

        monkeypatch.setattr(seeds, "_write_backup", _fail)
        rc = await seeds.run_prune(
            cache, None, apply=True, include_untagged=True,
            backup_dir=tmp_path / "bk", backup_root=tmp_path,
        )
        assert rc == 1
        assert fake.dump() == before


class TestRestore:
    async def test_restore_round_trip(self, seeds, tmp_path):
        cache, fake = await _cache()
        before = fake.dump()
        await seeds.run_prune(
            cache, None, apply=True, include_untagged=True,
            backup_dir=tmp_path / "bk", backup_root=tmp_path,
        )
        assert fake.dump() != before
        (backup,) = list((tmp_path / "bk").glob("*.json"))

        assert await seeds.run_restore(cache, backup, apply=False) == 0
        assert fake.dump() != before  # dry-run 무변경
        assert await seeds.run_restore(cache, backup, apply=True) == 0
        assert fake.dump() == before

    async def test_rejects_non_backup_file(self, seeds, tmp_path):
        cache, _ = await _cache()
        bogus = tmp_path / "x.json"
        bogus.write_text(json.dumps({"kind": "other"}), encoding="utf-8")
        assert await seeds.run_restore(cache, bogus, apply=True) == 1


def test_cli_prune_then_restore(seeds, tmp_path, monkeypatch):
    """CLI 배선 — 연결만 페이크로 바꾸고 main()으로 prune --apply → restore --apply."""
    cache, fake = asyncio.run(_cache())
    before = fake.dump()
    fake.aclose = AsyncMock()  # 스크립트가 연결을 닫아도 같은 페이크를 다시 붙인다

    async def _connect() -> RedisSchemaCache:
        attach_fake_redis(cache, fake)
        return cache

    monkeypatch.setattr(seeds, "_connect_redis", _connect)
    monkeypatch.setattr(seeds, "_structure_backup_root", lambda: tmp_path)
    bk = tmp_path / "bk"
    assert seeds.main(["prune", "--apply", "--backup-dir", str(bk)]) == 0
    assert fake.dump() != before
    (backup,) = list(bk.glob("*.json"))
    assert seeds.main(["restore", str(backup), "--apply"]) == 0
    assert fake.dump() == before
