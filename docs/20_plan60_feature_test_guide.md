# 20. Plan 60 노이즈 캔슬링 정밀화 기능 테스트 가이드

> **대상**: Plan 60(`plans/60-noise-cancellation-benchmark-refinement.md`)으로 개발된 기능 전체 — E1~E6 본체(D-106~D-111) + 후속 강화(E2 정밀화 D-112·STL D-113·B-7 임베딩 D-114).
> **선행 문서**: 게이트 공통 인프라·E2E 시나리오 실행법은 `16_plan52_noise_gate_test_guide.md`(이벤트 주입·서버 기동), 임베딩 모델 설치는 `19_embedding_model_install_guide.md`.
> **원칙**: Plan 60 기능은 **전부 옵트인(기본 off)** 이다. off 상태에서 현행 게이트와 비트동일함이 회귀 테스트로 고정되어 있으므로, "켜기 전 회귀 없음 → 켠 후 기능 동작"의 2단계로 검증한다.

---

## 0. Quick Guide — 3분 자동 검증

```bash
cd /path/to/collectorinfra

# ① Plan 60 포함 알람 스위트 전체 (외부 의존 없음 — DB/Redis/LLM 불필요)
python -m pytest tests/test_alarm/ -q
# 합격 기준: 713 passed, 4 skipped (2026-07-23 기준선)
#   - 4 skipped = 실모델 옵트인 테스트(§2.3) — 모델 미설정 시 스킵이 정상

# ② 아키텍처 계층 위반 검사
python scripts/arch_check.py --ci
# 합격 기준: exit 0 (위반 error 0건)

# ③ 플래그 off 회귀 (Plan 60 전 기능 비활성 시 현행 경로 비트동일)
python -m pytest tests/test_alarm/test_plan60_flags_off_regression.py -v
```

세 명령이 모두 통과하면 Plan 60 코드는 회귀 없이 정상이다. 기능별 상세 검증은 §3.

---

## 1. 기능 · 플래그 · 테스트 총괄표

| 기능 | D-번호 | 핵심 옵트인 플래그 (`NOISE_` 접두어) | 자동 테스트 파일 |
|------|--------|--------------------------------------|------------------|
| **E1** 재발생 억제 감사·관측성 | D-106 | (상시 — dedup은 기존 동작) `RECURRENCE_AUDIT_EVERY_N`(샘플링), `REPEAT_INTERVAL_SECONDS`(dedup TTL) | `test_recurrence_dedup.py` |
| **E2** 크로스-호스트 상관 | D-109 | `CROSS_HOST_CORRELATION_ENABLED` (+ `CORRELATION_SIM_THRESHOLD` 0.5, `CORRELATION_WINDOW_SECONDS` 120, `CORRELATION_MIN_CLUSTER_SIZE` 2, `CORRELATION_BUFFER_MAX` 1000) | `test_cross_host_correlation.py` |
| **E2 정밀화** 클러스터 메타 감사 + 위상 가중 | D-112 | `CORRELATION_TOPOLOGY_WEIGHT_ENABLED` (+ `CORRELATION_TOPOLOGY_WEIGHT` 0.2) | `test_cross_host_correlation.py`, `test_noise_gate_graph_integration.py` |
| **E3** 동적 baseline (Holt-Winters) | D-110 | `DYNAMIC_BASELINE_ENABLED` **AND** `ENABLE_AI_SEVERITY_BOOST` (+ `ANOMALY_Z_HIGH` 3.0, `ANOMALY_MIN_PERIODS` 3) | `test_anomaly.py`, `test_metric_baseline.py`, `test_anomaly_severity_guard.py` |
| **E3 2차** STL 분해 | D-113 | `ANOMALY_STL_ENABLED` (statsmodels 필요 — 없으면 HW 폴백) | `test_metric_stl.py`, `test_metric_stl_absence.py` |
| **E4** 토폴로지 다홉 연쇄 억제 | D-107 | `MULTI_HOP_CASCADE_ENABLED` (+ `TOPOLOGY_MAX_HOPS` 5, `TOPOLOGY_CACHE_TTL_SECONDS` 86400) | `test_topology.py`, `test_topology_loader.py`, `test_multi_hop_cascade.py` |
| **E5** 변경 상관 | D-111 | `CHANGE_CORRELATION_ENABLED` (+ `CHANGE_WINDOW_SECONDS` 3600) | `test_change_correlation.py`, `test_change_feed.py` |
| **E6** 통보 컨텍스트 보강 L1 | D-108 | `MESSAGE_ENRICHMENT_ENABLED` | `test_enrichment_profile.py`, `test_message_enrichment.py` |
| **B-7** 임베딩 주석 (L-2/L-4) | D-114 | `SEMANTIC_DEDUP_ANNOTATION_ENABLED`(L-2), `TOPOLOGY_TEXT_FUSION_ENABLED`(L-4) + `EMBEDDING_MODEL_PATH` | `test_embedding_provider.py`, `test_semantic_annotation.py`, `test_embedding_provider_realmodel.py` |
| **공통** 플래그 off 회귀 | — | (전부 off가 기본) | `test_plan60_flags_off_regression.py` |

