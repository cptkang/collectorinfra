-- 오라클 정본 C-01 · DB2(polestar_b0) — plans/122 O-3
-- 대상 시나리오: C-01 「전체 서버들의 CPU, 메모리 용량 및 사용률(평균·최대) 리스트 … 지난달 1개월 통계 기준」
-- 정답: 은행존 활성 서버당 1행 — 지난달(:prev_month) CPU·메모리 사용률 평균·최대 + 메모리 용량(PG 정본과 같은 열 · 별칭 동일).
--   집계 전 DOUBLE 캐스트 · 최종 DECIMAL(31,2)(D-086).
-- compare: rowset (PG 정본과 짝)
-- 검수: DB2: 미검수 — 폐쇄망 1회 사람 검수 후 동결(O-3). 부하: Server·Cpus·Memory 피벗 + 1개월 stat_m(시스템 질의와 같은 규모)
-- 근거: config/db_profiles/polestar_b0.yaml:488-514(D-068 용량+사용률 피벗 DB2 형)
SELECT
    CAST(ROUND(AVG(CASE WHEN c.resource_type = 'server.Cpus' AND s.avg_val BETWEEN 0 AND 1000 THEN CAST(s.avg_val AS DOUBLE) END), 2) AS DECIMAL(31, 2)) AS cpu_avg,
    CAST(ROUND(MAX(CASE WHEN c.resource_type = 'server.Cpus' AND s.max_val BETWEEN 0 AND 1000 THEN CAST(s.max_val AS DOUBLE) END), 2) AS DECIMAL(31, 2)) AS cpu_max,
    CAST(ROUND(AVG(CASE WHEN c.resource_type = 'server.Memory' AND s.avg_val BETWEEN 0 AND 1000 THEN CAST(s.avg_val AS DOUBLE) END), 2) AS DECIMAL(31, 2)) AS mem_avg,
    CAST(ROUND(MAX(CASE WHEN c.resource_type = 'server.Memory' AND s.max_val BETWEEN 0 AND 1000 THEN CAST(s.max_val AS DOUBLE) END), 2) AS DECIMAL(31, 2)) AS mem_max,
    MAX(CASE WHEN c.resource_type = 'server.Memory' AND cc.name = 'TotalSize' THEN cc.stringvalue_short END) AS mem_size
FROM POLESTAR.cmm_resource c
LEFT JOIN POLESTAR.core_config_prop cc
       ON cc.configuration_id = c.resource_conf_id
      AND cc.name = 'TotalSize'
LEFT JOIN POLESTAR.cmm_metric_stat_m s
       ON s.resource_id = c.id
      AND s.definition_name = 'Utilization'
      AND s.stat_date = :prev_month
WHERE c.resource_type IN ('server.Server', 'server.Cpus', 'server.Memory')
  AND c.dtime IS NULL
GROUP BY COALESCE(c.platform_resource_id, c.id)
HAVING MAX(CASE WHEN c.resource_type = 'server.Server' THEN 1 ELSE 0 END) = 1
FETCH FIRST 10000 ROWS ONLY
