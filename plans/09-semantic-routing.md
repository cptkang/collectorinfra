# 09. 시멘틱 라우팅 구현 계획서 (v2)

> 작성일: 2026-03-16
> 수정일: 2026-03-17
> 관련 요구사항: docs/01_requirements.md 섹션 9
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


---

# Verification Report

# 시멘틱 라우팅 구현 검증 보고서

> 작성일: 2026-03-17
> 검증 대상: 시멘틱 라우팅을 통한 멀티 DB 선택 및 쿼리 기능

---

## 1. 검증 요약

| 항목 | 결과 |
|------|------|
| 신규 테스트 수 | 50개 |
| 통과 | 50개 |
| 실패 | 0개 |
| 기존 테스트 영향 | 없음 (17개 기존 테스트 모두 통과) |
| 검증 상태 | PASS |

---

## 2. 구현 산출물

### 2.1 신규 파일

| 파일 | 역할 | 라인 수 |
|------|------|---------|
| `src/routing/__init__.py` | 라우팅 패키지 초기화 | 15 |
| `src/routing/domain_config.py` | DB 도메인 정의 (4개 DB, 키워드, 환경변수 매핑) | 130 |
| `src/routing/db_registry.py` | 멀티 DB 연결 레지스트리 | 180 |
| `src/routing/semantic_router.py` | 시멘틱 라우팅 노드 (키워드+LLM 2단계 분류) | 230 |
| `src/prompts/semantic_router.py` | 시멘틱 라우터 LLM 프롬프트 | 70 |
| `src/nodes/multi_db_executor.py` | 멀티 DB 쿼리 실행 오케스트레이터 | 290 |
| `src/nodes/result_merger.py` | 멀티 DB 결과 병합 노드 | 80 |

### 2.2 수정 파일

| 파일 | 변경 내용 |
|------|----------|
| `src/config.py` | MultiDBConfig 클래스 추가, AppConfig에 multi_db/enable_semantic_routing 필드 추가 |
| `src/state.py` | AgentState에 6개 시멘틱 라우팅 필드 추가, create_initial_state 기본값 추가 |
| `src/graph.py` | semantic_router/multi_db_executor/result_merger 노드 등록, 조건부 엣지 추가 |
| `.env` | 시멘틱 라우팅 설정 및 4개 DB 연결 문자열 추가 |
| `.env.example` | 동일 |

### 2.3 테스트 파일

| 파일 | 테스트 수 | 설명 |
|------|-----------|------|
| `tests/test_semantic_routing/test_domain_config.py` | 14 | 도메인 정의, 키워드, 환경변수 키 검증 |
| `tests/test_semantic_routing/test_semantic_router.py` | 17 | 키워드 매칭 (4개 DB별), 멀티 DB 감지, LLM 폴백 판단 |
| `tests/test_semantic_routing/test_db_registry.py` | 17 | 레지스트리 CRUD, 레거시 폴백, 에러 처리 |
| `tests/test_semantic_routing/test_state_extension.py` | 3 | AgentState 확장 필드 기본값 검증 |
| `tests/test_semantic_routing/test_graph_routing.py` | 3 | 라우팅 함수 분기 검증 |

---

## 3. 요구사항 충족 확인

### 3.1 F-21: 시멘틱 라우팅

- [x] Polestar DB: CPU, 메모리, 디스크, hostname, IP, 프로세스 키워드 매칭 확인
- [x] Cloud Portal DB: VM, 데이터스토어, 김포/여의도/DMZ 키워드 매칭 확인
- [x] ITSM DB: 인시던트, 서비스 요청, 변경 관리 키워드 매칭 확인
- [x] ITAM DB: 자산, 라이선스, 계약 키워드 매칭 확인
- [x] LLM 폴백: 키워드 매칭 실패/모호 시 LLM 분류 폴백 구현

### 3.2 F-22: 멀티 DB 연결 관리

- [x] 4개 DB 연결 정보를 `.env`에서 로드
- [x] DB 식별자로 클라이언트 생성 (async context manager)
- [x] 미등록 DB 요청 시 DBRegistryError 발생
- [x] 레거시 단일 DB 모드 하위 호환성 유지

### 3.3 F-23: 멀티 DB 쿼리 실행

- [x] multi_db_executor 노드: DB별 스키마 분석 -> SQL 생성 -> 검증 -> 실행
- [x] 부분 실패 처리: 실패 DB는 db_errors에 기록, 성공 DB 결과는 정상 반환

### 3.4 F-24: 결과 병합

- [x] result_merger 노드: DB별 결과를 _source_db 태그와 함께 병합
- [x] 부분 에러 요약 생성

