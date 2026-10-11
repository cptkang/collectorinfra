-- 탐침 ITAM-150-P04 · MariaDB(itam 폐쇄망) — plans/150 W7 연결 위치 탐침 (오라클 디렉터리 규칙만 빌린다 · 정답 SQL 아님)
-- 대상 시나리오: ITAM-150-P04 (testdata/itam_bench/scenarios.closed.probe.yaml — --check-oracle --env closed --scenarios 전용 · LLM 0)
-- 묻는 것: 서비스 핵심어 「통합인증」 서버(101 조건 — 그룹경로내용·구성항목설명내용 부분 일치) 중 지원 종료 tcdmsif79와 세 칸으로 이어지는 서버 · 날짜 조건 없음 — 행 = 이어진 서버 수
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 미실행
-- 출력: 행 수만(값 0 · --check-oracle) — 0행도 정보다(정답 오라클이 아니다 · 판정에 쓰지 않는다)
-- 근거: results/itam_bench/20261008-163344 5회차 ITAM-101(85행)·106 t2 · plans/150 §3 F5(106 t2 정답 범위 · 오라클 전환 재료)
SELECT DISTINCT s.`서버호스트명`
FROM `tcdmsif72` s
JOIN `tcdmsif79` e
  ON s.`그룹회사코드` = e.`그룹회사코드` AND s.`서버호스트명` = e.`서버호스트명`
  AND s.`IP주소내용` = e.`IP주소내용`
WHERE (s.`그룹경로내용` LIKE '%통합인증%'
    OR s.`구성항목설명내용` LIKE '%통합인증%')
LIMIT 10000
