-- 오라클 정본 C-07 · DB2(polestar_b0) — plans/122 O-3
-- 대상 시나리오: C-07 「메모리 이용률이 90%를 초과한 적이 있는 서버를 조회해줘」
-- 정답: 은행존 전 보관 기간 월 통계 server.Memory Utilization max_val > 90(게이트 <= 1000) 활성 서버 집합(PG 정본과 짝 · 별칭 동일).
-- compare: keyset
-- 검수: DB2: 미검수 — 폐쇄망 1회 사람 검수 후 동결(O-3). 부하: 메모리 자원 × 전 기간 stat_m(resource_id 조인) —
--   기간 무제한이라 이 목록 중 가장 넓은 stat_m 읽기다. 검수 때 실행 시간을 적고 30초를 넘으면 동결하지 않는다.
-- 근거: config/db_profiles/polestar_b0.yaml:355-357(전 보관 기간 · stat_m · 상한 게이트)
SELECT DISTINCT COALESCE(svr.name, svr.hostname) AS server_name
FROM POLESTAR.cmm_resource r
JOIN POLESTAR.cmm_resource svr
  ON COALESCE(svr.platform_resource_id, svr.id) = r.platform_resource_id
 AND svr.resource_type = 'server.Server'
 AND svr.dtime IS NULL
JOIN POLESTAR.cmm_metric_stat_m s
  ON s.resource_id = r.id
WHERE r.resource_type = 'server.Memory'
  AND r.dtime IS NULL
  AND s.definition_name = 'Utilization'
  AND s.max_val > 90
  AND s.max_val <= 1000
FETCH FIRST 10000 ROWS ONLY
