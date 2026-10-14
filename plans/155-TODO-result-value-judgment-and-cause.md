# 155. 조회 결과 값의 정상/위험 판정 · 원인 추정 분석

> **작성일**: 2026-10-13 · **v1.0**(최초 — 범위 정리 · 요건 인터뷰 전) · **v1.1**(2026-10-13 — 인터뷰 5문 확정 · §8) · **v1.2**(2026-10-14 — 현행 구현 실측 · 설계 · 게이트 G-1~G-10 권고안 · Wave W0~W5 · 수용 기준) · **v1.3**(2026-10-14 — 게이트 전건 권고안 확정 · D-330 등재)
> **상태**: **TODO — 계획 확정(코드 0건) · 게이트 G-1~G-10 확정(§3 · 2026-10-14 권고안 전건) · 결정 D-330** · 착수 가능(W0부터) · 전제 `plans/160`(완료)·D-327은 2026-10-14 main 병합(`4073f7f`)으로 이 브랜치에 있음
> **요청 원문(2026-10-13)**: `plans/160` 인터뷰 중 *"결과 안 위치와 의미까지만, 판정·원인 추정 분석은 별도의 계획으로 정리하라."* · 2026-10-14 *"현재 구현되어 있는 내용을 검토한후 155번 계획을 구체화하라."* · 게이트 확정 *"권고에 맞게 정리하라."*
> **성격**: 현행 실측 + 설계 + 확정 게이트 + 작업 분해.
> **상위·인접 계획**: `plans/160`(그래프·용어 사전·값 해석 — 이 계획의 전제 UI) · `plans/60`(노이즈 게이트 동적 baseline · **D-110** — v1.0의 「D-079」는 결번 표기 오류) · `plans/87`(APM 게이트웨이 · 잠정 임계 §5.4(b)) · `plans/153`(알람 설명 · 조사 저장소 · 수동 조사 W7 제외) · `plans/134`(제니퍼 장기 조회 작업 카드) · `sre_agent/`
> **관련 결정**: **D-330**(이 계획) · D-327 ④(160 값 해석의 LLM 문장은 판정·원인 금지 — 유지) · **D-274 ⑤**(WAS 판정은 게이트웨이 domain 한 곳 · 소비자 재구현 금지) · D-110(동적 baseline) · D-123(조사 submit/poll) · D-170 R-9 · D-172(`HOST_AUTHZ_MODE=admin_only` — 조회 권한 ≠ 조사 권한) · D-325 ⑩(운영자 수동 조사 버튼 제외 — 사유 「비용·예산 정책 별도 필요」) · D-262(결과 API 소유자 인가) · D-118 · D-162 · D-255 · D-303 · D-127·D-240 · D-179
> **실측 기준**: 2026-10-14 `apm_noise` = `main` `4073f7f`. `file:line`은 이 시점 값이다 — W0에서 다시 대조한다.

---

## 0. 무엇을 다루나

`plans/160`의 값 해석은 **결과 안에서의 위치와 의미**(순위·평균 대비·직전 추세)까지만 말한다. 이 계획은 그 다음 두 질문을 다룬다.

| 질문 | 예 | 160와의 차이 |
|---|---|---|
| **판정** — 이 값이 기준을 넘었나 | 「힙 사용률 0.93은 기준을 넘었나?」 | 결과 밖의 **기준**(임계치·평소 범위)이 있어야 한다 |
| **원인 추정** — 왜 이렇게 됐나 | 「15시 힙 급증은 GC 지연 때문인가?」 | 결과 밖의 **다른 신호**를 더 조회해야 한다 |

160에서 이 둘을 막은 이유는, 기준과 추가 신호가 없는 상태에서 LLM에 맡기면 **근거 없는 답을 지어내기** 때문이다. 이 계획의 답은 다음 두 줄이다.

- **판정은 LLM이 하지 않는다.** APM 게이트웨이(임계 소유자 · D-274 ⑤)가 결정적으로 판정하고, 본체는 그 결과를 기준 출처와 함께 **별도 블록**으로 보인다. 160의 LLM 문장 금지(D-327 ④)는 그대로 둔다.
- **원인 추정은 본체가 하지 않는다.** 「원인 조사」는 `sre_agent`(HolmesGPT)에 위임하고(계약 변경 없는 `sre_diagnose`), 결과는 대화 아래 **조사 작업 카드**에 「가설 · 참고용」으로 보인다.

### 0.1 요약 흐름

```
값 셀 클릭 ─┬─ POST /query/{id}/interpret   (160 현행 · 위치 계산 + LLM 문장)
            └─ POST /query/{id}/judge       (신규 · LLM 0)
                  인가 → APM 결과인지 → 그 행의 수치 칼럼만
                  → 게이트웨이 MCP apm_judge_values(rows)  ← 판정은 게이트웨이 domain
                  → 클릭한 칼럼이 입력에 든 판정만 + 같은 호스트의 게이트웨이 신호(was_signals)
팝오버: [계산값] [판정 · 참고용 · 기준 출처] [평소 범위: 없음(사유)] [LLM 해석 · 참고용] [원인 조사]
「원인 조사」 → POST /query/{id}/investigate  (인가 · 상한 · 감사)
                  → sre_diagnose(question, hostname, reference_time, lookback) 접수만
                  → InvestigationStore(kind=diagnosis · owner · query_id) 기록
             → 팝오버 닫힘 → 답 말풍선 아래 조사 작업 카드(진행 → 완료 · 15초 재조회)
```

