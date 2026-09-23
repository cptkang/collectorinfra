# Prometheus 연동 · 구성 · 사용 가이드

> **정본 범위**: 이 저장소에서 Prometheus를 **어디에 붙이고 · 무엇으로 설정하고 · 어떻게
> 쓰는지**를 한 곳에 모은 문서. 설계 근거는 `docs/02_decision.md` **D-119**, 픽스처·실연동
> 절차의 원본은 `docs/23` §8.2, 조사 서비스 기동은 `docs/26_sre_agent_guide.md` §5.4에 있다.
> 여기서는 그 조각들을 **연동 관점 하나로** 재구성하고, **2026-08-28 실측 상태**를 명시한다.
> **2026-09-10 갱신**: §0 요약을 당일 실측으로 갱신하고, §3.3을 **게이트 측 플래그 처리 방침**(사용자 확정 ③ 예비 코드 + 판정 기한 2027-02-20 — `plans/91` 1-10 · 구 `plans/70` P1-1)으로 확장했다. 조사 경로 접속 URL은 여전히 비어 있다.
> **2026-09-22 갱신**(`plans/92` v3 재검토 후속): 낡은 서술 세 곳을 HEAD `048c2be` 실측으로 정정했다. §3.4는 `inspect_host`에 프로덕션 호출부가 **있다**. §8.1 ②의 ITAM 유령 키는 D-214로 해소됐다. §9 코드 위치의 줄 번호도 바꿨다. §0의 본체 채팅 행도 함께 고쳤다. §4.3에는 루트 `uv.lock` 재생성(`mcp` 2.1.1 → 1.30.0 · D-181 부기)을 적었다. 접속 URL이 비어 있는 상태는 그대로다.

---

## 0. 30초 요약 — 지금 상태

| 항목 | 실측 결과 (2026-09-10 재실측 · 변동 없는 행은 2026-08-28 값) |
|---|---|
| PromQL 도구 구현 | ✅ 있음 — `mcp_server/mcp_server/promql_tools.py` (도구 7종: 고수준 `prom_metric_instant`·`prom_metric_range` + 원시 5종) |
| 도구 등록 배선 | ✅ 무조건 등록 — `mcp_server/mcp_server/server.py` |
| 조사(SRE) 측 소비 배선 | ✅ 있음 — `sre_agent`가 `mcp_servers`로 등록 (`interface/mcp_service.py`). 단 **결정적 사전수집(D-197·D-209)은 폴스타 도구만** 부른다(`evidence_prefetch.py` — 알람·지표·변경 이력); PromQL은 조사 LLM의 ReAct 루프에서만 쓰인다 |
| **접속 URL 설정** | ❌ **여전히 비어 있음(2026-09-10)** — `config.toml` `url = ""` · `mcp_server/.env`에 `PROMETHEUS_*` 0건 · 루트 `.env` 0건. `.env.example`에는 문서화됨(G-3 해소) |
| **`mcp_server` 기동 가능 여부** | ✅ 가능 — `mcp<2` 상한 고정 (D-181 · §4.3). 2026-09-10 실 기동 확인(plans/88 검증에서 9099 SSE 기동) |
| 게이트(노이즈 캔슬링) 측 Prometheus | ⚠️ 클라이언트만 존재 · 프로덕션 호출부 0건 → **2026-09-10 사용자 확정: ③ 예비 코드 명시 + 판정 기한 2027-02-20**(§3.3) |
| 본체 채팅(text2sql) 경로 | ❌ PromQL 프로파일 없음 — `HOST_INSPECT_PROFILES` 4종(폴스타 SQL 도구 3 + 프로세스 API 1)에 PromQL이 없다 (§3.4 · 2026-09-22 정정) |
| 품질 게이트(D-119 채택 조건) | ✅ **통과 확정** — 2026-08-06 A/B 실측 "열화 없음" (§7.3) |

**한 줄 결론**: 코드는 완성돼 있고 품질 게이트도 통과했으나, **주소가 비어 있어 실제로는 동작하지
않는다.** 남은 것은 ①`PROMETHEUS_URL` 설정 ②`nodename` 라벨 규약 확인 **둘뿐**이며, 둘 다 운영 Prometheus에
대한 **외부 선행조건**(`plans/91` 1-12 · P0-3)이라 코드로는 더 진행할 것이 없다. 게이트 측 채널은 그 선행조건이 풀릴 때까지
**예비 코드**로 두기로 확정했다(§3.3 · 판정 기한 2027-02-20).

---

## 1. 왜 이 구조인가 — 접근 경계 일원화 (D-119)

HolmesGPT에는 내장 `prometheus/metrics` toolset이 있지만 **쓰지 않는다**(`remote_vm_profile()`이
`{"enabled": False}`로 명시 비활성 — `sre_agent/sre_agent/toolset_profiles.py:202`). 대신 Prometheus
접근을 `mcp_server` 하나로 모았다.

```
                       ┌──────────────────────────────────────┐
   sre_agent           │            mcp_server                │        Prometheus
   (HolmesGPT)         │   "관측 데이터 읽기 접근 경계"        │
       │               │                                      │
       │  MCP/SSE      │  ┌────────────────────────────┐      │  HTTP GET
       ├──────────────▶│  │ 폴스타 SQL 고수준 도구 8종  │      │  /api/v1/query
       │  :9099(/9097) │  ├────────────────────────────┤      │  /api/v1/query_range
       │               │  │ PromQL 도구 (고수준2·원시5) │─────▶│  …
       │               │  └────────────────────────────┘      │
       │               │   URL·인증·timeout·감사 = 전부 여기   │
       └───────────────│                                      │
                       └──────────────────────────────────────┘
```

**이 배치가 사는 이유 4가지**

1. **자격증명 보관 지점 축소** — `PROMETHEUS_URL`·`PROMETHEUS_AUTH_HEADER`는 `mcp_server`에만 있다.
   `sre_agent`는 **Prometheus 주소를 모른다**(`settings.py`에 `prometheus_url` 필드 자체가 없음).
2. **감사 일원화** — 조사 중 발생한 모든 데이터 접근(SQL·PromQL)이 `mcp_server` 로그 한 곳에 남는다.
3. **hostname 정합의 결정적 강제 (핵심)** — LLM이 라벨 셀렉터를 직접 쓰지 않는다. 도구는
   `hostname` 인자만 받고, **서버가 `{nodename="<hostname>"}`을 조립**한다(D-035 3차 방어).
