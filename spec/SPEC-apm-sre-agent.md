# SPEC: apm-sre — `sre_agent` 제니퍼 소비측 (`plans/87` J3)

> 맵 `spec/CAPABILITY-MAP-87.md` · 생산측 계약 `spec/SPEC-apm-gateway.md` §3·§4 · 계획서 `plans/87` §5.4 · §0.6 #24~#32 · 결정 D-195 ② · D-274 ⑤⑦ · D-233

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
   - `APM_FOCUS_NOTE`(조사 순서): ① `apm_instance_map` → ② `apm_events` → ③ `apm_app_health`·`apm_runtime_health` → ④ 증상별(큐잉 `apm_active_services` · 지연 `apm_slow_transactions` → `apm_transaction_profile`(앞 도구의 `profile_ref`를 그대로) · 풀 `apm_resource_pool`) → ⑤ 인프라 대조(`polestar_metric_trend`·`prom_metric_range`) · 반증 도구 1회 · `apm_*`에 `investigation_id` 인자를 넣는다.
   - 실시간 전용 노트: `apm_active_services`·`apm_resource_pool`은 현재값 — 과거 사건의 증거로 서술하지 않는다(`OPENMETRICS_NOTE` 전례).
   - **폴백 노트**: `apm_*`가 없거나 오류(`source_unavailable`·`instance_unresolved`·`not_configured`)면 폴스타 MCP 도구(프로세스·OS 구성·메트릭 추세)로 대체하고 브리핑 `[한계]`에 사유를 적는다(D-233 — 셸 없음).
   - kind `"apm"`(또는 트리거 `meta.hints.solution == "apm"`)이면 APM 플레이북 1개만 주입한다(OS 플레이북 대신).
   - **정체 가드(P15)**: 같은 도구·같은 인자 호출이 3회 이상이면 조사를 "미결"로 표기하고 `[한계]`에 사유(가능한 범위 — 사후 판정 허용).
4. **판정 승격**(`APM_SIGNATURES_ENABLED`일 때만) — 도구 원시 출력 중 `source_kind == "apm_api"` JSON의 `was_signals`를 `Signal(name=kind, category, source="apm", label, evidence)`로 바꿔 기존 escalate-only 판정에 합친다. **WAS 규칙을 재구현하지 않는다.** 끄면 판정 결과 불변.
5. **권고 표** — `_CANDIDATES_BY_SIGNATURE`에 WAS kind 추가(가역성 순 · 계획서 §5.4(c)). 항목마다 **검증 방법**·**롤백**을 담을 수 있게 확장하되 기존 OS 항목의 렌더 문자열은 바이트 동일. 조치 문구는 서술이지 명령이 아니다(경계 테스트의 명령 리터럴 차단 유지).
6. **브리핑** — 증거에 소스 라벨 "애플리케이션(APM)"(apm_* 인용) · "인프라"(그 밖)를 구분 · `[한계]`에 정합 신뢰도(`medium` 이하)·APM 미가용 폴백·1분 창 상한 도달(도구 `limits`)을 싣는다. 인용 판정(`tool_names`)은 배선 변경 없음(§0.6 #30).
7. **트리거 힌트** — `payload["meta"]["hints"]`(있을 때만): `{"solution": "apm", "instance_id", "domain_id", "event_type", "txid"}` — 플레이북 선택 입력.

## 4. 수용 기준

- `APM_MCP_URL` 미설정 → `_build_mcp_servers()` 종전과 동일 · 설정 시 서버 2개(또는 APM 단독 1개).
- 플래그 둘 다 off → 지침 문자열·판정·권고·브리핑 바이트 동일(기존 테스트 무회귀 + 명시 단언).
- R-16: §2.3 제니퍼 유형 표본 전부(`ERROR_`/`WARNING_` 접두 유무 둘 다)가 `resourceType="apm.Instance"`에서 OS kind로 분류되지 않는다 — 조사측 분류기.
- 목업 WAS 시나리오 6종(큐잉·DB 풀·GC stall·힙·슬로우 SQL·외부 지연): 게이트웨이 도구 출력 픽스처(`was_signals` 포함) → 승격 신호 kind · 권고 후보가 결정적으로 맞는다(LLM 0회).
- 브리핑에 APM 증거가 인용되면 "애플리케이션(APM)" 라벨로 판정된다 · APM 미가용이면 폴백 사유가 지침·`[한계]`에 나온다.
- 정체 가드 — 같은 도구·인자 3회 → "미결" 표기 테스트.
- `cd sre_agent && .venv/bin/python -m pytest tests -q` 신규 실패 0(기준선 대조).
