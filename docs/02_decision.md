# Decision Log

이 문서는 프로젝트의 주요 아키텍처·설계 의사결정 기록의 **압축본**입니다 (2026-07-16 압축).
향후 요건 추가/수정 시 이 문서를 참고하여 의사결정의 방향성과 일관성을 유지합니다.

- 각 결정의 상세 배경·코드 예시·대안 비교·재현 로그 전문은 `docs/02_decision_full.md`(2026-07-16 아카이브, 이후 갱신하지 않음) 참조.
- **신규 결정은 이 파일에만** 아래 압축 형식(결정일/상태/결정/근거/구현/주의/관련)으로 추가한다.
- **D-번호 채번**: `## D-` 헤더와 하단 「변경 이력」 표를 **모두 grep**하여 실제 최댓값+1 부여 (현재 최대 D-085 → 다음 D-086).
- 본문 섹션 없는 번호(재사용 금지): D-039·D-040·D-060·D-077(변경 이력 표 행으로만 등재), D-052(D-051에서 replanner 인프라성 에러 가드용 예약), D-078~D-081(결번).

---

## D-001. LangGraph 상태 머신 아키텍처
- **결정일**: 2026-03 (초기 설계) | **상태**: 확정
- **결정**: 에이전트 프레임워크로 LangGraph 사용, 7개 노드 순차 파이프라인(input_parser → schema_analyzer → query_generator → query_validator → query_executor → result_organizer → output_generator).
- **근거**: 조건부 라우팅/재시도를 선언적으로 정의, 체크포인트로 멀티턴·중단복구 네이티브 지원, langchain-core 추상화로 Claude↔GPT 교체.
- **구현**: 노드/엣지 구성은 `src/graph.py`. 재시도 횟수는 `QueryConfig.max_retries`(기본 3).
- **주의**: 노드 간 데이터는 반드시 `AgentState` TypedDict를 통해 전달할 것.
- **관련**: 대안 HuggingFace Pipeline(분기/재시도 미지원)·Airflow/Prefect(과도) 기각.

## D-002. DBHub (MCP 서버)를 통한 DB 접근
- **결정일**: 2026-03 (초기 설계) | **상태**: 확정
- **결정**: DB 접근은 DBHub(MCP 서버)를 단일 게이트웨이로 사용. `search_objects`(스키마)/`execute_sql`(실행) API 분리.
- **근거**: 다중 DB 타입 단일 인터페이스, 서버 수준 readonly 강제, MCP 표준 프로토콜.
- **구현**: DB 추가 시 `dbhub.toml`에 연결정보 + `domain_config.py`에 도메인 정의. 타임아웃 30s, max_rows 10,000은 DBHub 설정.
- **관련**: 대안 직접 DB 라이브러리/ORM은 보안설정 산재·동적 스키마 탐색 부적합으로 기각. D-014에서 자체 MCP 서버로 확장.

## D-003. 3중 읽기 전용 방어
- **결정일**: 2026-03 (초기 설계) | **상태**: 확정 — 절대 변경 불가
- **결정**: 읽기 전용을 3개 레이어에서 동시 강제 — (1) DBHub `readonly=true`, (2) query_validator에서 DML/DDL 키워드 차단(INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/CREATE/GRANT/REVOKE), (3) query_generator 프롬프트에 "SELECT 문만 생성" 명시.
- **근거**: 다층 방어, LLM은 프롬프트 무시 가능하므로 프로그래밍적 검증 필수.
- **주의**: 이 결정은 변경하지 않는다. 어떤 요건이 추가돼도 쓰기 기능 허용 절대 금지.

## D-004. LLM 전용 시멘틱 라우팅
- **결정일**: 2026-03 (v2 개정) | **개정일**: 2026-06-10 | **상태**: 확정 (D-037로 복합 의도 분해 확장 계획, Plan 48)
- **결정**: DB 라우팅은 LLM 전용. 키워드 기반 사전 분류 미사용. `aliases`로 사용자 직접 DB 지정 인식, 멀티 DB는 DB별 `sub_query_context` 분리, `relevance_score` 임계값 이하 DB 제외.
- **근거**: 키워드는 동의어·줄임말·문맥·멀티 DB 판단 불가. LLM은 문맥 판단·확장 용이.
- **구현**: routing_intent 값 — `data_query`/`alarm_query`(→schema_analyzer 또는 multi_db_executor), `cache_management`, `synonym_registration`(→synonym_registrar), `general_inference`. 라우팅은 `_INTENT_ROUTE_MAP` 레지스트리 방식(`route_after_semantic_router()`). 프롬프트 `src/prompts/semantic_router.py`, 동적 프롬프트 `_build_router_prompt()`.
- **주의**: 키워드 기반 분류 재도입 금지. 새 intent 추가 시 3곳 수정 — (1) `_INTENT_ROUTE_MAP`, (2) `build_graph()` 노드 등록, (3) `conditional_edges` dict.
- **관련**: v1(키워드 1차+LLM 폴백 2단계) 폐기. D-037/Plan 48로 planner 기반 다중 task 분해 확장(`ENABLE_DEEPAGENT_ORCHESTRATION` 플래그).

## D-005. 멀티 DB 순차 실행 + 부분 실패 허용
- **결정일**: 2026-03 | **상태**: 확정 (병렬화는 향후 검토)
- **결정**: 멀티 DB 쿼리 시 각 DB를 순차 독립 실행, 일부 실패해도 성공 결과는 반환. 흐름: `multi_db_executor → result_merger → result_organizer → output_generator`.
- **근거**: DB별 에러 격리, 디버깅 용이, 부분 결과가 전체 실패보다 유용.
- **구현**: `_source_db` 태깅으로 출처 표시, `db_result_summary`로 DB별 성공/실패 보고. `result_merger`는 실행방식 독립 설계.
- **주의**: DB 간 JOIN(데이터 의존성)은 현재 미지원. 병렬 전환 시 `asyncio.gather(return_exceptions=True)`.

## D-006. 설정 계층화 + 자동 활성화
- **결정일**: 2026-03 | **상태**: 확정
- **결정**: 설정은 pydantic-settings 기반 계층 구조(`AppConfig` 하위 llm/dbhub/query/security/server/admin/multi_db 등). 멀티 DB 연결 설정 시 시멘틱 라우팅 자동 활성화, `ENABLE_SEMANTIC_ROUTING=false`로 명시 비활성화.
- **근거**: 타입 안전, 단일 DB 모드 레거시 호환, 자동 활성화.
- **구현**: DB 추가 시 `MultiDBConfig`에 `{db_id}_connection`, `{db_id}_type` 필드 추가. `get_active_db_ids()`가 연결 문자열 있는 DB만 활성 판단.

## D-007. 문서 처리: LLM 의미 매핑
- **결정일**: 2026-03 (Phase 2 설계) | **상태**: 구현 완료 (2026-03-17)
- **결정**: Excel/Word 양식의 헤더·플레이스홀더를 DB 컬럼에 매핑할 때 LLM 의미 매핑 사용(예: "서버명"→servers.hostname). 멀티시트는 시트별 독립 LLM 매핑(`map_fields_per_sheet()`), `target_sheets`로 특정 시트 지정 가능.
- **근거**: 양식 필드명은 비정형(한국어·약어·조직 고유어)이라 규칙 기반 매핑 불가.
- **구현**: Excel=openpyxl(헤더 자동탐지, 병합셀/서식/수식 보존), Word=python-docx(`{{placeholder}}`). `OrganizedData.sheet_mappings`에 시트별 결과. `sheet_mappings=None`이면 기존 `column_mapping`+`rows` 방식 폴백.

## D-008. 4-Phase 점진적 구축
- **결정일**: 2026-03 (초기 설계) | **상태**: 확정
- **결정**: 4 Phase 점진 구축 — (1) 자연어→SQL 파이프라인, (2) Excel/Word 양식 문서 생성, (3) 멀티턴·감사로그·쿼리승인, (4) Web UI. Phase 1~3 완료, Phase 4 미착수.
- **주의**: Phase 간 의존성 존중(Phase 2는 Phase 1 SQL 파이프라인에 의존). Phase 순서 변경은 의존성 검토 필요.

## 의사결정 간 연관 관계
- D-001(LangGraph)이 기반: 조건부 라우팅/재시도로 D-003(query_validator SQL 검증), D-004(semantic_router 노드), D-005(multi_db_executor/result_merger), D-006(설정 자동 활성화)이 파생.
- D-002(DBHub)가 D-003(readonly)·D-005(멀티 소스)의 인프라 제공.
- D-007(문서 처리)은 D-008 Phase 2에서 구현.

## D-009. 사용자 UI: 채팅 인터페이스 + SSE 스트리밍
- **결정일**: 2026-03-17 | **상태**: 확정
- **결정**: 사용자 Web UI를 채팅형으로 재설계, SSE 기반 토큰 스트리밍(`POST /api/v1/query/stream`, `text/event-stream`). SSE 미지원 시 `POST /api/v1/query` 자동 폴백. 파일 질의는 SSE 불필요.
- **근거**: 대화형 UX, TTFT 단축, 기존 API 호환.
- **구현**: SSE 이벤트 type — token/meta/done/error. 토큰 스트리밍은 최종 응답 노드(output_generator, general_inference)가 `src/llm.py::astream_text(llm, messages, tags=[USER_RESPONSE_TAG])`로 `.astream()` 호출. SSE 핸들러는 노드명이 아닌 `USER_RESPONSE_TAG`로 토큰 필터. 복합 질의 토큰 인터리빙은 `done` 이벤트의 권위 `response`로 프론트 보정.
- **주의**: `ainvoke()`는 토큰 이벤트 미발생 → 반드시 `astream_text` 사용. 중간 LLM 호출에는 태그 붙이지 말 것. 신규 최종응답 LLM 호출 추가 시 반드시 `astream_text`+`USER_RESPONSE_TAG` 사용.

## D-010. 3단계 스키마 캐싱 (메모리 → 파일 → DB)
- **결정일**: 2026-03-17 | **상태**: 확정
- **결정**: 스키마 조회 3단계 캐시 — 1차 메모리(TTL 5분), 2차 파일(`{cache_dir}/{db_id}_schema.json`, fingerprint 비교), 3차 DB 전체조회. fingerprint는 테이블별 컬럼수 → 정렬 JSON → SHA-256 해시.
- **근거**: 재시작 시 전체조회 방지, fingerprint 쿼리 경량, DB별 독립 캐시, 파일 손상 시 graceful fallback.
- **구현**: `SCHEMA_CACHE_DIR`, `SCHEMA_CACHE_ENABLED`. 포맷 변경 시 `CACHE_FORMAT_VERSION` 증가.
- **관련**: D-011에서 2차를 Redis로 업그레이드, D-019에서 fingerprint TTL 최적화.

## D-011. Redis 기반 스키마 캐시 + LLM 컬럼 설명/유사 단어
- **결정일**: 2026-03-17 | **상태**: 구현 완료 | **이전 결정**: D-010 확장
- **결정**: 2차 캐시를 파일→Redis 업그레이드, LLM 기반 컬럼 설명(description)+유사 단어(synonym) 생성 추가. TTL 없는 영구저장, fingerprint 변경 시만 갱신.
- **근거**: query_generator 프롬프트에 유사어 포함하여 컬럼 선택 정확도 향상.
- **구현**: Redis 키 `schema:{db_id}:meta|tables|relationships|descriptions|synonyms`. 모듈 `src/schema_cache/redis_cache.py`, `cache_manager.py`, `description_generator.py`, `src/nodes/cache_management.py`. Graceful fallback: Redis 장애→파일→DB. `SCHEMA_CACHE_BACKEND=file`이면 기존 동작 유지.
- **주의**: Redis 키 구조 변경 시 `CACHE_FORMAT_VERSION` 증가. 운영자 수동 유사어는 글로벌 사전(`synonyms:global`)에 보존 — DB별 synonyms는 `invalidate()` 시 삭제되나 `load_synonyms_with_global_fallback()`으로 재구축(Plan 30).

## D-012. 매핑-우선(Mapping-First) 필드 매핑 + 유사어 등록
- **결정일**: 2026-03-17 | **상태**: 구현 완료 | **이전 결정**: D-007 확장
- **결정**: 필드 매핑을 input_parser 직후 독립 노드(`field_mapper`)로 수행, 그 결과가 대상 DB 선택·SQL 생성·파일 생성 전체 주도. 그래프: `input_parser → field_mapper → semantic_router → ...`.
- **구현**: 3단계 매핑(프롬프트 힌트→Redis synonyms→LLM 추론, 앞 단계 성공 시 다음 스킵). field_mapper 단일 매핑을 query_generator·output_generator가 공유. semantic_router는 mapped_db_ids 우선 참조. LLM 추론 매핑은 사용자 승인 시 Redis synonyms에 자동 등록.
- **주의**: template_structure 없으면 field_mapper 스킵(텍스트 출력 흐름 무영향). 유사어 등록은 멀티턴 `pending_synonym_registrations` State 참조.

## D-013. 멀티턴 대화 + Human-in-the-loop (Phase 3)
- **결정일**: 2026-03-18 | **상태**: 구현 완료
- **결정**: LangGraph 체크포인트 기반 멀티턴 대화 + SQL 승인(HITL) + 유사어 등록 승인 구현. 단일턴/멀티턴을 별도 분기하지 않고 통합 단일 경로(단일턴=첫 턴 특수 케이스).
- **구현**: `context_resolver` 노드(그래프 첫 노드, 이전 맥락 추출), `approval_gate` 노드(`interrupt_before`로 SQL 실행 전 승인), `synonym_registrar` 노드. State: `messages: Annotated[list[BaseMessage], add_messages]`, `thread_id`, `conversation_context`, `awaiting_approval`/`approval_context`/`approval_action`/`approval_modified_sql`. API: `POST /query`(첫/후속 턴 자동분기), `GET /conversation/{thread_id}`. pending 상태는 체크포인트에서 자동 복원(별도 Redis 불필요), semantic_router가 pending 우선 라우팅.
- **주의**: `query_results` 대량 데이터 시 체크포인트 요약본 교체 검토. 동일 `thread_id` 동시 요청은 LangGraph 직렬화 의존.

## D-014. 자체 MCP 서버 구축 + SSE Transport 전환
- **결정일**: 2026-03-19 | **상태**: 구현 완료 | **이전 결정**: D-002 확장
- **결정**: 외부 npm `dbhub`를 자체 Python MCP 서버(`mcp_server/`, FastMCP 기반)로 교체, transport를 stdio→SSE 전환. DB 연결정보는 MCP 서버 VM에만 존재, 클라이언트는 서버 URL만 보유.
- **근거**: 커스터마이징 자유, 배포 분리(보안), Node.js 의존 제거, DB2 지원(기존 dbhub 미지원).
- **구현**: PostgreSQL(asyncpg)+DB2(ibm_db, asyncio.to_thread). 5개 도구(search_objects, execute_sql, get_table_schema, health_check, list_sources). 이중 보안(`mcp_server/security.py`+`src/security/sql_guard.py`). 설정: `DBHubConfig.server_url`(`http://localhost:9099/sse`), `mcp_call_timeout`(60s), `MultiDBConfig.active_db_ids_csv`. query_timeout·max_rows·연결문자열은 클라이언트에서 제거(서버 관리).
- **주의**: `dbhub.toml`은 deprecated 상태로 유지(롤백 대비). 새 도구는 `mcp_server/tools.py` 등록, DB 타입은 `mcp_server/db.py`+`config.toml`.

## D-015. Excel→CSV 변환으로 LLM 컨텍스트 보강 (Plan 19)
- **결정일**: 2026-03-23 | **상태**: 구현 완료 | **이전 결정**: D-007 확장
- **결정**: Excel 업로드 시 CSV 변환으로 헤더+예시 데이터를 추출해 LLM 컨텍스트 보강. 기존 파이프라인(field_mapper→SQL→DB→Excel 채우기)은 유지, CSV는 보강 수단으로만.
- **근거**: 헤더명만 보는 것보다 예시 데이터 패턴이 매핑 정확도 향상(예: "서버명"→hostname vs server_id 판별).
- **구현**: `CsvSheetData` 데이터클래스(시트별 헤더·예시 최대 50행·CSV 텍스트), `excel_to_csv()` 함수. CSV 변환 실패 시 `template_structure` 기반 헤더 추출 폴백. output_generator/excel_writer는 변경 없음.

## D-016. EAV 비정규화 테이블 쿼리 지원 (Plan 20)
- **결정일**: 2026-03-24 | **상태**: 구현 완료 | **이전 결정**: D-001 확장, D-014 연계
- **결정**: Polestar DB의 EAV(Entity-Attribute-Value) 구조 + 계층형 리소스 테이블(CMM_RESOURCE + CORE_CONFIG_PROP) 쿼리 지원 추가. DB 엔진(DB2/PostgreSQL)별 SQL 문법 분기 도입.
- **구현**: `src/prompts/polestar_patterns.py` 신규(POLESTAR_QUERY_PATTERNS/META/QUERY_GUIDE). schema_analyzer가 CMM_RESOURCE+CORE_CONFIG_PROP 존재 시 `_polestar_meta` 자동 삽입. query_generator가 EAV 피벗/계층탐색/조인 가이드 프롬프트 삽입. `DBDomainConfig.db_engine`·`AgentState.active_db_engine` 필드 추가. query_validator가 DB2(`FETCH FIRST N ROWS ONLY`)/PostgreSQL(`LIMIT N`) 문법 자동 대응, 테이블명 대소문자 무시 비교.
- **주의**: `_polestar_meta` 없으면 기존 로직 유지(비-Polestar 무영향). db_engine 기본값 "postgresql". 새 DB 엔진(Oracle 등) 추가 시 `_add_limit_clause` 분기 추가.
- **관련**: D-017에서 field_mapper까지 EAV 확장. D-020에서 polestar_patterns.py 삭제·범용화.

## D-017. EAV Field Mapper 전체 파이프라인 지원 (Plan 21)
- **결정일**: 2026-03-24 | **상태**: 구현 완료 | **이전 결정**: D-016 확장, D-012 확장
- **결정**: Field Mapper 3단계 매핑에 2.5단계 EAV synonym 매칭 삽입, `EAV:속성명` 접두사 규약으로 EAV 속성 매핑 표현. query_generator가 감지해 CASE WHEN 피벗 힌트 자동 생성.
- **구현**: `_apply_eav_synonym_mapping()` 신규(`src/document/field_mapper.py`), Redis `eav_name_synonyms`에서 매칭→`EAV:속성명`. `perform_3step_mapping()`에 `eav_name_synonyms` 파라미터 추가. mapping_sources에 `"eav_synonym"` 추가. `_format_schema_columns()`이 known_attributes를 `EAV:속성명` 가상 컬럼으로 포함. `_validate_mapping()`이 known_attributes 기준 검증.
- **주의**: `eav_name_synonyms`가 None/빈 dict이면 2.5단계 스킵(기존 동작 불변). `EAV:` 접두사 없는 매핑은 기존 처리. 정규 컬럼(`table.column`)과 공존 가능.

## D-018. LLM 지능형 필드 매핑 + 매핑 보고서 + 사용자 피드백 학습 (Plan 22)
- **결정일**: 2026-03-24 | **상태**: 구현 완료 | **이전 결정**: D-012 확장, D-017 확장
- **결정**: Field Mapper LLM 추론 단계를 Redis 유사어+DB descriptions+EAV names 통합 컨텍스트로 강화, LLM 매핑 결과를 즉시 Redis 등록, 구조화 MD 보고서 생성해 사용자가 MD 수정/업로드로 교정. 전략: "기본 등록→사후 교정"(pending 승인 대기 폐지).
- **근거**: 즉시 등록으로 반복 양식 조회 비용 절감, MD 파일 피드백이 자연어 파싱 불확실성 제거.
- **구현**: `_apply_llm_mapping_with_synonyms()`·`_register_llm_mappings_to_redis()`(source `llm_inferred`) 신규. `src/document/mapping_report.py` 신규(`generate_mapping_report()`, `parse_mapping_report()`, `analyze_md_diff()`, `apply_mapping_feedback_to_redis()`). API: `GET /query/{id}/mapping-report`, `POST /query/mapping-feedback`. `perform_3step_mapping()` 반환 `tuple[MappingResult, list[dict]]`로 변경.

