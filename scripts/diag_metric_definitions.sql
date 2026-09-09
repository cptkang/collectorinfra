-- ============================================================================
-- 진단: cmm_metric_stat_m의 resource_type × definition_name 전수 실측 (read-only)
-- 목적 1 (C-02): B0의 파일시스템·디스크 IO 통계가 전부 null인 원인 판별
--   - 프로필 전제(server.FileSystems+Utilization / server.Disks+MaxIORate)로
--     행이 실제 존재하는지 → 없으면 "명칭 불일치" 또는 "미수집" 확정
-- 목적 2 (C-08/D-예정): 가동률(uptime/availability) 계열 집계 지표 존재 여부 실측
--   - Availability/Uptime/UpTime 류 definition_name이 나오면 '가동률' 유사어를
--     그 지표에 등재, 없으면 결정적 안내 pre-gate로 처리 (사용자 확정 2026-09-07:
--     가동률=가용성 계열, CPU 사용률 매핑 금지)
-- 실행: 각 존에서 1회씩. 결과 3건(존×1)을 전부 회수할 것.
-- ============================================================================

-- [B0 — DB2, 스키마 한정 필수]
SELECT r.resource_type, s.definition_name, COUNT(*) AS row_cnt,
       MIN(s.stat_date) AS min_month, MAX(s.stat_date) AS max_month
FROM POLESTAR.cmm_metric_stat_m s
JOIN POLESTAR.cmm_resource r ON r.id = s.resource_id
GROUP BY r.resource_type, s.definition_name
ORDER BY r.resource_type, s.definition_name;

-- [CM-GP / CM-YD — PostgreSQL] (대조군: 정상 존의 명칭 조합 확인용)
SELECT r.resource_type, s.definition_name, COUNT(*) AS row_cnt,
       MIN(s.stat_date) AS min_month, MAX(s.stat_date) AS max_month
FROM polestar.cmm_metric_stat_m s
JOIN polestar.cmm_resource r ON r.id = s.resource_id
GROUP BY r.resource_type, s.definition_name
ORDER BY r.resource_type, s.definition_name;

-- ============================================================================
-- 판정 기준
-- (C-02) B0 결과에서:
--   a. 'server.FileSystems' + 'Utilization' 행이 있음 → 명칭 정상, 조인/데이터
--      경로 문제 → 실패 SQL 전문 확보 후 추가 진단 (formfill-diagnosis-protocol)
--   b. 유사 명칭(예: server.FileSystem 단수, definition 'UsedPercent' 등)만 있음
--      → config/db_profiles/polestar_b0.yaml few-shot·지식 명칭 교정
--   c. 파일시스템/디스크 계열 행 자체가 없음 → "B0 미수집" 각주를 프로필에 명시
--      (null이 데이터 특성임을 응답에서 설명 가능하게)
-- (C-08) 세 존 공통에서:
--   a. Availability/Uptime 계열 definition_name 존재 → 시맨틱 모델 aliases에
--      '가동률' 등재 → synonym_seeds derive/load → catalog_diff 동등성 확인
--   b. 부재 → '가동률' 결정적 안내 pre-gate (4단계에서 구현)
-- ============================================================================

-- ============================================================================
-- [추가 2026-09-08] 진단 2: cmm_metric_stat_d(일간) 존재·보관 실측 (D-201 5차)
-- 배경: "이번 달"=stat_d 당월 1일~어제 집계(D-201)가 CM은 정상인데 B0만 0건.
--   은행존은 수집 구성이 공동존과 다른 전례 2건(파일시스템 단수형만·디스크 IO 미수집)
--   — B0의 stat_d 미수집/보관 차이가 유력 가설. 세 존 모두 실행해 대조할 것.
-- ============================================================================

-- [B0 — DB2]
SELECT MIN(stat_date) AS min_d, MAX(stat_date) AS max_d, COUNT(*) AS row_cnt
FROM POLESTAR.cmm_metric_stat_d;

SELECT r.resource_type, s.definition_name, COUNT(*) AS row_cnt,
       MIN(s.stat_date) AS min_d, MAX(s.stat_date) AS max_d
FROM POLESTAR.cmm_metric_stat_d s
JOIN POLESTAR.cmm_resource r ON r.id = s.resource_id
GROUP BY r.resource_type, s.definition_name
ORDER BY r.resource_type, s.definition_name;

-- [CM-GP / CM-YD — PostgreSQL] (대조군)
SELECT MIN(stat_date) AS min_d, MAX(stat_date) AS max_d, COUNT(*) AS row_cnt
FROM polestar.cmm_metric_stat_d;

SELECT r.resource_type, s.definition_name, COUNT(*) AS row_cnt,
       MIN(s.stat_date) AS min_d, MAX(s.stat_date) AS max_d
FROM polestar.cmm_metric_stat_d s
JOIN polestar.cmm_resource r ON r.id = s.resource_id
GROUP BY r.resource_type, s.definition_name
ORDER BY r.resource_type, s.definition_name;

-- ============================================================================
-- 판정 기준 (진단 2)
--   a. B0 row_cnt=0 또는 server.Cpus 계열 부재 → "B0 stat_d 미수집" 확정
--      → b0 프로필의 이번 달 규칙을 "근사 집계 불가 — 직전월 기준 안내"로 교체
--        (+ 필요 시 미수집 안내 각주 맵에 등재. 디스크 IO와 동일 처리 계열)
--   b. B0에 데이터가 있는데 5차가 0건 → 생성 SQL(DB2 방언) 결함
--      → 해당 실행의 audit executed_sql 전문 확보 후 표현식 교정
--   c. max_d가 어제보다 오래됨(수집 지연/중단) → 보관·수집 주기 이슈로 별도 보고
-- ============================================================================
