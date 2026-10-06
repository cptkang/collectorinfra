-- 오라클 정본 ITAM-04 · MariaDB(itam) — plans/135 W0 (ITAM 질의 벤치 · 하네스 시나리오 아님)
-- 대상 시나리오: ITAM-04 「SVR-WEB-02 서버 자산 정보 조회해줘」
-- 정답: 저장값이 대문자인 호스트 키 ② 1행(SVR-WEB-02)
-- compare: keyset(호스트명)
-- 권장 spec: {id: ITAM-04, compare: keyset, key: [[sevrHostName, 호스트명, …]], db_ids: [itam]}
-- 검수: 미실행
-- 근거: testdata/itam/generate_init.py `_DESIGN` svr-web-02(호스트 키 ② 대소문자만 다름)
SELECT sevrHostName
FROM TCDMSIF80
WHERE sevrHostName = 'SVR-WEB-02'
ORDER BY sevrHostName
LIMIT 10
