-- 오라클 정본 C-09 · PostgreSQL(polestar_cm_gp · polestar_cm_yd · 로컬 polestar) — plans/122 O-3
-- 대상 시나리오: C-09 「2026년 5월과 비교해서 6월에 CPU 사용률 평균이 가장 많이 상승한 서버와 상승폭을 알려줘」
-- 정답: 두 달 모두 통계가 있는 서버의 CPU 평균 차(6월 − 5월 · %p) 최댓값 서버와 그 차. DB 마다 상위 50 후보.
-- compare: argmax · top 1 · 값 = increase. 시스템이 두 평균을 각각 반올림한 뒤 빼면 최대 0.01 어긋난다 → tol 0.02 권장.
-- 권장 spec: {id: C-09, compare: argmax, top: 1, tol: 0.02, key: [[server_name, pname, 서버명, 장비명]],
--             value: [increase, 상승폭, cpu_increase, avg_increase, diff, delta, increase_amount], db_ids: [polestar_b0, polestar_cm_gp, polestar_cm_yd]}
-- 검수: PG: 로컬 샌드박스 실행 확인 2026-09-29 (polestar 5434 · MCP 9099 · SQL 오류 0 · 앵커 2026-09-29) — 4행(최대 상승 SV-WEB-001 +7.6)
-- 근거: config/db_profiles/polestar_cm_gp.yaml:542-592(월간 통계 조인 구조 · 게이트) · 서버 조인은 B-12 머리 주석 참조
SELECT
    COALESCE(svr.name, svr.hostname) AS server_name,
    ROUND(AVG(CASE WHEN s.stat_date = '202605' THEN s.avg_val END)::numeric, 2) AS cpu_avg_202605,
    ROUND(AVG(CASE WHEN s.stat_date = '202606' THEN s.avg_val END)::numeric, 2) AS cpu_avg_202606,
    ROUND((AVG(CASE WHEN s.stat_date = '202606' THEN s.avg_val END)
         - AVG(CASE WHEN s.stat_date = '202605' THEN s.avg_val END))::numeric, 2) AS increase
FROM polestar.cmm_resource r
JOIN polestar.cmm_resource svr
  ON COALESCE(svr.platform_resource_id, svr.id) = r.platform_resource_id
 AND svr.resource_type = 'server.Server'
 AND svr.dtime IS NULL
JOIN polestar.cmm_metric_stat_m s
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
LIMIT 50
