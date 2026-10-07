# 138. RAG 문서 검색 라우팅 개선 — 명시 지목 강제 라우팅 · 문서군 고정 · R&R 설명 보강 · 원문 검색

> **작성일**: 2026-10-06
> **상태**: **WIP — v1.1 · W0~W6 구현(D-307 · 브랜치 `rag_improvement`) · 신규 테스트 38건 · 관련 모듈 회귀 신규 실패 0(기존 실패 29건은 HEAD `f7120c4` 격리 사본에서 동일) · 잔여 = 폐쇄망 배포 후 §6 재확인** — 파일명 `-WIP`
> **요청(사용자 2026-10-06)**: *"RAG 문서 검색 기능이 정상적으로 라우팅되지 않는 것 같음. … 위 내용 및 RAG 검색을 하도록 하는 라우팅을 강제하는 기능 도입에 대한 검토를 수행하시오. (e.g. `RAG에서 서버 관리자의 역할을 검색해줘`)"* → 후속 *"본부 전산관리매뉴얼에는 역할·책임·담당자(R&R) 내용이 포함되어 있음 · 시연 등 동작 가능성을 보여주기 위해서는 강제 라우팅하도록 하는 방안이 필요하겠음"* → *"이제까지 나왔던 최종 결론을 토대로 RAG 개선 구현계획을 작성하시오. (2)에 남는 위험은 그대로 두고, 이번 변경을 적용한 이후 다시 확인하도록 한다"*
> **관련 계획**: `plans/126`(문서 엔진 — 바꾸지 않음) · **`plans/127`(문서 라우팅 — G-6 (a) 유지 · (c)를 강제 명령 안에서만 조기 적용)** · `plans/132`(데이터 소스 명시 인식 — 별칭 정본·지목 고정 기계 재사용) · `plans/125`(조건부 처리기 계약)
> **관련 결정**: **D-004 부기**(등록 이름 인식·결정적 단락 = 라우팅 재도입 아님) · **D-281 ⑦**(명시 소스 인식) · **D-293**(소스 유사어 · 지목 소스 비활성 = 안내만) · **D-286**(plans/127 게이트 — G-6 (a)) · D-131(정본 일원화 · 사본 금지) · D-162(신규 `enable_*` 0) · D-194(answer 프로파일) · D-251 ⑦(기준선 → 수정 → 재측정) · D-255(매뉴얼 동반) · D-264 ②(권한 밖 소스 비노출) · D-303(회귀는 관련 모듈 단위)
> **D-번호**: **D-307**(2026-10-06 본문 등재 · D-004 부기 적용 · plans/127 G-6 (c) 부분 조기 적용).

---

## 0. 결론 요약

| 관찰(사용자 시험 2026-10-06) | 원인 | 이번 처분 |
|---|---|---|
| 「서버 관리자의 역할은?」이 일반 답변(`general_inference`)으로 감 | 분해 LLM이 보는 문서군 설명에 **R&R이 없음**(본부 매뉴얼에는 R&R이 있음) + 일반 답변으로 분류되면 바로잡는 장치 없음 | **W3** 설명·분해 절 보강(확률적 개선) · **W1** 강제 라우팅(결정적 경로) |
| 「백업 담당자의 역할은?」이 "직접 답하는 내용 없음" | 문서 처리기까지는 갔으나 시험 화면과 입력이 다름 — ㉠ 질의 재작성 ㉡ 두 문서군 혼입 ㉢ 답변 LLM 프로파일 차이(셋 중 결정 요인은 미확정) | ㉠ **W4** 원문 검색 · ㉢ **W5** answer 프로파일 · ㉡ 문서군을 지목하면 **W2** 고정, 지목 없으면 **현행 유지 → 적용 후 재확인(§6)** |
| 모호하면 되묻는 동작이 한 번도 안 나옴 | plans/127 **G-6 (a) 확정 설계** — 문서군 미지정이면 묻지 않고 전부(≤2) 검색 + 고지. 소스 선택 칩(plans/132)은 문서 영역에서 조건 불성립 | G-6 (a) **유지** · 고지 문구에 문서군 지정 안내 1줄(**W6**) |
| 강제 라우팅 수단 없음 | `RAG`·`사내 문서` 등이 문서 소스 별칭이 아님 · 분해 LLM을 건너뛰는 문서 명령 단락 없음 | **W1** 강제 단락 · **W0** 별칭 추가 |

## 1. 현행 실측 (HEAD `d1f4b2a` · 정적 읽기)

