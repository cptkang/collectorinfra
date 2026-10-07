# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> 이 파일은 모든 세션과 서브에이전트가 호출마다 싣는다. **지시만 짧게 두고, 근거·예시·세부 절차는 `docs/34_agent_reference.md`에 둔다**(D-304). 새 내용을 넣을 때도 같은 원칙을 따른다.

## Project Overview

자연어(한국어) 질의로 인프라 관측 데이터를 조회·분석하는 에이전트 플랫폼. 세 축으로 구성된다.

| 축 | 담당 | 진입 |
|---|---|---|
| **text2sql 파이프라인** | 자연어 → SQL/REST/MCP 조회 → 자연어 응답 또는 Excel/Word 양식 산출 | `src/` (LangGraph) |
| **알람 노이즈 캔슬링** | 폴스타 알람 수신·중복/상관 억제·분석·통보 | `noise_gate/` |
| **장애 조사** | HolmesGPT 기반 조사 위임 | `sre_agent/` (별도 프로세스) |

관측 데이터 읽기 경계는 `mcp_server/`(FastMCP)가 담당한다 — DB SQL 실행·폴스타 도구·PromQL.
도메인 판정이나 별도 자격증명을 가진 관측 소스는 독립 게이트웨이 패키지로 둔다(D-274) — 제니퍼 APM은 `apm_gateway/`.

- 원 요구사항: `spec.md` (초기 스펙 — 현 구현은 이보다 훨씬 확장됨)
- **의사결정: 색인 `docs/02_decision.md` + 본문 `docs/decisions/D-NNN.md`** — 작업 전 관련 결정 확인, 작업 후 갱신 (아래 「의사결정 기록」 참조)
- 계획서 전건 인덱스: `plans/INDEX.md` — **미완 계획서는 파일명의 번호 바로 뒤에 상태 태그를 단다**(`NN-TODO-slug.md` / `NN-WIP-slug.md`): `TODO`(코드 0건) · `WIP`(잔여 있음) · 무표기(완료·로드맵). 목록만으로 잔여를 판단할 수 있다(규칙: INDEX 「파일명 상태 접미사」) · 실행 경로 단일 출처: `docs/21_orchestration_ladder.md`
- 최근 작업 단위는 `plans/NN-*.md` + `spec/SPEC-*.md` + `spec/CAPABILITY-MAP-*.md` 조합으로 진행된다

## 저장소 지도

```
src/            text2sql 파이프라인 · FastAPI 앱 조립 · 웹 UI(static)
noise_gate/     알람 노이즈 게이트 (본체와 같은 프로세스) + alarm_server(독립 프로세스)
sre_agent/      HolmesGPT 장애 조사 (별도 venv·별도 프로세스)
mcp_server/     관측 데이터 읽기 MCP 서버 (자체 pyproject·별도 프로세스 — venv는 루트 공유)
apm_gateway/    제니퍼 APM Open API 게이트웨이 — 읽기 전용 MCP 서버(`apm_*`)·이벤트 폴러 (자체 pyproject·별도 프로세스 — venv는 루트 공유 · D-274)
config/         런타임 정본 YAML (DB 레지스트리·프로필·시맨틱 모델·지식·유사어 시드)
docs/           설계·가이드·의사결정(02 색인 + decisions/)·실수 이력(18)·사다리(21)·에이전트 참고서(34)
plans/          영역별 구현 계획서 (INDEX.md가 전건 인덱스)
spec/           SDD 산출물 — SPEC-*.md · CAPABILITY-MAP-*.md (D-244)
scripts/        품질 게이트(arch_check·overfit_check)·평가(eval_*)·운영 CLI
tests/          본체 테스트 (pytest가 noise_gate/tests와 함께 수집)
testdata/       픽스처·골드셋(text2sql_gold·routing_gold·pg init·prometheus)
db/ db2/ redis/ 로컬 개발용 docker-compose (PostgreSQL·DB2·Redis)
tools/          부속 도구 (drm-wrapper·migdata·redis_migration)
agents/         Claude Agent SDK 그린필드 실행기 (D-298 보존 · Phase 1·2는 보관 에이전트 필요)
```

## SDD 산출물 위치 — `spec/` (D-244)

