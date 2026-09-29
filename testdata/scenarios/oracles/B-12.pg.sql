-- 오라클 정본 B-12 · PostgreSQL(polestar_cm_gp · polestar_cm_yd · 로컬 polestar) — plans/122 O-3
-- 대상 시나리오: B-12 「2026년 6월 서버별 CPU 사용률 평균이 높은 상위 3대를 보여줘」
-- 정답: 2026-06 월 통계 서버별 CPU(server.Cpus · Utilization) avg_val 평균(쓰레기 값 게이트 0~1000) — 3 DB 전역 상위 3.
--   DB 마다 상위 50 후보를 낸다(전역 상위 3 · 동점 판정 재료). 절대 월이라 자리표가 없다.
-- compare: argmax · top 3 · 값 tol 0.01(반올림 2자리). 시스템 CSV 는 재정렬 전 원본이다 — 하네스가 값 열로 다시 정렬한다.
-- 권장 spec: {id: B-12, compare: argmax, top: 3, tol: 0.01, key: [[server_name, pname, 서버명, 장비명]],
--             value: [cpu_avg, avg_cpu, cpu_avg_usage, avg_cpu_usage, cpu_usage_avg, 평균], db_ids: [polestar_b0, polestar_cm_gp, polestar_cm_yd]}
--   key 별칭에 hostname 을 섞지 않는다(공동존은 name ≠ hostname — 다른 뜻의 열을 같은 키로 보면 거짓 판정).
-- 서버 식별: COALESCE(name, hostname)(D-058). 서버 행 조인은 COALESCE(platform_resource_id, id) — 운영 정본
--   `svr.id = r.platform_resource_id`와 운영 데이터에서 같고(서버 행의 platform_resource_id 는 NULL 또는 자기 id),
--   로컬 샌드박스(서버 행이 platform.server 앵커를 가리킴)에서도 성립한다.
-- 검수: PG: 로컬 샌드박스 실행 확인 2026-09-29 (polestar 5434 · MCP 9099 · SQL 오류 0 · 앵커 2026-09-29) — 4행(샌드박스 2026-06 통계 서버 4대)
-- 근거: config/db_profiles/polestar_cm_gp.yaml:515-541(D-068 용량+사용률 피벗 · 게이트) · :542-592(월간 통계 조인 구조)
SELECT
    COALESCE(svr.name, svr.hostname) AS server_name,
    ROUND(AVG(s.avg_val)::numeric, 2) AS cpu_avg
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
  AND s.stat_date = '202606'
  AND s.avg_val BETWEEN 0 AND 1000
GROUP BY svr.id, svr.name, svr.hostname
ORDER BY cpu_avg DESC
LIMIT 50
