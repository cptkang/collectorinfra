# 189. 운영 Prometheus 라벨 앵커 — 폴스타 hostname·IP ↔ `hostname`·`node_ip` 결정적 조립

> **작성일**: 2026-10-20 · **v1.0**
> **상태**: **WIP — 구현 완료 · 커밋 전(2026-10-20)** · 게이트 **G-1·G-2·G-3 전건 확정(사용자 · G-2 = A안)**(§6) · W1~W7 구현·검증 · 구현 중 해석·이탈은 §2.6 · **잔여**: ① 폐쇄망 표본 왕복(§4 ①~⑤ · 사용자) ② Docker IT 6건 실행(이 PC에 Docker 없음) ③ ruff·mypy 실검사(도구 없음) ④ 심층 브리핑 `_deep_section`의 `tool_names` 필터 비대칭(이탈 ⑦과 같은 결 · 미교정) ⑤ 보안 L-4(미등록 source 오류 문구에 활성 source 목록 노출 — 기존 패턴과 같음) ⑥ `plans/92` B-2 조인 키 재판정
> **계획 시작 SHA**: `ee9e4af`
> **진행(크래시 재개용 · 2026-10-20)**: W1~W6 완료(크래시로 깨진 `build_anchor_lookup_sql` 문자열 `\n` 복구 포함) · 계획 끝 회귀 1회 · verifier·security-auditor 1라운드(Critical 0) · 교정 8항목 반영 · W7 완료
> **검증**: 계획 끝 `regress.py --base ee9e4af --files <14파일>` rc=1 — 실패 귀속: mcp_server 3건 중 `test_readonly_fixed_sql` 빌더 커버리지 1건은 우리 변경(케이스 추가로 교정) · `test_sql_log` 동시성 2건은 기준 worktree에서도 실패(Windows) · 본체 선택분은 도구가 `-n`(xdist 미설치)으로 rc=4 → 직접 실행 309 passed·2 failed(둘 다 기준 실패 · `test_index_matches_sources`는 매뉴얼 재빌드 뒤 통과 — `tests/test_manual` 675 passed) · sre_agent 2,087 passed·74 failed·2 errors(기준 worktree와 실패 ID 대조 신규 0) · arch/overfit/config_fallback 통과 · **범위: 모듈 단위 — 전체 미실행** · 교정 라운드 뒤 대상만 재실행: mcp_server 492 passed·9 skipped / sre_agent 133 passed(메인 세션 재확인) · 교정분 `regress --no-tests` rc=0(`범위: 정적 게이트만 — 테스트 미실행`)
> **회귀 대상 파일**: `mcp_server/mcp_server/{config.py, polestar_tools.py, promql_tools.py, metric_source.py}` · `mcp_server/{.env.example, config.toml}` · `mcp_server/tests/{test_plan189_prom_anchor.py, test_plan189_verifier.py, test_readonly_fixed_sql.py}` · `sre_agent/sre_agent/{settings.py, application/investigation_guidance.py, application/investigation_dispatcher.py}` · `sre_agent/.env.example` · `sre_agent/tests/{test_plan189_prom_anchor_guidance.py, test_plan189_verifier_dispatch.py}` · `testdata/prometheus/{prometheus.yml, docker-compose.yml}` · `scripts/manual/content/admin.md` · `src/static/manual/{admin.html, user.html, help_index.json}` · `docs/27_prometheus_integration_guide.md`
> **발단**: 폐쇄망에서 `mcp_server` ↔ 운영 Prometheus 연결을 확인했다. 그러나 고수준 PromQL 도구가 조립하는 `{nodename="…"}` 라벨이 운영에 없어 조회가 **전건 빈 결과**다(`docs/27` §8 G-6 실현).
> **성격**: 행동 변경(D-337 ⑥) — 운영 설정을 켰을 때만 바뀐다. 설정 기본값(`nodename`)은 현행과 동일하게 둔다.
> **관련 결정**: **D-119**(③ 앵커 개정 부기 2026-10-20 — 이 계획의 정본) · D-035(결정적 가드) · D-120(실데이터 조사 LLM = 사내 백엔드) · D-271(공동존 김포·여의도) · D-089(DB별 SQL 격리) · D-088(overfit 스캔 — `mcp_server/mcp_server` 독스트링 포함) · D-255(매뉴얼 동반)
> **이관**: `plans/91` §1.3 **1-12**(운영 Prometheus `nodename` 규약 실측)의 실측은 끝났다. 남은 구현은 이 계획이 단일 소유한다.
> **회귀**: `mcp_server`는 루트 수집 밖 — `cd mcp_server && ../.venv/bin/python -m pytest <파일>` · `sre_agent`는 자체 venv