> `.env` 키는 표의 플래그 앞에 `NOISE_`를 붙인다 (예: `NOISE_MULTI_HOP_CASCADE_ENABLED=true`). **인라인 주석 금지**(프로젝트 규칙).

---

## 2. 자동 테스트 실행

### 2.1 전체 스위트와 합격 기준

```bash
python -m pytest tests/test_alarm/ -q
```

| 항목 | 기준 (2026-07-23) |
|------|-------------------|
| passed | **713** (Plan 52 기존 + Plan 60 전체) |
| skipped | **4** (실모델 옵트인 — §2.3에서만 실행) |
| failed | 0 |

> 참고: `tests/test_alarm_process_enrich.py`·`tests/test_alarm_enricher.py`(스위트 밖, 루트 레벨)의 analyzer fixture 실패 4건은 **Plan 60 이전부터 존재하는 별건**이다(클린 HEAD에서도 동일 실패 — `18_known_mistakes.md` 참조).

### 2.2 기능별 실행

```bash
# E1 재발생 감사
python -m pytest tests/test_alarm/test_recurrence_dedup.py -v
# E2 상관 (+D-112 위상 가중·메타)
python -m pytest tests/test_alarm/test_cross_host_correlation.py -v
# E3 baseline (HW)
python -m pytest tests/test_alarm/test_anomaly.py tests/test_alarm/test_metric_baseline.py tests/test_alarm/test_anomaly_severity_guard.py -v
# E3 STL (statsmodels 미설치면 importorskip 자동 스킵)
python -m pytest tests/test_alarm/test_metric_stl.py tests/test_alarm/test_metric_stl_absence.py -v
# E4 토폴로지·다홉
python -m pytest tests/test_alarm/test_topology.py tests/test_alarm/test_topology_loader.py tests/test_alarm/test_multi_hop_cascade.py -v
# E5 변경 상관
python -m pytest tests/test_alarm/test_change_correlation.py tests/test_alarm/test_change_feed.py -v
# E6 통보 보강
python -m pytest tests/test_alarm/test_enrichment_profile.py tests/test_alarm/test_message_enrichment.py -v
# B-7 임베딩 (fake provider — 모델 불필요)
python -m pytest tests/test_alarm/test_embedding_provider.py tests/test_alarm/test_semantic_annotation.py -v
```

### 2.3 옵트인 테스트 (외부 자산 필요 — CI 자동 스킵)

```bash
# STL 실계산 경로: statsmodels 설치 시에만 의미 (미설치면 skip)
pip install ".[stl]"
python -m pytest tests/test_alarm/test_metric_stl.py -v

# B-7 실모델: multilingual-e5-small 로컬 디렉토리 필요 (설치는 19번 가이드)
E5_MODEL_PATH=~/models/multilingual-e5-small \
  python -m pytest tests/test_alarm/test_embedding_provider_realmodel.py -v
# 합격 기준: 4 passed — 로컬 오프라인 로드 + 근접(≥0.893) > 임계 0.87 > 이질(≤0.852) 완전 분리
```

### 2.4 플래그 off 회귀 테스트의 의미

`test_plan60_flags_off_regression.py`는 Plan 60의 **핵심 안전장치**다. 전 플래그 off(기본값) 상태에서:

- 게이트 판정(tier/사유/priority/signals)이 Plan 60 도입 전과 **비트동일**
- E2 off → `_detect_storm`(기존 스톰 감지) 동작 비트동일, `correlated`는 휴면 인자
- E3 off → enricher gather 태스크·키셋 불변, analyzer 무변경
- E4 off → 1홉 폴백 비트동일
- E6 off → notifier 본문 비트동일
- B-7 off → provider 미생성·주석 경로 미진입

