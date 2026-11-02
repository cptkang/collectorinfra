# 198. 장애 조사·진단(RCA) 개선 — 현행 분석 · 상용 제품 조사 · 문헌 조사 · 개선 계획

> **작성일**: 2026-10-28 · **v1.1**(같은 날 — 사용자 결정 반영)
> **상태**: **WIP** — W0·W1·W2·W3·W5·W6·W7·W8(I-11) 구현(2026-10-28 · 신규 플래그 전부 기본 off = 비트 동일) · 계획 끝 모듈 단위 회귀 통과(재검토 교정 뒤 최종: 본체 4,146 통과 · 원래 실패 5 · sre_agent 2,699 통과 · 범위: 모듈 단위 — 전체 미실행) · verifier 2라운드 Critical 0 · Major 6건 교정 · **잔여**: W4(I-3 — 195 W4 선행) · I-4 ④ 화면 `ev_id` 링크(195 카드 선행) · I-12(라벨 누적 선행) · I-6(ITSM 선행) · 채택 처분 전부(154 W6 기준선 뒤 홀드아웃 A/B — F-HALLU·F-CITE·F-TMO·F-INC·F-LOOP·F-DIST·F-MISS·D07·유도 편향) · value_guard 산문 단위 환산(오탐률 측정 항목) · **사용자 확정(2026-10-28 · D-350 부기 2)**: Q4·Q5·Q6 권고 · W8 수동 버튼 경로 합류 미적용 · KEDB 초안 운영자 라벨 한정 · 제안값은 154 A/B로 확정 · W6 D07 A/B는 HolmesGPT 경로 · W3 재판정 시 conditional 심층 생략 허용 · 잔여 `apm_instance_map` 이웃 미배선 · **독립 재검토(2026-10-28 · Critical 0 · Major 3·Minor 13 → 교정)** 잔여: KEDB 초안 중복 방지가 KB 저장소 창 안만 봄(창 밖 key 조회 수단 없음) · 원장 대조 대형 출력(285KB×30행) 약 2초(판정 불변 유지 · 행 바이트 상한은 미도입) · 토폴로지 이웃 자원 유형 필터 없음(`polestar_topology` 출력에 resource_type 칸 없음 — 도구 호출 1건 낭비 수준, mcp_server 확장 시 처리) · 증상 선착 동시 발생 때 감사 `incident_joined` 행은 남음(카운터만 보정)
> **v1.1 부기(같은 날 · 인터뷰)**: Q1 「HolmesGPT 결과로 가설 판정을 바꾸지 않음」 · Q2 「조사를 연 운영자 선택 입력」 · Q7 「측정부터」 확정 · 154 W6 1회차 2주 안
> **v1.1 변경**: ① 변경 이력 — **현재 조회 가능한 데이터 없음 · 향후 ITSM 연동 시 확보**(사용자 2026-10-28) → I-6을 「ITSM 연동 뒤 착수」로 보류, Wave 순서에서 뺌 · ② 핵심 문헌 25편 원문을 **저장소 밖 `/Users/cptkang/AIOps/article/rca-investigation/`에 보관**(git 미저장 · 사용자 지시) · ③ 분석 중 발견한 문서 오류 2건을 I-0으로 편입(W0) · v1.0의 「D-035 참조 오기」 지적은 **철회**(D-035 본문 「통계·1차 분류는 Python 결정적, LLM은 해석만」이 근거 — §5 I-0 주) · ④ Q6 이관을 원 계획 153 §10.1·194 R8에 링크로 반영
> **분석 기준 HEAD**: `8bdb02a2` (계획 시작 SHA는 W0 착수 때 이 줄 아래에 적는다). 187(W13~W22)·188·190·194·196 구현분 대부분은 그 시점 미커밋이었고, 분석은 작업 트리 기준이다.
> **계획 시작 SHA**: `8bdb02a2` (W0 착수 2026-10-28 · 작업 트리 미커밋 변경 위에서 진행)
> **회귀 대상 파일**: config/settings_help/noise_gate_investigation.yaml · noise_gate/application/episode_coordinator.py · noise_gate/application/nodes/investigation_trigger.py · noise_gate/domain/cross_source.py · noise_gate/domain/incident_fingerprint.py · noise_gate/domain/investigation_accuracy.py · noise_gate/infrastructure/feedback_store.py · noise_gate/infrastructure/metrics.py · noise_gate/tests/test_plan198_w{0_accuracy,7_incident_memory,8_incident_unit}.py · scripts/manual/{features.yaml,content/admin.md,content/user.md} · src/api/routes/alarm.py · src/api/routes/investigation_label.py · src/api/server.py · src/api/settings_catalog.py · src/config/alarm.py · src/domain/kb_draft.py · src/static/{admin/dashboard.html,css/style.css,js/app.js,js/diagnosis-view.js,js/admin-investigation-accuracy.js,manual/admin.html,manual/user.html,manual/help_index.json} · tests/test_api/test_settings_catalog.py · tests/test_plan198_{w0_label_api,w7_memory_api,verify_ng}.py · sre_agent/.env.example · sre_agent/config/expected_sources.yaml · sre_agent/sre_agent/{settings.py,diagnosis.py,toolset_profiles.py} · sre_agent/sre_agent/application/{briefing_builder,evidence_prefetch,investigation_dispatcher,investigation_guidance,playbook_narrator,playbook_runner}.py · sre_agent/sre_agent/domain/{correlation,evidence_ledger,failure_guards,playbook_hypotheses,topology_neighbors,value_guard}.py · sre_agent/sre_agent/infrastructure/expected_sources_loader.py · sre_agent/sre_agent/interface/mcp_service.py · sre_agent/tests/test_plan198_{w1_evidence_ledger,w2_failure_guards,w3_discriminator,w5_sop_toolset,w6_topology,verify_sre}.py
> **구현 부기(2026-10-28 · W0~W8)**: 플래그 — sre_agent `CITATION_VALUE_CHECK_ENABLED`(W1) · `PARTIAL_BRIEFING_ON_INCOMPLETE`·`STALL_STOP_ENABLED`(+`STALL_REPEAT_THRESHOLD`=3·`STALL_IDLE_STEPS`=4 제안값)·`UNCHECKED_SOURCE_LIMITATION_ENABLED`(W2) · `SRE_PLAYBOOK_DISCRIMINATOR_ENABLED`·`_TOP_N`=2(W3) · `SOP_ANCHOR_ENABLED`·`KIND_TOOLSET_NARROWING_ENABLED`(W5) · `EVIDENCE_TOPOLOGY_NEIGHBORS_ENABLED`·`_MAX`=5(W6) / 본체 `NOISE_INVESTIGATION_LABEL_ENABLED`(W0) · `NOISE_INVESTIGATION_MEMORY_ENABLED`·`KEDB_MIN_COUNT`=3(W7) · `NOISE_INVESTIGATION_INCIDENT_UNIT_ENABLED`(W8). **이탈**: W2 정체 시 결론 지시 주입은 holmes 0.36 `call_stream`이 대화를 복사해 불가 → 협조적 중단 + 원장 부분 브리핑(정체·부분 브리핑 플래그는 함께 켤 것) · W5 툴셋 좁히기는 D-233 dict 장치 대신 ask 1회용 도구 등록부 사본(holmes private `_clone_base` 의존 — MCP 서버가 dict를 덮어씀) · SOP 앵커는 플레이북 모드 심층 잡에만 · W6 이웃 출처는 폴스타 토폴로지 1홉 + D-348 `previous_confirmed`만(`apm_instance_map` 미배선 · 이웃은 알람만 조회 · HolmesGPT 사전수집 경로 전용) · W7 유사 조사는 별도 라우트 `/similar`(조사 조회 응답 불변) · KEDB 초안은 플레이북 조사·운영자 라벨만 · W8 합류는 후속 모드(`investigation_followup_enabled`)에서만 효과.
> **사용자 제기(2026-10-28)**: *"장애 조사, 진단 RCA관련 기능에 대한 계획서와 구현 코드를 분석하고 관련 기능을 가지고 있는 제품이나 서비스의 기능을 조사하고 관련 문헌이나 논문을 자세히 찾아봐서 현재 구현된 기능을 개선하기 위한 방안을 계획파일로 정리하라."*
> **관련 결정**: D-003(읽기 전용 3중 방어) · D-138(조치 권고 · human-gated) · D-197(브리핑 계약·사건 좌표계·결정적 상관) · D-233(원격 프로파일 bash 제거) · D-324(조사 품질 벤치 계약) · D-238(벤치 처분 어휘) · D-341 ④(가설 순위는 결정적 · LLM은 바꾸지 않음)·⑮(심층 조사 운영 모드 · parallel은 독립 의견) · D-342·D-343(조사 불가) · D-347(수동 전용 · 부하 상한) · D-348(APM→폴스타 연결 학습) · D-162(신규 플래그 기본 off) · D-255(매뉴얼 동반)
> **관련 계획**: `plans/154`(조사 품질 벤치 · H-1~H-8) · `plans/187`(알람 유형별 플레이북) · `plans/101`(ML RCA · G5 GA-1~6) · `plans/91`(잔여 이관 장부) · `plans/153`(크로스소스 조사) · `plans/194`(수동 전용·부하) · `plans/195`(조사 내용·재질의) · `plans/196`(조사 화면 결함) · `plans/64`(B-3 자동 조치 거버넌스 — 착수 금지)
> **결정 번호**: **D-350**(W0 착수 시 §8 게이트 묶음 등재 · 2026-10-28)

