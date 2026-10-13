# 142. 포탈 기능 질의응답 — 채팅에서 AIOps 포탈의 기능·화면·용어를 물으면 매뉴얼을 근거로 사용 방법(입력 프롬프트 포함)과 개념을 답한다

> **작성일**: 2026-10-07 · **v1.1**
> **상태**: **WIP — W0~W7 구현 완료(2026-10-13 · 2026-10-14 main 병합 — 매뉴얼 번호 U-61·A-76으로 재부여(main 선점))** · D-315 등재 · 계획 끝 회귀·독립 검증(Critical 0)·교정 완료(§11.5) · **잔여**: 폐쇄망 확인(FabriX 서술 품질 · 운영 `.env` 사다리 단 · M-2 FabriX 재측정) · 사례 2건 `human_review`(사람) · pu-05·pu-14·pa-06 미해결
> **계획 시작 SHA**: `81a4562`(2026-10-13 · 브랜치 `RCA`)
> **회귀 대상 파일**: `.env.example` · `config/db_registry.yaml` · `scripts/manual/build.py` · `scripts/manual/cases.yaml` · `scripts/manual/content/admin.md` · `scripts/manual/content/user.md` · `scripts/manual/features.yaml` · `src/api/settings_catalog.py` · `src/config.py` · `src/graph.py` · `src/nodes/field_mapper.py` · `src/orchestration/conditional_agents.py` · `src/orchestration/deep_agent.py` · `src/orchestration/deepagents_tools.py` · `src/orchestration/doc_command.py` · `src/orchestration/intent_planner.py` · `src/orchestration/replanner.py` · `src/orchestration/subagents.py` · `src/prompts/intent_planner.py` · `src/prompts/orchestrator.py` · `src/routing/registry.py` · `src/static/index.html` · `src/static/js/app.js` · `src/static/manual/admin.html` · `src/static/manual/user.html` · `tests/test_api/test_settings_catalog.py` · `tests/test_cross_system/test_ownership_task.py` · `tests/test_cross_system/test_router_prompt_ownership.py` · `tests/test_nodes/test_field_mapper_node.py` · `tests/test_orchestration/test_deep_agent.py` · `tests/test_orchestration/test_plan111_task_frame.py` · `tests/test_orchestration/test_plan121_bundle_b.py` · `config/settings_help/portal_help.yaml` · `scripts/eval_portal_help.py` · `scripts/manual/fixtures/queries/40cd5dcc997d743e.json` · `scripts/manual/fixtures/queries/f2f49d86a9a82ca3.json` · `scripts/manual/fixtures/samples/Q-PORTAL-NOISE.json` · `scripts/manual/fixtures/samples/Q-PORTAL-SYNONYM.json` · `src/doc_qa/portal_help.py` · `src/orchestration/portal_help.py` · `src/prompts/portal_help.py` · `src/static/manual/help_index.json` · `src/utils/help_question.py` · `testdata/routing_gold/portal_help.yaml` · `tests/test_manual/test_help_index.py` · `tests/test_portal_help.py` · `tests/test_portal_help_gold.py` · `tests/test_portal_help_routing.py` · `tests/test_utils/test_help_question_method.py`
> **요청(사용자 2026-10-07)**: *"질의 응답에서 AIOps포탈의 기능에 대해 질의를 하면 대답해 주는 기능을 만들려고 한다. 예를 들어 "itam db에 유사어를 등록하려고 한다. 어떻게 해야 되냐?"라는 질문을 했을때 어떤 프롬프트를 어떻게 넣어야 되는지를 답변해주는 식으로 동작해야 한다. 또는 "노이즈 캔슬링의 '수신 알람'은 무엇인지 설명하라."라고 이야기하면 AIOps포탈의 노이즈 관제 화면의 '수신 알람'의 개념과 기능을 설명해야 된다. 이 요건에 맞게 계획파일을 작성하라."*
> **해석**
> - 「AIOps포탈의 기능」 = 이 저장소가 제공하는 웹 화면·채팅 기능 전부(메인 채팅 · 노이즈 관제 · 관리자 대시보드 · 채팅 명령). 사내 규정·설계 문서(`plans/126`·`127`의 문서 RAG)와는 **다른 소스**다.
> - 「어떤 프롬프트를 어떻게 넣어야 되는지」 = 절차 + **실제로 동작하는 입력 문장 예** + 주의점. 입력 예는 지어내면 안 된다(동작하지 않는 문장을 안내하게 된다).
> - 「개념과 기능을 설명」 = 화면 요소의 뜻 · 계산 방식 · 읽는 법(매뉴얼 「상세 설명」 항목 수준).
> - 계획만 쓴다. 코드는 게이트 확정 뒤다.
>
> **관련 계획**: `plans/116`(매뉴얼 · 원천 `scripts/manual/` — **이 기능의 근거 정본**) · `plans/126`(문서 질의 엔진 `src/doc_qa/` — 인용·근거 블록 재사용) · `plans/127`(2단 조건부 처리기 `doc_query` — 배선 선례) · `plans/138`(문서 명령 단락 — 강제 경로 선례) · `plans/129`(자체 RAG 패키지 — 의존하지 않음, §3.3) · `plans/103`(3단 동등성 — 통지만)
> **관련 결정**: **D-004 + 부기 2건**(키워드 사전 분류 금지 · 등록 이름 + 명령 형태 단락만 허용 — G-5에서 충돌 검토) · **D-038**(사용법 안내 — 병존, G-6) · **D-252**(매뉴얼 정본·사례 녹화 · 사례 결과는 지어내지 않는다) · **D-255**(매뉴얼 동반) · D-131(정본 일원화 · 사본 금지) · D-232(사용자별 인가) · D-251(기준 경로 2단 · 1단 비노출 선례) · D-284 ⑧(근거 0건이면 LLM 0회) · D-293·D-307(명시 지목 · 별칭) · D-194(answer 프로파일) · D-240(로컬 MLX)
> **D-번호**: **D-315 예약**(색인 예약 행 2026-10-07 — 병행 세션 D-316 예약 다음)
> **실측 기준**: `main` `b1fabf4` · 2026-10-07 · 코드 **읽기만**(수정 0 · LLM 0 · 서버 기동 0 · 과금 0). 작업 트리에는 병행 세션의 `plans/143`·`docs/02`·`plans/INDEX*` 미커밋 변경이 있다(이 계획과 무관).
> **근거 표기**: **코드** = `파일:라인` · **실측** = 파일을 열어 셈 · **추적** = 코드 경로를 따라간 결론(실행 없음) · **추정** = 검증하지 않은 추론