이 테스트가 깨지면 **옵트인 원칙 위반(회귀)** 이므로 배포 전 반드시 원인을 잡는다.

### 2.5 아키텍처 검사

```bash
python scripts/arch_check.py --ci      # exit 0 필수
```

Plan 60 불변식: domain 모듈(`topology.py`·`correlation.py`·`anomaly.py`·`enrichment_profile.py`)은 stdlib만 사용, `notification_policy.py`는 상관/그래프/이상탐지/임베딩 모듈을 import하지 않는다(워커가 산출해 인자 주입).

---

## 3. 기능별 상세 검증 (동작 원리 + 수동 확인)

수동 확인의 공통 관찰 포인트는 **감사 로그** `logs/alarm_decisions.jsonl`(JSONL — `NOISE_DECISION_STORE_PATH`)이다. 이벤트 주입 방법(서버 기동·토큰·API)은 `16_plan52_noise_gate_test_guide.md` §6을 그대로 사용한다.

### 3.1 E1 — 재발생 억제 감사·관측성 (D-106)

**무엇을 검증하나**: 동일 지문(fingerprint) 알람이 dedup TTL(`repeat_interval_seconds`, 기본 4h) 내 재유입되어 억제될 때, 이전에는 흔적이 없었으나 이제 감사 레코드가 남고 재통보 시 재발 이력이 표기된다.

**수동 시나리오**:
1. 동일 알람 이벤트를 2회 이상 연속 주입.
2. `logs/alarm_decisions.jsonl`에서 확인:
   - 재발생 억제 레코드의 최상위 **`recurrence`** 필드(count·first_seen 등) — signals 스키마와 분리된 별도 필드
   - `recurrence_audit_every_n` > 1로 설정하면 count%N==0일 때만 적재(샘플링) 확인
3. TTL 경과 후 재통보 알람의 notifier 출력(HTML)에 재발 표기 확인.

**자동 테스트가 고정하는 것**: dedup dict화·TTL 기준 필드·`record_recurrence`가 `aggregate()` 집계에서 제외됨(비-decision)·is_clear 단락 유지.

### 3.2 E2 — 크로스-호스트 상관 (D-109) + 정밀화 (D-112)

**무엇을 검증하나**: 같은 존(db_id) 내 여러 호스트에서 짧은 창(120s)에 유사 알람이 쏟아질 때, 첫 도착(대표)만 통보하고 이후 유사 멤버는 SUPPRESS된다. **존 경계(gp↔yd)는 절대 넘지 않는다**(B-6).

**수동 시나리오**:
1. `.env`: `NOISE_CROSS_HOST_CORRELATION_ENABLED=true`
2. 같은 db_id의 서로 다른 hostname으로 유사 메시지 알람 3건을 120초 내 주입.
3. 확인:
   - 1건째(대표) = 정상 통보, `correlation_min_cluster_size`(2)번째부터 = SUPPRESS(사유 "크로스-호스트 상관")
   - 억제 레코드의 signals에 `correlated: true`, 최상위 **`correlation_meta`** 필드(대표 지문·멤버 순번·유사도) — D-112 감사
4. **경계 확인**: 다른 db_id로 같은 메시지 주입 → 상관되지 않아야 함(존 경계).
5. **위상 가중**(D-112): `NOISE_CORRELATION_TOPOLOGY_WEIGHT_ENABLED=true` 추가 시, E4 그래프상 인접한 호스트 쌍은 유사도 보너스(+0.2)로 경계 케이스가 더 잘 묶임. off면 필드 Jaccard와 비트동일.

**불변식**: 심각도3은 군집돼도 각각 PAGE(억제 안 됨 — step7.5가 심각도3 단락 뒤).

### 3.3 E3 — 동적 baseline 이상탐지 (D-110) + STL (D-113)

**무엇을 검증하나**: CPU/메모리 메트릭의 시간대별 정상 패턴(일간 24h 주기)을 학습해, 패턴 대비 이탈(잔차 z-score ≥ 3.0)이면 AI 메시지 심각도의 **상향 후보**로만 공급한다(상향 전용 — 하향 없음, 게이트 무변경).

