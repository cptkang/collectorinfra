# 182. 리팩터 로드맵이 남긴 행동 변경 후보 — 한 계획서로 모은 기록 (기록)

> **작성일**: 2026-10-17 · **v1.0**(기록만 · 코드 0)
> **상태**: **TODO** — 사용자 결정 「한 계획서로 기록」(2026-10-17). 착수하려면 §3 결정을 정하고, 묶음(§2)마다 별도 Wave 또는 별도 계획으로 연다.
> **출처**: `plans/166` 로드맵(단계 3~8)과 그 하위 계획 `plans/168`~`plans/181`, `plans/163` v1.3·v1.4 잔여가 「행동 변경이라 고치지 않음 — 기록만」으로 남긴 항목. 구조 리팩터는 동작 불변이 성공 기준이라 이 항목들을 손대지 않았다(D-337 ⑥).
> **실측 기준**: 작업 트리(RCA `33f238ea` + 166 단계 8 미커밋 변경 · 2026-10-17). `file:line`은 이 시점 값이며 착수 때 다시 잰다.
> **관련 결정**: D-337 ⑥(행동 변경은 구조 리팩터와 분리 — 이 계획 전체의 근거) · D-331 ②(getattr 폴백 린트) · D-066(단일/멀티 대칭) · D-070(사용자/운영자 시크릿 분리) · D-161(죽은 경로 판정) · D-255(사용자 접점 변경 시 매뉴얼 동반) · D-003(읽기 전용 3중 방어)
> **적용 스킬**: 보안 묶음은 `.claude/skills/secure-coding/` · 나머지는 일반 구현(동작 변경 단언 테스트 동반)

---

## 1. 후보 표

**표기**: 보안 = 보안 성격 여부 · 권고 = 고침 / 유지 / 확인 필요 · 크기 = S(1~2파일·수십 줄) / M(여러 파일 또는 테스트 다수 갱신).

### 1.1 보안·응답 노출 (S)

| ID | 출처 | 위치 | 현재 동작 | 바뀌면 | 사용자 영향 | 보안 | 권고 | 크기 |
|---|---|---|---|---|---|---|---|---|
| S1 | 179 §5 (security-auditor Low) | `src/api/query/upload.py:103-106` · 사유 원천 `src/infrastructure/drm/scsl.py:108,150,152,157,167,174` | DRM 복호화 실패 시 `DrmDecryptError.reason`을 502 `detail`에 그대로 싣는다. 사유 대부분은 고정 문구(`_RET_REASONS`)지만 `:108`은 설정 키 이름, `:152`는 `java_bin` 서버 경로를 담는다 | 고정 사유(`_RET_REASONS`·타임아웃)만 화면에 남기고, 설정 누락·java 경로는 고정 문구 + 요청 번호(`user_error_detail` — 163 S3 규약) | 업로드 실패 문구(화면) | 예(내부 정보 노출 Low) | **고침** | S |
| S2 | 166 §5.2 W1 잔여(A5 보안 관찰) | `src/api/routes/admin_auth.py:96-98`(`username is None`) ↔ `src/api/dependencies.py:161`(break-glass `not payload.get("sub")`) · 사용자 토큰 `dependencies.py:95-96`(`not sub`) | `sub == ""`인 운영자 토큰을 `verify_admin_token`은 통과시키고(사용자명 `""`) break-glass는 거부한다. 발급 경로(`admin_auth.py:68`)상 빈 `sub`는 나오지 않는다 | 세 곳 모두 `not sub` → 401(엄격 쪽으로 통일) | 없음(발급 경로상 미발생) | 예 | **고침** | S |
| S3 | 179 §4 (security-auditor Low) | `src/api/query/store.py:24`(`_results_store` 모듈 전역 `OrderedDict`) · 조회 `:57` | 결과 저장소가 프로세스 메모리라 uvicorn 워커가 둘 이상이면 다른 워커가 받은 다운로드 요청이 404 | 공유 저장소(Redis 등)로 옮기거나 단일 워커 전제를 기동 시 검사 | 다중 워커 배포에서 파일 다운로드 간헐 404 | 아니오(가용성) | **확인 필요**(운영 워커 수) | M |
| S4 | 163 v1.4 잔여 ⑦ | `src/orchestration/apm_query.py:536`(`_retry_selection` — `f"{type(e).__name__}: {str(e)[:120]}"`) · `:576`(`info["error"]`) · `:1264`(`source_status.reason = str(e)`) | 예외 원문이 선택 메타·`source_status`에 남는다. 지금은 `query.py`가 `system:status`만 내보내 사용자에게 닿지 않는다 | 예외 종류 + 요청 번호만 남기고 원문은 로그 | 없음(현재 미노출) | 예(잠재 — 메타를 응답·감사로 넓히면 노출) | **고침**(낮은 우선) | S |
| S5 | 163 v1.4 잔여 | `src/nodes/cache_management.py:205`(`"error_message": str(e)`) | 화면 답은 고정 문구 + 요청 번호인데 상태 `error_message`에는 원문이 남는다 | `error_message`도 고정 사유로 | 확인 필요(`error_message` 소비처가 화면에 싣는지) | 예(잠재) | **확인 필요** | S |
| S6 | 163 v1.4 잔여 | `src/utils/user_error.py`(`degraded_reason` 붙은 결과 문구를 처리기 문구로 신뢰) · 규칙 문서 `.claude/skills/secure-coding/` | 구조적 차단이 아니라 규약이다 — 처리기가 `degraded_reason`을 붙이며 예외 원문을 넣으면 같은 구멍이 다시 열린다 | 스킬 규칙 1줄(「`degraded_reason`을 붙이는 처리기의 `error`·`final_response`는 고정 문구 + 요청 번호, 원문은 로그」) · 선택: 규칙 위반 정적 검사 | 없음 | 예 | **고침**(스킬 문서 · 코드 0) | S |
| S7 | 163 v1.3 W1 잔여 | `mcp_server/mcp_server/security.py:145`(`_code_text` — 세 방언 규칙으로 각각 읽음) · `:106-110`(`:=` 부작용 구문) · xfail 고정 `mcp_server/tests/test_plan163_w1_verify_readonly.py:27,34` | 오거부 3종: 세 방언 동시 판독(`'C:\Windows\'…delete`·`SELECT "into"`) · 값 안의 백슬래시 · PG `f(a := 1)` | 소스 방언을 검사기에 넘겨 해당 방언 규칙만 적용 — strict xfail 2건이 XPASS로 알려 준다 | 백슬래시가 든 호스트명·알람명 조회 불가 | 예(완화는 방어 약화 방향) | **유지**(운영 오거부 사례가 나오면 착수 — D-003) | M |

