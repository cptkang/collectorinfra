# 코드베이스 규모 적정성 문헌 검토 — "LLM 에이전트에 이만한 코드가 필요한가"

> 작성일: 2026-08-06
> **질문(사용자)**: 자원 관리·모니터링 기반 장애 대응 자동화(AIOps) 프로젝트의 코드가 방대해지고 있다. 일반적으로 LLM·AI를 이용하는 에이전트 구현에 이만한 코드가 필요한가? **AI 활용 위주로 코드를 간소화할 수 있는가?**
> **방법**: 코드베이스 전수 실측(2026-08-06, 브랜치 `multiintent` / `64666c7`) + 학술·산업 문헌 조사 16건. 실 LLM 호출 검증은 수행하지 않음(D-127 과금 승인 게이트 — 실측과 문헌만으로 도출).
> **관련 결정**: D-035(결정적=판단·LLM=보조), D-066(단일/멀티 경로 대칭), D-068·D-076(결정적 SQL 조립·SMQ), D-127(과금 API 승인 게이트), D-139(기능별 최상위 패키지 경계)
> **선행 문서**: `docs/deterministic_sql_composition_review.md`(2026-07-13), `docs/regex_llm_conversion_review.md`(2026-07-29), `docs/text2sql_quality_research.md`, `plans/69-query-generation-structural-refactoring.md`(형상 부채 — 완결)
> **수용처 계획**: `plans/70-codebase-scale-and-path-debt.md`

---

## 0. 결론 요약

| 질문 | 판정 | 근거 |
|---|---|---|
| LLM 에이전트에 6.7만 줄이 필요한가? | **정상 범위** — 문제 영역(text2sql + AIOps) 대비 이례적이지 않음 | Sculley 2015(95% glue code), BAIR 2024(compound AI systems), Uber QueryGPT·LinkedIn SQL Bot 동종 사례 |
| "AI에 더 맡겨" 간소화가 가능한가? | **이 프로젝트 조건에서는 역효과** | 폐쇄망 `Qwen3.5-9B` 전제 — 간소화 논문들의 전제(프론티어 모델 + 스키마 전량 컨텍스트 적재) 불성립 |
| 그럼 무엇이 문제인가? | **코드량이 아니라 "설명되지 않은 구조"** — 경로 강등 사다리·플래그·문서가 어디에도 한 장으로 정리돼 있지 않음 | 경로 사다리 4단이 미문서화(**본 문서 초판이 실제로 오독** — §4.1), `enable_*` 41개, 문서 60,051줄·결정 124건 |
| 실제 간소화 지렛대는? | **"코드 → 선언"**(시맨틱 레이어 완결) | arXiv 2604.25149(+17~23%p), arXiv 2606.31041(94.15%), 자체 문서 "선언적 원재료 80% 보유" |

**한 줄 요약**: 코드가 많은 것이 병이 아니라, **새 경로를 도입할 때 구 경로를 지우지 않은 것**이 병이다.

---

## 1. 실측 — 현재 코드베이스 규모

### 1.1 총량 (2026-08-06, venv·`build/` 제외)

| 구분 | 줄 수 | 비고 |
|---|---:|---|
| **프로덕션 Python** | **67,202** | `src` 51,037 · `noise_gate` 11,452 · `mcp_server` 2,724 · `sre_agent` 1,989 |
| 테스트 | 67,144 | 루트 47,391 · noise_gate 15,484 · mcp_server 2,173 · sre_agent 2,096 |
| 스크립트 | 7,136 | 루트 4,882 · noise_gate 1,431 · sre_agent 823 |
| 웹 UI (HTML/JS/CSS) | 9,074 | `src/static/` |
| 문서 (`docs/` + `plans/`) | **60,051** | 계획서 76개 · 문서 38개 |
| 의사결정 | 124건 | D-139까지(결번 포함) |

**테스트:프로덕션 = 1.00 : 1** — 이 비율 자체는 건전하다.

### 1.2 LLM 관여 비중 (핵심 지표)

| 항목 | 규모 | 프로덕션 대비 |
|---|---:|---:|
| 프롬프트 정의 (`src/prompts` + `noise_gate/prompts`) | 2,059줄 | 3.1% |
| LLM 호출 지점 (`invoke`/`ainvoke`/`generate`) | 70곳 | — |
| **LLM 직접 관여 코드 (추정)** | **~5%** | — |
| 그 외 (캐시·검증·라우팅·API·문서처리·상태관리) | **~95%** | — |

이 **5 : 95 비율이 본 검토의 출발점**이다. "AI 에이전트인데 왜 이렇게 코드가 많은가"라는 질문은 정확히 이 95%를 향한 것이다.

### 1.3 규모 상위 모듈 (프로덕션)

