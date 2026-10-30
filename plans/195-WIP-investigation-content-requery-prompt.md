# 195. 조사 화면에 「조사 내용」과 「질의응답에서 확인할 질의」 보여 주기 — 검토안

> **작성일**: 2026-10-27 · **v1.2**(같은 날 — `plans/196` 기준 정리 · §1.4) · v1.1 사용자 결정 반영 · v0.1 검토안
> **v1.2 변경(196 기준)**: 화면 문구 「다시 조회」를 「질의응답에서 확인」으로 바꿈(196 단계 카드 [이 서버로 다시 조회]와 충돌) · 폴스타 단계 질의의 대상 이름 출처를 196 확정 연결(`polestar_server` · `polestar_link`)로 고정 · 생략·결손·재사용 섹션 규칙 · 섹션 가산 키 `queries`는 196 `reason_detail`·`reused_from`과 같은 방식 · Wave 선후 = 196 W2b·W1(D6) 뒤 · D2 `was.sql` 행 추가(196 W3 뒤 확정)
> **상태**: **WIP** — W1~W5 구현(2026-10-27 · 커밋 없음) · 결정 전건 확정(§8.0 — Q1·Q5·Q7·Q8 사용자 확정 2026-10-27 권고안대로) · D-341 부기 24 · D-192 부기 등재 · 플래그 `NOISE_INVESTIGATION_REQUERY_ENABLED` 기본 off · 질의는 제니퍼 축만(폴스타 축 0건) · **잔여**: W0② 폴스타 「서버 한정 × 사건창」 골드(내부망 `--check-gold` 뒤 등재 + 카탈로그 폴스타 항목) · W0③ MLX 1회(이 체크아웃 `.env` 없음 — 두 평면 mlx 아님) · W5 캡처 2장(플래그 켠 장면 정의·촬영) · §3.5
> **v1.1 변경**: §8.0 결정 결과 신설 · §7 Wave 확정(W4 정식) · §6 D-192·D-341 ⑪ 부기 필요 명시(구현 Wave에서 등재) · §3.4 선택 → 확정
> **분석 기준 HEAD**: `1f61461f` (계획 시작 SHA는 W0 착수 때 이 줄 아래에 적는다)
> **계획 시작 SHA**: `1a2ad01a274b12bb7dd7eae7647d7e732ae7c9cf`(2026-10-27 · main clean · plans/196 병합 뒤 — 196 계약 실측 확인: `playbook_steps.py` 확정 연결이면 `polestar_server`를 제니퍼 host로 덮지 않음 · 섹션 `reason_detail`·`reused_from` 가산 키 · `scope.polestar_link` · 진단 카드 후보 블록)
> **회귀 대상 파일**: `src/domain/investigation_requery.py` · `src/api/routes/alarm.py` · `src/api/settings_catalog.py` · `src/config/alarm.py` · `config/settings_help/noise_gate_investigation.yaml` · `.env.example` · `src/static/{css/style.css,index.html,js/app.js,js/briefing-view.js,js/diagnosis-view.js}` · `src/static/manual/{user,admin}.html`·`help_index.json` · `scripts/manual/{features.yaml,content/user.md,content/admin.md}` · `noise_gate/infrastructure/investigation_store.py` · `sre_agent/sre_agent/application/{playbook_steps,playbook_runner,investigation_dispatcher}.py`(별 venv) · 테스트 `tests/test_plan195_{w1_investigation_requery,w3_requery_ui,verify_requery}.py` · `tests/test_api/test_plan195_w2_requery.py` · `noise_gate/tests/test_plan195_w4_briefing_queries.py` · `sre_agent/tests/test_plan195_w4_section_queries.py` · 단언 갱신 `tests/test_api/{test_settings_catalog,test_plan160_static_assets,test_ui_plan187_w15_diagnosis,test_plan186_auth_fail_closed}.py` · `tests/test_plan187_{fe2,w5b}_static.py`
> **계획 끝 회귀(2026-10-27)**: `regress.py --base 1a2ad01a --files <위 27파일>` exit 0 · 본체 통과 3,210 · 실패 6 전부 원래 실패 · 정적 게이트 통과 · `범위: 모듈 단위 — 전체 미실행` · `[전체 회귀 권고]`(src.config.alarm 87%) · verifier Critical·High 0 · Medium 2 교정 완료(가림 표지 2종 대조 · 「조회한 것」 `data-bv-step` 매칭)
> **사용자 제기(2026-10-27)**: *"조사 화면에는 조사내용과 이 조사를 다시 조회하기 위한 프롬프트를 사용자에게 보여줄 수 있는 방법을 검토해 봐라."*
> **관련 결정**: D-192(알람 → 질의 프롬프트 인계 — 검증된 형태만 · 확인 뒤 전송) · D-341 ⑪(조사 대화 고정 카드 · **추천 칩은 골드셋 동형만**)·⑫(문맥 승계 G-19)·㉜·부기 11(조사 흐름) · D-325 ③(사용자 뷰 마스킹) · D-347(수동 전용) · D-248(조사 대화 탭) · D-183(이력 재사용 — 확인 바 없음) · D-309·D-328(시간 해석 · 분 단위) · D-255(매뉴얼 동반) · D-162(기본 off)
> **관련 실수**: `docs/18` 2026-07-20 「골드셋 자체가 실 DB에서 실행 검증 없이 작성」 · 2026-10-19 「사용자 요청 경로마다 열람 인가를 따로 씀」
> **결정 번호**: 새 D-번호 없음. Q2 ② 확정으로 **D-341 ⑪ 부기 + D-192 부기가 필요**하다 — W1(템플릿 카탈로그 확정) 끝에 등재한다(§6).

---

## 0. 요약