## D-019. Fingerprint TTL 기반 Redis 캐시 최적화 (Plan 26)
- **결정일**: 2026-03-25 | **상태**: 구현 완료 | **이전 결정**: D-010·D-011 확장
- **결정**: 메모리 캐시(5분) 만료 후 Redis 조회 시, fingerprint 검증 타임스탬프(기본 30분 TTL)가 유효하면 DB fingerprint SQL 미실행하고 Redis 캐시 신뢰.
- **구현**: `SchemaCacheConfig.fingerprint_ttl_seconds=1800`. Redis 키 `schema:{db_id}:fingerprint_checked_at`. `is_fingerprint_fresh()`/`refresh_fingerprint_checked_at()`(redis_cache.py), cache_manager 위임(파일 백엔드는 항상 False). schema_analyzer·multi_db_executor 조회 2단계 분리(2차-A TTL 유효 시 DB 미조회, 2차-B 만료 시 fingerprint SQL 1회). `multi_db_executor._analyze_schema()`가 `SchemaCacheManager` 통합 사용.
- **주의**: 스키마 변경 반영 최대 30분 지연(트레이드오프). `SCHEMA_CACHE_FINGERPRINT_TTL_SECONDS`로 조절. Redis 장애 시 항상 False 반환하여 안전 폴백.

## D-020. LLM 기반 범용 스키마 구조 분석 (Plan 27)
- **결정일**: 2026-03-25 | **상태**: 확정
- **결정**: schema_analyzer.py의 Polestar 하드코딩 전면 제거, LLM 전면 분석 + HITL 검증 + 결과 자동 캐싱으로 전환. 새 DB 추가 시 schema_analyzer.py 코드 변경 없이 동작.
- **구현**: `DOMAIN_TABLE_HINTS` 삭제→`_llm_select_relevant_tables`. Polestar 전용 함수 3개 삭제→범용(`_analyze_db_structure`, `_collect_structure_samples`). `_polestar_meta`→`_structure_meta` 키 변경. `polestar_patterns.py` 파일 삭제(POLESTAR_META/QUERY_PATTERNS/QUERY_GUIDE 제거). DB 프로필 자동 생성(`config/db_profiles/{db_id}.yaml`, Redis+YAML 이중 캐싱). `structure_approval_gate` 노드+`interrupt_before`+`enable_structure_approval`(기본 활성).
- **주의**: YAML/JSON 프로필은 LLM+HITL 산출물이며 수동 편집하지 않는다. 환각 위험은 HITL로 처리.

## D-021. Gemini API 프로바이더 추가 + 민감 키 분리 (Plan 28)
- **결정일**: 2026-03-25 | **상태**: 구현 완료 | **이전 결정**: D-006 확장
- **결정**: Ollama 환각 검증 목적으로 Google Gemini를 3번째 LLM 프로바이더 추가. API 키 등 민감정보를 `.encenv` 파일로 `.env`와 분리 관리.
- **구현**: `LLMConfig.provider`에 `"gemini"` 추가(`Literal["ollama","fabrix","gemini"]`), `_create_gemini()` 팩토리(`src/llm.py`, `ChatGoogleGenerativeAI`). `.encenv`를 `.gitignore` 등록, `LLMConfig`/`AdminConfig`/`RedisConfig`의 `env_file`을 `[".env",".encenv"]`로 확장. `langchain-google-genai>=2.0.0` optional(`pip install -e ".[gemini]"`). Lazy import.
- **주의**: 권장 모델 `gemini-2.0-flash`(기본)·`gemini-3.1-pro`. `gemini-2.5-*`는 2026-06-17 deprecated 예정이므로 사용 금지. 팩토리 패턴 유지(노드는 `create_llm()` 단일 진입점). Gemini는 외부 네트워크 필요(폐쇄망 불가).

## D-022. RESOURCE_CONF_ID JOIN 금지 + hostname 브릿지 조인 필수화
- **결정일**: 2026-03-26 | **상태**: 확정 | **이전 결정**: D-016 수정(EAV 조인 교정), D-020 보강
- **결정**: `CMM_RESOURCE.RESOURCE_CONF_ID`를 `CORE_CONFIG_PROP.CONFIGURATION_ID` JOIN 조건으로 사용 금지. CMM_RESOURCE↔CORE_CONFIG_PROP 조인은 반드시 hostname 기반 값 브릿지 조인(value_joins)으로만 수행.
- **근거**: 운영 DB 분석 결과 두 컬럼이 직접 매핑되지 않고 FK도 없음. resource_conf_id 조인은 잘못된 결과 반환.
- **구현**: 올바른 패턴 — `core_config_prop p_host ON p_host.name='Hostname' AND p_host.stringvalue_short=r.hostname` 후 `p_x.configuration_id=p_host.configuration_id AND p_x.name='<속성>'`. query_generator/multi_db_executor가 `value_joins`를 `join_condition`보다 우선. Plan 33 보강(3중 방어): (1) YAML query_guide 금지 문구+`excluded_join_columns`, (2) 프롬프트 규칙 10+"-- JOIN 금지" 주석, (3) query_validator `_check_excluded_join_columns()` warning. `src/utils/schema_utils.py::build_excluded_join_map()` 공용 유틸.
- **주의**: resource_conf_id 기반 JOIN 절대 생성 금지. `join_condition`은 FK 존재 DB용 폴백으로 코드에서 제거하지 않음. validator warning 3회 이상 반복 시 error 승격 검토.
- **관련**: D-028에서 방어 체계를 vendor_id/os_id/os_param_id로 확장.

## D-023. 데이터 충분성 검사 로직 개선 (Plan 36)
- **결정일**: 2026-03-30 | **상태**: 확정
- **결정**: `result_organizer._check_data_sufficiency()`의 하드코딩 50% 임계값 제거, 매핑 출처별 차등 임계값(mapping_sources 기반)+`.env` 설정 가능 임계값 도입.
- **근거**: hint/synonym(정확)과 llm_inferred(추론)는 확신도가 달라 동일 임계값 부적절. 50% 하드코딩이 불완전 결과물 전달 원인.
- **구현**: `QueryConfig.sufficiency_required_threshold`(0.7)·`sufficiency_optional_threshold`(0.5). `_match_column_in_results()`(4단계 매칭), `_classify_mapped_columns()`(hint/synonym=필수, llm_inferred=선택), `_check_data_sufficiency()`에 `mapping_sources`·`app_config` 추가.
- **주의**: `mapping_sources=None`(레거시)은 모든 매핑을 required(70%)로 취급(기존 50%보다 의도적 강화). 빈 결과+집계 쿼리는 False로 변경(재시도 유도).

## D-024. Synonym 통합 관리 + EAV 접두사 비교 정규화 (Plan 37)
- **결정일**: 2026-03-30 | **상태**: 확정
- **결정**: EAV synonym을 `synonyms:global`에도 등록해 global 비교 인프라 공유, 필드명 비교에 `normalize_field_name()` 정규화 도입, EAV 접두사(`EAV:`)를 파이프라인 전체 일관 처리, EAV 쿼리 시 정규 컬럼 과도 필터링 제거.
- **근거**: EAV synonym이 `synonyms:eav_names`에만 격리되면 global 폴백/비교 인프라 활용 불가. 엑셀 헤더 줄바꿈/다중공백은 strip만으론 처리 불가. EAV entity/config 테이블이 달라 정규 컬럼 잘못 제외됨.
- **구현**: `SynonymLoader._process_synonym_data()`가 EAV를 `synonyms:eav_names`+`synonyms:global` 양쪽 등록. `normalize_field_name()`(`src/utils/schema_utils.py`, Unicode NFC·줄바꿈/탭→공백·다중공백 축소·strip). excel_parser·field_mapper·result_organizer·word_writer/excel_writer가 정규화+EAV 접두사 제거 적용. query_generator/multi_db_executor는 EAV 쿼리 시 정규 컬럼 필터링 제거(LLM이 JOIN 판단). `_classify_mapped_columns()`가 `eav_synonym`을 required 분류.
- **주의**: 정규 컬럼 필터링 제거로 LLM이 비-EAV 테이블도 프롬프트에서 보므로 부적절 JOIN 시 프롬프트 튜닝 필요.

## D-025. 3계층 하이브리드 필드 매핑 전파 정합성 (Plan 38)
- **결정일**: 2026-03-30 | **상태**: 구현 완료
- **결정**: field_mapper의 column_mapping 형식(`"cmm_resource.hostname"`, `"EAV:OSType"`)과 query_generator SQL alias 형식(`"cmm_resource_hostname"`, `"os_type"`) 불일치를 3계층 하이브리드 매칭으로 해결. Layer 1(규칙)→Layer 2(LLM)→Layer 3(폴백).
- **근거**: Layer 1이 80%+ 즉시 해결해 LLM 비용/지연 최소화, Layer 2는 미해결 항목에만 소규모 호출, Layer 3은 레거시 경로 커버.
- **구현**: `src/utils/column_matcher.py` 신규(`resolve_column_key()` 7단계 매칭, `build_resolved_mapping()`, `camel_to_snake()`). `src/prompts/column_resolver.py`(LLM 유사성). `state.py`에 `resolved_mapping` 추가. `result_organizer.py`(`_resolve_unmatched_via_llm()`, Step 4.5), `output_generator.py`(resolved 우선/column_mapping 폴백), `excel_writer.py`/`word_writer.py`(Layer 3 폴백).
- **주의**: 새 매칭 단계 추가 시 정확 매칭 최우선 유지. `_is_close_match` 편집거리 2 이상 확장은 오탐 위험 신중히. Layer 2 실패 시 Layer 3 위임(graceful) 유지 확인.
- **관련**: D-007/D-012/D-024 확장

## D-026. 사용자 로그인 및 인증 시스템 (Plan 39)
- **결정일**: 2026-04-01 | **상태**: 구현 완료
- **결정**: `AUTH_ENABLED=false` 기본(개발 무인증), DB 기반 사용자 저장, 자유 가입 + 관리자 권한 부여, SAML SSO 확장 기반(AuthProvider 추상화). bcrypt 해싱, 로그인 5회 제한, 계정 30분 잠금.
- **구현**: `src/domain/auth.py`(AuthProvider ABC), `src/domain/user.py`(User/UserRole/UserStatus), `src/utils/password.py`(bcrypt), `src/infrastructure/{auth_provider,user_repository,audit_repository}.py`(Local/Postgres, raw SQL+asyncpg, ORM 미사용), `src/api/dependencies.py`(require_user), `src/api/routes/user_auth.py`, `AuthConfig`(AUTH_ENABLED/auth_db_url/jwt_expire_hours), `state.py`(user_id/user_department/allowed_db_ids), `ddl/auth_tables.sql`(auth_users/audit_logs). JWT 시크릿은 AdminConfig.jwt_secret 공유 + `type` 클레임으로 구분.
- **주의**: 매 요청 DB에서 최신 사용자 정보 조회(토큰 최소정보). `allowed_db_ids`는 Plan 41 접근제어 전제.
- **관련**: D-006 확장 / D-070에서 type·role 검증 누락 보완, 시크릿 분리(AUTH_JWT_SECRET)

## D-027. 사용자 행위 감사 로깅 강화 (Plan 40)
- **결정일**: 2026-04-02 | **상태**: 확정
- **결정**: JSONL 파일 + PostgreSQL DB 이중 기록 유지. SQLite 대신 PostgreSQL(Plan 39 `audit_logs`) 확장. 통합 `AuditService`, 요청별 request_id/client_ip 자동 수집 `AuditMiddleware`. 이벤트 유형 2→10개(user_login/logout/login_fail/register/password_change/data_access/file_download/security_alert/admin_action/cache_operation). 금지 SQL·인젝션·대량 조회·로그인 실패 반복 시 security_alert 자동 생성.
- **근거**: JSONL=빠른 쓰기·운영 디버깅, PostgreSQL=복잡 조회/통계 → 이중 기록 최적.
- **구현**: `src/security/audit_service.py`, `src/api/middleware/audit_middleware.py`. `AuditConfig.retention_days`(현재 90일).
- **주의**: 대량화 시 월별 파티셔닝 검토. 실시간 보안 경고 알림(이메일/슬랙)은 별도 연동 필요.

## D-028. Polestar 불필요 lookup 테이블 JOIN 차단
- **결정일**: 2026-04-02 | **상태**: 확정
- **결정**: Polestar DB `cmm_vendor`/`cmm_os`/`cmm_os_param`은 쿼리 대상 제외. 데이터는 `core_config_prop` EAV 속성(Vendor/OSType/OSParameter)에 존재하므로 직접 JOIN 불필요.
- **구현**: YAML(`polestar.yaml`/`polestar_pg.yaml`)에 EAV `excluded_join_columns`(vendor_id/os_id/os_param_id) + `allowed_tables`(cmm_resource, core_config_prop만). `schema_analyzer._load_manual_profile()`가 allowed_tables로 relevant_tables 필터. `query_validator._validate_forbidden_joins()` 패턴 3 추가.
- **주의**: `allowed_tables`는 선택적(미설정 DB 기존 동작 유지). 새 테이블 추가 시 명시적 등록 필요.
- **관련**: D-022 보강(3중 방어 확장)

## D-029. 알람 조회 의도 분리 + 알람 전용 쿼리 템플릿 주입 (Plan 44)
- **결정일**: 2026-05-29 | **상태**: 확정
- **결정**: 알람/모니터링 질의를 `routing_intent="alarm_query"` 독립 의도로 분류, query_generator까지 결정론적 전파해 알람 전용 프롬프트(`POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE`, Template C-1~C-5) 주입. LLM 자율 선택이 아닌 의도 기반 강제 주입.
- **구현**: `routing/domain_config.py`(polestar 도메인에 CMM_ALARM/CMM_ALARM_DEF 등 추가), `prompts/semantic_router.py`(intent에 alarm_query), `nodes/query_generator.py`(`_build_system_prompt`에 routing_intent 파라미터), `prompts/query_generator.py`(알람 템플릿 상수).
- **주의**: polestar/polestar_b0는 DB2 엔진이나 Template C는 PostgreSQL 문법 — PostgreSQL 대상(gp/yd) 우선 검증. 알람 테이블 Redis 스키마 캐시 없으면 갱신 필요. routing_intent None/data_query면 기존 템플릿(하위호환).
- **관련**: D-004/D-016 확장

## D-030. ALARMSEVERITY=0 해소 상태 이력 쿼리 포함 (Plan 45)
- **결정일**: 2026-06-01 | **상태**: 확정
- **결정**: ALARMSEVERITY=0=알람 해소 상태. 이력 조회(C-2~C-5)는 `IN (0,1,2,3)`로 해소 포함, 활성 알람(C-1)은 CMM_ALARM_ACTIVE INNER JOIN이 0을 구조적 배제하므로 `IN (1,2,3)`. 해소만 조회는 `=0` 단독+ACTIVE JOIN 제외.
- **근거**: 0 배제 시 "지난달 알람 이력"에서 해소 알람 누락, CASE WHEN에 0 없으면 등급 컬럼 공백.
- **구현**: `domain_config.py` 심각도 설명 `0=해소` 추가. `prompts/query_generator.py` 알람 템플릿 [필수 WHERE]/[심각도 매핑]/[심각도0 분기] 섹션, CASE WHEN `WHEN CA.ALARMSEVERITY=0 THEN '해소'`, C-4 `해소_수` 컬럼.
- **주의**: polestar_b0(DB2) 동일 템플릿 적용 시 DB2 문법 호환성 확인.
- **관련**: D-029 확장

## D-031. 알람 소켓 수신 → LLM 분석 → worKB 발송 (Plan 46)
- **결정일**: 2026-06-04 | **상태**: 구현 완료
- **결정**: 알람 소켓 수신을 `alarm_server/`(독립 프로세스)로 분리, 에이전트 서버(`src/alarm/`)와 Redis Stream(`alarm:raw`)으로 연결. 분석·발송은 2-노드 LangGraph 서브그래프(`AlarmAnalysisGraph`).
- **근거**: mcp_server 분리 원칙 동일(라이프사이클/설정/배포 독립). Redis Stream으로 재시작 시 유실 방지.
- **구현**: `alarm_server/`(config `ALARM_SERVER_` 접두사, TcpReceiver 포트 9100, base_receiver). `src/alarm/domain/alarm.py`(AlarmEvent), `prompts/alarm_analyzer.py`, `infrastructure/redis_queue.py`, `application/nodes/{alarm_analyzer,alarm_notifier}.py`, `orchestration/alarm_graph.py`, `application/alarm_worker.py`(Redis 소비+dedup). `AlarmConfig`/`WorkbConfig`.
- **주의**: 발송 채널=worKB 단일(Generic Webhook 분기 포함, Slack 외부망 불가 제외). 중복 제거=in-memory dedup dict(alarm_id TTL, dedup_ttl_seconds=300). min_severity=2 기본. worKB 토큰 .encenv. AlarmWorker=orchestration, nodes=application 계층.
- **관련**: D-014 확장, D-029 연계

## D-032. 폴스타 알람 메시지 포맷 확정 — 단일행 JSON + AlarmEvent 필드 재설계 (Plan 46 개정)
- **결정일**: 2026-06-09 | **상태**: 확정
- **결정**: 폴스타 알람을 단일행 JSON으로 확정, `AlarmEvent` 필드를 폴스타 템플릿 변수와 1:1 대응 재설계. `dbId`는 인스턴스별 상수 기입, `serverName`=`${platformName}`=DB `server_name` 매핑.
- **필드 변경**: source_db_id→`db_id`, 신규 `server_name`/`ip_address`/`resource_ancestry`/`alarm_time`, alarm_state→`alarm_status`, alarm_conditions→`conditions`, raw_text→`raw_payload`(dict). alarm_description/definition/resource_name/resource_description 제거.
- **구현**: `domain/alarm.py`, `alarm_worker._process()`(datetime 파싱+is_clear 파생), `prompts/alarm_analyzer.py`, `nodes/{alarm_analyzer,alarm_notifier}.py`, `routes/alarm.py`, `base_receiver.py`.
- **주의**: **`${alarmStatus}`는 발생/해소 구분이 아니라 폴스타 UI 인지(ACK) 버튼 여부(`NOT_ACK` 등)이며 해소와 무관 — is_clear 판정은 `severity==0` 단독 기준(D-035 정정).** 표의 alarmStatus='발생'/'해소' 기술은 실측과 다름.
- **관련**: D-031 개정, D-035에서 정정

## D-033. 처리 현황에 유사어 매핑 표시 — 생성된 SQL 기반 역조회
- **결정일**: 2026-06-11 | **상태**: 확정
- **결정**: 처리현황 "SQL 생성" 단계에 사용자 용어→유사어→선택 컬럼/속성 매핑 표시. LLM 자기보고가 아닌 **생성된 SQL의 리터럴/컬럼을 유사어 사전 key와 대조하는 결정적 역조회**로 추출.
- **근거**: SQL은 LLM 결정의 산출물이라 실제 매핑을 가장 정직하게 반영. LLM 자기보고는 환각 위험으로 배제.
- **구현**: `src/utils/synonym_usage.py` 신규(`extract_synonym_usage(sql,...)`). EAV/RESOURCE_TYPE는 따옴표 리터럴 정확 일치, 일반 컬럼은 단어경계. `matched_user_terms` 정규화 대조. 사전 미등록 리터럴은 `unregistered`로 UI 경고 배지. `query_generator`가 SQL 직후 호출(try/except), `synonym_usage` State 필드. `app.js` 렌더링.
- **주의**: column_synonyms가 전 테이블×컬럼 수백 키 규모라 `name`/`id` 공통 컬럼명이 중복 매칭 → **bare 컬럼명 기준 중복제거 + matched_user_terms 있는 항목만 + 최대 15건 제한**(대량 출력 방지).
- **관련**: D-009, D-011/D-024

