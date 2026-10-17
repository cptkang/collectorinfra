# 184. 행동 변경 후보 1차 — 작은 고침 묶음 (plans/182 S 크기 + 결정 항목)

> **작성일**: 2026-10-17 · **v1.0**
> **상태**: **완료**(2026-10-17 · 커밋 전) — 182 1차 21건 처리(20 + S4b) · 계획 끝 회귀 이번 변경 탓 실패 0(교정 뒤 · 모듈 단위) · security-auditor Medium 2 교정(L1 안 A — 사용자 결정 · S4b) · verifier Critical 0 · Minor 5 처리·기록 · 매뉴얼 user.md 2곳
> **상위**: `plans/182` §1 후보 표 · §3 사용자 결정(2026-10-17). 사용자 결정 1 「작은 것 먼저 → 큰 묶음은 별도 계획」의 1차. J1은 `plans/185`(병행)가 맡는다.
> **성격**: 행동 변경(D-337 ⑥ — 구조 리팩터와 분리). 항목마다 바뀐 동작을 단언하는 테스트를 추가하고, 옛 동작을 단언하던 기존 테스트는 그 단언만 고친다(§3 목록).
> **계획 시작 SHA**: `13dc5a3f`(RCA · 작업 트리 깨끗 — 루트 `Claude outputs/`는 피어 산출물)
> **회귀 대상 파일(누적)**: §3.1 표의 제품 파일 19 + 테스트·스냅샷·매뉴얼 산출 20 (계획 끝 회귀 `--files` 39개 · §4.1) + 교정 `tests/test_orchestration/test_plan134_w34_apm.py`
> **관련 결정**: D-337 ⑥ · D-070(사용자/운영자 토큰) · D-153 부기(예외 원문 축약) · D-255(매뉴얼 동반) · D-251(사다리 2단 기준) · D-303(회귀)
> **범위 밖**: J1(→`plans/185`) · 182 「확인 필요」 S5·M4·M7·K4·K5·J4·P3 · 「유지」 항목 전부 · L3

---

## 1. 범위와 사용자 결정(2026-10-17 · 182 §3)

| ID | 내용 | 결정·수용 기준 |
|---|---|---|
| S1 | DRM 복호화 실패 502 `detail` | 고정 사유(`_RET_REASONS`·타임아웃·RET 미출력·산출 미생성)는 화면 유지. 설정 키 이름(`DRM 설정 누락: …`)·`java_bin` 경로가 든 사유만 고정 문구 + 요청 번호(`src/api/error_detail.py` 재사용 · 새 헬퍼 금지), 원문은 서버 로그 |
| S2 | 빈 `sub` 운영자 토큰 | `verify_admin_token`·break-glass·사용자 토큰 세 곳 모두 `not sub` → 401 |
| S3 | 결과 저장소 다중 워커 | 저장소 유지. 워커 2개 이상 기동을 검출하면 기동 로그 경고(검출 방법 실측) |
| S4 | APM 선택 메타·`source_status` 예외 원문 | 예외 종류 + 요청 번호, 원문은 로그 |
| S6 | `degraded_reason` 규약 | `secure-coding` 스킬 규칙 1줄(코드 0) |
| M1 | 첫 홉 이중 괄호 | 「(api_error · 요청 rid)」 꼴 |
| M2 | `SourceMcpError` 거부 고지 | `disc.dedupe` |
| M3 | deepagents 조립 실패 경고 | 「semantic_router로 폴백」 대신 실제 확정 단 이름 |
| M5 | 미존재 테이블 나열 | `sorted` |
| M6 | 보조 컬럼 표기 | 표기 집합 나열 |
| M9 | `_answer_impl` 독스트링 | 정정(동작 0) |
| K6 | `data_insufficient` `OrganizedData` | `sheet_mappings=None` 명시 |
| K7 | 텍스트 진입 `selected_db_ids` 이중 대입 | 한 곳만(인가 순서 확인 · 동작 동일) |
| K8 | 문서 응답 노트 | `DOC_NOTES`에 4종(`current_month_partial`·`limit_truncation`·`generator_notes`·`condition_changes`) 추가 + 사용자 매뉴얼(D-255) |
| J2 | 괄호 없는 현재시각 낱말 | `expression`으로 분류 |
| J3 | 보충 테이블 순서 | 정렬로 고정 · `prompt_render_diff` 기준선은 이 변경분만 갱신 |
| J5 | `fleet_tools` id 없는 인스턴스 | 건너뛰고 고지 |
| L1 | 인증 DB 중간 실패 | 대입 원복 + 풀 닫기 · 감사 로테이션 미시작 |
| L2 | `cache_mgr.disconnect()` 예외 | 삼키고 경고 |
| H1 | 설정 도움말 `TEXT2SQL_TEMPLATE_ASSEMBLY` | `config/settings_help/text2sql.yaml` 추가 → 원래 실패 1건 해소 |

