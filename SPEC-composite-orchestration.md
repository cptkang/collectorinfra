# Spec: 복합 질의 호스트 조사 오케스트레이션 (차수 3-A)

> 색인: `CAPABILITY-MAP-composite-orchestration.md`
> 수용 기준의 정본은 `plans/78` §5 각 Wave다. **여기에 복사하지 않는다**(`plans/80` §5.3-④).
> 착수 판정의 정본은 `plans/80` §5.2다. **여기에 순서를 쓰지 않는다**(§5.5-⑥).

## Objective

사용자 목표: *"CPU 사용량이 80% 이상인 서버를 조회하고 **해당 서버들**에서 동작 중인 프로세스를
보여줘"* — 지금은 **선행 결과가 후속 조사 대상으로 흐르지 않고**(G2), 대상이 여럿이어도 **1개로
절단**된다(G3). 두 갭은 `tests/test_orchestration/test_composite_host_scope.py`가 `xfail(strict)`로
고정해 두었다.

3-A는 그 흐름을 **끝까지 배선**한다 — 대상 해소(M1) → 관측 기반 마련(M2) → N-대상 fan-out(M3) ·
고수준 도구 배선(M4) → 인가(M5) · 충족도(M6) · 축약·캐시(M7) → 결과 소비(M8).

**3-A가 하지 않는 것**: e2e 수용 판정(S-1 후 = 3-B), 경로 선택 규칙(WU-18 = S-2 선행),
조사 실행 본체(`sre_agent`/`mcp_server` 소관), 권고 **생성**(`remediation.py` 소관).

## Tech Stack

기보유만 사용 — **신규 라이브러리 0건**(78 §4.7.4). Python 3.12/3.13 · LangGraph · pydantic v2 ·
pydantic-settings · `asyncio` · pytest(+`pytest-asyncio`).

## Commands

```bash
# 전체 회귀 (본체 + noise_gate 자동 수집). e2e는 playwright 미설치 — 기준선과 동일하게 제외
python -m pytest -q --ignore=tests/e2e

# 이번 작업 대상 스위트
python -m pytest -q tests/test_orchestration tests/test_nodes tests/test_dbhub \
                    tests/test_composite noise_gate/tests --ignore=tests/e2e

# 계층 규칙 (본체 + noise_gate 동시 검사)
python scripts/arch_check.py --ci

# 기준선 대조 — git stash 금지, 격리 사본 사용 (Known Mistakes)
git worktree add /private/tmp/claude-501/.../baseline HEAD
```

## Project Structure

| 경로 | 역할 | 이 작업에서 |
|---|---|---|
| `src/utils/prior_targets.py` ★ | 대상 해석 3단 + 결정적 확정 + `TargetRef` | **신규**(M1) · **위치 정정 C-1** |
| `src/utils/query_gen_common.py` | `looks_like_process_rows` 단일 출처 이동 | 수정(M1) |
| `src/security/audit_logger.py` | `log_investigation()` 추가 — 기존 `AuditEntry` 재사용 | 수정(M2) · **C-5** |
| `src/observability/investigation_metrics.py` | Tier 2 지표 4종 수집 | **신규**(M2) |
| `src/domain/host_authz.py` ★ | 호스트 인가 **순수 정책**(mode×role → 판정) | **신규**(M5) · **위치 정정 C-4** |
| `src/orchestration/investigation_cache.py` | 단기 조사 캐시(TTL·sweep) | **신규**(M7) |
| `src/orchestration/process_query.py` | 공통 모듈 호출 · N-대상 fan-out | 수정(M1·M3·M7) |
| `src/orchestration/subagents.py` | `_make_isolated_input` 2단 주입 | 수정(M1) |
| `src/orchestration/deepagents_tools.py` | 생산자·소비자 게이트 1단 주입 | 수정(M1) |
| `src/orchestration/agent_orchestrator.py` | 충족도 검증·1회 재계획 | 수정(M6) |
| `src/nodes/fault_diagnosis.py` | `_extract_targets` → 공통 모듈 · 브리핑 소비 | 수정(M1·M8) |
| `src/dbhub/client.py` | 고수준 도구 4종 배선 | 수정(M4) |
| `src/state.py` | `prior_targets`·**`user_role`** 필드 + 요청 스코프 초기화 | 수정(M1·M5) · **C-4** |
| `src/api/routes/query.py` | `user_role` 전파 3곳 | 수정(M5) · **C-4** |
| `src/orchestration/deep_agent.py` | ambient 전파 목록에 `user_role` | 수정(M5) · **C-4** |
| `src/config.py` | `CompositeConfig`(env_prefix `COMPOSITE_`) + `HOST_AUTHZ_MODE` | 수정(전 모듈) |
| `noise_gate/application/nodes/investigation_trigger.py` | 공통 모듈 경유(G5 대칭) | 수정(M1) |
| `tests/test_composite/` | 3-A 신규 테스트 | **신규** |

