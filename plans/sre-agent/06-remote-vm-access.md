# 06. 원격 VM 접근 — 프로메테우스 + 폴스타 MCP (Remote VM Access)

> 작성일: 2026-07-24 · **이관일: 2026-07-24** (SREAgent → collectorinfra `plans/sre-agent/`, 통합 결정: collectorinfra D-118 / SREAgent D-021)
> **결정 배경**: 사용자 지시(2026-07-24) — "원격 VM 접근은 프로메테우스와 폴스타 MCP를 기반으로 동작"(D-019). `docs/03_vm_diagnosis.md` §4의 옵션 비교를 확정한다: 옵션 3(중앙 관측 스택 경유)을 **Prometheus·폴스타 MCP 두 축**으로 구체화하고, SSH 경로(옵션 2·4)는 채택하지 않는다.
> **관련 결정**: D-004(VM 진단 대상·읽기 전용 프로파일), D-013(폴스타 MCP 일원화 — Plan 04), D-016(collectorinfra 연동 — Plan 05)
> **신규 결정(본 계획 예약, 구현 착수 시 등재)**: D-020(원격 VM 프로파일 구현 세부 — hostname 정합 규약 포함). ※ 이관 후 등재는 **collectorinfra `docs/02_decision.md` 번호 체계**를 grep해 그쪽 최댓값+1로 부여한다(이 D-번호는 SREAgent 체계의 예약 인용).
> **상태**: 계획(미구현) — **통합 갱신(2026-07-24)**: 구현 위치는 `sre_agent/` 독립 패키지(README). 폴스타 MCP 축은 본 저장소 `mcp_server/`(Plan 04 확장) 소비로 동일하다. **갱신(2026-07-27 · collectorinfra D-119)**: Prometheus 축의 전송 경로를 holmesgpt 내장 `prometheus/metrics` toolset 직결에서 **`mcp_server` 노출 PromQL 도구 경유로 변경** — 2축(소스) 구도는 유지하되 하향 의존은 `mcp_server` 하나가 된다. §1·§2·§3·§5·§6·§8이 이 기준으로 갱신됨(내장 toolset 세부는 §3 말미에 A안 복귀 폴백 참고로 보존).
> **번호 체계 주의**: 본 문서의 D-번호는 SREAgent(이관 전) 결정 체계의 인용 — collectorinfra D-번호와 무관(폴더 README 참조).

---

## 1. 개요 및 결정

원격 VM 진단의 데이터 경로를 다음 2개로 한정한다. 대상 VM에는 SREAgent를 배포하지 않으며(에이전트는 중앙 1곳 실행), SSH도 사용하지 않는다.

1. **Prometheus** — 실시간·고해상도 시계열. **(D-119) `mcp_server`가 노출하는 PromQL 도구 경유**(hostname 앵커 고수준 기본 + 원시 옵트인 — §3). holmesgpt 내장 `prometheus/metrics` toolset 직결은 미채택(§8 수용 기준 7의 품질 게이트 실패 시에만 복귀 폴백).
2. **폴스타 MCP** — 자원 메타·알람 이력·구성·프로세스 단면·장기 메트릭 통계. Plan 04의 자체 MCP 서버를 `Config.mcp_servers`로 등록 (RemoteMCPToolset 자동 발견 — Plan 04 §7.2 실측).

```
              [SREAgent DiagnosisAgent — 중앙 실행]
           HolmesGPT ToolCallingLLM + remote_vm_profile()
              │ RemoteMCPToolset — 하향 의존은 mcp_server 하나 (D-119)
              ▼ MCP (SSE)
     [mcp_server — 관측 데이터 읽기 접근 경계 (Plan 04)]
     폴스타 고수준 도구 8종            PromQL 도구 (D-119 신규)
              ▼ SELECT/GET (읽기 전용)          ▼ HTTP (PromQL)
   [폴스타 PG(gp/yd) · DB2(b0) · REST]    [Prometheus 서버]
                                               ▲ scrape
                                    [대상 VM: node_exporter …]
```

SSH 미채택 근거(D-004에서 확인): bash allowlist는 로컬 명령 prefix만 검증하므로 `ssh host <임의 명령>`의 원격 명령을 통제할 수 없다. 읽기 전용 원칙을 코드·서버 경계에서 강제할 수 있는 경로만 남긴다.