- **「조사 내용」은 대부분 이미 화면에 있다.** 장애 진단 탭의 「조사 흐름」이 단계 카드(왜 → 조회 → 결과 → 해석)를, 「실행 기록」이 도구와 인자를 한 줄씩 보여 준다. 다만 이 두 화면은 **옵트인 플래그 2개가 켜져야만** 조회 인자를 받는다. 조사 레코드 자체(브리핑)에는 단계별로 **도구 이름만** 남는다(§1).
- **「다시 조회할 질의」는 한 번 설계했다가 빠졌다.** D-341 ⑪이 조사 대화에 「대상 이름을 박은 추천 칩」을 정했지만, plans/187 W0에서 예시 3종 모두 골드셋 동형을 찾지 못해 칩을 내지 않았다(`app.js:4795` 주석 · `plans/187:205-211`). 이번 요청의 핵심 결정은 **D-192 「검증된 형태만」 규칙을 어떻게 다룰지**다(§6 · §8 Q2).
- **권고**: 브리핑의 `playbook.scope`(대상·사건창)와 단계 `action`에서 **코드 템플릿으로** 질의를 만든다(LLM 0). 서버 순수 함수가 만들고 조회 API 응답에 싣는다. 장애 진단 「조사 흐름」 단계 카드와 조사 대화 고정 카드가 같은 산출을 쓴다. 동작은 D-192 방식(입력창에 채움 → `[이대로 조회]/[수정]`)을 따른다. 검증 출처는 골드셋에 **제니퍼 벤치(`testdata/apm_bench`)를 더하는 안**을 권고한다(§3.3).
- **용어(v1.2)**: 사용자 화면에서 이 기능은 「질의응답에서 확인」이라 부른다. 「다시 조회」는 `plans/196`의 조사 재실행(폴스타 단계만 다시 돌림) 버튼 이름이다(§1.4). 계획서 본문의 「다시 조회할 질의」는 이 기능의 내부 이름으로만 남긴다.

### 0.1 해석 (확인 질문 — §8 Q1)

| 말 | 이 계획의 해석 | 다르게 읽힐 여지 |
|---|---|---|
| 조사 화면 | 「장애 진단」 탭의 조사 하위 탭(U-70)과 「조사 대화」 탭의 고정 카드(U-63) | 알람 카드 「조사 브리핑」 요약, 노이즈 관제 서랍 |
| 조사 내용 | 조사가 한 일 — 플레이북 단계, 호출한 도구(폴스타·제니퍼·SQL·PromQL)와 인자, 얻은 근거, 결론 | 「포스트모템 내보내기」(U-68, 이미 있음) 같은 문서 한 벌 |
| 다시 조회하기 위한 프롬프트 | 사용자가 근거를 직접 다시 확인하도록 **질의응답에 넣을 한국어 질의**(사다리 2단 `intent_orchestration`이 답함) | (B) 같은 조사를 조사 서비스(sre_agent)로 **다시 돌리는** 요청 — 이것은 이미 「다시 분석」(U-64 · G-28)·「추가 조사 요청」(U-66)이 있다 |

---

## 1. 현황 실측 (HEAD `1f61461f`)

### 1.1 저장되는 것 vs 보이는 것 vs 빠진 것

출처: 조사 저장소 `noise_gate/infrastructure/investigation_store.py`(레코드 모양 머리 주석 · `investigation_view` `:340`) · 실행 기록 요약 `noise_gate/infrastructure/investigation_activity_store.py`(`SUMMARY_EVENT_KEYS` `:33`) · 실 레코드 픽스처 `build/manual_capture/app-capture-diag/logs/{alarm_investigations,investigation_activity}.jsonl` · sre_agent 섹션 조립 `sre_agent/sre_agent/application/investigation_dispatcher.py:1440-1462`.

| 항목 | 어디에 남나 | 지금 화면 | 빠진 것 |
|---|---|---|---|
| 대상·사건창 | 브리핑 `playbook.scope` = `host`·`polestar_server`·`polestar_source`·`alarm_name`·`targets[{instance}]`·`incident_window{start,end,minutes}` (항상) | 조사 대화 머리 줄(`renderPinTarget`)·장애 진단 머리(`conversationHeadLines`) — 대상 이름·플레이북·범위 | 사건창 시각을 문장으로 보여 주는 곳이 없다 |
| 단계 목록·판정 | 브리핑 `playbook.sections[]` = `step`·`action`·`title`·`status`·`verdict`·`table`·`finding`·`opinion`·**`tools`(도구 이름만)** (항상) | 조사 대화 고정 카드(상세형 · 「사용 도구: 이름들」 `briefing-view.js:690`) · 장애 진단 조사 흐름 카드 | — |
| **도구 호출 인자** | sre_agent 실행 중 `StepContext.calls`(`playbook_steps.py:442`)에만 있고 **브리핑에 저장하지 않는다**. 실행 기록 이벤트 `label` = `도구 {인자 JSON}`(`tool_label` `playbook_steps.py:524`) · `phrase` = 조회 문장(`tool_phrase` · `step_findings.py:620` 사전) | 장애 진단 「실행 기록」(label 원문)·「조사 흐름」 조회 줄(phrase) | **두 옵트인이 모두 켜져야** 있다 — 본체 `NOISE_INVESTIGATION_LIVE_VIEW_ENABLED`(기본 off · `src/config/alarm.py:467`)와 sre_agent `SRE_ACTIVITY_LOG_ENABLED`(기본 off · `sre_agent/settings.py:338`). 둘 중 하나라도 꺼지면 조회 인자는 어디에도 없다. 조사 대화 고정 카드는 플래그와 무관하게 인자를 보여 주지 않는다 |
| 조회 결과 발췌 | 실행 중 버퍼에만(잡 TTL까지) — 보관 요약은 발췌 제외(G-37) | 장애 진단 실행 기록 ▸ 펼침(진행 중·TTL 안) | 보관 뒤엔 없음(의도 — G-37) |
| 결론 | 브리핑 `summary`·`synthesis`(가설·대응)·`briefing_lines` | 고정 카드·조사 흐름 「지금까지」 | — |
| HolmesGPT 엔진 조회 | 실행 기록 `tool_end` `label` = 도구 설명 문자열(`diagnosis.py:347`) — **구조화된 인자 없음**. 감사 로그 `tool_calls`는 이름 위주 | 실행 기록 한 줄 | 결정적 질의를 만들 재료가 없다 |
| 다시 조회할 질의 | — | **없음** (알람 카드에는 D-192 칩이 있으나 축이 `resource_type` 단위이고 조사 단계와 무관) | 전부 |

