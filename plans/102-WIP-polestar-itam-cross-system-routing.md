# 102. 폴스타 ↔ 자산관리(ITAM) 교차 시스템 질의 — 답변 영역 라우팅 · 값 기반 키 브리지 · 식별자 소재 프로브

> **작성일**: 2026-09-17
> **성격**: 구현 계획 + 구현 현황(§4.1) · **상태: 부분 구현(v6 · 2026-09-21 처분 반영) — X-1~X-9·L-1~L-4·X-12 완료(플래그 3종 기본 off) · 멀티 DB 노트 덮어쓰기 수정 · D-224(부분 확정)·D-225(확정) 등재 · **§4.1.5 사용자 결정 11건 처분 완료**(코드 7 · 기록만 4 — D-224 부기) · 전체 스위트 실행 완료 · 차단 X-10·X-11·X-13·X-14·L-5(`plans/103` P2-1 · D-127 승인 선행) · G-4·G-7 사용자 확정 · G-1~G-3·G-5·G-6·G-9는 기본 가정 적용·사용자 미확정(§7 · G-8→`plans/104` · G-10→`plans/103` v3 해소)** · 파일명 `-WIP` 유지(차단 5건 잔여)
> **⚠ 기준 개정(2026-09-23 · D-251)**: 기준 운영 단이 **2단 `intent_orchestration`**으로 바뀌었다(D-225 ① 전면 개정). **L-5(운영 `.env` 3단 전환)는 목표가 바뀐다** — 운영 전환 대상은 3단이 아니라 **1단 off(`ENABLE_DEEPAGENTS_PACKAGE=false`) + 2단 확정**이고 절차는 `plans/110` 부록 B.4다(사용자 반영). 3단은 2단 대비 비교 arm이다. 아래 L-5 행의 "3단 전환"·"`tier=semantic_router` 확인"은 D-225 시점 기록이다.
> **v2**(2026-09-17): **기준 실행 경로를 사다리 3단 `semantic_router`로 재정렬** — 사용자 지시 원문 *"기본은 시멘틱 라우터를 사용한다. 모든 동작은 시멘틱 라우터에서
> 동작되어야 한다. deepagents는 부가적으로 사용할 예정이라. 기본 동작은 시멘틱 라우팅을 통해 진행되어야 한다. 이 기준에 맞게 계획을 업데이트하라."*
> → 3단 실행 경로 실측(§1.6) · 함정 X-T8~X-T13 · 3단 배선 재설계(§3.5) · 기준 전환 트랙 L(§3.7) · 게이트 G-8~G-10 · **D-225 예약**(§9.2)
> **v3**(2026-09-17): 사용자 지시 *"3단 기능도 langgraph 기능을 이용하면 1단의 모든 기능을 구현할 수 있다. 검토하여 모두 구현하는 방향으로 계획을 작성하라. 구조 승인 기능은 admin 페이지에 mcp로 연결된 db리스트를 보여주고 각 db별 스키마 업데이트나 신규 내용을 조회하여 구조 분석을 통해 향후 사용할 수 있도록 정리하는 기능을 추가하라."*
> → **G-10 확정 = `plans/103`**(LangGraph 네이티브로 3단에 1·2단 전 기능 · D-226) · **G-8 해소 = `plans/104`**(구조 승인을 질의 경로에서 관리자 페이지로 · D-227) · X-13 진입을 103 `plan` 노드로 이관 · L-5 선행을 103 완료로
> **v4**(2026-09-17): 사용자 지시 *"102번 계획을 구현하라."*(팀 리드 경유) → 1차 구현(4트랙 병렬 · 기본 가정 적용) · G-4 사용자 확정 *"자산관리는 존 개념이 없이 1개의 시스템이다. 이에 맞게 정의하라."* · 사용자 중지 지시 *"현재 작업 중인 내용을 계획파일에 업데이트하고 우선 현재까지 마무리하고 작업을 중지하라."* → **구현 현황·재개 체크리스트 §4.1**
> **요청 취지(사용자 지시 원문, 2026-09-17)**: *"시멘틱 라우팅 측면에서 자산관리에서 조회할 것과 폴스타에서 조회할 것을 구분해야 한다.
> 사용 사례는 다양하게 있다. ① 자산관리 시스템에서 조회하는것은 하드웨어, 소프트웨어의 자산 정보, 계약정보, 담당자 정보등을 관리한
> 것들을 조회한다. ② 폴스타는 시스템의 모니터링 위주이기 때문에 서버 현황 정보등을 조회한다. ③ 폴스타의 정보의 주요 키를 이용하여
> 자산관리에서 정보를 조회하는 형식으로 동작할 수 있다. ④ 자산 정보의 주요키를 기준으로 폴스타의 정보를 조회할 수 있다. ⑤ 주요키의
> 경우 호스트 네임이나 ip 등의 데이터 내용을 보고 판단해야 한다. 사용자의 프롬프트 요구사항에 따라 자산관리 시스템을 조회할지?
> 폴스타를 조회할 지 판단해야 하며, 때에 따라서는 두가지를 모두 조회하여 적절한 시스템을 선택하여 후속 정보를 조회해야 한다.
> 위의 요구사항에 따라 시멘틱 라우팅이나 하네스 엔지니어링 쪽 코드의 수정 계획을 추가로 정리해야 한다. 위 요건에 맞게 추가 계획을
> 관련 문헌을 조사하여 정리하라. 자산관리 시스템에 맞는 mcp_server 정보를 설정하여 연결하라."*
> **상위/선행 계획**: **`plans/95`**(자산관리 DB 연동 — 이 계획은 95의 트랙 C(라우팅 경계)·E(교차 질의)를 승계·구체화한다, §8) ·
> **`plans/88`**(복합 질의 순차 의존 — `input_from`·선행 게이트·사후 대조, D-203) · **`plans/82`**(솔루션·존 그룹 실행 축, D-176 ·
> 존 순회 소재 탐색 Wave 5) · `plans/79`·`plans/80`(시멘틱 라우팅 개선·라우팅 골든셋) · `plans/94`(시나리오 하네스)
> **관련 결정**: D-004(LLM 전용 시멘틱 라우팅 — 키워드 사전 분류 금지) · D-035(결정적=판단·LLM=서술) · D-053(사본 금지) ·
> D-061(폴스타 `name`≠`hostname`) · D-086·D-095(선행 결과 스코프 주입) · D-089(DB별 특화 격리) · D-100(서버 식별 컬럼 엄격 판정) ·
> D-127(과금 API 건별 승인) · D-176(솔루션 축) · D-179(독스트링 리터럴도 overfit 게이트 대상) · D-203(순차 의존 계약) · **D-214**(자산 DB 편입) ·
> v2: D-037(트랙 A/B) · D-161(승격-폐기 동반 · 폐기 전 4항 실측) · D-162(사다리 관측·`docs/21` 단일 출처) · D-221 ⑤(정본 단 아님 경고)
> **신규 결정 예약**: **D-224**(§9.1) · **D-225**(§9.2 — v2). `docs/02_decision.md` 「채번 이력」 표에 등재(2026-09-17).
> ※ v2 채번 재확인 2026-09-17 — `## D-` 헤더 최댓값 **222** · 「채번 이력」 표 최댓값 **D-224**(이 계획) · 계획서 전수 grep에 D-225 사용 0 → **D-225**.
> ※ 채번 실측 2026-09-17 — `## D-` 헤더 최댓값 **222** · 「채번 이력」 표 **D-223 = `plans/101`(ML 장애 진단·예측, 병행 세션) 예약** 대조 → **D-224**.
> ※ **번호 재부여**: 최초 `plans/101` · D-223으로 작성했으나 병행 세션이 7분 먼저 `plans/101`(ML 장애 진단)과 D-223을 예약해 뒤 번호로 옮겼다(2026-09-17).
> **실측 기준**: `file:line`은 2026-09-17 작업 트리(`multiintent`, HEAD `c64ef98` + 미커밋 `plans/95` v5 구현분)에서 직접 확인했다. v2의 3단 경로 실측(§1.6)도 같은 날 같은 트리다.

---

## 0. 요약

### 0.1 요구사항을 검증 가능한 문장으로 바꾸면

| # | 사용자 요구 | 검증 가능한 문장 |
|---|---|---|
| **R1** | 자산관리 = HW/SW 자산·계약·담당자 | 자산 축 질의는 **자산관리만** 조회한다 |
| **R2** | 폴스타 = 모니터링·서버 현황 | 모니터링 축 질의는 **자산관리를 조회하지 않는다**(자산 DB에도 사용률 컬럼이 있어 오선택이 조용하다 — 95 함정 T5) |
| **R3** | 폴스타 키 → 자산관리 조회 | 폴스타 결과의 서버 키로 자산관리 조회가 **대상이 한정된 채** 이어진다 |
| **R4** | 자산 키 → 폴스타 조회 | 자산관리 결과의 서버 키로 폴스타 조회가 이어진다(R3의 역방향 — **같은 계약**) |
| **R5** | 키는 데이터 내용으로 판단 | 키 종류(호스트명·FQDN·IPv4·IPv6)를 **컬럼명이 아니라 값**으로 판정한다 |
| **R6** | 둘 다 조회해 적절한 시스템 선택 후 후속 조회 | 식별자 기준 질의에서 **어느 시스템이 그 식별자를 아는지 먼저 확인**하고, 결과로 시스템을 고른 뒤 후속 조회한다. "없다"와 "확인하지 못했다"를 구분한다 |

### 0.2 현행 판정 — 요구 6개 중 온전히 되는 것은 0개

| # | 현행 | 근거(§1) |
|---|---|---|
| R1·R2 | △ **설명문으로만** 구분 — LLM이 레지스트리 `description`을 읽고 고른다. 선택을 검증하는 코드가 없고, 분류 결과가 비거나 LLM이 실패하면 **첫 활성 DB로 침묵 폴백**한다. v2 기준 경로(3단)에서는 라우터 노드가 한 번, 순차 러너로 가면 서브질의마다 **한 번 더** 분류한다(X-T10) | §1.1 · §1.6 |
| R3 | △ 배관은 있다(`input_from`→스코프 블록). 그러나 대상 DB 컬럼 대응을 **LLM이 추측**(*"또는 동등한 식별 컬럼"*)하고 정규화가 없다. **3단에서는 이 배관(순차 러너)에 운영 설정으로 진입하지 못한다**(구조 승인 HITL 기본 on — X-T8 · 원문 순차 표지 의존 — X-T9) | §1.2 · §1.6 |
| R4 | ❌ **막힌다** — 자산 DB의 `sevrHostName`·`iPCtnt`가 서버 식별 컬럼으로 **인정되지 않아** 후속 단계가 `prior_no_identity`로 차단된다(실행 확인). 3단에서는 X-T8·X-T9가 그 앞에서 먼저 막는다 | §1.2 · §1.6 |
| R5 | ❌ 식별 키 판정이 **컬럼명 목록** 기반이다. IP는 스코프 키 후보에 **아예 없다** | §1.2 |
| R6 | ❌ 소재 탐색은 **폴스타 존 축·호스트명 완전 일치만** 있다(`host_discovery`). 시스템 축·IP는 없다. 호출처는 1·2단 `process_query` 서브에이전트뿐이라 **3단에는 도달 경로가 없다** | §1.3 · §1.6 |

### 0.3 한 줄 권고

**시스템 선택은 "LLM이 필요한 답변 영역을 말하고, 코드가 그 영역의 정본 시스템을 정한다"로 바꾸고, 시스템 사이의 연결은
"값으로 키 종류를 판정 → DB별 매니페스트로 대상 컬럼을 정함 → 매칭 등급을 사용자에게 보인다"는 하나의 계약으로 양방향에 쓴다.**
새 엔진을 만들지 않는다 — 순차 의존 배관(D-203)과 존 순회 판정(`host_discovery`)이 이미 있고, 빠진 것은 **키 판정·키 대응·시스템 축**이다.
**(v2) 계약은 사다리 3단 `semantic_router`에서 소유하고 판정한다.** 3단은 순차 러너로 2단 부품을 이미 함수로 쓰므로 함수는 그대로 쓰고,
빠진 **3단 진입 조건(LLM 구조화 출력 `chain`)·HITL 처분·소재 프로브 노드**를 더한다. 1단 `deep_agent`는 같은 함수를 부르는 부가 경로로 뒤따른다.

### 0.4 실행 경로 기준 (v2)

| 단 | v1 | **v2** |
|---|---|---|
| **3 `semantic_router`** | 1차 비범위 | **기준 경로** — R1~R6 계약을 여기서 소유·판정한다 |
| 1 `deep_agent` | 운영 기준(`.env` 실측) | **부가 경로** — 같은 판정 함수를 부르는 동등성 작업(X-14). 폐기 대상 아님(D-225 ②) |
| 2 `intent_orchestration` | 1단과 같은 함수 | **배선 기본 off**(트랙 L). 부품(`_llm_decompose`·`agent_orchestrator`·`subagents`)은 3단 순차 러너가 재사용하므로 **함수 수정은 3단에 그대로 반영**된다 |
| 4 `legacy` | 비범위 | 비범위 |

**운영은 아직 1단이다** — `.env:205` `ENABLE_DEEPAGENTS_PACKAGE=true` · `:198` `ENABLE_INTENT_ORCHESTRATION=true` · `:178` `ENABLE_SEMANTIC_ROUTING=true`.
기준 전환 자체가 선행 작업(트랙 L · §3.7)이고, **3단에 도달 경로가 없는 기능**(실시간 프로세스 조회 등 — X-T12)을 둔 채 운영을 먼저 바꾸면 그 기능이 퇴행한다(G-10).
**(v3)** 그 기능들은 **`plans/103`에서 3단에 모두 구현**하고, 3단 복합 실행을 막던 구조 승인 HITL은 **`plans/104`가 질의 경로에서 제거**한다. 운영 전환(L-5)은 103 완료 뒤다.

---

## 1. 현황 실측

### 1.1 시스템 선택 — LLM 분류 1회, 검증 없음, 빈 결과는 침묵 폴백

| 지점 | `file:line` | 현황 |
|---|---|---|
| 질의 DB 분류(v2 기준 · 3단) | `src/routing/semantic_router.py:244` → `:519` `_llm_classify` | 3단 `semantic_router` 노드가 **질의 전체**를 1회 분류해 `target_databases`·`is_multi_db`를 낸다 |
| 서브질의별 DB 분류 | `src/orchestration/subagents.py:114` `classify_dbs` → 같은 `_llm_classify` | 1단(deep_agent)·2단(intent), 그리고 **3단 순차 러너**(2단 부품 재사용 · §1.6)가 서브질의마다 LLM이 활성 DB 목록과 `description`을 보고 `relevance_score`로 **다시** 고른다 |
| 빈 분류 폴백 | 3단 `semantic_router.py:250`(LLM 실패) · `:338`(결과 없음) · 1·2단·순차 러너 `subagents.py:174` *"classify_dbs 결과 없음, 첫 활성 DB 사용"* | `ACTIVE_DB_IDS`에 itam이 들어오면 **자산 질의가 분류 실패 시 폴스타로 조용히 간다** |
| 프롬프트 DB 목록 | `semantic_router.py:901-928` `_render_db_list` | 레지스트리 `description` + 캐시 **「상세」를 덧붙인다** |
| 캐시 설명 생성 | `src/api/routes/schema_cache.py:534` · `scripts/schema_cache_cli.py:222` `generate_db_description` | 스키마에서 LLM이 설명을 만든다 — 자산 DB 스키마에는 CPU·메모리 사용률 컬럼이 있어, **생성된 「상세」가 95 W-8에서 뺀 사용률 어휘를 되살린다**(함정 X-T4) |
| 능력(capability) 축 | `config/db_registry.yaml:43-76` `solutions[].capabilities/requires` · `src/routing/registry.py:193` `capability_providers()` | **선언은 있으나 운영 소비처 0건**(호출은 `tests/test_orchestration/test_execution_groups.py:40`뿐). `apm·dpm`의 `requires: [host_location]` — *"폴스타에서 호스트를 먼저 찾고 APM을 본다"* — 는 R3·R4와 **같은 모양**인데 배선돼 있지 않다 |

→ **R1·R2의 실체는 "설명문이 좋기를 바란다"이다.** 선택이 틀려도 결과가 나오므로(두 DB 모두 사용률 컬럼 보유) 틀린 것을 알 길이 없다.

**빈 분류 침묵 폴백의 실측 빈도 (전달받은 값 — 우리가 잰 것이 아니다)**

| 항목 | 값 |
|---|---|
| 발동 | **21/380턴 = 5.5%**(`subagents.py:174` *"classify_dbs 결과 없음, 첫 활성 DB 사용: polestar_b0"*) |
| 원인 분해 | **"분류 결과 없음" 18건** vs **"LLM 호출 자체 실패" 3건**(FabriX `API returned error status: ERROR`) — 뒤의 3건은 분류 품질이 아니라 **가용성** 문제다. 분류에 LLM 1회를 거는 설계의 리스크 |
| 발동 조건 | 전건 **단일 시스템 질의** · 교차 시스템 0건 · 자산관리 언급 0건. 공통점은 **위치 표면어(김포·여의도·은행존)가 없는 질의** — 같은 로그에 존 역질문 후단 게이트(D-143 후속2) 140회가 함께 찍혀 같은 뿌리로 보인다 |
| 선택의 임의성 | `polestar_b0`는 `ACTIVE_DB_IDS` 첫 항목일 뿐 질의 내용과 무관하다 |
| 사용자 노출 | **아무 표시도 없이** 21회 발생 → X-T3 폴백 표기(`NOTE_ROUTING_FALLBACK`)의 필요 근거 |

- **출처**: run `20260918-182507` (`logs/server-baseline.log`·`raw.jsonl`) · 폐쇄망 FabriX · 제공 세션 `collectorinfra-36`(2026-09-21 전달).
- **한계 4종 — 이 수치로 더 말하지 않는다**: ①**우리가 직접 잰 값이 아니다**(전달받은 실측) ②측정 커밋 `5b093016`은 **이 저장소에 없는 dirty 트리**다 ③**사다리 2단 `intent_orchestration`에서 측정**됐다 — 우리 기준 경로(3단)가 아니다 ④**오답 여부는 측정되지 않았다**: 하네스가 존 역질문에 자동 응답해(380턴 중 221턴 전부 `polestar_cm_gp` 선택) 폴백이 걸린 14턴 중 12턴이 덮였고, 남은 2턴(R2-07)은 "알람 임계치를 바꿔줘"라는 오용 시나리오라 rows=0이 오답인지 단정할 수 없다.
- **측정 공백**: 침묵 폴백이 실제로 결과를 오염시키는지 재려면 **존 자동응답을 끈 arm**이 필요하다(`plans/99` 실험 설계 소관). 그 결과가 나오기 전까지 `ROUTER_CAPABILITY_OWNERSHIP_ENABLED` 기본값은 **off 유지**다.

