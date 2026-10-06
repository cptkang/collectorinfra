-- 오라클 정본 ITAM-09 · MariaDB(itam) — plans/135 W0 (ITAM 질의 벤치 · 하네스 시나리오 아님)
-- 대상 시나리오: ITAM-09 「6개월 안에 지원 종료(EOS)되는 서버 알려줘」
-- 정답: 하드웨어 또는 소프트웨어 지원 종료일이 앵커 날짜~6개월 뒤(경계 포함)인 서버 — 3열 복합 키 조인
-- compare: keyset(호스트명) · 기대(샌드박스 init 직후): svr-was-01·02·03
-- 권장 spec: {id: ITAM-09, compare: keyset, key: [[sevrHostName, 호스트명, …]], db_ids: [itam]}
-- 검수: 미실행
-- 근거: testdata/itam/README.md 로컬 오라클 ① — CURDATE() 대신 앵커 날짜(:today · :today_ymd)를 쓴다
SELECT a.sevrHostName
FROM TCDMSIF80 a
JOIN TCDMSIF79 b
  ON a.groupCoCd = b.groupCoCd AND a.sevrHostName = b.sevrHostName AND a.iPCtnt = b.iPCtnt
WHERE b.hWSportEndYmd BETWEEN :today_ymd AND DATE_FORMAT(DATE_ADD(:today, INTERVAL 6 MONTH), '%Y%m%d')
   OR b.sWSportEndYmd BETWEEN :today_ymd AND DATE_FORMAT(DATE_ADD(:today, INTERVAL 6 MONTH), '%Y%m%d')
ORDER BY a.sevrHostName
LIMIT 100
