# 오케스트레이션 사다리 — 실행 경로 단일 출처

> **작성** 2026-08-24 (plans/70 P2-2 / L1) · **개정** 2026-09-17 (plans/102 L-4 · D-225 기준 전환) ·
> **재개정** 2026-09-23 (**D-251 — 기준 운영 단 2단 `intent_orchestration` · 3단은 비교 arm**) ·
> **대상 코드** `src/graph.py` `build_graph()` · `src/orchestration/deep_agent.py` · `src/observability/ladder.py`
>
> 이 문서는 "지금 어느 실행 경로로 도는가"의 **단일 출처**다. `graph.py`의 분기 주석과
> `.env` 주석은 여기로 수렴한다.

## 왜 이 문서가 있는가

`plans/70` v1이 `graph.py`의 `if/elif` 형태만 보고 실행 경로 4종을 **"대등한 4경로 병존"**
으로 읽고, 그중 일부의 폐기를 권고했다. 실제 구조는 **빌드 타임에 한 단만 확정되는 사다리**다
(당시 서술은 "1 정본 + 3 폴백의 강등 사다리"). 그대로 실행했다면 당시 운영 경로가 붕괴했다.

정적 읽기로는 "죽은 경로처럼 보이는 것"과 "실제로 죽은 경로"가 구별되지 않는다.
이 문서와 기동 로그(아래 §5)가 그 구별을 대신한다.

### 기준 재전환 — D-251 (2026-09-23) · 현행

사용자 지시 *"2단은 기본 운영 단으로 보고 3단은 비교하여 2단 대비 성능을 확인할 수 있도록 한다."*에 따라
**기준 경로(기준 운영 단)는 2단 `intent_orchestration`이다.** D-225의 3단 기준을 전면 개정했다.

- **3단 `semantic_router`는 2단 대비 성능을 재는 비교 arm**이다 — 시나리오 하네스 `--arm tier3_router`
  (`plans/110` 부록 B). 3단 기능 동등성(`plans/103` · D-226)은 계속 진행한다.
- **1단 `deep_agent`는 운영에서 끄고 부가 경로(opt-in)로 유지**한다(D-225 ② 유지). 코드 기본값은 이미 off다.
  운영 `.env`의 `ENABLE_DEEPAGENTS_PACKAGE=false` 반영은 사용자가 한다(`plans/110` 부록 B.4).
- **`enable_intent_orchestration` 미입력 = on**(§6). DB 등록과 무관하다는 성질(X-T11 해소)은 유지된다.
- 정본 판정 `ladder.py` `is_canonical` = `INTENT_ORCHESTRATION`. 사유 어휘 5종은 **바꾸지 않았다**(판독 도구
  호환) — 2단 확정은 `intent_flag_on`(이제 기준 단의 사유), 3단 확정은 `none`이고, 3단이 기준이 아니라는
  사실은 WARNING 문구가 말한다(§5).
- 아래 「기준 전환 — D-225」 절과 본문 중 "기준 경로(3단)" 서술은 **D-225 시점 기록**이다. 서로 다르면 이 절이 이긴다.

### 기준 전환 — D-225 (2026-09-17) · *D-251로 개정됨*

사용자 지시 *"기본은 시멘틱 라우터를 사용한다. … deepagents는 부가적으로 사용할 예정"*에 따라
**기준 경로는 3단 `semantic_router`다.**

- **1단 `deep_agent`는 부가 경로(opt-in)** 다 — 폐기 대상이 아니다(§8). 확정돼도 강등이 아니라
  opt-in 기록(INFO)이다.
- **2단 `intent_orchestration` 배선은 기본 off**다. 모듈은 3단 순차 러너가 재사용하므로 유지한다(§7).
- 종전 서술("1단 정본 + 3 폴백", 사유 `flag_off`)은 이 문서·`ladder.py`·소비처에서 새 기준으로 바꿨다.
- **운영 `.env`는 아직 1단을 명시한다**(세 플래그 모두 true). 운영 전환은 `plans/102` L-5 —
  3단 기능 동등성(`plans/103` · D-226) 완료와 사용자 확인 뒤다. 코드 기준만 먼저 바뀌었으므로
  지금 운영 설정으로 1단이 성립하면 기동 로그는 `tier=deep_agent` 첫 줄에 opt-in 안내(INFO) 1줄을
  더 낸다(§5). 1단이 성립하지 않으면 opt-in 실패 경고(WARNING)다.

