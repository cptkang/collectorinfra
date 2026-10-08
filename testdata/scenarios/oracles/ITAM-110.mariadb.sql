-- 오라클 정본 ITAM-110 · MariaDB(itam 폐쇄망) — plans/146 W5 (1) (ITAM 질의 벤치 · 하네스 시나리오 아님)
-- 대상 시나리오: ITAM-110 「자산관리에서 도입한 지 5년 이상 된 노후 서버 리스트 보여줘」
-- 정답: tcdmsif80 취득년월일(문자형 YYYYMMDD)이 앵커 날짜 5년 전 이하(경계 포함)인 서버 — 빈 문자열·NULL 제외
--   G-4: 기준 칸은 취득년월일(대안 경과년수 — 4회차 대조로 가린다) · tcdmsif80 마트 가설(추정)
-- compare: keyset(서버호스트명) — 키 열 unclassified라 실행 시 키 값은 건수로만 남는다
-- 권장 spec: {id: ITAM-110, compare: keyset, key: [[서버호스트명, sevrHostName, …]], db_ids: [itam]}
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 --check-oracle 미실행 — 내부망에서 0행·오류면 시나리오를 observe로 되돌린다
-- 근거: results/itam_bench/20261007-174622 3회차 ITAM-110(tcdmsif80↔tcdmsif72 · 2,220행 — 조인은 활성 칸 때문 · 80만으로 답 가능 추정) · plans/146 §2
SELECT DISTINCT `서버호스트명`
FROM `tcdmsif80`
WHERE `취득년월일` <= DATE_FORMAT(DATE_SUB(:today, INTERVAL 5 YEAR), '%Y%m%d')
  AND TRIM(`취득년월일`) <> ''
ORDER BY `서버호스트명`
LIMIT 10000