4. **timeout 서버 강제** — 소비자가 우회할 수 없다(`make_client`가 클라이언트 생성 시점에 박는다).

> **부수 효과 실측(2026-08-06 A/B)**: 이 경로가 내장 toolset보다 **도구 호출 6.0회 / 58.7k 토큰**
> (내장은 8.0회 / 85.5k)으로 **더 적게 쓰고 같은 사실에 도달**했다. 라벨 조립을 서버가 대신하므로
> LLM의 탐색 단계가 짧아진다.

---

## 2. 두 개의 Prometheus 경로 — 혼동 금지

저장소에는 이름이 같은 **서로 다른 Prometheus 소비 경로가 둘** 있다. 설정 키도 코드도 다르다.

| | **① 조사 경로** (SRE Agent) | **② 게이트 경로** (노이즈 캔슬링) |
|---|---|---|
| 코드 | `mcp_server/mcp_server/promql_tools.py` | `noise_gate/infrastructure/prometheus_client.py` |
| 설정 위치 | `mcp_server/config.toml` `[prometheus]` + `mcp_server/.env` | 루트 `.env` `ALARM_PROMETHEUS_*` |
| 주소 지정 | 단일 `PROMETHEUS_URL` | **존(db_id)별 CSV** `db_id=url,…` |
| 소비자 | HolmesGPT ReAct 루프(LLM) | (예정) Holt-Winters baseline 산정 |
| **현재 상태** | 구현·배선 완료 · **URL 미설정**(외부 선행조건 대기) | 구현만 존재 · **호출부 0건** · **예비 코드 확정(2026-09-10 · 판정 기한 2027-02-20)** |
| 켜는 방법 | §3.1 | **지금은 켤 수 없다** — §3.3(플래그 처리 방침) |

②는 `polestar_metric_baseline.py:24`가 *"§5.2 확정 설계상 배선하지 않는다"*로 사유를 남긴
**의도적 보류**다. 처리 방식(배선 완결 / 삭제 / 기한부 예비코드)은 `plans/70` P1-1 → `plans/91` 1-10으로 이관됐고,
**2026-09-10 사용자가 ③ 기한부 예비 코드로 확정**했다(§3.3). 존별 CSV를 채워도 **현재는 아무 효과가 없다**.

---

## 3. 구성 (Configuration)

### 3.1 `mcp_server` — 접속 설정의 정본

**파일 ①: `mcp_server/config.toml`** (현재 값 — `url`이 비어 있다)

```toml
[prometheus]
url = ""                  # ← 여기가 비어 있어서 지금 동작하지 않는다
auth_header = ""          # 인증 헤더 "전체 값" (예: "Bearer <token>"). 비면 헤더 미부착
query_timeout = 30        # 서버가 강제하는 쿼리 timeout(초) — 원시 패스스루에도 동일 적용
expose_raw_promql = false # 원시 PromQL 5종 노출 여부 (기본 비노출)
```

**파일 ②: `mcp_server/.env`** — 보안 값은 TOML이 아니라 여기서 오버라이드한다.

| 환경변수 | TOML 대응 | 타입 | 의미 |
|---|---|---|---|
| `PROMETHEUS_URL` | `url` | str | base URL (예: `http://prom.internal:9090`) |
| `PROMETHEUS_AUTH_HEADER` | `auth_header` | str | `Authorization` 헤더 **전체 값** |
| `PROMETHEUS_QUERY_TIMEOUT` | `query_timeout` | int | 쿼리 timeout(초) |
| `EXPOSE_RAW_PROMQL` | `expose_raw_promql` | bool | `1/true/yes/on` → 원시 도구 노출 |

오버라이드 구현: `mcp_server/mcp_server/config.py:270-287`. 시스템 환경변수가 `.env`보다 우선한다.

> **⚠️ 실측 갭 2건**
> - `mcp_server/.env`에 `PROMETHEUS_*` 키가 **한 줄도 없다**(2026-08-28 확인).
> - `mcp_server/.env.example`에도 **문서화돼 있지 않다** — 신규 배포자가 이 키의 존재를 알 수 없다.
>   §8 후속 항목으로 남긴다.

**미설정일 때의 동작**: 침묵 폴백하지 않는다. 모든 PromQL 도구가 즉시 아래를 반환한다.

```json
{"error": "PROMETHEUS_URL 미설정 — PromQL 조회 불가"}
```

### 3.2 `sre_agent` — Prometheus 주소를 **두지 않는다**

`sre_agent`가 아는 것은 `mcp_server`의 SSE 주소 하나뿐이다.

```dotenv
# 파일: sre_agent/.env   (없으면 생성. 예제 파일에는 이 키가 없다 — 실측)
POLESTAR_MCP_URL=http://localhost:9097/sse    # 기본값은 9099
POLESTAR_MCP_TOKEN=<선택 — 설정 시 Bearer 헤더 부착>
```

`AgentSettings`(`sre_agent/sre_agent/settings.py:29`)에 `prometheus_url` 필드는 **의도적으로 없다**.
Prometheus를 바꾸려면 `sre_agent`가 아니라 `mcp_server` 설정을 바꾼다. 분리 절차 = **URL 1개 변경**.

### 3.3 게이트(노이즈 캔슬링) 측 — 플래그 처리 방침 (2026-09-10 확정 · `plans/91` 1-10)

```dotenv
# 파일: <레포 루트>/.env   ※ 지금 채워도 효과 없음 — 아래 3.3.1 참조
ALARM_PROMETHEUS_ENABLED=false                 # 옵트인 플래그 — 프로덕션 참조 0건
ALARM_PROMETHEUS_BASE_URLS_CSV=polestar_cm_gp=http://prom-gp:9090,polestar_cm_yd=http://prom-yd:9090
ALARM_PROMETHEUS_TIMEOUT_SECONDS=3
```

#### 3.3.1 지금 무엇이 있고 무엇이 없는가 (2026-09-10 실측 · D-161 ② 4항)

