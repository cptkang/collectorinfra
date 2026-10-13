# 158. 기준 SHA에서도 실패하는 「원래 실패」 테스트 18건 — 원인 분류와 격리 교정

> **작성일**: 2026-10-13 · **v1.0**(최초 — 원인 분류 완료 · 구현 전)
> **상태**: **TODO** — 코드 0건. 18건 전부 **환경 의존(c)** 으로 판정, 제품 결함(a) 0 · 테스트 부패(b) 0.
> **요청 원문(2026-10-13)**: *"결함은 후속 계획을 만들고 …"* (계획 작성까지만)
> **발견 경위**: `plans/152`(기준 `ee13b47`)·`plans/159`(기준 `6810336`)의 계획 끝 모듈 단위 회귀(`scripts/regress.py`)에서 기준 SHA에서도 실패하는 `[원래 실패]`가 반복해 나왔다(152 §10.2 · 회귀 로그 `logs/regress/20261013-145256-55038/failed_ids.txt`). 어떤 계획에도 추적되지 않아 회귀 판독 때마다 사람이 「원래 실패」를 가려내야 한다.
> **성격**: 실측 분류 + 테스트 격리 교정(제품 코드 변경 0 예상).
> **상위·인접 계획**: `plans/152`(발견 §10.2) · `plans/159`(재발견) · `plans/136`(회귀 도구 · 실패 귀속 판정)
> **관련 결정**: D-303(회귀 정책 — 병렬 불가 테스트는 원인을 먼저 고친다) · D-251(2단 기준 · 미입력 플래그 경고) · D-143(존 역질문 · 파일 경로) · D-232(조회 권한으로 존 선택지 거름) · Known Mistakes 「테스트 config는 검증 대상 필드를 명시해 `.env` 누수를 막는다」(2026-06-17 항목)
> **실측 기준**: 2026-10-13 `main` HEAD `39c5a0f`(작업 트리에 plans/159 등 미커밋 변경 있음). 실행: pyenv python 3.13.1 · `.venv` 없음 · pytest 8.4.2 · pytest-xdist 3.8.0 · **`instructor` 미설치** · **`.env`·`.encenv` 파일 없음**(`.env.example`만 있음). `file:line`은 이 시점 값이다.
> **계획 시작 SHA**: `39c5a0f` · **회귀 대상 파일**: `tests/test_api/test_plan122_t4_time_resolution_route.py` · `tests/test_orchestration/test_plan146_demonstrative_zero_rows.py` · `tests/test_orchestration/test_plan150_sample_antecedent.py` · `tests/test_routing_eval/test_eval_routing_modes.py` · `tests/test_structured_output/test_intent_extraction_typing.py` (+ 필요 시 `tests/conftest.py`)

---

## 0. 요약

18건은 **제품이 틀린 것도, 제품 변경을 테스트가 못 따라간 것도 아니다.** 테스트가 개발자 로컬 `.env`(`ACTIVE_DB_IDS=polestar,itam` 등)나 선택 설치 패키지(`instructor`)가 있다고 암묵 가정하고 쓰였고, 그것이 없는 환경(이 체크아웃 · 기준 SHA의 격리 워크트리 · 폐쇄망 배포본)에서는 도입 첫날부터 실패한다. 환경 변수를 주입하면 14건이 즉시 통과한다(§2 재현).

| 원인 | 건수 | 파일 | 교정 |
|---|---:|---|---|
| C-1 `ACTIVE_DB_IDS` 미설정 → 존 역질문이 가로챔 | 14 | t4 route 12 · plan146 1 · plan150 1 | 테스트 config에 활성 DB 명시 |
| C-2 `.env` 부재 + 미입력 플래그 경고(stderr)·활성 DB 0 | 2 | eval_routing_modes 2 | 서브프로세스 env·monkeypatch로 고정 |
| C-3 선택 의존 패키지 `instructor` 미설치 | 2 | intent_extraction_typing 2 | 의존 부재 시 skip(또는 dev extra 편입) — G-1 |

