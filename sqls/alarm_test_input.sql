-- ============================================================
-- alarm_test_input.sql
-- 알람 분석 테스트 API 입력값 추출 쿼리
-- POST /api/v1/alarm/analyze-test 의 요청 파라미터를 실제 DB에서 추출한다.
--
-- DB Engine  : PostgreSQL (Polestar 테스트 스키마)
-- 사용처     : Swagger UI > [alarm] POST /api/v1/alarm/analyze-test
--
-- AlarmTestRequest 필드 ← DB 컬럼 매핑
--   alarm_id          ← ca.id::TEXT
--   severity          ← ca.alarmseverity       (0=해소, 1=주의, 2=경고, 3=심각)
--   alarm_name        ← d.name                 (알람 정의 이름)
--   alarm_description ← d.description          (컬럼 없으면 '' 로 대체, Query 4 로 확인)
--   alarm_definition  ← md.name (없으면 d.name) (상위 마스터 정의명)
--   hostname          ← COALESCE(svr.hostname, cr.hostname)
--   resource_name     ← cr.name
--   resource_description ← cr.description
--   resource_type     ← cr.resource_type       (server.Cpus 등)
--   condition_log     ← ca.conditionlogtext
-- ============================================================


-- ============================================================
-- [Query 1] 최근 발생 알람 목록 (테스트 입력값 후보)
--
-- 목적: 최근 7일 이내 발생한 알람을 심각도 내림차순으로 조회.
--       결과 행 하나를 골라 Swagger 입력값으로 사용한다.
--
-- ctime 컬럼: TIMESTAMP (PostgreSQL 테스트 DB에서 이미 타임스탬프로 저장됨)
--   → 시간 필터: ca.ctime >= NOW() - INTERVAL 'N days'  (변환 불필요)
-- ============================================================
SELECT
    -- ── AlarmTestRequest 직접 매핑 필드 ──────────────────
    ca.id::TEXT                                         AS alarm_id,
    ca.alarmseverity                                    AS severity,
    CASE ca.alarmseverity
        WHEN 0 THEN '해소'
        WHEN 1 THEN '주의'
        WHEN 2 THEN '경고'
        WHEN 3 THEN '심각'
        ELSE ca.alarmseverity::TEXT
    END                                                 AS severity_label,
    d.name                                              AS alarm_name,
    -- ※ description 컬럼이 없으면 아래 줄을 ''::TEXT 로 교체 (Query 4 로 확인)
    COALESCE(d.description, '')                         AS alarm_description,
    -- alarm_definition: 마스터 정의명 우선, 없으면 d.name 재사용
    COALESCE(md.name, d.name)                           AS alarm_definition,
    COALESCE(svr.hostname, cr.hostname)                 AS hostname,
    cr.name                                             AS resource_name,
    COALESCE(cr.description, '')                        AS resource_description,
    cr.resource_type                                    AS resource_type,
    ca.conditionlogtext                                 AS condition_log,

    -- ── 확인용 부가 컬럼 (Swagger 입력에는 불필요) ───────
    ca.ctime                     AS alarm_time,
    COALESCE(svr.ipaddress, cr.ipaddress)               AS server_ip,
    COALESCE(svr.name, cr.name)                         AS server_name

FROM polestar.cmm_alarm ca

    -- 알람이 발생한 자원 (server.Cpus / server.Memory / server.FileSystem 등)
    JOIN polestar.cmm_resource cr
        ON ca.resource_id = cr.id
       AND cr.dtime IS NULL

    -- 상위 서버 본체 (server.Server)
    -- 서버 자체에 알람이 나면 platform_resource_id = NULL → parent_resource_id 사용
    LEFT JOIN polestar.cmm_resource svr
        ON svr.id = COALESCE(cr.platform_resource_id, cr.parent_resource_id)
       AND svr.resource_type = 'server.Server'
       AND svr.dtime IS NULL

    -- 알람 정의 (이름·설명)
    JOIN polestar.cmm_alarm_def d
        ON ca.definition_id = d.id

    -- 마스터(상위) 알람 정의 — alarm_definition 소스
    LEFT JOIN polestar.cmm_alarm_def md
        ON d.masterdefinition_id = md.id