### 1.2 순차 의존 배관과 식별 키 판정 — 컬럼명 목록 기반

배관은 두 경로에 있다(D-203): 1단 `src/orchestration/deepagents_tools.py:195` `_dependency_scope`(값 일치 G1 · 참조어 G2 · 순위어 G3) ·
2단 `input_from` → `src/utils/prior_dependency.py:176` `assess_prior_dependency` → `src/utils/query_gen_common.py:1314`
`build_prior_rows_block`(`{col} IN (...)` 스코프 블록).
**(v2) 3단은 별도 배관이 없다** — `sequential_runner`가 2단 부품(`_llm_decompose`·`agent_orchestrator`·`result_aggregator`)을 함수로 불러
2단과 **같은 게이트·같은 스코프 블록**을 탄다(`src/orchestration/sequential_runner.py:26-28` · §1.6).

그런데 **어느 컬럼이 서버 키인가**를 전부 한 함수가 컬럼 **이름**으로 정한다.

| 함수 | `file:line` | 규칙 |
|---|---|---|
| `is_server_identity_col` | `query_gen_common.py:1200` | 정확 일치 `{server_name, hostname, host_name, os_hostname, name, id, server_id}` + `server/host/os` 접두 `*_name/_id` |
| `collect_prior_identity_values` | `query_gen_common.py:1222` | hostname류 우선, 없으면 name류. **IP 분기 없음** |
| `_extract_identity_rows` | `subagents.py:620` | 식별 컬럼이 없으면 **전 컬럼을 그대로** 넘긴다 |

**실행 확인(2026-09-17, 루트 venv)**:

```
hostname -> True      ipaddress -> False     sevrHostName -> False
iPCtnt -> False       SEVR_HOST_NAME -> False  (G-4가 스네이크 대문자로 확정돼도 인정되지 않는다)
itam 선행 행  {"sevrHostName": "svr-web-01", "iPCtnt": "10.0.1.1"} -> ('', [])      ← prior_no_identity
polestar 선행 행 {"hostname": "svr-web-01", "ipaddress": "10.0.1.1"} -> ('hostname', ['svr-web-01'])
```

- **R4(자산 → 폴스타)는 지금 구조적으로 막힌다** — 선행 결과에 식별 컬럼이 "없다"고 판정되어 `prior_dependency.py:221` 게이트가 후속을 건너뛴다.
- **R3(폴스타 → 자산)는 반쯤 된다** — 값 목록은 `hostname`으로 모이지만 스코프 블록이 *"대상 DB에서 서버를 식별하는 컬럼(`hostname` 또는 동등한 식별 컬럼)에 적용하세요"*라고
  **대응을 LLM에 맡긴다.** 자산 DB의 `sevrHostName`은 `VARCHAR(300)`이고(95 §3.3-⑤) FQDN·대소문자 차이가 있으면 완전 일치 `IN`은 **0건을 조용히 낸다.**
- **IP는 키로 쓰이지 않는다** — 이름 목록에도, 값 수집 분기에도 없다. 자산 DB PK는 `groupCoCd+sevrHostName+iPCtnt`라 IP가 1급 키인데 버려진다.
- 이 목록에 `sevrHostName`을 **추가하는 것은 답이 아니다** — `utils/`는 공용 계층이라 자산 스키마 리터럴이 `overfit_check`에 걸리고(D-179),
  G-4(물리 식별자) 답에 따라 또 바뀐다. **키 대응은 코드가 아니라 DB별 데이터에 있어야 한다**(§3.3).

### 1.3 식별자 소재 탐색 — 선례는 있다, 축이 다르다

| 선례 | `file:line` | 가진 것 | 없는 것 |
|---|---|---|---|
| 판정 `host_discovery` | `src/domain/host_discovery.py` | `resolved`/`ambiguous`/`not_found` 3분 · **"없다"와 "확인하지 못했다" 분리** · 순수 함수(I/O·LLM 0) | 시스템 축(폴스타/자산) |
| 실행 `host_sweep` | `src/orchestration/host_sweep.py:54,70` | 인가 존만 순회 · 전수 순회 · 0건 미캐시 · 존당 고정 조회 1회(3존 ≈150ms 실측) | 자산 DB · IP |
| 조회 `lookup_host(s)` | `noise_gate/infrastructure/polestar_hostname_resolver.py:143-159,213,240` | 고정 SQL · fail-open · 배치 1쿼리 | `r.hostname = lit` **완전 일치만** — IP·FQDN 없음 |

→ R6은 **새로 설계할 것이 아니라 이 판정을 시스템 축으로 일반화**하면 된다(§3.4).
**(v2)** 단, 탐색을 부르는 곳은 `src/orchestration/process_query.py:326` **하나**다 — 1·2단 `process_query` 서브에이전트 전용이라 **3단에서는 도달하지 않는다.**
3단에는 판정을 부를 **자리(노드)부터** 만들어야 한다(§3.4 `entity_locator`).

### 1.4 mcp_server 연결 — 이번 작업에서 완료

| 항목 | 값 · 결과 |
|---|---|
| 설정 | `mcp_server/.env` `ITAM_CONNECTION=mariadb://itam_ro:…@localhost:3307/INST1` 추가(SELECT 전용 계정 · 원본 백업 보관). 소스 정의 `config.toml [[sources]] name="itam" type="mariadb"`는 95 W-4에서 랜딩 |
| 대상 | **로컬 MariaDB 샌드박스**(`testdata/itam`, 95 트랙 S). 운영 자산 DB는 G-2(database명)·G-3(네트워크·계정) 미확정이라 연결 불가 |
| 검증(임시 포트 19198 → 본 포트 9099 순) | 기동 로그 *"MariaDB 풀 초기화 성공: itam (풀 1-3)"* · `list_sources`에 `itam (mariadb, readonly)` · `search_objects` → `TCDMSIF79`·`TCDMSIF80` · `get_table_schema TCDMSIF80` → **68컬럼 · PK `[groupCoCd, sevrHostName, iPCtnt]`** · `execute_sql` 행수 30/29 · `DELETE` → *"읽기 전용 위반"* · 폴스타 `cmm_resource` 1597행(무회귀) |
| 재기동 | 본체용 MCP 서버(9099)를 새 코드로 재기동(6일 전 기동본은 MariaDB 지원 이전 코드). 재기동 직전 9099 연결 0건 · 본체 서버·평가 실행 0건 확인. **SRE용 9097(`EXPOSE_EXECUTE_SQL=false`)은 건드리지 않았다** |
| 의도적으로 하지 않은 것 | 루트 `.env` `ACTIVE_DB_IDS`에 `itam` 추가 — 95 §5 순서 불변식(W-6 프로필 = G-4 선행). **MCP 계층은 연결됐지만 text2sql 파이프라인은 아직 자산 DB를 쓰지 않는다** |

### 1.5 함정

| # | 함정 | 근거 | 증상 |
|---|---|---|---|
| **X-T1** | 식별 키 이름 판정 | §1.2 | 자산→폴스타 체인 **차단**, IP 키 **소실** |
| **X-T2** | 스코프 컬럼 대응을 LLM에 위임 | `query_gen_common.py:1314` 블록 문구 | 폴스타→자산 체인이 **FQDN·대소문자 차이로 0건** — 오류가 아니라 빈 결과 |
| **X-T3** | 빈 분류의 첫 활성 DB 폴백 | `subagents.py:174` | 자산 질의가 폴스타로 **침묵 이동** |
| **X-T4** | 캐시 「상세」가 경계를 되살림 | `semantic_router.py:919-927` + 설명 생성 경로 | 운영자가 설명 생성을 한 번 돌리면 R2 경계가 **소리 없이 무너진다** |
| **X-T5** | 한 서버 = 자산 DB 여러 행 | 자산 PK에 IP 포함(95 §3.3-②) | 다중 IP 서버가 조인 결과에서 **중복 행**으로 부풀거나, "모호"로 오판 |
| **X-T6** | 다중 IP 한 칸 | 95 트랙 S 시드 설계(호스트 키 4형 ④) | `IN ('10.0.1.1')`이 `'10.0.1.1,10.0.1.2'`를 **못 찾는다** |
| **X-T7** | 존 미배정 DB 실행 그룹 탈락 | `src/routing/execution_groups.py:58` `if not group_code` (95 T2 · ~~W-9 사용자 결정 대기~~ **95 v9 해소 — 실행기 잔여 그룹 · 공용 분할 불변**) | 폴스타(존 있음)+자산(존 없음) 교차 계획에서 **자산 쪽이 사유 없이 빠질 수 있다**. v2: 3단 멀티 DB 경로가 운영 `ZONE_GROUP_EXCLUSIVE=false`(`.env:89`)에서 이 분할을 **직접** 탄다(`multi_db_executor.py:904-909`) |
| **X-T8** | 3단 순차 러너의 HITL 진입 금지 | `sequential_runner.py:44-46` + `src/config.py:1310` `enable_structure_approval` 기본 **True**(운영 `.env` 미설정) | 기준 경로를 3단으로 바꾸면 교차 체인이 **한 번도 실행되지 않는다** — 오류 없이 멀티 DB 병렬·단일 조회로 떨어진다. **v3: `plans/104`가 구조 승인을 질의 경로에서 제거 · `plans/103`이 순차 러너 자체를 `plan`·`Send` 루프로 대체해 해소** · **2026-09-17 부기: 104 구현(D-227)으로 `enable_structure_approval`·게이트 삭제 — 러너 진입 조건은 SQL 승인 off만 남음** |
| **X-T9** | 순차 진입이 원문 표지 문자열에 의존 | `src/utils/prior_dependency.py:51-63` `SEQUENTIAL_MARKERS`(16종) | *"HW 지원 종료가 6개월 안 남은 서버의 현재 알람"*처럼 표지 없는 교차 질의는 병렬 조회로 가 **자산 결과가 폴스타 조회를 한정하지 않는다**. 표지 목록에 자산 어휘를 더하는 것은 D-004 위반. **v3: `plans/103` 라우터 `needs_plan` 진입으로 대체(표지는 전환기 후 삭제)** |
| **X-T10** | 3단 이중 분류 | `semantic_router.py:244` + `subagents.py:1056` | 라우터가 고른 시스템과 순차 러너 서브질의 재분류가 **다를 수 있다** — 소유 센서를 한 곳에만 두면 다른 곳에서 경계가 샌다 |
| **X-T11** | 2단 tri-state 자동 on | `src/config.py:1252`(미입력 + 멀티 DB → 2단 on) · `docs/21` §6 | 기준을 3단으로 바꿔도 `ENABLE_INTENT_ORCHESTRATION` 미입력 상태로 itam을 활성화(멀티 DB)하면 **2단으로 조용히 확정**된다 |
| **X-T12** | 1·2단 전용 기능 | §1.6 「3단 미도달 기능」 행 | 운영을 3단으로 먼저 바꾸면 실시간 프로세스 조회가 라우터의 `data_query`(SQL)로 가 `plans/50` M4의 없는 테이블 조회(`SQL0204N`) 계열로 **퇴행**한다. **v3: `plans/103` P1-3에서 3단 의도·노드로 구현** |
| **X-T13** | 소유 고정이 존 고정을 덮음 | `subagents.py:1049-1058` — task `db_ids`가 있으면 원문 위치 힌트 고정(`_apply_turn_hint_pinning`)·승계를 **건너뛴다** | 폴스타 소유 task에 폴스타 DB 전체를 `db_ids`로 박으면 *"김포 서버"*의 존 한정이 사라져 **전 존 팬아웃**이 된다 → 고정은 **단일 DB 시스템(자산)만**, 다중 존 시스템(폴스타)은 **소유 DB 집합으로 제한**만 한다(§3.1) |

### 1.6 사다리 3단(`semantic_router`) 실행 경로 — v2 기준 경로 실측

| 지점 | `file:line` | 현황 |
|---|---|---|
| 노드 등록 | `src/graph.py:436-470` | `enable_semantic_routing and not use_deep_agent`일 때만 `semantic_router`·`multi_db_executor`·`result_merger`·`cache_management`·`synonym_registrar`·`general_inference`(+옵트인 `fault_diagnosis`) |
| 분기 | `graph.py:123` `_INTENT_ROUTE_MAP` · `:133` `route_after_semantic_router` · `:154` `route_after_semantic_router_sequential` | 단일 DB → `schema_analyzer` · 멀티 DB → `multi_db_executor` · 순차 진입 성립 → `sequential_runner` · 존 역질문 → `END` |
| 순차 러너 등록 | `graph.py:472-481` | `COMPOSITE_SEQUENTIAL_FALLBACK_TIERS_ENABLED` on **이고 1·2단이 아닐 때만**(운영 `.env:479` true) |
| 순차 러너 진입 | `src/orchestration/sequential_runner.py:39-51` | 전부 AND — 플래그 · **HITL 승인 플래그 둘 다 off** · 데이터 의도 · 폼필·존 역질문 아님 · **원문 순차 표지**(`prior_dependency.py:51`) |
| 구조 승인 HITL | `src/config.py:1310` 기본 True · 운영 `.env` 미설정 · 요청 발생은 `src/nodes/schema_analyzer.py:1124-1150` | **운영 설정 그대로 3단이 되면 순차 러너는 진입하지 않는다**(X-T8). 승인 요청 자체는 수동 프로필·구조 캐시가 **없는** DB에서만 발생한다 |
| 순차 러너 내부 | `sequential_runner.py:26-28,35` | 2단 부품을 함수로 재사용 — `_llm_decompose`(`intent_planner.py:668` → `_enforce_plan_contract` `:729` → `validate_plan_dag` `:760`) → `agent_orchestrator`(`assess_prior_dependency` `:194`) → `run_data_query_pipeline`(`classify_dbs` 재분류 `subagents.py:1056` · `_extract_identity_rows` `:846`) → `result_aggregator`. 실행 가능 agent는 **`data_query`·`alarm_query` 둘뿐** |
| 멀티 DB 실행 | `src/nodes/multi_db_executor.py:875-909` · `:294-305` | 대상 DB를 그룹 순서로 돌지만 **DB 사이 키 전달은 없다** — 선행 스코프는 `state.prior_rows`가 있을 때만(순차 러너 경유). 존 미배정 DB 그룹 탈락(X-T7) |
| **3단 미도달 기능** | `graph.py:123` · `sequential_runner.py:35` · `subagents.py:1259-1293` | `process_query`(실시간 프로세스 API) · `host_inspect`(OS 구성·자원 현황 단건) · 이를 통해서만 부르는 `host_discovery`는 **3단에 도달 경로가 없다.** 라우터 프롬프트는 프로세스를 `data_query`로 분류한다(`src/prompts/semantic_router.py:106`) |
| 사다리 정본 판정 | `src/observability/ladder.py:35-37` `is_canonical` = `DEEP_AGENT` · `src/config.py:1241,1252`(2·3단 tri-state) · `:1267`(1단 기본 False) | **3단 확정은 지금 "강등"으로 기록·경고된다.** 정본을 1단으로 전제한 소비처 — `scripts/scenario/preflight.py:347` · `scripts/eval_text2sql.py:54` · D-221 ⑤ 리포트 경고 · 테스트 `tests/test_observability/test_ladder_state.py`·`test_ladder_startup_log.py`·`tests/test_prompt_render_matrix.py`·`tests/test_scenario/test_server.py`·`tests/test_discovery/test_sweep.py` |
| 라우팅 골든셋 | `scripts/eval_routing.py:141` | `_llm_classify`를 직접 부른다 — 3단 노드와 `classify_dbs`가 **같은 분류기**라 H-1은 경로와 무관하게 유효하다 |

→ **3단은 R1·R2의 분류 지점은 있으나, R3·R4 체인에는 운영 설정으로 진입하지 못하고(X-T8·X-T9) R6 소재 탐색은 도달하지 않는다.**
필요한 판정·배관 함수는 이미 3단이 쓰는 모듈 안에 있다. 빠진 것은 **진입 조건·HITL 처분·프로브를 부를 자리**다.

---

## 2. 문헌이 준 설계 원칙

> 서지·게재처는 §10. **동료심사 문헌을 1차 근거로**, preprint·산업 자료는 보조로만 쓴다.

| # | 원칙 | 근거 문헌(§10) | 이 계획에서 |
|---|---|---|---|
| **P1** | **시스템 선택은 SQL 생성과 분리된 별도 결정 단계**로 둔다. 라우팅은 범주가 뚜렷하고 분류가 정확할 때만 잘 작동한다 | DBCopilot(EDBT 2025) — 스키마 라우팅과 SQL 생성 분리 · Anthropic *Building effective agents*(2024) — *"routing works well … where classification can be handled accurately"* | 답변 영역 소유표(§3.1). LLM은 **무엇이 필요한지**(답변 영역)만 말하고, 정본 시스템 결정은 코드가 한다 — 분류 대상이 "DB 이름"보다 "필요한 정보 종류"가 더 뚜렷하다 |
| **P2** | **여러 소스에 걸친 질의는 소스별 하위 질의로 분해 → 각 소스에서 평가 → 결합**한다. 계획은 LLM, 실행은 결정적 연산자 | Symphony(CIDR 2023) · CAESURA(CIDR 2024) · TAG(CIDR 2025) · LLMCompiler(ICML 2024) · Least-to-Most·DecomP(ICLR 2023) | 기존 `input_from` DAG를 그대로 쓴다. 새로 넣는 것은 **소스 사이의 키 전달 계약**뿐(§3.3) |
| **P3** | 엔터티 대응은 **결정 3분**(일치·가능한 일치·불일치)으로 내리고, 가능한 일치는 사람에게 보인다 | Fellegi–Sunter(JASA 1969) · Magellan(PVLDB 2016) | 매칭 등급 `link`/`possible`/`non_link`(+`ambiguous`)을 **응답에 노출**(§3.3-④⑤) |
| **P4** | 식별자는 **우선순위가 있는 식별 항목**으로 맞추고, 여러 대상이 걸리면 **추측하지 않는다.** IP는 변하는 속성이라 보조 키다 | ServiceNow CMDB IRE(산업 — 식별 규칙 우선순위 · 모호 일치 시 식별 실패로 기록 · IP 가변) | DB별 키 매니페스트의 `priority` · `ambiguous`는 합치지 않고 보고(§3.3) |
| **P5** | 컬럼의 의미 타입은 **값 분포**로 판정하고, 컬럼명·테이블 문맥은 보조 신호로 결합한다 | Sherlock(KDD 2019) · Sato(PVLDB 2020) | R5를 **결정적**으로 구현(§3.2). 학습 모델은 쓰지 않는다 — 대상 타입이 4종이고 표준 구문(RFC)이 있어 규칙이 학습보다 검증 가능하다 |
| **P6** | 조인 가능성은 **값 집합의 교집합 크기**로 잰다 | JOSIE(SIGMOD 2019) | 키 브리지 **커버리지 지표**(선행 키 중 대상에서 찾은 비율)를 센서로(§3.3-⑤) |
| **P7** | 호스트명은 **대소문자 비구분 비교**, IPv6는 **정규 표기**로 비교한다 | RFC 4343(2006) · RFC 5952(2010) | 정규화 규칙의 규범 근거(§3.2) |
| **P8** | 하네스 = **가이드(행동 전 조향)** + **센서(행동 후 관측·교정)**, 각각 **계산적(결정적)** 또는 **추론적(LLM)**. 행동(기능) 하네스가 가장 미성숙하다 | Böckeler(2026, martinfowler.com) · OpenAI *Harness engineering*(2026 — 원문 직접 열람 불가, §10.3 표기) | §3.6 — 교차 시스템 계약마다 계산적 센서를 1개씩 소유시킨다 |
| **P9** | 엔터프라이즈 에이전트의 **엔터티 라우팅·답변 계약은 코드가 소유**하고, 고정 시나리오 + **한 차원만 깨뜨린 변이(fault injection)**로 각 검증기가 자기 차원만 잡는지 확인한다 | Ahn & Kim(2026 preprint, arXiv 2607.08028 — 한국 기업집단 데이터로 엔터티 라우팅 계약 검증, 변이 7종 7/7 검출) | §3.6 H-3 계약 변이 스위트 |
| **P10** | **정의가 분명한 작업은 워크플로(미리 정한 코드 경로)로, 자율 에이전트는 유연한 모델 주도 결정이 필요할 때만.** 복잡도는 필요할 때만 올린다 | Anthropic *Building effective agents*(2024) — *"workflows offer predictability and consistency for well-defined tasks, whereas agents are the better option when flexibility and model-driven decision-making are needed at scale"* · *"find the simplest solution possible, and only increasing complexity when needed"*(2026-09-17 원문 재확인) | **v2 기준** — 교차 시스템 계약(시스템 소유·키 브리지·소재 판정)은 정의가 분명한 작업이라 **라우팅 워크플로(3단)**가 소유하고, 자율 에이전트(1단 deepagents)는 부가 경로로 둔다. 사용자 기준과 문헌 권고가 같은 방향이다 |