---

## 0. 실측 요약 (2026-10-20 · 폐쇄망 · D-119 부기 원문 요약)

| 항목 | 결과 | 설계에 미치는 영향 |
|---|---|---|
| 타깃 라벨 | `hostname`(OS) · `node_ip`(OS IP) · `instance`(=`node_ip:port`) · `resources`(VM name — 교체 시 낡음) · `name` · `center` · `company` · `project` · `type="node"` 등. **`nodename` 없음** | 현행 조립 전건 0건 |
| 커버리지 | `up{type="node"}` 1229 · `hostname!=""` 1204 · `node_ip!=""` 1204 | 25 시리즈는 키 없음 → 범위 밖 |
| `hostname` 유일성 | 물리 호스트명이 들어간 그룹 8개(2~5건) · 동일값 중복 4쌍 | `hostname` 단독 조회는 여러 VM을 섞을 수 있다 |
| `node_ip` 유일성 | `count by (node_ip)(up{type="node"}) > 1` → **`{}` 25 한 건뿐**(공백 25) — **비어 있지 않은 `node_ip`는 유일** | `node_ip`가 가장 강한 키 |
| 표기 | 폴스타 `hostname`과 Prometheus `hostname`은 **같은 값**(둘 다 OS에서 가져옴) · 일부 FQDN도 양쪽이 같은 FQDN | **정규화하지 않는다** — 완전 일치 비교 |
| 적용 범위 | 공동존(`polestar_cm_gp`·`polestar_cm_yd`)의 **관제 요청 서버만** 등록 · **은행존 미사용** | 0건 = 「관제 미등록」 · b0은 호출 전 차단 |
| 메트릭 이름 | 표준 node_exporter `node_*` | 도구 `metric` 인자 계약 불변 |

## 1. 현행 (코드 실측)

| 위치 | 현행 동작 | 문제 |
|---|---|---|
| `mcp_server/mcp_server/promql_tools.py:78-98` `build_nodename_matcher`·`build_high_level_selector` | `metric{nodename="<hostname>"}` 고정 조립 | 라벨 이름 설정 불가 |
| 같은 파일 `:465-520` 도구 설명 | 「hostname: 폴스타 등록 서버명(=Prometheus nodename 라벨)」 | 공동존 등록명은 VM name — 앵커로 못 쓴다(D-119 부기 ⓒ) |
| `mcp_server/mcp_server/metric_source.py:90·336-345·537·618-624` | 커버리지 = `/api/v1/label/nodename/values` · `up{nodename=…}` | 같은 `nodename` 가정 |
| `mcp_server/mcp_server/config.py:54-66·265-271·398-417` `PrometheusConfig` | url·auth_header·query_timeout·expose_raw_promql | 앵커·적용 존 설정 없음 |
| `mcp_server/mcp_server/polestar_tools.py:247` `build_resource_status_sql` | `SVR.NAME = <server_name>` 매칭 | hostname·ipaddress를 돌려주는 조회가 없다 |
| `sre_agent/sre_agent/application/investigation_guidance.py:44-49·301` | `prom_metric_range`를 앵커 도구로 안내 | 빈 결과 사유를 다루는 지침 없음 |
| `testdata/prometheus/prometheus.yml` | `static_configs.labels.nodename` 주입 | 운영 규약(`hostname`·`node_ip`) 픽스처 없음 |

