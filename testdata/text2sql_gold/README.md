# Text-to-SQL EX 골드셋 (Plan 61 / E1, D-072)

폴스타 자연어→SQL 파이프라인의 **EX(Execution Accuracy = 결과집합 동치)** 를 측정하기 위한
골드 질의 데이터셋이다. `scripts/eval_text2sql.py` 러너가 이 디렉터리를 로드해 배치 채점한다.

## 파일 구성

| 파일 | 대상 DB | 엔진 | 항목 수 |
|------|---------|------|---------|
| `gp.yaml` | `polestar_cm_gp` (공동존 김포) | PostgreSQL | 15 |
| `yd.yaml` | `polestar_cm_yd` (공동존 여의도) | PostgreSQL | 6 |
| `b0.yaml` | `polestar_b0` (은행존) | DB2 | 5 |

합계 26건 (목표 20건 이상 충족).

## 항목 스키마

각 파일은 최상위 `items:` 리스트를 가지며, 항목 필드는 다음과 같다.

| 필드 | 필수 | 설명 |
|------|------|------|
| `id` | O | 고유 식별자(파일 접두사 gp/yd/b0-NNN) |
| `query` | O | 자연어 질의(한국어) |
| `db_id` | O | 대상 DB 식별자 |
| `gold_sql` | O | 정답 SQL — **SELECT/WITH 전용**(읽기전용, D-003) |
| `category` | O | `server_config` \| `performance` \| `alarm` \| `complex` \| `unhandled` |
| `coverage` | O | `inside` \| `outside` — 시맨틱 모델(트랙 C)로 결정적 처리 가능 여부 **예상값** |
| `gold_result_signature` | 선택 | `{row_count, columns}` 결과집합 시그니처(오프라인이라 row_count는 대부분 null) |
| `gold_smq` | 선택 | 트랙 C SMQ 생성 정확도 측정용 기대 중간표현(패턴 A/B/C) |
| `notes` | 선택 | 큐레이션 메모(근거·주의) |

## 큐레이션 원칙 (대표성)

Plan 61 §E1의 **대표성 원칙**을 따른다.

1. **1차 소스 = 검증된 예시 쌍**: `config/db_profiles/polestar_cm_*.yaml`의 `query_examples`
   (운영자가 직접 검증한 질문→SQL 쌍)를 골드 항목으로 정규화했다. 이들은 현행 파이프라인이
   프롬프트 예시로 참조하는 실질 정답이므로 EX 기준선으로 적합하다.
2. **2차 소스 = 실행 이력**: `sqls/act/*.sql`은 대부분 스키마 캐시/헬스체크 SQL과 테스트 노이즈
   (`SELECT 1`)라 폴스타 실질 질의가 거의 없어, 별도 항목으로 추가하지 않았다(기록만 참고).
3. **낙관 편향 방지 — 실패/미처리 질의 필수 포함**: 실행 이력만 뽑으면 "이미 잘 처리되던 질의"로
   편향되어 커버리지율이 낙관적으로 측정된다. 이를 막기 위해 **coverage=outside 항목을 5건 이상**
   포함했다:
   - `gp-013` 김포/여의도 교차 비교(멀티DB 취합+추론)
   - `gp-014` '유사한 사양' 의역·기준 미정의(재질문 유발)
   - `gp-015` 알람 이력 집계+기간창+랭킹 복합
   - `yd-005` '가동률'=사용률 의역 동의어(E5-4 임베딩 대상)
   - `yd-006` 알림계열 다단 조인(담당자 그룹)
   - `b0-005` '이용률' 동의어 + 리터럴 임계(E5-2 값 검색 의존)

`coverage` 필드는 트랙 C의 **커버리지율**(inside 비율) 및 **SMQ 정확도** 측정 축의 입력이다
(Plan 61 §7 트랙 C 검증기준). inside/outside는 *예상값*이며, E6 컴파일러 구현 후 실측으로
갱신한다(초기 baseline은 커버리지율의 상한 추정치).

