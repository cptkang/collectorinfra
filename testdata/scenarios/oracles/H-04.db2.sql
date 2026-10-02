-- 오라클 정본 H-04 · DB2(polestar_b0) — plans/122 O-3
-- 대상 시나리오: H-04 「은행존 채워줘」(adhoc_server_info.xlsx)
-- 정답: 은행존 활성 서버의 (서버명, 호스트명) 쌍 집합(약 2,3xx 쌍 — 키만). 산출 xlsx 의 쌍이 전부 여기에 속해야 한다.
-- compare: keyset · match subset (PG 정본과 짝 · 별칭 동일)
-- 검수: DB2: 미검수 — 폐쇄망 1회 사람 검수 후 동결(O-3). 부하: cmm_resource 서버 행 목록(대형 스캔 아님)
-- 근거: config/db_profiles/polestar_b0.yaml:219-225(서버명 ≠ 호스트명 구분) · D-148
SELECT COALESCE(r.name, r.hostname) AS server_name, r.hostname
FROM POLESTAR.cmm_resource r
WHERE r.resource_type = 'server.Server'
  AND r.dtime IS NULL
FETCH FIRST 10000 ROWS ONLY
