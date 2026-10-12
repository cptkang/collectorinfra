-- 탐침 ITAM-150-P12C · MariaDB(itam 폐쇄망) — plans/150 W7 조인 연결 탐침 (오라클 디렉터리 규칙만 빌린다 · 정답 SQL 아님)
-- 대상 시나리오: ITAM-150-P12C (testdata/itam_bench/scenarios.closed.probe.yaml — --check-oracle --env closed --scenarios 전용 · LLM 0)
-- 묻는 것: 가상화 호스트 tcdmsif89 안에서 같은 호스트참조ID가 가상화관리서버ID 여럿에 걸친 것 — 행 = 걸친 참조 ID 수(0이면 관리서버 없이도 유일)
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 미실행
-- 출력: 행 수만(값 0 · --check-oracle) — 각 행은 상수 1(값 칸 없음) · 행 수 = 건수 · 10000이면 「10000 이상」으로 읽는다 · 0행도 정보다(판정에 쓰지 않는다)
-- 근거: plans/150 §3 F14 · §5 W9 「조인 지도」 · ITAM-224 후보(참조 ID가 관리서버별일 수 있음 — 추정)
SELECT 1 AS `건`
FROM (
  SELECT p.`호스트참조ID`
  FROM `tcdmsif89` p
  GROUP BY p.`호스트참조ID`
  HAVING COUNT(DISTINCT p.`가상화관리서버ID`) > 1
) k
LIMIT 10000
