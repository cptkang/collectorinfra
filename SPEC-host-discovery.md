# Spec: host-discovery (Wave 5 · 요구 2)

> 지도: `CAPABILITY-MAP-execution-groups.md` v4 · 계획: `plans/82` v6 §4.3~§4.4 · Wave 5
> 결정: **D-176 후속3**(착수 시 부기) · 선행: `group-registry`·`prior-scope-wiring`(1차 구현 완료)
> 사용자 확정: **U4=전수 순회** · **U5=되묻기** · **U6=순회 존 목록 명시** · **U7=LLM 합성 1회 + 결정적 폴백** · **U12=60초 TTL 기본 on**

## ASSUMPTIONS (실측 확인분 — 추정 아님)

1. 막다른 안내의 정확한 지점은 `src/orchestration/process_query.py:718-721`이다 —
   `if not db_id:` → *"프로세스 조회 대상 DB(위치)를 식별하지 못했습니다. 위치(예: 김포/여의도)를
   지정해 주세요."* `_resolve_db_id`(`:84`)의 ①~④가 전부 실패하면 여기로 온다.
2. `_resolve_db_id` 우선순위는 **① `task.db_ids` > ② `prior_targets` > ③ `previous_db_ids` >
   ④ 위치 신호**이며 ②는 1차(D-176)가 신설했다. 탐색은 **⑤**로 그 뒤에 붙는다 —
   앞 순위가 하나라도 성립하면 탐색은 발동하지 않는다.
3. 재사용 자산은 `noise_gate/infrastructure/polestar_hostname_resolver.py`에 있고 시그니처는
   **실측**했다:
   - `PolestarHostnameResolver(registry)` — 생성자 인자 1개
   - `resolve_with_status(db_id: str, value: str) -> HostLookup`
   - `lookup_many(db_id: str, values: list[str]) -> dict[str, HostLookup]`
   - `HostLookup` 필드 = `hostname` · `server_name` · `availability`
4. 가용성 판정은 `src/domain/host_availability.py`(Plan 81 · D-175)의 순수 함수다 —
   `judge_availability(*, found=True, avail_status=None, is_maintenance=None, as_of=None, lookup_failed=False) -> HostAvailability`.
   **DB 왕복이 늘지 않는다**(같은 행의 컬럼을 쓴다).
5. 존 그룹 분할은 `partition_execution_groups(db_ids)`(`src/routing/execution_groups.py`)이고
   **실측 확인**: `['polestar_b0','polestar_cm_gp','polestar_cm_yd']` → `[('polestar:bank','은행존'), ('polestar:common','공동존')]`.
6. 탐색 비용은 **존당 고정 SELECT 1회 · LLM 0회**다. 감사로그 918건 실측 p50 49~53ms →
   3존 ≈ **150ms**. text2sql 파이프라인(스키마 분석 존당 ≤20s + LLM 90k~136k tok) 대비 무시 가능.
7. `src → noise_gate` 의존은 **이미 존재**한다(D-048 워커 in-process 기동 · CLAUDE.md 명시).
   신설이 아니라 잔존 경로의 재사용이다.

## ★ 계획서에서 벗어나는 지점 1건 (근거 첨부)

**계획서 §4.4는 발동 조건 1을 *"대상 그룹의 `backend != "sql"`이거나 솔루션이 `requires: [host_location]`을
선언"* 이라고 썼다. 지금은 그 조건이 항상 거짓이다** — 레지스트리에 등록된 solution은 `polestar`
하나(`backend: sql`, `requires` 없음)이고 apm·dpm은 주석 처리된 미등록 상태다(실측 2026-08-28).

그대로 구현하면 탐색이 **영원히 발동하지 않는다**. 그러나 요구 2의 실제 증상은 솔루션 축이 아니라
**프로세스 API 경로**에서 난다 — `process_query`가 단일 대상 API라 존을 하나 골라야 하고, 그것이
`_resolve_db_id` 실패 시 막다른 안내로 끝난다.

따라서 발동 조건 1을 **"단일 대상 API 경로에 진입했는데 대상 존이 미해소"** 로 좁혀 읽는다.
`requires: [host_location]` 선언은 **함께 지원**하되(APM 편입 시 자동으로 살아난다) 그것만을
조건으로 삼지 않는다. 이 이탈은 §4.4의 의도(*단일 대상 API라 존을 하나 골라야 한다*)를 유지하며
**오늘 발동하는 형태로 구체화**한 것이다.

## Objective

*"abd00 서버의 프로세스를 조회하라"* 가 **막다른 안내로 끝나지 않게** 한다. 인가된 존을
`query_order` 순으로 순회해 서버의 소재를 확정하고, 확정된 존으로 본 조회를 이어간다.

성공은 **위치를 모르는 사용자가 위치를 묻지 않고도 답을 받는 것**이다.

## Tech Stack

Python 3.12 · pydantic v2 · pytest / pytest-asyncio. **신규 의존 0** · **LLM 0회**(탐색 자체).

## Commands

```bash
pytest tests/test_discovery -q
pytest tests/test_composite/test_process_availability.py -q
python3 scripts/arch_check.py --ci
python3 scripts/overfit_check.py --ci
```

