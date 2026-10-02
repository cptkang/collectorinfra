-- 오라클 정본 C-06 · PostgreSQL(polestar_cm_gp · polestar_cm_yd · 로컬 polestar) — plans/122 O-3
-- 대상 시나리오: C-06 「이번 달 CPU 사용률」
-- 정답(②): 이번 달 1일~어제(:month_start_ymd ~ :yesterday_ymd · §10.3.1 「이번 달」 · D-201) 일 통계(stat_d)
--   서버별 CPU 사용률 평균(AVG avg_val)·최대(MAX max_val). 매월 1일은 범위가 비어 0행 — 오라클 0행은 보류다.
--   ①(당월 월 통계 행 유무 — plans/120 G-3 판정 재료)은 별도 정본 C-06-g3 이다(시스템과 비교하지 않는다).
--   서버 식별 열은 뺐다(값 대응 rowset).
-- compare: rowset · tol 0.01 · DB 별
-- 권장 spec: {id: C-06, compare: rowset, tol: 0.01, db_ids: [polestar_b0, polestar_cm_gp, polestar_cm_yd]}
--   plans/120 G-3 확정(O-5) 전에는 기준 테이블(월/일 통계) 해석이 미정이다 — 카탈로그 배선은 O-5 뒤에 한다.
-- 검수: PG: 로컬 샌드박스 실행 확인 2026-09-29 (polestar 5434 · MCP 9099 · SQL 오류 0 · 앵커 2026-09-29) — 0행: 샌드박스 stat_d 는 더미 5행(stat_date 'stat_date_1' 등)뿐이라 값 검증 불가 · SQL 구문만 확인
-- 근거: config/db_profiles/polestar_cm_gp.yaml:324-328(「이번 달」 = stat_d 1일~어제) · docs/02_decision.md D-201
SELECT
    ROUND(AVG(CASE WHEN s.avg_val BETWEEN 0 AND 1000 THEN s.avg_val END)::numeric, 2) AS cpu_avg,
    ROUND(MAX(CASE WHEN s.max_val BETWEEN 0 AND 1000 THEN s.max_val END)::numeric, 2) AS cpu_max
FROM polestar.cmm_resource r
JOIN polestar.cmm_resource svr
  ON COALESCE(svr.platform_resource_id, svr.id) = r.platform_resource_id
 AND svr.resource_type = 'server.Server'
 AND svr.dtime IS NULL
JOIN polestar.cmm_metric_stat_d s
  ON s.resource_id = r.id
WHERE r.resource_type = 'server.Cpus'
  AND r.dtime IS NULL
  AND s.definition_name = 'Utilization'
  AND s.stat_date BETWEEN :month_start_ymd AND :yesterday_ymd
GROUP BY svr.id
LIMIT 10000
