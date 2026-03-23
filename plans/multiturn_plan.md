# 멀티턴 대화 및 Human-in-the-loop 구현 계획

## 1. 개요

### 1.1 목적

현재 시스템은 **단일 턴(Stateless)** 방식으로 동작한다. 매 API 요청마다 `create_initial_state()`로 State를 초기화하므로, 이전 대화 맥락이 완전히 유실된다. 이를 **멀티턴(Stateful)** 방식으로 전환하여:

1. 이전 대화 맥락을 유지한 후속 질의를 지원한다
2. 사용자 확인이 필요한 작업(SQL 승인, 유사단어 재활용)에서 대화 흐름이 끊기지 않도록 한다
3. LangGraph의 네이티브 체크포인트 기능을 활용하여 상태를 영속화한다

### 1.2 현재 상태 (AS-IS)

```
사용자 → API 요청 → create_initial_state() → 그래프 실행 → 응답
         (매번 빈 State로 시작, 이전 대화 맥락 없음)
```

**구체적 문제:**

| # | 문제 | 현재 동작 | 기대 동작 | 출처 |
|---|------|----------|----------|------|
| 1 | 후속 질의 | "아까 조회한 서버 목록에서 CPU 80% 이상만 보여줘" → 실패 (이전 결과 없음) | 이전 쿼리 결과를 참조하여 추가 필터링 | spec §F-10 |
| 2 | SQL 승인 | SQL 생성 후 즉시 실행 (사용자 확인 없음) | SQL을 보여주고 승인/수정/거부 선택 대기 | spec §F-14 |
| 3 | 유사단어 재활용 (Smart Synonym Reuse) | `pending_synonym_reuse` State에만 존재 → 다음 요청 시 유실 | 체크포인트에 저장하여 다음 턴에서 복원 | schemacache §3.2, §10.5.1 |
| 4 | 결과 후속 조작 | "결과를 Excel로 만들어줘" → 실패 (이전 결과 없음) | 이전 query_results를 사용하여 Excel 생성 | spec §F-10 |
| 5 | 유사어 등록 승인 | LLM 추론 매핑 후 등록 질문 → 다음 요청 시 `pending_synonym_registrations` 유실 | "전체 등록" / "1, 3 등록" 응답으로 Redis synonyms에 등록 | xls_plan §3.6.2 |
| 6 | 캐시 관리 연속 작업 | "캐시 생성해줘" → "컬럼 설명도 생성해줘" → "유사 단어도 만들어줘" — 각 턴이 독립적 | 이전 캐시 작업 결과를 참조하여 연속 처리 | schemacache §4.5 |
| 7 | DB 설명 조회→수정 | "DB 목록 보여줘" → "polestar 설명을 변경해줘" — 맥락 단절 | 이전 조회 결과를 참조한 자연스러운 수정 | schemacache §2.2 |

### 1.2.1 schemacache_plan에서 식별된 멀티턴 의존 기능

schemacache_plan.md를 분석하여 **사용자 의향 확인이 필요한 멀티턴 대화 흐름** 전체를 정리한다.

#### A. Smart Synonym Reuse (schemacache §3.2, §10.5.1)

글로벌 사전에 없는 새 컬럼의 유사 단어를 생성할 때, LLM이 기존 유사 컬럼을 탐색하여 재활용을 제안하는 2턴 대화.

```
[턴 1] 사용자: "server_name 유사 단어를 생성해줘"
  → cache_management: 글로벌 사전에 없음 → LLM이 "hostname" 유사 발견
  → 응답: "hostname과 유사합니다. 재활용/새로 생성/병합 중 선택하세요"
  → State: pending_synonym_reuse = {target: "server_name", suggestions: [{column: "hostname", ...}]}

[턴 2] 사용자: "재활용" 또는 "새로 생성" 또는 "병합"
  → 체크포인트에서 pending_synonym_reuse 복원
  → semantic_router: pending 감지 → cache_management 강제 라우팅
  → cache_management: reuse-synonym 처리
  → 응답: "hostname의 유사 단어를 server_name에 재활용했습니다."
```

**현재 상태**: 단일 턴 내에서 제안은 동작하지만, 턴 2에서 `pending_synonym_reuse`가 유실되어 응답 처리 불가.

#### B. 유사어 등록 플로우 (xls_plan §3.6.2)

Excel/Word 양식 처리 후 LLM 추론 매핑이 발생한 경우, 해당 매핑을 Redis synonyms에 등록할지 사용자에게 확인하는 멀티턴 대화.

```
[턴 1] 사용자: "이 Excel에 데이터를 채워줘" (양식 업로드)
  → field_mapper: LLM 추론 매핑 3건 발생
  → 응답: "Excel을 생성했습니다. LLM이 추론한 매핑 3건:
    1. CPU 사용률 → cpu_metrics.usage_pct
    2. 디스크 잔여 → disk_metrics.free_gb
    3. 네트워크 대역폭 → network_metrics.bandwidth_mbps
    → 유사어로 등록하시겠습니까? (전체 등록 / 번호 선택 / 건너뛰기)"
  → State: pending_synonym_registrations = [{index:1, field:"CPU 사용률", column:"cpu_metrics.usage_pct", db_id:"polestar"}, ...]

[턴 2] 사용자: "1, 3 등록" 또는 "전체 등록" 또는 "건너뛰기"
  → 체크포인트에서 pending_synonym_registrations 복원
  → 선택된 항목만 Redis synonyms에 등록 (source: "operator")
  → 글로벌 사전에도 동기화
  → 응답: "2건 등록: CPU 사용률→usage_pct, 네트워크 대역폭→bandwidth_mbps"
```

**현재 상태**: `pending_synonym_registrations`가 State에 있지만, 다음 턴에서 유실됨. 등록 의도 파싱 로직 미구현.

#### C. 캐시 관리 연속 대화 (schemacache §4.5)

사용자가 자연어로 캐시를 관리하는 과정에서, 이전 턴의 결과를 참조한 후속 작업이 필요한 경우.

