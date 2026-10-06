-- 오라클 정본 ITAM-10 · MariaDB(itam) — plans/135 W0 (ITAM 질의 벤치 · 하네스 시나리오 아님)
-- 대상 시나리오: ITAM-10 「자산관리 시스템에서 하드웨어 지원이 이미 끝난 서버는?」
-- 정답: 하드웨어 지원 종료일이 앵커 날짜보다 앞선 서버
-- compare: keyset(호스트명) · 기대(샌드박스 init 직후): svr-was-05
-- 권장 spec: {id: ITAM-10, compare: keyset, key: [[sevrHostName, 호스트명, …]], db_ids: [itam]}
-- 검수: 미실행
-- 근거: testdata/itam/generate_init.py `_DESIGN` svr-was-05(이미 종료 -1개월)
SELECT sevrHostName
FROM TCDMSIF79
WHERE hWSportEndYmd < :today_ymd
ORDER BY sevrHostName
LIMIT 100
