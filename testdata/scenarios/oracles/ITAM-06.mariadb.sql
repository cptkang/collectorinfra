-- 오라클 정본 ITAM-06 · MariaDB(itam) — plans/135 W0 (ITAM 질의 벤치 · 하네스 시나리오 아님)
-- 대상 시나리오: ITAM-06 「svr-db-03 담당자가 누구야?」
-- 정답: 그 서버의 자산 행 수 1(담당자 열은 사람 정보라 키·값으로 쓰지 않는다 — plans/135 §3.5)
-- compare: count(결과 행 수)
-- 권장 spec: {id: ITAM-06, compare: count, db_ids: [itam]}
-- 검수: 미실행
-- 근거: testdata/itam/generate_init.py `_DESIGN` svr-db-03
SELECT COUNT(*) AS n
FROM TCDMSIF80
WHERE sevrHostName = 'svr-db-03'
LIMIT 1
