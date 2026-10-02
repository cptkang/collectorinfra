-- 오라클 정본 B-01 · PostgreSQL(polestar_cm_gp · polestar_cm_yd · 로컬 polestar) — plans/122 O-3
-- 대상 시나리오: B-01 「전체 서버의 호스트명, OS종류, 벤더를 조회해줘」
-- 정답: DB 별 활성 서버 수(server.Server · dtime IS NULL). 시스템 행 수가 이보다 크면 EAV 다중값 결함이다.
-- compare: count · system: row_counts_by_db(감사 DB 별 행 수 — CSV 불필요) · DB 별로 맞아야 통과
-- 권장 spec: {id: B-01, compare: count, system: row_counts_by_db, db_ids: [polestar_b0, polestar_cm_gp, polestar_cm_yd]}
-- 검수: PG: 로컬 샌드박스 실행 확인 2026-09-29 (polestar 5434 · MCP 9099 · SQL 오류 0 · 앵커 2026-09-29) — n=54
-- 근거: config/db_profiles/polestar_cm_gp.yaml:387-394 「서버 수를 조회해줘」(yd 동일 행)
SELECT COUNT(*) AS n
FROM polestar.cmm_resource r
WHERE r.resource_type = 'server.Server'
  AND r.dtime IS NULL
LIMIT 1