도입 커밋은 특정 가능하나 **전부 「테스트 도입 시점부터」** 다(회귀를 만든 코드 커밋 없음 — §3).

---

## 1. 문제

- `regress.py`가 `[원래 실패]`를 가르는 기준 SHA(워크트리)에는 `.env`가 없어, **실패가 「이번 계획의 신규 실패」인지 「원래 실패」인지 매번 사람이 확인**해야 한다.
- 18건이 상시 빨간색이면 회귀 신호의 분별력이 떨어진다(진짜 신규 실패가 묻힌다).
- CLAUDE.md 「로컬 `.env` 실측」은 `.env`가 있는 개발 맥을 전제하지만, 이 체크아웃에는 `.env`가 없다. **테스트는 `.env` 유무와 무관하게 같은 결과여야 한다**(Known Mistakes — 테스트 config `.env` 누수).

---

## 2. 실측 — 테스트 × 실패 메시지 × 분류 × 근거 × 조치

실행: `python -m pytest -q -p no:cacheprovider <5개 파일> --tb=short` → `18 failed, 167 passed, 2 skipped`(8.9초). 병렬/순서 의존 아님 — 단독·결정적 재현. 재현 확인: `ACTIVE_DB_IDS=polestar,itam`을 주고 t4·146·150 세 파일을 돌리면 `111 passed, 2 skipped`; 추가로 `ENABLE_SEMANTIC_ROUTING=false ENABLE_INTENT_ORCHESTRATION=true`를 주면 eval_routing_modes 통과(instructor 2건만 남음).

### 2.1 C-1 — 존 역질문 가로채기 (14건)

공통 원인: `_file_zone_clarification_or_none`(`src/api/routes/query.py:1849`)·`_zone_clarification_or_none`(`:2160`)이 위치어 없는 질의에 존 역질문을 낸다. 선택지는 `build_zone_clarification`(`src/utils/query_gen_common.py:2190~`)이 `options = [o for o in ZONE_CLARIFY_OPTIONS if not active or o["db_id"] in active]`로 거르는데, 활성 DB가 `polestar,itam`(샌드박스)이면 폴스타 존 3종이 전부 걸러져 `options`가 비고 `None`을 돌려 **역질문이 생략**된다. 활성 DB가 비어 있으면(`not active`) 3종 전부가 선택지로 남아 역질문이 뜬다. 테스트의 `AppConfig(...)`(t4 `:106`, 146 `:638`, 150 `:547`)는 `multi_db`를 명시하지 않아 `.env`의 `ACTIVE_DB_IDS`를 그대로 읽는다 → `.env` 유무로 결과가 갈린다. 로그 `clarification_issued kind=zone_select option_count=3`가 증거.

