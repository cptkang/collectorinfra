-- 오라클 정본 ITAM-203 · MariaDB(itam 폐쇄망) — plans/150 W9 6회차 신규 세트(scenarios.closed.r6.yaml · 초안 · 사용자 검토 대상)
-- 대상 시나리오: ITAM-203 「자산관리에서 취득한 지 8년 넘은 서버 몇 대인지 알려줘」
-- 정답: tcdmsif80 취득년월일(문자형 YYYYMMDD)이 앵커 날짜 8년 전보다 앞선(「넘은」 — 경계 제외) 서버 수 1행 — 빈 문자열·NULL 제외
--   서버 = 서로 다른 서버호스트명 · 대안 칸 경과년수는 오라클에 쓰지 않는다(ITAM-110 G-4와 같은 기준 칸)
-- compare: value(n) · 시스템이 목록으로 답하면 count(행 수) — 시나리오 count_rows_ok
-- 권장 spec: {id: ITAM-203, compare: value, value: [n, 서버수, …], db_ids: [itam]} + count_rows_ok: true
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 --check-oracle 미실행 — 내부망에서 0행·오류면 시나리오를 observe로 되돌린다
-- 근거: 정의 tcdmsif80 manages(취득일·경과 연수) · ITAM-110(5년 이상 · 목록)의 연수·집계 변형
SELECT COUNT(DISTINCT `서버호스트명`) AS n
FROM `tcdmsif80`
WHERE `취득년월일` < DATE_FORMAT(DATE_SUB(:today, INTERVAL 8 YEAR), '%Y%m%d')
  AND TRIM(`취득년월일`) <> ''
LIMIT 1
