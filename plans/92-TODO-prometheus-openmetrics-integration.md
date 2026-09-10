# 92. Prometheus 연동의 OpenMetrics 활용 — 노출 형식(exposition format)을 읽고·내보내고·백필하는 세 경로

> **작성일**: 2026-09-10
> **성격**: 구현 계획(조사·설계 완료, 구현 전) · **상태: 계획(미구현) — 사용자 확정 게이트 G-1~G-8 대기(§9)** · 코드 0건이라 파일명 `-TODO` · **v2(2026-09-10)**: PromQL 기반 연동과의 **병행(공존) 모델** 검토 추가(§4.8)
> **요청 취지(사용자 지시 원문)**: ① *"프로메테우스 연동시 오픈매트릭을 이용하는 방법을 추가할 계획을 추가하라"* · ② (v2) *"실제 promql 기반 연동과 함께 오픈매트릭을 추가적으로 연동하는 방안에 대해 검토하여 계획서에 추가하라"* — ②는 **대체가 아니라 병행**이다: PromQL 경로를 정본으로 두고 OpenMetrics 경로를 그 옆에 붙였을 때 소스 선택·정합·전환을 어떻게 다루는가(§4.8)
> **⚠ 해석 정정(§0.1)**: "Prometheus 연동에 OpenMetrics를 쓴다"는 한 문장이지만 실측하면 **방향이 다른 세 경로**로 갈라진다 —
> ① exporter의 `/metrics`를 **직접 읽는다**(Prometheus 서버 없이) · ② 우리가 OpenMetrics를 **내보낸다**(Prometheus가 우리를
> 스크레이프) · ③ 폴스타 이력을 OpenMetrics 파일로 **백필**한다(`promtool`). 셋은 코드 위치·안전 통제·가치가 다르므로
> 한 계획에 담되 **트랙 A/B/C로 분리**하고 착수 범위는 G-1로 확정받는다. 권고는 **A 우선**(§0.3).
> **상위/선행 계획**: **D-119**(`mcp_server` = 관측 데이터 읽기 접근 경계 — 트랙 A는 이 경계의 확장) · `docs/27_prometheus_integration_guide.md`
> (연동 정본 — 본 계획 완료 시 §10 「OpenMetrics」 절 추가) · **Plan 66** 4-A / **Plan 91** 1-10·1-12(운영 Prometheus 외부 대기 ·
> 게이트측 클라이언트 예비 코드 판정 기한 2027-02-20) · **Plan 55** M0(공통 신호 모델 — OpenMetrics를 정규화 형식으로) ·
> Plan 81 §1.3(`up{nodename}` 가용성 신호 한계) · Plan 87(제니퍼 — 벤더 exporter가 OpenMetrics를 내면 같은 파서 재사용) ·
> `plans/sre-agent/06` §3(원격 VM 2축 도구 표면)
> **관련 결정**: D-003(읽기 전용 — 트랙 A는 GET만, 트랙 B는 우리 데이터 노출이라 DB 쓰기 0) · D-035(결정적=판단·LLM=서술) ·
> D-119(접근 경계 일원화) · D-120(실 데이터 외부 SaaS 송신 금지) · D-122(조사 배치 `expose_*` 규약) · D-127(과금 API 건별 승인) ·
> D-139(패키지 경계 — 트랙 A·B-2는 `mcp_server`, B-1은 `src/api`+각 패키지 infrastructure) · D-161(플래그는 만료일 동반) ·
> D-181(`mcp<2` 고정 — `custom_route`는 1.29.1 실측) · `plans/80` §5.4-③(신규 플래그 기본 off = 비트 동일)
> **신규 결정 예약**: **D-210**(§10). `docs/02_decision.md` 「채번 이력」 표에 등재(2026-09-10).
> ※ 채번 실측 2026-09-10 — `## D-` 헤더 최댓값 **209** · 「변경 이력」 표 최댓값 **209** · 「채번 이력」 표 예약(D-105·115·134·158·
> 163~168·176·195) 대조 → **D-210**.
> **실측 기준**: 아래 `file:line`·값은 2026-09-10 현 브랜치(`multiintent`, HEAD `09183c8` + 미커밋 작업 트리)에서 직접 확인했다.
> **OpenMetrics 자료 범례**: ✔ 확인(스펙·공식 문서) · △ 추정(근거 있음, 픽스처 실측 필요) · ✖ 미확인.
> 스펙은 **OpenMetrics 1.0**(CNCF 2020-11 · 2024-08 Prometheus 프로젝트로 흡수, 저장소 `prometheus/OpenMetrics`) 기준이다.

---

## 0. 요약 — 이 계획이 실제로 푸는 문제

### 0.1 요청은 한 문장, 실체는 세 트랙

| 트랙 | 방향 | 한 줄 | 코드 위치 | 선행 결정 |
|---|---|---|---|---|
| **A. 읽기(pull)** | exporter → 우리 | exporter의 `/metrics`(OpenMetrics·text 0.0.4)를 `mcp_server`가 **직접 스크레이프**해 현재값을 도구로 제공 — **Prometheus 서버 없이** | `mcp_server/mcp_server/openmetrics_tools.py`(신규) | 없음 — D-119 ①이 "관측 소스 추가는 이 경계의 확장"으로 이미 허용 |
| **B. 내보내기(exposition)** | 우리 → Prometheus | B-1 본체 자기 관측 `/metrics`(RED·LLM 호출·게이트 결정 카운터) · B-2 **폴스타 → OpenMetrics 브리지**(`nodename=server_name`을 우리가 찍는다) | B-1 `src/api/routes/metrics.py`+각 패키지 infrastructure · B-2 `mcp_server` `custom_route` | B-1 인증 방식(G-5) · B-2 카디널리티·DB 부하 상한 |
| **C. 백필(backfill)** | 폴스타 이력 → Prometheus TSDB | `cmm_metric_stat_h`를 OpenMetrics 텍스트(타임스탬프 포함)로 써서 `promtool tsdb create-blocks-from openmetrics`로 적재 — **픽스처·검증 전용** | `scripts/polestar_to_openmetrics.py`(신규) | 없음(개발 도구) |

→ 트랙 A만이 **지금 막힌 것(운영 Prometheus 부재)**을 우회한다. B·C는 Prometheus가 있을 때 가치가 생긴다.

### 0.2 왜 지금 이 계획인가 — 실측 3건

| 실측 | 결과 | 의미 |
|---|---|---|
| ① 조사 경로 PromQL 도구 | 구현·배선 완료, A/B 게이트 통과 — 그러나 **`PROMETHEUS_URL` 비어 있음**(`mcp_server/config.toml:28` `url = ""` · `mcp_server/.env`·루트 `.env` 0건 · docs/27 §0) | 코드는 끝났는데 **서버가 없어 한 번도 동작한 적이 없다**. 외부 선행조건(P0-3 · `plans/91` 1-12) 대기 |
| ② 진짜 전제 `nodename` 규약 | 고수준 도구가 `{nodename="<hostname>"}`을 조립하므로 실 Prometheus 라벨이 다르면 **조용히 빈 결과**(docs/27 §3.5 · G-6) | 라벨 규약은 **인프라 소유자 협의 사항**이라 우리가 못 정한다 |
| ③ 게이트측 클라이언트 | `noise_gate/infrastructure/prometheus_client.py` 호출부 0건 → 예비 코드 확정, 판정 기한 2027-02-20(`plans/91` 1-10) | Prometheus가 오지 않으면 삭제된다 |

OpenMetrics는 이 셋에 각각 답을 준다 — **A**는 ①을 우회한다(exporter만 있으면 현재값은 얻는다 · hostname 정합은 라벨이 아니라
**타깃 URL 매핑**이라 ②에 의존하지 않는다). **B-2**는 ②를 뒤집는다(폴스타 파생 시계열의 `nodename`은 **우리가 찍으므로** 협의가
필요 없다). **C**는 ③의 판정에 새 입력을 준다(픽스처에 폴스타 이력이 실리면 E3 baseline의 PromQL 폴백을 **실 데이터로 검증**할 수 있다).

### 0.3 한 줄 권고

**A(읽기)를 1차로, B-1(자기 관측)을 2차로, B-2·C는 운영 Prometheus 확정 뒤로.** A는 `mcp_server` 안에서 PromQL 도구와
**같은 반환 계약**(`{"data": {"resultType": "vector", …}}` · `source_kind`만 다름)을 쓰는 얇은 도구 2종이라, 소비자(`sre_agent`
자동 발견 · 본체 `inspect_host`)의 배선 변경이 0이다. 단 **A의 운영 가치는 대상 호스트에 exporter가 실재하는지(G-3)에 전적으로
달려 있다** — 폐쇄망 은행존·공동존 호스트에 node_exporter가 없다면 A는 픽스처·향후 대비 코드에 그치고 **B-2가 Prometheus 연동의
실체**가 된다. 이 한 가지가 트랙 우선순위를 뒤집을 수 있으므로 G-3을 **가장 먼저** 답해야 한다.

