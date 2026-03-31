# Plan 22: LLM 지능형 필드 매핑 + 매핑 보고서 + 사용자 피드백 학습

> 작성일: 2026-03-24
> 상태: **구현 완료** (2026-03-24)
> 선행: Plan 21 (EAV Field Mapper 지원) — 구현 완료, D-012 (Mapping-First 전략)

---

## 1. 배경 및 문제 정의

### 1.1 현재 3단계 매핑의 한계

현재 `perform_3step_mapping()` (src/document/field_mapper.py)의 2단계(Redis synonyms)는 **정확한 문자열 일치**만 수행한다:

```python
# _synonym_match() — 현재 방식
def _synonym_match(field_lower, synonyms):
    for col_key, words in synonyms.items():
        for word in words:
            if word.lower().strip() == field_lower:  # ← 완전 일치만
                return col_key
```

**실패 사례:**

| 양식 필드 | Redis synonyms | 현재 결과 | 기대 결과 |
|----------|---------------|-----------|----------|
| "서버 호스트명" | `hostname: ["서버명", "호스트"]` | 매칭 실패 → LLM 폴백 | "서버명"과 유사 → 매칭 성공 |
| "CPU사용률(%)" | `cpu_usage: ["CPU 사용률", "씨피유"]` | 매칭 실패 (공백/특수문자 차이) | 의미 동일 → 매칭 성공 |
| "운영 체제" | EAV: `OSType: ["OS종류", "운영체제"]` | 매칭 실패 (띄어쓰기 차이) | "운영체제"와 유사 → 매칭 성공 |
| "물리메모리(GB)" | `memory_total: ["메모리", "RAM"]` | 매칭 실패 | "메모리" 포함 → 매칭 성공 |

### 1.2 LLM 폴백의 비효율

2단계 매칭 실패 시 3단계 LLM 호출로 넘어가는데:
- LLM 호출 비용 (토큰 소모)
- 응답 지연 (1~3초 추가)
- Redis에 이미 유사한 정보가 있음에도 활용하지 못함

### 1.3 매핑 결과의 불투명성

- LLM이 추론한 매핑 결과를 사용자가 검토할 방법이 없음
- `pending_synonym_registrations`는 응답 텍스트에 포함되지만 구조화되지 않음
- 사용자가 매핑을 수정하거나 피드백할 체계가 없음

### 1.4 해결 목표

1. **Redis 유사어 + LLM 추론 결합**: 정확 매칭 실패 시, Redis의 유사어/설명 정보를 컨텍스트로 LLM에게 "유사도 기반 매핑"을 위임
2. **LLM 매핑 결과를 기본적으로 Redis에 등록**: LLM 추론 결과는 즉시 Redis synonyms에 저장하여 다음 조회부터 정확 매칭으로 활용
3. **매핑 보고서 MD 파일 생성 및 다운로드**: 등록된 매핑 정보를 구조화된 MD 파일로 생성하여 사용자에게 제공
4. **사용자 MD 수정/업로드로 Redis 반영**: 사용자가 MD 파일을 다운로드 → 수정 → 재업로드하면 LLM이 변경사항을 분석하여 Redis 매핑 정보를 갱신

---

## 2. 전체 아키텍처

### 2.1 개선된 매핑 흐름

```
[기존 3단계]
  1단계: 프롬프트 힌트    (사용자 명시 매핑)
  2단계: Redis synonyms   (정확 문자열 일치)  ← 한계
  2.5단계: EAV synonyms   (정확 문자열 일치)  ← 한계
  3단계: LLM 추론         (스키마 descriptions 기반)

[개선 3+1단계]
  1단계: 프롬프트 힌트    (사용자 명시 매핑)         — 변경 없음
  2단계: Redis synonyms   (정확 문자열 일치)         — 변경 없음 (fast path)
  2.5단계: EAV synonyms   (정확 문자열 일치)         — 변경 없음 (fast path)
  ★ 3단계: LLM 통합 추론 (Redis 유사어 + DB descriptions + EAV names를 컨텍스트로,
           남은 전체 필드를 한번에 LLM에 전달하여 1회 호출로 매핑 완료)     — 신규
           → LLM 응답을 파싱하여 각 매핑을 즉시 Redis synonyms에 등록
  ★ 후처리: 매핑 보고서 MD 생성 → 사용자 다운로드 → 수정/업로드 → Redis 반영
```

**핵심 변경: 기존 3단계(LLM 전체 추론)를 대체**

기존에는 3단계에서 DB descriptions만으로 추론했으나, 개선안에서는 Redis 유사어 + DB descriptions + EAV names를 **모두 컨텍스트로 결합**하여 단일 LLM 호출로 처리한다. 이를 통해:
- LLM 호출 횟수: 기존 1회 → 개선 1회 (동일, 단 컨텍스트가 풍부해짐)
- 별도의 "LLM 유사어 추론" 단계를 두지 않고 기존 3단계를 **강화**하는 방식
- LLM 응답에서 각 필드의 매핑 결과 + 매칭 근거를 파싱하여 **즉시 Redis에 저장**

