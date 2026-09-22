# 92. Prometheus 연동의 OpenMetrics 활용 — 노출 형식(exposition format)을 읽고·내보내고·백필하는 세 경로

> **작성일**: 2026-09-10
> **성격**: 구현 계획(조사·설계 완료, 구현 전) · **상태: 계획(미구현) — 사용자 확정 게이트 G-1~G-8 대기(§9)** · 코드 0건이라 파일명 `-TODO` · **v2(2026-09-10)**: PromQL 기반 연동과의 **병행(공존) 모델** 검토 추가(§4.8)
> · **v3(2026-09-22) — 현 구현 심층 재실측 · 적정성 판정(§0.0)**: 방향은 유지한다. 사실 전제 정정 12건, 설계 결함 7건 수정, **범위 재편**(S0 = 트랙 A 최소형 · O2b는 S1 트리거 뒤 · B-1·C 보류)을 반영했다. 게이트는 여전히 전건 미응답이다(G-1·G-7 권고 변경)
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
> **[v3 정정]** Plan 87은 v2.1(2026-09-17)에서 "제니퍼는 OpenMetrics를 내지 않는다"로 확정했다. 관계가 **반대 방향**으로 바뀌었다 —
> 87 J7(`/metrics/apm`)이 이 계획 B-2의 **노출 기계를 재사용**한다(§0.0.4). Plan 81 §1.3은 한계만 적는다. `up{nodename}` 아이디어는 docs/27 §8 「후속 후보」에 있다.
> **관련 결정**: D-003(읽기 전용 — 트랙 A는 GET만, 트랙 B는 우리 데이터 노출이라 DB 쓰기 0) · D-035(결정적=판단·LLM=서술) ·
> D-119(접근 경계 일원화) · D-120(실 데이터 외부 SaaS 송신 금지) · D-122(조사 배치 `expose_*` 규약) · D-127(과금 API 건별 승인) ·
> D-139(패키지 경계 — 트랙 A·B-2는 `mcp_server`, B-1은 `src/api`+각 패키지 infrastructure) · D-161(플래그는 만료일 동반) ·
> D-181(`mcp<2` 고정 — `custom_route`는 1.29.1 실측) · `plans/80` §5.4-③(신규 플래그 기본 off = 비트 동일)
> **신규 결정 예약**: **D-210**(§10). `docs/02_decision.md` 「채번 이력」 표에 등재(2026-09-10).
> ※ 채번 실측 2026-09-10 — `## D-` 헤더 최댓값 **209** · 「변경 이력」 표 최댓값 **209** · 「채번 이력」 표 예약(D-105·115·134·158·
> 163~168·176·195) 대조 → **D-210**.
> **실측 기준**: 아래 `file:line`·값은 2026-09-10 현 브랜치(`multiintent`, HEAD `09183c8` + 미커밋 작업 트리)에서 직접 확인했다.
> **v3 재실측 기준**: 2026-09-22 HEAD `048c2be`(`09183c8` 이후 48커밋). v3에서 고친 `file:line`은 이 커밋 기준이다. 본문의 옛 값은
> 이력 보존을 위해 지우지 않고 **`[v3 정정]`** 표지를 붙여 바로잡았다. 한눈에 보려면 §0.0 표를 본다.
> **OpenMetrics 자료 범례**: ✔ 확인(스펙·공식 문서) · △ 추정(근거 있음, 픽스처 실측 필요) · ✖ 미확인.
> 스펙은 **OpenMetrics 1.0**(CNCF 2020-11 · 2024-08 Prometheus 프로젝트로 흡수, 저장소 `prometheus/OpenMetrics`) 기준이다.

---

## 0. 요약 — 이 계획이 실제로 푸는 문제

### 0.0 [v3] 적정성 판정 — 현 구현 재실측(2026-09-22 · HEAD `048c2be`)

> 사용자 지시(v3): *"현재 구현된 내용을 심도있게 분석하여 92번 계획의 적정한지 검토하고 업데이트하라."*

**판정: 방향은 적정, 전제·설계·범위는 고친다.** 아래 셋을 반영한 뒤에는 착수 가능한 계획이다.

| 축 | 판정 | 근거 |
|---|---|---|
| 문제 인식 — 운영 Prometheus가 없어 PromQL 경로가 한 번도 돌지 않았다 | ✅ 유효 | `mcp_server/config.toml:28` `url = ""` 그대로 · `/metrics`·`prometheus_client`·OTel 0건 그대로 · docs/27 §0 |
| 방향 — A 우선 · PromQL 정본 + OM 보완 · 전 플래그 기본 off | ✅ 적정 | D-119 ① · D-035 · `plans/80` §5.4-③. 09-10 이후 결정 중 이 방향과 충돌하는 것은 없다(§0.0.4) |
| 사실 전제 | ⚠ **12건 정정**(§0.0.1) — **8건(F-1·2·3·4·5의 `:93`·7·10·11)은 작성 시점에 이미 틀렸다.** 09-10 이후 변화로 생긴 것은 F-12(87 v2.1)와 줄 이동뿐이다 | 문제는 코드 변화가 아니라 **v1/v2 실측이 부족했던 것**이다. 옛 문서(docs/27 §3.4)를 재grep 없이 옮겼고, Prometheus 의미론을 확인하지 않았다 |
| 설계 | ⚠ **결함 7건**(§0.0.2) — 이 중 2건은 그대로 구현하면 **동작 불가·오판정**이다 | B-2 DB 풀 접근 · `stale` 판정 |
| 범위·순서 | ⚠ **과대** — S1 전제 기능(O2b)을 S0에 먼저 두었다 · B-1은 소비자가 없고 · C는 원천이 없다 | §0.0.3 |
| 게이트 | ❌ 12일간 G-1~G-8 전건 미응답 | 지금 필요한 답은 G-1·G-3·G-4 셋뿐이다(§9 [v3]) |

#### 0.0.1 사실 정정 (본문 해당 위치에 `[v3 정정]`으로 반영)

| # | v1/v2 주장 | 실측(2026-09-22) | 영향 |
|---|---|---|---|
| F-1 | exporter 응답에는 `nodename`이 없다(§2.4) | node_exporter는 `node_uname_info{nodename}`을 싣는다. Prometheus 경유 시 `exported_nodename`으로 밀린다(docs/27 §3.5 픽스처 실측) | 주입 규칙에 충돌 처리가 필요하다(D-2) |
| F-2 | 스크레이프가 죽어도 lookback 5m 동안 낡은 값이 성공으로 온다(§4.8.1) | 스크레이프 실패 → staleness marker → **빈 결과** ✔. 5분 잔류는 marker가 없는 경로에서만 생긴다 | `on_empty`가 스크레이프 장애도 잡는다 · `stale` 재설계(D-3) |
| F-3 | instant 결과의 타임스탬프로 신선도를 판정한다(§4.8.5) | `value[0]`은 **평가 시각**이다 ✔. 샘플 시각은 `timestamp()`로만 얻는다 | `stale` 조건이 항상 거짓이다(D-3) |
| F-4 | `inspect_host` 프로덕션 호출부 0건 · 4종 전부 폴스타 SQL(§3.2) | 호출부 `src/orchestration/host_inspect.py:209`(2026-08-31 · 플래그 기본 off). `processes`는 프로세스 API다. 위치 `client.py:414-435` | `metrics_live`는 "가치 후속"이 아니다. 키워드·식별자 표도 같이 고친다 |
| F-5 | sre_agent 배선 `mcp_service.py:93` · 비활성 `toolset_profiles.py:202` | `:93`은 작성 시점부터 다른 함수였다 → `mcp_service.py:105-119`·`:137-141` · `diagnosis.py:163-171` · 비활성 `toolset_profiles.py:239` | 줄 정정 |
| F-6 | 지침 1줄 — 삽입 위치 미정 | `investigation_guidance.py:175-197` `build_guidance()`. **사건 구간 노트(`:26-32`)와 충돌한다** | R-12 · `ANCHORED_TOOLS` 제외 |
| F-7 | B-1 키 `METRICS_ENDPOINT_ENABLED`·`METRICS_BEARER_TOKEN`(§4.4) | 필드를 추가할 `ObservabilityConfig`(`src/config.py:622-637`)는 `env_prefix="OBS_"`다 → 실제 키는 `OBS_METRICS_*` | 키 이름 정정 · 도움말 파일은 `observability_polestar.yaml` |
| F-8 | 게이트 결정 계측은 B-1이 채운다 | 퍼널은 이미 구현·노출됐다 — `decision_store.py:599-658` → `/api/v1/admin/noise/*`·`/api/v1/noise/*`(D-245) · JSON `/api/v1/alarm/metrics` | B-1의 "Prometheus 없이도 가치" 근거가 약해진다 |
| F-9 | 본체 다중 워커 여부는 O4에서 실측 | 단일 프로세스 확정(`src/main.py:102-107`) | multiproc 불요 |
| F-10 | B-2 kind ∈ `_METRIC_KINDS` → `_utilization_percent` | `mcp_server`의 상수는 `_METRIC_KIND_MAP`(`polestar_tools.py:53`)이다. `disk_io` = `MaxIORate`로 **퍼센트가 아니다** | 패밀리 분리(D-5) |
| F-11 | C가 폴스타 이력으로 E3 PromQL 폴백을 검증한다 | 로컬 `cmm_metric_stat_h`는 자리표시 5행이다 · E3 PromQL 채널은 **미배선 확정**(`polestar_metric_baseline.py:24`) · 픽스처 Prometheus는 데이터 볼륨이 없고 보존 15d다 | C 보류(§4.6 [v3]) |
| F-12 | Plan 87 — 벤더 exporter가 OpenMetrics를 내면 같은 파서를 재사용한다(머리말) | 87 v2.1(09-17): 제니퍼는 OpenMetrics를 **내지 않는다**. 오히려 87 J7이 B-2 노출 기계를 재사용한다 | B-2 직렬화기를 벤더 중립 모듈로 분리한다 · `nodename` 어휘를 정합한다(§0.0.4) |

