-- ============================================================================
-- Plan 67 E1: gp 골드셋 실운영형 서버명 4대 보강
-- ============================================================================
-- 배경: 골드셋(testdata/text2sql_gold/gp.yaml)이 실운영형 서버명
--       (cob0-bnndbp01·sbhdbo53·cocm-xgzapp09·cocm-ngcapo91)으로 갱신되었으나
--       픽스처(07)는 구 명칭(SV-WEB-001 등)만 보유 → gp-007/008/011/014 골드가
--       0행이 되어 EX 채점 불가(E1 2026-08-04 실측, Plan 67 v16).
--
-- ID 격리 범위 (01~08 파일 및 실 DB 실측상 미사용 확인):
--   cmm_resource     : 9680001 ~ 9689999 (conf id 동일 블록 내 968xxxx)
--   core_config_prop : 200200001 ~ 200299999
--
-- 관행 준수:
--   · EAV propertydefinition_id=454 통일(07과 동일)
--   · OSParameter는 LOB(stringvalue) 사용, stringvalue_short NULL
--     (Known Mistakes 2026-06-10 — gp-011 골드의 COALESCE와 정합)
--   · population pairing 불변식(08): server.Server 앵커마다 platform.server
--     쌍둥이 동반 — 어느 조회 규약이든 같은 서버 집합
--   · cocm-ngcapo91 사양(LOGICALCORE 16·TotalSize 65536)은 DB-ORA-023·
--     cocm-hdkapp01과 동일 문자열 — gp-014 "유사 사양 탐색"이 2행 반환
--
-- 멱등성: 상단 DELETE 블록이 위 범위를 정리한 뒤 INSERT하므로 재실행 안전.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 0. 멱등 정리 (본 파일 범위만)
-- ----------------------------------------------------------------------------
DELETE FROM polestar.core_config_prop WHERE id BETWEEN 200200001 AND 200299999;
DELETE FROM polestar.cmm_resource     WHERE id BETWEEN 9680001 AND 9689999;

-- ----------------------------------------------------------------------------
-- 1. 서버 4대 (server.Server 앵커)
--    gp-007: cob0-bnndbp01 가용성·IP 지목 조회
--    gp-008: sbhdbo53 hostname 조회 + Cpus 자식 조인
--    gp-011: cocm-xgzapp09 OSParameter(LOB) 조회
--    gp-014: cocm-ngcapo91 동일 사양 탐색 앵커
-- ----------------------------------------------------------------------------
INSERT INTO polestar.cmm_resource
  (dtype, id, acl_id, importance_id, inheritstatus, invisible, name, hostname,
   ipaddress, avail_status, resource_key, resource_type, resource_conf_id,
   platform_resource_id, parent_resource_id, ctime, dtime)
VALUES
  ('PlatformResource', 9680001, 112, 1, 1, 0, 'cob0-bnndbp01', 'cob0-bnndbp01', '10.68.0.1', 0, 'P67_cob0-bnndbp01', 'server.Server', 9681001, NULL, NULL, 1767200000000, NULL),
  ('PlatformResource', 9680002, 112, 1, 1, 0, 'sbhdbo53',      'sbhdbo53',      '10.68.0.2', 0, 'P67_sbhdbo53',      'server.Server', 9681002, NULL, NULL, 1767200000000, NULL),
  ('PlatformResource', 9680003, 112, 1, 1, 0, 'cocm-xgzapp09', 'cocm-xgzapp09', '10.68.0.3', 0, 'P67_cocm-xgzapp09', 'server.Server', 9681003, NULL, NULL, 1767200000000, NULL),
  ('PlatformResource', 9680004, 112, 1, 1, 0, 'cocm-ngcapo91', 'cocm-ngcapo91', '10.68.0.4', 0, 'P67_cocm-ngcapo91', 'server.Server', 9681004, NULL, NULL, 1767200000000, NULL);

-- ----------------------------------------------------------------------------
-- 2. 자식 리소스 (server.Cpus / server.Memory — parent/platform → 서버)
-- ----------------------------------------------------------------------------
INSERT INTO polestar.cmm_resource
  (dtype, id, acl_id, importance_id, inheritstatus, invisible, name, hostname,
   ipaddress, avail_status, resource_key, resource_type, resource_conf_id,
   platform_resource_id, parent_resource_id, ctime, dtime)