| 실측 항목 | 결과 | 근거 명령 |
|---|---|---|
| ① 운영 `.env` 실제값 | `ALARM_PROMETHEUS_*` **3키 전부 미기재**(= 코드 기본값 off · CSV 빈 값) | `grep -nE '^ALARM_PROMETHEUS' .env` → 0건 |
| ② 서빙 상태 | 운영 Prometheus **없음**(P0-3 인프라 대기 · `plans/91` 1-12). 로컬은 Docker 픽스처(9190)뿐 | `docker ps` · `docs/23` §8.2 |
| ③ 최종 수정 | `noise_gate/infrastructure/prometheus_client.py` — **2026-08-05** 패키지 분리 커밋(`b79808a`, 현 브랜치 소속) 이후 변경 0 | `git log -1 -- …/prometheus_client.py` |
| ④ 역방향 import(소비처) | 테스트 밖 **0건**. `polestar_metric_baseline.py:24`가 독스트링으로 *"배선하지 않는다"*를 남길 뿐 · `settings_catalog.py`가 CSV·timeout 2키를 **미소비 목록**에 명시 | `grep -rn prometheus_client noise_gate src --include=*.py` |

즉 삭제(②안)의 실측 요건은 이미 갖춰져 있다. 그런데도 삭제하지 않는 이유는 **`plans/91` 1-12(운영 Prometheus 실연동 —
`nodename` 규약 실측·표준화 협의)가 외부 대기 중**이고, 그 선행조건이 풀리는 순간 이 클라이언트가 바로 필요해지기 때문이다
(게이트 E3 baseline의 유일한 폴백 채널 — 3.3.3).

#### 3.3.2 확정 방침 — ③ 예비 코드 명시 + 판정 기한

| 항목 | 값 |
|---|---|
| 결정 | **③ 예비 코드로 유지**(배선 완결 ①·삭제 ② 기각) — 2026-09-10 사용자 확정(`plans/91` 1-10 · 원 `plans/70` P1-1 · `plans/66` §1.5 C3) |
| 판정 기한 | **2027-02-20**(D-161 C1 · `docs/flag_audit.md` 통일 기한) |
| 기한 도래 시 판정 규칙 | 1-12(운영 Prometheus)가 **해소됐으면** 3.3.3의 배선 작업으로 전환(플래그 on 경로 실효) · **미해소면** ②안 삭제(플래그 3키·클라이언트·도움말·카탈로그 미소비 목록 동시 제거 — 사유부 연장은 1회만) |
| 표기 위치 | `prometheus_client.py` 모듈 독스트링 · `src/config.py` `AlarmConfig` 주석 · `config/settings_help/alarm.yaml` 3항목 · `src/api/settings_catalog.py` 미소비 목록 주석 — **네 곳이 같은 문장**(기한·근거 D-161)을 갖는다 |
| 지금 값을 채우면 | **아무 일도 일어나지 않는다.** `prometheus_enabled=true`여도 이 플래그를 읽는 프로덕션 코드가 없다. 설정 화면의 도움말이 이 사실을 그대로 말한다 |

#### 3.3.3 (참고) 배선을 완결한다면 무엇을 해야 하는가 — 1-12 해소 시의 작업 목록

이 절은 **착수 지시가 아니다.** 기한 판정에서 "해소"로 갈릴 때 필요한 작업의 크기를 미리 적어 둔 것이다.

1. **소비처**: `noise_gate/infrastructure/polestar_metric_baseline.py`의 E3 baseline 조회에 **폴백 채널**로 편입 — 폴스타
   `cmm_metric_stat_h`가 비어 있거나(b0 DB2·조회 실패·이력 부족) 조회가 실패했을 때만 `PrometheusClient.query_series(db_id,
   promql, start, end)`로 같은 시계열을 얻는다. PromQL은 코드가 조립하는 고정 템플릿(kind→메트릭 매핑 — `DEFAULT_METRIC_SOURCE_BY_KIND`와
   대칭) · `{nodename="<server_name>"}` 셀렉터 · `now()` 금지(사건 시각 기준 start/end).
2. **게이팅**: `alarm_cfg.prometheus_enabled` **AND** `get_prometheus_base_url(db_id)`가 있을 때만. 둘 중 하나라도 없으면 종전과
   비트 동일(폴스타 경로만).
3. **설정**: `ALARM_PROMETHEUS_BASE_URLS_CSV`는 존(db_id)별 — 은행존·공동존 Prometheus가 다를 수 있다는 전제(프로세스 API와 같은 패턴).
   `settings_catalog.py` 미소비 목록에서 2키를 **제거**하고 도움말을 "소비됨"으로 고친다.
4. **검증**: `httpx.MockTransport` 단위(조립 셀렉터·timeout·None 폴백) + Docker 픽스처(9190) 통합. `nodename` 규약은 §6.1의 5단계를
   **먼저** 통과해야 한다 — 규약이 어긋나면 조회가 조용히 빈 결과가 되어(§3.5) baseline이 없는 것과 같다.
5. **회귀 게이트**: 플래그 off 비트 동일 테스트 · `arch_check --ci`(infrastructure → domain만) · `overfit_check --ci`.

#### 3.3.4 (참고) 삭제한다면 — ②안의 범위

기한 판정이 "미해소"로 갈리면 다음을 **한 커밋**에서 지운다(D-161 ② 4항 실측은 3.3.1 표를 갱신해 첨부):
`prometheus_client.py` · `AlarmConfig.prometheus_*` 3필드 + `get_prometheus_base_url` · `settings_catalog.py` 그룹·미소비 목록 3키 ·
`config/settings_help/alarm.yaml` 3항목 · 관련 테스트(`noise_gate/tests/test_prometheus_client*.py`) · 본 문서 §2·§3.3 · `docs/02`에
폐기 결정 등재. `polestar_metric_baseline.py:24`의 독스트링 한 줄은 "폴백 채널 없음"으로 고친다.

### 3.4 본체 채팅(text2sql) 경로 — PromQL 프로파일 없음

`src/dbhub/client.py:414` `HOST_INSPECT_PROFILES`는 4종(`processes` · `os_config` ·
`resource_status` · `metric_trend`)이다. `os_config`·`resource_status`·`metric_trend`는 폴스타 SQL 도구이고,
`processes`(`polestar_process_snapshot`)는 폴스타 **실시간 프로세스 API** 직결이다(`client.py:412` — `source` 인자 없음).
PromQL 프로파일은 없다.

