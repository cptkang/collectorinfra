# 129. 자체 문서 RAG 검색 패키지 `doc_rag/` — 벡터 DB·색인·하이브리드 검색을 직접 구성해 FabriX 리트리벌과 같은 계약으로 본체에 붙인다 (NWAgent1 `rag_parser` 참고)

> **작성일**: 2026-09-30 · **v1.0**
> **개정**: 2026-09-30 **v1.1** — ①**게이트 G-1~G-12 확정**(사용자 *"HWP는 사용하지 않는다 고려하지 않아도 되고 나머지는 권고에 맞게 진행하라."*) — HWP 제외(G-3) · 나머지 권고안 채택 ②**백엔드 선택 스위치 신설(G-12 · §4.2a)** — 사용자 질문 *"현재 구현하는 RAG와 구현되어 있는 RAG를 선택적으로 사용하도록 계획이 되어 있냐?"*에 대한 답이 **아니오**였다. v1.0은 문서군별 FabriX 접속 4키를 로컬 값으로 **덮어쓰는 교체**라 전환하면 FabriX 값이 사라지고(되돌리려면 자주 회전하는 값을 다시 받아야 한다) 혼합 운용 금지도 운영 규칙뿐이었다 → 두 접속값을 함께 두고 `RAG_BACKEND` 하나로 고르며, 시험 표면은 요청마다 고를 수 있게 한다. 이로써 **본체 변경이 「코드 0」에서 「선택 스위치 한 벌」로 바뀐다**(기본 `fabrix` = 현행 비트 동일 · D-162) ③D-288 본문 등재
> **상태**: **TODO(계획 · 코드 0)** — 게이트 G-1~G-12 확정(v1.1) · **정보 대기 3건**: G-6 서버 사양(모델 실행 위치 설정값) · G-9 FabriX 리트리벌 과금 여부(비교 측정 전제) · W0 원본 샘플
> **성격**: 참고 코드(NWAgent1) 실측 + 현행 본체 계약 실측 + 라이브러리 실측 + 단계별 구현 계획
>
> **요청 취지(사용자 지시 원문, 2026-09-30)**
> 1. *"126번 계획에서 구현한 fabrix기반의 RAG연동을 별도의 벡터 DB를 구성하여 직접 RAG환경을 구성하려고 한다. 현재 Fabrix환경에서 tools호출이 안되는데 별도의 RAG 연동이 가능하냐?"*
> 2. *"NWAgent1 프로젝트에 보면 RAG 관련 하여 구현한 코드가 있다. 참고하여 RAG를 별도의 패키지로 구현할 계획을 수립하라."*
> 3. (v1.1) *"HWP는 사용하지 않는다 고려하지 않아도 되고 나머지는 권고에 맞게 진행하라. 또한 현재 구현하는 RAG와 구현되어 있는 RAG를 선택적으로 사용하도록 계획이 되어 있냐?"*
>
> **해석**
> - 「별도의 벡터 DB로 직접 RAG 환경」 = 플랫폼이 하던 **파싱·청킹·임베딩·색인·검색·재순위를 우리가 소유**한다. 126의 전제(*"검색·임베딩·색인은 우리가 만들지 않는다"* — `plans/126` 해석 2)를 사용자가 바꾼 것이다 → D-288로 기록한다.
> - 「tools 호출이 안 된다」는 이 계획의 제약이 아니다. 126 엔진은 **코드가 검색하고 LLM은 서술만 1회** 하는 구조라 도구 호출(tool calling)을 쓰지 않는다(§1.1 — 코드 인용). NWAgent1도 같다 — 라우터 의도 `manual_query` → 그래프 노드가 RAG를 직접 부른다(`NWAgent1/semantic_router_graph.py:637-663`).
> - 「RAG를 별도의 패키지로」 = 최상위 패키지 `doc_rag/`를 만든다. **패키지에 답변 생성(LLM)까지 넣을지**는 해석이 둘로 갈려 G-1로 묻는다. 권고는 **검색 백엔드만**이다(답변 엔진 `src/doc_qa/`는 이미 있고 127 라우팅이 그것을 부른다).
>
> **상위/연결 계획**
> - **`plans/126`**(FabriX 리트리벌 연동 — 엔진 `src/doc_qa/` · 시험 표면 T-1~T-3) — 이 계획은 126의 **검색 공급자만 바꾼다.** 엔진·시험 표면·인가·감사는 그대로 쓴다
> - **`plans/127`**(문서 RAG 라우팅 편입 · 2단 처리기 `doc_query`) — 무변경. 처리기는 엔진을, 엔진은 URL을 부를 뿐이다
> - `plans/128`(LLM 게이트웨이 · G-5 「FabriX 리트리벌 편입」) — 관계만 등재(§11)
> - `plans/87`(`apm_gateway/` — 독립 게이트웨이 패키지 전례 · 경계 테스트 방식)
>
> **관련 결정**: D-003(읽기 전용 — 운영 관측 DB 대상. 이 패키지의 색인 저장소는 우리 소유라 대상 밖이지만 **질의 경로는 읽기만**) · D-004(키워드 사전 분류 금지 — NWAgent1 모드 자동 선택을 가져오지 않는 근거) · D-127·D-240(과금 승인 · 실 LLM은 로컬 MLX) · D-131(정본 일원화 — 문서군 의미는 루트 `config/rag_collections.yaml` 하나) · D-139(기능별 최상위 패키지) · D-162(신규 동작 기본 off) · D-198(외부 호출 벽시계 총상한) · D-236(조용히 비는 산출물 금지) · D-255(매뉴얼 동반) · D-274(독립 게이트웨이 패키지 허용 — 관측 소스 한정이던 것을 문서 검색으로 넓히는 것이 D-288) · **D-284**(126 — ② 「검색 파라미터 플랫폼 전속」은 로컬 백엔드에서 「검색 서버 전속」으로 읽는다 · §4.7) · D-286(127)
>
> **결정 등재(예약)**: **D-288** — `docs/02_decision.md` 「채번 이력」 표와 안내 라인에 예약으로 등재했다(2026-09-30). 등재 직전 확인: `## D-` 헤더·「변경 이력」 최댓값 D-287(병행 세션 미커밋 등재분) · 안내 라인 「다음 D-288」 · `plans`·`docs`·`spec`·`tasks`·`CLAUDE.md`에서 D-288을 쓴 곳은 안내 라인과 `plans/87` 변경 이력의 「다음 D-288」 인용뿐 → **D-288**. 게이트 확정 시 본문에 등재한다(**→ 2026-09-30 v1.1 본문 등재 완료** — `docs/02_decision.md` `## D-288`).
>
> **실측 기준**: 브랜치 `multiintent` HEAD `4c2614e` · 2026-09-30. 작업 트리에 병행 세션의 미커밋 변경 7파일(`docs/02` · `plans/87` · `plans/125` · `plans/INDEX.md` · `config/db_registry.yaml` · `src/routing/{registry,zones}.py`)이 있다 — 이 계획과 무관하며 건드리지 않았다. 본체 코드는 **읽기만** 했다(수정 0 · LLM 호출 0 · 서버 기동 0). 라이브러리 확인은 scratchpad 격리 venv에서만 했다.
> **NWAgent1 기준**: `/Users/cptkang/AIOps/NWAgent_bench/NWAgent1`(원격 `cptkang/NWAgent1` · 브랜치 `vLLMBench` · `e641abe0` 2026-06-22). 같은 원격의 `/Users/cptkang/AIOps/NWAgent`(브랜치 `A2A` · `a4548ae2`)와 `rag_parser/` 차이는 `rag/structured_rag.py` 1파일(JSON 장비 조회 — 문서 RAG와 무관)과 매뉴얼 PDF뿐이다(`diff -rq` 실측).
>
> **근거 표기**: **실측** = 실행·설치·셈으로 확인 · **코드** = `파일:라인` 인용 · **추적** = 코드 경로를 따라간 결론(실행 확인 없음) · **추정** = 검증하지 않은 추론 · **미확정** = 사용자·환경 확인이 필요한 값

---

## 0. 요약

### 0.1 한 문장

**문서 파싱·청킹·임베딩·벡터 DB·하이브리드 검색·재순위를 독립 패키지 `doc_rag/`로 만들고, FabriX 리트리벌과 똑같은 HTTP 계약으로 응답하게 한다. 본체는 두 접속값을 함께 두고 `RAG_BACKEND=fabrix|local` 하나로 골라 쓴다(시험 표면은 요청마다 선택) — 답변 생성·인용·인가·감사·채팅 라우팅은 126·127이 만든 것을 그대로 쓴다.**

### 0.2 결론 표

| 질문 | 답 | 근거 |
|---|---|---|
| 도구 호출 없이 되나 | **된다.** 검색은 코드가 HTTP로 부르고(`src/doc_qa/service.py:310`), LLM은 근거를 받아 서술만 1회 한다(`:394`). 채팅 라우팅도 분해 LLM의 JSON 출력(`views`)이다(`src/orchestration/intent_planner.py:1243`) | §1.1 |
| 무엇을 만드나 | 최상위 패키지 **`doc_rag/`** — ①색인 CLI(파싱 → 구조화 → 청킹 → 임베딩 → 적재 → 검증 → 활성화) ②검색 HTTP 서버(하이브리드 → 재순위 → 응답) | §4.1 · §4.3 · §4.4 |
| 본체와 어떻게 붙나 | **FabriX 리트리벌 호환 계약**(`POST {retrieval_id, query}` → `results[]`). 본체 클라이언트가 URL을 받은 그대로 쓰므로(`src/clients/fabrix_retrieval.py:260-265`) **클라이언트·파서·엔진을 한 벌만** 둔다 | §4.2 · G-2 |
| **두 RAG를 골라 쓰나**(v1.1) | **그렇다 — 선택 스위치.** `RAG_BACKEND`(기본 `fabrix` = 현행 비트 동일) + 로컬 공용 접속 2키(`RAG_LOCAL_ENDPOINT`·`RAG_LOCAL_TOKEN`). FabriX 문서군별 4키는 **그대로 남는다** → 전환·되돌리기 = 키 1개 + 「설정 리로드」(재기동 없음). 스위치가 **전역 하나**라 한 턴에 두 백엔드가 섞이지 않는다(점수 척도 혼합의 구조적 차단). CLI·운영자 API·관리자 탭은 **요청마다** 백엔드를 골라 같은 질의를 A/B 비교한다. 로컬 실패 시 FabriX로 **자동 전환하지 않는다**(사유 노출) | §4.2a · G-12 |
| 답변 생성은 | **`src/doc_qa/` 그대로**(근거 0건이면 LLM 0회 · 코드가 인용 부착 · 문서 본문 데이터 구획 · 감사). 패키지에는 LLM이 없다 | G-1 |
| 벡터 DB | **Qdrant**(Apache-2.0). 1차는 **내장 모드**(서버 운영 0 — 파일 경로로 연다). 필요하면 같은 코드로 서버 모드. 내장 모드에서 **밀집+희소 벡터 RRF 결합 검색이 동작함을 실측**했다 | §3.5 · G-7 |
| 검색 구성 | 현 FabriX 설정(126 §3.1a: Hybrid · RRF · BGE-M3 · rerank ≥0.3 · Top K 3 · 후보 15)과 **같은 축**을 재현한다 — 밀집(BGE-M3) + 어휘(희소) → RRF → 교차 인코더 재순위 → 하한 0.3 → 상위 3. HyDE는 1차에 넣지 않는다(패키지에 LLM 0) | §4.4 · §4.5 |
| NWAgent1에서 가져오는 것 | **파서(요소 분류·표 → 마크다운·글꼴 기반 제목 판정)·챕터 분할·헤더 기반 청킹 구조·임베딩 로더** — 시스코 전용 규칙을 걷어내고 한국 규정 번호 체계로 일반화 | §2 |
| 가져오지 않는 것 | **답변 체인 8종·모드 자동 선택·대화 이력·번역기·LLM 서비스** — 본체에 더 강한 계약이 이미 있거나 우리 규칙과 충돌한다 | §2.2 |
| 가져올 때 반드시 고칠 것 | **8건** — 하이브리드 임베딩 미동작 · 어휘 회수 없는 「하이브리드」 · 비결정 ID로 재색인 중복 · 짧은 청크 폐기(짧은 조항 유실) · 네트워크 탐지로 오프라인 판정 · PyMuPDF AGPL · 점수 재필터·문자열 신뢰도 · 질의 증강 | §2.3 |
| 가장 큰 위험 | ①**원본 형식**(스캔 PDF면 텍스트가 없다 — HWP는 사용자 확정으로 제외) ②**점수 척도**(본체 프롬프트가 「0.3 이상만 도달 · 최대 1.0」을 전제 — `src/prompts/doc_answer.py:42`) ③청킹 품질 | §9 |
| 전환 판단 | 같은 골드셋·같은 클라이언트로 **두 백엔드를 한 스크립트가 측정**(hit@3·MRR·0건률·지연)한 뒤 **측정표를 보고 사용자가 정한다**(G-10). FabriX 연동은 선택지로 남긴다(G-11) | §5 W6 · G-10 · G-11 |

