# 107. 의도 확정 후 프롬프트 재작성 — IntentFrame 기반 정규 질의(canonical query) 생성 · 원문/해석 이중 채널 · 소비자별 전환

> **작성일**: 2026-09-20 · **개정**: 2026-09-21 (**v2** — 코드 재실측 + 문헌 재검토 / **v2.1** — `plans/94` 계열과의 경계·소유권 정리)
> **상태**: 계획(미구현 · 코드 0건) — **사용자 확정 게이트 G-1~G-7 · G-9 전건 확정**(2026-09-21 · 권고안 채택 · §11). 남은 것은 게이트가 아니라 **측정 산출물 2건**(G-5 W5 보고 · G-6 U-7 비교 — §11.1) · 파일명은 **`-TODO` 유지**(구현 0건이라는 사실은 확정으로 바뀌지 않는다)
> **성격**: 현행 실측 + 구현 계획(코드 0건)
> **요청 취지(사용자 지시 원문, 2026-09-20)**: *"106-TODO-harness-intent-understanding.md 계획에서 이 프로젝트 안에서는 사용자 의도 파악 후 프롬프트를 재 작성하는 부분이 없다. 이런 처리를 진행하기 위한 계획을 정리하라."*
>
> **상위/연결 계획**: **`plans/106`**(의도 파악 하네스 — **H3을 이 계획으로 이관** · H1 되묻기 재개 · H2 해석 표시 · H8 실행 브리프와 같은 원천을 공유) ·
> `plans/79`(의도 추출 출력 계약 — E-3c `input_parser` 타입 계약 · 라우터 `sub_query_context`) · `plans/50`(멀티턴 압축 신호 M3) · `plans/75`(존 역질문 · LIMIT 원문 승격 · **자연어 재조합 금지 원칙** §4) ·
> `plans/73`(폼필 역질문 구조화 답변 · D-151) · `plans/88`(순차 의존 — 선행 결과 참조 sub_query) · `plans/90`(스코프 표시·승계) · `plans/102`(교차 시스템) · `plans/103`(3단 동등성)
> **측정·수정 가족(v2.1 추가 — §6에 처분 명시)**: **`plans/94`**(시나리오 하네스 **정본** — 이 계획의 E2E 회귀·`rewrite_trace` 수집·수용 기준은 **94 §19가 소유**) ·
> `plans/96`(run `20260915-131903` 분석 정본) · **`plans/98`**(제품측 코드 수정 장부 — **CU-15·CU-18 앵커 경합**) · `plans/99`(재측정 실험 설계) ·
> **`plans/108`**(run `20260918-182507` 정본 — **2단 전용 결함 처분 규율 G-2**가 이 계획 W0.5의 전제를 건드린다 · §11 G-9)
> **관련 결정**: **D-004**(LLM 전용 라우팅 · 원문 키워드 분류 금지) · D-053(사본 금지) · **D-055·D-056**(지시어 후속 · hostname filter 주입) · D-066(LIMIT 원문 승격) ·
> D-127(과금 승인) · D-143·D-205(존 역질문·스코프 승계) · **D-151**(폼필 구조화 답변) · **D-153 후속1**(지시어 있을 때만 직전 엔티티 주입) · **D-154**(혼합 존 열거 재작성)
> **신규 결정 예약**: 없음 — G-1 확정 뒤 채번(`docs/02_decision.md` 「채번 이력」 등재 필요 · D-161 부기).
> **실측 기준**: v1 = 브랜치 `multiintent` HEAD `783a4b5`(2026-09-18) · **v2 재실측 = HEAD `92c37c3`(2026-09-21) 작업 트리**.
> 코드는 읽기만 했다(수정 0 · LLM 호출 0). v1 인용 라인은 `783a4b5` 기준으로 **전건 대조 확인**했고, v2에서 바뀐 곳만 주석으로 표기한다.
>
> **문헌 자산**: `docs/literature/`(서지 정본 `bibliography.csv` · 인용문 `claims.md`) · **`docs/standardization_literature_review.md`**(canonicalization — v2에서 새로 편입) ·
> `docs/deterministic_sql_composition_review.md` · `docs/text2sql_quality_research.md` · `plans/106` 부록 A.
> **v1의 문헌 절(§10)은 `plans/106` 부록 A만 인용하고 저장소 자체 문헌 자산을 참조하지 않았다 — v2에서 정정한다**(§10).
>
> **근거 표기**: **실측** = 파일을 열어 확인 · **코드** = `파일:라인` · **추정** = 검증하지 않은 추론
> **문헌 확인 수준**(`docs/literature/README.md` 관례): **원문** = 본문 열람 · **요약** = 초록·요약 도구 · **서지** = 검색 결과 수준

---

## 0. 요약

### 0.1 전제 — 경로에 따라 갈린다 (**v2 정정**)

v1은 *"재작성은 이미 다섯 곳에서 일어난다"* 로 사용자 전제를 정정했다. **v2 재실측 결과 이 정정은 과잉 일반화였다.**
재작성은 **사다리 1·2단 전용 현상**이고, **기준 경로(3단 `semantic_router` · D-225)의 단일 DB 질의에는 재작성이 하나도 없다.**

| 사다리 단 | 단일 DB 질의의 재작성 | 근거(실측) |
|---|---|---|
| 1단 `deep_agent`(현 운영 확정) | **있음** — R1·R2·R6 | `deepagents_tools.py:33`이 subagents 핸들러 공유 → `subagents.py:1268` |
| 2단 `intent_orchestration` | **있음** — R1·R2·R6 | 동일 |
| **3단 `semantic_router`(기준 경로)** | **없음** | `src/routing/semantic_router.py`는 `user_query`를 덮지 않는다 · `sub_query_context`는 `multi_db_executor.py:649`(멀티 DB)에서만 소비 |

> `"user_query":` **할당 전수 grep**(v2) — `state.py:337`(생성) · `api/routes/query.py:766`(원문) · `subagents.py:770`(원문 전달)·`1268`(**교체**) · `result_aggregator.py:660`(**교체**) · observability 2곳. **그래프 노드는 0곳.**

→ **사용자 전제는 기준 경로 기준으로 정확하다.** 그리고 이것이 이 계획의 우선순위를 바꾼다:
**1·2단에는 기존 재작성을 *검증*하고, 3단에는 재작성을 *새로 도입*한다**(§4.11 사다리 단별 적용표). `plans/103`(3단 동등성)·`plans/102` L-5(운영 전환)와 직결된다.

| 현상 | 실측 |
|---|---|
| 재작성 지점 | ①라우터 `sub_query_context`(LLM · DB별 · 위치어 제거) ②플래너 `sub_query`(LLM · 태스크별 · 지시어→구체값) ③존 플레이스홀더·혼합 존 열거 치환(결정적 · D-154) ④지시어 hostname의 `filter_conditions` 주입(결정적 · D-056) ⑤`input_parser`의 맥락 반영 파싱(LLM · 멀티턴) **⑥`parsed_requirements["original_query"]` 교체(v2 신규 발굴 — 아래)** |
| **v2 신규 발굴 R6** | `_scope_parsed_requirements`(`subagents.py:746-750`)·`result_aggregator.py:658-659`가 `parsed["original_query"] = sub_query`로 덮는다. **LLM SQL 프롬프트의 `## 사용자 질의`가 바로 이 필드**(`query_generator.py:1606-1607`)다 — 즉 **LLM이 실제로 읽는 재작성문은 R1이 아니라 R6**이다(`subagents.py:349` 주석: *"intent_planner가 확장한 sub_query(state.user_query)는 프롬프트에 사용하지 않는다"*) |
| 공통 결함 | **정본 없음** — 각 지점이 서로 다른 입력에서 서로 다른 텍스트를 만든다 · LLM 재작성 2곳은 **자유 서술**이라 슬롯 누락·추가를 검출하지 못한다(오염 실측 2건) · **`user_query`가 원문/재작성 두 의미로 겹쳐 쓰인다**(`subagents.py:1268`) |
| 원문 의존 소비자 | 원문 표면어를 읽는 **결정적 판정 함수 13종**(§2.3)이 `user_query`를 입력으로 쓴다 — 1·2단에서는 **이미 재작성문을 읽고 있다**(미래 위험이 아니라 **현행 결함**) |
| 되묻기 답변 병합 | 존 선택(`selected_db_ids`)·폼필(`form_fill_answers`) 두 종만 **구조화 필드**로 병합된다. 106 H1이 새로 묻는 슬롯(기간·지표·대상)의 답을 **원 질의와 합치는 일반 경로가 없다** |
| **재작성 게이트 부재** | R1·R2는 **모든 질의에 무조건** 발동한다. 모호하지 않은 질의도 재작성되므로 오염 위험만 지고 이득은 없다 — 문헌이 공통으로 경고하는 지점(§10 L-5·L-6) |

### 0.2 결론

| 질문 | 답 |
|---|---|
| 무엇을 만드나 | **`IntentFrame`**(확정된 의도의 구조 정본 · 슬롯별 출처 표시) → 결정적 **병합**(발화·맥락·되묻기 답변·기본값) → 결정적 **렌더러**가 프롬프트 텍스트를 파생 → **검증**(슬롯 보존) → 소비자에게 전달 |
| 원문은 | **덮어쓰지 않는다.** 원문(**`raw_user_query` 신설** — v2 정정, §4.4)과 해석(`canonical_query`)을 **별도 채널**로 두고, 소비자마다 어느 쪽을 읽을지 명시한다 |
| 재작성 방식 | **기본은 대체가 아니라 병기(augment)** — LLM 프롬프트에 "사용자 원문" + "확정된 해석(구조 블록)"을 함께 넣는다. 원문을 버리는 대체(replace)는 측정 후 소비자별로만(G-2) |
| 재작성 **여부**는 | **게이트한다**(v2 신규 원칙 P-9) — 슬롯이 전부 이번 발화에서 나왔고 미해결이 없으면 **재작성하지 않고 통과**시킨다. 문헌 공통 권고이며(§10 L-5 Adobe·L-6 REWRITER Checker), 무조건 재작성이 이 저장소 오염 **3건**의 구조적 원인이고, 반대로 재작성 부재가 **1건**을 낳았다(§2.4) |
| LLM은 | 렌더는 **템플릿(LLM 0회)**. 단 **의도 보강(§4.8)에는 LLM을 쓴다** — 자유 서술이 아니라 **슬롯 후보를 구조화 출력**으로 내게 하고, 결정적 검증(존재성·허용 집합) 통과분만 채택한다(D-035 "LLM=후보 생성 · 결정=결정적"의 CHESS 배치) |
| 기존 재작성 6곳은 | 지우지 않는다. ③④는 병합 규칙으로 **흡수**, ①②⑥은 LLM 산출을 유지하되 **프레임 대조 검증**을 붙이고, 장기적으로 프레임 파생으로 대체 여부 판단(G-5) |
| 순서 | **W-1 실측 확정 → W0.5 슬롯 승격** → W0 골든셋 → W1 프레임·병합(**섀도**) → W2 렌더·검증(섀도) → **W2.5 의도 보강** → W3 소비자 1곳씩 전환 → W4 되묻기 재개 일반화(106 H1 연동) → W5 라우터·플래너 사후 검증.<br>**착수 범위는 G-1·G-9 확정(2026-09-21)으로 고정됐다** — 선행 차수는 **W-1(대상 목록 실측 확정) + W0.5(미승격 슬롯 승격 · 발현 빈도 순)** 이고, 그 결과 보고 뒤에 W0~W2로 넘어간다(§11) |

### 0.3 사용자 요청 취지와의 정합 (**v2 신설**)

요청 취지는 *"프롬프트를 정제해 **LLM이 재작성**을 통해 의도를 명확히 하고 **응답의 신뢰도와 정확성**을 높인다"* 였다. v1은 이 중 "LLM 재작성"을 사실상 거부했다(렌더 LLM 0회 · LLM은 W6 자연문 다듬기 옵션뿐). v2는 이를 **둘로 쪼개 각각 답한다.**

| 요청 요소 | v2의 답 | 근거 |
|---|---|---|
| **신뢰도** — 의도가 손실·오염되지 않게 | **결정적 병합 + 템플릿 렌더 + 슬롯 승격**(W0.5·W1·W2) | 저장소 오염 실측 4건(§2.4) · 재작성 누적 drift 경고(§10 L-7) |
| **정확성** — 의도를 더 잘 이해해 답을 개선 | **의도 보강(§4.8 · W2.5)** — DB 실값·스키마·질의 이력을 근거로 **LLM이 슬롯 후보를 제안**하고 결정적으로 검증 | DART-SQL **+12.41%/+5.38% EX** · 질의 이력 편입 **+40.2pt** · 되묻기 **+9.51%**(§10 L-1) |
| **LLM 재작성** 형태 | **자유 서술 재작성은 채택하지 않는다.** LLM은 *슬롯을 채우고*, 텍스트는 코드가 찍는다 | 자유 재작성 기대 이득은 **+1.6~2.0%p**(REWRITER · §10 L-6)인데 이 저장소 실측 손실은 **행 57% 절단·오라우팅·스코프 붕괴**(§2.4) — **비대칭이 결정 근거** |

### 0.4 권고 한 줄

**"프롬프트를 LLM에게 다시 쓰게 한다"가 아니라 "LLM은 슬롯을 채우고, 프롬프트는 코드가 그 구조에서 찍어낸다."** 원문은 항상 옆에 남기고, 모호하지 않으면 아예 건드리지 않는다.

---

## 1. 배경과 범위

### 1.1 왜 필요한가

- **106 H1(슬롯 게이트)이 되물은 뒤** — 답을 받아 원 질의와 합쳐 다시 실행해야 한다. 지금은 존 선택만 이 경로가 있다.
- **106 H2(해석 표시)·H8(실행 브리프)** — "무엇으로 해석했는가"를 보여주려면 해석이 **하나의 구조**로 존재해야 한다. 지금은 해석이 라우터 출력·플래너 출력·파서 출력에 흩어져 있다.
- **멀티턴 후속 질의** — "그 서버 메모리는?" 같은 발화는 직전 맥락과 합쳐져야 완결된다. 지금은 플래너 LLM이 자유 서술로 합치며, 그 과정에서 **오염이 두 번 실측됐다**(§2.4).
- **문헌·상용 사례** — Deep Research(명확화 → 상세 지시문 재작성 → 실행), Mediator-Assistant(모호 입력 → *well-structured instructions* 재구성), RECAP(대화 → 과업 인지형 의도 표현). 공통점은 **실행 모델이 받는 입력이 "확정된 의도"** 라는 것이다(`plans/106` 부록 A B-1·B-2·P-6).
- **도메인 문헌(v2 신규 · §10)** — NL2SQL 계열이 같은 결론에 도달해 있다: **QBridge**(ACL 2026)는 노이즈 질문을 *"structured, SQL-aligned intermediate representation"*(Gold Query)으로 재작성하고 **실행으로 검증·보수적 정제**한다. 이 계획의 "정본은 구조, 텍스트는 파생"(P-1)과 같은 형태이며, 이 저장소에는 그 IR에 해당하는 `SMQ`가 **이미 있다**(`src/semantic/ir.py:110` — §6 경계 선언).