## 2. Wave

| Wave | 담당 | 항목 | 파일(예정) |
|---|---|---|---|
| W1 | implementer A | S1·S2·S3·L1·L2·K7·S6 | `src/api/query/upload.py`·`src/infrastructure/drm/scsl.py`·`src/api/routes/admin_auth.py`·`src/api/dependencies.py`·`src/api/server.py`·`src/api/query/store.py`·`src/api/routes/query.py`·`.claude/skills/secure-coding/` |
| W2a | implementer B | S4·M1·M2·M3 | `src/orchestration/apm_query.py`·`src/orchestration/apm/`·`src/graph.py` |
| W2b | implementer C | K8(+매뉴얼)·K6·M9·H1 | `src/nodes/output_generator.py`·`src/nodes/result_organizer.py`·`src/doc_qa/service.py`·`config/settings_help/text2sql.yaml`·`scripts/manual/`·`src/static/manual/` |
| W3a | implementer D | M5·M6·J2·J3 | `src/sql_validation.py`·`src/domain/host_discovery.py`·`src/sql_time_conditions.py`·`src/nodes/schema_analyzer.py`·`prompt_render_diff` 기준선 |
| W3b | implementer E | J5 | `apm_gateway/apm_gateway/application/fleet_tools.py` |

병행 `plans/185`(J1)가 `src/nodes/query_generator.py`·`src/nodes/multi_db_executor.py`를 고친다 — 이 계획은 두 파일을 손대지 않는다.

## 3. 처리 결과 · 고친 기존 단언 · 스냅샷 갱신

### 3.1 항목별 처리

