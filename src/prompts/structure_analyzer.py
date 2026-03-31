"""DB 구조 분석 프롬프트.

LLM 기반으로 DB 스키마의 구조적 패턴(EAV, 계층형, JOIN 관계)을 감지하고,
구조 이해용 샘플 SQL을 생성하기 위한 프롬프트 상수를 정의한다.
"""

STRUCTURE_ANALYSIS_PROMPT = """\
아래 DB 스키마를 분석하여 특수한 구조적 패턴을 감지하세요.

감지할 패턴:
1. EAV(Entity-Attribute-Value): 속성이 행으로 저장되는 구조
   - entity 테이블, config(attribute-value) 테이블, attribute 컬럼, value 컬럼을 식별
   - FK 제약이 있으면 join_condition을 설정. FK가 없으면 join_condition을 생략하고 value_joins를 사용
   - value_joins: EAV 테이블의 특정 속성값이 entity 테이블의 컬럼값과 동일한 경우 (예: EAV의 'Hostname' 속성값 = entity의 hostname 컬럼값)
2. 계층형(Self-referencing): 부모-자식 관계를 같은 테이블 내 FK로 표현
   - id 컬럼, parent 컬럼, type 컬럼을 식별
3. JOIN 관계: 테이블 간 FK 관계

패턴이 감지되지 않으면 patterns를 빈 배열로 반환하세요.

반드시 JSON으로만 응답:
{
  "patterns": [
    {
      "type": "eav",
      "entity_table": "테이블명",
      "config_table": "테이블명",
      "join_condition": "config_table.FK = entity_table.PK (FK가 있는 경우에만, 없으면 생략)",
      "value_joins": [
        {
          "eav_attribute": "Hostname",
          "eav_value_column": "값 컬럼",
          "entity_column": "entity 테이블의 매칭 컬럼",
          "description": "값 기반 조인 설명"
        }
      ],
      "attribute_column": "속성명 컬럼",
      "value_column": "값 컬럼",
      "lob_value_column": "LOB값 컬럼 (있으면)",
      "lob_flag_column": "LOB 플래그 컬럼 (있으면)"
    },
    {
      "type": "hierarchy",
      "table": "테이블명",
      "id_column": "ID 컬럼",
      "parent_column": "부모 참조 컬럼",
      "type_column": "타입 구분 컬럼",
      "name_column": "이름 컬럼"
    }
  ],
  "query_guide": "이 DB를 쿼리할 때 참고할 패턴 설명과 예시 SQL (자연어+SQL 혼합, 상세하게)"
}"""

SAMPLE_SQL_GENERATION_PROMPT = """\
아래 DB 구조 분석 결과를 바탕으로, 이 DB의 구조를 이해하는 데 도움이 되는 SELECT 쿼리를 생성하세요.

규칙:
- 최대 3개의 SELECT 쿼리만 생성
- 모든 쿼리에 FETCH FIRST N ROWS ONLY 또는 LIMIT N 절 포함 (최대 30행)
- SELECT만 허용, INSERT/UPDATE/DELETE/DDL 절대 금지
- 목적: EAV 속성 목록, 계층 구조 샘플, 타입별 분포 등 구조 이해용 데이터

JSON 배열로만 응답:
[
  {"purpose": "쿼리 목적 설명", "sql": "SELECT ..."},
  ...
]"""