### 1.2 문구·로그 (M)

| ID | 출처 | 위치 | 현재 동작 | 바뀌면 | 사용자 영향 | 보안 | 권고 | 크기 |
|---|---|---|---|---|---|---|---|---|
| M1 | 163 v1.4 · 166 §5.2 W1 「문구 후보」 | `src/orchestration/apm/first_hop.py:129,131,140`(`부하 순위를 받지 못해({rank_error})`) · `rank_error` 생성 `apm_query.py:1507` · `_public_call_error` `apm_query.py:479`(`… (요청 {rid})`) | 첫 홉 고지가 「부하 순위를 받지 못해(api_error (요청 rid))」로 괄호가 겹친다 | 「(api_error · 요청 rid)」 꼴 | APM 답 고지 문구(화면) · 매뉴얼 `user.md`는 `(…)` 자리표시라 영향 없음(163 기록) | 아니오 | **고침** | S |
| M2 | 172 §5 | `src/orchestration/apm_query.py:1260-1267`(`SourceMcpError` 거부 — `ctx.notices` 그대로) ↔ 다른 거부 `:836,841,1029,1090`(`disc.dedupe(...)`) | 게이트웨이 연결 실패 거부만 고지를 중복 제거하지 않는다 | 다른 거부처럼 `disc.dedupe` | 같은 고지 중복 표시 가능 | 아니오 | **고침** | S |
| M3 | 174 §5 ① | `src/graph.py:958-963` | 1단 조립 불가 경고가 「semantic_router 경로로 폴백」이라 쓰지만, 2단 플래그가 켜져 있으면(미입력 = on · D-251) 실제 확정 단은 `intent_orchestration`이다(확정은 `:967` `resolve_ladder_tier`) | 확정 단 이름을 찍거나 「다음 성립 단으로」로 | 운영 로그 판독(오판 유도) | 아니오 | **고침** | S |
| M4 | 170 §4.3 7 | `src/security/pii_filter.py:231-239` | 설정 로드가 실패하는 동안 PII 설정을 읽을 때마다 경고(질의당 여러 번) | 최초 1회(+ 회복 시 1회) 경고 | 로그량 | 아니오 | **확인 필요**(반복 경고가 장애 관측 신호로 쓰이는지) | S |
| M5 | 173 §5.2 4 | `src/sql_validation.py:370-385`(`unknown_tables = set()` → `', '.join`) · 같은 문구의 다른 경로 `:840-845`는 이미 `sorted` | 미존재 테이블이 2개 이상이면 오류 문구 나열 순서가 프로세스(해시 시드)마다 달라질 수 있다 | `sorted` | 재시도 프롬프트·오류 문구 재현성 | 아니오 | **고침** | S |
| M6 | 173 §5.2 8 | `src/domain/host_discovery.py:566-570`(`weak = found[0]` → `weak.weak_label`) | 보조 컬럼만 일치한 시스템이 둘 이상이고 표기가 다르면 첫 시스템 표기만 문구에 나온다 | 표기 집합을 나열 | 호스트 판정 안내 문구 | 아니오 | **고침** | S |
| M7 | 163 v1.3 W1 잔여 | `src/nodes/multi_db_executor.py:811`(`_stop_detail = validation_error` — 머리말 없는 원문) → `src/orchestration/result_aggregator.py:1664-1673`(`safe_error_text(detail)`) | 멀티 경로 재생성 중단 고지에서 「(마지막 사유: …)」가 빠진다 — detail에 머리말이 없어 판별기가 원문으로 보고 지운다 | detail을 「SQL 검증 실패」 머리말 꼴로 넘겨 머리말만 실리게(단일 경로와 대칭 — 같은 파일 `:803-806` 주석) | 멀티 경로 실패 고지 문구 | 예(원문 노출 금지 규약 유지 필요) | **확인 필요** | S |
| M8 | 179 §4 이탈 | `src/api/query/progress.py:374-379` | 해시 불가 `node_name`이면 debug 로그 1줄이 더 남는다(호출부는 항상 문자열) | — | 없음 | 아니오 | **유지**(기록만) | — |
| M9 | 173 §5.2 9 | `src/doc_qa/service.py:509` | `_answer_impl` 독스트링이 `audit_context`를 「주면 감사 로그를 남긴다」고 하나 감사는 래퍼(`:597`)가 하고 인자는 쓰이지 않는다 | 독스트링 정정(동작 0) | 없음 | 아니오 | **고침**(문서만 — 다음 리팩터에 얹어도 됨) | S |

