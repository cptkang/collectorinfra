# 187. 알람 유형별 자동 조사 플레이북 — PAGE 알람마다 정해진 조사 단계를 결정적으로 일괄 수집하고, 단계별 LLM 소견과 종합 원인·대응 의견을 운영자에게 보인다

> **작성일**: 2026-10-17 · **v1.0** · **v1.1**(같은 날 — 사용자 지시 *"문헌 조사를 인터넷으로 접속하여 직접 다시 조사하여 계획서에 반영하라"*: 제니퍼 공식 백서 2·사례 4 원문 확보(이미지 PDF는 OCR) · 출처 검증 수준 재표기 · Datadog 3상태 서술 정정 · 결정적 판독 규칙 §4.2 신설 · S0 범위 비교 · S3 커넥션 획득 분리 · S2 WAS/호스트 CPU 대조) · **v1.2**(같은 날 — 사용자 지시 *"장애 대응에 대한 지식 저장소나 Troubleshooting Guide, 런북/플레이북, 인시던트 대응 매뉴얼, 알려진 문제 및 우회 방법 등을 자세히 조사하여 계획을 업데이트하라"*: 지식 유형 문헌 §3.7 신설 · **장애 대응 지식 저장소(KB) 설계 §5.7** — 런북 블록·알려진 오류(KEDB, KCS 구조·상태) 결정적 일치·우회 표시·매뉴얼 RAG 참고·초안→승인 수명주기 · 요건 R-14~R-17 · Wave W8~W9 · 수용 기준 8~10 · 게이트 G-13~G-17)
> **상태**: **WIP · 잔여 작업 일람은 §10.1(남은 9건 — 내부망·SME·캡처·커밋 · 2026-10-22 R-D 완화·G-26 공용·W12 2단·2차 가림·실프로세스 종단 처리)** · **v2.0 W13~W15(장애 진단 탭 · 실행 기록 · SSE) 구현 완료(2026-10-19 · 미커밋 · 두 플래그 기본 off) · 잔여: 사용자 경로 2차 마스킹 실효(사용자 결정) · 실프로세스 종단 1회 · 매뉴얼 캡처 · 내부망 실 출력 가림 표본** · **W0~W6·W8~W12 구현 완료(2026-10-19 · 미커밋) · 잔여: W7 내부망 shadow · R-D(게이트웨이 acquire/execute 구조화 필드 부재 → `db_split` n/a · 수용 기준 2·2′·9 부분) · G-26 HolmesGPT 도구 경로 캐시 공용 미구현 · 수용 기준 6(120s)·7 내부망 측정 · SME 자료(G-10) · 매뉴얼 캡처** · 모든 신규 플래그 기본 off(비트 동일 검증) · 검증·보안 감사 지적 교정 완료 · (구: TODO — 계획(코드 0건)) · **게이트 G-1~G-17 확정(2026-10-17 · 권고안 전부 · 사용자 「게이트 권고안대로 확정」)** · **G-18~G-21 확정(같은 날 · 권고안 · 사용자 「권고한데로 반영하라」)** · **G-22~G-27 확정(같은 날 · 권고안 · 사용자 「권고대로 정리하라」)** · **G-28~G-33 확정(같은 날 · 권고안 · 사용자 「권고안대로 확정하여 계획서에 반영하라」)** — 게이트 전건 확정 · **D-341 확정**
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
> **회귀 대상 파일**: 131개 — 본체 `src/api/{investigation_poll.py,settings_catalog.py,routes/alarm.py,routes/admin_incident_kb.py,server.py,thread_history.py}` · `src/config/alarm.py` · `src/domain/{investigation_followup,kb_draft,symptom_router}.py` · `src/infrastructure/investigation_request_limit.py` · `src/nodes/fault_diagnosis.py` · `src/prompts/investigation_followup.py` · `src/static/{index.html,noise.html,admin/*.html,css/style.css,js/{app,briefing-view,noise,admin-incident-kb}.js,manual/*}` · `noise_gate/{application/nodes/{alarm_notifier,investigation_trigger,notification_gate}.py,infrastructure/{decision_store,investigation_store,incident_kb_store,sre_agent_client}.py}` · `config/{rag_collections.yaml,settings_help/noise_gate_investigation.yaml}` · `scripts/manual/**` · sre_agent `application/{briefing_builder,investigation_dispatcher,investigation_guidance,investigation_jobs,playbook_*}.py`·`domain/{playbook*,known_error,engine_agreement,source_adapter,remediation}.py`·`infrastructure/{playbook_loader,known_error_loader,tool_result_cache,mcp_tool_client}.py`·`interface/mcp_service.py`·`settings.py`·`config/{playbooks/*.yaml,known_errors/README.md}`·`scripts/{runbook_coverage.py,investigation_bench/*}` · 테스트(`tests/**/test_plan187_*` · `sre_agent/tests/test_plan187_*` · 픽스처) · 목록 정본: `git status` 미커밋 변경
> **계획 끝 회귀(2026-10-19)**: `regress.py --base 99ec1ea4 --files <130>` — 본체 통과 4,314 · 원래 실패 6(TestProcessApiClient 2 · prometheus_client 부재 2 · settings_catalog `.env` 2) · sre_agent 통과 1,785 · 실패 0 · arch/overfit/config_fallback 통과 · ruff·mypy 이번 diff 줄 0 · 범위: 모듈 단위 — 전체 미실행 · `[전체 회귀 권고]` 허브 모듈 src.config.alarm 88% · 본체 레인은 plans/186 시각 의존 parametrize id의 xdist 수집 불일치로 직렬 실행(docs/18 2026-10-19)
> **전체 회귀(2026-10-19 · 사용자 요청)**: `regress.py --full --base 99ec1ea4` — 본체 통과 23,483 · 실패 55 · 에러 7 · 건너뜀 93(xdist -n 7 수집 정상) → 귀속 원래 실패 67 · 이번 변경 탓 1(`test_every_noise_key_has_section` — plans/187 `NOISE_INVESTIGATION_*` 13키 `SECTION_BY_KEY` 미등재 → `src/api/settings_catalog.py`에 「자동 장애 조사」로 등재, 재실행 통과) · mcp_server 통과 204 · 에러 6(prometheus_client·pymysql 부재) · apm_gateway 도구 TIMEOUT(2400초 — `test_plan134_w0b_verify.py::test_disconnect_before_promotion_cancels_job` 기준에서도 무한 대기) → 그 1건 제외 별도 실행 통과 1,628 · 실패 2(tool_list snapshot — 기준에서도 실패) · sre_agent(도구 미실행 — 자체 venv 없음) 타 체크아웃 venv로 통과 1,785 · 정적 게이트 통과 · 범위: 전체(사용자 요청)
> **origin/main 병합(2026-10-19 · plans/188 D-342·D-343과 통합)**: `_agent_getter`에 188 C1 재생성(툴셋 FAILED면 1회 새로 생성 · 잠금)을 합치고 `_default_diagnose_fn(settings, toolset_check, agent_getter)`로 두 배선 유지 · 플레이북 심층 결과도 188 A안·C7 기준(`tools_unavailable`·도구 호출 마크업)이면 서술 없이 `deep_dive.status=skipped`+사유(`_deep_skip_reason` · `sre_agent/tests/test_plan187_merge188_deep_skip.py`) · sre_agent 전체 2,059 통과
> **v2.0 구현(2026-10-19 · W13~W15 · 계획 시작 SHA `a4514020`)**: W13 sre_agent `domain/activity_log.py`(신규 · 링 버퍼 300건/256KB · `safe_append` best-effort)·`infrastructure/activity_masking.py`(신규 — `mask_secrets` 뒤 명령행·설정 비밀값 꼴 추가 가림)·`application/{investigation_dispatcher,investigation_jobs,playbook_steps,playbook_narrator}.py`·`diagnosis.py`(`_StreamDrain` 훅)·`infrastructure/tool_result_cache.py`·`interface/mcp_service.py`(`activity_since=-1`)·`settings.py`(`SRE_ACTIVITY_LOG_ENABLED`)·`.env.example` · W14 본체 `src/api/investigation_live_hub.py`(신규)·`investigation_poll.py`·`routes/alarm.py`(`/alarm/investigations/{active,stream}` · `activity_since`)·`server.py`·`src/config/alarm.py`(`NOISE_INVESTIGATION_LIVE_VIEW_ENABLED`·`_LIVE_POLL_SECONDS`)·`settings_catalog.py`·`config/settings_help/noise_gate_investigation.yaml`·`noise_gate/infrastructure/{sre_agent_client,investigation_activity_store}.py` · W15 `src/static/js/diagnosis-view.js`(신규)·`index.html`·`app.js`·`noise.js`·`briefing-view.js`(`runningText` 옵션)·`css/style.css`·`noise.html`·`admin/noise.html` · 매뉴얼 U-70·A-81 · 테스트 `sre_agent/tests/test_plan187_{w13_activity,v2_verify_activity}.py`·`tests/test_api/test_plan187_{w14_live_api,w14_live_hub,v2_verify_live}.py`·`test_ui_plan187_w15_diagnosis.py` + 고정값 갱신 6파일. **계획 끝 회귀**: `regress.py --base a4514020 --files <45>` rc=0 — 본체 통과 9,773·실패 0 · sre_agent 2,095·실패 0 · arch/overfit/config_fallback 통과 · ruff·mypy 이번 diff 줄 0 · 범위: 모듈 단위 — 전체 미실행 · `[전체 회귀 권고]` 허브 모듈 src.config.alarm 88% · `SreAgentClient.poll` 시그니처. 이후 검증 교정 라운드는 실패 테스트·모듈 선택분 + `--no-tests`만. **실측**: poll당 MCP 새 연결 21.9ms vs 재사용 5.9ms(로컬 가짜 서버) · 본체는 uvicorn 단일 워커 전제(`alarm_bus`와 같음 — 다중 워커면 폴러 ×워커). **이탈**: 비밀값 꼴은 `log_masking.py`(정본 `src/utils/log_masking.py`의 바이트 사본 4개 일치 테스트)가 아니라 신규 `activity_masking.py`에 둠 · 스트림 최대 수명 3600초·watch 200개 방어 상한 · 사용자 경로 `denied` 사유 `unavailable` 통합 · 노이즈 드로어는 접수 직후 자동 새 탭 대신 「장애 진단에서 보기」 버튼(팝업 차단). **잔여**: 사용자 경로 2차 마스킹(D-325 ③ `_investigation_mask_fn`)이 줄 전체 일치라 한 줄 JSON 안의 키·토큰은 못 가림(별 결정) · 실프로세스 종단 1회 · 매뉴얼 캡처 · `/active`·summary에 엔진 키는 이벤트를 본 조사만 · 종결 잡 발췌 TTL까지 유지(최악 약 128MB · 전형 43MB).
> **v2.1(2026-10-21 · W16 · 사용자 지시)**: *"노이즈 관제에서 바로 「조사 대화로 열기」 버튼이 언제든 보여야 된다 … 「조사 대화로 열기」를 누르면 「질의응답」, 「이벤트 알림」 옆에 같은 레벨의 탭이 열리는 방식이어야 하는데 새로운 페이지가 열린다. 수정하라."* → **실측**: 관제 드로어의 버튼은 ① 플레이북 브리핑 요약형에만 있고(종전 브리핑 0) ② 장애 진단이 켜지면(`liveViewOn`) 감춰졌으며 ③ 누르면 `/?investigation=` 새 브라우저 탭 — 그 탭에서도 장애 진단이 켜져 있으면 조사 대화가 아니라 진단 탭으로 갔다. **변경(G-43)**: §5.6.1 1 진입 개정 — 조사 대화는 본 화면의 **「조사 대화」 뷰 탭**(질의응답·이벤트 알람·장애 진단과 같은 줄 · × 닫기)으로 연다. 두 탭은 대화 화면을 함께 쓰고 탭을 옮길 때 대화(말풍선·thread·범위 칩·고정 카드·입력 초안)를 바꿔 끼운다 — 질의응답 대화는 그대로 남는다 · 스트리밍 중 전환 거부 · 이력 열기·새 대화는 질의응답 탭. 노이즈 관제 ⓒ 머리에 「조사 대화로 열기」를 **조사 id가 있으면 늘**(브리핑 종류·진행 상태·장애 진단 켜짐 무관 · break-glass 제외) 달고, 누르면 열려 있는 포털 창(`window.name=kbaiops-portal` · 머리글 노이즈 관제 링크 `rel="opener"`)을 찾아 그 창의 「조사 대화」 탭을 연다 — 못 찾으면 새 탭 `/?conversation=<id>`(진단 켜짐과 무관하게 조사 대화). 「장애 진단에서 보기」(새 탭 `/?diagnosis=`)·알람 카드 요약형 버튼 숨김(진단 켜짐 시)은 그대로. 산출물: `src/static/{index.html,css/style.css,js/app.js,js/noise.js,noise.html,admin/noise.html}`(캐시 버전 app 32·style 33·noise 17) · 테스트 `tests/test_api/test_ui_plan187_w16_conversation_tab.py`(node 실행 6건) + 고정값 갱신 5파일 · 매뉴얼 U-63. **미확인**: 실브라우저 창 이름 재사용(확장 미연결로 실측 못 함 — 브라우저가 다른 창 묶음으로 열면 새 탭 폴백).

