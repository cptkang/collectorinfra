# 168. query 라우트 스트림·응답·사전 게이트 공통화 — A1·A3 (W3 5a)

> **작성일**: 2026-10-16 · **v1.2**
> **상태**: **완료**(2026-10-16 · 커밋 전) — W1·W2 + 가드 보강(§4.4). `query.py` 4,377→4,256줄 · 차분 실행 87케이스 바이트 동일 · 계획 끝 회귀 원래 실패 1만 남김(모듈 단위). `_build_turn_response`는 소스 계수 가드 때문에 미추출(§4.2) — 계수 가드 전환은 **7b 착수 전 사용자 결정**(§5).
> **상위**: `plans/166` §0.1 단계 5 · **계획 ID 5a** (§5.5 A1·A3 행을 범위·성공 기준으로 옮김). 병행: 5b = `plans/169`(N1(a)·O6 — 파일 겹침 0).
> **성격**: 동작 불변 구조 리팩터(D-337) — 함수 추출만. **모듈 이동 없음**(A2 God module 분할은 7b). 새 함수는 모두 `src/api/routes/query.py` 안에 두므로 patch seam·`_` import 경로가 그대로다(shim 0).
> **관련 결정**: D-337(구조 리팩터 규약) · D-303(회귀 — 계획 끝 1회) · D-066(4경로 대칭) · D-183(감사 선기록) · D-248(턴 기록 `turn.stream`/`turn.response`) · D-161 · D-255(사용자 접점 변경 0 → 매뉴얼 대상 아님)
> **적용 스킬**: `.claude/skills/refactoring/`
> **계획 시작 SHA**: `3a0a1101`(RCA · 2026-10-16) · **회귀 대상 파일**: W1·W2 — `src/api/routes/query.py` 1 · 가드 보강 — `tests/test_api/test_thread_history.py` · 새 `tests/test_api/test_plan168_stream_builder_contract.py` *(계획 끝 회귀 1회 실행: §4.3 · 가드 보강은 교정 라운드 재실행: §4.4)*

---

## 1. 현황 실측 (HEAD `3a0a1101` · `src/api/routes/query.py` 4,377줄)

| 핸들러 | 줄 | 길이 | 구조 |
|---|---|---:|---|
| `process_query` (`POST /query`) | 2518–2717 | 200 | 감사·턴 기록 → 체크포인트 → 사전 게이트 3종 → 입력 조립 → `ainvoke` → 응답 dict → 저장 |
| `process_query_stream` (`POST /query/stream`) | 2723–3242 | 520 | 위 사전 게이트를 SSE 미니 제너레이터 3개로 반복 → `event_generator`(2843–3232) |
| `process_file_query` (`POST /query/file`) | 3253–3464 | 212 | 감사 → 확장자 → 사전 게이트 2종 → 크기·DRM·CSV → `create_initial_state` → `ainvoke` → 응답 dict |
| `process_file_query_stream` (`POST /query/file/stream`) | 3567–4089 | 523 | 위 사전 게이트를 SSE 미니 제너레이터 2개로 반복 → `event_generator`(3715–4079) |

- **스트림 본문 중복**: 두 `event_generator`의 astream 루프(2860–3108 ↔ 3731–3961)는 done 이벤트 조립부를 빼면 주석 몇 줄만 다르다. ainvoke 폴백·예외 처리도 같은 골격.
- **SSE 응답 헤더 dict 7곳**: 2773·2798·2819·3234·3618·3642·4081. **2776(텍스트 선택 거부)만 `X-Accel-Buffering` 없음** — 바이트 불변이므로 이 차이를 보존한다.
- **조기 반환 미니 제너레이터 5개**: 2766·2789·2811(텍스트) · 3610·3633(파일).

### 1.1 경로 간 차이 — 보존 대상 (공통화 시 인자로 받는다)