---

## 0. 결론 요약

| 질문 | 답 | 근거 |
|---|---|---|
| 지금 두 예시 질문은 어디로 가나 | **포탈 기능을 아는 담당이 없다.** 사용법 단락(③.8)은 「사용법·도움말 + 이 시스템」류만 잡아 지원 소스 목록을 답한다 — 예시 두 문장은 걸리지 않는다. 분해 LLM으로 넘어가 `general_inference`(근거 없는 일반 답 = 지어낼 위험)나 `alarm_query`(수신 알람 **조회**)로 갈 것으로 **추정**한다. W0에서 실측한다 | 코드 `src/utils/usage_query.py:22-34` · `src/orchestration/intent_planner.py:783-787` |
| 유사어 예시에 다른 위험이 있나 | **있다(추적).** 질문에 「등록」이 들어 있어 입력 분석 LLM이 `synonym_registration`을 내면 `field_mapper`의 오분류 가드(「등록」 부재 조건)를 통과해 실제 등록 흐름으로 들어갈 수 있다 — 사용 방법 질문이 「등록할 매핑 없음」 같은 등록 응답으로 끝난다. W0에서 확인하고 필요하면 W4에서 막는다 | 코드 `src/nodes/field_mapper.py:72-95` |
| 근거는 무엇으로 하나 | **매뉴얼 원천**(`scripts/manual/content/{user,admin}.md` + `features.yaml` + `cases.yaml`). 이미 「모든 기능」을 manifest와 가드 테스트로 약속하고(D-252 ②), 화면이 바뀌면 같은 작업에서 갱신된다(D-255). 별도 지식 사본을 만들지 않는다(D-131) | 실측(2026-10-13 재측): 사용자 56절 · 상세 항목 151 / 관리자 73절 · 상세 항목 365 · 원천 약 563KB |
| 예시 두 질문의 답이 매뉴얼에 있나 | **있다.** 유사어 = U-37(입력 형식 「(항목)에 '(낱말)' 유사 단어를 추가해줘」 · 「DB 유사어는 폴스타와 자산관리(ITAM)가 함께 씁니다」 · 녹화 사례 `Q-SYNONYM` 2턴) · 수신 알람 = U-44 상세 항목 `##### 수신 알람`과 KPI·퍼널·억제율 항목 | 코드 `scripts/manual/content/user.md:1168-` · `:1756` · `scripts/manual/cases.yaml:103-108` |
| 어떻게 찾나 | 매뉴얼 빌드가 절·항목 단위 **색인 파일**을 함께 만들고(커밋 — 배포 서버에 빌드 도구가 없다), 실행 시 **프로세스 안 어휘 검색**(한글 2글자 단위 BM25 + 제목 가중)으로 찾는다. 외부 검색 플랫폼·벡터 DB 0 | §3 · 코드 `scripts/manual/build.py:9` |
| 어떻게 답하나 | 근거 청크로 LLM 서술 1회(스트리밍) + **입력 예·매뉴얼 링크는 코드가 붙인다**(LLM이 프롬프트 예를 만들지 않는다). 근거 0건이면 LLM 0회 결정적 안내(D-284 ⑧과 같은 계약) | §4 |
| 어느 경로에 붙나 | **2단 조건부 처리기 `portal_help`**(D-251 기준 경로 · `doc_query`와 같은 결합 방식) + 「포탈 매뉴얼에서 …」 명시 명령 단락(D-307 방식). 1단 비노출 · 3단은 103 통지만 | §5 |
| 권한 | 사용자 = 사용자 매뉴얼만 · 관리자 = 두 권(G-2) | §4.4 |

---

## 1. 현행 실측

### 1.1 채팅 경로의 기존 장치