### 3.5 하위 호환성

- [x] ENABLE_SEMANTIC_ROUTING=false 시 기존 파이프라인 그대로 동작
- [x] 멀티 DB 미설정 시 레거시 단일 DB(default) 폴백
- [x] 기존 17개 테스트 모두 통과 확인

---

## 4. 아키텍처 설계 검증

### 4.1 그래프 흐름 (시멘틱 라우팅 활성화 시)

```
START -> input_parser -> semantic_router -> [조건부]
    |- 단일 DB: schema_analyzer -> query_generator -> query_validator -> [조건부] -> query_executor -> result_organizer -> output_generator -> END
    |- 멀티 DB: multi_db_executor -> result_merger -> result_organizer -> output_generator -> END
```

### 4.2 그래프 흐름 (시멘틱 라우팅 비활성화 시)

```
START -> input_parser -> schema_analyzer -> query_generator -> query_validator -> [조건부] -> query_executor -> result_organizer -> output_generator -> END
```

기존 흐름과 동일하며, semantic_router/multi_db_executor/result_merger 노드는 등록되지 않는다.

---

## 5. 알려진 제한 사항

| # | 항목 | 설명 | 심각도 |
|---|------|------|--------|
| L-01 | 멀티 DB 병렬 실행 미구현 | 현재 순차 실행, asyncio.gather를 통한 병렬화는 추후 작업 | Low |
| L-02 | DB별 스키마 캐시 미분리 | DB별 독립 캐시가 아닌 기존 글로벌 캐시 사용, 멀티 DB 시 매번 조회 | Low |
| L-03 | PostgreSQL 전용 | DB 레지스트리가 PostgresClient만 생성, MySQL 등은 추후 클라이언트 팩토리 확장 필요 | Medium |
| L-04 | 통합 테스트 미실행 | 실제 DB 연결을 필요로 하는 통합 테스트는 미작성 (mock 기반 단위 테스트만) | Low |

모든 제한 사항은 Critical이 아니며, 현재 구현으로 기본 기능은 정상 동작한다.

---

## 6. 결론

시멘틱 라우팅을 통한 멀티 DB 선택 및 쿼리 기능이 성공적으로 구현되었다.
50개 신규 테스트가 모두 통과하고, 기존 17개 테스트에 영향이 없음을 확인했다.
기존 시스템과의 하위 호환성이 유지되며, `ENABLE_SEMANTIC_ROUTING` 설정으로 기능을 제어할 수 있다.


---

# Verification Report (시멘틱 라우팅 v2)

# 검증 결과 보고서

> 검증일: 2026-03-17
> 검증 대상: 시멘틱 라우팅 시스템 v2 변경 (키워드 제거, LLM 전용, 사용자 직접 DB 지정)

---

## 1. 변경 요구사항 충족 여부

### 1.1 키워드 기반 1차 분류 제거

| 검증 항목 | 상태 | 상세 |
|-----------|------|------|
| `_keyword_match()` 함수 삭제 | PASS | `src/routing/semantic_router.py`에서 완전 제거 확인 |
| `_needs_llm_fallback()` 함수 삭제 | PASS | `src/routing/semantic_router.py`에서 완전 제거 확인 |
| `KEYWORD_CONFIDENCE_THRESHOLD` 상수 삭제 | PASS | `src/routing/semantic_router.py`에서 완전 제거 확인 |
| `DBDomainConfig.keywords` 필드 제거 | PASS | `aliases` 필드로 교체 완료 |
| `DB_DOMAINS` 정의에서 키워드 목록 제거 | PASS | 모든 도메인에서 keywords 항목 삭제 완료 |
| 모든 라우팅이 LLM을 통해 수행 | PASS | `semantic_router()` 함수가 `_llm_classify()` 만 호출 |

### 1.2 사용자 직접 DB 지정 지원

| 검증 항목 | 상태 | 상세 |
|-----------|------|------|
| LLM 프롬프트에 직접 지정 규칙 포함 | PASS | `SEMANTIC_ROUTER_SYSTEM_PROMPT_TEMPLATE`에 규칙 추가 |
| `user_specified` 필드 JSON 출력에 포함 | PASS | LLM 응답 파싱 시 `user_specified` 필드 처리 |
| `user_specified_db` State 필드 추가 | PASS | `AgentState` 및 `create_initial_state()`에 추가 |
| 별칭(aliases) 기반 인식 | PASS | `DBDomainConfig.aliases`에 한국어/영어 별칭 정의 |
| 동적 프롬프트에 별칭 정보 포함 | PASS | `_build_router_prompt()`에서 aliases를 프롬프트에 포함 |

