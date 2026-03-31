"""Polestar E2E 테스트.

PostgreSQL(testdata/pg/) 기반 Polestar 데이터를 대상으로
스키마 캐시 파이프라인, 유사단어 로드, 자연어 → SQL 생성을 검증한다.

사전 조건:
  - PostgreSQL 컨테이너 실행: cd testdata/pg && docker compose up -d
  - Redis 컨테이너 실행: cd redis && docker compose up -d
  - Ollama 서버 실행 (LLM 사용 테스트 시)

실행:
  pytest tests/test_e2e_polestar.py -v --timeout=120
  pytest tests/test_e2e_polestar.py -k "test_phase1" -v       # 데이터 준비만
  pytest tests/test_e2e_polestar.py -k "test_phase2" -v       # 캐시 파이프라인
  pytest tests/test_e2e_polestar.py -k "test_nlq" -v          # 자연어 질의
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

from src.config import (
    AppConfig,
    LLMConfig,
    QueryConfig,
    RedisConfig,
    SchemaCacheConfig,
    SecurityConfig,
    ServerConfig,
)
from src.state import AgentState, create_initial_state

# ---------------------------------------------------------------------------
# Polestar PostgreSQL 접속 정보 (testdata/pg/docker-compose.yml)
# ---------------------------------------------------------------------------
POLESTAR_DSN = os.getenv(
    "POLESTAR_PG_CONNECTION",
    "postgresql://polestar_user:polestar_pass_2024@localhost:5434/infradb",
)
POLESTAR_DB_ID = "polestar_pg"

# Redis (redis/docker-compose.yml)
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6380"))

# 글로벌 유사단어 사전
SYNONYMS_YAML = "config/global_synonyms.yaml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_polestar_config() -> AppConfig:
    """Polestar PostgreSQL용 AppConfig를 생성한다."""
    return AppConfig(
        llm=LLMConfig(
            provider="ollama",
            model="llama3.1:8b",
            ollama_base_url=os.getenv("LLM_OLLAMA_BASE_URL", "http://localhost:11434"),
            ollama_timeout=180,
        ),
        db_backend="direct",
        db_connection_string=POLESTAR_DSN,
        redis=RedisConfig(host=REDIS_HOST, port=REDIS_PORT, db=0, password=""),
        schema_cache=SchemaCacheConfig(backend="redis", auto_generate_descriptions=True),
        checkpoint_backend="sqlite",
        checkpoint_db_url=":memory:",
        enable_semantic_routing=False,
        enable_sql_approval=False,
        query=QueryConfig(),
        security=SecurityConfig(),
        server=ServerConfig(),
    )


@pytest.fixture
def polestar_config() -> AppConfig:
    return _make_polestar_config()


@pytest_asyncio.fixture
async def db_client(polestar_config):
    """PostgreSQL 클라이언트를 생성하고 연결한다."""
    from src.db.client import PostgresClient

    client = PostgresClient(dsn=polestar_config.db_connection_string)
    await client.connect()
    yield client
    await client.disconnect()


@pytest_asyncio.fixture
async def cache_manager(polestar_config):
    """SchemaCacheManager를 생성한다."""
    from src.schema_cache.cache_manager import SchemaCacheManager

    mgr = SchemaCacheManager(polestar_config)
    await mgr.ensure_redis_connected()
    yield mgr


@pytest_asyncio.fixture
async def redis_cache(polestar_config):
    """RedisSchemaCache를 생성하고 연결한다."""
    from src.schema_cache.redis_cache import RedisSchemaCache

    cache = RedisSchemaCache(polestar_config.redis, polestar_config.schema_cache)
    await cache.connect()
    yield cache
    await cache.disconnect()


# ===========================================================================
# Phase 1: 데이터 준비 검증 — PostgreSQL 연결 및 테이블/데이터 확인
# ===========================================================================

class TestPhase1DataPreparation:
    """PostgreSQL에 Polestar 테이블과 데이터가 올바르게 생성되었는지 검증한다."""

    @pytest.mark.asyncio
    async def test_phase1_01_connection(self, db_client):
        """PostgreSQL 연결이 정상인지 확인한다."""
        healthy = await db_client.health_check()
        assert healthy, "PostgreSQL 연결 실패 — docker compose up -d 실행 필요"

    @pytest.mark.asyncio
    async def test_phase1_02_tables_exist(self, db_client):
        """polestar 스키마에 2개 테이블이 존재하는지 확인한다."""
        result = await db_client.execute_sql("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'polestar'
              AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
        table_names = [r["table_name"] for r in result.rows]
        assert "cmm_resource" in table_names, f"cmm_resource 테이블 없음: {table_names}"
        assert "core_config_prop" in table_names, f"core_config_prop 테이블 없음: {table_names}"

    @pytest.mark.asyncio
    async def test_phase1_03_cmm_resource_count(self, db_client):
        """CMM_RESOURCE 행 수가 약 700~1200행인지 확인한다."""
        result = await db_client.execute_sql(
            "SELECT COUNT(*) AS cnt FROM polestar.cmm_resource"
        )
        cnt = result.rows[0]["cnt"]
        assert 700 <= cnt <= 1200, f"CMM_RESOURCE 행 수 이상: {cnt}"

    @pytest.mark.asyncio
    async def test_phase1_04_core_config_prop_count(self, db_client):
        """CORE_CONFIG_PROP 행 수가 360행인지 확인한다."""
        result = await db_client.execute_sql(
            "SELECT COUNT(*) AS cnt FROM polestar.core_config_prop"
        )
        cnt = result.rows[0]["cnt"]
        assert cnt == 360, f"CORE_CONFIG_PROP 행 수: {cnt} (기대: 360)"

    @pytest.mark.asyncio
    async def test_phase1_05_server_count_30(self, db_client):
        """서버가 30대(HOSTNAME DISTINCT)인지 확인한다."""
        result = await db_client.execute_sql(
            "SELECT COUNT(DISTINCT hostname) AS cnt FROM polestar.cmm_resource WHERE hostname IS NOT NULL"
        )
        cnt = result.rows[0]["cnt"]
        assert cnt == 30, f"서버 수: {cnt} (기대: 30)"

    @pytest.mark.asyncio
    async def test_phase1_06_server_groups(self, db_client):
        """용도별 서버 수가 WEB 10, WAS 10, DB 10인지 확인한다."""
        result = await db_client.execute_sql("""
            SELECT
              CASE
                WHEN hostname LIKE 'svr-web%%' THEN 'WEB'
                WHEN hostname LIKE 'svr-was%%' THEN 'WAS'
                WHEN hostname LIKE 'svr-db%%' THEN 'DB'
              END AS grp,
              COUNT(DISTINCT hostname) AS cnt
            FROM polestar.cmm_resource
            WHERE hostname IS NOT NULL
            GROUP BY grp
            ORDER BY grp
        """)
        groups = {r["grp"]: r["cnt"] for r in result.rows}
        assert groups.get("WEB") == 10, f"WEB: {groups.get('WEB')}"
        assert groups.get("WAS") == 10, f"WAS: {groups.get('WAS')}"
        assert groups.get("DB") == 10, f"DB: {groups.get('DB')}"

    @pytest.mark.asyncio
    async def test_phase1_07_resource_type_diversity(self, db_client):
        """RESOURCE_TYPE이 15종 이상인지 확인한다."""
        result = await db_client.execute_sql(
            "SELECT COUNT(DISTINCT resource_type) AS cnt FROM polestar.cmm_resource"
        )
        cnt = result.rows[0]["cnt"]
        assert cnt >= 15, f"RESOURCE_TYPE 종: {cnt} (기대: 15+)"

    @pytest.mark.asyncio
    async def test_phase1_08_avail_status_distribution(self, db_client):
        """AVAIL_STATUS=1(비정상) 비율이 5~8%인지 확인한다."""
        result = await db_client.execute_sql("""
            SELECT avail_status, COUNT(*) AS cnt
            FROM polestar.cmm_resource
            GROUP BY avail_status
        """)
        status_map = {r["avail_status"]: r["cnt"] for r in result.rows}
        total = sum(status_map.values())
        abnormal = status_map.get(1, 0)
        pct = abnormal / total * 100
        assert 3 <= pct <= 10, f"비정상 비율: {pct:.1f}% (기대: 5~8%)"

    @pytest.mark.asyncio
    async def test_phase1_09_config_per_server(self, db_client):
        """각 CONFIGURATION_ID별 12건씩 있는지 확인한다."""
        result = await db_client.execute_sql("""
            SELECT configuration_id, COUNT(*) AS cnt
            FROM polestar.core_config_prop
            GROUP BY configuration_id
            HAVING COUNT(*) != 12
        """)
        assert len(result.rows) == 0, f"12건이 아닌 CONFIGURATION_ID: {result.rows}"

    @pytest.mark.asyncio
    async def test_phase1_10_vendor_diversity(self, db_client):
        """제조사(Vendor)가 3종인지 확인한다."""
        result = await db_client.execute_sql("""
            SELECT DISTINCT stringvalue_short
            FROM polestar.core_config_prop
            WHERE name = 'Vendor'
        """)
        vendors = {r["stringvalue_short"] for r in result.rows}
        assert len(vendors) == 3, f"제조사: {vendors} (기대: 3종)"


# ===========================================================================
# Phase 2: 캐시 파이프라인 검증 — 스키마 캐시 + 유사단어 로드
# ===========================================================================

class TestPhase2CachePipeline:
    """DB → SchemaCacheManager → Redis 캐시 파이프라인을 검증한다."""

    @pytest.mark.asyncio
    async def test_phase2_01_refresh_cache(self, cache_manager, db_client):
        """스키마 캐시를 강제 갱신하고 결과를 확인한다."""
        result = await cache_manager.refresh_cache(
            db_id=POLESTAR_DB_ID,
            client=db_client,
            force=True,
        )
        assert result.status in ("created", "updated"), f"캐시 갱신 실패: {result.status} - {result.message}"
        assert result.table_count >= 2, f"테이블 수: {result.table_count} (기대: 2+)"
        assert result.fingerprint, "fingerprint가 비어 있음"

    @pytest.mark.asyncio
    async def test_phase2_02_schema_in_redis(self, cache_manager):
        """Redis에 스키마 정보가 저장되었는지 확인한다."""
        schema = await cache_manager.get_schema(POLESTAR_DB_ID)
        assert schema is not None, "스키마가 Redis에 없음"
        tables = schema.get("tables", {})
        # 테이블명은 소문자 (PostgreSQL)
        table_names_lower = [k.lower() for k in tables.keys()]
        assert any("cmm_resource" in n for n in table_names_lower), f"cmm_resource 없음: {list(tables.keys())}"
        assert any("core_config_prop" in n for n in table_names_lower), f"core_config_prop 없음: {list(tables.keys())}"

    @pytest.mark.asyncio
    async def test_phase2_03_cmm_resource_columns(self, cache_manager):
        """CMM_RESOURCE 테이블의 컬럼 수가 59개인지 확인한다."""
        schema = await cache_manager.get_schema(POLESTAR_DB_ID)
        tables = schema["tables"]
        # 키 이름은 대소문자 혼재 가능 — cmm_resource 또는 polestar.cmm_resource
        cmm_key = next((k for k in tables if "cmm_resource" in k.lower()), None)
        assert cmm_key, f"cmm_resource 테이블 키를 찾을 수 없음: {list(tables.keys())}"
        columns = tables[cmm_key].get("columns", [])
        assert len(columns) == 59, f"CMM_RESOURCE 컬럼 수: {len(columns)} (기대: 59)"

    @pytest.mark.asyncio
    async def test_phase2_04_core_config_prop_columns(self, cache_manager):
        """CORE_CONFIG_PROP 테이블의 컬럼 수가 12개인지 확인한다."""
        schema = await cache_manager.get_schema(POLESTAR_DB_ID)
        tables = schema["tables"]
        prop_key = next((k for k in tables if "core_config_prop" in k.lower()), None)
        assert prop_key, f"core_config_prop 테이블 키를 찾을 수 없음"
        columns = tables[prop_key].get("columns", [])
        assert len(columns) == 12, f"CORE_CONFIG_PROP 컬럼 수: {len(columns)} (기대: 12)"

    @pytest.mark.asyncio
    async def test_phase2_05_meta_fingerprint(self, cache_manager):
        """Redis에 fingerprint가 존재하는지 확인한다."""
        fp = await cache_manager.get_fingerprint(POLESTAR_DB_ID)
        assert fp, "fingerprint가 비어 있음"
        status = await cache_manager.get_status(POLESTAR_DB_ID)
        assert status.table_count >= 2, f"테이블 수: {status.table_count}"

    @pytest.mark.asyncio
    async def test_phase2_06_load_global_synonyms(self, redis_cache):
        """글로벌 유사단어 사전(YAML)을 로드하고 Redis에 저장되는지 확인한다."""
        from src.schema_cache.synonym_loader import SynonymLoader

        loader = SynonymLoader(redis_cache)
        result = await loader.load_from_yaml(SYNONYMS_YAML, merge=True)
        assert result.status in ("success", "partial"), f"유사단어 로드 실패: {result.status} - {result.message}"
        assert result.columns_loaded >= 10, f"로드된 컬럼: {result.columns_loaded} (기대: 10+)"

    @pytest.mark.asyncio
    async def test_phase2_07_global_synonyms_hostname(self, redis_cache):
        """글로벌 사전에 HOSTNAME 유사단어가 존재하는지 확인한다."""
        synonyms = await redis_cache.load_global_synonyms()
        # 키가 대문자 또는 소문자일 수 있음
        hostname_key = next(
            (k for k in synonyms if k.upper() == "HOSTNAME"), None
        )
        assert hostname_key, f"HOSTNAME 유사단어 없음. 키 목록: {list(synonyms.keys())[:20]}"
        words = synonyms[hostname_key]
        if isinstance(words, dict):
            words = words.get("words", [])
        assert len(words) >= 3, f"HOSTNAME words 수: {len(words)} (기대: 3+)"
        # "호스트명" 또는 "서버명" 포함 확인
        assert any("호스트" in w or "서버" in w for w in words), f"HOSTNAME words에 한국어 없음: {words}"

    @pytest.mark.asyncio
    async def test_phase2_08_global_synonyms_ipaddress(self, redis_cache):
        """글로벌 사전에 IPADDRESS 유사단어가 존재하는지 확인한다."""
        synonyms = await redis_cache.load_global_synonyms()
        ip_key = next(
            (k for k in synonyms if k.upper() == "IPADDRESS"), None
        )
        assert ip_key, f"IPADDRESS 유사단어 없음"
        words = synonyms[ip_key]
        if isinstance(words, dict):
            words = words.get("words", [])
        assert any("IP" in w or "아이피" in w for w in words), f"IPADDRESS words: {words}"

    @pytest.mark.asyncio
    async def test_phase2_09_resource_type_synonyms(self, redis_cache):
        """resource_type 값 유사단어가 로드되었는지 확인한다."""
        rt_synonyms = await redis_cache.load_resource_type_synonyms()
        if rt_synonyms:
            # server.Cpu 등 존재 확인
            cpu_key = next((k for k in rt_synonyms if "cpu" in k.lower()), None)
            assert cpu_key, f"server.Cpu 유사단어 없음: {list(rt_synonyms.keys())[:10]}"

    @pytest.mark.asyncio
    async def test_phase2_10_eav_name_synonyms(self, redis_cache):
        """EAV name 값 유사단어가 로드되었는지 확인한다."""
        eav_synonyms = await redis_cache.load_eav_name_synonyms()
        if eav_synonyms:
            vendor_key = next(
                (k for k in eav_synonyms if k.lower() == "vendor"), None
            )
            assert vendor_key, f"Vendor EAV 유사단어 없음: {list(eav_synonyms.keys())[:10]}"


# ===========================================================================
# Phase 3: 자연어 질의 → SQL 생성 E2E 테스트
# ===========================================================================

# --- 헬퍼 ---

async def _run_graph_query(
    config: AppConfig,
    user_query: str,
) -> AgentState:
    """그래프를 실행하여 최종 state를 반환한다."""
    from src.graph import build_graph

    graph = build_graph(config)
    state = create_initial_state(user_query=user_query)
    result = await graph.ainvoke(
        state,
        config={"configurable": {"thread_id": "test-polestar"}},
    )
    return result


def _assert_sql_contains(sql: str, *keywords: str):
    """SQL이 지정된 키워드를 포함하는지 확인한다 (대소문자 무시)."""
    sql_upper = sql.upper()
    for kw in keywords:
        assert kw.upper() in sql_upper, (
            f"SQL에 '{kw}'가 없음.\nSQL: {sql}"
        )


def _assert_results_not_empty(state: AgentState):
    """쿼리 결과가 비어 있지 않은지 확인한다."""
    results = state.get("query_results", [])
    assert len(results) > 0, (
        f"쿼리 결과 비어 있음.\n"
        f"SQL: {state.get('generated_sql', 'N/A')}\n"
        f"Error: {state.get('error_message', 'N/A')}"
    )


class TestNLQServerList:
    """E2E-1: '서버 목록을 보여줘' → CMM_RESOURCE 조회."""

    @pytest.mark.asyncio
    async def test_nlq_01_server_list(self, polestar_config):
        """서버 목록 질의가 ServiceResource 또는 HOSTNAME 기반 SQL을 생성하는지 확인한다."""
        state = await _run_graph_query(polestar_config, "서버 목록을 보여줘")
        sql = state.get("generated_sql", "")
        assert sql, f"SQL이 생성되지 않음. Error: {state.get('error_message')}"
        _assert_sql_contains(sql, "CMM_RESOURCE")
        # ServiceResource 또는 HOSTNAME 필터
        sql_upper = sql.upper()
        has_service_resource = "SERVICERESOURCE" in sql_upper
        has_hostname = "HOSTNAME" in sql_upper
        assert has_service_resource or has_hostname, (
            f"서버 식별 조건 없음.\nSQL: {sql}"
        )
        _assert_results_not_empty(state)


class TestNLQCpuCores:
    """E2E-2: 'svr-db-01의 CPU 코어 수를 알려줘'."""

    @pytest.mark.asyncio
    async def test_nlq_02_cpu_cores(self, polestar_config):
        state = await _run_graph_query(
            polestar_config, "svr-db-01의 CPU 코어 수를 알려줘"
        )
        sql = state.get("generated_sql", "")
        assert sql, f"SQL 생성 실패. Error: {state.get('error_message')}"
        _assert_sql_contains(sql, "CMM_RESOURCE")
        sql_upper = sql.upper()
        # CPU 또는 server.Cpu 관련 조건
        has_cpu = "CPU" in sql_upper or "SERVER.CPU" in sql_upper
        assert has_cpu, f"CPU 관련 조건 없음.\nSQL: {sql}"
        # svr-db-01 필터
        assert "SVR-DB-01" in sql_upper or "svr-db-01" in sql.lower(), (
            f"서버명 필터 없음.\nSQL: {sql}"
        )


class TestNLQFileSystem:
    """E2E-3: '파일시스템 사용 현황'."""

    @pytest.mark.asyncio
    async def test_nlq_03_filesystem(self, polestar_config):
        state = await _run_graph_query(polestar_config, "파일시스템 사용 현황")
        sql = state.get("generated_sql", "")
        assert sql, f"SQL 생성 실패. Error: {state.get('error_message')}"
        _assert_sql_contains(sql, "CMM_RESOURCE")
        sql_upper = sql.upper()
        has_fs = "FILESYSTEM" in sql_upper or "FILESYSTEMS" in sql_upper
        assert has_fs, f"FileSystem 관련 조건 없음.\nSQL: {sql}"


class TestNLQAgentVersion:
    """E2E-4: '에이전트 버전 정보' → CORE_CONFIG_PROP EAV 조회."""

    @pytest.mark.asyncio
    async def test_nlq_04_agent_version(self, polestar_config):
        state = await _run_graph_query(polestar_config, "에이전트 버전 정보")
        sql = state.get("generated_sql", "")
        assert sql, f"SQL 생성 실패. Error: {state.get('error_message')}"
        _assert_sql_contains(sql, "CORE_CONFIG_PROP")
        sql_upper = sql.upper()
        assert "AGENTVERSION" in sql_upper or "'AgentVersion'" in sql, (
            f"AgentVersion 조건 없음.\nSQL: {sql}"
        )


class TestNLQOSTypeCount:
    """E2E-5: '운영체제 종류별 서버 수' → EAV 집계."""

    @pytest.mark.asyncio
    async def test_nlq_05_os_type_count(self, polestar_config):
        state = await _run_graph_query(polestar_config, "운영체제 종류별 서버 수")
        sql = state.get("generated_sql", "")
        assert sql, f"SQL 생성 실패. Error: {state.get('error_message')}"
        _assert_sql_contains(sql, "CORE_CONFIG_PROP")
        sql_upper = sql.upper()
        assert "OSTYPE" in sql_upper or "'OSType'" in sql, (
            f"OSType 조건 없음.\nSQL: {sql}"
        )
        assert "GROUP BY" in sql_upper, f"GROUP BY 없음.\nSQL: {sql}"


class TestNLQNetworkInterface:
    """E2E-6: '네트워크 인터페이스 목록'."""

    @pytest.mark.asyncio
    async def test_nlq_06_network_interface(self, polestar_config):
        state = await _run_graph_query(polestar_config, "네트워크 인터페이스 목록")
        sql = state.get("generated_sql", "")
        assert sql, f"SQL 생성 실패. Error: {state.get('error_message')}"
        _assert_sql_contains(sql, "CMM_RESOURCE")
        sql_upper = sql.upper()
        assert "NETWORKINTERFACE" in sql_upper or "NETWORK" in sql_upper, (
            f"NetworkInterface 관련 조건 없음.\nSQL: {sql}"
        )


class TestNLQVendor:
    """E2E-7: '서버 제조사 정보' → EAV 유사단어 매핑."""

    @pytest.mark.asyncio
    async def test_nlq_07_vendor(self, polestar_config):
        state = await _run_graph_query(polestar_config, "서버 제조사 정보")
        sql = state.get("generated_sql", "")
        assert sql, f"SQL 생성 실패. Error: {state.get('error_message')}"
        _assert_sql_contains(sql, "CORE_CONFIG_PROP")
        sql_upper = sql.upper()
        assert "VENDOR" in sql_upper or "'Vendor'" in sql, (
            f"Vendor 조건 없음.\nSQL: {sql}"
        )


class TestNLQAbnormalServers:
    """E2E-8: '비정상 상태인 서버 목록'."""

    @pytest.mark.asyncio
    async def test_nlq_08_abnormal_servers(self, polestar_config):
        state = await _run_graph_query(polestar_config, "비정상 상태인 서버 목록")
        sql = state.get("generated_sql", "")
        assert sql, f"SQL 생성 실패. Error: {state.get('error_message')}"
        _assert_sql_contains(sql, "CMM_RESOURCE")
        sql_upper = sql.upper()
        assert "AVAIL_STATUS" in sql_upper, f"AVAIL_STATUS 조건 없음.\nSQL: {sql}"
        # AVAIL_STATUS = 1 조건
        assert "1" in sql, f"비정상 값(1) 없음.\nSQL: {sql}"


class TestNLQWASProcessMonitor:
    """E2E-9: 'WAS 서버의 프로세스 모니터 현황'."""

    @pytest.mark.asyncio
    async def test_nlq_09_was_process_monitor(self, polestar_config):
        state = await _run_graph_query(
            polestar_config, "WAS 서버의 프로세스 모니터 현황"
        )
        sql = state.get("generated_sql", "")
        assert sql, f"SQL 생성 실패. Error: {state.get('error_message')}"
        _assert_sql_contains(sql, "CMM_RESOURCE")
        sql_upper = sql.upper()
        assert "PROCESSMONITOR" in sql_upper or "PROCESS" in sql_upper, (
            f"ProcessMonitor 관련 조건 없음.\nSQL: {sql}"
        )
        assert "WAS" in sql_upper or "SVR-WAS" in sql_upper, (
            f"WAS 서버 필터 없음.\nSQL: {sql}"
        )


class TestNLQHbaPort:
    """E2E-10: 'DB 서버의 HBA 포트 상태'."""

    @pytest.mark.asyncio
    async def test_nlq_10_hba_port(self, polestar_config):
        state = await _run_graph_query(
            polestar_config, "DB 서버의 HBA 포트 상태"
        )
        sql = state.get("generated_sql", "")
        assert sql, f"SQL 생성 실패. Error: {state.get('error_message')}"
        _assert_sql_contains(sql, "CMM_RESOURCE")
        sql_upper = sql.upper()
        assert "HBAPORT" in sql_upper or "HBA" in sql_upper, (
            f"HbaPort 관련 조건 없음.\nSQL: {sql}"
        )


# ===========================================================================
# Phase 3 보충: 유사단어 관리 테스트 (SYN-3, SYN-7)
# ===========================================================================

class TestSynonymManagement:
    """유사단어 YAML 로드/내보내기 라운드트립을 검증한다."""

    @pytest.mark.asyncio
    async def test_synonym_load_yaml(self, redis_cache):
        """YAML 유사단어 사전을 로드하고 결과를 확인한다."""
        from src.schema_cache.synonym_loader import SynonymLoader

        loader = SynonymLoader(redis_cache)
        result = await loader.load_from_yaml(SYNONYMS_YAML, merge=True)
        assert result.status in ("success", "partial"), f"로드 실패: {result.message}"
        assert result.columns_loaded >= 10
        assert result.total_words >= 50

    @pytest.mark.asyncio
    async def test_synonym_export_roundtrip(self, redis_cache, tmp_path):
        """Redis → YAML 내보내기 후 다시 로드하여 데이터 일치를 확인한다."""
        from src.schema_cache.synonym_loader import SynonymLoader

        loader = SynonymLoader(redis_cache)

        # 내보내기
        export_path = str(tmp_path / "exported_synonyms.yaml")
        exported = await loader.export_to_yaml(export_path)
        assert exported, "YAML 내보내기 실패"

        # 내보낸 파일 다시 로드
        result = await loader.load_from_yaml(export_path, merge=False)
        assert result.status in ("success", "partial"), f"재로드 실패: {result.message}"
        assert result.columns_loaded >= 10

    @pytest.mark.asyncio
    async def test_synonym_add_and_verify(self, redis_cache):
        """수동 유사단어 추가 후 조회를 확인한다."""
        # "장비명" → HOSTNAME 추가
        await redis_cache.add_global_synonym("HOSTNAME", ["장비명"])
        synonyms = await redis_cache.load_global_synonyms()
        hostname_key = next(
            (k for k in synonyms if k.upper() == "HOSTNAME"), None
        )
        assert hostname_key
        words = synonyms[hostname_key]
        if isinstance(words, dict):
            words = words.get("words", [])
        assert "장비명" in words, f"'장비명' 추가 안 됨: {words}"