**활성 조건(주의)**: `NOISE_DYNAMIC_BASELINE_ENABLED=true` **그리고** `NOISE_ENABLE_AI_SEVERITY_BOOST=true` — **AND 조건**이다. 하나만 켜면 동작하지 않는다.

**수동 시나리오**:
1. 두 플래그 활성 후 CPU/메모리 알람 주입(폴스타 `cmm_metric_stat` 이력이 있는 서버).
2. 확인: 이탈 시 analyzer 후처리에서 심각도 상향(AlarmState `anomaly_severity`), 이력 부족(`anomaly_min_periods` 미만 주기)이면 계산 skip → 상향 없음.
3. **STL**(D-113): `NOISE_ANOMALY_STL_ENABLED=true` 추가 시 STL 잔차 기반으로 대체. statsmodels 미설치면 로그에 "STL skip, HW 폴백"이 남고 HW로 동작 — **앱은 절대 죽지 않는다**(자동 테스트 `test_metric_stl_absence.py`가 import 차단 상태를 실증).

**불변식**: escalate-only(하향 금지), off 시 enricher 키셋·analyzer 비트동일.

### 3.4 E4 — 토폴로지 다홉 연쇄 억제 (D-107)

**무엇을 검증하나**: 폴스타 `AVAIL_DEPEND` 의존 그래프에서 조상(최대 5홉)이 이미 비정상일 때 자손 알람을 연쇄로 판단한다. **하이브리드 재현율 정책**(B-5): root의 알람이 실제 통보됐으면(`root_notified`) SUPPRESS, 통보 확인이 안 되면 DASHBOARD 강등(완전 억제 회피).

**수동 시나리오**:
1. `.env`: `NOISE_MULTI_HOP_CASCADE_ENABLED=true`
2. 의존 관계가 있는 서버 쌍에서 조상 알람 → 자손 알람 순서로 주입.
3. 확인:
   - 자손 레코드 signals에 `cascaded: true`, noise_ctx에 `root_resource`(최상위 비정상 조상 ID)
   - root가 먼저 통보된 경우 자손 SUPPRESS / root 미통보면 DASHBOARD
4. 그래프 캐시: 정적 엣지는 24h 캐시(`topology_cache_ttl_seconds`), 동적 상태(AVAIL_STATUS)는 매 이벤트 조회.

**불변식**: 심각도3 억제 금지, off 시 기존 1홉 판단 비트동일.

### 3.5 E5 — 변경 상관 (D-111)

**무엇을 검증하나**: 알람 직전 창(1h) 내 폴스타 변경이력(`cmm_resource_lifecycle_history`)에 해당 리소스 관련 변경이 있으면 "변경 근접"을 원인 후보로 오버레이한다.

**수동 시나리오**:
1. `.env`: `NOISE_CHANGE_CORRELATION_ENABLED=true`
2. 변경이력이 있는 리소스에 대해 변경 시각 1h 내 알람 주입.
3. 확인: noise_ctx에 `change_nearby: true`·`change_candidates`(변경 후보 목록). 피드 부재·조회 실패 시 빈 결과로 무해(graceful — `fetch_recent_changes`가 빈 리스트 반환).

### 3.6 E6 — 통보 컨텍스트 보강 L1 (D-108)

**무엇을 검증하나**: 알람 메시지를 분석해 kind(cpu/memory/disk/network/process/log)를 분류하고, kind별 L1 프로파일에 따라 관련 현황(프로세스 테이블 등)을 통보에 첨부한다(기존 Plan 47-1 CPU/메모리 첨부의 kind 확장).

**수동 시나리오**:
1. `.env`: `NOISE_MESSAGE_ENRICHMENT_ENABLED=true` (프로세스 스냅샷 자체는 기존 `ALARM_PROCESS_ENRICH_ENABLED` — 접두어가 다름에 유의, 기본 true)
2. kind별 메시지로 알람 주입(예: "디스크 사용률 90%", "CPU 사용률 초과").
3. 확인: notifier HTML에 kind에 맞는 보강 블록 첨부. 분류 불가(kind가 화이트리스트 밖)면 첨부 없이 기존 본문과 동일 — DB 접근 전에 skip.

**불변식**: post-gate(라우팅·판정 불변), off 시 본문 비트동일.

### 3.7 B-7 — 임베딩 주석 L-2/L-4 (D-114)

