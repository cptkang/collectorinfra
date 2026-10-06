-- 오라클 정본 ITAM-07 · MariaDB(itam) — plans/135 W0 (ITAM 질의 벤치 · 하네스 시나리오 아님)
-- 대상 시나리오: ITAM-07 「이번 분기에 유지보수 계약이 끝나는 서버 있어?」 · ITAM-22 1턴
-- 정답: 유지보수 계약 종료일(CHAR(8) 'YYYYMMDD')이 앵커 날짜의 분기 첫날~마지막 날 안인 서버
-- compare: keyset(호스트명) · 기대(샌드박스 init 직후): svr-db-01·02·03
-- 권장 spec: {id: ITAM-07, compare: keyset, key: [[sevrHostName, 호스트명, …]], db_ids: [itam]}
-- 검수: 미실행
-- 근거: testdata/itam/README.md 로컬 오라클 ② — CURDATE() 대신 앵커 날짜(:today · 턴 송신 KST)를 쓴다
SELECT sevrHostName
FROM TCDMSIF80
WHERE manmenCtrcEndYmd
  BETWEEN DATE_FORMAT(MAKEDATE(YEAR(:today), 1) + INTERVAL (QUARTER(:today) - 1) QUARTER, '%Y%m%d')
      AND DATE_FORMAT(MAKEDATE(YEAR(:today), 1) + INTERVAL QUARTER(:today) QUARTER - INTERVAL 1 DAY, '%Y%m%d')
ORDER BY sevrHostName
LIMIT 100