### 1.2 범위

- **대상**: 공통 전단(`context_resolver → input_parser → field_mapper`) 뒤, 라우팅·실행 앞의 **의도 확정 표현**과 그것을 소비하는 LLM 프롬프트.
- **비대상**: SQL 생성 로직 자체 · 라우터 분류 지시문(`plans/79` 트랙 A) · 되묻기 **판정**(`plans/106` H1 — 이 계획은 판정 **이후**의 병합·재작성을 소유).
- **이관**: `plans/106` 트랙 H3(후속 턴 독립 질의 재작성 — 조건부)은 **이 계획으로 이관**한다(`plans/INDEX.md` 이관 조항 · D-208 구조 — 이관처가 태그를 단다). 106 H3의 설계 제약(섀도 · 원문 슬롯 우선 · diff 감사)은 이 계획의 원칙 P-2·P-4·P-6으로 승계한다.

---

## 2. 현행 실측

### 2.1 재작성 지점 전수

| # | 지점 | 방식 | 입력 → 출력 | 위치(실측) | 비고 |
|---|---|---|---|---|---|
| R1 | 라우터 `sub_query_context` | **LLM 자유 서술** | 원문 → DB별 "순수 조회 의도"(위치·DB 정보 제거) | 프롬프트 규칙 `src/prompts/semantic_router.py:62-77` · 단일 DB에서 SQL 생성 입력으로 사용 `src/orchestration/subagents.py:1238-1242` · 멀티 DB `src/nodes/multi_db_executor.py:649` | 위치가 SQL WHERE로 누출되는 것을 막는 목적(§4.9.6 디멘전 7) |
| R2 | 플래너 `sub_query` | **LLM 자유 서술** | 원문 + 압축 맥락 → 태스크별 지시(지시어를 구체 값으로 치환 · 선행 결과 참조) | `src/prompts/intent_planner.py:57-79` · 맥락 블록 `src/orchestration/intent_planner.py:580-613` | 오염 실측 2건(§2.4) |
| R3 | 존 표기 치환 | **결정적** | 원문 → 'ㅇㅇ존' 플레이스홀더 치환 · 미선택 존 열거 제거(D-154) | `src/api/routes/query.py:855-870` · `src/utils/query_gen_common.py:1847` | 주석: *"결정적 문자열 치환(LLM 재해석 아님)"* |
| R4 | 지시어 hostname 주입 | **결정적(슬롯 수준)** | 직전 서버 hostname → `filter_conditions` | `src/orchestration/subagents.py:344-352` `_inject_demonstrative_hostname` | 텍스트가 아니라 **슬롯**을 고친다 — 이 계획이 일반화하려는 방식의 선례 |
| R5 | `input_parser` 맥락 파싱 | LLM 구조화 | 원문 + 직전 SQL·결과 요약·테이블 → 10키 | `src/nodes/input_parser.py:100-118 · 219-225` | 재작성이 아니라 **해석**이지만 맥락이 슬롯에 섞여 들어오는 첫 지점 |
| **R6** (v2 신규) | `parsed_requirements["original_query"]` 교체 | **결정적 교체 · 내용은 R2의 LLM 산출** | 원문 → 태스크 `sub_query` | `src/orchestration/subagents.py:746-750`(`_scope_parsed_requirements`) · `src/orchestration/result_aggregator.py:658-659` | **LLM이 실제로 읽는 재작성문.** 의도된 축소다(D-094·D-092 — 전체 질의를 남기면 하위 task 결과로 전체 질문에 답한 듯 환각) |

> **v1 오류 정정**: v1 §4.4는 `query_generator`의 현재 입력을 *"`user_query` + `parsed_requirements`"* 로 적었다. 실측은 다르다 —
> `query_generator._build_user_prompt`는 `## 사용자 질의`에 **`parsed_requirements["original_query"]`**(=R6)를 쓰고(`query_generator.py:1606-1607`),
> `state["user_query"]`(=R1)는 **LLM 프롬프트가 아니라 §2.3 결정적 함수들**이 쓴다. 두 채널의 역할이 v1 서술과 반대다.

### 2.2 원문 보존 장치 — **이름과 실제가 다르다** (v2 정정)

| 장치 | 위치 | v1 서술 | **v2 실측** |
|---|---|---|---|
| `parsed_requirements.original_query` | `input_parser.py:124 · 254 · 355` | *"파서가 원문을 복사해 둔다"* | ⚠️ **원문 채널이 아니다** — 1·2단에서 R6이 `sub_query`로 덮는다. 실제 의미는 **"태스크 스코프 질의"** |
| `original_user_query` | `subagents.py:830-833` | 원문 보존 | ✅ 맞다 — 게이트 판정·재전송 전용(소비처 한정) |
| LIMIT 원문 승격(`resolved_limit`) | `src/state.py:125-127` · `subagents.py:770-776` | 원문 기준 LIMIT 보존 | ✅ 맞다 — **이 계획이 일반화하려는 방식의 유일한 선례**(§2.3) |
| 원문 우선 폴백 | `multi_db_executor.py:1389 · 1410 · 1999` | `original_query or sub_query_context` | ⚠️ **1·2단에서는 원문이 아니다**(앞 항목과 같은 원인) |

→ **v1 해석 정정**: 저장소가 원문을 "여러 겹으로 보존해 왔다"는 것은 **절반만 맞다.** 실제로는 **소비처 한 곳(`original_user_query`)과 슬롯 한 개(`resolved_limit`)만** 진짜 보존이고,
`original_query`라는 이름의 필드는 **이미 재작성 채널**이다. 따라서 §4.4의 "원문 채널"은 기존 필드 승격으로 만들 수 없고 **신규 불변 필드가 필요하다**.

### 2.3 `user_query` 표면어에 의존하는 결정적 함수 — **현행 결함** (v2 격상)

| 함수 | 정의 위치 | 호출(예) |
|---|---|---|
| `resolve_spike_request` | `src/domain/change_terms.py:105` | `src/nodes/query_generator.py:579` |
| `resolve_comparison_periods` | `src/utils/query_gen_common.py:291` | `query_generator.py:583` |
| `matched_filesystem_term` | `src/domain/change_terms.py:145` | `query_generator.py:590` |
| `resolve_absolute_threshold` | `src/domain/change_terms.py:164` | `query_generator.py:593` |
| `matched_other_metric_terms` | `src/domain/change_terms.py:152` | `query_generator.py:628` |
| `has_all_scope_keyword` | `src/utils/query_gen_common.py:456` | `multi_db_executor.py:2388` |
| `is_realtime_usage_query` | `src/utils/query_gen_common.py:603` | `subagents.py:814` |
| `resolve_effective_limit` | `src/utils/query_gen_common.py:538` | `query_generator.py:480` · `multi_db_executor.py:346` |
| `refers_to_demonstrative_server` | `src/utils/query_gen_common.py:1688` | `subagents.py:341` · `intent_planner.py:611` |
| 시간 범위 해석(`parsed_time_range` 병용) | `query_generator.py:485` | — |
| **`resolve_query_limit`**(v2 추가) | `query_gen_common.py` | **`semantic_compiler.py:276`** |
| **`_resolve_ranking`**(v2 추가) | `src/nodes/semantic_compiler.py:474` | `semantic_compiler.py:407` |
| **`_is_superlative`**(v2 추가) | `src/nodes/semantic_compiler.py:498` | `semantic_compiler.py:404` |

- `user_query`를 읽는 파일은 **22개**(실측 grep — `output_generator` 6 · `multi_db_executor` 6 · `input_parser` 5 · `subagents` 4 …).
- **v1은 이 표를 "재작성 시 영향권"(미래 위험)으로 두고 U-1을 "추정 · W0에서 확인"으로 미뤘다. 실측하면 현행 결함이다.**
  1·2단에서 `subagents.py:1268`이 `state["user_query"]`를 `sub_query_context`로 교체하므로 **이 함수들은 이미 재작성문을 읽고 있다.**
- **저장소는 이 결함을 한 번 겪고 한 슬롯만 고쳤다** — `resolve_effective_limit` docstring(`query_gen_common.py:545-552`, 실측):

  > *"오케스트레이션 단일 DB 경로는 user_query를 semantic_router 정제 질의(sub_query_context)로 교체하는데, 이 정제(문장 압축)가 '모든' 등 수량 한정어까지 탈락시켜 resolve_query_limit이 기본 1,000으로 떨어졌다(은행존 **2,328대 중 1,328대 절단** — 멀티 경로는 sub_query 유지로 미발현, **구조적 비대칭**). limit 신호는 문자열이 아니라 state(resolved_limit)로 운반해 상류의 어떤 문자열 훼손과도 무관하게 보존한다."*

- ⚠ **v2.3 정정 — 위 표의 13종 중 최소 3종은 이미 닫혀 있다.** v2가 *"남은 12종은 승격되지 않았다"* 고 쓴 것은 **실측 부족**이었다(2026-09-21 재실측):

| 함수 | v2 표기 | **v2.3 실측** | 근거 |
|---|---|---|---|
| `resolve_effective_limit` | 승격됨 | ✅ **승격됨** | `query_gen_common.py:538` — `state["resolved_limit"]` 우선(D-066) |
| **`resolve_query_limit`** | 미승격(W0.5 대상) | ⚠ **방향별로 갈린다 — 축소 닫힘 · 확대 잔존**(W-1 확인 대상 · **W0.5 우선순위에서는 제외**) | 승격을 거치지 않는 직접 호출부가 **둘**이다 — `src/nodes/semantic_compiler.py:276` · `src/tools/interpretation.py:46`(`binding.py:161` 경유). 본문(`query_gen_common.py:520-527`)은 `_EXPLICIT_COUNT_RE`/`_TOP_N_RE` 매치 시 `:524`, `has_all_scope_keyword(text)` 매치 시 `:526`에서 **`default_limit`에 닿기 전에 반환**하고, 승격값은 `:529`·`:531`(미매칭 폴백)에서만 쓰인다. 따라서 **① 축소 방향**(원문의 "모든"이 재작성에서 탈락) = **닫힘** — 미매칭이라 승격값이 반환된다 **② 확대·변조 방향**(재작성문이 원문에 없던 "전체/모든"·건수를 얻음) = **잔존** — 승격값이 상향으로 덮인다. 잔존 노출이 **조건부**(재작성이 표현을 *추가*해야 성립)라 W0.5 발현 빈도 우선순위에서는 빼고, **성립 사례 유무를 W-1이 확인**한다 |
| **`is_realtime_usage_query`** | **미승격(W0.5 대상)** | ✅ **승격됨** | `subagents.py:820`이 **`_make_isolated_input` 안**(교체 지점 `:1268`보다 **앞**)에서 `state["user_query"]`(=원문)로 판정해 `realtime_usage_intent`로 승격하고, 소비는 `:1215`·`:1244`가 그 승격 필드를 읽는다. 코드 주석이 근거를 직접 적는다 — *"**원문 기준**으로 판정해 승격 — sub_query/sub_query_context 재작성으로 '실시간' 표면어가 탈락해도 유지(resolved_limit과 동일 원리, D-066 후속7)"* |

- **표의 호출부 라인 2건도 어긋난다**(작업 트리 실측): `has_all_scope_keyword` → `multi_db_executor.py:2394`(표기 `:2388`)이고 **`sql_validation.py:193` 호출부가 표에서 빠져 있다** · `resolve_spike_request` 등 5종의 실제 호출부는 `query_generator.py:607·611·618·621·656`이다(표기 `:579·583·590·593·628`).
- **호출부 전수 grep(v2.3)** — `resolve_query_limit` 8곳:

| 호출부 | 판정 | 근거 |
|---|---|---|
| `src/nodes/semantic_compiler.py:276` | **축소 닫힘 · 확대 잔존**(W-1) | 직접 호출이나 `default_limit`이 승격 산출물이다(`query_generator.py:503-506`→`:696-698`→`semantic_compiler.py:1100`→`:276`). 미매칭 경로만 그 값에 닿는다 |
| `src/tools/interpretation.py:46`(`binding.py:161` 경유) | **축소 닫힘 · 확대 잔존 + 빈 `query` 폴백**(W-1 · **U-10**) | `resolve_limit(query or context.user_query, default_limit=context.default_limit)` — **LLM이 빈 값을 넘기면 `context.user_query`로 폴백**하고 그 값은 `column_deriver.py:130-142`의 `ToolContext(user_query=…)` = `query_generator` 경로의 `state["user_query"]`(1·2단 재작성문)다. `default_limit`은 승격값(`deps.default_limit`) — **`semantic_compiler.py:276`과 정확히 같은 비대칭** |
| `src/utils/query_gen_common.py:566` | 정상 | `resolve_effective_limit` **내부 폴백**이라 승격값이 우선한다 |
| `src/orchestration/subagents.py:776` | 정상 | **승격을 산출하는 지점** |
| `src/api/routes/query.py:829 · 849 · 1860 · 2122` | 정상 | 라우트가 **원문**(`body.query`·`query`)을 직접 넘긴다 |

- **따라서 W0.5의 실 대상은 「13종」이 아니라 「입력 오염 축의 미승격 슬롯」**이고, 그 목록 확정이 **W-1의 산출물**이다(§5).
  현재 확실한 미승격은 **spike · 비교기간 · 파일시스템 · 절대임계 · 기타지표 · 시간범위 · 순위 · 최상급 8종**이며,
  `has_all_scope_keyword`·`refers_to_demonstrative_server`는 **호출 순서에 따라 갈려 W-1이 판정**한다(추정으로 고정하지 않는다).
  **LIMIT 계열은 「닫힘」이 아니라 「축소 닫힘·확대 잔존」**이지만, 잔존이 조건부라 **우선순위에서 빼고 W-1 확인 대상으로 둔다**(성립 사례가 나오면 다시 연다).
