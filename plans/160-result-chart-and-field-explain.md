# 160. 조회 결과 그래프 보기 · 칼럼 용어 사전 · 값 해석 링크 · 표 넓게 보기

> **작성일**: 2026-10-13 · **v1.0**(최초) · **v1.1**(2026-10-13 — 인터뷰로 요건 확정 · 게이트 전건 확정 · D-327) · **v1.2**(2026-10-13 — 요건 추가: 패널 접힘 시 표 넓게 · 표 「넓게 보기」 버튼 · §2.8 · G-8·G-9) · **재채번**(2026-10-13 — 원 plans/154 · `origin/main` 병합 시 원격 apm_noise가 같은 번호를 먼저 푸시) · **v1.3**(2026-10-13 — 폐쇄망 실사용 피드백: 압축 날짜 시간 축 · 답 본문 표 해설 · G-3 개정 · §8)
> **상태**: **완료(2026-10-13 · W0~W6 · §8 개정 반영)** · 수용 기준 1~9 충족(§7) · 게이트 G-1~G-9 확정(§3) · 결정 **D-327**(구현 부기) · 판정·원인 추정은 `plans/155`로 분리 · 후속 권고 §7.4
> **요청 원문(2026-10-13)**: *"「지난 6시간 힙 메모리 추세 보여줘」라고 했을 때 테이블 형식으로 표기가 된다. 이렇게 조회가 된 데이터는 그래프 표현 버튼을 생성하여 그래프로 볼 수 있는 기능을 추가하려고 한다. 이런 유형의 프롬프트의 답변 정보를 그래프로 보여지거나 필드명 또는 필드 데이터의 해설을 LLM을 통해 재질문하도록 링크를 생성하여 해당 링크를 클릭했을 때 해석을 보여주는 UI를 제공하는 기능을 추가하려고 한다. 이 요건의 구현 계획을 수립하라."*
> **인터뷰 확정(2026-10-13)**: ① *"컬럼 헤더를 누르면 용어 사전이고 값을 누르면 값 해석으로 동작하도록 하라."* ② *"사전 우선, 없으면 LLM 생성으로 하라."* ③ *"결과 안 위치와 의미까지만, 판정·원인 추정 분석은 별도의 계획으로 정리하라."* ④ 링크 위치 *"권고대로 진행하라."* ⑤ 의도 정리(§0.1) *"예."*
> **추가 요청(2026-10-13 · v1.2)**: *"응답에 나온 표가 가로로 길어지면 스크롤을 해야 되고 보기가 힘들다. 「질의 이력」이나 「처리현황」을 접으면 표를 넓게 볼 수 있도록 하는 기능을 계획서에 포함시키고 넓게 보기 버튼을 만들어서 표만 넓게 보는 기능도 계획에 포함시켜라."*
> **성격**: 현행 실측 + 설계 + 확정 게이트 + 작업 분해.
> **관련 결정**: **D-327**(이 계획) · D-262(결과 API 소유자 인가 · 화면과 같은 마스킹 · 다운로드 감사) · D-248(대화 다시 불러오기) · D-003(읽기 전용) · D-162(신규 플래그 기본 off) · D-255(매뉴얼 동반) · D-303(회귀) · D-127·D-240(실 LLM = 로컬 MLX) · D-179(overfit 스캔) · D-274(게이트웨이 경계)
> **분리 계획**: `plans/155`(값의 정상/위험 판정 · 원인 추정 분석)
> **실측 기준**: 2026-10-13 `main` HEAD `6810336`. `file:line`은 이 시점 값이다 — W0에서 다시 대조한다.
> **계획 시작 SHA**: `6810336`(2026-10-13 착수) · **회귀 대상 파일**(44개 — 원격 병합분 제외): `src/domain/result_chart.py` · `src/domain/result_value_context.py` · `src/api/result_chart_view.py` · `src/api/routes/result_viz.py` · `src/api/routes/query.py` · `src/api/schemas.py` · `src/api/server.py` · `src/api/settings_catalog.py` · `src/api/thread_history.py` · `src/infrastructure/thread_repository.py` · `src/config.py` · `src/semantic/result_glossary.py` · `src/semantic/result_explainer.py` · `src/prompts/result_explain.py` · `config/result_glossary.yaml` · `config/settings_help/result_viz.yaml` · `.env.example` · `src/static/js/result-viz.js` · `src/static/js/app.js` · `src/static/css/style.css` · `src/static/index.html` · `src/static/vendor/chartjs/*`(3) · `scripts/manual/{captures.yaml,content/admin.md,content/user.md,features.yaml,run_capture.py,snapshot.py,fixtures/queries/3ee6ec6113ecd276.json}` · `src/static/manual/{admin,user}.html` · `tests/test_api/test_plan160_{chart_api,explain_api,static_assets,verify}.py` · `tests/test_api/test_settings_catalog.py` · `tests/test_domain/test_plan160_{result_chart,value_context}.py` · `tests/test_semantic/{__init__,test_plan160_glossary,test_plan160_explainer}.py` · `tests/test_manual/test_manual_coverage.py`

---

## 0. 요약

조회 응답의 표는 지금 **LLM이 쓴 마크다운 표**로만 보인다. 원본 행은 서버 `_results_store`에 `query_id`로 이미 보관되고(CSV 다운로드가 이것을 쓴다), 소유자 인가·마스킹 규칙(D-262)도 갖춰져 있다. 이 계획은 그 위에 세 동작을 얹고, 가로로 긴 표를 읽기 쉽게 하는 화면 개선 1건(D)을 함께 한다.

| 동작 | 무엇 | 핵심 원칙 |
|---|---|---|
| **A. 그래프 보기** | 답 말풍선에 「그래프로 보기」 버튼 → 펼침 패널에 선/막대 그래프 + **원본 데이터 격자** | 버튼 노출·축 선택은 질문 문구가 아니라 **데이터 모양으로 코드가 결정적으로 판정**(LLM 미사용). 데이터는 저장된 원본 행에서 온다 |
| **B. 칼럼 용어 사전** | 격자 **머리글** 클릭 → 팝오버에 그 칼럼의 뜻·단위 | **사전 우선, 없으면 LLM 생성**(「사전 미등재 · LLM 생성」 표시). LLM 생성분은 사전에 **자동 등록하지 않는다** |
| **C. 값 해석** | 격자 **셀 값** 클릭 → 팝오버에 그 값의 해석 | **결과 안에서의 위치와 의미까지만** — 최고·최저·순위, 평균 대비, 관련 칼럼 대비 비율, 직전 추세. **정상/위험 판정·원인 추정은 하지 않는다**(`plans/155`) |
| **D. 표 넓게 보기** | ① 「질의 이력」·「처리 현황」을 접으면 대화 영역과 표가 넓어진 칸만큼 넓어진다 ② 표마다 「넓게 보기」 버튼 → 표만 화면 전체 폭 오버레이로 본다 | 프런트(CSS·JS)만 — 서버·데이터 경로 변경 0. 마크다운 답 표와 A의 데이터 격자 모두 대상 |