---

## 1. 현행 실측(2026-10-14)

### 1.1 160 값 해석 — 끼울 자리

| 항목 | 실측 | 근거 |
|---|---|---|
| 클릭 → API | 셀 클릭 → `openExplain` → `POST /api/v1/query/{id}/interpret {table:int, column:str, row:int}`(`extra=forbid`) | `src/static/js/result-viz.js:910` · `src/api/routes/result_viz.py:166` |
| 표·행 찾기 | `_explain_table`: 플래그 → `_owned_result`(D-262) → `result_tables(query_results, result_tables, security)`(마스킹·표 분할·평탄화) → 표 index · 칼럼 실존 · `0 ≤ row < min(len,1000)` | `result_viz.py:176-283` |
| 계산 | `value_context()` → `facts[{key,label,value}]`(rank·is_max/min·vs_mean/median_pct·series_rank·trend·related_ratio / 비수치: frequency·row_time·row_ident) | `src/domain/result_value_context.py:302` |
| 응답 | `interpret_value()` → `{column,row,value,facts,text,source,reason}` · 캐시 `(query_id,table,column,row)` · LLM 20초 · 동시 4 | `src/semantic/result_explainer.py:206` |
| 금지 가드 | 프롬프트 규칙 2~4 + `FORBIDDEN_INTERPRETATION_TERMS` 18어(위험·정상·원인·주의·경고…) 부분 문자열 → 문장 폐기 | `src/prompts/result_explain.py:32-76` · `result_explainer.py:246-250` |
| 팝오버 | 전역 `openPop` 하나 · `fillInterpret(body,res)`가 값 → facts `<dl>` → LLM 블록 순으로 채운다 — **판정 블록·버튼을 붙일 자리** | `result-viz.js:787-908` |
| 출처 메타 | `result_source(stored)` = `db_ids` + `is_apm`(`apm_source_scope`/`apm_source_clarification` 유무)뿐. **APM 도구명·`was_signals`·대상 호스트는 저장 안 됨** | `src/semantic/result_glossary.py:75-83` · `src/api/result_chart_view.py:185` |
| 행 칼럼 | APM 행은 게이트웨이 키 그대로 + `hostname`(`dict(row)` + hostname). 평탄화(`chart_rows`)는 문자열 범주·시간 키·지표 키만 남기고 숫자 ID(`instance_id`)는 뺀다 | `src/orchestration/apm_query.py:3584-3596` · `src/domain/result_chart.py:362` |
| 플래그 | `ResultVizConfig`(`RESULT_VIZ_`) `chart_enabled`·`explain_enabled` 기본 off · 재기동 반영 | `src/config.py:1546-1558` |
| 매뉴얼 | 사용자 U-59 「칼럼 용어 사전·값 해석」(:761-806) — :795에 「판정·원인을 하지 않는다」 문구 | `scripts/manual/content/user.md` |

### 1.2 판정 기준 원천

| 원천 | 실측 | 쓸 수 있나 |
|---|---|---|
| **WAS 잠정 임계** `WasThresholds` | `apm_gateway/apm_gateway/domain/signals.py:38-66` — `heap_usage_ratio` 0.9 · `gc_time_usage_pct` 10.0 · `db_pool_usage_ratio` 0.95 · `error_rate` 0.2(`error_min_calls` 20) · 힙 지속 3샘플 · 누수 계단 · 스레드 정체 · 시간 비중. 정책 `apm_gateway/config/was_signatures.yaml`(값 = 기본값 · 「잠정 · J0-L-b로 보정」 — **운영 보정값 없음**) | **단일 임계** — 주의/위험 2단계 없음. 신호 종류마다 수준 고정(힙·GC = WARNING · DB 풀·오류 = CRITICAL) |
| 게이트웨이 신호 `was_signals` | `apm_app_health`·`apm_runtime_health`·`apm_resource_pool` 등 6개 도구가 이미 반환(`{kind,level,category,label,evidence,instance_id,source_tool}`) · 본체는 `meta["was_signals"]`로 옮겨 답 본문 결정적 줄을 만든다 | 이미 있는 **지속 판정**(예: 힙 3샘플 연속). 결과 저장소엔 안 실림 |
| 임계 구조화 조회 | **없다** — 임계는 evidence 문자열 안에만 | 신규 도구 필요 |
| 판정 대상 칼럼 | `apm_runtime_health` 행: `heap_used_mb`·`heap_committed_mb`·`gc_time_usage_pct`·**`heap_usage_ratio`(계산값)** · `apm_resource_pool` 행: `db_pool_active`·`db_pool_configured_avg`·**`db_pool_usage_ratio`(계산값)** — **분모가 같은 행에 있다**. `apm_metrics`는 long 형식(`metric,time_ms,value`)이라 분모가 다른 행 | runtime·pool 행은 행 단위 판정 가능 · long 형식은 비율 판정 불가 |
| 동적 baseline(D-110) | `noise_gate/infrastructure/polestar_metric_baseline.py` — 입력 `AlarmEvent` · 폴스타 `cmm_metric_stat_h` · CPU·메모리 2종 · 최소 3일+1 | **APM 미적용**. 순수 함수 `holt_winters_fit`·`anomaly_score`만 재사용 가능. APM 3일 이력 조회 가능 여부 미확인(plans/87 U-2 · J0-L-b) |
| 폴스타 알람 임계(2차) | **정정**: `cmm_alarm_def`엔 없지만 조건 테이블 **`cmm_alarm_def_c_con`**에 `threshold_numeric`·`threshold_numeric2`·`operator`·`units`·`thresholdtype`·`baselinetype` 실재(`testdata/pg/init/04_create_all_tables.sql:650-`). 단 `alarm_allowed_tables`(`config/db_profiles/polestar.yaml:624-633`) 밖 · 코드 참조 0 · 운영 DB2 구조 미확인 | 2차 — 범위 밖(G-9) |

