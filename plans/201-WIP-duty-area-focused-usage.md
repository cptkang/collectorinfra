# 201. 담당 업무별 중점 활용 — 담당 업무 지정 · 첫 화면 안내 · 알람 강조·카드 흐름 · 조사 결과 영역별 판정 · 조치 지원 · 담당 24시간 요약 · 사례 매뉴얼

> **작성일**: 2026-10-30 · **v1.2**(같은 날 — 전체 검토 반영: Q1 개정(본인 변경 없음) · 담당 업무 현황 카드 §3.7 · 외부 연계 → was_mw · DB 예시 축소 · 진행 순서 §10) · v1.1(같은 날 — §14 제안 P2·P3·P4·P5 본문 편입 · 발생/원인 영역 분리 · 원인 단서 §5.3 · W4 수동 조사 한정) · v1.0
> **상태**: **WIP** — **W2 완료(2026-10-30 · 커밋 없음 · 폐쇄망 검증 대기)**: 첫 화면 상황 안내 `WELCOME_GUIDE_ENABLED`(기본 off · 담당과 독립 · 로그인 사용자 · `<template>` 정적 문구 · 알람 줄은 수신 권한 없으면 비활성+사유 · 「원인을 찾고 있다면」은 입력창 포커스) · 담당별 예시 질문 `DUTY_WELCOME_HINTS_ENABLED`(유효 = `DUTY_AREA_ENABLED` AND · `/auth/me` `duty_welcome_queries` · 영역당 3·전체 6 · 사용법 끝 · 제목 한 줄) · 문구 정본 `config/duty_areas.yaml` `welcome_queries` · `verified_by` 규약(gold·assembler·sandbox·mlx·closed_net — 미래 날짜 거부) · 폐쇄망 전용은 `pending: closed_net` 로드 제외 · **로드 4/12**(서버/OS 2 · WAS 2 · DB 0 — 동형 골드 없음) · 실행 확인 0건(docker·MLX 없음 → 골드셋 동형만) · app.js v=50 · css v=47 · 매뉴얼 U-78(캡처 대기)·U-75 문구 · verifier Critical·Major 0 · Minor 4 처리 · 세부 D-354 부기 8 · **W4 완료(2026-10-30 · 커밋 없음 · 폐쇄망 검증 대기)**: sre_agent `SRE_DUTY_VIEW_ENABLED`(기본 off · 키 없음 = 비트 동일 · 단계 없는 잡 키 없음) · 브리핑 선택 키 `duty_view`(판독 계층·가설 상태를 영역으로 옮겨 쓰기 · 커넥션 가설 우선 · external → was_mw 「외부 연계」 꼬리표 · 근거 없는 normal 0 · unverified 사유 필수) · 매핑 `sre_agent/config/duty_area_layers.yaml` + 루트 계약 테스트(영역·라벨·DB 엔진 시그니처 6종 이름 사본) · noise_gate 사용자 뷰 `PLAYBOOK_VIEW_KEYS` 추가 · 동기 경로 인계 문구 1줄 · 「영역별 점검 결과」 표(textContent · 내 담당 맨 위 · 미지정도 보임) · briefing-view v=12 · app.js v=49 · 매뉴얼 U-77(캡처 대기) · verifier Critical 0 · Major 1(호스트 CPU 이상인데 R-E로 server_os 「정상」) · Minor 5 교정 · 재검증 xfail 6 → 통과 · Q5 검토 생략(피드백 교정 · 사용자 결정) · 세부 D-354 부기 7 · **W3 완료(2026-10-30 · 커밋 없음 · 폐쇄망 검증 대기)**: `DUTY_ALARM_FOCUS_ENABLED`(기본 off · 유효 = `DUTY_AREA_ENABLED` AND · 비트 동일) · `cause_hints`(제니퍼 유형 11 · 알려진 미매핑 8 · 프로세스 kind) · DB 엔진 시그니처 6종(실행 파일 경계 정규식) · SSE `apm_event_type`·`top_process_kinds`(분류 콜백 주입 — D-139 부기) · `/auth/me` `duty_alarm_focus` · 배지 발생/단서 · 「내 담당 먼저」(티어 순서 불변·숨김 0) · 추천 재정렬(현행 축 1개라 실효 없음 — 테스트 고정) · 현황 카드 제니퍼 유형 절 · app.js v=48 · admin.js v=16 · 매뉴얼 U-75·U-76·A-89(캡처 대기) · verifier Critical 0 · Major 1 교정(스냅샷 args 120자 절단으로 Java WAS kind 미탐 → enricher가 마스킹 전 원 항목을 주입 판정기로 분류해 상태 키 `top_process_kinds`만 싣는다 · 원문 미보관 · 재검증 Critical·Major·Minor 0) · W3b 제외 · 세부 D-354 부기 6 · **W1 완료(2026-10-30 · 커밋 없음 · 폐쇄망 검증 대기)**: 담당 업무 필드·가입 선택/관리자 지정·일괄 지정·「담당 업무 현황」 카드(지정률·매핑 점검 `resource_type`)·미지정 안내·플래그 `DUTY_AREA_ENABLED`(기본 off · 비트 동일) · verifier Critical 0(Major 1 교정 — 결정 기록 스트리밍 집계 `to_thread` · `mapping=false`) · 매뉴얼 U-75·A-89(캡처 대기 — Redis·PG 환경 필요) · 세부 결정 D-354 부기 5 · 잔여 W2~W7(진행 순서 §10: W3 → W4 → Q5 검토 → W2·W5~W7) · 이전 상태: 방향 확정(D-354) · Q1~Q13 확정 · W0 완료 · 전체 검토 v1.2
> **분석 기준 HEAD**: `60b4992b`
> **계획 시작 SHA**: `a289818f` (W1 착수 2026-10-30 — 회귀 기준)
> **W1 회귀**: `regress.py --base a289818f --files <W1 파일>` 종료 0 · `범위: 모듈 단위 — 전체 미실행` · 새 실패 0(기준선 worktree 대조) · 교정분(`decision_store.py`·담당 라우트·`user_auth`·`admin.js`·테스트 2) 재회귀: `범위: 모듈 단위 — 전체 미실행` · 본체 통과 2,803 · 실패 48(전부 「사본에서 수집 안 됨」 → 기준선 worktree 대조 결과 동일 ID · 새 실패 0) · 기준선 결함 `test_plan199_w6_breakglass` 시각 의존 parametrize로 xdist 수집 불일치(docs/18 2026-10-30 — 해당 노드 deselect) · 대조 스크립트 실수로 본체 기본 수집 전체가 두 트리에서 실행됨(현재 221F/68E · 기준선 221F/68E · 실패 ID 집합 동일 — 공식 `--full` 아님 · docs/18) · `[전체 회귀 권고]`(허브 `src.config` 87% · `get_me` 시그니처) — 사용자 요청 시 `--full` · ruff·mypy 미실행(Windows 환경에 도구 없음)
> **W3 회귀**: `regress.py --base a289818f --files <W3 파일 37>` · `범위: 모듈 단위 — 전체 미실행` · 본체 통과 11,084 · 실패 310 · 에러 153(도구 귀속은 전부 「사본에서 수집 안 됨」) → 직렬 재실행 321 통과(xdist 부하 node 기동 실패 0xC0000142·WinError 206) · 남은 142건은 기준선 worktree(`a289818f`)에서 142건 전부 동일 실패 → **새 실패 0** · 교정분(`services.py`·notifier·시그니처·admin.js·테스트 2) 재회귀 통과 1,351 · 실패 4(위 142에 포함 · 기준선 동일) · `[전체 회귀 권고]`(허브 `src.config.services` 87% · `get_me` 시그니처 — W1분) · ruff·mypy 미설치(pyflakes 대체) · 교정 2(원 항목 kind 판정 — `process_rank.py`·`alarm_context_enricher.py`·`alarm_graph.py`·notifier·테스트 3) 재회귀 `범위: 모듈 단위 — 전체 미실행` 통과 1,434 · 실패 2(`test_alarm_process_enrich` `TestProcessApiClient` 2 — 기준선 동일) · 권고 블록 없음
> **W4 회귀**: `regress.py --base a289818f --files <W4 본체·프론트·테스트·매뉴얼 21>`(기준선 결함 `test_plan199_w6_breakglass::test_user_path_health_is_not_registered` deselect) · `범위: 모듈 단위 — 전체 미실행` · 본체 통과 2,565 · 실패 78(도구 귀속 전부 「사본에서 수집 안 됨」) → 직렬 재실행 3 통과 · 남은 75건(전부 node `WinError 206` 명령줄 한도)은 기준선 worktree(`a289818f`)에서 75건 전부 동일 실패 → **새 실패 0** · 권고 블록 없음 · ruff·mypy 미설치(sre_agent는 pyflakes 0) · sre_agent(시스템 python · `.venv` 없음) W4·브리핑·디스패치·경계 170 통과 · 첫 회귀는 implementer 교정과 겹쳐 재실행(위가 최종)
> **W2 회귀**: `regress.py --base 34371f25 --files <W2 30>`(breakglass 노드 deselect) · `범위: 모듈 단위 — 전체 미실행` · 본체 통과 9,663 · 실패 125 · 에러 26(도구 귀속 151건 전부 「사본에서 수집 안 됨」) → 직렬 재실행(수집 불가 30 제외 121) → 기준선 worktree(`34371f25`) 대조 → 새 실패 3(`test_ui_plan192_w4_conversation_restore::test_check_auth_on_load_restores_only_without_query_entry` — node `-e` 명령줄 WinError 206 · 헬퍼 stdin 전달로 교정) · 교정분 재회귀 `범위: 모듈 단위 — 전체 미실행` 통과 1,431 · 실패 19 → 직렬 17 전부 기준선 동일 → **새 실패 0** · `[전체 회귀 권고]`(허브 `src.config` 87% · `src.config.services` 87%) — 사용자 요청 시 `--full` · ruff·mypy 미설치(pyflakes 대체)
> **회귀 대상 파일**: W1 — `config/duty_areas.yaml` · `config/settings_help/duty_area.yaml` · `src/domain/duty_areas.py` · `src/infrastructure/duty_area_vocabulary.py` · `src/api/duty_area_support.py` · `src/api/routes/duty_areas.py` · `src/config/services.py` · `src/config/__init__.py` · `src/api/settings_catalog.py` · `.env.example` · `src/domain/user.py` · `src/infrastructure/user_repository.py` · `src/api/server.py` · `ddl/auth_tables.sql` · `db/init/03_auth_tables.sql` · `src/api/schemas.py` · `src/api/routes/user_auth.py` · `src/api/routes/admin.py` · `noise_gate/infrastructure/decision_store.py` · `src/static/register.html` · `src/static/js/app.js` · `src/static/js/admin.js` · `src/static/admin/dashboard.html` · `src/static/index.html` · `tests/test_plan201_w1_duty_areas.py` · `tests/test_plan201_w1_verify.py` · `tests/test_api/test_settings_catalog.py` · 캐시 버전 단언 5개 · 매뉴얼(`scripts/manual/{features.yaml,content/user.md,content/admin.md}` · `src/static/manual/*`) · **W3** — `config/duty_areas.yaml` · `config/middleware_signatures.yaml` · `config/settings_help/duty_area.yaml` · `.env.example` · `src/config/services.py` · `src/domain/duty_areas.py` · `src/domain/middleware.py` · `src/api/duty_area_support.py` · `src/api/schemas.py` · `src/api/routes/user_auth.py` · `src/api/routes/duty_areas.py` · `src/api/server.py` · `noise_gate/application/nodes/alarm_notifier.py` · `noise_gate/application/alarm_worker.py` · `noise_gate/infrastructure/decision_store.py` · `noise_gate/domain/process_rank.py` · `noise_gate/application/nodes/alarm_context_enricher.py` · `noise_gate/orchestration/alarm_graph.py` · `src/static/js/app.js` · `src/static/index.html` · `src/static/js/admin.js` · `src/static/admin/dashboard.html` · 매뉴얼(`scripts/manual/*` · `src/static/manual/*`) · 테스트(`tests/test_plan201_w3_{duty_focus,frontend,middleware_db,verify}.py` · `noise_gate/tests/test_plan201_w3_notifier.py` · `noise_gate/tests/test_plan201_w3_enricher_kinds.py` · `tests/test_plan201_w1_{duty_areas,verify}.py` · `tests/test_api/test_settings_catalog.py` · 캐시 버전 단언 5) · **W4** — `sre_agent/config/duty_area_layers.yaml` · `sre_agent/sre_agent/domain/duty_view.py` · `sre_agent/sre_agent/infrastructure/duty_area_loader.py` · `sre_agent/sre_agent/settings.py` · `sre_agent/sre_agent/application/briefing_builder.py` · `sre_agent/sre_agent/application/investigation_dispatcher.py` · `sre_agent/.env.example` · `noise_gate/domain/investigation_briefing.py` · `noise_gate/infrastructure/investigation_store.py` · `src/static/js/briefing-view.js` · `src/static/js/app.js` · `src/static/index.html` · `src/static/noise.html` · `src/static/admin/noise.html` · 매뉴얼(`scripts/manual/{features.yaml,content/user.md}` · `src/static/manual/*`) · 테스트(`tests/test_plan201_w4_{duty_contract,duty_render,verify}.py` · `sre_agent/tests/test_plan201_w4_{duty_view,verify}.py` · 캐시 버전 단언 6) · **W2** — `src/config/services.py` · `src/config/__init__.py` · `src/api/settings_catalog.py` · `.env.example` · `config/settings_help/{duty_area,welcome_guide}.yaml` · `config/duty_areas.yaml` · `src/domain/duty_areas.py` · `src/api/duty_area_support.py` · `src/api/server.py` · `src/api/schemas.py` · `src/api/routes/user_auth.py` · `src/static/index.html` · `src/static/js/app.js` · `src/static/css/style.css` · 매뉴얼(`scripts/manual/{features.yaml,content/user.md}` · `src/static/manual/*`) · 테스트(`tests/test_plan201_w2_{welcome,frontend,verify}.py` · `tests/test_api/test_settings_catalog.py` · `tests/test_api/test_ui_plan192_w1_entry.py` · `tests/test_plan201_w4_verify.py` · 캐시 버전 단언 7)
> **사용자 제기(2026-10-30)**: *"각 영역별 담당자들이 이 시스템을 어떻게 사용하면 되지? 어떻게 해야 장애 인지, 분석, 조치를 잘 할 수 있도록 만들지?"* — 현장 피드백: *"접속했을 때 이걸로 장애 판단·조치를 어떻게 해야 할지, 어떻게 활용해야 할지 모르겠다."*
> **관련 결정**: **D-354**(본 계획 — 담당 업무는 권한이 아니라 중점) · D-082(존 RBAC — 역할 리스트 일반화 기각 · 별도 필드 전례) · D-178(표시 선호는 브라우저 · 권한은 서버) · D-069(역할=user/admin) · D-192(알람 → 질의 인계 — 검증된 형태만 · 확인 뒤 전송) · D-341 ⑪(추천 칩은 골드셋 동형만) · D-011(조치는 권고만) · D-035(출처 표기) · D-325(브리핑 공용 렌더) · D-350(RCA 개선 · 옵트인) · D-255(매뉴얼 동반) · D-162(기본 off) · D-347(조사 수동 전용·부하 상한) · D-263(알람 조회 경로 존 판정) · D-049(incident ack) · D-260(TICKET 일배치 요약 — worKB)
> **선행·병행 계획**: `plans/195`(조사 화면 「질의응답에서 확인할 질의」 — W5가 그 산출 위에 얹는다) · `plans/198`(RCA 개선 — W4가 같은 브리핑에 선택 키를 더한다) · `plans/194`·D-347(조사 수동 전용 — W4 적용 범위의 전제)
> **결정 번호**: D-354(2026-10-30 등재 · 원 D-353/`plans/200` — origin/main `36ce5f1` 병합 시 재부여: 원격 「장애 조사 실행 병렬화」가 D-353·`plans/200` 선점). Wave 구현 중 세부 결정은 D-354 부기로 적는다.