WHERE
    cr.dtime IS NULL
    -- 최근 7일 이내 발생
    AND ca.ctime >= NOW() - INTERVAL '7 days'
    -- 해소 포함 전체 심각도 (해소 제외: ca.alarmseverity > 0)
    AND ca.alarmseverity IN (0, 1, 2, 3)

ORDER BY
    ca.alarmseverity DESC,   -- 심각 → 경고 → 주의 → 해소
    ca.ctime DESC            -- 최신 우선

LIMIT 20;


-- ============================================================
-- [Query 2] 현재 활성 알람만 조회 (CMM_ALARM_ACTIVE 필터)
--
-- 목적: 지금 발생 중인(미해소) 알람 추출.
--       실제 장애 상황 재현 테스트에 가장 적합한 케이스.
-- ============================================================
SELECT
    ca.id::TEXT                                         AS alarm_id,
    ca.alarmseverity                                    AS severity,
    CASE ca.alarmseverity
        WHEN 1 THEN '주의'
        WHEN 2 THEN '경고'
        WHEN 3 THEN '심각'
        ELSE ca.alarmseverity::TEXT
    END                                                 AS severity_label,
    d.name                                              AS alarm_name,
    COALESCE(d.description, '')                         AS alarm_description,
    COALESCE(md.name, d.name)                           AS alarm_definition,
    COALESCE(svr.hostname, cr.hostname)                 AS hostname,
    cr.name                                             AS resource_name,
    COALESCE(cr.description, '')                        AS resource_description,
    cr.resource_type                                    AS resource_type,
    ca.conditionlogtext                                 AS condition_log,
    ca.ctime                     AS alarm_time,
    COALESCE(svr.ipaddress, cr.ipaddress)               AS server_ip

FROM polestar.cmm_alarm ca

    -- 현재 활성 알람만 포함
    JOIN polestar.cmm_alarm_active aa
        ON aa.alarm_id = ca.id

    JOIN polestar.cmm_resource cr
        ON ca.resource_id = cr.id
       AND cr.dtime IS NULL

    LEFT JOIN polestar.cmm_resource svr
        ON svr.id = COALESCE(cr.platform_resource_id, cr.parent_resource_id)
       AND svr.resource_type = 'server.Server'
       AND svr.dtime IS NULL

    JOIN polestar.cmm_alarm_def d
        ON ca.definition_id = d.id

    LEFT JOIN polestar.cmm_alarm_def md
        ON d.masterdefinition_id = md.id

WHERE
    cr.dtime IS NULL
    AND ca.alarmseverity IN (1, 2, 3)   -- 해소(0) 제외

ORDER BY
    ca.alarmseverity DESC,
    ca.ctime DESC

LIMIT 10;


-- ============================================================
-- [Query 3] 알람 유형별 대표 샘플 (테스트 케이스 다양화)
--
-- 목적: alarm_name(알람 정의) 종류를 파악하고,
--       각 유형별 최신 1건을 뽑아 다양한 케이스의 테스트 데이터로 활용.
-- ============================================================
SELECT
    ca.id::TEXT                                         AS alarm_id,
    ca.alarmseverity                                    AS severity,
    d.name                                              AS alarm_name,
    cr.resource_type                                    AS resource_type,
    COALESCE(svr.hostname, cr.hostname)                 AS hostname,
    cr.name                                             AS resource_name,
    ca.conditionlogtext                                 AS condition_log,
    ca.ctime                     AS alarm_time,
    -- 같은 alarm_name으로 최근 30일간 발생한 건수 (빈도 파악용)
    COUNT(*) OVER (PARTITION BY d.name)                 AS alarm_count_by_name

FROM polestar.cmm_alarm ca

    JOIN polestar.cmm_resource cr
        ON ca.resource_id = cr.id
       AND cr.dtime IS NULL

    LEFT JOIN polestar.cmm_resource svr
        ON svr.id = COALESCE(cr.platform_resource_id, cr.parent_resource_id)
       AND svr.resource_type = 'server.Server'
       AND svr.dtime IS NULL

    JOIN polestar.cmm_alarm_def d
        ON ca.definition_id = d.id

    -- 각 alarm_name별 최신 1건만 선택 (서브쿼리)
    JOIN (
        SELECT
            d2.name          AS def_name,
            MAX(ca2.id)      AS latest_id
        FROM polestar.cmm_alarm ca2
        JOIN polestar.cmm_alarm_def d2
            ON ca2.definition_id = d2.id
        WHERE ca2.alarmseverity IN (1, 2, 3)
          AND ca2.ctime >= NOW() - INTERVAL '30 days'
        GROUP BY d2.name
    ) latest
        ON d.name = latest.def_name
       AND ca.id  = latest.latest_id

