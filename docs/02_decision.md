# Decision Log

이 문서는 프로젝트의 주요 아키텍처·설계 의사결정 기록의 **압축본**입니다 (2026-07-16 압축).
향후 요건 추가/수정 시 이 문서를 참고하여 의사결정의 방향성과 일관성을 유지합니다.

- 각 결정의 상세 배경·코드 예시·대안 비교·재현 로그 전문은 `docs/02_decision_full.md`(2026-07-16 아카이브, 이후 갱신하지 않음) 참조.
- **신규 결정은 이 파일에만** 아래 압축 형식(결정일/상태/결정/근거/구현/주의/관련)으로 추가한다.
- **D-번호 채번**: `## D-` 헤더와 하단 「변경 이력」 표를 **모두 grep**하여 실제 최댓값+1 부여 (현재 최대 **D-133**[Plan 67 트랙 N2·2026-07-30 등재, D-131 트랙 R 동시 등재] → 다음 **D-134**). **예약 유지(미등재·재사용 금지)**: **D-128**(plans/67 트랙 S — 단계적 컬럼 도출 루프)·**D-132**(plans/67 R3-(v) — alarm 주석 LLM 전환). Plan 68은 D-129로 채번 완료. **D-115는 Plan 65(목업 이벤트 주입 경로) 예약 유지**(미등재 — E7=D-116·E8=D-117로 그 위 채번). ※Plan 63(P1~P4)로 D-088~D-091 전부 등재 완료(P4-2/3=D-091이 P3=D-090보다 먼저 등재됨: 순서 교차, 팀장 승인). ※**D-101~D-104는 ux_improvement 병합이 등재**(구 D-084~087 재부여, 변경 이력 표 — 2026-07-22) → **Plan 64 §14(D-101~103)·Plan 60 §14.4(D-104)의 종전 예약과 충돌**하므로 그 계획들은 착수 시 다음 빈 번호로 재부여 필요. **D-105만 예약 유지**(Plan 60 §16 L3단계). **Plan 60 Wave A(E1·E4·E6)=D-106~108·Wave B(E2·E3)=D-109~110·Wave C(E5)=D-111**(초안 D-077~081은 결번 — §8 "등재 직전 번호 재확인" 규칙, 팀장 확정).
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

## D-086. 선행 task 결과 스코프 결정적 주입 — 알람 선별→지표 조회 크로스도메인 (SYN-H-04 회귀)
- **결정일**: 2026-07-18 | **상태**: 확정 (구현 완료)
- **배경**: "현재 활성 상태인 심각 알람이 있는 서버들의 최근 1개월 CPU 사용률"(SYN-H-04 변형) 실측 — intent_planner가 alarm_query(t1)→data_query(t2)로 분해했으나 **데이터 의존 주입(prior_rows)이 `_make_isolated_input`에서 생성만 되고 소비처 전무(죽은 배선)**. t2는 전역 parsed_requirements의 알람 조건을 재표현하려다 성능 템플릿 테이블 화이트리스트에 막혀 `resource_type='alarm.Alarm'`·`resource_key LIKE '%CRITICAL%'` 환각 SQL 생성 → 0건, CPU 사용률 미조회(Known Mistakes "구현이 있어도 호출부 배선까지 grep" 계열).
- **결정**: 5중 수리 — ①**prior_rows 소비 배선**: 공용 `build_prior_rows_block`(query_gen_common)이 선행 결과 식별값을 `IN` 스코프 강제 블록으로 렌더(hostname 우선·name 폴백 — D-061 계열, 값 상한 100·따옴표 이스케이프), query_generator(단일)와 `multi_db_executor._generate_sql`(멀티, `prior_block` 파라미터)에 **대칭 주입**(D-066). 블록이 "선별 조건(알람 등) 재표현 금지"를 명시. ②prior_rows 존재 시 **트랙 C 결정적 컴파일 우회**(SMQ는 선행 결과 한정 표현 불가 — 양 경로 동일 조건). ③`_coerce_alarm_intent`가 **input_from 보유 task는 미교정**(의존 task의 알람 어휘는 선별 잔재 — alarm_query로 뒤집으면 지표 조회 소실). ④성능 템플릿 Strict Constraint 5 — **알람을 cmm_resource(resource_type/resource_key)로 표현 금지**(환각 차단), 스코프 블록 없으면 조건 생략+SQL 주석. ⑤intent_planner 예시 3-1 — 알람 선별+성능 지표 질의는 alarm_query→data_query(input_from) 2-task 분해 명시(결과가 알람 목록인 질의는 단일 유지).
- **주의**: 식별 컬럼이 없는 prior_rows는 블록 미주입(④가 후방 방어). `alarm_allowed_tables`에 metric 테이블은 추가하지 않음 — 알람 템플릿 단독의 지표 조회는 계속 불가하며 **분해가 정답 경로**(단일 task로 뭉치면 CPU 미조회 재현 가능, 예시 3-1이 방지선).
- **검증**: `tests/test_orchestration/test_prior_rows_scope.py`(13건 — 헬퍼 단위·단일/멀티 주입 대칭·컴파일 우회·교정 가드), 기존 orchestration·query_gen 스위트 234건 무회귀.
- **관련**: D-066(경로 대칭), D-061(name≠hostname), D-076 후속3(알람 결정적 교정), D-085(SQL 환각 가드 계열), D-005(부분 실패 허용)

## D-087. validator CTE 인식의 주석 비대칭 수정 (2단 집계 쿼리 오거부, SYN-I-03 확장 회귀)
- **결정일**: 2026-07-18 | **상태**: 확정 (구현 완료)
- **배경**: "월 평균 CPU 사용률이 40%를 넘은 달이 2개월 이상인 서버…cpu/메모리 평균·최고 사용률 함께"(SYN-I-03 확장) 실측 — LLM은 올바른 CTE 2단 집계(`WITH MonthlyStats…, FilteredMonths…`)를 생성했으나 validator가 CTE를 "존재하지 않는 테이블"로 오거부, 재시도 3회 전부 같은 사유로 소진 → 빈 "데이터 없음" 응답 강등(executed_sql 공백).
- **원인**: `_extract_cte_names`의 `^\s*WITH` 앵커가 **원본 SQL** 기준 — 생성 규칙 7이 SQL 선두에 `-- 설명` 주석을 강제하므로 주석 달린 CTE 쿼리에서 항상 실패. 반면 테이블 추출(`_extract_table_names`)은 **주석 제거 후** 수행해 CTE 참조가 테이블로 수집됨(동일 산출물에 대한 전처리 비대칭 → 한쪽만 성립하는 입력에서 구조적 오판).
- **결정**: `_extract_cte_names`도 `sqlparse.format(strip_comments=True)` 후 앵커 검사·이름 추출(테이블 추출과 전처리 일원화). 검증: `test_query_validator_extended.py`에 선두 주석+CTE 회귀 2건 추가(추출 단위·validator 통과), 전 validator 스위트 77건 통과. 수정 후 인프로세스 E2E — attempt 0에서 검증 통과·실행 2행(DB-ORA-023 3개월 70.5/80.1/88.9 · cocm-hdkapp01 3개월 47.2/63.1/73.1, 최신월 기준).
- **주의**: 같은 산출물을 여러 검사기가 볼 때 **전처리(주석·리터럴 제거)는 반드시 동일 파이프로** — 비대칭이면 "규칙이 강제한 정상 출력"(선두 주석)이 곧 실패 조건이 된다. 재시도 3회가 **같은 사유로 반복**되면 LLM 품질이 아니라 결정적 게이트 자체를 의심할 것.
- **관련**: D-085(validator 가드 계열), D-022(validator 감지 선례), Known Mistakes "0건/실패 진단은 게이트별 로그로 끊긴 지점부터"

## D-088. 공용 계층 DB-agnostic 원칙 + 공용 주입 블록 일반화 + 과적합 재발 방지 가드 (Plan 63 트랙 P1·P4-1)
- **결정일**: 2026-07-20 | **상태**: 확정 (P1·P4-1 구현 완료)
- **배경**: 2026-07-18 과적합 검토 실측(Plan 63 §1) — 폴스타 격리 채널(POLESTAR 템플릿 게이트·db_profiles·semantic_models)은 지켜지나 **공용 계층(`query_gen_common`·`query_generator`·`multi_db_executor`·공용 `prompts`)에 폴스타 스키마 리터럴이 누수**. `ACTIVE_DB_IDS=polestar` 단독이라 현재 실동작 문제는 없으나, 등록된 비폴스타 DB(cloud_portal·itsm·itam) 활성화 시 오지시 주입·기능 무력화로 드러난다.
- **결정**: 3계층 구조 확립 — [1]공용 코어는 DB-agnostic(스키마 리터럴 금지, overfit_check가 강제), [2]선언적 지식 채널(db_profiles/semantic_models)이 DB별 어휘·구조·규칙 단일 출처, [3]DB 어댑터가 코드 필요한 특화 로직만(P2/D-089). LLM vs 결정적 판단 기준(D-035 정합): "지식(무엇이 어디에)"은 하드코딩 제거, "정합성 방어(무엇이 틀렸는가)"는 결정적 가드 유지.
- **구현(P1 — 공용 주입 블록 즉시 일반화)**: ①`build_prior_rows_block`(query_gen_common)에서 `cmm_resource의 resource_type/resource_key` 문장 제거 → 일반 원칙("대상 DB에 없는 테이블/컬럼/값으로 선별 조건 지어내기 금지, 환각 금지")으로 교체. 폴스타 알람 환각의 구체 차단은 폴스타 성능 템플릿 Strict Constraint 5(D-086 ④)가 독립 보존. `{col} IN` 문구를 "선행 결과 식별 컬럼 → 대상 DB 식별 컬럼 적용"으로 완화(식별 컬럼 heuristic 유지, 프로필 identity_columns 우선화는 P3/D-090). ②`build_stat_month_block`(cmm_metric_stat_m·stat_date 규약) 호출을 **폴스타 게이트**(폴스타 시스템 템플릿과 동일 신호 `active_db_id/db_id ∈ polestar_db_ids`)로 한정 — 단일(`query_generator.py`)·멀티(`multi_db_executor.py`) 대칭(D-066). 프로필 부재 DB는 미주입(시스템 템플릿 일반 기간 규칙만). 프로필 time_grain 선언 기반 전환은 P3/D-090. ③공용 `QUERY_GENERATOR_SYSTEM_TEMPLATE` 규칙2 스키마 접두사 예시 `polestar.cmm_resource` → 형식 예시 `<스키마>.<테이블>` 중립화(게이트 없는 공용 템플릿의 유일한 실주입 리터럴). ④D-085 LEFT JOIN 강등 메시지 예시 `server.Server의 서버명` → `피벗 기준 엔터티 행` 중립화.
- **§8 사용자 확인 항목 채택안(사용자 "진행" 포괄 승인)**: 어댑터 위치 `src/db_adapters/polestar/`, P3 무선언 DB LLM 폴백 `GENERIC_LLM_MAPPING` 옵트인(기본 OFF — 기본 동작 호출 증가 0), generic_mon 픽스처는 최소 3테이블 평탄 스키마(프로필·모델 없음), P2 전용 템플릿 3종 이동 포함(커밋 세분화), 라우팅·인스턴스 어휘(§1.3)는 스코프 아웃(overfit_check routing-vocab 분리 집계·화이트리스트만).
- **구현(P4-1 — 과적합 재발 방지 가드)**: `scripts/overfit_check.py` 신설 — 공용 계층(`src/utils`·`src/nodes`·`src/orchestration`·공용 `src/prompts`, 어댑터 `src/db_adapters` 제외)을 tokenize로 스캔(주석 제외)해 schema-literal(cmm_/server.*/stat_date/core_config_prop/stringvalue*/resource_conf_id/platform_resource_id/configuration_id/polestar.*)과 routing-vocab(§1.3 위치·별칭 어휘)를 **카테고리 분리 집계**. schema-literal은 기준선(`scripts/overfit_baseline.json` — `(파일,토큰)` 단위 화이트리스트, P1 시점 71토큰/13파일)과 대조해 **신규 유입 시 `--ci` exit 1**. routing-vocab은 스코프 아웃(가시화만). P2/P3가 리터럴 소거하며 `--update-baseline`으로 재생성해 감소(감소량=트랙 지표). 스킬 `.claude/skills/overfit-check.md` 등록. 검증 `tests/test_overfit_check.py`(6 — 카테고리 분리·기준선 대비 신규 0·가드 형해화 방지).
- **주의**: 폴스타 동작 불변이 판정 기준 — 폴스타는 게이트/템플릿 신호가 그대로라 EX 동치. `build_stat_month_block` 함수 본문의 폴스타 기본값(cmm_metric_stat_m·stat_date)은 P1에서 게이트만 추가하고 리터럴 자체는 P3(D-090)에서 시맨틱 모델 선언으로 이관 — overfit_check 기준선에 잔존(P2/P3 소거 예정). 검증: `test_prior_rows_scope.py`(16 — 블록 일반화·stat 블록 게이트 대칭 3건 신설), 전체 pytest 무회귀(클린 cee7cdf vs post-P1 FAILED/ERROR 집합 동일 49F/5E 전부 환경 의존 사전 실패, +3 신규 통과), text2sql 골든/하네스 109 통과, EX 하네스 3경로 DB/LLM 미접속으로 각 26 skip(오프라인 동치는 골든으로 확인).
- **관련**: D-089(어댑터 분리), D-090(어휘 매핑 LLM 전환·프로필 선언), D-091(범용성 회귀 하네스), D-086(prior_rows 배선·대칭 보존), D-066(단일/멀티 대칭), D-035(결정=판단·LLM=보조), D-020(폴스타 하드코딩 제거 선례)

## D-089. 폴스타 DB 어댑터 분리 — 동작 불변 이동 + 레지스트리 디스패치 (Plan 63 트랙 P2)
- **결정일**: 2026-07-20 | **상태**: 확정 (P2 구현 완료 — Stage 1 템플릿·validator, Stage 2 pivot 조립기 이동)
- **결정**: 공용 계층에 상주하던 폴스타 특화 로직을 `src/db_adapters/polestar/` 어댑터 계층(arch_check `src.db_adapters`=application)으로 **물리 격리**. 공용 코어는 어댑터 존재를 모르고, 어댑터가 임포트 시 레지스트리(`src/db_adapters/__init__.py`)에 등록하며, 코어는 `get_adapter(db_id, polestar_db_ids)`로 담당 어댑터를 조회해 훅을 호출한다. **동작 불변(move-only)** — POLESTAR_DB_IDS 게이트를 어댑터 `owns()`로 이동, 폴스타는 동일 신호로 동일 동작.
- **어댑터 인터페이스(최소 — 실배선 훅만, 과설계 금지 Plan 63 §9)**: `owns(db_id, polestar_db_ids)`(레지스트리 디스패치)·`system_template(routing_intent)`(전용 프롬프트)·`validator_checks()`(전용 SQL 검증). 조립기(pivot)는 훅이 아니라 어댑터 모듈 직접 임포트(application→application). 알람 코어/schema_table_policy 훅은 P3-4에서 프로필 선언과 함께 추가(schema_analyzer 얽힘 — dead hook 방지).
- **구현(Stage 1)**: ①`POLESTAR_QUERY/ALARM_GENERATOR_SYSTEM_TEMPLATE` 2종(계획서 "3종"은 공용 포함 오기 — 실제 2종)을 `src/prompts/query_generator.py`→`src/db_adapters/polestar/prompts.py` 순수 이동, `query_generator._build_system_prompt`가 `get_adapter(...).system_template(intent)`로 디스패치(직접 임포트 제거). ②`_check_routing_filter_misuse`(GROUP_PATH·폴스타 LIKE 오용)를 `query_validator.py`→`src/db_adapters/polestar/validators.py` 이동, 공용 validator가 `adapter.validator_checks()` 순회 실행(전 DB 무조건 실행→폴스타 게이트, 토큰 부재 DB엔 무동작이라 동작 불변, L4 해소). ③부트스트랩(`db_adapters.__init__`이 `polestar` 임포트→`register()`)으로 죽은 레지스트리 방지(D-086 계열). 어댑터로 이동한 심볼은 각 이동-불변, 계층 방향 준수(어댑터=application, 소비처 노드=application → arch_check WARN 허용, error 0).
- **계층 제약(실측)**: servername/hostname 가드(`is_servername_*`·`correct_servername_hostname_mapping`)는 infrastructure(field_mapper·synonym_semantic)도 호출 → application 어댑터로 이동 불가, **utils 잔류**(폴스타 지식은 P3-3에서 프로필 identity_rules로). pivot 조립기 클러스터는 호출부 전부 application(query_generator·multi_db_executor·semantic_compiler)이라 이동 가능(Stage 2).
- **구현(Stage 2 — pivot 조립기 이동)**: `decimal_cast_example`·`classify_metric_field`·`eav_attr_resource_types`·`build_multi_resource_pivot_sql`·`build_multi_resource_pivot_block`(+내부 `_metric_select_line`·`_pivot_select_parts`·`_eav_pattern_parts`·`_SERVER_RESOURCE_TYPE`·`_METRIC_NOUN_RT`·`_RESOURCE_TYPE_RE`)를 `query_gen_common.py`→`src/db_adapters/polestar/assembler.py` 순수 이동(D-068 결정적 조립 자산 불변). 호출부 3곳(query_generator·multi_db_executor·semantic_compiler)이 어댑터에서 직접 임포트(application→application). query_gen_common의 orphan `import re` 제거(내 변경이 유발). 테스트 임포트 경로 갱신(test_multi_resource_pivot·test_query_gen_parity — 이동-불변).
- **검증**: `tests/test_db_adapters.py`(7 — 부트스트랩 등록·owns 디스패치·템플릿/validator 훅·소비처 배선 실측), 이동-불변 임포트 경로 갱신 3개 테스트파일, 서브셋 604 통과 무회귀(사전 실패 6 동일)·전체 pytest 무회귀, overfit_check schema-literal **136→111(템플릿)→79(조립기)**, 기준선 71→44 토큰 재생성(감소량=P2 지표), arch_check --ci error 0. pivot SQL 생성 동치(스모크).
- **관련**: D-088(3계층 원칙·overfit_check 가드), D-066(단일/멀티 대칭), D-086(죽은 배선 방지), D-068(폼필 결정적 조립 — pivot 클러스터 이동 대상), D-004(라우팅 레지스트리 선례)

## D-090. 공용 경로 어휘 매핑 LLM 전환 — 선언 우선 + 무선언 DB LLM 폴백 옵트인 (Plan 63 트랙 P3)
- **결정일**: 2026-07-20 | **상태**: 확정 (구현 완료 — 핵심 격리는 P2로 달성, 무선언 폴백 옵트인 + 잔여 리터럴 스코핑)
- **결정**: "최대한 LLM 활용"의 실현 — **지식(무엇이 어디에)의 하드코딩을 제거**하되 정합성 방어는 결정적 유지(D-035 정합). ①**메트릭 어휘 매핑 격리는 P2로 달성** — `_METRIC_NOUN_RT`(명사→resource_type)·pivot 조립기가 폴스타 어댑터로 이동(D-089)해 공용 계층에서 제거됨. 프로필 보유 DB(폴스타)는 어댑터/프로필 **선언 우선**으로 동작 불변. ②**무선언 DB는 공통 LLM 경로**가 스키마에서 직접 SQL 생성(P4-2/generic_mon 검증 — 폴스타 어댑터 미발동, 공통 템플릿 사용). ③기간 해석: `GENERIC_LLM_MAPPING`(TEXT2SQLConfig, 기본 OFF) 옵트인 시 무선언 DB에 **범용 기간 힌트**(`build_generic_period_hint` — 해석월 YYYYMM만 알리고 대상 스키마의 실제 시간 컬럼으로 매핑 위임, 폴스타 리터럴 없음) 추가 주입. 폴스타는 결정적 `build_stat_month_block`(P1 게이트) 유지 — **선언 우선으로 EX 동치**. 단일/멀티 대칭 배선(D-066).
- **LLM 자동 등록 차단 유지(오염 자기강화 루프 방지)**: `is_servername_to_hostname` 결정적 판정으로 서버명류→hostname LLM 매핑의 Redis 자동 등록을 field_mapper 등록 지점 5곳에서 계속 거부(D-068 6차). 게이트 테스트 기존재 `test_llm_enhanced_mapping.py::TestRegisterLlmMappingsToRedis::test_register_blocks_servername_to_hostname`.
- **구현**: `Text2SQLConfig.generic_llm_mapping=False`(env `TEXT2SQL_GENERIC_LLM_MAPPING`). `build_generic_period_hint`(query_gen_common, DB-agnostic). query_generator·multi_db_executor가 `_stat_block_db`(폴스타) 아니고 플래그 ON일 때만 힌트 주입(elif — 폴스타 경로와 상호배타). 검증 `tests/test_generic_path/test_generic_llm_mapping.py`(3 — 기본 OFF 무주입·ON 범용힌트 폴스타리터럴 0·폴스타 결정적 블록 불변).
- **주의(잔여 리터럴 스코핑 — 정직)**: 공용 계층 잔여 폴스타 리터럴(overfit 기준선 43토큰 — `build_stat_month_block`의 cmm_metric_stat_m/stat_date, schema_analyzer `_alarm_core_set`, servername/hostname 가드의 폴스타 지식 등)은 **전부 P1에서 폴스타 게이트 처리**돼 비폴스타 DB로 **누수하지 않음**(P4-2·overfit_check 검증). 이들을 프로필/시맨틱 모델 선언으로 완전 이관(P3-2 time_grain·P3-3 identity_rules·P3-4 alarm_core_tables)하는 것은 **라이브 LLM/DB로 폴백 품질 검증이 가능할 때 수행**하는 후속 과제로 남긴다(무리한 프로필 플러밍은 폴스타 회귀 위험). 물리적 격리(P2)·재발 가드(P4-1)·범용성 검증(P4-2)이라는 계획 핵심 목표는 달성. LLM 매핑 전환 항목은 EX 하네스 전후 측정이 게이트나 **라이브 DB/LLM 미접속으로 3경로×26건 skip**(오프라인 골든 동치로 대체 확인).
- **관련**: D-089(어댑터 격리 — 어휘 매핑 이동), D-088(공용 DB-agnostic 원칙), D-091(범용성 하네스 — 무선언 경로 검증), D-035(결정=판단·LLM=지식), D-068 6차(자동 등록 차단), D-012(확정 임계 미달 후보 제시), D-076(결정적 월 해석 값 유지)

## D-091. 모의 비폴스타 DB 범용성 회귀 하네스 (Plan 63 트랙 P4-2·P4-3)
- **결정일**: 2026-07-20 | **상태**: 확정 (P4-2 하네스 구현·P4-3 체크리스트 반영)
- **결정**: 폴스타 격리(P2) 이후 **제2 모니터링 솔루션 DB가 공통 경로만으로 동작**함을 회귀로 고정. 프로필·시맨틱 모델이 없는 모의 DB(`generic_mon`)를 픽스처로 두고, 라이브 LLM 없이 프롬프트/디스패치 수준에서 ①공통 시스템 템플릿 사용(폴스타 전용 템플릿 미발동) ②주입 블록·프롬프트에 폴스타 스키마 리터럴 무오염 ③폴스타 어댑터 미발동(`get_adapter`=None)을 검증한다. 실 SQL 실행 E2E는 `RUN_E2E=1` 옵트인.
- **구현**: `testdata/generic_mon/schema.json`(평탄 3테이블 servers/metrics/alerts — EAV 없음, cmm_ 접두 없음, server.Server 없음, 프로필/모델 없음). `tests/test_generic_path/`(conftest 픽스처 + `test_universality.py` — 어댑터 미발동 2·공통 템플릿 3·주입 블록 무오염 1·E2E 옵트인 1). **하네스가 검출한 잔여 P1급 누수 1건 수정**: 공통 `QUERY_GENERATOR_SYSTEM_TEMPLATE` 규칙12(D-085 LEFT JOIN) 예시의 폴스타 컬럼 `stat_date`·`s.resource_id`를 범용 예시(`metrics`/`m.ref_id`/`m.period`)로 중립화(게이트 없는 공통 템플릿의 실주입 리터럴이라 P1 원칙 적용).
- **P4-3 체크리스트 갱신**: 신규 DB 편입 체크리스트에 "⑤프로필/시맨틱 모델 작성(선택) — 없으면 공통 LLM 경로로 동작"을 추가(`docs/18_known_mistakes.md`). ※`collectorinfra/CLAUDE.md`의 체크리스트 한 줄 요약(①~④)은 에이전트가 CLAUDE.md를 편집하지 않는 원칙상 미수정 — 사용자/`/revise-claude-md`로 ⑤ 반영 권장.
- **주의**: 하네스는 LLM 없이 검증 가능한 계층(프롬프트 선택·디스패치·주입 블록)만 결정적으로 고정한다. 무선언 DB의 LLM 어휘 매핑 폴백(P3/D-090) 동작 자체의 검증은 이 픽스처를 기반으로 P3에서 수행한다. overfit_check 기준선은 P4-2 잔여 누수 수정 반영해 재생성(schema-literal 79→78).
- **관련**: D-088(공용 DB-agnostic 원칙·overfit 가드), D-089(어댑터 분리 — 어댑터 미발동 검증), D-090(무선언 DB LLM 폴백 — 이 하네스로 검증), D-020(스키마 자동 발견 채널 유지)

