-- 탐침 ITAM-150-P19A · MariaDB(itam 폐쇄망) — plans/150 W7 서버 수 기준 탐침 (오라클 디렉터리 규칙만 빌린다 · 정답 SQL 아님)
-- 대상 시나리오: ITAM-150-P19A (testdata/itam_bench/scenarios.closed.probe.yaml — --check-oracle --env closed --scenarios 전용 · LLM 0)
-- 묻는 것: 서버 통합 현황 tcdmsif80 전체 행 — 행 = COUNT(*) · P19B(고유 서버호스트명)와 같으면 80은 서버당 1행이다
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 미실행
-- 출력: 행 수만(값 0 · --check-oracle) — 각 행은 상수 1(값 칸 없음) · 행 수 = 건수 · 10000이면 「10000 이상」으로 읽는다
-- 근거: plans/150 검증 의견(2026-10-12) — r6 202·203·212 오라클은 COUNT(DISTINCT 서버호스트명), 시스템은 대개 COUNT(*) → 두 값이 같은지 확인
SELECT 1 AS `건`
FROM `tcdmsif80`
LIMIT 10000
