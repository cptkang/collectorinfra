-- 오라클 정본 ITAM-218 · MariaDB(itam 폐쇄망) — plans/150 W9 6회차 신규 세트(scenarios.closed.r6.yaml · 초안 · 사용자 검토 대상)
-- 대상 시나리오: ITAM-218 「서버 분류 경로 중에 서버가 제일 많은 곳 알려줘」
-- 정답: tcdmsif72 그룹경로내용별 행 수의 최댓값 그룹 — 판정기가 정렬된 상위 행에서 다시 1위를 고른다(동점 흡수) · NULL 경로 그룹 제외
-- NULL 처리: NULL 경로 그룹은 뺀다 — count 선례(112·116)와 다르게 둔 이유(206·207·227도 같은 기준으로 맞춤 — plans/150 검증 권고): 질문이 「경로 중 가장 많은 곳」이라 경로 미기재(NULL)는
--   답이 될 수 있는 「곳」이 아니다 · argmax는 1위 키만 대조하므로 NULL 그룹이 1위가 아닌 한 포함 여부가 판정을 바꾸지 않는다(NULL이 1위면 fail 가능 — 내부망 --check-oracle 때 확인)
-- compare: argmax(top 1 · 키 그룹경로내용 · 값 server_count) — 키 칸은 폐쇄망 정책 identifier 등급이 전제다(plans/150 W6 · 경로 값은 run 일관 가짜 값)
-- 권장 spec: {id: ITAM-218, compare: argmax, top: 1, key: [[그룹경로내용, …]], value: [server_count, 서버수, …], db_ids: [itam]}
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 --check-oracle 미실행 — 내부망에서 0행·오류면 시나리오를 observe로 되돌린다
-- 근거: ITAM-116(분류 경로별 서버 수 · count)의 순위 변형 · 정의 tcdmsif72 key_columns(그룹경로내용)
SELECT `그룹경로내용`, COUNT(*) AS server_count
FROM `tcdmsif72`
WHERE `그룹경로내용` IS NOT NULL
GROUP BY `그룹경로내용`
ORDER BY server_count DESC
LIMIT 100
