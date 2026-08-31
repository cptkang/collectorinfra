# 81. 호스트 가용성 사전 판정 — 프로세스·OS 조회 및 조사 진입 전 게이트

> 작성일: 2026-08-28 | **상태**: **구현 완료 (2026-08-28 · D-175 등재)** — W1~W7 전건.
> 신규 테스트 86건(본체 68 + `sre_agent` 18) · `sre_agent` 265 passed · arch_check 0 · 신규 실패 0.
> 구현 계약은 **`SPEC-host-availability-precheck.md`**, 태스크는 `tasks/plan-81.md`·`tasks/todo-81.md`.
> **요구(사용자, 2026-08-28)**: *"서버 프로세스 조회를 프롬프트로 요청할 때 지정한 서버가 power off
> 되어 있으면 조회 실패가 발생한다. 프로세스 조회나 OS 명령어로 조회 시 서버가 가용한지 사전에
> 판단하고 결과를 사용자에게 제공하는 기능을 추가하라. 이러한 처리는 OS 명령어뿐 아니라
> **SRE Agent에서도 처리해야 되는지 검토**한 후 계획을 수립하라."*
> **SRE Agent 검토 결론**: **필요하다 — 단 수단이 다르다.** 본체는 *조회를 막고 사유를 말하는* 게이트,
> `sre_agent`는 *죽은 대상에 조사 예산(시간·토큰)을 태우지 않는* 게이트다. 근거·대안 비교는 **§6**.
> **관련 결정**: D-003(읽기전용 절대원칙) · D-035(결정적=판단, LLM=보조) · D-046(서버명→hostname 해소) ·
> D-047-1(프로세스 선별·마스킹 결정적) · D-118(sre_agent 경계 — 양방향 import 0) ·
> D-119(관측 읽기 경계 = `mcp_server`) · D-171(공통 모듈 계층 배치) · D-172(기본값 충돌 시 `plans/80` §5.4 우선)
> **신규 결정 예약**: **D-175** (§10 · `docs/02_decision.md` 「채번 이력」 표 등재 완료)
> **관련 계획**: `plans/47-1`(프로세스 API) · `plans/71`(실시간 사용률 — **선례**) ·
> `plans/78`(조회↔조사 배선) · `plans/80`(78·79 공동 실행 계약) · `docs/25`(부하 가드 요구 계약)
> **실측 기준**: 현 브랜치 `multiintent` HEAD `c7d47e8` (2026-08-28 확인)

---

## 1. 문제 — 실측

### 1.1 재현 경로와 현행 동작

전원이 꺼진(또는 가용성 비정상인) 서버를 지목해 프로세스를 조회하면 **두 갈래**로 갈리는데,
**둘 다 원인을 말하지 못한다.**

| # | 실제 상황 | 코드 경로 | 사용자가 받는 문구 | 무엇이 잘못됐나 |
|---|---|---|---|---|
| ① | 프로세스 API가 응답하지 않음(타임아웃/비200) | `process_query.py:395` → `:667` | *"실시간 프로세스를 조회하지 못했습니다(프로세스 API 미응답/타임아웃). **잠시 후 다시 시도해 주세요**."* | **오안내**. 서버가 꺼져 있으면 재시도해도 같다. 사용자는 원인을 API 장애로 오인한다 |
| ② | API가 200 + 빈 `data.list` 반환 | `polestar_process_api.py:134` → `process_query.py:678` | *"서버 'X'의 현재 실행 중 프로세스 **0건**"* | **거짓 단정**. "프로세스가 없다"와 "서버가 꺼져 있다"는 전혀 다른 사실인데 전자로 보고된다 |
| ③ | 다대상 fan-out에서 대상 1건이 위 상태 | `process_query.py:448` | *"실패 대상: X(**대상 타임아웃**)"* | 원인 미상 — 재시도 대상인지 아닌지 판단 불가 |

②가 특히 위험하다. `is_sufficient: True`로 반환되므로 후단(`output_generator`)이 **정상 결과로 서술**한다.
CLAUDE.md Known Mistakes의 *"침묵적 폴백/강등 금지"*·*"0건/실패 진단은 진입·게이트별로 끊긴 지점부터 확정"*에
정면으로 걸린다.

