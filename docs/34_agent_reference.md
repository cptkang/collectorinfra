# 에이전트 작업 참고서 — CLAUDE.md 상세

> `CLAUDE.md`는 모든 세션과 서브에이전트가 호출마다 싣는 파일이라 **지시만 짧게** 둔다(D-304). 이 문서는 2026-10-06에 `CLAUDE.md`에서 옮긴 근거·예시·세부 절차의 **원문**이다.
> 통째로 읽지 말고 필요한 절만 찾아 읽는다. 지시가 서로 다르면 `CLAUDE.md`의 현행 지시가 우선한다.

## §0 프로젝트 규모 (실측 2026-08-31)

규모(실측 2026-08-31): `src/` 약 68K LOC · `noise_gate/` 약 14K LOC · 테스트 360개 파일
(`tests/` 263 · `noise_gate/tests/` 68 · `sre_agent/tests/` 20 · `mcp_server/tests/` 9).

## §1 SDD 산출물 위치 (D-244)

### SDD 산출물 위치 — `spec/` (D-244)

스펙 단계 산출물은 **`spec/` 한 폴더에 평면으로**(하위 폴더 없이) 둔다. **루트에 만들지 않는다.**

| 산출물 | 위치 |
|---|---|
| 기능 맵 `CAPABILITY-MAP-<NN 또는 slug>.md` | `spec/` |
| 모듈 스펙 `SPEC-<module-id>.md` (모듈이 하나뿐인 단일 스펙 포함) | `spec/` |
| 구현 계획·태스크 `plan-*.md` · `todo-*.md` | `tasks/` (종전 그대로) |
| 원 요구사항 `spec.md` | 루트 (종전 그대로 — 보관한 `requirements-analyst`·`research-planner`와 `agents/run.py`가 루트 경로로 읽는다) |

- **agent-skills 기본값을 이 규칙으로 대체한다.** 원본 `spec-driven-development` 스킬은 맵과 스펙을
  "project root"에 쓰라고 한다. 프로젝트로 옮긴 사본(`.claude/skills/spec-driven-development/SKILL.md`)은
  저장 위치를 `spec/`로 고쳤다(2026-10-07 · D-312 부기). 플러그인을 끈 이 프로젝트에는 `/spec`·`/build` 명령이 없다.
- **루트 `SPEC.md` 생성 금지.** 개발 맥 파일시스템(APFS)은 대소문자를 구분하지 않아서 `SPEC.md`와
  원 요구사항 `spec.md`가 같은 파일이다(inode 동일 실측 2026-09-22). 루트에 쓰면 원 요구사항을 덮어쓴다.
- 새 문서에서 참조할 때는 `spec/SPEC-x.md`처럼 경로를 적는다. 2026-09-22 이전 문서가 파일명만 적은
  참조(`SPEC-x.md`)는 `spec/`에서 찾는다. 이력 기록이라 일괄 치환하지 않았다.

## §2 실행 경로 — 오케스트레이션 사다리 세부

### 실행 경로 — 오케스트레이션 사다리

**실행 경로 4종은 대등하게 병존하지 않는다. 위에서부터 성립하는 한 단만 확정되며, 기준 경로는
2단 `intent_orchestration`이다(D-251 — D-225의 3단 기준을 2026-09-23 개정). 3단 `semantic_router`는
2단 대비 성능을 재는 비교 arm이며 3단 기능 동등성(`plans/103`)은 계속 진행한다.**
단일 출처는 `docs/21_orchestration_ladder.md`이며, 판정 코드는 `src/observability/ladder.py`,
배선은 `src/graph.py`의 `build_graph()`다.

| 단 | 이름 | 배선 | 활성 조건(앞 단이 전부 불성립일 때) |
|---:|---|---|---|
| 1 (부가 경로 · opt-in) | `deep_agent` | `field_mapper → deep_agent → END` | `enable_deepagents_package` **AND** 오케스트레이터 가용 **AND** deepagents 조립 성공 |
| **2 (기준 경로)** | `intent_orchestration` | `field_mapper → intent_planner → agent_orchestrator → [replanner 루프] → result_aggregator → END` | `enable_intent_orchestration`(미입력 = **on** · D-251) |
| 3 (비교 arm) | `semantic_router` | `field_mapper → semantic_router → 조건부 분기` | `enable_semantic_routing` |
| 4 | `legacy` | `field_mapper → schema_analyzer` 직행 | 위 셋 모두 불성립 |

- **배타성은 런타임이 아니라 빌드 타임이다** — 상위 단이 성립하면 하위 단은 노드조차 등록되지
  않는다. 확정은 기동당 1회이며, 그 결과는 기동 로그 1줄(`record_ladder_resolution`)로만 판독된다.
- `enable_semantic_routing`·`enable_intent_orchestration`은 **tri-state**다. `enable_semantic_routing`
  미입력(None)은 `ACTIVE_DB_IDS` 등록 여부로 자동 결정되고 경고를 남긴다 — 실행 경로가 DB 등록
  상태에 종속되므로 고정하려면 `.env`에 명시한다. **`enable_intent_orchestration` 미입력은 항상
  on**이다(`resolved_by=code_default` · D-251 — 종전 D-225 ④ "항상 off"를 개정).
- 1단 확정은 강등이 아니라 **부가 경로 opt-in 기록(INFO)**이고, 3·4단 확정과 1단 opt-in 실패
  (`orchestrator_unavailable`·`package_missing`)가 WARNING이다(D-251). 종전 사유 어휘 `flag_off`는
  **D-225로 폐기**했다 — 2026-09-17 이전 로그·run 기록의 `flag_off`는 옛 어휘다.
- **"코드에 분기가 남아 있다"는 사실만으로 죽은 경로를 판정하지 말 것.** 어느 단을 지우려면 그
  단이 확정되는 설정 조합이 실제로 쓰이지 않음을 먼저 보여야 한다(D-161 · plans/70 v1 오판 사례).

## §3 LangGraph 노드 세부

### LangGraph 노드