### 1.3 원인 조사 경로(sre_agent)

| 항목 | 실측 | 근거 |
|---|---|---|
| 계약 | sre_agent는 MCP 도구 5종(HTTP REST 아님). `sre_investigate_alarm`(알람 묶임 · fingerprint dedup) · **`sre_diagnose(question, server_name?, hostname?, db_id?, target_state?, reference_time?, lookback_minutes?)` = 알람 없는 진단(kind=diagnosis)** · `sre_get_investigation` 상태 `running/done/failed/timeout/stub/rejected/not_found` · 잡 TTL 3600초 | `sre_agent/sre_agent/interface/mcp_service.py:282-354` · `investigation_jobs.py:42-54,361-413` |
| 본체 클라이언트 | `SreAgentClient.submit/poll/diagnose` · `build_sre_agent_client(ng)`(URL 없으면 None) | `noise_gate/infrastructure/sre_agent_client.py:189-314` |
| 기존 채팅 진단 | `src/nodes/fault_diagnosis.py` — 호스트 인가 → diagnose → **45초 동기 대기** · InvestigationStore 미기록 · 성공 위임은 감사 안 함 | `:81-310` |
| 저장소 | `InvestigationStore` JSONL `{investigation_id, alarm_ids[], kind, status, alarm_db_ids?, verdict?, briefing?, briefing_lines?, ts, source}` · kind 실값 `alarm`/`episode` | `noise_gate/infrastructure/investigation_store.py:246-305` |
| 조회 라우트 | `GET /alarm/investigations/{id}`·`/by-alarm/{alarm_id}` — 지연 poll(같은 id 10초 1회 · id별 Lock · 종결만 기록) · 운영자(admin/인증 off)는 원문, 그 외 마스킹 | `src/api/routes/alarm.py:1626-1904` |
| 존 판정 | `_assert_investigation_zone` — **`alarm_ids`가 비면 제한 스코프 사용자 무조건 403** → 알람 없는 조사엔 그대로 못 쓴다 | `alarm.py:1687-1717` |
| 인가 | `authorize_host_investigation` · `HOST_AUTHZ_MODE` 기본 `admin_only`(admin·system만) | `src/domain/host_authz.py:47-49,99-` · `src/config.py:1393` |
| 화면 | `BriefingView`(재조회 15초 · 상한 300초 · `statusChip`·`renderBody`·`createPoller`) · 대화 비동기 카드 선례 = plans/134 `.apm-job-card`(말풍선 안 · 진행 3초 · 숨김 10초 · 403/404 「사라짐」) | `src/static/js/briefing-view.js:23-347` · `src/static/js/app.js:3182-3340` |
| 감사 | `log_investigation(entry_point, targets, outcome, user_id, backend, …)` event=`host_investigation` · entry_point 현행 `chat`/`event` | `src/security/audit_logger.py:373-447` |
| 예산·동시성 | sre_agent 기본 timeout 300 · 동시 2 · dedup·시간당 예산 **None**(꺼짐) · 워치독 600초 · 위반은 `rejected` | `sre_agent/settings.py:109-116` · D-123 · D-213 |

---

## 2. 설계

### 2.1 A. 판정(게이트웨이 소유 · 본체는 표시)

