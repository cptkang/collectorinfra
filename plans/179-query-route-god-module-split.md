# 179. 질의 라우트 God module 분할 — 계수 가드 전환·응답 리터럴 한 벌 · 진행 요약 표 주도 dispatch · `src/api/query/` + application 판정 모듈 + 재노출 shim (166 단계 7b · 스멜 A2)

> **작성일**: 2026-10-16 · **v1.0**
> **상태**: **완료**(2026-10-16 · 커밋 전 — 메인이 커밋) — query.py 4,256→2,007줄 · 새 `src/api/query/` 8모듈(1,806줄) + `src/pipeline/turn/` 4모듈(718줄) · 응답·사전 게이트·done 리터럴 한 벌(`_store_result` 10→5곳 · 빌더 클로저 4개 제거) · 진행 요약 표 주도 dispatch(cc 56→3) · 계수 가드 8파일 AST 전환(변이 M1~M19 FAIL) · 계획 끝 회귀 원래 실패 1만(모듈 단위) · verifier 차분 326케이스 0 · Critical 0 · Major 2(보호 공백 → 새 테스트로 메움) · 보안 감사 인가·감사 누락 0 · §4
> **상위**: `plans/166` §0.1 단계 7 · **계획 ID 7b**(스멜 A2 — §3 A2 행 · §5.7 W5 표 · §5.0 공통 규약 · §8 범위 밖). 선행 5a(`plans/168`) 완료. 같은 시간에 6c(`plans/174` — `src/graph.py`·`src/orchestration/`·`src/routing/registry.py`)·7c(`plans/177` — `noise_gate/`)가 돈다. 6d(`plans/173`)가 바꾼 `src/api/server.py`·`src/nodes/output_generator.py`는 커밋 전이다 — 이 계획은 그 내용을 고치지 않는다.
> **성격**: 동작 불변 구조 리팩터(D-337 · 166 §5.0) — SSE 원문 줄 · `data:` JSON 키 순서 · 응답 헤더 순서 · `_store_result` 저장 dict와 키 순서 · 게이트·감사·인가 호출 순서 · 예외 원문 미노출(163 S3) 불변 · W0 done 스냅샷 갱신 금지. **예외 1건(사용자 결정 2026-10-16)**: 168 §4.2의 소스 계수 가드를 「네 진입이 공유 헬퍼를 부르고 공유 헬퍼가 그 동작을 한다」 검사로 전환한다(§3 표). 그 밖의 테스트 수정 0.
> **계획 시작 SHA**: `1ad7c161`(RCA) + 단계 6·7d 미커밋 변경 위 — 기준 사본 `scratchpad/p179_base/`(2026-10-16 17:44:25 · `.venv`·`build`·`logs`·`.git`·`node_modules` 제외 rsync). 착수 시 `src/api/routes/query.py`는 HEAD와 같다(4,256줄).
> **회귀 대상 파일(누적)**: 23 + verifier 새 테스트 1 — `src/api/routes/query.py` · 새 `src/api/query/{__init__,store,sse,progress,fields,gates,deadline,turn_input,upload}.py` · 새 `src/pipeline/turn/{__init__,approval,zone,selection,intake}.py` · 가드 전환 8파일(§3) *(누적 끝 — 계획 끝 회귀 1회 실행: §4.2)* · verifier 새 테스트 `tests/test_api/test_plan179_verify.py`
> **관련 결정**: D-337(리팩터 규약 — shim·seam·`_` 공개명+별칭·행동 변경 분리) · D-303(회귀) · D-331(arch 경고) · D-088(overfit 기준선 경로 키) · D-255(매뉴얼 — 사용자 접점 변경 0이라 대상 아님) · D-248(턴 기록 가드)
> **적용 스킬**: `.claude/skills/refactoring/`

---

## 1. 범위 — 착수 시 실측(기준 사본)

