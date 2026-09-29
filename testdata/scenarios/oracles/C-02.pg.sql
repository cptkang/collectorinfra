-- 오라클 정본 C-02 · PostgreSQL(polestar_cm_gp · polestar_cm_yd · 로컬 polestar) — plans/122 O-3
-- 대상 시나리오: C-02 「지난 3개월간 전체 서버별 월간 CPU, 메모리, 파일시스템, 디스크 IO 성능 통계를 조회해줘」
-- 정답: (서버, 월) 1행 — 직전 완결 3개월(:month_minus_3 ~ :month_minus_1 · §10.3.1 「지난 N개월」)에 통계가 있는 조합마다
--   CPU·메모리·파일시스템(server.FileSystems) 사용률과 디스크 IO(server.Disks · MaxIORate)의 평균·최대.
--   키 열(서버·월)은 뺐다 — 월 표기가 시스템마다 갈린다('202606' · '2026-06-01'). 값으로 대응하는 rowset 이라 행 수(서버×월)와
--   열 값 멀티셋이 맞으면 월 범위도 맞은 것이다. 최소값 열은 판정하지 않는다(요청 「성능 통계」의 필수 열로 보지 않았다).
-- compare: rowset · tol 0.01 · DB 별(`_source_db`)
-- 권장 spec: {id: C-02, compare: rowset, tol: 0.01, db_ids: [polestar_b0, polestar_cm_gp, polestar_cm_yd]}
-- 검수: PG: 로컬 샌드박스 실행 확인 2026-09-29 (polestar 5434 · MCP 9099 · SQL 오류 0 · 앵커 2026-09-29) — 8행(6·7월 × 4대) · 앵커 2026-08-15 에서 12행(5~7월)
-- 근거: config/db_profiles/polestar_cm_gp.yaml:542-592(월간 성능 통계 · 월별 분해는 INNER JOIN — :335-337)
SELECT
    ROUND(AVG(CASE WHEN r.resource_type = 'server.Cpus' AND s.definition_name = 'Utilization' AND s.avg_val BETWEEN 0 AND 1000 THEN s.avg_val END)::numeric, 2) AS cpu_avg,
    ROUND(MAX(CASE WHEN r.resource_type = 'server.Cpus' AND s.definition_name = 'Utilization' AND s.max_val BETWEEN 0 AND 1000 THEN s.max_val END)::numeric, 2) AS cpu_max,
    ROUND(AVG(CASE WHEN r.resource_type = 'server.Memory' AND s.definition_name = 'Utilization' AND s.avg_val BETWEEN 0 AND 1000 THEN s.avg_val END)::numeric, 2) AS mem_avg,
    ROUND(MAX(CASE WHEN r.resource_type = 'server.Memory' AND s.definition_name = 'Utilization' AND s.max_val BETWEEN 0 AND 1000 THEN s.max_val END)::numeric, 2) AS mem_max,
    ROUND(AVG(CASE WHEN r.resource_type = 'server.FileSystems' AND s.definition_name = 'Utilization' AND s.avg_val BETWEEN 0 AND 1000 THEN s.avg_val END)::numeric, 2) AS fs_avg,
    ROUND(MAX(CASE WHEN r.resource_type = 'server.FileSystems' AND s.definition_name = 'Utilization' AND s.max_val BETWEEN 0 AND 1000 THEN s.max_val END)::numeric, 2) AS fs_max,
    ROUND(AVG(CASE WHEN r.resource_type = 'server.Disks' AND s.definition_name = 'MaxIORate' THEN s.avg_val END)::numeric, 2) AS io_avg,
    ROUND(MAX(CASE WHEN r.resource_type = 'server.Disks' AND s.definition_name = 'MaxIORate' THEN s.max_val END)::numeric, 2) AS io_max
FROM polestar.cmm_resource r
JOIN polestar.cmm_resource svr
  ON COALESCE(svr.platform_resource_id, svr.id) = r.platform_resource_id
 AND svr.resource_type = 'server.Server'
 AND svr.dtime IS NULL
JOIN polestar.cmm_metric_stat_m s
  ON s.resource_id = r.id
WHERE r.resource_type IN ('server.Cpus', 'server.Memory', 'server.FileSystems', 'server.Disks')
  AND r.dtime IS NULL
  AND s.definition_name IN ('Utilization', 'MaxIORate')
  AND s.stat_date BETWEEN :month_minus_3 AND :month_minus_1
GROUP BY svr.id, s.stat_date
LIMIT 10000