```
[턴 1] 사용자: "polestar DB의 스키마 캐시를 생성해줘"
  → 응답: "캐시 생성 완료. 테이블 12개, fingerprint: a3f2c1..."

[턴 2] 사용자: "컬럼 설명도 생성해줘"
  → 이전 턴에서 polestar를 대상으로 했으므로, db_id를 자동 추론
  → 응답: "polestar의 컬럼 설명 85개 생성 완료"

[턴 3] 사용자: "유사 단어도 만들어줘"
  → 동일하게 polestar 대상으로 자동 추론
  → 응답: "polestar의 유사 단어 85개 컬럼 생성 완료"

[턴 4] 사용자: "hostname의 유사 단어를 보여줘"
  → 응답: "[글로벌] 서버명, 호스트명... [polestar] servers.hostname: ..."
```

**현재 상태**: 각 턴이 독립적이라 "컬럼 설명도 생성해줘"에서 db_id를 추론 불가. 매번 DB를 명시해야 함.

#### D. DB 설명 관리 대화 (schemacache §2.2)

```
[턴 1] 사용자: "어떤 DB가 있어?"
  → 응답: "polestar: 인프라 모니터링 DB / cloud_portal: 클라우드 포탈 DB"

[턴 2] 사용자: "polestar 설명을 '서버 사양 및 성능 메트릭 관리 DB'로 변경해줘"
  → 이전 턴에서 DB 목록을 조회했으므로, 맥락적으로 자연스러운 후속 작업
  → 응답: "polestar 설명을 변경했습니다."
```

**현재 상태**: 단일 턴으로도 동작 가능하나, "위의" "아까" 등 참조 표현 사용 시 실패.

### 1.3 목표 상태 (TO-BE)

```
사용자 → API 요청 (thread_id 포함)
  ↓
thread_id로 체크포인트에서 이전 State 복원 (대화 히스토리 포함)
  ↓
messages에 새 사용자 메시지 추가
  ↓
그래프 실행 (이전 맥락 참조)
  ↓
State를 체크포인트에 저장
  ↓
응답 반환
```

### 1.4 단일 턴 / 멀티턴 처리 방식

**통합 단일 코드 경로**를 사용한다. "단일 턴 모드"와 "멀티턴 모드"를 별도로 분기하지 않는다. 모든 요청이 동일한 그래프를 통과하며, 단일 턴은 멀티턴의 **특수한 경우(첫 턴)**일 뿐이다.

```
[모든 요청]
  ↓
thread_id 확인
  ├─ thread_id 없음 → 새 UUID 발급 → 첫 턴 (= 단일 턴과 동일)
  └─ thread_id 있음 → 체크포인트 조회
       ├─ 체크포인트 없음 → 첫 턴 (full initial state)
       └─ 체크포인트 있음 → 후속 턴 (delta input만 전달)
  ↓
동일한 그래프 실행: context_resolver → input_parser → ...
  ↓
context_resolver가 자동 판별:
  - turn_count=1 → conversation_context=None (단일 턴과 동일하게 동작)
  - turn_count>1 → 이전 맥락 추출 (후속 질의 처리)
```

**핵심 — API 레이어의 입력 구성 방식 차이:**

| 구분 | 조건 | API가 graph.ainvoke()에 전달하는 입력 |
|------|------|--------------------------------------|
| **첫 턴** | 체크포인트 없음 | `create_initial_state()` — 전체 필드 초기화 |
| **후속 턴** | 체크포인트 있음 | **delta input만** — `{"user_query": "...", "messages": [HumanMessage(...)]}` |

후속 턴에서 `create_initial_state()`를 호출하면 체크포인트의 이전 State가 빈 값으로 덮어쓰기되므로, **반드시 변경된 필드(user_query, messages)만 전달**해야 한다. LangGraph의 `add_messages` reducer가 messages를 누적 처리하고, 나머지 필드는 체크포인트에서 복원된 값을 유지한다.

```python
# API 레이어 의사코드
if checkpoint_exists(thread_id):
    # 후속 턴: delta만 전달 (체크포인트가 나머지 복원)
    input_state = {
        "user_query": body.query,
        "messages": [HumanMessage(content=body.query)],
    }
else:
    # 첫 턴: 전체 초기화
    input_state = create_initial_state(
        user_query=body.query,
        thread_id=thread_id,
    )

result = await graph.ainvoke(input_state, {"configurable": {"thread_id": thread_id}})
```

> **왜 분기하지 않고 통합하는가?**
> - 그래프 자체는 동일 — `context_resolver`가 첫 턴/후속 턴을 자동 판별
> - 분기점은 API 레이어의 **입력 구성**뿐 — 첫 턴은 full state, 후속 턴은 delta
> - 단일 턴 요청(thread_id 미제공)은 매번 새 UUID → 체크포인트 없음 → 첫 턴 경로 → 기존 동작 100% 동일

### 1.5 구현 범위

| 기능 | 출처 | 우선순위 | 멀티턴 의존도 |
|------|------|---------|-------------|
| 멀티턴 대화 기반 인프라 (체크포인트, messages, thread_id) | spec §F-10, §6.3 | **P0** (핵심) | 전체 기반 |
| 후속 질의 맥락 참조 (데이터 조회 → 추가 필터/Excel 변환) | spec §F-10 | **P0** (핵심) | 이전 SQL/결과 참조 |
| `pending_synonym_reuse` 재활용 대화 | schemacache §3.2, §10.5.1 | **P1** (중요) | 2턴 대화 (제안 → 응답) |
| `pending_synonym_registrations` 유사어 등록 대화 | xls_plan §3.6.2 | **P1** (중요) | 2턴 대화 (제안 → 등록 응답) |
| Human-in-the-loop — SQL 승인 | spec §F-14 | **P1** (중요) | 2턴 대화 (SQL 제시 → 승인/거부) |
| 캐시 관리 연속 대화 (db_id 자동 추론) | schemacache §4.5 | **P2** (개선) | 이전 턴 db_id 참조 |
| 대화 히스토리 관리 (요약/압축/TTL) | — | **P2** (개선) | 히스토리 크기 제어 |

