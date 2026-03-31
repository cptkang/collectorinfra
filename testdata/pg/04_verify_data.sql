-- =============================================================================
-- POLESTAR 테스트 데이터 검증 쿼리 (PostgreSQL 버전)
-- 파일: testdata/pg/04_verify_data.sql
-- 설명: INSERT된 테스트 데이터의 정합성을 확인하는 SELECT 쿼리 10개
-- DB 엔진: PostgreSQL 14+
-- 원본: testdata/04_verify_data.sql (IBM DB2)
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. 전체 행 수 확인
-- 기대값: 약 780행 (서버 30대)
-- -----------------------------------------------------------------------------
SELECT COUNT(*) AS total_rows FROM polestar.cmm_resource;

-- -----------------------------------------------------------------------------
-- 2. 서버별 행 수 (HOSTNAME 기준)
-- 기대값: 30건 (svr-web-01 ~ svr-db-10)
-- ServiceResource + monitor group + Cpus 등 상위 컨테이너에 HOSTNAME 설정
-- -----------------------------------------------------------------------------
SELECT hostname, COUNT(*) AS cnt
FROM polestar.cmm_resource
WHERE hostname IS NOT NULL
GROUP BY hostname
ORDER BY hostname;

-- -----------------------------------------------------------------------------
-- 3. 용도별 서버 수
-- 기대값: WEB 10, WAS 10, DB 10
-- -----------------------------------------------------------------------------
SELECT
  CASE
    WHEN hostname LIKE 'svr-web%' THEN 'WEB'
    WHEN hostname LIKE 'svr-was%' THEN 'WAS'
    WHEN hostname LIKE 'svr-db%' THEN 'DB'
  END AS server_group,
  COUNT(DISTINCT hostname) AS server_count
FROM polestar.cmm_resource
WHERE hostname IS NOT NULL
GROUP BY
  CASE
    WHEN hostname LIKE 'svr-web%' THEN 'WEB'
    WHEN hostname LIKE 'svr-was%' THEN 'WAS'
    WHEN hostname LIKE 'svr-db%' THEN 'DB'
  END;

-- -----------------------------------------------------------------------------
-- 4. RESOURCE_TYPE별 분포
-- 기대값: 15종 이상
-- -----------------------------------------------------------------------------
SELECT resource_type, COUNT(*) AS cnt
FROM polestar.cmm_resource
GROUP BY resource_type
ORDER BY cnt DESC;

-- -----------------------------------------------------------------------------
-- 5. AVAIL_STATUS 분포 (비정상 리소스 확인)
-- 기대값: 0(정상) 약 92~95%, 1(비정상) 약 5~8%
-- -----------------------------------------------------------------------------
SELECT avail_status, COUNT(*) AS cnt,
       ROUND(COUNT(*)::numeric / (SELECT COUNT(*) FROM polestar.cmm_resource) * 100, 2) AS pct
FROM polestar.cmm_resource
GROUP BY avail_status;

-- -----------------------------------------------------------------------------
-- 6. CORE_CONFIG_PROP 행 수
-- 기대값: 360 (30대 x 12설정)
-- -----------------------------------------------------------------------------
SELECT COUNT(*) AS total_rows FROM polestar.core_config_prop;

-- -----------------------------------------------------------------------------
-- 7. CONFIGURATION_ID별 설정 항목 수
-- 기대값: 301~330 각각 12건
-- -----------------------------------------------------------------------------
SELECT configuration_id, COUNT(*) AS cnt
FROM polestar.core_config_prop
GROUP BY configuration_id
ORDER BY configuration_id;

-- -----------------------------------------------------------------------------
-- 8. 서버별 설정 값 비교 (웹서버 1대 - svr-web-01, CONFIGURATION_ID=301)
-- -----------------------------------------------------------------------------
SELECT name, stringvalue_short
FROM polestar.core_config_prop
WHERE configuration_id = 301
ORDER BY name;

-- -----------------------------------------------------------------------------
-- 9. 제조사(Vendor)별 서버 수
-- 기대값: VMware, Inc. / HPE / Dell Inc. 3종
-- -----------------------------------------------------------------------------
SELECT stringvalue_short AS vendor, COUNT(*) AS cnt
FROM polestar.core_config_prop
WHERE name = 'Vendor'
GROUP BY stringvalue_short;

-- -----------------------------------------------------------------------------
-- 10. OS 버전 분포
-- 기대값: CentOS 7/8/9 커널 2~3종
-- -----------------------------------------------------------------------------
SELECT stringvalue_short AS os_version, COUNT(*) AS cnt
FROM polestar.core_config_prop
WHERE name = 'OSVerson'
GROUP BY stringvalue_short;