> **v2.2(2026-10-21 · W17 · 사용자 지시)**: *"조사페이지에서 알람의 내용이 없어 어떤 알람으로 조사하고 있는지가 보이지 않는다. 그리고 조사하는 프로세스는 무엇때문에 어떤것을 조사하는지에 대한 정보도 같이 보여줘야한다."* → **실측**: 「장애 진단」 하위 탭 머리는 조사 대상(`subject`) 또는 조사 id와 종류·엔진·진척뿐이고 알람명·서버·심각도가 없었다. 실행 기록은 「S2 동작명」처럼 무엇을 했는지만 있고 왜(플레이북을 고른 근거 · 스텝이 보는 것 · HolmesGPT로 간 이유)가 없었다. **변경(G-44)**: ① 본체 조회 응답(실시간 화면 켜짐 + `activity_since` 조회에만)에 판정 기록 요약 `alarm`(알람명·서버·자원·발생 시각·판정·심각도 · 조건 로그는 운영자만 · 사용자 경로 마스킹)을 붙인다. ② sre_agent 실행 기록 이벤트에 선택 키 `purpose` — 플레이북 선택(`started`·playbook: 「플레이북 X 선택」 + 일치 조건/fallback 사유) · 스텝 시작·끝(`narrate.focus`) · HolmesGPT 시작(조사 이유 · 플레이북 미선택 사유 · 심층 모드 의미). 허브 사용자 키·보관 요약 키에 `purpose`(마스킹·상한 대상). ③ 화면 머리에 「알람 / 조건(운영자) / 대상 / 조사 이유」 줄(`dv-target`), 탭 이름은 「서버 · 알람명」, 스텝 행 아래 「목적: …」, 도구 행은 마우스 올림 설명. 판정 기록이 없으면 「알람 정보를 찾지 못했습니다」. 플래그 off = 비트 동일. 산출물: `sre_agent/sre_agent/{domain/activity_log.py,application/investigation_dispatcher.py}` · `src/api/{routes/alarm.py,investigation_live_hub.py}` · `noise_gate/infrastructure/investigation_activity_store.py` · `src/static/{js/diagnosis-view.js,css/style.css,index.html}`(캐시 diagnosis-view 3·style 34) · 테스트 `sre_agent/tests/test_plan187_w17_purpose.py`·`tests/test_api/test_plan187_w17_alarm_brief.py`·`tests/test_api/test_ui_plan187_w17_diagnosis_head.py` · 매뉴얼 U-70.

