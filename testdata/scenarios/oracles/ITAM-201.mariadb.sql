-- 오라클 정본 ITAM-201 · MariaDB(itam 폐쇄망) — plans/150 W9 6회차 신규 세트(scenarios.closed.r6.yaml · 초안 · 사용자 검토 대상)
-- 대상 시나리오: ITAM-201 「다음 달 말까지 유지보수 계약이 끝나는 서버 목록 보여줘」
-- 정답: tcdmsif80 유지보수계약종료년월일(문자형 YYYYMMDD)이 앵커 날짜 ~ 다음 달 말일 안인 서버 — tcdmsif72 조인 없음(마트 단독 · W3)
--   해석(확정 2026-10-12 · 검증 권고 — 문구를 해석이 하나인 꼴로 바꿈): 「다음 달 말까지」 = 오늘부터 다음 달 마지막 날까지(이미 끝난 계약 제외)
-- compare: keyset(서버호스트명 — 폐쇄망 정책 identifier · 키 값은 run 일관 가짜 값으로만 남는다)
-- 권장 spec: {id: ITAM-201, compare: keyset, key: [[서버호스트명, sevrHostName, …]], db_ids: [itam]}
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 --check-oracle 미실행 — 내부망에서 0행·오류면 시나리오를 observe로 되돌린다
-- 근거: 정의 tcdmsif80 manages(유지보수 계약 만료 · 서버 단위 질문은 이 테이블 하나로) · 4회차 오라클 ITAM-108과 같은 칸·같은 모양(기간만 변형)
SELECT DISTINCT `서버호스트명`
FROM `tcdmsif80`
WHERE `유지보수계약종료년월일`
  BETWEEN DATE_FORMAT(:today, '%Y%m%d')
      AND DATE_FORMAT(DATE_ADD(:next_month_start, INTERVAL 1 MONTH) - INTERVAL 1 DAY, '%Y%m%d')
ORDER BY `서버호스트명`
LIMIT 10000