#### 0.0.2 설계 결함 → 수정

| # | 결함 | 왜 문제인가 | 수정(반영 위치) |
|---|---|---|---|
| D-1 | B-2 브리지가 `custom_route` 핸들러에서 폴스타 SQL을 친다 | lifespan이 **SSE 세션마다** 열려 풀이 도구 컨텍스트에만 있다(P-5 · mcp 1.26.0·1.30.0 소스 실측). 핸들러는 닿지 못한다 → **동작 불가** | 브리지 전용 지연 풀(§4.5 [v3] 1) |
| D-2 | om 결과에 `nodename`을 무조건 주입한다 | `node_uname_info`와 충돌한다. 덮어쓰면 PromQL과 모양이 갈린다 | `exported_nodename` 이동 + `target_identity` 신원 확인(§4.2 [v3] · I-8). 대조 기준은 인자(`server_name`)가 아니라 허용목록 `os_hostname`이다. `uname`은 OS 호스트명이고 공동존은 name≠hostname이기 때문이다(D-046) |
| D-3 | `stale`을 instant 타임스탬프로 판정한다 | F-2·F-3 — **오판정**(영원히 불발)이다 | `scrape_down` 신설(`up`) · `stale`은 `timestamp()` · 왕복 ≤ 4(§4.8.5 [v3]) |
| D-4 | (γ) 인자 추가를 "회귀 0"으로 봤다 | 시그니처가 바뀌면 `tools/list`가 바뀐다 → 프롬프트 접두·A/B 게이트 전제가 달라진다 | 분기 등록 + I-6에 스키마 스냅샷(§4.8.2 [v3]) |
| D-5 | B-2가 시간 통계를 매 스크레이프 "현재값"처럼 낸다 | 최대 1시간 넘게 묵은 값 · 일괄 SQL 없음 · `max_rows` 절단 · `disk_io` 단위 | §4.5 [v3] 착수 조건 5건 |
| D-6 | 커버리지 테스트가 신규 키를 다 본다고 가정했다 | 추출기는 문자열 리터럴만 본다 → 타깃별 동적 키가 빠진다 · "7키" 불일치 | 1차 타깃별 인증 제외 · 단계별 키 목록(§4.7 [v3]) |
| D-7 | "기동 시 1회 해석"·"기동 시 커버리지 표" | 설정은 세션마다 다시 읽힌다 · 기동 훅이 없다 | 세션 시작 시 해석 · 모듈 수준 지연 캐시(§4.8.3·§4.8.4 [v3]) |

#### 0.0.3 범위 재편 — 권고 변경(사용자 미확정)

지금은 S0다(Prometheus 없음). 게이트도 전건 미응답이다. 그래서 **S0의 막힘을 푸는 최소 형태만 1차로 하고, 나머지는 관측 가능한 트리거 뒤로 미룬다**(CLAUDE.md "Simplicity First").

| 항목 | v2 | v3 권고 | 재개 트리거 |
|---|---|---|---|
| 트랙 A O0~O3 | 1차 | **1차 유지** — O2 착수는 G-3 답에 묶는다 | — |
| 병행 도구 표면(G-7) | (γ) 즉시 | **S0 (α) + 힌트** → S1 (γ)(분기 등록) | S1 |
| O2b 사다리·교차 검증 | O2 직후 | **S1 뒤** — S0에는 소스 선택지가 없다(PromQL은 항상 오류) | `PROMETHEUS_URL` 확보(plans/91 1-12) |
| B-1 본체 `/metrics` | 2차 | **보류** — 스크레이퍼가 없으면 소비자가 없다 · LLM 토큰 계측은 `src/llm.py` 훅 신설이라 별건 | 본체를 긁을 수집기 존재 |
| B-2 브리지 | S1 뒤 | 유지 + **§4.5 [v3] 5건이 착수 조건** · 직렬화기는 벤더 중립 모듈(§0.0.4 — 87 J7 공유) | S1 **또는** `plans/87` J7 착수(G-9) |
| C 백필 | S1 뒤 | **보류** — 재개 시 합성 원천 | plans/91 1-10 판정 = "배선" |

#### 0.0.4 09-10 이후 결정·계획과의 정합

D-211~D-248 본문과 그 뒤 「변경 이력」 행을 키워드 전수 대조했다(Prometheus·OpenMetrics·exporter·`/metrics`). **이 계획과 충돌하거나 대체하는 결정은 0건**이다.
다만 아래 항목은 계획에 반영하거나 표기를 고쳐야 한다.

| 대상 | 내용 | 계획 반영 |
|---|---|---|
| **`plans/87` v2.1(2026-09-17) · D-195 예약 갱신** | 제니퍼는 OpenMetrics를 **내지 않는다**(87 O-3). 대신 87 J7이 `mcp_server` `custom_route("/metrics/apm")`로 제니퍼 지표를 OpenMetrics 1.0으로 **내보내며**, 이때 **92 B-2의 직렬화기·인증·캐시 기계를 공유**한다(87 `:842`). 87 G-9 권고는 "92 B-2 착수와 묶음"이다 | ①머리말의 "Plan 87 — 벤더 exporter가 OpenMetrics를 내면 같은 파서 재사용"은 **무효**다(F-12) ②B-2 직렬화기를 `polestar_exporter.py` 안에 두지 말고 **벤더 중립 모듈**(`mcp_server/mcp_server/om_exposition.py` 제안 — 폴스타 리터럴 0 · overfit 스캔 대상)로 뗀다. 폴스타 브리지와 제니퍼 브리지가 이 모듈을 함께 쓴다 ③경로 네임스페이스 `/metrics`(폴스타)·`/metrics/apm`(제니퍼) ④타임스탬프 미노출은 87과 이미 일치한다(§4.5 [v3] 3) ⑤B-2 재개 트리거에 "87 J7 착수"를 더한다 — **B-2의 두 번째 소비자**다 |
| **`nodename` 어휘 — 87과 불일치** | 87 `:859`는 "`nodename` = 폴스타 **hostname**(D-119 규약)"이다. 92 B-2와 PromQL 도구는 `nodename` = 폴스타 **`server_name`**이다(D-119 ③ "`hostname(=server_name)`" · `promql_tools.py:447`). 폴스타에는 OS `hostname` 칼럼이 따로 있고 공동존은 name≠hostname이다(D-046 · `config/db_profiles/polestar.yaml:33`) | 한 TSDB에서 조인하려면 두 브리지가 **같은 값**을 `nodename`에 찍어야 한다. D-119를 정본으로 **`server_name`**을 쓰고, 87에는 표기 정정을 제안한다(87은 이 세션의 편집 범위 밖이라 **기록만** 한다). **✅ v3.1(2026-09-22) 반영 완료** — 87 §5.9에 정정과 역해소 단계(OS hostname → `cmm_resource.name` · D-046 역방향)를 넣었다. `custom_route` lifespan 부기와 변경 이력 행도 함께 넣었다. D-119의 인자명 `hostname`이 `server_name`을 뜻한다는 **이름 과적**이 혼동의 원인이다. docs/27 §10에 한 줄로 못 박는다 |
| D-225(2026-09-21 · 기준 경로 3단) | 새 로직은 3단이 부르는 공통 함수에 둔다(⑦) | O3의 `metrics_live`는 `host_inspect.py`의 표에 넣는다. 이 모듈이 2단 `intent_planner.py:204`와 3단 `tier3_plan.py:64` `_coerce_host_inspect_intent`의 **공통 함수**이므로 ⑦에 맞는다 |
| D-222·D-240(2026-09-17·09-21) | 실 LLM 검증의 기본은 로컬 MLX다. `RUN_E2E=1` 전용 스크립트는 여전히 승인 대상이다 | §5 [v3] 과금 경계 정정. `ab_promql_gate.py`의 실제 경로는 `sre_agent/scripts/`다 |
| D-230(2026-09-17) | 실 조사 여부 = tri-state `INVESTIGATION_LLM_ENABLED` | §5 [v3] 로컬 MLX 경로 조건 |
| D-229·D-233(2026-09-17·09-21) | toolset 정본은 프로파일이다. 원격 조사의 데이터 경로는 **MCP뿐**이다(bash 제거) | `om_*`가 MCP로 붙는다는 전제를 **강화**한다. 충돌 없음 |
| D-214(2026-09-17) | `mcp_server`에 MariaDB `itam` 편입 | D-119 ① "경계 확장" 선례. 충돌 없음 |
| D-223 예약 · `plans/101` | 관측 데이터는 `mcp_server`로만 | 충돌 없음. 단 101 `:672`는 Prometheus **분 해상도 ML 입력**의 선행으로 "`plans/92` 트랙 A"를 적었다. 트랙 A는 **현재값만** 준다(§1.2 — 시계열 보관 제외). 분 해상도 이력은 `prom_metric_range`(PromQL · `PROMETHEUS_URL`)의 몫이다 → **101 쪽 정정 필요**(기록만 한다) · **✅ v3.1 반영 완료**(101 §5.6 Prometheus 행 · §14 참조 · 변경 이력 v3.1) |
| D-161 ① 만료일 | 규정은 유효하다(D-230 2027-03-17 · D-235 2027-03-21로 계속 적용). 그러나 `docs/flag_audit.md`는 `AppConfig`만 다루고, `mcp_server`의 `expose_*` 3종(`expose_execute_sql`·`expose_polestar_tools`·`expose_raw_promql`)에는 만료일 기재 전례가 없다 | I-4의 "만료일 동반"을 구체화한다. `expose_*`가 **배치 표면 스위치**(D-122 전례 — 상시)인지, D-161 ① 대상인지를 **D-210 등재 때 확정**한다. 대상이면 `mcp_server/mcp_server/config.py` 필드 주석에 적는다(sre_agent `settings.py` 전례). 본체 B-1 플래그는 `flag_audit` 대상이다 |
| `plans/80` §5.4-③ | 원문은 "기본값 = **현행 동작**(비트 동일) · 기동 시 1회 해석"이다("기본 off"라는 표현은 요약이다) | 인용 표현만 맞춘다. 내용은 같다 |
| `plans/91` 1-10·1-12 | 변동 없음 — 1-10 기한 2027-02-20 · 1-12 **대기** | S1 트리거·C 재개 조건의 기준 |
| 인용 정밀도 | "Plan 81 §1.3(`up{nodename}`)" — plan 81 본문에는 `up{nodename}`이 없다. 그 아이디어는 docs/27 §8 「후속 후보」(`:578-583`)에 있다 · `plans/sre-agent/06` §6의 bash 전제는 D-233으로 대체됐다 | 이후 인용은 "Plan 81 §1.3 한계 + docs/27 §8 후속 후보"로 쓴다 |
| docs/27 자체의 낡은 서술 | §3.4(`:192-194` — `inspect_host` 호출부 0건) · §8.1 ②(ITAM 유령 키 — D-214로 해소) · §9 코드 위치(`toolset_profiles.py:202` → `:239` · `mcp_service.py:93` → `:105`) | O3의 docs/27 §10 작업에 **정정을 함께 포함**한다(이번 v3는 계획서만 고쳤다) · **✅ v3.1 정정 완료** — docs/27 §0 본체 채팅 행 · §3.4 · §8.1 ② · §9 코드 위치 · §4.3(uv.lock). O3에는 §10 신설만 남는다 |
| 채번 | D-210 **예약 유지**(채번 이력 표 `:28`). 현재 최댓값은 `## D-` 헤더·변경 이력 모두 **D-248**이다(머리말의 "209"는 작성 당시 값) | G-6 시점 등재 때 다시 실측한다 |

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
**[v3 정정]** C의 이 가치는 성립하지 않는다 — 로컬 이력 원천이 없고 E3 PromQL 채널은 미배선으로 확정됐다(F-11 · §4.6 [v3]). **B-2**의 가치는 오히려 늘었다 — `plans/87` J7이 같은 노출 기계를 쓴다(F-12).

