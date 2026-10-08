-- 탐침 ITAM-146-P02 · MariaDB(itam 폐쇄망) — plans/146 W5 (2) 연결 위치 탐침 (오라클 디렉터리 규칙만 빌린다 · 정답 SQL 아님)
-- 대상 시나리오: ITAM-146-P02 (testdata/itam_bench/scenarios.closed.probe.yaml — --check-oracle --env closed --scenarios 전용 · LLM 0)
-- 묻는 것: 서비스 핵심어 「통합인증」이 서버 원장 tcdmsif72 그룹경로내용에 부분 일치로 있나 — 행 = 일치한 서버 수
-- 검수: 모의 DB(127.0.0.1:3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 미실행
-- 출력: 행 수만(값 0 · --check-oracle) — 0행도 정보다(정답 오라클이 아니다 · 판정에 쓰지 않는다)
-- 근거: results/itam_bench/20261007-174622 3회차 ITAM-101·106 t1(질문 문구 전체 부분 일치 · 0행) · 정의 tcdmsif72 notes(서비스 연결 후보 칸)
SELECT DISTINCT s.`서버호스트명`
FROM `tcdmsif72` s
WHERE s.`그룹경로내용` LIKE '%통합인증%'
LIMIT 10000