| 항목 | 텍스트 스트림 | 파일 스트림 |
|---|---|---|
| `_scope_state` 시드 | `{**(checkpoint_state or {}), **input_state}` | `dict(initial_state)` |
| 그래프 입력 | `input_state` | `initial_state` |
| 처리 상한 | `effective_timeout`(폼필 답변이면 파일 상한) | `limit_sec`(파일 상한) |
| done `status` | `awaiting_approval` → 후단 존 게이트(`zone_clarification`) 또는 기간 되묻기면 `clarification` | 기간 되묻기면 `clarification`, 아니면 `completed` |
| done `awaiting_approval` | `output.get("awaiting_approval", False)` | 상수 `False` |
| `source_switch` | 누적 상태 값 | 상수 `None` |
| `_apm_source_fields` | 응답·done 둘 다 | 없음 |
| 저장 dict 추가 키 | — | `uploaded_file`·`uploaded_file_name` |
| `apm_source_pending`/`apm_source_scope` | done 뒤 `async for _ in _events: pass` | 없음 |
| 폴백 `TimeoutError` 문구 | 「처리 시간이 초과되었습니다. 질의를 단순화해주세요.」 | 「처리 시간이 초과되었습니다.」 (astream 절단 문구는 둘 다 앞의 긴 문구) |
| `db_scope` 선택 | `body.selected_db_ids` | `selected_list` |
| `viz_store_fields(user_query=)` | `body.query` | `query` |

비스트림 두 경로도 키 구성이 다르다(텍스트: `awaiting_approval`·`approval_context`·`scope_reexpand` 직접 조립·`_apm_source_fields` / 파일: `clarification`은 값이 있을 때만·`source_switch: None`·업로드 원본 저장). **dict 키 순서도 SSE·저장 JSON 바이트에 들어가므로 보존한다.**

## 2. 범위와 성공 기준 (166 §5.5 · §3 A1·A3)

| 스멜 | 처방 | 성공 기준 |
|---|---|---|
| **A1** Duplicated code | `_stream_graph_turn(...)`(공통 스트림 본문 — astream 루프·폴백·절단·예외) · `_build_turn_response(...)`(응답 dict·저장 dict·done 페이로드) · `_sse_response(gen)`(헤더). 차이(§1.1)는 **인자로** 받는다. Template Method(추상 클래스)는 훅 3개라 쓰지 않는다(166 §3 A1) | W0 done 스냅샷(`test_plan166_w0_done_payload_keys.py`) **무갱신 통과** · stream 테스트 15파일 통과 · query.py 줄 수 감소 |
| **A3** Long method | `_run_pre_gates(...) -> PreGateResult \| None` — 인가 → 선택 거부 반환 → 존 역질문(→ 범위 사전 선택) → 메모리 삭제의 **현행 순서 보존** | `test_plan104_db_authz.py`·`test_plan132_zone_gate_defer.py`·`test_query_audit_path.py`·`test_plan147_w3_scope.py` 통과 |

**호출자에 남기는 것(166 §5.5)**: `_scope_state` 시드 값 계산 · `_build_turn_input_state`/`create_initial_state` 호출과 `request_deadline`·`turn_disclosures` 세팅 · 감사(`_audit_user_request`)·`TurnRecorder`·`_shadow_input_guard` 순서 · 파일 검증(확장자·크기·DRM·CSV)과 그 `JSONResponse`/`HTTPException` 분기.

**하지 않는 것**: 경로 간 비대칭 해소(W0 부록 발견 4 — 행동 변경 · D-337 ⑥) · 오류 문구 변경(163 S3 정본 `src/api/error_detail.py`·`src/utils/user_error.py` 그대로 사용) · SSE 상수 사본(정본 `src/api/sse_events.py`) · 다른 모듈로의 이동(7b A2).

