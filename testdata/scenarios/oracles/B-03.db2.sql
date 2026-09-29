-- 오라클 정본 B-03 · DB2(polestar_b0) — plans/122 O-3
-- 대상 시나리오: B-03 「cocm-hdkapp01 서버의 OS 종류·버전, IP주소, 호스트네임, CPU 모델, 메모리 용량을 조회해줘」
-- 정답: 은행존에 그 서버가 있으면 1행(요청 6열), 없으면 0행. 등록명은 "<호스트명> (<설명>)" 구조라 OR 3분기로 식별한다.
-- compare: rowset (PG 정본과 짝 · 열 별칭이 PG 와 같아야 전 DB 합산 비교가 된다)
-- 검수: DB2: 미검수 — 폐쇄망 1회 사람 검수 후 동결(O-3). 부하: Server·Cpus·Memory 피벗 1회(시스템 질의와 같은 규모)
-- 근거: config/db_profiles/polestar_b0.yaml:236-244(장비명 OR 3분기) · :324-332(단일 서버 피벗 HAVING)
SELECT
    MAX(CASE WHEN c.resource_type = 'server.Server' THEN c.hostname END) AS hostname,
    MAX(CASE WHEN c.resource_type = 'server.Server' THEN c.ipaddress END) AS ipaddress,
    MAX(CASE WHEN c.resource_type = 'server.Server' AND cc.name = 'OSType' THEN cc.stringvalue_short END) AS ostype,
    MAX(CASE WHEN c.resource_type = 'server.Server' AND cc.name = 'OSVerson' THEN cc.stringvalue_short END) AS osversion,
    MAX(CASE WHEN c.resource_type = 'server.Cpus' AND cc.name = 'MODEL' THEN cc.stringvalue_short END) AS cpu_model,
    MAX(CASE WHEN c.resource_type = 'server.Memory' AND cc.name = 'TotalSize' THEN cc.stringvalue_short END) AS mem_size
FROM POLESTAR.cmm_resource c
LEFT JOIN POLESTAR.core_config_prop cc
       ON c.resource_conf_id = cc.configuration_id
      AND cc.name IN ('OSType', 'OSVerson', 'MODEL', 'TotalSize')
WHERE c.resource_type IN ('server.Server', 'server.Cpus', 'server.Memory')
  AND c.dtime IS NULL
GROUP BY COALESCE(c.platform_resource_id, c.id)
HAVING (MAX(CASE WHEN c.resource_type = 'server.Server' THEN c.name END) = 'cocm-hdkapp01'
     OR MAX(CASE WHEN c.resource_type = 'server.Server' THEN c.name END) LIKE 'cocm-hdkapp01 (%'
     OR MAX(CASE WHEN c.resource_type = 'server.Server' THEN c.name END) LIKE 'cocm-hdkapp01(%')
FETCH FIRST 10 ROWS ONLY
