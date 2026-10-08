-- 탐침 ITAM-146-P07B · MariaDB(itam 폐쇄망) — plans/146 W5 (2) 연결 위치 탐침 (오라클 디렉터리 규칙만 빌린다 · 정답 SQL 아님)
-- 대상 시나리오: ITAM-146-P07B (testdata/itam_bench/scenarios.closed.probe.yaml — --check-oracle --env closed --scenarios 전용 · LLM 0)
-- 묻는 것: 서버 통합 현황 tcdmsif80 ↔ 서버 원장 tcdmsif72(서버호스트명·IP주소내용) 행 배수 — 행 = tcdmsif80 키 중 72와 정확히 1행으로 이어지지 않는 것(0행 = 전부 1:1 · 마트 가설 지지)
-- 검수: 모의 DB(127.0.0.1:3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 미실행
-- 출력: 행 수만(값 0 · --check-oracle) — 0행도 정보다(정답 오라클이 아니다 · 판정에 쓰지 않는다)
-- 근거: 정의 tcdmsif80 related·notes(마트 성격 추정) · results/itam_bench/20261007-174622 3회차 ITAM-110(80↔72 조인) · 오라클 ITAM-108·110·111의 마트 가설
SELECT h.`서버호스트명`
FROM `tcdmsif80` h
LEFT JOIN `tcdmsif72` s
  ON h.`서버호스트명` = s.`서버호스트명` AND h.`IP주소내용` = s.`IP주소내용`
GROUP BY h.`서버호스트명`, h.`IP주소내용`
HAVING COUNT(s.`서버호스트명`) <> 1
LIMIT 10000