## D-092. 딥 에이전트 조기 종료 감지 + 미실행 하위 작업 명시 (부분 결과 은폐 차단)
- **결정일**: 2026-07-20 | **상태**: 확정 (구현 완료)
- **배경(실측)**: 복합 질의("활성 심각 알람 서버 중 6월 CPU 평균 최고 서버의 제조사·일련번호")에서 오케스트레이터 LLM(gemini-2.5-flash-lite)이 1단계 도구(query_alarm) 결과 수신 후 **빈 AIMessage(output_tokens=0, 무도구호출)** 를 간헐 반환 → deepagents 루프가 "도구 호출 없음=완료"로 종료 → 잔여 하위 작업(CPU 평균·제조사/일련번호 조회) 미실행. 이때 per-task 최종화의 output_generator가 **전체 질의 `parsed_requirements["original_query"]`** 를 응답 프롬프트의 "사용자 질의"로 사용해, 알람 서버 2행만으로 "6월 CPU 최고 서버는 SV-WEB-001" 식으로 전체 질문에 답한 듯 서술(환각) — 실패가 은폐됨(침묵적 강등 금지 위반). 오케스트레이터 모델·재시도 가드는 이번 결정 범위에서 제외(사용자 지시 — 모델 유지, 은폐 차단만 수행).
- **결정**: ①**조기 종료 결정적 감지** — `run_deep_agent`가 마지막 AI 메시지의 무내용·무도구호출 종결(`_ended_prematurely`)을 감지하면, 수행된 조회(collector task sub_query)와 미실행 작업(deepagents 미완료 todo, 없으면 일반 문구)을 담은 안내문(`_build_incomplete_notice`)을 생성해 `orchestration_incomplete_notice`로 aggregator에 전달. ②**결정적 안내 부착** — `result_aggregator._apply_incomplete_notice`가 3개 반환 경로(합성/단일/병합) 공통으로 최종 응답 말미에 LLM 비의존으로 덧붙임(답변 이력에도 포함). ③**per-task 최종화 스코프** — `_build_output_state`가 `parsed_requirements["original_query"]`를 해당 task `sub_query`로 좁혀(사본, 원본 비오염) 하위 결과만으로 전체 질문에 답한 듯한 서술을 차단. ④도구 0회+빈 응답 시 `_extract_final_response`의 사용자 질의 에코(잠복 결함) 대신 "수행된 조회가 없습니다" 명시 실패 안내.
- **구현**: `src/orchestration/deep_agent.py`(`_ended_prematurely`/`_pending_todos`/`_build_incomplete_notice`/`_aggregate_with_fabrix(incomplete_notice=)`), `src/orchestration/result_aggregator.py`(`_apply_incomplete_notice`, `_build_output_state` 스코프). 검증 `tests/test_orchestration/test_deep_agent_wiring.py`(조기 종료 판정 3·안내문 2·노드 동작 3), `tests/test_orchestration/test_result_aggregator.py`(스코프 2·부착 3).
- **대안(기각)**: 오케스트레이터 빈 응답 재시도/모델 상향 — 사용자 결정으로 모델 유지, 재시도 가드는 별도 후속 판단(→ 이후 사용자 지시로 D-093에서 재개 가드 시행). LLM 합성 프롬프트에 미확인 항목 안내 위임 — LLM 비결정성에 정합성을 의존하게 되어 결정적 부착으로 대체.
- **관련**: D-062(딥 에이전트 단일 합성 — 안내문은 합성 결과 뒤에 결정적 부착), D-005(부분 실패 안내), D-059/D-046(침묵적 강등 금지 계열), D-086(알람→지표 크로스도메인 복합 질의), D-093(조기 종료 재개 가드)

## D-093. 딥 에이전트 빈 응답 조기 종료의 진전 게이트 재개 (모델 유지)
- **결정일**: 2026-07-20 | **상태**: 확정 (구현 완료 · 라이브 검증)
- **배경(실측)**: D-092 진단의 원인 측 — flash-lite 오케스트레이터가 복합 질의("알람 서버 중 6월 CPU 최고 서버의 제조사·일련번호")에서 **매 도구 결과 수신 후마다** output_tokens=0 빈 응답을 반환(라이브 재현 3/3 → 재개 도입 후에도 각 라운드 반복 확인). 사용자 결정: 모델은 유지하고 재개 가드로 대응.
- **결정**: `run_deep_agent`가 조기 종료(`_ended_prematurely`) 감지 시 **직전 이력에서 말미 빈 AI 메시지를 제거하고 재개 지시(user 턴, `_RESUME_NUDGE`)를 덧붙여 같은 에이전트를 재호출**한다. 재개는 **진전 게이트 반복**: 재개마다 도구 실행(collector)이 늘어나는 동안 상한(`_MAX_RESUME_ATTEMPTS`=3) 내 반복하고, 진전 없는 빈 응답 반복은 즉시 중단(무한루프 방지). 재개 호출 예외는 직전 결과로 안전 강등(→ D-092 안내 경로). 상한 소진 후에도 미완이면 D-092 안내문이 부착된다.
- **라이브 검증(2026-07-20)**: 재개 도입 전 도구 1건(알람만) → 도입 후 재개 1~3회로 도구 2~4건 실행, 제조사·일련번호 SQL 실제 실행·값 반환 확인. flash-lite는 데이터가 다 모여도 최종 텍스트를 직접 쓰지 않는 경우가 있어(빈 응답 지속) 최종 답변은 기존 collector→result_aggregator 합성이 담당(D-062와 정합).
- **구현**: `src/orchestration/deep_agent.py`(`_MAX_RESUME_ATTEMPTS`/`_RESUME_NUDGE`/`_is_empty_ai_message`/`_resume_after_empty_response` + run_deep_agent 재개 루프). 검증 `tests/test_orchestration/test_deep_agent_wiring.py`(재개 성공·진전 없음 즉시 중단·진전 반복 완주·상한·예외 폴백).
- **대안(기각)**: 무조건 N회 재시도 — 진전 없는 반복 호출 낭비. LangGraph 체크포인터 기반 재개 — deepagents 호출부는 무체크포인터 설계라 이력 재주입이 더 단순·동형.
- **관련**: D-092(조기 종료 안내 — 재개 실패 시 최종 방어), D-094(재개로 드러난 생성측 스코프 결함), D-062(최종 응답은 collector 합성)

## D-094. sub-task SQL 생성의 original_query 스코프 (D-092 ③의 생성측 대칭)
- **결정일**: 2026-07-20 | **상태**: 확정 (구현 완료 · 라이브 검증)
- **배경(실측)**: D-093 라이브 검증에서 발견 — `query_generator._build_user_prompt`는 "## 사용자 질의"로 `parsed_requirements["original_query"]`를 사용하는데, orchestration 경로의 `_make_isolated_input`이 **전체 질의의 parsed_requirements를 그대로 전달**해 sub_query의 제약(선행 결과로 해석된 `서버명 IN ('SV-WEB-001','SV-BATCH-009')`)이 아닌 **전체 질문에 대한 SQL**이 생성됐다. data_query는 알람 테이블 접근이 없어(D-076) "알람 서버 중" 조건이 침묵 탈락 → 전 서버 기준 오답(Dell/KR2023ORA0023 — 알람 무관 서버) 반환.
- **결정**: `_make_isolated_input`이 `parsed_requirements` **사본**의 `original_query`만 task `sub_query`로 교체(`_scope_parsed_requirements`). time_range 등 구조화 필드는 보조 맥락으로 유지. 단일 task(sub_query==전체 질의)는 실질 불변. 단일/멀티 DB 경로 모두 이 지점 하나로 대칭 적용(known mistakes 대칭 원칙).
- **라이브 검증(2026-07-20)**: 적용 후 동일 질의에서 제조사·일련번호 SQL에 `HAVING … IN ('SV-WEB-001','SV-BATCH-009')` 필터가 정확히 생성·1차 검증 통과, 두 서버의 Vendor/SerialNumber(HPE/KR2024WEB0001, IBM/null) 반환.
- **잔여 한계(정직)**: 재개 라운드에서 **오케스트레이터가 쓰는 sub_query 자체**가 선행 제약을 누락하는 경우(예: "6월 CPU 최고 서버 조회"를 알람 서버 한정 없이 발행)는 본 결정의 범위 밖 — deepagents 경로는 D-086 prior_rows 결정적 주입이 배선되지 않아(`_run_subagent_tool`이 `prior={}` 전달) 선행 결과 전파를 오케스트레이터의 sub_query 작성 품질에 의존한다. 후속 과제로 기록.
- **구현**: `src/orchestration/subagents.py`(`_scope_parsed_requirements`). 검증 `tests/test_orchestration/test_subagents.py`(스코프·비오염·구조화 필드 유지).
- **관련**: D-092(최종화측 스코프 — 동일 결함 클래스), D-086(선행 결과 결정적 주입 — deepagents 미배선 잔여), D-076(data_query 알람 테이블 격리)

## D-095. deepagents 경로 선행 결과 스코프 결정적 주입 (D-086 배선 대칭)
- **결정일**: 2026-07-20 | **상태**: 확정 (구현 완료 · 라이브 검증 — 대상 질의 정답 도달)
- **배경**: D-094 잔여 — deepagents 경로는 planner의 input_from이 없어 `_run_subagent_tool`이 `prior={}`로 호출, D-086 prior_rows 스코프 강제가 미배선. 선행 선별(알람 서버 목록)의 후속 조회 전파가 오케스트레이터의 sub_query 작성 품질에 의존했고, 라이브에서 "6월 CPU 최고 서버 조회"가 전 서버 기준 SQL로 생성돼 알람 무관 서버가 답으로 나왔다.
- **결정**: `_dependency_scope`(deepagents_tools)가 **결정적 게이트**로 선행 조회 결과(성공·행 보유·data/alarm_query)를 후속 task의 input_from/prior로 주입 → 기존 `_make_isolated_input`→`build_prior_rows_block` 경로(D-086)로 SQL IN 스코프 강제. 게이트: **G1** sub_query에 선행 식별 값 등장(재생성 필터 탈락 방지 강화) / **G2** 참조·선별 어휘("해당/그 서버/앞서/선별…") / **G3** 순위·최상급 어휘("가장/최고/상위…"). 단 전역 명시("전체/모든/전 서버")면 미주입(독립 전역 조회 보호). 오케스트레이터 지시문에도 "후속 sub_query에 선별 식별자 명시" 규칙·예시 추가(G1 적중률 향상 — 보조, 정합성은 게이트가 보장).
- **라이브 검증(2026-07-20, 대상 질의 2런)**: 주입 로그 확인, SQL에 `IN ('SV-WEB-001','SV-BATCH-009')` 스코프 강제. 2런 차에서 정답 도달 — "HPE / KR2024WEB0001"(실측 ground truth 일치: 6월 CPU 평균 SV-WEB-001 42.8% > SV-BATCH-009 18.3%).
- **잔여 한계(정직)**: 폴스타 부모-자식(server.Server/server.Cpus) 피벗에서 스코프 필터를 HAVING 집계가 아닌 **WHERE 동일 alias에 넣는 형태 변동**이 관찰됨(1런 차 — 자식 리소스 name="Cpus"라 메트릭 행 전멸 → 0건 오답, validator 통과형). 2런 차는 validator의 LEFT JOIN 강등 검출이 재생성을 유도해 HAVING 패턴으로 교정됨. → **D-096에서 결정적 검출 시행**(폴스타 어댑터 validator_checks).
- **구현**: `src/orchestration/deepagents_tools.py`(`_dependency_scope`/게이트 상수/`_run_subagent_tool` 배선), `src/prompts/orchestrator.py`(식별자 명시 규칙). 검증 `tests/test_orchestration/test_deep_agent.py`(게이트 6 + 주입 통합 2).
- **대안(기각)**: 무조건 전체 주입 — 독립 복합 의도("알람 현황과 전체 CPU 현황 각각")에서 전역 조회를 오염. LLM에게 input_from 판단 위임 — 비결정성 재의존(본 결함의 원인 구조).
- **관련**: D-086(prior_rows 스코프 강제 — 본 결정이 deepagents에 배선), D-094(생성측 original_query 스코프 — 상보), D-093(재개 가드 — 후속 도구 호출 자체를 복원), D-088(공용 블록 DB-agnostic 유지)

## D-096. 폴스타 피벗 스코프 필터 WHERE 강등 결정적 검출 (어댑터 validator)
- **결정일**: 2026-07-20 | **상태**: 확정 (구현 완료 · 라이브 검증 — 오답 형태 차단→재생성→정답)
- **배경(실측)**: D-095 잔여 — 폴스타 부모-자식 피벗(한 alias로 `resource_type IN ('server.Server','server.Cpus')` 접기 + `GROUP BY COALESCE(platform_resource_id, id)`)에서 서버명 스코프 필터를 **WHERE의 동일 alias**에 거는 형태 변동. 자식 리소스 행(name='Cpus')이 전부 탈락해 메트릭 집계가 **침묵히 0건**("데이터 없음" 오답)이 되며, 기존 validator를 통과하는 형태였다(2026-07-20 라이브 1런 차 실측).
- **결정**: 폴스타 어댑터 `validator_checks`에 `check_scope_filter_where_demotion` 추가 — 주석 제거(sqlparse, D-087 규약) 후, ①`alias.resource_type IN (...)`에 리터럴 2개 이상(다중 레벨 접기)인 alias를 수집하고 ②그 alias의 `name/hostname`(COALESCE 형 포함) 필터가 **WHERE 구간**(GROUP BY/HAVING 이전)에 있으면 오류로 거부한다. 오류 메시지에 교정 형태(HAVING 집계 CASE WHEN 예시 또는 alias 분리)를 명시해 재생성을 유도한다. HAVING의 정상 집계 필터·단일 resource_type alias·서버/자식 분리 조인은 비검출. D-089 어댑터 디스패치로 폴스타에서만 발동(공용 계층 DB-agnostic 유지 — D-088).
- **라이브 검증(2026-07-20)**: 대상 질의에서 attempt 0이 정확히 이 형태를 생성 → 신규 체크가 거부 → attempt 2에서 HAVING 패턴으로 재생성 → 1건 정답(HPE/KR2024WEB0001, ground truth 일치). 이전 런에서 validator를 통과해 0건 오답을 냈던 형태가 결정적으로 차단됨을 확인.
- **구현**: `src/db_adapters/polestar/validators.py`(`check_scope_filter_where_demotion`), `adapter.py` validator_checks 등록. 검증 `tests/test_db_adapters.py::TestScopeFilterWhereDemotion`(실측 오답/정답 SQL 픽스처 — 검출 2·비검출 4·배선 1).
- **대안(기각)**: 이 쿼리 형태의 결정적 조립(D-076 트랙) — 유효하나 조립기 신설 비용 대비 validator 거부→재생성으로 충분함을 라이브로 확인(형태 변동이 재생성에서 자기 교정됨). 공용 validator에 배치 — 폴스타 스키마 지식(resource_type/name 계층)이라 어댑터 소속이 원칙(D-088/D-089).
- **관련**: D-095(잔여 한계 해소), D-087(주석 제거 후 판정 규약), D-089(어댑터 validator_checks 훅), D-085(LEFT JOIN 강등 검출 — 동일 계열의 침묵 0건 방지)

## D-097. 스코프된 피벗 조회의 서버 식별 컬럼 SELECT 포함 강제 (같은 행 식별)
- **결정일**: 2026-07-20 | **상태**: 확정 (구현 완료 · 라이브 검증)
- **배경(실측)**: 사용자 재테스트에서 "서버명과 제조사·일련번호가 같은 행에 나오지 않음" — 선행 스코프(HAVING name IN) 피벗 SQL의 SELECT가 manufacturer/serial_number만 조회(생성 변동 — 일부 런은 server_name 포함). 결과 행이 어느 서버의 값인지 식별 불가.
- **결정**: 이중 방어 — ①**공용 스코프 블록 규칙 4**(`build_prior_rows_block`): "각 행이 어느 서버의 값인지 알 수 있도록 서버 식별 컬럼을 SELECT에 반드시 포함(피벗이면 집계 CASE WHEN)" — DB-agnostic 문구(D-088 준수, overfit 기준선 무유입 확인). ②**폴스타 어댑터 validator** `check_scoped_pivot_missing_server_identity`: HAVING에 서버 식별(name/hostname) 스코프 필터가 있는데 SELECT에 cmm_resource alias의 식별 컬럼이 없으면 거부(교정 예시 포함). HAVING 스코프 없는 전체 피벗(폼필 조립기 SQL — HAVING 미방출 실측)은 미검사(오검출 방지). EAV alias(cc.name='Vendor')는 식별로 오인하지 않음.
- **라이브 검증**: 규칙 4 적용 후 첫 attempt부터 SELECT에 server_name 포함, 최종 행 `{server_name, manufacturer, serial_number, cpu_usage_avg}` 한 행 반환.
- **구현**: `src/utils/query_gen_common.py`(규칙 4), `src/db_adapters/polestar/validators.py`, `adapter.py`. 검증 `tests/test_db_adapters.py::TestScopedPivotMissingServerIdentity`(5).
- **관련**: D-095(스코프 주입 — 본 결함의 발현 문맥), D-096(피벗 형태 결함 검출 계열), D-088(공용 블록 DB-agnostic)

## D-098. 폴스타 피벗 성능 통계 조인 결함 2종 결정적 검출 (서버 엔터티 바인딩·INNER 강등)
- **결정일**: 2026-07-20 | **상태**: 확정 (구현 완료 · 라이브 검증 — 오답 형태 차단→재생성→정답)
- **배경(실측, D-097 라이브 검증 연쇄에서 발견)**: 통계 조인의 형태 변동 2종이 각각 침묵 오답을 유발. **①서버 엔터티 바인딩**: `cmm_metric_stat_m`을 `server.Server`로 고정된 alias의 id에 조인 — 통계는 자식 리소스(Cpus/Memory/Disks/FileSystems)에만 붙음(DB 실측) → 집계 전부 NULL → `ORDER BY … DESC`(PostgreSQL 기본 NULLS FIRST)로 **임의 서버가 1위 선택**(SV-BATCH-009 오답 실측). **②INNER 강등**: 다중 타입 피벗 alias에 통계를 INNER JOIN — 통계 없는 server.Server 행이 그룹 탈락 → HAVING 서버 필터 전부 NULL → **침묵 0건**("데이터 없음" 오답 실측).
- **결정**: 폴스타 어댑터 validator 2종 추가 — `check_metric_join_on_server_entity`(①: 통계 조인 상대 alias의 resource_type 제약이 {'server.Server'}뿐이면 거부. 제약 수집은 FROM~GROUP BY 구간 한정 — SELECT의 CASE WHEN 비교는 조인 제약이 아님), `check_pivot_metric_inner_join`(②: resource_type 2종 이상 + server.Server 포함 alias에 비-LEFT 통계 조인이면 거부, LEFT+ON 교정 지시. WHERE 조건 강등은 기존 D-085 검출이 담당). 둘 다 교정 형태 명시로 재생성 유도.
- **라이브 검증**: ②형태 attempt 0 생성 → 신규 체크 거부 → attempt 1 LEFT+ON 재생성 → 1행 정답(`SV-WEB-001 / HPE / KR2024WEB0001 / 42.8%` — ground truth 일치).
- **주의(누적 관찰 — 정직)**: 동일 질의 형태("선행 스코프 + 자식 리소스 메트릭 순위 + EAV 속성")에서 형태 변동 결함이 4종 누적 검출됨(D-096 WHERE 강등, D-097 식별 누락, D-098 ①②). validator 울타리로 정답 수렴을 실증했으나, 변동이 추가 관찰되면 이 형태는 결정적 조립(D-076 트랙) 편입을 재검토한다(Known Mistakes "반복 실패 형태는 결정적 조립" 원칙).
- **구현**: `src/db_adapters/polestar/validators.py`, `adapter.py`(validator_checks 5종). 검증 `tests/test_db_adapters.py::TestMetricJoinOnServerEntity`(5)·`TestPivotMetricInnerJoin`(4).
- **관련**: D-096/D-097(피벗 형태 결함 검출 계열), D-085(LEFT JOIN WHERE 강등 — ②의 상보), D-087(주석 제거 규약), D-076(결정적 조립 — 누적 시 편입 후보 → D-099에서 편입)

## D-099. 선행 스코프 + 메트릭 순위 질의의 결정적 조립 편입 (validator 울타리 → 조립)
- **결정일**: 2026-07-20 | **상태**: 확정 (구현 완료 · 라이브 정답 도달)
- **배경(실측)**: D-096~D-098로 가드를 5종 쌓았음에도 **6번째 변종**이 발생 — LLM이 `LEFT JOIN cmm_resource r … r.resource_type='server.Server'`로 고정한 alias를 집계에서 `CASE WHEN r.resource_type='server.Cpus'`로 검사(모순 조건 → 항상 NULL) + `ORDER BY … DESC`의 PostgreSQL 기본 NULLS FIRST가 겹쳐 **임의 서버(SV-BATCH-009)가 1위**로 반환. 이때 `retry_attempt=3`으로 **재시도 예산(QUERY_MAX_RETRY_COUNT=3)이 소진**돼 마지막 시도가 그대로 실행됐다. 프로젝트 원칙(CLAUDE.md "반복 실패하는 쿼리 형태는 결정적 조립 대상", D-035)에 따라 울타리 확장이 아니라 조립 편입으로 전환한다.
- **결정**: 이 형태("선행 스코프 + 자식 리소스 메트릭 순위 + EAV 속성")를 **기존 트랙 C 컴파일러(D-076)로 편입**한다(새 엔진 신설 금지 — D-067). ①조립기 `build_multi_resource_pivot_sql`에 `server_scope`(→ HAVING 집계 CASE WHEN, WHERE 배치 원천 차단)·`order_by`(→ `ORDER BY "<alias>" DESC **NULLS LAST**`) 추가. ②`compile_smq`/`_compile_ab`/`compile_from_nl`에 스코프 전달 + `_resolve_ranking`(최상급 어휘→measure alias 정렬). ③스코프가 있으면 식별 dimension을 SELECT에 결정적 포함(D-097 자동 충족). ④`query_generator`가 `prior_rows`를 `_prior_server_scope`로 전달 — **기존 우회(D-086) 해제**. ⑤커버리지 진입 보정: 스코프가 있으면 SMQ의 서버 식별 필터를 제거(중복·커버리지 밖 사유 해소)하고, SMQ 프롬프트에 스코프 노트를 주입해 "특정 서버 지목·정렬 상위"를 이유로 `pattern:none`을 반환하지 않게 한다(실측 재현 후 수정). ⑥`resolve_stat_month`가 **절대 월("2026년 6월"/"2026-06")** 을 해석하도록 확장 — 미해석 시 조립 SQL에 기간 필터가 빠져 전 기간 평균으로 순위가 뒤집혔다(실측).
- **보완 가드(LLM 폴백 경로용)**: `check_contradictory_alias_resource_type`(고정 alias를 다른 resource_type으로 검사 = 영원히 거짓), `check_ranking_order_by_nulls_last`(집계 DESC + 행 제한인데 NULLS LAST 없음). 조립이 발동하지 않는 잔여 질의를 방어한다.
- **라이브 검증(2026-07-20)**: 결정적 조립 발동(`시맨틱 결정적 컴파일 SQL(LLM 우회)`), SQL에 `stat_date='202606'`·`HAVING … IN ('SV-WEB-001','SV-BATCH-009')`·`ORDER BY "cpus_avg" DESC NULLS LAST` 전부 포함, 최종 응답 **"SV-WEB-001 / HPE / KR2024WEB0001 / 42.8%"** 단일 행 — DB ground truth 일치. 조립 SQL이 폴스타 validator 7종을 자체 통과함을 테스트로 고정(자기정합 가드).
- **구현**: `src/db_adapters/polestar/assembler.py`(scope/order_by), `src/nodes/semantic_compiler.py`(전달·`_resolve_ranking`·필터 제거·식별 dimension 보장), `src/prompts/semantic_compiler.py`(`SEMANTIC_SMQ_SCOPE_NOTE`), `src/nodes/query_generator.py`(`_prior_server_scope`·우회 해제), `src/utils/query_gen_common.py`(절대 월·스코프 블록 규칙 4), `src/db_adapters/polestar/validators.py`(가드 2종). 검증: `tests/text2sql/test_semantic_golden.py`(스코프·순위·커버리지 진입 8), `tests/test_db_adapters.py`(조립·가드 12), `tests/test_utils/test_multi_resource_pivot.py`(절대 월 7), `tests/test_orchestration/test_prior_rows_scope.py`(우회→전달로 갱신).
- **기존 테스트 갱신(정직)**: `test_prior_rows_bypasses_semantic_compile`은 **우회 동작을 정답으로 굳힌** 테스트라 `test_prior_rows_passed_to_semantic_compile_as_scope`로 교체(Known Mistakes "기존 테스트가 버그를 정답으로 굳혔는지 점검").
- **관련**: D-076(트랙 C 컴파일러 — 본 결정이 형태 편입), D-067(단일 조립 엔진 재사용), D-086(선행 스코프 프롬프트 블록 — 우회 해제), D-096~D-098(울타리 가드 — 폴백 경로 방어로 유지), D-035(결정=코드·LLM=지식)