- 기능 맵 `CAPABILITY-MAP-*.md`·모듈 스펙 `SPEC-*.md`는 **`spec/`에 평면으로** 둔다(하위 폴더 없음). `plan-*.md`·`todo-*.md`는 `tasks/`, 원 요구사항 `spec.md`는 루트 그대로. 프로젝트 스킬 `spec-driven-development`의 저장 위치도 `spec/`로 고쳐 두었다.
- **루트 `SPEC.md` 생성 금지** — 개발 맥(APFS)은 대소문자를 구분하지 않아 원 요구사항 `spec.md`를 덮어쓴다.
- 새 문서의 참조는 `spec/SPEC-x.md`처럼 경로로 적는다. 상세: `docs/34_agent_reference.md` §1

## 실행 경로 — 오케스트레이션 사다리

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

- **배타성은 빌드 타임이다** — 상위 단이 성립하면 하위 단은 노드조차 등록되지 않는다. 확정 결과는 기동 로그 1줄(`record_ladder_resolution`)로만 판독된다.
- `enable_semantic_routing` 미입력은 `ACTIVE_DB_IDS` 등록 여부로 자동 결정된다 — 고정하려면 `.env`에 명시한다.
- **"코드에 분기가 남아 있다"는 사실만으로 죽은 경로를 판정하지 말 것**(D-161). 로그 수준·옛 사유 어휘(`flag_off`) 등 상세: `docs/34` §2

## LangGraph 노드

공통 전단: `context_resolver → input_parser → field_mapper` (사다리 전 단 공통).
단일 DB 경로: `schema_analyzer → query_generator → query_validator → [approval_gate] → query_executor → result_organizer → output_generator`

- 검증 실패·SQL 에러·데이터 부족은 `query_generator`로 회귀한다(예산 `QUERY_MAX_RETRY_COUNT`, 기본 3)
- HITL 게이트는 SQL 승인 1종(`approval_gate`, 기본 off). **질의 경로는 DB 구조를 분석하지 않는다**(D-227 — 관리자 「DB 구조」 탭 담당)
- **상태**는 `AgentState`(`src/state.py`). 체크포인터는 **델타만 병합**하므로 요청 스코프 상태는 라우트에서 명시 초기화한다
- 3단 분기 대상·옵트인 노드 목록: `docs/34` §3 · `src/nodes/`

## Tech Stack

| Component | Technology |
|-----------|-----------|
| 에이전트 프레임워크 | LangGraph ≥0.2 (+ 옵트인 `deepagents` extra) |
| LLM provider | `ollama` / `fabrix`(KBGenAI, 운영) / `gemini` / `mlx`(맥북 로컬 테스트 전용 — 앱 밖 `mlx_lm.server`) — `LLM_PROVIDER`로 선택. 오케스트레이터는 `ORCHESTRATOR_PROVIDER`(`vllm`/`gemini`/`mlx`)로 별도 지정. **과금 판정은 두 평면을 모두 본다**(워커 비과금 `fabrix·ollama·mlx` / 오케스트레이터 비과금 `vllm·mlx` — D-222) |
| DB 접근 | DBHub 계열 MCP 서버(`mcp_server/`, readonly) 또는 direct(asyncpg) — `DB_BACKEND` |
| DB 엔진 | PostgreSQL · IBM DB2 · MariaDB(ITAM) (방언 분기 필수) |
| 문서 처리 | openpyxl(Excel) · python-docx(Word) — `document` extra |
| API 서버 | FastAPI + uvicorn (웹 UI 정적 자산 포함) |
| 상태 저장 | langgraph-checkpoint-sqlite(기본) / postgres(opt) · Redis(캐시·알람 스트림·세션) |
| 옵트인 extra | `semantic`(E5 임베딩) · `structured`(instructor) · `stl`(statsmodels) · `deepagents` · `gemini` · `e2e` |

`mcp<2` 상한 고정 — mcp 2.x는 `mcp.server.fastmcp`를 제거해 `mcp_server`가 임포트 단계에서 깨진다(D-181).

## 개발 명령