**병행(v2)의 한 줄**: OpenMetrics는 PromQL을 **대체하지 않는다**. PromQL이 정본(이력·집계·rate)이고 OpenMetrics는 **보완 채널** —
①Prometheus 부재·장애·미커버 호스트의 **강등 경로** ②같은 호스트를 두 소스로 읽어 `nodename` 규약 불일치를 **결정적으로 잡아내는 교차 검증**
③운영 Prometheus 편입 뒤에도 남는 **가장 신선한 현재값**. 소스 선택은 서버 사다리(코드)가 하고 LLM은 결과의 `source_kind`를 서술만 한다(D-035 · §4.8).

---

## 1. 요구 해석

### 1.1 요구 → 기능 매핑

| 사용자 요구 | 기능 | 트랙 |
|---|---|---|
| "Prometheus 연동 시 OpenMetrics를 이용" | exporter 노출 형식을 직접 소비 — Prometheus HTTP API(PromQL)의 **대체·보완 채널** | A |
| (함의) Prometheus가 우리 데이터를 보게 한다 | OpenMetrics 노출 엔드포인트 — 플랫폼 자기 관측 + 폴스타 브리지 | B |
| (함의) 이력이 없는 Prometheus를 채운다 | OpenMetrics 파일 백필 | C |

### 1.2 범위

**포함**: OpenMetrics 1.0 · Prometheus text 0.0.4 **양쪽 파싱**(실 exporter는 둘 다 낸다 — §2.3) · `mcp_server` 도구 2종 ·
설정·`.env.example`·커버리지 테스트 · 픽스처에 OpenMetrics 1.0 변형 추가 · B-1/B-2 노출 · C 스크립트 · docs/27 §10.
**제외**: OpenTelemetry/OTLP(다른 표준·수집기 필요) · remote-write(push 쓰기 경로 — 우리가 Prometheus에 **쓰는** 것이라 D-003
정신과 어긋나고 수신측 `--web.enable-remote-write-receiver` 필요) · Pushgateway(서비스 메트릭 안티패턴) · `mcp_server` 내
시계열 보관(이력은 Prometheus의 일이다 — 트랙 A는 **현재값만**) · 네이티브 히스토그램(protobuf 협상 — 텍스트 형식 밖).

### 1.3 전제·가정 (명시)

| # | 가정 | 근거 | 틀리면 |
|---|---|---|---|
| P-1 | 대상 호스트 일부에 node_exporter 또는 동등 exporter가 있거나 설치 가능하다 | ✖ 미확인 — 픽스처만 실재 | 트랙 A 운영 가치 0 → G-3에서 B-2 우선으로 전환 |
| P-2 | `prometheus-client`(pip · 순수 Python · 의존성 0)를 폐쇄망 미러에서 받을 수 있다 | △ 루트·sre_agent venv **미설치**(2026-09-10 실측) | 자체 파서(§4.2 대안 (b))로 후퇴 — 스펙 부분집합만 |
| P-3 | `mcp_server`는 단일 프로세스·단일 워커로 뜬다(`python -m mcp_server`) | ✔ `server.py` 기동 경로 | B-2 무영향. B-1은 uvicorn 다중 워커면 `PROMETHEUS_MULTIPROC_DIR` 필요 — O4에서 실측 |
| P-4 | 픽스처 exporter 2종은 Accept 협상에 따라 OpenMetrics 1.0을 낼 수 있다 | △ node_exporter는 `client_golang` promhttp 기반 · mock(nginx 정적)은 **0.0.4 고정 실측**(`mock_exporter/nginx.conf:4`) | 파서가 양쪽을 받으므로 설계 무영향 — 테스트 픽스처에 1.0 변형을 **별도 파일**로 둔다(§6) |

### 1.4 용어

- **노출 형식(exposition format)**: exporter가 `/metrics`로 내는 텍스트. **Prometheus text 0.0.4**(`text/plain; version=0.0.4`)와
  **OpenMetrics 1.0**(`application/openmetrics-text; version=1.0.0; charset=utf-8`) 두 종.
- **스크레이프**: 노출 형식을 HTTP GET으로 읽는 행위. 여기서는 Prometheus가 아니라 **`mcp_server`가** 한다(트랙 A).
- **브리지 exporter**: 원천이 Prometheus 형식이 아닌 데이터(폴스타 SQL)를 OpenMetrics로 바꿔 내는 엔드포인트(트랙 B-2).

---

## 2. OpenMetrics 조사 결과 (2026-09-10 · 출처는 §11)

### 2.1 스펙 요지 ✔

| 항목 | OpenMetrics 1.0 | Prometheus text 0.0.4 | 설계 함의 |
|---|---|---|---|
| Content-Type | `application/openmetrics-text; version=1.0.0; charset=utf-8` | `text/plain; version=0.0.4` | 파서 선택은 **응답 Content-Type**으로 결정(요청 Accept가 아님) |
| 종결자 | **`# EOF` 필수**(없으면 잘린 응답으로 간주) | 없음 | 1.0 파서는 strict — `# EOF` 부재 = 오류. 0.0.4는 관대 |
| 메타 | `# TYPE` · `# HELP` · **`# UNIT`**(신설) | `# TYPE` · `# HELP` | 카탈로그 도구가 unit을 실어 준다(LLM 단위 환각 억제) |
| 타입 | gauge · counter · **stateset** · **info** · histogram · **gaugehistogram** · summary · unknown | gauge · counter · histogram · summary · untyped | info/stateset은 라벨 나열형 — 정규화 시 값 1 게이지로 평탄화 |
| counter 표기 | 샘플명 **`_total` 필수**, 선택 `_created`(생성 시각) | 접미사 관례만 | `_created`는 소비자에게 노이즈 → 기본 제외 |
| 타임스탬프 | **초**(실수) | **밀리초** | 정규화 값은 초로 통일. 없으면 **스크레이프 시각**을 찍는다 |
| exemplar | `# {trace_id="…"} 값 ts` (counter·histogram bucket) | 없음 | 1차 무시(파서가 버려도 무방) |
| 관계 | 0.0.4의 **거의 상위 호환**(예외: `# EOF`·`_total` 강제·타임스탬프 단위) | — | 양쪽 파서 병존이 정답 |

### 2.2 Prometheus 측 동작 ✔

- 스크레이프 요청 `Accept`는 OpenMetrics 1.0을 최우선, 0.0.4를 폴백으로 협상한다(`application/openmetrics-text;version=1.0.0,
  application/openmetrics-text;version=0.0.1;q=0.75,text/plain;version=0.0.4;q=0.5,*/*;q=0.1`). 트랙 A의 스크레이퍼는 **같은 헤더를
  보낸다** — exporter 입장에서 우리가 Prometheus와 구별되지 않게.
- Prometheus 2.53(픽스처)·3.x 모두 OpenMetrics 1.0 수신 ✔. 3.0부터 UTF-8 메트릭명 허용(따옴표 표기) — 파서가 거부하지 않도록
  이름 정규식은 **경고만** 남기고 통과시킨다(고수준 `metric` 인자의 bare 이름 검증은 종전 `_METRIC_NAME_RE` 유지).
- `/federate?match[]=…`는 **텍스트 노출 형식**으로 응답한다 — Prometheus를 exporter처럼 읽는 경로. 트랙 A 파서가 그대로 먹으므로
  "Prometheus가 있으면 federate, 없으면 exporter"가 **한 파서**로 성립한다(§4.3 (c)).
- 백필: `promtool tsdb create-blocks-from openmetrics <input> [<output dir>]` — 입력은 **타임스탬프가 있는 OpenMetrics 텍스트 + `# EOF`**,
  블록을 데이터 디렉토리로 옮긴 뒤 Prometheus가 인식(2.38 이하는 `--storage.tsdb.allow-overlapping-blocks` 필요 · 2.53은 기본 허용).
  인식 시점은 재기동이 확실하다 △(컴팩션 주기 자동 인식은 픽스처 실측으로 확정).

### 2.3 Python 구현체 ✔/△

| 후보 | 내용 | 판정 |
|---|---|---|
| **`prometheus-client`**(공식 · 순수 Python · 의존성 0) | 파서 2종 — `prometheus_client.parser.text_string_to_metric_families`(0.0.4·관대) · `prometheus_client.openmetrics.parser.text_string_to_metric_families`(1.0·strict). 노출 2종 — `prometheus_client.exposition.generate_latest`(0.0.4) · `prometheus_client.openmetrics.exposition.generate_latest`(1.0, `CONTENT_TYPE_LATEST`) · `Accept` 협상 헬퍼 `choose_encoder` | **채택 권고**(G-4). **미설치** — 시그니처는 설치 후 `inspect.signature()` 실측이 선행(Known Mistakes) |
| 자체 파서(stdlib) | 스펙 부분집합(gauge·counter·histogram·`# EOF`) 300줄 내외 | P-2 불성립 시 폴백. exemplar·stateset·escape 규칙에서 스펙 이탈 위험 |
| `starlette_exporter`·`prometheus-fastapi-instrumentator` | B-1 FastAPI 자동 계측 | **비채택** — 의존성 추가 대비 이득 작음. RED 3종은 미들웨어 30줄이면 된다(`audit_middleware.py` 전례) |