| # | 테스트 | 실패 메시지(요지) | 분류 | 근거 | 조치 |
|---|---|---|---|---|---|
| 1 | `test_plan122_t4_…::test_four_entry_points_carry_state_value[file]` | `out["time_resolution"]` = None (응답이 zone_select 역질문) | (c) | t4:171 · query.py:1849 | W1 |
| 2 | 〃 `[file_stream]` | `KeyError: 'time_resolution'` | (c) | t4:171 | W1 |
| 3 | `test_stream_fallback_ainvoke_carries_state_value[file_stream]` | `KeyError: 'time_resolution'` | (c) | t4:179 | W1 |
| 4 | `test_clarify_end_is_clarification_response[file]` | 응답이 「양식을 채울 대상 존이…」(기대 「[조회 기간] 2월 30일은…」) | (c) | t4:228 | W1 |
| 5 | 〃 `[file_stream]` | 같음 | (c) | t4:228 | W1 |
| 6 | `test_clarify_end_fallback_ainvoke[file_stream]` | `clarification.kind` = `zone_select`(기대 `time_period`) | (c) | t4:244 | W1 |
| 7 | `test_file_answer_without_clarify_keeps_completed_shape[file]` | `status` = `clarification`(기대 `completed`) | (c) | t4:252 | W1 |
| 8 | 〃 `[file_stream]` | `clarification` 키가 응답에 존재 | (c) | t4:254 | W1 |
| 9 | `test_followup_clarify_turn_drops_prior_turn_outputs[astream-file]` | `kind` = `zone_select` | (c) | t4:294 | W1 |
| 10 | 〃 `[astream-file_stream]` | 같음 | (c) | t4:294 | W1 |
| 11 | 〃 `[ainvoke-file_stream]` | 같음 | (c) | t4:294 | W1 |
| 12 | `test_followup_clarify_fallback_meta_event_has_no_prior_sql[file_stream]` | meta 이벤트 없음(`assert ([])`) | (c) | t4:321 | W1 |
| 13 | `test_plan146_demonstrative_zero_rows::test_widened_rows_reach_stream_meta_and_csv_with_notice_first` | 응답이 「조회할 존이 지정되지 않았습니다…」(기대 `[안내] 앞 질문…`) | (c) | 146:698 · fixture 146:638 | W1 |
| 14 | `test_plan150_sample_antecedent::test_partial_rows_reach_stream_meta_and_csv_with_notice_first` | 같음 | (c) | 150:605 · fixture 150:547 | W1 |

t4에서 텍스트 경로(`text`·`text_stream`) 파라미터가 통과하는 이유: `_zone_clarification_or_none`은 질의가 `is_full_scan_query`이고 「서버」를 포함할 때만 발동하는데 「테스트 질의」는 해당 없다. 파일 경로는 「위치어 미해소면 무조건 발동」이라(`:1851` docstring) 12건이 전부 `[file]`·`[file_stream]` 계열이다.

### 2.2 C-2 — `eval_routing` 모드 테스트 (2건)

| # | 테스트 | 실패 메시지 | 분류 | 근거 | 조치 |
|---|---|---|---|---|---|
| 15 | `test_eval_routing_modes::TestLegacyModesUnchanged::test_static_mock_output_is_byte_identical` | stderr에 `enable_semantic_routing 미입력 …` · `enable_intent_orchestration 미입력 …` 두 줄이 추가돼 바이트 불일치 | (c) | 테스트 :188 · `_ENV`(:140~146)에 두 플래그 없음 · 경고 출처 `src/config.py:1737·1746`(D-251) | W2 |
| 16 | `TestCli::test_real_calls_stay_behind_optin_gate[argv1]` (`--runtime-prompt-conditions`) | `DID NOT RAISE SystemExit` — 「활성 DB가 없다」로 `scripts/eval_routing.py:2026`에서 먼저 종료해 옵트인 가드(`_require_optin`)에 닿지 않음 | (c) | 테스트 :630 · 가드 도달 전제가 `ACTIVE_DB_IDS` 존재 | W2 |

두 테스트 모두 `.env`가 있으면 경고가 억제되고(두 플래그 명시) 활성 DB가 채워져 통과한다. **#16은 과금 게이트 테스트이므로 skip이 아니라 입력 고정으로 고쳐야 한다**(가드가 실제로 걸리는지를 보는 테스트).

### 2.3 C-3 — 선택 의존 패키지 (2건)

| # | 테스트 | 실패 메시지 | 분류 | 근거 | 조치 |
|---|---|---|---|---|---|
| 17 | `test_intent_extraction_typing::TestDecomposeStructured::test_bad_agent_is_repaired_by_reask` | `['general_inference'] != ['process_query']` — 되먹임 재질의가 일어나지 않음 | (c) | 로그 `src/clients/instructor_adapter.py:312` 「instructor 사용 불가 — 기존 파싱 경로로 강등」 · `_Cfg(backend="instructor")`(테스트 :46) · `pyproject.toml:80` `structured` extra | W2 · G-1 |
| 18 | `test_exhaustion_reports_reason_and_keeps_fallback` | `out.get("degraded")` = None — 침묵 폴백 | (c) | 같음(테스트 :114) | W2 · G-1 |