---

## 0. 요약

**현행.** 조사는 세 경로(수동 버튼 · PAGE 자동 · 채팅 `fault_diagnosis`)로 sre_agent에 들어가고, 엔진은 두 개다. 결정적 플레이북(`SRE_PLAYBOOK_ENABLED` · 기본 off · 14종 YAML · 판독 R-A~R-H · 가설 3값)과 HolmesGPT ReAct 자유 조사(기본 경로)다. 결과는 브리핑 계약(D-197)으로 묶여 「장애 진단」 탭과 통보에 나간다(§1).

**진단.** 제품·문헌과 대조하면 공백은 여섯 가지다(§2).
① 운영에서 조사 정확도를 재는 고리가 없다 — 피드백은 기록만 되고 집계되지 않으며, 벤치 기준선도 0건이다.
② 가설을 반증 조회로 검증하는 루프가 없다 — 판단 보류 가설이 그대로 남는다.
③ 인용 검증이 문자열 마커 검사라 수치 환각을 막지 못한다(HolmesGPT 경로).
④ 변경 이벤트 상관은 기본 off이고 PostgreSQL만 된다. 무엇보다 **지금은 볼 수 있는 변경 데이터가 없다** — 향후 ITSM 연동 때 생긴다(사용자 2026-10-28).
⑤ 토폴로지·의존 관계를 원인 후보 확장에 쓰지 않는다 — 다른 호스트가 원인인 장애에 구조적으로 약하다.
⑥ 과거 사건 기억이 없다 — 알려진 오류(KEDB) 레코드 0건, 유사 사건 검색 0줄.

**권고.** 문서 오류 교정(I-0)과 열두 개 개선안(I-1~I-12 · §5)을 W0~W8(§6)로 나눈다. 변경 상관(I-6)은 ITSM 연동 뒤로 보류한다. 원칙은 세 가지다.
- **측정이 먼저다**(I-1 · W0). 상용 벤더의 정확도 주장(80~94%)과 독립 벤치(OpenRCA 11~21%)의 괴리가 크다. 우리 데이터로 재지 않은 개선은 채택하지 않는다.
- **결정적 층 위에 LLM을 얹는다.** Dynatrace·Instana·New Relic이 같은 구조이고, EviRCA·Flow-of-Action·PRAXIS가 이 분리로 정확도와 비용을 함께 개선했다고 보고한다. 우리 원칙(LLM 출력에 정합성 비의존 · D-341 ④)과도 맞는다.
- **154·101과 겹치는 것은 흡수하지 않고 연결한다.** 154 H-1~H-5는 이 계획의 I-4·I-9·I-10 전제다. 101 GA-2·GA-3(ML 국소화·유사 사건)은 G5 몫이고, 이 계획은 결정적 경량판만 둔다(§7).

**하지 않는 것**: 자동 조치(64 B-3 착수 금지 · D-003) · 그래프 학습 모델(GNN) · 운영 LLM 심판 채점(§9).

---

## 1. 현행 분석

### 1.1 흐름 (트리거 → 증거 → 추론 → 결과 → 화면·통보)

| 단계 | 구현 | 위치 | 기본값 |
|---|---|---|---|
| 트리거 · 수동 | 「이 알람 분석」 → `POST /alarm/{alarm_id}/investigations` · 사용자별 진행 1건·시간당 상한 | `src/api/routes/alarm.py` · `src/infrastructure/investigation_request_limit.py` | **수동 전용 on**(D-347) |
| 트리거 · 자동 | gate 뒤 PAGE 이상 → `sre_investigate_alarm` submit·poll · 크로스소스 사건당 종합 조사 | `noise_gate/application/nodes/investigation_trigger.py` · `episode_coordinator.py` | `investigation_trigger_enabled=False`(`src/config/alarm.py`) |
| 트리거 · 채팅 | 서버 1건 ∧ 증상→유형 1건이면 플레이북, 아니면 HolmesGPT | `src/nodes/fault_diagnosis.py` · `src/domain/symptom_router.py` | — |
| 접수 | FastMCP SSE 9098 · 도구 5종 · 잡 in-memory + 감사 JSONL · 동시 2·대기열 4·시간당 20·전체 600s | `sre_agent/interface/mcp_service.py` · `application/investigation_jobs.py` · `settings.py` | 상한 on |
| 엔진 A · 플레이북 | YAML 14종 선택 → 스텝 병렬 결정적 수집 → 판독 R-A~R-H → 가설 규칙표(지지/반박/보류) → KEDB 일치 → 단계 소견·종합 LLM(극성·환각 수치 가드) | `domain/playbook*.py` · `application/playbook_runner.py`·`playbook_narrator.py` · `config/playbooks/` | `SRE_PLAYBOOK_ENABLED=false` · `SRE_DEEP_DIVE_MODE=parallel` |
| 엔진 B · HolmesGPT | (사전수집 4축 + z-score 상관 — off) → **고정 질문 1종** → 지침 조립 → `call_stream` max_steps 40 → 후처리(미완주·APM 정체·실패 도구 인용 제외·조사 불가) | `application/evidence_prefetch.py` · `domain/correlation.py` · `mcp_service._job_to_question` · `application/investigation_guidance.py` · `diagnosis.py` · `investigation_dispatcher.py` | `evidence_correlation_enabled=False` |
| 결과 | 브리핑(severity·summary·timeline·cause·`root_cause_hypotheses`·recommendation·limitations·`citations_verified`) · 가설 순위는 상관 선행 지표 → 기타 이상 → **LLM 원인 맨 뒤** | `application/briefing_builder.py`(:98~) | — |
| 화면·통보 | 조사 저장소 JSONL(kind=alarm/episode/diagnosis/manual) · SSE 허브 · 「장애 진단」 탭(조사 흐름·실행 기록) · 후속 브리핑 통보 · KB 초안·포스트모템(LLM 0) | `noise_gate/infrastructure/investigation_store.py` · `src/api/investigation_live_hub.py` · `src/static/js/diagnosis-view.js` · `src/domain/kb_draft.py` | 수동 전용이면 PAGE에 「웹에서 조사 시작」 한 줄 |

조사 도구는 mcp_server의 `polestar_*` 9종(알람 이력·변경 이력·조건 로그·사건 알람·지표 추세·OS 구성·프로세스·자원 상태·토폴로지)과 `prom_*` 7종, apm_gateway의 `apm_*` 20여 종이다.

### 1.2 이미 있는 것 — 개선안이 다시 만들지 않을 것

- 결정적 가설 3값 판정과 순위(D-341 ④) · KEDB 일치 엔진(레코드만 0건) · 엔진 일치 표시(표시 전용) · 운영자 엔진 선택 기록(`/engine-choice`)
- 사건 좌표계(reference_time · lookback 120 · D-197) · z-score 선행성 상관 · 변경 오버레이(PG 전용 · off)
- 조치 권고(human-gated · 실행 경로 없음 · D-138)
- 피드백에 `investigation_id` 저장(91 1-4) · KE 피드백·엔진 선택 저장(`incident_kb_store.py`)
- 조사 품질 벤치 하네스(154 · 세계 24건 · 채점 RC@1/3·ECR·HALLU·DIST·ABST · 실패 유형 F-*)
- 조사 대화·추가 조사(`prior_findings`) · 잡 단위 도구 캐시

### 1.3 열려 있는 잔여 (이 계획의 전제·경계)

| 계획 | 잔여 | 이 계획과의 관계 |
|---|---|---|
| 154 | W6 내부망 1회차 A/A(사용자 실행 대기) · H-1~H-8 코드 0 | **W0의 기준선 출처.** H-4·H-5·H-1·H-3은 I-4·I-10·I-9가 이어받는다(§7) |
| 187 | W7 내부망 shadow · R-D·R-E 임계 보정 · SME 카탈로그 보정 등 13건 | 플레이북 품질 잔여 — 손대지 않는다. I-2는 187 가설 규칙표 위에 얹는다 |
| 101 | 코드 0 · G-1~G-18 게이트 대기 | GA-2(다차원 국소화)·GA-3(유사 사건)·GA-5(학습 순위)는 101 소관. I-7·I-8은 결정적 경량판만 |
| 91 | 1-14 vLLM tool-calling 완주 판정 · 1-15 DB2 런타임 · 1-16 statsmodels·e5 반입(기한 2027-02-20) | I-8 임베딩 단계는 1-16에 묶인다 |
| 153 | 증상 선착 사건이면 종합 조사가 나가지 않음 · 조사 예산 중복 발주 측정 | I-11이 이어받는다 |
| 194 | R8 같은 서버 묶음 조사(후속 판단) | I-11 |
| 195 | 조사 내용·질의응답 재질의(TODO) | I-3의 화면 변경과 같은 카드를 쓴다 — 195 뒤에 착수 |

---

## 2. 진단 — 공백과 근거

각 항목은 코드 실측 근거를 단다. 근거가 추정이면 「추정」으로 적는다.

