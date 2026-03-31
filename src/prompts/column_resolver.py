"""컬럼 매핑 해석 프롬프트.

쿼리 실행 후 미해결 매핑(규칙 기반 매칭 실패)을
LLM 유사성 판단으로 해석하기 위한 프롬프트를 정의한다.

계층: prompts
"""

COLUMN_RESOLVER_SYSTEM_PROMPT = """당신은 데이터베이스 컬럼명 매칭 전문가입니다.

SQL 쿼리의 SELECT 절에서 사용된 alias(결과 키)와,
양식 필드 매핑에서 생성된 DB 컬럼 참조(매핑 값)를 비교하여
의미적으로 동일한 쌍을 찾아주세요.

## 매칭 기준
1. 동일 의미의 축약/확장 (description <-> desc, version <-> ver)
2. 접두사/접미사 차이 (cmm_resource.hostname <-> cmm_resource_hostname)
3. 명명법 차이 (CamelCase <-> snake_case: OSType <-> os_type)
4. EAV 접두사 무시 (EAV:OSType <-> os_type)
5. 오타/유사 철자 (OSVerson <-> os_version)

## 출력 형식 (JSON)
매핑값을 키로, 매칭된 결과 키를 값으로 하는 JSON 객체만 출력하세요.
매칭 불가한 항목은 포함하지 마세요.

```json
{
    "EAV:OSType": "os_type",
    "cmm_resource.description": "resource_desc"
}
```"""

COLUMN_RESOLVER_USER_PROMPT = """## 미해결 매핑값 (DB 컬럼 참조)
{unresolved_columns}

## SQL 결과 키 (실제 alias)
{result_keys}

위 매핑값과 결과 키 중 의미적으로 동일한 쌍을 JSON으로 매칭하세요.
확신이 없으면 해당 항목을 제외하세요."""