A~C는 질의 파이프라인(LangGraph)을 건드리지 않는다 — 결과 저장 이후의 **사후 조회 API + 프런트 패널**이다. 응답에는 「그래프 가능」 신호 1필드만 추가한다.

### 0.1 확정된 의도(인터뷰 · 사용자 「예」)

| 항목 | 내용 |
|---|---|
| Outcome | 조회 결과에 「그래프로 보기」 버튼 → 펼친 패널에서 그래프 + 원본 데이터 격자 → **머리글 클릭 = 용어 사전**, **값 클릭 = 그 값의 해석**(팝오버) |
| User | 결과 표의 칼럼명·숫자를 바로 읽기 어려운 사용자(비전문 사용자·운영자 공통) |
| Why now | 「지난 6시간 힙 메모리 추세」 같은 시계열 답이 표로만 나와 추세가 안 읽히고 칼럼 뜻도 모른다 |
| Success | 버튼은 데이터 모양으로 판정(시계열 = 선·인스턴스별 시리즈, 범주+수치 = 막대) · 용어 사전은 사전 우선/없으면 LLM 생성(표시) · 값 해석은 결과 안 위치·의미까지 |
| Constraint | D-262 인가·마스킹(CSV와 같은 규칙) · 마스킹 후 값만 LLM 송신 · 결과에 실존하는 칼럼·값만 받음(자유 입력 없음) · 플래그 기본 off · 폐쇄망이라 Chart.js vendor 반입 · LLM 생성 용어 자동 등록 금지 |
| 추가(v1.2) | 패널(질의 이력·처리 현황) 접힘 시 표가 넓어진 폭을 쓴다 · 표마다 「넓게 보기」 버튼으로 표만 전체 폭 오버레이 |
| Out of scope | 마크다운 답 표에 링크 · 정상/위험 판정·원인 추정(`plans/155`) · 추세 전체 해석 버튼 · 결과 영구 저장(만료 시 「다시 실행」 문구만) |

---

## 1. 현행 실측

### 1.1 데이터는 이미 서버에 있다

| 항목 | 위치 | 비고 |
|---|---|---|
| 결과 저장소 | `src/api/routes/query.py:84-85` `_results_store`(OrderedDict · 상한 1000건 · 인메모리) | 서버 재기동·축출 시 소실 |
| 저장 지점 | `_store_result(...)` 호출 10곳(`query.py:2359`·`2406`·`2565`·`2631`·`2968`·`3076`·`3290`·`3334`·`3781`·`3872`) | 동기·스트림·파일·파일 스트림 4경로 × 역질문·정상 |
| 소유자 인가 | `_owned_result()` `query.py:163` | D-262 — 소유자·관리자만, 소유자 없음은 fail-closed |
| 마스킹 | `DataMasker.mask_rows()` `src/security/data_masker.py:122` | CSV가 화면과 같은 규칙으로 가림(`query.py:4188`) |
| 다운로드 감사 | `_audit_file_download()` `query.py:215` | |
| APM 행 | `src/orchestration/apm_query.py:2517` `query_results: rows`(전문) · 화면 표는 `display_rows`로 긴 셀 절단 | 힙 추세는 `heap_used_mb`·`heap_committed_mb` 등 중립 키(`apm_gateway/apm_gateway/adapters/jennifer/fields.py:35-48`) |
| 다중 표 경계 | 복합 턴은 task별 표를 이어 붙인 목록 · 경계는 `conversation_context[RESULT_TABLES_KEY]`(`src/orchestration/result_aggregator.py:259`) — **저장소에는 실리지 않음** | §2.2 A-2에서 저장 |
| 원 질문 | 저장 dict에 `user_query`가 있는지 W0 확인(값 해석 프롬프트 입력) | |

### 1.2 화면

- 정적 응답: `renderAgentMessage()` `src/static/js/app.js:1063` · 스트림 완료: `finalizeStreamingMessage()` `app.js:1871` · 대화 복원(D-248)도 `renderAgentMessage`를 탄다.
- 버튼 줄: `message-download-actions`(엑셀·CSV 다운로드) — 그래프 버튼은 같은 줄에 둔다.
- 차트 라이브러리 **없음**(`src/static/vendor/`에는 `three`만). 노이즈 관제 화면 추이 그래프는 손으로 만든 CSS 막대(`src/static/js/noise.js:322~`). 폐쇄망이라 CDN 불가 → vendor 반입.
- `app.js`는 5,414줄 — 새 기능은 별도 파일로 둔다.
- **표 폭 상한(D의 원인)**: 「질의 이력」(`#historyToggle` · `.chat-layout.history-collapsed`)·「처리 현황」(`#panelToggle` · `.chat-layout.panel-collapsed`) 접기는 이미 있고 그리드 칸은 넓어진다(`src/static/css/style.css:1115-1126`). 그러나 대화 영역 `.chat-messages`가 `max-width: 920px`(`style.css:1144`), 답 말풍선 `.message-content`가 `max-width: 75%`(`style.css:2103`)로 묶여 **패널을 접어도 표 폭은 그대로**다(약 690px). 표는 `.response-text table { display:block; overflow-x:auto; max-width:100% }`(`style.css:2237`)로 말풍선 안 가로 스크롤만 된다.
- 답 표(마크다운)는 머리글이 번역되고(「힙 사용량(MB)」) 값이 가공돼(「1,820.5」) 원본 칼럼·값과 1:1 대응이 안 된다 — 링크를 붙이지 않는 근거(G-3).

### 1.3 칼럼 설명 원천(용어 사전 근거)

- DB: `config/db_profiles/{db_id}.yaml`의 칼럼 `description`(예: `polestar.yaml:30-38`).
- APM: 게이트웨이 중립 키 사전(`apm_gateway/apm_gateway/adapters/jennifer/fields.py`) — **패키지 경계상 본체가 import할 수 없다**(D-274 · 양방향 import 0). 본체 쪽엔 `apm_query`의 뷰 라벨만 있다 → 본체 소유 사전이 필요하다(§2.3 B-1).

### 1.4 응답 계약 함정

- `QueryResponse`(`src/api/schemas.py:103`)에 선언하지 않은 필드는 pydantic이 **조용히 버린다**(121 §12.5 TP-0.1 함정).
- SSE `done` 이벤트·동기 응답·대화 기록(`src/api/thread_history.py:94` 턴 dict) **세 곳에 대칭으로** 실어야 복원 화면에도 버튼이 뜬다.

### 1.5 W0 재대조(2026-10-13) — 계획과 다른 점

위 `file:line`은 W0에서 대조해 대부분 일치했다(`.message-content`는 `style.css:2102`). 설계를 바꾼 차이는 다음 7건이다.