### 2.2 사용자 피드백 루프 (MD 파일 수정/업로드 방식)

```
field_mapper 완료
  ↓
LLM 매핑 결과를 즉시 Redis synonyms에 등록
  ↓
등록된 매핑 정보를 MD 보고서로 생성 (mapping_report_{timestamp}.md)
  ↓
SSE로 매핑 보고서 다운로드 링크 전달
  ↓
사용자가 MD 파일 다운로드 → 내용 검토
  ↓
[수정 필요 시] 사용자가 MD 파일을 직접 편집 (매핑 테이블의 컬럼 수정/삭제)
  ↓
수정된 MD 파일을 업로드 (/query/mapping-feedback 엔드포인트)
  ↓
LLM이 원본 MD와 수정 MD를 비교 분석 → 변경사항 추출
  ↓
변경사항을 Redis synonyms에 반영 (추가/수정/삭제)
  ↓
(선택) 매핑이 수정된 경우 → SQL 재생성 트리거
```

**핵심: "기본 등록 → 사후 수정" 전략**

기존 방식은 LLM 매핑을 pending 상태로 두고 사용자 승인을 기다렸으나,
개선안에서는 **LLM 매핑을 즉시 Redis에 등록**하고, 문제가 있으면 사용자가 MD 파일을 수정/업로드하여 교정한다.
이 방식의 장점:
- 사용자가 피드백하지 않아도 다음 조회부터 즉시 학습 효과 발생
- MD 파일이 매핑 현황의 단일 진실 소스(single source of truth) 역할
- 자연어 피드백 파싱의 불확실성 제거 (구조화된 MD 테이블로 명확한 의도 전달)

---

## 3. 상세 설계

### Phase A: LLM 통합 추론 + 즉시 Redis 등록 (기존 3단계 강화)

#### A-1. 기존 `_apply_llm_mapping()` 강화: Redis 유사어 컨텍스트 통합

**파일**: `src/document/field_mapper.py`

기존 3단계 LLM 전체 추론 함수를 강화한다. 별도의 "LLM 유사어 추론" 단계를 추가하는 대신, **기존 LLM 호출에 Redis 유사어 정보를 컨텍스트로 결합**하여 1회 호출로 처리한다.

```python
async def _apply_llm_mapping_with_synonyms(
    llm: BaseChatModel,
    remaining_fields: list[str],
    all_db_synonyms: dict[str, dict[str, list[str]]],
    all_db_descriptions: dict[str, dict[str, str]],
    eav_name_synonyms: dict[str, list[str]] | None,
    priority_db_ids: list[str],
    result: MappingResult,
    example_rows: Optional[list[list[str]]] = None,
) -> list[dict]:
    """Redis 유사어 + DB descriptions를 결합하여 전체 필드를 1회 LLM 호출로 매핑한다.

    기존 _apply_llm_mapping()을 대체한다.

    핵심 변경:
    - 프롬프트에 Redis synonyms + DB descriptions + EAV names를 모두 포함
    - 남은 전체 필드를 한번에 전달하여 LLM 호출 1회로 완료
    - LLM 응답에서 각 필드의 매칭 근거(reason)와 신뢰도(confidence)를 함께 추출
    - 매핑 결과를 즉시 Redis synonyms에 등록 (pending 없이 기본 등록)

    Returns:
        LLM 추론 상세 정보 리스트 (보고서 생성용)
        [{"field": "...", "column": "...", "db_id": "...",
          "matched_synonym": "...", "confidence": "...", "reason": "..."}]
    """
```

**기존 대비 변경점:**

| 항목 | 기존 `_apply_llm_mapping()` | 개선 `_apply_llm_mapping_with_synonyms()` |
|------|---------------------------|------------------------------------------|
| 컨텍스트 | DB descriptions만 | Redis synonyms + descriptions + EAV names |
| LLM 응답 형식 | `{field: {db_id, column}}` | `{field: {db_id, column, matched_synonym, confidence, reason}}` |
| 매핑 후 처리 | `pending_synonym_registrations` 생성 | **즉시 Redis synonyms에 등록** |
| source 태그 | `llm_inferred` | `llm_inferred` (유사어 컨텍스트 활용 여부와 무관하게 통일) |

#### A-2. LLM 통합 추론 프롬프트

**파일**: `src/prompts/field_mapper.py`

기존 `FIELD_MAPPER_MULTI_DB_SYSTEM_PROMPT`를 확장하여 Redis 유사어 컨텍스트를 포함한다.

