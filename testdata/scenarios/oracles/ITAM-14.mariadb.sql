-- 오라클 정본 ITAM-14 · MariaDB(itam) — plans/135 W0 (ITAM 질의 벤치 · 하네스 시나리오 아님)
-- 대상 시나리오: ITAM-14 「전체 서버 취득금액 합계 얼마야?」
-- 정답: 취득금액 합계 1행
-- compare: value(스칼라 · 시스템 1행 1열이면 열 이름 무관 — plans/135 §3.3 판정 어댑터)
-- 권장 spec: {id: ITAM-14, compare: value, value: [total, 합계, …], db_ids: [itam]}
-- 검수: 미실행
-- 근거: testdata/itam/generate_init.py `_base_main` acqsiAmt
SELECT SUM(acqsiAmt) AS total
FROM TCDMSIF80
LIMIT 1
