-- 오라클 정본 C-02 · DB2(polestar_b0) — plans/122 O-3
-- 대상 시나리오: C-02 「지난 3개월간 전체 서버별 월간 CPU, 메모리, 파일시스템, 디스크 IO 성능 통계를 조회해줘」
-- 정답: 은행존 (서버, 월) 1행 — 직전 완결 3개월. 은행존은 파일시스템이 단수형(server.FileSystem)이고
--   디스크 IO(MaxIORate)를 수집하지 않는다 → io 열은 NULL(PG 정본과 열 모양을 맞춘다 · 별칭 동일).
-- compare: rowset (PG 정본과 짝)
-- 검수: DB2: 미검수 — 폐쇄망 1회 사람 검수 후 동결(O-3). 부하: 3개월 stat_m × 서버 자원(시스템 질의와 같은 규모)
-- 근거: config/db_profiles/polestar_b0.yaml:293-297(단수형 FS · IO 미수집) · :516-565(월간 통계 DB2 형)
SELECT
    CAST(ROUND(AVG(CASE WHEN r.resource_type = 'server.Cpus' AND s.avg_val BETWEEN 0 AND 1000 THEN CAST(s.avg_val AS DOUBLE) END), 2) AS DECIMAL(31, 2)) AS cpu_avg,
    CAST(ROUND(MAX(CASE WHEN r.resource_type = 'server.Cpus' AND s.max_val BETWEEN 0 AND 1000 THEN CAST(s.max_val AS DOUBLE) END), 2) AS DECIMAL(31, 2)) AS cpu_max,
    CAST(ROUND(AVG(CASE WHEN r.resource_type = 'server.Memory' AND s.avg_val BETWEEN 0 AND 1000 THEN CAST(s.avg_val AS DOUBLE) END), 2) AS DECIMAL(31, 2)) AS mem_avg,
    CAST(ROUND(MAX(CASE WHEN r.resource_type = 'server.Memory' AND s.max_val BETWEEN 0 AND 1000 THEN CAST(s.max_val AS DOUBLE) END), 2) AS DECIMAL(31, 2)) AS mem_max,
    CAST(ROUND(AVG(CASE WHEN r.resource_type = 'server.FileSystem' AND s.avg_val BETWEEN 0 AND 1000 THEN CAST(s.avg_val AS DOUBLE) END), 2) AS DECIMAL(31, 2)) AS fs_avg,
    CAST(ROUND(MAX(CASE WHEN r.resource_type = 'server.FileSystem' AND s.max_val BETWEEN 0 AND 1000 THEN CAST(s.max_val AS DOUBLE) END), 2) AS DECIMAL(31, 2)) AS fs_max,
    CAST(NULL AS DECIMAL(31, 2)) AS io_avg,
    CAST(NULL AS DECIMAL(31, 2)) AS io_max
FROM POLESTAR.cmm_resource r
JOIN POLESTAR.cmm_resource svr
  ON COALESCE(svr.platform_resource_id, svr.id) = r.platform_resource_id
 AND svr.resource_type = 'server.Server'
 AND svr.dtime IS NULL
JOIN POLESTAR.cmm_metric_stat_m s
  ON s.resource_id = r.id
WHERE r.resource_type IN ('server.Cpus', 'server.Memory', 'server.FileSystem')
  AND r.dtime IS NULL
  AND s.definition_name = 'Utilization'
  AND s.stat_date BETWEEN :month_minus_3 AND :month_minus_1
GROUP BY svr.id, s.stat_date
FETCH FIRST 10000 ROWS ONLY
