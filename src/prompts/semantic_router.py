"""semantic_router 노드용 프롬프트 템플릿.

사용자의 자연어 질의를 분석하여 어떤 DB를 조회해야 하는지
분류하는 LLM 프롬프트를 정의한다.

v2 변경: 키워드 분류 제거, LLM 전용 라우팅.
사용자 직접 DB 지정, 멀티 DB sub_query_context 분리 규칙 추가.
동적 템플릿으로 변경 (활성 도메인 목록을 런타임에 주입).

v3 변경(Plan 67 R2): DB 나열(`{db_list}`)에 더해 위치/존 어휘(`{location_vocab}`)와
위치→DB 예시(`{location_db_examples}`)도 레지스트리(`config/db_registry.yaml`) 파생
렌더로 주입한다. 신규 DB 편입 시 이 프롬프트는 수정 대상이 아니다.
※ 아래 few-shot JSON 예시의 db_id는 예시 자체가 LLM 출력 형식을 고정하는 재료라
   렌더 대상이 아니다(어휘 나열만 파생).
"""

# ── 프롬프트 절 정본 (Plan 79 트랙 B / WU-D2 · D-053 사본 금지) ──────────────
#
# 단일 호출 프롬프트를 **절 단위 상수**로 쪼갠다. 트랙 B의 2단 분리(1단 intent / 2단 DB)가
# 같은 텍스트를 필요로 하는데, 복사하면 프롬프트가 두 벌이 되어 조용히 어긋난다.
#
# **텍스트는 한 글자도 바꾸지 않았다.** 아래 조립 결과가 추출 이전 렌더와 **바이트 동일**함을
# `tests/test_semantic_routing/test_prompt_byte_identity.py`가 골든 파일로 고정한다 —
# 렌더가 바뀌면 S-1(골든셋 회귀 · plans/80 WU-05) 기준선이 조용히 오염되기 때문이다.
#
# 절이 어느 단계로 가는지:
#   1단(intent) : _S_INTENT_CLASSES · _S_ALARM · _S_DB_GUIDE · _S_CACHE ·
#                 _S_FAULT_SLOT · _S_PRIORITY · _S_GENERAL
#   2단(DB)     : _S_DB_LIST · _S_USER_DB_SPEC · _S_MULTI_DB · _S_JUDGE_RULES · _S_EXAMPLES
#   양쪽 아님   : _S_HEADER · _S_OUTPUT_JSON (단계별 출력 형식이 다르므로 각자 새로 쓴다)

_S_HEADER = """당신은 인프라 관련 질의를 분석하여 적절한 데이터베이스를 선택하는 전문가입니다.
사용자의 질의를 분석하여 어떤 데이터베이스를 조회해야 하는지 판단하세요.

"""

_S_DB_LIST = """## 사용 가능한 데이터베이스

{db_list}

"""

_S_USER_DB_SPEC = """## 사용자 직접 DB 지정 규칙

사용자가 프롬프트에서 특정 DB를 명시적으로 지정할 수 있습니다.
다음과 같은 패턴을 인식하세요:
- DB 식별자 직접 언급: "polestar에서", "cloud_portal에서", "itsm에서", "itam에서"
- DB 표시명 언급: "Polestar DB에서", "Cloud Portal에서", "ITSM DB에서", "ITAM DB에서"
- 한국어 별칭: "클라우드 포탈에서", "자산관리 DB에서", "폴스타에서" 등
- 패턴: "~에서 조회해줘", "~에서 찾아줘", "~DB에서", "~에서 검색" 등

사용자가 DB를 직접 지정한 경우:
- 해당 DB를 반드시 결과에 포함하세요
- user_specified를 true로 설정하세요
- relevance_score를 1.0으로 설정하세요

"""

