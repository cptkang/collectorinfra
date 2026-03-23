# 09. 시멘틱 라우팅 구현 계획서 (v2)

> 작성일: 2026-03-16
> 수정일: 2026-03-17
> 관련 요구사항: docs/requirements.md 섹션 9
> 변경 사유: 키워드 기반 1차 분류 제거, LLM 전용 라우팅, 사용자 직접 DB 지정 지원, 멀티 DB 결과 취합 강화

## 1. 개요

사용자의 자연어 프롬프트를 **LLM 전용으로** 분석하여 적절한 DB를 자동 선택하고 쿼리를 실행하는 시멘틱 라우팅 기능을 구현한다.

**v2 핵심 변경 사항:**
1. 키워드 기반 1차 분류 로직 완전 제거 (`_keyword_match`, `_needs_llm_fallback` 함수 삭제)
2. 사용자가 프롬프트에서 직접 DB를 지정할 수 있는 기능 추가
3. 멀티 DB 쿼리 시 DB별 sub_query_context 분리 및 결과 취합 개선

## 2. 변경 범위

### 2.1 수정 모듈

| 파일 | 변경 내용 |
|------|----------|
| `src/routing/semantic_router.py` | `_keyword_match`, `_needs_llm_fallback` 함수 삭제. `semantic_router()` 함수를 LLM 전용으로 재구성. 사용자 직접 DB 지정 감지 로직 추가 |
| `src/routing/domain_config.py` | `DBDomainConfig`에서 `keywords` 필드 제거. `get_domain_aliases()` 함수 추가 (사용자 직접 지정 시 별칭 매칭용) |
| `src/prompts/semantic_router.py` | LLM 프롬프트 전면 개편: 사용자 직접 DB 지정 규칙, 멀티 DB 판단, sub_query_context 분리 규칙 추가. JSON 출력에 `user_specified`, `sub_query_context` 필드 추가 |
| `src/state.py` | `AgentState`에 `user_specified_db: Optional[str]` 필드 추가. `create_initial_state()`에 기본값 추가 |
| `src/nodes/result_merger.py` | 멀티 DB 결과 취합 로직 강화: DB별 결과 요약 정보 생성 |

### 2.2 변경 불필요 모듈

| 파일 | 이유 |
|------|------|
| `src/routing/db_registry.py` | DB 연결 관리 로직은 변경 불필요 |
| `src/nodes/multi_db_executor.py` | 이미 DB별 파이프라인 실행을 올바르게 구현하고 있음 |
| `src/graph.py` | 그래프 흐름(semantic_router -> 조건부 -> multi_db_executor/schema_analyzer)은 유지 |
| `src/config.py` | MultiDBConfig는 변경 불필요 |

## 3. 상세 설계

### 3.1 domain_config.py - keywords 필드 제거

**변경 전:**
```python
@dataclass(frozen=True)
class DBDomainConfig:
    db_id: str
    display_name: str
    description: str
    keywords: list[str] = field(default_factory=list)  # 제거 대상
    env_connection_key: str = ""
    env_type_key: str = ""
```

**변경 후:**
```python
@dataclass(frozen=True)
class DBDomainConfig:
    db_id: str
    display_name: str
    description: str
    aliases: list[str] = field(default_factory=list)  # 사용자 직접 DB 지정 시 인식할 별칭
    env_connection_key: str = ""
    env_type_key: str = ""
```

`aliases` 필드는 사용자가 프롬프트에서 DB를 직접 지정할 때 인식할 이름 목록이다:
- polestar: ["polestar", "폴스타", "Polestar"]
- cloud_portal: ["cloud_portal", "클라우드 포탈", "클라우드포탈", "Cloud Portal"]
- itsm: ["itsm", "ITSM"]
- itam: ["itam", "ITAM", "자산관리"]

**DB_DOMAINS 정의에서 keywords 관련 항목 전부 삭제.** 각 도메인의 `description`만 유지한다.

### 3.2 semantic_router.py - LLM 전용 라우팅으로 재구성

**삭제할 함수:**
- `_keyword_match()` - 키워드 기반 1차 분류
- `_needs_llm_fallback()` - LLM 폴백 판단

**삭제할 상수:**
- `KEYWORD_CONFIDENCE_THRESHOLD`

**수정할 함수:**
- `semantic_router()` - 메인 라우팅 함수

**추가할 함수:**
- `_detect_user_specified_db()` - 사용자 직접 DB 지정 감지