- **처분(§11 G-9 확정 · ㉮ 범위 한정 · 2026-09-21)**: 미승격 슬롯을 `resolved_limit`·`realtime_usage_intent`와 **동형으로** 승격한다.
  **우선순위는 실 트래픽 발현 빈도** — 시나리오 카탈로그 218건(= 두 run이 실행한 바로 그 집합) 프롬프트 표면어 실측:
  **절대임계 21 · 순위/최상급 20 · 비교기간 8 · 파일시스템 4 · spike 1**(전체스코프 29는 판정 보류분).
  **발현 근거가 없는 슬롯은 3단 전환 시점 재판정으로 미룬다.**
- **단 W-1(전수 실측)은 범위 축소와 무관하게 수행한다** — 무엇이 소멸하는지 알아야 재판정이 가능하다.
- 이 함수들은 전환 후에도 **원문 채널(`raw_user_query`)에 남긴다** — 재작성문·렌더 문구("최근 1시간(기본값)")가 섞이면 표면어 판정이 흔들린다.

### 2.4 재작성 오염 실측 사례 (회귀 케이스로 고정할 것)

| 일자 | 사례 | 원인 | 현 조치 |
|---|---|---|---|
| 2026-07-16 | "은행존 알람" → "김포 은행 공동존…"으로 재작성 · gp 오라우팅 | 플래너 LLM이 **명시 위치와 직전 위치를 병합** | 명시 위치가 있으면 직전 위치 줄을 입력에서 제거(`intent_planner.py:585-590`) |
| 2026-08-04 | 전량 조회 후 "OS 종류…확인" → 샘플 **4개 서버로 축소** 재작성 | 직전 엔티티(상한 샘플)를 **스코프로 오인** | 지시어 있을 때만 직전 엔티티 주입(D-153 후속1 · `intent_planner.py:607-613`) |
| 2026-08-05 | 상호배타 재선택 후 원문의 **미선택 존 위치어가 SQL WHERE로 누출** | 원문이 그대로 흐름 | 결정적 열거 치환(D-154 · R3) |

| 2026-07-24 | 은행존 "모든" 질의가 **LIMIT 1,000으로 절단**(2,328→1,000) | 라우터 정제가 수량 한정어 탈락 | `resolved_limit` 원문 승격(D-066 · `query_gen_common.py:545-552`) |

→ **방향이 둘로 갈린다. 이것이 게이트 설계의 근거다.**

| 방향 | 사례 | 함의 |
|---|---|---|
| **재작성이 일으킨 오염**(3건) | 2026-07-16 · 2026-08-04 · 2026-07-24 | 세 질의 모두 **모호하지 않았다** — 재작성이 필요 없는데 무조건 발동해 망가뜨렸다. → 게이트가 `pass_through`로 막는다 |
| **재작성 부재가 일으킨 누출**(1건) | 2026-08-05(미선택 존 위치어가 WHERE로) | 되묻기 답변이 들어와 **슬롯 출처가 발화 밖**이 된 경우다. → 게이트가 `non_utterance_source`로 **발동시킨다** |

→ 즉 게이트는 "재작성을 줄이는 장치"가 아니라 **"필요한 곳에만 발동시키는 장치"** 다. 두 방향을 하나의 판정(§4.3.1)으로 덮는다.
공통 원인은 하나 더 있다 — **"누가 이기는가(우선순위)"가 텍스트 안에 암묵적으로만 있었다.** 병합 규칙(§4.3)이 이를 코드로 명시한다.

> **손익 비대칭(§0.3 결정 근거)**: 자유 재작성의 문헌상 기대 이득은 NL2SQL에서 **+1.6~2.0%p**(REWRITER · §10 L-6)인데,
> 이 저장소가 실측한 손실은 **행 57% 절단 · 오라우팅 · 스코프 4대 붕괴**다. 이득이 손실을 정당화하지 못한다.

### 2.5 되묻기 답변의 전달 원칙 — **자연어 재조합 금지**

`src/api/schemas.py:39-49` 주석(실측):
- 존 선택: *"자연어 재조합 금지 원칙 — 선택 결과는 이 구조화 필드로만 전달되어 semantic_router/intent_planner의 결정적 고정으로 주입된다"*(Plan 75 §4)
- 폼필: *"자연어 재조합·LLM 파싱 없이 이 필드로만 전달되어 결정적 검증(존재성)·적용을 거친다"*(Plan 73 §11 · D-151)

→ **이 원칙이 이 계획의 설계를 결정한다.** 되묻기 답변을 원문 뒤에 문장으로 이어 붙여 LLM에 다시 파싱시키는 방식(문헌의 흔한 구현)은 **이 저장소 원칙과 충돌**한다. 답변은 **구조화 필드 → 프레임 슬롯**으로만 들어간다(§4.6).

---

## 3. 문제 정의

| ID | 문제 | 근거 |
|---|---|---|
| P1 | 확정된 의도의 **단일 정본이 없다** — 파서 10키 · 라우터 targets/sub_query_context · 플래너 tasks/sub_query가 각자 해석을 들고 있다 | §2.1 |
| P2 | LLM 재작성(R1·R2)은 **자유 서술**이라 슬롯 누락·추가·병합을 검출하지 못한다 | §2.4 |
| P3 | `user_query` 한 필드가 **원문과 재작성문 두 의미**를 오간다 — 소비자별 개별 복구가 누적 | §2.2 · §2.3 |
| P4 | 되묻기 답변의 **일반 병합 경로 부재**(존·폼필 전용 2종) | §2.5 |
| P5 | 재작성 전후를 **감사할 수 없다** — 무엇이 어떻게 바뀌었는지 기록 없음(106 G-P4 실측과 동일 원인) | `src/security/audit_logger.py:66-146` |
| P6 | 우선순위 규칙(명시 위치 최우선 · 지시어 조건부 승계 · 미선택 존 제거)이 **프롬프트 문장과 코드 주석에 분산** | §2.4 |
| **P7** (v2) | **재작성이 무조건 발동한다** — 모호하지 않은 질의도 재작성돼 이득 없이 오염 위험만 진다 | §2.4 4건 · §10 L-5·L-6 |
| **P8** (v2) | **의도를 *보강*하는 경로가 없다** — 사용자가 DB 실제 값·명칭을 모르고 쓴 표현을 근거 기반으로 채우는 단계가 부재. 부품(`entity_locator`·`column_value_index`·`condition_probe`·유사어 시드)은 있으나 **의도 표현과 미연결**(106 G-A2와 같은 진단) | §4.8 · §10 L-1 |
| **P9** (v2) | **사다리 단별 비대칭** — 3단(기준 경로) 단일 DB에는 재작성이 없고 1·2단에만 있다. 같은 질의가 단에 따라 다른 텍스트로 실행된다 | §0.1 |

---

## 4. 설계

### 4.1 원칙

| # | 원칙 | 이유 |
|---|---|---|
| P-1 | **정본은 구조, 텍스트는 파생** — 재작성문은 언제나 `IntentFrame`에서 렌더된다 | P1·P2 해소. 텍스트 diff가 아니라 **슬롯 diff**로 검증 가능 |
| P-2 | **원문 불변** — 신규 코드는 `user_query`를 덮어쓰지 않는다. 원문 채널·해석 채널을 분리한다 | P3. §2.3 결정적 함수 보호 |
| P-3 | **병합은 결정적 코드** — 발화·맥락·답변·기본값의 우선순위를 함수로 명시 | P6 · §2.4 오염 원인 제거 |
| P-4 | **섀도 먼저** — 프레임·렌더는 먼저 기록만 하고 소비자는 바꾸지 않는다 | 회귀 0 출발(CLAUDE.md 플래그 원칙) |
| P-5 | **병기(augment) 기본** — 원문을 버리지 않고 해석 블록을 옆에 붙인다 | 원문 뉘앙스 보존 · 오염 시에도 원문이 남아 LLM이 교차 확인 가능(**추정** — W3에서 측정) |
| P-6 | **모든 재작성은 감사된다** — 프레임 해시 · 슬롯 출처 · 원문↔해석 diff | P5 |
| P-7 | **되묻기 답변은 구조화 필드로만** 프레임에 들어간다 | §2.5 기존 원칙 준수 |
| P-8 | 원문 키워드 분류 금지(D-004)는 그대로 — 프레임 구축은 **파서·라우터 구조화 산출 + 레지스트리**만 입력으로 쓴다 | D-004 |
| **P-9** (v2) | **재작성은 게이트된다** — 슬롯 출처가 전부 `utterance`이고 `unresolved`가 비면 **재작성·병기하지 않고 통과**시킨다(§4.3.1) | P7 · §10 L-5(Adobe: *"not risking unnecessary insertions of unwanted phrases for clear queries"*) · L-6(REWRITER Checker) |
| **P-10** (v2) | **LLM은 후보 생성, 결정은 결정적** — 의도 보강(§4.8)에서 LLM은 **슬롯 후보를 구조화 출력**으로만 내고 채택은 결정적 검증이 한다. 자유 서술 재작성은 채택하지 않는다 | D-035 · CHESS 배치(`docs/standardization_literature_review.md` §3.4) |
| **P-11** (v2) | **커버리지 밖에서는 조용히 틀리지 않는다** — 보강·병합이 실패한 슬롯은 `unresolved`에 남기고 106 H1 되묻기 또는 명시 안내로 보낸다 | `docs/standardization_literature_review.md` §3.7(다) *"조용한 오답보다 명시 실패가 낫다"* |

### 4.2 `IntentFrame` 스키마 (초안)

```python
# src/domain/intent_frame.py  (domain 계층 · 순수 · 가칭)
# ⚠️ domain은 src.domain.* 외 내부 임포트 0이 현행 관례다(실측) — 아래 §4.10 계층 주의 참조.
class SlotValue(BaseModel):
    value: Any
    source: Literal[
        "utterance",            # 이번 턴 발화(파서)
        "clarification_answer", # 되묻기 구조화 답변(106 H1 · 존 선택 · 폼필)
        "context_inherited",    # 직전 턴 승계(context_resolver 압축 신호)
        "default",              # 기본값 정책(106 H1.5) — 106 H2 '가정' 표시 대상
        "registry",             # 레지스트리·결정표 파생(존→DB 등)
        "llm_enrichment",       # (v2) 의도 보강 §4.8 — 결정적 검증 통과분만
    ]
    evidence: str | None = None # 예: "previous_entities[0]" · "selected_db_ids"
    # (v2) SAGE-Agent의 specification uncertainty(사용자가 안 정함) vs
    #      model uncertainty(LLM이 확신 못 함) 분리 — 106 H1 되묻기 판정과 H2 가정 표시가
    #      같은 필드를 읽게 해 "판정 기준이 두 곳에 갈리는" 것을 막는다(§10 L-4).
    spec_uncertain: bool = False   # 사용자가 지정하지 않음 → 되묻기 후보
    model_confidence: float | None = None  # LLM 산출 슬롯의 확신도(없으면 None)

class IntentFrame(BaseModel):
    frame_version: int = 1
    intent: str                                  # 라우터 허용 집합(plans/79 E-1)에서 파생 — 사본 금지(D-053)
    goal_class: str | None = None                # 106 H7 최종 목표 등급(확정 전 None)
    targets: dict[str, SlotValue] = {}           # zone / db_ids / hosts / ip ...
    metrics: SlotValue | None = None
    time_range: SlotValue | None = None
    filters: list[dict] = []                     # filter_conditions(자유 형식 유지 — E-3c D3)
    aggregation: SlotValue | None = None
    limit: SlotValue | None = None               # 원문 승격값(D-066) 우선
    output: SlotValue | None = None              # text / excel / word
    depends_on: list[str] = []                   # 순차 의존(plans/88) — 선행 task_id 참조
    unresolved: list[dict] = []                  # [{slot, reason}] — state.py 관례
    original_query: str                          # 원문(불변)
```

- **입력**: `parsed_requirements`(E-3c 타입 계약 — `src/nodes/schemas.py:20` `ParsedRequirements`) + 라우터 결과 + `conversation_context` 압축 신호 + 구조화 답변 필드 + 레지스트리 **파생 결과값**.
- **E-3c와의 관계**: `ParsedRequirements`는 **LLM 출력 계약**, `IntentFrame`은 **병합 후 확정 의도**다.
- ⚠️ **v1 설계 오류 정정** — v1은 *"필드를 복사 정의하지 않는다(가능한 한 **참조**)"* 로 적었는데, 그대로 하면 `arch_check`가 실패한다:
  `src.domain`(domain) → `src.nodes`(**application**)는 역방향이고(`scripts/arch_check.py:42·68`), `merge_frame(..., registry)`의 `src.routing`은 **infrastructure**다.
  **정정**: `IntentFrame`은 domain에 **자립 모델**로 두고, `ParsedRequirements → IntentFrame` 변환과 레지스트리 해소는 **application 빌더가 수행**해 plain data로 주입한다.
  D-053(사본 금지)은 **정본 중복 금지**이지 타입 재선언 금지가 아니다 — 정본은 **변환 함수 1곳**으로 유지한다(`plans/79` E-1이 허용 집합 정본을 프롬프트 모듈 1곳에 둔 것과 같은 형태).

### 4.3 병합 규칙 — 우선순위를 코드로

```python
def merge_frame(parsed_now, prior_frame, answers, context, defaults, registry) -> IntentFrame
```

| 순위 | 출처 | 규칙 | 기존 근거 |
|---:|---|---|---|
| 1 | **되묻기 구조화 답변** | 답한 슬롯은 무조건 확정 | Plan 75 §4 · D-151 |
| 2 | **이번 발화 명시값** | 발화에 있는 위치·식별자·기간은 승계값을 **대체**(병합 금지) | 2026-07-16 사례 · `intent_planner.py:585-590` |
| 3 | 직전 턴 승계 — **위치/DB** | 이번 발화에 위치 슬롯이 **비었을 때만** | D-143·D-205 스코프 승계 |
| 3′ | 직전 턴 승계 — **엔티티(서버)** | 파서/라우터가 **지시 참조**를 구조로 표시한 경우에만 · 상한 샘플은 **스코프로 쓰지 않음** | D-153 후속1 · 2026-08-04 사례 · D-055/056 |
| 4 | 기본값 정책 | 106 H1.5 표 · `source="default"`로 표시 | 106 H2 |
| 5 | 레지스트리 파생 | 존 → DB, 선택 DB → 존 라벨 | R3(D-154) 흡수 |