_S_MULTI_DB = """## 멀티 DB 쿼리 판단

하나의 질의가 여러 DB의 데이터를 필요로 할 수 있습니다.
이 경우 각 DB별로 조회해야 할 내용을 sub_query_context에 분리하여 기술하세요.

**중요: sub_query_context에는 순수한 데이터 조회 의도만 기술하세요.**
DB를 식별하기 위해 사용된 위치/환경/존 정보({location_vocab} 등)는
sub_query_context에 포함하지 마세요. 이 정보는 DB 라우팅에만 사용되며,
실제 SQL 쿼리 조건으로 변환되어서는 안 됩니다.

예시:
- 입력: "여의도 개발 폴스타에서 서버 리스트 출력"
  -> polestar_cm_yd: sub_query_context = "서버 목록 조회" (O)
  -> polestar_cm_yd: sub_query_context = "여의도 개발 서버 목록 조회" (X - 위치 정보 포함 금지)

예시:
- 입력: "서버 사양과 해당 서버의 VM 정보를 알려줘"
  -> polestar: sub_query_context = "서버 사양(CPU, Memory, Disk) 조회"
  -> cloud_portal: sub_query_context = "서버에 연결된 VM 정보 조회"

- 입력: "김포 운영 폴스타에서 CPU 사용률 높은 서버 보여줘"
  -> polestar_cm_gp: sub_query_context = "CPU 사용률이 높은 서버 목록 조회"
  (주의: "김포 운영"은 DB 식별 정보이므로 sub_query_context에 포함하지 않음)

"""

_S_OUTPUT_JSON = """## 출력 형식

반드시 아래 JSON 형식으로만 응답하세요. 추가 설명은 불필요합니다.

```json
{{
    "intent": "data_query",
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

"""

_S_INTENT_CLASSES = """- data_query: 서버 사양, 성능 지표, 프로세스 등 일반 인프라 데이터 조회
- alarm_query: 알람 현황, 알람 이력, 임계값 초과, 모니터링 alert·이벤트(event) 조회
- cache_management: 캐시 생성/갱신/삭제, 유사어 관리, 컬럼 설명 변경{fault_diagnosis_class_line}
- general_inference: 위 어디에도 해당하지 않는 일반 응답. databases는 빈 배열([]) — **최후 수단**

"""

_S_JUDGE_RULES = """## 판단 규칙

1. 질의가 하나의 DB 도메인에만 해당하면 해당 DB만 선택합니다.
2. 질의가 여러 DB를 필요로 하면 관련된 모든 DB를 선택하고, 각 DB별 sub_query_context를 분리합니다.
3. relevance_score는 0.0~1.0 사이의 관련도 점수입니다.
4. 확실한 매칭이면 0.8 이상, 가능성 있는 매칭이면 0.5~0.8, 약한 연관이면 0.3~0.5를 부여합니다.
5. **관련도가 낮아도 그대로 값을 부여해 포함하세요.** 최종 제외 판단은 시스템이 합니다 —
   낮은 점수를 스스로 빼지 말고, 판단한 값을 그대로 적으세요.
6. 사용자가 DB를 직접 지정한 경우 해당 DB의 relevance_score를 1.0으로, user_specified를 true로 설정하세요.

"""

_S_ALARM = """## 알람 조회 판단 (alarm_query)

사용자가 알람 현황, 알람 이력, 임계값 초과 등 모니터링 이벤트 정보를 요청하는 경우:
- intent를 "alarm_query"로 설정하고 해당 polestar DB를 선택합니다.
- 지역/환경이 명시된 경우: {location_db_examples}

alarm_query로 분류할 질의 패턴:
- 알람 목록: "현재 발생 중인 알람", "알람 목록", "alert 현황", "알람 조회"
- 심각도 필터: "critical 알람", "warning 알람", "심각도별 알람", "심각 알람"
- 임계값/이상: "임계값 초과 서버", "CPU 알람", "디스크 알람", "메모리 알람"
- 알람 이력: "알람 이력", "지난주 알람", "알람 발생 횟수", "이번 달 알람"
- 이벤트 발생: "최근 event가 발생한 서버", "이벤트 발생 서버", "서버별 이벤트 내용"
- 모니터링 현황: "모니터링 정보", "모니터링 현황", "서버 모니터링", "모니터링 상태"
- 담당자 조회: "미확인 알람", "처리되지 않은 알람", "담당자 없는 알람"

"""