폴스타 `cmm_resource`에는 `name`(VM명) · `hostname`(OS 호스트명) · `ipaddress` 칼럼이 있다(`config/db_profiles/polestar.yaml` `entity_columns`).

## 2. 설계

### 2.1 설정 (`[prometheus]` · env 오버라이드)

| 키 | 기본 | 운영 권고 | 의미 |
|---|---|---|---|
| `anchor_mode` / `PROMETHEUS_ANCHOR_MODE` | `nodename` | `polestar_host` | `nodename` = 현행 바이트 동일 · `polestar_host` = §2.3 사다리 |
| `host_label` / `PROMETHEUS_HOST_LABEL` | `hostname` | `hostname` | Prometheus의 OS 호스트명 라벨 이름 |
| `ip_label` / `PROMETHEUS_IP_LABEL` | `node_ip` | `node_ip` | Prometheus의 OS IP 라벨 이름 |
| `instance_label` | `instance` | `instance` | 「모호」 판정용 — 서로 다른 값의 수를 센다 |
| `sources` / `PROMETHEUS_SOURCES` | `[]`(= 제한 없음 · 현행) | `["polestar_cm_gp","polestar_cm_yd"]` | Prometheus가 수집하는 폴스타 source. 목록 밖 source는 HTTP 없이 `prometheus_unsupported_zone` |

- 존 id는 코드에 넣지 않는다(overfit · D-088). 운영 값은 `config.toml`에 둔다. 폐쇄망 반입 시 `config.toml`·`.env`는 덮어쓰지 않고 diff로만 맞춘다.
- `mcp_server/.env.example`에 키를 추가하고 `tests/test_env_example_coverage.py`를 통과시킨다.

### 2.2 앵커 해석 — 서버가 폴스타에서 조회한다

- 도구 인자는 `hostname`(종전 이름 유지)과 **선택 인자 `source`**(**G-1 확정** — MCP 공개 시그니처에 추가)다. 값은 폴스타 등록명이나 OS 호스트명 어느 쪽이든 받는다. `anchor_mode=nodename`(기본)에서는 `source`를 받아도 쓰지 않는다(현행 동일).
- 서버는 `cmm_resource`(`server.Server` · `DTIME IS NULL`)에서 `NAME = x OR HOSTNAME = x`인 행의 `hostname`·`ipaddress`를 조회한다. `source`가 있으면 그 source만, 없으면 `sources` 목록 전체에서 찾는다. `source`가 `sources` 밖이면 조회 전에 `prometheus_unsupported_zone`이다.
  - 1행 → 그 값을 앵커로 쓴다.
  - 0행 → **Prometheus를 부르지 않고** `reason="polestar_not_found"` + 고정 힌트(「폴스타 등록명이나 OS 호스트명을 그대로 넘겨라. `source`를 지정했다면 빼고 다시 불러라」)(**G-2 확정 = A안**).
  - 폴스타 조회 오류 → 사다리 3단(`{hostname="x"}`)만 시도하고 「모호」 판정을 유지한다. 응답에 `matched_by="hostname_unverified"` · `anchor=null` · `warning="폴스타 조회 실패 — 서버 신원 미확인"`을 싣는다. 0건이면 `reason="anchor_unresolved"`(「관제 미등록」으로 쓰지 않는다). 오류 원문은 로그에 남긴다(침묵 폴백 금지).
  - 2행 이상 → `reason="polestar_ambiguous"` + 후보(source·name) 반환. 합치지 않는다.
