-- =============================================================================
-- POLESTAR 테스트 데이터 검증 쿼리
-- 파일: testdata/04_verify_data.sql
-- 설명: INSERT된 테스트 데이터의 정합성을 확인하는 SELECT 쿼리 10개
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. 전체 행 수 확인
-- 기대값: 약 780행 (서버 30대)
-- -----------------------------------------------------------------------------
SELECT COUNT(*) AS total_rows FROM POLESTAR.CMM_RESOURCE;

-- -----------------------------------------------------------------------------
-- 2. 서버별 행 수 (HOSTNAME 기준)
-- 기대값: 30건 (svr-web-01 ~ svr-db-10)
-- ServiceResource + monitor group + Cpus 등 상위 컨테이너에 HOSTNAME 설정
-- -----------------------------------------------------------------------------
SELECT HOSTNAME, COUNT(*) AS cnt
FROM POLESTAR.CMM_RESOURCE
WHERE HOSTNAME IS NOT NULL
GROUP BY HOSTNAME
ORDER BY HOSTNAME;

-- -----------------------------------------------------------------------------
-- 3. 용도별 서버 수
-- 기대값: WEB 10, WAS 10, DB 10
-- -----------------------------------------------------------------------------
SELECT
  CASE
    WHEN HOSTNAME LIKE 'svr-web%' THEN 'WEB'
    WHEN HOSTNAME LIKE 'svr-was%' THEN 'WAS'
    WHEN HOSTNAME LIKE 'svr-db%' THEN 'DB'
  END AS server_group,
  COUNT(DISTINCT HOSTNAME) AS server_count
FROM POLESTAR.CMM_RESOURCE
WHERE HOSTNAME IS NOT NULL
GROUP BY
  CASE
    WHEN HOSTNAME LIKE 'svr-web%' THEN 'WEB'
    WHEN HOSTNAME LIKE 'svr-was%' THEN 'WAS'
    WHEN HOSTNAME LIKE 'svr-db%' THEN 'DB'
  END;

-- -----------------------------------------------------------------------------
-- 4. RESOURCE_TYPE별 분포
-- 기대값: 15종 이상
-- -----------------------------------------------------------------------------
SELECT RESOURCE_TYPE, COUNT(*) AS cnt
FROM POLESTAR.CMM_RESOURCE
GROUP BY RESOURCE_TYPE
ORDER BY cnt DESC;

-- -----------------------------------------------------------------------------
-- 5. AVAIL_STATUS 분포 (비정상 리소스 확인)
-- 기대값: 0(정상) 약 92~95%, 1(비정상) 약 5~8%
-- -----------------------------------------------------------------------------
SELECT AVAIL_STATUS, COUNT(*) AS cnt,
       DECIMAL(CAST(COUNT(*) AS DECIMAL(10,2)) / (SELECT COUNT(*) FROM POLESTAR.CMM_RESOURCE) * 100, 5, 2) AS pct
FROM POLESTAR.CMM_RESOURCE
GROUP BY AVAIL_STATUS;

-- -----------------------------------------------------------------------------
-- 6. CORE_CONFIG_PROP 행 수
-- 기대값: 360 (30대 x 12설정)
-- -----------------------------------------------------------------------------
SELECT COUNT(*) AS total_rows FROM POLESTAR.CORE_CONFIG_PROP;

-- -----------------------------------------------------------------------------
-- 7. CONFIGURATION_ID별 설정 항목 수
-- 기대값: 301~330 각각 12건
-- -----------------------------------------------------------------------------
SELECT CONFIGURATION_ID, COUNT(*) AS cnt
FROM POLESTAR.CORE_CONFIG_PROP
GROUP BY CONFIGURATION_ID
ORDER BY CONFIGURATION_ID;

-- -----------------------------------------------------------------------------
-- 8. 서버별 설정 값 비교 (웹서버 1대 - svr-web-01, CONFIGURATION_ID=301)
-- -----------------------------------------------------------------------------
SELECT NAME, STRINGVALUE_SHORT
FROM POLESTAR.CORE_CONFIG_PROP
WHERE CONFIGURATION_ID = 301
ORDER BY NAME;

-- -----------------------------------------------------------------------------
-- 9. 제조사(Vendor)별 서버 수
-- 기대값: VMware, Inc. / HPE / Dell Inc. 3종
-- -----------------------------------------------------------------------------
SELECT STRINGVALUE_SHORT AS vendor, COUNT(*) AS cnt
FROM POLESTAR.CORE_CONFIG_PROP
WHERE NAME = 'Vendor'
GROUP BY STRINGVALUE_SHORT;

-- -----------------------------------------------------------------------------
-- 10. OS 버전 분포
-- 기대값: CentOS 7/8/9 커널 2~3종
-- -----------------------------------------------------------------------------
SELECT STRINGVALUE_SHORT AS os_version, COUNT(*) AS cnt
FROM POLESTAR.CORE_CONFIG_PROP
WHERE NAME = 'OSVerson'
GROUP BY STRINGVALUE_SHORT;