**무엇을 검증하나** (§15.4 D-035 경계 — **주석 전용, 판정 불변**):
- **L-2**: 지문은 다르지만 의미가 유사(≥0.87)한 알람에 `semantic_near_dup` 주석 → 감사 레코드 최상위 **`semantic_annotation`** 필드(재발 병합 후보 표시). 억제 판정은 결정적 지문 그대로.
- **L-4**: E4 root 서버명↔알람 텍스트 유사도를 noise_ctx **`root_text_similarity`** 로 주석. cascaded/root 판정 불변.

**사전 조건**: 모델 설치(`19_embedding_model_install_guide.md`) + `NOISE_EMBEDDING_MODEL_PATH` 설정.

**수동 시나리오**:
1. `.env`: `NOISE_SEMANTIC_DEDUP_ANNOTATION_ENABLED=true`, `NOISE_TOPOLOGY_TEXT_FUSION_ENABLED=true`
2. 표현만 다른 같은 의미의 알람 2건 주입(예: "CPU 사용률 90% 초과" / "CPU utilization exceeded 90%").
3. 확인:
   - 감사 레코드 `semantic_annotation` 필드(유사 대상·유사도)
   - **판정 불변 확인(핵심)**: 주석이 붙어도 tier/사유/priority가 모델 없이 돌렸을 때와 동일해야 한다(자동 테스트 `TestD035TierInvariance`가 고정)
4. 모델 경로를 제거하고 재기동 → 경고 1회 후 주석만 사라지고 게이트 정상(inert 확인).

---

## 4. 통합·E2E

- **그래프 통합**: `python -m pytest tests/test_alarm/test_noise_gate_graph_integration.py -v` — 워커→게이트→감사 기록의 전 구간 배선(신규 kwarg 전달 포함)을 검증한다.
- **서버 E2E**: `16_plan52_noise_gate_test_guide.md` §0/§6의 시나리오 러너로 이벤트를 주입하되, `.env`에 §3의 Plan 60 플래그를 추가해 기능별 관찰 포인트(§3의 JSONL 필드)를 확인한다. Plan 60 기능은 전부 게이트 파이프라인에 증분이므로 별도 러너가 필요 없다.

---

## 5. 배포 전 회귀·불변식 체크리스트

| # | 항목 | 확인 방법 |
|---|------|-----------|
| 1 | 전 플래그 off = 현행 비트동일 | `test_plan60_flags_off_regression.py` 통과 |
| 2 | 심각도3 절대 억제 금지 (E2·E4 포함) | 스위트 내 고정 테스트 + §3.2/§3.4 수동 확인 |
| 3 | 억제 ≠ 삭제 — 모든 억제는 감사 기록 | `logs/alarm_decisions.jsonl`에 SUPPRESS 레코드 존재 |
| 4 | D-035 경계 — 임베딩·LLM은 판정 불변 | `TestD035TierInvariance` + §3.7-3 |
| 5 | 계층 위반 0 | `arch_check.py --ci` exit 0 |
| 6 | 선택 의존성 부재 시 무해(graceful) | `test_metric_stl_absence.py` + B-7 inert 확인(§3.7-4) |
| 7 | E3는 상향 전용(하향 금지) | `test_anomaly_severity_guard.py` |
| 8 | 존 경계 불변(E2) | §3.2-4 수동 확인 |

---

## 6. 트러블슈팅

| 증상 | 원인 | 조치 |
|------|------|------|
| 기능을 켰는데 동작 안 함 | `.env` 키 접두어(`NOISE_`) 누락, 인라인 주석으로 값 오염, E3의 AND 조건(§3.3) 미충족 | `.env` 재확인 — 주석은 별도 줄로 |
| STL 테스트 skip | statsmodels 미설치 — 의도된 동작 | 켜려면 `pip install ".[stl]"` |
| 실모델 테스트 4 skipped | `E5_MODEL_PATH` 미설정 — 의도된 동작 | §2.3 명령으로 실행 |
| E2 상관이 안 묶임 | 창(120s) 초과 주입, 유사도 < 0.5, **다른 db_id**(존 경계 — 정상) | 주입 간격·메시지 유사도·존 확인 |
| E4 cascaded가 null | 의존 관계(AVAIL_DEPEND) 없음, 비PostgreSQL 존, 홉 상한 초과 | 대상 서버 쌍의 의존 데이터 확인 |
| E5 change_nearby가 null | off 상태이거나 창(1h) 내 변경이력 없음 | 변경이력 테이블 데이터·창 확인 |
| flags-off 회귀 실패 | Plan 60 코드가 off 경로를 오염(옵트인 위반) | **배포 중단** — 원인 수정 후 재검증, `18_known_mistakes.md` 기록 |
| 워커 재기동 후 상관/카운트 초기화 | in-memory 상태(E1 count·E2 클러스터·E4 캐시)는 휘발성 — 수용된 설계 | 정상 (영속화는 비범위) |