---

## 2. 아키텍처 설계

### 2.1 LangGraph 체크포인트 기반 상태 영속화

LangGraph는 **체크포인트(Checkpointer)** 를 통해 그래프 실행 상태를 자동으로 저장/복원한다. 동일한 `thread_id`로 그래프를 재실행하면 마지막 상태에서 이어서 동작한다.

```
┌─────────────────────────────────────────────┐
│  LangGraph Compiled Graph                   │
│                                             │
│  graph.ainvoke(state, {"configurable":      │
│    {"thread_id": "session-123"}})           │
│                                             │
│  [첫 실행] State 초기화 → 노드 실행 → 저장   │
│  [재실행] 체크포인트에서 복원 → 노드 실행 → 저장│
└──────────────────┬──────────────────────────┘
                   │
         ┌─────────▼──────────┐
         │   Checkpointer     │
         │  ┌───────────────┐ │
         │  │ Dev: SQLite   │ │
         │  │ Prod: Postgres│ │
         │  └───────────────┘ │
         └────────────────────┘
```

### 2.2 대화 히스토리 모델

대화 히스토리를 LangChain의 `BaseMessage` 체계로 관리한다.

```python
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

# State에 messages 필드 추가
messages: list[BaseMessage]  # 대화 히스토리

# 턴 구조:
#   HumanMessage("서버 CPU 사용률 80% 이상 목록 보여줘")
#   AIMessage("다음은 CPU 80% 이상 서버 목록입니다: ...")
#   HumanMessage("그 중에서 메모리도 90% 이상인 것만 보여줘")
#   AIMessage("CPU 80%↑ + 메모리 90%↑ 서버는 다음과 같습니다: ...")
```

### 2.3 그래프 흐름 변경

현재 그래프는 `START → input_parser → ...` 로 시작하지만, 멀티턴에서는 **대화 컨텍스트를 먼저 분석**하는 단계가 필요하다.

```
[변경 전]
START → input_parser → field_mapper → semantic_router → ...

[변경 후]
START → context_resolver → input_parser → field_mapper → semantic_router → ...
                ↑
    이전 대화에서 참조할 맥락 추출:
    - 이전 쿼리 결과 요약
    - 이전 SQL
    - 이전 스키마 정보
    - pending 상태 (synonym_reuse, approval 등)
```

### 2.4 Human-in-the-loop 흐름

LangGraph의 `interrupt_before` / `interrupt_after`를 사용하여 특정 노드에서 그래프 실행을 중단하고, 사용자 응답을 받은 후 재개한다.

```
[SQL 승인 흐름]

사용자: "서버 CPU 80% 이상 목록을 보여줘"
  ↓
input_parser → schema_analyzer → query_generator → [INTERRUPT]
  ↓
API 응답: {"type": "approval_required", "sql": "SELECT ...", "thread_id": "abc"}
  ↓
사용자: {"action": "approve"} 또는 {"action": "reject"} 또는 {"action": "modify", "sql": "..."}
  ↓
graph.ainvoke({"approval_action": "approve"}, {"thread_id": "abc"})
  ↓
query_validator → query_executor → result_organizer → output_generator
  ↓
최종 응답
```

---

## 3. State 변경

### 3.1 새 필드 추가

```python
class AgentState(TypedDict):
    # === (기존 필드 모두 유지) ===
    ...

    # === [Phase 3] 멀티턴 대화 ===
    messages: Annotated[list[BaseMessage], add_messages]  # 대화 히스토리 (LangGraph reducer)
    thread_id: Optional[str]                              # 세션 식별자
    conversation_context: Optional[dict]                  # context_resolver가 추출한 이전 맥락
    # {
    #   "previous_sql": "SELECT ...",
    #   "previous_results_summary": "15개 서버 ...",
    #   "previous_tables": ["servers", "cpu_metrics"],
    #   "turn_count": 3,
    # }

    # === [Phase 3] Human-in-the-loop ===
    awaiting_approval: bool                    # 사용자 승인 대기 여부
    approval_context: Optional[dict]           # 승인 요청 컨텍스트
    # {
    #   "type": "sql_approval" | "synonym_reuse",
    #   "sql": "SELECT ...",  (sql_approval 시)
    #   "suggestions": [...],  (synonym_reuse 시)
    # }
    approval_action: Optional[str]             # 사용자 승인 응답 ("approve"|"reject"|"modify")
    approval_modified_sql: Optional[str]       # 수정된 SQL (modify 시)
```

### 3.2 `messages` 필드의 Annotated reducer

LangGraph의 `add_messages` reducer를 사용하면 각 노드가 반환하는 메시지가 기존 messages에 **추가(append)** 된다. 덮어쓰기가 아닌 누적 방식이다.

```python
from typing import Annotated
from langgraph.graph import add_messages

messages: Annotated[list[BaseMessage], add_messages]
```

### 3.3 `create_initial_state` 변경

```python
def create_initial_state(
    user_query: str,
    uploaded_file: Optional[bytes] = None,
    file_type: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> AgentState:
    return AgentState(
        # ... (기존 필드)
        messages=[HumanMessage(content=user_query)],
        thread_id=thread_id,
        conversation_context=None,
        awaiting_approval=False,
        approval_context=None,
        approval_action=None,
        approval_modified_sql=None,
    )
```

---

## 4. 신규 노드: `context_resolver`

### 4.1 역할

첫 번째 노드로 실행되어, 이전 대화 맥락을 분석하고 현재 질의에 필요한 컨텍스트를 추출한다.

### 4.2 동작

