"""SynonymLoader 및 RedisSchemaCache 확장 메서드 테스트."""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from src.schema_cache.redis_cache import RedisSchemaCache
from src.schema_cache.synonym_loader import SynonymLoader, SynonymLoadResult


# === Fixtures ===


@pytest.fixture
def redis_config():
    """테스트용 RedisConfig."""
    config = MagicMock()
    config.host = "localhost"
    config.port = 6379
    config.db = 0
    config.password = ""
    config.ssl = False
    config.socket_timeout = 5
    return config


@pytest.fixture
def mock_redis():
    """Mock Redis 클라이언트."""
    redis = AsyncMock()
    redis.ping = AsyncMock(return_value=True)
    return redis


@pytest.fixture
def cache(redis_config, mock_redis):
    """연결된 RedisSchemaCache 인스턴스."""
    c = RedisSchemaCache(redis_config)
    c._redis = mock_redis
    c._connected = True
    return c


# === YAML 테스트 데이터 ===

SAMPLE_YAML_DATA = """\
version: "1.0"
domain: "test"
updated_at: "2026-03-20"
columns:
  HOSTNAME:
    description: "서버의 호스트명"
    words: ["호스트명", "서버명"]
  IPADDRESS:
    description: "IP 주소"
    words: ["아이피", "IP"]
resource_type_values:
  "server.Cpu":
    words: ["CPU", "프로세서"]
eav_name_values:
  "OSType":
    words: ["운영체제", "OS"]
"""

SAMPLE_JSON_DATA = {
    "version": "1.0",
    "domain": "test",
    "updated_at": "2026-03-20",
    "columns": {
        "HOSTNAME": {
            "description": "서버의 호스트명",
            "words": ["호스트명", "서버명"],
        },
        "IPADDRESS": {
            "description": "IP 주소",
            "words": ["아이피", "IP"],
        },
    },
    "resource_type_values": {
        "server.Cpu": {
            "words": ["CPU", "프로세서"],
        },
    },
    "eav_name_values": {
        "OSType": {
            "words": ["운영체제", "OS"],
        },
    },
}


# === Class 1: TestRedisResourceTypeSynonyms ===


class TestRedisResourceTypeSynonyms:
    """RedisSchemaCache의 resource_type 및 eav_name 관련 메서드 테스트."""

    @pytest.mark.asyncio
    async def test_save_resource_type_synonyms(self, cache, mock_redis):
        """save_resource_type_synonyms가 hset을 올바르게 호출한다."""
        synonyms = {
            "server.Cpu": ["CPU", "씨피유"],
            "server.Memory": ["메모리", "RAM"],
        }
        result = await cache.save_resource_type_synonyms(synonyms)

        assert result is True
        call_args = mock_redis.hset.call_args
        assert call_args[0][0] == "synonyms:resource_types"
        mapping = call_args.kwargs.get("mapping") or call_args[1].get("mapping")

        # 각 값이 JSON 배열 형태인지 검증
        parsed_cpu = json.loads(mapping["server.Cpu"])
        assert isinstance(parsed_cpu, list)
        assert parsed_cpu == ["CPU", "씨피유"]

        parsed_mem = json.loads(mapping["server.Memory"])
        assert parsed_mem == ["메모리", "RAM"]

    @pytest.mark.asyncio
    async def test_load_resource_type_synonyms(self, cache, mock_redis):
        """load_resource_type_synonyms가 dict[str, list[str]]을 올바르게 반환한다."""
        mock_redis.hgetall = AsyncMock(return_value={
            "server.Cpu": json.dumps(["CPU", "씨피유"]),
            "server.Memory": json.dumps(["메모리", "RAM"]),
        })

        result = await cache.load_resource_type_synonyms()

        assert result == {
            "server.Cpu": ["CPU", "씨피유"],
            "server.Memory": ["메모리", "RAM"],
        }

    @pytest.mark.asyncio
    async def test_load_resource_type_synonyms_empty(self, cache, mock_redis):
        """빈 결과를 올바르게 처리한다."""
        mock_redis.hgetall = AsyncMock(return_value={})

        result = await cache.load_resource_type_synonyms()

        assert result == {}

    @pytest.mark.asyncio
    async def test_save_eav_name_synonyms(self, cache, mock_redis):
        """save_eav_name_synonyms가 hset을 올바르게 호출한다."""
        synonyms = {
            "OSType": ["운영체제", "OS"],
            "CPUCount": ["CPU 개수", "코어수"],
        }
        result = await cache.save_eav_name_synonyms(synonyms)

        assert result is True
        call_args = mock_redis.hset.call_args
        assert call_args[0][0] == "synonyms:eav_names"
        mapping = call_args.kwargs.get("mapping") or call_args[1].get("mapping")

        parsed_os = json.loads(mapping["OSType"])
        assert parsed_os == ["운영체제", "OS"]

    @pytest.mark.asyncio
    async def test_load_eav_name_synonyms(self, cache, mock_redis):
        """load_eav_name_synonyms가 dict[str, list[str]]을 올바르게 반환한다."""
        mock_redis.hgetall = AsyncMock(return_value={
            "OSType": json.dumps(["운영체제", "OS"]),
            "CPUCount": json.dumps(["CPU 개수", "코어수"]),
        })

        result = await cache.load_eav_name_synonyms()

        assert result == {
            "OSType": ["운영체제", "OS"],
            "CPUCount": ["CPU 개수", "코어수"],
        }


