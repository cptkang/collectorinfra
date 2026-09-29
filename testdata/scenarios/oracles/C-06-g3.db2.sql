-- 오라클 정본 C-06-g3 · DB2(polestar_b0) — plans/122 O-3 · O-5
-- 대상: C-06 ① — plans/120 G-3 판정 재료(시스템과 비교하지 않는다). 열 뜻은 PG 정본과 같다.
-- 검수: DB2: 미검수 — 폐쇄망 1회 사람 검수 후 동결(O-3).
-- 부하 주의: stat_date 인덱스 유무 미확인 — 없으면 stat_m 1회 전체 스캔이다(집계 1행 · 조인 없음). 검수 때 실행 시간을 적는다.
-- 근거: plans/122 §9.1 C-06 행
SELECT
    COALESCE(SUM(CASE WHEN s.stat_date = :anchor_month THEN 1 ELSE 0 END), 0) AS n,
    COALESCE(SUM(CASE WHEN s.stat_date = :prev_month THEN 1 ELSE 0 END), 0) AS n_prev_month
FROM POLESTAR.cmm_metric_stat_m s
WHERE s.stat_date IN (:anchor_month, :prev_month)
FETCH FIRST 1 ROWS ONLY
