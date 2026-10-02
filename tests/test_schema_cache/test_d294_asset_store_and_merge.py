"""D-294 W3 — 자산 키 병합 규칙(`profile_merge`) · 생성 자산 파일 저장소(`AssetFileStore`).

- 새 자산 키: `allowed_tables`·`entity_keys`는 비었을 때만 · `relationships`·`query_rules`·
  `query_examples`는 합집합(사람 항목 우선) · 초안에 키가 없으면 base 그대로(O-6 결과 불변)
- 저장소: 현행 파일 v0 보관 · 버전 · 원자 교체 · 되돌리기 바이트 동일 · 로컬 샌드박스 표기
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.domain.profile_merge import merge_profile
from src.schema_cache.asset_store import AssetFileStore

BASE = {
    "source": "manual",
    "allowed_tables": ["t_a"],
    "relationships": [{"from": "t_b.a_id", "to": "t_a.id", "origin": "human"}],
    "query_examples": [{"question": "사람 질문", "sql": "SELECT 1"}],
    "query_guide": "사람 안내",
    "custom": {"keep": True},
}


class TestMergeAssetKeys:
    def test_human_values_are_preserved_and_new_items_appended(self):
        draft = {
            "allowed_tables": ["t_x"],
            "entity_keys": {"entity": "server", "table": "t_a", "keys": []},
            "relationships": [
                {"from": "t_b.a_id", "to": "t_a.id", "origin": "inferred"},
                {"from": "t_c.a_id", "to": "t_a.id", "origin": "inferred"},
            ],
            "query_rules": ["규칙 1"],
            "query_examples": [{"question": "사람 질문", "sql": "SELECT 2"},
                               {"question": "새 질문", "sql": "SELECT 3"}],
        }

        merged = merge_profile(BASE, draft, local_sandbox=False)

        assert merged["allowed_tables"] == ["t_a"]  # 사람 값 보존
        assert merged["entity_keys"]["table"] == "t_a"  # 비어 있어 채움
        assert merged["relationships"] == [
            {"from": "t_b.a_id", "to": "t_a.id", "origin": "human"},
            {"from": "t_c.a_id", "to": "t_a.id", "origin": "inferred"},
        ]
        assert merged["query_rules"] == ["규칙 1"]
        assert [e["sql"] for e in merged["query_examples"]] == ["SELECT 1", "SELECT 3"]
        assert merged["custom"] == {"keep": True} and merged["query_guide"] == "사람 안내"

    def test_structure_draft_without_asset_keys_keeps_base_exactly(self):
        """O-6 구조 분석 초안(자산 키 없음)의 병합 결과는 종전과 같다."""
        base = {**BASE, "relationships": "깨진 값도 그대로"}

        draft = {"patterns": [], "query_guide": "새 안내"}
        merged = merge_profile(base, draft, local_sandbox=False)

        assert merged["relationships"] == "깨진 값도 그대로"
        assert merged["query_examples"] == BASE["query_examples"]
        assert merged["allowed_tables"] == ["t_a"]
        assert "entity_keys" not in merged and "query_rules" not in merged


class TestMergeReviewFixes:
    """코드 리뷰 지적(2026-10-02) — 코드값 단위 라벨 병합 · 사람 편집 이상값 보존."""

    def test_code_labels_merge_per_code_value(self):
        base = {"code_values": {"t.c": ["1"]}, "code_labels": {"t.c": {"1": "old"}}}
        draft = {"code_values": {"t.c": ["1", "2"]},
                 "code_labels": {"t.c": {"1": "new", "2": "two"}, "t.d": {"Y": "사용"}}}

        merged = merge_profile(base, draft, local_sandbox=False)

        assert merged["code_values"]["t.c"] == ["1", "2"]
        assert merged["code_labels"] == {"t.c": {"1": "old", "2": "two"}, "t.d": {"Y": "사용"}}

    def test_non_list_human_values_survive_asset_drafts(self):
        base = {"relationships": "사람이 적은 메모", "code_labels": ["이상한 값"]}
        draft = {
            "relationships": [{"from": "a.x", "to": "b.x"}], "code_labels": {"t.c": {"1": "a"}},
        }

        merged = merge_profile(base, draft, local_sandbox=False)

        assert merged["relationships"] == "사람이 적은 메모"
        assert merged["code_labels"] == ["이상한 값"]


class TestAssetFileStore:
    @pytest.fixture
    def store(self, tmp_path: Path) -> AssetFileStore:
        return AssetFileStore(tmp_path, tmp_path / ".cache" / "structure")

    async def test_apply_archives_existing_file_and_rolls_back_bytes(self, store, tmp_path):
        path = tmp_path / "config" / "synonym_seeds" / "app_x.yaml"
        path.parent.mkdir(parents=True)
        original = "# 사람이 쓴 시드\ndb_id: app_x\ncolumn_synonyms: {}\n"
        path.write_text(original, encoding="utf-8")

        entry = await store.apply(
            "app_x", "seeds", {"db_id": "app_x", "column_synonyms": {"t.c": ["별칭"]}},
            by="admin", reason="승인", env="e", draft_id="d1", local_sandbox=False,
            header="# 생성물\n# 두 번째 줄",
        )

        assert entry["ver"] == 1 and entry["kind"] == "approved"
        text = path.read_text(encoding="utf-8")
        assert text.startswith("# 생성물\n# 두 번째 줄\n")
        assert yaml.safe_load(text)["column_synonyms"] == {"t.c": ["별칭"]}
        versions = await store.list_versions("app_x", "seeds")
        assert [(v["ver"], v["kind"]) for v in versions] == [(0, "baseline"), (1, "approved")]

        rolled = await store.rollback("app_x", "seeds", 0, by="admin", reason="복구", env="e")

        assert rolled["kind"] == "rollback" and rolled["rolled_back_from"] == 0
        assert path.read_bytes() == original.encode("utf-8")

    async def test_local_sandbox_marker_and_external_change(self, store, tmp_path):
        await store.apply("app_x", "prompt_template", {"section": "규칙"}, by=None, reason="r",
                          env="e", draft_id=None, local_sandbox=True, header="# 로컬")
        path = tmp_path / "config" / "knowledge" / "app_x" / "prompt_template.yaml"
        assert yaml.safe_load(path.read_text(encoding="utf-8"))["environment"] == "local_sandbox"

        path.write_text("section: 사람이 고침\n", encoding="utf-8")
        await store.apply("app_x", "prompt_template", {"section": "새 규칙"}, by=None, reason="r",
                          env="e", draft_id=None, local_sandbox=False, header="")

        kinds = [v["kind"] for v in await store.list_versions("app_x", "prompt_template")]
        assert kinds == ["approved", "external_change", "approved"]
        assert "environment" not in yaml.safe_load(path.read_text(encoding="utf-8"))

    async def test_rejections(self, store):
        with pytest.raises(ValueError, match="모르는 자산 종류"):
            store.path("app_x", "profile")
        with pytest.raises(ValueError, match="머리말"):
            await store.apply("app_x", "seeds", {}, by=None, reason="r", env="e", draft_id=None,
                              local_sandbox=False, header="주석 아님")
        with pytest.raises(ValueError, match="되돌릴 자산 버전"):
            await store.rollback("app_x", "seeds", 3, by=None, reason="r", env="e")
        with pytest.raises(ValueError):
            store.path("../x", "seeds")
