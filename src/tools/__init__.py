"""단계적 쿼리 조립용 fine-grained 도구 계층 (Plan 67 Phase S1 §4.2·4.3).

기존 파이프라인 노드를 통째로 감싼 굵은 도구(orchestration.deepagents_tools)와 달리,
컬럼 하나를 고르는 데 필요한 최소 단위 동작을 도구로 제공한다.

2층 구조:
    - 순수 함수(모듈별): 의존성을 인자로 주입받고 상태에 결합하지 않는다. 테스트·재사용 단위.
    - LangChain 도구(``binding.build_query_tools``): 의존성을 ``ToolContext``로 주입해 감싼
      LLM tool-calling 소재.

공용 계층이므로 특정 DB의 스키마 리터럴을 두지 않는다(D-088). DB 특화 판정이 필요하면
어댑터 레지스트리를 경유한다(D-089).
"""

from src.tools.binding import ToolContext, build_query_tools
from src.tools.catalog import catalog_entries, check_smq_coverage, search_catalog
from src.tools.interpretation import resolve_limit, resolve_time_range
from src.tools.lexicon import lookup_synonym, search_value_index
from src.tools.metrics import classify_metric_field
from src.tools.schema_probe import get_sample_data, get_table_schema
from src.tools.validation import validate_sql_draft

__all__ = [
    "ToolContext",
    "build_query_tools",
    "catalog_entries",
    "check_smq_coverage",
    "classify_metric_field",
    "get_sample_data",
    "get_table_schema",
    "lookup_synonym",
    "resolve_limit",
    "resolve_time_range",
    "search_catalog",
    "search_value_index",
    "validate_sql_draft",
]
