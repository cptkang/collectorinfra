# Spec: 조사 브리핑 키 계약 정합 (`plans/50` G6)

> 요구·실측 근거의 정본은 **`plans/50` §0.3 G6 · §9 개정 블록**이다. 배경을 복사하지 않는다.
> 모듈 id: **`briefing-contract`** (`CAPABILITY-MAP-50.md`) · 착수 결정: **D-197 예정**(미등재 — §Open).
> 의존: **없음**(맵의 루트 모듈) · 이 모듈에 의존: `diagnosis-briefing`.
> 사용자 확정(2026-09-02): **챗·알림 둘 다 즉시 노출**(플래그 없음 — 결함 수정이므로).

## Objective

`sre_agent`가 만드는 브리핑과 collectorinfra 두 소비자가 읽는 키가 어긋나 있다. 그 결과
**`briefing_builder`가 문서로 못 박은 "한계 서술 강제"가 출력단에서 소멸**하고, list·dict 값이
Python repr로 **운영자 알림에 그대로 나간다**.

### 실측 — 실 함수 호출 재현 (2026-09-02)

`build_briefing()`이 내는 형태를 두 소비자에 그대로 먹인 결과:

```
=== PUSH (WorkB HTML · _investigation_briefing_html) ===
타임라인: [&#x27;14:03 Mem 78%→95% ← polestar_metric_trend&#x27;, &#x27;14:06 OOM ← 근거 dmesg&#x27;]
병목: 메모리 포화
원인: 14:06 OOM ← 근거 dmesg
권고: {&#x27;items&#x27;: [&#x27;힙 상향 재기동&#x27;], &#x27;note&#x27;: &#x27;※ 실행은 운영자 승인 후 수동&#x27;}
citations_verified: True
summary: web-01 메모리 고갈 ← polestar_metric_trend

=== PULL (챗 · _briefing_to_text) ===
[타임라인] 14:03 Mem 78%→95% ← polestar_metric_trend
14:06 OOM ← 근거 dmesg          ← 라벨 없이 이어붙음
[권고] {'items': ['힙 상향 재기동'], 'note': '※ 실행은 운영자 승인 후 수동'}
[citations_verified] True
[summary] web-01 메모리 고갈 ← polestar_metric_trend
```

| 결함 | 원인 |
|---|---|
| **`limitations` 미도달** | 소비자 키가 `limitation`(단수). 폴백 루프는 스칼라만 통과시켜 list가 탈락 |
| **`hypotheses` 미도달** | 순서 목록에 없음 + list라 폴백 탈락. **가설 강등이 사용자에게 안 보인다** |
| **`severity` 미도달** | dict라 폴백 탈락. `sre-agent/02` §7이 요구한 `[중요도]` 헤더가 없다 |
| **repr 누출** | `recommendation`(dict)·`timeline`(push는 list) 을 `str(val)`로 렌더 |
| **`evidence` 영구 공백** | **생산자가 만들지 않는 키**를 소비자가 기다린다 |
| 영문 키 노출 | `citations_verified`·`summary`가 폴백 루프로 새어 원 키명으로 출력 |
| 두 경로 비대칭 | pull은 list를 줄바꿈 조인, push는 `str()` — 같은 브리핑이 다르게 보인다 |

**이 스펙이 만드는 것**: 브리핑 키 계약의 **단일 정본**과, 그것을 지키는 **공용 렌더러**.

**하지 않는 것**: 브리핑 내용 자체의 변경(rank·confidence·상대시각은 `diagnosis-briefing` 소관) ·
`briefing_builder`의 판정 로직 변경 · 알림 채널·전달 방식 변경(D-137 무변).

## 계약 결정

### ① 정본은 **생산자**다 — 그런데 그 정본은 이미 코드에 선언돼 있다

`sre_agent/application/briefing_builder.py`의 상수가 이미 6요소를 선언한다:

```python
BRIEFING_ELEMENTS = ("summary", "timeline", "bottleneck", "cause", "recommendation", "limitations")
```

`sre-agent/02` §7의 예시 브리핑도 `[요약][타임라인][병목][원인][권고][한계]`이고 **`[근거]`가 없다**
(근거는 타임라인 항목의 `← 도구명`으로 인라인 표기된다). 즉 소비자의 `evidence`·`limitation`은
**계약을 잘못 옮겨 적은 것**이다. 생산자를 바꾸지 않고 소비자를 정본에 맞춘다.

### ② 렌더 대상과 라벨 (확정)