---

## 7. 참조

- `plans/60-noise-cancellation-benchmark-refinement.md` — 기능 설계 원문 (§2 E1 · §4 E2 · §5 E3 · §6 E4 · §7 E5 · §15.3 L-2/L-4 · §16 E6)
- `docs/02_decision.md` D-106~D-114 — 기능별 확정 결정
- `docs/16_plan52_noise_gate_test_guide.md` — 게이트 공통 E2E(서버 기동·이벤트 주입·시나리오 러너)
- `docs/19_embedding_model_install_guide.md` — B-7 모델 설치
- `docs/18_known_mistakes.md` — 테스트 관련 과거 실수·방지책

---

## 8. 목업 이벤트 생성기 — 번호 입력만으로 캔슬링 테스트 (Plan 65 R1)

> **도구**: `scripts/mock_polestar_events.py` (Plan 65 `65-noise-cancellation-mock-event-generator.md`)
> **목적**: 폴스타 실계 없이 사전 정의 이벤트를 **번호 입력만으로** 생성·주입하고, 그 이벤트가
> 캔슬링(SUPPRESS/DASHBOARD 등)됐는지 결정 감사 JSONL로 자동 판정·표시한다. §3의 수동 절차를
> 손으로 페이로드를 만들지 않고 반복 실행할 수 있다.
> **성격**: 주입·관찰 전용(읽기전용 D-003) — 파이프라인(src/) 무변경. 게이트/판정 로직은 바꾸지 않는다.

### 8.1 실행 (대화형 메뉴 — 기본 형태)

```bash
cd /path/to/collectorinfra
source .venv/bin/activate

# ① 전제: alarm_server(TCP 9100)·AlarmWorker·게이트(NOISE_ENABLE_NOISE_GATE=true) 기동
python -m noise_gate.alarm_server        # 별도 터미널 (TCP 실경로 수신부)

# ② 목업 생성기 상주 실행 → 번호 입력
python scripts/mock_polestar_events.py
```

기동 시 전송 경로 도달성·결정 로그 존재를 점검하고 번호 메뉴를 출력한다. **번호 + Enter**로 해당
시나리오를 주입하면 결정 JSONL을 폴링(기본 30s)해 ✔/✘·tier·감사 필드를 표시하고 메뉴로 복귀한다.
보조 키: `l`(메뉴 재표시), `v`(판정 대기 on/off 토글 — off면 주입만), `q`(종료).

### 8.2 시나리오 카탈로그 (번호 고정)