- `src/api/routes/query.py` 4,256줄 · 최상위 함수 약 100개 · 라우트 10개(질의 진입 4 · 결과·다운로드·매핑 6).
- 이 모듈을 import하는 테스트·스크립트 82파일(`tests`·`noise_gate/tests`) · 본체 import 2곳(`src/api/routes/result_viz.py` — `_audit_file_download`·`_owned_result` / `src/api/routes/apm_jobs.py` — `_attachment_disposition`·`_audit_file_download`) · `scripts/manual/apm_harness.py`.
- 테스트가 이 모듈에서 바꿔 끼우는 seam(팀 리드 grep — `patch("src.api.routes.query.…")`·`setattr(<별칭>, "…")`·`patch.object`·직접 대입): `_zone_clarification_or_none` · `_file_zone_clarification_or_none` · `_audit_user_request` · `apply_selection_authorization` · `_get_checkpoint_state`. 읽기 전역: `_results_store`(재대입 없음).
- 이 모듈 소스를 읽는 가드(`read_text`·`ast.parse`·`inspect.getsource`·`__file__`): §3.

## 2. Wave

| Wave | 내용 | 성격 |
|---|---|---|
| W1a | 계수 가드 전환(§3) → 응답 dict·`_store_result`·done 페이로드 리터럴과 역질문 감사·선택 인가를 한 벌로(`_build_turn_response` 등) | 로직 통합 + 가드 테스트 수정(사용자 결정 예외) |
| W1b | `_extract_node_progress` 표 주도 dispatch(`dict[node_name → summarizer]`) | 로직 분해 |
| W2 | 순수 이동 — SSE·응답 전용 → `src/api/query/` · 순수 판정(역질문·인가·승인 해석) → application 모듈 · `query.py`는 라우트 + 전 심볼 재노출 shim | 순수 이동 |

W1a·W1b는 같은 파일의 겹치지 않는 영역이라 병렬로 돌렸다(W1b는 사본에서 만들고 Edit 1회로 반영). W2는 W1 끝 사본(`scratchpad/p179_w1state/`) 위에서 단독.

## 3. 가드 전환 표 (W1a — 사용자 결정 2026-10-16의 예외)

변이는 사본(`scratchpad/p179/w1a_mut`)에서 돌렸다 — 원본 PASS · 변이 M1~M19·M15b 전부 FAIL(`scratchpad/p179/w1a/mutations.json`). 계수가 그대로라 손대지 않은 가드: `test_host_authz`·`test_plan125_source_permissions`·`test_plan118_replan_budget`·`test_query_audit_path`·`test_doc_qa/test_api_and_prefix`·`test_wiring_parity`·`test_plan122_runner_inputs`·`test_query_result_access`·`test_multiturn` 2파일.