WHERE ca.alarmseverity IN (1, 2, 3)

ORDER BY
    ca.alarmseverity DESC,
    alarm_count_by_name DESC

LIMIT 30;


-- ============================================================
-- [Query 4] cmm_alarm_def 컬럼 목록 확인
--
-- 목적: alarm_description · alarm_definition 소스인
--       cmm_alarm_def.description 컬럼의 실제 존재 여부 확인.
--       없으면 Query 1/2/5 의 COALESCE(d.description, '') 를
--       ''::TEXT 로 교체해야 함.
-- ============================================================
SELECT
    column_name,
    data_type,
    is_nullable,
    character_maximum_length
FROM information_schema.columns
WHERE table_schema = 'polestar'
  AND table_name   = 'cmm_alarm_def'
ORDER BY ordinal_position;


-- ============================================================
-- [Query 5] 특정 서버의 최근 알람 이력
--
-- 목적: 특정 서버에서 발생한 알람만 추출.
--       서버 단위 테스트 케이스 구성 시 활용.
-- 사용법: 'YOUR_HOSTNAME' 을 실제 hostname 으로 교체.
-- ============================================================
SELECT
    ca.id::TEXT                                         AS alarm_id,
    ca.alarmseverity                                    AS severity,
    CASE ca.alarmseverity
        WHEN 0 THEN '해소'
        WHEN 1 THEN '주의'
        WHEN 2 THEN '경고'
        WHEN 3 THEN '심각'
        ELSE ca.alarmseverity::TEXT
    END                                                 AS severity_label,
    d.name                                              AS alarm_name,
    COALESCE(d.description, '')                         AS alarm_description,
    COALESCE(md.name, d.name)                           AS alarm_definition,
    COALESCE(svr.hostname, cr.hostname)                 AS hostname,
    cr.name                                             AS resource_name,
    COALESCE(cr.description, '')                        AS resource_description,
    cr.resource_type                                    AS resource_type,
    ca.conditionlogtext                                 AS condition_log,
    ca.ctime                     AS alarm_time

FROM polestar.cmm_alarm ca

    JOIN polestar.cmm_resource cr
        ON ca.resource_id = cr.id

    LEFT JOIN polestar.cmm_resource svr
        ON svr.id = COALESCE(cr.platform_resource_id, cr.parent_resource_id)
       AND svr.resource_type = 'server.Server'
       AND svr.dtime IS NULL

    JOIN polestar.cmm_alarm_def d
        ON ca.definition_id = d.id

    LEFT JOIN polestar.cmm_alarm_def md
        ON d.masterdefinition_id = md.id

WHERE
    COALESCE(svr.hostname, cr.hostname) = 'YOUR_HOSTNAME'   -- ← 교체
    AND ca.alarmseverity IN (1, 2, 3)
    AND ca.ctime >= NOW() - INTERVAL '30 days'

ORDER BY ca.ctime DESC

LIMIT 10;


-- ============================================================
-- [참고] Swagger 입력 JSON 형식
--
-- Query 1~3 결과의 행 하나를 아래 JSON 에 채워 Swagger 에 붙여넣기.
-- Swagger URL: http://<host>/docs  →  [alarm] POST /api/v1/alarm/analyze-test
--
-- {
--   "alarm_id":              "<alarm_id>",
--   "severity":              <severity>,           ← 정수 (1~3)
--   "alarm_name":            "<alarm_name>",
--   "alarm_description":     "<alarm_description>",
--   "alarm_definition":      "<alarm_definition>",
--   "hostname":              "<hostname>",
--   "resource_name":         "<resource_name>",
--   "resource_description":  "<resource_description>",
--   "resource_type":         "<resource_type>",   ← 예: "server.Cpus"
--   "condition_log":         "<condition_log>",
--   "source_db_id":          "polestar",           ← DB ID (polestar_cm_gp 등으로 교체)
--   "is_clear":              false,
--   "dry_run":               true,                 ← true: 미리보기만, 실제 발송 안 함
--   "send_notification":     false,
--   "channels":              null                  ← null이면 서버 설정 채널 사용
-- }
-- ============================================================