```bash
pip install -e ".[dev,document]"                 # 설치
python -m src.main --server                      # 본체 (FastAPI + 웹 UI + AlarmWorker in-process)
python -m src.main --query "김포 서버 CPU 사용률 상위 10건"   # 단일 질의 · 인자 없으면 대화형
python -m noise_gate.alarm_server                # 알람 수신부 (TCP 9100 → Redis Stream 'alarm:raw')
cd mcp_server && python -m mcp_server            # MCP 서버 (루트 venv · DB2 조회는 ibm-db 설치 확인)
cd apm_gateway && ../.venv/bin/python -m apm_gateway   # APM 게이트웨이 (apm_gateway/.env)

python scripts/regress.py --base <세션 시작 SHA>  # 구현·교정 뒤 기본 회귀 (아래 「회귀 테스트 정책」)
python scripts/arch_check.py --ci && python scripts/overfit_check.py --ci   # 품질 게이트
RUN_LOCAL_LLM=1 pytest <파일> -m live_llm         # 로컬 MLX 실 LLM 테스트 (외부 차단 가드 유지)
```

- 독립 패키지 테스트: `sre_agent`는 자체 `.venv`, `mcp_server`·`apm_gateway`는 `../.venv/bin/python -m pytest`(루트 수집 밖).
- 평가 하네스(`eval_text2sql`·`eval_routing`·`itam_bench`)는 실 파이프라인을 구동한다 — 먼저 `--dry-run`. MLX 서버(`scripts/mlx_server.sh`)·벤치 모드 등 전체 명령: `docs/34` §9

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

- **로컬 `.env` 실측(2026-10-07)**: `LLM_PROVIDER=mlx` · `ORCHESTRATOR_PROVIDER=mlx` · `DB_BACKEND=dbhub` · `ACTIVE_DB_IDS=polestar,itam` · 사다리 1·2·3단 플래그 모두 true → 1단으로 확정된다. 내부망은 FabriX이며 값은 폐쇄망 `.env`가 정본이다. **코드 기본값이 아니라 실제 `.env` 값을 읽고 판단할 것.** 2단 전환 절차: `docs/34` §4
- 신규 기능 플래그는 **기본 off = 현행 동작과 비트 동일**이 원칙이다(예외는 근거와 함께 config 주석에). 플래그는 **기동 시 1회 해석**한다

## 데이터 도메인

운영 대상은 폴스타(인프라 모니터링) DB 3종 + 로컬 샌드박스다. 존(zone)은 알림 지역 스코프 RBAC
단위이고, 존 그룹(zone_group)은 조회 순서의 축이다(은행존 → 공동존).

| db_id | 존 | 엔진 | 스키마 |
|---|---|---|---|
| `polestar_b0` | bankjon(은행존) | DB2 | `POLESTAR` (대문자 필수) |
| `polestar_cm_gp` | gongjon(공동존·김포 운영 — 「운영」은 김포 기준 용어 · 「운영체제」·「운영 중」·「운영자」·「운영팀」은 제외 · D-271) | PostgreSQL | `polestar` |
| `polestar_cm_yd` | gongjon(공동존·여의도 개발/스테이징/DR — 「개발」·「스테이징」·「DR」은 여의도 기준 용어 · D-271) | PostgreSQL | `polestar` |
| `polestar` | — | PostgreSQL | 로컬 도커 샌드박스(`testdata/pg/init`) |
| `itam` | — (존 미배정 · 단일 시스템) | MariaDB | 로컬 도커 샌드박스(`testdata/itam` · 3307 · `INST1` 가정) · 구조 정본 미작성(G-4) — plans/95 |

주요 데이터: 서버 사양·사용량(EAV `core_config_prop` 피벗 + `cmm_resource` 직접 컬럼),
성능지표(`cmm_metric_stat_[h,d,m]`), 알람(`cmm_alarm` / `cmm_alarm_active` / `cmm_alarm_def`),
프로세스·토폴로지(폴스타 REST·MCP 도구), Prometheus 메트릭(PromQL 도구).

스키마 지식은 레지스트리가 아니라 `db_profiles`/`knowledge`에 둔다. DB별 SQL 특화 로직은
`src/db_adapters/{db}/`에만 격리한다(D-089).

## 문서 처리

