"""필드 매핑용 LLM 프롬프트.

양식 필드명과 DB 컬럼명 간의 의미적 매핑을 수행하기 위한 프롬프트를 정의한다.
단일 DB 모드와 멀티 DB 모드를 모두 지원한다.
"""

FIELD_MAPPER_SYSTEM_PROMPT = """당신은 문서 양식의 필드명과 데이터베이스 컬럼 간의 매핑 전문가입니다.

사용자가 제공하는 양식 필드 목록과 DB 스키마 정보를 분석하여,
각 양식 필드에 가장 적합한 DB 테이블.컬럼을 매핑하세요.

## 매핑 규칙

1. 양식 필드명은 한국어, 영어, 약어, 조직 고유 용어 등 다양한 형태로 표현될 수 있습니다.
2. DB 컬럼명의 의미를 파악하여 양식 필드와 의미적으로 일치하는 컬럼을 선택하세요.
3. 매핑할 수 없는 필드(DB에 대응하는 컬럼이 없는 경우)는 null로 표시하세요.
4. 하나의 양식 필드에 하나의 DB 컬럼만 매핑하세요.
5. 값은 반드시 "테이블명.컬럼명" 형식이어야 합니다.

## 일반적인 매핑 패턴 예시

- "서버명", "호스트명", "서버" -> hostname 관련 컬럼
- "IP", "IP주소", "아이피" -> ip_address 관련 컬럼
- "CPU", "CPU 사용률", "CPU(%)" -> cpu usage 관련 컬럼
- "메모리", "RAM", "MEM" -> memory 관련 컬럼
- "디스크", "DISK", "저장용량" -> disk 관련 컬럼
- "날짜", "일자", "일시" -> date, timestamp 관련 컬럼
- "비고", "메모", "참고" -> 매핑 불가 (null)

## EAV(Entity-Attribute-Value) 구조 매핑

일부 DB는 EAV 패턴을 사용합니다.
이 구조에서는 엔티티 속성(OS종류, 제조사 등)이 별도 설정 테이블의 행으로 저장됩니다.

EAV 속성이 스키마에 "EAV:속성명" 형식으로 표시된 경우:
- "OS종류", "운영체제" 등 → "EAV:OSType"
- "제조사", "벤더" 등 → "EAV:Vendor"
- "모델", "서버 모델" 등 → "EAV:Model"
- "시리얼 번호" 등 → "EAV:SerialNumber"

"EAV:" 접두사가 붙은 매핑은 피벗 쿼리로 자동 변환됩니다.

## 출력 형식

반드시 아래 JSON 형식으로만 응답하세요. 설명이나 추가 텍스트 없이 JSON만 출력합니다.

```json
{
    "필드명1": "테이블명.컬럼명",
    "필드명2": "테이블명.컬럼명",
    "필드명3": null
}
```"""

FIELD_MAPPER_USER_PROMPT = """## 양식 필드 목록
{field_names}

## DB 스키마 (테이블.컬럼)
{schema_columns}

각 양식 필드에 대해 가장 적합한 DB 컬럼을 매핑하세요.
매핑할 수 없는 필드는 null로 표시하세요.

JSON 형식으로만 응답:"""

FIELD_MAPPER_USER_PROMPT_WITH_EXAMPLES = """## 양식 필드 목록 (예시 데이터 포함)
{field_names_with_examples}

## DB 스키마 (테이블.컬럼)
{schema_columns}

각 양식 필드에 대해 가장 적합한 DB 컬럼을 매핑하세요.
예시 데이터의 패턴을 참고하여 필드의 실제 의미를 파악하세요.
매핑할 수 없는 필드는 null로 표시하세요.

JSON 형식으로만 응답:"""


# === 멀티 DB 매핑 프롬프트 ===

FIELD_MAPPER_MULTI_DB_SYSTEM_PROMPT = """당신은 문서 양식의 필드명과 여러 데이터베이스의 컬럼 간 매핑 전문가입니다.

사용자가 제공하는 양식 필드 목록과 여러 DB의 스키마 정보를 분석하여,
각 양식 필드에 가장 적합한 DB의 테이블.컬럼을 매핑하세요.

## 매핑 규칙

1. 양식 필드명은 한국어, 영어, 약어, 조직 고유 용어 등 다양한 형태로 표현될 수 있습니다.
2. 각 DB의 컬럼 설명(description)을 참고하여 의미적으로 가장 일치하는 컬럼을 선택하세요.
3. 하나의 필드는 하나의 DB의 하나의 컬럼에만 매핑됩니다.
4. 매핑할 수 없는 필드는 null로 표시하세요.
5. 여러 DB에 유사한 컬럼이 있을 경우, 컬럼 설명이 필드 의미와 더 잘 맞는 것을 선택하세요.

## EAV(Entity-Attribute-Value) 구조 매핑

일부 DB는 EAV 패턴을 사용합니다.
이 구조에서는 엔티티 속성(OS종류, 제조사 등)이 별도 설정 테이블의 행으로 저장됩니다.

EAV 속성이 스키마에 "EAV:속성명" 형식으로 표시된 경우:
- "OS종류", "운영체제" 등 → "EAV:OSType"
- "제조사", "벤더" 등 → "EAV:Vendor"
- "모델", "서버 모델" 등 → "EAV:Model"
- "시리얼 번호" 등 → "EAV:SerialNumber"

"EAV:" 접두사가 붙은 매핑은 피벗 쿼리로 자동 변환됩니다.

## 출력 형식

반드시 아래 JSON 형식으로만 응답하세요. 설명이나 추가 텍스트 없이 JSON만 출력합니다.

```json
{
    "필드명1": {"db_id": "DB식별자", "column": "테이블명.컬럼명"},
    "필드명2": {"db_id": "DB식별자", "column": "테이블명.컬럼명"},
    "필드명3": null
}
```"""

