# Spec: 호스트 조사 경로 선택 + 진입 게이트 (WU-18 · Plan 78 W3-2·W3-3)

> **상위**: `plans/80` §5 WU 표 — **WU-18** · **상세**: `plans/78` W3-2·W3-3 (+ W3-1 수용 기준 ②)
> **관련 결정**: D-035(결정적=판단·LLM=보조) · D-046/D-047(프로세스 교정 전례) · D-118(sre_agent 경계)
> · D-122(mcp_server 반환 계약) · D-164(조사 경로 분화)
> **작성일**: 2026-08-28 · **상태**: **구현 완료**(S1~S9 충족 · 신규 테스트 23건).
> **남은 것**: 경로 선택 *정확도* 판정과 e2e 수용은 **WU-05·06(G-BILL) 이후**(§0.1).

---

## 0. Scope Check — 왜 지금, 무엇까지

### 0.1 게이트 상태 — **G-BILL 스킵 승인**(사용자 2026-08-28)

WU-18은 `plans/80` §5.2에서 **선행 WU-06 · 게이트 G-BILL**로 묶여 있었다. 사용자가
*"g-bill은 스킵하고 나머지를 진행하라"* 로 지시해 **구현·단위 검증까지 진행**한다.

**이 선례는 이미 있다** — `plans/80` §5.5 순서 계약 ① (v9 정밀화):

> *"WU-05가 막는 것은 **e2e 수용 검증**이지 구현·단위 검증이 아니다."*

차수 3-A의 8개 WU가 정확히 이 근거로 G-BILL 앞에서 먼저 랜딩했다. WU-18도 같은 취급이며,
**WU-06이 공급하기로 한 것은 임계·키워드의 *튜닝 재료*이지 구조가 아니다.**

| 남는 것 | 처리 |
|---|---|
| 경로 선택 **정확도** 판정(어떤 질의가 어느 경로로 가야 옳은가) | **WU-06 이후로 이월** — 본 스펙은 정확도를 검증하지 않는다 |
| e2e 수용(복합 질의가 실제로 조사까지 가는가) | **WU-05 이후** (3-A 통합 수용 판정과 함께) |

### 0.2 무엇을 고치는가 — WU-13의 ◐

`DBHubClient.inspect_host()`는 완성돼 있고 테스트도 12건 있으나 **프로덕션 호출부가 0건**이다
(2026-08-28 실측: `grep -rn "inspect_host" src/` → 정의부·로그뿐, `tests/`에만 호출).

`plans/78` W3-1 수용 기준은 **두 조각**이고 뒤쪽이 비어 있다:

| 조각 | 상태 |
|---|---|
| ① 클라이언트가 도구를 부를 수 있는가 (`inspect_host`) | ✅ WU-13 |
| ② **언제 부를지 정해진 경로가 있는가** (경로 선택) | ❌ **본 스펙** |

②가 없으면 *"본체에서 호출되고 소비된다"* 가 성립하지 않는다. 완료로 표시했다가 놓친 이력이
`docs/18_known_mistakes.md`(2026-08-27)에 있다.

---

## 1. Objective

**중간 비용대의 공백을 메운다.** 현재 "서버 상태를 본다"는 요구가 갈 곳은 둘뿐이다 —
`data_query`(DB SQL)이거나 `fault_diagnosis`(sre_agent 위임 · 비쌈). 그 사이의
**OS 구성·자원 현황·메트릭 추세 단건 조회**가 통째로 비어 있다(`plans/78` §2.2 G1).

**성공의 모습**: 단건 호스트 조사 요구가 **결정적 규칙으로** `host_inspect`에 도달하고,
플래그가 꺼져 있으면 **현행과 비트 동일**하며, 이 상태가 테스트로 고정된다.

### 1.1 경로표 (`plans/78` W3-2)

| 요구 | 경로 | 비용 | 상태 |
|---|---|---|---|
| 프로세스 목록 | `process_query`(실시간 API) | 낮음 | 기존(D-046/047 결정적 교정) |
| **OS 구성·자원 현황·메트릭 추세 단건** | **`host_inspect`**(`mcp_server` 고수준 도구) | 낮음~중 | **본 스펙 신설** |
| 원인 분석·해결방안 | `fault_diagnosis` → `sre_agent` | 높음 | 기존(옵트인) |

---

## 2. 설계

### 2.1 신규 subagent `host_inspect` — **도구는 하나만 는다**

