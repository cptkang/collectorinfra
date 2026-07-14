# Plan 60 심층 검토 — 문제점별 개선 제안서

> 작성일: 2026-07-13
> 근거: Plan 60 코드 실측 검토(§변경이력 2026-07-13 행)에서 발견한 구현 결함 5건 + 공통 리스크.
> 각 제안은 **현행 코드의 실제 호출부·상태·전례에 그대로 얹을 수 있는 수준**으로 구체화했다.
> 상태: 제안 (미구현) — §7 의사결정 항목 확정 후 착수.

---

## 1. P1 (E1) — 재발생 dedup 감사 사각지대 해소

### 문제 (실측)
- `_is_duplicate_fingerprint` True → `_process`가 **debug 로그 + ACK로 종료**(`alarm_worker.py` L372~381). 그래프에 진입하지 않으므로 `notification_gate`의 `store.record()`가 실행될 기회가 없다 → **억제된 재발생은 decision_store에 감사 0건** ("억제≠삭제" 위반 상태).
- 계획 초안의 dict화(`{first_seen, last_seen, count}`)는 **TTL 비교 기준 필드가 없다**. 현행 float 값의 의미는 "마지막 통보 시각"(중복 시 미갱신 → 고정창·TTL 경과 후 재통보). `last_seen`(중복마다 갱신)과 비교하면 슬라이딩 창으로 변질되어 **지속 재발 알람이 영원히 재통보되지 않는 회귀**.

### 개선안 (코드 수준)

**(1) 레코드 구조 — `last_notified`를 판정 기준으로 명시**

```python
# alarm_worker.py L67
self._gate_dedup: dict[str, dict] = {}
# fingerprint → {"first_seen", "last_notified", "last_seen", "count"}

def _is_duplicate_fingerprint(self, fingerprint, now, severity) -> tuple[bool, dict]:
    ttl = ...  # 심각도 분기 현행 유지
    rec = self._gate_dedup.get(fingerprint)
    if rec is not None and now - rec["last_notified"] < ttl:   # ★ 판정은 last_notified
        rec["count"] += 1
        rec["last_seen"] = now
        return True, dict(rec)
    prev = dict(rec) if rec is not None else {}                # 직전 창 메타(재통보 표기용)
    self._gate_dedup[fingerprint] = {
        "first_seen": now, "last_notified": now, "last_seen": now, "count": 1,
    }
    expired = [k for k, v in self._gate_dedup.items()
               if now - v["last_seen"] >= ttl]                 # 정리는 last_seen(연속 재발 count 보존)
    for k in expired:
        del self._gate_dedup[k]
    return False, prev
```

- 판정 필드(`last_notified`)와 정리 필드(`last_seen`)가 다름을 주석·테스트로 고정.
- 반환 tuple의 두 번째 값: 중복이면 현재 레코드(억제 감사용), 신규면 **직전 창 레코드**(재통보 시 "직전 4h N회 재발" 표기용).

**(2) 억제 시점 감사 — 워커 직접 기록 (`record_resolution` 전례)**

```python
# decision_store.py — record_resolution(D-049)와 동일 패턴
def record_recurrence(self, *, fingerprint: str, count: int,
                      first_seen_ts: float, alarm_id: str = "", ts=None) -> None:
    record = {"type": "recurrence", "fingerprint": fingerprint,
              "count": count, "first_seen_ts": first_seen_ts,
              "alarm_id": alarm_id, "ts": ...}
```

- `aggregate()`는 현재 `type=="resolution"`만 제외한다 → **`rec.get("type")`이 있는 레코드(비-decision) 일반 제외**로 바꿔 recurrence도 자동 제외(향후 타입 추가에도 안전).
- 호출부(`_process` L372 억제 분기): `self._decision_store`는 워커가 이미 보유. 매 억제마다 1줄이 과하면 `NOISE_RECURRENCE_AUDIT_EVERY_N`(기본 1) 샘플링 — count % N == 0일 때만 적재.

