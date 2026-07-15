-- ============================================================================
-- Plan 61 Text-to-SQL EX 골드셋 픽스처 (E1 / D-072)
-- ============================================================================
-- 목적: testdata/text2sql_gold/{gp,yd,b0}.yaml 골드 SQL 26건이 polestar_pg에서
--       비어있지 않은 결과집합을 반환하도록 하는 테스트 데이터.
--       (0건 결과는 EX 채점이 자명하게 일치해 벤치마크 변별력이 없음)
--
-- ID 격리 범위 (기존 더미·Plan 52 노이즈 픽스처 95xxxxx와 충돌 방지):
--   cmm_resource        : 9610001 ~ 9669999 (conf id 9611001 ~ 9661999)
--   core_config_prop    : 200100001 ~ 200199999
--   alarm/def/noti/group: 9610001 ~ 9619999
--   acc_user_group      : 9610001
--
-- 멱등성: 상단 DELETE 블록이 위 범위를 정리한 뒤 INSERT하므로 재실행 안전.
-- 읽기전용 원칙(D-003)은 런타임 에이전트 경로에만 적용 — 본 파일은 테스트 DB
-- 시드용 오프라인 스크립트다.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 0. 멱등 정리 (Plan 61 픽스처 범위만)
-- ----------------------------------------------------------------------------
DELETE FROM polestar.cmm_alarm_def_noti_group WHERE alarmnotification_id BETWEEN 9610001 AND 9619999;
DELETE FROM polestar.cmm_alarm_def_noti       WHERE id BETWEEN 9610001 AND 9619999;
DELETE FROM polestar.cmm_alarm_active         WHERE alarm_id BETWEEN 9610001 AND 9619999;
DELETE FROM polestar.cmm_alarm                WHERE id BETWEEN 9610001 AND 9619999;
DELETE FROM polestar.cmm_alarm_def            WHERE id BETWEEN 9610001 AND 9619999;
DELETE FROM polestar.acc_user_group           WHERE id = 9610001;
DELETE FROM polestar.cmm_metric_stat_m        WHERE resource_id BETWEEN 9610001 AND 9669999;
DELETE FROM polestar.core_config_prop         WHERE id BETWEEN 200100001 AND 200199999;
DELETE FROM polestar.cmm_resource             WHERE id BETWEEN 9610001 AND 9669999;

-- ----------------------------------------------------------------------------
-- 1. 서버 4대 (server.Server)
--    gp-007/011: SV-WEB-001 지목 조회
--    gp-014    : DB-ORA-023 와 동일 사양(cocm-hdkapp01) 탐색
--    gp-005    : cocm-hdkapp01 상세 조회
--    gp-006/yd-003: 비정상(avail_status != 0) 서버 → SV-BATCH-009
-- ----------------------------------------------------------------------------
INSERT INTO polestar.cmm_resource
  (dtype, id, acl_id, importance_id, inheritstatus, invisible, name, hostname,
   ipaddress, avail_status, resource_key, resource_type, resource_conf_id,
   platform_resource_id, parent_resource_id, ctime, dtime)
VALUES
  ('PlatformResource', 9610001, 112, 1, 1, 0, 'SV-WEB-001',    'svweb001',      '10.61.0.1', 0, 'P61_SV-WEB-001',    'server.Server', 9611001, NULL, NULL, 1767200000000, NULL),
  ('PlatformResource', 9610002, 112, 1, 1, 0, 'DB-ORA-023',    'dbora023',      '10.61.0.2', 0, 'P61_DB-ORA-023',    'server.Server', 9611002, NULL, NULL, 1767200000000, NULL),
  ('PlatformResource', 9610003, 112, 1, 1, 0, 'cocm-hdkapp01', 'cocm-hdkapp01', '10.61.0.3', 0, 'P61_cocm-hdkapp01', 'server.Server', 9611003, NULL, NULL, 1767200000000, NULL),
  ('PlatformResource', 9610004, 112, 1, 1, 0, 'SV-BATCH-009',  'svbatch009',    '10.61.0.4', 2, 'P61_SV-BATCH-009',  'server.Server', 9611004, NULL, NULL, 1767200000000, NULL);