| 장치 | 하는 일 | 이 기능과의 관계 | 근거 |
|---|---|---|---|
| 사용법 단락 ③.8 (`is_usage_query`) | 「지원 소스」「뭘 할 수 있」류 · 「사용법/도움말 + 에이전트 지칭」을 결정적으로 잡아 `general_inference` 단일 task → **활성·허용 소스 목록**을 코드가 조립(LLM 0) | 「무엇을 조회할 수 있나」는 실시간 레지스트리가 정본이라 매뉴얼보다 정확하다 → **유지**(G-6). 「노이즈 관제 사용법」처럼 대상이 붙으면 걸리지 않는다(「쿠버네티스 사용법」 제외 규칙) | 코드 `src/utils/usage_query.py:12-34` · `src/nodes/general_inference.py:347-355` |
| 문서 처리기 `doc_query` | 사내 규정·설계 문서(FabriX 리트리벌) | **별개 소스.** 레지스트리 문서 별칭에서 「매뉴얼」을 일부러 뺐다 — *"「매뉴얼」(앱 매뉴얼·사용법 문의)…는 넣지 않는다"* | 코드 `config/db_registry.yaml:524-526` · `src/orchestration/doc_query.py:146` |
| 조건부 처리기 결합 | 고정 목록 밖 처리기를 활성일 때만 분해·실행·재계획에 합친다(비활성 = 빈 dict = 종전 동일) | `portal_help`도 이 자리에 든다 | 코드 `src/orchestration/conditional_agents.py:82` |
| 문서 명령 단락 | 별칭 + 「~에서」 + 검색 동사면 분해 LLM을 건너뛴다(D-307) | 강제 경로의 선례 — 같은 형태로 만든다 | 코드 `src/orchestration/doc_command.py:165` |
| 근거·인용 조립 | 근거 블록 렌더 · 인용 부착을 코드가 한다 | 재사용 대상 | 코드 `src/doc_qa/evidence.py:75,121,164` |

### 1.2 매뉴얼 원천의 형태

- 절: `### U-37` 아래 `::: what` · `::: how` · `::: ui` · `::: caution` · `::: case <사례ID> <캡처>` · 상세 설명(`####` 소제목 · `#####` 항목) · 관련 링크(실측 `user.md:1168-` — 2026-10-13 재측).
- 사례: `cases.yaml`의 입력 문장(턴 단위) + `fixtures/samples/<ID>.json`의 녹화 결과(게재 판정 `ok`·`partly`만 실림 — D-252 ⑤).
- 산출: `src/static/manual/{user,admin}.html`(커밋 · 앵커 `#u-37`) — 채팅 답의 「매뉴얼에서 보기」 링크 대상.
- 용어: 사용자는 「노이즈 캔슬링」, 사용자 매뉴얼 장 제목은 「노이즈 관제」, 관리자 매뉴얼 장 제목은 「노이즈 캔슬링 관제(관리자)」 — 표면어가 갈린다(§3.2 별칭).

---

## 2. 범위

| 넣는다 | 넣지 않는다 |
|---|---|
| 포탈 기능 사용 방법 · 화면 요소 개념 · 입력 예 · 주의 · 매뉴얼 링크 | 사내 규정·설계 문서(`doc_query` 소관) · 데이터 조회 결과 |
| 사용자·관리자 매뉴얼 두 권(역할별) | 코드·`docs/`·계획서를 근거로 쓰기(G-1) |
| 2단 처리기 · 명시 명령 단락 · 매뉴얼 동반 | 1단 도구 노출(G-8) · 3단 구현(103 통지만) |
| 단일 턴 답 + 대화 이력 기반 후속 질문(분해 LLM의 `sub_query` 재작성에 맡김) | 질문 대상에 맞춘 입력 예 **생성**(예: ITAM 실제 컬럼명 채우기 — G-9) |

---

## 3. 근거 색인

### 3.1 색인 생성 (빌드 타임)

- `scripts/manual/build.py`가 HTML과 함께 **`src/static/manual/help_index.json`**을 만든다(G-7 위치) — 같은 원천·같은 커밋·같은 배포. 별도 사본 원천을 두지 않는다(D-131).
- 청크 단위
  - **절 청크**: `what`·`how`·`caution`(+ 화면 요소 표) — 절 제목 · 장 제목 · `id` · 권(user/admin) · 앵커.
  - **항목 청크**: `#####` 항목마다 하나 — 부모 절 `id`·제목 + 항목명(「수신 알람」) + 본문.
  - **사례 부착**: 절의 `cases`(`features.yaml`)에서 입력 문장을 **그대로** 청크 메타에 싣는다(`Q-SYNONYM` → 2문장). 녹화 판정 `wrong`·미게재 사례는 싣지 않는다.
- 원천 표기(`[[보일 글자|항목명]]`·`{{항목명}}`·`::: ui`)는 빌드의 기존 렌더 규칙으로 평문화한다 — 새 파서를 따로 두지 않는다.
- **동기화 가드**: `tests/test_manual`에 「색인 = 현재 원천으로 다시 만든 결과」 비교 1건(원천 해시 기록). 매뉴얼을 고치고 빌드를 안 돌리면 실패한다.

### 3.2 검색 (실행 시)

- 프로세스 시작 시 색인 1회 적재 · 메모리 상주(수백 청크 · 수 MB 이하 — **추정**, W1에서 셈).
- **한글 2글자 단위 + 영문·숫자 토큰 BM25**, 가중: 항목명·절 제목 ×3 · 장 제목 ×2(G-3 권고 — 외부 패키지 0). 따옴표로 묶은 화면 용어(「'수신 알람'」)는 정확 일치 항목명이면 최우선.
- **화면 별칭 소표**(색인 메타 · `features.yaml` 절에 `aliases` 칸 추가): 「노이즈 캔슬링」↔「노이즈 관제」, 「AIOps 포탈」「포탈」↔ 시스템 전체 등. 검색 질의 확장에만 쓰고 라우팅 판단에는 쓰지 않는다(D-004).
- 결과: 상위 k(초기 4) · 문자 예산(초기 6,000자) · 최소 점수 미만이면 0건 처리. k·예산·임계는 **모듈 상수**로 두고 W5 골드셋으로 정한다(설정 노출 0).
- E5 의미 검색 혼합은 W5에서 회수율 기준(§7 M-1) 미달일 때만 `semantic` extra로 추가한다.