**채택하지 않은 것**

| 대안 | 근거 | 미채택 사유 |
|---|---|---|
| 학습형·LLM 엔터티 매칭으로 런타임 키 대응 | Ditto(PVLDB 2020) · Peeters et al.(EDBT 2025) | 요청마다 비결정적이고(D-035) 호출 비용이 든다. 우리 키는 구문이 정의된 호스트명·IP라 **규칙이 충분**하다. 단, **운영 별칭 사전(예: 자산 호스트명 ↔ 폴스타 서버명) 후보를 오프라인으로 제안**하는 용도는 G-3 답에 따라 2차 검토 |
| 키워드로 질의를 사전 분류해 시스템 결정 | — | **D-004 위반**. 이 계획의 소유 검증은 **LLM이 낸 구조화 출력(답변 영역)**에 대해서만 결정적으로 동작하며 질의 원문을 키워드로 훑지 않는다 |
| 공용 식별 컬럼 목록에 자산 컬럼 추가 | — | §1.2 끝 — overfit 게이트·G-4 종속 |
| 모든 식별자 질의에서 항상 양쪽 시스템 조회 | Adaptive-RAG(NAACL 2024) — 질의 복잡도별로 **충분한 최소 비용 경로** | 비용·지연. 프로브 발동은 조건부(§3.4 · G-5) |

---

## 3. 설계

### 3.1 답변 영역 소유표 (R1·R2)

**정본 시스템은 "필요한 정보의 종류"로 정한다.** 레지스트리의 기존 능력 축(`solutions[].capabilities`)을 확장한다 — 두 번째 출처를 만들지 않는다(D-053).

| 답변 영역(capability) | 정본 | 사용자 요구 | 예 |
|---|---|---|---|
| `asset_inventory` — HW/SW 자산 분류·물품·시리얼·자산 상태·보유 부점 | **itam** | R1 | "svr-web-01의 자산 분류와 시리얼" |
| `asset_contract` — 구매·유지보수 계약, 금액, 무상 보증 | **itam** | R1 | "이번 분기 유지보수 만료 서버" |
| `asset_owner` — 담당자·담당 부점 | **itam** | R1 · **95 G-9(성명 PII) 선행** | "DB 서버 담당자" |
| `asset_lifecycle` — HW/SW 지원 종료(EOS/EOL)·경과년수·노후 교체 | **itam** | R1 | "EOL 6개월 내 서버" |
| `server_status` — 가용 상태·호스트·IP·OS 구성 | **polestar** | R2 | "김포 서버 현황" |
| `server_usage` — 사용률 시계열 | **polestar** | R2 · 95 G-8 (가) | "CPU 사용률 상위 10" |
| `alarm` · `process_list` · `host_location` | **polestar**(기존) | R2 | |
| `server_spec` — CPU 코어·메모리·디스크 용량 | **G-1** (기본: polestar) | R1 "하드웨어 자산 정보"와 R2 "서버 현황"이 **겹친다** | "메모리 용량" |

**쓰는 방식 — 가이드 1 · 센서 1**

1. **가이드(계산적 렌더)**: **3단 라우터 프롬프트**(`src/prompts/semantic_router.py` DB 안내 블록)와 **분해 프롬프트**(`src/prompts/intent_planner.py` — 3단 순차 러너가 재사용)에
   소유표를 레지스트리에서 **기동 시 1회** 렌더한다(프롬프트 접두 고정 — KV 캐시 무효화 방지). 1단 `ORCHESTRATOR_INSTRUCTIONS` 렌더는 부가 경로 작업(X-14)이다.
2. **센서(계산적 검증)**: 라우터 구조화 출력(`RouterDecision`)에 DB별 `capabilities` 필드를 추가하고, 코드가 `owner(capability) == 선택 DB의 시스템`을 확인한다.
   불일치면 **정본 시스템으로 교정 + 경과 노트**(DAG 보정 노트 `NOTE_DECOMPOSE` 선례). 이 검증은 **LLM 출력만** 본다 — 질의 원문을 키워드로 훑지 않는다(D-004 유지).
   **(v2) 검증 지점은 두 곳이다(X-T10)**: ① 3단 `semantic_router` 노드 — `_llm_classify` 직후(`semantic_router.py:244`) ② 순차 러너 분해 결과 — task마다 `capability` 필드를 받아
   코드가 대상을 정한다. 이때 **자산처럼 DB가 하나인 시스템은 `task.db_ids`로 고정**해 `classify_dbs` 재분류를 건너뛰고(`subagents.py:1049` 기존 우선 배관),
   **폴스타처럼 존이 여럿인 시스템은 고정하지 않고 분류·위치 힌트 결과를 소유 DB 집합으로 제한만** 한다 — 고정하면 존 한정이 사라진다(X-T13).
3. **X-T3 대응**: 분류가 비었거나 LLM이 실패했을 때의 첫 활성 DB 폴백(3단 `semantic_router.py:250,338` · `subagents.py:174`)은 **소유표가 켜진 경우** "분류 실패" 사유를 노출하고 폴백 사실을 응답에 표기한다(침묵 강등 금지).
4. **X-T4 대응**: 소유표에 등재된 DB는 캐시 「상세」 렌더 시 **다른 시스템 소유 영역 어휘를 넣은 설명이 생성되지 않도록** 생성 프롬프트에 소유표를 주입하고, 렌더 쪽은 레지스트리 플래그 `description_locked`(기본 false)로 「상세」 덧붙이기를 끌 수 있게 한다.
5. **(v2) 교차 체인 신호 `chain`**: `RouterDecision`에 `chain: list[capability]`를 둔다 — **앞 영역의 결과가 뒤 영역의 조회 대상을 정할 때만** 그 순서대로 채우고, 서로 독립이면 빈 목록이다.
   3단 순차 진입(§3.5)은 원문 표지 문자열이 아니라 **이 구조화 출력**으로 판정한다(X-T9 · D-004). 라우팅 골든셋 `expect.chain`(H-1)이 이 필드를 채점한다.

**레지스트리 표현(G-4)** — 권고: `itam`을 `solutions`에 등재(`backend: sql` · `capabilities: [asset_inventory, asset_contract, asset_owner, asset_lifecycle]`).
단 `solutions`는 실행 그룹 순서 축(D-176)이라 **존 미배정 DB 처분(95 W-9)과 한 몸**이다 — W-9 결정 전에는 DB 항목 수준 `capabilities`로 임시 선언하는 대안을 G-4에 함께 올린다.
**(v4) 위 권고는 폐기됐다** — `solutions` 등재는 D-214 ④ 「대안(기각)」과 충돌하고, 사용자가 *"자산관리는 존 개념이 없이 1개의 시스템이다. 이에 맞게 정의하라."*로 **DB 항목 수준 `capabilities`**를 확정했다(§7 G-4 · 구현 §4.1.1 X-7).

### 3.2 값 기반 키 판정·정규화 (R5)

**도메인 순수 함수**(`src/domain/entity_key.py` — I/O·LLM 0). 공용 계층이지만 **스키마 리터럴이 없다**(컬럼명을 모른 채 값만 본다).

| 판정 | 규칙 | 정규형 |
|---|---|---|
| `ipv4` / `ipv6` | 파이썬 `ipaddress.ip_address()` 파싱 성공 | IPv4 그대로 · IPv6 **압축 소문자 표기**(RFC 5952) |
| `fqdn` | 점으로 나뉜 레이블 2개 이상, 레이블마다 RFC 1123 구문(영숫자·하이픈 · 1~63자 · 하이픈으로 시작/끝 금지) · 전체 ≤253 · **영문자 1개 이상** | **casefold**(RFC 4343) + 파생 **단축명**(첫 레이블) |
| `hostname` | 레이블 1개, 위 구문 · 영문자 1개 이상 | casefold |
| `unknown` | 그 외 | — (키로 쓰지 않는다) |

- **다중값 셀(X-T6)**: `[,;\s/]+`로 나눠 토큰마다 판정한다. 한 셀에 IP 여러 개면 **그 행은 IP 여러 개를 가진다**.
- **영문자 1개 이상 규칙**: RFC 1123은 숫자만 있는 레이블을 허용하지만, 그걸 호스트명으로 받으면 **숫자 ID 컬럼이 키로 오인**된다(D-100 `id` 오수집과 같은 계열).
- **컬럼 타입 판정(P5)**: 비어 있지 않은 값 최대 50개를 표본으로, 한 타입이 **80% 이상**이면 그 타입. 미달·동률이면 `unknown` — **추측하지 않는다**.
  기존 컬럼명 판정(`is_server_identity_col`)은 버리지 않고 **보조 신호**로 결합한다: `이름 힌트 ∧ 값 타입`이면 강한 키, `값 타입만`이면 키, `이름 힌트만`이면 **현행 동작 유지**(플래그 off와 비트 동일 경계).
- **사용자 입력 식별자**: `input_parser`가 뽑은 `filter_conditions`의 값도 같은 함수로 타입을 매긴다 — 사용자가 IP를 주면 IP 키로 간다.

### 3.3 키 브리지 계약 (R3·R4 — 양방향 동일)

**① DB별 키 매니페스트(데이터)** — 구조 정본인 프로필(`config/db_profiles/{db_id}.yaml`)에 `entity_keys` 블록으로 둔다.

```yaml
entity_keys:
  entity: server
  table: cmm_resource            # 폴스타 예. 스키마 한정은 기존 db_schema 규칙을 따른다
  keys:
    - {type: hostname, column: hostname,  priority: 1, compare: casefold}   # fqdn 값은 단축명 비교를 병행
    - {type: ip,       column: ipaddress, priority: 2}
  # name은 hostname이 아니다(D-061) — 브리지 키로 쓰지 않는다(G-3)
```

- **자산관리 매니페스트는 G-4(물리 식별자) 확정 후 `config/db_profiles/itam.yaml`(95 W-6)에 들어간다.** 그 전에는 **로컬 하네스 전용**
  `testdata/itam/entity_keys.local.yaml`(전사본 `var` 가정 표기)만 둔다 — 로컬 DB에서 추출해 정본에 넣지 않는다(95 §4.6.2 파생 금지).
  자산 매니페스트에는 `row_multiplicity: per_ip`(PK에 IP 포함 — X-T5)를 적는다.

**② 선택** — 선행 결과(시스템 S)의 키 컬럼을 §3.2로 판정해 타입별 값 집합을 만들고, 대상(시스템 T) 매니페스트 키와의 교집합을 `priority` 순으로 본다.
값이 1개 이상인 첫 타입을 쓴다(P4). 둘 다 없으면 기존 `prior_no_identity` 게이트 — 단, 사유에 **"어떤 값 타입을 찾았고 대상은 무엇을 받는지"**를 적는다.

**③ 조립** — 대상 컬럼을 **코드가 확정**해 스코프 블록에 명시한다(X-T2의 *"또는 동등한 컬럼"* 제거). 비교는 정규화 형태로
(`LOWER(<col>) IN (…)` — PG `lower` · DB2 `LOWER` · MariaDB `LOWER`, 엔진 분기는 기존 방언 규칙). 다중값 IP 컬럼은 SQL로 **넓게 좁히고**(토큰 `LIKE`)
**코드가 토큰 완전 일치로 확정**한다 — 기존 사후 대조(`apply_scope_postcheck`) 선례: *SQL이 좁히고 코드가 확인한다.* 값 상한(`_MAX_PRIOR_SCOPE_VALUES`=100)과 절단 보고는 그대로.

**④ 판정(P3)** — 선행 키마다:

| 등급 | 조건 | 결과 포함 |
|---|---|---|
| `link` | 정규형 완전 일치(호스트명 casefold · IP 토큰) | 포함 |
| `possible` | FQDN 단축명 ↔ 단일 레이블 호스트명 일치 | **G-2** (기본: 포함 + 표기) |
| `non_link` | 대상에서 못 찾음 | 미포함 + 보고 |
| `ambiguous` | 한 키가 대상의 **서로 다른 엔터티 2개 이상**에 걸림(같은 엔터티의 IP별 행은 `per_ip`로 묶어 모호로 보지 않음) | **미포함** + 보고(P4 추측 금지) |

**(v6 · 처분 F) 엔터티 경계는 DB(존)를 가른다** — 엔터티 id 는 DB 무관 정규형이라 두 존의 동명 호스트가 한 엔터티로 합쳐졌다. **서로 다른 DB에 같은 이름이 있으면** 그 이름에 한해 id 를 DB로 한정해 별개 엔터티로 만든다 → 같은 키가 엔터티 2개에 걸려 `ambiguous` 가 되고, 결과에서 빠지며 어느 DB들에 중복됐는지가 매칭 노트(`cross_db_entities`)에 남는다. 판정 근거는 행의 `_source_db` 태그다 — 태그가 없으면 종전대로 한 엔터티다. 같은 DB 안의 `per_ip` 묶음은 **한정 대상이 아니다**.

**(v6 · 처분 G) 키 컬럼 선택의 1순위는 출처 DB 매니페스트 선언**이다 — 값 판정은 영문자가 든 단일 레이블을 전부 호스트명 계열로 받으므로 코드값(`Z99`)·심각도(`critical`)·OS명 컬럼도 후보가 된다. 출처 DB가 "이것이 서버 키다"라고 선언했으면 추측보다 그 선언이 이긴다. **선언이 없을 때만** 종전 값·이름 휴리스틱(강한 키 → 값 다양성 → 컬럼 순서)으로 내려간다. 프로브 쪽에도 같은 규칙을 적용한다(§3.4).

**(v6 · 처분 E) 브리지 행 제거와 `COMPOSITE_SCOPE_POSTCHECK_ENABLED` 는 서로 다른 플래그가 소유한다.** 브리지 판정(④)은 브리지 계약의 일부이므로 `CROSS_SYSTEM_KEY_BRIDGE_ENABLED` 가 소유하고, 기존 사후 대조(`apply_scope_postcheck`)는 자기 플래그가 소유한다. 하나로 묶으면 **"브리지 on 인데 판정 결과는 버린다"**는 조합이 생겨 매칭 보고와 실제 행이 어긋난다(D5 센서가 거짓이 된다). 두 플래그가 다 켜지면 대조는 두 번 돌지만 **제거 기준이 같은 스코프**라 결과는 수렴한다 — 순서는 브리지 판정이 뒤다(후속 결과에 대한 최종 판정).

**⑤ 보고(센서 · P6)** — 기존 경과 블록(`prior_dependency.render_dependency_notes` · `## 순차 처리 경과`)에 1건:
*"선행 N대 → 자산관리: 일치 a · 가능 b · 미발견 c(샘플 …) · 모호 d · 키=hostname"*. **커버리지 = (a+b)/N**을 로그·트레이스에도 남긴다.

**방향 예시**

| 방향 | 질의 | 계획 |
|---|---|---|
| R3 폴스타→자산 | "CPU 사용률 90% 넘는 서버들의 유지보수 계약 만료일" | t1 `server_usage`@polestar → t2 `asset_contract`@itam (`input_from: [t1]`, 키 hostname) |
| R4 자산→폴스타 | "HW 지원 종료가 6개월 안 남은 서버의 현재 알람" | t1 `asset_lifecycle`@itam → t2 `alarm`@polestar (`input_from: [t1]`, 키 hostname→없으면 IP). 폴스타 대상은 **존이 나뉘므로** 키를 존 소재 탐색(§3.4)으로 분배 |

### 3.4 식별자 소재 프로브와 시스템 선택 (R6)

**판정은 `host_discovery`를 (시스템, 존) 축으로 일반화**한다 — 3분 판정과 "없음 ≠ 확인 못 함"은 그대로.

**발동 조건(G-5 기본)** — 질의가 **명시 식별자**(§3.2로 타입이 매겨진 호스트명·IP)에 걸려 있고, 다음 중 하나:
(a) 필요한 답변 영역이 두 시스템에 걸친다 · (b) 소유 시스템에서 식별자를 못 찾았다 · (c) 소유가 모호한 영역(G-1 `server_spec`)이다.
식별자가 없는 집계형 질의("EOL 6개월 내 서버")는 프로브하지 않는다 — 소유표(§3.1)만으로 결정한다.

**실행 위치(v2 · 3단 기준)** — 신규 노드 **`entity_locator`**(`src/orchestration/entity_locator.py`)를 `semantic_router` **직후 · 분기 함수 앞**에 둔다.
`CROSS_SYSTEM_PROBE_ENABLED` on일 때만 등록한다(off = 노드·엣지 미등록 → 3단 그래프 비트 동일 · D-203 `sequential_runner` 선례).
라우터 노드(`src/routing/`, infrastructure)에 넣지 않는 이유: 실행부 `host_sweep`이 orchestration 계층이라 안쪽 계층에서 부를 수 없다(`arch_check`).
노드는 결정표대로 `target_databases`를 좁히고 사유를 `dependency_notes`에 싣는다. 식별자 입력은 전 단 공통 전단인 `input_parser`의 `filter_conditions`다.
1단 도구 `locate_entities`는 **같은 판정·실행 함수**를 부르는 부가 경로(X-14)다.