> **v2.3(2026-10-21 · W18 · 사용자 지시)**: *"조사 탭에서 시간, 알람, 서버 등의 정보로 이벤트에 대해 검색하여 바로 조사를 진행할 수 있는 기능도 추가하라."* → **변경(G-45)**: 「장애 진단」 탭의 「진행 중 목록」 위에 **「이벤트 찾아 조사」** 칸 — 시작·끝 시각(판정 시각 기준 · 비우면 최근 24시간 · 보존 30일)·알람명·서버(부분일치)로 판정 기록을 찾고(최근 30건), 행의 **[조사 시작]**이 §5.6.2 ① 「분석 요청」과 **같은 접수 라우트**(`POST /alarm/{alarm_id}/investigations` — 존 검사 fail-closed · 상한 공유 · 10분 합류 · 통보 없음)로 접수한 뒤 그 조사의 하위 탭을 연다. 검색은 G-29처럼 **새 API 없이** `/noise/decisions` 사용자 경로를 쓰고, 칸을 나눠 거르도록 선택 쿼리 `alarm_q`(알람명)·`server_q`(서버명·hostname) 부분일치를 가산한다(미지정 = 종전과 같음). 칸은 `/active` 응답 `event_search`(= `NOISE_INVESTIGATION_MANUAL_ENABLED`)가 true일 때만 보인다 — 새 플래그 없이 기존 두 옵트인(실시간 화면·다시 분석)이 모두 켜졌을 때만 동작한다. 진단 화면 파일에는 여전히 쓰기 요청이 없다(접수 함수는 app.js가 주입 · D-003 정적 테스트 유지). 실패·거부·합류 사유는 그 행에 보인다. 산출물: `noise_gate/infrastructure/decision_store.py`(`list_decisions` 인자 2) · `src/api/routes/{noise_dashboard.py,alarm.py}` · `src/static/{js/diagnosis-view.js,js/app.js,css/style.css,index.html}`(캐시 diagnosis-view 4·app 33·style 35) · 테스트 `tests/test_api/test_ui_plan187_w18_event_search.py`·`tests/test_api/test_plan187_w18_event_search_api.py` · 매뉴얼 U-70. **실브라우저 확인(2026-10-21 · Playwright headless Chromium · 별 포트 8765 · 인증 off)**: 검색 칸 표시 · 서버/알람명/시간 구간 필터 · 시작>끝 안내 · 조사 서비스 미가동 시 행 안의 실패 사유 · (목 응답으로) 하위 탭 「서버 · 알람명」·머리 줄·「목적:」 확인 · 버튼·날짜 입력 기본 모양과 없는 토큰 `--border-color`를 교정(`--border` · style 캐시 35 유지). 미확인은 §10.1 8·10.

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
| LLM 부하 | — | HolmesGPT는 조사당 최대 40 step(`settings.py:26` `max_steps=40` · 넓은 트리아지는 30 step을 넘긴다는 주석)이다. 플레이북은 ≤8회다. 모든 PAGE에 두 엔진을 다 돌리면 조사당 LLM 호출이 최대 약 48회가 된다. 운영 FabriX는 과금 대상은 아니지만 처리 용량을 쓴다 |
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
- LLM 호출 수: 단계 소견 ≤7 + 종합 1 = ≤8회/알람. 대안(단일 호출 섹션 출력)은 G-5.

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
  - 노이즈 관제 페이지(break-glass 운영자 토큰 · 헤더 전용)는 기존 `noise_stream`처럼 fetch 스트림으로 같은 경로를 읽는다 — 다만 G-34에 따라 노이즈 쪽 진입은 본 화면 `/?diagnosis=<id>` 새 브라우저 탭이 기본이다.
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
6. LLM 호출 ≤ 8회/알람 · 플레이북 수집+서술 ≤ 120s(내부망 측정 — MLX 수치는 로직 확인용).
7. W7 평가에서 원인 적중률이 HolmesGPT 단독 대비 악화 없음.
8. (v1.2) 활성 플레이북 전부가 런북 블록을 갖고(커버리지 100%), 검토 기한 경과 시 배지가 뜬다.
9. (v1.2) JDBC 획득 지연 픽스처에서 validated KE가 「일치」로, 같은 증상이지만 `db_split=execute`인 픽스처에서는 「불일치」로 나온다(KCS Environment·Cause Test가 가르는 경우). 필요한 스텝이 결손이면 「판단 보류」. (2026-10-22 완화) `db_split` 필드가 없는 실 게이트웨이 응답에서는 「판단 보류」로 나오면 충족이다 — 「일치/불일치」 분기는 필드 주입 픽스처로 확인한다.
10. (v1.2) 런타임 경로 어디에서도 KE·런북 파일에 쓰지 않는다(파일 쓰기 0 단언) · `not_validated`·`wip`는 가설 순위에 영향 0.
11. (v1.9) 노이즈 콘솔·채팅 알람 탭에서 30일 안의 PAGE가 아닌 알람을 찾아 「분석 요청」하면 플레이북 조사가 새 레코드로 생기고, 원 알람의 기존 조사는 바뀌지 않는다.
12. (v1.9) 「A1 응답 지연 원인 알려줘」처럼 대상·증상이 결정적으로 확정되는 질의는 `was.latency` 플레이북으로, 「그 서버 이상해」처럼 확정되지 않는 질의는 HolmesGPT로 간다 — 라우팅 판정에 LLM 호출 0회.
13. (v1.9) 사용자 요청 조사는 D-335 ⑥ 상한을 자동 조사와 공유하고, 통보·승격을 일으키지 않는다.
14. (v2.0) 플레이북 조사에서 각 도구 호출이 시작 후 폴러 한 주기(5초) + 푸시 지연 안에 실행 기록에 「진행」으로 나타나고, 끝나면 상태·소요·행 수로 바뀐다.
15. (v2.0) HolmesGPT 조사(심층·진단)도 도구 호출마다 실행 기록이 쌓인다 — 종결 전 빈 화면 0. 관리자에게는 AI 메모가 「검증 전」 배지로, 사용자에게는 보이지 않는다.
16. (v2.0) 동시 상한 2에서 조사 3건을 접수하면 3번째는 「대기 — 앞 n건」으로 보이고, 차례가 오면 진행으로 바뀐다.
17. (v2.0) 조사 A 진행 중에 조사 B를 열면 하위 탭이 2개가 되고, A는 배경에서 상태를 이어 받아 종결까지 바뀐다. 같은 조사를 다시 누르면 탭이 늘지 않는다. 하위 탭이 몇 개든 브라우저의 조사 스트림 연결은 1개다.
18. (v2.0) 조사가 300초를 넘어도(대기 포함) 화면 갱신이 종결 전에 멈추지 않는다. 연결 끊김·강등·60초 무수신은 사유와 함께 보인다.
19. (v2.0) 비밀값(명령행 표본 포함)은 sre_agent 응답에 나오지 않고, 사용자 경로의 한 줄·발췌는 D-325 ③ 마스킹을 한 번 더 거친다. 사용자 진행 중 목록에는 자기 채팅 조사만, 관리자 목록에는 전부가 보인다.
20. (v2.0) 두 플래그 off에서 MCP·API 응답과 화면이 종전과 비트 동일하고 조사 스트림 경로는 404다(기존 테스트).