## 2. 두 소스의 책임 경계 (핵심 설계)

같은 VM에 대해 메트릭 소스가 둘(Prometheus 실시간, 폴스타 `cmm_metric_stat_h/d/m` 롤업)이므로, **소스 선택 기준을 지침으로 명시**해 LLM의 소스 혼동을 막는다(LLM 비결정성 대응 원칙 — **(D-119) 등록이 `mcp_server` 하나이므로 지침도 MCP `llm_instructions` 한 곳에 통합 주입**, 종전 `system_prompt_additions`/`llm_instructions` 양분 해소).

| 질문 유형 | 담당 | 도구 |
|---|---|---|
| 실시간·초/분 단위 시계열, 임의 PromQL | **Prometheus** | (D-119) `mcp_server` PromQL 도구 — hostname 앵커 고수준(기본) + 원시 패스스루(옵트인), 도구명·표면은 R-B 착수 시 확정(§3) |
| 메트릭 탐색(이름·라벨·메타데이터) | Prometheus | (D-119) `mcp_server` 탐색 도구(labels/metadata/series — 원시군과 동일 옵트인 여부 착수 시 결정) |
| 장기 추세(시간/일/월 롤업, 폴스타 수집 기준) | 폴스타 MCP | `polestar_metric_trend` |
| 자원 메타·가용 상태·토폴로지 | 폴스타 MCP | `polestar_resource_status`·`polestar_topology` |
| 알람 이력·조건 로그 | 폴스타 MCP | `polestar_alarm_history`·`polestar_condition_log` |
| 프로세스 실시간 단면(cpu/mem 랭킹) | 폴스타 MCP | `polestar_process_snapshot` |
| OS·커널 구성 | 폴스타 MCP | `polestar_os_config` |
| 변경 이력 대조 | 폴스타 MCP | `polestar_change_history` |
| 원격 포트 개방 확인 | 내장 | `connectivity_check` |

교차 검증 규칙(지침에 포함): 급변 구간은 Prometheus로 확인하고, Prometheus 미커버 자원(스크레이프 대상 아님)은 `polestar_metric_trend`로 폴백하되 답변에 소스와 해상도 한계를 명시한다.

## 3. Prometheus 연동 — `mcp_server` PromQL 도구 경유 (D-119)

**(D-119 갱신)** holmesgpt 내장 toolset 직결 대신 **`mcp_server`가 Prometheus HTTP API를 래핑해 PromQL 도구로 노출**한다(서버측 스코프·감사·인증은 Plan 04 §4.4 관할, 도구 명세는 본 절).

- **도구 표면(잠정 — R-B 착수 시 확정)**:
  1. **고수준(기본 노출)**: hostname 앵커 — 인자를 `hostname(=폴스타 server_name)`으로 받아 서버가 `{nodename="<hostname>"}` 필터를 결정적으로 조립(§5-0). 예: `prom_metric_range(hostname, metric, window, step)`·`prom_metric_instant(hostname, metric)`.
  2. **원시(옵트인)**: instant/range/labels/metadata/series 패스스루 — `execute_sql` 기본 숨김 전례(`expose_raw_promql=true` 시에만). Prometheus HTTP API는 태생 읽기 전용이라 SQL형 검증 계층은 불요하나, 쿼리 timeout 파라미터를 서버가 강제한다.
  3. 반환·오류 계약은 폴스타 도구와 동일(JSON `{data..., queried_at, source_kind: "prometheus"}` · `{"error": ...}`).