**새로운 라우팅 흐름:**
```python
async def semantic_router(state, *, llm, app_config):
    user_query = state["user_query"]
    active_db_ids = app_config.multi_db.get_active_db_ids()

    if not active_db_ids:
        # 레거시 단일 DB 모드
        return {...}

    active_domains = [d for d in DB_DOMAINS if d.db_id in active_db_ids]

    # 1. LLM 기반 분류 (사용자 직접 지정 감지 포함)
    llm_results = await _llm_classify(llm, user_query, active_domains)

    # 2. 최소 관련도 필터링 및 정렬
    targets = [r for r in llm_results if r["relevance_score"] >= MIN_RELEVANCE_SCORE]
    targets.sort(key=lambda x: x["relevance_score"], reverse=True)

    # 3. 결과가 없으면 첫 번째 활성 DB 사용
    if not targets:
        targets = [{
            "db_id": active_db_ids[0],
            "relevance_score": 0.5,
            "sub_query_context": user_query,
            "user_specified": False,
            "reason": "LLM 분류 결과 없음, 기본 DB 사용",
        }]

    # 4. 사용자 직접 지정 DB 확인
    user_specified_db = None
    for t in targets:
        if t.get("user_specified"):
            user_specified_db = t["db_id"]
            break

    return {
        "target_databases": targets,
        "is_multi_db": len(targets) > 1,
        "active_db_id": targets[0]["db_id"],
        "user_specified_db": user_specified_db,
        "current_node": "semantic_router",
    }
```

### 3.3 _llm_classify 함수 수정

**변경 사항:**
- 응답 JSON 파싱 시 `user_specified`, `sub_query_context` 필드를 추출
- `reason` 필드를 기존 `sub_query_context` 대신 별도로 유지
- 프롬프트를 동적으로 구성 (활성 도메인의 description 기반)

```python
async def _llm_classify(llm, query, domains):
    # 동적 프롬프트 생성: 활성 도메인만 포함
    system_prompt = _build_router_prompt(domains)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=query),
    ]
    response = await llm.ainvoke(messages)
    parsed = _extract_json_from_response(response.content)

    if not parsed or "databases" not in parsed:
        return []

    valid_db_ids = {d.db_id for d in domains}
    results = []
    for db_entry in parsed["databases"]:
        db_id = db_entry.get("db_id", "")
        if db_id in valid_db_ids:
            results.append({
                "db_id": db_id,
                "relevance_score": float(db_entry.get("relevance_score", 0.5)),
                "sub_query_context": db_entry.get("sub_query_context", query),
                "user_specified": bool(db_entry.get("user_specified", False)),
                "reason": db_entry.get("reason", ""),
            })
    return results
```

### 3.4 _build_router_prompt 함수 추가

활성 도메인 목록을 기반으로 LLM 프롬프트를 동적으로 구성하는 함수를 추가한다.

```python
def _build_router_prompt(domains: list[DBDomainConfig]) -> str:
    """활성 도메인 기반으로 라우팅 프롬프트를 동적 생성한다."""
    db_descriptions = []
    for i, domain in enumerate(domains, 1):
        aliases_str = ", ".join(domain.aliases) if domain.aliases else domain.db_id
        db_descriptions.append(
            f"{i}. **{domain.display_name}** ({domain.db_id})\n"
            f"   - 별칭: {aliases_str}\n"
            f"   - {domain.description}"
        )
    db_list = "\n\n".join(db_descriptions)
    return SEMANTIC_ROUTER_SYSTEM_PROMPT_TEMPLATE.format(db_list=db_list)
```

### 3.5 prompts/semantic_router.py - 프롬프트 전면 개편

```python
SEMANTIC_ROUTER_SYSTEM_PROMPT_TEMPLATE = """당신은 인프라 관련 질의를 분석하여 적절한 데이터베이스를 선택하는 전문가입니다.
사용자의 질의를 분석하여 어떤 데이터베이스를 조회해야 하는지 판단하세요.

## 사용 가능한 데이터베이스

{db_list}

## 사용자 직접 DB 지정 규칙

사용자가 프롬프트에서 특정 DB를 명시적으로 지정할 수 있습니다.
다음과 같은 패턴을 인식하세요:
- DB 식별자 직접 언급: "polestar에서", "cloud_portal에서" 등
- DB 표시명 언급: "Polestar DB에서", "Cloud Portal에서" 등
- 한국어 별칭: "클라우드 포탈에서", "자산관리 DB에서" 등
- 패턴: "~에서 조회해줘", "~에서 찾아줘", "~DB에서" 등

사용자가 DB를 직접 지정한 경우 해당 DB를 반드시 포함하고, user_specified를 true로 설정하세요.

## 멀티 DB 쿼리 판단

하나의 질의가 여러 DB의 데이터를 필요로 할 수 있습니다.
이 경우 각 DB별로 조회해야 할 내용을 sub_query_context에 분리하여 기술하세요.

## 출력 형식

반드시 아래 JSON 형식으로만 응답하세요. 추가 설명은 불필요합니다.

```json
{{
    "databases": [
        {{
            "db_id": "데이터베이스 식별자",
            "relevance_score": 0.9,
            "reason": "선택 이유",
            "sub_query_context": "이 DB에서 조회할 구체적 내용",
            "user_specified": false
        }}
    ]
}}
```

## 판단 규칙

1. 질의가 하나의 DB 도메인에만 해당하면 해당 DB만 선택합니다.
2. 질의가 여러 DB를 필요로 하면 관련된 모든 DB를 선택하고, 각 DB별 sub_query_context를 분리합니다.
3. relevance_score는 0.0~1.0 사이의 관련도 점수입니다.
4. 확실한 매칭이면 0.8 이상, 가능성 있는 매칭이면 0.5~0.8, 약한 연관이면 0.3~0.5를 부여합니다.
5. 0.3 미만의 관련도를 가진 DB는 포함하지 마세요.
6. 사용자가 DB를 직접 지정한 경우 해당 DB의 relevance_score를 1.0으로, user_specified를 true로 설정하세요.

반드시 유효한 JSON만 출력하세요.
"""
```