## 1. 4단 구조

| 단 | 이름 | 진입 배선 | 활성 조건 (앞 단이 전부 불성립일 때) |
|---:|---|---|---|
| 1 (부가 경로 · opt-in) | `deep_agent` | `field_mapper → deep_agent → END` | `enable_deepagents_package` **AND** 오케스트레이터 가용 **AND** deepagents 패키지 조립 성공 |
| **2 (기준 경로 · D-251)** | `intent_orchestration` | `field_mapper → intent_planner → agent_orchestrator → [replanner 루프] → result_aggregator → END` | `enable_intent_orchestration` (미입력 = **on**, §6) |
| 3 (비교 arm) | `semantic_router` | `field_mapper → semantic_router → 조건부 분기` | `enable_semantic_routing` |
| 4 | `legacy` | `field_mapper → schema_analyzer` | 위 셋 모두 불성립 (`else`) |

**"앞 단이 전부 불성립일 때"가 핵심이다.** 확정 순서는 위에서 아래다 —
2·3단의 플래그가 켜져 있어도 1단이 성립하면 2·3단은 **노드조차 등록되지 않고**, 2단 플래그가
켜져 있으면 3단은 등록되지 않는다. 그래서 기준 단(2단)으로 돌리려면 1단 플래그가 off여야 하고,
3단(비교 arm)으로 돌리려면 1·2단 플래그가 모두 off여야 한다(D-251).

**대상 DB 선정 규칙 — 사다리 전 단 공용(plans/113 F-1·F-2 · D-246):** 이번 턴 원문 위치 힌트
(`parsed_requirements.target_db_hints`)의 결정적 고정은 **한 함수**(`routing/location_hints.pin_targets_to_hints`)가
정한다 — 해소는 폼필과 같은 `resolve_priority_db_ids` · 존 그룹 DB는 해소 집합으로 교체 · 존 없는 DB는 분류 결과
유지 · 상호배타(`ZONE_GROUP_EXCLUSIVE=true`)에서 두 존 그룹에 걸친 해소는 고정하지 않음 · 힌트 없음/해소 0건이면
종전 그대로. 3단은 라우터 LLM 분류 → 관련도 필터 → **고정** → 소유 검증 → 존 역질문 게이트. 1·2단(3단 순차
러너가 재사용하는 핸들러 포함)은 `classify_dbs` → **고정** → (고정 없을 때만) 승계 → 소유 제한이며, **복합 계획은
task 단위로 고정**한다(task 질의가 가리키는 원문 힌트 부분집합 · 원문에 없는 위치어는 무시 · 단일 task는 원문 전체).
3단 계획 루프의 복합 task도 라우터 고정 집합 안에서 같은 규칙으로 좁힌다. 멀티 DB 결과는 사다리 전 단이 지나는
`result_merger`가 순위 질의(최외곽 ORDER BY + 행 상한이 DB마다 같을 때)를 전역 재정렬하고(S-1), DB마다 1행인
스칼라 집계 질의는 DB별·전체 값을 코드로 계산한다(S-3 · 평균은 DB별만) — 원본 병합은 CSV용으로 보존.

## 2. 배타성은 런타임이 아니라 빌드 타임이다

노드 등록 자체가 배타적이다 (`src/graph.py`):

```python
if use_deep_agent:                                              # 1단 (부가 경로 opt-in)
    graph.add_node("deep_agent", ...)
if config.enable_intent_orchestration and not use_deep_agent:   # 2단 (배선 기본 off)
    graph.add_node("intent_planner", ...); ...
if config.enable_semantic_routing and not use_deep_agent:       # 3단 (기준 경로)
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
           ├─ provider=vllm   : /v1/models health check
           └─ provider=mlx    : /v1/models health check (vllm과 같은 경로 — 로컬 mlx_lm.server, plans/100)
_deep_agent_buildable(config, llm)          # ② 실제 조립 시도(폐쇄망 wheel 반입 확인)
    └─ build_deep_agent()이 RuntimeError면 False
```

