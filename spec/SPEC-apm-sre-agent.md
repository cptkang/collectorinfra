# SPEC: apm-sre — `sre_agent` 제니퍼 소비측 (`plans/87` J3 · J8)

> 맵 `spec/CAPABILITY-MAP-87.md` · 생산측 계약 `spec/SPEC-apm-gateway.md` §3·§4 · 계획서 `plans/87` §5.4 · §0.6 #24~#32 · **§0.13 (3)** · 결정 D-195 ② · D-274 ⑤⑦ · D-233 · **D-287 ②**
> **[J8 · 2026-09-30]** 게이트웨이가 제니퍼 소스 N개를 묶는다. 엔드포인트(`APM_MCP_URL`)는 하나 그대로이고 도구 이름도 그대로다(도구가 자동 발견되므로 새 인자 `source_ids`·`source_id`는 스키마로 보인다). 바뀐 것은 지침 문구와 힌트 한 줄뿐이다.

## 1. 범위

`sre_agent/` 안에서만 바꾼다. `apm_gateway`를 import하지 않는다(MCP 계약만 — 픽스처는 이 SPEC을 보고 복제).

## 2. 설정 (`AgentSettings` — `sre_agent/.env` · 접두 없음)

| 키 | 기본 | 뜻 |
|---|---|---|
| `APM_MCP_URL` · `APM_MCP_TOKEN` | 빈 값 · 빈 값 | 게이트웨이 SSE URL(예: `http://127.0.0.1:9096/sse`) · Bearer |
| `APM_GUIDANCE_ENABLED` | false | APM 지침·앵커·플레이북·정체 가드 |
| `APM_SIGNATURES_ENABLED` | false | 도구 출력의 `was_signals`를 `Signal`로 승격 |

## 3. 동작

1. **두 번째 MCP 서버** — `_build_mcp_servers()`: `APM_MCP_URL`이 있으면 `"apm": {"config": {"mode": "sse", "url": …, "health_check_tool": "gateway_health", headers(Bearer)}}`를 더한다. 폴스타 URL 없이 APM만 있어도 등록한다. **미설정이면 결과가 종전 dict와 같다(비트 동일).**
2. **kind 선판정(R-16)** — `classify_alarm_kind`: `resourceType == "apm.Instance"`(대소문자 무시)면 OS 키워드보다 **먼저** `"apm"`을 돌려준다. 플래그와 무관(게이트웨이 이벤트에서만 발현 — 폴스타 이벤트 판정 불변). 결과: OS 플레이북이 WAS 사건에 주입되지 않는다.
3. **지침**(`APM_GUIDANCE_ENABLED`일 때만 — 끄면 조립 문자열 바이트 동일)
   - 사건창 앵커 도구에 `apm_app_health`·`apm_runtime_health`·`apm_events`·`apm_slow_transactions` 추가.
   - `APM_FOCUS_NOTE`(조사 순서): ① `apm_instance_map` → ② `apm_events` → ③ `apm_app_health`·`apm_runtime_health` → ④ 증상별(큐잉 `apm_active_services` · 지연 `apm_slow_transactions` → `apm_transaction_profile`(앞 도구가 준 `profile_ref`의 **`source_id`**·`domain_id`·`txid`·`time_ms`를 그대로 — J8: 소스가 둘 이상이면 게이트웨이가 `source_id`를 요구한다) · 풀 `apm_resource_pool`) → ⑤ 인프라 대조(`polestar_metric_trend`·`prom_metric_range`) · 반증 도구 1회 · `apm_*`에 `investigation_id` 인자를 넣는다.
   - **[134 W7 · 2026-10-06]** 사건창 앵커에 구간 도구 `apm_status_stats`·`apm_metrics`(series만 창이 의미 있음 — catalog는 인자를 무시할 뿐 오류 아님)·`apm_transaction_trace`를 더해 7종. **변경 탐색 2종(`apm_source_changes`·`apm_change_impact`)은 앵커에 넣지 않는다** — 앵커 lookback(기본 120분)을 넘기면 게이트웨이 탐색 창(기준시각 끝 24시간 · `CHANGES_DEFAULT_MINUTES`)이 줄어 사건보다 몇 시간 앞선 배포를 놓친다. 대신 ⑥에 「reference_time=사건 기준시각만 넘긴다 — lookback을 비우면 탐색 24시간」을 적는다. `apm_period_compare`는 절대 구간 4개를 받아 앵커 대상이 아니다.
   - **[134 W7]** 조사 순서 ⑥(근거가 더 필요할 때 맞는 도구만): GUID 연계 `apm_transaction_trace`(앞 결과의 guid) · 변경 감지·전후 `apm_source_changes`·`apm_change_impact` · 평소 대비 `apm_period_compare`(current_*=사건 구간 · baseline_*=평소 구간 · ISO 절대 시각) · 실행 중 요청 상세 `apm_active_detail`(`apm_active_services`의 `active_ref` 그대로) · 설정·룰·색상 경계·PID→인스턴스·데이터 서버 `apm_config`(kind) · JVM 옵션·환경변수 `apm_environment`(비밀 값은 가려져 온다) — `apm_config`·`apm_environment`는 **조회 시점의 설정**(사건 뒤에 바뀌었을 수 있다 · 변경 감지로 확인). `apm_users`는 선조회하지 않는다(계정·권한 문제가 의심될 때만). `apm_transaction_profile`은 조사당 프로파일 호출 상한(기본 5회 — 넘으면 `rate_limited`) 안에서 가장 의심되는 거래부터 고른다(D-296 ④ 예산 유지 — 종전 노트에 숫자 문구가 없어 신설). 켜짐 렌더는 +3줄(대표 조합 2,595자 → 3,310자).
   - 실시간 전용 노트: `apm_active_services`·`apm_resource_pool`·`apm_active_detail`(134 W7)은 현재값 — 과거 사건의 증거로 서술하지 않는다(`OPENMETRICS_NOTE` 전례).
   - **폴백 노트**: `apm_*`가 없거나 오류(`source_unavailable`·`instance_unresolved`·`not_configured`)면 폴스타 MCP 도구(프로세스·OS 구성·메트릭 추세)로 대체하고 브리핑 `[한계]`에 사유를 적는다(D-233 — 셸 없음).
   - kind `"apm"`(또는 트리거 `meta.hints.solution == "apm"`)이면 APM 플레이북 1개만 주입한다(OS 플레이북 대신).
   - **정체 가드(P15)**: 같은 도구·같은 인자 호출이 3회 이상이면 조사를 "미결"로 표기하고 `[한계]`에 사유(가능한 범위 — 사후 판정 허용).