## 표본 목표 (plans/70 V2 · 2026-08-20)

26건은 "목표 20건 이상"은 넘겼으나 **카테고리별로 보면 얇다** — `unhandled` 1건,
`alarm` 3건으로는 그 경로의 거부·폴백 설계 근거가 되지 못한다. 한 건이 흔들리면
카테고리 정확도가 100%p 단위로 튄다.

| 카테고리 | 현재 (inside/outside) | 최소 목표 | 부족 | 근거 |
|---|---|---:|---:|---|
| `server_config` | 11 / 0 | 8 (outside ≥1) | outside 1 | 충족. 다만 outside 0이라 이 카테고리의 폴백 거동이 미측정 |
| `performance` | 4 / 2 | 8 (outside ≥3) | 2 (outside 1) | 지표·기간·임계 조합이 가장 넓은 축 |
| `alarm` | 1 / 2 | 6 (inside ≥3) | 3 (inside 2) | inside 1건은 결정적 경로 회귀를 사실상 감지 못 함 |
| `complex` | 4 / 1 | 6 (outside ≥2) | 2 (outside 1) | 피벗·자기조인 등 조합 난도 |
| `unhandled` | 0 / 1 | 4 | 3 | **거부 경로 설계 근거로 1건은 표본이 아니다** |
| 합계 | 20 / 6 | **32** | **+6** | 커버리지율 목표 70~80% 유지(현 76.9%) |

### 착수 조건 — 실 DB 검증 없이는 추가하지 않는다

기존 항목의 `notes`가 보여주듯(`실측 정비 15차` 등) 이 골드셋은 **항목마다 실 DB
`--check-gold` 실측으로 확정**돼 왔다. 골드 SQL은 채점의 정답이므로, 미검증 항목을 넣으면
그 오답이 이후 모든 평가의 기준이 된다. `--dry-run`은 스키마만 보므로 **미검증 항목도
통과시키고 카테고리 분포와 커버리지율만 좋아 보이게 만든다** — 측정이 나빠지면서 지표는
좋아지는 최악의 조합이다.

따라서 추가 절차는 다음 순서를 강제한다.

```bash
# 1) DBHUB 기동 확인 (미기동이면 여기서 중단 — 초안 골드를 커밋하지 않는다)
curl -s -o /dev/null -w '%{http_code}\n' --max-time 5 http://localhost:9099/sse

# 2) 후보 항목 작성 후 스키마 검증
.venv/bin/python scripts/eval_text2sql.py --dry-run

# 3) 실 DB 대조 — 이 단계를 통과한 항목만 확정한다
.venv/bin/python scripts/eval_text2sql.py --check-gold --db <b0|gp|yd>
```

## 엔진 방언 주의 (골드 SQL 작성 규칙)

- **PostgreSQL(gp/yd)**: 행 제한 `LIMIT n`, 소수 보존 `ROUND(AVG(...)::numeric, 2)`, 스키마 `polestar.`
- **DB2(b0)**: 행 제한 `FETCH FIRST n ROWS ONLY`, 소수 보존은 **집계 전** `ROUND(AVG(CAST(col AS DECIMAL(15,4))), 2)`,
  스키마 접두사 `POLESTAR.`(미인용 식별자 대문자 저장). `::numeric`은 DB2 문법 오류.
  근거: Known Mistakes 2026-07-09, docs 결정 D-053/D-057/D-065.

## EX 채점 방식

러너의 `execution_match(gold_rows, pred_rows)`는 Spider/BIRD 하네스의 **결과집합 동치** 채점을
참조한다 — 정렬 무관 멀티셋 비교, 컬럼 순서·별칭 무관(행 내 값 정렬), 부동소수 tolerance.
자세한 로직·모드는 `scripts/eval_text2sql.py` 및 `tests/text2sql/test_ex_harness.py` 참조.
