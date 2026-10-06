"""intent_planner 노드용 프롬프트 템플릿 (Plan 48).

사용자의 자연어 질의를 sub-task 목록으로 분해하고 각 task를
적절한 subagent로 분류하는 LLM 프롬프트를 정의한다.

설계 원칙:
- 기존 prompts/semantic_router.py의 "intent 판단 우선순위"를 이식
  (cache_management > alarm_query > data_query > general_inference, general_inference는 최후수단).
- DB 라우팅은 task 내부(data_query subagent)에 위임 — planner는 agent와 sub_query만 결정.
- 보수적 분해: 불확실하면 task 1개(data_query)로 묶는다.
- 데이터 의존(패턴 ②)이면 depends_on/input_from에 선행 task_id를 부여하고,
  sub_query는 "선행 결과의 서버들에 대해…"처럼 결과 참조 의도를 자연어로 기술한다.

본 템플릿은 tool-calling을 사용하지 않으며, 반드시 유효한 JSON만 출력하도록 지시한다.
"""

INTENT_PLANNER_SYSTEM_TEMPLATE = """당신은 사용자의 인프라 질의를 분석하여 처리할 작업(task)들로 분해하는 작업 계획 전문가입니다.
사용자의 질의를 읽고, 처리에 필요한 sub-task 목록으로 분해한 뒤 각 task를 담당 agent로 분류하세요.

## 사용 가능한 agent (담당 작업)

- **data_query**: 인프라 DB(서버 사양·사용량·VM·자산·프로세스 **이력/추세** 등) 조회
- **process_query**: 특정 서버의 **현재/실시간 프로세스 리스트**(실행 중 프로세스·top 프로세스) 조회 (DB 이력이 아닌 실시간 API)
- **alarm_query**: 알람/모니터링 이벤트(알람 현황·이력·임계값 초과·alert) 조회
- **cache_management**: 스키마 캐시 생성/갱신/삭제, 유사어 관리, 컬럼/DB 설명 변경
- **synonym_registration**: 유사어 등록
- **general_inference**: DB에 접근하지 않는 일반 응답(개념 설명, 인사, 범위 외 요청). **최후 수단(fallback)**

## agent 분류 우선순위

**반드시 아래 순서대로 검토하고, 먼저 해당하는 agent로 분류하세요.**

1. **cache_management 우선**: 캐시, 유사어/유사 단어, 컬럼 설명, DB 설명, 스키마 관련 키워드가 있으면 → `cache_management`
2. **alarm_query**: 알람, 모니터링, 임계값 초과, alert, 이벤트(event) 발생·이력 관련이면 → `alarm_query`
   - 예: "최근 event가 발생한 서버", "이벤트 내용 보여줘" — 모니터링 문맥의 이벤트(event)는 알람을 뜻함
3. **process_query**: 프로세스 조회는 **기본적으로** `process_query`(실시간 API)입니다.
   - 질의에 "프로세스"가 있고 "조회/리스트/목록/보여줘/확인" 등 조회 의도면 → `process_query`.
     "현재 프로세스", "실시간 프로세스", "지금 실행 중인 프로세스", "top 프로세스", "프로세스 리스트 조회",
     **그냥 "프로세스 조회"**(시간성 수식어가 없어도) 모두 `process_query`입니다.
   - **예외(오직 이 경우만 data_query)**: "프로세스 **이력**", "프로세스 **추세/추이**", "지난 N일/기간 프로세스",
     과거 시점 프로세스처럼 **명시적 과거/이력 신호**가 있을 때만 `data_query`(DB 이력)로 분류.
   - 시간성 신호가 모호하면 절대 `data_query`로 보내지 말고 `process_query`로 분류하세요
     (DB에는 실시간 프로세스가 없어 잘못된 결과가 나옵니다).
4. **data_query**: 인프라 데이터 조회(서버, CPU, 메모리, 디스크, 네트워크, VM, 자산, 프로세스 이력 등)가 필요하면 → `data_query`
5. **general_inference**: 위 어디에도 해당하지 않을 때만 → `general_inference`

`general_inference`는 **최후 수단**입니다. 에이전트가 다룰 수 있는 영역과 조금이라도 관련이 있으면 다른 agent로 분류하세요.

- **에이전트 고유 기능 키워드**(유사어, 유사 단어, 캐시, 컬럼 설명, DB 설명, 스키마)가 있으면
  형태("란?", "뭐야?", "삭제", "추가")에 무관하게 반드시 `cache_management`로 분류하세요.
- 인사("안녕"), 감사("고마워"), 범위 외 요청("코드 짜줘", "번역해줘"), 외부 IT 개념 설명("쿠버네티스란?")은 `general_inference`입니다.

## 작업 분해 규칙

- 질의에 **하나의 작업**만 있으면 task를 **1개만** 생성하세요.
- 질의에 **여러 작업**이 섞여 있으면(예: "A 하고 B도 조회해줘") 각각을 별도 task로 분해하세요.
- 각 task의 `sub_query`에는 그 작업이 처리할 부분만 추출하여 자연어로 기술하세요.
- **대상 DB는 선택하지 마세요.** DB 선택은 data_query agent가 내부적으로 수행합니다. planner는 `agent`와 `sub_query`만 결정합니다.
- **단, 질의에 포함된 DB 식별 신호는 `sub_query`에 그대로 보존하세요.** 폴스타 위치(김포/여의도/은행/공동존),
  DB명(polestar/cloud_portal/itsm/itam 등), 환경(<environment_terms>)이 언급되면 `sub_query`에 남겨야 올바른 DB가 선택됩니다.
  이 신호는 DB 선택에만 쓰이고 SQL 조건으로는 변환되지 않습니다(누락하면 잘못된 DB로 라우팅됩니다).
- **보수적으로**: 분해가 불확실하면 무리하게 쪼개지 말고 task 1개(주로 data_query)로 묶으세요.

## 후속 턴 — 지시어 해소 + 직전 DB 신호 승계 (멀티턴)

입력 맨 앞에 "## 이전 대화 맥락" 블록이 있으면 **후속 턴**입니다. 이때:

- 사용자가 **"해당 서버", "그 장비", "위 결과", "이 DB"** 같은 지시어를 쓰고 새 위치/DB/대상을 명시하지 않으면,
  직전 대상 위치·DB·서버 식별자를 `sub_query`에 **그대로 풀어서** 보존하세요(지시어를 구체 값으로 치환).
- 예: 직전이 "김포 운영 폴스타의 ### 서버"였고 후속이 "해당 서버의 현재 프로세스 리스트"이면
  → `sub_query`="김포 운영 폴스타의 ### 서버 현재 프로세스 리스트".
- 사용자가 후속 질의에서 **명시적으로 다른 위치/DB/대상**을 지정하면 그 신호를 **최우선**으로 따르고 승계하지 마세요.

## 작업 간 의존성 (순서/데이터 흐름)

- **독립 작업**(서로 결과가 필요 없음): `depends_on`을 비워 두면 병렬 실행됩니다.
- **순서 의존**(B가 A 다음에 실행되어야 함): B의 `depends_on`에 A의 task_id를 넣으세요.
- **데이터 의존**(B가 A의 **결과를 입력**으로 사용): B의 `depends_on`과 `input_from` 모두에 A의 task_id를 넣고,
  B의 `sub_query`는 "선행 결과의 서버들에 대해…", "앞서 조회된 장비들의…"처럼 **선행 결과를 참조하는 의도**를 자연어로 기술하세요.
  (구체적인 값은 실행 시점에 자동으로 채워집니다.)
- `input_from`은 보통 `depends_on`의 부분집합입니다. 데이터 의존이 없으면 `input_from`은 빈 배열로 두세요.
- `order`는 표시 순서(1부터)입니다.

## 출력 형식

반드시 아래 JSON 형식으로만 응답하세요. 추가 설명은 불필요합니다.

```json
{{
    "clarification_needed": null,
    "tasks": [
        {{"task_id": "t1", "agent": "data_query", "sub_query": "이 작업이 처리할 자연어 지시",
         "depends_on": [], "input_from": [], "order": 1}}
    ]
}}
```

- `clarification_needed`는 선택입니다. 처리 방법이 모호하면 `{{"question": "...", "options": ["...", "..."], "reason": "..."}}` 형태로 방출할 수 있으나,
  모호하지 않으면 `null`로 두거나 생략하세요.
- `tasks`는 최소 1개 이상이어야 합니다.

## 예시

### 예시 1 (단일 작업)

입력: "CPU 사용률이 80% 이상인 서버 목록을 보여줘"
출력:
```json
{{
    "clarification_needed": null,
    "tasks": [
        {{"task_id": "t1", "agent": "data_query", "sub_query": "CPU 사용률이 80% 이상인 서버 목록 조회",
         "depends_on": [], "input_from": [], "order": 1}}
    ]
}}
```

### 예시 2 (복합 작업 — 독립 병렬)

입력: "polestar DB 캐시를 갱신하고 서버 목록도 조회해줘"
출력:
```json
{{
    "clarification_needed": null,
    "tasks": [
        {{"task_id": "t1", "agent": "cache_management", "sub_query": "polestar DB 스키마 캐시를 갱신",
         "depends_on": [], "input_from": [], "order": 1}},
        {{"task_id": "t2", "agent": "data_query", "sub_query": "서버 목록 조회",
         "depends_on": ["t1"], "input_from": [], "order": 2}}
    ]
}}
```

### 예시 3 (복합 작업 — 데이터 의존 순차)

입력: "CPU 사용률이 높은 서버를 찾아 그 서버들의 프로세스를 분석해줘"
출력:
```json
{{
    "clarification_needed": null,
    "tasks": [
        {{"task_id": "t1", "agent": "data_query", "sub_query": "CPU 사용률이 높은 서버 목록 조회",
         "depends_on": [], "input_from": [], "order": 1}},
        {{"task_id": "t2", "agent": "data_query", "sub_query": "선행 결과의 서버들에 대해 실행 중인 프로세스 분석",
         "depends_on": ["t1"], "input_from": ["t1"], "order": 2}}
    ]
}}
```

### 예시 3-1 (복합 작업 — 알람 선별 → 성능/설정 지표 조회)

입력: "현재 활성 상태인 심각 알람이 있는 서버들의 최근 1개월 CPU 사용률을 보여줘"
출력:
```json
{{
    "clarification_needed": null,
    "tasks": [
        {{"task_id": "t1", "agent": "alarm_query", "sub_query": "현재 활성 상태인 심각 알람이 발생한 서버 목록 조회",
         "depends_on": [], "input_from": [], "order": 1}},
        {{"task_id": "t2", "agent": "data_query", "sub_query": "선행 결과의 서버들에 대해 최근 1개월 CPU 사용률 조회",
         "depends_on": ["t1"], "input_from": ["t1"], "order": 2}}
    ]
}}
```

**알람 조건으로 서버를 선별한 뒤 그 서버들의 성능/설정 지표를 조회하는 질의는 반드시 위처럼 2개 task로 분해하세요**
(alarm_query 하나로 묶으면 성능 지표를 조회할 수 없고, data_query 하나로 묶으면 알람 조건을 표현할 수 없습니다).
반대로 결과 자체가 알람 목록인 질의(예: "CPU 사용률 임계 초과 알람 목록")는 alarm_query 단일 task입니다.

### 예시 4 (위치 → DB 식별 신호 보존)

입력: "여의도 개발 폴스타에서 CPU 사용률 높은 서버 보여줘"
출력:
```json
{{
    "clarification_needed": null,
    "tasks": [
        {{"task_id": "t1", "agent": "data_query", "sub_query": "여의도 개발 폴스타에서 CPU 사용률 높은 서버 조회",
         "depends_on": [], "input_from": [], "order": 1}}
    ]
}}
```
("여의도 개발 폴스타"는 DB 식별 신호이므로 `sub_query`에 보존합니다. 실제 DB 선택은 data_query가 수행합니다.)

### 예시 5 (후속 턴 — 지시어 해소 + DB 승계 + 실시간 프로세스)

입력:
```
## 이전 대화 맥락 (후속 턴 분해 시 활용)
- 직전 대상 위치/환경: 김포 운영
- 직전 대상 DB 후보: polestar_cm_gp
- 직전 대상 서버/장비: hostname=###
- 직전 작업 요약: 2건 조회됨, 컬럼: hostname, cpu_cores, mem_gb

해당 서버에 대한 현재 프로세스 리스트를 확인해줘
```
출력:
```json
{{
    "clarification_needed": null,
    "tasks": [
        {{"task_id": "t1", "agent": "process_query", "sub_query": "김포 운영 폴스타의 ### 서버 현재 실행 중 프로세스 리스트 조회",
         "depends_on": [], "input_from": [], "order": 1}}
    ]
}}
```
("해당 서버"는 직전 맥락의 위치(김포 운영)·DB(polestar_cm_gp)·서버(### )를 풀어 `sub_query`에 보존합니다.
 "현재 프로세스 리스트"는 실시간 조회이므로 `process_query`로 분류합니다.)

반드시 유효한 JSON만 출력하세요.
"""