## Code Style

기존 코드와 동일하게 — 한국어 docstring(Args/Returns), 결정적 가드에는 **왜 그런지**를 주석으로
남긴다(실측 근거·D 번호 인용).

```python
def _reject(reason: str, detail: str = "") -> UnresolvedTargets:
    """대상 미확정을 **사유와 함께** 반환한다(침묵 폴백 금지 — 80 §5.4-④).

    검증 탈락 시 해석 3단으로 되돌아가지 않는다 — 무한 재시도 금지(78 W1-3-2).
    """
    return UnresolvedTargets(reason=reason, detail=detail)
```

- 신규 플래그는 **기본값이 현행 동작**이고 **기동 시 1회 해석**(78 P14).
- 강등·탈락·절단·폴백은 **구조화된 사유**로 남긴다. 로그만으로 끝내지 않는다.
- 기존 자산을 재사용한다 — `is_server_identity_col` · `is_demonstrative_identifier` ·
  `_extract_identity_rows` · `_looks_like_process_rows` · `select_top_processes`. **사본 금지**(D-053).

## Testing Strategy

pytest. 신규 테스트는 `tests/test_composite/<module_id 대응>.py`.

| 층위 | 무엇 | 이 작업에서 |
|---|---|---|
| 단위 | 결정적 로직(해소·검증·절단·TTL·인가 판정) | **전부 여기** |
| 계약 | 타입 계약(`ValidationError`)·상태 직렬화 형태(`dict`)·대칭 주입 | **전부 여기** |
| e2e | 실 LLM·실 호스트 | **하지 않는다** — 3-B(S-1 후) · D-127 |

- LLM이 필요한 경로(M1 해석 3단)는 **mock LLM**으로만 검증한다. 호출 횟수를 단언한다
  (1·2단 확정 시 **0회**).
- `xfail(strict=True)`로 고정된 W0 테스트는 해소되는 모듈에서 **마커를 제거**한다 —
  남겨두면 XPASS로 실패한다(그것이 설계 의도다).
- 회귀 판정은 **전체 실패 수가 기준선과 동일**함으로 한다(80 §5.4-①).

## Boundaries

**Always**
- 신규 플래그 기본 off → **비트동일** 회귀 0
- `python scripts/arch_check.py --ci` exit 0 (본체·`noise_gate` 양쪽)
- 단일/멀티 경로 **대칭 주입을 실측**(80 §5.4-⑤) — 1단(`deepagents_tools`)·2단(`subagents`) 양쪽
- 절단·실패·거부는 **응답에 노출**

**Ask first**
- `sre_agent`/`mcp_server` 쪽 코드 변경(별 패키지·별 venv)
- 새 D 번호 채번(`docs/02_decision.md` 3개 표 grep 후)
- 기존 플래그 기본값 변경

**Never**
- 실 LLM·실 호스트 호출 (**D-127** — 승인 없이는 `RUN_E2E=1` 설정조차 하지 않는다)
- 라우팅 결과·`relevance_score`·의도 분류 **단언** (3-A 조건)
- `src/routing/**` · `src/prompts/semantic_router.py` · `src/nodes/input_parser.py` **수정**(79 소유)
- `intent_planner`·`task_plan`·`TaskSpec` **수정**(R-13)
- `sre_agent` **import**(D-118) · 권고 **생성**(W4) · 변경 명령 **실행 경로**(D-003)

---

# 모듈 명세

각 절은 **Objective + Success Criteria delta**만 담는다. 수용 기준 정본은 `plans/78`.

## M1. `prior-targets` — WU-11 / 78 W1

**Objective**: 선행 task 결과에서 조사 대상 `[{hostname, ip, db_id}]`를 해소하는 **단일 모듈**을 만들고,
세 소비 경로(`process_query` · `fault_diagnosis` · `investigation_trigger`)가 **같은 함수**를 쓰게 한다.
G2(결과 의존 배선)와 G5(진입점 비대칭)를 **동시 해소**한다.