### 3.3 `plans/129`(자체 RAG)·`plans/126`(FabriX)에 얹지 않는 이유

- 근거가 저장소 안 약 0.5MB 마크다운이고 매 빌드마다 바뀐다 — 외부 색인(재청킹 → 새 retrieval_id 회전, D-284 ③) 운영 부담이 이득보다 크다.
- `RAG_ENABLED`(기본 off · 폐쇄망 접속값 필요)에 묶이면 로컬·폐쇄망 모두에서 꺼진 채로 남는다.
- 129가 완성돼도 이 색인을 그쪽으로 옮길 이유는 측정(M-1 미달)이 생길 때만이다.

---

## 4. 답변 서비스

### 4.1 위치

| 파일 | 계층 | 내용 |
|---|---|---|
| `src/doc_qa/portal_help.py` | application(기존 `doc_qa` 패키지 편입) | 색인 적재 · 검색 · 역할 필터 · 근거 조립 · 서술 1회 · 부착 |
| `src/prompts/portal_help.py` | prompts | 서술 지시(근거 밖 금지 · 입력 예 작성 금지 · 화면 이름은 근거 표기 그대로) |
| `src/orchestration/portal_help.py` | orchestration | 조건부 처리기 spec · 분해 절 렌더 · 활성 판정 · 명시 명령 파서 |

### 4.2 고정 단계 (LLM이 단계를 정하지 않는다)

```
1 질의 확정(명령 접두 제거) → 2 역할 확정(G-2) → 3 검색(§3.2)
  → 4a 근거 0건: 결정적 안내(LLM 0) — 「매뉴얼에서 찾지 못했습니다」 + 매뉴얼 링크 + ❓ 사용법 안내
  → 4b 근거 있음: 서술 1회(answer 프로파일 · USER_RESPONSE_TAG 스트리밍)
  → 5 코드 부착: ① 입력 예(사례 문장 원문) ② 매뉴얼에서 보기 링크(/manual/user#u-37) ③ 권한 고지(있을 때)
```

### 4.3 예시 질문의 기대 답 형태

**「itam db에 유사어를 등록하려고 한다. 어떻게 해야 되냐?」**(근거 U-37)
- 서술: DB 유사어는 폴스타와 ITAM이 함께 쓴다 → 채팅 입력창에 기준 항목(컬럼)과 낱말을 함께 적는다 → 답에서 등록 결과를 확인한다 · 모든 사용자에게 적용되니 확실한 것만.
- 코드 부착 입력 예(사례 원문): `hostname에 '장비이름' 유사 단어를 추가해줘` · 형식 `(항목)에 '(낱말)' 유사 단어를 추가해줘` — ITAM 컬럼은 `<컬럼명>` 자리표시로만 안내(G-9).
- 관리자에게는 A-24(컬럼 설명·유사어 초안 검토) 경로를 함께 싣는다.

**「노이즈 캔슬링의 '수신 알람'은 무엇인지 설명하라」**(근거 U-44 항목 「수신 알람」 + 인접 KPI·퍼널 항목)
- 서술(근거 `user.md:1756-` — 2026-10-13 재측): 무엇인가(고른 기간에 알람 거르기가 내린 판단의 수) · 어떻게 동작하나(PAGE·TICKET·DASHBOARD·SUPPRESS 카드의 합 = 퍼널 「수신」) · 읽는 법(해소 알림도 한 건으로 들어가 「장애 건수」보다 크다 · 억제율·퍼널의 분모) · 누르면 기간 안 판단 전체 목록이 열린다.
- 코드 부착: 노이즈 관제 화면 링크 · 매뉴얼 `#u-44` 링크.

### 4.4 역할 필터 (G-2)

- 사용자: 사용자 매뉴얼 청크만 검색한다. 관리자 매뉴얼에만 근거가 있으면 내용은 싣지 않고 「관리자 기능입니다(관리자 매뉴얼 A-NN 「제목」) — 관리자에게 요청하세요」를 결정적으로 답한다.
- 관리자: 두 권 모두 · 근거마다 권 표기.
- 역할은 기존 질의 경로의 인증 사용자 정보에서 읽는다(JWT `type`·role 클레임 — 새 판정 0). `src/doc_qa/authz.py`의 관리자 판정을 재사용한다.

### 4.5 실패·고지

- 색인 파일 부재·손상 → 처리기 비활성 + 기동 로그 1줄(침묵 폴백 금지). 질의 중 오류 → 구조화 사유를 응답에 노출, `general_inference`로 강등하지 않는다.
- 감사는 기존 질의 감사(`audit_middleware`)로 남는다 — 외부 호출이 없어 문서 검색 전용 감사(`log_doc_retrieval`)는 쓰지 않는다.

---

## 5. 라우팅 배선 (2단 기준 경로)