```python
async def context_resolver(state: AgentState, *, llm, app_config) -> dict:
    messages = state.get("messages", [])
    turn_count = len([m for m in messages if isinstance(m, HumanMessage)])

    # 첫 턴이면 맥락 없음
    if turn_count <= 1:
        return {"conversation_context": None, "current_node": "context_resolver"}

    # 이전 대화에서 맥락 추출
    previous_sql = state.get("generated_sql", "")
    previous_results = state.get("query_results", [])
    previous_tables = state.get("relevant_tables", [])
    previous_db_id = state.get("active_db_id")  # 캐시 관리 연속 대화용

    # pending 상태 감지
    pending_reuse = state.get("pending_synonym_reuse")
    pending_regs = state.get("pending_synonym_registrations")

    # 이전 결과 요약 (LLM 호출 없이 간단 요약)
    results_summary = ""
    if previous_results:
        results_summary = f"{len(previous_results)}건 조회됨"
        if len(previous_results) > 0:
            cols = list(previous_results[0].keys())
            results_summary += f", 컬럼: {', '.join(cols[:5])}"

    context = {
        "previous_sql": previous_sql,
        "previous_results_summary": results_summary,
        "previous_result_count": len(previous_results),
        "previous_tables": previous_tables,
        "previous_db_id": previous_db_id,
        "turn_count": turn_count,
        "has_pending_synonym_reuse": pending_reuse is not None,
        "has_pending_synonym_registrations": pending_regs is not None and len(pending_regs or []) > 0,
        "pending_synonym_reg_count": len(pending_regs) if pending_regs else 0,
    }

    return {
        "conversation_context": context,
        "current_node": "context_resolver",
    }
```

> **`previous_db_id` 활용 (schemacache §4.5 캐시 관리 연속 대화)**: 이전 턴에서 "polestar DB 캐시 생성"을 한 후 "컬럼 설명도 생성해줘"라고 하면, `previous_db_id`가 "polestar"이므로 `cache_management` 노드가 자동으로 polestar를 대상으로 처리한다. LLM 프롬프트에 이전 db_id를 포함하여 추론 정확도를 높인다.

### 4.3 input_parser 연동

`input_parser`가 `conversation_context`를 참조하여 후속 질의를 해석한다.

```
사용자: "그 중에서 메모리 90% 이상만"
  ↓
context_resolver: previous_sql = "SELECT ... WHERE cpu > 80"
  ↓
input_parser: conversation_context를 LLM 프롬프트에 포함
  → "이전 질의에서 CPU 80% 이상 서버를 조회했습니다.
     현재 질의: '그 중에서 메모리 90% 이상만'
     → 이전 조건에 메모리 조건 추가"
  ↓
query_generator: 이전 SQL을 기반으로 WHERE 조건 추가
```

---

## 5. Human-in-the-loop: SQL 승인

### 5.1 설정

```python
class AppConfig(BaseSettings):
    ...
    enable_sql_approval: bool = False  # SQL 승인 기능 활성화
```

### 5.2 그래프 변경 — `interrupt_before`

```python
def build_graph(config: AppConfig):
    ...
    # SQL 승인 활성화 시: query_executor 전에 interrupt
    if config.enable_sql_approval:
        graph.add_node("approval_gate", approval_gate)
        # query_validator → approval_gate → query_executor
        # approval_gate에서 interrupt_before 설정
    ...

    compiled = graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["approval_gate"] if config.enable_sql_approval else [],
    )
```

### 5.3 `approval_gate` 노드

```python
async def approval_gate(state: AgentState) -> dict:
    """SQL 승인 게이트.

    validation 통과 후, 실행 전에 사용자 승인을 요청한다.
    interrupt_before에 의해 이 노드 진입 시 그래프가 중단된다.

    재개 시 approval_action을 읽어 분기한다.
    """
    action = state.get("approval_action")

    if action is None:
        # 첫 진입: 승인 요청 상태 설정
        return {
            "awaiting_approval": True,
            "approval_context": {
                "type": "sql_approval",
                "sql": state["generated_sql"],
                "validation_result": state["validation_result"],
            },
            "final_response": (
                f"다음 SQL을 실행하시겠습니까?\n\n"
                f"```sql\n{state['generated_sql']}\n```\n\n"
                f"- 승인: \"실행\" 또는 \"approve\"\n"
                f"- 거부: \"취소\" 또는 \"reject\"\n"
                f"- 수정: 수정된 SQL을 직접 입력"
            ),
            "current_node": "approval_gate",
        }

    elif action == "approve":
        return {
            "awaiting_approval": False,
            "approval_context": None,
            "current_node": "approval_gate",
        }

    elif action == "reject":
        return {
            "awaiting_approval": False,
            "approval_context": None,
            "final_response": "쿼리 실행이 취소되었습니다.",
            "current_node": "approval_gate",
        }

    elif action == "modify":
        modified_sql = state.get("approval_modified_sql", "")
        return {
            "awaiting_approval": False,
            "approval_context": None,
            "generated_sql": modified_sql,
            "current_node": "approval_gate",
        }
```

### 5.4 API 흐름

```
[1] POST /api/v1/query
    body: {"query": "서버 목록", "thread_id": "session-1"}
    → 그래프 실행, approval_gate에서 interrupt
    → 응답: {"status": "awaiting_approval", "sql": "SELECT ...", "thread_id": "session-1"}

[2] POST /api/v1/query/approve
    body: {"thread_id": "session-1", "action": "approve"}
    → graph.ainvoke({"approval_action": "approve"}, {"thread_id": "session-1"})
    → 그래프 재개, query_executor → ... → 최종 응답
    → 응답: {"status": "completed", "response": "...", ...}
```

### 5.5 `route_after_approval` 조건부 라우팅

```python
def route_after_approval(state: AgentState) -> str:
    if state.get("approval_action") == "reject":
        return END
    if state.get("approval_action") == "modify":
        return "query_validator"  # 수정된 SQL 재검증
    return "query_executor"  # 승인됨, 실행
```

---

## 6. pending 상태의 멀티턴 지원

체크포인트 기반 State 복원으로, 별도 Redis 저장 없이 모든 pending 상태가 자동 보존된다.

### 6.1 해결 원리