**조회** — 시스템마다 **고정 조회 1회**, LLM 0회: 폴스타는 기존 `lookup_hosts`(인가 존만 · 0건 미캐시 — `host_sweep` 규약) + IP 조건 확장,
자산관리는 매니페스트 기반 `SELECT <키 컬럼> FROM <table> WHERE LOWER(<col>) IN (…)`를 MCP `execute_sql`(readonly)로.

**결정표**

| 필요한 답변 영역 | 프로브 결과 | 동작 |
|---|---|---|
| 한 시스템 소유 | 소유 시스템에서 발견 | 그 시스템만 조회(프로브 결과를 스코프로) |
| 한 시스템 소유 | 소유 시스템 **미발견** · 다른 시스템이 **키 선언 컬럼으로** 발견 | **조회하지 않고 사유 노출** — *"자산관리에 등록되지 않은 서버입니다(폴스타에는 있음)"* |
| 한 시스템 소유 | 소유 시스템 미발견 · 다른 시스템 일치가 **보조 컬럼뿐**(v6 · 처분 K) | **조회를 막지 않는다**(KEEP) + 사유 — *"폴스타에서는 등록명 일치만 있어 호스트명 기준 등록으로 보지 않았습니다"*. 브리지 키는 `hostname`뿐이라(G-3) `name` 일치는 반대 증거가 못 된다 |
| 두 시스템 모두 | 양쪽 발견 | 양쪽 조회 후 키 병합(§3.3 매칭 보고) |
| 두 시스템 모두 | 한쪽만 발견 | 발견된 쪽 조회 + 다른 쪽 미등록 사유 |
| 소유 모호(G-1) | 한쪽만 발견 | **발견된 시스템 선택** + 선택 근거 노출 |
| 소유 모호(G-1) | 양쪽 발견 | G-1 기본 정본 선택 + 다른 시스템에도 있음을 병기 |
| 어느 경우든 | 조회 실패 | *"확인하지 못함"* — 미발견과 **합치지 않는다** |

### 3.5 사다리 경로별 배선 — **3단 기준**, 1단은 같은 함수를 부르는 부가 경로 (v2)

v1은 운영 `.env`(1단)를 기준으로 삼았다. **v2는 사용자 기준(2026-09-17)에 따라 3단 `semantic_router`가 기준 경로다.** 계약은 3단에서 소유·판정하고,
판정·조립은 도메인/애플리케이션 함수 하나로 두어 **1단이 나중에 같은 함수를 부른다**(D-203 공통 모듈 선례). 3단 순차 러너가 2단 부품을 함수로 쓰므로(§1.6)
2단 함수에 넣는 수정은 **3단 작업으로 그대로 반영**된다 — 2단 배선을 따로 검증하지 않는다.
**(v3)** `plans/103`이 순차 러너를 `plan`→`dispatch`(`Send`)→`task_run`→`join`→`replan` 루프로 대체한다. 아래 ②·③·④의 호출 자리는 그때 **`plan`(소유 적용·진입)·`dispatch`/`join`(게이트·키 브리지)**으로 옮겨 가고, **함수는 같다**. HITL 조건(G-8)은 `plans/104`로 사라진다.

| 단 | 역할 | 가이드 | 센서·배선 |
|---|---|---|---|
| **3 `semantic_router`** | **기준** | ① 라우터 프롬프트(`src/prompts/semantic_router.py`)에 소유표 + `chain` 정의 + **양방향 교차 예시 2건** ② 분해 프롬프트(`src/prompts/intent_planner.py` 예시 3 데이터 의존 순차)에 교차 시스템 변형 + task `capability` 필드 — 순차 러너가 `_llm_decompose`로 재사용 | ① **소유 검증** — `semantic_router` 노드(`_llm_classify` 직후) · 빈 분류·LLM 실패 폴백 사유 노출(§3.1-2·3) ② **교차 체인 진입** — `sequential_entry`(`sequential_runner.py:39`)에 조건 추가: 라우터 `chain`이 영역 2개 이상을 담고 **소유 시스템이 서로 다르면** 원문 표지 없이 진입(X-T9). HITL 조건은 **G-8** ③ **분해 task 소유 적용** — 단일 DB 시스템은 `db_ids` 고정, 다중 존 시스템은 소유 DB 집합으로 제한(X-T10·X-T13) · DAG 보정 노트 경로(`validate_plan_dag`) ④ **키 브리지** — `agent_orchestrator`의 `assess_prior_dependency` · `subagents._extract_identity_rows` · `query_gen_common.build_prior_rows_block`(순차 러너 경유) ⑤ **소재 프로브** — 노드 `entity_locator`(§3.4) ⑥ **키 의존 없는 양쪽 조회**(R1+R2 동시 · `chain` 빈 목록) — 현행 `multi_db_executor`. 존 미배정 DB 탈락(X-T7)을 D6 센서로 사유화 |
| 1 `deep_agent` | **부가**(X-14) | `src/prompts/orchestrator.py` `ORCHESTRATOR_INSTRUCTIONS`에 **같은 소유표** 렌더 + 교차 예시 + *"식별자로 시스템이 불명확하면 소재 확인 도구를 먼저"* | 도구 `locate_entities`(`deepagents_tools.build_tools`) · `_dependency_scope`(`deepagents_tools.py:195`)·`_matched_values`(`:162`)에 **같은 키 판정 함수**. 3단 계약 확정 뒤 **동등성만** 맞춘다 |
| 2 `intent_orchestration` | 배선 기본 off(트랙 L) | — | 부품은 3단이 재사용 — 별도 작업 없음 |
| 4 `legacy` | 비범위 | — | — |

**플래그(기본 off = 현행 비트 동일 · 기동 시 1회 해석)**: `CROSS_SYSTEM_KEY_BRIDGE_ENABLED`(§3.2·3.3) ·
`CROSS_SYSTEM_PROBE_ENABLED`(§3.4 · 3단 노드 `entity_locator` 등록) · `ROUTER_CAPABILITY_OWNERSHIP_ENABLED`(§3.1 · `chain` 필드와 3단 순차 진입 조건 확장 포함). 셋은 독립적으로 켤 수 있다.
**off면 3단 그래프의 노드 구성·분기 함수·라우터 프롬프트가 현행과 같다.**

### 3.6 하네스 — 가이드·센서·평가 (P8·P9)

**계약마다 그 계약을 소유하는 계산적 센서가 정확히 하나** 있게 한다.

| 계약 차원 | 가이드(행동 전) | 센서(행동 후 · 계산적) |
|---|---|---|
| **D1 시스템 소유** | 소유표 렌더 | 선택 DB ↔ `owner(capability)` 검증 |
| **D2 키 타입** | 스코프 블록의 키 타입 명시 | 값 기반 판정 결과 ↔ 매니페스트 수용 타입 |
| **D3 정규화** | 정규형 리터럴 | 정규형 왕복 검사(casefold · RFC 5952) |
| **D4 체인 게이트** | 교차 예시 | 선행 실패·0건·키 없음 시 후속 미실행(`prior_dependency` 게이트) |
| **D5 매칭 보고** | — | 경과 노트에 등급별 건수 존재 · 합계 = N |
| **D6 침묵 탈락** | — | 계획에 있던 시스템의 결과·사유가 응답에 **둘 중 하나는** 있다(X-T7 포함) |
| **D7 소재 판정** | 소재 확인 도구 설명(ACI · poka-yoke) | 미발견 ≠ 확인 못 함 구분 유지 |
| **D8 체인 진입**(v2) | 라우터 `chain` 정의·예시 | 3단에서 `chain`이 서로 다른 소유 시스템 2개 이상 → 순차 러너 진입 · 빈 목록 → 현행 분기. 진입 판정 입력은 **구조화 출력뿐**(원문 문자열 0) |

**평가 하네스**

- **H-1 라우팅 골든셋 확장**(`testdata/routing_gold/routing.yaml` · `scripts/eval_routing.py`): 판정 필드 `expect.chain`(시스템 순서)·`expect.key_type`·`expect.probe` 추가.
  케이스군 — 자산 단독(R1) · 모니터링 단독 `forbid: itam`(R2) · 폴스타→자산(R3) · 자산→폴스타(R4) · IP 앵커(R5) · 식별자 프로브(R6) · 소유 모호(G-1).
  분류기는 `_llm_classify` 하나라(`scripts/eval_routing.py:141`) 3단 라우터 노드와 순차 러너 재분류를 함께 채점한다.
- **H-2 시나리오 군 신설** `testdata/scenarios/m_cross_system.yaml`(plans/94 카탈로그): 로컬 샌드박스 2종(폴스타 PG 5434 + 자산 MariaDB 3307 · MCP 9099)에서 **사다리 3단 확정 기동**(기동 로그 `tier=semantic_router` 첨부)으로
  **결정적 오라클**로 판정 — ①기대 호스트 집합은 `testdata/itam/README.md`의 수기 SQL 오라클 ②**모니터링 단독 응답에 자산 시드의 사용률 식별 패턴(소수부 `.37`)이 나오면 R2 위반**(95 트랙 S가 심어 둔 판별값을 센서로 쓴다)
  ③호스트 키 4형(정확·대소문자·FQDN·다중 IP)이 각각 기대 등급(`link`/`link`/`possible`/`link`)으로 보고되는지.
- **H-3 계약 변이 스위트**(P9): 기준 1건에서 **D1~D8을 하나씩만 깨뜨린 변이 8건**을 만들고, **깨뜨린 차원의 센서만** 실패하는지 단언한다. 단위 테스트 · LLM 0 · 기본 스위트.
- **H-4 로컬 실행 리그**: 목업 LLM 경로로 전 체인을 먼저 돌리고(`RUN_DOCKER_IT=1`), 실 LLM 실행은 **D-127 건별 승인 + `RUN_E2E=1`** 뒤에만.
  리그의 자산 프로필은 전사본에서 만든 **테스트 전용 사본**(`testdata/itam/` · 가정 표기)을 쓴다 — `config/db_profiles/`에 넣지 않는다.
  **(v2) 리그 설정은 3단을 명시한다** — `ENABLE_DEEPAGENTS_PACKAGE=false` · `ENABLE_INTENT_ORCHESTRATION=false` · `ENABLE_SEMANTIC_ROUTING=true`(미입력 금지 — X-T11). G-8 처분 전에는 구조 승인 설정도 명시한다.

### 3.7 기준 경로 전환 — 트랙 L (v2 · D-225)

사용자 기준을 코드·문서·운영에 반영하는 최소 작업이다. **삭제는 없다** — 1단은 부가 경로로 유지하고(D-161 ① 승격-폐기 동반 원칙의 **명시 예외** — 사용자가 부가 사용 예정을 밝혔다),
2단 모듈은 3단이 재사용한다(`docs/21` §7). 따라서 D-161 ② 4항 실측(폐기 제안 요건)은 해당하지 않는다.

| # | 대상 | 변경 | 비고 |
|---|---|---|---|
| **L-1** | `src/config.py:1241,1252` tri-state 해석 | 미입력 시 **3단 확정** — `enable_semantic_routing` 자동 on 유지, `enable_intent_orchestration` 자동 **off**(X-T11). `enable_deepagents_package` 기본 False 유지(`:1267`) | 사용자 확정 설정은 `.env`가 아니라 **코드 기본값**으로 고정. 운영 `.env`는 세 플래그를 명시하므로 이 변경만으로 운영 단은 바뀌지 않는다 |
| **L-2** | `src/observability/ladder.py:35-37` 외 | 정본 단 = `SEMANTIC_ROUTER`. **1단 확정은 "강등"이 아니라 부가 경로 opt-in으로 INFO**, 2단·4단 확정은 WARNING. 강등 사유 어휘(`flag_off` 등) 재정의 | 기동 로그 형식(`tier=… degraded_reason=… resolved_by=…`)은 유지 — 로그 판독 도구 호환 |
| **L-3** | 정본을 1단으로 전제한 소비처 | `scripts/scenario/preflight.py:347` · `scripts/eval_text2sql.py:54`(기준 경로 표기·`--path` 기본) · D-221 ⑤ 리포트 최상단 경고 기준 · 테스트 5파일(§1.6) | **`scripts/scenario/preflight.py`는 2026-09-17 현재 병행 세션이 미커밋 수정 중** — 착수 전 `git status`·`ListAgents`로 확인 |
| **L-4** | 문서 | `docs/21_orchestration_ladder.md` §1·§4·§5·§8 · `CLAUDE.md` 사다리 표·운영 실측 문단 · `src/graph.py:353-364,716-718` 사다리 주석 · `.env.example` 사다리 주석 | **코드(L-1·L-2)와 같은 커밋** — 문서가 실제 기동과 어긋나면 `docs/21`의 단일 출처 역할이 깨진다(D-162) |
| **L-5** | 운영 `.env` | `ENABLE_DEEPAGENTS_PACKAGE=false` · `ENABLE_INTENT_ORCHESTRATION=false` 명시 | **`plans/103` P5(동등성 판정) 완료 뒤, 사용자 확인 후에만**(v3 — 종전 "G-10 처분 뒤"). 전환 직후 기동 로그 `tier=semantic_router` 확인 |
| **L-6** | 검증 기준 이동 | `plans/95` §0.2 ⑥ · W-12 · §6-2의 "1단에서 판정" → 3단 | **v2와 함께 반영 완료**(95 v7) |

---

## 4. 트랙·작업 분해

| WU | 트랙 | 작업 | 선행 | verify |
|---|---|---|---|---|
| **X-0** | — | G-1~G-6·G-9 확정(사용자 인터뷰 · G-7 v2 확정 · G-8·G-10 v3 해소) | — | 답이 §7에 기록 |
| **X-1** | K | `src/domain/entity_key.py` — 값 타입 판정·정규화·다중값 분해·컬럼 타입 판정(§3.2) | — | RFC 사례(IPv6 압축·대소문자) · 숫자 ID 오인 0 · 다중 IP 셀 · 표본 80% 경계 · 순수성(I/O 0) |
| **X-2** | K | 매니페스트 스키마·로더 + 폴스타 프로필 4종 `entity_keys` + `testdata/itam/entity_keys.local.yaml` | X-1 · G-3 | 매니페스트 컬럼이 프로필 구조 정본에 실존 · `catalog_diff`/`prompt_render_diff` 무변화 |
| **X-3** | K | 키 브리지(선택·조립·판정·보고 — §3.3) · 방언 3종 | X-1·X-2 · G-2 | 샌드박스 키 4형 기대 등급 · `ambiguous` 미포함 · `per_ip` 묶음 · 절단 보고 · 매칭 합계 = N |
| **X-4** | K | **3단 기준 배선**(v2) — 순차 러너가 부르는 `subagents._extract_identity_rows` · `agent_orchestrator`의 `assess_prior_dependency` · `build_prior_rows_block`(플래그). 1단 `_dependency_scope`는 X-14 | X-3 | **플래그 off 비트 동일**(기존 D-203 테스트 전건) · on에서 **3단 순차 러너 경유** 자산→폴스타 게이트 통과(§1.2 실행 재현이 반대로 뒤집힘) |
| **X-5** | P | `host_discovery` 판정 (시스템, 존) 일반화 | — | 기존 존 판정 테스트 무변화 · 시스템 축 3분 + 확인 못 함 분리 |
| **X-6** | P | 소재 프로브 실행(폴스타 `lookup_hosts` IP 확장 · 자산 매니페스트 조회) · **3단 노드 `entity_locator`**(플래그 on일 때만 등록 · §3.4). 1단 도구 `locate_entities`는 X-14 | X-2·X-5 · G-5 | 시스템당 조회 1회 · 인가 존만 · 0건 미캐시 · 결정표(§3.4) 7행 단위 테스트 · **off에서 3단 그래프 노드 목록 무변화** · `arch_check` 위반 0 |
| **X-7** | R | 레지스트리 답변 영역(G-4 형태) · 라우터 `RouterDecision.capabilities`·**`chain`** · 소유 검증 **2지점**(3단 라우터 노드 · 분해 task — 단일 DB 시스템 `db_ids` 고정 / 다중 존 시스템 DB 집합 제한) · 교정 노트 · 빈 분류·LLM 실패 폴백 표기 · `description_locked`(플래그) | G-1·G-4 | 플래그 off 라우터 프롬프트 골든 무변화 · on에서 소유 위반 교정(두 지점 각각) · 폴스타 소유 task의 존 한정 유지(X-T13) · 키워드 스캔 0(D-004 — 코드 리뷰 체크) |
| **X-8** | R·H | 가이드 — **3단 라우터 프롬프트**·`intent_planner` 분해 예시(순차 러너 재사용) 교차 양방향 · 소유표·`chain` 렌더. `ORCHESTRATOR_INSTRUCTIONS`는 X-14 | X-7 | `scripts/prompt_render_diff.py` — off 무변화 · on 증분만 |
| **X-9** | H | 라우팅 골든셋·판정기 확장(H-1) | X-7 | `eval_routing --mock` 신규 케이스 전건 · 기존 18건 무회귀 |
| **X-13** | R | **3단 교차 체인 진입**(v2) — ~~`sequential_entry`에 라우터 `chain` 조건 · HITL 조건 처분(G-8)~~ **v3: `plans/103` `plan` 노드 진입(`needs_plan` ∨ `chain` 비어 있지 않음)에 `chain` 조건 합류 · HITL은 `plans/104`로 제거** · 불성립 사유 노트 | X-7 · **103 P2-1 · 104 A-8** | 표지 없는 교차 질의 2방향(R3·R4 예시)이 순차 러너로 진입 · `chain` 빈 목록이면 현행 분기 · 원문 문자열 매칭 코드 추가 0(D-004 리뷰 체크) · 플래그 off 분기 함수 비트 동일 · 기존 D-203 3·4단 진입 테스트 전건 |
| **X-10** | H | 시나리오 군 `m_cross_system`(H-2) · 계약 변이 스위트(H-3) · 로컬 리그(H-4) — **3단 확정 기동** | X-4·X-6·X-8·**X-13** | 변이 **8/8** · 각 변이는 소유 센서만 실패 · `RUN_DOCKER_IT=1` 목업 체인 통과 · 기동 로그 `tier=semantic_router` |
| **X-11** | H | 실 LLM 평가 — 경계·체인·프로브(**3단**) | X-10 · **95 W-6(G-4)** · D-127 승인 | 성공 기준 §5 1~4 · 3단 확정 기동 로그 첨부 |
| **X-14** | K·P·R | **1단 부가 경로 동등성**(v2) — `ORCHESTRATOR_INSTRUCTIONS` 소유표·교차 예시 · 도구 `locate_entities` · `_dependency_scope` 값 기반 키 판정 — 전부 3단과 **같은 함수** 호출 | X-11 · G-9 | H-2 시나리오를 1단 확정 기동으로 재실행해 판정이 3단과 일치 · 1단 전용 판정 로직 신설 0 |
| **X-12** | — | 문서 — D-224·**D-225** 등재 · `docs/21`(트랙 L-4와 합류) · INDEX · `plans/95` 연결 | 전건 | 링크·번호 실존 |
| **L-1~L-4** | L | 기준 경로 전환 — 코드 기본값·사다리 정본 판정·소비처·문서(§3.7) | — (L-3은 병행 세션 확인) | 설정 미입력 + 멀티 DB에서 기동 로그 `tier=semantic_router` · 1단 opt-in 시 WARNING 아닌 INFO · 사다리 테스트 갱신 전건 통과 · `docs/21`·`CLAUDE.md`가 기동 로그와 일치 |
| **L-5** | L | 운영 `.env` 3단 전환(§3.7) | L-1~L-4 · **`plans/103` P5**(v3) · 사용자 확인 | 전환 후 기동 로그 `tier=semantic_router` · 기존 폴스타 골드셋 회귀 0 |
| **L-6** | L | `plans/95` 검증 기준 1단 → 3단 | — | **완료**(95 v7) |