공통 전단: `context_resolver → input_parser → field_mapper` (사다리 전 단 공통).
3단(semantic_router) 경로의 분기 대상은 `schema_analyzer`(단일 DB) · `multi_db_executor`(멀티 DB) ·
`cache_management` · `synonym_registrar` · `general_inference` · `fault_diagnosis`(옵트인) · `END`(역질문).

단일 DB 경로: `schema_analyzer → query_generator → query_validator →
[approval_gate] → query_executor → result_organizer → output_generator`

- `query_validator` 실패 → `query_generator` 회귀 (예산 `QUERY_MAX_RETRY_COUNT`, 기본 3)
- `query_executor` SQL 에러 → `query_generator` 회귀 (에러 컨텍스트 동반)
- `result_organizer` 데이터 부족 → `query_generator` 회귀
- HITL 게이트는 SQL 승인 1종(`approval_gate`, `enable_sql_approval` 기본 off)이며 `interrupt_before`로
  배선된다. **질의 경로는 DB 구조를 분석하지 않는다**(D-227) — 구조 분석·승인·버전은 관리자 「DB 구조」 탭
  (`src/api/routes/db_structure.py`)에서 하고, 구조 정보(수동 프로필·승인본)가 없는 DB는 멈추지 않고 사유를 알린다
- 노드 전체 목록은 `src/nodes/` 참조 — 후보 생성/선택, 단계적 컬럼 도출, 조건 프로브,
  실시간 사용률, 시맨틱 컴파일러 등 옵트인 노드가 다수 있다.
- **상태**는 `TypedDict`(`AgentState`, `src/state.py`). LangGraph 체크포인터는 **델타만 병합**하므로
  요청 스코프 상태는 라우트에서 명시 초기화한다(아래 Known Mistakes 참조).

## §4 설정과 정본 파일 세부

### 설정과 정본 파일

설정은 `pydantic-settings` 계층 구조다 — `AppConfig`(`src/config.py`, 23개 nested config)가
`.env`/`.encenv`에서 읽는다. 접근은 `cfg.<그룹>.<필드>` (예: `cfg.composite.max_targets`).

| 정본 | 내용 | 신규 편입 시 |
|---|---|---|
| `config/db_registry.yaml` | DB 등록·존·솔루션·실행 그룹·위치 표면어·제품군 | **신규 DB는 여기 + `.env` 둘만 수정** |
| `config/db_profiles/{db_id}.yaml` | 구조 정본 (테이블·EAV·컬럼) | |
| `config/knowledge/{db_id}/` | 큐레이션 지식(카탈로그 등) | |
| `config/semantic_models/{db_id}.yaml` | 시맨틱 모델 — **폴백 사본**(런타임은 profiles+knowledge에서 생성) | 동등성은 `scripts/catalog_diff.py` |
| `config/synonym_seeds/{db_id}.yaml` | 유사어 시드 | |
| `config/middleware_signatures.yaml` · `change_terms.yaml` | 미들웨어 식별 · 변경 용어 | |

- 운영 실측(`.env`, 2026-08-31): `LLM_PROVIDER=gemini` · `ORCHESTRATOR_PROVIDER=gemini` ·
  `DB_BACKEND=dbhub` · `ACTIVE_DB_IDS=polestar` · 사다리 1·2·3단 플래그 모두 true.
  **코드 기본값이 아니라 이 실제값을 근거로 판단할 것.** 세 플래그를 모두 명시하므로 운영은
  아직 1단으로 확정된다 — 기준 경로(2단)로의 운영 전환은 `ENABLE_DEEPAGENTS_PACKAGE=false`를
  사용자가 폐쇄망 `.env`에 반영하는 것이다(D-251 · 절차 `plans/110` 부록 B.4).
- 신규 기능 플래그는 **기본 off = 현행 동작과 비트 동일**이 원칙이다(`plans/80` §5.4-③).
  명시적 예외는 근거와 함께 config 주석에 남긴다(예: `COMPOSITE_AVAILABILITY_PRECHECK_ENABLED`,
  `COMPOSITE_HOST_DISCOVERY_ENABLED`, `COMPOSITE_SCOPE_SELECT_ENABLED`, D-203 순차 의존 계약 7종은 기본 on).
- 플래그는 **기동 시 1회 해석**한다 — 요청 시점에 바꾸면 프롬프트 접두가 흔들려 KV 캐시가 무효화된다.

## §5 문서 처리

### 문서 처리

- **Excel**: 헤더 행 자동 감지 → 데이터 행 채우기. 병합셀·수식·서식 보존. Excel→CSV→LLM→Excel
  파이프라인(`src/document/excel_csv_converter.py`)과 다중 헤더·월 피벗 폼필을 지원한다.
- **Word**: `{{placeholder}}` 및 표 구조 감지 후 스타일 보존 채우기.
- 양식 필드명 ↔ DB 컬럼 매핑은 LLM 의미 매핑 + 매핑 보고서(`mapping_report.py`) + 사용자 피드백.
- 스키마·조인이 고정된 쿼리(폼필 피벗 등)는 **코드가 runnable SQL을 직접 조립**하고 LLM을
  우회한다(실패 시에만 폴백) — LLM 비결정성 대응.
- 업로드 양식의 DRM 해제는 `src/infrastructure/drm/`(`DRM_*` 설정, `docs/22_drm_deployment_guide.md`).

## §6 매뉴얼 동반 정책 (D-255) 절차

### 매뉴얼 동반 정책 (D-255)

**사용자·관리자가 직접 쓰는 기능을 새로 만들거나 사용법·화면을 바꾸면, 같은 작업에서 매뉴얼을 함께 갱신한다.**
매뉴얼은 `src/static/manual/{user,admin}.html`(D-252)이며 원천은 `scripts/manual/`이다. 사용자 접점이 없는 변경
(내부 리팩터·성능·벤치·하네스·운영 스크립트)은 대상이 아니다.

1. `scripts/manual/features.yaml` 항목 추가 또는 `anchors` 갱신 (화면 버튼 없는 채팅·API 기능은 `no_ui`)
2. `scripts/manual/content/{user,admin}.md` 해당 절 5칸 작성·갱신. 사용자 절은 사례(`cases`) 또는 `case_na` 사유
3. 화면이 바뀌면 `captures.yaml` 갱신 → `python -m scripts.manual.run_capture --only <캡처ID>`(LLM 0 ·
   포트 18981·18982 점유 먼저 확인 · 자기 PID만 종료)
