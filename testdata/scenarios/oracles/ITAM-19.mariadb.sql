-- 오라클 정본 ITAM-19 · MariaDB(itam) — plans/135 W0 (ITAM 질의 벤치 · 하네스 시나리오 아님)
-- 대상 시나리오: ITAM-19 「자산관리에서 CPU가 16개 이상인 서버 몇 대야?」
-- 정답: CPU 개수 16 이상 서버 수(시드 보강 — 18)
-- compare: value(n) · 시스템이 목록으로 답하면 count(행 수) — 시나리오 count_rows_ok(plans/135 §3.3)
-- 권장 spec: {id: ITAM-19, compare: value, value: [n, 서버수, …], db_ids: [itam]}
-- 검수: 미실행
-- 근거: testdata/itam/generate_init.py `_boost` cPUCnt
SELECT COUNT(*) AS n
FROM TCDMSIF80
WHERE cPUCnt >= 16
LIMIT 1