### 1.2 이미 있는 것 — 경로 비대칭이 문제의 실체

가용성 표기는 **이 저장소에 이미 구현돼 있다.** 다만 **한 경로에만** 있다.

| 경로 | 가용성 판정 | 근거 |
|---|---|---|
| 실시간 사용률 조회 (Plan 71) | **있음** — 서버 목록 SQL에 `avail_status`를 포함해 행마다 `가용성` 컬럼을 붙이고, `미수집` 원인을 요약에 명시 | `realtime_usage.py:73-78`, `:230-245`, `:288-295` |
| 실시간 프로세스 조회 (D-046/D-047-1) | **없음** | `process_query.py` 전수 — `avail_status` 참조 0건 |
| 장애 진단 위임 (`fault_diagnosis`) | **없음** | `src/nodes/fault_diagnosis.py` 전수 |
| 알람 자동 조사 (`investigation_trigger`) | **없음** | `noise_gate/application/nodes/investigation_trigger.py` 전수 |
| `sre_agent` 조사 dispatcher | **없음** — 가드 5종(dedup·동시·타임아웃·예산·in-flight) 중 가용성 없음 | `investigation_dispatcher.py:196-231` |

> **실측 방법**: `grep -rn "avail_status" --include="*.py" src/ noise_gate/ mcp_server/` → `realtime_usage.py`·
> 프롬프트·골든 데이터 외 **0건**. `grep -rn "가용" …` → 조사/프로세스 경로 **0건**.