| 층 | 변경 | 비고 |
|---|---|---|
| 계층 A 단락 | **③.9 포탈 명시 명령** — 포탈 별칭(G-4: 「포탈 매뉴얼」「사용자 매뉴얼」「관리자 매뉴얼」「AIOps 포탈」「포탈 도움말」 · 정본 `config/db_registry.yaml` `solutions[portal].aliases`) + 「~에서/~로」 + 설명·검색 동사면 `portal_help` 단일 task | D-307과 같은 판정 재료(등록 이름 + 명령 형태)만 쓴다. 맨 「매뉴얼」은 넣지 않는다(본부 전산관리매뉴얼과 충돌) |
| 계층 B 분해 LLM | 처리기 목록에 `portal_help` 1줄 + 대조 예 4쌍: 「수신 알람이 뭐야」↔「오늘 수신 알람 몇 건」 · 「유사어 등록 방법」↔「hostname에 '장비이름' 유사어 등록해줘」 · 「결과 엑셀 내려받는 법」↔「CPU 상위 10건 엑셀로」 · 「포탈 기능 설명」↔「쿠버네티스 사용법」 | 활성일 때만 렌더(비활성 = 프롬프트 바이트 불변) |
| ③.8 사용법 단락 | 변경 없음(G-6) | |
| `general_inference` 안전망 | **넣는다(G-5 (b) 확정 2026-10-13)** — 1·2단 수렴점(`subagents.run_general_inference`)에서 결정적 단락·`direct_response` task가 아닐 때만, 좁은 조건(§6.0)으로 `portal_help`로 되돌린다 | D-004 부기 변경 — 사용자 승인 |
| 설정 | `PortalHelpConfig.enabled`(`PORTAL_HELP_ENABLED`) 1필드 · **기본 on(G-7 (b) — 원칙 예외 · 사용자 확정)** | 기동 시 1회 해석 · 색인 부재면 켜도 비활성 |
| 1단 `deep_agent` | **도구 1종 노출(G-8 (b) 확정)** — 활성일 때만 목록에 든다(기동 시 1회 해석 · 턴마다 흔들리지 않음) + 오케스트레이터 지시 1줄 | 로컬 `.env`는 1단 확정 — 1·2단 모두 확인 |
| 3단 | `plans/103` 동등성 항목 1행 통지 | |

---

## 6. 게이트 (권고안 = 본문 기본값)

### 6.0 사용자 확정 (2026-10-13 · 팀 리드 경유)

| # | 확정 | 권고 대비 | 본문 반영 |
|---|---|---|---|
| G-1 | (a) 매뉴얼 두 권만 | 권고 | — |
| G-2 | (a) 「관리자 기능」 안내 + 절 제목만 | 권고 | — |
| G-3 | (a) 어휘(BM25) 먼저 | 권고 | — |
| **G-4** | 별칭 5종을 **`config/db_registry.yaml` 소스 항목**(`solutions[portal].aliases`)으로 둔다 — D-307 형태(판정 재료 = 레지스트리 별칭 + 명령 형태) | 위치 확정 | §5 ③.9 |
| **G-5** | **(b) 되돌린다** — 분해 LLM이 `general_inference`로 보낸 질문을 매뉴얼 검색 근거로 `portal_help`로 되돌린다. **D-004 부기(D-307 「질문 내용어로 소스를 정하지 않는다」) 변경을 사용자가 승인**했다. 범위는 좁게(오탐 시 정상 라우팅이 깨지지 않게) 잡고 경계를 테스트로 고정한다 | **권고와 다름** | §5 안전망 행 · D-004 부기 |
| G-6 | (a) 사용법 단락 ③.8 유지 | 권고 | — |
| **G-7** | **(b) 기본 on** — 「신규 플래그 기본 off」 원칙의 예외(사용자 확정 근거를 config 주석·D-315에 적는다). 켠 상태가 사용법 외 기존 질의를 바꾸지 않는지 회귀로 확인 · 색인 위치는 `src/static/manual/help_index.json` | **권고와 다름** | §5 설정 행 |
| **G-8** | **(b) 1단 `deep_agent`에도 매뉴얼 도구 노출** — 1·2단 어디서든 동작(D-038 대칭) | **권고와 다름** | §5 1단 행 |
| G-9 | (a) 사례 원문 + 형식 문장 · 대상은 `<자리표시>` | 권고 | — |

아래 원 게이트 표는 검토 이력으로 남긴다.

| # | 질문 | 선택지 | 권고 | 고르면 달라지는 것 |
|---|---|---|---|---|
| G-1 | 답의 근거 범위 | (a) 매뉴얼 두 권만 (b) + `docs/` 운영 문서 | **(a)** | (b)는 내부 용어·구현 상세가 사용자 답에 섞이고 정본이 둘이 된다 |
| G-2 | 일반 사용자가 관리자 기능을 물으면 | (a) 「관리자 기능」 안내 + 절 제목만 (b) 관리자 매뉴얼 내용도 답 (c) 답하지 않음 | **(a)** | (b)는 사용자가 할 수 없는 절차를 안내 · (c)는 왜 안 되는지 모른다. 관리자 매뉴얼 HTML 자체는 지금도 무인증 공개(D-252 ①)라 (a)는 보안 장치가 아니라 안내 정확도 장치다 |
| G-3 | 검색 방식 | (a) 어휘(BM25) 먼저 · 기준 미달 시 E5 혼합 (b) 처음부터 E5 혼합 (c) `plans/129` 완성 대기 | **(a)** | (b)는 `semantic` extra 설치가 필수 조건이 된다 · (c)는 착수가 129에 묶인다 |
| G-4 | 강제 명령 별칭 | (a) §5 목록 5종 (b) + 맨 「매뉴얼」 | **(a)** | (b)는 「본부 매뉴얼에서 …」류와 충돌 — 문서 별칭에서 뺀 근거와 같다(`db_registry.yaml:524-526`) |
| G-5 | 분해 LLM이 `general_inference`로 잘못 보낸 경우 검색 점수로 되돌릴지 | (a) 안 한다 (b) 높은 임계 이상이면 매뉴얼로 답 | **(a)** · W5에서 오분류율이 M-2 미달이면 다시 묻는다 | (b)는 D-004 부기(내용어로 소스 결정 금지)의 개정이 필요하다 — **충돌 사안이라 사용자 결정 없이는 하지 않는다** |
| G-6 | 기존 사용법 단락(③.8)과의 관계 | (a) 유지 — 「무엇을 조회할 수 있나」는 실시간 소스 목록 (b) 매뉴얼 답으로 대체 | **(a)** | (b)는 연결 안 된 소스를 매뉴얼 문장대로 「된다」고 답할 수 있다 |
| G-7 | 기본값·색인 위치 | (a) 기본 off · 색인 `src/static/manual/help_index.json` (b) 기본 on (c) 색인을 `config/` 아래 | **(a)** · 폐쇄망 확인 뒤 코드 기본값 on 전환은 별도 확인 | (b)는 「신규 플래그 기본 off」 원칙의 예외 근거가 필요 · (c)는 빌드 산출이 정본 폴더(`config/`)에 섞인다 |
| G-8 | 1단 `deep_agent`에 도구로 노출 | (a) 비노출 (b) 검색 도구 1종 노출 | **(a)** | (a)면 로컬 기본 `.env`(1단 확정)에서는 동작하지 않는다 — 운영 `.env`가 어느 단으로 확정되는지 확인 필요 |
| G-9 | 입력 예 | (a) 매뉴얼 사례 원문 + 형식 문장만, 대상은 `<자리표시>` (b) LLM이 질문 대상(ITAM 컬럼 등)에 맞춰 바꿔 씀 | **(a)** | (b)는 존재하지 않는 컬럼명·동작하지 않는 문장을 안내할 수 있다(검증 수단 없음) |