```python
FIELD_MAPPER_ENHANCED_SYSTEM_PROMPT = """당신은 양식 필드명과 여러 데이터베이스의 컬럼 간 매핑 전문가입니다.

사용자가 제공하는 양식 필드 목록과 DB 스키마 정보(컬럼 설명 + 유사어)를 분석하여,
각 양식 필드에 가장 적합한 DB의 테이블.컬럼을 매핑하세요.

## 매핑 규칙

1. **유사어 우선 매칭**: 컬럼에 등록된 유사어(synonyms)가 양식 필드와 유사하면 우선 매칭
   - 부분 문자열 포함 (예: "물리메모리(GB)" → "메모리" 포함)
   - 동의어/유의어 관계 (예: "호스트명" ↔ "서버명")
   - 약어 확장 (예: "CPU사용률(%)" ↔ "CPU 사용률")
   - 띄어쓰기/특수문자 차이 무시 (예: "운영 체제" ↔ "운영체제")
   - 한국어-영어 대응 (예: "아이피" ↔ "IP")
2. **컬럼 설명(description) 기반 매칭**: 유사어에 없으면 컬럼 설명의 의미를 분석
3. 확신이 없으면 null. 잘못된 매핑은 null보다 나쁩니다.
4. 하나의 필드는 하나의 DB의 하나의 컬럼에만 매핑
5. EAV 속성은 "EAV:속성명" 형식으로 매핑

## 출력 형식 (JSON)
{
    "필드명": {
        "db_id": "DB식별자",
        "column": "테이블.컬럼",
        "matched_synonym": "매칭에 활용된 유사어 (없으면 null)",
        "confidence": "high|medium|low",
        "reason": "매칭 근거 (한국어, 1줄)"
    }
}
매핑 불가: "필드명": null
"""

FIELD_MAPPER_ENHANCED_USER_PROMPT = """## 매핑 대상 양식 필드
{field_names}

## DB별 스키마 정보 (컬럼 설명 + 유사어)
{db_schema_with_synonyms}

## EAV 속성 유사어 (있는 경우)
{eav_context}

각 필드에 대해 가장 적합한 DB와 컬럼을 매핑하세요.
유사어가 있는 컬럼을 우선 검토하고, 매칭 근거를 함께 설명하세요.
확신이 없는 필드는 null로 표시하세요.

JSON 형식으로만 응답:"""
```

#### A-3. `perform_3step_mapping()` 확장

**파일**: `src/document/field_mapper.py`

기존 함수의 3단계(LLM 추론)를 강화된 버전으로 교체한다. 기존 호출부는 하위 호환 유지.

```python
async def perform_3step_mapping(
    llm: BaseChatModel,
    field_names: list[str],
    field_mapping_hints: list[dict],
    all_db_synonyms: dict[str, dict[str, list[str]]],
    all_db_descriptions: dict[str, dict[str, str]],
    priority_db_ids: list[str],
    example_rows: Optional[list[list[str]]] = None,
    eav_name_synonyms: dict[str, list[str]] | None = None,
    cache_manager: Optional[Any] = None,  # ★ 신규: Redis 즉시 등록용
) -> tuple[MappingResult, list[dict]]:  # ★ 반환값 확장: 추론 상세 포함
    # 1단계: 힌트 (기존)
    # 2단계: Redis synonyms 정확 매칭 (기존)
    # 2.5단계: EAV synonyms 정확 매칭 (기존)

    # ★ 3단계: LLM 통합 추론 (Redis 유사어 + descriptions 결합, 전체 필드 1회 호출)
    llm_inference_details = []
    if remaining:
        llm_inference_details = await _apply_llm_mapping_with_synonyms(
            llm, list(remaining),
            all_db_synonyms, all_db_descriptions,
            eav_name_synonyms, priority_db_ids, result,
            example_rows=remaining_examples,
        )

        # ★ LLM 매핑 결과를 즉시 Redis에 등록
        if cache_manager and llm_inference_details:
            await _register_llm_mappings_to_redis(
                cache_manager, llm_inference_details
            )

    return result, llm_inference_details
```

#### A-4. LLM 매핑 결과 즉시 Redis 등록

**파일**: `src/document/field_mapper.py`

```python
async def _register_llm_mappings_to_redis(
    cache_manager: Any,
    llm_inference_details: list[dict],
) -> int:
    """LLM 추론 매핑 결과를 즉시 Redis synonyms에 등록한다.

    Args:
        cache_manager: SchemaCacheManager 인스턴스
        llm_inference_details: LLM 추론 결과 리스트

    Returns:
        등록된 유사어 수
    """
    registered = 0
    for detail in llm_inference_details:
        field = detail.get("field")
        column = detail.get("column")
        db_id = detail.get("db_id")
        if not field or not column or not db_id:
            continue

        # EAV 매핑은 eav_name_synonyms에 등록
        if column.startswith("EAV:"):
            eav_name = column[4:]
            existing = await cache_manager.get_eav_name_synonyms()
            words = existing.get(eav_name, [])
            if field not in words:
                words.append(field)
                existing[eav_name] = words
                await cache_manager.save_eav_name_synonyms(existing)
                registered += 1
        else:
            # 일반 컬럼 synonyms에 등록
            await cache_manager.add_synonyms(
                db_id, column, [field], source="llm_inferred"
            )
            registered += 1

    logger.info("LLM 매핑 결과 Redis 등록: %d건", registered)
    return registered
```