### 0.3 착수 순서

```
W0 사전 확인 (원본 샘플·서버 사양·라이선스·모델 반입 · 코드 0)
  → W1 패키지 골격 · 도메인 · 설정 · 경계 테스트
  → W2 파싱 · 구조화 · 청킹            (NWAgent1 이식 + 결함 교정 · 합성 픽스처)
  → W3 임베딩 · 어휘 가중 · 저장소 · 색인 CLI   (세대 · 결정적 ID · 검증 · 활성화)
  → W4 검색 · 재순위 · 점수 계약 · HTTP 서버   ★로컬에서 rag_probe로 첫 실호출(LLM 0)
  → W5 본체 선택 스위치 + 연결 검증 (RAG_BACKEND · 시험 표면 선택 · 골든 픽스처 · 런북 · 관리자 매뉴얼)
  → W6 품질 측정 (골드셋 · 두 백엔드 비교 — FabriX 쪽은 G-9 뒤)
  → W7 서술 end-to-end (로컬 MLX)
  → W8 매뉴얼 · D-288 본문 등재
```

**W4가 「실제로 되는지」를 처음 보는 지점**이다 — W5 스위치 전이므로 **개발 `.env`의 한 문서군 FabriX 4키 자리에 로컬 값을 임시로 넣고** `scripts/rag_probe.py --search-only`(LLM 0)로 확인한다(계약 호환이라 가능). 운영 전환 경로는 W5의 스위치다.

## 1. 배경과 범위

### 1.1 도구 호출과 무관한 이유 (코드)

| 구간 | 무엇이 하나 | 코드 |
|---|---|---|
| 검색 | **코드**가 `retrieve_many()` → `httpx` POST | `src/doc_qa/service.py:310` · `src/clients/fabrix_retrieval.py:260-265` |
| 근거 0건 | LLM 호출 0회 · 결정적 안내 | `src/doc_qa/service.py` 7-b 절 |
| 서술 | LLM 1회(도구 없음) | `src/doc_qa/service.py:394` `astream_text(llm, messages)` |
| 채팅 라우팅(127) | 분해 LLM이 JSON `views: ["doc.hq_manual"]` 선택 → 처리기가 코드로 엔진 호출 | `src/orchestration/intent_planner.py:1243` · `src/orchestration/doc_query.py` |
| 도구 호출이 실제로 필요한 곳 | 1단 `deep_agent` · noise_gate 트랙 B · sre_agent — **vLLM 오케스트레이터 평면**이다(FabriX 아님) | `plans/128` §2 · `src/clients/fabrix_kbgenai.py:377`(`bind_tools`는 등록만) |

따라서 검색 공급자를 바꿔도 도구 호출 지원 여부는 아무 영향이 없다.

### 1.2 포함 · 제외

**포함**
1. 최상위 패키지 `doc_rag/` — 색인 CLI · 검색 HTTP 서버 · 자체 설정 · 자체 테스트
2. FabriX 호환 응답 계약 + 계약 골든 픽스처(패키지·본체 양쪽 테스트)
3. 문서 형식: **PDF · DOCX · TXT/MD**(G-3 확정 — **HWP·HWPX는 사용하지 않는다**(사용자) · PPTX는 원문 부서가 PDF로 내보낸다)
4. (v1.1) **본체 백엔드 선택 스위치**(§4.2a) — 설정 3키 · 접속 해석 분기 · 시험 표면 요청별 선택 · 캐시 키·감사·진단의 백엔드 표기 · 관리자 탭 선택 상자 · 설정 도움말 · 매뉴얼
5. 품질 측정: 126 W7(골드셋)을 흡수해 **두 백엔드를 같은 스크립트로** 측정
6. 런북 `docs/33_doc_rag_runbook.md` · 관리자 매뉴얼 절 · D-288

**제외**
| 항목 | 이유 | 어디로 |
|---|---|---|
| 답변 생성(LLM)을 패키지에 넣기 | 두 번째 답변 엔진이 생긴다 — 0건 처분·인용·인가·감사가 갈라진다 | G-1 (b)를 고르면 재설계 |
| HyDE(질의 → 가상 답변 → 검색) | 패키지에 LLM이 필요하다. 측정으로 이득이 보이면 본체 쪽 질의 확장으로 | §11 |
| OCR(스캔 PDF) | 텍스트 없는 페이지는 **보고서에 드러내고** 색인은 계속한다 | §11 |
| 문서 단위 권한 | 우리 원본에는 권한 메타가 없다. 문서군 단위 인가(126 `authz`)만 | §11 |
| 관리자 화면에 색인 상태(세대·청크 수) 표시 | 1차는 `/health`·`/collections`·`rag_probe --raw`. 백엔드 선택 상자(§4.2a)만 1차에 넣는다 | §11 |
| 본체 라우팅·프롬프트·답변 엔진 로직 변경 | 계약 호환으로 필요 없다 — 엔진은 `backend` 인자를 접속 해석에 **전달만** 한다 | — |
| 문서군마다 백엔드를 따로 고르기 | 한 턴에 척도가 다른 점수가 섞인다(§3.2) — 스위치는 전역 하나 | G-12 |
| 로컬 실패 시 FabriX 자동 전환 | 침묵 폴백 금지(D-236) · 어느 백엔드가 답했는지 흐려진다 | G-12 |

### 1.3 이 분리가 성립하는 근거

본체 엔진의 입력은 `(문서군, 질의)` 쌍이고 검색은 **접속 4종(URL 전문·토큰·클라이언트 키·`retrieval_id`)을 받은 그대로 쓰는** HTTP 호출이다(126 §3.1 「URL은 조립하지 않고 전문을 저장한다」). 그래서 **같은 요청을 받아 같은 모양의 응답을 돌려주는 서버**면 누가 만들었든 엔진은 차이를 모른다. 클라이언트·파서·엔진을 한 벌만 두어도 되는 이유다. 다만 **접속값을 바꿔 끼우는 것만으로는 두 RAG를 「골라 쓰지」 못한다**(v1.0의 한계 — 한쪽 값이 사라진다) → 본체에 선택 스위치를 둔다(§4.2a).

## 2. NWAgent1 `rag_parser` 실측 — 가져올 것 · 고칠 것 · 버릴 것

### 2.1 구성 (실측 · 6,576줄)

```
rag_parser/
  parser/        layout_parser.py(525) · markdown_converter.py(232)   PDF → 요소 → 마크다운 → 챕터
  preprocessing/ pipeline.py(155)                                     위 셋을 잇는 전처리
  splitter/      cisco_splitter.py(291)                               헤더 분할 + 재귀 분할 + CLI 블록 보존
  embedding/     embedding_service.py(338) · dual_embedding_service.py(470) · model_selection.py(342)
                 offline_utils.py(169) · benchmark.py(160)
  vectorstore/   chroma_store.py(261) · indexing_pipeline.py(251) · json_indexer.py(106)
  rag/           rag_chain.py(631) · advanced_rag.py(816) · rag_factory.py(330)
                 multilingual_rag.py(468) · structured_rag.py(109)
  llm/           llama_service.py(329)      translation/ translator.py(314)
  main.py(133)                                                        CLI(대화형 · 단일 질의)
```

흐름: `IndexingPipeline.index_pdf` → `PreprocessingPipeline.process`(파싱 → 마크다운 → 챕터 파일) → `CiscoManualSplitter.split_document` → `VectorStore.add_documents`(ChromaDB `PersistentClient` · 코사인) → 질의 시 `UnifiedRAGInterface.query` → `RAGFactory.auto_select_mode`(키워드) → 체인별 검색·프롬프트·Ollama 생성.

**테스트**: 문서 RAG 경로를 고정하는 단위 테스트가 거의 없다 — `tests/test_rag.py`는 `structured_rag`(JSON 장비 조회), `tests/test_rag_integration.py`는 라우터 + 실 LLM 통합, `splitter/test_splitter.py`는 함수 1개다(실측). **가져오는 코드는 테스트를 새로 쓴다.**

**NWAgent1 문서의 수치는 인용하지 않는다** — `doc/EMBEDDING_STRATEGY_GUIDE.md:179-187`의 「영문 전용 95% · 다국어 88%」 등은 측정 조건·원자료가 없다.

### 2.2 처분 표 (요약 — 파일별 전수는 부록 A)

| NWAgent1 | 처분 | 이유 |
|---|---|---|
| `parser/layout_parser.py` 요소 분류(제목·문단·목록·표·경고) · 표 → 마크다운 · 글꼴 크기·굵기 제목 판정 · 위치 정렬 | **가져와 일반화** | 규정·설계 문서에도 그대로 필요하다. 시스코 CLI 패턴 제거 · 한국 규정 번호 체계 추가 · **PyMuPDF → pdfplumber**(§2.3 ⑥) |
| 같은 파일의 `unstructured` hi_res 경로 | 버림 | NLTK 데이터·레이아웃 모델을 받아야 한다(폐쇄망) — NWAgent1도 오프라인이면 PyMuPDF로 떨어진다(`layout_parser.py:108-117`) |
| `parser/markdown_converter.py` `ChapterSplitter`(제목 수준 분할 · 페이지 범위 frontmatter) | **가져옴** | 인용에 쓸 **페이지 범위·섹션 경로**의 출처다. 디스크 중간 파일 대신 메모리로 |
| `splitter/cisco_splitter.py` 헤더 분할 → 재귀 분할 · 한글 길이 함수 · 블록 보존 | **구조 차용** | 틀은 맞다. 짧은 조각 폐기·시스코 키워드·CLI 전용 규칙은 교정(§2.3 ④) |
| `embedding/embedding_service.py` 로컬 경로 해석 · E5 접두 · 정규화 · 배치 | **가져옴(축소)** | 네트워크 탐지 제거(§2.3 ⑤) · 로딩 규율은 본체 `noise_gate/infrastructure/embedding_provider.py`(로컬 디렉토리만)와 맞춘다 |
| 같은 파일 `HybridEmbeddingService` | 버림 | 희소 경로가 동작하지 않는다(§2.3 ①) |
| `dual_embedding_service.py` · `model_selection.py` · `translation/` · `rag/multilingual_rag.py` | 버림 | 영문 매뉴얼 + 한국어 질의용이다. 우리 코퍼스는 한국어 · 번역기는 Google/DeepL **외부 API**(외부 송신 — D-127 대상) |
| `vectorstore/chroma_store.py` | **인터페이스만 차용**(추가·검색·통계·메타 필터) | 구현은 교체 — 비결정 ID·upsert 없음·세대 없음·밀집 전용(§2.3 ③) |
| `vectorstore/indexing_pipeline.py` | **흐름 차용** | 디렉토리 일괄 · 통계 JSON은 좋다. 파일별 실패 계속 · 세대 빌드 → 검증 → 활성화로 확장 |
| `preprocessing/pipeline.py` | 흐름 차용 | Windows poppler PATH 조작(`pipeline.py:14-24`) 제거 |
| `rag/rag_chain.py` `RAGChain` · `ReRankingRAGChain` | 버림 | 답변 엔진은 `src/doc_qa/`가 더 강한 계약으로 있다. LLM 1~10점 재순위 + 정규식 파싱(`rag_chain.py:466-630`)은 비결정적 → **교차 인코더**로 대체 |
| `rag/advanced_rag.py` 6종 · `rag/rag_factory.py` | 버림 | 키워드 모드 자동 선택(`rag_factory.py:162-201` — D-004 성격 충돌) · 메모리 대화 이력(체크포인터 소유) · 질의 증강(§2.3 ⑧) |
| `llm/llama_service.py` | 버림 | 패키지에 LLM 없음(G-1) |
| `vectorstore/json_indexer.py` · `rag/structured_rag.py` | 해당 없음 | 구조화 JSON 조회 |
| `main.py` | 참고 | 우리 CLI는 색인 관리 + 검색 진단(`search`) |

