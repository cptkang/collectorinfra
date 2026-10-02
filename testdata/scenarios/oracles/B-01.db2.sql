-- 오라클 정본 B-01 · DB2(polestar_b0) — plans/122 O-3
-- 대상 시나리오: B-01 「전체 서버의 호스트명, OS종류, 벤더를 조회해줘」
-- 정답: 은행존 활성 서버 수(server.Server · dtime IS NULL)
-- compare: count · system: row_counts_by_db (PG 정본과 짝)
-- 검수: DB2: 미검수 — 폐쇄망 1회 사람 검수 후 동결(O-3). 부하: cmm_resource 1회 집계(대형 스캔 아님)
-- 근거: config/db_profiles/polestar_b0.yaml:410-417 「서버 수를 조회해줘」
SELECT COUNT(*) AS n
FROM POLESTAR.cmm_resource r
WHERE r.resource_type = 'server.Server'
  AND r.dtime IS NULL
FETCH FIRST 1 ROWS ONLY