**A-1 게이트웨이 domain — `signals.py`에 순수 함수 `judge_row_values(row, th)` 신설**
- 입력: 행 하나(중립 키 → 수치). 출력: `ValueJudgment{metric, value, threshold, comparator, state, level, inputs[], basis, threshold_source}` 목록.
- 대상(1차): `heap_usage_ratio`(행에 있으면 그 값 · 없으면 `heap_used_mb ÷ heap_committed_mb`) · `gc_time_usage_pct` · `db_pool_usage_ratio`(같은 방식) · `error_rate`(호출 수 ≥ `error_min_calls`일 때만 — 행 칼럼은 W0 확인). 기존 `judge_*`의 임계·수준을 그대로 쓴다 — 새 임계를 만들지 않는다(G-2).
- `state`: `exceeded`(기준 초과) · `within`(기준 이내) · 입력 부족이면 판정 항목 없음.
- `level`: 초과일 때만 — 그 신호 종류의 기존 수준(WARNING → 「주의」 · CRITICAL → 「위험」).
- `basis: "single_point"` — 한 시점 값 비교임을 명시. 게이트웨이 신호(힙 3샘플 연속 등)와 의미가 다르다.
- `threshold_source`: `"was_signatures.yaml · 잠정(J0-L-b 보정 전)"` — 정책 파일 주석에서 읽지 않고 상수로.
- `inputs`: 판정에 쓴 칼럼 키 — 본체가 「클릭한 칼럼이 관련된 판정」만 고르는 데 쓴다(본체에 지표 키 리터럴을 두지 않는다 · D-179).

**A-2 게이트웨이 MCP 도구 `apm_judge_values(rows: list[dict])`**
- 읽기 전용 · 외부 호출 0 · `run_tool` 패턴(`gateway_health` 선례 `interface/server.py:1233-1244`) · 행 상한 50 · 수치 아닌 값 무시.
- 반환 `{judgments: [[...행별 ValueJudgment]], thresholds_version}`.
- 테스트: `apm_gateway/tests/test_server.py`의 `EXPECTED_TOOLS` 갱신 · `test_signals.py` 판정 단위 · `tests/test_boundary.py` 무변.
- **LLM 도구 노출 여부**(W0): 본체 사다리 1·2단이 게이트웨이 도구 목록을 LLM에 그대로 노출하면 이 도구가 질의 계획에 끼어들 수 있다 → 노출 경로가 있으면 레지스트리 `views`에 넣지 않고 제외 목록에 둔다.

**A-3 본체 API — `POST /api/v1/query/{query_id}/judge {table, column, row}`**(`result_viz.py`)
- `_explain_table`와 같은 인가·실존 검증(공용 헬퍼 재사용) → `judgment_enabled` 확인.
- APM 결과가 아니면(`is_apm` 거짓) → `{judgments: [], reason: "판정 기준 없음 — 1차는 APM 런타임 지표만"}`.
- 그 행에서 **수치 칼럼만** 골라 게이트웨이에 보낸다(문자열·호스트명은 보내지 않음). 세션은 `open_source_session`(`src/clients/source_mcp_client.py`) · 엔드포인트는 저장된 `apm_source_scope` → 없으면 기본 apm 소스 · 타임아웃 5초.
- 응답에서 `column ∈ inputs`인 판정만 남긴다. 클릭한 칼럼과 무관하면 `reason: "이 칼럼은 판정 대상이 아님"`.
- **게이트웨이 신호 동봉**: 질의 시점 `meta["was_signals"]`를 결과 저장소(`viz_store_fields`)에 함께 싣고(신규 키 · 질의 경로 4곳 대칭), 같은 `hostname`의 신호를 `signals`로 돌려준다 — 지속 판정이 있으면 단일 시점 판정보다 먼저 보인다.
- 평소 범위: 1차는 `baseline: {available: false, reason: "APM 지표는 평소 범위 원천 없음(D-110은 폴스타 CPU·메모리 전용)"}`(G-3).
- 게이트웨이 실패·타임아웃 → `reason`에 사유(침묵 강등 금지) · 160 해석은 영향 없음.
- 캐시 `(query_id, table, row)` LRU 500. 감사 1행 `event="result_judgment"`(칼럼 · 값 sha256 · state · level · threshold_source).
- LLM 호출 0.

**A-4 팝오버** — 셀 클릭 시 `/interpret`와 `/judge`를 **병렬 요청**, 각자 실패해도 다른 블록은 뜬다. 블록 순서: 계산값 → **판정**(「참고용」 배지 · `힙 사용률 0.93 ≥ 기준 0.90 → 기준 초과(주의)` · 출처 · 「단일 시점 비교」 주석 · 게이트웨이 신호가 있으면 그 줄을 위에) → 평소 범위 줄 → LLM 해석 → 「원인 조사」 버튼. 판정 문장은 **코드 문자열**이라 160의 LLM 어휘 가드와 무관하다.

### 2.2 B. 원인 조사(sre_agent 위임)