VALUES
  ('PlatformResource', 9682001, 112, 1, 1, 0, 'Cpus',   NULL, NULL, 0, 'P67_CPUS_9680001', 'server.Cpus',   9682501, 9680001, 9680001, 1767200000000, NULL),
  ('PlatformResource', 9682002, 112, 1, 1, 0, 'Cpus',   NULL, NULL, 0, 'P67_CPUS_9680002', 'server.Cpus',   9682502, 9680002, 9680002, 1767200000000, NULL),
  ('PlatformResource', 9682003, 112, 1, 1, 0, 'Cpus',   NULL, NULL, 0, 'P67_CPUS_9680003', 'server.Cpus',   9682503, 9680003, 9680003, 1767200000000, NULL),
  ('PlatformResource', 9682004, 112, 1, 1, 0, 'Cpus',   NULL, NULL, 0, 'P67_CPUS_9680004', 'server.Cpus',   9682504, 9680004, 9680004, 1767200000000, NULL),
  ('PlatformResource', 9683001, 112, 1, 1, 0, 'Memory', NULL, NULL, 0, 'P67_MEM_9680001',  'server.Memory', 9683501, 9680001, 9680001, 1767200000000, NULL),
  ('PlatformResource', 9683002, 112, 1, 1, 0, 'Memory', NULL, NULL, 0, 'P67_MEM_9680002',  'server.Memory', 9683502, 9680002, 9680002, 1767200000000, NULL),
  ('PlatformResource', 9683003, 112, 1, 1, 0, 'Memory', NULL, NULL, 0, 'P67_MEM_9680003',  'server.Memory', 9683503, 9680003, 9680003, 1767200000000, NULL),
  ('PlatformResource', 9683004, 112, 1, 1, 0, 'Memory', NULL, NULL, 0, 'P67_MEM_9680004',  'server.Memory', 9683504, 9680004, 9680004, 1767200000000, NULL);

-- ----------------------------------------------------------------------------
-- 3. platform.server 쌍둥이 (08 population pairing 불변식 유지)
-- ----------------------------------------------------------------------------
INSERT INTO polestar.cmm_resource
  (dtype, id, acl_id, avail_status, ctime, dtime, haschildren, hostname,
   importance_id, inheritstatus, invisible, ipaddress, name,
   parent_resource_id, platform_resource_id, resource_key, resource_type,
   resource_conf_id)
VALUES
  ('ServiceResource', 9684001, 112, 0, 1767200000000, NULL, 1, 'cob0-bnndbp01', 1, 0, 0, '10.68.0.1', 'cob0-bnndbp01', 9680001, 9680001, 'P67PAIR_PF_9680001', 'platform.server', 9681001),
  ('ServiceResource', 9684002, 112, 0, 1767200000000, NULL, 1, 'sbhdbo53',      1, 0, 0, '10.68.0.2', 'sbhdbo53',      9680002, 9680002, 'P67PAIR_PF_9680002', 'platform.server', 9681002),
  ('ServiceResource', 9684003, 112, 0, 1767200000000, NULL, 1, 'cocm-xgzapp09', 1, 0, 0, '10.68.0.3', 'cocm-xgzapp09', 9680003, 9680003, 'P67PAIR_PF_9680003', 'platform.server', 9681003),
  ('ServiceResource', 9684004, 112, 0, 1767200000000, NULL, 1, 'cocm-ngcapo91', 1, 0, 0, '10.68.0.4', 'cocm-ngcapo91', 9680004, 9680004, 'P67PAIR_PF_9680004', 'platform.server', 9681004);

-- ----------------------------------------------------------------------------
-- 4. EAV (core_config_prop)
--    서버 conf: Hostname/OSType/OSVerson/Vendor/SerialNumber/MODEL/IPAddress
--               (+cocm-xgzapp09만 OSParameter LOB — gp-011)
--    Cpus conf: MODEL/LOGICALCORE/PHYSICALCORE
--    Memory conf: TotalSize (MB)
-- ----------------------------------------------------------------------------
INSERT INTO polestar.core_config_prop
  (dtype, id, propertydefinition_id, configuration_id, name, stringvalue_short, stringvalue)