**Success Criteria** (정본: 78 W1 수용 기준)
- `TargetRef(BaseModel)` — 필수 키 누락·타입 불일치가 **`ValidationError`로 잡힌다**.
- 상태에 실리는 값은 **`dict`**(`model_dump()`) — 체크포인터 직렬화 회귀 방지.
- 해석 계단: 1·2단 확정 시 **LLM 호출 0회**. 미지 표면형은 3단으로 해소.
- 확정 검증: **결과 행에 없는 컬럼명**을 LLM이 반환해도 대상이 생성되지 않고 사유가 남는다.
  검증 탈락이 **3단 재호출 루프를 만들지 않는다**.
- 지시어("해당 서버")·**프로세스 행**(`pid` 보유)이 대상으로 오인되지 않는다.
- 상한(`COMPOSITE_MAX_TARGETS`) 초과 시 **절단 사실이 결과에 실린다**.
- 우선순위 ① 이번 턴 `filter_conditions` → ② `prior_targets` → ③ `previous_entities`
  → ④ 알람 페이로드. **①이 ②를 이긴다.**
- 대칭: 1단·2단 **양쪽**에서 대상 전달을 단언(2건).
- 범위: 변경분에 `intent_planner`·`task_plan` 수정이 **없다**(R-13).
- **`T-G2` xfail 마커 제거**(해소되었으므로).

## M2. `investigation-audit` — WU-14 / 78 W6 ★Tier 1

**Objective**: 조사 감사 레코드 스키마를 **생성**하고(계약 C-B v2 — 78이 소유), 실패 트레이스·기동
로그·**Tier 2 판별 지표 4종**(압축·캐시·라우팅·비용 귀속)을 남긴다. **M7이 이것을 선행으로 갖는다** —
측정 없이 최적화를 쌓지 않는다(78 §4.6.2).

**Success Criteria** (정본: 78 W6 수용 기준)
- 실패한 조사의 **전체 경로를 추적으로 재구성**할 수 있다.
- 지표 4종이 실제로 남는다 — Tier 2 착수 가능 판정의 근거.
- 레코드에 **인가 판정 결과 슬롯**이 있다(M5가 채운다 · W6-5).
- stdout 원문은 **마스킹 후** 저장.
- 트랙 C 재개 시 79가 신뢰도 필드를 **추가**할 수 있는 형태다(계약 C-B v2).

## M3. `target-fanout` — WU-12 / 78 W2-1~6

**Objective**: `run_process_query`를 N-대상 처리로 확장한다. 단일 대상은 **기존과 동일 경로**.

**Success Criteria** (정본: 78 W2 수용 기준 중 1~6 항)
- 3개 입력 → **3개 조사**. **`T-G3` xfail 마커 제거**.
- 1개 실패 시 나머지가 반환되고 **실패 사유가 응답에 노출**된다(개별 try/except).
- 전체 타임아웃(`COMPOSITE_TOTAL_TIMEOUT_SECONDS`) 상한이 실동작한다.
- 응답에 **조사 대상 수 / 성공 / 실패 / 절단 여부·수**가 포함된다.
- 단일 대상 경로 **회귀 0**.
- 같은 호스트 **동시 조사 1건으로 직렬화**(in-flight 키 `(db_id, hostname)`).
- 부하 가드 요구(`nice`·명령 timeout·`top -n 1`)가 **계약에 명시**된다 — 구현은 `sre_agent`.

## M4. `mcp-highlevel-tools` — WU-13 / 78 W3-1·4

**Objective**: `DBHubClient`의 기존 `_call_tool`로 `mcp_server` 고수준 도구 4종
(`polestar_process_snapshot`·`os_config`·`resource_status`·`metric_trend`)을 **신규 커넥터 없이** 호출한다.

**Success Criteria** (정본: 78 W3 수용 기준 1·4항)
- 반환 계약 `{rows, row_count, queried_at, source_kind, source, engine}`이 **그대로 소비**된다.
- 도구 이름이 기존 **동사+목적어 관례**를 따른다. 인자 스키마 **실행 전 검증**이 있고,
  실패는 **구조화 형태로 반환**된다.
- 도구 수를 늘리는 대신 **`profile` 인자로 흡수**한다.
- **읽기 전용 불변** — `execute_sql` 노출 정책(D-122 ④)을 건드리지 않는다.

## M5. `host-authz` — WU-15 / 78 W3-5

**Objective**: 조회 권한(`allowed_db_ids`)과 **조사 권한을 분리**한다. `HOST_AUTHZ_MODE=admin_only` —
**미설정·미상 값도 차단**(fail-closed).