#### A-5. mapping_sources 값 (변경 없음)

| 값 | 의미 | 단계 |
|----|------|------|
| `hint` | 사용자 프롬프트 힌트 | 1단계 |
| `synonym` | Redis 컬럼 유사어 정확 매칭 | 2단계 |
| `eav_synonym` | Redis EAV NAME 유사어 정확 매칭 | 2.5단계 |
| `llm_inferred` | LLM 통합 추론 (유사어+descriptions 컨텍스트) | 3단계 |

---

### Phase B: 매핑 보고서 MD 파일 생성

#### B-1. 매핑 보고서 생성 모듈

**신규 파일**: `src/document/mapping_report.py`

매핑 완료 후 결과를 구조화된 Markdown 파일로 생성한다.

```python
def generate_mapping_report(
    field_names: list[str],
    mapping_result: MappingResult,
    template_name: str | None = None,
    llm_inference_details: list[dict] | None = None,
) -> str:
    """매핑 결과를 Markdown 보고서로 생성한다.

    Args:
        field_names: 양식 필드명 목록
        mapping_result: MappingResult 객체
        template_name: 원본 양식 파일명 (선택)
        llm_inference_details: LLM 추론 상세 (confidence, reason 등)

    Returns:
        Markdown 문자열
    """
```

**보고서 형식 예시:**

```markdown
# 필드 매핑 보고서

> 생성일시: 2026-03-24 14:30:00
> 원본 양식: CMM_RESOURCE(873).xlsx
> 매핑 성공: 12/15 필드 (80%)

## 매핑 결과 요약

| # | 양식 필드 | 매핑 대상 | DB | 매핑 방법 | 신뢰도 |
|---|----------|----------|-----|----------|--------|
| 1 | 서버명 | CMM_RESOURCE.HOSTNAME | polestar | synonym (정확) | - |
| 2 | IP주소 | CMM_RESOURCE.IP_ADDRESS | polestar | synonym (정확) | - |
| 3 | 서버 호스트명 | CMM_RESOURCE.HOSTNAME | polestar | LLM+유사어 | high |
| 4 | CPU사용률(%) | cpu_metrics.usage_pct | monitoring | LLM+유사어 | medium |
| 5 | OS종류 | EAV:OSType | polestar | EAV synonym | - |
| 6 | 물리메모리(GB) | memory_metrics.total_gb | monitoring | LLM 추론 | medium |
| 7 | 비고 | (매핑 불가) | - | - | - |

## LLM 추론 매핑 상세

### 3. 서버 호스트명 → CMM_RESOURCE.HOSTNAME
- **매칭 근거**: "서버 호스트명"은 기존 유사어 "서버명", "호스트"와 의미적으로 동일
- **매칭된 유사어**: "서버명"
- **신뢰도**: high

### 4. CPU사용률(%) → cpu_metrics.usage_pct
- **매칭 근거**: "CPU사용률"은 기존 유사어 "CPU 사용률"과 공백/특수문자 차이만 있음
- **매칭된 유사어**: "CPU 사용률"
- **신뢰도**: medium

## 피드백 안내

매핑이 올바르지 않은 항목이 있으면 다음과 같이 피드백해주세요:
- "3번 매핑 맞아" → 해당 매핑을 유사어로 등록합니다
- "4번 틀렸어, cpu_pct가 아니라 cpu_percent야" → 매핑을 수정하고 유사어를 등록합니다
- "전부 맞아" → 모든 LLM 추론 매핑을 유사어로 등록합니다
- "6번 삭제해줘" → 해당 매핑을 제거합니다
```

#### B-2. field_mapper 노드에서 보고서 생성

**파일**: `src/nodes/field_mapper.py`

`field_mapper()` 함수에서 매핑 완료 후 **항상** 보고서를 생성한다 (LLM 추론 여부와 무관).
보고서에는 Redis에 등록된 모든 매핑 정보가 포함되므로 사용자가 전체 현황을 확인할 수 있다.