```
2157  src/document/field_mapper.py        # 양식 필드 ↔ DB 컬럼 의미 매핑 (다단계)
1901  src/schema_cache/redis_cache.py     # 3단 스키마 캐시 L1
1761  src/schema_cache/cache_manager.py
1579  src/api/routes/admin.py             # 운영자 대시보드 API
1507  src/api/routes/query.py
1462  src/nodes/multi_db_executor.py
1459  noise_gate/application/alarm_worker.py
1420  src/api/routes/alarm.py
1399  src/db_adapters/polestar/prompts.py
1293  src/nodes/schema_analyzer.py
1159  src/nodes/query_generator.py
1144  src/nodes/semantic_compiler.py
```

**관찰**: 상위 12개 중 LLM 프롬프트 파일은 1개(`polestar/prompts.py`)뿐이다. 나머지는 캐시·API·실행·매핑 — 즉 **전통 소프트웨어 영역**이다.

---

## 2. 문헌 — "95%"는 이상이 아니라 정설이다

### 2.1 Sculley et al., NeurIPS 2015 — Hidden Technical Debt in Machine Learning Systems

성숙한 ML 시스템은 **"많아야 5%가 ML 코드이고, 최소 95%는 glue code"**라고 명시한다. 모델 자체가 아니라 데이터 입출력·검증·설정이 시스템 대부분을 차지한다는 것이 10년 전부터의 정설이며, 본 프로젝트의 5:95 비율과 정확히 일치한다.

이 논문이 정의한 부채 유형 중 본 프로젝트에 해당하는 것은 §4에서 다룬다.

### 2.2 BAIR, 2024 — The Shift from Models to Compound AI Systems

프로덕션 최고 성능 AI 애플리케이션은 단일 대형 모델이 아니라 **리트리버·코드 실행기·검증기·메모리를 조합한 복합 시스템**이다. 이유는 ① 모델은 학습 데이터 범위에 갇히지만 복합 시스템은 외부 DB·검색으로 최신 데이터에 접근할 수 있고 ② 동일 투자 대비 수익률이 높기 때문이다. 이 패턴은 이미 지배적이다(엔터프라이즈 LLM 애플리케이션의 60%가 RAG, 30%가 다단계 체인 사용).

### 2.3 HumanLayer — 12-Factor Agents

Factor 8 **"Own your control flow"**: 모델이 다음 행동을 고를 수는 있어도, **루프·중단조건·재시도·승인 게이트·예산 상한은 애플리케이션이 소유**해야 한다. 그 차이가 "제품"과 "폭주하는 프로세스"를 가른다.

핵심 통찰: *"성공한 프로덕션 AI 애플리케이션은 완전 자율 에이전트가 아니라, LLM 능력을 요소요소에 전략적으로 통합한 잘 설계된 전통 소프트웨어다."*

본 프로젝트의 `graph.py` 라우팅·`max_retry` 예산·`enable_sql_approval` HITL 게이트가 정확히 이 원칙의 구현이다.

### 2.4 Anthropic — Building Effective Agents

- **워크플로**(사전 정의된 코드 경로) vs **에이전트**(모델이 스스로 프로세스를 지휘)를 구분하고, *"워크플로는 잘 정의된 작업에 예측가능성과 일관성을 제공한다"*고 한다.
- *"복잡성은 결과가 명백히 개선될 때에**만** 추가하라."*
- 여기서 "복잡성"은 **코드 줄 수가 아니라 모델에게 넘기는 자율성**을 뜻한다. 이 구분이 본 검토의 핵심이다 — 코드를 줄이고 자율성을 늘리는 것은 이 문헌이 말하는 "간소화"가 **아니다**.

### 2.5 동종 프로덕션 시스템 — 전부 다중 컴포넌트

| 시스템 | 구조 | 결과 |
|---|---|---|
| **Uber QueryGPT** | Workspaces(도메인별 큐레이션) + Intent Agent + Table Agent + Column Prune Agent | 쿼리 작성 10분→3분, 연 14만 시간 절감 |
| **LinkedIn SQL Bot** | LangChain/LangGraph 멀티에이전트 + RAG + 지식그래프 + LLM 랭킹·교정 | 정확도 만족도 95% |

두 사례의 공통 교훈: *"고품질 Text-to-SQL은 프롬프팅 이상을 요구한다 — 검색·랭킹·검증·최적화·UX가 필요하다."* 특히 Uber는 **단순 프롬프트에서 시작해 이 구조로 진화**했다. 즉 컴포넌트 증가는 실패가 아니라 성숙의 흔적이다.

> **판정 1**: `src/` 51,037줄은 이 문제 영역에서 이례적이지 않다. 규모 자체를 문제로 삼는 전제는 문헌 근거가 없다.

---

## 3. "AI에 더 맡겨 간소화"가 이 프로젝트에서 역효과인 이유

