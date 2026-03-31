-- ============================================================================
-- Polestar EAV 쿼리 패턴
--
-- Polestar DB의 계층형 리소스(CMM_RESOURCE)와 EAV 설정(CORE_CONFIG_PROP)
-- 테이블에 대한 쿼리 패턴 모음.
-- LLM이 SQL을 생성할 때 참고하는 예시로 사용된다.
-- 파일: src/prompts/polestar_patterns.py
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. 서버 목록 조회 (기본 정보)
--
-- 용도: 전체 서버 목록과 기본 정보(호스트명, IP, 상태, 설명) 조회.
--       HOSTNAME IS NOT NULL로 서버 레벨 리소스만 필터링.
--       DTIME IS NULL로 삭제되지 않은 리소스만 조회.
-- 파일: src/prompts/polestar_patterns.py:10-15
-- ---------------------------------------------------------------------------
SELECT r.ID, r.HOSTNAME, r.IPADDRESS, r.AVAIL_STATUS, r.DESCRIPTION
FROM CMM_RESOURCE r
WHERE r.HOSTNAME IS NOT NULL
  AND r.DTIME IS NULL;


-- ---------------------------------------------------------------------------
-- 2. 서버 상세 정보 (EAV 피벗 쿼리)
--
-- 용도: EAV(Entity-Attribute-Value) 구조의 CORE_CONFIG_PROP 테이블에서
--       서버별 설정 값(OS, 벤더, 모델 등)을 컬럼으로 피벗하여 조회.
--       CASE WHEN + MAX + GROUP BY 패턴으로 행→컬럼 변환.
--
-- EAV 구조:
--   CORE_CONFIG_PROP: (CONFIGURATION_ID, NAME, STRINGVALUE_SHORT)
--   예: (301, 'OSType', 'Linux'), (301, 'Vendor', 'Dell')
--
-- JOIN 조건 (hostname 브릿지):
--   CMM_RESOURCE.RESOURCE_CONF_ID는 JOIN 키로 사용할 수 없다.
--   두 테이블 간에 FK 제약이 없으므로, hostname 값 기반 브릿지 조인을 사용한다:
--   1단계: CORE_CONFIG_PROP에서 NAME='Hostname'인 행의 STRINGVALUE_SHORT = CMM_RESOURCE.HOSTNAME
--   2단계: 동일 CONFIGURATION_ID를 공유하는 다른 EAV 속성을 조인
--
-- 파일: src/prompts/polestar_patterns.py:19-32
-- ---------------------------------------------------------------------------
SELECT
    r.ID, r.HOSTNAME, r.IPADDRESS,
    MAX(CASE WHEN p_attr.NAME = 'OSType' THEN p_attr.STRINGVALUE_SHORT END) AS OS_TYPE,
    MAX(CASE WHEN p_attr.NAME = 'OSVerson' THEN p_attr.STRINGVALUE_SHORT END) AS OS_VERSION,
    MAX(CASE WHEN p_attr.NAME = 'Model' THEN p_attr.STRINGVALUE_SHORT END) AS MODEL,
    MAX(CASE WHEN p_attr.NAME = 'Vendor' THEN p_attr.STRINGVALUE_SHORT END) AS VENDOR,
    MAX(CASE WHEN p_attr.NAME = 'SerialNumber' THEN p_attr.STRINGVALUE_SHORT END) AS SERIAL_NUMBER,
    MAX(CASE WHEN p_attr.NAME = 'IPaddress' THEN p_attr.STRINGVALUE_SHORT END) AS CONFIG_IP
FROM CMM_RESOURCE r
-- 1단계: hostname 값으로 CORE_CONFIG_PROP의 Hostname 속성 행을 찾는다
LEFT JOIN CORE_CONFIG_PROP p_host
    ON p_host.NAME = 'Hostname' AND p_host.STRINGVALUE_SHORT = r.HOSTNAME
-- 2단계: 동일 CONFIGURATION_ID를 공유하는 다른 EAV 속성을 조인한다
LEFT JOIN CORE_CONFIG_PROP p_attr
    ON p_attr.CONFIGURATION_ID = p_host.CONFIGURATION_ID AND p_attr.NAME != 'Hostname'
WHERE r.HOSTNAME IS NOT NULL AND r.DTIME IS NULL
GROUP BY r.ID, r.HOSTNAME, r.IPADDRESS;