-- ----------------------------------------------------------------------------
-- 2. 자식 리소스 (platform_resource_id/parent_resource_id → 서버)
--    server.Cpus/Memory: EAV 사양 + 성능통계 피벗 (gp-005/009/014, b0-004/005, yd-004/005)
--    server.FileSystems/Disks: 월간 통계 (gp-010)
--    server.Cpu: 개별 CPU 코어 (gp-008 parent_resource_id 조인)
-- ----------------------------------------------------------------------------
INSERT INTO polestar.cmm_resource
  (dtype, id, acl_id, importance_id, inheritstatus, invisible, name, hostname,
   ipaddress, avail_status, resource_key, resource_type, resource_conf_id,
   platform_resource_id, parent_resource_id, ctime, dtime)
VALUES
  -- server.Cpus (사양 conf 보유)
  ('PlatformResource', 9620001, 112, 1, 1, 0, 'Cpus', NULL, NULL, 0, 'P61_CPUS_9610001', 'server.Cpus', 9621001, 9610001, 9610001, 1767200000000, NULL),
  ('PlatformResource', 9620002, 112, 1, 1, 0, 'Cpus', NULL, NULL, 0, 'P61_CPUS_9610002', 'server.Cpus', 9621002, 9610002, 9610002, 1767200000000, NULL),
  ('PlatformResource', 9620003, 112, 1, 1, 0, 'Cpus', NULL, NULL, 0, 'P61_CPUS_9610003', 'server.Cpus', 9621003, 9610003, 9610003, 1767200000000, NULL),
  ('PlatformResource', 9620004, 112, 1, 1, 0, 'Cpus', NULL, NULL, 0, 'P61_CPUS_9610004', 'server.Cpus', 9621004, 9610004, 9610004, 1767200000000, NULL),
  -- server.Memory (TotalSize conf 보유)
  ('PlatformResource', 9630001, 112, 1, 1, 0, 'Memory', NULL, NULL, 0, 'P61_MEM_9610001', 'server.Memory', 9631001, 9610001, 9610001, 1767200000000, NULL),
  ('PlatformResource', 9630002, 112, 1, 1, 0, 'Memory', NULL, NULL, 0, 'P61_MEM_9610002', 'server.Memory', 9631002, 9610002, 9610002, 1767200000000, NULL),
  ('PlatformResource', 9630003, 112, 1, 1, 0, 'Memory', NULL, NULL, 0, 'P61_MEM_9610003', 'server.Memory', 9631003, 9610003, 9610003, 1767200000000, NULL),
  ('PlatformResource', 9630004, 112, 1, 1, 0, 'Memory', NULL, NULL, 0, 'P61_MEM_9610004', 'server.Memory', 9631004, 9610004, 9610004, 1767200000000, NULL),
  -- server.FileSystems
  ('PlatformResource', 9640001, 112, 1, 1, 0, 'FileSystems', NULL, NULL, 0, 'P61_FS_9610001', 'server.FileSystems', NULL, 9610001, 9610001, 1767200000000, NULL),
  ('PlatformResource', 9640002, 112, 1, 1, 0, 'FileSystems', NULL, NULL, 0, 'P61_FS_9610002', 'server.FileSystems', NULL, 9610002, 9610002, 1767200000000, NULL),
  ('PlatformResource', 9640003, 112, 1, 1, 0, 'FileSystems', NULL, NULL, 0, 'P61_FS_9610003', 'server.FileSystems', NULL, 9610003, 9610003, 1767200000000, NULL),
  ('PlatformResource', 9640004, 112, 1, 1, 0, 'FileSystems', NULL, NULL, 0, 'P61_FS_9610004', 'server.FileSystems', NULL, 9610004, 9610004, 1767200000000, NULL),
  -- server.Disks
  ('PlatformResource', 9650001, 112, 1, 1, 0, 'Disks', NULL, NULL, 0, 'P61_DISKS_9610001', 'server.Disks', NULL, 9610001, 9610001, 1767200000000, NULL),
  ('PlatformResource', 9650002, 112, 1, 1, 0, 'Disks', NULL, NULL, 0, 'P61_DISKS_9610002', 'server.Disks', NULL, 9610002, 9610002, 1767200000000, NULL),
  ('PlatformResource', 9650003, 112, 1, 1, 0, 'Disks', NULL, NULL, 0, 'P61_DISKS_9610003', 'server.Disks', NULL, 9610003, 9610003, 1767200000000, NULL),
  ('PlatformResource', 9650004, 112, 1, 1, 0, 'Disks', NULL, NULL, 0, 'P61_DISKS_9610004', 'server.Disks', NULL, 9610004, 9610004, 1767200000000, NULL),
  -- server.Cpu (개별 코어 — gp-008)
  ('PlatformResource', 9660001, 112, 1, 1, 0, 'CPU0', NULL, NULL, 0, 'P61_CPU0_9610001', 'server.Cpu', NULL, 9610001, 9610001, 1767200000000, NULL),
  ('PlatformResource', 9660002, 112, 1, 1, 0, 'CPU1', NULL, NULL, 0, 'P61_CPU1_9610001', 'server.Cpu', NULL, 9610001, 9610001, 1767200000000, NULL);

