# 187. 알람 유형별 자동 조사 플레이북 — PAGE 알람마다 정해진 조사 단계를 결정적으로 일괄 수집하고, 단계별 LLM 소견과 종합 원인·대응 의견을 운영자에게 보인다

> **작성일**: 2026-10-17 · **v1.0** · **v1.1**(같은 날 — 사용자 지시 *"문헌 조사를 인터넷으로 접속하여 직접 다시 조사하여 계획서에 반영하라"*: 제니퍼 공식 백서 2·사례 4 원문 확보(이미지 PDF는 OCR) · 출처 검증 수준 재표기 · Datadog 3상태 서술 정정 · 결정적 판독 규칙 §4.2 신설 · S0 범위 비교 · S3 커넥션 획득 분리 · S2 WAS/호스트 CPU 대조) · **v1.2**(같은 날 — 사용자 지시 *"장애 대응에 대한 지식 저장소나 Troubleshooting Guide, 런북/플레이북, 인시던트 대응 매뉴얼, 알려진 문제 및 우회 방법 등을 자세히 조사하여 계획을 업데이트하라"*: 지식 유형 문헌 §3.7 신설 · **장애 대응 지식 저장소(KB) 설계 §5.7** — 런북 블록·알려진 오류(KEDB, KCS 구조·상태) 결정적 일치·우회 표시·매뉴얼 RAG 참고·초안→승인 수명주기 · 요건 R-14~R-17 · Wave W8~W9 · 수용 기준 8~10 · 게이트 G-13~G-17)
> **상태**: **WIP · v3.1 W22 조사 시간 사슬 상향·정합(§5.6.6 — 기본값 상향 · 기동 시 사슬 정합 경고 · 결손 스텝 1회 재시도) 구현 완료(2026-10-23 · 미커밋)** · v3.0 W21 수집 타임아웃 개선(§5.6.5 — P1·P3~P6 적용 · P2 미적용 · P0·P7 내부망) 구현 완료(2026-10-23 · 미커밋)** · v2.9 W20 장애 진단 「조사 흐름」(§5.6.4 · G-47~G-50) 구현 완료(2026-10-22 · 미커밋 · 두 플래그 아래) · 잔여 작업 일람은 §10.1(남은 13건 — W22 X-View 도구 상한·조사 흐름 넘침 표기 추가 — 내부망·SME·커밋 · R-E 임계 보정 판단 · 2026-10-22 R-D 완화·G-26 공용·W12 2단·2차 가림·실프로세스 종단 처리 · v2.4 W19 관제 → 기존 포털 장애 진단 탭(G-46) · W19 실화면 확인·매뉴얼 캡처 U-63·U-70 처리)** · **v2.0 W13~W15(장애 진단 탭 · 실행 기록 · SSE) 구현 완료(2026-10-19 · 미커밋 · 두 플래그 기본 off) · 잔여: 사용자 경로 2차 마스킹 실효(사용자 결정) · 실프로세스 종단 1회 · 매뉴얼 캡처 · 내부망 실 출력 가림 표본** · **W0~W6·W8~W12 구현 완료(2026-10-19 · 미커밋) · 잔여: W7 내부망 shadow · R-D(게이트웨이 acquire/execute 구조화 필드 부재 → `db_split` n/a · 수용 기준 2·2′·9 부분) · G-26 HolmesGPT 도구 경로 캐시 공용 미구현 · 수용 기준 6(120s)·7 내부망 측정 · SME 자료(G-10) · 매뉴얼 캡처** · 모든 신규 플래그 기본 off(비트 동일 검증) · 검증·보안 감사 지적 교정 완료 · (구: TODO — 계획(코드 0건)) · **게이트 G-1~G-17 확정(2026-10-17 · 권고안 전부 · 사용자 「게이트 권고안대로 확정」)** · **G-18~G-21 확정(같은 날 · 권고안 · 사용자 「권고한데로 반영하라」)** · **G-22~G-27 확정(같은 날 · 권고안 · 사용자 「권고대로 정리하라」)** · **G-28~G-33 확정(같은 날 · 권고안 · 사용자 「권고안대로 확정하여 계획서에 반영하라」)** — 게이트 전건 확정 · **D-341 확정**
> **v1.3**(같은 날): 게이트 확정 반영 · §5.6 화면 설계 상세화(요약형 카드·드로어 + 조사 상세 화면 + 관리자 「조사 지식」 탭) · W5·W9 산출물 보강 · G-18 신설
> **v2.0**(2026-10-19 · 검토안 → 같은 날 인터뷰로 확정): 사용자 제기 *"holmesgpt나 playbook 기반 조사시 시간이 오래 걸리고 다수의 조사가 진행되어 사용자가 진행상황을 못보고 멈춰있는 것으로 보인다. 알람이나 조사를 눌렀을경우 별도의 장애 진단 탭(이벤트 알람 탭 옆에)을 열어서 현재 진행중인 명령어나 api등을 보여주고 중간 단계을 조회내용도 볼 수 있도록 하라. 진행중에 새로운 이벤트를 조사하면 새로운 탭이 열리면서 조사가 진행되도록 하라."* → 실측(§5.6.3 ⓐ — 「멈춘 것처럼」 보이는 원인 5건: 화면 재조회 300초 상한 · 동시 상한 대기 비표시 · HolmesGPT 진행 무노출 · 플레이북 스텝 안 도구 호출 무노출 · 진행 중 조사 목록 없음) → §5.6.3 신설(「장애 진단」 뷰 탭 · 조사별 하위 탭 · 실행 기록(도구·명령 한 줄 · 상태 · 소요) · 중간 결과 · 대기 순번) · R-20~R-22 · W13~W15 · 수용 기준 14~20 · G-34~G-40 검토안 → **같은 날 인터뷰로 G-34~G-42 확정**(§9 — 권고와 다른 선택: G-36 발췌도 사용자에게·가림 후 모두 · G-38 AI 메모 관리자 표시 · G-39 SSE · G-40 상한 없음 · 추가 G-41 목록 범위 · G-42 상세 푸시 범위) → D-341 부기 4 · D-325 부기(② SSE 도입 · ③ 발췌 노출)
> **v1.9**(같은 날): 사용자 제기 *"사용자는 이벤트 알람을 보거나 봤던 것을 찾아 알람에 대해 다시 분석을 진행할 수 있어야 한다. 알람이나 현상에 대해 사용자가 질의하면 플레이북이나 HolmesGPT로 조사를 해야 한다"* → 검토(현행: 지난 알람 검색은 노이즈 콘솔만 · 재분석 경로 없음 · 채팅 질의는 기본 off 동기 `fault_diagnosis`만) → 권고안 확정. §5.6.2 신설(「이 알람 분석」 버튼 · 채팅 「지난 알람」 검색 · 증상 질의 결정적 라우팅 · 비동기 조사 카드) · R-18·R-19 · §5.0 잡 경로 2행 · W11·W12 · 수용 기준 11~13 · G-28~G-33 · G-9 수동 재실행 부분 개정(D-325 ⑩ 후속 계획 = 이 계획) · D-341 부기 3 ㉑~㉖
> **v1.8**(같은 날): G-22~G-27 권고안 확정 · §5.0.1 확정 표기 · W2(도구 캐시)·W4(엔진 모드·병렬)·W5(비교 화면·선택 기록)·W7(shadow = parallel) 반영 · G-27 문구 명확화 · D-341 부기 ⑮~⑳
> **v1.7**(같은 날 · 검토안): 사용자 제기 *"플레이북 엔진과 HolmesGPT를 동시에 돌리고 운영자가 둘중에 적정한 것을 보고 판단"* → §5.0.1 병렬 비교 모드(`SRE_DEEP_DIVE_MODE=parallel` · 정적 지시만 공유 · 잡 단위 도구 캐시 · 같은 틀 나란히 비교 + 결정적 일치 표시 · 운영자 선택 기록) · G-22 권고 변경 · G-26·G-27 신설
> **v1.6**(같은 날): §5.0 역할 분담 신설 — 187 플레이북 엔진 vs HolmesGPT 엔진 · 구성 요소별 책임표 · 잡 종류별 실행 경로 · 심층 실행 조건 · 기존 부품(사전수집·`PLAYBOOK_NOTES`·severity_judge·브리핑 6요소) 처리 · G-22~G-25 신설(대기)
> **v1.5**(같은 날): G-18~G-21 권고안 확정 · G-21 세부 3항(보충 한 줄 허용 · admin_only 유지 · `prior_findings` 가산) 확정 · W10 신설 · D-341 부기
> **v1.4**(같은 날 · 검토안): 사용자 제기 *"조사 상세화면에서 … 질의 응답 화면에서 새 대화로 열어주는건 어떨지 검토"* → §5.6.1 「조사 대화」 — 상세를 새 대화의 첫 고정 카드로 일원화하는 G-18 (d)를 권고 · G-19~G-21 신설(문맥 승계 · 브리핑 질문 · sre_agent 추가 조사)
> **번호**: 187(구 182 · 2026-10-19 `origin/main` 병합 시 원격 `plans/182~186`과 충돌해 재채번) — 작성 당시 형제 체크아웃 `collectorinfra`가 166~181을 쓰고 있어 182로 잡았었다.
> **요청 원문(사용자 2026-10-17)**: *"page 이벤트 알람이 발생했을때 이를 자동으로 분석하는 기능을 추가하려고 한다. 예를 들어 'A1' WAS 인스턴스 지연 알람이 발생하면 이 인스턴스가 구동하고 있는 서버의 호스트 네임을 조사하고 해당 호스트의 지연이 발생되고 있는 제니퍼 인스턴스를 찾아 해당 인스턴스의 p96응답시간, TPS, 에러율, 엑티브 서비스 수를 보여주고 이상이 있는지 없는지를 LLM을 통해 분석하여 의견을 제시한다. 이후 이 인스턴스의 힙 사용량, GC시간비중, 프로세스의 CPU, DB 컨녁션 풀 사용량 등을 확인하고 이를 LLM이 분석하여 운영자에게 의견일 제공한다. 또한 인스턴스의 트랜젝션 상위 5건을 SQL, 외부 호출 시간을 나워서 보여주고 이를 LLM이 분석하여 의견을 제공한다. 또한 폴스타를 통해 해당 호스트의 CPU, 메모리, 파일 시스템 사용률을 시간대 별로 조회하여 분석한 내용을 운영자에게 보여준다. 또한 폴스타의 제니퍼의 알람이 발생한 시간 전후로 폴스타의 연관있는 알람이 있는지 조회하여 분석하고 의견을 운영자에게 제공한다. 또 폴스트에서 프로세스를 조회하여 CPU 사용량이 많은 프로세스 리스트 5~10개정도를 조회하여 보여준다. 최종적으로 앞서 조회한 정보를 종합하여 알람의 원인과 대응 방법을 종합적으로 분석하여 운영자에게 보여주고 어떻게 대응해야 되는지의 의견을 제시해야 한다. … SMS, APM, DPM(향후 연동), ITSM 등을 자동으로 조사하고 조사한 정보를 이용하여 종합적인 분석 내용을 사용자에게 보여줘야 한다. 이러한 어떤 알람이 발생했을때 어떤 정보를 조사해야 되는지를 장애 대응, 인프라 운영 메뉴얼, 장비 메뉴얼, 논문, 문헌, 도서 등을 검색하여 알람별 조회해야 되는 정보들을 정리하고 이 정리된 자료를 기반으로 알람에 따른 조사와 대응을 일괄로 진행할 수 있도록 기능을 구현할 계획 파일을 정리하라."*
> **용어**: SMS = 폴스타(`mcp_server/` polestar_* 도구) · APM = 제니퍼(`apm_gateway/` apm_* 도구) · DPM = DB 성능 모니터링(미연동 · `plans/55`) · ITSM = 변경·장애 티켓 시스템(미연동 · 저장소 코드 0건)
> **관련 계획**: `plans/153`(조사 브리핑 저장·웹 표시·크로스소스 조사 — **이 계획의 표시·저장 기반**) · `plans/91`(1-5 kind별 플레이북 문구) · `plans/64`(이벤트 자동 조사 — 완료) · `plans/87`·`plans/134`(제니퍼 게이트웨이 도구) · `plans/155`(값 판정 = 게이트웨이 · 원인 = sre_diagnose) · `plans/144`(크로스소스 사건 상관) · `plans/162`(PAGE 필요성 — 트리거 모수) · `plans/154`(조사 품질 벤치 — 평가 재사용) · `plans/165` F3(제니퍼→폴스타 hostname 정확 일치 결함) · `plans/55`(DPM 로드맵) · `plans/101`(ML RCA — 범위 밖)
> **관련 결정**: **D-035**(결정적 = 판단 · LLM = 서술) · **D-118**·D-139(`sre_agent` 양방향 import 0 · MCP 계약만) · D-123(submit/poll) · D-124·D-137(트리거 배선 · 즉시통보+후속) · **D-195 ②·D-274 ⑤**(WAS 판정은 게이트웨이 domain 단일 정의 · 소비자 재구현 금지) · D-197(사전수집·지침) · D-317(크로스소스 사건) · **D-325**(조사 저장소·웹 브리핑) · **D-335**(값 판정 = 게이트웨이) · D-127·D-240(과금 게이트) · D-255(매뉴얼 동반) · D-303(회귀) · D-179(overfit 스캔 — 독스트링 리터럴)
> **D-번호**: **D-341**(구 D-338 · 2026-10-19 원격 D-338·D-339와 충돌해 재채번 · 2026-10-17 예약 → 같은 날 본문 등재 `docs/decisions/D-341.md`)
> **실측 기준**: `main` `384f97f`(2026-10-17). `file:line`은 이 시점 값이다 — W0에서 다시 대조한다.
> **계획 시작 SHA**: `99ec1ea4`(2026-10-19 · 구현 착수 · 회귀 `--base`)
> **회귀 대상 파일**: 131개 — 본체 `src/api/{investigation_poll.py,settings_catalog.py,routes/alarm.py,routes/admin_incident_kb.py,server.py,thread_history.py}` · `src/config/alarm.py` · `src/domain/{investigation_followup,kb_draft,symptom_router}.py` · `src/infrastructure/investigation_request_limit.py` · `src/nodes/fault_diagnosis.py` · `src/prompts/investigation_followup.py` · `src/static/{index.html,noise.html,admin/*.html,css/style.css,js/{app,briefing-view,noise,admin-incident-kb}.js,manual/*}` · `noise_gate/{application/nodes/{alarm_notifier,investigation_trigger,notification_gate}.py,infrastructure/{decision_store,investigation_store,incident_kb_store,sre_agent_client}.py}` · `config/{rag_collections.yaml,settings_help/noise_gate_investigation.yaml}` · `scripts/manual/**` · sre_agent `application/{briefing_builder,investigation_dispatcher,investigation_guidance,investigation_jobs,playbook_*}.py`·`domain/{playbook*,known_error,engine_agreement,source_adapter,remediation}.py`·`infrastructure/{playbook_loader,known_error_loader,tool_result_cache,mcp_tool_client}.py`·`interface/mcp_service.py`·`settings.py`·`config/{playbooks/*.yaml,known_errors/README.md}`·`scripts/{runbook_coverage.py,investigation_bench/*}` · 테스트(`tests/**/test_plan187_*` · `sre_agent/tests/test_plan187_*` · 픽스처) · (W22 가산) `apm_gateway/{apm_gateway/config.py,.env.example,tests/test_config.py}` · `src/api/routes/alarm_schemas.py` · `src/static/js/briefing-view.js` · 루트 `.env.example` · `tests/test_api/{test_alarm_capabilities,test_plan153_w3_briefing_view}.py` · `sre_agent/.env.example` · 목록 정본: `git status` 미커밋 변경
> **계획 끝 회귀(2026-10-19)**: `regress.py --base 99ec1ea4 --files <130>` — 본체 통과 4,314 · 원래 실패 6(TestProcessApiClient 2 · prometheus_client 부재 2 · settings_catalog `.env` 2) · sre_agent 통과 1,785 · 실패 0 · arch/overfit/config_fallback 통과 · ruff·mypy 이번 diff 줄 0 · 범위: 모듈 단위 — 전체 미실행 · `[전체 회귀 권고]` 허브 모듈 src.config.alarm 88% · 본체 레인은 plans/186 시각 의존 parametrize id의 xdist 수집 불일치로 직렬 실행(docs/18 2026-10-19)
> **전체 회귀(2026-10-19 · 사용자 요청)**: `regress.py --full --base 99ec1ea4` — 본체 통과 23,483 · 실패 55 · 에러 7 · 건너뜀 93(xdist -n 7 수집 정상) → 귀속 원래 실패 67 · 이번 변경 탓 1(`test_every_noise_key_has_section` — plans/187 `NOISE_INVESTIGATION_*` 13키 `SECTION_BY_KEY` 미등재 → `src/api/settings_catalog.py`에 「자동 장애 조사」로 등재, 재실행 통과) · mcp_server 통과 204 · 에러 6(prometheus_client·pymysql 부재) · apm_gateway 도구 TIMEOUT(2400초 — `test_plan134_w0b_verify.py::test_disconnect_before_promotion_cancels_job` 기준에서도 무한 대기) → 그 1건 제외 별도 실행 통과 1,628 · 실패 2(tool_list snapshot — 기준에서도 실패) · sre_agent(도구 미실행 — 자체 venv 없음) 타 체크아웃 venv로 통과 1,785 · 정적 게이트 통과 · 범위: 전체(사용자 요청)
> **origin/main 병합(2026-10-19 · plans/188 D-342·D-343과 통합)**: `_agent_getter`에 188 C1 재생성(툴셋 FAILED면 1회 새로 생성 · 잠금)을 합치고 `_default_diagnose_fn(settings, toolset_check, agent_getter)`로 두 배선 유지 · 플레이북 심층 결과도 188 A안·C7 기준(`tools_unavailable`·도구 호출 마크업)이면 서술 없이 `deep_dive.status=skipped`+사유(`_deep_skip_reason` · `sre_agent/tests/test_plan187_merge188_deep_skip.py`) · sre_agent 전체 2,059 통과
> **v2.0 구현(2026-10-19 · W13~W15 · 계획 시작 SHA `a4514020`)**: W13 sre_agent `domain/activity_log.py`(신규 · 링 버퍼 300건/256KB · `safe_append` best-effort)·`infrastructure/activity_masking.py`(신규 — `mask_secrets` 뒤 명령행·설정 비밀값 꼴 추가 가림)·`application/{investigation_dispatcher,investigation_jobs,playbook_steps,playbook_narrator}.py`·`diagnosis.py`(`_StreamDrain` 훅)·`infrastructure/tool_result_cache.py`·`interface/mcp_service.py`(`activity_since=-1`)·`settings.py`(`SRE_ACTIVITY_LOG_ENABLED`)·`.env.example` · W14 본체 `src/api/investigation_live_hub.py`(신규)·`investigation_poll.py`·`routes/alarm.py`(`/alarm/investigations/{active,stream}` · `activity_since`)·`server.py`·`src/config/alarm.py`(`NOISE_INVESTIGATION_LIVE_VIEW_ENABLED`·`_LIVE_POLL_SECONDS`)·`settings_catalog.py`·`config/settings_help/noise_gate_investigation.yaml`·`noise_gate/infrastructure/{sre_agent_client,investigation_activity_store}.py` · W15 `src/static/js/diagnosis-view.js`(신규)·`index.html`·`app.js`·`noise.js`·`briefing-view.js`(`runningText` 옵션)·`css/style.css`·`noise.html`·`admin/noise.html` · 매뉴얼 U-70·A-81 · 테스트 `sre_agent/tests/test_plan187_{w13_activity,v2_verify_activity}.py`·`tests/test_api/test_plan187_{w14_live_api,w14_live_hub,v2_verify_live}.py`·`test_ui_plan187_w15_diagnosis.py` + 고정값 갱신 6파일. **계획 끝 회귀**: `regress.py --base a4514020 --files <45>` rc=0 — 본체 통과 9,773·실패 0 · sre_agent 2,095·실패 0 · arch/overfit/config_fallback 통과 · ruff·mypy 이번 diff 줄 0 · 범위: 모듈 단위 — 전체 미실행 · `[전체 회귀 권고]` 허브 모듈 src.config.alarm 88% · `SreAgentClient.poll` 시그니처. 이후 검증 교정 라운드는 실패 테스트·모듈 선택분 + `--no-tests`만. **실측**: poll당 MCP 새 연결 21.9ms vs 재사용 5.9ms(로컬 가짜 서버) · 본체는 uvicorn 단일 워커 전제(`alarm_bus`와 같음 — 다중 워커면 폴러 ×워커). **이탈**: 비밀값 꼴은 `log_masking.py`(정본 `src/utils/log_masking.py`의 바이트 사본 4개 일치 테스트)가 아니라 신규 `activity_masking.py`에 둠 · 스트림 최대 수명 3600초·watch 200개 방어 상한 · 사용자 경로 `denied` 사유 `unavailable` 통합 · 노이즈 드로어는 접수 직후 자동 새 탭 대신 「장애 진단에서 보기」 버튼(팝업 차단). **잔여**: 사용자 경로 2차 마스킹(D-325 ③ `_investigation_mask_fn`)이 줄 전체 일치라 한 줄 JSON 안의 키·토큰은 못 가림(별 결정) · 실프로세스 종단 1회 · 매뉴얼 캡처 · `/active`·summary에 엔진 키는 이벤트를 본 조사만 · 종결 잡 발췌 TTL까지 유지(최악 약 128MB · 전형 43MB).
> **v2.1(2026-10-21 · W16 · 사용자 지시)**: *"노이즈 관제에서 바로 「조사 대화로 열기」 버튼이 언제든 보여야 된다 … 「조사 대화로 열기」를 누르면 「질의응답」, 「이벤트 알림」 옆에 같은 레벨의 탭이 열리는 방식이어야 하는데 새로운 페이지가 열린다. 수정하라."* → **실측**: 관제 드로어의 버튼은 ① 플레이북 브리핑 요약형에만 있고(종전 브리핑 0) ② 장애 진단이 켜지면(`liveViewOn`) 감춰졌으며 ③ 누르면 `/?investigation=` 새 브라우저 탭 — 그 탭에서도 장애 진단이 켜져 있으면 조사 대화가 아니라 진단 탭으로 갔다. **변경(G-43)**: §5.6.1 1 진입 개정 — 조사 대화는 본 화면의 **「조사 대화」 뷰 탭**(질의응답·이벤트 알람·장애 진단과 같은 줄 · × 닫기)으로 연다. 두 탭은 대화 화면을 함께 쓰고 탭을 옮길 때 대화(말풍선·thread·범위 칩·고정 카드·입력 초안)를 바꿔 끼운다 — 질의응답 대화는 그대로 남는다 · 스트리밍 중 전환 거부 · 이력 열기·새 대화는 질의응답 탭. 노이즈 관제 ⓒ 머리에 「조사 대화로 열기」를 **조사 id가 있으면 늘**(브리핑 종류·진행 상태·장애 진단 켜짐 무관 · break-glass 제외) 달고, 누르면 열려 있는 포털 창(`window.name=kbaiops-portal` · 머리글 노이즈 관제 링크 `rel="opener"`)을 찾아 그 창의 「조사 대화」 탭을 연다 — 못 찾으면 새 탭 `/?conversation=<id>`(진단 켜짐과 무관하게 조사 대화). 「장애 진단에서 보기」(새 탭 `/?diagnosis=`)·알람 카드 요약형 버튼 숨김(진단 켜짐 시)은 그대로. 산출물: `src/static/{index.html,css/style.css,js/app.js,js/noise.js,noise.html,admin/noise.html}`(캐시 버전 app 32·style 33·noise 17) · 테스트 `tests/test_api/test_ui_plan187_w16_conversation_tab.py`(node 실행 6건) + 고정값 갱신 5파일 · 매뉴얼 U-63. **미확인**: 실브라우저 창 이름 재사용(확장 미연결로 실측 못 함 — 브라우저가 다른 창 묶음으로 열면 새 탭 폴백).

> **v2.2(2026-10-21 · W17 · 사용자 지시)**: *"조사페이지에서 알람의 내용이 없어 어떤 알람으로 조사하고 있는지가 보이지 않는다. 그리고 조사하는 프로세스는 무엇때문에 어떤것을 조사하는지에 대한 정보도 같이 보여줘야한다."* → **실측**: 「장애 진단」 하위 탭 머리는 조사 대상(`subject`) 또는 조사 id와 종류·엔진·진척뿐이고 알람명·서버·심각도가 없었다. 실행 기록은 「S2 동작명」처럼 무엇을 했는지만 있고 왜(플레이북을 고른 근거 · 스텝이 보는 것 · HolmesGPT로 간 이유)가 없었다. **변경(G-44)**: ① 본체 조회 응답(실시간 화면 켜짐 + `activity_since` 조회에만)에 판정 기록 요약 `alarm`(알람명·서버·자원·발생 시각·판정·심각도 · 조건 로그는 운영자만 · 사용자 경로 마스킹)을 붙인다. ② sre_agent 실행 기록 이벤트에 선택 키 `purpose` — 플레이북 선택(`started`·playbook: 「플레이북 X 선택」 + 일치 조건/fallback 사유) · 스텝 시작·끝(`narrate.focus`) · HolmesGPT 시작(조사 이유 · 플레이북 미선택 사유 · 심층 모드 의미). 허브 사용자 키·보관 요약 키에 `purpose`(마스킹·상한 대상). ③ 화면 머리에 「알람 / 조건(운영자) / 대상 / 조사 이유」 줄(`dv-target`), 탭 이름은 「서버 · 알람명」, 스텝 행 아래 「목적: …」, 도구 행은 마우스 올림 설명. 판정 기록이 없으면 「알람 정보를 찾지 못했습니다」. 플래그 off = 비트 동일. 산출물: `sre_agent/sre_agent/{domain/activity_log.py,application/investigation_dispatcher.py}` · `src/api/{routes/alarm.py,investigation_live_hub.py}` · `noise_gate/infrastructure/investigation_activity_store.py` · `src/static/{js/diagnosis-view.js,css/style.css,index.html}`(캐시 diagnosis-view 3·style 34) · 테스트 `sre_agent/tests/test_plan187_w17_purpose.py`·`tests/test_api/test_plan187_w17_alarm_brief.py`·`tests/test_api/test_ui_plan187_w17_diagnosis_head.py` · 매뉴얼 U-70.

> **v2.3(2026-10-21 · W18 · 사용자 지시)**: *"조사 탭에서 시간, 알람, 서버 등의 정보로 이벤트에 대해 검색하여 바로 조사를 진행할 수 있는 기능도 추가하라."* → **변경(G-45)**: 「장애 진단」 탭의 「진행 중 목록」 위에 **「이벤트 찾아 조사」** 칸 — 시작·끝 시각(판정 시각 기준 · 비우면 최근 24시간 · 보존 30일)·알람명·서버(부분일치)로 판정 기록을 찾고(최근 30건), 행의 **[조사 시작]**이 §5.6.2 ① 「분석 요청」과 **같은 접수 라우트**(`POST /alarm/{alarm_id}/investigations` — 존 검사 fail-closed · 상한 공유 · 10분 합류 · 통보 없음)로 접수한 뒤 그 조사의 하위 탭을 연다. 검색은 G-29처럼 **새 API 없이** `/noise/decisions` 사용자 경로를 쓰고, 칸을 나눠 거르도록 선택 쿼리 `alarm_q`(알람명)·`server_q`(서버명·hostname) 부분일치를 가산한다(미지정 = 종전과 같음). 칸은 `/active` 응답 `event_search`(= `NOISE_INVESTIGATION_MANUAL_ENABLED`)가 true일 때만 보인다 — 새 플래그 없이 기존 두 옵트인(실시간 화면·다시 분석)이 모두 켜졌을 때만 동작한다. 진단 화면 파일에는 여전히 쓰기 요청이 없다(접수 함수는 app.js가 주입 · D-003 정적 테스트 유지). 실패·거부·합류 사유는 그 행에 보인다. 산출물: `noise_gate/infrastructure/decision_store.py`(`list_decisions` 인자 2) · `src/api/routes/{noise_dashboard.py,alarm.py}` · `src/static/{js/diagnosis-view.js,js/app.js,css/style.css,index.html}`(캐시 diagnosis-view 4·app 33·style 35) · 테스트 `tests/test_api/test_ui_plan187_w18_event_search.py`·`tests/test_api/test_plan187_w18_event_search_api.py` · 매뉴얼 U-70. **실브라우저 확인(2026-10-21 · Playwright headless Chromium · 별 포트 8765 · 인증 off)**: 검색 칸 표시 · 서버/알람명/시간 구간 필터 · 시작>끝 안내 · 조사 서비스 미가동 시 행 안의 실패 사유 · (목 응답으로) 하위 탭 「서버 · 알람명」·머리 줄·「목적:」 확인 · 버튼·날짜 입력 기본 모양과 없는 토큰 `--border-color`를 교정(`--border` · style 캐시 35 유지). 미확인은 §10.1 8·10.

> **v2.4(2026-10-22 · W19 · 사용자 지시)**: *"웹 UI가 아직도 조사대화로 열기하면 질의응답페이지로 넘어가는 경우도 있고, 아직도 새로운 창으로 열린다. 노이즈관제화면과 질의응답화면이 달라서 새로운 창으로 열리는데 기존 창에 장애 진단 탭에서 열리도록 수정되어야 한다."* + 사용자 결정 「장애 진단 탭(권장)」(관제의 「조사 대화로 열기」도 장애 진단 탭으로). → **실측**: 관제 드로어 ⓒ 머리에 버튼이 2개였다 — ① 「장애 진단에서 보기」는 **늘** `window.open("/?diagnosis=", "_blank")` 새 브라우저 탭(G-34) ② 「조사 대화로 열기」는 이름 창 `window.open("", "kbaiops-portal")`로 포털을 찾는데, 관제를 포털 머리글 링크가 아니라 주소·즐겨찾기·관리자 화면(`/static/admin/noise.html`)으로 열면 이름 창을 못 찾아 **빈 탭을 새로 연 뒤** `/?conversation=`으로 보냈다(W16 「미확인」이 이 경우) ③ 찾은 경우에도 「조사 대화」 탭은 대화 화면을 질의응답과 함께 써서 질의응답 페이지로 넘어간 것처럼 보였다. **변경(G-46)**: 관제 ⓒ 머리의 진입 버튼을 **1개**로 합친다 — 장애 진단 켜짐이면 「장애 진단에서 보기」, 꺼짐이면 「조사 대화로 열기」(이름만 다름). 누르면 **열려 있는 포털 창**에 연다 — 포털은 `openInvestigationEntry`로 장애 진단 탭의 그 조사 하위 탭(꺼져 있으면 조사 대화 탭)을 연다. 포털 찾기 순서: ① `window.opener`(머리글 링크 `rel="opener"`) 직접 호출 ② `BroadcastChannel("kbaiops-portal")` 묻기→답→지정→결과(포털 탭이 여럿이면 마지막으로 본 탭 하나 · 단계마다 400ms) ③ 열린 포털이 없을 때만 새 탭 `/?diagnosis=<id>`(꺼져 있으면 포털이 조사 대화로 연다). 이름 창 탐색·`window.name`은 폐기. 관제 서랍에 결과 안내(「열려 있는 포털 창에 열었습니다 — 그 창으로 옮겨 확인하세요」 · 거부 시 「포털 창이 열지 못했습니다」) — 브라우저가 다른 탭으로 포커스를 못 옮길 수 있어서다. break-glass 토큰은 장애 진단이 켜져 있을 때만 버튼(종전과 같음). `/?conversation=` 쿼리 진입은 하위 호환으로 남긴다. 산출물: `src/static/js/{noise.js,app.js}` · `index.html` 주석 · 캐시 app 34·noise 18(`noise.html`·`admin/noise.html`) · 테스트 `tests/test_api/test_ui_plan187_w16_conversation_tab.py`(포털 node 실행 5건 교체) + 고정값 갱신 4파일 · 매뉴얼 U-63·U-70·A-81 문구. **실브라우저 확인(2026-10-22 · Playwright headless Chromium · 실제 함수 본문을 뽑은 최소 페이지 2개)**: 관계없는 탭의 관제 → 채널로 기존 포털(창 2→2) · 포털 2개 → 1개만 열림(창 3→3) · 머리글 링크로 연 관제 → opener(창 2→2) · 포털 없음 → 새 탭 `/?diagnosis=inv-9`. 같은 날 후속으로 본체 실화면 확인(켜짐 3 진입 · 꺼짐 2 진입 · 포털 없음)과 매뉴얼 캡처 U-63·U-70 처리 — §10.1 「처리함」 9·16.
> **v2.5(2026-10-22 · 검토안 · 사용자 지시)**: *"장애 진단 페이지에서 실행기록, 조사결과 등이 나오는데 그 시간의 로그만 나와 뭘하고 있는지가 보이지 않는다. … 운영자가 봤을때 이해할 수 있는 내용이 표시되어야 한다. … 이 요건에 맞게 계획파일을 정리하라."* → 실측(§5.6.4 ⓐ — 원인 8건: 기계어 한 줄 · 단계 결과 문장 없음 · 해석은 종결 뒤 상한 안 일부 단계만 · 건너뛴 단계 미표시 · 단계 연결 없음 · 조회/결과 두 칸 분리 · HolmesGPT 사용자 노출 0 · host.cpu에 WAS 프로세스 CPU·실행 중 요청·실행 중 SQL 단계 없음(R-E 판단 보류)) → **§5.6.4 신설**(단계 카드 = 왜·조회·결과·해석 · 결정적 문장 4종 + LLM 해석 · 지금까지 줄 · 플레이북 사슬 보강) · W20 · 수용 기준 21~27 · 위험 3건 · **G-47~G-50 확정 대기** · §10.1 17 · D-341 부기 11
> **v2.6(2026-10-22 · 검토 · 사용자 지시)**: *"문장을 만드는 것도 AI를 이용하는 것에 대해 검토하라."* → §5.6.4 ⓕ 신설(문장 종류별 AI 적합성 표 · 기존 소견기·가드·상한 실측) → **혼합안 권고**(사실 문장은 문형으로 즉시 · AI는 단계 끝 즉시 「결과 요약 + 해석」 — 소견 호출을 앞당겨 호출 수 무증가 · 실패 시 문형 문장 유지) · 전면 AI안 비권고 사유 5 · G-47 개정 · 수용 28 · 정교 문형 대상을 8종으로 축소
> **v2.7(2026-10-22 · 확정 · 사용자 결정)**: *"g-47은 단계가 끝날때 마다 작성하고 주의 사항을 명기하라. g48은 사용자에게도 보여준다. g49는 권고, g50은 다른 선택지"* → **G-47 단계 끝마다 AI 해석 + 주의 사항 명기**(§5.6.4 ⓖ — 계획서·화면 양쪽) · **G-48 사용자에게도 AI 판단 표시**(G-38 개정) · **G-49 권고안**(host.cpu·host.process 3단계 보강) · **G-50 「조사 흐름」·「실행 기록」 전환 탭**(현행 화면은 「실행 기록」 탭에 그대로) · 수용 15·28 개정 · 29 신설 · 위험 「AI 메모 오독」 개정
> **v2.8(2026-10-22 · 사용자 지시)**: *"호출수 상한은 왜 있는거냐?"* → 근거 설명(v1.0 [제안] 값 — was.latency 수집 7단계 + 종합에 맞춘 수 · FabriX 공유 용량·지연 우려 · 과금 아님) → *"호출수 상한을 없애라"* → **단계 소견 호출 상한 제거**(`NarratorLimits.max_step_calls` 기본 7 → None — 소견 대상 단계 전부 + 종합 1 · 명시하면 종전 절단) · 구현 완료(미커밋) · §5.4·수용 6·28·ⓕ·ⓖ C5·C8·G-47·§10.1 2 개정 · 지연은 단계당 타임아웃 20s·동시 4·전체 예산·시간당 예산 가드로만 관리
> **v2.9 W20 구현(2026-10-22 · 세션 시작 `394cc4b1` · 미커밋)**: **a** sre_agent `domain/step_findings.py`(신규 — 결과 한 줄 정교 문형 8종 + 일반 문형 · `tool_phrase` 사전 · `when`/건너뜀 문장 · 판정 불가 실행은 「판정할 수 없어(사유) 확인을 위해 실행」 · `narrate.why`는 조건 참일 때만) · `domain/{activity_log,playbook}.py`(`finding`·`because`·`phrase` · kind `step_skip` · `narrate.why`) · `application/{playbook_runner,playbook_narrator,playbook_prompts,investigation_dispatcher,playbook_steps}.py`(`step_skip` · 단계 끝 해석 `StepOpinionStream` — 별 실행기 · 그 시점까지 끝난 단계 판독만 · `prior_findings` 줄 때만 「앞 단계 결과」·추측·순위 금지 지시(없으면 프롬프트 비트 동일) · finalize 재호출 0 · partial `finding`/`opinion{status,text,reason,basis}`/건너뜀 항목 · 활동 기록 off면 종전 경로) · **b** host.cpu·host.process `version: 2`(S2 `apm.runtime_and_pool` `for_each: host_was` when `S0r.status == 'ok' and S0r.verdict != 'no_was'` · S3a `apm.active_services` `target: was_cpu_top` when `S2.status == 'ok' and cpu_owner in [...]` · S3b 신규 `apm.active_sql` — `apm_active_detail` · S3a CPU 시간 상위 3 · 리터럴 `?`(이스케이프·큰따옴표·`$$`·16진·지수·음수) → `mask_secrets` → 1KB · 발췌도 재가림) · `RunScope.host_was`(정상 WAS 포함 · `to_dict` 제외 — S5·S7 대상 비트 동일) · S1을 S0r 바로 뒤로(R-E TPS 근거) · host.process에 S4(cpu)·S6 추가 · `plan_stages` 수정 없이 `[S4,S6] [S0r] [S1,S2] [S3a] [S3b,S5,S7]` · **c** 본체 `src/api/investigation_live_hub.py`(사용자 경로 새 키 3종·`note` 가림 · HolmesGPT `ai_note` 사용자 허용(G-48) · 플레이북 소견 초안 `ai_note`는 사용자 경로 차단 — 가드 전 문장) · `noise_gate/infrastructure/investigation_activity_store.py`(`SUMMARY_EVENT_KEYS` +3 · `ai_note` 제외 유지) · `src/static/js/diagnosis-view.js`(「조사 흐름」·「실행 기록」 전환 탭 · 카드 순서 = 플레이북 순서 · 지금까지 줄(이상 판정 + 판정 없는 1행 이상 · 최근 4) · 건너뜀 카드(조건식 원문 대신 문장) · ⓘ C1~C8 · 「종합과 다름」 칩(1위 가설 evidence_steps · supported/refuted vs degraded/normal) · HolmesGPT 회차 카드) · css · 캐시 v(diagnosis-view 6·style 36) · 매뉴얼 U-70·A-81 · 캡처 `diagnosis-tab`(픽스처 `inv-manual-flow`) · **테스트** sre_agent 새 4파일(w20a_findings·w20a_flow·w20b_playbook_chain·w20_verify — 종단 픽스처 `w20_flow_hostcpu.json`) · 본체 새 2파일(`test_ui_plan187_w20_flow.py`·`test_plan187_w20_verify.py`) + 기존 9파일 갱신 · **회귀** `regress.py --base 394cc4b1 --files <36>` — 본체 통과 1,159 · sre_agent 통과 2,234 · 실패 0 · arch/overfit/config_fallback 통과 · ruff·mypy 이번 diff 줄 0 · 범위: 모듈 단위 — 전체 미실행 · **MLX 1회**(단계 끝 프롬프트 대표 2단계): 1건 가드 통과 · 1건 극성 가드 탈락(다중 인스턴스 「정상 범위」 언급 — 종전 가드 동작 · 「AI 해석 없음 — 사유」 경로 확인) · **잔여**: R-E 임계(`cpu_gap_pct`)로 계획 ⓑ 예시 수치(호스트 96 · A1 74/78)는 `other_process` → S3a·S3b 생략(테스트 strict xfail 2로 고정 — 임계·규칙 변경은 결정 필요) · WAS 정상이면 S1 생략 → TPS 결손 → `cpu_owner` 판정 불가로 S3a 실행(정상 경로) · 내부망 FabriX 단계 끝 지연·실 SQL 가림 표본(§10.1 2·7)
> **v3.0(2026-10-22 · W21 · 사용자 지시)**: 내부망 was.latency 조사에서 *"대부분이 타임아웃이고 제대로된 분석을 못하고 있다. 개선 방안을 검토하라"* → 원인 5건 실측·권고(§5.6.5) → *"권고에 맞게 계획서에 기록하고 구현하라"* → **W21 구현(2026-10-23 · 미커밋)**: P1 배치 동시 호출(기본 4) · P3 스텝별 `timeout_s`(S0·S0r 60) · P4 타임아웃 진행 사유·중단 · P5 소견 타임아웃 설정(45s) · P6 APM 소스 → 폴스타 매핑 · **P2 미적용**(S0+S1 합계 증가 — 사유 §5.6.5 ⓓ) · P0·P7 내부망 이월 · 수용 30~35 · D-341 부기 12 · 회귀 `regress.py --base 394cc4b1 --files <W21 22개>` 종료 0(교정 뒤 재실행 · 본체 925·sre_agent 2,276 통과 · 실패 0 · 모듈 단위 — 전체 미실행)
> **v3.2(2026-10-23 · W23 · 사용자 지시)**: 내부망 실화면 *"조사대화가 어떤 알람에 대한 조사인지 알 수가 없다"* · *"「조사 대화로 열기」를 눌러도 새로운 탭이 열리지 않는다"* · *"조사 대화 쪽에는 어떤 알람내용인지 보여야 어떻게 조사를 하고 있는지를 알 수 있다. 어떤 정보를 보여줘야 되는지 검토하여 UI를 업데이트하라"* · *"playwright을 이용하여 UI를 검증하여 조사대화가 적절히 동작하도록 수정하라"* → **W23 구현(미커밋)**: ① 조사 대화 고정 카드 머리 「조사(종류·초점) · 알람 · 판정 사유 · 조건(관리자) · 함께 조사하는 알람(최대 5 · 외 N건) · 대상·조사 이유 · 플레이북(id·버전·선택 근거·범위)」 — 조사 조회 `with_alarm=true`(첫 조회만 · 판정 기록 요약 + `related`) · 알람 요약 `stage_label`·`alarm_count` 가산 · 재조회마다 머리 다시 그림(플레이북 줄은 결과가 나오면 생김) ② 노이즈 관제 진입 버튼은 **항상 새 브라우저 탭**(켜짐 `/?diagnosis=` · 꺼짐 `/?conversation=`) — 열린 포털 창 재사용(opener·BroadcastChannel)은 다른 탭을 앞으로 못 가져와 「아무 일도 안 일어난 것」처럼 보여 폐기(사용자 선택 · W19 G-46 개정) · Playwright 실화면 검증(캡처 스냅샷 서버 18981 · 관리자) — D-341 부기 14.
> **v3.3(2026-10-23 · W24 · 사용자 지시)**: *"알람이 발생된 소스가 어디인지 정보가 있어야 한다. 은행존 제니퍼인지? 공동존 김포 폴스타인지?"* · *"CPU사용률이 높으면 어떤 것들을 조사해야 되는지에 대한 정보도 있어야 한다"* → **W24 구현(미커밋)**: ① 머리 「발생 소스」 — 판정 기록 `db_id`를 레지스트리 정본으로 풀어(`_alarm_source_text` · `zone_labels_for`·`source_labels_for`·`alarm_source` 재사용 — 신규 매핑 0) 「공동존 김포 폴스타 (polestar_cm_gp)」 · 「은행 제니퍼(은행존)」 · 단일 설정 「제니퍼(존 미지정)」 · 함께 조사하는 알람에도 소스 ② 머리 「조사 항목」 — 종결 브리핑(없으면 진행 중 `partial_briefing`)의 플레이북 단계 제목·상태 목록 + 진행 중 진척(`progress`) · 플레이북 없는 조사는 줄 없음(본체가 sre_agent 플레이북 YAML을 읽지 않는다 — D-139 경계) — D-341 부기 15.
> **v3.1(2026-10-23 · W22 · 사용자 지시)**: *"원인 분석을 위한 각종 타임아웃이 너무 짧아 끊기는 경우가 많다. 기본적인 타임아웃 시간을 조절하거나 대책을 마련하라."* + *"제니퍼 api 호출건은 20초말고 30초로 늘리자."* → **W22 구현(2026-10-23 · 미커밋)**: 시간 사슬 기본값 상향(제니퍼 API 30 · MCP 도구 1건 60 · 스텝 90 · S0/S0r 120 · 수집 240 · 소견 60 · 종합 90 설정화 · 조사 600 · 캐시 대기 = 조사 상한 · 본체 후속 poll 600) · sre_agent 기동 시 사슬 정합 경고 1줄 · 「스텝 타임아웃」 결손 스텝 1회 재시도(`SRE_PLAYBOOK_RETRY_TIMED_OUT_STEPS` 기본 on) — §5.6.6 · 수용 36~41 · D-341 부기 13.

---

## 0. 증거 규칙

- 과금 호출 0 · DB 호출 0 · 서버 기동 0 · 실 제니퍼 호출 0. §2는 저장소 읽기·grep으로 실측했다.
- §3 문헌 근거는 웹 직접 조사(2026-10-17 · v1.1 · §3.7은 v1.2)다. 검증 수준 표기([원문]·[원문·OCR]·[공식 문서]·[2차]·[제안]·[미확인])는 §3.0을 따른다.
- 「(추론)」 = 코드 읽기로 판단했으나 실행으로 확인하지 않음. 착수 Wave에서 테스트로 먼저 확정한다.

## 1. 결론

### 1.1 한 줄

PAGE 알람이 오면 지금은 sre_agent(HolmesGPT)가 **LLM이 고르는 도구 순서로** 자유 조사하고(kind별 플레이북은 지침 문구일 뿐), 브리핑은 한 덩어리 서술이다. 사용자가 요구한 것은 **알람 유형마다 정해진 조사 단계가 빠짐없이 같은 순서로 실행되고, 단계마다 데이터 표 + 소견, 끝에 종합 원인·대응 의견**이 나오는 것이다. 이 계획은 RCACopilot의 「알람 유형별 핸들러(결정적 수집 워크플로) → LLM 요약」 구조[원문]를 따라, ① 문헌 근거로 정리한 **알람 유형별 조사 카탈로그**(§4)를 ② 선언형 **플레이북 YAML**로 코드화하고 ③ sre_agent에 **플레이북 실행기**(결정적 대상 확정·일괄 수집·부분 실패 허용)와 ④ **단계별 소견기·종합기**(LLM은 서술만 · 판정은 게이트웨이·결정적 코드)를 더해 ⑤ `plans/153`의 조사 저장소·웹 브리핑 뷰에 **단계 섹션 브리핑**으로 보인다. 기존 HolmesGPT 자유 조사는 없애지 않고 플레이북 뒤의 선택적 「심층 조사」로 둔다(G-2).

### 1.2 요건 → 설계 대응

| # | 요건(사용자 예시 「A1 WAS 인스턴스 지연」) | 수집 도구(현행 존재) | 판정 주체 | 설계 절 |
|---|---|---|---|---|
| R-1 | 알람 인스턴스 → 구동 호스트명 | 알람 페이로드 hostname(게이트웨이 폴러 역해소 `poller.py:225`) → 없거나 신뢰도 낮으면 `apm_instance_map(query=인스턴스)` | 결정적(정합 신뢰도) | §5.3 S0 |
| R-2 | 그 호스트에서 **지연 중인** 제니퍼 인스턴스 찾기 | `apm_instance_map(hostname)` → 인스턴스별 `apm_app_health` | 게이트웨이 판정(`was_signals`) | §5.3 S0 |
| R-3 | p95 응답시간·TPS·에러율·액티브 서비스 + 이상 유무 소견 | `apm_app_health`(구간 p50/p95·에러율·TPS·액티브) + `apm_period_compare`(기준선) | 이상 유무 = 게이트웨이 판정 · LLM = 소견 | §5.3 S1 |
| R-4 | 힙·GC 시간 비중·프로세스 CPU·DB 커넥션 풀 + 소견 | `apm_runtime_health` · `apm_resource_pool`(현재값 전용) | 게이트웨이 판정(힙 압박·GC 지연·풀 고갈) | §5.3 S2 |
| R-5 | 트랜잭션 상위 5건을 SQL·외부 호출 시간으로 나눠 표시 + 소견 | `apm_slow_transactions(n=5)`(cpu·sql·fetch·external·network 분해) · 1위 건 `apm_transaction_profile`(예산 1) | 게이트웨이 판정(SQL 지연·외부 호출 지연) | §5.3 S3 |
| R-6 | 폴스타 호스트 CPU·메모리·FS 사용률 시간대별 + 분석 | `polestar_metric_trend(kind=cpu/memory/filesystem, granularity=h, baseline_periods)` | 결정적(절대 임계·z-score — `correlation.py`) | §5.3 S4 |
| R-7 | 제니퍼 알람 시각 전후 폴스타 연관 알람 + 소견 | `polestar_incident_alarms`(사건창) + `apm_events` · 결정적 상관 `correlate()` | 결정적 타임라인·선후 | §5.3 S5 |
| R-8 | 폴스타 프로세스 CPU 상위 5~10 | `polestar_process_snapshot(top_n=10, sort=cpu)` — **실시간 단면** | 표시만(판정 없음) | §5.3 S6 |
| R-9 | 변경 이력(ITSM · 제니퍼 배포 · 폴스타 구성 변경) | `apm_source_changes`·`apm_change_impact` · `polestar_change_history`(PG만) · **ITSM 어댑터 신설** | 결정적(시간 근접 = 「상관 후보」) | §5.3 S7 · §6 |
| R-10 | DPM(향후) | 어댑터 슬롯만 · 단계 기본 비활성 | — | §6 |
| R-11 | 종합 원인·대응 의견 | 단계 결과 + 결정적 가설 순위 + 카탈로그 대응 문구 → LLM 종합 1회 | 가설 순위 = 결정적 · 서술 = LLM | §5.4 |
| R-12 | 알람별 조사 항목을 문헌 근거로 정리 | — | — | §3 · §4 |
| R-13 | 알람에 따라 조사·대응 일괄 진행 | 플레이북 선택기 + 실행기 | 결정적 | §5.1~5.2 |
| R-14 | 알람 유형별 런북(의미·영향·진단·완화)을 조사와 함께 제시 (v1.2) | 플레이북 YAML `runbook` 블록 · 사내 매뉴얼 링크 | 결정적(유형 → 런북 1:1) | §5.7.2 |
| R-15 | 알려진 문제·우회 방법을 알람에 자동 대응 (v1.2) | 알려진 오류 레코드(KEDB) · 플레이북 판독 결과 | 결정적 일치(`match` + `cause_test`) | §5.7.3 |
| R-16 | 장애 대응 매뉴얼·TSG 문서 참조 (v1.2) | `src/doc_qa`(plans/126) RAG 컬렉션 | 참고 인용만(판정 무관) | §5.7.4 |
| R-17 | 조사 결과를 지식으로 축적·갱신 (v1.2) | 조사 저장소(plans/153) → 초안 → 사람 승인 | 사람 승인(LLM 자동 등록 차단) | §5.7.5 |
| R-18 | 지금 보거나 지난 알람을 찾아 다시 분석 (v1.9) | 노이즈 결정 저장소(`/noise/decisions` · 30일) · 알람 조사 잡(사용자 요청) | 결정적(유형 매칭 · 상한) | §5.6.2 ①② |
| R-19 | 알람·현상 질의 → 플레이북 또는 HolmesGPT 조사 (v1.9) | 결정적 대상·증상 매핑 → 플레이북 잡 · 불성립 시 `sre_diagnose` | 결정적 라우팅(LLM 분류 미의존) | §5.6.2 ③ |
| R-20 | 조사 진행 상황을 별도 「장애 진단」 탭(이벤트 알람 탭 옆)에서 실시간으로 본다 — 지금 실행 중인 도구·명령·API (v2.0) | sre_agent 잡 실행 기록(신규 · 메모리 링 버퍼) → `sre_get_investigation` 선택 인자 → 본체 중계 | 표시만(판정 무관) | §5.6.3 ⓑⓒ |
| R-21 | 중간 단계의 조회 내용(끝난 단계 결과 · 도구 응답 발췌)을 진행 중에 본다 (v2.0) | 플레이북 `partial_briefing`(기존) + 실행 기록의 응답 발췌(신규 · 마스킹) | 표시만 | §5.6.3 ⓒⓓ |
| R-22 | 진행 중에 다른 알람을 조사하면 새 탭이 열리고 기존 조사도 계속 진행·갱신된다 (v2.0) | 화면 하위 탭(조사 1건 = 탭 1개 · 상한 없음) · SSE 연결 1개 다중화(배경 탭 = 요약만) | — | §5.6.3 ⓓ |

### 1.3 범위 밖

- **자동 조치(재기동·롤백·풀 상향 등) 실행** — 대응은 「제안」으로만 낸다(Azure SRE Agent Review 모드 [공식 문서] 등 문헌 공통 human-in-the-loop · `plans/64` §8.3 B-3 거버넌스 착수 금지 유지). 플레이북 YAML의 대응 항목은 문구이지 실행 명령이 아니다.
- 트리거 정책 변경 — 자동 조사는 현행 `investigation_trigger_min_tier`(기본 PAGE) 진입을 그대로 쓴다. PAGE 모수 축소는 `plans/162` 소관. (v1.9) 사용자가 요청한 알람 분석(§5.6.2)은 자동 트리거가 아니라 별 진입이며 등급과 무관하다.
- ML RCA·임베딩 기반 유사 과거 사건 검색(`plans/101`) — §4.0의 「재발 확인」은 동일 알람 이력 조회까지만. v1.2의 알려진 오류 일치(§5.7.3)는 **선언된 조건의 결정적 평가**이지 유사도 검색이 아니다.
- 사람 조정용 인시던트 대응 절차(역할·통보·대외 커뮤니케이션 — PagerDuty IC/Scribe 체계 · NIST SP 800-61r3)의 자동화 — 브리핑은 매뉴얼의 해당 절 링크만 보인다(§5.7.1).
- KB의 웹 편집기 · 우회 조치의 자동 실행(KEDB 우회도 「제안」 문구 — 위 자동 조치 금지와 같다).
- DPM·ITSM의 **실 연동 구현** — 이 계획은 어댑터 계약·슬롯·스텁까지. 실 연동은 API 명세 확보 뒤 별 계획(G-6).
- 플레이북 웹 편집기 — 플레이북은 저장소 YAML로 관리한다(G-11).

---

## 2. 현행 실측

### 2.1 조사 흐름 (PAGE → 조사 → 브리핑)

| 지점 | 실측 | 근거 |
|---|---|---|
| 트리거 | 게이트 티어 ≥ `investigation_trigger_min_tier`(기본 `PAGE`)면 sre_agent에 submit | `src/config.py:1127-1134` · `noise_gate/application/nodes/investigation_trigger.py` |
| 조사 엔진 | HolmesGPT ReAct — 도구 선택·순서를 LLM이 정한다 · 코드는 dedup·동시성·타임아웃(300s)·예산만 | `sre_agent/application/investigation_dispatcher.py:1-30` |
| 사전수집 | 폴스타 사건창 전 알람 + 지표 4종(cpu·memory·filesystem·disk_io, baseline 포함) + 변경 1건(플래그) → 결정적 상관 | `sre_agent/application/evidence_prefetch.py:1-60` |
| **APM 사전수집** | **없음** — APM 트리거는 폴스타 소스를 모르면 사전수집 생략(`INFRA_SOURCE_UNKNOWN_NOTE`) | `evidence_prefetch.py:55` |
| kind별 플레이북 | `PLAYBOOK_NOTES`(cpu·memory·disk…) + `APM_PLAYBOOK_NOTE` — **지침 문자열**로 LLM 프롬프트에 붙을 뿐, 실행 보장 없음 | `investigation_guidance.py:192-242,341-365` |
| 알람 kind 분류 | `classify_alarm_kind`(apm·cpu·memory·disk·network·process·log) — 게이트 분류기와 동형 | `investigation_guidance.py:158` |
| 브리핑 | `build_briefing` — LLM 서술 + 결정적 타임라인·가설 · 단계 섹션 구조 없음 | `briefing_builder.py:272` |
| 저장·웹 표시 | 조사 저장소(`investigation_store.py`) · `/alarm/investigations/*` · 카드·드로어 브리핑 뷰(`briefing-view.js`) | `plans/153` W2·W3 |

### 2.2 요구 데이터의 도구 가용성

| 요구 | 도구 | 비고 |
|---|---|---|
| 인스턴스↔호스트 | `apm_instance_map(hostname|query)` | 정합 신뢰도·근거 포함. **폴스타 hostname과 정확 일치 결함**(`plans/165` F3) — S0가 이 결함을 그대로 물려받는다 |
| p95·TPS·에러율·액티브 | `apm_app_health` | 구간 p50/p95 + 에러율(1분 조각) · **p96 지표는 없다**(G-7) |
| 기준선 비교 | `apm_period_compare` | 절대 구간 4개 인자 — 「전일 동시간」 창은 실행기가 계산해 넘긴다 |
| 힙·GC%·프로세스 CPU·스레드 | `apm_runtime_health` | 구간 추세(5분 간격) + 판정(힙 압박·GC 지연) |
| DB 커넥션 풀 | `apm_resource_pool` | **현재값 전용** — 알람 시점 값이 아니다(지연 조사 시 표기 필수) |
| 트랜잭션 상위 N 분해 | `apm_slow_transactions(n)` | cpu·sql·fetch·external·network 시간 + 판정 |
| 단건 프로파일 | `apm_transaction_profile` | 조사당 호출 예산 있음 |
| 제니퍼 이벤트·오류 | `apm_events` | 오류 유형별 건수 |
| 변경(제니퍼) | `apm_source_changes`·`apm_change_impact` | reference_time만 넘긴다(기본 24h 탐색 — `investigation_guidance.py:53-57`) |
| 호스트 사용률 시계열 | `polestar_metric_trend` | **최소 granularity = 시간(h)** — 분 단위는 Prometheus `prom_metric_range`(구성된 경우) |
| 사건창 알람 | `polestar_incident_alarms` · `polestar_alarm_history` | |
| 프로세스 Top | `polestar_process_snapshot` | **실시간 단면**(`captured_at`) · `PROCESS_API_BASE_URL` 필요 |
| 변경(폴스타) | `polestar_change_history` | PostgreSQL(gp·yd)만 · DB2(b0) 미지원 |
| 가용성·토폴로지 | `polestar_resource_status` · `polestar_topology` | |
| DPM · ITSM | **없음** | 신설 어댑터 슬롯(§6) |

**결론**: 사용자 예시 7단계 중 DPM·ITSM을 뺀 전부가 **현행 MCP 도구로 수집 가능**하다. 새로 필요한 것은 도구가 아니라 ① 단계를 정하는 카탈로그·플레이북 ② 결정적 실행기 ③ 단계별 서술 ④ 섹션형 브리핑 계약이다.

### 2.3 W0 재대조 (2026-10-19 · 99ec1ea4)

> §2.1·§2.2·§5.0·§5.3·§5.6·§5.6.1·§5.6.2·§8이 인용한 위치를 99ec1ea4에서 다시 확인했다. 본문 수치는 지우지 않고 이 표로 정정한다. sre_agent 경로는 안쪽 패키지 기준이다(`sre_agent/application/…` = 저장소 `sre_agent/sre_agent/application/…`). 상태: **변경** = 위치가 옮겨짐 · **소멸** = 그 이름의 실체가 없음 · **불변** = 그대로.

| 계획서 표기 | 현재 위치(99ec1ea4) | 상태 |
|---|---|---|
| `src/config.py:1127-1134` (`investigation_trigger_enabled`·`_min_tier`) | `src/config/alarm.py:361-369` (config 패키지화) · `investigation_total_timeout_seconds` `:376` · `fault_diagnosis_enabled` `:409` · `decision_store_max_lines` `:270` | 변경 |
| `noise_gate/application/nodes/investigation_trigger.py` | 같은 파일 · `investigation_trigger_node` `:57` · 티어 판정 `:136-138` | 불변 |
| `investigation_dispatcher.py:1-30` (300s·예산) | 같은 위치 · 시간당 예산 `:348-350`(`hourly_budget_exceeded`) · 300s 정본 `settings.py:109` | 불변 |
| `evidence_prefetch.py:1-60` | 같은 파일(282줄) · `BatchCaller` `:36` · `METRIC_KINDS` `:39` · `EvidenceScope` `:53` · `scope_from_job` `:69` · `build_calls` `:131` · `prefetch_and_correlate` `:224` | 불변 |
| `evidence_prefetch.py:55` (`INFRA_SOURCE_UNKNOWN_NOTE`) | `:49` | 변경 |
| `investigation_guidance.py:192-242` (`PLAYBOOK_NOTES`) | `:192-238` · `playbook_note` `:240` | 변경(끝줄) |
| `investigation_guidance.py:341-365` (`APM_PLAYBOOK_NOTE`) | `:341-355` · `apm_playbook_note` `:360` · `apm_notes` `:368` · `build_guidance` `:473` | 변경(끝줄) |
| `investigation_guidance.py:158` (`classify_alarm_kind`) | `:158` · 키워드 표 `_KIND_KEYWORDS` `:148` | 불변 |
| `investigation_guidance.py:53-57` (변경 24h 탐색) | 주석 `:45-57` · `APM_ANCHORED_TOOLS` `:59` | 불변 |
| `briefing_builder.py:272` (`build_briefing`) | `:421` · `BRIEFING_ELEMENTS` `:33` · `HUMAN_GATED_NOTE` `:49` · `stub_briefing` `:495` | 변경 |
| `correlation.py` (z-score·절대 임계) | `sre_agent/domain/correlation.py` · `metric_finding` `:120` · `correlate` `:242` · `Z_THRESHOLD=3.0` | 불변 |
| `severity_judge` (모듈) | 모듈 없음 — 플래그 `severity_judge_enabled`(`settings.py:152`)뿐 · 실체는 `sre_agent/domain/severity_signatures.py`(`ImportanceVerdict` `:81` · `judge` `:248` · `apm_payloads` `:196` · `was_signals_from_outputs` `:215`) | 소멸(이름) |
| `remediation.py` | `sre_agent/domain/remediation.py` · `recommend` `:186` · `recommend_lines` `:231` | 불변 |
| `mcp_tool_client.py` (두 MCP 클라이언트·배치 콜러블 — §5.1) | 95줄 · `make_batch_caller`는 **`polestar_mcp_url`만** 지원 · 배치는 한 SSE 세션 **순차** 호출 · 배선 `interface/mcp_service.py:223` · APM 배치 콜러블 **없음**(`apm_mcp_url`은 `settings.py:39`에만) | 변경(전제 절반) |
| `settings.py:26` (`max_steps=40`) | `:26` | 불변 |
| `investigation_store.py` · by-alarm | `noise_gate/infrastructure/investigation_store.py` · `get_by_alarm` `:368` · 등급 `_grade` `:412`(끝난 사건 2 > 그 알람 kind=alarm 1 > 기타 0) | 불변 |
| `/alarm/investigations/*` | `src/api/routes/alarm.py` · by-alarm `:1312` · 단건 `:1345` | 불변 |
| `src/api/routes/noise_dashboard.py` (`/noise/decisions`) | `/decisions` `:689` · `/decisions/{alarm_id}` `:735` · `get_by_alarm` 사용 `:433` | 불변 |
| `noise_gate/infrastructure/decision_store.py` | `DecisionStore` `:110` (`max_lines=20000`) | 불변 |
| `src/nodes/fault_diagnosis.py` · `authorize_host_investigation` | `fault_diagnosis` `:81` · 호출 `:119` · 정의 `src/domain/host_authz.py:99` · `HOST_AUTHZ_MODE` `src/config/security.py:154`(기본 admin_only) | 불변 |
| `src/nodes/context_resolver.py:310` (`_extract_previous_entities`) | `:310` | 불변 |
| `src/orchestration/intent_planner.py` | 존재 | 불변 |
| `src/static/js/briefing-view.js` 367줄 | 367줄 | 불변 |
| `app.js:5026` (알람 카드 브리핑) | `:4943` · `:5022` · `:5037-5065` (전체 5495줄) | 변경 |
| `app.js:4767` (`startNewChat`) | `:4621` | 변경 |
| `app.js:5167` (`stageAlarmPrompt`) | `:5179` | 변경 |
| `app.js:3182` (`apm_job_id` 카드 복원) | `:3194-3209` (`apmJobRefs`) | 변경 |
| (참고) D-192 알람 질의 칩 | `ALARM_PROMPT_AXES` `app.js:3975-3995` | — |
| `noise.js:1849` (드로어 ⓒ) | `:1848-1849` (전체 3281줄) | 불변 |
| `noise.js:1863` (break-glass 주석) | `:1867` | 변경 |
| `style.css:1144` (max-width 920px) | `:1144` | 불변 |
| `src/static/admin/dashboard.html` 탭 11종 | 11종(apmsources·auditlogs·dbconfig·dbstructure·drm·feedback·incidents·ragdocs·settings·sourcememory·users) | 불변 |
| `src/api/thread_history.py` (disclosures) | 132줄 · `:109` | 불변 |
| `apm_gateway` `ApmTools`(§5.3 도구명) | D-337로 `application/{apm_core,instance_tools,health_tools,transaction_tools,compare_tools,stats_tools,scope_tools,fleet_tools,manage_tools}.py` + `tools.py`(253줄) 분할 · MCP 등록 `interface/server.py`(`_Dispatch` 래핑 · 큰 결과는 아티팩트) · 도구 이름·인자는 불변 | 변경(내부) |
| `apm_gateway/.../poller.py:225` (`resolver.reverse`) | `:225` | 불변 |

**도구 모양 실측** (합성 Open API + `httpx.MockTransport` in-process 호출 · 망 호출 0 · 키 집합 사본은 `sre_agent/tests/test_plan187_w0_fixtures.py` 상수)

| 도구 (server.py 줄) | 인자(공통 `investigation_id`·`thread_id`·`owner`·`wait_seconds`·`targets` 외) | 응답(공통 봉투 `rows`·`row_count`·`queried_at`·`source_kind="apm_api"`·`source`·`tool`·`limits`·`sources`·`total_row_count` 외) |
|---|---|---|
| `apm_instance_map` `:379` | `hostname` · `source_ids` · `domain_id` · `query` · `business` (`query`/`business`는 `hostname`과 배타) | 행: 인스턴스 메타 + `match_confidence`·`match_reason` · query면 `hostname`·`match_kind`·`match_tier`·`search_confidence` + 최상위 `search{tier,counts}` · business면 `business_names`·`match_kinds` + `business{counts}` · 미매칭 `suggestions`·`disclosures` |
| `apm_app_health` `:416` | `hostname`\|`instance_name`\|`instance_id` · `reference_time` · `lookback_minutes` · `source_ids` | **과거 모드** 행 `window{calls,errors,error_rate,response_time_p50_ms,p95_ms,max_ms}` + 최상위 `hourly`·`window`·`was_signals` — **TPS·액티브 없음**(실시간 모드에만 `tps`·`active_services`·`reject_rate`…) |
| `apm_period_compare` `:1073` | `current_start`·`current_end`·`baseline_start`·`baseline_end`(필수) · `hostname`\|`instance_name` · `source_ids` · `n` · `full` | 행 `current`·`baseline`·`delta{abs,pct}` (calls·failures·failure_rate·avg/max_response_ms) + `summary` · **p95 없음**(시 단위 통계 — `[한계]` 문구로 명시) |
| `apm_runtime_health` `:457` | 위 + `metrics`(list) · `interval_minute` | 과거 모드 행 `trend{heap_used_mb,heap_committed_mb,gc_time_usage_pct:[{time_ms,value}]}`(5분) · `metrics=["proc_cpu","active_service"]`면 `process_cpu_pct`·`active_services` 추세 |
| `apm_resource_pool` `:503` | `hostname`\|`instance_name`\|`instance_id` · `source_ids` | 현재값 전용 — `db_pool_active`·`db_pool_configured_avg`·`db_pool_idle_avg`·`db_pool_usage_ratio`·`active_services`·`active_by_running_mode`·`active_by_datasource`·`thread_current` |
| `apm_slow_transactions` `:544` | `target`(hostname\|instance_name) · `reference_time` · `lookback_minutes` · `n` · `source_ids` · `full` | 행: `response_time_ms`·`cpu_ms`·`sql_ms`·`fetch_ms`·`external_ms`·`network_ms`·`frontend_ms`·건수 3종·`application`(서비스명 — 마스킹 URL)·`profile_ref{source_id,domain_id,txid,time_ms}` … + `summary{…,sql_fetch_share,external_share}` |
| `apm_transaction_profile` `:695` | `hostname`\|`instance_name` · `domain_id` · `txid` · `time_ms` · `top_k` · `source_id` · `profile_no` · `include_param_key` (예산 있음) | 행 `transaction{…}`·`profile_excerpt`(마스킹 텍스트)·`profile_truncated`·`sqls[]` · `[한계] 프로파일 텍스트 형식 미검증(J0-L-b 녹화 전)` |
| `apm_events` `:632` | `hostname`\|`instance_name` · `reference_time` · `lookback_minutes` · `level` · `level_mode` · `error_type` · `record`(event\|error) · `n` · `full` · `source_ids` | 이벤트 행 `time_ms`·`level`·`event_type`(원문 — `ERROR_` 접두 유지)·`event_kind`·`value`·`message`·`profile_ref` … · 오류 행 `error_type`·`txid`·`profile_index` · 최상위 `record`·`errors_by_type` |
| `apm_active_services` `:590` | `target` · `n` · `source_ids` · `full` | 현재값 전용 · 31키(`elapsed_ms`·`cpu_ms`·`running_mode`·`running_text`·`active_ref` …) + `summary{total,by_running_mode,by_status}` |
| `apm_source_changes` `:918` | `target` · `reference_time` · `lookback_minutes`(기본 1440) · `source_ids` | 행 `change_detected_at`·`change_detected_ms` + 인스턴스 키 |
| `apm_change_impact` `:1023` | 위 + `width_minutes`(기본 60) · `n` · `full` | 행 + `before`/`after{calls,tx_errors,error_rate,avg_response_ms,p95_response_ms,…}`·`delta` + `summary` |
| 게이트웨이 오류 | — | `{error: 코드, reason, source_kind, source, tool}` · 코드 not_configured·invalid_argument·instance_unresolved·profile_ref_mismatch·source_unavailable·contract_violation·apm_quota_exceeded·apm_api_error·rate_limited |
| `polestar_metric_trend` (`mcp_server/polestar_tools.py:587`) | `source`·`server_name`·`kind`·`granularity`·`periods`·`reference_time`·`lookback_minutes`·`baseline_periods` | `_ok` 봉투(`source_kind="polestar_db"`·`engine`) + 행 `stat_date`·`avg_val`·`min_val`·`max_val` + `kind`·`granularity`·`window{…,stat_date_incident_from,…}` |
| `polestar_incident_alarms` `:541` | `source`·`server_name`·`reference_time`·`lookback_minutes`·`alarm_name` | 행 `alarm_id`·`alarm_time`·`severity`·`alarm_status`·`resource_name`·`alarm_name`·`resource_type` + `window` |
| `polestar_process_snapshot` `:712` | `hostname`·`top_n`·`sort`(cpu\|mem) | 행 `name`·`pid`·`ppid`·`user`·`p100cpu`·`pcpu`·`pmem`·`rss`·`args` + `total_processes`·`captured_at`·`source_kind="polestar_process_realtime"`·`note` |
| `polestar_change_history` `:805` | `source`·`server_name`·`hours`·`reference_time`·`lookback_minutes` | 행 `id`·`resource_id`·`resource_type`·`lifecycle_type`·`description`·`event_time`(epoch) · DB2는 `unsupported=True` |
| 폴스타 오류 | — | `{"error": 문장}` · 서버 매칭은 `SVR.NAME` 정확 일치 |

**`was_signals` 어휘**: 항목 키 `kind`·`level`(CRITICAL\|WARNING)·`category`(strong\|medium)·`label`·`evidence`·`instance_id`·`source_tool`·`source_id`. kind = `was_service_queuing`·`was_db_pool_exhaustion`·`was_gc_stall`·`was_heap_pressure`·`was_external_call_delay`·`was_error_burst`·`was_slow_sql`·`was_thread_pool_exhaustion` 8종 — **응답시간·지연 kind는 없다**. 이벤트 매핑(`adapters/jennifer/fields.py:60` `EVENT_TYPE_SIGNALS`)에도 지연 이벤트가 없다.

**W0 판정 결과**

- **R-D(커넥션 획득 분리)**: 게이트웨이에 커넥션 획득·SQL 실행을 가르는 **구조화 필드가 없다**(GET-CONNECTION 계열 키 0). 남는 근거는 형식 미검증 `profile_excerpt` 텍스트뿐이다 → 지시대로 `db_split=n/a`로 픽스처를 고정하고 문헌 결론은 `case_conclusion`에 따로 둔다. §8 수용 기준 2·2′·9의 acquire/execute 기대와 충돌 — 팀 리드 판단 필요.
- **plans/165 F3**: 미해결(계획서 TODO). `src/db_adapters/polestar/assembler.py` ~`:1152-1158` 정확 일치 `HAVING … IN (...)` 그대로 · `cross_system_key_bridge_enabled=False`(`src/config/__init__.py:158`). sre_agent 쪽에는 APM 호스트 → 폴스타 `source`·`server_name` 변환이 아예 없다(`scope_from_job`은 APM 트리거면 `meta.episode.infra_db_ids[0]`가 없을 때 폴스타 축 생략). S0의 폴스타 연결은 신규 구현 대상이다.
- **추가 사실**: ① `apm_period_compare`에 p95가 없어 S1 기준선 p95는 기준 시각을 바꾼 `apm_app_health` 추가 호출로 얻는다 ② 과거 모드 `apm_app_health`에 TPS·액티브가 없어 TPS = `window.calls`/창 초, 액티브·WAS 프로세스 CPU는 `apm_runtime_health(metrics=[…])` 추세로 얻는다 ③ R-G 코어 수를 주는 도구가 없다(픽스처는 4코어 가정) ④ `apm_slow_transactions`에 서비스명 필드가 따로 없어 R-A `service_spread`는 `application`(마스킹 URL)으로 묶는다.

**제안 칩 골든 대조(§5.6.1 4항)**

| 칩 | text2sql_gold · routing_gold | 결과 |
|---|---|---|
| 「{host} 서버 최근 7일 CPU 사용률 추이」 | 7일 CPU 추이 항목 없음 — 가까운 것은 gp-009·yd-004·b0-004(지난 1개월 평균·최대 CPU — 기존 D-192 칩과 같은 축) · routing r-080(Prometheus 30분 CPU 추이 · 미채점) | 없음 → 그 칩은 내지 않음 |
| 「{instance} 응답시간 지난주 같은 시간대와 비교」 | 없음 — 별도 세트 apm_bench `APM-COMPARE-day`·`APM-COMPARE-week`만 유사 | 없음 → 그 칩은 내지 않음 |
| 「{host} 프로세스 CPU 상위 10」 | 없음 — routing r-081(실시간 프로세스 목록 · 미채점)만 유사 | 없음 → 그 칩은 내지 않음 |

**산출물**: 골든 픽스처 9건·README·SME CSV 2종 `sre_agent/tests/fixtures/plan187/` · 모양 검사 `sre_agent/tests/test_plan187_w0_fixtures.py`.

---

## 3. 문헌 조사 — 조사 항목의 근거 (v1.1 직접 재조사 · 2026-10-17)

### 3.0 조사 방법과 검증 수준

v1.0의 근거는 서브에이전트가 검색 요약으로 모은 것이었다. v1.1에서는 같은 출처와 미확인 항목을 **직접 접속해 다시 확인**했다. 제니퍼 공식 문서 6건(백서 2 · 사례 4)은 원문 PDF를 내려받았다. 사례 4건은 이미지 PDF라 텍스트 레이어가 없어서 OCR(tesseract eng)로 읽었다. 표의 검증 수준은 다음 기준으로 매겼다.

| 표기 | 뜻 |
|---|---|
| **[원문]** | 원문 본문을 직접 읽고 인용·수치를 대조했다 |
| **[원문·OCR]** | 이미지 PDF를 OCR로 읽었다. 본문 서술은 판독했으나 화면 캡처 안의 표·로그 숫자는 깨진 곳이 있다 |
| **[공식 문서]** | 벤더 공식 문서 페이지를 직접 읽었다 |
| **[2차]** | 원문에 접근하지 못해 원문을 인용한 2차 자료로 확인했다 |
| **[제안]** | 출처가 직접 말하지 않는 이 프로젝트의 설계 값이다. 파일럿에서 튜닝한다 |
| **[미확인]** | 공개 자료로 찾지 못했다 |

**v1.0 정정**: Datadog Bits AI 공식 페이지에는 결론 상태로 「evidence-backed 결론」과 **inconclusive**(근거 불충분)만 나온다. v1.0에 적은 「validated/invalidated/inconclusive」 3상태는 원문에 없다. 플레이북의 가설 상태 어휘는 이 계획이 정하는 [제안]으로 바꿨다(§5.4).

### 3.1 제니퍼 공식 문서 — 사용자 예시(WAS 지연)의 직접 근거

| 문서 | 이 계획에 주는 것 | 수준 |
|---|---|---|
| **JENNIFER Technical Article 「X-View」**(S.J. Kim, 2009, v2.5+) | ① 평균의 함정: 몇 건의 느린 서비스가 평균에 묻히므로 **트랜잭션 개별 단위로 보고, 문제 트랜잭션의 자원 사용·프로파일로 내려간다**. ② **산점도 패턴 3종**: **수직(스파이크)** = 공유 자원 고갈로 대기하던 트랜잭션이 자원이 풀리는 순간 한꺼번에 끝난 것(예: 커넥션 풀이 모두 점유됨) · **파형** = 자원 부족으로 응답시간이 반복 요동 · **수평선** = 짧게 잡힌 타임아웃. ③ **범위 판정 규칙**: 스파이크 트랜잭션이 **한 서버**에서만 나오면 내부 자원, **여러 서버**에서 나오면 외부(공통) 자원 · **한 서비스**에 몰리면 그 서비스 전용 자원, **여러 서비스**면 공통 자원. ④ OS의 % 사용률은 공급만 보여 주고 수요는 보여 주지 않는다(100%에 묶이면 필요량을 알 수 없다) | [원문] https://cdn.jennifersoft.com/wp-content/uploads/Documents/en/JENNIFER_White_Paper-Why_Monitor_Response_Time_by_Transaction_X-View.pdf |
| **JENNIFER White Paper 「Performance Analysis Model」**(Bruce Park, 2011) | ① **Little's Law**: 액티브 서비스 수 = TPS × 응답시간(초). ② **처리량-응답시간 관계**: 포화점까지는 TPS가 부하에 비례해 오르고 응답시간은 평탄하다(Light Load). 포화점을 넘으면 TPS가 평탄해지고 응답시간이 급등한다(Heavy Load). 병목이 겹치면 TPS가 오히려 떨어진다(Buckle Zone). ③ CPU 사용률과 응답시간: 단일 CPU는 50%를 넘으면 대기열·응답시간이 급증하고, 4 CPU 이상은 약 80%까지 완만하다가 그 뒤 급증한다 → 용량 계획 기준 70~80%. ④ 응답시간 분해: 네트워크 · 웹서버 · WAS(JSP·클래스·메서드) · DB(커넥션 · SQL · 실행). ⑤ 측정 구간은 피크 시간대로 잡고, 피크마다 다르므로 여러 번 측정해 평균한다 → 기준선은 단일 시점이 아니라 다회 평균 | [원문] https://cdn.jennifersoft.com/wp-content/uploads/Documents/en/JENNIFER_White_Paper-Performance_Analysis_Model.pdf |
| **사례: Application Delay from Misconfigured JDBC Connection** | X-View에서 14시 이후 지연군(30초 초과 적색) 선택 → 트랜잭션 프로파일. **GET-CONNECTION에 약 15.6초가 걸렸고 SQL 실행은 7ms였다** → 원인은 WAS 커넥션 풀을 쓰지 않고 자체 JDBC 관리자로 매번 연결을 맺은 것. **교훈: 「DB 시간」을 커넥션 획득과 SQL 실행으로 나눠 보지 않으면 원인 계층을 오판한다** | [원문·OCR] https://cdn.jennifersoft.com/wp-content/uploads/Documents/en/JENNIFER_Case_Study-Direct_JDBC.pdf |
| **사례: Performance Problem with Services Accessing External Systems** | 서버 CPU 50% 초과 + 응답 지연. X-View 5초 초과군을 분석하니 **지연 트랜잭션 공통 요인 = 같은 LDAP search 메서드**(메서드 프로파일) → 파라미터(사용자 그룹)별로 지연 정도가 갈림 → 원인은 LDAP 검색 인덱스 손상. 인덱스 복구 뒤 응답 2초 미만, 시스템 CPU 50→20%. **그러나 Java 프로세스 CPU는 15~20%로 그대로** → 호스트 CPU 상승은 WAS가 아닌 다른 프로세스 몫이었다. **교훈: 호스트 CPU와 WAS 프로세스 CPU를 나눠 보고, 차이는 프로세스 Top으로 확인한다** | [원문·OCR] https://cdn.jennifersoft.com/wp-content/uploads/Documents/en/JENNIFER_Case_Study-External_Transaction.pdf |
| **사례: Analyzing Case of High CPU Utilization** | 평소 CPU 50%가 무작위로 100%까지 오르며 응답 지연·다운. **CPU 급등이 유입 요청량(TPS)과 상관이 없었다** → 액티브 서비스 목록의 서비스별 CPU 칸으로 소수 서비스가 CPU를 독점하는 것을 찾음 → 원인은 불필요한 루프. 액티브 서비스 색 구분: 파랑 1초 미만 · 보라 1~3초 · 주황 3~8초 · 빨강 8초 초과. **교훈: CPU 급등이 TPS와 비례하지 않으면 부하가 아니라 특정 서비스 로직을 의심한다** | [원문·OCR] https://cdn.jennifersoft.com/wp-content/uploads/Documents/en/JENNIFER_Case_Study-CPU.pdf |
| **사례: Out of Memory (OOM) Detection and Analysis** | 무작위 OOM과 잦은 Full GC. **힙 사용량이 약 3시간마다 급증하는 주기**를 보고 컬렉션 모니터링을 켬 → 계속 커지는 Vector 객체와 그 생성 스택 → static 필드에 요소 삭제 로직이 주석 처리된 코드. **교훈: 힙 추세의 모양(주기적 급증·바닥 상승)이 누수 판별의 첫 신호다** | [원문·OCR] https://cdn.jennifersoft.com/wp-content/uploads/Documents/en/JENNIFER_Case_Study-OOM.pdf |
| 제니퍼 문서 목록 | 위 6건의 출처. 제니퍼5 사용자 가이드는 서버에 포함돼 있고 공개 링크는 없다 | [공식 문서] https://jennifersoft.com/en/library/documentations/ |

**한계**: 공개된 제니퍼 문서는 2009~2011년 4.x·5.x 시절 자료다. 현재 버전의 공식 「응답지연 분석 절차」 매뉴얼은 공개 자료에 없다([미확인]). 다만 분석 원리(개별 트랜잭션·산점도 패턴·범위 판정·Little's Law·응답시간 분해)는 버전과 무관하다. 게이트웨이가 이미 같은 축(구간 p95 · 시간 분해 · 액티브 · 판정)을 제공하므로 플레이북 규칙으로 옮길 수 있다.

### 3.2 방법론 · 자동 조사 사례 · 학술

| 근거 | 플레이북에 주는 것 | 수준 |
|---|---|---|
| Google SRE book 12장 Effective Troubleshooting | **Triage 우선**("Stopping the bleeding should be your first priority") → 대응 제안은 완화 먼저, 근본 원인 나중 · **"What touched it last"**("Recent changes to a system can be a productive place to start") → 변경 단계 S7 · 함정 목록("correlation is not causation" · 무관한 증상 · 과거 원인 고착 · 공통 원인에 의한 가짜 상관) → 종합 단계 금지 규칙 | [원문] https://sre.google/sre-book/effective-troubleshooting/ |
| Google SRE book 6장 Four Golden Signals(지연·트래픽·에러·포화) | 1차 증상 단계 S1 · 증상(what)과 원인(why)의 분리 | [원문] https://sre.google/sre-book/monitoring-distributed-systems/ |
| Brendan Gregg USE Method · Linux 체크리스트 | 자원 단계(S2 JVM · S4 호스트)에서 자원마다 사용률·포화·오류를 본다. 포화 기준은 CPU run queue > CPU 수, 메모리 스왑 in/out·OOM kill, 디스크 await·큐 길이다. 파일시스템은 포화 정의가 없고 가득 차면 ENOSPC가 난다 | [원문] https://www.brendangregg.com/usemethod.html · https://brendangregg.com/USEmethod/use-linux.html |
| Netflix 「Linux Performance Analysis in 60,000 ms」 | 첫 1분 10개 명령: uptime → dmesg\|tail → vmstat 1 → mpstat -P ALL 1 → pidstat 1 → iostat -xz 1 → free -m → sar -n DEV 1 → sar -n TCP,ETCP 1 → top. 원격 셸이 허용되면 HolmesGPT 심층 조사 지침에 쓰고, 플레이북은 폴스타 수집 값으로 대체한다 | [2차] 원문 403 · https://www.thomas-krenn.com/en/wiki/Linux_performance_analysis_in_60_seconds |
| **RCACopilot** — Chen et al., *Automatic Root Cause Analysis via Large Language Models for Cloud Incidents*, EuroSys '24, DOI 10.1145/3627703.3629553 | **이 계획의 골격.** 알람 유형별 incident handler(결정 트리형 수집 워크플로) · 재사용 액션 3종(scope switching · query · mitigation) · 조회 결과 enum 분기 · 수집 정보를 120~140단어로 요약한 뒤 LLM이 원인 범주를 예측 · 정확도 최대 0.766(1년 실데이터) · 수집 컴포넌트를 4년 넘게 운영 · 신규 원인 범주 24.96% → 미매칭 폴백이 필요하다 | [원문] https://arxiv.org/abs/2305.15778 |
| Ahmed et al., ICSE 2023(arXiv 2301.03797) | 진단 데이터 없이 인시던트 텍스트만으로 하는 LLM 추천에는 한계가 있다 → 수집 단계를 먼저 두는 근거 | [2차] https://arxiv.org/pdf/2301.03797 |
| **CNCF 블로그 — STCLab의 HolmesGPT 알람 자동 진단**(2026-04) | 같은 모델에서 런북이 있으면 **4.6/5**, 없으면 **3.6/5**. 런북 없이는 무관한 도구를 부르며 루프를 돌고 단계 예산을 소진한다(20+ 단계·무관한 가설 3개). 런북이 있으면 도구 호출 3~4회. 「모델 교체보다 런북의 제외 규칙이 더 효과적이었다」. 런북 머리에 범위·사용 도구·주의 메타를 둔다. 수동 분류 15~20분이 요약 읽기 2분 미만으로 줄었다 → **이 저장소 sre_agent가 HolmesGPT라서 가장 직접적인 근거다.** 플레이북의 결정적 수집과 심층 조사용 런북 메타(G-2)를 함께 쓴다 | [원문] https://www.cncf.io/blog/2026/04/21/auto-diagnosing-kubernetes-alerts-with-holmesgpt-and-cncf-tools/ |
| **Azure SRE Agent — Automate incident response** | 응답 계획 = 심각도·제목 필터 → 하위 에이전트 · 자율 수준 **Review(조치 전 승인)/Autonomous** · **재조사 쿨다운**. HTTP 500 예에서는 업로드한 런북대로 **상위 의존성 → 커넥션 풀 → 최근 배포** 순으로 조사하고, 찾은 것·근거·다음 조치로 보고한다 | [공식 문서] https://learn.microsoft.com/azure/sre-agent/automate-incidents |
| Datadog Bits AI SRE — Investigate Alerts | 모니터가 alert 상태에 들어갈 때 자동 시작(warn·no data·재통보는 제외) · 가설을 세우고 텔레메트리로 확인 · 근거가 부족하면 **inconclusive** · 서비스 태그로 상관 정확도를 높인다 | [공식 문서] https://docs.datadoghq.com/bits_ai/bits_ai_sre/investigate_alerts |
| PagerDuty change events · Rundeck Automated Diagnostics · AWS SSM Automation · Shoreline Op Packs · Robusta enrichment | 「알람 매칭 → 읽기 전용 수집 → 요약 → 사람 승인 조치」 공통 패턴 · 변경은 알람이 아니라 상관 후보다 | [2차] 부록 A |

### 3.3 자원별 판독 근거 (JVM · 커넥션 풀 · DB)

| 근거 | 내용 | 수준 |
|---|---|---|
| JDK 「GC overhead limit exceeded」 | GC가 실행 시간의 98%를 넘게 쓰면서 힙을 2% 미만 회수하는 상태가 연속되면 발생한다. 원인은 살아 있는 데이터가 힙에 겨우 들어가는 상태다. `-XX:-UseGCOverheadLimit`은 증상만 가린다. Full GC 뒤 사용량이 총 힙에 가까우면 증설 또는 누수 분석 | [2차] Oracle 원문 미접속 · https://mastertheboss.com/java/how-to-solve-java-lang-outofmemoryerror-gc-overhead-limit-exceeded/ · https://support.atlassian.com/confluence/kb/confluence-crashes-due-to-outofmemoryerror-gc-overhead-limit-exceeded-error/ |
| 커넥션 풀(HikariCP 지표 해설) | 신호: 대기 스레드(pending) 지속 > 0 · 획득 시간 급등 · active = max. **쿼리 지연 지표에는 풀 대기 시간이 들어가지 않는다.** 획득 시간만으로는 「풀 과소」와 「DB 지연」을 가를 수 없으므로 같은 창의 DB 세션·활성 작업과 맞춰 본다. 점유 시간이 길면 쿼리가 빨라도 풀이 고갈된다 → 제니퍼 JDBC 사례의 GET-CONNECTION 분리와 같은 결론 | [2차] 벤더 블로그 · https://oneuptime.com/blog/post/2026-08-24-correlate-pool-checkout-database-session-saturation/view |
| IBM Db2 LUW 락 대기 | `MON_GET_CONNECTION`으로 의심 세션의 애플리케이션 핸들 → `MON_GET_APPL_LOCKWAIT`(대기 중인 락과 보유 핸들) · `MON_GET_LOCKS` · 장기 기록은 `mon_lockwait`·`mon_lw_thresh` 락 이벤트 모니터 | [공식 문서] https://www.ibm.com/docs/en/SSEPGG_11.5.0/com.ibm.db2.luw.sql.rtn.doc/doc/r0056429.html |
| MariaDB 락 대기 | `information_schema.INNODB_LOCK_WAITS` + `INNODB_TRX`(PROCESS 권한) · 10.6+ `sys.innodb_lock_waits`(대기·블로킹 프로세스·쿼리·대기 시간) · performance_schema의 락 대기 계측에는 버전별 결함 보고가 있다 | [공식 문서] https://mariadb.com/kb/en/innodb_lock_waits-and-xinnodb_lock_waits-sys-schema-views · https://mariadb.com/kb/en/information-schema-innodb_lock_waits-table/ |
| PostgreSQL 락 대기 | `pg_stat_activity`의 `wait_event_type='Lock'` · `pg_blocking_pids` 블로커 체인 · idle in transaction | [2차] https://www.netdata.cloud/guides/postgres/postgres-blocking-queries/ |

DB 3엔진 항목은 **DPM 단계(S8)의 수집 명세**다. 업무 DB는 이 저장소의 조회 대상이 아니라 DPM이나 별도 읽기 전용 계정이 필요하다(G-6).

### 3.4 시간창 · 기준선

| 근거 | 내용 | 수준 |
|---|---|---|
| ServiceNow Event Management 알람 상관 규칙 | 주·부 이벤트의 시간 차 기본 **60분**, 최대 1440분 | [공식 문서] https://www.servicenow.com/docs/r/it-operations-management/event-management/alert-correlation-rule-form.html |
| Dynatrace Davis 이벤트 분석 시간 | 이벤트는 관측 슬라이딩 창(예: 5분 창 중 3분 위반)이 끝난 뒤 발생하지만 **시작 시각은 첫 위반 시점으로 소급**한다 · 분석 창을 이벤트 시작 기준 ±오프셋으로 둔다 → **알람 시각 ≠ 증상 시작이므로 알람 이전 창이 필요하다**는 근거 | [공식 문서] https://docs.dynatrace.com/docs/discover-dynatrace/references/semantic-dictionary/model/davis · https://www.dynatrace.com/support/help/shortlink/problem-lifecycle |
| 제니퍼 Performance Analysis Model | 기준은 피크 시간대를 여러 번 측정한 평균이다 | [원문] §3.1 |
| 기준선 관행(직전·전일·전주 동시간 · 최근 N주) | Splunk·Coralogix·Grafana | [2차] 부록 A |
| 알람 전후 조사 창의 표준 권고 | — | **[미확인]** 공개 표준 없음 → §4.0 값은 [제안] |

### 3.5 국내 금융권

| 근거 | 내용 | 수준 |
|---|---|---|
| 금융감독원 「금융 IT 안전성 강화 가이드라인」 제정 TF(2023-03 보도) | 전산장애 주요 원인을 **프로그램 오류 · 비상대책 · 성능관리**로 꼽고 업권 협회와 최소 기준을 제정한다. 성능관리 가이드라인에는 단계별 대응방안 · 대형 이벤트 성능관리 · 성능관리 비상대책을 넣는다고 밝혔다. **가이드라인 원문(모니터링·임계·단계별 대응)은 공개 자료에서 찾지 못했다** | 보도 [2차] https://www.sciencetimes.co.kr/news/%EF%BC%82%EA%B8%88%EC%9C%B5-%EC%A0%84%EC%82%B0%EC%9E%A5%EC%95%A0-%EB%A7%89%EC%9E%90%EF%BC%82%EC%95%88%EC%A0%84%EC%84%B1-%EA%B0%80%EC%9D%B4%EB%93%9C%EB%9D%BC%EC%9D%B8-%EC%A0%9C%EC%A0%95/?cat=128 · 원문 **[미확인]** |
| 금융권 전산장애 건수(2019 196 → 2020 198 → 2021 228 · 은행 35.2%) · 증권은 동시접속 급증 부하가 주원인 | 성능·부하형 장애의 비중이 크다는 정황. 원인 유형 통계 원문은 미확인 | [2차] 언론 · https://www.kukinews.com/article/view/kuk202210120132 |

→ G-10: 은행연합회 업권 성능관리 가이드라인과 사내 장애 대응 매뉴얼을 확보해 카탈로그를 보정한다.

### 3.6 설계 원칙 도출

1. **증상 → 자원 → 의존 계층 → 변경 → 종합** 순서가 공통이다(SRE 6·12장 · USE · Azure SRE Agent 예시 순서).
2. **수집은 결정적으로, 해석은 LLM으로** 한다(RCACopilot · D-035). 런북이 조사 품질을 모델보다 크게 좌우한다(CNCF/STCLab 4.6 vs 3.6).
3. **범위 전환(scope switching)을 첫 스텝에 둔다.** 같은 업무의 다른 인스턴스·다른 서버도 지연인지, 한 서비스인지 여러 서비스인지가 내부/외부·전용/공통 자원을 가른다(제니퍼 X-View 백서 · RCACopilot).
4. **응답시간은 분해해서 본다.** DB 시간은 커넥션 획득과 SQL 실행으로, 호스트 CPU는 WAS 프로세스 몫과 그 밖으로 나눈다(제니퍼 JDBC·외부 호출 사례).
5. **TPS·응답시간·액티브의 관계로 「부하형」과 「처리 지연형」을 가른다**(Little's Law · 포화점 모델 · CPU 사례).
6. 단계 요약은 길이 상한을 두고, 근거가 부족하면 「판단 보류(inconclusive)」로 쓴다(RCACopilot · Datadog).
7. 조치는 제안만 하고 사람이 승인한다(Azure Review 모드 · SRE Triage 우선 → 완화 제안을 먼저 낸다).
8. 미매칭 유형에는 공통 폴백 플레이북을 둔다(RCACopilot 신규 범주 24.96%).
9. (v1.2) **모든 알람 유형에 런북 항목이 있어야 하고, 런북은 운영 환경이 바뀌는 속도로 낡는다** → 유형 ↔ 런북 1:1 커버리지 검사 · 검토 기한 · 발생 시 갱신 유도(§3.7 SRE Workbook).
10. (v1.2) **알려진 문제는 「증상 + 환경 + 원인 + 원인 확인법 + 우회」 구조로 저장하고, 원인 확인법을 결정적 검사로 쓴다**(KCS Cause Test · ITIL known error).
11. (v1.2) **신뢰 상태를 둔다** — 검증되지 않은 지식은 배지로 구분하고 일치 판정에 쓰지 않는다(KCS 4상태).
12. (v1.2) **TSG 찾는 시간 자체가 비용이다**(DeepRmd: 완화 시간의 36.3%) → 알람에 런북·알려진 오류를 자동으로 붙인다. 단 기존 TSG는 대부분 그대로 자동화할 수 없다(StepFly 92건 연구) → 사내 문서는 사람이 구조화해 편입하고, LLM 추출은 초안까지만.

### 3.7 (v1.2) 장애 대응 지식 유형 — TSG · 런북/플레이북 · 대응 매뉴얼 · 알려진 오류 · 포스트모템

**용어 정리.** 문헌마다 경계가 다르므로, 이 계획은 다음 다섯 가지로 나눠 쓴다(정의 출처는 각 행).

| 유형 | 문헌 정의 | 이 계획의 대응물 |
|---|---|---|
| **런북 / 알람 플레이북 / TSG** | Google SRE Workbook: *"Playbooks contain high-level instructions on how to respond to automated alerts"* · *"Each alert should have a corresponding playbook entry"* [원문]. kube-prometheus 런북 템플릿 절 = **Meaning · Impact · Diagnosis · Mitigation**, 파일명 = 알람명 [원문]. Microsoft는 같은 것을 TSG라 부른다(아래 연구) | 플레이북 YAML의 `steps`(진단 — 실행형) + `runbook` 블록(의미·영향·완화 서술 · §5.7.2) |
| **플레이북(조정형)** | 런북이 한 작업의 기술 절차라면, 플레이북은 역할·결정 지점·커뮤니케이션을 다루는 상위 문서라는 구분이 업계 공통 서술이다 — 다만 혼용도 흔하다 [2차: Cutover · Squadcast · Cortex] | 자동화 범위 밖 · 사내 매뉴얼 링크(§5.7.1) |
| **인시던트 대응 매뉴얼** | PagerDuty Incident Response: 단계(On-Call · Before · During · After · Crisis)와 역할(Incident Commander · Deputy · Scribe · SME · Customer/Internal Liaison) [공식 문서]. NIST SP 800-61 **Rev. 3(2025-04)**은 Rev. 2(2012)를 대체하고 사고 대응을 CSF 2.0 위험관리 안으로 옮겼다 [공식 문서 — 본문 PDF 미열람 · 보안 사고 중심] | 범위 밖(§1.3) · 매뉴얼 RAG 참고(§5.7.4) |
| **알려진 오류 / 우회(KEDB)** | ITIL 4 문제 관리: 원인이 문서화되고 우회가 있는 문제 = known error · 기록은 KEDB · 문제 통제(problem control)가 문제를 「우회가 딸린 known error」로 바꿀 수 있음 · 영구 해결은 변경 관리로 [2차 — AXELOS 원문 미열람: IT Process Maps · Atlassian · InvGate]. 우회도 원인도 없으면 「known problem」으로 공유한다는 서술도 있다 [2차] | 알려진 오류 레코드(§5.7.3) |
| **지식 문서(KCS)** | KCS v6: 문서 구조 = **Issue**(요청자 표현의 증상) · **Environment**(제품·구성·최근 변경 — 해결 후에도 그대로인 조건 · 증상이 같고 원인이 다른 문서를 가르는 칸) · **Resolution** · **Cause**(선택) · **Cause Test**(원인 확인 절차) · 메타(재사용 수 등) [원문]. 신뢰 상태 = **Work in Progress**(해결 미상) · **Not Validated**(피드백 부족) · **Validated**(완전·재사용 가능) · **Archived**(논리 삭제 · 연결된 사건에서는 열람) · 거버넌스 = Experience Based / Compliance Based [원문] | 레코드 스키마·상태·거버넌스(§5.7.3·§5.7.5) |
| **포스트모템** | SRE Book 15장: 작성 기준(사용자 영향 임계 초과 · 데이터 손실 · 당직 개입(롤백 등) · 해결 시간 임계 초과 · 모니터링 실패) · 비난 없는(blameless) 원칙 · 조직 아카이브에 저장·공유 [원문]. PagerDuty 템플릿 절 = Overview · What Happened · Contributing Factors · Resolution · Impact · Responders · Timeline · How'd We Do? · Action Items · Messaging [공식 문서] | 조사 브리핑(plans/153 저장소)을 포스트모템·KB 초안의 입력으로(§5.7.5) |

**연구 결과 — TSG 자동화(모두 Microsoft 계열 · 논문 초록·본문 직접 확인).**

| 문헌 | 확인한 내용 | 설계 반영 |
|---|---|---|
| Jiang et al., *How to Mitigate the Incident?* (DeepRmd · ESEC/FSE 2020 산업) [원문 초록] | 18개 서비스 실증: TSG 기록이 있는 사건 27.2% · 그중 36.2%가 2회 이상 재발 · **완화 시간의 평균 36.3%를 TSG 찾는 데 씀** · 사건 서술↔TSG 텍스트 유사도 추천으로 Top-1 80.3% | 런북·알려진 오류를 알람에 자동 부착(§5.7.2·3). 단 우리는 유사도가 아니라 유형 키·결정적 조건으로 연결(LLM·임베딩 비결정성 회피 — CLAUDE.md 원칙) |
| Shetty et al., *AutoTSG* (arXiv 2205.13457) [원문 초록] | TSG는 *"siloed, unstructured, and often incomplete"* · 4K+ TSG · 400+ 당직 피드백 · TSG → 실행 워크플로 합성(문장 식별 0.89 · 파싱 P 0.94 / R 0.91) | 진단은 처음부터 구조화된 YAML로 작성 — 자연어 TSG 파싱에 의존하지 않음 |
| Nissist (arXiv 2402.17531) [원문 초록] | TSG와 과거 완화 논의에서 지식 베이스를 만들어 단계별 제안 · TSG는 비정형·불완전해 신규 당직자 부담 · TTM 감소(초록에 수치 없음) | 우회 「제안」과 다음 확인 항목 제시(§5.7.3) · 자동 실행 없음 |
| StepFly (arXiv 2510.10074 · FSE 2026) [원문 본문] | **실제 TSG 92건 분석**: 대개 5~15단계(최대 30) · 독립 단계가 많아 병렬 여지 · 질의 템플릿이 토큰의 35.85% · 품질 문제 = 명확성·정밀성 37.4% · DB 지시 27.2% · 데이터 흐름 20.4% · 표현·구조 9.9% · 제어 흐름 5.1% → *"Most TSGs, in their current form, are not readily suitable for automation"*. 품질 교정 도구(TSG Mentor) → LLM이 실행 DAG·질의 준비 플러그인 추출(DAG F1 ≈94.89%) → 스케줄러 병렬 실행 · 성공률 94.38%(GPT-4.1) | ① 사내 TSG·매뉴얼 편입은 **사람이 구조화**(LLM 추출은 초안 · G-14) ② 플레이북 스텝 병렬 실행 근거 보강(§5.1) ③ 질의는 이름 있는 도구 호출로만(템플릿 자유 질의 금지) |
| FLASH (Microsoft Research 2024) [공식 초록] | 반복 사건 진단 워크플로 에이전트 — 상태 감독 + 과거 실패에서 얻은 교훈(hindsight)을 다음 실행에 반영 · 생산 사건 250+건·5 시나리오에서 정확도 평균 +13.2% | 반복 사건은 알려진 오류 레코드로 고정(§5.7.3) · 교훈 반영은 사람 승인 경로로만(§5.7.5) |
| LLexus (ACM SIGOPS OSR 2024) [공식 초록] | TSG는 길고 암묵지에 의존하며 품질이 고르지 않음 → 에이전트로 TSG 실행 자동화 | 위와 같은 판단 |
| Google SRE Book 「Introduction」 [원문] | 모범 대응을 미리 생각해 「playbook」에 기록하면 즉흥 대응 대비 **MTTR 약 3배 개선** | 런북 필수화 근거(원칙 9) |
| Google SRE Workbook 「On-Call」 [원문] | *"Details in playbooks go out of date at the same rate as production environment changes"* · 페이지가 울리면 당직자가 런북을 갱신 · 매번 같은 명령 목록이면 자동화하라 · 팀이 최소 구조를 합의하고 비대해지는 것을 경계 | 검토 기한·발생 시 갱신 유도(§5.7.5) · 진단 = 플레이북 자동화 · 런북 블록 필드 최소화(§5.7.2) |

**찾지 못한 것([미확인])**: AXELOS ITIL 4 원문의 KEDB 정의(2차 서술만) · Atlassian 자체의 runbook/playbook 정의 페이지 · NIST 800-61r3 본문의 운영 장애 적용 범위 · 국내 금융권 장애 대응 매뉴얼 공개 양식(전자금융감독규정 시행세칙의 비상대책·장애 보고 서식은 G-10 확보 대상).

---

## 4. 알람 유형별 조사 카탈로그 (플레이북 원천)

### 4.0 공통 규칙

| 항목 | 값 | 근거 |
|---|---|---|
| 사건창 | 알람 시각 **−30분 ~ +10분** | [제안] 알람 시각은 증상 시작보다 늦다(Dynatrace 시작 시각 소급 [공식 문서]) · 상관 규칙 기본 창 60분(ServiceNow [공식 문서])보다 좁게 잡아 잡음을 줄인다 · 파일럿 튜닝 |
| 기준선 | **전일 동시간 · 전주 동시간**(은행 업무의 요일·월말 주기 → 전주 비중) · 가능하면 최근 N주 평균 | 관행 [2차] · 피크를 여러 번 측정해 평균(제니퍼 분석 모델 [원문]) · 비중은 [제안] |
| 변경 대조 창 | −24h(필요 시 −7d) · 「상관 후보」로만 표기 | [제안] · SRE 12장 함정(상관≠인과) |
| 소스 간 시각 허용 | ±5분(제니퍼 1분 · 폴스타 시간 집계 차) | [제안] |
| 재발 확인 | 동일 (호스트, 알람명) 최근 20일 | [제안] RCACopilot 재발 분포(93.80% 20일 이내)[원문] 참고 |
| 단계 소견 길이 | 단계당 ≤ 4문장(약 140단어) | RCACopilot 요약 120~140단어[원문] |
| 실시간 전용 도구 | `apm_resource_pool`·`apm_active_services`·`polestar_process_snapshot`은 수집 시각 ≠ 알람 시각이면 「현재값 · 알람 후 N분」 배지 필수 | 도구 계약(§2.2) |

### 4.1 유형 표 — 1차 증상 · 2차 원인 · 교차 소스 · 흔한 원인 · 즉시 대응(제안)

> 스텝 ID는 §5.3 스텝 라이브러리. ★ = 사용자 예시에 명시된 항목.

| 유형 ID | 매칭(결정적) | 스텝 순서 | 흔한 원인 | 대응 제안(사람 승인) |
|---|---|---|---|---|
| **was.latency** WAS 응답 지연 ★ | `resourceType=apm.Instance` + 이벤트 유형/`was_signals` kind = 응답시간·지연 | S0 대상 확정·**범위 비교**(R-A) → S1 골든 시그널★(R-B 부하형/처리 지연형) → S2 JVM·풀★(R-E·R-F) → S3 트랜잭션 Top5 분해★(R-C·R-D) → S4 호스트 시계열★ → S5 연관 알람★ → S6 프로세스 Top★(R-E) → S7 변경 → (S8 DPM) → S9 종합★ | DB 슬로우 쿼리·락 · **커넥션 획득 지연**(풀 미사용·풀 고갈 — 제니퍼 JDBC 사례) · 외부 호출 지연(제니퍼 LDAP 사례) · GC 과다 · 배포 회귀 · 트래픽 급증 | 완화 먼저(SRE Triage) — 스레드 덤프 확보(재기동 **전**) → 원인 계층별: SQL 실행이면 DBA·DPM · 커넥션 획득이면 풀 설정·점유 시간 · 외부면 연계 담당·타임아웃 · GC면 힙 덤프 후 재기동 · 배포 직후면 롤백 검토 |
| **was.error** 에러율 급증 | 동 + 에러·예외 | S0 → S1 → `apm_events(record=error)` 오류 유형 상위 → S3 → S7 변경(우선) → S5 → S9 | 배포 회귀 · 의존 시스템 장애 · 설정·인증서 · 풀 고갈 | 인스턴스 편중이면 격리 · 전체면 공통 의존성·코드 → 롤백 검토 |
| **was.active** 액티브 서비스 급증·행 | 동 + 액티브·스레드 정체 | S0 → S1(TPS 대비 액티브) → `apm_active_services`(장시간 실행·동일 실행 텍스트 묶음) → S2(풀 대기·GC) → S3 → S5 → S9 | 락·데드락 · DB 지연 · 외부 무응답(타임아웃 미설정) · Full GC | 스레드 덤프 확보 → 재기동 검토 · 외부 타임아웃 설정 확인 |
| **was.heap** 힙·OOM·GC 과다 | 동 + 힙·GC·메모리 | S0 → S2(힙 추세 — GC 후 바닥 우상향 여부) → S1(트래픽 비례 여부) → S7 → S4 → S9 | 누수(바닥 상승) vs 힙 과소(트래픽 비례) | 힙 덤프 확보 → 재기동 · 누수면 개발 이관 · 과소면 증설 검토 |
| **was.pool** DB 커넥션 풀 고갈 | 동 + 풀·커넥션 | S0 → S2(풀) → S3(SQL 비중) → (S8 DPM 세션·락) → S1 → S5 → S9 | 슬로우 쿼리·락 · 커넥션 미반환 · 인스턴스 수×max > DB 세션 상한 | 슬로우 쿼리·락 해소 우선 · 풀 상향은 근본 해결 아님을 명시 |
| **host.cpu** CPU 과다 | `classify_alarm_kind=cpu` | S4(cpu, baseline) → S6 프로세스 Top(cpu) → S5 → **역방향 S0′**(호스트 → 그 호스트 WAS 인스턴스 → S1 영향 확인) → S7 → S9 | 폭주 프로세스 · 배치 · 트래픽 | 프로세스 확인 후 담당 이관 · 배치 일정 조정 |
| **host.memory** 메모리·스왑 | `=memory` | S4(memory) → S6(sort=mem) → S0′·S2(JVM 힙이 원인인지) → S5 → S9 | 프로세스 누수 · JVM 힙 과대 설정 · 캐시 | OOM 이력 확인 · 상위 프로세스 담당 이관 |
| **host.fs** 파일시스템 풀 | `=disk`(FS) | S4(filesystem, 증가율 → 고갈 예상 시각 계산) → S5 → S7 → S9 | 로그 급증 · 덤프 파일 · 삭제됐으나 열린 파일 · inode | 로그 정리·로테이션 · 덤프 이동 (명령은 문구만) |
| **host.disk_io** 디스크 I/O | `=disk`(IO) | S4(disk_io) → S6 → S5 → S9 | 백업·배치 · 디스크 장애 | 배치 일정 확인 · 스토리지 담당 |
| **host.process** 프로세스 다운 | `=process` | `polestar_resource_status` → S5 → `polestar_alarm_history` → S0′(WAS면 S1) → S7 → S9 | OOM kill · 호스트 재부팅 · 배포 | 호스트 생존/프로세스만인지 구분 → 재기동 검토 |
| **host.network** 도달 불가 | `=network` | `polestar_resource_status` → `polestar_topology`(같은 구간 다수인지) → S5 → S9 | 호스트 다운 · 네트워크 구간 장애 · 장비 변경 | 단일/구간 판별 → 네트워크 담당 이관 |
| **db.*** DB 지연·락·세션(DPM) | DPM 연동 뒤 | S8 DPM(대기 세션·블로커 체인·장기 실행 쿼리) → S0′(영향 WAS 풀) → S9 | 락·idle in transaction · DDL | 블로커 확인 후 사람 판단 · 자동 kill 금지 |
| **fallback** 미매칭 | 위 전부 불일치 | S4(4종) → S5 → `polestar_alarm_history`(재발) → S7 → S9 | — | 수집 결과 요약만 · 「유형 미정 — 플레이북 추가 후보」 표기 |

DB 3엔진 락 대기 진단은 §3.3 공식 문서로 수집 명세를 정했다(S8 · DPM 연동 시). 현재 버전 제니퍼의 공식 분석 절차와 금융권 성능관리 가이드라인 원문은 [미확인]이다. G-10에서 사내 장애 대응 매뉴얼과 제니퍼 기술지원 문서를 받으면 W6에서 보정한다.

### 4.2 결정적 판독 규칙 (문헌 → 코드)

LLM이 아니라 코드가 계산하는 분류다. 결과는 enum으로 내고 S9 가설 순위와 `when` 분기의 입력으로 쓴다. 게이트웨이가 이미 같은 판정을 내면 그 값을 인용하고 다시 계산하지 않는다(D-274 ⑤). 임계값은 [제안] 초기값이며 플레이북 YAML의 인자로 둔다.

| ID | 규칙 | 입력 | 출력 enum | 근거 |
|---|---|---|---|---|
| **R-A 범위** | 지연 인스턴스가 **같은 호스트의 일부만**이면 「인스턴스 내부」, **호스트 전체**면 「호스트 자원」, **같은 업무의 다른 호스트까지**면 「공통 의존(DB·외부·네트워크)」. Top5가 **한 서비스에 몰리면** 「서비스 전용 자원」, **여러 서비스면** 「공유 자원」 | S0 인스턴스별 판정 · `apm_instance_map(business=…)` 같은 업무 인스턴스 · S3 서비스명 | `scope ∈ {instance, host, shared_dependency}` · `service_spread ∈ {single, many}` | 제니퍼 X-View 백서 [원문] · RCACopilot scope switching [원문] |
| **R-B 부하형/처리 지연형** | 사건창 TPS가 기준선 대비 +X% 이상이고 p95가 비례해 오르면 「부하 증가형」. TPS가 평탄하거나 감소하는데 p95·액티브가 오르면 「처리 지연형(병목)」. TPS 감소 + p95 급등이면 「붕괴 구간」 | S1 TPS·p95·액티브(사건창 vs 기준선) | `load_mode ∈ {load_increase, processing_delay, buckle}` | 제니퍼 분석 모델(포화점·Buckle Zone) [원문] |
| **R-B′ Little 정합** | 관측 액티브 ≫ TPS×평균 응답시간이면 집계 창 불일치나 행(hang)을 의심한다(지표 정합 경고) | S1 | `little_check ∈ {consistent, active_excess}` | 제니퍼 분석 모델 Little's Law [원문] |
| **R-C 지배 계층** | Top5 각 건의 시간 분해(sql·fetch·external·cpu·network) 중 최대 비중 항목, 5건 다수결 | S3 `apm_slow_transactions` | `dominant ∈ {sql, fetch, external, cpu, network, other}` | 제니퍼 분석 모델 응답시간 분해 [원문] |
| **R-D 커넥션 획득 분리** | 1위 건 프로파일에서 커넥션 획득 구간이 SQL 실행보다 크면 「커넥션 획득 지연」으로 바꿔 분류한다(DB 시간을 SQL로 오판하지 않기) | S3 `apm_transaction_profile`(예산 1) | `db_split ∈ {acquire, execute, n/a}` | 제니퍼 JDBC 사례(GET-CONNECTION 15.6s vs 실행 7ms) [원문·OCR] · 풀 지표 해설 [2차] |
| **R-E CPU 귀속** | 호스트 CPU(폴스타)가 높은데 WAS 프로세스 CPU(제니퍼)가 낮으면 「WAS 밖 프로세스」 → S6 Top과 대조. 둘 다 높고 TPS와 비례하지 않으면 「특정 서비스 로직」 → 액티브 서비스 CPU 상위 | S2 프로세스 CPU · S4 host CPU · S1 TPS · S6 | `cpu_owner ∈ {was_load, was_logic, other_process, none}` | 제니퍼 외부 호출 사례(시스템 CPU 50→20% vs Java 15~20%) · CPU 사례(TPS 무관 급등) [원문·OCR] |
| **R-F 힙 모양** | GC 뒤 바닥이 단조 상승하면 「누수 의심」, 주기적 급증이면 「주기성 적재」, TPS 비례면 「힙 과소」 | S2 힙 추세(5분) · S1 TPS | `heap_shape ∈ {leak_suspect, periodic, load_proportional, normal}` | 제니퍼 OOM 사례(3시간 주기) [원문·OCR] · GC overhead [2차] |
| **R-G 호스트 포화** | CPU 사용률 ≥ 80%(4코어 이상) 또는 ≥ 50%(1~2코어)면 응답 영향 가능 구간 · FS는 증가율로 고갈 예상 시각을 계산 | S4 | `host_saturation ∈ {cpu, memory, fs, none}` + `fs_eta` | 제니퍼 분석 모델 CPU-응답시간 [원문] · USE [원문] |
| **R-H 변경 근접** | 사건창 시작 전 24h 안의 변경(제니퍼 배포·폴스타 구성·ITSM)을 시간 근접순으로 정렬해 「상관 후보」로만 표시한다 | S7 | `change_candidates[]` | SRE 12장 "What touched it last" [원문] |

S9 가설 순위 규칙은 이 enum의 조합으로 정한다. 예를 들어 `dominant=sql ∧ db_split=execute ∧ scope=shared_dependency`이면 「업무 DB 지연(공통 의존)」이 1순위다. `db_split=acquire ∧ scope=instance`이면 「인스턴스 커넥션 풀 설정·점유」가 1순위다. 규칙표는 W3에서 `domain/playbook_hypotheses.py`로 코드화한다.

---

## 5. 설계

### 5.0 (v1.6) 역할 분담 — 187 플레이북과 sre_agent(HolmesGPT)

**한 줄**: 187는 sre_agent **바깥의 별도 시스템이 아니다.** sre_agent는 「조사 서비스」(잡 계약·가드·판정·브리핑·감사)이고, 그 안에 조사 엔진이 둘 있게 된다.
- **플레이북 엔진(187 신규)** — 알려진 유형을 **정해진 절차로** 빠짐없이 수집하고, 결정적으로 판독한 뒤, 단계 소견과 종합을 낸다. 「무엇을 어떤 순서로 볼지」를 코드와 YAML이 정한다.
- **HolmesGPT 엔진(기존)** — **열린 질문**을 맡는다. 미지의 원인을 찾고, 플레이북이 「판단 보류」로 남긴 부분을 파고들고, 사용자의 자유 질문에 답한다. 「무엇을 볼지」를 LLM이 정한다.

**구성 요소별 책임 (2026-10-17 실측 기준)**

| 구성 요소 | 지금 하는 일 | 187 이후 하는 일 | 하지 않는 일 |
|---|---|---|---|
| `noise_gate` | 알람 수신 · 티어 판정 · PAGE 조사 트리거 · 조사 저장소 · 통보 | **변경 없음**(페이로드 계약 불변) | 조사 실행 · 원인 판단 |
| `apm_gateway` | 제니퍼 조회 · WAS 값 판정 `was_signals`(유일한 WAS 판정 주체 · D-274 ⑤ · D-335) | 변경 없음(S0~S3·S5·S7의 데이터·판정 공급) | 원인 추론 |
| `mcp_server` | 폴스타 읽기 도구 | 변경 없음(S4~S7 공급) | 판정 |
| sre_agent **잡 계약·dispatcher** | `sre_investigate_alarm`·`sre_diagnose`·`sre_get_investigation` · 중복·동시성·타임아웃(300s)·예산 가드 | 엔진 선택(플래그·매칭)과 예산 분배만 추가 | — |
| sre_agent **사전수집** `evidence_prefetch` | 폴스타 알람·지표 4종·변경 → 결정적 상관(`job.correlation`) | 플레이북 모드에서는 **S4·S5가 같은 함수를 재사용해 대체**(중복 호출 0 · `job.correlation`은 S4·S5 결과로 채움 — G-24) | — |
| sre_agent **지침** `PLAYBOOK_NOTES` | kind별 문구를 HolmesGPT 프롬프트에 부착 | 플래그 off 경로 전용으로 유지. on이면 HolmesGPT 심층의 지침 = YAML `deep_dive.focus` + `prior_findings`(**YAML이 정본**) | — |
| **플레이북 엔진**(187) | 없음 | 유형 매칭 · S0~S8 결정적 수집 · 판독 규칙 R-A~R-H · 단계 소견(LLM 서술) · 결정적 가설 순위 + 종합(LLM 1회) · 런북 · 알려진 오류 일치 | 판정 재정의(게이트웨이 판정을 따른다) · 조치 실행 |
| **HolmesGPT 엔진** | 모든 PAGE 조사의 주 엔진 | ① 플래그 off: 현행 그대로 ② on: **조건부 심층 조사**(G-22) ③ 채팅 진단·추가 조사 요청(`sre_diagnose` · G-21)의 엔진 | 플레이북 가설 순위 덮어쓰기 |
| `severity_judge` | HolmesGPT 도구 원시 출력의 시그니처 → 중요도·`escalate`(→ noise_gate 후속 승격 CW-C) | 입력 = 플레이북 도구 원시 출력 ∪ 심층 도구 출력(G-25). `ImportanceVerdict` 계약 불변 | — |
| `remediation.py` | 시그니처별 일반 조치 후보 | 유지. 표시 순서는 알려진 오류 우회 > 런북 완화 > 일반 조치 후보(§5.7.3) | 실행 |
| `briefing_builder` | 6요소(`summary`·`timeline`·`bottleneck`·`cause`·`recommendation`·`limitations`) 조립 | 6요소를 **플레이북 결과로 채움**(workb·채팅 등 기존 소비처 호환) + 선택 키 `playbook`(단계 섹션·종합·런북·알려진 오류) + `deep_dive`(심층 결과) | — |
| 본체 `src/` | 카드·드로어 표시 · 채팅 `fault_diagnosis`(동기 위임) | 조사 대화(G-18) · 추가 조사 요청 접수·인가·상한(G-21) · 매뉴얼 RAG 인용(G-15) | 조사 실행 |

**잡 종류별 실행 경로 (`SRE_PLAYBOOK_ENABLED`)**

| 잡 | off(현행 · 비트 동일) | on |
|---|---|---|
| 알람 조사(PAGE · 유형 매칭) | 사전수집 → HolmesGPT → severity_judge → 브리핑 | 플레이북(S0~S9) → [심층 조건 충족 시 HolmesGPT(`prior_findings` + 지침)] → severity_judge → 브리핑 |
| 알람 조사(미매칭) | 같음 | fallback 플레이북 → HolmesGPT 심층(항상 — 신규 유형 대응 · RCACopilot 24.96%) |
| 사건 종합 조사(`kind=episode` · D-325 ⑥) | 같음 | 대표(원인) 알람의 유형으로 플레이북 + 기존 `cross_source` 블록 유지(G-23) |
| (v1.9) 사용자 요청 알람 분석(「이 알람 분석」 · G-28) | (경로 없음) | 알람 조사와 같은 경로(유형 매칭 플레이북 + `SRE_DEEP_DIVE_MODE`) · 기준 시각 = 원 알람 발생 시각 · `kind=manual` · `parent_alarm_id` |
| (v1.9) 채팅 증상 질의(대상·증상 결정적 매핑 성립 · G-32) | `fault_diagnosis` 동기 HolmesGPT | 가상 알람 맥락(대상·유형·기준 시각)으로 플레이북 잡 → 비동기 조사 카드(G-33). 매핑 불성립이면 아래 진단 행 |
| 진단(`sre_diagnose` — 채팅 `fault_diagnosis` · G-21 추가 조사) | HolmesGPT | **HolmesGPT 그대로**(자유 질문에는 고정 절차가 맞지 않다). G-21은 `prior_findings`로 이미 수집한 것을 넘겨 중복 조회를 줄인다 |

**심층 조사 실행 조건(G-22 권고 — G-2 세부)**: 아래 중 하나일 때만 실행한다. 1순위 가설이 「지지」이거나 Validated 알려진 오류가 일치하면 생략해 LLM 비용을 줄인다. 생략했다는 사실과 사유는 브리핑에 표시한다.
- 1순위 가설이 「판단 보류」
- 결손 단계가 2개 이상
- fallback 플레이북

예산은 dispatcher 300s 안에서 플레이북 ≤120s, 나머지를 심층에 쓴다.

**충돌 처리**: 심층 결과는 별도 섹션 「심층 조사(HolmesGPT · 가설)」로 보이고, 결정적 가설 순위를 바꾸지 않는다. 결론이 다르면 둘 다 보이고 「엔진 간 불일치」 배지를 단다. 판정은 언제나 게이트웨이·판독 규칙 쪽을 따른다(D-035).

**왜 이렇게 나누나**: 문헌은 「알려진 것은 절차로, 모르는 것은 탐색으로」를 공통으로 말한다. RCACopilot은 핸들러(결정적 수집)와 LLM 요약을 나누고 신규 범주에 대비한다. SRE Workbook은 「매번 같은 명령 목록이면 자동화」를 권한다. CNCF/STCLab은 런북이 있을 때 HolmesGPT 품질이 올랐다(4.6 vs 3.6)고 보고했다. 그래서 플레이북 결과를 HolmesGPT의 출발점으로 넘긴다.

#### 5.0.1 (v1.7 · **v1.8 확정** — G-22·G-26·G-27) 병렬 비교 모드 — 두 엔진을 동시에 돌리고 운영자가 고른다

사용자 제기(2026-10-17): *"플레이북 엔진과 HolmesGPT를 동시에 돌리고 운영자가 둘중에 적정한 것을 보고 판단할 수 있도록 하는 방법에 대해 검토해봐."*

**판단: 채택 권고. 다만 「항상 병렬」로 고정하지 않고 운영 모드로 둔다.** G-22의 선택지에 `parallel`을 더하고, 도입 초기(내부망 shadow · 운영 첫 기간)에는 `parallel`로 운영한다. 그 기간에 쌓인 운영자 선택 통계를 근거로 유형별로 `conditional`로 좁힌다.

**동시 실행의 득실 (2026-10-17 실측 기준)**

| | 득 | 실 |
|---|---|---|
| 판단 | 서로 독립인 두 의견을 받는다. 플레이북 결과에 끌려가지 않는 「두 번째 의견」이 된다 | 새벽 당직에게 결론 두 개는 인지 부담이다 → 같은 틀로 나란히 보이고, 일치 여부를 먼저 보여 줘야 한다 |
| 시간 | 순차(플레이북 ≤120s → 심층) 대비 벽시계 시간이 줄어든다. 플레이북이 먼저 끝나 먼저 보인다 | — |
| LLM 부하 | — | HolmesGPT는 조사당 최대 40 step(`settings.py:26` `max_steps=40` · 넓은 트리아지는 30 step을 넘긴다는 주석)이다. 플레이북은 소견 대상 단계 수 + 종합 1회다(v2.8 — 호출 상한 없음). 모든 PAGE에 두 엔진을 다 돌리면 조사당 LLM 호출이 최대 약 48회가 된다. 운영 FabriX는 과금 대상은 아니지만 처리 용량을 쓴다 |
| 데이터 소스 부하 | — | sre_agent MCP 클라이언트에 결과 캐시가 없다(`mcp_tool_client.py` 실측). 두 엔진이 같은 제니퍼·폴스타 도구를 각자 부르면 호출이 두 배가 되고, 제니퍼 프로파일 예산과도 겹친다 |
| 품질 | — | 결과(`prior_findings`)를 넘기지 못해 순차 방식의 이점 일부를 잃는다. 다만 CNCF/STCLab의 개선(4.6 vs 3.6)은 **런북(지시문)** 효과였다 → 병렬에서도 플레이북 YAML의 정적 지시(`runbook`·`deep_dive.focus`)는 처음부터 넘길 수 있다 |
| 평가 | 같은 사건에서 두 엔진을 비교한 운영 데이터가 생긴다(`plans/154` · W7 보강) | — |

**병렬 모드 설계**

1. **실행**: dispatcher가 한 잡 안에서 두 엔진을 동시에 시작한다. 잡이 둘로 나뉘지 않으므로 같은 호스트 동시성 가드는 그대로다. 전체 300s를 공유하고, 플레이북은 ≤120s 상한을 유지한다.
2. **HolmesGPT 입력**: 알람 페이로드 + 해당 유형 플레이북 YAML의 정적 지시(런북 의미·영향·`deep_dive.focus`)만 넘긴다. 플레이북의 **결과**는 넘기지 않는다(독립성 유지).
3. **잡 단위 도구 결과 캐시(신규)**: 같은 잡 안에서 같은 도구·같은 인자는 한 번만 호출한다. 키는 도구명 + 정규화된 인자다. 제니퍼 프로파일·실시간 단면처럼 비싸거나 시점이 중요한 도구는 캐시로 공유한다. 병렬에서 먼저 시작된 호출은 나중 요청이 결과를 기다려 받는다. 캐시는 잡 종료 시 버린다.
4. **예산 강등**: `investigation_hourly_budget`(기본 None)이나 별도 상한을 넘기면 그 잡은 `conditional`로 자동 강등한다. 브리핑에 「심층 생략 — 예산」 사유를 보인다(침묵 금지).
5. **판정·통보는 바뀌지 않는다**: 중요도 판정 입력은 두 엔진 도구 출력의 합집합이다(G-25). 브리핑 6요소와 workb 즉시·후속 통보는 **플레이북 종합**으로 채운다. 플레이북은 결정적이고 먼저 끝나기 때문이다. HolmesGPT 결과는 도착하면 `deep_dive` 키로 붙고, 화면이 재조회로 채운다.
6. **운영자 비교 화면**(조사 대화 첫 카드 · §5.6.1): 맨 위에 두 엔진의 결론을 **같은 틀로 나란히** 보인다. 틀은 1순위 원인 · 근거(도구·수치) · 대응 제안 · 미결 사항이다.
   - 위에 **일치 표시**를 둔다. 일치 판정은 결정적으로 하며, 비교 대상은 서술이 아니라 근거다. 플레이북의 지배 계층(R-C · DB/외부/CPU/GC/호스트 자원)과 HolmesGPT가 인용한 도구·시그니처(`severity_judge` 매칭 결과)의 계층을 대조해 「같은 계층」·「다른 계층」·「비교 불가」로 표시한다. 대략적인 대조라는 점을 표시한다.
   - 판정 칩(게이트웨이 · 판독 규칙)은 공통으로 한 번만 보인다. 판정은 엔진 선택과 무관하기 때문이다(D-035).
7. **운영자 선택이 하는 일**: 「이 결론이 맞다: 플레이북 / HolmesGPT / 둘 다 / 둘 다 아님」을 기록한다(FeedbackStore 패턴 · 조사 저장소에 `engine_choice`). 선택은 **판정·티어·통보를 바꾸지 않는다**. 쓰임은 셋이다.
   - KB 초안의 원인 기본값(§5.7.5)
   - 유형별 엔진 적중 통계(관리자 「조사 지식」 탭)
   - `parallel` → `conditional` 전환 근거
8. **카드·요약형**: 「종합(플레이북)」 한 줄 + 「HolmesGPT: 같은 계층/다른 계층/진행 중」 칩 하나만 둔다. 비교는 조사 대화에서 본다.

**설정**: `SRE_DEEP_DIVE_MODE` = `off`(플레이북만) | `conditional`(G-22 원안) | `parallel`(이 절). 플래그 `SRE_PLAYBOOK_ENABLED`가 off면 이 값은 읽지 않는다(현행 비트 동일).

**남는 위험**
- 운영자가 서술이 더 그럴듯한 쪽을 고르는 경향이 있을 수 있다(LLM 서술의 설득력 편향). 그래서 근거(인용 도구·수치)를 서술보다 먼저 보이고, 판정은 공통 칸에 고정한다.
- 선택 데이터가 적으면 전환 근거가 약하다. 유형별 최소 표본 수를 정해 두고, 그 전에는 전환하지 않는다(G-27).

### 5.1 배치 — sre_agent 안 「플레이북 모드」 (G-1)

```
noise_gate (PAGE) ──submit(기존 페이로드 그대로)──▶ sre_agent
                                                    ├─ 플레이북 선택기(domain · 결정적)
                                                    ├─ 플레이북 실행기(application · 결정적 · MCP 배치 콜러블 주입)
                                                    │     폴스타 MCP · APM 게이트웨이 MCP · [ITSM] · [DPM]
                                                    ├─ 단계 소견기(LLM · 단계 병렬 · 서술만)
                                                    ├─ 종합기(결정적 가설 순위 + LLM 1회)
                                                    └─ (선택) HolmesGPT 심층 조사 — G-2
                                                  ──▶ briefing(sections[]) ──▶ 조사 저장소·웹 뷰·workb 요약(plans/153)
```

- 이유: sre_agent에 이미 두 MCP 클라이언트·배치 콜러블 주입(`evidence_prefetch.BatchCaller`)·잡 저장소·dispatcher 가드·브리핑 계약·noise_gate 트리거·웹 저장이 있다. noise_gate는 무수정(페이로드 계약 불변 · D-118 MCP 계약만).
- 플래그: `SRE_PLAYBOOK_ENABLED`(sre_agent settings · 기본 off = 현행 비트 동일). on이면 플레이북 매칭 시 플레이북 모드, 미매칭이면 fallback 플레이북(G-3).

### 5.2 플레이북 스키마 (선언형 YAML · `sre_agent/config/playbooks/*.yaml`)

```yaml
id: was.latency
version: 1
match:                       # 결정적 매칭 — 위에서부터 첫 일치
  resource_type: apm.Instance
  any_kind: [latency, response_time]     # was_signals kind 또는 이벤트 유형 정규화 값
window: {before_min: 30, after_min: 10, baseline: [prev_day, prev_week]}
steps:
  - id: S0
    action: scope.resolve_was_targets    # 스텝 라이브러리 참조
    args: {pick: degraded, max_instances: 3}
  - id: S1
    action: apm.golden_signals
    for_each: targets
    narrate: {focus: "p95·TPS·에러율·액티브의 기준선 대비 변화", max_sentences: 4}
  - id: S3
    action: apm.slow_tx_breakdown
    args: {n: 5, profile_top: 1}
    when: "S1.verdict != normal"         # enum 분기(RCACopilot) — 결정적 평가
  ...
  - id: S9
    action: synthesize
response_hints:                         # 대응 제안 문구(실행 아님)
  sql_dominant: "DBA·DPM에서 해당 SQL 실행계획·락 확인"
  external_dominant: "연계 대상 응답·타임아웃 설정 확인"
```

- 스키마는 pydantic으로 검증(기동 시 1회 로드 · 실패 시 기동 경고 + 그 플레이북만 비활성).
- `when`은 작은 결정적 표현식(단계 결과의 enum·수치 비교만 · `eval` 금지 — 화이트리스트 파서).
- 제품 어휘(도구명)는 스텝 라이브러리(application)에만 둔다. 플레이북 YAML은 액션 ID만 쓴다(overfit·벤더 중립).

### 5.3 스텝 라이브러리 (재사용 액션 — RCACopilot scope/query/mitigation 대응)

| ID | 액션 | 호출(배치) | 출력(결정적 구조) | 비고 |
|---|---|---|---|---|
| **S0** `scope.resolve_was_targets` | 알람 인스턴스 → 호스트 → 그 호스트 인스턴스 전체 → 판정이 이상인 것 선별 | `apm_instance_map(query)` → `apm_instance_map(hostname)` → `apm_app_health`×N(병렬) | `host`·`polestar_server`·`targets[{instance, verdict, confidence}]` + **R-A 범위**(같은 업무 인스턴스는 `apm_instance_map(business=…)` 1회 + `apm_app_health` 상한 N) | 정합 신뢰도 낮음/0건이면 「대상 확정 실패」 + 이후 APM 스텝은 알람 인스턴스 단독으로 강등 · 폴스타 서버명 브리지 실패는 `plans/165` F3 의존(§8 위험) |
| S0′ `scope.host_to_was` | 호스트 알람의 역방향 — 호스트의 WAS 인스턴스 영향 확인 | `apm_instance_map(hostname)` → `apm_app_health` | 동 | 제니퍼 미배포 환경이면 생략(결손 아님) |
| **S1** `apm.golden_signals` | p95·TPS·에러율·액티브 + 기준선 | `apm_app_health(reference_time, lookback)` · `apm_period_compare(전일·전주 동시간)` | 표: 지표 × (사건창·전일·전주·변화율) + 게이트웨이 verdict + **R-B·R-B′** | p96 → G-7 |
| **S2** `apm.runtime_and_pool` | 힙·GC%·프로세스 CPU·스레드 + DB 풀 | `apm_runtime_health` · `apm_resource_pool` | 추세 표 + 풀 현재값(배지) + verdict + **R-F**(R-E는 S4·S6 뒤 계산) | 풀은 현재값 전용 |
| **S3** `apm.slow_tx_breakdown` | 트랜잭션 Top5 시간 분해 | `apm_slow_transactions(n=5)` · 1위 `apm_transaction_profile`(예산 1) | 표: 서비스·응답·SQL·외부·CPU·fetch·지배 계층 + **R-C**(지배 계층) · **R-D**(1위 건 커넥션 획득 vs SQL 실행) · R-A 서비스 분산 | 지배 계층이 S9 대응 힌트 키 · 프로파일 안 커넥션 획득 구간의 필드명은 W0 게이트웨이 응답 실측으로 확정 |
| **S4** `host.usage_trend` | CPU·메모리·FS(+IO) 시간대별 | `polestar_metric_trend(kind, granularity=h, reference_time, lookback, baseline_periods)` ×3~4 | 시간대 표 + 결정적 이상(절대 임계·z-score — `correlation.py` 재사용) | FS는 증가율 → 고갈 예상 시각 |
| **S5** `xsource.related_alarms` | 알람 전후 폴스타·제니퍼 알람 | `polestar_incident_alarms` · `apm_events` | 타임라인(선행/동시/후행 · 오프셋) — `correlate()` 재사용 | 상관≠인과 문구 고정 |
| **S6** `host.process_top` | CPU 상위 프로세스 10 | `polestar_process_snapshot(top_n=10, sort=cpu)` | 표 + `captured_at` 배지 | 판정 없음 · 명령행 마스킹은 서버 값만 |
| **S7** `change.recent` | 변경 대조 | `apm_source_changes`·`apm_change_impact`(reference_time만) · `polestar_change_history`(PG) · **ITSM 어댑터** | 변경 목록 + 알람까지 시간 | 「상관 후보」 표기 |
| S8 `dpm.db_health` | DPM | 어댑터(미구현 → `skipped: not_connected`) | — | G-6 |
| **S9** `synthesize` | 종합 | (LLM 1회) | §5.4 | |

공통 규칙: 스텝별 개별 try/except(독립 신호 · Known Mistakes) · 결손은 `status: missing` + 사유로 남기고 다음 스텝 진행 · 스텝 타임아웃 · 전체 예산 = dispatcher 300s 안 · 같은 배치 단계는 병렬.

### 5.4 단계 소견기 · 종합기 (LLM = 서술만)

- **판정은 LLM이 하지 않는다**(D-035 · D-274 ⑤ · D-335). 「이상 있음/없음」 칸은 게이트웨이 `was_signals`·`correlation.py`·플레이북 임계가 채우고, LLM 소견은 그 판정과 표 값을 **인용해 해석**한다. 사용자 원문 「이상이 있는지 없는지를 LLM을 통해 분석」은 「결정적 판정 + LLM 해석 의견」으로 읽는다 → **G-4 확인**.
- 결정적 가드(후처리): 소견이 판정과 반대 극성(판정 normal인데 「이상」 서술 등)이면 소견을 버리고 「판정과 불일치 — 소견 생략」 표기 · 소견에 등장하는 수치가 표에 없으면 제거(환각 수치 가드) · 문장 수 상한.
- 단계 소견: 단계 데이터(요약 직렬화 · 토큰 상한) + 판정 + 플레이북 `narrate.focus` → ≤4문장. 단계 간 독립이라 병렬.
- 종합(S9): 입력 = 단계 판정·소견·지배 계층·타임라인·변경 후보 + **결정적 가설 순위**(규칙: 예 — S3 지배 계층 SQL ∧ S2 풀 고갈 → 「DB 지연 → 풀 고갈 → 응답 지연」 1순위 · 근거 스텝 ID 첨부) + 플레이북 `response_hints`. 출력 = 원인 가설(상태 = 지지/반박/판단 보류 — 이 계획의 [제안] 어휘. 「판단 보류」는 Datadog Bits AI의 inconclusive[공식 문서]와 같은 뜻) · 대응 순서(제안) · 추가 확인 필요 항목. LLM은 가설 순위를 바꾸지 않고 서술한다(G-5).
- LLM 호출 수: ~~단계 소견 ≤7 + 종합 1 = ≤8회/알람~~ → **v2.8(2026-10-22 · 사용자 지시 「호출수 상한을 없애라」): 상한 없음 — `narrate` 지시가 있고 ok인 단계 전부 + 종합 1**(플레이북 단계 수가 곧 상한 · `NarratorLimits.max_step_calls=None` · 명시하면 종전처럼 결정적 우선순위로 자른다). 대안(단일 호출 섹션 출력)은 G-5.

### 5.5 브리핑 계약 확장 (하위 호환)

`briefing`에 선택 키 `playbook` 가산 — 기존 키·소비처 불변.

```json
"playbook": {
  "id": "was.latency", "version": 1, "matched_by": "resource_type+kind",
  "scope": {"alarm_instance": "A1", "host": "...", "targets": [...]},
  "sections": [
    {"step": "S1", "title": "WAS 골든 시그널", "status": "ok|missing|skipped",
     "verdict": "degraded", "verdict_source": "apm_gateway",
     "table": {...}, "badges": ["현재값 · 알람 후 3분"],
     "opinion": "...", "opinion_dropped_reason": null, "tools": ["apm_app_health"]}
  ],
  "synthesis": {"hypotheses": [...], "actions": [...], "followups": [...]},
  "timings": {...}
}
```

### 5.6 표시 (v1.3 상세화 — 2026-10-17 실측: `src/static/js/briefing-view.js` 367줄 · 알람 카드 `app.js:5026` · 관제 드로어 ⓒ `noise.js:1849` · 관리자 `src/static/admin/dashboard.html` 탭 11종)

**원칙**: 새 화면을 늘리지 않고 plans/153의 세 진입점(알람 카드 · 노이즈 관제 드로어 · workb)에 얹는다. 다만 9단계 표(트랜잭션 5건 분해 · 시간대 추이 · 프로세스 10건)는 카드 폭(약 470px)에 들어가지 않아 **상세 화면 1개**를 더한다(G-18). 렌더는 `briefing-view.js`에 `playbook` 렌더 함수를 더해 세 곳이 공용으로 쓴다(D-247 공용 패널). `briefing.playbook` 키가 없으면 종전 렌더 그대로(비트 동일).

| 진입점 | 대상 | 보이는 것 |
|---|---|---|
| ① 알람 카드 「조사 브리핑」(채팅 화면 · 접힘) | 사용자·운영자 | **요약형** — 종합 3줄 · 알려진 문제 일치 배지 · 단계 판정 칩 한 줄(S0~S8) · 진척 · 「전체 보기」 |
| ② 노이즈 관제 드로어 ⓒ | 운영자 | ①과 같은 요약형 + 「전체 보기」 |
| ③ **조사 상세 화면** `/static/investigation.html?id=` (신규 · G-18) | 사용자·운영자(존 검사 — 기존 `/alarm/investigations/*` 인가 재사용) | 전체 섹션 · 표 · 소견 · 런북 · 알려진 문제 · 참고 문서 · 피드백 |
| ④ workb 후속 메시지 | 당직 | 종합 3줄 + ③ 링크(D-137 흐름 · 본문 길이 상한) |
| ⑤ 관리자 「조사 지식」 탭 (신규 · `dashboard.html`) | 관리자 | 런북 커버리지 · 검토 대상(기한 경과·「아님」 누적) · KB 초안 목록·YAML 내보내기 |

**① 요약형 (카드 · 드로어)**

```
조사 브리핑  [진행 중 S4/9]  플레이북 was.latency
종합  ▸ 1순위 DB 커넥션 획득 지연 → 풀 고갈 → 응답 지연 [지지]
      ▸ 대응(제안) 풀 점유 트랜잭션 확인 → 업무팀 통보
알려진 문제  [일치] KE-2026-001 배치 시간대 풀 고갈 (30일 4회)
단계  S0✓ S1[이상] S2[이상] S3[SQL] S4[정상] S5… S6… S7… S8[미연동]
                                              [전체 보기 →]
```

**③ 상세 화면 배치(위 → 아래 · 순서 고정)**

1. **머리**: 알람 원문 · 대상 확정 결과(A1 → 호스트 · 같은 호스트의 지연 인스턴스 · 범위 비교 결과 R-A) · 상태 칩 · 진척 바(S n/9) · 수집 시각.
2. **종합(맨 위 고정)**: 원인 가설 순위표(순위 · 가설 · 상태 지지/반박/판단 보류 · 근거 단계 링크 — 누르면 해당 단계로 스크롤) · 대응 순서(제안 · 위험도·검증·롤백 · human-gated 문구 `HUMAN_GATED_NOTE` 그대로) · 추가 확인 항목.
3. **알려진 문제**(§5.7.3): 일치/후보/판단 보류 카드 — 충족 조건 목록 · 우회 · 재발 수 · 「맞음/아님/우회 효과 없음」 3버튼.
4. **런북**(§5.7.2 · 접힘): 의미 · 영향 · 완화 · 에스컬레이션 · 담당 · 「검토 기한 경과」 배지.
5. **단계 섹션 S0~S8**(아코디언 · 이상 단계는 기본 펼침): 머리 줄 = 단계명 · 판정 칩(정상/이상/판단 보류/결손/미연동/생략) · 판정 출처 배지(`apm_gateway`·결정적 규칙) · 「현재값 · 알람 후 n분」 배지. 본문 = 표 + 소견(「AI 소견」 배지 · 가드로 버려지면 사유) + 사용 도구.
   - S1 골든 시그널: 인스턴스 × (p95 · TPS · 에러율 · 액티브) 현재/기준선(전일·전주)/변화율 — 이상 칸 강조.
   - S2 런타임: 힙·GC 시간 비중·프로세스 CPU(WAS 몫 vs 호스트 전체)·풀 사용/최대/대기.
   - S3 느린 트랜잭션 5건: 행마다 **가로 누적 막대(SQL · 외부 호출 · CPU · 기타)** + 수치 · 지배 계층 칩 · 1위 건 커넥션 획득/SQL 실행 분리.
   - S4 호스트 추이: CPU·메모리·FS 시간대별 표 + 행마다 인라인 SVG 스파크라인(외부 차트 라이브러리 없음) · 알람 시각 세로선 · 임계·z-score 초과 칸 강조.
   - S5 연관 알람: 폴스타·제니퍼 합친 타임라인(알람 시각 기준 ±분 · 소스 칩 · 선후 표시) — plans/153 「두 소스 타임라인」 소표 재사용.
   - S6 프로세스 CPU 상위 10 표(실시간 배지) · S7 변경(제니퍼·폴스타·ITSM — 시간 근접 「상관 후보」) · S8 DPM(「미연동」).
6. **참고 문서**(§5.7.4 · G-15): 문서명·절·발췌·링크 최대 3건 — 「참고 · 판정 무관」 머리말.
7. **꼬리**: 기존 유효/노이즈 피드백 · 「KB 초안 만들기」(운영자만 · §5.7.5) · 「포스트모템 내보내기」(G-16 기준 충족 건만).

#### 5.6.1 (v1.4 · **v1.5 확정** — G-18 (d) · G-19~G-21) 「조사 대화」 — 질의응답 화면의 새 대화로 열어 추가 조사

사용자 제기(2026-10-17): *"조사 상세화면에서 사용자가 다른 내용을 추가로 조사하는 등을 해야 한다. 이런 것들을 위해서 질의 응답 화면에서 새 대화로 열어주는건 어떨지 검토해봐."*

**기존 장치 실측(2026-10-17)** — 새로 만들 것이 적다.

| 필요한 것 | 이미 있는 것 |
|---|---|
| 새 대화 시작 | `startNewChat`(`app.js:4767`) · 새로고침 = 새 대화(D-248 ⑧) |
| 대화 저장·사이드바에서 다시 열기 | `query_thread_turns` 턴 기록(D-248) |
| 대화에 붙은 작업 카드 복원 | `disclosures[].ref.apm_job_id` → 제니퍼 작업 카드 복원(D-300 · `app.js:3182`) — 같은 방식으로 `ref.investigation_id` |
| 알람 → 질의 인계(확인 뒤 전송) | `stageAlarmPrompt`(`app.js:5167` · D-192 — 골드셋 동형 문구만 · `[이대로 조회]/[수정]`) |
| 브리핑 조회·존 검사·사용자 경로 마스킹 | `/api/v1/alarm/investigations/*`(D-325 ③) |
| 대화 속 제니퍼 조회 | 두 구간 비교·기간 순위 채팅(D-313) |
| 대화에서 sre_agent 추가 조사 | `sre_diagnose` 위임 + 조사 카드(D-335 · `plans/155` — **미구현**) |

**판단: 채택 권고.** 다만 「상세 화면 + 새 대화 버튼」 두 개를 만들기보다 **조사 상세를 그 새 대화의 첫 카드로 일원화**한다(G-18 (d)). 근거: ① 상세 화면을 보던 사용자의 다음 행동은 대부분 추가 질의다 — 화면을 한 번 더 건너지 않는다 ② 저장·다시 열기·작업 카드 복원이 D-248·D-300으로 이미 있다 ③ 새 페이지의 인증·존 검사·캐시 버전 관리를 따로 만들지 않는다 ④ 대화 열 폭(최대 920px · `style.css:1144`)이면 9단계 표가 들어간다 ⑤ 알람이 대화에 끼어들지 않게 한 D-182는 **자동 삽입**을 막은 결정이라, 사용자가 눌러 여는 것과 충돌하지 않는다.

**동작(1단계 — 파이프라인 무변경)**
1. **진입**: 카드·드로어 요약형의 「조사 대화로 열기」 · workb 링크 = `/?investigation=<id>`. 알람 카드(같은 페이지)에서는 질의응답 탭으로 전환해 새 대화를 연다. 답변 스트리밍 중이면 버튼을 비활성화한다(진행 중 대화 보호). 노이즈 관제·workb에서는 새 탭으로 연다(D-248 ①과 같은 이유). **(v2.1 개정 · G-43)**: 조사 대화는 질의응답 탭이 아니라 같은 줄의 **「조사 대화」 뷰 탭**으로 연다(질의응답 대화 보존). 노이즈 관제 드로어는 ⓒ 머리 버튼을 상시 두고 열린 포털 창의 그 탭을 연다(새 페이지 0 · 못 찾을 때만 새 탭 `/?conversation=`).
2. **새 대화의 첫 카드**: 조사 브리핑 전체(§5.6 ③의 배치 그대로)를 「고정 카드」로 그린다. 이 카드는 **LLM 대화 문맥이 아니다**. 진행 중이면 15초 재조회로 채운다.
3. **범위 칩 미리 설정**: 브리핑 `scope`의 대상 호스트 폴스타 db_id로 DB 범위 칩을 맞춘다(결정적).
4. **추가 조사 추천 칩**: 단계별로 **대상 이름을 박은** 결정적 문구를 낸다. D-192 규칙에 따라 골드셋 동형 문구만 쓰고, 입력창에 채운 뒤 확인을 받고 보낸다. 예:
   - S4 → 「{host} 서버 최근 7일 CPU 사용률 추이」
   - S1 → 「{instance} 응답시간 지난주 같은 시간대와 비교」(D-313)
   - S6 → 「{host} 프로세스 CPU 상위 10」

   추천 문구마다 골드셋 항목을 W0에서 확보한다. 확보하지 못한 칩은 내지 않는다.
5. **저장**: 첫 질의 턴의 `disclosures`에 `{ref: {investigation_id}}`를 실어 사이드바에서 다시 열면 카드가 복원된다. 질의 없이 닫으면 남지 않는다(D-248 ④ — 완결 턴만). 이 사실을 카드 아래에 한 줄로 알린다.
6. **한계 고지**: 1단계에서 파이프라인은 브리핑을 모른다. 「이 서버」·「그 시간대」 같은 지시어는 풀리지 않는다(CLAUDE.md 「지시어는 식별자가 아니다」). 입력창 안내 문구와 추천 칩으로 대상 이름을 명시하게 유도한다.

**2단계 이후(게이트)**
- **문맥 승계**(G-19): 요청에 `investigation_id`를 실으면 서버가 조사 저장소에서 대상 호스트·인스턴스·사건창·db_id를 **결정적으로** 읽는다. 이를 `context_resolver`의 이전 엔티티와 같은 형태(`_extract_previous_entities` — `src/nodes/context_resolver.py:310`)의 시드로 넣어 지시어를 푼다. `AgentState`·`context_resolver`를 바꾸므로 `[웨이브 회귀 필요]` 대상이다. 브리핑 서술은 넣지 않는다.
- **브리핑 자체에 대한 질문**(G-20): 「왜 SQL 지배로 봤나?」 같은 질문이다. 답은 브리핑의 판정 근거(규칙 id·충족 수치·사용 도구)를 결정적으로 꺼내 보이고, LLM은 그 근거만 서술한다(`src/doc_qa`의 「근거 없으면 LLM 생략」 상태기계와 같은 원칙). 판정을 다시 하지 않는다.
- **sre_agent 추가 조사**(G-21): D-335 `sre_diagnose` 위임(`plans/155`)을 재사용한다. 조건은 다음과 같다.
  - 질문은 코드 템플릿만 쓴다. 대상과 창은 브리핑 `scope`에서 가져온다.
  - 권한은 `HOST_AUTHZ_MODE` 기본 admin_only다.
  - 사용자당 진행 1건, 시간당 3건 상한을 둔다.

  `plans/155` 구현이 선행 조건이다.
  - **정정(v1.4 · 같은 날 실측)**: 처음에는 「자유 텍스트 질문은 D-335 ④와 충돌」이라고 적었으나 과장이었다. D-335 ④의 「템플릿 질문」은 결과 값 클릭 경로에 한정된다. 채팅에는 이미 자유 텍스트 위임 경로 `fault_diagnosis`가 있다(`src/nodes/fault_diagnosis.py` · Plan 64 CW-B · `fault_diagnosis_enabled` 기본 off · 로컬 `.env` 미설정). 이 경로는 사용자 질의 원문을 `question`으로 보내고, 인가 → 가용성 → 위임 순서로 처리하며, 전체 타임아웃(`investigation_total_timeout_seconds` 기본 45s) 안에서 **동기 대기**한다. 그래서 G-21의 쟁점은 「자유 텍스트 허용 여부」가 아니라 **동기 대기 vs 비동기 카드**와 **이전 조사 결과를 넘기는 계약**이다.
  - **G-21 상세 권고**: ① 진입은 조사 대화의 명시 버튼 「추가 조사 요청」(라우터 의도 분류에 맡기지 않음) ② 질문 = 코드 템플릿 + 결정적 「조사 초점」 선택(예: S3 1위 트랜잭션 원인 · DB 측 대기 · 변경 영향 · 같은 업무 다른 서버) + 사용자 보충 한 줄(선택 · 길이 상한 · 마스킹) ③ 대상·사건창은 브리핑 `scope`에서 결정적으로 채움 ④ `sre_diagnose`에 선택 인자 `prior_findings`(구조화 요약 — 판정·판독 enum·이미 쓴 도구 · LLM 서술 제외)를 가산(`target_state` 선례 · 구버전 호출자 fail-open) ⑤ 비동기 접수 + 대화 안 조사 카드(15초 재조회 · D-335 ⑧) ⑥ 저장 = 조사 저장소 `kind=diagnosis` + `owner_sub` + `parent_investigation_id` · 대화 `disclosures` ref로 복원 ⑦ 인가 `authorize_host_investigation`(기본 admin_only) · 가용성 게이트 · 사용자당 진행 1건·시간당 3건·같은 (부모 조사, 초점) 재접수는 기존 id 반환 ⑧ 결과는 「가설 · 참고용」이며 원 플레이북 판정·브리핑을 바꾸지 않음.

**주의**
- 노이즈 관제의 break-glass 운영자 토큰은 질의응답 API를 쓰지 못한다(`noise.js:1863` 주석). 그 경로에서는 드로어 안에서 전체 렌더를 펼쳐 보이는 것으로 대신한다.
- 추가 질의는 일반 질의와 같은 비용·감사·DB 범위 권한을 탄다. 조사 브리핑을 볼 수 있는 존과 질의 가능한 DB 범위가 다른 사용자는 칩이 실패할 수 있으므로, 범위 밖 db_id면 칩을 비활성화하고 사유를 보인다.
- 새로고침하면 `?investigation=`이 남아 있어 같은 조사 대화가 새로 열린다. 열고 나면 `history.replaceState`로 쿼리를 지운다(D-248 ⑧ 「새로고침 = 새 대화」 유지).

**동작**: 진행 중에는 기존 15초 재조회(D-325 ②)로 끝난 단계부터 채운다(G-9 — 수집 중 단계는 스켈레톤). 실패 단계는 사유를 보이고 나머지는 그대로(침묵 금지). 사용자 경로는 기존 마스킹 규칙(D-325 ③)을 섹션 표·소견에도 적용한다. ~~수동 재실행 버튼은 두지 않는다(G-9 · D-325 ⑩).~~ (v1.9) 다시 분석은 §5.6.2 「이 알람 분석」(G-28)으로 한다 — 원 조사를 덮어쓰지 않는 새 조사다.

#### 5.6.2 (v1.9 · **확정** — G-28~G-33) 알람 다시 분석 · 지난 알람 찾기 · 증상 질의 조사

**검토 결과(2026-10-17 실측)** — 요구 두 가지 모두 v1.8까지 공백이었다.

| 요구 | v1.8까지 | 근거 |
|---|---|---|
| 지난 알람 찾기 | 노이즈 콘솔에만 있음(사용자 경로 · 1h~30d · 알람명·서버명 부분일치) | `src/api/routes/noise_dashboard.py` `/noise/decisions` · 결정 저장소 상한 20000줄(`noise_gate/infrastructure/decision_store.py`) |
| 채팅 화면에서 지난 알람 | 없음 — 알람 탭은 세션 한정 · 이력 조회 0건 | D-182 · `app.js`에 `/noise/decisions` 호출 0건 |
| 다시 분석 | 없음 — G-9에서 수동 재실행 버튼 제외(D-325 ⑩ 「후속 계획」) | §9 G-9 |
| 조사가 없는 알람 분석 | 불가 — 조사 대화·G-21은 `investigation_id`가 있어야 시작 | §5.6.1 |
| 증상 질의 → 조사 | `fault_diagnosis` 하나 · HolmesGPT 동기 45s · 기본 off · LLM 의도 분류 · 플레이북 미사용 | `src/nodes/fault_diagnosis.py` · `src/orchestration/intent_planner.py` · §5.0 |

**① 「이 알람 분석」 버튼 (G-28~G-31)**
- 위치: 노이즈 콘솔 목록 행·드로어, 채팅 알람 카드, 조사 대화 고정 카드.
- 대상: 등급 무관(PAGE가 아닌 알람 포함) · 결정 저장소 보존 범위(30일 · 20000줄) 안의 알람. 조사가 있으면 「다시 분석」, 없으면 「분석 요청」으로 표기한다.
- 실행: 자동 알람 조사와 같은 잡이다 — 유형 매칭 플레이북 + `SRE_DEEP_DIVE_MODE`(§5.0.1). 기준 시각은 **원 알람 발생 시각**(사건창도 그 기준). 실시간 단면 스텝(풀·프로세스 Top)은 「현재값 — 알람 시점 아님」 배지를 그대로 단다. 비동기로 접수하고 카드는 15초 재조회로 끝난 단계부터 채운다.
- 저장: 새 조사 레코드 `kind=manual` · `parent_alarm_id` · `owner_sub` · (있으면) `parent_investigation_id`. **원 조사는 덮어쓰지 않는다** — 카드·드로어는 최신을 기본으로 보이고 이전 조사를 「이전 조사 N건」으로 나열한다. `by-alarm` 우선순위(D-325 구현 부기 ②)에 「사용자 요청 최신」을 자기 단독 다음에 넣는다.
- 권한(G-30): 그 알람을 볼 수 있는 **존 범위 사용자**(D-325 ④ 존 검사 · fail-closed 동일). 관리자 전용으로 좁히지 않는다. G-21(보충 한 줄 자유 입력이 있는 추가 조사)의 admin_only와 다르다 — 이 버튼은 자유 입력이 없고 대상이 알람 레코드로 고정된다.
- 상한: D-335 ⑥ 상한(동시 진행 · 시간당 예산)을 자동 조사와 **공유**한다. 같은 알람에 진행 중이거나 최근 10분 안의 사용자 요청 조사가 있으면 새로 띄우지 않고 그 조사에 합류시킨다 [10분은 제안 값]. 상한 초과는 사유를 보이고 거부한다(침묵 대기열 금지).
- 통보: 사용자 요청 조사는 통보 채널(D-137)로 내보내지 않는다. 결과는 요청 화면과 조사 대화에서만 본다.

**② 채팅 화면 「지난 알람」 검색 (G-29)**
- 알람 탭에 기간(1h·24h·7d·30d)·검색어 입력을 둔다. **새 API 없이** 기존 `/noise/decisions` 사용자 경로를 쓴다(존 스코프 · 마스킹 동일).
- 결과 행을 고르면 알람 카드가 펼쳐진다 — 조사가 있으면 요약형 + 「조사 대화로 열기」, 없으면 ①의 「분석 요청」.
- 세션 한정 알람 탭(D-182)의 실시간 목록은 그대로 두고, 검색은 별 칸으로 분리한다.

**③ 채팅 증상 질의 라우팅 (G-32·G-33)**
- 판정은 결정적이다(LLM 의도 분류에 의존하지 않음 — D-035).
  1. 대상 식별: 기존 엔티티 추출 결과의 hostname·서버명·제니퍼 인스턴스명이 정확히 하나로 확정됐는가(지시어는 식별자가 아니다).
  2. 증상 → 유형: 질의 표면어를 플레이북 유형으로 매핑 — 사전은 sre_agent `classify_alarm_kind`(`investigation_guidance.py:158`)와 같은 키워드 + 플레이북 YAML `match.surface_terms`(선택 키 가산)를 정본으로 한다. 결과가 하나일 때만 성립.
  3. 둘 다 성립 → **플레이북 잡**: 대상·유형·기준 시각(질의 속 시각, 없으면 현재)으로 가상 알람 맥락을 만들어 넘긴다. 이 맥락은 알람 저장소·노이즈 게이트에 쓰지 않는다. 저장은 `kind=manual` · `origin=chat` · `parent_alarm_id` 없음.
  4. 하나라도 불성립 → **HolmesGPT**: G-21의 비동기 `sre_diagnose`(질문 원문 · 식별된 대상만).
  5. 질의가 ②에서 고른 알람을 참조하는 경우(알람 카드 「이 알람에 대해 묻기」)는 ①과 같은 알람 조사 경로를 탄다.
- 응답 형식(G-33): 동기 45초 대기 대신 **대화 안 비동기 조사 카드**(W10 카드 재사용) — 어떤 엔진·어떤 판정 경로로 갔는지(「플레이북 was.latency — 대상·증상 일치」 / 「HolmesGPT — 증상 유형 미확정」)를 카드 머리에 결정적으로 표시한다. 끝나면 그대로 조사 대화(§5.6.1)로 이어진다.
- 플래그 `SRE_CHAT_INVESTIGATION_ENABLED`(기본 off) — off면 `fault_diagnosis`가 현행과 비트 동일. 권한은 현행 `fault_diagnosis`의 `authorize_host_investigation`(`HOST_AUTHZ_MODE`)을 그대로 쓰고, 상한은 ①과 공유한다.

**하지 않는 것**: 질의마다 무조건 조사를 띄우지 않는다(조회형 질의는 기존 text2sql 경로) · 가상 알람 맥락을 알람으로 저장하지 않는다 · 사용자 요청 조사로 통보·승격(CW-C)을 일으키지 않는다.

#### 5.6.3 (v2.0 · **확정 2026-10-19 — G-34~G-42**) 「장애 진단」 탭 — 진행 중 조사의 실행 기록·중간 결과 실시간 표시

사용자 제기(2026-10-19): *"조사시 시간이 오래 걸리고 다수의 조사가 진행되어 사용자가 진행상황을 못보고 멈춰있는 것으로 보인다. … 별도의 장애 진단 탭(이벤트 알람 탭 옆에)을 열어서 현재 진행중인 명령어나 api등을 보여주고 중간 단계의 조회내용도 볼 수 있도록 하라. 진행중에 새로운 이벤트를 조사하면 새로운 탭이 열리면서 조사가 진행되도록 하라."*

**ⓐ 실측 — 「멈춘 것처럼」 보이는 원인 (2026-10-19 · `a4514020`)**

| # | 원인 | 근거(`file:line`) | 결과 |
|---|---|---|---|
| F1 | **화면 재조회가 300초에서 멈춘다.** 상한은 카드를 연 시점부터 세는데, sre_agent 전체 타임아웃(300s)은 동시 상한 대기가 끝난 뒤부터 센다. 대기와 `parallel` 모드가 겹치면 조사가 끝나기 전에 화면이 갱신을 멈춘다 | `briefing-view.js:24`(`DEFAULT_LIMIT_S=300`) · `:1245~1262`(`createPoller` limited) · `src/config/alarm.py:384` · `sre_agent/settings.py:115` | 조사는 진행 중인데 화면은 정지 |
| F2 | **동시 상한 대기가 보이지 않는다.** 접수 즉시 `status=running`으로 바뀌고 워커가 세마포어(기본 2)를 기다린다. 대기 중이라는 표지가 응답에 없다 | `investigation_dispatcher.py:479`(running 전이) · `:590`(`with self._semaphore`) · `settings.py:120` | 3번째부터는 「진행 중」인데 아무것도 안 바뀜 |
| F3 | **HolmesGPT 진행이 밖으로 나가지 않는다.** `_StreamDrain`이 `call_stream`의 `START_TOOL`·`TOOL_RESULT`·`AI_MESSAGE`를 받지만 인스턴스에만 쌓고, 잡에는 종결 때 `tool_calls_summary`만 옮긴다. `progress`·`partial_briefing`은 플레이북 잡에만 있다 | `diagnosis.py:189~215` · `investigation_dispatcher.py:723` | 심층·진단 조사는 끝날 때까지 빈 화면 |
| F4 | **플레이북은 단계 단위로만 보인다.** 스텝 안의 개별 도구 호출(도구명·인자·응답 크기·소요)과 소견·종합 LLM 호출은 진척에 없다 | `StepContext.call`(`playbook_steps.py:274`) · `_progress_library`(`investigation_dispatcher.py:844`) | S3 하나가 길면 그 동안 정지처럼 보임 |
| F5 | **진행 중 조사 목록이 없다.** 카드·드로어는 알람 1건씩, 펼쳤을 때만 재조회한다. 여러 조사가 동시에 돌 때 무엇이 어디까지 왔는지 한 곳에서 볼 수 없다. `sre_list_investigations`는 있으나 화면 소비 0건 | `mcp_service.py:475` · `app.js:5397~5432`(펼칠 때만 `panel.open()`) | 「다수 조사」 상황 파악 불가 |

이미 있는 것(재사용): 플레이북 `progress{done,total,current_step}`·`partial_briefing`(W4) · 본체 중계(`investigation_poll.py:47` `LIVE_KEYS` — 저장 안 함) · 사용자 경로 마스킹(`alarm.py:1189` `_attach_live_progress` · D-325 ③) · 비밀값 마스킹(`sre_agent/infrastructure/log_masking.py:173` `mask_secrets`) · holmes 도구 한 줄 표기(`ToolCallResult.description` = 도구별 `get_parameterized_one_liner` — MCP는 `<toolset>: <tool> {params}`, bash는 명령 앞 200자).

**ⓑ sre_agent — 잡 실행 기록(activity)** (W13 · 플래그 `SRE_ACTIVITY_LOG_ENABLED` 기본 off)

> (2026-10-29 · 사용자 결정 · D-341 부기 23) `SRE_ACTIVITY_LOG_ENABLED` 코드 기본값을 **on**으로 개정 — 끄면(`false`) 응답 비트 동일은 그대로 · 본체 `NOISE_INVESTIGATION_LIVE_VIEW_ENABLED`는 기본 off 그대로 · 발췌 가림 표본 확인은 기본 on 이후 내부망 첫 배포 때.

- 잡마다 **메모리 링 버퍼**를 둔다(domain 순수 · 스레드 안전 append · 단조 증가 `seq`). 상한은 이벤트 300건 · 발췌 1건 2KB · AI 메모 1건 2KB · 잡당 합계 256KB [제안 값]. 넘으면 오래된 것부터 버리고 `truncated=true`를 단다. 잡 TTL(1시간 · `investigation_jobs.py:55`)과 함께 사라진다.
- 이벤트: `{seq, at, engine: playbook|holmes|system, kind, step?, tool?, label, status?, elapsed_ms?, rows?, excerpt?, note?}`

  | kind | 기록 지점 | label 예 |
  |---|---|---|
  | `queued`·`started` | `_worker` 세마포어 획득 전후(`investigation_dispatcher.py:590`) · 대기 순번 = 대기 중 잡 수 | 「동시 조사 상한 대기 — 앞 1건」 |
  | `step_start`·`step_end` | `_progress_library.wrap`(`:844~`) | 「S3 느린 트랜잭션 5건」 |
  | `tool_start`·`tool_end` (playbook) | `StepContext.call`(`playbook_steps.py:274`) — 호출 전·후. 캐시 적중은 `status=cached` | `apm_slow_transactions {instance: A1, n: 5}` |
  | `tool_start`·`tool_end` (holmes) | `_StreamDrain`의 `START_TOOL`(도구명만 옴) · `TOOL_RESULT`(`description` 한 줄 · `result.status`) | `polestar: polestar_metric_trend {...}` · `uptime` |
  | `llm_start`·`llm_end` | 소견·종합 호출(`playbook_narrator.py`) · holmes 판단 회차 | 「S3 소견 작성」 · 「HolmesGPT 판단 2회차」 |
  | `ai_note` | holmes `AI_MESSAGE` 본문 · 플레이북 단계 소견 초안 — `note`(2KB 상한) | **G-38 확정**: 운영자 전용 · 「AI 메모 — 검증 전」 배지 |
  | `deep_start`·`deep_end`·`finalize` | 심층 시작·종결 · 브리핑 조립 | 「심층 조사(HolmesGPT) 시작 — parallel」 |

- `label`·`excerpt`·`note`는 **버퍼에 넣기 전에** `mask_secrets`를 거친다. **G-36 확정(가림 처리 후 모두 보임)**: 액션 단위 발췌 제외 목록을 두지 않는다 — 프로세스 명령행·bash 출력도 가린 뒤 발췌로 남긴다. 대신 W13에서 `mask_secrets`가 명령행 비밀값 꼴(`--password=`·`--password <값>`·`-p<값>`(DB 클라이언트)·`token=`·`secret=`·`Authorization:`·URL 사용자정보 `user:pass@`·JDBC `password=`)을 가리는지 표본 테스트로 고정하고, 부족한 꼴은 `log_masking.py`에 더한다(D-339와 같은 모듈 — 로그·실행 기록 공용). 원시 도구 출력 전체는 남기지 않는다(D-324 ⑦ 「원시 도구 기록 미보관」과 같은 원칙 — 발췌만).
- HolmesGPT 경로는 `diagnose_fn`에 선택 인자 `activity`(없으면 무동작)를 더해 `_StreamDrain`에 넘긴다. holmes 내부 코드는 바꾸지 않는다(스트림 소비자 쪽 훅만).
- **계약**: `sre_get_investigation(investigation_id, activity_since: int = -1)` 선택 인자 가산. `-1`(기본·구버전 호출자)이면 응답이 종전과 비트 동일하다. `≥0`이면 `activity: {next_seq, truncated, events: [seq > activity_since]}` · `phase`(queued/collecting/narrating/deep_dive/finalizing) · `queue_position` · `elapsed_s`를 더한다. D-123 상태 어휘는 바꾸지 않는다(대기도 `running` — 구분은 `phase`로). sre_agent는 종전처럼 접수·조회(submit/poll)만 한다 — 브라우저로 미는 일은 본체가 맡는다(D-123 · D-118 경계 불변).

**ⓒ 본체 — 실시간 허브·SSE·권한·보관** (W14 · 플래그 `NOISE_INVESTIGATION_LIVE_VIEW_ENABLED` 기본 off)

- **실시간 허브(G-39 확정 — 서버 푸시)**: 본체 프로세스 안 `InvestigationLiveHub`(기존 `alarm_bus`와 같은 in-process 방식). 미종결 조사마다 폴러 1개가 sre_agent에 `NOISE_INVESTIGATION_LIVE_POLL_SECONDS`(기본 5 [제안]) 간격으로 **한 번만** 묻는다(보는 사람 수와 무관). 받은 이벤트는 조사별 누적 버퍼(ⓑ와 같은 상한)에 쌓고 구독자에게 나눠 보낸다. 기존 `POLL_LIVE`(`investigation_poll.py:47`)는 이 허브로 대체하고, 진행 화면이 아닌 경로의 `POLL_MIN_INTERVAL=10`은 그대로 둔다.
  - 폴러 수명: 구독자(스트림 연결)가 1명 이상이고 조사가 미종결인 동안만 돈다. 종결을 보면 마지막 1회를 더 읽고 멈춘다. 구독자 0이 60초 이어지면 멈추고 버퍼는 10분 뒤 비운다(키 만료 sweep — Known Mistakes 「데몬 in-memory dict」). 폴러 예외는 개별 try/except로 그 조사에만 「갱신 실패 — 사유」 이벤트를 보내고 다음 주기에 다시 묻는다(침묵 정지 금지).
  - 재기동하면 허브는 비어 있고, 다음 구독 때 조사 저장소의 미종결 레코드에서 폴러를 다시 띄운다(커서 0부터 — sre_agent 잡이 살아 있으면 이어서 보인다).
- **SSE 경로**: `GET /api/v1/alarm/investigations/stream?watch=<id>:<seq>,…` 신설 — `/alarm/notifications/stream`(`alarm.py:550~612`)과 같은 틀(`resolve_stream_user` 쿠키 → Authorization 헤더 → 쿼리 토큰 · 25초 ping · `StreamingResponse(text/event-stream)`). 브라우저당 **연결 1개**로 다중화한다 — 하위 탭마다 EventSource를 열면 HTTP/1.1 출처당 동시 연결 6개 상한에 걸려 탭이 무제한(G-40)일 때 깨진다.
  - `event: summary` — **목록 범위의 모든 미종결 조사**의 상태·`phase`·대기 순번·진척·경과(작음 · 바뀔 때만).
  - `event: activity` — **`watch`에 든 조사(열린 하위 탭)만**의 실행 기록 이벤트(`seq` 이후). 탭을 열면 그 조사의 누적분을 한 번에 받아 채우고, 이후 새 이벤트만 받는다(G-42 확정 — 열린 탭의 조사만).
  - `event: partial` — 열린 탭 조사의 `partial_briefing` 변경분 · `event: done` — 종결(최종 브리핑은 기존 `GET /investigations/{id}`로 다시 읽는다).
  - 탭을 열고 닫으면 브라우저가 `watch`(조사별 마지막 `seq` 포함)를 바꿔 다시 연결한다 — 커서가 있어 빠지거나 겹치는 이벤트가 없다. `id:` 필드에 이벤트 키를 실어 EventSource 자동 재연결(`Last-Event-ID`)에도 이어 받는다.
  - `watch`의 각 조사는 연결 때 권한을 검사하고, 권한 없는 id는 조용히 빼지 않고 `event: denied {id}`로 알린다.
  - 노이즈 관제 페이지(break-glass 운영자 토큰 · 헤더 전용)는 기존 `noise_stream`처럼 fetch 스트림으로 같은 경로를 읽는다 — 다만 G-34에 따라 노이즈 쪽 진입은 본 화면 `/?diagnosis=<id>` 새 브라우저 탭이 기본이다(**v2.4 G-46 개정**: 열려 있는 포털 창의 장애 진단 탭이 먼저 · 새 탭은 열린 포털이 없을 때만).
- **목록 범위(G-41 확정)**: **관리자**(`is_admin` — role admin 사용자 · break-glass 운영자 토큰) = **모든 미종결 조사**(자동·요청·채팅 · 존 무관). **사용자** = **내가 채팅으로 시작한 조사**(`owner_sub` = 나 · 채팅 기원 `kind=diagnosis`/증상 라우팅). 사용자가 알람 카드·분석 요청에서 직접 연 조사는 목록에는 없지만 그 하위 탭은 열린다(기존 존 검사 · D-325 ④ fail-closed) — 「목록에 안 보이는 것」과 「열 수 없는 것」을 구분한다.
- **경로 보강**: 기존 `GET /api/v1/alarm/investigations/{id}`(및 `by-alarm`)에 `activity_since` 쿼리를 가산한다(SSE를 못 쓰는 환경·종결 뒤 재조회용 · 없으면 종전과 같다). `GET /api/v1/alarm/investigations/active` 신설 — 위 목록 범위 · 첫 화면과 SSE 끊김 대체에 쓴다. 저장소 미종결 레코드 + 허브 진척(sre_agent 추가 호출 0).
- **노출 범위(G-36 확정)**: 한 줄·상태·소요·행 수 **와 응답 발췌** 모두 사용자·관리자에게 보인다. 사용자 경로는 sre_agent `mask_secrets` 뒤에 **D-325 ③ 텍스트 마스킹을 한 번 더** 거친다(`_investigation_mask_fn` 재사용 · 발췌 포함). **AI 메모(`ai_note`)는 관리자에게만**(G-38) — 사용자 경로에서는 키·이벤트 자체를 뺀다.
- **보관(G-37 확정)**: 종결 시 실행 기록 요약(발췌·AI 메모 제외 — 한 줄·상태·소요·행 수)을 `logs/investigation_activity.jsonl`(회전 상한 · FeedbackStore 방식)에 1행으로 남긴다. sre_agent 잡이 TTL로 사라진 뒤에도 탭에서 「무엇을 조회했나」를 다시 볼 수 있다(발췌는 종결 뒤 잡 TTL까지만).

**ⓓ 화면 — 「장애 진단」 뷰 탭** (W15 · `index.html` 뷰 탭 · 신규 `js/diagnosis-view.js` · `briefing-view.js` 상세형 재사용)

```
[질의응답] [이벤트 알람 ●3] [장애 진단 ⟳2]
┌ 장애 진단 ───────────────────────────────────────────────────────────┐
│ [진행 중 목록] [A1 응답 지연 ⟳ 2:14 ×] [SAE0011 CPU ✓ ×] [DB풀 ⏸대기 ×] … ▾열린 탭 11 │
├──────────────────────────────────────────────────────────────────────┤
│ A1 응답 지연 · 플레이북 was.latency + HolmesGPT(parallel) · 진행 S4/9 · 2분 14초 · ● 실시간 │
│ 단계  S0✓ S1[이상] S2[이상] S3⟳ S4… S5… S6… S7… S8[미연동] │ 심층 ⟳ 판단 2회차 │
├─ 실행 기록 ───────────────────────────── [자동 스크롤 ☑] ┬─ 중간 결과 ──────┤
│ 14:02:11 플레이북 S3 ⟳ apm_slow_transactions {A1, n=5}      │ S1 골든 시그널 표   │
│ 14:02:09 플레이북 S2 ✓ apm_resource_pool {A1} 1.2s 3행 ▸    │ S2 런타임 표        │
│ 14:02:07 HolmesGPT [AI 메모 — 검증 전] 커넥션 대기 의심 ▸   │ (끝난 단계부터)     │  ← 관리자만
│ 14:02:05 HolmesGPT  ✓ polestar_metric_trend {...} 2.8s ▸    │                    │
│ 14:01:58 시스템     ✓ 동시 조사 상한 대기 종료(대기 41초)     │                    │
└──────────────────────────────────────────────────────────────────────┘
  끝나면: 상세형 브리핑 전체 + [조사 대화로 열기] [다시 분석]
```

- **진입(G-34 확정 — 항상 장애 진단 탭)**: 알람 카드 「조사 브리핑」의 [진행 보기] · [분석 요청]/[다시 분석] 접수 직후 · 채팅 조사 카드 · 노이즈 관제 드로어(다른 페이지라 새 브라우저 탭 `/?diagnosis=<id>` — D-248 ①) · workb 링크. 진행 중·종결 모두 「장애 진단」 탭으로 전환하고 그 조사의 하위 탭을 연다. 「조사 대화」는 종결 화면의 [조사 대화로 열기] 버튼으로 남는다(D-341 ⑪의 진입만 개정 — 대화 자체는 유지).
- **새 조사 = 새 하위 탭(G-35 확정 — 장애 진단 화면 안 하위 탭)**: 이미 열린 조사를 다시 누르면 그 탭으로 이동만 한다(중복 0).
- **하위 탭 상한 없음(G-40 확정)**: 자동으로 닫지 않는다. 탭 줄은 가로 스크롤하고 오른쪽 끝 「▾ 열린 탭 n」 메뉴로 전체 목록·「종결된 탭 모두 닫기」를 준다. 탭 하나의 화면 행은 실행 기록 상한(300건)을 넘지 않는다. 탭 수가 늘어도 상세 기록은 열린 탭만 받고(SSE `watch`) 배경 탭은 `summary`만 바뀐다.
- **갱신(G-39 확정 — SSE)**: 연결 1개로 받는다. 종결되면 그 조사의 `watch`를 뺀다. 300초 상한(F1)과 안전 상한이 없다 — 종결이 곧 멈춤이다. 연결 상태를 머리에 보인다(● 실시간 / ◌ 재연결 중 n초 / ⚠ 끊김 — 사유). EventSource 자동 재연결이 3회 연속 실패하면 15초 커서 조회(`/investigations/{id}?activity_since=` · `/active`)로 내려가고 「실시간 연결 끊김 — 15초 간격 조회 중」을 보인다(침묵 강등 금지). 60초 동안 아무 이벤트·ping이 없으면 「마지막 수신 n초 전」을 보인다.
- **실행 기록**: 최신이 위. 행 = 시각 · 엔진 칩 · 단계 · 상태 아이콘(⟳ 진행 / ✓ 성공 / ⚠ 실패 / ↺ 캐시 / ⏸ 대기) · 한 줄 · 소요 · 행 수. ▸를 누르면 발췌를 펼친다(사용자·관리자 모두 · 가림 처리분 · G-36). `parallel`이면 엔진 칩으로 두 엔진을 구분하고 상단에 엔진별 필터를 둔다. AI 메모 행은 관리자에게만, 다른 색 배경 + 「AI 메모 — 검증 전」 배지로 그리고 필터에서 끌 수 있다(G-38 · D-035 — 결론처럼 읽히지 않게).
- **중간 결과**: `partial_briefing.sections`를 `briefing-view.js` 상세형 단계 렌더로 끝난 단계부터 그린다(W5 재사용 · 새 렌더 0). HolmesGPT 단독 조사는 중간 결과 칸에 「도구 결과 n건 수집 — 결론은 종결 후」를 보인다(관리자는 AI 메모가 실행 기록에 섞여 보인다).
- **진행 중 목록**(맨 앞 고정 탭): ⓒ 목록 범위(관리자 전체 · 사용자 내 채팅 조사) 표 — 대상 · 유형 · 엔진 · 상태(대기 순번 포함) · 경과 · 기원(자동/요청/채팅 — 관리자 화면에는 요청자 표시). 행을 누르면 하위 탭을 연다. 뷰 탭 배지 = 목록의 진행 중 건수. 처음엔 `/active`로 그리고 이후 SSE `summary`로 갱신한다.
- **새로고침 복원**: 열린 하위 탭 id 목록을 `sessionStorage`에 둔다(try/catch · 없으면 빈 상태로 시작). 서버 상태는 다시 읽는다.
- **이벤트 알람 탭과의 관계**: 알람 카드 요약형은 그대로 둔다(D-182 — 알람은 대화에 섞지 않음). 진단 탭 = 진행 관찰, 조사 대화 = 끝난 뒤 추가 질의.

**ⓔ 보안·비용**
- 화면은 읽기만 한다. 탭에서 조사를 멈추거나 명령을 실행하는 버튼은 두지 않는다(D-003 · 자동 조치 범위 밖 유지).
- 비밀값은 sre_agent 밖으로 나가기 전에 가린다(`mask_secrets` — 명령행 꼴 표본 테스트로 고정). 사용자 경로는 추가로 D-325 ③ 마스킹을 탄다(발췌 포함). AI 메모는 관리자 경로만. PII 필터는 LLM 전송 경로가 아니므로 대상이 아니다.
- 권한은 SSE 연결 때만이 아니라 `watch` 추가(재연결) 때마다 조사별로 검사한다. 사용자 경로의 `summary`에는 목록 범위 밖 조사가 실리지 않는다(관리자 목록 누출 방지 테스트).
- sre_agent 부하: 미종결 조사 1건당 5초에 MCP poll 1회(보는 사람·탭 수와 무관 — 허브). 현행(카드마다 15초 · 최소 10초 간격)과 같은 차수다. 각 poll이 MCP SSE 연결을 새로 여는 비용(`investigation_poll.py:123`)은 W14에서 측정하고, 크면 간격을 늘리거나 연결 재사용을 별 결정으로 다룬다.
- 본체 부하: 브라우저당 SSE 연결 1개(+ 기존 알람 알림 스트림 1개). 상세 이벤트는 열린 탭 조사만 보낸다(G-42).
- 다중 워커: 허브는 프로세스 메모리다. 본체를 uvicorn 다중 워커로 띄우면 워커마다 폴러가 생긴다(부하 ×워커 수 · 결과는 같다) — `alarm_bus`와 같은 전제(단일 프로세스)를 W14에서 확인해 계획서에 적는다.
- 플래그 off(`SRE_ACTIVITY_LOG_ENABLED`·`NOISE_INVESTIGATION_LIVE_VIEW_ENABLED`)면 탭이 보이지 않고 SSE 경로는 404이며, MCP 응답·API 응답·카드 동작이 종전과 비트 동일하다.

**하지 않는 것**: 프롬프트 원문 노출 · 사용자 경로의 AI 메모 노출(G-38) · 원시 도구 출력 전체 보관 · 조사 취소/재시도 버튼(재분석은 기존 「다시 분석」) · 하위 탭마다 별도 SSE 연결 · 동시 상한(2) 자체 상향(대기 가시화가 먼저 — 운영 측정 뒤 `INVESTIGATION_MAX_CONCURRENT`로 별도 판단).

#### 5.6.4 (v2.5 · **v2.7 확정 2026-10-22 — G-47~G-50**) 「조사 흐름」 — 단계마다 왜 · 무엇을 조회 · 무엇이 나왔나 · 해석 · 다음을 문장으로

사용자 제기(2026-10-22): *"장애 진단 페이지에서 실행기록, 조사결과 등이 나오는데 그 시간의 로그만 나와 뭘하고 있는지가 보이지 않는다. 그 시간에 어떤 작업을 했고 어떤 조사를 했고 어떤 결과가 나와서 분석내용은 뭐다 라는 내용이 표시가 되어야 한다. 예를 들어 cpu가 높아서 해당 서버의 프로세스 리스트를 조사했다. 그리고 프로세스의 cpu 사용량을 조사했다. 그랬더니 was프로세스가 높게 나왔고 제니퍼의 인스턴스를 보니 어떤 인스턴스가 cpu를 많이 쓰고 있고 어떤 쿼리를 사용 중에 있다 등의 예시 처럼 운영자가 봤을때 이해할 수 있는 내용이 표시되어야 한다."*

**ⓐ 실측 — 「로그만 보이는」 원인 (2026-10-22 · `394cc4b1` + 미커밋 W19)**

| # | 원인 | 근거(`file:line`) | 결과 |
|---|---|---|---|
| H1 | **실행 기록 행이 기계어 한 줄이다.** 단계 행 = `"{sid} {ACTION_TITLES[action]}"`, 도구 행 = 도구명+인자 | `investigation_dispatcher.py:1022` · `playbook_steps.py:95`(`ACTION_TITLES`) · `StepContext.call` | 도구 어휘를 알아야 읽힌다 |
| H2 | **단계가 끝나도 「무엇이 나왔나」 문장이 없다.** 중간 결과는 표·판정 배지(`StepResult.to_dict(include_data=False)`)뿐 | `investigation_dispatcher.py:1002~1013`(`publish` — `sections`) | 운영자가 표를 직접 해석해야 한다 |
| H3 | **해석(단계 소견 · LLM)은 모든 단계가 끝난 뒤 한꺼번에, 호출 상한 안의 우선순위 단계만** 만든다 | `playbook_runner.py:808~837`(`_narration_plan` — 상한·우선순위 · 나머지는 `dropped_reason`) · `:986` | 진행 중 해석 0 · 종결 뒤에도 소견 없는 단계가 있다 |
| H4 | **건너뛴 단계가 기록에 안 나온다.** `when` 거짓·예산 소진은 자리표시만 만들고 라이브러리를 거치지 않아 단계 이벤트 0 · 사유는 조건식 원문(`when_false — S0r.status == 'ok' and …`) | `playbook_runner.py:614~627` · `wrap`은 실행 단계만(`investigation_dispatcher.py:1015~`) | 「왜 WAS를 안 봤나」가 안 보인다 |
| H5 | **단계 사이 연결이 없다.** 목적(`narrate.focus` — G-44)은 「무엇을 볼지」이지 「앞 결과 때문에 지금 본다」가 아니다 | `diagnosis-view.js:268` | 서사가 끊긴다 |
| H6 | **조회와 결과가 두 칸에 갈라져 있다** — 실행 기록(좌)·중간 결과(우)를 시각으로 맞춰 봐야 한다 | §5.6.3 ⓓ · `diagnosis-view.js` `dv-log`·`dv-partial` | |
| H7 | **HolmesGPT는 도구 행 + AI 메모(관리자만 · G-38)뿐** — 사용자에게는 무엇을 왜 조회했는지 0 | §5.6.3 ⓑ | |
| H8 | **사용자 예시의 사슬 중 host.cpu 플레이북에 없는 고리** — ① WAS 프로세스 CPU(`apm.runtime_and_pool` 스텝 없음 → R-E `cpu_owner`가 CPU 이상이어도 「WAS 프로세스 CPU 미수집 — 귀속 불가」로 판단 보류) ② 인스턴스의 실행 중 요청별 CPU 시간·SQL 수(`apm.active_services` — 액션은 있으나 host.cpu 미사용) ③ 실행 중 SQL 문(게이트웨이 `apm_active_detail` — 도구는 있으나 액션 없음). S1 골든 시그널은 `S0r` 이상일 때만 돈다 | `sre_agent/config/playbooks/host.cpu.yaml` · `playbook_readings.py:44,564~600` · `playbook_steps.py:67` · `apm_gateway/apm_gateway/application/transaction_tools.py:124`(`cpu_ms`·`sql_count`) · `manage_tools.py:1064` | 「어떤 인스턴스가 CPU를 많이 쓰고 어떤 쿼리를 쓰는가」에 답하지 못한다 |

**ⓑ 목표 화면 — 단계 카드 1장 = 왜 → 조회 → 결과 → 해석 → (다음 카드의 「왜」로 이어짐)**

```
┌ SAE0011 CPU 사용률 95% · 플레이북 host.cpu · 진행 4/8 · 1분 52초 · ● 실시간 ─────────────┐
│ 지금까지  CPU 포화(전일의 2.2배) → java(WAS) 프로세스 78% → 인스턴스 A1이 CPU 대부분       │
│           → A1 실행 중 요청 확인 중 ⟳                                                     │
├─────────────────────────────────────────────────────────────────────────────────────┤
│ ① 14:02:01 CPU 사용률 추이                                             ✓ 1.2s [이상]  │
│   왜    알람 「CPU 사용률 95%」 — 평소보다 얼마나 높은지부터 본다                          │
│   조회  폴스타 · SAE0011 CPU 사용률 시간대별(13:30~14:10 · 전일·전주 같은 시간 비교)        │
│   결과  최대 96% · 전일 같은 시간 43%의 2.2배 · 30분째 90% 이상(포화)          표 보기 ▸  │
│   해석  [AI · 이 단계까지의 근거] 전일 대비 급상승이며 포화가 지속되고 있다 ⓘ주의          │
│ ② 14:02:03 프로세스 CPU 상위                                           ✓ 0.8s         │
│   왜    CPU 포화 → 어느 프로세스가 쓰는지 확인                                             │
│   조회  폴스타 · SAE0011 프로세스 목록(CPU 순 상위 10) · 현재값                            │
│   결과  1위 java(WAS A1) 78% · 2위 sshd 3% — 1위가 상위 10개 합의 85%                       │
│ ③ 14:02:05 WAS 프로세스 CPU                                            ✓ 1.9s [이상]  │
│   왜    1위가 WAS 프로세스 → 제니퍼에서 인스턴스별 CPU 확인                                 │
│   조회  제니퍼 · A1·A2 JVM 런타임(프로세스 CPU·GC·힙 · 사건창)                              │
│   결과  A1 프로세스 CPU 최대 74%(호스트 96%의 대부분) · A2 6% → CPU 주인 = WAS 처리(R-E)    │
│ ④ 14:02:08 실행 중 요청                                                ⟳ 진행 중      │
│   왜    A1이 CPU 대부분 → 지금 무엇을 처리 중인지                                          │
│   조회  제니퍼 · A1 실행 중 요청(CPU 시간 순 상위 5) · 현재값                               │
│ ⑤ 건너뜀  WAS 골든 시그널 — 이 호스트 WAS 인스턴스의 응답·오류가 정상이라 생략               │
│ 위 카드 목록은 [조사 흐름] 탭 · [실행 기록] 탭 = 현행 실행 기록·중간 결과 화면 그대로(G-50) │
└─────────────────────────────────────────────────────────────────────────────────────┘
  끝나면: 「지금까지」 줄 → 종합(가설 1위 · 서술) · 상세형 브리핑 전체 (단계 해석은 단계가 끝날 때 이미 붙어 있다 — 다시 쓰지 않음)
```

**ⓒ 구성 — 결정적 문장 4종 + LLM 해석 1종**

1. **결과 한 줄(`finding` · 결정적 · LLM 0)** — sre_agent `domain/step_findings.py`(순수 · 신규) 액션별 문형 함수 `summarize(action, StepResult, readings) -> str | None`. 표 상위 행·판정·배지·판독 enum만 고정 문형에 꽂는다. **수치는 그 단계 표에 있는 값만**(반올림 규칙만 허용 — 소견 가드와 같은 「표 밖 수치 0」 테스트). 액션 18종(`ACTION_TITLES`) 모두 문형을 둔다(커버리지 테스트) · 결손·실패 단계는 사유 문장. `step_end` 이벤트 `finding` 키 + `partial_briefing.sections[]` `finding` 키로 가산한다.
2. **왜(`because` · 결정적)** — ⓐ 조건 없는 단계: 플레이북 YAML 새 선택 키 `narrate.why`(작성자 한 문장 · 없으면 「{focus}를 확인」) · 첫 단계는 알람 요약을 앞에 붙인다. ⓑ `when`으로 실행된 단계: 조건식 원소별 문형 사전(`when` 화이트리스트 연산 단위 — 예 `S0r.verdict == 'degraded'` → 「{S0r 제목}에서 이상 판정」 · `cpu_owner in ['was_load', …]` → 「CPU 주인 = {enum 한국어}」) + 그 근거 단계의 `finding` 인용. `step_start` 이벤트 `because` 키.
3. **조회(`phrase` · 결정적)** — 도구별 문형 사전 `TOOL_PHRASES`(sre_agent domain · 도구명 → 「{출처} · {대상} {무엇}({조건})」 · 인자에서 대상·시간창·개수만 꺼낸다 · 현재값 전용 도구는 「현재값」 꼬리). 사전에 없는 도구(HolmesGPT의 임의 도구 포함)는 현행 한 줄을 그대로 쓴다(빈칸 0). 현행 한 줄은 「상세 기록」에 남는다. `tool_start`·`tool_end` 이벤트 `phrase` 키.
4. **건너뜀(`step_skip` 이벤트 · 결정적)** — runner가 `when_false`·`budget` 자리표시를 만들 때 이벤트 1건(`run_playbook`에 선택 인자 `activity` — 없으면 무동작). 사유는 ②의 문형 사전으로 문장화(「{근거 단계 제목} 결과가 정상이라 생략」 · 「시간 예산 소진으로 생략」). 조건식 원문은 상세 기록에만.
5. **해석(단계 소견 · LLM)** — **G-47 확정: 단계가 끝날 때마다 작성**(ⓕ 혼합안 · 주의 사항 ⓖ). 「AI 소견」 표지 · 가드 탈락(`dropped_reason`)이면 「소견 없음 — 사유」. 소견은 결과 한 줄을 대체하지 않는다(사실 = 결정적 · 의미 = LLM — D-035).
6. **지금까지 줄(결정적 · 본체 화면 계산 — sre_agent 무변경)** — 판정이 이상인 단계의 `finding` 핵심 구와 판독 enum(R-E 등)을 화살표로 최대 4고리 · 진행 중 단계는 「확인 중 ⟳」. 종결하면 종합(가설 1위 + 서술 첫 문장)으로 바꾼다.
7. **HolmesGPT 카드** — 판단 회차 단위: 「조회」(③ 문형) · 「결과」(상태·행 수 · 발췌 펼침) · 「AI 판단」(`ai_note`) — **G-48 확정: 사용자·관리자 모두에게 「AI 판단 — 검증 전」 배지로 표시**(G-38 개정 · 두 겹 가림 · 브리핑·통보에는 쓰지 않음). `parallel`이면 엔진별 카드 줄을 나눈다(현행 엔진 필터 유지).
8. **화면 배치** — **G-50 확정(대안 1): 조사 하위 탭 안에 「조사 흐름」·「실행 기록」 전환 탭** · 처음 열면 「조사 흐름」(단계 카드 · 표는 카드 안 「표 보기 ▸」) · 「실행 기록」 = 현행 실행 기록·중간 결과 좌우 화면 그대로(엔진 필터·자동 스크롤·발췌 · 새 렌더 0) · 탭을 바꿔도 SSE 연결·커서는 그대로(같은 이벤트를 두 보기로 그린다).
   - **(2026-10-29 · `plans/199` §3.10으로 개정 · D-341 부기 22)** 플래그 `NOISE_INVESTIGATION_SUMMARY_VIEW_ENABLED`가 켜지면 전환 탭 대신 한 화면 세로 배치(진행 중: 한눈에 보기 → 조사 흐름 → [근거 보기] 중간 결과 → 「실행 기록 (n건)」 맨 아래 접힘 섹션) · 꺼지면 위 G-50 화면 그대로.

**ⓓ 플레이북 보강 — 사용자 예시 사슬 잇기 (G-49)**

host.cpu(그리고 같은 사슬을 쓰는 host.process)에 조건부 단계 3개를 더하고 버전을 올린다(`version: 2`).

| 단계 | 액션 | 조건(`when`) | 보이는 결과 |
|---|---|---|---|
| S2 | `apm.runtime_and_pool`(기존 · for_each targets) | `S0r.status == 'ok'`(이 호스트에 WAS가 있음) | 인스턴스별 프로세스 CPU·GC·힙 → **R-E `cpu_owner` 판단 가능**(H8 ①) |
| S3a | `apm.active_services`(기존 액션) | `cpu_owner in ['was_load', 'was_logic']` | CPU 주인 인스턴스의 실행 중 요청 — CPU 시간·SQL 수·경과 상위 5 |
| S3b | `apm.active_sql`(**신규 액션** — `apm_active_detail`) | `S3a.status == 'ok'` | S3a CPU 시간 상위 3건의 실행 중 SQL 문(리터럴 가림) · 애플리케이션 |

- S3a·S3b는 **현재값**이다 — 알람 시각이 아니라 조사 시각 기준이라 배지 「현재값 · 조사 시각 hh:mm」을 달고, 알람과 조사 시각 차이가 10분 [제안] 넘으면 결과 한 줄에 「알람 시점과 다를 수 있음」을 붙인다(§8 위험 「실시간 전용 데이터」).
- SQL 문은 리터럴을 `?`로 바꾸고 `mask_secrets`를 거친다(게이트웨이 `mask_text` 재사용 가능 여부는 W20b에서 실측). 길이 상한 1KB [제안].
- 판독 enum을 참조하는 `when`(S3a)이 근거 단계(S2·S4·S6) 뒤 단계로 배치되는지 `plan_stages` 의존 분석을 W20b에서 확인한다(조건 문법 `in [..]`은 `playbook_expr.py:14`에 있음).
- LLM 호출 수는 늘지 않는다(소견 상한 8 그대로 · 결정적 수집만 +3).

**ⓔ 계약 · 보관 · 가림 · 플래그**

- **계약(가산만)**: 이벤트 키 `finding`·`because`·`phrase` · kind `step_skip` · `partial_briefing.sections[].finding`. 모두 `SRE_ACTIVITY_LOG_ENABLED`가 켜졌을 때만 붙는다 — 꺼지면 MCP 응답 비트 동일(새 플래그 없음).
- **보관(G-37 연장)**: `finding`·`because`·`phrase`는 결정적·가림 처리분이라 종결 요약(`investigation_activity_store.py` `SUMMARY_EVENT_KEYS`)에 넣는다 — 잡 TTL 뒤에도 흐름 카드를 다시 그린다. 해석(소견)은 종결 브리핑에서 읽는다(중복 저장 0).
- **가림**: 새 키 전부 `label`과 같은 두 겹(sre_agent `mask_secrets` → 사용자 경로 `_investigation_mask_fn`)을 탄다 — 허브 중계·사용자 경로 가림 대상 키 목록에 더한다(누락 시 원문 노출 — 표본 테스트로 고정).
- **화면**: 기존 `NOISE_INVESTIGATION_LIVE_VIEW_ENABLED` 아래에서 바꾼다(아직 운영 미투입 · 꺼지면 탭 자체가 없음 — 비트 동일 유지). 새 키가 없는 이벤트(구버전 sre_agent)는 현행 한 줄로 그린다.
- **플레이북 보강(G-49)**은 플레이북 모드가 켜진 환경에만 영향 — YAML 버전 올림으로 브리핑에 판이 남는다.

**ⓕ (v2.6 검토 · 2026-10-22) 문장 생성에 AI(LLM)를 쓰는 안** — 사용자 지시 *"문장을 만드는 것도 AI를 이용하는 것에 대해 검토하라."*

실측 전제: 단계 소견기는 이미 있다 — `PlaybookNarrator.narrate_steps`(`playbook_narrator.py:456` · 병렬 4 · 단계당 타임아웃 20s) · 결정적 가드 `guard_text`(`:364` — ① 극성 역전 버림 ② 표·판정·판독에 없는 수치 버림 ③ 순위·확정 어휘 ④ 문장 수 ⑤ 길이) · 상한 `NarratorLimits.max_step_calls=7` + 종합 1 = 8(수용 기준 6). 가드에 걸리면 **소견 전체를 버린다**(`dropped_reason`) — 고쳐 쓰지 않는다.

| 문장 | AI로 만들면 나아지는 것 | 위험 · 비용 | 판단 |
|---|---|---|---|
| **결과 한 줄** | 표 여러 열을 엮은 자연스러운 문장(「1위가 상위 10개 합의 85%」) · 액션 18종 문형 작성·유지 비용 감소 | 단계마다 호출 +1(보강 host.cpu는 실행 단계 8 — v2.6 당시 상한 7 초과 · v2.8 상한 없음) · 단계 끝 → 문장 표시까지 FabriX 지연(수 초~타임아웃 20s) · 가드 탈락·타임아웃이면 빈칸 · 수치 환각은 가드가 막지만 막으면 문장이 사라진다 | **혼합** — 결정적 한 줄을 즉시 보이고(사실 뼈대 · 항상 있음), AI 문단은 아래 「AI 요약」으로 덧붙인다 |
| **해석** | (이미 AI) | 지금은 종결 뒤 일괄 | **단계 끝 즉시로 앞당겨 결과 요약과 한 호출로 합친다**(아래 권고) |
| **왜(단계 연결)** | 앞 결과를 엮은 매끄러운 이유 | 다음 단계는 YAML `when`이 이미 결정했다. AI가 쓰면 **실제 선택 근거와 다른 이유를 지어낼 수 있다**(사후 합리화) — 「왜 이걸 봤나」가 틀리면 운영자가 조사 논리를 오해한다 | **결정적 유지** — 문장은 실제 조건의 번역이어야 한다 |
| **조회** | 사전 없이 모든 도구 서술 | 도구 호출마다 AI = 호출 폭증(조사 1건 도구 20~40회) · 인자 오기술 | **결정적 유지**(사전 밖 도구는 현행 한 줄) |
| **건너뜀** | — | 「왜」와 같은 사후 합리화 | **결정적 유지** |
| **지금까지 줄** | 고리 요약이 자연스러움 | 진행 중 갱신마다 재생성 = 호출 폭증 · 덜 모인 증거로 결론 단정 | **진행 중 결정적 · 종결 시 종합(이미 AI)** |
| **HolmesGPT 회차 요약** | 문형 사전이 없는 영역(임의 도구·bash) — AI 말고는 문장을 만들 방법이 없다 | 검증 전 서술(G-38) · 회차마다 호출 +1 | **G-48과 함께 결정** — 사용자 노출이면 회차 요약에 가드(①②)를 거는 별 호출, 관리자만이면 `ai_note` 그대로 |

**권고(혼합안) → v2.7 확정(G-47)**: 사실 문장 4종(결과 한 줄·왜·조회·건너뜀)과 진행 중 「지금까지」는 결정적으로 즉시 보인다. **AI는 단계가 끝나는 즉시 그 단계의 「결과 요약 + 해석」 한 문단을 만든다** — 현행 단계 소견 호출을 종결 뒤에서 단계 끝으로 앞당기고 결과 요약까지 맡기는 것이라 **호출 수는 늘지 않는다**(상한 7 그대로). 가드를 통과한 문단만 카드에 「AI 요약 · 이 단계 시점」 표지로 붙고, 탈락·타임아웃·상한 밖 단계는 결정적 문장만 남는다(빈칸 0).
- ~~상한 배분~~(v2.8 상한 없음으로 불필요): 소견 대상 단계는 모두 끝나는 즉시 부른다. 상한을 명시로 다시 줄 때만 이상 판정 단계를 끝난 순서대로 먼저 배분한다.
- 수집과 겹쳐 돈다: 소견 호출은 별 실행기에서 돌고 다음 단계 수집을 막지 않는다. 종합은 마지막 단계 문단까지 기다린 뒤 1회.
- 뒤 증거와의 어긋남: 단계 문단은 그 시점 증거만 본다 — 「이 단계 시점」 표지 · 종합이 최종 판단이다(D-035). 종결 뒤 재작성은 하지 않는다(호출 2배).
- 가림·PII: 단계 표는 지금도 소견 프롬프트로 LLM에 간다 — 새 노출 범주 없음. AI 문단은 `label`과 같은 두 겹 가림을 탄다.
- 결정적 문형 작성 부담 완화: 액션 18종 모두에 정교한 문형을 두지 않는다 — 사용자 예시 사슬 6종(`host.usage_trend`·`host.process_top`·`scope.host_to_was`·`apm.runtime_and_pool`·`apm.active_services`·`apm.active_sql`)과 `apm.slow_tx_breakdown`·`apm.golden_signals`만 정교 문형, 나머지는 일반 문형(「판정 · 표 n행 · 첫 행 핵심 열」).

**전면 AI안(결과·왜·조회·건너뜀까지 AI)을 권하지 않는 이유**: ① 호출 수가 단계 수 × 2 이상(조회 문장은 도구 호출마다)으로 늘어 FabriX 부하·지연이 커진다(v2.8 상한 제거 뒤에도 유효) ② 결과가 AI 응답을 기다려 늦게 뜬다(진행 가시성이라는 원 요건과 반대) ③ 가드 탈락·타임아웃·FabriX 장애 때 화면이 비거나 원문 로그로 돌아간다 ④ 「왜」를 AI가 쓰면 실제 조건과 다른 이유가 나올 수 있다 ⑤ Known Mistakes 「LLM 출력에 정합성을 의존하지 않는다 — 결정적 가드로 후처리」.

**검증**: 가짜 LLM으로 단계 끝 호출 시점·상한 배분·가드 탈락 시 결정적 문장 유지를 단위·종단으로 고정 · MLX는 바뀐 프롬프트 1종 대표 소수 1회(D-127) · FabriX 단계당 지연은 내부망 측정(§10.1 2와 함께).

**ⓖ (v2.7 확정 · G-47) 단계 끝 AI 해석의 주의 사항 — 계획서와 화면에 모두 적는다**

| # | 주의 사항 | 화면 표기(카드 「해석」 줄 · ⓘ주의 펼침) | 설계 대응 |
|---:|---|---|---|
| C1 | **그 단계까지의 근거만 보고 쓴 해석이다** — 뒤 단계 결과로 달라질 수 있다 | 표지 「AI · 이 단계까지의 근거」 · ⓘ「뒤 단계 결과에 따라 달라질 수 있습니다. 최종 판단은 종합을 따르세요」 | 프롬프트에 「앞 단계 결과 한 줄」만 넣고 뒤 단계 추측 금지 지시 · 종결 뒤 재작성 0(호출 2배 방지) |
| C2 | **원인 판정이 아니다** — 원인 순위·결론은 종합(가설 순위 = 코드값)이다(D-035) | ⓘ「원인 확정이 아닌 단계 해석입니다」 | 가드 ③ 순위·확정 어휘 차단을 단계 해석에도 적용 |
| C3 | **종합과 어긋나 보이면 종합을 따른다** | 종결 뒤 판정 극성이 종합 1위 가설과 반대인 단계 해석에 「종합과 다름 — 종합 기준」 칩(결정적 비교) | 단계 판정(verdict)·종합 가설 상태로 비교 — LLM 0 |
| C4 | **현재값 단계(프로세스 상위·실행 중 요청·실행 중 SQL)는 조사 시각 기준이다** — 알람 시점과 다를 수 있다 | 「현재값 · 조사 시각 hh:mm」 배지 · 알람과 10분 [제안] 넘게 차이 나면 ⓘ에 「알람 시점과 다를 수 있음」 | 소견 프롬프트에 「현재값」 명시(기존 실시간 배지 규칙) |
| C5 | **해석이 없는 단계가 있다** — 가드 탈락(표 밖 수치·극성 반대)·타임아웃(20s) | 「AI 해석 없음 — {사유}」(빈칸 아님) · 사실 문장(결과 한 줄)은 그대로 | 호출 상한 없음(v2.8) — 소견 대상 단계 전부 호출 |
| C6 | **수치는 표에 있는 값만 쓴다** — 그래도 표 해석의 오독은 있을 수 있다 | 「표 보기 ▸」로 원 표 확인 유도 | 가드 ② 환각 수치 버림 |
| C7 | **사용자에게도 보인다**(G-48과 함께) — 가림이 놓친 값이 문장에 실릴 수 있다 | — | 두 겹 가림(sre_agent `mask_secrets` → 사용자 경로 `_investigation_mask_fn`) · 내부망 표본 확인(§10.1 7) |
| C8 | **지연·비용** — 단계가 끝난 뒤 해석이 붙기까지 FabriX 응답 시간만큼 늦다 · 호출은 소견 대상 단계 수 + 종합 1(v2.8 상한 없음) | 해석 자리에 「AI 해석 작성 중 ⟳」 | 별 실행기 — 다음 단계 수집을 막지 않음 · FabriX 지연은 내부망 측정(§10.1 2) · MLX 수치는 로직 확인용 |

같은 주의 사항을 HolmesGPT 「AI 판단」(G-48)에도 적용한다 — 표지 「AI 판단 — 검증 전」 · ⓘ에 C1·C2·C7. HolmesGPT 판단 문장은 단계 해석과 달리 가드를 거치지 않은 원문(가림만)이므로 ⓘ에 「자동 검사를 거치지 않은 AI 중간 판단입니다」를 더한다.

**하지 않는 것**: LLM이 결과 한 줄을 쓰게 하기(수치 환각 — 결정적 문형만) · 조사 순서를 LLM이 그때그때 고르기(플레이북은 선언형 조건부 유지 — 적응형은 HolmesGPT 담당) · 실행 중 SQL 원문(리터럴 포함) 노출 · 새 SSE 이벤트 종류(기존 `activity`·`partial`에 키만 가산).

#### 5.6.5 (v3.0 · **확정 2026-10-22 — 사용자 「권고에 맞게 계획서에 기록하고 구현하라」**) W21 수집 타임아웃 개선

**ⓐ 증상 (내부망 실화면 · was.latency · 제니퍼 소스 `cm_prd` · 도메인 10020)** — S0 대상 확정 결손(「스텝 타임아웃 30.0s」 · `apm_instance_map`) · S1 골든 시그널 결손(30.0s) · S2 정상이나 일부 결손(실시간 조회 `apm_api_error` 도메인 10020 · `apm_resource_pool` `source_unavailable`) + 「소견기 타임아웃 20s」 · S3 완료(1위 73초 · SQL 0)이나 「소견기 타임아웃 20s」 · `when` 판정 불가로 실행 · S4 결손(폴스타 서버 미확정 — APM 페이로드에 `meta.episode.infra_db_ids` 없고 설정된 소스 없음 — `plans/165` F3).

**ⓑ 원인 (코드 실측 · `394cc4b1` + 미커밋 W19·W20)**

| # | 원인 | 위치 |
|---:|---|---|
| 1 | 배치 콜러블이 SSE 세션 1개에서 calls를 **순차** 호출(호출당 `evidence_prefetch_timeout_seconds` 20s) · S0은 instance_map 1 + host·business 2 + 비교 인스턴스 app_health 최대 8 + 기준선 2 = 최대 13건 순차 | `sre_agent/sre_agent/infrastructure/mcp_tool_client.py` `run_tool_batch_async` · `application/playbook_steps.py` `resolve_was_targets` |
| 2 | 스텝 타임아웃 30s 고정 · 타임아웃이면 받아 둔 응답을 버리고 사유는 「스텝 타임아웃 30.0s」뿐 | `application/playbook_runner.py` `DEFAULT_STEP_TIMEOUT_S` · `_run` |
| 3 | 타임아웃 스텝의 스레드가 계속 돌며 새 호출을 내 다음 단계와 게이트웨이를 경합(`fut.cancel()`은 실행 중이면 무효) | `_run` |
| 4 | 단계 소견 타임아웃 20s 코드 고정(설정 없음) · `_narrator`가 남은 시간이 적으면 더 줄인다 | `application/playbook_narrator.py` `NarratorLimits` · `investigation_dispatcher.py` `_narrator` |
| 5 | `resolve_polestar_source(payload, configured)`의 `configured`를 아무도 넘기지 않는다(설정 미배선) | `playbook_runner.py` `resolve_polestar_source` · dispatcher `_collect_playbook` |

**ⓒ 개선 (권고안 = 확정)**

| # | 개선 | 내용 | 할 곳 |
|---|---|---|---|
| P0 | 호출별 소요 실측 | 실행 기록 탭·게이트웨이 로그로 S0·S1 호출별 소요를 잰다 — 코드 작업 없음 | 내부망(§10.1) |
| P1 | 배치 동시 호출 | 한 MCP 세션 안에서 상한 있는 동시 `call_tool`(asyncio Semaphore) · 응답 순서 = calls 순서 · 호출별 오류 격리 유지 · 폴스타·APM 콜러블 둘 다 · 설정 `SRE_TOOL_BATCH_CONCURRENCY`(기본 **4** — 사용자 확정 · 1 = 종전 순차) | sre_agent |
| P2 | S0 호출 축소 | 알람 호스트 인스턴스의 app_health를 `apm_app_health(hostname=…)` 1건으로 묶고 응답을 인스턴스별로 나눠 기존 판정 입력(`_window_stats`·`_signals`·`target_verdict`)에 그대로 넣는다 — 판정 로직 재구현 금지(D-195 ②·D-274 ⑤) · 필요한 필드가 없으면 미적용·사유 기록 | sre_agent |
| P3 | 단계별 타임아웃 | 플레이북 스텝 선택 키 `timeout_s`(미지정 = 기본 30s) · was.latency S0 = 60 · host.cpu·host.process S0r = 60 · 남은 예산(`sre_playbook_budget_s` 120s)보다 길지 않다 | sre_agent · YAML |
| P4 | 부분 결과 보존 + 중단 | 타임아웃 사유에 진행 표시(「스텝 타임아웃 60s — 요청 13건 중 9건 완료 · 대기 apm_app_health」) · 타임아웃 스텝은 이후 새 호출을 내지 않는다(취소 이벤트를 `StepContext.call`이 확인) · S0 타임아웃이면 범위는 초기 범위(알람 인스턴스 단독)로 강등 — 부분 응답으로 범위를 정하지 않는다(비교 집합이 불완전해 오판 위험) | sre_agent |
| P5 | 소견기 타임아웃 설정화 | `SRE_NARRATOR_STEP_TIMEOUT_S`(기본 **45s**) → `NarratorLimits.step_timeout_s` · `_narrator` 축소 로직·조사 전체 300s 상한 유지 · W20 단계 끝 해석은 별 실행기라 수집을 늦추지 않는다 | sre_agent |
| P6 | 폴스타 소스 대체 매핑 | `SRE_PLAYBOOK_POLESTAR_SOURCE_MAP`(APM 소스 ID → 폴스타 db_id JSON · 기본 빈 매핑 = 현행) · 페이로드 `infra_db_ids`가 우선 · 매핑으로 정하면 「설정 매핑으로 대체(cm_prd→polestar_cm_gp)」와 존 오조회 위험을 사유·화면에 표시 · hostname 브리지(`plans/165` F3)는 범위 밖 | sre_agent |
| P7 | 게이트웨이 실패 진단 | 실시간 API(도메인 10020) `apm_api_error` · `apm_resource_pool` `source_unavailable` 원인 진단 — 코드 작업 없음 | 내부망(§10.1) |

P5 기본값 45s 근거: 조사 전체 300s − 수집 예산 120s = 소견에 최소 180s가 남는다. 소견 동시 4 × 약 3회차 × 45s = 135s + 종합 30s는 그 안에 들어가지만, 60s면 3 × 60 = 180s로 종합 여유가 없다.

**ⓓ 구현 결과 (2026-10-23 · 세션 시작 `394cc4b1` · 미커밋)**

| # | 처리 | 내용 |
|---|---|---|
| P1 | **적용** | `infrastructure/mcp_tool_client.py` `run_tool_batch_async(concurrency=)` — Semaphore + gather · 순서 유지 · 호출별 격리 · ≤ 1이면 종전 순차 루프. 동시 요청 안전성 실측: mcp 1.25.0 `shared/session.py` `send_request`가 요청 id별 응답 스트림(`_response_streams[request_id]`)을 등록하고 `_handle_response`가 id로 배달 — 잠금 없음. 사전수집(evidence_prefetch)도 같은 콜러블이라 함께 적용된다 |
| P2 | **미적용** | 게이트웨이 `apm_app_health(hostname=…)` 응답은 인스턴스별 행(`inst_meta` + `window`)과 instance_id가 달린 신호를 내므로 모양은 맞다. 그러나 잡 캐시는 (도구, 인자) 키라 종전엔 S0의 인스턴스별 호출이 S1을 캐시 적중시켰다. 가짜 콜러블 비교(was.* 픽스처 10종 × `for_each_max` 2): S0 8 → 7건, S0+S1 합계는 20회 중 18회 동일·2회(latency_sql·case2_ldap_external · 운영 설정 `for_each_max` 미지정) 18 → 19건으로 늘어 중단 기준(합계 증가 시 미적용)에 걸렸다. 호스트 인스턴스가 대상보다 많은 실제 13건 사례에서는 줄겠지만, 순증을 없애려면 S1이 S0 결과를 재사용해야 해 범위 밖이다. P1(동시 4)로 S0 13건이 4회차로 줄어 효과가 대부분 흡수된다 — P0 실측 뒤 재검토(§10.1 18) |
| P3 | **적용** | `domain/playbook.py` `StepSpec.timeout_s`(선택 · > 0) · runner `step_wait_s` = min(스텝 값 또는 기본 30s, 남은 예산) · 스텝마다 제출 시각부터 실시간으로 잰다(앞 스텝 대기가 누적되지 않음) · YAML was.latency S0 · host.cpu·host.process S0r = 60 · 버전 was.latency 1 → 2 · host.cpu·host.process 2 → 3 |
| P4 | **적용**(verifier Major 2건 교정 반영) | `StepContext` 중단 신호·진행 계수(요청·완료·대기 도구) · 중단 뒤 `call`은 부르지도 기록하지도 않고 「스텝 타임아웃으로 중단」 오류 · runner `_timed_out` 사유 「스텝 타임아웃 60s — 요청 13건 중 9건 완료 · 대기 apm_app_health」 꼴 · 그 순간 끝나 있으면 결과를 쓴다 · 대기 상한은 스텝이 실제로 시작한 시각부터 잰다(큐 대기 제외 · 예산 안에 시작 못 하면 「스텝 미시작」) · 스레드 풀은 단계마다 새로 만들어 중단된 스레드가 다음 단계 워커를 붙잡지 않는다 · 중단 뒤 돌아온 배치는 기록·적재하지 않는다 · **한계**: 중단은 배치 단위다 — 이미 보낸 배치 안의 남은 호출(동시 상한 대기분)은 타임아웃 뒤에도 게이트웨이로 나간다 · dispatcher가 단계 끝을 스텝당 1회만 내고(타임아웃 즉시 `step_end`·부분 브리핑) 늦게 끝난 결과는 기록·해석 제출 0 · 사유는 실행 기록 `step_end.finding`(「수집 결손 — 사유」)·부분 브리핑·최종 섹션 `reason`에 실리고 화면(`diagnosis-view.js`)은 기존 필드로 그대로 보인다(JS 무수정). S0 타임아웃이면 범위는 초기 범위(알람 인스턴스 단독) |
| P5 | **적용** | `interface/mcp_service.py` `NarratorLimits(step_timeout_s=settings.sre_narrator_step_timeout_s)` · W20 단계 끝 해석 실행기는 같은 `wiring.narrator_limits`를 쓴다 · `_narrator` 축소 유지 |
| P6 | **적용** | APM 소스 ID 경로 실측: 게이트웨이 `apm.source_id` → noise_gate `apm_trigger_hints` → 잡 payload `meta.hints.source_id`(`event`에는 `dbId`만). runner `mapped_polestar_source`·dispatcher `_collect_playbook` → `run_playbook(polestar_source=, polestar_source_basis=)` · 우선순위 `infra_db_ids[0]` → 매핑 → 미확정 · 매핑을 실제로 쓴 경우에만 근거 문장이 run notes·S0·S4 사유·`scope.polestar_basis`에 실린다(값 없으면 키 없음 = 종전 모양) · 폴스타 알람 경로 불변 |

새 설정(sre_agent `.env` · 접두사 없음): `SRE_TOOL_BATCH_CONCURRENCY`=4(1~8 · 실효 동시 호출 = 스텝 워커 4 × 이 값 × 동시 조사 수) · `SRE_NARRATOR_STEP_TIMEOUT_S`=45 · `SRE_PLAYBOOK_POLESTAR_SOURCE_MAP`={}(JSON). `sre_agent/.env.example`·관리자 매뉴얼 A-77 표 갱신.

#### 5.6.6 (v3.1 · **사용자 지시 2026-10-23**) W22 조사 시간 사슬 상향·정합

**ⓐ 지시** — *"원인 분석을 위한 각종 타임아웃이 너무 짧아 끊기는 경우가 많다. 기본적인 타임아웃 시간을 조절하거나 대책을 마련하라."* + *"제니퍼 api 호출건은 20초말고 30초로 늘리자."* 배경: W21 적용 뒤에도 내부망(FabriX · 제니퍼 `cm_prd`) 실화면에서 S0·S1 스텝 타임아웃·소견기 타임아웃이 남았다. 이번은 **기본값 상향 + 사슬 정합 + 결손 스텝 재시도**다. 신규 플래그가 아니라 기존 값 상향이라 「기본 off」 원칙 대상이 아니며(재시도 스위치만 예외 — 아래), 사용자 확정 값은 `.env`가 아니라 코드 기본값으로 바꿨다.

**ⓑ 시간 사슬 (안쪽 → 바깥 · 실제 적용 값)**

| 층 | 위치 | 전 | 후 |
|---|---|---:|---:|
| 제니퍼 API 1건(httpx 단계별 상한) | `apm_gateway/apm_gateway/config.py` `JENNIFER_API_TIMEOUT_SECONDS`(소스별 `JENNIFER_<ID>_API_TIMEOUT_SECONDS`는 전역 승계) | 10s | **30s** |
| sre_agent MCP 도구 1건 | `sre_agent/sre_agent/settings.py` `evidence_prefetch_timeout_seconds`(단일 출처 `TOOL_CALL_TIMEOUT_S` · `mcp_tool_client.py` 폴백 같은 상수) | 20s | **60s** |
| 플레이북 스텝 기본 | `application/playbook_runner.py` `DEFAULT_STEP_TIMEOUT_S` | 30s | **90s** |
| S0 / S0r(대상 확정) | YAML `timeout_s` — was.latency(v3) · host.cpu·host.process(v4) | 60s | **120s** |
| 플레이북 수집 예산 | `sre_playbook_budget_s` · runner `DEFAULT_BUDGET_S` | 120s | **240s** |
| 소견기 단계 | `sre_narrator_step_timeout_s` | 45s | **60s** |
| 소견기 종합 | `sre_narrator_synthesis_timeout_s`(**신설** · 종전 `NarratorLimits` 코드 고정) | 30s | **90s** |
| 조사 1건 전체 | `investigation_timeout_seconds` | 300s | **600s** |
| 사전수집 타임박스 | dispatcher `max(60, 조사/2)`(무변경) | 150s | 300s |
| 도구 캐시 대기 | dispatcher 잡 캐시 `ToolResultCache(wait_timeout_s=조사 전체)` · 모듈 기본 `DEFAULT_WAIT_TIMEOUT_S` | 300s 고정 | **조사 상한(600s)** |
| 본체 후속 poll | `src/config/alarm.py` `investigation_followup_timeout_seconds` · 폴백(`routes/alarm.py` · `alarm_schemas.py` · `noise_gate/.../alarm_notifier.py`) · 웹 재조회 상한 `briefing-view.js` `DEFAULT_LIMIT_S`(관제 드로어는 capabilities를 읽지 않아 이 값을 쓴다) | 300s | **1200s**(2026-10-23 main 병합 뒤 사용자 지시 — D-344 ④ 「후속 대기 > 조사 상한 · 권장 2배」 정렬 · 처음 600s) |
| 본체 즉시 경로 | `investigation_mcp_call_timeout_seconds` 10 · `investigation_total_timeout_seconds` 45 | — | 무변경(즉시 통보 경로 · 비동기 후속이 본 경로) |
| 화면 SSE | `investigation_live_hub.py` `STREAM_MAX_SECONDS` 3600 · 이벤트 300건 | — | 무변경 |

관계: 제니퍼 API 30 < MCP 1건 60 < 스텝 90 ≤ S0 120 ≤ 수집 240 < 조사 600 · 수집 240 + 소견 60 + 종합 90 = 390 ≤ 600 · 수집 뒤 남는 360s 안에 소견 동시 4 × 약 3회차 × 60 = 180 + 종합 90 = 270.

**MCP 1건 60s 근거(게이트웨이 코드 실측 · 2026-10-23)** — 게이트웨이 도구 하나가 부르는 제니퍼 API는 전부 **순차**(병렬·재시도 없음)이고 소스당 초당 5회 스로틀을 동시 MCP 호출이 나눠 쓴다. 호출 수(대상 1개 기준): `apm_instance_map`(query/hostname) 0(인벤토리 캐시) · `apm_active_services`·`apm_source_changes`·`apm_status_stats` 1~2 · `apm_period_compare`·`apm_resource_pool`·`apm_events` 2 · `apm_runtime_health`·`apm_transaction_profile` 3 · **`apm_app_health`(과거 시각)·`apm_slow_transactions` 41(X-View 조각 40 + 시 단위 1)** · `apm_change_impact`(24h · n 미지정) 1 + 변경 건수 × 122. 「최악 횟수 × 30s」로는 X-View 도구를 덮을 상한이 없다(41 × 30 = 1230s). 60s는 호출 1~3회 도구는 덮고 X-View 도구는 대기 포함 건당 약 1.4s 이하일 때 버틴다. 90으로 올리면 스텝 기본 90과 같아져 「도구 < 스텝」이 깨진다 → **60s 유지** · X-View 도구 대책은 별 결정(§10.1 21).

**ⓒ 대책 (구현)**

| # | 대책 | 내용 |
|---|---|---|
| 1 | **사슬 정합 검사** | `settings.py` `timechain_warnings()`(순수 함수) · `interface/mcp_service.py` `_log_timechain` — 플레이북 배선 적재 직후 1회. 관계 5종(MCP 1건 < 스텝 기본 · 스텝 기본 ≤ 수집 예산 · YAML 스텝 `timeout_s` 최대 ≤ 수집 예산 · 수집 예산 < 조사 전체 · 수집 + 소견 + 종합 ≤ 조사 전체)이 어긋나면 **경고 1줄**(값 나열), 정합이면 info 1줄 「조사 시간 사슬」. 기동은 막지 않는다. 게이트웨이·본체 값은 다른 프로세스라 검사 밖 — 설정 주석에 상대값을 적었다 |
| 2 | **결손 스텝 1회 재시도** | `SRE_PLAYBOOK_RETRY_TIMED_OUT_STEPS`(기본 **on** — 사용자 대책 요청의 일부 · D-161 기본 off 원칙의 예외 · off = W21과 비트 동일). 수집 단계가 모두 끝난 뒤 남은 예산이 있으면 첫 시도 사유가 「스텝 타임아웃」인 스텝만 YAML 순서로 1회씩 순차 재실행(「스텝 미시작」은 예산 끝까지 기다린 뒤에만 나와 남은 예산이 0이므로 제외 · 실패·when_false·budget 건너뜀 제외). 상한 = min(스텝 상한, 남은 예산) · 새 `StepContext`(prior = 현재 결과) · 잡 도구 캐시를 그대로 거쳐 이미 받은 호출과 첫 시도가 아직 기다리는 호출은 다시 부르지 않는다(진행 중 항목을 기다려 받음). 사유: 「재시도 1회 — 성공(첫 시도: …)」 / 「… · 재시도 1회 — 다시 타임아웃 90s — 요청 n건 중 m건 완료 · 대기 …」. **범위 불변** — S0이 재시도로 성공해도 범위를 바꾸지 않고 뒤 단계를 다시 돌리지 않는다(「범위는 초기 범위 유지(뒤 단계는 이미 실행됨)」 · 뒤 단계를 다시 돌리면 예산이 두 배로 들고 다른 범위의 결과가 섞여 결정성이 깨진다). dispatcher는 러너가 단 `ctx.step_id`·`retry_of`로 sid를 확정하고 단계 끝을 (스텝, 시도)마다 1회 낸다 — 재시도는 실행 기록 `step_start`(라벨 「(재시도)」 · 「첫 시도가 시간 안에 끝나지 않아 1회 다시 확인」)·`step_end`·부분 브리핑 항목 교체·(성공 시) 단계 끝 해석 제출을 다시 내고, 첫 시도 스레드의 늦은 완료는 기록 0(W21 계약 유지). 결정적 · LLM 0 |
| 3 | 소견기 재호출 없음 | 소견기 타임아웃 단계의 종결 시 재호출은 하지 않는다(수용 기준 28 유지) — 재시도는 수집 단계에만 |
| 4 | HolmesGPT 시간(보고) | parallel 모드는 잡 시작과 함께 데몬 스레드로 돌고 플레이북과 같은 deadline(시작 + 600s)을 쓴다 → 심층은 최대 약 600s까지 쓸 수 있고 잡이 `running`인 시간도 길어진다. sre_agent에 HolmesGPT 자체 전체 시간 상한은 없고 `max_steps=40`(도달 시 `[미완주]`)뿐이다(LLM·MCP 1건은 holmes/litellm 기본). deadline에 걸리면 기다림만 끊고, 잡 종료로 캐시가 닫혀 이후 도구 호출은 오류로 돌아온다(외부 호출 정지 · LLM 턴은 max_steps까지 더 돌 수 있고 결과는 버려진다). conditional 모드는 남은 30s 이상이면 시작하므로 시작 빈도가 늘어난다 |

**ⓓ 화면 이벤트 상한 (확인만 · 무변경)** — 600s parallel 조사 추정 이벤트: 플레이북 약 130~180 + HolmesGPT 20턴 약 100(40턴이면 160~240) = 약 230~420건으로 링 버퍼 300건(또는 잡당 256KB)을 넘을 수 있다. 넘치면 sre_agent 링 버퍼·본체 허브가 오래된 것부터 버리고 `truncated`를 달며, 「장애 진단」 화면은 「오래된 기록 일부는 버퍼 상한으로 빠졌습니다.」를 띄운다(화면 행 300 초과 시 「화면 상한 300행을 넘어 오래된 행을 접었습니다.」). 조사 이유·스텝 목적·부분 브리핑 섹션은 따로 보관돼 남고, 빠지는 것은 초반 수집 단계의 시작·도구 줄이다. 조사 흐름 이벤트는 같은 300건에서 잘리지만 별도 표기는 없다(§10.1 22). STREAM_MAX 3600s > 600s라 스트림 수명 문제는 없다.

**ⓔ 운영 주의** — 폐쇄망 `sre_agent/.env`에 옛 값(`INVESTIGATION_TIMEOUT_SECONDS=300` 등)을 명시해 두었으면 코드 기본값 상향이 적용되지 않는다 — 기동 로그 「조사 시간 사슬」 1줄로 실제 값을 확인한다. 반영에는 sre_agent(9098)·apm_gateway(9096)·본체 재기동이 필요하다.

### 5.7 (v1.2) 장애 대응 지식 저장소(KB) — 런북 · 알려진 오류 · 매뉴얼 참조 · 축적

#### 5.7.1 구성과 원칙

| 층 | 저장 | 누가 쓰나 | 브리핑에서의 역할 | 판정 영향 |
|---|---|---|---|---|
| 런북 블록 | 플레이북 YAML 안 `runbook` (§5.7.2) | SME · 저장소 리뷰 | 섹션 맨 위 「이 알람의 의미·영향·완화 지침」 | 없음(서술) |
| 알려진 오류(KEDB) | `sre_agent/config/known_errors/*.yaml` (§5.7.3) | SME · 문제 관리 담당 · 저장소 리뷰 | 종합 위 「알려진 문제 일치/후보」 + 우회 제안 | **결정적 가설 순위에 편입**(Validated + 완전 일치만) |
| 대응 매뉴얼·TSG 문서 | `src/doc_qa` RAG 컬렉션(`config/rag_collections.yaml`) (§5.7.4) | 문서 담당 | 「참고 문서」 인용 목록 | 없음(참고) |
| 축적(초안) | plans/153 조사 저장소의 KB 초안 레코드 (§5.7.5) | 운영자 · 시스템 초안 | 미표시(관리자 검토 화면) | 없음 |

- **사실 출처의 위계**: 결정적 판정(게이트웨이·R-A~R-H) > Validated 알려진 오류 > 런북 서술 > 매뉴얼 RAG 인용 > LLM 소견. LLM 종합은 위 순위를 바꾸지 않고 인용한다(D-035).
- **실행 경로 없음**: 우회·완화는 모두 「제안」 문구다. `remediation.py`와 같은 규칙(명령 리터럴 금지 · 위험도 · 검증 · 롤백 · 고위험×저신뢰 = 「검토 필요」)을 KEDB 우회 항목에 그대로 적용하고, 같은 경계 테스트로 막는다.
- **패키지 경계**: 일치 판정은 sre_agent domain(순수 함수)에서 끝내고 브리핑에 실어 보낸다(D-118 — 본체는 결과만 표시). 매뉴얼 RAG는 본체 `src/doc_qa` 소관이므로 sre_agent가 호출하지 않고 **본체 표시 단계에서 조회해 붙인다**(G-15).

#### 5.7.2 런북 블록 — 「모든 알람 유형에 런북 1개」

플레이북 YAML에 kube-prometheus 템플릿 절(Meaning·Impact·Diagnosis·Mitigation)을 그대로 대응시킨다. Diagnosis는 `steps`가 실행형으로 대신하므로 서술 칸을 두지 않는다(SRE Workbook: 매번 같은 명령 목록이면 자동화).

```yaml
runbook:
  meaning: "WAS 인스턴스 응답시간(p95)이 기준선 대비 상승했다."
  impact: "해당 업무 응답 지연 · 액티브 누적 시 스레드 고갈로 전 업무 영향 가능"
  mitigation:            # 제안 — 실행 명령 아님 · remediation.py 규칙 동일
    - {when: "db_split == 'acquire'", action: "커넥션 풀 점유 트랜잭션 확인 후 담당 업무팀에 통보", risk: low,
       verification: "풀 대기 0 · p95 기준선 복귀", rollback: "해당 없음"}
  escalation: "업무 담당 → WAS 담당 → DBA (사내 매뉴얼 §x)"   # 조정형 플레이북·대응 매뉴얼 링크 자리
  references: ["manual://incident/was-latency"]               # 사내 문서 식별자(§5.7.4)
  owner: "WAS운영팀"
  last_reviewed: 2026-10-17
  review_every_days: 180
```

- **커버리지 게이트**: 로더 테스트가 플레이북마다 `meaning`·`impact`·`owner`·`last_reviewed` 존재를 강제한다. 관리 스크립트 `sre_agent/scripts/runbook_coverage.py`는 최근 N일 PAGE 알람의 유형 분포(조사 저장소 집계)와 플레이북을 대조해 「런북 없는 유형(→ fallback 처리된 건수)」을 낸다 — SRE Workbook *"Each alert should have a corresponding playbook entry"*.
- **낡음 표시**: `last_reviewed + review_every_days` 경과 시 브리핑 런북 섹션에 「검토 기한 경과」 배지(내용은 그대로 표시 — 숨기지 않는다).

#### 5.7.3 알려진 오류 레코드 (KEDB · KCS 구조)

```yaml
id: KE-2026-001
title: "배치 시간대 JDBC 커넥션 풀 고갈로 온라인 응답 지연"
state: validated            # wip | not_validated | validated | archived  (KCS 4상태)
governance: experience      # experience | compliance (KCS)
issue: "A1 등 온라인 WAS 응답 지연 알람 · 액티브 서비스 누적"          # 증상 — 운영자 표현
environment:                 # 해결 후에도 참인 조건 — 같은 증상·다른 원인을 가르는 칸
  playbook: was.latency
  targets: {solution: ["인터넷뱅킹"], instance_glob: ["A*"]}
  time_window: {weekday: [1-5], hours: ["02:00-04:00"]}
match:                       # §5.2 `when` 화이트리스트 평가기 재사용 — 판독 enum·판정만 참조
  all: ["S2.pool.verdict == 'exhausted'", "db_split == 'acquire'"]
cause: "야간 배치가 동일 데이터소스 풀을 공유해 커넥션을 장시간 점유"
cause_test:                  # KCS Cause Test → 결정적 확인(플레이북 결과로 평가)
  all: ["S3.top1.acquire_ms > S3.top1.sql_ms"]
workaround:                  # 제안 — remediation.py 형식 · 명령 리터럴 금지
  - {action: "배치 담당에 동시 실행 축소 요청", risk: low, verification: "풀 대기 해소", rollback: "해당 없음"}
permanent_fix: {status: open, problem_ref: "ITSM PRB-xxxx"}   # 해결되면 archived (ITIL: 영구 해결은 변경 관리)
owner: "WAS운영팀"
last_reviewed: 2026-10-17
review_every_days: 90
```

- **일치 판정(결정적)**: `environment` 충족 ∧ `match` 충족 → **후보**. 여기에 `cause_test`까지 충족 → **일치**. 평가 입력은 플레이북 실행 결과(판정·R-A~R-H enum·스코프)뿐이며 자유 텍스트·임베딩 유사도는 쓰지 않는다. 필요한 스텝이 결손이면 「판단 보류」(침묵 불일치 금지).
- **상태별 사용**: `validated` = 일치 시 종합 위 「알려진 문제 일치」 + 가설 1순위 고정(근거: KE id · 충족 조건) · `not_validated` = 「미검증 후보」 배지로만 표시, 가설 순위 미반영 · `wip` = 표시 안 함(관리자 화면만) · `archived` = 표시 안 함(이력 열람만 — KCS 「연결된 사건에서는 열람」).
- **재발 신호**: 일치 건수·최근 일치 시각을 조사 저장소에서 집계해 레코드 옆에 표시 — 영구 해결 우선순위 근거(DeepRmd: TSG 있는 사건의 36.2% 재발 · RCACopilot: 재발의 93.80%가 20일 내).
- **remediation.py와의 관계**: 기존 시그니처 → 조치 후보 표는 그대로 둔다(일반 지식). KEDB 우회는 「이 환경·이 원인」에 한정된 지식이라 별도 칸으로 보이고, 같은 화면에서 일반 조치 후보 위에 둔다.

#### 5.7.4 대응 매뉴얼 · TSG 문서 참조 (RAG — 참고 인용만)

- 사내 장애 대응 매뉴얼·장비 매뉴얼·기존 TSG를 `src/doc_qa`(plans/126) 컬렉션 `incident_docs`로 색인한다. 조회 질의는 결정적으로 조립한다(플레이북 id + 런북 `references` + 대상 솔루션명) — LLM 질의 생성 없음.
- 결과는 「참고 문서」(문서명·절·발췌·링크) 최대 3건으로만 보이고 **판정·가설 순위·종합 입력에 넣지 않는다**(매뉴얼 품질 편차 — StepFly 품질 문제 분포 · AutoTSG 「incomplete」). 종합에 넣을지는 G-15.
- `runbook.references`에 명시된 문서는 RAG 순위와 무관하게 먼저 보인다(사람이 고른 연결 > 검색).

#### 5.7.5 축적과 갱신 — 초안 → 사람 승인 (LLM 자동 등록 차단)

1. **초안 생성(결정적)**: 조사 완료 건에서 운영자가 「KB 초안 만들기」를 누르면, 브리핑에서 issue(알람 원문) · environment(S0 스코프·플레이북 id) · match 후보(실제 성립한 판독 enum) · cause(1순위 가설 — 「미검증」 표기) · 타임라인을 채운 `wip` 초안을 조사 저장소에 남긴다. LLM은 제목·원인 문장 다듬기 **제안**까지만(G-14). 포스트모템이 필요한 건(SRE Book 기준 · G-16)은 PagerDuty 템플릿 절로 같은 데이터를 내보낸다.
2. **승인**: 관리자가 초안을 검토해 YAML 조각을 내려받고 저장소 리뷰로 편입한다(G-11과 같은 경로 — 런타임 쓰기 0). `not_validated` → `validated` 승격은 사람만 한다. **쓰기 지점 차단**: 로더는 `state: validated` 레코드에 `owner`·`last_reviewed`가 없으면 거부하고, 초안 레코드는 sre_agent 로더 경로에 존재하지 않는다(CLAUDE.md 「LLM 자동 등록은 쓰기 지점에서 결정적으로 차단」).
3. **사용 피드백 = 검토**(KCS *Reuse is Review*): 브리핑의 알려진 오류 카드에 「맞음/아님/우회 효과 없음」 3버튼 — 기록은 `FeedbackStore`(noise_gate · JSONL) 패턴을 재사용해 `ke_id`별로 남기고, 「아님」이 연속 N회면 관리자 화면에 「재검토 필요」를 띄운다(자동 강등 안 함).
4. **낡음 관리**: `review_every_days` 경과 · 관련 ITSM 문제 티켓 종결(G-6 연동 후) · 일치 0건 180일 → 관리자 화면 「검토 대상」 목록. 아카이브는 사람이 한다.

#### 5.7.6 브리핑 계약 추가 (하위 호환 — §5.5에 선택 키 가산)

```json
"playbook": {
  "runbook": {"meaning": "...", "impact": "...", "mitigation": [...], "escalation": "...",
              "stale": false, "owner": "..."},
  "known_errors": [
    {"id": "KE-2026-001", "state": "validated", "result": "match|candidate|inconclusive",
     "satisfied": ["S2.pool.verdict == 'exhausted'", "..."], "missing_steps": [],
     "workaround": [...], "recurrence": {"count_30d": 4, "last": "..."}}
  ],
  "references": [{"title": "...", "section": "...", "excerpt": "...", "href": "..."}]
}
```

---

## 6. 외부 소스 어댑터 슬롯 — ITSM · DPM

- 계약: `SourceAdapter` 프로토콜(sre_agent domain) — `name` · `available()` · `fetch(step_id, scope, window) -> StepResult`. 미연동은 `skipped: not_connected`로 브리핑에 「미연동」 표시(침묵 생략 금지).
- **ITSM**: 필요 질의 = ① 대상 호스트·업무의 변경 티켓(−24h~+10m) ② 대상의 열린 장애 티켓 ③ 최근 20일 동일 대상 장애. 연동 방식(REST API · DB 읽기 · MCP 서버 신설 — D-274 독립 게이트웨이 원칙 적용 후보)과 자격증명 위치는 **G-6** 결정 뒤 별 계획.
- **DPM**: `plans/55` 로드맵 따름. 필요 질의 = 대기 세션·블로커 체인·장기 실행 쿼리·세션 수 대비 풀 합계. 엔진별(PG·DB2·MariaDB) 지표는 [미확인] — DPM 제품 확정 시 조사.

---

## 7. 작업 분해 (Wave)

| Wave | 내용 | 산출 | 검증 |
|---|---|---|---|
| **W0** | 실측 재대조(`file:line`) · 사용자 예시 시나리오 골든 픽스처(제니퍼·폴스타 녹화 응답 — `plans/153` 픽스처 형식 · 정상/지연(SQL 지배)/지연(외부 지배)/GC/결손 5종) · 카탈로그 §4·판독 규칙 §4.2를 SME 검토용 표로 반출 · 게이트웨이 프로파일 응답의 커넥션 획득 구간 필드 실측(R-D) | `sre_agent/tests/fixtures/plan187/` | 픽스처 스키마 테스트 |
| **W1** | 플레이북 스키마·로더·선택기(domain · 순수) · `when` 화이트리스트 평가기 · 플레이북 YAML 2종(`was.latency`·`fallback`) | `sre_agent/domain/playbook.py` · `sre_agent/config/playbooks/` | 매칭 표 테스트(유형 13종 × 대표 알람) · 잘못된 YAML 거부 · `eval` 미사용 |
| **W2** | 실행기 + 스텝 라이브러리 S0~S7(배치 콜러블 주입 · 부분 실패 · 실시간 배지 · 기준선 창 계산) · **잡 단위 도구 결과 캐시**(G-26 — 도구명+정규화 인자 키 · 진행 중 호출 대기 공유 · 잡 종료 폐기 · 두 엔진 공용) · S4·S5가 `evidence_prefetch` 함수 재사용(G-24) | `sre_agent/application/playbook_runner.py`·`playbook_steps.py` · `infrastructure/tool_result_cache.py` | 가짜 콜러블로 5 픽스처 종단 · 스텝 1개 실패 시 나머지 진행 · 호출 수·순서 단언 · 같은 인자 동시 2요청 = 실호출 1 · 잡 간 캐시 누수 0 |
| **W3** | 판독 규칙 R-A~R-H(enum) · 단계 소견기·종합기 + 결정적 가드(극성·수치·길이) + 가설 순위 규칙 | `playbook_narrator.py` · `domain/playbook_hypotheses.py` | 규칙별 경계 표 테스트(제니퍼 사례 4건을 픽스처로 재현: JDBC 획득 지연 → `db_split=acquire` · LDAP → `dominant=external`·`cpu_owner=other_process` · CPU 루프 → `cpu_owner=was_logic` · OOM 3시간 주기 → `heap_shape=periodic`) · 가짜 LLM(극성 반대·환각 수치 주입) → 가드 동작 · 프롬프트 렌더 스냅샷 |
| **W4** | dispatcher 배선 — `SRE_DEEP_DIVE_MODE`(`off`·`conditional`·`parallel` · G-22) + 유형별 재정의(YAML `deep_dive.mode`) · `parallel` = 한 잡 안 두 엔진 동시(HolmesGPT엔 정적 지시만) · 예산 초과 시 `conditional` 강등+사유 · 플레이북 모드에서 사전수집 생략(G-24) · episode = 대표 알람 유형 플레이북 + `cross_source` 유지(G-23) · severity_judge 입력 = 두 엔진 도구 출력 합집합(G-25) · 브리핑 6요소 = 플레이북 종합 · `playbook`·`deep_dive` 키 · 결정적 계층 일치 표시(`engine_agreement`) · 잡 진척 | `investigation_dispatcher.py`·`briefing_builder.py`·`settings.py` · `domain/engine_agreement.py` | 플래그 off 비트 동일(기존 sre_agent 테스트) · 모드 3종 종단 · 강등 사유 · HolmesGPT 입력에 플레이북 결과 없음 단언(parallel) · 심층 생략 시에도 escalate 유지 · 일치 표시 경계 표 |
| **W5** | 웹 렌더(§5.6) — `briefing-view.js` `playbook` 렌더(요약형·상세형) · 알람 카드·관제 드로어 요약형 · **상세 표시**(G-18 확정 (d) 조사 대화: `?investigation=` 진입 · 새 대화 고정 카드 · 범위 칩 · 추천 칩 · `disclosures` 복원) · **엔진 비교**(§5.0.1 — 같은 틀 나란히 · 일치 칩 · 근거 먼저 · 판정 공통 칸 · 「이 결론이 맞다」 4택 기록 `engine_choice`) · workb 요약+링크 · 매뉴얼(D-255 — 사용자·관리자) | `briefing-view.js` · `app.js`·`noise.js`(요약형 배선) · (d)면 `app.js` 조사 대화 · `src/api/thread_history.py`(ref 기록) / (a)면 `src/static/investigation.html` · noise_gate 통보 요약 · `scripts/manual/` | `playbook` 키 없을 때 종전 렌더 비트 동일 · 렌더 스냅샷(5 픽스처) · 존 검사(제한 스코프 403) · 사용자 경로 마스킹 · `pytest tests/test_manual` |
| **W6** | 카탈로그 나머지 유형 YAML(`was.error/active/heap/pool` · `host.*`) · 어댑터 슬롯(ITSM·DPM 스텁) · SME 피드백 반영 | YAML 9종 · `domain/source_adapter.py` | 유형별 픽스처 1건씩 |
| **W8** (v1.2) | KB 읽기 측 — 런북 블록 스키마·커버리지 검사 · 알려진 오류 스키마·로더(쓰기 지점 차단) · 일치 판정(`environment`·`match`·`cause_test` · 결손 = 판단 보류) · 가설 순위 편입(validated만) · 브리핑 `runbook`·`known_errors` 키 · 웹 카드 · 매뉴얼(D-255) | `sre_agent/domain/known_error.py` · `sre_agent/config/known_errors/` · `sre_agent/scripts/runbook_coverage.py` · `briefing-view.js` | 상태 4종 × 일치/후보/보류 표 테스트 · `remediation.py` 경계 테스트(명령 리터럴)를 우회 항목에 확장 · 런북 누락 플레이북 로드 실패 · W0 픽스처(JDBC 사례)에 KE 1건 일치 종단 |
| **W9** (v1.2) | KB 쓰기·참조 측 — 조사 저장소 초안 레코드 · 「KB 초안 만들기」(결정적 채움) · YAML 조각 내보내기 · 포스트모템 내보내기(G-16) · 사용 피드백 3버튼(FeedbackStore 패턴) · 검토 대상 목록 · `incident_docs` RAG 컬렉션 참고 인용(G-15) | `src/` 조사 저장소 · 관리자 「조사 지식」 탭(`admin/dashboard.html`·`js/admin-incident-kb.js`) · `config/rag_collections.yaml` · 매뉴얼 | 초안이 sre_agent 로더 경로에 쓰이지 않음(쓰기 0) 단언 · LLM 제안 텍스트가 `state`·`match`를 바꾸지 못함 · RAG 결과가 종합 입력에 없음 단언 |
| **W10** (v1.5) | 조사 대화의 「추가 조사 요청」(G-21) — 초점 목록(결정적) · 템플릿+초점+보충 한 줄(길이 상한·마스킹) 질문 조립 · 대상·사건창 = 브리핑 `scope` · `sre_diagnose` 선택 인자 `prior_findings`(판정·판독 enum·사용 도구·사건창 — LLM 서술 제외) · 비동기 접수 + 대화 안 조사 카드 · 저장 `kind=diagnosis`·`owner_sub`·`parent_investigation_id` · `disclosures` ref 복원 · 인가·가용성·상한(진행 1 · 시간당 3 · 동일 (부모, 초점) 재사용) · 매뉴얼 | `src/domain/`(질문 조립 · 순수) · `src/api/routes/`(접수) · `noise_gate/infrastructure/investigation_store.py` · sre_agent `interface/mcp_service.py`·`application/investigation_jobs.py`·`investigation_guidance.py`(`prior_findings` 소비) · `app.js` | `prior_findings` 없는 구버전 호출 비트 동일(sre_agent 기존 테스트) · 권한 없음 403+감사 · 상한 초과 거부 사유 · 재접수 id 재사용 · 보충 문장 마스킹·길이 · LLM 서술이 `prior_findings`에 섞이지 않음 단언 · 원 브리핑 불변 |
| **W11** (v1.9) | 알람 다시 분석·지난 알람 찾기(§5.6.2 ①② · G-28~G-31) — 사용자 요청 알람 조사 접수 라우트(존 검사 fail-closed · 상한 공유 · 10분 합류) · 조사 레코드 `kind=manual`·`parent_alarm_id`·`owner_sub` · `by-alarm` 우선순위 가산 · 「이전 조사 N건」 · 노이즈 콘솔 행·드로어/채팅 알람 카드/조사 대화 버튼 · 채팅 알람 탭 「지난 알람」 검색(`/noise/decisions` 재사용) · 통보 미발행 · 매뉴얼(D-255) | `src/api/routes/alarm.py` · `noise_gate/infrastructure/investigation_store.py` · `noise_gate` 트리거 진입(사용자 요청) · `app.js`·`noise.js` · `scripts/manual/` | 존 밖 알람 403 · 상한 초과 거부 사유 · 같은 알람 연타 = 조사 1건 · 원 조사 불변 단언 · 통보 0건 · 플래그 off 비트 동일 |
| **W12** (v1.9) | 채팅 증상 질의 조사(§5.6.2 ③ · G-32·G-33) — 결정적 라우터(대상 1건 확정 × 증상 유형 1건) · YAML `match.surface_terms` · 가상 알람 맥락(저장 0) · 플레이북 잡/비동기 `sre_diagnose` 분기 · 대화 안 조사 카드 경로 표기 · `SRE_CHAT_INVESTIGATION_ENABLED`(기본 off) · 매뉴얼 | `src/domain/`(라우터 · 순수) · `src/nodes/fault_diagnosis.py`(플래그 분기) · sre_agent `interface/mcp_service.py`(가상 맥락 입력) · `app.js` | 라우팅 표 테스트(대상 0·1·2 × 유형 0·1·2) · 지시어만 있는 질의 = HolmesGPT · 가상 맥락이 알람·결정 저장소에 쓰이지 않음 · 플래그 off에서 `fault_diagnosis` 기존 테스트 비트 동일 |
| **W13** (v2.0) | sre_agent 잡 실행 기록(§5.6.3 ⓑ) — domain 링 버퍼(이벤트 스키마 · `seq` · 상한·`truncated`) · 기록 지점 배선(세마포어 대기·획득 · 스텝 시작/끝 · `StepContext.call` 도구 전후·캐시 적중 · 소견/종합 LLM 시작/끝 · `_StreamDrain` `START_TOOL`/`TOOL_RESULT`/`AI_MESSAGE` 훅 — `diagnose_fn` 선택 인자 `activity`) · `ai_note`(AI 메모 · G-38) · `mask_secrets` 선적용 + 명령행 비밀값 꼴 보강(G-36 — 발췌 제외 목록 없음) · `sre_get_investigation(activity_since=-1)` · `phase`·`queue_position`·`elapsed_s` · `SRE_ACTIVITY_LOG_ENABLED`(기본 off) · `.env.example` | `sre_agent/domain/activity_log.py`(신규) · `application/investigation_dispatcher.py`·`playbook_steps.py`·`playbook_narrator.py`·`investigation_jobs.py` · `diagnosis.py` · `infrastructure/log_masking.py` · `interface/mcp_service.py` · `settings.py` | `activity_since` 미지정 응답 비트 동일(기존 sre_agent 테스트) · 커서 단조·중복 0 · 상한 초과 `truncated` · 명령행 비밀값 표본(`--password=x`·`-pSecret`·`user:pw@host`·`token=`·JDBC) 발췌·한 줄·AI 메모 어디에도 미유출 · 동시 append 스레드 안전 · 가짜 holmes 스트림 → `tool_start`가 `tool_end`보다 먼저 보임 · `AI_MESSAGE` → `ai_note` 1건 · 동시 상한 2에 3건 접수 → 3번째 `phase=queued`·순번 1 |
| **W14** (v2.0) | 본체 실시간 허브·SSE(§5.6.3 ⓒ · G-39·G-41·G-42) — `InvestigationLiveHub`(미종결 조사당 폴러 1개 · `NOISE_INVESTIGATION_LIVE_POLL_SECONDS` · 구독자 0 60초면 정지 · 버퍼 만료 sweep · 재기동 시 저장소 미종결에서 재개) · `GET /alarm/investigations/stream?watch=` 신설(`resolve_stream_user` · 25초 ping · `summary`=목록 범위 전체 · `activity`/`partial`/`done`=watch만 · `denied` · `id:` 재연결) · 목록 범위(관리자 전체 · 사용자 내 채팅 조사) · `GET /alarm/investigations/active` · 조사 조회 경로 `activity_since` 가산 · 사용자 경로 D-325 ③ 마스킹(발췌 포함)·`ai_note` 제거 · 종결 요약 JSONL(G-37) · `NOISE_INVESTIGATION_LIVE_VIEW_ENABLED`(기본 off) · `settings_catalog` 등재 · MCP 재연결 비용·다중 워커 전제 실측 | `src/api/investigation_poll.py` · (신규) `src/api/investigation_live_hub.py` · `src/api/routes/alarm.py`·`alarm_schemas.py` · `noise_gate/infrastructure/sre_agent_client.py`(`poll` 선택 인자) · (신규) `noise_gate/infrastructure/investigation_activity_store.py` · `src/config/alarm.py` · `src/api/settings_catalog.py` | 플래그 off 응답 비트 동일·stream 404 · 구독자 3명 동시 → sre_agent poll 1회/간격 · 구독자 0 → 폴러 정지 · 사용자 스트림 `summary`에 남의 채팅·자동 조사 0건 · 관리자 스트림 전체 · watch에 존 밖 id → `denied` · watch 밖 조사의 `activity` 0건 · 사용자 경로 발췌 마스킹 적용·`ai_note` 0건 / 관리자 경로 `ai_note` 있음 · 재연결 커서로 누락·중복 0 · 폴러 예외 → 그 조사만 실패 이벤트 · 종결 뒤(잡 TTL 경과) 요약 재조회 |
| **W15** (v2.0) | 화면 「장애 진단」 뷰 탭(§5.6.3 ⓓ) — 뷰 탭·배지 · 진행 중 목록 고정 탭 · 조사별 하위 탭(중복 0 · **상한 없음** · 가로 스크롤 + 「▾ 열린 탭 n」 메뉴 · 종결 탭 모두 닫기 · `sessionStorage` 복원) · SSE 클라이언트(연결 1개 · 탭 열고 닫을 때 `watch` 재연결 · 연결 상태 표시 · 3회 실패 시 15초 조회 강등 표시) · 실행 기록(엔진 필터 · 자동 스크롤 · 발췌 펼침 · 관리자 AI 메모 배지 행) · 중간 결과(상세형 재사용) · 진입 재배선(카드·분석 요청·채팅 조사 카드·노이즈 드로어 `/?diagnosis=` · workb 링크 — 진행·종결 모두 진단 탭 · G-34) · 종결 화면 [조사 대화로 열기] · 매뉴얼(D-255 — 사용자 U-· 관리자 A- 신규 절) | `src/static/index.html` · (신규) `src/static/js/diagnosis-view.js` · `app.js`·`noise.js`(진입 배선) · `css/style.css` · `scripts/manual/` | 정적 UI 테스트(`tests/test_api/test_ui_*` 형식 — 탭 마크업 · 플래그 off 탭 숨김 · 같은 id 두 번 = 탭 1개 · 탭 20개 열어도 EventSource 1개) · `node --check` · 가짜 응답 렌더 스냅샷(사용자 화면 AI 메모 행 0) · `pytest tests/test_manual` · 실프로세스 종단 1회(가짜 LLM — 플레이북·HolmesGPT 각 1건 동시 · 두 탭 모두 종결까지 갱신 · 300초 넘는 조사 정지 0) |
| **W16** (v2.1) | 「조사 대화」 뷰 탭 · 노이즈 관제 상시 버튼(G-43) — 탭 마크업(숨김 · × 닫기) · 대화 바꿔 끼우기(capture/restore · 스트리밍 중 거부 · 떼어 낸 패널 재조회 재개) · `openInvestigationConversation` → 조사 대화 탭 · `?conversation=` · `window.KBPortal` · 관제 `conversationButton`·`openPortalConversation` · 매뉴얼 U-63 | `src/static/{index.html,css/style.css,js/app.js,js/noise.js,noise.html,admin/noise.html}` · `scripts/manual/` | node 실행 — 질의응답 대화 보존·복원 · 스트리밍 중 전환 거부 · 닫기 = 재조회 정지 · 포털 창 재사용(이동 0) · 못 찾으면 `/?conversation=` · 정적 — 상시 조건(`liveViewOn` 무관) · `pytest tests/test_manual` |
| **W17** (v2.2) | 장애 진단 머리 「어떤 알람을 · 왜」(G-44) — 조회 응답 `alarm`(판정 기록 요약 · 조건 로그 운영자만) · 실행 기록 `purpose`(플레이북 선택 근거 · 스텝 초점 · HolmesGPT 이유) · 허브·보관 요약 키 · 화면 `dv-target`·`dv-purpose`·탭 이름 · 매뉴얼 U-70 | `sre_agent/sre_agent/{domain/activity_log.py,application/investigation_dispatcher.py}` · `src/api/{routes/alarm.py,investigation_live_hub.py}` · `noise_gate/infrastructure/investigation_activity_store.py` · `src/static/{js/diagnosis-view.js,css/style.css,index.html}` | sre_agent — `purpose` 가림·상한 · 선택 근거 문구 · 선택 줄이 스텝보다 먼저 · 스텝 초점 · 본체 — 사용자 조건 로그 제외 · 관리자 포함 · 커서 없음·기록 없음·채팅 조사 = 키 없음 · 플래그 off = 키 없음 · node 렌더 — 머리 줄·탭 이름·목적 줄·도구 행 설명 · 판정 기록 없음 문구 · `pytest tests/test_manual` |
| **W18** (v2.3) | 장애 진단 「이벤트 찾아 조사」(G-45) — `/noise/decisions` `alarm_q`·`server_q` 부분일치(가산) · `/active` `event_search` · 진단 화면 검색 칸(시작·끝·알람명·서버 · 최근 30건) · [조사 시작] → 기존 다시 분석 접수(app.js 주입) → 하위 탭 · 매뉴얼 U-70 | `noise_gate/infrastructure/decision_store.py` · `src/api/routes/{noise_dashboard.py,alarm.py}` · `src/static/{js/diagnosis-view.js,js/app.js,css/style.css,index.html}` | 부분일치 칸별 필터·AND·미지정 종전 · 사유는 서버 칸에 안 걸림 · 길이 상한 422 · `event_search` 플래그 반영 · node — 칸 숨김(플래그 off·접수 함수 없음) · 검색 쿼리 · 행·[조사 시작] → 하위 탭 · 실패/거부/합류 문구 · 진단 파일 쓰기 요청 0 · `pytest tests/test_manual` |
| **W19** (v2.4) | 노이즈 관제 → 기존 포털 창 장애 진단 탭(G-46) — 관제 진입 버튼 1개 · `openInPortal`(opener → `askPortalTabs` BroadcastChannel → 새 탭 폴백) · 포털 `KBPortal.openInvestigation` + 채널 수신부(지정 탭만) · 이름 창 폐기 · 매뉴얼 U-63·U-70·A-81 | `src/static/js/{noise.js,app.js}` · `src/static/{index.html,noise.html,admin/noise.html}` | node — opener 재사용·거부 · 채널 2탭 중 1개 · 포털 없음 새 탭 · 진단 우선/꺼짐 조사 대화 · 정적 배선 · Playwright 4 시나리오 · `pytest tests/test_manual` |
| **W20** (v2.5 · **구현 2026-10-22** — v2.9 부기) | 「조사 흐름」(§5.6.4 · G-47~G-50 확정 뒤) — **a** sre_agent: `domain/step_findings.py`(액션 18종 결과 문형 · 표 밖 수치 0) · `TOOL_PHRASES`·`when` 문장 사전 · YAML `narrate.why` · `step_skip` 이벤트(`run_playbook` 선택 인자 `activity`) · 이벤트 `finding`·`because`·`phrase` · partial `finding` · 종결 요약 키 · 단계 끝 소견 호출(G-47 — 별 실행기 · 「이 단계까지의 근거」 · partial `opinion` · 주의 프롬프트) · **b** 플레이북 보강(G-49): host.cpu·host.process v2 · 신규 액션 `apm.active_sql`(리터럴 가림) · `plan_stages` 판독 의존 확인 · **c** 본체: 허브·사용자 경로 가림 키 · 사용자 경로 `ai_note` 허용(G-48 · 두 겹 가림) · 화면 「조사 흐름」·「실행 기록」 전환 탭(G-50) · 단계 카드·지금까지 줄·건너뜀 카드·주의 표지·ⓘ·「종합과 다름」 칩(ⓖ) · HolmesGPT 회차 카드 · 매뉴얼 U-70 문구·캡처(픽스처 `activity`에 새 키) | `sre_agent/sre_agent/{domain/step_findings.py,domain/activity_log.py,application/{playbook_runner.py,playbook_steps.py,investigation_dispatcher.py}}` · `sre_agent/config/playbooks/host.{cpu,process}.yaml` · `src/api/investigation_live_hub.py` · `src/api/routes/alarm.py` · `noise_gate/infrastructure/investigation_activity_store.py` · `src/static/js/diagnosis-view.js` · `scripts/manual/` | 수용 15·21~29 · 문형 커버리지 · 표 밖 수치 0 · 건너뜀 문장 · 가림 키 단언 · node 렌더(새 키 유무) · 사용자 예시 픽스처 종단(가짜 LLM) · Playwright 캡처 · `pytest tests/test_manual` |
| **W21** (v3.0 · **구현 2026-10-23**) | 수집 타임아웃 개선(§5.6.5) — P1 배치 동시 호출 · P3 스텝별 `timeout_s` · P4 타임아웃 진행 사유·중단 · P5 소견 타임아웃 설정 · P6 폴스타 소스 매핑 · P2 미적용 · P0·P7 내부망 | `sre_agent/sre_agent/{infrastructure/mcp_tool_client.py,settings.py,domain/playbook.py,application/{playbook_runner,playbook_steps,investigation_dispatcher}.py,interface/mcp_service.py}` · `sre_agent/config/playbooks/{was.latency,host.cpu,host.process}.yaml` · `sre_agent/.env.example` · `scripts/manual/` A-77 | 수용 30~35 · `test_plan187_w21_{batch_concurrency,timeouts}.py` |
| **W22** (v3.1 · **구현 2026-10-23**) | 조사 시간 사슬 상향·정합(§5.6.6) — 기본값 상향(제니퍼 API 30 · MCP 1건 60 · 스텝 90 · S0/S0r 120 · 수집 240 · 소견 60 · 종합 90 · 조사 600 · 캐시 대기 = 조사 상한 · 본체 후속 1200) · 기동 시 사슬 정합 경고 · 「스텝 타임아웃」 결손 스텝 1회 재시도(기본 on) | `sre_agent/sre_agent/{settings.py,infrastructure/{mcp_tool_client,tool_result_cache}.py,application/{playbook_runner,playbook_steps,investigation_dispatcher}.py,interface/mcp_service.py}` · YAML 3종 · `apm_gateway/apm_gateway/config.py` · `src/config/alarm.py` · `src/api/routes/{alarm,alarm_schemas}.py` · `noise_gate/application/nodes/alarm_notifier.py` · `src/static/js/briefing-view.js` · `.env.example` 3종 · 매뉴얼 A-77 · 테스트 `sre_agent/tests/test_plan187_w22_{timechain,verify}.py` | 수용 기준 36~41 |
| **W23** (v3.2 · **구현 2026-10-23**) | 조사 대화 머리 「무엇을 어떤 방식으로 조사하나」 · 관제 진입 항상 새 탭(G-46 개정) | `src/api/routes/alarm.py`(`with_alarm`·`related`·`stage_label`·`alarm_count`) · `src/static/js/{app,diagnosis-view,noise}.js` · `src/static/css/style.css` · `src/static/{index,noise,admin/noise}.html`(v 갱신) · 매뉴얼 U-63 · 테스트 `tests/test_api/test_ui_plan187_w23_conversation_head.py`·`test_plan187_w17_alarm_brief.py`·`test_ui_plan187_w16_conversation_tab.py` | Playwright 실화면(관리자 3종 조사 · 새 탭 2회) |
| **W24** (v3.3 · **구현 2026-10-23**) | 조사 대화 머리 「발생 소스」(존·위치·수집 시스템) · 「조사 항목」(플레이북 단계·상태·진척) | `src/api/routes/alarm.py`(`_alarm_source_text` · 요약 `db_id`·`source` · `related[].source`) · `src/static/js/{app,diagnosis-view}.js` · `src/static/css/style.css` · `src/static/index.html`(v 갱신) · 매뉴얼 U-63 · 테스트 `tests/test_api/test_ui_plan187_w24_head_source_steps.py`·`test_plan187_w17_alarm_brief.py` | Playwright 실화면(하네스 예시 dbId 운영 값 · CPU 조사 host.cpu 브리핑) |
| **W7** | 평가 — `plans/154` 합성 장애 세계에서 「플레이북 모드 vs HolmesGPT 단독」 원인 적중·소요·LLM 호출 수 비교(MLX 대표 소수 · D-127) · 내부망 shadow(**`parallel` 모드** · 브리핑만 저장 · 통보 무변경 · 운영자 선택 수집 시작) | 결과 보고 | 수용 기준 §8 |

회귀: Wave 중간은 자기 테스트 + `regress.py --no-tests --files …` · 계획 끝 `regress.py --base <계획 시작 SHA> --files …` 1회(D-303) · sre_agent는 자체 venv pytest.

## 8. 수용 기준 · 위험

**수용 기준**
1. 사용자 예시(was.latency 지연·SQL 지배 픽스처)에서 브리핑이 **S0 대상 확정 → S1 → S2 → S3 → S4 → S5 → S6 → S7 → 종합** 섹션을 순서대로 갖고, 각 섹션에 표·판정(출처)·소견·도구명이 있다.
2. S3 표에 5건 각각 SQL·외부 호출 시간이 분리돼 있고 지배 계층(R-C)이 결정적으로 표기된다. 1위 건은 커넥션 획득과 SQL 실행이 나뉜다(R-D) — **2026-10-22 완화(사용자 결정)**: 게이트웨이 응답에 구조화 필드가 있을 때만 나누고, 없으면 `db_split=n/a`와 사유를 표기하면 충족이다. 필드 확보는 내부망 이월(§10.1 3).
2′. 제니퍼 공식 사례 4건을 재현한 픽스처에서 R-A~R-F가 사례의 결론과 같은 enum을 낸다(R-D는 필드를 주입한 픽스처에서 — 2026-10-22 완화).
3. 판정 극성과 반대인 소견·표에 없는 수치는 0건으로 남는다(가드 테스트).
4. 어느 스텝이 실패해도 나머지 섹션과 종합이 나오고, 실패 섹션은 사유를 보인다.
5. 플래그 off에서 sre_agent·noise_gate 기존 테스트 비트 동일.
6. (v2.8 개정) LLM 호출 = 소견 대상 단계 수 + 종합 1(상한 없음 · 단계마다 1회 초과 0) · 플레이북 수집+서술 ≤ 120s(내부망 측정 — MLX 수치는 로직 확인용).
7. W7 평가에서 원인 적중률이 HolmesGPT 단독 대비 악화 없음.
8. (v1.2) 활성 플레이북 전부가 런북 블록을 갖고(커버리지 100%), 검토 기한 경과 시 배지가 뜬다.
9. (v1.2) JDBC 획득 지연 픽스처에서 validated KE가 「일치」로, 같은 증상이지만 `db_split=execute`인 픽스처에서는 「불일치」로 나온다(KCS Environment·Cause Test가 가르는 경우). 필요한 스텝이 결손이면 「판단 보류」. (2026-10-22 완화) `db_split` 필드가 없는 실 게이트웨이 응답에서는 「판단 보류」로 나오면 충족이다 — 「일치/불일치」 분기는 필드 주입 픽스처로 확인한다.
10. (v1.2) 런타임 경로 어디에서도 KE·런북 파일에 쓰지 않는다(파일 쓰기 0 단언) · `not_validated`·`wip`는 가설 순위에 영향 0.
11. (v1.9) 노이즈 콘솔·채팅 알람 탭에서 30일 안의 PAGE가 아닌 알람을 찾아 「분석 요청」하면 플레이북 조사가 새 레코드로 생기고, 원 알람의 기존 조사는 바뀌지 않는다.
12. (v1.9) 「A1 응답 지연 원인 알려줘」처럼 대상·증상이 결정적으로 확정되는 질의는 `was.latency` 플레이북으로, 「그 서버 이상해」처럼 확정되지 않는 질의는 HolmesGPT로 간다 — 라우팅 판정에 LLM 호출 0회.
13. (v1.9) 사용자 요청 조사는 D-335 ⑥ 상한을 자동 조사와 공유하고, 통보·승격을 일으키지 않는다.
14. (v2.0) 플레이북 조사에서 각 도구 호출이 시작 후 폴러 한 주기(5초) + 푸시 지연 안에 실행 기록에 「진행」으로 나타나고, 끝나면 상태·소요·행 수로 바뀐다.
15. (v2.0 · v2.7 개정) HolmesGPT 조사(심층·진단)도 도구 호출마다 실행 기록이 쌓인다 — 종결 전 빈 화면 0. AI 메모는 사용자·관리자 모두에게 「검증 전」 배지로 보이고(G-48 — v2.0에서는 관리자만), 사용자 경로는 두 겹 가림을 거친다.
16. (v2.0) 동시 상한 2에서 조사 3건을 접수하면 3번째는 「대기 — 앞 n건」으로 보이고, 차례가 오면 진행으로 바뀐다.
17. (v2.0) 조사 A 진행 중에 조사 B를 열면 하위 탭이 2개가 되고, A는 배경에서 상태를 이어 받아 종결까지 바뀐다. 같은 조사를 다시 누르면 탭이 늘지 않는다. 하위 탭이 몇 개든 브라우저의 조사 스트림 연결은 1개다.
18. (v2.0) 조사가 300초를 넘어도(대기 포함) 화면 갱신이 종결 전에 멈추지 않는다. 연결 끊김·강등·60초 무수신은 사유와 함께 보인다.
19. (v2.0) 비밀값(명령행 표본 포함)은 sre_agent 응답에 나오지 않고, 사용자 경로의 한 줄·발췌는 D-325 ③ 마스킹을 한 번 더 거친다. 사용자 진행 중 목록에는 자기 채팅 조사만, 관리자 목록에는 전부가 보인다.
20. (v2.0) 두 플래그 off에서 MCP·API 응답과 화면이 종전과 비트 동일하고 조사 스트림 경로는 404다(기존 테스트).
21. (v2.5) host.cpu 사용자 예시 픽스처(java WAS 78% · 인스턴스 A1 CPU 대부분 · 실행 중 요청·SQL)에서 단계 카드가 CPU 추이 → 프로세스 상위 → WAS 프로세스 CPU → 실행 중 요청 → 실행 중 SQL 순으로 나오고, 카드마다 왜·조회·결과 문장이 있으며 「지금까지」 줄이 이 고리를 잇는다(G-49 권고안 기준).
22. (v2.5) 결과 한 줄의 수치는 모두 그 단계 표에 있는 값이다(표 밖 수치 0 · 가드 테스트) · 결과 한 줄·왜·조회·건너뜀 문장 생성에 LLM 호출 0.
23. (v2.5) 건너뛴 단계(`when` 거짓·예산 소진)도 카드로 나오고 사유가 조건식 원문이 아닌 문장이다.
24. (v2.5) 단계가 끝나면 폴러 한 주기 + 푸시 지연 안에(종결 전) 그 카드에 결과 한 줄이 보인다.
25. (v2.5) 액션 18종 모두 결과 문형이 있고(커버리지 테스트) · 문형 사전에 없는 도구는 현행 한 줄로 보인다(빈칸 0) · 새 키가 없는 이벤트(구버전)도 그려진다.
26. (v2.5) `finding`·`because`·`phrase`·실행 중 SQL이 `label`과 같은 두 겹 가림을 탄다(표본: 비밀값·명령행·SQL 리터럴) · 종결 요약 보관에 세 키가 남아 잡 TTL 뒤에도 카드가 다시 그려진다.
27. (v2.5) `SRE_ACTIVITY_LOG_ENABLED` off에서 MCP 응답 비트 동일 · `NOISE_INVESTIGATION_LIVE_VIEW_ENABLED` off에서 화면 비트 동일 · 현행 실행 기록은 「상세 기록」 안에 그대로 있다.
28. (v2.6 · v2.7 확정) AI 단계 해석은 그 단계가 끝난 뒤 소견 타임아웃(20s) 안에 카드에 붙고(종결 전), 가드 탈락·타임아웃 단계는 「AI 해석 없음 — 사유」와 결정적 문장이 그대로 있다(빈칸 0) · 알람당 LLM 호출 = 소견 대상 단계 + 종합 1 · 「왜」·「조회」·「건너뜀」 생성에 LLM 호출 0.
29. (v2.7) 단계 해석·HolmesGPT AI 판단 모두 주의 표지와 ⓘ 문구(ⓖ C1~C8)가 붙고, 종결 뒤 종합과 극성이 다른 단계 해석에 「종합과 다름」 칩이 붙는다 · 「조사 흐름」·「실행 기록」 탭을 오가도 스트림 연결은 1개이고 실행 기록 탭 화면은 v2.4와 같다.
30. (v3.0) 배치 동시 상한 c에서 지연 d인 호출 n건의 벽시계가 약 ⌈n/c⌉×d이고, 응답 순서는 요청 순서이며, 한 건 실패가 다른 응답에 영향이 없다 · c = 1이면 종전 순차와 같다.
31. (v3.0) 스텝 `timeout_s`가 있으면 그 값, 없으면 30s를 쓰고 어느 쪽이든 남은 예산보다 길지 않다 · 같은 단계 스텝들의 대기 상한이 서로 누적되지 않는다.
32. (v3.0) 스텝 타임아웃 사유에 요청·완료 건수와 대기 중 도구가 있고, 그 사유가 실행 기록 단계 끝·부분 브리핑·최종 섹션에 같은 문구로 보인다 · 타임아웃 뒤 그 스텝은 새 도구 호출을 내지 않는다.
33. (v3.0) 소견 타임아웃이 설정값으로 배선되고, 남은 시간이 적으면 종전대로 줄어든다 · 단계 끝 해석 LLM이 느려도 수집 단계 진행이 늦어지지 않는다.
34. (v3.0) 폴스타 소스 매핑이 비면 현행과 같다 · 페이로드 `infra_db_ids`가 매핑보다 우선한다 · 매핑으로 정하면 「설정 매핑으로 대체(소스→db_id)」와 오조회 위험이 run notes·S0·S4 사유에 보인다.
35. (v3.0) 새 설정 3개의 기본값에서 기존 sre_agent·본체 테스트가 통과한다(모듈 단위 회귀) · 내부망에서 S0·S1 결손이 줄었는지는 P0 실측으로 확인한다(§10.1 18).
36. (v3.1) 시간 사슬 기본값이 §5.6.6 ⓑ 표와 같다(제니퍼 API 30 · MCP 1건 60 · 스텝 90 · S0/S0r 120 · 수집 240 · 소견 60 · 종합 90 · 조사 600 · 본체 후속 1200(D-344 ④)) · 즉시 경로 10/45s는 무변경 · 수용 기준 31의 「없으면 30s」는 90s로, 수용 기준 6의 수집 상한 120s는 240s로 읽는다.
37. (v3.1) sre_agent 기동 시 사슬 관계 5종 중 하나라도 어긋나면 경고 1줄(값 나열)이 나오고 기동은 계속된다 · 정합이면 경고가 없다.
38. (v3.1) 재시도가 켜져 있으면 「스텝 타임아웃」 결손 스텝만 남은 예산 안에서 YAML 순서로 1회 다시 돌고, 성공·다시 타임아웃 사유가 최종 섹션·실행 기록 단계 끝·부분 브리핑에 같은 문장으로 보인다 · 남은 예산이 없으면 재시도 0 · 꺼져 있으면 W21과 같다.
39. (v3.1) 재시도는 잡 도구 캐시를 거쳐 이미 받은(또는 첫 시도가 기다리는) 호출을 다시 부르지 않는다 · 대상 확정 스텝이 재시도로 성공해도 범위와 뒤 단계 결과는 바뀌지 않고 그 사실이 사유에 보인다 · 첫 시도의 늦은 완료는 기록되지 않는다.
40. (v3.1) 소견기 재호출은 없다(수용 기준 28 유지) · 재시도는 LLM 호출 0.
41. (v3.1) 잡 도구 캐시 대기 상한이 조사 전체 상한과 같다 · 바뀐 값의 기본값에서 기존 테스트가 통과한다(모듈 단위 회귀) · 내부망 실효는 P0 실측으로 확인한다(§10.1 18).

**위험**
- **호스트 브리지**: 제니퍼 hostname ↔ 폴스타 서버명 정확 일치 결함(`plans/165` F3) — S4~S6이 0건이 될 수 있다. W0에서 F3 해결 여부 확인 · 미해결이면 S0가 「폴스타 서버 미확정」을 명시하고 폴스타 스텝을 결손 처리(침묵 0건 금지).
- **실시간 전용 데이터**: 풀·프로세스 Top은 알람 시점이 아니다 — 배지 + 소견 프롬프트에 「현재값」 명시. 지연(조사 대기열) 크면 가치 하락 → G-9.
- **폴스타 시간 단위**: 시간(h) 집계라 10분짜리 스파이크는 희석된다 — Prometheus 구성 시 `prom_metric_range` 우선(G-8).
- **LLM 비용·지연**(FabriX 운영 · v2.8 상한 제거로 알람당 단계 수 + 1회 — 보강 host.cpu 약 9~10회): 알람당 호출 × PAGE 건수 — `plans/162`로 PAGE 모수가 줄기 전에는 시간당 예산 가드(`investigation_hourly_budget`) 공유.
- 제니퍼 프로파일 예산 · 게이트웨이 부하 — S0 대상 상한 3 · 프로파일 1건.
- (v1.2) **KB 신뢰 붕괴**: 낡은 Validated KE가 잘못 일치하면 운영자가 엉뚱한 우회를 따른다 — `cause_test` 필수 · 검토 기한 · 「아님」 피드백 재검토 · 일치 카드에 충족 조건을 그대로 노출(근거 없는 권고 금지).
- (v1.2) **KB 공동화**: 쓰는 사람이 없으면 비어 있다(SRE Workbook — 런북은 운영 변화 속도로 낡는다) — 초안 생성을 한 번 클릭으로 낮추고, 커버리지·검토 대상 목록을 관리자 화면 기본 노출. 운영 책임자 지정은 G-17.
- (v2.0) **명령·출력 노출**: HolmesGPT bash 명령 한 줄·도구 인자·발췌에 호스트·IP·SQL 조각·프로세스 명령행이 실리고, G-36 확정으로 **사용자에게도** 발췌가 보인다. 방어는 가림 두 겹(sre_agent `mask_secrets` → 본체 D-325 ③)뿐이라 **가림 규칙이 놓친 꼴은 그대로 노출된다** — W13 표본 테스트로 꼴을 고정하고, 운영 투입 전 내부망 실 출력 표본으로 한 번 더 확인한다[내부망 확인 필요]. 놓친 꼴이 나오면 `log_masking.py`에 더하는 것으로 대응한다(제외 목록 부활은 사용자 재결정 사항).
- (v2.0 · v2.7 개정) **AI 메모·단계 해석 오독**: 검증 전 AI 서술이 결론처럼 읽힐 수 있고, G-48로 **사용자에게도** 보인다 — 배지·ⓘ 주의 문구(ⓖ)·「종합과 다름」 칩 · 브리핑·통보에는 쓰지 않는다(D-035).
- (v2.0) **갱신 부하**: 미종결 조사 수 × (60/5) MCP poll/분(보는 사람·탭 수와 무관) — poll마다 MCP SSE 연결을 새로 연다. W14에서 측정하고 기준 초과 시 간격 상향 또는 연결 재사용(별 결정). 브라우저 쪽은 연결 1개.
- (v2.0) **메모리**: 잡당 실행 기록 256KB × 보관 잡 500(`_DEFAULT_MAX_JOBS`) = 최대 약 128MB(sre_agent) + 본체 허브 같은 차수 — 종결 잡은 발췌·AI 메모를 버리고 요약만 남겨 상한을 낮춘다 [W13·W14에서 실측 후 확정]. 하위 탭 무제한은 브라우저 메모리 문제로 돌아온다 — 탭당 화면 행 300건 상한.
- (v1.2) 사내 매뉴얼에 민감 정보(계정·IP) — RAG 색인 전 `pii_filter`·마스킹 경로 통과(plans/126 기존 규칙).
- (v2.5) **결정적 문장의 오독**: 결과 한 줄이 표 상위 행만 요약하므로 「1위만 원인」처럼 읽힐 수 있다 — 문형에 범위(「상위 10 중」·「현재값」)를 항상 넣고, 해석·원인 단정은 종합(가설 순위)에만 둔다.
- (v2.5) **실행 중 SQL 노출**(G-49): 리터럴 가림이 놓친 값(주민번호·계좌 등이 SQL 상수로 실린 경우) — 리터럴 전부 `?` 치환 후 `mask_secrets` · 내부망 실 SQL 표본으로 확인(§10.1 7과 함께)[내부망 확인 필요].
- (v3.1) **조사가 길어짐**: 조사 전체 600s · 수집 240s로 조사 1건이 동시 상한(`investigation_max_concurrent` 2) 자리를 더 오래 잡아 알람 폭주 때 대기가 늘고, 본체 후속 메시지도 최대 10분 뒤에 올 수 있다 — 화면에서 진행(장애 진단 실행 기록·대기 순번)이 보이므로 수용한다. 동시 호출은 스텝 워커 4 × 배치 동시 4 × 동시 조사 2가 그대로라 게이트웨이 부하 상한은 늘지 않고 시간만 길어진다. X-View 도구(호출 41회)는 60s 상한에도 끊길 수 있다(§10.1 21).
- (v2.5) **새 키 가림 누락**: 허브 중계·사용자 경로 가림 대상 키 목록에 `finding`·`because`·`phrase`를 빠뜨리면 원문이 나간다 — 키 목록 단언 테스트.

## 9. 사용자 확정 게이트

> **확정(2026-10-17)**: 사용자 지시 *"게이트 권고안대로 확정"* — **G-1~G-17은 아래 표의 굵은 글씨(권고) 항목으로 확정**됐다(D-341). G-9의 수동 재실행 버튼은 D-325 ⑩에 따라 두지 않는다(**v1.9 G-28로 부분 개정** — 원 조사를 덮어쓰지 않는 새 조사로 허용). G-18은 v1.3에서 새로 생겨 확정 대기다.
>
> **확정(2026-10-19 · v2.0 · 인터뷰)**: 사용자 제기(「장애 진단」 탭 · 진행 중 명령·API·중간 결과 표시 · 조사마다 새 탭) → §5.6.3 검토안 G-34~G-40 → 사용자 *「결정해야될 사항의 인터뷰를 진행하라」* → 문항별 선택으로 확정하고, 인터뷰 중 드러난 두 문항(G-41 목록 범위 · G-42 상세 푸시 범위)을 추가 확정했다. 권고와 다른 선택 5건: G-36(발췌 사용자에게 · 가림 후 모두) · G-38(AI 메모 관리자 표시) · G-39(SSE) · G-40(상한 없음) · G-41(사용자 지정 범위). **기존 결정 개정**: D-341 ⑪ 진입(G-34) · D-325 ②(15초 재조회 · SSE 신규 없음 → 진단 탭은 SSE · 카드 재조회는 그대로) · D-325 ③(사용자 경로 텍스트 마스킹 유지 · 발췌도 마스킹 후 사용자에게) — D-341 부기 4 · D-325 부기로 등재. D-123 상태 어휘 불변(`phase` 가산).
>
> **추가 확정(2026-10-17 · v1.9)**: 사용자 지시 *"권고안대로 확정하여 계획서에 반영하라"* — **G-28~G-33 권고안 확정**(§5.6.2). 사용자 요청 알람 분석 허용(새 조사 · 원 조사 보존) · 등급 무관 30일 · 존 범위 사용자 · 상한 공유 · 증상 질의 결정적 라우팅(플레이북/HolmesGPT) · 비동기 조사 카드. D-325 ⑩(「후속 계획」)의 후속이 이 계획이다.

> **추가 확정(2026-10-17 · v1.8)**: 사용자 지시 *"권고대로 정리하라"* — **G-22~G-27 권고안 확정**. 심층 조사는 운영 모드 `SRE_DEEP_DIVE_MODE`로 두고 도입 초기 `parallel`(§5.0.1) · episode는 대표 알람 유형 플레이북 · 플레이북 모드에서 사전수집 생략 · severity_judge 입력은 두 엔진 합집합 · 잡 단위 도구 캐시 도입 · 전환은 표본·비율 기준의 권고 표시 + 관리자 수동 전환. 이로써 G-1~G-27 전건 확정.
>
> **추가 확정(2026-10-17 · v1.5)**: 사용자 지시 *"권고한데로 반영하라"* — **G-18 (d) 조사 대화 · G-19 1단계 승계 없음 → 2단계 결정적 시드 · G-20 후속 · G-21 상세 권고(§5.6.1) 전부 확정**. G-21 세부: ① 보충 한 줄 자유 입력 **허용**(길이 상한 · 마스킹) ② 권한 기본 **admin_only 유지**(`HOST_AUTHZ_MODE`로 개방) ③ `sre_diagnose`에 선택 인자 **`prior_findings` 가산**. G-21의 「추가 조사 요청」은 G-9의 「같은 조사 재실행」과 다르다 — 재실행 버튼은 계속 두지 않으며, 수동 조사 요청의 비용 통제는 D-335 ⑥ 상한을 따른다.

| # | 질문 | 권고 |
|---|---|---|
| G-1 | 실행 위치 | **(a) sre_agent 플레이북 모드**(§5.1) · (b) noise_gate 인프로세스 · (c) 새 패키지 |
| G-2 | HolmesGPT 자유 조사와의 관계 | **(a) 플레이북 먼저 → 결과를 지침으로 넘겨 HolmesGPT 심층 조사(선택 · 플래그)** · (b) 플레이북이 대체 · (c) 병렬 |
| G-3 | 미매칭 알람 | **fallback 플레이북**(공통 점검) · 또는 현행 HolmesGPT로 |
| G-4 | 「이상 유무를 LLM으로 분석」의 의미 | **판정은 게이트웨이·결정적 규칙, LLM은 판정을 인용한 해석 의견**(D-035·D-274 ⑤·D-335와 정합). LLM이 판정까지 하길 원하면 D-335와 충돌 → 별도 결정 필요 |
| G-5 | LLM 호출 형태 | **단계별 소견(병렬) + 종합 1회** · 또는 전 단계 단일 호출(호출 1회 · 섹션 품질·가드 난도↑) |
| G-6 | ITSM·DPM 연동 | 제품·API 명세·자격증명 위치 확인 후 **별 계획** · 이 계획은 슬롯·스텁까지 |
| G-7 | 「p96」 | 오기로 보고 **p95**(게이트웨이 제공값) · 실제 p96이 필요하면 게이트웨이 확장 필요 |
| G-8 | 호스트 시계열 해상도 | **폴스타 시간(h) 기본 + Prometheus 구성 시 분 단위 병기** |
| G-9 | 트리거·진척 표시 | PAGE 자동(현행 조건) + **스텝별 부분 결과 먼저 표시** · 수동 재실행 버튼은 D-325 ⑩(수동 조사 제외)과 충돌 → 별 결정 · **v1.9 → G-28로 해소** |
| G-10 | 카탈로그 보정 근거 | 공개 제니퍼 문서(2009~2011)는 §3.1로 반영했다. 다음 자료를 확보한다: ① 현행 제니퍼 버전 분석 가이드(기술지원) ② 은행연합회 업권 「성능관리 가이드라인」 원문(금감원 2023 TF 산출물) ③ 사내 장애 대응 매뉴얼 ④ SME 인터뷰. 확보 뒤 R-A~R-H 임계와 카탈로그를 보정한다 |
| G-11 | 플레이북 관리 | **저장소 YAML + 리뷰** · 관리자 UI 편집은 범위 밖 |
| G-12 | 사건창·기준선 초기값 | §4.0 [제안] 값으로 시작 · 내부망 shadow(W7)에서 튜닝 |
| G-13 (v1.2) | KB 저장 위치 | **(a) 저장소 YAML(`sre_agent/config/known_errors/`) + 리뷰 편입 — 런타임 쓰기 0**(G-11과 같은 경로) · (b) 조사 저장소 DB에 두고 관리자 승인 화면에서 상태 전이(편하지만 sre_agent가 본체 저장소를 읽어야 해 D-118 경계 재설계 필요) |
| G-14 (v1.2) | LLM의 KB 관여 범위 | **초안 문장 다듬기 제안까지만**(상태·`match`·`cause_test`는 사람만) · 또는 LLM 관여 0 · 사내 기존 TSG를 LLM으로 구조화 추출하는 것도 「초안」으로만(StepFly·AutoTSG 수준 정확도도 사람 검토 전제) |
| G-15 (v1.2) | 매뉴얼 RAG 인용의 쓰임 | **참고 목록만 · 종합 입력 제외** · 또는 종합 프롬프트에 발췌 포함(근거 풍부 ↔ 매뉴얼 오류 전파). 조회 주체는 본체 표시 단계 권고(sre_agent는 RAG 미호출) |
| G-16 (v1.2) | 포스트모템 범위 | **작성 기준(SRE Book 5항 중 사내 채택분) 충족 건에 「포스트모템 내보내기」 제공만** · 포스트모템 관리 기능(검토·액션 아이템 추적)은 ITSM 연동(G-6) 뒤 별 계획 |
| G-18 (v1.3 · v1.4 권고 변경 · **확정 (d)**) | 상세 표시 위치 | **(d) 질의응답 화면의 「조사 대화」 — 새 대화의 첫 고정 카드로 상세를 그리고 이어서 추가 질의**(§5.6.1 · 새 페이지 0) · (a) 조사 상세 화면 신설(`/static/investigation.html`) · (b) 카드·드로어 안 확장만 · (c) 노이즈 관제 화면 안 새 탭 |
| G-19 (v1.4 · **확정**) | 조사 대화의 문맥 승계 | **1단계는 승계 없음(대상 이름을 박은 추천 칩 + 안내)** → 2단계에서 `investigation_id` → 결정적 엔티티 시드(`context_resolver`) |
| G-20 (v1.4 · **확정 — 후속**) | 브리핑 자체에 대한 질문 | **후속**(판정 근거 결정적 인용 + LLM 서술만 · 재판정 없음) · 1단계 범위 밖 |
| G-21 (v1.4 · **확정**) | 대화에서 sre_agent 추가 조사 | **명시 버튼 → 비동기 `sre_diagnose` + 대화 안 조사 카드 · 질문 = 템플릿 + 초점 선택 + 보충 한 줄 · `prior_findings` 선택 인자 가산 · admin_only · 상한**(§5.6.1 G-21 상세) — `plans/155` 공용부 위에 구현 · 기존 동기 경로 `fault_diagnosis`는 그대로 |
| G-22 (v1.6 · v1.7 권고 변경 · **확정 — 운영 모드 · 초기 parallel**) | HolmesGPT 심층 실행 조건 | **v1.7 권고: 운영 모드 `SRE_DEEP_DIVE_MODE` — 도입 초기 `parallel`(두 엔진 동시 · 운영자 비교·선택 · §5.0.1) → 유형별 선택 통계로 `conditional` 전환** · v1.6 원안: 조건부(1순위 가설 판단 보류 · 결손 ≥2 · fallback일 때만 · 생략 사유 표시 · 예산 플레이북 ≤120s / 나머지 심층) · 또는 항상 실행 · 또는 사용자 요청(G-21)만 |
| G-23 (v1.6 · **확정**) | 사건 종합 조사(`kind=episode`) | **대표(원인) 알람 유형의 플레이북 + `cross_source` 블록 유지** · 또는 episode는 현행 HolmesGPT 유지 |
| G-24 (v1.6 · **확정**) | 사전수집과 S4·S5 중복 | **플레이북 모드에서는 사전수집 생략 · S4·S5가 같은 함수 재사용 · `job.correlation`을 S4·S5 결과로 채움** · 또는 둘 다 실행 |
| G-25 (v1.6 · **확정**) | severity_judge 입력 | **플레이북 도구 원시 출력 ∪ 심층 도구 출력**(`ImportanceVerdict`·escalate 계약 불변) · 또는 심층 출력만(심층 생략 시 escalate 신호 소실) |
| G-26 (v1.7 · **확정**) | 잡 단위 도구 결과 캐시 | **도입**(병렬·순차 모두 중복 호출 제거 · 잡 종료 시 폐기) · 또는 미도입(병렬 시 호출 2배) |
@@G27@@ `parallel` → `conditional` 전환 기준 | **유형별 운영자 선택 표본 ≥30건이고, 플레이북이 맞다는 선택(「플레이북」+「둘 다」) 비율 ≥80%면 관리자 「조사 지식」 탭에 그 유형의 「전환 권고」를 띄운다. 전환은 관리자가 플레이북 YAML `deep_dive.mode`를 바꿔 리뷰로 반영한다(자동 전환 없음 · G-11)** [수치는 제안 값 — shadow에서 조정] |
| G-28 (v1.9 · **확정**) | 알람 다시 분석 허용 | **허용 — 「이 알람 분석」 버튼 · 원 조사를 덮어쓰지 않는 새 조사(`kind=manual` · `parent_alarm_id`) · 자동 조사와 같은 경로** (G-9·D-325 ⑩ 부분 개정) · 또는 계속 제외 |
| G-29 (v1.9 · **확정**) | 대상 범위 · 찾기 | **등급 무관 · 결정 저장소 보존 범위(30일 · 20000줄) · 채팅 알람 탭 「지난 알람」 검색은 `/noise/decisions` 재사용(새 API 없음)** · 또는 PAGE만 |
| G-30 (v1.9 · **확정**) | 권한 | **알람 열람 존 범위 사용자(fail-closed)** · 또는 admin_only(G-21과 같게) |
| G-31 (v1.9 · **확정**) | 결과 보관 · 비용 | **새 레코드 + 이전 조사 나열 · D-335 ⑥ 상한 공유 · 같은 알람 10분 합류 · 통보·승격 없음** [10분은 제안 값] |
| G-32 (v1.9 · **확정**) | 증상 질의 라우팅 | **대상 1건 × 증상 유형 1건이 결정적으로 확정되면 플레이북(가상 알람 맥락 · 저장 0), 아니면 HolmesGPT(`sre_diagnose`) · LLM 분류 미의존** · 또는 전부 HolmesGPT(§5.0 v1.8) |
| G-33 (v1.9 · **확정**) | 채팅 응답 방식 | **대화 안 비동기 조사 카드(W10 재사용) + 경로 표기 · `SRE_CHAT_INVESTIGATION_ENABLED` 기본 off(off = `fault_diagnosis` 비트 동일)** · 또는 동기 45초 유지 |
| G-34 (v2.0 · **확정** · 노이즈 드로어 진입은 G-46으로 개정) | 알람·조사를 눌렀을 때 여는 곳 | **항상 「장애 진단」 탭의 그 조사 하위 탭(진행 중·종결 모두) — 카드 [진행 보기] · 분석 접수 직후 · 채팅 조사 카드 · 노이즈 드로어(~~`/?diagnosis=` 새 브라우저 탭~~ → 열린 포털 창 · G-46) · workb 링크. 「조사 대화」는 종결 화면 버튼**(권고안 · D-341 ⑪ 진입 부분 개정) · ~~진행 중일 때만 진단 탭~~ |
| G-35 (v2.0 · **확정**) | 「새로운 탭」의 뜻 | **장애 진단 화면 안의 하위 탭**(권고안) · ~~브라우저 새 탭~~ |
| G-36 (v2.0 · **확정**) | 실행 기록 노출 범위 | **한 줄·상태·소요·행 수·응답 발췌 모두 사용자·관리자에게 — 가림 처리 후 모두 보임(발췌 제외 목록 없음 · 사용자 경로는 D-325 ③ 마스킹 한 번 더)**(권고와 다름 — 권고는 발췌 운영자만) · ~~발췌 운영자만~~ · ~~발췌 없음~~ |
| G-37 (v2.0 · **확정**) | 실행 기록 보관 | **종결 시 요약(발췌·AI 메모 제외)을 본체 JSONL에 1행**(권고안) · ~~보관 안 함~~ |
| G-38 (v2.0 · **확정** · **v2.7 G-48로 개정**) | LLM 중간 서술(HolmesGPT `AI_MESSAGE` · 소견 초안) | **v2.7: 사용자·관리자 모두에게 「AI 판단 — 검증 전」 배지로 표시(두 겹 가림) · 브리핑·통보에 쓰지 않음** · ~~관리자에게만 · 사용자 경로에는 없음~~(v2.0 확정 — 권고와 다름) · ~~표시 안 함~~ |
| G-39 (v2.0 · **확정**) | 갱신 방식 | **본체→브라우저 SSE 푸시 — 브라우저당 연결 1개 다중화 · 본체 허브가 조사당 sre_agent poll 1회/5초 · 300초·안전 상한 없이 종결로 멈춤 · 끊기면 15초 조회로 강등 표시**(권고와 다름 — 권고는 커서 poll · D-325 ② 부분 개정) · ~~커서 poll 5s/30s~~ |
| G-40 (v2.0 · **확정**) | 하위 탭 상한·정리 | **상한 없음 · 자동 닫기 없음 · 「▾ 열린 탭 n」 메뉴 · 종결 탭 모두 닫기 · `sessionStorage` 복원**(권고와 다름 — 권고는 8개) · ~~8개~~ |
| G-41 (v2.0 · **확정** · 인터뷰 추가) | 「진행 중 목록」 범위 | **관리자 = 모든 조사(자동·요청·채팅 · 존 무관) · 사용자 = 내가 채팅으로 시작한 조사**(사용자 지정 — 「관리자는 모든요청을 보여주고 사용자는 내 채팅 조사를 보여줘라」). 사용자가 알람에서 직접 연 조사는 목록에 없어도 하위 탭으로 열린다(존 검사) · ~~내 존 알람 조사 + 내 채팅 조사(권고)~~ · ~~내 존 모든 조사~~ · ~~내가 요청한 것만~~ |
| G-42 (v2.0 · **확정** · 인터뷰 추가) | SSE 상세 기록 푸시 범위 | **열린 하위 탭의 조사만 — 탭을 열면 누적분을 한 번에 받고 이후 새 이벤트만 · 진행률·상태 요약은 목록 전체에 항상**(권고안) · ~~목록의 모든 조사~~ |
| G-43 (v2.1 · **확정** · 사용자 지시 2026-10-21 · 관제 버튼은 G-46으로 개정) | 조사 대화를 여는 곳 · 노이즈 관제 버튼 | **본 화면 「조사 대화」 뷰 탭(질의응답과 같은 줄 · 대화 화면 공유 · 바꿔 끼우기) · 노이즈 관제 ⓒ 머리에 조사 id가 있으면 늘 버튼 → 열린 포털 창의 그 탭(못 찾으면 새 탭 `/?conversation=`)** · ~~질의응답 탭에서 새 대화~~ · ~~관제는 `/?investigation=` 새 탭 · 진단 켜지면 숨김~~ |
| G-44 (v2.2 · **확정** · 사용자 지시 2026-10-21) | 장애 진단 화면에 보일 조사 맥락 | **머리에 알람 요약(알람명·서버·자원·발생·판정·심각도 · 조건 로그 운영자만)·채팅 대상·조사 이유(플레이북 선택 근거/HolmesGPT 이유) · 실행 기록 스텝마다 목적(`narrate.focus`)** · 알람 요약은 판정 기록에서(보존 범위 밖이면 「찾지 못함」) |
| G-45 (v2.3 · **확정** · 사용자 지시 2026-10-21) | 조사 탭에서 이벤트를 찾아 바로 조사 | **장애 진단 「진행 중 목록」 위 검색 칸(시간·알람명·서버) → 판정 기록 `/noise/decisions`(G-29와 같은 경로 · 칸별 부분일치 가산) → [조사 시작] = §5.6.2 ① 접수(권한·상한·합류 동일)** · 새 플래그 없음(실시간 화면 + 다시 분석 둘 다 켜졌을 때만) |
| G-46 (v2.4 · **확정** · 사용자 지시·결정 2026-10-22) | 노이즈 관제에서 조사를 여는 곳 | **열려 있는 포털 창의 「장애 진단」 탭 그 조사 하위 탭(꺼져 있으면 「조사 대화」 탭) — 관제 버튼 1개(켜짐 「장애 진단에서 보기」·꺼짐 「조사 대화로 열기」) · 포털 찾기 opener → BroadcastChannel(마지막으로 본 탭 1개) → 없을 때만 새 탭 `/?diagnosis=`** · 이름 창 탐색 폐기 · G-34 노이즈 드로어·G-43 관제 버튼 개정 |
| G-47 (v2.5 · **v2.7 확정** · 사용자 결정 2026-10-22) | 문장을 누가 만드나 · AI 해석은 언제 보이나 (§5.6.4 ⓕ·ⓖ) | **단계가 끝날 때마다 AI 해석 작성 + 주의 사항 명기(ⓖ C1~C8 — 계획서·화면 ⓘ)** · 사실 문장(결과 한 줄·왜·조회·건너뜀)은 정해진 문형으로 즉시 · 호출 상한 없음(v2.8) · 실패 시 문형 문장만 · ~~문형만·종결 뒤 일괄~~ · ~~문장 전부 AI~~ |
| G-48 (v2.5 · **v2.7 확정** · 사용자 결정 2026-10-22) | HolmesGPT 카드의 「AI 판단」을 사용자에게도 보이나 | **사용자에게도 보인다 — 「AI 판단 — 검증 전」 배지 · 두 겹 가림 · ⓘ 주의(C1·C2·C7 + 자동 검사 미적용)** · G-38 개정 · ~~관리자만~~ · ~~사용자에게 첫 문장만~~ |
| G-49 (v2.5 · **v2.7 확정 — 권고안** · 사용자 결정 2026-10-22) | 사용자 예시 사슬(프로세스 → WAS 인스턴스 CPU → 실행 중 SQL)을 플레이북에 넣나 | **host.cpu·host.process에 조건부 3단계(S2 WAS 프로세스 CPU · S3a 실행 중 요청 · S3b 실행 중 SQL — 리터럴 가림 · 사용자·관리자 모두 · 현재값 배지)** · ~~S2만~~ · ~~추가 없음~~ |
| G-50 (v2.5 · **v2.7 확정 — 대안 1** · 사용자 결정 2026-10-22) | 장애 진단 화면 배치 | **조사 하위 탭 안 「조사 흐름」·「실행 기록」 전환 탭 — 처음은 조사 흐름 · 실행 기록 = 현행 화면 그대로**(권고와 다름 — 권고는 카드 기본 + 상세 기록 접힘) · ~~카드 기본 + 접힘~~ · ~~현행 좌우 + 카드 덧붙임~~ |
| G-17 (v1.2) | KB 운영 책임 | 유형별 `owner` 팀 지정 · KE 승인권자(문제 관리 담당) · 검토 주기 기본값(런북 180일 · KE 90일 [제안]) |

## 10. 다음 단계

(v2.7 · 2026-10-22 · G-47~G-50 확정) **다음: W20 a(sre_agent 문장·이벤트) → b(플레이북 보강 — G-49 결과에 따라) → c(본체 가림 키·화면·매뉴얼) 순서로 위임.** a와 b는 독립이고 c는 a 뒤에 둔다. 이하 이전 기록: (v2.0 · 2026-10-19 · **W13~W15 구현 완료**) 다음: ① 사용자 경로 2차 마스킹 강화 여부 사용자 결정(토큰 단위 대조 — D-325 ③ 범위 확대) ② 두 플래그를 켠 실프로세스 종단 1회(가짜 LLM · 플레이북·HolmesGPT 동시) + 매뉴얼 U-70·A-81 캡처 ③ 내부망 실 도구 출력 표본으로 가림 확인 후 투입 ④ W7 shadow. 이하 이전 기록: (v2.0 · 2026-10-19) **G-34~G-42 확정 · D-341 부기 4 · D-325 부기 등재 완료 → W13(sre_agent 실행 기록·가림 보강) → W14(본체 허브·SSE) → W15(장애 진단 탭·매뉴얼) 순서로 team-lead 위임.** W13은 다른 Wave와 독립이고, W15는 W14 뒤에 둔다. W7(shadow)은 W13~W15와 병행할 수 있으며 실행 기록이 shadow 관찰에도 쓰인다. 아래는 v1.9까지의 다음 단계다.

G-1~G-33 전건 확정 · D-341 등재·부기 3건 완료(2026-10-17) → W0 착수(team-lead 위임 · 계획 시작 SHA 기록). W10은 `plans/155` 공용부(조사 저장소 `kind=diagnosis` · 접수·조회 라우트 · 지연 poll 공용 모듈)가 먼저 있어야 한다 — 155가 먼저 끝나지 않으면 W10에서 그 공용부를 155 설계 그대로 구현하고 155에 완료를 표기한다. G-19 2단계(결정적 문맥 시드)와 G-20(브리핑 질문)은 별 계획. W11은 W4(엔진 배선) 뒤, W12는 W10(비동기 `sre_diagnose`·조사 카드) 뒤에 둔다.

### 10.1 잔여 작업 일람 (2026-10-23 갱신 · 처음 2026-10-21)

머리 각 판(v1.x~v2.3)과 §10에 흩어져 있던 잔여를 한곳에 모았다. 끝나면 「처리」 칸에 적고 머리 상태·`plans/INDEX.md` 상태 칸을 함께 고친다. 2026-10-22 사용자 지시 *"잔여 작업을 진행하라."* + 결정 3건(아래 3·6·14)으로 처리했다 — D-341 부기 9.

**남은 것**

| # | 잔여 | 근거(Wave · 수용 기준 · 게이트) | 막힌 이유 · 필요한 것 | 할 곳 |
|---:|---|---|---|---|
| 1 | **W7 내부망 shadow** — `parallel` 모드 · 브리핑만 저장 · 통보 무변경 · 운영자 선택 수집. (외부망 가짜 LLM 평가는 끝남 — 합성 세계 응답 모양이 실 도구와 달라 플레이북 판정 비교는 불가 · D-341 구현 부기 ③) | W7 · 수용 7 | 내부망 실 알람·FabriX | 내부망 |
| 2 | **수용 기준 6 측정** — 단계마다 AI 해석(호출 상한 없음 · v2.8)일 때 수집+서술 ≤ 120s · FabriX 단계당 지연 | 수용 6 | MLX·가짜 LLM 수치는 로직 확인용 | 내부망 |
| 3 | **R-D 필드 확보** — 게이트웨이 프로파일 응답의 커넥션 획득/SQL 실행 구조화 필드(있으면 판독 규칙이 바로 쓴다) | W0·W3 · 수용 2·2′·9(**2026-10-22 완화로 충족**) | 제니퍼 API에 값이 있는지 내부망 확인 · 로컬 제니퍼는 라이선스 없음 | 내부망 → `apm_gateway` |
| 5 | **SME 자료(G-10)로 카탈로그 보정** | W6 · G-10 · §4.1 [미확인] | 자료 미입수 | 사용자 · SME |
| 7 | **내부망 실 도구 출력 표본으로 가림 확인** — 놓친 꼴은 `log_masking.py`·`activity_masking.py`에 더한다 | W13 · §8 위험 「명령·출력 노출」 | 운영 투입 전 필수 · 표본은 내부망에만 | 내부망 |
| 12 | **알려진 한계 확정** — `/active`·summary의 엔진 키는 이벤트를 본 조사에만 · 종결 잡 발췌 TTL까지 유지(최악 약 128MB · 전형 43MB) | W13·W14 · §8 위험 「메모리」 | 내부망 메모리 실측 뒤 유지 또는 조기 폐기 결정 | 내부망 → 결정 |
| 13 | **커밋** — v2.1~v2.3(W16~W18)과 2026-10-22 잔여 처리분 미커밋 | — | 사용자 요청 시 | 사용자 |
| 15 | **G-26 캐시 적중 실관측** — 실프로세스에서 HolmesGPT는 돌았으나 가짜 LLM이 비MCP 도구(`TodoWrite`)만 불러 적중 0(설계상 우회). 적중은 단위 테스트(16 스레드 · 실호출 1·적중 8)로만 확인 | G-26 · D-341 부기 9 ② | 실 데이터 도구·실 LLM 필요 | 내부망 shadow(감사 `cache_hits`) |
| 17 | **W20 「조사 흐름」(v2.5~v2.7 · §5.6.4)** — 단계 카드(왜·조회·결과·단계 끝 AI 해석 + 주의) · 사용자에게도 AI 판단 · host.cpu·host.process 사슬 보강 · 「조사 흐름」·「실행 기록」 전환 탭 | W20 · 수용 15·21~29 · G-47~G-50(**2026-10-22 확정**) | **처리함(2026-10-22 · W20 구현 · 미커밋)** — 잔여: R-E 임계 보정(사용자 예시 수치에서 `other_process`) · 내부망 지연·가림 표본 | 외부망 구현 → 내부망 지연·가림 확인 |
| 18 | **W21 P0 호출별 소요 실측 + W22 값 재보정** — 실행 기록 탭·게이트웨이 로그로 S0·S1 호출별 소요 · W21·W22 적용 뒤 S0·S1 결손·재시도 성공률이 줄었는지 · 기동 로그 「조사 시간 사슬」 1줄로 폐쇄망 `.env`가 옛 값(300·120 등)을 덮고 있지 않은지 · 실측으로 W22 사슬 값(MCP 1건 60 · 스텝 90 · S0 120 · 수집 240 · 조사 600) 재보정 · P2(app_health 호스트 묶음) 재검토 근거 | W21·W22 · 수용 35·41 · §5.6.5·§5.6.6 | 내부망 실 조사 | 내부망 |
| 19 | **W21 P7 게이트웨이 실패 진단** — 실시간 API(도메인 10020) `apm_api_error` · `apm_resource_pool` `source_unavailable` 원인 | W21 · §5.6.5 | 내부망 제니퍼·게이트웨이 로그 | 내부망 → `apm_gateway` |
| 20 | **W21 폴스타 소스 매핑 운영 값** — `SRE_PLAYBOOK_POLESTAR_SOURCE_MAP`(예 `{"cm_prd":"polestar_cm_gp"}`)을 폐쇄망 sre_agent `.env`에 둘지 · 근본 해결은 hostname 브리지(`plans/165` F3) | W21 P6 | 운영자 결정 | 내부망 `.env` |
| 21 | **W22 X-View 도구 1건 상한** — (main 병합 2026-10-23: `plans/190` D-344 ② X-View 사전 확인(`APM_XVIEW_CONFIRM_OVER` 기본 10)이 기간 모드 `apm_app_health`를 시 단위 대체로 돌려 이 항목의 app_health 몫은 덜어진다 — 남는 것은 `apm_slow_transactions`·`apm_change_impact`) `apm_app_health`(과거 시각)·`apm_slow_transactions`는 제니퍼 API 41회 · `apm_change_impact`(n 미지정)는 1 + 변경 × 122회를 순차로 불러 MCP 1건 60s에 끊길 수 있다. 안: ① X-View 도구에만 도구별 상한(약 85s) ② sre_agent가 `apm_change_impact`에 n 명시 ③ 게이트웨이 X-View 조각 병렬화 또는 `wait_seconds` 작업 핸들 폴링 | W22 · §5.6.6 ⓑ | 사용자 결정 · P0 실측(실제 건당 소요) | 결정 → sre_agent·apm_gateway |
| 22 | **W22 조사 흐름 이벤트 넘침 표기** — 600s parallel 조사 추정 230~420건으로 링 버퍼 300건을 넘을 수 있다. 실행 기록은 「오래된 기록 일부는 버퍼 상한으로 빠졌습니다.」를 띄우나 조사 흐름 탭은 표기가 없다 | W22 · §5.6.6 ⓓ | 내부망 실 이벤트 수 실측 뒤 상한·표기 결정 | 내부망 → 결정 |

**처리함(2026-10-22)**

| # | 항목 | 처리 |
|---:|---|---|
| 3 | R-D | 사용자 결정 「기준 완화 + 내부망 이월」 — §8 수용 기준 2·2′·9 문구 완화(필드 없으면 `n/a`·판단 보류 표기로 충족). 필드 확보는 위 3번으로 남김 |
| 4 | G-26 HolmesGPT 도구 경로 캐시 공용 | `sre_agent/sre_agent/diagnosis.py`(`ask(tool_cache=)` · 요청별 얕은 사본의 `_directly_invoke_tool_call` 교체) · `investigation_dispatcher.py`(잡 사본 `tool_cache`) · `interface/mcp_service.py` · 테스트 `sre_agent/tests/test_plan187_g26_holmes_cache.py`(11) |
| 6 | 사용자 경로 2차 가림 | 사용자 결정 「줄 안 부분까지」 — `src/api/routes/alarm.py` `_investigation_mask_fn`(`mask_secrets` + 값 패턴 부분 일치) · 테스트 `tests/test_api/test_plan187_inline_mask.py`(11) · D-325 부기 |
| 8 | 실프로세스 종단 1회 | Playwright(headless Chromium) — 본체 8765(인증 on · 가짜 사용자 저장소) + sre_agent 9098(`parallel`·실행 기록 on) + 가짜 LLM 19180 + 폴스타 MCP 9099(DB 없음): 이벤트 찾아 조사 → 실 접수 → 실행 기록 16행·「목적:」·「● 실시간」 → 종결 브리핑(완료) · HolmesGPT done(판단 2·도구 1) · 10분 합류 확인 |
| 10 | 실인증 사용자 경로 | 공동존 사용자: 은행존 알람 [조사 시작] → 행에 「해당 존의 알람에 대한 권한이 없습니다」(403) · 은행존 사용자가 공동존 조사를 열면 「열 수 없는 조사」 · 조건 로그는 관리자 응답에만(API 대조) · 사용자 진행 중 목록에 직접 연 조사 없음(G-41대로) |
| 11 | W16 포털 창 재사용 | 포털 머리글 「노이즈 관제」(rel=opener)로 연 관제에서 「조사 대화로 열기」 → 창 수 2→2(새 탭 0) · 포털이 「조사 대화」 탭으로 전환 |
| 9 | 매뉴얼 캡처(2026-10-22 · W19 후속) | Docker 기동 뒤 캡처 프로필 `capture-diag`(`scripts/manual/snapshot.py` — `capture` + 실시간 화면 on · 조사 서비스 주소는 닫힌 포트) 신설 · 예시 조사의 실행 기록 요약을 `fixtures/alarms/briefing.json` `activity`로 넣고 `alarms.seed_briefing`이 보관 파일에 씀 · 장면 `diagnosis-tab`(U-70)·`investigation-conversation`(U-63) 촬영 · 매뉴얼 그림·번호 설명 연결 · `pytest tests/test_manual` 675 통과 |
| 16 | W19 본체 실화면 확인(2026-10-22) | 캡처 서버 18981(인증 on · 예시 판정·조사 시드) + Playwright headless Chromium. 장애 진단 켜짐: 머리글 링크로 연 관제 · 주소로 연 관제 · 관리자 노이즈 화면(「결정 이력」 탭) 세 경우 모두 「장애 진단에서 보기」 → 기존 포털이 장애 진단 탭·그 조사 하위 탭으로 전환, 창 수 2→2 · 안내문 표시 · 포털 없음 → 새 탭 1개가 장애 진단 탭으로 열림. 꺼짐(`capture` 프로필): 머리글 링크·주소 두 경우 「조사 대화로 열기」 → 기존 포털 「조사 대화」 탭·고정 카드, 창 수 2→2. 브라우저 포커스 이동은 headless에서 판정 불가 — 안내문으로 대신한다 |
| 14 | W12 2단 배선(신규 · D-341 구현 부기 ④) | 사용자 결정 「2단에도 연결」 — `src/graph.py` `_wire_tier2`·`route_after_field_mapper_tier2` · `src/nodes/fault_diagnosis.py` `chat_symptom_entry` · `src/domain/symptom_router.py` `DIAGNOSIS_CUES`·`has_diagnosis_cue` · 테스트 `tests/test_plan187_w12_ladder2.py`(19) · 매뉴얼 U-69 주의 1줄. **이탈**: 진단 표지(원인·진단·왜·rca) 조건 추가 — 조회 질의가 조사로 빠지지 않게 3단 프롬프트 규칙을 결정적으로 옮김 |

**별 계획으로 넘긴 것**(이 계획서 잔여 아님): G-19 2단계(결정적 문맥 시드) · G-20(브리핑 질문) · `plans/155` 나머지(결과 셀 판정 등).

**범위 밖 관찰**
- (2026-10-21) 폰 폭(390px)에서 본 화면 머리글(테마 · 노이즈 관제 · 매뉴얼 버튼)이 212px 가로로 넘친다 — 질의응답 탭에서도 같다.
- (2026-10-22) 사용자 경로 `/noise/decisions`는 존과 무관하게 전 존 판정 기록(가림 적용)을 보여 준다 — 기존 설계(사용자 경로 읽기 5종 · D-245)라 「이벤트 찾아 조사」에도 타 존 행이 보이고, [조사 시작]은 존 검사에서 거부된다. 목록부터 존으로 거를지는 별 결정.
- (2026-10-22) 권한 없는 조사를 `/?diagnosis=`로 열면 하위 탭 머리가 「⟳ 진행」으로 보인다(본문은 「열 수 없는 조사」) — 표시만의 문제.

---

## 부록 A. 출처 목록 (v1.2 — 검증 수준은 §3.0)

**제니퍼 공식(원문 PDF 확보 · 사례 4건은 OCR)**
- X-View Technical Article(2009) https://cdn.jennifersoft.com/wp-content/uploads/Documents/en/JENNIFER_White_Paper-Why_Monitor_Response_Time_by_Transaction_X-View.pdf [원문]
- Performance Analysis Model(2011) https://cdn.jennifersoft.com/wp-content/uploads/Documents/en/JENNIFER_White_Paper-Performance_Analysis_Model.pdf [원문]
- Case Study: Misconfigured JDBC Connection https://cdn.jennifersoft.com/wp-content/uploads/Documents/en/JENNIFER_Case_Study-Direct_JDBC.pdf [원문·OCR]
- Case Study: Services Accessing External Systems https://cdn.jennifersoft.com/wp-content/uploads/Documents/en/JENNIFER_Case_Study-External_Transaction.pdf [원문·OCR]
- Case Study: High CPU Utilization https://cdn.jennifersoft.com/wp-content/uploads/Documents/en/JENNIFER_Case_Study-CPU.pdf [원문·OCR]
- Case Study: OOM Detection and Analysis https://cdn.jennifersoft.com/wp-content/uploads/Documents/en/JENNIFER_Case_Study-OOM.pdf [원문·OCR]
- 문서 목록 https://jennifersoft.com/en/library/documentations/ [공식 문서]

**방법론 · 학술**
- Google SRE book 12장 https://sre.google/sre-book/effective-troubleshooting/ · 6장 https://sre.google/sre-book/monitoring-distributed-systems/ [원문]
- Brendan Gregg USE https://www.brendangregg.com/usemethod.html · Linux 체크리스트 https://brendangregg.com/USEmethod/use-linux.html [원문]
- Netflix 60초 분석(원문 403) → https://www.thomas-krenn.com/en/wiki/Linux_performance_analysis_in_60_seconds [2차]
- Tom Wilkie RED https://grafana.com/files/grafanacon_eu_2018/Tom_Wilkie_GrafanaCon_EU_2018.pdf [2차]
- RCACopilot, EuroSys '24, DOI 10.1145/3627703.3629553 · https://arxiv.org/abs/2305.15778 [원문]
- Ahmed et al., ICSE 2023 https://arxiv.org/pdf/2301.03797 [2차] · AIOps LLM 서베이 https://arxiv.org/pdf/2406.11213 [2차]

**자동 조사 제품·사례**
- CNCF/STCLab HolmesGPT 알람 자동 진단 https://www.cncf.io/blog/2026/04/21/auto-diagnosing-kubernetes-alerts-with-holmesgpt-and-cncf-tools/ [원문]
- Azure SRE Agent https://learn.microsoft.com/azure/sre-agent/automate-incidents [공식 문서]
- Datadog Bits AI SRE https://docs.datadoghq.com/bits_ai/bits_ai_sre/investigate_alerts [공식 문서]
- PagerDuty change events https://support.pagerduty.com/docs/change-events · Rundeck https://docs.rundeck.com/docs/learning/solutions/automated-diagnostics/ · AWS SSM https://aws.amazon.com/blogs/mt/use-amazon-eventbridge-rules-to-run-aws-systems-manager-automation-in-response-to-cloudwatch-alarms · Robusta https://docs.robusta.dev/master/playbook-reference/builtin-alert-enrichment.html [2차]

**자원·DB**
- GC overhead limit https://mastertheboss.com/java/how-to-solve-java-lang-outofmemoryerror-gc-overhead-limit-exceeded/ · https://support.atlassian.com/confluence/kb/confluence-crashes-due-to-outofmemoryerror-gc-overhead-limit-exceeded-error/ [2차]
- 커넥션 풀 획득·DB 세션 대조 https://oneuptime.com/blog/post/2026-08-24-correlate-pool-checkout-database-session-saturation/view [2차]
- Db2 MON_GET_APPL_LOCKWAIT https://www.ibm.com/docs/en/SSEPGG_11.5.0/com.ibm.db2.luw.sql.rtn.doc/doc/r0056429.html [공식 문서]
- MariaDB sys.innodb_lock_waits https://mariadb.com/kb/en/innodb_lock_waits-and-xinnodb_lock_waits-sys-schema-views · INNODB_LOCK_WAITS https://mariadb.com/kb/en/information-schema-innodb_lock_waits-table/ [공식 문서]
- PostgreSQL 블로킹 https://www.netdata.cloud/guides/postgres/postgres-blocking-queries/ [2차]

**시간창 · 국내**
- ServiceNow 알람 상관 규칙 https://www.servicenow.com/docs/r/it-operations-management/event-management/alert-correlation-rule-form.html [공식 문서]
- Dynatrace Davis 모델·문제 수명주기 https://docs.dynatrace.com/docs/discover-dynatrace/references/semantic-dictionary/model/davis · https://www.dynatrace.com/support/help/shortlink/problem-lifecycle [공식 문서]
- 금감원 금융 IT 안전성 가이드라인 TF 보도(2023-03) https://www.sciencetimes.co.kr/news/%EF%BC%82%EA%B8%88%EC%9C%B5-%EC%A0%84%EC%82%B0%EC%9E%A5%EC%95%A0-%EB%A7%89%EC%9E%90%EF%BC%82%EC%95%88%EC%A0%84%EC%84%B1-%EA%B0%80%EC%9D%B4%EB%93%9C%EB%9D%BC%EC%9D%B8-%EC%A0%9C%EC%A0%95/?cat=128 [2차] · 전산장애 건수 기사 https://www.kukinews.com/article/view/kuk202210120132 [2차]

**장애 대응 지식 유형 (v1.2 · 2026-10-17 직접 조회)**
- Google SRE Book Introduction(playbook MTTR 약 3배) https://sre.google/sre-book/introduction/ [원문] · Postmortem Culture https://sre.google/sre-book/postmortem-culture/ [원문]
- Google SRE Workbook On-Call(알람마다 playbook 항목 · 낡음 · 자동화) https://sre.google/workbook/on-call/ [원문]
- kube-prometheus 런북 템플릿(Meaning·Impact·Diagnosis·Mitigation) https://runbooks.prometheus-operator.dev/docs/add-runbook/ · 색인 https://runbooks.prometheus-operator.dev/ [원문]
- PagerDuty Incident Response https://response.pagerduty.com/ · 포스트모템 템플릿 https://response.pagerduty.com/after/post_mortem_template/ [공식 문서]
- NIST SP 800-61 Rev. 3 (2025-04) https://csrc.nist.gov/pubs/sp/800/61/r3/final [공식 문서 — 초록만]
- KCS v6 문서 구조 https://library.serviceinnovation.org/KCS/KCS_v6/KCS_v6_Practices_Guide/030/040/010/020 · 문서 상태 https://library.serviceinnovation.org/KCS/KCS_v6/KCS_v6_Practices_Guide/030/040/010/030 · 예시 https://library.serviceinnovation.org/Get_Help/KCS_Methodology_Help/Examples_of_KCS_Structured_Knowledge_Articles [원문]
- ITIL 4 문제 관리·KEDB(2차) https://wiki.en.it-processmaps.com/index.php/Problem_Management · https://confluence.atlassian.com/display/SERVICEDESKSERVER033/Problem+management · https://www.invgate.com/guides/problem-management [2차]
- runbook vs playbook(업계 서술) https://cutover.com/blog/incident-management-runbook · https://dev.to/squadcast/runbook-vs-playbook-whats-the-difference-134g · https://www.cortex.io/post/runbooks-vs-playbooks [2차]

**TSG 연구 (v1.2)**
- DeepRmd, ESEC/FSE 2020 산업 https://2020.esec-fse.org/details/esecfse-2020-industry-papers/9/How-to-Mitigate-the-Incident-An-Effective-Troubleshooting-Guide-Recommendation-Techn [원문 초록]
- AutoTSG https://arxiv.org/abs/2205.13457 [원문 초록]
- Nissist https://arxiv.org/abs/2402.17531 [원문 초록]
- StepFly(FSE 2026) https://arxiv.org/html/2510.10074v1 [원문 본문]
- FLASH https://www.microsoft.com/en-us/research/project/flash-a-reliable-workflow-automation-agent/ [공식 초록]
- LLexus https://www.microsoft.com/en-us/research/?p=1054683 [공식 초록]
- How to Fight Production Incidents?(SoCC 2022) https://www.microsoft.com/en-us/research/?p=881646 [미확인 — 본문 미열람 · 인용 안 함]

**찾지 못한 것([미확인])**: 현행 버전 제니퍼 공식 응답지연 분석 절차 · 금융권 성능관리 가이드라인 원문 · 알람 전후 조사 창의 공개 표준 · Oracle JDK Troubleshooting Guide 원문 페이지(2차 인용만 확인).
