# 77. 유사어 제안 대기열 + 승인 웹 UI — 자동 캡처 · 선택적 영구 저장 (Synonym Proposal Queue & Approval UI)

> 작성일: 2026-08-25
> **상태**: **계획 (미구현)** — 사용자 요건 확정 3건 반영(§1.2). 착수 시 D-163 등재.
> **대상 기능**: 유사어 후보 자동 캡처(`src/nodes/query_generator.py`·`schema_analyzer.py`·`src/document/field_mapper.py`),
> 제안 대기열 저장(`src/schema_cache/redis_cache.py`), 승인 API(`src/api/routes/`),
> 승인 UI(`src/static/admin/dashboard.html`·`src/static/js/admin.js`·`src/static/js/app.js`)
> **선행/근거 문서**: `plans/61-text2sql-candidate-selection.md` 트랙 B(E5-1~E5-3),
> `docs/synonym_management_analysis.md`, `SPEC-ops-logging-and-synonym-set.md` §7(모듈 C)
> **관련 결정**: **D-011**(유사단어 2계층·글로벌 사전 TTL 없는 영구저장), **D-012**(매핑-우선 +
> LLM 추론 매핑은 **사용자 승인 시** 등록), **D-013**(멀티턴 HITL — 유사어 등록 승인),
> **D-019·D-051**(Redis 캐시 구조 불변·스케일 가드), **D-035**(결정적 규칙=판단, LLM=보조),
> **D-075**(동의어 매칭 고도화), **D-142**(앵커 없는 동의어 집합 — **모호하면 등록 0건** 원칙),
> **D-151**(멀티턴 HITL 폼필 — 구조화 답변 위젯 패턴, 본 계획 UI의 직접 선례),
> **D-027·D-034**(감사 로깅)
> **신규 결정(착수 시 등재)**: **D-163**(유사어 제안 대기열 — 자동 캡처 + 관리자 승인 게이트 + 기각 목록)

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
2. **모호한 짝은 자동 확정하지 않는다** — 미등록 리터럴은 "어떤 질의어와 짝인지"가 결정적으로
   확정되지 않는 경우가 있다. 이때 임의 짝짓기 대신 **증거(질의 원문·SQL 스니펫·매칭 점수)를
   첨부해 사람에게 넘긴다**. D-142가 앵커 모호 시 등록 0건으로 결론낸 것과 같은 이유다 —
   오등록은 조용히 검색 품질을 갉아먹고 자기강화된다.
3. **침묵 수집 금지** — 이번 턴에 무엇이 캡처됐는지 채팅 패널로 사용자에게 보여준다. 사용자는
   그 자리에서 잘못된 제안을 기각하거나 표기를 교정할 수 있다.
4. **무회귀 옵트인** — 플래그 `SYNONYM_PROPOSALS=false` 기본. OFF면 캡처·저장·UI 전부 미동작이며
   기존 경로(자연어 승인 포함)는 무변경이다.

### 2.1 기존 결정과의 관계 — Redis 키 신설이 D-142와 충돌하지 않는 근거

D-142는 대안 "대칭 집합 저장소 신설(`synonym:sets`)"을 **기각**했다. 기각 사유는 두 가지였다:
(a) `schema_analyzer`·`field_mapper`·`query_generator` **매칭 경로 전반에 대칭 주입이 필요**,
(b) Redis 키 스키마 신설이 D-019·D-051 캐시 구조 불변과 충돌.

