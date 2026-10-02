-- ============================================================================
-- 진단: 알람 유형(CPU 등) 식별 축 전수 실측 (read-only) — D-202 후속, D-05 0건 판별
-- 배경: "이번달 CPU 임계값 초과 알람"이 3존에서 0건/실패. LLM은 d.name ILIKE '%CPU%'
--   등을 시도했으나, 알람 정의명·로그 텍스트에 CPU가 실제로 어떤 표기로 들어가는지
--   미실측 상태라 유형 필터의 정답 패턴을 확정할 수 없다. 이 실측이 나와야
--   ①유형 필터 결정적 조립(assembler 확장) 또는 ②프롬프트 지식 등재를 결정한다.
-- 실행: 각 존에서 1회씩(§1~§4). 결과를 전부 회수할 것. B0는 스키마 대문자 필수.
-- ============================================================================

-- [§1] 알람 정의명 분포 — 최근 3개월 발생 알람 기준 상위 60
-- (CM-GP / CM-YD — PostgreSQL)
SELECT d.name AS def_name, COUNT(*) AS cnt
FROM polestar.cmm_alarm a
JOIN polestar.cmm_alarm_def d ON a.definition_id = d.id
WHERE a.ctime >= TIMESTAMP '2026-06-01 00:00:00'
GROUP BY d.name
ORDER BY cnt DESC NULLS LAST
LIMIT 60;

-- (B0 — DB2)
SELECT d.name AS def_name, COUNT(*) AS cnt
FROM POLESTAR.cmm_alarm a
JOIN POLESTAR.cmm_alarm_def d ON a.definition_id = d.id
WHERE a.ctime >= TIMESTAMP '2026-06-01 00:00:00'
GROUP BY d.name
ORDER BY cnt DESC NULLS LAST
FETCH FIRST 60 ROWS ONLY;

-- [§2] CPU 표기 후보 — 정의명·정의 테이블 전체에서 대소문자 무시 탐색
-- (CM — PostgreSQL)
SELECT DISTINCT d.name AS def_name
FROM polestar.cmm_alarm_def d
WHERE UPPER(d.name) LIKE '%CPU%'
   OR UPPER(d.name) LIKE '%PROCESSOR%'
LIMIT 60;

-- (B0 — DB2)
SELECT DISTINCT d.name AS def_name
FROM POLESTAR.cmm_alarm_def d
WHERE UPPER(d.name) LIKE '%CPU%'
   OR UPPER(d.name) LIKE '%PROCESSOR%'
FETCH FIRST 60 ROWS ONLY;

-- [§3] 알람이 붙는 자원 타입 분포 — 유형↔resource_type 대응 확인
-- ("CPU 알람 = res.resource_type='server.Cpus'"가 성립하는지 판별)
-- (CM — PostgreSQL)
SELECT res.resource_type, COUNT(*) AS cnt
FROM polestar.cmm_alarm a
JOIN polestar.cmm_resource res ON a.resource_id = res.id
WHERE a.ctime >= TIMESTAMP '2026-06-01 00:00:00'
GROUP BY res.resource_type
ORDER BY cnt DESC NULLS LAST
LIMIT 30;

-- (B0 — DB2)
SELECT res.resource_type, COUNT(*) AS cnt
FROM POLESTAR.cmm_alarm a
JOIN POLESTAR.cmm_resource res ON a.resource_id = res.id
WHERE a.ctime >= TIMESTAMP '2026-06-01 00:00:00'
GROUP BY res.resource_type
ORDER BY cnt DESC NULLS LAST
FETCH FIRST 30 ROWS ONLY;

-- [§4] conditionlogtext 샘플 — CPU 후보 10건 + 무관 예시 대조용 10건
-- (CM — PostgreSQL; B0는 스키마 대문자 + LIMIT → FETCH FIRST 치환)
SELECT a.conditionlogtext
FROM polestar.cmm_alarm a
WHERE UPPER(a.conditionlogtext) LIKE '%CPU%'
ORDER BY a.ctime DESC
LIMIT 10;

SELECT a.conditionlogtext
FROM polestar.cmm_alarm a
WHERE UPPER(a.conditionlogtext) NOT LIKE '%CPU%'
ORDER BY a.ctime DESC
LIMIT 10;

-- ============================================================================
-- 판정 기준
-- a. §2에서 CPU 계열 정의명 존재 + §1에서 그 정의로 실제 발생 있음
--    → 유형 필터 = definition 조인(d.name 패턴) 확정 → 결정적 조립/지식 등재
-- b. §2가 비고 §3에서 server.Cpus 타입 알람 존재
--    → 유형 필터 = res.resource_type = 'server.Cpus' 확정 (정의명 아님)
-- c. §2·§3 모두 비고 §4의 CPU 후보만 존재
--    → 유형 필터 = conditionlogtext 패턴 (표기 변형 목록 필요 — 샘플로 확정)
-- d. 전부 0건 → "이번달 CPU 알람 0건"이 실데이터 정답 — D-05는 결함 아님으로 종결
--    (이 경우 0건 응답에 조건 요약을 덧붙이는 것만 검토)
-- ============================================================================