4. `python -m scripts.manual.build` → `pytest tests/test_manual` 통과
5. 완료 보고에 매뉴얼 반영 여부를 적는다. 못 한 부분은 사유와 잔여로 남긴다

역방향 가드(`test_reverse_every_button_and_tab_is_documented`)는 `<button id>`·탭 값만 잡는다. 링크·메뉴 항목·
채팅 명령·API는 가드가 모르므로 스스로 챙긴다. 가드를 `ignore`로 우회하지 말 것(사유 없는 우회 금지).

## §7 패키지 경계 (D-139) 세부

### 패키지 경계 — 기능별 최상위 폴더 (D-139)

기능 단위 코드는 **자기 최상위 패키지 폴더 안에서** 구현한다. 각 패키지는 자기 `tests/`·
`scripts/`·`testdata/`를 소유하며, 본체 `src/`는 text2sql 파이프라인과 조립(entry)만 남긴다.

| 패키지 | 담당 | 실행 형태 | 경계 |
|---|---|---|---|
| `src/` | text2sql 파이프라인·API 조립 | 본체 프로세스 | — |
| `noise_gate/` | 알람 노이즈 캔슬링·분석·통보 + TCP 수신부(`alarm_server/`) | 게이트·워커는 **본체와 같은 프로세스·같은 venv**, 수신부는 독립 프로세스 | `src/ → noise_gate` 의존 잔존(D-048 워커 in-process 기동). 역방향은 config/llm/utils/routing 최소 |
| `sre_agent/` | HolmesGPT 장애 조사 | 별도 venv·별도 프로세스 | 양방향 import 0 (MCP 계약만) |
| `mcp_server/` | 관측 데이터 읽기 경계 | 별도 프로세스·별도 cwd (**자체 venv 없음 — 루트 공유**) | 양방향 import 0 |
| `apm_gateway/` | 제니퍼 APM 연동 — Open API GET 허용목록 조회·`apm_*` 8종·WAS 판정(`was_signals`)·이벤트 폴러(`alarm:raw` 생산) · 제니퍼 뷰 서버 N개(소스 — 소스↔존은 루트 레지스트리 `solutions[apm].sources` · D-287) | 별도 프로세스·별도 cwd (**자체 venv 없음 — 루트 공유** · 제니퍼가 있는 환경에만 배포) | 양방향 import 0 (MCP · `alarm:raw` 계약만 — `tests/test_boundary.py`) · 제니퍼 토큰은 여기에만(D-274 · D-195) |

- **신규 기능은 소속 패키지 폴더에** 만들고, 본체 수정은 배선 최소로 한정한다.
- `noise_gate`는 **평탄 레이아웃**(디렉토리 자체가 패키지) — 2단 중첩은 루트에서 import가
  해석되지 않아 editable 설치에 의존하게 된다(D-139 실측). `sre_agent`·`mcp_server`는 **자체
  `pyproject.toml`·자체 cwd**를 가져 2단 중첩을 유지한다(`sre_agent`는 자체 venv도 보유 —
  본체 >=3.11 · holmesgpt 스택 >=3.13으로 요구 버전이 갈린다. `mcp_server`·`apm_gateway`는 >=3.11로 같아
  루트 venv를 공유한다). `apm_gateway`는 2단 중첩이라 `arch_check.py`가 모듈 이름을 해석하지 못해
  계층 방향을 자기 `tests/test_boundary.py`(AST)로 검사하고, `overfit_check.py`는 벤더 어댑터
  `adapters/jennifer/`만 빼고 스캔한다(`plans/87` G-11).
- 예외: `src/api/routes/alarm.py`는 알람 전용이지만 본체 앱 인증 계층에 묶여 `src/api/`에 남긴다
  (옮기면 `noise_gate → src.api` 역방향 결합 신설 — D-139 근거 참조)

## §8 Known Mistakes 핵심 원칙 — 근거 포함 원문 (2026-10-06 시점)

> 이 원문의 D-번호 관련 두 줄(「안내 라인 등재」·「세 곳 grep」)은 D-304로 대체됐다 — 채번은 `docs/02_decision.md` 색인 「쓰는 법」을 따른다.

### Known Mistakes 핵심 원칙

> 전체 실수 이력(50여 건, 원인·방지책 상세)은 `docs/18_known_mistakes.md` 참조. 아래는 반복 실수에서 추출한 예방 원칙 요약.

**과금 외부 API 승인 게이트 · 실 LLM 테스트는 로컬 MLX 기준 (D-127 · D-240 개정 — 2026-09-21 사용자 정책)**
- **실 LLM이 필요한 테스트·스모크·검증은 로컬 MLX로 진행한다** — 워커·오케스트레이터 두 평면이 모두 `mlx`(127.0.0.1 루프백 `mlx_lm.server`)면 비과금이라(D-222) **사용자 승인 없이** 에이전트가 실행한다
  - 실행 전 두 평면이 모두 `mlx`로 해석되는지 설정 해석 출력(`python -m scripts.bench --show-env` · `--preflight`)으로 확인한다. 하나라도 과금 평면(gemini 등)이면 실행하지 않는다(`.encenv`에 Gemini 키가 상존한다)
  - 진입점: pytest `live_llm`은 `RUN_LOCAL_LLM=1`(외부 차단 가드 유지 — 과금 호출은 구조적으로 나가지 않고 시도하면 차단 실패로 드러난다) · 시나리오 `--run`·벤치 `--mode run`은 두 평면이 비과금이면 이미 승인·`RUN_E2E` 없이 돈다(D-216 · D-222). `scripts/eval_routing.py`도 두 평면이 `mlx` 루프백이면 `RUN_E2E` 없이 돈다(`local_mlx_mode()` · D-240 부기) — 하나라도 과금 평면이면 종전대로 `RUN_E2E=1` 과 건별 승인이 필요하다
  - MLX 서버는 캐시 모델로만 기동(다운로드 금지)·127.0.0.1 바인딩·자기가 띄운 PID만 종료한다. MLX 결과는 로직 확인용이다 — 성능(지연) 결론은 내부망 결과로만 낸다
  - **MLX 검증은 최소로 계획한다**(2026-10-06 사용자 — *"MLX는 속도가 느리다"*). 기본 검증은 가짜 LLM·목 API·실프로세스 종단이고, MLX는 그걸로 증명 못 하는 것(바뀐 프롬프트·선택 경로가 실 모델에서 도는지)만 대표 문항 소수로 1회 스모크한다. 계획서에 MLX 문항 수·예상 소요를 적고, Wave마다 골드 전수 재측정·반복 실행을 넣지 않는다. 선택 정확도·지연은 내부망 FabriX 측정 잔여로 남긴다