간소화를 지지하는 논문들은 **전제 조건**이 붙어 있다. 본 프로젝트는 그 전제를 만족하지 않는다.

### 3.1 결정적 전제 — 이 프로젝트의 LLM은 폐쇄망 9B 모델이다

`src/config.py:74-76` 실측:
```python
provider: Literal["vllm", "gemini"] = "vllm"
model: str = "Qwen3.5-9B"     # vLLM 서빙 모델
```
Gemini는 테스트/PoC 전용(egress 필요)이며 **운영은 vLLM 자체 서빙 9B**다. 아래 문헌들은 전부 이 조건에서 해석해야 한다.

### 3.2 The Death of Schema Linking? (arXiv 2408.07702)

스키마 링킹 단계를 버리라고 주장하지만, **명시된 전제가 두 가지**다.

1. *"잘 추론하는(well-reasoned)"* 최신 모델일 것 — 무관한 스키마 요소가 다수 섞여도 관련 요소를 골라낼 수 있어야 함
2. **스키마가 모델 컨텍스트에 통째로 들어갈 것** — 논문 원문: *"스키마가 모델의 컨텍스트 윈도우에 들어가는 경우에 한해 스키마 링킹을 전면 생략"*

본 프로젝트는 9B 모델 + EAV 구조 다중 DB(PostgreSQL·DB2)로 **두 전제 모두 불성립**이다. `src/schema_cache/`(7,016줄)를 걷어내는 것은 이 논문의 권고가 아니라 오독이다.

### 3.3 모델 격차 실측 — 7B/9B급은 아직 12~13%p 뒤진다

BIRD 벤치마크 기준 Qwen2.5-Coder-7B 계열 69.19% vs GPT-4o 약 82%. 특화 기법(Alpha-SQL의 MCTS, Arctic-Text2SQL-R1의 RL, CSC-SQL의 corrective self-consistency)으로 프론티어급에 도달한 사례가 있으나 — **그 기법들이 곧 "추가 코드"다**. 코드를 줄이면 격차가 되돌아온다.

### 3.4 긴 프롬프트로 코드를 대체할 수 없다 — AgentIF (Tsinghua)

- 지시문이 길어질수록 준수율이 하락하며, **6,000단어를 넘으면 모든 모델의 지시 만족도가 0에 수렴**한다.
- LLaMA-3.3-70B조차 단일 프롬프트 정확도 92.1 대비 변형 프롬프트 신뢰도는 71.0.

"결정적 코드를 지우고 프롬프트에 규칙을 더 담는다"는 전략의 물리적 상한이다.

### 3.5 Context Rot (Chroma)

18개 프론티어 모델 **전부** 입력 길이 증가에 따라 성능이 저하되며, 문서화된 컨텍스트 한계 훨씬 이전에 **30~50% 정확도 하락**이 관측된다. "lost in the middle" 효과로 중간 위치 정보의 회수율이 특히 낮다.

→ 스키마 캐시·선별을 없애고 전량 주입하는 방향은 이 결과와 정면 충돌한다.

### 3.6 에이전트를 늘려 코드를 줄이는 방향 — MAST (UC Berkeley/IBM, arXiv 2503.13657)

7개 멀티에이전트 프레임워크의 실행 트레이스 1,600여 건 분석:

| 실패 범주 | 비중 |
|---|---:|
| 시스템 설계·명세 불량 | 42% |
| 에이전트 간 조율 붕괴 | 37% |
| 검증 취약 | 21% |

주요 실패 모드는 **단계 반복(15.7%)**, **종료 조건 미인지(12.4%)**, 작업 오해, 컨텍스트 붕괴다. 조율되지 않은 멀티에이전트는 오류를 최대 17배 증폭시키며, 검증 병목을 둔 중앙집중 구조는 4.4배로 억제한다.

Cognition **「Don't Build Multi-Agents」** 도 같은 진단이다 — 단일 스레드로 연속된 컨텍스트를 유지하는 편이 낫고, 어려운 부분은 멀티에이전트 자체가 아니라 **컨텍스트 엔지니어링**이다.

> 본 프로젝트에 직접 해당: `src/orchestration/`(3,778줄)의 intent_planner → agent_orchestrator → replanner 루프가 정확히 MAST가 지목한 "단계 반복·종료 조건" 실패 지대다. `max_replan: int = 3` 가드가 있는 것은 옳은 대응이나, **경로 자체의 필요성**은 §4에서 재검토한다.

### 3.7 반대로, 현 구조를 지지하는 문헌 — NVIDIA SLM 논문 (arXiv 2506.02153)