이 둘은 `instructor`가 있어야만 의미가 있는 테스트다(되먹임 재질의는 instructor 재시도 기능). 같은 파일의 나머지 테스트는 백엔드 off 경로라 통과한다. `grep importorskip`·`find_spec("instructor")` 가드가 repo 전체에 0건 — 설치 여부 분기가 없다.

---

## 3. 언제부터·무엇이 도입했나

| 테스트 | 도입 커밋 | 비고 |
|---|---|---|
| `test_plan122_t4_time_resolution_route.py` | `9cc8c6b`(2026-10-06) | `.env` 없는 격리 워크트리(`9cc8c6b`)에서 도입 시점 그대로 **12건 실패**. 부모(`9cc8c6b~1`)엔 파일이 없어 이전 상태 비교 불가. 이후 `42deda2`·`3c4adab`에서도 동일 12건 |
| `test_plan146_demonstrative_zero_rows.py` | `aa69985`(2026-10-08) | 도입 직후부터(파일 경로 `/query/stream` 존 게이트 `3c4adab`·`89773af` 이후 구조 — 코드 회귀 아님) |
| `test_plan150_sample_antecedent.py` | `39220a5`(2026-10-12) | 146과 같은 패턴을 복제 |
| `test_eval_routing_modes.py` | `f51cd49`(2026-09-29) | 미입력 플래그 경고는 `284137a`(2026-09-17)·`d957fc0`(2026-09-23, D-251)에서 이미 존재 → 테스트가 `.env` 있는 환경에서만 통과해 온 것 |
| `test_intent_extraction_typing.py` | `c7d47e8`(2026-08-27) | `instructor`는 선택 extra — 설치된 환경에서만 통과 |

**결론**: 회귀를 만든 코드 커밋은 없다. 「깨졌다」가 아니라 「이 환경에서 통과한 적이 없다」이므로 이분 탐색 대상이 아니다. 단 t4 12건은 **폐쇄망 배포본·CI·다른 개발자 체크아웃에서도 같이 실패**한다는 점에서 실질 위험이다.

---

## 4. 사용자 확정 게이트

기존 결정과 충돌하는 제품 동작 변경은 없다(전건 테스트 격리). 아래 둘은 **테스트 수정 방식의 선택**이며 어느 쪽이든 제품 동작은 같다. 사용자 응답이 없으면 권고안으로 진행해도 안전하다.

