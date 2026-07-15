"""유사어 시드 derive·load·export 테스트 (Plan 61 트랙 B 후속).

- derive: 시맨틱 모델·프로필 → 시드 파일 결정적 생성(매핑 규칙·최소길이 가드·재현성)
- load_seed_yaml: 합집합 병합(무손실)·source 태깅·db_id 필수
- export_seed_yaml: per-DB 사전 스냅샷 내보내기
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from src.schema_cache.synonym_loader import SynonymLoader

REPO = Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def seeds_mod(tmp_path, monkeypatch):
    """scripts/synonym_seeds.py를 tmp 디렉터리 기준으로 로드한다."""
    spec = importlib.util.spec_from_file_location(
        "synonym_seeds", REPO / "scripts" / "synonym_seeds.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["synonym_seeds"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "SEMANTIC_DIR", tmp_path / "semantic_models")
    monkeypatch.setattr(mod, "PROFILE_DIR", tmp_path / "db_profiles")
    monkeypatch.setattr(mod, "SEED_DIR", tmp_path / "synonym_seeds")
    (tmp_path / "semantic_models").mkdir()
    (tmp_path / "db_profiles").mkdir()
    return mod


def _write_sources(tmp_path: Path) -> None:
    model = {
        "pattern_a": {
            "dimensions": [
                {"name": "hostname", "source": "direct", "column": "hostname",
                 "aliases": ["호스트명", "호스트네임", "x"]},  # "x"는 1글자 — 제외돼야 함
                {"name": "OSType", "source": "eav", "attribute": "OSType",
                 "aliases": ["운영체제", "OS종류"]},
            ]
        },
        "pattern_b": {
            "metric_tables": {"hour": "cmm_metric_stat_h", "month": "cmm_metric_stat_m"},
            "value_columns": {"avg": "avg_val", "max": "max_val"},
            "measures": [
                {"resource_type": "server.Cpus", "definition_name": "Utilization",
                 "aliases": ["CPU 사용률"]},
                {"resource_type": "server.Memory", "definition_name": "Utilization",
                 "aliases": ["메모리 사용률", "메모리"]},
            ],
        },
        "pattern_c": {"severity_map": {"심각": 3, "경고": 2}},
    }
    profile = {
        "known_attributes": [
            {"name": "OSType", "synonyms": ["OS 타입"]},
            {"name": "TotalSize", "synonyms": ["메모리 용량"]},
        ]
    }
    (tmp_path / "semantic_models" / "testdb.yaml").write_text(
        yaml.safe_dump(model, allow_unicode=True), encoding="utf-8"
    )
    (tmp_path / "db_profiles" / "testdb.yaml").write_text(
        yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8"
    )


class TestDerive:
    def test_mapping_rules(self, seeds_mod, tmp_path):
        _write_sources(tmp_path)
        out = seeds_mod.derive_seed("testdb")
        data = yaml.safe_load(out.read_text(encoding="utf-8"))

        cols = data["column_synonyms"]
        # direct dim → cmm_resource.{column}, 1글자 "x" 제외
        assert cols["polestar.cmm_resource.hostname"] == ["호스트네임", "호스트명"]
        # eav dim → core_config_prop.name 게이트 키
        assert "운영체제" in cols["polestar.core_config_prop.name"]
        # 패턴 B → 각 metric 테이블 avg 컬럼 키에 measure aliases 합본
        for tbl in ("cmm_metric_stat_h", "cmm_metric_stat_m"):
            assert set(cols[f"polestar.{tbl}.avg_val"]) == {
                "CPU 사용률", "메모리 사용률", "메모리"
            }
        # 패턴 C → severity 단어·column_values 승격
        assert set(cols["polestar.cmm_alarm.alarmseverity"]) == {"심각", "경고"}
        assert data["column_values"]["cmm_alarm.alarmseverity"]["심각"] == {
            "op": "=", "value": 3
        }
        # eav_names: 시맨틱 모델 + 프로필 known_attributes 병합
        assert set(data["eav_names"]["OSType"]) == {"OS 타입", "OS종류", "운영체제"}
        assert data["eav_names"]["TotalSize"] == ["메모리 용량"]
        assert data["source_tag"] == "operator"

    def test_deterministic(self, seeds_mod, tmp_path):
        _write_sources(tmp_path)
        first = seeds_mod.derive_seed("testdb").read_text(encoding="utf-8")
        second = seeds_mod.derive_seed("testdb").read_text(encoding="utf-8")
        assert first == second

    def test_missing_model_returns_none(self, seeds_mod):
        assert seeds_mod.derive_seed("nope") is None


@pytest.fixture
def mock_redis_cache():
    cache = AsyncMock()
    cache.add_synonyms = AsyncMock(return_value=True)
    cache.add_global_synonym = AsyncMock(return_value=True)
    cache.load_eav_name_synonyms = AsyncMock(
        return_value={"OSType": ["기존단어"]}
    )
    cache.save_eav_name_synonyms = AsyncMock(return_value=True)
    cache.load_column_value_synonyms = AsyncMock(
        return_value={"CMM_ALARM.ALARMSEVERITY": {"심각": {"op": "=", "value": 9}}}
    )
    cache.save_column_value_synonyms = AsyncMock(return_value=True)
    cache.load_synonyms = AsyncMock(
        return_value={"polestar.cmm_resource.hostname": ["호스트명"]}
    )
    return cache


def _write_seed(tmp_path: Path, **overrides) -> Path:
    payload = {
        "db_id": "testdb",
        "source_tag": "operator",
        "column_synonyms": {
            "polestar.cmm_resource.hostname": ["호스트명", "호스트네임", "y"],
        },
        "eav_names": {"OSType": ["운영체제"]},
        "column_values": {
            "cmm_alarm.alarmseverity": {
                "심각": {"op": "=", "value": 3},
                "경고": {"op": "=", "value": 2},
            }
        },
    }
    payload.update(overrides)
    p = tmp_path / "seed.yaml"
    p.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return p


class TestLoadSeed:
    @pytest.mark.asyncio
    async def test_load_merges_with_source_tag(self, tmp_path, mock_redis_cache):
        loader = SynonymLoader(redis_cache=mock_redis_cache)
        result = await loader.load_seed_yaml(str(_write_seed(tmp_path)))

        assert result.status == "success"
        # column_synonyms: add_synonyms(합집합 API)로 병합 + source 태깅, 1글자 "y" 제외
        mock_redis_cache.add_synonyms.assert_awaited_once_with(
            "testdb", "polestar.cmm_resource.hostname",
            ["호스트명", "호스트네임"], source="operator",
        )
        # eav_names: read-union-save — 기존 단어 보존 + 신규 합집합
        saved = mock_redis_cache.save_eav_name_synonyms.await_args.args[0]
        assert saved["OSType"] == ["기존단어", "운영체제"]
        # column_values: 기존 등록 우선(심각=9 유지), 신규(경고) 보충
        saved_cv = mock_redis_cache.save_column_value_synonyms.await_args.args[0]
        merged = saved_cv["CMM_ALARM.ALARMSEVERITY"]
        assert merged["심각"] == {"op": "=", "value": 9}
        assert merged["경고"] == {"op": "=", "value": 2}

    @pytest.mark.asyncio
    async def test_load_requires_db_id(self, tmp_path, mock_redis_cache):
        loader = SynonymLoader(redis_cache=mock_redis_cache)
        seed = _write_seed(tmp_path, db_id=None)
        result = await loader.load_seed_yaml(str(seed))
        assert result.status == "error"
        mock_redis_cache.add_synonyms.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_load_missing_file(self, mock_redis_cache):
        loader = SynonymLoader(redis_cache=mock_redis_cache)
        result = await loader.load_seed_yaml("/no/such/seed.yaml")
        assert result.status == "error"


class TestExportSeed:
    @pytest.mark.asyncio
    async def test_export_writes_snapshot(self, tmp_path, mock_redis_cache):
        loader = SynonymLoader(redis_cache=mock_redis_cache)
        out = tmp_path / "export" / "testdb.yaml"
        ok = await loader.export_seed_yaml("testdb", str(out))
        assert ok is True
        data = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert data["db_id"] == "testdb"
        assert data["column_synonyms"]["polestar.cmm_resource.hostname"] == ["호스트명"]
        assert "eav_names" in data and "column_values" in data
