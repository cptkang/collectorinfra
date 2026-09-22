# 77. 유사어 제안 대기열 + 승인 웹 UI — 자동 캡처 · 선택적 영구 저장 (Synonym Proposal Queue & Approval UI)

> 작성일: 2026-08-25
> **상태**: **계획 (미구현)** — 사용자 요건 확정 3건 반영(§1.2). 착수 시 D-163 등재. · **v2(2026-09-22) 구현 현황 재검토(§0)**: 계획 산출물 **코드 0건**이라 `-TODO`를 유지한다. 갭 3층은 그대로 남아 있다. 앵커 드리프트와 사실 오류 17건을 정정하고, 그대로 구현하면 **동작하지 않는 설계 결함 2건**(D-1 승인 반영 키 · D-3 S3 이미 등록됨)을 포함해 결함 10건을 반영했다. 계획 이후 생긴 전제(plans/104 · D-227 · D-228 → G-11 (b) · plans/109)도 반영했다. 미결 사항은 3건에서 7건으로 늘었다(§11) · **v2.1(2026-09-22) 성능 효과 판정(§0.7)**: 지금 범위로는 성능 효과가 작다. 지연 개선은 0이고, 최신 벤치에서 관측된 유사어 결함은 사전 부족이 아니라 **오염·과잉 매칭**이다. 사전 위생을 앞세우는 범위 재편안을 Q-8로 추가했다(미결 8건)
> **대상 기능**: 유사어 후보 자동 캡처(`src/nodes/query_generator.py`·`schema_analyzer.py`·`src/document/field_mapper.py`),
> 제안 대기열 저장(`src/schema_cache/redis_cache.py`), 승인 API(`src/api/routes/`),
> 승인 UI(`src/static/admin/dashboard.html`·`src/static/js/admin.js`·`src/static/js/app.js`)
> **[v2 추가]** 캡처 대칭 대상 `src/nodes/multi_db_executor.py`(D-5), 관리자 탭은 별도 모듈 `src/static/js/admin-synonyms.js`(plans/104 `admin-db-structure.js` 선례)
> **선행/근거 문서**: `plans/61-text2sql-candidate-selection.md` 트랙 B(E5-1~E5-3),
> `docs/synonym_management_analysis.md`, `SPEC-ops-logging-and-synonym-set.md` §7(모듈 C)
> **[v2 정정]** 스펙 경로는 `spec/SPEC-ops-logging-and-synonym-set.md`이다(D-244). **[v2 추가]** 인접 계획: `plans/104`(D-227 — 관리자 「DB 구조」 탭 · S4 감사 헬퍼 · S5 출처 병합 · G-11 (b)) · `plans/106` G-6(H4 후보와 이 큐 공유 여부) · `plans/109`(설정 노출 축소)
> **v2 재실측 기준**: 2026-09-22 HEAD `048c2be`(`multiintent` · 미커밋 작업 트리 포함). v2에서 고친 `file:line`은 이 커밋 기준이다.
> 본문의 옛 값은 이력 보존을 위해 지우지 않고 **`[v2 정정]`** 표지를 붙여 바로잡았다. 한눈에 보려면 §0 표를 본다.
> **관련 결정**: **D-011**(유사단어 2계층·글로벌 사전 TTL 없는 영구저장), **D-012**(매핑-우선 +
> LLM 추론 매핑은 **사용자 승인 시** 등록), **D-013**(멀티턴 HITL — 유사어 등록 승인),
> **D-019·D-051**(Redis 캐시 구조 불변·스케일 가드), **D-035**(결정적 규칙=판단, LLM=보조),
> **D-075**(동의어 매칭 고도화), **D-142**(앵커 없는 동의어 집합 — **모호하면 등록 0건** 원칙),
> **D-151**(멀티턴 HITL 폼필 — 구조화 답변 위젯 패턴, 본 계획 UI의 직접 선례),
> **D-027·D-034**(감사 로깅)
> **[v2 추가]** **D-227**(질의 경로는 구조를 읽기만 · 관리자 탭 · ⑥ 감사 `audit_logged` · ⑨ G-11 (b)), **D-228**(질의 경로 자동 생성
> 유사어의 전역 전파 차단 — D-227로 가드 소멸, ④는 G-11 (b)로 처분), **D-187**(양식 저장 값 패널 — 구조화 패널 2번째 선례),
> **D-225**(사다리 3단 기준 · 운영은 아직 1단), **D-244**(스펙 산출물 `spec/`), **D-248**(질의 스레드 턴 단위 서버 저장)
> **신규 결정(착수 시 등재)**: **D-163**(유사어 제안 대기열 — 자동 캡처 + 관리자 승인 게이트 + 기각 목록)
> **[v2 확인]** D-163은 `docs/02_decision.md` 「채번 이력」 표에 `예약 유지`로 등재돼 있어 예약이 유효하다(2026-09-22 실측 ·
> `## D-` 헤더 최댓값 D-248 · `D-163` 본문 헤더 0건).

---

## 0. [v2] 구현 현황 재검토 (2026-09-22 · HEAD `048c2be`)

> 사용자 지시(v2): *"77번 계획 파일의 구현 현황을 검토하고 현재 구현되어 있는 현황을 분석하여 계획을 업데이트하라."*

**판정: 미구현. 계획 산출물은 코드 0건이다. 갭 3층(캡처·저장·승인 UI)도 작성 시점 그대로 남아 있다.**
다만 그대로 착수하면 안 된다. 작성 시점(2026-08-25)부터 있던 설계 결함 2건이 있어 핵심 수용 기준 두 개가
성립하지 않는다. "승인 후 제안 재발생 0"(D-1)과 "선택한 것만 영구 저장"(D-3)이다. 그 사이 plans/104(D-227)가
관리자 탭·감사·유사어 출처 병합의 새 관례를 만들었으므로 설계도 거기에 맞춘다.

### 0.1 계획 산출물 대조 — 전부 0건

| Phase | 계획 산출물 | 현재 | 실측 |
|---|---|---|---|
| P1 | `src/utils/synonym_proposals.py`(캡처 순수 함수 4종) | ❌ 없음 | `src/utils/` 유사어 모듈은 `synonym_governance.py`·`synonym_set_parser.py`·`synonym_usage.py` 3종뿐이다 |
| P2 | `redis_cache.py` 대기열 API 5종 · 키 `synonym:proposals`·`:rejected` | ❌ 없음 | `upsert_proposals`·`synonym:proposals` grep 결과 0건 |
| P3 | `src/api/routes/synonyms.py` · 관리자 탭 `data-tab="synonyms"` | ❌ 없음 | 라우트 파일 16종 중에 없다. 탭은 8종인데 유사어 탭이 없다(F-5) |
| P4 | 응답 메타 `synonym_proposals` · `renderSynonymProposalPanel()` · 사용자 라우트 2종 | ❌ 없음 | grep 0건 |
| P5 | 승인·기각·삭제 감사 · lazy sweep | ❌ 없음 | 대상 코드가 없다 |
| §5 | 설정 5종(`SYNONYM_PROPOSAL*`) | ❌ 없음 | `SynonymMatchConfig`(`src/config.py:247-282`)에 없다 |
| §8 | 테스트 4파일 | ❌ 없음 | 이름에 `synonym`이 든 테스트 17파일(`find tests -name "*synonym*.py"`)은 모두 기존 기능(사용 역조회·등록기·집합·퍼지/의미·거버넌스·전역 가드 등)용이다. 대기열 테스트는 0건이다 |

검색식: `grep -rn "synonym_proposal\|SYNONYM_PROPOSAL\|synonym:proposals\|upsert_proposals\|renderSynonymProposalPanel"` 결과는
`plans/77`·`plans/85` 두 계획서뿐이다. 이름을 달리 쓴 기능 구현도 없다. "제안 → 승인"과 모양이 같은 기능은 plans/104의
**설명·유사어 초안 → 적용**(관리자 「DB 구조」 탭 O-4)뿐이다. 이 기능은 LLM이 생성한 DB 단위 초안을 적용할 뿐이고,
질의 중에 드러난 후보를 모으는 경로는 아니다.

### 0.2 갭 3층 재실측 — 전부 유효하다. 캡처 경로 커버리지는 v1보다 좁다

| 층 | 2026-09-22 현재 | v1 대비 |
|---|---|---|
| 캡처 | `_instrument_synonym_usage()`(`query_generator.py:971-1022`)가 `unregistered`를 **로그 1줄**(`:1010-1021`)과 처리 현황 UI(`query.py:635-640` → `app.js:3050-3058` "사전 미등록 (LLM 직접 추론)" 배지)로만 소비한다 | 동일. **새로 드러난 것**: 이 값이 UI까지 가는 경로는 **3단 직결 단일 DB 체인 하나뿐**이다. `multi_db_executor`는 `extract_synonym_usage`를 아예 부르지 않는다. 1·2단 서브에이전트와 3단 task 서브그래프는 값을 계산한 뒤 결과를 접는 단계(`subagents.py:1290-1340`)에서 버린다(D-5) |
| 저장 | 폼필 `pending_synonym_registrations`만 있다(State · `nodes/field_mapper.py:147` → `:507-537`) | 동일. 단 폼필 LLM 매핑은 pending과 **별개로 이미 Redis에 자동 등록된다**(D-3) |
| 승인 UI | 채팅 자연어(`synonym_registrar.py` 3단 해석). 관리자 탭은 8종(`dashboard.html:85-92`)인데 유사어 탭이 없다. 유사어 관리자 API는 3종(`schema_cache.py:639`·`:666`·`:705`)이고 **글로벌 사전 API는 0건**이다 | 동일(탭 6→8은 plans/104·83 추가분) |
| (§10-1) E5-3 사용 메타 | `increment_synonym_usage`(`redis_cache.py:1050`)·`prune_stale_synonyms`(`:1128`)의 `src/` 호출부가 0건이다. 입력을 만드는 `collect_synonym_usage_events`(`synonym_usage.py:121`)도 호출부가 0건이다 | 동일 |

### 0.3 계획 이후 생긴 전제 — 본문 설계에 반영한다