②가 따로 있는 이유: ①이 통과해도 deepagents 패키지가 없으면 그래프 빌드가 크래시한다.
빌드 시점에 조립을 한 번 시도해보고, 실패하면 하위 단으로 안전 폴백한다.

## 4. 확정 사유 5종

`src/observability/ladder.py`가 판정한다. 사유 없는 확정은 진단이 불가능하다.
로그 필드명은 판독 도구 호환을 위해 `degraded_reason` 그대로다.

| 사유 | 확정 단 | 의미 | 대응 |
|---|---|---|---|
| `intent_flag_on` | 2단 | 1단 플래그 off · 2단 플래그 on(미입력 포함)으로 **기준 단** 확정(D-251) | — |
| `none` | 3단 · 1단 | 3단(비교 arm — 2단 플래그 명시 off) 확정, 또는 부가 경로(1단) opt-in 확정 | 3단을 의도하지 않았으면 `ENABLE_INTENT_ORCHESTRATION=true` 명시 |
| `semantic_routing_off` | 4단 | 1단 플래그 off · 2·3단 플래그도 off | 의도하지 않았으면 `ENABLE_INTENT_ORCHESTRATION=true` 명시 |
| `orchestrator_unavailable` | 2·3·4단 | 1단 플래그는 on인데 오케스트레이터(vLLM/Gemini/mlx) 미가용 | health check·api_key 확인 |
| `package_missing` | 2·3·4단 | 백엔드는 1단을 골랐으나 deepagents 조립 실패 | 폐쇄망 wheel 반입 |

- **opt-in 실패 사유(`orchestrator_unavailable`·`package_missing`)가 하위 단 사유보다 먼저다.** 1단을
  켰는데 못 올라갔으면 3단(기준)에 떨어져도 의도한 경로가 아니다. 시나리오 러너는 이 두 사유로
  확정된 프로파일을 INVALID로 본다(`scripts/scenario/server.py` `UNINTENDED_DEGRADATION` =
  `ladder.py` `OPTIN_FAILURE_REASONS`).
- **`flag_off`는 D-225로 폐기했다.** 종전에는 "1단 플래그 off"를 뜻했는데, 1단 off가 기준 상태가
  되면서 그 자체로는 사유가 아니다. 어느 비기준 단으로 갔는지를 `intent_flag_on`·`semantic_routing_off`가
  대신 말한다. 2026-09-17 이전 run 기록·로그의 `flag_off`는 종전 어휘다 — 새 어휘로는 확정 단에 따라
  2단 `intent_flag_on` · 3단 `none` · 4단 `semantic_routing_off`에 해당한다.

## 5. 기동 로그 읽는 법

```
INFO  오케스트레이션 사다리 확정: tier=<단> degraded_reason=<사유> resolved_by=<출처>   ← 항상 1줄 (형식 불변)
INFO  부가 경로(deep_agent) opt-in으로 확정됐습니다 — …                                 ← 1단 확정일 때만
WARN  부가 경로(deep_agent) opt-in이 성립하지 않아 <단> 단으로 확정됐습니다 (사유: <사유>). …  ← opt-in 실패일 때만
WARN  기준 경로(intent_orchestration)가 아닌 <단> 단으로 확정됐습니다 (사유: <사유>). …  ← 3·4단 확정일 때만(D-251)
```

추가 줄은 **최대 1줄**이다. 2단(기준) 확정 + 사유 `intent_flag_on`이면 첫 줄만 남는다.
첫 줄 형식은 바꾸지 않는다 — `scripts/scenario/server.py`가 정규식으로 읽는다.

- `tier` — 확정된 단 (§1의 이름)
- `degraded_reason` — §4의 사유
- `resolved_by` — `explicit_env`(플래그를 명시 설정) / `auto_multidb`(`enable_semantic_routing` 미입력 →
  멀티 DB 등록 여부로 자동 해석, §6) / `code_default`(`enable_semantic_routing`은 명시했고
  `enable_intent_orchestration`만 미입력 → 코드 기본값 **on**(D-251), §6)

**D-251 이후 기대 로그 (설정 미입력 + 멀티 DB):**

```
오케스트레이션 사다리 확정: tier=intent_orchestration degraded_reason=intent_flag_on resolved_by=auto_multidb
```