**`inspect_host`(`client.py:483`)에는 프로덕션 호출부가 있다** — `src/orchestration/host_inspect.py:209` `run_host_inspect`
(Plan 78 W3 · 2026-08-31). 실행 함수 `run_host_inspect`는 `subagents.py:1427`이 서브에이전트로 등록한다. 판정 함수
(`detect_profile`·`has_target_signal`)는 `intent_planner.py:204` `_coerce_host_inspect_intent`가 쓰고, 3단 `tier3_plan.py:191`이 그 교정을 재사용한다.
경로는 `COMPOSITE_INVESTIGATION_ENABLED`(기본 off · `src/config.py:1110`) 뒤에 있다.
종전 판(2026-08-28)의 "호출부 0건"은 틀린 서술이었다(2026-09-22 정정 · `docs/18` 기록).

**채팅에서 PromQL을 쓰려면** 프로파일 표에 한 행을 더하는 것만으로는 부족하다. `host_inspect.py`의 `_PROFILE_KEYWORDS`
(`:47-51`)와 `_PROFILE_IDENTIFIER`(`:54-58` — 현재 3종)도 함께 고쳐야 한다. 이 모듈이 2·3단 공통 함수이므로 D-225 ⑦에 맞는 자리다.
계획은 `plans/92` O3(`metrics_live` · 선택)에 있다.

### 3.5 ★ 진짜 전제는 `nodename` 라벨 규약이다

고수준 도구는 `{nodename="<hostname>"}`을 **서버가 조립**한다. 따라서 실 Prometheus 메트릭에
`nodename` 라벨이 없거나 값이 폴스타 `server_name`과 다르면 **조회가 전건 빈 결과**가 된다.
도구는 성공(HTTP 200)을 반환하고 데이터만 비므로, **LLM이 그 공백을 서술로 메운다** — 가장 위험한
실패 모드다.

Docker 픽스처 실측(2026-08-06, 참고 기준선):

- `nodename` 커버리지 **1404/1404 = 100%** — `static_configs.labels`로 **수집 시점 주입**이라
  job(node·mock) 무관하게 전 메트릭이 보유한다.
- node_exporter가 `node_uname_info`에 싣는 자기 `nodename`은 타깃 라벨과 충돌해
  **`exported_nodename`으로 밀린다**(타깃 라벨 승리 → 조립 안전). **소비 측은 반드시 `nodename`을 쓴다.**
- 미존재 hostname → `status=success` + 빈 배열로 graceful(오류 아님).

실 Prometheus 편입 시 이 4가지를 측정하는 절차는 **§6**에 있다.

---

## 4. 기동 방법

### 4.1 Docker 픽스처 (개발·테스트)

```bash
# [CWD=레포 루트]
cd testdata/prometheus && docker compose up -d && cd -

# 확인 — nodename 라벨이 실려 오는지
curl -s 'http://localhost:9190/api/v1/query?query=up' | python -m json.tool | grep -E 'nodename|value'
```

| 컨테이너 | 포트 | 역할 |
|---|---|---|
| `fixture_prometheus` | **9190** → 9090 | Prometheus 2.53.0 · 보존 15d · 무인증 · scrape 5s |
| `fixture_target_vm` | **9101** → 9100 | ubuntu + node_exporter · `hostname=svr-web-01` |
| `fixture_mock_exporter` | **9102** → 80 | nginx가 정적 `/metrics` 서빙 — **결정적 단언용** |

> **포트가 왜 밀렸나**: 호스트 9090은 langfuse-minio가, 9100은 알람 수신부(폴스타 push)가 점유한다.
> 컨테이너 내부 포트는 표준 그대로다.

mock exporter의 **고정값**(문자열 대조 검증에 쓰인다):

```
mock_cpu_usage_percent{mode="user"}   97.5
mock_cpu_usage_percent{mode="system"}  1.5
mock_memory_used_bytes           8589934592     # 8 GiB
mock_oom_kills_total                       3
```

`nodename` 값은 PG 픽스처 `polestar.cmm_resource.server_name`과 **동일하게 정렬**돼 있다
(`svr-web-01`) — 그래야 서버측 조립과 소스 교차 검증이 픽스처에서 실제로 단언된다.

**job `polestar`(폴스타 브리지 B-2)** 는 호스트 `host.docker.internal:9098/metrics`를 긁는다. 그 포트에
`EXPOSE_POLESTAR_EXPORTER=true`·`SERVER_PORT=9098`로 `mcp_server`를 따로 띄워야 `up{job="polestar"} == 1`이
된다(미기동이면 0 — 다른 job은 무관). 브리지 소스 `[[openmetrics.bridge_sources]]`는 **TOML 전용**(env 키 없음)이라
레포 `config.toml`(비어 있음)로 띄우면 `polestar_bridge_*`·서버 시리즈가 비어 나온다 — 소스를 넣은 사본 TOML로 기동한다.

### 4.2 `mcp_server` 조사 프로파일 기동

`mcp_server`는 전용 venv가 없다. 루트 `.venv`에 `PYTHONPATH`를 얹어 띄운다.

```bash
# [CWD=레포 루트] — 조사 프로파일(9097). 본체 NL→SQL용 9099와 별도 인스턴스다.
POLESTAR_CONNECTION='postgresql://…@localhost:5434/infradb' \
PROMETHEUS_URL='http://localhost:9190' \
EXPOSE_EXECUTE_SQL=false \
EXPOSE_RAW_PROMQL=false \
EXPOSE_POLESTAR_TOOLS=true \
SERVER_PORT=9097 \
PYTHONPATH="$PWD/mcp_server" .venv/bin/python -m mcp_server
```

- **조사 배치는 `EXPOSE_EXECUTE_SQL`·`EXPOSE_RAW_PROMQL`를 반드시 `false`**로 둔다(D-122).
  원시 SQL/PromQL을 열면 LLM이 방언 오류로 step을 소진한다.
- 레포의 `config.toml`은 본체 파이프라인용이라 `expose_execute_sql = true`다. 그래서 위처럼
  **환경변수로 덮은 별도 인스턴스**를 띄운다.
- 이 배치가 노출하는 것: **폴스타 고수준 8종 + PromQL 고수준 2종**.

### 4.3 `mcp` 버전 제약 — `<2` 고정 (해소됨 · D-181)

**2026-08-28 이전 상태**: 루트 `.venv`의 `mcp`가 **2.1.1**로 올라 `mcp_server`가 임포트 단계에서
죽었다. mcp 2.x가 `FastMCP`를 `MCPServer`로 개명하며 `mcp.server.fastmcp` 모듈을 제거했기 때문이다.
영향 파일은 `server.py`·`tools.py`·`polestar_tools.py`·`promql_tools.py` 4종.