| # | 실측 | 반영 |
|---|---|---|
| ① | 저장 dict에 질문 원문(`user_query`)·출처 DB 키가 없다(`db_scope`가 db_id 목록 · APM은 `apm_source_scope` 존재로만 추정) | W2: 저장 10곳에 `user_query`·`result_tables`·`chart`를 싣는다 · 출처는 `db_scope`·`apm_source_*`로 판단 |
| ② | 대화 복원 `showThreadTurns`(`app.js` ≈:4555)는 CSV 버튼이 생기지 않게 `query_id` 없이 그린다 | W2: `query_thread_turns.chart JSONB`(`ADD COLUMN IF NOT EXISTS`) + `TurnRecorder` · W3: 복원 턴은 `result_query_id`를 따로 넘긴다 |
| ③ | `RESULT_TABLES_KEY`는 `conversation_context` 안 `{label, rows(행 수)}` 목록뿐(2표 이상 · 합 일치일 때만) · query.py는 읽지 않는다 | 동기는 `result["conversation_context"]`, 스트림은 `_scope_state`에서 얻어 저장 · 표 분할은 행 수로 순서대로 |
| ④ | APM 힙 추세는 **인스턴스당 1행 + 중첩 `trend{지표:[{time_ms,value}]}`**(평탄 시계열 아님 · 시간 epoch ms) | W1 `chart_rows`로 평탄화 · API·격자·값 해석의 행 번호는 `chart_rows` 기준 |
| ⑤ | `db_profiles`에 표→칼럼→설명 트리가 없다(`entity_columns`·`known_attributes`만) | 용어 사전 ① 원천을 `SchemaCacheManager.get_descriptions`(`"table.column"`) · `load_knowledge_descriptions` · 프로필 `entity_columns`·`known_attributes`로 바꾼다 |
| ⑥ | 새 설정 그룹은 설정 카탈로그 건수 테스트(26그룹·402항목)에 걸린다 | `GROUP_ORDER`·`GROUP_TITLES` · `config/settings_help/result_viz.yaml` · `.env.example` · 건수 단언 갱신 |
| ⑦ | 감사에 해시 전용 필드가 없다 · fabrix LLM은 PII 차단 덤프가 클라이언트 안에서 자동 적용된다 | 해시는 `AuditLogEntry.extra`/`_record_event(**kwargs)`로 · PII 경로는 별도 배선 불필요 |

---

## 2. 설계

### 2.1 전체 흐름

```
질의 완료 ─ _store_result(rows, tables) ─ 응답에 chart = {kind, tables:[…]} | None
                                              │
화면: chart != None → [그래프로 보기] 버튼    │
   클릭 → GET  /api/v1/query/{id}/chart  ─────┘ (소유자 인가 → 마스킹 → 판정 → 시리즈 데이터)
   패널: 그래프 + 원본 데이터 격자
   머리글 클릭 → GET  /api/v1/query/{id}/glossary?table=&column=
                 (인가 → 칼럼 실존 검증 → 사전 조회 → 없으면 LLM 생성 · 캐시)
   셀 클릭     → POST /api/v1/query/{id}/interpret {table, column, row}
                 (인가 → 칼럼·행 실존 검증 → 마스킹 → 결정적 위치 계산 → LLM 문장화 · 캐시)
   팝오버: 출처 표시(「용어 사전」 / 「사전 미등재 · LLM 생성」 / 「LLM 해석 · 참고용」)
```

### 2.2 A. 그래프 보기

**A-1 판정 — `src/domain/result_chart.py`(신규 · 순수 함수 · I/O·LLM 0)**

- 입력: 행 목록(마스킹 후), 표 경계(있으면). 출력: 표마다 `ChartSpec{kind, x, y[], series_by, label}` 또는 None.
- 칼럼 유형은 **값 모양으로만** 판정한다 — 칼럼 이름 리터럴 금지(overfit_check · D-179).
  - 시간: ISO 날짜시각 문자열·datetime 파싱 성공률 ≥ 90% · epoch(ms/s)는 값 범위 + 단조 증가 비율로 판정.
  - 수치: int/float 또는 수치 문자열 성공률 ≥ 90%(불린 제외).
  - 범주: 그 밖의 문자열.
- 종류(G-5):
  - `line` — 시간 칼럼 1 + 수치 칼럼 ≥ 1. 범주 칼럼이 있고 고유값 ≤ 10이면 시리즈 분할(`series_by` — 예: 인스턴스별 힙). 고유값 > 10이면 상위 10 + 나머지 숨김 고지.
  - `bar` — 시간 칼럼 없음 + 범주 1 + 수치 ≥ 1 + 행 ≤ 50(상위 N 질의).
  - 그 외 → None(버튼 없음). 행 < 2도 None.
- 식별자성 수치(고유값 = 행 수인 정수 칼럼)는 y에서 뺀다 — 값 모양 규칙.
- 점 상한 2,000/시리즈 — 넘으면 균등 간격 축소 + `downsampled: true` 고지.

**A-2 응답 신호** — `QueryResponse.chart: dict | None`(kind·표별 라벨만, 데이터 없음). 동기·SSE done·파일 경로·대화 기록 턴 **4곳 대칭**. 판정은 `_store_result` 직전에 한 번만 — 헬퍼 `_chart_field(rows, tables)`로 10개 저장 지점에 같은 방식으로 붙인다. 복합 턴 표 경계(`RESULT_TABLES_KEY`)도 저장소에 함께 싣는다.

**A-3 데이터 API** — `GET /api/v1/query/{query_id}/chart`
- `_owned_result()`(D-262) → `DataMasker.mask_rows()` → A-1 판정 → 시리즈 데이터 + 격자용 행 반환. 화면·CSV와 **같은 마스킹**.
- 저장소에 없음(재기동·축출) → 404 「결과가 만료되었습니다 — 같은 질문을 다시 실행해 주세요」(G-6). 복원된 옛 대화에서 흔히 난다 — 버튼은 그대로 두고 패널에 이 문구를 보인다(침묵 실패 금지).
- 감사: 조회 감사 1행(`_audit_file_download`와 같은 계열, `file_type="chart"`).

**A-4 프런트 — `src/static/js/result-viz.js`(신규) + CSS**
- `ResultViz.attach(bubbleEl, data)`를 `renderAgentMessage`·`finalizeStreamingMessage` 두 곳에서 1줄씩 호출(복원 경로는 앞의 것으로 자동 포함).
- 버튼: `message-download-actions` 줄에 「그래프로 보기」. 클릭 시 패널 펼침(지연 로드 · 재클릭 접기).
- 패널: 표가 여럿이면 표 선택 탭 · 그래프(Chart.js — G-1 · 시리즈 범례 토글 · 툴팁) · 「데이터」 격자(원본 칼럼명 + 사용자 라벨). 머리글·셀은 링크 모양(밑줄 점선 + 커서)으로 클릭 가능함을 보인다.
- 테마: `theme.js` 토큰(라이트/다크) 준수.

### 2.3 B. 칼럼 용어 사전(머리글 클릭)