4. **판정 승격**(`APM_SIGNATURES_ENABLED`일 때만) — 도구 원시 출력 중 `source_kind == "apm_api"` JSON의 `was_signals`를 `Signal(name=kind, category, source="apm", label, evidence)`로 바꿔 기존 escalate-only 판정에 합친다. **WAS 규칙을 재구현하지 않는다.** 끄면 판정 결과 불변.
5. **권고 표** — `_CANDIDATES_BY_SIGNATURE`에 WAS kind 추가(가역성 순 · 계획서 §5.4(c)). 항목마다 **검증 방법**·**롤백**을 담을 수 있게 확장하되 기존 OS 항목의 렌더 문자열은 바이트 동일. 조치 문구는 서술이지 명령이 아니다(경계 테스트의 명령 리터럴 차단 유지).
6. **브리핑** — 증거에 소스 라벨 "애플리케이션(APM)"(apm_* 인용) · "인프라"(그 밖)를 구분 · `[한계]`에 정합 신뢰도(`medium` 이하)·APM 미가용 폴백·1분 창 상한 도달(도구 `limits`)을 싣는다. 인용 판정(`tool_names`)은 배선 변경 없음(§0.6 #30).
7. **트리거 힌트** — `payload["meta"]["hints"]`(있을 때만): `{"solution": "apm", "source_id", "instance_id", "domain_id", "event_type", "txid"}` — 플레이북 선택 입력. 지침의 힌트 한 줄은 `event_type · source_id · instance_id · domain_id · txid` 순서로 값이 있는 키만 싣는다(J8 — `source_id` 추가 · 값이 없으면 종전 줄과 같다).

## 4. 수용 기준

- `APM_MCP_URL` 미설정 → `_build_mcp_servers()` 종전과 동일 · 설정 시 서버 2개(또는 APM 단독 1개).
- 플래그 둘 다 off → 지침 문자열·판정·권고·브리핑 바이트 동일(기존 테스트 무회귀 + 명시 단언).
- R-16: §2.3 제니퍼 유형 표본 전부(`ERROR_`/`WARNING_` 접두 유무 둘 다)가 `resourceType="apm.Instance"`에서 OS kind로 분류되지 않는다 — 조사측 분류기.
- 목업 WAS 시나리오 6종(큐잉·DB 풀·GC stall·힙·슬로우 SQL·외부 지연): 게이트웨이 도구 출력 픽스처(`was_signals` 포함) → 승격 신호 kind · 권고 후보가 결정적으로 맞는다(LLM 0회).
- 브리핑에 APM 증거가 인용되면 "애플리케이션(APM)" 라벨로 판정된다 · APM 미가용이면 폴백 사유가 지침·`[한계]`에 나온다.
- 정체 가드 — 같은 도구·인자 3회 → "미결" 표기 테스트.
- **[J8]** 지침 문구에 `profile_ref`의 `source_id` · 힌트 줄에 `source_id=…`(없으면 종전 줄) · `APM_GUIDANCE_ENABLED` off 바이트 불변(`sre_agent/tests/test_plan87_j8_source_id.py`).
- **[134 W7]** 앵커 튜플(기존 4종이 앞 · 새 3종) · 변경 탐색 2종 앵커 제외와 「reference_time만」 문구 · ⑥ 도구 문구 · 계정 선조회 금지 · 예산 문구 · 켜짐 렌더 sha256 고정 · 꺼짐 해시 `da3ea4f`와 동일(`sre_agent/tests/test_plan134_w7_guidance.py`).
- `cd sre_agent && .venv/bin/python -m pytest tests -q` 신규 실패 0(기준선 대조).