_S_EXAMPLES = """## 예시

입력: "서버 CPU 사용률이 80% 이상인 목록을 보여줘"
출력:
```json
{{
    "databases": [
        {{"db_id": "polestar_b0", "relevance_score": 0.95, "reason": "서버 CPU 사용률 조회", "sub_query_context": "CPU 사용률이 80% 이상인 서버 목록 조회", "user_specified": false}}
    ]
}}
```

입력: "은행 폴스타에서 서버 목록 조회해줘"
출력:
```json
{{
    "databases": [
        {{"db_id": "polestar_b0", "relevance_score": 1.0, "reason": "사용자가 은행 폴스타 DB를 직접 지정", "sub_query_context": "서버 목록 조회", "user_specified": true}}
    ]
}}
```

입력: "김포 영역의 VM 목록과 해당 VM이 설치된 서버 스펙을 알려줘"
출력:
```json
{{
    "databases": [
        {{"db_id": "cloud_portal", "relevance_score": 0.9, "reason": "김포 영역 VM 목록 조회", "sub_query_context": "김포 영역의 VM 목록과 상세 정보 조회", "user_specified": false}},
        {{"db_id": "polestar_b0", "relevance_score": 0.8, "reason": "VM이 설치된 서버 스펙 조회", "sub_query_context": "VM이 설치된 서버의 CPU, Memory, Disk 사양 조회", "user_specified": false}}
    ]
}}
```

입력: "현재 발생 중인 서버 알람 목록을 보여줘"
출력:
```json
{{
    "intent": "alarm_query",
    "databases": [
        {{"db_id": "polestar_b0", "relevance_score": 0.95, "reason": "서버 알람 목록 조회 — alarm_query 의도", "sub_query_context": "현재 활성 알람 목록 조회", "user_specified": false}}
    ]
}}
```

입력: "김포 폴스타에서 critical 알람 이력을 조회해줘"
출력:
```json
{{
    "intent": "alarm_query",
    "databases": [
        {{"db_id": "polestar_cm_gp", "relevance_score": 1.0, "reason": "사용자가 김포 폴스타 직접 지정, critical 알람 이력 조회", "sub_query_context": "critical 심각도 알람 이력 조회", "user_specified": true}}
    ]
}}
```

입력: "여의도 개발 서버 중 CPU 임계값 초과 알람이 발생한 서버 목록"
출력:
```json
{{
    "intent": "alarm_query",
    "databases": [
        {{"db_id": "polestar_cm_yd", "relevance_score": 0.95, "reason": "여의도 개발 환경 CPU 알람 조회", "sub_query_context": "CPU 임계값 초과 알람 발생 서버 목록 조회", "user_specified": false}}
    ]
}}
```

입력: "이번 달 서버의 CPU 및 메모리 알람 이력을 보여줘"
출력:
```json
{{
    "intent": "alarm_query",
    "databases": [
        {{"db_id": "polestar_b0", "relevance_score": 0.95, "reason": "서버 CPU/메모리 키워드 알람 이력 조회 — alarm_query 의도", "sub_query_context": "서버 CPU 및 메모리 알람 이번 달 이력 조회", "user_specified": false}}
    ]
}}
```

입력: "장비 담당 부서와 그 장비의 최근 상태를 알려줘"
출력:
```json
{{
    "intent": "data_query",
    "databases": [
        {{"db_id": "itam", "relevance_score": 0.9, "reason": "자산 담당 부서 정보 보유", "sub_query_context": "장비별 담당 부서 조회", "user_specified": false}},
        {{"db_id": "polestar_b0", "relevance_score": 0.6, "reason": "장비 상태 지표를 보유하나 '최근 상태'의 범위가 모호 — 가능성 있는 매칭", "sub_query_context": "장비 최근 상태 지표 조회", "user_specified": false}}
    ]
}}
```

입력: "최근 알람이 좀 있었던 것 같은데, 관련된 변경 작업도 있었는지 같이 봐줘"
출력:
```json
{{
    "intent": "alarm_query",
    "databases": [
        {{"db_id": "polestar_b0", "relevance_score": 0.85, "reason": "최근 알람 이력 조회", "sub_query_context": "최근 알람 이력 조회", "user_specified": false}},
        {{"db_id": "itsm", "relevance_score": 0.4, "reason": "관련 변경 작업이 있을 수 있으나 질의가 대상을 명시하지 않음 — 약한 연관", "sub_query_context": "최근 변경 작업 이력 조회", "user_specified": false}}
    ]
}}
```

"""

