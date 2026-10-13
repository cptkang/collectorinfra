"""plans/160 W4 — 결과 용어 사전 조회(`src/semantic/result_glossary.py`) · D-327."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.semantic import result_glossary as rg
from src.semantic.result_glossary import (
    GLOSSARY_PATH,
    ResultSource,
    load_glossary,
    lookup_term,
    result_source,
)

#: W0 실측 ⑤ 첫 편입 키(본체 소유 사전).
FIRST_KEYS = (
    "heap_used_mb heap_committed_mb non_heap_used_mb gc_time_usage_pct process_cpu_pct "
    "process_memory_mb thread_current thread_daemon thread_started socket_count file_count "
    "collection_count heap_usage_ratio response_time_avg_ms tps active_services "
    "bad_response_active_services reject_rate concurrent_users arrival_rate db_pool_active "
    "db_pool_idle_avg db_pool_configured_avg time_ms instance_id instance_name domain_id source_id"
).split()


def _provider(mapping: dict[str, dict[str, str]], calls: list[str] | None = None):
    async def provide(db_id: str) -> dict[str, str]:
        if calls is not None:
            calls.append(db_id)
        return mapping.get(db_id, {})

    return provide


class TestGlossaryFile:
    def test_first_keys_all_present_with_text(self) -> None:
        terms = load_glossary()
        missing = [k for k in FIRST_KEYS if k not in terms]
        assert missing == []
        assert all(terms[k]["text"] for k in FIRST_KEYS)

    def test_format_version_and_units(self) -> None:
        data = yaml.safe_load(GLOSSARY_PATH.read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert load_glossary()["heap_used_mb"]["unit"] == "MB"
        assert load_glossary()["gc_time_usage_pct"]["unit"] == "%"

    def test_bad_file_yields_empty(self, tmp_path: Path) -> None:
        bad = tmp_path / "g.yaml"
        bad.write_text("version: 2\nterms: {}\n", encoding="utf-8")
        assert load_glossary(bad) == {}
        assert load_glossary(tmp_path / "none.yaml") == {}


class TestResultSource:
    def test_db_scope_and_apm_flags(self) -> None:
        src = result_source(
            {"db_scope": {"db_ids": ["db_a", "db_b"]}, "apm_source_scope": {"x": 1}}
        )
        assert src == ResultSource(db_ids=("db_a", "db_b"), is_apm=True)
        assert src.key == "apm|db_a,db_b"

    def test_clarification_counts_as_apm(self) -> None:
        assert result_source({"apm_source_clarification": {"q": 1}}).is_apm is True

    def test_empty(self) -> None:
        src = result_source({})
        assert src == ResultSource()
        assert "출처 미상" in src.label


class TestLookup:
    async def test_apm_glossary_hit(self) -> None:
        hit = await lookup_term("heap_used_mb", ResultSource(is_apm=True))
        assert hit == {
            "source": "glossary",
            "text": load_glossary()["heap_used_mb"]["text"],
            "unit": "MB",
            "origin": "result_glossary",
        }

    async def test_case_insensitive_glossary(self) -> None:
        hit = await lookup_term("TPS", ResultSource(is_apm=True))
        assert hit is not None and hit["origin"] == "result_glossary"

    async def test_db_provider_tail_match_first_of_distinct(self, tmp_path: Path) -> None:
        provider = _provider(
            {"nodb_x": {"t2.col_x": "설명 B", "t1.COL_X": "설명 A", "t1.other": "무관"}}
        )
        hit = await lookup_term(
            "col_x", ResultSource(db_ids=("nodb_x",)),
            description_provider=provider, knowledge_root=tmp_path,
        )
        assert hit is not None
        assert hit["text"] == "설명 A"  # 키 정렬 순 첫 것("t1.COL_X" < "t2.col_x")
        assert hit["origin"] == "schema_cache:nodb_x:t1.COL_X 외 1종"
        assert hit["unit"] == ""

    async def test_db_provider_same_text_no_ambiguity_note(self, tmp_path: Path) -> None:
        provider = _provider({"nodb_x": {"a.col_y": "같은 설명", "b.col_y": "같은 설명"}})
        hit = await lookup_term(
            "col_y", ResultSource(db_ids=("nodb_x",)),
            description_provider=provider, knowledge_root=tmp_path,
        )
        assert hit is not None and hit["origin"] == "schema_cache:nodb_x:a.col_y"

    async def test_knowledge_before_provider(self, tmp_path: Path) -> None:
        (tmp_path / "nodb_k").mkdir()
        (tmp_path / "nodb_k" / "column_descriptions.yaml").write_text(
            "version: 1\norigin: claude_code\ndescriptions:\n  \"t.col_k\": \"지식 설명\"\n",
            encoding="utf-8",
        )
        calls: list[str] = []
        provider = _provider({"nodb_k": {"t.col_k": "캐시 설명"}}, calls)
        hit = await lookup_term(
            "col_k", ResultSource(db_ids=("nodb_k",)),
            description_provider=provider, knowledge_root=tmp_path,
        )
        assert hit is not None and hit["text"] == "지식 설명"
        assert hit["origin"] == "knowledge:nodb_k:t.col_k"
        assert calls == []

    async def test_profile_first(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        profile = {
            "patterns": [
                {
                    "entity_columns": [{"name": "Col_P", "description": "프로필 설명"}],
                    "known_attributes": [{"name": "AttrQ", "description": "속성 설명"}],
                }
            ]
        }
        monkeypatch.setattr(rg, "load_structure_profile", lambda db_id: profile)
        calls: list[str] = []
        provider = _provider({"nodb_p": {"t.col_p": "캐시 설명"}}, calls)
        src = ResultSource(db_ids=("nodb_p",))
        hit = await lookup_term(
            "col_p", src, description_provider=provider, knowledge_root=tmp_path
        )
        assert hit is not None and hit["text"] == "프로필 설명"
        assert hit["origin"] == "db_profile:nodb_p:entity_columns"
        hit2 = await lookup_term("attrq", src, knowledge_root=tmp_path)
        assert hit2 is not None and hit2["origin"] == "db_profile:nodb_p:known_attributes"
        assert calls == []

    async def test_real_profile_entity_column(self) -> None:
        """실 프로필 파일 — 런타임 조회(사본 없음)."""
        hit = await lookup_term("HOSTNAME", ResultSource(db_ids=("polestar",)))
        assert hit is not None and hit["origin"] == "db_profile:polestar:entity_columns"

    async def test_apm_prefers_glossary_over_inherited_db(self, tmp_path: Path) -> None:
        provider = _provider({"nodb_x": {"t.tps": "DB 쪽 tps"}})
        apm = await lookup_term(
            "tps", ResultSource(db_ids=("nodb_x",), is_apm=True),
            description_provider=provider, knowledge_root=tmp_path,
        )
        assert apm is not None and apm["origin"] == "result_glossary"
        db = await lookup_term(
            "tps", ResultSource(db_ids=("nodb_x",)),
            description_provider=provider, knowledge_root=tmp_path,
        )
        assert db is not None and db["text"] == "DB 쪽 tps"

    async def test_db_result_falls_back_to_glossary(self, tmp_path: Path) -> None:
        hit = await lookup_term(
            "time_ms", ResultSource(db_ids=("nodb_x",)),
            description_provider=_provider({}), knowledge_root=tmp_path,
        )
        assert hit is not None and hit["origin"] == "result_glossary"

    async def test_provider_failure_is_miss(self, tmp_path: Path) -> None:
        async def broken(db_id: str) -> dict[str, str]:
            raise ConnectionError("redis down")

        hit = await lookup_term(
            "unknown_col_z", ResultSource(db_ids=("nodb_x",)),
            description_provider=broken, knowledge_root=tmp_path,
        )
        assert hit is None

    async def test_bad_db_id_skipped(self, tmp_path: Path) -> None:
        hit = await lookup_term(
            "unknown_col_z", ResultSource(db_ids=("../evil",)), knowledge_root=tmp_path
        )
        assert hit is None

    async def test_miss(self, tmp_path: Path) -> None:
        assert await lookup_term(
            "unknown_col_z", ResultSource(is_apm=True), knowledge_root=tmp_path
        ) is None