---

## 7. 측정 기준

골드셋 `testdata/routing_gold/portal_help.yaml`(신규 · 약 30건): 양성 20(사용자 14 · 관리자 6 — 요청 예시 2건 포함 · 후속 질문 2턴 2건) + 음성 10(같은 낱말의 데이터 조회·실제 등록 요청·일반 개념 질문).

| # | 지표 | 방법 | 기준(초기 — G 확정 시 조정) |
|---|---|---|---|
| M-1 | 검색 회수율 recall@4 | 결정적(LLM 0) — pytest | 양성 ≥ 90% |
| M-2 | 라우팅 정확도 | `eval_routing` 확장 · 로컬 MLX 두 평면(D-240) | 양성 → `portal_help` ≥ 85% · 음성 오탐 ≤ 10% |
| M-3 | 답 정합 | 가짜 LLM + 대표 3건만 MLX 1회 | 기대 절 링크 포함 · 입력 예가 사례 원문과 바이트 일치 · 근거 0건 질문에 LLM 0회 |
| M-4 | 등록 오인 0 | 노드 모드(`--decomposition --node`) | 「유사어 등록 방법」류가 등록 흐름으로 끝나지 않음 |

MLX 수치는 로직 확인용이다 — 정확도·지연 결론은 폐쇄망(FabriX)에서 낸다.

---

## 8. 작업 단위

| W | 내용 | 산출 | 검증 |
|---|---|---|---|
| **W0** | 기준선 — 예시 2건 + 골드셋 초안을 현행 2단으로 실행(로컬 MLX · `--show-env`로 두 평면 확인) · 어디로 가는지·등록 오인 여부 기록 | §11 실측 기록 | 로그 `intent_planner`·`field_mapper` 판정 줄 |
| **W1** | 색인 생성기(`build.py` 확장) · `features.yaml` `aliases` 칸 · 동기화 가드 | `help_index.json` · 테스트 | `pytest tests/test_manual` |
| **W2** | 검색기 + 답변 서비스 + 프롬프트 · 역할 필터 · 결정적 0건·권한 안내 | `src/doc_qa/portal_help.py` · `src/prompts/portal_help.py` | M-1 · M-3(가짜 LLM) |
| **W3** | 2단 배선 — 조건부 처리기 · 분해 절 · ③.9 명시 명령 · 설정 1필드 · 기동 로그 | `src/orchestration/portal_help.py` · `conditional_agents` 1줄 · `src/config.py` | 비활성 바이트 불변(분해 프롬프트 렌더 비교 `prompt_render_diff.py`) · 단위 테스트 |
| **W4** | 등록 오인 가드(W0에서 재현될 때만) — 「등록」이 있어도 방법을 묻는 의문형이고 대기 목록이 없으면 등록 흐름에 들어가지 않는다(쓰기 지점 결정적 차단) | `field_mapper` 가드 1곳 | M-4 · 기존 등록 회귀(`test_field_mapper_node`) |
| **W5** | 골드셋 · `eval_routing` 확장 · MLX 측정 · 기준 미달 항목 교정(분해 대조 예 · 별칭 · 가중) | 골드셋 · 측정 기록 | M-1~M-4 |
| **W6** | 매뉴얼 동반(D-255) — 사용자 절 「포탈 기능 물어보기」 신설(U-NN) · 사례 2건(요청 예시 2문장) 녹화·판정 · 관리자 설정 절에 `PORTAL_HELP_ENABLED` | `features.yaml` · `content/*.md` · 캡처 · 빌드 | `pytest tests/test_manual` |
| **W7** | 기록 — D-315 본문 · 색인 · CHANGELOG · INDEX · `plans/103` 통지 · `docs/21` 사다리 2단 처리기 목록 1행 | 문서 | — |