**B-1 사전 — `config/result_glossary.yaml`(신규) + 로더 `src/semantic/result_glossary.py`**
- 조회 순서: ① DB 칼럼 = 그 결과의 `db_id` 프로필(`config/db_profiles/{db_id}.yaml`)의 `description`(사본 금지 — 런타임 조회) ② APM 중립 키 = `result_glossary.yaml`(본체 소유 · 키·한국어 설명·단위) ③ 없으면 LLM 생성.
- 첫 편입 범위: 힙·GC·응답시간·TPS·에러율 등 런타임 추세·애플리케이션 상태 계열 중립 키(W0에서 실제 결과 칼럼을 채집해 확정).
- 프로필 경로는 결과의 출처(`db_id`·APM 여부)를 저장 dict에서 읽는다 — W0에서 저장 키 확인.

**B-2 API** — `GET /api/v1/query/{query_id}/glossary?table=&column=`
- `_owned_result()` → `column`이 그 표 행 키에 **실존**하는지 검증(아니면 400).
- 사전 적중 → `{source: "glossary", text, unit, origin}` 즉시 반환(LLM 호출 0).
- 사전 미적중 → LLM 생성: 입력은 칼럼명 · 원 질문 · 출처 시스템 라벨 · 그 칼럼 표본 값 3개(마스킹 후) · 이웃 칼럼명. 지시: 「뜻과 단위를 2~3문장 · 확실하지 않으면 그렇다고 말할 것」. 반환 `{source: "llm_generated", text}` → UI 「사전 미등재 · LLM 생성」 표시.
- **자동 등록 금지** — 생성분은 응답 캐시에만 둔다(사전 파일·DB에 쓰지 않음 · LLM 자동 등록 오염 자기강화 방지). 미적중 칼럼은 로그 1줄(`glossary_miss`)로 남겨 사람이 사전을 보강할 근거로 쓴다.
- 캐시: `(출처, column)` 키 인메모리 LRU — 같은 칼럼은 결과가 달라도 같은 설명.

### 2.4 C. 값 해석(셀 클릭)

**C-1 결정적 위치 계산 — `src/domain/result_value_context.py`(신규 · 순수)**
- 입력: 그 표의 행(마스킹 후) · 칼럼 · 대상 행 번호. 출력(수치 칼럼): 순위(n/N) · 최고/최저 여부 · 평균·중앙값 대비 차(%) · 같은 시리즈(`series_by`) 안에서의 위치 · 시간 축이 있으면 직전 k개 점 대비 방향(상승/하락/평탄) · 같은 행의 **단위가 같은 관련 수치 칼럼** 대비 비율(예: used/committed — 단위·값 모양으로만 고르고 이름 리터럴 금지).
- 비수치 칼럼: 그 값의 빈도(표 안 몇 건) · 같은 행의 시간·식별 칼럼.
- 숫자는 **코드가 계산**한다 — LLM이 계산하지 않는다(비결정성 가드).

**C-2 API** — `POST /api/v1/query/{query_id}/interpret` `{table, column, row}`
- `_owned_result()` → `table`·`column`·`row` 실존 검증(아니면 400). 자유 텍스트는 받지 않는다 — 프롬프트 주입 표면 차단.
- C-1 결과 + 원 질문 + 칼럼 사전 설명(B-1 적중 시)을 LLM에 주고 **2~4문장 한국어 문장화**만 시킨다. 지시: 「주어진 수치만 사용 · 정상/위험 판정 금지 · 원인 추정 금지 · 데이터에 없는 사실 금지」.
- 반환: `{facts: [...결정적 계산값], text, source: "llm_interpretation"}` — UI는 **계산값 목록을 먼저**, LLM 문장을 「LLM 해석 · 참고용」으로 그 아래에 보인다. LLM 실패·타임아웃(20초)이면 계산값만 + 실패 사유.
- 출력 가드: 문장에 판정·원인 어휘(「위험」·「정상」·「장애」·「원인」·「필요합니다」 등 — 목록은 프롬프트 모듈 상수)가 나오면 문장을 버리고 계산값만 보인다(로그 1줄). 금지 범위는 좁게 — 「상승」·「최고치」 같은 위치 서술은 허용.
- 캐시: `(query_id, table, column, row)` LRU(상한 500).

### 2.5 공통 — LLM·보안·감사

- LLM: `create_llm(config, purpose="answer")`(워커 평면 — `src/api/routes/doc_search.py:118` 선례). 테스트·스모크는 로컬 MLX(D-127·D-240).
- 프롬프트: `src/prompts/result_explain.py`(신규 — 용어 생성·값 해석 2종).
- PII: 마스킹 후 값만 송신. FabriX PII 필터 차단 시 `logs/pii_block/` 덤프 경로를 타는지 W0에서 실측 · 차단되면 사전/계산값만 + 사유 표시.
- 감사: 질의 감사 대상(`audit_service`) — 용어·해석 요청 1행(대상 칼럼, 값은 해시 · 원문 미기록).
- 팝오버: Esc 닫기 · 포커스 이동 · 로딩 스피너 · 출처 배지. 링크는 대화 입력창에 질문을 넣지 않는다(G-2).

### 2.6 D. 표 넓게 보기(프런트만)

**D-1 패널 접힘 연동(CSS)**
- 「질의 이력」·「처리 현황」 중 **하나라도 접히면** `.chat-messages`의 상한을 푼다 — `.chat-layout.history-collapsed .chat-messages, .chat-layout.panel-collapsed .chat-messages { max-width: <넓은 상한> }`. 상한 값은 W0에서 1440px 이상 해상도 실측으로 정한다(권고: 둘 다 접힘 = 상한 없음 · 하나만 접힘 = 1280px).
- 답 말풍선은 **표·그래프 패널이 든 것만** 폭 제한을 푼다 — `.message--agent .message-content:has(table, .result-viz-panel) { max-width: 100% }`. 표 없는 문장 답은 75%를 유지한다(긴 줄은 읽기 어렵다). 표 안 문단(`.response-text p`)은 그대로 흐른다.
- 접기/펼치기 전환은 기존 토글만 쓴다 — 새 버튼·새 상태 없음. 좁은 화면 미디어 쿼리(`style.css:4617` 1024px 이하)는 현행 유지.

**D-2 표 「넓게 보기」 버튼(JS · `src/static/js/result-viz.js`의 독립 함수 `ResultViz.enhanceTables(rootEl)`)**
- 대상: 답 말풍선의 마크다운 표(`.response-text table`)와 A의 데이터 격자. 호출 지점은 `renderAgentMessage`·`finalizeStreamingMessage`(스트리밍 토큰 렌더 중에는 붙이지 않는다 — 렌더가 매 프레임 갈아엎는다).
- 버튼 노출: 표가 말풍선보다 넓을 때만(`scrollWidth > clientWidth`) 표 오른쪽 위에 「넓게 보기」. 패널 접힘·창 크기 변화로 넘침이 바뀌면 다시 판정한다(`ResizeObserver` 1개 공유).
- 오버레이: 표 **복제본**을 화면 전체 폭(좌우 여백 24px) 고정 오버레이에 띄운다 — 머리글 고정(`position: sticky`) · 가로·세로 스크롤 · 닫기 버튼 · Esc · 바깥 클릭 닫기 · 열 때 포커스 이동, 닫으면 버튼으로 복귀(`role="dialog"` · `aria-modal`). 원본 표 DOM은 옮기지 않는다(대화 스크롤 위치 보존).
- A의 데이터 격자를 오버레이로 열면 머리글·셀 클릭(B·C 링크)도 그대로 동작한다(같은 이벤트 위임).
- 테마 토큰 준수 · 서버 호출 0.