**순서 불변식**
- **X-1~X-3은 게이트 없이 착수 가능**(G-2·G-3은 기본 가정으로 시작하고 답이 오면 매니페스트·등급 표만 바꾼다).
- **운영 활성화는 95 W-12(=`ACTIVE_DB_IDS`에 itam) 이후**다. 이 계획의 플래그를 먼저 켜도 itam이 비활성이면 교차 체인은 발생하지 않는다 —
  단, **값 기반 키 판정(X-4)은 폴스타 단독 체인에도 영향**을 주므로 플래그 on 전 기존 D-203 시나리오 재측정이 필요하다.
- **(v2) 판정 경로는 3단이다** — X-4·X-6·X-13의 verify와 X-10·X-11은 3단 확정 기동에서 한다. 운영 `.env`가 아직 1단이므로 로컬 리그·평가는 H-4 설정을 명시한다.
- **(v2) X-14(1단 동등성)는 3단 계약 확정(X-11) 뒤**다 — 기준이 먼저 고정돼야 동등성을 잴 수 있다.
- **(v2→v3) L-5(운영 전환)는 `plans/103` P5 뒤**다 — 3단 미도달 기능(X-T12)을 둔 채 운영을 바꾸면 퇴행한다. L-1~L-4는 운영 단을 바꾸지 않으므로(운영 `.env`가 세 플래그 명시) 먼저 착수할 수 있다.
- **(v2) L-1(tri-state 자동 off)은 95 W-12보다 먼저** 들어가야 한다 — 미입력 환경에서 itam 활성화(멀티 DB)가 2단 자동 확정을 부르는 것을 막는다(X-T11).


### 4.1 구현 현황 (v5 · 2026-09-21 재개 — 재개 체크리스트 1~7 처리)

> 착수 기준(v4 · 2026-09-17): 작업 트리 `multiintent` HEAD `c64ef98` + 병행 세션 미커밋 변경.
> **재개 기준(v5 · 2026-09-21)**: v4 구현분은 전부 커밋됐다 — HEAD **`284137a`**("ITAM, mlx 테스트, 벤치마크 테스트 코드 수정", 256 files · 사용자 일괄 커밋). 재개 시점 작업 트리는 깨끗했고, 기준선 대조는 `git worktree add <scratchpad> HEAD`로 했다. **이번에도 커밋하지 않는다**(사용자가 일괄 커밋).
> 플래그 3종(`CROSS_SYSTEM_KEY_BRIDGE_ENABLED` · `CROSS_SYSTEM_PROBE_ENABLED` · `ROUTER_CAPABILITY_OWNERSHIP_ENABLED`)은 **기본 off = 현행 비트 동일**이고, 운영·로컬 `.env`는 건드리지 않았다(L-5 차단 유지).

#### 4.1.1 WU별 상태

| WU | 상태 | 랜딩(주요 파일) · 비고 |
|---|---|---|
| **X-1** | ✅ | `src/domain/entity_key.py`(값 판정·정규화·다중값 분해·컬럼 계열 판정·매니페스트 모델·매칭 등급) · 테스트 `tests/test_cross_system/test_entity_key.py`. **계획 대비 조정 3**: ①컬럼 판정 단위를 값 타입이 아니라 **키 계열**(`hostname`=hostname·fqdn / `ip`=ipv4·ipv6)로 — 한 컬럼에 단축명·FQDN이 섞이는 게 정상 ②단일 레이블 **2자 이상**(`Y`/`N`·`N/A`가 호스트명 컬럼으로 판정되던 것 차단) ③`possible`은 **FQDN ↔ 단일 레이블**만(양쪽 FQDN이면 도메인이 다른 별개 호스트) |
| **X-2** | ✅ | 로더 `src/schema_cache/entity_key_manifest.py` · 폴스타 프로필 4종 `entity_keys`(`cmm_resource` · hostname p1 casefold · ipaddress p2 · `name` 제외 — G-3) · `testdata/itam/entity_keys.local.yaml`(G-4 물리 식별자 미확정 **가정 표기** · `per_ip` · IP 다중값). 프롬프트 렌더·카탈로그 무변화 테스트 포함(`test_manifest_profiles.py`). **런타임 로더는 testdata를 읽지 않는다** → 자산 매니페스트는 런타임에 없다(§4.1.4 ②) |
| **X-3** | ✅ | `src/nodes/key_bridge.py`(선택·조립·판정·보고). utils는 **인자 확장만**(`assess_prior_dependency(identity=, no_identity_hint=)` · `build_prior_rows_block(target_scope=)`) — utils→domain import 금지 계층 규칙 때문 |
| **X-4** | ✅ | 플래그 뒤 배선: `agent_orchestrator`(게이트·브리지 사후 판정) · `subagents._extract_identity_rows` · `query_generator` · `multi_db_executor._prior_for_db`. off 비트 동일 테스트 + on에서 **3단 순차 러너 경유 자산→폴스타 체인 통과**(§1.2 실행 재현이 뒤집힘 — `test_bridge_wiring.py`). 1단 `_dependency_scope`는 미적용(X-14) |
| **X-5** | ✅ | `src/domain/host_discovery.py` 시스템 축 추가(`SystemProbe`·`plan_probe`·`decide_systems`(결정표 7행)·`system_trace_payload`) — 기존 존 축 API·테스트 불변 · `test_probe_decision.py` |
| **X-6** | ✅(실측 제외) | `src/orchestration/entity_locator.py`(LLM 0 · 시스템/존당 고정 조회 1회 · 인가 존만 · 미캐시 · 개별 try) · `noise_gate/infrastructure/polestar_hostname_resolver.py` **신규** `build_host_probe_sql`·`probe_hosts`(기존 `lookup_host(s)` 불변 · 프로브는 fail-open 아님) · `src/graph.py` 3단 전용 등록(off면 노드·엣지·분기 함수 무변화 — `test_probe_graph_wiring.py`). itam은 **존 없음 · 시스템 1회 조회**(G-4 확정). 성공 기준 9(300ms) **미측정** |
| **X-7** | ✅ | 레지스트리: 최상위 `capabilities`(설명 카탈로그 10종) · `solutions[polestar].capabilities`에 `server_status` · **itam DB 항목 `capabilities`**(G-4 확정) · `DBEntry.description_locked`(기본 false) · 소유 API(`system_of`·`capability_owners`·`system_db_ids`…) · `src/routing/capability_ownership.py` · 라우터 출력 on 전용 서브클래스(`OwnershipRouterDecision` 등 — instructor 스키마 off 불변) · 소유 검증 2지점(라우터 노드 / 분해 task: 단일 DB 시스템 `db_ids` 고정 · 다중 존 시스템 제한만 — X-T13) · 폴백 노트(X-T3) · X-T4 설명 생성 프롬프트 · `output_generator._append_cross_system_notes`(교차 시스템 노트 4종만 렌더) |
| **X-8** | ✅ | 라우터(단일·2단 대칭)·분해 프롬프트 on 전용 슬롯 — **off 골든 바이트 동일 · `prompt_render_diff` 0** · on 증분(폴스타 3존+itam 기준 라우터 +2,652자 · 분해 +2,283자) · 기동 시 1회 렌더(캐시) |
| **X-9** | ✅ | 골든셋 25건(기존 18 + 신규 7) · `expect.chain`·`key_type`·`probe` · `eval_routing --mock` **off 25/25**(기존 18 무회귀 · chain 채점 불가 7건 명시) · **on 25/25(chain 7/7)** · `chain_reversed` 변이 검출. `key_type`·`probe`는 라우터 출력에 없어 "라우터 단계 채점 불가(H-2 소관)"로 표기(거짓 통과 금지). 목업 결과는 배관 검증이지 모델 품질이 아니다 |
| **X-13** | ⛔ 차단 | `plans/103` P2-1 미구현(`needs_plan` 코드 0건). `plans/104` A-8은 병행 세션이 구현 완료(구조 승인 HITL 삭제). `sequential_entry` 무수정 |
| **X-10·X-11·X-14** | ⛔ 차단 | X-10 ← X-13 · X-11 ← X-10 · 자산 프로필(104) · D-127 승인 · X-14 ← X-11 |
| **X-12** | ✅ (v5) | `config/settings_help/{general,router}.yaml`(신규 플래그 3종 · 사다리 플래그 설명) · `.env.example` · `docs/21` 반영. **v5 완료**: `docs/02_decision.md` **D-224**(부분 확정 · G-4 확정 원문·기본 가정 6종 미확정 표기)·**D-225**(확정 · 코드 기준 전환 완료 · L-5 대기) 본문 등재 + 안내 라인·「채번 이력」·「변경 이력」 3곳 갱신 · `docs/18` 5건 기록 · `plans/INDEX.md` 102행 갱신 |
| **L-1** | ✅ | `src/config.py` — `enable_intent_orchestration` 미입력 → **항상 off** · `resolved_by=code_default`(semantic 명시 + intent 미입력) · 경고 문구 · 테스트 고정 |
| **L-2** | ✅ | `src/observability/ladder.py` — 기준 단 3단 · 1단 opt-in은 INFO · 2·4단·opt-in 실패는 WARNING · 사유 어휘 `none`/`intent_flag_on`/`semantic_routing_off`/`orchestrator_unavailable`/`package_missing`(**`flag_off` 폐기**) · `OPTIN_FAILURE_REASONS` ↔ 시나리오 `UNINTENDED_DEGRADATION` 동기 테스트 · 기동 로그 첫 줄 형식 불변 |
| **L-3** | ✅ | `scripts/scenario/preflight.py`(`_check_ladder`만) · `report.py`(기준 단 3단 · 1단은 안내) · `server.py`(주석) · `scripts/eval_text2sql.py --path semantic_router` 추가(기본 `orchestration` 유지 — 단일 DB 파이프라인 직접 구동이라 사다리 단 중립) |
| **L-4** | ✅ (v5) | `docs/21` · `src/graph.py` 사다리 주석 · `.env.example` · 설정 도움말 반영. **v5 완료**: `CLAUDE.md` 사다리 절(도입 문장·표 라벨 1 부가 경로/3 기준 경로·tri-state 문단·1단 opt-in INFO/`flag_off` 폐기 문단·운영 실측 문단) · 옛 "1단 정본" 주석 `src/api/routes/query.py`(3곳)·`src/static/js/app.js` · 옛 `flag_off` 서술(D-221 부기 신설 · D-222 부기 2 ② 개정 표기 · `plans/94` 무효 판정 규칙 · `plans/96` O-4 · `plans/99` 5곳 — preflight 예시를 실제 출력(`intent_flag_on`)으로 교정하고 사유 어휘 정의를 새 3종으로 교체). **남긴 것**: `docs/21:26`·`scripts/scenario/report.py:400`의 `flag_off` 언급은 *옛 어휘임을 설명하는 문장 자체*라 유지 |
| **L-5** | ⛔ 차단 | `plans/103` P5 + 사용자 확인. `.env` 무변경 |

#### 4.1.2 적용한 기본 가정 (사용자 미확정 — 답이 오면 해당 데이터·상수만 바꾼다)

| 게이트 | 적용값 | 바꿀 곳 |
|---|---|---|
| G-1 | `server_spec` 정본 폴스타 + 결정표 "소유 모호" 행 | ~~`entity_locator.OWNERSHIP_AMBIGUOUS_CAPABILITIES`~~ **`config/db_registry.yaml` `capabilities[].ambiguous_owner`**(v6 · 처분 J) · 골든 r-076 |
| G-2 | 호스트명 → IP 우선 · FQDN 단축명 일치는 **포함 + "가능한 일치" 표기** | 프로필 `entity_keys` priority · `key_bridge` possible 포함 |
| G-3 | 폴스타 `hostname`만 브리지 키(`name` 제외 · D-061) | 폴스타 프로필 `entity_keys` |
| G-5 | 조건부 발동(두 시스템 필요 · 소유 시스템 미발견 · 소유 모호) | `host_discovery.plan_probe` |
| G-6 | 미발견 샘플 10 + "다른 시스템에만 있음" 안내 | `host_discovery.decide_systems` |
| G-9 | (a) 프로세스 단위 opt-in — X-14 범위 밖 | — |
| **G-4** | ✅ **사용자 확정(2026-09-17)** — DB 항목 `capabilities` · `solutions`·`zone_groups` 미등재 | §7 · **✅ 사용자 확정(2026-09-17 · v4)**: *"자산관리는 존 개념이 없이 1개의 시스템이다. 이에 맞게 정의하라."* → itam은 **DB 항목 수준 `capabilities`**로 선언하고 `solutions`·`zone_groups`에 등재하지 않는다(D-214 ④ 기각 대안과 비충돌 · 기본 가정 `solutions` 등재는 D-214 ④와 충돌해 이탈안으로 올렸고 사용자가 이 방향을 확정). 소유표·프로브·매니페스트·응답 표기 어디에도 itam에 존 의미 없음 — 프로브는 "존 없음 · 시스템 1회 조회" |

`asset_owner`(담당자 성명) 비활성 완화(§6)는 **불필요로 재판정** — `plans/95` G-9가 "마스킹 없이 노출"로 확정됐다. itam 소유 영역에 포함했다.

#### 4.1.3 검증 (중지 시점 · 과금 호출 0 · 실 DB·MCP 0)

- `tests/test_cross_system/` **298 passed** · 트랙 L·R이 수정한 기존 테스트 묶음 **290 passed** · 관련 디렉토리 묶음 980 + 2,510 + 2,843 passed(린트 수정 뒤 핵심 845 passed 재확인).
- 실패는 전부 우리 변경 밖으로 확정: HEAD worktree·편집 전 사본에서 재현(`test_prompt_render_matches_snapshot` · `test_polestar_prompt_render::…[polestar_b0]` · `test_cache_manager` 1 · `test_cache_manager_new_features` 3 · noise_gate 분석기 4) · 병행 세션 plans/104 테스트 1건 1회성(단독 재실행 통과).
- 게이트: `arch_check --ci` error 0(WARN +2 — `key_bridge` import · `src/schema_cache/` 이동 검토) · `overfit_check --ci` 신규 0 · `prompt_render_diff --ci` 0 · ruff 변경 파일 HEAD 대비 신규 0(병행 세션 변경분 제외) · mypy HEAD 대비 신규 2건 **환경성**(`entity_locator` `langchain_core` import-not-found · `graph.route_after_entity_locator` no-any-return — uvx 환경에 langchain/langgraph 없음, 기존 `route_after_*`와 같은 부류).
- **전체 스위트 미완**: 백그라운드 실행을 LLM provider 덮어쓰기 없이 시작했다가 공유 MLX 8080 호출 위험으로 11% 지점에서 중단했다.
- **중지 후 재확인(main 세션 · 2026-09-17 17:5x)**: `LLM_PROVIDER=ollama OLLAMA_BASE_URL=http://127.0.0.1:9 .venv/bin/python -m pytest tests/test_cross_system tests/test_observability tests/test_scenario/test_preflight_ladder.py tests/test_routing_eval tests/test_semantic_routing tests/test_discovery` → **795 passed · 0 failed** · `arch_check --ci` exit 0 · `overfit_check --ci` exit 0. 전체 스위트는 여전히 미실행.
- `mcp_server` 코드 무변경 → 로컬 MCP 9099 재기동 불필요.

**v5 검증 (2026-09-21 재개 · 과금 호출 0 · 실 DB·MCP 0 · 커밋 0)**

- **전체 스위트**(`LLM_PROVIDER=ollama OLLAMA_BASE_URL=http://127.0.0.1:9 pytest`, 428초): **8,010 passed · 25 failed · 31 skipped · 5 errors**.
- **기준선 대조** — `git worktree add <scratchpad> HEAD`(`284137a`)에서 같은 명령으로 전체 스위트: **7,990 passed · 37 failed · 31 skipped · 8 errors**. 실패 집합 `comm` 대조 결과 **우리 쪽에만 있는 실패는 1건**이고, 그 1건(`tests/test_plan31_field_mapping_fix.py::…::test_llm_synonym_counted_in_summary`)은 **기존 `.env` 누수 결함**으로 확정했다 — 로컬 `.env:437 SYNONYM_FUZZY_MATCH=true`(코드 기본값 `False`)면 2단계 퍼지 매칭이 `IP`를 먼저 잡아 2.8단계가 아예 돌지 않는다. `SYNONYM_FUZZY_MATCH=false`를 주면 통과한다. 기준선 worktree는 **`.env`가 untracked라 존재하지 않아** 통과했을 뿐이다. → **우리 변경으로 인한 신규 회귀 0**.
  - 기준선 쪽에만 있는 실패 16건(`test_query_stream_progress[file]` 4 · `test_unknown_class` 4 · `test_dbhub_integration` 3 · `test_settings_catalog` 2 · `test_alarm_process_enrich` 2 · `test_answer_history_propagation` 1)도 같은 이유(`.env` 부재)와 실행 순서 차이다 — 기준선 worktree는 환경 완전 복제가 아니다.
  - `tests/test_schema_cache/test_plan104_service_registration.py::…::test_full_admin_flow_never_writes_registry_env_or_mcp_server`는 **두 트리 모두 단독 실행 시 25 passed** — 전체 스위트 실행 순서 의존이다.
