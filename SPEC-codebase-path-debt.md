# SPEC: 코드베이스 경로·설정 부채 정리

> **출처**: `plans/70-codebase-scale-and-path-debt.md`(v4, 2026-08-06 작성) 분석 + 2026-08-20 전수 실측
> **성격**: 기능 추가 없음. **동작 불변 또는 명시 승인된 동작 축소**만 수행한다.
> **상태**: 초안 — 사용자 승인 대기. 승인 전 구현 착수 금지.

---

## 0. 실측 검증 — 계획서 전제 중 5건이 이미 무효다

계획서는 14일 전(2026-08-06 · `64666c7`) 작성됐고, 그 사이 D-140~D-142 작업이 랜딩했다.
**계획서 주장을 그대로 승계하지 않고 전수 재측정한 결과**는 아래와 같다.

| # | 계획서 전제 | 2026-08-20 실측 | 판정 |
|---|---|---|---|
| 1 | **D-140을 plans/70이 예약**(미등재·재사용 금지) | `## D-140`·`D-141`·`D-142` **모두 등재됨** (SQL 로그·트레이스·동의어) | ❌ **무효 — 재채번 필수** |
| 2 | `src/observability/`가 없어 P2-5 배치를 고민(`llm_call_counter.py`는 타 브랜치) | **존재**(D-141로 신설, 5모듈 810줄 + 테스트 156건) | ❌ 무효 — **배치 결정 완료·재사용 가능** |
| 3 | 설정 필드 **263개** | **251개**(중첩 포함 실측 = 카탈로그 노출 수와 일치) | ❌ 무효 — 재현 불가 |
| 4 | `enable_*`/`*_enabled` 플래그 **41개** | **43개** (D-140/141이 `sql_log_enabled`·`trace_enabled` 추가) | ⚠️ 수치 갱신 |
| 5 | `graph.py:375`(배타)·`539`(레거시)·`640`(빌드 로그) | **381 · 546 · 645** (D-141의 `TracedGraph` 5줄로 밀림) | ⚠️ 라인 갱신 |
| 6 | `scripts/eval_text2sql.py:50` `PATHS`에 `deep_agent` 없음 | `PATHS = ("graph", "orchestration", "multidb")` — **정본 평가 수단 부재 확인** | ✅ 성립 |
| 7 | 골드셋 26건(b0 5 · gp 15 · yd 6), 커버리지 76.9% | **26건 일치**(b0 5 · gp 15 · yd 6) | ✅ 성립 |
| 8 | `docs/`는 `20_`까지 → `21_` 예약 가능 | **최대 20** — 예약 여전히 유효 | ✅ 성립 |
| 9 | `plans/README.md`는 인덱스가 아님 | 첫 줄이 "구현 계획서 목차"이고 표 44행 존재하나 **Plan 68·69·70 미등재** — "인덱스가 아니다"보다 **"인덱스인데 갱신이 멈췄다"**가 정확 | ⚠️ 서술 정정 |

**계획서에 없는 신규 발견 2건** (이번 실측에서 드러남)

| # | 발견 | 근거 |
|---|---|---|
| N1 | **`.venv/lib/python3.12/site-packages/src/`에 7월 23일자 stale 비-editable 사본**이 남아 있다. 프로젝트 루트에서 실행하면 올바른 쪽이 잡히지만, 다른 cwd에서 실행하면 stale이 먼저 잡힌다 | D-139가 "과거 stale 비-editable `.venv/src` 로드 사고 이력"으로 경고한 바로 그 유형. 2026-08-19 검증 스크립트가 실제로 이것을 잡아 `ImportError` 발생 |
| N2 | `sre_agent/`에 자체 `.venv`가 있어 **규모 측정이 오염**된다(순진한 `find`로 14,377 파일). 계획서 §1.1의 `sre_agent 1,989`는 venv 제외 값이나 **재현 명령에 제외 조건이 없다** | `find sre_agent -name "*.py" -path "*venv*" \| wc -l` → 14,377 |

> **§0의 함의**: 계획서 §1.1·§1.3의 수치는 **재현 명령과 함께 갱신**되어야 한다. 특히 D-140
> 재채번은 계획 착수의 **선행 조건**이다 — P0-4/P4-1이 전제한 번호가 이미 쓰였다.

---

## 1. Objective

### 무엇을·왜