-- ----------------------------------------------------------------------------
-- 3. EAV 속성 (core_config_prop)
--    서버 conf: OSType/OSVerson/Vendor/SerialNumber/MODEL/Hostname/IPAddress/OSParameter
--    Cpus conf: MODEL/LOGICALCORE/PHYSICALCORE — DB-ORA-023 == cocm-hdkapp01 (gp-014 동일사양)
--    Memory conf: TotalSize — DB-ORA-023 == cocm-hdkapp01
--    OSParameter는 LOB(stringvalue) 사용 — stringvalue_short는 NULL
--    (Known Mistakes 2026-06-10: OSParameter LOB 값 조회 주의)
-- ----------------------------------------------------------------------------
INSERT INTO polestar.core_config_prop
  (dtype, id, propertydefinition_id, configuration_id, name, stringvalue_short, stringvalue)
VALUES
  -- SV-WEB-001 서버 conf(9611001)
  ('SIMPLE', 200100001, 454, 9611001, 'Hostname',     'svweb001', NULL),
  ('SIMPLE', 200100002, 454, 9611001, 'OSType',       'Linux', NULL),
  ('SIMPLE', 200100003, 454, 9611001, 'OSVerson',     'Red Hat Enterprise Linux 8.6', NULL),
  ('SIMPLE', 200100004, 454, 9611001, 'Vendor',       'HPE', NULL),
  ('SIMPLE', 200100005, 454, 9611001, 'SerialNumber', 'KR2024WEB0001', NULL),
  ('SIMPLE', 200100006, 454, 9611001, 'MODEL',        'ProLiant DL380 Gen10', NULL),
  ('SIMPLE', 200100007, 454, 9611001, 'IPAddress',    '10.61.0.1', NULL),
  ('SIMPLE', 200100008, 454, 9611001, 'OSParameter',  NULL,
   E'kernel.shmmax = 68719476736\nkernel.shmall = 4294967296\nnet.core.somaxconn = 1024\nvm.swappiness = 10\nfs.file-max = 6815744'),
  -- DB-ORA-023 서버 conf(9611002)
  ('SIMPLE', 200100011, 454, 9611002, 'Hostname',     'dbora023', NULL),
  ('SIMPLE', 200100012, 454, 9611002, 'OSType',       'Linux', NULL),
  ('SIMPLE', 200100013, 454, 9611002, 'OSVerson',     'Red Hat Enterprise Linux 8.4', NULL),
  ('SIMPLE', 200100014, 454, 9611002, 'Vendor',       'Dell', NULL),
  ('SIMPLE', 200100015, 454, 9611002, 'SerialNumber', 'KR2023ORA0023', NULL),
  ('SIMPLE', 200100016, 454, 9611002, 'MODEL',        'PowerEdge R750', NULL),
  ('SIMPLE', 200100017, 454, 9611002, 'IPAddress',    '10.61.0.2', NULL),
  ('SIMPLE', 200100018, 454, 9611002, 'OSParameter',  NULL,
   E'kernel.shmmax = 137438953472\nkernel.sem = 250 32000 100 128\nnet.ipv4.ip_local_port_range = 9000 65500'),
  -- cocm-hdkapp01 서버 conf(9611003)
  ('SIMPLE', 200100021, 454, 9611003, 'Hostname',     'cocm-hdkapp01', NULL),
  ('SIMPLE', 200100022, 454, 9611003, 'OSType',       'Linux', NULL),
  ('SIMPLE', 200100023, 454, 9611003, 'OSVerson',     'Red Hat Enterprise Linux 8.6', NULL),
  ('SIMPLE', 200100024, 454, 9611003, 'Vendor',       'HPE', NULL),
  ('SIMPLE', 200100025, 454, 9611003, 'SerialNumber', 'KR2024APP0001', NULL),
  ('SIMPLE', 200100026, 454, 9611003, 'MODEL',        'ProLiant DL360 Gen10', NULL),
  ('SIMPLE', 200100027, 454, 9611003, 'IPAddress',    '10.61.0.3', NULL),
  -- SV-BATCH-009 서버 conf(9611004)
  ('SIMPLE', 200100031, 454, 9611004, 'Hostname',     'svbatch009', NULL),
  ('SIMPLE', 200100032, 454, 9611004, 'OSType',       'AIX', NULL),
  ('SIMPLE', 200100033, 454, 9611004, 'OSVerson',     'AIX 7.2', NULL),
  ('SIMPLE', 200100034, 454, 9611004, 'Vendor',       'IBM', NULL),
  -- Cpus conf: SV-WEB-001(9621001)
  ('SIMPLE', 200100041, 454, 9621001, 'MODEL',        'Intel Xeon Silver 4210', NULL),
  ('SIMPLE', 200100042, 454, 9621001, 'LOGICALCORE',  '8', NULL),
  ('SIMPLE', 200100043, 454, 9621001, 'PHYSICALCORE', '4', NULL),
  -- Cpus conf: DB-ORA-023(9621002) — cocm-hdkapp01과 동일 사양
  ('SIMPLE', 200100044, 454, 9621002, 'MODEL',        'Intel Xeon Gold 6248R', NULL),
  ('SIMPLE', 200100045, 454, 9621002, 'LOGICALCORE',  '16', NULL),
  ('SIMPLE', 200100046, 454, 9621002, 'PHYSICALCORE', '8', NULL),
  -- Cpus conf: cocm-hdkapp01(9621003) — DB-ORA-023과 동일 사양 (gp-014)
  ('SIMPLE', 200100047, 454, 9621003, 'MODEL',        'Intel Xeon Gold 6248R', NULL),
  ('SIMPLE', 200100048, 454, 9621003, 'LOGICALCORE',  '16', NULL),
  ('SIMPLE', 200100049, 454, 9621003, 'PHYSICALCORE', '8', NULL),
  -- Cpus conf: SV-BATCH-009(9621004)
  ('SIMPLE', 200100050, 454, 9621004, 'MODEL',        'IBM POWER9', NULL),
  ('SIMPLE', 200100051, 454, 9621004, 'LOGICALCORE',  '4', NULL),
  ('SIMPLE', 200100052, 454, 9621004, 'PHYSICALCORE', '2', NULL),
  -- Memory conf: TotalSize (MB)
  ('SIMPLE', 200100061, 454, 9631001, 'TotalSize', '32768', NULL),
  ('SIMPLE', 200100062, 454, 9631002, 'TotalSize', '65536', NULL),
  ('SIMPLE', 200100063, 454, 9631003, 'TotalSize', '65536', NULL),
  ('SIMPLE', 200100064, 454, 9631004, 'TotalSize', '16384', NULL);