_S_DB_GUIDE = """## DB 설명 조회 의도

사용자가 "어떤 DB가 있어?", "DB 목록을 보여줘", "사용 가능한 데이터베이스 목록" 등
DB 목록/설명 조회를 요청하는 경우, intent를 "cache_management"로 설정하고
action을 "db-guide"로 설정하세요.

"""

_S_CACHE = """## 캐시 관리 의도 분류

사용자가 스키마 캐시를 관리하려는 요청인 경우, intent를 "cache_management"로 설정하세요.

캐시 관리 관련 키워드 (아래 키워드가 포함되면 intent를 "cache_management"로):
- 캐시: "캐시 생성", "캐시 갱신", "캐시 삭제", "캐시 상태", "스키마 캐시"
- 유사 단어: "유사 단어 생성", "유사 단어 보여줘", "유사 단어 추가", "유사 단어 삭제",
  "유사 단어 변경", "유사 단어 목록", "유사 단어를 만들어줘", "유사 단어를 갱신"
- 컬럼 설명: "컬럼 설명 생성", "컬럼 설명 보여줘", "컬럼 설명 변경", "설명을 수정",
  "설명을 추가", "설명을 변경"
- DB 설명: "DB 설명 생성", "DB 설명 설정", "DB 설명 변경", "DB 설명을 만들어줘"
- 재활용 응답: "재활용", "새로 생성", "병합" (이전 질문에 대한 짧은 응답)

주의: "재활용", "새로 생성", "병합" 등 짧은 단어만 입력된 경우에도
데이터 조회가 아닌 캐시 관리 의도로 분류하세요.

캐시 관리 요청 예시:
- "polestar DB의 스키마 캐시를 생성해줘" -> intent: "cache_management"
- "전체 DB 캐시 상태를 보여줘" -> intent: "cache_management"
- "polestar 캐시를 삭제해줘" -> intent: "cache_management"
- "hostname의 유사 단어를 보여줘" -> intent: "cache_management"
- "hostname에 '서버호스트' 유사 단어를 추가해줘" -> intent: "cache_management"
- "hostname의 유사 단어를 생성해줘" -> intent: "cache_management"
- "hostname 컬럼의 설명을 변경해줘" -> intent: "cache_management"
- "DB 설명을 생성해줘" -> intent: "cache_management"
- "재활용" -> intent: "cache_management"
- "병합" -> intent: "cache_management"

"""

_S_FAULT_SLOT = """{fault_diagnosis_section}"""

_S_PRIORITY = """## intent 판단 우선순위

**반드시 아래 순서대로 검토하고, 먼저 해당하는 intent로 분류하세요.**

1. **cache_management 우선**: 캐시, 유사어/유사 단어, 컬럼 설명, DB 설명, 스키마 관련 키워드가 있으면 → `cache_management`
2. **alarm_query**: 알람, 모니터링, 임계값 초과, alert 관련이면 → `alarm_query`
3. **data_query**: 인프라 데이터 조회(서버, CPU, 메모리, 디스크, 네트워크, VM, 자산 등)가 필요하면 → `data_query`
4. **general_inference**: 위 항목 중 어디에도 해당하지 않을 때만 → `general_inference`

`general_inference`는 **최후 수단(last resort)**입니다. 에이전트가 다룰 수 있는 영역과 조금이라도 관련이 있으면 다른 intent로 분류하세요.

"""