멀티턴이 구현되면, 동일 `thread_id`로 재호출 시 LangGraph 체크포인트가 이전 State를 통째로 복원한다. 따라서 `pending_synonym_reuse`, `pending_synonym_registrations` 등의 pending 상태가 별도 저장 로직 없이 자동 보존된다.

> schemacache_plan §10.5.1에서 제안한 "Redis에 `pending_synonym_reuse:{thread_id}` 키로 저장" 방식은 **불필요**해진다. 체크포인트가 더 일반적이고 안전한 해법이다.

### 6.2 `pending_synonym_reuse` — 유사단어 재활용 (schemacache §3.2)

```
[턴 1] 사용자: "server_name 유사 단어를 생성해줘"
  → cache_management: pending_synonym_reuse 설정
  → 응답: "hostname과 유사합니다. 재활용하시겠습니까?"
  → State가 체크포인트에 저장됨 (pending_synonym_reuse 포함)

[턴 2] 사용자: "재활용" (동일 thread_id)
  → 체크포인트에서 State 복원 (pending_synonym_reuse 있음)
  → context_resolver: pending 감지 → routing_hint 설정
  → semantic_router: pending_synonym_reuse 감지 → cache_management 강제 라우팅
  → cache_management: reuse-synonym 처리, pending_synonym_reuse = None
  → 응답: "hostname의 유사 단어를 server_name에 재활용했습니다."
```

### 6.3 `pending_synonym_registrations` — 유사어 등록 승인 (xls_plan §3.6.2)

Excel/Word 처리 후 LLM 추론 매핑이 발생하면, 사용자에게 유사어 등록 여부를 확인한다.

```
[턴 1] 사용자: "이 Excel에 데이터를 채워줘" (양식 업로드)
  → field_mapper: LLM 추론 매핑 3건
  → output_generator: 매핑 내역 표시 + 유사어 등록 질문
  → State: pending_synonym_registrations = [
      {index: 1, field: "CPU 사용률", column: "cpu_metrics.usage_pct", db_id: "polestar"},
      {index: 2, field: "디스크 잔여", column: "disk_metrics.free_gb", db_id: "polestar"},
      {index: 3, field: "네트워크 대역폭", column: "network_metrics.bandwidth_mbps", db_id: "polestar"},
    ]
  → 응답: "Excel 생성 완료. LLM 추론 매핑:
    1. CPU 사용률 → cpu_metrics.usage_pct
    2. 디스크 잔여 → disk_metrics.free_gb
    3. 네트워크 대역폭 → network_metrics.bandwidth_mbps
    유사어로 등록하시겠습니까? (전체 등록 / 번호 선택 / 건너뛰기)"
  → 체크포인트 저장

[턴 2] 사용자: "1, 3 등록" (동일 thread_id)
  → 체크포인트에서 State 복원 (pending_synonym_registrations 있음)
  → context_resolver: pending_synonym_registrations 감지 → routing_hint 설정
  → input_parser: 유사어 등록 의도 파싱 (정규식: "(\d+(?:,\s*\d+)*)\s*번?\s*등록")
  → synonym_registrar 로직: 1번, 3번만 Redis에 등록
    - cache_mgr.add_synonyms("polestar", "cpu_metrics.usage_pct", ["CPU 사용률"], source="operator")
    - cache_mgr.add_synonyms("polestar", "network_metrics.bandwidth_mbps", ["네트워크 대역폭"], source="operator")
    - 글로벌 사전에도 동기화
  → pending_synonym_registrations = None
  → 응답: "2건 등록 완료: CPU 사용률→usage_pct, 네트워크 대역폭→bandwidth_mbps"
```

**지원하는 등록 패턴:**

| 사용자 입력 | 파싱 결과 | 동작 |
|-----------|----------|------|
| "전체 등록" / "모두 등록" | mode: "all" | 전체 항목 등록 |
| "1, 3 등록" | mode: "selective", indices: [1, 3] | 선택 항목만 등록 |
| "1번 등록" | mode: "selective", indices: [1] | 단건 등록 |
| "건너뛰기" / "등록 안 함" | mode: "skip" | 등록 없이 pending 해제 |

### 6.4 `semantic_router` 변경 — pending 우선 라우팅

```python
async def semantic_router(state, *, llm=None, app_config=None):
    ...
    # [우선순위 2] pending 상태 체크 (체크포인트에서 복원)
    # pending_synonym_reuse → cache_management 강제 라우팅
    pending_reuse = state.get("pending_synonym_reuse")
    if pending_reuse:
        logger.info("pending_synonym_reuse 감지, cache_management로 강제 라우팅")
        return {
            "target_databases": [],
            "is_multi_db": False,
            "active_db_id": None,
            "user_specified_db": None,
            "routing_intent": "cache_management",
            "current_node": "semantic_router",
        }

    # pending_synonym_registrations → synonym_registrar 라우팅
    pending_regs = state.get("pending_synonym_registrations")
    if pending_regs:
        logger.info("pending_synonym_registrations 감지, synonym_registrar로 라우팅")
        return {
            "target_databases": [],
            "is_multi_db": False,
            "active_db_id": None,
            "user_specified_db": None,
            "routing_intent": "synonym_registration",
            "current_node": "semantic_router",
        }

    # [우선순위 3] 활성 DB 없음 → 레거시 모드
    # [우선순위 4] LLM 분류 → data_query 또는 cache_management
    ...
```

### 6.5 context_resolver에서 pending 상태 요약

```python
async def context_resolver(state, *, llm, app_config) -> dict:
    ...
    # pending 상태 감지 — routing_hint에 반영
    pending_reuse = state.get("pending_synonym_reuse")
    pending_regs = state.get("pending_synonym_registrations")

    context = {
        "previous_sql": previous_sql,
        "previous_results_summary": results_summary,
        "previous_result_count": len(previous_results),
        "previous_tables": previous_tables,
        "previous_db_id": state.get("active_db_id"),  # 이전 턴의 DB → 연속 작업 시 자동 추론
        "turn_count": turn_count,
        "has_pending_synonym_reuse": pending_reuse is not None,
        "has_pending_synonym_registrations": pending_regs is not None and len(pending_regs) > 0,
    }
    ...
```