**B-1 접수 — `POST /api/v1/query/{query_id}/investigate {table, column, row}`**
1. `investigate_enabled` · sre_agent 클라이언트 구성(`build_sre_agent_client`가 None이면 503 + 사유).
2. `_owned_result` + 표·칼럼·행 실존(A-3과 같은 헬퍼).
3. **호스트 인가** `authorize_host_investigation`(G-5 · D-170 R-9 · 기본 `admin_only`). 거부도 감사.
4. 대상 추출(순수 함수 `src/domain/result_investigation.py`): 행의 호스트 칼럼 → `hostname` · 시간 칼럼(160 `chart_rows`의 시간 판정 재사용) → `reference_time`(ISO) · 없으면 결과 저장 시각 · `lookback_minutes` = 설정(기본 30). 호스트가 없으면 400 「조사 대상 호스트를 결과에서 찾지 못했습니다」.
5. 질문 문장은 **코드 템플릿**(`src/prompts/result_investigate.py`) — 칼럼 사전 라벨 · 값·단위 · A의 판정 요약(있으면) · 인스턴스명 · 시각. 사용자 자유 텍스트 없음(주입 표면 차단).
6. 상한(G-6): 사용자별 진행 중 1건 · 시간당 N건(기본 3) · 같은 `(query_id, table, row)` 재접수는 기존 조사 id 반환. 위반은 429 + 사유.
7. `SreAgentClient`로 `sre_diagnose` **접수만**(대기 없음 — W0에서 클라이언트가 접수·poll을 분리해 부를 수 있는지 확인, 안 되면 접수 메서드 추가) → `rejected`면 사유 그대로.
8. `InvestigationStore.record(kind="diagnosis", alarm_ids=[], owner_sub, query_id, subject={table,column,row,hostname}, status="submitted", source="result_viz")` — 레코드 필드 3개 추가(선택 필드 · 기존 레코드 호환).
9. 감사 `log_investigation(entry_point="result_viz", backend="sre_agent", targets=[hostname], outcome=…)` — 성공·거부·상한·실패 모두.
10. 응답 `{investigation_id, status}`.

**B-2 조회** — `GET /api/v1/query/{query_id}/investigations`(그 결과의 조사 목록) · `GET /api/v1/query/investigations/{investigation_id}`(단건 · 지연 poll)
- 인가: **소유자(`owner_sub`) 또는 관리자** — 알람 존 판정(`_assert_investigation_zone`)은 쓰지 않는다(G-7 · 알람 없는 레코드는 그 함수에서 403).
- 지연 poll·잠금·종결 기록은 `alarm.py`의 `_refresh_investigation`·`_INVESTIGATION_POLL_*`를 **공용 모듈로 추출**해 두 라우트가 쓴다(G-10 · 동작 불변 · 기존 알람 테스트로 보증).
- 마스킹은 `_investigation_mask_fn`·`investigation_view(…, operator_view)` 그대로.

### 2.3 C. 조사 작업 카드(프런트)

- 접수 성공 → 팝오버 닫기 → 그 답 말풍선 아래 `.result-investigation-card` 추가(제목 「원인 조사 · HolmesGPT」 · 대상 호스트·지표·값 · 상태 칩).
- 재조회: `BriefingView.createPoller`(15초 · 상한 = 설정 `investigation_followup_timeout_seconds` 300 → 넘으면 「아직 진행 중 — 나중에 다시 확인」 + 수동 새로고침) · 카드가 DOM에서 빠지면 정지 · 401 로그인 · 403/404 「사라짐」(plans/134 카드 규칙).
- 완료: `BriefingView.renderBody`로 브리핑·판정 렌더 + 상단 고정 문구 **「가설 · 참고용 — 자동 조사 결과이며 확정 원인이 아닙니다」**. `stub`·`rejected`·`timeout`·`failed`는 사유 문구.
- 복원(G-8): `ResultViz.attach` 시 `investigate_enabled`면 `GET …/investigations`로 그 결과의 조사 카드를 다시 붙인다(저장소 상한 안에서).
- 버튼 노출: 서버가 판정 응답에 `can_investigate`(플래그·클라이언트 구성·호스트 인가 사전 판정)를 실어 준다 — 안 되는 사용자에겐 버튼을 숨기지 않고 비활성 + 사유 툴팁(「관리자만 조사를 요청할 수 있습니다」).

### 2.4 플래그 · 설정

`ResultVizConfig`에 추가(D-162 기본 off · 기동 시 1회):

| 키 | 기본 | 의미 |
|---|---|---|
| `judgment_enabled` | false | `/judge` · 판정 블록. `explain_enabled` 필요 |
| `investigate_enabled` | false | `/investigate`·조회 2종 · 버튼 · 카드. `explain_enabled` 필요 + `NOISE_INVESTIGATION_SERVICE_URL` |
| `investigate_user_hourly_limit` | 3 | 사용자별 시간당 접수 상한 |
| `investigate_lookback_minutes` | 30 | `sre_diagnose` lookback |

둘 다 off면 현행과 비트 동일(라우트 미등록 · 응답 필드 없음 · 저장소 신규 키 미기록). 게이트웨이 도구 `apm_judge_values`는 플래그 없음 — 읽기 전용 순수 계산이고 호출처가 플래그 뒤에 있다(LLM 노출은 A-2에서 차단).