**★ 더 위험했던 2차 증상 — 조용한 skip**: `test_promql_tools.py`는 모듈 상단이
`try: import … except ImportError: HAS_MCP=False` + `pytestmark = skipif`라, 파손 상태에서
**53건이 전부 skip되면서 "통과"처럼 보였다.** 전체 스위트에서는 앞선 모듈이 먼저 임포트를
시도해 수집조차 통과했다. **임포트 가드 skip은 "환경 부재"와 "환경 파손"을 구별하지 못한다.**

**조치(완료)**

```bash
.venv/bin/pip install "mcp<2"     # → 1.29.1
```

`pyproject.toml`(루트)·`mcp_server/pyproject.toml` 양쪽에 **`mcp<2` 상한을 고정**해 재발을 막았다.

> **2026-09-22 추가 — 루트 `uv.lock` 재생성(D-181 부기).** 상한을 고정한 뒤에도 `uv.lock`은 옛 상태(`mcp` **2.1.1** · 지정자 없음)였다.
> 그래서 `uv sync --frozen`으로 설치하면 위 파손이 그대로 재현될 수 있었다. `uv lock`으로 재생성해 **`mcp` 1.30.0**(`specifier = "<2"`)으로 맞췄다.
> 버전 변화는 `mcp` 계열 4건뿐이다. 1.30.0과 1.29.1로 `mcp_server` 스위트를 나란히 돌린 결과(스크래치 venv · Windows)
> **회귀 0**이었다. 1.30.0은 245 passed / 2 failed, 1.29.1은 246 passed / 1 failed다. 실패는 둘 다 `test_sql_log.py` 동시 쓰기 테스트로,
> Windows에서 `O_APPEND` 원자성이 성립하지 않아 생기는 플랫폼 실패이며 `mcp`와 무관하다.

| venv | `mcp` 버전 | 상태 |
|---|---|---|
| 루트 `.venv` | **1.29.1** | ✅ 정상 (2026-08-28 복구) |
| `sre_agent/.venv` | 1.25.0 | ✅ 정상 (별도 venv — 애초에 무영향) |

**복구 후 실측**

| 검증 | 복구 전 | 복구 후 |
|---|---|---|
| `mcp_server` 전체 | 대량 skip | **183 passed / 2 skipped** |
| `test_promql_tools.py` | **53 skipped** | **52 passed / 1 skipped** |
| `tests/test_dbhub_integration.py` | 수집 오류 | **60 passed** |
| `pip check` | — | 깨진 요구 0 |

**다운그레이드가 안전한 방향인 근거**: `claude-agent-sdk 0.1.48`의 요구는 `mcp>=0.1.0`으로
**상한이 없고**, 실사용 API(`mcp.server.Server`·`mcp.types`)는 1.x 이래 불변이다. 본체가 쓰는
`mcp.client.sse.sse_client`는 1.x·2.x 양쪽에 존재한다. 즉 **2.x에서 깨지는 것은 서버
프레임워크(FastMCP) 하나뿐**이고 그 소비자는 전부 이 저장소 코드다.

> **상한을 풀려면**: `MCPServer` API 마이그레이션(4파일 동시 변경)과 **같은 커밋에서만** 한다.
> 자세한 판단 근거는 `docs/02_decision.md` **D-181**.

---

## 5. 사용 방법 — 도구 레퍼런스

### 5.1 고수준 도구 2종 (기본 노출 · 조사 경로의 정식 인터페이스)

| 도구 | 인자 | 기본값 | 조립되는 PromQL |
|---|---|---|---|
| `prom_metric_instant` | `hostname`, `metric` | — | `metric{nodename="hostname"}` |
| `prom_metric_range` | `hostname`, `metric`, `window`, `step` | `1h`, `60s` | 동일 셀렉터 + `start/end/step` |

- `hostname` = **폴스타 등록 서버명**(= Prometheus `nodename` 라벨).
- `metric`은 **bare 메트릭 이름만** 허용한다 — `^[a-zA-Z_:][a-zA-Z0-9_:]*$`.
  `node_cpu_seconds_total{mode="idle"}` 처럼 셀렉터를 붙이면 **거부**되고 원시 도구로 유도된다.
- `window`/`step`은 `30s`·`15m`·`1h`·`7d`·`2w` 형식(양수만). `range`는 `end=now`, `start=now-window`.

```python
# 실제로 나가는 요청 (window=1h, step=60s)
GET /api/v1/query_range
    ?query=node_load1{nodename="svr-web-01"}
    &start=<now-3600>&end=<now>&step=60
```

### 5.2 원시 도구 5종 (옵트인 `EXPOSE_RAW_PROMQL=true`)

| 도구 | 인자 | 엔드포인트 |
|---|---|---|
| `prom_query` | `query`, `time?` | `/api/v1/query` |
| `prom_query_range` | `query`, `start`, `end`, `step` | `/api/v1/query_range` |
| `prom_labels` | — | `/api/v1/labels` |
| `prom_metadata` | `metric?` | `/api/v1/metadata` |
| `prom_series` | `match` | `/api/v1/series` |

기본 비노출이다(`execute_sql` 기본 숨김 전례). **탐색적 조사 배치에서만 켠다** — LLM 조사 배치에서
켜면 방언 오류로 step을 소진한다(D-122). 켜더라도 **timeout은 서버가 강제**하므로 우회 불가.

**읽기 전용(D-003)**: 노출 엔드포인트는 위 5개뿐이다. `admin/tsdb` 등 쓰기 API는 노출하지 않는다.

### 5.3 반환 · 오류 계약

정상 (JSON **문자열**):

```json
{
  "data": { "resultType": "vector", "result": [ … ] },
  "queried_at": "2026-08-28T05:12:33.101+00:00",
  "source_kind": "prometheus",
  "query": "node_load1{nodename=\"svr-web-01\"}",
  "endpoint": "/api/v1/query",
  "result_count": 1,
  "window": "1h", "step": "60s"        // range 전용
}
```

오류 — **예외를 전파하지 않는다**. 항상 `{"error": …}` 문자열이다.

| 상황 | 메시지 |
|---|---|
| URL 미설정 | `PROMETHEUS_URL 미설정 — PromQL 조회 불가` |
| hostname 공백 | `hostname이 비어 있음` |
| metric이 bare 이름 아님 | `고수준 도구의 metric은 bare 메트릭 이름만 허용: …` |
| duration 형식 오류 | `지원하지 않는 duration 형식: … (예: 30s, 15m, 1h, 7d, 2w)` |
| 비200 | `Prometheus 비200 응답: status=<code>` |
| `status != success` | `Prometheus 오류 응답: <reason>` |
| timeout·네트워크 | 예외 문자열 그대로 |