| # | 공백 | 실측 근거 | 제품·문헌 대조 |
|---|---|---|---|
| **G1** | **운영 정확도 측정 고리 부재** | 피드백 `investigation_id`·`engine_choice`·`ke_feedback`은 기록만 하고 집계·평가 소비처가 없다(grep 소비처 `admin_incident_kb`·`kb_draft`뿐). `src/observability/investigation_metrics.py`는 78 조사 최적화 지표(압축·캐시·비용)지 RCA 정확도가 아니다. 154 기준선 수치 0건 | Datadog Bits AI는 실 인시던트 사람 라벨 + 보관 텔레메트리 재생 + 사람과 정렬한 LLM 심판으로 정기 평가한다(공식 블로그). incident.io는 조사 뒤 피드백 폼을 받는다. Where RCA Fails(NeurIPS'26 D&B)는 top@k가 「후보 누락」과 「순위 오류」를 구분 못 한다고 지적한다 |
| **G2** | **가설 검증 루프 부재** | 플레이북 가설은 1회 평가다. 판단 보류면 HolmesGPT 심층만 띄우고(`investigation_dispatcher.py` conditional 분기), **심층 결과는 가설 상태를 바꾸지 않는다**(`domain/engine_agreement.py` 「표시 전용」 · D-341 ⑮). W22 결손 스텝 재시도는 재수집이지 판별 조회가 아니다. HolmesGPT 경로의 가설은 사후 정렬일 뿐이다(`briefing_builder.py:98`) | Datadog: 가설마다 표적 쿼리 → 채택·기각 → 하위 가설 재귀, 기각 분기도 화면에 남긴다. JustDiag: 증거·가설·충돌·다음 점검을 명시 상태로 유지. plans/51 §3.5 감별 진단(Differential Diagnosis)은 문서에만 있다 |
| **G3** | **인용 검증이 문자열 검사** | `CITATION_MARKERS = ("←","<-","출처","인용","근거","[원문")`(`briefing_builder.py:44`) — 「근거」라는 단어만 써도 인용으로 친다. 값 대조는 플레이북 소견기에만 있다 | Why Do AI Agents Fail at Cloud RCA(2602.09937): 1,675회 실행에서 최다 실패가 「환각된 데이터 해석」·「불완전 탐색」. Google Gemini Cloud Assist는 관찰마다 원천 데이터 링크를 단다 |
| **G4** | **변경 상관 약함 — 데이터 자체가 없음** | **현재 조회 가능한 변경 이력 데이터가 없다 · 향후 ITSM 연동 시 확보(사용자 2026-10-28)**. 코드 쪽: `EVIDENCE_CHANGE_OVERLAY_ENABLED` 기본 off · DB2는 한계 문장만 · 플레이북 S7은 플레이북이 off면 안 돈다 · ITSM 어댑터는 슬롯만(187 §6). 따라서 브리핑 가설 순위 1단 「변경 직후」는 운영에서 입력이 비어 있다 | 조사 제품 다섯 곳 이상(incident.io·Rootly·Instana·New Relic Change Tracking·AWS DevOps Agent)이 변경을 1급 증거로 쓴다. Gandalf(NSDI'20) · Groot(ASE'21). **LLM 기반 변경 유발 장애 동료심사 문헌은 공백**(§4.4) |
| **G5** | **토폴로지 미사용** | `polestar_topology`는 `host.network` 플레이북 스텝 1곳에서만 쓴다. 의존 그래프는 noise_gate 상관·억제에만 쓴다. 상관은 단일 서버 시차 순서화이고 다중 서버 연쇄는 91 1-3에서 축소됐다. 벤치 D07(cross_host_cascade) 같은 장애에 구조적으로 약하다(**추정** — 기준선 미측정) | Dynatrace Smartscape·Instana·Resolve.ai·ServiceNow 영향 분석. PRAXIS(DSN 2026): 의존 그래프 순회로 ReAct 대비 정확도 최대 6.3배·토큰 5.3배 절감. GALA+(ASE'26): AC@1 +25pp. **반대 근거**: Does Graph Structure Earn Its Place(2609.27069) — RCAEval에서 그래프 효과 유의 없음 |
| **G6** | **사건 기억 부재** | sre_agent에 유사 사건·임베딩 코드 0줄 · `config/known_errors/`에 README만(레코드 0건) · KB 초안은 사람 승인 뒤 승격 · 조사 저장소는 과거 사건 색인으로 안 쓰인다 | incident.io·Rootly·PagerDuty·Cleric·Azure SRE Agent·Komodor가 과거 사건을 쓴다. eARCO(유사 사건 few-shot) · TSG Suggester · Nissist. Cleric은 고객 간 일반화가 어렵다고 밝힌다 → 범위를 같은 호스트군·알람 유형으로 좁힌다 |
| G7 | 기본 설정이 가장 빈약한 경로 | 플레이북·상관·변경·중요도·APM·크로스소스가 전부 기본 off → 기본은 「고정 질문 + 자유 ReAct」(154 §2.2) | HolmesGPT 커뮤니티(CNCF 블로그 2026-01): 「모델보다 런북이 중요」 · 툴셋 30개 이상을 켜면 노이즈로 정확도 저하. Flow-of-Action(WWW'25): SOP 명시화로 ReAct 35.5% → 64.0% |
| G8 | 미완주·타임아웃 정보 손실 | 미완주면 답변만 남고, 타임아웃이면 stub 브리핑에 도구 기록은 버려진다 · MLX 9B 실측 3세계 중 2건 F-TMO | Stalled, Biased, and Confused(FORGE 2026): 다중 홉에서 정체·편향 |
| G9 | 사건 단위 조사 부족 | 같은 서버의 여러 알람을 한 조사로 묶지 않는다(194 R8) · 증상 선착 크로스소스 사건 누락(153 §10.1) | Dynatrace Problem 단위 · PagerDuty 사람 호출 전 triage · COLA(ICSE-SEIP'24) 경보 집계 F1 0.90~0.93 |

---

## 3. 상용 제품·오픈소스 조사 (2025~2026 기능 기준)

> 근거 수준: WebSearch 요약과 공식 문서·블로그. **(V)** = 공식 문서·논문으로 확인, **(M)** = 벤더 자체 주장(독립 검증 없음). 2026-10-28 기준. 기존 조사(`docs/aiops_benchmark/aiops_research_dossier.md` · `incident_investigation_literature.md` · `ml_library_vendor_survey.md` · plans/187 §3)를 최신 기능으로 갱신하고 새 제품을 더했다.

### 3.1 제품별 요약

| 제품 | 트리거 | 증거·추론 | 결과 표현 | 학습·피드백 | 안전장치 | 수치 |
|---|---|---|---|---|---|---|
| Datadog Bits AI SRE | 알림 시 자동 | 가설 생성 → 표적 쿼리 검증 → 하위 가설 재귀·가지치기 · 2026-03 소스 코드 접근 | 가설 트리(**기각 분기 노출**) · 로그 발췌 · 타임라인 · 즉시·장기 조치 | 정기 벤치(사람 라벨 + 재생 + 정렬된 LLM 심판) **(V)** | 블로그 미기술 | 해결 시간 90~95% 단축 **(M)** |
| Dynatrace Davis / Intelligence | Problem 자동 탐지 | **결정적 인과 AI**(Smartscape 토폴로지 순회 + 이벤트 타임라인)가 근거층, 에이전트가 그 위 | 인과 체인 있는 Problem | 2026 노코드 커스텀 에이전트 | 가드레일(상세 미확인) | 하루 300만 건 분석 **(M)** |
| AWS DevOps Agent | 알림·티켓 자동 / 수동 · Dynatrace가 호출 | 앱 토폴로지 학습 · 관측·CI/CD·배포 이력 · 가설 탐색 | RCA + 완화 권고 | 커스텀 스킬 | 진단 보조(자동 수정 없음) | RCA 94% **(M)** |
| Azure SRE Agent | Response Plan 자동 | **업로드 런북이 조사 계획을 결정** · 과거 조사 맥락 | 조사 세션 · MCP 서버 | 과거 조사 사용 | **Review/Autonomous 모드 + 별도 RBAC, 둘 다 충족해야 실행 (V)** | 미공개 |
| Google Gemini Cloud Assist | 수동 | 로그·구성·메트릭·토폴로지 · SRE 플레이북 · 불확실하면 복수 가설 | **관찰마다 원천 링크**(팩트체크) · 후속 단계 · 리비전 재실행 | 미확인 | 미확인 | 미공개 |
| Splunk Observability AI agent | 알림 열람 시(옵트인) | APM·트랜잭션 오류·K8s 상관 · 복수 가설 | **가설별 근거량 기반 상대 신뢰도** + 별도 조치 계획 **(V)** | 미확인 | 커스텀 메트릭 미지원 **(V)** | 미공개 |
| New Relic SRE Agent | 알림 · 채팅 | 인과 그래프 + 안티패턴 지식 + Change Tracking · 생성형 + 결정적 혼합 | 미확인 | 미확인 | 미확인 | 미공개 |
| IBM Instana | 1클릭 수동 | 토폴로지·트레이스·로그 · 배포·설정 변경 · 인과 AI + LLM | RCA | 미확인 | 미확인 | MTTR 60~80% **(M)** |
| PagerDuty SRE Agent | 사람 호출 전 가상 대응자 | 관련 인시던트 · 외부 도구 · MCP | 진단·조치 제안 · **자기 갱신 런북** | 과거 인시던트 학습 | 조치 승인은 사람 | 50% 빠름 **(M)** |
| incident.io AI SRE | 선언 시 자동 | 유사 과거 사건 · 변경(배포·플래그·설정) · 런북 · 텔레메트리 | 조사 요약 | **조사 뒤 피드백 폼** | 미확인 | 미공개 |
| Rootly AI SRE | 인시던트 시 | 텔레메트리 · 최근 변경 · 유사 사건 | **순위 가설 + 신뢰도 + 근거** | 미확인 | 미확인 | 미공개 |
| Cleric | 알림 시 자동 | 병렬 확인 | 조사 요약 | **해결된 조사·팀 피드백이 운영 기억** | 기본 읽기 전용 | 실행 가능 소견 92% **(M)** |
| Resolve.ai | 알림 | 앱·인프라·변경을 **지식 그래프로 자동 구축 후 순회** | 미확인 | 그래프 지속 갱신 | 읽기 전용 · 메타데이터만 | 공개 벤치 없음 |
| Traversal | 알림 | 미공개 | 완화 단계 포함 RCA | 미확인 | 미확인 | 75~82% **(M · 페이지마다 불일치)** · OpenRCA가 실 RCA를 반영 못 한다고 비판 |
| Grafana Assistant / Sift | 프롬프트 / 자동(Sift) | Sift = 정해진 디텍터(결정적), Assistant = 다중 에이전트 LLM | 지속 리포트 | 미확인 | 사용자 권한 범위 데이터만 | 미공개 |
| Komodor Klaudia | 자동 이상 | 과거 조사 흐름 · 변경 이력 | RCA | 과거 조사 | 미확인 | RCA는 Pod만 **(V)** |
| HolmesGPT (CNCF Sandbox) | 알림 · 수동 | YAML 툴셋 · 마크다운 런북 · 에이전트 루프 | — | 런북 | 읽기 전용 권한 | 자체 eval 프레임워크 · 「모델보다 런북」 · 툴셋 30+ 노이즈 |
| Coroot(오픈소스) | 이상 탐지 시 | eBPF · 서비스 의존 그래프 · 이상 상관 + LLM 설명 | 수정 제안 | 미확인 | 미확인 | 자체 AI 벤치 공개 |
| ServiceNow · BigPanda Biggy · Elastic Agent Builder | — | CMDB 의존성 영향 분석 · 대화형 Q&A · 사용자 정의 도구 워크플로 | — | — | — | 미공개 |

**조사 공백**: Datadog Watchdog RCA 내부 방식 · k8sgpt · Keep · Causely 내부 방식 · 제니퍼소프트·와탭의 AI 조사 기능은 이번 검색에서 근거를 찾지 못했다. 제니퍼5·엑셈·와탭 MCP는 기존 `ml_library_vendor_survey.md`에 있다.

### 3.2 공통 패턴 — 우리와의 대조

| # | 공통 패턴 | 채택 제품 | 우리 현행 | 개선안 |
|---|---|---|---|---|
| P1 | 가설 생성 → 표적 조회로 검증 → 재귀 | Datadog · Splunk · Google · Rootly · AWS | 플레이북 1회 평가 · 보류는 방치 | I-2 |
| P2 | 기각 가설까지 보이는 가설 트리 · 근거량 신뢰도 | Datadog · Splunk · Rootly | 3값 판정은 있으나 화면은 순위 목록 · 신뢰도는 high/medium/low 고정 규칙 | I-3 |
| P3 | 결정적 인과·토폴로지 층 + LLM | Dynatrace · New Relic · Instana · Grafana Sift | 플레이북은 결정적(off) · 토폴로지 미사용 | I-7 · I-9 |
| P4 | 변경 이벤트 상관 | incident.io · Rootly · Instana · New Relic · AWS | 데이터 없음(ITSM 연동 전) | I-6(보류 — ITSM 연동 뒤) |
| P5 | 과거 사건 기억 · 피드백 | incident.io · Cleric · PagerDuty · Azure · Komodor | KEDB 0건 · 검색 없음 | I-8 · I-1 |
| P6 | 런북 주도 · 런북 자동 갱신 | Azure · PagerDuty · Google · HolmesGPT | 플레이북 14종(사람 작성) | I-9 · I-12 |
| P7 | 관찰별 원천 링크(팩트체크) | Google · Datadog | 도구명만 · 195 TODO | I-4(+195) |
| P8 | 조사(읽기)와 조치(쓰기) 분리 · 승인 모드 | Azure · Cleric · Resolve · PagerDuty | 읽기 전용 · 조치 권고만 | 현행 유지(§9) |
| P9 | 자동 트리거 + 수동 재조사 | 대부분 | 수동 전용 기본 · 다시 분석·추가 조사 | 현행 유지 |
| P10 | MCP로 외부 에이전트 연동 | Azure · PagerDuty · Grafana · Dynatrace↔AWS | sre_agent가 MCP 서버 | 현행 유지 |

**수치에 대해**: 벤더 정확도 80~94%는 자체 데이터·자체 정의다. OpenRCA(ICLR'25)는 당시 최고 모델 11.34%, OpenRCA 2.0(2606.27154)은 11개 모델 평균 20.7%다. 두 숫자는 비교 대상이 아니며, **우리 데이터로 직접 재야 한다**는 것이 유일하게 확실한 결론이다(I-1).

---

## 4. 문헌 조사

> 방법: arXiv API로 제목·저자·comment·초록 수치를 확인했다. 게재처는 arXiv comment에 적힌 경우만 「확인」으로 표기했다. **DOI는 조회하지 않았다**. 수치는 초록에 있는 것만 옮겼다. 핵심 25건은 작성자가 arXiv API로 재대조했다(2026-10-28 — 25건 실존·게재처 표기 일치). 이미 `docs/aiops_benchmark/ml_rca_literature.md`(101 · RCA 71건)·`incident_investigation_literature.md`(64)·plans/187 §3·plans/51 §3.8에 있는 문헌은 1줄 재확인만 했다. 인용수는 쓰지 않는다(OpenAlex 과소집계 — `docs/literature/README.md`).
>
> **원문 보관(사용자 지시 2026-10-28 · git 미저장)**: 아래 표의 arXiv 문헌 25편 PDF·메타데이터·조사 원보고는 저장소 밖 **`/Users/cptkang/AIOps/article/rca-investigation/`**에 있다(`pdf/<arXiv ID>.pdf` · `metadata.tsv` · `README.md`). 25편 전부 arXiv API로 실존과 comment를 확인했다. 이 폴더는 git 저장소가 아니며 `docs/literature/bibliography.csv`에도 올리지 않는다. Gandalf(NSDI'20)는 arXiv 원문이 없어 빠져 있다.

### 4.1 동료심사

| ID | 문헌 | 게재처 | 핵심 · 보고 수치(데이터셋) | 우리에게 |
|---|---|---|---|---|
| L-A1 | Ahmed 외, Recommending Root-Cause and Mitigation Steps for Cloud Incidents using LLMs | ICSE'23 · 2301.03797 | MS 사건 4만 건+ · 사건 텍스트 기반 원인·완화 생성 | 텍스트만으로는 「유사 사건 회상」 수준 → 라이브 증거 수집이 필수(현행 유지 근거) |
| L-A2 | RCAgent | CIKM'24 · 2310.16340 | 도구 증강 자율 에이전트 · 관찰 키 기반 컨텍스트 관리 | 도구 출력 압축·키 참조 → I-4 원장 키 |
| L-A3 | **Flow-of-Action**: SOP Enhanced LLM Multi-Agent RCA | WWW'25 Industry · 2502.08224 | SOP 명시화로 ReAct 환각 억제 · 35.50% → 64.01% | **결정적 플레이북 + 자유 조사 구조의 직접 근거** → I-9 |
| L-A4 | **PRAXIS**: Program Analysis with Observability for RCA | DSN 2026 · 2512.22113 | 서비스 의존 그래프 + 코드 PDG 위 LLM 구조적 순회 · ReAct 대비 정확도 최대 6.3배 · 토큰 5.3배 절감(실사건 30건) | 의존 관계로 탐색 범위를 좁힌다 → I-7 |
| L-A5 | **GALA+**: Graph-Augmented LLM Agents for RCA and Incident Response | ASE'26 · 2608.08968 | 그래프 유도 조사 · 조치 권고 · 인간 가이드 평가 · AC@1 +25pp 이상(벤치 2종) | I-7 · 조치 권고 평가 틀 |
| L-A6 | **Where RCA Fails**: A Retrieval-Reranking Decomposition | NeurIPS'26 D&B · 2609.36686 | top@k는 후보 누락과 순위 오류를 구분 못 함 · 복잡 결함에서 통계 기준선 79~100% 오순위 · 그래프 기법이 최고 통계 기준선을 못 이김 | **I-1 지표 분해**(후보 재현율 vs 순위) |
| L-A7 | **Stalled, Biased, and Confused**: Reasoning Failures in LLMs for Cloud RCA | FORGE 2026 · 2601.22208 | 다중 홉 전파에서 정체·편향 유형화 | I-10 가드 |
| L-A8 | CloudIR-Bench | APSEC 2026 · 2610.29185 | 사건 보고서 기반 RCA·완화 예측 공개 벤치 | 조치 권고 평가 참고 |
| L-A9 | COLA: Knowledge-aware Alert Aggregation | ICSE-SEIP'24 · 2403.06485 | 지식 + 통계 하이브리드 경보 집계 · F1 0.901~0.930 | I-11 사건 단위 묶음 |
| L-A10 | BARO | FSE'24 · 2405.09330 | 다변량 베이지안 온라인 변화점 + 순위 · 인과 그래프 불필요 | I-5 변화점 판독(재확인) |
| L-A11 | PSqueeze / FDA / RiskLoc | JSS 2305.03331 / POMACS 1911.01225 / 2205.10004(게재처 미확인) | 다차원 속성 조합 국소화 | 101 GA-2 소관(재확인) |
| L-A12 | Groot | ASE'21 · 2108.00344 | 이벤트(경보·변경) 그래프 RCA · 산업 | I-6 · I-7 |
| L-A13 | Gandalf | NSDI'20(기억 기반 · 미확인) | 배포 변경과 장애 상관 | I-6 |
| 재확인 | RCACopilot(EuroSys'24 · 2305.15778 · RCA 정확도 최대 0.766) · OpenRCA(ICLR'25 · 11.34%) · AIOpsLab(MLSys'25 · 2501.06706) · ITBench(ICML'25 · 2502.05352 · SRE 해결률 13.8%) · RCAEval(2412.17015) · CIRCA · MicroRank · TraceRCA · Eadro · RUN · Sage | 기존 문헌 정본에 있음 | — | — |

### 4.2 Preprint (심사 여부 미확인)

| ID | 문헌 | arXiv | 핵심 · 수치 | 우리에게 |
|---|---|---|---|---|
| L-B1 | **Why Do AI Agents Systematically Fail at Cloud RCA?** | 2602.09937 | OpenRCA 전체 × 5개 LLM 1,675회 · 실패 12종 · 최다 = 환각된 데이터 해석·불완전 탐색 · 프롬프트만으로 해결 불가 | **I-4·I-10 가드 체크리스트** |
| L-B2 | **EviRCA**: Decoupling Evidence Extraction from Reasoning | 2609.19825 | 결정적 증거 추출 뒤 LLM 추론 · 정답률 40.6~43.9%(OpenRCA 기준선 최대 15.2%) · 토큰 15~26배 절감 | **I-5** · 플레이북 구조와 동형 |
| L-B3 | **Cloud-OpsBench** | 2603.00468 | 754 케이스 · 57 결함 유형 · 최강 JRA 0.76 · **증거 폐쇄율(ECR) 0.38** | 154 ECR 지표 출처(재확인) · I-1 |
| L-B4 | **JustDiag!**: Diagnostic Justification Engine | 2606.19407 | 증거·가설·충돌·다음 점검을 명시 상태로 유지 · 66 실사건 · 최종답·과정 2층 채점 | **I-2·I-3 가설 상태 모델** |
| L-B5 | OpenRCA 2.0 | 2606.27154(work in progress) | 인과 전파 경로 단계 라벨 · 11개 LLM 평균 20.7% · 「서비스는 맞히나 경로가 불성립」이 주 실패 | 벤치 경로 채점 아이디어 |
| L-B6 | **StepFly**: Agentic TSG Automation | 2510.10074 | 실제 TSG 92건 · 성공률 약 94% · 병렬 실행 시간 32.9~70.4% 단축 | I-9 · I-12 런북 → 실행 그래프 |
| L-B7 | Does Graph Structure Earn Its Place in Microservice RCA? | 2609.27069 | RCAEval에서 그래프 효과 유의 없음(Avg@5 +0.003 · p=0.844) | **I-7 반대 근거** — 소거 실험 필수 |
| L-B8 | PACE-LM | 2309.05833 | LLM RCA 신뢰도 보정 · 낮은 신뢰는 사람에게 | I-3 |
| L-B9 | eARCO · Dependency Aware Incident Linking · TSG Suggester · FixItFlow · Nissist | 2504.11505 · 2403.18639 · 2609.35780 · 2607.13035 · 2402.17531 | 유사 사건 few-shot · 의존성 인식 사건 연결 · 런북 추천·생성 | I-8 · I-12 |
| L-B10 | RCLAgent · mABC · TrioXpert · DynaCausal · EvoCause · ARGUS · OpsCortex | 2508.20370 · 2404.12135 · 2506.10043 · 2510.22613 · 2607.27290 · 2608.23084 · 2606.20758 | 다중 에이전트·재귀 가설 · 인과 그래프 진화 · MCP 접지 K8s RCA · 도출과 설명 분리 | 참고 — 채택 근거로 쓰지 않음 |

### 4.3 산업 보고

- IBM, Causal AI-based Root Cause Identification: Research to Practice at Scale (2502.18240 · 게재처 미확인) — 인과 RCA 대규모 운영 경험.
- Causely (2605.18327) — 벤더 자체 평가, 이해상충.
- Elasticsearch 자율 SRE 에이전트 (2604.03933) — 단일 사례.
- Flow-of-Action·COLA·PRAXIS·RCACopilot·Ahmed는 산업 데이터 기반이다.

### 4.4 공백 — 보수적 설계의 근거

1. **서버·WAS·APM 중심(비마이크로서비스) RCA의 동료심사 연구·벤치가 거의 없다.** 대부분 TrainTicket·OnlineBoutique·MS 내부 데이터다 → 문헌 수치를 우리 기대치로 옮기지 않는다. 154 합성 세계가 유일한 측정 수단이다.
2. **변경 유발 장애의 LLM 기반 동료심사 연구 미발견** → I-6은 LLM이 아니라 결정적 시간창 규칙으로 한다.
3. **LLM-as-judge로 사건 요약·포스트모템을 평가한 연구 미발견** → 운영 채점에 LLM 심판을 쓰지 않는다(§9).
4. **폐쇄망·소형 사내 모델에서의 RCA 에이전트 성능 연구 부재** → FabriX·vLLM 성능은 154 W6에서 직접 잰다.
5. **「결정적 플레이북 + 자유 조사」 하이브리드의 통제 비교 실험 부재**(Flow-of-Action이 가장 가깝다) → 엔진 비교는 우리 벤치의 기여 지점이다.
6. 읽기 전용 안전 경계의 RCA 특화 문헌 미발견 · 완화 권고 정량 평가는 CloudIR-Bench 외 거의 없음.
7. 검색 한계: 런북 LLM·사건 요약 평가 질의가 불발한 것은 「이번 검색에서 미발견」이지 「문헌 없음」이 아니다. 다수 게재처·DOI가 미확인이다.

---

## 5. 개선안 (I-1~I-12)

모든 개선안은 공통 규칙을 따른다.
- 신규 플래그 **기본 off = 현행과 비트 동일**(D-162). 조사 결과 계약은 **키 가산만** 한다(D-197 브리핑 계약).
- **가설 순위는 결정적**이다(D-341 ④). LLM은 서술만 하고 순위·상태를 바꾸지 않는다.
- **채택은 154 벤치 홀드아웃 A/B로 처분한다**(D-238 어휘 · D-324). 외부망 MLX 결과는 로직 확인용이고 채택 근거가 아니다(D-174).
- 읽기 전용(D-003). 조치 실행 경로를 만들지 않는다.

### I-0 분석 중 발견한 문서 오류 교정 · W0(동작 변경 없음)

| # | 오류 | 실측 | 교정 |
|---|---|---|---|
| E-1 | `docs/26_sre_agent_guide.md`가 조사 1건 전체 타임아웃을 **300초**로 적는다(167 · 236 · 293 · 399행 4곳) | 코드 기본값은 **600초**다(`sre_agent/sre_agent/settings.py` `investigation_timeout_seconds: int = 600` · 시간 사슬 주석 「수집 예산 240 + 소견 60 + 종합 90 = 390 ≤ 600」 · plans/187 W22 상향) | 4곳을 600으로 고치고 시간 사슬 한 줄을 덧붙인다. 167행 「collectorinfra MCP 호출 60초 · 동기 불가」 서술은 숫자만 고친다 |
| E-2 | `plans/sre-agent/02-incident-investigation-holmesgpt.md` 머리의 `상태: 계획(미구현)` | `sre_agent/`에 조사 루프·브리핑·조치 권고가 구현돼 있다(§1.1). 예약 D-009·D-010·D-011은 collectorinfra 결정으로 흡수됐다(조치 권고 = D-138) | 상태 줄을 「구현됨 — 현행 정본은 `sre_agent/` 코드와 docs/26 · 이후 변경은 plans/154·187·198」로 고친다(시점 문서화 · 본문 불변) |

- **철회(v1.1)**: v1.0 보고에서 「`engine_agreement.py`·`playbook_hypotheses.py`의 D-035 인용은 오기」라고 했으나 **오기가 아니다**. D-035 본문의 결정문에 「통계·1차 분류는 Python 결정적, LLM은 해석만」이 들어 있고, 저장소 전역(sre_agent·noise_gate·src 수십 곳)이 이 원칙의 출처로 D-035를 인용한다. 색인 제목(「알람 이력 기반 패턴 분석」)만 보고 판단한 실수다. 교정 대상이 아니다.
- **verify**: `grep -n '300' docs/26_sre_agent_guide.md`에서 조사 전체 타임아웃 언급 0 · 매뉴얼 해당 없음(내부 문서).

### I-1 조사 정확도 고리 — 운영 라벨과 분해 지표 (G1) · **우선순위 1**

- **문제**: 조사가 맞았는지 운영에서 알 길이 없다. 벤치 기준선도 0건이다.
- **설계**
  1. 조사 화면에 운영자 확정 입력 1줄을 둔다: 「실제 원인」 = (1순위 가설 맞음 / k순위 가설이 맞음 / 목록에 없음 + 자유 서술 / 판단 불가). 기존 `feedback_store`의 `investigation_id`에 붙인다(새 저장소 없음).
  2. 결정적 집계기(LLM 0)가 조사 저장소 × 라벨에서 지표를 낸다. L-A6을 따라 **후보 재현율**(정답이 가설 목록에 있었나)과 **순위**(RC@1·RC@3)를 분리한다. 그 밖에 엔진별(플레이북/HolmesGPT/병렬) · 알람 kind별 · 미완주율 · 「조사 불가」율을 낸다.
  3. 관리자 화면에 주간 표 1장을 둔다(형식은 Q2).
  4. 라벨이 붙은 운영 사건은 154 세계 후보 목록으로 내보낸다. 실제 반출은 D-308 반출 규칙을 따른다.
- **근거**: Datadog 평가 방식(V) · incident.io 피드백 폼 · L-A6 · L-B3.
- **verify**: 집계기 단위 테스트(라벨 4종 × 엔진 3종 고정 픽스처 → 지표 값 단언) · 라벨 없는 조사는 분모에서 제외(「판정 불가 ≠ 오답」 — D-238 L5).
- **매뉴얼**: 사용자·관리자 화면이 바뀐다 → D-255 동반.

### I-2 판별 조회로 가설을 검증하는 1회 루프 (G2) · 우선순위 2

- **문제**: 판단 보류 가설이 그대로 결론에 남는다. 심층 결과는 표시 전용이다.
- **설계**
  1. 187 가설 규칙표(`domain/playbook_hypotheses.py`) 각 행에 선택 필드 `discriminators`를 둔다. 보류의 원인이 된 결손 조건마다, 그 조건을 참·거짓으로 가르는 **추가 스텝**(기존 `playbook_steps` 재사용 · 인자는 사건 좌표계)을 적는다.
  2. 1차 평가 뒤 상위 N개(기본 2) 보류 가설에 대해 판별 스텝을 **1회만** 결정적으로 실행하고 3값 판정을 다시 한다. 루프 상한은 1회다. 예산은 기존 잡 예산(전체 600s · 도구 상한)을 쓴다.
  3. 결과에 `hypotheses[].verification = {ran, steps, before, after}`를 가산한다. 상태 변화는 규칙이 정한다. LLM은 관여하지 않는다 → D-341 ④와 양립한다.
  4. **HolmesGPT 심층 결과를 가설 상태에 반영하는 안**은 D-341 ⑮(독립 의견 · 표시 전용) 개정이 필요하다 → Q1. 권고는 **반영하지 않음**이다. 심층 결과가 반증을 가리키면 「엔진 불일치」 배지만 강화한다.
- **근거**: Datadog 가설 검증 재귀 · JustDiag(L-B4) 「다음 점검」 상태 · plans/51 §3.5 감별 진단.
- **verify**: 판별 스텝이 보류를 지지/반박으로 바꾸는 픽스처 · 결손이 그대로면 보류 유지 · 상한 1회 · 플래그 off면 비트 동일. 154 세계 D03(유인)·D10(유인 강화)에서 F-DIST 감소를 A/B로 본다.

### I-3 가설 트리 표시와 근거량 신뢰도 (G2 · P2) · 우선순위 3

- **문제**: 화면이 「순위 목록」이라 왜 다른 원인이 기각됐는지 보이지 않는다. confidence는 고정 규칙(high/medium/low)이다.
- **설계**
  1. 가설을 **지지 / 보류 / 기각** 세 칸으로 나눠 보인다. 기각 가설에는 기각시킨 판독 1줄을 붙인다(이미 규칙 평가에 있는 값 — 새 계산 없음).
  2. 신뢰도는 결정적 점수로 낸다: 지지 조건 수 · 반박 조건 수 · 결손 수 · 독립 소스 수(폴스타/제니퍼/Prometheus). 표시는 3단계와 근거 개수(「근거 4 · 반증 0 · 결손 1」)다. LLM 자기 평가는 쓰지 않는다.
  3. 1순위가 보류이거나 근거 점수가 문턱 미만이면 결론 문장을 「가설 목록」 형태로 강등한다(L-B8 · 단정 금지).
  4. 195의 단계 카드와 같은 카드 영역을 쓴다 → 195 뒤에 착수한다.
- **근거**: Datadog 기각 분기 · Splunk 근거량 신뢰도(V) · Rootly 가설 점수 · PACE-LM.
- **verify**: 점수 함수 단위 테스트 · 강등 조건 테스트 · 화면 캡처 · 매뉴얼.

### I-4 증거 원장과 값 대조 인용 검증 (G3) · 우선순위 2 · **154 H-4 이어받음**

- **문제**: HolmesGPT 서술의 수치·호스트·시각이 도구 출력에 실제로 있는지 확인하지 않는다.
- **설계**
  1. 잡마다 **증거 원장**을 만든다. 행은 도구 호출 1건으로, `{ev_id, tool, args, 요약 수치·식별자 추출, 시각}`이다. 기존 `ToolCallRecord`·활동 기록을 재사용한다.
  2. 브리핑 서술의 수치·호스트명·시각 토큰을 결정적으로 추출해 원장과 대조한다. 플레이북 소견기의 환각 수치 가드를 공용 모듈로 올려 두 엔진이 같이 쓴다.
  3. 대조 실패 토큰이 있는 가설은 「[가설]」로 강등하고 `limitations`에 「원장에 없는 값: …」을 적는다. `citations_verified`는 마커 검사가 아니라 대조 결과로 정한다(플래그 on일 때).
  4. 화면은 서술 옆에 `ev_id` 링크를 붙여 원 조회(도구·인자)를 연다. 195 「질의응답에서 확인」 질의와 같은 원장을 쓴다.
- **근거**: L-B1(환각된 데이터 해석이 최다 실패) · Google 관찰별 원천 링크 · RCAgent 관찰 키.
- **verify**: 154 F-HALLU·F-CITE 유형 세계 A/B · 단위 테스트(정확 일치 · 단위 변환 1자리 반올림 허용 · 호스트 별칭은 `src/domain/host_names.py` 정규화) · 오탐률(정상 서술이 강등되는 비율)을 함께 보고한다.

### I-5 결정적 증거 요약을 LLM 입력의 기본으로 (G7) · **154 H-2·H-3과 동일 — 154에서 처분**

- 154 H-2(사전수집 kind별 축)·H-3(사전수집 요약 지침 주입)이 이 개선안이다. 이 계획은 근거만 보강한다: EviRCA(L-B2 · 토큰 15~26배 절감)·Flow-of-Action(L-A3)·BARO(L-A10 · 변화점 판독을 요약에 넣는다). **새 코드를 이 계획에서 쓰지 않는다**(§7).

### I-6 변경 이벤트 상관을 1급 증거로 (G4) · **보류 — ITSM 연동 뒤 착수**

- **보류 사유(사용자 2026-10-28)**: 현재 조사에서 볼 수 있는 변경 이력 데이터가 없다. 변경 이력은 **향후 ITSM 연동 때** 확보된다. 데이터 없이 규칙을 만들면 운영에서 입력이 빈 채로 남으므로 이 계획의 Wave 순서에서 뺀다(§6). ITSM 연동 계획이 서면 그 계획의 후속 Wave로 옮기거나 여기서 재개한다.
- **지금 하지 않는 것**: 폴스타 `polestar_change_history` DB2 분기 · 제니퍼 변경 도구 기본 편입 · ITAM 변경 이력 조사.
- **재개 시 설계(보존)**
  1. 변경 소스 = ITSM 변경 레코드(187 §6 ITSM 어댑터 슬롯에 연결). 계약 필드 최소: 변경 id · 대상 CI(호스트/서비스 — `src/domain/host_names.py` 정규화) · 시작·종료 시각 · 유형.
  2. 결정적 규칙: 사건 시작 전 창(lookback 내 · 가까울수록 우선)의 변경을 **후보 원인 1행**으로 세운다. 브리핑 가설 순위 1단 「변경 직후」의 입력을 채우는 일이다. 대상이 영향 범위(I-7 이웃) 밖이면 뺀다.
  3. 대조군: 같은 변경을 받았는데 정상인 호스트가 있으면 「변경 단독 원인 가능성 낮음」을 표기한다(FUNNEL 이중차분 발상 · 101 P-20 · 결정적 카운트만).
- **지금 할 수 있는 준비(코드 0)**: ITSM 연동 요구에 위 계약 필드를 적어 둔다. 154 세계 D05(change_induced)는 ITSM 형태 변경 레코드를 주는 세계로 유지한다.
- **근거**: P4(5개 이상 제품) · Gandalf · Groot · 공백 §4.4-2(그래서 LLM이 아닌 규칙).

### I-7 토폴로지 1홉 후보 확장 — 결정적 경량판 (G5) · 우선순위 4 · **소거 실험 조건부**

- **설계**
  1. 사건 시점의 이웃을 결정적으로 모은다: 폴스타 `polestar_topology` · 제니퍼 `apm_instance_map`(WAS↔호스트) · D-348 확정 연결(`polestar_link`)만. 추정 연결은 쓰지 않는다.
  2. 이웃 호스트(1홉 · 상한 기본 5)의 같은 창 알람·지표 이상을 1회 조회해 「이웃이 먼저 이상」이면 **외부 원인 후보**를 세운다. 순위 규칙은 선행성(이미 있는 `correlation.py` 선행 판정 재사용)이다.
  3. HolmesGPT 지침에는 이웃 목록과 선행 판정만 넣고, 이웃 밖 호스트 탐색은 지침으로 막는다(PRAXIS식 탐색 경계).
  4. **채택 조건**: L-B7·L-A6이 그래프 이득을 부정하므로, 154 D07(cross_host_cascade)과 이웃 없는 세계를 함께 돌려 이득과 비용(도구 호출 수)을 모두 본다. 이득이 A/A 잡음폭 안이면 폐기한다.
  5. 2홉 이상·학습형 그래프·Groot식 이벤트 그래프는 101 GA-1 소관이다.
- **근거**: Dynatrace·Instana·Resolve.ai · PRAXIS(L-A4) · GALA+(L-A5) · 반대 근거 L-B7.
- **verify**: 위 A/B · 이웃 상한·연결 출처 필터 단위 테스트.

### I-8 사건 기억 — 결정적 지문 유사 사건과 KEDB 부트스트랩 (G6) · 우선순위 4

- **설계**
  1. **지문**: 조사 저장소 레코드마다 결정적 지문을 만든다 = (알람 kind · 플레이북 id · 지배 판독 enum 묶음 · 지지 가설 id · 호스트군/서비스). 임베딩 없이 집합 유사도(Jaccard)로 상위 3건을 찾는다.
  2. 브리핑에 「비슷한 과거 조사」 3건(날짜·결론·I-1 라벨)을 가산한다. **라벨이 붙은 것만** 결론을 보이고, 없으면 「확정 전」으로 보인다(오염 자기강화 방지 — Known Mistakes 「LLM 자동 등록은 쓰기 지점에서 차단」).
  3. **KEDB 부트스트랩**: 같은 지문 + 같은 확정 원인이 N회(기본 3) 쌓이면 KE 초안을 `wip`로 만든다. 승격은 사람이 한다(현 KB 초안 흐름 그대로).
  4. HolmesGPT 지침에 유사 사건 결론 1줄을 넣는 안은 eARCO(L-B9)식 few-shot이다. 154 A/B로 처분하고, 오답 유도(편향 — L-A7)를 함께 잰다.
  5. 임베딩 검색(e5)은 91 1-16 반입과 101 GA-3에 맡긴다.
- **근거**: P5 · Cleric(범위 좁힘) · eARCO · Dependency Aware Incident Linking · TSG Suggester.
- **verify**: 지문 결정성(같은 입력 → 같은 지문) · 라벨 없는 결론 비노출 · 초안 생성 문턱 단위 테스트.

### I-9 자유 조사의 SOP 앵커와 툴셋 좁히기 (G7) · 우선순위 3 · **154 H-1과 결합**

- **설계**
  1. 154 H-1(kind별 질문)에 더해, 플레이북이 있는 kind는 HolmesGPT 질문에 그 플레이북의 **스텝 순서를 조사 계획 앵커**로 넣는다(parallel 모드의 「결과가 아닌 정적 지시」 규칙 — D-341 ⑮ 안에서). Flow-of-Action·StepFly의 방식이다.
  2. kind별 **허용 툴셋**을 YAML로 정한다(예: host.* = polestar·prom, was.* = apm + polestar 확정 연결 대상만). HolmesGPT 툴셋 프로파일(`toolset_profiles.py`)에서 해당 잡만 좁힌다. 현행 원격 프로파일 bash 제거(D-233)와 같은 장치다.
  3. 좁힌 툴셋 밖이 필요하다고 판단되면 LLM이 「추가 조사 필요: X」를 쓰게 한다. 실행은 하지 않는다(추가 조사 버튼으로 사람에게 넘긴다).
- **근거**: Azure 런북 주도 · HolmesGPT 「툴셋 30+ 노이즈」 · L-A3 · L-B6.
- **verify**: 154 F-MISS·F-LOOP·step 수 A/B · 툴셋 필터 단위 테스트.

### I-10 실패 모드 가드 — 정체·미조회 소스·부분 브리핑 (G8) · 우선순위 2 · **154 H-5 이어받음**

- **설계**
  1. **정체 탐지**: 같은 (도구·정규화 인자) 반복 k회(기본 3) 또는 새 원장 행 없는 연속 step m회면 그 잡에 「정체」를 기록하고 결론 단계로 넘기는 지시를 주입한다. APM 정체 가드(87 J3)를 공용화한다.
  2. **미조회 소스 표기**: kind별 기대 소스(I-9 YAML) 중 호출 0인 것을 `limitations`에 「확인하지 않은 소스: …」로 적는다(L-B1 불완전 탐색).
  3. **부분 브리핑**: 154 H-5 그대로다 — 미완주·타임아웃이면 원장 행으로 결정적 부분 브리핑을 만든다. 154 W0 실측대로 `call_stream` 소비 쪽에서 회수한다.
- **근거**: L-B1 · L-A7 · 154 MLX 실측(F-TMO 2/3).
- **verify**: 반복 호출 가짜 LLM 픽스처 · 미조회 소스 표기 · 타임아웃 세계에서 stub 대신 부분 브리핑.

### I-11 사건 단위 조사 — 같은 서버 알람 묶음 · 증상 선착 보완 (G9) · 우선순위 5

- **설계**: 같은 호스트(정규화)·같은 사건 창의 진행 중 조사가 있으면 새 알람은 **그 조사에 증거로 합류**시키고 별도 잡을 내지 않는다(194 R8). 153 증상 선착 누락은 「사건 확정 시점에 종합 조사가 없으면 1회 발주」 규칙으로 메운다. 묶음 키는 noise_gate 상관 결과를 그대로 쓴다(COLA식 — 새 상관 로직 없음).
- **verify**: 묶음·합류 단위 테스트 · 중복 발주 수 측정(153 잔여 항목).

### I-12 자유 조사 성공 경로 → 플레이북·런북 후보 제안 · 우선순위 6(후순위)

- **설계**: I-1 라벨이 「맞음」인 HolmesGPT 조사의 도구 호출 순서를 kind별로 모아, 3회 이상 같은 순서가 나오면 플레이북 스텝 후보 초안(YAML diff 형태 · `wip`)을 만든다. 적용은 사람이 한다. 자동 반영은 하지 않는다.
- **근거**: PagerDuty 자기 갱신 런북 · Azure · FixItFlow·TSG Suggester(L-B9).
- **전제**: I-1이 라벨을 충분히 쌓은 뒤다. 그 전에는 착수하지 않는다.

---

## 6. Wave 계획

| Wave | 내용 | 전제 | 산출 · verify |
|---|---|---|---|
| **W0** | I-0 문서 오류 2건 교정 · I-1 라벨 입력·결정적 집계기 · 154 W6 1회차 기준선 수용(사용자 실행 · 2주 안) | Q2 확정 · 154 W6(기준선 수용만) | grep 확인 · 집계 단위 테스트 · 기준선 표 1장 · 매뉴얼 |
| **W1** | I-4 증거 원장 · 값 대조 인용 검증(공용 가드) | W0 기준선 | 154 F-HALLU·F-CITE A/B · 오탐률 |
| **W2** | I-10 정체·미조회 소스·부분 브리핑 | 154 H-5 W0 실측 | 154 F-TMO·F-INC·F-LOOP A/B |
| **W3** | I-2 판별 조회 1회 루프(플레이북) | Q1 · 187 W7 shadow 결과 | D03·D10 F-DIST A/B |
| **W4** | I-3 가설 트리·근거량 신뢰도 화면 | 195 W4 · W3 | 화면 캡처 · 매뉴얼 · 강등 테스트 |
| **W5** | I-9 SOP 앵커·kind별 툴셋 (+154 H-1) | 154 H-1 처분 | F-MISS·step 수 A/B |
| **W6** | I-7 토폴로지 1홉 · 소거 실험 | Q4 · D-348 확정 연결 운영값 | D07 A/B · 잡음폭 밖일 때만 채택 |
| **W7** | I-8 지문 유사 사건 · KEDB 부트스트랩 | W0 라벨 누적 · Q5 | 결정성·비노출 테스트 · 유도 편향 A/B |
| **W8** | I-11 사건 단위 조사 · I-12 런북 후보 | Q6 · 라벨 누적 | 중복 발주 수 · 초안 생성 테스트 |
| *(보류)* | I-6 변경 상관 | **ITSM 연동**(변경 데이터 확보) | 재개 시 D05 A/B · 거짓 변경 가설 0 |

- **회귀**: D-303을 따른다. Wave 중간에는 자기 테스트와 `regress.py --no-tests`만 돌리고, 계획 끝에 1회 모듈 단위 회귀를 돈다. sre_agent는 자체 `.venv`에서 돈다.
- **실 LLM**: 외부망 MLX 대표 소수 1회(로직 확인용) · 채택 판정은 내부망 154 회차 · 과금 평면은 쓰지 않는다(D-127·D-240).

---

## 7. 다른 계획과의 경계 — 흡수하지 않고 연결

| 대상 | 경계 |
|---|---|
| 154 H-1~H-8 | 처분 권한은 154에 있다. 이 계획은 H-4(→I-4)·H-5(→I-10)를 구현 Wave로 가져오고, 구현 뒤 처분은 154 회차에서 한다. H-1·H-2·H-3·H-6·H-7·H-8은 154가 처분하고 이 계획은 근거만 보탠다(I-5) |
| 187 | 플레이북 YAML·판독·가설 규칙표의 소유자는 187이다. I-2는 규칙표에 선택 필드를 가산할 뿐이다. 187 잔여 13건은 건드리지 않는다 |
| 101 | ML(광역 스캔·다차원 국소화·임베딩 유사 사건·학습 순위·예측)은 101 G5 소관이다. I-7·I-8은 결정적 경량판이고, 101이 랜딩하면 그 결과를 보조 증거로 받는 슬롯만 남긴다 |
| 195 | 「조사 내용·질의응답에서 확인」 카드가 먼저 들어간다. I-3·I-4 화면은 그 카드에 가산한다 |
| 153·194 | 153 §10.1 「증상 선착 사건」과 194 R8 중 「같은 서버 알람 묶음 조사」를 I-11로 이관했다(원 계획에 링크 · Q6 (a) 권고 적용). 194 R8의 잡 사이 캐시(T4)와 153 「조사 예산」 측정은 원 계획에 남는다 |
| 64 B-3 | 자동 조치는 범위 밖이다(착수 금지 유지) |

---

## 8. 사용자 결정 게이트 — 정리 결과 (v1.1 · 2026-10-28)

> 사용자 지시: *"변경 이력은 향후 ITSM연동시 이력을 볼 수 있다. 현재는 볼 수 있는 데이터가 없다. 핵심 문헌은 별도의 폴더에 저장하고 git에는 저장하지 않는다. 발견한 오류는 수정계획에 같이 포함시키고 나머지는 권고에 맞게 정리하라."* — Q3·Q9는 **사용자 결정**이다. 이어진 인터뷰(같은 날)에서 Q1·Q2·Q7을 권고대로 **확정**했고, Q8에는 154 W6 1회차 시점(**2주 안**)을 받았다. Q4·Q5·Q6은 **권고 적용 · 사용자 확인 전**이다. → 2026-10-28 사용자 「권고대로 진행」으로 **확정**(D-350 부기 2). 구현 Wave 착수 시 이 표를 묶어 D-번호로 등재한다.

| Q | 질문 | 결과 | 반영 |
|---|---|---|---|
| **Q1** | HolmesGPT 심층 결과가 플레이북 가설 상태를 바꿔도 되나? (D-341 ⑮ 개정 여부) | **(a) 안 됨 — 사용자 확정(인터뷰)** · 불일치 배지만 강화 · 결정성 유지 · D-341 개정 없음 | I-2 ④ |
| **Q2** | 운영자 「실제 원인」 입력 위치 | **(a) 조사를 연 운영자가 조사 화면 하단 1줄에 선택 입력 — 사용자 확정(인터뷰)** · 주간 표에 입력률을 함께 보인다 | I-1 · W0 |
| **Q3** | 변경 이력의 출처 범위 | **사용자 결정 — 현재 데이터 없음 · ITSM 연동 시 확보** → I-6 보류(ITSM 연동 뒤) | I-6 · §6 *(보류)* |
| **Q4** | 토폴로지 이웃의 출처 | **(a) 폴스타 토폴로지 + 제니퍼 인스턴스 맵 + D-348 확정 연결만** *(권고 적용)* | I-7 · W6 |
| **Q5** | 유사 과거 조사 노출 범위 | **(a) 라벨 붙은 것만 사용자·관리자 모두** *(권고 적용)* | I-8 · W7 |
| **Q6** | 153 증상 선착 누락·194 R8 묶음 조사 이관 | **(a) 이관** — 원 계획에 이관처 링크를 달았다(153 §10.1 · 194 R8) *(권고 적용)* | I-11 · §7 |
| **Q7** | 우선순위 | **(a) §6 순서 — 사용자 확정(인터뷰): 측정부터**(측정 → 근거 검증 → 가드 → 가설 검증 → 화면 → 앵커 → 토폴로지 → 기억 → 사건 단위 · I-6은 보류로 빠짐) | §6 |
| **Q8** | 154 W6 1회차 전에 W1·W2 구현 시작 | **(a) 구현은 시작, 채택 처분은 기준선 뒤** · 사용자 답: 154 W6 1회차는 **2주 안** 실행 가능 → W0에서 기준선을 받고 W1부터 A/B 판정 | §6 |
| **Q9** | 문헌 보관 방식 | **사용자 결정 — 별도 폴더에 저장 · git에 저장하지 않음** → `/Users/cptkang/AIOps/article/rca-investigation/`(저장소 밖 · git 저장소 아님) · `bibliography.csv` 등재 안 함 | §4 머리 · 부록 A |

---

## 9. 하지 않는 것과 위험

### 9.1 비채택

- **자동 조치·폐루프**: D-003 · 64 B-3 착수 금지. Azure Review 모드 같은 승인형 실행도 범위 밖이다.
- **그래프 학습·GNN·신경 인과 발견**(RUN·DynaCausal·EvoCause 등): 라벨·데이터 요구가 높고, L-B7·L-A6이 이득을 부정한다. 101에서 판단한다.
- **운영 채점에 LLM 심판 사용**: 공백 §4.4-3. Datadog도 사람 판정과의 정렬을 먼저 했다. 154 외부망 검토(Claude Code)는 벤치에 한한다.
- **다중 에이전트 RCA**(mABC·RCLAgent·TrioXpert): 폐쇄망 단일 모델 평면에서 비용 대비 근거가 약하다(공백 §4.4-5).

### 9.2 위험

| 위험 | 대응 |
|---|---|
| 라벨이 쌓이지 않아 I-1·I-8·I-12가 무력화 | 입력 1줄·선택형 · 주간 표에 「라벨률」을 함께 보인다 · 154 세계로 보완 |
| 값 대조 가드의 오탐으로 정상 서술이 강등 | 오탐률을 A/B에서 같이 보고 · 허용 규칙(단위·반올림·별칭) 테스트 |
| 판별 조회·이웃 조회로 조회 대상 부하 증가 | 기존 잡 예산·mcp/apm 동시 상한(D-347) 안에서만 · 상한 1회·이웃 5 |
| 유사 사건이 틀린 결론을 증폭(편향) | 라벨 붙은 것만 결론 노출 · 지침 주입은 A/B로 편향 측정 뒤 |
| 비마이크로서비스 환경이라 문헌 수치가 재현되지 않음 | 수치를 기대치로 옮기지 않음 · 채택은 우리 벤치만 |
| 154 W6 지연으로 처분 정체 | Q8 (a) — 구현과 처분 분리 |

---

## 10. 테스트·회귀·매뉴얼

- 테스트는 Wave별로 sre_agent `tests/`(자체 `.venv`)와 noise_gate·본체 `tests/`에 둔다. 파일명은 `test_plan198_*.py`다.
- 모든 Wave에 「플래그 off = 비트 동일」 테스트 1건을 둔다.
- 화면이 바뀌는 W0·W4·W7은 매뉴얼(`scripts/manual/` → `src/static/manual/`)을 같은 Wave에서 갱신한다(D-255).
- 품질 게이트: `arch_check.py --ci` · `overfit_check.py --ci`(sre_agent는 대상 밖이지만 noise_gate·본체 변경분은 대상) · 바꾼 파일 ruff·mypy.

## 부록 A. 조사 원자료

조사 원자료(제품별 표 `products_report.md` · 문헌별 표 `literature_report.md`)와 문헌 원문 25편은 **`/Users/cptkang/AIOps/article/rca-investigation/`**에 있다(git 미저장 · 사용자 지시). 계획서에는 해석만 둔다. 출처 URL 요약은 다음과 같다.
- Datadog `datadoghq.com/blog/building-bits-ai-sre` · Azure `learn.microsoft.com/azure/sre-agent/` (run-modes · response-plan · mcp-server) · Google `docs.cloud.google.com/cloud-assist/investigations` · Splunk `help.splunk.com` AI troubleshooting agent · incident.io `docs.incident.io/investigations/overview` · Cleric `blog.langchain.com/customers-cleric` · Resolve.ai `resolve.ai/our-agentic-ai` · Traversal `traversal.com/blog/openrca-isnt-root-cause-analysis-why-that-matters` · HolmesGPT `cncf.io/blog/2026/01/07/holmesgpt-agentic-troubleshooting-built-for-the-cloud-native-era` · Dynatrace Perform 2026 보도(businesswire 2026-01-28) · AWS DevOps Agent 블로그 · IBM Instana Intelligent Incident Investigation · Komodor Klaudia 블로그 · Coroot `coroot.com/ai-benchmark`
- 문헌은 §4 표의 arXiv ID로 찾는다.
