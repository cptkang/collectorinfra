# 오케스트레이션 사다리 — 실행 경로 단일 출처

> **작성** 2026-08-24 (plans/70 P2-2 / L1) · **대상 코드** `src/graph.py` `build_graph()` ·
> `src/orchestration/deep_agent.py` · `src/observability/ladder.py`
>
> 이 문서는 "지금 어느 실행 경로로 도는가"의 **단일 출처**다. `graph.py`의 분기 주석과
> `.env` 주석은 여기로 수렴한다.

## 왜 이 문서가 있는가

`plans/70` v1이 `graph.py`의 `if/elif` 형태만 보고 실행 경로 4종을 **"대등한 4경로 병존"**
으로 읽고, 그중 일부의 폐기를 권고했다. 실제 구조는 **1 정본 + 3 폴백의 강등 사다리**다.
그대로 실행했다면 운영 정본 경로가 붕괴한다.

정적 읽기로는 "죽은 경로처럼 보이는 것"과 "실제로 죽은 경로"가 구별되지 않는다.
이 문서와 기동 로그(아래 §5)가 그 구별을 대신한다.

## 1. 4단 구조

| 단 | 이름 | 진입 배선 | 활성 조건 (앞 단이 전부 불성립일 때) |
|---:|---|---|---|
| **1 (정본)** | `deep_agent` | `field_mapper → deep_agent → END` | `enable_deepagents_package` **AND** 오케스트레이터 가용 **AND** deepagents 패키지 조립 성공 |
| 2 | `intent_orchestration` | `field_mapper → intent_planner → agent_orchestrator → [replanner 루프] → result_aggregator → END` | `enable_deepagent_orchestration` |
| 3 | `semantic_router` | `field_mapper → semantic_router → 조건부 분기` | `enable_semantic_routing` |
| 4 | `legacy` | `field_mapper → schema_analyzer` | 위 셋 모두 불성립 (`else`) |

**"앞 단이 전부 불성립일 때"가 핵심이다.** 2·3단의 플래그가 켜져 있어도 1단이 성립하면
2·3단은 **노드조차 등록되지 않는다**.

## 2. 배타성은 런타임이 아니라 빌드 타임이다

노드 등록 자체가 배타적이다 (`src/graph.py`):

```python
if use_deep_agent:                                              # 1단
    graph.add_node("deep_agent", ...)
if config.enable_deepagent_orchestration and not use_deep_agent:  # 2단
    graph.add_node("intent_planner", ...); ...
if config.enable_semantic_routing and not use_deep_agent:         # 3단
    graph.add_node("semantic_router", ...)
```

이어서 배선도 `if / elif / elif / else` 체인이다. 따라서:

- **요청 시점에는 이미 단일 경로만 존재한다.** 요청별 강등도, 경로 간 이동도 없다.
- 확정은 `build_graph()` 안에서 **기동당 1회**뿐이다. 그래서 관측도 요청별 카운터가 아니라
  기동 로그 1줄이다(§5).
- 어느 단을 지우려면 **그 단이 확정되는 설정 조합이 실제로 쓰이지 않음**을 먼저 보여야 한다.
  코드에 분기가 남아 있다는 사실만으로는 아무것도 증명되지 않는다.

## 3. 1단의 활성 조건 — 두 단계로 나뉜다

```
select_orchestration_backend(config)        # ① 플래그 + 오케스트레이터 가용성
    └─ enable_deepagents_package AND orchestrator_available(config)
           ├─ provider=gemini : api_key 유무
           └─ provider=vllm   : /v1/models health check
_deep_agent_buildable(config, llm)          # ② 실제 조립 시도(폐쇄망 wheel 반입 확인)
    └─ build_deep_agent()이 RuntimeError면 False
```

②가 따로 있는 이유: ①이 통과해도 deepagents 패키지가 없으면 그래프 빌드가 크래시한다.
빌드 시점에 조립을 한 번 시도해보고, 실패하면 하위 단으로 안전 폴백한다.

## 4. 강등 사유 4종

`src/observability/ladder.py`가 판정한다. 사유 없는 강등은 진단이 불가능하다.

| 사유 | 의미 | 대응 |
|---|---|---|
| `none` | 정본(1단) 확정 | — |
| `flag_off` | `enable_deepagents_package`가 off | 운영 선택. 의도한 것인지 확인 |
| `orchestrator_unavailable` | 플래그는 on인데 오케스트레이터(vLLM/Gemini) 미가용 | health check·api_key 확인 |
| `package_missing` | 백엔드는 골랐으나 deepagents 조립 실패 | 폐쇄망 wheel 반입 |

## 5. 기동 로그 읽는 법

```
INFO  오케스트레이션 사다리 확정: tier=<단> degraded_reason=<사유> resolved_by=<출처>
WARN  정본 경로(deep_agent)가 아닌 <단> 단으로 확정됐습니다 (사유: <사유>). …   ← 비정본일 때만
```