_S_GENERAL = """## 일반 추론 의도 (general_inference)

**위의 cache_management, alarm_query, data_query 중 어디에도 해당하지 않을 때만** intent를 "general_inference"로 설정하고 databases를 빈 배열([])로 반환하세요.

### 양성 조건 (아래 조건을 만족하고, 음성 조건에 해당하지 않을 때만 general_inference)

1. [에이전트 외부 IT 개념 설명] 이 에이전트의 기능·운영과 무관한 순수 IT/인프라 개념 질문
   - 패턴: "~란?", "~가 뭐야?", "~의 차이는?", "~는 어떻게 동작해?"
   - 예: "쿠버네티스란?", "RAID 5와 RAID 6의 차이는?", "로드밸런서가 뭔지 설명해줘"
   - **주의**: "유사어란?", "캐시란?", "유사 단어가 뭐야?" 등 에이전트 자체 기능에 대한 "~란?" 질문은 → `cache_management`

2. [순수 기능 문의] 에이전트가 할 수 있는 것을 포괄적으로 묻는 질문 (특정 기능 아님)
   - 예: "너는 뭘 할 수 있어?", "어떤 기능이 있어?"
   - **주의**: "유사어 기능이 뭐야?", "캐시 관리가 어떻게 돼?" 등 특정 기능 문의는 → `cache_management`

3. [범위 외 요청] DB 조회·에이전트 기능과 완전히 무관한 작업 요청
   - 예: "코드 짜줘", "문서 만들어줘", "이메일 초안 작성해줘", "번역해줘"

4. [인사·감사·단순 확인]
   - 예: "안녕!", "고마워", "알겠어", "확인했어"

### 음성 조건 (아래 중 하나라도 해당하면 general_inference 절대 금지)

- **에이전트 고유 기능 키워드 포함**: 유사어, 유사 단어, 캐시, 컬럼 설명, DB 설명, 스키마, cache
  → 이 키워드가 있으면 형태("란?", "뭐야?", "삭제", "추가")에 무관하게 반드시 `cache_management`
- **인프라 데이터 조회**: 서버, CPU, 메모리, 디스크, 네트워크, VM, 자산, 프로세스, 장비, 사용률, 스펙 등
- **알람·모니터링 키워드**: 알람, alert, 모니터링, 임계값, 이력
- **특정 DB 명칭**: polestar, cloud_portal, itsm, itam, 폴스타, 포탈 등
- **조회 동사**: "~목록 보여줘", "~에서 조회해줘", "~찾아줘"

### 예시

입력: "쿠버네티스가 뭐야?"
출력: {{"intent": "general_inference", "databases": []}}

입력: "안녕!"
출력: {{"intent": "general_inference", "databases": []}}

입력: "코드 짜줘"
출력: {{"intent": "general_inference", "databases": []}}

입력: "유사어 삭제란?"  (← "유사어" 키워드 → cache_management 우선)
출력: {{"intent": "cache_management", "databases": []}}

입력: "유사 단어가 뭐야?"  (← "유사 단어" 키워드 → cache_management 우선)
출력: {{"intent": "cache_management", "databases": []}}

입력: "캐시란 무엇인가요?"  (← "캐시" 키워드 → cache_management 우선)
출력: {{"intent": "cache_management", "databases": []}}

입력: "우리 서버 중 CPU 사용률 높은 것 보여줘"  (← 데이터 조회 필요)
출력: {{"intent": "data_query", "databases": [...]}}

캐시 관리가 아닌 일반 데이터 조회 요청이면 intent를 "data_query"로 설정하세요 (기본값).
알람/모니터링 이벤트 조회 요청이면 intent를 "alarm_query"로 설정하세요.

JSON에 "intent" 필드를 포함하세요:
```json
{{
    "intent": "data_query",
    "databases": [...]
}}
```

반드시 유효한 JSON만 출력하세요.
"""