실측 메모: 로컬 `.env`에는 위 두 플래그가 없다(= off). 폐쇄망 값은 폐쇄망 `.env`가 정본이라 확인이 필요하다(§8 Q6의 판단 근거).

### 1.2 이미 설계됐다가 빠진 것 — D-341 ⑪ 추천 칩

- 설계(`plans/187` §5.6.1 4항): 단계별로 대상 이름을 박은 결정적 문구, D-192 규칙(골드셋 동형만 · 확인 후 전송). 예: S4 「{host} 서버 최근 7일 CPU 사용률 추이」 · S1 「{instance} 응답시간 지난주 같은 시간대와 비교」 · S6 「{host} 프로세스 CPU 상위 10」.
- W0 대조(`plans/187:205-211`): 세 문구 모두 `text2sql_gold`·`routing_gold`에 동형이 없었다(가까운 것: gp-009·yd-004·b0-004 「지난달 1개월 평균·최대」 · r-080·r-081 미채점 · apm_bench `APM-COMPARE-*`는 별도 세트라 불인정). → **칩 0개로 출시**(`app.js:4795`).
- 같은 절 659행: 조사 열람 존과 질의 DB 범위가 다른 사용자는 칩이 실패할 수 있으니 범위 밖이면 비활성 + 사유 — 이번 계획이 그대로 받는다(§4).

### 1.3 질의응답 쪽 인계 장치 (재사용 대상)

- `stageAlarmPrompt`(`app.js:6235`): `setActiveView("chat")` → 입력창 채움 → 확인 바 `[이대로 조회]/[수정]` → `handleSend()` 단일 진입점. 진행 중이면 조회 버튼 비활성(중복 과금 방지).
- `reuseHistoryQuery`(`app.js:4463`): 확인 바 없이 채우기만(D-183). 두 함수는 계약을 일부러 갈라 두었다(D-192).
- **차이점**: 조사 대화 탭은 질의응답과 대화 화면을 함께 쓰고 탭을 옮길 때 대화를 바꿔 끼운다(`setActiveView` `app.js:4307`). `stageAlarmPrompt`를 그대로 부르면 **조사 대화에서 질의응답 탭으로 넘어간다**. 조사 대화 안에서 조회하려면(그래야 `disclosures.ref.investigation_id`로 이력에 조사와 묶인다) 탭을 유지하는 변형이 필요하다(§5).

### 1.4 `plans/196`과의 관계 (v1.2 — 196이 기준)

196(장애 진단 조사 화면 결함 · WIP)이 같은 화면과 같은 브리핑 모양을 먼저 바꾼다. 이 계획은 196 계약(`plans/196` §5)을 그대로 따르고 고치지 않는다.

| 196 변경 | 이 계획에 주는 영향 | 이 계획의 규칙 |
|---|---|---|
| 단계 카드 [이 서버로 다시 조회]·[해당 서버 없음]·후보 목록(196 §1.1 ② · W2b · `diagnosis-view.js` 「폴스타 단계만 다시 조회합니다」) | 같은 카드에 「다시 조회」 두 개가 생긴다 — 하나는 조사 재실행, 하나는 질의응답 질의 | 이 기능의 화면 문구는 **「질의응답에서 확인」**. 카드 안 위치는 196 후보 블록 **아래**, 단계 결과 줄 다음. 후보 블록이 열린(선택 대기) 단계에는 질의 줄을 그리지 않는다 |
| 브리핑 범위 가산 키 `polestar_link`(`linked`·`absent`·`unresolved`) · 확정 연결이면 `polestar_server` = 폴스타 등록명(제니퍼 hostname으로 덮지 않음 · `playbook_steps.py` `resolve_was_targets`) | 폴스타 단계 질의의 대상 이름이 `host`(제니퍼 이름)와 다를 수 있다 | 폴스타 축 질의(`host.*`·`xsource.related_alarms`·`change.recent`의 폴스타 도구)는 **`scope.polestar_server`만** 쓴다. 비었거나 `polestar_link`가 `absent`·`unresolved`면 만들지 않는다. 제니퍼 축 질의는 `host`·`targets[].instance`를 쓴다 |
| 폴스타 축 단계 상태 `skipped`(선택 대기·폴스타 미등록 — 결손 아님) · `missing` | 조회하지 않은 단계다 | 섹션 `status`가 `skipped`·`missing`이면 질의를 만들지 않는다(조사도 못 본 것을 질의응답이 대신 보여 주는 듯 보이지 않게) |
| 재조회 자식 조사(`meta.hints.rerun` · 재사용 섹션 `reused_from`) | 자식 조사에는 폴스타 단계가 새 연결로, 나머지는 부모 결과로 있다 | 자식 조사 브리핑 하나로 만든다 — `reused_from` 섹션도 같은 템플릿(사건창은 자식 `scope.incident_window`). 질의는 부모·자식 탭마다 따로 보인다 |
| 섹션 가산 키 `reason_detail`(운영자 상세 · 사용자 뷰에서 뺌 · 196 §5.3) | — | 질의는 `reason`·`reason_detail`을 쓰지 않는다(가린 사용자 뷰의 `scope`·`action`·`status`만) |
| 상태 한국어 라벨 맵(196 D6 · `step_status_label`) | 화면 표지가 겹칠 수 있다 | 「지금 값」 표지(Q5)는 196 라벨과 별도 칸에 둔다 — 상태 라벨을 덮지 않는다 |
| D2 APM SQL 계열 플레이북(196 W3 · Q2 권고 `was.sql` · **미구현**) | 새 action이 생길 수 있다 | §3.2에 자리만 둔다. 196 W3의 action·도구 이름이 확정된 뒤 W0 대조표에 넣는다 |
| 본체 API `GET …/host-candidates`·`POST …/host-link`(196 §5.5 · `src/api/routes/alarm.py`) | 같은 라우트 파일을 고친다 | 이 계획 W2는 `GET /alarm/investigations/{id}` 응답 가산만 한다 — 196 엔드포인트·플래그(`NOISE_HOST_LINK_ENABLED`)와 독립 |