### 1.1 채팅 경로 (2단 `intent_orchestration`)

```
input_parser ─ _ensure_source_hints: 원문의 등록 별칭 → target_db_hints (D-293)
intent_planner._plan_turn
  ├ [계층 A] 결정적 단락 ②.7 양식 기억 · ②.8 소스 기억 · ②.4 소스 칩 답변 · ②.5 존 답변 · ③ … · ③.8 사용법
  ├ [계층 B] _llm_decompose ← doc_query 선택은 여기 분해 LLM 하나뿐
  └ 출구: _normalize_plan_exit → _apply_source_selection(지목 고정 · 소스 칩)
agent_orchestrator → doc_query.run_doc_query → doc_qa.service.answer_from_documents
result_aggregator ─ 단일 task면 final_response 그대로
```

| 사실 | 근거 |
|---|---|
| 분해 프롬프트에 렌더되는 문서군 정보 = `title`·`description`·`sibling_note`뿐(`answer_domains`·`surface_terms` 미렌더) | `src/orchestration/doc_query.py:109-116` |
| 본부 매뉴얼 설명에 역할·책임·담당자 없음 | `config/rag_collections.yaml:20-22` |
| 분해 절 문서 범위 = 규정·절차·승인 기준·보안지침·아키텍처 — R&R 없음 | `src/prompts/intent_planner.py:481` |
| 지목 고정 대상 = `data_query`·`alarm_query`·`process_query`뿐(`general_inference` 제외) | `src/orchestration/conditional_agents.py:69` |
| 지목 고정은 **소스 단위** — 문서로 고정해도 `views=[]` → 두 문서군 검색 | `conditional_agents.py:168` → `doc_query.py:161` |
| 문서 처리기는 `task.sub_query`(분해 LLM 재작성)로 검색 | `doc_query.py:170` |
| 문서 처리기 LLM = 오케스트레이터 주입 LLM(answer 프로파일 아님) · 시험 화면은 `purpose="answer"` | `agent_orchestrator.py:457` · `src/api/routes/doc_search.py:118` |
| "관련 문서는 찾았으나 직접 답하는 내용은 없었습니다" = 답변 프롬프트 규칙 2 | `src/prompts/doc_answer.py:26-31` |
| 문서 소스 별칭 = `전산관리매뉴얼`·`본부매뉴얼`·`전산관리규정`·`설계문서` | `config/db_registry.yaml:387` |
| 한글 별칭은 부분 문자열 비교(띄어 쓴 「본부 매뉴얼」 불일치) · 라틴 별칭은 단어 경계(`RAG에서` 일치) | `src/utils/query_gen_common.py:1750` `term_in_text` |
| 1단 `deep_agent` 도구 목록에 문서 처리기 없음 · 3단 `semantic_router`에도 없음 | `src/orchestration/deepagents_tools.py` grep `doc` 0건 |

### 1.2 관리자 「문서 검색 시험」과의 차이

| 항목 | 시험 화면 | 채팅 |
|---|---|---|
| 라우팅 | 없음(사람이 지정) | 분해 LLM |
| 문서군 | 지정한 1개 | LLM `views` · 비면 전부(≤2) |
| 검색 질의 | 원문 | `sub_query`(재작성) |
| 답변 LLM | `purpose="answer"` | 주입 LLM |

## 2. 확정 게이트 (사용자 2026-10-06 → D-307)

| # | 질문 | 확정 |
|---|---|---|
| **G-1** | 「문서 소스 별칭 + 「~에서/~로」 + 검색 동사」 명령으로 분해 LLM을 건너뛰는 단락이 D-004 부기 범주인가 | **부기 범주로 타당** — 판정 재료는 레지스트리 별칭(정본)과 명령 형태뿐 · 질문 내용어(「역할」·「절차」)로는 문서행을 정하지 않는다 |
| **G-2** | 문서 소스 별칭 추가 | **권고안 채택** — 추가 `RAG`·`사내 문서`·`사내문서`·`전산관리 매뉴얼`·`본부 매뉴얼`·`설계 문서`·`아키텍처 문서`·`아키텍처 설계문서` / 제외 `문서`·`매뉴얼`·`KB`·`지식베이스` / 보류 `문서 검색` |
| **G-3** | 강제 명령 안에서 문서군 이름으로 문서군 고정 | **권고 채택** — 강제 명령의 **지목 구간**(「~에서」 앞 구간)에서만 판정 · 지목어·명령어를 뗀 원문으로 검색 |
| **G-4** | plans/127 G-6(문서군 미지정 처분) | **(a) 유지** — 전부(≤2) 검색 + 고지 · 고지에 문서군 지정 안내 1줄 추가 · 되묻기 (b)는 추후 |
| **G-5** | 복합 질의(문서 + 다른 소스) 보강 | **추후 개선으로 제외** |
| 잔여 | 「RAG에서 …」(문서군 미지목)의 두 문서군 혼입 위험 | **현행 유지 — 이번 변경 적용 후 §6 비교 시험으로 재확인**(현행과 같은 처분이라 악화 요인 없음) |