### 2.5 계층 배치(arch_check · 게이트웨이 경계)

| 파일 | 계층 |
|---|---|
| `apm_gateway/apm_gateway/domain/signals.py`(+`judge_row_values`) | 게이트웨이 domain(stdlib만) |
| `apm_gateway/apm_gateway/application/tools.py` · `interface/server.py`(+도구) | 게이트웨이 application·interface |
| `src/domain/result_investigation.py`(대상 추출 · 순수) | domain |
| `src/prompts/result_investigate.py`(질문 템플릿) | prompts |
| `src/api/routes/result_viz.py`(+3 라우트) · `src/api/investigation_poll.py`(공용 지연 poll — 추출) | interface |
| `noise_gate/infrastructure/investigation_store.py`(+선택 필드) | noise_gate infrastructure(`src → noise_gate` 허용 · D-048) |

본체 ↔ 게이트웨이 import 0 유지(MCP 호출만 · D-274 ③). 본체에 APM 지표 키·임계 리터럴 없음(D-274 ⑤ · D-179).

---

## 3. 게이트 — 확정(2026-10-14 · 사용자 「권고에 맞게 정리하라」 → 권고안 전건)

| # | 질문 | 선택지 | 확정 | 근거 |
|---|---|---|---|---|
| **G-1** | 판정을 어디서 하나 | (a) 게이트웨이 신규 도구 `apm_judge_values`가 판정 (b) 게이트웨이는 임계만 주고 본체가 비교 (c) 행 판정 없이 기존 `was_signals`만 표시 | **(a)** | D-274 ⑤ 「WAS 판정은 게이트웨이 domain 한 곳 · 소비자 재구현 금지」. (b)는 비교 로직이 본체로 새고, (c)는 클릭한 값에 대한 답이 아니다 |
| **G-2** | 판정 단계·어휘 | (a) 「기준 초과(주의/위험 — 신호 종류의 기존 수준)」·「기준 이내」·「판정 기준 없음」 — 새 임계 없음 (b) 2단계 임계 신설(예: 주의 = 임계 × 0.9) (c) 「정상/주의/위험」 어휘 | **(a)** | 운영 보정값조차 없는 잠정 임계 위에 단계를 더 지어내지 않는다. 「정상」은 임계 이내라는 뜻 이상을 함의해 운영 조치 오판 위험 |
| **G-3** | 평소 범위(1차) | (a) 「평소 범위 없음(APM 원천 없음)」 고지만 · APM baseline은 후속 (b) 결과 안 추세(30분)로 간이 범위 | **(a)** | D-110 baseline은 3일 이력 전제. 30분 창은 「평소」가 아니다. APM 이력 창 상한(J0-L-b) 확인이 먼저 |
| **G-4** | 원인 조사 입력 | (a) `sre_diagnose` 재사용(계약 변경 0) (b) 합성 알람으로 `sre_investigate_alarm` (c) sre_agent 신규 도구 | **(a)** | 알람 없는 진단 입력이 이미 있다. (b)는 가짜 알람이 fingerprint·통계를 오염시킨다 |
| **G-5** | 「원인 조사」 권한 | (a) `authorize_host_investigation` 그대로(기본 `admin_only`) (b) 결과 소유자면 누구나 | **(a)** | D-170 R-9 「조회 권한 ≠ 조사 권한」 · 정책 한 곳(`host_authz.py`). 넓히려면 `HOST_AUTHZ_MODE` 값으로 |
| **G-6** | 비용·예산(D-325 ⑩의 제외 사유) | (a) 사용자별 진행 중 1 · 시간당 3 · 같은 행 재접수 = 기존 id + sre_agent dispatcher 상한(동시 2) 그대로 + 운영 `hourly_budget` 설정 권고 (b) 본체 상한 없음 | **(a)** | 수동 조사는 사람 클릭 수만큼 LLM 비용이 난다. sre_agent 쪽 시간당 예산이 기본 꺼짐이라 본체 상한이 1차 방어 |
| **G-7** | 저장·조회 인가 | (a) `InvestigationStore`에 `kind=diagnosis`·`owner_sub`·`query_id` 추가 · 조회는 소유자/관리자 (b) 별도 저장소 | **(a)** | 화면·마스킹·poll이 한 저장소를 쓴다. 알람 존 판정은 알람 없는 레코드에 맞지 않는다 |
| **G-8** | 카드 지속 | (a) 대화 다시 열면 `query_id`로 재조회해 복원 (b) 화면 세션 동안만 | **(a)** | 조사는 수 분 걸린다 — 새로고침 한 번에 결과를 잃으면 재접수(비용)로 이어진다 |
| **G-9** | 폴스타 서버 지표(2차) | (a) 이 계획 범위 밖 → 후속 계획(`cmm_alarm_def_c_con` 임계 · 운영 DB2 구조 · 존 RBAC) (b) 이 계획 Wave에 포함 | **(a)** | 인터뷰 ① 「APM 먼저」. 임계 테이블이 허용 목록 밖이고 운영 구조 미확인 |
| **G-10** | 지연 poll 코드 | (a) `alarm.py`에서 공용 모듈로 추출해 공유 (b) `result_viz.py`에 복제 | **(a)** | 같은 잠금·간격·종결 규칙을 두 벌로 두면 어긋난다. 추출은 동작 불변 · 기존 알람 테스트가 보증 |