**D-3 플래그 없음(G-9)** — D는 CSS·JS 표시 개선뿐이라 데이터·응답 계약을 바꾸지 않는다. 신규 플래그 기본 off 원칙(D-162)의 예외로 두고 근거를 D-327에 적는다. A~C의 플래그(`chart_enabled`·`explain_enabled`)와 무관하게 동작한다.

### 2.7 플래그

`ResultVizConfig`(`cfg.result_viz`) — `chart_enabled`·`explain_enabled` 둘 다 **기본 off**(D-162). `chart_enabled` off면 응답 `chart`는 None·`/chart` 404 — 현행과 비트 동일. `explain_enabled` off면 격자는 보이되 머리글·셀이 링크가 아니고 `/glossary`·`/interpret` 404. 기동 시 1회 해석.

### 2.8 계층 배치(arch_check)

| 파일 | 계층 |
|---|---|
| `src/domain/result_chart.py` · `src/domain/result_value_context.py` | domain(순수) |
| `src/prompts/result_explain.py` | prompts |
| `src/semantic/result_glossary.py`(사전 로더 · db_profiles 조회) | application |
| `src/api/routes/result_viz.py`(신규 라우터 — 3 API) | interface |

`query.py`의 `_results_store`·`_owned_result`를 새 라우터가 쓰도록 import 경로만 연다(로직 이동 없음).

---

## 3. 게이트 — 확정(2026-10-13 · 인터뷰 · G-8·G-9는 v1.2 권고안)

| # | 질문 | 확정 | 근거 |
|---|---|---|---|
| **G-1** | 그래프 라이브러리 | **Chart.js 4 UMD를 `src/static/vendor/`에 반입**(MIT · 선·막대·범례·툴팁 내장) | 폐쇄망 반입 1파일 · 선·막대를 한 API로. 기각: uPlot(막대 약함) · SVG 직접(~400줄 자체 구현) |
| **G-2** | 링크 동작 | **전용 API → 팝오버**(파이프라인 미경유) | 새 대화 턴으로 보내면 계획기·라우터가 데이터 조회로 오분류할 수 있고 턴·감사·지연 비용이 크다 |
| **G-3** | 링크 위치 | ~~그래프 패널의 원본 데이터 격자에만~~ → **격자 + 답 본문 표**(2026-10-13 개정 · §8) | 원 근거(답 표는 LLM이 번역·가공)가 실측과 달랐다 — 정상 응답의 결과 표는 `output_generator._render_result_table`가 원본 행으로 렌더한다. LLM이 쓴 번역 머리글 표만 대응 불가로 남는다 |
| **G-4** | 해설 대상 | **머리글 = 용어 사전(사전 우선 · 없으면 LLM 생성 · 표시)** · **값 = 결과 안 위치·의미 해석** · 추세 전체 해석 버튼 없음 | 사용자 지시 ①②③ |
| **G-5** | 그래프 판정 범위 | **시계열 선 + 범주 막대** | 상위 N 질의도 버튼이 뜬다 |
| **G-6** | 만료된 결과 | **404 「다시 실행」 문구만** | 결과 영구 저장은 보존·보안 결정이라 별도 계획 |
| **G-7** | 판정·원인 추정 | **범위 밖 → `plans/155`** | 사용자 지시 ③ · 임계치 원천이 본체에 없어 LLM이 지어내게 된다 |
| **G-8** | 패널 접힘 시 넓힐 대상(v1.2) | **표·그래프 패널이 든 답 말풍선만 100%** · 문장 답은 75% 유지 · 대화 영역 상한은 접힘 조합별로 완화 | 사용자 추가 요청 · 문장까지 전폭으로 펴면 줄이 길어 읽기 어렵다 |
| **G-9** | D의 플래그(v1.2) | **플래그 없음**(D-162 예외 · D-327에 근거) | 표시 개선만 · 데이터·계약 불변 · 되돌리기는 CSS·JS 되돌림 |

---

## 4. 작업 분해(Wave)

| Wave | 범위 | 산출 | 검증 |
|---|---|---|---|
| **W0** | 실측 재대조 | `file:line` 갱신 · 힙 추세 질의의 실제 `query_results` 칼럼·값 모양 채집(가짜 LLM 종단 또는 픽스처) · 저장 dict의 `user_query`·`db_id`·APM 출처 키 · 복합 턴 표 경계 shape · PII 필터 경로 | 계획서 §1 갱신 |
| **W1** | A-1·C-1 domain | `result_chart.py` · `result_value_context.py` + 단위 테스트(시계열·시리즈 분할·상위 N 막대·식별자 제외·혼합 타입·빈 값·10 초과 시리즈·점 축소 · 순위·평균 대비·직전 추세·관련 칼럼 비율·비수치 빈도) | `pytest` 자기 파일 · `regress.py --no-tests` |
| **W2** | A-2·A-3 백엔드 | `ResultVizConfig` · `QueryResponse.chart` · 4경로 대칭 신호 · 표 경계 저장 · `GET …/chart` · 감사 | API 테스트: 소유자 403·관리자 통과·인증 off·404 만료·플래그 off 비트 동일·마스킹 적용 · **`[웨이브 회귀 필요]`**(config·schemas 변경) |
| **W3** | A-4 프런트 그래프 | Chart.js vendor 반입 · `result-viz.js` · CSS · `app.js` 호출 2줄 · `index.html` 스크립트 | 실 브라우저: 힙 6시간 추세(시리즈 분할) · 상위 N 막대 · 복원 대화 만료 문구 · 다크 모드 |
| **W4** | B·C 해설 | 사전·로더 · 프롬프트 2종 · `GET …/glossary` · `POST …/interpret`(실존 검증·출력 가드·캐시·타임아웃) · 팝오버 UI | 가짜 LLM: 사전 적중 시 LLM 호출 0 · 미적중 시 생성 표시·자동 등록 없음 · 실존하지 않는 칼럼/행 400 · 마스킹 값만 프롬프트에 실림 · 판정 어휘 문장 폐기 · 캐시 적중 · 실패 문구 · MLX 대표 2건 1회(바뀐 프롬프트) |
| **W5** | D 표 넓게 보기(**독립 · W0 뒤 먼저 해도 된다**) | CSS 접힘 연동(D-1) · `ResultViz.enhanceTables` 오버레이(D-2) · 호출 2줄 | 실 브라우저: 패널 접힘 4조합에서 표 폭 실측 · 문장 답 75% 유지 · 넘칠 때만 버튼 · 오버레이 Esc·포커스 복귀 · 다크 모드 · 1024px 이하 현행 동일 |
| **W6** | 마무리 | 매뉴얼(D-255 — 사용자 매뉴얼 「그래프로 보기」·「용어 사전·값 해석」·「표 넓게 보기」 절 + 캡처, 관리자 매뉴얼 플래그·사전 보강 절차) · D-327 구현 부기 · INDEX | `pytest tests/test_manual` · `regress.py --base <시작 SHA> --files …` |