- Gemini 등 **과금이 발생하는 외부 API는 사용자의 명시 승인 없이 호출 금지** — 실행 건마다 승인을 받는다(포괄 승인 없음). 가드를 끄는 `RUN_E2E=1`은 그 승인 뒤에만 설정한다
- 실 호출 경로는 전부 옵트인(`RUN_LOCAL_LLM=1` · `RUN_E2E=1`) 뒤에 두고, **키 존재만으로 실행되는 게이팅 금지**(키는 `.encenv`에 상존한다는 전제) — 수동 스크립트도 코드 게이트로 강제

**실측 우선 (추정 금지)**
- 외부 패키지 API는 `inspect.signature()`로 실제 시그니처 실측 후 사용 — 계획서 의사코드를 신뢰하지 말 것
- 코드/UI "부재"를 단정하기 전 워드 경계 grep(`-w`)·전수 확인으로 실측. 구현·설정이 있어도 호출부 배선까지 grep으로 확인(정의만 있으면 무효)
- 결정적 게이트가 의존하는 데이터는 실 런타임 shape로 검증 — mock 통과 ≠ 프로덕션 동작(로더가 구조를 변형할 수 있음)
- 0건/실패 진단은 안쪽 단계부터 추정 수정하지 말고 진입·게이트별 로그로 끊긴 지점부터 확정(증상보다 라우팅 먼저). 필드 null은 데이터 부재가 아니라 생성 SQL 오류일 수 있음
- **경로·모듈 폐기 제안은 D-161 ② 4항 실측 첨부 필수** — ①`.env` 운영 실제값(코드 기본값 아님) ②관련 패키지의 실 설치·서빙 상태 ③대상 파일 `git log` 최종 수정일(**`--all` 사용 시 `git merge-base --is-ancestor`로 현 브랜치 소속 확인**) ④역방향 import(다른 경로가 이 모듈을 재사용하는지). 하나라도 누락된 폐기 제안은 반려한다 — "죽은 경로처럼 보이는 것"과 "실제로 죽은 경로"는 정적 읽기로 구별되지 않는다
- **D-번호 예약은 `docs/02_decision.md` 안내 라인에 등재해야 효력이 있다** — 계획서에만 적은 예약은 채번 grep 대상이 아니라 소진된다(D-161 부기)

**pydantic-settings / .env**
- `.env`의 list/dict 필드는 JSON 배열 형식(`["a","b"]`)으로 작성
- `env_file` 로딩은 `os.environ`에 주입되지 않음 → `os.getenv()`로 설정값·설정 유무 판단 금지(pydantic 필드/`AliasChoices`로 판정)
- `.env` 계열 파일에 인라인 주석 금지(주석은 별도 줄, 특히 빈 값 뒤 금지)
- BaseSettings nested 필드는 `Field(default_factory=...)`로 선언(임포트 시점 고정 방지). 테스트 config는 검증 대상 필드를 명시해 `.env` 누수 차단

**LLM 비결정성 대응**
- LLM 분류·매핑·alias·방언 출력에 정합성을 의존하지 말 것 — 결정적 가드로 후처리 교정하고, 스키마·조인이 고정된 쿼리(폼필 피벗 등)는 코드가 runnable SQL을 직접 조립(LLM 우회, 실패 시에만 폴백)
- 프롬프트 강제가 프로필 few-shot 예시와 경쟁해 반복 실패하면 그 쿼리 형태는 결정적 조립 대상
- 금지 규칙(negative instruction)은 범위를 좁게 못 박고 유지해야 할 정상 동작을 명시 재확인
- LLM 자동 등록(유사어 등)은 오염 자기강화 루프 위험 — 출력 교정만으론 부족, 쓰기(등록) 지점에서 결정적 차단

**단일/멀티 경로 대칭 · 멀티 엔진 방언**
- 프롬프트 블록·스키마 메타(`_structure_meta`)·엔진/스키마 규칙은 단일 DB·멀티 DB 경로 **양쪽에 실제 주입됐는지 실측**(한쪽만 고치는 비대칭이 반복 원인)
- PostgreSQL/DB2 방언 분기 필수: LIMIT vs FETCH FIRST, `::numeric` vs `CAST(… AS DECIMAL)`(반드시 집계 **전** 캐스트), DB2 결과 칼럼 라틴 소문자화, 스키마 한정(대문자 POLESTAR)
  - **단, EAV 숫자 속성은 양 엔진 모두 `CAST(… AS NUMERIC)`을 쓴다**(2026-09-16 사용자 확정 G-7). 폐쇄망 실측(`config/db_profiles/polestar_cm_yd.yaml:436`, 2026-08-21)이 *"DB2에서 `CAST(… AS NUMERIC)`만 유효, INT/BIGINT는 `'4.0'` 파싱 오류"*로 기록하고, DB2가 NUMERIC을 DECIMAL 동의어로 수용한다. 시나리오 단언(B-07·B-08 `sql_must_match: (?i)\bnumeric\b`)도 이 형태를 요구한다 — **`AS DECIMAL`을 강제하면 그 단언이 깨진다.** 위 `DECIMAL` 표기는 일반 수치 캐스트에 대한 것이고, 충돌하면 실측이 이긴다
- 새 DB 편입 체크리스트: ①위치 힌트(`_LOCATION_DB_HINTS`) ②런타임 `.env` base_url ③엔진 방언 ④스키마 한정(db_schema)