---

## 4. 작업 분해(Wave)

| Wave | 범위 | 산출 | 검증 |
|---|---|---|---|
| **W0** | 실측 재대조 | `file:line` 갱신 · `/interpret`의 평탄화 행에 `hostname`·시간 칼럼이 남는지(runtime·pool 결과 픽스처) · `error_rate` 행 칼럼 실재 · `SreAgentClient` 접수·poll 분리 호출 가능 여부 · 사다리 1·2단이 게이트웨이 도구를 LLM에 노출하는 경로 · `log_investigation` entry_point 제약 · capabilities 응답 위치 | 계획서 §1 갱신 |
| **W1** | A-1·A-2 게이트웨이 | `judge_row_values` + `apm_judge_values` + 테스트(초과·이내·경계값·분모 0·입력 부족·수치 아님·행 상한·`inputs`) · `EXPECTED_TOOLS` | `cd apm_gateway && ../.venv/bin/python -m pytest` 자기 파일 + `test_server.py`·`test_boundary.py` |
| **W2** | A-3 본체 판정 | `ResultVizConfig` 4키 · `was_signals` 저장(4경로 대칭) · `POST …/judge` · 감사 · 캐시 | 가짜 MCP 세션: 플래그 off 비트 동일 · 비 APM 사유 · 무관 칼럼 사유 · 게이트웨이 실패 사유 · 소유자 403·관리자 통과 · 수치 칼럼만 송신 · LLM 호출 0 · **`[웨이브 회귀 필요]`**(config) |
| **W3** | B 백엔드 | 지연 poll 공용 추출(G-10) · 저장소 선택 필드 · `result_investigation.py`·질문 템플릿 · `POST …/investigate` · 조회 2종 · 인가·상한·감사 | 가짜 `SreAgentClient`: 인가 거부 감사 · 상한 429 · 재접수 같은 id · 호스트 없음 400 · rejected 사유 · 소유자 외 403 · 마스킹 · **기존 알람 조사 테스트 무변**(`tests/test_api/` alarm 조사) |
| **W4** | A-4·C 프런트 | 팝오버 판정 블록 · 병렬 요청 · 버튼(비활성 사유) · 조사 카드 · 복원 · CSS(테마 토큰) | 실 브라우저: 힙 추세 결과 셀 → 판정 블록(초과/이내) · 비 APM 결과 사유 · 버튼 → 카드 진행 → 완료(가짜 sre_agent) · 새로고침 복원 · 다크 모드 |
| **W5** | 마무리 | 매뉴얼(D-255 — 사용자 U-59 개정 + 「값 판정·원인 조사」 절 · 관리자 플래그·상한·`HOST_AUTHZ_MODE` 절) · D-330 구현 부기 · D-327 부기(④ 유지 · 판정은 별도 블록) · D-325 부기(⑩과 별개 진입점 · 예산 정책 G-6) · INDEX | `pytest tests/test_manual` · `regress.py --base <시작 SHA> --files …` |

실 LLM: 본체 신규 프롬프트 없음(판정 LLM 0 · 질문은 코드 템플릿). sre_agent 종단은 두 평면 `mlx` 확인 시 대표 1건 1회만(선택 · D-127·D-240).

---

## 5. 수용 기준

1. 「지난 6시간 힙 메모리 추세 보여줘」 결과에서 힙 사용률(또는 힙 사용량) 값을 누르면 판정 블록이 뜬다: 기준값·비교·「기준 초과(주의)」 또는 「기준 이내」·기준 출처(잠정)·「단일 시점 비교」·「참고용」. LLM은 호출되지 않는다.
2. 같은 호스트에 게이트웨이 지속 신호가 있으면 판정 블록 위에 그 줄이 보인다.
3. 판정 대상이 아닌 칼럼·비 APM 결과·게이트웨이 장애에서는 각각 사유 문구가 보이고, 160 값 해석은 그대로 뜬다.
4. 평소 범위 줄은 「없음(사유)」으로 보인다(빈칸 아님).
5. 관리자가 「원인 조사」를 누르면 팝오버가 닫히고 답 아래 조사 카드가 「진행 중」으로 붙는다. 완료되면 브리핑이 「가설 · 참고용」 문구와 함께 보인다. 새로고침 뒤에도 카드가 복원된다.
6. 일반 사용자(`admin_only`)에겐 버튼이 비활성 + 사유로 보이고, API 직접 호출은 403 + 감사 1행.
7. 같은 행 재클릭은 새 조사를 만들지 않는다. 시간당 상한을 넘으면 사유가 보인다.
8. 다른 사용자의 조사는 조회되지 않는다(관리자 제외).
9. 플래그 off에서 응답·화면·저장소가 현행과 동일하다. 기존 알람 조사 화면·API 동작이 바뀌지 않는다.
10. 본체 코드에 APM 지표 키·임계 리터럴이 없다(`overfit_check` · 게이트웨이 경계 테스트 통과).