---

## 2. 표시 위치 선택지

| 안 | 위치 | 장점 | 단점 |
|---|---|---|---|
| **P1** | 장애 진단 → 조사 하위 탭 → 「조사 흐름」 단계 카드마다 「질의응답에서 확인」 한 줄(196 후보 블록 아래 · §1.4) | 단계 근거 바로 옆이라 무엇을 다시 보는지 분명하다. 조사 흐름 카드는 이미 「조회」 줄이 있어 자리가 맞는다 | 진단 탭은 `NOISE_INVESTIGATION_LIVE_VIEW_ENABLED`가 켜져야 보인다(U-70 case_na). 꺼진 배포에서는 기능이 없다 |
| **P2** | 조사 대화 고정 카드 아래 「다시 조회할 질의」 묶음 | 플래그와 무관하게 늘 있다. 같은 탭 입력창에 바로 채울 수 있어 동선이 가장 짧다. D-341 ⑪ 원 설계 위치다 | 단계 근거와 떨어져 보인다(카드 위쪽 섹션과 번호로 이어 줘야 한다) |
| P3 | 알람 카드 「조사 브리핑」 요약 | 알람에서 바로 | 카드 폭이 좁고 D-192 칩과 겹쳐 혼란 · 요약형 원칙(D-341 §5.6 ①)과 어긋남 |
| P4 | 노이즈 관제 서랍 | — | 관제는 조회 전용 화면 · 질의응답 입력창이 없다 |

**권고: P1 + P2**(같은 서버 산출을 두 곳이 그린다 — 렌더만 둘). P3·P4는 하지 않는다.

「조사 내용」 표시는 새 칸을 만들지 않는다 — 조사 흐름·실행 기록·고정 카드가 이미 그린다. 빠진 것은 **플래그가 꺼졌을 때와 조사 대화에서 단계별 조회 인자가 안 보이는 것**뿐이라, 이는 §3.4(W4 · Q6 확정)로 다룬다.

---

## 3. 질의 생성 방식

### 3.1 세 방식 평가 (CLAUDE.md 「LLM 비결정성」)

| 방식 | 내용 | 평가 |
|---|---|---|
| **(a) 결정적 템플릿** | 단계 `action`(→ 도구) × 브리핑 `scope`(대상 이름·사건창)를 코드 템플릿에 채운다 | **권고.** 같은 조사는 늘 같은 질의. 템플릿마다 검증 출처(골드셋·벤치 항목 id)를 붙여 테스트로 고정할 수 있다. 재료가 브리핑에 이미 있어(§1.1 1행) sre_agent 변경 없이 된다 |
| (b) LLM 요약 | 브리핑·실행 기록을 LLM에 주고 질의를 받는다 | 비권고. 답변 가능성 보장 없음(D-192 대안 기각 사유 그대로) · 같은 조사가 볼 때마다 다른 질의 · 브리핑 원문이 LLM으로 나간다(FabriX PII 필터·비용) |
| (c) 혼합 | (a)가 0건일 때만 LLM(D-192 Phase 2 `/alarm/suggest-prompt` 방식 · 기본 off) | HolmesGPT 단독 조사(§1.1 HolmesGPT 행)에만 의미가 있다. 이번 범위에서는 **보류 권고**(§8 Q7) |

템플릿 규칙(전부 결정적):
- 대상은 **이름을 박는다**(지시어 금지 — D-341 ⑫ · 고정 카드는 질의 맥락이 아니다). 대상이 비거나 가림 표시(`config.security.mask_pattern`)가 섞이면 그 질의를 만들지 않는다.
- 폴스타 축 대상 이름은 `scope.polestar_server`(196 확정 연결 반영)만 쓴다 · `polestar_link`가 `absent`·`unresolved`이거나 섹션 `status`가 `skipped`·`missing`이면 만들지 않는다(§1.4).
- 시간은 **사건창 절대 시각**으로 적는다(「10월 26일 19:10~19:40」). 상대 표현(「최근 30분」)은 지금 기준으로 풀려 조사 당시와 달라진다. 분 단위 해석은 D-328(사건 분 그대로 · 통계 테이블은 시 확장 + 고지)이 받는다.
- **현재값 전용 단계**(프로세스 목록·실행 중 요청·커넥션 풀)는 과거 시점을 다시 볼 수 없다. 넣는다면 「지금 값 — 조사 당시와 다를 수 있음」 표지를 붙인다(§8 Q5).
- 도구 인자 원문(`label`)은 쓰지 않는다 — 실행 중 SQL 문(`apm_active_detail`) 같은 값이 질의에 섞이지 않게 한다.

### 3.2 단계 → 질의응답 경로 (사다리 2단 기준)

2단의 처리기: `data_query`·`alarm_query`(SQL) · `process_query`(폴스타 실시간 REST) · `host_inspect`(mcp_server 고수준 · 조사 플래그 필요) · `apm_query`(제니퍼 게이트웨이 · 엔드포인트 있을 때만 · WAS 질의 정본). 출처 `src/orchestration/subagents.py:1507-1600` · `apm_query.py` 머리 · `config/db_registry.yaml` `solutions[apm].views`.

