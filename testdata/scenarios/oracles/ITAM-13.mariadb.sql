-- 오라클 정본 ITAM-13 · MariaDB(itam) — plans/135 W0 (ITAM 질의 벤치 · 하네스 시나리오 아님)
-- 대상 시나리오: ITAM-13 「자산관리에서 취득금액 제일 큰 서버 3대랑 금액 보여줘」
-- 정답: 취득금액 상위 3 — 판정기가 전 행에서 상위 N 을 다시 고른다(동점 흡수)
-- compare: argmax(top 3 · 키 호스트명 · 값 취득금액) · 금액은 행별 값을 기록하지 않는다(plans/135 §3.5.6)
-- 권장 spec: {id: ITAM-13, compare: argmax, top: 3, key: [[sevrHostName, …]], value: [acqsiAmt, …], db_ids: [itam]}
-- 검수: 미실행
-- 근거: testdata/itam/generate_init.py `_base_main` acqsiAmt(행 번호 증가)
SELECT sevrHostName, acqsiAmt
FROM TCDMSIF80
ORDER BY acqsiAmt DESC
LIMIT 100
