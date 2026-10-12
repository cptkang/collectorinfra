-- 오라클 정본 ITAM-212 · MariaDB(itam 폐쇄망) — plans/150 W9 6회차 신규 세트(scenarios.closed.r6.yaml · 초안 · 사용자 검토 대상)
-- 대상 시나리오: ITAM-212 턴2 「그 서버들 중에 유지보수 계약이 아직 남아 있는 건 몇 대야?」(턴1 「도입한 지 6년 넘은 서버 목록 보여줘」 — 관찰)
-- 정답: 턴1 범위 전체(tcdmsif80 취득년월일이 앵커 날짜 6년 전보다 앞선 서버) 중 유지보수계약종료년월일이 앵커 날짜 이후(당일 포함)인 서버 수 1행
--   턴1 결과가 21건 이상이면 표본만 기억된다 — 앞 조건을 다시 걸어 전체 범위로 세야 맞다(W1 · G-1 (a)) · 「해지」 칸은 보지 않는다(검토 대상)
--   서버 = 서로 다른 서버호스트명 · 오라클 파일 이름은 시나리오 ID(턴2 단독 오라클 — 샌드박스 ITAM-22 선례)
-- compare: value(n) · 시스템이 목록으로 답하면 count(행 수) — 시나리오 count_rows_ok
-- 권장 spec: {id: ITAM-212, compare: value, value: [n, 서버수, …], db_ids: [itam]} + count_rows_ok: true
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 --check-oracle 미실행 — 내부망에서 0행·오류면 시나리오를 observe로 되돌린다
-- 근거: 정의 tcdmsif80 manages(취득일·유지보수 계약 만료 — 한 행에 모음) · ITAM-110(취득)·108(계약 종료) 칸의 조합
SELECT COUNT(DISTINCT `서버호스트명`) AS n
FROM `tcdmsif80`
WHERE `취득년월일` < DATE_FORMAT(DATE_SUB(:today, INTERVAL 6 YEAR), '%Y%m%d')
  AND TRIM(`취득년월일`) <> ''
  AND `유지보수계약종료년월일` >= DATE_FORMAT(:today, '%Y%m%d')
LIMIT 1