## D-100. 질의에 언급된 모든 항목의 결과 표시 — 하위 조회 컨텍스트 확장 + 서버 키 병합
- **결정일**: 2026-07-21 | **상태**: 확정 (구현 완료 · 라이브 정답 도달)
- **배경(실측)**: "심각 알람 서버 중 6월 CPU 최고 서버의 제조사·일련번호" 최종 표에 서버명·제조사·일련번호·CPU평균은 나오나 **알람명·심각도가 누락**. 원인 — 오케스트레이터가 질의를 2단계로 쪼개며 ①알람 조회가 서버명만 SELECT(알람명·심각도 소실) ②최종 표는 마지막(CPU) 조회만 노출. 사용자 요구: 질의에 언급된 모든 개념을 한 표에 표시(모든 프롬프트 일반화). 선택된 방식 = 하위조회 확장 + 표시 계층 병합(접근 A).
- **결정**: ①**알람 조회 컨텍스트 확장**(`_compile_c`): 패턴 C는 서버명만 요청돼도 선별 근거(서버명·알람명 `D.NAME`·심각도 `ALARMSEVERITY`)를 결정적으로 앞에 포함. 표시·병합 친화 alias(server_name/alarm_name/severity). 카탈로그 정의 dimension만 써 환각 불가. ②**서버 키 결정적 병합**(`result_aggregator._merge_task_results_by_identity`): 딥에이전트 합성 모드에서 여러 하위 조회가 공통 서버 식별 컬럼을 공유하면 canonical 키(server_name 선호)로 outer join → 통합 표. 기준(base) = 행수 최소 조회(가장 좁게 스코프됨)의 서버만 남겨 "가장 높은 서버"가 대상 전체로 번지지 않게 함(tie면 선행=선별 기준). 서버당 1행(대표), 다건은 로그. 공통 키 없으면 LLM 합성 폴백(D-062). 병합 성공 시 단일 `output_generator`로 표/자연어 생성. ③**전체 컬럼 표시 강제**(`output_generator`): 시스템 프롬프트 규칙 3 + `_build_response_prompt` 컬럼 목록 명시 — LLM이 질의 문구("제조사와 일련번호")에 이끌려 컬럼을 임의 생략하지 못하게 함(실측: 6컬럼 병합 rows에서 3컬럼만 표시). ④**최상급 순위 LIMIT 1**(`_compile_ab`): order_by(최상급)면 결정적 조립 limit=1 — default_limit(1000)로 전체 반환 시 병합 대상이 순위 1건이 아니라 선별 전체가 됨(실측 2건).
- **부작용 수정(회귀 차단)**: 알람 조회가 알람명도 반환하면서 D-095 선행 스코프 추출이 "name" 부분매칭으로 `alarm_name`을 서버 식별값으로 오수집 → CPU HAVING이 `IN ('SV-WEB-001','CPU 사용률 임계 초과',…)`로 오염(실측). **서버 식별 컬럼 엄격 판정**(`is_server_identity_col`) 공용 함수 신설 — 정확 매칭 + server/host/os 계열 *_name/_id만 인정, alarm_name/definition_name/severity 배제. `_collect_prior_identity_values`·`_extract_identity_rows` 두 추출 지점에 적용.
- **라이브 검증(2026-07-21)**: 알람 조회 `server_name·alarm_name·severity` 반환, CPU 조회 `LIMIT 1`(HAVING 오염 없음), 병합 1행 6컬럼, 최종 표 **| 서버명 | 알람명 | 심각도 | 제조사 | 일련번호 | CPU 사용률 평균 | / | SV-WEB-001 | CPU 사용률 임계 초과 | 3 | HPE | KR2024WEB0001 | 42.8 |** — 질의 전 항목 한 행 표시.
- **구현**: `src/nodes/semantic_compiler.py`(`_compile_c` 컨텍스트·`_compile_ab` limit), `src/orchestration/result_aggregator.py`(`_merge_task_results_by_identity`·`_finalize_merged_rows`·`_find_identity_col`), `src/nodes/output_generator.py`+`src/prompts/output_generator.py`(전체 컬럼), `src/utils/query_gen_common.py`(`is_server_identity_col`)·`src/orchestration/subagents.py`(적용). 검증: `tests/test_orchestration/test_result_aggregator.py`(병합 6), `tests/text2sql/test_semantic_golden.py`(컨텍스트·LIMIT 4).
- **대안(기각)**: 단일 통합 SQL(알람 JOIN 지표) — 교차 조인 복잡·일반화 어려움(사용자도 A 선택). 표시 프롬프트만 강제 — 데이터 미수집(알람명) 항목은 못 채우거나 환각.
- **관련**: D-099(결정적 조립 — 순위 LIMIT·스코프 상보), D-095(선행 스코프 추출 — 오염 수정), D-062(합성 폴백 — 병합 불가 시), D-047(query_results 승격)

## D-106. Plan 60 E1 — 재발생 dedup 관측성 강화 (count/last_seen 집계 + 감사)
- **결정일**: 2026-07-21 | **상태**: 확정 (구현 완료 · Plan 60 Wave A)
- **배경(실측)**: 재발생 dedup은 기구현이나, 억제된 재발이 그래프 진입 **이전**에 종료돼 decision_store 감사 사각지대(§3.1). 억제 count도 대표 알람·감사에 미노출("억제≠삭제" 실질 사각).
- **결정**: `_gate_dedup`를 `{first_seen,last_notified,last_seen,count}` dict화. **TTL 비교는 last_notified 기준(고정창 — 슬라이딩 창 변질=지속 재발 알람 영구 미재통보 회귀 방지)**, 만료 sweep은 last_seen(재발 레코드 count 보존). `_is_duplicate_fingerprint`는 `(is_dup, meta)` tuple 반환. 억제 시 `record_recurrence`(type="recurrence") 감사 적재(recurrence_audit_every_n 샘플링), 재통보 시 직전 창 count를 그래프 state `recurrence`로 전달해 대표 알람에 "직전 Nh C회 재발 후 재통보" 1줄 표기. `aggregate()`는 `type` 보유(비-decision) 레코드 일반 제외로 리팩토링. recurrence는 signals 동결 스키마 **밖** decision_store 최상위 필드.
- **근거**: Moogsoft/BigPanda 재발생 count 집계. 억제 판정(TTL·심각도 분기)은 현행과 **비트 동일**(집계만 추가) — 기존 dedup 테스트 그대로 통과.
- **구현**: `alarm_worker.py`(_gate_dedup·_is_duplicate_fingerprint·_record_recurrence·graph state), `decision_store.py`(record_recurrence·aggregate 일반화·record recurrence 인자), `alarm_graph.py`(AlarmState.recurrence), `notification_gate.py`·`alarm_notifier.py`, `config.py`(recurrence_audit_every_n). 검증: `test_recurrence_dedup.py`·`test_sev3_repeat_interval.py`(회귀).
- **주의**: 옵트인 플래그 불필요(게이트 off면 경로 미진입 — 회귀 0). in-memory 상태는 재기동 시 자연 초기화(관측성 신호 — 영속화 비범위).
- **관련**: D-048(게이트 4-티어), D-049(record_resolution 전례), Plan 60 §3. (계획 초안 D-077은 결번 확정 → D-106 재부여, §8 규칙.)

## D-107. Plan 60 E4 — 토폴로지 의존성 그래프 + 다홉 하이브리드 억제 (B-1·B-5 확정)
- **결정일**: 2026-07-21 | **상태**: 확정 (구현 완료 · Plan 60 Wave A)
- **배경(실측)**: 현행 의존성 억제는 부모 1홉(parent_avail_status)만 판정 — 다홉 조부모→손자 연쇄 미탐지. 그래프 없이는 크로스-호스트 위상 상관(E2)·Plan 50 결정적 RCA 불가(공용 병목 자산).
- **사용자 확정**: **B-1=(a) AVAIL_DEPEND 단독**(CMDB 병합은 커버리지 부족 실증 시 확장·현 비범위). **B-5=하이브리드**(root 통보 확인 시 SUPPRESS, 미확인 시 DASHBOARD 강등 — 재현율 우선).
- **결정**: 신규 domain `topology.py`(DependencyGraph — BFS 조상·방문집합 순환방어·홉상한, is_cascaded/find_root/name_of, **stdlib only**) + 인프라 `topology_loader.py`(정적 엣지 장기캐시 topology_cache_ttl_seconds + 동적 AVAIL_STATUS IN 조회, 홉 상한 유계). 게이트 step6.4 확장: `cascaded`면 root_notified→SUPPRESS/미통보→DASHBOARD, `cascaded` 미제공(1홉·수집실패)→현행 parent_avail_status 폴백. **root_notified는 enricher가 캐시 이후** worker `_active_firings`(configurable 참조 전달)로 신선 산출(동적 — 캐시 금지). 정책 모듈은 topology **import 금지**(noise_ctx bool/id만 소비 — 순수성). 1차 범위=gp/yd(PostgreSQL, db_engine 판정 D-057), b0(DB2)→1홉 폴백→비억제. signals 동결 스키마 **Wave A 일괄 확장**(cascaded·root_resource·correlated[E2 휴면 예약]) + `decide_notification(correlated=False)` 휴면 인자.
- **근거**: Davis Smartscape·MicroRCA(속성 그래프). 다홉은 `dependency_suppression AND multi_hop_cascade_enabled`(상위 모드). Plan 50 RCA의 최우선 선행자산.
- **구현**: `topology.py`·`topology_loader.py` 신설, `notification_policy.py`(step6.4·_signals·correlated 인자), `polestar_noise_context.py`(SVR.ID·`_NOISE_CTX_KEYS` 단일 출처·다홉 산출), `alarm_context_enricher.py`(root_notified), `alarm_worker.py`(active_firings 참조 전달), `config.py`(multi_hop_cascade_enabled·topology_cache_ttl_seconds·topology_max_hops). 검증: `test_topology.py`·`test_topology_loader.py`·`test_multi_hop_cascade.py`·`test_dependency_suppress.py`(1홉 회귀).
- **주의(실측 정정)**: 엣지 로더 SQL(엣지 보유 행만)은 부모 없는 root의 NAME이 누락 → 조상 IN 조회에 NAME 보강(없으면 root_notified 항상 False 오작동). 심각도3 step3 단락 불변(cascaded여도 PAGE). multi_hop off면 게이트 비트 동일(회귀 0).
- **관련**: D-048, D-057(엔진 방언 판정), Plan 50(RCA 선행자산), Plan 60 §6. (초안 D-080 결번 → D-107 재부여.)

## D-108. Plan 60 E6 — 통보 컨텍스트 보강 L1 선구현 (Plan 64 §4.8의 L1 단계)
- **결정일**: 2026-07-21 | **상태**: 확정 (L1 구현 완료 · Plan 60 Wave A / L3는 Plan 64 §4.8 후속)
- **배경(실측)**: 사용자 요건("어느 프로세스가 원인인지 통보")은 CPU/메모리에 한해 이미 end-to-end 기구현(Plan 47-1: enrich_processes→ProcessSnapshot→notifier 표). §16은 신규 구축이 아니라 **범위 확장**.
- **결정**: `classify_alarm_kind` cpu|memory → +disk/network/process/log(순수·키워드). 신규 domain `enrichment_profile.py`(kind→L1 프로파일 요지·csv 오버라이드, 순수 stdlib). notifier `_process_table_html`을 kind별 보강 블록으로 일반화(cpu/memory 표 **비트동일**). disk/network는 host-wide 프로세스 스냅샷 참고 첨부(신규 SQL 0), **process/log는 데이터 소스 미확정→요지 제목만**(graceful — 확인 안 된 SQL/테이블 미생성). 메시지형 자유텍스트 LLM 분류는 결정적 프로파일로 대체(서술 전용·D-035 — 신규 LLM 접점 과침습 회피, 후속). post-gate·라우팅 **불변**(첨부만). 옵트인 message_enrichment_enabled(기본 off) — 기존 process_enrich_enabled(CPU/메모리) 경로 비트동일.
- **근거**: 노이즈 캔슬링 두 축(억제+보강)의 보강 L1. **단일 결정 2단계** — L1=Plan 60 §16(즉시·블로커 없음), L3(top/pidstat/journalctl·추이)=Plan 64 §4.8(D-102·B-1 후).
- **구현**: `process_rank.py`(classify_alarm_kind), `enrichment_profile.py` 신설, `alarm.py`(MessageEnrichment), `alarm_context_enricher.py`(build_message_enrichment), `alarm_notifier.py`(kind별 블록), `alarm_graph.py`(AlarmState.enrichment), `alarm_worker.py`·`config.py`(message_enrichment_enabled·enrichment_min_tier·enrichment_l1_timeout_seconds·enrichment_profile_map_csv), `api/routes/alarm.py`. 검증: `test_enrichment_profile.py`·`test_message_enrichment.py`·`test_alarm_process_rank.py`.
- **주의(실측)**: classify_alarm_kind 확장이 disk 등을 반환하므로 `enrich_processes`·API 경로에 **cpu/memory 전용 가드 필수**(없으면 disk 알람이 프로세스 조회 유발 → 기존 보강 게이팅 회귀). message off면 통보 비트동일.
- **관련**: Plan 47-1(프로세스 보강 전례), Plan 64 §4.8(L3 단계), D-003(읽기전용), D-035, Plan 60 §16. (초안 D-105 → D-101~105 예약블록 충돌로 D-108 재부여, §8 규칙.)

## D-109. Plan 60 E2 — 크로스-호스트 이벤트 상관 (Wave B · B-6 확정)
- **결정일**: 2026-07-22 | **상태**: 확정 (구현 완료 · Plan 60 Wave B)
- **배경(실측)**: 현행 `_detect_storm`은 스코프 `db_id|server` 내 발생 다발만 억제 — **동일 서버 경계 안**. 스위치 장애로 20대 서버가 동시 알람 시 서버별 각각 통보. Moogsoft Cookbook(필드 Tanimoto)·Splunk Event Analytics(episode)는 서버 경계 넘어 필드 유사도·시간 근접으로 군집.
- **사용자 확정**: **B-6 = db_id(존) 경계 내 상관**(존 간 gp↔yd 상관 금지 — 공통 원인 실증 후 확장).
- **결정**: 신규 domain `correlation.py`(stdlib only — `signature_tokens`[server_name 제외]·`jaccard`[Tanimoto]·`ClusterState`·`match_cluster`[동점 first_ts 오름차순=결정성]). 워커 `_detect_correlated_storm`(**온라인 그리디 군집**: 첫 도착=대표·소급 선출 없음, db_id 존 스코프, `correlation_min_cluster_size`번째 멤버부터 억제) — `_detect_storm`과 **독립 병존**. sig_label은 워커가 domain `scan_signature_severity`로 사전 산출(정책 순수성 — infra 미import). 게이트 **step7.5**(step7 스톰 뒤·step8 매트릭스 앞): `cross_host_correlation_enabled and correlated → SUPPRESS`("크로스-호스트 상관 — 클러스터 대표 외 억제", storm 사유와 구분). `correlated` 인자·`signals["correlated"]`는 E4가 이미 추가(로직만 배선). 상관 버퍼는 값 상한(`correlation_buffer_max`)뿐 아니라 **만료 클러스터·빈 스코프 키 sweep**(§10).
- **근거**: 심각도3 step3 단락 불변(군집돼도 각각 PAGE). `cross_host_correlation_enabled=False`(기본)면 detection 미수행·`_detect_storm` 비트동일(회귀 0).
- **구현**: `correlation.py` 신설, `alarm_worker.py`(_detect_correlated_storm·_correlation_clusters·sweep·correlated 시드), `notification_gate.py`(correlated 전달), `notification_policy.py`(step7.5), `alarm_graph.py`(AlarmState.correlated), `config.py`(cross_host_correlation_enabled·correlation_sim_threshold·correlation_window_seconds·correlation_min_cluster_size·correlation_buffer_max·correlation_field_weights_csv). 검증: `test_cross_host_correlation.py`·`test_plan60_flags_off_regression.py`·`test_storm_grouping.py`(회귀).
- **주의**: **E2 1차 = 필드 Jaccard 유사도만** — 위상 가중(E4 그래프 연계)은 단계적 후속(FI). decision_store 클러스터 메타 첨부는 surgical 범위 밖(워커 debug 로그만·후속 분리 가능). 오프라인 itemset mining 시드(Fan 2018)·순서 민감 유사도(Cheng 2016)는 FI 백로그.
- **관련**: D-048, Plan 52 `_detect_storm`(전례), Plan 60 §4. (초안 D-078 결번 → D-109 재부여.)