**(3) 재통보 시점 표기 — 그래프 state 전달**

- `AlarmState`(alarm_graph.py)에 `recurrence: Optional[dict]` 키 추가. 워커가 신규(비중복) 처리 시 `prev` 메타를 주입.
- `notification_gate`: `store.record(decision, alarm_id=..., recurrence=state.get("recurrence"))` — `record()`에 선택 인자 추가, 최상위 필드로 기록(§8.2 signals 동결 스키마 미훼손).
- `alarm_notifier`: recurrence 존재 시 메시지에 "직전 {window}h {count}회 재발 후 재통보" 1줄 첨부.

### 회귀 경계·검증
- 억제 판정(TTL·심각도 분기)은 비트 동일 — 기존 dedup 테스트 그대로 통과.
- 신규 테스트: ① 지속 재발 알람이 TTL 경과 후 재통보되는지(슬라이딩 창 변질 방지) ② `record_recurrence`가 `aggregate()` by_tier/total에서 제외 ③ 재통보 이벤트의 recurrence 메타 = 직전 창 count ④ is_clear 이벤트는 dedup 미호출(현행 단락 유지).
- 플래그 불필요(게이트 off 시 경로 미진입). in-memory라 재기동 시 초기화(수용).

---

## 2. P2 (E2) — 크로스-호스트 상관: 계층 정정 + 온라인 의미론

### 문제 (실측)
- 계획의 "domain `correlation.py`가 `noise_signal_tools.scan_message_signature` 재사용"은 **domain→infrastructure 역방향 의존**(arch_check 위반). 실측상 그 함수는 domain `severity_signatures.scan_signature_severity`의 얇은 래퍼.
- `correlate_events(window_events)`는 배치 서명이지만 워커는 이벤트를 **1건씩 순차 처리**하며 앞선 판정을 소급 변경할 수 없다 — 대표 선출·군집 의미론이 미정의.
- 억제가 step7(storm)을 재사용하면 감사 reason이 "스톰 — 동일 서버 다발"로 남아 오해 유발.

### 개선안 (코드 수준)

**(1) 계층 정정 — 토큰화는 domain 함수 + 워커 산출**

```python
# src/alarm/domain/correlation.py (stdlib only)
def signature_tokens(alarm_name: str, resource_type: str,
                     signature_label: str, server_name: str = "") -> frozenset[str]:
    ...  # 정규화(소문자·구분자 분리). server_name은 토큰에서 제외(호스트 경계를 넘는 게 목적)

def jaccard(a: frozenset, b: frozenset) -> float: ...

@dataclass
class ClusterState:                      # 워커가 보관하는 활성 클러스터
    representative_fp: str
    tokens: frozenset[str]
    first_ts: float
    last_ts: float
    member_count: int = 1

def match_cluster(clusters, tokens, *, sim_threshold) -> int | None:
    """대표 토큰과의 Jaccard 최고점(동점 시 first_ts 오름차순 — 결정성) 인덱스 반환."""
```

- `signature_label`은 워커가 `scan_signature_severity(event.condition_log)`(**domain** 함수)로 산출해 전달. application→domain 의존만 사용, correlation.py는 값만 소비.

**(2) 온라인 그리디 군집 — `_detect_correlated_storm`**

```python
# alarm_worker.py — 신규 상태 + detection (storm과 별개)
self._correlation_clusters: dict[str, list[ClusterState]] = {}  # db_id → 활성 클러스터

def _detect_correlated_storm(self, event, now) -> bool:
    if event.is_clear:
        return False
    scope = event.db_id                       # DB(존) 경계는 넘지 않음 — 기본 스코프
    clusters = self._correlation_clusters.setdefault(scope, [])
    # ① 만료 정리(창 밖 last_ts 클러스터 제거·빈 스코프 키 삭제 — sweep 의무)
    # ② 버퍼 상한(correlation_buffer_max) 초과 시 oldest 제거 + warning(침묵 금지)
    tokens = signature_tokens(...)
    idx = match_cluster(clusters, tokens, sim_threshold=cfg.correlation_sim_threshold)
    if idx is not None:
        c = clusters[idx]
        c.member_count += 1; c.last_ts = now
        return c.member_count >= cfg.correlation_min_cluster_size  # 대표 포함 n번째부터 억제
    clusters.append(ClusterState(compute_fingerprint(event), tokens, now, now))
    return False                              # 첫 도착 = 대표 → 통보
```

