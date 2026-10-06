-- 오라클 정본 ITAM-11 · MariaDB(itam) — plans/135 W0 (ITAM 질의 벤치 · 하네스 시나리오 아님)
-- 대상 시나리오: ITAM-11 「도입한 지 5년 이상 된 노후 서버 리스트 보여줘」
-- 정답: 경과년수 5 이상(경계 포함 · NULL 제외) — 취득일자도 경과년수와 맞춰 시드돼 어느 열로 풀어도 같다
-- compare: keyset(호스트명) · 기대: svr-db-07·08
-- 권장 spec: {id: ITAM-11, compare: keyset, key: [[sevrHostName, 호스트명, …]], db_ids: [itam]}
-- 검수: 미실행
-- 근거: testdata/itam/README.md 로컬 오라클 ③
SELECT sevrHostName
FROM TCDMSIF80
WHERE elapsNoy >= 5
ORDER BY sevrHostName
LIMIT 100
