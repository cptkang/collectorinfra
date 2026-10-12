-- 오라클 정본 ITAM-227 · MariaDB(itam 폐쇄망) — plans/150 W9 6회차 신규 세트(scenarios.closed.r6.yaml · 초안 · 사용자 검토 대상)
-- 대상 시나리오: ITAM-227 「담당 부서별로 관리하는 서버가 몇 대씩인지 부서 이름으로 보여줘」
-- 정답: tcdmsif80 담당부점명별 행 수 — 결과 행 = 부서 그룹(NULL·빈 문자열 그룹 제외) · 조인 불필요 대조군(tcdmsbr01·tcdmsif72를 잇지 않는다)
-- NULL 처리: NULL·빈 문자열 그룹을 뺀다(plans/150 검증 권고 — count 기준 통일 · 218과 같음). 5회차 112·116 선례(NULL 그룹 포함)와 다르다
--   미기재는 답이 될 「그룹」이 아니다 · 시스템이 NULL 그룹을 따로 내면 그룹 수 +1로 fail할 수 있다 — 내부망 --check-oracle 때 NULL 그룹 유무를 함께 본다
-- compare: count(그룹 수) — 부서 이름은 키로 쓰지 않는다 · 건수 열을 n으로 짓지 않는다
-- 권장 spec: {id: ITAM-227, compare: count, db_ids: [itam]}
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 --check-oracle 미실행 — 내부망에서 0행·오류면 시나리오를 observe로 되돌린다
-- 근거: 정의 tcdmsif80 manages(담당자/부점을 한 행에 모음 · 담당부점명) · 2회차 102 관측(72↔br01 부서 칸 전부 NULL) — 80 단독으로 부서 이름이 나온다
SELECT `담당부점명`, COUNT(*) AS server_count
FROM `tcdmsif80`
WHERE `담당부점명` IS NOT NULL AND TRIM(`담당부점명`) <> ''
GROUP BY `담당부점명`
ORDER BY `담당부점명`
LIMIT 10000