★ **이름 충돌 주의**: 저장소에 `noise_gate/infrastructure/prometheus_client.py`(게이트측 HTTP 클라이언트 모듈)가 있다. pip 패키지
`prometheus_client`와 **최상위 이름이 같다**. `noise_gate.infrastructure.prometheus_client`로만 임포트되므로 실행 충돌은 없으나,
`sys.path`에 `noise_gate/infrastructure`가 직접 얹히는 환경(테스트 `rootdir` 오설정 등)에서는 pip 패키지를 가린다. O1에서 임포트
스모크(`python -c "import prometheus_client; print(prometheus_client.__file__)"`)를 테스트로 고정한다.

### 2.4 픽스처 실측 상태 (2026-09-10)

| 항목 | 값 | 출처 |
|---|---|---|
| mock exporter Content-Type | `text/plain; version=0.0.4` **고정**(nginx `default_type`) · `# EOF` 없음 | `testdata/prometheus/mock_exporter/nginx.conf:4` · `metrics.txt` |
| mock 고정값 | `mock_cpu_usage_percent{mode="user"} 97.5` · `mock_memory_used_bytes 8589934592` · `mock_oom_kills_total 3` | `metrics.txt` — 트랙 A 결정적 단언에 그대로 재사용 |
| node_exporter(target-vm) | 9101 · `hostname=svr-web-01` · Accept 협상 △ | `docker-compose.yml:22-29` |
| 픽스처 가동 | **미가동**(`docker ps` 0건) | 실측 명령은 §5 O0 |
| `nodename` 주입 방식 | 스크레이프 설정 `static_configs.labels`(수집 시점) — exporter 응답 자체에는 `nodename`이 **없다** | `prometheus.yml:12-19` |

마지막 행이 트랙 A 설계의 근거다 — **exporter를 직접 읽으면 `nodename`이 없다.** 그래서 서버가 hostname→타깃을 정하고 결과에
`nodename=<hostname>`을 **주입**한다(§4.3 (d)). Prometheus 경로와 라벨 어휘가 같아져 소비자가 두 경로를 구별할 필요가 없다.

---

## 3. 현행 실측 — 어디에 꽂히는가

### 3.1 `mcp_server` (트랙 A·B-2 소재지)

| 자산 | 위치 | 재사용 |
|---|---|---|
| PromQL 도구 7종·셀렉터 조립·반환 계약 `_ok/_err`·감사 로그 | `mcp_server/mcp_server/promql_tools.py`(575줄) | 반환 계약·감사 파이프·`make_client` 패턴·duration 파서 **그대로** |
| `PrometheusConfig` + env 오버라이드 | `config.py:54-66` · `_apply_env_overrides:268-287` | `OpenMetricsConfig` 동형 추가 |
| `.env.example` 커버리지 테스트 — `inspect.getsource(_apply_env_overrides)`에서 키 추출 | `mcp_server/tests/test_env_example_coverage.py` | 신규 키는 **그 함수 안에서** 읽어야 테스트가 본다(밖에서 읽으면 추출기가 놓친다 — docs/27 §8.1) |
| 도구 등록 배선 | `server.py:117-135` (`FastMCP(...)` → `register_*`) | `register_openmetrics_tools(mcp, expose=...)` 1줄 |
| `FastMCP.custom_route(path, methods, name=None, include_in_schema=True)` | mcp 1.29.1 **실측** | B-2 `/metrics` 라우트 |
| 폴스타 고수준 SQL 조립(최근 지표·활성 알람) | `polestar_tools.py` | B-2 브리지의 데이터 원천 |
| MockTransport 단위 + `RUN_DOCKER_IT` 통합 패턴 | `tests/test_promql_tools.py:63-81, 609-616` | 동형 복제 |
| overfit 스캔 대상 | `scripts/overfit_check.py:62`(`mcp_server/mcp_server` 포함 · `polestar_tools.py`만 EXCLUDE) | `openmetrics_tools.py`는 **범용**이어야 한다(폴스타 리터럴 0). B-2 브리지는 `polestar_exporter.py`로 분리해 EXCLUDE 대칭 등재 |

### 3.2 소비자

| 소비자 | 현재 | 트랙 A 이후 |
|---|---|---|
| `sre_agent`(HolmesGPT) | `mcp_server` 도구 **자동 발견**(`interface/mcp_service.py:93` RemoteMCPToolset) · 내장 `prometheus/metrics` toolset 비활성(`toolset_profiles.py:202`) | 배선 변경 0. 지침 1줄 — *"`prom_*`가 URL 미설정 오류를 내면 `om_*`로"* |
| 본체 채팅 `inspect_host` | `HOST_INSPECT_PROFILES` 4종 전부 폴스타 SQL(`src/dbhub/client.py:314-335`) · 프로덕션 호출부 0건(docs/27 §3.4) | `metrics_live` 프로파일 1건 추가(옵트인) — 호출부가 0건이라 **가치는 후속** |
| 게이트 E3 baseline | `polestar_metric_baseline.py` — 폴스타 `cmm_metric_stat_h`만 | 무변경(현재값만으로는 baseline 불가). C가 픽스처 검증 입력을 준다 |

### 3.3 본체 (트랙 B-1 소재지)

| 항목 | 실측 |
|---|---|
| 기존 노출 | `/health`(`src/api/routes/health.py:22`) · `/admin/noise/health` — **`/metrics` 없음**, `prometheus_client`·OTel 계측 0건 |
| 계측 후보 | 요청 RED(`audit_middleware.py` 옆) · LLM 호출 수·토큰·지연(`src/llm.py` — **과금 관측**은 D-127 운영의 실측 근거가 된다) · 파이프라인 노드 지연(`src/graph.py`) · 게이트 결정 카운터(`noise_gate` stage — `plans/54` 퍼널 축과 동일) · SSE 활성 스트림 수(`plans/89`) |
| 계층 | 레지스트리는 pip 패키지의 **프로세스 전역 `REGISTRY`**를 쓴다 — `src`·`noise_gate` 사이에 **신규 import 0**(D-139 역방향 최소 원칙). 각 패키지가 자기 infrastructure 모듈에서 메트릭을 정의하고, `src/api/routes/metrics.py`(interface)가 전역을 노출 |
| 인증 | 본체는 사용자/운영자 JWT 분리. `/metrics`는 관례상 무인증이나 여기서는 **기본 off + 운영자 토큰 또는 정적 Bearer**(G-5) |

### 3.4 갭 — 무엇이 비어 있는가

| # | 갭 | 트랙 |
|---|---|---|
| G-A1 | 노출 형식 파서·정규화 코드 0건 | A |
| G-A2 | hostname → 스크레이프 타깃 해석기 0건(허용목록·폴스타 IP 파생 모두 없음) | A |
| G-A3 | 픽스처에 OpenMetrics 1.0 응답이 없다(mock은 0.0.4 고정) | A(테스트) |
| G-B1 | 본체 `/metrics` 없음 · 계측 0 | B-1 |
| G-B2 | 폴스타 → 노출 형식 변환 0건 | B-2 |
| G-C1 | 백필 스크립트 0건 | C |
| G-D | docs/27에 OpenMetrics 절 없음 · `.env.example` 키 없음 | 문서 |

---

## 4. 설계

### 4.1 한 장 그림

```
                 ┌──────────────────────── mcp_server (관측 데이터 읽기 경계 · D-119) ────────────────────────┐
                 │                                                                                              │
 sre_agent ──MCP─┤  prom_*  ──HTTP──▶ Prometheus /api/v1/query…      (있을 때)                                  │
 본체 inspect ───┤  om_*    ──HTTP──▶ exporter  /metrics             (트랙 A · Prometheus 없어도)                │
                 │           └─ Accept 협상 → Content-Type별 파서 → 정규화(vector) → nodename 주입 → 축약        │
                 │                                                                                              │
                 │  GET /metrics  ◀──scrape── Prometheus            (트랙 B-2 · custom_route · 폴스타 브리지)    │
                 └──────────────────────────────────────────────────────────────────────────────────────────────┘
 본체 FastAPI    GET /metrics  ◀──scrape── Prometheus               (트랙 B-1 · RED·LLM·게이트 카운터)
 scripts/        polestar_to_openmetrics.py ──▶ *.om.txt ──promtool──▶ 픽스처 TSDB 블록   (트랙 C)
```

### 4.2 트랙 A — 파서·정규화 (`mcp_server/mcp_server/openmetrics.py` · 순수 함수)

- `parse_exposition(text: str, content_type: str) -> list[MetricFamily]` — Content-Type이 `application/openmetrics-text`면 1.0 strict
  파서, 그 외는 0.0.4 관대 파서. 둘 다 `prometheus_client` 위임(대안 (b) 자체 파서는 P-2 불성립 시).