새 실행 경로를 기본값으로 승격할 때 구 경로를 지우지 않아 누적된 **경로·설정·문서 부채**를
정리한다. 목표는 코드량 감축이 아니라 **"살아있는데 설명이 없는 구조"를 읽을 수 있게 만드는 것**이다.

계획서 §0.1의 진단을 실측으로 승계한다 — 코드 규모(프로덕션 67k줄, LLM 직접 관여 ~5%)는
정상 범위이고, "AI에 더 맡겨 간소화"는 폐쇄망 9B 모델 조건에서 역효과로 이미 판정됐다
(`regex_llm_conversion_review.md` 선례). **실제 이상 신호는 미완결 마이그레이션 하나다.**

### 사용자

- **1차**: 이 저장소에서 작업하는 개발자·에이전트 — 어느 실행 경로가 도는지 읽어낼 수 있어야 한다
- **2차**: 운영자 — 기동 시 어느 단으로 확정됐는지, 왜 강등됐는지 로그로 알 수 있어야 한다

### 성공의 모습

1. `docs/21_orchestration_ladder.md`만 읽고 **"트랙 A를 지우면 트랙 B가 깨진다"**를 알 수 있다
   *(계획서 v1이 실패한 판단을 문서가 방지하는지가 합격 기준)*
2. 기동 로그 1줄로 **확정된 단과 강등 사유**를 판독할 수 있다
3. 플래그 43개 전수가 **판정(존치/상수화/삭제/기한부)**과 함께 표에 있다
4. 경로·모듈 폐기 제안에 **4항 실측 첨부가 강제**된다(D-143)

### 비목표 (계획서 §0.2·§0.5 승계 — 문헌 근거 있음)

- 검증·마스킹·보안 코드의 LLM 대체
- `schema_cache/` 축소 또는 스키마 전량 주입
- 노드 통합 → 단일 대형 프롬프트
- 에이전트 추가를 통한 코드 감축
- **테스트 코드 감축** — 현 1:1 비율은 이 작업의 유일한 안전망

---

## 2. Capability Map

계획서의 P0~P4는 실행 순서 축이라 의존 관계가 교차한다. **독립적으로 테스트·출시 가능한
단위**로 다시 자른다.

| Module id | 책임 | 계획서 대응 | 의존 |
|---|---|---|---|
| `promotion-rule` | 승격-폐기 동반 원칙 + 폐기 전 4항 실측 의무를 결정으로 고정 | P0-4, P4-1~3 | — |
| `path-observability` | 기동 단 확정 로그 + 강등 관측 상설화 | P0-1, P2-5 | — |
| `flag-audit` | 플래그 43개 전수 감사표 + 사문화 플래그 처리 | P0-3, P1-1, P1-2 | `promotion-rule` |
| `doc-hygiene` | 계획 인덱스 신설·완결분 아카이빙·채번 라인 정리 | P1-3, P1-4, P1-5 | — |
| `eval-baseline` | 정본 경로 평가 수단 + 골드셋 확충 + 렌더 골든 확장 | P0-5, P0-6, P0-2 | — |
| `ladder-docs` | 사다리 단일 출처 문서화 + 명명·암묵활성 정리 + 4단 정리 | P2-1~4 | `path-observability`, `promotion-rule` |
| `semantic-convergence` | 시맨틱 레이어 완결 + 중복 프롬프트 경로 폐기 | P3-1~4 | `eval-baseline`, `ladder-docs` |
| `env-hygiene` | **신규(N1·N2)** — stale 설치본 정리 + 규모 측정 재현성 | — | — |

**빌드 순서**

```
[선행] promotion-rule          ← 규칙을 먼저 고정해야 이후가 그 아래 실행됨
   │
   ├─ path-observability ─┐
   ├─ flag-audit          │
   ├─ doc-hygiene         │    (서로 파일 안 겹침 · 병렬 가능)
   ├─ eval-baseline       │
   └─ env-hygiene ────────┘
                          ↓
                     ladder-docs
                          ↓
                  semantic-convergence   ← 최대 작업 · 게이트 필요
```

**의존 방향 근거**
- `promotion-rule`이 선행인 이유: 이후 모든 폐기 제안이 이 규칙의 4항 실측을 통과해야 한다.
  규칙을 나중에 만들면 이미 내린 판단을 소급 검증하게 된다.
- `ladder-docs`가 `path-observability`에 의존: 사다리 문서에 **실제 확정 단**을 적으려면
  기동 로그가 먼저 있어야 한다. 추정으로 쓰면 계획서 v1의 오독을 문서로 굳힌다.