| # | 변화 | 근거 | 본 계획에 미치는 영향 |
|---|---|---|---|
| C-1 | 관리자 「DB 구조」 탭 신설 — 탭 로직을 **별도 모듈**로 두고 `window.AdminApi`를 쓴다. 렌더링은 createElement/textContent만 쓴다(XSS 방지) | plans/104 A-7 · `src/static/js/admin-db-structure.js:1-20` · `dashboard.html:578` | P3 UI는 `admin.js` 인라인 로더(v1 선례)가 아니라 이 형식을 따른다 |
| C-2 | 관리자 작업 감사 공용 헬퍼 `log_admin_event` + 응답 `audit_logged` | plans/104 S4 · `src/api/admin_audit.py:19-60` · `db_structure.py:185-199` | P5가 가리키던 `admin.py` 패턴을 이 헬퍼로 바꾼다 |
| C-3 | LLM 재생성은 `llm` 출처 단어만 교체하고 `operator` 단어는 보존한다 | plans/104 S5 · R10 · `redis_cache.py:689-705` `save_synonyms` | 승인 항목을 `operator`로 태깅하면 재생성에도 남는다(P2 전제 강화). 단 DB 무효화에는 남지 않는다(D-7) |
| C-4 | 폼필 LLM 매핑 자동 등록이 구조 정보 유무로 갈린다. 있으면 전역(EAV는 `synonyms:eav_names` + 전역)에, 없으면 그 DB 캐시에 `llm` 출처로 등록한다(EAV는 0건) | D-228 ④ → D-227 ⑨ G-11 (b) · `document/field_mapper.py:1113-1146`·`:1458-1560` | §1.3이 "현행 유지"로 둔 자동 등록 경로가 바뀌었다. S3 캡처·승인 반영 대상도 이 분기를 따라야 한다 |
| C-5 | 채팅 캐시 생성·무효화 6종은 관리자 전용이 됐다. **유사어 추가·삭제·등록은 여전히 비관리자도 할 수 있다** | plans/104 S2 · `cache_management.py:212-219` `_ADMIN_ONLY_ACTIONS` · `synonym_registrar.py:141-149` · `query.py:2608` 라우트에 역할 의존성 없음 | 요건 1.2-3 "확정은 관리자"는 이 계획의 대기열 경로에만 적용된다(D-10) |
| C-6 | 사다리 기준 경로가 3단으로 바뀌었다. 3단에 계획 루프 task 서브그래프가 생겼다. 운영 `.env`는 아직 1단으로 확정된다 | D-225 · plans/103 P1-1 · `graph.py:228-286` · CLAUDE.md 운영 실측 | 캡처와 패널 데이터는 State 전파에 기대지 말아야 한다(D-5) |
| C-7 | 구조화 패널 선례가 D-151(폼필 답변)에 더해 D-187(양식 저장 값 — 체크박스와 [선택 삭제])까지 2종이 됐다 | `app.js:2108-2188` · `app.js:2190-2215` | P4 패널의 직접 선례다 |
| C-8 | 질의 스레드가 서버 앱 DB에 턴 단위로 저장된다 | D-248 · `src/infrastructure/thread_repository.py` | P4의 "자기 세션 제안 한정"을 `user_id`+`thread_id`로 판정할 수 있다 |
| C-9 | 설정 노출을 줄이는 작업이 진행 중이다(`.env.example` 활성 키 목표 ≤45 · 현재 162). 카탈로그는 전수 단언 350필드로 고정돼 있다 | plans/109 CS-56 · `tests/test_api/test_settings_catalog.py:189,329` | 노브 5종을 새로 만드는 것은 이 방향에 역행한다(D-8) |
| C-10 | 다른 계획이 이 큐를 연결점으로 지목했다 — 신규 코드값(104 §3.x) · 하네스 가이드라인 후보(106 G-6) | `plans/104`:266·541 · `plans/106`:420·628 | 착수 전에 수용 여부를 정한다(Q-7) |

### 0.4 사실 정정 (본문 해당 위치에 `[v2 정정]`으로 반영)

| # | v1 주장·앵커 | 실측(2026-09-22) | 성격 |
|---|---|---|---|
| F-1 | 글로벌 사전 영구 저장 `redis_cache.py:1071-1074` | 키 상수는 `:1244-1247`, 저장은 `save_global_synonyms :1249`에 있다. 유사어 키에 `expire` 호출이 0건이라 영구성은 유지된다 | 줄 이동 |
| F-2 | 캐시 클리어 시 글로벌 보존 `:1423-1465` | `invalidate :1593-1633` · `invalidate_all :1634-1680`에서 보존된다. **단 DB별 `schema:{db}:synonyms`는 삭제 대상이다** | 줄 이동 + 누락 사실(D-7) |
| F-3 | `synonym_usage.py:107-117` · 호출 `query_generator.py:806` | `:106-118` · 호출 `:978`(함수 `:971-1022`, 노드 반환 `:1134`) | 줄 이동 |
| F-4 | pending 생성 `nodes/field_mapper.py:620-646` | `_build_pending_registrations :507-537`(호출 `:147`) | 줄 이동 |
| F-5 | 관리자 탭 6종 `dashboard.html:38-43` | **8종** `:85-92`(settings·dbconfig·dbstructure·users·auditlogs·incidents·feedback·drm) | 변화 |
| F-6 | "관리자 API는 조회/LLM생성/컬럼삭제 3개뿐" `schema_cache.py:550-640` | 스키마 캐시 라우트는 14종이고, 그중 **유사어** 라우트가 3종이다(`:639`·`:666`·`:705`). 글로벌 사전 API 0건은 사실이다 | 표현 오류 |
| F-7 | 매핑 보고서 재업로드 `query.py:1786-1870` | `:2608-2700`. 라우트에 역할 의존성이 없다(C-5) | 줄 이동 + 누락 사실 |
| F-8 | column_value_index 선례 `redis_cache.py:1710-1750` | `save_column_value_index :1901` · `load_… :1934` | 줄 이동 |
| F-9 | S2 폼필 지점 `field_mapper._synonym_match()` | `_synonym_match`(`document/field_mapper.py:815`)는 **정확 매칭** 함수다(Pass 1·2). 근사 히트 지점은 `_apply_fuzzy_synonym_fallback :675`·`_apply_semantic_synonym_fallback :744`의 **확정 분기**다 | **작성 시점 오류** |
| F-10 | S2 스키마 분석 지점 `schema_analyzer.py:120-125` | 퍼지 `:126-131` · 의미 `:143-157`(함수 `:54-167`) | 줄 이동 |
| F-11 | 재오염 가드 `query_gen_common.py:735` | `is_servername_to_hostname :1162` | 줄 이동 |
| F-12 | 자동 등록 `field_mapper.py:1118-1133` | 발견 `_apply_llm_synonym_discovery :916` → 등록 `_register_llm_synonym_discoveries_to_redis :1148`. 추론 매핑 등록은 `_register_llm_mappings_to_redis :1458`(호출 `:424`)이다. G-11 (b) 분기가 들어 있다 | 줄 이동 + 변화(C-4) |
| F-13 | 관리자 탭 동형 패턴 `admin.js:1163,1387,1561` | `loadUsers :1496` · `loadAuditLogs :1895` · `loadIncidents :2177`. 최신 탭은 별도 모듈이다(C-1) | 줄 이동 + 변화 |
| F-14 | 응답 조립 5지점 `query.py:827·1108·1284`, SSE `:1036·:1059` | **10지점**이다. `form_fill_clarification` 배선 기준으로 동기·SSE × 텍스트·파일 = `:1472`·`:1755`·`:1781`·`:1833`·`:1859`·`:2042`·`:2432`·`:2462`·`:2503`·`:2531` | 변화 |
| F-15 | HITL 패널 `app.js:1285-1340` | `renderFormFillPanel :2108-2182` · `appendFormFillPanelToLastBubble :2183` | 줄 이동 |
| F-16 | `evidence.query`는 "감사 로깅과 동일한 마스킹 헬퍼"를 통과한다(§3.2) | 감사 `audit_service.log_user_request`는 질의 원문을 **마스킹하지 않는다**(`DataMasker`는 결과 행 전용). 텍스트 마스킹 헬퍼는 `src/security/pii_filter.py:365` `scrub_pii`다. 이 헬퍼는 infrastructure 계층이라 P1 utils에서 import할 수 없다(D-9) | **작성 시점 오류** |
| F-17 | 감사 패턴 `admin.py:381-418` · 미가용 명시 `admin.py:726` | 공용 헬퍼 `src/api/admin_audit.py:19` `log_admin_event`로 일원화됐다(C-2) | 변화 |

### 0.5 설계 결함 — 본문 반영