# === Class 2: TestSynonymLoaderYaml ===


class TestSynonymLoaderYaml:
    """YAML 로드 테스트."""

    @pytest.mark.asyncio
    async def test_load_from_yaml_basic(self, cache, mock_redis):
        """임시 YAML 파일에서 유사단어를 로드하고 결과를 검증한다."""
        # add_global_synonym mock 설정 (병합 모드)
        mock_redis.hget = AsyncMock(return_value=None)

        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        )
        try:
            tmp.write(SAMPLE_YAML_DATA)
            tmp.close()

            loader = SynonymLoader(redis_cache=cache)
            result = await loader.load_from_yaml(tmp.name, merge=True)

            # SynonymLoadResult 검증
            assert result.status == "success"
            assert result.columns_loaded == 2  # HOSTNAME, IPADDRESS
            assert result.resource_types_loaded == 1  # server.Cpu
            assert result.eav_names_loaded == 1  # OSType
            # total_words: HOSTNAME(2) + IPADDRESS(2) + server.Cpu(2) + OSType(2) = 8
            assert result.total_words == 8
            assert result.merge_mode is True

            # merge=True이므로 add_global_synonym이 호출되어야 함
            # HOSTNAME, IPADDRESS 각각에 대해 호출
            add_calls = [
                c for c in mock_redis.hget.call_args_list
                if c[0][0] == "synonyms:global"
            ]
            # hget은 add_global_synonym 내부에서 호출됨
            assert len(add_calls) >= 2

            # save_resource_type_synonyms 호출 확인
            # hset이 synonyms:resource_types 키로 호출되어야 함
            rt_calls = [
                c for c in mock_redis.hset.call_args_list
                if len(c[0]) > 0 and c[0][0] == "synonyms:resource_types"
            ]
            assert len(rt_calls) >= 1

            # save_eav_name_synonyms 호출 확인
            eav_calls = [
                c for c in mock_redis.hset.call_args_list
                if len(c[0]) > 0 and c[0][0] == "synonyms:eav_names"
            ]
            assert len(eav_calls) >= 1
        finally:
            os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_load_from_yaml_merge_false(self, cache, mock_redis):
        """merge=False이면 save_global_synonyms를 호출한다."""
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        )
        try:
            tmp.write(SAMPLE_YAML_DATA)
            tmp.close()

            loader = SynonymLoader(redis_cache=cache)
            result = await loader.load_from_yaml(tmp.name, merge=False)

            assert result.status == "success"
            assert result.merge_mode is False

            # merge=False이면 save_global_synonyms (hset with mapping) 호출
            global_calls = [
                c for c in mock_redis.hset.call_args_list
                if len(c[0]) > 0 and c[0][0] == "synonyms:global"
            ]
            assert len(global_calls) >= 1
        finally:
            os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_load_from_yaml_file_not_found(self, cache):
        """존재하지 않는 파일이면 status='error'를 반환한다."""
        loader = SynonymLoader(redis_cache=cache)
        result = await loader.load_from_yaml("/nonexistent/path/test.yaml")

        assert result.status == "error"
        assert "파일" in result.message


# === Class 3: TestSynonymLoaderJson ===