- **좁은 재확인**: `tests/test_cross_system` + `test_observability` + `test_preflight_ladder` + `test_routing_eval` + `test_semantic_routing` + `test_discovery` + `test_prior_scope_by_db` + `test_plan104_query_path` + `test_composite` → **1,197 passed · 0 failed**. 신규 `tests/test_cross_system/test_multi_db_note_merge.py` 5건 포함(수정 전에는 2건 실패로 재현됐다).
- **게이트**: `arch_check --ci` **exit 0**(error 0 · WARN 80 — `key_bridge` 2건 포함, 처분은 §4.1.4-6) · `overfit_check --ci` **exit 0**(`scripts/overfit_baseline.json` 무변경) · `prompt_render_diff --ci` **exit 0** · ruff(`uvx --offline`) 변경 파일 HEAD 대비 **신규 0**(양쪽 90건 동일) · 신규 테스트 파일 ruff **0** · mypy(`--python-version 3.12`) `multi_db_executor.py` HEAD 대비 **델타 0**(양쪽 110건 동일 — 전체 카운트 차 +2는 병행 세션의 미커밋 `semantic_router.py`에서 난다).
- **병행 세션 주의(실측 2026-09-21 07:4x~07:5x)**: 작업 트리에 다른 세션의 미커밋 변경이 계속 들어왔다 — `src/routing/semantic_router.py`(+113/-16 · `plans/95` W-10) · `plans/{49,91,INDEX}.md` · `docs/26` · `sre_agent/*`. 공유 파일은 편집 직전 `git diff`로 확인하고 외과적으로만 고쳤다.

#### 4.1.4 재개 결과 (v5 · 2026-09-21 — v4 체크리스트 1~7 처리)

| # | 항목 | 결과 |
|---|---|---|
| 1 | **멀티 DB 경로 노트 덮어쓰기 수정** | ✅ 재현 테스트(`tests/test_cross_system/test_multi_db_note_merge.py` 5건 — 먼저 2건 실패 재현) → `src/nodes/multi_db_executor.py` 결과 조립에서 `state`의 기존 노트를 앞에 이어 붙이고 이번 노드 노트를 `add_db_note`로 병합(**단일 경로 `schema_analyzer`와 같은 규칙 · DB당 1건**). **반환 shape는 유지** — 이번 노드 노트가 없으면 키를 만들지 않는다(키 부재 = state 보존). 기존 단언 `test_prior_scope_by_db::test_executor_returns_notes_only_when_present` 무변화 |
| 2 | **전체 스위트** | ✅ 실행(수치는 §4.1.3) · 실패는 HEAD 기준선 대조로 전건 기존 실패 확정 |
| 3 | **D-224·D-225 본문 등재** | ✅ 등재 직전 세 곳(`## D-` 헤더 최댓값 D-231 · 「변경 이력」 · 「채번 이력」) 재grep — D-224·D-225 헤더 0건·예약 행만 존재 → 예약 번호 그대로 사용. D-224 **부분 확정**(G-4 확정 원문 수록 · 기본 가정 6종을 「사용자 미확정」으로 명시) · D-225 **확정**(코드 기준 전환 완료 · L-5 대기 · `flag_off` 폐기와 새 사유 어휘 · `OPTIN_FAILURE_REASONS`↔`UNINTENDED_DEGRADATION` 동기 · `eval_text2sql --path semantic_router` 추가 및 기본값 `orchestration` 유지 사유). D-221 ⑤에 개정 부기 신설, D-222 부기 2 ②에 개정 표기 |
| 4 | **`docs/18` 기록** | ✅ 5건 — ①워커별 스크래치 경로 미분리(사본 덮어쓰기) ②기준선 worktree에서 `git checkout --` ③계획 기본 가정이 D-214 ④ 「대안(기각)」과 충돌 ④넓은 스위트를 provider 덮어쓰기 없이 시작 ⑤**(신규)** 리듀서 없는 누적 state 키를 병합 없이 반환(체크리스트 1의 근본 원인) |
| 5 | **`CLAUDE.md` 사다리 절 + 옛 서술 정리** | ✅ §4.1.1 L-4 참조 |
| 6 | **`arch_check` WARN +2 처분** | ✅ **`src/nodes/key_bridge.py`를 그대로 둔다.** 근거: ①이 WARN은 `arch_check.py:238` `severity="warning"`으로 **설계상 권고**다(`--ci` exit 0 · error 0) ②같은 종류 WARN이 저장소에 80건 있고 그중 `src/nodes/`의 노드 보조 모듈 교차 import가 `column_deriver`(6)·`query_validator`(5)·`candidate_generator`(4)·`prompt_blocks`(2) 등 **확립된 패턴**이다 — key_bridge만 옮기면 오히려 불일치다 ③`src/schema_cache/`(infrastructure)로 옮기는 것은 의미가 틀리다: 그 패키지는 스키마 캐시·로더 소유이고(매니페스트 **로더**는 이미 거기 있다), key_bridge는 I/O 없는 조립·판정이라 소비처(노드)와 같은 높이다 ④`src/utils/`는 **불가** — `ALLOWED_DEPS["utils"] = set()`이라 `src.domain.entity_key` import가 warning이 아니라 **error**가 된다(구현 시 utils를 인자 확장만 한 것과 같은 이유) |
| 7 | **`plans/95` W-10** | ⛔ **이번 범위에서 제외 — 95 소관 유지.** 실측(2026-09-21 07:52): **병행 세션이 지금 구현 중**이다 — 작업 트리 `src/routing/semantic_router.py`가 HEAD 대비 +113/-16이고 신규 함수 `_keep_zoneless_targets`(docstring이 `plans/95 W-10`·`plans/102` 트랙 R 실측을 인용)가 `selected_db_ids` 분기(`:176`)에 배선돼 있다. HEAD에는 0건. 중복 구현·충돌을 피해 손대지 않았다. **2단 대칭**(`subagents.py:1101-1106` 소유 고정이 `selected_db_ids` 있으면 건너뜀)은 그 세션 범위에 **포함되지 않았다**(미커밋 diff 실측 — 고친 곳은 게이트 **아래** `raw_targets` 산출부). 다만 이 조건은 결함이 아니라 `:1098` 주석·X-T13 근거로 **의도된 설계**이며, 남은 것은 *"존 선택 재개 턴에서 소유 고정을 건너뛰는 설계가 W-10 이후에도 맞나"* 라는 **미결 설계 판단**이다(§4.1.6 v6 부기 · 사용자 판단 대상 · 95 소관) |

**이번 재개에서 하지 않은 것**: X-13·X-10·X-11·X-14·L-5는 선행(`plans/103` P2-1 `needs_plan` 코드 0건 · D-127 승인)이 여전히 불성립이라 **구현하지 않았다**. §4.1.5 사용자 결정 필요 항목은 **현행 동작을 그대로 두었다**(임의 변경 0). G-1~G-3·G-5·G-6·G-9 기본 가정도 그대로다.

#### 4.1.5 처분 (2026-09-21 사용자 권고 승인 — v6)

> 사용자 지시 *"권고에 맞게 진행하라."* — 구현 중 드러난 「사용자 결정 필요」 11건을 팀 리드 권고대로 처분했다.
> **새 D-번호는 부여하지 않았다** — 전부 D-224 ①~⑧(B는 D-225) 계약의 판정 경계를 좁히는 것이고 새 계약을 세우지 않는다.
> 정본 기록은 `docs/02_decision.md` D-224 「부기(2026-09-21)」 표다. **플래그 3종은 기본 off 그대로**이고 off 경로는 비트 동일이다.

| # | 항목 | 처분 | 랜딩 · 테스트 |
|---|---|---|---|
| **A** | 자산 매니페스트 런타임 공급 | **기록만** — `plans/104`가 itam 프로필을 만들 때까지 기다린다. `testdata/` 매니페스트를 설정 경로로 읽는 배선을 **추가하지 않는다**: 소비처(X-10 로컬 리그)가 X-13 차단으로 아직 없어 **쓰이지 않는 설정 손잡이만 는다**. 그때까지 자산 프로브가 "확인하지 못함"을 반환하는 것은 정상 동작이다 | 코드 0 |
| **B** | opt-in 실패 run 의 리포트 표기 | 최상단 **`[안내]` 1줄 + 제외(INVALID) 시나리오 건수**. 경고가 아니다 — 확정 단이 기준 단(3단)이라 측정값 자체는 유효하고, 알려야 할 것은 "그 프로파일은 한 건도 재지 않았다"는 사실이다. 기존 「기준 단이 아님」 경고와 조건·문구를 갈랐다 | `scripts/scenario/report.py` `optin_failure_profiles`·`optin_failure_excluded` · 테스트 3 |
| **C** | 직접 지정 DB의 소유 위반 | **교정하지 않는 현행 유지 + 사유 노트 1건**. 지정을 뒤집지 않되 침묵하지 않는다(자산 DB에도 사용률 컬럼이 있어 오답이 조용하다 — §0.2 R2 · 95 T5). 노트 문구에 *"교정하지 않고 그대로 조회했다"*가 드러난다. 검증 2지점(라우터 노드 · 분해 task) **대칭** 적용 · 소유 플래그 off면 노트 0 | `capability_ownership.REASON_OWNER_USER_SPECIFIED`·`user_specified_ownership_notes` · 테스트 5 |
| **D** | `capability` 없는 분해 task | **추가하지 않는다** — 검증 입력(답변 영역)이 없는데 `classify_dbs` 결과를 교정하면 질의 문자열 기반 판정으로 변질된다(D-004). X-11 실측에서 few-shot 누락률을 본 뒤 재검토 | 코드 0 |
| **E** | 브리지 행 제거 플래그 | `COMPOSITE_SCOPE_POSTCHECK_ENABLED`에 **묶지 않는다**(현행 유지) — 근거와 두 플래그의 상호작용은 §3.3 말미 | 코드 0 |
| **F** | 다른 존의 동명 호스트 | **`ambiguous`로 판정하고 결과에 넣지 않으며 사유를 매칭 보고에 남긴다**(D-224 ④ 모호 추측 금지). 엔터티 id 를 **충돌한 이름에 한해** DB로 한정해 같은 키가 엔터티 2개에 걸리게 했다 — 같은 DB 안의 `per_ip` 다중 행 묶음은 종전대로 한 엔터티다 | `key_bridge._entity_key`·`_cross_db_entities` · 노트 `cross_db_entities` · 테스트 3 |
| **G** | 코드값·심각도 컬럼 오판 | **출처 DB 매니페스트 선언 컬럼이 1순위**, 선언이 없을 때만 값·이름 휴리스틱. 출처는 행 `_source_db` → 없으면 선행 결과 `target_db_ids`. 프로브 쪽도 대칭 — 존 순회 행의 컬럼 강도를 매니페스트 선언이 정한다(투영은 `probe_hosts` 고정이라 SQL 무변경) | `entity_key.detect_key_columns(declared=)`·`KeyColumn.declared` · `key_bridge.declared_key_columns` · `entity_locator.zoned_key_columns` · 테스트 7 |
| **H** | D-099 결정적 컴파일 | 브리지 on + 브리지 스코프 있음 + 종전 스코프 None 이면 **컴파일을 건너뛰고** 브리지 스코프 블록이 실리는 일반 경로로 보낸다(스코프 없이 컴파일하면 LIMIT 절단으로 선행이 지목한 행이 빠진다). 사유는 로그 1줄 + 경과 노트 1건 | `query_generator._bridge_only_scope`·`_try_semantic` · `key_bridge.compile_skip_note` · 테스트 6 |
| **I** | `none_found` | **KEEP 유지**(HALT 아님) — 프로브는 고정 규칙(완전 일치·단축명·IP)이라 본 조회가 다른 표기로 찾을 여지가 있고 반대 증거도 없다 | 코드 0 |
| **J** | G-1 모호 소유 영역 | **레지스트리 선언으로 이동** — 최상위 `capabilities[].ambiguous_owner`(기본 false) 1개. 코드 상수 `OWNERSHIP_AMBIGUOUS_CAPABILITIES` 제거(호출부 grep 잔존 0). 소유 선언(누가 정본인가)과 경쟁하지 않아 두 번째 출처가 아니다(D-053). G-1 기본 가정(`server_spec` 정본=폴스타·모호)은 그대로 | `config/db_registry.yaml` · `CapabilitySpec.ambiguous_owner` · `registry.ambiguous_capabilities()` · 테스트 4 |
| **K** | 프로브 HALT 오판(G-3 연관) | 프로브 SQL은 `hostname`·`name`을 함께 보지만 **브리지 키는 `hostname`뿐**이다(G-3). 어느 컬럼으로 맞았는지를 판정에 실어, 다른 시스템 일치가 `name`뿐이면 **② HALT 대신 KEEP + 사유 노트**(신규 행 `owner_missing_weak_elsewhere`). `hostname` 일치가 하나라도 있으면 종전대로 ② HALT | `host_discovery.SystemProbe.weak_only`·`strong_hits()`·`ROW_WEAK_ELSEWHERE` · `entity_locator._Entities.strong` · 테스트 6 |

**검증(2026-09-21 · 과금 호출 0 · 실 DB·MCP 0 · 커밋 0)** — 신규 `tests/test_cross_system/test_plan102_recommendations.py` **34건**(권고마다 *바뀐 동작 + 반대 케이스*를 함께 고정: F는 정상 link·`per_ip`, K는 `hostname` 일치, G는 선언 부재, H는 플래그 off).
기존 테스트 **1건 갱신** — `test_ownership_router.py::test_excluded_entries_are_not_corrected` 의 "직접 지정" 파라미터를 **정본을 지정한 경우**로 바꿨다(C 처분으로 정본이 아닌 직접 지정에는 노트가 생기므로 종전 단언이 경계를 잘못 고정한다).

#### 4.1.6 위험 · 기존 결함 (기록만)

1단 부분 영향(X-14 잔여) · IP 비교에 `LOWER` 없음 · 폴스타 IP 단일값 완전 일치 · 게이트웨이 IP 조건도 프로브 발동 · 샌드박스 `resource_type` 필터 실측 필요 · 운영 on 전 D-203 시나리오 재측정 필요 · 존 선택 재개 턴에서 자산 task가 폴스타로 가는 문제(95 W-10 — **v6 실측(2026-09-21): 병행 세션이 W-10을 ✅ 완료로 기록했다**(95 v14 미커밋 — `_keep_zoneless_targets` · `db_scope.zone_selection_db_ids` · `subagents._make_isolated_input`의 `zone_selection_db_ids` 키 · task 고정 `subagents.py:1124`). **그러나 소유 고정 스킵은 그대로 남아 있다** — `src/orchestration/subagents.py:1101-1106`의 `task_owner` 블록이 여전히 `and not isolated.get("selected_db_ids")`로 게이트돼, 존 선택 재개 턴에서는 답변 영역 소유 고정이 아예 돌지 않는다(W-10 이 고친 것은 `raw_targets` 산출이고 이 게이트는 그 앞이다). **v6 부기(2026-09-21 · collectorinfra-36 지적으로 프레이밍 정정)**: 이 조건은 **빠뜨린 것이 아니라 의도적으로 넣은 것**이다 — 바로 위 `subagents.py:1098` 주석 *"이미 DB가 정해진 task(`db_ids`)·존 선택 재개 턴(`selected_db_ids`)은 건드리지 않는다"* 와 `:1095-1097`의 X-T13 근거(*"다중 존 시스템 소유 → 고정하지 않는다 … 고정하면 원문의 존 한정이 사라진다"*)가 그것이다. 따라서 질문은 "W-10 이 이 줄을 빠뜨렸나"가 아니라 **"존 선택 재개 턴에서 소유 고정·제한을 건너뛰는 설계가 W-10 이후에도 맞나"** 이고, 이는 구현 누락이 아니라 **미결 설계 판단**이다(사용자 판단 대상 · 구현 세션이 임의로 바꿀 사안 아님). 단, X-T13 근거는 **다중 존 시스템(폴스타)** 에 대한 것이고 **단일 DB 시스템 소유(자산 = itam)** 에는 그대로 적용되지 않는다 — W-10 요건(*존 선택과 무관하게 itam 유지*)과 정면으로 맞물리는 지점이 여기다. **95 소관 · 이번 범위에서 손대지 않았다**(같은 파일·인접 라인을 병행 세션이 편집 중 — 충돌 방지). 소유 플래그가 기본 off 라 현재 영향 0) · 기존 라우터 few-shot 9건에 `capabilities`가 없어 실 LLM이 필드를 빠뜨릴 수 있음(X-11 실측 대상) · `tests/test_semantic_routing/test_two_stage.py` 템플릿 키 계약에 슬롯 4개 증가(off 렌더는 빈 문자열 — 골든·`prompt_render_diff` 0으로 확인) · [기존 버그·미수정] `SEMANTIC_ROUTER_UNKNOWN_EXAMPLE`·`SEMANTIC_ROUTER_FAULT_DIAGNOSIS_SECTION`이 `.format()` 값으로 들어가 `{{ }}`가 LLM에 그대로 렌더됨 · **[잔여 · 범위 밖] opt-in 실패 사유 집합의 사본이 둘이다** — 정본 `src/observability/ladder.py:67` `OPTIN_FAILURE_REASONS` ↔ 사본 `scripts/scenario/server.py:43` `UNINTENDED_DEGRADATION`(D-053). 권고 B에서 리포트(`report.py`)는 **정본을 직접 보도록** 했고 둘의 동등성은 동기 테스트가 지킨다. 사본 자체를 지워 정본 하나로 만드는 것은 **러너 기동 경로의 의존이 바뀌므로 이번 범위 밖**이다(2026-09-21 팀 리드 판단).

---

## 5. 성공 기준

**판정 경로(v2)**: 1~9는 **사다리 3단 확정 기동**에서 판정한다(기동 로그 `tier=semantic_router` 첨부). 1단 동등성은 10, 기준 전환은 11.

1. **R1·R2 경계** — 라우팅 골든셋: 자산 단독 케이스는 itam만, 모니터링 단독 케이스는 itam **0회**(`forbid`). 시나리오 H-2의 `.37` 패턴이 모니터링 응답에 **0건**.
2. **R3·R4 양방향 체인** — 3단 로컬 리그에서 두 방향 모두 기대 호스트 집합(README 오라클)과 일치. 순차 표지 없는 질의도 라우터 `chain`으로 순차 러너에 진입하고(X-T9), 자산→폴스타가 `prior_no_identity`로 막히지 않는다.
3. **R5 값 판정** — 컬럼명과 무관하게(`sevrHostName`·`iPCtnt`·별칭) 키가 인정되고, 사용자가 준 IP로 체인이 이어진다. 숫자 ID 컬럼 오인 0.
4. **R6 소재 프로브** — 결정표 7행이 각각 재현되고, 미발견과 확인 실패가 응답에서 구분된다.
5. **침묵 0** — 매칭 보고 합계 = 선행 키 수 · 계획한 시스템은 결과 또는 사유 중 하나를 반드시 남긴다(X-T7 포함) · 빈 분류 폴백이 응답에 표기된다.
6. **비트 동일** — 플래그 3종 off에서 라우터 프롬프트 골든·`prompt_render_diff`·기존 D-203 테스트 전건 무변화 · **3단 그래프 노드 구성·분기 함수 무변화**(`entity_locator` 미등록).
7. **게이트** — `arch_check --ci`·`overfit_check --ci` 신규 위반 0. 공용 계층(`utils/`·`domain/`·`orchestration/`)과 독스트링에 자산 스키마 리터럴 0(D-179).
8. **계약 변이** — H-3 변이 **8/8** 검출(D1~D8), 각 변이는 자기 차원 센서만 실패.
9. **비용** — 소재 프로브는 시스템(폴스타는 인가 존)당 고정 조회 1회, LLM 추가 호출 0. 로컬 리그 기준 프로브 전체 지연 300ms 이내.
10. **부가 경로 동등성**(v2 · X-14) — 1단 확정 기동에서 H-2 시나리오 판정이 3단과 일치하고, 1단 전용 판정 로직이 없다(같은 함수 호출 grep).
11. **기준 전환**(v2 · 트랙 L) — 설정 미입력 + 멀티 DB에서 기동 로그 `tier=semantic_router` · 1단 opt-in은 WARNING이 아니라 INFO · `docs/21`·`CLAUDE.md` 사다리 표가 기동 로그와 일치 · 운영 전환(L-5) 후 기존 폴스타 골드셋 회귀 0.

