-- 오라클 정본 ITAM-112 · MariaDB(itam 폐쇄망) — plans/146 W5 (1) (ITAM 질의 벤치 · 하네스 시나리오 아님)
-- 대상 시나리오: ITAM-112 「자산관리 시스템에서 운영체제별 서버 수 알려줘」
-- 정답: tcdmsif72 운영체제타입내용별 행 수 — 결과 행 = 운영체제 그룹(NULL 그룹 포함)
-- compare: count(그룹 수) — 운영체제타입내용은 폐쇄망 정책(column_policy.closed.yaml)에 없어 unclassified라 키로 쓰지 않는다(키는 general만)
--   건수 열을 n으로 짓지 않는다(n이면 count가 그 합 = 전체 서버 수로 읽힌다)
-- 권장 spec: {id: ITAM-112, compare: count, db_ids: [itam]}
-- 검수: 모의 DB(127.0.0.1:3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 --check-oracle 미실행 — 내부망에서 0행·오류면 시나리오를 observe로 되돌린다
-- 근거: results/itam_bench/20261007-174622 3회차 ITAM-112(tcdmsif72 운영체제타입내용 집계 · 5행) · plans/146 §2
SELECT `운영체제타입내용`, COUNT(*) AS server_count
FROM `tcdmsif72`
GROUP BY `운영체제타입내용`
ORDER BY `운영체제타입내용`
LIMIT 10000