-- ----------------------------------------------------------------------------
-- 4. 월간 성능통계 (cmm_metric_stat_m)
--    stat_date: 'YYYYMM' (골드 SQL: TO_DATE(stat_date || '01', 'YYYYMMDD'))
--    Cpus/Memory/FileSystems: Utilization, Disks: MaxIORate
--    b0-005: DB-ORA-023 Memory 202606 max_val=95.2 (> 90)
-- ----------------------------------------------------------------------------
INSERT INTO polestar.cmm_metric_stat_m
  (resource_id, definition_name, stat_date, avg_val, min_val, max_val)
VALUES
  -- SV-WEB-001: Cpus(9620001) / Memory(9630001) / FS(9640001) / Disks(9650001)
  (9620001, 'Utilization', '202605', 35.2, 12.1, 61.7),
  (9620001, 'Utilization', '202606', 42.8, 15.3, 72.4),
  (9620001, 'Utilization', '202607', 38.5, 14.0, 66.2),
  (9630001, 'Utilization', '202605', 55.1, 40.2, 71.9),
  (9630001, 'Utilization', '202606', 58.7, 43.5, 76.3),
  (9630001, 'Utilization', '202607', 57.2, 41.8, 74.5),
  (9640001, 'Utilization', '202606', 62.4, 55.0, 70.1),
  (9640001, 'Utilization', '202607', 64.1, 56.2, 72.8),
  (9650001, 'MaxIORate',   '202606', 118.6, 45.2, 312.4),
  (9650001, 'MaxIORate',   '202607', 125.3, 48.7, 298.1),
  -- DB-ORA-023: Cpus(9620002) / Memory(9630002) / FS(9640002) / Disks(9650002)
  (9620002, 'Utilization', '202605', 68.4, 32.5, 89.2),
  (9620002, 'Utilization', '202606', 72.1, 35.8, 91.6),
  (9620002, 'Utilization', '202607', 70.5, 34.2, 88.9),
  (9630002, 'Utilization', '202605', 78.3, 65.1, 88.4),
  (9630002, 'Utilization', '202606', 82.6, 68.9, 95.2),
  (9630002, 'Utilization', '202607', 80.1, 66.3, 89.7),
  (9640002, 'Utilization', '202606', 71.5, 63.2, 80.6),
  (9640002, 'Utilization', '202607', 73.8, 64.9, 82.3),
  (9650002, 'MaxIORate',   '202606', 245.7, 98.4, 587.2),
  (9650002, 'MaxIORate',   '202607', 251.2, 102.6, 601.8),
  -- cocm-hdkapp01: Cpus(9620003) / Memory(9630003) / FS(9640003) / Disks(9650003)
  (9620003, 'Utilization', '202605', 45.6, 20.3, 71.8),
  (9620003, 'Utilization', '202606', 48.9, 22.7, 75.4),
  (9620003, 'Utilization', '202607', 47.2, 21.5, 73.1),
  (9630003, 'Utilization', '202605', 61.8, 50.4, 74.2),
  (9630003, 'Utilization', '202606', 64.3, 52.8, 77.6),
  (9630003, 'Utilization', '202607', 63.1, 51.6, 75.9),
  (9640003, 'Utilization', '202606', 58.2, 49.1, 67.4),
  (9640003, 'Utilization', '202607', 59.7, 50.3, 69.2),
  (9650003, 'MaxIORate',   '202606', 156.4, 62.8, 389.5),
  (9650003, 'MaxIORate',   '202607', 161.9, 65.2, 402.3),
  -- SV-BATCH-009: Cpus(9620004) / Memory(9630004) / FS(9640004) / Disks(9650004)
  (9620004, 'Utilization', '202605', 15.8, 5.2, 42.6),
  (9620004, 'Utilization', '202606', 18.3, 6.7, 48.1),
  (9620004, 'Utilization', '202607', 16.9, 5.9, 45.3),
  (9630004, 'Utilization', '202605', 38.4, 30.1, 47.2),
  (9630004, 'Utilization', '202606', 40.7, 32.5, 49.8),
  (9630004, 'Utilization', '202607', 39.5, 31.3, 48.4),
  (9640004, 'Utilization', '202606', 45.6, 38.2, 53.7),
  (9640004, 'Utilization', '202607', 46.8, 39.4, 55.1),
  (9650004, 'MaxIORate',   '202606', 78.2, 28.4, 195.6),
  (9650004, 'MaxIORate',   '202607', 81.5, 30.1, 203.8);