### 0.3 한 줄 권고

**A(읽기)를 1차로, B-1(자기 관측)을 2차로, B-2·C는 운영 Prometheus 확정 뒤로.** A는 `mcp_server` 안에서 PromQL 도구와
**같은 반환 계약**(`{"data": {"resultType": "vector", …}}` · `source_kind`만 다름)을 쓰는 얇은 도구 2종이라, 소비자(`sre_agent`
자동 발견 · 본체 `inspect_host`)의 배선 변경이 0이다. 단 **A의 운영 가치는 대상 호스트에 exporter가 실재하는지(G-3)에 전적으로
달려 있다** — 폐쇄망 은행존·공동존 호스트에 node_exporter가 없다면 A는 픽스처·향후 대비 코드에 그치고 **B-2가 Prometheus 연동의
실체**가 된다. 이 한 가지가 트랙 우선순위를 뒤집을 수 있으므로 G-3을 **가장 먼저** 답해야 한다.

**[v3 정정]** 권고를 **"A(S0형)만 1차 — O2b는 S1, B-1·C는 보류, B-2는 S1 또는 87 J7 착수 뒤"**로 좁혔다(§0.0.3). B-1을 2차에서 내린 이유는 두 가지다.
스크레이퍼가 없으면 `/metrics`를 읽는 쪽이 없다. 그리고 게이트 퍼널은 이미 관제 API로 노출된다(F-8).

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
| P-2 | `prometheus-client`(pip · 순수 Python · 의존성 0)를 폐쇄망 미러에서 받을 수 있다 | △ 루트·sre_agent venv **미설치**(2026-09-10 실측) · **[v3]** 루트 `uv.lock`에도 0건(2026-09-22) | 자체 파서(§4.2 대안 (b))로 후퇴 — 스펙 부분집합만 |
| P-3 | `mcp_server`는 단일 프로세스·단일 워커로 뜬다(`python -m mcp_server`) | ✔ `server.py` 기동 경로 · **[v3]** 본체도 단일 프로세스 확정 — `src/main.py:102-107` `uvicorn.run(..., reload=True)` workers 미지정 | B-2 무영향. **[v3 정정]** B-1의 `PROMETHEUS_MULTIPROC_DIR`은 불요 |
| P-4 | 픽스처 exporter 2종은 Accept 협상에 따라 OpenMetrics 1.0을 낼 수 있다 | △ node_exporter는 `client_golang` promhttp 기반 · mock(nginx 정적)은 **0.0.4 고정 실측**(`mock_exporter/nginx.conf:4`) · **[v3]** △ `client_golang`의 `HandlerOpts.EnableOpenMetrics`는 기본 false로 알려져 있다. 켜지 않은 exporter는 OpenMetrics를 요청받아도 0.0.4로 답한다. 픽스처 node_exporter v1.8.1(`target-vm/Dockerfile:5`)도 그럴 수 있어 O0에서 확정한다 | 파서가 양쪽을 받으므로 설계 무영향 — 테스트 픽스처에 1.0 변형을 **별도 파일**로 둔다(§6). 실 exporter가 주로 0.0.4를 낸다면 **주 경로는 0.0.4 파서**이고 1.0 strict 파서는 보조 경로다 |
| P-5 | **[v3]** `mcp_server` 요청 스코프 자원(DB 풀·설정)은 도구 밖에서도 닿는다 | ✖ **거짓** — lifespan은 **SSE 세션마다** 열린다(`server.py:86-102` → mcp `Server.run()`이 연결마다 `self.lifespan` 진입 · mcp 1.26.0·1.30.0 소스 실측). `pool_manager`·`config`는 도구의 `ctx.request_context.lifespan_context`에만 있다 | `custom_route` 핸들러(`Request → Response`)는 풀에 닿지 않는다 → B-2 설계 보강(§4.5 · §0.0.2 D-1) · "기동 시 1회"는 "세션 시작 시"로 읽는다 |

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
| **[v3 정정]** exporter 자체 `nodename` | node_exporter는 **`node_uname_info{nodename="<uname>"}`에 자기 `nodename`을 싣는다**. Prometheus 경유 시에는 타깃 라벨과 충돌해 `exported_nodename`으로 밀린다(`honor_labels=false` 기본). 위 행의 "없다"는 **`node_uname_info` 한 패밀리를 빼면** 참이다 | docs/27 §3.5(픽스처 실측 2026-08-06) · `target-vm/Dockerfile`(`hostname=svr-web-01`) |

마지막 두 행이 트랙 A 설계의 근거다 — **exporter를 직접 읽으면 대부분의 시리즈에 `nodename`이 없다.** 그래서 서버가 hostname→타깃을 정하고 결과에
`nodename=<hostname>`을 **주입**한다(§4.3 (d)). Prometheus 경로와 라벨 어휘가 같아져 소비자가 두 경로를 구별할 필요가 없다.
**[v3]** 단 `node_uname_info`처럼 이미 `nodename`을 가진 시리즈는 **Prometheus와 똑같이 `exported_nodename`으로 옮긴 뒤** 주입한다.
덮어쓰면 PromQL 결과와 모양이 갈린다. 옮긴 원래 값은 버리지 않는다. **타깃 신원 확인**(허용목록에 적은 URL이 정말 그 호스트인가)에 쓴다(§4.2 ①).

---

## 3. 현행 실측 — 어디에 꽂히는가

### 3.1 `mcp_server` (트랙 A·B-2 소재지)