- SQL 빌더는 `polestar_tools.py`의 기존 빌더 옆에 순수 함수로 둔다(방언: DB2 대문자 스키마 · `FETCH FIRST`). **결과 캐시는 두지 않는다(G-3 확정)** — 호출마다 폴스타를 조회해 VM 교체를 바로 반영한다.

### 2.3 조립 사다리 (AND 고정 금지)

| 단 | 셀렉터 | 성립 조건 |
|---:|---|---|
| 1 | `metric{hostname="h",node_ip="ip"}` | 결과 ≥1 · 서로 다른 `instance` 1개 |
| 2 | `metric{node_ip="ip"}` | 같음 — `node_ip`는 실측상 유일하므로 주력 대체 단 |
| 3 | `metric{hostname="h"}` | 같음 · **서로 다른 `instance`가 2개 이상이면 `reason="prometheus_ambiguous"`**(물리 호스트명 그룹 대응) |

- 앵커 값이 비어 있는 단은 건너뛴다(폴스타 `ipaddress` 공백 등).
- 성립 판정은 `up{…}` instant 1회로 한다. range 도구는 성립한 셀렉터로 본 조회를 1회 한다 — 최대 HTTP는 `up` 3회 + 본 조회 1회다.
- 라벨 값은 기존 `_label_value_escape`로 이스케이프한다. 정규식 매처(`=~`)는 쓰지 않는다.

### 2.6 구현 중 확정한 해석·이탈 (2026-10-20 · 검토 수용)

| # | 내용 | 이유 |
|---|---|---|
| 해석 1 | OM 확장 `prom_metric_instant`(plans/92 · 기본 off)는 `source`를 이미 메트릭 소스(`auto`·`prometheus`·`exporter`)로 쓴다. 시그니처는 그대로 두고, `anchor_mode=polestar_host`일 때만 세 값 밖의 `source`를 db_id로 보고 메트릭 소스는 `auto`로 처리한다. 레거시 `prom_metric_instant`·`prom_metric_range`에는 G-1대로 `source`(db_id)를 추가했다 | 기존 OM 계약·테스트 무수정 |
| 해석 2 | `sources=[]`(제한 없음)이면 풀의 활성 source 중 postgresql·db2 엔진만 찾는다. 설정 `sources` 중 풀에 없거나 비활성인 source는 경고 로그만 남기고 대상에서 뺀다(실패로 세지 않음) | ITAM 등 비폴스타 DB 제외 · 미배포 source가 G-2 강등을 유발하지 않게 |
| 해석 3 | 일부 source만 조회 오류이고 다른 source에서 행이 나오면 그 행으로 판정하고, warning에 실패 source를 적는다 | 부분 장애에서도 신원 확인 가능 |
| 이탈 ① | 「모호」 판정(서로 다른 `instance` 2개 이상)을 3단만이 아니라 **모든 단**에 적용한다 | 1·2단도 섞인 결과를 합치지 않는다 |
| 이탈 ② | range 도구에 `reference_time`이 있으면 `up` 성립 판정 시각을 구간 끝(`time=end`)으로 둔다 | 과거 사건 구간의 VM을 현재 시각이 아니라 그 시각 기준으로 찾는다 |
| 이탈 ③ | HTTP 0회 응답(존 차단·`polestar_not_found`)도 감사 로그 1줄을 남긴다(`query=(미조회)` · `reason=…`) | 감사 일원화(D-119 ④) |
| 이탈 ④ | 폴스타 행은 1개인데 `hostname`·`ipaddress`가 모두 비면 입력값으로 3단만 시도하고 `matched_by=hostname_unverified`, 0건이면 `anchor_unresolved` | G-2 A안의 「신원 미확인」과 같은 처리 |
| 이탈 ⑤ | 백슬래시가 든 hostname은 호출 오류로 처리한다. 앵커 모드에서 hostname 255자·source 64자를 넘으면 거부한다(nodename 모드는 종전대로 무제한) | 라벨 주입·로그 오염 방어(보안 L-1·L-2) |
| 이탈 ⑥ | OM 교차 검증은 Prometheus 응답에 `reason`이 있으면 판정하지 않고 `inconclusive_reason=<그 reason>`으로 낸다(`prometheus_not_monitored`·`anchor_unresolved` 포함) | 모호·존 차단 응답으로 scrape_down·not_scraped 오판정이 나던 것 교정 |
| 이탈 ⑦ | sre_agent는 reason 달린 `prom_*` 0건 응답을 브리핑 인용 근거에서 빼고 한계 1줄로 알린다. reason 판독은 JSON 구조 우선, 실패 시에만 정규식. reason 어휘는 `promql_tools.py`의 `REASON_*`와 같은지 텍스트 대조 가드로 고정한다(패키지 간 import 0) | `plans/188` C3와 같은 결 — 0건을 근거로 인용 금지 |