→ 기준 2단 확정 · 추가 줄 없음. `tests/test_observability/test_ladder_startup_log.py`
`test_unset_flags_with_multi_db_start_on_tier2`가 실제 `build_graph()`로 이 줄을 고정한다.
(D-225 시점 기대 로그는 `tier=semantic_router degraded_reason=none`이었다.)

**실측 (2026-08-20, 운영 `.env` · D-225 이전 어휘):**

```
오케스트레이션 사다리 확정: tier=deep_agent degraded_reason=none resolved_by=explicit_env
```

→ 당시 정본으로 본 1단 확정 · 플래그는 명시 설정 · **레거시 4단 미도달**. D-225 이후 같은 설정은
같은 첫 줄에 "부가 경로(deep_agent) opt-in으로 확정" INFO 1줄이 붙는다(운영 `.env`가 아직 1단이다 — 서두).

확정 결과는 실패 트레이스 헤더의 `ladder` 필드에도 실린다(`logs/trace/<날짜>/<request_id>.jsonl`).
단이 다르면 노드 구성 자체가 다르므로, 이 값 없이는 `node_path`를 해석할 기준이 없다.

## 6. tri-state 플래그 주의

`enable_semantic_routing` · `enable_intent_orchestration`은 `bool | None`이다
(`config.py` `model_post_init`이 `None`을 해석한다). 두 플래그의 `None` 해석이 다르다.

| 플래그 | `None`(미입력)일 때 | 기동 경고 |
|---|---|---|
| `enable_semantic_routing` (3단) | **멀티 DB 등록 여부로 자동 결정** — 활성 DB가 있으면 on | "멀티 DB 등록 여부로 자동 결정합니다" |
| `enable_intent_orchestration` (2단) | **항상 on** — DB 등록과 무관 (D-251 ① · 종전 D-225 ④는 항상 off) | "on으로 확정합니다 … 3단(비교 arm)으로 돌리려면 `ENABLE_INTENT_ORCHESTRATION=false`를 명시" |

3단 플래그는 여전히 운영 경로가 **DB 등록 상태에 종속**된다. DB를 하나 등록/해제하는 것만으로
3단↔4단이 바뀔 수 있다. 자동 해석이 발동했는지는 로그의 `resolved_by=auto_multidb`로만 알 수 있다 —
`model_post_init`이 `None`을 bool로 덮어쓴 뒤에는 명시 설정과 구별되지 않는다.

2단 플래그는 종전(D-037)에 3단과 같이 "멀티 DB면 자동 on"이었다. 그래서 3단 기준으로 운영하다
DB를 하나 더 등록하는 순간 2단으로 **조용히** 확정됐다(`plans/102` X-T11). D-225는 미입력을 항상 off로,
D-251은 **항상 on**(기준 단)으로 고정했다 — 어느 쪽이든 DB 등록과 무관하다. 3단 플래그를 명시한 채
2단만 미입력이면 `resolved_by=code_default`로 남는다.

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

**모듈 의존 방향은 D-225 기준 전환으로 바뀌지 않았다.** 바뀐 것은 1단이 정본이 아니라 부가
경로가 됐다는 사실뿐이다. 3단 순차 러너(`src/orchestration/sequential_runner.py`)도 2단 부품
(`_llm_decompose`·`agent_orchestrator`·`result_aggregator` — `agent_orchestrator`가
`subagents.run_data_query_pipeline`을 부른다)을 함수로 재사용하므로, 2단 배선이 기본 off여도
2단 모듈은 기준 경로의 의존 대상이다.

**따라서:**

- **2단 모듈을 지우면 1단이 import 단계에서 깨진다.** 3단 순차 러너도 같은 부품을 쓰므로
  기준 경로의 복합 질의까지 멈춘다.
  "2단 배선이 안 쓰인다"는 관찰은 "2단 모듈이 안 쓰인다"를 함의하지 않는다 — 이 구별을
  놓친 것이 `plans/70` v1 오독의 정확한 지점이다.
- **3단 모듈을 지우면 2단이 깨지고, 연쇄로 1단이 깨진다.**
- 폐기 검토 시 확인할 것은 **배선 도달 가능성이 아니라 역방향 import**다(D-161 ② 4항).

## 8. 삭제 금지 — D-037 명시