| # | 결함 | 그대로 구현하면 | 수정 |
|---|---|---|---|
| **D-1** | **S1 승인 반영 키가 미등록 판정 키와 다르다.** `extract_synonym_usage`는 `synonyms:eav_names`·`synonyms:resource_types`의 **키**에 리터럴이 없을 때 미등록으로 본다(`synonym_usage.py:106-116` ← `schema_analyzer.py:780-781`). 그런데 P2는 승인을 `add_global_synonym`(`synonyms:global`)과 `add_synonyms`(DB별)로 반영한다 | 승인한 뒤에도 같은 리터럴이 계속 `unregistered`로 잡힌다. 그러면 §7 "제안 재발생 0"이 성립하지 않고, 승인한 항목이 대기열에 되돌아온다 | 승인 반영 대상을 type별로 고정한다. `eav_name`은 `synonyms:eav_names`에 쓰고 전역에도 함께 쓴다(현행 `_register_llm_mappings_to_redis` EAV 분기 `:1521-1540`와 같은 모양). `resource_type`은 `synonyms:resource_types`, `column`은 DB별 + 전역에 쓴다. 두 키에는 **단어 단위 추가 API가 없고** `save_*`는 필드를 통째로 HSET한다(`:1724`·`:1781`). 그래서 load·merge·save 헬퍼 `add_eav_name_synonym`·`add_resource_type_synonym`을 신설한다. 키는 `%`를 뗀 리터럴을 쓰고 대소문자는 보존한다 |
| **D-2** | S1은 `type=column`을 만들지 않는다. `unregistered`는 EAV NAME·RESOURCE_TYPE 리터럴만 반환한다 | §3.2 스키마의 `column`은 S2·S3에서만 나온다 | 스키마 설명만 정정한다(코드 영향 없음) |
| **D-3** | **S3 항목은 이미 사전에 들어가 있다.** `llm_inferred` 매핑은 `_register_llm_mappings_to_redis`(`:424`)가 즉시 등록한다(C-4 분기). pending 루프(`synonym_registrar.py:141-149`)는 실제로는 `operator`로 출처를 올리고 전역에 다시 쓰는 일만 한다. 2.8 LLM 유사어 발견(`llm_synonym`)은 pending에도 오르지 않고 자동 등록만 된다 | S3 제안을 "승인해야 등록되는 후보"로 보여 주면 사실과 다르다. 기각해도 사전에서 빠지지 않아 기각이 무의미하다. 요건 1.2-2 "선택한 것만 영구 저장"이 S3에서는 성립하지 않는다 | S3는 **사후 확인 제안**(`status` 옆 `already_registered: true`)으로 표기한다. 승인은 출처를 `operator`로 올리는 것이고, 기각은 **등록 철회**(기존 단어 단위 삭제 `remove_synonyms`·`remove_global_synonym` + 기각 해시)다. 자동 등록 자체를 대기열 뒤로 옮길지는 §1.3이 기록한 사용자 선택(현행 유지)을 다시 묻는 일이므로 Q-4로 올린다 |
| **D-4** | S2 의미 매칭은 짝 정보를 버린다. `semantic_tables_matching_query`(`synonym_semantic.py:284`)의 반환형은 `set[str]`로 테이블명뿐이다. 또 스키마 분석 쪽 히트는 **테이블 보완용**이라 그 유사어가 SQL에 실제로 쓰였다는 보장이 없다 | 스키마 분석 S2(semantic)에서는 `key`·`word`를 확정할 수 없다. v1의 "둘 다 확정" 주장은 퍼지에서만 맞다 | S2 우선순위를 다시 정한다. ① 폼필 퍼지·의미 **확정 분기**(실제 매핑에 쓰인다) ② 스키마 분석 퍼지(`s`·`cand`·`score`가 루프 안에 있다) ③ 스키마 분석 의미는 반환형 확장 뒤로 미룬다. 퍼지·의미 플래그(`SYNONYM_FUZZY_MATCH`·`SYNONYM_SEMANTIC_MATCH`)는 코드 기본값이 off다. 이 체크아웃에는 `.env`가 없어 운영값을 확인하지 못했다. 둘 다 off면 S2 캡처는 0건이다 |
| **D-5** | **경로 커버리지 비대칭.** S1 캡처 지점(`query_generator`)은 단일 DB 조회에서만 돈다. `multi_db_executor`에는 `extract_synonym_usage` 호출이 없다. 1·2단 서브에이전트와 3단 task 서브그래프는 계산한 값을 결과 접기에서 버린다. v1 P4는 응답 메타를 **State 전파**로 채우도록 설계했다 | 존 팬아웃(plans/113) 같은 멀티 DB 질의에서는 캡처가 0건이다. 운영은 1단으로 확정되므로 채팅 패널이 운영에서 거의 뜨지 않는다 | ① 캡처는 노드 안에서 대기열에 **직접 적재**한다(턴당 1회 배치 · State 전파 불필요). `multi_db_executor`에는 DB별 생성 SQL 기준으로 대칭 캡처를 넣는다 ② 패널 데이터는 라우트가 턴 종료 뒤 대기열에서 이번 턴 캡처분(`user_id`+`thread_id`+턴 식별)을 조회해 붙인다. 응답 조립 10지점은 **공용 헬퍼 1개**로 채운다(F-14) |
| **D-6** | 전역 사전에는 출처 메타가 없다. `synonyms:global` 값은 `{words, description}`뿐이다(`redis_cache.py:1380-1390`) | P3의 "출처(operator/seed/llm) 배지"는 DB별 사전에서만 성립한다 | 전역 표에는 출처 배지를 두지 않는다(저장 형식 불변 — D-019·D-051). 전역에 출처를 붙이는 저장 형식 확장은 범위 밖이다(§10) |
| **D-7** | **DB별 승인분은 영구가 아니다.** `invalidate(db_id)`·`invalidate_all`이 `schema:{db}:synonyms`를 지운다(F-2). plans/104의 설명 백업(`structure_store.py:672` `backup_descriptions`)은 초안을 적용한 시점의 스냅샷이라 승인분이 들어 있지 않다. 그래서 무효화 뒤 복원(`cache_manager.py:1467`)해도 승인분은 돌아오지 않는다 | 요건 1.2-2 "영구 캐시에 저장"이 DB별 반영분에서는 성립하지 않는다 | 영구성의 정본은 **전역 키**(`synonyms:global`·`:eav_names`·`:resource_types`)로 명시한다. DB별 반영분을 지킬지는 Q-5로 정한다(권고: 승인 시 설명 백업의 `synonyms`에도 병합 기록) |
| **D-8** | 설정 5종 신설 | plans/109의 노출 축소 목표와 충돌한다. 카탈로그 단언 350을 갱신해야 한다 | 권고: `SYNONYM_PROPOSALS` 플래그 1개만 만든다. TTL 30일·상한 500·패널 5건·소스 목록은 모듈 상수로 둔다(운영 근거가 생기면 승격). 노브가 필요하면 C등급으로 둔다 → Q-6 |
| **D-9** | 마스킹을 P1 utils에서 하도록 읽힌다(F-16) | `utils → security` import는 계층 위반이다(`arch_check --ci` 실패) | `scrub_pii` + 200자 절단은 적재 지점(nodes)이나 P2 저장 계층에서 적용한다. P1은 절단만 하는 순수 함수다 |
| **D-10** | 요건 1.2-3과 공존하는 비관리자 쓰기 경로 — 자연어 등록(`synonym_registrar`), 채팅 유사어 추가·삭제(`_ADMIN_ONLY_ACTIONS`에 없음), `/query/mapping-feedback`(역할 의존성 없음) | 대기열만 관리자가 확정하고, 같은 사전을 비관리자가 다른 경로로 바꿀 수 있다 | 본 계획 범위 밖이다(§1.3 "현행 유지"와 같은 축). 사실만 기록하고 후속 여부는 Q-7로 정한다 |

### 0.6 착수 순서 재편 (v1 P1~P5 골격 유지)

1. **P1** — 순수 함수(지문·S1·S3 shape·절단). S1은 단일(`query_generator`)과 멀티(`multi_db_executor`)를 **같은 차수**에 배선한다(D-5).
2. **P2** — type별 반영 대상표(D-1)와 단어 단위 추가 헬퍼 2종. 기각 = 철회(S3 · D-3). 영구성 정책(D-7 · Q-5).
3. **P3** — `src/api/routes/synonyms.py` + `src/static/js/admin-synonyms.js`(C-1 형식) + `log_admin_event`(C-2).
4. **P4** — 라우트 조회형 패널(D-5). D-151·D-187과 같은 구조화 패널을 쓴다.
5. **P5** — 감사·sweep·구조화 로그.
6. **S2** — 폼필 확정 분기 → 스키마 분석 퍼지 순으로 진행한다. 의미 매칭은 반환형 확장을 뒤로 미룬다(D-4). 퍼지·의미 플래그가 운영에서 off면 우선순위를 더 낮춘다.

착수 전 확인: Q-1~Q-3(v1)에 **Q-4(S3 의미)·Q-5(DB별 영구성)·Q-6(노브 수)·Q-7(연결점·비관리자 경로)** 을 더한다(§11).

**[v2.1]** 위 순서는 Q-8 (a), 즉 지금 범위를 유지할 때의 순서다. 권고안 (b)(사전 위생 우선)를 고르면 §0.7.4 순서를 따른다.

### 0.7 [v2.1] 성능 효과 판정 (2026-09-22)

> 사용자 질문: *"이 계획이 이 서비스의 성능 향상에 효과적일까?"* → 판정 기록 지시: *"추가하라."*

**판정: 지금 범위(v2)로는 효과가 작다.** 지연은 줄지 않고 오히려 조금 늘어난다. 정확도 효과는 간접적이고 느리게
나타나며, 최신 벤치에서 관측된 실패 원인을 겨냥하지 않는다. 관측된 유사어 결함은 "사전에 단어가 부족하다"가 아니라
"사전이 오염돼 있고 너무 넓게 매칭된다"이다. 다만 계획의 절반(사전 조회·삭제, 기각 목록, 자동 등록 승인제)은 그 결함에
맞는다. 범위를 뒤집으면 기여할 수 있다(Q-8).

#### 0.7.1 축별 판정

| 축 | 판정 | 근거 |
|---|---|---|
| 지연 | **개선 0 · 소폭 증가** | 가장 큰 비용은 답변 서술이다. `result_aggregator`가 p50 21.5초이고, 응답 길이와의 상관은 r=0.75(약 39자/초)다(plans/114 §1·§2.3). 타임아웃 55턴은 전부 SQL을 실행한 **뒤** 서술 단계에서 죽었다(§2.3). 본 계획은 이 구간을 건드리지 않는다. 오히려 턴마다 대기열 적재(Redis 쓰기 1회)와 패널 조회가 추가된다 |
| 정확도 — 관측 결함과의 정합 | **불일치** | 관측된 유사어 결함 3종은 모두 과잉 쪽이다. ① 동의어로 테이블을 강제 보충한 557회 중 61회가 화이트리스트 밖이었다(§2.8-②) ② 범용어 오염: `'서버'→acc_audit_log.host`가 154회 나왔고, '메모'·'평균'·'상태'도 무관한 테이블에 걸린다. 이것들은 시드가 아니라 **Redis 런타임 등록분**이다(§2.8-③) ③ 서브 테이블 동의어가 정확 매칭으로 우선돼 759행이 공란이 됐다(H-03 · §2.7). "사전에 단어가 없어서 틀렸다"고 특정된 사례는 없다. 본 계획은 사전에 단어를 **더하는** 경로이고, 오염의 원천인 자동 등록은 현행 유지로 남긴다(§1.3) |
| 정확도 — 최대 오답 원인 | **무관** | SQL 생성 241회 중 61회(25.3%)가 SQL이 아니라 산문이었다. 그중 36회는 알람 의도 소실이다(2단 `intent_planner` 결정적 단락 · §2.5) |
| 전제 측정 | **없음** | 유사어 A/B 축은 있다(`scripts/eval_text2sql.py:75`·`:81` `AB_AXES`의 `synonym_fuzzy`). 하지만 plans·docs에 기록된 결과가 없다. "사전이 커지면 EX가 오른다"는 검증되지 않은 가정이다 |
| 운영 도달 | **낮음** | S1 미등록 리터럴은 LLM이 사전 없이 맞힌 값(등록해도 이득이 작다)이거나 환각한 값(관리자가 기각해야 한다)이다. 운영 폴스타 DB는 수동 프로필의 `known_attributes`가 EAV 사전에 이미 동기화된다(`schema_analyzer.py:720-735`). 운영은 1단이고 멀티 DB 질의가 많아서 D-5를 고치기 전까지 캡처는 사실상 0건이다. 효과는 관리자 승인 처리량에 묶인다 |

#### 0.7.2 기여할 수 있는 부분

| 본 계획 요소 | 맞닿는 관측 결함 | 관계 |
|---|---|---|
| P3 하단 「등록된 사전」(조회·단어 삭제) + 감사(C-2) | 범용어 오염 정리(plans/114 **G-F**) | G-F는 "삭제 대상 목록을 뽑아 확인받고 지운다"로 권고돼 있다. 그런데 사람이 보고 지울 도구가 지금 없다(글로벌 사전 API 0건 · §0.2) |
| 기각 목록(`synonym:proposals:rejected`) | 오염 재유입 | 한 번 지운 범용어가 자동 등록으로 되돌아오지 않게 막는다 |
| 자동 등록을 승인 게이트 뒤로(Q-4 (b)) | 오염 원천(plans/114 **P-4 ②** 등록 지점 차단) | 규칙(P-4 ②)으로 먼저 막고, 규칙을 통과한 것은 사람이 확정하는 두 층이 된다. 요건 1.2-2 "선택한 것만 영구 저장"도 문자 그대로 성립한다 |

#### 0.7.3 한계