# 단일 호출(현행) 프롬프트 — 위 절을 **순서 그대로** 이어 붙인 것이다.
# 이 조립이 추출 이전 문자열과 바이트 동일해야 한다(골든 테스트가 강제).
SEMANTIC_ROUTER_SYSTEM_PROMPT_TEMPLATE = (
    _S_HEADER
    + _S_DB_LIST
    + _S_USER_DB_SPEC
    + _S_MULTI_DB
    + _S_OUTPUT_JSON
    + _S_INTENT_CLASSES
    + _S_JUDGE_RULES
    + _S_ALARM
    + _S_EXAMPLES
    + _S_DB_GUIDE
    + _S_CACHE
    + _S_FAULT_SLOT
    + _S_PRIORITY
    + _S_GENERAL
)


# ══════════════════════════════════════════════════════════════════════════
# 2단 분리 프롬프트 (Plan 79 트랙 B / WU-D2 · `ROUTER_TWO_STAGE_ENABLED`)
# ══════════════════════════════════════════════════════════════════════════
#
# **본문은 위 절 상수를 재사용한다** — 새로 쓰는 것은 각 단계의 「출력 형식」뿐이다(D-053).
#
# 1단계가 라벨 하나만 내는 것이 이 트랙의 존재 이유다(B-0 ④): 멀티 DB 불변식(§1.1) 때문에
# 단일 호출에서는 순수 label-only가 불가능한데, intent만 떼면 그 단계에 한해 성립한다.
#
# **첫 줄이 라벨인 것이 핵심이다**(C-0). 라우터 평면 이동 후 그 자리의 logprob이 곧 의도 신뢰도가
# 된다 — 현행 JSON 선행 프롬프트는 첫 토큰이 구조 토큰이라 읽어도 무의미하다.
# 둘째 줄 JSON은 **자기보고** 신뢰도이며 잠정이다(`ROUTER_CONFIDENCE_SOURCE=self_report`).

# 1단계 전용 머리말. `_S_HEADER`를 그대로 쓰면 *"데이터베이스를 선택하는 전문가"* 로 시작해
# **지시가 모순된다** — 1단계는 DB를 고르지 않는다. 상충하는 지시는 분류를 떨어뜨리므로
# 단계 전용 머리말을 준다(새로 쓰는 텍스트는 이것과 각 단계의 「출력 형식」뿐이다).
_S_STAGE1_HEADER = """당신은 인프라 관련 질의의 **의도**를 분류하는 전문가입니다.
사용자의 질의가 어떤 종류의 요청인지 판단하세요. **이 단계에서는 데이터베이스를 고르지 않습니다.**

"""

_S_STAGE1_OUTPUT = """## 출력 형식

**첫 줄에 의도 라벨 하나만** 적으세요. 설명·따옴표·JSON을 붙이지 마세요.
둘째 줄에는 그 판단의 확신도를 JSON으로 적으세요.

```
alarm_query
{{"confidence": 0.9}}
```

- 첫 줄은 아래 라벨 중 **정확히 하나**입니다.
- confidence는 0.0~1.0이며, 확실하면 0.8 이상, 애매하면 0.5 미만을 적으세요.
- **애매해도 라벨을 고르세요.** 최종 판단은 시스템이 합니다 — 낮은 확신도를 그대로 적으면 됩니다.

"""

SEMANTIC_ROUTER_STAGE1_INTENT_TEMPLATE = (
    _S_STAGE1_HEADER
    + _S_STAGE1_OUTPUT
    + _S_INTENT_CLASSES
    + _S_ALARM
    + _S_DB_GUIDE
    + _S_CACHE
    + _S_FAULT_SLOT
    + _S_PRIORITY
    + _S_GENERAL
)

# 2단계는 intent가 **이미 확정된 상태**에서 DB만 고른다.
#
# B-2(컨텍스트 대역폭 손실) 완화: 2단계는 1단계의 내부 표현을 볼 수 없으므로, 확정된 intent에
# **해당하는 절만** 함께 넣는다(전량이 아니다 — 그러면 프롬프트 축소 이득 B-0 ③이 사라진다).
# 이 완화의 효과는 **미측정**이다(SPEC M-2 · 실 LLM 대조가 필요하다).