Excel·Word 양식 채우기(병합셀·수식·서식 보존) · 필드↔컬럼은 LLM 의미 매핑 + 매핑 보고서 + 사용자 피드백 ·
**스키마·조인이 고정된 쿼리(폼필 피벗 등)는 코드가 runnable SQL을 직접 조립**한다(LLM 우회, 실패 시에만 폴백) ·
업로드 양식 DRM 해제는 `src/infrastructure/drm/`. 상세: `docs/34` §5

## 매뉴얼 동반 정책 (D-255)

**사용자·관리자가 직접 쓰는 기능을 새로 만들거나 사용법·화면을 바꾸면, 같은 작업에서 매뉴얼(`src/static/manual/{user,admin}.html`, 원천 `scripts/manual/`)을 함께 갱신한다.** 내부 리팩터·성능·벤치·하네스는 대상이 아니다. 완료 보고에 반영 여부를 적고, 역방향 가드를 `ignore`로 우회하지 않는다. 절차(`features.yaml` → `content/*.md` → 캡처 → `build` → `pytest tests/test_manual`)와 포트·PID 주의: `docs/34` §6

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

| 게이트 | 검사 |
|---|---|
| `arch_check.py --ci` | Clean Architecture 계층 의존 방향 (`src/` + `noise_gate/`) |
| `overfit_check.py --ci` | 공용 계층의 폴스타 스키마 리터럴·운영 도메인 신규 유입 |
| `regress.py` | 모듈 단위 회귀 + 위 두 게이트 + 바꾼 파일 ruff·mypy + 실패 귀속 (D-303) · `--no-tests` = 정적 게이트만(Wave 중간) |
| `catalog_diff.py` · `prompt_render_diff.py` · `pii_probe.py`·`pii_regex_check.py` | 시맨틱 모델 사본 동등성 · 프롬프트 렌더 회귀 · PII 규칙 |

`overfit_check` 기준선(`scripts/overfit_baseline.json`)은 **전면 재생성 금지** — 자기 델타만 소거한다. 스캔 대상에 `noise_gate/domain`·`mcp_server/mcp_server`가 포함되므로 **독스트링의 스키마 리터럴도 걸린다**(D-179). 위반 수정 패턴: `docs/34` §12

## 회귀 테스트 정책 (D-303)

- **모듈 단위 회귀는 계획서 하나가 끝날 때 1회**(마지막 Wave · 계획서 없는 단건 작업은 작업 끝) — team-lead가 `python scripts/regress.py --base <계획 시작 SHA> --files <계획이 바꾼 파일…>`로 돌린다. `--base`를 생략하면 `HEAD` 기준이라 피어 커밋이 섞인다. 목록만 보려면 `--plan`.
- **Wave 중간**은 implementer가 자기가 쓰거나 바꾼 테스트 파일만 `pytest`로 돌리고 `regress.py --no-tests --files …`(정적 게이트만)를 돌린다. verifier는 팀 리드의 회귀 결과를 받아 쓰고 자기 테스트만 돌린다. 예외: 도구가 `[웨이브 회귀 필요]`(공개 시그니처·`src/config.py`·`src/state.py`·테스트 기반 파일 변경)를 내면 그 Wave 끝에 모듈 단위 회귀를 돌린다. 계획서에 적힌 Wave별 회귀 문구는 이 규칙으로 읽는다(D-303 부기 2026-10-07).
- **전체 회귀(`--full`·맨 `pytest`)는 사용자가 요청할 때만** — 계획 완료·Wave 종료·커밋 전에도 스스로 돌리지 않는다. 도구가 `[전체 회귀 권고]` 블록을 내면 **그 블록을 보고에 그대로 옮긴다**.
- 보고에는 도구 마지막 범위 줄(`범위: 모듈 단위 — 전체 미실행`)을 옮긴다. 모듈 단위 결과를 전체 무회귀처럼 쓰지 않는다. 교정 라운드에서는 실패했던 테스트와 그 모듈 선택분만 다시 돌린다.
- 병렬로 못 도는 테스트는 원인(전역 상태 미원복 등)을 먼저 고치고, 그래도 안 되면 `serial` 마커. `addopts`에 `-n`을 넣지 않는다.
- 선택 규칙·권고 조건·실패 귀속 판정·도구를 못 쓸 때의 grep 대체 절차: `docs/34` §10 · `plans/136`

## 에이전트 모델과 위임 (D-304 · D-312)

