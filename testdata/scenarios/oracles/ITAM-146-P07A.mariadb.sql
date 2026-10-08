-- 탐침 ITAM-146-P07A · MariaDB(itam 폐쇄망) — plans/146 W5 (2) 연결 위치 탐침 (오라클 디렉터리 규칙만 빌린다 · 정답 SQL 아님)
-- 대상 시나리오: ITAM-146-P07A (testdata/itam_bench/scenarios.closed.probe.yaml — --check-oracle --env closed --scenarios 전용 · LLM 0)
-- 묻는 것: 매핑 경유 — 서버 원장 tcdmsif72 → 매핑 tcdmsif78(서버호스트명·IP주소내용) → 자산 원장 tcdmsif41(물품 두 칸)이 이어지나 — 행 = 이어진 서버 수
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 미실행
-- 출력: 행 수만(값 0 · --check-oracle) — 0행도 정보다(정답 오라클이 아니다 · 판정에 쓰지 않는다)
-- 근거: 정의 tcdmsif78 related(tcdmsif72 서버호스트명·IP주소내용 · tcdmsif41 물품분류번호·물품고유번호) · plans/146 §3 F5(매핑 경유 후보 · 추정)
SELECT DISTINCT s.`서버호스트명`
FROM `tcdmsif72` s
JOIN `tcdmsif78` p
  ON s.`서버호스트명` = p.`서버호스트명` AND s.`IP주소내용` = p.`IP주소내용`
JOIN `tcdmsif41` m
  ON p.`물품분류번호` = m.`물품분류번호` AND p.`물품고유번호` = m.`물품고유번호`
LIMIT 10000