- **첫 도착 이벤트가 곧 대표**(소급 없음). 결정성 테스트는 "동일 이벤트 시퀀스 → 동일 판정 시퀀스"로 고정.
- `_detect_storm`(동일 서버)은 무변경·병존 — 두 플래그 독립.

**(3) 게이트 — 별도 step 7.5·별도 사유**

```python
# notification_policy.py — 인자 추가(기본 False → 하위호환·회귀 0)
def decide_notification(..., storm: bool = False, correlated: bool = False):
    ...
    # step 7.5: 크로스-호스트 상관 (storm 다음, 매트릭스 이전)
    if cross_host_correlation_enabled and correlated:
        return _decision(TIER_SUPPRESS, "크로스-호스트 상관 — 클러스터 대표 외 억제")
```

- 클러스터 식별자(대표 fingerprint)는 워커 로그 + `record()` 선택 인자(P1의 recurrence와 동일 방식)로 감사에 첨부.
- signals 스키마에 `correlated` 키 추가는 §6 공통(일괄 1회 버전업)에서 처리.

### 회귀 경계·검증
- `cross_host_correlation_enabled=False`(기본) → detection 자체 미수행, `_detect_storm` 비트 동일.
- 심각도3은 step3 단락으로 군집돼도 각각 PAGE(불변) — 테스트 고정.
- 신규 테스트: 동일 원인성 3호스트 다발 → 대표 1건 통보+2건 억제 / 유사도 미달 → 전건 통보 / 버퍼 sweep(만료·빈 키·상한) / step7.5 reason 문자열.

---

## 3. P3 (E3) — 동적 baseline: 주입 지점 정정 + 매핑표 + 순수 Python HW

### 문제 (실측)
- "context_enricher가 `analysis.ai_message_severity`에 주입"은 불가능 — 배선은 `context_enricher → analyzer → (agentic_enricher) → gate`이고 analysis는 **analyzer가 생성**.
- 어떤 알람에 어떤 시계열을 조회할지(알람→메트릭 매핑)가 미정의 — LogMonitor 등 비메트릭 알람 처리 불명.
- 매 알람마다 3주기 STL 계산은 3s 예산 초과 위험. statsmodels 반입(B-3)이 블로커로 걸려 있음.

### 개선안 (코드 수준)

**(1) 주입 지점 — analyzer 후처리 훅(agentic_enricher 전례 L250~262)**

```python
# alarm_analyzer.py — LLM 결과 파싱 직후(결정적 후처리, LLM 무관)
anomaly_sev = state.get("anomaly_severity")   # context_enricher가 산출한 상향 후보
if (anomaly_sev is not None
        and getattr(cfg.noise_gate, "dynamic_baseline_enabled", False)
        and anomaly_sev > event.severity                       # 상향 전용
        and anomaly_sev > (result.ai_message_severity or 0)):  # 기존 ai보다 클 때만
    result.ai_message_severity = anomaly_sev
```

- `AlarmState`에 `anomaly_severity: Optional[int]` 키 추가. **계산**은 context_enricher의 `asyncio.gather`에 4번째 코루틴으로 편승(타임아웃·graceful 기존 틀 재사용), **반영**은 analyzer 이후.
- agentic_enricher와 공존해도 안전: 셋(LLM·agentic·anomaly) 모두 "후보 > 기존" 가드의 상향 전용 → 결과는 max와 동일. 게이트(`enable_ai_severity_boost` AND)는 무변경.

**(2) 알람→메트릭 결정 매핑표 — `classify_alarm_kind` 재사용**