- `semantic-convergence`가 `eval-baseline`에 의존: 정본 경로 평가 수단 없이 "현행 이상"을
  판정할 수 없다(계획서 게이트 4).

---

## 3. Tech Stack

기존 스택 유지. **신규 서드파티 의존성 0.**

| 구분 | 기술 |
|---|---|
| 설정 | pydantic-settings (`src/config.py`, 카탈로그 SSOT는 D-129 인트로스펙션) |
| 그래프 | LangGraph ≥0.2.0 |
| 관측 | `src/observability/`(D-141 신설) · stdlib `logging` · structlog |
| 평가 | `scripts/eval_text2sql.py` + `testdata/text2sql_gold/*.yaml` |
| 테스트 | pytest (`asyncio_mode = "auto"`) |

---

## 4. Commands

```bash
# 전체 테스트 (본체 + noise_gate 자동 수집)
pytest

# 아키텍처 계층 검사 (모든 Step의 공통 게이트)
python scripts/arch_check.py --ci
python scripts/overfit_check.py --ci

# 평가 하네스 (실 LLM 미호출)
.venv/bin/python scripts/eval_text2sql.py --dry-run

# 프롬프트 렌더 골든
pytest tests/test_prompt_render_matrix.py -q
UPDATE_PROMPT_SNAPSHOT=1 pytest tests/test_prompt_render_matrix.py -q   # 신규 키 채록 시에만

# 플래그 참조 수 재산출 (§0 실측 재현)
for f in $(grep -oE "^\s+(enable_[a-z_]*|use_[a-z_]*|[a-z_]*_enabled)\s*:" src/config.py | tr -d ' :' | sort -u); do
  echo "$(grep -rn "$f" --include='*.py' src noise_gate | grep -v '/tests/' | grep -v 'config.py' | wc -l) $f"
done | sort -n

# 규모 측정 (N2 — venv 제외 필수)
find src noise_gate mcp_server sre_agent -name "*.py" \
  -not -path "*/tests/*" -not -path "*/__pycache__/*" -not -path "*venv*" | xargs wc -l | tail -1

# 클린 기준선 (Known Mistakes — git stash 금지, .env 복사 필수)
git worktree add /tmp/base-70 HEAD && cp .env .encenv /tmp/base-70/
```

---

## 5. Project Structure

```
docs/
├── 21_orchestration_ladder.md      ← [신규] ladder-docs: 사다리 단일 출처
├── flag_audit.md                   ← [신규] flag-audit: 43개 전수 판정표
└── 02_decision.md                  ← [변경] D-143 등재 (D-140 아님 — §0 ①)

plans/
├── INDEX.md                        ← [신규] doc-hygiene: 75개 전건 인덱스
└── archive/                        ← [신규] 완결 계획서 이동 대상

src/
├── graph.py                        ← [변경] path-observability: 빌드 로그 2필드
├── config.py                       ← [변경] ladder-docs: 개명 + AliasChoices
└── observability/                  ← [재사용] D-141 인프라 (신설 아님)

scripts/eval_text2sql.py            ← [변경] eval-baseline: --path deep_agent
testdata/text2sql_gold/*.yaml       ← [변경] eval-baseline: 26건 → 확충
tests/snapshots/prompt_render_sha256.json  ← [변경] 2단 시나리오 추가
```

---

## 6. Code Style

기존 관례를 따른다. 이 작업의 특성상 **"왜 남기는가"를 적는 것**이 핵심이다.

```python
# 좋은 예 — 존치 근거를 코드에 남긴다 (삭제 제안자가 4항 실측을 하도록)
# 트랙 A(의도 분해)는 트랙 B(deepagents)의 폴백이자 **모듈 공급자**다.
# `deepagents_tools → intent_planner·subagents` 의존이 있어 트랙 A를 지우면
# 정본 경로가 붕괴한다(D-037 Phase 3~6 진행 중, docs/21_orchestration_ladder.md).
if config.enable_intent_orchestration and not use_deep_agent:
    ...
```

**규약**
- 사다리 각 단의 분기 상단에 **존치 근거 + 문서 링크** 주석
- 플래그 삭제·상수화 시 **생성 D-번호와 폐기 근거**를 커밋 메시지에
- 신규 `enable_*` 플래그를 만들지 않는다 (플래그 부채를 줄이는 작업이 플래그를 늘리면 자기모순)
- 라인 번호를 문서에 적을 때는 **함수·상수명을 함께** 적는다 (§0 ⑤ — 라인은 밀린다)