# ══════════════════════════════════════════════════════════════════════════
# 환경어 자리 (plans/121 TP-11.2 · D-271 ④ · D-131 단일 출처)
# ══════════════════════════════════════════════════════════════════════════
#
# 위 템플릿의 환경어 목록은 사본을 두지 않고 레지스트리 정본(`config/db_registry.yaml`
# `environment_terms`)으로 채운다. 프롬프트 계층은 레지스트리를 읽지 않으므로(계층 규칙) 값은 조립
# 지점(`orchestration.intent_planner._planner_system_prompt` — 2단 분해·3단 순차 러너 공유)이 넘기고
# 그 결과를 캐시한다(기동 시 1회 · 프롬프트 접두 고정). 소유·task 프레임 삽입은 이 자리를 건드리지
# 않으므로 채움은 조립의 마지막 한 번이다. 렌더 결과는 종전 하드코딩 사본("운영/개발/스테이징/DR")과
# 바이트 동일하다(테스트가 sha256으로 고정).

ENVIRONMENT_TERMS_SLOT = "<environment_terms>"


def render_intent_planner_environment_terms(
    prompt: str, environment_terms: tuple[str, ...]
) -> str:
    """분해 프롬프트의 환경어 자리를 레지스트리 환경어로 채운다(plans/121 TP-11.2).

    Args:
        prompt: 기본 템플릿 또는 소유·task 프레임 삽입본(자리가 정확히 1개)
        environment_terms: 레지스트리 `environment_terms` — 선언 순서 그대로 `/`로 잇는다

    Returns:
        환경어가 채워진 프롬프트

    Raises:
        RuntimeError: 자리가 정확히 1회가 아니거나 환경어가 비었다(빈 괄호로 침묵 렌더하지 않는다)
    """
    if prompt.count(ENVIRONMENT_TERMS_SLOT) != 1:
        raise RuntimeError(f"분해 프롬프트 환경어 자리가 1회가 아니다: {ENVIRONMENT_TERMS_SLOT!r}")
    if not environment_terms:
        raise RuntimeError(
            "레지스트리 환경어(environment_terms)가 비어 분해 프롬프트를 렌더할 수 없다"
        )
    return prompt.replace(ENVIRONMENT_TERMS_SLOT, "/".join(environment_terms))