**Success Criteria** (정본: 78 W3 수용 기준 인가 단언)
- 인가되지 않은 호스트는 조사가 **시작되지 않는다**(호출 0회).
- **채팅·이벤트 양쪽에서 동일하게** 막힌다(대칭 2건 — G5).
- 판정은 **위임·호출 직전**(실행 경계)에서 이뤄진다 — planner/LLM 경로가 아니다.
- 거부는 **조용히 건너뛰지 않고** 사유와 함께 결과·감사 레코드에 남는다(M2 슬롯).
- 미상 모드 값 → **차단**(fail-open 아님)을 단언한다.

## M6. `sufficiency-replan` — WU-16 / 78 W5

**Objective**: VMAO의 Verify를 **최소 형태로만** 도입한다 — 결정적 충족도 체크(LLM 미사용),
**1회만** 재계획, 실행 전 준비 검증, 대상 정합 사후 대조.

**Success Criteria** (정본: 78 W5 수용 기준)
- **무한 루프 부재** · **재시도 1회 상한** 단언.
- 미충족 사유가 응답에 **노출**된다.
- 조사 경로 **미가용을 조사 실패로 기록하지 않는다** — 착수 전 확인하고 사유 반환.
- **`prior_targets`에 없는 hostname이 결과에 실리면 오류로 잡힌다.**

## M7. `fanout-compaction` — WU-17 / 78 W2-7·8 (Tier 2)

**Objective**: 결정적 2단 축약과 단기 조사 캐시. **M2 완료가 선행 조건**이다.

**Success Criteria** (정본: 78 W2 수용 기준 7·8항)
- 절단 발생 시 **호스트별 절단 행 수**가 결과에 존재한다.
- **원문 전량 보존** — 상위 N만 남기지 않는다(§3.4.3-⑤ 조건).
- TTL 내 재조회는 **수집기를 호출하지 않고**, 응답에 **수집 시각**이 표기된다(실시간 오인 방지).
- TTL 경과 후 재수집한다. **만료 키가 sweep**된다(dict 무한 증가 없음).

## M8. `diagnosis-consumption` — WU-19 / 78 W4

**Objective**: 조사 브리핑 6요소와 `Remediation` 목록을 **소비·표시만** 한다. 생성하지 않는다.

**Success Criteria** (정본: 78 W4 수용 기준)
- 권고 생성 코드를 78이 **만들지 않는다**(중복 부재 단언).
- 위험도·신뢰도가 **원본 그대로** 표시된다 — "검토 필요" 강등 항목을 정식 권고처럼 보이게 하지 않는다.
- **변경 명령 실행 경로 부재** 단언 통과.
- 조사 실패·타임아웃·인가 거부 사유가 **노출**된다.

---

## 실측으로 닫은 Open Question · **계획 78 정정 5건**

명세 단계에서 `mcp_server/`·`src/api/dependencies.py`·`scripts/arch_check.py`를 **실측**한 결과,
`plans/78` §6.1이 정적 읽기만으로 세운 배치가 **성립하지 않는다**는 것이 드러났다.
아래는 정정본이며, `plans/78`·`plans/80`에 반영해야 한다.

### C-1 ★ `prior_targets` 위치 — `src/orchestration/` → **`src/utils/`**

`plans/78` §6.1은 *"arch_check.py 정합: orchestration → {infrastructure, domain} 유지"* 라고 적었는데,
이는 **모듈 자신의 나가는 의존만** 본 것이다. **소비자의 들어오는 의존**을 놓쳤다:

| 소비자 | 계층 | orchestration import 가능? |
|---|---|---|
| `src/orchestration/process_query.py` | orchestration | ✅ |
| `src/nodes/fault_diagnosis.py` | **application** | ❌ 허용 = {domain, config, utils, prompts, infrastructure} |
| `noise_gate/application/nodes/investigation_trigger.py` | **application** | ❌ 동일 |

W1-4가 요구하는 **3경로 공통화(G5)** 자체가 orchestration 배치에서는 불가능하다.
→ **`src/utils/prior_targets.py`**. 근거 셋:
1. `utils`는 최하위라 **모든 계층이 import 가능**하다.
2. `noise_gate → src.utils`는 **이미 존재하는 허용 최소 집합**(D-139 · `src.utils.json_extract` 3건 실측) —
   역방향 결합이 **신설되지 않는다**.
3. 선례 정합 — 동종 자산(`is_server_identity_col`·`is_demonstrative_identifier`·`HOST_IDENTIFIER_FIELDS`·
   `collect_prior_identity_values`)이 **이미 전부 `query_gen_common.py`(utils)** 에 있다.