- **서버측 일원화**: `PROMETHEUS_URL`·인증 헤더(`PROMETHEUS_AUTH_HEADER`)는 `mcp_server` 설정에만 존재(`sre_agent` 미보유), 도구 호출·쿼리·소요·행수 감사 로깅은 폴스타 도구와 동일 파이프.
- **수집 전제(불변)**: 대상 VM에 node_exporter(+ 필요 시 process/blackbox/DB exporter) 설치, Prometheus가 스크레이프. 스크레이프 설정에서 **VM 식별 라벨 규약**(§5)을 표준화한다.
- **(폴백 참고 — A안 복귀 시에만)** holmesgpt 내장 `prometheus/metrics` toolset 0.36.0 실측: 도구 8종(`ExecuteInstantQuery`·`ExecuteRangeQuery`·`GetAllLabels`·`GetLabelValues`·`GetMetricMetadata`·`GetMetricNames`·`GetSeries`·`ListPrometheusRules`), `PrometheusConfig`(`prometheus_url`·`additional_headers`·query timeout 20s/최대 180s·`rules_cache_duration_seconds=1800` 등), URL 폴백(config → env `PROMETHEUS_URL` → k8s 디스커버리 — VM 환경은 명시 설정 원칙·미설정 시 prereq 실패 사유 반환), 검증 시 `PrerequisiteCacheMode.DISABLED` 필수(캐시 히트 시 파싱 생략 함정, `docs/18_known_mistakes.md` 기실측). 복귀 판단은 §8 수용 기준 7의 품질 게이트.

## 4. 폴스타 MCP 연동 (Plan 04 소비 — 재기술 최소화)

Plan 04 §7.2의 `Config.mcp_servers` 등록을 그대로 사용한다(`mode=sse`, `headers` Bearer — Plan 04 §6-4, `health_check_tool="list_sources"`). 본 계획의 추가 사항은 하나다:

- `llm_instructions`에 §2의 소스 선택·교차 검증 규칙을 기술한다 — **(D-119) Prometheus 측 규칙도 같은 `llm_instructions`에 통합**(등록이 `mcp_server` 하나이므로 주입 지점도 하나, `system_prompt_additions` 양분 해소). 착수 시 **지침이 실제 시스템 프롬프트에 포함되는지 실측**한다(Plan 04 §7.2 주의 계승 — mock 통과 ≠ 프로덕션).

## 5. hostname 정합 규약 (D-020 예약 — 본 계획의 최대 리스크)

Prometheus 라벨(`instance`=ip:port 관례)과 폴스타 식별자(`server_name`/`hostname`)가 불일치하면 LLM이 서로 다른 VM을 잇는 오류가 난다. 방침:

0. **서버측 결정적 조립(D-119 — 최우선 방어)**: 고수준 PromQL 도구가 `hostname` 인자에서 `{nodename="<server_name>"}` 필터를 **서버에서 조립** — LLM이 라벨을 직접 다루지 않는다. 아래 1·2는 유지되며, 2(지침)의 잔존 의존은 원시 도구 옵트인 경로에만 남는다.
1. **수집 측 표준화(1차)**: 스크레이프 설정에서 `nodename` 라벨을 폴스타 `server_name`과 동일 값으로 부여(node_exporter `node_uname_info`의 nodename 활용 또는 relabel). 규약을 본 계획 산출물로 문서화.
2. **지침 명시(2차)**: "Prometheus에서는 `nodename="<server_name>"` 라벨로 필터하라"를 시스템 프롬프트에 포함.
3. 라벨 표준화가 불가능한 기존 스크레이프가 발견되면 매핑 테이블(설정 파일)을 도입하되, **착수 시 실제 스크레이프 설정을 실측한 후** 결정한다(추정 금지). → D-020 등재 시 확정.

## 6. 프로파일 이원화 — `remote_vm_profile()`

`sre_agent` 패키지의 `toolset_profiles.py`(SREAgent에서 이관)에 원격용 프로파일을 추가한다. 로컬용 `vm_profile()`(D-004)은 유지.

| toolset | vm_profile (로컬) | remote_vm_profile (본 계획) | 근거 |
|---|---|---|---|
| `bash` | VM_DIAG_ALLOW 확장 | **확장 안 함** (내장 core 텍스트 유틸만) | 중앙 실행 호스트의 `ps`·`free` 출력은 대상 VM 정보가 아님 — LLM 오인 방지. "로컬 셸은 대상 VM이 아니다" 지침 병기 |
| `kubernetes/logs` | 비활성 | 비활성 | k8s 아님 (D-004) |
| `connectivity_check` | 유지 | 유지 | 원격 TCP 확인은 원격에서도 유효 |
| `core_investigation`·`internet`·`skills` | 유지 | 유지 | 대상 무관 |
| `prometheus/metrics` | 비활성(기본) | **비활성 유지 — (D-119) PromQL은 `mcp_server` 도구로 소비**(A안 복귀 시에만 활성) | §3 |
| `mcp_server`(폴스타+PromQL) | — | **`Config.mcp_servers` 등록** | §3·§4. toolsets dict가 아닌 Config 인자 — `DiagnosisAgent`에 `mcp_servers` 전달 확장 필요 |