| 자산 | 위치 | 재사용 |
|---|---|---|
| PromQL 도구 7종·셀렉터 조립·반환 계약 `_ok/_err`·감사 로그 | `mcp_server/mcp_server/promql_tools.py`(575줄) | 반환 계약·감사 파이프·`make_client` 패턴·duration 파서 **그대로** |
| `PrometheusConfig` + env 오버라이드 | `config.py:54-66` · `_apply_env_overrides:268-287` | `OpenMetricsConfig` 동형 추가 |
| `.env.example` 커버리지 테스트 — `inspect.getsource(_apply_env_overrides)`에서 키 추출 | `mcp_server/tests/test_env_example_coverage.py` | 신규 키는 **그 함수 안에서** 읽어야 테스트가 본다(밖에서 읽으면 추출기가 놓친다 — docs/27 §8.1) |
| 도구 등록 배선 | `server.py:117-135` (`FastMCP(...)` → `register_*`) | `register_openmetrics_tools(mcp, expose=...)` 1줄 |
| `FastMCP.custom_route(path, methods, name=None, include_in_schema=True)` | mcp 1.29.1 **실측** · **[v3]** 1.26.0에서도 동일 시그니처. `sse_app()`이 커스텀 라우트를 **맨 뒤에** 붙이고, `build_asgi_app`(`server.py:67-82`)의 Bearer 미들웨어가 앱 전체를 감싸므로 인증도 적용된다 | B-2 `/metrics` 라우트. **[v3] 단 핸들러는 lifespan 컨텍스트(DB 풀)에 닿지 않는다**(P-5) |
| 폴스타 고수준 SQL 조립(최근 지표·활성 알람) | `polestar_tools.py` · **[v3]** kind 상수는 `_METRIC_KIND_MAP`(`:53-58` — `disk_io` = `server.Disks`/`MaxIORate`, 퍼센트 아님). 지표 SQL `build_metric_trend_sql`(`:400`)은 **서버 1대용**이다 | B-2 브리지의 데이터 원천. **[v3]** 전 서버 일괄 SQL은 새로 짜야 한다(§4.5) |
| MockTransport 단위 + `RUN_DOCKER_IT` 통합 패턴 | `tests/test_promql_tools.py:63-81, 609-616` | 동형 복제 |
| overfit 스캔 대상 | `scripts/overfit_check.py:62`(`mcp_server/mcp_server` 포함 · `polestar_tools.py`만 EXCLUDE) | `openmetrics_tools.py`는 **범용**이어야 한다(폴스타 리터럴 0). B-2 브리지는 `polestar_exporter.py`로 분리해 EXCLUDE 대칭 등재 |

### 3.2 소비자

| 소비자 | 현재 | 트랙 A 이후 |
|---|---|---|
| `sre_agent`(HolmesGPT) | `mcp_server` 도구 **자동 발견**(`interface/mcp_service.py:93` RemoteMCPToolset) · 내장 `prometheus/metrics` toolset 비활성(`toolset_profiles.py:202`) · **[v3 정정]** `:93`은 작성 시점부터 `_job_to_question()` 본문이었다. 실제 배선은 `mcp_service.py:105-119` `_build_mcp_servers()` → `:137-141` `DiagnosisAgent(..., mcp_servers=)` → `diagnosis.py:163-171` `Config(mcp_servers=)`. `RemoteMCPToolset`은 holmes 내부 클래스다(로컬 미설치라 클래스는 미확인 ✖). 비활성은 `toolset_profiles.py:239`, 원격 프로파일은 bash도 off(`:240` · D-233) | 배선 변경 0. 지침 1줄 — *"`prom_*`가 URL 미설정 오류를 내면 `om_*`로"* · **[v3]** 삽입 위치는 `application/investigation_guidance.py:175-197` `build_guidance()`의 상수 노트(`PLAYBOOK_NOTES` `:122-167` 전례)다. ★사건 구간 노트(`:26-32`)가 *"인자 없이 호출하면 현재 시각 기준 최신 데이터이며 사건 증거가 아니다"*라고 지시하므로, **현재값 전용인 `om_*`는 `ANCHORED_TOOLS`(`:19-24`)에 넣지 않는다.** 대신 "현재 상태로만 서술" 문장을 함께 둔다(R-12) |
| 본체 채팅 `inspect_host` | `HOST_INSPECT_PROFILES` 4종 전부 폴스타 SQL(`src/dbhub/client.py:314-335`) · 프로덕션 호출부 0건(docs/27 §3.4) · **[v3 정정]** 위치 `client.py:414-435`. 4종 중 `processes`는 SQL이 아니라 **프로세스 API**다(`:412`). **호출부도 있다** — `src/orchestration/host_inspect.py:209` `run_host_inspect`(2026-08-31 `1868330`, 게이트 `COMPOSITE_INVESTIGATION_ENABLED` 기본 off `src/config.py:1110`). 계획 v1은 docs/27 §3.4(`:192-194`)의 옛 서술을 재grep 없이 옮겼다 | `metrics_live` 프로파일 1건 추가(옵트인) — 호출부가 0건이라 **가치는 후속** · **[v3 정정]** 플래그만 켜면 경로가 돈다. 추가하려면 `host_inspect.py`의 `_PROFILE_KEYWORDS`(`:47-51`)·`_PROFILE_IDENTIFIER`(`:54-58` — 현재 3종만)도 같이 고친다 |
| 게이트 E3 baseline | `polestar_metric_baseline.py` — 폴스타 `cmm_metric_stat_h`만 | 무변경(현재값만으로는 baseline 불가). C가 픽스처 검증 입력을 준다 · **[v3 정정]** PromQL 채널은 **배선하지 않기로 확정**됐다(`polestar_metric_baseline.py:24`). 로컬 PG의 `cmm_metric_stat_h`도 자리표시 5행뿐이다(`stat_date='stat_date_1'` … `testdata/pg/init/05_insert_dummy_data.sql:1054-1059`). 따라서 C가 줄 "검증 입력"의 원천과 대상이 둘 다 없다(§4.6) |

### 3.3 본체 (트랙 B-1 소재지)

| 항목 | 실측 |
|---|---|
| 기존 노출 | `/health`(`src/api/routes/health.py:22`) · `/admin/noise/health` — **`/metrics` 없음**, `prometheus_client`·OTel 계측 0건 · **[v3]** 실제 경로는 `/api/v1/` 접두(`src/api/server.py:621`). 그 뒤 사용자용 읽기 `/api/v1/noise/*`(D-245)가 추가됐다. **JSON 운영 지표 `GET /api/v1/alarm/metrics`**(`src/api/routes/alarm.py:1116` · Plan 52 §9)가 이미 있어 이름이 겹친다. `/metrics` 0건·`prometheus_client` 0건·OTel 0건은 2026-09-22에도 그대로다 |
| 계측 후보 | 요청 RED(`audit_middleware.py` 옆) · LLM 호출 수·토큰·지연(`src/llm.py` — **과금 관측**은 D-127 운영의 실측 근거가 된다) · 파이프라인 노드 지연(`src/graph.py`) · 게이트 결정 카운터(`noise_gate` stage — `plans/54` 퍼널 축과 동일) · SSE 활성 스트림 수(`plans/89`) |
| **[v3]** 이미 있는 것 | ①게이트 퍼널은 **구현·노출 완료**: `noise_gate/infrastructure/decision_store.py` `funnel()`(`:599-658`)·`aggregate()`·`timeseries_report()`가 `logs/alarm_decisions.jsonl`을 다시 읽어 집계한다. 관제 API는 `/api/v1/admin/noise/*`·`/api/v1/noise/*`, 단계 어휘는 `notification_policy.py:73-88` `STAGE_ORDER` 14단계다. 증가형 카운터는 0건이다 ②`src/observability/`에 8개 모듈이 있다(인메모리 `group_metrics`·`investigation_metrics` — `snapshot()`을 외부로 노출하는 API는 없다). ③`src/llm.py`에는 토큰·호출 계측 훅이 **0건**이다. `investigation_metrics.record_investigation(tokens=)`에는 실 토큰이 한 번도 들어간 적이 없다(`fault_diagnosis.py:133,159`) ④SSE 동시 스트림 수 추적은 0건이다(`notification_bus.py:15-27` `_queues`를 쓸 수 있다) |
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
- **[v3] 규칙 ① 보강 — 라벨 충돌과 타깃 신원**: 시리즈에 이미 `nodename`이 있으면 Prometheus `honor_labels=false`와 같게
  `exported_nodename`으로 옮긴 뒤 주입한다. 그러면 docs/27 §3.5 픽스처 실측과 같은 모양이 된다.
  스크레이프 응답에 `node_uname_info`가 있으면 그 원래 `nodename`을 반환 최상위 `uname_nodename`에 **그대로** 싣는다.
  이 값은 **OS 호스트명**(폴스타 `cmm_resource.hostname`)이지 도구 인자 `hostname`(= 폴스타 `server_name` · D-119 ③)이 아니다.
  공동존은 두 값이 다르다(D-046). 그래서 인자와 곧바로 비교하면 거짓 불일치가 난다.
  대조 기준은 허용목록 항목의 선택 필드 `os_hostname`이다. 비교는 소문자화하고 첫 `.` 뒤를 버린다(FQDN↔단축명 — §4.8.5와 같은 정규화 함수).
  `target_identity`는 다음과 같다.
  - `match`: `os_hostname`이 있고 일치하거나, `server_name`과 일치
  - `mismatch`: `os_hostname`이 있는데 불일치 → **허용목록 오등록**(URL이 다른 호스트를 가리킴)
  - `unverified`: `os_hostname`이 없고 `server_name`과도 불일치
  - `unknown`: `node_uname_info`가 없음

  값은 반환하되 오류로 격하하지 않는다. 판단은 코드가 하고 LLM은 서술만 한다(D-035).
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
| 다중 워커 | P-3 실측 후 결정 — 다중이면 `PROMETHEUS_MULTIPROC_DIR` + `multiprocess.MultiProcessCollector` · **[v3 확정]** 단일 프로세스(P-3) → 불요 |
| **[v3]** 실측 반영 | ①필드를 추가할 `ObservabilityConfig`(`src/config.py:622-637`)는 `env_prefix="OBS_"`다. 그래서 키는 `OBS_METRICS_ENDPOINT_ENABLED`·`OBS_METRICS_BEARER_TOKEN`이고, 도움말은 `config/settings_help/observability_polestar.yaml`에 둔다. 토큰을 `SecretStr`로 두면 웹 UI에서 편집할 수 없다(`settings_catalog.py:9-10`) ②라우터는 `/api/v1` 아래에 붙는다 → Prometheus `metrics_path: /api/v1/metrics`. 기존 JSON `/api/v1/alarm/metrics`와 이름이 겹치니 docs/27 §10에 둘을 구별해 적는다 ③`noise_gate_decisions_total`은 `DecisionStore.record()`(`decision_store.py:109`) 옆 증가 카운터로 둔다. 어휘는 `STAGE_ORDER`와 맞춘다. JSONL 재집계 퍼널과 **두 집계가 병존**함을 명시한다(값이 다르면 파일 퍼널이 정본) ④LLM 계측은 `src/llm.py`에 훅이 없어 **콜백/래퍼를 새로 만드는 별도 작업**이다. `/metrics`의 부속이 아니다 ⑤**착수는 보류**(§0.0.3) — 스크레이프할 수집기가 없으면 `/metrics`를 읽는 소비자가 없다 |

