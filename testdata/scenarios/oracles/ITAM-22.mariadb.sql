-- 오라클 정본 ITAM-22 · MariaDB(itam) — plans/135 W0 (ITAM 질의 벤치 · 하네스 시나리오 아님)
-- 대상 시나리오: ITAM-22 2턴 「그 서버들 지원 종료일도 알려줘」(1턴 = 이번 분기 유지보수 계약 만료 서버)
-- 정답: 1턴 서버 중 지원 종료일 짝(3열 복합 키)이 있는 서버 · 기대: svr-db-01·02·03
-- compare: keyset(호스트명)
-- 권장 spec: {id: ITAM-22, compare: keyset, key: [[sevrHostName, 호스트명, …]], db_ids: [itam]}
-- 검수: 미실행
-- 근거: testdata/itam/README.md 로컬 오라클 ② + ①의 조인
SELECT a.sevrHostName
FROM TCDMSIF80 a
JOIN TCDMSIF79 b
  ON a.groupCoCd = b.groupCoCd AND a.sevrHostName = b.sevrHostName AND a.iPCtnt = b.iPCtnt
WHERE a.manmenCtrcEndYmd
  BETWEEN DATE_FORMAT(MAKEDATE(YEAR(:today), 1) + INTERVAL (QUARTER(:today) - 1) QUARTER, '%Y%m%d')
      AND DATE_FORMAT(MAKEDATE(YEAR(:today), 1) + INTERVAL QUARTER(:today) QUARTER - INTERVAL 1 DAY, '%Y%m%d')
ORDER BY a.sevrHostName
LIMIT 100