시그니처(안): `remote_vm_profile() -> dict` + `DiagnosisAgent(toolsets=..., mcp_servers=...)` — **(D-119) Prometheus 접속 설정은 `mcp_server` 측으로 이동해 프로파일 인자에서 제외**. 세부는 D-020 등재 시 확정.

### 설정 확장 (`AgentSettings` — Plan 04 §8과 통합)

`polestar_mcp_url`, `polestar_mcp_token`(SecretStr). **(D-119)** `prometheus_url`·`prometheus_auth_header`는 `AgentSettings`에서 제외 — `mcp_server` 설정(config.toml·서버 `.env`)으로 이동한다(§3 서버측 일원화). pydantic 필드로만 판정, `.env` 인라인 주석 금지, list/dict는 JSON 형식 (Known Mistakes 원칙).

## 7. 대표 조사 흐름 (검증 시나리오 겸용)

"web-01 CPU 급증" PAGE 트리거(Plan 01 §8 페이로드의 `db_id`·`server_name`이 조사 컨텍스트로 주입 — Plan 02):

1. `polestar_alarm_history`·`polestar_condition_log` — 알람 맥락·재발 여부
2. `mcp_server` PromQL 고수준 도구(예: `prom_metric_range("web-01", "node_cpu_seconds_total", …)` — 서버가 nodename 필터 조립, D-119) — 급증 시각·형태 확정
3. `polestar_process_snapshot` — 현재 상위 프로세스(실시간 단면임을 명시)
4. `polestar_change_history`·`polestar_os_config` — 변경·구성 대조
5. 결정적 후처리(Plan 02): severity_judge·브리핑 조립

## 8. 구현 순서·검증·수용 기준

| Wave | 내용 | 선행 |
|---|---|---|
| **R-A** | `remote_vm_profile()` + `DiagnosisAgent.mcp_servers` 확장 + `AgentSettings` 확장 + 단위 테스트 | 없음 (즉시 가능) |
| **R-B** | **(D-119 갱신)** `mcp_server` PromQL 도구 구현(§3 — 고수준 hostname 앵커 + 원시 옵트인·서버측 nodename 조립·감사 파이프 합류) + 로컬 픽스처 검증 — **Docker 픽스처(§8.1)** 로 **MCP 경유** 도구 발견·쿼리 e2e(`PrerequisiteCacheMode.DISABLED`) + **품질 게이트**(수용 기준 7) | R-A |
| **R-C** | 폴스타 MCP 합류 — Plan 04 M-C와 동시(같은 등록 코드) | Plan 04 M-A/B |
| **R-D** | hostname 규약 실측·확정(§5, D-020) + 실 Prometheus 연동 검증 | 인프라 접근 |

### 8.1 Docker 테스트 픽스처 구성 (R-B 구체화 · 2026-07-27)

`testdata/prometheus/`(docker-compose + prometheus.yml)로 로컬 완결 픽스처를 구성한다 — 기존 PG 픽스처 전례(`testdata/pg/docker-compose.yml` — 실재 확인)를 따르며, PG 픽스처와 함께 기동해 §7 교차 검증 시나리오를 재현한다:

| 서비스 | 이미지(안) | 포트(안) | 역할 |
|---|---|---|---|
| prometheus | prom/prometheus | 9090 | 스크레이프·PromQL HTTP API — `mcp_server` PromQL 도구와 내장 toolset(A/B 품질 게이트) **양쪽의 공통 대상** |
| node_exporter | prom/node-exporter | **9101(재배치)** | 실 계측 파형 검증(카운터·게이지 실동작) — 기본 9100은 collectorinfra 알람 수신 포트(폴스타 push)와 충돌하므로 재배치 |
| mock_exporter | 정적 `/metrics` 서빙(경량 http) | 9102 | **결정적 단언용** 합성 메트릭 — 고정 값·"CPU 급증" 파형을 사전 정의해 §7 시나리오를 값 단언 가능하게 재현(실 계측은 비결정적이라 단언 부적합) |
| (병행) postgres | `testdata/pg` 기존 | 5432 | 폴스타 스키마 서브셋 — 교차 검증의 폴스타 축 |