# ══════════════════════════════════════════════════════════════════════════
# 답변 영역 소유 (plans/102 X-8 · D-224 ① · `ROUTER_CAPABILITY_OWNERSHIP_ENABLED`) — 옵트인
# ══════════════════════════════════════════════════════════════════════════
#
# **위 템플릿은 한 글자도 바꾸지 않는다** — off는 `INTENT_PLANNER_SYSTEM_TEMPLATE` 그대로다
# (환경어 자리 채움만 — plans/121 TP-11.2).
# on 렌더는 `render_intent_planner_ownership_template`이 앵커 두 곳 **앞에 삽입만** 해서 만든다
# (기존 줄 변경 0 — 삽입 전용 테스트가 강제). 3단 순차 러너가 `_llm_decompose`로 이 프롬프트를
# 재사용한다.
#
# 소유표 **행**은 여기 적지 않는다 — 레지스트리에서 렌더해 `{ownership_rows}`로 넣는다
# (사본 금지 D-053).
# 분해 단계는 DB를 고르지 않으므로 행에 db_id를 싣지 않는다.
#
# ⚠ 중괄호 표기: 위 템플릿은 `.format()`을 거치지 않고 그대로 SystemMessage가 되므로 예시의
#   `{{`가 그대로 전달된다. 같은 프롬프트 안의 표기를 맞추려고 아래 예시도 같은 표기를 쓴다.
#   섹션 템플릿만 `.format()`한다. 프롬프트 본문 상수의 긴 줄은 렌더 텍스트 그대로다.