**대가**: `utils` 허용 의존이 `set()`이라 `src.config`를 import할 수 없다 → 상한·플래그는 **인자로 주입**한다.
Redis 유사어(2단)·LLM 컬럼 지목(3단)도 **주입 콜러블**로 받는다. 테스트 용이성은 부수 이득이다.

### C-2 ★ `TargetRef` 필드 — `{hostname, ip, db_id}` → **`{server_name, hostname, ip, db_id}`**

`mcp_server/mcp_server/polestar_tools.py` 실측 — **식별 키가 도구마다 갈린다**:

| 도구 | 식별 인자 |
|---|---|
| `polestar_metric_trend(source, server_name, kind, granularity, periods)` | **`server_name`** |
| `polestar_resource_status(source, server_name)` | **`server_name`** |
| `polestar_os_config(source, hostname)` | `hostname` |
| `polestar_process_snapshot(hostname, top_n, sort)` | `hostname` · **`source` 인자 없음** |

`hostname`만 담으면 **4종 중 2종을 호출할 수 없다**. `fault_diagnosis._extract_targets`도 이미
`(server_name, hostname, db_id)`를 반환하고, `process_query`는 `_resolve_canonical_hostname`으로
**서버명↔호스트명을 구분**한다(D-046 — 폴스타는 name≠hostname). 둘을 **한 필드로 뭉개면 0건**이 된다.

### C-3 고수준 도구 이름 — **`polestar_` 접두**

`plans/78` W3-1은 `os_config`·`resource_status`·`metric_trend`로 적었으나 실제 등록명은
`polestar_os_config`·`polestar_resource_status`·`polestar_metric_trend`·`polestar_process_snapshot`이다.
반환 계약은 계획대로 `{rows, row_count, queried_at, source_kind, source, engine}`이며,
오류는 **`{error}` 단일 키**다(`_ok`/`_err` 실측).

### C-4 ★ 인가 — role 클레임이 **`AgentState`에 전파되지 않는다**

`src/api/dependencies.py` 실측: 판정 재료는 `current_user["role"] == UserRole.ADMIN.value`(`"admin"`).
그런데 `query.py`가 초기 state에 싣는 것은 `user_id`·`user_department`·`allowed_db_ids`뿐 —
**`role`이 없다**. 즉 W3-5를 *"실행 경계에서 판정"* 하려면 78이 예상하지 않은 **전파 배선**이 선행한다:
`state.py` 필드 → `query.py` 3곳 → `subagents._make_isolated_input` → `deep_agent` ambient 목록.

또한 인가 게이트 모듈은 **`src/domain/host_authz.py`**(순수 정책)로 둔다 — C-1과 같은 이유로
`src/orchestration/`은 불가하고, 정책 판정은 I/O가 없어 domain이 맞다(`domain` 허용 의존 = `set()`).

**이벤트 경로의 주체**: 알람 자동 조사에는 사용자가 없다. `system` 주체를 명시 도입하고,
`admin_only`는 `admin`과 `system`만 허용한다. **미상 role·미상 mode는 전부 차단**(fail-closed).

### C-5 감사는 **신규 모듈이 아니라 기존 자산 확장**

`src/security/audit_logger.py`에 `AuditEntry` + `_write_audit_file` + 날짜별 JSONL이 **이미 있다**.
신규 감사 모듈을 만들면 감사 경로가 두 벌이 된다(D-053) → **`log_investigation()` 추가**로 간다.
Tier 2 **지표**는 성격이 다르므로(감사=규정 준수, 지표=최적화 판정 재료 — `ObservabilityConfig`
docstring이 이 구분을 명시한다) `src/observability/`에 분리한다.

**이벤트 경로 감사**는 `investigation_trigger`가 **이미 `decision_store`에 남긴다** — 중복 배선하지
않는다. 따라서 `noise_gate → src.security` 역방향 결합은 **신설되지 않는다**.

---

## 남은 Open Question

| # | 질문 | 처리 |
|---|---|---|
| Q4 | `admin_only`에서 **`system` 주체 허용**이 옳은가 — 알람 자동 조사를 막으면 CW-A가 무력화되고, 열면 인가 우회로가 된다 | **잠정 허용 + 감사 필수**로 구현하고 사용자 확인을 받는다. 막는 쪽이 필요하면 mode 값 추가로 해소된다 |
| Q5 | C-1~C-5를 `plans/78`·`plans/80`에 **언제 반영**하는가 | 구현 완료 후 일괄(문서 정정 축) — `plans/80` §5.3-⑤ "완료 시 표의 상태만 갱신" 정합 |
