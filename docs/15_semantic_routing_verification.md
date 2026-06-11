# 시멘틱 라우팅 코드 검증 보고서

- **검증일**: 2026-06-10
- **대상**: `src/routing/semantic_router.py`, `src/routing/domain_config.py`, `src/prompts/semantic_router.py`, `src/graph.py`(`route_after_semantic_router`)
- **결론**: **프로덕션 코드는 정상 동작.** 테스트 7건 실패는 모두 코드 버그가 아닌 테스트 측 문제(v2 리팩토링 미반영, 테스트 격리 미흡).

---

## 1. 검증 방법

1. 기존 테스트 스위트 실행: `python -m pytest tests/test_semantic_routing/`
2. 모킹 LLM을 사용한 11개 시나리오 직접 실행 — `semantic_router` 노드 실행 후 `route_after_semantic_router`로 그래프 분기까지 end-to-end 확인

## 2. 동작 검증 결과 (11개 시나리오)

`semantic_router` → `route_after_semantic_router` 전체 경로를 시나리오별로 실행한 결과, 전부 기대대로 동작함.

| # | 시나리오 | 기대 동작 | 결과 |
|---|---|---|---|
| 1 | 단일 DB data_query | `polestar` 선택, `schema_analyzer`로 분기 | ✅ |
| 2 | 멀티 DB 응답 | 관련도순 정렬(`cloud_portal` 0.9 우선), `is_multi_db=True`, `multi_db_executor`로 분기 | ✅ |
| 3 | 사용자 직접 DB 지정 (`user_specified=true`) | `user_specified_db=polestar_cm_gp` 설정 | ✅ |
| 4 | alarm_query 의도 | `routing_intent="alarm_query"` 전달 후 `schema_analyzer`로 진행 (알람 전용 처리는 schema_analyzer/query_generator에서 수행) | ✅ |
| 5 | cache_management 의도 | `cache_management` 노드로 분기 | ✅ |
| 6 | 비활성 DB(itsm)만 응답 | 필터링 후 첫 번째 활성 DB로 폴백 | ✅ |
| 7 | 관련도 0.3 미만(`MIN_RELEVANCE_SCORE`) | 필터링 후 기본 DB 폴백 | ✅ |
| 8 | LLM이 JSON 아닌 응답 | 기본 DB 폴백, 파이프라인 계속 진행 | ✅ |
| 9 | LLM 호출 예외 | 첫 번째 활성 DB로 폴백 (`relevance_score=0.5`) | ✅ |
| 10 | 활성 DB 없음 (레거시 모드) | `db_id="default"` 단일 모드 | ✅ |
| 11 | `pending_synonym_reuse` 존재 | LLM 호출 없이 `cache_management` 강제 라우팅 | ✅ |

## 3. 테스트 스위트 결과: 47 통과 / 7 실패

실패 7건은 모두 테스트 측 문제이며, 프로덕션 동작에는 영향 없음.

### (a) `TestLLMClassify` 5건 — v2 반환형 미반영 (테스트 구버전)

- `tests/test_semantic_routing/test_semantic_router.py`
  - `test_single_db_classification`
  - `test_multi_db_classification`
  - `test_user_specified_db`
  - `test_filters_invalid_db_ids`
  - `test_empty_response`
- 원인: 테스트가 `_llm_classify`의 v1 반환형 `list[dict]`를 기대하지만, v2(intent 분류 도입)에서 `{"intent": ..., "databases": [...]}` dict로 변경됨.
- 호출부 `semantic_router`는 `isinstance(llm_results, dict)` 분기로 dict를 올바르게 처리하므로 프로덕션 동작은 정상.

### (b) "활성 DB 없음" 테스트 2건 — 테스트 격리 미흡

- `test_db_registry.py::TestMultiDBConfig::test_no_active_dbs`
- `test_semantic_router.py::TestSemanticRouter::test_legacy_mode_no_active_dbs`
- 원인: `MultiDBConfig`가 `env_file=".env"`를 로드하도록 설정되어 있어(`src/config.py:183-188`), 테스트에서 `MultiDBConfig()`를 생성하면 리포의 `.env`(`ACTIVE_DB_IDS=polestar`)가 읽혀 "활성 DB 없음" 전제가 깨짐.
- 수정 방향: 테스트에서 `MultiDBConfig(active_db_ids_csv="")`처럼 명시 주입하거나 `_env_file=None`로 생성하여 환경 격리.

## 4. 부수적으로 발견한 코드 정합성 문제 (동작에는 무해)