### 2.3 가져올 때 반드시 고칠 것 (8건)

| # | 결함 | 근거 | 우리 설계 |
|---|---|---|---|
| ① | **하이브리드 임베딩이 동작하지 않는다.** `HybridEmbeddingService`가 `SentenceTransformer.encode(..., return_dense=True, return_sparse=True)`를 부르고 `outputs['dense_vecs']`를 읽는다(`embedding_service.py:316-333`). 그 인자는 FlagEmbedding `BGEM3FlagModel`의 것이다 | **실측**: sentence-transformers 5.6.0 `encode` 시그니처에 `return_sparse` 없음(`**kwargs`만) → 배열 반환 또는 예외 → `except`에서 밀집으로 폴백(**추적**). 호출처도 0건(export만 · grep 실측) | 어휘 경로를 **별도 희소 인덱스**로 만든다(§4.4 · G-5) |
| ② | **「하이브리드 검색」에 어휘 회수가 없다.** `HybridSearchRAG`는 벡터 상위 2k건을 시스코 정규식 키워드로 **재가중**할 뿐이다(`advanced_rag.py:54` `query` · `:138` `_rerank_with_keywords`) | **코드** | 벡터 상위에 없는 조항 번호·약어는 영원히 못 찾는다 → **어휘 인덱스에서 독립적으로 후보를 뽑아** RRF로 합친다 |
| ③ | **재색인하면 중복 적재된다.** ID가 `doc_{count+i+j}`(`chroma_store.py:99`) · upsert·문서 단위 삭제 없음 · 세대 없음 | **코드** | 결정적 ID(uuid5) · 세대 단위 새 컬렉션 → 검증 → 활성화 · 되돌리기(§4.3) |
| ④ | **짧은 청크를 버린다.** `min_chunk_size` 미만(기본 100 「토큰」 · 한글 1자 ≈ 1.5)을 `continue`(`cisco_splitter.py:217-218`) | **코드** — 한글 약 67자 미만 조각이 사라진다. 규정의 짧은 조항(「제5조(적용 범위) 이 규정은 …」)이 통째로 색인에서 빠질 수 있다 | **버리지 않고 같은 상위 섹션의 이웃과 병합**한다 · **원문 커버리지 불변식**(모든 원문 문자가 어느 청크엔가 들어간다)을 테스트로 고정 |
| ⑤ | **네트워크 연결 시도로 오프라인을 판정한다.** `huggingface.co:443` 소켓 연결(`offline_utils.py:47-68`) | **코드** — 폐쇄망 서버에서 외부 연결을 시도한다 · 본체 테스트의 네트워크 가드와도 어긋난다 | **로컬 디렉토리 경로만** 받는다 · 로드 전 `HF_HUB_OFFLINE=1` · 디렉토리가 아니면 **기동 실패**(검색 서버에게 임베딩은 핵심 기능이라 inert 강등이 아니라 fail-fast · 사유는 `/health`) |
| ⑥ | **PyMuPDF는 AGPL-3.0이다.** | **실측**: 설치본 메타데이터 `pymupdf 1.28.2` = *"Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial"* · `pdfplumber 0.11.10` = MIT · `pdfminer.six` = MIT | 기본 파서는 **pdfplumber**. 줄 단위 글꼴 크기·글꼴명이 나온다(**실측** — `extract_text_lines()` → `('Chapter 3 Accounts', 18.0 · Helvetica-Bold)`) → NWAgent1의 글꼴 기반 제목 판정을 그대로 옮길 수 있다. PyMuPDF는 G-4 허용 시에만 |
| ⑦ | **점수로 다시 거르고, 답변 문자열로 신뢰도를 매긴다.** `score_threshold` 0.5(`rag_chain.py:63,104`) · 「없습니다」가 답에 있으면 감점(`:289-290`) | **코드** — 126 §3.3 ①(재필터 금지)과 충돌 · 문자열 휴리스틱은 비결정 | 필터는 **서버의 재순위 하한 한 곳**(§4.6). 본체는 지금처럼 재필터 0 · 신뢰도 점수 없음 |
| ⑧ | **질의를 고쳐서 검색한다.** 「설정 방법 절차 명령어」 덧붙이기(`advanced_rag.py:468-487`) · 진단 명령어 덧붙이기(`:587`) | **코드** — 126 §4.5 질의 규칙 1(원문 보존 — 어휘 경로가 사내 용어·조항 번호에 의존)과 충돌 | 받은 질의를 **그대로** 검색한다(공백·전각 정규화만) |

## 3. 현행 본체 실측 — 붙일 자리

### 3.1 교체 지점 = HTTP 계약 (`src/clients/fabrix_retrieval.py`)

| 항목 | 현행 | 코드 |
|---|---|---|
| 요청 | `POST <URL 전문>` · 헤더 `x-openapi-token` · `x-generative-ai-client` · 본문 `{"retrieval_id", "query"}` | `:260-265` |
| 판정 순서 | ①오류 봉투(`error`·`errorCode` — HTTP 상태 무관) → ①-b PII 차단 형태 → ②`results` 파싱 → ③빈 배열 = `empty` | `interpret_body` `:174-216` |
| 읽는 필드 | `results[]`의 `content` · `title` · `filename` · `doc_id` · `catalog_id` · `rank` · `rank_score` · `url` · `content_type` · `id` · `has_permission`(false면 제외) · `content_info.subtitle` · `executed_query` / 최상위 `hyde_query` | `parse_results` `:122-171` |
| 자산 폐기 판정 | `errorCode`가 `NoContent` **그리고** `message`에 `retrieval does not exist` → `stale_id` | `:103-119` |
| HTTP 실패 | 4xx·5xx인데 본문이 정상 형태면 `error` | `:285-292` |
| 타임아웃 | read 상한 + 벽시계 총상한(`RAG_TIMEOUT` 12 · `RAG_TOTAL_TIMEOUT` 20) | `:219-232` · `:300-314` |

### 3.2 점수 척도를 전제하는 곳 — **서버가 지켜야 할 계약**

| 위치 | 전제 | 로컬 서버가 어기면 |
|---|---|---|
| `src/prompts/doc_answer.py:42-43` | *"유사도는 **0.3 이상만 이 목록에 도달**하며 1.0이 최대입니다"* — LLM이 근거 강약을 읽는 기준 | RRF 점수(0.0x~0.x)나 코사인 원점수를 그대로 내면 **프롬프트가 거짓**이 되고 LLM이 모든 근거를 약하다고 읽는다 |
| `src/doc_qa/evidence.py:68-72` | 컬렉션이 달라도 **같은 리랭커 · 같은 척도**라 전역 정렬 | 한 문서군은 FabriX, 한 문서군은 로컬이면 척도가 섞인다 |

→ §4.6 점수 계약으로 **서버 쪽에서** 지킨다(본체 무변경). 실측: Qdrant RRF 결합 점수는 `0.75 · 0.6667 · 0.5` 같은 **순위 점수**라(§3.5) 그대로 내면 안 된다.

### 3.3 접속 설정 (현행 — 선택 스위치를 넣을 자리는 §4.2a)

- 문서군별 **정적 4키**: `RAG_HQ_MANUAL_{ENDPOINT,TOKEN,CLIENT_KEY,RETRIEVAL_ID}` · `RAG_ARCH_DOCS_*`(`.env.example:1055-1063`). **넷 다 있어야** 문서군이 켜진다(126 R-8).
- 관리자 화면 「환경변수 설정」에서 편집 → 「설정 리로드」(126 W1 · `RELOADABLE_KEYS`).
- 접속 해석은 한 함수다 — `resolve_collections()`(`src/infrastructure/doc_sources.py:205`)가 정본 의미 + `_connection_of()`(`:190` — `CONNECTION_FIELD_MAP` 정적 표)를 결합한다. 호출처 5곳: 엔진(`src/doc_qa/service.py:250`) · 채팅 처리기 인가(`src/orchestration/doc_query.py:168`) · 운영자 API(`src/api/routes/doc_search.py:81`·`:131`) · CLI(`scripts/rag_probe.py:73`). **선택 스위치를 이 함수 한 곳에 넣으면 모든 경로가 같은 규칙을 쓴다**(§4.2a).
- 캐시 키 `rag:hit:{문서군}:{retrieval_id}:{질의 해시}`(`src/doc_qa/service.py:99-106`).
- `DocCollection.asset_recorded_at`은 `retrieval_id` 앞 14자리 숫자만 날짜로 읽는다(`src/infrastructure/doc_sources.py:85-94`) — 로컬 문서군 id(`hq_manual`)는 빈 문자열이 되어 **오표시가 없다**(코드).

### 3.4 본체의 선례 (패키지는 import하지 않고 방식만 따른다)

| 선례 | 무엇을 따르나 |
|---|---|
| `noise_gate/infrastructure/embedding_provider.py` | 로컬 디렉토리에서만 로드 · 로드 전 오프라인 환경변수 · lazy import |
| `src/schema_cache/synonym_semantic.py` | 임베딩 백엔드 2종(`local` 인프로세스 / `vllm` `/v1/embeddings`) |
| 루트 `multilingual-e5-small/`(`.gitignore:49`) | 모델 디렉토리 반입 관례(git 밖) |
| `apm_gateway/`(pyproject · `tests/test_boundary.py`) | 2단 중첩 독립 패키지 · 루트 venv 공유 · AST 경계 테스트(`arch_check`가 2단 중첩을 해석하지 못해 패키지 안에서 검사 — `plans/87` G-11) |
| `noise_gate/tests/test_embedding_provider_realmodel.py` | 실모델 테스트 옵트인 = 모델 경로 환경변수 + `skipif` |

### 3.5 라이브러리 실측 (2026-09-30)

| 확인 | 결과 |
|---|---|
| 루트 venv | Python 3.12.11 · torch 2.13.0 · sentence-transformers 5.6.0 · transformers 5.14.1 · numpy 2.5.1 **이미 설치** · python-docx 1.2.0 설치 · qdrant-client·chromadb·pdfplumber·PyMuPDF 미설치 |
| 의존성 해석(`uv pip compile`, 루트 `pip freeze` 142개 고정 + 추가분) | `qdrant-client 1.19.1` · `pymupdf 1.28.2` · `pdfplumber 0.11.10` 해석 성공 · 루트 고정 버전 변경 0 · `chromadb 1.5.9`(+`onnxruntime 1.30.0`)도 해석 |
| **Qdrant 내장 모드 하이브리드**(격리 venv · `QdrantClient(path=…)`) | 이름 붙은 밀집 벡터 + 희소 벡터 컬렉션 → `query_points(prefetch=[밀집, 희소], query=FusionQuery(RRF))` **동작** · 결과 `[('b', 0.75), ('c', 0.6667), ('a', 0.5)]` |
| sentence-transformers 5.6.0 | `CrossEncoder`(재순위) · `SparseEncoder` 존재 · `encode`에 `return_sparse` 없음 |
| 라이선스(설치본 메타데이터) | qdrant-client Apache-2.0 · pdfplumber MIT · pdfminer.six MIT · pypdfium2 BSD-3/Apache-2.0 · **PyMuPDF AGPL-3.0 또는 상용** |