## Project Structure

```
src/domain/host_discovery.py         신규 — 순회 결과 판정(0건/단일/다중) · 순수
src/orchestration/host_sweep.py      신규 — 존 순회 실행 + TTL 캐시 (I/O 경계)
src/orchestration/process_query.py   수정 — `_resolve_db_id` 실패 시 ⑤ 탐색 진입
src/config.py                        수정 — CompositeConfig 플래그 3개
src/state.py                         수정 — discovery_trace(요청 스코프)
tests/test_discovery/                신규
```

## Code Style

domain은 **순수**다 — I/O·LLM·전역 상태 없음. 순회 실행(`host_sweep`)만 I/O를 갖는다.

```python
@dataclass(frozen=True)
class ZoneHit:
    """한 존에서 찾은 대상 1건."""

    db_id: str
    zone_label: str
    hostname: str
    server_name: str
    availability: HostAvailability


@dataclass(frozen=True)
class SweepOutcome:
    """순회 결과 전체. **순회한 존 목록을 항상 담는다** — 0건일 때 그것이 답이다."""

    identifier: str
    swept: tuple[str, ...]          # 실제로 순회한 존 라벨(인가 필터 후)
    hits: tuple[ZoneHit, ...]
    errors: dict[str, str]          # {db_id: 사유} — 조회 실패는 "없음"과 다르다
```

## Testing Strategy

**전부 mock — LLM·네트워크·DB 미사용**(D-127 · DBHub `localhost:9099` CLOSED).
순회 실행은 콜백 주입으로 대체한다.

| 대상 | 검증 |
|---|---|
| 순회 범위 | `allowed_db_ids` ∩ 활성 폴스타 존만 — **권한 밖 존은 순회하지 않는다**(존재 여부 누출 차단) |
| 순회 순서 | `query_order`(은행존 → 공동존) · 입력 순서에 불변 |
| U4 전수 순회 | 첫 히트에서 멈추지 않는다 · `COMPOSITE_DISCOVERY_EARLY_EXIT=true`면 멈춘다 |
| U5 다중 히트 | 2건 이상 → clarification 반환 · **선택지가 발견된 존으로 좁혀진다** |
| U6 0건 | 순회한 존 라벨이 **전부** 문구에 나온다 · 조회 실패 존은 "확인 실패"로 구분 |
| 가용성 | 히트 행의 `avail_status`·`is_maintenance`로 병기 · **추가 왕복 0** |
| U12 캐시 | 60초 내 재호출은 순회 0회 · **0건은 캐시하지 않는다** · TTL 경과 후 재순회 |
| 배선 | `_resolve_db_id` ①~④가 성립하면 **탐색 미진입**(호출 수 0) · 플래그 off면 현행 문구 바이트 동일 |

## Boundaries

**Always**
- **인가된 존만 순회**한다 — 권한 밖 존의 서버 존재 여부가 새어나가면 안 된다.
- 0건은 **순회한 존 목록을 명시**한다(U6). 조회 실패 존은 "없음"이 아니라 **"확인 실패"** 로 구분한다.
- 다중 히트는 **되묻는다**(U5). 임의 선택 금지 — 오답을 정답처럼 만든다.
- 탐색은 **읽기 전용 SELECT**만. LLM 0회.
- 매 태스크 `arch_check --ci` 통과.

**Ask first**
- 탐색 결과를 멀티턴 승계 신호로 저장 (`previous_db_ids` 오염 위험 — 별건)
- `_resolve_db_id` 우선순위 ①~④ 변경 (D-143·D-176 계약)
- 프로세스 외 경로(실시간 사용량 등)로 탐색 확대

**Never**
- 첫 히트에서 조용히 중단 (U4 — 동명 호스트 은폐)
- 다중 히트에서 임의 선택
- **0건 결과를 캐시** (신규 등록 서버가 TTL 동안 안 보인다)
- 탐색 실패를 "서버 없음"으로 강등 (침묵 폴백 금지)

## Success Criteria

1. `"abd00 서버의 프로세스"` 가 위치 미지정으로 **막다른 안내로 끝나지 않는다**.
2. 서버가 한 존에만 있으면 **되묻지 않고** 그 존으로 본 조회가 이어진다.
3. 두 존 이상이면 **발견된 존으로 좁힌 선택지**로 되묻는다.
4. 0건이면 **순회한 존 목록**이 응답에 나오고, 조회 실패 존은 별도로 구분된다.
5. 인가 밖 존은 순회 대상에서 제외된다(테스트가 호출 목록으로 단언).
6. 60초 내 같은 식별자 재조회는 순회를 반복하지 않는다. 단 **0건은 캐시되지 않는다**.
7. 플래그 off면 기존 테스트 전량 통과 + 현행 안내 문구 **바이트 동일**.
8. `arch_check --ci` exit 0 · `overfit_check` 위반 0.

## Open Questions

없음 — U4·U5·U6·U7·U12 확정. §4.4 발동 조건의 이탈은 위 §에 근거 첨부.
