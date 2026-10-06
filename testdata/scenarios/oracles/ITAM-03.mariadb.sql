-- 오라클 정본 ITAM-03 · MariaDB(itam) — plans/135 W0 (ITAM 질의 벤치 · 하네스 시나리오 아님)
-- 대상 시나리오: ITAM-03 「자산관리에서 svr-web-01 서버 정보 알려줘」
-- 정답: 호스트 키 ① 정확 일치 1행
-- compare: keyset(호스트명)
-- 권장 spec: {id: ITAM-03, compare: keyset, key: [[sevrHostName, 호스트명, …]], db_ids: [itam]}
-- 검수: 미실행
-- 근거: testdata/itam/generate_init.py `_DESIGN` svr-web-01(호스트 키 ①)
SELECT sevrHostName
FROM TCDMSIF80
WHERE sevrHostName = 'svr-web-01'
ORDER BY sevrHostName
LIMIT 10
