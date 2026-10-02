-- 오라클 정본 C-06-g3 · PostgreSQL(polestar_cm_gp · polestar_cm_yd · 로컬 polestar) — plans/122 O-3 · O-5
-- 대상: C-06 ① — plans/120 G-3 「월 통계(stat_m)에 진행 중 월 행이 있는가」 판정 재료. 시스템 결과와 비교하지 않는다.
-- 결과: n = 앵커 월(:anchor_month · 진행 중) 행 수 · n_prev_month = 직전 완결 월(:prev_month) 행 수(적재 여부 대조군).
--   n > 0 이면 월 통계가 진행 중 월을 적재한다(G-3 = 예). n = 0 이고 n_prev_month > 0 이면 직전월까지만 적재한다.
-- 실행(O-5 · 폐쇄망 1회 · 사용자): python -m scripts.scenario.oracle C-06-g3 --db polestar_cm_gp --db polestar_cm_yd --db polestar_b0
-- 검수: PG: 로컬 샌드박스 실행 확인 2026-09-29 (polestar 5434 · MCP 9099 · SQL 오류 0 · 앵커 2026-09-29) — n=0 · n_prev_month=0 · 앵커 2026-06-15 에서 n=16 · n_prev_month=8
-- 근거: plans/122 §9.1 C-06 행 · §10.3.1 주석(anchor=data_latest 미사용 → 월초 미적재는 O-5 로 확인)
SELECT
    COALESCE(SUM(CASE WHEN s.stat_date = :anchor_month THEN 1 ELSE 0 END), 0) AS n,
    COALESCE(SUM(CASE WHEN s.stat_date = :prev_month THEN 1 ELSE 0 END), 0) AS n_prev_month
FROM polestar.cmm_metric_stat_m s
WHERE s.stat_date IN (:anchor_month, :prev_month)
LIMIT 1
