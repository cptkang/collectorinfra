-- 탐침 ITAM-150-P06 · MariaDB(itam 폐쇄망) — plans/150 W7 연결 위치 탐침 (오라클 디렉터리 규칙만 빌린다 · 정답 SQL 아님)
-- 대상 시나리오: ITAM-150-P06 (testdata/itam_bench/scenarios.closed.probe.yaml — --check-oracle --env closed --scenarios 전용 · LLM 0)
-- 묻는 것: 스토리지 tcdmsif51 ↔ 스토리지 할당 tcdmsif52(그룹회사코드·시리얼번호) 연결(조건 없음) — 행 = 할당 행이 있는 스토리지 장비 수
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 미실행
-- 출력: 행 수만(값 0 · --check-oracle) — 0행도 정보다(정답 오라클이 아니다 · 판정에 쓰지 않는다)
-- 근거: results/itam_bench/20261008-163344 5회차 ITAM-117(51⨝52 · 핵심어 LIKE · 0행) · plans/150 §3 F6
SELECT DISTINCT d.`그룹회사코드`, d.`시리얼번호`
FROM `tcdmsif51` d
JOIN `tcdmsif52` a
  ON d.`그룹회사코드` = a.`그룹회사코드` AND d.`시리얼번호` = a.`시리얼번호`
LIMIT 10000
