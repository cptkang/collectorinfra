-- 오라클 정본 ITAM-20 · MariaDB(itam) — plans/135 W0 (ITAM 질의 벤치 · 하네스 시나리오 아님)
-- 대상 시나리오: ITAM-20 「자산관리 시스템에서 운영체제별 서버 수 알려줘」
-- 정답: 운영체제별 서버 수(시드 보강 — Linux 14 · AIX 9 · Windows 7)
-- compare: rowset(열 이름 무관 · 값 멀티셋)
-- 권장 spec: {id: ITAM-20, compare: rowset, db_ids: [itam]}
-- 검수: 미실행
-- 근거: testdata/itam/generate_init.py `_boost` oSTypzCtnt
SELECT oSTypzCtnt AS os, COUNT(*) AS n
FROM TCDMSIF80
GROUP BY oSTypzCtnt
ORDER BY os
LIMIT 100
