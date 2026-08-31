# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

자연어(한국어) 질의로 인프라 관측 데이터를 조회·분석하는 에이전트 플랫폼. 세 축으로 구성된다.

| 축 | 담당 | 진입 |
|---|---|---|
| **text2sql 파이프라인** | 자연어 → SQL/REST/MCP 조회 → 자연어 응답 또는 Excel/Word 양식 산출 | `src/` (LangGraph) |
| **알람 노이즈 캔슬링** | 폴스타 알람 수신·중복/상관 억제·분석·통보 | `noise_gate/` |
| **장애 조사** | HolmesGPT 기반 조사 위임 | `sre_agent/` (별도 프로세스) |

관측 데이터 읽기 경계는 `mcp_server/`(FastMCP)가 담당한다 — DB SQL 실행·폴스타 도구·PromQL.

규모(실측 2026-08-31): `src/` 약 68K LOC · `noise_gate/` 약 14K LOC · 테스트 360개 파일
(`tests/` 263 · `noise_gate/tests/` 68 · `sre_agent/tests/` 20 · `mcp_server/tests/` 9).

- 원 요구사항: `spec.md` (초기 스펙 — 현 구현은 이보다 훨씬 확장됨)
- **의사결정 정본: `docs/02_decision.md`** — 작업 전 필독, 작업 후 갱신 (아래 「의사결정 기록」 참조)
- 계획서 전건 인덱스: `plans/INDEX.md` (91건) · 실행 경로 단일 출처: `docs/21_orchestration_ladder.md`
- 최근 작업 단위는 `plans/NN-*.md` + 루트 `SPEC-*.md` + `CAPABILITY-MAP-*.md` 조합으로 진행된다.

## 저장소 지도

```
src/            text2sql 파이프라인 · FastAPI 앱 조립 · 웹 UI(static)
noise_gate/     알람 노이즈 게이트 (본체와 같은 프로세스) + alarm_server(독립 프로세스)
sre_agent/      HolmesGPT 장애 조사 (별도 venv·별도 프로세스)
mcp_server/     관측 데이터 읽기 MCP 서버 (별도 venv·별도 프로세스)
config/         런타임 정본 YAML (DB 레지스트리·프로필·시맨틱 모델·지식·유사어 시드)
docs/           설계·가이드·의사결정(02)·실수 이력(18)·사다리(21)
plans/          영역별 구현 계획서 (INDEX.md가 전건 인덱스)
scripts/        품질 게이트(arch_check·overfit_check)·평가(eval_*)·운영 CLI
tests/          본체 테스트 (pytest가 noise_gate/tests와 함께 수집)
testdata/       픽스처·골드셋(text2sql_gold·routing_gold·pg init·prometheus)
db/ db2/ redis/ 로컬 개발용 docker-compose (PostgreSQL·DB2·Redis)
tools/          부속 도구 (drm-wrapper·migdata·redis_migration)
agents/         Claude Agent SDK 실행 스크립트 (멀티에이전트 빌드)
```

## 실행 경로 — 오케스트레이션 사다리

**실행 경로 4종은 대등하게 병존하지 않는다. 1 정본 + 3 폴백의 강등 사다리다.**
단일 출처는 `docs/21_orchestration_ladder.md`이며, 판정 코드는 `src/observability/ladder.py`,
배선은 `src/graph.py`의 `build_graph()`다.

| 단 | 이름 | 배선 | 활성 조건(앞 단이 전부 불성립일 때) |
|---:|---|---|---|
| **1 (정본)** | `deep_agent` | `field_mapper → deep_agent → END` | `enable_deepagents_package` **AND** 오케스트레이터 가용 **AND** deepagents 조립 성공 |
| 2 | `intent_orchestration` | `field_mapper → intent_planner → agent_orchestrator → [replanner 루프] → result_aggregator → END` | `enable_intent_orchestration` |
| 3 | `semantic_router` | `field_mapper → semantic_router → 조건부 분기` | `enable_semantic_routing` |
| 4 | `legacy` | `field_mapper → schema_analyzer` 직행 | 위 셋 모두 불성립 |

- **배타성은 런타임이 아니라 빌드 타임이다** — 상위 단이 성립하면 하위 단은 노드조차 등록되지
  않는다. 확정은 기동당 1회이며, 그 결과는 기동 로그 1줄(`record_ladder_resolution`)로만 판독된다.