**아직 모르는 것**(W0): Qdrant 내장 모드의 희소 벡터 `Modifier.IDF` 지원 · BGE-M3·재순위 모델의 CPU 지연·메모리(아래 수치는 **추정**) · 서버 사양.

## 4. 설계

### 4.1 배치 — 독립 패키지 `doc_rag/` (별도 프로세스 · 루트 venv 공유)

```
 본체 프로세스 (python -m src.main --server)             doc_rag 프로세스 (python -m doc_rag serve)
 ┌──────────────────────────────────────────┐           ┌───────────────────────────────────────────┐
 │ 채팅 → intent_planner → doc_query(127)    │           │ POST /retrieval  (FabriX 호환)             │
 │ 관리자 탭 · rag_probe · /api/v1/doc/search │           │   질의 → 밀집 임베딩 + 어휘 희소 벡터        │
 │        ↓                                   │ =local   │   → Qdrant RRF 후보 15                      │
 │ src/doc_qa (엔진: 인가·예산·서술·인용·감사) │ ───────▶ │   → 교차 인코더 재순위 → 하한 0.3 → 상위 3 │
 │        ↓                                   │ 127.0.0.1│ GET /health · GET /collections              │
 │ 접속 해석: RAG_BACKEND=fabrix|local       │  :9094   │ 색인 저장소 (Qdrant 내장 · 세대 디렉토리)   │
 └──────────────────────────────────────────┘           └───────────────────────────────────────────┘
          │  RAG_BACKEND=fabrix (기본)                              ▲
          ▼  (클라이언트 src/clients/fabrix_retrieval 한 벌)    python -m doc_rag index build|verify|activate
   FabriX Retrieval Connector (현행 · 선택지로 유지)        (원본 문서 → 파싱 → 청킹 → 임베딩 → 새 세대)
```

**왜 별도 프로세스인가**
1. **메모리 격리** — 임베딩 + 재순위 모델이 수 GB다(추정 §4.5). 본체 FastAPI 프로세스에 싣지 않는다.
2. **색인 작업 격리** — 대량 임베딩(수 분~수십 분)이 채팅 응답을 막지 않는다.
3. **계약이 곧 경계** — HTTP 계약이 FabriX와 같으므로 본체는 클라이언트·파서·엔진을 한 벌만 두고 **선택 스위치만** 더한다(§4.2 · §4.2a).
4. 전례 — `mcp_server/` · `apm_gateway/` · `sre_agent/`(D-139 · D-274).

**왜 루트 venv를 공유하나** — torch·sentence-transformers가 이미 루트에 있고, 추가분이 루트 고정 버전을 건드리지 않고 해석된다(§3.5). `apm_gateway` 전례(≥3.11 · 루트 공유). 자체 `pyproject.toml`에 의존성을 선언해 따로 배포할 수도 있게 한다.

**디렉토리**

```
doc_rag/
  pyproject.toml          # name = "doc-rag" · 의존성 선언 · pytest testpaths = ["tests"]
  README.md
  config/
    corpus.yaml           # 문서군별 원본 위치·형식·제목 패턴·청킹 수치 (id는 루트 config/rag_collections.yaml과 같아야 한다)
  doc_rag/
    __main__.py           # 엔트리 하나: serve | index … | search …
    config.py             # pydantic-settings DOC_RAG_*
    domain/               # Element · Section · Chunk · Hit · 점수 계약 상수 (순수)
    ingest/               # pdf.py(pdfplumber) · docx.py · text.py · (hwpx.py · pptx.py — G-3)
    structure/            # 제목 판정 · 번호 체계(장·절·조·항·호·목) · 섹션 트리 · 페이지 추적
    chunking/             # 섹션 우선 분할 · 재귀 분할 · 작은 조각 병합 · 보존 블록(표·코드)
    embedding/            # dense: local(ST) | vllm ; lexical: 토크나이저 + 희소 가중
    rerank/               # CrossEncoder local | vllm score
    store/                # Qdrant(내장|서버) · 세대 · 결정적 ID · 임베딩 캐시
    search/               # 하이브리드 → RRF → 재순위 → 하한 → 상위 k
    server/               # FastAPI: POST /retrieval · GET /health · GET /collections
    cli/                  # index build|verify|activate|rollback|list|prune · search
  tests/                  # 모델 0 · 네트워크 0이 기본 · 실모델은 옵트인
  testdata/               # 합성 PDF·DOCX 픽스처(실문서 금지) · 계약 골든 응답
```

**패키지 내부 계층**: domain → config → infrastructure(`ingest`·`embedding`·`rerank`·`store`) → application(`structure`·`chunking`·`search`·색인 흐름) → interface(`server`·`cli`) → entry(`__main__`). `arch_check`가 2단 중첩을 해석하지 못하므로 **패키지 안의 `tests/test_boundary.py`가 검사**한다(`apm_gateway` 방식) — ①양방향 import 0(`doc_rag` ↔ `src`·`noise_gate`·`sre_agent`·`mcp_server`·`apm_gateway`) ②계층 방향 ③**LLM 클라이언트 import 0**(`langchain_openai`·`openai`·FabriX 클라이언트 — G-1 (a)를 기계로 고정).

### 4.2 본체와의 계약 — FabriX 리트리벌 호환 (G-2)

**요청** (본체가 지금 보내는 그대로)

```
POST http://127.0.0.1:9094/retrieval
x-openapi-token: <DOC_RAG_API_TOKEN>
x-generative-ai-client: <호출자 표지 — 비어 있지만 않으면 된다>
{"retrieval_id": "hq_manual", "query": "비밀번호 변경 주기는?"}
```

**응답 필드**

| 필드 | 우리가 채우는 값 |
|---|---|
| `results[].id` | 청크 id(uuid5 — 결정적) |
| `results[].doc_id` | 원본 파일 sha256 앞 16자 |
| `results[].catalog_id` | 문서군 id(`hq_manual`) |
| `results[].title` | 문서 제목(문서 속성 → 첫 1수준 제목 → 파일명 순) |
| `results[].filename` | 원본 파일명 |
| `results[].content` | 청크 본문(머리에 섹션 경로 1줄 — §4.3) |
| `results[].content_info.subtitle` | 섹션 경로 + 페이지 — 예 `제3장 계정관리 > 제12조(비밀번호) · p.14–15` |
| `results[].rank` · `rank_score` | 1..k · **재순위 점수(0~1 · 하한 이상만)** — §4.6 |
| `results[].has_permission` | `true`(문서 단위 권한 없음 — §1.2) |
| `results[].url` | `<파일명>#page=<시작 페이지>`(본체 `RAG_DOC_URL_BASE`와 결합 — 없으면 본체가 파일명만 표기) |
| `results[].content_type` | `text` · `table` |
| `results[].executed_query` | 받은 질의 그대로(HyDE 없음 → 본체 진단 `hyde_applied=false`) |
| 추가(본체 파서가 무시 · 원시 응답·진단용) | `engine: "doc_rag"` · `index_generation` · `timings_ms{embed,search,rerank}` · `candidates` |

**오류 봉투** (본체 판정 순서가 그대로 읽는다)

| 상황 | 응답 | 본체 처분 |
|---|---|---|
| 모르는 `retrieval_id`(오타 · 미색인 · 폐기 세대) | HTTP 404 + `{"message": "retrieval does not exist. retrieval id: <id>", "error": "data is invalid", "errorCode": "NoContent"}` | `stale_id` — 사유 문구가 「자산 ID 폐기 — 관리자 교체 필요」라 로컬에서는 **「문서군 id 확인 · 색인 활성화 여부 확인」**으로 런북 판독표에 추가(W5) |
| 인증 실패 | HTTP 401 + `{"error": "unauthorized", "errorCode": "Unauthorized", "message": "…"}` | `error` |
| 모델 미로드 · 서버 예산 초과 · 동시성 초과 | HTTP 503 + `errorCode` `Unavailable` / `Timeout` / `Busy` | `error`(사유 노출 — 침묵 폴백 없음) |
| 결과 0건(하한 통과 없음) | HTTP 200 + `{"results": []}` | `empty` — 「문서에 없음」 결정적 안내 · LLM 0회 |

**`retrieval_id` 규칙**
- **문서군 id**(`hq_manual`) = 그 문서군의 **활성 세대 별칭**. 본체 스위치가 `local`이면 **본체가 이 값을 자동으로 보낸다**(§4.2a — `.env`에 넣지 않는다). 재색인해도 id가 바뀌지 않으므로 **126의 자산 ID 회전 문제(R-16)가 로컬에서는 없다.**
- **세대 id**(`20261002103000_hq_manual`) = 특정 세대 고정. 서버는 받아 주지만 본체 스위치 경로는 쓰지 않는다 — 세대 간 회귀 비교(`python -m doc_rag search --generation`·직접 호출)용이다.

**대안(기각) — 자체 계약 + 본체에 두 번째 클라이언트**: 클라이언트·파서·오류 판정·시험 표면이 두 벌이 된다. FabriX 호환은 **추가 필드만으로 확장**되므로 잃는 것이 거의 없다. 대가는 벤더 명명(`x-openapi-token` 등)이 우리 서버에 남는 것뿐이다. **어느 백엔드를 부를지 고르는 일은 계약이 아니라 접속 해석의 몫**이다 → §4.2a.

### 4.2a 백엔드 선택 — 두 RAG를 설정 하나로 골라 쓴다 (G-12 · v1.1 신설)

**v1.0의 한계**: 전환이 문서군별 FabriX 4키를 로컬 값으로 **덮어쓰는** 교체였다. ①전환하면 FabriX 값이 사라지고, 되돌리려면 자주 회전하는 FabriX 값(126 §3.1b)을 다시 받아야 한다 ②두 백엔드를 같은 질의로 나란히 비교할 표면이 없다 ③혼합 운용 금지가 운영 규칙뿐이었다.

**설정 (본체 `RagConfig` · 3키 추가)**

| 키 | 기본 | 뜻 |
|---|---|---|
| `RAG_BACKEND` | **`fabrix`** | 채팅·운영자 API·CLI의 **기본 백엔드** — `fabrix` \| `local`. **전역 하나**다 |
| `RAG_LOCAL_ENDPOINT` | 빈 값 | 검색 서버 주소 전문 — 예 `http://127.0.0.1:9094/retrieval` |
| `RAG_LOCAL_TOKEN` | 빈 값 | 검색 서버 토큰(`DOC_RAG_API_TOKEN`과 같은 값) |

- **FabriX 문서군별 4키(`RAG_HQ_MANUAL_*` 등)는 그대로 둔다** → 두 접속값이 함께 있다.
- **로컬은 공용 접속**이다 — 검색 서버 하나가 모든 문서군을 서비스하므로 문서군별 키가 필요 없다. 해석 규칙: `endpoint` = `RAG_LOCAL_ENDPOINT` · `token` = `RAG_LOCAL_TOKEN` · `client_key` = 상수 `collectorinfra`(서버가 호출자 표지로만 쓴다) · `retrieval_id` = **문서군 id**(활성 세대 별칭 — §4.2).
- 로컬 접속 2키 중 하나라도 비면 그 백엔드의 문서군은 **비활성 + 사유**(`RAG_LOCAL_ENDPOINT 미입력` 등 — 126 사유 누적 규칙 그대로).

**해석 (한 함수 · 모든 경로 공통)**

- `resolve_collections(rag, *, backend=None, path=None)` — `backend`가 None이면 `RAG_BACKEND`. 결과 `DocCollection`에 `backend` 필드를 싣는다. 호출처 5곳(§3.3)이 같은 규칙을 쓴다.
- 엔진 `answer_from_documents(..., backend=None)` — 인자를 해석 함수에 **전달만** 한다. 검색 단계 이후 로직은 바뀌지 않는다.
- **채팅(127 `doc_query`)은 선택하지 않는다** — 늘 설정값을 쓴다. 채팅 사용자가 백엔드를 고르는 표면은 만들지 않는다.

