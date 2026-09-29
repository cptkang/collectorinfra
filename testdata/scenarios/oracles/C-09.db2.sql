-- 오라클 정본 C-09 · DB2(polestar_b0) — plans/122 O-3
-- 대상 시나리오: C-09 「2026년 5월과 비교해서 6월에 CPU 사용률 평균이 가장 많이 상승한 서버와 상승폭을 알려줘」
-- 정답: 은행존 두 달 모두 통계가 있는 서버의 CPU 평균 차(6월 − 5월) 상위 50 후보. 집계 전 DOUBLE 캐스트(D-086).
-- compare: argmax (PG 정본과 짝 · 열 별칭 동일)
-- 검수: DB2: 미검수 — 폐쇄망 1회 사람 검수 후 동결(O-3). 부하: 2개월 stat_m × Cpus 조인
-- 근거: config/db_profiles/polestar_b0.yaml:284-289(DB2 소수점 보존) · :516-565(월간 통계 조인 구조)
SELECT
    COALESCE(svr.name, svr.hostname) AS server_name,
    CAST(ROUND(AVG(CASE WHEN s.stat_date = '202605' THEN CAST(s.avg_val AS DOUBLE) END), 2) AS DECIMAL(31, 2)) AS cpu_avg_202605,
    CAST(ROUND(AVG(CASE WHEN s.stat_date = '202606' THEN CAST(s.avg_val AS DOUBLE) END), 2) AS DECIMAL(31, 2)) AS cpu_avg_202606,
    CAST(ROUND(AVG(CASE WHEN s.stat_date = '202606' THEN CAST(s.avg_val AS DOUBLE) END)
             - AVG(CASE WHEN s.stat_date = '202605' THEN CAST(s.avg_val AS DOUBLE) END), 2) AS DECIMAL(31, 2)) AS increase
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
  AND s.stat_date IN ('202605', '202606')
  AND s.avg_val BETWEEN 0 AND 1000
GROUP BY svr.id, svr.name, svr.hostname
HAVING COUNT(CASE WHEN s.stat_date = '202605' THEN 1 END) > 0
   AND COUNT(CASE WHEN s.stat_date = '202606' THEN 1 END) > 0
ORDER BY increase DESC
FETCH FIRST 50 ROWS ONLY
