# 183. orchestration 지연 import 순환(SCC) 해소 — `apm_jobs` → `apm_query` 역간선 제거

> **작성일**: 2026-10-17 · **v1.0**
> **상태**: **완료**(2026-10-17 · 커밋 전) — orchestration 지연 import SCC 8→0모듈 · 역간선 `apm_jobs`→`apm_query` 1개를 의존 역전(세션 공장 공급자 훅)으로 제거 · 기존 테스트 수정 0 · 계획 끝 회귀 실패 0(모듈 단위) · verifier Critical·Major 0 · §4
> **상위**: `plans/166` §5.8 미달 지표 「orchestration 지연 import SCC」(8모듈) · `docs/18` 2026-10-17 SCC 항목. 사용자 결정(2026-10-17): 「순환 import만 바로」.
> **성격**: 동작 불변 구조 리팩터(D-337) — 응답·로그 문구·거부 코드 불변 · 테스트 seam 유지 · 기존 테스트 수정 0 · 스냅샷 갱신 금지.
> **계획 시작 SHA**: `a4d2db92`(RCA · 작업 트리 깨끗)
> **회귀 대상 파일(누적)**: `src/orchestration/apm_jobs.py` · `src/orchestration/apm_query.py` · 새 `tests/test_orchestration/test_plan183_import_scc.py` *(누적 끝 — 계획 끝 회귀 1회: §4)* · verifier 새 테스트 `tests/test_orchestration/test_plan183_verify.py`
> **관련 결정**: D-337(②seam 보존 ⑥행동 변경 분리) · D-303(회귀)
> **범위 밖**: `apm_jobs.py:381` `source_call_timeout` getattr 폴백(plans/182 L3 — 행동 변경 후보)

---

## 1. 실측 (a4d2db92)

측정 정의는 plans/166 §5.8 지표 5와 같다 — `src/orchestration/` 하위 모듈 간 import 그래프 · 함수 안 지연 import 포함 · `__init__.py` 노드 제외 · `from pkg import name`은 `pkg.name`이 모듈이면 그 모듈로도 간선 · 크기≥2 강연결 요소의 모듈 수.

SCC 1개 · 8모듈: `apm.builders` · `apm.calls` · `apm.context` · `apm.first_hop` · `apm.jobs_notices` · `apm.targets` · `apm_jobs` · `apm_query`.

SCC 내부 간선 18개 중 지연 import는 **1개뿐**이고 나머지 17개는 최상위 import다.

| 간선 | 종류 |
|---|---|
| `apm_query` → `apm.{builders,calls,context,first_hop,jobs_notices,targets}` · `apm_jobs` | 최상위 |
| `apm.{calls,jobs_notices,targets}` → `apm_jobs` | 최상위 |
| `apm/*` 내부(`builders`→`calls`·`context`·`targets` · `context`→`calls`·`targets` · `first_hop`→`calls` · `jobs_notices`→`calls` · `targets`→`calls`·`jobs_notices`) | 최상위 · DAG |
| **`apm_jobs:374` → `apm_query`** (`ApmJobService._call` 안) | **지연 — 유일한 역간선** |

`apm_jobs._call`이 `apm_query`에서 읽는 이름은 셋이다.
- `apm_endpoint`·`APM_SYSTEM` — 정의는 이미 `src/orchestration/apm/common.py`(SCC 밖 leaf)에 있고 `apm_query`는 재노출만 한다. 이 둘을 patch하는 테스트는 없다.
- `_SESSION_FACTORY` — 테스트 seam. `apm_query` 모듈 전역이 정본이고 테스트 28파일 남짓이 `monkeypatch.setattr(aq, "_SESSION_FACTORY", …)`로 바꾼다. 그중 `ApmJobService`를 직접 만드는 테스트(`test_plan134_apm_jobs_api.py:306` 등)도 있어, `_call`이 **호출 시점의** `aq._SESSION_FACTORY`를 읽어야 한다.

## 2. 방법

