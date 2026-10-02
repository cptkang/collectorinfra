-- 오라클 정본 H-04 · PostgreSQL(로컬 polestar 검증용 — H-04 의 대상은 은행존 DB2 하나다) — plans/122 O-3
-- 대상 시나리오: H-04 「은행존 채워줘」(adhoc_server_info.xlsx — 서버명·호스트명·IP·OS버전·메모리용량·비고)
-- 정답: 활성 서버의 (서버명, 호스트명) 쌍 집합. 산출 xlsx 의 (서버명, 호스트명) 쌍이 전부 이 집합에 속해야 한다.
--   「두 열이 다르다」만 보면 name = hostname 인 정상 서버에서 오탐한다(D-148 오매핑 = 서버명 열에 호스트명이 들어간 경우).
--   서버명은 COALESCE(name, hostname)(D-058).
-- compare: keyset · match subset(산출 ⊆ 오라클) · 시스템 쪽은 폼필 산출물(xlsx)의 두 열을 result 모양으로 넘긴다
-- 권장 spec: {id: H-04, compare: keyset, match: subset, key: [[server_name, 서버명], [hostname, 호스트명]], db_ids: [polestar_b0]}
-- 검수: PG: 로컬 샌드박스 실행 확인 2026-09-29 (polestar 5434 · MCP 9099 · SQL 오류 0 · 앵커 2026-09-29) — 54쌍
-- 근거: config/db_profiles/polestar_cm_gp.yaml:219-224(D-058) · docs/02_decision.md D-148
SELECT COALESCE(r.name, r.hostname) AS server_name, r.hostname
FROM polestar.cmm_resource r
WHERE r.resource_type = 'server.Server'
  AND r.dtime IS NULL
LIMIT 10000