---

## 0. 요약

- **문제**: 이 시스템에는 담당자가 "내 영역 일인가 → 원인이 내 쪽인가 → 무엇을 하고 누구에게 넘기나"를 따라갈 길잡이가 없다. 첫 화면 예시 질문은 누구에게나 같은 5개(그중 2개는 비활성)이고, 알람·조사 결과도 담당과 무관하게 같은 순서로 나온다. 현장 피드백 "어떻게 활용해야 할지 모르겠다"는 이 공백을 가리킨다.
- **방향(D-354)**: 사용자가 **담당 업무**(서버/OS · WAS/미들웨어 · DB — 우선 3개)를 고른다. 이 값은 **권한이 아니고 무엇도 숨기지 않는다.** "이 영역을 중점적으로 보게 해 장애 인지·처리를 돕는다"는 의미로만 쓴다. 지정은 선택사항이며 **미지정 사용자는 현행과 비트 단위로 같다**(이점도 없다).
- **단계**:
  | W | 내용 | 미지정 사용자 | 위험 |
  |---|---|---|---|
  | W1 | 담당 업무 필드 · 가입 선택/관리자 지정(본인 변경 없음 — Q1 개정) · 어휘 정본 `config/duty_areas.yaml` · 관리자 「담당 업무 현황」 카드(지정률 · 매핑 점검) | 영향 없음 | 낮음 |
  | W2 | 첫 화면 정리 — 상황 안내 3줄(P2 · 모든 사용자) + 예시 질의를 담당별 고정 문구로 | 상황 안내만 · 예시는 현행 | 낮음 |
  | W3 | 알람 카드 「내 담당」 표시(발생 영역 + 조사 없이 만드는 원인 단서 §5.3) · 「내 담당 먼저」 정렬 · 추천 질의 담당 축 우선 | 현행 그대로 | 낮음 |
  | W4 | 조사 브리핑에 **영역별 점검 결과**(OS/WAS/DB 이상·정상·미확인) + **인계 판단** · 담당자는 자기 영역 행이 맨 위 · **수동 조사가 끝난 알람만**(D-347) | **판정표는 보인다**(§6.4 Q4) | 중간 |
  | W5 | 조치 지원 — 담당별 다음 확인 질의 · 영역별 점검 순서 · 인계 요약 복사 + **알람 카드 인지→분석→조치 재배치**(P3 · 모든 사용자) | 재배치·공통 순서 | 낮음~중간 |
  | W6 | 첫 화면 「지난 24시간 · 내 담당」 요약 카드(P5) | 표시 안 함 | 낮음~중간 |
  | W7 | 사용자 매뉴얼 「사례로 배우기」 — 영역별 1건 · 로컬 샌드박스 재현(P4) | 같음 | 없음(문서) |
- **원칙**: 문구·판정은 **결정적 조립**(config 템플릿 · 코드)이고 새 LLM 호출을 만들지 않는다. 추천 질의는 **답할 수 있다고 확인된 형태만** 낸다(D-192 · D-341 ⑪). 모든 Wave는 플래그 기본 off.

---

## 1. 현행 실측 (2026-10-30 · HEAD `60b4992b`)

| 항목 | 현행 | 위치 |
|---|---|---|
| 사용자 속성 | `role`(user/admin) · `department`(자유 텍스트) · `allowed_db_ids` · `allowed_sources` · `alarm_zones` · `is_protected` — 담당 개념 없음 | `src/domain/user.py:30-71` |
| `department` 쓰임 | state `user_department`로 실리지만 프롬프트·판단에서 읽는 곳 0건 | `src/api/routes/query.py:383` · `src/state.py:349` |
| 컬럼 보강 | 기동 시 `ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS …` 멱등 패턴 | `src/api/server.py:63-78` |
| 첫 화면 예시 | HTML 하드코딩 버튼 5개(활성 2 · 비활성 2 · 사용법 1) · `ㅇㅇ존` 자리표시는 `_substitute_zone_placeholder`가 치환 | `src/static/index.html:194-205` · `src/api/routes/query.py:379` |
| 알람 추천 질의 | `ALARM_PROMPT_AXES` — `server.*` 축만 · **`apm.Instance`(WAS) 축 없음** · `server.Network`는 답변 가능성 미실측으로 비움 | `src/static/js/app.js:4085-4112` |
| 알람 유형 판정 | `classify_alarm_kind` → apm·cpu·memory·disk·network·process·log | `noise_gate/domain/process_rank.py:63` |
| 조사 플레이북 | `host.{cpu,disk_io,fs,memory,network,process}` · `was.{active,error,heap,latency,pool,sql}` | `sre_agent/config/playbooks/` |
| DB 관련 근거 | DB 내부 지표 소스 **없음**. 제니퍼 SQL(`apm.active_sql`·`apm.slow_tx_breakdown` SQL 구간)과 DB 서버 OS 지표만 | `sre_agent/config/playbooks/was.sql.yaml` |
| 미들웨어 식별 | WAS·웹서버·캐시/메시징·TP — **DB 엔진 시그니처 없음** | `config/middleware_signatures.yaml` |
| 조사 브리핑 | 6요소(요약·타임라인·병목·원인·권고·한계) 결정적 조립 · 선택 키 전례 `cross_source` · 권고는 human-gated 문구 강제 | `sre_agent/sre_agent/application/briefing_builder.py:36-43,259,452` |
| 브리핑 렌더 | 알람 카드·노이즈 관제 드로어가 `renderBody` 하나로 그림 | `src/static/js/briefing-view.js` |
| 표시 선호 저장 | 알람 표시 레벨 = localStorage(D-178) | `src/static/js/app.js` |

### 1.1 W0 실측 (2026-10-30 · 로컬 코드 기준 — 폐쇄망 실값은 ①만 남음)

| # | 질문 | 결과 | 계획 영향 |
|---|---|---|---|
| ① | DB 서버·DB 프로세스 알람의 `resource_type` | 레포(샌드박스 `testdata/pg/init` 19종 · config · 코드)에 `db.*`·`database` 계열 **없음** — `server.*`(HBA 포함)·`apm.Instance`·`network.*`(프롬프트·목업)뿐. 폐쇄망 실값 목록 기록 없음 | `db` 영역 `resource_types: []` 유지. DB 영역의 발생 판정은 불가 → DB 담당 강조는 **원인 단서(§5.3)·조사(W4)로만**. 폐쇄망 세 존 실값은 **전달 요청 1건**으로 남김 |
| ② | SSE payload에 `resource_type`·`alarm_kind` | `resource_type` **있음**(`alarm_notifier.py:948-1000` `_tier_sse_payload` · PAGE는 `:1104-1161`). `alarm_kind`는 최상위 키 **없음**(분석 테스트 푸시의 `process_snapshot.alarm_kind`에만) | §5.1 `dutyAreasOf`는 `resource_type`만 키로 쓴다(`alarm_kind` 단계 삭제) |
| ③ | `SQL_EVENT_TYPES`가 SSE까지 오나 | **안 온다.** 이 집합은 sre_agent에만 있고(`playbook_runner.py:133-139`) 게이트웨이 `EVENT_TYPE_SIGNALS`에도 없다 | SQL 계열 db 단서는 게이트웨이 kind 추가가 필요 → §5.3 ①에서 **2차로 미룸** |
| ④ | 제니퍼가 인스턴스의 DB 대상(JDBC URL·호스트)을 주나 | **구조화 필드 없음.** 실행 중 서비스의 `datasource`(JNDI 이름, 예 `jdbc/orderDS` — `fields.py:327-329`)뿐. `environment-variable`에 `spring.datasource.url`이 섞일 수는 있으나 해석 코드 없음(마스킹 대상) | W4 db 영역 근거는 **「앱이 본 SQL」까지로 확정**. DB 서버 OS 지표 연결은 범위 밖(§13) |
| ⑤ | SSE payload에 제니퍼 이벤트 유형·kind | 원 유형명이 `alarm_name`으로만 온다. 정규화 유형(`apm.event_type_norm`)·kind(`apm.was_signals[].kind`)는 alarm:raw에 있으나 **SSE 조립이 `raw_payload`를 싣지 않아 빠진다** | §5.3 ①을 쓰려면 notifier가 `raw_payload.apm.event_type_norm`을 SSE 키 1개(`apm_event_type`)로 옮겨야 한다 — noise_gate 소폭 변경(W3 · 플래그 뒤 · 당초 kind 목록 `apm_signal_kinds`였으나 §12 Q8로 유형으로 바꿈) |
| ⑤' | `process_snapshot`이 실 카드에 오나 | **실 경로 SSE(티어·PAGE)에는 없다** — 분석 테스트 푸시(`alarm_test_payload.py`)와 worKB·웹훅 본문에만. notifier state에는 있다(`alarm_notifier.py:405`) | §5.3 ②도 SSE에 상위 프로세스 요약 키가 필요 — ⑤와 같은 W3 소폭 변경으로 묶는다 |
| ⑥ | 결정 기록의 담당·존 키 · 24시간 집계 | 레코드에 `resource_type`(수동 조사 플래그 on일 때 — 기본 on)·`db_id`·`alarm_name` 있음, 존·솔루션은 조회 시 `db_id`로 유도. kind는 `apm_noise_policy_enabled`(기본 off)일 때 `stage_evidence.apm_policy.event_type_norm`만. 24시간 집계 `GET /noise/summary`(기본 `24h` · 티어별 건수) **있음 · `require_user` · 존 필터 없음** | W6는 이 집계를 재사용하되 **사용자 알람 존 필터를 새로 넣는다**. ~~결정 레코드에는 kind가 없으므로 W6 「단서」 표시는 `resource_type`·`alarm_name` 기준으로 한정~~ → **개정(v1.2)**: 폐쇄망은 `noise_apm_noise_policy_enabled=true`라 제니퍼 판정 100%에 `stage_evidence.apm_policy.event_type_norm`이 있다(§1.2 ⑤) — W6도 W3와 같은 유형 단서(`cause_hints`)를 쓴다(DB 담당 카드가 비지 않게). 정책 off 환경에서는 유형 단서 없이 `resource_type` 기준만 |
| ⑦ | 프로파일 `acquire_ms`·`execute_ms` | **게이트웨이가 만들지 않는다**(`transaction_tools.py:504-514` 반환 행에 없음 · 원천 필드명 근거 없음 · 프로파일 텍스트는 형식 미검증). R-D는 **항상 n/a** | Q7 영향: H-POOL-INSTANCE는 `db_split == acquire`가 조건이라 **현재 지지될 수 없다** → 커넥션 계열은 H-DB-POOL-CHAIN(SQL 구간 지배 — `sql_ms`로 판독 가능)이 지지될 때만 db, 그 밖은 **둘 다 의심**이 기본이 된다 |
| 렌더 | 채팅 장애 진단 응답 렌더 | `sre_chat_investigation_enabled` off(기본): 브리핑을 `render_briefing_lines` 텍스트로 바꿔 말풍선(`fault_diagnosis.py:804-870`). on: 접수 카드 안에서 `BriefingView.renderBody` 요약형(`app.js:5497-5559`) | §6.2: on 경로는 `renderBody` 수정으로 자동 반영. off 경로는 `render_briefing_lines`에 「영역별 점검」 1줄(인계 문구)만 추가 |
| 195 | plans/195 진행 | 실측 당시 이 브랜치에는 코드 0이었으나 **origin/main 병합(`36ce5f1` · 2026-10-30)으로 들어옴 — WIP · W1~W5 구현 · 플래그 `NOISE_INVESTIGATION_REQUERY_ENABLED` 기본 off · 질의는 제니퍼 축만**. 결정적 템플릿 `src/domain/investigation_requery.py` `build_requery` · 조사 조회 API `requery` 가산(`src/api/routes/alarm.py` `_attach_requery`) | W5 §7.1은 이 `requery` 산출을 담당 축으로 **재정렬만** 하면 된다(새 질의 생성 없음). 폴스타 축 질의가 없으므로 server_os·db 담당은 당분간 §7.2 점검 순서가 주 콘텐츠 — W5 착수 때 195 산출 형태를 다시 실측 |
| 시그니처 | DB 엔진 미들웨어 시그니처 | `middleware_signatures.yaml` 13종에 DB 엔진 **0건** | §13 「DB 엔진 시그니처」를 W4 선행으로 당긴 판단 유지 |