| # | 이름 | 이벤트 | 기대 | 필요 플래그·전제 |
|---|------|--------|------|------------------|
| 1 | sev3-page | 설비 UPS 경고 sev3 단건 | PAGE(단락) | (게이트 on) |
| 2 | low-suppress | 중요도 낮음 /fsapp 사용률 sev1 | SUPPRESS/TICKET | (게이트 on, ALARM_MIN_SEVERITY≤1) |
| 3 | maint-suppress | 유지보수 중 가용성 DOWN sev2 | SUPPRESS | (게이트 on, 픽스처 noise-test-maint) |
| 4 | dup-suppress | 동일 알람 2연속 | 2건째 SUPPRESS(dedup) | (게이트 on) |
| 5 | clear | DOWN(sev2)→UP(sev0) | 2건째 클리어 처리(통보 아님) | (게이트 on) |
| 6 | distinct-pair | 같은 호스트 상이 알람 2건 | **음성 대조군** 각각 독립 판정 | (게이트 on) |
| 7 | recur-audit (E1) | 동일 알람 3회 반복 | recurrence 감사 count | (상시 — `NOISE_RECURRENCE_AUDIT_EVERY_N`) |
| 8 | cross-host (E2) | 3호스트 동시 DOWN | correlation_meta·대표 외 SUPPRESS | `NOISE_CROSS_HOST_CORRELATION_ENABLED` |
| 9 | cascade (E4) | 상위→하위 자원 연쇄 | signals.cascaded=true | `NOISE_MULTI_HOP_CASCADE_ENABLED`+`DEPENDENCY_SUPPRESSION`+토폴로지 픽스처 |
| 10 | change-corr (E5) | 변경이력 근접 알람 | change 근접 오버레이(승격) | `NOISE_CHANGE_CORRELATION_ENABLED`+변경이력 픽스처 |
| 11 | semantic-dup (B-7) | 표현만 다른 유사 텍스트 쌍 | semantic_annotation(판정 불변) | `NOISE_SEMANTIC_DEDUP_ANNOTATION_ENABLED`+로컬 모델(§19) |
| 12 | invest-trigger | sev3 PAGE→자동 조사 submit | PAGE + 조사 submit **accepted/duplicate** | `NOISE_INVESTIGATION_TRIGGER_ENABLED` + sre_agent 조사 서비스(RUN_E2E=1 완주) |
| 13 | non-alarm (E7-b) | 승인/안내성 비알람 단건(`…Cloud PC 사양변경 승인바랍니다`) | SUPPRESS(비운영 — step0.5) | `NOISE_NON_ALARM_FILTER_ENABLED` |
| 14 | net-site-cascade (E7-c/d) | 동일 사이트 네트워크 장비 2대(`<장비ID>.<도메인>\|\|(장애) 세종대`) | site 토큰(`세종대`) 추출 + E2 상관 차원 | `NOISE_FORMAT_TOLERANT_PARSING_ENABLED`+`NOISE_CORRELATION_SITE_DIMENSION_ENABLED` |

- **[12] invest-trigger는 활성화**되었다(Plan 66 R9): S8 변형 sev3 단건(PAGE 단락 확정)을 주입하면
  `notification_gate` 직후 `investigation_trigger` 노드(Plan 64 CW-A)가 `sre_investigate_alarm`을
  submit→poll하고, 그 결과를 `decision_store.record_investigation`이 `logs/alarm_decisions.jsonl`에
  `type="investigation"` 레코드(`investigation_id`·`status`·`verdict`)로 감사한다. 도구의 판정기는 이
  investigation 감사 레코드를 조회해 **accepted**(신규 `investigation_id`) / **duplicate**(기존 id
  재사용 — dispatcher/JobStore dedup) / **submit 실패**(`investigation_id` 없음 — 서비스 미기동 graceful)를
  `[조사 N]` 줄로 표시한다. `investigation_id`는 uuid4 hex이므로 **재사용이 곧 dedup 확정 신호**다
  (동일 시나리오 연속 주입 시 2회째가 기존 id를 재사용하면 duplicate로 표기).
  - **전제**: `NOISE_INVESTIGATION_TRIGGER_ENABLED=true`(플래그 off면 주입 없이 사유 출력·중단) +
    sre_agent 조사 서비스(`investigation_service_url`, 기본 `localhost:9098/sse`) 기동. 선택 시 조사 서비스
    도달성을 `[조사 서비스] ✔/✘`로 사전 표기한다(비차단 — **미도달이어도 게이트 PAGE 판정·통보는 정상
    완료되고 트리거만 graceful 실패**해 "submit 실패(사유)"로 표기).
  - **RUN_E2E 경계**: 기본은 submit 응답·`investigation_id`·accepted/duplicate 확인까지다(스텁 서비스·LLM
    키 부재로도 검증 가능 — poll 최종 status는 `stub`). 실 HolmesGPT 조사 완주·브리핑 수신 대조는 LLM
    비용이 발생하므로 `RUN_E2E=1` 옵트인에서만 한다.
- **[9]·[10]은 토폴로지(AVAIL_DEPEND)·변경이력(cmm_resource_lifecycle_history) 픽스처가 필요**하다.
  현 도커 픽스처(`06_plan52_noise_fixtures.sql`)에는 두 데이터가 없어 cascaded/change_nearby가
  관측되지 않을 수 있다(플래그는 사전 점검되나 픽스처는 후속 — Plan 65 §7 G-3).
- **[11] semantic-dup은 지문이 다른 유사 텍스트**여야 주석이 붙는다(동일 지문은 결정적 dedup으로
  처리). 도구는 서버명을 달리해(szaaso01/szaaso02) 지문을 분리하고 텍스트는 유사하게 유지한다.
