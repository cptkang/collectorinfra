"""카탈로그 생성기 테스트 (Plan 67 R1).

검증 축:
    1. 순수 파생 — 임의 도메인(폴스타 무관) structure_meta로 카탈로그가 만들어진다(D-088 공용 계층).
    2. 별칭 delta — alias_deny/alias_extra가 프로필 유사어 위에 가감으로만 적용된다.
    3. resource_type — 구조화 키 우선, 미이관 프로필은 description 표기 폴백.
    4. 동등성 — 실 프로필 4종에서 생성한 카탈로그가 기존 semantic_models와 필드 단위로 동등(diff 0).
    5. 리허설 — "컬럼 별칭 1건 추가" 시 수정 파일 수 1곳(기존 4곳).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.schema_cache.catalog_builder import (  # noqa: E402
    attribute_resource_types,
    build_catalog,
    deep_merge,
    load_knowledge_overrides,
)

SEMANTIC_DIR = REPO_ROOT / "config" / "semantic_models"
PROFILE_DIR = REPO_ROOT / "config" / "db_profiles"
KNOWLEDGE_DIR = REPO_ROOT / "config" / "knowledge"

# 도메인 무관 합성 구조 메타 — 공용 계층이 특정 제품 지식 없이 동작함을 보인다.
SYNTHETIC_META = {
    "patterns": [
        {
            "type": "eav",
            "entity_table": "asset",
            "config_table": "asset_prop",
            "attribute_column": "prop_name",
            "value_column": "prop_value",
            "lob_value_column": "prop_text",
            "direct_join": {"entity_column": "conf_id", "config_column": "configuration_id",
                            "description": "설명은 카탈로그에 싣지 않는다"},
            "known_attributes": [
                {"name": "Firmware", "resource_type": "asset.Board",
                 "description": "펌웨어 버전", "synonyms": ["펌웨어", "FW", "펌웨어버전"]},
                {"name": "Capacity", "resource_type": "asset.Storage",
                 "description": "용량", "synonyms": ["용량"], "lob": True},
                {"name": "Legacy", "description": "구표기 [resource_type: asset.Old]",
                 "synonyms": ["레거시"]},
            ],
        }
    ],
    "column_synonyms": {
        "asset.label": {"words": ["자산명", "이름", "라벨"]},
    },
    "alarm_allowed_tables": ["evt_event", "evt_def", "acl_role"],
}

SYNTHETIC_OVERRIDES = {
    "pattern_a": {
        "entity_resource_type": "asset.Unit",
        "direct_dimensions": [{"name": "label", "column": "label"}],
        "attributes": {"exclude": ["Legacy"]},
        "alias_deny": {"label": ["이름"], "Firmware": ["FW"]},
        "alias_extra": {"label": ["asset_name"]},
        "filterable": ["label"],
    },
    "pattern_b": {"measures": [{"resource_type": "asset.Storage", "definition_name": "IORate"}]},
    "pattern_c": {"base_table": {"table": "evt_event"}, "allowed_tables_deny": ["acl_role"]},
}


def _build_synthetic() -> dict:
    return build_catalog(SYNTHETIC_META, db_id="synthetic", overrides=SYNTHETIC_OVERRIDES)


class TestPureDerivation:
    """1. 도메인 무관 파생."""

    def test_eav_block_derived_from_structure(self):
        catalog = _build_synthetic()
        assert catalog["pattern_a"]["eav"] == {
            "entity_table": "asset",
            "config_table": "asset_prop",
            "attribute_column": "prop_name",
            "value_column": "prop_value",
            "lob_value_column": "prop_text",
            "direct_join": {"entity_column": "conf_id", "config_column": "configuration_id"},
        }

    def test_direct_dimension_precedes_eav_and_carries_entity_resource_type(self):
        dims = _build_synthetic()["pattern_a"]["dimensions"]
        assert [d["name"] for d in dims] == ["label", "Firmware", "Capacity"]
        assert dims[0] == {
            "name": "label", "source": "direct", "column": "label",
            "resource_type": "asset.Unit", "aliases": ["자산명", "라벨", "asset_name"],
        }

    def test_excluded_attribute_is_dropped(self):
        names = [d["name"] for d in _build_synthetic()["pattern_a"]["dimensions"]]
        assert "Legacy" not in names

    def test_lob_flag_and_measures_and_allowed_tables(self):
        catalog = _build_synthetic()
        capacity = next(d for d in catalog["pattern_a"]["dimensions"] if d["name"] == "Capacity")
        assert capacity["lob"] is True
        assert catalog["pattern_b"]["measures"][0]["definition_name"] == "IORate"
        # 허용 테이블은 구조 정본에서 파생하고 deny만 뺀다.
        assert catalog["pattern_c"]["allowed_tables"] == ["evt_event", "evt_def"]
        assert "allowed_tables_deny" not in catalog["pattern_c"]

    def test_no_structure_meta_yields_empty_pattern_a(self):
        catalog = build_catalog(None, db_id="x", overrides=SYNTHETIC_OVERRIDES)
        assert "pattern_a" not in catalog


class TestAliasDelta:
    """2. 별칭은 파생 + delta(가감)로만 만들어진다."""

    def test_deny_removes_and_extra_appends(self):
        dims = {d["name"]: d for d in _build_synthetic()["pattern_a"]["dimensions"]}
        assert dims["Firmware"]["aliases"] == ["펌웨어", "펌웨어버전"]  # FW deny
        assert dims["label"]["aliases"] == ["자산명", "라벨", "asset_name"]  # 이름 deny + extra

    def test_new_profile_synonym_flows_through_without_touching_overrides(self):
        meta = yaml.safe_load(yaml.safe_dump(SYNTHETIC_META))
        attrs = meta["patterns"][0]["known_attributes"]
        next(a for a in attrs if a["name"] == "Firmware")["synonyms"].append("신규별칭")
        catalog = build_catalog(meta, db_id="synthetic", overrides=SYNTHETIC_OVERRIDES)
        dims = {d["name"]: d for d in catalog["pattern_a"]["dimensions"]}
        assert "신규별칭" in dims["Firmware"]["aliases"]

    def test_duplicate_alias_is_not_repeated(self):
        overrides = deep_merge(SYNTHETIC_OVERRIDES, {"pattern_a": {"alias_extra": {"Firmware": ["펌웨어"]}}})
        dims = {d["name"]: d for d in
                build_catalog(SYNTHETIC_META, overrides=overrides)["pattern_a"]["dimensions"]}
        assert dims["Firmware"]["aliases"].count("펌웨어") == 1


class TestResourceTypeField:
    """3. resource_type은 구조화 키 우선, 구프로필은 description 폴백."""

    def test_structured_key_wins(self):
        assert attribute_resource_types(SYNTHETIC_META)["FIRMWARE"] == "asset.Board"

    def test_legacy_description_tag_is_fallback(self):
        assert attribute_resource_types(SYNTHETIC_META)["LEGACY"] == "asset.Old"

    def test_first_tag_used_when_description_has_multiple(self):
        meta = {"patterns": [{"type": "eav", "known_attributes": [
            {"name": "Both", "description": "A [resource_type: t.One] / B [resource_type: t.Two]"}]}]}
        assert attribute_resource_types(meta)["BOTH"] == "t.One"

    def test_empty_inputs(self):
        assert attribute_resource_types(None) == {}
        assert attribute_resource_types({}) == {}


class TestOverlayChain:
    """오버라이드 상속(extends)은 뒤/자기 자신이 우선한다."""

    def test_extends_merges_parent(self, tmp_path):
        (tmp_path / "_base").mkdir()
        (tmp_path / "child").mkdir()
        (tmp_path / "_base" / "catalog.yaml").write_text(
            "pattern_a:\n  entity_resource_type: base.Type\n  filterable: [a]\n", encoding="utf-8")
        (tmp_path / "child" / "catalog.yaml").write_text(
            "extends: [_base]\npattern_a:\n  filterable: [b]\n", encoding="utf-8")
        merged = load_knowledge_overrides("child", knowledge_dir=str(tmp_path))
        assert merged["pattern_a"]["entity_resource_type"] == "base.Type"
        assert merged["pattern_a"]["filterable"] == ["b"]
        assert "extends" not in merged

    def test_missing_override_returns_empty(self, tmp_path):
        assert load_knowledge_overrides("nope", knowledge_dir=str(tmp_path)) == {}

    def test_cyclic_extends_does_not_recurse_forever(self, tmp_path):
        for name in ("a", "b"):
            (tmp_path / name).mkdir()
        (tmp_path / "a" / "catalog.yaml").write_text("extends: [b]\nx: 1\n", encoding="utf-8")
        (tmp_path / "b" / "catalog.yaml").write_text("extends: [a]\ny: 2\n", encoding="utf-8")
        merged = load_knowledge_overrides("a", knowledge_dir=str(tmp_path))
        assert merged == {"x": 1, "y": 2}


# ──────────────────────────────────────────────
# 4. 동등성 — 생성 카탈로그 vs 기존 시맨틱 모델 (diff 0)
# ──────────────────────────────────────────────

_DB_IDS = sorted(p.stem for p in SEMANTIC_DIR.glob("*.yaml"))


@pytest.mark.parametrize("db_id", _DB_IDS)
def test_generated_catalog_matches_semantic_model(db_id):
    """생성 카탈로그가 기존 수기 사본과 필드 단위로 동등해야 한다(전환 무회귀 근거)."""
    from scripts.catalog_diff import diff_db

    diffs, err = diff_db(db_id)
    assert err is None, err
    assert diffs == [], f"{db_id} 카탈로그 차이: {diffs}"


# ──────────────────────────────────────────────
# 5. 리허설 — 컬럼 별칭 1건 추가 시 수정 파일 수
# ──────────────────────────────────────────────

# R1 이전 동기화 지점 4곳(§1.2-①). 별칭 1건 추가 시 각각을 고쳐야 했는지 확인한다.
_SYNC_POINTS = {
    "db_profiles": PROFILE_DIR / "polestar_cm_gp.yaml",
    "semantic_models": SEMANTIC_DIR / "polestar_cm_gp.yaml",
    "prompts": REPO_ROOT / "src" / "db_adapters" / "polestar" / "prompts.py",
    "assembler": REPO_ROOT / "src" / "db_adapters" / "polestar" / "assembler.py",
}


def test_alias_addition_requires_single_file_edit():
    """'컬럼 별칭 1건 추가' 리허설: 수정해야 하는 파일이 정본 1곳뿐이어야 한다.

    판정 방법 — 대상 속성(Vendor)의 별칭 지식이 어느 파일에 실제로 적재돼 있는지 실측하고,
    프로필만 고쳐도 카탈로그에 반영되는지 확인한다. semantic_models는 프로필에서 파생 가능함이
    동등성 테스트로 실증됐으므로 수기 동기화 대상에서 빠진다(런타임 경로 단언은
    `test_alias_addition_reaches_runtime_semantic_model`).
    """
    profile = yaml.safe_load(_SYNC_POINTS["db_profiles"].read_text(encoding="utf-8"))
    vendor = next(a for p in profile["patterns"] if p.get("type") == "eav"
                  for a in p["known_attributes"] if a["name"] == "Vendor")
    new_alias = "제조업자테스트"
    vendor["synonyms"].append(new_alias)

    overrides = load_knowledge_overrides("polestar_cm_gp", knowledge_dir=str(KNOWLEDGE_DIR))
    catalog = build_catalog(profile, db_id="polestar_cm_gp", overrides=overrides)
    dims = {d["name"]: d for d in catalog["pattern_a"]["dimensions"]}
    assert new_alias in dims["Vendor"]["aliases"], "프로필 수정이 카탈로그에 반영되지 않음"

    # 프로필 외 나머지 동기화 지점에는 이 속성의 별칭 지식이 없어야 한다(수정 불필요).
    existing_aliases = [a for a in vendor["synonyms"] if a != new_alias]
    needs_edit = ["db_profiles"]
    for point in ("prompts", "assembler"):
        text = _SYNC_POINTS[point].read_text(encoding="utf-8")
        if any(alias in text for alias in existing_aliases):
            needs_edit.append(point)
    assert needs_edit == ["db_profiles"], f"수정 필요 파일: {needs_edit}"


def test_alias_addition_reaches_runtime_semantic_model(monkeypatch):
    """리허설의 런타임 단언: 프로필에 별칭을 넣으면 실행 경로의 시맨틱 모델에 그대로 나타난다.

    `semantic_compiler.load_semantic_model`이 정본에서 카탈로그를 만들어 쓰므로
    semantic_models YAML을 건드리지 않아도 반영된다(Plan 67 R1 배선).
    """
    from src.nodes import semantic_compiler

    profile = yaml.safe_load(_SYNC_POINTS["db_profiles"].read_text(encoding="utf-8"))
    vendor = next(a for p in profile["patterns"] if p.get("type") == "eav"
                  for a in p["known_attributes"] if a["name"] == "Vendor")
    vendor["synonyms"].append("제조업자테스트")
    monkeypatch.setattr(
        "src.schema_cache.catalog_builder.load_structure_profile", lambda *a, **k: profile)

    model = semantic_compiler.load_semantic_model("polestar_cm_gp", use_cache=False)
    dims = {d["name"]: d for d in model["pattern_a"]["dimensions"]}
    assert "제조업자테스트" in dims["Vendor"]["aliases"]


def test_semantic_model_falls_back_to_copy_when_canonical_missing(monkeypatch):
    """정본 생성이 불가하면 기존 semantic_models 사본으로 강등된다(무회귀 안전망)."""
    from src.nodes import semantic_compiler

    monkeypatch.setattr(
        "src.schema_cache.catalog_builder.load_structure_profile", lambda *a, **k: None)
    model = semantic_compiler.load_semantic_model("polestar_cm_gp", use_cache=False)
    assert model is not None and model["db_id"] == "polestar_cm_gp"


def test_uncurated_db_gets_no_catalog(monkeypatch):
    """큐레이션이 없는 DB는 카탈로그를 만들지 않는다(미선별 컬럼 유입 차단)."""
    from src.nodes import semantic_compiler

    monkeypatch.setattr(
        "src.schema_cache.catalog_builder.load_knowledge_overrides", lambda *a, **k: {})
    assert semantic_compiler.load_semantic_model("nonexistent_db", use_cache=False) is None


# ──────────────────────────────────────────────
# 6. 계층 taxonomy — parent 스탬프 + 정규화 블록 (Plan 67 N4 / D-133)
# ──────────────────────────────────────────────

SYNTHETIC_TAXONOMY = {
    "taxonomy": {
        "용량류": {
            "aliases": ["사이즈"],
            "dimensions": ["Capacity", "Legacy", "없는속성"],
        },
        "지표": {"measures": ["asset.Storage/IORate"]},
        "빈상위어": {"dimensions": ["없는속성"]},
    }
}


def _build_with_taxonomy(extra: dict | None = None) -> dict:
    overrides = deep_merge(SYNTHETIC_OVERRIDES, deep_merge(SYNTHETIC_TAXONOMY, extra or {}))
    return build_catalog(SYNTHETIC_META, db_id="synthetic", overrides=overrides)


class TestTaxonomy:
    """상위어 선언이 항목 parent와 taxonomy 블록으로 반영된다."""

    def test_parent_stamped_on_dimension_and_measure(self):
        catalog = _build_with_taxonomy()
        dims = {d["name"]: d for d in catalog["pattern_a"]["dimensions"]}
        assert dims["Capacity"]["parent"] == "용량류"
        assert "parent" not in dims["Firmware"]        # 선언되지 않은 항목은 무부모
        assert catalog["pattern_b"]["measures"][0]["parent"] == "지표"

    def test_block_lists_only_catalog_children(self):
        """수록되지 않은 하위(exclude·오타)는 블록에서 빠진다 — db별 exclude 차이 흡수."""
        taxonomy = _build_with_taxonomy()["taxonomy"]
        assert taxonomy["용량류"]["dimensions"] == ["Capacity"]  # Legacy는 exclude, 없는속성은 오타
        assert taxonomy["용량류"]["aliases"] == ["사이즈"]
        assert taxonomy["지표"]["measures"] == [
            {"resource_type": "asset.Storage", "definition_name": "IORate"}
        ]

    def test_hypernym_without_any_child_is_dropped(self):
        assert "빈상위어" not in _build_with_taxonomy()["taxonomy"]

    def test_first_parent_wins_on_duplicate_declaration(self):
        """하위 항목의 부모는 하나만 둔다(다중 부모는 계층이 아니라 그래프)."""
        catalog = _build_with_taxonomy({"taxonomy": {"두번째": {"dimensions": ["Capacity"]}}})
        dims = {d["name"]: d for d in catalog["pattern_a"]["dimensions"]}
        assert dims["Capacity"]["parent"] == "용량류"
        assert "두번째" not in catalog["taxonomy"]

    def test_no_declaration_means_no_taxonomy_key(self):
        assert "taxonomy" not in _build_synthetic()

    def test_overrides_input_is_not_mutated_by_stamping(self):
        """parent 스탬프가 호출자의 오버라이드 dict를 오염시키지 않는다."""
        overrides = deep_merge(SYNTHETIC_OVERRIDES, SYNTHETIC_TAXONOMY)
        build_catalog(SYNTHETIC_META, db_id="synthetic", overrides=overrides)
        assert "parent" not in overrides["pattern_b"]["measures"][0]

    def test_real_catalog_stages_core_terms_only(self):
        """실 카탈로그: 핵심 용어만 계층화됐다(전면 조화 금지 — ontology drift 경고)."""
        overrides = load_knowledge_overrides("polestar_cm_gp", knowledge_dir=str(KNOWLEDGE_DIR))
        profile = yaml.safe_load(
            (PROFILE_DIR / "polestar_cm_gp.yaml").read_text(encoding="utf-8"))
        catalog = build_catalog(profile, db_id="polestar_cm_gp", overrides=overrides)
        taxonomy = catalog["taxonomy"]
        assert set(taxonomy) == {"사용률", "코어", "모델"}
        assert taxonomy["코어"]["dimensions"] == ["LOGICALCORE", "PHYSICALCORE"]
        assert taxonomy["모델"]["dimensions"] == ["Model", "MODEL"]
        # 사용률 하위는 Utilization 3종뿐 — 디스크 IO(MaxIORate)는 사용률이 아니라 무부모.
        assert [m["resource_type"] for m in taxonomy["사용률"]["measures"]] == [
            "server.Cpus", "server.Memory", "server.FileSystems"
        ]
        by_key = {
            (m["resource_type"], m["definition_name"]): m
            for m in catalog["pattern_b"]["measures"]
        }
        assert by_key[("server.Disks", "MaxIORate")].get("parent") is None

    def test_catalog_diff_excludes_additive_taxonomy(self):
        """동결 사본 대조에서 N4 추가분만 제외된다(다른 필드는 그대로 검출)."""
        from scripts.catalog_diff import build_for, strip_additive

        generated, err = build_for("polestar_cm_gp")
        assert err is None
        stripped = strip_additive(generated)
        assert "taxonomy" in generated and "taxonomy" not in stripped
        assert all(
            "parent" not in d for d in stripped["pattern_a"]["dimensions"]
        )
        assert all("parent" not in m for m in stripped["pattern_b"]["measures"])
        # 제외는 additive 키에만 적용된다 — 별칭이 달라지면 여전히 차이로 잡힌다.
        stripped["pattern_a"]["dimensions"][0]["aliases"] = ["변조"]
        assert stripped != strip_additive(generated)


def test_profile_overlay_merge_mechanism():
    """db_profiles 오버레이(공통 베이스 + db별 diff) 병합 기제 — YAML 분할은 보류(Plan 67 R1-5).

    gp↔yd 실측 차이가 3줄뿐이라 지금 파일을 쪼개는 것은 실익이 없어 보류했다(팀 리드 승인).
    병합 자체는 `deep_merge`로 처리 가능함을 실제 프로필 형태로 확인해 둔다.
    """
    base = yaml.safe_load((PROFILE_DIR / "polestar_cm_gp.yaml").read_text(encoding="utf-8"))
    overlay = {"column_synonyms": {"cmm_resource.name": {"words": ["등록명"]}}}
    merged = deep_merge(base, overlay)
    assert merged["column_synonyms"]["cmm_resource.name"]["words"] == ["등록명"]
    assert merged["column_synonyms"]["cmm_resource.name"]["description"] == \
        base["column_synonyms"]["cmm_resource.name"]["description"]
    assert merged["patterns"] == base["patterns"]