## 6. 위험

| 위험 | 대응 |
|---|---|
| 「기준 초과」가 운영 조치로 바로 이어짐 | 「참고용」·잠정 출처·단일 시점 주석 상시 표시 · 지속 신호를 먼저 · 감사 기록(인터뷰 ⑤) |
| 잠정 임계가 운영과 안 맞음 | 출처에 「잠정(J0-L-b 보정 전)」 명시 · 보정은 게이트웨이 정책 파일만 바꾸면 반영(범위 밖 · §7) |
| 단일 시점 판정과 게이트웨이 지속 신호가 엇갈림 | 둘 다 보이고 의미 차이를 문구로 고정(`basis`) |
| 수동 조사 비용 폭주 | G-6 상한 + 재접수 dedup + 감사 · 운영 `hourly_budget` 설정 권고를 관리자 매뉴얼에 |
| 신규 게이트웨이 도구가 LLM 질의 계획에 끼어듦 | W0에서 노출 경로 실측 → 레지스트리 `views` 미등재 · 제외 목록 |
| 지연 poll 추출이 알람 화면을 깸 | 동작 불변 추출 · 기존 알람 조사 테스트 무변을 W3 통과 조건으로 |
| 조사가 300초를 넘어 카드가 멈춤 | 상한 도달 문구 + 수동 새로고침 · 워치독(600초) 이후 `failed(stuck_watchdog)` 표시 |
| 브리핑 서술 속 호스트명·IP 미마스킹(plans/153 한계) | 일반 사용자는 버튼 자체가 비활성(G-5) · 운영자 뷰만 원문 |
| 결과 저장소 만료(인메모리 1000) | 판정·접수는 160과 같은 만료 문구. 조사 카드는 저장소(JSONL)에서 복원되므로 결과 만료와 무관 |

## 7. 범위 밖

- `plans/160`의 그래프·용어 사전·값 해석 자체(160 소관 · LLM 문장 금지 D-327 ④ 유지)
- 임계치 정책 변경(게이트웨이 `was_signatures.yaml` 운영값 보정 — J0-L-b)
- 폴스타 서버 지표 판정(2차 · G-9 — `cmm_alarm_def_c_con` 임계 · 운영 DB2 구조 확인 · 존 RBAC)
- APM 지표 평소 범위(동적 baseline) — APM 이력 창 상한 확인 뒤 후속
- 알람 관제 드로어의 수동 조사 버튼(D-325 ⑩ — 별개 진입점)

## 8. 인터뷰 확정(2026-10-13 · v1.1)

| # | 질문 | 확정 |
|---|---|---|
| 1 | 판정 지표 범위 | **APM 런타임(힙·GC·DB 풀 등) 먼저**, 폴스타 서버 지표(CPU·메모리·디스크)는 2차 |
| 2 | 기준 우선순위 | **고정 임계 우선**. 평소 범위(동적 baseline)는 보조 줄, 두 기준이 다르면 **둘 다 기준 출처와 함께**. 기준 없는 지표는 「판정 기준 없음」 |
| 3 | 원인 추정 진입 | 값 팝오버의 「원인 조사」는 **`sre_agent` 조사 위임**. 본체에 별도 원인 추정 경로를 만들지 않는다 |
| 4 | 응답 표시 | 팝오버는 접수 후 닫고, **대화 아래 조사 작업 카드**(진행→완료) |
| 5 | 책임 표시 | 판정마다 **「참고용」 + 기준 출처·값**, 판정·조사 접수는 **감사 로그**, 원인은 **「가설」로만** |

## 9. 개정 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| v1.0 | 2026-10-13 | 최초 — 범위 정리 · 기준 원천 후보 · 미결 질문 5 |
| v1.1 | 2026-10-13 | 인터뷰 5문 확정(§8) |
| v1.2 | 2026-10-14 | 현행 구현 실측 3갈래(160 값 해석 · 판정 기준 원천 · sre_agent 위임 경로) · 정정 2건(D-079 → **D-110** · 폴스타 임계는 `cmm_alarm_def_c_con`에 실재) · 설계 §2(판정 = 게이트웨이 `apm_judge_values` · 원인 = `sre_diagnose` 위임 · 조사 카드) · 게이트 G-1~G-10 권고안 · Wave W0~W5 · 수용 기준 10 · 코드 0 |
| v1.3 | 2026-10-14 | 게이트 G-1~G-10 권고안 전건 확정(사용자 「권고에 맞게 정리하라」) · D-330 등재 · 상태 「계획 확정 · 착수 가능」 · 코드 0 |
