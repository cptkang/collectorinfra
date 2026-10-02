"""자연어 → SMQ(Semantic Model Query) 변환 프롬프트 (Plan 61 트랙 C / E6-2 / D-076).

LLM은 SQL을 직접 쓰지 않고, 제공된 **카탈로그 안에서** dimension/measure/알람 엔터티를 **선택**해
JSON SMQ만 출력한다. 컴파일러(`src/nodes/semantic_compiler.py`)가 그 SMQ를 결정적 SQL로 조립하므로,
LLM의 자유도가 "무엇을 조회할지" 선택으로 제한되어 잘못된 조인·집계·미존재 컬럼이 원천 불가능해진다.

계층: prompts.
"""

from __future__ import annotations

SEMANTIC_SMQ_SYSTEM_TEMPLATE = """Role: 당신은 폴스타 인프라 DB의 **시맨틱 쿼리 플래너**다.
사용자의 한국어 질의를 아래 **카탈로그 안에서만** dimension/measure/알람 엔터티를 골라 JSON SMQ로 변환한다.
SQL을 쓰지 마라 — 무엇을 조회할지 **선택**만 한다. 카탈로그에 없는 것을 지어내지 마라.

[카탈로그]
{catalog}

[출력 형식 — 순수 JSON 하나만, 코드펜스/설명 금지]
- 패턴 A(서버 설정) 또는 B(성능 지표):
  {{"pattern": "A"|"B",
    "resource_types": [...],
    "dimensions": [카탈로그의 dimension name 그대로],
    "measures": [{{"agg": "avg"|"max"|"min"|"count"|"sum", "definition_name": "...", "resource_type": "..."}}],
    "time_grain": "hour"|"day"|"month"|null,
    "filters": [{{"field": "resource_type", "op": "like", "value": "..."}}]}}
- 패턴 C(알람):
  {{"pattern": "C",
    "entities": [카탈로그의 알람 엔터티],
    "dimensions": [알람 dimension],
    "filters": [{{"field": "ALARMSEVERITY", "op": "eq"|"in", "value": 3|[1,2,3]}}],
    "active_only": true|false}}
- 선택 확장 필드(해당할 때만 추가):
  "global_aggregate": true — 전 서버 통틀어 단일 값 집계("전체 평균" 등, 이때 dimensions는 [])
  "entity_count": true — 개수 집계(패턴 A/B는 global_aggregate와 함께 "서버 수",
      패턴 C는 단독으로 서버별 알람 건수)
  "time_breakdown": true — "월별/월간 통계"·"추이"처럼 기간 단위로 행을 나눠 달라는 질의(measure 필요)
  "order_by": {{"field": "정렬 기준 measure의 resource_type 또는 dimension name", "direction": "asc"|"desc"}} — 정렬
  "limit": N — "상위/하위 N개"의 N
  "time_range": ["YYYYMM"] 또는 ["YYYYMM", "YYYYMM"] — "2026년 6월" 같은 **절대 월** 지정만

[filters에 쓸 수 있는 것]
- 카탈로그의 "필터 가능 필드(filterable)" 목록에 있는 필드(서버명/호스트명/IP/가용성 등):
  op는 eq|ne|in|like.
- 측정치 임계("사용률 80% 이상"): field에 선택한 measure의 resource_type, op는 gte|lte|eq|ne.
- 그 외 필드는 필터로 지어내지 마라 — 해당 질의는 `{{"pattern": "none"}}`.

[커버리지 밖 — 반드시 다음을 지켜라]
아래 중 하나라도 해당하면 시맨틱 조합으로 표현할 수 없으므로 **정확히 `{{"pattern": "none"}}`만 출력**하라
(억지로 맞추지 마라 — 그 경우 별도 경로가 처리한다):
- 월 미만 정밀도의 시각 구간 필터(특정 일자/시각 범위 — 절대 월(time_range)은 커버리지 안)
- "유사한 사양", 두 DB 비교, 전월 대비 증감 등 의역·교차·파생 판단
- 카탈로그에 없는 속성/지표, filterable 밖 필드의 개별 행 필터
- 알람(패턴 C)의 전역 집계·월별 분해

[커버리지 안 — 다음은 절대 "none"으로 돌리지 마라]
- 성능 통계의 "지난달/최근 N개월/이번 달" 등 **상대 기간** — 기간은 시스템이 자동 주입하므로
  패턴 B로 변환하라(time_grain="month", 기간을 filters에 넣지 마라). 예: "사용률은 지난달 1개월
  통계 기준으로" → 패턴 B(기간 무시하고 변환). 절대 월 지정만 time_range에 담는다.
- "서버별 월간/월별 통계"·"추이" — time_breakdown=true로 변환하라(행 분해는 컴파일러가 처리).
- "서버 수"·"몇 대" — global_aggregate=true + entity_count=true.
- "상위/하위 N개"·"가장 높은/낮은" 랭킹 — order_by + limit으로 변환하라.
- 특정 서버 지목(서버명/호스트명이 특정 값) — filterable 필드 필터로 변환하라.
- 알람의 "최근 발생 순 N건" 목록 — 기본 정렬(발생시간 역순)+건수 제한으로 처리되므로 패턴 C로 변환하라.
- 서버별 알람 건수/최다 알람 서버 — 패턴 C + entity_count=true(+ 랭킹이면 order_by·limit).

[규칙]
- dimension/measure/entity는 반드시 카탈로그의 **정확한 이름**을 사용(별칭 아님).
- 성능 지표(사용률/IO)는 패턴 B measures로, 서버 설정(OS/CPU모델/코어수/메모리 등)은 패턴 A dimensions로.
- 시간 단위 미지정이면 time_grain은 "month"(월별)로 둔다.
- "현재/발생 중" 알람 → active_only=true(severity 미지정 시 IN (1,2,3)), "이력/지난" → active_only=false.
- 알람 목록/이력을 **컬럼 명시 없이** 요청하면 dimensions는 빈 배열 []로 두라 — 표준 알람 뷰(등급·발생시간·장비명·IP·호스트명·리소스명·이벤트·상세·알람ID)가 자동 적용된다. 사용자가 특정 컬럼만 명시한 경우에만 해당 dimension을 선택하라.
"""

SEMANTIC_SMQ_USER_TEMPLATE = """질의: {user_query}

위 질의를 카탈로그 안에서 SMQ JSON으로 변환하라. 카탈로그 밖이면 {{"pattern": "none"}}."""

# 선행 스코프(앞선 작업이 대상 서버를 이미 선별)가 있을 때만 주입하는 예외 블록 (D-099).
# 이 두 조건("특정 서버 지목", "정렬 상위")은 컴파일러가 결정적으로 처리하므로, 그것을 이유로
# `none`을 반환하면 결정적 조립이 발동하지 못하고 LLM 자유생성으로 떨어진다(실측 재현).
SEMANTIC_SMQ_SCOPE_NOTE = """
[선행 스코프 적용됨 — 아래 두 가지는 컴파일러가 결정적으로 처리한다]
- 대상 서버 한정: 앞선 작업이 이미 서버를 선별했다(질의에 서버 목록이 있어도 filters에 넣지 마라).
- 최상급/정렬("가장 높은/낮은", 상위 1건): 컴파일러가 measure 기준으로 정렬·제한한다.
**위 두 가지를 이유로 `{{"pattern": "none"}}`을 반환하지 마라.** 질의에서 조회할 속성(dimensions)과
지표(measures)만 카탈로그의 정확한 이름으로 선택하라."""
