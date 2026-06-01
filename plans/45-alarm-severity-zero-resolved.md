# Plan 45 — 알람 심각도 0(해소) 지원

## 배경 및 문제

Polestar DB의 `CMM_ALARM.ALARMSEVERITY` 컬럼은 알람이 **해소(resolved)** 되면 값이 `0`으로 기록된다.
현재 시스템은 심각도를 1~3으로만 정의하고 있어 다음 두 가지 문제가 발생한다.

### 문제 1 — 이력 쿼리에서 해소된 알람 누락

이력 조회 템플릿(C-2, C-3, C-4, C-5)은 `CMM_ALARM_ACTIVE` JOIN 없이 `CMM_ALARM`을 직접 조회한다.
이때 innermost WHERE 절에 `CA.ALARMSEVERITY IN (1, 2, 3)` 조건이 있어
severity=0인 해소 레코드가 전부 배제된다.

결과: "지난달 발생한 모든 알람", "알람 이력 조회" 등의 사용자 요청에서
해소된 알람이 응답에 포함되지 않는다.

### 문제 2 — CASE WHEN 미지원으로 `등급` 컬럼 공백 출력

모든 템플릿의 CASE WHEN 구문은 0을 처리하지 않아 `ELSE ''`로 빠진다.
해소 레코드가 쿼리에 포함되더라도 `등급` 컬럼이 공백으로 출력된다.

---

## 심각도 정의 (개정)

| ALARMSEVERITY | 의미 | 키워드 |
|---|---|---|
| 0 | 해소 (Resolved/Cleared) | 해소, 해제, resolved, cleared, normal |
| 1 | 주의 (Info/Notice) | 주의, info, notice |
| 2 | 경고 (Warning) | 경고, warning |
| 3 | 심각 (Critical) | 심각, critical |

---

## 수정 범위

### 파일 1: `src/prompts/query_generator.py`

#### 1-A. `[필수 WHERE 조건]` 섹션 수정

쿼리 유형별로 기본 심각도 조건을 분리한다.

**현재 (모든 쿼리에 동일 적용)**:
```
- CA.ALARMSEVERITY IN (1, 2, 3)     -- 유효한 심각도만 (1=주의, 2=경고, 3=심각)
```

**변경 후**:
```
- CR.DTIME IS NULL                  -- 삭제된 리소스 제외
- [활성 알람 쿼리] CA.ALARMSEVERITY IN (1, 2, 3)
  -- CMM_ALARM_ACTIVE JOIN을 사용하는 현재 활성 알람 조회(C-1)에만 적용
  -- ALARM_ACTIVE JOIN이 이미 활성 알람만 걸러주므로 사실상 중복 조건이나 명시적 안전망으로 유지
- [이력 쿼리] CA.ALARMSEVERITY IN (0, 1, 2, 3)
  -- CMM_ALARM_ACTIVE JOIN 없는 이력 조회(C-2~C-5)에 적용
  -- 0=해소 레코드를 포함하여 완전한 발생-해소 이력을 반환
```

#### 1-B. `[심각도 매핑]` 섹션 수정

**현재**:
```
- 심각/critical/CRITICAL → ALARMSEVERITY = 3
- 경고/warning/WARNING   → ALARMSEVERITY = 2
- 주의/info/INFO/notice  → ALARMSEVERITY = 1
- 미지정 시 → IN (1, 2, 3) 전체 포함
```

**변경 후**:
```
- 심각/critical/CRITICAL              → ALARMSEVERITY = 3
- 경고/warning/WARNING                → ALARMSEVERITY = 2
- 주의/info/INFO/notice               → ALARMSEVERITY = 1
- 해소/해제/resolved/cleared/normal   → ALARMSEVERITY = 0
- 미지정 시 (활성 알람 쿼리)          → IN (1, 2, 3)
- 미지정 시 (이력 쿼리)               → IN (0, 1, 2, 3)  ← 0 추가
```

#### 1-C. CASE WHEN 구문 수정 — 5개 템플릿 모두 해당

**현재** (C-1, C-2, C-3, C-4, C-5에 반복):
```sql
CASE
    WHEN CA.ALARMSEVERITY = 1 THEN '주의'
    WHEN CA.ALARMSEVERITY = 2 THEN '경고'
    WHEN CA.ALARMSEVERITY = 3 THEN '심각'
    ELSE ''
END AS ALARMSEVERITY,
```

**변경 후**:
```sql
CASE
    WHEN CA.ALARMSEVERITY = 0 THEN '해소'
    WHEN CA.ALARMSEVERITY = 1 THEN '주의'
    WHEN CA.ALARMSEVERITY = 2 THEN '경고'
    WHEN CA.ALARMSEVERITY = 3 THEN '심각'
    ELSE ''
END AS ALARMSEVERITY,
```

> Template C-4의 집계 컬럼도 수정 필요:
> ```sql
> COUNT(CASE WHEN A.ALARMSEVERITY = '해소' THEN 1 END) AS "해소_수",
> COUNT(CASE WHEN A.ALARMSEVERITY = '심각' THEN 1 END) AS "심각_수",
> COUNT(CASE WHEN A.ALARMSEVERITY = '경고' THEN 1 END) AS "경고_수",
> COUNT(CASE WHEN A.ALARMSEVERITY = '주의' THEN 1 END) AS "주의_수",
> ```
> 또한 Template C-4의 WHERE도 `IN (0, 1, 2, 3)` 으로 변경.