```python
# src/alarm/domain/anomaly.py
METRIC_SOURCE_BY_KIND = {
    "cpu":    ("server.Cpus",   "Utilization"),   # definition_name은 착수 시 실측 확정
    "memory": ("server.Memory", "Utilization"),
}
```

- `classify_alarm_kind(event)`(Plan 47-1, cpu|memory)가 None이거나 매핑 부재 → **계산 skip → None(상향 없음)**. 1차 범위 CPU·메모리 한정, 디스크·네트워크는 definition_name 실측 후 확장.

**(3) 순수 Python Holt-Winters — B-3 블로커 해소**

```python
# src/alarm/domain/anomaly.py (stdlib only — math/statistics)
def holt_winters_fit(series: list[float], period: int) -> HWState | None:
    """additive 삼중 지수평활. len(series) < anomaly_min_periods*period면 None."""
def residual_sigma(series, state) -> float
def anomaly_score(state, sigma, value) -> float          # 잔차 z-score(결정적)
def severity_from_anomaly(score, z_high) -> int | None    # z>hi → 상향 후보, 아니면 None
```

- 외부 패키지 불요 → statsmodels 반입 협의(B-3) 자체가 소멸, domain stdlib-only 원칙과의 긴장도 해소. STL은 정확도 요구 확인 시 2차 강화(인프라 계층 헬퍼).

**(4) baseline 캐시 — 이벤트 시점은 조회+z-score만**

- 인프라 어댑터 `polestar_metric_baseline.py`: `cmm_metric_stat_h` 고정 SQL(읽기전용) → HW 상태·잔차 σ를 Redis `alarm:baseline:{db_id}:{server}:{kind}`(TTL `anomaly_baseline_cache_ttl_seconds=3600`)에 캐시. 캐시 미스에서만 시계열 조회·적합. 캐시 실패는 무시(순수 최적화 — enrich 캐시 전례).

### 회귀 경계·검증
- `dynamic_baseline_enabled=False`(기본) → anomaly_severity 항상 None → analyzer 후처리 no-op → 게이트 비트 무변경.
- 신규 테스트: 계절 피크 비오탐(합성 사인+노이즈 시계열, 정적 z-score 대비) / 히스토리 부족 None / 비메트릭 알람 skip / 상향 가드(LLM ai=3, anomaly=2 → 3 유지) / `max()` 하향 불가.

---

## 4. P4 (E4) — 토폴로지: 정적/동적 분리 + 방언 스코프 + 재현율 하이브리드

### 문제 (실측)
- `is_cascaded(node, abnormal)`의 abnormal(AVAIL_STATUS)은 **동적 값** — 24h 캐시 대상이 아님. 계획은 엣지(정적)와 상태(동적)의 분리가 없다.
- 현행 노이즈 SQL은 PostgreSQL 방언 고정(`LIMIT 1`·`polestar.` 스키마) — b0(DB2) 유입 시 신규 SQL도 실패.
- 다홉 억제는 "근본원인 노드만 PAGE"를 전제하나 **root 알람이 실제 PAGE됐다는 보장이 없다**(파이프라인 미유입·min_severity 드롭·dedup 가능) → 억제 반경 확대만큼 재현율 리스크 증가.

### 개선안 (코드 수준)

**(1) 정적 엣지 로더(장기 캐시) / 동적 상태(매 이벤트) 분리**

```sql
-- 엣지 로더(인프라, db_id별 topology_cache_ttl_seconds=86400 캐시)
SELECT ID, NAME, AVAIL_DEPEND_RESOURCE_ID, AVAIL_DEPEND_RESOURCE_ID_2
FROM polestar.cmm_resource
WHERE DTIME IS NULL AND AVAIL_DEPEND_RESOURCE_ID IS NOT NULL   -- 엣지 보유 행만(전량 스캔 아님)
```