| # | 질문 | 선택지 | 권고 |
|---|---|---|---|
| G-1 | `instructor` 필요 2건(#17·18)을 어떻게 다룰까 | (a) `pytest.importorskip("instructor")`로 **미설치 시 skip**하고 설치 환경에서만 실행 · (b) `instructor`를 `dev` extra에 편입해 항상 설치 · (c) 대역 백엔드로 재질의 로직만 검증하도록 재작성 | **(a)** — `structured`는 폐쇄망이 wheel 반입해야 하는 선택 extra(`pyproject.toml:78`)라 (b)는 배포 부담을 키운다. (c)는 테스트가 검증하려는 대상(instructor 되먹임)을 바꾼다. skip 사유 문구에 설치 명령을 적어 침묵 skip이 되지 않게 한다 |
| G-2 | 활성 DB 고정을 어디서 할까 (#1~16) | (a) **파일별 fixture에서 `multi_db`를 명시** · (b) `tests/conftest.py` autouse로 `ACTIVE_DB_IDS`를 전역 고정 | **(a)** — 전역 고정은 활성 DB 상태에 의존하는 다른 수많은 테스트(예: 존 역질문을 검증하는 테스트)의 전제를 바꾼다. `conftest.py:26~42`의 `MCP_SOURCE_ENDPOINTS` 선례는 「누수 차단」 한 가지 키에 한정됐다. (a)는 영향 범위가 5개 파일로 닫힌다 |

---

## 5. 설계 (G-1·G-2 권고안 기준)

### 5.1 C-1 — 활성 DB를 테스트 config에 명시

- 세 fixture(`t4:106 app_config` · `146:632 _route_config` · `150:541 _route_config`)의 `AppConfig(...)`에 **`multi_db`로 활성 DB를 명시**한다(예: 샌드박스 `polestar` 단일 — 존 3종이 선택지에서 빠져 역질문이 생략되는 상태를 **환경이 아니라 테스트가 소유**하게 한다). `.env`가 있든 없든 같은 값이 된다.
- 필드명은 구현 때 `inspect`로 실측한다(`MultiDBConfig.active_db_ids_csv`의 validation alias는 `ACTIVE_DB_IDS`/`MULTI_DB_ACTIVE_DB_IDS_CSV` — `src/config.py:655`; init 인자 사용 시 alias 규칙 확인). 안 되면 `monkeypatch.setenv("ACTIVE_DB_IDS", …)`로 대체.
- **t4는 `scope="module"` fixture**라 `monkeypatch`(함수 스코프)를 못 쓴다 — 함수 스코프로 낮추거나 init 인자를 쓴다(W1에서 확인).
- 의도 검증: 역질문 자체를 보는 별도 테스트(존 역질문 발동)가 있다면 그 테스트의 활성 DB 전제는 건드리지 않는다.

### 5.2 C-2 — `eval_routing` 테스트 입력 고정

- #15: 클래스 `_ENV`(`:140`)에 `ENABLE_SEMANTIC_ROUTING`·`ENABLE_INTENT_ORCHESTRATION`을 명시한다 — 테스트 기대값(`_ROUTING_MOCK_STDERR`)은 **그대로 두고** 경고가 안 나는 입력으로 바꾼다(기대값을 바꾸면 「종전과 바이트 동일」이라는 테스트 목적이 무너진다). 값은 기존 기대 출력이 만들어진 조건과 같게(`false`/`true` 조합은 W2에서 `diff`로 확인).
- #16: `monkeypatch.setenv("ACTIVE_DB_IDS", …)`로 활성 DB를 주어 `scripts/eval_routing.py:2026` 조기 종료를 지나 `_require_optin`에 닿게 한다(`--active-dbs` 인자로 주입해도 된다). 가드가 실제로 걸리는지(`SystemExit(2)`)를 계속 단언한다 — **skip·단언 약화 금지**(과금 게이트 D-127).

### 5.3 C-3 — 선택 의존 skip

- `TestDecomposeStructured`의 두 테스트(또는 클래스 단위)에 `pytest.importorskip("instructor", reason="structured extra 필요: pip install -e '.[structured]'")` 또는 동등한 `skipif`. 백엔드 off 테스트(S1)는 영향 없이 계속 돈다.

---

## 6. 작업 분해

| Wave | 내용 | 검증 |
|---|---|---|
| **W0** | 재현 고정 — 현 18건 실패를 다시 확인하고 §2 근거 줄번호를 대조. `.env` 유무 2조건으로 각 파일 실행(`ACTIVE_DB_IDS` 유무) | 18 fail → env 주입 시 14 pass 재현 |
| **W1** | C-1 교정(5.1) — t4·146·150 fixture | 세 파일 **`.env`·`ACTIVE_DB_IDS` 없이** 전건 통과 + `ACTIVE_DB_IDS=polestar,itam`이 있어도 통과 |
| **W2** | C-2·C-3 교정(5.2·5.3) | 두 파일 전건 통과(instructor 2건은 skip 사유 출력) · `#16`이 `SystemExit(2)`를 실제로 단언 |
| **W3** | 계획 끝 모듈 단위 회귀 1회 + 기록(`[원래 실패]` 0 확인) · Known Mistakes 1행 · INDEX 갱신(메인 세션) | 아래 §7 |

W1·W2는 서로 다른 파일이라 병렬 가능하다. 제품 코드 변경은 0 예상 — W1에서 제품 결함이 드러나면 이 계획에서 멈추고 게이트로 올린다.

---

## 7. 수용 기준

1. 대상 18건이 **`.env`·`.encenv` 없는 환경**에서 통과(또는 G-1 (a)의 2건은 사유 명시 skip).
2. 같은 18건이 **로컬 `.env`(`ACTIVE_DB_IDS=polestar,itam` 등)가 있는 환경**에서도 통과 — 두 조건 결과 동일.
3. 기준 SHA 워크트리(`git worktree add --detach`)에서 돌린 `regress.py`의 `[원래 실패]` 목록이 이 5개 파일에서 0.
4. 격리 후에도 존 역질문을 **발동시키는** 기존 테스트는 그대로 통과(전역 오염 없음) — `python scripts/regress.py --base 39c5a0f --files <5개 테스트 파일>` 모듈 단위 회귀 신규 실패 0. 보고에 도구 마지막 범위 줄(`범위: 모듈 단위 — 전체 미실행`)을 옮긴다. 전체 회귀는 사용자 요청 시에만.
5. `arch_check`·`overfit_check`·ruff 통과(테스트 파일 변경이라 영향 없을 것으로 예상).
6. 매뉴얼 동반(D-255): 해당 없음 — 사용자·관리자 기능 변경 0.

---

## 8. 위험

| 위험 | 대응 |
|---|---|
| 활성 DB를 명시하면 t4가 검증하려던 경로가 바뀜 | t4의 목적은 `time_resolution` 계약 — 존 역질문은 변수일 뿐. 교정 후 `[text]`·`[file]` 파라미터가 같은 단언으로 같은 결과를 내는지 대조 |
| `scope="module"` fixture + 환경 의존 | 5.1 — init 인자 우선, 불가 시 스코프 조정 |
| #15 기대 stderr가 정말 「플래그 명시 상태」에서 나온 출력인지 불확실 | W2에서 `.env` 있는 조건(두 플래그 명시)의 실제 stderr와 `_ROUTING_MOCK_STDERR`를 `diff`로 확정 — 어긋나면 입력 조합을 조정(기대값은 바꾸지 않음) |
| 같은 패턴(fixture가 `.env`에 기댐)의 **미발견 다른 테스트** | 이 계획 범위는 실측된 18건. 패턴 전수 스캔은 하지 않는다(전체 회귀 금지) — 후속으로 `grep -rn "AppConfig(" tests`에서 `multi_db` 미명시 fixture를 후보로만 기록 |

---

## 9. 의사결정 정합

- 신규 결정 불필요(테스트 격리 · 제품 동작 불변). Known Mistakes에 「테스트 config가 `.env`의 `ACTIVE_DB_IDS`에 기대면 `.env` 없는 환경에서 존 역질문 분기가 달라진다」를 1행 추가하는 것을 권고(메인 세션).
- D-303 「병렬로 못 도는 테스트는 원인을 먼저 고친다」와 정합 — 이 18건은 병렬 문제가 아니라 환경 의존이라 `serial` 마커로 가릴 수 없다(가려도 실패한다). `addopts`에 `-n`을 넣지 않는다.
- D-232 · D-143(존 역질문 동작)은 건드리지 않는다.

---

## 10. 진행 기록

- 2026-10-13 — v1.0 작성. 18건 실행·분류 완료, 워크트리 3개(`9cc8c6b`·`42deda2`·`3c4adab`)로 도입 시점 확인 후 제거. 제품 코드·테스트·INDEX·결정 변경 0.

## 변경 이력

- v1.0 (2026-10-13) — 최초(번호 158 예약 스텁 대체).
