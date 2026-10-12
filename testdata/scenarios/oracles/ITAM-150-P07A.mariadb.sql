-- 탐침 ITAM-150-P07A · MariaDB(itam 폐쇄망) — plans/150 W7 연결 위치 탐침 (오라클 디렉터리 규칙만 빌린다 · 정답 SQL 아님)
-- 대상 시나리오: ITAM-150-P07A (testdata/itam_bench/scenarios.closed.probe.yaml — --check-oracle --env closed --scenarios 전용 · LLM 0)
-- 묻는 것: 서비스 핵심어 「무인공과금」가 서버 원장 tcdmsif72 그룹경로내용·구성항목설명내용에 부분 일치로 있나 — 행 = 일치한 서버 수
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 미실행
-- 출력: 행 수만(값 0 · --check-oracle) — 0행도 정보다(정답 오라클이 아니다 · 판정에 쓰지 않는다)
-- 근거: plans/150 v1.2 G-5(6회차 서비스 이름) · §5 W9 서비스 턴 0행 판별
SELECT DISTINCT s.`서버호스트명`
FROM `tcdmsif72` s
WHERE (s.`그룹경로내용` LIKE '%무인공과금%'
    OR s.`구성항목설명내용` LIKE '%무인공과금%')
LIMIT 10000