**위험**
- **호스트 브리지**: 제니퍼 hostname ↔ 폴스타 서버명 정확 일치 결함(`plans/165` F3) — S4~S6이 0건이 될 수 있다. W0에서 F3 해결 여부 확인 · 미해결이면 S0가 「폴스타 서버 미확정」을 명시하고 폴스타 스텝을 결손 처리(침묵 0건 금지).
- **실시간 전용 데이터**: 풀·프로세스 Top은 알람 시점이 아니다 — 배지 + 소견 프롬프트에 「현재값」 명시. 지연(조사 대기열) 크면 가치 하락 → G-9.
- **폴스타 시간 단위**: 시간(h) 집계라 10분짜리 스파이크는 희석된다 — Prometheus 구성 시 `prom_metric_range` 우선(G-8).
- **LLM 비용·지연**(FabriX 운영): 알람당 8회 × PAGE 건수 — `plans/162`로 PAGE 모수가 줄기 전에는 시간당 예산 가드(`investigation_hourly_budget`) 공유.
- 제니퍼 프로파일 예산 · 게이트웨이 부하 — S0 대상 상한 3 · 프로파일 1건.
- (v1.2) **KB 신뢰 붕괴**: 낡은 Validated KE가 잘못 일치하면 운영자가 엉뚱한 우회를 따른다 — `cause_test` 필수 · 검토 기한 · 「아님」 피드백 재검토 · 일치 카드에 충족 조건을 그대로 노출(근거 없는 권고 금지).
- (v1.2) **KB 공동화**: 쓰는 사람이 없으면 비어 있다(SRE Workbook — 런북은 운영 변화 속도로 낡는다) — 초안 생성을 한 번 클릭으로 낮추고, 커버리지·검토 대상 목록을 관리자 화면 기본 노출. 운영 책임자 지정은 G-17.
- (v2.0) **명령·출력 노출**: HolmesGPT bash 명령 한 줄·도구 인자·발췌에 호스트·IP·SQL 조각·프로세스 명령행이 실리고, G-36 확정으로 **사용자에게도** 발췌가 보인다. 방어는 가림 두 겹(sre_agent `mask_secrets` → 본체 D-325 ③)뿐이라 **가림 규칙이 놓친 꼴은 그대로 노출된다** — W13 표본 테스트로 꼴을 고정하고, 운영 투입 전 내부망 실 출력 표본으로 한 번 더 확인한다[내부망 확인 필요]. 놓친 꼴이 나오면 `log_masking.py`에 더하는 것으로 대응한다(제외 목록 부활은 사용자 재결정 사항).
- (v2.0) **AI 메모 오독**: 관리자에게 보이는 HolmesGPT 중간 서술이 결론처럼 읽힐 수 있다 — 배지·배경색·필터 끄기 · 브리핑·통보에는 쓰지 않는다(D-035).
- (v2.0) **갱신 부하**: 미종결 조사 수 × (60/5) MCP poll/분(보는 사람·탭 수와 무관) — poll마다 MCP SSE 연결을 새로 연다. W14에서 측정하고 기준 초과 시 간격 상향 또는 연결 재사용(별 결정). 브라우저 쪽은 연결 1개.
- (v2.0) **메모리**: 잡당 실행 기록 256KB × 보관 잡 500(`_DEFAULT_MAX_JOBS`) = 최대 약 128MB(sre_agent) + 본체 허브 같은 차수 — 종결 잡은 발췌·AI 메모를 버리고 요약만 남겨 상한을 낮춘다 [W13·W14에서 실측 후 확정]. 하위 탭 무제한은 브라우저 메모리 문제로 돌아온다 — 탭당 화면 행 300건 상한.
- (v1.2) 사내 매뉴얼에 민감 정보(계정·IP) — RAG 색인 전 `pii_filter`·마스킹 경로 통과(plans/126 기존 규칙).

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
| G-34 (v2.0 · **확정**) | 알람·조사를 눌렀을 때 여는 곳 | **항상 「장애 진단」 탭의 그 조사 하위 탭(진행 중·종결 모두) — 카드 [진행 보기] · 분석 접수 직후 · 채팅 조사 카드 · 노이즈 드로어(`/?diagnosis=` 새 브라우저 탭) · workb 링크. 「조사 대화」는 종결 화면 버튼**(권고안 · D-341 ⑪ 진입 부분 개정) · ~~진행 중일 때만 진단 탭~~ |
| G-35 (v2.0 · **확정**) | 「새로운 탭」의 뜻 | **장애 진단 화면 안의 하위 탭**(권고안) · ~~브라우저 새 탭~~ |
| G-36 (v2.0 · **확정**) | 실행 기록 노출 범위 | **한 줄·상태·소요·행 수·응답 발췌 모두 사용자·관리자에게 — 가림 처리 후 모두 보임(발췌 제외 목록 없음 · 사용자 경로는 D-325 ③ 마스킹 한 번 더)**(권고와 다름 — 권고는 발췌 운영자만) · ~~발췌 운영자만~~ · ~~발췌 없음~~ |
| G-37 (v2.0 · **확정**) | 실행 기록 보관 | **종결 시 요약(발췌·AI 메모 제외)을 본체 JSONL에 1행**(권고안) · ~~보관 안 함~~ |
| G-38 (v2.0 · **확정**) | LLM 중간 서술(HolmesGPT `AI_MESSAGE` · 소견 초안) | **관리자에게만 「AI 메모 — 검증 전」 배지로 표시 · 사용자 경로에는 없음 · 브리핑·통보에 쓰지 않음**(권고와 다름 — 권고는 표시 안 함) · ~~표시 안 함~~ |
| G-39 (v2.0 · **확정**) | 갱신 방식 | **본체→브라우저 SSE 푸시 — 브라우저당 연결 1개 다중화 · 본체 허브가 조사당 sre_agent poll 1회/5초 · 300초·안전 상한 없이 종결로 멈춤 · 끊기면 15초 조회로 강등 표시**(권고와 다름 — 권고는 커서 poll · D-325 ② 부분 개정) · ~~커서 poll 5s/30s~~ |
| G-40 (v2.0 · **확정**) | 하위 탭 상한·정리 | **상한 없음 · 자동 닫기 없음 · 「▾ 열린 탭 n」 메뉴 · 종결 탭 모두 닫기 · `sessionStorage` 복원**(권고와 다름 — 권고는 8개) · ~~8개~~ |
| G-41 (v2.0 · **확정** · 인터뷰 추가) | 「진행 중 목록」 범위 | **관리자 = 모든 조사(자동·요청·채팅 · 존 무관) · 사용자 = 내가 채팅으로 시작한 조사**(사용자 지정 — 「관리자는 모든요청을 보여주고 사용자는 내 채팅 조사를 보여줘라」). 사용자가 알람에서 직접 연 조사는 목록에 없어도 하위 탭으로 열린다(존 검사) · ~~내 존 알람 조사 + 내 채팅 조사(권고)~~ · ~~내 존 모든 조사~~ · ~~내가 요청한 것만~~ |
| G-42 (v2.0 · **확정** · 인터뷰 추가) | SSE 상세 기록 푸시 범위 | **열린 하위 탭의 조사만 — 탭을 열면 누적분을 한 번에 받고 이후 새 이벤트만 · 진행률·상태 요약은 목록 전체에 항상**(권고안) · ~~목록의 모든 조사~~ |
| G-43 (v2.1 · **확정** · 사용자 지시 2026-10-21) | 조사 대화를 여는 곳 · 노이즈 관제 버튼 | **본 화면 「조사 대화」 뷰 탭(질의응답과 같은 줄 · 대화 화면 공유 · 바꿔 끼우기) · 노이즈 관제 ⓒ 머리에 조사 id가 있으면 늘 버튼 → 열린 포털 창의 그 탭(못 찾으면 새 탭 `/?conversation=`)** · ~~질의응답 탭에서 새 대화~~ · ~~관제는 `/?investigation=` 새 탭 · 진단 켜지면 숨김~~ |
| G-44 (v2.2 · **확정** · 사용자 지시 2026-10-21) | 장애 진단 화면에 보일 조사 맥락 | **머리에 알람 요약(알람명·서버·자원·발생·판정·심각도 · 조건 로그 운영자만)·채팅 대상·조사 이유(플레이북 선택 근거/HolmesGPT 이유) · 실행 기록 스텝마다 목적(`narrate.focus`)** · 알람 요약은 판정 기록에서(보존 범위 밖이면 「찾지 못함」) |
| G-45 (v2.3 · **확정** · 사용자 지시 2026-10-21) | 조사 탭에서 이벤트를 찾아 바로 조사 | **장애 진단 「진행 중 목록」 위 검색 칸(시간·알람명·서버) → 판정 기록 `/noise/decisions`(G-29와 같은 경로 · 칸별 부분일치 가산) → [조사 시작] = §5.6.2 ① 접수(권한·상한·합류 동일)** · 새 플래그 없음(실시간 화면 + 다시 분석 둘 다 켜졌을 때만) |
| G-17 (v1.2) | KB 운영 책임 | 유형별 `owner` 팀 지정 · KE 승인권자(문제 관리 담당) · 검토 주기 기본값(런북 180일 · KE 90일 [제안]) |