## 3. 설계

### W0. 문서 소스 별칭 추가 (G-2)

`config/db_registry.yaml` `solutions[doc].aliases`:

```yaml
aliases: ["전산관리매뉴얼", "전산관리 매뉴얼", "본부매뉴얼", "본부 매뉴얼", "전산관리규정",
          "설계문서", "설계 문서", "아키텍처 문서", "아키텍처 설계문서",
          "RAG", "사내 문서", "사내문서"]
```

- **파급 범위 주의**: 별칭은 W1 강제 단락뿐 아니라 **기존 지목 고정(D-293)에도 즉시 반영**된다 — 별칭만 있어도(명령형 없이) 조회 task(`data_query` 등)를 문서 처리기로 고정하고, 문서가 비활성이면 안내만 한다(D-293 G-1). 그래서 일반어(`문서`·`매뉴얼`)는 넣지 않는다.
- 제외 근거: `문서` — 「워드 문서로 만들어줘」 등 문서 산출 요청 탈취 · `매뉴얼` — 이 앱 매뉴얼(D-252)·사용법 문의와 겹침 · `KB` — KB국민은행(KBGenAI)과 겹침.

### W1. 문서 검색 명령 단락 — 계층 A ②.9 (G-1 · G-5)

**위치**: `src/orchestration/intent_planner.py` `_plan_turn`의 ②.8(소스 기억 명령) 다음, ②.4(소스 칩 답변 턴) 앞. 판정 함수는 새 모듈 `src/orchestration/doc_command.py`(orchestration 계층 · 순수 함수 + 계획 조립)에 둔다 — `intent_planner.py` 비대화 방지, `doc_query.py`와 같은 계층.

**판정(전부 결정적 · LLM 0)**:

1. **지목 구간 찾기** — 원문에서 「`<구간>` + (에서는 | 에서 | 으로 | 로)」 형태를 찾는다. `<구간>`은 조사 바로 앞의 어절 1~3개다.
2. **문서 지목** — `<구간>`에 문서 소스 별칭(`registry.solution_aliases("doc")` — 정본)이 `term_in_text`로 맞는다.
3. **명령 동사** — 지목 구간 **뒤**에 검색 동사가 있다: `검색` · `찾아` · `찾기` · `찾아봐` · `조회` · `알려`(코드 상수 — 양식 기억 명령 `_FORM_FILL_VERB_KEYWORDS` 선례).
4. **다른 소스 지목 없음(G-5)** — 원문의 **다른 지목 구간**(「X에서/X로」)에 문서 외 등록 소스(비DB 시스템 별칭 · DB 별칭)가 있으면 단락하지 않고 종전 분해로 보낸다. 질문 내용 속 위치어(「김포 센터 백업 담당자」 — 조사 「에서」와 결합하지 않음)는 지목으로 보지 않는다.
5. **양식 턴 제외** — `template_structure`·`uploaded_file`·`selected_db_ids`·`selected_sources`가 있으면 단락하지 않는다(기존 단락들과 같은 보호).

**처분**:

| 조건 | 계획 |
|---|---|
| 문서 라우팅 활성(`doc_query.doc_active`) | `doc_query` 단일 task · `views` = W2 판정 · `sub_query` = W2 정리 질의 · 경로 표지 `doc_command` · **분해 LLM 호출 0** |
| 비활성(RAG off · 채팅 라우팅 off · 문서군 0) | `general_inference` 단일 task + `direct_response` = `source_notice_text([사용자 지목어])` + `source_notice=["doc"]` — D-293 G-1 「안내만」과 같은 문구·같은 표지(다른 소스로 대신 답하지 않음) |