### 1.2 W0 폐쇄망 실측 (2026-10-30 · 사용자 전달 — b0 · 본체 결정 기록 20,000줄 · 운영 `.env`)

| # | 결과 | 계획 영향 |
|---|---|---|
| ① b0 `resource_type`(30일 알람 · 코드값) | server.Cpus 273,293 · LogMonitor 47,604 · FileSystem 36,942 · Disks 13,686 · Memory 9,237 · NetworkInterface 8,763 · ProcessMonitor 5,009 · FileSystems 4,501 · Server 1,773 · Process 1,275 · VirtualMemory 896 · **icmp.ICMPNode 75** — DB 전용 유형 **없음 확정(b0)**. **cm_gp(2차 · `cm_query.py`)**: server.Cpus 9,341 · server.FileSystem 684 · server.Server 197 · server.Memory 98 · server.WebURLMonitor 18. **cm_yd**: server.Memory 50,167 · server.Cpus 33,108 · server.Process 10,211 · server.LogMonitor 4,815 · server.FileSystem 3,805 · server.Disks 3,083 · server.Server 1,070 · server.WebURLMonitor 6 — **세 존 모두 `server.*`(+b0 icmp)뿐** | 알람 기준 DB 전용 유형 **없음 확정(세 존)** — db는 코드값 매핑만 두고 실제 단서는 프로세스·제니퍼에서 온다(§3.1). `icmp.ICMPNode`는 네트워크 성격 — 3영역 밖(미매핑 = 강조 없음). 반출값은 화면을 손으로 옮겨 적은 것이라 철자 오차가 있을 수 있다 → 대조는 **대소문자 무시**(사용자 확인 2026-10-30) |
| ①″ 표시 이름 대응(`cmm_resource_type` · 세 존 29행 동일) | CPU ← network.cpus·sap.Cpus·server.Cpus · 메모리 ← {db2,mariadb,mssql,mysql,server,tibero}.Memory·network.memorys·sap.OSMemory · 프로세스 ← {mariadb,mssql,mysql,oracle,postgresql,server,sybase,tibero}.Process·network.process · 개별 파일시스템 ← network.filesystem·sap.Filesystem·server.FileSystem · 디스크 ← sap·server.Disks · 로그 모니터 ← server.LogMonitor · WebURL 감시 ← weburl.WebURLMonitor · 서버 ← server.Server | **한 표시 이름이 여러 코드를 묶는다** — 메모리·프로세스는 DB 엔진형 유형과 공유한다. 표시 이름은 server_os로만 매핑하고 DB 엔진형은 코드값으로만 db에 둔다(§3.1). 존별 차이 없음 → YAML 한 벌 |
| ①′ **알람 payload의 `resource_type`은 코드값이 아니라 한글 표시 이름** | 결정 기록 `resource_type`(= 알람 이벤트 `${resourceType}` 그대로 · `notification_gate.py` `_manual_source_fields`): apm.Instance 2,891 · **CPU 513 · 메모리 413 · 프로세스 231 · 디스크 45 · 개별 파일시스템 38 · 서버 31 · 로그 모니터 14 · WebURL 감시 1** — `server.*` 코드값 0건 | **§3.1·§5.1을 바꾼다** — 영역 매핑 키는 표시 이름이어야 한다. 이름↔코드 대응은 폴스타 `cmm_resource_type`(`name`=코드 · `displayname`=표시 이름)에서 재실측해 `config/duty_areas.yaml`에 **두 형태를 함께** 적는다(§3.1). 「CPU」가 `server.Cpu`·`server.Cpus` 둘 다일 수 있어 코드 → 이름 역변환은 쓰지 않는다 |
| ② DB 엔진 프로세스(등록 감시 자원) | b0: 「DB2 엔진」 25 · db2sysc 3 · db2syscr 2 · mysqld 1 · 적재 프로세스(probe-db-*) 등 — **사용자가 직접 등록하는 값이라 DB 서버 수에 크게 못 미침** | 등록 감시 자원으로 DB 서버를 찾지 않는다. DB 엔진 시그니처(§13 → W4 선행)는 **실시간 프로세스 목록**(영향 프로세스 스냅샷 · R-E `cpu_owner` 입력)에 적용한다. 초기 시그니처는 **제품 표준 엔진 프로세스 이름**으로 정한다: `db2sysc`(DB2 — b0 등록 자원에서도 확인) · `mysqld`·`mariadbd` · `postgres` · `ora_pmon_*`·`oracle` · `tbsvr`(Tibero). 어느 서버가 DB인지 사용자가 알 수 없어(2026-10-30) 폐쇄망 사전 실측은 **하지 않는다** — 오탐보다 미탐 쪽 위험이라(미탐 = 단서 없음, 현행과 같음) 운영 중 알람 카드의 영향 프로세스 표에서 관찰해 보강한다 |
| ③ alarm:raw 제니퍼 이벤트 | 최근 2,000건 중 제니퍼 **0건**. 2차: 결정 기록(`logs/` — `.env`의 `log/`는 실제 경로와 다름) `event_type_norm` 분포 — BAD_RESPONSE_TIME_APPLICATION 716 · SERVICE_EXCEPTION 584 · **BAD_RESPONSE_TIME_SQL 426** · BAD_RESPONSE_TIME_EXTERNAL_CALL 357 · AGENT_STOP 254 · HTTP_IO_EXCEPTION 221 · **SQL_TOOMANY_FETCH 150** · `METRICS EVENT` 37(공백 포함 원값) · SOCKET_EXCEPTION 18 · **DB_CONNECTION_FAIL 4** (계 2,767) | 제니퍼 이벤트는 들어오고 있다 — alarm:raw 0건은 보존 길이 안에 다른 소스가 몰린 것으로 보고 **재실측하지 않는다**. **SQL 계열이 576건(21%)으로 db 단서의 사실상 유일한 원천**이고 DB 연결 실패는 4건뿐이다 → **SQL 단서를 1차에 넣는다**(§12 Q8 · §5.3 — 게이트웨이 kind를 늘리지 않고 notifier가 `event_type_norm`을 SSE 키로 옮겨 `config/duty_areas.yaml`의 유형 목록으로 대조 · sre_agent 사본 동기화 불필요). 응답 지연·서비스 예외(1,300건)는 유형만으로 원인을 못 가려 단서 없음(§5.3 ① 그대로). 유형 대조는 원값 공백·대소문자를 정규화한다 |
| ④ 프로파일 커넥션 획득 줄 | **없음** | Q7 권고 유지 — H-POOL-INSTANCE(→ was_mw)는 게이트웨이 구조화 전까지 지지 불가. 획득 시간 구조화 후속은 만들지 않는다 |
| ⑤ 결정 기록 | 20,000줄 중 판정 레코드 4,177(나머지 15,823은 `type` = resolution·recurrence·investigation·late_promotion·l3_state 보조 레코드 — tier 없음이 정상). 판정 레코드의 `resource_type`·`db_id`·`alarm_name` **100%** · hostname 99% · 제니퍼 판정은 `apm_policy` 100%(2,891). tier: suppress 2,604 · dashboard 963 · page 432 · ticket 178 | W6은 판정 레코드(`type` 없음)만 센다. 담당 판정은 표시 이름 기준(①′). 제니퍼는 `event_type_norm`으로 원인 단서까지 표시 가능 |
| ⑥ 운영 `.env` | `noise_fault_diagnosis_enabled=true` · `SRE_CHAT_INVESTIGATION_ENABLED` 미설정(= off) · `noise_apm_noise_policy_enabled=true` · `noise_app_impact_enabled=true` · `noise_investigation_episode_context_enabled=true` · **`noise_cross_source_mode=shadow`** · `sre_playbook_enabled=true` · `noise_decision_store_path=log/alarm_decisions.jsonl` · **오타 `noise_investigation_manal_enabled`**(manual — 무효지만 코드 기본 true라 영향 없음) | 채팅 진단은 **동기 텍스트 경로** → W4 인계 문구는 `render_briefing_lines` 1줄이 실제 노출 경로. **shadow에서는 `episode_lines`를 만들지 않는다**(annotate·enforce만 — `alarm_notifier.py` `episode_lines`) → §5.3 단서 ③은 폐쇄망에서 **현재 비활성** |

> **곁가지 발견 2 (범위 밖 · 기존 결함 후보 · 별도 판단 필요)**: ①′ 때문에 알람 카드 결정적 추천 질의 `ALARM_PROMPT_AXES`(`app.js:4085` — `server.Cpus` 등 코드값 키)는 **폐쇄망 폴스타 알람에서 한 번도 맞지 않는다**(전부 「미매핑 — LLM 보강」 경로). `classify_alarm_kind`의 디스크 키워드에도 「파일시스템」이 없어 「개별 파일시스템」 알람은 분류 None이다. W3의 「추천 질의 담당 축 우선 재정렬」은 이 결함이 고쳐져야 의미가 있다. → **별도 계획 `plans/204`로 W3 착수 전에 고친다**(§12 Q11 · 결함 수정이라 플래그 없음).

> **곁가지 발견 (범위 밖 · 별도 판단 필요)**: `/noise/*` 사용자 경로(`noise_dashboard.py:1288-1296` `require_user`)와 `/alarm/metrics`는 **존 필터가 없어 비관리자에게 전 존의 집계·결정 목록이 보인다.** 알람 SSE는 `event_visible_to`로 존을 거르는 것(D-263)과 어긋난다. 이 계획에서는 W6 카드만 존 필터를 넣고, 기존 경로는 **당분간 전 존 공개를 유지**한다(2026-10-30 사용자 결정 · `plans/203` 보류 · D-263 부기 2). W6 카드는 링크 대상인 알람 탭(존 판정 적용)과 맞추려고 존 필터를 그대로 둔다.

---

## 2. 원칙