**시험 표면 — 요청마다 선택 (A/B 비교)**

| 표면 | 선택 방법 | 생략하면 |
|---|---|---|
| T-1 CLI `scripts/rag_probe.py` | `--backend fabrix\|local` · `--list`는 **두 백엔드의 접속 상태를 함께** 보인다 | 설정값 |
| T-2 운영자 API `POST /api/v1/doc/search` | 본문 `backend` 필드(관리자 전용 · 허용값 외 422) · `GET /api/v1/doc/collections`는 백엔드별 상태 | 설정값 |
| T-3 관리자 탭 「문서 검색 시험」 | **백엔드 선택 상자**(기본 = 현재 설정값) · 접속 상태 표에 두 백엔드 열 | 설정값 |

같은 질의를 백엔드만 바꿔 두 번 보내면 결과·점수·소요 시간을 나란히 볼 수 있다. 한 요청 안에서는 백엔드가 하나라 점수 척도가 섞이지 않는다.

**전역 하나인 이유 — 혼합의 구조적 차단**: 문서군마다 고르게 하면 한 턴에 FabriX 점수와 로컬 점수가 함께 올라와 `evidence.py`의 전역 정렬 전제(같은 리랭커)가 깨진다(§3.2). v1.0은 이것을 운영 규칙으로 막았고, v1.1은 **설정 구조로 불가능하게** 한다.

**부수 규칙**

1. **자동 폴백 금지** — 로컬이 실패(503·연결 불가)해도 FabriX로 넘어가지 않는다. 엔진이 사유(`검색 서비스에 연결하지 못했습니다` 등)를 그대로 낸다. 어느 백엔드가 답했는지가 흐려지면 품질 판단·감사가 무너진다(D-236 · 126 G-11과 같은 결).
2. **캐시 키** — FabriX는 **현행 형식 그대로**(`rag:hit:{문서군}:{retrieval_id}:…` — 비트 동일), 로컬만 접두 `rag:hit:local:`을 붙인다. 로컬 `retrieval_id`는 별칭이라 재색인해도 바뀌지 않으므로 **활성화 직후 TTL(`RAG_CACHE_TTL` 기본 300초) 동안 이전 세대 결과가 나올 수 있다** — `index activate` 출력과 런북에 적는다.
3. **감사·진단** — `log_doc_retrieval`에 `backend` 필드 · 엔진 `diagnostics["backend"]` · 요청별 선택이면 `backend_source: "request"`(설정값이면 `"config"`).
4. **설정 카탈로그** — 3키를 `rag` 그룹에 등재 · **`RELOADABLE_KEYS`**(재기동 없이 「설정 리로드」로 전환) · `SENSITIVE_VALUE_KEYS`에 `RAG_LOCAL_ENDPOINT`·`RAG_LOCAL_TOKEN`(126 G-14와 같은 「마스킹 O · 편집 O」) · 도움말 `config/settings_help/rag.yaml` 3항목 · `.env.example` 3줄(값 없음).
5. **기본값 `fabrix` = 현행 비트 동일**(D-162) — 키를 넣지 않은 배포는 해석·캐시 키·감사 필드 외 동작이 같다(감사에 `backend: "fabrix"` 필드가 더해지는 것만 다르다).
6. **새 문서군 추가가 로컬에서는 쉬워진다** — 로컬은 공용 접속이라 `CONNECTION_FIELD_MAP`(FabriX 정적 4키 표)에 없는 문서군도 루트 정본 + `corpus.yaml` + 색인만으로 켜진다. FabriX는 종전대로 정적 4키가 필요하다(`docs/32` §5).

**전환 절차** — 관리자 화면 「환경변수 설정」 → `RAG_BACKEND`를 `local`로 → 저장 → 「설정 리로드」. **되돌리기** = `fabrix`로 같은 절차. 전환 전에 T-3 탭에서 로컬을 골라 몇 건 확인하는 것을 런북 첫 단계로 둔다.

### 4.3 색인 파이프라인 (오프라인 CLI)

```
python -m doc_rag index build   --collection hq_manual          # 새 세대 빌드 + 보고서
python -m doc_rag index verify  --collection hq_manual --generation <G>
python -m doc_rag index activate --collection hq_manual --generation <G>   # 검증 통과 세대만(--force 예외)
python -m doc_rag index rollback --collection hq_manual          # 직전 활성 세대로
python -m doc_rag index list | prune --keep 3
python -m doc_rag search -c hq_manual -q "…"                     # 서버 없이 검색 진단(본문 미출력 옵션)
```

1. **원본** — `DOC_RAG_CORPUS_DIR/<문서군>/` 아래 파일(git 밖 · 권한 0750). 문서 id = 파일 sha256. 형식은 `corpus.yaml`의 허용 목록만(G-3).
2. **파싱** (`ingest/`) — PDF: pdfplumber `extract_text_lines()`로 줄·글꼴 크기·글꼴명 → NWAgent1 `_classify_element`·`_is_bold_text` 판정 이식 · `find_tables()` 표 → 마크다운(`_table_data_to_markdown` 이식) · 표 영역 안 텍스트 중복 제거(`_is_in_table_region` 이식). DOCX: python-docx 문단 스타일(`Heading N`) · 표. TXT/MD: 그대로. **텍스트 없는 페이지(스캔)는 목록으로 보고서에 싣고 색인은 계속**한다(D-236 — 조용히 비지 않는다).
3. **구조화** (`structure/`) — 요소 → 섹션 트리. 제목 판정 = 글꼴(크기·굵기) **그리고/또는** 번호 체계. 기본 번호 세트: `제N장` · `제N절` · `제N조(제목)` · `①` 항 · `1.` 호 · `가.` 목 · `N.N` · `부칙` · `별표`. **문서군마다 `corpus.yaml`에서 켜고 끈다** — 코드에 문서군 특화 리터럴을 두지 않는다(overfit 규율).
4. **청킹** (`chunking/`)
   - 섹션(조) 단위 우선 → 상한 초과면 재귀 분할(문단 → 줄 → 문장) + 겹침
   - **작은 조각은 버리지 않고** 같은 상위 섹션의 이웃과 병합(§2.3 ④)
   - 표·코드는 쪼개지 않는다. 상한을 넘는 표는 **행 단위로 나누고 머리 행을 반복**한다
   - 청크 머리에 **섹션 경로 1줄**(`[제3장 계정관리 > 제12조(비밀번호)]`) — 검색·서술 모두 문맥을 얻는다(NWAgent1 `SemanticChunkEnhancer`의 문맥 보강 의도를 결정적으로)
   - 길이 단위 = **문자 수**(본체 예산이 문자 기반 — 문서당 4,000자). 초기값 상한 1,200자 · 겹침 150자 — **W6 측정으로 확정**
   - 청크마다 페이지 범위 · 섹션 경로 · 요소 종류 보존(인용용)
5. **임베딩** — 밀집(BGE-M3) + 어휘 희소 벡터(§4.4). **임베딩 캐시**(`sha256(모델 id + 청크 텍스트)` → 벡터) — 재색인 때 바뀐 청크만 계산한다.
6. **적재** (`store/`) — 청크 id = `uuid5(ns, "<문서군>|<doc_sha>|<섹션 경로>|<순번>")` — 같은 입력이면 같은 id(재실행 멱등).
   - **세대**: 내장 모드는 한 저장 경로를 **한 프로세스만** 연다(파일 잠금) → 색인 CLI는 **새 세대 디렉토리** `index/<문서군>/<세대>/`에 쓰고, 서버는 **활성 세대만** 읽는다. 활성 포인터 = `index/<문서군>/active.json`(원자적 교체). 서버는 요청마다 포인터 mtime을 보고 바뀌었으면 새 세대를 열고 옛 세대를 닫는다.
   - 서버 모드(G-7)는 Qdrant 컬렉션 별칭(alias)으로 같은 동작.
7. **검증** (`verify`) — 빈 청크 0 · 원문 커버리지 100% · 임베딩 차원 일치 · 파싱 경고 수 · **골드셋 중 이 문서군 질의의 hit@3가 직전 활성 세대보다 낮으면 활성화 거부**(`--force`로만).
8. **보고서** — `reports/doc_rag/<문서군>/<세대>.json`: 파일별 상태 · 페이지 수 · 텍스트 없는 페이지 · 표 수 · 섹션 트리 요약 · 청크 수·길이 분포 · 경고. **본문은 싣지 않는다.**

### 4.4 검색 (서버 요청 경로)

1. **질의 확정** — 받은 그대로(공백·전각만 정규화 · §2.3 ⑧)
2. **밀집 벡터**(질의 임베딩) + **어휘 희소 벡터**
3. **후보** — Qdrant `query_points`: 밀집 prefetch(C) + 희소 prefetch(C) → **RRF** → 후보 C(기본 15 — 플랫폼 「rerank candidates 15」와 같게)
4. **재순위** — 교차 인코더(질의, 청크) → sigmoid 0~1
5. **하한** — 기본 0.3 미만 제거(§4.6) → **상위 k**(기본 3)
6. **응답 조립**(§4.2)

- **서버 예산**: 요청당 벽시계 상한 `DOC_RAG_REQUEST_BUDGET_SEC`(기본 8초 — 본체 read 상한 12초보다 작게). 넘으면 503 `Timeout` 봉투로 **끝낸다**(D-198 교훈 — 무한대기 금지).
- **동시성**: 모델 추론은 스레드 풀(`asyncio.to_thread`) · 동시 상한(기본 2) · 대기열 초과는 503 `Busy`.

**어휘 경로 선택지 (G-5)**

| 안 | 방식 | 장점 | 단점 |
|---|---|---|---|
| **(a) 권고 — 시작점** | 문자 2~3그램 + 공백 토큰 → BM25식 가중 → 희소 벡터 | 의존성 0 · 결정적 · 조항 번호(`제12조`)·약어·영문 코드에 강함 · 형태소 사전 불필요 | 벡터 차원(어휘) 큼 · 조사 붙은 어형은 n-그램이 흡수 |
| (b) | BGE-M3 학습형 희소(lexical weights) | 플랫폼과 같은 모델 계열 | FlagEmbedding 의존 · 모델 호출 1회 추가 |
| (c) | 형태소 분석(kiwipiepy) + BM25 | 한국어 어휘 정확도 | LGPL(W0 확인) · 사전 반입 |

IDF: Qdrant 희소 벡터 `Modifier.IDF`가 내장 모드에서 되면 그것을 쓰고, 안 되면 **세대별 문서 빈도 표를 색인 때 저장**해 질의 가중에 쓴다(W0 실측으로 결정). (a)로 시작해 W6에서 (b)와 비교한다.

### 4.5 모델 (G-6)

| 역할 | 권고 | 라이선스 | 비고 |
|---|---|---|---|
| 밀집 임베딩 | **BAAI/bge-m3** (1024차원 · 다국어 · 최대 8,192토큰) | MIT(W0 모델 카드 확인) | 플랫폼과 같은 계열 → **비교가 공정** |
| 재순위 | **BAAI/bge-reranker-v2-m3** (교차 인코더) | Apache-2.0(W0 확인) | `sentence_transformers.CrossEncoder` 존재 실측 |
| 비교 기준선 | `multilingual-e5-small`(루트에 이미 있음 · 384차원) | MIT | W6 비교용 — 운영 권고 아님 |

- **실행 위치**: (a) doc_rag 프로세스 CPU 상주 (b) 폐쇄망 vLLM GPU — `/v1/embeddings`(밀집) · score/rerank API(재순위) — 본체 `synonym_semantic` `vllm` 백엔드와 같은 방식. **어휘 경로는 (a)·(b) 무관하게 인프로세스**(모델 불필요).
- **자원(추정 — W0 실측)**: fp32 기준 두 모델 합계 RAM 약 4~5GB · CPU 재순위 15쌍 수백 ms~수 초 · 질의 임베딩 수십~수백 ms.
- **반입**: 모델 디렉토리만(`DOC_RAG_EMBED_MODEL_PATH` · `DOC_RAG_RERANK_MODEL_PATH`) · 로드 전 `HF_HUB_OFFLINE=1`·`TRANSFORMERS_OFFLINE=1` · 디렉토리가 아니면 **기동 실패**(§2.3 ⑤).