### 2.4 반환 계약 (기존 `_ok`에 필드 추가 · 기존 필드 불변)

| 필드 | 값 |
|---|---|
| `matched_by` | `hostname+node_ip` · `node_ip` · `hostname` · `nodename`(현행 모드) |
| `tried` | 시도한 셀렉터 목록 |
| `anchor` | `{source, name, hostname, ipaddress}` — 폴스타에서 해석한 값 |
| `reason` (0건일 때만) | `prometheus_not_monitored`(전 단 0건 — 관제 미등록) · `prometheus_ambiguous` · `polestar_ambiguous` · `prometheus_unsupported_zone` · `polestar_not_found` · `anchor_unresolved`(G-2 A안) |
| `hint` (0건일 때만) | 「Prometheus 미수집 서버다. 폴스타 지표(`polestar_metric_trend`)로 확인하라」 — 고정 문장 |

0건은 `{error}`가 아니라 성공 응답 + `reason`으로 낸다. 그래야 `plans/188` C3(실패 호출을 근거로 인용)과 섞이지 않는다. 다만 브리핑 쪽에서 0건 응답을 근거로 인용하지 않는지 W5에서 확인한다.

### 2.5 OpenMetrics 경로 (`metric_source.py`)

- `anchor_mode=polestar_host`이면 커버리지를 `/api/v1/label/<ip_label>/values`로 계산하고, `up` 확인은 §2.3 사다리 함수를 재사용한다.
- `VERDICT_LABEL_MISMATCH` 문구의 「nodename」을 설정한 라벨 이름으로 바꾼다.
- `plans/92` B-2 브리지(`polestar_*{nodename=server_name}`)와의 조인 규약 불일치는 이 계획에서 고치지 않는다 — 92에 기록만 한다(§5).

## 3. Wave

| Wave | 내용 | 대상 파일 | 완료 판정 |
|---|---|---|---|
| W1 | 설정 5키 · env 오버라이드 · `.env.example` | `mcp_server/mcp_server/config.py` · `mcp_server/.env.example` · `mcp_server/config.toml`(주석만) | 기본값에서 `PrometheusConfig` 동등 · 커버리지 테스트 통과 |
| W2 | 폴스타 앵커 해석 SQL·함수(PG·DB2) | `mcp_server/mcp_server/polestar_tools.py`(또는 `polestar_helpers.py`) | 1행·0행·2행 이상 · 방언 2종 단위 테스트 |
| W3 | 사다리 조립·판정·반환 필드 · 존 차단 · 도구 인자 `source` · 도구 설명 교정 | `mcp_server/mcp_server/promql_tools.py` | `nodename` 모드 바이트 동일(기존 52건 통과) · 사다리 3단·모호·미등록·존 차단 테스트(`httpx.MockTransport`) |
| W4 | 커버리지·`up` 확인 라벨 설정화 | `mcp_server/mcp_server/metric_source.py` | OM off 바이트 동일 · `polestar_host` 모드 판정 테스트 |
| W5 | 조사 지침 — `hostname` 인자 의미 · `source` 인자 안내(알람 `dbId`를 그대로 넘긴다 · G-1) · `reason` 처리(폴스타 지표로 대체 · 0건을 「정상」으로 서술 금지) · 0건 응답 근거 인용 여부 확인 | `sre_agent/sre_agent/application/investigation_guidance.py` · 필요 시 `briefing_builder.py` | 지침 렌더 단언 테스트 · 기존 `test_investigation_guidance` 통과 |
| W6 | 픽스처 — `hostname`·`node_ip` 타깃 라벨 + 「`resources` 낡음」 타깃 1개 + 같은 `hostname` 두 타깃 | `testdata/prometheus/{prometheus.yml,docker-compose.yml}` | `RUN_DOCKER_IT=1` 통합 테스트로 사다리 1·2·3단과 모호 판정 실 HTTP 확인 |
| W7 | 문서·기록 — `docs/27` §0·§3.5·§6·§8 G-6 · 매뉴얼 A-58(설정 키가 늘어 관리자 대상 · D-255) · D-119 구현 부기 · `plans/91` 1-12 종결 · 폐쇄망 반입 파일 목록 + 확인 grep 심볼 | 문서 | `pytest tests/test_manual` 통과 |