### 1.3 상태 키·고지 전달 (K)

| ID | 출처 | 위치 | 현재 동작 | 바뀌면 | 사용자 영향 | 보안 | 권고 | 크기 |
|---|---|---|---|---|---|---|---|---|
| K1 | 181 §5 | 반환 `src/orchestration/agent_orchestrator.py:205` · 읽기 `src/orchestration/replanner.py:1303` · 폐기 주석 `src/state.py:414` · 예외 고정 `tests/test_plan181_node_return_keys.py:89-90` | `sufficiency_shortfalls`는 `AgentState` 미선언이라 LangGraph가 버린다 — 그래프 경로에서 replanner가 늘 빈 값을 읽는다. 실제 전달은 `dependency_notes`(NOTE_SUFFICIENCY) | 폐기일(2027-03-09 — `state.py:414`)에 반환·읽기 둘 다 삭제 + `KNOWN_UNDECLARED` 항목 제거 | 없음 | 아니오 | **유지**(폐기일에 정리) | S |
| K2 | 181 §5 | `src/nodes/multi_db_executor.py:1463-1464` · 2단 소비 `src/orchestration/subagents.py:1393` | `skipped_dbs`는 2단이 반환 dict에서 직접 읽고, 3단·task 서브그래프에서는 버려진다 | — | 없음(사유는 `dependency_notes`가 운반) | 아니오 | **유지**(의도) | — |
| K3 | 181 §5 | `src/nodes/multi_db_executor.py:1468-1469` | `regen_stops`는 그래프 경로에서 버려진다(반환부 주석에 의도 명시 — plans/119) | — | 없음(사유는 `db_errors`가 운반) | 아니오 | **유지**(의도) | — |
| K4 | 181 §5 (W1-A 대략 grep · 미확정) | `src/nodes/output_generator.py:1967,2016,2051,2093` 등 · `src/pipeline/output_notes.py:353,491,660,690` · `OutputState` `src/state.py:515` | 출력 노드가 `OutputState` 밖 키(`db_results`·`scope_narrowed`·`turn_disclosures`·`resolved_limit`·`db_executed_sqls`·`dependency_notes` 등 16종)를 읽는다 — 2단·1단 출구에서는 늘 비어 있다 | 키별로 단일 경로 전용(의도)인지 판정 → 누락이면 2단 출구가 채우게 | 2단 응답의 고지·노트 누락 가능(docs/18 2026-10-06 계열) | 아니오 | **확인 필요** | M |
| K5 | 174 §5 ② | `src/orchestration/replanner.py:631`(`"reason": "replan_task_invalid"`) | `plan_validation`이 남긴 노트가 뒤 필터 6종 중 하나가 루프를 끝내면 반환에서 사라진다(종전 동작) | 종료 필터가 앞 노트를 보존 | 재계획 무효 사유 고지 누락 | 아니오 | **확인 필요** | S |
| K6 | 173 §5.2 7 | `src/nodes/result_organizer.py:290-297` | `data_insufficient` 반환의 `OrganizedData`에 `sheet_mappings` 키가 없다(소비처는 `.get`) | `sheet_mappings=None` 명시 | 없음(영향 미미) | 아니오 | **고침**(낮은 우선) | S |
| K7 | 179 §5 | `src/api/routes/query.py:594`(게이트 안 `body.selected_db_ids = …`) ↔ `:1232,1335`(호출자가 반환값을 다시 대입) | 텍스트 진입에서 같은 값을 두 번 대입한다 | 한 곳만 남김 | 없음 | 예(인가 결과 대입 지점 — 바꿀 때 인가 순서 확인) | **고침**(구조 정리에 가까움 — 동작 동일 확인 뒤) | S |
| K8 | 166 §8 U-8 · 173 §5.2 11 | `src/nodes/output_generator.py:1821-1850`(`TEXT_NOTES` 13 ↔ `DOC_NOTES` 9) · 적용 `:170,217` | 문서(xlsx/docx) 응답에 텍스트 경로의 노트 4종(`current_month_partial`·`limit_truncation`·`generator_notes`·`condition_changes`)이 붙지 않는다. 4종 도입 커밋(`2d485af`·`f4501b2`)은 텍스트 경로만 고쳤고 문서 경로 제외 의도 기록은 없다 | 의도 확인 뒤 누락이면 `DOC_NOTES`에 추가 | 문서 응답에서 당월 부분·LIMIT 절단·조건 변경 고지 누락 | 아니오 | **확인 필요**(plans/123 계열 의도) → 누락이면 고침 + 매뉴얼(D-255) | S |

