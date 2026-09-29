# SPEC: apm-noise — `noise_gate` 제니퍼 소비측 (`plans/87` J4)

> 맵 `spec/CAPABILITY-MAP-87.md` · 생산측 계약 `spec/SPEC-apm-gateway.md` §3·§5 · 계획서 `plans/87` §5.5 · §0.6 #28·#33~#36 · 결정 D-195 ② · D-274 ④⑤⑦

## 1. 범위

`noise_gate/`와 설정 필드(`src/config.py` `NoiseGateConfig` — 공유 파일이라 자기 줄만 추가)만 바꾼다. 폴러·정규화는 **게이트웨이에 있다**(여기에 신설하지 않는다). `apm_gateway`를 import하지 않는다.

## 2. 설정 (루트 `.env` · `NoiseGateConfig` `env_prefix="NOISE_"`)

| 키 | 기본 | 뜻 |
|---|---|---|
| `NOISE_APP_IMPACT_ENABLED` | false | `app_impact` 승격 |
| `NOISE_APM_MCP_URL` · `NOISE_APM_MCP_TOKEN` | 빈 값 | 게이트웨이 SSE URL · Bearer(제니퍼 토큰이 아니다) |
| `NOISE_APP_IMPACT_WINDOW_MINUTES` | 10 | 사건창 |

## 3. 동작

1. **kind 선판정(R-16 · U-13 확정안)** — `classify_alarm_kind`: `resource_type == "apm.Instance"`(대소문자 무시)면 OS 키워드보다 먼저 `"apm"`. 결과: OS kind L3 보강 프로파일(`host_diagnostic_collector`)·OS 플레이북이 붙지 않는다. E6 호스트 보강은 **"호스트 참고"로 유지**(교차 증거 — 프로세스 표 등은 참고용 표기). 플래그 무관(게이트웨이 이벤트에서만 발현).
2. **소스 배지** — `dbId == "jennifer"`(또는 `raw_payload.source == "jennifer"`) 이벤트의 `ServerIdentity.source_label`이 **"제니퍼"**. 레지스트리 DB가 아니므로 `source_labels_for(db_id)` 앞에 분기(121 TP-9.2 "solutions 전용 family" 전까지 로컬 상수 — 레지스트리 등재는 J5).
3. **트리거 힌트** — `build_trigger_payload`: `resource_type == "apm.Instance"`일 때만 `meta["hints"] = {"solution": "apm", "instance_id", "domain_id", "event_type", "txid"}`(값은 `raw_payload["apm"]`에서). 그 밖의 이벤트는 페이로드 바이트 동일.
4. **`app_impact` 승격**(`NOISE_APP_IMPACT_ENABLED` + URL일 때만) — 폴스타 알람(비 apm)에 대해 게이트웨이 `apm_events(hostname, reference_time=<alarm_time ISO>, lookback_minutes=<window>, level="fatal", investigation_id=<alarm_id>)`를 MCP로 호출. `rows`가 있으면 `app_impact = {"source": "jennifer", "fatal_events": n, "event_types": [...], "was_signals": [...]}`이고 **승격만** 한다(DASHBOARD→PAGE 등 — 억제 해제·강등 없음 · 심각도 3 불변). 호출 실패·오류 응답은 `app_impact = None` + 사유 로그/감사(침묵 금지) · 판정 불변. 클라이언트는 `noise_gate/infrastructure/sre_agent_client.py` 전례(SSE · Bearer · 타임아웃).

## 4. 수용 기준

- 게이트웨이가 낸 `alarm:raw` 레코드(SPEC-apm-gateway §5 픽스처 복제)가 워커 파싱(`AlarmEvent`)과 조사 트리거 `REQUIRED_EVENT_FIELDS` 검증을 통과한다(import 없이 복제한 계약 픽스처).
- R-16: §2.3 제니퍼 유형 표본 전부(`ERROR_`/`WARNING_` 접두 유무)가 게이트 분류기에서 OS kind로 분류되지 않는다 · 폴스타 이벤트 판정 불변.
- 배지 "제니퍼"(db_id가 아니라 family 해석).
- `app_impact`: 승격만 하는 비대칭 테스트 · 심각도 3 불변 · 억제 결정 불변 · 게이트웨이 오류 시 판정 불변 + 사유 기록.
- 플래그 off 비트 동일 — `test_plan60_flags_off_regression.py`에 섹션 추가.
- 알람 화면이 바뀌면(배지 값 추가) 매뉴얼 반영 여부를 판정해 보고(D-255).
- 루트 `pytest`(본체 + noise_gate) 신규 실패 0(기준선 대조).
