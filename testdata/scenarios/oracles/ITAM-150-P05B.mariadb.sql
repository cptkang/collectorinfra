-- 탐침 ITAM-150-P05B · MariaDB(itam 폐쇄망) — plans/150 W7 연결 위치 탐침 (오라클 디렉터리 규칙만 빌린다 · 정답 SQL 아님)
-- 대상 시나리오: ITAM-150-P05B (testdata/itam_bench/scenarios.closed.probe.yaml — --check-oracle --env closed --scenarios 전용 · LLM 0)
-- 묻는 것: 「통합인증」 서버(101 조건)의 담당부점코드가 부점 tcdmsbr01 부점코드로 이어져 부점명이 비지 않은 서버 — 행 = 부서 이름이 있는 서버 수(72 ↔ tcdmsbr01 · P05A 대조)
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 미실행
-- 출력: 행 수만(값 0 · --check-oracle) — 0행도 정보다(정답 오라클이 아니다 · 판정에 쓰지 않는다)
-- 근거: results/itam_bench/20261008-163344 5회차 ITAM-102(부서 칸 전부 NULL) · 정의 tcdmsbr01 notes(~부점코드 → 부점명) · plans/150 §3 F10
SELECT DISTINCT s.`서버호스트명`
FROM `tcdmsif72` s
JOIN `tcdmsbr01` b
  ON s.`담당부점코드` = b.`부점코드`
WHERE (s.`그룹경로내용` LIKE '%통합인증%'
    OR s.`구성항목설명내용` LIKE '%통합인증%')
  AND b.`부점명` IS NOT NULL AND b.`부점명` <> ''
LIMIT 10000