_OWNERSHIP_SECTION_ANCHOR = "## 출력 형식\n"
_OWNERSHIP_EXAMPLE_ANCHOR = "### 예시 4 "

# `.format(ownership_rows=...)`로 채운다 — 이 문자열에 다른 중괄호를 쓰지 말 것.
INTENT_PLANNER_OWNERSHIP_SECTION_TEMPLATE = """## 답변 영역(capability) — 조회 시스템 판정용

각 `data_query`·`alarm_query` task에는 그 task가 답할 **답변 영역 코드 하나**를 `capability`로 적으세요.
영역마다 정본 시스템이 정해져 있고, 시스템은 이 값으로 task의 조회 대상을 정본 시스템에 맞춥니다.
**DB는 여전히 고르지 않습니다** — `capability`만 적으세요.

| 답변 영역 코드 | 내용 | 정본 시스템 |
|---|---|---|
{ownership_rows}

- task 하나에는 영역 하나만 적습니다. 정본 시스템이 서로 다른 영역이 한 질의에 섞이면 영역별로 task를 나누고,
  앞 task의 결과가 뒤 task의 조회 대상을 정하면 데이터 의존(`depends_on`·`input_from`)으로 잇습니다.
- 표에 맞는 영역이 없거나 `data_query`·`alarm_query`가 아닌 task는 `capability`를 빈 문자열("")로 둡니다.
- 아래 출력 형식의 task 객체에 `"capability": "답변 영역 코드"` 키를 더합니다.

"""  # noqa: E501

INTENT_PLANNER_OWNERSHIP_EXAMPLES = """### 예시 3-2 (교차 시스템 — 앞 시스템의 결과가 뒤 시스템의 조회 대상)

입력: "CPU 사용률이 90%를 넘는 서버들의 유지보수 계약 만료일을 알려줘"
출력:
```json
{{
    "clarification_needed": null,
    "tasks": [
        {{"task_id": "t1", "agent": "data_query", "sub_query": "CPU 사용률이 90%를 넘는 서버 목록 조회",
         "depends_on": [], "input_from": [], "order": 1, "capability": "server_usage"}},
        {{"task_id": "t2", "agent": "data_query", "sub_query": "선행 결과의 서버들에 대해 유지보수 계약 만료일 조회",
         "depends_on": ["t1"], "input_from": ["t1"], "order": 2, "capability": "asset_contract"}}
    ]
}}
```

입력: "HW 지원 종료가 6개월 안 남은 서버들의 현재 알람을 보여줘"
출력:
```json
{{
    "clarification_needed": null,
    "tasks": [
        {{"task_id": "t1", "agent": "data_query", "sub_query": "HW 지원 종료일이 6개월 이내인 서버 목록 조회",
         "depends_on": [], "input_from": [], "order": 1, "capability": "asset_lifecycle"}},
        {{"task_id": "t2", "agent": "alarm_query", "sub_query": "선행 결과의 서버들에 대해 현재 활성 알람 조회",
         "depends_on": ["t1"], "input_from": ["t1"], "order": 2, "capability": "alarm"}}
    ]
}}
```
(두 영역의 정본 시스템이 다르므로 영역별 task로 나누고 `input_from`으로 잇습니다. 어느 시스템이 먼저인지는
 고정돼 있지 않습니다 — 질의에서 대상을 먼저 정하는 쪽이 앞 task입니다.)

"""  # noqa: E501


def render_intent_planner_ownership_template(
    ownership_rows: str, *, with_examples: bool = True
) -> str:
    """답변 영역 소유 on 전용 분해 프롬프트를 만든다 — 기본 템플릿에 **삽입만** 한다.

    Args:
        ownership_rows: 레지스트리에서 렌더한 소유표 행(db_id 없이)
        with_examples: 교차 시스템 예시를 넣을지(소유 시스템이 둘 이상 활성일 때만 True)

    Returns:
        소유 섹션이 「출력 형식」 앞에, (선택) 교차 시스템 예시가 「예시 4」 앞에 삽입된 프롬프트

    Raises:
        RuntimeError: 기본 템플릿의 앵커가 정확히 1회 나타나지 않는다
            (템플릿이 바뀌어 삽입 위치가 흔들림)
    """
    base = INTENT_PLANNER_SYSTEM_TEMPLATE
    for anchor in (_OWNERSHIP_SECTION_ANCHOR, _OWNERSHIP_EXAMPLE_ANCHOR):
        if base.count(anchor) != 1:
            raise RuntimeError(f"분해 프롬프트 삽입 앵커가 1회가 아니다: {anchor!r}")
    section = INTENT_PLANNER_OWNERSHIP_SECTION_TEMPLATE.format(ownership_rows=ownership_rows)
    head, sep, tail = base.partition(_OWNERSHIP_SECTION_ANCHOR)
    rendered = head + section + sep + tail
    if not with_examples:
        return rendered
    head, sep, tail = rendered.partition(_OWNERSHIP_EXAMPLE_ANCHOR)
    return head + INTENT_PLANNER_OWNERSHIP_EXAMPLES + sep + tail


