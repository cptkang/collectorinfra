# SQL Queries Reference

프로젝트에서 코드에 정의되어 실행되는 SQL 쿼리 모음.
LLM이 동적으로 생성하는 SQL이 아닌, 코드에 하드코딩/템플릿 정의된 SQL만 포함한다.

## 파일 구성

| 파일 | 설명 | 소스 코드 |
|------|------|-----------|
| `01_schema_discovery.sql` | 스키마 탐색 (fingerprint, 테이블/컬럼/FK 조회, health check) | `src/db/client.py`, `src/dbhub/client.py`, `src/schema_cache/fingerprint.py` |
| `02_polestar_eav_patterns.sql` | Polestar EAV 쿼리 패턴 (서버목록, EAV 피벗, 계층 탐색) | `src/prompts/polestar_patterns.py` |
| `03_polestar_samples.sql` | Polestar 샘플 데이터 수집 (속성 분포, 리소스 타입, 계층 구조) | `src/nodes/schema_analyzer.py` |
| `04_test_verification.sql` | 테스트 데이터 검증 (행 수, 분포, 설정값 확인) | `testdata/pg/04_verify_data.sql` |

## SQL 실행 흐름

```
사용자 질의
  |
  v
schema_analyzer
  |- 01: fingerprint SQL (변경 감지)
  |- 01: 테이블/컬럼/FK 조회 (캐시 미스 시)
  |- 03: Polestar 샘플 수집 (EAV DB 감지 시)
  v
query_generator
  |- 02: Polestar 패턴 참고 (LLM 프롬프트에 삽입)
  |- LLM이 사용자 질의에 맞는 SQL 동적 생성
  v
query_executor
  |- LLM 생성 SQL 실행
  v
결과 반환
```

## 제약 사항

- 모든 SQL은 **SELECT 문만** 허용 (Read-Only)
- 타임아웃: 30초
- 최대 결과 행 수: 10,000
- DB 엔진별 행 제한 문법: PostgreSQL(`LIMIT N`), DB2(`FETCH FIRST N ROWS ONLY`)