- 인가는 바꾸지 않는다 — 소스 축(`allowed_sources`)·문서군 축(민감) 판정은 처리기 실행 경계(`doc_query.py:147-169`)에 있어 강제 경로도 그대로 거친다.
- 출구 처리(`_normalize_plan_exit`·`_apply_source_selection`)는 `doc_query` task를 건드리지 않는다(고정 대상 밖) — 멱등 확인 테스트만 둔다. `turn_sources`에 `doc`이 실린다(다음 턴 `previous_sources` 재료 — 종전 의미 그대로).
- **신규 플래그 없음**(D-162) — 활성은 이미 `RAG_ENABLED` ∧ `RAG_CHAT_ROUTING_ENABLED`가 쥔다. 비활성 배포에서 바뀌는 것은 「문서 지목 + 명령」 문장의 처분(종전: 분해 LLM 임의 처리 → 안내만)뿐이며 D-293 G-1 원칙과 같다.

**예시 판정표(테스트 골드 겸용)**:

| 원문 | 강제 | views | 검색 질의 |
|---|---|---|---|
| RAG에서 서버 관리자의 역할을 검색해줘 | ✔ | (비움 → G-6 (a)) | 서버 관리자의 역할 |
| 본부 매뉴얼에서 백업 담당자의 역할을 찾아줘 | ✔ | `doc.hq_manual` | 백업 담당자의 역할 |
| 전산관리매뉴얼에서 계정 신청 승인자 알려줘 | ✔ | `doc.hq_manual` | 계정 신청 승인자 |
| 사내 문서에서 운영·개발 분리 기준 검색하시오 | ✔ | (비움) | 운영·개발 분리 기준 |
| 아키텍처 문서에서 서버 구성 단위를 찾아줘 | ✔ | `doc.arch_docs` | 서버 구성 단위 |
| 서버 관리자의 역할을 RAG에서 찾아줘 | ✔(지목 구간이 중간) | (비움) | 서버 관리자의 역할 |
| RAG가 뭐야? | ✘(조사 「에서/로」 없음 · 동사 없음) | — | — |
| 서버 관리자의 역할은? | ✘(지목 없음 → 종전 분해 · W3로 개선) | — | — |
| RAG에서 백업 담당자 찾고 김포 폴스타에서 CPU 보여줘 | ✘(다른 소스 지목 — G-5) | — | — |
| 김포 서버 CPU 목록을 워드 문서로 만들어줘 | ✘(`문서` 별칭 아님) | — | — |

### W2. 지목 구간의 문서군 고정 · 질의 정리 (G-3)

- **문서군 판정 재료** = 각 문서군의 `title` ∪ `surface_terms`(정본 `config/rag_collections.yaml` · 사본 금지 D-131). 비교는 **공백 무시**(「본부 매뉴얼」 ≡ 「본부매뉴얼」).
- **판정 범위 = W1의 지목 구간뿐** — 문장 중간의 「지침」·「아키텍처」 같은 일반어는 보지 않는다(plans/127 D-004 대조표의 우려를 범위로 차단).
- 결과: 한 문서군만 맞으면 그 문서군 · 둘 다 맞으면 둘 다 · 없으면 비움(→ G-6 (a)). 민감·비활성 문서군은 `doc_views`에 없으므로 자연히 제외된다(보기 어휘 = `sanitize_views` 닫힌 어휘 그대로 통과시킨다).
- 문서 소스 별칭 중 문서군 특정 별칭(`전산관리매뉴얼`·`설계문서` 등)은 이미 `surface_terms`와 겹쳐 같은 판정을 받는다. 띄어 쓴 형태는 공백 무시 비교로 맞는다 — `surface_terms`에 사본을 추가하지 않는다.
- **질의 정리**: ① 지목 구간 + 조사 제거 ② 문장 끝 명령 꼬리 제거(「을/를」 + 검색해줘·검색해 줘·검색하시오·검색·찾아줘·찾아 줘·찾아봐·조회해줘·알려줘 등) ③ 나머지는 **원문 그대로**(용어·조항 번호 보존 — plans/126 §4.5 규칙 1). 정리 결과가 비면 단락하지 않는다(종전 분해).
- 엔진의 `strip_surface_prefix`는 그대로 둔다(정리된 질의에는 접두가 없어 무동작).

### W3. R&R 설명 보강 (케이스 1 — 확률적 개선)

- `config/rag_collections.yaml` `hq_manual`:
  - `description`에 「부서·담당자별 역할과 책임(R&R)·업무 분장」 1문장 추가 — 분해 프롬프트 문서군 표에 렌더된다.
  - `answer_domains`에 `역할`·`책임` 추가(문서화 목적 · 렌더 안 됨).