- `enable_semantic_routing`·`enable_intent_orchestration`은 **tri-state**다. 미입력(None)이면
  `ACTIVE_DB_IDS` 등록 여부로 자동 결정되고 경고를 남긴다 — 실행 경로가 DB 등록 상태에 종속되므로
  고정하려면 `.env`에 명시한다.
- **"코드에 분기가 남아 있다"는 사실만으로 죽은 경로를 판정하지 말 것.** 어느 단을 지우려면 그
  단이 확정되는 설정 조합이 실제로 쓰이지 않음을 먼저 보여야 한다(D-161 · plans/70 v1 오판 사례).

## LangGraph 노드

공통 전단: `context_resolver → input_parser → field_mapper` (사다리 전 단 공통).
3단(semantic_router) 경로의 분기 대상은 `schema_analyzer`(단일 DB) · `multi_db_executor`(멀티 DB) ·
`cache_management` · `synonym_registrar` · `general_inference` · `fault_diagnosis`(옵트인) · `END`(역질문).

단일 DB 경로: `schema_analyzer → [structure_approval_gate] → query_generator → query_validator →
[approval_gate] → query_executor → result_organizer → output_generator`

- `query_validator` 실패 → `query_generator` 회귀 (예산 `QUERY_MAX_RETRY_COUNT`, 기본 3)
- `query_executor` SQL 에러 → `query_generator` 회귀 (에러 컨텍스트 동반)
- `result_organizer` 데이터 부족 → `query_generator` 회귀
- HITL 게이트 2종은 `interrupt_before`로 배선된다(`enable_sql_approval` 기본 off /
  `enable_structure_approval` 기본 **on**)
- 노드 전체 목록은 `src/nodes/` 참조 — 후보 생성/선택, 단계적 컬럼 도출, 조건 프로브,
  실시간 사용률, 시맨틱 컴파일러 등 옵트인 노드가 다수 있다.
- **상태**는 `TypedDict`(`AgentState`, `src/state.py`). LangGraph 체크포인터는 **델타만 병합**하므로
  요청 스코프 상태는 라우트에서 명시 초기화한다(아래 Known Mistakes 참조).

## Tech Stack

| Component | Technology |
|-----------|-----------|
| 에이전트 프레임워크 | LangGraph ≥0.2 (+ 옵트인 `deepagents` extra) |
| LLM provider | `ollama` / `fabrix`(KBGenAI, 운영) / `gemini` — `LLM_PROVIDER`로 선택. 오케스트레이터는 `ORCHESTRATOR_PROVIDER`로 별도 지정 |
| DB 접근 | DBHub 계열 MCP 서버(`mcp_server/`, readonly) 또는 direct(asyncpg) — `DB_BACKEND` |
| DB 엔진 | PostgreSQL · IBM DB2 (방언 분기 필수) |
| 문서 처리 | openpyxl(Excel) · python-docx(Word) — `document` extra |
| API 서버 | FastAPI + uvicorn (웹 UI 정적 자산 포함) |
| 상태 저장 | langgraph-checkpoint-sqlite(기본) / postgres(opt) · Redis(캐시·알람 스트림·세션) |
| 옵트인 extra | `semantic`(E5 임베딩) · `structured`(instructor) · `stl`(statsmodels) · `deepagents` · `gemini` · `e2e` |

`mcp<2` 상한 고정 — mcp 2.x는 `mcp.server.fastmcp`를 제거해 `mcp_server`가 임포트 단계에서 깨진다(D-181).

## 개발 명령

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

# MCP 서버 (별도 venv·별도 cwd)
cd mcp_server && python -m mcp_server

# 테스트
pytest                                   # 본체 + noise_gate 자동 수집
pytest tests/test_graph.py -v
cd sre_agent && .venv/bin/python -m pytest tests -q   # 자체 venv 보유
cd mcp_server && ../.venv/bin/python -m pytest       # 자체 venv 없음 — 루트 venv 사용

# 품질 게이트
python scripts/arch_check.py --ci        # 계층 의존성 (스킬: /arch-check)
python scripts/overfit_check.py --ci     # 공용 계층 스키마 리터럴 누수 (스킬: /overfit-check)
ruff check src/ tests/ && mypy src/