### 1.4 판정·결정성 (J)

| ID | 출처 | 위치 | 현재 동작 | 바뀌면 | 사용자 영향 | 보안 | 권고 | 크기 |
|---|---|---|---|---|---|---|---|---|
| J1 | 169 §(5b) 「행동 변경 후보」 | 단일 `src/nodes/query_generator.py:401`(`_metric_entries` 버림) · `:435-440`(`c is None`인 필드만 회수) ↔ 멀티 `src/nodes/multi_db_executor.py:2255-2257`(`metric_entries` 회수) | 단일 경로 폼필은 `cmm_metric_stat` 칼럼에 매핑된 사용률 필드를 metric 회수 대상에서 빼고 버린다. 멀티는 회수한다 | 단일을 멀티에 맞춤(D-066 대칭) | 단일 DB 양식에서 사용률 칸 빈칸 가능 | 아니오 | **고침**(멀티에 맞춤 — 골드셋·폼필 산출 대조 동반) | M |
| J2 | 173 §5.2 3 | `src/sql_time_conditions.py:576`(`_read_condition`) · 소비 `src/db_adapters/polestar/validators.py:1060`(`_judge_groups`) | 괄호 없는 현재시각 낱말(`CURRENT_TIMESTAMP`·`CURRENT_DATE`·`SYSDATE`)을 `reason=column`으로 분류해 그 경계를 구간 계산에서 뺀다(`NOW()`·DB2 `CURRENT TIMESTAMP`는 `expression`) | `expression`으로 분류 | 시간 조건 검증이 해당 경계를 못 봄(기간 판정 누락) | 아니오 | **고침** | S |
| J3 | 173 §5.2 5 | `src/nodes/schema_analyzer.py:692,723`(`_supplement_bare` set 순회) · `:753,779,789`(`_alarm_core_set`) | 보충 테이블 순서가 해시 시드마다 달라 `relevant_tables`·프롬프트 접두가 재기동마다 바뀔 수 있다 | 정렬·입력 순서 보존 | 재현성 · KV 캐시 적중 | 아니오 | **고침**(`prompt_render_diff` 바이트 변화 허용 필요 — §3) | S |
| J4 | 166 §5.8 미달 표(X4) | `src/routing/semantic_router.py:600-603`(`load_config()` 전역 읽기) ↔ 정본 `src/routing/capability_ownership.py:55`(호출부가 넘긴 설정) | 3단 소유 판정만 넘긴 설정이 아니라 전역 설정을 읽는다 — 둘이 다르면(테스트 대역·설정 재적재) 3단만 다르게 판정 | 정본 `capability_ownership_on(app_config)` 사용 | 운영(설정 1벌)에서는 없음 | 아니오 | **확인 필요**(행동 동등성 실측 뒤 통합) | S |
| J5 | 175 §5 · 178 §5(6e 이월) | `apm_gateway/apm_gateway/application/fleet_tools.py:520,527` | 인스턴스 id가 `None`이어도 `application_status(did, [None], …)`를 부른다(mypy list-item 1건이 가리키는 지점 · 기준선부터) | id 없는 인스턴스는 건너뛰고 고지 | 불필요한 게이트웨이 호출·오류 응답 | 아니오 | **고침** | S |
| J6 | 173 §5.2 10 | `src/domain/time_spec.py:679`(`resolve`) — 분기 위치는 착수 때 커버리지로 다시 잰다 | 도달 불가 분기 3(last `rolling` 참 쪽 · between `start > now` 참 쪽 · since `precision=="hour"` 참 쪽) | 제거 | 없음 | 아니오 | **유지**(제거는 D-161 실측 첨부 뒤 별건) | S |