- `src/prompts/intent_planner.py` `INTENT_PLANNER_DOC_SECTION` 첫 문단 범위에 「역할·책임·담당자(R&R)·업무 분장」 추가. 문서군 이름은 적지 않는다(`overfit_check` · D-131).
- 영향: 문서 라우팅 **활성일 때만** 렌더되는 절이므로 비활성 배포 바이트 불변. 활성 배포는 분해 프롬프트가 바뀌므로 `prompt_render_diff.py` 기준 갱신과 D-251 ⑦ 재측정 대상(§6).

### W4. 단일 문서 task는 원문으로 검색 (케이스 2 ㉠)

- 분해 LLM 경로에서 계획이 **`doc_query` task 하나뿐**이고 **첫 턴**(`conversation_context` 없음)이면, 분해 출구(`_sanitize_task_views` 직후)에서 `sub_query`를 원문으로 되돌린다.
- 후속 턴은 종전대로 `sub_query`를 쓴다 — 「그럼 백업 담당자는?」 같은 생략형은 재작성이 맥락을 채우므로 원문이 더 나쁘다.
- W1 경로는 W2 정리 질의를 이미 쓰므로 대상 아님.

### W5. 문서 답변 LLM을 answer 프로파일로 (케이스 2 ㉢)

- `run_doc_query`가 엔진에 넘기는 LLM을 `create_llm(app_config, purpose="answer")`로 바꾼다 — `general_inference.py:341` 선례(D-194). 관리자 시험 화면과 같은 프로파일이 된다.
- 테스트는 LLM 생성을 주입 가능한 형태로(모듈 함수 monkeypatch) 둔다 — 실 LLM 0.

### W6. 문서군 미지정 고지에 지정 안내 1줄 (G-4)

- `doc_query.py:163-164` 고지 문구 뒤에 「문서군을 지정하면 더 정확합니다(예: 「<첫 문서군 제목>에서 … 찾아줘」).」를 붙인다. 문서군 제목은 정본에서 렌더(사본 금지).
- G-6 처분(전부 검색)은 바꾸지 않는다.

## 4. 변경 파일

| 파일 | W | 내용 |
|---|---|---|
| `config/db_registry.yaml` | W0 | `solutions[doc].aliases` 8개 추가 |
| `src/orchestration/doc_command.py` (신규) | W1·W2 | 지목 구간·명령 판정 · 문서군 판정 · 질의 정리 · 계획 조립 |
| `src/orchestration/intent_planner.py` | W1·W4 | ②.9 단락 배선 · 단일 문서 task 원문 복원 |
| `src/orchestration/doc_query.py` | W5·W6 | answer 프로파일 · 미지정 고지 안내 |
| `config/rag_collections.yaml` | W3 | `hq_manual` 설명·`answer_domains` |
| `src/prompts/intent_planner.py` | W3 | 문서 절 범위에 R&R |
| `tests/test_orchestration/test_plan138_doc_command.py` (신규) | 전부 | §5 |
| `scripts/manual/content/user.md` · `scripts/manual/features.yaml` · `src/static/manual/user.html` | 매뉴얼 | §7 |

- 계층: `doc_command.py`는 orchestration — `routing.registry`(infrastructure)·`infrastructure.doc_sources`·`utils.query_gen_common`·`routing.source_hints`만 import. `arch_check.py --ci` 통과 조건.
- `overfit_check`: 코드·독스트링에 문서군 id·제목 리터럴을 쓰지 않는다(테스트는 대상 밖).

## 5. 테스트 (LLM 0 · 네트워크 0 · D-303 관련 모듈 단위)