- `tier` — 확정된 단 (§1의 이름)
- `degraded_reason` — §4의 사유
- `resolved_by` — `explicit_env`(플래그를 명시 설정) / `auto_multidb`(tri-state 자동 해석, §6)

**실측 (2026-08-20, 운영 `.env`):**

```
오케스트레이션 사다리 확정: tier=deep_agent degraded_reason=none resolved_by=explicit_env
```

→ 정본 1단 확정 · 강등 없음 · 플래그는 명시 설정 · **레거시 4단 미도달**.

확정 결과는 실패 트레이스 헤더의 `ladder` 필드에도 실린다(`logs/trace/<날짜>/<request_id>.jsonl`).
단이 다르면 노드 구성 자체가 다르므로, 이 값 없이는 `node_path`를 해석할 기준이 없다.

## 6. tri-state 플래그 주의

`enable_semantic_routing` · `enable_deepagent_orchestration`은 `bool | None`이다.
`None`이면 **멀티 DB 등록 여부로 자동 결정**된다(`config.py` `model_post_init`).

즉 운영 경로가 **DB 등록 상태에 종속**된다. DB를 하나 등록/해제하는 것만으로 확정 단이
바뀔 수 있다. 자동 해석이 발동했는지는 로그의 `resolved_by=auto_multidb`로만 알 수 있다 —
`model_post_init`이 `None`을 bool로 덮어쓴 뒤에는 명시 설정과 구별되지 않는다.

## 7. 모듈 의존 방향 — 상위 단이 하위 단 모듈을 **재사용한다**

배선은 배타적이지만(§2), **모듈 의존은 배타적이지 않다.** 1단은 2단의 구현을 도구로 쓰고,
2단은 3단의 분류기를 쓴다. 이것이 "트랙 A를 지우면 트랙 B가 깨진다"의 실체다.

```
1단 deep_agent
  └─ src/orchestration/deep_agent.py:19   → deepagents_tools.build_tools
  │    └─ deepagents_tools.py:20          → intent_planner.has_alarm_signal          [2단 모듈]
  │    └─ deepagents_tools.py:21          → subagents.SUBAGENT_REGISTRY               [2단 모듈]
  │                                          subagents._extract_identity_rows
  │                                          subagents._make_isolated_input
  └─ src/orchestration/deep_agent.py:460  → result_aggregator.result_aggregator       [2단 노드]

2단 intent_orchestration
  └─ src/orchestration/subagents.py:48    → routing.semantic_router.MIN_RELEVANCE_SCORE  [3단 모듈]
                                             routing.semantic_router._llm_classify
```

**따라서:**

- **2단 모듈을 지우면 1단이 import 단계에서 깨진다.** 1단이 정본이므로 운영 전체가 멈춘다.
  "2단 배선이 안 쓰인다"는 관찰은 "2단 모듈이 안 쓰인다"를 함의하지 않는다 — 이 구별을
  놓친 것이 `plans/70` v1 오독의 정확한 지점이다.
- **3단 모듈을 지우면 2단이 깨지고, 연쇄로 1단이 깨진다.**
- 폐기 검토 시 확인할 것은 **배선 도달 가능성이 아니라 역방향 import**다(D-143 ② 4항).

## 8. 삭제 금지 — D-037 명시

> **semantic_router 로직 삭제 금지(재사용). `route_after_semantic_router` /
> `_INTENT_ROUTE_MAP` 삭제 금지(하위호환).**

보존 대상은 **하위 단**이다. 상위 단(1·2단)을 폐기할 근거는 어디에도 없다.
`plans/49:55` — *"Track-A Phase 2(기 구현)의 성공기준은 **폴백 경로로 유지**된다"*.

경로·모듈 폐기를 제안하려면 **D-143 ② 4항 실측**(운영 `.env` 실제값 / 패키지 실 설치·서빙
상태 / 브랜치 한정 `git log` 최종 수정일 / 역방향 import)을 첨부해야 한다. 하나라도 빠진
폐기 제안은 반려된다.

## 9. 명명 부채 (미해소)

`enable_deepagent_orchestration`이 가리키는 것은 **2단(트랙 A · 의도 분해)** 이고,
1단(트랙 B · deepagents 패키지)은 `enable_deepagents_package`다. 이름이 뒤섞여 오독을
유발한다 — plans/70 L2에서 개명 검토 대상.

## 참조

| 대상 | 위치 |
|---|---|
| 단 판정·기동 로그 | `src/observability/ladder.py` |
| 배선 | `src/graph.py` `build_graph()` |
| 백엔드 선택·가용성 | `src/orchestration/deep_agent.py` |
| 플래그 전수 감사 | `docs/flag_audit.md` |
| 폐기 규칙 | `docs/02_decision.md` D-143 |
| 정본 경로 평가 | `scripts/eval_text2sql.py --path deep_agent` |