- **3′의 "지시 참조" 판정**: 현재 `refers_to_demonstrative_server`(원문 표면어)를 쓴다. 이 계획은 판정 결과를 **프레임 병합 입력으로 받기만** 하고 판정 방식은 바꾸지 않는다(D-004와의 관계는 현행 유지 — G-6에서 파서 구조화 필드로의 이전 여부 결정).
- **미선택 존 제거(D-154)**: 텍스트 치환이 아니라 **프레임의 `targets.zone`이 선택값으로 확정**되므로 렌더 결과에 미선택 존이 나타날 수 없다(구조적 해소). R3 텍스트 치환은 **원문 채널용으로 유지**(처리 현황 표시 등 기존 소비자).

### 4.3.1 재작성 게이트 — **건드릴 이유가 있을 때만 건드린다** (v2 신설 · P-9)

문헌이 공통으로 경고하는 지점이다. Adobe Experience Platform AI Assistant는 재작성을 **모호성 분류기 뒤에** 두어
*"clear queries에 불필요한 구절이 삽입될 위험을 지지 않는다"*(§10 L-5), REWRITER는 **Checker**가 먼저 결함 질의를 골라
*"unnecessary rewriting and potential hallucinations"* 를 줄인다(§10 L-6). 이 저장소 오염 4건은 **전부 게이트 부재가 낳았다**(§2.4).

```python
def rewrite_needed(frame: IntentFrame) -> tuple[bool, str]:
    # 재작성·병기가 필요한지 결정적으로 판정한다(사유는 rewrite_trace에 기록).
    if frame.unresolved:
        return True, "unresolved_slot"
    if any(sv.source != "utterance" for sv in frame.all_slots()):
        return True, "non_utterance_source"   # 승계·기본값·되묻기답변·보강이 섞였다
    return False, "pass_through"              # 전부 이번 발화에서 나왔다 → 원문 그대로
```

| 게이트 결과 | 동작 |
|---|---|
| `pass_through` | **원문 그대로.** 프레임은 만들되(감사·표시용) 프롬프트를 바꾸지 않는다 |
| `non_utterance_source` | 병기(augment) — 어느 슬롯이 발화 밖에서 왔는지 블록에 `출처` 표기(106 H2 가정 표시와 같은 원천) |
| `unresolved_slot` | 106 H1 되묻기 후보. 묻지 않기로 하면 기본값 + 가정 표시(P-11) |

- **측정 의무**: 섀도 기간에 `pass_through` 비율을 보고한다. 이 비율이 높을수록 현행 무조건 재작성이 **헛일하고 있었다**는 뜻이다(W5 리포트 항목).
- 게이트는 **라우팅 결정을 바꾸지 않는다**(D-004) — 바꾸는 것은 프롬프트 텍스트뿐이다.

### 4.4 이중 채널과 소비자 전환 표

> **v2 정정** — v1은 원문 채널을 `original_query` **기존 값 승격**으로 설계했으나, 그 필드는 이미 재작성 채널이다(§2.2). **신규 불변 필드를 만든다.**

| 채널 | 필드 | 내용 | 쓰는 쪽 |
|---|---|---|---|
| **원문** | **`raw_user_query`(v2 신설)** | 라우트 진입 시 1회 기록(`api/routes/query.py:766` 지점) · **어떤 코드도 덮지 않는다** | §2.3 결정적 함수 13종 · LIMIT 승격 · 감사 · 이력 few-shot 검색(`select_history_fewshot` — `prompt_blocks.py:187`) · 재전송 페이로드 |
| (의미 고정) | `original_query` | **"태스크 스코프 질의"** — 현 사용처의 실제 의미. 이름 오해를 막는 주석을 정의부에 단다 | `query_generator` LLM 프롬프트(R6) · `output_generator` |
| (표시) | `display_query` | R3 존 치환본 — 화면 표시용(G-7) | 처리 현황 표시 |
| **해석** | `intent_frame` · `canonical_query` · `canonical_block` | 프레임과 그 렌더 | LLM 프롬프트(아래 표) · 106 H2 해석 표시 · 106 H8 브리프 |
| (호환) | `user_query` | **현행 의미 유지** — 이 계획은 새로 덮어쓰지 않는다 | 기존 전 소비자(전환 전까지) |

- **`original_query`를 원문으로 되돌리는 안은 기각** — 소비처(`subagents.py:746` · `result_aggregator.py:658` · `query_generator.py:1606` · `output_generator.py:283·643·961`)가 **축소된 질의를 기대**하며, 되돌리면 D-092 환각이 재발한다.

**LLM 프롬프트 소비자 — 전환 후보와 순서(W3)**

| 순서 | 소비자 | 현재 입력 | 전환 형태 | 선정 이유 |
|---:|---|---|---|---|
| 1 | `output_generator` 응답 서술 | `original_query or user_query`(`output_generator.py:643` 등) | **병기 — 단, 태스크 스코프 부분 프레임만**(v2 정정) | 조회 결과를 바꾸지 않는다(서술만). ⚠️ 전체 프레임을 실으면 `result_aggregator.py:651-656`의 축소(*"전체 질의를 그대로 두면 하위 task 결과만으로 전체 질문에 답한 듯 서술하는 환각"*)를 **무력화**한다 |
| 2 | `general_inference` | `user_query` | 병기 | DB 미접근 경로 · 영향 작음 |
| 3 | `query_generator` 프롬프트 | **`parsed_requirements["original_query"]`(=R6) + `parsed_requirements` JSON**(v2 정정 — `query_generator.py:1606-1610`) | **병기** — 구조 블록을 `파싱된 요구사항` 옆에 | SQL 품질에 직접 영향 · `plans/61`·`67` 측정 체계로 회귀 판정 |
| 4 | 멀티 DB 대상별 입력 | `sub_query_context`(R1) | 프레임 파생 `canonical_query(target)`와 **대조 후 채택**(§4.7) | 라우터 LLM 산출 검증 |
| 5 | 플래너 태스크 입력 | `sub_query`(R2) | 태스크별 부분 프레임 첨부 + 대조(§4.7) | 오염 실측 경로 |

- 소비자 전환은 **설정 목록으로 1곳씩**: `CANONICAL_QUERY_CONSUMERS=output_generator,general_inference,...`.
- 전환은 소비자 코드가 `get_prompt_query(state, consumer=...)` 한 함수로 채널을 고르게 해 **분기를 한곳에 모은다**(현재 흩어진 `original_query or sub_query_context or user_query` 폴백 체인을 대체).

### 4.5 렌더러 — 한 프레임, 네 가지 출력

| 출력 | 형태 | 소비자 | LLM |
|---|---|---|---|
| **`canonical_block`** | 구조 블록(한국어 라벨 고정 순서) | LLM 프롬프트 병기용 | 0 |
| **`canonical_query`** | 완결 자연문 1문장 | 대체(replace) 모드 · 라우터·플래너 대조 | 0(템플릿) |
| 해석 표시 문구 | 짧은 한 줄 + 가정 표시 | 106 H2 | 0 |
| 실행 브리프 | `ExecutionBrief` | 106 H8 | 0 |

```text
# canonical_block 예 (템플릿 · LLM 0회)
[확정된 해석]
- 의도: data_query / 목표: 현황 파악
- 대상: 공동존 김포 운영(polestar_cm_gp) · 서버 web01 (출처: 직전 턴 승계)
- 지표: CPU 사용률
- 기간: 최근 1시간 (기본값 — 사용자 미지정)
- 집계: 평균
- 산출: 표
[사용자 원문]
그 서버 CPU는?
```

- 라벨·순서·기본값 표기 문구는 **`src/prompts/`의 상수 1곳**(계층: prompts)에서 관리하고 `scripts/prompt_render_diff.py --ci`에 편입한다.
- **렌더 변형은 2종이 필수다**(v2 — R1·R2가 위치어에 **정반대 지시**를 하기 때문). 실측:
  - R2 플래너 프롬프트(`src/prompts/intent_planner.py:59-61`): *"질의에 포함된 DB 식별 신호는 `sub_query`에 **그대로 보존**하세요 … 누락하면 잘못된 DB로 라우팅됩니다"*
  - R1 라우터 프롬프트(`src/prompts/semantic_router.py:64-67`): *"`sub_query_context`에는 순수한 데이터 조회 의도만 … 위치/환경/존 정보는 **포함하지 마세요**"*

  | 변형 | 위치 라벨 | 소비자 |
  |---|---|---|
  | `canonical_query(routing=True)` | **포함** | (라우팅 입력 — **현재는 쓰지 않는다**, 아래 경계) |
  | `canonical_query(target=db_id)` | **제외** | 대상 DB 고정 후 SQL 생성 입력 |

- **라우팅 입력 경계(v2 명시)**: **라우터에는 프레임 렌더를 넣지 않는다.** D-004가 라우팅을 LLM 전용으로 두고 원문 키워드 분류를 금지하므로, 라우터 입력은 **원문 그대로**여야 한다.
  프레임에서 라우팅 입력을 파생하면 결정적 전처리가 라우팅에 개입하게 돼 D-004와 충돌한다. → 프레임은 **라우팅 결과 이후** 소비자만 담당한다(§1.2 범위와 일치).
- 블록형은 "대상" 줄을 두되 SQL 생성 프롬프트 규칙이 "대상 줄은 라우팅 정보이며 WHERE에 쓰지 말 것"을 명시 — **프롬프트 변경이므로 Ask first**.
- 공용 계층에 폴스타 스키마 리터럴을 넣지 않는다(`overfit_check`) — 라벨은 레지스트리 표시명에서 가져온다.

### 4.6 되묻기 재개 경로 일반화 (106 H1 연동)

```
[턴 N]  질의 → 프레임 구축 → 106 H1 판정: missing_slot(time_range)
        → status="clarification" 페이로드에 {pending_frame_ref, asked_slot, options} 포함
        → pending_frame은 체크포인터 스레드 상태에 저장 (클라이언트로 원문 재조합 금지)
[턴 N+1] 사용자 선택 → 요청 body의 구조화 필드 clarification_answers={slot: value}
        → merge_frame(prior=pending_frame, answers=...) → 렌더 → 실행
```

- 신규 요청 필드 `clarification_answers: dict[str, Any] | None`(`src/api/schemas.py` — `selected_db_ids`·`form_fill_answers`와 같은 계열). 값은 **옵션 목록 중 선택**만 허용(자유 입력은 G-4) — 결정적 검증(존재성·허용 집합) 후 슬롯에 들어간다.
- 존 선택(`selected_db_ids`)은 **기존 경로 유지** — 프레임 병합에서는 `clarification_answer` 출처로 읽기만 한다(두 경로 공존 · 이행은 G-5).
- 체크포인터는 **델타 병합**이므로 `pending_frame`은 요청 스코프에서 명시 초기화·소거한다(CLAUDE.md Known Mistakes — 요청 스코프 상태 초기화).

### 4.7 LLM 재작성(R1·R2·**R6**) 사후 검증

지우지 않고 **프레임과 대조**한다. **v2에서 R6(`parsed_requirements["original_query"]`)을 대상에 추가한다** — LLM이 실제로 읽는 재작성문이기 때문이다(§2.1).

| 검사 | 방법(결정적) | 실패 시 |
|---|---|---|
| 슬롯 보존 | 프레임의 식별자·지표·기간 값이 재작성문에 **정규화 일치**로 존재 | `sub_query_context` 대신 `canonical_query(target)` 사용 + 사유 기록 |
| 위치 누출 | 대상이 고정된 뒤의 재작성문에 **미선택 존 위치어**가 없는가(표면어 목록은 기존 `LOCATION_HINT_TERMS` · `src/utils/query_gen_common.py:1624` 재사용) | 동일 |
| 스코프 축소 | 재작성문의 식별자 집합 ⊆ 프레임 대상 · **프레임에 없는 식별자 추가 금지** | 동일(2026-08-04 사례 직접 차단) |
| 병합 오염 | 이번 발화 명시 위치 ≠ 재작성문 위치 | 동일(2026-07-16 사례 직접 차단) |

- 이 검사는 **라우팅 결정(`db_id` 선택)을 바꾸지 않는다** — D-004(라우팅은 LLM)와 충돌하지 않는다. 바꾸는 것은 선택된 DB에 넘길 **텍스트**뿐이다.
- 운영 초기에는 **검사만 하고 교체하지 않는 섀도 모드**(`REWRITE_VERIFY_MODE=shadow`)로 실패율부터 잰다.

### 4.8 의도 보강 (grounded enrichment) — **v2 신설 · 사용자 요청의 "정확성" 축**

v1에는 정확도를 **올리는** 트랙이 없었다(손실·오염 **방지**만 있었다 — §0.3). 문헌이 가리키는 이득은 여기 있다.

| 기법 | 실증 효과 | 출처 |
|---|---|---|
| **DB 실값·실명 근거 질문 재작성 + 실행 기반 정제** | **+12.41% / +5.38% EX** | DART-SQL(Findings ACL 2024) |
| **질의 이력 검색 편입** | **+40.2pt**(52.1→92.3 · SEDE 857문항) — 단일 기법 최대 | Schema-First Retrieval(arXiv:2606.28387) |
| 시맨틱 레이어 문서 제공 | +17~23%p | Cube 벤치마크(arXiv:2604.25149) |
| 선택적 사람 개입(되묻기) | +9.51%(Spider) | HLR-SQL(Information Systems 2026) |

> 출처: `docs/standardization_literature_review.md` §2 — **이 저장소가 이미 보유한 문헌 검토다**(v1 미참조).

**설계 — P-10을 지킨다: LLM은 후보, 결정은 결정적.**

```
[프레임 구축 후 · 게이트가 재작성 필요를 판정한 슬롯만]
  근거 수집(결정적) : db_profiles · semantic_models · synonym_seeds · column_value_index · entity_locator 결과
        ↓
  LLM 1회(구조화 출력) : {slot: [후보값…], 근거: [출처]} — 자유 서술 금지 · instructor 구조화(plans/79 E-3)
        ↓
  결정적 검증 : 값 존재성(값 인덱스) · 허용 집합(레지스트리) · 스키마 존재(profiles)
        ↓
  통과분만 SlotValue(source="llm_enrichment", model_confidence=…) 로 병합
  미통과 → unresolved 에 남긴다(P-11 · 106 H1 되묻기 후보)
```