| 묶음 | 내용 |
|---|---|
| 판정 | §3 W1 판정표 10행 전부 + 공백 변형(「본부 매뉴얼에서」/「본부매뉴얼에서」) + 조사 변형(에서는·으로·로) + 동사 변형 |
| 비일치 보호 | `RAG가 뭐야?` · `문서`·`매뉴얼` 단독 · 워드 문서 산출 요청 · 양식 업로드 턴 · 소스 칩 답변 턴 |
| 문서군 고정 | 한 문서군·두 문서군·미지목 · 민감/비활성 문서군 제외 · 문장 중간 일반어(「지침」) 무시 |
| 질의 정리 | 지목 구간·명령 꼬리 제거 · 용어·조항 번호 보존 · 정리 결과 빈 문자열이면 단락 안 함 |
| 처분 | 활성 → `doc_query` 단일 task·LLM 미호출(분해 함수 호출 0 단언) · 비활성 → 안내만(`source_notice`) |
| 출구 멱등 | `_normalize_plan_exit`·`_apply_source_selection` 뒤에도 task 불변 · `turn_sources == ["doc"]` |
| W4 | 단일 문서 task 첫 턴 → 원문 · 후속 턴 → `sub_query` 유지 · 복합 계획 → 불변 |
| W5 | `run_doc_query`가 answer 프로파일 LLM을 엔진에 넘김(주입 확인) |
| W6 | 미지정 고지에 안내 1줄 · 지정 시 고지 없음 |
| 별칭 파급 | 기존 D-293 테스트(`test_plan132_source_selection.py` · `test_plan132_w6_source_synonyms.py` · `test_plan132_w2_source_slot.py`) 회귀 · 「RAG」 지목 + `data_query` task → 문서 고정 |
| 프롬프트 | 비활성 배포 분해 프롬프트 바이트 불변 · 활성 렌더에 R&R 문구 포함 |

품질 게이트: `ruff` · `mypy src/` · `arch_check.py --ci` · `overfit_check.py --ci` · `prompt_render_diff.py`(활성 렌더 기준 갱신 사유 기록) · `pytest tests/test_manual`.

## 6. 적용 후 재확인 (잔여 위험 처분)

사용자 확정: 「RAG에서 …」(문서군 미지목)의 두 문서군 혼입 위험은 **현행 유지** — 처분이 현행(G-6 (a))과 같으므로 악화되지 않는다. 적용 후 아래로 다시 본다.

1. **사다리 확인** — 기동 로그 `record_ladder_resolution` 1줄로 2단 확정을 확인한다. 1단(`deep_agent`)·3단이면 문서 처리기가 없어 이 계획의 효과가 없다(§1.1).
2. **사용자 시험 4건 재실행** + 형태별 비교:

| # | 질의 | 기대 |
|---|---|---|
| 1 | 서버 관리자의 역할은? | 문서 처리기 선택(W3 — 확률적) |
| 2 | 백업 담당자의 역할은? | 근거 기반 답(W4·W5) |
| 3 | 운영시스템은 개발시스템과 꼭 구분이 필요한지? | 종전과 같음(회귀 없음) |
| 4 | 서버 구성 단위는 어떻게 되는지? | 종전과 같음(회귀 없음) |
| 5 | RAG에서 서버 관리자의 역할을 검색해줘 | 강제 · 두 문서군 + 고지 |
| 6 | 본부 매뉴얼에서 서버 관리자의 역할을 검색해줘 | 강제 · 본부 매뉴얼만 · 시험 화면과 같은 답 |
| 7 | RAG에서 백업 담당자의 역할을 찾아줘 | 강제 · 두 문서군 |
| 8 | 본부 매뉴얼에서 백업 담당자의 역할을 찾아줘 | 강제 · 본부 매뉴얼만 |

3. **혼입 판정** — 5↔6, 7↔8을 비교한다. 비슷하면 G-6 (a) 유지로 종결. 미지목 쪽(5·7)만 나쁘면 G-6 (b) 되묻기 또는 다른 처분을 **측정 근거와 함께** 다시 묻는다.
4. **로그 확인** — `intent_planner` 경로 표지(`doc_command` / `llm_decompose`) · `doc_query status=… 문서군=[…] 근거=N건` · 시험 화면 진단(`diagnostics.counts`·`score_max`)과 대조.

**6-a. 1차 재확인 결과와 반복 호출 진단(2026-10-06)** — 폐쇄망 1차 시험에서 라우팅은 의도대로였으나 A1·A3·A5가 0건, A4가 "직접 답 없음"이었다. 재현 결과 채팅이 보낸 질의는 시험 화면 입력(명사구)과 **바이트 단위로 같았고**, A1(본부 매뉴얼 · 「서버 관리자의 역할」 0건)과 B1(같은 문서군 · 같은 질의 → 정상)이 갈려 **플랫폼 응답 비결정(HyDE·내부 단계)** 가설(H1)이 유력하다. 「명사구 축약이 원인」 가설은 사용자 실측(시험 화면도 명사구 · 형태별 회수 차이 작음)으로 기각했다. 판정 도구 `scripts/rag_hyde_repeat.py` — 시험 문장을 채팅과 같은 코드(`parse_doc_command`)로 해석해 같은 요청을 N회 반복하고 회차별 status·건수·점수·ms·HyDE 지문·문서 묶음과 판정 코드(S0/S1/P1/P2/Z0/Z1/E · 종합 H0/H1/H3/E)를 찍는다(우리 쪽 LLM 0 · 다른 폴더에서 실행 가능 · 상세 JSON은 작업 폴더). H1이면 F2(0건 안내 문구에서 "다시 물어도 같은 답" 삭제) · F3(0건이면 검색 1회 재시도 — plans/126 「0건 재시도 비권장」 개정 필요) 상정. **후속: `plans/141`**(설정 대조 고찰 · 0건 재시도 · 리트리벌 재구성 실험).

