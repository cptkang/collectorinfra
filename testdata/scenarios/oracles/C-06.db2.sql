-- 오라클 정본 C-06 · DB2(polestar_b0) — plans/122 O-3
-- 대상 시나리오: C-06 「이번 달 CPU 사용률」
-- 정답(②): 은행존 이번 달 1일~어제 일 통계 서버별 CPU 평균·최대(PG 정본과 같은 열 · 별칭 동일). 집계 전 DOUBLE 캐스트(D-086).
-- compare: rowset (PG 정본과 짝)
-- 검수: DB2: 미검수 — 폐쇄망 1회 사람 검수 후 동결(O-3). 부하: 당월 stat_d(최대 30일) × Cpus
-- 근거: config/db_profiles/polestar_b0.yaml:347-351(「이번 달」 = stat_d 1일~어제) · D-201
SELECT
    CAST(ROUND(AVG(CASE WHEN s.avg_val BETWEEN 0 AND 1000 THEN CAST(s.avg_val AS DOUBLE) END), 2) AS DECIMAL(31, 2)) AS cpu_avg,
    CAST(ROUND(MAX(CASE WHEN s.max_val BETWEEN 0 AND 1000 THEN CAST(s.max_val AS DOUBLE) END), 2) AS DECIMAL(31, 2)) AS cpu_max
FROM POLESTAR.cmm_resource r
JOIN POLESTAR.cmm_resource svr
  ON COALESCE(svr.platform_resource_id, svr.id) = r.platform_resource_id
 AND svr.resource_type = 'server.Server'
 AND svr.dtime IS NULL
JOIN POLESTAR.cmm_metric_stat_d s
  ON s.resource_id = r.id
WHERE r.resource_type = 'server.Cpus'
  AND r.dtime IS NULL
  AND s.definition_name = 'Utilization'
  AND s.stat_date BETWEEN :month_start_ymd AND :yesterday_ymd
GROUP BY svr.id
FETCH FIRST 10000 ROWS ONLY