**사전 게이트 실측(HEAD)**: 텍스트 두 경로 = `apply_selection_authorization`·`apply_source_selection_authorization`·`apply_apm_source_selection_authorization` → 거부면 반환 → `_zone_clarification_or_none` → (없으면) `_scope_select_or_none` → 있으면 `_audit_clarification` 후 반환 → `_form_memory_delete_or_none`. 파일 두 경로 = 확장자 검사 → `_parse_selected_db_ids_form` → `apply_selection_authorization` → 거부면 반환 → `_file_zone_clarification_or_none` → 있으면 감사 후 반환 (메모리 삭제·소스 인가 없음). 확장자 오류 응답은 비스트림 `HTTPException` 문구와 스트림 `JSONResponse` 문구가 다르다(보존).

## 3. Wave

| Wave | 범위 | 담당 |
|---|---|---|
| **W1** | `_sse_response` + `_run_pre_gates`(A3) — 사전 게이트 4핸들러 적용 | implementer 1 |
| **W2** | `_stream_graph_turn` + `_build_turn_response`(A1) — 두 `event_generator` 본문과 비스트림 응답 조립 | implementer 1 |

- 두 Wave 모두 `src/api/routes/query.py` 한 파일이라 순차. 테스트 파일 수정 0이 기준(D-337 ②).
- Wave 중간 검증: 자기 테스트 + `python scripts/regress.py --base 3a0a1101 --no-tests --files src/api/routes/query.py`.
- 계획 끝(W2 뒤) 1회: `python scripts/regress.py --base 3a0a1101 --files <회귀 대상 파일…>`.

## 4. 결과 (2026-10-16 · 커밋 전)

### 4.1 바뀐 것 — `src/api/routes/query.py` 한 파일 (4,377 → 4,256줄 · −121 · shim·별칭 0)

| 함수 | 스멜 | 내용 |
|---|---|---|
| `_sse_response(gen, *, accel_buffering=True)` | A1 | SSE 응답 헤더 7곳 → 1곳. 텍스트 스트림 선택 거부만 `accel_buffering=False`(현행 헤더 보존) |
| `_single_done_stream(payload)` | A3 | 조기 반환 미니 제너레이터 5개 → 1개 |
| `PreGateResult` · `_run_text_pre_gates(...)` | A3 | 텍스트 두 진입의 존 역질문 → (없으면) 범위 사전 선택 → 메모리 삭제 판정. 순서 현행 그대로 |
| `_stream_graph_turn(...)` | A1 | 두 스트림 진입의 SSE 본문 공통(시작 상태 · astream 루프 전체 · 토큰 보충·META · 드레인 · ainvoke 폴백 · 절단·예외 · 마감 해제). 경로 차이는 인자(`graph_input`·`scope_seed`·`limit_sec`·`fallback_timeout_message`·`user_query`·`thread_id`)와 빌더 클로저 2개(`stream_done`·`fallback_done`)로 받는다 |

호출자에 남은 것(166 §5.5): `_scope_state` 시드 계산 · 상태 조립 · 감사·턴 기록 순서 · 파일 검증. `apm_source_pending/scope` 드레인(`async for _ in _events: pass`)은 done 뒤 그대로.

### 4.2 이탈 — 소스 문자열 계수 가드 (근거·되돌리는 비용)

