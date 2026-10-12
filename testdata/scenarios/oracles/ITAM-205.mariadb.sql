-- 오라클 정본 ITAM-205 · MariaDB(itam 폐쇄망) — plans/150 W9 6회차 신규 세트(scenarios.closed.r6.yaml · 초안 · 사용자 검토 대상)
-- 대상 시나리오: ITAM-205 「올해 안에 하드웨어 지원이 끝나는 서버 있어?」
-- 정답: tcdmsif79 하드웨어지원종료년월일(문자형 YYYYMMDD)이 앵커 날짜 ~ 앵커 연도 12월 31일 안인 서버 — 하드웨어 단독(소프트웨어 칸 OR 없음)
-- compare: keyset(서버호스트명 — 폐쇄망 정책 identifier · 키 값은 run 일관 가짜 값으로만 남는다)
-- 권장 spec: {id: ITAM-205, compare: keyset, key: [[서버호스트명, sevrHostName, …]], db_ids: [itam]}
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 --check-oracle 미실행 — 내부망에서 0행·오류면 시나리오를 observe로 되돌린다
-- 근거: 정의 tcdmsif79 manages(하드웨어/소프트웨어 지원 종료일) · ITAM-109(6개월 · HW/SW OR)의 단독 칸·연말 경계 변형
SELECT DISTINCT `서버호스트명`
FROM `tcdmsif79`
WHERE `하드웨어지원종료년월일`
  BETWEEN DATE_FORMAT(:today, '%Y%m%d')
      AND DATE_FORMAT(:today, '%Y1231')
ORDER BY `서버호스트명`
LIMIT 10000