| 플레이북 action (도구) | 질의응답 경로 | 검증 출처(현재) | 판정 |
|---|---|---|---|
| `host.usage_trend` (`polestar_metric_trend`) | `data_query` — 성능 통계 SQL | 골드셋은 「지난달 1개월 평균·최대」(gp-009·yd-004·b0-004)뿐 · **사건창 시간대 추이 없음** | 골드셋 항목 추가 필요 |
| `host.process_top` (`polestar_process_snapshot`) | `process_query` — 현재값 | routing r-081(미채점)만 | 현재값 · 미검증 |
| `host.alarm_history`·`xsource.related_alarms` (`polestar_alarm_history`·`polestar_incident_alarms`) | `alarm_query` — 알람 SQL | gp 「최근 3개월 경고 이상 알람 상위 10 서버」 · routing 「이번 달 CPU 임계값 초과 알람 이력」 — **서버 한정·사건창 형은 없음** | 골드셋 항목 추가 필요 |
| `change.recent` (`polestar_change_history`) | `data_query` 변경 용어(`change_terms`) 경로 — 폴스타 변경 이력과 같은 데이터인지 **미확인** | 없음 | W0 실측 |
| `change.recent` (`apm_source_changes`) | `apm_query` `apm.changes` | apm_bench 항목 확인 필요 | W0 대조 |
| `host.topology` (`polestar_topology`) | **경로 없음**(src에 topology 처리기 0) | — | 질의 만들지 않음 |
| `host.resource_status` (`polestar_resource_status`) | `host_inspect` `resource_status` 프로파일(조사 플래그 on에서만) | 없음 | 미검증 · 플래그 의존 |
| `scope.host_to_was` (`apm_instance_map`) | `apm_query` `apm.instances` | apm_bench 「{host} 서버에 올라가 있는 WAS 인스턴스가 뭐야?」 | 검증 형태 있음(벤치) |
| `apm.golden_signals` (`apm_app_health`·`apm_period_compare`) | `apm.app_health` · 비교 창(`window: compare`) | apm_bench 「{host} WAS 지난 30분 에러율이랑 p95 응답시간」 · `APM-COMPARE-day/week` | 있음(벤치) |
| `apm.slow_tx_breakdown` (`apm_slow_transactions`·`apm_transaction_profile`) | `apm.slow_tx` · `apm.profile`(앞 결과 참조 — 단독 질의 불가) | apm_bench `APM-SLOWTX-*` | slow_tx만 있음 |
| `apm.runtime_and_pool` (`apm_runtime_health`·`apm_resource_pool`) | `apm.runtime` · `apm.pool`(현재값) | apm_bench 힙 추세·커넥션 풀 항목 | 있음(벤치 · 풀은 현재값) |
| `apm.active_services` (`apm_active_services`) | `apm.active`(현재값) | apm_bench 「{inst} 지금 돌고 있는 서비스」 | 현재값 |
| `apm.active_sql` (`apm_active_detail`) | 단독 보기 없음 · SQL 원문 | — | **만들지 않음**(민감) |
| `apm.events_errors` (`apm_events`) | `apm.events` | apm_bench 이벤트·오류 기록 항목 | 있음(벤치) |
| `dpm.db_health` | 미연동 | — | 만들지 않음 |
| HolmesGPT 도구(mcp_server 고수준 · PromQL 포함) | 인자 미구조화 | — | 만들지 않음(§3.1 (c) 보류) |
| (196 W3 D2 · `was.sql` 등 APM SQL 계열 — 미구현) | `apm_query` 보기 미정 | 없음 | 196 W3 뒤 W0 대조표에 추가 |

요약: **제니퍼 단계는 apm_bench를 검증 출처로 인정하면 대부분 질의를 낼 수 있다.** 폴스타 단계는 골드셋에 「서버 한정 × 사건창 시간대」 형을 새로 넣어 실 DB로 검증해야 한다(docs/18 2026-07-20 — 실행 검증 없는 골드 금지).

### 3.3 「검증된 형태」 기준 — D-192와의 관계

D-192는 「추천 = 골드셋 동형의 재사용」이고 D-341 ⑪이 조사 대화에 같은 규칙을 걸었다. 선택지는 §8 Q2. 권고는 **「골드셋 + 제니퍼 벤치」를 검증 출처로 인정**하고(벤치는 처리기·보기·대상·창까지 기대값을 채점하는 세트라 골드셋과 같은 성격이다), 폴스타 시간대 형은 W0에서 골드 항목을 보탠 뒤에만 낸다. 이 선택은 D-341 ⑪의 「골드셋 동형」 문언을 넓히므로 **결정 후 D-341 부기(또는 D-192 부기)로 등재 예정**이다.

### 3.4 조사 내용 보강 — 단계별 조회를 브리핑에 저장 (Q6 확정 · W4)

지금 조회 인자는 옵트인 2개가 켜졌을 때 실행 기록에만 있다. sre_agent가 섹션에 `queries: [{tool, phrase}]`(196 `reason_detail`·`reused_from`과 같은 `StepResult` 가산 키 — 값이 없으면 키를 싣지 않는다 · 재사용 섹션은 부모 섹션의 `queries`를 그대로 가진다)(이미 만드는 `tool_phrase` 문장 · 인자 원문은 제외)를 저장하면, 플래그와 무관하게 조사 대화 고정 카드·보관 뒤에도 「무엇을 조회했나」가 보인다. 비용: sre_agent 변경(별 venv 테스트) · 브리핑 32KB 상한(`MAX_BRIEFING_BYTES`) 안에서 클립 우선순위 조정. Q6 = 저장 확정(2026-10-27) — W4 정식 Wave.

### 3.5 W0 결과 (2026-10-27 · 실측)