query.py **소스 텍스트의 출현 횟수**를 세는 테스트가 12개 이상 있다(`test_plan121_plan_summary`·`test_plan121_stream_dependency_notes`·`test_db_scope_contract`·`test_plan160_verify`(`_store_result` 정확히 10)·`test_plan104_db_authz`·`test_host_authz`·`test_plan125_source_permissions`·`test_v7_audit_and_estimate`(역질문 감사 4곳) 등). 테스트 단언을 바꾸지 않는다는 규칙(D-337 ②) 아래에서 다음은 **진입마다 한 벌씩 남겼다**:
- 응답 dict · `_store_result` · done 페이로드 리터럴 — 스트림은 빌더 클로저 4개(텍스트·파일 × astream·폴백) 안, 비스트림은 핸들러 안. 그래서 **`_build_turn_response`는 만들지 않았다**(앞쪽 공통 키만 뽑으면 순감소 약 20줄에 헬퍼 2개 — 실익 없음).
- 선택 인가·선택 거부 반환·역질문 감사 호출·사전 게이트 응답 렌더 — 그래서 `_run_pre_gates`는 인가를 빼고 텍스트 전용(`_run_text_pre_gates`)이 됐고, 파일 진입은 판정이 한 줄이라 인라인 그대로다.
- 되돌리는 비용: 가드를 「공유 헬퍼를 네 진입이 부른다」 형태로 바꾸는 테스트 수정(약 8파일)을 승인하면 위 리터럴을 한 벌로 줄일 수 있다(빌더 4개 약 330줄 중 상당 부분). 승인 전에는 현 구조가 규칙 안의 최대치다.
- 그 밖 이탈(런타임 동등): `except asyncio.TimeoutError` → `except TimeoutError`(3.11+ 같은 클래스 · 옮긴 줄의 ruff UP041) · 옮긴 줄 E501 줄바꿈 4건 · mypy용 타입 주석 · `_form_memory_delete_or_none` 인자 타입 `dict | None`으로 정정. 평가 시점 변화 3건(`scope_seed` 핸들러에서 계산 · 드레인 판정 done 전 · 스트림 역질문 `_pre_gate_plan_summary()` 즉시 평가)은 관찰 가능한 차이 없음(verifier 하네스 실측).

### 4.3 검증

- **차분 실행(verifier)**: 기준선 `3a0a1101` worktree vs 기준선+현 `query.py`만 얹은 worktree — **87케이스 · 차이 0**(SSE 원문 줄·`data:` JSON 키 순서·응답 헤더 순서·`_store_result` 저장 dict 키 순서·게이트/감사 호출 순서·그래프 소비 로그). 케이스: 텍스트·파일 스트림 각 33(정상·토큰 보충·폴백·무출력·TypeError 폴백·승인 대기·후단 존·기간 되묻기·source_switch·드레인·절단·폴백 타임아웃·예외·빌더 예외) + 사전 게이트 20 + 기타 4. 예외 원문 미노출 6케이스 확인(plans/163 S3).
- **W0 done 스냅샷** 무갱신 통과. 핵심 31파일 761 passed(기준선과 같음 · 원래 실패 1).
- **게이트**: arch error 0 · 경고 17 · overfit·config 폴백 통과 · ruff/mypy 이번 diff 줄 0 · `prompt_render_diff --ci` 0 · import 스모크(`src.api.server`·`src.graph`).
- **계획 끝 회귀(1회)**: `python scripts/regress.py --base 3a0a1101 --files src/api/routes/query.py` — 본체 통과 2,221 · 실패 2 · 건너뜀 7. 귀속: [원래 실패] `test_plan160_chart_api.py::test_create_app_registers_viz_routes_only_when_chart_enabled[True]` / [이번 변경 탓] `test_v7_audit_and_estimate.py::TestR6ClarificationAudit::test_every_pre_gate_entry_point_records_the_fire`(W1이 역질문 감사 리터럴 4곳을 2곳으로 줄임) → 감사 호출을 네 진입으로 되돌려 교정 · 교정 라운드 8파일 204 passed. `[전체 회귀 권고]` 블록 없음. `범위: 모듈 단위 — 전체 미실행`.
- **verifier**: Critical 0 · **Major 1** · Minor 1 · Info 5.
  - Major — `tests/test_api/test_thread_history.py::test_entry_points_wrap_every_exit`가 공허해졌다: 핸들러 안 `StreamingResponse(...)` 호출만 검사하는데 이제 핸들러는 `_sse_response(turn.stream(...))`를 부른다. 현재 코드는 스트림 출구 7곳 모두 `turn.stream`으로 감싸 결함은 없고, 본문 2곳은 동적 테스트가 계속 잡지만 **조기 반환 5곳은 지키는 테스트가 없다**(변이로 확인). 처방은 가드가 `_sse_response`도 보게 하는 테스트 보강 — 단언 추가라 승인 대상(사용자 판단).
  - Minor — 빌더 호출 계약(키워드) 불일치가 나면 TypeError가 astream 안쪽 `except (AttributeError, TypeError, …)`로 가 조용히 폴백한다(현재 4빌더 키워드 일치 · 빌더 본문 TypeError는 종전과 같은 길). 후속에서 빌더 시그니처 고정 테스트 권고.
