-- 탐침 ITAM-150-P18A · MariaDB(itam 폐쇄망) — plans/150 W7 조인 연결 탐침 (오라클 디렉터리 규칙만 빌린다 · 정답 SQL 아님)
-- 대상 시나리오: ITAM-150-P18A (testdata/itam_bench/scenarios.closed.probe.yaml — --check-oracle --env closed --scenarios 전용 · LLM 0)
-- 묻는 것: 서버 소프트웨어 tcdmsif75 고유 서버(서버호스트명·IP주소내용) 중 서버 원장 tcdmsif72와 두 칸으로 이어지는 것 — 행 = 연결 서버 수
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 미실행
-- 출력: 행 수만(값 0 · --check-oracle) — 각 행은 상수 1(값 칸 없음) · 행 수 = 건수 · 10000이면 「10000 이상」으로 읽는다 · 0행도 정보다(판정에 쓰지 않는다)
-- 근거: plans/150 §3 F14 · §5 W9 「조인 지도」 · ITAM-232 후보
SELECT 1 AS `건`
FROM (
  SELECT DISTINCT w.`서버호스트명`, w.`IP주소내용`
  FROM `tcdmsif75` w
  WHERE EXISTS (
    SELECT 1 FROM `tcdmsif72` s
    WHERE s.`서버호스트명` = w.`서버호스트명` AND s.`IP주소내용` = w.`IP주소내용`
  )
) k
LIMIT 10000