```python
async def field_mapper(state, *, llm=None, app_config=None) -> dict:
    # ... 기존 로직 ...

    # ★ 매핑 보고서 생성 (Redis에 등록된 매핑 포함)
    mapping_report_md = None
    if mapping_result.column_mapping:
        from src.document.mapping_report import generate_mapping_report
        mapping_report_md = generate_mapping_report(
            field_names=field_names,
            mapping_result=mapping_result,
            template_name=state.get("output_file_name"),
            llm_inference_details=llm_inference_details,
        )

    return {
        # ... 기존 필드 ...
        "mapping_report_md": mapping_report_md,  # ★ 신규
        "llm_inference_details": llm_inference_details,  # ★ 신규
    }
```

#### B-3. AgentState 확장

**파일**: `src/state.py`

```python
class AgentState(TypedDict):
    # ... 기존 필드 ...

    # === 매핑 보고서 ===
    mapping_report_md: Optional[str]                    # 매핑 보고서 MD 문자열
    llm_inference_details: Optional[list[dict]]          # LLM 추론 상세 정보
```

#### B-4. API 엔드포인트: 매핑 보고서 다운로드

**파일**: `src/api/routes/query.py`

```python
@router.get("/query/{query_id}/mapping-report")
async def download_mapping_report(query_id: str) -> StreamingResponse:
    """매핑 보고서 MD 파일을 다운로드한다."""
    if query_id not in _results_store:
        raise HTTPException(status_code=404, detail="결과를 찾을 수 없습니다.")

    stored = _results_store[query_id]
    report_md = stored.get("mapping_report_md")

    if not report_md:
        raise HTTPException(status_code=404, detail="매핑 보고서가 없습니다.")

    return StreamingResponse(
        io.BytesIO(report_md.encode("utf-8")),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="mapping_report_{query_id[:8]}.md"'
        },
    )
```

#### B-5. SSE 이벤트에 보고서 알림 추가

**파일**: `src/api/routes/query.py`

`field_mapper` 노드 완료 시 매핑 보고서 존재를 알린다:

```python
# _extract_node_progress() 확장
elif node_name == "field_mapper":
    mapping = output.get("column_mapping", {})
    sources = output.get("mapping_sources", {})
    has_report = output.get("mapping_report_md") is not None
    return {
        "mapped_count": sum(1 for v in mapping.values() if v is not None),
        "total_count": len(mapping),
        "sources": {
            "hint": sum(1 for s in sources.values() if s == "hint"),
            "synonym": sum(1 for s in sources.values() if s == "synonym"),
            "eav_synonym": sum(1 for s in sources.values() if s == "eav_synonym"),
            "llm_synonym_inferred": sum(1 for s in sources.values() if s == "llm_synonym_inferred"),
            "llm_inferred": sum(1 for s in sources.values() if s == "llm_inferred"),
        },
        "has_mapping_report": has_report,
    }
```

#### B-6. 프론트엔드: 매핑 보고서 다운로드 버튼

**파일**: `src/static/js/app.js`

`field_mapper` 노드 완료 이벤트에서 `has_mapping_report: true`면 다운로드 버튼 표시.

```
[채팅 응답 영역]
  ┌──────────────────────────────────────────┐
  │ 📋 필드 매핑 보고서가 생성되었습니다.       │
  │ [보고서 다운로드]  [매핑 피드백 보내기]     │
  └──────────────────────────────────────────┘
```

---

### Phase C: 사용자 MD 파일 수정/업로드 → LLM 분석 → Redis 반영

#### C-1. MD 파일 업로드 API 엔드포인트

**파일**: `src/api/routes/query.py`

```python
@router.post("/query/mapping-feedback")
async def process_mapping_feedback(
    request: Request,
    file: UploadFile = File(...),
    query_id: str = Form(...),
) -> dict:
    """수정된 매핑 보고서 MD 파일을 업로드하여 Redis에 반영한다.

    1. 원본 MD (query_id로 조회)와 수정 MD를 비교
    2. LLM으로 변경사항 분석
    3. 변경사항을 Redis synonyms에 반영 (추가/수정/삭제)
    4. 결과 요약 반환
    """
```

#### C-2. MD 파일 파싱 모듈

**파일**: `src/document/mapping_report.py` (보고서 생성 모듈에 추가)

```python
def parse_mapping_report(md_content: str) -> list[dict]:
    """매핑 보고서 MD 파일에서 매핑 테이블을 파싱한다.

    MD 파일의 '매핑 결과 요약' 테이블을 파싱하여 각 행의 정보를 추출한다.

    Args:
        md_content: MD 파일 전체 내용

    Returns:
        파싱된 매핑 목록
        [{"index": 1, "field": "서버명", "column": "CMM_RESOURCE.HOSTNAME",
          "db_id": "polestar", "method": "synonym (정확)", "confidence": "-"}]
    """
```

#### C-3. 변경사항 비교 및 LLM 분석

**파일**: `src/document/field_mapper.py`