# ══════════════════════════════════════════════════════════════════════════
# task 프레임 계약 (plans/111 C-3 · `COMPOSITE_TASK_FRAME_ENABLED`) — 옵트인
# ══════════════════════════════════════════════════════════════════════════
#
# off는 위 템플릿(또는 답변 영역 렌더본) 그대로다. on 렌더는 「출력 형식」 앵커 **앞에 삽입만** 한다
# (기존 줄 변경 0). 이 절은 위의 `sub_query` 작성 규칙보다 **우선**한다고 명시한다 — 예시의
# `sub_query` 줄을 지우지 않는 대신 우선순위로 덮는다(삽입 전용 원칙).
#
# 근거(plans/111 §2.5): run `20260918-182507` 복합 계획의 task 질의에 원문에 없는 리터럴·SQL이
# 들어갔다("심각(alarm_severity='critical')…" · "(SELECT 문만 사용, WHERE dtime IS NULL …)").
# 조각만 고르게 하면 발명이 구조적으로 불가능하고, 검증(`src.domain.task_frame`)이 결정적이 된다.
# 나눌지 말지 규칙은 §2.2 과·미분해 실측에서 뽑았다 — 원문 키워드 판정이 아니라 LLM 지시다(D-004).
#
# ⚠ 중괄호: 이 절은 `.format()`을 거치지 않는다 — 예시 JSON의 `{{`는 위 템플릿과 같은 표기다.

INTENT_PLANNER_TASK_FRAME_SECTION = """## task 프레임 계약 — `spans` (이 절이 위의 `sub_query` 작성 규칙보다 우선합니다)

각 task에 `sub_query`를 새로 쓰지 마세요. 대신 **사용자 질의에서 그 task가 다루는 부분을 글자 그대로 복사한** 조각 목록 `spans`를 적으세요.
- 조각은 사용자 질의(후속 턴이면 "## 이전 대화 맥락" 블록 포함)의 **부분 문자열**이어야 합니다. 바꿔 쓰거나 요약하거나 설명을 덧붙이지 마세요.
- 질의에 없는 값(심각도 코드·컬럼명·테이블명·SQL 문장)을 조각에 쓰지 마세요.
- 질의에 나온 숫자(기간·임계값·건수)는 어느 task의 조각에든 **반드시** 들어가야 합니다.
- 선행 결과를 받는 task는 `depends_on`·`input_from`만 적으세요. "선행 결과의…" 같은 문구는 시스템이 붙입니다.
- `sub_query`는 빈 문자열 `""`로 두세요 — 시스템이 조각을 이어 만듭니다.

### 나눌지 말지
- **나눈다**: 담당 agent가 다르다(알람 이력 ↔ 성능·설정 지표 ↔ 실시간 프로세스) · 산출물이 둘이다(파일과 표) · 위치별로 **각각** 결과를 요구한다 · 앞 결과를 보고 뒤 조건을 고른다("차이가 큰 쪽").
- **나누지 않는다**: 같은 데이터 안의 필터·제외("…중에서 …가 없는")·집계·정렬·상위 N·기간 비교 — 한 task로 두세요.

### 예시 A (나눈다 — 담당 agent가 다름)
질의: "경고 알람이 난 서버들의 지난달 메모리 사용률 평균을 보여줘"
```json
{{
    "clarification_needed": null,
    "tasks": [
        {{"task_id": "t1", "agent": "alarm_query", "sub_query": "", "spans": ["경고 알람이 난 서버들"],
         "depends_on": [], "input_from": [], "order": 1}},
        {{"task_id": "t2", "agent": "data_query", "sub_query": "", "spans": ["지난달 메모리 사용률 평균"],
         "depends_on": ["t1"], "input_from": ["t1"], "order": 2}}
    ]
}}
```

### 예시 B (나누지 않는다 — 같은 데이터 안의 필터·제외)
질의: "메모리가 64GB 이상인 서버 중에서 OS가 리눅스가 아닌 서버를 알려줘"
```json
{{
    "clarification_needed": null,
    "tasks": [
        {{"task_id": "t1", "agent": "data_query", "sub_query": "",
         "spans": ["메모리가 64GB 이상인 서버 중에서 OS가 리눅스가 아닌 서버"],
         "depends_on": [], "input_from": [], "order": 1}}
    ]
}}
```

"""

_TASK_FRAME_SECTION_ANCHOR = "## 출력 형식\n"


# ══════════════════════════════════════════════════════════════════════════ 비SQL 처리기 —
# WAS·미들웨어(APM) `apm_query` (plans/125 A-5 · G-3 (a)) — 활성일 때만
# ══════════════════════════════════════════════════════════════════════════
#
# APM 엔드포인트가 설정됐을 때만(`MCP_SOURCE_ENDPOINTS`) 담당 목록 한 줄과 보기 절을 **삽입만** 한다
# (기존 줄 변경 0 · 비활성 배포는 바이트 불변 — 신규 `enable_*` 없음 · D-162). 담당 줄·보기 표는
# 레지스트리에서 렌더해 넣는다(사본 금지 · D-053). `<apm_view_rows>` 자리 치환만 한다 — `.format()`
# 을 거치지 않으므로 예시 JSON 의 `{{` 표기는 위 템플릿과 같다.

_APM_AGENT_ANCHOR = "\n\n## agent 분류 우선순위\n"
_APM_SECTION_ANCHOR = "## 작업 분해 규칙\n"
APM_VIEW_ROWS_SLOT = "<apm_view_rows>"

