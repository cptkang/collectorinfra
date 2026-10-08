-- 탐침 ITAM-146-P09 · MariaDB(itam 폐쇄망) — plans/146 W5 (2) 연결 위치 탐침 (오라클 디렉터리 규칙만 빌린다 · 정답 SQL 아님)
-- 대상 시나리오: ITAM-146-P09 (testdata/itam_bench/scenarios.closed.probe.yaml — --check-oracle --env closed --scenarios 전용 · LLM 0)
-- 묻는 것: 가상 서버 tcdmsif92 서버호스트명이 서버 원장 tcdmsif72 서버호스트명과 이어지나 — 행 = 이어진 호스트 수
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 미실행
-- 출력: 행 수만(값 0 · --check-oracle) — 0행도 정보다(정답 오라클이 아니다 · 판정에 쓰지 않는다)
-- 근거: 정의 tcdmsif92 related(tcdmsif72 서버호스트명) · results/itam_bench/20261007-174622 3회차 ITAM-113(가상 서버 범위 해석 미확정)
SELECT DISTINCT v.`서버호스트명`
FROM `tcdmsif92` v
JOIN `tcdmsif72` s
  ON v.`서버호스트명` = s.`서버호스트명`
LIMIT 10000