- SLM(<10B)은 *"소수의 특화 작업을 반복적으로, 변형 없이"* 수행하는 에이전트 노드에 **충분히 강력하고, 본질적으로 더 적합하며, 필연적으로 더 경제적**이다.
- 7B SLM 서빙은 70~175B 대비 **10~30배 저렴**(지연·에너지·FLOPs).
- **핵심 권고**: 이를 활용하려면 에이전트 작업을 **좁고 반복적인 하위작업으로 분해**해야 한다. 범용 대화 능력이 필요한 곳에만 큰 모델을 두는 **이종(heterogeneous) 구성**이 자연스러운 선택이다.

→ 9B 모델을 쓰기로 한 이상, **노드를 잘게 나누고 각 노드에 결정적 가드를 두는 현 구조가 문헌이 권고하는 형태**다. LangGraph 7노드 분해는 낭비가 아니라 SLM 활용의 전제다.

> **판정 2**: "AI에 더 맡겨 코드를 줄인다"는 방향은 본 프로젝트의 모델 조건에서 정확도·신뢰도를 함께 잃는다. 문헌상 지지되지 않는다.

### 3.8 이미 자체 실측으로 검증된 사항

`docs/regex_llm_conversion_review.md`(2026-07-29)가 `src/` 정규식 210곳을 전수 분석하고 내린 결론:

> *"**전면 전환은 부적합하다. 이 프로젝트는 이미 반대 방향(LLM → 정규식)으로 여러 번 실측 후 되돌린 이력이 코드 주석에 남아 있다.**"* — LLM 전환이 정당한 곳은 **210곳 중 3곳**(시트명 추출·유사어 등록 의사·HITL 승인 의사).

부적합 판정 사유가 문헌과 일치한다:
- **SQL 검증(78곳)** — 검증 대상이 LLM 출력인데 검증자도 LLM이면 자기참조
- **LLM 출력 파싱(20곳)** — 동일
- **마스킹(15곳)** — 보안은 결정성이 요건

> **"AI에 더 맡기기"는 이미 시도해서 되돌린 경로다.** 새로운 제안이 아니라 재시도 제안이 된다.

---

## 4. 그렇다면 진짜 문제는 무엇인가 — 경로 부채

정상 범위를 벗어난 것은 **3가지**이며, 전부 Sculley 논문의 부채 유형에 정확히 대응한다.

### 4.1 ① 실행 경로 — **정정(2026-08-06 재실측): "4종 병존"이 아니라 "1 정본 + 3 폴백 사다리"**

> **본 절의 초판 진단은 오류였다.** 초판은 이 구조를 Sculley의 "Dead Experimental Codepaths"로 분류하고 트랙 B(deepagents) 폐기를 권고했으나, 재실측 결과 **네 경로 전부 살아 있고 상위 2단은 현재 개발 중**임이 확인됐다. 반증 4건:
>
> | 초판 주장 | 재실측 |
> |---|---|
> | `enable_deepagents_package` 기본 `False` | **`.env`에 `ENABLE_DEEPAGENTS_PACKAGE=true`** — 코드 기본값만 읽고 운영 설정 미확인 |
> | 폐쇄망 wheel 반입 필요 | **deepagents 0.6.10 설치 완료** |
> | 사문화된 실험 경로 | **별도 브랜치 `llm-call-governance`에서 2026-08-04 커밋 2건 — 병렬 개발 중** — `deepagents_tools.py` +63(신규 테스트 263줄), `deep_agent.py` +26. *(현 브랜치 `multiintent` 기준 최종 수정은 2026-07-29·2026-07-21 — 초판은 `git log --all` 결과를 브랜치 확인 없이 "현 브랜치 이틀 전 수정"으로 기재했다)* |
> | 트랙 A·B는 경쟁 경로 | **`deep_agent → deepagents_tools → intent_planner·subagents`** — B가 A를 재사용. A 폐기 시 B 붕괴 |
>
> 추가로 D-037 상태는 "**Phase 3~6 예정**"(진행 중 로드맵), `plans/49:55`는 "Track-A Phase 2는 **폴백 경로로 유지**"를 명시한다. 즉 이 구조는 **폐쇄망에서 vLLM·Gemini 가용성이 불확실한 환경의 의도된 graceful degradation 사다리**이며, Sculley의 부채 유형에 해당하지 않는다.
>
> **남는 진짜 부채는 삭제 대상이 아니라 "설명 부재"다** — 이 사다리가 한 장으로 문서화된 곳이 없어 본 문서 초판이 실제로 오독했다. 처방은 §6.2에서 "경로 삭제"에서 "계층 명시화"로 교체했다. 상세 근거와 수정 계획은 `plans/70` v2 §1.2·§1.2.1 참조.

`src/graph.py:475-545` 실측. `field_mapper` 이후 분기는 형태상 4갈래이나, 실제로는 **상위 단이 불가할 때 하위 단으로 내려가는 강등 사다리**다.

