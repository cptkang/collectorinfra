-- 탐침 ITAM-146-P06 · MariaDB(itam 폐쇄망) — plans/146 W5 (2) 연결 위치 탐침 (오라클 디렉터리 규칙만 빌린다 · 정답 SQL 아님)
-- 대상 시나리오: ITAM-146-P06 (testdata/itam_bench/scenarios.closed.probe.yaml — --check-oracle --env closed --scenarios 전용 · LLM 0)
-- 묻는 것: 서버 원장 tcdmsif72의 물품 키(물품분류번호·물품고유번호)가 자산 원장 tcdmsif41과 겹치나(조건 없음) — 행 = 이어진 서버 수
-- 검수: 모의 DB(127.0.0.1:3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 미실행
-- 출력: 행 수만(값 0 · --check-oracle) — 0행도 정보다(정답 오라클이 아니다 · 판정에 쓰지 않는다)
-- 근거: 정의 tcdmsif72·tcdmsif41 related(물품분류번호·물품고유번호 — 값 겹침 미확인) · 2회차 72↔41 0행 · results/itam_bench/20261007-174622 3회차 ITAM-108 재시도(72↔43 물품 두 칸 · 0행) · plans/146 §3 F5
SELECT DISTINCT s.`서버호스트명`
FROM `tcdmsif72` s
JOIN `tcdmsif41` m
  ON s.`물품분류번호` = m.`물품분류번호` AND s.`물품고유번호` = m.`물품고유번호`
LIMIT 10000