- plans/114 run은 전부 2단, 1구간 223턴이라 **축 측정으로는 성립하지 않았다**(114 §1). 위 실패 관측은 서버 로그를 판독한 사실이라 근거로 쓴다.
- 지연 결론은 내부망 측정으로만 확정한다(D-240). 이 판정의 지연 축은 "본 계획이 병목 구간을 건드리지 않는다"는 구조 판단이다.
- 운영 `.env`의 `SYNONYM_*` 값은 이 체크아웃에서 확인하지 못했다(D-4).

#### 0.7.4 권고 — 범위 재편안 (Q-8 (b))

성능이 목적이라면 본 계획보다 앞설 것이 셋 있다.
1. plans/114 **P-3**(서술 축소)와 **G-E**(시간 상한에 걸려도 조회 결과가 있으면 표로 완료)
2. 산문 생성·알람 의도 소실 — 111 D-1 교정은 이미 랜딩돼 있지만 `COMPOSITE_TASK_FRAME_ENABLED`가 폐쇄망에서 off다. 3단 전환도 같은 문제를 푼다(114 §2.5)
3. plans/114 **P-4**(강제 보충 축소 · 범용어 등록 차단)

본 계획은 그 뒤에 순서를 뒤집어 진행한다.

| 차수 | 내용 | 대응 |
|---|---|---|
| 1차 — 사전 위생 | ① 관리자 사전 조회·단어 삭제 API와 탭(P3 하단 · 감사 C-2) ② 기각 목록 ③ 자동 등록(`llm_inferred`·`llm_synonym`)을 대기열 뒤로 옮긴다(Q-4 (b)). 범용어 차단 규칙은 plans/114 P-4 ②가 소유한다 — 먼저 랜딩된 쪽의 규칙을 공유하고 사본을 만들지 않는다 | 114 G-F의 도구 · P-4 ②의 사람 확정 층 |
| 게이트 | 유사어 on/off A/B(`eval_text2sql.py --ab`)로 사전이 EX에 기여하는 정도를 먼저 잰다. 로직은 로컬 MLX로 확인하고, 결론은 내부망 결과로 낸다 | 미검증 전제 해소 |
| 2차 — 캡처 | S1·S2 캡처와 채팅 패널(P4). 게이트에서 효과가 확인될 때만 진행한다 | 요건 1.2-1·1.2-4·1.2-5 |

재편안은 요건 1.2-1(자동 제안)을 2차로 미루고, §1.3에 기록된 사용자 선택(자동 등록 현행 유지)을 바꾼다. 둘 다
사용자가 확정한 사항이라 Q-8로 올린다.

---

## 1. 배경과 요건

### 1.1 무엇이 없나 (실측, 2026-08-25)

유사어 사전 자체는 이미 **TTL 없는 영구 저장**이고(`redis_cache.py:1071-1074`, D-011), 캐시 클리어
시에도 글로벌 사전은 보존된다(`redis_cache.py:1423-1465`). 빠진 것은 **"질의 처리 중 드러난
유사어 후보를 사람이 골라 영구화하는 경로"**이며, 세 층 모두 갭이 있다.

| 층 | 현행 | 갭 |
|---|---|---|
| **후보 캡처** | `extract_synonym_usage()`가 `unregistered`(SQL에 쓰였으나 사전에 없는 EAV NAME·RESOURCE_TYPE 리터럴)를 **이미 반환**(`src/utils/synonym_usage.py:107-117`), `query_generator.py:806`에서 호출 | **소비 지점이 로그·UI 표시뿐**. 어디에도 축적되지 않아 턴이 끝나면 소실 |
| **후보 저장** | 폼필 경로만 `pending_synonym_registrations`(State, `field_mapper.py:620-646`) | **State는 세션 스코프** — 대화가 끝나면 후보가 사라진다. 텍스트 질의 경로는 후보 생성 자체가 0건 |
| **승인 UI** | 채팅에서 **자연어로만**("전체 등록"/"1, 3번 등록"/"건너뛰기") — `synonym_registrar.py`가 3단(상위 파싱→결정적 선파서→LLM 분류)으로 해석, 실패 시 재질의 | 전용 화면 없음. 관리자 대시보드 탭 6종(`dashboard.html:38-43`)에 **유사어 관리 탭 없음**. 관리자 API는 조회/LLM생성/컬럼삭제 3개뿐(`schema_cache.py:550-640`)이고 **글로벌 사전(`synonyms:global`) API는 0건** |

부수적으로, 사전을 사람이 고치는 유일한 경로는 **매핑 보고서 MD 다운로드 → 수정 → 재업로드**
(`query.py:1786-1870`)라 채팅 흐름과 분리돼 있고 폼필 질의에만 붙는다.

> **[v2 정정]** 앵커 이동: 영구 저장 `redis_cache.py:1244-1249`(F-1) · 글로벌 보존 `:1593-1680`(F-2 — **DB별 synonyms는
> 무효화로 삭제된다**) · `synonym_usage.py:106-118` · 호출 `query_generator.py:978`(F-3) · pending `nodes/field_mapper.py:507-537`(F-4) ·
> 탭 **8종** `dashboard.html:85-92`(F-5) · 유사어 API 3종 `schema_cache.py:639·666·705`(전체 라우트는 14종 — F-6) ·
> 매핑 보고서 `query.py:2608-2700`(F-7). 표 내용 중 두 가지가 틀리거나 빠졌다. ①"사람이 고치는 유일한 경로"가 아니다.
> 채팅 자연어 등록(`synonym_registrar`)과 채팅 유사어 추가·삭제(`cache_management`)도 사전을 고치며, 셋 다 비관리자도 쓸 수
> 있다(C-5 · D-10). ②폼필 LLM 매핑은 pending과 별개로 **이미 자동 등록된다**(D-3). 캡처 소비 지점은 v1 표 그대로 로그·UI뿐이다.
> 다만 UI까지 가는 경로는 3단 직결 단일 DB 체인 하나뿐이다(§0.2 · D-5).

### 1.2 사용자 요건 (2026-08-25 확정)

1. **자동 제안**: "쿼리 생성 시 사용했던 유사어가 캐시에 등록되어 있지 않았다면 **해당 내용을
   자동으로 제안 항목으로 등록**" — 사용자가 수동으로 제안하는 방식이 아니다.
2. **선택적 영구 저장**: 제안 중 **선택한 것만** 영구 캐시(Redis)에 저장한다.
3. **권한**: 제안 적재는 시스템이 자동으로, **확정(영구 쓰기)은 관리자**가 한다.
4. **후보 범위**: 폼필 후보 + **텍스트 질의 경로의 근사 히트**까지 수집한다.
5. **UI 배치**: **채팅 인라인 패널 + 관리자 대시보드 탭** 둘 다.

### 1.3 이 계획이 하지 않는 것 (범위 밖)

- 매칭 알고리즘 변경(퍼지·의미 검색 자체) — Plan 61 E5-1/E5-4 소관, 본 계획은 그 **출력을 수집**만 한다.
- `increment_synonym_usage`/`prune_stale_synonyms` 런타임 배선 — 구현체는 있으나 호출부가 0건인
  별개 갭이다(§10 후속). 본 계획은 제안 랭킹에 사용횟수를 **쓰지 않고** 자체 근거(등장 횟수)만 쓴다.
- 자동 등록 경로(`_apply_llm_synonym_discovery` → `add_global_synonym`, `field_mapper.py:1118-1133`)의
  승인제 전환 — 사용자가 선택지 3안 중 2안(폼필+텍스트 근사)을 택했으므로 **현행 유지**한다.
  **[v2 정정]** 앵커는 발견 `document/field_mapper.py:916` → 등록 `:1148`, 추론 매핑 등록 `:1458`(호출 `:424`)이다(F-12).
  "현행"의 내용도 바뀌었다. 2026-09-17부터 구조 정보(수동 프로필·승인본)가 있는 DB만 전역에 쓰고, 없는 DB는 그 DB 캐시에
  `llm` 출처로만 쓴다(G-11 (b) · C-4). **현행 유지의 귀결**로 S3 후보는 제안 시점에 이미 사전에 있다. 그래서 요건 1.2-2
  "선택한 것만 영구 저장"은 S3에서 성립하지 않는다(D-3 · Q-4).

---

## 2. 설계 개요

```
 [캡처]                        [대기열]                    [승인]                 [사전]
 query_generator  ──┐
 (unregistered)     │
 schema_analyzer  ──┼──▶ synonym_proposals ──▶ 관리자 탭 / 채팅 패널 ──▶ add_synonyms
 (fuzzy/semantic)   │     (Redis, 영구)          승인 · 교정 · 기각        add_global_synonym
 field_mapper     ──┘            │                      │                  (TTL 없는 영구)
 (llm_inferred)                  │                      ▼
                                 └──────────────  synonym_proposals:rejected
                                    (재제안 차단)
```

**원칙 4가지**

1. **캡처는 자동, 쓰기는 승인 게이트** — 제안 적재는 사전을 바꾸지 않는다. 매칭 계단
   (`schema_analyzer`·`field_mapper`·`query_generator`)은 **대기열을 읽지 않는다**. 승인된 항목만
   기존 `add_synonyms`/`add_global_synonym`을 타고 사전에 들어간다(경로 신설 0건).
   **[v2 정정]** "경로 신설 0건"은 성립하지 않는다. S1(`eav_name`·`resource_type`)은 `synonyms:eav_names`·`synonyms:resource_types`에
   반영해야 미등록 판정이 풀린다. 두 키에는 단어 단위 추가 API가 없으므로 병합 헬퍼 2종을 신설한다(D-1). 또 S3는 제안 시점에
   이미 사전에 있다. 따라서 "적재는 사전을 바꾸지 않는다"는 S1·S2에만 해당한다. S3에서 승인은 출처 승격, 기각은 철회다(D-3).
2. **모호한 짝은 자동 확정하지 않는다** — 미등록 리터럴은 "어떤 질의어와 짝인지"가 결정적으로
   확정되지 않는 경우가 있다. 이때 임의 짝짓기 대신 **증거(질의 원문·SQL 스니펫·매칭 점수)를
   첨부해 사람에게 넘긴다**. D-142가 앵커 모호 시 등록 0건으로 결론낸 것과 같은 이유다 —
   오등록은 조용히 검색 품질을 갉아먹고 자기강화된다.
3. **침묵 수집 금지** — 이번 턴에 무엇이 캡처됐는지 채팅 패널로 사용자에게 보여준다. 사용자는
   그 자리에서 잘못된 제안을 기각하거나 표기를 교정할 수 있다.
4. **무회귀 옵트인** — 플래그 `SYNONYM_PROPOSALS=false` 기본. OFF면 캡처·저장·UI 전부 미동작이며
   기존 경로(자연어 승인 포함)는 무변경이다.
5. **[v2 추가] 경로 대칭은 State가 아니라 저장소로 보장한다** — 캡처는 노드가 대기열에 직접 적재하고, 패널은 라우트가
   대기열에서 이번 턴 캡처분을 조회해 붙인다. 사다리 단(1·2·3)과 단일/멀티 DB 중 어느 경로로 가도 결과 접기가 값을
   버리지 않는다(D-5 · C-6).