---

## 7. 체크포인트 저장소

### 7.1 개발 환경: SQLite

```python
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

checkpointer = AsyncSqliteSaver.from_conn_string("checkpoints.db")
```

### 7.2 프로덕션 환경: PostgreSQL

```python
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

checkpointer = AsyncPostgresSaver.from_conn_string(
    "postgresql://user:pass@host:5432/checkpoints"
)
```

### 7.3 설정 확장

```python
class AppConfig(BaseSettings):
    ...
    checkpoint_backend: Literal["memory", "sqlite", "postgres"] = "sqlite"
    checkpoint_db_url: str = "checkpoints.db"
    enable_sql_approval: bool = False
    conversation_max_turns: int = 20       # 대화 최대 턴 수
    conversation_ttl_hours: int = 24       # 대화 세션 유효 시간
```

### 7.4 `_create_checkpointer` 변경

현재 `_create_checkpointer_simple`이 `InMemorySaver()`만 반환한다. 이를 확장한다.

```python
async def _create_checkpointer(config: AppConfig):
    if config.checkpoint_backend == "sqlite":
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        return AsyncSqliteSaver.from_conn_string(config.checkpoint_db_url)
    elif config.checkpoint_backend == "postgres":
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        return AsyncPostgresSaver.from_conn_string(config.checkpoint_db_url)
    else:
        return InMemorySaver()
```

---

## 8. API 변경

### 8.1 기존 API 수정

#### `POST /api/v1/query`

```python
@router.post("/query")
async def process_query(request: Request, body: QueryRequest):
    graph = request.app.state.graph
    config = request.app.state.config
    query_id = str(uuid.uuid4())
    thread_id = body.thread_id or query_id  # 미제공 시 새 UUID → 단일 턴

    thread_config = {"configurable": {"thread_id": thread_id}}

    # ──────────────────────────────────────────────
    # 핵심: 첫 턴 vs 후속 턴 입력 구성 분기
    # ──────────────────────────────────────────────
    checkpoint_state = await _get_checkpoint_state(graph, thread_config)

    if checkpoint_state is not None:
        # ── 후속 턴 (체크포인트 존재) ──
        # delta input만 전달. 나머지 필드는 체크포인트에서 자동 복원.
        # messages는 add_messages reducer가 기존에 append.
        # user_query는 현재 질의로 덮어쓰기.

        if checkpoint_state.get("awaiting_approval"):
            # SQL 승인 대기 중 → approval 응답으로 처리
            input_state = {
                "user_query": body.query,
                "messages": [HumanMessage(content=body.query)],
                "approval_action": _parse_approval(body.query),
            }
        else:
            # 일반 후속 질의 (맥락 참조, pending 처리 등)
            input_state = {
                "user_query": body.query,
                "messages": [HumanMessage(content=body.query)],
            }
    else:
        # ── 첫 턴 (체크포인트 없음) ──
        # 전체 State 초기화. 단일 턴 요청도 여기로 진입.
        input_state = create_initial_state(
            user_query=body.query,
            uploaded_file=...,   # body에서 추출
            file_type=...,
            thread_id=thread_id,
        )

    # 그래프 실행 (첫 턴/후속 턴 모두 동일 그래프)
    result = await asyncio.wait_for(
        graph.ainvoke(input_state, thread_config),
        timeout=config.server.query_timeout,
    )

    # 응답 구성
    status = "awaiting_approval" if result.get("awaiting_approval") else "completed"
    turn_count = len([
        m for m in result.get("messages", [])
        if isinstance(m, HumanMessage)
    ])

    return QueryResponse(
        query_id=query_id,
        status=status,
        response=result.get("final_response", ""),
        thread_id=thread_id,
        awaiting_approval=result.get("awaiting_approval", False),
        approval_context=result.get("approval_context"),
        turn_count=turn_count,
        ...
    )
```

> **`_get_checkpoint_state` 구현**: LangGraph의 `graph.get_state(thread_config)`를 호출하여 체크포인트 존재 여부와 마지막 State를 확인한다. 체크포인트가 없으면 `None`을 반환한다.
>
> **왜 full state를 후속 턴에 전달하면 안 되는가?**
> `create_initial_state()`는 모든 필드를 빈 값으로 초기화한다. 이를 후속 턴에 전달하면 체크포인트의 `generated_sql`, `query_results`, `pending_synonym_reuse` 등 이전 맥락이 모두 빈 값으로 덮어쓰기된다. delta input만 전달하면 LangGraph가 체크포인트의 기존 값을 유지하고, 전달된 필드만 업데이트한다.

### 8.2 새 API 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| `POST` | `/api/v1/query/approve` | SQL 승인/거부/수정 |
| `GET` | `/api/v1/conversation/{thread_id}` | 대화 히스토리 조회 |
| `DELETE` | `/api/v1/conversation/{thread_id}` | 대화 세션 삭제 |
| `GET` | `/api/v1/conversations` | 활성 대화 세션 목록 |

### 8.3 응답 모델 확장

```python
class QueryResponse(BaseModel):
    query_id: str
    status: str  # "completed" | "awaiting_approval" | "error"
    response: str
    thread_id: Optional[str] = None            # 멀티턴용 세션 ID
    awaiting_approval: bool = False            # 승인 대기 여부
    approval_context: Optional[dict] = None    # 승인 컨텍스트 (sql 등)
    has_file: bool = False
    file_name: Optional[str] = None
    executed_sql: Optional[str] = None
    row_count: Optional[int] = None
    processing_time_ms: Optional[float] = None
    turn_count: Optional[int] = None           # 현재 대화 턴 수
```

---

## 9. input_parser 프롬프트 변경

멀티턴에서 `input_parser`가 이전 맥락을 참조할 수 있도록 프롬프트를 확장한다.