# 평가 하네스 (실 파이프라인 구동 — 과금 경로다. 먼저 --dry-run/--mock으로 확인할 것)
python scripts/eval_text2sql.py --dry-run
python scripts/eval_routing.py --help
```

**e2e·실 LLM 호출은 `RUN_E2E=1` 옵트인 뒤에 있다.** `tests/conftest.py`가 전역 네트워크 가드를
설치하고 `live_llm` 마커를 자동 skip한다 — 설정·실행 자체가 사용자 승인 사항이다(D-127).

## 설정과 정본 파일

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
  **코드 기본값이 아니라 이 실제값을 근거로 판단할 것.**
- 신규 기능 플래그는 **기본 off = 현행 동작과 비트 동일**이 원칙이다(`plans/80` §5.4-③).
  명시적 예외는 근거와 함께 config 주석에 남긴다(예: `COMPOSITE_AVAILABILITY_PRECHECK_ENABLED`,
  `COMPOSITE_HOST_DISCOVERY_ENABLED`, `COMPOSITE_SCOPE_SELECT_ENABLED`는 기본 on).
- 플래그는 **기동 시 1회 해석**한다 — 요청 시점에 바꾸면 프롬프트 접두가 흔들려 KV 캐시가 무효화된다.

## 데이터 도메인

운영 대상은 폴스타(인프라 모니터링) DB 3종 + 로컬 샌드박스다. 존(zone)은 알림 지역 스코프 RBAC
단위이고, 존 그룹(zone_group)은 조회 순서의 축이다(은행존 → 공동존).

| db_id | 존 | 엔진 | 스키마 |
|---|---|---|---|
| `polestar_b0` | bankjon(은행존) | DB2 | `POLESTAR` (대문자 필수) |
| `polestar_cm_gp` | gongjon(공동존·김포 운영/DR) | PostgreSQL | `polestar` |
| `polestar_cm_yd` | gongjon(공동존·여의도 개발/스테이징) | PostgreSQL | `polestar` |
| `polestar` | — | PostgreSQL | 로컬 도커 샌드박스(`testdata/pg/init`) |

주요 데이터: 서버 사양·사용량(EAV `core_config_prop` 피벗 + `cmm_resource` 직접 컬럼),
성능지표(`cmm_metric_stat_[h,d,m]`), 알람(`cmm_alarm` / `cmm_alarm_active` / `cmm_alarm_def`),
프로세스·토폴로지(폴스타 REST·MCP 도구), Prometheus 메트릭(PromQL 도구).

스키마 지식은 레지스트리가 아니라 `db_profiles`/`knowledge`에 둔다. DB별 SQL 특화 로직은
`src/db_adapters/{db}/`에만 격리한다(D-089).

## 문서 처리

- **Excel**: 헤더 행 자동 감지 → 데이터 행 채우기. 병합셀·수식·서식 보존. Excel→CSV→LLM→Excel
  파이프라인(`src/document/excel_csv_converter.py`)과 다중 헤더·월 피벗 폼필을 지원한다.
- **Word**: `{{placeholder}}` 및 표 구조 감지 후 스타일 보존 채우기.
- 양식 필드명 ↔ DB 컬럼 매핑은 LLM 의미 매핑 + 매핑 보고서(`mapping_report.py`) + 사용자 피드백.
- 스키마·조인이 고정된 쿼리(폼필 피벗 등)는 **코드가 runnable SQL을 직접 조립**하고 LLM을
  우회한다(실패 시에만 폴백) — LLM 비결정성 대응.
- 업로드 양식의 DRM 해제는 `src/infrastructure/drm/`(`DRM_*` 설정, `docs/22_drm_deployment_guide.md`).

## 보안 · 제약

- **읽기 전용 DB 접근만** — INSERT/UPDATE/DELETE/DDL 생성 금지. 3중 방어(D-003):
  프롬프트 지시 + `src/security/sql_guard.py` 검증 + MCP 서버 readonly.
- 생성 SQL은 실행 전 검증한다(구문·안전성·참조 테이블/컬럼 존재·LIMIT). 기본 LIMIT 1000,
  재시도 예산 3. **DB 레벨 제한(timeout·max_rows)은 MCP 서버가 관리한다** — 클라이언트 설정 아님.
- 민감 데이터 마스킹(`data_masker.py`)과 FabriX PII 필터 대응(`pii_filter.py`,
  근거 `docs/pii_filtering_rules.md`). 차단 원인 진단용 덤프는 `logs/pii_block/`에 남고 서버 밖으로 나가지 않는다.
- 모든 질의 실행은 감사 로그 대상(`src/security/audit_*`, `src/api/middleware/audit_middleware.py`).
- 인증은 사용자/운영자 분리(JWT). **토큰 서명 검증만으로 끝내지 말고 `type`·role 클레임을 명시 검증**한다.
- 응답시간 목표: 단순 질의 <10s · 복합 <30s · 문서 생성 <60s.

## 품질 게이트

| 게이트 | 검사 | 실행 |
|---|---|---|
| `arch_check.py` | Clean Architecture 계층 의존 방향 (`src/` + `noise_gate/` 동시) | `--ci` |
| `overfit_check.py` | 공용 계층의 폴스타 스키마 리터럴·운영 도메인 누수 (기준선 대비 신규 유입 차단) | `--ci` |
| `catalog_diff.py` | 시맨틱 모델 사본 ↔ profiles+knowledge 파생 동등성 | |
| `prompt_render_diff.py` | 프롬프트 렌더 회귀 | |
| `pii_probe.py` / `pii_regex_check.py` | PII 규칙 점검 | |

`overfit_check`의 기준선은 `scripts/overfit_baseline.json`이다 — **전면 재생성 금지**,
자기 델타만 소거한다. 스캔 대상에 `noise_gate/domain`·`mcp_server/mcp_server`가 포함되므로
**독스트링의 스키마 리터럴도 게이트에 걸린다**(D-179 실사례).

## Multi-Agent Build System

`.claude/agents/`의 `.md` 파일로 에이전트를 정의하고, Claude Agent SDK(`agents/run.py`) 또는
Claude Code 서브에이전트로 실행한다.

| Phase | Agent | 산출물 |
|-------|-------|--------|
| 1 | **requirements-analyst** | `docs/01_requirements.md` |
| 2 | **research-planner** | `plans/*.md` |
| 3 | **implementer** | `src/`, `pyproject.toml` |
| 4 | **verifier** | `tests/`, `docs/verification_report.md` |

**team-lead**가 각 Phase 산출물을 검토·승인한 후 다음 Phase로 진행한다.

```bash
python -m agents.run              # 전체 (Phase 1~4)
python -m agents.run --phase 1    # 요구사항 분석만
```

프로젝트 스킬: `/arch-check` · `/overfit-check` (`.claude/skills/`).

## 패키지 경계 — 기능별 최상위 폴더 (D-139)

기능 단위 코드는 **자기 최상위 패키지 폴더 안에서** 구현한다. 각 패키지는 자기 `tests/`·
`scripts/`·`testdata/`를 소유하며, 본체 `src/`는 text2sql 파이프라인과 조립(entry)만 남긴다.

| 패키지 | 담당 | 실행 형태 | 경계 |
|---|---|---|---|
| `src/` | text2sql 파이프라인·API 조립 | 본체 프로세스 | — |
| `noise_gate/` | 알람 노이즈 캔슬링·분석·통보 + TCP 수신부(`alarm_server/`) | 게이트·워커는 **본체와 같은 프로세스·같은 venv**, 수신부는 독립 프로세스 | `src/ → noise_gate` 의존 잔존(D-048 워커 in-process 기동). 역방향은 config/llm/utils/routing 최소 |
| `sre_agent/` | HolmesGPT 장애 조사 | 별도 venv·별도 프로세스 | 양방향 import 0 (MCP 계약만) |
| `mcp_server/` | 관측 데이터 읽기 경계 | 별도 venv·별도 프로세스 | 양방향 import 0 |

- **신규 기능은 소속 패키지 폴더에** 만들고, 본체 수정은 배선 최소로 한정한다.
- `noise_gate`는 **평탄 레이아웃**(디렉토리 자체가 패키지) — 2단 중첩은 루트에서 import가
  해석되지 않아 editable 설치에 의존하게 된다(D-139 실측). `sre_agent`·`mcp_server`는 자체
  venv·자체 cwd라 2단 중첩 유지.
- 예외: `src/api/routes/alarm.py`는 알람 전용이지만 본체 앱 인증 계층에 묶여 `src/api/`에 남긴다
  (옮기면 `noise_gate → src.api` 역방향 결합 신설 — D-139 근거 참조)

## Clean Architecture 계층 규칙

의존성은 안쪽(domain)에서 바깥쪽(entry)으로만 향해야 한다. `src/`와 `noise_gate/`에 **동일하게**
적용되며 `arch_check.py`가 양쪽을 함께 검사한다(패키지 내 `tests/`·`scripts/`는 대상 제외).

```
domain → config/utils → prompts → infrastructure → application → orchestration → interface → entry
```

`src/` 매핑(정본은 `arch_check.py`의 `MODULE_LAYER_MAP`): `state.py`·`domain/`=domain ·
`config.py`=config · `utils/`=utils · `prompts/`=prompts · `llm.py`·`clients/`·`db/`·`dbhub/`·
`security/`·`schema_cache/`·`document/`·`routing/`·`infrastructure/`·`observability/`=infrastructure ·
`nodes/`·`db_adapters/`·`semantic/`·`tools/`·`sql_validation.py`=**application** ·
`orchestration/`·`graph.py`=orchestration · `api/`=interface · `main.py`=entry.

`db_adapters/`·`tools/`·`semantic/`이 infrastructure가 아니라 application인 것에 주의한다 —
노드·어댑터의 순수 함수를 재노출하는 계층이라 소비처(nodes·orchestration)와 같은 높이다.

```bash
python scripts/arch_check.py              # 위반 검사
python scripts/arch_check.py --verbose    # 의존성 매트릭스 포함
python scripts/arch_check.py --ci         # CI 모드 (위반 시 exit 1)
```

Claude Code 스킬: `/arch-check` 로 호출 가능 (`.claude/skills/arch-check.md`)

## 실수 방지 및 의사결정 관리

### 에이전트 실수 이력 관리

에이전트가 작업 중 실수한 항목은 `docs/18_known_mistakes.md`의 표에 기록하여 동일 실수가 반복되지 않도록 한다.

- 실수 발생 시: 원인과 수정 내용을 `docs/18_known_mistakes.md`에 즉시 기록
- 작업 시작 시: 아래 "Known Mistakes 핵심 원칙"을 확인하고, 관련 영역 작업 시 `docs/18_known_mistakes.md`의 상세 이력 참조
- 형식: `[날짜] 실수 내용 — 원인 — 방지책`

### 의사결정 기록 (`docs/02_decision.md`)

프로젝트의 아키텍처·설계 의사결정은 `docs/02_decision.md`에 일원화하여 관리한다.

**작업 전 (필수)**:
1. `docs/02_decision.md`를 읽고 기존 결정 사항을 확인한다.
2. 수행할 작업이 기존 결정과 충돌하는지 검토한다.
3. **충돌이 발견되면 임의로 진행하지 말고 사용자에게 문의**하여 결정을 받는다.

**작업 후 (필수)**:
1. 작업 중 새로운 의사결정이 발생하면 `docs/02_decision.md`에 추가한다.
2. 기존 결정이 변경되었으면 해당 항목의 상태를 갱신한다.
3. 형식: 기존 `D-NNN` 번호 체계를 따른다 (결정일, 상태, 결정 내용, 근거, 대안).

---

## Known Mistakes 핵심 원칙

> 전체 실수 이력(50여 건, 원인·방지책 상세)은 `docs/18_known_mistakes.md` 참조. 아래는 반복 실수에서 추출한 예방 원칙 요약.

**과금 외부 API 승인 게이트 (D-127 · 2026-07-28 사용자 정책)**
- Gemini 등 **과금이 발생하는 외부 API는 사용자의 명시 승인 없이 호출 금지** — 테스트·스모크·e2e·검증 실행 전부 해당하며, 실행 건마다 승인을 받는다(포괄 승인 없음)
- 실 호출 경로는 전부 `RUN_E2E=1` 옵트인 뒤에 두고, **키 존재만으로 실행되는 게이팅 금지**(키는 `.encenv`에 상존한다는 전제) — 수동 스크립트도 코드 게이트로 강제
- 에이전트는 `RUN_E2E=1` 설정·실행 자체를 사용자 승인 후에만 수행한다

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