### 4.5 트랙 B-2 — 폴스타 → OpenMetrics 브리지 (`mcp_server` `custom_route("/metrics")`)

- **내는 것(1차 3패밀리)**: `polestar_server_info{nodename,zone,db_id,ipaddress,os}` 1(info) · `polestar_metric_utilization_percent{nodename,kind}`
  (gauge — `cmm_metric_stat_h` 최근 시각 · kind ∈ `_METRIC_KINDS` 4종) · `polestar_alarm_active{nodename,severity}`(gauge — 활성 알람 수).
  **[v3 정정]** `mcp_server`의 상수는 `_METRIC_KIND_MAP`(`polestar_tools.py:53`)이다(`_METRIC_KINDS`는 본체 `src/dbhub/client.py:439`). `disk_io`는 퍼센트가 아니다 → 아래 착수 조건 2 참조.
- **`nodename` = 폴스타 `server_name`** — 규약을 **우리가 정의**한다. node_exporter를 같은 Prometheus가 스크레이프하면
  `static_configs.labels.nodename`을 이 값에 맞추면 되므로, G-6(협의) 대상이 "인프라가 우리 규약을 따르는가"로 **단순해진다**.
- **DB 보호**: 스크레이프마다 SQL을 치지 않는다 — 응답 캐시 TTL(`bridge_cache_seconds` 기본 60) · 존(db_id)별 1쿼리 · 행 상한.
  DB 레벨 timeout·max_rows는 종전대로 `SourceConfig`가 강제(D-119 ④).
- **카디널리티**: 서버 수 × kind 4 × 존 3 — 수천 시리즈 수준. 상한 초과 시 노출을 **잘라내지 말고** `polestar_bridge_truncated 1`
  게이지로 알린다.
- **게이팅**: `expose_polestar_exporter`(기본 false) · `.env` `EXPOSE_POLESTAR_EXPORTER` · 전송 인증은 기존 `MCP_BEARER_TOKEN` 미들웨어를
  **그대로 통과**하므로 Prometheus 쪽에 `authorization` 설정 필요(docs/27 §10에 예시).
- **overfit**: 폴스타 리터럴이 있으므로 `polestar_exporter.py`로 분리해 `overfit_check.py` EXCLUDE에 `polestar_tools.py`와 대칭 등재.
  **[v3]** 직렬화·Accept 협상·응답 캐시·절단 게이지 같은 **범용 기계**는 `om_exposition.py`(폴스타 리터럴 0 · 스캔 대상)로 뗀다.
  그러면 `plans/87` J7(`/metrics/apm`)이 같은 모듈을 쓴다(§0.0.4). `polestar_exporter.py`에는 SQL과 패밀리 정의만 남는다.
- **[v3] 착수 조건 — 아래 5건을 설계에 넣기 전에는 O5를 시작하지 않는다.**
  1. **DB 접근(P-5)** — 핸들러는 lifespan의 `pool_manager`에 닿지 않는다. 브리지 **전용 풀**을 둔다. 첫 스크레이프 때 지연 생성하고, 소스당 `pool_max_size=1`, 앱 종료 시 정리한다.
     기존 lifespan을 프로세스 수준으로 올리는 안은 도구 경로 동작을 바꾸므로(비트 동일 위반) 기각한다.
  2. **단위** — `_METRIC_KIND_MAP`에서 `disk_io`는 `MaxIORate`라 퍼센트가 아니다. `polestar_metric_utilization_percent{kind}`는 `cpu`·`memory`·`filesystem` 3종으로 한정한다.
     `disk_io`는 단위를 실측한 뒤(✖) 별도 패밀리로 두거나 1차에서 제외한다.
  3. **신선도** — 원천이 **시간 통계**(`stat_date` varchar · 시간 버킷)라 15s마다 긁어도 값은 최대 1시간 넘게 묵어 있다.
     노출 샘플에 **명시 타임스탬프를 달지 않는다**. 명시 타임스탬프는 staleness가 적용되지 않고, 헤드보다 오래되면 거부(out of bounds)될 위험이 있다 △.
     대신 `polestar_metric_stat_timestamp_seconds{nodename,kind}` gauge로 "이 값이 몇 시 통계인가"를 함께 낸다.
     캐시 TTL도 60s가 아니라 시간 통계 주기에 맞춘다(기본 300s 제안 — 픽스처·운영 실측 후 확정).
  4. **일괄 SQL** — 서버 1대용 `build_metric_trend_sql`로는 존별 1쿼리를 만들 수 없다. "서버·kind별 최신 1행" 일괄 SQL을 PG/DB2 방언으로 새로 짜고
     `EXPLAIN` 비용을 실측한다(운영 서버 수 ✖).
  5. **행 상한** — `SourceConfig.max_rows=10000`이 서버 수×kind를 자를 수 있다. 절단을 감지해 `polestar_bridge_truncated 1`을 올린다(침묵 절단 금지).

### 4.6 트랙 C — 백필 스크립트 (`scripts/polestar_to_openmetrics.py`)