**멀티턴 / 상태 관리**
- LangGraph 체크포인터는 델타만 병합 — 요청 스코프 상태(uploaded_file, 매핑 산출물 등)는 라우트에서 명시 초기화하고 노드 스킵 경로는 자기정리
- 승계 신호(hostname/db_id)는 top-level 승격 경로가 대칭인지 + 이번 턴 파싱이 승계값을 덮어쓰지 않는지(우선순위) 확인. 지시어("해당/그 서버")는 식별자가 아님 — previous_entities로 폴백
- 멀티턴 검증은 요청 본문에 thread_id가 실제로 실리는지 프론트까지 확인

**폴백 · 에러 처리**
- 침묵적 폴백/강등 금지 — 산출물 생성 실패는 사유를 구조화해 사용자 응답에 노출, 예외 삼키는 폴백은 실패 SQL·컨텍스트를 로그로 가시화
- 독립 신호 수집은 개별 try/except로 부분 반환 보장(한 try 블록에 묶지 말 것)
- 장시간 실행 경로(SSE 스트리밍 등)는 전체 타임아웃 가드 필수(per-call 타임아웃만으론 무력화됨)
- 데몬류 in-memory dict는 값 bound뿐 아니라 키 만료 sweep도 추가

**보안 · 인가**
- 토큰 서명 검증만으로 끝내지 말고 `type`·role 클레임을 명시 검증(UI 게이트 ≠ 인가). 사용자/운영자 시크릿 분리
- 인증 UX는 "로그인 안 한 첫 방문자" 경로를 실제로 밟아 확인

**테스트 · 작업 절차**
- 대량 테스트 실패는 원인별 분류부터(`--tb=line` 후 유형 카운트) — 한 유형이 지배적이면 단일 오염원 의심. e2e는 `RUN_E2E=1` 옵트인
- 결정적 상수·매트릭스 값 변경 시 그 값을 단언하는 테스트를 repo 전체 grep으로 일괄 갱신. 기존 테스트가 버그를 정답으로 굳혔는지도 점검
- 클린 기준선 검증은 `git stash`가 아니라 `git worktree add <dir> HEAD`(격리 사본)
- 신규 D-번호는 `docs/02_decision.md`의 `## D-` 헤더·「변경 이력」 표·「채번 이력」 표를 모두 grep해 실제 최댓값+1 부여(예약은 「채번 이력」 표에 등재해야 효력)
- 산출물 검증은 미리보기 일부가 아니라 실제 산출 파일의 전 칼럼 확인

## §9 개발 명령 전체 (2026-10-07 CLAUDE.md에서 이관 · D-312)

> CLAUDE.md에는 자주 쓰는 명령만 남겼다. 아래는 이관 직전 원문이다.


```bash
# 설치 (uv 또는 pip)
pip install -e ".[dev,document]"

# 본체 서버 (FastAPI + 웹 UI + AlarmWorker in-process 기동)
python -m src.main --server
# 단일 질의 CLI / 대화형 CLI
python -m src.main --query "김포 서버 CPU 사용률 상위 10건"
python -m src.main

# 알람 수신부 (독립 프로세스, TCP 9100 → Redis Stream 'alarm:raw')
python -m noise_gate.alarm_server

# MLX 로컬 LLM 서버 (맥북 테스트 전용 · 루트 venv 밖 설치 · 127.0.0.1 바인딩 필수 — docs/03_setup_guide.md §7.2)
# .env: LLM_PROVIDER=mlx · ORCHESTRATOR_PROVIDER=mlx · LLM_MLX_MODEL/ORCHESTRATOR_MODEL=서버 모델 ID · *_BASE_URL=http://127.0.0.1:8080/v1
# 설치(선택): uv tool install "mlx-lm==0.31.3" — 없으면 스크립트가 uvx로 실행한다
scripts/mlx_server.sh                    # .env 모델·포트로 기동(포그라운드) · --dry-run 점검만 · MLX_ALLOW_DOWNLOAD=1 캐시 외 모델

# MCP 서버 (별도 프로세스·별도 cwd — 자체 venv 없음, 루트 venv로 기동)
# DB2(polestar_b0) 조회에는 ibm-db가 필요한데 루트 venv에는 미설치다(실측 2026-09-02).
# mcp_server/pyproject.toml에만 선언돼 있으므로 DB2 대상 기동 전 설치 여부를 확인할 것.
cd mcp_server && python -m mcp_server

# APM 게이트웨이 (제니퍼 · 별도 프로세스·별도 cwd · 루트 venv) — 설정은 apm_gateway/.env(.env.example 참고)
cd apm_gateway && ../.venv/bin/python -m apm_gateway

# 테스트 — 구현·교정 뒤 기본은 모듈 단위 회귀(아래 「회귀 테스트 정책」 · D-303)
python scripts/regress.py --base <세션 시작 SHA>   # 모듈 단위 · 병렬 · 정적 게이트 · 실패 귀속
python scripts/regress.py --full         # 전 패키지 전체 — 사용자가 요청할 때만
pytest                                   # 본체 + noise_gate 자동 수집(전체 · 직렬 — 사용자 요청 시만)
pytest tests/test_graph.py -v
cd sre_agent && .venv/bin/python -m pytest tests -q   # 자체 venv 보유
cd mcp_server && ../.venv/bin/python -m pytest       # 자체 venv 없음 — 루트 venv 사용
cd apm_gateway && ../.venv/bin/python -m pytest -q    # 루트 수집 밖 · 자체 venv 없음 — 루트 venv 사용

# 품질 게이트
python scripts/arch_check.py --ci        # 계층 의존성 
python scripts/overfit_check.py --ci     # 공용 계층 스키마 리터럴 누수 
ruff check src/ tests/ && mypy src/

# 평가 하네스 (실 파이프라인 구동 — 과금 경로다. 먼저 --dry-run/--mock으로 확인할 것)
python scripts/eval_text2sql.py --dry-run
python scripts/eval_routing.py --help

# ITAM 질의 벤치 (plans/135 · D-301 — 사용자 프롬프트 시나리오 · 로그 위생·누출 관문 · LLM 0인 두 모드부터)
python -m scripts.itam_bench --dry-run          # 시나리오·프롬프트 린트·실행 계획만
python -m scripts.itam_bench --check-oracle     # 정답 SQL만 읽기 전용 실행 (ITAM 샌드박스 3307 + MCP 9099 필요)
python -m scripts.itam_bench --run              # 벤치 서버로 사용자 경로 실행 — 두 평면 비과금일 때만(과금 평면은 거부)
```