즉 신규 발명이 아니라 **선례를 나머지 3경로로 대칭 확장**하는 일이다(Known Mistakes: *"단일/멀티 경로 대칭 —
한쪽만 고치는 비대칭이 반복 원인"*).

### 1.3 쓸 수 있는 신호와 그 한계

| 신호 | 출처 | 무엇을 말하나 | 한계 |
|---|---|---|---|
| `cmm_resource.avail_status` | 폴스타 DB (0=정상, 1=비정상/DOWN, 2=알 수 없음 — `src/prompts/output_generator.py:30`) | 폴스타가 판정한 **가용성** | **전원 off를 특정하지 못한다** — Power off와 에이전트 통신 이슈가 같은 값 (`realtime_usage.py:73-74` 2026-07-24 실측) |
| `cmm_resource.is_maintenance` | 폴스타 DB | **계획된 점검** 중인지 | 점검 등록 운영 편차 |
| measurement 최근 수집 시각 | 폴스타 REST (Plan 71) | 데이터 신선도(`수집 지연`/`미수집`) | 수집 주기와 조회 시점 경합으로 **일시 미수집** 가능(재조회로 해소 — `realtime_usage.py:293-295`) |
| 프로세스 API 응답 형태 | 폴스타 프로세스 API | 미응답 vs 빈 목록 | 원인 미분해 |
| 알람 이력 (`cmm_alarm`) | 폴스타 DB / `polestar_alarm_history` | DOWN 알람 발생 시각 | 알람 정의 편차 |

**이 계획이 지키는 선**: 사용자에게 **"전원이 꺼져 있습니다"라고 단정하지 않는다.**
말할 수 있는 것은 *"폴스타 가용성이 비정상(중지/통신이상)이며 마지막 확인 시각은 …"* 까지다.
단정은 D-035(결정적 판단)의 근거 범위를 넘고, 실측이 그 구분 불가를 이미 확인했다(§1.3 1행).

### 1.4 하지 않는 것

- **ICMP ping·TCP 포트 스캔 신설 금지** — 본체에 네트워크 프로브 능력을 새로 여는 것은 보안 표면 확장이고,
  관측 데이터 읽기 경계(D-119)를 우회한다. 판정은 **이미 수집 중인 관측 데이터**로만 한다.
- **폴스타 밖 신규 데이터 소스 도입 금지** — 신규 라이브러리·프로세스 0건.
- **조사 실행 경로 변경 금지** — `sre_agent` toolset·명령 allowlist는 건드리지 않는다(D-118 · `docs/25` 소관).

---

## 2. 설계 원칙

1. **결정적 판정** — 가용성 판정에 LLM을 쓰지 않는다(D-035). 입력은 컬럼 값과 시각뿐이다.
2. **추가 왕복 0을 목표로 한다** — 단일 대상 경로는 **이미 `cmm_resource`를 조회하고 있다**
   (`polestar_hostname_resolver.build_hostname_sql:68-95`). 그 SELECT에 컬럼 2개를 더하면 왕복이 늘지 않는다.
3. **침묵 금지** — 판정 결과·근거·기준 시각을 **결과 dict와 사용자 문구 양쪽에** 싣는다.
4. **회귀 0** — 플래그 미설정 시 현행과 **비트 동일**(`plans/80` §5.4-③ · D-172).
5. **사본 금지(D-053)** — 판정 로직 1곳, 조회 SQL 1곳. 4개 소비 지점이 같은 모듈을 쓴다.
6. **계층 규칙 준수** — 소비자 중 둘이 `application`이므로 공통 모듈은 `domain`/`utils`에 둔다(**D-171 선례**).

---

## 3. 판정 모델

### 3.1 결과 타입 (순수 도메인)

```python
# src/domain/host_availability.py  — 의존 0 (domain 허용 의존은 set())
@dataclass(frozen=True)
class HostAvailability:
    state: str        # "available" | "unavailable" | "maintenance" | "unknown"
    reason: str       # 기계 판독 사유 코드 (avail_status_down / maintenance / no_record / stale_metrics / …)
    evidence: dict    # {"avail_status": 1, "is_maintenance": 0, "last_collected_at": "...", "source": "cmm_resource"}
    as_of: str | None # 판정 근거 데이터의 기준 시각
    message: str      # 사용자 문구 조각 (단정 금지 어휘 고정)
```

### 3.2 결정표

| `avail_status` | `is_maintenance` | 수집 신선도(선택) | 판정 | 사용자 문구(예) |
|---|---|---|---|---|
| 0 | 0 | 신선 | `available` | — (문구 없음, 종전과 동일 진행) |
| 0 | 0 | 미수집/지연 | `unknown` | "가용성은 정상이나 최근 수집값이 없습니다(수집 지연 가능) — 결과가 비어 있을 수 있습니다." |
| 0 | 1 | — | `maintenance` | "대상 서버가 **점검(maintenance) 상태**로 등록돼 있습니다." |
| 1 | — | — | `unavailable` | "대상 서버의 폴스타 **가용성이 비정상(중지/통신이상)** 입니다(기준 시각 …). 서버가 내려가 있거나 모니터링 에이전트와 통신이 끊긴 상태로, **실시간 프로세스 조회가 불가**합니다." |
| 2 | — | — | `unknown` | "대상 서버의 가용성이 **알 수 없음**으로 등록돼 있습니다 — 조회 결과가 비어 있을 수 있습니다." |
| 행 없음 | — | — | `unknown` | "대상 서버를 폴스타 자원 목록에서 찾지 못했습니다(서버명 확인 필요)." |
| 조회 실패 | — | — | `unknown` | (사유 로그만 — 종전 경로 그대로 진행, **회귀 0**) |

> 신선도 열은 **Tier 2**(§5 W3-옵션)다. 1단계는 `avail_status`·`is_maintenance` 2컬럼만으로 판정한다 —
> measurement 호출을 붙이면 왕복이 늘고 §2-2 원칙이 깨진다.

### 3.3 판정별 동작 정책 (권장안)

| 판정 | 프로세스/OS 조회 | 조사(`sre_agent`) 위임 | 근거 |
|---|---|---|---|
| `available` | 진행 | 진행 | 현행 동일 |
| `unavailable` | **수집 생략** + 사유 안내 | **위임 생략**(또는 다운 원인 축으로 축소 — §9 게이트 G-2) | 성공할 수 없는 호출에 타임아웃·예산을 태우지 않는다 |
| `maintenance` | **진행하되 경고 병기** | **위임 생략 권장**(점검 중 조사 = 오탐 생산) | 점검 중 상태는 장애가 아니다 |
| `unknown` | **진행 + 사유 병기** | 진행 + 사실 주입 | 판정 근거가 약할 때 차단하면 정상 조회를 막는다(거짓 차단 > 거짓 진행) |

**핵심 비대칭**: `unavailable`은 차단, `unknown`은 진행. *"판정이 확실할 때만 막는다"* — 게이트가 정상 조회를
잡아먹는 것이 이 기능의 유일한 회귀 위험이기 때문이다.

---

## 4. 배치 (계층 규칙 준수)

```
domain → config/utils → prompts → infrastructure → application → orchestration → interface → entry
```

| 신규/변경 | 파일 | 계층 | 이유 |
|---|---|---|---|
| **신규** | `src/domain/host_availability.py` — 결정표·문구 조립(순수 함수) | `domain` | 소비자 4곳 중 둘이 `application`(`fault_diagnosis`·`investigation_trigger`) → `orchestration`에 두면 import 불가. **D-171과 동일한 실수를 반복하지 않는다** |
| **확장** | `noise_gate/infrastructure/polestar_hostname_resolver.py` — SELECT에 `avail_status`·`is_maintenance` 추가, `resolve_with_status()` 신설, 기존 `resolve()`는 얇은 래퍼로 보존 | `infrastructure` | **추가 왕복 0**. 호출부 2곳(`process_query.py:265-270`, 테스트)만 영향 |
| **확장** | 동 파일 — `build_host_status_sql(db_id, values[], engine)` (IN 절 배치 조회) | `infrastructure` | fan-out N대상을 **1쿼리**로. 엔진 분기(DB2 `FETCH FIRST`/무스키마) 기존 규약 재사용 |
| **배선** | `src/orchestration/process_query.py` | `orchestration` | 단일·fan-out **양쪽 대칭** 배선 |
| **배선** | `src/nodes/fault_diagnosis.py` | `application` | 대상 해소 직후 |
| **배선** | `noise_gate/application/nodes/investigation_trigger.py` | `application` | 알람 자동 조사 |
| **확장** | `sre_agent/sre_agent/application/investigation_dispatcher.py` — 가드 6번째 | `application`(sre_agent) | §6 참조 |
| **확장** | `src/config.py` `CompositeConfig` | `config` | 플래그 3개 |

> `noise_gate → src.domain`·`src → noise_gate.infrastructure`는 **이미 존재하는 허용 결합**(D-139 · D-171)이라
> 역방향 결합이 신설되지 않는다. `scripts/arch_check.py --ci` exit 0이 수용 기준이다.

---

## 5. 작업 Wave

### W1 — 판정 도메인 + 조회 확장 *(선행조건 없음)*

- `src/domain/host_availability.py`: `HostAvailability`, `judge_availability(avail_status, is_maintenance, *, last_collected_at=None, now=None) -> HostAvailability`, `format_notice(av) -> str`
- `polestar_hostname_resolver`: `build_hostname_sql`에 컬럼 2개 추가 · `resolve_with_status()` 신설 · `build_host_status_sql()` 신설
- **`resolve()`의 반환형·동작은 불변** — 기존 테스트(`tests/test_orchestration/test_process_hostname_resolve.py` 13건) 무수정 통과가 수용 기준
- 검증: 결정표 전 행 단위 테스트 · SQL 골든(PG/DB2 방언 각각) · 컬럼 대소문자 편차(`_row_value`) 방어

### W2 — `process_query` 배선 *(W1 선행)*

- `_collect_one_target`: `_resolve_canonical_hostname` → `resolve_with_status`로 교체(왕복 증가 0),
  `unavailable`/`maintenance`면 **프로세스 API 호출 자체를 생략**하고 `{ok: False, reason: "host_unavailable", availability: {...}}` 반환
- `_fanout`: 대상별 판정을 **1회 배치 SQL**로 선취(`build_host_status_sql`) → 불가 대상은 수집 대상에서 제외
- **대칭 검증 필수** — 단일 경로만 고치는 것이 이 저장소의 반복 실수다(Known Mistakes). 두 경로 각각에 테스트를 건다
- 부수 이득: 죽은 호스트로의 API 호출이 사라져 fan-out 총 소요가 줄고, `_inflight_lock` 점유가 없어진다

### W3 — 결과·문구 *(W2 선행)*

- `organized_data.summary`: 판정 문구를 **맨 앞**에 배치(사용자가 표보다 먼저 읽는다)
- `process_query` 메타에 `availability: {hostname: {state, reason, as_of}}` 추가 — 감사·UI가 소비
- fan-out 요약: `실패 N건`을 **사유별로 분해**(`가용성 비정상 2건 / 타임아웃 1건`) — 재시도 가치가 있는 실패와 없는 실패를 구분
- `_empty_result` 호출 시 `is_sufficient: False` 유지 + 재시도 유도 문구를 **판정에 따라 분기**
  (`unavailable`이면 *"잠시 후 다시 시도"* 문구를 **내보내지 않는다** — §1.1 ①의 직접 수정)
- *(옵션 · Tier 2)* measurement 신선도 신호 편입 — Plan 71 클라이언트 재사용, 왕복 1회 추가. **기본 off**

### W4 — 장애 진단 경로 *(W1 선행 · W2와 병렬 가능)*

- `fault_diagnosis`: `resolve_targets` 직후 판정 → `unavailable`이면 **위임 전 종결**하고
  *"대상 서버가 가용하지 않아 조사를 수행하지 않았습니다"* + 근거(기준 시각·마지막 알람) 응답
- 인가 거부 문구(`_DENY_MESSAGES`)와 **같은 형태**로 사유를 분기한다 — 선례가 이미 있다

### W5 — `sre_agent` 계약 확장 + 결정적 게이트 *(W1·W4 선행 · **§6 결론 반영**)*

- **본체**: 페이로드에 `target_state` 추가
  - 알람: `build_trigger_payload()`의 `meta.target_state`
  - 채팅: `sre_diagnose(question, server_name, hostname, db_id, target_state=None)` **선택 인자** 추가
  - 둘 다 **후방 호환** — `validate_payload`는 추가 키를 거부하지 않고(`investigation_jobs.py:98-116` 실측),
    MCP 도구는 기본값 `None`이라 구버전 호출자가 그대로 동작한다
- **`sre_agent`**: `_apply_sync_guards`에 **6번째 가드** 추가 — `target_state.state == "unavailable"`이면
  `"target_unavailable"` 사유로 즉시 terminal 확정 + 브리핑에 사실 명시.
  **필드가 없으면 종전과 동일하게 통과**(fail-open — 이 가드는 안전성 가드가 아니라 낭비 방지 가드다)
- `sre_agent`는 **DB를 조회하지 않는다** — 판정은 본체가, 게이트는 `sre_agent`가 한다(D-118 경계 유지)

### W6 — 알람 자동 조사 트리거 *(W1·W5 선행)*

- `investigation_trigger`: 트리거 직전 판정 → `target_state` 동봉
- **주의(설계 함정)**: 알람 자체가 *"서버 DOWN"* 알람일 수 있다. 이때 조사를 막으면 **정작 필요한 조사가 차단**된다.
  → **DOWN 계열 알람은 게이트 예외**로 둔다(알람 종류로 결정적 판정 · §9 게이트 G-3)

### W7 — 설정·감사·문서 *(전 Wave 병행)*

| 플래그 | 기본값 | 의미 |
|---|---|---|
| `COMPOSITE_AVAILABILITY_PRECHECK_ENABLED` | `false` | 전체 스위치. off면 **비트 동일**(§7) |
| `COMPOSITE_AVAILABILITY_BLOCK_ON_UNAVAILABLE` | `true` | off면 판정 문구만 붙이고 조회는 진행(관찰 모드) |
| `COMPOSITE_AVAILABILITY_STALENESS_ENABLED` | `false` | Tier 2 신선도 신호(왕복 +1) |

- 감사: `log_investigation()`에 `target_state` 필드 추가(신규 감사 경로 신설 금지 — D-171 선례)
- 문서: `docs/02_decision.md` D-175 등재 · `docs/25`에 "죽은 대상 조사 금지"를 L-5로 추가 · `plans/INDEX.md` 등재

---

## 6. SRE Agent에서도 처리해야 하는가 — 검토

### 6.1 결론

**필요하다.** 다만 본체 게이트와 **목적이 다르고**, `sre_agent`가 **스스로 판정해서는 안 된다.**

| | 본체 게이트 | `sre_agent` 게이트 |
|---|---|---|
| 목적 | 사용자에게 **정확한 사유**를 준다 | **조사 예산(시간·토큰·동시성)** 을 지킨다 |
| 수단 | 폴스타 DB 판정 → 조회 생략 + 문구 | 페이로드로 받은 사실 → 조사 거부 |
| 판정 주체 | **본체**(DB 접근 보유) | 없음 — **받은 사실만 소비** |

### 6.2 왜 본체 게이트만으로 부족한가 — 실측 4항

**① 원격 배치에서 실패가 "에러"가 아니라 "빈 데이터"로 나타난다.**
현행 운영 프로파일 `remote_vm_profile()`(`toolset_profiles.py:182`)은 **bash를 확장하지 않는다**
(`builtin_allowlist: "core"`, allow 빈 리스트). 대상 VM 데이터는 전부 MCP 도구(폴스타 DB/API·PromQL)로 온다.
전원이 꺼진 호스트에 대해 이 도구들은 **에러가 아니라 빈 결과**를 돌려준다 —
ReAct 루프는 "도구를 더 불러보자"로 반응하고, 그 결과 **조사 전체 타임아웃(기본 300s)까지 소모**한 뒤
근거 없는 서술을 낼 수 있다. 실측 조사 1건 소요 161s(`docs/25` 인용)이므로 무시할 비용이 아니다.

**② SSH 옵트인 시에는 실패가 곱해진다.**
`REMOTE_SSH_ALLOW = ["ssh"]`(`toolset_profiles.py:107`)를 `extra_allow`로 넘기는 구성에서는 실제 SSH 연결 실패가
발생하고, 무거운 명령은 `timeout 20 nice -n 10` 가드 형태로만 허용된다(`LOAD_GUARD_PREFIX`).
즉 **명령 1건당 최대 20초**를 죽은 호스트에 태우며, ReAct가 여러 번 시도하면 그대로 조사 예산이 증발한다.

**③ 진입점이 둘이고, 본체 게이트는 그중 하나만 막는다.**
`docs/25` L-4가 이미 같은 구조를 문서화했다 — *"본체 락은 본체를 경유하는 조사만 막고, `sre_agent`가 다른
경로(이벤트 자동 조사 등)로 같은 호스트를 동시에 잡을 수 있다."*
가용성도 똑같다. 알람 자동 조사·향후 추가 진입점은 본체 게이트를 통과하지 않는다.

**④ 요구의 성격이 부하 가드와 같은 계열이다.**
`docs/25`의 L-1~L-4는 *"조사가 대상을 악화시키지 않는다"* 를 말한다. 여기에 더할 것이 **L-5 —
*"조사할 수 없는 대상을 조사하지 않는다"*** 이다. 같은 문서, 같은 계약 형식으로 전달하는 것이 일관된다.

### 6.3 대안 비교 — `sre_agent`가 직접 판정하게 할 것인가

| 안 | 방식 | 평가 |
|---|---|---|
| **A (채택)** | 본체가 판정 → 페이로드 `target_state`로 전달 → `sre_agent`는 결정적 게이트만 | 신규 의존 0 · D-118 경계 무변경 · 후방 호환(추가 키/선택 인자) · 판정 로직 단일 출처 |
| B | `sre_agent`가 `mcp_server`의 `polestar_resource_status`를 **코드에서** 호출 | 현재 `sre_agent`에 **코드용 MCP 클라이언트가 없다**(MCP 등록은 LLM 도구 노출 전용 — `DiagnosisAgent(mcp_servers=…)`). 신규 클라이언트·인증·타임아웃·감사가 전부 추가된다. 판정 로직이 두 벌이 된다(D-053 위반) |
| C | LLM이 조사 첫 단계에서 `polestar_resource_status`를 부르게 지침 주입 | **비결정적**. `docs/25`가 이미 실측한 바 — *"`system_prompt_additions`를 넘기는 프로덕션 호출부가 0건이라 지침만으로는 아무것도 강제되지 않는다"* |

**A의 잔여 한계(명시)**: `target_state`가 없는 호출자(구버전·직접 MCP 호출)는 게이트를 통과한다.
이는 fail-open **의도된 설계**다 — 이 가드는 안전 가드가 아니라 낭비 방지 가드이므로, 정보가 없을 때
조사를 막으면 정상 조사를 잃는다. 커버리지가 필요해지면 B를 후속으로 검토한다.

---

## 7. 회귀 안전

| 항목 | 보장 방법 |
|---|---|
| 플래그 off 시 동작 | `COMPOSITE_AVAILABILITY_PRECHECK_ENABLED=false`(기본)면 판정 함수를 **호출하지 않는다** — SQL 컬럼 추가분도 소비되지 않아 결과 dict가 비트 동일 |
| `resolve()` 호출부 | 반환형·의미 불변. 기존 13건 테스트 무수정 통과가 수용 기준 |
| SQL 확장 위험 | `cmm_resource.avail_status`·`is_maintenance`는 DDL에 존재 확인(`testdata/pg/init/01_create_tables.sql:26,47`). DB2(b0) 스키마는 **착수 전 실측 필요**(§9 게이트 G-4) |
| `sre_agent` 계약 | `validate_payload`는 추가 키를 거부하지 않음(실측). `sre_diagnose` 신규 인자는 기본값 `None` |
| 기준선 | `pytest`(본체+noise_gate) · `cd sre_agent && pytest` · `scripts/arch_check.py --ci` 전부 착수 전 스냅샷 후 대조 |

---

## 8. 검증 계획

### 8.1 단위 (외부 호출 0 — 과금 없음)

| 대상 | 건수(목표) | 내용 |
|---|---|---|
| `judge_availability` | 8 | §3.2 결정표 전 행 + 경계(문자열 `"1"`, `None`, 미상 값) |
| `build_hostname_sql` / `build_host_status_sql` | 6 | PG/DB2 방언 골든 · IN 절 이스케이프 · 빈 목록 |
| `resolve_with_status` | 5 | 정상/0건/조회 실패/컬럼 대소문자 편차/`resolve()` 호환 |
| `process_query` 단일 | 5 | `unavailable`이면 **API 클라이언트가 호출되지 않음**을 mock 호출 횟수 0으로 단언 |
| `process_query` fan-out | 5 | 배치 판정 1쿼리 · 사유별 실패 분해 · 대칭성(단일과 같은 문구 규약) |
| `fault_diagnosis` | 3 | 위임 전 종결 · 사유 문구 · `sre_agent_client` 호출 0 |
| `investigation_trigger` | 3 | `target_state` 동봉 · DOWN 알람 예외(§9 G-3) |
| `sre_agent` dispatcher | 4 | `target_unavailable` terminal · 필드 부재 시 통과 · in-flight 해제 · 감사 레코드 |
| 플래그 off 회귀 | 3 | 결과 dict 비트 동일 |

### 8.2 통합·실환경

- **폴스타 DB 조회**(내부, 과금 없음): 가용성 비정상 서버 1대를 실제로 지목해 판정·문구 확인
- **프로세스 API**(내부, 과금 없음): ②의 실제 응답 형태(빈 `list` vs 미응답) 확정 — 지금은 **미확인**이다.
  로그 `프로세스 API data.list 비어있음(0건)`(`polestar_process_api.py:135`)으로 관측 가능
- **`sre_agent` 조사 완주**: LLM 실호출이 필요하므로 **D-127 승인 게이트 — 실행 건마다 사용자 승인**.
  `RUN_E2E=1` 설정·실행 자체가 승인 대상이다

---

## 9. 미해결 게이트 — 사용자 확정 필요

| # | 사항 | 선택지 | 권장 |
|---|---|---|---|
| **G-1** | 플래그 기본값 | (a) `false` / (b) `true` | **✅ 확정 (b) 기본 on** — `plans/80` §5.4-③의 **명시적 예외**(현행 동작 자체가 결함). off 경로 비트 동일성은 테스트로 고정 |
| **G-2** | `unavailable` 대상의 조사 요청 처리 | (a) 거부 + 사실 브리핑 / (b) 축소 조사 / (c) 그대로 진행 | **✅ 확정 (a)** — `fault_diagnosis`가 위임 전 종결, `sre_agent`는 6번째 가드로 거부 |
| **G-3** | DOWN 계열 알람의 자동 조사 | (a) 게이트 예외 / (b) 일괄 차단 | **✅ 확정 (a)** — `is_availability_alarm`(표면어 재현율 우선)로 결정적 판정 |
| **G-4** | b0(DB2) 컬럼 존재 | 실측 필요 | **✅ 실측 해소** — `AVAIL_STATUS` INTEGER · `IS_MAINTENANCE` SMALLINT 보유(`testdata/01_create_tables.sql:39,60`). **양 엔진 대칭**, 우려한 비대칭 없음 |
| **G-5** | `maintenance` 상태의 프로세스 조회 | (a) 경고 후 진행 / (b) 차단 | **✅ 확정 (a)** — 문구를 요약 맨 앞에 붙이고 조회는 진행 |

---

## 10. 산출물 · 영향 파일

| 구분 | 파일 |
|---|---|
| 신규 | `src/domain/host_availability.py` |
| 신규(테스트) | `tests/test_domain/test_host_availability.py` · `tests/test_orchestration/test_process_availability.py` · `noise_gate/tests/test_investigation_target_state.py` · `sre_agent/tests/test_target_unavailable_guard.py` |
| 변경 | `noise_gate/infrastructure/polestar_hostname_resolver.py` · `src/orchestration/process_query.py` · `src/nodes/fault_diagnosis.py` · `noise_gate/application/nodes/investigation_trigger.py` · `noise_gate/domain/investigation_payload.py` · `src/config.py` · `src/security/audit_logger.py` |
| 변경(`sre_agent`) | `sre_agent/sre_agent/application/investigation_dispatcher.py` · `sre_agent/sre_agent/interface/mcp_service.py` |
| 문서 | `docs/02_decision.md`(**D-175**) · `docs/25_host_investigation_load_guard.md`(L-5 추가) · `plans/INDEX.md` |

**D-175 예약 내용(요지)**: *호스트 가용성 사전 판정 — 판정은 본체(`src/domain/host_availability.py`)가
결정적으로 수행하고, `sre_agent`는 페이로드로 받은 사실만 소비하는 fail-open 낭비 방지 가드를 둔다.
`unavailable`만 차단하고 `unknown`은 진행한다. "전원 off"로 단정하지 않는다.*

---

## 11. 착수 순서

```
W1 (판정+조회)  →  W2 (프로세스 배선)  →  W3 (문구·메타)
       └──────→  W4 (장애 진단)  →  W5 (sre_agent 계약·게이트)  →  W6 (알람 트리거)
W7 (설정·감사·문서) — 전 구간 병행
```

**실제 진행(2026-08-28)**: 게이트 5건을 착수 전 확정(G-4는 실측 해소)한 뒤 W1→W7을 순서대로
랜딩했다. 계획 대비 변경 2건 — ①공용 조회 함수를 `orchestration`이 아니라
**`noise_gate/infrastructure`**(`lookup_host`/`lookup_hosts`)에 두었다: 소비자 셋 중 둘이
`application`이라 계층상 `orchestration`을 볼 수 없다(D-171과 **같은 함정**을 그대로 밟을 뻔했다).
②알람 페이로드의 `target_state`는 **값이 있을 때만 키를 만든다** — 항상 넣으면 판정 off·가용성
알람 예외 경로에서도 페이로드가 달라져 "미설정 시 종전과 동일"이 깨진다(계약 테스트가 이를 짚었다).