### 1.5 수명주기·설정 접근 (L)

| ID | 출처 | 위치 | 현재 동작 | 바뀌면 | 사용자 영향 | 보안 | 권고 | 크기 |
|---|---|---|---|---|---|---|---|---|
| L1 | 173 §5.2 1 | `src/api/server.py:468-519`(대입 `:492-494` → DDL `:505` → 실패 `:519`) · 감사 로테이션 시작 `:531-534` | DDL 등 중간 실패 시 이미 대입한 `auth_pool`·`user_repo`·`audit_repo`가 남아 풀이 열린 채 감사 로테이션 태스크가 시작된다(로그는 「인증 기능 비활성」) | 실패 시 대입 원복 + 풀 닫기 | 인증 반쯤 켜진 상태(로그와 실제 불일치) | 예(인증 상태 불일치 — 확인 필요) | **고침** | S |
| L2 | 173 §5.2 2 | `src/api/server.py:429-432`(`await cache_mgr.disconnect()` → `logger.info("서버 종료")`) ↔ 다른 정리 `:421`(`_close_quietly`) | `disconnect()`만 예외를 삼키지 않아 실패 시 「서버 종료」 로그 생략·예외 전파 | 다른 정리 단계처럼 삼키고 경고 | 종료 로그 누락 | 아니오 | **고침** | S |
| L3 | 172 §5 · 174 §5 ③ | `src/orchestration/apm_query.py:1233-1234` · `src/orchestration/apm_jobs.py:381`(`source_call_timeout` 폴백 10.0 = 필드 기본값 `src/config/data_access.py:49-50`) · `src/graph.py:640,711` · `src/routing/semantic_router.py:597,603,675` | getattr 폴백이 남아 있으나 폴백값이 필드 기본값과 같아 실 `AppConfig`에서는 결과 동일(`config_fallback_check --ci` rc 0). 다른 결과는 테스트 대역(MagicMock 등)에서만 난다. 허용 목록 2곳(`src/db_adapters/template_assembler.py:113` 폴백 False ↔ 기본 True 등)은 대역 계약 | 직접 접근으로 전환 | 없음(운영) | 아니오 | **유지**(전환은 테스트 대역 정리와 함께 — 동작 불변 리팩터로 다룰 수 있음) | M |

### 1.6 성능 (P)

