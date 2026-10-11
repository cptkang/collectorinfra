-- 오라클 정본 ITAM-207 · MariaDB(itam 폐쇄망) — plans/150 W9 6회차 신규 세트(scenarios.closed.r6.yaml · 초안 · 사용자 검토 대상)
-- 대상 시나리오: ITAM-207 「자산관리 기준으로 설치 지역별 서버 대수 알려줘」
-- 정답: tcdmsif72 설치지역내용별 행 수 — 결과 행 = 설치 지역 그룹(NULL·빈 문자열 그룹 제외) · 칸은 tcdmsif80에도 있다(허용 경로 — 그룹 수만 대조)
-- NULL 처리: NULL·빈 문자열 그룹을 뺀다(plans/150 검증 권고 — count 기준 통일 · 218과 같음). 5회차 112·116 선례(NULL 그룹 포함)와 다르다
--   미기재는 답이 될 「그룹」이 아니다 · 시스템이 NULL 그룹을 따로 내면 그룹 수 +1로 fail할 수 있다 — 내부망 --check-oracle 때 NULL 그룹 유무를 함께 본다
-- compare: count(그룹 수) — 설치지역내용은 키로 쓰지 않는다 · 건수 열을 n으로 짓지 않는다
-- 권장 spec: {id: ITAM-207, compare: count, db_ids: [itam]}
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 --check-oracle 미실행 — 내부망에서 0행·오류면 시나리오를 observe로 되돌린다
-- 근거: ITAM-112·206과 같은 서버 원장 그룹 집계 모양(새 집계 축) · 정의 tcdmsif72 manages(설치 지역)
SELECT `설치지역내용`, COUNT(*) AS server_count
FROM `tcdmsif72`
WHERE `설치지역내용` IS NOT NULL AND TRIM(`설치지역내용`) <> ''
GROUP BY `설치지역내용`
ORDER BY `설치지역내용`
LIMIT 10000