- `to_instant_vector(families, *, hostname, scraped_at, metric=None, prefix=None, max_series) -> dict` — Prometheus
  `/api/v1/query` 응답과 **동형**: `{"resultType": "vector", "result": [{"metric": {"__name__": …, "nodename": hostname, …}, "value": [ts, "v"]}]}`.
  규칙: ①`nodename` 주입(exporter 응답에 없다 — §2.4) · ②`_created` 샘플 제외 · ③info/stateset은 값 1 게이지 평탄화 ·
  ④히스토그램은 `_bucket/_sum/_count`를 그대로(소비자가 PromQL 결과와 같은 모양을 기대) · ⑤타임스탬프 없으면 `scraped_at` ·
  ⑥`metric`(bare 이름 · 종전 `_METRIC_NAME_RE`) 또는 `prefix` 필터 → `max_series` 초과 시 **절단 + `truncated: true`** (침묵 절단 금지).
- `to_catalog(families) -> list[{"name", "type", "unit", "help", "series"}]` — 카탈로그 도구용.

### 4.3 트랙 A — 도구 2종 (`openmetrics_tools.py` · "적지만 더 나은 도구")

> v2: 아래 `om_*` 2종은 **소스 명시형**(항상 exporter 직결)이다. PromQL과 병행할 때의 **통합 도구·자동 사다리**는 §4.8에서 정한다 —
> `om_*`는 그 사다리의 하위 단이자 교차 검증의 두 번째 소스로 **그대로 재사용**되며, 노출 여부는 G-7에 따른다.

| 도구 | 인자 | 반환 | 비고 |
|---|---|---|---|
| `om_metric_instant` | `hostname`, `metric` **또는** `prefix`, `max_series?`(기본 200) | `_ok(data=<vector>, query="<metric>{nodename=…}", endpoint="/metrics", source_kind="openmetrics", content_type=…, target="<허용목록 이름>", truncated=…)` | PromQL `prom_metric_instant`와 **소비자 관점 동형** |
| `om_metric_catalog` | `hostname`, `prefix?` | 패밀리 목록(name·type·unit·help·series 수) | `prom_metadata` 대체. LLM이 메트릭 이름을 **추측하지 않게** |

- **(a) 타깃 해석 — SSRF 차단이 본질**: LLM은 URL을 절대 넘기지 못한다. `hostname` → 타깃은 서버 설정 **허용목록**에서만 온다.
  1차 = 정적 `[[openmetrics.targets]]`(`hostname`·`url`·선택 `auth_header`) / 2차(G-2 옵트인) = 폴스타 `cmm_resource.ipaddress` +
  기본 포트 `9100`으로 파생(`config/db_profiles/polestar.yaml:35` 실측 컬럼). 2차도 **사설 대역 허용목록(CIDR)**을 강제한다.
  미등록 hostname → `{"error": "스크레이프 타깃 미등록: <hostname>"}` (HTTP 0회).
- **(b) 클라이언트**: `make_client` 동형 — timeout 서버 강제(`scrape_timeout` 기본 10s) · **응답 크기 상한**(`max_body_bytes` 기본
  4 MiB — node_exporter 전량은 수백 KB~수 MB) · `Accept`는 §2.2의 Prometheus 헤더 그대로 · 리다이렉트 **금지**(`follow_redirects=False` —
  허용목록 우회 차단) · GET만.
- **(c) Prometheus federate 겸용**: 타깃 항목에 `kind = "federate"`를 주면 URL을 `/federate?match[]={nodename="<hostname>"}`로
  조립한다 — 같은 파서·같은 반환. Prometheus가 생기면 **설정만으로** exporter 직결에서 federate로 전환된다.
- **(d) 반환 계약 정합**: `_ok`·`_err`·감사 로그(`openmetrics audit: tool=… target=… elapsed_ms=… families=… series=… truncated=…`)를
  `promql_tools`와 공유한다(헬퍼를 `promql_tools`에서 임포트 — 순환 없음).
- **(e) 카운터 rate**: 현재값만 있으므로 rate를 낼 수 없다. **`om_metric_instant`는 counter를 그대로 돌려주고 `type: "counter"`를
  실어** LLM이 "누적값"임을 알게 한다. 2회 스크레이프 델타(`om_metric_rate(hostname, metric, interval=5s)`)는 **후속 후보**로만
  적는다(왕복 2회·슬립 — 조사 step 예산 소모).
- **(f) 게이팅**: `expose_openmetrics_tools`(기본 **false** — off면 도구 미등록 = 비트 동일) · env `EXPOSE_OPENMETRICS_TOOLS`.
  타깃 0건이면 등록은 하되 도구가 `{"error": "OpenMetrics 스크레이프 타깃 미설정"}`을 즉시 반환(PromQL URL 미설정 전례 — 침묵 폴백 금지).

### 4.4 트랙 B-1 — 본체 자기 관측 `/metrics`

| 항목 | 설계 |
|---|---|
| 라우트 | `src/api/routes/metrics.py` — `GET /metrics`, `Accept` 협상(`choose_encoder`)으로 1.0/0.0.4 응답. `METRICS_ENDPOINT_ENABLED`(기본 off · 만료일 D-161) |
| 인증 | G-5 택1 — (i) 운영자 JWT · (ii) 정적 Bearer `METRICS_BEARER_TOKEN`(D-125 `mcp_server` 전례) · (iii) 무인증+네트워크 제한. 권고 (ii) — Prometheus `authorization.credentials`로 바로 붙는다 |
| 계측(1차 5종) | `http_requests_total{route,method,status}` · `http_request_duration_seconds`(histogram) · `llm_calls_total{provider,purpose,outcome}` · `llm_tokens_total{provider,direction}` · `noise_gate_decisions_total{stage,decision}` |
| 라벨 통제 | **경로 템플릿**만(원 URL 금지 — 카디널리티·PII) · `db_id`는 허용, `hostname`·`thread_id`·사용자 식별자 **금지**(마스킹 규칙 `data_masker.py`와 대칭) |
| 계층 | 메트릭 정의는 `src/observability/metrics.py`(infrastructure)·`noise_gate/infrastructure/metrics.py` — 전역 `REGISTRY` 공유로 패키지 간 import 0. `arch_check --ci` 양쪽 0 |
| 다중 워커 | P-3 실측 후 결정 — 다중이면 `PROMETHEUS_MULTIPROC_DIR` + `multiprocess.MultiProcessCollector` |

### 4.5 트랙 B-2 — 폴스타 → OpenMetrics 브리지 (`mcp_server` `custom_route("/metrics")`)

- **내는 것(1차 3패밀리)**: `polestar_server_info{nodename,zone,db_id,ipaddress,os}` 1(info) · `polestar_metric_utilization_percent{nodename,kind}`
  (gauge — `cmm_metric_stat_h` 최근 시각 · kind ∈ `_METRIC_KINDS` 4종) · `polestar_alarm_active{nodename,severity}`(gauge — 활성 알람 수).
- **`nodename` = 폴스타 `server_name`** — 규약을 **우리가 정의**한다. node_exporter를 같은 Prometheus가 스크레이프하면
  `static_configs.labels.nodename`을 이 값에 맞추면 되므로, G-6(협의) 대상이 "인프라가 우리 규약을 따르는가"로 **단순해진다**.
- **DB 보호**: 스크레이프마다 SQL을 치지 않는다 — 응답 캐시 TTL(`bridge_cache_seconds` 기본 60) · 존(db_id)별 1쿼리 · 행 상한.
  DB 레벨 timeout·max_rows는 종전대로 `SourceConfig`가 강제(D-119 ④).
- **카디널리티**: 서버 수 × kind 4 × 존 3 — 수천 시리즈 수준. 상한 초과 시 노출을 **잘라내지 말고** `polestar_bridge_truncated 1`
  게이지로 알린다.
- **게이팅**: `expose_polestar_exporter`(기본 false) · `.env` `EXPOSE_POLESTAR_EXPORTER` · 전송 인증은 기존 `MCP_BEARER_TOKEN` 미들웨어를
  **그대로 통과**하므로 Prometheus 쪽에 `authorization` 설정 필요(docs/27 §10에 예시).
- **overfit**: 폴스타 리터럴이 있으므로 `polestar_exporter.py`로 분리해 `overfit_check.py` EXCLUDE에 `polestar_tools.py`와 대칭 등재.

### 4.6 트랙 C — 백필 스크립트 (`scripts/polestar_to_openmetrics.py`)

- 입력: `db_id`·`server_name` 목록·기간. 원천 `cmm_metric_stat_h`(폴스타 SQL — 읽기 전용 · `mcp_server` 경유 또는 direct asyncpg).
- 출력: OpenMetrics 1.0 텍스트 — `# TYPE … gauge`, 샘플마다 **초 단위 타임스탬프**, 시리즈별 시간 오름차순, 말미 `# EOF`.
  메트릭명·라벨은 **B-2와 동일 어휘**(같은 Prometheus에서 이력·현재가 이어진다).
