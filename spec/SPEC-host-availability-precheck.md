# Spec: 호스트 가용성 사전 판정 (Plan 81)

> 요구·검토 근거의 정본은 **`plans/81`** 이다. 여기에 배경을 복사하지 않는다.
> 착수 결정 사항: **D-175**(`docs/02_decision.md` 「채번 이력」 예약 등재 완료).
> **스코프 체크(Phase 0)**: 단일 역량이다 — 판정 코어 하나를 4개 소비 지점에 대칭 배선하는 일이며,
> 각 배선은 독립 출시 가능한 역량이 아니라 같은 계약의 슬라이스다. → **capability map 생략**.

## Objective

전원이 꺼졌거나 가용성이 비정상인 서버를 지목해 **실시간 프로세스 조회·OS 레벨 조사**를 요청하면,
지금은 사용자가 **원인을 알 수 없는 두 가지 응답** 중 하나를 받는다(`plans/81` §1.1 실측):

- *"프로세스 API 미응답/타임아웃. **잠시 후 다시 시도해 주세요**"* → 재시도해도 같다(오안내)
- *"현재 실행 중 프로세스 **0건**"* → `is_sufficient: True`로 반환돼 후단이 **정상 결과로 서술**(거짓 단정)

**이 스펙이 만드는 것**: 조회·조사 **진입 전에** 폴스타 자원 정보로 대상 가용성을 **결정적으로 판정**하고,
불가하면 호출을 생략한 뒤 **사유·근거·기준 시각**을 사용자에게 제공한다. 조사(`sre_agent`) 경로에는
그 판정을 **사실로 전달**해 죽은 대상에 조사 예산(300s·토큰)을 태우지 않는다.

**하지 않는 것**: ICMP/포트 프로브 신설 · 폴스타 밖 신규 데이터 소스 · `sre_agent` toolset/명령 allowlist 변경 ·
"전원이 꺼져 있습니다"라는 **단정**(`avail_status`는 Power off와 에이전트 통신 이슈를 구분하지 못한다 —
`realtime_usage.py:73-74` 2026-07-24 실측).

## 확정된 게이트 (2026-08-28 사용자)

| # | 사항 | 확정 |
|---|---|---|
| G-1 | 플래그 기본값 | **기본 on** — `COMPOSITE_AVAILABILITY_PRECHECK_ENABLED=true`. *"기본값은 현행 동작과 비트 동일"*(`plans/80` §5.4-③) 규약의 **명시적 예외**이며 근거를 D-175에 기록한다. off 경로의 비트 동일성은 **테스트로 계속 고정**한다 |
| G-2 | `unavailable` 대상 조사 요청 | **거부 + 사실 브리핑** — `sre_agent` 위임 없이 종결, 가용성 근거·기준 시각 응답 |
| G-3 | DOWN 계열 알람의 자동 조사 | **게이트 예외**(조사 진행) — 막으면 정작 필요한 조사가 사라진다 |
| G-4 | b0(DB2) 컬럼 존재 | **해소(실측)** — `AVAIL_STATUS` INTEGER · `IS_MAINTENANCE` SMALLINT 보유(`testdata/01_create_tables.sql:39,60`). **양 엔진 대칭 판정 가능**, 계획서가 우려한 비대칭 없음 |
| G-5 | `maintenance` 상태 프로세스 조회 | **경고 후 진행** — 점검 중이어도 서버는 살아 있을 수 있다 |

## Tech Stack

기보유만 사용 — **신규 라이브러리 0건**. Python 3.12/3.13 · LangGraph · pydantic v2 · pydantic-settings ·
`asyncio` · pytest(+`pytest-asyncio`). `sre_agent`는 자체 venv(Python 3.13 · holmesgpt 0.36.0).

## Commands