- **낸다(제니퍼 축 · 출처 = `testdata/apm_bench/scenarios.yaml`)**: `scope.host_to_was`(APM-INST-host) · `apm.golden_signals`(APM-HEALTH-colloquial · APM-HEALTH-host-30m) · `apm.runtime_and_pool`(APM-RUNTIME-heap · APM-RUNTIME-metrics-interval · 현재값 APM-POOL-inst · APM-POOL-mode) · `apm.slow_tx_breakdown`(APM-SLOWTX-10m · APM-MT-profile-sql) · `apm.events_errors`(APM-ERROR-record · APM-ERROR-colloquial) · `apm.active_services`(현재값 APM-ACTIVE-inst · APM-ACTIVE-n) · `change.recent` 제니퍼 축(APM-CHANGES-default · APM-CHANGES-3d · 사건창 끝 기준 24시간 = 스텝 기본 lookback).
- **내지 않는다(부재를 테스트로 고정)**: `host.usage_trend` · `host.process_top`(r-081 미채점) · `host.alarm_history` · `xsource.related_alarms` · `change.recent` 폴스타 축(④ — `polestar_change_history`는 자원 생애주기 이력, `change_terms`는 급증 어휘라 다른 데이터) · `host.resource_status` · `host.topology` · `apm.active_sql` · `dpm.db_health` · `scope.resolve_was_targets` · `synthesize` · HolmesGPT 단독. 196 W3 `was.sql`·`was.fallback`에 새 action 없음.
- **시간 표기**: 「YYYY년 M월 D일 오전/오후 h시 m분부터 …까지」 — 질의응답 `time_expr.interpret_detail(subject="event")`가 0~23시·날짜 걸침을 분 단위로 되읽음을 테스트로 고정. 「19:10~19:40」은 하루로 뭉개지고 표지 없는 1~12시는 되묻기라 쓰지 않는다.
- **② 폴스타 골드 미등재(잔여)**: 샌드박스 `polestar_pg`에서 서버 한정 알람 이력 초안 SQL 1행 실행 확인(성능 추이는 샌드박스 시 통계가 자리 행뿐이라 검증 불가). gp/yd/b0 골드는 실 대상 DB `--check-gold`로 확정해 온 세트라(README 착수 조건 · docs/18 2026-07-20) 픽스처 서버명·시각 골드는 등재하지 않았다 — 내부망에서 실존 서버·실제 알람 시각으로 검증 뒤 등재하고 카탈로그에 폴스타 축 항목을 더한다(가드는 구현·테스트 완료).
- **③ MLX 미실행(잔여)**: 이 체크아웃에 `.env`가 없어 두 평면이 `ollama`/`vllm`로 해석됨(`scripts.bench --show-env`) — 절대 구간 질의가 벤치와 같은 보기·창으로 처리되는지 1회 확인 필요.

---

## 4. 보안·권한

| 항목 | 현황 | 이 기능의 규칙 |
|---|---|---|
| 조사 열람 인가 | `_assert_investigation_access`(`src/api/routes/alarm.py:1713`) — 알람 존 전부 · 사용자 요청 조사는 소유자 · 추가 조사는 소유자·관리자 | 질의는 **조사 조회 응답에 가산**한다 — 열람 인가를 새로 쓰지 않는다(docs/18 2026-10-19 재발 방지) |
| 마스킹 | 사용자 뷰는 D-325 ③ `mask_fn`(`alarm.py:1128` — 비밀값 꼴만 가림 · 서버 이름은 남음) | 질의는 **이미 가린 사용자 뷰**에서 만든다(운영자 원문에서 만들지 않음). 가림 표시가 섞인 값은 질의 생략. 도구 인자 원문·실행 중 SQL은 쓰지 않는다 |
| 질의 권한 ≠ 조사 권한 | 조사는 **알람 존** 기준, 질의응답은 사용자 `allowed_db_ids`(`src/state.py:353`) 기준 — 서로 다르다. 「내 요청 조사」는 존 검사 없이 열린다(`_request_owner_view`) | 서버가 대상 `polestar_source`(→ db_id)를 사용자 `allowed_db_ids`와 대조해 범위 밖이면 질의에 `disabled_reason`을 싣고 UI는 버튼을 막고 사유를 보인다(plans/187:659). 질의를 실행해도 질의응답 쪽 DB 범위 검사가 최종 방어선이다 |
| 새로 드러나는 정보 | 질의 문장의 값(서버·인스턴스 이름·사건 시각)은 이미 같은 화면에 보이는 값 | 새 노출 없음 |
| 감사 | 질의응답 실행은 기존 감사(`audit_*`) 대상 | 조사 대화에서 보낸 첫 턴은 이미 `disclosures.ref.investigation_id`가 실린다. 질의응답 탭으로 보낸 경우를 구분하려면 요청에 `origin="investigation_requery"` 같은 표지를 더할지 §8 Q4에서 함께 정한다 |
| 과금 | 질의 1번 = 질의응답 1턴 | 자동 전송 금지 · 확인 뒤 전송 · 진행 중 버튼 비활성(D-192 그대로) |

---

## 5. 사용자 동작

| 동작 | 내용 | 권고 |
|---|---|---|
| 보기 | 단계 카드·고정 카드에 질의 문장 표시(「질의응답에서 확인」 머리 · 현재값 표지 · 범위 밖 사유) | 기본 |
| 복사 | 문장을 클립보드로 | 기본 |
| 조사 대화에서 조회 | 조사 대화 탭 입력창에 채우고 확인 바 → `[이대로 조회]`. 장애 진단 탭에서 누르면 그 조사의 조사 대화를 열고(`openInvestigationConversation`) 채운다 | **권고** — 조회가 조사에 묶여 이력에 남는다 |
| 질의응답으로 보내기 | `stageAlarmPrompt`와 같은 동작(질의응답 탭) | 대안 — 조사와 무관한 새 대화가 된다 |
| 즉시 조회 | 누르자마자 전송 | 비권고(D-192 G-2 — 사람이 읽지 않은 문장을 보내지 않는다) |

구현 메모: 조사 대화 탭을 유지하는 인계는 `stageAlarmPrompt`를 고치지 않고 새 함수(예 `stageInvestigationPrompt`)로 둔다 — D-192가 갈라 둔 두 계약(`reuseHistoryQuery`·`stageAlarmPrompt`)에 세 번째를 더하는 셈이라 세 함수 주석이 서로를 가리키게 한다. `app.js:4795` 「추천 칩은 내지 않는다」 주석은 이 기능이 들어갈 때 고친다.

---

## 6. 결정과의 관계 (충돌 점검)

