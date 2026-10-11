-- 오라클 정본 ITAM-202 · MariaDB(itam 폐쇄망) — plans/150 W9 6회차 신규 세트(scenarios.closed.r6.yaml · 초안 · 사용자 검토 대상)
-- 대상 시나리오: ITAM-202 「유지보수 계약이 이미 끝난 서버가 몇 대야?」
-- 정답: tcdmsif80 유지보수계약종료년월일(문자형 YYYYMMDD)이 앵커 날짜 전인 서버 수 1행 — 빈 문자열·NULL 제외 · tcdmsif72 조인 없음(마트 단독)
--   서버 = 서로 다른 서버호스트명(keyset 오라클 108·110과 같은 서버 식별) · 「해지」 칸은 보지 않는다(질문이 종료일만 묻는다 — 검토 대상)
-- compare: value(n) · 시스템이 목록으로 답하면 count(행 수) — 시나리오 count_rows_ok(ITAM-107과 같은 모양)
-- 권장 spec: {id: ITAM-202, compare: value, value: [n, 서버수, …], db_ids: [itam]} + count_rows_ok: true
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 --check-oracle 미실행 — 내부망에서 0행·오류면 시나리오를 observe로 되돌린다
-- 근거: 정의 tcdmsif80 manages(유지보수 계약 만료) · 과거 경계(종료일 < 오늘) — ITAM-108(이번 분기)·201(다음 달)과 짝
SELECT COUNT(DISTINCT `서버호스트명`) AS n
FROM `tcdmsif80`
WHERE `유지보수계약종료년월일` < DATE_FORMAT(:today, '%Y%m%d')
  AND TRIM(`유지보수계약종료년월일`) <> ''
LIMIT 1