#### 1-D. WHERE 조건 주석 명확화 — 이력 템플릿 각각

C-2, C-3, C-4, C-5의 innermost WHERE에서:
```sql
-- 현재
AND CA.ALARMSEVERITY IN (1, 2, 3)

-- 변경 후
AND CA.ALARMSEVERITY IN (0, 1, 2, 3)  -- 0=해소 포함 (이력 조회)
```

#### 1-E. `[현재 활성 알람 vs 알람 이력 분기]` 섹션 — 해소 설명 추가

**추가할 설명**:
```
[심각도 0(해소)과 활성/이력 분기]
- ALARMSEVERITY = 0 레코드는 알람이 해소된 시점에 기록된다.
- 현재 활성 알람(CMM_ALARM_ACTIVE JOIN) 조회 시: 해소된 알람은 ALARM_ACTIVE에 존재하지 않으므로
  severity=0 레코드는 JOIN 조건에 의해 자동 제외된다. WHERE 조건은 IN (1, 2, 3) 유지.
- 이력 조회 시: severity=0을 포함해야 발생→해소 전체 이력을 조회할 수 있다.
  WHERE 조건은 IN (0, 1, 2, 3) 사용.
- 사용자가 "해소된 알람만 조회" 요청 시 → ALARMSEVERITY = 0 단독 조건 사용
  (이 경우에도 CMM_ALARM_ACTIVE JOIN 제외)
```

---

### 파일 2: `src/routing/domain_config.py`

4개 Polestar DB 설명에서 심각도 설명 수정.

**현재** (4개 DB 공통):
```python
"알람 심각도(1=주의/2=경고/3=심각)"
```

**변경 후**:
```python
"알람 심각도(0=해소/1=주의/2=경고/3=심각)"
```

대상 라인:
- `polestar` DB (line 43)
- `polestar_b0` DB (line 61)
- `polestar_cm_gp` DB (line 78)
- `polestar_cm_yd` DB (line 96)

---

### 파일 3: `plans/44-polestar-monitoring-alert-routing.md`

plan 44의 심각도 매핑 테이블을 다음과 같이 업데이트한다.

**변경 위치**: ALARMSEVERITY 코드 정의 섹션 (lines 444-448, 1022-1028)

**현재**:
```
ALARMSEVERITY = 1 → "주의" (Info/Notice)
ALARMSEVERITY = 2 → "경고" (Warning)
ALARMSEVERITY = 3 → "심각" (Critical)
```

**변경 후**:
```
ALARMSEVERITY = 0 → "해소" (Resolved/Cleared)
ALARMSEVERITY = 1 → "주의" (Info/Notice)
ALARMSEVERITY = 2 → "경고" (Warning)
ALARMSEVERITY = 3 → "심각" (Critical)
```

---

## 변경 적용 순서

1. `plans/44-polestar-monitoring-alert-routing.md` — 문서 기준 업데이트
2. `src/routing/domain_config.py` — DB 도메인 설명 수정 (4곳)
3. `src/prompts/query_generator.py` — 프롬프트 수정
   - [필수 WHERE 조건] 섹션 (1-A)
   - [심각도 매핑] 섹션 (1-B)
   - [현재 활성 알람 vs 알람 이력 분기] 섹션 (1-E)
   - Template C-1 CASE WHEN (1-C)
   - Template C-2 WHERE + CASE WHEN (1-C, 1-D)
   - Template C-3 WHERE + CASE WHEN (1-C, 1-D)
   - Template C-4 WHERE + CASE WHEN + 집계 컬럼 (1-C, 1-D)
   - Template C-5 WHERE + CASE WHEN (1-C, 1-D)

---

## 검증 기준

| 시나리오 | 기대 결과 |
|---|---|
| "현재 발생 중인 알람 목록" | severity 1~3만 반환 (CMM_ALARM_ACTIVE JOIN으로 0 자동 제외) |
| "지난달 알람 이력" | severity 0~3 모두 반환; 등급 컬럼에 '해소' 표시 |
| "심각 알람 이력" | severity=3 반환 (해소된 심각 알람은 severity=0으로 변경된 경우 별도 레코드로 조회됨) |
| "해소된 알람 조회" | ALARMSEVERITY = 0 단독 조건으로 해소 레코드만 반환 |
| 집계 쿼리 | '해소_수' 컬럼 추가, 총_알람_수에 해소 포함 |
| domain description 확인 | LLM이 0=해소를 인식하고 자연어 응답에 반영 |

---

## 의사결정 기록 (`docs/02_decision.md` 반영 필요)

본 계획 구현 시 `docs/02_decision.md`에 다음 결정을 추가한다:

> **D-030** — ALARMSEVERITY=0은 알람 해소 상태를 나타내며, 이력 조회 쿼리에서 기본 포함 대상으로 처리한다.
> 활성 알람 조회(CMM_ALARM_ACTIVE JOIN)는 JOIN 구조상 0이 자연 배제되므로 별도 필터 불필요.