```python
async def analyze_md_diff(
    llm: BaseChatModel,
    original_mappings: list[dict],
    modified_mappings: list[dict],
) -> dict:
    """원본과 수정된 매핑 보고서를 비교하여 변경사항을 추출한다.

    단순 비교로 처리 가능한 경우 (컬럼명 변경, 행 삭제 등)는 규칙 기반 비교.
    애매한 경우 (필드명 변경, 의미 불명확)만 LLM에 위임.

    Args:
        llm: LLM 인스턴스
        original_mappings: 원본 매핑 테이블 파싱 결과
        modified_mappings: 수정된 매핑 테이블 파싱 결과

    Returns:
        {
            "added": [{"field": "...", "column": "...", "db_id": "..."}],
            "modified": [{"field": "...", "old_column": "...", "new_column": "...", "db_id": "..."}],
            "deleted": [{"field": "...", "old_column": "..."}],
            "summary": "변경사항 요약"
        }
    """
```

#### C-4. 변경사항 Redis 반영

**파일**: `src/document/field_mapper.py`

```python
async def apply_mapping_feedback_to_redis(
    cache_manager: Any,
    diff_result: dict,
) -> dict:
    """MD 비교 결과를 Redis synonyms에 반영한다.

    Args:
        cache_manager: SchemaCacheManager 인스턴스
        diff_result: analyze_md_diff()의 반환값

    처리 로직:
    - added: 새 필드→컬럼 매핑을 Redis synonyms에 추가 (source: "user_corrected")
    - modified: 기존 유사어 제거 + 새 컬럼에 유사어 등록 (source: "user_corrected")
    - deleted: 해당 유사어를 Redis synonyms에서 제거

    Returns:
        {"registered": N, "modified": N, "deleted": N, "summary": "..."}
    """
```

#### C-5. 피드백 처리 전체 흐름

```
사용자가 수정된 MD 파일 업로드
  ↓
POST /query/mapping-feedback (file + query_id)
  ↓
원본 MD 조회 (_results_store[query_id]["mapping_report_md"])
  ↓
parse_mapping_report(원본) → 원본 매핑 리스트
parse_mapping_report(수정본) → 수정 매핑 리스트
  ↓
analyze_md_diff(원본, 수정본) → 변경사항 추출
  (단순 변경은 규칙 비교, 애매한 경우만 LLM)
  ↓
apply_mapping_feedback_to_redis(cache_manager, diff) → Redis 반영
  ↓
응답: {"registered": 2, "modified": 1, "deleted": 0, "summary": "..."}
```

**MD 파일 수정 방식의 장점:**
- 자연어 파싱의 불확실성 제거 — 테이블 구조가 곧 의도
- 사용자가 전체 매핑 현황을 한눈에 보고 일괄 수정 가능
- MD 파일 자체가 감사 이력(수정 전/후 비교 가능)
- 오프라인에서도 편집 가능 (텍스트 에디터, IDE 등)

---

## 4. 수정 대상 파일 요약

| 파일 | 변경 내용 | Phase | 우선순위 |
|------|----------|-------|---------|
| `src/document/field_mapper.py` | `_apply_llm_mapping_with_synonyms()` 신규 (기존 `_apply_llm_mapping` 대체), `_register_llm_mappings_to_redis()` 신규, `perform_3step_mapping()` 확장, `analyze_md_diff()` 신규, `apply_mapping_feedback_to_redis()` 신규 | A, C | P0 |
| `src/prompts/field_mapper.py` | `FIELD_MAPPER_ENHANCED_*` 프롬프트 신규 (기존 멀티DB 프롬프트 강화) | A | P0 |
| `src/document/mapping_report.py` | **신규 파일** — 매핑 보고서 MD 생성 + MD 파싱 (`generate_mapping_report`, `parse_mapping_report`) | B, C | P0 |
| `src/nodes/field_mapper.py` | 보고서 생성 호출, cache_manager 전달, LLM 즉시 등록 연동 | A, B | P0 |
| `src/state.py` | `mapping_report_md`, `llm_inference_details` 필드 추가 | B | P0 |
| `src/api/routes/query.py` | `/query/{id}/mapping-report` 다운로드, `/query/mapping-feedback` MD 업로드, SSE field_mapper 이벤트 보강 | B, C | P1 |
| `src/static/js/app.js` | 매핑 보고서 다운로드 버튼, MD 파일 업로드 UI | B, C | P2 |
| `tests/` | LLM 통합 추론, 보고서 생성/파싱, MD diff, Redis 반영 테스트 | 전체 | P1 |

---

## 5. 구현 순서