```
field_mapper ─┬→ deep_agent                            (트랙 B · deepagents 패키지)
              ├→ intent_planner → agent_orchestrator   (Plan 48/49 의도 분해 + replanner 루프)
              │    └→ replanner ⟲ → result_aggregator
              ├→ semantic_router → {schema_analyzer | multi_db_executor | cache_management
              │                     | synonym_registrar | general_inference | fault_diagnosis}
              └→ schema_analyzer                       (레거시 모드)
```

- `src/orchestration/` **3,778줄**이 상위 2경로에 묶여 있다.
- 설정 주석 실측: *"semantic_routing과 **상호 배타** — 둘 다 활성이면 orchestration 우선"*
- 기본값 실측(`config.py:875-882`): `enable_semantic_routing`·`enable_deepagent_orchestration` 모두 `None`일 때 **멀티 DB 환경이면 자동 활성** → 운영 환경에서 어느 경로가 실제로 도는지가 `.env`가 아니라 **DB 등록 상태에 따라 암묵 결정**된다.
- `enable_deepagents_package`는 기본 `False`이며, 패키지 조립 실패 시 `semantic_router`로 폴백한다(`graph.py:331-337`).

**Sculley의 진단**: dead experimental codepaths는 *"하위 호환 유지를 어렵게 하고 순환복잡도를 지수적으로 증가시킨다"*. 논문은 Knight Capital이 노후 실험 경로 때문에 45분에 4억 6,500만 달러를 잃은 사례를 든다.

### 4.2 ② 설정 부채 — `enable_*` 41개 / bool 72개 / 필드 263개

Sculley: *"대규모 ML 시스템에서 **설정 복잡도는 코드 복잡도에 필적**한다."*

실측된 플래그별 프로덕션 참조 수(테스트·`config.py` 제외) 하위 구간:

| 참조 수 | 플래그 |
|---:|---|
| **0** | `prometheus_enabled` |
| 1 | `anomaly_stl_enabled`, `db_enabled`, `jsonl_enabled` |
| 2 | `change_correlation_enabled`, `decision_store_enabled` |
| 3 | `enable_agentic_enricher`, `enable_deepagent_orchestration`, `enable_deepagents_package`, `enable_semantic_routing`, `feedback_store_enabled`, `sse_bridge_enabled` |

`prometheus_enabled`는 **프로덕션 참조 0건**이다. `noise_gate/infrastructure/prometheus_client.py`(구현 존재)를 이 플래그로 게이팅하는 호출부가 없다. `polestar_metric_baseline.py:24` 주석이 사유를 밝힌다 — *"prometheus_client(폴백 채널·preparatory)는 §5.2 확정 설계상 **배선하지 않는다**"*. 즉 **의도적 예비 코드**이나, 형상으로는 CLAUDE.md Known Mistakes의 *"구현·설정이 있어도 호출부 배선까지 grep으로 확인(정의만 있으면 무효)"* 사례에 정확히 해당한다.

**부채가 부채를 낳은 증거**: 41개 플래그를 관리하기 위해 `config.py` 896줄 + `settings_catalog.py` 951줄 + `admin.py` 1,579줄이 추가로 생겼다(D-129 설정 웹UI). 41개 독립 플래그의 이론적 조합은 2⁴¹이며 실제 테스트되는 조합은 극소수다.

### 4.3 ③ 문서·프로세스 부채 — 60,051줄 / 계획서 76개 / 결정 124건

- `docs/02_decision.md`의 **D-번호 채번 안내 라인 하나가 1,200자를 넘는다** — 예약·결번·재부여·충돌 이력이 누적된 결과다(D-134 예약, D-115 예약 유지, D-101~104 재부여 충돌, D-078~081 결번 등).
- `plans/69`의 개정 이력이 v1~v9까지 있고 각 항목이 수백 자다.
- 프로덕션 코드 67,202줄에 대해 문서 60,051줄 — **거의 1:1**이다.

이는 품질 관리의 흔적이기도 하지만, **신규 세션·신규 인원의 컨텍스트 적재 비용**을 직접 증가시킨다. §3.5의 Context Rot이 사람에게도 적용되는 지점이다.

### 4.4 ④ (정정) `build/lib` 50,818줄 — 리포지토리 오염 아님

초기 조사에서 `build/lib`(src 전체의 낡은 사본, 178파일 50,818줄)를 리포지토리 오염으로 분류했으나, **실측 결과 정정한다**:

```
git ls-files build/  →  0건 (미추적)
.gitignore:15        →  build/  (이미 등재)
```

즉 형상 관리는 정상이다. 남는 영향은 **로컬 `grep`·에이전트 탐색 결과 오염**뿐이며, 조치 난도와 우선순위가 크게 낮다.

> **판정 3**: 문제는 코드량이 아니라 **"새 경로를 기본값으로 승격할 때 구 경로를 지우지 않은 것"**이다. 3대 부채가 전부 이 한 가지 누락에서 파생됐다.