-- ---------------------------------------------------------------------------
-- 3. 하위 리소스 조회 (계층 탐색)
--
-- 용도: 특정 서버(hostname)의 하위 리소스를 resource_type 별로 조회.
--       CMM_RESOURCE의 self-join (PARENT_RESOURCE_ID → ID)을 사용.
--
-- 계층 구조 예시:
--   Server (hostname='svr-web-01')
--     ├─ server.Cpus (CPU 관리)
--     │    └─ server.Cpu (개별 코어)
--     ├─ server.Memory (물리 메모리)
--     ├─ server.FileSystems (파일시스템 관리)
--     │    └─ server.FileSystem (개별 마운트포인트)
--     └─ server.NetworkInterfaces (NIC 관리)
--          └─ server.NetworkInterface (개별 인터페이스)
--
-- 파일: src/prompts/polestar_patterns.py:36-42
-- 파라미터: :hostname = 서버 호스트명, :resource_type = 리소스 타입
-- ---------------------------------------------------------------------------
SELECT child.ID, child.NAME, child.RESOURCE_TYPE, child.AVAIL_STATUS, child.DESCRIPTION
FROM CMM_RESOURCE child
JOIN CMM_RESOURCE parent ON child.PARENT_RESOURCE_ID = parent.ID
WHERE parent.HOSTNAME = :hostname
  AND child.RESOURCE_TYPE = :resource_type;


-- ---------------------------------------------------------------------------
-- 4. CPU 코어 수 집계 (3단계 계층 JOIN)
--
-- 용도: 서버별 CPU 코어 수를 집계한다.
--       3단계 계층 구조를 JOIN으로 탐색:
--       Server → server.Cpus(컨테이너) → server.Cpu(개별 코어)
--
-- 파일: src/prompts/polestar_patterns.py:46-55
-- ---------------------------------------------------------------------------
SELECT server.HOSTNAME, COUNT(cpu.ID) AS CPU_CORE_COUNT
FROM CMM_RESOURCE server
JOIN CMM_RESOURCE cpus ON cpus.PARENT_RESOURCE_ID = server.ID
    AND cpus.RESOURCE_TYPE = 'server.Cpus'
JOIN CMM_RESOURCE cpu ON cpu.PARENT_RESOURCE_ID = cpus.ID
    AND cpu.RESOURCE_TYPE = 'server.Cpu'
WHERE server.HOSTNAME IS NOT NULL
GROUP BY server.HOSTNAME;


-- ---------------------------------------------------------------------------
-- 5. 파일시스템 목록 조회
--
-- 용도: 서버별 파일시스템 마운트포인트 목록 조회.
--       Server → server.FileSystems(컨테이너) → 개별 파일시스템
--
-- 파일: src/prompts/polestar_patterns.py:59-66
-- ---------------------------------------------------------------------------
SELECT server.HOSTNAME, fs.NAME AS MOUNT_POINT, fs.AVAIL_STATUS
FROM CMM_RESOURCE fs
JOIN CMM_RESOURCE fsc ON fs.PARENT_RESOURCE_ID = fsc.ID
    AND fsc.RESOURCE_TYPE = 'server.FileSystems'
JOIN CMM_RESOURCE server ON fsc.PARENT_RESOURCE_ID = server.ID
WHERE server.HOSTNAME IS NOT NULL;


-- ---------------------------------------------------------------------------
-- 6. 네트워크 인터페이스 목록 조회
--
-- 용도: 서버별 네트워크 인터페이스 목록 조회.
--       Server → server.NetworkInterfaces(컨테이너) → 개별 NIC
--
-- 파일: src/prompts/polestar_patterns.py:71-77
-- ---------------------------------------------------------------------------
SELECT server.HOSTNAME, ni.NAME AS INTERFACE_NAME, ni.AVAIL_STATUS
FROM CMM_RESOURCE ni
JOIN CMM_RESOURCE nic ON ni.PARENT_RESOURCE_ID = nic.ID
    AND nic.RESOURCE_TYPE = 'server.NetworkInterfaces'
JOIN CMM_RESOURCE server ON nic.PARENT_RESOURCE_ID = server.ID
WHERE server.HOSTNAME IS NOT NULL;
