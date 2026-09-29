-- 오라클 정본 H-17 · DB2(polestar_b0) — plans/122 O-3
-- 대상 시나리오: H-17 「은행존 2026년 6월 기준 채워줘」(CPU_양식.xlsx)
-- 결과: 은행존 활성 서버 수(n) — 참고 값. 행 정의는 G-10 대기(PG 정본 머리 주석 참조) · 카탈로그 배선 금지.
-- 검수: DB2: 미검수 — 폐쇄망 1회 사람 검수 후 동결(O-3). 부하: cmm_resource 1회 집계
-- 근거: plans/122 §9.1 H-17 행 · §9.4 · config/db_profiles/polestar_b0.yaml:410-417
SELECT COUNT(*) AS n
FROM POLESTAR.cmm_resource r
WHERE r.resource_type = 'server.Server'
  AND r.dtime IS NULL
FETCH FIRST 1 ROWS ONLY
