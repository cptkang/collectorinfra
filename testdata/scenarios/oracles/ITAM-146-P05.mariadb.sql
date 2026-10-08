-- 탐침 ITAM-146-P05 · MariaDB(itam 폐쇄망) — plans/146 W5 (2) 연결 위치 탐침 (오라클 디렉터리 규칙만 빌린다 · 정답 SQL 아님)
-- 대상 시나리오: ITAM-146-P05 (testdata/itam_bench/scenarios.closed.probe.yaml — --check-oracle --env closed --scenarios 전용 · LLM 0)
-- 묻는 것: P01에서 찾은 어플리케이션명이 서버 원장 자유 기재 칸(그룹경로내용·구성항목설명내용·용도내용)에 나타나나(두 단계 · 3회차 ITAM-104 모양) — 행 = 이어진 서버 수
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 미실행
-- 출력: 행 수만(값 0 · --check-oracle) — 0행도 정보다(정답 오라클이 아니다 · 판정에 쓰지 않는다)
-- 근거: results/itam_bench/20261007-174622 3회차 ITAM-104(어플리케이션명 → 서버 3칸 부분 일치 LEFT JOIN · 417행) · plans/146 §3 F3(두 단계 해석 후보 · 추정)
SELECT DISTINCT s.`서버호스트명`
FROM `tcdmsgt82` a
JOIN `tcdmsif72` s
  ON s.`그룹경로내용` LIKE CONCAT('%', a.`어플리케이션명`, '%')
  OR s.`구성항목설명내용` LIKE CONCAT('%', a.`어플리케이션명`, '%')
  OR s.`용도내용` LIKE CONCAT('%', a.`어플리케이션명`, '%')
WHERE a.`어플리케이션명` LIKE '%통합인증%'
LIMIT 10000