**감사 로그** — 성공/실패 모두 남는다(폴스타 도구와 동일 파이프):

```
promql audit: tool=prom_metric_instant query=node_load1{nodename="svr-web-01"} elapsed_ms=12.4 rows=1
promql audit: tool=prom_metric_range  query=… elapsed_ms=30021.0 error=ReadTimeout
```

### 5.4 도구 없이 직접 확인하기 (curl)

```bash
PROM=http://localhost:9190     # 픽스처. 실서버는 http://prom.internal:9090

# 고수준 도구가 만드는 것과 동일한 질의
curl -s --data-urlencode 'query=mock_memory_used_bytes{nodename="svr-web-01"}' \
     "$PROM/api/v1/query" | python -m json.tool
# → data.result[0].value[1] == "8589934592"
```

### 5.5 조사 LLM은 이걸 어떻게 쓰나

`sre_agent`가 `remote_vm_profile()` + `mcp_servers={"polestar": …}`로 `DiagnosisAgent`를 만들면,
HolmesGPT의 `RemoteMCPToolset`이 `mcp_server` 엔드포인트에서 도구를 **자동 발견**한다. LLM은
"CPU를 보고 싶다" 수준의 의도만 갖고 `prom_metric_instant(hostname="svr-web-01",
metric="mock_cpu_usage_percent")`를 호출하며, **라벨 문자열은 한 번도 만들지 않는다.**

---

## 6. 실 Prometheus 편입 체크리스트

> **⚠️ D-120 절대 제약**: 실 폴스타·실 Prometheus 데이터를 **Gemini(외부 SaaS)로 보내는 조합은
> 금지**다. 실연동 조사 LLM은 사내 백엔드(vLLM 등)여야 한다. 자세한 판정표는 `docs/23` §8.0.

### 6.1 라벨 규약 실측 (P0-3의 실체)

```bash
PROM=http://prom.internal:9090

# ① 도달성·무인증 여부
curl -s -o /dev/null -w '%{http_code}\n' "$PROM/api/v1/status/buildinfo"

# ② nodename 라벨 존재·값 목록
curl -s "$PROM/api/v1/label/nodename/values" | python -m json.tool | head -20

# ③ 커버리지 — 전체 대비 nodename 보유 시리즈
curl -s --data-urlencode 'query=count({__name__=~".+"})'             "$PROM/api/v1/query"
curl -s --data-urlencode 'query=count({__name__=~".+",nodename!=""})' "$PROM/api/v1/query"

# ④ 폴스타 서버명과 실제로 맞는가 — 대표 호스트 1건 왕복
HOST='<폴스타 server_name 하나>'
curl -s --data-urlencode "query=up{nodename=\"$HOST\"}" "$PROM/api/v1/query" | python -m json.tool

# ⑤ 보존 기간 — range 조회 가능 범위
curl -s "$PROM/api/v1/status/runtimeinfo" | python -m json.tool | grep -i retention
```

**자가 검증**: 위 5개를 `PROM=http://localhost:9190`(픽스처)로 먼저 돌리면
`200` / `['svr-web-01']` / `1404`·`1404` / `2 시리즈` / `15d`가 나온다. 실서버에서 다르면
**명령이 아니라 환경이 다른 것**이다.

**판정**

| ③ 커버리지 | ④ 일치 | 결론 |
|---|---|---|
| 높음 | 일치 | 그대로 진행 |
| 높음 | **불일치**(FQDN vs 단축명 등) | `server_name` ↔ `nodename` **정규화 규약 합의 먼저**(행정) |
| 낮음 / 0 | — | 스크레이프 설정에 `nodename` 타깃 라벨 주입 필요 — **인프라 소유자 협의** |

### 6.2 편입 순서

1. `mcp_server`가 기동되는지 확인(§4.3 — `mcp<2` 유지). 안 되면 나머지가 전부 무의미하다.
2. §6.1 라벨 규약 실측 → 불일치면 여기서 멈추고 협의.
3. `mcp_server/.env`에 `PROMETHEUS_URL`(+ 필요 시 `PROMETHEUS_AUTH_HEADER`) 설정.
4. 조사 프로파일 인스턴스 기동(§4.2) — `EXPOSE_RAW_PROMQL=false` 유지.
5. `sre_agent/.env`의 `POLESTAR_MCP_URL`을 그 인스턴스로 지정.
6. 대표 호스트 1건으로 고수준 도구 왕복 확인.

---

## 7. 검증

### 7.1 단위 (실 Prometheus 불요 — `httpx.MockTransport`)

```bash
cd mcp_server && ../.venv/bin/python -m pytest tests/test_promql_tools.py -q
```

nodename 조립 · timeout 강제 · 원시 게이팅 · 반환/오류/감사 계약을 고정한다.
**52 passed / 1 skipped**(skip 1건은 Docker 옵트인 — 2026-08-28 실측).

> **"통과"를 읽을 때의 주의**: 이 파일은 `mcp`·`httpx` 임포트 실패 시 **전건 skip**으로 넘어간다.
> `passed` 수를 보지 않고 exit code만 보면 파손을 통과로 오독한다(§4.3의 실제 사고).

### 7.2 Docker 통합 (옵트인)

```bash
cd testdata/prometheus && docker compose up -d && cd -
cd mcp_server && RUN_DOCKER_IT=1 ../.venv/bin/python -m pytest tests/test_promql_tools.py -q
```

실 HTTP로 `{nodename="svr-web-01"}` 조립 → mock 고정값 단언 · 원시 옵트인 경로 · timeout 강제.

### 7.3 D-119 품질 게이트 (과금 — **사용자 승인 필수 · D-127**)

```bash
RUN_E2E=1 python sre_agent/scripts/ab_promql_gate.py --trials 2
```

내장 toolset(A) vs `mcp_server` PromQL(B)로 같은 조사를 완주시켜 비교한다. 판정은 픽스처
고정값 4종의 **결정적 문자열 대조**(LLM을 심판으로 쓰지 않는다 — D-035).

