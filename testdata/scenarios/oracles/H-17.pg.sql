-- 오라클 정본 H-17 · PostgreSQL(로컬 polestar 검증용 — H-17 의 대상은 은행존 DB2 하나다) — plans/122 O-3
-- 대상 시나리오: H-17 「은행존 2026년 6월 기준 채워줘」(CPU_양식.xlsx)
-- 결과: 활성 서버 수(n)만 낸다. **이것은 H-17 의 정답이 아니다** — 조립기 행 정의(2,328 대 2,338)는 G-10 으로
--   정답을 정하기 전까지 수동 유지다(plans/122 §9.4 · D-275 ⑩). 카탈로그 배선 금지 · 사람 판독용 참고 값.
-- compare: count(G-10 확정 뒤 행 정의가 「활성 서버 1행」으로 정해질 때만 · system: result)
-- 검수: PG: 로컬 샌드박스 실행 확인 2026-09-29 (polestar 5434 · MCP 9099 · SQL 오류 0 · 앵커 2026-09-29) — n=54
-- 근거: plans/122 §9.1 H-17 행 · §9.4
SELECT COUNT(*) AS n
FROM polestar.cmm_resource r
WHERE r.resource_type = 'server.Server'
  AND r.dtime IS NULL
LIMIT 1