## D-110. Plan 60 E3 — 동적 baseline 이상탐지 (Wave B · B-3 순수 Python HW)
- **결정일**: 2026-07-22 | **상태**: 확정 (구현 완료 · Plan 60 Wave B)
- **배경(실측)**: 게이트 step1은 `max(폴스타 severity, ai_message_severity)` 상향 슬롯을 이미 보유(`enable_ai_severity_boost`)하나 baseline 이탈 공급원이 없었다. Dynatrace 적응형 baseline·New Relic Holt-Winters·Splunk 적응형 임계는 시계열 동적 baseline 이탈을 상향 신호로 쓴다. Plan 50 정적 z-score는 계절성 오탐이 크다. **E3는 게이트 수정이 아니라 상향 슬롯을 채우는 백엔드 신설**.
- **사용자 확정**: **B-3 = 순수 Python Holt-Winters**(외부 패키지 불요·domain stdlib-only — statsmodels 반입 협의 소멸).
- **결정**: 신규 domain `anomaly.py`(math/statistics만 — additive 삼중 지수평활 `holt_winters_fit`·`residual_sigma`·`anomaly_score`·`severity_from_anomaly`[상향 전용]·`METRIC_SOURCE_BY_KIND`). 인프라 `polestar_metric_baseline.py`(`cmm_metric_stat_h` 고정 SQL 읽기전용·Redis `alarm:baseline:{db_id}:{server}:{kind}` 캐시). **배선**: 계산=enricher gather 5번째 코루틴→`AlarmState.anomaly_severity`, 반영=`alarm_analyzer` LLM 파싱 직후 결정적 후처리(상향 전용 가드: `anomaly_sev>severity && anomaly_sev>기존 ai`, `dynamic_baseline_enabled AND enable_ai_severity_boost`). **게이트(`notification_policy`) 코드 무변경**(anomaly 참조 0 — 상향 슬롯 공급만). 알람→메트릭은 `classify_alarm_kind` 재사용하되 **`kind in METRIC_SOURCE_BY_KIND` 화이트리스트**(Known Mistakes #2 — `kind is not None` 금지, E6 disk/network/log 변질 방지) → 1차 CPU·메모리만·그 외 skip→None.
- **근거**: Szmit(2012) Holt-Winters 이상탐지·경량 통계가 폐쇄망·해석성·결정성 유리(Choi 2021). 상향 전용(`max()` 하향 불가·SSOT 보존). 히스토리 부족(<anomaly_min_periods*period)·비PostgreSQL→None(상향 없음).
- **구현**: `anomaly.py`·`polestar_metric_baseline.py` 신설, `alarm_context_enricher.py`(_anomaly_baseline 코루틴), `alarm_analyzer.py`(후처리 훅), `alarm_graph.py`(AlarmState.anomaly_severity), `alarm_worker.py`(metric_baseline 주입), `config.py`(dynamic_baseline_enabled·anomaly_z_high·anomaly_min_periods·anomaly_baseline_cache_ttl_seconds). 검증: `test_anomaly.py`·`test_metric_baseline.py`·`test_anomaly_severity_guard.py`·`test_plan60_flags_off_regression.py`(F섹션).
- **주의(실측)**: `METRIC_SOURCE_BY_KIND` definition_name(server.Cpus/server.Memory·Utilization)은 assembler 피벗 실측과 일치. 계절 period=24(일간·히스토리 3일) 1차 채택, 주간(168)·STL은 2차. **prometheus_client.py는 미배선**(§5.2 확정 소스=cmm_metric_stat, prometheus는 l3 "제안" 폴백 — preparatory 유지). `dynamic_baseline_enabled=False`면 enricher 키셋·analyzer ai_message_severity 무변경(회귀 0).
- **관련**: D-035(결정적=판단·LLM=보조), Plan 50(정적 z-score 대체), Plan 60 §5·§13.1. (초안 D-079 결번 → D-110 재부여.)

## D-111. Plan 60 E5 — 변경/구성 이벤트 상관 (Wave C · B-2 폴스타 변경이력 선조사 확정)
- **결정일**: 2026-07-22 | **상태**: 확정 (1차 구현 완료 · Plan 60 Wave C)
- **배경**: "장애의 최대 원인은 변경"이나 현행 게이트·RCA는 배포·구성변경을 보지 못한다. Davis/Watchdog은 결함 배포를 근본원인으로 지목(faulty-deployment detection).
- **선조사(팀리드 폴스타 DB 실측)**: **B-2(a) 확정** — 폴스타 개발 DB(:5434, 실 제품 스키마 394테이블)에 변경이력 테이블 **실재·적합**. primary=`cmm_resource_lifecycle_history`(resource_id·event_time·lifecycle_type·resource_type·description — 변경 이벤트 구조 완비). 보조=`core_config_history`(구성 변경)·`sms_agent_patch_history`(배포/패치·faulty-deployment). **주의**: 개발 sandbox 데이터는 합성 placeholder(event_time=1..5·cmm_resource 조인 0)라 통합 오버레이 미검증 — **스키마는 실 제품 스키마이나 dev 데이터는 더미**(실운영 편입 시 event_time 단위·조인 재검증). 로직 검증은 합성 ChangeEvent 단위 테스트.
- **결정**: 신규 infra `change_feed.py`(`ChangeEvent`·`fetch_recent_changes(window)` — `cmm_resource_lifecycle_history` 읽기전용 조회, 피드 부재/실패/비PostgreSQL→빈 리스트 graceful, topology_loader 패턴 재사용). 신규 domain `change_correlation.py`(`overlay_changes(incident_window, changes, *, affected_resource_ids)` — 타임라인 오버레이+영향범위 매칭, 순수·결정적, 덕타이핑으로 infra 미import). `polestar_noise_context._NOISE_CTX_KEYS`에 `change_nearby`·`change_candidates` 추가·`_compute_change_correlation`. 게이트 **step9 보조 조정**: `change_nearby → promote("변경 근접(원인성)")` — **억제 아님·승격만**(재현율 우선·PAGE 근거 보강, change 모듈 import 없이 noise_ctx bool만 소비). **change_nearby는 signals 동결 스키마 밖**(승격 사유가 decision.reason에 감사).
- **근거**: Davis/Watchdog faulty-deployment. 읽기전용(D-003) — 변경 실행/롤백은 범위 밖(자동복구 별도). `change_correlation_enabled=False`(기본)면 변경 조회 미수행·게이트 비트동일(회귀 0).
- **구현**: `change_feed.py`·`change_correlation.py` 신설, `polestar_noise_context.py`(change 산출·_NOISE_CTX_KEYS), `alarm_context_enricher.py`(플래그 배선), `notification_policy.py`(step9 change promote), `config.py`(change_correlation_enabled·change_window_seconds). 검증: `test_change_correlation.py`·`test_change_feed.py`·`test_plan60_flags_off_regression.py`(E5 섹션). 1차 소스=cmm_resource_lifecycle_history만(보조 union·외부 CI/CMDB·수동 등록은 후속).
- **주의(복구 이력)**: 본 결정 등재 직전 사용자 GitHub Desktop 원격 병합(ux_improvement)이 미커밋 Wave B를 스태시로 대피시킴 — 팀리드가 스태시 복원→E5 재적용→전체 재검증(634 passed·arch_check 0)으로 무손실 복구(변경 이력 참조).
- **관련**: D-048(게이트), Plan 50 RCA(원인 후보 전달), Plan 64(변경 상관 확장), D-003(읽기전용), Plan 60 §7·§13.1. (초안 D-081 결번 → D-111 재부여.)

## D-112. Plan 60 E2 정밀화 — 상관 클러스터 메타 감사 + 위상 가중 (D-109 후속 완결)
- **결정일**: 2026-07-23 | **상태**: 확정 (구현 완료 · 사용자 인터뷰 확정)
- **배경**: E2(D-109)는 필드 Jaccard 온라인 군집으로 1차 구현하고 **①클러스터 메타 감사 ②위상 가중(E4 토폴로지)** 을 "후속"으로 유예했다(D-109·D-111 노트). 사용자 인터뷰로 두 건을 **지금 착수**로 확정(E3 주간 계절성은 2차 보류).
- **결정 ①(클러스터 메타 감사)**: 상관 SUPPRESS 시 대표 식별자·멤버 순번·유사도를 decision_store 감사에 노출. `match_cluster`가 `(idx, best_score)` 반환, 워커 `_detect_correlated_storm`이 `(correlated, meta)` 반환(메타=`{representative_fp, member_seq, similarity}`, 억제 시만). graph state `correlation_meta` → `notification_gate` → `decision_store.record(correlation_meta=...)`가 **최상위 별도 필드**로 기록(**signals 동결 스키마 밖** — E1 recurrence 전례). None이면 키 미포함(기존 스냅샷 무훼손).
- **결정 ②(위상 가중)**: E4 `DependencyGraph` 인접성을 상관 유사도에 가중. 신규 `id_of(name)`·`is_related(a,b,max_hops)`(topology.py·domain). `match_cluster(..., adjacent: list[bool]|None, topo_weight)` — 인접 클러스터에 `topo_weight`(기본 0.2) 보너스. **correlation.py는 topology 미import** — 워커가 그래프로 adjacent bool 리스트 산출·주입(정책 순수성·§10 워커-주입). 워커는 그래프를 자체 캐시(`_topology_graph_cache`·hot-path 클라이언트 open 회피, 실패=음성 TTL)로 로드해 sync detection에 주입. **옵트인** `correlation_topology_weight_enabled`(기본 off)→off면 Jaccard 비트동일(회귀 0). **B-6 불변**(db_id 존 스코프 내 인접성만).
- **Known Mistakes #1 재확인(실측)**: `id_of(server_name)`는 엣지 로더(`build_edges_sql WHERE AVAIL_DEPEND IS NOT NULL`)의 **자식 행 NAME만** 해소 → 순수 root 서버(엣지 없음)는 name 부재→id_of None→adjacent False→**위상 보너스 미발동·필드 Jaccard로 정상 군집**. **정확성 버그 아닌 우아한 열화**로 확인(E4 enricher의 IN 조회 name 보강은 워커 경로 미적용 — root만 폴백, 대다수 비-root는 정상 커버). 워커 경로 root name 보강은 FI 후보.
- **근거**: MicroRCA(속성 그래프 인접성). 억제 판정 결과는 현행과 동일(메타·보너스만 추가). 프로젝트 관례상 신규 옵트인 플래그 2개·시그니처 변경 2건·감사 스키마 확장이라 D-109 갱신이 아닌 별도 채번(감사 추적성).
- **구현**: `correlation.py`(match_cluster 튜플·adjacent·ClusterState.representative_node), `topology.py`(id_of·is_related·_ids_by_name), `alarm_worker.py`(_detect_correlated_storm 튜플·_load_topology_graph 캐시·adjacent 산출), `alarm_graph.py`(AlarmState.correlation_meta), `notification_gate.py`·`decision_store.py`(correlation_meta 최상위 필드), `config.py`(correlation_topology_weight_enabled·correlation_topology_weight). 검증: `test_cross_host_correlation.py`·`test_topology.py`·`test_plan60_flags_off_regression.py`·`test_noise_gate_graph_integration.py`(감사 대역 kwarg 수정). **665 passed·arch_check 0**.
- **부수 수정(회귀 차단)**: `test_noise_gate_graph_integration.py`의 `_RecordingStore.record` 대역이 신규 correlation_meta kwarg 미수용 시 TypeError를 gate try/except가 삼켜 **감사 누락**되던 2건 수정(대역에 kwarg 추가).
- **관련**: D-109(E2 본체·이 정밀화가 "위상 가중 후속" 유예 해소), D-107(E4 DependencyGraph 자산 재사용), D-049(record 별도 필드 전례), Plan 60 §4.2·§13.1. (초안 결번 없음 — D-112 신규.)

## D-113. Plan 60 E3 2차 강화 — STL 분해 이상탐지 (statsmodels optional·HW 폴백)
- **결정일**: 2026-07-23 | **상태**: 확정 (구현 완료 · 사용자 지시 · 운영 반영은 폐쇄망 반입 협의 후)
- **배경**: E3(D-110)는 순수 Python Holt-Winters로 1차 구현하고 STL은 §5.2대로 "2차 강화·인프라 헬퍼"로 유예했다. 사용자가 후속 강화 중 STL 도입을 지시. STL(robust Loess 분해)은 계절 성분을 더 정교히 분리해 계절 피크 오탐을 추가로 낮춘다(§5.3).
- **결정**: 신규 인프라 `metric_stl.py::stl_anomaly_score(series, period, *, min_periods)` — **robust STL** 적합 후 `resid[-1] / pstdev(resid)`(최신 관측 잔차 z-score) 반환. **domain `anomaly.py`는 stdlib-only 불변**(STL은 infra만). **statsmodels lazy import**(함수 내부 — 미설치 시 앱 미충돌). `polestar_metric_baseline.compute_severity`가 `anomaly_stl_enabled`일 때만 STL 시도→성공 시 domain `severity_from_anomaly(score, z_high)`로 매핑(escalate-only 계약 불변), 실패(미설치·fit 오류·데이터부족·σ≤_SIGMA_EPS)→**순수 Python HW로 graceful 폴백**(사유 로그 — 침묵 강등 금지). **옵트인** `anomaly_stl_enabled`(기본 off→HW 비트동일·회귀 0).
- **의존성**: statsmodels는 `pyproject.toml [project.optional-dependencies].stl`에만(필수 dependencies 편입 금지 — 폐쇄망 반입 협의 미완). 미설치 CI는 `pytest.importorskip`으로 STL 테스트 자동 스킵.
- **실측 정정(외부 API)**: STL API는 `STL(series, period=P, robust=True).fit()`·`res.resid`(ndarray)로 지시와 일치. 단 **STL(Loess)은 상수열에서도 부동소수 잔차(σ≈4.6e-14)를 남겨** 정확히 0이 안 됨(순수 HW는 σ=0.0) → `_SIGMA_EPS=1e-9` 임계로 상수열을 None 처리(실 신호 σ는 O(1)이라 무영향).
- **운영 제약(명시)**: **운영 반영에는 statsmodels 폐쇄망 반입·보안 협의(행정 절차)가 별도로 필요.** 현재 dev만 설치(0.14.6), 기본 플래그 off라 반입 전까지 운영 경로 비트동일.
- **구현**: `metric_stl.py`(신규·lazy·graceful), `polestar_metric_baseline.py`(compute_severity STL 분기·HW 폴백), `config.py`(anomaly_stl_enabled), `pyproject.toml`(optional stl). 검증: `test_metric_stl.py`(importorskip·계절 비오탐·스파이크·부족·상수열)·`test_metric_stl_absence.py`(미설치 graceful)·`test_metric_baseline.py`(STL 라우팅·HW 폴백)·`test_plan60_flags_off_regression.py`(off→HW 비트동일). **677 passed·arch_check 0**.
- **관련**: D-110(E3 본체·STL "2차 강화" 유예 해소), D-035(결정적=판단), Plan 60 §5.2·§5.3·§13.1. (초안 결번 없음 — D-113 신규.)

## D-114. Plan 60 B-7 — 로컬 임베딩 주석 (L-2 근접중복 + L-4 토폴로지·텍스트 융합, D-035 주석 전용)
- **결정일**: 2026-07-23 | **상태**: 확정 (구현 완료 · 사용자 지시 · **운영 활성화는 보안팀 반입 협의 완료 선행**)
- **배경**: §15.3 L-2/L-4는 결정적 지문·그래프가 놓치는 **의미적 근접중복·root 귀속**을 로컬 임베딩으로 보강하는 옵션이었고, B-7(로컬 임베딩 반입)이 블로커였다. 사용자 인터뷰로 L-2+L-4 동시 착수 확정.
- **결정(§15.4 D-035 경계 절대 준수)**: **임베딩은 관측성·주석 전용 — 결정적 게이트 판단(SUPPRESS/PAGE/티어)·억제 지문·상관 군집·다홉 억제를 절대 변경하지 않는다.** 신규 infra `embedding_provider.py::AlarmEmbeddingProvider`(DI·lazy import·**로컬 디렉토리 전용 로드**[isdir 가드+`HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE`+`local_files_only` 지원 실측]·런타임 다운로드 금지·미가용 시 inert+경고 1회·LRU 캐시). **L-2**(워커): 신규 non-dup 이벤트가 최근 이벤트와 임베딩 유사(≥`embedding_similarity_threshold`)·다른 fingerprint면 `semantic_near_dup` **주석**(재발 count 병합 후보)을 decision_store 최상위 필드로 — **결정적 dedup(compute_fingerprint) 불변**. `_recent_event_texts` 스코프 deque(상한·만료 sweep). **L-4**(enricher): root 리소스 NAME과 알람 텍스트 유사도를 `noise_ctx["root_text_similarity"]` **주석**으로 — cascaded/root 판정 불변. **옵트인**(`semantic_dedup_annotation_enabled`·`topology_text_fusion_enabled` 기본 off)→off면 provider 미생성·비트동일.
- **폐쇄망·의존성**: sentence-transformers/torch는 `pyproject [project.optional-dependencies].semantic`만(필수 편입 금지·미반입 시 앱 정상·임베딩만 inert). **운영 반영엔 statsmodels와 동종의 폐쇄망 반입·보안 협의(행정 절차) 별도 필요** — 협의 문서 `docs/plan60_embedding_import_security_review.md`(모델·패키지·해시·라이선스·오프라인 근거·safetensors 우선/pickle 회피·배포 절차).
- **실측·모델 확정(2026-07-23 갱신)**: 사용자 HF 접근 승인 후 **`multilingual-e5-small` 확정**. 앞서 "HF hub 차단"은 실 네트워크가 아니라 세션 `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` 잔재였음이 재실측(curl 200)으로 확인 → e5-small 로컬 다운로드(dev 1회·466MB·safetensors·pickle 없음·SHA256 `7a77d5da…`·MIT)·**오프라인 로컬 로드 검증**(다운로드 0). 판별 실측(근접 6쌍·이질 6쌍): **e5-small 완전 분리(+0.041)**·단 마진이 bge-m3(+0.145)의 1/3.5로 좁음 → 기본 임계 0.85 부적정(이질 max 0.852) → **임계 0.87로 재튜닝(config·테스트 갱신)**. e5 `query:` prefix는 실익 미미(+0.041→0.045)라 미반영(문서화). 옵트인 실모델 테스트 `test_embedding_provider_realmodel.py`(`E5_MODEL_PATH` 설정 시 실행). **트레이드오프**: e5-small 유효하나 좁은 마진 — 운영 실데이터 오탐 시 bge-m3 교체 여지(security 문서 §5·§8). 협의 문서 아티팩트·해시·비교 갱신 완료.
- **D-035 준수 근거(구조·테스트)**: `src/alarm/domain/` 임베딩 참조 0건·`decide_notification` 시그니처에 임베딩 인자 없음(주석은 결정 산출 **뒤** 감사·noise_ctx 관측 필드로만). 주석 유무와 무관하게 tier/reason/priority/signals 비트동일 테스트(`TestD035TierInvariance`)·L-4 provider 유무와 cascaded/root 동일(`TestL4EnrichNoiseContextInvariance`). 자동 등록 쓰기 지점 없음(오염 루프 차단).
- **구현**: `embedding_provider.py`(신규 provider+`build_event_text`), `alarm_worker.py`(L-2·_recent_event_texts·_build_embedding_provider·주입), `alarm_context_enricher.py`(L-4 root_text_similarity), `decision_store.py`(semantic_annotation 최상위 필드), `alarm_graph.py`(AlarmState.semantic_annotation), `notification_gate.py`, `config.py`(5 플래그). 검증: `test_embedding_provider.py`·`test_semantic_annotation.py`(D-035 불변 단언)·flags-off. **713 passed·arch_check 0**.
- **관련**: §15.3(L-1~L-5)·§15.4(D-035 경계), D-035·D-048, D-084(E5-4 synonym_semantic 로컬 임베딩 패턴 재사용), D-107(E4 그래프·L-4), D-113(폐쇄망 optional·lazy 전례). (초안 결번 없음 — D-114 신규.)

> **Plan 60 Wave A·B·C + 정밀화·2차강화 구현 완료**: Wave A(E1·E4·E6=D-106~108)·Wave B(E2·E3=D-109~110)·Wave C(E5=D-111)·E2 정밀화(D-112)·E3 STL 2차(D-113)·B-7 임베딩 주석(D-114). **운영 활성화 선행조건**: STL·임베딩은 폐쇄망 반입·보안 협의 완료 후. 후속(E5 보조소스 union·외부 CI/CMDB·E3 주간 계절성·E2 워커 root name 보강·B-8 게이트 경계 probe[Plan 64 D-102 선행])는 범위 밖.

## D-116. Plan 60 E7 — 실측 ITSM 사례 기반 텍스트·주석 신호 보완 (억제≠삭제의 텍스트 확장)
- **결정일**: 2026-07-23 | **상태**: 확정 (계획 · B-9 코로보레이션 게이팅 확정 · **구현 완료 2026-07-27**, Plan 66 Wave 1-B) | **번호**: 등재 최댓값 D-114·**D-115는 Plan 65(목업 이벤트 주입 경로) 예약 유지** → E7은 **D-116** 부여(등재 직전 `## D-` 헤더·변경 이력 표 재확인 완료).
- **구현(2026-07-27 · Plan 66 Wave 1-B)**: 신규 domain `annotation_signal.py::extract_annotation_signal`(stdlib·정규식·순수). E7-a=워커 dedup 분기 하베스팅→`record_recurrence(annotation=…)`(재통보 0)·코로보레이션 게이팅(`planned_work` AND (`resolution` OR E2 correlated OR E5 `change_nearby`)→notification_policy step7.7 DASHBOARD 강등, 주석 단독 미강등). E7-b=`is_operational_alarm`(순수)→step0.5 SUPPRESS(애매 시 알람 간주). E7-c=`_build_alarm_event_from_payload` graceful 폴백(미식별 시 보수적 비-해소 severity·드롭/크래시 0)+site 토큰. E7-d=`correlation.signature_tokens` 사이트 차원(워커 주입·domain 순수)+chattering(`repeating`) 감사 라벨. 신규 플래그 5종 전부 기본 off(`annotation_harvest_enabled`·`annotation_planned_suppress`·`non_alarm_filter_enabled`·`format_tolerant_parsing_enabled`·`correlation_site_dimension_enabled`). **검증**: `tests/test_alarm` 688→**716 passed**(+28 E7)·29 skipped·0 failed, flags-off 비트동일(`TestE7AllFlagsOffBitIdentical`), `arch_check --ci` exit 0, 정책 모듈 annotation_signal 미import(순수성). **팀장 승인 설계 2건**: ①보수적 PAGE는 payload 빌더가 티어 미결정이라 severity 3 fabricate 대신 비-해소 severity 폴백(드롭·크래시 0 충족) ②`fleeting` 라벨은 self-heal 경로 변경 위험으로 본 Wave 보류(감사 전용·XS).
- **배경**: Plan 65 §2.4 실측 ITSM 알림 13샘플(S1~S8)을 현행 게이트(E1~E6)에 대조한 결과, **핵심 노이즈 신호의 상당수가 구조화 필드·DB가 아니라 「알람 텍스트」·「운영자 주석」에 있음**을 실측 확인. **결정적 발견**: `compute_fingerprint`가 주석 텍스트 미포함(`notification_policy.py` L51~64) → S3/S4/S6 주석 재발신(`=> 담당자…`·`예정된 IPL 작업으로 발생`·`서비스 영향 없음`)은 같은 지문으로 E1 dedup에 억제되나, 억제는 그래프 진입 **이전**(`alarm_worker.py` L481~494)에 ACK → **주석이 실어 온 계획작업·해소 신호가 폐기**되는 "억제≠삭제"의 텍스트 사각지대. E5는 `cmm_resource_lifecycle_history` **DB만** 읽어 이 텍스트 신호 미포착.
- **결정(§1.3·D-035 경계 계승 — 결정적=판단·LLM=주석·재현율 우선)**: 4계열 보완, 전부 **결정적 1차·LLM annotate-only·옵트인 기본 off·읽기전용·신규 모델 반입 없음**(기존 `alarm_analyzer` LLM 재사용 → B-7류 블로커 없음). **① E7-a 주석 하베스팅**[핵심]: 신규 domain `annotation_signal.py::extract_annotation_signal`(정규식·`planned_work`/`resolution`/`operator_ack` 마커)로, dedup 억제 시 ACK 이전에 주석 신호를 추출해 `record_recurrence(annotation=…)` 최상위 필드로 원 인시던트에 보존(재통보 0). **② E7-b 비알람 사전분류**: `is_operational_alarm(event)` 결정적 마커 판정(알람 마커 부재+비알람 마커 존재 시 비알람)→신규 step0.5 SUPPRESS·감사, **애매하면 알람 간주**(재현율 우선). **③ E7-c 파서 견고성+사이트 토큰**: `_build_alarm_event_from_payload` 이질 포맷 graceful 폴백→미식별 시 보수적 PAGE(드롭·크래시 0), 네트워크 장비 포맷(`\|\|(장애) <사이트명>`)에서 사이트 토큰 추출. **④ E7-d E2 사이트 상관 차원 + E1 ISA-18.2 chattering 정합**: `correlation.signature_tokens`에 사이트 가중 차원(E4 위상가중 패턴·워커 주입·domain 순수성 유지·존 경계 불변), chattering(fleeting/repeating) 감사 라벨(판정 무변경).
- **B-9 해소(코로보레이션 게이팅 확정)**: E7-a 계획-무해 주석의 억제는 **텍스트 단독으로 억제강화 절대 금지** — `planned_work` 마커 **AND** (E2 동시-다발 클러스터 소속 OR E5 `change_nearby` OR `resolution` 마커) 동시 충족 시에만 **DASHBOARD 강등**(SUPPRESS 아님·E4 하이브리드 정합). 주석 단독은 **강등 없이 주석만** 첨부. `resolution`은 `is_clear`(sev0)와 동일 취급 금지(폴스타 SSOT 보존)·자동 클로즈 없음(D-003). LLM은 결정적 억제를 뒤집지 못함(SOC 한계연구 정합).
- **회귀 통제**: 신규 플래그(`annotation_harvest_enabled`·`annotation_planned_suppress`·`non_alarm_filter_enabled`·`format_tolerant_parsing_enabled`·`correlation_site_dimension_enabled`) 전부 기본 off→현행 비트동일. 정책 모듈은 annotation_signal 미import(워커 주입). 심각도3 단락 불변. `test_plan60_flags_off_regression.py` E7 섹션·`arch_check --ci` 0.
- **문헌(§17.8)**: ISA-18.2/EEMUA 191(shelving·chattering·합리화·알람율 지표)·NLP 알람 텍스트마이닝(Drain/BERT)·LLM 인시던트 구조화 추출(ICPE 2025·EuroSys 2024)·산업 알람플러드 연관규칙(Control Eng. Practice 2023)·결정적 지문+조합해싱(2025)·SOC 비-actionable 필터링(arXiv 2208.12729)·Moogsoft 유지보수창·alarm correlation 유사도(Control Eng. Practice 2024).
- **관련**: Plan 60 §17·§8(B-9), Plan 65 §2.4(S1~S8 실측 재료)·D-115(예약), D-035·D-048(게이트 원칙), D-106(E1 dedup·record_recurrence)·D-108(E6 보강)·D-109·D-112(E2 상관·사이트 차원 재사용)·D-111(E5 change_nearby)·D-114(B-7 주석 전용 전례). Wave: E7-a→E7-b→E7-c→E7-d.

## D-117. Plan 60 E8 — L3 실호스트 조사 편입 (통보 보강·측정 기반 dedup·경계 상향)
- **결정일**: 2026-07-24 | **상태**: 확정 (계획 · 보안 인터뷰로 방향·통제 확정 · **미구현 — 착수 시 구현**, Plan 60 §18) | **번호**: 등재 최댓값 D-116·D-115는 Plan 65 예약 유지 → **D-117**(등재 직전 `## D-` 헤더·변경 이력 표 재확인 완료).
- **배경**: 사용자 지시("Plan 60에 **L3 단계 기능을 추가해서라도** 처리하라 — 메모리 90% 알람 시 `ps`로 상위 메모리 프로세스·`vmstat`로 사용량을 확인해 심각도·중요도·영향도를 판단하고 추가정보 전달 또는 중복제거. 보안 결정은 인터뷰로"). 종전 D-104(§14.4 경계 `uptime` probe만)·Plan 60 §16.4·§17.11은 **L3(OS 직접 명령)를 Plan 60 범위 밖(=Plan 64 §4.8 L3)** 으로 두었으나, 사용자가 **경계 변경을 명시 승인**하고 보안 인터뷰로 방향을 확정.
- **보안 인터뷰 확정(2026-07-24)**: ① **접근 경로 = A. 폴스타 에이전트 확장**(폴스타가 이미 호스트에 둔 에이전트에 read-only 스냅샷 노출 → 신규 접근경로 0·검증 채널 재사용, Plan 64 §7.1·Plan 51 §9; SSH 신규 수집기·로그 파이프라인 미채택). ② **실행 모델 = 둘 다**(게이트 동기 경계 probe[§14.4 D-104 확장·escalate-only·고중요·sev2·2s·캐시] + PAGE post-gate 비차단 보강[게이트 <10s 예산 무영향]). ③ **허용목록 = §7.2 전체 USE 프로파일**(kind 스코프 — 매 조사는 알람 kind별 프로파일만 실행). ④ **통제 = 최소권한 read-only·권고만**(D-003 유지·변경명령[renice/kill/dmesg -C 등] 수집기 물리 제외·자격증명 폴스타 채널측 전용·전 수집 감사+마스킹·nice/timeout 가드·Linux 1차).
- **결정(§18 E8)**: L3 실호스트 read-only 조사를 **노이즈 게이트 목적**으로 Plan 60에 편입. **세 용도(동일 1회 수집 공유)**: ① **통보 보강** — kind별 USE 분석→브리핑(top 프로세스·병목·OOM·근거 인용, Plan 64 §6 재사용). ② **측정 기반 dedup** — 상태지문 `{top_rss_pid, oom_flag, swap_active, sat_bucket}` 보존→재발 대조(동일·완화→억제 유지 / 악화→escalate, escalate-only·게이트 소급 변경 없음). ③ **경계 상향** — probe 부하/포화 이상→1단계 승격, severity_judge 결정적 신호(OOM=강, Plan 64 §5.2 재사용). **공용 자산 재사용(중복 구현 금지)**: L3 수집기 `host_diagnostic_collector.py`(Plan 64 §7·폴스타 에이전트 어댑터)·severity_judge(§5)·briefing(§6). Plan 60 신규 = 게이트 배선(§14.4 probe kind 확장·post-gate 보강 훅·측정 dedup 상태지문).
- **불변식(§1.3·D-003·D-035 계승)**: 읽기전용(§7.2 허용목록)·변경명령 물리 제외·조치는 Plan 64 §8 권고만(자동 실행 없음). **escalate-only**(억제 되돌리지 않음·하향 없음·심각도3 단락·유지보수 억제 불침범). 결정적=판단·LLM=서술(D-035). 옵트인 `l3_enrichment_enabled`·`gate_l3_probe_enabled`(기본 off)→비활성 시 L1/텍스트 경로 비트동일(회귀 0)·L3 부재/미노출→L1 폴백. 폐쇄망(폴스타 에이전트 채널만·외부 SaaS·신규 SSH 없음).
- **관련·선행**: 본 결정이 종전 **D-104**(§14.4 경계 probe — `uptime`만 → **kind별 USE 프로파일로 확장**)·**Plan 64 D-102/B-1**(L3 보안결정 — 노이즈 게이트 용도에 한해 **A안·최소권한 read-only로 확정**; Plan 64 전면 RCA 착수 시 이 방향과 정렬)을 확정·확장한다. Plan 60 §18·§16.4·§17.11·§14.4, Plan 64 §4.8.6·§5·§6·§7, D-108(E6 L1 보강)·D-116(E7 텍스트 신호)·D-035·D-048·D-003. **전면 RCA·인과추론·조치 거버넌스는 Plan 64 유지**(경계: Plan 60 §18=게이트 목적 escalate-only·kind 스코프 / Plan 64=다단계 RCA·remediation).

## D-118. SREAgent 통합 편입 — HolmesGPT 조사 기능을 `sre_agent/` 독립 패키지로 (plans/sre-agent/ 이관)
- **결정일**: 2026-07-24 | **상태**: 확정 (**패키지 골격 구현 완료 2026-07-27 · Plan 66 Wave 2-B 골격** — 조사 코어 W-A·서비스 run_service(2-C)·dispatcher(2-D)·원격 프로파일(R5)은 후속) | **번호**: 등재 최댓값 D-117 → **D-118**(등재 직전 `## D-` 헤더·변경 이력 표 재확인 완료).
- **골격 구현(2026-07-27 · Plan 66 Wave 2-B 골격)**: collectorinfra 최상위 독립 패키지 `sre_agent/`(자체 `pyproject.toml`·`.venv`[Python 3.13.1·holmesgpt 0.36.0 스택]·`sre_agent/scripts/arch_check.py`) — 구 SREAgent `src/sre_agent/`(settings·diagnosis·toolset_profiles·__init__) 이관. **경계 불변식 테스트로 고정**(`sre_agent/tests/test_boundary.py` — sre_agent→collectorinfra `src.` import 0 **및** 역방향 src→sre_agent import 0 양방향 단언). `run_service.py`·조사 코어 W-A는 Simplicity First로 미구현(2-C 소관·README 명시). 검증: `sre_agent/tests` 18 passed·`arch_check --ci` exit 0·본체 `test_alarm` 716 무회귀. AgentSettings Gemini 확장은 D-120 참조.
- **배경**: 사용자 지시("SREAgent를 별도 프로젝트로 구성하지 말고 collectorinfra와 통합한다. 계획 01~06을 이관하고, HolmesGPT 기능은 별도 폴더로 구분해 향후 별도 프로젝트로 분리할 수 있는 구성으로"). SREAgent(`/Users/cptkang/AIOps/SREAgent`)는 HolmesGPT 기반 장애 진단 에이전트로, 폴스타 MCP 연동·조사 서비스 노출(submit/poll)·원격 VM 2축(Prometheus+폴스타 MCP) 계획과 초기 구현(diagnosis·toolset_profiles·settings, arch_check)을 보유.
- **결정**: ① 계획 6종을 `plans/sre-agent/`로 이관 — 01(게이트)·03(목업)은 **대체됨**(기존 Plan 52/60·Plan 65가 담당), 02(HolmesGPT 조사)·04(`mcp_server` 고수준 도구 확장)·05(서비스 경계)·06(원격 VM 2축)은 유효(상세는 그 폴더 README). ② HolmesGPT 조사 기능은 최상위 **독립 패키지 `sre_agent/`**로 구현 — `mcp_server/` 전례(자체 pyproject·venv[>=3.13, holmesgpt 스택]·별도 프로세스·단일 엔트리 `run_service`)로 본체(>=3.11, LangGraph 스택)와 런타임 격리. ③ **경계 불변식**: 양방향 import 0(호출은 MCP 클라이언트뿐 — `sre_investigate_alarm`/`sre_get_investigation` submit/poll, `contract_version` 페이로드), 하향 데이터는 기존 `mcp_server`(조사용 고수준 도구 8종·args 마스킹·도메인 deny·전송 인증 확장)와 Prometheus만. ④ **분리 절차 = 폴더 복사 + URL 설정 변경**(계약·코드 무변경이 회귀 기준).
- **관련**: 게이트 자동 조사 트리거 훅(Plan 60 §14)의 위임처가 된다 — Plan 64의 `investigation_graph` 자체 구현은 sre_agent 위임으로 **대체 확정**(2026-07-24 계획 정합화: Plan 64 §0 통합 재편·Plan 60 §14.2/§18 갱신·Plan 62 C6/P4/§5.2·Plan 65 §4.3 `invest-trigger` — 코드 미착수, 문서 반영 완료). D-035(결정적=판단/LLM=보조) 계승 — 조사 LLM은 증거 수집·서술만, severity_judge는 결정적 escalate-only. SREAgent 측 결정 원문: 그쪽 `docs/02_decision.md` D-001~D-021(특히 D-013 MCP 일원화·D-016 연동 요건·D-019 원격 2축·D-021 통합 종결).

## D-119. PromQL 접근의 `mcp_server` 통합 — 관측 데이터 읽기 접근 경계 일원화 (holmesgpt 내장 Prometheus toolset 직결 미채택)
- **결정일**: 2026-07-27 | **상태**: 확정 (**코드 구현 완료 2026-07-27 · Plan 66 Wave 2-B′ R-A/R-B** — A/B 품질 게이트·Docker Prometheus 픽스처·hostname 정합 규약[D-020]은 Docker/LLM 부재로 보류) | **번호**: 등재 최댓값 D-118 → **D-119**(등재 직전 `## D-` 헤더·변경 이력 표 재확인 완료).
- **구현(2026-07-27 · Plan 66 Wave 2-B′)**: **R-B(mcp_server)** — `mcp_server/mcp_server/promql_tools.py`: 고수준 도구 2종(`prom_metric_instant(hostname,metric)`·`prom_metric_range(hostname,metric,window,step)`)이 `build_high_level_selector`로 **서버측 `{nodename="<hostname>"}` 결정적 조립**(§5-0·D-035 3차 방어 — LLM 라벨 미취급·bare 메트릭만 허용, 임의 PromQL은 거부→원시 도구 유도), 원시 5종(`prom_query`·`prom_query_range`·`prom_labels`·`prom_metadata`·`prom_series`)은 **`expose_raw_promql=true` 옵트인**(`execute_sql` 전례). `PrometheusConfig`(url·auth_header·query_timeout·expose_raw_promql) — **접속 설정·인증·timeout·감사 전부 mcp_server 측 일원화**(sre_agent 미보유). 반환 `{data,queried_at,source_kind:"prometheus"}`·오류 `{error}`(URL 미설정도 명시 오류·침묵 폴백 금지). **R-A(sre_agent)** — `remote_vm_profile()`(bash 미확장·prometheus/metrics 비활성 유지[PromQL은 mcp_server 경유]·mcp_server를 `Config.mcp_servers` 등록)·`DiagnosisAgent(mcp_servers=…)`(holmes Config 실측: `mcp_servers: Optional[dict[str,dict[str,Any]]]`·RemoteMCPToolset mode=sse/url/headers Bearer/health_check_tool)·`AgentSettings.polestar_mcp_url·polestar_mcp_token`(SecretStr, **prometheus 설정 제외**). **검증**: mcp_server/tests 103→**155 passed**(HTTP는 httpx MockTransport — nodename 조립·timeout 강제·원시 게이팅·반환/오류/감사 단위 고정)·sre_agent/tests 18→**30 passed**(mcp_servers 실 런타임 등록 `PrerequisiteCacheMode.DISABLED` 검증)·test_alarm 716 무회귀·arch 0·경계 0. **Docker 실 e2e 검증 완료(2026-07-28)**: Prometheus 픽스처(9190·§8.1) 기동 후 `TestDockerPrometheusIntegration`가 **실 HTTP로** 고수준 도구의 서버측 `{nodename="svr-web-01"}` 조립→mock 결정값 단언(`mock_memory_used_bytes=8589934592`·`mock_cpu_usage_percent` user=97.5/system=1.5·`mock_oom_kills_total=3`)·원시 옵트인 경로·timeout 서버 강제를 통과(`RUN_DOCKER_IT=1` 2 passed·기본 스위트 skip 유지·무회귀). **보류**: **D-119 채택 게이트(A/B 품질 열화 없음 실측 — 내장 toolset 대 mcp_server 도구 조사 완주 비교)**·hostname 스크레이프 규약 실측(D-020) — **GEMINI_API_KEY 필요**(LLM 조사 완주 전제).
- **배경**: 사용자 검토 지시("PromQL도 MCP 서버로 통합하는 것을 검토하라") 후 채택 지시. 종전 sre-agent/06(SREAgent D-019 인용)은 원격 VM 2축 중 Prometheus 축을 holmesgpt 내장 `prometheus/metrics` toolset 직결(A안)로 계획 — 이 경우 감사·자격증명이 `mcp_server` 경계 밖에 있고, hostname 정합(nodename 라벨)이 프롬프트 지침에만 의존한다.
- **결정**: ① `mcp_server`의 성격을 "폴스타 데이터 접근 전용"에서 **"관측 데이터 읽기 접근 경계"**(폴스타 + Prometheus)로 재정의 — 향후 관측 소스 추가도 별도 서버가 아니라 이 경계의 확장으로 편입한다. ② Prometheus 접근은 `mcp_server`가 노출하는 PromQL 도구로 일원화(B안): 도구 표면은 **hostname 앵커 고수준 도구 기본 노출 + 원시 PromQL 도구 옵트인**(`execute_sql` 기본 숨김 전례) — 세부는 R5 착수 시 확정. ③ **hostname 정합의 결정적 강제**: 고수준 도구가 `hostname(=server_name)` 인자에서 `{nodename="…"}` 필터를 서버측에서 조립(LLM이 라벨 미취급 — D-035·LLM 비결정성 대응의 3차 방어, 기존 1차 스크레이프 표준화·2차 지침은 유지). ④ 자격증명(`PROMETHEUS_AUTH_HEADER`)·쿼리 타임아웃·감사 로깅을 서버측 일원화 — 조사 중 전 데이터 접근(SQL·PromQL)이 `mcp_server` 감사 한 곳에 기록. ⑤ 지침 주입 일원화: 소스 선택·교차 검증 규칙(sre-agent/06 §2)을 MCP `llm_instructions` 한 곳으로(종전 `system_prompt_additions`/`llm_instructions` 양분 해소). ⑥ `sre_agent`의 하향 의존이 `mcp_server` 하나로 축소(분리 절차 = URL 1개 변경).
- **검증 게이트(채택 조건)**: R5(Plan 66 2-B′) 수용 기준에 편입 — 동일 조사 시나리오(sre-agent/06 §7)를 내장 toolset(A) 대 `mcp_server` 도구(B)로 완주 비교해 **B의 조사 품질 열화 없음을 실측** 확인. 열화 확인 시 A안 복귀(손실은 얇은 HTTP 래퍼뿐 — 복귀 비용 낮음).
- **근거**: 결정적 가드 우선(D-035)·감사 일원화·자격증명 보관 지점 축소·경계 단순화. **대안 기각**: A안 유지(감사·hostname 정합 약점), C안 별도 Prometheus MCP 서버(얇은 래퍼에 패키지·프로세스·인증 과중), A+B 병행(같은 소스 2경로 — 소스 혼동 배가).
- **관련**: D-118(`sre_agent` 경계 — 하향 의존 서술 갱신), SREAgent D-019(원격 2축 — **2축(소스) 구도는 유지**, Prometheus 축의 전송 경로만 `mcp_server` 경유로 갱신), SREAgent D-013(폴스타 MCP 일원화 — 관측 일원화로 확장). 반영: sre-agent/04(§1 스코프 재정의·§4.4)·06(§1~§3·§5·§6·§8)·02(§3·§8·§11 전송 경로 표기)·README(하향 의존)·Plan 66(R5·2-B′·§2).

## D-120. HolmesGPT 개발·테스트 LLM — Gemini API 경로 (운영 LLM 결정과 분리)
- **결정일**: 2026-07-27 | **상태**: 확정 (**구현 완료 2026-07-27 · Plan 66 Wave 2-0** — 실 Gemini 왕복은 GEMINI_API_KEY 부재로 보류) | **번호**: 등재 최댓값 D-119 → **D-120**(등재 직전 `## D-` 헤더·변경 이력 표 재확인 완료).
- **구현(2026-07-27 · Plan 66 Wave 2-0)**: `sre_agent/.venv`에 **holmesgpt 0.36.0 설치**(동봉 litellm **1.89.0**·google-genai 2.14.0·mcp 1.25.0 실측). `AgentSettings` 확장 — `investigation_llm_model: str = "gemini/gemini-2.0-flash"`·`gemini_api_key: SecretStr | None`(pydantic 필드 판정·`os.getenv` 금지). **모델 실측**: litellm 1.89.0 `supports_function_calling` — gemini-2.0-flash/2.5-flash/2.5-flash-lite=True, 1.5-flash=False. **모델 선택은 D-021 준수** — gemini-2.5-*는 D-021이 2026-06-17 deprecated·사용 금지로 못박아, 권장 기본 **gemini-2.0-flash 채택**(tool-calling 실측 True). 모델 문자열 prefix `gemini/`·env `GEMINI_API_KEY`(GOOGLE_API_KEY 우선) litellm 소스 실측 확정. 스모크 하네스 `sre_agent/scripts/smoke_llm.py`(①litellm tool-calling 왕복 ②DiagnosisAgent.ask — toolsets={} 데이터 통제). **GEMINI_API_KEY 미설정 → 두 단계 "보류" 명시 출력·graceful(rc 0)**. 검증: `sre_agent/tests` 18 passed(실 LLM 없이)·arch_check exit 0. **잔여**: 실 Gemini 왕복 e2e는 키 설정 후 `RUN_E2E=1` 옵트인.
- **배경**: 사용자 지시("HolmesGPT 테스트를 위해 Gemini API로 테스트할 수 있도록 코드를 작성하는 계획을 추가하라"). sre-agent/02는 유능한 tool-calling 모델을 전제하나 운영 LLM 환경(Plan 66 §7-1 — 폐쇄망·워커 LLM 우선 원칙과의 긴장)은 미확정이었고, 이 미확정이 Phase 2 전체를 차단하는 착수 게이트였다.
- **결정**: ① 개발·테스트 LLM으로 **Gemini API** 채택 — holmesgpt의 litellm 경유 규약(예상 `gemini/<model>` 모델 문자열·`GEMINI_API_KEY`, **착수 시 holmesgpt 0.36.0 동봉 litellm 버전으로 실측** — 실측 우선 원칙)으로 접속. ② `AgentSettings`에 `investigation_llm_model`·`gemini_api_key`(SecretStr) 추가 — pydantic 필드로만 판정(`os.getenv` 금지), 기본 모델은 착수 시 tool-calling 실측 후 결정. ③ **스모크 하네스** `sre_agent/scripts/smoke_llm.py`: (a) litellm 단독 tool-calling 왕복(함수 호출 1회 강제 → 호출·인자 파싱 확인) (b) `DiagnosisAgent` `ask` 1회(로컬 mock MCP 픽스처 대상) — holmesgpt 반입(P0-2) 직후 실행 가능한 최소 검증. ④ e2e는 `RUN_E2E=1` + API 키 존재 시 옵트인(기본 스위트 편입 금지·비용 가드 `investigation_hourly_budget` 적용). ⑤ **게이트 완화**: Plan 66 §7-1의 실체를 "운영 LLM 확정(운영 활성화 전 필요)"으로 축소 — Phase 2 개발·검증(D-119 A/B 품질 게이트 포함)은 Gemini로 선행 가능.
- **데이터 통제(절대 제약)**: Gemini API는 외부 SaaS — **개발·테스트 전용, 운영 투입 금지**. 외부 송신 입력은 **목업(Plan 65 생성기)·로컬 Docker 픽스처 데이터만** — 실 운영(폴스타) 데이터의 외부 API 송신 금지(폐쇄망·마스킹 원칙 정합). 결정적 차단: 테스트 환경 `mcp_server` config.toml에는 로컬 픽스처 소스만 등록(운영 `{NAME_UPPER}_CONNECTION` 미설정 → 소스 자동 비활성 — 기존 빈 값 비활성 규약 재사용, 물리적으로 실 데이터 접근 불가).
- **관련**: D-118(`sre_agent` 패키지)·D-119(품질 게이트의 실행 LLM으로 사용)·D-035(LLM=증거 수집·서술만 — 테스트 LLM 교체와 무관하게 불변). 반영: sre-agent/02 §2·§10.1·§12, Plan 66 R16·2-0·§7-1/7-5.

## D-121. 목업 폴스타 이벤트 주입 경로 — TCP 실경로 기본 + Redis 직주입 폴백 (Plan 65 목업 이벤트 생성기)
- **결정일**: 2026-07-27 | **상태**: 확정 (구현 완료 · Plan 66 Wave 1-A) | **번호**: 등재 최댓값 **D-120 → D-121**(등재 직전 `## D-` 헤더·「변경 이력」 표 재확인 — Plan 65 §7이 예약한 **D-115는 무효화**: 그 위에 D-116~D-120이 등재되어 채번 규칙[최댓값+1]상 D-121이 실제 다음 번호).
- **배경**: 폴스타 실계 없이 사전 정의 이벤트를 간단한 호출로 생성·주입해 노이즈 게이트(alarm_server→Redis Stream→AlarmWorker→게이트)의 캔슬링(SUPPRESS 등)을 사용자가 직접 반복 테스트하도록. 기존 `noise_gate_scenario_test.py`(Plan 52)는 API·Redis 직주입만 지원해 alarm_server TCP 수신부를 건너뛴다.
- **결정**: 주입 경로는 **TCP 실경로(localhost:9100·alarm_server 포함 전 구간)를 기본**으로, Redis 직주입(XADD `alarm:raw`)을 폴백으로 둔다. TCP 기본 근거: 기존 스크립트가 커버 못하는 유일한 구간이 TCP 수신부이고 "폴스타가 보낸 것과 동일" 취지에 부합. alarm_server 미기동 시 **침묵 폴백 금지** — 명확한 사유 출력 후 `--path redis` 안내(D-035·Known Mistakes). 파이프라인(src/) 코드 무변경(주입·관찰만).
- **구현**: 신규 `scripts/mock_polestar_events.py`(단일 파일·stdlib+기존 redis) — 시나리오 카탈로그(§4.1 기본 6종·§4.2 Plan 60 5종·§4.3 invest-trigger 스텁[R8 미구현 보류]) + `make_payload()`(payload 14키 실측 준수) + `TcpSender`/`RedisSender` + 판정기(`logs/alarm_decisions.jsonl` 폴링·tier·감사 필드 대조, decision_store 실측 스키마) + 대화형 메뉴 루프(`input()`·l/v/q) + `--send` 단발 모드. 테스트 `tests/test_scripts/`(42 passed·1 skipped[RUN_E2E 옵트인]), 가이드 `docs/20_plan60_feature_test_guide.md` §8.
- **주의·후속**: cascade(E4)·change-corr(E5) 시나리오는 AVAIL_DEPEND 토폴로지·`cmm_resource_lifecycle_history` 변경이력 픽스처가 현 도커 픽스처에 부재(Plan 65 §7 G-3) — 시나리오 정의·플래그 사전점검·의존성 명시까지만(픽스처 확장은 범위 밖). invest-trigger[12]는 R8(게이트 훅 submit 배선) 구현 후 활성.
- **관련**: Plan 65(전체)·Plan 66 R1/Wave 1-A, D-048/D-049(노이즈 게이트·decision_store 감사 — 판정 근거), D-003(읽기전용 — DB 미기록·Redis/TCP 주입만), D-035(판정 로직 무변경), D-116(E7 × 목업 교차 검증 1-C의 도구).

## D-122. mcp_server 조사용 고수준 도구 8종 노출 정책 (execute_sql 기본 비노출·도메인 deny·값 인자 SQL 조립)
- **결정일**: 2026-07-27 | **상태**: 확정 (구현 완료 · Plan 66 Wave 2-A · sre-agent/04 M-A/M-B — SREAgent D-014 인용을 collectorinfra 번호로 재부여) | **번호**: 등재 최댓값 **D-121 → D-122**(등재 직전 `## D-` 헤더·「변경 이력」 표 재확인).
- **배경**: HolmesGPT 조사 코어(sre_agent)가 폴스타 관측 데이터를 읽을 때, LLM에 raw SQL 작성을 열면 방언 오류·금지 조인(D-022/D-028)·D-035 위반 리스크가 크다. `mcp_server`(D-014 자체 MCP 서버)를 조사용 고수준 도구로 확장해 LLM이 **값 인자만** 주고 서버가 고정 SQL을 조립하도록 한다(D-119 관측 읽기 접근 경계 일원화의 폴스타 축).
- **결정**: ① **조사용 고수준 도구 8종**(`polestar_` 접두): `alarm_history`·`metric_trend`·`resource_status`·`topology`·`process_snapshot`(args 마스킹)·`os_config`·`change_history`·`condition_log`. 반환 JSON 문자열 `{rows,row_count,queried_at,source_kind,source,engine}`, 오류 `{"error":…}`(예외 비전파). **각 고정 SQL에 LIMIT/FETCH FIRST 명시**(max_rows 사후 슬라이스의 DB 전량 fetch 함정 회피 — SQL 자체 제한이 1차). ② **값 인자만 수신**(raw SQL 미수신) — 서버가 `_sql_literal`(널바이트 제거+`'` 이중화)로 보간(파라미터 바인딩 부재 계약을 단위 테스트로 고정). ③ **방언 분기 전부 서버 내부**(PG `polestar.`+LIMIT / DB2 `POLESTAR.`+FETCH FIRST·소문자화) — 소비자는 방언 무지. RESOURCE_CONF_ID 조인 미사용·hostname 브릿지·COALESCE(PLATFORM_RESOURCE_ID)(D-022/D-028/D-030). ④ **execute_sql 노출 정책**: **코드 기본값 `expose_execute_sql=False`**(SREAgent/HolmesGPT 배치는 고수준 도구만) — 단, 본체 `src/dbhub/client.py`가 execute_sql을 런타임 호출하므로 **이 배치의 config.toml만 opt-in(true)**로 두어 본체 무회귀(코드 기본과 배치 설정 분리). 노출 시 `validate_readonly`+`validate_polestar_domain`(RESOURCE_CONF_ID=CONFIGURATION_ID 조인·cmm_vendor/cmm_os/cmm_os_param 참조 deny) 이중 적용. ⑤ **process_snapshot 마스킹**: password/token/api_key/credential 값·접속문자열 비밀번호 마스킹·120자 절단.
- **구현**: 신규 `mcp_server/mcp_server/polestar_tools.py`(8도구+순수 SQL 빌더/마스킹), `security.py`(validate_polestar_domain), `tools.py`(execute_sql 옵트인 게이트), `config.py`(expose_execute_sql·process_api_base_url), `server.py`(register_polestar_tools). **검증**: `mcp_server/tests` 34→**103 passed·1 skipped**(단위: 이스케이프·validate_readonly 우회·도메인 deny·방언 SQL·마스킹·반환 계약), `tests/test_alarm` 716 무회귀, `arch_check` exit 0(mcp_server는 스캐너 범위 밖·본체와 미상호import). **Docker PG 통합은 미기동으로 skip**(`RUN_DOCKER_IT=1` 옵트인·사유 명시).
- **잔여·부채**: **실 PG 런타임 검증 완료(2026-07-28 · M-D PG 부채 일부 해소)** — Docker PG 픽스처(5434·infradb·`polestar.cmm_resource` 1581행) 기동 후 `TestDockerIntegration`가 고수준 도구를 **실 asyncpg 연결로** 호출해 반환 계약(`source_kind="polestar_db"`·`engine="postgresql"`)·PG LIMIT 방언·`polestar.` 스키마·`svr-web-01` 조회·행수/컬럼을 단언 통과(`RUN_DOCKER_IT=1`·기본 스위트 skip 유지). **DB2 런타임 검증은 계속 보류**(픽스처 부재). PromQL 도구(§4.4)는 D-119(구현·Docker e2e 완료). M-C(HolmesGPT `mcp_servers` 연동)는 D-119 R-A(구현). change_history event_time epoch 스케일은 실운영 편입 시 보정(코드 주석).
- **관련**: D-014(자체 MCP 서버 확장), D-119(관측 읽기 접근 경계 — 폴스타 축), D-003(읽기전용), D-035(LLM=값 인자·서버=SQL 조립), D-022/D-028/D-030(폴스타 조인 금지), sre-agent/04 §3~§9·Plan 66 R3/2-A.

## D-123. sre_agent 조사 서비스 — submit/poll 비동기 잡 계약 + 결정적 후처리 경계 (조사 루프 HolmesGPT 위임·severity_judge escalate-only·briefing 인용 검증)
- **결정일**: 2026-07-27 | **상태**: 확정 (구현 완료 · Plan 66 Wave 2-C/2-D · sre-agent/05·02 W-B — SREAgent D-009·D-017·D-018 인용 재부여) | **번호**: 등재 최댓값 **D-122 → D-123**(등재 직전 `## D-` 헤더·「변경 이력」 표 재확인).
- **배경**: 조사는 최대 300s까지 걸리나 collectorinfra MCP 호출 타임아웃은 60s — 동기 계약 불성립. 또한 조사의 판단(중요도 2차·브리핑 신뢰)은 LLM 서술이 아니라 결정적 로직이어야 한다(D-035). sre_agent 독립 패키지(D-118)에 조사 서비스 경계를 확정한다.
- **결정**: ① **submit/poll 비동기 잡 계약**(FastMCP·SSE·포트 9098·정적 Bearer): `sre_investigate_alarm(payload, wait_seconds=0)→{investigation_id, status: accepted|duplicate|rejected}`·`sre_get_investigation`·`sre_diagnose`·`sre_list_investigations`·`sre_health`(health_check_tool 대상). 트리거 페이로드 `contract_version: "1"`·필수 `event.serverName/hostname/severity`·선택 결측 수용. **콜백 없음**(1차 submit/poll만). ② **잡 저장소**(application): in-memory(값 bound + 키 만료 sweep) + **감사 JSONL 이중 기록** + **재기동 시 running/accepted 잡=failed(reason=restart)**(침묵 유실 금지). ③ **결정적 dispatcher**(폭주 방지): fingerprint **dedup TTL**·**동시 상한**(기본 2·세마포어)·**전체 타임아웃**(기본 300s·per-call 아닌 조사 전체)·**시간당 예산**·토큰 비용 감사. ④ **severity_judge**(domain 순수): 도구 **원시 출력** 시그니처 매칭(OOM/soft lockup/hung task/FS 오류/conntrack/segfault·원격은 Prometheus 카운터 대체)→`ImportanceVerdict`, **escalate-only 불변식**(`level=max(baseline,proposed)`·하향/소급 변경 절대 불가·LLM 최종 판정 위임 금지·D-035). ⑤ **briefing_builder**(결정적): 6요소 스키마 조립·**인용 결여 단정은 "가설"로 강등**·한계 서술 강제. ⑥ **DiagnosisResult 확장**: `tool_outputs: list[ToolCallRecord]`(holmesgpt 0.36.0 `LLMResult.tool_calls` 실측 — severity_judge가 원시 출력 참조). ⑦ **조사 실행부는 LLM 키(gemini_api_key) 부재 시 명시 스텁**(briefing/verdict에 "조사 미실행 — LLM 키 부재(스텁)" 노출·침묵 금지, 가드는 그대로 적용). **조치 실행 경로 부재**(D-003·D-011 — remediation은 권고만·미구현).
- **구현·검증**: `sre_agent/sre_agent/interface/mcp_service.py`·`application/{investigation_jobs,investigation_dispatcher,briefing_builder}.py`·`domain/severity_signatures.py`·`run_service.py`(유일 엔트리). 신규 플래그 전부 기본 off/보수값(`investigation_timeout_seconds=300`·`investigation_max_concurrent=2`·`investigation_dedup_ttl_seconds=None`·`investigation_hourly_budget=None`·`severity_judge_enabled=False`). **`sre_agent/tests` 30→140 passed**(dispatcher 가드·severity 시그니처·briefing 인용·계약·잡 저장소 sweep/재기동·경계). arch_check exit 0·**경계 양방향 import 0**(collectorinfra src 미참조·run_service 유일 엔트리·수신부/게이트/조치 실행 코드 부재 테스트 고정)·본체 test_alarm 716 무회귀. HTTP/LLM 없이 fake·픽스처로 결정적 검증.
- **잔여·보류**: 실 HolmesGPT 조사 완주(LLM 키 부재)·게이트 훅→submit 배선(R8·Plan 64 CW-A·Phase 3)·remediation_recommender(4-A)·실 Prometheus/폴스타 e2e. **관련**: D-118(sre_agent 패키지)·D-120(테스트 LLM)·D-119(mcp_servers 등록·관측 경계)·D-117(E8 severity_judge 재사용)·D-035(결정적=판단/LLM=서술)·D-003/D-011(읽기전용·조치 권고만), sre-agent/05 §3~§8·02 §4/§6/§7·Plan 66 R4/R6/R7·2-C/2-D.

## D-124. collectorinfra 게이트→조사 트리거 배선 (CW-A — 비차단 emit·submit/poll·브리핑 첨부·옵트인) [구 D-101 재편분]
- **결정일**: 2026-07-27 | **상태**: 확정 (구현 완료 · Plan 66 Wave 3-A · Plan 64 §0.2 CW-A·§0.3 — 구 D-101[트리거·오케스트레이션] 재편·축소 등재) | **번호**: 등재 최댓값 **D-123 → D-124**(등재 직전 `## D-` 헤더·「변경 이력」 표 재확인).
- **배경**: 노이즈 게이트가 대표 사건만 PAGE로 통보할 때, 그 사건을 `sre_agent` 조사 서비스(D-123)에 위임해 브리핑을 통보에 첨부한다(Plan 64 §0.2·Plan 60 §14.2). 조사 파이프라인은 재구현하지 않고(D-118) MCP 계약으로만 소비한다.
- **결정**: ① **collectorinfra 신규 자산은 배선뿐** — `src/alarm/infrastructure/sre_agent_client.py`(DBHubClient 패턴 미러링·SSE·재연결·Bearer)·`domain/investigation_payload.py`(`contract_version:"1"` 직렬화·순수)·`application/nodes/investigation_trigger.py`(트리거 노드)·`decision_store.record_investigation`·`alarm_notifier` 브리핑 첨부·config 플래그. **`investigation_graph.py`·`severity_judge.py`·`briefing_deliverer.py`·`remediation_recommender.py`는 생성하지 않음**(sre_agent 소관). ② **경계**: sre_agent 패키지 import 0(MCP JSON 계약만·grep 고정). ③ **옵트인 기본 off**(`investigation_trigger_enabled=False`·`investigation_trigger_min_tier="PAGE"`·service_url·token[SecretStr]·mcp_call/poll/total 타임아웃) → off면 게이트·통보 **비트동일**(회귀 0·트리거 노드 클라이언트 미접촉). ④ **전체 타임아웃 가드**(submit+poll 시퀀스에 `asyncio.wait_for(total_timeout)`·per-call 아님·기본 45s). ⑤ **비차단 graceful**: 서비스 다운/타임아웃/거부(rejected)/파싱실패 시 게이트 통보·판정 정상 완료·트리거만 graceful 실패(사유를 `record_investigation` 감사에 구조화·침묵 금지). investigation 감사 레코드는 `type` 보유로 티어 집계(`aggregate()`) 불변. ⑥ **읽기전용·조치 없음(D-003)**.
- **구현·검증**: `tests/test_alarm` 716→**740 passed**(+24 CW-A 계약: 필수필드 결측→rejected·중복→duplicate·서비스 다운 graceful·전체 타임아웃·브리핑 렌더, FakeSreClient로 sre_agent 미import). flags-off 비트동일(`test_plan60_flags_off_regression.py` F 섹션)·arch exit 0·경계 import 0.
- **설계 노트(팀장 판단·프로덕션 정련 권고)**: 현 구현은 브리핑을 **동일 통보에 첨부**(트리거 노드가 게이트와 notifier 사이에서 전체 타임아웃 내 poll 완료 후 첨부) — 게이트 자체는 무변경(<10s 예산 무영향)이나 **통보 발송이 조사 완료까지(최대 total_timeout) 대기**. 스텁 서비스(즉시 완료)·옵트인 off 기본에선 무해하나, **실 LLM(수십 초) 투입 시 PAGE 통보 지연** 발생. Plan 64 §6.2가 "브리핑 첨부 **또는 후속 메시지**"를 허용하므로, 운영 활성화 전 **즉시 통보 + 브리핑 후속 메시지**(진 fire-and-forget 백그라운드 태스크)로 전환 권고(별도 정련 Wave — 실 LLM 경로 착수 시).
- **CW-B/CW-C 구현(2026-07-28 · Plan 66 Wave 3-B)**: **CW-B(pull 위임)** — `SreAgentClient.diagnose(question, server_name?, hostname?, db_id?)`(`sre_diagnose` 호출·기존 poll 재사용) + 신규 routing intent **`fault_diagnosis`**(D-004 3곳 대칭: `_INTENT_ROUTE_MAP`·`build_graph` 노드 등록·`conditional_edges`, 전부 `fault_diagnosis_enabled` on일 때만) + 신규 종단 노드 `src/nodes/fault_diagnosis.py`(진단 의도→diagnose→poll→자연어 응답·`final_response`·LLM 재호출 없음). off면 라우터 프롬프트 미노출·노드 미배선·의도 발생 시 `data_query` 강등(비트동일). **CW-C(escalate-only 승격)** — `investigation_trigger`가 poll `verdict`(dict `escalate=True`/문자열 양쪽 방어 판정)를 소비해 `fault_escalation_enabled` on일 때 `investigation_escalation` state→`alarm_notifier` 상향 안내 블록 첨부. **`notification_decision` 반환/변경 없음**(tier/reason/priority 소급 변경·하향 금지·`test_escalate_only_decision_unchanged` 고정). 옵트인 `fault_diagnosis_enabled`·`fault_escalation_enabled` 기본 off. **검증**: `tests/test_alarm` 740→**756 passed**(CW-C 16)·router/graph/intent 146→**152 passed**(CW-B 6)·신규 파일 38 passed·arch 0·sre_agent import 0·flags-off 비트동일. (별건 사전존재 실패: `test_api/test_routes.py` 6[MagicMock config]·`test_multiturn` capability addendum 1[로컬 `.env`의 active_db_ids='polestar'로 소스 카탈로그 비어있지 않음 — 3-A/3-B 무관·클린 HEAD+.env에서도 동일 실패 실측].)
- **관련**: D-123(조사 서비스 계약)·D-118(sre_agent 경계)·D-004(시맨틱 라우팅·intent 3곳)·D-035·D-003, 구 D-101(재편·축소)·Plan 64 §0.2/§0.3/§6.2·Plan 60 §14.2·sre-agent/05 §4/§7·Plan 66 R8/3-A·3-B. **후속**: 즉시통보+후속 브리핑 정련(실 LLM 착수 시).

## D-125. MCP 전송 인증 — 정적 Bearer (mcp_server·sre_agent 조사 서비스 양쪽·클라이언트 헤더)
- **결정일**: 2026-07-28 | **상태**: 확정 (구현 완료 · Plan 66 Wave 3-D · sre-agent/04 M-D·§6-4·05 §5 — SREAgent D-015 인용 재부여) | **번호**: 등재 최댓값 **D-124 → D-125**(등재 직전 `## D-` 헤더·「변경 이력」 표 재확인).
- **배경**: `mcp_server`(관측 읽기 경계)·`sre_agent` 조사 서비스(9098)는 SSE로 노출된다 — 전송 인증이 없으면 네트워크 도달 가능한 누구나 도구를 호출한다. 정적 Bearer로 1차 인증(mTLS 승격은 협의 후·범위 밖).
- **결정**: ① **정적 Bearer 미들웨어**(Starlette ASGI `add_middleware` — FastMCP `run(transport='sse')`은 미들웨어 주입점 부재라 `sse_app()`+uvicorn 조립): `mcp_server`(`ServerConfig.bearer_token`·env `MCP_BEARER_TOKEN`)·`sre_agent`(2-C `service_bearer_token: SecretStr`·`StaticBearerAuthMiddleware` 기구현 — 재사용). ② **토큰 미설정 시 무인증 통과**(로컬/개발·네트워크 격리 전제 → 기존 동작 **비트동일·회귀 0**), 설정 시 `Authorization: Bearer <token>` 불일치 401(`{"error":"unauthorized"}`)·일치 통과. 비-HTTP scope(lifespan)는 토큰 무관 통과. ③ **클라이언트 헤더**: `src/dbhub/client.py`(`DBHubConfig.bearer_token`·env `DBHUB_BEARER_TOKEN`→`sse_client(headers=…)`)·`src/alarm/infrastructure/sre_agent_client.py`(3-A 기구현 Bearer). 토큰은 pydantic 필드(SecretStr)로만 판정(`os.getenv` 금지). ④ **읽기전용(D-003)**·토큰 부재 시 `headers=None` 원본 바이트동일.
- **구현·검증**: `mcp_server/mcp_server/{config,server,__main__}.py`·`pyproject.toml`(uvicorn)·`src/dbhub/client.py`·`src/config.py`(DBHubConfig.bearer_token 1줄). **mcp_server/tests 155→166 passed**(미들웨어: 토큰 없음 통과·불일치 401·일치 통과·클라이언트 헤더)·sre_agent/tests 140 무회귀·test_alarm 756 무회귀·arch 양쪽 exit 0·경계 유지. **부수 회귀 수정**: `tests/test_dbhub_integration.py` 15건(D-122 expose_execute_sql 기본값 변경 미포착 — HEAD에서 이미 실패)을 픽스처 `register_tools(mock_mcp, expose_execute_sql=True)` opt-in으로 교정 → 57 passed(`docs/18_known_mistakes.md` 등재).
- **잔여·보류**: 실 폴스타 DB 런타임 검증(PG·DB2·M-D)은 Docker/실인스턴스 부재로 보류(RUN_DOCKER_IT/실인스턴스 옵트인). mTLS 승격은 협의 후.
- **관련**: D-014(자체 MCP 서버)·D-122(고수준 도구·execute_sql 정책)·D-119(관측 경계)·D-123/D-124(조사 서비스·배선)·D-003, sre-agent/04 §6-4/§9 M-D·05 §5·Plan 66 R13/3-D.

## D-126. 실 DB 런타임 검증 스코프 PostgreSQL 한정 + Prometheus 픽스처 target-vm 승격 (node_exporter 설치형)
- **결정일**: 2026-07-28 | **상태**: 확정 (픽스처 승격·검증 완료) | **번호**: 등재 최댓값 D-125 → **D-126**(등재 직전 재확인 완료).
- **배경**: 사용자 지시("DB 검증은 DB2가 아닌 PostgreSQL로 진행하고, Prometheus는 도커에 인프라를 생성하고 node_exporter를 설치하여 테스트할 수 있는 환경을 구성하라"). DB2 실 검증은 로컬 픽스처 부재(ibm-db·이미지 제약)로 무기한 보류 상태였고, Prometheus 픽스처는 단독 node-exporter 컨테이너라 §5-1 수집 측 표준화(실 uname 경로)를 검증할 수 없었다.
- **결정**: ① M-D/R13의 실 DB 런타임 검증 수용 기준을 "PG·DB2 각 1회"에서 **PostgreSQL 1회 이상으로 한정** — PG는 Docker 픽스처 e2e로 기완료(a58e9b0). DB2 방언 경로(FETCH FIRST·집계 전 CAST·대문자 스키마·칼럼 소문자화)는 단위 테스트로 유지하고, DB2 실 검증은 실 b0 인스턴스 접근 확보 시 별도 항목으로 재개(운영 투입 전 필수 여부는 그 시점 재결정). ② Prometheus 픽스처를 **VM 유사 대상 인프라로 승격**: `testdata/prometheus/target-vm/`(ubuntu 24.04 베이스에 node_exporter v1.8.1을 빌드 시 반입·설치, compose `hostname: svr-web-01`)이 기존 단독 node-exporter 컨테이너를 대체 — `node_uname_info{nodename="svr-web-01"}` **실 uname 경로**가 가용해져 sre-agent/06 §5-1이 픽스처에서 실측 검증 가능(0차 static 라벨과 병존).
- **실측**: 빌드·재기동 후 스크레이프 타깃 2종(node·mock) up, `node_uname_info` nodename=svr-web-01(실 uname), `RUN_DOCKER_IT=1` e2e 2 passed 무회귀.
- **관련**: D-119(서버측 nodename 조립 — 0차 방어)·D-122(M-D 검증 부채)·D-125(전송 인증). 반영: sre-agent/04 §9 수용 기준 ⑤·06 §8.1·plans/66 R13/3-D.

## D-127. 과금 외부 API(Gemini) 호출 승인 게이트 — 무단 호출 금지
- **결정일**: 2026-07-28 | **상태**: 확정 (게이트 구현 완료) | **번호**: 등재 최댓값 D-126 → **D-127**(등재 직전 재확인 완료).
- **배경**: 사용자 지시("테스트 실행 시 Gemini API를 사용자 지시 없이 무단으로 호출하지 말 것 — 비용 발생, 무조건 사전 승인"). 실측: `tests/test_alarm/test_agentic_enricher_gemini_live.py`가 **키 존재만으로 기본 스위트에서 실 호출**되는 게이팅(skipif not _KEY)이었고 — 키가 `.encenv`에 상존하는 환경에서는 전체 pytest 실행마다 무단 과금 호출이 발생하는 구조 — D-120 스모크도 키만으로 실행 가능했다.
- **결정**: ① 과금 외부 API 실 호출(테스트·스모크·e2e·수동 스크립트)은 **사용자 명시 승인 후에만** — 실행 건마다 승인, 포괄 승인 없음. ② **코드 게이트(결정적 차단)**: 실 호출 경로는 전부 `RUN_E2E=1` 옵트인 뒤에 둔다 — 키 존재 게이팅 금지. live 테스트 pytestmark에 `RUN_E2E=1` 조건 추가, `smoke_llm.py`에 미승인 시 "보류(사용자 승인 필요)" 출력 후 종료 가드. ③ `RUN_E2E=1` 설정·실행은 승인 행위 — 에이전트는 승인 없이 설정하지 않는다(CLAUDE.md 핵심 원칙 등재). ④ 승인된 호출도 비용 감사(tokens/cost — D-123 dispatcher) 기록 유지.
- **검증**: 기본 스위트에서 live 테스트 3건 skip(사유에 D-127 명시)·스모크 미승인 실행 시 보류 종료(exit 0·실 호출 0) 실측.
- **구현 보강(2026-07-29)**: 최초 조치가 파일 1개(`test_agentic_enricher_gemini_live.py`)만 게이팅해 **기본 스위트 17건이 실 Gemini를 계속 호출**하던 것을 실측 발견 → ① 17건(`test_e2e_polestar.py` NLQ 10·`test_pipeline.py` 3·`test_nodes/test_result_organizer_mapping.py` 2·`test_xls_plan_integration.py` 2)에 `@pytest.mark.live_llm` 부여(RUN_E2E 미설정 시 skip) ② `tests/conftest.py`에 **전역 소켓 가드** 설치 — RUN_E2E 미설정 시 공인 IP 접속을 `connect` 이전에 차단(패킷 미발신)하고 어느 테스트가 어디로 나가려 했는지 명시해 실패시킨다(사설·루프백 허용, RUN_E2E=1이면 미설치). 개별 게이팅 누락이 곧 무단 호출이 되는 구조를 이중 방어로 대체.
- **관련**: D-120(테스트 LLM — 데이터 통제에 비용 통제 추가)·D-021(키 분리 보관)·D-123(비용 감사). 반영: CLAUDE.md·sre-agent/02 §10.1·plans/66 §5.

## D-129. 설정 웹UI 카탈로그 SSOT = pydantic 인트로스펙션 (시크릿 편집 차단 + "재시작 필요" 기본 반영 정책)
- **결정일**: 2026-07-29 | **상태**: 확정 (Phase 1~3 구현 완료 · Plan 68) | **번호**: 등재 최댓값 **D-127**, **D-128은 plans/67(단계적 LLM 질의 조립) 예약**(미등재) → **D-129**(등재 직전 `## D-` 헤더·「변경 이력」 표 재확인).
- **배경**: 기존 `/admin` 환경변수 탭은 `.env`에 **실존하는 키만** 평면 테이블로 나열하고 임의 키·임의 값을 무검증 저장했다(백업·롤백 없음, 마스킹 값 재저장 서버 가드 없음, 감사 기록 0건). 설정 코드 전수 분석 실측: 옵션은 `.env`∪`.env.example` 165키지만 **config.py에만 정의된 필드가 59개**(파일 파싱만으로는 열거 불가), 유효값 우선순위는 **OS env > `.encenv` > `.env` > 코드 기본값**이라 `.encenv` 관리 키는 `.env`를 고쳐도 기능적으로 무효, 저장 후 반영 시점은 경로마다 다르다(모듈 임포트 시점 `app.state.config` 고정·`build_graph` partial 주입 때문에 `load_config.cache_clear()`가 닿는 경로는 4곳뿐).
- **결정**: ① **카탈로그 SSOT = `AppConfig` 인트로스펙션**(`model_fields` 순회 = 17그룹 209필드 + top-level 15 = **224필드**, env 키는 `validation_alias.choices[0]` 우선). config.py에 필드를 추가하면 코드 수정 없이 UI에 자동 편입되며, 미분류 신규 필드의 기본 메타는 보수적으로 "재시작 필요"다. ② **시크릿 웹UI 편집 차단**(SecretStr + `.encenv.example` 등재 키 + `model_post_init` 보정 키 = 12키) — 정책 근거(D-070/D-071)뿐 아니라 **기능 근거**(`.env` 수정이 무효)로 필수. "설정됨/미설정" 상태만 read-only 표시. ③ **반영 정책은 "재시작 필요"가 기본** — 즉시 반영은 소비 지점 실측으로 확인된 7키만 예외(`SYNONYM_*` 5·`SCHEMA_CACHE_AUTO_GENERATE_DESCRIPTIONS`·`DBHUB_BEARER_TOKEN`)이며, 저장 응답과 UI 배너에 재시작 필요 키를 명시한다(침묵 금지). 재시작 버튼·핫 리로드 확대는 범위 밖. ④ **저장은 결정적 3단 검증 + 안전장치**: sanitize(개행·인라인 `#` 금지) → 타입 검증 → **그룹 클래스 단위 dry-run**(`GroupCls(_env_file=tmp)` — `AppConfig(_env_file=…)`는 nested에 전파되지 않음이 실측 확정, `ValidationError`와 `SettingsError`를 모두 포착) → `.env.bak` 백업·임시 파일·`os.replace` 원자 교체·실패 시 롤백 → `SETTINGS_UPDATE` 감사(비민감 키는 이전값→새값 쌍, 민감/시크릿 키는 키 이름만). 마스킹 값(`********`) 수신 시 해당 키는 무시해 원값을 보존한다. ⑤ **미소비 필드 16개**(코드가 읽지 않는 필드)는 `consumed=false`로 구분해 UI 기본 숨김(툴바 토글로만 노출) — 노브 환상 방지. ⑥ **`ENABLE_SEMANTIC_ROUTING` 무력화 버그 교정**: `os.getenv` 판정을 제거하고 `bool | None` 3상으로 전환(`enable_deepagent_orchestration`과 동일) — `.env`의 false가 무시되고 `ACTIVE_DB_IDS` 존재 시 강제 활성화되던 동작을 없애되, 미설정 시 자동 활성화는 보존한다.
- **구현·검증**: `src/api/settings_catalog.py`(신규)·`src/api/routes/admin.py`(`GET /admin/settings/schema` 신설·`PUT /admin/settings` 강화·`reset_keys`)·`src/domain/audit.py`(`SETTINGS_UPDATE`)·`src/static/{admin/dashboard.html,js/admin.js,css/style.css}`(아코디언·타입별 위젯·검색/필터·diff 모달·재시작 배너·401 처리·lazy 로드)·`src/config.py`·`.env`(`NOISE_ENABLE_NOISE_GATE` 중복 등재 정리)·`.env.example`(웹UI 안내). **T1~T6 43 passed**(T1 커버리지 게이트가 `카탈로그 ⊇ .env∪.env.example − 시크릿`을 실파싱 전수 단언 — 파일에만 키가 추가되면 CI 실패)·전체 스위트 무회귀·`arch_check --ci` 0. UI는 jsdom 하네스로 렌더링·저장 payload·인라인 오류 36건 확인(외부 호출 0 — D-127 준수).
- **주의**: OS env·`.encenv` 오버라이드는 카탈로그가 실효값과 함께 출처를 표시하지만 **저장해도 반영되지 않는다**(UI 경고 뱃지). 서버 CWD가 프로젝트 루트와 다르면 UI가 쓰는 `.env`와 pydantic이 읽는 `.env`가 갈라지므로 스키마 응답 `warnings`로 경고한다.
- **관련**: D-006(설정 계층화·자동 활성화)·D-070/D-071(시크릿 분리·기본 크레덴셜 제거)·D-127(과금 API 승인 게이트 — 본 작업은 외부 호출 0)·Plan 68(§1~§5·부록 A 224필드 확정표).
- **부기(2026-07-29, Plan 67 Phase 0)**: `ORCHESTRATOR_RECURSION_LIMIT`·`ALARM_DEFAULT_TEST_DB_ID` 필드 추가로 카탈로그 **224→226필드**(`tests/test_api/test_settings_catalog.py` 카운트 단언 갱신 완료). 별건 확인 필요: `ORCHESTRATOR_MAX_HISTORY_TURNS`는 실제 미소비인데 `UNCONSUMED_KEYS`에 누락되어 UI에 "소비 중"으로 표시됨(Plan 68 소관).

## D-130. HITL SQL 승인 파서·라우팅 fail-closed 전환 (기본 거부 — Plan 67 Phase 0)

- **결정일**: 2026-07-29 | **상태**: 확정 (구현 완료 · Plan 67 Phase 0-3 ⑧) | **번호**: 등재 최댓값 D-129 → **D-130** (`## D-` 헤더·「변경 이력」 표 재확인).
- **배경**: `src/api/routes/query.py`의 `_parse_approval`이 fail-open이었다 — 기본값 `approve` + prefix 매칭이라 "확인해보고 알려줘"의 "확인"이 승인으로 오탐되어 **미승인 SQL이 실행**될 수 있었다(`docs/regex_llm_conversion_review.md` A12 실측). `src/graph.py::route_after_approval`도 미지 action을 `query_executor`로 보내는 동일 방향의 기본값을 갖고 있었다.
- **결정**: 승인 게이트는 **fail-closed가 기본**이다. ①`_parse_approval`: 승인/거부 표현 하나로만 이루어진 입력(어미·문장부호 허용)만 인정하고, 그 외는 의도 불명으로 **reject** 처리(+WARNING 로그, "쿼리 실행이 취소되었습니다." 응답). 승인·거부 문자열 리스트는 기존 유지. ②`route_after_approval`: `approve`/`modify`만 명시 라우팅, 그 외(reject·None·미지 값)는 END — 명시 승인 없이 SQL 실행 금지. 비스트리밍 `/query`·SSE `/query/stream` 양 경로 공통 적용(`_build_turn_input_state` 공유).
- **UX 영향**: 종전에는 승인 대기 중 모호한 입력이 승인으로 처리됐으나, 이제 취소로 처리된다(재요청으로 회복 가능 — 오실행보다 안전 우선). 향후 R3-(ii)에서 승인 의도 파서의 LLM 전환 시에도 이 fail-closed 기본은 유지한다.
- **구현·검증**: `src/api/routes/query.py`·`src/graph.py`·테스트(`tests/test_multiturn/test_api_multiturn.py` 오탐 사례 단언, `tests/test_multiturn/test_graph_multiturn.py` fail-closed 라우팅 단언). Phase 0 결함 수정 14건과 동일 배치로 진행(전체 회귀 0 — worktree HEAD 기준선 대조 실측).
- **관련**: D-011(HITL 승인 게이트)·규칙 "침묵적 폴백 금지"·Plan 67 §5 Phase 0-3 ⑧·`docs/regex_llm_conversion_review.md` §7-1.

## D-131. SQL 지식 정본 일원화 + DB 레지스트리 (Plan 67 트랙 R — R1·R2 구현 완료)

- **결정일**: 2026-07-30 | **상태**: 확정 (R1·R2 구현 완료) | **번호**: Plan 67 예약 선점분(안내 라인 명시) — 등재 직전 `## D-` 헤더·「변경 이력」 재확인.
- **배경**: 동일 SQL 지식이 4곳 사본(폴스타 프롬프트 904L·조립기 메타·db_profiles 555L×3(gp↔yd diff 5줄)·semantic_models 4벌)으로 관리되어 1건 수정에 4곳 동기화가 필요했고, 실물 드리프트도 실측됨(Template A가 빈 EAV Hostname/IPaddress를 예시로 가르침 — D-058/D-061 모순). 신규 DB 편입은 9곳+ 동시 수정, 위치 키워드 튜플은 6곳 사본. 검사 도구(overfit_check)는 공용 4계층만 스캔해 사각지대 9건이 미게이트(`docs/polestar_bias_review.md`).
- **결정**: ① **지식 정본 = db_profiles 구조 + `config/knowledge/` delta 오버라이드(alias_deny/alias_extra)** — `catalog_builder`가 카탈로그를 파생하고, 기존 semantic_models 4벌과 **동등성 diff 0 실측 후** `load_semantic_model()`을 정본 파생 경로로 전환(실패 시 YAML 폴백+로그, semantic_models는 폴백 사본으로 강등·헤더 표시). 큐레이션 없는 DB는 카탈로그 미생성(미선별 컬럼 유입 차단). 프롬프트 지표 블록은 정본 렌더(바이트 동일 실증), assembler의 description 주석 파싱은 구조화 키(`resource_type:`)로 대체(폼필 골든 13건 바이트 보존). ② **DB 레지스트리 = `config/db_registry.yaml` 단일 등록점** — DB_DOMAINS·zones·위치 힌트·위치 키워드 튜플 6곳을 레지스트리 파생 단일 정의로 통합(위치 키워드는 라우팅 의도 분류에 미사용 — D-004 경계, 어댑터 훅 확장 아님 — D-089 경계). 편향 4건 해소(캐시 폴백·CORE_TABLES·피벗 부분문자열 판정·seeds 휴리스틱). ③ **overfit_check 확대가 완료 조건** — document·schema_cache·alarm/domain·mcp_server(전용 모듈 EXCLUDE 대칭)·tools 편입 + 운영 리터럴 카테고리(kbonecloud 등) 신설.
- **검증**: catalog_diff --ci 차이 0(4개 DB)·prompt_render_diff 바이트 동일·골든 회귀 171건(카탈로그 산출 모델로 실 조립 바이트 보존)·리허설 2종(별칭 1건 추가→런타임 반영 1파일 / 가짜 DB 편입 ≤2파일·튜플 정의처 grep 1곳)·전체 스위트 기준선 대조 실패 집합 완전 동일(회귀 0).
- **주의(잔여)**: R1-5(db_profiles 오버레이 YAML 분할)는 보류(gp↔yd 3줄 차이로 실익 부족 — 병합 함수·테스트만 확보). 프롬프트 잔여 블록(Template A/B 예제·알람 템플릿)은 diff≠0이라 옵트인 플래그 후속. `polestar_b0.yaml:456` 질의 예제의 EAV IPaddress 조회는 **b0 실측(D-058/D-061은 gp 실측) 후 판단** — 무단 수정 금지. semantic_models YAML 삭제 금지(폴백 + synonym_seeds 파생 원천).
- **관련**: D-067(이중 조립 엔진 금지)·D-076(SMQ)·D-088~D-091(DB-agnostic·어댑터)·D-004·Plan 67 §3.2 트랙 R·`docs/polestar_bias_review.md`.

## D-133. 표현·명칭 표준화 트랙 N — 질의 이력 검색(VQR형) + 계층 taxonomy (N2 구현 완료·N4 잔여)

- **결정일**: 2026-07-30 | **상태**: 확정 (N2 구현 완료, N4 후속) | **번호**: Plan 67 예약 선점분. ※ D-132(alarm 주석 LLM 전환)는 예약 유지(미등재).
- **배경**: 2026-07-29 표준화 문헌 조사(`docs/standardization_literature_review.md`) — 질의 이력 검색 편입 +40.2pt(단일 기법 최대), "선언 1곳 소비 N곳"(상용 5종 일치). 사용자 인터뷰로 N2·N4 채택(N1 임베딩 재배치·N3 공급원 교체 미채택, 임베딩은 IP-4 계측 후 판단).
- **결정**: ① **질의 이력 저장소** `query_history:{db_id}` — **검증된 쌍만** 편입(골드셋 26건+프로필 query_examples 29건 초기 적재, 운영 이력은 사람 확인 게이트 필수·자동 편입 원천 차단 — 쓰기는 명시 함수만, 위장 SQL 거부, 부분 저장 없음). ② **검색은 어휘·퍼지**(flex_match 재활용) — 임베딩 승격은 IP-4 적중률 계측 데이터로 별도 판단. ③ **few-shot 동적 선택** `TEXT2SQL_QUERY_HISTORY_FEWSHOT`(기본 OFF) — ON 시 폴백 LLM 경로의 고정 few-shot을 이력 상위 예시로 교체(동일 포맷터 경유), OFF 시 프롬프트 sha256 완전 일치 실증. ④ N4(계층 taxonomy — R1 카탈로그 `parent` 필드, 핵심 용어부터)는 후속.
- **주의**: 대칭 주입(multi_db_executor·subagents·deepagents 경로)은 S2에서 공유 헬퍼 레벨로 처리(D-066). 실 적재는 Redis 기동 후 `scripts/query_history_seed.py load`.
- **관련**: D-012(사람 승인 루프)·D-066·D-075/D-084(동의어)·Plan 67 §3.3·`docs/standardization_literature_review.md`.

---

## 변경 이력

> 각 변경의 상세 전문은 `docs/02_decision_full.md`(2026-07-16 아카이브) 참조.

| 날짜 | 결정 ID | 변경 내용 |
|------|---------|----------|
| 2026-07-30 | D-131·D-133 | **Plan 67 트랙 R(지식 정본+레지스트리)·트랙 N2(질의 이력 검색) 구현 완료 등재** — R1: 카탈로그 diff 0 실증 후 load_semantic_model 정본 파생 전환(semantic_models는 폴백 사본), Template A 빈 EAV 예제 드리프트 교정(D-058/D-061), assembler 주석 파싱→구조화 키. R2: db_registry.yaml 단일점·위치 튜플 6곳→1·편향 4건 해소·overfit 확대(+운영 리터럴 카테고리·tools 편입). N2: VQR형 저장소(검증 쌍만·사람 확인 게이트)+어휘·퍼지 검색+few-shot 동적 선택(기본 OFF·OFF 시 sha256 동일)+IP-4 계측. 부수: S1 tools 계층 10종+validate_sql 순수 함수 추출(D-128 트랙 S 선행), PolestarAdapter classify_metric_field 위임 훅. 검증: 전체 스위트 기준선 대조 실패 집합 완전 동일(회귀 0)·골든 171·리허설 2종·게이트 전부 그린. 잔여: N4 taxonomy·프롬프트 잔여 블록·b0.yaml EAV 예제 실측·D-128(S2)·D-132(alarm LLM) 예약 유지. 최댓값 D-131→**D-133**(D-132 결번 아님 — 예약). |
| 2026-07-29 | D-130 | **HITL SQL 승인 파서·라우팅 fail-closed 전환 (Plan 67 Phase 0)** — `_parse_approval` 기본 `approve`+prefix 매칭이 "확인해보고 알려줘"를 승인 오탐해 미승인 SQL 실행 가능하던 fail-open을 반전: 승인/거부 표현 단독 입력만 인정, 그 외 reject(+WARNING·취소 응답). `route_after_approval`도 approve/modify 외 전부 END(fail-closed). Phase 0 결함 수정 14건 배치의 일부(나머지: admin 접속문자열 비밀번호 마스킹 + `GET /admin/settings` `DB_CONNECTION_STRING` 평문 노출 차단, alarm 라우트 db_id 설정화, dead `try_semantic_compile` 삭제, `prior_rows` TypedDict 선언, 캐시 타입힌트·invalidate 키, JSON/SQL 추출 공용화(D-066 대칭 테스트 고정), 운영 URL 기본값 공란화(.env 이관), recursion_limit 명시 설정, orchestrator 프롬프트 tool 누락, mcp_server 폴스타 가드·도구 등록 게이트화(기본 현행 보존)). 회귀 0(worktree HEAD 대조)·arch/overfit CI 0·외부 호출 0(D-127). 최댓값 D-129→**D-130**. ※ Plan 67 예약 재부여: 트랙 R=D-131·alarm 주석 LLM=D-132·트랙 N=D-133 (D-129를 Plan 68이 등재하여 순차 이동, D-128 트랙 S 예약 유지). |
| 2026-07-29 | D-129 | **설정 웹UI 카탈로그 SSOT = pydantic 인트로스펙션 (Plan 68 Phase 1~3 구현 완료)** — `AppConfig.model_fields` 순회로 **224필드**(17그룹 209+top-level 15) 자동 도출(파일 파싱 불가한 config 전용 59키 포함·env 키는 alias 우선). 시크릿 12키 웹UI 편집 차단(정책 D-070/071 + 기능 근거: 우선순위 OS env>`.encenv`>`.env`라 수정이 무효). 반영 정책 **"재시작 필요" 기본**(즉시 반영 7키만 예외·저장 응답/배너에 명시, 침묵 금지). 저장은 sanitize→타입→**그룹 dry-run**(`GroupCls(_env_file=tmp)` — `AppConfig(_env_file=)`는 nested 미전파 실측·`SettingsError`도 포착)→`.env.bak` 백업·원자 교체·롤백→`SETTINGS_UPDATE` 감사(비민감 old→new·민감은 키 이름만)·마스킹 값 수신 시 원값 보존. 미소비 16키 `consumed=false` 기본 숨김. UI 전면 개편(아코디언·타입별 위젯·검색/필터·diff 모달·재시작 배너·401 처리·lazy). 부수: `ENABLE_SEMANTIC_ROUTING` `os.getenv` 판정 제거→`bool\|None`(`.env`의 false 무시 버그 교정·자동 활성 보존), `.env` `NOISE_ENABLE_NOISE_GATE` 중복 등재 정리. T1~T6 43 passed(T1 커버리지 게이트 전수 단언)·전체 무회귀·arch 0·외부 호출 0(D-127). 최댓값 D-127→**D-129**(D-128은 plans/67 예약). |
| 2026-07-28 | D-120·D-118 | **(갱신) MVP 실 완주 실증 + max_steps 가드·diagnose_fn 실 배선 교정** — 실 조사 미완주 원인 2건 실측 교정: ①`max_steps` 10→40 + step-limit graceful(`DiagnosisResult(incomplete=True)` 구조화 반환 — 하드실패·침묵 금지) ②`_default_diagnose_fn`에 `remote_vm_profile()`+mcp_servers(Bearer) 배선(2-C 스텁 우선 구현의 실 배선 누락). RUN_E2E e2e로 **실 Gemini(3.5-flash) 조사 완주 실증**(161s·promql 감사 37건·서버측 `{nodename="svr-web-01"}` 조립 실동작 — **D-119 실증**, A/B 게이트는 잔여)·한국어 인용 진단 산출. 조사 배치는 고수준 도구만 기동(`EXPOSE_EXECUTE_SQL/RAW_PROMQL=false` — raw 노출 시 LLM 방언 오류로 step 소진, D-122 재확인). sre_agent 144 passed·가드 단위 3(LLM 불요). ※ 실 호출분은 D-127 정책 지시와 시간 중첩 발생 — 이후 건별 사용자 승인 절차 적용. |
| 2026-07-28 | D-127 | **과금 외부 API(Gemini) 호출 승인 게이트** — 사용자 지시("테스트 시 Gemini 무단 호출 금지·무조건 사전 승인"). 실 호출은 건마다 사용자 승인 필수(포괄 승인 없음), 실 호출 경로 전부 `RUN_E2E=1` 옵트인 뒤(**키 존재 게이팅 금지** — agentic_enricher live 테스트가 키만으로 기본 스위트에서 실 호출되던 문제 실측·차단), smoke_llm 미승인 시 보류 종료 가드, RUN_E2E 설정=승인 행위(CLAUDE.md 등재). 검증: live 3건 skip·스모크 보류·실 호출 0 실측. 등재 최댓값 D-126→D-127. |
| 2026-07-28 | D-120 | **(갱신) Gemini 키 배선 실측 교정 + 기본 모델 gemini-3.5-flash 확정** — 키는 기설정(`.encenv` `LLM_GEMINI_API_KEY`·D-021 분리 보관)이었으나 sre_agent가 `GEMINI_API_KEY`+`.env`만 읽어 "미설정" 오판 → `AgentSettings` env_file `(".env",".encenv")`·`AliasChoices("GEMINI_API_KEY","LLM_GEMINI_API_KEY")` 확장(비밀 단일 보관 유지·복제 없음·CWD 기준이라 분리 후에도 동형). 모델: D-021 권장 gemini-2.0-flash **서버 퇴역 404 실측**·gemini-3.1-pro는 preview만 존재 → **ListModels 실측으로 gemini-3.5-flash 채택**(known_mistakes 등재 — 문서 권장치+가용 실측+실 왕복 3중 확인 원칙). 스모크 완주: [1/2] litellm tool-calling 실 왕복 OK(**HolmesGPT 성립 조건 첫 실 API 검증 — §7-1 판단 근거**)·[2/2] DiagnosisAgent.ask OK. sre_agent 140 passed 무회귀. |
| 2026-07-28 | D-126 | **실 DB 검증 PostgreSQL 한정 + Prometheus 픽스처 target-vm 승격** — 사용자 지시. ①M-D/R13 실 DB 수용 기준 PG·DB2 각 1회→**PG 1회 이상**(PG는 a58e9b0 기완료·DB2 방언은 단위 테스트 유지·실 검증은 b0 인스턴스 확보 시 별도) ②Prometheus 픽스처에 VM 유사 인프라 `target-vm`(ubuntu 24.04+node_exporter v1.8.1 빌드 반입 설치·hostname=svr-web-01) 생성 — 단독 node-exporter 컨테이너 대체, `node_uname_info` 실 uname 경로로 §5-1 수집 표준화 검증 가능(0차 static 라벨과 병존). 실측: 타깃 2종 up·`RUN_DOCKER_IT=1` e2e 2 passed 무회귀. 반영: sre-agent/04 §9⑤·06 §8.1·plans/66 R13/3-D. 최댓값 D-125→D-126. |
| 2026-07-28 | D-119·D-122 | **(검증) Docker 픽스처 실 e2e — PromQL·PG 고수준 도구 (Plan 66)** — Prometheus(9190·§8.1)·PG(5434·`cmm_resource` 1581행) 픽스처 기동 후 `TestDockerPrometheusIntegration`·`TestDockerIntegration` placeholder를 **실 단언 e2e로 교체**. PromQL: 서버측 `{nodename="svr-web-01"}` 조립→mock 결정값(memory 8589934592·cpu 97.5/1.5·oom 3)·원시 옵트인·timeout 강제. PG: 실 asyncpg 연결·반환 계약·PG LIMIT 방언·`polestar.` 스키마·svr-web-01. `RUN_DOCKER_IT=1` **2 passed**·기본 스위트 166/2 무회귀(연결 정보 env 주입·하드코딩 금지). **D-122 M-D PG 부채 일부 해소**(DB2 보류)·**D-119 PromQL 실 HTTP 검증**(A/B 품질 게이트는 GEMINI_API_KEY 대기). sre-agent/06 §8.1 포트 9190 반영. 신규 D-번호 없음(D-119/D-122 검증). |
| 2026-07-28 | D-125 | **MCP 전송 인증 정적 Bearer (구현 완료·Plan 66 Wave 3-D·sre-agent/04 M-D)** — mcp_server(`ServerConfig.bearer_token`·Starlette 미들웨어·`sse_app()`+uvicorn)·sre_agent(2-C `StaticBearerAuthMiddleware` 재사용)·클라이언트 헤더(dbhub `_auth_headers`·sre_agent_client 기구현). 토큰 미설정→무인증 통과(비트동일·회귀 0)·설정→불일치 401·일치 통과. mcp_server/tests 155→166·sre_agent 140·test_alarm 756 무회귀·arch 양쪽 0. 부수: `test_dbhub_integration.py` 15건(D-122 expose_execute_sql 미포착 회귀·HEAD 기실패) 픽스처 opt-in 교정→57 passed(known_mistakes 등재). 실 DB 검증(M-D) Docker 보류. SREAgent D-015 인용. 최댓값 D-124→D-125. |
| 2026-07-28 | D-124 | **(확장) 게이트→조사 배선 CW-B/CW-C 구현 (Plan 66 Wave 3-B)** — CW-B: `SreAgentClient.diagnose`(sre_diagnose·poll 재사용)+신규 intent `fault_diagnosis`(D-004 3곳 대칭·`fault_diagnosis_enabled` on 시만)+종단 노드. CW-C: `investigation_trigger`가 poll verdict.escalate 소비→`fault_escalation_enabled` on 시 escalate-only 후속 통보 승격(`notification_decision` 소급 변경·하향 없음). 옵트인 기본 off. test_alarm 740→756·router/graph 146→152·arch 0·sre_agent import 0·flags-off 비트동일. 사전존재 실패(test_api 6·test_multiturn 1[.env active_db_ids]) 3-A/3-B 무관 실측. |
| 2026-07-27 | D-124 | **collectorinfra 게이트→조사 트리거 배선 CW-A (구현 완료·Plan 66 Wave 3-A·구 D-101 재편분)** — `src/alarm/infrastructure/sre_agent_client.py`(DBHubClient 미러링·SSE·Bearer)·`domain/investigation_payload.py`(contract "1")·`nodes/investigation_trigger.py`·`decision_store.record_investigation`·`alarm_notifier` 브리핑 첨부·config 7플래그(전부 off). 게이트 훅 비차단 emit(tier≥min_tier)→submit/poll(전체 타임아웃 45s asyncio.wait_for)→브리핑 첨부+감사. 서비스 다운/타임아웃/rejected graceful(침묵 금지). sre_agent import 0·investigation_graph 등 미생성. test_alarm 716→740·flags-off 비트동일·arch 0. **설계 노트**: 브리핑을 동일 통보에 첨부(조사 완료까지 대기) — 실 LLM 지연 시 즉시통보+후속메시지로 정련 권고(§6.2 허용). 최댓값 D-123→D-124. |
| 2026-07-27 | D-123 | **sre_agent 조사 서비스 submit/poll 계약 + 결정적 후처리 경계 (구현 완료·Plan 66 Wave 2-C/2-D·sre-agent/05·02 W-B)** — FastMCP 서비스(9098·SSE·Bearer) 도구 5종·`contract_version "1"`·잡 저장소(in-memory+감사 JSONL·sweep·재기동 running→failed)·dispatcher(dedup TTL·동시 상한 2·전체 타임아웃 300s·시간당 예산·토큰 감사)·severity_judge(domain 순수·원시 출력 시그니처 매칭·escalate-only `level=max(baseline,proposed)`)·briefing_builder(6요소·인용 결여→가설 강등)·DiagnosisResult 확장(`tool_outputs`·holmesgpt 실측). LLM 키 부재 시 명시 스텁·조치 실행 경로 부재(D-003/D-011). 신규 플래그 전부 기본 off. `sre_agent/tests` 30→140 passed·arch 0·경계 양방향 import 0·test_alarm 716 무회귀. 잔여: 실 조사 완주(키)·게이트 훅 배선(R8/Phase 3)·remediation(4-A). SREAgent D-009/D-017/D-018 인용 재부여. 최댓값 D-122→D-123. |
| 2026-07-27 | D-119 | **(상태 갱신) PromQL mcp_server 통합 코드 구현 완료 (Plan 66 Wave 2-B′ R-A/R-B)** — R-B: `mcp_server/promql_tools.py` 고수준 2종(`prom_metric_instant/range`·서버측 `{nodename}` 결정적 조립·bare 메트릭만)+원시 5종(`expose_raw_promql` 옵트인)·`PrometheusConfig`(url/auth/timeout·mcp_server 측 일원화)·감사 파이프. R-A: `remote_vm_profile()`(bash 미확장·prometheus toolset 비활성·mcp_server를 Config.mcp_servers 등록)·`DiagnosisAgent(mcp_servers=)`(holmes Config 실측)·`AgentSettings.polestar_mcp_url/token`(prometheus 제외). mcp_server/tests 103→155·sre_agent/tests 18→30·test_alarm 716 무회귀·arch 0·경계 0. HTTP는 httpx MockTransport 단위. **보류**: Docker Prometheus 픽스처·MCP e2e·**A/B 품질 게이트**·hostname 규약(D-020) — Docker+GEMINI_API_KEY 필요. 상태 「미구현」→「코드 구현 완료(A/B 게이트 보류)」. |
| 2026-07-27 | D-120 | **(상태 갱신) Gemini 테스트 LLM 경로 구현 완료 (Plan 66 Wave 2-0)** — `sre_agent/.venv` holmesgpt 0.36.0 설치(동봉 litellm 1.89.0 실측)·`AgentSettings.investigation_llm_model`(`gemini/gemini-2.0-flash` — **D-021 준수**: gemini-2.5-* 사용 금지→권장 기본, tool-calling 실측 True)·`gemini_api_key`(SecretStr)·`smoke_llm.py`(litellm 왕복+DiagnosisAgent.ask). GEMINI_API_KEY 미설정→2단계 "보류" graceful. `sre_agent/tests` 18 passed·arch 0. 실 왕복 e2e는 키+RUN_E2E 옵트인 보류. 상태 「미구현」→「구현 완료(왕복 보류)」. |
| 2026-07-27 | D-118 | **(상태 갱신) sre_agent 패키지 골격 구현 완료 (Plan 66 Wave 2-B 골격)** — collectorinfra 최상위 독립 패키지 `sre_agent/`(자체 pyproject·`.venv` Python3.13+holmesgpt·arch_check) 생성, 구 SREAgent `src/sre_agent/`(settings·diagnosis·toolset_profiles) 이관. 경계 양방향 import 0 테스트 고정. run_service·조사 코어 W-A는 2-C 소관 미구현(Simplicity First). `sre_agent/tests` 18 passed·arch 0·본체 test_alarm 716 무회귀. 상태 「미구현」→「골격 구현 완료(조사 코어·서비스 후속)」. |
| 2026-07-27 | D-122 | **mcp_server 조사용 고수준 도구 8종 노출 정책 (구현 완료·Plan 66 Wave 2-A·sre-agent/04 M-A/M-B)** — `polestar_alarm_history/metric_trend/resource_status/topology/process_snapshot[마스킹]/os_config/change_history/condition_log`. 값 인자만 수신·서버가 고정 SQL 조립(`_sql_literal` 이스케이프)·각 SQL LIMIT/FETCH FIRST 명시·방언 분기 전부 서버 내부. execute_sql 코드 기본 비노출(`expose_execute_sql=False`)+이 배치 config.toml opt-in(본체 런타임 의존 무회귀)·노출 시 도메인 deny(RESOURCE_CONF_ID 조인·cmm_vendor/os/os_param). `mcp_server/tests` 34→103 passed·test_alarm 716 무회귀·arch 0. Docker PG 통합 skip(RUN_DOCKER_IT 옵트인). 잔여: 실 DB 검증(M-D·R13)·PromQL(R5)·M-C(2-C). SREAgent D-014 인용 재부여. 최댓값 D-121→D-122. |
| 2026-07-27 | D-121 | **목업 폴스타 이벤트 주입 경로 — TCP 실경로 기본 + Redis 폴백 (Plan 65 목업 생성기, 구현 완료·Plan 66 Wave 1-A)** — 신규 `scripts/mock_polestar_events.py`(카탈로그 12종·`make_payload` 14키 실측·TcpSender/RedisSender·JSONL 판정기·대화형 메뉴·`--send`)+`tests/test_scripts/`(42 passed·1 skipped)+`docs/20` §8. TCP 기본(alarm_server 전 구간)·Redis 폴백·침묵 폴백 금지(미기동 시 사유+`--path redis` 안내)·src/ 무변경. cascade/change-corr는 픽스처 부재(§7 G-3)로 정의·점검까지만, invest-trigger는 R8 후 활성. **채번**: Plan 65 §7 예약 D-115 무효화(그 위 D-116~120 등재)→최댓값 D-120+1=**D-121**. |
| 2026-07-27 | D-116 | **(상태 갱신) Plan 60 E7 구현 완료 (Plan 66 Wave 1-B)** — `annotation_signal.py`(순수)+E7-a 하베스팅/코로보레이션 게이팅(step7.7 DASHBOARD 강등)+E7-b `is_operational_alarm`(step0.5)+E7-c graceful 폴백/site 토큰+E7-d 사이트 상관 차원/chattering 감사. 플래그 5종 전부 기본 off. `tests/test_alarm` 688→**716 passed**(+28)·0 failed·flags-off 비트동일·`arch_check` exit 0·정책 모듈 순수성(annotation_signal 미import). 팀장 승인 설계: 보수적 severity 폴백(severity 3 fabricate 회피)·`fleeting` 라벨 보류(self-heal 경로 위험). 상태 「미구현」→「구현 완료」. |
| 2026-07-27 | D-120 | **HolmesGPT 개발·테스트 LLM = Gemini API 경로(운영 LLM 결정과 분리)** — 사용자 지시("Gemini API로 테스트할 수 있도록 코드 작성 계획 추가"). litellm 경유(예상 `gemini/<model>`·`GEMINI_API_KEY` — 착수 시 실측), `AgentSettings.investigation_llm_model`·`gemini_api_key`(SecretStr), 스모크 하네스 `sre_agent/scripts/smoke_llm.py`(litellm tool-calling 왕복 + DiagnosisAgent ask 1회·mock MCP 픽스처), e2e는 `RUN_E2E=1`+키 옵트인. **데이터 통제**: 외부 SaaS — 테스트 전용·운영 금지, 송신 입력은 목업·로컬 픽스처만(테스트 config에 운영 connection 미설정 → 소스 자동 비활성으로 물리 차단). **게이트 완화**: Plan 66 §7-1 실체를 "운영 LLM 확정"으로 축소 — Phase 2 개발은 Gemini로 선행 가능. 반영: sre-agent/02 §2/§10.1/§12·Plan 66 R16/2-0/§7. 등재 최댓값 D-119→D-120. |
| 2026-07-27 | D-119 | **PromQL 접근 `mcp_server` 통합 — 관측 데이터 읽기 접근 경계 일원화(holmesgpt 내장 Prometheus toolset 직결 미채택)** — 사용자 채택 지시("PromQL도 MCP 서버로 통합"). `mcp_server` 성격 재정의(폴스타 전용→관측 읽기 경계), Prometheus 접근=`mcp_server` 노출 PromQL 도구(hostname 앵커 고수준 기본+원시 옵트인 — `execute_sql` 전례), hostname 정합 서버측 결정적 강제(`{nodename=…}` 서버 조립 — D-035 3차 방어), 자격증명·타임아웃·감사 서버측 일원화, 지침 주입 `llm_instructions` 한 곳, `sre_agent` 하향 의존=`mcp_server` 1개(분리=URL 1개). 검증 게이트: R5(2-B′)에서 내장 toolset 대비 품질 열화 없음 실측 — 열화 시 A안 복귀. 대안 기각: A안 유지·별도 Prometheus 서버·병행. 반영: sre-agent/04·06·02·README·Plan 66. 등재 최댓값 D-118→D-119. |
| 2026-07-24 | D-118 | **(갱신) 계획 정합화 완료 — sre-agent 위임 방식으로 Plan 60·62·64·65 갱신** — 사용자 지시("sre-agent 계획과 60~65 검토 후 sre-agent를 이용하는 방식으로 계획 수정"). ①Plan 64 §0 통합 재편 신설(`investigation_graph` 자체 구현 **대체 확정**·섹션별 상태 매핑·collectorinfra 측 CW-A~C 배선[게이트 훅→`sre_investigate_alarm` submit/poll·브리핑 통보 첨부·pull `sre_diagnose` 위임·escalate-only 승격]·예약 D-101~103 재편) ②Plan 60 §14.2 훅 위임처를 Plan 50 push 훅에서 `sre_agent` MCP 계약으로 교체, §18.4/§18.6 공용 자산 소재 재정의(severity_judge·briefing=`sre_agent` 소재·MCP 소비 / 게이트 배선·L3 수집기=collectorinfra 자산) ③Plan 62 C6/P4/§5.2 반영 ④Plan 65 §4.3 `invest-trigger` 델타 편입(sre-agent/03 잔존분) ⑤역방향 델타: E8(D-117) 폴스타 에이전트 채널의 `polestar_host_snapshot` 노출 후보를 sre-agent/02·04·06에 기록. Plan 61·63은 접점 없음(무변경). 신규 채번 없음(D-118 상태 갱신). |
| 2026-07-24 | D-118 | **SREAgent 통합 편입 — 계획 6종 `plans/sre-agent/` 이관 + HolmesGPT 조사 기능 `sre_agent/` 독립 패키지 구성(분리 가능)** — 사용자 지시("별도 프로젝트로 구성하지 말고 통합, HolmesGPT 기능은 별도 폴더로 분리 가능 구성"). 01(게이트)·03(목업)=대체(기존 Plan 52/60·65 담당), 02(HolmesGPT ReAct 조사+결정적 후처리)·04(`mcp_server` 조사용 고수준 도구 8종·마스킹·도메인 deny·인증 확장, noise_signals 폐기)·05(submit/poll MCP 서비스·패키지 경계 기준 문서)·06(원격 VM=Prometheus+폴스타 MCP 2축·SSH 미채택)=유효. 경계: `mcp_server/` 전례(자체 pyproject·venv >=3.13·별도 프로세스·엔트리 `run_service` 단일), 양방향 import 0·통신은 MCP 계약뿐(`contract_version`), 분리=폴더 복사+URL 변경. 게이트 훅(Plan 60 §14) 자동 조사 위임처 — Plan 64 `investigation_graph` 대체 가능(착수 시 경계 재확인). 이관 문서 내 D-번호는 SREAgent 체계 인용(README 명시). 등재 최댓값 D-117→D-118. |
| 2026-07-24 | D-117 | **Plan 60 E8 L3 실호스트 조사 편입 (계획·미구현·보안 인터뷰로 방향 확정)** — 사용자 지시("Plan 60에 L3 단계 기능을 추가해서라도 처리하라, 보안 결정은 인터뷰로"). 메모리 90% 알람 예시(`ps` top 메모리 프로세스·`vmstat` 사용량)로 심각도·중요도·영향도 판단·추가정보 전달·중복제거. **보안 인터뷰 확정**: ①접근=폴스타 에이전트 확장(신규 접근경로 0·B-1 A안) ②실행=둘 다(게이트 동기 경계 probe §14.4 확장 + PAGE post-gate 비차단 보강) ③허용목록=§7.2 전체 USE 프로파일(kind 스코프) ④통제=최소권한 read-only·권고만(D-003 유지·변경명령 물리 제외·감사+마스킹). **경계 개정**: D-104(경계 uptime probe만)·§16.4·§17.11의 「L3=Plan 60 범위 밖」→ 노이즈 게이트 목적(통보 보강·측정 dedup·경계 상향)으로 편입. 세 용도 동일 1회 수집 공유(상태지문 대조 dedup=악화 시만 escalate-only). 공용 자산 재사용(Plan 64 §7 수집기·§5 severity_judge·§6 브리핑·중복 구현 금지). D-104·Plan 64 D-102/B-1을 A안·최소권한 read-only로 확정·확장. escalate-only·옵트인 기본 off·회귀 0. 전면 RCA·조치는 Plan 64 유지. 등재 최댓값 D-116→D-117. |
| 2026-07-23 | D-116 | **Plan 60 E7 실측 ITSM 사례 기반 텍스트·주석 신호 보완 (계획·미구현·B-9 코로보레이션 게이팅 확정)** — 사용자 지시("Plan 65 alert 사례로 Plan 60 보완, 문헌 검색"). Plan 65 §2.4 실측 13샘플(S1~S8)을 E1~E6에 대조. **결정적 발견**: `compute_fingerprint`가 주석 텍스트 미포함(L51~64)→S3/S4/S6 주석 재발신은 E1 dedup 억제되나 주석(계획작업·해소 신호)이 그래프 진입 전 ACK로 폐기 = "억제≠삭제"의 텍스트 사각지대. 4계열: **E7-a**(주석 하베스팅 — 억제하되 신호 보존·`annotation_signal.py`·`record_recurrence(annotation)`), **E7-b**(비알람 사전분류 `is_operational_alarm`·애매 시 알람 간주), **E7-c**(이질 포맷 graceful 폴백→보수적 PAGE·사이트 토큰), **E7-d**(E2 사이트 상관 차원+ISA-18.2 chattering 감사). 전부 결정적 1차·LLM annotate-only·재현율 우선·옵트인 기본 off·신규 모델 반입 없음. **B-9 확정**: 계획-무해 억제는 코로보레이션 게이팅(주석 단독 강등 금지·E2/E5/resolution 동시 충족 시에만 DASHBOARD 강등). 문헌: ISA-18.2/EEMUA·NLP 텍스트마이닝·LLM 구조화 추출·산업 알람플러드 연관규칙·SOC 비-actionable 필터·Moogsoft 유지보수창. **번호**: 등재 최댓값 D-114·D-115는 Plan 65 예약→D-116. 착수 시 구현·회귀 0 검증 예정. |
| 2026-07-23 | D-114 | **Plan 60 B-7 로컬 임베딩 주석 — L-2 근접중복 + L-4 토폴로지·텍스트 융합 (D-035 주석 전용)** — 사용자 지시. 신규 infra `AlarmEmbeddingProvider`(DI·lazy·**로컬 디렉토리 전용**·isdir+offline env·다운로드 금지·inert graceful·LRU). **L-2**(워커): 의미적 근접중복→`semantic_near_dup` 주석(decision_store 최상위·재발 count 병합 후보)·**결정적 지문 불변**·`_recent_event_texts` sweep. **L-4**(enricher): root NAME↔알람 텍스트 유사도→`noise_ctx["root_text_similarity"]` 주석·cascaded/root 판정 불변. **§15.4 D-035 절대 준수**: domain 임베딩 참조 0·decide_notification 인자 없음·티어 비트동일 단언·자동 등록 쓰기 없음. 옵트인 기본 off(회귀 0). sentence-transformers는 optional `semantic` 그룹. 실측·**모델 확정(multilingual-e5-small·2026-07-23)**: HF 접근 승인 후 e5-small 로컬 다운로드(466MB·safetensors·SHA256·MIT)·오프라인 로컬 로드 검증·완전 분리(+0.041)·마진이 bge-m3(+0.145)보다 좁아 **임계 0.85→0.87 재튜닝**·prefix 실익 미미 미반영·옵트인 실모델 테스트 추가. 트레이드오프(좁은 마진→운영 오탐 시 bge-m3 교체 여지) 문서화. **운영 활성화엔 보안팀 반입 협의 선행**(`docs/plan60_embedding_import_security_review.md`). 713 passed·실모델 옵트인 4 skipped·arch 0. |
| 2026-07-23 | D-113 | **Plan 60 E3 2차 강화 — STL 분해 이상탐지 (statsmodels optional·HW 폴백)** — 사용자 지시. 신규 infra `metric_stl.py::stl_anomaly_score`(robust STL·`resid[-1]/pstdev`·**statsmodels lazy import**·미설치/실패→None+로그). `compute_severity`가 `anomaly_stl_enabled`일 때만 STL→domain `severity_from_anomaly` 매핑(escalate-only 불변), 실패→순수 HW graceful 폴백(사유 로그). **domain anomaly.py 불변**·옵트인 기본 off(HW 비트동일). statsmodels는 `[project.optional-dependencies].stl`만(필수 편입 금지). 실측: STL Loess 상수열 잔차 σ≈4.6e-14→`_SIGMA_EPS=1e-9` 가드. **운영 반영엔 폐쇄망 반입·보안 협의(행정) 별도 필요.** 677 passed·arch 0. |
| 2026-07-23 | D-112 | **Plan 60 E2 정밀화 — 상관 클러스터 메타 감사 + 위상 가중 (D-109 후속 완결)** — 사용자 인터뷰 확정 2건. ①메타 감사: `match_cluster`→`(idx,score)`·`_detect_correlated_storm`→`(correlated,meta)`, decision_store `correlation_meta` **최상위 필드**(대표 지문·member_seq·유사도·signals 밖·recurrence 전례). ②위상 가중: E4 `DependencyGraph.id_of`/`is_related` 추가, `match_cluster(adjacent,topo_weight)` 인접 클러스터 보너스, correlation.py는 topology 미import(워커-주입·§10), 워커 그래프 자체 캐시, 옵트인 `correlation_topology_weight_enabled`(기본 off·회귀 0), B-6 존 경계 불변. **Known Mistakes #1 실측**: id_of는 엣지 보유(자식) 서버만 해소·root는 name 부재→보너스 미발동·Jaccard 폴백(버그 아닌 우아한 열화). 부수: gate 감사 대역 kwarg 누락(TypeError 삼킴→감사 누락) 2건 수정. 665 passed·arch 0. |
| 2026-07-22 | D-111 | **Plan 60 E5 변경/구성 이벤트 상관 (Wave C · B-2 폴스타 변경이력)** — 선조사(폴스타 DB 실측): `cmm_resource_lifecycle_history`(resource_id·event_time·lifecycle_type·description) 등 변경이력 테이블 실재·적합(dev 데이터는 합성 placeholder). 신규 infra `change_feed.py`(fetch_recent_changes·읽기전용·graceful)+domain `change_correlation.py`(overlay_changes·순수). `_NOISE_CTX_KEYS`에 change_nearby/change_candidates, 게이트 step9 promote("변경 근접(원인성)"·**억제 아님·승격만**·change 모듈 미import). signals 미확장. off→변경조회 미수행·비트동일. 1차=cmm_resource_lifecycle_history만. **복구 이력**: ux_improvement 병합이 미커밋 Wave B를 스태시 대피→복원·E5 재적용·재검증(634 passed) 무손실. 초안 D-081 결번→D-111. |
| 2026-07-22 | D-110 | **Plan 60 E3 동적 baseline 이상탐지 (Wave B · B-3 순수 Python HW)** — 신규 domain `anomaly.py`(math/statistics만·additive Holt-Winters)+infra `polestar_metric_baseline.py`(cmm_metric_stat_h 읽기전용·Redis 캐시). 배선: 계산=enricher gather 5번째 코루틴→AlarmState.anomaly_severity, 반영=analyzer 후처리 상향 가드(dynamic_baseline_enabled AND enable_ai_severity_boost·상향 전용). **게이트 무변경**(ai_message_severity 슬롯 공급). classify_alarm_kind 화이트리스트(`kind in METRIC_SOURCE_BY_KIND`)로 1차 CPU/메모리만. prometheus_client 미배선(§5.2=cmm_metric_stat). 초안 D-079 결번→D-110. |
| 2026-07-22 | D-109 | **Plan 60 E2 크로스-호스트 상관 (Wave B · B-6 존 경계)** — 신규 domain `correlation.py`(stdlib·signature_tokens[server 제외]·jaccard·match_cluster[동점 first_ts]). 워커 `_detect_correlated_storm`(온라인 그리디·첫 도착=대표·db_id 존 스코프·min_cluster_size번째부터 억제·버퍼 sweep+상한)·`_detect_storm`과 독립 병존. 게이트 step7.5(cross_host_correlation_enabled and correlated→SUPPRESS·storm 사유와 구분). sig_label은 domain scan_signature_severity(정책 순수성). E2 1차=필드 Jaccard만(위상 가중 후속). off→_detect_storm 비트동일. 초안 D-078 결번→D-109. |
| 2026-07-22 | D-101~104 | **ux_improvement 브랜치 병합(골든셋 실데이터 수렴)** — 번호 충돌로 구 D-084~087을 D-101~104로 재부여(팀장 번호 우선). D-101(이번 턴 원문 위치 힌트 결정적 DB 고정+맥락 오염 차단, 구 D-084), D-102(지난 N개월 범위 해석 `resolve_stat_month_range`, 구 D-085), D-103(사용률 집계 크래시 면역 DOUBLE+값 게이트 SQL0413N, 구 D-086), D-104(생성 SQL 한글 토큰 잔존 validator 차단, 구 D-087). 폴스타 조립기/프롬프트는 어댑터 계층(D-089)으로 이식, `_compile_c` 알람 SELECT는 D-100 채택. 코드 주석 D-번호도 매핑 갱신. |
| 2026-07-21 | D-108 | **Plan 60 E6 통보 컨텍스트 보강 L1 선구현 (Wave A)** — classify_alarm_kind cpu\|memory→+disk/network/process/log, 신규 domain `enrichment_profile.py`, notifier kind별 보강 블록 일반화(cpu/memory 표 비트동일), disk/network=host-wide 스냅샷 참고·process/log=요지만(graceful·신규 SQL 0), 메시지형 LLM 분류는 결정적 프로파일 대체(서술 전용·후속). 옵트인 message_enrichment_enabled(기본 off·회귀 0)·cpu/memory 전용 가드. L3는 Plan 64 §4.8. 초안 D-105→예약충돌로 D-108 재부여. |
| 2026-07-21 | D-107 | **Plan 60 E4 토폴로지 그래프 + 다홉 하이브리드 억제 (Wave A · B-1·B-5 확정)** — 신규 domain `topology.py`(stdlib)+인프라 `topology_loader.py`(정적 엣지 장기캐시+동적 AVAIL_STATUS IN 조회). 게이트 step6.4: cascaded면 root_notified→SUPPRESS/미통보→DASHBOARD, 미제공→1홉 폴백. root_notified는 enricher가 worker `_active_firings`로 신선 산출. 정책 모듈 topology import 금지. 1차=gp/yd(PostgreSQL), b0→1홉 폴백. signals Wave A 일괄 확장(cascaded·root_resource·correlated). B-1=AVAIL_DEPEND 단독·B-5=하이브리드. 초안 D-080 결번→D-107. |
| 2026-07-21 | D-106 | **Plan 60 E1 재발생 dedup 관측성 강화 (Wave A)** — `_gate_dedup` dict화({first_seen,last_notified,last_seen,count}), TTL 비교=last_notified 고정창(슬라이딩 변질 회귀 방지)·만료 sweep=last_seen, `_is_duplicate_fingerprint`(is_dup,meta) tuple, `record_recurrence`(type=recurrence) 감사·aggregate 비-decision 일반 제외, 재통보 시 대표 알람 재발 표기. 억제 판정 비트동일. 옵트인 불필요(게이트 off면 미진입). 초안 D-077 결번→D-106. |
| 2026-07-21 | D-100 | **질의 전 항목 결과 표시 — 하위조회 컨텍스트 확장 + 서버 키 병합** — 알람 조회가 서버명·알람명·심각도를 결정적 포함(`_compile_c`), `result_aggregator`가 여러 하위 조회를 공통 서버 키로 병합(행수 최소 base 스코프, 대표 1행), output_generator 전체 컬럼 표시 강제, 최상급 순위 LIMIT 1. 부작용(알람명이 선행 스코프 오염)은 `is_server_identity_col` 엄격 판정으로 차단. 라이브: 서버명·알람명·심각도·제조사·일련번호·CPU평균 한 행 표시. |
| 2026-07-20 | D-099 | **선행 스코프+메트릭 순위 질의 결정적 조립 편입** — 가드 5종에도 6번째 변종(모순 alias 조건 → 항상 NULL → NULLS FIRST로 임의 서버 1위, 재시도 예산 소진) 발생 → 울타리 확장 대신 트랙 C 컴파일러(D-076)로 편입. 조립기에 server_scope(HAVING)·order_by(NULLS LAST) 추가, prior_rows 우회 해제, SMQ 커버리지 진입 보정(식별 필터 제거+스코프 노트), `resolve_stat_month` 절대 월 해석. 보완 가드 2종(모순 조건·NULLS LAST)은 폴백 방어로 유지. 라이브 정답 도달. |
| 2026-07-20 | D-098 | **폴스타 피벗 통계 조인 결함 2종 결정적 검출** — ①통계를 server.Server 고정 alias id에 조인(통계는 자식 리소스에만 붙음 → NULL 정렬로 임의 서버 1위 오답) ②다중 타입 피벗에 통계 INNER JOIN(server.Server 행 그룹 탈락 → 침묵 0건). 어댑터 validator 2종 추가, 라이브에서 ② 차단→재생성→정답. 동일 형태 결함 4종 누적 — 추가 관찰 시 결정적 조립(D-076) 재검토. |
| 2026-07-20 | D-097 | **스코프된 피벗 조회 서버 식별 컬럼 SELECT 강제** — "서버명과 제조사·일련번호가 같은 행에 안 나옴" 해소. 공용 스코프 블록 규칙 4(식별 컬럼 포함) + 어댑터 validator(HAVING 스코프 있는데 SELECT에 식별 없으면 거부). 폼필 조립기 형태는 미검사(오검출 방지). 라이브: 한 행에 server_name+manufacturer+serial_number 반환. |
| 2026-07-20 | D-096 | **폴스타 피벗 스코프 필터 WHERE 강등 결정적 검출** — 어댑터 validator에 `check_scope_filter_where_demotion` 추가: 다중 resource_type 피벗 alias의 name/hostname 필터가 WHERE에 있으면 거부(자식 리소스 행 탈락 → 침묵 0건 오답 형태). 교정 예시(HAVING 집계) 포함 메시지로 재생성 유도. 라이브: attempt 0 오답 형태 차단 → 재생성 → 정답 도달. |
| 2026-07-20 | D-095 | **deepagents 선행 결과 스코프 결정적 주입** — `_dependency_scope` 게이트(G1 값 일치/G2 참조 어휘/G3 순위 어휘, 전역 명시 시 제외)로 collector의 선행 조회 결과를 input_from/prior로 주입해 D-086 IN 스코프 강제를 deepagents에 배선. 오케스트레이터 지시문에 식별자 명시 규칙 추가. 라이브 정답 도달(HPE/KR2024WEB0001, ground truth 일치). 폴스타 피벗 스코프 필터의 WHERE 강등 변동은 후속 과제. |
| 2026-07-20 | D-094 | **sub-task SQL 생성 original_query 스코프** — `_make_isolated_input`이 parsed_requirements 사본의 original_query를 task sub_query로 교체. 전체 질의 유출로 sub_query 제약(서버명 한정)이 SQL에서 침묵 탈락하던 결함 수정(라이브 검증: IN 필터 정확 생성, 알람 서버 Vendor/SerialNumber 반환). 오케스트레이터 sub_query 자체의 제약 누락(deepagents prior_rows 미배선)은 후속 과제. |
| 2026-07-20 | D-093 | **딥 에이전트 빈 응답 조기 종료의 진전 게이트 재개** — 조기 종료 감지 시 말미 빈 AI 제거 + 재개 지시로 재호출, 도구 실행이 늘어나는 동안 최대 3회(진전 없으면 즉시 중단, 예외는 D-092 안내로 강등). flash-lite 모델 유지(사용자 결정). 라이브: 도구 1건→2~4건, 제조사·일련번호 SQL 실행 도달. |
| 2026-07-20 | D-092 | **딥 에이전트 조기 종료 감지 + 미실행 하위 작업 명시** — 오케스트레이터 빈 응답(무내용·무도구호출) 종결을 결정적 감지, 수행된 조회·미실행 작업(미완료 todo)을 최종 응답 말미에 결정적 부착. per-task 최종화의 original_query를 sub_query로 스코프해 부분 결과의 전체 질문 답변 위장(환각) 차단. 도구 0회+빈 응답 시 질의 에코 대신 명시 실패 안내. 모델·재시도 가드는 범위 외(사용자 결정). |
| 2026-07-20 | D-090 | **공용 경로 어휘 매핑 LLM 전환 (Plan 63 P3)** — 메트릭 어휘 격리는 P2로 달성(어댑터), 무선언 DB는 공통 LLM 경로(P4-2 검증). `GENERIC_LLM_MAPPING`(기본 OFF) 옵트인 시 무선언 DB에 범용 기간 힌트(`build_generic_period_hint`, 폴스타 리터럴 없음) 주입, 폴스타는 결정적 블록 유지(선언 우선 EX 동치). LLM 자동등록 차단 유지(D-068 6차). 잔여 리터럴은 폴스타 게이트로 비누수(후속 프로필 이관 스코핑). |
| 2026-07-20 | D-091 | **모의 비폴스타 DB 범용성 회귀 하네스 (Plan 63 P4-2·P4-3)** — `testdata/generic_mon/`(평탄 3테이블, 프로필·모델 없음)+`tests/test_generic_path/`(공통 템플릿 사용·폴스타 리터럴 무오염·어댑터 미발동 검증, E2E 옵트인). 하네스가 검출한 공통 템플릿 D-085 예시의 `stat_date` 잔여 누수 중립화. 편입 체크리스트에 ⑤프로필/모델(선택) 추가. |
| 2026-07-20 | D-089 | **폴스타 DB 어댑터 분리 (Plan 63 P2)** — `src/db_adapters/polestar/` 어댑터 계층+레지스트리 디스패치. Stage 1: POLESTAR 템플릿 2종·`_check_routing_filter_misuse` 이동+`get_adapter().system_template/validator_checks` 배선. Stage 2: pivot 조립기 클러스터(build_multi_resource_pivot_sql 등) 이동. 동작 불변, 호출부 3곳 어댑터 직접 임포트. servername/hostname 가드는 infra 호출로 utils 잔류. overfit schema-literal 136→79, 기준선 71→44. |
| 2026-07-20 | D-088 | **공용 계층 DB-agnostic 원칙 + 공용 주입 블록 일반화 + 과적합 가드 (Plan 63 P1·P4-1)** — build_prior_rows_block cmm_resource 문장 제거·일반화, build_stat_month_block 폴스타 게이트(단일/멀티 대칭), 공용 템플릿 스키마 접두사 예시 중립화, D-085 메시지 중립화. `scripts/overfit_check.py`(schema-literal/routing-vocab 분리·기준선 71토큰·--ci 게이트)+스킬 등록. 폴스타 동작 불변. |
| 2026-07-18 | D-087 | **validator CTE 인식 주석 비대칭 수정** — `_extract_cte_names`가 선두 주석 시 `^WITH` 앵커 실패로 CTE를 미존재 테이블로 오거부(2단 집계 쿼리 전멸) → 주석 제거 후 판정으로 테이블 추출과 전처리 일원화. |
| 2026-07-18 | D-086 | **선행 task 결과 스코프 결정적 주입(크로스도메인 알람→지표)** — 죽은 prior_rows 배선 복구(`build_prior_rows_block` 단일/멀티 대칭), 트랙 C 우회, `_coerce_alarm_intent` input_from 가드, 성능 템플릿 알람 환각 금지, planner 예시 3-1. |
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
