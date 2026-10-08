-- 오라클 정본 ITAM-107 · MariaDB(itam 폐쇄망) — plans/146 W5 (1) (ITAM 질의 벤치 · 하네스 시나리오 아님)
-- 대상 시나리오: ITAM-107 「자산관리 시스템에 등록된 서버 몇 대야?」
-- 정답: 서버 원장 tcdmsif72 전 행 수 1행 — 활성 거름 없음(활성 칸 코드값 근거 0 · K6) · P1 코드값 승인 뒤 개정
-- compare: value(n) · 시스템이 목록으로 답하면 count(행 수) — 시나리오 count_rows_ok(plans/135 §3.3 · ITAM-19와 같은 모양)
-- 권장 spec: {id: ITAM-107, compare: value, value: [n, 서버수, …], db_ids: [itam]} + count_rows_ok: true
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 --check-oracle 미실행 — 내부망에서 0행·오류면 시나리오를 observe로 되돌린다
-- 근거: results/itam_bench/20261007-174622 3회차 ITAM-107 실행 SQL(COUNT(*) tcdmsif72 · 1행) · plans/146 §2
SELECT COUNT(*) AS n
FROM `tcdmsif72`
LIMIT 1
