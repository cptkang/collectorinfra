-- ============================================================================
-- 테스트 데이터 검증 SQL
--
-- testdata/pg/ Docker 환경에서 데이터 정합성을 확인하는 쿼리 모음.
-- 파일: testdata/pg/04_verify_data.sql
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. CMM_RESOURCE 전체 행 수
-- 용도: 테스트 데이터 INSERT 정상 여부 확인
-- 기대값: 서버 30대 + 하위 리소스 포함 수백 건
-- ---------------------------------------------------------------------------
SELECT COUNT(*) AS total_rows FROM polestar.cmm_resource;


-- ---------------------------------------------------------------------------
-- 2. 서버별 리소스 행 수
-- 용도: 각 서버의 하위 리소스가 올바르게 생성되었는지 확인
-- ---------------------------------------------------------------------------
SELECT hostname, COUNT(*) AS cnt
FROM polestar.cmm_resource
WHERE hostname IS NOT NULL
GROUP BY hostname
ORDER BY hostname;


-- ---------------------------------------------------------------------------
-- 3. 용도별 서버 수 (WEB/WAS/DB 분류)
-- 용도: 서버 호스트명 명명 규칙(svr-web-*, svr-was-*, svr-db-*)에 따른 분류 확인
-- ---------------------------------------------------------------------------
SELECT
    CASE
        WHEN hostname LIKE 'svr-web%' THEN 'WEB'
        WHEN hostname LIKE 'svr-was%' THEN 'WAS'
        WHEN hostname LIKE 'svr-db%' THEN 'DB'
        ELSE 'OTHER'
    END AS server_group,
    COUNT(DISTINCT hostname) AS server_count
FROM polestar.cmm_resource
WHERE hostname IS NOT NULL
GROUP BY
    CASE
        WHEN hostname LIKE 'svr-web%' THEN 'WEB'
        WHEN hostname LIKE 'svr-was%' THEN 'WAS'
        WHEN hostname LIKE 'svr-db%' THEN 'DB'
        ELSE 'OTHER'
    END
ORDER BY server_group;


-- ---------------------------------------------------------------------------
-- 4. RESOURCE_TYPE별 분포
-- 용도: 계층 구조의 리소스 타입 분포 확인
-- 기대 타입: Server, server.Cpus, server.Cpu, server.Memory,
--           server.FileSystems, server.FileSystem,
--           server.NetworkInterfaces, server.NetworkInterface 등
-- ---------------------------------------------------------------------------
SELECT resource_type, COUNT(*) AS cnt
FROM polestar.cmm_resource
GROUP BY resource_type
ORDER BY cnt DESC;


-- ---------------------------------------------------------------------------
-- 5. AVAIL_STATUS 분포 (비정상 리소스 확인)
-- 용도: 리소스 가용 상태 분포 확인. 비정상(0이 아닌 값) 리소스 비율 파악.
-- ---------------------------------------------------------------------------
SELECT
    avail_status,
    COUNT(*) AS cnt,
    ROUND(COUNT(*)::numeric / (SELECT COUNT(*) FROM polestar.cmm_resource) * 100, 2) AS pct
FROM polestar.cmm_resource
GROUP BY avail_status
ORDER BY avail_status;


-- ---------------------------------------------------------------------------
-- 6. CORE_CONFIG_PROP 전체 행 수
-- 용도: EAV 설정 데이터 INSERT 정상 여부 확인
-- 기대값: 서버 30대 x 속성 12개 = 360건
-- ---------------------------------------------------------------------------
SELECT COUNT(*) AS total_rows FROM polestar.core_config_prop;


-- ---------------------------------------------------------------------------
-- 7. CONFIGURATION_ID별 설정 항목 수
-- 용도: 각 서버(CONFIGURATION_ID)에 올바른 수의 설정 속성이 있는지 확인
-- 기대값: 각 CONFIGURATION_ID당 12건 (known_attributes 12개)
-- ---------------------------------------------------------------------------
SELECT configuration_id, COUNT(*) AS cnt
FROM polestar.core_config_prop
GROUP BY configuration_id
ORDER BY configuration_id;


-- ---------------------------------------------------------------------------
-- 8. 특정 서버의 설정 값 전체 조회
-- 용도: 개별 서버의 EAV 설정 값이 올바르게 저장되었는지 확인
-- 파라미터: configuration_id = 301 (첫 번째 서버)
-- ---------------------------------------------------------------------------
SELECT name, stringvalue_short
FROM polestar.core_config_prop
WHERE configuration_id = 301
ORDER BY name;


-- ---------------------------------------------------------------------------
-- 9. 제조사(Vendor)별 서버 수
-- 용도: EAV 속성 'Vendor'의 값 분포 확인
-- ---------------------------------------------------------------------------
SELECT stringvalue_short AS vendor, COUNT(*) AS cnt
FROM polestar.core_config_prop
WHERE name = 'Vendor'
GROUP BY stringvalue_short
ORDER BY cnt DESC;


-- ---------------------------------------------------------------------------
-- 10. OS 버전 분포
-- 용도: EAV 속성 'OSVerson'(원본 Polestar 제품의 오탈자, 실제 DB 값)의 분포 확인
-- 주의: 'OSVersion'이 아니라 'OSVerson'이 실제 DB에 저장된 값임
-- ---------------------------------------------------------------------------
SELECT stringvalue_short AS os_version, COUNT(*) AS cnt
FROM polestar.core_config_prop
WHERE name = 'OSVerson'
GROUP BY stringvalue_short
ORDER BY cnt DESC;
