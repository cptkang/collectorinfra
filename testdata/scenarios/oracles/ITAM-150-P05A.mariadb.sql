-- 탐침 ITAM-150-P05A · MariaDB(itam 폐쇄망) — plans/150 W7 연결 위치 탐침 (오라클 디렉터리 규칙만 빌린다 · 정답 SQL 아님)
-- 대상 시나리오: ITAM-150-P05A (testdata/itam_bench/scenarios.closed.probe.yaml — --check-oracle --env closed --scenarios 전용 · LLM 0)
-- 묻는 것: 「통합인증」 서버(101 조건) 중 서버 통합 현황 tcdmsif80(서버호스트명·IP주소내용)의 담당부점명이 비지 않은 서버 — 행 = 부서 이름이 있는 서버 수
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 미실행
-- 출력: 행 수만(값 0 · --check-oracle) — 0행도 정보다(정답 오라클이 아니다 · 판정에 쓰지 않는다)
-- 근거: results/itam_bench/20261008-163344 5회차 ITAM-102(72 담당부점코드 85/85 NULL) · plans/150 §3 F10(149 P10 대체)
SELECT DISTINCT s.`서버호스트명`
FROM `tcdmsif72` s
JOIN `tcdmsif80` h
  ON s.`서버호스트명` = h.`서버호스트명` AND s.`IP주소내용` = h.`IP주소내용`
WHERE (s.`그룹경로내용` LIKE '%통합인증%'
    OR s.`구성항목설명내용` LIKE '%통합인증%')
  AND h.`담당부점명` IS NOT NULL AND h.`담당부점명` <> ''
LIMIT 10000