-- ----------------------------------------------------------------------------
-- 5. 알람 체계 (gp-012/015, yd-006)
--    def: 마스터(9610001) ← 자식 def(9610002/9610003, masterdefinition_id)
--    noti: definition_id = 마스터 def id (yd-006: DN.DEFINITION_ID = D.MASTERDEFINITION_ID)
--    noti_group: alarmnotification_id → noti.id, targetgroups → acc_user_group.id
--    alarm: severity 2/3, ctime 최근 3개월 이내(gp-015), 활성 2건(gp-012)
-- ----------------------------------------------------------------------------
INSERT INTO polestar.acc_user_group (id, master, name, description)
VALUES (9610001, 0, '인프라운영1팀', 'Plan 61 픽스처 — 알람 담당자 그룹');

INSERT INTO polestar.cmm_alarm_def
  (dtype, id, enabled, name, alarmseverity, masterdefinition_id, description)
VALUES
  ('AlarmDefinition', 9610001, 1, '자원 사용률 임계 초과 (마스터)', 3, NULL,    'Plan 61 픽스처 — 마스터 정의'),
  ('AlarmDefinition', 9610002, 1, 'CPU 사용률 임계 초과',           3, 9610001, 'Plan 61 픽스처'),
  ('AlarmDefinition', 9610003, 1, '메모리 사용률 임계 초과',        2, 9610001, 'Plan 61 픽스처');

