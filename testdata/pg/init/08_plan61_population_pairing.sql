-- ============================================================================
-- Plan 61 B4-(b): 서버 이중 표현 페어 정렬 (population pairing)
-- ============================================================================
-- 배경 (docs/plan61_bugfix_plan.md B4):
--   샌드박스에는 서버 표현 규약이 두 가지 혼재한다.
--     · 합성 더미 40대(02_insert): 앵커 = resource_type='platform.server' 행
--       (가족에 server.Server 행 없음)
--     · 실캡처(hostapo01/02, 05_insert_excel_data)·Plan61 픽스처(07)·Plan52
--       노이즈(06): 앵커 = resource_type='server.Server' 행(자기참조
--       platform_resource_id, platform.% 행 없음 — 실 폴스타 형상)
--   두 모집단이 서로소라 'platform.server%' 조회(프로필 예시 규약)와
--   server.Server 피벗(시맨틱 모델/D-050 규약)이 서로 다른 서버를 반환
--   → 생성 SQL 품질과 무관하게 EX 채점이 구조적으로 실패한다.
--
-- 목적: 실 DB의 불변식("어느 규약으로 조회해도 같은 서버 집합")을 재현.
--   각 활성 서버가 두 표현을 모두 갖도록 반대편 쌍둥이 행을 추가한다.
--   쌍둥이는 hostname/ipaddress/name/avail_status/ctime을 복사하고
--   **resource_conf_id를 원본과 공유**하여 EAV 속성 값이 동일하게 조회된다.
--   가족 연결은 platform_resource_id = 원본 가족 앵커 id.
--
-- ID 격리 범위 (기존 300xxx 합성·95xxxxx Plan52·96[1-6]xxxxx Plan61과 격리):
--   server.Server 쌍둥이   : 9670001 ~ 9670999
--   platform.server 쌍둥이 : 9671001 ~ 9671999
--
-- 멱등성: 상단 DELETE가 위 범위를 정리한 뒤 SELECT 기반 INSERT하므로 재실행 안전.
-- 06/07 등 기존 픽스처는 불변(수정 금지 — 각 플랜 소유).
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 0. 멱등 정리 (본 픽스처 범위만)
-- ----------------------------------------------------------------------------
DELETE FROM polestar.cmm_resource WHERE id BETWEEN 9670001 AND 9671999;

-- ----------------------------------------------------------------------------
-- 1. platform.server 앵커(합성 40대) → server.Server 쌍둥이 추가
--    피벗(door B)이 이 가족들을 보게 된다. GROUP BY COALESCE(platform_resource_id, id)
--    기준으로 가족당 1행(앵커 id 그룹) 유지.
-- ----------------------------------------------------------------------------
INSERT INTO polestar.cmm_resource
  (dtype, id, acl_id, avail_status, ctime, dtime, haschildren, hostname,
   importance_id, inheritstatus, invisible, ipaddress, name,
   parent_resource_id, platform_resource_id, resource_key, resource_type,
   resource_conf_id)
SELECT
  'PlatformResource',
  9670000 + ROW_NUMBER() OVER (ORDER BY a.id),
  a.acl_id,
  a.avail_status,
  a.ctime,
  NULL,
  0,
  a.hostname,
  a.importance_id,
  0,
  0,
  a.ipaddress,
  a.name,
  a.id,
  a.id,
  'P61PAIR_SS_' || a.id,
  'server.Server',
  a.resource_conf_id
FROM polestar.cmm_resource a
WHERE a.resource_type LIKE 'platform.server%'
  AND a.dtime IS NULL
  AND NOT EXISTS (
    SELECT 1 FROM polestar.cmm_resource s
    WHERE s.resource_type = 'server.Server'
      AND s.dtime IS NULL
      AND s.platform_resource_id = a.id
  );

-- ----------------------------------------------------------------------------
-- 2. server.Server 앵커(실캡처 2 + Plan61 4 + Plan52 노이즈 4)
--    → platform.server 쌍둥이 추가. door A(LIKE 'platform.server%')가
--    이 서버들도 보게 된다. 노이즈 행(hostname NULL)도 페어링해
--    두 장부의 결과 집합이 항상 동일하도록 유지한다.
-- ----------------------------------------------------------------------------
INSERT INTO polestar.cmm_resource
  (dtype, id, acl_id, avail_status, ctime, dtime, haschildren, hostname,
   importance_id, inheritstatus, invisible, ipaddress, name,
   parent_resource_id, platform_resource_id, resource_key, resource_type,
   resource_conf_id)
SELECT
  'ServiceResource',
  9671000 + ROW_NUMBER() OVER (ORDER BY s.id),
  s.acl_id,
  s.avail_status,
  s.ctime,
  NULL,
  1,
  s.hostname,
  s.importance_id,
  0,
  0,
  s.ipaddress,
  s.name,
  COALESCE(s.platform_resource_id, s.id),
  COALESCE(s.platform_resource_id, s.id),
  'P61PAIR_PF_' || s.id,
  'platform.server',
  s.resource_conf_id
FROM polestar.cmm_resource s
WHERE s.resource_type = 'server.Server'
  AND s.dtime IS NULL
  AND s.id < 9670000  -- 1번에서 만든 쌍둥이 제외
  AND NOT EXISTS (
    SELECT 1 FROM polestar.cmm_resource p
    WHERE p.resource_type LIKE 'platform.server%'
      AND p.dtime IS NULL
      AND p.id = COALESCE(s.platform_resource_id, s.id)
  )
  AND NOT EXISTS (
    SELECT 1 FROM polestar.cmm_resource p2
    WHERE p2.resource_type LIKE 'platform.server%'
      AND p2.dtime IS NULL
      AND p2.platform_resource_id = COALESCE(s.platform_resource_id, s.id)
  );

COMMIT;

-- ----------------------------------------------------------------------------
-- 검증(수동 실행용): 두 장부의 서버 수·hostname 집합이 동일해야 한다.
--   SELECT COUNT(*) FROM polestar.cmm_resource
--    WHERE resource_type LIKE 'platform.server%' AND dtime IS NULL;
--   SELECT COUNT(*) FROM polestar.cmm_resource
--    WHERE resource_type = 'server.Server' AND dtime IS NULL;
--   -- hostname 대칭차: 0행이어야 함
--   SELECT COALESCE(a.hostname, b.hostname)
--     FROM (SELECT hostname FROM polestar.cmm_resource
--            WHERE resource_type LIKE 'platform.server%' AND dtime IS NULL) a
--     FULL OUTER JOIN
--          (SELECT hostname FROM polestar.cmm_resource
--            WHERE resource_type = 'server.Server' AND dtime IS NULL) b
--       ON a.hostname = b.hostname
--    WHERE a.hostname IS NULL OR b.hostname IS NULL;
-- ----------------------------------------------------------------------------
