# 코드베이스 현황 — 파드/노드/호스트 식별과 APM↔SMS↔DPM 상관 (조사일 2026-11-03, 읽기 전용)

## 0. 요약
- 문제의 핵심(파드 IP/이름 ≠ 폴스타 서버)은 이미 한 번 다뤄졌다: **D-355 / plans/202(WIP)** — 제니퍼 웹 세션 내부 API `hostAddress`(노드 IP)를 받아 폴스타 서버와 정확 일치 1건이면 자동 연결. 플래그 `JENNIFER_NODE_ADDRESS_ENABLED` **기본 off**, 폐쇄망 검증 대기.
- 그러나 이 연결은 **조사(sre_agent) 단계의 `meta.hints.polestar_link`에만** 쓰인다. 노이즈 게이트 상관 키(`host_key`)·크로스소스 에피소드에는 반영되지 않는다(아래 §3).
- 범용 pod→node 매핑(K8s API / kube-state-metrics)은 코드에 **없다**. K8s API는 계정 협의 불가로 기각(plans/202 §0 #5).
- DPM은 **슬롯·스텁만** 있고 실연동 없음.

## 1. apm_gateway (제니퍼)
- 구조: `apm_gateway/apm_gateway/{adapters/jennifer, application, interface}`. 읽기 전용 MCP 서버(`apm_*`) + 이벤트 폴러(`alarm:raw`).
- Open API 허용 목록: `apm_gateway/apm_gateway/adapters/jennifer/allowlist.py:73-139` — `/api/domain`, `/api/instance?domain_id`, `/api/realtime/*`, `/api/activeService/list`, `/api/transaction/*`, `/api/dbsearch/*`, `/api/status/*`, `/api/business`, `/api/dbmetrics/*`, `/api-v2/environment-variable/{domainId}` 등.
- 인스턴스 필드 매핑: `adapters/jennifer/fields.py:250-255` — `instanceId`→`instance_id`, `hostName`→`host_name`, `ipAddress`→`ip_address`. 파드 인스턴스는 `hostName`=파드명, `ipAddress`=파드 IP(10.139.x)뿐(plans/202 §0 #1). 노드 주소 칸 없음.
- 식별: 인스턴스는 `(source_id, domain_id, instance_id)`; 도구 `apm_instance_map`(`interface/server.py:466`)이 이름 검색(D-290).
- **K8s 대응 코드(유일)**: 웹 세션 `GET /agent/upgrade/list?sid=<domain>` 파서 `adapters/jennifer/web_fields.py:3-35`(허용 칸 `instId, sid, hostName, ip, hostAddress, status, statusMsg, lastLiveTime`만 보존, JMX 자격증명 폐기; `hostAddress≠ip`이면 `pod`, 도메인 종류 `pod|vm|mixed` `KIND_POD`), 로그인 `adapters/jennifer/web_session.py`, 캐시·차단기 `application/node_address.py`(TTL 기본 900s, 마지막 도메인 종류 기억), 설정 `config.py:28,580-588`(`JENNIFER_NODE_ADDRESS_ENABLED/TTL_SECONDS/MIN_REFETCH_SECONDS`, 소스별 웹 URL/계정), 조립 `__main__.py:87-136`(폴러와 `apm_instance_map`의 `host_address` 칸에 공유).
- `alarm:raw`에는 `apm.host_address`, `apm.host_address_status`, `apm.pod` 가산(D-355 ①; 플래그 off면 키 없음).
- 실측(plans/202 §0 #12-13,16,19): VM 도메인은 전 인스턴스 `hostAddress=ip`, 파드 도메인은 전 인스턴스 `hostAddress≠ip`. 도메인당 에이전트 상한 100. 웹 세션 전용(토큰/무인증은 /login 리다이렉트), b0는 https:7901 사설 인증서·브라우저 방식 로그인 필요.
- 없음 확인: `grep -rniE "pod|k8s|kubernetes|container|hostAddress" apm_gateway --include='*.py'` → 위 node_address 관련 외 K8s API/컨테이너 필드 처리 없음. Open API 환경변수·알람 원문·뷰서버 extension `InstanceData` 모두 노드 주소 없음(plans/202 §0 #2,6).

## 2. mcp_server (폴스타 SMS · Prometheus)
- 폴스타 도구: `mcp_server/mcp_server/polestar_tools.py` — `polestar_alarm_history`(712), `polestar_incident_alarms`(765), `polestar_metric_trend`(811), `polestar_resource_status`(869), `polestar_topology`(899), `polestar_process_snapshot`(936), `polestar_os_config`(1010), `polestar_change_history`(1041), `polestar_condition_log`(1101). SQL 도구는 `tools.py`.
- 호스트 키: `config/db_profiles/polestar_cm_gp.yaml:28-62,214-222` — `cmm_resource.name`(등록명, 일부 NULL) / `cmm_resource.hostname`(OS 호스트명) / `cmm_resource.ipaddress`; EAV `core_config_prop`(Hostname, IPaddress). 서버 식별 출력은 `COALESCE(name, hostname)`.
- **K8s/컨테이너 리소스 타입: 없음 확인**: `grep -rniE "container|docker|kubernetes|k8s|pod" config/db_profiles config/knowledge testdata/pg` → docker-compose 인프라 언급뿐. 폴스타는 노드를 일반 서버(Server)로 등록(노드 IP 10.136 등록, `tunl0` 인터페이스가 파드 대역 10.139 — plans/202 §0 #3). `tunl0` 프리픽스 매칭은 클러스터 간 파드 대역 중복으로 기각(§0 #4).
- 노드 IP → 폴스타 서버 조회: `noise_gate/application/host_candidates.py:87-124` `node_address_link`(전 존 probe, IP 정확 일치), 판정 `noise_gate/domain/host_link.py:479` `node_address_match`.
- Prometheus: `mcp_server/mcp_server/promql_tools.py:876-1017` — `prom_metric_instant/range`, `prom_query(_range)`, `prom_labels`, `prom_metadata`, `prom_series`. 고수준 도구는 `{nodename="<hostname>"}`(기본) 또는 앵커 모드 `polestar_host`(plans/189·D-119 부기: 폴스타 hostname/IP → Prometheus `hostname`/`node_ip` 라벨로 결정적 조립; `config.py:114-129`). 운영 라벨(plans/189 §0): `hostname, node_ip, instance(=node_ip:port), resources, name, center, company, project, type="node"`; 비어있지 않은 `node_ip`는 유일(1204/1229); 공동존만 등록·은행존 미사용; 메트릭은 node_exporter `node_*`.
- **kube-state-metrics 계열 없음 확인**: `grep -rniE "kube_pod|kube_node|kube-state|cadvisor|container_cpu" . ` (코드/yaml) → 코드·설정에는 없음; 문서(plans/…)에서도 node_exporter `node_*` 외 가용 확인 없음(매칭 파일은 plans/* 의 일반 언급). 파드 라벨 가용성은 미검증 → 폐쇄망에서 `prom_labels`/`prom_series`로 `kube_pod_info{node=,host_ip=,pod_ip=}` 존재 실측 필요.

## 3. noise_gate (상관 키)
- 공통 호스트 키: `noise_gate/domain/alarm.py:35-47` `normalize_host_key`(소문자, FQDN 첫 라벨, IP는 유지), `:128` `host_key`(원천 = `hostname`; APM은 게이트웨이가 정합한 hostname), `:138` `host_key_strength` — APM은 `raw.apm.match_reason`이 `override|host_name`이면 strong, 그 외 weak, `ambiguous`면 none (`:23-25,153`).
- 크로스소스 에피소드: `noise_gate/domain/cross_source.py:190` `episode_key(zone, host_key)`, `:233` 양쪽 strong일 때만 결합; 규칙 `noise_gate/infrastructure/cross_source_rules.py:50,120,217`(equal 키 = zone, host_key).
- **갭**: 파드 알람의 `hostname`=파드명 → polestar 서버명과 불일치 → `host_key` 정합 실패(none/weak) → 폴스타×제니퍼 에피소드 결합 불가. `grep -rnE "host_address|apm\.pod" noise_gate` (investigation_trigger/host_link/host_candidates 제외) → **0건**: 노드 주소가 게이트 `host_key`로 승격되지 않는다.
- 노드 연결 지점: `noise_gate/application/nodes/investigation_trigger.py:683-736` `_attach_polestar_link`, `:769-817` `_node_address_hint`(`apm.pod is True`, `host_address_status=="ok"`, IPv4 검증 → `node_address_link`) → `meta.hints.polestar_link {basis:node_address}`. 호스트 연결 학습 D-348: `noise_gate/domain/host_link.py`, `infrastructure/host_link_store.py`(Redis `hostlink:{source_id}:{hostname}`), 정규화 `src/domain/host_names.py`; 플래그 `NOISE_HOST_LINK_ENABLED` 기본 off.
- APM 상관 스코프: D-349 — 같은 도메인끼리만(`db_id|domain`) 묶고 대표 외는 DASHBOARD 상한; `signal_detection.py:382` `detect_correlated_storm`; 플래그 `NOISE_APM_CORRELATION_DOMAIN_SCOPE_ENABLED` 기본 off. D-317: 폴스타×제니퍼 복합 캔슬링(공통 호스트 키).
- **매핑 표를 조회할 후보 지점**: (a) `alarm.py` `host_key` 계산(파드면 노드 호스트명/서버명으로 대체, strength 부여) (b) `investigation_trigger._node_address_hint` 결과 재사용 (c) `host_link_store` 확정 연결을 host_key에 투영.

## 4. sre_agent (조사)
- 소스 선택 순서(D-348 ④/D-355): `sre_agent/sre_agent/application/playbook_runner.py:458-477` — `meta.episode.infra_db_ids[0]` → `polestar_link` linked → (파드 unresolved면 설정 매핑 대체 **금지**, 사유 반환) → 설정 매핑(`SRE_PLAYBOOK_POLESTAR_SOURCE_MAP`) → 결손. 힌트 파싱 `:365-400`, 파드 미확정 `:423-448`, 근거 문장 `:326`("제니퍼 노드 주소 일치 — {host_address} (알람 시점 · 자동)"), 실패 사유 `:342`.
- 증거 선인출: `sre_agent/sre_agent/application/evidence_prefetch.py:67,108-112,169-190` — node_address linked면 그 `db_id/server_name`으로 폴스타 축 개방, 파드 unresolved면 `skip_infra`. 토폴로지 이웃: `domain/topology_neighbors.py:73`.
- 즉 APM 인스턴스→호스트 해소는 **polestar_link 힌트 의존**; sre_agent 자체는 후보를 찾지 않는다(import 0, D-139).
- DPM: `sre_agent/sre_agent/domain/source_adapter.py:1-28`, `playbook_steps.py:49,149` — `dpm.db_health` 스텝은 **스텁(미연동)**(D-341 ⑤, plans/187 §6). DPM↔호스트 키 설계는 없음. 확인: `grep -rniE dpm mcp_server apm_gateway` → 0건.

## 5. 관련 결정/계획 (색인 grep)
- D-195(제니퍼 APM 연동) · D-287/D-322(다중 소스) · D-290(조회 대상 해석: 인스턴스 이름 우선) · D-296/D-299(읽기 전 경로 허용) · D-274(게이트웨이 자격증명 경계).
- D-188(알람 서버 식별 역조회 hostname→등록명·IP·존; APM 연결은 D-348) · D-046/D-061(호스트명 해소, 은행존 서버명·호스트명·IP 구분) · D-036(알람 영향 프로세스) · D-175(호스트 가용성 사전판정).
- D-109/D-112/D-317/D-349(크로스호스트·크로스소스 상관, 공통 호스트 키, 도메인 스코프) · D-197(사건 좌표계·결정적 상관) · D-117/D-189(L3 실호스트 조사).
- **D-348**(APM 알람→폴스타 서버 연결 학습, 후보+사용자 선택, K8s 워크로드 이름 토큰 LIKE; `plans/196`) · **D-355**(파드 노드 주소 자동 연결, `plans/202`; 잔여: 수동 재분석 경로 `src/api/routes/alarm.py _manual_event`가 `apm` 블록을 잃음, 로그인 차단기 메모리, 폐쇄망 검증).
- D-119 부기(Prometheus 앵커, `plans/189`) · D-341(조사 플레이북·DPM 슬롯) · `plans/55`(멀티소스 로드맵: APM·DPM), `plans/101`(ML RCA: 폴스타·제니퍼·DPM 연계, TODO), `plans/87`(제니퍼 연동), `plans/144`(크로스소스 사건).

## 6. 플러그인 지점 요약 / 갭
| 지점 | 현황 | pod→node→host 매핑 연결 위치 |
|---|---|---|
| 노드 IP 출처 | 웹 세션 `hostAddress`(D-355, 플래그 off) | 대안 출처(kube-state-metrics `kube_pod_info`, 에이전트 주입)는 코드에 없음 → `apm_gateway/application/node_address.py` 옆 신규 provider |
| 노드→폴스타 서버 | `host_candidates.node_address_link` (IP 정확 일치) | 재사용 |
| 게이트 상관 키 | `Alarm.host_key`는 hostname만 | **미반영(핵심 갭)** — `alarm.py:128-160` |
| 조사 소스 선택 | `polestar_link` 힌트 반영 완료 | 수동 재분석 경로 갭(`alarm.py _manual_event`) |
| Prometheus | node_exporter `node_ip`/`hostname` 앵커(plans/189) | 노드 IP로 직접 앵커 가능; 파드 단위 지표(kube-state/cAdvisor)는 미확인 |
| DPM | 스텁 | 키 규약 미정 |