| 파일 | 단언 전 | 단언 후 | 변이(→ FAIL) |
|---|---|---|---|
| `tests/test_api/test_plan121_plan_summary.py` | 소스 계수 `_plan_summary_field(result)` 4 · `(_scope_state)` 2 · `_plan_summary_carry` 4 · `_pre_gate_plan_summary` 4 | 진입별로 사전 게이트·렌더·응답 빌더를 1회씩 부름 · `_stream_graph_turn`이 astream 빌더 1·폴백 빌더 1·done 빌더 2를 부름 · 헬퍼 본문이 계획 요약 칸을 펼침 | M1 응답 dict 칸 제거 · M2 파일 비스트림 직접 조립 · M4 · M9 · M19 사전 게이트 역질문 칸 제거 |
| `tests/test_api/test_plan121_stream_dependency_notes.py` | done의 `_dependency_notes_field(response_data)` 4 · db_scope 줄 4 · `(_scope_state)` 2 | done 빌더 2곳 · 노트 칸이 db_scope 바로 뒤 · astream 인자·조건식 · 종료 노드 델타(`output`) 사용 없음 | M3 노트 칸 제거 · M4 델타로 조립 · M5 |
| `tests/test_api/test_db_scope_contract.py` | 응답 리터럴 `"db_scope": response_data.get("db_scope")` 4 · `selected_db_ids=body…` 3 · `=selected_list` 3 | 리터럴 1벌 · 진입→빌더 경로 · 빌더별 state 인자(result/scope_state) · done 빌더 · 진입별 `TurnContext(selected_db_ids·text_entry)` | M2 · M4 · M5 done db_scope 제거 · M6 텍스트 스트림 ctx 선택 None |
| `tests/test_api/test_plan160_verify.py` | AST `_store_result` 호출 정확히 10 | 5곳 + 진입→공통 본문→절단 함수 경로마다 저장 1회 · viz 칸 단언 유지 | M2 · M7 astream 저장 제거 · M8 부분 결과 viz 칸 제거 |
| `tests/test_api/test_plan104_db_authz.py` | `apply_selection_authorization(` 5 · `SELECTION_DENIED_MESSAGE` 5 · 문자열 위치 | 진입별 사전 게이트 1회 · 게이트 안 인가 호출 · 거부 반환문 · 렌더의 `gate.response` · 게이트가 상태 조립보다 앞 | M9 파일 스트림 게이트 생략 · M10 인가 호출 제거 · M11 거부 반환 제거 · M12 게이트를 상태 조립 뒤로 |
| `tests/test_scope_select/test_v7_audit_and_estimate.py` | 역질문 감사 리터럴 4곳(`route.__file__` 정규식) · `_audit_scope_narrowed(...)` 2 | 진입별 thread_id 값(실제값 2·요청값 2) · 역질문 분기 첫 문장이 감사·마지막이 return · 감사 호출 1곳 · `_audit_scope_narrowed` 2 유지 | M9 · M13 감사 호출 제거 · M14 파일 비스트림 thread_id 요청값 |
| `tests/test_api/test_thread_history.py` | 스트림 진입 SSE 출구 하한 4/3 | 사전 게이트 조기 반환이 출구 1곳으로 합쳐져 하한 2/2 · **추가**: 비스트림 출구가 전부 `await turn.response(...)`(cast 벗겨 확인) · 응답 출구 하한 3/3 | M15 비스트림 게이트 응답을 기록기 없이 반환(종전 가드는 통과했을 변이) · M15b · M16 파일 스트림 게이트 출구 `turn.stream` 제거 |
| `tests/test_api/test_plan168_stream_builder_contract.py` | 두 스트림 진입의 빌더 클로저 4개 시그니처 = `_stream_graph_turn` 안 호출 | 클로저 제거 → 공통 본문의 공유 빌더 호출과 정의 시그니처 일치 · 스트림 진입의 `ctx` 전달·`TurnContext` 1회 · 폴백 `'fallback'`·비스트림 `'json'` 모드 | M17 done 빌더 키워드 개명 · M18 폴백 mode 바꿈 |

## 4. 결과

### 4.1 구조

| 대상 | 전 | 후 |
|---|---|---|
| `src/api/routes/query.py` | 4,256줄 | 2,007줄 — 라우트 10개 + seam 호출자 + 소스 가드가 이름으로 찾는 공유 헬퍼 + 재노출 shim |
| 응답 dict · done 페이로드 · `_store_result` | 진입·빌더마다 한 벌(응답 6벌 · done 4벌 · 저장 10곳) | `_turn_response_data`·`_turn_done_payload` 한 벌 · 저장 5곳 · `TurnContext`(frozen)로 진입 차이를 인자화 · 빌더 클로저 4개 제거 |
| 사전 게이트 | 텍스트 전용 `_run_text_pre_gates` + 파일 진입 인라인 · 인가·감사 진입마다 | `_run_pre_gates`(선택 인가 → 거부 반환 → 존 역질문/범위 사전 선택 → 역질문 감사 → 메모리 삭제 · 순서 불변) + 렌더 `_pre_gate_response`·`_pre_gate_done` |
| `_extract_node_progress` | 172줄 · cc 56 · 노드명 분기 16곳 | 16줄 · cc 3 · `_NODE_PROGRESS_SUMMARIZERS` 23항목 + `_progress_*` 18개 |
| `src/api/query/`(interface) | — | `store`(결과 저장소·소유 확인·첨부 파일명·다운로드 감사) · `sse` · `progress` · `fields`(응답 칸) · `gates`(역질문·범위 선택·메모리 삭제 판정과 그 감사) · `deadline` · `turn_input` · `upload` |
| `src/pipeline/turn/`(application) | — | `approval`(승인 의사 해석) · `zone`(존 역질문·자리표시자) · `selection`(선택 인가·APM 소스 답) · `intake`(턴 고지·섀도·원문 시드) — `src.api`·`src.orchestration` import 0 |