`.claude/agents/`의 정의로 서브에이전트를 띄운다. **모델은 정의 파일과 `.claude/settings.json`에 고정 — Agent 호출에 `model` 인자를 넣지 않는다**(사용자 지시 시만).

| 에이전트 | 모델 | 역할 |
|---|---|---|
| team-lead | opus | 계획서 하나를 위임·검토·승인해 끝낸다 |
| implementer | opus | 위임 범위 코드 구현 |
| verifier | opus | 위임 범위 독립 검증·테스트 작성 (제품 코드는 고치지 않음) |
| code-reviewer · security-auditor | sonnet | 변경 리뷰 · 보안 감사 (agent-skills에서 옮겨 옴 — `.claude/THIRD_PARTY.md`) |
| `model` 미지정(general-purpose 등) | sonnet | 조사·보조 |
| Explore · Plan(내장) | 메인 모델 상속 | 탐색 |

- 자동 압축 창은 모델 단위: Opus 5.5 = 300K · Sonnet 5.5 = 200K(`.claude/settings.json`).
- **위임 원칙** — 에이전트 하나에 작업 하나(계획서 하나 또는 Wave 하나), 다음 작업은 새 에이전트. `SendMessage` 재개는 같은 작업의 교정에만. 위임 프롬프트에 파일·D-번호·계획서 절을 직접 적고 문서 전체를 읽히지 않는다. 보고는 요약으로 받는다.
- 그린필드용 `requirements-analyst`·`research-planner`와 형식 불일치로 로드되지 않던 `.claude/skills/` 2종은 `.claude/archive/`에 보관했다(D-312 — 복원은 원위치로 옮기면 된다). agent-skills 플러그인은 이 프로젝트에서 끄고, 쓰던 스킬 2종·에이전트 2종만 프로젝트로 옮겼다(D-312 부기).

## 패키지 경계 — 기능별 최상위 폴더 (D-139)

기능 단위 코드는 **자기 최상위 패키지 폴더 안에서** 구현하고, 각 패키지는 자기 `tests/`·`scripts/`·`testdata/`를 소유한다. 본체 `src/`는 text2sql 파이프라인과 조립만 남기고, 본체 수정은 배선 최소로 한정한다.

| 패키지 | 실행 형태 | 경계 |
|---|---|---|
| `noise_gate/` | 게이트·워커는 본체와 같은 프로세스·venv, 수신부(`alarm_server/`)는 독립 프로세스 | `src → noise_gate` 의존 잔존(D-048). 역방향은 config/llm/utils/routing 최소 |
| `sre_agent/` | 별도 venv·별도 프로세스 | 양방향 import 0 (MCP 계약만) |
| `mcp_server/` | 별도 프로세스·cwd (루트 venv 공유) | 양방향 import 0 |
| `apm_gateway/` | 별도 프로세스·cwd (루트 venv 공유 · 제니퍼 환경에만 배포) | 양방향 import 0 (MCP · `alarm:raw` 계약만 — `tests/test_boundary.py`) · 제니퍼 토큰은 여기에만(D-274) |

`noise_gate` 평탄 레이아웃·2단 중첩·게이트 예외·`src/api/routes/alarm.py` 예외: `docs/34` §7

## Clean Architecture 계층 규칙

의존성은 안쪽(domain)에서 바깥쪽(entry)으로만 향해야 한다. `src/`와 `noise_gate/`에 **동일하게**
적용되며 `arch_check.py`가 양쪽을 함께 검사한다(패키지 내 `tests/`·`scripts/`는 대상 제외).

```
domain → config/utils → prompts → infrastructure → application → orchestration → interface → entry
```

`src/` 매핑(정본은 `arch_check.py`의 `MODULE_LAYER_MAP`): `state.py`·`domain/`=domain ·
`config.py`=config · `utils/`=utils · `prompts/`=prompts · `llm.py`·`clients/`·`db/`·`dbhub/`·
`security/`·`schema_cache/`·`document/`·`routing/`·`infrastructure/`·`observability/`=infrastructure ·
`nodes/`·`db_adapters/`·`semantic/`·`tools/`·`doc_qa/`·`sql_validation.py`·`sql_time_conditions.py`=**application** ·
`orchestration/`·`graph.py`=orchestration · `api/`=interface · `main.py`=entry.