```
Step 1 (P0): LLM 통합 추론 + 즉시 Redis 등록
  ├── src/prompts/field_mapper.py        — FIELD_MAPPER_ENHANCED_* 프롬프트 추가
  ├── src/document/field_mapper.py       — _apply_llm_mapping_with_synonyms() 신규
  │                                         _register_llm_mappings_to_redis() 신규
  │                                         perform_3step_mapping() 확장 (cache_manager, 반환값)
  └── src/nodes/field_mapper.py          — cache_manager 전달, 즉시 등록 연동

Step 2 (P0): 매핑 보고서 MD 생성
  ├── src/document/mapping_report.py     — 신규 모듈 (generate_mapping_report)
  ├── src/state.py                       — mapping_report_md, llm_inference_details 추가
  └── src/nodes/field_mapper.py          — 보고서 생성 호출, State에 저장

Step 3 (P1): API 엔드포인트 + SSE 보강
  ├── src/api/routes/query.py            — GET /mapping-report (다운로드)
  ├── src/api/routes/query.py            — SSE field_mapper 이벤트 보강
  └── src/api/routes/query.py            — _results_store에 보고서 저장

Step 4 (P1): MD 파일 업로드 피드백 처리
  ├── src/document/mapping_report.py     — parse_mapping_report() 추가
  ├── src/document/field_mapper.py       — analyze_md_diff(), apply_mapping_feedback_to_redis()
  └── src/api/routes/query.py            — POST /query/mapping-feedback 엔드포인트

Step 5 (P2): 프론트엔드 UI
  └── src/static/js/app.js              — 보고서 다운로드 버튼, MD 파일 업로드 UI

Step 6 (P1): 테스트
  ├── tests/test_document/test_mapping_report.py        — 보고서 생성/파싱 테스트
  ├── tests/test_document/test_llm_enhanced_mapping.py  — LLM 통합 추론 + Redis 등록 테스트
  └── tests/test_document/test_mapping_feedback.py      — MD diff + Redis 반영 테스트
```

---

## 6. Redis 데이터 구조 변경

### 6.1 유사어 source 태그 확장

기존 Redis synonyms의 source 태그에 새 값을 추가한다:

| source 값 | 의미 |
|-----------|------|
| `llm` | LLM이 스키마 분석(description 생성) 시 자동 생성 (기존) |
| `operator` | 관리자가 CLI/YAML으로 수동 등록 (기존) |
| `llm_inferred` | LLM 필드 매핑 추론 시 자동 등록 **(신규)** |
| `user_corrected` | 사용자가 MD 파일 수정/업로드로 교정 **(신규)** |

### 6.2 매핑 이력 Redis 키 (선택)

반복적인 피드백 학습을 추적하기 위해 매핑 이력을 별도 키에 저장할 수 있다:

```
mapping_history:{template_hash} → List (JSON)
[
  {
    "timestamp": "2026-03-24T14:30:00",
    "field": "서버 호스트명",
    "mapped_to": "CMM_RESOURCE.HOSTNAME",
    "source": "llm_synonym_inferred",
    "user_action": "approved",
    "registered_synonym": "서버 호스트명"
  }
]
```

이 이력은 동일 양식이 반복 사용될 때 매핑 품질을 점진적으로 개선하는 데 활용한다.

---

## 7. 설정

### 7.1 AppConfig 확장

**파일**: `src/config.py`

```python
class FieldMapperConfig(BaseModel):
    """필드 매퍼 설정."""
    enable_llm_synonym_inference: bool = True    # LLM 유사어 추론 활성화
    generate_mapping_report: bool = True          # 매핑 보고서 자동 생성
    synonym_inference_confidence_threshold: str = "medium"  # 최소 신뢰도 (low/medium/high)
    save_mapping_history: bool = False            # 매핑 이력 저장 여부
```

### 7.2 .env 설정

```env
# 필드 매퍼 설정
FIELD_MAPPER_ENABLE_LLM_SYNONYM_INFERENCE=true
FIELD_MAPPER_GENERATE_MAPPING_REPORT=true
FIELD_MAPPER_SYNONYM_INFERENCE_CONFIDENCE_THRESHOLD=medium
```

---

## 8. 리스크 및 완화 방안

| 리스크 | 영향 | 완화 방안 |
|--------|------|----------|
| LLM 추론이 잘못된 매핑을 Redis에 즉시 등록 | 잘못된 유사어가 이후 조회에 영향 | source 태그 `llm_inferred`로 구분하여 일괄 정리 가능, MD 보고서로 사용자 검증 유도 |
| 잘못 등록된 유사어가 정확 매칭에서 오매칭 유발 | 이후 양식에서 잘못된 컬럼 선택 | 사용자 MD 수정/업로드로 교정 가능, confidence threshold로 low 매핑 제외 |
| LLM 통합 추론으로 프롬프트 크기 증가 | 토큰 비용 증가 | 유사어는 상위 N개만 포함, descriptions는 매칭 가능성 높은 컬럼만 필터링 |
| MD 파일 파싱 오류 (사용자가 테이블 구조를 깨뜨린 경우) | 피드백 처리 실패 | 파싱 실패 시 에러 메시지 반환, 원본 MD 형식 안내 |
| 기존 exact match 매핑 성능에 영향 | 회귀 | LLM 통합 추론은 exact match 실패 후에만 실행, 1/2/2.5단계 로직 변경 없음 |
| 동일 양식 반복 조회 시 첫 회만 LLM 호출 | - | 즉시 등록 전략으로 2차 조회부터 2단계 정확 매칭 (자기학습 효과) |