| ID | 바꾼 곳 | 새 동작 | 단언 테스트 |
|---|---|---|---|
| S1 | `src/infrastructure/drm/decryptor.py`(`DrmDecryptError(user_visible=…)` 키워드 전용 표지) · `scsl.py`(설정 누락·java 경로 2곳 `user_visible=False`) · `src/api/query/upload.py` | 표지가 False면 502 `detail` = 「…복호화에 실패했습니다: 서버 DRM 설정 오류 — 관리자에게 문의하세요 (요청 rid)」(요청 번호 `current_request_id`) · 원문은 실패 로그 1줄(요청 번호 포함 — verifier Minor 교정으로 이중 로그 제거). 고정 사유·타임아웃은 종전대로 화면 | `tests/test_api/test_plan184_security_lifecycle.py` `test_scsl_*`·`test_upload_*` |
| S2 | `src/api/routes/admin_auth.py` `verify_admin_token` | `if not username` → 빈 `sub` 401(사용자·break-glass는 이미 `not sub`) | 같은 파일 `test_*_empty_sub_*`·`test_admin_token_type_still_checked` |
| S3 | `src/api/server.py` `_detect_worker_count`·`_warn_multi_worker`(lifespan 선두) | CLI `--workers N`·`WEB_CONCURRENCY`(uvicorn 실측 — spawn 자식이 argv·environ 상속)로 2 이상이면 경고 1줄. 1이면 무출력. `--reload`가 있으면 1(uvicorn reload 모드는 workers 무시 — verifier Minor 교정). gunicorn·`uvicorn.run(workers=N)` 등은 검출 밖(한계) | `test_detect_worker_count`·`test_multi_worker_warns_once`·`test_single_worker_logs_nothing` |
| S4 | `src/orchestration/apm_query.py` `_retry_selection`·`_session_step` | 메타 `error`·`source_status.reason` = 「예외종류 (요청 rid)」, 원문은 warning 로그 | `tests/test_orchestration/test_plan184_apm_meta.py` |
| S4b | `src/orchestration/apm/calls.py` `_exception_error`(새 · `_run_calls`의 `SourceMcpError`) · `apm/targets.py` `_lookup` · `_EXC_TYPE_RE`가 새 꼴도 인식 | 호출 단위 예외 사유 = 「예외종류 (요청 rid)」(세션이 원문 없이 지은 `_CODE_PHRASE_RE` 사유는 그대로), 원문은 warning 로그 → `meta.failures`·`source_status.reason`·`LinkEntry.reason`에 원문 없음 · 고지에는 종류가 보이고 요청 번호는 한 번만(verifier·auditor 지적 → 팀 리드 지시로 이번에 처리) | `test_plan184_apm_meta.py` 추가분 · `test_plan184_verify.py::test_call_failure_source_status_reason_hides_exception`(strict xfail 해제 → 통과) |
| S6 | `.claude/skills/secure-coding/SKILL.md` §C | 규칙 1줄 추가(코드 0) | — |
| M1 | `apm_query.py` `_RID_TAIL_RE`(`rank_error` 생성부) | 「부하 순위를 받지 못해(api_error · 요청 rid)」 | `test_rank_failure_notice_has_no_nested_parentheses` 외 2 |
| M2 | `apm_query.py` `_session_step` | `SourceMcpError` 거부도 `disc.dedupe(ctx.notices)` | `test_gateway_down_refusal_dedupes_notices` |
| M3 | `src/graph.py` `build_graph` | 경고를 `resolve_ladder_tier` 뒤로 옮겨 확정 단 이름(`_tier.value`)을 찍는다 | `tests/test_orchestration/test_plan184_ladder_warning.py` |
| M5 | `src/sql_validation.py` `_check_references` | `sorted(unknown_tables)` | `tests/test_plan184_unknown_tables_order.py`(해시 시드 0·1·2) |
| M6 | `src/domain/host_discovery.py` `_decide_single` | 보조 컬럼 표기를 겹침 없이 시스템 순서로 나열(하나면 종전 바이트 동일) | `tests/test_cross_system/test_plan184_weak_labels.py` |
| M9 | `src/doc_qa/service.py` `_answer_impl` 독스트링 | 정정(동작 0) | — |
| K6 | `src/nodes/result_organizer.py` | `data_insufficient`에 `sheet_mappings=None` | `tests/test_nodes/test_plan184_result_organizer.py` |
| K7 | `src/api/routes/query.py` `process_query`·`process_query_stream` | 게이트 안 본문 대입만 남기고 호출자 재대입 제거(`_, gate = …`). 인가 순서 불변 | `test_pre_gate_writes_authorized_selection_to_body`·`test_text_entries_do_not_reassign_selection` |
| K8 | `src/nodes/output_generator.py` `DOC_NOTES` | 4종 추가(텍스트 순서 그대로 — 결과적으로 `DOC_NOTES == TEXT_NOTES` · 명시 튜플 유지) | `tests/test_nodes/test_plan184_doc_notes.py` |
| J2 | `src/sql_time_conditions.py` `_is_plain_column` | 토큰 하나가 `_NOW_FUNCTION_RE`에 fullmatch면 식(`expression`). 큰따옴표 식별자·접두 이름은 컬럼 유지. 소비처 영향: 현재시각 낱말이 든 SQL은 ①「현재시각 함수 금지」로 종전대로 반려되고, 덧붙던 「경계 불일치」 오류만 빠진다(`NOW()` 경로와 같아짐) | `tests/test_plan184_now_word_expression.py`(18) |
| J3 | `src/nodes/schema_analyzer.py` | 보충 순서 = 프로필 `allowed_tables` 기재 순서 · 알람 핵심 테이블 튜플 고정. `prompt_render_diff`·`prompt_render_sha256` 스냅샷은 이 경로를 거치지 않아 **영향 없음**(갱신 0) | `tests/test_nodes/test_plan184_schema_analyzer_order.py`(해시 시드 0·1·2) |
| J5 | `apm_gateway/apm_gateway/application/fleet_tools.py` | id 없는 인스턴스는 `application_status` 미호출 · `[한계]` 1줄 고지 · `instances_total`·`instances_unranked`에 포함 | `apm_gateway/tests/test_plan184_fleet_no_id.py` |
| L1 | `src/api/server.py` `_init_auth_db` | 풀 생성 뒤 실패 시 **인증 비활성**이면 state 6종 None 원복 + 풀 닫기 → 감사 로테이션 미시작. **인증 활성**이면 원복하지 않고(종전 state·풀·로테이션 유지) 경고만 「인증 DB 초기화 일부 실패 — 인증 활성이라 사용자 조회 연결은 유지합니다」로 정정. **사용자 결정 2026-10-17 안 A** — 근거: 원복하면 `get_current_user`가 payload-only 폴백(전 DB 허용·is_active 미조회)으로 인가를 강등한다(security-auditor Medium). 원복 범위(비활성일 때 state 6종 — AuditService DB 미러 포함)는 계획대로(verifier Minor ③ 기록). payload-only 폴백 자체(B·C안)는 `plans/182` L4 후보 | `test_auth_db_failure_reverts_state_and_closes_pool`·`test_auth_db_failure_keeps_state_when_auth_enabled`·`test_auth_db_pool_failure_keeps_old_warning`·`test_auth_db_success_keeps_state` |
| L2 | `src/api/server.py` lifespan 종료 | `cache_mgr.disconnect()` 예외를 삼키고 warning · 「서버 종료」 로그 유지 | `test_shutdown_cache_disconnect_failure_is_swallowed` |
| H1 | `config/settings_help/text2sql.yaml` | `TEXT2SQL_TEMPLATE_ASSEMBLY` 항목 추가 · 머리 주석 「기본 on 예외」 셋→넷 | `tests/test_api/test_settings_help.py`(원래 실패 해소) |