VALUES
  -- cob0-bnndbp01 서버 conf(9681001)
  ('SIMPLE', 200200001, 454, 9681001, 'Hostname',     'cob0-bnndbp01', NULL),
  ('SIMPLE', 200200002, 454, 9681001, 'OSType',       'Linux', NULL),
  ('SIMPLE', 200200003, 454, 9681001, 'OSVerson',     'Red Hat Enterprise Linux 8.8', NULL),
  ('SIMPLE', 200200004, 454, 9681001, 'Vendor',       'HPE', NULL),
  ('SIMPLE', 200200005, 454, 9681001, 'SerialNumber', 'KR2024BNN0001', NULL),
  ('SIMPLE', 200200006, 454, 9681001, 'MODEL',        'ProLiant DL380 Gen11', NULL),
  ('SIMPLE', 200200007, 454, 9681001, 'IPAddress',    '10.68.0.1', NULL),
  -- sbhdbo53 서버 conf(9681002)
  ('SIMPLE', 200200011, 454, 9681002, 'Hostname',     'sbhdbo53', NULL),
  ('SIMPLE', 200200012, 454, 9681002, 'OSType',       'Linux', NULL),
  ('SIMPLE', 200200013, 454, 9681002, 'OSVerson',     'Red Hat Enterprise Linux 8.6', NULL),
  ('SIMPLE', 200200014, 454, 9681002, 'Vendor',       'Dell', NULL),
  ('SIMPLE', 200200015, 454, 9681002, 'SerialNumber', 'KR2023DBO0053', NULL),
  ('SIMPLE', 200200016, 454, 9681002, 'MODEL',        'PowerEdge R650', NULL),
  ('SIMPLE', 200200017, 454, 9681002, 'IPAddress',    '10.68.0.2', NULL),
  -- cocm-xgzapp09 서버 conf(9681003) — OSParameter LOB 포함(gp-011)
  ('SIMPLE', 200200021, 454, 9681003, 'Hostname',     'cocm-xgzapp09', NULL),
  ('SIMPLE', 200200022, 454, 9681003, 'OSType',       'Linux', NULL),
  ('SIMPLE', 200200023, 454, 9681003, 'OSVerson',     'Red Hat Enterprise Linux 8.6', NULL),
  ('SIMPLE', 200200024, 454, 9681003, 'Vendor',       'HPE', NULL),
  ('SIMPLE', 200200025, 454, 9681003, 'SerialNumber', 'KR2024XGZ0009', NULL),
  ('SIMPLE', 200200026, 454, 9681003, 'MODEL',        'ProLiant DL360 Gen10', NULL),
  ('SIMPLE', 200200027, 454, 9681003, 'IPAddress',    '10.68.0.3', NULL),
  ('SIMPLE', 200200028, 454, 9681003, 'OSParameter',  NULL,
   E'kernel.shmmax = 68719476736\nkernel.shmall = 4294967296\nnet.core.somaxconn = 65535\nvm.swappiness = 10\nfs.file-max = 6815744'),
  -- cocm-ngcapo91 서버 conf(9681004)
  ('SIMPLE', 200200031, 454, 9681004, 'Hostname',     'cocm-ngcapo91', NULL),
  ('SIMPLE', 200200032, 454, 9681004, 'OSType',       'Linux', NULL),
  ('SIMPLE', 200200033, 454, 9681004, 'OSVerson',     'Red Hat Enterprise Linux 8.6', NULL),
  ('SIMPLE', 200200034, 454, 9681004, 'Vendor',       'HPE', NULL),
  ('SIMPLE', 200200035, 454, 9681004, 'SerialNumber', 'KR2024NGC0091', NULL),
  ('SIMPLE', 200200036, 454, 9681004, 'MODEL',        'ProLiant DL360 Gen10', NULL),
  ('SIMPLE', 200200037, 454, 9681004, 'IPAddress',    '10.68.0.4', NULL),
  -- Cpus conf
  ('SIMPLE', 200200041, 454, 9682501, 'MODEL',        'Intel Xeon Gold 6338', NULL),
  ('SIMPLE', 200200042, 454, 9682501, 'LOGICALCORE',  '8', NULL),
  ('SIMPLE', 200200043, 454, 9682501, 'PHYSICALCORE', '4', NULL),
  ('SIMPLE', 200200044, 454, 9682502, 'MODEL',        'Intel Xeon Silver 4314', NULL),
  ('SIMPLE', 200200045, 454, 9682502, 'LOGICALCORE',  '8', NULL),
  ('SIMPLE', 200200046, 454, 9682502, 'PHYSICALCORE', '4', NULL),
  ('SIMPLE', 200200047, 454, 9682503, 'MODEL',        'Intel Xeon Gold 6248R', NULL),
  ('SIMPLE', 200200048, 454, 9682503, 'LOGICALCORE',  '8', NULL),
  ('SIMPLE', 200200049, 454, 9682503, 'PHYSICALCORE', '4', NULL),
  -- cocm-ngcapo91 Cpus(9682504) — DB-ORA-023·cocm-hdkapp01과 동일 사양(gp-014)
  ('SIMPLE', 200200050, 454, 9682504, 'MODEL',        'Intel Xeon Gold 6248R', NULL),
  ('SIMPLE', 200200051, 454, 9682504, 'LOGICALCORE',  '16', NULL),
  ('SIMPLE', 200200052, 454, 9682504, 'PHYSICALCORE', '8', NULL),
  -- Memory conf: TotalSize (MB) — cocm-ngcapo91은 65536(gp-014 동일 사양)
  ('SIMPLE', 200200061, 454, 9683501, 'TotalSize', '32768', NULL),
  ('SIMPLE', 200200062, 454, 9683502, 'TotalSize', '16384', NULL),
  ('SIMPLE', 200200063, 454, 9683503, 'TotalSize', '32768', NULL),
  ('SIMPLE', 200200064, 454, 9683504, 'TotalSize', '65536', NULL);

COMMIT;