- 적재: `promtool tsdb create-blocks-from openmetrics out.om.txt ./blocks` → 픽스처 `data/`로 이동 → 컨테이너 재기동(§2.2 △ 확정 후 조정).
- 용도: `prom_metric_range`·E3 Holt-Winters 폴백을 **실 폴스타 이력**으로 검증. 운영 Prometheus에 넣는 것은 **범위 밖**(운영 TSDB 쓰기는 인프라 소유자 결정).

### 4.7 설정 (전부 `mcp_server`, 보안 값은 `.env`)

```toml
# mcp_server/config.toml — 신규 절 (기본값 = 현행 동작과 비트 동일)
[openmetrics]
expose_openmetrics_tools = false     # om_* 도구 등록 여부 (EXPOSE_OPENMETRICS_TOOLS)
scrape_timeout = 10                  # 서버 강제 timeout(초) (OPENMETRICS_SCRAPE_TIMEOUT)
max_body_bytes = 4194304             # 응답 상한 4 MiB (OPENMETRICS_MAX_BODY_BYTES)
max_series = 200                     # 도구 반환 시리즈 상한 (OPENMETRICS_MAX_SERIES)
target_cidr_allowlist = ""           # 2차(G-2) IP 파생 시 허용 대역 CSV (OPENMETRICS_TARGET_CIDRS)
expose_polestar_exporter = false     # B-2 /metrics 브리지 (EXPOSE_POLESTAR_EXPORTER)
bridge_cache_seconds = 60            # B-2 응답 캐시 TTL (OPENMETRICS_BRIDGE_CACHE_SECONDS)

[[openmetrics.targets]]              # 1차 정적 허용목록 — hostname = 폴스타 server_name
hostname = "svr-web-01"
url = "http://localhost:9102/metrics"   # 픽스처 mock(0.0.4) · node_exporter는 9101
# kind = "exporter" | "federate"
# auth_header = ""                   # 보안 값은 .env: OPENMETRICS_TARGET_<HOSTNAME_UPPER>_AUTH_HEADER
```

- 환경변수 오버라이드는 **`_apply_env_overrides` 안에서** 읽는다(커버리지 테스트 추출 범위) · `.env.example`에 7키 + 타깃 예시 문서화 ·
  `test_openmetrics_keys_documented_together` 신설(Prometheus 4키 테스트 동형).
- 본체(B-1): `ObservabilityConfig`(`src/config.py` nested · `Field(default_factory=…)`)에 `metrics_endpoint_enabled`·`metrics_bearer_token` —
  `settings_catalog.py`·`config/settings_help/*.yaml` 동반 등재(D-129 전수 회귀).


### 4.8 ★ PromQL 기반 연동과의 병행 — 채널 공존 모델 (v2 · 사용자 지시 ②)

#### 4.8.1 두 채널은 무엇이 같고 무엇이 다른가 (실측·스펙 기준)

| 축 | PromQL 경로(`prom_*` · 정본) | OpenMetrics 경로(`om_*` · 보완) | 병행 함의 |
|---|---|---|---|
| 데이터 원천 | Prometheus TSDB(스크레이프·보존 15d 픽스처) | exporter 응답 **그 순간** | 이력·집계는 PromQL만. 현재값은 둘 다 |
| 값의 시각 | instant 쿼리는 **lookback 5m 안의 마지막 샘플**(Prometheus 기본 `query.lookback-delta`) — 스크레이프가 죽어도 최대 5분간 **낡은 값이 성공으로** 온다 | 응답 시각 = 스크레이프 시각(정확히 지금) | ★두 값의 **타임스탬프 차이**가 "Prometheus 스크레이프 정지"의 결정적 신호가 된다 |
| 라벨 | `job`·`instance` + 스크레이프 설정의 `nodename`(인프라가 찍음) | exporter 라벨만 — `nodename`은 **서버가 주입** | 교차 비교 시 `job`·`instance`는 제외하고 `__name__`+나머지 라벨로 정렬 |
| hostname 정합 | `{nodename="…"}` — 라벨 규약이 틀리면 **빈 결과·성공**(docs/27 §3.5) | 타깃 URL 매핑 — 규약 무관 | ★PromQL 빈 결과 + OM 데이터 있음 = **규약 불일치 진단**(G-6의 실측을 도구가 대신한다) |
| rate·집계 | `rate()`·`avg_over_time` 등 원시 도구(옵트인)로 가능 | 불가 — counter 누적값만 | rate 요구는 PromQL로만 라우팅(§4.8.3 규칙 R3) |
| 커버리지 | Prometheus가 스크레이프하는 호스트만 | 허용목록에 있는 호스트 | 서로 다른 호스트 집합일 수 있다 — **호스트별 가용 소스 표**가 필요(§4.8.4) |
| 부하 | TSDB 조회 1회 | exporter GET 1회(수백 KB) + 파싱 | 교차 검증은 2회 — 기본 off, 명시 요청 시만 |
| 실패 모드 | URL 미설정 · HTTP 오류 · **조용한 빈 결과** | 타깃 미등록 · HTTP 오류 · 잘린 응답(`# EOF` 부재) | 사다리는 오류뿐 아니라 **빈 결과도 강등 사유로 취급**할지 정책이 필요(G-8) |

#### 4.8.2 도구 표면 — 세 가지 공존 형태 (G-7)

| 형태 | 도구 | 소스 선택 주체 | 장점 | 단점 |
|---|---|---|---|---|
| **(α) 병존** | `prom_*` 2종 + `om_*` 2종 그대로 | **LLM** | 구현 최소(각자 독립) | R-4 혼동 — 어느 것을 부를지 LLM이 정한다(D-035 위반 성격) · 지침 의존 |
| **(β) 통합** | `metric_instant(hostname, metric, source="auto")` · `metric_range(hostname, metric, window, step)` · `metric_catalog(hostname)` — `prom_*`·`om_*`는 내부 구현으로 숨김 | **서버 사다리** | LLM은 소스를 모른다(D-119 ③과 같은 결) · 반환에 `source_kind`·`fallback_reason` 동반 | 기존 `prom_*` 이름을 바꾸면 `ab_promql_gate.py`·지침·docs/27 §5 전부 개정 · 원시 `prom_query`류는 어차피 Prometheus 전용 |
| **(γ) 하이브리드(권고)** | 기존 `prom_*` **이름·계약 유지** + `source` 인자 추가(`"auto"` 기본 · `"prometheus"` · `"exporter"`) + `om_metric_catalog`만 별도 노출 · `cross_check` 인자(§4.8.5) | 서버 사다리, 필요 시 LLM이 명시 | 회귀 0(`source` 미지정 = 종전과 비트 동일) · 도구 수 +1(카탈로그)뿐 · 전환 단계(§4.8.6)마다 설정만 바뀐다 | `prom_` 접두가 exporter 결과도 낸다 — 이름 의미가 넓어짐(반환의 `source_kind`가 진실을 말한다) |

**권고 (γ)**. 근거: ①D-035 — 소스 선택은 결정적 코드가 한다 ②D-119 A/B 게이트가 통과한 도구 이름·계약을 깨지 않는다 ③`source="auto"`의
기본 사다리는 설정으로 꺼진 상태(G-8)라 **현행 동작과 비트 동일**로 출발한다. (α)는 `EXPOSE_OPENMETRICS_TOOLS=true`로 언제든 병존
형태로도 노출 가능하므로 (γ)의 부분집합이다.

#### 4.8.3 서버 소스 사다리 — `source="auto"`의 결정 규칙 (결정적 · 기동 시 1회 해석)

```
metric_instant(hostname, metric, source="auto")
  R1  Prometheus URL 설정됨 AND 호스트가 Prometheus 커버리지(§4.8.4)에 있음 → PromQL 시도
        성공·비어있지 않음 → 반환(source_kind="prometheus")
        HTTP/timeout 오류            → [fallback_policy ∈ {on_unavailable, on_empty}] exporter 시도, fallback_reason="prometheus_error"
        성공·빈 결과                 → [fallback_policy == on_empty]           exporter 시도, fallback_reason="prometheus_empty"
  R2  Prometheus 미설정 OR 호스트 미커버 → 허용목록에 타깃 있으면 exporter(source_kind="openmetrics", fallback_reason="prometheus_unavailable"/"host_not_covered")
  R3  range·rate 요구(metric_range · 원시 prom_query*)는 Prometheus 전용 — 없으면 구조화 오류 {"error": "…", "hint": "현재값은 metric_instant(source=auto)"}
  R4  두 소스 모두 불가 → {"error": "조회 가능한 소스 없음", "tried": [...]}  (침묵 폴백 금지 · I-3)
  R5  강등은 도구 호출당 최대 1회(왕복 2회 상한) — 조사 step 예산 보호
```