| ID | 출처 | 위치 | 현재 동작 | 바뀌면 | 사용자 영향 | 보안 | 권고 | 크기 |
|---|---|---|---|---|---|---|---|---|
| P1 | 171 §4 3 (verifier Minor ②) | `noise_gate/domain/notification_policy.py:1232`(`decide_notification`) · `:1380`(`replace`) | 규칙 표 도입 뒤 호출 1건 비용 약 4배(심각도2 5.5→23.0µs — 약 40필드 frozen dataclass 조립 + `replace` 2회 생성) | 표지를 별도 인자로 넘겨 `replace` 제거 | 없음(알람 1건당 수십 µs) | 아니오 | **유지**(측정 계획이 생기면) | S |
| P2 | 171 §4 2 | `noise_gate/domain/notification_policy.py:1030`(`_rule_cross_source_cap`) · `:1387` | 7.6 경로에서 매트릭스를 2회 계산(순수 계산 — 결과 동일) | 캐시 | 없음 | 아니오 | **유지** | S |
| P3 | 173 §5.2 6 | `src/nodes/schema_analyzer.py:366`(`_load_manual_profile`) · 호출 `:448,628,646,854,1083` | 요청당 최대 5회 파일을 읽는다 | 요청 스코프 1회 | 질의 지연 소폭 | 아니오 | **확인 필요**(테스트가 호출 횟수를 patch로 봄 — 테스트 갱신 동반) | S |
| P4 | 177 §5 「(선택) 성능」 | `noise_gate/application/alarm_worker.py:200,302,357`(협력자 생성 함수) · 호출 15곳 | 워커 메서드 호출마다 host 어댑터 + 서비스 객체 2개 생성 | 워커당 1회 생성 후 재사용 | 없음(알람 1건당 수십 회 · 무시 가능) | 아니오 | **유지**(측정 계획이 생기면) | S |

### 1.7 설정 도움말 (H)

| ID | 출처 | 위치 | 현재 동작 | 바뀌면 | 사용자 영향 | 보안 | 권고 | 크기 |
|---|---|---|---|---|---|---|---|---|
| H1 | 166 §5.9(단계 8 계획 끝 회귀 원래 실패) | 필드 `src/config/text2sql.py:202`(`template_assembly: bool = True`) · 도움말 `config/settings_help/text2sql.yaml`(항목 없음) · 실패 테스트 `tests/test_api/test_settings_help.py::test_t2_curated_groups_have_full_coverage` | 설정 도움말 큐레이션에 `TEXT2SQL_TEMPLATE_ASSEMBLY`가 없어 위 테스트가 실패한다(기준 worktree `33f238ea`에서도 같은 단언으로 실패 — 166 단계 8과 무관) | `text2sql.yaml`에 항목 추가 → 원래 실패 1건 해소 | 관리자 설정 화면에서 이 설정의 도움말 부재 | 아니오 | **고침**(가장 작음) | S |

### 1.8 해소됨 (원 계획서에 후보로 적혔으나 이미 처리)

| 원 출처 | 내용 | 근거 |
|---|---|---|
| 168 §5 | 계수 가드 전환·응답 리터럴 한 벌 | `plans/179` W1a에서 전환 완료(가드 8파일 · `_build_turn_response`·`_turn_done_payload` 한 벌) |
| 174 §4 이탈 | `_coerce_arg` 해시 불가 `spec.type`이면 `TypeError` | 메인이 되돌림 — `src/orchestration/apm/args.py:164` `isinstance(spec.type, str)` 가드로 종전 `(False, None)` 복원 |
| 177 · 180 · 168 | 행동 변경 후보 | 원 계획서가 「0건」·「없음」으로 기록 |

## 2. 묶음 제안

