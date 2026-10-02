"""서버 엔터티 키 매니페스트 데이터 (plans/102 X-2 · D-224 ③).

검증 계약:
1. 폴스타 프로필 4종의 `entity_keys`가 로더로 읽히고, 선언 컬럼이 **같은 프로필의 구조 정본**
   (EAV 패턴 `entity_table`·`entity_columns`)에 실존한다. `name`(등록명)은 키가 아니다(D-061 · G-3).
2. 자산관리 로컬 하네스 매니페스트(`testdata/itam/entity_keys.local.yaml`)의 컬럼이
   샌드박스 DDL에 실존하고 `per_ip`·`multi_value`가 선언돼 있다.
   런타임 로더는 testdata를 읽지 않는다(plans/95 §4.6.2).
3. **블록을 추가해도 프롬프트·카탈로그 렌더가 바뀌지 않는다** —
   `entity_keys`를 뺀 프로필과 렌더 바이트 동일.

LLM·DB·네트워크 0(D-127).
"""

from __future__ import annotations

import asyncio
import copy
import importlib
import re
from pathlib import Path

import pytest
import yaml

from src.domain.entity_key import (
    FAMILY_HOSTNAME,
    FAMILY_IP,
    ROW_MULTIPLICITY_ONE,
    ROW_MULTIPLICITY_PER_IP,
)
from src.schema_cache.catalog_builder import build_catalog, load_knowledge_overrides
from src.schema_cache.entity_key_manifest import (
    PROFILES_DIR,
    clear_manifest_cache,
    load_entity_key_manifest,
    load_manifest_file,
)

ROOT = Path(__file__).resolve().parents[2]
POLESTAR_DBS = ("polestar", "polestar_b0", "polestar_cm_gp", "polestar_cm_yd")
ITAM_LOCAL = ROOT / "testdata" / "itam" / "entity_keys.local.yaml"
ITAM_DDL = ROOT / "testdata" / "itam" / "init" / "01_schema.sql"


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_manifest_cache()
    yield
    clear_manifest_cache()


def _profile(db_id: str) -> dict:
    return yaml.safe_load((PROFILES_DIR / f"{db_id}.yaml").read_text(encoding="utf-8"))


def _entity_columns(profile: dict, table: str) -> set[str]:
    cols: set[str] = set()
    for pattern in profile.get("patterns") or []:
        if str(pattern.get("entity_table", "")).lower() != table.lower():
            continue
        for item in pattern.get("entity_columns") or []:
            if isinstance(item, dict) and item.get("name"):
                cols.add(str(item["name"]).lower())
    return cols


# ──────────────────────────────────────────────
# 1. 폴스타 프로필 4종
# ──────────────────────────────────────────────


@pytest.mark.parametrize("db_id", POLESTAR_DBS)
def test_polestar_manifest_loads_with_hostname_then_ip(db_id):
    manifest = load_entity_key_manifest(db_id)
    assert manifest is not None, f"{db_id}: entity_keys 블록 없음 또는 구조 오류"
    assert manifest.entity == "server" and manifest.table == "cmm_resource"
    assert manifest.row_multiplicity == ROW_MULTIPLICITY_ONE
    assert manifest.families() == (FAMILY_HOSTNAME, FAMILY_IP)
    host, ip = manifest.keys_by_priority()
    assert (host.family, host.column, host.priority, host.compare) == (
        FAMILY_HOSTNAME,
        "hostname",
        1,
        "casefold",
    )
    assert (ip.family, ip.column, ip.priority, ip.multi_value) == (FAMILY_IP, "ipaddress", 2, False)
    assert Path(manifest.source).resolve().parent == PROFILES_DIR.resolve()


@pytest.mark.parametrize("db_id", POLESTAR_DBS)
def test_polestar_manifest_columns_exist_in_structure_profile(db_id):
    """매니페스트의 테이블·컬럼은 그 프로필 구조 정본에 선언돼 있어야 한다.

    틀린 대응은 오류가 아니라 조용한 0건을 낸다.
    """
    profile = _profile(db_id)
    manifest = load_entity_key_manifest(db_id)
    assert manifest is not None
    assert manifest.table in (profile.get("allowed_tables") or [])
    declared = _entity_columns(profile, manifest.table)
    assert declared, f"{db_id}: {manifest.table} entity_columns 선언 없음"
    for key in manifest.keys:
        assert key.column.lower() in declared, f"{db_id}: {key.column}이 구조 정본에 없음"