**실 LLM 테스트는 로컬 MLX로 돌린다(D-240).** `tests/conftest.py`가 전역 네트워크 가드(공인 IP 차단)를
설치하고 `live_llm` 마커를 자동 skip한다. `RUN_LOCAL_LLM=1`은 `live_llm`을 돌리되 **가드를 유지**한다 —
두 평면이 `mlx`(루프백)면 승인 없이 쓴다. 가드를 끄는 `RUN_E2E=1`(외부 허용)은 건별 사용자 승인 사항이다(D-127).

```bash
RUN_LOCAL_LLM=1 pytest tests/test_pipeline.py -m live_llm   # 로컬 MLX 실 LLM 테스트 (외부 차단 유지)
```

## §10 회귀 테스트 정책 원문 (D-303 · 2026-10-07 이관 · D-312)

> CLAUDE.md에는 에이전트가 지킬 행동만 남겼다. 선택 규칙·권고 조건·병렬·실패 귀속 세부는 아래 원문과 `plans/136`·`scripts/regress.py --help`가 정본이다.


구현·교정 뒤 회귀는 **바꾼 모듈 단위**로 돌린다. **전체 회귀는 사용자가 요청할 때만** 돌린다. 도구는 `scripts/regress.py`이고 근거·실측은 `plans/136`에 있다.

```bash
python scripts/regress.py --base <세션 시작 SHA>                 # 기본 — 모듈 단위 · 병렬 · 정적 게이트 · 실패 귀속
python scripts/regress.py --base <SHA> --files <내가 바꾼 파일…>  # 병행 세션 변경을 빼고 내 파일만 기준으로
python scripts/regress.py --plan                                  # 무엇을 돌릴지 목록만(실행 안 함)
python scripts/regress.py --wide                                  # 1단계 확장(공개 시그니처를 바꾸면 자동 적용)
python scripts/regress.py --full                                  # 사용자가 요청할 때만 — 전 패키지 전체 · 병렬
```

`--base`는 세션 시작 SHA로 준다. 생략하면 `HEAD` 기준이라 병행 세션이 커밋했으면 범위가 틀린다(도구가 경고한다).

1. **선택** — 다음을 합쳐 돌린다. 결과는 모듈별 표로 나온다.
   - 바꾼 테스트 파일
   - 바꾼 Python 모듈을 **직접 import**하는 테스트. 함수 안의 import와 `patch("src.x.y")` 같은 문자열 모듈 경로도 포함한다
   - 바꾼 비Python 파일의 경로·파일명을 적은 테스트
   - 바꾼 독립 패키지(apm_gateway·mcp_server·sre_agent)의 테스트 전체
   - 저장소 전역 가드(`@pytest.mark.repo_guard` — 소스 트리 전체를 훑는 테스트)
2. **전체는 요청 시만** — 계획 완료·Wave 종료·커밋 전에도 자동으로 돌리지 않는다. 도구가 출력 끝에 `[전체 회귀 권고]` 블록을 내면 **그 블록을 사용자 보고에 그대로 옮기고**, 전체 회귀는 스스로 돌리지 않는다. 권고는 실패가 아니라 종료 코드에 영향이 없다. 권고 조건(결정적):
   - 테스트 기반 파일 변경 — `pyproject.toml`·`uv.lock`·pytest 설정·루트 conftest
   - 허브 모듈 변경 — 전이 import가 테스트의 85% 이상에 닿는 모듈(예: `src/config.py`)
   - 공개 함수·클래스의 시그니처·반환 형태를 깨는 변경 — 이때는 `--wide`도 자동 적용된다
   - Python 모듈 이동·삭제
   - 직접 import 선택이 테스트의 40% 이상
3. **병렬** — 본체·apm_gateway는 `pytest-xdist` 워커(합계 최대 8), mcp_server·sre_agent는 별도 프로세스로 동시에 돈다. 선택이 100건(정적 테스트 함수 수) 미만이면 직렬이다. 병렬로 못 도는 테스트만 `serial` 마커로 직렬 분리하되, 원인(전역 상태 미원복 등) 교정이 먼저다. `addopts`에 `-n`을 넣지 않는다.
4. **정적 게이트는 매번** — 도구가 `arch_check --ci`·`overfit_check --ci`를 함께 돌린다. ruff·mypy는 바꾼 파일만 검사하고, 이번 diff 줄에 걸린 위반만 신규로 센다.
5. **실패분만 재대조** — 도구가 실패 ID만 `--base` 커밋의 격리 worktree(`.env` 계열 심링크 · `PYTHONPATH`=사본)에서 다시 돌려 「원래 실패 / 이번 변경 탓 / 새 테스트 실패」로 가른다. 이번 변경 탓·새 테스트 실패·판정 불가·정적 게이트 실패·TIMEOUT이 있으면 종료 코드 1, 원래 실패뿐이면 0이다. 실패 ID 목록 등 산출물은 `logs/regress/<시각>-<pid>/`에 남는다. 교정 라운드에서는 실패했던 테스트와 그 모듈 선택분만 다시 돌린다.
6. **보고에 범위를 적는다** — 도구 출력 마지막 줄(`범위: 모듈 단위 — 전체 미실행`)을 옮긴다. 예: *"모듈 단위 회귀 — 대상 모듈 3 · 412건 통과 · 정적 게이트 통과 · 전체 미실행"*. 모듈 단위 결과를 전체 무회귀처럼 쓰지 않는다.

**도구를 쓸 수 없을 때(대체 절차)** — 바꾼 모듈마다 아래 grep으로 직접 import하는 테스트를 찾아 바꾼 테스트와 함께 `pytest` 한 번으로 돌리고, 정적 게이트를 따로 돌린다. 위 2의 권고 조건을 손으로 판단해 하나라도 걸리면 같은 형식의 `[전체 회귀 권고]` 블록을 보고에 적는다.