class TestSynonymLoaderJson:
    """JSON 로드 테스트."""

    @pytest.mark.asyncio
    async def test_load_from_json_basic(self, cache, mock_redis):
        """임시 JSON 파일에서 유사단어를 로드하고 결과를 검증한다."""
        mock_redis.hget = AsyncMock(return_value=None)

        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        try:
            json.dump(SAMPLE_JSON_DATA, tmp, ensure_ascii=False)
            tmp.close()

            loader = SynonymLoader(redis_cache=cache)
            result = await loader.load_from_json(tmp.name, merge=True)

            assert result.status == "success"
            assert result.columns_loaded == 2
            assert result.resource_types_loaded == 1
            assert result.eav_names_loaded == 1
            assert result.total_words == 8
        finally:
            os.unlink(tmp.name)


# === Class 4: TestSynonymLoaderAuto ===


class TestSynonymLoaderAuto:
    """자동 감지 테스트."""

    @pytest.mark.asyncio
    async def test_load_auto_yaml(self, cache, mock_redis):
        """.yaml 확장자이면 load_from_yaml을 호출한다."""
        mock_redis.hget = AsyncMock(return_value=None)

        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        )
        try:
            tmp.write(SAMPLE_YAML_DATA)
            tmp.close()

            loader = SynonymLoader(redis_cache=cache)
            result = await loader.load_auto(tmp.name)

            assert result.status == "success"
            assert result.columns_loaded == 2
        finally:
            os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_load_auto_json(self, cache, mock_redis):
        """.json 확장자이면 load_from_json을 호출한다."""
        mock_redis.hget = AsyncMock(return_value=None)

        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        try:
            json.dump(SAMPLE_JSON_DATA, tmp, ensure_ascii=False)
            tmp.close()

            loader = SynonymLoader(redis_cache=cache)
            result = await loader.load_auto(tmp.name)

            assert result.status == "success"
            assert result.columns_loaded == 2
        finally:
            os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_load_auto_unsupported(self, cache):
        """.txt 확장자이면 status='error'를 반환한다."""
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        )
        try:
            tmp.write("test")
            tmp.close()

            loader = SynonymLoader(redis_cache=cache)
            result = await loader.load_auto(tmp.name)

            assert result.status == "error"
            assert "지원하지 않는" in result.message
        finally:
            os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_load_auto_default_path(self, cache):
        """file_path=None이면 DEFAULT_FILE을 사용한다."""
        loader = SynonymLoader(redis_cache=cache)

        with patch.object(loader, "load_from_yaml") as mock_load:
            mock_load.return_value = SynonymLoadResult(
                status="success",
                file_path=SynonymLoader.DEFAULT_FILE,
            )
            result = await loader.load_auto(file_path=None)

            mock_load.assert_called_once_with(
                SynonymLoader.DEFAULT_FILE, merge=True
            )


# === Class 5: TestSynonymLoaderCheckAndReload ===


class TestSynonymLoaderCheckAndReload:
    """변경 감지 테스트."""

    @pytest.mark.asyncio
    async def test_check_and_reload_no_previous(self, cache):
        """이전 로드 이력이 없으면 None을 반환한다."""
        loader = SynonymLoader(redis_cache=cache)
        # _last_file_path가 None (기본값)
        result = await loader.check_and_reload()
        assert result is None

    @pytest.mark.asyncio
    async def test_check_and_reload_no_change(self, cache):
        """파일 mtime이 동일하면 None을 반환한다."""
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        )
        try:
            tmp.write(SAMPLE_YAML_DATA)
            tmp.close()

            loader = SynonymLoader(redis_cache=cache)
            loader._last_file_path = tmp.name
            loader._last_file_mtime = os.path.getmtime(tmp.name)

            result = await loader.check_and_reload()
            assert result is None
        finally:
            os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_check_and_reload_changed(self, cache, mock_redis):
        """파일 mtime이 변경되면 load_auto를 호출하여 결과를 반환한다."""
        mock_redis.hget = AsyncMock(return_value=None)

        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        )
        try:
            tmp.write(SAMPLE_YAML_DATA)
            tmp.close()

            loader = SynonymLoader(redis_cache=cache)
            loader._last_file_path = tmp.name
            # mtime을 과거로 설정하여 변경 감지 유도
            loader._last_file_mtime = os.path.getmtime(tmp.name) - 100

            result = await loader.check_and_reload()
            assert result is not None
            assert result.status == "success"
        finally:
            os.unlink(tmp.name)


# === Class 6: TestSynonymLoaderExport ===