- **재사용 부품(신설 아님)**: `entity_locator`(`src/orchestration/entity_locator.py`) · 값 인덱스(`query_generator._build_value_index_injection`, `query_generator.py:1210-1216`) · `condition_probe` 노드 · `synonym_seeds` · `select_history_fewshot`(`prompt_blocks.py:187`).
- **선행 결정과의 관계(중요)**: `docs/standardization_literature_review.md` §3.3은 DART-SQL을 *"Plan 67 트랙 S 루프의 도구 탐색과 효과 중복 가능성 있어 **별도 채택하지 않음**"* 으로 **한 번 기각**했다.
  → **이 계획은 그 기각을 뒤집지 않는다.** 차이는 산출물이다: 트랙 S는 **SQL 후보**를 탐색하고, 여기서는 **의도 슬롯**을 채운다(되묻기·해석 표시·브리프가 같은 슬롯을 쓴다).
  중복 여부는 **W2.5 착수 전 측정으로 확인**하고, 중복이면 트랙 S 산출을 슬롯으로 승격하는 쪽으로 전환한다(**G-6**).
- **과금**: LLM 1회 추가 — 기존 워커 평면. **D-127 건별 승인** 대상이며 기본 off(`INTENT_ENRICHMENT_ENABLED=false`).
- **자연문 다듬기(v1 W6)는 폐기한다** — 측정 가능한 이득 근거가 없고(문헌에도 없다), `canonical_block`은 LLM이 읽는 구조 블록이라 문장 품질이 변수가 아니다. 이 자리를 의도 보강이 대신한다.

### 4.9 감사 — `rewrite_trace`

```json
{
  "frame_hash": "…", "frame_version": 1,
  "slots": {"time_range": {"value": "1h", "source": "default"}, "...": "..."},
  "renderer": "block_v1", "mode": "shadow|augment|replace",
  "consumers": ["output_generator"],
  "gate": {"needed": true, "reason": "non_utterance_source"},
  "verify": {"r1": "pass", "r2": "fail:scope_shrink", "r6": "pass"},
  "enrichment": {"used": false, "proposed": 0, "accepted": 0, "rejected_reasons": []},
  "prompt_rev": "…"
}
```

- `plans/106` H5(`clarification_decision`)·H5.2(`intent_signal`)·H9(`prompt_rev`)와 **같은 레코드 설계 차수**에 합친다(감사 스키마 1회 변경).
- 원문 전문은 기존 `user_request` 감사에만 있고, `rewrite_trace`에는 **슬롯 값과 출처만** 남긴다(PII 정책 · D-183).

### 4.10 계층 배치 (`arch_check` 기준)

| 모듈 | 계층 | 내용 |
|---|---|---|
| `src/domain/intent_frame.py` | domain | `IntentFrame`·`SlotValue`·`merge_frame`·`rewrite_needed`(순수) · 검증 함수(§4.7 순수 부분) |
| `src/prompts/canonical_query.py` | prompts | 렌더 템플릿·라벨 상수 |
| `src/nodes/intent_frame_builder.py` 또는 기존 노드 내 호출 | application | `ParsedRequirements`·라우터 결과·레지스트리 → **변환·해소** 후 프레임 구축 · state 기록 |
| `src/orchestration/subagents.py`·`intent_planner.py` | orchestration | R1·R2·R6 대조 배선 · 태스크별 부분 프레임 |
| `src/api/schemas.py`·`routes/query.py` | interface | `raw_user_query` 기록 · `clarification_answers` 필드 · pending_frame 재개 |

- ⚠️ **계층 주의(v2)** — `src/domain/*`는 현재 **`src.domain.*` 외 내부 임포트가 0건**이다(실측). `intent_frame.py`는 이 관례를 지켜야 한다:
  `ParsedRequirements`(application)·레지스트리(infrastructure)를 **import하지 않고**, application 빌더가 **plain dict/값으로 변환해 주입**한다. `scripts/arch_check.py --ci`로 확인한다.
- **노드 신설 vs 기존 노드 내 호출**은 G-3. 권고는 **`field_mapper` 직후 공통 전단에서 1회 구축**(사다리 전 단 공통) 후 라우터 결과로 **보강**(라우터 뒤 1회 갱신). 신규 노드는 `build_graph()` 배선 변경이 필요하다.
- **D-225 ⑦ 준수**: *"새 판정·조립 로직은 **3단이 부르는 공통 함수**에 두고 1단은 같은 함수를 호출한다. 1단 전용 판정 로직 신설 금지."*
  → `merge_frame`·`rewrite_needed`·렌더러는 **전부 공통 함수**로 두고, 사다리 단별 차이는 **호출 지점**만으로 둔다(동등성은 같은 함수 호출 grep으로 확인).

### 4.11 사다리 단별 적용표 (**v2 신설** — §0.1 진단의 귀결)

| 단 | 현행 | 이 계획의 적용 | 비고 |
|---|---|---|---|
| **3단 `semantic_router`(기준 경로)** | 단일 DB 재작성 **0곳** · 멀티 DB만 R1 | **신규 도입** — 프레임 구축 + 병기(게이트 통과 시) | `plans/103` 3단 동등성의 전제를 하나 줄인다(1·2·3단이 같은 확정 의도를 받는다) |
| 1·2단(`deep_agent`·`intent_orchestration`) | R1·R2·R6 상시 발동 | **기존 재작성 검증**(§4.7) + W0.5 슬롯 승격 | 현 운영 확정 단이므로 **회귀 위험이 가장 큰 곳** — 섀도를 길게 둔다 |
| 4단 `legacy` | 재작성 없음 | 적용 없음 | — |

- **착수 순서의 함의**: 운영 `.env`는 현재 1단 확정이고(실측 2026-09-21: 세 플래그 true), 기준 경로 전환(`plans/102` L-5)은 `plans/103` P5 뒤다.
  → **W0.5(1·2단 결함 패치)가 먼저 효과를 내고**, W1~W3(프레임·병기)은 **3단 전환과 보조를 맞춘다**.
- **"3단이 레버"의 측정 근거는 이 계획이 소유하지 않는다**(D-053) — `plans/108` §1.2가 정본이다:
  run `20260918-182507`의 타임아웃 199/380턴 중 **131턴이 2단 전용 노드**(`result_aggregator` 95 · `replanner` 36)에서 사망했고 3단에는 그 노드가 없다.
  위 표는 **재작성 지점의 단별 분포**만 말한다 — 두 사실은 서로 다른 축이므로 사본이 아니며, 인용이 필요하면 108 §1.2를 가리킨다.
- **`plans/108` G-2 선례는 여기로 전이되지 않는다(§11 G-9 ㉮ 확정 · 2026-09-21)** — 108이 보류한 `CU-B1`은 **2단 전용**
  결함(`plans/108:283`)인데, W0.5가 닫는 오염은 **1단에서도 난다**(`deepagents_tools.py:33` → `SUBAGENT_REGISTRY` →
  `subagents.py:1268` 공유). **1단은 지금 트래픽을 받는 운영 확정 단**이므로 죽은 경로가 아니고 D-161 규율 대상이 아니다.
  **비대칭을 숨기지 않는다** — *"108의 보류 대상은 **2단 전용** 결함이고, 이 건은 **운영 확정 단(1단)에서도 발현**한다."*
- **다만 수명이 한정된 투자다** — 3단 전환 시 이 부류는 소멸하므로 **싸게 끝낸다**: 미승격 슬롯만, 실 트래픽 발현 빈도 순,
  발현 근거 없는 슬롯은 전환 시점 재판정(§11 G-9 범위 3항).

---

## 5. 실행 단위 (W-1~W5)

| WU | 내용 | 산출물 | 과금 | 선행 |
|---|---|---|---|---|
| **W-1** (v2 신설) | **실측 확정** — U-1·U-2 수행, §2.3·§4.4 표를 실측으로 고정. `user_query` 22개 파일의 기대 의미(원문/재작성문) 분류 · R6 소비처 전수 | §2.3·§4.4 표 개정 커밋 | 0 | — |
| **W0.5** (v2 신설 · **G-9 ㉮ 확정 · 범위 한정**) | **슬롯 승격(즉효 패치)** — 표면어 판정을 원문 기준 state 필드로 승격한다. `resolved_limit`(D-066)·`realtime_usage_intent`(D-066 후속7 · `subagents.py:820`)가 **이미 있는 두 선례**이고, W0.5는 이를 **미승격 슬롯에 일반화**한다.<br>**범위(G-9 확정 3항)**: ⑴**입력 오염 축의 미승격 슬롯만**. **실시간은 닫혀 제외**, **LIMIT 계열은 「축소 닫힘·확대 잔존」이라 우선순위에서 제외하되 W-1·U-10 확인 대상**(§2.3) ⑵**우선순위는 실 트래픽 발현 빈도**(카탈로그 218건 실측: 절대임계 21 · 순위/최상급 20 · 비교기간 8 · 파일시스템 4 · spike 1) · 발현 근거 없는 슬롯은 **3단 전환 시 재판정** ⑶`plans/98` **CU-18은 98 소유** — 107은 참조만(§6).<br>**대상 목록 확정이 W-1의 산출물**이다 | state 필드 · `resolve_*` 소비부 · 회귀 테스트 | 0 | **W-1** |
| **W0** | 골든셋·회귀 케이스 (**v2.1 축소 — 소유권 분할**) | `testdata/routing_gold/rewrite.yaml`(가칭 · **이 계획 소유**) — **슬롯 단위 채점만** 담는다: **파서·라우터 출력 스냅샷 + 기대 슬롯**으로 `merge_frame`·`rewrite_needed`·렌더를 **LLM·서버 없이** 채점(현 `testdata/routing_gold/`에는 `routing.yaml`·`decomposition.yaml` 2건만 있고 `rewrite.yaml`은 없다 — 실측 2026-09-21). 범주 ①§2.4 오염 **4건** ②지시어 후속 ③위치 전환 ④되묻기 재개 ⑤멀티 의도·순차 의존(plans/88) ⑥멀티 DB(불변식: `plans/79` §1.1) ⑦**게이트 `pass_through` 케이스**.<br>**E2E(서버 왕복) 회귀는 신설하지 않는다** — `plans/94` **§19**가 소유한다(§6). 오염 4건 중 **3건은 94 카탈로그에 이미 단언이 있다**(F-01·F-03·F-04 / B-10·A-02 / G-02 — §6 94 행) | 0 | — |
| **W1** | `IntentFrame` · `merge_frame` · `rewrite_needed` · 섀도 구축 | domain 모듈 · state 필드 `intent_frame`·`raw_user_query` · 단위 테스트(W0 스냅샷) | 0 | W0 |
| **W2** | 렌더러 · 검증 · `rewrite_trace` | prompts 모듈 · 감사 필드 · `prompt_render_diff` 편입 | 0 | W1 |
| **W2.5** (v2 신설) | **의도 보강**(§4.8) — 근거 수집 → LLM 구조화 후보 → 결정적 검증 | `INTENT_ENRICHMENT_ENABLED` · 보강 모듈 | **건별 승인**(D-127) | W2 · G-6 |
| **W3** | 소비자 전환(§4.4 순서 1→5, 1곳씩) | `get_prompt_query()` · `CANONICAL_QUERY_CONSUMERS` | 실 파이프라인 채점은 건별 승인 | W2 |
| **W4** | 되묻기 재개 일반화 | `clarification_answers` · pending_frame · 프론트 카드(106 H1과 공동) | 0 | W1 · 106 H1 |
| **W5** | R1·R2·R6 사후 검증(섀도 → 교체) + **게이트 `pass_through` 비율 보고** | `REWRITE_VERIFY_MODE` | 0(검증) | W2 |

```
W-1 ─▶ W0.5 (단독 이득 · 프레임 불필요)
  └──▶ W0 ─▶ W1 ─▶ W2 ─┬─▶ W3 (소비자 1곳씩)
                        ├─▶ W5 (R1·R2·R6 섀도 검증 + 게이트 비율)
                        ├─▶ W4 (106 H1과 동시)
                        └─▶ W2.5 (의도 보강 · G-6 확인 후)
```

> **v1 W6(LLM 자연문 다듬기) 폐기** — §4.8 참조. 이득 근거가 없고 `canonical_block`은 구조 블록이라 문장 품질이 변수가 아니다.

### 5.1 설정 (전부 기본 off = 현행 비트 동일)

```bash
INTENT_FRAME_ENABLED=false            # 프레임 구축(섀도 기록 포함)
CANONICAL_QUERY_MODE=off              # off | shadow | augment | replace
CANONICAL_QUERY_CONSUMERS=            # 전환 소비자 CSV (§4.4)
REWRITE_GATE_MODE=off                 # (v2) off | shadow | enforce  — §4.3.1 재작성 게이트
REWRITE_VERIFY_MODE=off               # off | shadow | enforce  (R1·R2·R6 대조)
INTENT_ENRICHMENT_ENABLED=false       # (v2) §4.8 의도 보강 — LLM 1회 · D-127 건별 승인
CLARIFICATION_ANSWERS_ENABLED=false   # W4 — 106 H1과 함께 켠다
```

- **W0.5는 플래그가 없다** — 슬롯 승격은 *원문 기준 판정*을 복원하는 **결함 수정**이라 "현행 동작과 비트 동일"이 목표가 아니다. 대신 W0 골든셋으로 전후를 고정하고, 변화가 생기는 케이스를 **전부 열거해 보고**한다(`plans/80` §5.4-③ 예외 — 근거를 config 주석에 남긴다).

- 기동 시 1회 해석(KV 캐시 원칙) · 설정 그룹 배치는 G-3.

### 5.2 수용 기준