MLX 로컬 시험은 FabriX Retrieval이 사내망 전용이라 실 검색 대조가 불가하다 — 판정·계획은 단위 테스트로, 검색 품질은 폐쇄망에서 확인한다.

## 7. 매뉴얼 (D-255)

- `U-47 데이터 소스를 이름으로 지정해 묻기` — 문서 지목 예문(「RAG에서 … 검색해줘」 · 「본부 매뉴얼에서 … 찾아줘」)과 문서군 지정 시 정확도 안내 추가. 화면 버튼 없음(`no_ui` 유지 · 캡처 없음).
- `U-46 사내 문서 질의` — R&R 질의 예 추가 · 미지정 고지 문구 갱신.
- `python -m scripts.manual.build` → `pytest tests/test_manual`.

## 8. 범위 밖 · 잔여

| 항목 | 처분 |
|---|---|
| 복합 질의(문서 + 다른 소스) 강제 | **G-5 — 추후 개선**. 지목 고정 대상에 `general_inference` 포함(문서 지목 시) 검토 포함 |
| 문서군 되묻기(G-6 (b)) | §6-3 결과에 따라 재상정 |
| 화면 「문서 검색」 토글(`selected_sources=["doc"]`) | 추후 — `src/api/routes/query.py:1127`이 칩 답변 턴 조건과 묶여 첫 턴 수용 여부 확인 필요 |
| 1단 `deep_agent`·3단 경로의 문서 처리기 | 범위 밖(기준 경로 = 2단 · D-251) |
| 문서 엔진·검색 파라미터 | 불변(plans/126 소유 · 플랫폼 전속) |
| **관찰(구현 중 2026-10-06)** — 분해 출력 골격의 `views` 키는 **APM 활성일 때만** 렌더된다(`src/prompts/intent_planner.py` `APM_SKELETON_TAIL_WITH_KEYS` · plans/134 W1 실측: 로컬 9B는 골격에 없는 키를 0/15 냄). 문서만 활성인 배포에서는 분해 LLM이 문서군을 거의 고르지 않아 **늘 두 문서군을 검색했을 가능성**이 있다(케이스 2 ㉡과 연결) | 이번 범위 밖 — §6-3 비교 시험에서 「본부 매뉴얼 없이 물은 경우」의 `views`를 로그로 함께 본다. 문서 전용 골격 키 추가는 측정 후 별도 결정 |

## 9. 위험

| 위험 | 대응 |
|---|---|
| 명령 패턴이 넓어져 키워드 분류로 변질 | 판정 재료를 「등록 별칭 + 조사 + 동사」로 고정 · 내용어 판정 금지를 D-307 주의에 명시 · 비일치 골드 테스트 |
| 별칭 추가가 기존 지목 고정을 넓힘 | 일반어 제외(W0) · D-293 회귀 테스트 |
| 원문 검색이 후속 턴 생략형을 망침 | 첫 턴에만(W4) |
| 분해 프롬프트 변경(W3)이 다른 담당 분류를 흔듦 | 활성 배포에서만 렌더 · 기존 문서 골드 + §6 회귀 질의 3·4 |
| 폐쇄망 부분 반영으로 오진 | §10 파일 목록 + 확인 grep 심볼로 반영 검증 |

## 10. 폐쇄망 배포 (구현 시 채움)

| 파일 | 확인 grep 심볼 |
|---|---|
| `config/db_registry.yaml` | `"사내 문서"` |
| `src/orchestration/doc_command.py` | `def parse_doc_command` |
| `src/orchestration/intent_planner.py` | `def _doc_command_plan` · `def _restore_doc_query_text` |
| `src/orchestration/doc_query.py` | `def _answer_llm` · `문서군을 지정하면 더 정확합니다` |
| `config/rag_collections.yaml` | `R&R` |
| `src/prompts/intent_planner.py` | `R&R` |
| `src/static/manual/user.html` | `RAG에서 서버 관리자의 역할을 검색해줘` |