```bash
# 본체 + noise_gate 회귀 (e2e는 playwright 미설치 — 기준선과 동일 제외)
python -m pytest -q --ignore=tests/e2e

# 이번 작업 대상 스위트
python -m pytest -q tests/test_domain tests/test_orchestration tests/test_nodes noise_gate/tests

# sre_agent (별도 venv·별도 cwd)
cd sre_agent && python -m pytest -q

# 계층 규칙 (본체 + noise_gate 동시 검사)
python scripts/arch_check.py --ci

# 기준선 대조 — git stash 금지, 격리 사본 사용 (Known Mistakes)
git worktree add <dir> HEAD
```

## Project Structure

| 경로 | 계층 | 이 작업에서 |
|---|---|---|
| `src/domain/host_availability.py` | `domain` (의존 0) | **신규** — 결정표·문구 조립(순수 함수) |
| `noise_gate/infrastructure/polestar_hostname_resolver.py` | `infrastructure` | **확장** — SELECT에 컬럼 2개 · `resolve_with_status()` · `build_host_status_sql()` |
| `src/orchestration/process_query.py` | `orchestration` | 배선(단일·fan-out **대칭**) |
| `src/nodes/fault_diagnosis.py` | `application` | 배선(위임 전 게이트) |
| `noise_gate/application/nodes/investigation_trigger.py` | `application` | 배선(`target_state` 동봉 · DOWN 알람 예외) |
| `noise_gate/domain/investigation_payload.py` | `domain` | `meta.target_state` 추가 |
| `sre_agent/.../investigation_dispatcher.py` · `interface/mcp_service.py` | `application`(sre_agent) | 6번째 가드 · 선택 인자 |
| `src/config.py` | `config` | `CompositeConfig` 플래그 3개 |
| `tests/test_domain/` · `tests/test_orchestration/` · `noise_gate/tests/` · `sre_agent/tests/` | — | 신규 테스트 |

**계층 근거(D-171 선례)**: 소비자 4곳 중 둘이 `application`이므로 공통 판정은 `domain`에 둔다 —
`orchestration`에 두면 `application`이 import할 수 없어 대칭 배선 자체가 불가능해진다.

## Code Style

기존 모듈과 동일 — 한국어 docstring(Args/Returns), 결정 근거를 주석에 남기고, `from __future__ import annotations`.

```python
# src/domain/host_availability.py — domain 계층(허용 의존 set()): 외부 import 금지
AVAILABLE = "available"
UNAVAILABLE = "unavailable"
MAINTENANCE = "maintenance"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class HostAvailability:
    """대상 호스트 가용성 판정 결과 (Plan 81 §3.1).

    `state`만으로 동작을 가르지 않는다 — `reason`·`evidence`는 사용자 문구와 감사 양쪽이
    소비하며, 근거 없는 판정을 만들지 않기 위한 계약이다(D-035).
    """

    state: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    as_of: Optional[str] = None

    @property
    def blocks_collection(self) -> bool:
        """수집을 생략해야 하는 판정인지. **unavailable만 True** — unknown은 진행한다."""
        return self.state == UNAVAILABLE
```

## Testing Strategy

- 프레임워크: `pytest` + `pytest-asyncio`. 본체·noise_gate는 루트 `pytest`가 자동 수집, `sre_agent`는 자체 venv.
- 외부 호출 0 — 폴스타 DB/API·LLM은 전부 mock. **과금 API 호출 없음**(D-127 무관).
- 테스트 위치: 본체 `tests/test_domain/`·`tests/test_orchestration/`, `noise_gate/tests/`, `sre_agent/tests/`.
- **회귀 고정 3종**(이 작업의 핵심 안전망):
  1. `resolve()`의 반환형·동작·로그 문구 **불변**(`tests/test_orchestration/test_process_hostname_resolve.py` 21건 통과)
  2. 플래그 off 시 결과 dict **비트 동일**
  3. 단일·fan-out **양 경로 각각**에 판정 테스트(한쪽만 고치는 비대칭이 이 저장소의 반복 실수)