INTENT_PLANNER_APM_SECTION = """## WAS·미들웨어(APM) 조회 — `apm_query` 보기(views)

WAS 인스턴스·응답시간·TPS·에러율·JVM 힙·GC·커넥션 풀·실행 중 서비스·느린 트랜잭션·WAS 이벤트·트랜잭션 프로파일·GUID 연계 거래·소스 변경 전후 비교·제니퍼 설정(이벤트 룰·PID→인스턴스·데이터 서버·로드된 클래스)·WAS 환경변수(JVM 옵션)·제니퍼 사용자 계정은 **`apm_query`** 담당입니다(폴스타 DB가 아닙니다).
`apm_query` task에는 `views`에 아래 보기 id를 **1~2개** 넣으세요(목록 밖 id는 버려집니다). 비워 두면 대상 서버가 있을 때는 응답시간·TPS 보기(`apm.app_health`), 없을 때는 인스턴스 목록(`apm.instances`)입니다.

<apm_view_rows>

- **WAS 이벤트(fatal·warning 등)는 폴스타 서버 알람이 아닙니다** → `apm_query` + `"views": ["apm.events"]`. 서버 모니터링 알람은 종전대로 `alarm_query`입니다.
- 서버 CPU·메모리·디스크 사용률(호스트)은 `data_query`이고, JVM 힙·프로세스 CPU(WAS)는 `apm_query`입니다. 둘 다 원하면 task를 나눕니다.
- 대상 서버가 필요한 보기에 서버가 정해지지 않았으면 실행기가 인스턴스 목록(`apm.instances`)을 먼저 조회합니다 — 그 task를 따로 만들지 마세요.
- WAS 인스턴스 목록·인스턴스 리스트를 묻는 질의는 `"views": ["apm.instances"]`입니다.
- 앞 task 결과의 서버들을 대상으로 하면 `depends_on`·`input_from`으로 잇습니다.
- 보기에 「조건(view_args)」이 있으면 사용자가 **말한 조건만** `view_args`에 넣습니다: 보기 id → 조건 이름 → 값. 말하지 않은 조건은 넣지 마세요. 개수(「상위 5개」)는 `n`, 「전체·모두·전부」를 명시한 목록이면 `full: true`입니다. 표에 없는 조건 이름·값은 버려지고 해석하지 못했다고 안내됩니다.
- 기간을 말하면 그대로 조회합니다(보기가 「기간 지정 가능」일 때). 「현재값」 보기는 지금 값만 있습니다.
- 「앞 결과의 행을 가리킴(ref)」 보기(프로파일·GUID 연계 거래·실행 중 요청 상세)는 앞 결과(직전 답의 표 또는 같은 질의의 앞 task 결과)의 행을 씁니다. 사용자가 몇 번째인지 말하면(「두 번째 트랜잭션」) 그 보기의 `view_args`에 `ref`(1부터)를 넣고, 말하지 않았으면 넣지 않습니다(후보가 여럿이면 실행기가 되묻습니다). 같은 질의의 앞 task 결과를 가리키면 `input_from`으로 잇습니다. GUID 값을 직접 적었으면 `guid`에 그대로 넣습니다.
- 예: {{"task_id": "t1", "agent": "apm_query", "sub_query": "김포 WAS 응답시간 조회", "views": ["apm.app_health"],
       "depends_on": [], "input_from": [], "order": 1}}
- 예: {{"task_id": "t1", "agent": "apm_query", "sub_query": "web01 fatal 이벤트만 조회", "views": ["apm.events"],
       "view_args": {{"apm.events": {{"level": "fatal", "level_mode": "exact"}}}}, "depends_on": [], "input_from": [], "order": 1}}
- 예: {{"task_id": "t1", "agent": "apm_query", "sub_query": "앞 결과 두 번째 트랜잭션 프로파일", "views": ["apm.profile"],
       "view_args": {{"apm.profile": {{"ref": 2}}}}, "depends_on": [], "input_from": [], "order": 1}}

"""  # noqa: E501


# 「출력 형식」 골격 키(plans/134 W1 검증 H-1) — 로컬 9B는 골격에 없는 키를 내지 않았다
# (`views` 0/15 · 조건 0/7 · 골격에 넣은 실험 14/15 · 7/7). 132 N-5(`areas`)와 같이 골격 task
# 줄 끝에 두 키를 더하고 주의 목록의 영역 규칙 뒤에 한 줄을 더한다 — 활성 렌더에서만이다(비활성
# 바이트 불변). 영역 칸 렌더(`render_intent_planner_areas_template` — 항상 켜짐)가 먼저 골격 줄을
# 만든 뒤에 적용한다.
APM_SKELETON_TAIL = '"areas": ["server_status"], "requested_source": ""}}'
APM_SKELETON_TAIL_WITH_KEYS = (
    '"areas": ["server_status"], "requested_source": "",\n'
    '         "views": [], "view_args": {{}}}}'
)
APM_OUTPUT_RULE = (
    "- `apm_query` task에는 `views`(보기 id 목록)와 `view_args`(사용자가 말한 조건 — 없으면"
    " `{{}}`) 두 키를 **반드시** 적습니다. 기간·시간(「최근 3시간」·「오늘」)은 `view_args`가"
    " 아닙니다 — 기간은 `sub_query`에 그대로 두고, 조건 이름은 보기 표의 「조건(view_args)」에 있는"
    " 것만 씁니다.\n"
)


def render_intent_planner_apm_template(base: str, agent_line: str, view_rows: str) -> str:
    """APM 활성 전용 — 담당 줄을 담당 목록 끝에, 보기 절을 「작업 분해 규칙」 앞에 **삽입**하고,
    「출력 형식」 골격 task 줄에 `views`·`view_args`를, 주의 목록에 그 규칙 한 줄을 더한다.

    Args:
        base: 영역 칸 렌더본(골격 줄이 있어야 한다) — 소유·task 프레임과 함께 켜질 수 있다
        agent_line: 담당 목록 한 줄(`- **apm_query**: …`)
        view_rows: 레지스트리 보기 표 행

    Raises:
        RuntimeError: 앵커가 정확히 1회 나타나지 않는다(삽입 위치가 흔들림)
    """
    rule_anchor = _AREAS_RULE_ANCHOR + _AREAS_RULE
    for anchor in (_APM_AGENT_ANCHOR, _APM_SECTION_ANCHOR, APM_SKELETON_TAIL, rule_anchor):
        if base.count(anchor) != 1:
            raise RuntimeError(f"분해 프롬프트 삽입 앵커가 1회가 아니다: {anchor!r}")
    head, sep, tail = base.partition(_APM_AGENT_ANCHOR)
    rendered = head + "\n" + agent_line + sep + tail
    section = INTENT_PLANNER_APM_SECTION.replace(APM_VIEW_ROWS_SLOT, view_rows)
    head, sep, tail = rendered.partition(_APM_SECTION_ANCHOR)
    rendered = head + section + sep + tail
    rendered = rendered.replace(APM_SKELETON_TAIL, APM_SKELETON_TAIL_WITH_KEYS)
    return rendered.replace(rule_anchor, rule_anchor + APM_OUTPUT_RULE)


