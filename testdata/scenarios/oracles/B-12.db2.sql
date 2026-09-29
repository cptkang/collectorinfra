-- 오라클 정본 B-12 · DB2(polestar_b0) — plans/122 O-3
-- 대상 시나리오: B-12 「2026년 6월 서버별 CPU 사용률 평균이 높은 상위 3대를 보여줘」
-- 정답: 은행존 2026-06 서버별 CPU 평균 상위 50 후보(전역 상위 3 판정 재료). DB2 AVG 는 정수 컬럼을 정수로 집계하므로
--   집계 전 DOUBLE 캐스트 · 최종 DECIMAL(31,2)(D-086).
-- compare: argmax (PG 정본과 짝 · 열 별칭 동일)
-- 검수: DB2: 미검수 — 폐쇄망 1회 사람 검수 후 동결(O-3). 부하: 1개월 stat_m × Cpus 조인(시스템 질의와 같은 규모)
-- 근거: config/db_profiles/polestar_b0.yaml:284-289(DB2 소수점 보존) · :488-514(용량+사용률 피벗)
SELECT
    COALESCE(svr.name, svr.hostname) AS server_name,
    CAST(ROUND(AVG(CAST(s.avg_val AS DOUBLE)), 2) AS DECIMAL(31, 2)) AS cpu_avg
FROM POLESTAR.cmm_resource r
JOIN POLESTAR.cmm_resource svr
  ON COALESCE(svr.platform_resource_id, svr.id) = r.platform_resource_id
 AND svr.resource_type = 'server.Server'
 AND svr.dtime IS NULL
JOIN POLESTAR.cmm_metric_stat_m s
  ON s.resource_id = r.id
WHERE r.resource_type = 'server.Cpus'
  AND r.dtime IS NULL
  AND s.definition_name = 'Utilization'
  AND s.stat_date = '202606'
  AND s.avg_val BETWEEN 0 AND 1000
GROUP BY svr.id, svr.name, svr.hostname
ORDER BY cpu_avg DESC
FETCH FIRST 50 ROWS ONLY