| 결정 | 관계 | 처리 |
|---|---|---|
| **D-192 · D-341 ⑪ 「골드셋 동형만」** | **Q2 ② 확정(2026-10-27)** — 검증 출처를 「골드셋 + 제니퍼 벤치(`testdata/apm_bench`)」로 넓히므로 **문언 개정**이다. 미검증 표지 허용(③)은 택하지 않았다 — 「검증된 형태만」 원칙 자체는 유지 | **부기 2건 필요 — 구현 Wave에서 등재**: D-341 ⑪(조사 대화 질의 = 골드셋·제니퍼 벤치 동형 · 단계 카드 P1 포함 · 조사 대화 탭 유지 인계) · D-192(검증 출처에 벤치 추가 — 알람 카드 칩 축은 불변). 등재 시점 = W1 끝(템플릿 카탈로그·출처 id 확정 뒤) · 색인 상태 칸·CHANGELOG 1행 동반 |
| D-341 ⑪ 「고정 카드는 질의 맥락이 아니다」 · ⑫ G-19 | 질의가 대상·시각을 문장 안에 다 담아 맥락이 필요 없다 | 충돌 없음 · G-19 2단계(결정적 문맥 시드)와 독립 |
| D-325 ③ 사용자 뷰 마스킹 | 가린 뷰에서 만든다 | 충돌 없음 |
| D-183 · D-192 인계 계약 | 새 인계 함수를 더한다 | 기존 두 함수 불변 · 테스트로 고정 |
| D-347 수동 전용 | 무관(조사 실행이 아니라 조회 질의) | — |
| D-248 ④ 완결 턴만 기록 · ⑧ 부기 | 조사 대화에서 조회하면 첫 턴이 조사와 묶여 이력에 남는다 | 충돌 없음 |
| D-162 기본 off | 화면에 새 요소가 생긴다 | 플래그 기본 off 권고(§8 Q8) |

---

## 7. Wave (v1.1 확정 — Q2·Q3·Q4·Q6 반영)

선후: W0 → W1 → W2 → W3. W4(sre_agent)는 W1과 병렬 가능(파일 겹침 없음 — W3의 고정 카드 「조회한 것」 줄은 W4 키를 읽으므로 W4 뒤). W5는 W3·W4 뒤.

**196 선행(v1.2)**: W2는 196 W2b(`src/api/routes/alarm.py` 호스트 연결 API) 뒤, W3는 196 W1(D6 문구·상태 라벨)·W2b(`diagnosis-view.js` 후보 블록) 뒤, W4는 196 섹션 가산 키(`playbook_steps.py`) 뒤에 착수한다 — 같은 파일을 196이 미커밋으로 고치는 중이다. W0의 APM SQL 계열 대조는 196 W3 뒤.


| Wave | 내용 | 산출·수용 기준 |
|---|---|---|
| **W0** 실측·검증 출처 | ①단계별 템플릿 초안 × 골드셋·apm_bench 대조표(항목 id) ②폴스타 「서버 한정 × 사건창 시간대」 형 골드 항목 초안 — 로컬 DB 실행 검증(docs/18 2026-07-20) ③대표 질의 소수를 로컬 MLX로 1회(두 평면 `mlx` 확인 · `RUN_LOCAL_LLM=1`) 라우팅·처리기 확인 ④`polestar_change_history`와 질의응답 변경 경로가 같은 데이터인지 확인 | 템플릿마다 출처 id 또는 「출처 없음 → 내지 않음」. Gemini·`RUN_E2E=1` 금지 |
| **W1** 도메인 (+ D-341 ⑪·D-192 부기 등재) | `src/domain/investigation_requery.py`(순수 · stdlib) — 입력: 가린 사용자 뷰의 `playbook`(scope·sections·incident_window) / 출력: `[{step, title, prompt, path, source_ref, realtime}]` · 템플릿 카탈로그는 모듈 상수(출처 id 동봉) | 같은 입력 → 같은 출력 · 대상 비거나 가림 표시면 생략 · 지시어 0 · 절대 시각 · 출처 없는 action은 0건(테스트로 부재 고정 — D-192 `server.Network` 선례) · `overfit_check` 신규 유입 0(도구 이름은 스키마 리터럴이 아님을 실측) |
| **W2** API | `GET /alarm/investigations/{id}` 응답에 `requery` 가산(플래그 on에서만 · off면 응답 비트 동일) · `allowed_db_ids` 대조 → `disabled_reason` · 설정 1개 + `settings_help`·카탈로그 자기 델타 | 인가 재사용(새 인가 코드 0) · 운영자 뷰도 같은 가린 산출(원문 미사용) · `src/config/` 변경이라 `[웨이브 회귀 필요]` |
| **W3** UI | 장애 진단 조사 흐름 단계 카드 「질의응답에서 확인」 줄(P1 · 196 후보 블록 아래 · 선택 대기 단계는 그리지 않음) · 조사 대화 고정 카드 묶음(P2) · 복사 · 「조사 대화에서 조회」(새 인계 함수 · 확인 바 · 진행 중 비활성) · 서버 텍스트는 `textContent`(D-192 — 속성에 넣지 않음) | D-183·D-192 두 함수 불변 테스트 · 자동 전송 0 · 범위 밖 비활성 + 사유 |
| **W4** 조사 내용 보강(Q6 확정) | sre_agent 섹션 `queries[{tool, phrase}]` 저장 · 고정 카드 상세형에 「조회한 것」 줄 · 32KB 클립 순서 | sre_agent 별 venv 테스트 · 인자 원문 미저장 · 구버전 브리핑(키 없음) 렌더 무변경 |
| **W5** 매뉴얼 | U-63(조사 대화) · U-70(장애 진단 조사 흐름) 본문·앵커·캡처(D-255) | `pytest tests/test_manual` |
| 계획 끝 | W4는 sre_agent 별 venv 테스트를 따로 보고 · `python scripts/regress.py --base <계획 시작 SHA> --files <누적>` 1회 · verifier(보안 감사 합침) | 회귀 원래 실패만 · Critical 0 |

테스트 방법: W1 순수 함수 단위(픽스처 = `build/manual_capture/.../alarm_investigations.jsonl` 모양의 플레이북 브리핑 · host/was 각 1종 · 가림 표시·빈 대상·현재값 단계) · W2 라우트(플래그 off 비트 동일 · 존/소유자 인가 재사용 · `allowed_db_ids` 범위 밖 · 운영자 뷰) · W3 D-190 방식 함수 원문 추출 + DOM 스텁(자동 전송 없음 · 탭 유지 · 확인 바) · MLX는 W0 1회만.