## D-034. 주기적 헬스체크 로그 노이즈 감소 — 성공 경로 로그 전역 강등
- **결정일**: 2026-06-11 | **상태**: 확정
- **결정**: /health 폴링(30초)의 성공 경로 로그를 전역 강등. `setup_logging()`에서 httpx 로거를 WARNING 상향(성공 HTTP INFO 억제), DB 클라이언트 연결 성공/종료 로그 INFO→DEBUG.
- **근거**: 강등 대상은 성공 경로뿐 — 연결 실패는 예외·WARNING으로 여전히 드러남. 질의 이력은 sql_file_logger·감사로깅이 별도 기록.
- **구현**: `security/audit_logger.py`, `dbhub/client.py`, `db/client.py`.
- **관련**: D-027(영향 없음)

## D-035. 알람 이력 기반 패턴 분석 — 폴스타 DB 직접 조회 (Plan 47)
- **결정일**: 2026-06-11 | **상태**: 구현 완료
- **결정**: 알람 패턴 분석 이력 소스를 폴스타 DB 직접 조회(고정 SQL, DBHub, 기본 lookback 90일)로 구현. 통계·1차 분류는 Python 결정적, LLM은 해석만. 그래프 3-노드(`alarm_context_enricher` 추가). 결과 단기 Redis 캐시(TTL 5분).
- **근거**: 폴스타 DB에 전체 이력 이미 존재(단일 진실원천). 별도 저장소는 중복·이력공백·정합성 부담. lookback 90일=월주기 3회 관측 최소.
- **구현**: `domain/alarm_pattern.py`(`compute_history_stats()` 순수함수), `infrastructure/polestar_history.py`(`PolestarAlarmHistoryRepository`, 고정 SQL, Template C-6 서버매칭 `SVR.ID=COALESCE(CR.PLATFORM_RESOURCE_ID,CR.ID)`+`SVR.NAME=server_name`, `_SERVER_MATCH_BY_DB_ID`), `nodes/alarm_context_enricher.py`(`enrich_history()`), `AlarmConfig` 6개 필드(history_enabled/lookback_days/max_rows/cache_ttl/enrich_timeout/burst_threshold).
- **주의**: **is_clear는 `severity==0` 단독 기준으로 통일 — D-032의 alarmStatus='발생'/'해소' 정정**(alarmStatus는 ACK 상태). 시간 윈도우는 `event.alarm_time` 기준(now 아님). 공동존(gp/yd)은 r.name 매칭, hostname 금지. graceful degradation(실패/타임아웃5초/미등록 db_id → history_stats=None, 분석·발송 절대 차단 금지). 패턴은 부가정보(발송 억제 미사용), 심각도3은 is_routine 무관 권고 유지. 미등록 db_id는 조회 전 차단("첫 발생" 오판 방지). ALARM_HISTORY_ENABLED=false면 노드 제외.
- **관련**: D-022/D-030/D-031/D-032(정정 포함)

## D-036. 알람 영향 프로세스 보강 — 폴스타 실시간 프로세스 API (Plan 47-1)
- **결정일**: 2026-06-16 | **상태**: 구현 완료
- **결정**: CPU/메모리 **발생** 알람에 한해 폴스타 실시간 프로세스 API(`GET {base_url}/rest/server/process/listByhostname?hostname=`)를 **hostname으로 조회**해 상위 점유 프로세스를 결정적 선별·마스킹. 별도 노드 없이 `alarm_context_enricher`에 추가, 이력 조회와 `asyncio.gather` 동시 실행·각자 독립 degradation.
- **근거**: "왜 자원이 높은가"의 직접 근거가 프로세스 점유율. HTTP 의존은 짧은 타임아웃(3초)+degradation 격리.
- **구현**: `domain/process_rank.py`(`classify_alarm_kind`/`select_top_processes`/`mask_args` 순수함수), `infrastructure/polestar_process_api.py`(`PolestarProcessApiClient`, `ProcessApiResult`), `enrich_processes()`, `AlarmConfig`(process_enrich_enabled/process_api_base_urls_csv/process_api_timeout_seconds/process_top_n, `get_process_api_base_url()`).
- **주의**: **조회 키는 `event.hostname`(DB 이력은 `r.name`=serverName — 정반대 키, 실측 serverName≠hostname).** 인증 불필요·`http://`만. **민감정보 마스킹 필수**(password/passwd/pwd/secret/token/api_key/access_key/credential·접속문자열 비밀번호 → `mask_args()`, LLM/UI/workb/webhook 노출 전 마스킹, 회귀 테스트 고정). SSRF 방지: base_url 고정값, hostname만 `urllib.parse.quote(safe='')`. 게이팅=CPU/메모리+발생(is_clear=False)+base_url 매핑+enabled+client. graceful(API 실패→process_snapshot=None, 노드 항상 두 키 반환). LLM은 상위 프로세스 인용만·미조회 시 추측 금지·마스킹 복원 금지.
- **관련**: D-035/D-032/D-022

## D-037. deepagents 기반 의도 분해 오케스트레이션 (Plan 48)
- **결정일**: 2026-06-16 | **상태**: Phase 1·2 구현 완료 / 실제 deepagents 패키지 도입(2026-06-17, Plan 49 트랙 B 재진입) / deepagents 0.6.10 설치+step6 실측 완료 / Phase 3~6 예정
- **결정**: `semantic_router`의 단일 의도 라우팅을 deepagents 패턴(planner+subagent 위임)으로 확장 — 질의를 여러 sub-task로 분해·순차/병렬 실행 후 통합. 자체 LangGraph 노드(`intent_planner`→`agent_orchestrator`→`result_aggregator`)로 구현.
- **근거**: 현 semantic_router는 한 질의=한 작업이라 "A하고 B조회" 복합 질의 처리 못 함.
- **세부**: TaskSpec `{task_id, agent, sub_query, depends_on, order}`. agent∈{data_query, cache_management, synonym_registration, general_inference, alarm_query}. intent_planner 실패 시 단일 data_query 폴백. agent_orchestrator=depends_on 위상정렬→같은 레벨 asyncio.gather 병렬. `ENABLE_DEEPAGENT_ORCHESTRATION` 미입력 시 멀티 DB 환경이면 기본 활성(tri-state, model_post_init이 get_active_db_ids 기준 해석). 신규 `src/orchestration/` 패키지. 결과 기반 후속 3패턴: ①독립병렬 ②데이터의존 순차(input_from) ③조건부 동적 재계획(replanner, MAX_REPLAN). 모호성 명료화 인터럽트(clarification_needed 슬롯, Phase 4 clarification_gate, MAX_CLARIFY=2).
- **트랙 B 재진입(2026-06-17)**: tool-calling 블로커를 **폐쇄망 vLLM 오케스트레이터**(Qwen3.5-9B, OpenAI 호환 /v1)+`langchain-openai ChatOpenAI(base_url=vLLM) bind_tools`로 해소. 역할 분리: **vLLM=제어평면(write_todos/task 위임/tool_calls), FabriX(KBGenAIChat)=데이터평면(NL→SQL→조회→응답)**. SUBAGENT_REGISTRY 5작업을 `@tool` 노출하되 FabriX는 도구 내부에서만 호출. vLLM 미서빙/off 시 기존 semantic_router 폴백.
- **주의**: **semantic_router 로직 삭제 금지(재사용). route_after_semantic_router/_INTENT_ROUTE_MAP 삭제 금지(하위호환).** 명시값은 pydantic-settings 필드로 읽어 os.getenv 미사용. Phase 1에서 clarification_needed 슬롯 반드시 예약. **create_deep_agent는 `system_prompt` 인자(instructions 아님, Known Mistakes 2026-06-17).**
- **관련**: D-004 확장, D-005 일반화

## D-038. 사용법/지원 소스 안내 — general_inference 그라운딩 (도움말 디스커버리)
- **결정일**: 2026-06-23 | **상태**: 구현 완료
- **결정**: "이 에이전트로 뭘 할 수 있나/지원 소스" 문의에 실제 활성·허용 소스 근거 안내를 채팅 제공. 생성 위치를 `general_inference` 노드 한 곳으로 일원화, 도움말 버튼 추가.
- **근거**: 신규 사용자 디스커버리(빈 질의→실패→이탈 완화). 환각 차단 핵심 — 사실(온라인 소스·메트릭)은 코드 조립, 문장만 LLM.
- **구현**: `general_inference.py`(`_build_source_catalog`=active∩allowed+DB_DOMAINS, `_build_system_prompt`), `prompts/orchestrator.py`(사용법 문의→general_answer 위임 규칙), index.html/css/app.js(`❓ 사용법` 점선 버튼). 세 백엔드(deep_agent/semantic_router/intent_planner)가 모두 general_inference로 수렴.
- **주의**: general_inference는 DB 미접근 노드 — 라이브 health 체크 금지(설정 get_active_db_ids·DB_DOMAINS만 참조). allowed_db_ids(D-026) 지정 사용자는 교집합 소스만(못 쓰는 소스 광고 금지). `_SUPPORTED_CAPABILITIES`는 수기 목록이라 신규 도메인 추가 시 동기화.
- **관련**: D-026, D-037

## D-041. 멀티턴 컨텍스트 전파 및 엔티티 보존 (Plan 50)
- **결정일**: 2026-06-25 | **상태**: 구현 완료
- **결정**: 후속 턴 분해(intent_planner)에 직전 턴 해소 DB/위치/대상 엔티티를 압축 주입, data_query가 이전 턴 DB 승계, "현재/실시간 프로세스"류는 `process_query` 1급 의도로 실시간 프로세스 API 라우팅.
- **세부**: M3(context_resolver)=`previous_db_ids`/`previous_entities`(행수 상한 `_MAX_ENTITY_ROWS=20`)/`previous_location`. M1(intent_planner)=`_build_context_block` 압축 1블록 주입(원시 히스토리 금지). M2(subagents.run_data_query_pipeline)=DB 승계 우선순위 ①이번 턴 명시 위치/DB > ②mapped_db_ids > ③previous_db_ids > ④전체 fan-out, `_has_new_location_db_signal`. M4=`src/orchestration/process_query.py` 신규(alarm의 polestar_process_api.py·process_rank.py 재사용).
- **주의**: 엔티티/맥락 블록은 압축 1블록·상한 유지(토큰 재증가 방지). process_query는 base_url 매핑(AlarmConfig.process_api_base_urls_csv) 있는 폴스타(김포/여의도)만 실동작 — 신규 폴스타 추가 시 매핑 필요. 마스킹·상위 N 선별은 결정적(LLM 원시 주입 금지).
- **관련**: D-013 확장, D-037, D-009. **번호 주의: Plan 50 §6은 "D-039" 표기했으나 선점되어 D-041 부여.**

## D-042. 제어 평면 컨텍스트 예산 · 평면 분리 강제 · Qwen no-think (Plan 50)
- **결정일**: 2026-06-25 | **상태**: 구현 완료(클라이언트) / 서버 기동 파라미터(B8) 인프라 진행중
- **결정**: tool-calling 제어평면(vLLM Qwen3.5-9B, 소용량)에는 계획 신호만, 대용량 작업(SQL/데이터/응답)은 FabriX 평면 강제. 원시 도구 결과는 collector에만, 오케스트레이터 컨텍스트엔 요약본만. 상한값은 하드코딩 금지 — `OrchestratorConfig`(env `ORCHESTRATOR_*`) 노브. Qwen 계열은 no-think 기본.
- **근거**: 관찰 오류 `Input tokens must be <=95232. Given: 197986`(제어평면 컨텍스트 폭증) → 멀티턴 압축(D-041)·도구결과 축소·예산노브·no-think 다중 방어.
- **구현**: B6 `OrchestratorConfig`(`max_input_tokens` 기본 **12000**, context_budget_ratio 0.8, max_tool_result_tokens 2000, max_history_turns 6). B7 `_create_orchestrator_vllm`(`enable_thinking:bool=False`, Qwen 계열일 때만 `extra_body.chat_template_kwargs.enable_thinking` 부착·계열 가드). B1/B2 `deepagents_tools`(반환 텍스트 상한 축소, 원본은 collector). B8 서버 `max_model_len=16384`/`gpu_memory_utilization=0.85`.
- **주의**: 클라이언트 입력 예산=16384−출력여유(~4000)=12000(계획서 32768/24000 가정 무효). os.getenv 미사용(pydantic 필드). 대형 모델 교체 시 서버 max_model_len↑+클라 ORCHESTRATOR_MAX_INPUT_TOKENS↑+ENABLE_THINKING을 한 세트로. **extra_body는 model_kwargs 경유 금지 — ChatOpenAI 전용 인자로 직접 전달(Known Mistakes 2026-06-25).**
- **관련**: D-037 운영 보강. **번호 주의: Plan 50 §6 "D-040" 표기했으나 선점되어 D-042.**

## D-043. 재조회(대체) 후속 task의 1차 시도 결과 본문 숨김 (supersedes)
- **결정일**: 2026-06-26 | **상태**: 확정
- **결정**: 후속 task에 `supersedes`(대체하는 선행 task_id 목록) 필드 도입. 대체(재조회)형=`supersedes:["t1"]`→result_aggregator가 본문에서 대체된 선행 서술 숨기고 후속만 노출. 추가(보강)형=`supersedes:[]`→둘 다 노출(D-005 유지).
- **근거**: 빈/누락 결과에 replanner가 재조회 task 추가 후 t1(실패)+t2(성공)를 둘 다 본문에 이어붙여 "없다→있다" 모순 답변 발생.
- **구현**: `prompts/replanner.py`(supersedes 규칙·예시), `orchestration/replanner.py::_assign_ids`(supersedes 보존+임시 id 재매핑, 누락 시 빈 배열), `orchestration/result_aggregator.py`(`_collect_superseded`).
- **주의**: 대체 후속이 **성공(에러 없음)했을 때만** 선행 숨김(재조회 실패 시 1차 결과 유지). 숨김은 최종 본문에만 — 처리현황(SSE)엔 두 task 투명 유지(관찰성). 전부 제외되는 비정상 시 전체 사용(방어). **대안 기각: "단일 엔티티 0건이어도 재조회 차단"은 교정 기회 상실로 기각.**
- **관련**: D-005, D-037/D-039, D-040