@pytest.mark.parametrize("db_id", POLESTAR_DBS)
def test_registered_name_is_not_a_bridge_key(db_id):
    """D-061 — 등록명(name)은 hostname이 아니다. G-3 기본 가정: hostname만 브리지 키."""
    manifest = load_entity_key_manifest(db_id)
    assert manifest is not None
    assert "name" not in {k.column.lower() for k in manifest.keys}


# ──────────────────────────────────────────────
# 2. 자산관리 로컬 하네스 매니페스트
# ──────────────────────────────────────────────


def _ddl_columns(table: str) -> set[str]:
    text = ITAM_DDL.read_text(encoding="utf-8")
    m = re.search(rf"CREATE TABLE `{re.escape(table)}` \((.*?)\n\)", text, re.S)
    assert m, f"DDL에 {table} 없음"
    return set(re.findall(r"^\s*`([^`]+)`", m.group(1), re.M))


def test_itam_local_manifest_shape():
    manifest = load_manifest_file(ITAM_LOCAL, "itam")
    assert manifest is not None
    assert manifest.row_multiplicity == ROW_MULTIPLICITY_PER_IP
    host, ip = manifest.keys_by_priority()
    assert (host.family, host.column, host.priority, host.compare, host.multi_value) == (
        FAMILY_HOSTNAME,
        "sevrHostName",
        1,
        "casefold",
        False,
    )
    assert (ip.family, ip.column, ip.priority, ip.multi_value) == (FAMILY_IP, "iPCtnt", 2, True)


def test_itam_local_manifest_columns_exist_in_sandbox_ddl():
    manifest = load_manifest_file(ITAM_LOCAL, "itam")
    assert manifest is not None
    columns = _ddl_columns(manifest.table)
    for key in manifest.keys:
        assert key.column in columns


def test_itam_local_manifest_marks_assumption():
    """G-4 미확정 가정 표기 — 운영 정본으로 오인되지 않게 파일 머리에 명시한다."""
    head = ITAM_LOCAL.read_text(encoding="utf-8")
    assert "G-4" in head and "가정" in head


def test_runtime_loader_never_reads_testdata():
    manifest = load_entity_key_manifest("itam")
    if (
        manifest is not None
    ):  # plans/95 W-6 이후 정본이 생기면 그 출처는 config/db_profiles여야 한다
        assert "testdata" not in manifest.source


# ──────────────────────────────────────────────
# 3. 렌더 불변 — 블록 추가 전후 바이트 동일
# ──────────────────────────────────────────────


def _structure_meta(db_id: str) -> dict:
    from src.nodes.schema_analyzer import _load_manual_profile

    profile = _load_manual_profile(db_id)
    assert profile is not None and "entity_keys" in profile
    return {k: v for k, v in profile.items() if k != "source"}


def _renders(meta: dict, db_id: str) -> tuple[str, str, str]:
    qg = importlib.import_module("src.nodes.query_generator")
    mde = importlib.import_module("src.nodes.multi_db_executor")
    schema_info = {
        "tables": {"cmm_resource": {"columns": [{"name": "hostname", "type": "varchar"}]}},
        "_structure_meta": meta,
    }
    guide = qg._format_structure_guide(meta)
    system = qg._build_system_prompt(
        schema_info,
        1000,
        active_db_id=db_id,
        polestar_db_ids={db_id},
        active_db_engine="db2" if db_id.endswith("b0") else "postgresql",
    )
    multi = asyncio.run(mde._build_multi_structure_guide(schema_info, {}, "서버 목록", db_id, None))
    return guide, system, multi


@pytest.mark.parametrize("db_id", POLESTAR_DBS)
def test_entity_keys_block_does_not_change_prompt_render(db_id):
    with_block = _structure_meta(db_id)
    without = copy.deepcopy(with_block)
    without.pop("entity_keys")
    assert _renders(with_block, db_id) == _renders(without, db_id)


@pytest.mark.parametrize("db_id", POLESTAR_DBS)
def test_entity_keys_block_does_not_change_catalog(db_id):
    with_block = _profile(db_id)
    without = copy.deepcopy(with_block)
    without.pop("entity_keys")
    overrides = load_knowledge_overrides(db_id)
    assert build_catalog(with_block, db_id=db_id, overrides=overrides) == build_catalog(
        without, db_id=db_id, overrides=overrides
    )