---

## 8. 사용자 결정

### 8.0 결정 결과 (2026-10-27)

| # | 결정 | 상태 | 계획 반영 |
|---|---|---|---|
| Q1 | A 질의응답에 넣을 질의 | **사용자 확정**(2026-10-27 · 권고안대로) | 조사 재실행(B)은 범위 밖 — 기존 U-64·U-66 |
| Q2 | **② 골드셋 + 제니퍼 벤치 인정** · 폴스타는 W0 실측으로 골드 보강 | **사용자 확정** | §3.3 · §6(D-341 ⑪·D-192 부기 — W1 끝 등재) · W0 |
| Q3 | **둘 다**(장애 진단 단계 카드 P1 + 조사 대화 고정 카드 P2) | **사용자 확정** | W3 |
| Q4 | **복사 + 조사 대화 입력창에 채우고 「이대로 조회」 확인 후 조회** | **사용자 확정** | W3 새 인계 함수(조사 대화 탭 유지) · 첫 턴은 기존 `disclosures.ref.investigation_id`로 조사와 묶이므로 별도 `origin` 표지는 두지 않는다(§4) |
| Q5 | 「지금 값 — 조사 당시와 다를 수 있음」 표시 달고 넣음 | **사용자 확정**(2026-10-27 · 권고안대로) | W1 `realtime` 표지 · W3 렌더 |
| Q6 | **저장**(sre_agent 섹션 `queries[{tool, phrase}]`) | **사용자 확정** | W4 정식 Wave |
| Q7 | HolmesGPT 단독 조사는 질의 없음 | **사용자 확정**(2026-10-27 · 권고안대로) | W1 — 플레이북 섹션 없는 브리핑은 0건 |
| Q8 | 기본 off(관리자가 켬) | **사용자 확정**(2026-10-27 · 권고안대로) | W2 설정 1개 · D-162 |

### 8.1 원 질문 표 (v0.1 — 기록용)

| # | 질문 | 선택지 → 결과 | 권고 |
|---|---|---|---|
| Q1 | 「다시 조회하기 위한 프롬프트」가 무엇인가 | **A 질의응답에 넣을 질의** → 사용자가 근거 데이터를 직접 다시 본다(이 계획) · B 조사를 다시 돌리는 요청 → 이미 「다시 분석」「추가 조사 요청」 버튼이 있어 새로 만들 것 없음 · A+B → A만 만들고 B 버튼 위치만 안내 | A |
| Q2 | 어떤 질의까지 보여 주나(답이 나온다는 확인 기준) | ① **지금 규칙 유지(텍스트투SQL 정답 세트에 같은 꼴이 있는 것만)** → 현재 보여 줄 질의가 거의 없음, 폴스타 단계는 정답 세트를 늘려야 생김 · ② **제니퍼 벤치 세트도 인정** → 제니퍼(WAS) 단계 대부분에 바로 질의가 생김, 폴스타는 ①과 같음 · ③ 확인 안 된 질의도 「답이 안 나올 수 있음」 표시를 달아 보여 줌 → 단계마다 질의가 생기지만 눌렀을 때 엉뚱한 답이 나올 수 있음(지금 결정 D-192와 반대) | ② (+ 폴스타 정답 세트 보강은 W0) |
| Q3 | 어디에 보여 주나 | 장애 진단 탭 단계 카드 → 근거 옆에서 바로, 단 실시간 화면 설정이 켜진 곳에서만 보임 · 조사 대화 카드 → 늘 보임, 바로 입력 가능 · **둘 다** → 두 곳에 같은 질의 | 둘 다 |
| Q4 | 누르면 무엇이 되나 | 보기·복사만 → 실수 전송 0, 대신 붙여넣기 필요 · **조사 대화 입력창에 채우고 「이대로 조회」로 확인 후 조회** → 조사와 묶여 이력에 남음 · 질의응답 탭으로 보냄 → 조사와 무관한 새 대화 · 누르자마자 조회 → 읽지 않고 보내 비용 발생 위험 | 복사 + 조사 대화에서 확인 후 조회 |
| Q5 | 「지금 값만 볼 수 있는」 단계(프로세스 목록·실행 중 요청·커넥션 풀)는 | 빼기 → 혼동 0, 대신 그 단계는 질의 없음 · **넣되 「지금 값 — 조사 당시와 다를 수 있음」 표시** → 현재 상태 확인용으로 씀 | 표시 달고 넣기 |
| Q6 | 조사 내용(무엇을 조회했나)을 조사 기록에 따로 저장할까 | 안 함 → 실시간 화면 설정 2개가 켜진 곳에서만 장애 진단 탭에 보임 · **저장** → 설정과 무관하게 조사 대화 카드에도 「조회한 것」 줄이 생김(조사 서비스 쪽 변경 포함) | 폐쇄망 설정값 확인 뒤 결정 — 두 설정이 꺼져 있다면 저장 |
| Q7 | AI 자유 조사(HolmesGPT)만 한 조사는 | **질의 없음** → 정해진 꼴을 만들 재료가 없음 · AI가 질의를 지어냄(기본 꺼짐) → 답이 나온다는 보장 없음 | 질의 없음 |
| Q8 | 처음에 켜 둘까 | **기본 꺼짐, 관리자가 켬** → 지금 화면과 같음 · 기본 켜짐 → 바로 보임 | 기본 꺼짐 |

---

## 9. 범위 밖

- 조사를 다시 돌리는 기능(이미 있음 — U-64·U-66) · 폴스타 서버를 골라 폴스타 단계만 다시 조회(`plans/196` §1.1 ②).
- 제니퍼 → 폴스타 호스트 연결·후보·패턴(`plans/196`).
- 조사 대화의 문맥 승계(G-19 2단계 — 별 계획).
- 브리핑 자체에 대한 질문(G-20 — 별 계획).
- 알람 카드 D-192 칩의 축 확장.
- 노이즈 관제 서랍·알람 카드 요약형 변경.