## 10. 다음 단계

(v2.0 · 2026-10-19 · **W13~W15 구현 완료**) 다음: ① 사용자 경로 2차 마스킹 강화 여부 사용자 결정(토큰 단위 대조 — D-325 ③ 범위 확대) ② 두 플래그를 켠 실프로세스 종단 1회(가짜 LLM · 플레이북·HolmesGPT 동시) + 매뉴얼 U-70·A-81 캡처 ③ 내부망 실 도구 출력 표본으로 가림 확인 후 투입 ④ W7 shadow. 이하 이전 기록: (v2.0 · 2026-10-19) **G-34~G-42 확정 · D-341 부기 4 · D-325 부기 등재 완료 → W13(sre_agent 실행 기록·가림 보강) → W14(본체 허브·SSE) → W15(장애 진단 탭·매뉴얼) 순서로 team-lead 위임.** W13은 다른 Wave와 독립이고, W15는 W14 뒤에 둔다. W7(shadow)은 W13~W15와 병행할 수 있으며 실행 기록이 shadow 관찰에도 쓰인다. 아래는 v1.9까지의 다음 단계다.

G-1~G-33 전건 확정 · D-341 등재·부기 3건 완료(2026-10-17) → W0 착수(team-lead 위임 · 계획 시작 SHA 기록). W10은 `plans/155` 공용부(조사 저장소 `kind=diagnosis` · 접수·조회 라우트 · 지연 poll 공용 모듈)가 먼저 있어야 한다 — 155가 먼저 끝나지 않으면 W10에서 그 공용부를 155 설계 그대로 구현하고 155에 완료를 표기한다. G-19 2단계(결정적 문맥 시드)와 G-20(브리핑 질문)은 별 계획. W11은 W4(엔진 배선) 뒤, W12는 W10(비동기 `sre_diagnose`·조사 카드) 뒤에 둔다.