---

## 5. 수용 기준

1. 「지난 6시간 힙 메모리 추세 보여줘」 응답에 「그래프로 보기」 버튼이 뜨고, 누르면 시간 축 선 그래프가 인스턴스별 시리즈로 그려진다. 값은 CSV와 같다(마스킹 포함).
2. 그릴 수 없는 결과(목록·단일 행·텍스트 위주)에는 버튼이 없다.
3. 격자 머리글을 누르면 사전 설명이 뜬다(LLM 호출 없음). 사전에 없는 칼럼은 「사전 미등재 · LLM 생성」 표시와 함께 생성 설명이 뜨고, 사전 파일은 바뀌지 않는다.
4. 격자 값을 누르면 결정적 계산값(순위·평균 대비·직전 추세·관련 칼럼 비율)과 LLM 문장이 뜬다. 문장에 정상/위험 판정·원인 추정이 없다.
5. 결과에 없는 칼럼·행으로 API를 호출하면 400. 다른 사용자의 `query_id`면 403(관리자 제외 · 인증 off 예외 — D-262와 같음).
6. 플래그 off에서 응답·화면이 현행과 동일하다(`chart` None · 버튼 없음).
7. 서버 재기동 뒤 복원 대화에서 버튼을 누르면 만료 문구가 보인다(빈 패널·콘솔 오류 없음).
8. 「질의 이력」·「처리 현황」 중 하나라도 접으면 가로로 긴 표가 넓어진 폭만큼 펼쳐진다. 표 없는 문장 답의 폭은 바뀌지 않는다.
9. 말풍선보다 넓은 표에만 「넓게 보기」가 뜨고, 누르면 표만 화면 전체 폭으로 보인다(머리글 고정 · Esc로 닫으면 원래 스크롤 위치).

## 6. 위험

| 위험 | 대응 |
|---|---|
| 시간 칼럼 오판(숫자 ID를 epoch로) | epoch는 범위 + 단조 증가 비율로 판정 · 판정 테스트에 반례 고정 |
| 값 해석 LLM이 판정·원인을 말함 | 프롬프트 금지 + 출력 어휘 가드(문장 폐기 → 계산값만) · 계산값과 LLM 문장 분리 표시 |
| LLM 생성 용어가 틀림 | 「사전 미등재 · LLM 생성」 표시 · 자동 등록 금지 · `glossary_miss` 로그로 사전 보강 |
| 관련 칼럼 비율 오매칭(단위 다른 칼럼끼리) | 단위·값 모양 일치할 때만 · 확신 없으면 비율 생략 |
| 해설이 PII 필터에 막힘 | 마스킹 후 값만 송신 · 차단 시 사전/계산값만 + 사유 표시 |
| 인메모리 저장소 상한 1000 | 만료 문구(G-6). 영속화는 별도 계획 |
| `query.py` 비대화 | 새 라우터 파일로 분리, `query.py`는 신호 헬퍼 호출만 |
| `:has()` 미지원 브라우저 | 폐쇄망 표준 브라우저 버전 W0 확인 · 미지원이면 JS가 표 있는 말풍선에 클래스를 단다 |
| 넓힌 대화 영역에서 입력창·사용자 말풍선이 어색하게 퍼짐 | 넓히는 대상은 답 말풍선(표 포함)뿐 · 입력창 폭은 현행 유지 |

---

## 7. 구현 기록(2026-10-13 · W0~W6 완료)

### 7.1 Wave별 산출

| Wave | 산출 |
|---|---|
| W0 | 실측 재대조 → §1.5(7건) |
| W1 | `src/domain/result_chart.py`(`classify_columns`·`ChartSpec`·`detect_chart`·`build_chart_data`·`chart_rows`·`split_tables`) · `src/domain/result_value_context.py`(`value_context` — facts 어휘 rank/is_max/is_min/vs_mean_pct/vs_median_pct/series_rank/trend/related_ratio/frequency/row_time/row_ident) |
| W2 | `ResultVizConfig`(`RESULT_VIZ_CHART_ENABLED`·`RESULT_VIZ_EXPLAIN_ENABLED` 기본 off · 설정 카탈로그 27그룹 · `config/settings_help/result_viz.yaml` · `.env.example`) · `QueryResponse.chart` · `src/api/result_chart_view.py`(판정 파이프라인 단일 함수 `chart_tables` = 마스킹 → 표 분할 → 평탄화 → 재마스킹 → 판정 → epoch 벽시계 변환 · 신호 `chart_field`) · 저장 10곳에 `chart`·`user_query`·`result_tables` · 응답 4경로(동기·SSE done·파일·파일 스트림 · 부분 결과 포함) · 대화 기록 `query_thread_turns.chart JSONB` · `src/api/routes/result_viz.py` `GET …/chart`(격자 1,000행 상한 + `rows_total` · 감사 `file_type="chart"`) |
| W3 | `ResultViz.attach`(그래프 패널 · 표 탭 · Chart.js 선/막대 · 범례 토글 · 격자 `table.result-viz-grid` · 만료 문구 · 테마 관찰) · Chart.js `<script>` · app.js 호출 2줄 + 복원 경로 `result_query_id` · CSS 「plans/160 A·B·C」 블록 · 정적 자산 `style.css?v=28`·`result-viz.js?v=3`·`app.js?v=24` |
| W4 | `config/result_glossary.yaml`(APM 중립 키 28) · `src/semantic/result_glossary.py`(출처 판단 · 조회 순서) · `src/prompts/result_explain.py`(용어·해석 프롬프트 · `<<<DATA>>>` 구획 · 금지 어휘) · `src/semantic/result_explainer.py`(LRU 2종 · 동시 4 · 20초) · `GET …/glossary` · `POST …/interpret` · 감사 `result_explain`(값은 sha256) · 팝오버 UI |
| W5 | 표 넓게 보기 — `style.css` 「plans/160 D」 블록 · `ResultViz.enhanceTables` 오버레이 |
| W6 | 매뉴얼 U-58 그래프로 보기 · U-59 칼럼 용어 사전·값 해석 · U-60 표 넓게 보기 · A-74 결과 그래프·값 해석 설정 · A-75 결과 용어 사전 보강 · 캡처 7장 · replay 스냅샷 프로필 `capture-viz` · 손 제작 힙 추세 픽스처 |

### 7.2 계획 이탈(근거 · 되돌리는 비용)

