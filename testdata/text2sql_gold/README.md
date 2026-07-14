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

## 엔진 방언 주의 (골드 SQL 작성 규칙)

- **PostgreSQL(gp/yd)**: 행 제한 `LIMIT n`, 소수 보존 `ROUND(AVG(...)::numeric, 2)`, 스키마 `polestar.`
- **DB2(b0)**: 행 제한 `FETCH FIRST n ROWS ONLY`, 소수 보존은 **집계 전** `ROUND(AVG(CAST(col AS DECIMAL(15,4))), 2)`,
  스키마 접두사 `POLESTAR.`(미인용 식별자 대문자 저장). `::numeric`은 DB2 문법 오류.
  근거: Known Mistakes 2026-07-09, docs 결정 D-053/D-057/D-065.

## EX 채점 방식

러너의 `execution_match(gold_rows, pred_rows)`는 Spider/BIRD 하네스의 **결과집합 동치** 채점을
참조한다 — 정렬 무관 멀티셋 비교, 컬럼 순서·별칭 무관(행 내 값 정렬), 부동소수 tolerance.
자세한 로직·모드는 `scripts/eval_text2sql.py` 및 `tests/text2sql/test_ex_harness.py` 참조.