class TestSynonymLoaderExport:
    """내보내기 테스트."""

    @pytest.mark.asyncio
    async def test_export_to_yaml(self, cache, mock_redis):
        """Redis 데이터를 YAML 파일로 내보내고 파일 내용을 검증한다."""
        # mock 설정
        mock_redis.hgetall = AsyncMock(side_effect=self._mock_hgetall)

        loader = SynonymLoader(redis_cache=cache)

        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        )
        tmp.close()
        try:
            success = await loader.export_to_yaml(tmp.name)
            assert success is True

            # 생성된 YAML 파일 읽기
            with open(tmp.name, encoding="utf-8") as f:
                data = yaml.safe_load(f)

            # 섹션 존재 확인
            assert "columns" in data
            assert "resource_type_values" in data
            assert "eav_name_values" in data
            assert "version" in data

            # 데이터 일관성 검증
            assert "HOSTNAME" in data["columns"]
            assert data["columns"]["HOSTNAME"]["words"] == ["서버명", "호스트명"]
            assert data["columns"]["HOSTNAME"]["description"] == "서버의 호스트명"

            assert "server.Cpu" in data["resource_type_values"]
            assert data["resource_type_values"]["server.Cpu"]["words"] == [
                "CPU", "프로세서"
            ]

            assert "OSType" in data["eav_name_values"]
            assert data["eav_name_values"]["OSType"]["words"] == [
                "운영체제", "OS"
            ]
        finally:
            os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_export_to_json(self, cache, mock_redis):
        """Redis 데이터를 JSON 파일로 내보내고 파일 내용을 검증한다."""
        # mock 설정
        mock_redis.hgetall = AsyncMock(side_effect=self._mock_hgetall)

        loader = SynonymLoader(redis_cache=cache)

        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        tmp.close()
        try:
            success = await loader.export_to_json(tmp.name)
            assert success is True

            # 생성된 JSON 파일 읽기
            with open(tmp.name, encoding="utf-8") as f:
                data = json.load(f)

            # 섹션 존재 확인
            assert "columns" in data
            assert "resource_type_values" in data
            assert "eav_name_values" in data
            assert "version" in data

            # 데이터 일관성 검증
            assert "HOSTNAME" in data["columns"]
            assert data["columns"]["HOSTNAME"]["words"] == ["서버명", "호스트명"]

            assert "server.Cpu" in data["resource_type_values"]
            assert data["resource_type_values"]["server.Cpu"]["words"] == [
                "CPU", "프로세서"
            ]

            assert "OSType" in data["eav_name_values"]
            assert data["eav_name_values"]["OSType"]["words"] == [
                "운영체제", "OS"
            ]
        finally:
            os.unlink(tmp.name)

    @staticmethod
    def _mock_hgetall(key: str) -> dict:
        """Redis hgetall mock: 키별로 다른 데이터를 반환한다."""
        if key == "synonyms:global":
            return {
                "HOSTNAME": json.dumps({
                    "words": ["서버명", "호스트명"],
                    "description": "서버의 호스트명",
                }),
                "IPADDRESS": json.dumps({
                    "words": ["아이피", "IP"],
                    "description": "IP 주소",
                }),
            }
        elif key == "synonyms:resource_types":
            return {
                "server.Cpu": json.dumps(["CPU", "프로세서"]),
            }
        elif key == "synonyms:eav_names":
            return {
                "OSType": json.dumps(["운영체제", "OS"]),
            }
        return {}


# === Class 7: TestSynonymLoadResult ===


class TestSynonymLoadResult:
    """SynonymLoadResult 데이터클래스 테스트."""

    def test_default_values(self):
        """기본값이 올바르게 설정된다."""
        result = SynonymLoadResult(status="success", file_path="test.yaml")

        assert result.status == "success"
        assert result.file_path == "test.yaml"
        assert result.columns_loaded == 0
        assert result.resource_types_loaded == 0
        assert result.eav_names_loaded == 0
        assert result.total_words == 0
        assert result.merge_mode is True
        assert result.errors == []
        assert result.message == ""

    def test_custom_values(self):
        """사용자 지정값이 올바르게 설정된다."""
        result = SynonymLoadResult(
            status="partial",
            file_path="/path/to/synonyms.yaml",
            columns_loaded=10,
            resource_types_loaded=5,
            eav_names_loaded=3,
            total_words=50,
            merge_mode=False,
            errors=["경고: 일부 컬럼 누락"],
            message="부분 로드 완료",
        )

        assert result.status == "partial"
        assert result.file_path == "/path/to/synonyms.yaml"
        assert result.columns_loaded == 10
        assert result.resource_types_loaded == 5
        assert result.eav_names_loaded == 3
        assert result.total_words == 50
        assert result.merge_mode is False
        assert result.errors == ["경고: 일부 컬럼 누락"]
        assert result.message == "부분 로드 완료"
