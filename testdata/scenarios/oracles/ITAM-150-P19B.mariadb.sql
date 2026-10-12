-- 탐침 ITAM-150-P19B · MariaDB(itam 폐쇄망) — plans/150 W7 서버 수 기준 탐침 (오라클 디렉터리 규칙만 빌린다 · 정답 SQL 아님)
-- 대상 시나리오: ITAM-150-P19B (testdata/itam_bench/scenarios.closed.probe.yaml — --check-oracle --env closed --scenarios 전용 · LLM 0)
-- 묻는 것: 서버 통합 현황 tcdmsif80의 고유 서버호스트명(NULL 제외) — 행 = COUNT(DISTINCT 서버호스트명) · P19A와 다르면 서버 수 정의를 문서화한다
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 미실행
-- 출력: 행 수만(값 0 · --check-oracle) — 각 행은 상수 1(값 칸 없음) · 행 수 = 건수 · 10000이면 「10000 이상」으로 읽는다
-- 근거: plans/150 검증 의견(2026-10-12) — r6 202·203·212 오라클의 서버 식별 기준
SELECT 1 AS `건`
FROM (SELECT DISTINCT `서버호스트명` FROM `tcdmsif80` WHERE `서버호스트명` IS NOT NULL) h
LIMIT 10000
