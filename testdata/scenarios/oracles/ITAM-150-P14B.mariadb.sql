-- 탐침 ITAM-150-P14B · MariaDB(itam 폐쇄망) — plans/150 W7 조인 연결 탐침 (오라클 디렉터리 규칙만 빌린다 · 정답 SQL 아님)
-- 대상 시나리오: ITAM-150-P14B (testdata/itam_bench/scenarios.closed.probe.yaml — --check-oracle --env closed --scenarios 전용 · LLM 0)
-- 묻는 것: tcdmsif11 행 중 tcdmsif12와 계약번호·그룹회사코드로 이어지는 행 — 행 = 연결 수(그룹회사코드 추가)
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 미실행
-- 출력: 행 수만(값 0 · --check-oracle) — 각 행은 상수 1(값 칸 없음) · 행 수 = 건수 · 10000이면 「10000 이상」으로 읽는다 · 0행도 정보다(판정에 쓰지 않는다)
-- 근거: plans/150 §3 F14 · §5 W9 「조인 지도」 · ITAM-225 후보
SELECT 1 AS `건`
FROM `tcdmsif11` c
WHERE EXISTS (
  SELECT 1 FROM `tcdmsif12` i
  WHERE i.`계약번호` = c.`계약번호` AND i.`그룹회사코드` = c.`그룹회사코드`
)
LIMIT 10000