1. `apm_endpoint`·`APM_SYSTEM`은 `apm_jobs`가 `src.orchestration.apm.common`에서 직접 읽는다(같은 객체 — 동작 동일).
2. `_SESSION_FACTORY`는 의존 역전으로 읽는다. `apm_jobs`에 세션 공장 공급자 훅(기본 `None`을 돌려준다)을 두고, `apm_query`가 import될 때 자기 전역 `_SESSION_FACTORY`를 **호출 시점에** 읽는 공급자를 그 훅에 등록한다. `_call`은 훅을 불러 `session_factory`를 얻는다.
   - 테스트가 `aq._SESSION_FACTORY`를 바꾸면 그 테스트는 이미 `apm_query`를 import했으므로 훅이 등록돼 있고, 공급자가 호출 시점의 값을 읽어 seam이 그대로 반영된다.
   - `apm_query`가 import되지 않은 프로세스에서는 훅이 `None`을 돌려준다. 이는 `aq._SESSION_FACTORY`의 기본값 `None`과 같아 실행 결과가 동일하다(달라지는 것은 `apm_query` 모듈 적재라는 부수효과가 사라지는 것뿐).
3. 지연 import 줄을 지운다. 결과 SCC 0모듈(`apm/*` 내부와 `apm_query`→하위 간선은 DAG).
4. 가드 테스트 — 같은 정의로 orchestration SCC가 비었음(크기≥2 요소 0)을 단언하고, `apm_jobs`가 `apm_query`를 (지연 포함) import하지 않음을 단언한다.

## 3. 수용 기준

- 측정 정의로 orchestration SCC ≤ 2모듈(목표 0).
- 기존 테스트 수정 0 · `test_plan180_verify.py` 동적 seam 테스트·`ApmJobService` 경로 테스트 통과.
- 계획 끝 `regress.py --base a4d2db92 --files <바꾼 파일>` 원래 실패 2(`test_plan160_chart_api…[True]` · `test_settings_help.py::test_t2_curated_groups_have_full_coverage`)만.
- arch_check error 0 · overfit 신규 0.

## 4. 결과

- **SCC**: 전 8모듈(`apm.builders`·`apm.calls`·`apm.context`·`apm.first_hop`·`apm.jobs_notices`·`apm.targets`·`apm_jobs`·`apm_query`) → 후 **0**(같은 정의 · 단계 5 끝 2보다 적다).
- **끊은 간선**: `apm_jobs:374` → `apm_query`(지연). `apm_jobs`는 `apm_endpoint`·`APM_SYSTEM`을 `src.orchestration.apm.common`에서 최상위 import하고, 세션 공장은 모듈 전역 훅 `session_factory_provider`(기본 `_no_session_factory` → None)로 읽는다. `apm_query`가 `_SESSION_FACTORY` 정의 바로 아래에서 `jobs.session_factory_provider = lambda: _SESSION_FACTORY`를 등록한다 — 전역 이름을 호출 시점에 조회하므로 `monkeypatch.setattr(aq, "_SESSION_FACTORY", …)`가 그대로 반영된다(seam 정본은 `apm_query` 유지).
- **가드**: `test_plan183_import_scc.py` — orchestration SCC 0 · `apm_jobs` AST 전체에 `apm_query` import 0 · seam 반영. verifier 확인: 원본 간선 하나만 되살리면 8모듈 SCC가 재현돼 가드가 공허하지 않다.
- **계획 끝 회귀**: `regress.py --base a4d2db92 --files` 3파일 → rc 0 · 본체 통과 2,113 · 실패 0 · 건너뜀 4 · arch·overfit·config_fallback 통과 · ruff·mypy 이번 diff 줄 위반 0 · `범위: 모듈 단위 — 전체 미실행`(권고 블록 없음). 원래 실패 2건은 선택 범위 밖이라 실행되지 않았다.
- **verifier**: Critical 0 · Major 0 · Minor 1(가드가 AST import만 봐 `importlib` 문자열 우회는 못 잡음 — verifier 테스트 `test_apm_jobs_has_no_dynamic_import_bypass`가 보완) · 정보 2(`src/orchestration/__init__.py`가 `apm_query`를 연쇄 적재해 기본 공급자는 현재 실제로 쓰이지 않음 — 값은 둘 다 None으로 동일 · undo 뒤 None 단언은 직접 대입 0 전제). 새 `test_plan183_verify.py` 13건 통과.
- **행동 차이**: 없음. `apm_query`가 아직 적재되지 않은 상태에서 `_call`이 그 모듈을 적재하던 부수효과만 사라진다(패키지 `__init__`이 이미 적재하므로 현재는 관측되지 않는다).