`db_adapters/`·`tools/`·`semantic/`이 infrastructure가 아니라 application인 것에 주의한다 —
노드·어댑터의 순수 함수를 재노출하는 계층이라 소비처(nodes·orchestration)와 같은 높이다.
검사: `python scripts/arch_check.py --ci`(`--verbose`는 의존성 매트릭스) · 위반 수정 패턴은 `docs/34` §12.

## 실수 방지 및 의사결정 관리

### 에이전트 실수 이력 (`docs/18_known_mistakes.md`)

- 실수 발생 시 원인과 수정 내용을 **파일 맨 아래에 항목 블록으로** 즉시 추가한다: `### YYYY-MM-DD · 한 줄 요약` 다음 줄에 `- **실수**:` · `- **원인**:` · `- **방지책**:`
- 작업 시작 시 아래 「Known Mistakes 핵심 원칙」을 확인하고, 관련 영역이면 키워드로 grep해 걸린 항목만 읽는다(목록은 `grep -n '^### '`). **통째로 읽지 않는다.**

### 의사결정 기록 — 색인 `docs/02_decision.md` + 본문 `docs/decisions/D-NNN.md` (D-304)

**작업 전 (필수)**:
1. 작업 영역의 키워드로 색인을 grep하고, 관련 결정 파일만 읽는다. 색인이나 `docs/decisions/` 전체를 통째로 읽지 않는다.
2. 수행할 작업이 기존 결정과 충돌하는지 검토한다.
3. **충돌이 발견되면 임의로 진행하지 말고 사용자에게 문의**하여 결정을 받는다.

**작업 후 (필수)**:
1. 새 결정은 `docs/decisions/D-NNN.md` 새 파일 + 색인 표 끝 1행 + `docs/decisions/CHANGELOG.md` 맨 위 1행으로 등재한다.
2. 기존 결정이 바뀌면 그 파일의 상태·부기와 색인 행의 상태 칸을 고치고 CHANGELOG에 1행을 넣는다.
3. 형식(결정일·상태·결정·근거·구현·주의·관련)과 채번 규칙은 색인의 「쓰는 법」을 따른다.

### 계획서 인덱스 (`plans/INDEX.md`)

상태 칸은 240자 이내 요약으로 쓰고 상세는 계획서 머리에 둔다. INDEX 머리에는 최종 갱신 날짜만 두고, 갱신 이력은 `plans/INDEX-CHANGELOG.md` 맨 위에 한 줄씩 추가한다(D-304).

---

## Known Mistakes 핵심 원칙

> 한 줄 규칙이다. 근거·사례 원문은 `docs/34` §8·§11, 전체 이력은 `docs/18_known_mistakes.md`(grep으로 조회).

**과금 게이트 · 실 LLM (D-127 · D-240)**
- 실 LLM 테스트·스모크는 **로컬 MLX**로 한다. 워커·오케스트레이터 두 평면이 모두 `mlx`(127.0.0.1)로 해석되는지 `python -m scripts.bench --show-env`/`--preflight`로 확인하면 승인 없이 실행한다. 하나라도 과금 평면이면 실행하지 않는다(`.encenv`에 Gemini 키 상존).
- pytest `live_llm`은 `RUN_LOCAL_LLM=1`(가드 유지). 시나리오 `--run`·벤치 `--mode run`·`eval_routing`은 두 평면 `mlx`면 승인 없이 돈다.
- **과금 외부 API(Gemini 등)와 가드를 끄는 `RUN_E2E=1`은 건마다 사용자 승인** — 포괄 승인 없음. 실 호출 경로는 옵트인 뒤에 두고 키 존재만으로 실행되게 하지 않는다.
- MLX 서버는 캐시 모델로만·127.0.0.1 바인딩·자기 PID만 종료. MLX 결과는 로직 확인용(지연 결론은 내부망). **MLX 검증은 최소** — 기본은 가짜 LLM·목·실프로세스 종단, MLX는 바뀐 프롬프트·선택 경로만 대표 소수 1회.