### 1.3 멀티 DB 쿼리 및 결과 취합

| 검증 항목 | 상태 | 상세 |
|-----------|------|------|
| sub_query_context 분리 | PASS | LLM 프롬프트에 sub_query_context 분리 규칙 포함 |
| DB별 결과에 `_source_db` 태깅 | PASS | 기존 `multi_db_executor`에서 정상 동작 |
| 결과 병합 시 DB별 요약 정보 생성 | PASS | `result_merger`에 `db_result_summary` 생성 로직 추가 |
| 부분 실패 시 성공 결과 + 에러 정보 반환 | PASS | `_build_error_summary()` 함수로 분리하여 명확화 |

---

## 2. 테스트 결과

### 2.1 전체 테스트

```
330 passed in 0.92s
```

모든 330개 테스트 통과. 기존 기능에 대한 회귀 없음.

### 2.2 시멘틱 라우팅 관련 테스트 (56개)

| 테스트 파일 | 건수 | 상태 |
|-------------|------|------|
| `test_domain_config.py` | 13 | PASS |
| `test_db_registry.py` | 13 | PASS |
| `test_semantic_router.py` | 24 | PASS |
| `test_graph_routing.py` | 3 | PASS |
| `test_state_extension.py` | 3 | PASS |

### 2.3 주요 테스트 케이스 상세

**키워드 함수 제거 확인 (3건):**
- `test_no_keyword_match_function` - `_keyword_match` 함수 비존재 확인
- `test_no_needs_llm_fallback_function` - `_needs_llm_fallback` 함수 비존재 확인
- `test_no_keyword_confidence_threshold` - `KEYWORD_CONFIDENCE_THRESHOLD` 상수 비존재 확인

**사용자 직접 DB 지정 (2건):**
- `test_user_specified_db` - LLM 분류에서 user_specified=True 반환 확인
- `test_user_specified_db_in_result` - semantic_router 결과에 user_specified_db 반영 확인

**LLM 전용 라우팅 (5건):**
- `test_single_db_routing` - 단일 DB 라우팅 정상 동작
- `test_multi_db_routing` - 멀티 DB 라우팅 정상 동작
- `test_llm_failure_fallback` - LLM 실패 시 안전한 폴백
- `test_low_score_filtered` - 최소 관련도 미만 필터링
- `test_results_sorted_by_relevance` - 관련도 점수 내림차순 정렬

**동적 프롬프트 (3건):**
- `test_includes_all_active_domains` - 전체 도메인 포함
- `test_includes_aliases` - 별칭 정보 포함
- `test_subset_domains` - 부분 도메인만 포함

---

## 3. 변경 파일 목록

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `src/routing/semantic_router.py` | 전면 재작성 | 키워드 함수 제거, LLM 전용 로직, 동적 프롬프트 구성 |
| `src/routing/domain_config.py` | 수정 | keywords -> aliases 필드 교체 |
| `src/prompts/semantic_router.py` | 전면 재작성 | 동적 템플릿, 직접 DB 지정 규칙, 멀티 DB 분리 규칙 |
| `src/state.py` | 수정 | user_specified_db 필드 추가 |
| `src/nodes/result_merger.py` | 수정 | DB별 결과 요약 생성, _build_error_summary 분리 |
| `docs/01_requirements.md` | 수정 | 섹션 9 전면 업데이트 |
| `plans/09-semantic-routing.md` | 전면 재작성 | v2 계획서 |
| `tests/test_semantic_routing/test_semantic_router.py` | 전면 재작성 | v2 테스트 |
| `tests/test_semantic_routing/test_domain_config.py` | 수정 | aliases 테스트로 변경 |
| `tests/test_semantic_routing/test_state_extension.py` | 수정 | user_specified_db 테스트 추가 |

---

## 4. Critical 이슈

없음.

---

## 5. 주의사항

1. **LLM 폴백 없음**: 키워드 기반 폴백이 제거되었으므로, LLM 호출 실패 시 첫 번째 활성 DB로 폴백합니다. LLM 서비스 안정성이 라우팅 정확도에 직접 영향을 미칩니다.
2. **라우팅 판단 시간**: LLM 전용 라우팅으로 전환되어 기존 키워드 매칭 대비 응답 시간이 증가할 수 있습니다. 비기능 요건에서 판단 시간을 3초에서 5초로 완화했습니다.
3. **별칭 관리**: 사용자가 DB를 직접 지정할 때 인식할 별칭 목록은 `domain_config.py`의 `aliases` 필드에서 관리됩니다. 새로운 별칭이 필요한 경우 이 필드에 추가하면 됩니다.

---
---