INSERT INTO polestar.cmm_alarm_def_noti
  (id, alarmseverity, notimethod, notitarget, definition_id)
VALUES (9610001, 2, 'EMAIL', 'GROUP', 9610001);

INSERT INTO polestar.cmm_alarm_def_noti_group
  (alarmnotification_id, targetgroups)
VALUES (9610001, 9610001);

INSERT INTO polestar.cmm_alarm
  (id, alarmseverity, ctime, conditionlogtext, currentalarmstatus, resourcestatus,
   definition_id, resource_id, dtime)
VALUES
  (9610001, 3, TIMESTAMP '2026-07-13 10:15:00', 'CPU Utilization 92% > 90% (5min)',    'ACTIVE',  1, 9610002, 9610001, NULL),
  (9610002, 3, TIMESTAMP '2026-07-10 03:42:00', 'Memory Utilization 95% > 90% (5min)', 'CLEARED', 1, 9610003, 9610002, NULL),
  (9610003, 2, TIMESTAMP '2026-06-25 14:08:00', 'Memory Utilization 85% > 80% (5min)', 'CLEARED', 1, 9610003, 9610002, NULL),
  (9610004, 2, TIMESTAMP '2026-06-05 22:31:00', 'CPU Utilization 82% > 80% (5min)',    'CLEARED', 1, 9610002, 9610004, NULL),
  (9610005, 3, TIMESTAMP '2026-05-20 07:55:00', 'CPU Utilization 94% > 90% (5min)',    'ACTIVE',  1, 9610002, 9610004, NULL);

INSERT INTO polestar.cmm_alarm_active
  (alarm_id, alarmseverity, ctime, conditionlogtext, currentalarmstatus, resourcestatus,
   definition_id, resource_id)
VALUES
  (9610001, 3, TIMESTAMP '2026-07-13 10:15:00', 'CPU Utilization 92% > 90% (5min)', 'ACTIVE', 1, 9610002, 9610001),
  (9610005, 3, TIMESTAMP '2026-05-20 07:55:00', 'CPU Utilization 94% > 90% (5min)', 'ACTIVE', 1, 9610002, 9610004);

COMMIT;