---

## 5. 실제 간소화 지렛대 — "코드 → 선언"

문헌이 지지하는 유일한 감축 방향은 "LLM에 맡기기"가 아니라 **결정적 로직을 선언적 데이터로 옮기기**다.

### 5.1 시맨틱 레이어 — 정확도와 코드량을 동시에 개선하는 유일한 축

| 근거 | 내용 |
|---|---|
| **arXiv 2604.25149** (Semantic Layers for Reliable LLM-Powered Data Analytics) | ClickHouse/Contoso 100문항, 프론티어 3모델 페어 평가. 스키마만 vs 스키마+**4KB 시맨틱 문서** → **정확도 +17~23%p**. 문서 추가 후 세 모델이 통계적으로 구분 불가(67.7~68.7%) — 개선의 원천이 모델 지능이 아니라 **스키마가 인코딩하지 못한 비즈니스 시맨틱의 공급**임을 시사 |
| **arXiv 2606.31041** (Semantic-Layer-Mediated NL2SQL Agent) | LLM은 중간표현(**SMQ**)만 생성, **결정적 컴파일러가 방언별 SQL로 변환**. Spider2-snow 547문항 실행정확도 **94.15%**(리더보드 3위) |
| **dbt Semantic Layer / MetricFlow** | *"모델이 옳은 metric·dimension만 고르면 쿼리는 정의상 정확하며, 잘못된 조인·집계를 만들 수 없다."* dbt Labs 벤치마크: text-to-SQL 84.1% → 시맨틱 레이어 **100.0%** |
| **템플릿/슬롯필링 계보** | Finegan-Dollak 2018, RYANSQL(Spider EM 58.2%), SeaD, RingSQL — 모델은 "어느 템플릿 + 어느 슬롯값"만 고르고 SQL은 **결정적으로 복원**. 트레이드오프: 커버리지가 템플릿 집합에 갇힘 |

**9B 모델 관점의 함의**: arXiv 2604.25149에서 시맨틱 문서 제공 시 모델 간 성능 차가 사라졌다는 것은, **작은 모델일수록 시맨틱 레이어의 이득이 크다**는 뜻이다. 본 프로젝트가 가장 큰 수혜를 볼 조건이다.

### 5.2 결정적 사실 — 이 결론은 이미 사내 문서에 있다

`docs/deterministic_sql_composition_review.md`(2026-07-13) §0:

> *"`collectorinfra`는 이 기법의 **선언적 원재료를 이미 80% 보유**하고 있다. `config/db_profiles/*.yaml`의 `known_attributes`(dimension 카탈로그), `value_joins`/`direct_join`(조인 정의), `query_examples`(검증된 예시 쿼리), 그리고 하드코딩 Template A/B가 그것이다. **빠진 것은 단 하나 — 이 선언들을 LLM이 텍스트로 모방하는 대신, "컬럼 선택"만 LLM이 하고 SQL을 조립하는 결정적 조합 엔진이다.**"*

즉 사용자가 원하는 "간소화"의 설계도는 **이미 존재하며, 완결되지 않은 채 기존 프롬프트 경로와 병존**하고 있다. 이것이 `query_generator.py`(1,159) + `prompt_blocks.py`(725) + `polestar/prompts.py`(1,399) + `semantic_compiler.py`(1,144) = **4,427줄이 동시에 존재하는 구조적 이유**다.

**→ 감축은 "새 엔진 도입"이 아니라 "이미 만든 엔진으로 일원화하고 구 경로를 폐기"할 때 발생한다.** §4의 진단과 같은 처방이다.

### 5.3 참고 — 채택하지 않는 기법과 사유

| 기법 | 문헌 | 본 프로젝트 판정 |
|---|---|---|
| **CodeAct** (arXiv 2402.01030) — 액션을 실행가능 Python으로 통일, 도구 수 감소·성공률 최대 +20%p | ICML 2024 | **부적합** — 읽기전용 DB 접근(D 제약)과 코드 실행 샌드박스는 폐쇄망 보안 요건과 충돌. 도구 조합 유연성보다 SQL 안전성이 우선 |
| **Code execution with MCP** (Anthropic) — 도구 정의를 파일로 두고 점진 탐색, 입력 토큰 **-78.5%**(771K→165K) | 2025-11 | **부분 적용 검토 가치** — `mcp_server` 고수준 도구 8종(D-122) 노출 정책에 이미 유사 취지 반영. 9B 모델의 코드 생성 신뢰도가 전제라 확대는 신중 |
| **Agent Skills** (마크다운 폴더 + 점진 공개) | 2025-12 개방표준 | **간접 시사** — "능력을 코드가 아닌 선언으로 추가"라는 점에서 §5.1과 동일 철학. `db_profiles/*.yaml` 확장이 본 프로젝트판 대응물 |

