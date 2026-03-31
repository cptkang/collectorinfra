-- ============================================================================
-- Polestar EAV 샘플 데이터 수집 SQL
--
-- schema_analyzer가 Polestar DB 감지 시 실행하는 쿼리.
-- EAV 구조 이해를 위한 샘플 데이터와 분포 정보를 수집한다.
-- 파일: src/nodes/schema_analyzer.py (함수: _collect_polestar_samples)
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. CORE_CONFIG_PROP 속성명별 샘플 데이터
--
-- 용도: EAV 테이블의 속성명(NAME)과 값(STRINGVALUE_SHORT)의 분포를 파악.
--       known_attributes 목록 확인 및 LLM 프롬프트에 샘플 데이터로 삽입.
--       IS_LOB = 0으로 대용량 텍스트 값을 제외.
-- 파일: src/nodes/schema_analyzer.py:171-177
-- 참고: DB2에서는 FETCH FIRST N ROWS ONLY, PostgreSQL에서는 LIMIT N 사용
-- ---------------------------------------------------------------------------

-- PostgreSQL 버전
SELECT NAME, STRINGVALUE_SHORT, CONFIGURATION_ID
FROM CORE_CONFIG_PROP
WHERE IS_LOB = 0
GROUP BY NAME, STRINGVALUE_SHORT, CONFIGURATION_ID
LIMIT 30;

-- DB2 버전
SELECT NAME, STRINGVALUE_SHORT, CONFIGURATION_ID
FROM CORE_CONFIG_PROP
WHERE IS_LOB = 0
GROUP BY NAME, STRINGVALUE_SHORT, CONFIGURATION_ID
FETCH FIRST 30 ROWS ONLY;


-- ---------------------------------------------------------------------------
-- 2. RESOURCE_TYPE별 리소스 분포
--
-- 용도: CMM_RESOURCE 테이블의 계층 구조에서 어떤 리소스 타입이 존재하는지 파악.
--       DTIME IS NULL로 삭제되지 않은 리소스만 집계.
--       LLM이 올바른 RESOURCE_TYPE 값을 사용하도록 분포 정보 제공.
-- 파일: src/nodes/schema_analyzer.py:186-193
-- ---------------------------------------------------------------------------

-- PostgreSQL 버전
SELECT RESOURCE_TYPE, COUNT(*) AS CNT
FROM CMM_RESOURCE
WHERE DTIME IS NULL
GROUP BY RESOURCE_TYPE
ORDER BY CNT DESC
LIMIT 20;

-- DB2 버전
SELECT RESOURCE_TYPE, COUNT(*) AS CNT
FROM CMM_RESOURCE
WHERE DTIME IS NULL
GROUP BY RESOURCE_TYPE
ORDER BY CNT DESC
FETCH FIRST 20 ROWS ONLY;


-- ---------------------------------------------------------------------------
-- 3. 계층 구조 샘플 (서버 1건)
--
-- 용도: CMM_RESOURCE의 계층형 self-join 구조를 LLM이 이해하도록
--       HOSTNAME이 있는 서버 리소스 1건의 기본 정보를 수집.
--       PARENT_RESOURCE_ID를 통한 상위-하위 관계 파악용.
-- 파일: src/nodes/schema_analyzer.py:202-207
-- ---------------------------------------------------------------------------

-- PostgreSQL 버전
SELECT r.ID, r.NAME, r.RESOURCE_TYPE, r.PARENT_RESOURCE_ID, r.HOSTNAME
FROM CMM_RESOURCE r
WHERE r.HOSTNAME IS NOT NULL
LIMIT 1;

-- DB2 버전
SELECT r.ID, r.NAME, r.RESOURCE_TYPE, r.PARENT_RESOURCE_ID, r.HOSTNAME
FROM CMM_RESOURCE r
WHERE r.HOSTNAME IS NOT NULL
FETCH FIRST 1 ROWS ONLY;
