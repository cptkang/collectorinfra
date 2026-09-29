-- 오라클 정본 C-01 · PostgreSQL(polestar_cm_gp · polestar_cm_yd · 로컬 polestar) — plans/122 O-3
-- 대상 시나리오: C-01 「전체 서버들의 CPU, 메모리 용량 및 사용률(평균·최대) 리스트를 조회해줘. 사용률은 지난달 1개월 통계 기준으로.」
-- 정답: 활성 서버당 1행 — 지난달(:prev_month = 앵커 기준 직전 완결 월) CPU·메모리 사용률 평균·최대 + 메모리 용량.
--   통계 없는 서버도 행이 있다(LEFT JOIN · D-068). 서버 행이 없는 그룹(고아 자원)은 서버가 아니므로 뺀다.
--   「CPU 용량」은 해석이 갈린다(LOGICALCORE · PHYSICALCORE · MODEL) — 판정하지 않는다(열에서 뺐다).
--   서버 식별 열도 뺐다 — 값으로 대응하는 rowset 이라 열 이름·표기(name/hostname)와 무관하게 행 수와 값만 본다.
-- compare: rowset · tol 0.01 · DB 별(`_source_db`)로 행 수와 열 값 멀티셋이 맞아야 통과
-- 권장 spec: {id: C-01, compare: rowset, tol: 0.01, db_ids: [polestar_b0, polestar_cm_gp, polestar_cm_yd]}
-- 검수: PG: 로컬 샌드박스 실행 확인 2026-09-29 (polestar 5434 · MCP 9099 · SQL 오류 0 · 앵커 2026-09-29) — 54행(8월 통계 없음 → 사용률 NULL) · 앵커 2026-08-15 에서 7월 값 4대 확인
-- 근거: config/db_profiles/polestar_cm_gp.yaml:515-541(D-068 용량+사용률 피벗 · 게이트 0~1000)
SELECT
    ROUND(AVG(CASE WHEN c.resource_type = 'server.Cpus' AND s.avg_val BETWEEN 0 AND 1000 THEN s.avg_val END)::numeric, 2) AS cpu_avg,
    ROUND(MAX(CASE WHEN c.resource_type = 'server.Cpus' AND s.max_val BETWEEN 0 AND 1000 THEN s.max_val END)::numeric, 2) AS cpu_max,
    ROUND(AVG(CASE WHEN c.resource_type = 'server.Memory' AND s.avg_val BETWEEN 0 AND 1000 THEN s.avg_val END)::numeric, 2) AS mem_avg,
    ROUND(MAX(CASE WHEN c.resource_type = 'server.Memory' AND s.max_val BETWEEN 0 AND 1000 THEN s.max_val END)::numeric, 2) AS mem_max,
    MAX(CASE WHEN c.resource_type = 'server.Memory' AND cc.name = 'TotalSize' THEN cc.stringvalue_short END) AS mem_size
FROM polestar.cmm_resource c
LEFT JOIN polestar.core_config_prop cc
       ON cc.configuration_id = c.resource_conf_id
      AND cc.name = 'TotalSize'
LEFT JOIN polestar.cmm_metric_stat_m s
       ON s.resource_id = c.id
      AND s.definition_name = 'Utilization'
      AND s.stat_date = :prev_month
WHERE c.resource_type IN ('server.Server', 'server.Cpus', 'server.Memory')
  AND c.dtime IS NULL
GROUP BY COALESCE(c.platform_resource_id, c.id)
HAVING MAX(CASE WHEN c.resource_type = 'server.Server' THEN 1 ELSE 0 END) = 1
LIMIT 10000
