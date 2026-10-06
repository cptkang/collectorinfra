-- 오라클 정본 ITAM-18 · MariaDB(itam) — plans/135 W0 (ITAM 질의 벤치 · 하네스 시나리오 아님)
-- 대상 시나리오: ITAM-18 「자산관리 기준으로 메모리가 제일 큰 서버 5대」
-- 정답: 메모리 용량 상위 5(30행 전부 다른 값 — 시드 보강) · 기대: svr-web-01·05 · svr-was-04·08 · svr-db-02
-- compare: argmax(top 5 · 키 호스트명 · 값 메모리 용량)
-- 권장 spec: {id: ITAM-18, compare: argmax, top: 5, key: [[sevrHostName, …]], value: [sevrMmryCapc, …], db_ids: [itam]}
-- 검수: 미실행
-- 근거: testdata/itam/generate_init.py `_boost` sevrMmryCapc
SELECT sevrHostName, sevrMmryCapc
FROM TCDMSIF80
ORDER BY sevrMmryCapc DESC
LIMIT 100