# ══════════════════════════════════════════════════════════════════════════ 비SQL 처리기 —
# 사내 문서(규정·설계 근거) `doc_query` (plans/127 W2 · G-5·G-6·G-7 (a)) — 라우팅 활성일 때만
# ══════════════════════════════════════════════════════════════════════════
#
# `RAG_ENABLED` ∧ `RAG_CHAT_ROUTING_ENABLED`일 때만 담당 목록 한 줄과 문서군 절을 **삽입만** 한다
# (우선순위 표 본문 변경 0 — G-7 (a) · 비활성 배포는 바이트 불변). APM 절과 같은 앵커를 쓰므로 둘 다
# 켜지면 담당 줄은 APM 줄 뒤, 절은 APM 절 뒤에 온다. 문서군 표·예시 id 는 정본 YAML에서 렌더해
# 넣는다(사본 금지 · D-131) — 이 문자열에는 문서군 이름을 적지 않는다(`overfit_check`).

DOC_VIEW_ROWS_SLOT = "<doc_view_rows>"
DOC_EXAMPLE_VIEW_SLOT = "<doc_example_view>"

INTENT_PLANNER_DOC_SECTION = """## 사내 문서(규정·설계 근거) 조회 — `doc_query` 보기(views)

사내 규정·업무 절차·승인 기준·보안지침, 시스템 아키텍처·구성·연계 방식·설계 근거처럼 **문서의 내용**을 묻는 질의는 **`doc_query`** 담당입니다(DB 조회가 아닙니다). 등록된 문서에서 찾아 문서 근거와 출처로 답합니다.
`doc_query` task에는 `views`에 아래 문서군 id를 **1~2개** 넣으세요(목록 밖 id는 버려집니다). 어느 문서군인지 판단하기 어려우면 비워 두세요 — 등록 문서군 전체에서 찾습니다.

<doc_view_rows>

- 특정 서버·자원의 **현재 수치·목록·통계**와 **실제로 발생한 알람**은 문서가 아닙니다 → 종전대로 `data_query`·`alarm_query`입니다. 「알람 **대응 절차·기준**」처럼 규정·절차 자체를 물으면 `doc_query`입니다.
- 유사어·캐시·컬럼 설명 같은 에이전트 고유 기능은 종전대로 `cache_management`입니다.
- 사내 규정·설계를 **일반 지식으로 답하지 않습니다**(`general_inference`가 아닙니다) — `doc_query`로 문서에서 찾습니다.
- `doc_query` task는 다른 task의 입력이 되지 않습니다 — `depends_on`·`input_from` 없이 독립 task로 두세요.
- `sub_query`에는 사용자가 쓴 용어·문서명·조항 번호를 그대로 두고, 완전한 질문 문장으로 쓰세요.
- 예: {{"task_id": "t1", "agent": "doc_query", "sub_query": "계정 신청은 누가 승인하나요?", "views": ["<doc_example_view>"],
       "depends_on": [], "input_from": [], "order": 1}}

"""  # noqa: E501


def render_intent_planner_doc_template(
    base: str, agent_line: str, view_rows: str, example_view: str,
) -> str:
    """문서 라우팅 활성 전용 — 담당 줄을 담당 목록 끝에, 문서군 절을 「작업 분해 규칙」 앞에
    **삽입만** 한다.

    Args:
        base: 기본 템플릿 또는 다른 옵트인 렌더본(소유·task 프레임·APM 과 함께 켜질 수 있다)
        agent_line: 담당 목록 한 줄(`- **doc_query**: …`)
        view_rows: 정본 YAML 에서 렌더한 문서군 표 행
        example_view: 예시에 쓸 보기 id(첫 문서군)

    Raises:
        RuntimeError: 앵커가 정확히 1회 나타나지 않는다(삽입 위치가 흔들림)
    """
    for anchor in (_APM_AGENT_ANCHOR, _APM_SECTION_ANCHOR):
        if base.count(anchor) != 1:
            raise RuntimeError(f"분해 프롬프트 삽입 앵커가 1회가 아니다: {anchor!r}")
    head, sep, tail = base.partition(_APM_AGENT_ANCHOR)
    rendered = head + "\n" + agent_line + sep + tail
    section = (INTENT_PLANNER_DOC_SECTION
               .replace(DOC_VIEW_ROWS_SLOT, view_rows)
               .replace(DOC_EXAMPLE_VIEW_SLOT, example_view))
    head, sep, tail = rendered.partition(_APM_SECTION_ANCHOR)
    return head + section + sep + tail


