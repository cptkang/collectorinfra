"""cache_management 노드용 프롬프트 템플릿.

사용자의 캐시 관리 의도를 분석하여 action, db_id, target을 추출한다.
"""

CACHE_MANAGEMENT_PARSE_PROMPT = """사용자의 캐시 관리 요청을 분석하세요.

## 가능한 action
- `generate`: 스키마 캐시 생성/갱신
- `generate-descriptions`: 컬럼 설명 (재)생성
- `generate-synonyms`: 유사 단어 생성
- `generate-global-synonyms`: 글로벌 유사 단어 LLM 생성 ("hostname의 유사 단어를 생성해줘", "server_name 필드의 유사 단어를 만들어줘. 예: 서버명, 호스트")
- `generate-db-description`: DB 설명 생성 (LLM으로 자동 생성)
- `set-db-description`: DB 설명 수동 설정 (target_column에 설명 텍스트)
- `db-guide`: DB 목록/설명 조회 ("어떤 DB가 있어?", "DB 목록 보여줘")
- `status`: 캐시 상태 조회
- `invalidate`: 캐시 삭제
- `list-synonyms`: 유사 단어 목록 조회 ("유사 단어 목록을 보여줘", "hostname의 유사 단어를 보여줘", "polestar DB의 유사 단어를 보여줘")
- `add-synonym`: 유사 단어 추가 ("hostname에 '서버호스트' 유사 단어를 추가해줘")
- `remove-synonym`: 유사 단어 삭제 ("hostname에서 '호스트네임' 유사 단어를 삭제해줘")
- `update-synonym`: 유사 단어 교체 ("usage_pct의 유사 단어를 '사용률, 사용비율'로 변경해줘")
- `update-description`: 글로벌 컬럼 설명 수정 ("hostname 컬럼의 설명을 '서버의 호스트명 (FQDN)'으로 변경해줘")
- `reuse-synonym`: 유사 필드 재활용 응답 ("재활용", "새로 생성", "병합")

## 출력 형식

반드시 JSON만 출력하세요.

```json
{{
    "action": "generate",
    "db_id": "polestar",
    "target_table": null,
    "target_column": null,
    "words": null,
    "seed_words": null,
    "description": null,
    "reuse_mode": null
}}
```

- db_id: 대상 DB 식별자 (null이면 전체 또는 글로벌)
- target_table: 특정 테이블 (유사 단어 관리 시)
- target_column: 특정 컬럼명. table.column 형식 또는 bare column name (유사 단어 관리 시)
- words: 추가/삭제/교체할 유사 단어 목록 (add-synonym, remove-synonym, update-synonym 시). 문자열 배열.
- seed_words: 사용자가 제공한 유사 단어 예시 (generate-global-synonyms 시, 선택). 문자열 배열.
- description: 컬럼 설명 텍스트 (update-description 시)
- reuse_mode: 재활용 모드 (reuse-synonym 시). "reuse" | "new" | "merge"

## generate-global-synonyms 판별 기준
사용자가 특정 필드/컬럼에 대해 "유사 단어를 생성해줘", "유사 단어를 만들어줘", "더 만들어줘" 등의 **생성** 요청을 한 경우.
DB를 지정하지 않고 컬럼명만 지정하여 유사 단어 **생성**을 요청하면 이 action을 사용하세요.
"예:", "예시:", "참고:" 뒤에 오는 단어들은 seed_words로 추출하세요.

## update-description 판별 기준
사용자가 "컬럼의 설명을 변경해줘", "설명을 추가해줘", "설명을 수정해줘" 등의 요청을 한 경우.
description 필드에 새 설명 텍스트를 추출하세요.

## reuse-synonym 판별 기준
사용자가 이전 재활용 제안에 대해 "재활용", "hostname 유사 단어 재활용" -> reuse_mode: "reuse"
"새로 생성" -> reuse_mode: "new"
"병합" -> reuse_mode: "merge"

## 사용자 요청

{user_query}
"""

GENERATE_GLOBAL_SYNONYMS_PROMPT = """당신은 DB 컬럼명에 대한 유사 단어(synonym) 생성 전문가입니다.
주어진 컬럼명에 대해 사용자가 자연어로 질의할 때 사용할 수 있는
다양한 표현(한국어, 영어, 약어, 조직 고유 용어)을 생성하세요.

컬럼명: {column_name}{seed_words_text}

이 컬럼에 대한 유사 단어와 한 줄 설명을 생성해주세요.
한국어 표현을 우선으로, 영어/약어도 포함하세요.
최소 5개 이상의 유사 단어를 생성하세요.

반드시 아래 JSON 형식으로만 응답하세요:

```json
{{"words": ["유사단어1", "유사단어2", ...], "description": "컬럼 설명 (한국어 한 줄)"}}
```
"""

FIND_SIMILAR_COLUMNS_PROMPT = """당신은 DB 컬럼 간의 의미적 유사성을 판단하는 전문가입니다.

아래 대상 컬럼과 의미적으로 유사한 컬럼을 기존 목록에서 찾아주세요.
의미적으로 유사하다는 것은, 동일하거나 매우 비슷한 데이터를 가리키는 컬럼을 의미합니다.
(예: hostname과 server_name은 둘 다 서버 식별자이므로 유사)

대상 컬럼: {target_column}

기존 컬럼 목록:
{existing_columns_info}

유사한 컬럼이 있으면 JSON 배열로 응답하세요. 없으면 빈 배열 []을 반환하세요.
유사도가 높은 것만 포함하세요 (최대 3개).

```json
[{{"column": "유사컬럼명", "reason": "유사한 이유"}}]
```
"""
