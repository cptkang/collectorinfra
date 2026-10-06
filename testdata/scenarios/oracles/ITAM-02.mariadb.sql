-- 오라클 정본 ITAM-02 · MariaDB(itam) — plans/135 W0 (ITAM 질의 벤치 · 하네스 시나리오 아님)
-- 대상 시나리오: ITAM-02 「자산관리 시스템에 등록된 서버 목록 보여줘」 · ITAM-12 「서버별 담당 부서 알려줘」
-- 정답: 서버 자산 행 수(TCDMSIF80 전 행)
-- compare: count(결과 행 수)
-- 권장 spec: {id: ITAM-02, compare: count, db_ids: [itam]}
-- 검수: 미실행
-- 근거: testdata/itam/generate_init.py `_SANDBOX_HOSTS`(30행)
SELECT COUNT(*) AS n
FROM TCDMSIF80
LIMIT 1