```python
# src/alarm/domain/topology.py (stdlib only, 불변 스냅샷)
class DependencyGraph:
    def ancestors(self, node_id, *, max_hops) -> list[str]:   # BFS·방문집합(순환 방어)·홉 상한
    def name_of(self, node_id) -> str | None                  # root 서버명 역조회(P4-3용)
```

```sql
-- 상태 조회(매 이벤트, 신선값): 조상 ID만 IN — 행 수 = 홉 상한(≤5)으로 유계
SELECT ID, AVAIL_STATUS FROM polestar.cmm_resource
WHERE ID IN (...) AND DTIME IS NULL
```

- enricher: 알람 서버의 리소스 **ID** 해소는 기존 `build_resource_signal_sql`에 `SVR.ID` 컬럼 1개 추가(동일 쿼리·하위호환)로 해결 → 캐시 그래프에서 `ancestors()` → IN 조회 → `noise_ctx["cascaded"]`(bool)·`noise_ctx["root_resource"]`(최근접이 아닌 **최상위 비정상 조상 ID**) 산출.
- 어느 단계든 실패 → 현행 1홉 `parent_avail_status` 판정 폴백(보수적·회귀 0).

**(2) 엔진 방언 스코프 명시**

- E4 1차는 **gp/yd(PostgreSQL) 한정**: 로더가 `get_domain_by_id(db_id).db_engine != "postgresql"`이면 즉시 None → 1홉 폴백 → 비억제(보수적). b0 편입 시 D-053/D-057 체크리스트(①위치 힌트 ②base_url ③엔진 방언 ④스키마 한정) 적용 — `FETCH FIRST`·CURRENT SCHEMA 분기.

**(3) 재현율 정책 — 하이브리드(B-5 권고안)**

```python
# notification_policy.py step6.4 확장 (권고: B-5 하이브리드)
if dependency_suppression and noise_ctx.get("cascaded"):
    if noise_ctx.get("root_notified"):        # 워커가 결정적으로 산출·주입
        return _decision(TIER_SUPPRESS, "의존성 억제(다홉) — 근본원인 노드 통보됨")
    return _decision(TIER_DASHBOARD, "의존성 연쇄(다홉) — 근본원인 미통보, 대시보드 강등")
# cascaded 미제공(1홉 모드·수집 실패) → 현행 parent_avail_status 판정 폴백(무변경)
```

- `root_notified` 산출은 **이미 존재하는 워커 상태 재사용**: `_active_firings`의 스코프 키가 `f"{db_id}|{server_name}"`이므로, root 리소스의 서버명(`DependencyGraph.name_of`)으로 `self._active_firings.get(f"{db_id}|{root_server_name}")`이 window 내 활성인지 확인 — 신규 저장소 불요·결정적.
- 효과: root가 실제로 통보된 연쇄만 SUPPRESS(정밀 억제), 불확실하면 DASHBOARD(억제≠삭제·재현율 우선). 완전 억제 대비 노이즈 감소 폭은 줄지만 D-035 원칙과 정합 — **B-5는 이 하이브리드를 권고**.

### 회귀 경계·검증
- `multi_hop_cascade_enabled=False`(기본) → 로더·BFS 미수행, step6.4 현행 비트 동일.
- 신규 테스트: 다홉(조부모 비정상) 연쇄 억제 / 순환 엣지 무한루프 방어 / 홉 상한 / root 미통보 → DASHBOARD / b0(비PostgreSQL) → 1홉 폴백 / 캐시 TTL 무효화.

---

## 5. P5 (E5) — noise_ctx 계약 일관성 + 피드 graceful

### 문제 (실측)
- noise_ctx 동결 계약(5키)은 **세 곳에서 구성**되고(`_noise_unavailable()` / `_unavailable()` / 정상 fetch dict) **Redis 캐시로 직렬화 왕복**된다(`alarm:noisectx:*`, TTL 300s). 신규 키를 한 곳만 추가하면 경로별 키 불일치.