| 위치 | 문제 | 영향 |
|---|---|---|
| `src/routing/semantic_router.py:248` | `_llm_classify` 반환 타입 어노테이션이 `-> list[dict]`인데 실제로는 dict(`{"intent", "databases"}`)를 반환. docstring("분류 결과 목록")도 구버전 | 없음 (호출부가 dict 처리) — 가독성/유지보수 저해 |
| `src/routing/semantic_router.py:131-145` | 레거시 모드 반환값에만 `routing_intent` 키 누락 | 없음 (State 기본값 `None` → `schema_analyzer`로 정상 분기) — 다른 경로와 비일관 |
| `src/state.py:118` | `routing_intent` 주석이 `"data_query" \| "cache_management"`만 명시. 실제 사용 중인 `alarm_query`, `synonym_registration` 누락 | 없음 — 문서화 부정확 |

## 5. 라이브 검증 (실제 LLM + Redis 구동 환경)

- **검증일**: 2026-06-10 (DB·Redis 서버 구동 후)
- **방법**: 실제 LLM과 Redis(DB 설명 캐시)를 사용하여 `semantic_router` 노드 + `route_after_semantic_router` 분기를 end-to-end 실행. 라우팅 단계는 DB 연결이 불필요하므로 멀티 DB 케이스는 `MultiDBConfig(active_db_ids_csv="polestar,polestar_cm_gp,polestar_cm_yd,cloud_portal")` 오버라이드로 검증.

### (a) ⚠️ 운영 설정(Gemini) 차단 이슈 발견

`.env`의 `LLM_PROVIDER=gemini`(gemini-2.5-flash)로 실행 시 **모든 LLM 호출이 `400 API key expired`로 실패**.
폴백 로직은 정상 동작하여 모든 질의가 첫 번째 활성 DB(`relevance_score=0.5`)로 라우팅되고 intent는 `data_query`로 고정됨 — 즉 **현재 운영 설정에서는 시멘틱 라우팅(알람/캐시 의도 분류, 멀티 DB 분배, DB 지정 감지)이 사실상 비활성 상태**. `.encenv`의 `LLM_GEMINI_API_KEY` 갱신 필요.

### (b) Ollama(gemma3:12b) 라이브 결과: 12/12 통과

| # | 설정 | 질의 | 결과 |
|---|---|---|---|
| 1 | 단일 DB | 서버 목록을 보여줘 | ✅ polestar(0.95), data_query, schema_analyzer |
| 2 | 단일 DB | 현재 발생 중인 critical 알람을 보여줘 | ✅ alarm_query |
| 3 | 단일 DB | polestar 캐시를 삭제해줘 | ✅ cache_management 분기 |
| 4 | 멀티 DB | CPU 사용률 80% 이상 서버 목록 | ✅ polestar(0.95) |
| 5 | 멀티 DB | 김포 폴스타에서 서버 목록 조회 | ✅ polestar_cm_gp, user_specified=true (intent가 alarm_query로 오분류된 점은 주의 — 아래 참고) |
| 6 | 멀티 DB | 여의도 개발 서버 목록 | ✅ polestar_cm_yd(1.0) |
| 7 | 멀티 DB | 전체 VM 대수 | ✅ cloud_portal(0.95) |
| 8 | 멀티 DB | 현재 발생 중인 알람 목록 | ✅ alarm_query, polestar |
| 9 | 멀티 DB | 김포 운영 서버의 알람 현황 | ✅ alarm_query, polestar_cm_gp(1.0) |
| 10 | 멀티 DB | VM 목록 + 설치 서버 스펙 | ✅ is_multi_db=true, cloud_portal(0.9)+polestar(0.85), multi_db_executor 분기 |
| 11 | 멀티 DB | hostname의 유사 단어를 보여줘 | ✅ cache_management 분기 |
| 12 | 멀티 DB | 스키마 캐시 상태를 보여줘 | ✅ cache_management 분기 |

참고:
- 케이스 5에서 intent가 `alarm_query`로 오분류됨(순수 데이터 조회 질의). DB 선택·분기는 정확했고 alarm_query도 `schema_analyzer`로 진행하므로 라우팅 결과는 동일하나, 후속 노드의 알람 전용 프롬프트 가이드가 불필요하게 적용될 수 있음. 모델 의존적 현상(gemma3:12b)으로 Gemini 키 갱신 후 재확인 권장.
- Redis는 정상 연결되었으나 DB 설명 캐시(`get_db_descriptions`)가 비어 있어 프롬프트 보강 없이 동작함. DB 설명 생성 후 라우팅 정확도가 더 개선될 수 있음.

## 6. 권장 후속 조치

1. `TestLLMClassify` 5건을 v2 dict 반환형 기준으로 갱신
2. "활성 DB 없음" 테스트 2건에 `.env` 격리 적용
3. `_llm_classify` 타입 어노테이션/docstring을 실제 반환형으로 수정
4. 레거시 모드 반환값에 `routing_intent: "data_query"` 추가 (일관성)
5. `state.py`의 `routing_intent` 주석에 `alarm_query`, `synonym_registration` 추가
6. **(긴급)** `.encenv`의 `LLM_GEMINI_API_KEY` 갱신 — 만료로 인해 운영 설정에서 라우팅이 전부 폴백 동작 중