| 묶음 | 후보 | 고침 권고 | 동반 | 메모 |
|---|---|---|---|---|
| ① 보안·응답 노출 | S1~S7 (7) | S1·S2·S4·S6 | security-auditor · `secure-coding` 스킬 · S1은 매뉴얼 확인(D-255) | 163 계열 규약(고정 문구 + 요청 번호) 재사용 — 새 헬퍼 중복 금지(`user_error.py`·`error_detail.py`가 정본) |
| ② 문구·로그 | M1~M9 (9) | M1·M2·M3·M5·M6·M9 | 문구 단언 테스트 갱신 · M1·M6·M7은 화면 문구라 매뉴얼 확인 | M7은 보안 규약과 맞물려 ①과 함께 볼 수도 있다 |
| ③ 상태 키·고지 전달 | K1~K8 (8) | K6·K7 (K8은 확인 뒤) | `test_plan181_node_return_keys.py` `KNOWN_UNDECLARED` 갱신 · K8은 매뉴얼 | K1은 2027-03-09 폐기일 작업으로 미룸 |
| ④ 판정·결정성 | J1~J6 (6) | J1·J2·J3·J5 | J1 골드셋·폼필 대조 · J3 `prompt_render_diff` 바이트 변화 허용 · J5는 `apm_gateway` 자체 테스트 | J1은 사용자 산출(양식)이 바뀌는 가장 큰 항목 |
| ⑤ 수명주기·설정 접근 | L1~L3 (3) | L1·L2 | L1은 보안 관점 검토 | L3은 동작 불변 리팩터로 분리 가능 |
| ⑥ 성능 | P1~P4 (4) | — | 측정 계획 | 전부 유지·확인 — 측정 근거가 생길 때만 |
| ⑦ 설정 도움말 | H1 (1) | H1 | `pytest tests/test_api/test_settings_help.py` | 단독으로 먼저 해도 된다(원래 실패 1건 해소) |

합계 38건(고침 권고 19 · 확인 필요 9 · 유지 10). 해소됨 2건은 별도(§1.8).

## 3. 착수 전 결정할 것

| # | 질문 | 선택지 |
|---|---|---|
| 1 | 진행 단위 | (a) 이 계획 하나에서 묶음별 Wave · (b) ①·④처럼 큰 묶음은 별도 계획으로 분리 · (c) H1·M5 같은 S 크기만 먼저 모아 처리 |
| 2 | S1 DRM 사유 | (a) `_RET_REASONS` 고정 사유는 화면 유지 · 설정·경로 사유만 고정 문구 · (b) 전부 고정 문구 + 요청 번호 |
| 3 | S3 결과 저장소 | 폐쇄망 운영의 uvicorn 워커 수 확인 → 1이면 유지(기동 시 검사만) · 2 이상이면 공유 저장소 |
| 4 | K8 문서 경로 노트 | 4종을 문서 응답에서 뺀 것이 의도인지(plans/123 계열) — 누락이면 고침 + 매뉴얼 |
| 5 | J1 단일 경로 사용률 회수 | 멀티에 맞출지 — 단일 DB 양식 산출이 바뀐다(골드셋·시연 영향) |
| 6 | 결정성(J3·M5) | 프롬프트 접두·오류 문구 바이트가 한 번 바뀌는 것을 허용할지(`prompt_render_diff` 기준선 갱신) |
| 7 | D-번호 | 묶음별로 새 결정을 등재할지 · 기존 결정 부기로 충분한지(S6은 163·D-153 부기 후보) |

## 4. 범위 밖

- 테스트 수정이 필요한 구조 정리: `plans/177` 가드 범위(`test_plan144_verify.py` `_NOISE_GATE_CHANGED`) · `plans/178` `apm_app_health` 이동 · `plans/180` 가드 고정 15정의 · `plans/179` 원 가드 4파일 범위 확대 · `plans/170` §4.3 1~6(seam 잔류·공허 소스 가드·Facade 우회 5곳·이름 혼동·대칭 테스트·타입 주석).
- 구조 리팩터 후보(긴 함수·SCC·2,000줄 초과 파일·shim 제거) — `plans/166` §5.8 미달 표·§5.9 남김 표.
- 가독성·표기 후보(동작 동일): `plans/171` §4 1(`UTC` 표기) · `plans/176` §5 전부 · `plans/178` §5 `_Forward` 클래스 경유·미사용 `logger` · `plans/175` §5 `RunData` `get_type_hints`.
- 사용자가 이미 「하지 않음」으로 확정한 행동 변경: 존 역질문 신호 통일(166 U-1 ②) · `TEXT2SQL_MULTI_FULL_VALIDATION` 기본 ON(166 U-9).
- 사내망 `verify=False`(D-332 부기 — 의도 · 결함 아님) · 로그 비밀값 가림(`plans/167` 별도).

---

## 변경 이력

- 2026-10-17 v1.0 — 최초 작성(기록만 · 코드 0). 168~181·163 잔여·166 §5.2·§5.8·§5.9·§8의 행동 변경 후보 38건을 7묶음으로 모으고 위치를 작업 트리에서 다시 쟀다 — 사용자 결정 「한 계획서로 기록」.