### 3.2 고친 기존 단언(행동 변경에 따른 것만)

| 파일::테스트 | 옛 → 새 |
|---|---|
| `tests/test_api/test_plan163_s3_user_errors.py::test_apm_rank_failure_reason_hidden_in_first_hop_notice` | `(apm_api_error (요청 rid))` → `(apm_api_error · 요청 rid)` (M1) |
| `tests/test_api/test_plan166_w1_authz_sse.py::test_sub_rules_stay_per_kind` | 운영자 빈 `sub` → `""` 반환 ⇒ 401 (S2) |
| `tests/test_api/test_plan173_t1_lifespan_order.py` | `_config`에 `auth_enabled=False` 인자(실 `AuthConfig.enabled` 기본 False와 맞춤 — 종전 대역에는 `enabled` 필드가 없었다) · `test_mid_start_failures_keep_partial_state`는 `auth_enabled=True`로 종전 기대 유지(인증 경고 문구만 새 문구) · 새 `test_mid_start_failures_revert_auth_state`(비활성 — state 6종 None·풀 즉시 닫기·감사 루프 없음) (L1) |
| `tests/test_api/test_plan179_verify.py::test_entry_reassigns_authorized_selection` | 텍스트 진입 대입 대상 `body.selected_db_ids` ⇒ `_` (K7) |
| `tests/test_orchestration/test_plan134_w34_apm.py::test_first_hop_takes_every_instance_including_hostless_ones` · `::test_b2_first_hop_reads_the_whole_result_file` | 「(api_error (요청 -))」 ⇒ 「(api_error · 요청 -)」 (M1 · 계획 끝 회귀에서 발견 — 팀 리드가 2줄 수정) |
| `tests/test_nodes/test_plan166_w0_output_note_order.py` `DOC_PATH_NOTES`·`test_text_only_notes_absent_from_document_path` | 문서 경로 노트 11 ⇒ 15(4종 포함) · 「텍스트 전용 4종 부재」 ⇒ 「포함·텍스트 순서와 동일」(테스트 이름 유지) (K8) |

### 3.3 스냅샷 갱신(해당분만)

- `tests/snapshots/plan173_t4/read_condition.json` — `now_function` 케이스 `x.ts < CURRENT_TIMESTAMP`의 `reason` `column` → `expression` 1줄(J2)
- `tests/snapshots/plan173_t6/result_organizer.json` — `insufficient_diag`·`insufficient_no_diag`의 `organized_data`에 `"sheet_mappings": null`(K6)

### 3.4 매뉴얼(D-255)

- `scripts/manual/content/user.md` U-27(양식 채우기 결과 받기) `caution`에 1항목 — 양식 답에도 진행 중인 달·LIMIT 절단(U-20)·생성기 메모/조건 변경(U-19) 안내가 [기준월 안내] 앞에 붙는다. `features.yaml`·캡처 변화 없음. `python -m scripts.manual.build`(user.html·admin.html·help_index.json — 후 둘은 빌드 스탬프 줄만) · `pytest tests/test_manual` 619 passed.
- S1: `user.md` U-33 DRM 업로드 `caution`(「…복호화에 실패했습니다: (사유)」)에 「서버 DRM 설정 문제면 사유 대신 『서버 DRM 설정 오류 — 관리자에게 문의하세요 (요청 …)』 — 요청 번호를 관리자에게 알릴 것」 1문장 추가(팀 리드) → 재빌드 · `tests/test_manual` 619 passed.
- M1 첫 홉 고지는 매뉴얼이 `(…)` 자리표시라 갱신 불요.

## 4. 검증

### 4.1 계획 끝 회귀(1회 · D-303)

`python scripts/regress.py --base 13dc5a3f --files <39개>` — 본체 통과 7,231 · 실패 4 · 에러 0 · 건너뜀 33 · apm_gateway 통과 1,630 · 실패 0 · arch_check·overfit_check·config_fallback_check 통과 · ruff·mypy 이번 diff 줄 0 · `범위: 모듈 단위 — 전체 미실행` · `[전체 회귀 권고]` 블록 없음.