**실측 우선**
- 외부 패키지 API는 `inspect.signature()`로 실측한다. "부재" 단정 전 `-w` grep 전수 확인, 정의뿐 아니라 호출부 배선까지 확인한다.
- 결정적 게이트가 의존하는 데이터는 실 런타임 shape로 검증한다(mock 통과 ≠ 프로덕션).
- 0건·실패 진단은 진입·게이트별 로그로 끊긴 지점부터 확정한다(라우팅 먼저). 필드 null은 생성 SQL 오류일 수 있다.
- 경로·모듈 폐기 제안에는 D-161 ② 4항 실측(운영 `.env` · 설치·서빙 상태 · `git log` 최종 수정일 · 역방향 import)을 첨부한다.
- D-번호 예약은 색인 표에 `예약` 행을 넣어야 효력이 있다. 도구·모델 동작은 설치된 실물로 실측한 뒤 권고한다.

**pydantic-settings / .env**
- list/dict 필드는 JSON 배열(`["a","b"]`). `.env` 계열에 인라인 주석 금지.
- `env_file`은 `os.environ`에 주입되지 않는다 → `os.getenv()`로 설정 판단 금지.
- nested 필드는 `Field(default_factory=...)`. 테스트 config는 검증 대상 필드를 명시해 `.env` 누수를 막는다.

**LLM 비결정성**
- LLM 출력(분류·매핑·alias·방언)에 정합성을 의존하지 않는다 — 결정적 가드로 후처리하고, 고정 형태 쿼리는 코드가 SQL을 직접 조립한다(실패 시만 폴백).
- 프롬프트 강제가 few-shot과 경쟁해 반복 실패하면 결정적 조립 대상이다. 금지 규칙은 범위를 좁게 쓰고 유지할 정상 동작을 재확인한다.
- LLM 자동 등록(유사어 등)은 쓰기 지점에서 결정적으로 차단한다(오염 자기강화).

**단일/멀티 대칭 · 방언**
- 프롬프트 블록·`_structure_meta`·엔진/스키마 규칙이 단일·멀티 **양쪽에 주입됐는지 실측**한다.
- PostgreSQL/DB2: LIMIT vs FETCH FIRST · 수치 캐스트는 집계 **전** · DB2 결과 칼럼 소문자화 · 스키마 대문자 `POLESTAR`. **EAV 숫자 속성은 양 엔진 모두 `CAST(… AS NUMERIC)`**(G-7 — `AS DECIMAL` 강제 금지).
- 새 DB 편입: ①위치 힌트(`_LOCATION_DB_HINTS`) ②`.env` base_url ③엔진 방언 ④스키마 한정(db_schema).

**멀티턴 / 상태**
- 체크포인터는 델타만 병합 — 요청 스코프 상태는 라우트에서 명시 초기화, 노드 스킵 경로는 자기정리.
- 승계 신호(hostname/db_id)는 승격 경로 대칭과 우선순위를 확인한다. 지시어("그 서버")는 식별자가 아니다. 멀티턴 검증은 thread_id가 요청 본문에 실리는지 프론트까지 본다.

**폴백 · 에러**
- 침묵 폴백·강등 금지 — 실패 사유를 구조화해 응답에 노출하고, 삼키는 폴백은 실패 SQL·컨텍스트를 로그로 남긴다.
- 독립 신호 수집은 개별 try/except. 장시간 경로(SSE)는 전체 타임아웃 가드. 데몬 in-memory dict는 키 만료 sweep.

**보안 · 인가**
- 토큰 서명 검증 뒤 `type`·role 클레임을 명시 검증한다. 사용자/운영자 시크릿 분리. 인증 UX는 미로그인 첫 방문 경로로 확인한다.

**테스트 · 작업 절차**
- 대량 실패는 `--tb=line`으로 유형부터 센다(단일 오염원 의심). 상수·매트릭스 변경 시 그 값을 단언하는 테스트를 repo 전체 grep으로 갱신한다.
- 기준선 대조는 `git stash`가 아니라 `git worktree add --detach <dir> <세션 시작 SHA>`.
- 신규 D-번호는 색인 표(예약 행 포함) 최댓값+1 — 등재 직전 `ls docs/decisions/ | tail -3`으로 병행 세션 선점 확인.
- 산출물은 미리보기가 아니라 실제 파일의 전 칼럼으로 검증한다. 큰 문서(결정 색인·`docs/decisions/`·`docs/18`·`plans/INDEX.md`)는 grep으로 좁혀 읽는다.