---

## 7. Testing Strategy

| 레벨 | 대상 | 비고 |
|---|---|---|
| 골든 무갱신 | `tests/snapshots/prompt_render_sha256.json` | **동작 불변 Step은 갱신되면 안 된다.** 갱신이 필요하면 그 Step은 동작 변경 |
| 기준선 대조 | 전체 스위트 실패 집합 diff | `git worktree add` + **`.env`·`.encenv` 복사**(3회 재발한 실수) |
| 경로별 발동 | 사다리 4단 각 조건으로 빌드 → 로그 단언 | `path-observability`의 DoD |
| 회귀 | 구 환경변수명으로도 기동 | `ladder-docs` 개명의 핵심 회귀 |
| 평가 | `--dry-run` 스키마 검증 0위반 | `eval-baseline` |

**측정 원칙**
- 성능·규모 수치는 **재현 명령을 함께** 기록한다(§0 N2가 이 원칙의 근거)
- 실 LLM 호출 검증은 **건별 승인 후**(D-127). `--dry-run`·`--mock`은 승인 대상 아님

---

## 8. Boundaries

**Always do**
- 모든 Step 완료 시 `arch_check --ci` + `overfit_check --ci` + 영역 테스트 그린
- Step 1개 = 커밋 1개 (롤백 단위 보존)
- 폐기 제안에 **D-143 ② 4항 실측** 첨부 (운영 설정값 · 런타임 가용성 · 브랜치 한정 git log · 역방향 import)
- 기준선 worktree에 `.env`·`.encenv` **복사**
- 문서에 라인 번호를 적을 때 함수·상수명 병기

**Ask first**
- 사다리 4단(레거시 모드) 제거 — 계획서 게이트 6
- 시맨틱 레이어 수렴 착수 — 계획서 게이트 4 (LLM 출력 분포가 바뀐다)
- 플래그 삭제 중 `prometheus_enabled` 처리 방향 (배선 완결 / 삭제 / 기한부 존치)
- 완결 계획서 아카이빙 — 참조 링크가 끊길 수 있음
- 실 LLM 호출을 수반하는 검증 (D-127, 건별)

**Never do**
- 트랙 A·B 관련 파일 삭제 (`ladder-docs` Phase에서 삭제 diff 0줄임을 커밋에서 확인)
- 신규 `enable_*` 플래그 추가
- 테스트 코드 감축
- `git stash`로 기준선 대조
- **D-140·D-141·D-142 재사용** (이미 등재됨 — §0 ①)

---

## 9. Success Criteria

모듈별 완료 조건. 전부 실측 가능한 형태로 기술한다.

### `promotion-rule`
- [ ] `docs/02_decision.md`에 **D-143**으로 등재 (`## D-` 헤더 + 「변경 이력」 표 양쪽)
- [ ] 채번 안내 라인이 "현재 최대 D-142 → 다음 D-143"으로 갱신
- [ ] `CLAUDE.md` Known Mistakes 원칙에 4항 실측 의무 1~2줄 반영
- [ ] plans/70 본문의 "D-140 예약" 표기가 D-143으로 정정

### `path-observability`
- [ ] 기동 로그 1줄로 확정 단(`use_deep_agent`)과 강등 사유(`package_missing`/`orchestrator_unavailable`/`flag_off`/`none`) 판독
- [ ] tri-state 자동 해석 발동 여부(`resolved_by`)가 로그에 기록
- [ ] 4단 각 조건으로 빌드 시 로그가 해당 단·사유를 출력함을 테스트로 단언
- [ ] 골든 스냅샷 **무갱신**
- [ ] 강등 지표가 `src/observability/`에 배치 (신규 모듈 신설 없음 — D-141 인프라 재사용)

### `flag-audit`
- [ ] `docs/flag_audit.md`에 **43행**(현 실측치) 누락 0
- [ ] 각 행에 생성 D-번호 · 코드 기본값 · **`.env` 실제값** · 프로덕션 참조 수 · 최종 변경일(브랜치 한정) · 판정
- [ ] 참조 0건·1~2건 플래그의 처리 방향이 판정 칸에 확정
- [ ] 표의 참조 수가 §4 재현 명령 결과와 일치