_S_STAGE2_OUTPUT = """## 출력 형식

반드시 아래 JSON 형식으로만 응답하세요. 추가 설명은 불필요합니다.
**의도(intent)는 이미 확정됐습니다 — 다시 판단하지 말고 DB만 고르세요.**

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

"""

_S_STAGE2_INTENT_CONTEXT = """## 확정된 의도

이번 질의의 의도는 **{confirmed_intent}** 로 이미 확정됐습니다.
아래 판단 근거를 참고해 **그 의도에 맞는 DB**를 고르세요.

{intent_section}
"""

SEMANTIC_ROUTER_STAGE2_DATABASE_TEMPLATE = (
    _S_HEADER
    + _S_STAGE2_INTENT_CONTEXT
    + _S_DB_LIST
    + _S_USER_DB_SPEC
    + _S_MULTI_DB
    + _S_STAGE2_OUTPUT
    + _S_JUDGE_RULES
    + _S_EXAMPLES
)

# 확정된 intent별로 2단계에 넘길 판단 근거 절 (B-2 완화).
# `data_query`는 전용 절이 없다 — DB 선택 절만으로 충분하므로 빈 문자열이다.
# `fault_diagnosis`는 옵트인이라 여기 두지 않고 호출부가 조건부로 넘긴다(계약 C-A).
STAGE2_INTENT_SECTIONS: dict[str, str] = {
    "data_query": "",
    "alarm_query": _S_ALARM,
    "cache_management": _S_CACHE,
    "general_inference": _S_GENERAL,
}

# 2단계 호출 자체가 불필요한 의도 — DB를 고를 대상이 없다(Q3).
# 조기 차단(신뢰도 기반)과 달리 **의도 종류로 판정**하므로 근거가 확실하다.
INTENTS_WITHOUT_DATABASES: frozenset[str] = frozenset(
    {"cache_management", "general_inference"}
)


# ── Plan 64 CW-B: 장애 진단 pull 위임 의도 (fault_diagnosis) ──
# fault_diagnosis_enabled=True일 때만 위 기본 프롬프트 뒤에 이 섹션을 덧붙인다.
# 옵트인 off면 이 섹션이 붙지 않아 프롬프트가 비트동일 → LLM이 fault_diagnosis를 절대
# 산출하지 않는다(라우팅 회귀 0). 범위를 좁게 못 박아 일반 데이터 조회(data_query)를
# 잠식하지 않게 한다(Known Mistakes: negative instruction은 범위를 좁게 유지).
# (Plan 79 A-3/A-5) fault_diagnosis는 옵트인(Plan 64 CW-B)이므로 **클래스 정의 줄과 절 본문
# 모두** 조건부다. 정의만 남겨도 LLM이 그 클래스를 알게 되어 off 상태에서 산출할 수 있는데,
# 그때는 그래프에 해당 노드가 없다. off면 두 상수 모두 빈 문자열로 치환된다.
# ── intent 클래스 정본 (Plan 79 트랙 E-1 · D-053 사본 금지) ────────────────────
# 「출력 형식」이 정의하는 클래스 집합의 **단일 출처**다. 라우팅 코드는 이 집합을 import해
# 쓰고, 별도 목록을 다시 적지 않는다 — 사본을 두면 프롬프트와 코드가 조용히 어긋난다.
#
# 프롬프트 **본문의 나열은 손대지 않았다**: 트랙 A 골든셋 회귀(plans/80 WU-05)가 아직
# 실행되지 않아 프롬프트 텍스트가 바뀌면 측정 기준이 흔들린다. 동기화는 문자열 렌더가 아니라
# 테스트가 강제한다(tests/test_semantic_routing/test_router_output_contract.py S3).
SEMANTIC_ROUTER_BASE_INTENTS: frozenset[str] = frozenset({
    "data_query",
    "alarm_query",
    "cache_management",
    "general_inference",
})