| 키 | 타입 | 라벨 | 순서 | 비고 |
|---|---|---|---|---|
| `severity` | dict | `[중요도]` | 0 | `심각(신뢰도 high) · 상향 · 게이트 PAGE · 시그니처 oom_kill` |
| `summary` | str | `[요약]` | 1 | 현재 맨 아래 영문 키로 나오는 것을 올린다 |
| `timeline` | list[str] | `[타임라인]` | 2 | 항목마다 줄바꿈 + 들여쓰기 |
| `bottleneck` | str | `[병목]` | 3 | |
| `cause` | str | `[원인]` | 4 | |
| `recommendation` | dict | `[권고]` | 5 | `items` 줄바꿈 + `note` 말미 |
| `root_cause_hypotheses` | list[dict] | `[원인 가설]` | 5 | **`diagnosis-briefing`이 추가**(rank·confidence·evidence). `1) (high) 원인 — 근거: …` 로 편다 |
| `limitations` | list[str] | `[한계]` | 6 | **신규 도달** |
| `hypotheses` | list[str] | `[가설]` | 7 | **신규 도달**. 비어 있으면 생략 |
| `citations_verified` | bool | — | — | **미출력**. False면 생산자가 이미 `limitations`에 사유를 넣는다(중복) |
| `stub` / `elements` | — | — | — | 미출력(기존과 동일) |

- **모르는 키는 버리지 않는다** — 순서 목록에 없는 키가 오면 스칼라·list·dict 모두
  `[<키명>]`으로 말미에 렌더한다. 생산자가 필드를 늘려도 **침묵 누락이 생기지 않게** 한다
  (이번 결함의 재발 방지가 이 규칙 하나에 걸려 있다).
- **빈 값은 생략** — `None`·`""`·`[]`·`{}`는 렌더하지 않는다.

### ③ 공용 렌더러의 소재지 — `noise_gate/domain/investigation_briefing.py` (신규)

두 소비자가 서로 다른 패키지(`src`·`noise_gate`)에 있어 한쪽에 두면 반대쪽이 못 쓴다.

| 후보 | 판정 |
|---|---|
| **`noise_gate/domain/`** | **채택**. `src → noise_gate.domain` 은 확립된 전례다(`src/security/audit_logger.py:19`·`src/orchestration/process_query.py:36`가 `noise_gate.domain.process_rank`를 쓴다). domain은 최내곽이라 계층 규칙상 누구나 의존 가능. `investigation_payload.py`(**송신** 계약)의 대칭 짝으로 **수신** 계약이 놓이는 자리다 |
| `src/domain/` | 불가 — `noise_gate → src` **역방향 결합 신설**(D-139 금지) |
| 각자 구현 + 계약 테스트 | 비채택 — 이번 결함이 정확히 "각자 구현"의 산물이다 |

렌더러는 **평문(plain)** 을 산출하고, HTML 이스케이프·`<br>` 변환은 **소비자가** 한다
(domain은 표현 매체를 모른다).

```python
def render_briefing_lines(briefing: dict) -> list[tuple[str, str]]:
    """브리핑 dict → [(라벨, 평문 값), ...] 순서 고정. 표현 매체 비의존(순수 함수)."""
```

## Tech Stack

Python ≥3.11 · 표준 라이브러리만(domain 계층 제약) · pytest.
`sre_agent` 측은 변경 없음(정본이므로). `BRIEFING_ELEMENTS`(6요소 조립 대상)는 실제 산출 키의 부분집합이지만
문서화된 의미("조립 대상 6요소")가 맞으므로 **상수는 손대지 않고**, 산출 키 전체는 양쪽 **대칭 리터럴 테스트**로
고정한다(`sre_agent`가 루트 venv에서 import되지 않아 한 파일에서 대조할 수 없다 — 구현 시 확정).

## Commands

```bash
# 대상 테스트
pytest tests/test_alarm -q                          # push 렌더 회귀
pytest tests/ -k "briefing" -q                      # 신규 계약 테스트
cd sre_agent && .venv/bin/python -m pytest tests -q # 생산자 무회귀

# 품질 게이트
python scripts/arch_check.py --ci
python scripts/overfit_check.py --ci
ruff check src/ noise_gate/ tests/
```

## Project Structure

```
noise_gate/domain/investigation_briefing.py     신규 — 공용 렌더러(순수 함수)
noise_gate/application/nodes/alarm_notifier.py  수정 — _investigation_briefing_html이 렌더러 소비
src/nodes/fault_diagnosis.py                    수정 — _briefing_to_text가 렌더러 소비
sre_agent/sre_agent/application/briefing_builder.py  수정 — BRIEFING_ELEMENTS 상수 정합(1줄)
tests/test_briefing_contract.py                 신규 — 계약·렌더 테스트(소비자 2곳 + 렌더러)
sre_agent/tests/test_briefing_builder.py        수정 — 산출 키 집합 대칭 리터럴 단언
```

## Code Style

기존 domain 모듈(`investigation_payload.py`)의 스타일을 따른다 — 모듈 독스트링에 계층 제약 명시,
순수 함수, 덕 타이핑, 한국어 독스트링.