### `doc-hygiene`
- [ ] `plans/INDEX.md`에 **75개 전건** 등재 (번호·제목·상태·완결일)
- [ ] 아카이빙 후 상호 참조 링크 유효성 검사 통과
- [ ] `docs/02_decision.md` 채번 안내 라인이 1,200자+에서 축약되고 예약 목록이 표로 분리

### `eval-baseline`
- [ ] `scripts/eval_text2sql.py --path deep_agent`가 인자로 수용되고 mock 모드에서 정상 종료
- [ ] 기존 3경로(`graph`/`orchestration`/`multidb`) 동작 불변
- [ ] 골드셋이 카테고리별 최소 표본 충족 — 특히 `unhandled`(현 1건)·`outside`(현 6건) 보강
- [ ] `--dry-run` 스키마 위반 0건 유지 · **기존 26건 불변**(추가만)
- [ ] 프롬프트 렌더 골든에 2단(orchestration) 시나리오 추가, **기존 12키 값 불변**

### `ladder-docs`
- [ ] `docs/21_orchestration_ladder.md`만 읽고 "트랙 A를 지우면 트랙 B가 깨진다"를 알 수 있다
- [ ] `graph.py` 분기 주석과 `.env` 주석이 이 문서를 가리킨다
- [ ] `enable_deepagent_orchestration` 개명 후 **구 환경변수명으로도 기동**된다(AliasChoices)
- [ ] `.env` 명시값이 있을 때 tri-state 자동 해석이 개입하지 않음을 테스트로 단언
- [ ] 트랙 A·B 파일 **삭제 diff 0줄**

### `semantic-convergence`
- [ ] 커버리지 목표선과 카테고리별 판정이 문서화
- [ ] 폐기 대상 프롬프트 경로가 카테고리별 단계 적용 (전면 아님)
- [ ] 커버리지 밖 질의의 **거부/에스컬레이션 경로** 확정 — "그럴듯하지만 틀린 SQL을 조용히 신뢰된 답으로 내놓지 않는다"
- [ ] 평가 결과가 **현행 이상**임을 정본 경로(`--path deep_agent`)로 실측

### `env-hygiene` *(신규)*
- [ ] `.venv/site-packages/src/` stale 사본 제거 또는 editable 재설치
- [ ] 프로젝트 밖 cwd에서 `import src.config` 시 프로젝트 사본이 잡힘을 실측
- [ ] 규모 측정 재현 명령에 `-not -path "*venv*"` 포함 · 계획서 §1.1 수치 갱신

---

## 10. Open Questions

**Q1 (블로킹 — `promotion-rule` 착수 전)**
D-140이 소진됐으므로 신규 번호는 **D-143**이다. 다만 `docs/02_decision.md:8`에 따르면
**D-105·D-115·D-134가 다른 계획에 예약**되어 있다. D-143이 맞는지, 아니면 예약분 중
하나를 회수할지 확인이 필요하다. *(기본: D-143 신규 채번)*

**Q2 (블로킹 — `ladder-docs`)**
계획서 게이트 6(레거시 4단 제거)은 `path-observability`의 기동 로그로 해소된다. 다만
**제거 대상이 3줄**(`graph.add_edge("field_mapper","schema_analyzer")`)이라 감축 효과가
-5~-20줄이다. 사다리 단순화(4단→3단)라는 목적만으로 착수할지, 아니면 존치할지.
*(기본: 기동 로그로 4단 미확정 확인 시 제거)*

**Q3 (비블로킹)**
`prometheus_enabled`(참조 0건) 처리 — ①배선 완결 ②플래그·클라이언트 동시 삭제
③"예비 코드" 주석 + 폐기 기한. *(기본: ③ — D-143 기한부 존치가 새 규칙의 첫 적용 사례가 된다)*

**Q4 (비블로킹)**
문서 아카이빙 범위 — `plans/` 75개 중 완결분 기준을 무엇으로 할지(상태 표기? 최종 수정일?).
*(기본: 계획서 자체에 "완결/잔여 0건" 표기가 있는 것만 이동, 판단이 애매하면 남긴다)*

---

## 11. 다음 단계

1. 이 스펙과 **Capability Map을 승인**받는다 (모듈 경계·의존 방향·빌드 순서)
2. Q1·Q2 확정
3. `planning-and-task-breakdown`으로 모듈별 태스크 분해 → `tasks/plan-70.md`
4. `promotion-rule`부터 착수 (규칙을 먼저 고정)