---

## 6. 위험 · 비범위

**위험**

| 위험 | 완화 |
|---|---|
| 소유 검증이 키워드 사전으로 변질(D-004) | 검증 입력을 **LLM 구조화 출력의 답변 영역**으로 한정. 질의 원문 문자열 매칭 코드 금지를 리뷰 체크 항목으로 |
| 값 기반 판정이 폴스타 단독 체인을 바꿈 | 플래그 off 비트 동일 · on 전 D-203 시나리오(plans/88 실 검증 11건 계열) 재측정 |
| FQDN 단축명 충돌(다른 도메인의 같은 단축명) | 단축명 일치는 `possible` 등급으로만 · 한 키가 서로 다른 엔터티 2개에 걸리면 `ambiguous` 미포함 |
| IP 재사용·변경(자산 DB는 연계 스냅샷 — 95 G-11) | IP는 2순위 키(P4) · 매칭 보고에 키 타입 표기 |
| 담당자 성명 노출(R1 `asset_owner`) | **95 G-9(PII·마스킹) 확정 전 `asset_owner` 영역은 비활성**으로 둔다 |
| 프로브 권한 누출 — 권한 밖 존·시스템 순회 자체가 정보 | `host_sweep` 규약 1(인가 범위만) 그대로 · 자산관리 가시성은 95 G-7(RBAC) 답에 종속 |
| 프롬프트 증분으로 토큰·지연 증가 | 소유표·예시는 기동 시 1회 렌더(접두 고정) · 증분은 `prompt_render_diff`로 크기 보고 |
| 로컬 샌드박스 통과 ≠ 운영 정합 | 95 §4.6.2 — 키 대응의 운영 근거는 95 G-4(식별자)·G-6(키 정합 샘플)로만 |
| 존 미배정 DB 탈락(X-T7) | 95 W-9 결정 전에는 교차 계획에서 **자산 결과 부재를 D6 센서로 사유화** |
| ~~(v2) HITL 진입 완화(G-8 (b))가 승인 우회로 변질~~ **v3: 해당 없음 — 구조 승인 자체가 질의 경로에서 제거됨(`plans/104`)** | 진입 허용은 **대상 DB 전부가 수동 프로필 또는 구조 캐시를 가져 승인 요청이 발생할 수 없을 때**로만 — 결정적 판정, 하나라도 없으면 현행대로 미진입 + 사유 노트 |
| (v2) 운영 전환이 1·2단 전용 기능을 퇴행시킴(X-T12) | L-5를 `plans/103` P5 뒤로(v3) · 전환 전 실시간 프로세스·단건 점검 질의를 3단으로 재측정 |
| (v2) 3단 이중 분류 사이의 경계 누수(X-T10) | 소유 센서 2지점 · 변이 스위트에 지점별 변이 포함 |
| (v2) 병행 세션이 사다리 소비처를 편집 중 | L-3 착수 전 `git status`·`ListAgents` 확인(`scripts/scenario/preflight.py`는 2026-09-17 미커밋 수정 중) |

**비범위**
- ~~사다리 3단·4단의 교차 체인(G-7 · 2차 후보).~~ **v2: 3단이 기준 경로로 바뀌어 범위 안이다.** 4단 `legacy`만 비범위.
- (v2) **3단 기능 동등성 전반** — `process_query`·`host_inspect`의 3단 도달 경로. 이 계획은 R6에 필요한 소재 판정만 3단에 배선한다. **v3: `plans/103`이 담당.**
- (v3) 구조 분석·구조 승인 — `plans/104`.
- (v2) deepagents를 3단 라우터의 요청 단위 분기 대상으로 편입하는 설계(G-9 (b)).
- 학습형/LLM 엔터티 매칭의 런타임 사용(§2 미채택). 오프라인 별칭 후보 제안은 2차 검토.
- ITSM·클라우드 포탈로의 확장(같은 계약이 재사용되지만 이 계획의 검증 대상은 폴스타·자산관리 2개).
- 운영 자산 DB 연결(95 G-2·G-3) · 자산 프로필 정본(95 W-6 · G-4) · `ACTIVE_DB_IDS` 활성화(95 W-12).
- 시리얼 번호 등 제3의 키(자산 DB에는 있으나 폴스타 대응 컬럼 미확인 — 필요 시 매니페스트 행 추가로 확장).

---

## 7. 사용자 확정 게이트

| # | 질문 | 왜 막히는가 | 기본 가정(답 없으면 이걸로 진행) |
|---|---|---|---|
| **G-1** | **서버 사양**(CPU 코어·메모리·디스크 용량)은 어느 시스템이 정본인가? "하드웨어 자산 정보"(자산관리)와 "서버 현황"(폴스타)이 겹친다 | 소유표 `server_spec` · 결정표 "소유 모호" 행 | **폴스타**(현재 구성 실측값). "자산 대장상 사양"처럼 자산을 명시하면 자산관리 |
| **G-2** | 매칭 등급 — FQDN 단축명 일치(`svr-web-01.corp` ↔ `svr-web-01`)를 결과에 포함하는가? 키 우선순위는 호스트명 → IP인가? | §3.3-④ `possible` 처리 | 호스트명 → IP · 단축명 일치는 **포함 + "가능한 일치" 표기** |
| **G-3** | 폴스타의 `name`(서버명)과 `hostname`이 다를 때 자산 `sevrHostName`은 어느 쪽과 대응하는가?(D-061 · 95 G-6과 같은 실측으로 답해진다) | 폴스타 매니페스트 키 목록 | `hostname`만 브리지 키 |
| **G-4** | 레지스트리 표현 — 자산관리를 `solutions`에 등재(능력 축 단일화 · 95 W-9 결정과 한 몸)할지, DB 항목에 `capabilities`를 신설할지 | X-7 형태 | **`solutions` 등재**, 단 95 W-9 결정 전에는 DB 항목 임시 선언 · **부기(2026-09-17 · 95 v9)**: 사용자 사실 *"자산관리는 존이 없고 1개의 시스템에서 모든 자산을 관리한다"* → **가상 존(`zone_groups`) 등재 기각**. 95 W-9는 실행기 한정 잔여 그룹(`multi_db_executor._with_unzoned_group`)으로 해소돼 이 게이트와의 결합이 풀렸다 — 남은 질문은 능력 표현 형태뿐 · **✅ 사용자 확정(2026-09-17 · v4)**: *"자산관리는 존 개념이 없이 1개의 시스템이다. 이에 맞게 정의하라."* → itam은 **DB 항목 수준 `capabilities`**로 선언하고 `solutions`·`zone_groups`에 등재하지 않는다(D-214 ④ 기각 대안과 비충돌 · 기본 가정 `solutions` 등재는 D-214 ④와 충돌해 이탈안으로 올렸고 사용자가 이 방향을 확정). 소유표·프로브·매니페스트·응답 표기 어디에도 itam에 존 의미 없음 — 프로브는 "존 없음 · 시스템 1회 조회" |
| **G-5** | 소재 프로브 발동 — 식별자 질의마다 **항상 양쪽**을 확인할지, **소유 시스템 우선 · 미발견/양쪽 필요/소유 모호일 때만** 확인할지 | 비용·지연 대 안내 품질 | **후자**(§3.4 발동 조건) |
| **G-6** | 불일치 노출 수준 — 미발견 키 샘플을 몇 개 보일지, "다른 시스템에만 있음" 안내를 넣을지 | 응답 길이·PII | 건수 + 샘플 10 · 안내 포함 |
| **G-7** | ~~대상 경로 — 운영 1단(`deep_agent`)·2단 우선, 3단·레거시는 2차로 미루는가?~~ | 배선 범위 | **확정(2026-09-17 사용자 기준 · v2)** — v1 기본 가정을 **뒤집었다**: 3단 `semantic_router` 기준 · 1단은 부가(X-14) · 2단 배선 기본 off · 4단 비범위 |
| **G-8**(v2) | **3단 순차 러너의 HITL 조건** — 구조 승인(`ENABLE_STRUCTURE_APPROVAL`, 코드 기본 on · 운영 미설정)이 켜져 있으면 순차 러너는 진입하지 않는다(X-T8). 교차 체인을 어떻게 여는가? (a) 운영에서 구조 승인 off (b) 진입 금지를 **"승인 요청이 실제로 발생할 수 있을 때"로 좁힌다** — 대상 DB 전부가 수동 프로필·구조 캐시를 가지면 진입(승인 요청은 둘 다 없는 DB에서만 난다 — `schema_analyzer.py:1124-1150`) (c) 현행 유지(3단에서 R3·R4 불가) | X-13 · R3·R4 | ~~(b)~~ **해소(v3 · 2026-09-17 사용자 지시)** — 구조 승인을 질의 경로 HITL에서 **관리자 페이지 기능**(MCP 연결 DB 목록 · 스키마 변경·신규 내용 점검 · 구조 분석 초안·승인)으로 옮긴다 → **`plans/104`**. 질의 경로에 구조 승인이 없으므로 이 질문 자체가 사라진다 · **2026-09-17 부기: 104 구현 완료(D-227)** |
| **G-9**(v2) | **deepagents "부가 사용"의 형태** — (a) 프로세스 단위 opt-in: 현행 빌드 타임 배타 유지(켜면 그 프로세스 전체가 1단) (b) 3단 라우터의 **분기 대상 하나**로 편입해 요청 단위로 위임(옵트인 `fault_diagnosis` 노드 선례) | X-14 범위 · D-225 ② | **(a)** — 부가 사용처가 정해지면 (b)를 별도 계획으로 |
| **G-10**(v2) | **3단 미도달 기능**(`process_query` 실시간 프로세스 · `host_inspect` 단건 점검 — X-T12)을 어디서 다루고, 운영 전환(L-5)을 그 뒤로 미룰 것인가? "모든 동작은 시멘틱 라우터에서"의 나머지 범위다 | L-5 순서 · 기능 퇴행 | **확정(v3 · 2026-09-17 사용자 지시)** — *"langgraph 기능을 이용하면 1단의 모든 기능을 구현할 수 있다 … 모두 구현하는 방향으로"* → **`plans/103`**(3단에 1·2단 전 기능 구현) · L-5는 103 P5 뒤 · 이 계획은 R6 소재 판정만 3단에 배선 |

**95번 계획 게이트 중 이 계획의 전제**: 95 **G-4**(물리 식별자 — 자산 매니페스트) · **G-6**(키 정합 샘플) · **G-7**(자산 RBAC) · **G-9**(담당자 PII) · **W-9**(존 미배정 DB 처분).

---

## 8. `plans/95`와의 관계

| 95 항목 | 이 계획에서 |
|---|---|
| 트랙 C(라우팅 경계 · W-8 부분 랜딩) | **트랙 R로 승계** — 설명문 경계(W-8)는 유지하고, 그 위에 소유표·소유 센서를 얹는다 |
| 트랙 E(교차 질의 · G-6) | **트랙 K·P로 구체화** — "키 병합"을 매니페스트·값 판정·매칭 등급 계약으로 |
| G-8(T5 경계) | 2026-09-17 사용자 요구가 **방향 (가)를 확인**했다(모니터링=폴스타 · 자산·계약·담당자=자산관리). 남은 겹침은 **서버 사양 하나** → 이 계획 **G-1**로 이관 |
| W-9/W-10(T2·T3) | 교차 계획의 자산 결과 탈락(X-T7)과 혼합 질의 HITL — 95 결정 대기 유지, 이 계획은 D6 센서로 침묵만 막는다 · **부기(2026-09-17 · 95 v9)**: W-9 해소(실행기 잔여 그룹 · D-214 ④). W-10은 X-7 이후로 보류하되 **요건 확정** — *존 역질문이 발동하더라도 itam은 존 선택과 무관하게 대상에 남아야 한다*(재개 턴 `selected_db_ids` 고정 — `src/routing/semantic_router.py:136-158` — 에서 itam 탈락 금지) |
| 트랙 S(로컬 샌드박스) | H-2·H-4의 무대 — 호스트 키 4형·`.37` 판별값을 **센서 오라클로 재사용** |
| W-4(연결) | §1.4 — 로컬 샌드박스 대상 `mcp_server/.env` 설정·9099 재기동 완료(2026-09-17) |
| W-12(활성화 순서 불변식) | 그대로 — 이 계획의 운영 활성화도 그 뒤. (v2) **L-1(tri-state 2단 자동 off)은 W-12보다 먼저**(X-T11) |
| §0.2 ⑥ · W-12 verify · §6-2("1단 `deep_agent`에서 판정") | (v2) **3단 `semantic_router` 판정으로 변경** — 95 v7에서 반영(트랙 L-6) |

---

## 9. 신규 결정 예약 — D-224 · D-225

### 9.1 D-224

**제목(예정)**: 폴스타 ↔ 자산관리 교차 시스템 질의 계약 — 답변 영역 소유 · 값 기반 키 판정 · 키 브리지 매니페스트 · 식별자 소재 프로브

**담을 내용**:
1. **답변 영역 소유** — 정본 시스템은 필요한 정보 종류(capability)로 정하고 레지스트리 데이터로 선언한다. LLM은 답변 영역을, 코드는 정본 시스템을 판정한다.
   소유 검증은 LLM 구조화 출력만 입력으로 받는다(D-004 유지 — 질의 원문 키워드 분류 금지).
2. **값 기반 식별 키 판정** — 서버 키는 컬럼명이 아니라 값(호스트명·FQDN·IPv4·IPv6)으로 판정하고 RFC 4343(대소문자 비구분)·RFC 5952(IPv6 정규 표기)로 정규화한다.
   컬럼명 판정(D-100)은 보조 신호로 남긴다. 근거 실측: 자산 DB `sevrHostName`·`iPCtnt`가 이름 판정에 걸리지 않아 자산→폴스타 체인이 `prior_no_identity`로 차단되고, IP는 스코프 키 후보가 아니었다.
3. **키 브리지 매니페스트** — 시스템 간 키 대응은 공용 코드가 아니라 DB별 프로필 `entity_keys`(대상 컬럼·우선순위·다중값·행 다중도)에 둔다.
   양방향이 같은 계약이며, 대상 컬럼은 코드가 확정한다(LLM 대응 위임 제거).
4. **매칭 등급 노출** — `link`/`possible`/`non_link`/`ambiguous`로 판정하고 경과 노트로 노출한다. 모호 일치는 결과에 넣지 않는다(추측 금지).
5. **식별자 소재 프로브** — `host_discovery` 판정을 (시스템, 존) 축으로 일반화하고 "미발견"과 "확인 못 함"을 분리한다. 발동은 조건부.
6. **하네스 계약** — 계약 차원마다 계산적 센서 하나(D1~D8), 변이 스위트로 소유 센서만 실패함을 고정한다. 플래그 3종 기본 off(비트 동일). **(v2) 계약은 사다리 3단이 소유·판정하고, 1단은 같은 함수를 부르는 부가 경로다.** 3단 교차 체인 진입은 원문 표지가 아니라 라우터 구조화 출력 `chain`으로 판정한다.

### 9.2 D-225 (v2)

**제목(예정)**: 실행 경로 기준 전환 — 사다리 3단 `semantic_router` 기준 · `deep_agent` 부가 경로 · 2단 배선 기본 off (D-037·D-161·D-162·D-221 ⑤ 개정)

**근거(사용자 지시 원문, 2026-09-17)**: *"기본은 시멘틱 라우터를 사용한다. 모든 동작은 시멘틱 라우터에서 동작되어야 한다. deepagents는 부가적으로 사용할 예정이라. 기본 동작은 시멘틱 라우팅을 통해 진행되어야 한다."*

**담을 내용**:
1. **기준 경로 = 사다리 3단 `semantic_router`.** 신규 기능의 수용 기준은 3단 확정 기동에서 판정한다. `docs/21`의 "1 정본 + 3 폴백" 서술과 `ladder.py` 정본 판정을 3단 기준으로 바꾼다.
2. **`deep_agent`(1단)는 부가 경로** — 폐기 대상이 아니다. D-161 ①(승격-폐기 동반)의 **명시 예외**: 사용자가 부가 사용 예정을 밝혔다. 1단 확정은 강등 경고가 아니라 opt-in 기록. 부가 사용 형태는 G-9.
3. **2단 `intent_orchestration` 배선은 기본 off**, 모듈은 유지 — 3단 순차 러너가 부품을 함수로 재사용한다(`docs/21` §7 모듈 의존 방향 불변).
4. **코드 기본값으로 고정** — tri-state 미입력이 2단으로 자동 확정되지 않게 한다(X-T11). 운영 `.env` 전환(L-5)은 3단 기능 동등성(`plans/103` · D-226) 완료 뒤.
5. **정본 전제 소비처 재정의** — 시나리오 사전 점검·`eval_text2sql` 경로 표기·D-221 ⑤ 리포트 경고의 기준 단을 3단으로.
6. **기능 배치 원칙** — 새 판정·조립 로직은 3단이 부르는 공통 함수에 두고, 1단은 같은 함수를 호출한다(1단 전용 판정 로직 신설 금지).

등재 시 `docs/02_decision.md`의 `## D-` 헤더·「변경 이력」·「채번 이력」 표를 재확인하고 최댓값+1을 재부여한다(예약 소진 가능 — D-224·D-225 모두).

---

## 10. 참고 문헌

> **서지 검증(2026-09-17)**: 게재처 페이지·원문 PDF·arXiv로 확인했다. **OpenAlex가 요청 한도 초과(HTTP 429)**라 인용수는 적지 않았다
> (적더라도 레코드 단위 하한값일 뿐이다 — `plans/78` §11 주의). 문헌 선정은 **동료심사 여부와 이 계획과의 논리적 적합성**으로 했다.

