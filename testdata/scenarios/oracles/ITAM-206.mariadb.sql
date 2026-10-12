-- 오라클 정본 ITAM-206 · MariaDB(itam 폐쇄망) — plans/150 W9 6회차 신규 세트(scenarios.closed.r6.yaml · 초안 · 사용자 검토 대상)
-- 대상 시나리오: ITAM-206 「운영체제 버전별로 서버 몇 대씩인지 보여줘」
-- 정답: tcdmsif72 운영체제버전내용별 행 수 — 결과 행 = 운영체제 버전 그룹(NULL·빈 문자열 그룹 제외) · 테이블은 K2 k2-server-count-by-os(tcdmsif72)와 맞췄다
-- NULL 처리: NULL·빈 문자열 그룹을 뺀다(plans/150 검증 권고 — count 기준 통일 · 218과 같음). 5회차 112·116 선례(NULL 그룹 포함)와 다르다
--   미기재는 답이 될 「그룹」이 아니다 · 시스템이 NULL 그룹을 따로 내면 그룹 수 +1로 fail할 수 있다 — 내부망 --check-oracle 때 NULL 그룹 유무를 함께 본다
-- compare: count(그룹 수) — 운영체제버전내용은 폐쇄망 정책 밖(unclassified)이라 키로 쓰지 않는다 · 건수 열을 n으로 짓지 않는다
-- 권장 spec: {id: ITAM-206, compare: count, db_ids: [itam]}
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 --check-oracle 미실행 — 내부망에서 0행·오류면 시나리오를 observe로 되돌린다
-- 근거: ITAM-112(운영체제타입내용별) 오라클 모양의 집계 축 변경 · K2 k2-server-count-by-os description 「버전은 운영체제버전내용 칸」
SELECT `운영체제버전내용`, COUNT(*) AS server_count
FROM `tcdmsif72`
WHERE `운영체제버전내용` IS NOT NULL AND TRIM(`운영체제버전내용`) <> ''
GROUP BY `운영체제버전내용`
ORDER BY `운영체제버전내용`
LIMIT 10000