### 4.6 점수 계약 — 본체 프롬프트가 참이 되도록

1. `rank_score` = **재순위 점수(sigmoid 0~1)**. RRF·코사인 원점수는 내지 않는다.
2. **하한 이상만 반환** — `DOC_RAG_SCORE_FLOOR` 기본 **0.3**. `src/prompts/doc_answer.py:42`의 「0.3 이상만 도달 · 최대 1.0」이 로컬에서도 참이 된다.
3. **재순위는 필수 구성요소**다(끄는 설정을 두지 않는다). CPU로 느리면 vLLM 백엔드나 더 작은 재순위 모델로 푼다(G-6).
4. **혼합 불가(설정 구조 — v1.1)** — 한 턴에 FabriX 문서군과 로컬 문서군이 함께 불리면 `evidence.py`의 전역 정렬 전제(같은 리랭커)가 깨진다. v1.0은 운영 규칙으로 막았고, v1.1은 **백엔드 스위치가 전역 하나이고 시험 표면의 선택도 요청 전체에 적용**되므로 섞일 경로가 없다(§4.2a).
5. `/health`에 점수 계약(`floor` · 재순위 모델 id)을 싣는다.

### 4.7 설정 — `DOC_RAG_*` (패키지 자체 `.env` · `pydantic-settings`)

| 키 | 기본 | 뜻 |
|---|---|---|
| `DOC_RAG_HOST` · `DOC_RAG_PORT` | `127.0.0.1` · **9094** | 9095는 `plans/128`이 제안 · 9096 apm · 9097·9098 sre · 9099 mcp · 9100 alarm(점유 실측 — 착수 시 재확인) |
| `DOC_RAG_API_TOKEN` | (필수 · `SecretStr`) | 비면 기동 실패 · 상수시간 비교 |
| `DOC_RAG_CORPUS_DIR` · `DOC_RAG_INDEX_DIR` · `DOC_RAG_REPORT_DIR` | — | 원본 · 색인 세대 · 보고서 |
| `DOC_RAG_STORE_MODE` · `DOC_RAG_QDRANT_URL` | `local` · — | 내장 / 서버(G-7) |
| `DOC_RAG_EMBED_BACKEND` · `_MODEL_PATH` · `_VLLM_BASE_URL` · `_VLLM_MODEL` | `local` | 밀집 임베딩 |
| `DOC_RAG_RERANK_BACKEND` · `_MODEL_PATH` · `_VLLM_*` | `local` | 재순위 |
| `DOC_RAG_TOP_K` · `DOC_RAG_CANDIDATES` · `DOC_RAG_SCORE_FLOOR` | 3 · 15 · 0.3 | 현 FabriX 설정과 같게 시작 |
| `DOC_RAG_REQUEST_BUDGET_SEC` · `DOC_RAG_MAX_CONCURRENCY` · `DOC_RAG_DEVICE` | 8 · 2 · `cpu` | |

- **검색 파라미터 소유**: 126 D-284 ②는 「플랫폼 전속 → 본체는 재필터 0」이다. 로컬 백엔드에서는 **소유자가 doc_rag 서버 설정**으로 바뀔 뿐 **본체 재필터 0 원칙은 그대로**다 — 필터는 서버 한 곳에만 있다.
- **정본 일원화(D-131)**: 문서군의 **의미**(제목·설명·표면어·민감 여부)는 루트 `config/rag_collections.yaml` 하나다. `doc_rag/config/corpus.yaml`은 **색인 방법**(원본 위치·형식·제목 패턴·청킹 수치)만 갖고 id로 연결한다. 본체 쪽 테스트가 두 YAML의 id 집합을 대조한다(import 없이 파일만 읽는다).

### 4.8 보안

- **127.0.0.1 바인딩 기본**(본체와 같은 호스트 — G-8). 다른 호스트면 방화벽 + 토큰.
- **쓰기 HTTP 엔드포인트 0** — 색인·활성화·삭제는 CLI만(서버 호스트 셸 권한 = 관리 권한). 서버는 읽기 전용 경로만 연다.
- 토큰 상수시간 비교 · 로그 마스킹(끝 4자) · **로그·보고서에 문서 본문 0**(제목·id·길이·점수만 — 126 클라이언트와 같은 규율).
- **색인 디렉토리 = 문서 사본**이다 — 권한 0750 · git 제외 · 백업 정책은 원본과 같은 등급(G-8에서 함께 확인).
- 문서 단위 권한 없음(`has_permission` 항상 true) → 민감 문서군은 루트 정본 `sensitive: true` + 126 `authz`(관리자·허용 사용자만)로 막는다.
- 외부 송신 0 — 모델·색인·검색이 모두 사내 서버 안이다. 문서가 외부 플랫폼에 저장되지 않는다는 점이 FabriX 리트리벌 대비 이점이다. 서술 단계의 LLM 송신과 PII 필터 대응은 본체 흐름 그대로다.

### 4.9 관측

- 요청당 로그 1줄: 문서군 · 세대 · 후보 수 · 반환 수 · 점수 범위 · 단계별 ms(`embed`/`search`/`rerank`) · 호출자 표지 · 토큰 끝 4자.
- `GET /health`: 모델 로드 상태 · 문서군별 활성 세대·청크 수·색인 시각 · 점수 계약 · 실패 사유(fail-fast 사유 포함).
- `GET /collections`: 문서군 목록 · 세대 이력(본체 관리자 화면 연계는 후속).
- 본체 쪽은 변경 없음 — 감사 `log_doc_retrieval`·진단(`diagnostics`)·`rag_probe --raw`가 추가 필드(`engine`·`index_generation`·`timings_ms`)를 원시 응답으로 보여 준다.

### 4.10 왜 `src/`·`mcp_server/` 안이 아닌가

| 후보 | 기각 이유 |
|---|---|
| `src/doc_qa/` 안에 인프로세스 | 모델 수 GB가 본체 프로세스에 상주 · 색인 배치가 응답 경로와 경쟁 · 사용자 지시(「별도의 패키지」) |
| `mcp_server/` | 관측 데이터 읽기 경계(D-119) 밖 — 126 D-284 대안에서 이미 기각 |
| `sre_agent/` | 자체 venv(≥3.13 · holmes 스택) · 장애 조사 축과 무관 |

「기존 소유 패키지 편입을 먼저 검토한다」는 원칙(`plans/101` v2 배치 재검토 — 사용자 지시)을 따랐다 — 문서 검색 축을 소유한 기존 패키지는 `src/doc_qa`(답변 엔진)뿐이고, 그 안에 넣으면 위 첫 행의 문제가 생긴다.

## 5. 구현 트랙

| W | 산출물 | 완료 기준 (verify) |
|---|---|---|
| **W0** 사전 확인(코드 0) | ①원본 샘플(문서군별 1~2건 · 형식 목록 — G-3) ②서버 사양(CPU·RAM·GPU 유무 — G-6) ③모델 2종 반입·라이선스 확인 ④Qdrant 내장 모드 `Modifier.IDF` 실측 ⑤CPU 지연·메모리 측정(scratch — 합성 문장 100건) ⑥포트 재확인 | 게이트 G-1~G-8 답 · 측정표 1장 |
| **W1** 골격 | `doc_rag/pyproject.toml` · `config.py` · `domain/` · `__main__` · `tests/test_boundary.py`(import 0 · 계층 · LLM import 0) · `tests/test_config.py` | `cd doc_rag && ../.venv/bin/python -m pytest` 통과 · 루트 `pytest` 수집 영향 0 |
| **W2** 파싱·구조화·청킹 | `ingest/`(pdf·docx·text) · `structure/` · `chunking/` · 합성 픽스처(`testdata/` — python-docx로 생성 · 작은 PDF 커밋) | **원문 커버리지 100%** · 짧은 조항 병합 · 표 미분할(상한 초과 시 머리 행 반복) · 페이지 범위 보존 · 결정적 청크 id(2회 실행 동일) · 스캔 페이지 보고 |
| **W3** 임베딩·저장소·색인 CLI | `embedding/`(local·vllm · 어휘 가중) · `store/`(Qdrant 내장 · 세대 · `active.json`) · 임베딩 캐시 · `cli/index` 6명령 · 보고서 | 가짜 임베더(해시 기반)로 build → verify → activate → rollback 왕복 · 재빌드 멱등(점 수 불변) · 세대 디렉토리 분리 |
| **W4** 검색·서버 ★ | `search/` · `rerank/` · `server/`(FastAPI) · 점수 계약 · 오류 봉투 4종 · 서버 예산·동시성 · 계약 골든 응답 `testdata/contract/*.json` | 계약 테스트(스키마·봉투·인증·예산) · **로컬 실모델 옵트인 테스트**(`DOC_RAG_EMBED_MODEL_PATH` 있을 때만) · 본체 `.env`를 로컬로 돌려 `python scripts/rag_probe.py -c hq_manual -q … --search-only` 성공(LLM 0) |
| **W5** 본체 선택 스위치 + 연결 검증 (v1.1) | ①**스위치**(§4.2a): `RagConfig` 3키 · `resolve_collections(backend=)` 분기 · `DocCollection.backend` · 엔진 `backend` 전달 · 로컬 캐시 키 접두 · 감사·진단 `backend` ②**시험 표면 선택**: `rag_probe.py --backend`·`--list` 두 백엔드 표시 · `POST /api/v1/doc/search` `backend` 필드 · 관리자 탭 선택 상자·상태 표 두 열(`admin-rag-docs.js` 캐시 버전 올림) ③**설정 카탈로그**: `rag` 그룹 3키 · `RELOADABLE_KEYS` · `SENSITIVE_VALUE_KEYS` · 도움말 3항목 · `.env.example` 3줄 · 카탈로그 카운트 단언 갱신 ④**연결 검증**: 골든 응답을 `interpret_body`로 해석 · 두 YAML id 대조 ⑤**문서**: 런북 `docs/33_doc_rag_runbook.md`(기동 · 색인 갱신 · 전환·되돌리기 = `RAG_BACKEND` · 오류 판독 · 캐시 TTL 주의) · `docs/32` §6 판독표에 로컬 `stale_id` 의미 1줄 · **관리자 매뉴얼 A-62·A-63 갱신 + 캡처**(화면이 바뀐다 — D-255) | **기본 `fabrix`에서 해석 결과·캐시 키가 현행과 같음**(기존 `tests/test_doc_qa` 전건 무수정 통과 — 기준선 대비 신규 실패 0 · `git worktree` 대조) · 스위치 테스트(§6.2) · `local`로 바꾸고 「설정 리로드」만으로 로컬 응답(재기동 없음) · 되돌리기 동일 · 로컬 서버를 끈 채 `local`이면 **FabriX로 넘어가지 않고** 사유 노출 · `tests/test_manual` 통과 · `arch_check`·`overfit_check` 신규 0 |
| **W6** 품질 측정 | `testdata/rag_gold/queries.yaml`(126 W7 흡수 — 질의 → 기대 문서·섹션) · `scripts/rag_eval.py`(**본체 클라이언트 `retrieve_one`으로 두 백엔드 동일 측정** — hit@1/@3 · MRR · 0건률 · p50/p95) · 청크 크기·어휘 방식(a/b) 비교표 | 골드 분모 고정 규칙(126 v1.1) · 로컬 측정 완료 · **FabriX 측정은 G-9(과금) 확정 뒤** · 결과를 D-288 근거에 기록 |
| **W7** 서술 end-to-end | 로컬 MLX로 엔진 end-to-end 1건 이상(`RUN_LOCAL_LLM=1` — 두 평면 `mlx` 확인 후 · D-240) — 126의 「W2 로컬 MLX 서술 1건 미실행」 잔여도 여기서 닫힌다 | 근거 있음·0건·서버 중단(503) 세 경우 응답 문구 확인 |
| **W8** 문서 | 관리자 매뉴얼 절(§8) · D-288 본문 등재 · `plans/INDEX.md` 상태 갱신 · 폐쇄망 반입 목록(부록 C) | `python -m scripts.manual.build` → `pytest tests/test_manual` 통과 |