## D-044. 스트리밍 응답 조건부 자동 스크롤 (stick-to-bottom) + 맨 아래 이동 버튼
- **결정일**: 2026-06-26 | **상태**: 확정
- **결정**: ChatGPT/Claude류 stick-to-bottom 모델 도입(프론트엔드 전용, 백엔드/SSE 무변경). 맨 아래(`BOTTOM_THRESHOLD_PX=24` 이내) 고정 상태 추적, 고정 시만 토큰 추종(비smooth), 위로 스크롤 시 면역. 사용자 본인 질의만 무조건 맨 아래. 플로팅 "맨 아래로" 버튼(고정 해제 시 표시). 신규 도착 시 버튼에 점(`has-new`).
- **근거**: 토큰마다 무조건 scrollToBottom + `scroll-behavior:smooth`로 화면 튀고 과거 읽기 방해.
- **구현**: `static/js/app.js`(stickToBottom/hasNewContent/isNearBottom()/scrollToBottomIfSticky()/updateScrollToBottomBtn()), `index.html`(#scrollToBottomBtn), `style.css`(.chat-main position:relative, .chat-messages scroll-behavior:smooth 제거, .scroll-to-bottom-btn).
- **주의**: 사용자 질의(renderUserMessage)만 scrollToBottom() 유지, 토큰/에이전트 출력은 scrollToBottomIfSticky().
- **관련**: D-009. **번호 주의: 계획서 초안 D-043 선점되어 D-044.**

## D-045. 스트리밍 마크다운 비파괴 렌더 (DOM 모핑) — 표 가로 스크롤·텍스트 선택 보존
- **결정일**: 2026-06-26 | **상태**: 확정
- **결정**: 토큰마다 `innerHTML` 전체 교체 대신 DOM 모핑(B-1)으로 전환. full `marked.parse` 결과를 기존 DOM에 diff 적용해 동일 위치·태그 요소 재사용→표 scrollLeft·텍스트 선택 보존. 토큰 버스트는 rAF 코얼레싱(프레임당 1회).
- **근거**: 매 토큰 서브트리 파괴·재생성 시 가로 스크롤한 표가 좌측 초기화(scrollLeft은 라이브 프로퍼티).
- **구현**: 자체 morph(외부 의존성 0) — `morphChildren`(인덱스+nodeName 재사용, isEqualNode로 무변경 스킵)+`syncAttributes`, `renderStreamingMarkdown(el,md)`, `scheduleStreamingRender()`(rAF, _streamAccumulated/_streamRafQueued). `createStreamingMessage()`에서 _streamAccumulated="" 초기화.
- **주의**: **morphdom 미사용(폐쇄망 CDN 불가+재현 리스크) — 자체 구현.** 실패 시 폴백(방안 A: 표 scrollLeft만 스냅샷·복원하며 전체 교체). B-2 블록 증분 파싱은 맥락 오판 위험으로 보류.
- **관련**: D-009, D-044

## D-046. 프로세스 조회 시 서버명 → 호스트명 해소 (process_query)
- **결정일**: 2026-06-26 | **상태**: 확정
- **결정**: process_query가 프로세스 API 호출 전에 입력 식별자(서버명/호스트명)를 정규 hostname으로 해소. 신규 `PolestarHostnameResolver`(infrastructure)가 폴스타 DB(cmm_resource)에 고정 SELECT 단일문 실행(LLM 미사용, DBHub 경로 재사용).
- **근거**: API 조회 키=hostname인데 사용자는 서버명 질의. 공동존(gp/yd)은 name≠hostname → 서버명 그대로 전달 시 0건 → "리소스명=프로세스" 행 환각 폴백.
- **구현**: `src/alarm/infrastructure/polestar_hostname_resolver.py` 신규(`build_hostname_sql`=`server.Server`·`DTIME IS NULL`·name/hostname OR·name 우선 ORDER BY, `resolve`). `orchestration/process_query.py`(`_resolve_canonical_hostname`).
- **주의**: 매칭은 name(서버명) 우선. **graceful 폴백: 미등록 db_id/실패/0건/빈 hostname → None → 원시 입력 사용.** D-022(RESOURCE_CONF_ID JOIN 금지)·`is_lob` 조건 금지 정합. 관찰성: server_name(원본)·hostname(해소값) 동시 보존, 다르면 `서버명(호스트명 ...)` 병기. 인스턴스별 스키마 다르면 `_SCHEMA_BY_DB_ID` 등록.
- **관련**: D-041(M4), D-036

## D-047. 프로세스 조회 대상 서버 식별자 추출 (input_parser 규칙 보강)
- **결정일**: 2026-06-29 | **상태**: 확정
- **결정**: 특정 서버 지목 질의(특히 프로세스)에서 서버 식별자를 항상 filter_conditions로 추출, `_resolve_hostname`은 한글 field 변형까지 방어적 인정. 시간성 신호 없는 프로세스 조회는 `process_query`(실시간 API)로 결정적 교정.
- **근거**: "김포 ###서버 프로세스 리스트"가 식별 실패 — input_parser에 서버명→hostname filter 추출 규칙 부재, LLM이 한글 field명 생성 시 `_HOST_FIELDS` 매칭 실패로 identifier=None.
- **구현**: input_parser 규칙 14 신설(단일 서버 지목 시 `{"field":"hostname","op":"=","value":"<서버식별자>"}` 추출, 위치/DB 수식어는 target_db_hints로 분리). `process_query._HOST_FIELDS` 확장(영문 host/device_name + 한글 서버명/서버이름/장비명/호스트명/서버/장비). `prompts/intent_planner.py` 규칙 3 강화(프로세스=기본 process_query, 명시적 과거/이력 신호만 data_query), `intent_planner._coerce_process_intent`(data_query+프로세스+이력신호 없음→process_query 결정적 교정).
- **프로세스 결과 표시/다운로드(사용자 결정)**: 채팅 말풍선=상위 N만(process_top_n=5 표시전용), **CSV 다운로드=전체 프로세스**. `process_query.py`(전체 1회 정렬·마스킹, organized_data.rows=상위 N, query_results=전체). `result_aggregator._finalize_task`가 query_results를 **top-level 승격**(orchestration 경로 CSV 버튼·row_count 동작). `download_csv`는 컬럼명 등장순서 합집합+restval=""/extrasaction="ignore".
- **주의**: 복수 서버 지목은 현재 첫 host filter만(다중 요구 시 리스트 반환 확장). 채팅 표시 N 조정은 `ALARM_PROCESS_TOP_N`.
- **관련**: D-046, D-041(M4)

## D-048. 알람 노이즈 캔슬링 — 4-티어 발송 게이트 (Plan 52 E1)
- **결정일**: 2026-06-29 | **상태**: 구현 완료(Phase E1 MVP), 하위결정 E3~E5 순차 완료
- **결정**: 폴스타 알람을 결정적 규칙 파이프라인으로 **PAGE/TICKET/DASHBOARD/SUPPRESS** 4-티어 라우팅(전 기능 옵트인 `enable_noise_gate=False` 기본). LLM(is_routine)은 보조 입력 1개, 판단은 결정적. **심각도3은 어떤 억제도 없이 항상 PAGE**(D-035 계승). 억제·강등도 감사 기록(억제≠삭제). 신호는 폴스타 읽기전용 DB에서 고정 SQL, 실패 시 보수적 PAGE(재현율 우선).
- **하위결정**: D-048.1(4-티어·결정적판단·reason 기록). D-048.2(중요도×심각도 매트릭스+유지보수/자가복구 억제 E1, 의존/인히비션/플래핑 E2, **심각도3 절대 PAGE**). D-048.3(IMPORTANCE_ID/IS_MAINTENANCE/`cmm_alarm_def_noti*` 앵커, 미매핑=보통 보수적, 불확실→None 절대 단정 suppress 미반환). D-048.4(재현율 우선·억제≠삭제 decision_store JSONL·옵트인). D-048.5(E3 완료, AI 심각도는 LLM 인컨텍스트·ML 미사용, **상향전용 monotonic max()**, 메시지형 한정, ai_severity_escalate_only=True). D-048.6(향후 미구현, Plan 55 멀티소스 확장 자리예약). D-048.7(E5 완료 2026-07-02, deepagents Advisory Enricher, 3경로 자동선택 `_select_backend`, **승격 전용 비대칭** R-10/R-12, 심각도3 미개입, 메시지형 한정, **도구·health·ReAct는 infrastructure `noise_signal_tools.py` 배치**로 arch error 회피, collector 패턴, graceful, 옵트인 `enable_agentic_enricher=False`, gemini 테스트 경로). D-048.8(E3 완료, severity1×중요도낮음 매트릭스 셀 SUPPRESS→DASHBOARD). D-048.9(E3 완료, TICKET=감사+DASHBOARD(SSE)+일배치 요약큐, MTTA/MTTR/사건전환율은 계측부재로 null+unavailable_metrics 환각금지). D-048.10(E3후속 완료, 워커→UI 실시간 SSE Redis pub/sub 브리지 `infrastructure/sse_bridge.py`, at-most-once, 이중발행 방지). D-048.11(E4 완료 2026-07-01, LLM 액션가능성 판단 few-shot·ML 미사용, actionable→promote/noise→demote(가드), 심각도3 절대 PAGE 불변, 옵트인 `enable_llm_actionability=False`).
- **핵심 설계(E1)**: `notification_policy.decide_notification`(순수함수 domain 8단계: 실효심각도→수집실패 보수화→심각도3 단락 PAGE→해소/자가복구→유지보수 SUPPRESS→매트릭스→보조조정→확정). min_severity 역할분리(게이트 활성 시 `1≤severity<min_severity`만 드롭, **severity0 전달·severity3 절대 드롭 금지**, 권장 ALARM_MIN_SEVERITY=1). 핑거프린트 dedup(`compute_fingerprint(db_id,server|hostname,alarm_name,resource)`, TTL=repeat_interval_seconds 기본4h, 해소 dedup 제외). 자가복구 상관(`_firing_registry`, self_heal_window_seconds 기본300, 심각도3 제외). NoiseGateConfig는 AppConfig 형제필드 `cfg.noise_gate.*`(env_prefix `NOISE_`).
- **구현**: `domain/notification_policy.py`, `infrastructure/polestar_noise_context.py`(고정 SQL 읽기전용), `infrastructure/decision_store.py`(JSONL 감사+억제율), `nodes/notification_gate.py`, `orchestration/alarm_graph.py`, `alarm_worker.py`.
- **주의**: **graceful degradation(신호 수집 실패/미등록 db_id/타임아웃→source="unavailable"→보수적 PAGE, 발송 절대 차단 금지). 회귀0(옵트인, enable_noise_gate=False면 게이트 노드 미포함).** 독립 신호는 개별 try/except로 부분반환(Known Mistakes 2026-06-29). in-memory 상태 dict는 키 만료 sweep 필수. 결정적 매트릭스 셀 변경 시 단언 테스트 repo 전체 grep.
- **번호 정정**: 최초 D-040→선점→D-041 1차→multiintent(D-041~D-047) 재충돌→사용자 승인으로 **D-048** 최종. Plan 50의 D-041~D-047 보존.
- **관련**: D-035 확장, D-003, D-029~D-032, D-037(트랙 B vLLM)

## D-049. ack/incident 라이프사이클 계측 — PostgreSQL 단일 저장소 (Plan 52 §9.1·§13.1#9)
- **결정일**: 2026-06-30 | **상태**: 확정(사용자 결정) — 구현 완료(백엔드+UI 2 surface: 채팅 알람카드 확인버튼+admin "열린 사건" 패널)
- **결정**: incident 라이프사이클(firing→ack→resolved) 저장소를 **PostgreSQL 단일 저장소**로(Redis 단독·혼합 기각). MTTA/MTTR/사건전환율을 window SQL로 산출(D-048.9의 null 제거). 앱은 이미 쓰기 PG(audit_logs/auth_users) 운영이라 신규 의존 아님.
- **근거**: ack은 API 프로세스·incident 생성/해소는 워커 프로세스에서 발생(프로세스 경계). JSONL은 append-only라 상태전이 UPDATE 불가·cross-process 비안전. 상태전이 UPDATE+시간창 집계는 PostgreSQL 강점.
- **구현**: port `IncidentStore`(domain)+`infrastructure/incident_repository.py`(audit_repository 미러). 테이블 `alarm_incidents`(fingerprint/alarm_id/db_id/server/severity/priority/tier/**status(open|ack|resolved)**/created_at/acked_at/acked_by/resolved_at/resolution). **쓰기 단일화(D-048.10 브리지 재사용)**: 워커는 PG 쓰기 안 함 — Redis 채널 `alarm:incident` 발행→API 단일 PG 라이터(subscriber)가 영속. open=notifier PAGE 시 발행, resolved=워커 clear 이벤트, ack=`POST /alarm/incidents/{id}/ack`(API 직접 UPDATE, 식별키=incident id). 전용 PG 풀(`incident_tracking_enabled`, auth_pool 종속 회피). 지표: mtta_seconds/incident_mttr_seconds/incident_conversion_rate.
- **주의**: **옵트인 `incident_tracking_enabled`(기본 False)+`incident_event_channel`(기본 alarm:incident), graceful(Redis/PG 미가용 시 폴백, 발송·기동 무차단, 회귀0).** self-heal MTTR(`auto_recovery_mttr_seconds`)은 sev1..suppress_max만(sev3 제외) 편향 지표 — paged incident MTTR(PG `incident_mttr_seconds`)과 **라벨로 명확 구분**(환각 금지). 같은 fingerprint 복수 open 경합 시 resolved UPDATE는 `ORDER BY created_at DESC LIMIT 1` 기본.
- **관련**: D-048.9(후속 계측), D-048.10(브리지 패턴), D-048.4, D-026/D-027, D-003

## D-050. 단일 서버 필터 + EAV 피벗(CPU·메모리) 조회 SQL 교정 (HAVING 패턴)
- **결정일**: 2026-06-29 | **상태**: 확정
- **결정**: 서버 식별 필터(서버명/호스트명)를 EAV 피벗과 함께 쓸 때는 WHERE가 아니라 HAVING(집계 후 server.Server 행 기준)으로 적용한다. avail_status와 동일 기법. WHERE는 resource_type/dtime 등 행집합 한정용으로만.
- **근거**: 피벗에 `WHERE c.name='###'`를 붙이면 그 술어가 server.Server 행에만 참이라 GROUP BY 전에 server.Cpus/server.Memory 행이 제거돼 CPU·메모리 CASE 집계가 NULL이 됨(OS/IP/호스트명만 정상 = 관측 증상 일치).
- **구현**: `prompts/query_generator.py` filter_conditions 섹션에 HAVING 규칙·예시, `config/db_profiles/polestar_cm_gp.yaml`·`polestar_cm_yd.yaml`에 단일 서버+OS/IP/호스트명+CPU/메모리 통합 query_example(HAVING 패턴). 패턴: `GROUP BY COALESCE(c.platform_resource_id, c.id) HAVING MAX(CASE WHEN c.resource_type='server.Server' THEN c.name END)='장비명'`. "호스트명이 XXX인" 명시 시에만 c.name→c.hostname.
- **주의**: EAV 피벗+서버 식별 필터를 WHERE에 두면 CPU/메모리 NULL 됨 — 반드시 HAVING. LLM 의존이라 100% 보장 아님(validator/result 후속 안전망).
- **관련**: D-063(이 사례를 "속성 부재"로 오판한 가드 — 본 결정이 진짜 원인), D-046(EAV/hostname 해소)

## D-051. allowed_tables 유사어 동적 보완을 질의 매칭분으로 게이트 (b0 토큰 폭증 차단)
- **결정일**: 2026-06-30 | **상태**: 확정(1차 구현 — schema_analyzer 게이트)
- **결정**: allowed_tables 유사어 동적 보완을 이번 질의(원질의+query_targets)에 실제 등장한 유사어의 테이블만으로 게이트하고 보완 수 상한(`_MAX_SYNONYM_SUPPLEMENT_TABLES=15`) 적용. b0 relevant을 5개 수준으로 수렴시켜 프롬프트 ~5-10K로.
- **근거**: b0 단일 조회가 `Input tokens must be <= 95232. Given: 197951`(FabriX 데이터 평면 GptOss ~95K 한도)로 실패. `schema_analyzer`가 `cache_mgr.get_synonyms(db_id)`의 모든 col_key 테이블을 무조건 `_allowed`에 추가 → b0(유사어 최다 누적 DB)가 5개 화이트리스트→407개로 부풀어 스키마 통째 덤프. DB2라서가 아니라 유사어 누적이 원인(시한폭탄형).
- **구현**: `schema_analyzer._synonym_tables_matching_query()` 헬퍼 신설(빈 문자열·1글자 유사어 제외). 보조: FabriX 데이터 평면 토큰 가드(`WORKER_MAX_INPUT_TOKENS`), 전체-테이블 폴백 제거(P1).
- **주의**: 사전류 전 테이블 순회 시 매칭+상한 필수(2026-06-11 재발). 대안(전면 제거)은 비-화이트리스트 유사어 매핑 DB가 있으면 깨질 수 있어 보류.
- **관련**: D-042(데이터 평면 토큰 전제 정정). 후속 예약: replanner 인프라성 에러 가드(D-052 예정)

## D-053. hostname 해소 SQL 엔진 인지(은행 레거시 b0 DB2 프로세스 조회)
- **결정일**: 2026-06-30 | **상태**: 확정
- **결정**: `build_hostname_sql`을 엔진 인지로. `resolve()`가 `get_domain_by_id(db_id).db_engine` 조회 후 전달 — db2(b0)는 `FETCH FIRST 1 ROWS ONLY`+무스키마 `cmm_resource`(CURRENT SCHEMA), postgresql(gp/yd)는 기존 `LIMIT 1`+`polestar.` 스키마.
- **근거**: b0 실시간 프로세스 조회 0건. D-046 `build_hostname_sql`이 PostgreSQL 방언 고정(LIMIT 1·polestar 스키마)이라 DB2 b0에서 SQL 실패 → `resolve()`가 예외 삼키고 None → 원시 서버명이 hostname으로 API에 전달 → 0건.
- **구현**: `src/alarm/infrastructure/polestar_hostname_resolver.py`(`_table`/`build_hostname_sql` 엔진 분기, 실패 로그에 engine·sql). `process_query.py::_resolve_db_id._LOCATION_DB_HINTS`에 b0 힌트(은행/레거시/은행존) 추가(라이브 진단으로 드러난 1차 게이트 — db_id=None). `result_aggregator._collect_db_promotion`이 실행 task target_db_ids를 `active_db_id`/`target_databases`로 top-level 승격(멀티턴 후속 db_id 소실 복원).
- **주의**: 고정 SQL은 대상 DB 엔진별 방언(LIMIT vs FETCH FIRST, 스키마 한정)을 반드시 분기. 예외 삼키는 폴백은 실패 SQL·engine을 로그에 남길 것. 새 DB 편입 체크리스트: 위치 힌트+base_url+엔진 방언. `polestar_history.py`도 동일 패턴이라 b0 알람 이력에서 방언 불일치 잠재(후속).
- **관련**: D-046 보강

## D-054. 레거시 단일 `polestar` 도메인 폐기 + db_profiles 정리 + 면책 문구 환각 차단
- **결정일**: 2026-06-30 | **상태**: 확정
- **결정**: (1) 레거시 `polestar` 도메인 폐기 — `DB_DOMAINS`에서 `db_id="polestar"` 제거(7→6), b0가 승계. 바 별칭("폴스타")은 LLM 시멘틱 라우팅이 b0/gp/yd 중 선택. (2) db_profiles의 test_db/unknown/polestar_pg/polestar.yaml 삭제. (3) `OUTPUT_GENERATOR_SYSTEM_PROMPT` 규칙 6 추가 — 안 물었으면 컬럼(avail_status 등) 부재 면책·안내 금지.
- **근거**: output_generator LLM이 "avail_status 컬럼이 없으므로 판단 불가" 같은 묻지 않은 면책을 자발 환각(코드에 없는 문자열).
- **구현**: `src/routing/domain_config.py`(polestar 엔트리 제거), `src/prompts/output_generator.py`(규칙 6), `src/api/routes/alarm.py`(AlarmTestRequest.db_id 기본 `polestar_b0`), `semantic_router.py`/`cache_management.py` few-shot db_id 갱신.
- **주의**: `field_mapper.py`의 `if db_id_lower=="polestar"` 분기는 무해 dead branch로 그대로 둠(라우팅 우선순위 변경 리스크 회피).
- **관련**: D-004, D-053 후속

## D-055. 후속 턴 "해당 서버" 지시어 hostname 오추출 차단 + 결과 요약 유지 정정
- **결정일**: 2026-07-01 | **상태**: 확정
- **결정**: (1) 결정적 지시어 가드 `process_query._is_demonstrative_value()` — 이번 턴 filter 값이 지시어(해당/그/이/저/위/직전+서버/장비)·영문 플레이스홀더(previous/prev+server/host)이면 hostname 미인정, `previous_entities`로 폴백(①·② 양쪽). (2) input_parser 규칙 14 보강: 지시어만 지목 시 filter 비움. (3) output_generator 규칙 6 정정: "없는 컬럼 면책"만 금지, 조회 데이터 요약·이상 분석(규칙 2·5)은 유지.
- **근거**: 후속 "해당 서버 프로세스"가 `previous_server`(플레이스홀더)로 0건 — 규칙 14가 지시어도 서버 지목으로 봐 LLM이 플레이스홀더 생성, 이번-턴 filter가 previous_entities보다 우선순위 높아 오염. D-054 규칙 6 과잉 적용으로 정상 요약까지 소실.
- **구현**: `src/orchestration/process_query.py`(`_is_demonstrative_value`, `_DEMONSTRATIVE_PREFIXES/_NOUNS`), `src/prompts/input_parser.py`, `src/prompts/output_generator.py`.
- **주의**: 지시어("해당/그/이/위 서버")는 실제 식별자 아님 — hostname 인정 금지. 부정 지시는 유지할 정상 동작 명시.
- **관련**: D-046, D-047, D-053, D-054 후속

## D-056. 멀티턴 후속 판단형 질의에 직전 턴 답변 전파 (Approach A)
- **결정일**: 2026-07-01 | **상태**: 확정(1단계 A — ③ rows 보존/intent_planner 분석 라우팅은 유보)
- **결정**: Approach A(①②④, 최소 침습·결정적). ② 경로별 단일 종료 노드에서만 최종 답변을 AIMessage로 messages에 append(orchestration=`result_aggregator._with_answer_history`, 직접경로=output_generator/general_inference/error_response). ① `SubAgentSpec.needs_history` 플래그(general_inference만 True), `_make_isolated_input`이 needs_history agent에만 트리밍 이력(`_MAX_HISTORY_MESSAGES=10`) 전달, 데이터 조회 agent는 `[]` 유지. ④ general_inference가 후속 턴에 `conversation_context` 그라운딩 + `_JUDGMENT_GUIDANCE`(데이터 있으면 거부 말고 판단 후 추가 필요분만 짚기).
- **근거**: 판단형 후속 턴이 직전 데이터 활용 못하고 거부. 원인: 답변이 messages에 안 쌓임, 격리 입력이 이력 삭제(`"messages":[]`), general_inference가 conversation_context 미독.
- **구현**: `result_aggregator.py`(`_with_answer_history`), `subagents.py`(`needs_history`/`_history_for_agent`), `general_inference.py`(`_JUDGMENT_GUIDANCE`/`_build_context_grounding`), `output_generator.py`(얇은 래퍼), `graph.py`(error_response 누적).
- **주의**: subagent 반환 messages는 task_results에만 담기고 top-level 미승격(D-053 비대칭) → 이중 누적 없음.
- **후속(같은 날)**: (a) 엔티티 sticky — 판단 턴이 query_results=[]로 덮어써 previous_entities 소실 → `context_resolver`가 이번 턴 추출 비면 직전 conversation_context의 previous_entities/db_ids/location 승계(fresh 있으면 우선). (b) 미지원 역량 광고 차단 — `_JUDGMENT_GUIDANCE`/카탈로그 분기에 "제안은 `_SUPPORTED_CAPABILITIES` 목록 안에서만, MySQL/InnoDB/버퍼풀·앱파라미터 예시 금지". (c) 프로세스 결과 행 오수집 차단 — `_looks_like_process_rows()`(첫 행 `pid` 키)로 프로세스 행을 엔티티 harvesting에서 제외(서버 식별자는 filter_conditions에서만). (d) data/alarm_query 지시어 서버 필터 결정적 주입(근본) — `subagents._inject_demonstrative_hostname()`가 지시어로 특정 서버 지목+filter 비면 직전 hostname을 filter_conditions에 주입(전역 "전체/모든" 제외). query_generator._build_user_prompt가 확장 sub_query를 안 쓰고 original_query+filter만 써서 hostname 미도달이 근본 원인.
- **관련**: D-041, D-047, D-053, D-055, D-062

## D-057. 폼필(멀티DB) SQL 생성의 엔진·스키마 인지 — b0(DB2) POLESTAR 스키마 한정
- **결정일**: 2026-07-02 | **상태**: 확정
- **결정**: 테이블 스키마 한정을 domain_config `db_schema`를 단일 출처로 결정적 적용. `DBDomainConfig.db_schema`: gp/yd=`polestar`, b0=`POLESTAR`(DB2 대문자), 그 외 미설정. `multi_db_executor._generate_sql(db_id)`가 스키마 규칙 결정적 주입(설정 시 `POLESTAR.테이블`, DB2면 FETCH FIRST 명시).
- **근거**: b0 폼필이 `SQL0204N "SDQ000.CMM_RESOURCE" undefined`. 엔진·스키마 인지가 `build_hostname_sql`(D-053)에만 있고 LLM SQL 생성 경로 미이식 → 무스키마 `cmm_resource` 생성 → DB2가 CURRENT SCHEMA(SDQ000)로 해소. 실측: `TABSCHEMA='POLESTAR'`, `CURRENT SCHEMA=SDQ000`.
- **구현**: `src/routing/domain_config.py`(db_schema 필드), `src/routing/db_schema.py`(신규 `get_schema_prefix`/`qualify_table`), `multi_db_executor.py`, `polestar_hostname_resolver._table`(db_schema 우선), `polestar_b0.yaml`(예시 POLESTAR 한정).
- **주의**: DB2는 미인용 식별자를 대문자 저장 → 스키마명 대문자(POLESTAR). 최선(운영)은 mcp_server `.env` `POLESTAR_B0_CONNECTION`에 `CURRENTSCHEMA=POLESTAR` 추가(전 경로 자동 해소). 앱 `SET CURRENT SCHEMA` 앞단은 DBHub 읽기전용이라 불가.
- **관련**: D-053 확장

## D-058. 공동존(gp/yd) 서버 식별자 NULL 폴백 — COALESCE(name, hostname)
- **결정일**: 2026-07-02 | **상태**: 잠정(graceful degradation) — H1/H2 확정은 D-057 해소 후 진단
- **결정**: 서버 식별 출력 컬럼을 항상 `COALESCE(cmm_resource.name, cmm_resource.hostname)`로 생성(gp/yd 공통). 값 필터도 `(r.name='X' OR r.hostname='X')`. name/hostname 별도 요청 시 각각 출력하되 대표 컬럼은 COALESCE.
- **근거**: 여의도 폼필에서 `cmm_resource.name`이 전부 NULL이라 서버 식별 불가. name 미채움(H2) 또는 피벗 SQL 오류(H1, D-050류)인지 미판별 — COALESCE는 양쪽에서 안전한 임시책.
- **구현**: `polestar_cm_yd.yaml`·`polestar_cm_gp.yaml` query_guide `[★ 서버 식별자 NULL 폴백]` 절, 대표 query_examples server_name 출력 COALESCE화.
- **주의**: 확정 수정 아님. 057 해소 후 생성 SQL + `COUNT(*) FILTER(WHERE name IS NOT NULL)`로 H1/H2 확정 → H1이면 피벗 SQL 교정(COALESCE로 덮지 말 것), H2면 COALESCE 확정.
- **관련**: D-050, D-057(선행)

## D-059. 폼필 실패 시 침묵적 CSV 강등 금지 — 사유 노출
- **결정일**: 2026-07-02 | **상태**: 확정
- **결정**: `_generate_document_file`이 실패 시 `{"reason": ...}`(매핑 없음/양식·파일 없음/채우기 오류)를 반환하고 output_generator가 사유를 최종 응답에 결정적 노출. 성공 판별은 `file_bytes` 키 존재로. 데이터·매핑 있으면 일부 NULL이어도 Excel 생성 유지(total_filled==0도 bytes 반환).
- **근거**: 실패 시 `None` 반환 → output_file=None → 프론트가 Excel 감추고 CSV만 노출 → 사용자가 이유 모른 채 침묵적 강등.
- **구현**: `src/nodes/output_generator.py`(`_generate_document_file` 반환 규약: 성공=file_bytes, 실패=reason).
- **주의**: 결정적 산출물 생성 실패는 사유 구조화해 사용자 응답에 노출(침묵적 폴백 금지).
- **관련**: D-047/2026-06-26 정합

## D-061. 은행존(b0) 서버명(등록명)·호스트명·IP 구분 — 직접 컬럼 사용
- **결정일**: 2026-07-08 | **상태**: 확정(라이브 실측 기반)
- **결정**: b0도 서버명(name)≠호스트명(hostname) 구분. `column_synonyms` 추가(서버명/서버 이름/장비명→`cmm_resource.name`, 호스트명/호스트네임→`cmm_resource.hostname`), EAV `Hostname` synonyms에서 "서버명" 제거. 호스트명·IP 출력은 직접 컬럼(`r.hostname`/`r.ipaddress`) 사용.
- **근거**: b0 폼필에서 "서버 이름"·"호스트네임"이 둘 다 등록명으로 출력. 실측: `cmm_resource.name`=등록명(`"<host> (<desc>)"` 구조, 공백 유무 불규칙, name==hostname 서버도 다수), `hostname`/`ipaddress` 직접 컬럼=클린.
- **구현**: `config/db_profiles/polestar_b0.yaml` 단일 파일(코드 변경 없음). `column_synonyms`는 field_mapper 단계 적용이라 LLM 경로+D-038 결정적 빌더(`_ALLOWED_DIRECT_COLUMNS`) 양쪽 분리.
- **주의**: name에서 호스트명 파싱 금지(name==hostname·불규칙 공백으로 취약, 직접 컬럼이 항상 클린). name==hostname 서버는 두 칼럼 같은 값 나오는 게 정상(버그 아님).
- **관련**: D-058, 2026-06-10(공동존 name≠hostname)

## D-062. 딥 에이전트 경로 복합 결과의 단일 LLM 합성 (모순 이중 답변 제거)
- **결정일**: 2026-06-29 | **상태**: 확정
- **결정**: 복합 task 결과는 deterministic 이어붙이기 대신 LLM 1회 호출로 단일 일관 답변 합성(`result_aggregator(..., synthesize=True)`, `_synthesize_finalized`). 단일 task는 기존대로 통과(스트리밍 유지). 적용 경로 둘: 딥 에이전트(트랙 B) `_aggregate_with_fabrix`, 다중 의도 오케스트레이션(트랙 A) `result_aggregator` 그래프 노드.
- **근거**: 재시도 시 1차 "부분 성공"(CPU null·error 없음→completed)+2차 완전 성공이 둘 다 completed로 `"\n\n".join` → "CPU 없음"과 "CPU 8코어"가 한 말풍선에 혼재. supersedes(D-043)는 부분 성공엔 failed→success 매칭 안 됨.
- **구현**: `prompts/result_synthesizer.py`(신규), `output_generator.py`(`stream_user_response` 파라미터), `result_aggregator.py`(synthesize/`_synthesize_finalized`), `deep_agent.py`, `graph.py`(트랙 A 노드를 `partial(..., synthesize=True)` 바인딩 — 폐쇄망 실제 활성 경로라 필수).
- **주의**: 폐쇄망(deepagents 미설치)에서 실제 경로는 트랙 A이므로 graph.py 바인딩 필수(트랙 B만 적용하면 미발동). 합성 모드+복합 task는 per-task output_generator를 `stream_user_response=False`로, 최종 합성 1회만 USER_RESPONSE_TAG. 합성 예외/빈 결과면 `_merge_finalized` 폴백.
- **관련**: D-005, D-009, D-043, D-047

## D-063. 엔티티 확보 후 무의미 재시도 차단 (replanner 필드-null 재조회 가드)
- **결정일**: 2026-06-29 | **상태**: 확정(가드). 단, 이 사례의 진짜 원인은 조회 SQL 오류 — 근본 수정은 D-050. 본 가드는 동일 쿼리 반복 재시도 방지용 효율 가드로만 유효
- **결정**: `replanner`에 결정적 가드 `_filter_futile_retries` 추가. 행수+독립성으로 판정(supersedes 필드 존재에만 의존 안 함) — 제거(a) 신규 task supersedes가 >0행 반환 선행을 가리킴, 제거(b) 신규 task 독립(depends_on 비어있음)+같은 agent 선행이 >0행. 보존: 선행 0건이면 재조회 허용, 데이터 의존 보강·다른 agent 보강 허용.
- **근거**: CPU·메모리 null(실제로는 EAV 피벗+서버명 필터 SQL 오류=D-050)을 replanner가 "불충분→재조회"로 오판해 max_replan(3)까지 헛 재시도 → DB 왕복 4회+모순 이중 답변.
- **구현**: `replanner._filter_futile_retries`, `prompts/replanner.py` 판단 원칙 7(행 있고 일부 필드 null=속성 부재로 재조회 금지, 0건=대상 못 찾음과 구분).
- **주의**: "필드 null=속성 부재"를 단정하지 말 것(진짜 원인은 SQL 오류일 수 있음 — D-050). 필드가 정말 다른 DB에 있으면 회수 못 함 — 옳은 수정 지점은 field_mapper/DB 선택이지 replanner 아님.
- **관련**: D-043, D-062, D-040, D-050(근본)

## D-064. 텍스트 후속 턴의 폼필 요청-스코프 상태 누수 차단
- **결정일**: 2026-07-09 | **상태**: 확정(옵션 B — 경로 트리거 초기화 + field_mapper 스킵 자기정리)
- **결정**: 폼필 상태는 요청-스코프(세션-스코프 아님) — 이번 턴에 파일 없으면 존재 금지. (경로) 텍스트 후속 턴 입력을 `state.create_followup_input()`로 생성, 폼필 트리거(uploaded_file/file_type/csv_sheet_data)를 None 초기화. (노드) `field_mapper`가 template 없이 스킵 시 매핑 산출물(`_CLEARED_MAPPING_FIELDS`: mapped_db_ids/column_mapping/db_column_mapping/mapping_sources/llm_inference_details/mapping_report_md)을 None 정리.
- **근거**: LangGraph 체크포인터 델타 병합 — 텍스트 경로가 `{user_query,messages}`만 줘서 직전 폼업로드 턴의 uploaded_file/template_structure/mapped_db_ids 잔존 → input_parser 재파싱·intent_planner가 잔존 mapped_db_ids로 DB 고정.
- **구현**: `src/state.py`(create_followup_input), `src/api/routes/query.py`(텍스트 경로 2곳), `src/nodes/field_mapper.py`(_CLEARED_MAPPING_FIELDS).
- **주의**: `pending_synonym_registrations`(멀티턴 유사어 등록 흐름)는 초기화 금지 — 지우면 "N턴 폼필→N+1턴 등록" 깨짐. messages/conversation_context/pending_synonym_reuse도 보존. 체크포인터 병합은 MemorySaver 2턴 재현으로 실측.
- **관련**: D-053, D-055/D-056(승계 신호 불변)

## D-065. 바(bare) "공동존" 위치의 DB 라우팅 (gp+yd)
- **결정일**: 2026-07-09 | **상태**: 확정(aliases 보강 + input_parser 결정적 가드, `_resolve_priority_db_ids` 전용 분기 미사용)
- **결정**: gp/yd `aliases`에 단독 `"공동존"`/`"공동존 폴스타"` 추가(기존 지역-배제 로직상 "공동존"→[gp,yd], "공동존 김포"→[gp] 세분화 유지). input_parser 결정적 가드 `_ensure_location_hints`로 원문 위치 표면어(공동존/김포/여의도/은행/레거시/은행존)를 target_db_hints에 보강.
- **근거**: "공동존 전체 서버" 폼필이 b0로 오라우팅 — gp/yd aliases가 복합어만 있고 단독 "공동존" 없음, LLM 힌트 추출 비결정적 → priority 비면 active_db_ids 순서(b0 첫 항목)로 오확정.
- **구현**: `src/routing/domain_config.py`(gp/yd aliases), `src/nodes/input_parser.py`(`_ensure_location_hints`), `src/prompts/input_parser.py`(규칙 10 예시).
- **주의**: `_resolve_priority_db_ids` 전용 분기(`if "공동존" in hint: return [gp,yd]`) 넣지 말 것 — "공동존 김포" 세분화를 덮어써 회귀. aliases만 안전.
- **후속**: (1) 제품명 단독 "폴스타"가 b0 끌어들임 — bare "폴스타"가 b0 alias "은행 폴스타"에 부분매칭. `_resolve_priority_db_ids`가 지역 토큰 있으면 제품명 단독(폴스타/polestar) 제거(`_is_generic_only_hint`). 소수점 소실은 이 오라우팅의 결과(b0 DB2는 사용률 int). (2a) 공동존 gp만 조회(yd 누락) — field_mapper가 첫 priority DB에만 매핑 → `_replicate_mapping_for_multi_location`이 다중 priority면 매핑을 전 DB 복제. (2b) b0 사용률 정수 — DB2 `AVG()`가 정수 컬럼 정수 집계(truncate). 집계 전 DECIMAL 캐스트 `AVG(CAST(s.avg_val AS DECIMAL(15,4)))`(`::numeric`은 DB2 문법 오류). (3) 다중 존 다중 hint 상호 소거 — "은행 폴스타와 공동존 김포 폴스타"가 b0만. 지역 배제가 전체 hint 목록 훑어 양쪽 배제 → priority 빔. 수정: hint 단위 배제 `_hint_excludes_db(hint,db_id)`+`_DB_EXCLUDING_REGIONS`, "~와/과" 나열 hint는 서로 배제 금지.
- **관련**: D-053, D-057/D-058

## D-066. 단일/멀티 DB SQL 생성 경로 동등화 (few-shot 예시·전체조회 LIMIT 공유)
- **결정일**: 2026-07-09 | **상태**: 확정(RC1+RC4 우선 — 공용 헬퍼로 동등화)
- **결정**: 두 경로가 동일 출처 쓰도록 공용 헬퍼 `src/utils/query_gen_common.py` 신설 — `build_query_examples_block(structure_meta)`(프로필 query_examples를 few-shot 블록으로, 단일/멀티 공유), `resolve_query_limit(user_query, default_limit)`("전체/모든/모두"→100000 상향, 두 경로 적용).
- **근거**: 공동존 CPU/메모리 사용률 폼필이 `data_insufficient`+환각 조인(`cmm_metric_stat_h` 직접조인·`definition_name='CPUUtilization'`). b0(단일)는 정상. 원인: 단일 DB는 query_generator 풀 파이프라인(few-shot 예시 주입)이라 field_mapper 가짜 매핑을 이기지만, 멀티 DB `multi_db_executor`는 query_examples 미주입·default_limit 고정한 축소 재구현이라 가짜 매핑을 그대로 따름.
- **구현**: `src/utils/query_gen_common.py`(신규), `query_generator.py`, `multi_db_executor.py`.
- **주의**: "주입 코드 추가"만으론 부족 — 주입 대상 데이터가 그 경로에 실제 존재하는지 실측. 새 프롬프트 필드 추가 시 양 경로에서 실제 프롬프트 문자열에 실렸는지 확인.
- **후속**: (RC1 무효 근본) `build_query_examples_block`의 structure_meta가 멀티 DB 경로에선 항상 None — `_analyze_schema`가 `get_schema_or_fetch`(테이블 스키마만)만 호출, `_structure_meta`(query_guide/examples)는 별도 캐시 키라 미부착(단일 DB의 schema_analyzer 노드에만 있음). 수정: `_analyze_schema`가 `_load_manual_profile`→`get_structure_meta` 폴백으로 명시 부착. (후속2) RC2b: gp/yd EAV Hostname synonyms가 아직 `["EAV호스트명","서버명","호스트네임"]`(D-061이 b0만 고침) → `["EAV호스트명"]`로 축소. RC2: `## 양식-DB 매핑(반드시 SELECT 포함)`이 field_mapper의 지어낸 `cmm_metric_stat_h.cpu_avg_val` 강제 → `_generate_sql`이 `cmm_metric_stat` 매핑을 강제 블록에서 제외하고 예시 피벗(_m) 안내로 전환(사용률=EAV/피벗형이라 field→column 강제 불가). (후속3) 요청 무한 hang: field_mapper가 사용률 필드 매핑 시도 → step 3 LLM hang. Fix A: `perform_3step_mapping`이 사용률 필드(`_is_metric_usage_field`, `_schema_uses_metric_stat_pivot`일 때만)를 remaining에서 제외해 미매핑→예시 위임. Fix B: SSE 루프(`astream_events`)에 전체 타임아웃 부재 → 수동 iterator+`asyncio.wait_for(anext, timeout)`. (후속4) Fix A로 metric 필드 미매핑→excel 헤더 매칭 끊김. 미매핑 필드는 헤더명 그대로 alias(`AS "CPU 평균"`) 지시를 multi_db_executor에 이식, 소수 캐스트는 `decimal_cast_example(db_engine)`로 엔진별(PG `::numeric`/DB2 `CAST DECIMAL`). (후속5) DB별 독립 LLM 호출로 식별 필드 alias 비결정적 → "결과 alias는 양식 필드명(한글 헤더) 그대로, 임의 영문 금지". excel `_normalize_cell_value`로 Decimal→float. (후속6) 프롬프트 튜닝 한계 → 결정적 구조 3종: ① 동일 스키마 멀티 DB는 첫 검증 SQL 재사용(`_sql_by_schema`, 키=(engine,schema)), ② `active_db_engine`을 `get_domain_by_id(db_id).db_engine`로 실제 주입(지금껏 None이라 b0를 PG로 취급), ③ excel 매칭 표기 정규화(소문자+공백/언더스코어 제거).
- **관련**: D-057, D-050, D-058

## D-067. 재발 방지 드리프트 가드 (프로필·경로 중복 감시 테스트)
- **결정일**: 2026-07-09 | **상태**: 확정(B+C 가드레일. 근본 리팩터 A(프로필 상속)는 보류)
- **결정**: 저비용 테스트 가드레일로 "전파 누락·경로 비대칭"을 CI에서 즉시 실패시킴. B. 프로필 드리프트 가드(`test_polestar_profile_consistency.py`) — B-1 3개 프로필 각각 과거 버그 불변식 만족(EAV Hostname에 식별어 없음=D-061, name/hostname column_synonyms 분리, 월별 metric 예시 존재=D-066, 환각 패턴 없음, source=manual), B-2 gp/yd 실질 라인 차이는 allowlist(OSParameter 1건)뿐. C. 경로 패리티 가드(`test_query_gen_parity.py::TestCrossPathParity`) — 두 SQL 생성 경로가 동일 공유 헬퍼 참조·예시 실주입·LIMIT 대칭.
- **근거**: 반복 재발의 근본은 regression이 아니라 중복 — gp/yd/b0 near-duplicate 프로필 3벌(gp/yd 567줄 중 실질 1줄만 차이), 단일/멀티 DB SQL 생성 로직 2벌. 한 복사본만 패치하면 나머지에 결함 잔존.
- **구현**: 테스트 전용(프로덕션 코드 미변경).
- **주의**: 둘 다 "이미 아는 결함의 재발"만 잡음 — 신규 버그는 B-1에 assert 한 줄 추가 전까지 못 잡음(새 지뢰 밟을 때마다 성장). 근본 A(프로필 상속)는 loader 변경 부담으로 보류.
- **관련**: D-061, D-066

## D-068. 폼필 EAV 강제 SELECT의 resource_type 인지 다중 리소스 피벗
- **결정일**: 2026-07-13 | **상태**: 확정 (강제 블록 결정화 + 단일/멀티 경로 공유 헬퍼)
- **결정 (1차)**: 폼필 EAV 강제 SELECT를 resource_type 인지 다중 리소스 피벗으로 결정화. `eav_attr_resource_types(schema_info)`가 known_attributes 설명의 `[resource_type: …]`를 파싱해 속성명→resource_type 맵 구성, `build_multi_resource_pivot_block(...)`이 자식 리소스 EAV 속성 존재 시 resource_type 구분 CASE WHEN + `cc.configuration_id = c.resource_conf_id` 조인 + `GROUP BY COALESCE(platform_resource_id, id)` 형태를 결정적 생성. 두 경로가 공유 헬퍼 호출.
- **근거**: 강제 블록이 resource_type 구분 없이 `cc.name='LOGICALCORE'`만 생성 → LOGICALCORE(server.Cpus)·TotalSize(server.Memory) 등 자식 리소스 속성이 서버 config 그룹에 없어 영영 NULL. 틀린 명시 지시가 프로필 예시를 이김.
- **구현**: `query_generator`/`multi_db_executor` 공유. 부수 수정: `_build_user_prompt`가 누적 리스트 `parts`를 `parts=col.split('.')`로 덮어써 사용자 질의/요구사항 섹션 유실 → `col_parts`로 격리.
- **2차 정정 (metric 통합)**: config 전용 피벗이 사용률 필드(미매핑 unmapped 블록 alias `r`/`s`)와 GROUP BY 충돌(`column "r.name" must appear in GROUP BY`)로 회귀. `build_multi_resource_pivot_block`에 `metric_fields`·`db_engine` 인자 추가해 사용률을 같은 GROUP BY 스켈레톤에 접음(`ROUND(AVG/MAX(CASE WHEN …definition_name='Utilization'…))`). `classify_metric_field`로 골라 미매핑/metric 별도 블록에서 제외.
- **3차 정정 (결정적 조립)**: 통합 스켈레톤을 프롬프트로 강제해도 프로필 few-shot(월별 분해)과 경쟁해 서버 중복+config 빈칸. `build_multi_resource_pivot_sql(...)`이 스키마 한정·엔진별 소수캐스트·엔진별 LIMIT/FETCH·`resolve_stat_month`로 기간필터까지 완성 runnable SQL을 코드가 직접 조립하고 LLM 우회. 멀티는 자식 EAV 감지 시 즉시 return, 단일 `_try_build_form_fill_pivot_sql`은 LLM 전 단락, 재시도(에러) 시에만 LLM 폴백.
- **4차 정정 (게이트가 발동 안 됨)**: 게이트 `use_multi_resource_pivot=bool(child_eav)`의 `child_eav`가 `eav_attr_resource_types`(→attr_rt)에 의존하는데, 헬퍼가 `known_attributes`를 dict로 가정. 실 로더 `_load_manual_profile`은 known_attributes를 문자열로 평탄화하고 원본을 `known_attributes_detail`에 보존 → attr_rt 항상 빔 → 빌더 미발동(단일 성공은 LLM 폴백 우연). 수정: `eav_attr_resource_types`가 `known_attributes_detail` 우선, 없으면 known_attributes 폴백.
- **5차 정정 (서버명→hostname 오매핑)**: 결정성 발동 후 `서버 이름`이 `column_mapping["서버 이름"]="EAV:Hostname"`으로 매핑돼 서버 이름·호스트네임 둘 다 hostname. 전역 `global_synonyms.yaml`의 `Hostname:[…,서버명,…]`가 미끼. 공유 헬퍼 `correct_servername_hostname_mapping(column_mapping, entity_table)`이 서버명/서버이름/장비명/리소스명/등록명류가 `EAV:Hostname`이면 `<entity>.name`으로 교정, 두 경로가 분류 직전 호출.
- **6차 정정 (재오염 자기강화 루프)**: `_register_llm_mappings_to_redis`/`_register_llm_synonym_discoveries_to_redis`/`apply_mapping_feedback_to_redis`가 오매핑을 사용자 확인 없이 Redis에 자동 재등록 → 씨앗에서 재번짐. 수정 A: `is_servername_to_hostname(field, column)`으로 서버명류→hostname 자동 등록을 세 등록 함수 전부에서 거부. 수정 B: `correct_servername_hostname_mapping`을 직접 `*.hostname` 컬럼(`is_hostname_target`)까지 확장. 판정 헬퍼는 `query_gen_common` 단일 출처.
- **7차 정정 (이종 엔진 CSV 칼럼 중복·DB2 스케일)**: (1) DB2가 결과 칼럼 라틴 문자를 소문자 반환 → `IP주소`/`ip주소` CSV 중복. `_merge_results`가 칼럼명을 정규화(소문자·공백/언더스코어 제거) canonical(양식 필드 우선)로 통일. (2) DB2 `AVG(DECIMAL)` 스케일 확장으로 엑셀 제로필 → `_metric_select_line` DB2를 `CAST(ROUND(AVG/MAX(...),2) AS DECIMAL(15,2))`로 스케일 2 고정(PG는 `::numeric` 유지).
- **주의**: Hostname 브릿지 조인 금지 명시. 순수 server.Server EAV·비폼필 경로는 기존 브릿지 블록 유지(회귀 0). 조각난 강제 블록에 또 다른 구조 강제를 얹으면 alias·집계 스코프 충돌 → 반드시 상호배타 게이팅. 프롬프트 유도가 경쟁 지시로 반복 실패하면 코드가 직접 조립. 결정적 게이트가 의존하는 데이터의 실 런타임 shape를 반드시 실측(로더 변형 시 mock 통과·프로덕션 게이트 사망). 결정성이 노출한 매핑 오류는 되돌리지 말고 결정적 가드로 교정. 자동등록 오염은 출력 교정만으론 부족, 쓰기(등록) 지점에서 차단.
- **관련**: D-050, D-057, D-066, D-067

## D-069. 어드민 접근 통합 RBAC (Plan 59 Part A · 방향 A)
- **결정일**: 2026-07-14 | **상태**: 확정 (사용자 승인: 방향 A 1안)
- **결정**: 어드민 접근을 DB `user.role == ADMIN`으로 판정(통합 RBAC). 고정 운영자 계정은 break-glass seed 1개로 축소. 신규 `dependencies.require_admin_user` 통합 가드: ①개발 모드(AUTH_ENABLED=false) 우회 ②break-glass 운영자 토큰(type="admin") 허용 ③사용자 토큰+DB 실시간 role==admin.
- **근거**: 방향 B(운영자 계정 분리)는 "권한 부여로 어드민 접근" 기대 미충족. 방향 A는 죽은 `UserRole.ADMIN` 필드(소비처 0건)를 부활시켜 멘탈 모델 일치, break-glass seed로 DB 장애 시 진입성 보존.
- **구현**: `admin.py`(15개)·`schema_cache.py`(14개) 엔드포인트를 `require_admin`→`require_admin_user`로 교체. 기동 시 활성 관리자 0명이면 `server._seed_admin_user` 멱등 생성. 최소-1-admin 가드 `_ensure_not_last_active_admin`(마지막 활성 관리자 강등·비활성화·삭제 차단). 프론트: role==admin·개발 모드일 때만 어드민 링크 노출.
- **관련**: D-026, D-070(시크릿 분리), D-071(하드닝), D-082(존 RBAC 재사용)

## D-070. 운영자 토큰 type 검증 + 사용자/운영자 시크릿 분리 (권한 상승 차단)
- **결정일**: 2026-07-14 | **상태**: 확정 (보안 즉시 수정, 방향 무관)
- **결정**: ① `verify_admin_token`이 `payload.get("type") != "admin"`이면 401(D-026 원의도 강제, 사용자 토큰 통과 즉시 차단). ② 사용자 토큰을 `AuthConfig.jwt_secret`(env `AUTH_JWT_SECRET`)로 분리 서명(`_create_user_token`)·검증(`dependencies._verify_user_token`), 운영자 토큰은 `config.admin.jwt_secret` 유지 → 교차 서명 불가.
- **근거 [보안 결함]**: `verify_admin_token`이 `sub`만 확인하고 `type`·`role` 미검증 + 사용자 토큰도 동일 시크릿 서명 → 임의 사용자 JWT로 모든 `/admin/*`·schema_cache 통과 가능(.env 수정·DB 접속정보 변경·사용자 삭제). ①은 P0 핫픽스, ②는 심층 방어.
- **구현**: 마이그레이션은 배포 시 기존 사용자 토큰 무효화(재로그인 수용). 회귀 `tests/test_api/test_admin_rbac.py`.
- **관련**: D-026(원의도 미구현이었음), D-069

## D-071. 기본 크레덴셜·시크릿 하드닝 (운영 모드 기동 거부)
- **결정일**: 2026-07-14 | **상태**: 확정 (사용자 승인: 강한 거부, JWT 시크릿 사전 기입 권장)
- **결정**: `AdminConfig` 기본 크레덴셜 `admin`/`admin123` 제거(빈 문자열 기본값). 운영 모드(AUTH_ENABLED=true)에서 `ADMIN_USERNAME`/`ADMIN_PASSWORD`·`ADMIN_JWT_SECRET`·`AUTH_JWT_SECRET` 미설정 시 기동 거부(`server._validate_production_secrets`). 개발 모드는 jwt_secret 미설정 시 임시 랜덤 생성.
- **구현**: "명시 설정 여부"를 `AdminConfig`/`AuthConfig`의 `PrivateAttr _jwt_secret_explicit` 플래그(model_post_init에서 자동 생성 전 기록)와 pydantic 필드로 판정.
- **주의**: `os.getenv`는 pydantic-settings의 `.env`/`.encenv` 로딩 값을 못 봄(2026-06-10 교훈) → 설정 유무 판단에 `os.getenv` 사용 금지.
- **관련**: D-070, Known Mistakes 2026-06-10

## D-082. 알림 지역 스코프 RBAC + 쿠키 SSE 인증 + 존 필터 (Plan 59 Part C)
- **결정일**: 2026-07-14 | **상태**: 확정 (사용자 승인: 데이터 모델 1, SSE 인증 쿠키(B))
- **결정**: 무인증 브로드캐스트하던 `/alarm/notifications/stream`을 지역 스코프로 인가(관리자=전 존, 공동존/은행존 운영자=해당 존, 일반=403, 중복 할당 가능). 데이터 모델 1: `User.alarm_zones: list[str]` 신규 필드. 쿠키 기반 SSE 인증(선택 B): 로그인 시 JWT를 HttpOnly `user_token` 쿠키로도 세팅.
- **근거**: 조회 권한(allowed_db_ids)과 알림 존은 별개 축이라 전용 필드 분리. 모델 2(역할 리스트 일반화)는 Part A 소비처 광범위 변경으로 기각. 구독자측 필터로 충분(트래픽 소규모).
- **구현**: 존↔db_id 단일 출처 `src/routing/zones.py`(`ZONE_GONGJON=[polestar_cm_gp, polestar_cm_yd]`, `ZONE_BANKJON=[polestar_b0]`, `zone_to_db_ids`/`db_id_to_zone`/`normalize_zones`). `dependencies.resolve_stream_user`(쿠키→헤더→쿼리)·`alarm_zones_for_user`(admin·개발=전 존)로 존 산출→빈 집합 403, `event_generator`가 `db_id_to_zone(event.db_id)`로 구독자 존만 통과. DDL `ALTER TABLE ADD COLUMN IF NOT EXISTS alarm_zones TEXT[]` 멱등. 프론트: 권한자만 EventSource open+수신 토글(localStorage).
- **주의**: SSO 보존(필수 제약) — 로그인/인증 흐름 불변, 역할/존은 인증 성공 후 인가 계층에만 추가. 존↔db_id 하드코딩 분산 금지(D-053 교훈).
- **관련**: D-069(통합 RBAC 재사용), D-026(인증 불변), D-053. 번호: 최초 D-072 등재 → 2026-07-15 팀장 브랜치 Plan 61(D-072~076)·Plan 60(D-077~081) 선점 확인, 팀장 우선 원칙으로 D-082 재배정.

## D-083. 보호 root 계정 + 감사 로그 로테이션 배선 + 어드민 진입 규약 정정 (Plan 59-a)
- **결정일**: 2026-07-15 | **상태**: 확정 (사용자 승인)
- **결정**: 실테스트로 드러난 3결함 수정. (1) 어드민 정상 진입은 `/login` 단일 창구 → role==admin이면 ADMIN 버튼 → `/admin`. admin.js 미인증 리다이렉트를 `/admin/login`→`/login?next=/admin`으로 교정, 403(비관리자)은 `/`로. (2) 보호 root 계정 `User.is_protected: bool` 신설, seed admin을 `is_protected=True`로 생성. (3) 감사 로그 로테이션 배선.
- **근거**: `/admin/login`(env 운영자 break-glass 전용)이 DB 사용자(role==admin) 인증을 안 해 로그인 실패. `cleanup_old_logs()` 구현·설정은 있으나 호출부 전역 0건이라 무효. 보호 표식은 `is_protected` 컬럼 방식(username 변경에도 보호 고정, `user_id==ADMIN_USERNAME` 매칭 기각).
- **구현**: DDL `ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS is_protected BOOLEAN NOT NULL DEFAULT FALSE`(멱등). 보호 계정 역할·상태 변경·삭제·PW초기화 403 차단, 부서·알림그룹은 허용. 로테이션: `retention_days`(기본 90, `AUDIT_RETENTION_DAYS`) 기준 (A)기동 1회 (B)하루 1회 백그라운드 태스크 (C)수동 `POST /admin/audit/cleanup`, `<=0`이면 비활성. 알림그룹 체크박스 UI(K리전 공동존·은행존)·부서 인라인 편집.
- **주의**: `/admin/login`은 링크 없는 break-glass 전용으로만 유지. login.html `next` 복귀는 같은 출처 상대경로만(오픈 리다이렉트 방지). 설정·구현이 있어도 호출·스케줄 배선을 grep 확인.
- **관련**: D-069(최소-1-admin 가드 보완), D-070(진입 규약), D-082(알림그룹 UI 완성). 번호: 최초 D-073 등재 → 2026-07-15 팀장 브랜치 선점 확인, D-083 재배정.

## D-072. Text-to-SQL EX 평가 하네스 (Plan 61 E1)
- **결정일**: 2026-07-14 | **상태**: 확정 (구현 완료 — 순수 신규 파일·오프라인 배치, 런타임 경로 무변경)
- **결정**: `scripts/eval_text2sql.py`가 골드셋을 배치 실행해 EX(Execution Accuracy=결과집합 동치)·재시도·LLM 호출·지연을 리포트. 3경로 구동(`--path {graph,orchestration,multidb}`)으로 단일/멀티 DB 비대칭 회귀 실측. A/B 축 플래그(`--synonym-fuzzy`/`--value-retrieval`/`--candidate-count`/`--selection`/`--semantic-compose`).
- **근거**: Plan 61 세 트랙 개선을 데이터로 정당화할 EX 측정 수단이 부재. 트랙 C 착수 필요성(커버리지율)·트랙 A 착수 필요성(커버리지 밖 비율)이 추정에 그침.
- **구현**: 골드셋 `testdata/text2sql_gold/`(26건: gp 15·yd 6·b0 5), 항목 스키마 `(query, gold_sql, gold_result_signature, gold_smq, category, coverage, notes)`. 트랙 C 전용 축 2개: SMQ 생성 정확도(`gold_smq`→`smq_match`, EX와 분리)·커버리지율(`coverage=inside` 비율 `coverage_stats`). 접속 안전성 `--dry-run`/`--mock`/graceful 스킵. 단위 `tests/text2sql/test_ex_harness.py`.
- **주의**: EX는 SQL 문자열이 아니라 결과집합 동치로 채점(Spider/BIRD 방식). 대표성 원칙 — `coverage=outside` 항목을 반드시 포함해 커버리지율 낙관 편향 방지. 순수 신규 파일만 추가, `src/` 런타임 경로 무변경. 실행은 D-003 읽기전용 전제.
- **관련**: D-003, D-035, D-051, D-019

## D-073. 다중 후보 생성 — 복잡도 조건부 (Plan 61 트랙 A / E2·E3)
- **결정일**: 2026-07-15 | **상태**: 확정 (구현 완료 — 기본 OFF `TEXT2SQL_MULTI_CANDIDATE=false`, 회귀 0)
- **결정**: `src/nodes/candidate_generator.py` 신설. 후보 이득은 "많음"이 아니라 "다양성"에서 온다(CHASE-SQL·XiYan-SQL) → multi_prompt 우선: (a)base 현행 템플릿, (b)divide-and-conquer(CTE 분해), (c)실행계획 CoT. E3 복잡도 게이트 `classify_complexity`(결정적 규칙: 다중 query_target·집계/순위/범위비교 키워드·EAV 다중속성 → complex)로 `TEXT2SQL_COMPLEXITY_GATE=on`이면 complex만 다중 후보.
- **근거**: 현행은 단일-후보·에러기반-재시도 구조. E1 하네스 측정 결과 커버리지 밖 비율 23.1%(6/26)로 통계적 2차 방어선 정당화.
- **구현**: `src/prompts/candidate_strategies.py`에 전략 suffix(base 프롬프트에 추가 지시만 덧붙임). 삽입 지점은 그래프 엣지 아닌 `query_generator` 함수 내부(경로 A·B 공유) + `multi_db_executor._generate_sql`(경로 C 명시 이식), `subagents.py` 무변경. 상태 `AgentState.sql_candidates`([{sql,strategy,confidence}]) 추가, `generated_sql`은 선택 결과로 유지. 플래그: `TEXT2SQL_CANDIDATE_COUNT`(3)·`TEXT2SQL_CANDIDATE_STRATEGIES`(multi_prompt)·`Text2SQLConfig` 노출.
- **주의**: 복잡도 게이트는 품질 목적(비용 컷 아님). OFF 시 단일 LLM 호출 1회로 기존 경로 바이트 무변경(`test_multi_candidate_off_single_path`). temperature 다양화는 provider 지원 의존.
- **관련**: D-074(실행기반 선택 — 짝), D-076, D-066, D-035

## D-074. 실행기반 후보 선택 — 규칙필터→결과일관성 투표→LLM 선택 폴백 (Plan 61 트랙 A / E4)
- **결정일**: 2026-07-15 | **상태**: 확정 (구현 완료 — 기본 OFF, 회귀 0)
- **결정**: `src/nodes/candidate_selector.py` 신설. 선택 파이프라인(결정적 우선): (1) 규칙 사전필터(경로별 validator 주입, 통과분 잔류, 전부 탈락 시 필터 없이 실행) (2) 읽기전용 실행(D-003) (3) 결과 일관성 투표(결과집합 시그니처로 그룹화, 최다 그룹 선택, 신뢰도=승자 그룹/성공 후보 수) (4) 동수/전패 폴백은 `hybrid`/`llm`에서 LLM 쌍대비교 심판, 전 후보 실패 시 `all_failed=True`→기존 에러기반 재시도 강등.
- **근거**: 다중 후보(D-073)를 만들어도 선택기 없으면 이득 없음. 폐쇄망·파인튜닝 부재로 결정적 결과일관성 투표 1차, LLM 쌍대비교 폴백(hybrid 사용자 결정).
- **구현**: `validate`·`execute`를 주입받아 경로 (A/B)단일 DB·(C)멀티 DB 비대칭 원천 차단(selector는 db·config 직접 import 안 함). 합성 헬퍼 `run_candidate_pipeline`이 생성+선택 공유. 3단 폴백 신뢰도 게이트: `semantic_fallback=candidate_then_human`이면 다중 후보→신뢰도 게이트(`TEXT2SQL_FALLBACK_CONFIDENCE_MIN`)→사람 검토(기존 `approval_gate` HITL 재사용). `semantic_fallback` 기본 `llm`→`candidate_then_human` 전환. 플래그 `TEXT2SQL_SELECTION`(hybrid).
- **주의**: OFF 기본, 후보 1개면 즉시 반환(=현행). `fallback_confidence_min=0.0` 기본에서 human_review는 전 후보 실패 시에만 발동. 경로 C의 3단 폴백 HITL은 그래프 고유라 orchestration/multidb에선 정보 필드로만(우아한 강등). 검증 `test_candidate_pipeline.py`(17)·`test_query_generator_multi_candidate.py`(9).
- **관련**: D-073(짝), D-003, D-035, D-066

## D-075. 동의어 매칭 고도화 — 유연 매칭 + 값 검색 + 사전 위생 (Plan 61 트랙 B / E5)
- **결정일**: 2026-07-14 (E5-3 거버넌스·E5-2 런타임 주입 보강: 2026-07-15) | **상태**: 대부분 구현 (E5-1·E5-2·E5-3 상당부; E5-4는 D-084로 구현)
- **결정**: 동의어 관리 취약점 3축 보강. E5-1 유연 매칭 `src/utils/flex_match.py`(폴백 계단: 정확 동등→한글 자모 분해·NFC→편집거리 Levenshtein→토큰/부분어 포함, 각 매칭에 신뢰도 점수, 임계 이하는 후보 제시). E5-2 값 검색 `src/schema_cache/value_index.py`(distinct resource_type·EAV NAME을 읽기전용 SELECT DISTINCT로 인덱싱, `build_value_index`/`search_value_index`). E5-3 사전 위생(거버넌스 메타·상한 config화).
- **근거**: `docs/synonym_management_analysis.md` 판정 — 목적·거버넌스는 적정하나 매칭 정밀도(정확일치 한정)·값 검색 미흡·측정 부재. "메모리 사용률"은 잡지만 "메모리 이용률/점유율"·오탈자 놓침, WHERE 리터럴 미보유 시 환각.
- **구현**: E5-1 적용 2지점 — ①`field_mapper._synonym_match(fuzzy=...)`(폼필, 임계 이하는 `pending_synonym_registrations` 후보 회부) ②`schema_analyzer._synonym_tables_matching_query(fuzzy=...)`(텍스트 질의). 빈문자열·1글자 가드를 공유 유틸에 신설. E5-3 거버넌스: Redis 저장 구조를 `{words,sources,meta}`로 확장(하위호환), `increment_synonym_usage`·`prune_stale_synonyms`(operator 보존·레거시 보존·strictly-older 경계), 충돌 우선순위 랭킹 `src/utils/synonym_governance.py`(operator>usage_count>confidence>컬럼사전순). E5-2 런타임: `value_index.load_or_build_value_index`를 `schema_analyzer`가 적재→`state.column_value_index`, `query_gen_common.build_value_index_block`로 프롬프트 주입(경로 A/B). config `SynonymMatchConfig`(env_prefix `SYNONYM_`).
- **주의**: 플래그 전부 기본 OFF — `SYNONYM_FUZZY_MATCH`·`SYNONYM_VALUE_RETRIEVAL`·`SYNONYM_GOVERNANCE`·`SYNONYM_SEMANTIC_MATCH` OFF 시 저장/경로 바이트 무변경(회귀 0). E5-4 의미(임베딩) 검색은 D-084에서 구현(기본 OFF — 최초 자리예약은 E5-1 후 잔여 미매칭율 측정 게이트였음). 퍼지 과잉매칭은 신뢰도 임계·후보 제시로 방어.
- **관련**: D-012, D-035, D-051, D-019, D-073/D-074(값 검색 프롬프트 주입 소비)

## D-076. 시맨틱 모델 기반 결정적 SQL 조합 (Plan 61 트랙 C / E6)
- **결정일**: 2026-07-14 | **상태**: 구현 (기본 OFF·옵트인, 커버리지 내 결정적 컴파일 + 커버리지 밖 현행 LLM 폴백)
- **결정**: 쿼리 생성을 "SQL을 쓰라"에서 "검증된 dimension을 조합하라"로 재정의(SMQ+결정적 컴파일러). LLM은 자연어→SMQ(컬럼 선택만), 컴파일러가 SMQ→방언별 SQL → 잘못된 조인·집계·미존재 컬럼이 원천 불가능.
- **근거**: 현행은 프로필 선언을 LLM에 텍스트 주입해 자유 모방시켜 전체 복사·CASE-WHEN 누락·금지 id 조인·미존재 resource_type 반복. 폴스타는 선언적 원재료 보유, 빠진 것은 결정적 조합 엔진 하나. D-035와 가장 정합.
- **구현**: E6-1 시맨틱 모델 `config/semantic_models/{db_id}.yaml`(gp/yd/b0, 기존 `db_profiles/*.yaml` 불변, 3패턴 A 서버설정 EAV 피벗·B 성능지표 measure·C 알람 정규화 조인, db_engine/db_schema는 `get_domain_by_id`에서 결정적 주입). E6-2 `src/nodes/semantic_compiler.py`(폴스타판 SMQ Pydantic, 패턴 A/B는 기존 `build_multi_resource_pivot_sql`(D-068) 재사용 — `explicit_measures` 인자 추가로 후방호환, 패턴 C는 알람 정규화 조인 전용). E6-3 `coverage_router`가 `compile_from_nl`을 `query_generator` 진입부(경로 A·B 공유)+`multi_db_executor._generate_sql`(경로 C 명시 이식)에서 호출, 커버리지 밖(LOB·서버필터·기간필터·집계·미정의)은 None→현행 LLM 폴백. E5-2 값 검색 연결(SMQ 필터가 모델 밖 실측값 참조 시 value_index 검증). E6-4 골든 회귀 `tests/text2sql/test_semantic_golden.py`(34건).
- **주의**: 기본 OFF `TEXT2SQL_SEMANTIC_COMPOSE=false` → 진입 가드 전부 False, 기존 경로 무변경. 이중 조립 엔진 금지(D-067) — pivot 테스트 13건 바이트 보존 확인. 이름은 `coverage_router`(기존 `src/routing/semantic_router.py` 의도 라우터와 충돌 회피). Model(server.Server)/MODEL(server.Cpus) 대소문자 충돌은 정확이름 우선. SMQ 선택 오류(문법 정상·의미 오답)는 남아 E1 하네스 SMQ 정확도 축으로 별도 측정(gold_smq 6건+outside 6건).
- **관련**: D-035, D-067, D-068, D-057, D-003, D-072, D-075(E5-2 의존)

## D-084. E5-4 임베딩 의미 검색 구현 — 계단 마지막 단 + 백엔드 2종 (Plan 61 트랙 B / E5-4)
- **결정일**: 2026-07-16 | **상태**: 확정 (구현 완료 — 기본 OFF·옵트인, 회귀 0)
- **결정**: D-075에서 자리예약만 있던 E5-4를 Plan 61 §E5-4 폐쇄망 확정 설계대로 구현. 문서상 "착수 근거 소멸(시딩으로 E5-1 잔여 미매칭 38.5%→0%)" 상태였으나 **사용자 지시(2026-07-16)로 선구현** — 기본 OFF를 유지하고 활성화 판단은 실사용 질의 로그 재측정 후로 유보. `src/schema_cache/synonym_semantic.py` 신설: 정확일치 → E5-1 퍼지 → **임베딩** 계단 마지막 단(임베딩 단독 매칭 금지), 인프로세스 numpy 코사인 인덱스(Redis 무변경 — D-019·D-051 불변), 단어 임베딩 LRU 캐시(상한 2만, bound 필수), 확정 임계 `SYNONYM_SEMANTIC_CONFIDENCE_MIN`(기본 0.65) 미만은 확정 매핑이 아닌 후보 제시(LLM 발견·승인 루프 D-012 위임).
- **백엔드 2종**(`SYNONYM_SEMANTIC_BACKEND`, 사용자 지시): `local`(기본) — 오프라인 반입 sentence-transformers 다국어 모델 **인프로세스 CPU 상주**(`SYNONYM_SEMANTIC_MODEL_PATH`); `vllm`(옵션) — **별도 vLLM 서버에 임베딩 모델을 로딩**해 OpenAI 호환 `/v1/embeddings` 호출(`SYNONYM_SEMANTIC_VLLM_BASE_URL`·`SYNONYM_SEMANTIC_VLLM_MODEL`, SSL 검증 옵션 D-060 계열, 타임아웃 15s).
- **구현**: E5-1과 동일 2지점 **대칭** 주입 — ①`schema_analyzer._synonym_tables_matching_query(semantic=...)`(텍스트 질의 allowed_tables 동적 보완, cap·중복 가드 준수) ②`field_mapper._apply_synonym_mapping` Pass 4(폼필, `is_servername_to_hostname` 가드 D-068 동일 적용, 임계 미달은 pending 승인 루프 위임). 의존성 optional extra `pip install .[semantic]`(vllm 백엔드는 numpy만 필수). config `SynonymMatchConfig` 확장. **계측 로그(E5-계측)**: 동의어 사용 결정 지점 전체(정확/퍼지/임베딩 매칭, EAV, 확정·임계 미달, query_generator 최종 SQL 반영·미등록 리터럴)에 `[동의어]` 태그 INFO 로그 — 테스트 시 `pytest --log-cli-level=INFO`로 콘솔 확인, 로그 계약은 테스트로 고정.
- **주의**: `SYNONYM_SEMANTIC_MATCH=false` 기본 OFF — OFF 시 임베딩 모듈을 임포트조차 하지 않음(바이트 무변경, 테스트로 고정). ON+요건 미충족(모델 경로·의존성·vLLM 설정 부재)이면 **경고 1회 후** 임베딩 단계만 무매칭(침묵 강등 금지 — 사유 가시화, 기존 정확·퍼지 계단은 무변경). 런타임 임베딩 실패(vLLM 미서빙·타임아웃)도 경고 후 이번 호출만 무매칭 강등. 검증 `test_synonym_semantic.py`(15)·`test_schema_analyzer_synonym_semantic.py`(10)·`test_field_mapper_semantic.py`(10).
- **관련**: D-075(자리예약 해소), D-012(후보 승인 루프), D-068(hostname 가드), D-060(SSL 검증 선례), D-051(보완 상한), D-019(Redis 불변)

## D-085. LEFT JOIN 강등(WHERE 필터 배치) 결정적 가드 + 생성 규칙 (SYN-H-02 회귀)
- **결정일**: 2026-07-16 | **상태**: 확정 (구현 완료)
- **배경**: 복합 쿼리 검증(SYN-H-02, "2026년 6월 CPU 사용률 평균이 40%를 넘은 서버의 서버명과 논리코어 수") 실측에서 생성 SQL이 LEFT JOIN한 `cmm_metric_stat_m`의 필터(`definition_name`·`stat_date`)를 WHERE에 배치 → 미매칭 행 탈락으로 LEFT JOIN이 INNER JOIN으로 강등, 메트릭 행이 없는 server.Server 행이 GROUP BY 전에 제거되어 **서버명 전체 NULL** 반환(행 수는 3건으로 정상처럼 보이는 침묵 결함). 유사어 매핑은 정상이었고 순수 SQL 골격 오류.
- **결정**: 이중 방어 — ①생성 예방: `src/prompts/query_generator.py` 두 템플릿(범용 규칙 12, Polestar 추가 규칙 11)에 "LEFT JOIN 테이블 필터는 ON 절 배치(임계 조건은 HAVING), 행 필터 의도면 INNER JOIN" 규칙 추가. ②결정적 차단: `query_validator` 6.7 `_check_left_join_where_demotion()` — LEFT JOIN 참조(별칭/bare명)의 컬럼이 최상위 WHERE에서 비교 연산자로 사용되면 **error**(기존 재시도 루프로 교정 지침 회귀). `multi_db_executor._validate_sql_simple`에도 동일 호출 배선(단일/멀티 대칭, D-066 계열).
- **구현**: 오탐 억제 — 문자열 리터럴·괄호 반복 마스킹으로 서브쿼리 내부 WHERE·함수 인자(COALESCE 등)·IN 리스트 제외, `IS NULL`/`IS NOT NULL`은 연산자 집합 비포함으로 자연 제외, INNER JOIN 테이블의 WHERE 필터는 정상 의미이므로 무감지(Template B 무영향). 검증 `tests/test_nodes/test_query_validator_left_join_demotion.py`(회귀 원본 SQL 감지·교정본 통과·멀티 경로 대칭 포함).
- **주의**: 자동 재작성(WHERE→ON 이동)은 하지 않음 — sqlparse는 토크나이저라 OR/괄호 결합 시 의미 보존 재배치가 불안전, 감지+교정 지침 재생성(6.6 금지 조인과 동일 기법)으로 통일. LEFT JOIN 서브쿼리(`LEFT JOIN (SELECT ...) x`)는 마스킹으로 감지 대상 제외(보수적 범위).
- **관련**: D-066(경로 대칭), D-068(피벗 패턴), D-022(validator 감지 선례), Known Mistakes "LLM 비결정성 대응"

---

## 변경 이력

> 각 변경의 상세 전문은 `docs/02_decision_full.md`(2026-07-16 아카이브) 참조.

| 날짜 | 결정 ID | 변경 내용 |
|------|---------|----------|
| 2026-07-16 | D-085 | **LEFT JOIN 강등(WHERE 필터) 결정적 가드 + 생성 규칙** — SYN-H-02 실측 회귀(서버명 전체 NULL) 대응. validator 6.7 `_check_left_join_where_demotion`(error→재시도)·`_validate_sql_simple` 대칭 배선·프롬프트 규칙 2개 템플릿 추가. |
| 2026-07-16 | D-084 | **E5-4 임베딩 의미 검색 구현(Plan 61 트랙 B)** — 정확→퍼지→임베딩 계단 마지막 단 `synonym_semantic.py`(numpy 코사인·LRU 캐시), 백엔드 local(sentence-transformers CPU 상주, 기본)/vllm(별도 서버 `/v1/embeddings`), E5-1과 동일 2지점 대칭 주입, 기본 OFF·회귀 0. |
| 2026-07-16 | 전체 | **문서 압축본 전환** — 전 결정(D-001~D-083)·변경 이력을 압축 형식으로 재작성. 전문은 `docs/02_decision_full.md` 아카이브. |
| 2026-07-15 | D-083 | **보호 root 계정 + 감사 로그 로테이션 + 어드민 진입 규약 정정 (Plan 59-a)** — 미인증 `/admin`→`/login?next=/admin` 교정, `is_protected` root 가드(역할/삭제/PW 403), `cleanup_old_logs` 배선(기동+일1회+수동). |
| 2026-07-14 | D-082 | **알림 지역 스코프 RBAC + 쿠키 SSE 인증 + 존 필터 (Plan 59 Part C)** — 무인증 SSE를 존 스코프로 인가, `alarm_zones` 필드, `routing/zones.py` 단일출처, 쿠키 인증+프론트 사전 게이트. |
| 2026-07-14 | D-069~071 | **어드민 접근 통합 RBAC + 권한 상승 차단 + 크레덴셜 하드닝 (Plan 59 Part A)** — 사용자 JWT의 `/admin` 통과(P0) 차단(type 검증·시크릿 분리), DB role==admin 가드, 기본 크레덴셜 제거. |
| 2026-07-15 | D-073 | **다중 후보 생성(Plan 61 트랙 A / E2·E3)** — `candidate_generator.py`(multi_prompt CoT 전략)+결정적 `classify_complexity`, 3경로 공유, 기본 OFF·회귀 0. |
| 2026-07-15 | D-074 | **실행기반 후보 선택(Plan 61 트랙 A / E4)** — `candidate_selector.py`(규칙필터→읽기전용 실행→결과일관성 투표→LLM 쌍대비교 폴백), 3단 폴백, 기본 OFF. |
| 2026-07-15 | D-075 | **(보강) E5-3 거버넌스 + E5-2 런타임 적재** — 유사어 메타 Redis 확장·prune·`synonym_governance.py`, value_index를 schema_analyzer가 적재→생성 프롬프트 주입. 기본 OFF. |
| 2026-07-14 | D-076 | **(후속) 로컬 개발 샌드박스(db_id `polestar`) 트랙 C 검증 환경 편입** — "전체 CPU 사용률" 0건 진단, `polestar.yaml`+`DB_DOMAINS` 재등재로 SMQ 컴파일 E2E 성공(50행). |
| 2026-07-14 | D-076 | **(후속4) LLM 폴백 경로의 기간(stat_month) 결정적 주입** — 진행중 달 포함·월별 중복 → `build_stat_month_block`으로 `s.stat_date='YYYYMM'` 등호 필터 강제(단일·멀티 주입). |
| 2026-07-14 | D-076 | **(후속3) 로컬 폴백 LLM 경로 복구 + 알람/이벤트 라우팅 결정화** — event 질의 환각 SQL 3중 수정: polestar db_profile 신설, isolated routing_intent 설정, `_coerce_alarm_intent` 교정. |
| 2026-07-14 | D-076 | **(후속2) 패턴 B 식별 dimension 결정적 주입 + 레거시 라우팅 테스트 현행화** — LLM SMQ의 `dimensions=[]` → `default_dimensions:[name,hostname]` 주입, stale 테스트 9건 현행화. |
| 2026-07-14 | D-076 | **시맨틱 모델 기반 결정적 SQL 조합(Plan 61 트랙 C / E6)** — NL→SMQ(선택)·컴파일러가 SMQ→방언SQL로 환각 차단, `semantic_models/*.yaml`+`semantic_compiler.py`, 기본 OFF. |
| 2026-07-14 | D-072 | **Text-to-SQL EX 평가 하네스(Plan 61 E1)** — 3경로 EX 측정 `scripts/eval_text2sql.py`+골드셋 26건(inside/outside·gold_smq), src 런타임 무변경. |
| 2026-07-14 | D-075 | **동의어 매칭 고도화(Plan 61 트랙 B / E5)** — `flex_match.py`(정확→자모→편집거리 계단) 2지점 적용, value_index 인프라, 기본 OFF. |
| 2026-07-13 | D-068 7차 | **이종 엔진 CSV 칼럼 중복 + DB2 통계 스케일 제로필** — DB2 라틴 소문자화로 CSV 중복칼럼→`_merge_results` 정규화 canonical, DB2 AVG 스케일→최종 CAST DECIMAL(15,2) 고정. |
| 2026-07-13 | D-068 6차 | **재오염 자기강화 루프 차단(등록 가드) + 직접 컬럼 교정 확장** — 서버명→hostname 자동등록을 세 등록함수 전부 거부(`is_servername_to_hostname`), 직접 hostname 컬럼도 교정. |
| 2026-07-13 | D-068 5차 | **서버명/서버이름이 EAV Hostname으로 오매핑 → 둘 다 hostname** — 전역 유사어 미끼가 원인, `correct_servername_hostname_mapping` 가드로 `<entity>.name` 교정(단일·멀티). |
| 2026-07-13 | D-068 4차 | **3차 결정적 빌더가 실 런타임에서 발동조차 안 됐음** — 로더가 known_attributes를 문자열 평탄화해 attr_rt 항상 빔 → `known_attributes_detail` 우선 읽어 복원, 실 로더 end-to-end 회귀. |
| 2026-07-13 | D-068 | **폼필 EAV 강제 SELECT의 resource_type 인지 다중 리소스 피벗** — 자식 리소스(CPU코어/메모리) NULL → 2~4차 정정 거쳐 `build_multi_resource_pivot_sql`로 결정적 조립·LLM 우회. |
| 2026-07-09 | D-067 | **재발 방지 드리프트 가드(테스트 전용)** — 프로필 3벌·2경로 SQL생성 중복 불변식 검사(`test_polestar_profile_consistency`·`test_query_gen_parity`), 프로덕션 무변경. |
| 2026-07-09 | D-066 | **단일/멀티 DB SQL 생성 경로 동등화(few-shot 예시·전체조회 LIMIT 공유)** — DB 개수가 경로를 갈라 멀티가 예시·LIMIT 누락→환각 → 공용 `query_gen_common.py`로 단일출처화. |
| 2026-07-09 | D-064 | **텍스트 후속 턴의 폼필 요청-스코프 상태 누수 차단** — 체크포인터 델타병합으로 옛 template/mapped_db_ids 잔존 → `create_followup_input` 트리거 초기화+field_mapper 스킵 자기정리. |
| 2026-07-09 | D-065 | **바 "공동존" 위치 DB 라우팅(gp+yd)** — 단독 "공동존" alias 부재로 b0 오라우팅 → gp/yd aliases에 "공동존" 추가+`_ensure_location_hints` 가드(전용 분기 미채택). |
| 2026-07-01 | D-048 | **Phase E4 — LLM 액션가능성 판단(피드백 few-shot, ML 모델 미사용)** — 운영자 피드백 few-shot→`llm_actionability` 자문, 결정은 결정적 policy step9(승격우선·sev3 PAGE 불변), 옵트인. |
| 2026-06-30 | D-049 | **ack/incident 라이프사이클 계측 — PostgreSQL 단일 저장소 (Plan 52 §9.1)** — ack(API)·incident(워커) 2프로세스 → PG 단일저장소+Redis 브리지, MTTA/MTTR/전환율, 옵트인·UI 카드/admin 탭. |
| 2026-06-30 | D-048 | **E3 후속 — 워커→UI 실시간 SSE Redis pub/sub 브리지 (D-048.10)** — cross-process 티어 결정이 UI 미표시 → `sse_bridge.py` pub/sub 재팬아웃, 옵트인·회귀 0. |
| 2026-06-30 | D-048 | **Phase E3 — AI 메시지 심각도 보강(상향 전용) + 억제 매트릭스 강등 + TICKET 일배치 큐 + 메타모니터링/운영지표** — 상향전용 결합, sev1×낮음 DASHBOARD, TICKET 큐, 메타알림, 옵트인. |
| 2026-06-29 | D-048 | **Phase E2 — 연쇄/인히비션/플래핑/스톰 억제** — 결정 파이프라인 step4~7 결정적 규칙(의존성·인히비션·플래핑·스톰), sev3 PAGE 불변, 옵트인, 키 만료 sweep. |
| 2026-06-29 | D-048 | **noise_context graceful degradation 부분 반환 교정** — resource·noti SQL을 한 try에 묶어 한쪽 실패에 전부 unavailable → 독립 try/except 분리, 회귀 테스트 4종. |
| 2026-06-29 | D-048 | **알람 노이즈 캔슬링 4-티어 발송 게이트 (Plan 52 E1 MVP)** — 결정적 PAGE/TICKET/DASHBOARD/SUPPRESS 라우팅, sev3 절대 PAGE·억제≠삭제, 옵트인. 번호 D-040→D-048 정정. |
| 2026-07-08 | D-061 | **은행존(b0) 서버명(등록명)·호스트명·IP 구분** — name/hostname 둘 다 등록명 출력 → column_synonyms 추가·EAV Hostname synonyms에서 "서버명" 제거, 호스트명/IP는 직접 컬럼. |
| 2026-07-07 | D-060 | **오케스트레이터 vLLM SSL 검증 토글 — 인증서 없는 443 엔드포인트 대응** — SSL 검증 실패로 semantic_router 오폴백 → `verify_ssl` 노브(health+ChatOpenAI 양경로 httpx verify=False). |
| 2026-07-02 | D-059 | **폼필 실패 시 침묵적 CSV 강등 금지 — 사유 노출** — `_generate_document_file` None 반환으로 원인 은닉 → `{"reason":...}` 반환, output_generator가 사유 결정적 노출. |
| 2026-07-02 | D-058 | **공동존(gp/yd) 서버 식별자 NULL 폴백** — `cmm_resource.name` 전부 NULL로 서버 식별 불가 → 식별 컬럼·값필터를 `COALESCE(name,hostname)`로. |
| 2026-07-02 | D-057 | **폼필(멀티DB) SQL 생성의 엔진·스키마 인지 — b0(DB2) POLESTAR 한정** — b0 무스키마→SDQ000 오해소 → `DBDomainConfig.db_schema` 단일출처, `_generate_sql`이 스키마+DB2 방언 주입. |
| 2026-07-01 | D-056 | **멀티턴 후속 판단형 질의에 직전 턴 답변 전파(Approach A, ①②④)** — 판단 턴이 "데이터 없음" 거부 → 종료노드 AIMessage 누적·general_inference에만 이력 전달·context 그라운딩(다수 후속 보강). |
| 2026-07-01 | D-055 | **후속 턴 "해당 서버" 지시어 hostname 오추출 차단 + 결과 요약 유지 정정** — 지시어를 hostname 오추출 → `_is_demonstrative_value` 가드로 배제·previous_entities 폴백, 규칙6 요약 유지 단서. |
| 2026-06-30 | D-054 | **레거시 `polestar` 도메인 폐기 + db_profiles 정리 + 면책 문구 환각 차단** — DB_DOMAINS 7→6, 스텁 프로필 삭제, output_generator 규칙6(묻지 않은 컬럼 면책 환각 금지). |
| 2026-06-30 | D-053 | **은행 b0 실시간 프로세스 조회 0건 수정(2건)** — `_LOCATION_DB_HINTS`에 b0 누락(db_id=None)·`build_hostname_sql` DB2 방언 미분기·멀티턴 db_id 미승격 3중 수정. |
| 2026-06-30 | D-051 | **allowed_tables 유사어 동적 보완을 질의 매칭분으로 게이트** — 무조건 전 테이블 추가로 system_prompt 104K 초과 → 질의 매칭 유사어 테이블만(상한15) 보완. |
| 2026-06-29 | D-050 | **단일 서버 필터 + EAV 피벗(CPU·메모리) 조회 SQL 교정(HAVING 패턴)** — 서버필터를 WHERE로 붙여 Cpus/Memory 행 제거→NULL → 식별 필터를 HAVING(집계 후)으로. |
| 2026-06-29 | D-063 | **엔티티 확보 후 무의미 재시도 차단(replanner 필드-null 재조회 가드)** — 필드 null을 0건으로 오판해 헛 재시도 → `_filter_futile_retries`가 확보 후 같은의도 재조회 제거(행수+독립성 판정). |
| 2026-06-29 | D-062 | **딥 에이전트 경로 복합 결과의 단일 LLM 합성(모순 이중 답변 제거)** — 재시도 부분성공이 "없음↔있음" 모순 이어붙임 → `result_aggregator(synthesize=True)` LLM 1회 합성(트랙 A·B 둘 다). |
| 2026-06-29 | D-047 | **프로세스 결과: 채팅 상위 N + CSV 전체 다운로드(사용자 결정)** — 상위 N만 남기고 전체 폐기·query_results 미승격 → 전체 정렬 후 organized=상위N/query_results=전체 top-level 승격. |
| 2026-06-29 | D-047 | **프로세스 조회 대상 서버 식별자 추출 + 실시간 라우팅 결정적 교정** — "### 서버 프로세스"가 식별 실패·환각 → input_parser 규칙14 hostname 추출+`_coerce_process_intent` 라우팅 교정. |
| 2026-06-26 | D-046 | **프로세스 조회 시 서버명 → 호스트명 해소 (process_query)** — API 키는 hostname인데 서버명 질의로 0건→DB 환각 → `polestar_hostname_resolver.py`로 API 호출 전 정규 hostname 해소. |
| 2026-06-26 | D-045 | **스트리밍 마크다운 비파괴 렌더(DOM 모핑) — 표 가로 스크롤·텍스트 선택 보존** — 토큰마다 innerHTML 재생성으로 스크롤 리셋 → 자체 경량 morph 구현+rAF 코얼레싱. |
| 2026-06-26 | D-044 | **스트리밍 응답 조건부 자동 스크롤(stick-to-bottom) + 맨 아래 이동 플로팅 버튼** — 무조건 scrollToBottom으로 화면 튐 → stick-to-bottom 상태추적(임계24px)+플로팅 버튼, 프론트 전용. |
| 2026-06-26 | D-043 | **재조회(대체) 후속 task의 1차 시도 결과 본문 숨김 (supersedes)** — 재조회 성공에도 1차 실패 서술 이어붙여 모순 → `supersedes` 필드+`_collect_superseded`로 본문 한정 숨김(현황은 유지). |
| 2026-06-24 | D-039 | **orchestration 처리 현황에 생성 SQL·대상 DB·DB 에러 노출 (관찰성 보강)** — orchestration 경로가 SQL 미노출 → pipeline 결과에 generated_sql/target_db_ids/db_errors 추가·프론트 렌더. |
| 2026-06-23 | D-040 | **replanner 과(過)재계획으로 인한 일반 안내 답변 중복 출력 수정** — 텍스트 300자 절단·general_inference 후속 반복 → 상한 1500자+신규 task 전부 general_inference면 종료 가드. |
| 2026-06-23 | D-039 | **다중 의도 처리 현황 표시 + 본문 작업 라벨 제거 (Plan 49 §3.6/§5 step 7)** — 4개 오케스트레이션 노드 SSE 미표시 → 화이트리스트+`_extract_node_progress` 분기·replan_history 보존·본문 라벨 제거. |
| 2026-06-23 | D-038 | **사용법/지원 소스 안내 — general_inference 그라운딩 + 도움말 버튼** — active∩allowed DB+도메인 설명을 코드로 조립해 그라운딩, `❓ 사용법` 버튼, 못 쓰는 소스 광고 차단. |
| 2026-06-17 | D-037 | **테스트용 워커 provider override 추가 — deepagent 경로 전체 gemini 검증 (Plan 49 §4.7)** — 운영 FabriX 유지·테스트만 gemini 검증 토글(`create_llm(provider_override=)`+`worker_provider_override`). |
| 2026-06-17 | D-037 | **deepagents 0.6.10 실제 설치 + step6(도구 결과→FabriX 재정리) 실측 구현 (Plan 49 §4.3)** — 실 설치로 표면 실측(`system_prompt`·ToolMessage), collector로 원본 결과 수집→FabriX 최종화. |
| 2026-06-17 | D-037 | **트랙 B 런타임 그래프 배선 완료 (Plan 49 §7 step 7)** — `build_deep_agent`를 실행 경로에 배선(`field_mapper→deep_agent→END`), 미설치 시 semantic_router 2중 폴백, 기본 무변. |
| 2026-06-17 | D-037 | **실제 deepagents 패키지 도입 결정 (Plan 49 개정 — 트랙 B 재진입)** — 트랙 B 제거 번복, vLLM(Qwen3.5-9B)이 deepagents 구동·FabriX가 실질 응답처리, 백엔드=vLLM 가용성 옵션. |
| 2026-06-16 | D-037 | **Phase 2 구현 완료 (Plan 49 — 결과 기반 동적 재계획 + 진행 추적)** — `replanner.py`(결과 평가·후속 task 증분 append·max_replan 가드), 조건부 루프 엣지, SSE progress 보류. |
| 2026-06-16 | D-037 | **Phase 1 보강 — 라우팅 신호 보존 구현 (Plan 48 §4.9.6/§8.1, R-14)** — planner→classify_dbs 분리로 위치→DB 신호 소실 → 보존 규칙+db_descriptions 주입+sub_query_context SQL 입력. |
| 2026-06-16 | D-037 | **기본값 전환 + Phase 2 계획 착수** — `enable_deepagent_orchestration`를 tri-state None(멀티DB 기본 동작)로, Plan 49 상세 계획 작성. |
| 2026-06-16 | D-037 | **Phase 1 구현 완료 (Plan 48 §8)** — 신규 `src/orchestration/`(intent_planner·agent_orchestrator·result_aggregator·subagents), 패턴①독립병렬·②의존순차, 트랙 A, 기본 False. |
| 2026-06-16 | D-037 | **모호성 명료화 인터럽트(Clarification HITL) 추가 (Plan 48 §4.11)** — 모호 시 선택지 되묻는 멀티턴 인터럽트(트랙 A), Phase1=슬롯 예약/Phase4=구현, MAX_CLARIFY 가드. |
| 2026-06-16 | D-037 | **deepagents 기반 의도 분해 오케스트레이션 계획 확정 (Plan 48)** — 단계적 하이브리드(패턴 자체구현/격리 PoC), 복합의도 분해+순차·병렬, deepagents 직접도입 회피. |
| 2026-06-16 | D-036 | **알람 영향 프로세스 보강 (Plan 47-1)** — CPU/메모리 알람에 폴스타 실시간 프로세스 API를 hostname으로 조회·상위N 마스킹 근거 추가, args 민감정보 마스킹 필수. |
| 2026-06-11 | D-035 | **알람 이력 기반 패턴 분석 (Plan 47)** — 폴스타 DB 직접조회(고정SQL 90일)+통계 순수함수+enricher 노드(Redis 캐시·graceful), is_clear=severity=0 단독 기준 정정. |
| 2026-06-11 | D-034 | **주기적 헬스체크 로그 노이즈 감소** — httpx 로거 WARNING 상향, 연결 성공·종료 로그 INFO→DEBUG, 실패 경로 로그 유지. |
| 2026-06-11 | D-033 | **처리 현황 유사어 매핑 표시** — `synonym_usage.py` 신규(SQL 리터럴 역조회+미등록 감지), query_generator 반환·SSE/UI 렌더링. |
| 2026-06-11 | D-033 | **일반 컬럼 매핑 대량 출력 수정** — bare 컬럼명 그룹화·중복 제거, 사용자 용어 매칭 항목만, 상한 15건. |
| 2026-06-11 | D-009 | **처리 현황 schema_analyzer "스키마 요약" 제거** — 중첩 구조 오독으로 정상 출력된 적 없는 버그성 표시를 백엔드/프론트/명세 일괄 제거. |
| 2026-06-09 | D-032 | **폴스타 알람 메시지 포맷 확정 + AlarmEvent 필드 재설계 (Plan 46 개정)** — 단일행 JSON 템플릿, 구 필드 제거·신 필드 추가, alarm_worker._process 재작성. |
| 2026-06-04 | D-031 | **알람 소켓 수신 → LLM 분석 → worKB 발송 (Plan 46)** — alarm_server 독립 프로세스, AlarmWorker Redis Stream 소비, 2-노드 AlarmAnalysisGraph, lifespan 등록. |
| 2026-06-01 | D-030 | **ALARMSEVERITY=0 해소 상태 이력 쿼리 포함 (Plan 45)** — 4개 도메인 0=해소 추가, C-1~C-5 CASE WHEN 0·WHERE IN(0,1,2,3), C-4 해소_수 집계. |
| 2026-05-29 | D-029 | **알람 조회 의도 분리 (Plan 44)** — routing_intent="alarm_query" 추가, semantic_router 규칙+예시 7건, query_generator Template C-1~C-5. |
| 2026-04-02 | D-028 | **Polestar 불필요 lookup 테이블 JOIN 차단 (Plan 42)** — excluded_join_columns에 vendor_id/os_id/os_param_id, allowed_tables 필드, schema_analyzer 필터·validator 패턴3. |
| 2026-04-02 | D-027 | **사용자 행위 감사 로깅 강화 (Plan 40)** — JSONL+PostgreSQL 이중 기록, 통합 AuditService·AuditMiddleware, 10개 이벤트 유형, 보안 경고 자동 감지. |
| 2026-04-01 | D-026 | **사용자 로그인 및 인증 시스템 (Plan 39)** — auth/user 도메인·repository·routes 신규, AuthConfig, 감사 테이블, UI login/register/admin. |
| 2026-03-30 | D-025 | **3계층 하이브리드 필드 매핑 전파 정합성 (Plan 38)** — column_matcher·column_resolver 신규, resolved_mapping State, result_organizer/output_generator/writer 계층 통합. |
| 2026-03-30 | D-024 | **Synonym 통합 관리 + EAV 접두사 비교 정규화 (Plan 37)** — EAV synonym global 통합, normalize_field_name, 스키마 조회 시 synonym 자동생성, EAV 접두사 처리. |
| 2026-03-30 | D-023 | **데이터 충분성 검사 로직 개선 (Plan 36)** — mapping_sources 기반 차등 임계값, _match_column_in_results/_classify_mapped_columns 추출, sufficiency 임계값 추가. |
| 2026-03-26 | D-022 | **Plan 33 보강: 3중 방어 + 사후 감지** — excluded_join_columns YAML·시스템 프롬프트 규칙10·스키마 JOIN 금지 주석·validator ON 절 감지, `schema_utils.py` 신규. |
| 2026-03-26 | D-022 | **RESOURCE_CONF_ID JOIN 금지: hostname 브릿지 조인 필수화** — SQL 패턴·구조 분석 프롬프트·조인 힌트 로직을 value_joins 우선으로 변경. |
| 2026-03-26 | D-011 | **캐시 유효성 검증 및 무효화 정합성 개선 (Plan 30)** — 저장 전 유효성 게이트, invalidate 정책 변경(DB별 삭제·글로벌 보존), stale 자동정리, 인메모리 버퍼. |
| 2026-03-25 | D-021 | **Gemini API 프로바이더 추가 + .encenv 민감 키 분리 (Plan 28)** — LLMConfig.provider gemini, _create_gemini 팩토리, .encenv 도입, langchain-google-genai optional. |
| 2026-03-25 | D-019 | **Fingerprint TTL 기반 Redis 캐시 최적화 (Plan 26)** — fingerprint_ttl_seconds·fingerprint_checked_at 키, 2차-A/B 캐시 분기, multi_db_executor SchemaCacheManager 통합. |
| 2026-03-24 | D-018 | **LLM 지능형 필드 매핑 (Plan 22)** — LLM 통합 추론(synonyms+descriptions), 즉시 Redis 등록, 매핑 보고서 MD 생성/파싱·수정/업로드, API 2개, 프론트 UI. |
| 2026-03-24 | D-009 | **Plan 23 UI 수정** — SSE 인디케이터, 스트리밍 다운로드 버튼, Fallback Progress Panel, thread_id 전달, URL encodeURIComponent 보안 강화. |
| 2026-03-24 | D-017 | **EAV Field Mapper 전체 파이프라인 지원** — _apply_eav_synonym_mapping·2.5단계 매핑·EAV: 접두사 규약·프롬프트 가이드·_validate_mapping EAV 검증·피벗 힌트. |
| 2026-03-24 | D-016 | **EAV 비정규화 테이블 쿼리 지원** — polestar_patterns.py 신규, schema_analyzer 자동감지, query_generator Polestar 가이드, DB 엔진별 LIMIT, validator DB2 대응. |
| 2026-03-23 | D-015 | **Excel→CSV 변환 LLM 컨텍스트 보강** — CsvSheetData, excel_to_csv(), 시트별 순환 LLM 호출, field_mapper 예시 데이터 프롬프트. |
| 2026-03-19 | D-014 | **자체 MCP 서버 구축** — mcp_server 독립 패키지, SSE transport, DB2 지원, DBHubConfig/QueryConfig/MultiDBConfig 재설계. |
| 2026-03-18 | D-013 | **Phase 3 멀티턴 대화 + Human-in-the-loop 구현** — context_resolver·approval_gate·synonym_registrar 노드 신설, 체크포인트 State 복원, API 멀티턴 지원. |
| 2026-03-17 | D-012 | **매핑-우선(Mapping-First) 전략 도입** — field_mapper 노드 신설, 3단계 매핑, 유사어 등록 플로우. |
| 2026-03-17 | D-007 | **Phase 2 구현 완료** — Excel/Word 파싱, LLM 의미 매핑, 문서 생성. |
| 2026-03-17 | D-008 | **Phase 2 진행 상태 업데이트: 완료** |
| 2026-03-17 | D-009 | **사용자 UI 채팅 인터페이스 + SSE 스트리밍 결정 추가** |
| 2026-03-17 | D-007 | **멀티시트 독립 매핑 지원 추가** — 시트별 필드 매핑, target_sheets 필터링. |
| 2026-03-17 | D-004 | **v1(키워드+LLM) → v2(LLM 전용)로 개정** — 키워드 기반 분류 완전 제거. |
| 2026-03-17 | D-004 | **사용자 직접 DB 지정 기능 추가 (aliases 필드)** |
| 2026-03-17 | D-005 | **멀티 DB 결과 병합 시 `db_result_summary` 생성 추가** |
| 2026-03-17 | D-010 | **3단계 스키마 캐싱 결정 추가** — 메모리→파일→DB, fingerprint 변경감지. |
| 2026-03-17 | D-011 | **Redis 기반 스키마 캐시 + LLM 컬럼 설명/유사 단어 구현** |
| 2026-03-18 | D-011 | **유사단어 2계층(DB별+글로벌)** — source 태깅, invalidate 보존, 프롬프트 기반 synonym CRUD 완성. |
| 2026-03-18 | D-011 | **글로벌 유사단어 description 확장** — synonyms:global value를 {words, description}로 확장, update-description action, list-synonyms description 표시. |
| 2026-03-18 | D-011 | **프롬프트 기반 글로벌 유사 단어 LLM 생성** — generate-global-synonyms action, seed_words 지원, 기존 항목 merge. |
| 2026-03-18 | D-011 | **Smart Synonym Reuse** — 새 필드 추가 시 LLM 유사 컬럼 탐색·재활용 제안, pending_synonym_reuse State, reuse/new/merge 모드. |
| 2026-03-25 | D-020 | **LLM 기반 범용 스키마 구조 분석** — Polestar 하드코딩 제거, LLM+HITL 기반 DB 구조 자동 감지. |
| 2026-03-17 | 전체 | **초기 decision.md 작성** |