**기존 테스트 갱신 3건(구현 중 확정)** — 전부 **화이트박스 패치 지점**이 설계상 이동한 것이며,
각 테스트의 *의도*는 보존했다(수정 내역을 남긴다 — 조용한 기준선 완화 금지):

| 파일 | 무엇을 바꿨나 | 왜 |
|---|---|---|
| `tests/test_orchestration/test_process_hostname_resolve.py` | 패치 대상 `resolve` → `resolve_with_status` (2곳) | 실 호출 경로가 바뀌었다. `resolve`를 패치하면 대역이 쓰이지 않아 **테스트가 검증하려던 것을 검증하지 못한다** |
| `tests/test_composite/test_compaction_cache.py` · `test_target_fanout.py` | 패치 대상 `_resolve_canonical_hostname` → `_resolve_target_lookup` + `_lookup_targets` | 위와 동일. 반환값은 `lookup_failed` 판정으로 바꿔 **종전 의도(원시 값 폴백)를 그대로** 유지 |
| `tests/test_composite/test_target_fanout.py::test_single_target_keeps_legacy_shape` | 기대 키 집합에 `availability` 추가 | 판정 메타가 계약에 들어왔다. **off면 종전 키 집합 그대로**임을 `TestPrecheckOffIsBitIdentical`이 따로 고정한다 |

## Boundaries

- **Always**: 판정은 결정적(LLM 0회) · 판정 사유를 결과와 문구 양쪽에 노출 · 실패는 사유를 남기고 종전 경로로 진행 · 커밋 전 `pytest` + `arch_check --ci`
- **Ask first**: 결정표 값 변경 · 플래그 기본값 변경 · `sre_agent` 계약(payload/도구 시그니처) 확장 범위 확대 · 신규 DB 왕복 추가
- **Never**: ICMP/포트 프로브 추가 · "전원 off" 단정 문구 · `sre_agent`에서 DB 직접 조회(D-118) · `unknown` 판정으로 조회 차단 · 판정 로직 사본 생성(D-053)

## Success Criteria

1. 가용성 비정상 서버의 프로세스 조회 시 **프로세스 API가 호출되지 않고**(mock 호출 횟수 0), 응답에
   *"가용성이 비정상(중지/통신이상)"* + 기준 시각이 포함되며 *"잠시 후 다시 시도"* 문구가 **없다**
2. 같은 상황이 fan-out(N대상)에서도 동일하게 동작하고, 실패 요약이 **사유별로 분해**된다(`가용성 비정상 2건 / 타임아웃 1건`)
3. `is_sufficient`가 가용 불가 대상에 대해 **`False`** 로 반환된다(거짓 정상 서술 차단)
4. `fault_diagnosis`가 `unavailable` 대상에 대해 **`sre_agent_client`를 호출하지 않고** 사유를 응답한다
5. `investigation_trigger`가 `target_state`를 페이로드에 싣고, **DOWN 계열 알람은 예외로 조사를 진행**한다
6. `sre_agent` dispatcher가 `target_state.state == "unavailable"`이면 `target_unavailable` 사유로 즉시 terminal 확정하고, **필드가 없으면 종전과 동일하게 통과**(fail-open)
7. 대상 판정을 위한 **DB 왕복이 늘지 않는다** — 단일 경로는 기존 hostname 해소 쿼리 확장, fan-out은 배치 1쿼리
8. `python -m pytest -q --ignore=tests/e2e` 기준선 대비 **신규 실패 0**, `cd sre_agent && pytest` 회귀 0, `arch_check --ci` exit 0
   (기준선은 `git worktree add <dir> HEAD` 격리 사본으로 측정 — `git stash` 금지)
9. 플래그 off 시 결과 dict가 **비트 동일**(테스트로 고정)

## Open Questions

없음 — G-1~G-5 전부 확정(위 표). 착수 후 발생하는 결정은 D-175 본문 등재 시 반영한다.