### 10.1 동료심사 문헌

| 문헌 | 게재 | 식별자 | 이 계획 반영 |
|---|---|---|---|
| Wang et al. **DBCopilot: Natural Language Querying over Massive Databases via Schema Routing** | EDBT 2025 | DOI `10.48786/edbt.2025.57` (원문 PDF 확인) · arXiv 2312.03463 | **P1** — 대상 DB·테이블 선택(스키마 라우팅)을 SQL 생성과 분리한 결정 단계로 |
| Chen, Gu, Cao, Fan, Madden, Tang. **Symphony: Towards Natural Language Query Answering over Multi-modal Data Lakes** | CIDR 2023 | [cidrdb.org/cidr2023/papers/p51-chen.pdf](https://www.cidrdb.org/cidr2023/papers/p51-chen.pdf) | **P2** — 소스 발견 → 하위 질의 분해 → 소스별 평가 → 결합 |
| Urban, Binnig. **CAESURA: Language Models as Multi-Modal Query Planners** | CIDR 2024 (+ 데모 SIGMOD Companion 2024, DOI `10.1145/3626246.3654732`) | [cidrdb.org/cidr2024/papers/p14-urban.pdf](https://www.cidrdb.org/cidr2024/papers/p14-urban.pdf) | **P2** — LLM이 계획, 실행은 연산자(결정적) |
| Biswal et al. **Text2SQL is Not Enough: Unifying AI and Databases with TAG** | CIDR 2025 | arXiv 2408.14717 | **P2** — 단일 Text2SQL로 안 되는 질의를 조회·합성 파이프라인으로 |
| Kim et al. **An LLM Compiler for Parallel Function Calling** | ICML 2024 (PMLR v235) | arXiv 2312.04511 | **P2** — 의존 DAG 계획(`input_from` 재사용 근거) |
| Zhou et al. **Least-to-Most Prompting Enables Complex Reasoning in Large Language Models** | ICLR 2023 | OpenReview `WZH7099tgfM` · arXiv 2205.10625 | **P2** — 앞 하위 문제의 답이 뒤 하위 문제의 입력 |
| Khot et al. **Decomposed Prompting: A Modular Approach for Solving Complex Tasks** | ICLR 2023 | arXiv 2210.02406 | **P2** — 분해기 + 하위 작업 처리기 라이브러리(서브에이전트 구조와 동형) |
| Fellegi, Sunter. **A Theory for Record Linkage** | JASA 64(328), 1969 | DOI `10.1080/01621459.1969.10501049` | **P3** — link / possible link / non-link 3분 결정 |
| Konda et al. **Magellan: Toward Building Entity Matching Management Systems** | PVLDB 9(12), 2016 | DOI `10.14778/2994509.2994535` | **P3** — 매칭 알고리즘보다 단계별 절차·도구가 실무를 좌우 → 매칭 보고·운영 확인 절차 |
| Hulsebos et al. **Sherlock: A Deep Learning Approach to Semantic Data Type Detection** | KDD 2019 | DOI `10.1145/3292500.3330993` | **P5** — 컬럼 의미 타입을 값 특징으로 판정(사전·정규식 기준선도 비교 대상에 포함된 연구) |
| Zhang et al. **Sato: Contextual Semantic Type Detection in Tables** | PVLDB 13(11), 2020 | DOI `10.14778/3407790.3407793` | **P5** — 값 신호 + 테이블 문맥 결합 → 값 타입 + 컬럼명 힌트 결합 |
| Zhu, Deng, Nargesian, Miller. **JOSIE: Overlap Set Similarity Search for Finding Joinable Tables in Data Lakes** | SIGMOD 2019 | DOI `10.1145/3299869.3300065` | **P6** — 조인 가능성 = 값 집합 교집합 → 커버리지 지표 |
| Jeong et al. **Adaptive-RAG: Learning to Adapt Retrieval-Augmented Large Language Models through Question Complexity** | NAACL 2024 | [ACL Anthology 2024.naacl-long.389](https://aclanthology.org/2024.naacl-long.389/) | 프로브 조건부 발동(§2 미채택 표 · G-5) — 충분한 최소 비용 경로 |
| Lei et al. **Spider 2.0: Evaluating Language Models on Real-World Enterprise Text-to-SQL Workflows** | ICLR 2025 (Oral) | [ICLR 2025 proceedings](https://proceedings.iclr.cc/paper_files/paper/2025/file/46c10f6c8ea5aa6f267bcdabcb123f97-Paper-Conference.pdf) | 배경 — 다중 방언·다중 질의 엔터프라이즈 워크플로에서 최고 에이전트도 21.3% → 키 대응·조립을 LLM에 맡기지 않는 근거 |
| Wiederhold. **Mediators in the Architecture of Future Information Systems** | IEEE Computer 25(3):38–49, 1992 | DOI `10.1109/2.121508` | 배경 — 소스별 지식을 가진 중개 계층 → DB별 매니페스트(§3.3) |
| Li, Li, Suhara, Doan, Tan. **Deep Entity Matching with Pre-Trained Language Models (Ditto)** | PVLDB 14(1), 2020 | DOI `10.14778/3421424.3421431` | **미채택 대조군** — 학습형 매칭(§2) |
| Peeters, Steiner, Bizer. **Entity Matching using Large Language Models** | EDBT 2025 | arXiv 2310.11244 | **미채택 대조군** — LLM 매칭(§2) · 오프라인 별칭 후보 제안 2차 검토 근거 |
| Doan, Halevy, Ives. **Principles of Data Integration** | Morgan Kaufmann, 2012(교재) | ISBN 978-0-12-416044-6 | 배경 — 스키마 매핑·문자열 매칭 |

### 10.2 Preprint (보조 근거 — 동료심사 미완)

| 문헌 | 식별자 | 이 계획 반영 |
|---|---|---|
| Ahn, Kim. **From Prompts to Contracts: Harness Engineering for Auditable Enterprise LLM Agents** (2026-07-09) | arXiv 2607.08028 | **P9 · H-3** — 엔터티 라우팅을 프롬프트가 아니라 코드·매니페스트가 소유하고, 고정 시나리오 30건 + 계약 차원 하나씩 깨뜨린 변이 7건(7/7 검출, 각 변이는 자기 차원 검증기만 실패)으로 검증 |
| Xu et al. **ReWOO: Decoupling Reasoning from Observations for Efficient Augmented Language Models** (2023) | arXiv 2305.18323 | 보조 — 계획(LLM)과 도구 실행(비모수 워커) 분리 → 프로브·조회는 LLM 없이 |

### 10.3 표준 · 산업 자료

| 자료 | 이 계획 반영 |
|---|---|
| **RFC 4343** — DNS Case Insensitivity Clarification (IETF, 2006) | **P7** — 호스트명 비교 casefold |
| **RFC 5952** — A Recommendation for IPv6 Address Text Representation (IETF, 2010) | **P7** — IPv6 소문자·최장 0 구간 압축 정규형 |
| **Anthropic, *Building effective agents*** (2024-12-19) | **P10**(v2 — 워크플로는 정의가 분명한 작업에 예측 가능성·일관성, 에이전트는 유연성이 필요할 때 · 가장 단순한 해법부터. 2026-09-17 원문 재확인) · **P1** — 라우팅은 범주가 뚜렷하고 분류가 정확할 때 · prompt chaining의 프로그램 게이트 · 도구 설계(ACI · poka-yoke) → `locate_entities` 도구 설계 |
| **B. Böckeler, *Harness engineering for coding agent users*** (martinfowler.com, 2026-04-02) | **P8** — 가이드(피드포워드)·센서(피드백), 계산적·추론적 통제, *"behaviour harness"*가 가장 미성숙 |
| **OpenAI, *Harness engineering: leveraging Codex in an agent-first world*** (2026-02) | 보조 — 환경·제약을 기계적으로 강제하는 하네스. **원문은 HTTP 403으로 직접 열람하지 못했고 2차 요약으로만 확인** — 설계 근거로 쓰지 않고 용어 출처로만 인용 |
| **ServiceNow CMDB Identification and Reconciliation Engine(IRE)** — 공식 문서(개요 수준) + 커뮤니티 기사(A. Ritolia, 2026-04-07) | **P4** — 식별 규칙 우선순위 · 여러 CI가 일치하면 추측하지 않고 식별 실패·"Ambiguous match" 기록 · IP는 변할 수 있는 속성. **세부 규칙은 공식 문서에서 확인되지 않아 커뮤니티 기사 기준** |

### 10.4 내부 참조

`plans/95`(§3 스키마 · §4.6 샌드박스 · G-4/G-6/G-7/G-8/G-9 · W-9) · `plans/88`(D-203 순차 의존 계약) · `plans/82` Wave 5(존 순회 소재 탐색 · D-176 후속3) ·
`plans/79`·`plans/80`(라우팅 골든셋 S-1) · `plans/94`(시나리오 하네스) · `docs/21_orchestration_ladder.md`.

### 10.5 조사에서 확인한 공백

- **자연어 질의 에이전트에서 자산 원장(CMDB)과 모니터링 시스템의 식별자를 맞추는 문제를 다룬 동료심사 문헌을 찾지 못했다.** 검색 결과는 벤더 문서·특허·업계 블로그였다.
  → 이 공백이 **보수적 설계**(규칙 기반 결정 · 모호 일치 추측 금지 · 매칭 등급 노출)의 근거다. 성능 수치의 외부 기준선이 없으므로 합격선은 로컬 오라클(§3.6 H-2)로 정한다.
- **"하네스 엔지니어링"은 2026년 산업 용어**로, 동료심사 문헌이 거의 없고 preprint·실무 기고가 대부분이다. 원칙(P8·P9)은 채택하되 **정량 주장은 인용하지 않는다.**
- **엔터티 라우팅 계약 + 변이 검증(P9)**의 근거는 현재 preprint 1편뿐이다 — 방법(한 차원씩 깨뜨리기)은 표준적인 변이 테스트라 채택 위험이 낮다고 판단했다.

---

## 11. 변경 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| v1 | 2026-09-17 | 최초 작성(사용자 지시). 요구 R1~R6 · 실측(시스템 선택 LLM 1회·검증 없음·빈 분류 침묵 폴백 · 능력 축 운영 소비처 0 · ★식별 키 컬럼명 판정으로 자산 DB `sevrHostName`·`iPCtnt` 불인정 → 자산→폴스타 체인 `prior_no_identity` 차단 실행 확인 · IP 스코프 키 부재 · 소재 탐색은 폴스타 존 축·호스트명 완전 일치만) · 함정 X-T1~T7 · 문헌 원칙 P1~P9(동료심사 18 · preprint 2 · 표준·산업 6 · 공백 3) · 설계(답변 영역 소유표 · 값 기반 키 판정 · 키 브리지 매니페스트·매칭 등급 · 소재 프로브 결정표 · 1·2단 배선 · 하네스 D1~D7) · WU X-0~X-12 · 게이트 G-1~G-7 · D-224 예약. **mcp_server 연결 완료**(§1.4 — 로컬 MariaDB 샌드박스 · 9099 재기동 · 실연결 검증) |
| v2 | 2026-09-17 | **기준 실행 경로를 사다리 3단 `semantic_router`로 재정렬**(사용자 지시 *"기본은 시멘틱 라우터를 사용한다 … deepagents는 부가적으로 사용할 예정"*). 3단 경로 실측 §1.6 신설(★순차 러너는 구조 승인 HITL 기본 on이라 운영 설정으로 진입하지 않음 · 진입이 원문 표지 문자열 의존 · 라우터 노드+순차 러너 이중 분류 · `host_discovery`·`process_query`·`host_inspect` 3단 미도달 · 사다리 정본 판정이 1단 고정) · 함정 X-T8~X-T13 · 원칙 P10(워크플로 우선 — Anthropic 원문 재확인) · §3.1 소유 검증 2지점·`chain` 필드·단일 DB 시스템만 `db_ids` 고정 · §3.4 3단 노드 `entity_locator` · §3.5 3단 기준 배선표 재작성 · §3.6 D8 체인 진입 · §3.7 트랙 L(기준 전환 L-1~L-6) · WU X-13(3단 체인 진입)·X-14(1단 부가 동등성) · 성공 기준 판정 경로 명시·10·11 · 게이트 G-7 확정(v1 기본 가정 뒤집음)·G-8~G-10 신설 · **D-225 예약** · `plans/95` v7 검증 기준 1단→3단 |
| v3 | 2026-09-17 | **G-10·G-8을 새 계획으로 확정·해소**(사용자 지시 *"3단 기능도 langgraph 기능을 이용하면 1단의 모든 기능을 구현할 수 있다 … 구조 승인 기능은 admin 페이지에 … 추가하라"*). G-10 → `plans/103`(LangGraph 네이티브 계획·`Send` 팬아웃·재계획 루프·태스크 서브그래프 · D-226 예약) · G-8 → `plans/104`(구조 승인 HITL을 관리자 페이지로 · D-227 예약). X-T8·X-T9·X-T12 해소 경로 표기 · §3.5 호출 자리가 103 `plan`·`dispatch`·`join`으로 옮겨 감(함수 동일) · X-13 진입을 103 `plan` 노드로 이관 · L-5 선행을 103 P5로 · 위험·비범위 갱신 |
| v4 | 2026-09-17 | **1차 구현 후 사용자 지시로 중지**(구현 지시 *"102번 계획을 구현하라."* · 중지 지시 *"현재 작업 중인 내용을 계획파일에 업데이트하고 우선 현재까지 마무리하고 작업을 중지하라."*). 구현: X-1~X-9(값 기반 키 판정·매니페스트·키 브리지·3단 배선 · 시스템 축 프로브·`entity_locator` · 답변 영역 소유·프롬프트·골든셋) · L-1~L-3(2단 미입력 off · 기준 단 3단·사유 어휘 재정의 · 소비처) · L-4·X-12 문서 일부. 플래그 3종 기본 off 비트 동일(골든·`prompt_render_diff`·노드 목록·D-203 테스트). 차단 유지: X-10·X-11·X-13(103 P2-1)·X-14·L-5. **G-4 사용자 확정**(DB 항목 `capabilities` — 기본 가정 `solutions` 등재가 D-214 ④와 충돌해 이탈안 보고 → 확정). `asset_owner` 비활성 완화 불필요 재판정(95 G-9). 구현 현황·기본 가정·검증·재개 체크리스트·사용자 결정 필요 §4.1 신설. 파일명 `-TODO` → `-WIP`. **v4 보강(main 세션 · 사용자 지시 *"여기서 멈추고 관련 계획파일에 추가 진행해야될 작업을 업데이트하라."*)**: §3.1 G-4 옛 권고(`solutions` 등재)에 폐기 표기 · §4.1.3 중지 후 재확인 795 passed·게이트 exit 0 · §4.1.4-1 멀티 DB 노트 덮어쓰기를 정적 확인(단일 경로 `schema_analyzer`는 병합, 멀티 경로는 미병합 — 수동 프로필 없는 DB가 끼면 사실상 매번 발동) · -5 `CLAUDE.md` 반영 문안 수록(팀 리드 최종 보고에서 옮김) · -7 관련 계획 후속(`plans/95` W-10 선행 충족 · `plans/103` P2-1 `chain` 합류) 추가 · 코드 변경 0 |
| v5 | 2026-09-21 | **재개(사용자 지시 *"102번 계획을 구현하라."* — 팀 리드 경유) · v4 체크리스트 1~7 처리.** ①멀티 DB 경로 `dependency_notes` 덮어쓰기 **수정**(재현 테스트 먼저 → state 노트 병합 · 단일 경로와 같은 `add_db_note` 규칙 · 반환 shape 유지) ②**전체 스위트 실행**(HEAD `284137a` 기준선 worktree 대조로 실패 전건을 기존 실패로 확정) ③**D-224**(부분 확정)·**D-225**(확정) 본문 등재 + D-221 ⑤ 부기·D-222 부기 2 ② 개정 표기 ④`docs/18` 5건 ⑤`CLAUDE.md` 사다리 절 + 옛 "1단 정본"·`flag_off` 서술 정리(`plans/94`·`96`·`99` 포함 · `plans/99` preflight 예시를 실제 출력으로 교정) ⑥`arch_check` WARN +2 = **`key_bridge` 현 위치 유지**(근거 4항) ⑦`plans/95` W-10은 **병행 세션이 구현 중**임을 실측하고 손대지 않음. **차단 유지**: X-10·X-11·X-13·X-14·L-5. 사용자 결정 필요 항목·기본 가정 **변경 0** · 커밋 0 · `.env` 무변경 · 과금 호출 0 |
| v6 | 2026-09-21 | **§4.1.5 「사용자 결정 필요」 11건 처분**(사용자 지시 *"권고에 맞게 진행하라."* — 팀 리드 권고 승인 · 팀 리드 경유). §4.1.5를 **처분 표**로 교체하고 §3.3에 E·F·G 근거 문단, §3.4 결정표에 K 행(②′)을 더했다. **코드 7건**: B 리포트 최상단 opt-in 실패 `[안내]`+제외 건수(`scripts/scenario/report.py`) · C 직접 지정이 정본이 아니면 교정 없이 사유 노트(검증 2지점 대칭) · F 다른 DB(존)의 동명 호스트는 `ambiguous`(엔터티 id를 충돌 이름에 한해 DB로 한정 · `per_ip` 불변) · G 출처 DB 매니페스트 선언 컬럼이 1순위(브리지·프로브 대칭 · `detect_key_columns(declared=)`) · H 브리지 스코프만 있으면 D-099 결정적 컴파일 건너뛰고 일반 경로(사유 로그·노트) · J G-1 모호 영역을 `config/db_registry.yaml` `capabilities[].ambiguous_owner`로 이동(코드 상수 제거·잔존 0) · K 다른 시스템 일치가 `name`뿐이면 ② HALT 대신 KEEP(`owner_missing_weak_elsewhere`). **기록만 4건**: A 자산 매니페스트 런타임 공급은 104 대기 · D `capability` 없는 task 교정 추가 안 함(D-004) · E 브리지 행 제거를 `COMPOSITE_SCOPE_POSTCHECK_ENABLED`에 묶지 않음 · I `none_found` KEEP 유지. **검증**: 신규 단위 34건(권고마다 반대 케이스 동반) · 관련 묶음 2,182 passed · 게이트 `arch_check`·`overfit_check`(기준선 무변경)·`prompt_render_diff`·`catalog_diff` 전부 exit 0 · ruff 신규 0 · mypy 신규 0. 플래그 3종 기본 off·`.env` 무변경·커밋 0. **95 W-10 확인**: 병행 세션이 ✅ 완료로 기록했으나 `subagents.py:1101-1106` 소유 고정 스킵은 잔존 — 95 소관(§4.1.6). 차단 유지 X-10·X-11·X-13·X-14·L-5 → 파일명 `-WIP` 유지 |