**2026-08-06 최종 실측**: **A 완주 2/2 · 사실 4.0/4 / B 완주 2/2 · 사실 4.0/4 — 동률, 열화 없음.**
→ **B안(현행) 유지 확정.** 산출물 `eval_results/d119_ab_gate_final.json`.

> **문서 정정 이력**: `docs/02_decision.md`의 D-119 본문 라인이 통과 이후에도 *"보류"*로
> 남아 있었다(상태 라인·구현 라인 2곳). **2026-08-28 정정 완료** — 두 라인 모두 해소 사실과
> 잔여 항목(운영 Prometheus 편입 실측)을 가리키도록 갱신했다.

---

## 8. 알려진 갭 · 후속

| # | 갭 | 영향 | 제안 |
|---|---|---|---|
| ~~G-1~~ | ~~`mcp` 2.1.1 → `mcp_server` 기동 불가~~ | — | ✅ **해소(2026-08-28)** — `mcp<2` 고정 · D-181 |
| ~~G-2~~ | ~~`test_promql_tools.py` 53건 조용한 skip~~ | — | ✅ **해소** — 52 passed로 복귀. 다만 임포트 가드 skip 구조는 남아 있다(G-7) |
| G-7 | 임포트 가드 skip이 *환경 부재*와 *환경 파손*을 구별하지 못함 | 파손이 통과로 보인다 | "skip이 지배적이면 실패"하는 CI 게이트 검토(미착수) |
| ~~G-3~~ | ~~`.env.example` 오버라이드 키 9종 누락 + 유령 키 + 존 3종 미문서화~~ | — | ✅ **해소(2026-08-28)** — 키 보강 + 커버리지 테스트 8건 신설 · §8.1 |
| G-4 | 게이트 측 Prometheus 채널 미배선 | 존별 CSV가 무효 | ✅ **처리 방침 확정(2026-09-10)** — ③ 예비 코드 + 판정 기한 2027-02-20(§3.3 · `plans/91` 1-10). 배선은 1-12 해소 시 §3.3.3 |
| G-5 | 본체 채팅 경로에 PromQL 프로파일 없음 | 채팅에서 메트릭 조회 불가 | 필요 시 `HOST_INSPECT_PROFILES` 확장 + `host_inspect.py` 키워드·식별자 표 동반 수정(§3.4 · `plans/92` O3 선택) (미요청) |
| G-6 | 실 Prometheus `nodename` 규약 미확인 | 조회가 **조용히 빈 결과** | §6.1 실측 후 인프라 소유자 협의 |

### 8.1 G-3 상세 — `mcp_server/.env.example`의 문서화 공백

`.env.example`은 배포자가 **"무엇을 설정할 수 있는가"를 알게 되는 유일한 창구**다. 여기 없는 키는
코드를 읽지 않는 한 존재를 알 수 없다. 실측(2026-08-28) 결과 세 종류의 공백이 있다.

**① 오버라이드 키 14종 중 9종 누락**

`config.py`가 읽는 키(좌)와 `.env.example`이 문서화한 키(우)를 대조한 결과:

| 환경변수 | 문서화 | 누락 시 실제로 벌어지는 일 |
|---|---|---|
| `SERVER_NAME`·`SERVER_HOST`·`SERVER_PORT`·`SERVER_TRANSPORT`·`SERVER_LOG_LEVEL` | ✅ | — |
| **`MCP_BEARER_TOKEN`** | ❌ | **전송 인증 토큰.** 존재를 모르면 **인증 없이 뜬 것을 정상으로 오인**한다 — 가장 심각 |
| `PROMETHEUS_URL` | ❌ | PromQL 도구 전건 실패. 원인이 "설정 누락"임을 알 방법이 `.env.example`에 없다 |
| `PROMETHEUS_AUTH_HEADER` | ❌ | 인증 걸린 Prometheus에 붙지 못함 |
| `PROMETHEUS_QUERY_TIMEOUT` | ❌ | 30초 고정으로 알고 지나감 |
| `EXPOSE_RAW_PROMQL` | ❌ | **D-122가 "조사 배치는 반드시 false"라고 요구하는 키인데, 배포자가 키 이름을 모른다** |
| `EXPOSE_EXECUTE_SQL` | ❌ | 동상 — 조사 배치에서 원시 SQL이 열린 채로 뜬다 |
| `EXPOSE_POLESTAR_TOOLS` | ❌ | 폴스타 소스 없는 배치에서 도구 표면을 줄일 수단을 모름 |
| `POLESTAR_DOMAIN_GUARD` | ❌ | 폴스타 미서빙 배치에서 옵트아웃 못 함 |
| `PROCESS_API_BASE_URL` | ❌ | `process_snapshot` 도구가 조용히 오류 반환 |

> **왜 문제인가**: 이 키들의 값은 `config.toml` 주석에는 적혀 있다. 하지만 `config.toml`은
> **형상 관리 대상 파일**이고 `.env`가 **환경별 오버라이드 창구**다. 보안 값(`MCP_BEARER_TOKEN`·
> `PROMETHEUS_AUTH_HEADER`)은 애초에 TOML에 쓰면 안 되므로 **`.env.example`에만 있을 수 있는
> 키**인데, 그게 비어 있다. 특히 `EXPOSE_*` 3종은 **D-122가 배치별로 다르게 요구하는 값**이라
> 문서화 공백이 곧 설정 사고로 이어진다(실제로 §4.2의 조사 프로파일 기동 명령은 이 키들을
> 환경변수로 넘기는데, 그 근거를 `.env.example`에서 확인할 수 없다).

**② 유령 키 1종 — `ITAM_CONNECTION`**

`.env.example`에 `ITAM_CONNECTION`이 있으나 `config.toml`에 **`itam` 소스가 없다.** 소스명 규약이
`{name을 대문자로}_CONNECTION`이므로 이 키는 **아무 소스에도 매핑되지 않고 조용히 무시된다.**
배포자가 값을 채워도 아무 일이 일어나지 않는데, 그 사실을 알려주는 신호가 없다.

> **2026-09-22 갱신 — D-214로 해소(더는 유령 키가 아니다).** `plans/95` 구현(2026-09-17)이 `config.toml`에
> `[[sources]] name = "itam"`(`type = "mariadb"` · `:131-140`)을 추가했다. `.env.example`도 `ITAM_CONNECTION`을
> **유효 키로 다시 넣었다**(`:105` · 로컬 샌드박스 3307 예시).
> `test_defined_sources_have_documented_connection_key`의 파라미터도 `itam`을 포함한 4종이 됐다.
> 역방향 검사 `test_no_ghost_connection_keys`가 이 상태를 계속 지킨다. 아래 조치 2의 "삭제"는 당시(08-28)의 처리이고, 이후 D-214가 대체했다.