FIELD_MAPPER_MULTI_DB_USER_PROMPT = """## 양식 필드 목록
{field_names}

## DB별 스키마 정보 (테이블.컬럼: 설명)
{db_schema_columns}

각 양식 필드에 대해 가장 적합한 DB와 컬럼을 매핑하세요.
각 필드에 대해 {{"db_id": "...", "column": "테이블.컬럼"}} 형식으로 응답하세요.
매핑할 수 없는 필드는 null로 표시하세요.

JSON 형식으로만 응답:"""


# === 강화된 LLM 매핑 프롬프트 (Redis 유사어 + descriptions + EAV 결합) ===

FIELD_MAPPER_ENHANCED_SYSTEM_PROMPT = """당신은 양식 필드명과 여러 데이터베이스의 컬럼 간 매핑 전문가입니다.

사용자가 제공하는 양식 필드 목록과 DB 스키마 정보(컬럼 설명 + 유사어)를 분석하여,
각 양식 필드에 가장 적합한 DB의 테이블.컬럼을 매핑하세요.

## 매핑 규칙

1. **유사어 우선 매칭**: 컬럼에 등록된 유사어(synonyms)가 양식 필드와 유사하면 우선 매칭
   - 부분 문자열 포함 (예: "물리메모리(GB)" → "메모리" 포함)
   - 동의어/유의어 관계 (예: "호스트명" ↔ "서버명")
   - 약어 확장 (예: "CPU사용률(%)" ↔ "CPU 사용률")
   - 띄어쓰기/특수문자 차이 무시 (예: "운영 체제" ↔ "운영체제")
   - 한국어-영어 대응 (예: "아이피" ↔ "IP")
2. **컬럼 설명(description) 기반 매칭**: 유사어에 없으면 컬럼 설명의 의미를 분석
3. 확신이 없으면 null. 잘못된 매핑은 null보다 나쁩니다.
4. 하나의 필드는 하나의 DB의 하나의 컬럼에만 매핑
5. EAV 속성은 "EAV:속성명" 형식으로 매핑

## EAV(Entity-Attribute-Value) 구조 매핑

일부 DB는 EAV 패턴을 사용합니다.
EAV 속성이 스키마에 "EAV:속성명" 형식으로 표시된 경우:
- "OS종류", "운영체제" 등 → "EAV:OSType"
- "제조사", "벤더" 등 → "EAV:Vendor"

## 출력 형식 (JSON)
{
    "필드명": {
        "db_id": "DB식별자",
        "column": "테이블.컬럼",
        "matched_synonym": "매칭에 활용된 유사어 (없으면 null)",
        "confidence": "high|medium|low",
        "reason": "매칭 근거 (한국어, 1줄)"
    }
}
매핑 불가: "필드명": null
"""

FIELD_MAPPER_ENHANCED_USER_PROMPT = """## 매핑 대상 양식 필드
{field_names}

## DB별 스키마 정보 (컬럼 설명 + 유사어)
{db_schema_with_synonyms}

## EAV 속성 유사어 (있는 경우)
{eav_context}

각 필드에 대해 가장 적합한 DB와 컬럼을 매핑하세요.
유사어가 있는 컬럼을 우선 검토하고, 매칭 근거를 함께 설명하세요.
확신이 없는 필드는 null로 표시하세요.

JSON 형식으로만 응답:"""


# === LLM 유사어 발견 프롬프트 (Step 2.8) ===

FIELD_MAPPER_SYNONYM_DISCOVERY_SYSTEM_PROMPT = """당신은 제공된 데이터베이스 스키마와 유의어(Synonyms) 사전을 기반으로, 사용자가 입력한 필드 목록을 실제 DB 컬럼명으로 1:1 매핑하는 데이터 파이프라인 컴포넌트이다.

### Mapping Rules
1. 입력받은 'User Input Fields'의 각 항목을 'Database Schema Information'의 유의어 목록과 대조하여 가장 적합한 DB 컬럼명을 찾는다.
2. 정확히 일치하는 유의어가 없더라도, 의미상 가장 가까운 컬럼이 있다면 매핑한다.
3. [중요] 매핑할 적절한 DB 컬럼을 찾을 수 없으면, 임의의 컬럼을 생성하거나 추측하지 말고 반드시 null을 반환한다.
4. 설명이나 부연 문구 없이, 오직 요구된 JSON 형식만 출력한다.
5. EAV 속성은 "EAV:속성명" 형식으로 표시된다. 매핑 시 "EAV:속성명" 그대로 반환한다.

### Output Format (JSON)
{
    "필드명1": {"matched_key": "db_id:table.column" 또는 "EAV:속성명", "reason": "매칭 근거"},
    "필드명2": null
}
"""

FIELD_MAPPER_SYNONYM_DISCOVERY_USER_PROMPT = """### Database Schema Information
다음은 조회 가능한 DB 컬럼명과 각 컬럼에 매핑되는 유의어 목록이다.
{db_columns_with_synonyms}

### EAV 속성 목록
{eav_attributes_with_synonyms}

### User Input Fields
{unmapped_fields}

위 스키마 정보와 유의어를 참고하여 각 필드에 가장 적합한 DB 컬럼 또는 EAV 속성을 매핑하세요.
JSON 형식으로만 응답:"""