- W1~W4는 `mcp_server` 한 패키지라 implementer 1명이 순서대로 한다. W5는 `sre_agent`이므로 별도 위임한다.
- `overfit_check`: 새 SQL·독스트링에 스키마 리터럴이 늘면 기존 빌더와 같은 위치·패턴을 따르고, 기준선은 자기 델타만 소거한다.

## 4. 검증

- **단위**: 모두 `httpx.MockTransport` + 가짜 DB 풀. 실 Prometheus는 필요 없다.
- **현행 동일성**: `anchor_mode` 미설정에서 `test_promql_tools.py`·`test_metric_source_ladder.py` 등 기존 테스트가 수정 없이 통과해야 한다.
- **회귀**: 계획 끝에 1회 `python scripts/regress.py --base ee9e4af --files <계획이 바꾼 파일…>` + `mcp_server`·`sre_agent` 자기 테스트.
- **폐쇄망 확인(사용자)**: 표본 3종으로 `prom_metric_instant` 왕복 —
  ① 정상 서버 → `matched_by=hostname+node_ip`
  ② VM 교체 서버(`resources` 낡음) → `hostname+node_ip` 또는 `node_ip`(폴스타 등록명을 넘겨도 성립해야 한다)
  ③ 물리 호스트명 그룹 서버 → `node_ip`(1단은 `instance` 1개면 성립)
  ④ 관제 미등록 공동존 서버 → `reason=prometheus_not_monitored`
  ⑤ 은행존 서버 → `reason=prometheus_unsupported_zone` · Prometheus HTTP 0회(감사 로그는 `query=(미조회) reason=prometheus_unsupported_zone` 1줄 — §2.6 이탈 ③)
- 실데이터 조사 LLM은 사내 백엔드만 쓴다(D-120).

## 5. 다른 계획과의 관계

- `plans/91` 1-12: 실측 완료 → 이 계획으로 이관(91 행에 표기).
- `plans/92` B-2 브리지: `nodename = server_name` 규약을 전제한다. 운영 Prometheus는 `nodename` 라벨을 쓰지 않고 공동존 등록명(VM name)으로는 매칭이 안 되므로, B-2를 운영에 켜기 전에 조인 키를 `hostname`·`node_ip`로 맞출지 재판정해야 한다(92 머리에 기록).

## 6. 확정 게이트