- **남김 근거**(`scratchpad/p179/w2/keep.txt`): ① 라우트 10개 — 다운로드 라우트를 옮기면 `test_query_result_access.py:391` 계수 가드가 공허해져 「라우트 4종만」 대신 10개를 모두 남겼다(이탈 — 되돌리는 비용: 그 가드를 경로 무관 AST로 바꾸는 테스트 수정 1파일) ② W1a 전환 가드가 query.py AST에서 이름으로 찾는 헬퍼(`_stream_graph_turn`·`_build_*`·`_turn_*`·`_run_pre_gates`·`TurnContext` 등) ③ seam 호출자(`_partial_on_timeout` — `_get_checkpoint_state` patch) ④ `_build_turn_input_state`·`_audit_user_request`(텍스트 가드).
- **shim**: 재노출 이름 227개 전후 동일 · 옮긴 정의 118개 중 로직 차이 0(32개 타입 주석만 — `scratchpad/p179/w2/move_check.txt`) · 로거 이름 `src.api.routes.query` 유지 · 모듈 밖에서 쓰는 `_owned_result`·`_attachment_disposition`·`_audit_file_download`는 공개명(`owned_result` 등) + `_` 별칭(D-337 ③ · `result_viz.py`·`apm_jobs.py` import는 그대로).
- **이탈(런타임 동등)**: W1b — 해시 불가 `node_name`이면 debug 로그 1줄이 더 남는다(호출부는 항상 문자열). 비스트림 반환 6곳 `cast(QueryResponse, …)`(mypy 전용).

### 4.2 검증

- **계획 끝 회귀(1회)**: `python scripts/regress.py --base 1ad7c161 --files <23파일>` — 본체 통과 2,228 · 실패 1 · 건너뜀 7 · 귀속 [원래 실패] `test_plan160_chart_api.py::test_create_app_registers_viz_routes_only_when_chart_enabled[True]` · 정적 게이트 전부 통과 · `[전체 회귀 권고]` 블록 없음 · `범위: 모듈 단위 — 전체 미실행`. **주의**: git 기본 diff 알고리즘이 대량 삭제 뒤 손대지 않은 `return create_initial_state(` 줄(기존 mypy `no-any-return`)을 「이번 diff 줄」로 짝지어 mypy가 실패로 집계한다 — `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=diff.algorithm GIT_CONFIG_VALUE_0=histogram`으로 돌렸다(기본 알고리즘에서는 그 1줄만 실패).
- **verifier**(차분 326케이스 — SSE 191·JSON 135 · 기준 사본 vs 현 트리 · SSE 원문 줄·키 순서·헤더 순서·저장 dict·감사·인가 호출 순서·로그): **차이 0**. Critical 0 · **Major 2** · Minor 1.
  - Major 1 — W1a가 `test_plan104_db_authz::test_gate_precedes_state_assembly`를 호출 순서 검사로 바꾸면서 종전의 대입문(`selected_list, _selection_denied = …`) 위치 검사가 빠졌다. 파일 진입이 인가 결과를 버리고 요청 원문 선택으로 상태를 조립하는 변이(V1·V2)와 인가한 선택을 본문에 되쓰지 않는 변이(V22)가 기존 81파일에서 살아남았다(하류 `allowed_db_ids` 필터가 있어 즉시 우회는 아님).
  - Major 2 — 공유 헬퍼로 합친 진입별 분기 7개(텍스트 스트림 거부 응답 `X-Accel-Buffering` · 파일 스트림 done 승인 대기 · 파일 스트림 드레인 · 비스트림 null clarification 키 · 사전 게이트 done 키 순서 · 파일 진입의 질의 원문 전달)를 지키는 테스트가 없었다(V6·V7·V9·V10·V11·V23/V23b 생존).
  - Minor — query.py 부정 단언 4건(`test_db_scope_contract.py:85` · `test_plan121_stream_dependency_notes.py:79` · `test_wiring_parity.py:44` · `test_doc_qa/test_api_and_prefix.py:48`)이 W2로 옮긴 모듈을 보지 않는다(변이 W2-a~c 생존).
  - **처방(교정 완료)**: verifier 새 테스트 `tests/test_api/test_plan179_verify.py`(13함수·37케이스 — 정적 + 네 진입 실행 · 부정 단언을 옮긴 모듈까지 적용) — 위 변이 23종 전부 FAIL로 포착. 기존 테스트 수정 0. 팀 리드 재실행 37 passed · `regress.py --no-tests` 종료 0.