# 옵트인 플래그(fault_diagnosis_enabled)에 종속된 클래스 — Plan 64 CW-B · plans/80 계약 C-A.
# off일 때 허용 집합에 넣으면 그래프에 없는 노드로 라우팅된다.
SEMANTIC_ROUTER_OPTIN_INTENTS: frozenset[str] = frozenset({"fault_diagnosis"})


def allowed_intents(*, fault_diagnosis_enabled: bool = False) -> frozenset[str]:
    """플래그 상태에 따른 허용 intent 집합.

    Args:
        fault_diagnosis_enabled: 장애 진단 옵트인 여부. False면 fault_diagnosis가 빠진다.

    Returns:
        LLM이 산출해도 되는 intent 문자열 집합.
    """
    if fault_diagnosis_enabled:
        return SEMANTIC_ROUTER_BASE_INTENTS | SEMANTIC_ROUTER_OPTIN_INTENTS
    return SEMANTIC_ROUTER_BASE_INTENTS


SEMANTIC_ROUTER_FAULT_DIAGNOSIS_CLASS_LINE = (
    "\n- fault_diagnosis: 특정 서버/장비의 장애 **원인 분석·진단** 요청 (단순 조회가 아님)"
)

SEMANTIC_ROUTER_FAULT_DIAGNOSIS_SECTION = """

## 장애 진단 의도 (fault_diagnosis) — 최우선 검토

사용자가 특정 서버/장비의 **장애 원인 분석·진단**을 명시적으로 요청하면 intent를
"fault_diagnosis"로 설정하세요. 이는 단순 데이터 조회(data_query)가 아니라 "왜 이런 문제가
발생했는가"를 진단·해석해 달라는 요청입니다.

fault_diagnosis로 분류할 질의 패턴(장애·이상 상황 + 원인/진단 동사):
- "○○ 서버 원인 분석해줘", "○○ 장애 진단해줘", "○○ 왜 죽었어?", "○○ 왜 느려?"
- "이 서버 문제 원인 좀 봐줘", "장애 원인 파악해줘", "무슨 일이 있었는지 분석해줘"
- "해당 서버 이상 원인 진단", "RCA 해줘", "근본 원인 분석"

판단 규칙:
- 대상 DB(폴스타 인스턴스)를 식별할 수 있으면 위 data_query와 동일하게 databases에 포함하세요
  (진단 대상 스코프 힌트). 식별 불가하면 databases는 빈 배열([])로 두어도 됩니다.
- **단순 수치 조회는 fault_diagnosis가 아닙니다**: "CPU 사용률 보여줘", "메모리 얼마야?",
  "서버 목록", "알람 현황"은 각각 data_query/alarm_query입니다. "원인·진단·왜"가 없는
  단순 조회는 fault_diagnosis로 분류하지 마세요.
- 알람 목록/이력 조회는 alarm_query이고, 그 알람의 **원인 진단** 요청이면 fault_diagnosis입니다.

예시:
입력: "web-01 서버 장애 원인 분석해줘"
출력: {{"intent": "fault_diagnosis", "databases": [{{"db_id": "polestar_b0", "relevance_score": 0.9, "reason": "web-01 장애 원인 진단 대상", "sub_query_context": "web-01 장애 원인 진단", "user_specified": false}}]}}

입력: "여의도 폴스타 db-02 왜 느린지 진단해줘"
출력: {{"intent": "fault_diagnosis", "databases": [{{"db_id": "polestar_cm_yd", "relevance_score": 1.0, "reason": "사용자가 여의도 폴스타 지정, db-02 성능 저하 원인 진단", "sub_query_context": "db-02 성능 저하 원인 진단", "user_specified": true}}]}}

입력: "web-01 CPU 사용률 보여줘"  (← 단순 조회 → data_query)
출력: {{"intent": "data_query", "databases": [...]}}
"""