1. **권한이 아니다.** 담당 업무는 인가 판정(`allowed_db_ids`·`allowed_sources`·`alarm_zones`·`host_authz`)에 쓰지 않는다. 데이터·알람·조사를 **숨기지 않고 순서와 강조만** 바꾼다(재현율 우선 D-048과 정합).
2. **선택사항이다.** 미지정(`None`/빈 목록) = 현행 비트 동일. 플래그 off도 현행 비트 동일.
3. **여러 개를 고를 수 있다.** 작은 팀이 OS와 WAS를 함께 맡는 경우를 자연스럽게 표현한다(`alarm_zones`와 같은 리스트 · D-082 전례).
4. **어휘 정본은 하나**다 — `config/duty_areas.yaml`. 코드에 영역 문자열을 박지 않는다(overfit_check 정합). 별도 프로세스(`sre_agent`)가 쓰는 영역 코드는 계약으로 보고 대조 테스트로 묶는다(§6.3).
5. **결정적 조립.** 예시 문구·추천 질의·판정표·인계 문구는 config 템플릿과 코드로 만든다. 새 LLM 호출을 만들지 않는다.
6. **답할 수 있는 것만 권한다**(D-192 · D-341 ⑪). 예시·추천 질의는 골드셋 동형이거나 결정적 조립기 진입이 확인된 형태만 싣는다. 확인 못 한 영역은 비워 두고 사유를 주석으로 남긴다(`server.Network` 전례).
7. **정상 단정 금지.** 근거가 없는 영역은 「정상」이 아니라 「미확인(사유)」로 표시한다(침묵 폴백 금지).
8. **조치는 권고만**(D-011). 실행 경로를 만들지 않는다.

---

## 3. W1 — 담당 업무 지정

### 3.1 어휘 정본 `config/duty_areas.yaml` (신규)

```yaml
# 담당 업무 어휘 정본(D-354). 권한이 아니라 중점 — 무엇도 숨기지 않는다.
# 코드는 이 파일의 code만 안다. 영역 추가는 이 파일 + 매뉴얼 + 계약 테스트(§6.3)로 한다.
areas:
  - code: server_os
    label: 서버/OS
    description: CPU·메모리·파일시스템·프로세스 등 서버 자원
    # 코드값 + 표시 이름(W0 §1.2 ①″ — 세 존 동일). 대조는 대소문자 무시
    resource_types: [server.Server, 서버, server.Cpus, CPU, server.Memory, 메모리, server.FileSystem, server.FileSystems, 개별 파일시스템,
                     server.Disks, 디스크, server.Process, 프로세스, server.LogMonitor, 로그 모니터]
    alarm_kinds: [cpu, memory, disk, process, log]
    playbook_prefixes: [host.]
  - code: was_mw
    label: WAS/미들웨어
    description: WAS 인스턴스 응답·에러·힙·풀, 미들웨어 프로세스
    resource_types: [apm.Instance, weburl.WebURLMonitor, server.WebURLMonitor, WebURL 감시]   # WebURL 감시 = 서비스 접점(§12 Q9)
    alarm_kinds: [apm]
    playbook_prefixes: [was.]
  - code: db
    label: DB
    description: DB 서버 자원과 애플리케이션이 본 SQL 지연(DB 내부 지표는 미수집)
    # 엔진형 유형은 정의만 있고 30일 알람 0건(세 존 · §1.2 ①). 코드값만 적는다 — 표시 이름(메모리·프로세스)은 server.*와 같아 못 가른다
    resource_types: [db2.Memory, mariadb.Memory, mssql.Memory, mysql.Memory, tibero.Memory,
                     mariadb.Process, mssql.Process, mysql.Process, oracle.Process, postgresql.Process, sybase.Process, tibero.Process]
    alarm_kinds: []
    playbook_prefixes: [was.sql]  # SQL 패턴 플레이북은 WAS와 DB 양쪽 영역
# 각 Wave가 섹션을 더한다: welcome_queries(W2) · alarm_prompt_priority · cause_hints(W3) · checklists(W5).
```

- **W0 폐쇄망 반영(§1.2 ①′)**: 실제 알람 payload의 `resource_type`은 한글 표시 이름(「CPU」·「메모리」·「개별 파일시스템」…)이다. `resource_types`에는 코드값과 표시 이름을 **함께** 적는다(예: `[server.Cpus, CPU, server.Memory, 메모리, …]`). 표시 이름 목록은 `cmm_resource_type.displayname` 세 존 실측(§1.2 ①″)으로 채웠다.
- **표시 이름의 한계(①″)**: 「메모리」·「프로세스」는 `server.*`와 DB 엔진형 유형(`db2.Memory`·`oracle.Process` 등)이 같은 이름을 쓴다. 알람 payload에는 표시 이름만 오므로 **표시 이름은 server_os로만 매핑**한다(현재 세 존 알람은 전부 `server.*` — 수용 가능. DB 엔진형 유형은 라이선스상 b0에서만 쓸 수 있다고 알려져 있으나 **b0 등록 자원에도 0건**이다 — b0 `CMM_RESOURCE` 비 `server.*` 실측: management.MonitorGroup 4,276 · icmp.ICMPNode 1,002 · management.Group 834 · polestar.* 내부 자원 30여 · 커스텀 스크립트 감시 4. 미도입이거나 DB 감시를 DPM으로 옮긴 것으로 추정(사용자 2026-10-30). **현재 세 존 모두 모호성 위험 0** — 엔진형 자원이 등록되기 시작할 때만 재판단한다). DB 엔진형 자원 감시가 켜지면 표시 이름으로는 가를 수 없으니 그때 코드값 확보(hostname 역조회 D-179 경로 또는 템플릿 변수)를 따로 판단한다.
- **DB 엔진 시그니처는 실시간 프로세스 목록에만 쓴다**(v1.2 · 사용자 확인 2026-10-30). 폴스타 「프로세스」 알람은 사용자가 자원 트리에 직접 등록한 프로세스에서만 나고 DB 엔진을 등록한 경우는 거의 없다 — 프로세스 알람의 대상 이름 대조로 db 단서를 만들지 않는다. 시그니처가 쓰이는 곳은 CPU·메모리 알람의 「영향 프로세스」 스냅샷(§5.3 ②)과 수동 조사 호스트 단계(W4 R-E `cpu_owner`) 둘뿐이다. 「실시간 프로세스에 DB 엔진이 보이면 DB 서버」가 현재 유일한 (대략적) DB 서버 식별 근거다 — DPM 연동 전까지(§13).
- `weburl.WebURLMonitor`(WebURL 감시)는 **was_mw 발생 영역**으로 둔다(§12 Q9 — 서비스 접점은 WAS 담당이 먼저 본다 · 원인 단서 없음 · Q5 WAS 검토자에게 확인 항목 1줄). 알람 집계에는 `server.WebURLMonitor`로도 나와 두 철자를 함께 적는다. `network.*`·`sap.*`는 3영역 밖(미매핑 = 강조 없음).
- `network`·`storage`·`backup`은 이번 범위 밖이다(§13). 코드 값은 이 파일에 행을 더하는 것으로 확장한다.
- 한 알람·단계가 여러 영역에 속할 수 있다(`was.sql` → was_mw·db). 판정 함수는 **영역 목록**을 돌려준다.

### 3.2 저장

| 대상 | 변경 |
|---|---|
| `src/domain/user.py` | `duty_areas: list[str] \| None = None` 필드 · `to_auth_dict()`에 키 추가 |
| `src/api/server.py` `_ensure_auth_tables` | `ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS duty_areas TEXT[]` (기존 행 NULL = 미지정) |
| `ddl/auth_tables.sql` · `db/init/03_auth_tables.sql` | 신규 설치 DDL에 같은 열 |
| `src/infrastructure/user_repository.py` | 읽기·쓰기 매핑 |
| `src/api/schemas.py` | 가입 요청 · `UpdateUserRequest` · 사용자 응답에 `duty_areas` |

- 검증: 값은 `config/duty_areas.yaml`의 `code` 집합 안이어야 한다 — 밖이면 422(조용히 버리지 않는다). 중복 제거 · 선언 순서로 정렬.
- 어휘에서 코드가 빠지면(영역 폐지) 저장된 값은 읽을 때 무시하고 경고 로그 1줄(재저장 시 정리).

### 3.3 지정 경로 (3곳)

| 경로 | 화면 | 서버 |
|---|---|---|
| 가입 | `src/static/register.html` 체크박스(선택 · 미선택 가능) | `user_auth.py` 가입 라우트 |
| 관리자 | `src/static/js/admin.js` 사용자 탭 — 존 체크박스(`admin.js:1829`) 옆에 담당 체크박스 | `admin.py` 사용자 수정(`:1026` 부서 처리와 같은 자리) |
| 관리자 일괄 | 사용자 탭 다중 선택 → 담당 일괄 지정(§3.7) | 같은 수정 경로 반복(사용자별 감사 로그) |

- **본인 변경 경로는 만들지 않는다**(§12 Q1 개정 · v1.2) — 현재 사용자가 자기 정보를 바꾸는 화면이 없다(본인 API는 `PUT /auth/password` 하나 · 이를 부르는 화면 0). 가입 뒤 변경은 관리자만 한다. 본인 정보 화면이 생기면 그때 추가를 판단한다.
- 변경은 감사 로그 대상이다(누가·누구의·이전값 → 새값).
- 플래그 `DUTY_AREA_ENABLED`(기본 false) off면 세 화면 모두 항목을 그리지 않고, API는 필드를 받지 않는다(무시가 아니라 스키마에서 제외 — 현행 비트 동일).

### 3.4 클라이언트 전달

- 로그인 사용자 정보 응답(`/me` 계열)에 `duty_areas`와 영역 메타(`code`·`label`·W2~W5가 쓰는 섹션)를 싣는다. 프론트는 이것만 읽는다 — 프론트에 영역 어휘 사본을 두지 않는다.

### 3.5 미지정 안내

- 플래그 on · 담당 미지정 사용자에게 로그인 후 질의응답 탭 상단에 1회 안내: "담당 업무를 지정하면 예시 질문·알람·조사 결과가 담당 영역 중심으로 정리됩니다. 담당 업무는 관리자가 지정합니다 — 관리자에게 요청하세요. [닫기]". 본인 변경 화면이 없으므로 [지정하기] 버튼은 두지 않는다(Q1 개정). [닫기]는 localStorage에 기억(D-178과 같은 성격의 표시 선호).
- 인증 방식은 모든 경로가 `local`이라(`user_auth.py:218` · `server.py:212` — SSO 가입 없음) **새 사용자는 전부 가입 화면을 거친다.** 지정률을 좌우하는 것은 기존 가입자이며, §3.7 일괄 지정이 주 경로다.

### 3.7 관리자 「담당 업무 현황」 카드 (v1.2 · 사용자 관리 탭)

별도 대시보드를 만들지 않고 관리자 대시보드 기존 **「사용자 관리」 탭**(`src/static/admin/dashboard.html` `data-tab="users"`) 상단에 카드 하나를 둔다. 담당 기능의 상태 점검을 한곳에 모은다.

| 칸 | 내용 |
|---|---|
| 지정률 | 「담당 지정 12/40명 · 서버/OS 5 · WAS/미들웨어 4 · DB 3 · 미지정 28」 · 사용자 목록 「담당」 열 · 「미지정만 보기」 필터 · 다중 선택 일괄 지정(§3.3) |
| 매핑 점검 | 최근 7일 판정 레코드에서 **볼 때마다** 센다(메모리 계수기 없음 · 재기동에 안 사라짐 · 새 수집 없음): ① 어느 영역에도 매핑되지 않은 `resource_type` 상위 5 + 건수 ② `cause_hints`에 없는 제니퍼 `event_type_norm` 상위 5 + 건수. 「유형만으로 원인을 못 가르는 유형」(일반 응답 지연·서비스 예외·에이전트 중지 등 — `cause_hints.known_unmapped`)은 **알려진 미매핑**으로 따로 접어 경고에서 뺀다 |
| 알림 | 알려진 목록 밖의 미매핑 유형이 있으면 「사용자 관리」 탭 이름 옆에 **건수 배지** — 관리자가 대시보드에 들어오기만 하면 보인다(로그만 남기면 조용히 깨지는 것과 같다 · 원칙 7) |
| 조치 안내 | 「`config/duty_areas.yaml`의 `resource_types` 또는 `cause_hints`에 추가하세요」 1줄 · 표시 이름 매핑 근거(§1.2 ①″) 링크 |

- 관리자 전용(`require_admin_user`). 플래그 `DUTY_AREA_ENABLED` off면 카드·열·배지 모두 없음.
- 결정 기록 읽기는 노이즈 관제 집계(`/noise/summary`)와 같은 저장소 읽기를 재사용한다(새 파일 경로 없음).

### 3.6 W0 실측 (W1 착수 전)