| 단계 | 기준 |
|---|---|
| 공통 | off 경로 `pytest -q` 회귀 0 · `arch_check --ci` · `overfit_check --ci` · `prompt_render_diff --ci` 통과 |
| **W-1** | §2.3·§4.4 표의 **모든 행이 `파일:라인`으로 뒷받침**될 것(추정 0) |
| **W0.5** | 승격 전후 판정이 달라지는 케이스를 **전수 열거**하고, 각각이 "원문 기준이 옳다"를 만족할 것 · 1·2단에서 §2.4 LIMIT 사례와 **동형 결함이 재현되지 않을 것** · **(v2.1)** `plans/94` **V28** — 오염 4건 대응 시나리오가 회귀 0 |
| W1 | W0 전 케이스에서 **병합 결과 슬롯 = 기대 슬롯**(결정적) · §2.4 **4건 오염 재현 0** · 게이트 `pass_through` 케이스에서 **프롬프트 바이트 불변** |
| W2 | 렌더 결과 슬롯 보존 100%(자기 검증) · 렌더 결정성(같은 프레임 → 같은 텍스트) |
| **W2.5** | 보강 제안 중 **결정적 검증 통과분만** 병합될 것(미통과가 슬롯에 들어가면 실패) · 보강 on/off 비교에서 `eval_text2sql` **비열화** · 근거 없는 값 생성 0 |
| W3 | 소비자별 기존 평가 대비 **비열화**(`eval_routing` `multi_preserved` · `eval_text2sql` · **`plans/94` §19 V29**) — 기준값은 전환 전 기준선 측정으로 정한다(v1 G-8 삭제 — 게이트가 아니라 산출물). **(v2.1)** 94 시나리오 판정은 **94가 소유**하므로 이 계획은 기준선 run id와 비열화 폭만 적는다 |
| W4 | 되묻기 재개 후 **재질의 없이** 실행 완료 · 답변 외 슬롯 불변 |
| W5 | 섀도 기간 R1·R2·R6 검증 실패율 + **게이트 `pass_through` 비율** 보고 → enforce 전환은 사용자 승인. **(v2.1)** 이 비율은 하네스가 읽어야 하므로 **적재는 이 계획(§4.9 `rewrite_trace`) · 수집·리포트는 `plans/94` §19 O-e**가 소유한다 — `plans/94` §17 **O-b**가 *"제품이 싣지 않으면 감사 로그로 못 가져온다"* 를 이미 확정(`analyze.LLM_COST_UNMEASURABLE`)한 것과 같은 함정이다 |

---

## 6. 다른 계획·결정과의 관계

| 대상 | 관계 |
|---|---|
| **`plans/106`** | H3 **이관**(§1.2). H1이 판정 → **107이 재개·병합**. H2·H8은 107의 렌더러 출력을 쓴다(원천 공유 — 사본 금지). H5·H5.2·H9 감사 레코드와 `rewrite_trace`는 **한 차수**에 합친다 |
| `plans/79` | E-3c `ParsedRequirements`가 프레임의 입력 계약. 라우터 출력 계약(E-1·E-2)은 그대로 — 107은 라우팅 **결과 이후**만 다룬다. 트랙 A 프롬프트 측정(S-1)과 간섭하지 않도록 **프롬프트 변경(W3 병기 블록)은 S-1 이후** |
| `plans/50` | `conversation_context` 압축 신호가 3·3′ 순위의 입력 |
| `plans/75` | 존 역질문 재개·LIMIT 원문 승격·자연어 재조합 금지 원칙을 **일반화** — 기존 경로를 대체하지 않고 공존 |
| `plans/88` | 순차 의존 태스크는 `depends_on`으로 표현. 선행 결과가 후속 대상이 되는 슬롯은 `source="registry"`가 아니라 **실행 결과 참조**(`evidence="task:t1"`)로 둔다(G-6) |
| `plans/102` | 교차 시스템 질의는 `targets`에 시스템별 슬롯을 두고 `entity_locator` 결과를 `registry` 출처로 병합 |
| `plans/103` | 3단 LangGraph 동등성 — 프레임을 공통 전단에서 만들면 1·2·3단이 **같은 확정 의도**를 받는다(동등성의 전제가 하나 줄어든다). **§4.11 적용표가 이 계획과 103의 접점이다** |
| **`plans/94`**(v2.1) | **시나리오 하네스 정본 — 이 계획이 요구하는 하네스 측 항목은 94가 소유하는 형태로 신설했다(`plans/94` §19).**<br>①**골든셋 소유권 분할**: 이 계획은 `testdata/routing_gold/rewrite.yaml`(슬롯 단위 채점 · LLM·서버 0)만 소유하고 **E2E 시나리오는 94 `testdata/scenarios/`(218건)에 편입**한다.<br>②**§2.4 오염 4건 중 3건은 94에 이미 단언이 있다**(실측 2026-09-21): *2026-08-05 미선택 존 누출* → `f_zone_hitl.yaml:38`(F-01 `sql_must_not_match: ["(?i)여의도"]` · `:42` 주석이 **D-154를 직접 인용**)·`:102`(F-03)·`:124`(F-04 `ㅇㅇ존`)·`a_routing.yaml:27`(A-01) / *2026-07-24 LIMIT 절단* → `b_resource.yaml:165-167`(B-10 `sql_must_not_match: ["(?i)\blimit\s+\d"]` + `row_count: {min: 2000}`)·`a_routing.yaml:42`(A-02) / *2026-08-04 직전 엔티티 스코프 축소* → `g_multiturn.yaml:64-74`(G-02 턴2 `sql_must_match: ["(?i)\bin\s*\("]` / 턴3 `sql_must_not_match` 동일 패턴 — **D-153 후속1 계약 그 자체**).<br>③**미커버 1건**: *2026-07-16 은행존 알람 오라우팅* — `d_alarm.yaml`에 `db_ids` 단언이 **0건**이라 존×알람 교차 시나리오가 없다 → 94 §19 **Y-10**이 신설한다.<br>④**신설 항목(전부 94 소유 · 94의 기존 최댓값 Y-9·O-d·V27·G-12에 이어 채번)**: 단언 **Y-10~Y-12** · 수집 **O-e** · 수용 기준 **V28~V31** · 게이트 **G-13** |
| **`plans/96`**(v2.1) | run `20260915-131903` **분석 정본**(P/S/O/V 번호의 출처). 이 계획은 96의 번호 체계를 **쓰지 않는다** — 96이 확정한 P군·S군 중 재작성에 귀속되는 항목이 없다(96 P군은 스키마·프롬프트·LIMIT·타임아웃 계열). 96은 **참조만** |
| **`plans/98`**(v2.1) | **제품측 코드 수정 장부 — 앵커 경합 2건 확정.**<br>①**CU-18**(잔여 · **98이 계속 소유** · 98 G-10 소관) = `assembler.build_alarm_history_sql`(`assembler.py:1474`) 호출부(`:1562`)가 `resolve_query_limit` 결과를 **쓰지 않는다** — **「소비 누락」 축**이다(`plans/98:371-377`: *"고친 값이 소비되지 않는다" 유형*). §2.3의 `resolve_query_limit`은 **같은 슬롯의 「입력 오염」 축**이고 **축소 방향은 D-066으로 닫혀 있다**(확대 방향만 조건부 잔존 · W-1·U-10) → **107은 CU-18을 손대지 않는다.** 다만 **같은 슬롯의 반대편 축이므로 한 차수로 묶어 실행하면 검증이 한 번에 끝난다**는 실행 권고는 남긴다.<br>②**CU-15**(잔여 · 게이트 **G-11** 대기 — D-153 후속1 충돌)는 **§2.1 R4**(`subagents.py:344-352` `_inject_demonstrative_hostname`)와 **같은 경로**다 → **이 계획은 그 앵커를 건드리지 않고 98 G-11의 결론을 승계**한다.<br>③**설계 선례(경합 아님)**: CU-8은 `resolved_limit` 승격을 일반화하는 대신 **승격 의존 자체를 없앴다**(`output_generator.py:1082` `_applied_row_limit()` — 소비 시점에 `query_attempts`의 SQL에서 적용 상한을 다시 읽는다) → **W0.5도 "승격"이 유일한 해법인지 슬롯별로 판단할 것.**<br>④**경계 대칭**: 98 §0은 *"`plans/94`의 `scripts/scenario/`·`testdata/scenarios/`를 건드리지 않는다"* 를 선언한다. 이 계획도 **같은 선언**을 한다(§5 W0 · 94 §19에 기록) |
| **`plans/99`**(v2.1) | 재측정 실험 설계. 이 계획은 **독립 run을 요구하지 않는다** — 99 E-3(전 시나리오 재측정)에 **arm을 추가하지 않고**, 94 §19 V29의 비열화 판정만 기존 run 대비로 한다(추가 예산 0 · D-127 무관) |
| **`plans/108`**(v2.1) | run `20260918-182507` **정본**.<br>①**사다리 사실의 분담(D-053)**: 108 §1.2(타임아웃 199턴 중 **131턴이 2단 전용 노드**에서 사망)가 *"3단이 레버"* 의 **측정 정본**이고, 이 계획 §0.1·§4.11은 **재작성 지점의 단별 분포**만 말한다 — 축이 달라 사본이 아니다. 인용이 필요하면 108 §1.2를 가리킨다.<br>②**규율 충돌**: 108 §3.3 `CU-B1`은 *"2단 전용이고 기준 경로는 3단(D-225 · D-161 죽은 경로 투자 금지)"* 을 근거로 **보류가 사용자 확정**됐다(108 §4 **G-2**). **그러나 이 선례는 107로 전이되지 않는다** — **§11 G-9 확정(㉮ · 범위 한정)**: *"108의 보류 대상은 **2단 전용** 결함이고, 이 건은 **운영 확정 단(1단)에서도 발현**한다"*(`deepagents_tools.py:33` → `subagents.py:1268` 공유 · `.env` 실측 세 플래그 true). **비대칭을 숨기지 않고 사유를 명시**한다. 대신 **수명이 한정된 투자**로 보고 범위를 3항으로 못 박았다(미승격 슬롯만 · 발현 빈도 순 · CU-18 미접촉).<br>③**재사용**: 108 `CU-A2`(산문 응답 조기 종결 + **사유 노출** · 랜딩)가 낸 사유 노출 경로를 §4.7 재작성 검증 실패 고지에 **재사용**한다 — 신규 고지 경로를 만들지 않는다 |
| **`plans/61`·`67`·`69`**(v2 추가) | **시맨틱 IR `SMQ`와의 경계** — `src/semantic/ir.py:110` `SMQ`는 이미 `entities·dimensions·measures·filters·time_grain·time_range·order_by·limit`을 담는 **구조 정본**이다. 겹침이 명백하므로 경계를 선언한다: **`IntentFrame` = 라우팅 이전 · 스키마 비결합 · 출처/불확실성 보유** / **`SMQ` = 스키마 결합 · 컴파일 대상**. 방향은 `IntentFrame → SMQ`(역방향 금지)이며, 두 모델이 같은 슬롯을 각자 텍스트에서 재유도하지 않게 한다(§2.3의 `semantic_compiler` 3종이 그 재유도다). QBridge의 *"SQL-aligned intermediate representation"* 이 이 자리의 `SMQ`에 해당한다(§10 L-2) |
| **`docs/standardization_literature_review.md`**(v2 추가) | canonicalization 문헌 정본. §4.8 보강 수치·§3.7 표준화의 역설·§3.6 자동 등록 금지 근거를 **여기서 인용**한다(사본 금지 — 해석만 이 계획에 남긴다) |
| **D-035**(v2 추가) | *"통계·1차 분류는 Python 결정적, LLM은 해석만"* — §4.8의 "LLM=후보 생성, 결정=결정적"(P-10) 배치가 이 결정을 따른다 |
| **D-225 ⑦**(v2 추가) | *"새 판정·조립 로직은 3단이 부르는 공통 함수에 두고 1단은 같은 함수를 호출"* — §4.10 배치 제약 |
| D-004 | 프레임 구축·검증은 라우팅 결정을 바꾸지 않는다. 원문 키워드 분류 없음(§4.3 3′의 기존 표면어 판정은 현행 유지 · G-6) |
| D-053 | 렌더 출력 4종은 **같은 프레임에서 파생** — 해석 표시·브리프·재작성문이 서로 사본이 되지 않는다 |
| D-151 · Plan 75 §4 | 되묻기 답변은 구조화 필드로만(§4.6) |

---

## 7. 위험

| ID | 위험 | 영향 | 완화 |
|---|---|---|---|
| R-1 | 병기 블록이 프롬프트를 길게 만든다 | 토큰·지연 증가 · FabriX 입력 한도 | 블록은 슬롯 수만큼(수십 토큰 수준 — **추정**) · 소비자별 켜기 · `plans/50` B3 상한과 합산 측정 |
| R-2 | 병기 시 원문과 해석이 **충돌**하면 LLM이 원문을 따를 수 있다 | 해석 무효화 | 블록에 "해석은 확정값이며 원문보다 우선" 문구 — **프롬프트 변경이므로 Ask first** · W3 측정 |
| R-3 | 프레임 필드와 파서 자유 형식(`filter_conditions`) 간 불일치 | 병합 누락 | E-3c D3처럼 **중첩 항목은 느슨하게** 받고 상위 슬롯만 타입화 |
| R-4 | 소비자 전환 중 `user_query`·`original_query`·`canonical_query` 3채널 혼재 | 복잡도 증가 | `get_prompt_query()` 단일 진입 · 전환 완료 소비자 목록을 config로 가시화 · 잔여를 이 계획 태그로 추적 |
| R-5 | pending_frame 상태 누수(다른 요청으로 승계) | 잘못된 재개 | 요청 스코프 명시 초기화 · 재개 요청에 `pending_frame_ref` 일치 검증 |
| R-6 | 템플릿 자연문이 어색해 SQL 생성 품질 저하 | 품질 열화 | 기본은 병기(원문 유지) · replace는 측정 후 소비자별 · **병기 모드에서는 원문이 그대로 남으므로 노출 제한적**(v2 — 자연문 다듬기 옵션은 §4.8로 대체) |
| R-7 | 경로 비대칭(3단만 적용) | 사다리 단별 거동 차이 | 공통 전단 1회 구축(G-3 권고) · 2단 R2 대조 포함 · **§4.11 적용표로 단별 책임 명시**(v2) |
| **R-8**(v2) | **재작성 누적 drift** — 턴이 쌓이며 재작성이 재작성을 낳아 원 의도에서 멀어진다 | 멀티턴 품질 붕괴 | 문헌이 임계점을 보고한다(query drift · arXiv:2605.00560 · §10 L-7). 완화: **모든 렌더는 `raw_user_query`에서 다시 파생**(재작성문을 입력으로 재작성 금지) · 게이트(P-9) · 턴별 `frame_hash` 감사 |
| **R-9**(v2) | 의도 보강(§4.8)이 **없는 값을 만들어낸다**(환각 슬롯) | 오답을 확신 있게 제시 | 결정적 검증(값 인덱스·레지스트리·profiles) 통과분만 채택 · 미통과는 `unresolved`(P-11) · 보강 전후 `eval_text2sql` 대조 |
| **R-10**(v2) | `IntentFrame`이 `SMQ`와 갈라져 **슬롯 정본이 둘**이 된다 | D-053 위반 · 드리프트 | §6 경계 선언 + `IntentFrame → SMQ` 단방향 · 같은 슬롯을 두 모델이 각자 텍스트에서 재유도하지 않게 검사(W2 테스트) |

