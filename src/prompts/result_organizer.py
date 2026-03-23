"""result_organizer 노드용 프롬프트 템플릿.

쿼리 결과를 정리하고 요약을 생성하는 LLM 프롬프트를 정의한다.
"""

RESULT_ORGANIZER_SUMMARY_PROMPT = """아래 쿼리 결과를 한국어로 간략히 요약하세요.
1~2문장으로 핵심 내용만 전달하세요.

## 사용자 질의
{user_query}

## 조회 대상
{query_targets}

## 결과 건수
{row_count}건

## 데이터 미리보기 (상위 5건)
{preview_data}

요약:
"""