`plans/78` W3-4 *"적지만 더 나은 도구 — 도구 수를 늘리는 대신 `profile` 인자로 흡수한다"* 를
따른다. 4개 프로파일(`os_config`·`resource_status`·`metric_trend`·`processes`)을
**subagent 1개**가 흡수하고, 프로파일은 **결정적으로 판정**한다.

- 명명: `_TOOL_NAMES` 동사+목적어 관례 → **`host_inspect`**(호스트를 조사한다).
- 반환: `inspect_host`의 서버 계약(`{rows,row_count,queried_at,source_kind,source,engine}`)을
  **그대로** 싣는다. 실패는 예외가 아니라 `{error: 사유}` — 구조화 실패여야 다음 행동을 고를 수 있다.

### 2.2 진입 게이트 (W3-3) — **handler 한 곳**

`plans/78` P14가 구현 방식을 못 박는다:

```
✘ 금지: 조사 불필요 판정 → build_tools()에서 조사 계열 도구 제외
✔ 채택: 도구는 항상 등록 → handler 진입부에서 게이트(거부 사유를 구조화 반환)
```

**도구 목록은 플래그와 무관하게 고정**이다. 도구 정의는 직렬화 컨텍스트의 **접두부**라,
목록이 흔들리면 이후 전 턴의 KV 캐시가 무효화된다(캐시 토큰이 10배 싸다 — Manus).
가용성만 handler가 제어하고, 거부는 **사유를 구조화해 반환**해 모델이 대체 경로를 고르게 한다.

> **초안 정정(2026-08-28)**: 1차 구현은 "기동 시 목록 필터 + handler 게이트"의 **두 겹**으로
> 만들었다. `plans/80` §5.4-③(비트 동일성)을 지키려는 의도였으나, **P14가 명시적으로 금지한
> 방식**이었고 기존 테스트 3건이 이를 잡아냈다(`test_build_tools_exposes_five_named_tools` ·
> `test_orchestrator_instructions_list_every_exposed_tool` · `test_subagent_registry_has_expected_agents`).
> 필터를 걷어내고 P14대로 되돌렸다 — **계획서가 더 옳았다**.

**목록에 넣으면 프롬프트에도 넣어야 한다**: `ORCHESTRATOR_INSTRUCTIONS`의 "사용 가능한 도구"에
`inspect_host`를 등재한다. 누락되면 오케스트레이터가 존재를 모른 채 다른 도구로 대체하거나
지어낸다(`query_live_processes` 누락 실측 — Plan 67 Phase 0 ②).

### 2.3 경로 선택 규칙 (W3-2) — 결정적, **보수적으로**

`_coerce_process_intent`(D-046/047) 전례를 그대로 따른다 — LLM 분류를 **교정**하되 값을 만들지 않는다.

**발화 조건은 셋을 모두 만족할 때만**이다:

1. 현재 분류가 `data_query`다 (다른 경로는 건드리지 않는다)
2. **프로파일 키워드가 명시적으로** 있다 (아래 표 — 좁게 못 박는다)
3. **대상 호스트가 식별된다** (`filter_conditions`·`prior_targets`·`previous_entities`)

> **왜 이렇게 좁은가**: `data_query`는 본체의 주력 경로다. 여기서 욕심을 내면 정상 조회를
> 잠식하는데, **G-BILL 스킵으로 정확도를 측정할 수단이 없다**(§0.1). 측정 없이 넓히지 않는다 —
> 넓히는 것은 WU-06 이후의 판단이다. Known Mistakes: *"금지·교정 규칙은 범위를 좁게 못 박는다."*

| profile | 키워드(부분일치) |
|---|---|
| `os_config` | `os 정보` · `os정보` · `운영체제` · `커널` · `os 버전` · `os 구성` |
| `resource_status` | `자원 현황` · `자원현황` · `리소스 현황` · `리소스현황` |
| `metric_trend` | `메트릭 추세` · `지표 추세` · `사용률 추세` |

**충돌 시 우선순위**: 표의 선언 순서를 따르고, 판정 근거를 로그에 남긴다(침묵 금지).
**`processes`는 이 교정의 대상이 아니다** — `process_query`(실시간 API)가 이미 1급 경로다(D-041).

---

## 3. Commands

```bash
.venv/bin/python -m pytest tests/test_composite/ -q          # 본 스펙 범위
.venv/bin/python -m pytest -q --tb=no -p no:randomly         # 전체 회귀(기준선 대조)
.venv/bin/python scripts/arch_check.py --ci                  # 계층 검사
```

