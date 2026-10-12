-- 탐침 ITAM-150-P13B · MariaDB(itam 폐쇄망) — plans/150 W7 조인 연결 탐침 (오라클 디렉터리 규칙만 빌린다 · 정답 SQL 아님)
-- 대상 시나리오: ITAM-150-P13B (testdata/itam_bench/scenarios.closed.probe.yaml — --check-oracle --env closed --scenarios 전용 · LLM 0)
-- 묻는 것: tcdmsif89 행 중 tcdmsif86과 군집참조ID·가상화관리서버ID로 이어지는 행 — 행 = 연결 수(관리서버 추가)
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 미실행
-- 출력: 행 수만(값 0 · --check-oracle) — 각 행은 상수 1(값 칸 없음) · 행 수 = 건수 · 10000이면 「10000 이상」으로 읽는다 · 0행도 정보다(판정에 쓰지 않는다)
-- 근거: plans/150 §3 F14 · §5 W9 「조인 지도」 · ITAM-224 후보
SELECT 1 AS `건`
FROM `tcdmsif89` p
WHERE EXISTS (
  SELECT 1 FROM `tcdmsif86` c
  WHERE c.`군집참조ID` = p.`군집참조ID` AND c.`가상화관리서버ID` = p.`가상화관리서버ID`
)
LIMIT 10000