- `fallback_policy`(설정 · G-8): `off`(기본 — R1의 강등 없음 = 종전 동작) · `on_unavailable`(오류·미설정·미커버 시만) · `on_empty`(빈 결과도 강등).
  `on_empty`가 §3.5의 "조용한 빈 결과"를 실제로 잡지만, **정당한 빈 결과**(그 메트릭이 원래 없음)에도 exporter GET이 1회 더 나간다.
- `fallback_reason`은 반환 JSON 최상위에 **항상** 실린다(강등 없으면 `null`). 감사 로그에도 같은 필드 — 운영에서 "얼마나 자주 강등되는가"가
  곧 Prometheus 연동 건강도다.
- 강등 결과는 PromQL 결과와 **같은 vector 모양**이지만 `job`·`instance`가 없고 `nodename`이 주입값이다(§4.2). 소비자는 `source_kind`로 구별한다.

#### 4.8.4 호스트별 가용 소스 표 — 커버리지 불일치를 명시한다

두 채널이 보는 호스트 집합은 다르다(Prometheus는 스크레이프 설정, exporter는 우리 허용목록). 서버는 기동 시 표를 만든다:

| 출처 | 방법 | 갱신 |
|---|---|---|
| Prometheus 커버리지 | `GET /api/v1/label/nodename/values`(읽기 API · D-003) → 집합. 실패 시 "미상"으로 두고 R1은 시도 후 결과로 판단 | 기동 시 + TTL(기본 10분) |
| exporter 커버리지 | `[[openmetrics.targets]]` 허용목록(+G-2 (ii) 파생) | 기동 시 |

`metric_catalog(hostname)`은 이 표를 함께 돌려준다(`sources_available: ["prometheus","exporter"]`) — LLM이 "이 호스트는 exporter만
있다"를 **추측이 아니라 사실로** 안다. Plan 81 §1.3의 가용성 한계(`up{nodename}`)도 이 표로 갈린다: Prometheus 커버·`up==0` = 스크레이프
실패, exporter 직결 성공 = 호스트 생존 → **"Power off vs 에이전트 통신 이슈"** 구분이 두 채널 병행에서 처음 가능해진다.

#### 4.8.5 교차 검증 — 두 소스를 같이 읽어 규약·신선도를 결정적으로 판정

`metric_instant(..., cross_check=true)`(기본 false · 왕복 2회) 또는 운영 스크립트 `scripts/prom_om_cross_check.py`(호스트 목록 일괄):

| 판정 | 조건(결정적) | 의미 | 조치 안내(반환 `diagnosis`) |
|---|---|---|---|
| `label_mismatch` | PromQL 빈 결과 **AND** exporter 데이터 있음 **AND** Prometheus 커버리지에 유사 값 존재(예: FQDN vs 단축명 — 정규화 후 일치) | `nodename` 규약 불일치(docs/27 §6.1 ④의 실체) | "스크레이프 설정 `nodename` 정규화 필요 — 후보: <값>" |
| `not_scraped` | PromQL 빈 결과 **AND** exporter 있음 **AND** 커버리지에 후보 없음 | Prometheus가 이 호스트를 안 긁는다 | "타깃 등록 필요 — 인프라 협의" |
| `stale` | PromQL 값 타임스탬프가 `now − scrape_interval×3`보다 오래됨(값은 lookback 덕에 성공으로 옴) **AND** exporter 신선 | 스크레이프 정지 | "Prometheus 타깃 상태 확인(`up`)" |
| `value_drift` | 같은 시리즈(`job`·`instance` 제외 정렬) gauge 값 차가 허용 오차(기본 상대 5% 또는 절대 ε) 초과 **AND** 두 타임스탬프 차 < scrape_interval | 이례 — 계측 차이·exporter 다중 인스턴스 | 수치 그대로 보고(판단 유보) |
| `consistent` | 위 어느 것도 아님 | 정상 | — |

- 판정은 **전부 코드**(D-035). LLM은 `diagnosis`를 서술만 한다. 결과에는 두 소스의 원 값·타임스탬프·비교 시리즈 수가 실린다.
- 이 표가 docs/27 §6.1 「라벨 규약 실측 5단계」를 **도구 한 번**으로 대체한다 — 운영 Prometheus 편입 시 첫 실측이 "curl 5개"가 아니라
  `prom_om_cross_check.py --hosts <대표 3건>`이 된다.

#### 4.8.6 전환 단계 — OpenMetrics는 어느 단계에서도 버려지지 않는다

| 단계 | 상태 | `fallback_policy` | OpenMetrics 역할 |
|---|---|---|---|
| **S0** 지금 | Prometheus 없음 | (무관 — R2) | **유일한 현재값 채널**(exporter 있는 호스트만) |
| **S1** 편입 초기 | 운영 Prometheus 붙음 · 커버리지 부분 · `nodename` 규약 미확정 | `on_empty` | 미커버 호스트 강등 + **교차 검증으로 규약 확정**(§4.8.5) |
| **S2** 안정 | 커버리지 전체 · 규약 확정 | `on_unavailable` | Prometheus 장애 시 강등 채널 · 주기 교차 검증(규약 **회귀** 감시) · 가장 신선한 현재값 |
| **S3** (선택) | B-2 브리지도 Prometheus가 스크레이프 | `on_unavailable` | 폴스타 파생 시계열과 exporter 시계열이 **같은 `nodename` 어휘**로 한 TSDB에 — PromQL로 교차 조인 가능 |

단계 이동은 **설정 2개**(`PROMETHEUS_URL` · `OPENMETRICS_FALLBACK_POLICY`)로 끝나고 코드는 바뀌지 않는다. 게이트측 예비 클라이언트
(`plans/91` 1-10 · 기한 2027-02-20)는 S1 도달 시 docs/27 §3.3.3 배선으로 넘어가며, 그 판정에 §4.8.5 교차 검증 결과가 실측 근거로 첨부된다.

#### 4.8.7 병행 시 추가되는 통제·설정·테스트

- **설정(§4.7 추가)**: `fallback_policy = "off"`(env `OPENMETRICS_FALLBACK_POLICY`) · `coverage_ttl_seconds = 600` · `cross_check_tolerance = 0.05` ·
  `scrape_interval_hint = 15`(stale 판정 기준 — Prometheus `status/config`에서 읽을 수 있으면 실측값 우선).
- **불변식 추가**: I-6 `source` 미지정·`fallback_policy=off`면 `prom_*` 요청·응답이 **바이트 동일**(스냅샷 테스트) · I-7 강등은 호출당 1회.
- **테스트(O2b)**: 사다리 매트릭스 — (URL 설정 × 커버리지 포함 × PromQL 결과 {성공·빈·오류} × 타깃 {있음·없음} × 정책 3종) 전 조합을
  MockTransport 2개(Prometheus·exporter)로 고정 · `fallback_reason` 값 단언 · 교차 검증 5판정 각 1건 이상(픽스처 mock 고정값으로
  `consistent`, `nodename` 다른 두 스텁으로 `label_mismatch`, 타임스탬프 조작으로 `stale`) · Docker 통합은 픽스처 Prometheus(9190)와 mock(9102)을
  **동시에** 읽어 `consistent`를 실증.
- **지침 1문장(결정적)**: *"메트릭 도구 결과의 `source_kind`가 `openmetrics`면 Prometheus 대신 exporter에서 직접 읽은 현재값이며 이력·rate는
  없다. `fallback_reason`을 그대로 언급하라."*

---

## 5. 구현 계획 — Wave O0 ~ O6