- **매뉴얼**: 대상 아님 — 사용자 접점 변경 없음(D-255).

### 4.4 가드 보강 (사용자 결정 2026-10-16 — 「테스트 수정 0」의 예외)

- **사유**: W1의 `_sse_response`가 `test_thread_history.py::test_entry_points_wrap_every_exit`(D-248 턴 기록 가드)를 공허하게 만들었다(§4.3 Major). 사용자가 보강을 결정 — D-337 ②의 「테스트 수정 0」 예외이며 단언 완화 없이 **검사 대상을 넓히기만** 했다.
- **내용**: 검사 호출 이름에 `_sse_response`를 추가(첫 인자 `turn.stream(...)` 요구 그대로) · 스트림 진입의 SSE 출구 수 하한(`process_query_stream` ≥4 · `process_file_query_stream` ≥3)으로 공허 통과 차단. `_single_done_stream` 출구는 `_sse_response`의 인자라 같은 검사에 걸린다.
- **verifier Minor 반영**: 새 `tests/test_api/test_plan168_stream_builder_contract.py`(4케이스) — `_stream_graph_turn` 안 `stream_done`·`fallback_done` 호출의 위치 인자 수·키워드 집합이 두 스트림 진입의 빌더 클로저 시그니처와 같은지(기본값·`*args`·`**kw` 없음) AST로 고정.
- **실효 확인(변이 · scratchpad 사본 — 제품 파일 무변경)**: 파일 스트림 조기 반환 출구에서 `turn.stream` 제거 → FAIL · 텍스트 스트림 본문을 `StreamingResponse` 직접 호출(기록기 없음)로 → FAIL · 텍스트 `_stream_done` 키워드 하나 개명 → 계약 테스트 FAIL. 원본은 전부 PASS.
- **재실행(교정 라운드)**: `test_thread_history.py`·`test_plan168_stream_builder_contract.py`·`test_plan166_w0_done_payload_keys.py`·`test_plan119_stream_timeouts.py` 115 passed · 1 skipped · `regress.py --base 3a0a1101 --no-tests --files <위 테스트 2>` 종료 0(`범위: 정적 게이트만 — 테스트 미실행`).

## 5. 후속 (다른 계획으로)

- **계수 가드 전환 — 7b(A2) 착수 전 사용자 결정 필요**(2026-10-16 사용자: 「7b 착수 때 정한다」). 응답 리터럴을 한 벌로 줄이려면 출현 횟수를 세는 가드 약 8파일(§4.2)을 「네 진입이 공유 헬퍼를 부른다」 형태로 바꿔야 한다. 정하기 전에는 현 구조 유지.
- 비스트림 두 핸들러(`process_query`·`process_file_query`)의 응답 조립 공통화도 같은 결정에 묶인다.

## 6. 이력

- 2026-10-16 v1.0 — 작성·착수(166 단계 5a). 줄 번호는 W2 이동 뒤 HEAD `3a0a1101`로 재실측.
- 2026-10-16 v1.1 — W1(`_sse_response`·`_run_text_pre_gates`·`_single_done_stream`) · W2(`_stream_graph_turn` + 빌더 클로저) 완료 · 계획 끝 회귀 1회(이번 변경 탓 1건 교정) · verifier 차분 87케이스 동일 · Major 1(가드 공허화)·`_build_turn_response` 미추출은 §4.2·§4.3.
- 2026-10-16 v1.2 — 사용자 결정 2건 반영: ① 턴 기록 가드 보강 + 빌더 계약 테스트(§4.4) ② 계수 가드 전환은 7b 착수 전 결정(§5). **완료** — 파일명 `168-WIP-…` → `168-query-stream-dedup.md`.