- **security-auditor**: 진입별 인가·감사 호출 순서 전후 동일 — **인가 누락 0 · 감사 누락 0**. Critical·High·Medium 0. Low 2(기존 동작 — DRM 복호화 실패 `e.reason`이 502 detail로 나감 · 인메모리 결과 저장소 다중 워커 404) · Info 2(테스트가 `query._results_store`를 재대입하면 `store.py`는 옛 객체를 씀 · `apply_*`·`resolve_approval_action` patch는 옮긴 모듈 내부 호출에 닿지 않음 — 현재 그런 테스트 없음).
- **구현자 자기 검증**: W1a 하네스 87·134케이스 차이 0 · 변이 M1~M19·M15b FAIL · W1b 차분 8,646케이스 0 · W2 하네스 134·87케이스 0 · query 관련 80파일 2,040 passed(원래 실패 1) · `prompt_render_diff --ci` 0 · arch·overfit·config 폴백 통과.
- **매뉴얼**: 대상 아님 — 사용자 접점 변경 없음(D-255).

## 5. 후속

- **shim·별칭 제거**(D-337 ①③ — 다음 계획): 재노출 import 12블록 · `_owned_result`·`_attachment_disposition`·`_audit_file_download` 별칭. 166 단계 8에서 `grep -w` 0 확인 뒤.
- query.py에 이동으로 안 쓰이게 된 import 약 30건 — shim 이름 집합(227) 보존을 위해 남겼다. shim 제거 때 함께 정리.
- `src/api/query/gates.py`의 역질문 판정(`_zone_clarification_or_none` 등)은 seam이라 api에 남겼다 — application으로 내리려면 테스트 seam을 새 모듈로 옮기는 테스트 수정이 필요하다(D-337 주의 ②).
- 부정 단언 4건은 새 테스트가 옮긴 모듈까지 보지만, 원 가드 4파일 자체는 query.py만 본다(테스트 수정 0 규칙으로 유지).
- 매뉴얼 `scripts/manual/content/admin.md:4130`의 근거 위치 `src/api/routes/query.py` `_owned_result` — 재노출로 여전히 유효하나 정의는 `src/api/query/store.py`. 매뉴얼 개정 때 함께.
- `regress.py` 변경 줄 판정이 git 기본 diff 알고리즘에 의존한다(대량 이동 뒤 오탐) — 도구 측 `--histogram` 기본화는 별건 후보.
- 행동 변경 후보(별도 계획): DRM 복호화 실패 사유 원문의 502 detail 노출을 고정 문구로(plans/163 계열) · 텍스트 호출자의 `body.selected_db_ids` 재대입 중복(게이트 안에서 이미 기록 — 구조 정리 후보).

## 6. 이력

- 2026-10-16 v0.1 — 작성·착수(166 단계 7b). 기준 사본 rsync · seam·소스 가드 실측.
- 2026-10-16 v1.0 — W1a(가드 전환·리터럴 한 벌)·W1b(진행 요약 dispatch) 병렬 · W2(순수 이동) · 계획 끝 회귀 1회(원래 실패 1만) · verifier 차분 326케이스 0 · Major 2를 새 테스트로 메움 · 보안 감사 누락 0. **완료** — 파일명 `179-WIP-…` → `179-query-route-god-module-split.md`.