### 9.1 맥락 주입

```python
async def input_parser(state, *, llm, app_config):
    context = state.get("conversation_context")

    if context and context.get("turn_count", 0) > 1:
        # 후속 질의: 이전 맥락을 프롬프트에 포함
        context_prompt = (
            f"\n## 이전 대화 맥락\n"
            f"- 이전 SQL: {context.get('previous_sql', '없음')}\n"
            f"- 이전 결과: {context.get('previous_results_summary', '없음')}\n"
            f"- 사용된 테이블: {', '.join(context.get('previous_tables', []))}\n"
            f"- 대화 턴: {context['turn_count']}번째\n\n"
            f"사용자가 '그것', '아까', '위의', '그 중에서' 등 이전 대화를 참조하는 "
            f"표현을 사용하면, 이전 맥락을 활용하여 요구사항을 해석하세요.\n"
        )
        # 기존 프롬프트에 맥락 추가
        ...
```

### 9.2 대명사/참조 해결

| 사용자 입력 | 해석 |
|-----------|------|
| "그 중에서 메모리 90% 이상만" | 이전 결과에 WHERE memory > 90 추가 |
| "아까 결과를 Excel로 만들어줘" | 이전 query_results를 사용하여 Excel 생성 |
| "서버명만 보여줘" | 이전 쿼리에서 SELECT를 hostname만으로 변경 |
| "더 자세히" | 이전 쿼리에 컬럼 추가 또는 LIMIT 증가 |

---

## 10. 대화 히스토리 관리

### 10.1 히스토리 크기 제어

대화가 길어지면 LLM 컨텍스트 윈도우를 초과할 수 있다. 최근 N턴만 유지하거나, 오래된 대화를 요약한다.

```python
MAX_HISTORY_TURNS = 10  # 최근 10턴 유지

def _trim_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    """대화 히스토리를 최대 턴 수로 제한한다."""
    if len(messages) <= MAX_HISTORY_TURNS * 2:
        return messages
    # 최근 N*2개 메시지만 유지 (Human + AI 쌍)
    return messages[-(MAX_HISTORY_TURNS * 2):]
```

### 10.2 세션 만료

```python
# API 레벨에서 세션 TTL 관리
# thread_id별 마지막 접근 시간 추적
# conversation_ttl_hours 초과 시 체크포인트 삭제
```

---

## 11. 수정 파일 목록

### 11.1 수정 파일

| 파일 | 변경 내용 |
|------|-----------|
| `src/state.py` | `messages`, `thread_id`, `conversation_context`, HITL 필드 추가. `pending_synonym_registrations` 이미 존재 (xls_plan에서 추가됨) |
| `src/graph.py` | `context_resolver` 노드 추가, `approval_gate` 노드 추가, `synonym_registrar` 노드 추가, `interrupt_before` 설정, 체크포인터 비동기 지원, `route_after_semantic_router`에 `synonym_registration` 분기 추가 |
| `src/config.py` | `enable_sql_approval`, `conversation_max_turns`, `conversation_ttl_hours` 설정 추가 |
| `src/api/routes/query.py` | 체크포인트 기반 State 복원 로직, `approval` 처리, `thread_id` 응답 포함, 멀티턴 입력 구성 (이전 State merge) |
| `src/api/schemas.py` | `QueryResponse`에 `thread_id`, `awaiting_approval`, `approval_context`, `turn_count`, `pending_registrations` 추가 |
| `src/nodes/input_parser.py` | `conversation_context` 참조하여 후속 질의 맥락 주입. 유사어 등록 의도 파싱 ("전체 등록", "1, 3 등록") |
| `src/nodes/output_generator.py` | LLM 추론 매핑 시 `pending_synonym_registrations`에 항목 저장 + 등록 질문 포함 (xls_plan §3.6.1) |
| `src/routing/semantic_router.py` | pending 우선 라우팅: `pending_synonym_reuse` → cache_management, `pending_synonym_registrations` → synonym_registrar |
| `src/nodes/cache_management.py` | `conversation_context.previous_db_id` 참조하여 db_id 자동 추론 |
| `src/prompts/input_parser.py` | 이전 대화 맥락 포함 프롬프트 템플릿 추가 |
| `src/prompts/query_generator.py` | 이전 SQL 참조 프롬프트 추가 |
| `src/prompts/cache_management.py` | `conversation_context.previous_db_id` 활용 안내 추가 |

### 11.2 신규 파일

| 파일 | 설명 |
|------|------|
| `src/nodes/context_resolver.py` | 이전 대화 맥락 추출 노드. pending 상태 감지, previous_db_id/SQL/결과 요약 |
| `src/nodes/approval_gate.py` | SQL 승인 게이트 노드. LangGraph interrupt_before 활용 |
| `src/nodes/synonym_registrar.py` | 유사어 등록 처리 노드. `pending_synonym_registrations`에서 사용자 선택 항목을 Redis synonyms에 등록 |
| `src/api/routes/conversation.py` | 대화 세션 관리 API (히스토리 조회, 삭제) |

### 11.3 의존성 추가

| 패키지 | 용도 |
|--------|------|
| `langgraph-checkpoint-sqlite` | SQLite 체크포인트 (dev) |
| `langgraph-checkpoint-postgres` | PostgreSQL 체크포인트 (prod) |

---

## 12. 구현 순서