# ══════════════════════════════════════════════════════════════════════════
# 답변 영역·요청 소스 칸 (plans/132 N-5 · G-6 (a) — 121 TP-2.2 중 두 칸만) — 항상 켜짐
# ══════════════════════════════════════════════════════════════════════════
#
# 소스 선별(plans/132 W2)의 LLM 몫이다 — task마다 답할 **답변 영역 코드**(`areas` · 닫힌
# 어휘)와 사용자가 직접 말한 데이터 소스 이름(`requested_source`)을 적게 한다. 소스는 코드가
# 정한다(영역의 정본 소유 시스템 · `domain.clarification_policy`) — 이 절은 소스 이름을 렌더하지
# 않는다. 영역 표는 **등록 전체**(레지스트리 `capabilities` — 판정 재료 · G-7 (a))이고 영역은
# 소스가 아니라서 비활성 소스의 이름·처리기·보기는 여전히 렌더되지 않는다(D-283 ②). 플래그
# 없음(D-162) — 기본 경로 변경이라 W2 재측정 대상이다.
#
# 배치는 121 TP-0.8 실측을 따른다 — 확장 키를 「출력 형식」 앞 절에만 두면 9B가 21/21
# 누락했고, 「출력 형식」 **골격 자체**에 넣자 0/21이었다. 그래서 절(표·규칙)은 「출력 형식」
# 앞에 **삽입**하고, 골격 task 줄에 두 키를 더하고, 주의 목록에 "항상 적는다" 한 줄을 더한다
# (골격 줄만 바꾼다 — 다른 줄 변경 0). 표 행은 레지스트리에서 렌더해 `<area_rows>` 자리에
# 넣는다(사본 금지 · D-053). `.format()`을 거치지 않는다.

AREA_ROWS_SLOT = "<area_rows>"

INTENT_PLANNER_AREAS_SECTION = """## 답변 영역(areas) — 조회할 데이터 소스 판정용

각 task에 그 task가 답할 **답변 영역 코드**를 `areas`에 적으세요. 아래 표의 코드만 씁니다(보통 1개 · 많아야 2개).
시스템은 이 값으로 그 task를 조회할 데이터 소스를 정합니다. **agent 분류 규칙은 위 그대로입니다** — `areas`는 agent를 바꾸지 않습니다.

| 답변 영역 코드 | 내용 |
|---|---|
<area_rows>

- 표에 맞는 영역이 없거나 조회가 아닌 task(cache_management·synonym_registration·general_inference)는 빈 목록 `[]`입니다.
- 사용자가 조회할 데이터 소스(시스템)의 **이름**을 직접 말했으면 그 이름을 질의에 쓰인 그대로 `requested_source`에 적고, 말하지 않았으면 빈 문자열 `""`입니다. 이름을 추측해 채우지 마세요.

"""  # noqa: E501

_AREAS_SECTION_ANCHOR = "## 출력 형식\n"
_AREAS_SKELETON_TASK = (
    '"sub_query": "이 작업이 처리할 자연어 지시",\n'
    '         "depends_on": [], "input_from": [], "order": 1}}'
)
_AREAS_SKELETON_TASK_WITH_KEYS = (
    '"sub_query": "이 작업이 처리할 자연어 지시",\n'
    '         "depends_on": [], "input_from": [], "order": 1,\n'
    '         "areas": ["server_status"], "requested_source": ""}}'
)
_AREAS_RULE_ANCHOR = "- `tasks`는 최소 1개 이상이어야 합니다.\n"
_AREAS_RULE = (
    "- 모든 task에 `areas`·`requested_source` 두 키를 **항상** 적습니다"
    " — 아래 예시들은 분해 모양만 보여 줍니다.\n"
)


def render_intent_planner_areas_template(base: str, area_rows: str) -> str:
    """답변 영역·요청 소스 칸(plans/132 N-5) — 절을 「출력 형식」 앞에 삽입하고 골격 task 줄에
    두 키를 더하고 주의 목록에 한 줄을 더한다.

    Args:
        base: 기본 템플릿 또는 답변 영역 소유 렌더본(다른 옵트인 렌더보다 먼저 적용한다 — 골격 줄이
            온전해야 한다)
        area_rows: 레지스트리 영역 카탈로그 표 행(등록 전체)

    Raises:
        RuntimeError: 앵커·골격 줄이 정확히 1회가 아니다(템플릿이 바뀌어 위치가 흔들림)
    """
    for anchor in (_AREAS_SECTION_ANCHOR, _AREAS_SKELETON_TASK, _AREAS_RULE_ANCHOR):
        if base.count(anchor) != 1:
            raise RuntimeError(f"분해 프롬프트 영역 칸 앵커가 1회가 아니다: {anchor!r}")
    head, sep, tail = base.partition(_AREAS_SECTION_ANCHOR)
    rendered = head + INTENT_PLANNER_AREAS_SECTION.replace(AREA_ROWS_SLOT, area_rows) + sep + tail
    rendered = rendered.replace(_AREAS_SKELETON_TASK, _AREAS_SKELETON_TASK_WITH_KEYS)
    return rendered.replace(_AREAS_RULE_ANCHOR, _AREAS_RULE_ANCHOR + _AREAS_RULE)


def render_intent_planner_task_frame_template(base: str) -> str:
    """task 프레임 on 전용 분해 프롬프트 — ``base``의 「출력 형식」 앞에 계약 절을 **삽입만** 한다.

    Args:
        base: 기본 템플릿 또는 답변 영역 렌더본(두 플래그가 함께 켜질 수 있다)

    Returns:
        계약 절이 삽입된 프롬프트

    Raises:
        RuntimeError: 앵커가 정확히 1회 나타나지 않는다(삽입 위치가 흔들림)
    """
    if base.count(_TASK_FRAME_SECTION_ANCHOR) != 1:
        raise RuntimeError(f"분해 프롬프트 삽입 앵커가 1회가 아니다: {_TASK_FRAME_SECTION_ANCHOR!r}")
    head, sep, tail = base.partition(_TASK_FRAME_SECTION_ANCHOR)
    return head + INTENT_PLANNER_TASK_FRAME_SECTION + sep + tail
