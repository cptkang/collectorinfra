# SPEC: apm-noise — `noise_gate` 제니퍼 소비측 (`plans/87` J4 · J8)

> 맵 `spec/CAPABILITY-MAP-87.md` · 생산측 계약 `spec/SPEC-apm-gateway.md` §3·§5 · 계획서 `plans/87` §5.5 · §0.6 #28·#33~#36 · **§0.13 (3)** · 결정 D-195 ② · D-274 ④⑤⑦ · **D-287 ④**
> **[J8 · 2026-09-30]** 제니퍼 소스가 여럿이다(은행존·공동존·레거시 — 게이트웨이 1개가 묶는다). 소스 ↔ 존 정본은 루트 레지스트리 `config/db_registry.yaml` `solutions[apm].sources[]`(`{id, label, zone}`)이고, `noise_gate`는 레지스트리 조회(`src.routing.registry` — 기존 역방향 의존 범위)로 존을 푼다. 게이트웨이는 존을 모른다.

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
2. **소스 배지** — `dbId == "jennifer"` · **`dbId`가 `jennifer_`로 시작**(J8 — `jennifer_<source_id>`) · 또는 `raw_payload.source == "jennifer"`인 이벤트의 `ServerIdentity.source_label`이 **"제니퍼"**. 레지스트리 DB가 아니므로 `source_labels_for(db_id)` 앞에 분기(121 TP-9.2 "solutions 전용 family" 전까지 로컬 상수 — 레지스트리 등재는 J5).
   - **[J8] 존·툴팁** — `zone_labels_for(dbId)`가 DB 항목이 없으면 레지스트리 `alarm_source(dbId)`(`{family}_{소스 id}` → 소스)의 존을 푼다(사이트 라벨은 빈 값). 툴팁 상세(`source_detail`) = `"제니퍼 — {존 약칭} {apm.domain_name}; {dbId}"`(있는 조각만) — 예 `제니퍼 — 은행존; jennifer_bank` · `제니퍼 — 은행존 운영도메인; jennifer_legacy`. 존 없는 소스(단일 설정 `jennifer` · 레지스트리에 없는 소스 id)는 v4 문자열 그대로(`제니퍼 — 운영도메인; jennifer`). 레지스트리에 없는 소스 id는 레지스트리가 id별 경고 1회를 남긴다.
3. **트리거 힌트** — `build_trigger_payload`: `resource_type == "apm.Instance"`일 때만 `meta["hints"] = {"solution": "apm", "source_id", "instance_id", "domain_id", "event_type", "txid"}`(값은 `raw_payload["apm"]`에서 · `source_id`는 J8 — 원문에 없으면 None · 키 집합 고정). 그 밖의 이벤트는 페이로드 바이트 동일.
4. **`app_impact` 승격**(`NOISE_APP_IMPACT_ENABLED` + URL일 때만) — 폴스타 알람(비 apm)에 대해 게이트웨이 `apm_events(hostname, reference_time=<alarm_time ISO>, lookback_minutes=<window>, level="fatal", investigation_id=<alarm_id>[, source_ids=<알람 존의 소스>])`를 MCP로 호출. **[J8] 존 좁히기** — `apm_source_ids_for(dbId)`: 알람 DB의 레지스트리 존 → `solutions[apm].sources[]` 중 같은 존 id(선언 순서 · 예 `polestar_b0` → `["bank","legacy"]` · `polestar_cm_gp` → `["common"]`). 존이 없거나 그 존에 소스가 없으면 `source_ids`를 넣지 않는다(전 소스 = 종전 호출과 같은 인자). 게이트웨이가 모르는 id라고 답하면(`invalid_argument` — 레지스트리와 게이트웨이 `JENNIFER_SOURCES` 불일치) 아래 오류 경로(판정 불변 + 사유)로 가고 **전 소스로 다시 부르지 않는다**(다른 존 승격 방지 · 침묵 폴백 금지). `rows`가 있으면 `app_impact = {"source": "jennifer", "fatal_events": n, "event_types": [...], "was_signals": [...]}`이고 **승격만** 한다(DASHBOARD→PAGE 등 — 억제 해제·강등 없음 · 심각도 3 불변). 호출 실패·오류 응답은 `app_impact = None` + 사유 로그/감사(침묵 금지) · 판정 불변. 클라이언트는 `noise_gate/infrastructure/sre_agent_client.py` 전례(SSE · Bearer · 타임아웃).

## 4. 수용 기준

- 게이트웨이가 낸 `alarm:raw` 레코드(SPEC-apm-gateway §5 픽스처 복제)가 워커 파싱(`AlarmEvent`)과 조사 트리거 `REQUIRED_EVENT_FIELDS` 검증을 통과한다(import 없이 복제한 계약 픽스처).
- R-16: §2.3 제니퍼 유형 표본 전부(`ERROR_`/`WARNING_` 접두 유무)가 게이트 분류기에서 OS kind로 분류되지 않는다 · 폴스타 이벤트 판정 불변.
- 배지 "제니퍼"(db_id가 아니라 family 해석).
- **[J8]** 툴팁 상세 3종(존 소스 · 도메인 유무 · 단일 설정 바이트 불변) · `apm_source_ids_for` 존 → 소스 · `app_impact` 호출 인자(존 소스만 · 존 없음 = `source_ids` 키 없음) · `invalid_argument` 시 재시도 없음(호출 1회) · 힌트 `source_id` · 클라이언트가 값이 있을 때만 `source_ids`를 싣는다(`noise_gate/tests/test_plan87_j8_multi_source.py`).
- `app_impact`: 승격만 하는 비대칭 테스트 · 심각도 3 불변 · 억제 결정 불변 · 게이트웨이 오류 시 판정 불변 + 사유 기록.
- 플래그 off 비트 동일 — `test_plan60_flags_off_regression.py`에 섹션 추가.
- 알람 화면이 바뀌면(배지 값 추가) 매뉴얼 반영 여부를 판정해 보고(D-255).
- 루트 `pytest`(본체 + noise_gate) 신규 실패 0(기준선 대조).