기존 `SEMANTIC_ROUTER_SYSTEM_PROMPT` 상수는 `SEMANTIC_ROUTER_SYSTEM_PROMPT_TEMPLATE`로 교체한다.

### 3.6 state.py - user_specified_db 필드 추가

```python
class AgentState(TypedDict):
    # ... 기존 필드 ...
    user_specified_db: Optional[str]         # 사용자가 직접 지정한 DB (없으면 None)
```

`create_initial_state()`에 `user_specified_db=None` 추가.

### 3.7 result_merger.py - 결과 취합 개선

멀티 DB 결과 병합 시 DB별 결과 요약을 생성하여 result_organizer에 전달한다:

```python
async def result_merger(state, *, app_config):
    db_results = state.get("db_results", {})
    db_errors = state.get("db_errors", {})

    merged_results = state.get("query_results", [])

    # DB별 결과 요약 정보 생성
    db_result_summary = {}
    for db_id, rows in db_results.items():
        domain = get_domain_by_id(db_id)
        db_result_summary[db_id] = {
            "display_name": domain.display_name if domain else db_id,
            "row_count": len(rows),
            "columns": list(rows[0].keys()) if rows else [],
        }

    # 에러 요약
    error_summary = _build_error_summary(db_results, db_errors)

    return {
        "query_results": merged_results,
        "db_result_summary": db_result_summary,
        "error_message": error_summary if not db_results else None,
        "current_node": "result_merger",
    }
```

## 4. 테스트 계획

### 4.1 기존 테스트 수정

| 테스트 | 변경 내용 |
|--------|----------|
| `test_keyword_routing` | **삭제** - 키워드 라우팅이 제거되므로 불필요 |
| `test_llm_fallback_routing` | **삭제 및 재작성** -> `test_llm_routing` - LLM 전용 라우팅 테스트로 변경 |

### 4.2 신규 테스트

| 테스트 | 설명 |
|--------|------|
| `test_llm_only_routing` | LLM 전용으로 각 DB 도메인이 정확히 분류되는지 검증 |
| `test_user_specified_db` | 사용자가 "polestar에서 조회해줘" 등으로 DB를 지정했을 때 해당 DB가 선택되는지 검증 |
| `test_user_specified_db_aliases` | 한국어 별칭("클라우드 포탈에서")으로 DB 지정 시 올바르게 인식되는지 검증 |
| `test_multi_db_detection` | 멀티 DB 질의를 정확히 판별하는지 검증 (기존 유지) |
| `test_multi_db_sub_query_context` | 멀티 DB 시 각 DB별 sub_query_context가 적절히 분리되는지 검증 |
| `test_single_db_backward_compat` | 기존 단일 DB 모드가 정상 동작하는지 검증 (기존 유지) |
| `test_no_keyword_functions` | `_keyword_match`, `_needs_llm_fallback` 함수가 존재하지 않음을 확인 |
| `test_domain_config_no_keywords` | `DBDomainConfig`에 `keywords` 필드가 없음을 확인 |
| `test_result_merger_multi_db` | 멀티 DB 결과 병합 및 소스 DB 태깅 검증 |

## 5. 구현 순서

1. `src/routing/domain_config.py` - `keywords` 필드를 `aliases` 필드로 교체
2. `src/state.py` - `user_specified_db` 필드 추가
3. `src/prompts/semantic_router.py` - LLM 프롬프트 전면 개편 (동적 템플릿)
4. `src/routing/semantic_router.py` - 키워드 함수 삭제, LLM 전용 로직, 동적 프롬프트 구성
5. `src/nodes/result_merger.py` - DB별 결과 요약 생성
6. 테스트 업데이트 및 신규 테스트 작성

## 6. 리스크 및 고려사항

| 리스크 | 대응 |
|--------|------|
| LLM 호출 실패 시 폴백 없음 | 키워드 폴백을 제거하므로 LLM 실패 시 명확한 에러 메시지를 사용자에게 반환 |
| LLM 응답 지연 | 라우팅 판단 시간 제한을 5초로 설정. 초과 시 타임아웃 에러 반환 |
| LLM이 잘못된 DB를 선택 | DB description을 충분히 상세하게 작성. 테스트 케이스로 정확도 검증 |
| 사용자 직접 지정 시 오타 | aliases에 자주 사용되는 변형을 포함. LLM이 유사 표현도 인식 가능 |