```bash
grep -rlE "src\.<패키지>\.<모듈>\b|from src\.<패키지> import [^#]*\b<모듈>\b" tests noise_gate/tests
```

## §11 Known Mistakes 핵심 원칙 — 2026-10-07 축약 직전 CLAUDE.md 원문 (D-312)

> §8(2026-10-06 원문)을 한 번 줄인 판이다. CLAUDE.md에는 이것을 다시 한 줄 규칙으로 줄여 두었다.


> 전체 이력은 `docs/18_known_mistakes.md`(grep으로 조회). 아래는 반복 실수에서 추출한 예방 원칙 요약이다. 근거·사례를 붙인 원문은 `docs/34` §8.

**과금 외부 API 승인 게이트 · 실 LLM 테스트는 로컬 MLX 기준 (D-127 · D-240 개정 — 2026-09-21 사용자 정책)**
- **실 LLM이 필요한 테스트·스모크·검증은 로컬 MLX로 진행한다** — 워커·오케스트레이터 두 평면이 모두 `mlx`(127.0.0.1 루프백 `mlx_lm.server`)면 비과금이라(D-222) **사용자 승인 없이** 에이전트가 실행한다
  - 실행 전 두 평면이 모두 `mlx`로 해석되는지 설정 해석 출력(`python -m scripts.bench --show-env` · `--preflight`)으로 확인한다. 하나라도 과금 평면(gemini 등)이면 실행하지 않는다(`.encenv`에 Gemini 키가 상존한다)
  - 진입점: pytest `live_llm`은 `RUN_LOCAL_LLM=1`(외부 차단 가드 유지) · 시나리오 `--run`·벤치 `--mode run`·`scripts/eval_routing.py`는 두 평면이 `mlx` 루프백이면 승인·`RUN_E2E` 없이 돈다(D-216 · D-222 · D-240 부기) — 하나라도 과금 평면이면 종전대로 `RUN_E2E=1`과 건별 승인이 필요하다
  - MLX 서버는 캐시 모델로만 기동(다운로드 금지)·127.0.0.1 바인딩·자기가 띄운 PID만 종료한다. MLX 결과는 로직 확인용이다 — 성능(지연) 결론은 내부망 결과로만 낸다
  - **MLX 검증은 최소로 계획한다**(2026-10-06 — *"MLX는 속도가 느리다"*). 기본 검증은 가짜 LLM·목 API·실프로세스 종단이고, MLX는 바뀐 프롬프트·선택 경로만 대표 문항 소수로 1회 스모크한다. 계획서에 MLX 문항 수·예상 소요를 적고, Wave마다 골드 전수 재측정·반복 실행을 넣지 않는다
- Gemini 등 **과금이 발생하는 외부 API는 사용자의 명시 승인 없이 호출 금지** — 실행 건마다 승인을 받는다(포괄 승인 없음). 가드를 끄는 `RUN_E2E=1`은 그 승인 뒤에만 설정한다
- 실 호출 경로는 전부 옵트인(`RUN_LOCAL_LLM=1` · `RUN_E2E=1`) 뒤에 두고, **키 존재만으로 실행되는 게이팅 금지**(키는 `.encenv`에 상존한다는 전제) — 수동 스크립트도 코드 게이트로 강제

**실측 우선 (추정 금지)**
- 외부 패키지 API는 `inspect.signature()`로 실제 시그니처 실측 후 사용 — 계획서 의사코드를 신뢰하지 말 것
- 코드/UI "부재"를 단정하기 전 워드 경계 grep(`-w`)·전수 확인으로 실측. 구현·설정이 있어도 호출부 배선까지 grep으로 확인(정의만 있으면 무효)
- 결정적 게이트가 의존하는 데이터는 실 런타임 shape로 검증 — mock 통과 ≠ 프로덕션 동작(로더가 구조를 변형할 수 있음)
- 0건/실패 진단은 안쪽 단계부터 추정 수정하지 말고 진입·게이트별 로그로 끊긴 지점부터 확정(증상보다 라우팅 먼저). 필드 null은 데이터 부재가 아니라 생성 SQL 오류일 수 있음
- **경로·모듈 폐기 제안은 D-161 ② 4항 실측 첨부 필수** — ①`.env` 운영 실제값 ②관련 패키지의 실 설치·서빙 상태 ③대상 파일 `git log` 최종 수정일(`--all`이면 `git merge-base --is-ancestor`로 현 브랜치 소속 확인) ④역방향 import. 하나라도 누락된 폐기 제안은 반려한다
- **D-번호 예약은 `docs/02_decision.md` 색인 표에 `예약` 행을 넣어야 효력이 있다** — 계획서에만 적은 예약은 소진된다(D-161 · D-304)
- 도구·모델 동작(컨텍스트 창·설정 키 등)을 권고하기 전에 설치된 실물·문서로 실측한다 — 이름만 보고 추정하지 않는다(2026-10-06 사례: `docs/18`)

**pydantic-settings / .env**
- `.env`의 list/dict 필드는 JSON 배열 형식(`["a","b"]`)으로 작성
- `env_file` 로딩은 `os.environ`에 주입되지 않음 → `os.getenv()`로 설정값·설정 유무 판단 금지(pydantic 필드/`AliasChoices`로 판정)
- `.env` 계열 파일에 인라인 주석 금지(주석은 별도 줄, 특히 빈 값 뒤 금지)
- BaseSettings nested 필드는 `Field(default_factory=...)`로 선언(임포트 시점 고정 방지). 테스트 config는 검증 대상 필드를 명시해 `.env` 누수 차단

**LLM 비결정성 대응**
- LLM 분류·매핑·alias·방언 출력에 정합성을 의존하지 말 것 — 결정적 가드로 후처리 교정하고, 스키마·조인이 고정된 쿼리(폼필 피벗 등)는 코드가 runnable SQL을 직접 조립(LLM 우회, 실패 시에만 폴백)
- 프롬프트 강제가 프로필 few-shot 예시와 경쟁해 반복 실패하면 그 쿼리 형태는 결정적 조립 대상
- 금지 규칙(negative instruction)은 범위를 좁게 못 박고 유지해야 할 정상 동작을 명시 재확인
- LLM 자동 등록(유사어 등)은 오염 자기강화 루프 위험 — 출력 교정만으론 부족, 쓰기(등록) 지점에서 결정적 차단

