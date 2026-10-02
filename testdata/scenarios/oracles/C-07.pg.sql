-- 오라클 정본 C-07 · PostgreSQL(polestar_cm_gp · polestar_cm_yd · 로컬 polestar) — plans/122 O-3
-- 대상 시나리오: C-07 「메모리 이용률이 90%를 초과한 적이 있는 서버를 조회해줘」
-- 정답: 전 보관 기간(기간 조건 없음 · 「~한 적이 있는」) 월 통계에서 server.Memory Utilization max_val > 90
--   (쓰레기 값 게이트 max_val <= 1000)인 활성 서버 집합. 「이용률」 = 사용률(Utilization)로 푼 결과와 같으면 합격이다.
-- compare: keyset · match equal · 서버가 여러 달 걸려도 집합으로 본다(시스템이 월별 행을 내도 된다)
-- 권장 spec: {id: C-07, compare: keyset, key: [[server_name, pname, 서버명, 장비명]], db_ids: [polestar_b0, polestar_cm_gp, polestar_cm_yd]}
-- 검수: PG: 로컬 샌드박스 실행 확인 2026-09-29 (polestar 5434 · MCP 9099 · SQL 오류 0 · 앵커 2026-09-29) — 1행(DB-ORA-023 · 2026-06 max 95.2)
-- 근거: config/db_profiles/polestar_cm_gp.yaml:332-334(전 보관 기간 · stat_m · 상한 게이트)
SELECT DISTINCT COALESCE(svr.name, svr.hostname) AS server_name
FROM polestar.cmm_resource r
JOIN polestar.cmm_resource svr
  ON COALESCE(svr.platform_resource_id, svr.id) = r.platform_resource_id
 AND svr.resource_type = 'server.Server'
 AND svr.dtime IS NULL
JOIN polestar.cmm_metric_stat_m s
  ON s.resource_id = r.id
WHERE r.resource_type = 'server.Memory'
  AND r.dtime IS NULL
  AND s.definition_name = 'Utilization'
  AND s.max_val > 90
  AND s.max_val <= 1000
LIMIT 10000