## 6. 테스트 계획

### 6.1 패키지 단위 (모델 0 · 네트워크 0 · 기본 실행)
- **파싱**: 합성 PDF(제목 글꼴·표·빈 페이지) · DOCX(Heading 스타일·표) → 요소 종류·페이지 번호 · 스캔 페이지 보고
- **구조화**: 번호 체계 판정(장·절·조·항·호·목·부칙·별표) · 섹션 트리 · 패턴 끄기
- **청킹 불변식**: 원문 커버리지 100% · 청크 상한 준수(표 예외 규칙) · 짧은 조각 0건 폐기 · 결정적 id · 섹션 경로 머리말
- **저장소**: 가짜 임베더 + 임시 디렉토리 Qdrant 내장 모드 — 세대 빌드·활성화·되돌리기 · 활성 포인터 원자 교체 · 멱등
- **검색**: 어휘 경로가 벡터 상위 밖의 조항 번호를 회수(§2.3 ② 회귀 방지) · RRF · 가짜 재순위로 하한·상위 k
- **서버 계약**: 요청·응답 스키마 · 오류 봉투 4종 · 인증 · 예산 초과 503 · 동시성 503 · **응답·로그에 본문 외 누설 0**(로그에 본문 0)
- **경계**: import 0(양방향) · 계층 · LLM 클라이언트 import 0

### 6.2 본체 (W5)
- 골든 응답 → `interpret_body` → 필드 전수 매핑 · 오류 봉투 → `stale_id`/`error`
- 두 YAML의 문서군 id 대조
- **선택 스위치**(v1.1): ①키 미입력 = `fabrix` · 해석 결과가 현행과 필드 단위 동일 ②`local` 해석 — 공용 접속 · `client_key` 상수 · `retrieval_id` = 문서군 id · 로컬 2키 미입력 사유 누적 ③`backend` 인자가 설정값을 이긴다(요청 단위) ④허용값 밖(`RAG_BACKEND=foo`) = 설정 검증 오류 · API 422 ⑤캐시 키 — `fabrix` 형식 불변 · `local` 접두 ⑥감사 `backend`·`backend_source` ⑦**로컬 실패가 FabriX 호출로 이어지지 않음**(호출 대상 URL 단언) ⑧`CONNECTION_FIELD_MAP` 밖 문서군이 `local`에서 켜지고 `fabrix`에서는 사유와 함께 꺼짐 ⑨운영자 API `backend` 필드는 관리자만(126 인가 그대로)
- 127 라우팅 미선점·무변경 가드(`tests/test_doc_qa/test_api_and_prefix.py` `TestRouteWiring`) 그대로 통과 — 채팅 경로에 백엔드 선택 표면 0

### 6.3 실모델 (옵트인)
- `DOC_RAG_EMBED_MODEL_PATH`·`DOC_RAG_RERANK_MODEL_PATH`가 디렉토리일 때만(`noise_gate` 전례) — 로드 · 차원 · 한국어 문장 유사도 순서 sanity · 재순위 점수 0~1

### 6.4 측정 (W6 — §5)

### 6.5 실호출 정책
- 로컬 서버는 루프백이라 **과금 0 · 승인 불필요**. 본체 테스트의 네트워크 가드(공인 IP 차단)와도 충돌하지 않는다.
- **FabriX 리트리벌 비교 측정은 G-9 확정 전 금지**(126 G-7 · D-127 — 과금이면 건별 승인).

## 7. 품질 게이트 준수 메모

- `arch_check`: 2단 중첩 미해석 → 패키지 안 경계 테스트로 대체(`apm_gateway` G-11 전례). 본체 변경(W5)은 기존 계층 안(config · infrastructure `doc_sources` · application `doc_qa` · interface `api`)에서만 일어난다 — 새 계층 의존 0.
- `overfit_check`: `doc_rag/doc_rag`를 스캔 대상에 편입(`apm_gateway/apm_gateway` 전례 · 기준선 전면 재생성 금지 — 자기 델타만). 문서군 특화 리터럴은 `corpus.yaml`로.
- **D-162**: `RAG_BACKEND` 기본 `fabrix` — 키를 넣지 않은 배포는 접속 해석·캐시 키·응답이 **현행과 같다**(감사 레코드에 `backend` 필드가 더해지는 것만 다르다).
- `ruff`·`mypy`: 루트 venv에 없으므로 `uvx --offline`(기준선 대조).
- **루트 `pytest` 수집**: `testpaths = ["tests", "noise_gate/tests"]`(`pyproject.toml:130`) — `doc_rag/tests`는 루트 수집 대상이 아니다(패키지 디렉토리에서 따로 실행 · `mcp_server` 방식).

## 8. 매뉴얼 동반 (D-255)

- **관리자 매뉴얼**
  - (W5 — 화면이 바뀌는 작업과 같은 작업에서) 기존 **A-62·A-63(문서 검색 시험) 갱신** — 백엔드 선택 상자 · 상태 표 두 열 · 「원시 응답의 `engine`·`index_generation`」 판독 · `captures.yaml` 갱신 → `run_capture --only <캡처ID>`(126 W6 잔여 캡처 1~2장을 여기서 함께 닫는다). 역방향 가드는 `<button id>`·탭 값만 잡으므로 **선택 상자는 스스로 챙긴다**.
  - (W5) **「문서 검색 백엔드 전환(FabriX ↔ 자체 검색)」** 절 — `RAG_BACKEND` 설정·리로드 절차 · 되돌리기 · 자동 폴백 없음 · 캐시 TTL 주의.
  - (W8) **「문서 색인 갱신」** 절 — 화면 버튼이 없는 CLI 절차라 `features.yaml`에 `no_ui`로 등재 · 5칸 작성.
  - 설정 도움말(`config/settings_help/rag.yaml`) 3항목은 W5에서 5칸 + 사례로.
- **사용자 매뉴얼**: 변화 없음(답변 형식·인용 형식이 같다). 문서 기준 시점 안내(126 서술 지시 5)는 그대로다.

## 9. 위험과 완화

| # | 위험 | 완화 |
|---|---|---|
| R-1 | **원본 형식** — 스캔 PDF면 텍스트가 없다(HWP는 제외 확정) | G-3 · 보고서가 텍스트 없는 페이지를 드러낸다 · PPTX는 PDF로 받는다 · OCR은 후속 |
| R-2 | **청킹 품질** — 규정 구조(장·조) 인식 실패 시 근거가 조각난다 | 문서군별 패턴 설정 · 보고서의 섹션 트리 요약 · 골드셋 hit@3로 활성화 게이트 |
| R-3 | **점수 척도 불일치** — 프롬프트 전제가 거짓이 된다 | 재순위 필수 · 하한 0.3 · **전역 스위치로 혼합 불가**(§4.2a) · `/health`에 계약 표시 |
| R-4 | CPU 지연·메모리 | W0 실측 · vLLM 백엔드 · 동시성 상한 · 서버 예산 8초 → 503 |
| R-5 | 색인이 문서 사본 | 권한 0750 · git 제외 · 로그·보고서 본문 0 |
| R-6 | 색인 낡음(원본은 바뀌었는데 재색인 안 함) | `/health`·원시 응답에 활성 세대 시각 · 갱신 주기 운영 합의(G-8) · 런북 |
| R-7 | 라이선스 | 기본 파서 pdfplumber(MIT) · PyMuPDF는 G-4 · 모델 라이선스 W0 확인 |
| R-8 | 내장 모드 파일 잠금(한 경로 한 프로세스) | 세대 디렉토리 분리 · 서버는 활성 세대만 연다 · 규모·동시성 요구가 생기면 서버 모드(같은 코드) |
| R-9 | FabriX 대비 품질 열세(HyDE 없음) | W6 측정 후 전환 · FabriX 접속값 상주(되돌리기 = `RAG_BACKEND=fabrix` + 설정 리로드) · HyDE는 본체 질의 확장 후속 |
| R-10 | **두 번째 답변 엔진이 생긴다** | G-1 (a) · 패키지 LLM import 0을 경계 테스트로 고정 |
| R-11 | 계약 드리프트(본체 파서가 바뀌면 서버가 모른다) | 골든 응답을 **패키지·본체 양쪽 테스트**가 읽는다 |
| R-12 | 운영 프로세스가 하나 늘어난다 | 수동 기동 관례(`python -m <패키지>`) · 런북에 기동·상태 확인 · `/health` |
| **R-13**(v1.1) | **스위치를 `local`로 둔 채 검색 서버가 꺼져 문서 질의가 전부 실패** | 자동 폴백을 두지 않는 대신 **사유가 즉시 드러난다**(「검색 서비스에 연결하지 못했습니다」) · `rag_probe --list`·관리자 탭이 두 백엔드 상태를 함께 보인다 · 런북 첫 조치 = 서버 기동 또는 `RAG_BACKEND=fabrix` |
| **R-14**(v1.1) | 재색인 직후 캐시가 이전 세대 결과를 돌려준다 | 로컬 캐시 키 접두 분리 · TTL(기본 300초) 명시 · `index activate` 출력·런북 안내 |

## 10. 게이트 — **v1.1 전건 확정**

> 용어: **「본체」** = collectorinfra 서버(`python -m src.main --server`). **「검색 서버」** = 이번에 만들 `doc_rag` 프로세스. **「문서군」** = 본부 전산관리매뉴얼 · 아키텍처 설계문서처럼 따로 검색하는 문서 묶음(126의 컬렉션).
>
> **확정 근거(2026-09-30)**: 사용자 *"HWP는 사용하지 않는다 고려하지 않아도 되고 나머지는 권고에 맞게 진행하라."* — G-3은 사용자 지정, 나머지는 권고안 채택. G-12는 같은 메시지의 질문(*"선택적으로 사용하도록 계획이 되어 있냐?"*)에 답하며 신설했고 권고안으로 반영했다(바꾸려면 이 표에서 되돌린다).

| # | 질문 | 확정 | 남은 정보 |
|---|---|---|---|
| **G-1** | 패키지에 답변 생성(LLM)까지 넣나 | **(a) 검색만** — 답변·인용·인가·감사·채팅 연결은 본체 `src/doc_qa` · 패키지 LLM import 0을 경계 테스트로 고정 | — |
| **G-2** | 본체와 어떤 약속(계약)으로 붙나 | **(a) FabriX 리트리벌과 같은 요청·응답** — 클라이언트·파서·엔진 한 벌 | — |
| **G-3** | 원본 문서 형식 | **PDF · DOCX · TXT/MD** · **HWP·HWPX 제외(사용자)** · PPTX는 원문 부서가 PDF로 내보낸다 · 스캔 페이지는 보고서에 드러내고 OCR은 후속 | W0 원본 샘플 1~2건(청킹 규칙 실측용) |
| **G-4** | PDF 파서 라이선스 | **(a) pdfplumber만(MIT)** · PyMuPDF(AGPL) 미사용 | — |
| **G-5** | 키워드(어휘) 검색 방식 | **(a) 문자 n-그램으로 시작 · W6에서 (b) BGE-M3 희소와 비교** | — |
| **G-6** | 모델을 어디서 돌리나 | **두 실행 위치를 모두 구현하고 설정으로 고른다**(`DOC_RAG_EMBED_BACKEND`·`DOC_RAG_RERANK_BACKEND` = `local` \| `vllm` · 기본 `local` CPU) — 권고 「서버 사양을 보고 결정」에 따라 **어느 값으로 운영할지만** 사양 수령 뒤 W0 측정으로 정한다 | **서버 CPU·RAM·GPU 유무** · vLLM 서버에 모델 추가 가능 여부 |
| **G-7** | 벡터 DB 운용 형태 | **(a) Qdrant 내장** — 규모·동시 접근 요구가 생기면 서버 모드(코드 동일 · `DOC_RAG_STORE_MODE`) | — |
| **G-8** | 배치 호스트 · 운영 | **(a) 본체와 같은 호스트**(127.0.0.1:9094) · 재색인은 원본이 바뀔 때 수동(CLI) | 원본 보관 경로 · 문서 갱신 빈도(런북 기재용) |
| **G-9** | FabriX 비교 측정의 과금 | **과금 여부 확인 뒤 1회 측정** — 확인 전에는 FabriX 쪽 측정을 하지 않는다(D-127) · 로컬 측정은 진행 | **FabriX 리트리벌 호출 과금 여부**(126 G-7과 같은 질문) |
| **G-10** | 전환 기준 | **(b) 측정표를 보고 사용자 판단** | — |
| **G-11** | FabriX 연동을 남기나 | **(a) 남김** — 선택 스위치의 한쪽 값으로 상주(§4.2a) | — |
| **G-12**(v1.1) | 두 RAG를 **골라 쓰는** 방법 | **전역 스위치 `RAG_BACKEND`(기본 `fabrix`) + 로컬 공용 접속 2키 + 시험 표면 요청별 선택 · 자동 폴백 없음**(§4.2a). 기각: 문서군별 스위치(한 턴 척도 혼합) · 접속값 교체(v1.0 — 한쪽 값 소실) · 로컬 실패 시 FabriX 자동 전환(침묵 폴백) · 채팅 사용자 선택(사용자가 판단할 근거가 없다) | — |