| Wave | 내용 | 산출물 | verify | 의존 |
|---|---|---|---|---|
| **O0** 실측·스펙 고정 | ①픽스처 기동 → `curl -H 'Accept: application/openmetrics-text;version=1.0.0' :9101/metrics -D-`로 node_exporter 협상 결과 확정(P-4) ②`pip install prometheus-client` → `inspect.signature()` 4함수 실측 ③임포트 스모크(§2.3 이름 충돌) ④G-3 답변 | §2.3 표 갱신 · `docs/18` 필요 시 | 실측 값이 계획서에 기재 | G-3·G-4 |
| **O1** 파서·정규화 | `openmetrics.py` 순수 함수 3종 · 골든 픽스처 `testdata/openmetrics/{om_1_0_full.txt, text_0_0_4.txt, truncated_no_eof.txt}` | 단위 테스트 ≥ 20(전 타입·`# EOF` 부재·`_created` 제외·nodename 주입·절단 플래그·UTF-8 이름) | `pytest mcp_server/tests/test_openmetrics_parse.py` | O0 |
| **O2** 도구·설정 | `openmetrics_tools.py` 2종 · `OpenMetricsConfig` · env 오버라이드 · `.env.example` · 서버 배선 · 픽스처 `mock_exporter/metrics_om.txt` + nginx 두 번째 location(`/metrics-om`, 1.0 Content-Type) | MockTransport 단위 ≥ 25(허용목록 외 → HTTP 0회 · 리다이렉트 거부 · 크기 상한 · timeout · Accept 헤더 · 반환 동형 · flags-off 미등록) · `RUN_DOCKER_IT=1` 통합(mock 고정값 3종 문자열 대조) · `test_env_example_coverage` 통과 | `cd mcp_server && ../.venv/bin/python -m pytest -q` · overfit 0 | O1 |
| **O2b** 병행 사다리(v2) | `prom_*`에 `source`·`cross_check` 인자 · 소스 사다리·커버리지 표·교차 검증 5판정 · `fallback_policy` 설정 · `scripts/prom_om_cross_check.py` | 사다리 매트릭스 MockTransport 2개 · 바이트 동일 스냅샷(`source` 미지정·정책 off) · 5판정 각 ≥1 · Docker 동시 읽기 `consistent` | `pytest mcp_server/tests/test_metric_source_ladder.py` | O2 · G-7·G-8 |
| **O3** 소비 배선 | `sre_agent` 조사 지침 1줄(`investigation_guidance` — 결정적 문장) · 본체 `HOST_INSPECT_PROFILES["metrics_live"]`(옵트인·`identifier: server_name`) · docs/27 §10 | 지침 렌더 스냅샷 · `inspect_host` 인자 검증 테스트 | `pytest tests/test_dbhub_integration.py` · sre_agent 회귀 | O2 |
| **O4** 트랙 B-1 | `src/observability/metrics.py` · `noise_gate/infrastructure/metrics.py` · `src/api/routes/metrics.py` · 설정 3곳 | RED 3종 미들웨어 테스트 · 협상 2형식 · 인증 거부 · 라벨 금지 목록 · flags-off 라우트 404 · `arch_check --ci` | `pytest tests/test_api -q` | G-5 |
| **O5** 트랙 B-2 | `mcp_server/mcp_server/polestar_exporter.py` · `custom_route` 배선 · overfit EXCLUDE | 노출 텍스트 골든(3패밀리·`# EOF`) · 캐시 TTL · 절단 게이지 · Bearer 통과 · 픽스처 Prometheus가 실제 스크레이프(`up{job="polestar"}==1`) | `RUN_DOCKER_IT=1` | O2 · Prometheus 확정 |
| **O6** 트랙 C | `scripts/polestar_to_openmetrics.py` · `promtool` 런북 | 출력이 O1 strict 파서를 통과 · 픽스처 적재 후 `prom_metric_range` 왕복 | 수동 런북 + 단위 | O5 어휘 |

**착수 순서 권고**: O0 → O1 → O2 → O2b → O3(여기까지가 트랙 A+병행 · **실 LLM 호출 0**) → G-1 재확인 → O4 → O5 → O6.
**과금 경계**: 전 Wave가 D-127 무과금이다. 유일한 과금 항목은 O3 뒤 **조사 LLM 실 완주 확인**(`ab_promql_gate.py` 동형으로
`om_*` 경로 1회 — 픽스처 고정값 결정적 대조)이며 건별 승인 뒤에만 한다. 픽스처 데이터는 실 데이터가 아니므로 D-120 무저촉.

---

## 6. 산출물·파일 배치

```
mcp_server/mcp_server/openmetrics.py             파서·정규화·카탈로그 (순수 함수 · 폴스타 리터럴 0)
mcp_server/mcp_server/openmetrics_tools.py       om_metric_instant · om_metric_catalog · 타깃 해석 · 클라이언트 · 감사
mcp_server/mcp_server/polestar_exporter.py       (O5) B-2 브리지 — overfit EXCLUDE 대칭
mcp_server/mcp_server/config.py                  OpenMetricsConfig · targets · env 오버라이드(함수 안)
mcp_server/config.toml · .env.example            [openmetrics] 절 · 7키 + 타깃 예시
mcp_server/tests/test_openmetrics_parse.py       O1
mcp_server/tests/test_openmetrics_tools.py       O2 (MockTransport + RUN_DOCKER_IT)
mcp_server/mcp_server/metric_source.py           (O2b) 소스 사다리·커버리지 표·교차 검증 판정 (순수 함수 + 얇은 I/O)
mcp_server/tests/test_metric_source_ladder.py    O2b 매트릭스·바이트 동일 스냅샷
scripts/prom_om_cross_check.py                   (O2b) 호스트 일괄 교차 검증 — docs/27 §6.1 5단계 대체
mcp_server/tests/test_env_example_coverage.py    test_openmetrics_keys_documented_together 추가
testdata/openmetrics/*.txt                       골든 픽스처 3종
testdata/prometheus/mock_exporter/metrics_om.txt · nginx.conf   OpenMetrics 1.0 변형 location
src/observability/metrics.py · src/api/routes/metrics.py · noise_gate/infrastructure/metrics.py   (O4)
scripts/polestar_to_openmetrics.py               (O6)
docs/27_prometheus_integration_guide.md §10      OpenMetrics — 세 트랙·설정·검증·federate 전환
```

의존성: `mcp_server/pyproject.toml`에 `prometheus-client>=0.20`(G-4) · 루트 `pyproject.toml` extra `metrics`(O4 시).

---

## 7. 안전 통제·거버넌스

### 7.1 불변식

| # | 불변식 | 강제 지점 |
|---|---|---|
| I-1 | LLM은 URL·라벨을 만들지 않는다 — `hostname`만 넘기고 타깃·라벨은 서버가 정한다(D-119 ③ 확장) | 허용목록 해석기 · `nodename` 주입 |
| I-2 | 읽기 전용 — GET · 리다이렉트 금지 · Prometheus 쓰기 API 미노출 · B-2는 DB SELECT만(D-003) | `make_client` · 라우트 표면 |
| I-3 | 침묵 폴백 금지 — 타깃 미등록·미설정·절단·`# EOF` 부재는 전부 **구조화 오류/플래그**로 반환 | `_err` · `truncated` |
| I-4 | 플래그 기본 off = 비트 동일(도구 미등록·라우트 부재) · 만료일 동반(D-161) | 등록 분기 · 테스트 |
| I-5 | 실 데이터 × 외부 SaaS 금지(D-120) — `om_*` 결과도 PromQL과 같은 통제 | 조사 LLM 백엔드 판정표(docs/23 §8.0) |
| I-6 | (v2) `source` 미지정 + `fallback_policy=off` = `prom_*` 요청·응답 **바이트 동일** | 스냅샷 테스트 |
| I-7 | (v2) 소스 강등은 호출당 최대 1회 · 교차 검증은 명시 요청 시만 | 사다리 R5 |

### 7.2 신뢰 경계·SSRF

exporter 스크레이프는 **서버가 임의 HTTP를 치는 기능**이라 SSRF 표면이다. 통제 4겹 — ①허용목록 밖 hostname은 HTTP 0회
②2차 IP 파생은 CIDR 허용목록 강제(루프백·링크로컬·메타데이터 대역 **명시 거부**) ③리다이렉트 금지 ④응답 크기·timeout 상한.
B-1/B-2 노출은 **인증 뒤**에만(무인증 노출은 G-5 (iii) 명시 선택 시에만·네트워크 제한 전제).

### 7.3 PII·마스킹

노출 형식 라벨에 사용자·스레드·원 URL 금지(§4.4). exporter 응답의 라벨은 그대로 통과하되 도구 결과가 조사 LLM으로 가는 경로는
PromQL과 동일하므로 추가 마스킹 없음. B-2 `ipaddress` 라벨은 **info 패밀리 한정**·`EXPOSE_POLESTAR_EXPORTER` 뒤(운영자 판단).

### 7.4 부하 가드

exporter: 도구 호출당 GET 1회·크기 상한 · 브리지: 캐시 TTL·존별 1쿼리·DB timeout은 `SourceConfig` · 본체 `/metrics`: 레지스트리
직렬화만(DB 0).

---

## 8. 리스크·미해결

| # | 리스크 | 영향 | 대응 |
|---|---|---|---|
| R-1 | **운영 호스트에 exporter가 없다**(P-1) | 트랙 A 운영 가치 0 | G-3 선답 · 없으면 B-2 우선으로 재편(§0.3) |
| R-2 | 폐쇄망에서 `prometheus-client` 미러 부재 | O1 지연 | 자체 파서 폴백(§2.3 (b)) — 스펙 부분집합 명시 |
| R-3 | node_exporter 전량 응답이 커서 LLM 컨텍스트 소모 | 조사 step 낭비 | 서버측 `metric/prefix` 필터·`max_series`·카탈로그 도구 선행 |
| R-4 | LLM이 `prom_*`와 `om_*`를 혼용·혼동 | 조사 품질 | **(γ) 하이브리드로 근본 해소** — 소스 선택을 서버 사다리로(§4.8.2) + `source_kind` 서술 지침 1줄 + D-119 동형 A/B 1회(승인) |
| R-9 | (v2) `on_empty` 정책이 정당한 빈 결과에도 exporter GET을 1회 더 낸다 | 부하·지연 | 기본 `off` · S1(규약 미확정) 기간만 `on_empty` 권고(§4.8.6) · 호출당 1회 상한 |
| R-10 | (v2) 두 소스 값이 다를 때 LLM이 한쪽을 임의 채택 | 서술 오류 | `value_drift`는 판단 유보로 **두 값 모두** 반환·지침에 "수치 그대로" 명시(D-035) |
| R-5 | counter 현재값을 rate로 오독 | 서술 오류 | `type` 동반 반환 · 지침 "누적값" 문장 |
| R-6 | B-2 카디널리티 폭증 | Prometheus 메모리 | 절단 게이지 · kind 4종 고정 · 존별 상한 |
| R-7 | 이름 충돌(`noise_gate/.../prometheus_client.py` vs pip) | 오임포트 | O0 스모크 테스트 고정 |
| R-8 | 백필 블록 인식 시점 불확실(△) | C 런북 오류 | 픽스처 실측으로 확정 후 런북 기재 |