1. 폐쇄망 세 존의 `resource_type` 실제 값 목록 — DB 서버·DB 프로세스 알람이 어떤 유형으로 오는지(`db` 영역 `resource_types`를 채울 수 있는지).
2. 알람 SSE payload에 `resource_type`·`alarm_kind`가 실리는지(W3 판정 키).
3. 제니퍼 이벤트 유형 중 SQL 계열 정규화 값(`SQL_EVENT_TYPES`)이 SSE까지 오는지(`db` 영역 알람 판정).
4. 제니퍼가 인스턴스의 DB 연결 대상(JDBC URL·DB 호스트)을 Open API로 주는지 — 주면 W4에서 DB 서버 OS 지표까지 원인 근거로 쓸 수 있다.
5. 알람 SSE payload에 제니퍼 이벤트 유형(정규화 값 또는 게이트웨이 `EVENT_TYPE_SIGNALS` kind)이 실리는지 — §5.3 원인 단서 ①의 판정 키. (W0 결과: kind 없음 · `process_snapshot`도 실 SSE에 없음 · `episode_lines`만 있음 — §1.1)
6. 노이즈 결정 기록(결정 저장소) 레코드에 `resource_type`·제니퍼 이벤트 kind·존(db_id)이 남는지 — W6 요약 카드의 담당·존 필터 키. 노이즈 관제 사용자 경로(`noise_dashboard.py`)의 24시간 집계를 재사용할 수 있는지와 그 경로의 존 필터 유무.
7. 제니퍼 트랜잭션 프로파일에 `acquire_ms`(커넥션 획득)·`execute_ms`(SQL 실행) 구조화 필드가 오는지 — 없으면 R-D `db_split`이 늘 n/a라 커넥션 계열이 항상 「둘 다 의심」이 된다(§6.1 · Q7).

---

## 4. W2 — 첫 화면: 상황 안내 + 담당별 예시 질의

### 4.0 첫 화면 배치 (P2 · P5 · 예시 질의 통합)

세 요소를 따로 쌓으면 길어지고 겹친다(「정기 점검」 안내 ↔ 예시 질의 · 「알람을 받았다면」 안내 ↔ 24시간 요약). 한 화면에서 역할을 나눈다. 첫 질문을 보내면 지금처럼 함께 사라진다.

```
┌ 지난 24시간 · 내 담당(서버/OS) ─────────── [이벤트 알람에서 보기] ┐  ← W6 (담당 지정 + 알람 존 있는 사용자만)
│ 즉시 통보 2 · 대기열 14 · 억제 120                                 │
│ 확인 필요 ▸ 김포 APP-03 FS 92% (단서: OS)                           │
└───────────────────────────────────────────────────────────────────┘
이 화면에서 할 수 있는 일                                              ← W2 상황 안내 (모든 사용자)
 🔔 알람을 받았다면 — 이벤트 알람 탭에서 카드의 「원인이 내 쪽인가」를 먼저 보세요
 🔍 원인을 찾고 있다면 — 조사 대화 탭에 서버 이름과 증상을 적으세요 (예: 김포 APP-03 CPU 왜 높아?)
 📄 보고 자료가 필요하다면 — 엑셀·워드 양식을 올리면 빈칸을 채워 드립니다
이렇게 물어보세요 · 서버/OS 담당 추천                                  ← W2 예시 질의 (미지정 = 현행 버튼)
 [CPU 80% 이상 서버] [FS 90% 이상] [메모리 급증 서버] ... [❓ 사용법]
```

- **상황 안내(P2)**: 기능 이름(탭) 대신 상황으로 시작하게 하는 3줄 문장 목록. 「정기 점검」은 예시 질의가 그 역할을 하므로 따로 두지 않는다. 줄을 누르면 해당 탭으로 이동한다. 담당과 무관하게 모든 사용자에게 보인다.
- 사용자에게 보이지 않는 탭(알람 존 없음 · 플래그 off 탭)으로 가는 줄은 숨기지 않고 비활성 + 사유(「알람 수신 지역이 지정되지 않았습니다」 등).
- 문구는 정적 마크업(`index.html`)이며 매뉴얼·포탈 도움말(D-315)과 같은 용어를 쓴다. 탭 이름이 바뀌면 함께 고친다.
- 플래그 `WELCOME_GUIDE_ENABLED`(기본 false · 담당 플래그와 독립) — off면 현행 첫 화면 그대로.

### 4.1 예시 질의 동작

- 플래그 `DUTY_WELCOME_HINTS_ENABLED`(기본 false) on · 담당 지정 사용자: 첫 화면 예시 버튼을 **담당 영역 문구**로 바꾼다. 여러 영역이면 선언 순서대로 영역마다 최대 3개, 전체 최대 6개. 「❓ 사용법」 버튼은 항상 끝에 남긴다.
- 미지정 · 플래그 off: `index.html`의 현행 버튼 그대로(JS가 교체하지 않는다).
- 버튼 위에 작은 제목 「서버/OS 담당 추천」처럼 영역 이름을 붙여 왜 이 질문이 보이는지 알린다.
- `ㅇㅇ존` 자리표시 규약을 그대로 쓴다(존 선택 흐름 재사용).

### 4.2 문구 정본

- `config/duty_areas.yaml` 각 영역의 `welcome_queries: [{label, query, verified_by}]`.
- `verified_by`는 필수다 — 골드셋 항목 id 또는 결정적 조립기 이름. **비어 있는 문구는 로드 시 제외하고 경고**한다(D-192 · D-341 ⑪). 검증 근거 없는 문구가 화면에 나가지 않는 것을 코드로 보장한다.

### 4.3 문구 초안 (부록 A) — W2에서 확정

- 초안은 인지(지금 상태) → 분석(추세·비교) → 점검(정기) 순으로 영역당 4~5개를 둔다.
- **DB 영역은 간접 근거 문구로 한정한다** — DB 서버의 OS 지표와 제니퍼가 본 SQL. DB 내부(세션·락·테이블스페이스)를 묻는 문구는 넣지 않는다(답할 수 없음).
- 확정 절차: ① 골드셋 동형 검색 ② 동형 없으면 로컬 샌드박스에서 실행 확인(MLX 최소 · 문구당 1회) ③ 폐쇄망 담당자 검토(§12 Q5).

---

## 5. W3 — 알람 화면 담당 강조

### 5.1 영역 판정 (프론트 순수 함수)

- `dutyAreasOf(alarm)` — `resource_type`으로 §3.1 매핑을 본다(SSE에 `alarm_kind` 최상위 키 없음 — §1.1 ②). 폐쇄망 값은 한글 표시 이름이므로(§1.2 ①′) 매핑 대조는 코드값·표시 이름 둘 다 받는다. 단위 테스트 픽스처도 표시 이름(「CPU」·「개별 파일시스템」)으로 쓴다. 매핑은 §3.4로 받은 메타만 쓴다. 미상은 빈 목록(강조 안 함).
- 단위 테스트는 실 SSE payload 형태(§3.6 실측)로 쓴다.

### 5.2 표시

| 요소 | 동작 |
|---|---|
| 「내 담당」 배지 | 「내 담당 · 발생」(발생 영역이 담당과 겹침) · 「내 담당 · 단서」(§5.3 원인 단서 영역이 담당과 겹침). 둘 다 알람 도착 시 payload만으로 정한다 — 조사 결과를 기다리지 않는다 |
| 「내 담당 먼저」 토글 | 알람 목록 정렬만 바꾼다(같은 티어 안에서 담당 카드 우선 · **티어 순서는 깨지 않는다** — PAGE가 담당 외라고 뒤로 가지 않는다). 숨김 없음. 토글 상태는 localStorage(D-178 성격) |
| 추천 질의 순서 | `buildAlarmPrompts` 결과를 담당 축 우선으로 재정렬(`alarm_prompt_priority`). **새 추천을 만들지 않는다** — 검증된 추천의 순서만 |
| 노이즈 관제 | 결정 이력 솔루션 필터(D-351) 기본값을 담당에서 유도(server_os → 폴스타 · was_mw → 제니퍼) — §12 Q3 |

- 플래그 `DUTY_ALARM_FOCUS_ENABLED`(기본 false).
- 서버의 존·티어 가시성 판정(`event_visible_to`)은 건드리지 않는다.

### 5.3 원인 단서 — 조사 없이 알람 정보만으로 (2026-10-30 사용자 제기 반영)

조사는 **수동 전용**이고(D-347 ① — 자동 조사 off) 시간당 예산 20건·대기열 4건 상한이 있다(D-347 ②). 따라서 **대부분의 알람은 조사 결과(W4)가 없다.** 원인 영역을 조사에만 기대면 담당 강조가 대부분의 알람에서 작동하지 않으므로, 알람이 이미 싣고 오는 정보로 **단서**를 만든다. 자동 조사를 다시 켜는 것은 이 계획의 범위가 아니다(D-347 유지).

| 단서 | 출처(알람 도착 시 이미 있음) | 단서 영역 | 범위 |
|---|---|---|---|
| ① 제니퍼 이벤트 유형 | 정규화 유형 `apm.event_type_norm`(§12 Q8 — SSE 키 `apm_event_type`) — `BAD_RESPONSE_TIME_SQL` · `SQL_TOOMANY_FETCH` · `DB_CONN_UNCLOSED` · `DB_CONNECTION_FAIL`·`JDBC_CONNECTION_FAIL` · `MAYBE_GC_TIME_DELAY`·`OUTOFMEMORY`·`JVM_HEAP_MEM_HIGH` · `BAD_RESPONSE_TIME_EXTERNAL_CALL`·`HTTP_IO_EXCEPTION`·`SOCKET_EXCEPTION` | db(SQL 응답 지연) · **was_mw·db 둘 다**(대량 조회 — 앱 쿼리 설계 몫이 큼) · **was_mw**(미반납 — 앱 누수 · Q7 ②) · **둘 다**(연결 실패 · Q7 ②) · was_mw(GC·힙 — 표본 0건) · **was_mw · 근거 「외부 연계」**(외부 호출 지연·HTTP IO·소켓 — Q12) | 제니퍼 알람 중 해당 유형만(표본 기준 약 42%). **응답 지연(일반)·서비스 예외·에이전트 중지처럼 유형만으로 원인을 못 가르는 알람은 단서 없음**(표본 약 1,550건 · §1.2 ③ — `known_unmapped`로 §3.7 경고에서 제외) |
| ② 영향 프로세스 | `process_snapshot`(Plan 47-1 · **CPU/메모리 알람만** · 폴스타 실시간 프로세스 조회 · `ALARM_PROCESS_ENRICH_ENABLED` 기본 true) 상위 프로세스 → `middleware_signatures` kind | was_mw(WAS·웹서버) · db(DB 엔진 — §13 시그니처 선행 · 「이 서버에서 DB 엔진 실행 중」) · 그 밖 server_os | 폴스타 CPU/메모리 알람 중 스냅샷이 있는 것. **프로세스 알람의 대상 이름은 쓰지 않는다**(§3.1 — 등록 프로세스뿐) |
| ③ 사건 묶음 | `episode_lines`(plans/144 크로스소스 · 같은 시각 다른 소스 알람) | 묶인 상대 알람의 발생 영역 | 크로스소스 묶음이 켜진 환경(annotate·enforce)만 — **폐쇄망은 현재 shadow라 비활성**(§1.2 ⑥) |