---

## 9. 검증 기준

### 9.1 단위 테스트

- [ ] `_apply_llm_mapping_with_synonyms()`: Redis 유사어 컨텍스트가 프롬프트에 포함되는지 확인
- [ ] `_apply_llm_mapping_with_synonyms()`: LLM 응답에서 confidence, reason, matched_synonym 파싱
- [ ] `_apply_llm_mapping_with_synonyms()`: confidence threshold 필터링 (low 제외)
- [ ] `_register_llm_mappings_to_redis()`: 일반 컬럼 매핑 → Redis synonyms 등록 확인
- [ ] `_register_llm_mappings_to_redis()`: EAV 매핑 → Redis eav_name_synonyms 등록 확인
- [ ] `generate_mapping_report()`: 모든 매핑 소스별 정상 출력 (hint, synonym, llm_inferred)
- [ ] `generate_mapping_report()`: LLM 추론 상세 (reason, confidence, matched_synonym) 포함
- [ ] `parse_mapping_report()`: MD 테이블 → 매핑 리스트 파싱 정확성
- [ ] `parse_mapping_report()`: 사용자가 수정한 MD (컬럼 변경, 행 삭제) 파싱
- [ ] `analyze_md_diff()`: 컬럼 변경 감지 (modified)
- [ ] `analyze_md_diff()`: 행 삭제 감지 (deleted)
- [ ] `apply_mapping_feedback_to_redis()`: modified → 기존 유사어 제거 + 새 유사어 등록

### 9.2 통합 테스트

- [ ] 양식 "서버 호스트명" → Redis에 "서버명" 유사어 존재 → LLM 통합 추론으로 매핑 + 즉시 Redis 등록
- [ ] 매핑 완료 후 `/mapping-report` 엔드포인트에서 MD 파일 다운로드 가능
- [ ] 수정된 MD 업로드 → `/query/mapping-feedback` → Redis 반영 확인
- [ ] 등록 후 동일 양식 재조회 → 2단계에서 정확 매칭 (LLM 호출 없음)
- [ ] SSE 스트리밍에서 field_mapper 완료 시 매핑 보고서 존재 알림

### 9.3 회귀 테스트

- [ ] Redis 미연결 시 graceful fallback (즉시 등록 스킵, LLM 추론만 수행)
- [ ] template_structure 없는 텍스트 모드에서 field_mapper 정상 스킵
- [ ] 기존 1/2/2.5단계 매핑 로직 변경 없음 확인
- [ ] 기존 pending_synonym_registrations 플로우 호환성

### 9.4 자기학습 효과 검증

- [ ] 1차 조회: LLM 통합 추론 → 즉시 Redis 등록 → MD 보고서 생성
- [ ] 2차 조회 (동일 양식): 2단계 정확 매칭 → LLM 호출 없이 매핑 완료
- [ ] 응답 시간: 2차 조회가 1차보다 유의미하게 빠름
- [ ] MD 수정/업로드 후 3차 조회: 수정된 매핑이 반영되어 정확 매칭

---

## 10. 기대 효과

### 10.1 정량적 효과

| 지표 | 현재 | 개선 후 |
|------|------|---------|
| LLM 매핑 결과 활용 시점 | 다음 조회 시 사용자 승인 후 | **즉시** (1차 조회에서 바로 등록) |
| 반복 양식 조회 시 LLM 비용 | 매번 동일 (pending이라 미등록) | 1차만 LLM 호출, 2차부터 0 |
| LLM 추론 컨텍스트 품질 | descriptions만 | synonyms + descriptions + EAV (매칭 정확도 향상) |
| 매핑 정확도 가시성 | 없음 | 100% (MD 보고서 제공) |
| 사용자 피드백 반영 방법 | 자연어 ("3번 맞아") → 파싱 불확실 | MD 파일 수정/업로드 → 구조화된 비교 |

### 10.2 정성적 효과

- **즉시 학습**: LLM 매핑이 바로 Redis에 등록되어 사용자 액션 없이도 자기학습
- **MD 기반 피드백**: 구조화된 테이블 편집으로 피드백 의도가 명확, 자연어 파싱 오류 제거
- **감사 추적**: MD 보고서가 매핑 이력/근거의 단일 진실 소스 (원본 vs 수정본 비교 가능)
- **source 태그**: `llm_inferred` / `user_corrected` 구분으로 자동 등록과 수동 교정 이력 관리