**단일/멀티 경로 대칭 · 멀티 엔진 방언**
- 프롬프트 블록·스키마 메타(`_structure_meta`)·엔진/스키마 규칙은 단일 DB·멀티 DB 경로 **양쪽에 실제 주입됐는지 실측**(한쪽만 고치는 비대칭이 반복 원인)
- PostgreSQL/DB2 방언 분기 필수: LIMIT vs FETCH FIRST, `::numeric` vs `CAST(… AS DECIMAL)`(반드시 집계 **전** 캐스트), DB2 결과 칼럼 라틴 소문자화, 스키마 한정(대문자 POLESTAR)
  - **단, EAV 숫자 속성은 양 엔진 모두 `CAST(… AS NUMERIC)`을 쓴다**(2026-09-16 사용자 확정 G-7) — `AS DECIMAL`을 강제하면 시나리오 단언(B-07·B-08)이 깨진다. 충돌하면 실측이 이긴다(근거: `docs/34` §8)
- 새 DB 편입 체크리스트: ①위치 힌트(`_LOCATION_DB_HINTS`) ②런타임 `.env` base_url ③엔진 방언 ④스키마 한정(db_schema)

**멀티턴 / 상태 관리**
- LangGraph 체크포인터는 델타만 병합 — 요청 스코프 상태(uploaded_file, 매핑 산출물 등)는 라우트에서 명시 초기화하고 노드 스킵 경로는 자기정리
- 승계 신호(hostname/db_id)는 top-level 승격 경로가 대칭인지 + 이번 턴 파싱이 승계값을 덮어쓰지 않는지(우선순위) 확인. 지시어("해당/그 서버")는 식별자가 아님 — previous_entities로 폴백
- 멀티턴 검증은 요청 본문에 thread_id가 실제로 실리는지 프론트까지 확인

**폴백 · 에러 처리**
- 침묵적 폴백/강등 금지 — 산출물 생성 실패는 사유를 구조화해 사용자 응답에 노출, 예외 삼키는 폴백은 실패 SQL·컨텍스트를 로그로 가시화
- 독립 신호 수집은 개별 try/except로 부분 반환 보장(한 try 블록에 묶지 말 것)
- 장시간 실행 경로(SSE 스트리밍 등)는 전체 타임아웃 가드 필수(per-call 타임아웃만으론 무력화됨)
- 데몬류 in-memory dict는 값 bound뿐 아니라 키 만료 sweep도 추가

**보안 · 인가**
- 토큰 서명 검증만으로 끝내지 말고 `type`·role 클레임을 명시 검증(UI 게이트 ≠ 인가). 사용자/운영자 시크릿 분리
- 인증 UX는 "로그인 안 한 첫 방문자" 경로를 실제로 밟아 확인

**테스트 · 작업 절차**
- 대량 테스트 실패는 원인별 분류부터(`--tb=line` 후 유형 카운트) — 한 유형이 지배적이면 단일 오염원 의심. e2e는 `RUN_E2E=1` 옵트인
- 결정적 상수·매트릭스 값 변경 시 그 값을 단언하는 테스트를 repo 전체 grep으로 일괄 갱신. 기존 테스트가 버그를 정답으로 굳혔는지도 점검
- 클린 기준선 검증은 `git stash`가 아니라 `git worktree add <dir> HEAD`(격리 사본)
- 신규 D-번호는 `docs/02_decision.md` 색인 표(예약 행 포함)의 최댓값+1 — 등재 직전 `ls docs/decisions/ | tail -3`으로 병행 세션 선점을 확인한다(D-304)
- 산출물 검증은 미리보기 일부가 아니라 실제 산출 파일의 전 칼럼 확인
- 큰 문서(`docs/02_decision.md` 색인 · `docs/decisions/` · `docs/18` · `plans/INDEX.md`)는 통째로 읽지 않는다 — grep으로 좁힌 뒤 해당 항목만 읽는다(D-304)

## §12 품질 게이트 위반 대처 (2026-10-07 · 보관한 `.claude/skills/` 2종의 현행 요지 · D-312)

`.claude/skills/arch-check.md`·`overfit-check.md`는 Claude Code가 읽는 형식(`.claude/skills/<이름>/SKILL.md`)이 아니라 한 번도 로드되지 않았다. 원문은 `.claude/archive/skills/`에 있다. 게이트는 스크립트로 직접 돌린다.

**`arch_check.py`** — `--ci`(위반 시 exit 1) · `--verbose`(의존성 매트릭스) · `--json`. 계층 매핑 정본은 스크립트의 `MODULE_LAYER_MAP`이다(CLAUDE.md 「Clean Architecture 계층 규칙」). 위반 수정 패턴:
- **A. 함수 이동** — 하위 계층이 상위 계층 함수를 import하면 그 함수를 `utils/`나 같은 계층 모듈로 옮긴다.
- **B. 의존성 역전** — 하위 계층에 `Protocol`을 두고 상위 계층이 구현한다.
- **C. 콜백 주입** — 상위 계층 로직을 함수·팩토리 인자로 넘긴다.
- 노드(`src/nodes/*`)끼리 직접 import하지 않는다. 노드는 `src/graph.py`를 import하지 않는다.

**`overfit_check.py`** — `--ci`(게이트 대상 신규 유입 시 exit 1) · `--verbose` · `--json`. 카테고리 `schema-literal`(테이블·컬럼·리소스타입)과 `ops-literal`(운영 도메인·사설 IP)은 게이트 대상, `routing-vocab`(위치·별칭 어휘)은 집계만 한다. 신규 유입이면 리터럴을 어댑터(`src/db_adapters/`)·프로필·레지스트리로 옮기고 운영 주소는 `.env`로 옮긴다. 기준선 `scripts/overfit_baseline.json`은 **`--update-baseline`으로 전면 재생성하지 않는다** — 자기 델타만 소거한다(CLAUDE.md 「품질 게이트」). 스캔 대상에 `noise_gate/domain`·`mcp_server/mcp_server`가 포함되므로 독스트링 리터럴도 걸린다.