- **스크레이프·라벨 규약(§5 검증 겸용)**: prometheus.yml에서 두 익스포터에 relabel로 `nodename` 라벨을 부여하되, **값은 PG 픽스처 `cmm_resource`의 server_name과 동일**(예: `web-01`)하게 맞춘다 — §5-0 서버측 조립과 §2 소스 교차 검증 규칙이 픽스처에서 실제로 동작·단언된다.
- **소비 지점**: ① `mcp_server` PromQL 도구 통합 테스트(nodename 조립 → 실 쿼리 → 값 단언[mock_exporter]·원시 옵트인·timeout 강제·감사 로그) ② RemoteMCPToolset 발견·호출 e2e(R-B) ③ D-119 품질 게이트 A/B(같은 픽스처를 내장 toolset과 `mcp_server` 도구가 공유 — 조건 동일성 보장) ④ Gemini e2e(D-120 — 픽스처 메트릭은 로컬 컨테이너 계측·합성 값이므로 "목업·픽스처만 외부 송신" 데이터 통제와 정합).
- **운용 관례**: Docker 미기동 시 해당 테스트 skip(기존 PG 픽스처 관례), 실 LLM e2e는 `RUN_E2E=1` 옵트인 유지. compose 세부(이미지 태그·mock 메트릭 목록·기동 스크립트)는 착수 시 확정.

수용 기준:
1. 원격 프로파일에서 로컬 VM 진단 명령(`ps` 등)이 bash allowlist에 **없음**을 테스트로 고정 (로컬/원격 프로파일 비대칭이 의도임을 명시 — 단일/멀티 경로 대칭 원칙의 의도적 예외)
2. (D-119) `mcp_server` PromQL 도구가 RemoteMCPToolset으로 자동 발견됨을 실 런타임 로그로 확인(내장 `prometheus/metrics`는 비활성 유지·캐시 비활성)
3. §2 소스 선택 지침이 실제 시스템 프롬프트·toolset 지침에 포함됨을 실측 (D-119: `llm_instructions` 단일 주입 지점)
4. hostname 정합 규약 문서화 + 서버측 조립 단위 테스트(§5-0) + 지침 반영 (§5)
5. SSH 부재 상태로 §7 시나리오 완주 (Plan 04 서버 + 로컬 픽스처 조합 가능)
6. `arch_check --ci` 통과
7. **(D-119 검증 게이트)** §7 시나리오를 내장 toolset(A) 대 `mcp_server` PromQL 도구(B)로 각 완주 비교 — B의 조사 품질 열화 없음 확인. 열화 확인 시 A안 복귀(§3 폴백 참고 절차, `docs/02_decision.md` D-119 상태 갱신)

## 9. 오픈 이슈

- Prometheus 서버의 실 위치·인증 방식·보존 기간 미확정 — R-B는 로컬 픽스처로 선행 가능, R-D에서 실측 (D-119: 접속 설정 보관 지점은 `mcp_server`)
- (D-119) 원시 PromQL 패스스루 옵트인의 기본값·탐색 도구(labels/metadata/series) 노출 범위 — R-B 착수 시 확정
- 스크레이프 라벨 표준화 가능 여부(기존 스크레이프 설정 소유권) — §5-3
- b0(DB2) 권역 VM이 Prometheus 스크레이프 밖일 가능성 — `polestar_metric_trend` 폴백 경로가 커버, 해상도 한계 명시
- 로그 소스 부재: 본 계획 2축에는 VM 로그(syslog/journal) 원격 수집이 없다 — 필요 시 Loki/VictoriaLogs 등 로그 스택 추가는 별도 결정(현 스코프 외)
- (통합 델타 2026-07-24) collectorinfra Plan 60 §18 E8(D-117)의 폴스타 에이전트 스냅샷 채널이 Plan 04 `polestar_host_snapshot` 후보 도구로 노출되면, 원격 L3(USE 명령·dmesg/journal 원문)가 2축의 폴스타 MCP 축 안에서 가용해진다 — E8 구현 진척에 연동해 결정(로그 스택 필요성도 그때 재평가)