---

## 8. 되돌리기

- 모든 단계가 플래그 뒤에 있다. `CANONICAL_QUERY_MODE=off`면 프레임이 있어도 **어떤 소비자도 읽지 않는다**.
- `user_query` 의미를 바꾸지 않으므로(원칙 P-2) 되돌릴 때 **데이터 이행이 없다**.
- 감사 필드 추가는 **추가 전용**(기존 필드 변경 없음).

---

## 9. 착수 전 확인 · 미확인 사항

| ID | 항목 | 확인 방법 |
|---|---|---|
| U-1 | ~~§2.3 결정적 함수가 재작성문을 받았을 때 판정이 달라지는지~~ → **v2에서 해소.** 1·2단에서 **이미 재작성문을 받고 있음이 실측 확인**됨(§2.3). 남은 것은 *"어떤 케이스에서 결과가 달라지는가"* 의 전수 열거 | **W-1·W0.5의 작업 항목으로 이관** — 추정이 아니라 확정 사항 |
| U-2 | `user_query`를 읽는 22개 파일의 **실제 의미**(원문 기대 / 재작성문 기대) 분류 | 파일별 호출부 정독 — 결과를 §2.3·§4.4 표에 반영(**W-1**) |
| U-3 | 존 역질문 재개 시 원 질의·선택값이 체크포인터/요청 중 어디로 흐르는지 전 경로 | `routes/query.py:807 · 949 · 1130` 주변 정독 |
| U-4 | 프론트가 `clarification_answers` 제출용 카드를 존 역질문 카드 변형으로 그릴 수 있는지 | `src/static/` 클라이언트 분기 실측(106 U-9와 동일 작업) |
| U-5 | 병기 블록 추가 시 각 프롬프트의 토큰 증가량 | `prompt_render_diff` 렌더 결과 길이 비교 |
| U-6 | 순차 의존(`plans/88`)에서 선행 결과를 프레임 슬롯으로 참조할 때 D-203 선행 결과 게이트와의 순서 | `sequential_runner` 정독 |
| **U-7**(v2) | 의도 보강(§4.8)이 `plans/67` 트랙 S 도구 탐색과 **실제로 중복인지** | 두 산출물(SQL 후보 vs 의도 슬롯)을 같은 골드셋에 돌려 비교 — **G-6 판단 입력** |
| **U-8**(v2) | `SMQ`의 어느 필드가 `IntentFrame` 슬롯에서 **직접 파생 가능**한지(재유도 제거 범위) | `semantic_compiler.py:276·404·407`의 표면어 판정을 슬롯 입력으로 치환 가능한지 실측 |
| **U-9**(v2) | 게이트 `pass_through` 비율(현행 무조건 재작성의 헛일 비율) | 섀도 기간 `rewrite_trace.gate` 집계(**W5**) |
| **U-10**(v2.3) | **`src/tools/binding.py:161`의 빈 `query` 폴백이 실제로 발동하는지** — 실측 확정분: `resolve_limit(query or context.user_query, default_limit=context.default_limit)`에서 **LLM이 빈 값을 넘기면 `context.user_query`로 폴백**하고, 그 값은 `column_deriver.py:130-142`의 `ToolContext(user_query=…)` = `query_generator` 경로의 `state["user_query"]`(1·2단 **재작성문**)다. `default_limit`은 승격값(`deps.default_limit`)이라 **`semantic_compiler.py:276`과 정확히 같은 비대칭**(축소 닫힘 · 확대 잔존)이고, 여기엔 **빈 `query` 폴백이 하나 더** 얹힌다. 미확정은 *"LLM이 실제로 빈 값을 넘기는 빈도"* 와 *"확대 방향 성립 사례가 있는가"* 다(**추정**) | 섀도 로그로 `resolve_limit` 도구 인자 분포 수집(빈 값 비율) + 확대 방향 성립 사례 탐색 — **W-1**. 성립하면 **G-9 범위를 다시 연다**(그때는 `semantic_compiler.py:276`·`binding.py:161` 2곳이 대상) |

---

## 10. 근거 문헌 — **v2 전면 재검토**

### 10.1 v1 인용 정정

| v1 인용 | v2 재확인 | 조치 |
|---|---|---|
| *The Case for Intent-Based Query Rewriting*(arXiv:2511.20419)를 "의도 기반 재작성" 근거로 인용 | **주제 불일치.** 이 논문의 "intent"는 **SQL 질의의 분석 의도**이고 재작성 대상도 **SQL→SQL**이다(접근 제어·비용 제약 우회 목적, 시스템명 INQURE). *"alter the structure and syntactic outcome of an original query while keeping the obtainable insights intact"* — **사용자 프롬프트 재작성과 무관** | **인용 철회** |
| Ma et al. *Query Rewriting for RAG*(EMNLP 2023) — "서지만 확인" | RAG 검색 질의 재작성. 이 계획 대상(프롬프트 의도 확정)과 층위가 다르다 | **참고로 격하** |
| 저장소 자체 문헌 자산 미참조 | `docs/standardization_literature_review.md` 등 **도메인 실증 수치가 이미 저장소에 있었다** | **§10.2에 편입** |

### 10.2 문헌이 말하는 것 (확인 수준 표기)

| # | 근거 | 확인 | 이 계획에서 쓴 곳 |
|---|---|---|---|
| **L-1** | `docs/standardization_literature_review.md` §2 — **DB 실값 근거 질문 재작성 +12.41%/+5.38% EX**(DART-SQL, Findings ACL 2024) · **질의 이력 편입 +40.2pt**(arXiv:2606.28387) · 시맨틱 레이어 +17~23%p · **되묻기 +9.51%**(HLR-SQL) | 저장소 정본 | **§4.8 의도 보강의 이득 근거** · §0.3 "정확성" 축 |
| **L-2** | **QBridge**(ACL 2026 · `aclanthology.org/2026.acl-long.402/`) — 노이즈 질문을 *"Gold Query — a **structured, SQL-aligned intermediate representation**"* 으로 재작성, **실행 검증** 후 *"conservatively refines"* | 요약 | §1.1 · **P-1 "정본은 구조, 텍스트는 파생"의 도메인 최신 지지** · §6 `SMQ` 경계 |
| **L-3** | **B-1 Mediator**(arXiv:2602.07338) — *"explicate user inputs into explicit, **well-structured instructions**"* · *"root cause lies in an **intent alignment gap** rather than intrinsic capability deficits"* · *"scaling model size or improving training alone cannot resolve this gap"* | 요약 | §1.1 · 의도 확정 층이 **모델 개선으로 대체되지 않는다**는 근거 |
| **L-4** | **A-3 SAGE-Agent**(arXiv:2511.08798) — **specification uncertainty(사용자가 안 정함) vs model uncertainty(LLM이 확신 못 함) 분리** · EVPI로 질문 선택 · *"7-39% higher coverage"* · **되묻기 1.5~2.7배 감소** | 요약 | **§4.2 `SlotValue.spec_uncertain`·`model_confidence`**(v2 신설) · 106 H1 판정과 원천 공유 |
| **L-5** | *Detecting Ambiguities to Guide Query Rewrite …*(arXiv:2502.00537 · **Adobe Experience Platform AI Assistant 실배포**) — 재작성을 **모호성 분류기 뒤에 게이트**, *"not risking unnecessary insertions of unwanted phrases for clear queries"* | 요약 | **§4.3.1 재작성 게이트(P-9)의 직접 근거** · 106 부록 A.1에 "열지 않음"으로 남아 있던 항목 |
| **L-6** | **REWRITER**(arXiv:2412.17068) — NL2SQL 질문 재작성. **Checker**가 결함 질의를 먼저 선별해 *"unnecessary rewriting and potential hallucinations"* 최소화. 이득 **Spider +1.6% · BIRD +2.0% EX** | 요약 | **§4.3.1 게이트** · **§0.3 손익 비대칭의 분자**(자유 재작성 기대 이득 ≈ 2%p) |
| **L-7** | **query drift**(arXiv:2605.00560, `docs/standardization_literature_review.md` §3.6 경유) — **재작성 누적의 해로운 임계점** · 랭커 피드백 게이팅 제안 | 저장소 정본 경유 | **R-8 위험** · "재작성문을 입력으로 재작성 금지" 규칙 |
| **L-8** | **RECAP**(arXiv:2509.04472) — 대화를 *"concise representations of user goals"* 로 재작성. 대상 결함 **ambiguity · intent drift · vagueness · mixed-goal** · prompt 기반 + **DPO 파인튜닝 재작성기** | 요약 | **W0 케이스 범주 4종** · 파인튜닝 없는 이 계획의 한계 표시 |
| **L-9** | CLEAR(ICDE 2025) · AmbiSQL(arXiv:2508.15276) — NL2SQL 모호성: **탐지 → 선택지 되묻기 → 재구성** 파이프라인 | 서지 | §4.6 "선택지만"(G-4)이 도메인 표준 형태임을 확인 |
| **L-10** | `docs/standardization_literature_review.md` §3.7 — 표준화의 역설: (가)precision-recall (나)ontology drift (다)**커버리지 절벽** *"조용한 오답보다 명시 실패가 낫다"* | 저장소 정본 | **P-11**(v2) · `unresolved` 설계 |
| L-11 | 106 P-6 OpenAI Deep Research — 명확화 → 상세 지시문 재작성 → 실행 | 요약 | §1.1 · 렌더러의 "실행 모델은 확정 의도를 받는다" |

### 10.3 방향 판정 — 문헌 대비 이 계획은 맞는가

| 설계 결정 | 문헌 판정 | 근거 |
|---|---|---|
| **구조(IntentFrame)를 정본으로, 텍스트는 파생** | ✅ **지지 · 도메인 최신 합의** | L-2 QBridge(SQL-aligned IR) · L-4 SAGE(슬롯 공간) · L-3 Mediator(well-structured instructions) |
| **재작성을 게이트한다** | ✅ **지지 · v1에 없던 필수 요소** | L-5 Adobe 실배포 · L-6 REWRITER Checker |
| **자유 서술 LLM 재작성 미채택** | ✅ **조건부 지지** — 문헌의 자유 재작성 이득은 +1.6~2.0%p(L-6)인데 이 저장소 실측 손실은 훨씬 크다(§2.4) | L-6 수치 + §2.4 |
| **LLM을 슬롯 보강에 쓴다(§4.8)** | ✅ **강하게 지지 · v1의 공백** | L-1 DART-SQL +12.41%/+5.38% · 이력 편입 +40.2pt |
| **슬롯에 불확실성 2종을 싣는다** | ✅ 지지 | L-4 specification vs model uncertainty |
| **되묻기는 선택지만** | ✅ 지지 | L-9 CLEAR·AmbiSQL · §2.5 저장소 원칙 |
| 파인튜닝된 재작성기 | ❌ **미채택** — L-8 RECAP는 DPO 재작성기로 추가 이득을 보고하지만 폐쇄망·운영 제약상 범위 밖 | 한계로 명시 |

> **결론**: v1의 방향(구조 정본 + 템플릿 렌더 + 원문 병기)은 **문헌과 어긋나지 않는다** — 오히려 도메인 최신 문헌(L-2)이 같은 형태로 수렴했다.
> v1에 **없던 것은 둘**이다: **①재작성 게이트**(L-5·L-6) **②의도 보강**(L-1). v2가 이 둘을 추가한다.
> 그리고 v1이 *"문헌은 LLM 자유 재작성을 기본으로 한다"* 고 적은 것은 **v2 재검토로 부정확함이 드러났다** — 도메인 문헌은 이미 구조화·게이트 방향이다.

---

## 11. 사용자 확정 게이트 — **전건 확정 (2026-09-21 · 권고안 채택)**

> **2026-09-21 사용자 지시**: *"계획서의 사용자 확정 게이트는 권고안대로 확정 처리하라. 대기 상태로 남기지 말 것."*
> 아래 권고가 그대로 결정이다. **표기는 `plans/108` §4(같은 지시로 확정된 선례)의 형식을 따른다.**
>
> **두 가지를 구분한다.**
> - **게이트 대기 = 없다.** 무엇을 할지에 대한 사람 판단은 전부 닫혔다.
> - **「측정 산출물 대기」 = G-5 · G-6 두 건.** 이 둘은 *"측정 후 결정한다"* 가 **절차로 확정**된 것이다 —
>   착수를 막지 않으며, 측정 결과를 받은 뒤에 **한 번 더** 사용자에게 올리는 지점만 남는다(§11.1).
>
> **신규 D-번호는 부여하지 않았다** — 확정 내용을 `docs/02_decision.md`에 등재할지는 별건이며, 등재 시 「채번 이력」 선행이 필요하다(D-161 부기).