> **semantic_router 로직 삭제 금지(재사용). `route_after_semantic_router` /
> `_INTENT_ROUTE_MAP` 삭제 금지(하위호환).**

보존 대상은 **하위 단**이다. 상위 단(1·2단)을 폐기할 근거는 어디에도 없다.
`plans/49:55` — *"Track-A Phase 2(기 구현)의 성공기준은 **폴백 경로로 유지**된다"*.

**D-225 — 기준 전환은 폐기를 동반하지 않는다.** 3단이 기준 경로로 올라갔지만 1단 `deep_agent`는
**폐기 대상이 아니다.** D-161 ①(승격-폐기 동반 원칙)의 **명시 예외**다 — 사용자가 1단을 부가
경로로 쓸 예정임을 밝혔다(부가 사용 형태는 `plans/102` G-9). 2단도 배선만 기본 off이고 모듈은
3단이 재사용한다(§7). 삭제가 없으므로 아래 D-161 ② 4항 실측은 이번 전환에 해당하지 않는다.
1단·2단 중 어느 하나라도 지우려면 여전히 그 실측이 필요하다.

경로·모듈 폐기를 제안하려면 **D-161 ② 4항 실측**(운영 `.env` 실제값 / 패키지 실 설치·서빙
상태 / 브랜치 한정 `git log` 최종 수정일 / 역방향 import)을 첨부해야 한다. 하나라도 빠진
폐기 제안은 반려된다.

## 9. 명명 부채 — 해소됨 (2026-08-24, L2)

구 이름 `enable_deepagent_orchestration`이 가리키는 것은 **2단(트랙 A · 의도 분해)** 인데,
1단(트랙 B) 플래그 `enable_deepagents_package`와 이름이 뒤섞여 오독을 유발했다.

**`enable_intent_orchestration` / `ENABLE_INTENT_ORCHESTRATION`으로 개명**했다.
구 환경변수명은 `AliasChoices`로 계속 인식하되 **2027-02-20 폐기 예정**이다(D-161 ①).

### 구 키를 쓸 때의 침묵 손실 — 실측 2026-08-24

`AliasChoices`는 **소스 우선순위보다 별칭 순서를 먼저 적용한다.** 따라서:

| `.env` | OS env | 적용값 |
|---|---|---|
| `ENABLE_INTENT_ORCHESTRATION=true` | — | `True` |
| `ENABLE_INTENT_ORCHESTRATION=true` | `ENABLE_DEEPAGENT_ORCHESTRATION=false` | **`True`** ← 구 키가 무시된다 |
| — | `ENABLE_INTENT_ORCHESTRATION=false` | `False` |

보통은 OS env가 `.env`를 이기지만, **서로 다른 별칭**이면 그 규칙이 적용되지 않는다.
구 키로 오버라이드하려던 의도가 조용히 사라지므로, 구 키가 설정돼 있으면 기동 시
경고를 남긴다(`src/config.py` `model_post_init`). **두 키를 동시에 두지 말 것.**

구 키는 설정 카탈로그(D-129)에 등재하지 않는다 — 관리자 화면은 신 키만 편집한다.
다만 화면의 *실효값*은 `AppConfig`에서 읽으므로 구 키로 들어온 값도 정확히 표시된다.

## 참조

| 대상 | 위치 |
|---|---|
| 단 판정·기동 로그 | `src/observability/ladder.py` |
| 배선 | `src/graph.py` `build_graph()` |
| 백엔드 선택·가용성 | `src/orchestration/deep_agent.py` |
| 플래그 전수 감사 | `docs/flag_audit.md` |
| 폐기 규칙 | `docs/02_decision.md` D-161 |
| 기준 경로(3단) 평가 | `scripts/eval_text2sql.py --path semantic_router` — 확정 단이 3단이 아니면 전 항목 스킵 |
| 부가 경로(1단) 평가 | `scripts/eval_text2sql.py --path deep_agent` — 확정 단이 1단이 아니면 전 항목 스킵 |
| 시나리오 사전 점검 | `python -m scripts.scenario --preflight` — 3단·1단 OK · 2·4단·opt-in 실패는 주의 + 조치 |
| 기준 결정 | `docs/02_decision.md` D-225 · `plans/102` §3.7 |
