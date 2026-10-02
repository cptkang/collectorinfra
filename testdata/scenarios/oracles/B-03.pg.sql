-- 오라클 정본 B-03 · PostgreSQL(polestar_cm_gp · polestar_cm_yd · 로컬 polestar) — plans/122 O-3
-- 대상 시나리오: B-03 「cocm-hdkapp01 서버의 OS 종류·버전, IP주소, 호스트네임, CPU 모델, 메모리 용량을 조회해줘」
-- 정답: 그 서버 1행의 요청 6열(OS종류·OS버전·IP·호스트명·CPU모델·메모리용량). 열 이름이 아니라 값으로 대응한다.
-- compare: rowset(column_subset — 행 수 동일 + 오라클 열마다 값 멀티셋이 시스템 어떤 열과 일치)
-- 권장 spec: {id: B-03, compare: rowset, db_ids: [polestar_b0, polestar_cm_gp, polestar_cm_yd]}
--   서버는 한 DB 에만 있다 — 다른 DB 는 0행이 정답이다(전 DB 합 1행).
-- 주의: IP 는 마스킹 설정(SECURITY mask_ip)이 켜져 있으면 양쪽이 같게 가려진 뒤 비교된다(마스킹 대칭).
-- 검수: PG: 로컬 샌드박스 실행 확인 2026-09-29 (polestar 5434 · MCP 9099 · SQL 오류 0 · 앵커 2026-09-29) — 1행(6열 모두 값 있음)
-- 근거: config/db_profiles/polestar_cm_gp.yaml:441-466 단일 서버 피벗(식별은 HAVING · WHERE 금지)
SELECT
    MAX(CASE WHEN c.resource_type = 'server.Server' THEN c.hostname END) AS hostname,
    MAX(CASE WHEN c.resource_type = 'server.Server' THEN c.ipaddress END) AS ipaddress,
    MAX(CASE WHEN c.resource_type = 'server.Server' AND cc.name = 'OSType' THEN cc.stringvalue_short END) AS ostype,
    MAX(CASE WHEN c.resource_type = 'server.Server' AND cc.name = 'OSVerson' THEN cc.stringvalue_short END) AS osversion,
    MAX(CASE WHEN c.resource_type = 'server.Cpus' AND cc.name = 'MODEL' THEN cc.stringvalue_short END) AS cpu_model,
    MAX(CASE WHEN c.resource_type = 'server.Memory' AND cc.name = 'TotalSize' THEN cc.stringvalue_short END) AS mem_size
FROM polestar.cmm_resource c
LEFT JOIN polestar.core_config_prop cc
       ON c.resource_conf_id = cc.configuration_id
      AND cc.name IN ('OSType', 'OSVerson', 'MODEL', 'TotalSize')
WHERE c.resource_type IN ('server.Server', 'server.Cpus', 'server.Memory')
  AND c.dtime IS NULL
GROUP BY COALESCE(c.platform_resource_id, c.id)
HAVING MAX(CASE WHEN c.resource_type = 'server.Server' THEN c.name END) = 'cocm-hdkapp01'
LIMIT 10