---

## 6. 종합 판정과 권고 방향

### 6.1 사용자 질문에 대한 직접 답변

**Q1. LLM 에이전트 구현에 이만한 코드가 일반적인가?**
→ **그렇다.** Sculley의 95% glue code, BAIR의 compound AI systems, Uber·LinkedIn 프로덕션 사례가 일치한다. 특히 폐쇄망 9B 모델 전제에서 결정적 스캐폴딩은 정확도의 **원천**이지 낭비가 아니다.

**Q2. AI 활용 위주로 간소화할 수 있는가?**
→ **이 조건에서는 불가하며 역효과다.** 간소화 논문들의 전제(프론티어 모델·스키마 전량 적재)가 불성립이고, 자체 실측(`regex_llm_conversion_review.md`)이 이미 같은 결론에 도달해 되돌린 이력이 있다.

**Q3. 그럼 무엇을 해야 하는가?**
→ **경로 부채 정리 + 시맨틱 레이어 일원화.** 두 조치 모두 "새로 만들기"가 아니라 **"이미 만든 것 중 하나로 수렴시키고 나머지를 폐기"**다.

### 6.2 감축 추정 (상세는 `plans/70`)

**2026-08-06 재실측 반영 — §4.1 정정에 따라 경로 삭제분을 제외했다.**

| 대상 | 성격 | 추정 감축 |
|---|---|---:|
| ~~오케스트레이션 경로 4종 → 1~2종~~ | **철회** — 사다리 전 단 존치 확정(§4.1) | **0** |
| 레거시 모드(사다리 4단)만 정리 | 코드 삭제(진입 0건 확인 조건) | -100 ~ -300줄 |
| 경로 사다리 문서화·명명 정리·강등 관측 | 명시화(순증 포함) | 0 ~ +100줄 |
| `enable_*` 플래그 감사·상수화 | 코드 + 설정 삭제 | -150 ~ -750줄 |
| 시맨틱 레이어 일원화 후 중복 프롬프트 경로 폐기 | 순감 | **-1,400 ~ -2,300줄** |
| 문서 아카이빙 | 컨텍스트 비용 | 문서 60k → 약 절반 |
| `build/lib` 로컬 정리 | 탐색 비용만 | (형상 무관) |

**프로덕션 코드 순감 추정 1,700~3,400줄(약 3~5%).** 초판 추정(6,000~8,500줄, 9~13%)에서 **절반 이하로 하향**됐다. 나머지 95~97%는 구조적으로 필요한 코드다.

> **이 하향이 시사하는 것**: 실측을 붙일수록 "잘라낼 수 있는 코드"는 줄어들었다. 이는 본 문서의 핵심 판정 — *"코드량은 정상이며, 문제는 설명되지 않은 구조"* — 을 오히려 강화한다. 감축 여지 대부분(-1,400~-2,300)이 §5의 시맨틱 레이어 수렴 한 곳에 집중된다는 점도 같은 결론을 가리킨다.

### 6.3 하지 말아야 할 것 (문헌 근거 명확)

| 금지 | 사유 | 근거 |
|---|---|---|
| `sql_validation.py`(937) · `sql_guard.py` · `data_masker.py`의 LLM 대체 | 검증 대상이 LLM 출력인데 검증자도 LLM이면 자기참조. 읽기전용·마스킹은 결정성이 요건 | `regex_llm_conversion_review.md` §5.1~5.3, 12-Factor Factor 8 |
| `schema_cache/`(7,016) 축소·전량 주입 전환 | 9B 모델 + context rot 환경에서 스키마 선별은 정확도의 전제조건 | Chroma Context Rot, arXiv 2408.07702의 전제 불성립 |
| 노드 통합 후 단일 대형 프롬프트화 | 6,000단어 초과 시 지시 준수 붕괴. SLM은 좁은 반복 작업 분해가 전제 | AgentIF, arXiv 2506.02153 |
| 에이전트 수를 늘려 코드 감축 | 실패의 42%가 명세, 37%가 조율. 오류 최대 17배 증폭 | MAST(arXiv 2503.13657), Cognition |

### 6.4 재발 방지 규칙 (핵심)

> **새 경로를 기본값으로 승격할 때, 구 경로 삭제를 같은 D-번호 안에 포함시킨다.**
> 승격 시점에 삭제가 불가하면 **폐기 기한(일자)**을 D-번호에 명시하고, 기한 도래 시 삭제 또는 사유를 붙인 연장 중 하나를 강제한다.

§4의 3대 부채(경로 사다리 미문서화·플래그 41개·문서 60k)가 전부 이 규칙 하나의 부재에서 파생됐다. Sculley 논문의 권고(*"플래그 생성 시 실제 달력 만료일을 설정하고, 도래 시 제거하거나 새 날짜·사유와 함께 명시적으로 연장하라"*)와 동일하다.

