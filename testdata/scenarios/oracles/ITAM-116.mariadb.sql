-- 오라클 정본 ITAM-116 · MariaDB(itam 폐쇄망) — plans/146 W5 (1) (ITAM 질의 벤치 · 하네스 시나리오 아님)
-- 대상 시나리오: ITAM-116 「자산관리 시스템에서 서버 분류 경로별 서버 수 알려줘」
-- 정답: tcdmsif72 그룹경로내용별 행 수 — 결과 행 = 분류 경로 그룹(NULL 그룹 포함)
-- compare: count(그룹 수) — 경로 값에 업무 이름이 섞일 수 있어 키로 쓰지 않는다 · 건수 열을 n으로 짓지 않는다
-- 권장 spec: {id: ITAM-116, compare: count, db_ids: [itam]}
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 --check-oracle 미실행 — 내부망에서 0행·오류면 시나리오를 observe로 되돌린다
-- 근거: results/itam_bench/20261007-174622 3회차 ITAM-116(tcdmsif72 그룹경로내용 집계 · 749행) · plans/146 §2
SELECT `그룹경로내용`, COUNT(*) AS server_count
FROM `tcdmsif72`
GROUP BY `그룹경로내용`
ORDER BY `그룹경로내용`
LIMIT 10000
