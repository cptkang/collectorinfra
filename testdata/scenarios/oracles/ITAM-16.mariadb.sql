-- 오라클 정본 ITAM-16 · MariaDB(itam) — plans/135 W0 (ITAM 질의 벤치 · 하네스 시나리오 아님)
-- 대상 시나리오: ITAM-16 「자산관리 시스템에서 호스트명이랑 IP를 '호스트(IP)' 형태로 붙여서 보여줘」
-- 정답: 서버마다 'host(ip)' 문자열 1행 — MariaDB 문자열 결합은 CONCAT( ) (`||` 는 OR 로 해석돼 0)
-- compare: rowset(값 멀티셋 · 열 이름 무관)
-- 권장 spec: {id: ITAM-16, compare: rowset, db_ids: [itam]}
-- 검수: 미실행
-- 근거: testdata/itam/README.md 리허설 기록(`||` 침묵 오답)
SELECT CONCAT(sevrHostName, '(', iPCtnt, ')') AS host_ip
FROM TCDMSIF80
ORDER BY host_ip
LIMIT 100