<details><summary>v1.0 게이트 원 질문(기록)</summary>

| # | 질문 | 선택지와 결과 | 권고 |
|---|---|---|---|
| G-1 | 패키지에 답변 생성(LLM)까지 넣나 | (a) 검색만 / (b) 답변까지 — 본체 엔진과 두 벌 · 127 채팅 연결 교체 | (a) |
| G-2 | 본체와 어떤 계약으로 붙나 | (a) FabriX와 같은 요청·응답 / (b) 우리만의 API | (a) |
| G-3 | 원본 문서 형식 | PDF · DOCX · HWP · HWPX · PPTX · 스캔 | PDF/DOCX로 받기 |
| G-4 | PDF 파서 라이선스 | (a) pdfplumber만 / (b) PyMuPDF 허용 | (a) |
| G-5 | 어휘 검색 방식 | (a) 문자 n-그램 / (b) BGE-M3 희소 / (c) 형태소 | (a)로 시작 · (b) 비교 |
| G-6 | 모델 실행 위치 | (a) CPU / (b) vLLM GPU | 사양을 보고 결정 |
| G-7 | 벡터 DB 운용 | (a) Qdrant 내장 / (b) Qdrant 서버 / (c) Chroma | (a) |
| G-8 | 배치 호스트 | (a) 같은 호스트 / (b) 별도 | (a) |
| G-9 | FabriX 측정 과금 | 과금이면 건별 승인 | 확인 후 1회 |
| G-10 | 전환 기준 | (a) hit@3 ≥ FabriX / (b) 사용자 판단 | (b) |
| G-11 | FabriX 연동 유지 | (a) 남김 / (b) 폐기 | (a) |

</details>

## 11. 후속 (등재만)

1. **HyDE·질의 확장** — W6에서 FabriX 대비 회수가 부족하면 본체 쪽(엔진 1단계)에서 LLM 질의 확장. 검색 서버에는 LLM을 넣지 않는다.
2. **OCR** — 스캔 PDF가 확인되면(G-3).
3. **문서 단위 권한** — 원본에 권한 메타가 생기면 `has_permission` 계산.
4. **관리자 화면 색인 상태** — `/collections`를 「문서 검색 시험」 탭에 표시(본체 화면 변경 — D-255 동반).
5. **새 문서군 추가 간소화** — **로컬 백엔드는 v1.1에서 해소**(공용 접속 — §4.2a 부수 규칙 6). FabriX는 종전대로 문서군마다 본체 정적 4키가 필요하다(126 §4.12 · `docs/32` §5).
6. **`plans/128` 관계** — LLM 게이트웨이 G-5(FabriX 리트리벌 편입)가 채택되면 검색 서버도 같은 게이트웨이 뒤에 둘지 검토. 의존은 없다.
7. **`sre_agent` 근거 공급**(126 §11 7) — 검색 서버 HTTP 계약을 MCP로 감싸 조사 중 설계문서 인용.

## 부록 A. NWAgent1 `rag_parser` 파일별 처분 전수

| 파일 | 줄 | 처분 | 옮길 요소 / 버리는 이유 |
|---|---:|---|---|
| `parser/layout_parser.py` | 525 | 이식·수정 | `ElementType`·`DocumentElement` → `domain/` · `_classify_element`·`_get_dominant_font_size`·`_is_bold_text`·`_detect_header_level`·`_sort_elements_by_position`·`_extract_table_regions`·`_table_data_to_markdown`·`_is_in_table_region` → `ingest/pdf.py`(pdfplumber 기반) · CLI·경고 키워드(시스코) 제거 · `unstructured` 경로 제거 |
| `parser/markdown_converter.py` | 232 | 이식·수정 | `ChapterSplitter.split_by_chapters`(수준 분할·페이지 범위) → `structure/` · 파일 쓰기(`split_to_files`) 제거 · `MarkdownConverter`는 표·목록 변환부만 |
| `preprocessing/pipeline.py` | 155 | 흐름 차용 | 파싱 → 변환 → 분할 순서 · poppler PATH·`sys.path` 조작 제거 |
| `splitter/cisco_splitter.py` | 291 | 구조 차용 | 헤더 분할 → 재귀 분할 틀 · 길이 함수(문자 기준으로 교체) · 블록 보존 일반화 · `_postprocess_chunks` 폐기 규칙 → 병합 · `_extract_keywords`(시스코) 제거 · `SemanticChunkEnhancer`의 「(계속)」 표기 대신 섹션 경로 머리말 |
| `splitter/test_splitter.py` | 110 | 참고 | 테스트는 새로 쓴다 |
| `embedding/embedding_service.py` | 338 | 축소 이식 | `_resolve_model_path`(로컬 디렉토리 분기만) · E5 접두 · 정규화 · 배치 인코딩 · `HybridEmbeddingService`·`_preprocess_cli`·네트워크 탐지 제거 |
| `embedding/offline_utils.py` | 169 | 버림 | 소켓 연결로 오프라인 판정(§2.3 ⑤) |
| `embedding/dual_embedding_service.py` · `model_selection.py` | 812 | 버림 | 영문 문서 + 한국어 질의 전제 |
| `embedding/benchmark.py` | 160 | 참고 | W6 측정 스크립트 설계 참고(시스코 질의 세트는 쓰지 않는다) |
| `vectorstore/chroma_store.py` | 261 | 인터페이스 차용 | `add_documents`·`search`·`_build_where_filter`·`get_collection_stats` 모양 · 구현은 Qdrant · 결정적 id · 세대 |
| `vectorstore/indexing_pipeline.py` | 251 | 흐름 차용 | `index_directory` 통계 JSON · 파일별 결과 · 세대 빌드·검증·활성화로 확장 |
| `vectorstore/json_indexer.py` | 106 | 해당 없음 | 구조화 JSON |
| `rag/rag_chain.py` | 631 | 버림 | 답변 엔진은 `src/doc_qa` · LLM 재순위 → 교차 인코더 · 점수 재필터·문자열 신뢰도(§2.3 ⑦) |
| `rag/advanced_rag.py` | 816 | 버림 | 어휘 회수 없는 하이브리드(§2.3 ②) · 질의 증강(⑧) · 메모리 대화 이력 · 모드별 프롬프트 |
| `rag/rag_factory.py` | 330 | 버림 | 키워드 모드 자동 선택(D-004 성격 충돌) |
| `rag/multilingual_rag.py` · `translation/translator.py` | 782 | 버림 | 외부 번역 API · 영문 코퍼스 전제 |
| `rag/structured_rag.py` | 109 | 해당 없음 | 장비·서브넷 JSON 조회 |
| `llm/llama_service.py` | 329 | 버림 | 패키지에 LLM 없음 |
| `main.py` | 133 | 참고 | CLI 형태만 |

## 부록 B. 응답 예시 (FabriX 호환 · 합성 값)

```json
{
  "results": [
    {
      "id": "3f0c9a0e-5b1e-5c2a-9d0e-6a1f2b3c4d5e",
      "doc_id": "a1b2c3d4e5f60718",
      "catalog_id": "hq_manual",
      "title": "본부 전산관리매뉴얼",
      "filename": "전산관리매뉴얼_2026.pdf",
      "content": "[제3장 계정관리 > 제12조(비밀번호)]\n① 비밀번호는 90일마다 변경한다. …",
      "content_info": {"subtitle": "제3장 계정관리 > 제12조(비밀번호) · p.14–15"},
      "rank": 1,
      "rank_score": 0.87,
      "has_permission": true,
      "url": "전산관리매뉴얼_2026.pdf#page=14",
      "content_type": "text",
      "executed_query": "비밀번호 변경 주기는?"
    }
  ],
  "engine": "doc_rag",
  "index_generation": "20261002103000_hq_manual",
  "timings_ms": {"embed": 85, "search": 12, "rerank": 640},
  "candidates": 15
}
```

## 부록 C. 폐쇄망 반입 목록 (랜딩 시 갱신)

| 구분 | 항목 | 비고 |
|---|---|---|
| wheel | `qdrant-client` + 전이(`grpcio` · `protobuf` · `portalocker` 등) | 루트 고정과 충돌 0(§3.5) — W1에서 `uv pip compile` 산출 목록으로 확정 |
| wheel | `pdfplumber` + `pdfminer.six` · `pypdfium2` | MIT · BSD/Apache |
| wheel(조건부) | `pymupdf`(G-4 허용 시) · `FlagEmbedding`(G-5 (b) 채택 시) · `kiwipiepy`(G-5 (c)) | |
| 모델 디렉토리 | `bge-m3` · `bge-reranker-v2-m3` | git 밖 · `DOC_RAG_*_MODEL_PATH` |
| 코드 | `doc_rag/` 전체 · 본체 테스트 2파일 · `docs/33` · 매뉴얼 원천 | 파일 단위 수동 복사(126 부록 C 관례) |
| 설정 | `doc_rag/.env`(토큰·경로·모델 경로) · 본체 `.env` 3키(`RAG_BACKEND` · `RAG_LOCAL_ENDPOINT` · `RAG_LOCAL_TOKEN`) — FabriX 4키는 그대로 | 토큰은 채팅·문서에 적지 않는다 |

## 변경 이력

| 날짜 | 판 | 내용 |
|---|---|---|
| 2026-09-30 | v1.0 | 최초 작성 — NWAgent1 `rag_parser` 6,576줄 실측(처분 표 · 결함 8건) · 본체 계약 실측(`fabrix_retrieval.py` · 점수 척도 전제 2곳) · 라이브러리 실측(의존성 해석 · Qdrant 내장 모드 하이브리드 RRF · 라이선스 · pdfplumber 글꼴 정보) · 게이트 G-1~G-11 · D-288 예약 |
| 2026-09-30 | v1.1 | 게이트 G-1~G-12 확정(사용자 *"HWP는 사용하지 않는다 … 나머지는 권고에 맞게 진행하라"*) · HWP 제외 · **백엔드 선택 스위치 신설(§4.2a · G-12)** — 사용자 질문 *"선택적으로 사용하도록 계획이 되어 있냐?"*의 답이 「아니오(v1.0은 접속값 교체)」여서 두 접속값 병존 + `RAG_BACKEND` 전역 스위치 + 시험 표면 요청별 선택 + 자동 폴백 없음으로 고쳤다 · 본체 변경 「코드 0」 → 「선택 스위치 한 벌」(W5 확장) · 혼합 운용 금지를 운영 규칙에서 설정 구조로 · R-13·R-14 신설 · 매뉴얼 A-62·A-63 갱신을 W5로 · D-288 본문 등재 |