### 10.1 잔여 작업 일람 (2026-10-22 갱신 · 처음 2026-10-21)

머리 각 판(v1.x~v2.3)과 §10에 흩어져 있던 잔여를 한곳에 모았다. 끝나면 「처리」 칸에 적고 머리 상태·`plans/INDEX.md` 상태 칸을 함께 고친다. 2026-10-22 사용자 지시 *"잔여 작업을 진행하라."* + 결정 3건(아래 3·6·14)으로 처리했다 — D-341 부기 9.

**남은 것**

| # | 잔여 | 근거(Wave · 수용 기준 · 게이트) | 막힌 이유 · 필요한 것 | 할 곳 |
|---:|---|---|---|---|
| 1 | **W7 내부망 shadow** — `parallel` 모드 · 브리핑만 저장 · 통보 무변경 · 운영자 선택 수집. (외부망 가짜 LLM 평가는 끝남 — 합성 세계 응답 모양이 실 도구와 달라 플레이북 판정 비교는 불가 · D-341 구현 부기 ③) | W7 · 수용 7 | 내부망 실 알람·FabriX | 내부망 |
| 2 | **수용 기준 6 측정** — LLM ≤ 8회/알람 · 수집+서술 ≤ 120s | 수용 6 | MLX·가짜 LLM 수치는 로직 확인용 | 내부망 |
| 3 | **R-D 필드 확보** — 게이트웨이 프로파일 응답의 커넥션 획득/SQL 실행 구조화 필드(있으면 판독 규칙이 바로 쓴다) | W0·W3 · 수용 2·2′·9(**2026-10-22 완화로 충족**) | 제니퍼 API에 값이 있는지 내부망 확인 · 로컬 제니퍼는 라이선스 없음 | 내부망 → `apm_gateway` |
| 5 | **SME 자료(G-10)로 카탈로그 보정** | W6 · G-10 · §4.1 [미확인] | 자료 미입수 | 사용자 · SME |
| 7 | **내부망 실 도구 출력 표본으로 가림 확인** — 놓친 꼴은 `log_masking.py`·`activity_masking.py`에 더한다 | W13 · §8 위험 「명령·출력 노출」 | 운영 투입 전 필수 · 표본은 내부망에만 | 내부망 |
| 9 | **매뉴얼 캡처** — U-63 · U-70(장애 진단 · 이벤트 찾아 조사)(A-81은 `no_ui`라 대상 아님) | D-255 | 캡처 하네스(`scripts/manual/run_capture`)가 로그인·캡처 DB에 PostgreSQL을 쓰는데 **Docker 데몬 미가동**. U-70은 진행 화면용 가짜 조사 서비스 프로필(`snapshot.py` PROFILES)도 새로 필요 | 로컬(Docker 기동 뒤) |
| 12 | **알려진 한계 확정** — `/active`·summary의 엔진 키는 이벤트를 본 조사에만 · 종결 잡 발췌 TTL까지 유지(최악 약 128MB · 전형 43MB) | W13·W14 · §8 위험 「메모리」 | 내부망 메모리 실측 뒤 유지 또는 조기 폐기 결정 | 내부망 → 결정 |
| 13 | **커밋** — v2.1~v2.3(W16~W18)과 2026-10-22 잔여 처리분 미커밋 | — | 사용자 요청 시 | 사용자 |
| 15 | **G-26 캐시 적중 실관측** — 실프로세스에서 HolmesGPT는 돌았으나 가짜 LLM이 비MCP 도구(`TodoWrite`)만 불러 적중 0(설계상 우회). 적중은 단위 테스트(16 스레드 · 실호출 1·적중 8)로만 확인 | G-26 · D-341 부기 9 ② | 실 데이터 도구·실 LLM 필요 | 내부망 shadow(감사 `cache_hits`) |

