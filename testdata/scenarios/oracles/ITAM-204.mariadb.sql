-- 오라클 정본 ITAM-204 · MariaDB(itam 폐쇄망) — plans/150 W9 6회차 신규 세트(scenarios.closed.r6.yaml · 초안 · 사용자 검토 대상)
-- 대상 시나리오: ITAM-204 「자산관리에 등록된 서버 취득금액 총합 알려줘」
-- 정답: tcdmsif80 취득금액 합계 1행 — 서버 단위 금액은 tcdmsif80(ITAM-111 G-4와 같은 해석) · 합계라 argmax가 아니다
-- compare: value(취득금액 합계) — 금액은 합계만 기록한다(plans/135 §3.5.6)
-- 권장 spec: {id: ITAM-204, compare: value, value: [total_amount, 취득금액, 합계, …], db_ids: [itam]}
-- 검수: 모의 DB(포트 3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 --check-oracle 미실행 — 내부망에서 0행·오류면 시나리오를 observe로 되돌린다
-- 근거: 정의 tcdmsif80 manages(서버 단위 취득금액) · ITAM-111(상위 3)의 집계 변형
SELECT SUM(`취득금액`) AS total_amount
FROM `tcdmsif80`
LIMIT 1