```python
#: 렌더 순서와 라벨. 순서 목록에 없는 키는 말미에 원 키명으로 렌더한다(침묵 누락 방지).
_ORDERED: tuple[tuple[str, str], ...] = (
    ("severity", "중요도"), ("summary", "요약"), ("timeline", "타임라인"),
    ("bottleneck", "병목"), ("cause", "원인"), ("recommendation", "권고"),
    ("limitations", "한계"), ("hypotheses", "가설"),
)
#: 메타데이터라 출력하지 않는 키(사유가 이미 limitations에 반영됨).
_SUPPRESSED: frozenset[str] = frozenset({"citations_verified", "stub", "elements"})
```

## Testing Strategy

pytest. 위치는 본체 `tests/test_briefing_contract.py`(두 소비자 + 렌더러를 한 파일에서 대조) — 생산자 쪽
대칭 테스트는 `sre_agent/tests/test_briefing_builder.py::test_output_keys_match_consumer_contract`.
(초안이 적은 `tests/test_alarm/`은 존재하지 않는 경로였다 — 실측 후 정정.)

| 테스트 | 검증 |
|---|---|
| `test_contract_keys_covered` | **생산자 산출 키 ⊇ 렌더러 인지 키** — `build_briefing()` 반환 키를 실제로 만들어 대조. 생산자가 키를 늘리면 실패한다 |
| `test_limitations_reach_user` | `limitations` 항목 문자열이 **두 소비자 출력 모두에** 나타난다 |
| `test_hypotheses_reach_user` | 동일 — 가설 강등분 |
| `test_no_repr_leak` | 두 소비자 출력에 `{'`·`['`·`&#x27;` 가 없다 |
| `test_unknown_key_not_dropped` | 렌더러가 모르는 키를 넣어도 출력에 나타난다 |
| `test_empty_values_omitted` | `[]`·`{}`·`""`는 라벨째 생략 |
| `test_stub_briefing_unchanged` | 스텁 경로(`{"stub": True, ...}`) 출력 **비트 동일**(회귀 0) |
| `test_push_pull_symmetry` | 같은 briefing에서 두 경로의 **라벨 집합·순서가 동일** |

**TDD 순서**: 위 실측 재현을 먼저 실패 테스트로 고정하고(현행에서 red), 렌더러 도입 후 green.

## Boundaries

- **Always**: 렌더러는 순수 함수(표준 라이브러리만) · 소비자 2곳을 **같은 커밋에서** 고친다 ·
  스텁 경로 출력 비트 동일 확인 · `arch_check --ci` 통과.
- **Ask first**: 생산자(`briefing_builder`) 출력 키의 **추가·개명** · 알림 전달 방식(D-137) 변경 ·
  `overfit_baseline.json` 갱신.
- **Never**: 실 LLM 조사 실행(D-127) · `noise_gate → src` 역방향 import 신설 ·
  `briefing_builder`의 인용 검증·가설 강등 **판정 로직** 변경(이 스펙은 표현만 고친다) ·
  기존 테스트 삭제.

## Success Criteria

1. `limitations`·`hypotheses`·`severity`가 **챗 응답과 WorkB 알림 양쪽에 나타난다**(테스트로 단언).
2. 두 소비자 출력에 **Python repr 흔적이 없다**(`{'`·`['`·`&#x27;` 0건).
3. 같은 briefing에 대해 **두 경로의 라벨 집합·순서가 동일**하다.
4. 렌더러가 **모르는 키를 침묵 누락하지 않는다**.
5. 스텁 브리핑 출력이 **현행과 비트 동일**(회귀 0).
6. `pytest tests/test_alarm` 무회귀 · `sre_agent/tests` 무회귀 · `arch_check --ci` exit 0.
7. `plans/50` §0.3 G6 항목이 **해소**로 갱신되고 D-197가 등재된다.

## Open Questions — 구현 시 확정(2026-09-02)

O-1 기본안 채택(`심각(신뢰도 high) — 게이트 PAGE · 조사 상향 · 시그니처 oom_kill`) · O-2 `[근거]` 폐기 확정 · O-3 D-197는 6모듈 완료 시점에 한 번에 등재(모듈 1 랜딩과 같은 세션이라 근거 분산 없음).

| # | 쟁점 | 기본안 |
|---|---|---|
| **O-1** | `severity` dict의 한 줄 표현 형식 — `sre-agent/02` §7 예시(`심각(신뢰도 high) — 게이트 PAGE + 조사 상향`)를 그대로 쓸지 | 예시 형식 채택. `signals`는 이름 나열 |
| **O-2** | `evidence` 라벨을 영영 버릴지, `timeline`의 인용 부분을 분리해 되살릴지 | **버린다**. 근거는 타임라인 인라인 표기가 정본(`sre-agent/02` §7). 되살리면 생산자 변경이 필요해 이 모듈 범위를 넘는다 |
| **O-3** | D-197를 이 모듈 랜딩 시 등재할지, 6모듈 완료 후 한 번에 할지 | **이 모듈 랜딩 시**. 계약 정본 지정은 이 모듈에서 확정되는 결정이라 뒤로 미루면 근거가 흩어진다 |