> **[v3] 보류 — 전제 붕괴.** ①로컬 PG `cmm_metric_stat_h`는 자리표시 5행이라(§3.2) 백필할 이력이 없다.
> ②검증 대상으로 든 E3 PromQL 폴백은 "배선하지 않는다"로 확정됐다(`polestar_metric_baseline.py:24`).
> ③실 이력은 폐쇄망 운영 DB에만 있다. 그것으로 픽스처를 만들어 저장소에 넣으면 실 데이터 반입이다(D-120 정신).
> ④픽스처 Prometheus는 데이터 볼륨이 없고 보존 기간이 기본 15d다(`testdata/prometheus/docker-compose.yml`). 15일보다 오래된 블록은 적재하자마자 보존 정책으로 지워진다 △.
> **재개 조건**: plans/91 1-10 판정(2027-02-20)이 "E3 PromQL 채널 배선"으로 나올 때. 재개 시 원천은 **합성 시계열 생성기**로 한다(백필 절차·파서 왕복 검증 전용).
> compose에는 `--storage.tsdb.retention.time`과 데이터 볼륨을 추가한다. `promtool`은 `prom/prometheus` 이미지 안의 것을 쓴다(개발 머신 설치 불요).
> 아래 본문은 재개 시의 설계로 보존한다.

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
# auth_header = ""                   # 보안 값은 .env: OPENMETRICS_TARGET_<HOSTNAME_UPPER>_AUTH_HEADER  ← [v3] 1차 제외(§4.7 [v3])
# os_hostname = "svr-web-01"         # [v3] 선택 — node_uname_info 신원 대조 기준(폴스타 cmm_resource.hostname · §4.2 [v3])
```

- 환경변수 오버라이드는 **`_apply_env_overrides` 안에서** 읽는다(커버리지 테스트 추출 범위) · `.env.example`에 7키 + 타깃 예시 문서화 ·
  `test_openmetrics_keys_documented_together` 신설(Prometheus 4키 테스트 동형).
- 본체(B-1): `ObservabilityConfig`(`src/config.py` nested · `Field(default_factory=…)`)에 `metrics_endpoint_enabled`·`metrics_bearer_token` —
  `settings_catalog.py`·`config/settings_help/*.yaml` 동반 등재(D-129 전수 회귀).
- **[v3 정정] 키 목록 단일화** — v2 §4.8.7이 키를 더해 "7키"가 맞지 않게 됐다. 단계별로 나눈다.
  **S0(트랙 A)**: `EXPOSE_OPENMETRICS_TOOLS` · `OPENMETRICS_SCRAPE_TIMEOUT` · `OPENMETRICS_MAX_BODY_BYTES` · `OPENMETRICS_MAX_SERIES` 4키(`target_cidr_allowlist`는 G-2 (ii) 채택 시).
  **S1(O2b)**: `OPENMETRICS_FALLBACK_POLICY` · `OPENMETRICS_COVERAGE_TTL_SECONDS` · `OPENMETRICS_CROSS_CHECK_TOLERANCE` · `OPENMETRICS_SCRAPE_INTERVAL_HINT`.
  **B-2**: `EXPOSE_POLESTAR_EXPORTER` · `OPENMETRICS_BRIDGE_CACHE_SECONDS`.
  문서화 동반 테스트도 단계별로 하나씩 둔다.
- **[v3] 타깃별 인증 키는 추출기가 못 본다** — `test_env_example_coverage.py`의 `_ENV_KEY_RE`는 `_apply_env_overrides` 안의 **문자열 리터럴**만 뽑는다.
  `OPENMETRICS_TARGET_<HOSTNAME_UPPER>_AUTH_HEADER`는 f-string이라 빠진다. 그래서 1차는 **타깃별 인증을 넣지 않는다**(node_exporter 기본 무인증 · 필요성 ✖).
  넣게 되면 `test_defined_sources_have_documented_connection_key`(`*_CONNECTION` 동적 키 전례)와 같은 전용 테스트를 함께 둔다.
- **[v3] B-1 키 이름** — `ObservabilityConfig`는 이미 `env_prefix="OBS_"`다(§4.4 [v3] ①). 실제 키는 `OBS_METRICS_ENDPOINT_ENABLED`·`OBS_METRICS_BEARER_TOKEN`이다.


### 4.8 ★ PromQL 기반 연동과의 병행 — 채널 공존 모델 (v2 · 사용자 지시 ②)

#### 4.8.1 두 채널은 무엇이 같고 무엇이 다른가 (실측·스펙 기준)

| 축 | PromQL 경로(`prom_*` · 정본) | OpenMetrics 경로(`om_*` · 보완) | 병행 함의 |
|---|---|---|---|
| 데이터 원천 | Prometheus TSDB(스크레이프·보존 15d 픽스처) | exporter 응답 **그 순간** | 이력·집계는 PromQL만. 현재값은 둘 다 |
| 값의 시각 | instant 쿼리는 **lookback 5m 안의 마지막 샘플**(Prometheus 기본 `query.lookback-delta`) — 스크레이프가 죽어도 최대 5분간 **낡은 값이 성공으로** 온다 · **[v3 정정]** 스크레이프 **실패**는 빈 스크레이프로 처리돼 그 타깃 시리즈 전부에 staleness marker가 찍히고 instant 결과는 곧 **빈 결과**가 된다 ✔(Prometheus docs *Staleness*). 5분 잔류는 marker가 없는 경로(exporter가 명시 타임스탬프를 다는 경우 등)에서만 생긴다. 또 instant 결과의 `value[0]`은 **평가 시각**이지 샘플 시각이 아니다 ✔. 샘플 시각은 `timestamp(<selector>)`로만 얻는다. O0에서 픽스처로 확인한다(mock-exporter를 멈춘 뒤 instant·`up` 조회) | 응답 시각 = 스크레이프 시각(정확히 지금) | ★두 값의 **타임스탬프 차이**가 "Prometheus 스크레이프 정지"의 결정적 신호가 된다 · **[v3 정정]** 스크레이프 정지의 결정적 신호는 타임스탬프 차이가 아니라 **`up{nodename}`==0 + exporter 직결 성공**이다(§4.8.5 `scrape_down`) |
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

> **[v3] 권고 수정 — 단계형: S0 = (α) + 힌트, S1 = (γ).**
> ①**(γ)는 플래그가 꺼져 있어도 비트 동일이 아니다.** FastMCP는 함수 시그니처로 도구 스키마를 만든다. `prom_metric_instant`에 `source`·`cross_check`를
> 더하면 `tools/list`가 바뀐다. 그러면 sre_agent 프롬프트 접두가 바뀌고(KV 캐시 무효), D-119 A/B 게이트가 본 스키마와도 달라진다. v2의 I-6은 요청·응답만 봤다.
> 따라서 확장 시그니처는 **OM 기능이 켜졌을 때만 등록하는 분기 등록**으로 하고, 플래그 off면 기존 함수를 그대로 등록한다. I-6에 `tools/list` 스냅샷을 더한다.
> ②**S0에서는 소스 선택지가 없다.** `PROMETHEUS_URL`이 비어 있어 `prom_*`는 항상 오류다. 사다리 R1은 실행될 일이 없고 R2는 "항상 exporter"와 같다.
> 이때 (γ)의 이득(소스 선택을 코드가 한다)은 0이고 비용(스키마 변경·사다리·커버리지 표)만 남는다.
> **S0 형태**: `prom_*`는 무변경으로 두고 `om_*` 2종을 옵트인으로 노출한다. OM 기능이 켜졌을 때만 `PROMETHEUS_URL 미설정` 오류에 `"hint": "om_metric_instant"` 1필드를 더한다(off면 오류 바이트 동일).
> LLM 입장에서 선택은 결정적이다 — 한쪽이 항상 오류와 대체 도구명을 돌려준다.
> ③(γ)는 **S1 트리거**(`PROMETHEUS_URL` 확보 · plans/91 1-12 해소)가 생기면 구현한다. 그때 두 소스가 실제로 경합하고 교차 검증도 뜻을 갖는다. 아래 §4.8.3~§4.8.5는 그 S1 설계로 보존한다.

#### 4.8.3 서버 소스 사다리 — `source="auto"`의 결정 규칙 (결정적 · 기동 시 1회 해석)

> **[v3 정정]** `mcp_server`는 설정을 **SSE 세션마다** `load_config()`로 다시 읽는다(`server.py:88`). 그리고 프로세스 기동 훅이 없다(P-5).
> 그래서 "기동 시 1회"는 **"세션 시작 시 1회"**로 읽는다. 호출 도중에 정책이 바뀌지 않는다는 성질은 그대로다.

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
| Prometheus 커버리지 | `GET /api/v1/label/nodename/values`(읽기 API · D-003) → 집합. 실패 시 "미상"으로 두고 R1은 시도 후 결과로 판단 | 기동 시 + TTL(기본 10분) · **[v3 정정]** 기동 훅이 없으니 **모듈 수준 지연 캐시**(첫 조회 시 적재) + TTL로 둔다. 세션 간에 공유된다 |
| exporter 커버리지 | `[[openmetrics.targets]]` 허용목록(+G-2 (ii) 파생) | 기동 시 · **[v3]** 세션 설정에서 읽는다 |

`metric_catalog(hostname)`은 이 표를 함께 돌려준다(`sources_available: ["prometheus","exporter"]`) — LLM이 "이 호스트는 exporter만
있다"를 **추측이 아니라 사실로** 안다. Plan 81 §1.3의 가용성 한계(`up{nodename}`)도 이 표로 갈린다: Prometheus 커버·`up==0` = 스크레이프
실패, exporter 직결 성공 = 호스트 생존 → **"Power off vs 에이전트 통신 이슈"** 구분이 두 채널 병행에서 처음 가능해진다.

#### 4.8.5 교차 검증 — 두 소스를 같이 읽어 규약·신선도를 결정적으로 판정

`metric_instant(..., cross_check=true)`(기본 false · 왕복 2회) 또는 운영 스크립트 `scripts/prom_om_cross_check.py`(호스트 목록 일괄):

| 판정 | 조건(결정적) | 의미 | 조치 안내(반환 `diagnosis`) |
|---|---|---|---|
| `label_mismatch` | PromQL 빈 결과 **AND** exporter 데이터 있음 **AND** Prometheus 커버리지에 유사 값 존재(예: FQDN vs 단축명 — 정규화 후 일치) | `nodename` 규약 불일치(docs/27 §6.1 ④의 실체) | "스크레이프 설정 `nodename` 정규화 필요 — 후보: <값>" |
| `not_scraped` | PromQL 빈 결과 **AND** exporter 있음 **AND** 커버리지에 후보 없음 | Prometheus가 이 호스트를 안 긁는다 | "타깃 등록 필요 — 인프라 협의" |
| `stale` | PromQL 값 타임스탬프가 `now − scrape_interval×3`보다 오래됨(값은 lookback 덕에 성공으로 옴) **AND** exporter 신선 · **[v3 정정]** instant `value[0]`은 평가 시각이라 이 조건은 **항상 거짓**이다. 서버가 조립한 `timestamp(<metric>{nodename="…"})`의 값(샘플 시각)으로 판정한다. staleness marker 때문에 이 판정은 드물다(명시 타임스탬프 exporter 등) | 스크레이프 정지 | "Prometheus 타깃 상태 확인(`up`)" |
| **[v3 신설]** `scrape_down` | PromQL 빈 결과 **AND** 서버 조립 `up{nodename="…"}` 중 값 0인 시리즈 존재 **AND** exporter 직결 성공. 한 호스트에 job이 여럿이면(픽스처도 `node`·`mock` 2개) 0인 `job`·`instance`를 결과에 그대로 싣는다 | Prometheus→타깃 수집 경로 장애. 호스트와 exporter는 살아 있다 — Plan 81 §1.3 "Power off vs 에이전트 통신 이슈"의 **통신 쪽** 실체 | "Prometheus 스크레이프 경로 확인(방화벽·타깃 주소) — 호스트는 응답함" |
| `value_drift` | 같은 시리즈(`job`·`instance` 제외 정렬) gauge 값 차가 허용 오차(기본 상대 5% 또는 절대 ε) 초과 **AND** 두 타임스탬프 차 < scrape_interval | 이례 — 계측 차이·exporter 다중 인스턴스 | 수치 그대로 보고(판단 유보) |
| `consistent` | 위 어느 것도 아님 | 정상 | — |

- 판정은 **전부 코드**(D-035). LLM은 `diagnosis`를 서술만 한다. 결과에는 두 소스의 원 값·타임스탬프·비교 시리즈 수가 실린다.
- **[v3]** 판정은 6종이다(`scrape_down` 신설). `not_scraped`의 조건에 "`up{nodename}` 시리즈 없음"을 더해 `scrape_down`과 가른다.
  교차 검증 1회의 왕복은 **최대 4회**다(PromQL 본 조회 · `up` · `timestamp()` · exporter 1). 필요할 때만 뒤 두 개를 부른다.
  I-7 "호출당 1회 강등"은 **강등(fallback)** 규칙이고, 명시 요청인 교차 검증에는 이 4회 상한을 따로 둔다.
  `label_mismatch`의 "정규화 후 일치"는 §4.2 [v3]과 **같은 함수**를 쓴다(소문자화 + 첫 `.` 뒤 절단). 규칙을 한 곳에 둔다.
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
  **[v3]** I-6에 **`tools/list` 스키마 동일**을 더한다(§4.8.2 [v3] ① — 확장 시그니처는 분기 등록). 교차 검증은 왕복 4회 상한(§4.8.5 [v3]).
- **테스트(O2b)**: 사다리 매트릭스 — (URL 설정 × 커버리지 포함 × PromQL 결과 {성공·빈·오류} × 타깃 {있음·없음} × 정책 3종) 전 조합을
  MockTransport 2개(Prometheus·exporter)로 고정 · `fallback_reason` 값 단언 · 교차 검증 5판정 각 1건 이상(픽스처 mock 고정값으로
  `consistent`, `nodename` 다른 두 스텁으로 `label_mismatch`, 타임스탬프 조작으로 `stale`) · Docker 통합은 픽스처 Prometheus(9190)와 mock(9102)을
  **동시에** 읽어 `consistent`를 실증.
  **[v3]** 판정은 6종이다. `stale`은 `timestamp()` 응답 스텁으로, `scrape_down`은 `up`=0 스텁으로 고정한다. Docker 통합에 한 경로를 더한다 —
  **mock-exporter 컨테이너를 멈추고** 픽스처 Prometheus를 읽으면 빈 결과 + `up`=0이 나오는지 본다(F-2 staleness 실측). 이것은 O0 실측과 같은 절차다.
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

> **[v3] 착수 순서 재편(§0.0.3)**
>
> | 단계 | Wave | 착수 조건 |
> |---|---|---|
> | **1차(S0)** | **O0 → O1 → O2 → O3** | G-3 선답 → O2 착수 · G-4(의존성) → O1 착수 |
> | 2차(S1) | O2b(사다리·교차 검증 6판정 · (γ) 분기 등록) | S1 트리거 = `PROMETHEUS_URL` 확보(plans/91 1-12 해소) |
> | 보류 | O4(B-1) | 본체를 스크레이프할 수집기 존재 |
> | 보류 | O5(B-2) | (S1 **또는** `plans/87` J7 착수) + §4.5 [v3] 착수 조건 5건 설계 반영 · 직렬화기 벤더 중립 분리(§0.0.4) |
> | 보류 | O6(C) | plans/91 1-10 판정이 "E3 PromQL 채널 배선"일 때 · 합성 원천 |
>
> **O0 추가 실측(v3)**: ①node_exporter v1.8.1이 OpenMetrics Accept에 1.0으로 답하는가, 아니면 0.0.4로 답하는가(P-4 △ — 주 파서 경로 결정)
> ②mock-exporter를 멈춘 뒤 instant 결과가 빈 결과인지, `up`=0인지(F-2 staleness) ③target-vm 직결 응답의 `node_uname_info{nodename}`이
> `svr-web-01`인가(§4.2 [v3] 신원 확인의 픽스처 기준값 — 픽스처는 server_name = OS hostname이라 `match`. `mismatch`·`unverified`는 단위 스텁으로 고정) ④`prometheus-client` 파서 함수 `inspect.signature()` 실측(원 ②)
> ⑤설치된 `mcp` 버전 실측(R-11).
> **O2 착수 = O1 완료 + G-3 답**. G-3이 "없음"이면 O2를 멈추고 §0.3의 B-2 우선 재편을 G-1로 다시 묻는다.
> **O3 범위(S0)**: `om_*` 서술 지침 + "현재 상태로만 서술" 문장(R-12) · `prom_*` URL 미설정 오류의 `hint`(OM on 한정) · docs/27 §10 ·
> `inspect_host` `metrics_live`(선택 — `host_inspect.py` 키워드·식별자 표 동반 수정). **§3.4 옛 서술 정정**(`inspect_host` 호출부 0건)도 docs/27 §10 작업에 포함한다. **✅ v3.1에서 선행 완료**(§0.0.4 docs/27 행).
>
> **[v3] 과금 경계 정정(D-240)**: 실 LLM 검증의 기본 경로는 이제 **로컬 MLX**다(D-240 ① — 두 평면 `mlx` 루프백이면 승인 불요).
> 그러나 `ab_promql_gate.py`는 **Gemini 전용**이고 `RUN_E2E=1` 하드 게이트다(`sre_agent/scripts/ab_promql_gate.py:16,263`). D-240 주의 ①("`RUN_E2E=1` 전용 스크립트에는
> 로컬 모드가 없다")에 그대로 걸리므로 **지금은 여전히 건별 승인 대상**이다. 로컬 MLX로 돌리려면 두 가지가 필요하다. ①sre_agent 조사 LLM을 `API_BASE=http://127.0.0.1:8080/v1`로 둔다
> (`settings.py` `api_base` · OpenAI 호환) ②`INVESTIGATION_LLM_ENABLED=true`로 둔다(D-230 tri-state). 여기에 하네스 로컬 모드 편입이 별건으로 필요하다(D-240 부기 `eval_routing.py` 전례 — 과금 판정 `external_planes` 재사용 + `mlx`·루프백 요구).
> 편입 전까지 O3 실 완주 확인은 **선택 항목**이다(결정적 대조는 MockTransport·Docker 통합이 이미 한다).

---

## 6. 산출물·파일 배치

```
mcp_server/mcp_server/openmetrics.py             파서·정규화·카탈로그 (순수 함수 · 폴스타 리터럴 0)
mcp_server/mcp_server/openmetrics_tools.py       om_metric_instant · om_metric_catalog · 타깃 해석 · 클라이언트 · 감사
mcp_server/mcp_server/polestar_exporter.py       (O5) B-2 브리지 — overfit EXCLUDE 대칭 · [v3] SQL·패밀리 정의만
mcp_server/mcp_server/om_exposition.py           (O5 · [v3]) 벤더 중립 노출 기계(직렬화·협상·캐시·절단 게이지) — plans/87 J7 공유 · 스캔 대상
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
| I-4 | 플래그 기본 off = 비트 동일(도구 미등록·라우트 부재) · 만료일 동반(D-161) · **[v3]** `mcp_server` `expose_*`가 만료일 대상인지는 D-210 등재 때 확정한다(기존 3종에 전례 없음 — §0.0.4) | 등록 분기 · 테스트 |
| I-5 | 실 데이터 × 외부 SaaS 금지(D-120) — `om_*` 결과도 PromQL과 같은 통제 | 조사 LLM 백엔드 판정표(docs/23 §8.0) · **[v3]** 판정표에는 MLX 행이 없다(D-222·D-240 이전 작성). 로컬 MLX는 루프백이라 외부 SaaS가 아니다. 다만 MLX로 돌리는 대상은 **픽스처 데이터**로 한정한다(성능 결론 금지 — D-240 주의 ④) |
| I-6 | (v2) `source` 미지정 + `fallback_policy=off` = `prom_*` 요청·응답 **바이트 동일** · **[v3]** + `tools/list` 스키마 동일(확장 시그니처는 OM on일 때만 분기 등록) | 스냅샷 테스트(요청·응답·`tools/list` 3종) |
| I-7 | (v2) 소스 강등은 호출당 최대 1회 · 교차 검증은 명시 요청 시만 · **[v3]** 교차 검증 1회 왕복 ≤ 4 | 사다리 R5 · §4.8.5 [v3] |
| I-8 | **[v3]** 이미 `nodename`을 가진 시리즈는 `exported_nodename`으로 옮긴 뒤 주입한다(덮어쓰기 금지 — Prometheus와 같은 모양) · 허용목록 오등록은 `target_identity`로 드러낸다 | §4.2 [v3] · 단위 테스트(`node_uname_info` 스텁) |

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
| R-11 | **[v3]** 루트 `uv.lock`이 `mcp` **2.1.1**을 고정하고 있다. requires-dist에 `<2` 지정자가 없어 lock이 D-181(`mcp<2`) 이전 상태다. 최종 갱신은 2026-08-31 `1868330`이다 | `uv sync --frozen` 계열로 설치하면 `mcp.server.fastmcp`가 없어 `mcp_server`와 트랙 A·B-2가 임포트 단계에서 깨진다 | **본 계획 범위 밖**(D-181 소관). 기록만 한다. 재lock 여부는 사용자 판단이다. O0 설치 시 `mcp` 버전을 실측해 계획서에 적는다 · **✅ 해소(v3.1 · 2026-09-22 · 사용자 지시)**: `uv lock` 재생성으로 `mcp` 1.30.0(`specifier = "<2"`)이 됐다. 버전 변화는 `mcp` 계열 4건뿐이다. `mcp_server` 스위트를 1.30.0/1.29.1로 나란히 돌린 결과 회귀 0이다(D-181 부기). **O0의 `custom_route`·lifespan 실측은 1.30.0 기준으로 한다** |
| R-12 | **[v3]** `om_*`는 **현재값 전용**인데, push 조사는 사건 기준시각(`reference_time`)으로 앵커된다(`investigation_guidance.py:26-32`) | 과거 사건의 증거로 현재값을 인용 → 서술 오류 | `om_*`는 `ANCHORED_TOOLS`에 넣지 않는다. 결정적 지침 1문장 — *"`om_*` 결과는 조회 시점의 현재 상태다. 사건 구간 증거로 인용하지 말고 '현재 상태'로만 서술하라"* · 반환에 `observed_at`(스크레이프 시각)을 항상 싣는다 |
| R-13 | **[v3]** 12일간 게이트 G-1~G-8 전건 미응답. 특히 G-3(운영 exporter 실재)은 인프라 소유자 확인이 필요한 외부 질문이다 | 트랙 A 운영 가치 미확정인 채 코드가 쌓인다 | 1차를 O0~O3로 줄이고, O2 착수를 G-3 답에 묶는다(§5 [v3]) |

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

> **[v3] 게이트 갱신 (2026-09-22 — 8건 전부 미응답 · 사용자 확정 아님)**
>
> | 게이트 | v2 권고 | v3 권고 | 바꾼 이유 |
> |---|---|---|---|
> | **G-1** 범위 | (b) A+B-1 | **(a) A만**(1차 = O0~O3). B-1·B-2·C는 §5 [v3] 표의 트리거 뒤 | B-1은 스크레이퍼가 없으면 소비자가 없다. 퍼널은 관제 API로 이미 노출된다(§3.3 [v3]). C는 전제가 무너졌다(§4.6 [v3]) |
> | **G-3** 전제 | 가장 먼저 | **유지 — O2 착수 조건으로 격상** | R-13 |
> | **G-5** B-1 인증 | (ii) 정적 Bearer | 유지 · 착수 보류라 **답 불요**(B-1 재개 시 묻는다) | — |
> | **G-7** 도구 표면 | (γ) 즉시 | **단계형 — S0 (α)+힌트 → S1 (γ)(분기 등록)** | §4.8.2 [v3] — (γ)는 스키마를 바꾸고, S0에는 소스 선택지가 없다 |
> | **G-8** 강등 기본값 | `off` | 유지 · S1까지 **답 불요** | O2b가 S1 뒤로 옮겨졌다 |
>
> **지금 사용자에게 필요한 답은 G-1·G-3·G-4 셋**이다(G-2는 (i) 정적 허용목록으로 시작하면 답 불요 · G-6은 O2 완료 시점 규칙이라 그대로 둔다).
> **표기 주의**: 본문 §0.2·§4.5의 "G-6"(라벨 규약 협의)은 **docs/27 §8의 G-6**이다. 이 표의 G-6(D-210 등재 시점)과는 다른 항목이다.

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
>
> **[v3] 예약 문구 보정(등재 시 반영)**: ①1차 범위는 트랙 A S0형이다(`om_*` 2종 + 힌트 · (γ)는 S1). ②교차 검증은 6판정이다(`scrape_down` 추가 · `stale`은 `timestamp()`).
> ③타깃 신원(`target_identity` · 대조 기준 `os_hostname`)을 넣는다. ④B-2 노출 기계는 벤더 중립 모듈로 두고 `plans/87` J7과 공유한다. `nodename` = `server_name`(D-119 ③).
> ⑤C는 보류다. ⑥`expose_*`가 D-161 ① 만료일 대상인지를 확정한다. ⑦관련 결정에 D-195(예약)·D-214·D-222·D-225 ⑦·D-230·D-233·D-240을 더한다.
> 등재 직전에 `## D-` 헤더·변경 이력·채번 이력 3곳을 다시 실측한다(2026-09-22 현재 최댓값 D-248 · D-210은 예약 유지).

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
**[v3 추가]** `plans/87` v2.1 O-1·O-3·O-5·J7(`:842-865`)·G-9 · `plans/101` `:672`(트랙 A 인용 — 정정 필요) · `src/orchestration/host_inspect.py` ·
`sre_agent/sre_agent/application/investigation_guidance.py` · `noise_gate/infrastructure/decision_store.py` · `docs/02_decision.md` D-046·D-195(예약)·D-214·D-222·D-225·D-230·D-233·D-240 ·
Prometheus docs *Querying basics — Staleness* · *HTTP API — instant queries*(결과 타임스탬프 = 평가 시각) · *Functions — `timestamp()`* ·
`client_golang` `promhttp.HandlerOpts.EnableOpenMetrics` · `mcp` 1.26.0 `mcp/server/fastmcp/server.py`(`custom_route`·`sse_app`)·`mcp/server/lowlevel/server.py`(`run` → lifespan 진입).

---

## 12. 변경 이력

| 일자 | 내용 | 근거 |
|---|---|---|
| 2026-09-10 | v1 신설 — 사용자 지시("프로메테우스 연동시 오픈매트릭을 이용하는 방법을 추가할 계획을 추가하라") · 세 트랙 분리·A 우선 권고 · 실측(PromQL URL 미설정 · `prometheus-client` 미설치 · mock 0.0.4 고정 · `custom_route` 1.29.1 실재 · 픽스처 미가동) · G-1~G-6 · D-210 예약(채번 이력 표 등재) · 코드 0건 `-TODO` | 본 세션 실측 |
| 2026-09-10 | v2 — 사용자 지시("실제 promql 기반 연동과 함께 오픈매트릭을 추가적으로 연동하는 방안에 대해 검토하여 계획서에 추가하라") → **§4.8 병행(공존) 모델 신설**: 두 채널 차이표(lookback 5m 낡은 값·`job/instance`·커버리지 불일치) · 도구 표면 3형태 중 **(γ) 하이브리드 권고**(`prom_*` 유지 + `source="auto"`) · 서버 소스 사다리 R1~R5 · 호스트별 가용 소스 표 · **교차 검증 5판정**(docs/27 §6.1 5단계를 도구 1회로 대체 · Plan 81 Power off/통신 구분) · 전환 단계 S0~S3(설정 2개로 이동) · O2b Wave · I-6·I-7 · R-9·R-10 · **G-7·G-8** 신설 · D-210 예약 문구 보강 | 본 세션 검토 |
| 2026-09-22 | **v3** — 사용자 지시("현재 구현된 내용을 심도있게 분석하여 92번 계획의 적정한지 검토하고 업데이트하라") → **§0.0 적정성 판정** 신설. 결론: 방향 적정, 전제·설계·범위를 고친다. **사실 정정 12건**(F-1~F-12: exporter `nodename`·staleness·instant 타임스탬프·`inspect_host` 호출부·sre_agent 배선 줄·지침 위치·B-1 `OBS_` 키 이름·퍼널 기존재·단일 워커·`_METRIC_KIND_MAP`/`disk_io`·C 원천 부재·Plan 87 방향 역전) · **설계 결함 7건**(D-1 B-2 lifespan 접근 불가 · D-2 `nodename` 충돌/신원 확인 · D-3 `stale` 오판정 → `scrape_down` 신설 6판정 · D-4 (γ) 스키마 변경 → 분기 등록 · D-5 시간 통계 신선도·일괄 SQL·절단 · D-6 동적 키 커버리지 · D-7 세션 단위 설정) · **범위 재편**(1차 = O0~O3 S0형 · O2b는 S1 트리거 뒤 · B-1·C 보류 · B-2는 87 J7 공유 기계) · G-1 권고 (b)→(a) · G-7 권고 (γ)→단계형 · P-5·I-8·R-11~R-13 신설 · §5 과금 경계 D-240 정정 · §0.0.4 09-10 이후 결정·계획 정합(충돌 0건 · 87 `nodename` 어휘 불일치 · 101 트랙 A 인용 오류 · D-161 `expose_*` 만료일 전례 부재). 옛 서술은 지우지 않고 `[v3 정정]` 표지. 코드 0건이라 `-TODO` 유지 | 본 세션 재실측(HEAD `048c2be` · 병렬 조사 2건 + 직접 확인 · mcp 1.26.0 소스 · 픽스처·PG init 파일) |
| 2026-09-22 | **v3.1** — 사용자 지시("고치지 않은 것들을 권고에 맞게 모두 수정하라") → v3가 "기록만" 한 교차 항목 4건을 실제로 고쳤다. ①**R-11 해소**: 루트 `uv.lock`을 `uv lock`으로 재생성했다(`mcp` 2.1.1 → **1.30.0** · `httpx-sse` 추가 · `mcp-types`·`opentelemetry-api` 제거 · 그 외 버전 변화 0). `mcp_server` 스위트를 1.30.0/1.29.1로 나란히 돌려 회귀 0을 확인했다(실패는 두 버전 공통인 Windows 전용 `test_sql_log.py` 동시 쓰기뿐). D-181 부기와 변경 이력 행을 등재했다 ②**`plans/87` §5.9 J7 `nodename` = `server_name` 정정** + OS hostname 역해소 단계 + `custom_route` lifespan 부기 + 변경 이력 ③**`plans/101` §5.6** Prometheus 행 선행 조건을 트랙 A에서 `prom_metric_range`로 바꿨다(+ 참조·변경 이력 v3.1) ④**docs/27** §0 본체 채팅 행 · §3.4(`inspect_host` 호출부 있음) · §4.3(uv.lock) · §8.1 ②(ITAM — D-214로 해소) · §9 코드 위치 정정. O3에는 docs/27 §10 신설만 남는다. P-5·D-1의 SDK 근거를 1.30.0 소스로 재확인했다(`Server.run` lifespan 진입 · `sse_app` 연결별 `run` · 커스텀 라우트 말미). 계획 범위·게이트 변경 없음 · 코드 0건 `-TODO` 유지 | 본 세션 실행(uv 0.11.3 · 스크래치 venv 2개 · 저장소 밖) |