### 개선안
- 신규 키(`change_nearby`·`change_candidates`)는 **세 구성점에 동시 추가** + 소비는 전부 `.get()`(구버전 캐시 항목 하위호환). 계약 dict 구성을 상수 `_NOISE_CTX_KEYS` 기반 헬퍼 하나로 단일 출처화하면 향후 키 추가 시 불일치 자체가 불가능해짐(권장).
- 캐시 staleness(변경 직후 최대 300s 미반영)는 promote 전용 신호라 재현율 무해 — 문서화만.
- 변경 피드 어댑터는 인터페이스(`fetch_recent_changes(window) -> list[ChangeEvent]`)만 고정하고 구현은 B-2 확정 후: (a) 폴스타 변경이력 테이블 (b) 외부 CI/CMDB (c) 수동 등록 API(FastAPI 라우트 1개 + JSONL 저장 — decision_store 패턴 재사용). 피드 부재/실패 → 키 None → step9 promote 미발동(게이트 무변경).

---

## 6. P6 (공통) — 스키마·상태·테스트 규율

1. **signals §8.2 일괄 1회 버전업**: E1~E5가 조각 확장하지 않고, Wave A에서 신규 키(`correlated`·`cascaded`·`root_resource`·`root_notified` 등 채택분)를 한 번에 확정. `_signals()` 갱신 시 **그 스키마를 단언하는 테스트를 repo 전체 grep으로 전수 갱신**(Known Mistakes 2026-06-30 E3 — 흩어진 단언 누락 방지). recurrence·클러스터 메타는 signals가 아닌 decision_store 레코드 최상위 필드로.
2. **신규 in-memory 상태 sweep 의무**: `_gate_dedup`(dict화)·`_correlation_clusters`·baseline 캐시 — 값 상한뿐 아니라 **키 만료 sweep**을 형제 상태와 일관 구현, sweep 테스트 포함(Known Mistakes 2026-06-29 E2).
3. **플래그 off 전수 회귀**: 기존 `test_e2_flags_off_regression.py` 패턴으로 `test_plan60_flags_off_regression.py` 신설 — 신규 플래그 전부 off 시 decide_notification·detection 경로 비트 동일 고정.
4. **심각도3 단락 불변**: 신규 step(7.5·6.4 확장)이 step3 뒤에만 위치함을 테스트로 고정.

---

## 7. 착수 순서·의사결정 요청

### 권고 착수 순서 (Plan 60 §9 Wave와 정합)

| 순서 | 항목 | 규모 | 즉시 가치 | 선행 의존 |
|------|------|------|----------|----------|
| 1 | **P1 (E1)** | XS | 감사 사각지대 해소 — 유일하게 "현행 버그성 공백" 수정 | 없음 |
| 1' | **P6-1 signals 버전업** | XS | 이후 전 항목의 스키마 기반 | 없음 |
| 2 | **P4 (E4)** | M | 노이즈·RCA 공용 선행자산 | B-1·B-5 확정 |
| 3 | **P2 (E2) ∥ P3 (E3)** | M each | 병렬 가능 | P4(위상 가중은 2차)·B-3 소멸 |
| 4 | **P5 (E5)** | M~L | 피드 확보 후 | B-2 확정 |

### 사용자 확인 필요 (Plan 60 §8 연동)

| ID | 결정 | 권고 |
|----|------|------|
| B-1 | 토폴로지 소스 | (a) `AVAIL_DEPEND`만으로 1차 착수 — CMDB 병합은 E5와 함께 재평가 |
| B-2 | 변경 피드 소스 | 폴스타 변경이력 테이블 유무 선조사 → 없으면 (c) 수동 등록 API 임시 |
| B-3 | statsmodels 반입 | **불요 — 순수 Python Holt-Winters로 1차 구현(P3-3), 블로커 소멸** |
| B-5 | 다홉 억제 재현율 정책 | **하이브리드(P4-3): root 통보 확인 시 SUPPRESS, 미확인 시 DASHBOARD 강등** |
| 신규 | E2 상관 스코프 | db_id(존) 경계 내 상관을 기본으로 — 존 간 상관은 근거 확보 후 |