| ID | 물음 | 권고 = **확정** | 확정 후 처리 |
|---|---|---|---|
| **G-1** | 채택 범위 — (a) W-1+W0.5만 (b) +W0~W2 섀도 (c) +W3·W4 (d) +W2.5 의도 보강 | **(a) 단독 선행 착수 → 결과 보고 후 (b)(c).** (a)는 프레임 승인과 무관하게 **현행 결함을 닫는다**(§2.3) | **확정** — 착수 단위는 **W-1 → W0.5**. ⚠ **W0.5의 범위는 G-9(㉮ 범위 한정)가 정한다** — 입력 오염 축의 **미승격 슬롯만**, 발현 빈도 순. 두 확정을 합치면 **"W-1 전수 실측 → W0.5 미승격 슬롯 승격(발현 빈도 순)"** 이 선행 차수이고, 발현 근거 없는 슬롯만 3단 전환 시 재판정이다 — **모순이 아니라 중첩**이다 |
| **G-1.5** | 사용자 요청의 "LLM 재작성"을 어떤 형태로 받을지 | **㉮ 결정적 렌더 유지 + 의도 보강(§4.8)으로 LLM 활용.** ㉰(자유 서술 재작성)는 기대 이득 **+1.6~2.0%p**(L-6)인데 이 저장소 실측 손실은 **행 57% 절단·오라우팅·스코프 붕괴**다(§0.3 손익 비대칭). ㉯는 요청의 "정확성" 축을 비운다 | **확정** — LLM은 **슬롯을 채우고**, 텍스트는 코드가 찍는다(§0.4). 자유 서술 재작성 신설 **금지**가 이 계획의 고정 제약이 된다. 단 **기존 R1·R2·R6은 지우지 않는다**(G-5) |
| **G-2** | 재작성 적용 형태 | **병기(augment) 기본.** 원문을 버리는 대체(replace)는 **측정 후 소비자별로만** | **확정** — `CANONICAL_QUERY_MODE` 기본 `off`, 전환 시 `augment`가 기본값. `replace`는 소비자별 측정치 없이는 켜지 않는다(§4.4) |
| **G-3** | 프레임 구축 위치 · 설정 그룹 | **기존 노드 내 호출**(`field_mapper` 뒤 공통 전단 1회 구축 + 라우터 뒤 1회 보강). 신규 노드는 `build_graph()` 배선 변경이 필요해 회귀면이 넓다 | **확정** — **D-225 ⑦ 준수**: `merge_frame`·`rewrite_needed`·렌더러는 **3단이 부르는 공통 함수**에 두고 1·2단은 같은 함수를 호출한다. **1단 전용 판정 로직 신설 금지.** 배치는 §4.10 표대로이며 `arch_check --ci`로 확인한다 |
| **G-4** | 되묻기 답변 입력 형태 | **선택지만.** 자유 입력은 파서 재호출을 부르고, 이는 **자연어 재조합 금지 원칙**(§2.5 · `schemas.py:39-49` · Plan 75 §4 · D-151)과 충돌한다. L-9(CLEAR·AmbiSQL)가 도메인 표준으로 확인 | **확정** — 답변은 **구조화 필드 → 프레임 슬롯**으로만 들어간다(§4.6). 프론트 카드도 선택지형으로 그린다(U-4) |
| **G-5** | 기존 재작성 R1·R2·R6 처분 시점 | **섀도 검증을 유지하고, 실패율 + 게이트 `pass_through` 비율을 보고한 뒤 enforce를 재판단한다** — 이 **절차가 확정**이다. 지금 enforce로 바로 가지 않는다 | **확정(절차)** · **측정 산출물 대기** — `REWRITE_VERIFY_MODE=shadow`로 W5를 돌려 산출물을 만든다. **enforce 전환만** 그 산출물을 받은 뒤 사용자에게 다시 올린다(§11.1). 기존 R1·R2·R6은 **지우지 않는다** |
| **G-6** | 의도 보강(§4.8)이 `plans/67` 트랙 S와 중복인지 | **U-7 측정 후 결정한다** — 이 **측정 선행이 확정**이다. `docs/standardization_literature_review.md` §3.3이 DART-SQL을 "중복 가능성"으로 **한 번 기각**했으므로 그 기각을 존중하고 **측정으로 다시 연다**(추정으로 뒤집지 않는다) | **확정(절차)** · **측정 산출물 대기** — U-7(두 산출물을 같은 골드셋에 돌려 비교)이 W2.5의 선행이다. **W2.5는 D-127 건별 승인 경로**이므로 측정 설계까지가 이번 확정 범위다 |
| **G-7** | R3 치환본(존 표기 결정적 치환)을 `display_query`로 분리할지 | **예** — 원문 채널(`raw_user_query`)을 **순수 원문**으로 유지해야 §2.3의 표면어 판정이 흔들리지 않는다 | **확정** — R3 산출물은 `display_query`로 분리하고 `raw_user_query`는 불변으로 둔다(§4.4) |
| ~~G-8~~ | ~~W3 비열화 판정 기준값~~ | **삭제**(v2) — 게이트가 아니라 W3 산출물이다 | 기준값은 전환 전 기준선 측정으로 정하고 `plans/94` **V29**가 판정한다 |
| **G-9**(v2.1 신설 · **v2.3 근거 정정**) | **1·2단 전용 결함(§2.3)에 투자하는가** — `plans/108` §4 **G-2**가 *"2단 전용 결함은 보류, 기준 경로는 3단"*(D-225 · D-161)을 **이미 사용자 확정**했고, W0.5가 닫는 오염도 1·2단에서만 난다 | **㉮ 착수 — 단 범위를 못 박는다.** ①**죽은 경로가 아니다**: 108 G-2가 보류한 `CU-B1`은 **2단 전용**(`plans/108:283`)인데, W0.5가 닫는 오염은 **1단에서도 난다**(`deepagents_tools.py:33` → `SUBAGENT_REGISTRY` → `subagents.py:1268` 공유). **1단은 지금 트래픽을 받는 운영 확정 단**(`.env` 실측 2026-09-21 세 플래그 true)이므로 **D-161 규율의 적용 대상이 아니고 108 G-2 선례는 전이되지 않는다.** ②**단 수명이 한정된 투자**다 — 3단 전환 시 이 부류는 소멸(3단 재작성 0곳 · §0.1)하므로 **싸게 끝내야 한다.** ③**v2.1의 ㉰(=`resolve_query_limit` 1종만)는 기각**한다 — CU-18은 **「소비 누락」** 축(`plans/98:371-377` · `assembler.py:1474`·호출부 `:1562`)이고 W0.5는 **「입력 오염」** 축인데, LIMIT 계열의 입력 오염은 **축소 방향이 D-066으로 닫혀** 있어(§2.3) ㉰가 새로 닫는 것은 **조건부 확대 노출 하나**뿐이다. 그 하나에 차수를 쓰는 것보다 **발현 빈도가 실측된 슬롯을 먼저 닫는 편**이 같은 비용으로 더 많은 오염을 없앤다 | **확정** — **범위 3항**: ⑴대상은 **입력 오염 축의 미승격 슬롯만**. `resolve_effective_limit`(D-066)·`is_realtime_usage_query`(D-066 후속7 · `subagents.py:820`)는 **닫혀 제외**, **`resolve_query_limit`은 「축소 닫힘·확대 잔존」이라 우선순위에서 제외하되 W-1 확인 대상**으로 남긴다(성립 사례가 나오면 다시 연다 · **U-10**) ⑵**우선순위는 실 트래픽 발현 빈도** — 카탈로그 218건 실측(절대임계 21 · 순위/최상급 20 · 비교기간 8 · 파일시스템 4 · spike 1). **발현 근거가 없는 슬롯은 3단 전환 시점 재판정**으로 미룬다 ⑶**`plans/98` CU-18은 98이 계속 소유**한다(98 G-10 소관) — 107은 **참조만** 하고 손대지 않는다. **목록 확정은 W-1의 산출물**이다 |

### 11.1 확정 이후에 남는 것 — 「측정 산출물 대기」 2건

**게이트 대기는 0건이다.** 아래 둘은 *무엇을 할지*가 아니라 *측정 결과를 받고 한 번 더 확인할 지점*이다.

| 출처 | 산출물 | 그 산출물로 되묻는 것 | 막히는가 |
|---|---|---|---|
| **G-5** | W5 섀도 보고 — R1·R2·R6 검증 실패율 + 게이트 `pass_through` 비율(`plans/94` **O-e**가 수집) | `REWRITE_VERIFY_MODE`를 `enforce`로 올릴지 | **아니다** — 섀도 착수는 확정이다 |
| **G-6** | U-7 비교 — 의도 보강 산출(의도 슬롯) vs `plans/67` 트랙 S 산출(SQL 후보)을 같은 골드셋에 | W2.5를 독립 착수할지, 트랙 S 산출을 슬롯으로 승격할지 | **아니다** — 측정 설계·실행이 확정 절차다. 단 실 파이프라인 구동은 **D-127 건별 승인** |

---

## 부록. 변경 이력

| 일자 | 버전 | 내용 |
|---|---|---|
| 2026-09-20 | v1 | 최초 작성 — 재작성 지점 5곳·원문 보존 장치·원문 의존 결정적 함수 10종·오염 실측 3건 실측 · `IntentFrame`·병합 우선순위·이중 채널·렌더러 4종·R1/R2 사후 검증·되묻기 재개 일반화 설계 · W0~W6 · 게이트 G-1~G-8 · `plans/106` H3 이관 수용 |
| 2026-09-21 | **v2** | **코드 재실측 + 문헌 재검토 개정.** ①**R6 발굴**(`parsed_requirements["original_query"]` 교체 — LLM이 실제로 읽는 재작성문) ②**§2.2 정정**(`original_query`는 원문 채널이 아니다 → `raw_user_query` 신설) ③**§2.3 격상**(미래 위험 → **현행 결함** · `semantic_compiler` 3종 추가로 10→13종) ④**§0.1 정정**(3단 기준 경로에는 재작성이 없다 — 사용자 전제가 기준 경로 기준으로 정확) ⑤**§4.3.1 재작성 게이트 신설**(P-9) ⑥**§4.8 의도 보강 신설**(v1 W6 자연문 다듬기 폐기) ⑦**§4.2 arch 위반 정정**(domain→application 역방향) + 불확실성 2종 ⑧**§4.11 사다리 단별 적용표** ⑨**§6 `SMQ` 경계 선언** ⑩**§10 문헌 전면 개정**(arXiv:2511.20419 인용 철회 · 저장소 문헌 자산 편입 · 도메인 문헌 L-1~L-10) ⑪**W-1·W0.5·W2.5 신설** · G-1.5 신설 · G-8 삭제 |
| 2026-09-21 | **v2.1** | **94 계열(94·96·98·99·108)과의 중복·영향 정리.** 설계·실측은 v2 그대로이고 **경계와 소유권만** 얹었다. ①**§6에 5행 추가** — 골든셋 소유권 분할(E2E는 94, 슬롯 채점은 이 계획) · §2.4 오염 4건 중 **3건이 94 카탈로그에 이미 커버**됨을 `파일:라인`으로 확정, 미커버 1건(은행존 알람 오라우팅)은 94 §19 Y-10으로 이관 ②**앵커 경합 2건 확정** — `plans/98` CU-18(같은 함수 `resolve_query_limit`의 반대편 · W0.5와 한 차수) · CU-15(R4와 같은 경로 · **G-11 결론 승계, 미접촉**) ③**§11 G-9 신설** — W0.5의 1·2단 전용성이 `plans/108` G-2 확정(2단 전용 결함 보류)과 같은 잣대에 걸린다 ④**§5 W0 축소 · W0.5 선행 추가 · §5.2 수용 기준을 94 V28~V31로 연결** ⑤**§4.11에 108 §1.2 참조 추가**(사다리 측정 정본은 108 · 이 계획은 재작성 분포만 — D-053) ⑥신규 D-번호 **0건** |
| 2026-09-21 | **v2.2** | **사용자 확정 게이트 전건 확정**(사용자 지시 *"계획서의 사용자 확정 게이트는 권고안대로 확정 처리하라. 대기 상태로 남기지 말 것."*). ①**§11 전면 개정** — G-1·G-1.5·G-2~G-7·**G-9** 를 **권고안 그대로 확정**하고 `plans/108` §4 형식(`권고 = 확정` / `확정 후 처리`)으로 옮겼다. 각 행에 **확정 근거**를 함께 적었다 ②**G-5·G-6은 「게이트 대기」가 아니라 「측정 산출물 대기」**로 성격을 바꿨다 — *"측정 후 결정"* 이라는 **절차 자체가 확정**이며 착수를 막지 않는다(**§11.1 신설**) ③**G-1(a) ↔ G-9 중첩 해소** — 선행 차수는 **W-1 전수 실측 + W0.5**이고, 나머지는 3단 전환 시 재판정이다(당시 G-9는 ㉰였고 **v2.3에서 ㉮로 정정**) ④확정을 **§0.2 순서 · §2.3 12종 처분 · §4.11 · §5 W0.5 · §6 `plans/108` 행**에 전파했다 ⑤**헤더 상태 줄**을 「전건 확정 · 측정 산출물 2건 잔여」로 교체하고 **파일명 `-TODO` 는 유지**(코드 0건) ⑥신규 D-번호 **0건**(확정 내용의 `docs/02_decision.md` 등재는 별건 — 「채번 이력」 선행 필요) |
| 2026-09-21 | **v2.3** | **G-9 근거 정정 + §2.3 재실측**(사용자 확정 · **최종**). ①**G-9 = ㉮(범위 한정) 확정** — v2.1의 ㉰(=`resolve_query_limit` 1종만) 근거는 **축(axis) 혼동**이었다: CU-18은 **「소비 누락」**(`plans/98:371-377` · `assembler.py:1474`·`:1562`)이고 W0.5는 **「입력 오염」**인데, LIMIT 계열의 입력 오염은 **축소 방향이 D-066으로 닫혀** 있어 ㉰가 새로 닫는 것은 **조건부 확대 노출 하나**뿐이다 — 같은 비용이면 **발현 빈도가 실측된 슬롯을 먼저** 닫는 편이 낫다 ②**LIMIT 판정은 방향별로 갈린다(단정 교정)** — 본문(`query_gen_common.py:520-527`)이 건수(`_EXPLICIT_COUNT_RE`/`_TOP_N_RE`)·전체스코프(`has_all_scope_keyword`) 매치 시 `:524`·`:526`에서 **`default_limit`에 닿기 전에 반환**하므로 승격값은 **미매칭 폴백**(`:529`·`:531`)에서만 효력이 있다. **축소 방향 닫힘 ✅ / 확대·변조 방향 잔존 ⚠** → *"실질 닫힘"* 이 아니라 **W-1 확인 대상**으로 표기하고, 잔존이 조건부라 **W0.5 우선순위에서만 제외**한다 ③**U-10 유지(앵커 `binding.py:161`)** — `resolve_limit(query or context.user_query, …)`의 **빈 `query` 폴백**이 `column_deriver.py:130-142`의 `ToolContext(user_query=…)`(=1·2단 재작성문)로 떨어진다. `semantic_compiler.py:276`과 **같은 비대칭 + 폴백 하나 추가**. 성립하면 **G-9 범위를 다시 연다** ④**§2.3 독립 정정** — `is_realtime_usage_query`는 **이미 승격**(`subagents.py:820`이 교체 지점 `:1268` **앞**에서 원문 판정 → `realtime_usage_intent` · `:1215`·`:1244`가 소비 · D-066 후속7). 호출부 라인 오기 2건(`multi_db_executor.py:2388`→`:2394` · `query_generator.py` 5종은 `:607·611·618·621·656`)·**누락 호출부 `sql_validation.py:193`** 표기 ⑤**`plans/108` G-2 비대칭 사유 명시**(§4.11 · §6 · §11) ⑥`has_all_scope_keyword`·`refers_to_demonstrative_server`는 **단정하지 않고 W-1 이관** ⑦우선순위는 실 트래픽 발현 빈도(절대임계 21 · 순위/최상급 20 · 비교기간 8 · 파일시스템 4 · spike 1 — 카탈로그 218건 실측) |
