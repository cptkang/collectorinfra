-- 오라클 정본 ITAM-111 · MariaDB(itam 폐쇄망) — plans/146 W5 (1) (ITAM 질의 벤치 · 하네스 시나리오 아님)
-- 대상 시나리오: ITAM-111 「자산관리에서 취득금액 제일 큰 서버 3대랑 금액 보여줘」
-- 정답: tcdmsif80 취득금액 상위 3 — 판정기가 정렬된 상위 행에서 다시 상위 N을 고른다(동점 흡수)
--   G-4: 서버 단위 금액은 tcdmsif80(가이드 g03) — 자산 원장 tcdmsif41 해석(3회차)과 갈린다
-- compare: argmax(top 3 · 키 서버호스트명 · 값 취득금액) — 샌드박스 ITAM-13과 같은 모양 · 금액은 행별 값을 기록하지 않는다(plans/135 §3.5.6)
-- 권장 spec: {id: ITAM-111, compare: argmax, top: 3, key: [[서버호스트명, sevrHostName, …]], value: [취득금액, acqsiAmt, …], db_ids: [itam]}
-- 검수: 모의 DB(127.0.0.1:3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 --check-oracle 미실행 — 내부망에서 0행·오류면 시나리오를 observe로 되돌린다
-- 근거: results/itam_bench/20261007-174622 3회차 ITAM-111(tcdmsif41 + 분류 코드 조건 · 3행) · 정의 tcdmsif80 manages(취득) · plans/146 §3 F8
SELECT `서버호스트명`, `취득금액`
FROM `tcdmsif80`
WHERE `취득금액` IS NOT NULL
ORDER BY `취득금액` DESC
LIMIT 100