- 단서는 **원인 판정이 아니다.** 화면 문구는 「단서 · 알람 정보만 봄(조사 전)」으로 고정하고, 근거(이벤트 유형 이름·프로세스 이름·묶인 알람)를 함께 보인다. 단서가 없으면 아무것도 표시하지 않는다(「원인 미상」 같은 빈 단정도 하지 않는다).
- 판정 함수는 프론트 순수 함수이고, ①의 유형 → 영역 매핑은 `config/duty_areas.yaml` `cause_hints`에 둔다. **①은 게이트웨이 kind가 아니라 정규화 유형으로 대조한다**(§12 Q8) — kind는 연결 실패·미반납을 한 kind로 묶고 SQL 계열 kind가 없어서다. 유형 리터럴은 `cause_hints`에만 두고, 대조는 공백·대소문자를 정규화한다(폐쇄망 원값 `METRICS EVENT` · §1.2 ③). 게이트웨이 `EVENT_TYPE_SIGNALS`와 sre_agent `SQL_EVENT_TYPES`는 고치지 않는다.
- **W0 반영(§1.1 ③⑤⑤' · §1.2 ③)**: 실 SSE에는 유형·kind·`process_snapshot`이 없다. 그래서 W3에 noise_gate 소폭 변경을 둔다 — notifier가 이미 가진 값을 SSE 키로 옮긴다: **`apm_event_type`**(`raw_payload.apm.event_type_norm` · 단서 ①) · `top_process_kinds`(스냅샷 상위 프로세스의 미들웨어 kind · 단서 ②). 둘 다 플래그 `DUTY_ALARM_FOCUS_ENABLED` 뒤에만 싣는다(off = payload 비트 동일). 당초 1차에서 뺐던 **SQL 계열 db 단서는 1차에 넣는다**(§12 Q8 — 폐쇄망 표본에서 SQL 계열 576건 · 21%가 db 단서의 사실상 유일한 원천). `apm_signal_kinds`는 ①이 유형으로 대조하게 되어 싣지 않는다.
- 조사(W4)가 끝난 알람은 조사 결과가 단서보다 우선한다 — 카드가 조사 결과를 펼쳤을 때만 바뀐다(펼칠 때만 조회 원칙 유지 · 배지는 단서 기준 그대로).

### 5.4 W3b (선택) — WAS 알람 추천 축

- 현재 `apm.Instance` 축이 없어 WAS 담당자는 알람 카드에서 추천 질의를 받지 못한다. 제니퍼 벤치(`testdata/apm_bench`) 동형으로 확인되는 문구가 있으면 `ALARM_PROMPT_AXES`에 `apm.Instance` 축을 더한다(plans/195 Q2 ② 「제니퍼 벤치 인정」 결정과 같은 기준). 확인 못 하면 비워 두고 사유 주석.

---

## 6. W4 — 조사 결과 영역별 판정과 인계 판단

담당자가 조사 결과에서 가장 먼저 알고 싶은 것은 **"원인이 내 영역인가, 넘겨야 하나"**다. R&R이 복잡한 환경(b0 은행 · cm 은행/통합MSP 혼재)에서 인계 판단이 빨라지는 것이 처리 시간에 가장 크게 듣는다.

> **적용 범위(2026-10-30)**: W4는 **사용자가 [조사 시작]을 눌러 조사가 끝난 알람**에만 나온다(D-347 수동 전용). 담당자가 실제로 파고들 때 인계 판단을 돕는 기능이고, 전체 알람의 원인 영역 표시는 §5.3 원인 단서가 맡는다.

### 6.1 산출 — 브리핑 선택 키 `duty_view`

6요소는 그대로 두고 선택 키를 더한다(`cross_source` 전례 · `briefing_builder.py:259`).

```json
"duty_view": {
  "areas": [
    {"code": "was_mw", "status": "abnormal", "signals": ["힙 사용률 92% (S3 ← apm.heap_trend)"], "steps": ["S2","S3"]},
    {"code": "db", "status": "suspect", "signals": ["Top5 트랜잭션 SQL 구간 비중 71% (S3 ← apm.slow_tx_breakdown)"], "steps": ["S3"]},
    {"code": "server_os", "status": "unverified", "reason": "이 조사는 호스트 단계를 돌리지 않음", "steps": []}
  ],
  "handoff": "이상 신호가 WAS/미들웨어·DB 영역에 있습니다 — 두 담당의 확인이 필요합니다."
}
```

- **발생 영역 ≠ 원인 영역 (2026-10-30 사용자 제기 반영)**: 제니퍼 알람은 WAS에서 **드러났을 뿐** 원인은 DB·OS일 수 있다. 그래서 두 값을 따로 둔다.
  - **발생 영역**(W3 배지) — 알람 출처로 결정적: 제니퍼 → was_mw · 폴스타 `server.*` → server_os. 단계 `action` 접두(`apm.*`)로 원인 영역을 정하지 않는다(`apm.slow_tx_breakdown`은 WAS 도구지만 SQL 구간 지배면 DB 근거다).
  - **원인 영역**(W4) — 플레이북이 이미 만드는 **판독 계층**(`sre_agent/sre_agent/domain/engine_agreement.py` `LAYERS` — R-C `dominant` · R-D `db_split` · R-F `heap_shape` · R-E `cpu_owner` · R-G `host_saturation`)과 가설 규칙표(`playbook_hypotheses.py` `HYPOTHESIS_RULES`)의 상태(지지·판단 보류·반박)를 담당 영역으로 옮겨 쓴다. 새 판정 로직을 만들지 않는다.
  - 계층 → 영역: `db` → db · `gc`·`cpu`(R-E `was_logic`·`was_load`) → was_mw · `host_resource` → server_os · `external` → **was_mw + 근거 「외부 연계 — 상대 시스템 확인 필요」**(Q12 · v1.2 — 애플리케이션 담당이 따로 없어 연계 설정을 알고 상대에 연락하는 WAS 담당이 받는다 · 표시에 「(외부 연계)」를 붙여 「WAS 문제」 오독을 막는다 · 애플리케이션 영역을 열면 옮긴다 §13) · R-E `other_process` → 그 프로세스의 미들웨어 시그니처 kind로(WAS·웹서버 → was_mw · DB 엔진 → db · 그 밖 → server_os). DB 엔진 시그니처 추가(§13)를 **W4 선행**으로 당긴다.
  - **커넥션 풀·DB 연결 (§12 Q7 권고)** — 같은 증상(획득 지연·풀 고갈)이 DB 지연의 결과일 수도, WAS 풀 설정·앱 누수일 수도 있다. 계층표(`engine_agreement.py`)는 획득·실행·풀 고갈을 모두 `db`로 묶지만 이는 두 엔진의 결론 비교용(표시 전용)이라 담당 판정에는 거칠다. 그래서 커넥션 계열은 **가설 규칙표를 계층표보다 먼저** 본다.
    | 가설(`playbook_hypotheses.py`) | 조건 | 원인 영역 |
    |---|---|---|
    | H-POOL-INSTANCE (인스턴스 풀 설정·점유) | 획득 지연 ∧ 이 인스턴스만 | was_mw |
    | H-DB-POOL-CHAIN (DB 지연 → 풀 고갈) | SQL 구간 지배 ∧ 풀 고갈 | db (was_mw는 영향 — 발생 영역으로만) |
    | H-DB-SHARED (업무 DB 공통 지연) | 실행 지연 ∧ 공통 의존 | db |
    | H-DB-INSTANCE (인스턴스 SQL 지연) | 실행 지연 ∧ 이 인스턴스만 | db |
    - 1순위 가설이 **지지**면 위 표대로 한 영역 · **판단 보류**(예: R-D `db_split`이 n/a라 획득·실행을 못 가름)면 **was_mw·db 둘 다 의심** + 사유 「획득·실행 구분 정보 없음」 · 커넥션 계열 가설이 없으면 계층표로 대략 판정. 계층표는 고치지 않는다(187·198 엔진 일치 표시에 영향).
    - 가설 → 영역 매핑도 `sre_agent/config/duty_area_layers.yaml`에 둔다(가설 id 키). R-D가 판독되려면 게이트웨이가 프로파일 `acquire_ms`·`execute_ms`를 줘야 한다(W0 ⑦).
  - 근거 한계: ① OS 원인 확인은 WAS 서버의 폴스타 연결(D-348·D-355)이 있어야 한다 — 없으면 server_os = `unverified`(「WAS 서버의 폴스타 연결 없음」). ② DB 서버 자체 지표는 WAS가 어느 DB 서버를 쓰는지 모르면 볼 수 없다 — db 영역 근거는 「앱이 본 SQL」까지이고, 제니퍼가 JDBC 대상 주소를 주는지는 W0 실측(§3.6 ④). ③ 상관 ≠ 인과(`CORRELATION_NOT_CAUSATION_NOTE`) — 표시는 「원인 확정」이 아니라 「의심 · 확인 필요」.
  - 매핑은 sre_agent 자체 config(`sre_agent/config/duty_area_layers.yaml`)에 두고 영역 코드는 §6.3 계약으로 묶는다. 역방향(OS 알람인데 원인이 WAS 프로세스)도 같은 표로 대칭 처리된다(R-E `cpu_owner`).
- **상태 4값**: `abnormal`(단계 판정이 이상 · 도구 인용 있음) · `suspect`(가설 · 한쪽 인용) · `normal`(해당 영역 단계가 돌았고 **인용 있는 정상 판정**) · `unverified`(단계 없음·실패·생략 — 사유 필수). 근거 없이 `normal`을 내지 않는다(원칙 7).
- 상태 판정은 기존 단계 판정·증거 원장(`evidence_ledger`)·인용 검증 결과를 읽기만 한다 — 새 LLM 호출 없음.
- **인계 문구**: `abnormal`/`suspect` 영역 목록으로 결정적 템플릿을 고른다. 조직·사람 이름은 넣지 않는다(R&R은 이 시스템이 정하지 않는다 — §13).
- HolmesGPT(심층) 단독 조사처럼 단계가 없는 잡은 `duty_view`를 싣지 않는다(키 없음 = 현행 렌더).

### 6.2 화면 — `briefing-view.js` `renderBody`

- 요약 바로 아래 「영역별 점검 결과」 3행 표(영역 · 상태 칩 · 근거 1줄 · [단계 보기]). `unverified`는 사유를 그대로 보인다.
- 담당 지정 사용자는 **자기 영역 행이 맨 위**이고 행에 「내 담당」 표시. 미지정은 선언 순서.
- 인계 문구는 표 아래 한 줄.
- 알람 카드·노이즈 관제 드로어가 같은 함수를 쓰므로 한 번 고치면 두 곳에 같이 나온다(D-325).
- 채팅 장애 진단 경로(W0 실측 §1.1 렌더): 조사 접수 경로(`sre_chat_investigation_enabled` on)는 카드가 `renderBody`를 쓰므로 자동 반영. 동기 경로(off · 기본)는 `render_briefing_lines`에 인계 문구 1줄만 더한다.

### 6.3 영역 코드 계약

- `config/duty_areas.yaml`(본체)과 `sre_agent/config/duty_area_layers.yaml`(sre_agent)의 영역 코드 집합이 같아야 한다. 양방향 import 0(D-139)을 지키기 위해 **루트 `tests/`의 계약 테스트가 두 YAML을 파일로 읽어 대조**한다.

### 6.4 플래그 · 미지정 사용자

- sre_agent `SRE_DUTY_VIEW_ENABLED`(기본 false) — off면 `duty_view` 키 없음 = 브리핑 비트 동일.
- 화면은 키가 있으면 그린다. **판정표는 담당 미지정 사용자에게도 보인다**(권고 — 인계 판단은 담당과 무관하게 유용) — §12 Q4.

### 6.5 검증

- 플레이북 단위 고정 픽스처(host.cpu · was.heap · was.sql · 단계 실패 · 단계 생략)로 상태 4값과 사유를 단언한다.
- **"근거 없는 normal 0건"**을 별도 단언으로 고정한다.
- MLX는 쓰지 않는다(LLM 경로 변경 없음).

---

## 7. W5 — 조치 지원

### 7.1 담당별 다음 확인 질의

- `plans/195`가 만드는 「질의응답에서 확인할 질의」(`queries` 섹션 · 코드 템플릿 · D-192 방식 확인 뒤 전송)를 **담당 영역 순으로 재정렬하고 영역 이름으로 묶는다.** 새 질의 생성기를 만들지 않는다 — 195가 선행이다.
- 195가 미착수면 W5.1은 보류하고 W5.2·W5.3만 진행한다.

### 7.2 영역별 점검 순서 (정적 체크리스트)

- `config/duty_areas.yaml` `checklists: {<alarm_kind 또는 playbook id>: [단계 문장…]}` — 예: server_os × cpu = "① 같은 시각 다른 서버도 높은지 ② 상위 프로세스 ③ 최근 변경 ④ 지속 시간". 
- 브리핑 「권고」 아래에 담당 영역 것만 접어서 보인다(미지정 = 공통 순서 1벌만 · 공통도 없으면 없음).
- 문장은 **사람이 확인하는 순서**이고 시스템 실행이 아니다(D-011 · human-gated 문구 유지).
- 문안 출처는 폐쇄망 담당자 검토(§12 Q5). 초안만 W5에서 쓴다.

### 7.3 인계 요약 복사

- [인계용 요약 복사] — 알람(서버·시각·유형) · 영역별 점검 결과(§6) · 근거 단계 · 조사 화면 링크를 결정적 텍스트로 클립보드에 넣는다. 메신저·티켓에 붙여 넣는 용도.
- 마스킹은 사용자 뷰 규칙(D-325 ③)을 그대로 거친 값만 쓴다.

- 플래그 `DUTY_ACTION_ASSIST_ENABLED`(기본 false).

### 7.4 알람 카드 재배치 — 인지 → 분석 → 조치 (P3)

이벤트 알람 탭 카드의 칸은 지금 순서 없이 나열돼(요약 · 패턴 분석 · 영향 프로세스 · 사건 묶음 · 추정 원인 · 권고 조치 · 조사 · 추천 질의 · 확인 · 유용했나요) 무엇부터 보고 언제 끝난 건지 알기 어렵다. **덧붙이지 않고 재배치한다** — 기존 칸의 내용은 그대로, 순서와 묶음만 바꾸고 새로 붙는 것은 맨 위 진행 표시 한 줄뿐이다.

```
[알람] APP-03 · 응답시간 지연 · 경고                [발생: WAS] [내 담당 · 단서: DB]
 ● 인지 ─── ○ 분석 ─── ○ 조치
① 무슨 일인가       요약 · 패턴 분석 · 사건 묶음
② 원인이 내 쪽인가   조사(브리핑 — 끝났으면 영역별 점검 결과 W4) · 영향 프로세스 · 추정 원인
③ 무엇을 하나       권고 조치 · 점검 순서(W5) · 추천 질의 · [인계 요약 복사] · [확인(ack)] · 이 알람이 유용했나요?
```

| 묶음 | 옮겨 오는 기존 칸 | 이 계획이 더하는 칸 |
|---|---|---|
| ① 무슨 일인가 | 요약 · 패턴 분석 · 사건 묶음 | 「발생」·「단서」 배지(W3 — 카드 머리) |
| ② 원인이 내 쪽인가 | 조사(브리핑) · 영향 프로세스 · 추정 원인 | 영역별 점검 결과(W4 — 브리핑 안) |
| ③ 무엇을 하나 | 권고 조치 · 추천 질의 · 확인(ack) · 유용했나요 | 점검 순서 · 인계 요약 복사(W5) |

- **진행 표시**: 인지 = 카드를 열었을 때 · 분석 = 조사 결과를 펼쳤을 때 · 조치 = 확인(ack)·인계 복사·추천 질의 전송 중 하나. 상태는 **브라우저에만**(localStorage · 서버로 보내지 않음 — 수집 아님). 확인(ack)만 지금처럼 서버 기록(D-049).
- **유지하는 동작**: 「일상적 반복」 알람의 추천 질의 접기(Plan 86) · 조사 결과는 펼칠 때만 조회(plans/153 W3) · 조사 칸 「조사 안 됨 + [조사 시작]」(D-347) · 게이트 off 시 피드백 버튼 미렌더(Plan 83 T7).
- **범위**: 이벤트 알람 탭 카드만. 노이즈 관제 드로어 배치는 바꾸지 않는다(W4 영역별 판정은 공용 렌더라 드로어에도 나온다).
- 담당과 무관하게 모든 사용자에게 적용되고, W3·W4·W5 칸은 각 플래그가 켜졌을 때만 들어간다.
- 플래그 `ALARM_CARD_FLOW_ENABLED`(기본 false · 담당 플래그와 독립) — off면 현행 순서 그대로.
- 매뉴얼 캡처(알람 카드 전 장) 재촬영 대상.

---

## 7A. W6 — 첫 화면 「지난 24시간 · 내 담당」 요약 카드 (P5)

- **TICKET 일배치 요약(D-260)과 다르다.** D-260은 화면이 아니라 worKB 메시지(기본 off · 매일 `NOISE_TICKET_BATCH_SUMMARY_HOUR`시 · 기본 수신자 · 지문별 건수)다. W6은 첫 화면 카드이며 worKB 요약은 건드리지 않는다. **worKB 요약의 담당별 분리는 범위 밖**이다(통보 수신자 = R&R 문제 · §13).
- **원천**: 새 집계·수집 없이 노이즈 결정 기록을 쓴다. 노이즈 관제의 24시간 집계를 재사용할 수 있으면 재사용한다(W0 ⑥).
- **필터**: ① **사용자 알람 존 범위**(`alarm_zones_for_user` · 알람 조회 경로와 같은 판정 D-263) — 카드가 존 밖 알람을 보이면 인가 우회다. ② 담당 영역 — 발생 영역 또는 원인 단서(§5.1 · §5.3)가 담당과 겹치는 알람.
- **내용**: 티어별 건수(즉시 통보 · 대기열 · 화면 표시 · 억제 — 억제는 관리자만 · D-178 규칙) · 「확인 필요」 상위 3건(PAGE·TICKET 중 최신 · 서버 · 유형 · 단서) · 반복 상위 1종. [이벤트 알람에서 보기]는 「내 담당 먼저」 정렬을 켠 채 알람 탭을 연다.
- **표시 조건**: `DUTY_DAILY_SUMMARY_ENABLED`(기본 false) · 담당 지정 · 알람 존 있음. 그 밖 = 카드 없음(첫 화면 현행).
- 첫 화면을 열 때 1회 조회 · 자동 갱신 없음(부하 최소).
- **검증**: 존 밖 알람 0건 단언 · 비관리자 억제 건수 미노출 · 플래그 off = 첫 화면 비트 동일.

---

## 8. 매뉴얼 (D-255)

| Wave | 사용자 매뉴얼(`scripts/manual/content/user.md`) | 관리자 매뉴얼(`admin.md`) |
|---|---|---|
| W1 | 「내 담당 업무 지정」(가입 시 선택 · 이후 변경은 관리자에게 요청 · 권한이 아님 · 미지정이면 달라지는 것 없음) | 사용자 탭 담당 체크박스 · 일괄 지정 · 「담당 업무 현황」 카드(지정률 · 매핑 점검 · 배지 대응법 — §3.7) |
| W2 | 「담당별 예시 질문」 | — |
| W3 | 「내 담당 알람 먼저 보기」 | 노이즈 관제 기본 필터(Q3 채택 시) |
| W4 | 「조사 결과 읽기 — 영역별 점검 결과와 인계」 | — |
| W5 | 「담당별 점검 순서·인계 요약」 · 「알람 카드 읽는 순서」(재배치 · 캡처 재촬영) | 체크리스트 문안 관리(`config/duty_areas.yaml`) |
| W6 | 「첫 화면 24시간 요약」 | — |
| W7 | 「사례로 배우기」(§8.1) | — |

- 절차: `features.yaml` → `content/*.md` → 캡처 → `build` → `pytest tests/test_manual`(`docs/34` §6).
- 포탈 도움말(D-315)은 매뉴얼 색인을 쓰므로 매뉴얼 갱신으로 "내 담당은 어떻게 바꿔?"에 답하게 된다.

### 8.1 W7 — 「사례로 배우기」 장 (P4)

- 사용자 매뉴얼에 새 장. 영역별 1건(OS · WAS · DB) — 기능 설명이 아니라 **흐름**을 보인다. 기능 설명은 기존 장으로 연결만 한다.
- 사례 1건(1쪽 남짓): ① 상황(어떤 알람이 왔나) ② 화면 캡처와 함께 본 것(카드 ① → ② → ③) ③ 판단 포인트(예: 「영역별 점검에서 DB 의심 → DB 담당에 인계」) ④ 결과.
- **사례 원천 = 로컬 샌드박스 재현**(2026-10-30 사용자 확정) — 폴스타 PG 샌드박스(`testdata/pg/init`) + 제니퍼 mock(`apm_gateway/testdata/jennifer`)으로 같은 모양의 사례를 만들어 캡처한다. 폐쇄망 반출 없음. 영역별 담당자(Q5 검토자)가 실제 흐름과 맞는지 검토한다.
- 사례 후보: OS — FS 사용률 경고(영향 프로세스 · 급증 추천 질의) · WAS — 힙 압박 이벤트(조사 → WAS 이상 · OS 정상) · DB — 응답 지연 알람인데 SQL 구간 지배(조사 → DB 의심 · 인계).
- 화면 안 연습 모드는 하지 않는다(2026-10-30 사용자: 과함).
- 포탈 도움말이 이 장을 근거로 "CPU 알람 오면 어떻게 해?"에 답하게 된다.
- **선행**: W4·W5(+카드 재배치) 화면이 생긴 뒤 캡처한다(재촬영 방지).

---

## 9. 설정

| 플래그 | 위치 | 기본 | Wave |
|---|---|---|---|
| `DUTY_AREA_ENABLED` | 본체 `src/config/` 새 그룹 `DutyAreaConfig` | false | W1 |
| `DUTY_WELCOME_HINTS_ENABLED` | 〃 | false | W2 |
| `DUTY_ALARM_FOCUS_ENABLED` | 〃 | false | W3 |
| `SRE_DUTY_VIEW_ENABLED` | `sre_agent` 설정 | false | W4 |
| `DUTY_ACTION_ASSIST_ENABLED` | 본체 | false | W5 |
| `WELCOME_GUIDE_ENABLED` | 본체 · **담당 플래그와 독립** | false | W2(P2) |
| `ALARM_CARD_FLOW_ENABLED` | 본체 · **담당 플래그와 독립** | false | W5(P3) |
| `DUTY_DAILY_SUMMARY_ENABLED` | 본체 | false | W6 |

- 담당 의존 플래그(`DUTY_WELCOME_HINTS_ENABLED`·`DUTY_ALARM_FOCUS_ENABLED`·`DUTY_ACTION_ASSIST_ENABLED`·`DUTY_DAILY_SUMMARY_ENABLED`)는 `DUTY_AREA_ENABLED`가 off면 함께 off로 해석한다(기동 시 1회). `WELCOME_GUIDE_ENABLED`·`ALARM_CARD_FLOW_ENABLED`는 담당과 무관하게 단독으로 켤 수 있다.
- `.env.example`에 전 키를 코드 기본값으로 추가한다(`settings_catalog.field_index()` 대조).
- `src/config/` 변경은 `[웨이브 회귀 필요]` 대상이다(D-303).

---

## 10. Wave 계획

| Wave | 범위 | 선행 | 산출 검증 |
|---|---|---|---|
| **W0** | §3.6 실측 7건 · 채팅 진단 렌더 경로(§6.2) · 195 진행 상태 확인 · §12 질문 확정 | — | 실측 기록을 이 계획서 §1에 추가 |
| **W1** | §3 전부(§3.7 현황 카드 포함) · 매뉴얼 W1 | W0 · `plans/204` | 어휘 밖 값 422 · 미지정/플래그 off 비트 동일 · DDL 멱등 · 감사 로그 · 권한 판정 함수에 `duty_areas` 참조 0건(grep 단언) · `user_repository` 가입·수정·조회 왕복(위치 인자 SQL) · 본인 변경 API 0건 · 매핑 점검 픽스처(미매핑·알려진 미매핑 분리 · 배지 건수) |
| **W2** | §4(상황 안내 §4.0 + 예시 질의) · 부록 A 확정 | W1(예시 질의만 · 상황 안내는 선행 없음) | `verified_by` 없는 문구 로드 제외 · 플래그 off 시 첫 화면 불변 · 권한 없는 탭 줄 비활성+사유 · 문구별 실행 확인 기록 |
| **W3** | §5(+W3b 선택) | W1 · W0 ② | 티어 순서 불변 단언 · 숨김 0 · 추천 재정렬만(개수 불변) |
| **W4** | §6 | W1(화면 강조만) · sre_agent 독립 | 상태 4값 픽스처 · 근거 없는 normal 0 · 키 없음 = 현행 렌더 · 계약 테스트 |
| **W5** | §7(+§7.4 카드 재배치) | W4 · 195(§7.1만) | 체크리스트 담당 필터 · 복사 텍스트 마스킹 · human-gated 문구 유지 · 재배치 전후 칸 집합 동일(누락 0) · 유지 동작 4종 회귀 |
| **W6** | §7A | W3(§5.1·§5.3 판정 함수) · W0 ⑥ | 존 밖 알람 0 · 비관리자 억제 미노출 · 플래그 off 비트 동일 |
| **W7** | §8.1 | W4·W5 화면 | 사례 3건 캡처 · `pytest tests/test_manual` · 영역별 담당자 검토 |

- **진행 순서(v1.2 · 전체 검토 권고)**: 가치가 W3 단서·W4 판정에 몰려 있으므로 앞당긴다 — ① `plans/204`(추천 키·분류 결함 — 담당과 무관한 이득) → ② W1 → ③ W3 → ④ W4 → ~~⑤ Q5 담당자 검토 1회~~ → **Q5 담당자 검토는 건너뛰고 나중에 피드백을 받아 교정한다(2026-10-30 사용자 결정)** → ⑥ W2·W5·W6·W7. W2 상황 안내·W5 카드 재배치는 담당과 독립이라 언제든 병행 가능.
- 회귀는 D-303대로 계획 끝 1회(`regress.py --base <계획 시작 SHA> --files …`), W1(`src/config/`·스키마)은 Wave 끝 모듈 회귀.
- sre_agent 테스트는 자체 `.venv`로 돈다.

---

## 11. 위험과 대응

| 위험 | 대응 |
|---|---|
| 담당이 권한처럼 쓰이기 시작함(나중에 누가 필터로 씀) | D-354에 "인가 판정 사용 금지" 명시 · W1 grep 단언(인가 모듈의 `duty_areas` 참조 0) |
| 담당 외 중대 알람을 놓침 | 숨김 없음 · 티어 순서 우선 · 정렬 토글은 사용자 선택 |
| 답 못 하는 예시 질의로 신뢰 하락 | `verified_by` 필수 · 로드 시 제외 |
| 판정표의 「정상」이 오진을 덮음 | 근거 없는 normal 금지 · `unverified` 사유 필수 |
| DB 담당자가 얻는 게 적어 실망 | 문구·매뉴얼에 "DB 내부 지표는 미수집 — 현재는 DB 서버 자원과 앱이 본 SQL"을 명시 · §13 데이터 편입을 별도 트랙으로 |
| 영역 코드 본체·sre_agent 불일치 | §6.3 계약 테스트 |
| 담당 미지정 다수로 효과 없음 | §3.5 안내 · 관리자 일괄 지정 · 상황 안내(W2)·카드 재배치(W5)는 담당과 무관하게 작동 |
| 대부분의 알람에 조사 결과가 없어 원인 영역을 모름(D-347 수동 전용) | 원인 단서(§5.3 · 알람 정보만) · W4는 수동 조사 알람 한정으로 명시 · 단서 없으면 표시 안 함 |
| 단서가 원인 판정으로 오독됨 | 「단서 · 알람 정보만 봄(조사 전)」 고정 문구 + 근거 병기 |
| 카드 재배치로 익숙한 배치가 바뀜 | 독립 플래그 · 칸 집합 동일 단언 · 매뉴얼 「알람 카드 읽는 순서」 |
| 24시간 요약 카드가 존 인가를 우회 | `alarm_zones_for_user` 필터 · 존 밖 0건 단언 |
| 표시 이름·유형이 바뀌어 매핑이 조용히 깨짐 | §3.7 매핑 점검(결정 기록에서 즉시 계산) · 관리자 탭 배지 |
| 「강조 안 됨 = 내 일 아님」 오독(특히 DB 담당 — 발생 강조 0) | 문구·매뉴얼에 「중점이지 배정이 아님」 · DB 영역 안내 「DB 발생 알람 유형 없음 — 단서로만 표시」 |
| 플래그 8개 조합으로 운영 설정 혼동 | `.env.example`·관리자 매뉴얼에 「권장 켜기 조합」 표 1개 |
| 외부 연계가 「WAS 문제」로 오독 | 근거 문구 「외부 연계 — 상대 시스템 확인 필요」 고정(Q12) |

---

## 12. 질문 (Q1~Q6: 2026-10-30 사용자 확정 — 「Q1~6은 권고안대로 진행」 · Q7~Q11: 2026-10-30 「권고사항으로 적용」 — 전체 검토에서 재확인)

| # | 질문 | 결정 |
|---|---|---|
| Q1 | 본인이 담당을 바꿀 수 있게 할까? | ~~허용~~ → **개정(2026-10-30 사용자): 본인 변경 없음 · 가입 시 선택 + 관리자 변경** — 사용자가 자기 정보를 바꾸는 화면 자체가 없다(본인 API는 비밀번호 변경뿐 · 화면 0). 본인 정보 화면이 생기면 재판단. 관리자 일괄 지정(§3.7)으로 부담을 줄인다 |
| Q2 | 여러 개 선택 허용? | **허용**(원칙 3) |
| Q3 | 노이즈 관제 기본 필터도 담당으로 맞출까? | **보류** — 노이즈 관제는 관제·관리자 성격이 강하다. W3 뒤 사용 피드백으로 결정 |
| Q4 | W4 판정표를 미지정 사용자에게도 보일까? | **보임** — 인계 판단은 담당과 무관하게 유용 · 플래그로만 제어 |
| Q5 | 예시 문구·점검 순서 문안을 누가 검토할까? | ~~폐쇄망 영역별 담당자 1명씩(OS·WAS·DB) 검토 후 확정~~ → **Q5 담당자 검토는 건너뛰고 나중에 피드백을 받아 교정한다(2026-10-30 사용자 결정)** |
| Q6 | 가입 화면에서 담당 선택을 노출할까, 관리자 지정만 할까? | **노출(선택)** — 지정률이 효과를 좌우한다 |
| Q7 | 「커넥션 획득만 지연(실행 정상)」·제니퍼 DB 연결 실패/미반납 이벤트를 어느 영역으로 볼까? | **권고 적용**(2026-10-30) — ① **W4(조사 뒤)**: 가설 규칙표 우선 — H-POOL-INSTANCE 지지 → was_mw · H-DB-POOL-CHAIN·H-DB-SHARED·H-DB-INSTANCE 지지 → db · 판단 보류(획득·실행 구분 불가)일 때만 **둘 다 의심** + 사유(§6.1). 계층표는 고치지 않는다. ② **W3 단서(조사 전)**: 게이트웨이가 DB 연결 실패 2종·미반납을 한 kind(`was_db_pool_exhaustion`)로 묶으므로 kind 단위로 **둘 다 단서**(안 a). 미반납만 was_mw로 가르려면 게이트웨이 kind 세분이 필요하나 플레이북 선택·조치 권고까지 영향이 커 W3 운영 피드백 뒤 검토(안 b 보류). 단서는 강조일 뿐이라 과잉 표시 피해가 작다. **W0 ⑦**: 게이트웨이가 `acquire_ms`·`execute_ms`를 주지 않아 H-POOL-INSTANCE(→ was_mw)는 지금 지지될 수 없다 — 당분간 커넥션 계열은 「db(H-DB-POOL-CHAIN 지지 시)」 아니면 「둘 다 의심」. **Q8로 ② 갱신**: W3 단서가 kind가 아니라 정규화 유형으로 대조하게 되어 안 b의 효과를 게이트웨이 변경 없이 얻는다 — `DB_CONN_UNCLOSED` → was_mw(앱 누수) · `DB_CONNECTION_FAIL`·`JDBC_CONNECTION_FAIL` → 둘 다 |
| Q8 | SQL 계열 db 단서를 1차(W3)에 넣을까? | **넣는다**(권고 적용) — 폐쇄망 표본 2,767건 중 SQL 계열 576건(21%) · DB 연결 실패 4건이라 빼면 DB 담당이 W3에서 받는 게 없다. notifier가 `raw_payload.apm.event_type_norm`을 SSE 키 `apm_event_type`으로 싣고(플래그 뒤) `cause_hints` 유형 목록으로 대조 — 게이트웨이·sre_agent 무변경. `BAD_RESPONSE_TIME_SQL` → db · `SQL_TOOMANY_FETCH` → 둘 다(§5.3 ①) |
| Q9 | WebURL 감시(`weburl.WebURLMonitor`)는 어느 영역? | **was_mw 발생 영역**(권고 적용) — 서비스 접점 감시라 WAS 담당이 먼저 본다 · 원인 단서 없음 · 건수 적음(gp 18 · yd 6 / 30일). Q5 WAS 검토자 확인 항목에 1줄 |
| Q10 | `/noise/*`·`/alarm/metrics` 존 필터 부재(§1.2 곁가지) | **전 존 공개 유지 · `plans/203` 보류**(2026-10-30 사용자 결정 — 의도한 설계는 아니나 지금은 존 간 경계를 나누기 어려움 · 필요 시 존 스코프 · D-263 부기 2). 이 계획 W6은 알람 탭과 맞춰 존 필터 유지 |
| Q11 | 추천 질의 키(`ALARM_PROMPT_AXES`)·`classify_alarm_kind`가 폐쇄망 표시 이름과 불일치(§1.2 곁가지 2) | **별도 결함 수정 `plans/204`를 W3 착수 전에**(권고 적용) — 플래그 없음(결함 수정). 담당 플래그 뒤에 두면 미지정 사용자가 계속 결함을 겪는다. 추천 문구가 LLM 보강 → 결정적으로 바뀌는 점은 그 계획에 명시 |
| Q12 | 외부 연계(외부 호출 지연·HTTP IO·소켓 예외) 원인은 어느 영역? | **was_mw**(2026-10-30 사용자 제안 · 검토 후 채택) — 강조 효과는 「영역 없음」과 같지만(제니퍼 알람은 원래 was_mw 발생) W4 인계 문구에 받을 사람이 생긴다. 근거 문구 「외부 연계 — 상대 시스템 확인 필요」 고정 · 애플리케이션 영역을 열면 옮긴다 |
| Q13 | 담당 기능의 상태(지정률·매핑 깨짐)를 어디서 보나? | **관리자 「사용자 관리」 탭 「담당 업무 현황」 카드 + 탭 배지**(§3.7 · 별도 대시보드 없음 · 로그만으로는 안 봄) |

---

## 13. 범위 밖 (후속 트랙)

- **영역 확장**: 네트워크·스토리지·백업 — 데이터가 서버 측 단면뿐이거나(네트워크·스토리지) 없다(백업). 영역만 먼저 열면 빈 화면이 된다. 데이터 편입과 함께 연다.
- **DB 데이터 보강**: ① `middleware_signatures.yaml`에 DB 엔진 시그니처(DB2·Oracle·PostgreSQL·MariaDB 프로세스) — **이 계획 안으로 당김**(W3 원인 단서 ② · W4 `other_process` 판정의 선행 · W3 착수 전 추가) ② DB 내부 지표 소스 편입(DPM 등) — 장기 · 범위 밖. DPM은 **DB 서버 식별 근거**도 될 수 있다 — 지금은 실시간 프로세스에 DB 엔진이 보이는지가 유일한 (대략적) 근거라 「ㅇㅇ존 DB 서버의 …」 질의를 답할 수 없다(부록 A DB 3·4 제외 사유).
- **애플리케이션 영역**: 외부 연계(Q12)는 애플리케이션 담당이 생기면 그 영역으로 옮긴다.
- **R&R 기반 통보 라우팅**: 서버·영역별 책임 주체(은행/통합MSP)는 이 시스템이 정의하지 않는다. ITAM `tcdmsif80.담당부점명`·폴스타 `cmm_alarm_def_noti_*` 실측 뒤 별도 계획.
- **담당 주체 표시**: 알람·조사 화면에 ITAM 담당 부점 표시 — 별도 계획.

---

## 14. 함께 검토한 제안의 처리 (2026-10-30)

| # | 제안 | 처리 |
|---|---|---|
| P1 | 사용 측정(담당별 클릭·전송·열람 집계) | **후순위** — 사용자: 불필요한 정보 수집 · 나중에 |
| P2 | 상황으로 시작하는 첫 화면 | **편입** → W2 §4.0 |
| P3 | 알람 카드 인지 → 분석 → 조치 | **편입** → W5 §7.4(재배치 · 덧붙이지 않음) |
| P4 | 과거 사례 따라 하기 | **편입** → W7 §8.1(매뉴얼 사례 장만 · 연습 모드 제외 · 로컬 샌드박스 재현) |
| P5 | 담당 24시간 요약 | **편입** → W6 §7A(화면 카드만 · worKB 담당별 분리 제외) |
| P6 | 결과 피드백 버튼 | **제외** — 이미 있다: 알람 「이 알람이 유용했나요?」(Plan 52 E4) · 조사 「실제 원인」 라벨(D-350 ② · 자동 조사는 관리자만) · 알려진 문제 3버튼(198 W8). 빈 곳은 질의응답 답변 피드백뿐 |

---

## 부록 A. 담당별 예시 질의 초안 (W2에서 검증 · 확정)

> `verified_by` 확정 전 초안이다. 골드셋 동형·실행 확인을 통과한 것만 싣는다.

**서버/OS**
1. ㅇㅇ존 서버 중 지금 CPU 사용률이 80% 이상인 서버를 보여줘
2. ㅇㅇ존 서버 중 파일시스템 사용률이 90% 이상인 서버와 파일시스템을 보여줘
3. ㅇㅇ존에서 지난달 대비 메모리 사용률이 10%p 이상 오른 서버를 보여줘
4. ㅇㅇ존 서버의 최근 24시간 알람 발생 건수 상위 10대를 보여줘
5. ㅇㅇ존 서버 중 지난 1개월 CPU 평균 사용률 상위 10대를 보여줘

**WAS/미들웨어**
1. 지금 응답시간이 가장 나쁜 WAS 인스턴스 5개를 보여줘
2. 최근 1시간 에러가 가장 많은 WAS 인스턴스를 보여줘
3. 힙 사용률이 높은 WAS 인스턴스를 보여줘
4. 어제 응답시간이 가장 나빴던 업무(서비스)를 보여줘
5. ㅇㅇ존에서 실행 중인 WAS·웹서버 프로세스가 있는 서버 목록을 보여줘

**DB** (DB 서버 자원 · 앱이 본 SQL — DB 내부 지표 미수집)
1. 최근 1시간 SQL 응답 지연 이벤트가 난 WAS 인스턴스를 보여줘
2. 지금 실행 중인 SQL이 오래 걸리는 요청을 보여줘

> ~~3. ㅇㅇ존 DB 서버의 지난 1개월 CPU·메모리 사용률~~ · ~~4. ㅇㅇ존 DB 서버의 파일시스템 사용률~~ — **제외(v1.2)**: 「DB 서버」를 식별할 근거가 없다(DB 자원 유형 0 · 엔진형 자원 등록 0 · 존 전체 실시간 프로세스를 훑는 질의 경로 없음). DPM 연동 때 재검토(§13). DB 영역 예시는 APM 기반 2개만 둔다.
