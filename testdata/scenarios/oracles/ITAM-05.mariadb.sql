-- 오라클 정본 ITAM-05 · MariaDB(itam) — plans/135 W0 (ITAM 질의 벤치 · 하네스 시나리오 아님)
-- 대상 시나리오: ITAM-05 「svr-web-03 서버 시리얼 번호 알려줘」
-- 정답: FQDN 으로 저장된 호스트 키 ③(svr-web-03.synth.example)의 시리얼 번호 1건
-- compare: keyset(시리얼 번호 — 묻는 값이 시리얼이라 결과에 호스트 열이 없을 수 있다)
-- 권장 spec: {id: ITAM-05, compare: keyset, key: [[srialNoCtnt, 시리얼번호, …]], db_ids: [itam]}
-- 검수: 미실행
-- 근거: testdata/itam/generate_init.py `_DESIGN` svr-web-03(호스트 키 ③ FQDN) · `srialNoCtnt` 합성값
SELECT srialNoCtnt
FROM TCDMSIF80
WHERE sevrHostName = 'svr-web-03' OR sevrHostName LIKE 'svr-web-03.%'
ORDER BY srialNoCtnt
LIMIT 10