> **기준선**(2026-08-28 실측 · 위 명령 그대로): **36 failed · 5118 passed · 54 skipped · 24 errors**.
> 이 중 19건은 외부 인프라 미기동(DBHub·Redis)이다. `plans/80` §5.2 참조.

---

## 4. Project Structure

```
src/orchestration/host_inspect.py     → 신규: handler + 프로파일 판정 + 게이트
src/orchestration/subagents.py        → 레지스트리 등록 (목록은 고정 — P14)
src/orchestration/deepagents_tools.py → _TOOL_NAMES에 inspect_host 추가
src/prompts/orchestrator.py           → "사용 가능한 도구"에 inspect_host 등재
src/orchestration/intent_planner.py   → _coerce_host_inspect_intent (경로 선택)
tests/test_composite/test_host_inspect_routing.py → 신규
```

**신규 의존성 없음.** `DBHubClient.inspect_host`(WU-13)와 `get_db_client`(기존)를 재사용한다.

---

## 5. Boundaries

**Always do**
- 플래그 off = **분류 결과가 현행과 동일** — 교정이 발화하지 않는다(S5).
  도구 목록·프롬프트는 P14에 따라 **플래그와 무관하게 고정**이므로 비트 동일성 대상이 아니다
- 교정·거부는 **사유를 로그·반환에 남긴다**(침묵 폴백 금지)
- 프로파일이 요구하는 식별자가 없으면 **다른 필드로 대체하지 않는다**(D-046 — 엉뚱한 호스트 방지)

**Never do**
- **도구 목록을 플래그로 거르기**(P14 — KV 캐시 무효화). 가용성은 handler가 제어한다
- **`intent_planner`에서 `src.utils.prior_targets` 직접 임포트**(R-13 소유 경계 — 80 §6).
  대상 신호 판정은 `host_inspect.has_target_signal()`이 감싼다
- `execute_sql` 노출 정책 변경(D-122 ④ — 읽기 전용 불변)
- 키워드 집합을 **측정 없이 넓히기**(§2.3 — WU-06 이후 판단)
- **실 LLM 호출**(D-127 — 건별 승인 없이 금지)

---

## 6. Success Criteria

**전부 자동 검증 가능하다. 정확도는 포함하지 않는다**(§0.1).

| # | 기준 | 검증 |
|---|---|---|
| S1 | 레지스트리에 등재된다(디스패치·`allowed_agents()`가 이 경로를 안다) | 멤버십 단언 |
| S2 | **도구 목록이 플래그에 의존하지 않는다**(P14) · 프롬프트에도 등재돼 있다 | `build_tools` 소스 단언 + `ORCHESTRATOR_INSTRUCTIONS` 단언 |
| S3 | 플래그 off인데 handler에 도달하면 **구조화 거부**를 반환한다(fail-closed) | 반환 dict에 사유 키 |
| S4 | 경로 교정은 **셋을 다 만족할 때만** 발화한다 | 키워드 없음 / 호스트 없음 / 비-`data_query` 각각 미발화 |
| S5 | 플래그 off면 **교정이 발화하지 않는다** | task 리스트 불변 단언 |
| S6 | 프로파일 판정이 결정적이다 | 키워드→profile 매핑 단언 |
| S7 | handler가 `inspect_host` 반환 계약을 **변형 없이** 싣는다 | 모의 클라이언트 반환값 통과 단언 |
| S8 | `execute_sql`을 부르지 않는다 | AST 단언(기존 `test_mcp_tools.py` 전례) |
| S9 | 전체 회귀 **기준선 불변** · `arch_check --ci` exit 0 | §3 명령 |

---

## 7. Open Questions

| # | 질문 | 영향 |
|---|---|---|
| Q1 | 키워드 집합이 실제 질의 분포를 얼마나 덮는가 | **WU-06 이후 판정** — 지금은 좁게 두고 측정 후 넓힌다 |
| Q2 | `metric_trend`가 `data_query`의 추세 SQL과 겹칠 때 어느 쪽이 옳은가 | 동일 — 현재는 **명시 키워드(`메트릭 추세`)일 때만** 가져온다 |
| Q3 | 다중 호스트 요구가 오면? | 본 스펙은 **단건**만(경로표가 "단건 조회"). N개는 W2 fan-out 소관 |