| 실패 | 귀속 | 처리 |
|---|---|---|
| `tests/test_api/test_plan160_chart_api.py::test_create_app_registers_viz_routes_only_when_chart_enabled[True]` | 원래 실패 | — |
| `tests/test_manual/test_help_index.py::test_index_matches_sources` | 이번 변경(회귀 도중 팀 리드가 `user.md` S1 문구를 고쳐 빌드 산출과 어긋남) | 재빌드 → `tests/test_manual` 619 passed |
| `tests/test_orchestration/test_plan134_w34_apm.py::test_first_hop_takes_every_instance_including_hostless_ones` · `::test_b2_first_hop_reads_the_whole_result_file` | 이번 변경(M1 — 옛 이중 괄호 단언, 줄바꿈으로 쪼개진 리터럴이라 W2a grep에서 빠짐 · `docs/18`) | 단언 2줄 새 꼴로 → 모듈 46 passed |

`tests/test_api/test_settings_help.py::test_t2_curated_groups_have_full_coverage`(원래 실패)는 H1로 해소됐다.

### 4.2 verifier · security-auditor (한 라운드)

- security-auditor: Critical 0 · High 0 · Medium 2 · Low 3. S1·S2·S3·K7·S6 충족.
  - Medium ① L1 인가 약화 → 교정(안 A — §3.1 L1 행) · 교정 뒤 `test_plan184_security_lifecycle`·`test_plan173_t1_lifespan_order`·`test_plan184_verify` 47 passed · 1 xfailed · `--no-tests` rc 0.
  - Medium ② S4 잔여 — 호출 단위 사유(`apm/calls.py` `call.error = str(e)` → `meta["failures"]`·`source_status.reason`·`LinkEntry.reason`)에 원문이 남는다 → 팀 리드 지시로 이번에 처리(§3.1 S4b).
  - Low: 감사 로그 `log_drm_decrypt(error=str(e))`에 원문(서버 내부 기록 — 허용) · 관리자 DRM 진단은 원문 노출(관리자 전용 — 의도) · `DrmDecryptError` 기본 `user_visible=True`(현 생성처 전수 확인 — 새 생성처는 주의).
- verifier: Critical 0 · Major 0 · Minor 5 · 결정 위배 없음. 새 `tests/test_api/test_plan184_verify.py` — 텍스트 진입 2종 인가 선택 하류 전달·전부 비인가 거부 · 문서 노트가 [기준월 안내] 앞에 한 번씩 · S4b. Minor ① S4b → 처리(§3.1) ② S3 `--reload` 오경보 → 처리 ③ L1 원복 범위 → 기록(§3.1) ④ §3.2 목록 누락 → 추가 ⑤ S1 이중 로그 → 처리.
- 교정 라운드(S4b·S3 reload·S1 로그): `regress.py --base 13dc5a3f --files <8개>`(apm/calls.py·apm/targets.py·upload.py·server.py·테스트 4) → 본체 통과 681 · 실패 1(원래 실패 `test_plan160_chart_api…[True]`) · 정적 게이트 통과 · ruff·mypy 이번 diff 줄 0 · `범위: 모듈 단위 — 전체 미실행`. `apm/calls.py`는 도구 선택분이 1파일뿐이라 apm 소비 모듈 테스트(`tests/test_orchestration/` apm 관련 + `test_plan134_w34_apm`·`test_plan163_s3_user_errors`)를 따로 돌려 1537 passed · 1 xfailed.
- 잔여(종전 동작 · 범위 밖): 인증 활성인데 풀 생성 **전** 실패하면 `get_current_user`가 payload-only로 통과(전 DB 허용)하고 경고 문구는 「(인증 기능 비활성)」이라 실제와 다르다 — fail-closed 여부는 사용자 결정 사항(`plans/182` 후보로 넘김).

---

## 변경 이력

- 2026-10-17 v1.2 — 교정 라운드: S4b 처리 · S3 `--reload` · S1 로그 1회 · L1 안 A 사용자 확정 기록 · §3.2 누락 행 추가.
- 2026-10-17 v1.1 — 완료. W1~W3 결과·고친 단언·계획 끝 회귀·감사/검증·L1 교정(안 A) 기록 · 파일명 WIP 태그 제거.
- 2026-10-17 v1.0 — 최초 작성. 182 §3 사용자 결정(2026-10-17) 반영 · 5 implementer 병렬.