| # | 질문 | 상태 |
|---|---|---|
| G-1 | 도구에 선택 인자 `source`를 추가하는가(MCP 공개 시그니처 변경) | **확정(2026-10-20 · 사용자) — 추가**. 공동존 두 DB에 같은 이름이 있을 때 구분 수단이 된다. 없을 때는 `sources` 전체를 찾는다 |
| G-2 | 폴스타 앵커 해석이 실패하면(0행 · 조회 오류) `{hostname="x"}`만으로 Prometheus를 조회하는가 | **확정(2026-10-20 · 사용자) — A안: 0행은 차단 · 조회 오류만 표시 강등**(v1.0 권고 「항상 시도」에서 개정). 근거는 §6.1 |
| G-3 | 앵커 해석 결과를 캐시하는가 | **확정(2026-10-20 · 사용자) — 캐시하지 않음**. 조사당 호출 수가 적고, VM 교체를 즉시 반영하는 편이 중요하다 |

### 6.1 G-2 판단 근거

**언제 해석이 실패하는가**

| 경우 | 예 | `{hostname="x"}` 단독 조회의 결과 |
|---|---|---|
| ① 폴스타 조회 0행 — 표기가 다른 값 | 단축명 `web01`을 넘겼는데 폴스타·Prometheus 둘 다 FQDN | 0건 — 완전 일치라 도움이 안 된다 |
| ② 0행 — 폴스타에 없는 값 | 물리 호스트명 · 다른 시스템(APM 등)의 이름 · 지시어 오해로 만든 이름 | 물리 호스트명이면 **엉뚱한 VM 1대**가 잡힐 수 있다(아래 위험 1) |
| ③ 0행 — 잘못된 `source` | 여의도 서버에 `source=polestar_cm_gp` | 다른 source에 있는 서버라면 맞는 값이 나오지만, 앵커(IP)가 없어 단 1·2를 못 쓴다 |
| ④ 폴스타 DB 조회 오류 | 장애 조사 대상이 바로 폴스타 DB 또는 그 망 | Prometheus는 독립 소스라 **이때가 가장 쓸모 있다** |

**조회 키에 이미 `HOSTNAME = x`가 들어 있다(§2.2)**: OS 호스트명을 넘겨도 폴스타에 있으면 1행으로 해석된다. 그래서 0행 강등이 실제로 더 살리는 것은 ②·③뿐이고, ①은 어차피 0건이다.

**위험**
1. **오귀속을 판정으로 막지 못한다** — 「모호」 판정은 같은 `hostname`에 instance가 2개 이상일 때만 걸린다. 물리 호스트명 아래 VM이 1대뿐이면 `count by (hostname) > 1` 실측에도 나타나지 않고, 그 VM의 지표가 요청한 이름의 지표로 반환된다.
2. **앵커가 없다** — 폴스타 행이 없으므로 응답의 `anchor`가 비고, 브리핑이 그 지표를 폴스타의 어느 서버와 묶을지 근거가 없다.
3. **존 판정이 빠진다** — source를 모르면 은행존 차단이 실제로는 「Prometheus 0건 → 관제 미등록」으로 바뀐다. 사유가 틀린다.

**권고(개정)**
- **0행(①②③)은 차단** — Prometheus를 부르지 않고 `reason="polestar_not_found"`로 반환한다. 힌트 문장: 「폴스타 등록명이나 OS 호스트명을 그대로 넘겨라. `source`를 지정했다면 빼고 다시 불러라」. ③은 `source`를 빼면 전체 source에서 찾으므로 한 번 더 부르면 해소된다.
- **조회 오류(④)만 표시하고 강등** — `{hostname="x"}` 단독으로 3단만 시도한다(「모호」 판정 유지). 응답에 `matched_by="hostname_unverified"` · `anchor=null` · `warning="폴스타 조회 실패 — 서버 신원 미확인"`을 싣는다. 0건이면 `reason="anchor_unresolved"`로 낸다(「관제 미등록」으로 쓰지 않는다).
- **운영 뒤 재판정** — 감사 로그에서 `polestar_not_found` 건수와 넘어온 값을 본다. ②가 잦으면 0행 강등을 이 표시 방식으로 넓히는 것을 다시 검토한다.