| # | 이탈 | 근거 | 되돌리기 |
|---|---|---|---|
| 1 | 식별자 제외를 「정수 · 전부 고유 · 정렬값 등차열(행 ≥ 3)」로 좁힘(계획: 고유값 = 행 수인 정수) | 상위 N 건수 같은 측정값이 우연히 전부 고유하면 막대가 사라진다. 대가로 비등차 정수 ID(`id`)는 y에 남는다(매뉴얼 주의 칸 · 범례로 숨김) | 상수·함수 1개 |
| 2 | **텍스트 위주 목록 제외** `_MAX_VARYING_CATEGORIES = 2` — 변하는 범주 칼럼이 2개 초과면 그래프 없음 | replay 실 녹화에서 목록형 답 오탐 3건(등급 막대 · 알람 ID 선 · 43행 서버 목록). 대가로 범주 3개 이상인 시계열도 그리지 않는다 | 상수 1개 |
| 3 | 한 시리즈 안 x 고유 강제 — 막대 x가 겹치면 다른 범주로 분할, 안 되면 None · 선도 같음(EAV long은 None) | 프런트가 같은 x를 마지막 값으로 덮어 틀린 그래프가 됐다(verifier) | 함수 3개 |
| 4 | 시간 x: datetime은 벽시계 그대로 epoch ms, **epoch 칼럼은 view 계층이 서버 로컬 벽시계 ISO로 바꾼다** → 화면은 UTC 서식으로 그려 원 벽시계가 보인다 | epoch(APM `time_ms`)만 UTC로 보여 문장·표와 9시간 어긋났다(verifier). domain은 시간대를 읽지 않는다. 격자 시간 칸은 ISO, CSV는 원 epoch라 표기가 다르다 | view 함수 2개 |
| 5 | 중첩 시계열 평탄화 `chart_rows` 신설 · 행 번호는 평탄 행 기준 · 평탄화 뒤 재마스킹 | APM 힙 추세가 인스턴스당 1행 + 중첩 `trend`(§1.5 ④) · DataMasker는 중첩 dict를 보지 않는다 | — |
| 6 | 막대 점은 행 순서 유지 | 상위 N 질의의 정렬 보존 | — |
| 7 | 그릴 표 없음 → `/chart` 404 「그래프로 그릴 수 있는 표가 없습니다」 · 플래그 off면 라우터 미등록(404) · 해설 API는 두 플래그 모두 on일 때만 | 빈 `tables: []`는 침묵 실패 · 플래그 off에서 401/422가 새로 생기지 않게(HEAD와 같은 404) | — |
| 8 | 되묻기 턴은 신호 없음 · 부분 결과는 표 경계 없이 판정 | 후속 입력이 직전 턴 행을 비우지 않아 되묻기 답에 직전 그래프가 열렸다(verifier) | — |
| 9 | SSE done은 신호 None이면 키 생략(바이트 불변) · 동기 `QueryResponse`는 `"chart": null`(기본값 필드 — `time_resolution` 선례) | 기존 키 집합 단언 테스트·스냅샷 0건(verifier grep) | — |
| 10 | 용어 생성 캐시 키에 결과 소유자 포함(계획: `(출처, column)`) · 생성 프롬프트에 「표본 값 인용 금지」 | 보안 감사 M-1 — 사용자 A의 질문·표본 값이 든 LLM 문장이 B에게 갔다 | 키 1줄 |
| 11 | 해설 LLM 동시 호출 상한 4(대기 포함 20초) · 원 질문을 `<<<DATA>>>` 구획 안으로(300자) · 구획 표지 무력화 · 금지 어휘에 「때문」·「로 인해」·「필요할」·「주의」 추가 · 로그 칼럼명 `%r` | 보안 감사 M-2·L-2·L-4 · verifier Minor | — |
| 12 | 감사 event `"result_explain"`은 `AuditEvent` enum 밖 문자열 | enum 추가는 `src/domain/audit.py` 변경 — 같은 str 필드 | enum 1행 |
| 13 | 패널 접힘 시 `.message-content`에 `min(75%, 654px)` 상한 · 패널 펼침 동안 `result-viz-open` 클래스로 말풍선 100% | 대화 영역이 넓어져도 문장 답 폭 불변(G-8) · inline-block 말풍선에서 캔버스가 108px로 쪼그라듦 | CSS 블록 |
| 14 | 대화 복원 턴은 `result_query_id`로 따로 넘김(CSV 버튼 없음) · 복원 턴에 다운로드 줄이 없으면 새로 만든다 | §1.5 ② | — |

### 7.3 검증

- **수용 기준** — 1: 힙 추세 선 · 인스턴스 시리즈 · KST 축 · 격자 74행 = CSV 펼침 74행(시간 칸 표기만 다름 · 마스킹 `***MASKED***` 일치) — `test_heap_trend_chart_is_line_split_by_instance`·`test_masking_matches_screen_and_csv` + replay 실 앱 · 2: 목록·단일 행·문장·상세·텍스트 위주 목록·되묻기 턴 버튼 없음 — `test_list_rows_have_no_signal_and_chart_404`·`test_clarification_turn_has_no_chart_signal`·반례 domain 테스트 + replay · 3: `test_glossary_hit_no_llm`·`test_miss_generates_logs_and_leaves_glossary_untouched` · 4: `test_interpret_returns_facts_and_text`·`test_interpret_guard_drop_is_facts_only` · 5: `test_nonexistent_target_is_400`·`test_other_user_403_admin_passes`·`test_auth_off_passes` · 6: `test_flag_off_keeps_response_unchanged` + HEAD worktree 바이트 대조(SSE done 8건 동일 · 동기 응답 `chart: null`만 추가) + replay · 7: replay 재기동 → 복원 → 만료 문구(페이지 오류 0 · 브라우저 자체 404 로그 1줄) · 8: replay 1440px 표 말풍선 708 → 1036/924 → 1262px · 문장 답 564 → 654(기능 이전 상한) · 9: replay 넘침 표에만 버튼 · 오버레이 sticky 머리글 · Esc 후 스크롤 위치·포커스 복귀.
- **화면 검증 방식**: 정적 하네스(가짜 fetch · Playwright · 44항목) + 매뉴얼 replay 서버(실 FastAPI 앱 · LLM 0 · 플래그 on/off 두 스냅샷) + 다크 모드.
- **MLX 대표 2건**(두 평면 mlx 확인 · 교정 전·후 각 1회): 용어 생성 「avg_cpu_usage_pct」 → `llm_generated` 2문장(교정 후 표본 값 인용 없음) · 값 해석 `heap_used_mb` 최고점 → 판정·원인 어휘 없는 3문장(4~6초).
- **회귀(계획 끝 1회)**: `python scripts/regress.py --base 6810336 --files <회귀 대상 파일 44개>` → rc=0 · 본체 통과 7,946 · 실패 61 · 에러 5 → 귀속 「원래 실패 66」(새 실패 0) · arch_check·overfit_check 통과 · ruff·mypy 이번 diff 줄 위반 0 · 범위: 모듈 단위 — 전체 미실행 · `[전체 회귀 권고] 사유: 허브 모듈 변경(전이 import 영향 ≥ 85%) — src.config 88%`.

### 7.4 잔여 · 후속 권고