---

## 9. 사용자 확정 게이트

| 게이트 | 질문 | 선택지 | 권고 |
|---|---|---|---|
| **G-1** 범위 | 어느 트랙까지 착수하는가 | (a) A만 · (b) A+B-1 · (c) A+B 전부 · (d) 전부(C 포함) | **(b)** — A는 지금 막힌 것을 풀고, B-1은 Prometheus 없이도 D-127 과금 관측·plans/54 퍼널 계측 가치 |
| **G-2** 타깃 해석 | hostname → exporter를 어떻게 정하는가 | (i) 정적 허용목록만 · (ii) (i)+폴스타 `ipaddress`:9100 파생(CIDR 허용목록) | **(i)로 시작**, (ii)는 G-3 결과가 "대다수 호스트에 exporter 있음"일 때만 |
| **G-3** 전제 | 운영(은행존·공동존) 호스트에 node_exporter 등 exporter가 **있는가 / 설치 계획이 있는가** | 있음 · 일부 · 없음 · 모름 | **모름이면 인프라 소유자에게 1문항 확인이 선행** — 답에 따라 A/B-2 우선순위가 바뀐다(§0.3) |
| **G-4** 의존성 | `prometheus-client` pip 추가 승인(순수 Python·의존성 0) | 승인 · 자체 파서 | **승인** |
| **G-5** B-1 인증 | `/metrics` 보호 방식 | (i) 운영자 JWT · (ii) 정적 Bearer · (iii) 무인증+네트워크 제한 | **(ii)** — Prometheus `authorization` 설정과 직결 |
| **G-6** 등재 시점 | D-210 본문 등재를 언제 | O2 완료(트랙 A 코드 실재) 시 · 전 트랙 완료 시 | **O2 완료 시**, 트랙 B·C는 부기 |
| **G-7** 병행 도구 표면(v2) | PromQL과 OpenMetrics를 어떤 형태로 공존시키는가(§4.8.2) | (α) 병존 · (β) 통합 · (γ) 하이브리드 | **(γ)** — `prom_*` 이름·계약 유지 + `source="auto"` 사다리 + `om_metric_catalog` |
| **G-8** 강등 정책 기본값(v2) | `fallback_policy` 코드 기본값(§4.8.3) | `off` · `on_unavailable` · `on_empty` | **`off`**(비트 동일 원칙 `plans/80` §5.4-③) — 운영 `.env`에서 S1은 `on_empty`, S2는 `on_unavailable`(사용자 확정 설정은 코드 기본값으로 고정하는 관례가 있으므로 S2 도달 시 재판단) |

---

## 10. 신규 결정 예약 — D-210 (등재는 G-6 시점)

> **D-210. Prometheus 연동의 OpenMetrics 활용 — 노출 형식 직접 읽기 도구(`om_*`) · 자기 관측/폴스타 브리지 노출 · 픽스처 백필**
> - **결정(예정)**: ① `mcp_server`에 OpenMetrics/text 0.0.4 스크레이프 도구 2종(`om_metric_instant`·`om_metric_catalog`) — Prometheus
>   서버 없이 exporter 현재값 조회, **타깃은 서버 허용목록에서만 해석**(LLM URL 미취급 · SSRF 4겹) · `nodename=<hostname>` 서버 주입으로
>   PromQL 경로와 반환 계약 동형(`source_kind="openmetrics"`) · federate 겸용 · 기본 off. D-119 ①의 "관측 소스 추가 = 경계 확장" 적용. **(v2) PromQL 병행**: `prom_*` 이름·계약 유지 + `source="auto"` 서버 사다리(강등 사유 `fallback_reason` 항상 동반 · 호출당 1회) + 호스트별 가용 소스 표 + 교차 검증 5판정(`label_mismatch`·`not_scraped`·`stale`·`value_drift`·`consistent` — 코드 판정, LLM 서술만) · `fallback_policy` 기본 `off`(비트 동일).
>   ② 본체 `/metrics`(RED·LLM 호출·게이트 결정 — 기본 off·Bearer)와 `mcp_server` 폴스타 브리지(`nodename=server_name` 규약을 우리가
>   정의 · 캐시 TTL · 절단 게이지 — 기본 off). ③ `cmm_metric_stat_h` → OpenMetrics 파일 → `promtool` 백필은 **픽스처·검증 전용**.
> - **근거**: 운영 Prometheus 부재(P0-3 외부 대기)로 PromQL 경로가 한 번도 동작하지 못한 상태를 코드 측에서 우회 · `nodename` 협의(G-6)
>   의존 축소 · 결정적 가드 우선(D-035) · 침묵 폴백 금지.
> - **대안 기각**: OTLP(다른 표준·수집기) · remote-write(쓰기 경로) · Pushgateway(안티패턴) · `mcp_server` 내 시계열 보관(이력은 Prometheus 소관).
> - **관련**: D-003 · D-119 · D-120 · D-122 · D-139 · D-161 · D-181 · `plans/91` 1-10·1-12 · `plans/81` §1.3 · `plans/55` M0.

---

## 11. 참고

### 11.1 스펙·공식 문서 (✔)
- OpenMetrics 1.0 specification — `prometheus/OpenMetrics` 저장소 `specification/OpenMetrics.md`(텍스트 형식·타입·`# EOF`·exemplar·`_created`).
- Prometheus docs — *Exposition formats*(text 0.0.4·OpenMetrics 협상) · *Backfilling from OpenMetrics format*(`promtool tsdb create-blocks-from openmetrics`) ·
  *HTTP API — federation*(`/federate`) · *Prometheus 3.0 migration*(UTF-8 이름).
- `prometheus/client_python` README — `prometheus_client.openmetrics.{parser,exposition}` · `parser.text_string_to_metric_families` · `choose_encoder` · 다중 프로세스 모드.
- OpenMetrics → Prometheus 흡수 공지(2024-08, Prometheus 블로그/저장소 README).

### 11.2 내부 참조
`docs/27_prometheus_integration_guide.md`(§0·§2·§3.5·§5.3·§8 G-5·G-6) · `docs/02_decision.md` D-119·D-120·D-122·D-181 · `plans/91` 1-10·1-12 ·
`plans/66` 4-A · `plans/55` §2·§8 · `plans/81` §1.3(가용성 — §4.8.4가 Power off/통신 이슈 구분을 연다) · `plans/87` §3.1 · `plans/sre-agent/06` §3·§5-0 · `mcp_server/tests/test_env_example_coverage.py` ·
`testdata/prometheus/*`.

---

## 12. 변경 이력

| 일자 | 내용 | 근거 |
|---|---|---|
| 2026-09-10 | v1 신설 — 사용자 지시("프로메테우스 연동시 오픈매트릭을 이용하는 방법을 추가할 계획을 추가하라") · 세 트랙 분리·A 우선 권고 · 실측(PromQL URL 미설정 · `prometheus-client` 미설치 · mock 0.0.4 고정 · `custom_route` 1.29.1 실재 · 픽스처 미가동) · G-1~G-6 · D-210 예약(채번 이력 표 등재) · 코드 0건 `-TODO` | 본 세션 실측 |
| 2026-09-10 | v2 — 사용자 지시("실제 promql 기반 연동과 함께 오픈매트릭을 추가적으로 연동하는 방안에 대해 검토하여 계획서에 추가하라") → **§4.8 병행(공존) 모델 신설**: 두 채널 차이표(lookback 5m 낡은 값·`job/instance`·커버리지 불일치) · 도구 표면 3형태 중 **(γ) 하이브리드 권고**(`prom_*` 유지 + `source="auto"`) · 서버 소스 사다리 R1~R5 · 호스트별 가용 소스 표 · **교차 검증 5판정**(docs/27 §6.1 5단계를 도구 1회로 대체 · Plan 81 Power off/통신 구분) · 전환 단계 S0~S3(설정 2개로 이동) · O2b Wave · I-6·I-7 · R-9·R-10 · **G-7·G-8** 신설 · D-210 예약 문구 보강 | 본 세션 검토 |