## 11. 작업 순서

1. W0·W3(설정·프롬프트) → 2. W1·W2(`doc_command.py` + 배선) → 3. W4·W5·W6 → 4. 테스트·게이트 → 5. 매뉴얼 → 6. 폐쇄망 배포 목록 확정 → 7. §6 재확인(사용자).

## 12. 구현 기록 (v1.1 · 2026-10-06 · 브랜치 `rag_improvement` — `main` `f7120c4`에서 분기)

| W | 구현 | 위치 |
|---|---|---|
| W0 | 별칭 8개 추가(계획대로) | `config/db_registry.yaml` `solutions[doc].aliases` |
| W1 | ②.9 단락 — 양식·존·소스 선택 답변 턴 제외 · 활성 = `doc_query` 단일 task(분해 LLM 0) · 비활성 = 안내만 · 경로 표지 `doc_command` | `src/orchestration/intent_planner.py` `_doc_command_plan` · `src/orchestration/doc_command.py` `parse_doc_command` |
| W2 | 지목 구간 = 조사(에서는·에서·으로·로)가 붙은 어절과 앞 최대 2어절 중 **가장 긴 문서 별칭**을 담는 구간(+ 별칭을 담은 소유격 어절 「RAG의」) · 문서군은 그 구간에서만 공백 무시 대조 · 질의 = 지목 구간 + 명령 꼬리(을/를 + 검색·찾아·조회·알려 + 보조 어절) 제거 | `doc_command.py` `_designators` · `match_collections` · `_strip_command_tail` |
| W3 | `hq_manual` 설명에 「부서·담당자별 역할과 책임(R&R)·업무 분장」 · `answer_domains`에 역할·책임 · 분해 문서 절 범위에 「역할·책임·담당자(R&R)·업무 분장」 | `config/rag_collections.yaml` · `src/prompts/intent_planner.py` |
| W4 | 첫 턴(`conversation_context` 없음) 단일 `doc_query` task는 원문 검색 | `intent_planner.py` `_restore_doc_query_text` |
| W5 | 서술 LLM = `create_llm(purpose="answer")` · 생성 실패 시 주입 LLM + WARNING | `src/orchestration/doc_query.py` `_answer_llm` |
| W6 | 미지정 고지 뒤 「문서군을 지정하면 더 정확합니다(예: 「<첫 문서군 제목>에서 … 찾아줘」).」 | `doc_query.py` `run_doc_query` |
| 매뉴얼 | U-46 how 3·4(강제 명령·문서군 지정) · caution 2줄 · U-47 사내 문서 별칭 표 | `scripts/manual/content/user.md` → `src/static/manual/user.html` |

**검증**:
- 신규 `tests/test_orchestration/test_plan138_doc_command.py` 38건 통과(판정표 11 · 비일치 8 · 내용어 비판정 · 문서군 고정 · 계층 A 단락 · 비활성 안내 · 답변 턴 제외 4 · 출구 멱등 · 별칭 파급 · W4 3 · W5 2 · W6 · 프롬프트 활성/비활성).
- 기존 테스트 2곳의 엔진 대역 fixture에 서술 LLM 대역(`_answer_llm` → 주입 LLM) 추가 — `test_plan127_doc_query.py` · `test_plan127_doc_active_path.py`.
- 관련 모듈 회귀(D-303 — 분해·문서·소스 지목·레지스트리를 import하는 76개 파일): 현재 30 실패 / HEAD 격리 사본 29 실패 — 차이 1건(`test_query_stream_progress.py::test_timeout_error_carries_failure_trace[text]`)은 양쪽에서 반복 실행 시 통과·실패가 갈리는 시간 의존 불안정 테스트 → **신규 실패 0**. 기존 실패 29건(스트림 시간 상한·스트림 범위·SQL 운반·평가 모드·구조화 분해 재질의)은 이번 범위 밖.
- `arch_check --ci` 위반 0 · `overfit_check --ci` 신규 유입 0 · `prompt_render_diff --ci` 바이트 동일 · `pytest tests/test_manual` 543 통과. `ruff`·`mypy`는 이 환경에 미설치라 `pyflakes`로 대체(신규 경고 0).
- 실 LLM·실 검색 호출 0 — FabriX Retrieval은 사내망 전용이라 §6 재확인은 폐쇄망에서 한다.