회귀(D-303 부기): Wave 중간은 자기 테스트 + `python scripts/regress.py --base 81a4562 --no-tests --files <바꾼 파일…>` · 계획 끝 1회 `python scripts/regress.py --base 81a4562 --files <회귀 대상 파일…>`(모듈 단위 · 전체 회귀는 요청 시만) + `arch_check --ci` · `overfit_check --ci`(색인 JSON은 스캔 대상 밖 · 처리기 코드에 폴스타 스키마 리터럴 0).

---

## 9. 위험

| 위험 | 대응 |
|---|---|
| 분해 LLM이 「수신 알람」을 데이터 조회로 보냄(또는 반대) | 대조 예 4쌍 · ③.9 명시 명령으로 확실한 경로 제공 · M-2로 측정 · 미달 시 G-5 재질문 |
| 매뉴얼과 화면이 어긋난 채 답함 | D-255 동반 + 색인 동기화 가드 — 매뉴얼이 정확한 만큼만 정확하다(매뉴얼 결함은 그 자리에서 고친다 · D-252 G-8) |
| 입력 예를 LLM이 지어냄 | 입력 예는 코드 부착(G-9 (a)) · 서술 지시에 작성 금지 · M-3 바이트 대조 |
| 관리자 절차가 사용자에게 안내됨 | 역할 필터(G-2) |
| 로컬에서 동작 확인이 안 됨 | 로컬 `.env`는 1단 확정 — W0·W5는 2단 전환 상태에서 실행한다 |
| 한글 2글자 BM25가 조사·어미에 약함 | 제목 가중 · 별칭 확장 · M-1 미달이면 E5 혼합(G-3) |

---

## 10. 완료 조건

- M-1~M-4 기준 충족(MLX 기준) · 예시 2건이 §4.3 형태로 답함(녹화 사례로 매뉴얼에 실림).
- 플래그 off에서 분해 프롬프트·라우팅 결과 바이트 불변.
- 매뉴얼 신설 절 · `tests/test_manual` 통과 · D-315 본문 등재.
- 폐쇄망 확인(FabriX 서술 · 운영 `.env` 사다리 단) 항목은 잔여로 남긴다.

## 11. 실측 기록

### 11.1 W0 기준선 (2026-10-13 · 로컬 MLX `mlx-community/Qwen3.5-9B-OptiQ-4bit` 두 평면 · 각 1회 · 코드 수정 0)

2단은 프로세스 환경 `ENABLE_DEEPAGENTS_PACKAGE=false`로 확정(`.env` 불변). 1단은 `create_deep_agent`를 실제 조립하고 도메인 도구 7종을 실행 없는 가짜 함수로 바꿔 첫 도구 호출만 기록했다.

| # | 문항 | input_parser `synonym_registration` | field_mapper 등록 흐름 | 2단 경로 / agent | 1단 첫 도구 |
|---|---|---|---|---|---|
| 1 | itam db에 유사어를 등록하려고 한다. 어떻게 해야 되냐? | `{"mode":"all"}` | **진입**(가드 통과 — 「등록할 유사어 매핑이 없습니다…」 반환 · 쓰기 전 조기 반환) | `llm_decompose` / **`cache_management`** | `general_answer` |
| 2 | 노이즈 캔슬링의 '수신 알람'은 무엇인지 설명하라. | null | — | `llm_decompose` / `general_inference` | **도구 0회 · 오케스트레이터 직답(헤드셋 기능으로 오답)** |
| 3 | 결과를 엑셀로 내려받으려면 어떻게 해? | null | — | `llm_decompose` / `general_inference` | `general_answer` |
| 4 | (음성) 오늘 수신 알람 몇 건이야 | null | — | `llm_decompose` / `alarm_query` | `query_alarm` |

- ③.8 사용법 단락은 1~3 어느 것도 잡지 않았다(「어떻게」「설명하라」는 단락 어휘 밖 — 계획서 §0 추정 확인).
- **등록 오인 재현** → W4 수행. 문항 1은 분해가 `general_inference`가 아니라 `cache_management`로 보냈으므로 G-5 되돌림 대상 담당에 `cache_management`를 포함한다(방법 의문 표지가 있을 때만).
- 1단 문항 2의 직답 오답 → G-8 (b) 도구 노출 + 오케스트레이터 지시(포탈 기능·화면 용어 질문은 직접 답하지 말고 도구 호출).

### 11.2 W1~W4 구현 기록 (2026-10-13)

- **W1 색인**: `scripts/manual/build.py` `build_help_index()` → `src/static/manual/help_index.json`(W6 빌드 뒤 670청크 · 약 0.7MB · 항목 앵커 단위 · 중복 항목명은 id에 `:<앵커>` 접미). `features.yaml` `aliases` 칸(U-44·U-45 「노이즈 캔슬링」 · A-42 「노이즈 관제」 · U-22 「엑셀」). 동기화 가드 `tests/test_manual/test_help_index.py`.
- **W2 검색·답변**: `src/doc_qa/portal_help.py` BM25(TOP_K 4 · CHAR_BUDGET 6000 · MIN_SCORE 0.3 · 되돌림 0.5·내용어 ≥3자 · 서술 ≤1200자) · 상태 ok/empty/admin_only/error · 근거 0건이면 LLM 0회 · 입력 예·링크는 코드 부착(G-9 (a)) · 역할 필터 `authz._is_admin`(G-2). 적재 약 77ms · 질의 0.3~4ms. 표지 판정 `src/utils/help_question.py`.
- **W3 배선**: 레지스트리 `code: portal`·`data_source: false`·별칭 5종(G-4) · `PortalHelpConfig.enabled` 기본 **on**(G-7 (b)) · ③.9 `_portal_command_plan`(②.9 뒤 · 데이터 표지면 명령 아님) · 분해 절 `INTENT_PLANNER_PORTAL_SECTION` + 대조 예 4쌍 · G-5 되돌림 `coerce_portal_redirect`(general_inference·cache_management · 방법/개념 의문 + 점수 ≥0.5) · 1단 도구 `answer_portal_help` + 오케스트레이터 지시(G-8 (b)) · replanner 포탈 전용 계획 결정적 종료 · 설정 카탈로그 그룹 「포탈 도움말」 · 처리 현황 라벨. **off에서 분해 프롬프트 바이트 동일**(8032자 · sha 06da76f1a17e).
- **W4 등록 오인 가드**: `field_mapper` — 대기 목록 없음 + `is_method_question`(어떻게·방법·하려면·사용법·절차)이면 등록 신호를 버린다.

