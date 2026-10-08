-- 오라클 정본 ITAM-108 · MariaDB(itam 폐쇄망) — plans/146 W5 (1) (ITAM 질의 벤치 · 하네스 시나리오 아님)
-- 대상 시나리오: ITAM-108 「이번 분기에 유지보수 계약이 끝나는 서버 있어?」
-- 정답: tcdmsif80 유지보수계약종료년월일(문자형 YYYYMMDD)이 앵커 날짜가 속한 분기 첫날~마지막 날 안인 서버
--   tcdmsif80 마트 가설(정의 notes 「서버 단위 질문은 이 테이블 하나로」 — 추정) — 탐침 ITAM-146-P07B(80↔72 행 배수)와 함께 확인
-- compare: keyset(서버호스트명) — 키 열 unclassified라 실행 시 키 값은 건수로만 남는다
-- 권장 spec: {id: ITAM-108, compare: keyset, key: [[서버호스트명, sevrHostName, …]], db_ids: [itam]}
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 --check-oracle 미실행 — 내부망에서 0행·오류면 시나리오를 observe로 되돌린다
-- 근거: results/itam_bench/20261007-174622 3회차 ITAM-108(첫 시도 tcdmsif80 · DB 1054) · 정의 tcdmsif80 manages(유지보수 계약) · 샌드박스 정본 ITAM-07과 같은 분기 식
SELECT DISTINCT `서버호스트명`
FROM `tcdmsif80`
WHERE `유지보수계약종료년월일`
  BETWEEN DATE_FORMAT(MAKEDATE(YEAR(:today), 1) + INTERVAL (QUARTER(:today) - 1) QUARTER, '%Y%m%d')
      AND DATE_FORMAT(MAKEDATE(YEAR(:today), 1) + INTERVAL QUARTER(:today) QUARTER - INTERVAL 1 DAY, '%Y%m%d')
ORDER BY `서버호스트명`
LIMIT 10000