**처리함(2026-10-22)**

| # | 항목 | 처리 |
|---:|---|---|
| 3 | R-D | 사용자 결정 「기준 완화 + 내부망 이월」 — §8 수용 기준 2·2′·9 문구 완화(필드 없으면 `n/a`·판단 보류 표기로 충족). 필드 확보는 위 3번으로 남김 |
| 4 | G-26 HolmesGPT 도구 경로 캐시 공용 | `sre_agent/sre_agent/diagnosis.py`(`ask(tool_cache=)` · 요청별 얕은 사본의 `_directly_invoke_tool_call` 교체) · `investigation_dispatcher.py`(잡 사본 `tool_cache`) · `interface/mcp_service.py` · 테스트 `sre_agent/tests/test_plan187_g26_holmes_cache.py`(11) |
| 6 | 사용자 경로 2차 가림 | 사용자 결정 「줄 안 부분까지」 — `src/api/routes/alarm.py` `_investigation_mask_fn`(`mask_secrets` + 값 패턴 부분 일치) · 테스트 `tests/test_api/test_plan187_inline_mask.py`(11) · D-325 부기 |
| 8 | 실프로세스 종단 1회 | Playwright(headless Chromium) — 본체 8765(인증 on · 가짜 사용자 저장소) + sre_agent 9098(`parallel`·실행 기록 on) + 가짜 LLM 19180 + 폴스타 MCP 9099(DB 없음): 이벤트 찾아 조사 → 실 접수 → 실행 기록 16행·「목적:」·「● 실시간」 → 종결 브리핑(완료) · HolmesGPT done(판단 2·도구 1) · 10분 합류 확인 |
| 10 | 실인증 사용자 경로 | 공동존 사용자: 은행존 알람 [조사 시작] → 행에 「해당 존의 알람에 대한 권한이 없습니다」(403) · 은행존 사용자가 공동존 조사를 열면 「열 수 없는 조사」 · 조건 로그는 관리자 응답에만(API 대조) · 사용자 진행 중 목록에 직접 연 조사 없음(G-41대로) |
| 11 | W16 포털 창 재사용 | 포털 머리글 「노이즈 관제」(rel=opener)로 연 관제에서 「조사 대화로 열기」 → 창 수 2→2(새 탭 0) · 포털이 「조사 대화」 탭으로 전환 |
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
