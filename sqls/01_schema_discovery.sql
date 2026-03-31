-- ============================================================================
-- 스키마 탐색 SQL
--
-- DB 스키마 정보를 수집하는 쿼리 모음.
-- schema_analyzer 및 db/client.py에서 사용된다.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Fingerprint 생성 (스키마 변경 감지용)
--
-- 용도: information_schema에서 테이블명 + 컬럼 수를 조회하여 SHA-256 해시를 생성.
--       캐시된 fingerprint와 비교하여 스키마 변경 여부를 판단한다.
-- 파일: src/schema_cache/fingerprint.py:17-25
-- 호출: schema_analyzer._fetch_fingerprint(), multi_db_executor._analyze_schema()
-- ---------------------------------------------------------------------------
SELECT
    table_name,
    COUNT(*) AS column_count
FROM information_schema.columns
WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
GROUP BY table_name
ORDER BY table_name;


-- ---------------------------------------------------------------------------
-- 2. 테이블 목록 조회
--
-- 용도: PostgreSQL DB의 모든 사용자 테이블 목록을 조회한다.
--       시스템 스키마(information_schema, pg_catalog, pg_toast)는 제외.
-- 파일: src/db/client.py:97-103
-- 호출: PostgresClient.search_objects()
-- ---------------------------------------------------------------------------
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_schema NOT IN ('information_schema', 'pg_catalog', 'pg_toast')
  AND table_type = 'BASE TABLE'
ORDER BY table_schema, table_name;


-- ---------------------------------------------------------------------------
-- 3. 컬럼 정보 조회
--
-- 용도: 특정 테이블의 컬럼 상세 정보를 조회한다.
--       컬럼명, 데이터타입, NULL 가능 여부, 기본값을 포함.
-- 파일: src/db/client.py:125-130
-- 호출: PostgresClient.get_table_schema()
-- 파라미터: $1 = table_schema, $2 = table_name
-- ---------------------------------------------------------------------------
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = $1 AND table_name = $2
ORDER BY ordinal_position;


-- ---------------------------------------------------------------------------
-- 4. Primary Key 조회
--
-- 용도: 특정 테이블의 Primary Key 컬럼을 식별한다.
-- 파일: src/db/client.py:131-140
-- 호출: PostgresClient.get_table_schema()
-- 파라미터: $1 = table_schema, $2 = table_name
-- ---------------------------------------------------------------------------
SELECT kcu.column_name
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON tc.constraint_name = kcu.constraint_name
  AND tc.table_schema = kcu.table_schema
WHERE tc.table_schema = $1
  AND tc.table_name = $2
  AND tc.constraint_type = 'PRIMARY KEY';


-- ---------------------------------------------------------------------------
-- 5. Foreign Key 관계 조회 (PostgresClient)
--
-- 용도: DB 전체의 Foreign Key 관계를 조회한다.
--       테이블 간 참조 관계를 파악하여 JOIN 힌트에 활용.
-- 파일: src/db/client.py:260-275
-- 호출: PostgresClient._get_foreign_keys()
-- ---------------------------------------------------------------------------
SELECT
    tc.table_name AS from_table,
    kcu.column_name AS from_column,
    ccu.table_name AS to_table,
    ccu.column_name AS to_column
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
    ON tc.constraint_name = kcu.constraint_name
    AND tc.table_schema = kcu.table_schema
JOIN information_schema.constraint_column_usage ccu
    ON tc.constraint_name = ccu.constraint_name
    AND tc.table_schema = ccu.table_schema
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND tc.table_schema NOT IN ('information_schema', 'pg_catalog', 'pg_toast');


-- ---------------------------------------------------------------------------
-- 6. Foreign Key 관계 조회 (DBHub MCP 클라이언트)
--
-- 용도: DBHub MCP 서버를 통한 FK 관계 조회. #5와 동일 목적이나 table_schema 필터 없음.
-- 파일: src/dbhub/client.py:348-360
-- 호출: DBHubClient._get_foreign_keys()
-- ---------------------------------------------------------------------------
SELECT
    tc.table_name AS from_table,
    kcu.column_name AS from_column,
    ccu.table_name AS to_table,
    ccu.column_name AS to_column
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
    ON tc.constraint_name = kcu.constraint_name
JOIN information_schema.constraint_column_usage ccu
    ON tc.constraint_name = ccu.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY';


-- ---------------------------------------------------------------------------
-- 7. 샘플 데이터 조회
--
-- 용도: 테이블의 샘플 데이터를 안전하게 조회한다.
--       테이블명은 정규식 검증(_VALID_TABLE_NAME)으로 SQL 인젝션 방어.
-- 파일: src/db/client.py:193-195
-- 호출: PostgresClient.get_sample_data()
-- 참고: {table_name}과 {limit}은 파이썬 코드에서 안전하게 치환됨
-- ---------------------------------------------------------------------------
-- SELECT * FROM {table_name} LIMIT {limit};


-- ---------------------------------------------------------------------------
-- 8. Health Check
--
-- 용도: DB 연결 상태를 확인하는 최소 쿼리.
-- 파일: src/db/client.py:81
-- 호출: PostgresClient.health_check()
-- ---------------------------------------------------------------------------
SELECT 1;