- **[13] non-alarm은 알람 마커 부재 + 비알람 마커(승인/요청/바랍니다) 존재** 텍스트로 구성된다
  (Plan 65 §2.4 S8 실측 `내부Cloud ○○○님의 Cloud PC 사양변경 승인바랍니다`, 담당자 가명화 ○○○).
  플래그 on 시 `is_operational_alarm=False`로 게이트 step0.5(심각도3 단락보다 앞)에서 SUPPRESS
  "비운영 알람"으로 억제된다. off면 현행 매트릭스 판정(비트동일).
- **[14] net-site-cascade는 `serverName`에 `<장비ID>.<도메인>\|\|(장애) <사이트명>` 마커**를 싣는다
  (Plan 65 §2.4 S5, S8530/K8530 페어). E7-c 파서가 사이트 토큰 `세종대`를 추출하고(`raw_payload.
  _site_token`), E7-d가 이 토큰을 E2 상관 signature 차원으로 주입한다. 실제 대표 외 SUPPRESS까지
  관측하려면 `NOISE_CROSS_HOST_CORRELATION_ENABLED`도 함께 켜야 한다(사이트 차원은 상관을 강화할 뿐).
  기존 [9] cascade는 사이트명을 conditionLog에만 실어 추출 0이었으므로 별도 신규 시나리오다(무변경).

각 시나리오는 실행 전 필요 플래그·모델을 `NoiseGateConfig`(NOISE_ prefix `.env`)로 사전 점검하고,
미충족이면 **주입하지 않고 사유를 출력**한다(침묵 실패 금지).

### 8.3 판정 (캔슬링 여부 확인)

주입한 `MOCK-<run_id>-<seq>` alarmId를 `logs/alarm_decisions.jsonl`에서 폴링해 대조한다:

- 결정 레코드(`tier` 보유): 기대 tier와 대조(✔/✘).
- 재발 레코드(`type=recurrence`): dedup 억제로 판정하고 `count`를 표시.
- 조사 레코드(`type=investigation`, [12]): `investigation_id` 재사용 여부로 accepted/duplicate를,
  `investigation_id` 없음이면 submit 실패(graceful)를 표시(`status`는 poll 최종 상태 stub/done/down 등).
- Plan 60 감사 필드 표시: 최상위 `recurrence`(E1)·`correlation_meta`(E2)·`semantic_annotation`(B-7),
  `signals.cascaded`/`root_resource`(E4)·`correlated`(E2).

레코드 미검출(타임아웃)은 실패가 아닌 **미확정(?)** 으로 표시하고 서버 기동·플래그·`ALARM_MIN_SEVERITY`
확인을 안내한다.

### 8.4 기동 옵션 (비대화형 단발 주입 포함)

```bash
# 단발 주입 후 종료(자동화·도구 e2e용)
python scripts/mock_polestar_events.py --send dup-suppress
python scripts/mock_polestar_events.py --send cross-host --path redis
```

| 옵션 | 기본 | 의미 |
|------|------|------|
| `--host` / `--port` | localhost / 9100 | TCP 대상(폴스타 실경로 수신부) |
| `--path tcp\|redis` | tcp | 주입 경로(tcp 실경로 / redis 폴백 XADD alarm:raw) |
| `--redis-url` | redis://localhost:6379/0 | `--path redis`일 때 대상 |
| `--decision-log` | logs/alarm_decisions.jsonl | 판정 대상 결정 JSONL |
| `--timeout` | 30 | 판정 대기(초) |
| `--db-id` | polestar_pg | 대상 DB 프로필(도커 픽스처) |
| `--send NAME` | — | 단발 주입 후 종료 |

- **TCP(기본)** 는 alarm_server TcpReceiver 파싱·XADD를 포함한 폴스타 실경로 100%를 커버한다.
  미기동 시 명확한 사유와 `--path redis` 안내를 출력한다(침묵 폴백 금지).
- **Redis(폴백)** 는 수신부를 건너뛰고 워커 이후 경로를 커버한다.

### 8.5 자동 테스트

```bash
python -m pytest tests/test_scripts -q          # 빌더·카탈로그·메뉴 디스패치·판정 로직 단위
RUN_E2E=1 python -m pytest tests/test_scripts -q # 전송·판정 e2e(서버 필요 — 옵트인)
```