본 계획의 `synonym:proposals`는 (a)에 해당하지 않는다 — **매칭 계단이 이 키를 읽지 않는다**.
승인 전까지 어떤 매칭에도 영향이 없고, 승인 후에는 기존 사전 키(`synonyms:global` 등)로만
반영되므로 **캐시 구조는 그대로다**. (b)에 대해서는 Plan 61 E5-2가 이미 같은 판단으로 별도 키
`schema:{db}:column_value_index`를 신설한 선례가 있다(`redis_cache.py:1710-1750` — "의미가 달라
그 키를 덮어쓰지 않고 전용 키를 신설한다, 회귀 0").

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

**규칙**
- 캡처는 **실패해도 본 흐름에 영향이 없어야 한다** — S1이 이미 그렇듯(`query_generator.py:800`
  "실패해도 SQL 생성에는 영향이 없다") 각 소스를 **개별 try/except**로 감싼다(한 try에 묶지 않는다).
- 캡처 자체는 순수 함수라 **매 질의 오버헤드가 dict 조립 수준**이며, 플래그 OFF면 호출 자체를 건너뛴다.
- **재제안 차단**: 적재 직전 `rejected` 해시를 조회해 지문이 있으면 버린다.

**검증**: `tests/test_utils/test_synonym_proposals.py` — 지문 결정성, 자동 짝짓기가 후보 2개 이상일 때
null을 남기는지, 마스킹·절단, S1/S2/S3 각 shape.

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
- **부분 성공을 침묵하지 않는다** — 5건 중 2건 실패면 성공/실패 목록을 모두 반환하고 UI가 표시한다.
- **쓰기 직전 결정적 검증**(D-142 ⑤와 동일 규약): 길이 1~64자, 허용 문자, 중복 제거,
  기존 등록과 충돌 시 침묵 병합 금지(충돌 사실을 응답에 노출).
- **재오염 차단 가드 재사용**: 서버명/서버이름류 → hostname 등록 거부 판정
  (`src/utils/query_gen_common.py:735` "자동 유사어 등록 차단(재오염 방지)")을 승인 경로에도 적용한다.
  관리자가 명시 승인해도 이 가드에 걸리면 **경고를 띄우고 확인을 한 번 더 받는다**.

**검증**: `tests/test_schema_cache/test_synonym_proposals_store.py` — upsert 멱등·hits 증가,
승인 시 실제 사전 키 반영, 기각 후 재적재 차단, sweep 경계(strictly-older), Redis 미가용 시 graceful.

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

**UI**: `dashboard.html`에 탭 `data-tab="synonyms"` 추가 + `admin.js`에 로더 —
기존 `users`/`auditlogs`/`incidents` 탭과 **동형 패턴**(`admin.js:1163,1387,1561`)으로 작성한다.

화면 구성:
- 상단 **제안 대기열** 표 — 체크박스 · 소스 배지 · `key ← word` (미확정이면 입력칸) · 근거(질의 원문
  툴팁) · hits · 신뢰도 · 최종 등장. 하단에 [선택 승인] [선택 기각(사유)] 버튼.
- 하단 **등록된 사전** 표 — 글로벌/DB별 토글, 검색, 출처(operator/seed/llm) 배지, 개별 삭제.

**검증**: `tests/test_api/test_synonym_proposals_api.py` — 비관리자 401/403, 승인 후 사전 반영,
교정값 우선, 미확정 항목 승인 시 400, 감사 로그 기록.

### P4 — 채팅 인라인 패널

**응답 메타 확장**: `query.py`의 응답 조립부(`:827`, `:1108`, `:1284`, SSE `:1036`·`:1059` — **전 경로 대칭**,
Known Mistakes「단일/멀티 경로 대칭」)에 `synonym_proposals` 추가 — 이번 턴 신규 캡처 요약(최대 5건).

**패널**: `app.js`에 `renderSynonymProposalPanel()` — **폼필 HITL 패널(D-151, `app.js:1285-1340`)과
동형**으로 작성한다. 그 주석이 명시하듯 "자연어 재조합·LLM 파싱 없음"이 핵심이다.

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

**검증**: `tests/test_api/test_synonym_panel_flow.py` + 패널 렌더 스모크.
비관리자가 approve를 직접 호출하면 403인지 반드시 단언한다.

### P5 — 감사·정리 배선

- 승인/기각/삭제 전부 **감사 로그**(`request.app.state.audit_service`, `admin.py:381-418` 패턴).
  기록 필드: actor, action, 제안 id, key/word, db_id, 사유. 감사 저장소 없으면 응답에 그 사실을 명시
  (admin.py:726 선례 — 침묵 금지).
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

---

## 6. 회귀 경계

- 플래그 OFF가 **기본**이며, OFF 경로에서 `extract_synonym_usage` 이후 코드 경로는 현행과 바이트 동일해야 한다.
- 캡처 예외는 삼키되 **로그로 가시화**한다(침묵 폴백 금지 — 다만 SQL 생성·응답에는 영향 없음).
- Redis 미가용 시 캡처는 no-op, 채팅 패널은 미표시. 질의는 정상 완료된다.
- 매칭 계단은 대기열을 **읽지 않는다** — 승인 전 제안이 검색 품질에 영향을 주면 승인 게이트가 무의미해진다.
  이 불변식을 테스트로 고정한다(대기열에 항목이 있어도 매칭 결과가 동일).
- `python scripts/arch_check.py --ci` 통과: utils(순수) → schema_cache(infrastructure) →
  nodes(application) → api(interface) → static(entry) 방향만 사용.

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

**실측 우선**: 승인 후 "정확 히트로 승격"은 mock이 아니라 **실제 Redis + 실 질의 경로**로 확인한다
(Known Mistakes「결정적 게이트가 의존하는 데이터는 실 런타임 shape로 검증」). 실 DB 접속이 필요한
구간은 `RUN_E2E=1` 옵트인 뒤에 둔다.

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

---

## 9. 리스크

| 리스크 | 영향 | 완화 |
|---|---|---|
| 제안 폭주(질의마다 수십 건) | 관리자 피로 → 검토 포기 | 지문 기반 병합(hits 증가), 상한 500·패널 5건, 근거 점수 정렬, 기각 목록 |
| 오등록으로 사전 오염 | 검색 품질 저하가 자기강화 | 관리자 승인 게이트, 미확정 항목 승인 차단, 재오염 가드 재사용, 감사 추적 |
| 캡처 오버헤드 | 질의 지연 | 플래그 OFF 기본, 순수 dict 조립, Redis 쓰기는 턴당 1회 배치 |
| 민감정보 유출 | `evidence.query`에 사내 정보 | 감사와 동일 마스킹 + 200자 절단, 관리자 전용 노출 |
| 경로 비대칭(SSE만 누락) | 스트리밍 사용자에게 패널 미표시 | `query.py` 응답 조립 5지점 전부 grep으로 확인 후 배선 |

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

---

## 11. 미결 사항 (착수 전 확인)

1. **패널 노출 대상** — 모든 사용자에게 제안 패널을 보일지, 특정 role에만 보일지. 기본안: 모든
   로그인 사용자(투명성 원칙 4). 반대 의견이 있으면 관리자만으로 좁힌다.
2. **제안 만료 30일** — 운영 감각에 맞는지. 짧으면 검토 전에 사라지고, 길면 대기열이 노후한다.
3. **`user_flagged` 우선순위** — 사용자가 등록 요청한 항목을 관리자 탭 상단에 고정할지, 근거 점수
   정렬에 가중치만 줄지. 기본안: 상단 고정 + 배지.