| 단계 | 작업 | 의존성 | 난이도 | 관련 기능 |
|------|------|--------|--------|----------|
| **1** | State 확장 (`messages`, `thread_id`, HITL 필드) | 없음 | 낮 | 전체 기반 |
| **2** | 체크포인터 변경 (SQLite async → 실제 동작 확인) | 단계 1 | 중 | 전체 기반 |
| **3** | `context_resolver` 노드 구현 (pending 감지 + 맥락 추출) | 단계 1 | 중 | 후속 질의, 캐시 연속 대화 |
| **4** | 그래프에 `context_resolver` 등록 + 엣지 | 단계 3 | 낮 | — |
| **5** | `input_parser` 프롬프트에 맥락 주입 + 유사어 등록 의도 파싱 | 단계 3 | 중 | 후속 질의, 유사어 등록 |
| **6** | `query_generator` 프롬프트에 이전 SQL 참조 추가 | 단계 3 | 낮 | 후속 질의 |
| **7** | API 변경 — thread_id 기반 State 복원, 응답 확장 | 단계 2 | 중 | 전체 멀티턴 |
| **8** | `semantic_router` — pending 우선 라우팅 (reuse + registrations) | 단계 2 | 중 | synonym 재활용, 유사어 등록 |
| **9** | `synonym_registrar` 노드 구현 + 그래프 분기 | 단계 8 | 중 | 유사어 등록 승인 (xls_plan) |
| **10** | `cache_management` — `previous_db_id` 자동 추론 | 단계 3 | 낮 | 캐시 연속 대화 |
| **11** | `approval_gate` 노드 구현 | 단계 1 | 중 | SQL 승인 |
| **12** | 그래프에 approval 분기 추가 (`interrupt_before`) | 단계 11 | 중 | SQL 승인 |
| **13** | approval API 엔드포인트 구현 | 단계 12 | 중 | SQL 승인 |
| **14** | 대화 세션 관리 API | 단계 7 | 낮 | 히스토리 관리 |
| **15** | 대화 히스토리 크기 제어 / 세션 만료 | 단계 7 | 낮 | 히스토리 관리 |
| **16** | 단위 테스트 | 단계 1~15 | 중 | — |
| **17** | 통합 테스트 (멀티턴 E2E, HITL E2E, synonym 등록 E2E) | 단계 16 | 높 | — |

---

## 13. 리스크 및 고려사항

### 13.1 체크포인트 크기

- `query_results`에 대량 데이터가 포함되면 체크포인트 크기가 커진다
- **대응**: `query_results`는 최근 턴 것만 유지하거나, 체크포인트 저장 시 결과를 요약본으로 교체

### 13.2 동시성

- 동일 `thread_id`에 동시 요청이 들어오면 충돌 가능
- **대응**: thread_id별 락(lock) 또는 LangGraph의 자체 직렬화에 의존

### 13.3 LLM 컨텍스트 윈도우

- 대화 히스토리가 길어지면 토큰 한도 초과
- **대응**: `MAX_HISTORY_TURNS` 제한 + 오래된 턴 요약 압축

### 13.4 기존 단일 턴 호환성

- `thread_id`가 없는 요청은 매번 새 UUID를 생성하여 기존과 동일하게 동작
- **대응**: 하위 호환 100% 보장

### 13.5 SSE 스트리밍과 HITL 조합

- `interrupt_before`로 중단 시 SSE 스트리밍 응답이 어떻게 종료되는지 확인 필요
- **대응**: 중단 시 `{"type": "approval_required", ...}` SSE 이벤트를 전송하고 스트림 종료

---

## 14. 테스트 계획

### 14.1 단위 테스트

| 테스트 | 대상 |
|--------|------|
| context_resolver 맥락 추출 | 이전 State에서 올바른 맥락 추출 + pending 감지 확인 |
| context_resolver previous_db_id | 이전 턴 db_id가 context에 포함되는지 |
| approval_gate 분기 | approve/reject/modify 각 경우 올바른 State 반환 |
| synonym_registrar 전체 등록 | pending에서 전체 항목 Redis 등록 확인 |
| synonym_registrar 선택 등록 | 지정 번호만 등록, 나머지 유지 확인 |
| messages reducer | 메시지 누적 정상 동작 확인 |
| input_parser 맥락 주입 | conversation_context가 프롬프트에 반영되는지 |
| input_parser 등록 의도 파싱 | "전체 등록", "1, 3 등록", "건너뛰기" 파싱 확인 |
| semantic_router pending 우선 라우팅 | pending_synonym_reuse → cache_management, pending_synonym_registrations → synonym_registrar |
| 히스토리 트리밍 | MAX_HISTORY_TURNS 초과 시 올바른 압축 |

### 14.2 통합 테스트

| 테스트 | 시나리오 |
|--------|---------|
| 멀티턴 기본 흐름 | 질의 → 후속 질의 (동일 thread_id) → 맥락 참조 확인 |
| 결과 후속 조작 | 질의 → "아까 결과를 Excel로" → 이전 results 사용 확인 |
| SQL 승인 흐름 | 질의 → 승인 대기 → 승인 → 실행 |
| SQL 거부 흐름 | 질의 → 승인 대기 → 거부 → 종료 |
| SQL 수정 흐름 | 질의 → 승인 대기 → SQL 수정 → 재검증 → 실행 |
| synonym 재활용 멀티턴 | "server_name 유사 단어 생성" → 재활용 제안 → "재활용" |
| synonym 재활용 — 새로 생성 | 제안 → "새로 생성" → LLM이 독립 생성 |
| synonym 재활용 — 병합 | 제안 → "병합" → 기존 + 신규 merge |
| 유사어 등록 — 전체 | Excel 처리 → LLM 추론 매핑 → "전체 등록" → Redis 확인 |
| 유사어 등록 — 선택 | Excel 처리 → "1, 3 등록" → 1, 3만 Redis에 등록 |
| 유사어 등록 — 건너뛰기 | Excel 처리 → "건너뛰기" → pending 해제, Redis 변화 없음 |
| 유사어 등록 후 재매핑 확인 | 등록 후 동일 양식 재요청 → synonym 매핑으로 LLM 호출 0 |
| 캐시 연속 대화 | "polestar 캐시 생성" → "설명도 생성해줘" → previous_db_id로 자동 추론 |
| DB 설명 조회→수정 | "DB 목록 보여줘" → "polestar 설명 변경해줘" |
| 세션 만료 | TTL 초과 후 요청 → 새 세션 시작 |
| 하위 호환 | thread_id 없는 요청 → 기존 단일 턴 동작 |