### 2.1 기존 결정과의 관계 — Redis 키 신설이 D-142와 충돌하지 않는 근거

D-142는 대안 "대칭 집합 저장소 신설(`synonym:sets`)"을 **기각**했다. 기각 사유는 두 가지였다:
(a) `schema_analyzer`·`field_mapper`·`query_generator` **매칭 경로 전반에 대칭 주입이 필요**,
(b) Redis 키 스키마 신설이 D-019·D-051 캐시 구조 불변과 충돌.

본 계획의 `synonym:proposals`는 (a)에 해당하지 않는다 — **매칭 계단이 이 키를 읽지 않는다**.
승인 전까지 어떤 매칭에도 영향이 없고, 승인 후에는 기존 사전 키(`synonyms:global` 등)로만
반영되므로 **캐시 구조는 그대로다**. (b)에 대해서는 Plan 61 E5-2가 이미 같은 판단으로 별도 키
`schema:{db}:column_value_index`를 신설한 선례가 있다(`redis_cache.py:1710-1750` — "의미가 달라
그 키를 덮어쓰지 않고 전용 키를 신설한다, 회귀 0").

> **[v2 정정]** 선례 위치는 `redis_cache.py:1901-1960`이다(F-8). plans/104도 같은 판단으로 관리자 자산 키
> `schema:{db_id}:{ADMIN_ASSET_SUFFIXES}`(버전·초안·스냅샷·등록 상태)를 신설했다. **[v2 추가]** 새 키는 `schema:` 접두가 아니므로
> `invalidate_all`(`schema:*` 스캔 · `:1634-1680`)에 지워지지 않는다. 기존 `synonyms:*` 전역 키와 같은 수명이다. D-1 헬퍼 2종은
> 기존 키 **안의 값**을 병합할 뿐 새 키를 만들지 않으므로 이 절의 판단을 바꾸지 않는다.

---

## 3. 데이터 모델

### 3.1 Redis 키 (신설 2개)

| 키 | 타입 | TTL | 내용 |
|---|---|---|---|
| `synonym:proposals` | Hash | 없음(항목별 만료 필드 + 조회 시 sweep) | field=제안 지문, value=제안 JSON |
| `synonym:proposals:rejected` | Hash | 없음(동일 sweep) | field=제안 지문, value=`{rejected_at, by, reason}` |

**Hash 필드에는 개별 TTL이 없으므로** 무한 누적을 막는 장치를 코드로 둔다 — 항목의 `expires_at`을
조회·적재 시점에 sweep하고(기본 30일), 전체 상한(기본 500건) 초과 시 **근거 점수 하위부터 축출**한다.
Known Mistakes「데몬류 in-memory dict는 값 bound뿐 아니라 키 만료 sweep도 추가」와 같은 함정이다.

### 3.2 제안 항목 스키마

```json
{
  "id": "sha1(source|type|key|word|db_id)[:16]",
  "source": "unregistered_literal | fuzzy_hit | semantic_hit | llm_inferred_mapping",
  "type":   "eav_name | resource_type | column",
  "key":    "Hostname",              // 사전 키(= 정본 표기). 미확정이면 null
  "word":   "서버명",                 // 등록할 유사어(사용자 표기). 미확정이면 null
  "db_id":  "gp",                    // 글로벌 전용이면 null
  "confidence": 0.87,                // fuzzy/semantic만. 그 외 null
  "hits": 3,                          // 동일 지문 재등장 횟수(근거 점수)
  "first_seen": "2026-08-25T10:00:00+09:00",
  "last_seen":  "2026-08-25T14:20:00+09:00",
  "expires_at": "2026-09-24T14:20:00+09:00",
  "status": "pending | user_flagged | approved | rejected",
  "evidence": {
    "query": "여의도 서버명 목록 뽑아줘",     // 원문(마스킹 후, 200자 절단)
    "sql_snippet": "WHERE NAME = 'Hostname'", // 120자 절단
    "matched_via": "flex_match",
    "user_terms": ["서버명"]
  }
}
```

- **`key`/`word` 중 하나가 null이면 "미확정 제안"** — UI에서 사람이 채워야 승인 버튼이 활성화된다(원칙 2).
- `hits`는 같은 제안이 여러 질의에서 반복 등장할수록 올라가며, 정렬·축출의 기준이 된다.
- `evidence.query`는 **감사 로깅과 동일한 마스킹 헬퍼**를 통과시킨다(민감정보 유출 방지).
  **[v2 정정]** 감사는 질의 원문을 마스킹하지 않는다(`audit_service.log_user_request`). 쓸 헬퍼는
  `src/security/pii_filter.py:365` `scrub_pii`다(F-16). 이 헬퍼는 infrastructure 계층이므로 P1(utils)이 아니라 적재 지점(nodes)이나
  P2에서 적용한다. P1은 절단만 한다(D-9).
- **[v2 추가]** `type` 값의 출처: S1은 `eav_name`·`resource_type`만 만들고, `column`은 S2·S3에서만 나온다(D-2). S1 `key`는 리터럴에서
  `%`를 뗀 값을 쓰고 대소문자는 보존한다(`synonym_usage.py:110`·`:115`가 비교에 쓰는 정규화와 같다).
- **[v2 추가]** S3 항목에는 `already_registered: true`와 등록 위치(`scope: "global" | "db"`)를 싣는다. 제안 시점에 이미 사전에
  있으므로 UI가 "추가"가 아니라 "확인(유지·철회)"으로 보여 준다(D-3).
- **[v2 추가]** 패널 조회용으로 `evidence`에 `user_id`·`thread_id`·턴 식별을 싣는다. 관리자 탭 응답에서는 `user_id`를 가린다(C-8 · D-5).

---

## 4. Phase별 구현

### P1 — 후보 캡처 계층 (utils, 순수 함수)

**신규**: `src/utils/synonym_proposals.py` — Redis I/O 없는 순수 로직(계층 규칙: utils).

```
build_proposals_from_usage(usage, *, query, sql, db_id) -> list[dict]   # S1
build_proposal_from_match(key, word, score, matcher, *, query, db_id) -> dict  # S2
build_proposals_from_pending(pending, *, query) -> list[dict]            # S3
proposal_fingerprint(p) -> str
```

**3개 캡처 소스**

| 소스 | 배선 지점 | 캡처 대상 | 확정 여부 |
|---|---|---|---|
| **S1 미등록 리터럴** | `query_generator._instrument_synonym_usage()`(`query_generator.py:800-835`)의 `usage["unregistered"]` | SQL에 쓰였으나 사전에 없는 EAV NAME·RESOURCE_TYPE 값 | `key`=리터럴 확정, `word`는 `query_targets` 중 기존 매핑에 쓰이지 않은 용어가 **정확히 1개일 때만** 자동 채움. 그 외 null(사람 확정) |
| **S2 근사 히트** | `schema_analyzer._synonym_tables_matching_query()`(fuzzy/semantic 분기, `schema_analyzer.py:120-125`)와 `field_mapper._synonym_match()` | 퍼지·의미 매칭으로 **실제 사용된** 히트 중 질의어 표기가 사전에 없는 것 | `key`(사전 키)·`word`(질의어) 둘 다 확정. 승인 시 다음 질의부터 **정확 히트로 승격** |
| **S3 폼필 추론 매핑** | `field_mapper._build_pending_registrations()`(`nodes/field_mapper.py:620-646`) | 기존 `pending_synonym_registrations` 항목 | `key`=컬럼, `word`=필드명 확정 |

> **[v2 정정] 캡처 소스 표의 앵커와 판정**
> - **S1**: 앵커는 `query_generator.py:971-1022`이다(F-3). **멀티 DB 대칭 지점이 빠져 있었다.** `multi_db_executor`는
>   `extract_synonym_usage`를 부르지 않으므로 DB별 생성 SQL 기준 캡처를 새로 배선한다(D-5). 1·2단 서브에이전트와 3단 task
>   서브그래프는 같은 `query_generator` 노드 함수를 거치므로, 노드 안에서 직접 적재하면 따로 배선할 필요가 없다.
> - **S2**: v1의 "`key`·`word` 둘 다 확정"은 **퍼지에서만** 맞다. 스키마 분석 쪽 의미 매칭(`schema_analyzer.py:143-157`)이 부르는
>   `semantic_tables_matching_query`(`synonym_semantic.py:284`)는 테이블명 `set[str]`만 돌려준다(D-4). 폼필 쪽 지점은
>   `_synonym_match()`가 아니다 — 그 함수는 정확 매칭이다(F-9). 근사 히트는 `_apply_fuzzy_synonym_fallback`(`document/field_mapper.py:675`)과
>   `_apply_semantic_synonym_fallback`(`:744`)의 **임계 이상 확정 분기**다. 스키마 분석 쪽 히트는 테이블 보완용이라 SQL에 쓰였다는
>   보장이 없으므로 폼필 확정 분기를 우선한다(§0.6-6). 두 플래그가 운영에서 off면 S2는 0건이다(운영값 미확인 — 이 체크아웃에 `.env` 없음).
> - **S3**: 앵커는 `nodes/field_mapper.py:507-537`(호출 `:147`)이다(F-4). **이 항목은 제안 시점에 이미 사전에 등록돼 있다.**
>   `_register_llm_mappings_to_redis`(`document/field_mapper.py:424` → `:1458`)가 즉시 쓰기 때문이다(D-3). `llm_synonym`(2.8 발견 ·
>   `:1035`·`:1053`)은 pending에도 오르지 않고 자동 등록만 되므로, S3에 포함할지를 Q-4에서 함께 정한다.

**규칙**
- 캡처는 **실패해도 본 흐름에 영향이 없어야 한다** — S1이 이미 그렇듯(`query_generator.py:800`
  "실패해도 SQL 생성에는 영향이 없다") 각 소스를 **개별 try/except**로 감싼다(한 try에 묶지 않는다).
- 캡처 자체는 순수 함수라 **매 질의 오버헤드가 dict 조립 수준**이며, 플래그 OFF면 호출 자체를 건너뛴다.
- **재제안 차단**: 적재 직전 `rejected` 해시를 조회해 지문이 있으면 버린다.

**검증**: `tests/test_utils/test_synonym_proposals.py` — 지문 결정성, 자동 짝짓기가 후보 2개 이상일 때
null을 남기는지, 마스킹·절단, S1/S2/S3 각 shape.
**[v2 정정]** P1은 순수 함수라 마스킹을 하지 않고 절단만 한다(D-9). 마스킹 단언은 적재 지점 테스트로 옮긴다.
**[v2 추가]** 단일·멀티 대칭 단언을 추가한다. 같은 질의를 `query_generator` 경로와 `multi_db_executor` 경로로 보냈을 때 S1 제안
shape가 같아야 한다(D-5).

### P2 — 대기열 저장소 (infrastructure)

**변경**: `src/schema_cache/redis_cache.py` — 기존 유사어 API 옆에 대기열 API 추가.

```
upsert_proposals(items) -> int          # 지문 충돌 시 hits+1 · last_seen 갱신(중복 행 생성 금지)
load_proposals(*, status=None, db_id=None, limit, offset) -> (list, total)
approve_proposals(ids, *, edits, actor) -> dict   # 사전 반영 + 대기열 제거
reject_proposals(ids, *, reason, actor) -> dict   # rejected 해시로 이동
sweep_proposals() -> dict                # 만료·상한 축출
```

- **승인 적용은 기존 경로 재사용**: `type`에 따라 `add_global_synonym`(글로벌) +
  `add_synonyms(db_id, column, [word], source="operator")`(DB별). `source="operator"`로 태깅해야
  E5-3 감쇠(`prune_stale_synonyms`)가 승인 항목을 보호한다(`synonym_loader`의 시드 태깅과 동일 관례).
  **[v2 정정] 이 문장대로면 S1은 승인해도 해소되지 않는다(D-1).** 반영 대상을 type별로 고정한다.

  | `type` | 반영 대상(정본) | 함께 쓰는 곳 | 비고 |
  |---|---|---|---|
  | `eav_name` | `synonyms:eav_names[key]` — **신설** `add_eav_name_synonym`(load·merge·save) | `synonyms:global[key]` | 현행 자동 등록 EAV 분기(`document/field_mapper.py:1521-1540`)와 같은 모양이다. 구조 정보 없는 DB의 EAV는 0건(G-11 (b)와 같은 규칙) |
  | `resource_type` | `synonyms:resource_types[key]` — **신설** `add_resource_type_synonym` | — | 현재는 `save_resource_type_synonyms`(`:1724`)가 필드를 통째로 HSET만 한다 |
  | `column` | DB별 `add_synonyms(..., source="operator")` | 구조 정보가 있으면 `add_global_synonym(bare)` | G-11 (b) 분기를 그대로 따른다(C-4). 구조 정보가 없는 DB에 전역 쓰기 0 |

  S3의 기각은 **철회**다. 기존 단어 단위 삭제 API인 `remove_global_synonym`(`:1490`)과 DB별 `remove_synonyms(db_id, column, words)`
  (`:940` · `cache_manager.py:729`)로 지운 뒤 기각 해시에 넣는다(D-3). EAV 사전 쪽 단어 삭제 API는 없으므로 `eav_name` 철회에
  필요하면 D-1 헬퍼와 함께 만든다.
  **[v2 추가] 영구성**: 요건 1.2-2의 "영구"는 전역 키에서만 성립한다. DB별 반영분은 `invalidate`가 지우고, plans/104 설명 백업에도
  없다(D-7). 처리 방식은 Q-5로 정한다.
  **[v2 추가]** 전역 사전 값은 `{words, description}`뿐이라 출처 태그를 실을 수 없다(`:1383` · D-6). 출처 보호(C-3)는 DB별 사전에만
  적용된다.
- **부분 성공을 침묵하지 않는다** — 5건 중 2건 실패면 성공/실패 목록을 모두 반환하고 UI가 표시한다.
- **쓰기 직전 결정적 검증**(D-142 ⑤와 동일 규약): 길이 1~64자, 허용 문자, 중복 제거,
  기존 등록과 충돌 시 침묵 병합 금지(충돌 사실을 응답에 노출).
- **재오염 차단 가드 재사용**: 서버명/서버이름류 → hostname 등록 거부 판정
  (`src/utils/query_gen_common.py:735` "자동 유사어 등록 차단(재오염 방지)")을 승인 경로에도 적용한다.
  관리자가 명시 승인해도 이 가드에 걸리면 **경고를 띄우고 확인을 한 번 더 받는다**.
  **[v2 정정]** 가드 위치는 `is_servername_to_hostname`(`query_gen_common.py:1162`)이다(F-11). 현재 호출처는 폼필 자동 등록(`document/field_mapper.py` 6곳)과
  `synonym_semantic.py`이다.

**검증**: `tests/test_schema_cache/test_synonym_proposals_store.py` — upsert 멱등·hits 증가,
승인 시 실제 사전 키 반영, 기각 후 재적재 차단, sweep 경계(strictly-older), Redis 미가용 시 graceful.
**[v2 추가]** type별 반영 대상 단언 — `eav_name` 승인 뒤 `load_eav_name_synonyms()`에 key가 있고, 같은 SQL로
`extract_synonym_usage`를 다시 돌리면 `unregistered`가 0건이어야 한다(D-1 회귀 방지). S3 기각 뒤 사전에서 단어가 빠져야 한다(D-3).
구조 정보가 없는 DB의 `column` 승인은 전역 쓰기가 0건이어야 한다(G-11 (b)).

### P3 — 관리자 API + 대시보드 탭

**신규 라우트**: `src/api/routes/synonyms.py` (전부 `Depends(require_admin_user)`)

| 메서드 | 경로 | 용도 |
|---|---|---|
| GET | `/api/v1/admin/synonyms/proposals` | 대기열 조회(status·db_id·source 필터, 페이지네이션) |
| POST | `/api/v1/admin/synonyms/proposals/approve` | `{items: [{id, key, word, db_id}]}` — 교정값 동봉 승인 |
| POST | `/api/v1/admin/synonyms/proposals/reject` | `{ids, reason}` |
| GET | `/api/v1/admin/synonyms/dictionary` | 글로벌 사전 조회(출처·메타 포함) — **현재 API 0건인 영역** |
| DELETE | `/api/v1/admin/synonyms/dictionary/{key}/{word}` | 개별 단어 삭제(기존 컬럼 단위 삭제보다 세밀) |

DB별 사전 조회는 기존 `GET /admin/schema-cache/{db_id}/synonyms`를 그대로 쓴다(중복 신설 금지).

> **[v2 정정]** 기존 DB별 조회는 `schema_cache.py:639`에 있다. 같은 파일의 스키마 캐시 라우트는 14종이다(F-6).
> 글로벌 사전 조회·단어 삭제 API는 여전히 0건이므로 두 신설 라우트가 첫 API다.
> **[v2 추가]** 모든 응답에 `audit_logged`를 싣는다(D-227 ⑥ · C-2). 승인 응답에는 type별 반영 결과(`targets: ["eav_names", "global"]`)와
> 가드 경고를 싣는다(D-1). `dictionary` 조회의 출처 배지는 DB별 사전에만 있다(D-6).

**UI**: `dashboard.html`에 탭 `data-tab="synonyms"` 추가 + `admin.js`에 로더 —
기존 `users`/`auditlogs`/`incidents` 탭과 **동형 패턴**(`admin.js:1163,1387,1561`)으로 작성한다.

> **[v2 정정]** 인용한 앵커는 `loadUsers :1496` · `loadAuditLogs :1895` · `loadIncidents :2177`로 옮겨졌다(F-13). 선례도 바뀌었다.
> 가장 최근 탭인 「DB 구조」(plans/104 A-7)는 `admin.js` 인라인이 아니라 **별도 모듈** `src/static/js/admin-db-structure.js`에 있다.
> 이 모듈은 `window.AdminApi`로 토큰·401·알림을 재사용하고, 서버 값을 createElement/textContent로만 넣는다(XSS 방지 ·
> `dashboard.html:224`·`:578`). 유사어 탭도 `src/static/js/admin-synonyms.js`로 같은 형식을 따른다. 제안 원문(`evidence.query`)은
> 사용자 입력이므로 HTML 문자열로 넣지 않는다.

화면 구성:
- 상단 **제안 대기열** 표 — 체크박스 · 소스 배지 · `key ← word` (미확정이면 입력칸) · 근거(질의 원문
  툴팁) · hits · 신뢰도 · 최종 등장. 하단에 [선택 승인] [선택 기각(사유)] 버튼.
- 하단 **등록된 사전** 표 — 글로벌/DB별 토글, 검색, 출처(operator/seed/llm) 배지, 개별 삭제.

**검증**: `tests/test_api/test_synonym_proposals_api.py` — 비관리자 401/403, 승인 후 사전 반영,
교정값 우선, 미확정 항목 승인 시 400, 감사 로그 기록.

### P4 — 채팅 인라인 패널

**응답 메타 확장**: `query.py`의 응답 조립부(`:827`, `:1108`, `:1284`, SSE `:1036`·`:1059` — **전 경로 대칭**,
Known Mistakes「단일/멀티 경로 대칭」)에 `synonym_proposals` 추가 — 이번 턴 신규 캡처 요약(최대 5건).

> **[v2 정정]** 응답 조립부는 5지점이 아니라 **10지점**이다. `form_fill_clarification` 배선이 기준이며, 동기·SSE × 텍스트·파일 조합이다:
> `:1472`·`:1755`·`:1781`·`:1833`·`:1859`·`:2042`·`:2432`·`:2462`·`:2503`·`:2531`(F-14). 값을 **State에서 꺼내면 안 된다**.
> 1·2단 서브에이전트와 3단 task 서브그래프는 결과를 접을 때 `synonym_usage`를 버리고, 멀티 DB는 아예 계산하지 않는다(D-5).
> 그래서 라우트가 턴 종료 뒤 대기열에서 이번 턴(`user_id`+`thread_id`+턴 식별) 캡처분을 조회하는 **공용 헬퍼 1개**를 10지점에서
> 부른다. 이렇게 하면 사다리 단에 따른 비대칭이 구조적으로 생기지 않는다.

**패널**: `app.js`에 `renderSynonymProposalPanel()` — **폼필 HITL 패널(D-151, `app.js:1285-1340`)과
동형**으로 작성한다. 그 주석이 명시하듯 "자연어 재조합·LLM 파싱 없음"이 핵심이다.

> **[v2 정정]** 폼필 패널은 `renderFormFillPanel :2108-2182` · `appendFormFillPanelToLastBubble :2183`로 옮겨졌다(F-15). 체크박스와
> [선택 삭제] 모양이 더 가까운 선례는 D-187 양식 저장 값 패널(`renderFormMemoryPanel :2195`)이다(C-7). S3 제안(`already_registered`)
> 행은 [유지]·[철회 요청]으로 표기한다(D-3).

- 각 제안 행: 체크박스 · `key ← word`(미확정이면 입력) · 근거 배지
- 버튼: **[선택 항목 등록 요청]**(→ `status=user_flagged`로 승격, 관리자 탭 상단 노출) ·
  **[이 제안 제외]**(→ 즉시 기각, 재제안 차단)
- **관리자 계정으로 로그인한 경우에만** [바로 등록] 버튼을 추가로 노출(role 클레임을 **서버에서**
  검증 — UI 게이트는 인가가 아니다, Known Mistakes「보안·인가」).

**기존 자연어 승인의 처리**: `synonym_registrar`의 자연어 경로는 **그대로 둔다**(제거하지 않음).
패널은 그 위에 얹는 결정적 대안이며, 플래그 OFF면 현행 그대로다.

**신규 라우트(일반 사용자)**: `POST /api/v1/synonyms/proposals/flag` ·
`POST /api/v1/synonyms/proposals/dismiss` — 자기 세션에서 캡처된 제안 id에 한정.
**사전 쓰기 권한은 없다**(요건 1.2-3).
**[v2 추가]** "자기 세션"은 `Depends(require_user)`(`src/api/dependencies.py:218`)의 `sub`와 제안 `evidence`에 실린
`user_id`·`thread_id`가 일치하는지로 판정한다(C-8). 이 채널에는 사전 쓰기가 없지만, 같은 사전을 비관리자가 바꾸는 기존 경로 3종
(자연어 등록 · 채팅 유사어 추가·삭제 · 매핑 보고서 업로드)은 그대로 남는다(D-10 · Q-7).

**검증**: `tests/test_api/test_synonym_panel_flow.py` + 패널 렌더 스모크.
비관리자가 approve를 직접 호출하면 403인지 반드시 단언한다.

### P5 — 감사·정리 배선

- 승인/기각/삭제 전부 **감사 로그**(`request.app.state.audit_service`, `admin.py:381-418` 패턴).
  기록 필드: actor, action, 제안 id, key/word, db_id, 사유. 감사 저장소 없으면 응답에 그 사실을 명시
  (admin.py:726 선례 — 침묵 금지).
  **[v2 정정]** 감사 패턴은 공용 헬퍼 `src/api/admin_audit.py:19` `log_admin_event`로 일원화됐다(plans/104 S4 · F-17).
  `AuditEvent.ADMIN_ACTION` + 응답 `audit_logged` 형식을 `db_structure.py:185-199` `_audit`·`_with_audit`과 똑같이 쓴다(사본 금지 — D-053).
  사용자 라우트(flag·dismiss)는 사전을 바꾸지 않으므로 구조화 로그만 남긴다.
- `sweep_proposals()`를 대기열 조회·적재 시점에 호출(별도 데몬 없이 lazy sweep).
- 캡처·승인 건수를 구조화 로그로 남겨 이후 튜닝 근거로 삼는다.

---

## 5. 설정 플래그 (신규)

| 키 | 기본 | 용도 |
|---|---|---|
| `SYNONYM_PROPOSALS` | `false` | 전체 기능 on/off. OFF면 캡처·저장·UI·API 전부 미동작 |
| `SYNONYM_PROPOSAL_SOURCES` | `["unregistered_literal","fuzzy_hit","semantic_hit","llm_inferred_mapping"]` | 소스별 선택 활성화 |
| `SYNONYM_PROPOSAL_TTL_DAYS` | `30` | 미처리 제안 만료 |
| `SYNONYM_PROPOSAL_MAX` | `500` | 대기열 상한(초과 시 근거 점수 하위 축출) |
| `SYNONYM_PROPOSAL_PANEL_MAX` | `5` | 채팅 패널 1턴 표시 상한 |

`SynonymMatchConfig`(env_prefix `SYNONYM_`)에 필드를 추가한다. **list 필드는 `.env`에 JSON 배열로**
쓰고 `Field(default_factory=...)`로 선언한다(Known Mistakes「pydantic-settings」). `.env.example`에는
주석을 **별도 줄**로 넣는다.

> **[v2 정정 · 권고]** 노브 5종은 plans/109의 노출 축소 방향(`.env.example` 활성 키 목표 ≤45 · CS-56)과 어긋난다. 새 필드마다
> 설정 카탈로그 전수 단언(`tests/test_api/test_settings_catalog.py:189`·`:329` = 350)을 갱신해야 하고, 카탈로그 설명·도움말도
> 채워야 한다(D-8). **권고안**: `SYNONYM_PROPOSALS` 1개만 신설하고 나머지 4개(`SOURCES`·`TTL_DAYS`·`MAX`·`PANEL_MAX`)는
> `src/utils/synonym_proposals.py` 모듈 상수로 둔다. 운영 근거가 생기면 그때 노브로 올린다. 노브로 둘 경우에는 C등급
> (`settings_catalog.setting_grade`)으로 웹 UI 기본 화면에서 숨긴다. 어느 쪽으로 할지는 Q-6로 정한다.
> 설정 경로는 `src/config.py:247-282`(`SynonymMatchConfig`)이다.

---

## 6. 회귀 경계

- 플래그 OFF가 **기본**이며, OFF 경로에서 `extract_synonym_usage` 이후 코드 경로는 현행과 바이트 동일해야 한다.
- 캡처 예외는 삼키되 **로그로 가시화**한다(침묵 폴백 금지 — 다만 SQL 생성·응답에는 영향 없음).
- Redis 미가용 시 캡처는 no-op, 채팅 패널은 미표시. 질의는 정상 완료된다.
- 매칭 계단은 대기열을 **읽지 않는다** — 승인 전 제안이 검색 품질에 영향을 주면 승인 게이트가 무의미해진다.
  이 불변식을 테스트로 고정한다(대기열에 항목이 있어도 매칭 결과가 동일).
- `python scripts/arch_check.py --ci` 통과: utils(순수) → schema_cache(infrastructure) →
  nodes(application) → api(interface) → static(entry) 방향만 사용.
  **[v2 정정]** `static/`은 `arch_check`의 계층 매핑 대상이 아니다(`MODULE_LAYER_MAP`에 없음). P1(utils)은 `src.security`
  (infrastructure)를 import할 수 없으므로 마스킹은 nodes나 P2에서 한다(D-9).
- **[v2 추가]** 플래그 OFF면 `multi_db_executor`에 새로 넣는 캡처 호출도 건너뛴다. 멀티 DB 경로에 역조회 비용이 새로 붙지 않게
  하고, 기존 멀티 경로 테스트를 무회귀로 유지한다(D-5).
- **[v2 추가]** 공용 조회 헬퍼는 플래그 OFF면 응답 10지점에 키를 싣지 않는다. 응답 스키마 단언 테스트(`tests/test_api/test_routes.py` 등)가
  비트 동일해야 한다.

---

## 7. 검증 계획

| 축 | 방법 | 통과 기준 |
|---|---|---|
| 캡처 정확성 | S1/S2/S3 단위 테스트 | 미등록 리터럴이 제안으로, 이미 등록된 것은 제안 0건 |
| 자동 짝짓기 안전 | 후보 2개 이상 시나리오 | `word=null`로 남고 승인 API가 400 |
| 멱등·중복 | 동일 질의 3회 | 행 1개, `hits=3` |
| 재제안 차단 | 기각 후 동일 질의 | 대기열 0건 |
| 승인 반영 | 승인 → 재질의 | 다음 턴에 **정확 히트**로 잡히고 제안 재발생 0 |
| 권한 | 비관리자 approve 호출 | 403, 사전 무변경 |
| 무회귀 | 플래그 OFF 전체 테스트 | 기존 테스트 전건 통과, 유사어 관련 153건(Plan 61 §12) 포함 |
| 계층 | `arch_check --ci` | exit 0 |
| **[v2 추가]** S1 승인 반영 키 | `eav_name`·`resource_type` 승인 → 같은 SQL 재역조회 | `unregistered` 0건(D-1) |
| **[v2 추가]** S3 의미 | S3 제안 기각 | 사전에서 해당 단어 제거 + 재제안 0(D-3) |
| **[v2 추가]** 경로 대칭 | 단일(`query_generator`) · 멀티(`multi_db_executor`) · 3단 task 서브그래프 · 1·2단 서브에이전트 | 네 경로 모두 대기열 적재 건수 동일, 패널 표시 동일(D-5) |
| **[v2 추가]** G-11 (b) 준수 | 구조 정보 없는 DB의 `column`·`eav_name` 승인 | 전역 쓰기 0 · EAV 0 |
| **[v2 추가]** 감사 | 승인·기각·삭제 | 응답 `audit_logged` 존재 · `ADMIN_ACTION` 기록(C-2) |

**실측 우선**: 승인 후 "정확 히트로 승격"은 mock이 아니라 **실제 Redis + 실 질의 경로**로 확인한다
(Known Mistakes「결정적 게이트가 의존하는 데이터는 실 런타임 shape로 검증」). 실 DB 접속이 필요한
구간은 `RUN_E2E=1` 옵트인 뒤에 둔다.
**[v2 정정]** 실 LLM이 필요한 검증은 로컬 MLX로 한다(D-240). 두 평면이 `mlx` 루프백이면 승인 없이 `RUN_LOCAL_LLM=1`로 돌리고,
외부 차단 가드는 유지한다. `RUN_E2E=1`은 과금 평면을 쓸 때만 필요하며 건별 사용자 승인 사항이다(D-127). 로컬 PG
샌드박스(`polestar` · `testdata/pg/init`)와 로컬 Redis(`redis/`)로 재현할 수 있다.

---

## 8. 변경 파일

**신규**
- `src/utils/synonym_proposals.py` (캡처 순수 로직)
- `src/api/routes/synonyms.py` (제안·사전 API)
- `tests/test_utils/test_synonym_proposals.py`, `tests/test_schema_cache/test_synonym_proposals_store.py`,
  `tests/test_api/test_synonym_proposals_api.py`, `tests/test_api/test_synonym_panel_flow.py`

**변경**
- `src/schema_cache/redis_cache.py` (대기열 저장·승인·기각·sweep)
- `src/nodes/query_generator.py` (S1 배선 — `_instrument_synonym_usage` 확장)
- `src/nodes/schema_analyzer.py` (S2 배선 — 근사 히트 반환)
- `src/document/field_mapper.py` · `src/nodes/field_mapper.py` (S2 폼필 지점 · S3 배선)
- `src/api/routes/query.py` (응답 메타 `synonym_proposals` — **전 경로 대칭**)
- `src/api/server.py` (라우터 등록), `src/api/schemas.py` (응답 필드)
- `src/config.py` (`SynonymMatchConfig` 필드 5개), `.env.example`
- `src/static/admin/dashboard.html` · `src/static/js/admin.js` (유사어 관리 탭)
- `src/static/js/app.js` · `src/static/css/style.css` (인라인 패널)

**[v2 정정·추가]**
- 신규 `src/static/js/admin-synonyms.js` — 탭 로직은 `admin.js`가 아니라 여기에 둔다(C-1). `admin.js`는 수정하지 않거나 최소
  수정만 한다. `dashboard.html`에는 탭 버튼·패널·`<script>` 1줄을 추가한다.
- 변경 `src/nodes/multi_db_executor.py` — S1 대칭 캡처(D-5).
- 변경 `src/schema_cache/redis_cache.py` — D-1 헬퍼 `add_eav_name_synonym`·`add_resource_type_synonym`(+ EAV 철회용 단어 삭제). 필요하면
  `cache_manager.py`에 같은 이름의 위임 메서드를 둔다.
- 재사용(수정 없음) `src/api/admin_audit.py`(C-2) · `src/security/pii_filter.py` `scrub_pii`(D-9) · `src/api/dependencies.py`
  `require_admin_user`·`require_user`.
- 변경 가능성 `src/schema_cache/structure_store.py` — Q-5에서 "백업 병합"을 고를 때만(D-7).
- 노브를 둘 때만(Q-6) `src/api/settings_catalog.py` 메타와 `tests/test_api/test_settings_catalog.py` 전수 단언(350 → +N)을 갱신한다.
- `src/document/field_mapper.py` · `src/nodes/field_mapper.py`의 S2 폼필 지점은 `_apply_fuzzy_synonym_fallback`·`_apply_semantic_synonym_fallback`의
  확정 분기다(F-9). `_synonym_match`는 건드리지 않는다.
- `query.py`의 수정은 응답 조립 10지점에서 공용 헬퍼를 부르는 것뿐이다(F-14 · D-5).

---

## 9. 리스크

| 리스크 | 영향 | 완화 |
|---|---|---|
| 제안 폭주(질의마다 수십 건) | 관리자 피로 → 검토 포기 | 지문 기반 병합(hits 증가), 상한 500·패널 5건, 근거 점수 정렬, 기각 목록 |
| 오등록으로 사전 오염 | 검색 품질 저하가 자기강화 | 관리자 승인 게이트, 미확정 항목 승인 차단, 재오염 가드 재사용, 감사 추적 |
| 캡처 오버헤드 | 질의 지연 | 플래그 OFF 기본, 순수 dict 조립, Redis 쓰기는 턴당 1회 배치 |
| 민감정보 유출 | `evidence.query`에 사내 정보 | 감사와 동일 마스킹 + 200자 절단, 관리자 전용 노출 |
| 경로 비대칭(SSE만 누락) | 스트리밍 사용자에게 패널 미표시 | `query.py` 응답 조립 5지점 전부 grep으로 확인 후 배선 |
| **[v2 추가]** 경로 비대칭(멀티 DB·사다리 단) | 존 팬아웃 질의와 운영 1단에서 캡처·패널 0건 | 노드 직접 적재 + 라우트 조회형 패널(D-5) · `multi_db_executor` 대칭 캡처 · 네 경로 대칭 테스트(§7) |
| **[v2 추가]** 승인 반영 키 오류 | 승인한 제안이 대기열로 돌아온다 → 관리자 신뢰 상실 | type별 반영 대상표(P2 · D-1) + 재역조회 단언 |
| **[v2 추가]** S3 "승인해야 등록" 오인 | 기각해도 사전에 남는다 | `already_registered` 표기 · 기각 = 철회(D-3) · Q-4 |
| **[v2 추가]** DB별 승인분 소실 | 캐시 무효화 뒤 승인 효과가 사라진다 | 전역 키를 영구 정본으로 명시 · Q-5(백업 병합) |

---

## 10. 후속 (본 계획 밖, 연결점만 명시)

1. **E5-3 사용 메타 배선** — `increment_synonym_usage`/`prune_stale_synonyms` 호출부가 0건이다
   (구현체는 `redis_cache.py:877,955`, 호출은 테스트에서만). 배선되면 제안 랭킹과 사전 선별 주입에
   실사용 데이터를 쓸 수 있다. Plan 61 E5-3의 잔여 항목이다.
2. **매핑 보고서 업로드 경로 정리** — `POST /query/mapping-feedback`(`query.py:1786`)와 본 계획의
   승인 UI가 같은 목적(사람이 매핑을 고쳐 Redis 반영)을 서로 다른 UX로 제공하게 된다. 통합 여부는
   본 기능 안정화 후 D-161(경로 승격-폐기 동반 원칙, 폐기 전 4항 실측)에 따라 판단한다.
3. **승인 항목의 시드 승격** — 반복 승인되는 항목은 `config/synonym_seeds/*.yaml`로 내려
   git 아티팩트화하면 신규 DB 편입 시 재현된다(`docs/synonym_seed_migration_guide.md`).

> **[v2 정정]** ① 호출부 0건은 2026-09-22에도 그대로다. 위치는 `increment_synonym_usage :1050` · `prune_stale_synonyms :1128`로
> 옮겨졌다. 입력 변환 `collect_synonym_usage_events`(`synonym_usage.py:121`)도 호출부가 0건이다. 본 계획의 S1 캡처와 같은 지점
> (`_instrument_synonym_usage`)에서 나오는 값이라, 배선할 때 한 번에 붙일 수 있다. ② 매핑 보고서 라우트는 `query.py:2608`이다.
> 역할 의존성이 없어 비관리자도 사전을 바꿀 수 있다(C-5). ③ 시드 로드는 plans/104 O-7(관리자 등록 흐름)에 런타임 진입점이 생겼다
> (`src/schema_cache/db_registration_service.py:582` → `SynonymLoader.load_seed_yaml`). 승격된 시드는 그 경로로 재현된다.

**[v2 추가] 후속 연결점**
4. **plans/104 신규 코드값 → 제안 큐** — 104 §3.x가 "새 값은 신규 코드값으로 보이고, 유사어 등록은 plans/77 큐로 넘길 수 있다"고
   적었다. 받으면 소스 `new_code_value`(S4)가 된다. 수용 여부는 Q-7로 정한다.
5. **plans/106 G-6 — 하네스 가이드라인 후보와 큐 공유** — H4 후보(가이드라인 증류)를 같은 승인 큐로 받을지 정해야 한다. 유사어가
   아닌 항목이 섞이면 §3.2 스키마(`type`)를 넓혀야 하므로, 기본안은 **공유하지 않는 것**이다(Q-7).
6. **전역 사전 출처 메타** — `synonyms:global`에 출처 태그를 붙이는 저장 형식 확장(하위호환 dict). D-6의 배지 공백과 E5-3 감쇠의
   전역 적용이 이 확장을 전제로 한다. D-019·D-051(구조 불변) 재판단 대상이다.
7. **비관리자 사전 쓰기 경로 정리** — 자연어 등록 · 채팅 유사어 추가·삭제 · 매핑 보고서 업로드(D-10). 요건 1.2-3을 사전 전체로
   넓힐지는 본 계획을 안정화한 뒤 D-161(경로 승격-폐기 동반 · 폐기 전 4항 실측)에 따라 판단한다.

---

## 11. 미결 사항 (착수 전 확인)

1. **패널 노출 대상** — 모든 사용자에게 제안 패널을 보일지, 특정 role에만 보일지. 기본안: 모든
   로그인 사용자(투명성 원칙 4). 반대 의견이 있으면 관리자만으로 좁힌다.
2. **제안 만료 30일** — 운영 감각에 맞는지. 짧으면 검토 전에 사라지고, 길면 대기열이 노후한다.
3. **`user_flagged` 우선순위** — 사용자가 등록 요청한 항목을 관리자 탭 상단에 고정할지, 근거 점수
   정렬에 가중치만 줄지. 기본안: 상단 고정 + 배지.

**[v2 추가] 착수 전 확인 4건** — 1~3은 여전히 미응답이며 기본안은 그대로다.

4. **Q-4 · S3(폼필 LLM 매핑)의 의미**(D-3) — S3 후보는 제안 시점에 이미 사전에 자동 등록돼 있다.
   - (a) **사후 확인으로 수용**(기본안) — 자동 등록은 §1.3 결정대로 유지한다. 대기열에서 승인은 `operator` 승격, 기각은 철회다.
     요건 1.2-2 "선택한 것만"이 S3에서는 "철회하지 않은 것만"이 된다.
   - (b) 자동 등록을 대기열 뒤로 옮긴다 — 요건 1.2-2가 S3에도 문자 그대로 성립한다. 대신 §1.3에 기록된 사용자 선택(현행 유지)을
     바꾸는 것이고, 승인 전까지 폼필 매핑 학습이 멈춘다(D-227 G-11이 (a) 차단안을 "학습 소실"로 기각한 것과 같은 비용이다).
   - 부속 질문: 2.8 LLM 유사어 발견(`llm_synonym`)도 S3에 넣는가? 기본안은 넣는 것이다. 지금은 pending에도 오르지 않아
     사람이 한 번도 보지 않는다.
5. **Q-5 · DB별 승인분의 영구성**(D-7) — (a) **승인 시 plans/104 설명 백업의 `synonyms`에도 병합 기록**(기본안 — 무효화 뒤 복원에
   포함된다) · (b) 전역 키만 영구로 보고, DB별 반영은 무효화 시 소실을 감수(문서화만) · (c) 승인 이력(대기열 `approved` 레코드)으로
   재적용하는 복원 경로를 신설.
6. **Q-6 · 설정 노브 수**(D-8) — (a) **`SYNONYM_PROPOSALS` 1개 + 모듈 상수 4개**(기본안 · plans/109 방향) · (b) v1대로 5개(C등급 표기 ·
   카탈로그 단언 350→355).
7. **Q-7 · 범위 경계 3건**(C-10 · D-10) — ① plans/104 신규 코드값을 S4 소스로 받는가(기본안: **1차 범위 밖**, 스키마만 열어 둠)
   ② plans/106 H4 후보와 큐를 공유하는가(기본안: **공유 안 함**) ③ 비관리자 사전 쓰기 경로 3종을 그대로 두는가(기본안: **그대로 두고
   §10-7 후속**으로 넘김).

**[v2.1 추가]**

8. **Q-8 · 범위 재편**(§0.7) — **Q-1~Q-7보다 먼저 정한다.** 이 답에 따라 나머지 질문의 범위가 달라진다.
   - (a) v2 범위 그대로 — 캡처 중심으로 가고, 자동 등록은 현행 유지한다(§0.6 순서).
   - (b) **사전 위생 우선으로 재편**(권고 · §0.7.4) — 1차는 사전 조회·삭제, 기각 목록, 자동 등록 승인제다. 이어 A/B 게이트를
     거치고, 2차에서 캡처·패널을 한다. (b)를 고르면 Q-4는 (b)로 정해지고, Q-1·Q-3(패널 노출·`user_flagged`)은 2차로 미뤄진다.
   - (c) 보류 — plans/114 트랙 P 뒤로 미루고 지금은 착수하지 않는다.
   - 어느 쪽이든 목적이 성능이라면 plans/114 트랙 P(P-3·P-4)와 산문·알람 의도 교정이 먼저다(§0.7.4).

> **착수 판정**: Q-1~Q-7이 모두 기본안으로 확정되면 §0.6 순서로 바로 착수할 수 있다. 외부 의존(운영 접속·과금 API)은 없다.
> 실 LLM 검증은 로컬 MLX로 한다(D-240). 착수할 때 D-163을 본문에 등재하고 파일명을 `-TODO` → `-WIP`로 바꾼다.
> **[v2.1]** Q-8이 먼저다. (a)면 위 판정이 그대로 적용된다. (b)면 §0.7.4의 1차만 착수하며, 1차에 필요한 확인은
> Q-2(만료)·Q-4(=(b))·Q-5·Q-6·Q-7 ③이다. 2차는 A/B 게이트 결과를 본 뒤 다시 판단한다.