- 비등차 정수 ID 칼럼이 막대·선 y에 남을 수 있다(범례로 숨김 — 매뉴얼 U-58 주의).
- 해설 API 사용자별 rate limit · 실패 부정 캐시 없음(동시 4 상한만) — FabriX 쿼터 운영 실측 뒤 판단.
- 감사 `value_sha256`은 저엔트로피 값이면 사전 대입으로 추정 가능(계획 §2.5 그대로) — 키드 HMAC 또는 제거는 별도 판단.
- APM 단일 소스 배포에서 `apm_source_scope`가 저장되지 않으면 출처가 DB로 판정돼 사전 조회 순서만 바뀐다(중립 키는 대개 사전으로 적중).
- 폐쇄망 표준 브라우저 실 화면 확인(`:has()` 폴백 클래스 포함) · 운영 FabriX로 해설 지연·PII 차단 실측.
- 스트림 경로 시간 되묻기의 `_clarify_store_fields` 비대칭(기존 D5)은 그대로 — 신호만 막았다.

## 8. 개정(2026-10-13 · v1.3 — 폐쇄망 실사용 피드백)

**요청 원문**: *"첨부 그림처럼 표 rows로 되어 있는 추세데이터도 그래프 처리가 되어야 한다. 또한 1. 처럼 원본표에도 설명에 있어야 한다"* (첨부: 은행존 월간 메모리 추세 3행 — 「그래프로 보기」 없음 · 답 표에 링크 없음. 「1.」 = 직전 답변에서 제시한 선택지 1 「답 표를 원본 행 기준으로 해설」)

### 8.1 원인(실측)

| 증상 | 원인 |
|---|---|
| 「그래프로 보기」 없음 | `stat_month`가 `"202607"` 같은 YYYYMM **문자열**(폴스타 `cmm_metric_stat_m.stat_date` — `config/db_profiles/polestar_b0.yaml:281`). ISO도 epoch도 아니라 수치로 판정 → 「정수·전부 고유·등차열」 식별자 규칙에 걸려 y에서도 빠짐 → 시간 칼럼 없음 · 변하는 범주 칼럼 없음 → 그릴 수 없음(`detect_chart` None). 일·시 통계(`YYYYMMDD`·`YYYYMMDDHH`)도 같다 |
| 답 표에 해설 없음 | G-3 설계대로 격자에만 링크. 그런데 G-3의 근거(답 표는 LLM이 번역·가공)는 정상 응답 경로에서 틀렸다 — 결과 표는 코드가 원본 행·원 칼럼명으로 렌더한다(`src/nodes/output_generator.py:617` · `render_markdown_table`) |

### 8.2 변경

1. **압축 날짜 시간 축**(`src/domain/result_chart.py` `parse_datetime`·`_parse_compact`) — **문자열** `YYYYMM`·`YYYYMMDD`·`YYYYMMDDHH`·`YYYY-MM`을 시간으로 읽는다. 연도 1970~2100 · 월·일·시 실재일 때만. **정수는 대상 아님**(10자리 시 단위가 epoch 초 범위와 겹치고, 정수 ID와 구분이 어렵다). 격자 값은 원문 그대로다(벽시계 변환은 epoch 정수만 — `_epoch_to_wall`이 `parse_datetime` 성공 값을 건드리지 않는다).
2. **월 단위 축 서식**(`result-viz.js`) — 시간 x가 전부 「그 달 1일 00:00」이면 눈금을 점마다(12개 넘으면 솎음) 두고 「2026-07」로 적는다. 툴팁도 같다.
3. **신호**(`chart_field`) — 두 플래그 on이면 `explain: true`를 더하고, 그릴 표가 없어도 결과 표가 있으면 `{"kind": null, "tables": [], "explain": true}`를 보낸다. 해석 off면 종전과 바이트 동일.
4. **원본 표 API** `GET /api/v1/query/{id}/tables`(해석 on일 때만 · 소유자 인가 · 다운로드 감사 `file_type="table"`) — 그릴 수 없는 표를 포함한 전 표의 칼럼·격자 행(1,000행)·`rows_total`·`kind`. 행은 그래프 API와 같은 함수(`result_tables` — 분할·평탄·마스킹·epoch 벽시계)에서 나와 행 번호가 같다.
5. **해설 대상 확대** — `/glossary`·`/interpret`가 그릴 수 없는 표도 받는다(`_explain_table` → `result_tables`).
6. **답 본문 표 링크**(`ResultViz.attach` → `linkAnswerTables`) — 답의 마크다운 표마다 머리글이 절반 이상 원본 칼럼과 같은 원본 표를 고르고(원본 표 하나는 답 표 하나에만), **원 칼럼명과 같은 머리글**만 링크한다. 행은 링크할 칸의 ¾ 이상이 값까지 맞는 원본 행을 찾아(같은 순번 우선 · 숫자는 값 비교 · ISO `T`/공백 동일시 · 한 원본 행은 한 번만) 그 행 번호로 링크한다. 못 맞춘 칸·행은 그대로 둔다(추측 링크 없음). 위임 클릭 리스너는 `table[data-rv-explain]`로 넓혔다(넓게 보기 복제본 포함).

### 8.3 선택지 1과의 차이(보고)

직전 답변의 선택지 1은 「답 표를 원본 행으로 다시 그림 — 모양이 바뀜」이었다. 실측 결과 답 표가 이미 원본 행으로 렌더된 표라, **표를 갈아 끼우지 않고 원본 행과 칸을 맞춰 링크만 단다**(모양 불변 · 해설은 원본 값 기준으로 정확). 맞출 수 없는 표(LLM이 머리글을 번역한 표 · 복합 필드명 `A > B` · 멀티 DB 출처 칼럼)는 링크가 없고 그래프 패널 격자를 쓴다.

### 8.4 검증

- 단위·API: `tests/test_api/test_plan160_answer_table_explain.py`(16) · 기존 160 테스트 갱신 2건(신호에 `explain` · 실패 주입 대상 `result_tables`) — 160 테스트 전체 통과.
- 브라우저(capture-viz 재생 서버 · LLM 0): 월간 추세 답 표 머리글 6·칸 18 링크 + 「그래프로 보기」 선 그래프(가로축 2026-07~09) · 사양 목록(그릴 수 없음) 답 표 링크 8 · 힙 추세(LLM 번역 머리글) 링크 0 · 콘솔 오류 0.
- 매뉴얼: U-58(압축 날짜)·U-59(답 본문 표) 본문 · 캡처 `result-answer-table-explain`(손 제작 재생 `fixtures/queries/720fee46a68a27bf.json` · 서버·값 가상) · `pytest tests/test_manual` 602 통과.

### 8.5 잔여

- 정수형 YYYYMM(`CAST … AS INTEGER`)은 여전히 식별자 규칙에 걸린다 — 운영 SQL이 문자열을 그대로 내므로 보류.
- 답 표가 20행으로 잘린 결과는 원본 앞 1,000행 안에서만 행을 찾는다(멀티 DB 균형 추출도 이 범위 안이면 맞춘다).
- 폐쇄망에서 두 플래그(`RESULT_VIZ_CHART_ENABLED`·`RESULT_VIZ_EXPLAIN_ENABLED`) on 여부 확인 필요 — 둘 다 on이어야 답 표 링크가 붙는다.