### 11.3 W5 측정 (골드셋 `testdata/routing_gold/portal_help.yaml` 30건 · `scripts/eval_portal_help.py` · 로컬 MLX 두 평면 · 1회)

| 지표 | 결과 | 비고 |
|---|---|---|
| M-1 검색 적중(결정적) | 19/20 | pu-05(「읽어」 어미) 미적중 |
| M-2 라우팅 양성 | 18/20 | pu-14 분해 파싱 실패 → data_query 폴백 · pa-06 「워치독」 general_inference(별칭은 일반 개념 질문까지 끌어와 넣지 않음) |
| M-2 음성 오탐 | 1/10 → 교정 후 0 | ng-01 「오늘 수신 알람 몇 건이야」→portal_help → `coerce_portal_data_request` 추가 뒤 alarm_query |
| M-3 입력 예 바이트 일치 | 3/3 | |
| M-4 등록 오인 | 0 | |
| 1단 첫 도구 | 3/3 `answer_portal_help` | |

- 교정: `coerce_portal_data_request`(알람·프로세스 교정 앞) · 서술 프롬프트 `render_system(has_examples)` · U-22 별칭 「엑셀」. 재측정에서 pu-03(엑셀)도 portal_help — 다만 G-5 되돌림 점수는 0.445(<0.5)라 LLM 분해로 통과한 것이고, ng-01 교정이 가드 덕인지 LLM 변동인지는 판정 로그가 없어 미확정.
- MLX 수치는 로직 확인용이다(D-240). 폐쇄망 FabriX 수치는 잔여.

### 11.4 W6 매뉴얼 (D-255)

- 사용자 **U-61 「포탈 기능·사용법 물어보기(매뉴얼로 답하기)」**(8장 끝) · 관리자 **A-76 「포탈 도움말(채팅 기능 질의응답) 설정」**(11장).
- 사례 녹화(로컬 MLX · 2단): Q-PORTAL-SYNONYM — G-5 되돌림(cache_management→portal_help · 근거 U-37 · 0.672) · 대리 판정 **partly**(서술이 U-37 조건 일부 누락 — 본문 포인트에 명시) · Q-PORTAL-NOISE — 근거 U-44 「수신 알람」 · 대리 판정 **ok**. `human_review`는 사람 몫(V-10).
- `pytest tests/test_manual tests/test_portal_help.py tests/test_portal_help_gold.py` 660 passed.

### 11.5 계획 끝 회귀 · 독립 검증 · 교정 (2026-10-13)

- **계획 끝 회귀**(`regress.py --base 81a4562 --files <회귀 대상 파일 49개>`): 본체 통과 9,662 · 실패 1(원래 실패 `tests/test_api/test_settings_help.py::test_t2_curated_groups_have_full_coverage` — plans/143 소관) · arch·overfit·mypy 통과 · ruff E501 1건(`help_question.py` 독스트링) 수정 뒤 통과 · `범위: 모듈 단위 — 전체 미실행` · `[전체 회귀 권고]`(src.config 허브 88% · 직접 선택 47%).
- **독립 검증 + 보안 감사**(1라운드): Critical 0 · Major 4 · Minor 4. 인가(신원 = 검증된 JWT·DB 사용자 `role` · 1·2단 대칭 · fail-closed)·역할 필터 우회·레지스트리 부작용·기본 on 비사용법 경로 11종 on/off 동일은 이상 없음. 지적: V-1 「AIOps 포탈에서 … 알려줘」 데이터 요청 가로챔(off에서도) · V-2 일반 IT 질문이 관리자 매뉴얼 용어로 되돌려짐 · V-3 화면 용어 안 데이터 표지로 포탈 질문 뒤집힘 · V-4 「어떻게 돼」 값 질문 · V-5 질문 구획 · V-6 원문 사용법 판정 · V-7 「하려면」+명령형 · V-8 `data_source` 문자열.
- **교정**: 8건 전부(D-315 부기 2026-10-13). 재현 테스트 `tests/test_portal_help_verify.py`의 xfail 0 · 골드 음성 ng-11~14(`role: user`) 추가 · `eval_portal_help.py`가 골드 `role`을 `user_role`로 주입 · U-61 「AIOps 포탈」 사용 조건 1줄 + 재빌드. 교정 파일 모듈 회귀: 본체 통과 3,020 · 실패 0 · 정적 게이트 통과 · `범위: 모듈 단위 — 전체 미실행` · `tests/test_manual` 594 passed.
- 교정 뒤 MLX 재측정은 하지 않았다(결정적 가드만 변경 · 프롬프트는 질문 구획만). 관리자 양성(pa-*)의 분해 출구 되돌림이 이제 역할로 매칭되므로 M-2 재측정 시 `role` 주입본으로 잰다.
