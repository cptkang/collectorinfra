-- 탐침 ITAM-146-P01 · MariaDB(itam 폐쇄망) — plans/146 W5 (2) 연결 위치 탐침 (오라클 디렉터리 규칙만 빌린다 · 정답 SQL 아님)
-- 대상 시나리오: ITAM-146-P01 (testdata/itam_bench/scenarios.closed.probe.yaml — --check-oracle --env closed --scenarios 전용 · LLM 0)
-- 묻는 것: 서비스 핵심어(시나리오 원문 「통합인증」)가 어플리케이션 목록 tcdmsgt82의 어플리케이션명에 부분 일치로 있나 — 행 = 일치한 어플리케이션 수
-- 검수: 모의 DB(127.0.0.1:3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 미실행
-- 출력: 행 수만(값 0 · --check-oracle) — 0행도 정보다(정답 오라클이 아니다 · 판정에 쓰지 않는다)
-- 근거: results/itam_bench/20261007-174622 3회차 ITAM-104(어플리케이션명을 서버 칸에 댄 집계만 행 반환) · plans/146 §3 F3 · 정의 tcdmsgt82 notes
SELECT DISTINCT a.`어플리케이션코드`
FROM `tcdmsgt82` a
WHERE a.`어플리케이션명` LIKE '%통합인증%'
LIMIT 10000