**③ 운영 존 3종 미문서화 — `POLESTAR_B0`·`POLESTAR_CM_GP`·`POLESTAR_CM_YD`**

`config.toml`은 이 세 소스를 정의하고, **실제 `mcp_server/.env`도 `POLESTAR_CM_GP_CONNECTION`·
`POLESTAR_CM_YD_CONNECTION`을 설정하고 있다**(= 지금 쓰이는 키다). 그런데 `.env.example`에는 없다.
즉 **현행 운영 설정을 예제 파일만 보고는 재현할 수 없다.**

**조치 (2026-08-28 완료)**

1. ✅ 누락 9키를 **배치별 권장값 주석과 함께** 추가 — 본체 NL→SQL 배치는 `EXPOSE_EXECUTE_SQL=true`,
   조사 배치는 `EXPOSE_EXECUTE_SQL=false`/`EXPOSE_RAW_PROMQL=false`(D-122)를 예제에 명시.
2. ✅ `ITAM_CONNECTION`은 **삭제하되 사유를 주석으로 남겼다** — itam은 오타가 아니라
   `config/db_registry.yaml`·`spec.md`에 실재하는 **계획 DB**다. 지우고 끝내면 다음 사람이 다시
   추가하므로, *"`config.toml`에 `[[sources]] name="itam"`을 먼저 추가해야 이 키가 효력을 갖는다"*를
   적어 두는 편이 정확하다.
3. ✅ `POLESTAR_CM_GP`·`POLESTAR_CM_YD`·`POLESTAR_B0` 연결 키 추가(b0는 DB2 문자열 형식).
4. ✅ **재발 방지 — 이게 본질이다.** `mcp_server/tests/test_env_example_coverage.py`(8건):

   | 테스트 | 고정하는 것 |
   |---|---|
   | `test_every_override_key_is_documented` | 코드가 읽는 키 ⊆ 예제가 제시하는 키 |
   | `test_no_ghost_connection_keys` | 예제의 `*_CONNECTION` ⊆ `config.toml` 소스 (유령 키 차단) |
   | `test_defined_sources_have_documented_connection_key` | 존 3종이 예제에 있다 (2026-09-17 D-214 이후 `itam` 포함 4종) |
   | `test_prometheus_keys_documented_together` | Prometheus 4키를 함께 문서화 |
   | `test_extractor_finds_the_known_keys` | **추출기 자가 검증** — 정규식이 빈 집합을 뽑으면 위 단언들이 공허하게 통과한다 |

   **키 목록을 테스트에 복제하지 않는다** — `inspect.getsource(_apply_env_overrides)`에서 직접
   추출한다. 복제본을 두면 **그 사본이 다음 번 누락 지점**이 된다(본체 `settings_catalog` 전수
   회귀와 같은 발상 — D-129).

> **이 테스트는 `mcp` 패키지에 의존하지 않는다.** `config.py`가 `mcp`를 임포트하지 않으므로
> 임포트 가드가 없다 — §4.3의 사고(가드 skip이 파손을 숨김)와 같은 방식으로 무력화되지 않는다.

**현재 상태**: 오버라이드 키 **14/14 문서화** · 유령 연결 키 **0** · `mcp_server` **191 passed**.

---

### 후속 후보 — 가용성 판정에 `up{nodename=…}` 편입

Plan 81(호스트 가용성 사전 판정)은 현재 폴스타 `cmm_resource.avail_status`만 본다. Prometheus가
연결되면 `up{nodename="<hostname>"}`이 **훨씬 직접적인 가용성 신호**이고, `avail_status`가
구분하지 못하는 **Power off vs 에이전트 통신 이슈**를 갈라낼 수 있다. 현재는 `plans/81` §1.3의
**명시된 한계**로 남아 있다(미착수 · 사용자 승인 없음).

---

## 9. 참조

| 문서 | 다루는 것 |
|---|---|
| `docs/02_decision.md` **D-119** | 설계 결정 · 라벨 규약 실측 · 품질 게이트 결과 |
| `docs/02_decision.md` **D-120** | 조사 LLM 데이터 통제(실 데이터 외부 송신 금지) |
| `docs/02_decision.md` **D-122** | 조사 배치의 `expose_*` 규약 |
| `docs/23` §8.2 | 실 Prometheus 연결 절차 원본 · 픽스처 포트 지도 |
| `docs/26_sre_agent_guide.md` §5.4 | 조사 프로파일 인스턴스 기동 · toolset 프로파일 표 |
| `plans/sre-agent/06-remote-vm-access.md` | 원격 VM 2축 접근 설계(§3 도구 표면 · §5-0 결정적 조립) |
| `plans/91` 1-10 (원 `plans/70` P1-1) | 게이트 측 `prometheus_enabled` 처리 — ③ 예비 코드 확정(2026-09-10 · 기한 2027-02-20) |

**주요 코드 위치**

```
mcp_server/mcp_server/promql_tools.py          도구 7종 · 셀렉터 조립 · HTTP · 감사
mcp_server/mcp_server/config.py:54,270         PrometheusConfig · env 오버라이드
mcp_server/mcp_server/server.py:133            register_promql_tools 호출
mcp_server/config.toml:27                      [prometheus] 섹션
sre_agent/sre_agent/toolset_profiles.py:239    내장 prometheus/metrics 비활성 (원격 프로파일 remote_vm_profile · bash도 off — D-233)
sre_agent/sre_agent/interface/mcp_service.py:105 _build_mcp_servers (SSE 등록 · 주입은 :137-141)
noise_gate/infrastructure/prometheus_client.py 게이트 경로 클라이언트(호출부 0건 · 예비 코드 · 판정 기한 2027-02-20)
src/config.py:713-719                          ALARM_PROMETHEUS_* 필드(예비 코드 주석 포함)
src/dbhub/client.py:414                        HOST_INSPECT_PROFILES (PromQL 프로파일 없음 · §3.4)
src/orchestration/host_inspect.py:209          inspect_host 프로덕션 호출부 (run_host_inspect)
testdata/prometheus/                           Docker 픽스처(compose·scrape·mock exporter)
```