**추가 규칙 (2026-08-06 재실측 후 신설)**:

> **폐기를 제안하려면 4항을 실측해 첨부한다** — ①`.env` 등 실제 운영 설정값(코드 기본값 아님) ②관련 패키지·외부 의존의 실제 설치·서빙 상태 ③대상 파일의 `git log` 최종 수정일과 관련 D-번호 ④**대상 모듈을 다른 경로가 import하는지**(역방향 의존).

본 문서 초판이 이 4항을 모두 누락한 채 deepagents 폐기를 권고했고, ④를 확인하지 않은 탓에 **실행하면 정본 경로를 붕괴시킬 제안**을 냈다(§4.1). *"죽은 경로처럼 보이는 것"과 "실제로 죽은 경로"의 구별은 정적 읽기로 불가능하다* — 이 규칙은 그 구별 비용을 폐기 제안자에게 부과한다. `plans/70` v2 §P4의 D-140 ②로 등재 예정.

---

## 부록 A. 참고 문헌

**시스템 아키텍처·기술부채**
- Sculley et al., *Hidden Technical Debt in Machine Learning Systems*, NeurIPS 2015 — https://papers.neurips.cc/paper/5656-hidden-technical-debt-in-machine-learning-systems.pdf
- BAIR, *The Shift from Models to Compound AI Systems*, 2024-02
- HumanLayer, *12-Factor Agents* (Factor 2 Own Your Prompts / Factor 8 Own Your Control Flow) — https://github.com/humanlayer/12-factor-agents
- Anthropic, *Building Effective Agents* — https://www.anthropic.com/engineering/building-effective-agents

**모델 능력 한계**
- Maamari et al., *The Death of Schema Linking? Text-to-SQL in the Age of Well-Reasoned Language Models*, arXiv 2408.07702
- Chroma, *Context Rot: How Increasing Input Tokens Impacts LLM Performance* — https://www.trychroma.com/research/context-rot
- Tsinghua KEG, *AGENTIF: Benchmarking Instruction Following of LLMs in Agentic Scenarios*
- Belcak et al. (NVIDIA), *Small Language Models are the Future of Agentic AI*, arXiv 2506.02153

**멀티에이전트 실패**
- Cemri et al., *Why Do Multi-Agent LLM Systems Fail?* (MAST), arXiv 2503.13657
- Cognition, *Don't Build Multi-Agents* — https://cognition.com/blog/dont-build-multi-agents

**시맨틱 레이어·결정적 조립**
- *Semantic Layers for Reliable LLM-Powered Data Analytics*, arXiv 2604.25149
- *A Semantic-Layer-Mediated Agent for NL2SQL over Heterogeneous Enterprise Databases* (SMQ), arXiv 2606.31041
- Finegan-Dollak et al. 2018 (템플릿 베이스라인); Choi et al., *RYANSQL*, CL 2021
- dbt Semantic Layer / MetricFlow 문서

**프로덕션 사례·벤치마크**
- Uber, *QueryGPT – Natural Language to SQL Using Generative AI* — https://www.uber.com/en-ES/blog/query-gpt/
- LinkedIn SQL Bot (ZenML LLMOps Database)
- Microsoft Research, *AIOpsLab: A Holistic Framework to Evaluate AI Agents for Enabling Autonomous Clouds*, MLSys 2025
- Alpha-SQL (arXiv 2502.17248), Arctic-Text2SQL-R1 (arXiv 2505.20315), CSC-SQL (arXiv 2505.13271) — 7B급 BIRD 성능

**기타**
- Wang et al., *Executable Code Actions Elicit Better LLM Agents* (CodeAct), ICML 2024, arXiv 2402.01030
- Anthropic, *Code execution with MCP: Building more efficient agents*, 2025-11
- Anthropic, *Agent Skills* (agentskills.io 개방표준, 2025-12)

## 부록 B. 실측 재현 명령

```bash
# 프로덕션 LOC (venv·build·테스트·스크립트 제외)
find src noise_gate sre_agent mcp_server -name "*.py" \
  -not -path "*/tests/*" -not -path "*/scripts/*" \
  -not -path "*/__pycache__/*" -not -path "*/.venv/*" -exec cat {} + | wc -l

# 플래그별 프로덕션 참조 수
for f in $(grep -oE "^\s+(enable_[a-z_]*|use_[a-z_]*|[a-z_]*_enabled)\s*:" src/config.py | tr -d ' :'); do
  echo "$(grep -rn "$f" --include='*.py' src noise_gate | grep -v '/tests/' | grep -v 'config.py' | wc -l) $f"
done | sort -n

# 경로 분기 실측
sed -n '475,545p' src/graph.py

# 문서 규모
find docs plans -name "*.md" -exec cat {} + | wc -l
```
