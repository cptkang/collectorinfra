# 214. 서버 정보 수집 — 에이전트리스(SSH) 읽기 전용 수집 게이트웨이 · 존별 인스턴스 · 정기 구성 스냅샷 + 장애 시점 진단 수집

> **작성일**: 2026-11-05 · **v1.1**(2026-11-05 · 사용자 확정 4건 반영 — G-1 SSH 채택 · Ansible 미사용 · 대상 Python 미설치 · 존 망 분리 → 실행 엔진을 Ansible에서 **OpenSSH 직접 실행**으로 바꾸고 **존별 인스턴스**로 배치) · **v1.2**(2026-11-05 · 사용자 지시 「본체에 전달하는게 필요한지 검토하여 적용하라」 → §8.1 검토 · **본체 연결 편입 — 스냅샷 조회만 · 실시간 수집은 `fault_diagnosis`→`sre_agent` 경유** · 주소는 `MCP_SOURCE_ENDPOINTS` 하나로 · Wave 재배열) · **v1.3**(2026-11-05 · 사용자 지시 「대부분의 OS가 레드햇리눅스이다. 쉘스크립트를 이용하는 방안도 같이 검토하여 반영하라」 → §4.2 셸스크립트 3형태 검토 · **배포형 bash 수집 스크립트 + 강제 명령 채택** · cron 주기 실행은 1차 미채택(G-13) · RHEL 기본 도구(sysstat `sa` 과거 지표 · `rpm` 설치 시각) 편입 · 카탈로그·픽스처 RHEL 기준) · **v1.4**(2026-11-05 · 사용자 질의 「수집 명령어 생성에 LLM이 필요해 보이는데 어디서 하면 적절하냐」 → 권고대로 정리: **LLM은 실행 시점에 명령을 만들지 않고 ① 조사 중 프로파일 선택(`sre_agent`) ② 오프라인 카탈로그 초안 작성(§7.4)에만 쓴다** · 공백 기록 → 초안 → 결정적 검사 → 사람 승인 → 재배포 경로 신설 · 매개변수형 항목 `diag.log_tail`·`diag.mw.<kind>` 추가(§7.3) · W8 신설)
> **상태**: **TODO** — 코드 0 · **D-367 확정**(계획 · 코드 0 · 부기 1 본체 연결 · 부기 2 셸스크립트·RHEL · 부기 3 LLM 역할 경계) · 남은 게이트 G-5·G-7~G-14(§12) · W0 폐쇄망 실측 대기
> **성격**: 기능 추가 · 신규 독립 패키지 · 소비처 배선은 **기본 off = 현행 비트 동일**
> **관련 결정**: **D-367**(이 계획의 정본) · D-003(읽기 전용) · D-035(결정적=판단 · LLM=서술) · **D-117·D-189**(L3 실호스트 조사 — 경로 B 허용목록 명령 실행) · D-118·D-139(패키지 경계 · import 0) · D-119(관측 읽기 경계) · **D-233**(원격 조사 프로파일 bash 제거 — 유지) · **D-274**(별도 자격증명을 가진 관측 소스 = 독립 게이트웨이 패키지) · D-161(승격–폐기 동반) · SREAgent D-004·D-019(`plans/sre-agent/06` §1 「SSH 미채택」 — **D-367로 개정**) · D-364(관측 게이트웨이 주소 = `MCP_SOURCE_ENDPOINTS` 하나) · D-255(매뉴얼 동반) · `plans/125`(관측 소스 MCP 엔드포인트 계약) · Plan 78(본체 `host_inspect` · `HOST_AUTHZ_MODE` 조사 인가)
> **패키지**: 신규 `host_gateway/`(자체 pyproject · 별도 프로세스 · 루트 venv 공유 — `apm_gateway/` 전례) · 대상 서버 배포물(bash 수집 스크립트 1개 — 강제 명령으로만 실행 · `authorized_keys` 1줄) · 소비처 `sre_agent/`·`noise_gate/`·본체 `src/`(`host_inspect` 프로파일 2개)는 배선만

---

## 0. 요약

지금 장애 진단이 볼 수 있는 서버 정보는 **간접 관측뿐**이다 — 폴스타 DB(구성 EAV·지표 롤업·알람·프로세스 단면), Prometheus(공동존 중 관제 요청 서버만 · 은행존 없음 — D-119 부기), 제니퍼 APM(앱), ITAM(자산). **대상 서버의 OS를 직접 들여다보는 장치는 운영에 없다.**

이 계획은 **SSH로 고정 명령만 실행하는 읽기 전용 "서버 정보 수집 게이트웨이"(`host_gateway/`)** 를 존마다 하나씩 둔다.

1. **LLM은 명령을 고르지 않는다** — 게이트웨이는 `profile_id`(고정 카탈로그)와 형식이 검증된 인자만 받는다. 이것이 `plans/sre-agent/06`이 SSH를 기각한 근거(「LLM이 `ssh host <임의 명령>`을 만든다」)를 없앤다(D-367 ①).
2. **대상 서버 쪽에서도 막는다** — 전용 계정의 `authorized_keys`에 **강제 명령(forced command)** 으로 디스패처 스크립트를 걸어, 키가 유출돼도 카탈로그에 있는 항목 말고는 실행되지 않게 한다(§6 ②). 수집 로직 자체를 이 **bash 수집 스크립트**(RHEL 기본 셸 · Python 불필요)에 두어, 프로파일 하나를 SSH 세션 하나로 끝낸다(§4.2).
3. **두 가지 수집 모드** — ① **정기 구성 스냅샷**(일 1회 + 수동) → 저장 → **이전 스냅샷과 비교 = 변경 이력**. ② **장애 시점 진단 수집**(장애 종류별 USE 묶음).
4. **존별 인스턴스** — 은행존·공동존 망이 분리돼 있으므로 같은 코드를 존마다 배치하고 설정(존·인벤토리·키)만 다르게 둔다. SSH 키는 각 존 인스턴스만 갖는다(D-274).
5. **경로 단일화** — 소비처(`sre_agent` 조사 · `noise_gate` L3 보강 · 본체 채팅)는 MCP 도구로만 부른다. `noise_gate`의 자체 ssh 실행기는 게이트웨이 경로가 검증되면 폐기한다(D-161 · D-189 근거 4).
6. **본체(채팅)는 저장된 스냅샷만 읽는다**(v1.2 · §8.1) — "서버 X의 sysctl·패키지·서비스", "어제 이후 바뀐 것" 같은 질의는 본체 `host_inspect`의 새 프로파일 2개가 `host_facts`·`host_snapshot_diff`로 답한다(SSH 유발 0). 장애 시점 실시간 수집은 본체가 직접 부르지 않고 기존 `fault_diagnosis` → `sre_agent` 위임 경로가 맡는다.
7. **새 수집 명령이 필요할 때 LLM은 「초안 작성자」다**(v1.4 · §7.4) — 조사 중 카탈로그에 없는 정보가 필요하면 `sre_agent`는 명령을 만들어 실행하지 않고 **수집 공백**을 구조화해 남긴다. 공백이 쌓이면 오프라인 CLI에서 LLM이 카탈로그 YAML 초안을 쓰고, 결정적 검사와 사람 승인을 거쳐 수집 스크립트를 다시 만들어 배포한다. **승인·재배포 전에는 그 항목이 대상 서버에 존재하지 않으므로 실행될 수 없다.**

---

## 1. 요구와 확정 사항

사용자 지시(2026-11-05): *"서버의 정보를 수집하여 정확한 장애 진단을 해야 한다. 지금은 서버의 정보를 수집하기 위한 장치가 없다. 따라서 ansible와 같은 IaC 도구를 이용하여 OS 명령어를 통해 정보를 수집하는 등의 작업을 할 수 있다. 더 좋은 방안이 있으면 제안하고 서버의 정보를 수집하는 방안에 대해 계획 파일을 작성하라."*

사용자 확정(2026-11-05): *"SSH 미채택 결정을 바꾸자. ansible을 미사용중이다. python은 미설치되어 있다. 은행존, 공동존 망은 분리되어 있다."*

| 게이트 | 확정 | 이 계획에 미친 영향 |
|---|---|---|
| G-1 | **SSH 미채택 결정 개정** | 원격 진단 2축(Prometheus · 폴스타) → **3축**(+ 서버 직접 수집) · D-367 등재 |
| G-2 | **운영팀 Ansible 미사용** | 기존 인벤토리·계정·AWX 재사용 이점 없음 |
| G-3 | **대상 서버 Python 미설치** | Ansible 모듈(`command`·`setup`·`package_facts` 등)이 돌지 않는다 → **Ansible 제외**(§4) · 대상 쪽 수집은 bash 스크립트(§4.2) |
| G-6 | **은행존·공동존 망 분리** | 게이트웨이 **존별 인스턴스 2개**(§5.2) |
| G-4 | (G-3에서 따라 나옴) | 실행 엔진 = **OpenSSH 클라이언트 서브프로세스**(§4.1) |
| — | **대상 OS 대부분 RHEL**(사용자 2026-11-05) | 카탈로그·권한·픽스처를 RHEL 기준으로(§7·§9) · RHEL 기본 도구 편입(§4.2) |
| — | **셸스크립트 방안 검토 지시**(사용자 2026-11-05) | §4.2 — 배포형 bash 수집 스크립트 + 강제 명령 채택 · cron 주기 실행은 G-13 |
| — | **수집 명령 생성에 LLM 활용 — 권고대로 정리**(사용자 2026-11-05) | 실행 시점 생성 금지 · LLM은 프로파일 선택(실행 시점)·카탈로그 초안(오프라인)만 · §7.3·§7.4 |

**범위 밖**: 서버 **변경**(설정 배포·재기동·패키지 설치)은 하지 않는다. D-003 읽기 전용을 이 게이트웨이에도 그대로 적용한다.

---

## 2. 현황 실측 (2026-11-05 · 브랜치 `apm_noise`)

### 2.1 지금 진단이 쓰는 서버 정보 소스

| 소스 | 경로 | 얻는 것 | 한계 |
|---|---|---|---|
| 폴스타 DB | `mcp_server` `polestar_os_config`·`polestar_process_snapshot`·`polestar_metric_trend`·`polestar_alarm_history` 등 | 구성 EAV · 지표 롤업 · 프로세스 단면 · 알람 | 폴스타 에이전트가 모으는 항목만 · 로그 원문은 관제 규칙 매칭분만 · 패키지·sysctl·서비스 목록 없음 |
| Prometheus | `mcp_server` `prom_*`(D-119) | node_exporter USE 분해·고해상도 시계열 | **공동존 중 관제 요청 서버만 · 은행존 미사용**(D-119 부기) · 프로세스·로그 없음 |
| 제니퍼 APM | `apm_gateway` | 앱 트랜잭션·인스턴스 | OS 아님 |
| ITAM | DB(MariaDB) | 자산 대장 | 정적 |

### 2.2 이미 있는 OS 직접 수집 코드 (운영 미배치)

| 자산 | 위치 | 상태 |
|---|---|---|
| L3 post-gate 보강 수집기 | `noise_gate/infrastructure/host_diagnostic_collector.py` — `L3_PROFILES`(cpu·memory·disk·network·process·log) · `timeout 20 nice -n 10` 접두 · deny 마커 · `mask_args` · `build_ssh_runner`(ssh BatchMode 서브프로세스) | 플래그 `l3_enrichment_enabled` 기본 off(만료 2027-02-20) · `l3_host_access_mode="allowlist_exec"`만 구현 · **`StrictHostKeyChecking=accept-new`**(§6 ⑦에서 교정) |
| 로컬 VM 조사 프로파일 | `sre_agent/sre_agent/toolset_profiles.py` `vm_profile()` — `VM_DIAG_ALLOW`(light 20 · heavy 가드형 12) | 원격 조사는 `remote_vm_profile()`이 **bash를 끔**(D-233) → 원격 OS 신호 0 |
| MCP 등록 | `sre_agent/sre_agent/interface/mcp_service.py` `_build_mcp_servers()` — `polestar`·`apm` 등록 · `DiagnosisAgent`는 지연 생성 1개 공유 | 조사마다 MCP 목록을 바꾸지 않는다 → 존별 인스턴스는 **둘 다 상시 등록**(§8) |
| 본체 서버 점검 경로 | `src/orchestration/host_inspect.py`(Plan 78 W3 · 2단 subagent) — 프로파일 `os_config`·`resource_status`·`metric_trend`·`metrics_live`를 키워드로 결정적 판정 · `DBHubClient.inspect_host`로 `mcp_server` 호출 · `authorize_targets`(allowed_db_ids) · 게이트 `composite.investigation_enabled` | 데이터는 폴스타·Prometheus뿐 · 프로파일 판정은 「의도적으로 좁다」(측정 없이 넓히지 않음) |
| 본체 장애 진단 위임 | `src/nodes/fault_diagnosis.py` — "○○ 서버 원인 분석해줘"류를 `sre_agent`에 submit/poll 위임 · `src/domain/host_authz.py` 조사 인가(`HOST_AUTHZ_MODE` 기본 `admin_only` · fail-closed · 「조회 권한 ≠ 조사 권한」) | `sre_agent`가 host 도구를 쓰면(W5) **본체 진단 질의는 코드 변경 없이** 서버 직접 수집 근거를 받는다 |
| 본체 관측 소스 주소 | `src/config/data_access.py` `MCP_SOURCE_ENDPOINTS`(시스템 코드 → URL · `plans/125` A-2 · 코드가 없으면 그 시스템 비활성) · `MCP_SOURCE_TOKENS` · `src/clients/source_mcp_client.py` | `noise_gate`도 같은 키를 쓴다(D-364 — `apm` 선례) |
| holmes 도구 이름 충돌 처리 | holmes 0.36.0 `tool_executor.resolve_tool_name_collisions` — 여러 MCP 툴셋에 같은 도구 이름이 있으면 `<toolset>__<tool>`로 이름을 나눈다(2026-11-05 설치본 실측) | 존별 인스턴스가 같은 도구 이름을 써도 충돌 없이 둘 다 노출된다 |
| 설계 근거 | `docs/aiops_benchmark/l3_host_collection_mechanism.md` §5(채널 B: `command_id` → 사전등록 명령) · `docs/25_host_investigation_load_guard.md` | 설계만 있음 |

### 2.3 진단 공백

1. **은행존 전체**와 **공동존 Prometheus 미등록 서버**는 OS 실시간 신호(CPU 분해·swap si/so·iowait·D-state)가 없다.
2. **로그 원문**(`journalctl -p err`·`dmesg` OOM/call trace/FS RO 리마운트)이 없다.
3. **최근 변경**(패키지·커널·sysctl·서비스 enable·cron·마운트)을 알 길이 없다.
4. **프로세스 상세**(FD 수 vs limit · thread 수)가 없다.

---

## 3. 기존 결정과의 관계

| 결정 | 관계 |
|---|---|
| **SREAgent D-004·D-019 · `plans/sre-agent/06` §1** 「SSH 미채택」 | **D-367로 개정**(사용자 확정 2026-11-05). 기각 근거였던 「LLM이 원격 명령 문자열을 만든다」는 게이트웨이(명령 고정)와 대상 쪽 강제 명령(§6 ②)으로 없앤다. 06 §1에 개정 부기를 단다. |
| D-117 · **D-189** 경로 B | **정합 · 확장**. 실행 채널을 `noise_gate` 내부 ssh에서 게이트웨이로 옮기고 용도를 조사 일반 + 구성 스냅샷으로 넓힌다. 불변식(읽기 전용·변경 명령 물리 제외·권고만·escalate-only·자격증명 분리·감사·마스킹·nice/timeout·Linux 1차) 전부 계승. W7에서 D-189 부기. |
| D-233 · D-235 | **유지** — LLM에 셸을 돌려주지 않는다. |
| D-274 | **근거** — SSH 키는 `host_gateway` 존 인스턴스만 보유. |
| D-003 | **확장 적용** — ① 카탈로그에 변경 명령 부재 ② 대상 쪽 디스패처가 카탈로그 밖 거부 ③ 대상 계정 최소권한. |
| 2026-07-21 「신규 에이전트 설치 곤란」 | **정합** — 데몬·패키지 설치 없음. 대상 배포물은 계정·`authorized_keys` 1줄·bash 수집 스크립트 1개(G-10). 상주 작업(cron)은 두지 않는다(G-13). |
| D-161 | W7에서 `noise_gate` `build_ssh_runner` 폐기 판정. |

---

## 4. 방안 비교 (v1.1 — G-2·G-3 확정 반영)

| 안 | 방식 | 대상 신규 설치 | 읽기 전용 강제 | 장애 시점 지연 | 판정 |
|---|---|---|---|---|---|
| A. Ansible | 중앙에서 SSH로 모듈 실행 | 없음 — **단 대상 Python 필요** | 플레이북 고정 | 호출당 수 초 | **기각** — 대상에 Python이 없어 쓸 수 있는 것은 `raw` 모듈(원격 셸에 문자열 전달)뿐이다. 그러면 직접 SSH와 하는 일이 같고 무게(`ansible-core` 반입·GPLv3 검토)만 늘어난다. 운영팀도 쓰지 않아 재사용 이점이 없다. |
| **B. 직접 SSH** | OpenSSH 클라이언트로 고정 명령 실행 (현 `noise_gate` 방식) | 계정 · `authorized_keys` · 디스패처(bash 수집 스크립트) 1개 | 게이트웨이 카탈로그 + **대상 쪽 강제 명령** | 1초 안팎 · 프로파일 1개 = SSH 세션 1개(§6 ②) | **채택** |
| C. 에이전트 설치형 | osquery · Salt · Telegraf · process-exporter | **필요** | — | 짧음 | 기각 — 설치 제약 |
| D. 기존 에이전트 확장 | node_exporter `--collector.systemd`·textfile collector · 폴스타 LogMonitor 규칙 | 설정 | — | — | 보완(§14) — 은행존 공백은 못 메움 |
| E. 로그 중앙 전송 | rsyslog/journald → 중앙 수집 서버 | 없음(OS 기본) | — | 짧음 | 후속 후보(§14) |

> 참고: RHEL은 `python3` 명령이 없어도 패키지 관리자가 쓰는 인터프리터가 깔려 있다 — RHEL 7은 `/usr/bin/python`(2.7 · yum 의존), RHEL 8/9는 `/usr/libexec/platform-python`(dnf 의존). 이 사실은 A안 판단을 바꾸지 않는다(운영팀 미사용 · 셸스크립트로 필요한 신호를 모두 얻음 · 시스템 인터프리터를 수집에 쓰면 OS 업데이트에 묶인다). W0에서 OS 분포를 볼 때 함께 기록만 한다.

### 4.1 실행 엔진 — OpenSSH 클라이언트 서브프로세스

- **채택**: 게이트웨이 호스트의 `ssh` 바이너리를 `asyncio.create_subprocess_exec`(argv · 로컬 셸 없음)로 부른다. 현 `noise_gate` `build_ssh_runner`와 같은 방식이고 새 Python 의존이 없다.
- 고정 옵션: `BatchMode=yes` · `StrictHostKeyChecking=yes` · `UserKnownHostsFile=<존 인스턴스 전용>` · `IdentitiesOnly=yes` · `IdentityFile=<존 키>` · `ConnectTimeout=5` · `ServerAliveInterval`·`ServerAliveCountMax` · `-T`(pty 없음) · `LogLevel=ERROR`.
- 대안(기각): `asyncssh`(순수 Python) — 폐쇄망 반입 1건이 늘고, known_hosts·키 형식 처리를 다시 검증해야 한다. 이점(연결 재사용)은 프로파일당 세션 1개 설계로 이미 얻는다.

---

### 4.2 셸스크립트 방안 검토 (v1.3 · 대상 대부분 RHEL)

「셸스크립트로 수집한다」에는 세 형태가 있고, 보안·부하 성질이 서로 다르다.

| 형태 | 방식 | 장점 | 단점 | 판정 |
|---|---|---|---|---|
| **S1. 배포형 수집 스크립트 + 강제 명령** | 수집 로직을 bash 스크립트 1개(항목 = 함수)로 대상에 두고, SSH 접속 때 `authorized_keys` 강제 명령으로만 실행한다. 게이트웨이는 `<profile_id> [인자]`만 보낸다 | 프로파일 1개 = SSH 세션 1개(명령마다 접속하지 않음) · Python 불필요 · **대상 쪽에서 허용목록을 강제**(키 유출에도 안전) · 상주 프로세스 없음 · 부하 가드·출력 절단을 대상에서 바로 건다 | 서버마다 스크립트 배포·버전 관리 | **채택(기본)** — v1.1의 「디스패처」를 수집 스크립트로 구체화한 것 |
| S2. cron 주기 실행 + 결과 파일 | 같은 스크립트를 cron으로 N분마다 돌려 대상 로컬에 스냅샷 링 버퍼를 남기고 게이트웨이가 가져간다 | 장애 **이전** 시점의 프로세스 상태 확보 · 게이트웨이가 멈춰도 수집 계속 | 상주 작업 = 사실상 경량 에이전트(2026-07-21 설치 제약 경계) · 디스크·로테이션 관리 · 포화 서버에서도 멈추지 않고 돈다 · 서버 수만큼 운영 대상이 늘어난다 | **1차 미채택** — 과거 시스템 지표는 RHEL `sysstat`의 `sa` 파일이 이미 남긴다(아래). 프로세스 이력 공백이 파일럿에서 실측되면 G-13으로 다시 판단 |
| S3. 접속 때 스크립트 전송 실행 | `ssh host bash -s < collect.sh` | 배포 없음 | 대상이 임의 스크립트를 받는다 = 임의 명령 실행과 같다 · 강제 명령과 양립 불가 | **기각** |
| (참고) 대상 → 중앙 전송(push) | 스크립트가 결과를 중앙으로 보낸다 | — | 대상에서 바깥 연결과 자격증명이 필요하다 | **기각** |

**S1 스크립트 규약**:
- 언어는 **bash**(RHEL 기본 `/bin/bash`). 소수 비-RHEL Linux도 bash가 있다. 생성기가 카탈로그 YAML에서 만들고 `shellcheck`·`bash -n`으로 정적 검사한다(손으로 고치지 않음 · §6 ③).
- 각 항목은 함수 하나다. 함수 안에서만 가드 접두(`timeout 20 nice -n 10 ionice -c3`)·출력 상한(`head -c`)·구분자(`=====AIOPS:<항목>:rc=<n>=====`)를 붙인다. 게이트웨이는 구분자로 잘라 명령별 rc와 출력을 얻는다.
- 함수가 쓰는 명령은 **RHEL 기본 설치 패키지**(coreutils·procps-ng·util-linux·iproute·systemd·rpm·chrony) 우선이다. `sysstat`(`mpstat`·`iostat`·`sar`)은 설치 여부를 W0에서 실측하고, 없으면 `rc=127` 사유로 남긴다.
- 대상에 남기는 것은 스크립트 1개뿐이다. 임시 파일·로그 파일을 만들지 않는다(대상 쪽 기록은 `logger -t aiops-ro` syslog 1줄).

**RHEL 기본 도구 활용(신규 설치 0)**:

| 도구 | 얻는 것 | 편입 |
|---|---|---|
| `sysstat` `sa` 파일(`/var/log/sa/saDD`) | **장애 시각 전후의 과거** CPU 분해·메모리·페이징·IO·네트워크·run-queue — `sar -f … -s <시각> -e <시각>`. 알람이 늦게 와도 그 시점 값을 본다(Prometheus 미등록 서버·은행존의 과거 공백을 메움) | **편입** — `diag.history`(§7.2). 수집 주기(RHEL 기본 10분)·보존(RHEL 8 기본 28일)·sysstat 활성 여부는 W0 실측 |
| `rpm -qa --qf … %{INSTALLTIME}` | 패키지별 설치·갱신 시각(비-root로 읽힘) | **편입** — 스냅샷 패키지 항목 |
| `yum history` / `dnf history` | 트랜잭션 단위 변경 이력(언제·무엇) | **조건부** — RHEL 7 `yum history`는 root 필요. 권한을 W0 실측하고 필요하면 sudoers 정확 일치 1줄(G-5) |
| `needs-restarting -r`(yum-utils/dnf-utils) | 커널·핵심 라이브러리 갱신 뒤 재부팅 필요 여부 | **조건부** — 설치돼 있을 때만 |
| `getenforce` · `tuned-adm active` · `/var/crash` 목록 | SELinux 모드 · 튜닝 프로필 · kdump 크래시 덤프 흔적 | **편입** — 스냅샷 |
| `sos report` | RHEL 표준 종합 진단 묶음 | **범위 밖** — root·수 분·수백 MB라 조사 중 실행에 맞지 않는다(벤더 지원 건 수동 실행용) |
| PCP(`pmlogger`) | 프로세스 이력까지 기록 | **제외** — 패키지·데몬 설치 필요 |
| Red Hat Insights | 구성 분석 | **제외** — 외부 SaaS(폐쇄망) |

---

## 5. 아키텍처

### 5.1 구성

```
        중앙(collectorinfra 본체 · sre_agent · noise_gate)
          │ MCP(SSE · Bearer)                    │ MCP(SSE · Bearer)
          ▼                                      ▼
 ┌─ host_gateway [zone=bankjon] ─┐      ┌─ host_gateway [zone=gongjon] ─┐
 │ MCP 도구 4종                    │      │ (같은 코드 · 설정만 다름)          │
 │ 입력 검증 · 카탈로그 · 가드      │      │                                 │
 │ 마스킹 · 감사 · 스냅샷 SQLite   │      │                                 │
 │ SSH 키 · known_hosts(존 전용)  │      │                                 │
 └──────────────┬────────────────┘      └──────────────┬──────────────────┘
                │ SSH 22 (존 망 안)                      │ SSH 22
                ▼                                        ▼
   [은행존 서버들]                              [공동존 서버들 — 김포·여의도]
   aiops-ro 계정 · authorized_keys:
   command="/usr/local/sbin/aiops-ro-dispatch",from="<게이트웨이 IP>",restrict
```

- **MCP 도구 4종**: `host_profiles()`(카탈로그 목록) · `host_collect(host, profile_id, args?)`(장애 시점 수집 · 동기 · 전체 상한 30초) · `host_facts(host)`(최신 스냅샷) · `host_snapshot_diff(host, since)`(변경 이력).
- **반환 계약**: 기존 MCP 도구와 같은 형태 — `{data, queried_at, source_kind: "host", zone}` · 오류 `{error, reason}`. 항목별 부분 성공을 그대로 싣는다(침묵 폴백 금지). 사유: `host_not_in_inventory`(이 존 인벤토리에 없음 — 다른 존일 수 있음을 `hint`로 안내) · `unreachable` · `auth_failed` · `host_key_mismatch` · `dispatcher_version_mismatch` · `busy`(같은 호스트 수집 진행 중) · `profile_unknown` · `arg_invalid`. LLM이 수집 실패를 「정상」으로 서술하지 못하게 한다.
- **호스트 식별**: 도구 인자 `host`는 인벤토리 키(OS hostname)다. 폴스타 서버명(VM name) ↔ hostname·IP 해석은 이미 `mcp_server`(D-360 · D-363)가 하므로 게이트웨이는 다시 구현하지 않는다. 조사 지침에 「폴스타로 hostname을 먼저 확인한 뒤 host 도구를 부른다」를 넣는다.

### 5.2 존별 인스턴스

| 항목 | 은행존 인스턴스 | 공동존 인스턴스 |
|---|---|---|
| 설정 `HOST_GATEWAY_ZONE` | `bankjon` | `gongjon` |
| 인벤토리 원천 | 폴스타 `polestar_b0` 등록 서버 | 폴스타 `polestar_cm_gp`·`polestar_cm_yd` 등록 서버 |
| SSH 키·known_hosts | 은행존 전용 | 공동존 전용(키를 존끼리 공유하지 않는다) |
| 배치 위치 | 은행존 망 안 호스트(G-11) | 공동존 망 안 호스트(G-11) |

- **인벤토리**: `host_gateway/config/inventory.<zone>.yaml`(hostname · IP · 포트 · OS 계열 · 사용 여부). 생성 스크립트가 폴스타 등록 목록(hostname·IP)에서 초안을 만들고 운영자가 확인해 커밋한다(인벤토리 밖 호스트는 수집 불가 — 결정적 경계). 파일럿은 수기 YAML로 시작한다.
- 존 판정은 db_registry의 db_id → zone 매핑을 쓴다. 게이트웨이는 존 이름만 알고 db_registry를 import하지 않는다.
- 공동존 안에서 김포·여의도 망이 또 나뉘어 있으면 인스턴스를 더 쪼갠다 — 코드는 같고 설정만 늘어난다(W0 확인).

---

## 6. 보안 통제 (보안팀 제출 목록의 원본)

| # | 통제 | 내용 | 검증 |
|---|---|---|---|
| ① | 명령 고정(게이트웨이) | 카탈로그는 저장소에만 있다. 런타임 입력은 `host`(인벤토리 키)·`profile_id`(이넘)·인자(항목별 정규식: unit `^[A-Za-z0-9@._-]{1,128}$` · pid 정수 · since는 허용 값 목록)뿐. 자유 문자열 경로 없음 | 단위: 카탈로그 밖 입력 거부 |
| ② | **강제 명령(대상 서버)** | `authorized_keys`: `command="/usr/local/sbin/aiops-ro-dispatch",from="<존 게이트웨이 IP>",restrict`(`restrict` = pty·포워딩·에이전트·X11 전부 금지). 수집 스크립트(bash · root 소유 0755 · RHEL SELinux에서는 배포 뒤 `restorecon`으로 레이블 `bin_t` 확인)는 `SSH_ORIGINAL_COMMAND`를 `<profile_id> [인자…]`로만 받아 `case`로 카탈로그 항목에 대응시키고, 인자를 자체 정규식으로 다시 검사한다. 모르는 항목·인자는 rc 2로 거부한다. 셸 메타문자(`;`·`|`·`&`·`$`·`` ` ``·`>`·`<`·`(`·줄바꿈)가 하나라도 있으면 즉시 거부한다. **키가 유출돼도 카탈로그 밖 명령은 실행되지 않는다** | 디스패처 단위 테스트(픽스처 컨테이너에서 거부·허용 전수) |
| ③ | 단일 정본 · 버전 고정 | 수집 스크립트는 카탈로그 YAML에서 **생성**한다(손으로 고치지 않음 · `shellcheck`·`bash -n` 통과 필수). 머리에 카탈로그 해시를 박고, 게이트웨이는 수집 전에 `aiops-ro-dispatch version`으로 해시를 대조해 다르면 `dispatcher_version_mismatch`로 거부한다 | 생성물 재현성 테스트 · 불일치 거부 테스트 |
| ④ | 변경 명령 부재 | `kill`·`renice`·`sysctl -w`·`systemctl restart/stop/start/enable/disable`·`dmesg -C/-c`·`journalctl --vacuum/--rotate/--flush`·`rm`·패키지 설치 명령이 카탈로그·디스패처에 0건 + 방어적 deny | 단위(D-189 불변식 테스트 이식) |
| ⑤ | 최소권한 계정 | 전용 비-root 계정(이름 G-5) · 로그인 셸은 디스패처 경유만 · 비밀번호 잠금 · 로그 읽기는 그룹(`systemd-journal`·`adm` — RHEL에서 시스템 journal 읽기 허용 그룹) · **RHEL `/var/log/messages`는 root 전용(0600)** 이라 journal을 1순위로 쓰고, journald가 비영속(`/var/log/journal` 없음 → 부팅 이후분만)인 서버만 sudoers 정확 일치 1줄로 messages 꼬리를 읽는다 · `dmesg`가 `kernel.dmesg_restrict=1`로 막히면 sudoers 정확 일치 1줄(`/usr/bin/dmesg -T`) · `yum history`(RHEL 7 root 필요)도 같은 방식 · 와일드카드 금지 · `/proc/<pid>/fd`는 프로세스 소유자만 볼 수 있어 다른 계정 프로세스는 「권한 없음」 사유로 남긴다 | 파일럿 `sudo -l` 표본 확인 |
| ⑥ | 자격증명 격리 | 개인키는 존 인스턴스만 · 파일 권한 600 · 비밀번호 인증 금지(BatchMode) · 존끼리 키 공유 금지 · 키 회전 절차 문서화(`docs/36` 관례) | 경계 테스트: 다른 패키지에 키 설정 필드 0 |
| ⑦ | 호스트 키 고정 | known_hosts 사전 배포 · `StrictHostKeyChecking=yes`. **`accept-new` 금지**(현 `noise_gate` 수집기가 쓰는 값 — 처음 접속하는 서버가 위장돼 있어도 그대로 신뢰한다) | 설정 단언 테스트 |
| ⑧ | 부하 가드 | 디스패처가 모든 명령을 `timeout 20 nice -n 10 ionice -c3`로 감싼다(`docs/25` 정본 값 · `ionice`가 없으면 생략하고 결과에 적음) · `top -b -n 1` 등 1회 스냅샷 고정 · 게이트웨이: 호스트별 진행 1건(`busy`) · 인스턴스 전체 동시 상한(기본 4) · 항목당 출력 상한(디스패처 `head -c`) | 단위 + 픽스처 |
| ⑨ | 마스킹 | 출력에서 비밀값(`password=`·토큰·키 형태)·명령 인자 마스킹 · `/proc/*/environ`·코어덤프·홈 디렉터리 파일은 카탈로그에 넣지 않음 · 패턴은 `mask_args`와 같은 규칙을 게이트웨이에 재정의(경계상 import 금지) | 단위 |
| ⑩ | 감사 | 전 호출 jsonl — 호출 주체(토큰 주체)·조사/알람 id·zone·host·profile·인자·rc·소요·출력 바이트 · 출력 본문은 감사에 넣지 않음. 대상 서버 쪽은 sshd 로그 + 디스패처의 `logger -t aiops-ro`(syslog) 1줄 | 단위 · 파일럿 syslog 확인 |
| ⑪ | 전송 인증 · 소비처별 도구 허용 | MCP Bearer 토큰(소비처별 분리) · 바인딩 주소 설정 · `type` 클레임 검증 · **토큰마다 호출 가능한 도구를 게이트웨이가 정한다** — `sre_agent`·`noise_gate` = 4종 전부 / 본체 = `host_profiles`·`host_facts`·`host_snapshot_diff`만(`host_collect` 거부 = 본체 경로 SSH 유발 0 · §8.1) | 단위: 본체 토큰으로 `host_collect` → 거부 |
| ⑫ | 망 경로 | 대상 22번은 같은 존 게이트웨이 IP에서만(방화벽 + `from=` 이중) · 중앙 → 존 게이트웨이 MCP 포트만 허용 | 파일럿 |

> **디스패처가 「에이전트 설치」인가**: 데몬·패키지·상주 프로세스가 없고, SSH 접속 때만 sshd가 실행하는 bash 스크립트 1개다(cron 등 상주 작업 없음 — §4.2 S2 미채택). 계정과 `authorized_keys`를 어차피 만들어야 하므로 같은 배포 단계에 실린다. 배포가 불가하면(G-10 거부) ②③을 빼고 `from=`·`restrict`만 건 일반 명령 실행으로 간다 — 이때 대상 쪽 방어가 없어지므로 보안팀 승인 조건을 다시 확인한다.

---

## 7. 수집 카탈로그 (초안 — W1에서 YAML로 확정)

**수집은 대상의 bash 수집 스크립트가 RHEL 기본 명령으로 하고, 파싱은 게이트웨이(Python)** 가 한다(§4.2 S1). **RHEL(RPM 계열)이 기본**이다. Debian 계열 분기는 W0 분포에서 비-RHEL Linux가 실재할 때만 만든다. OS 계열은 `/etc/os-release`(`ID`·`VERSION_ID`)로 판정한다.

### 7.1 정기 구성 스냅샷 (`snapshot.*`)

| 항목 | 명령 | 진단 쓰임 |
|---|---|---|
| OS·커널 | `cat /etc/os-release` · `cat /etc/redhat-release` · `uname -srvm` | 커널 교체 · 마이너 버전 |
| 가동·재부팅 | `cat /proc/uptime` · `last -x -n 20 reboot shutdown` | 예상 밖 재부팅 |
| 패키지 | `rpm -qa --qf '%{NAME}\t%{VERSION}-%{RELEASE}\t%{ARCH}\t%{INSTALLTIME}\n'` · (조건부) `yum history list` / `dnf history list` · (비-RHEL만) `dpkg-query -W` | 최근 설치·갱신과 그 시각 · 트랜잭션 이력 |
| 재부팅 필요 | (설치돼 있으면) `needs-restarting -r` | 커널 갱신 뒤 미재부팅 |
| 보안·튜닝 | `getenforce` · `tuned-adm active` | SELinux 모드 변경 · 튜닝 프로필 변경 |
| 크래시 흔적 | `ls -l /var/crash` | kdump 덤프 생성(커널 패닉) |
| 서비스 | `systemctl list-unit-files --type=service --no-pager --no-legend` · `systemctl list-units --type=service --state=running --no-pager --no-legend` | 서비스 비활성화·신규 서비스 |
| sysctl 선택 키 | `sysctl <키 목록>`(카탈로그 고정 목록 — `vm.swappiness`·`vm.overcommit_memory`·`fs.file-max`·`net.core.somaxconn`·`kernel.pid_max` 등) | 튜닝 변경 |
| 마운트·용량 | `cat /proc/mounts` · `df -hP` · `df -iP` | 마운트 누락·RO 전환 |
| 리스닝 포트 | `ss -lntu`(프로세스 열 `-p`는 다른 사용자 프로세스가 안 보이므로 1차 제외 · G-7) | 포트 변경·서비스 미기동 |
| 한도 | `cat /etc/security/limits.conf` · `ls /etc/security/limits.d` | FD 한도 |
| cron | `cat /etc/crontab` · `ls -l /etc/cron.d`(사용자 crontab은 G-7) | 새 배치 작업 |
| 시간 동기 | `chronyc tracking`(RHEL 7+ 기본) · `timedatectl show` | 시간 틀어짐 |
| 네트워크 | `ip -o addr` · `ip route` · `cat /etc/resolv.conf` | 라우트·DNS 변경 |

- **저장**: 호스트별 정규화 JSON(정렬) + 수집 시각 · SQLite(존 인스턴스 소유) · 보존 기본 90일(G-9).
- **비교**: 항목 단위 추가·삭제·변경 → `host_snapshot_diff(host, since)`. 매번 바뀌는 값(uptime·시각)은 비교 제외 목록에 둔다.
- **스케줄**: 일 1회(인스턴스 내부 스케줄러 · 시작 시각 분산) + 수동 CLI. 스냅샷 1건 = SSH 세션 1개(디스패처 `snapshot.all`이 구분자로 묶어 출력).

### 7.2 장애 시점 진단 프로파일 (`diag.*`)

`noise_gate` `L3_PROFILES`·`sre_agent` `VM_DIAG_ALLOW`를 옮겨 **단일 정본**으로 만든다. 프로파일 1개 = SSH 세션 1개.

| profile | 명령(디스패처가 가드 접두를 붙임) |
|---|---|
| `diag.cpu` | `uptime` · `cat /proc/loadavg` · `top -b -n 1` · `mpstat -P ALL 1 1` · `ps aux --sort=-%cpu`(상위 N줄) |
| `diag.memory` | `free -m` · `cat /proc/meminfo` · `vmstat 1 3` · `ps aux --sort=-%mem` · `dmesg -T`(OOM 줄 추출은 게이트웨이) |
| `diag.disk` | `df -hP` · `df -iP` · `iostat -xz 1 2` · `dmesg -T` |
| `diag.network` | `ss -s` · `ss -tan` · `sar -n DEV 1 1` · `dmesg -T` |
| `diag.process` | `ps -eo pid,ppid,user,stat,nlwp,%cpu,%mem,etime,args --sort=-%cpu` · `journalctl -p err --since -1h --no-pager`(journal 비영속 서버는 sudoers 1줄로 `/var/log/messages` 꼬리 — §6 ⑤) |
| `diag.service` `<unit>` `<since>` | `systemctl status <unit> --no-pager` · `journalctl -u <unit> --since <since> --no-pager -n 500` (since 허용값 `-15min`·`-1h`·`-6h`·`-24h`) |
| `diag.proc_detail` `<pid>` | `cat /proc/<pid>/limits` · `cat /proc/<pid>/status` · `ls /proc/<pid>/fd`(개수는 게이트웨이가 셈 · 다른 계정 프로세스는 권한 없음 사유) |
| `diag.history` `<날짜>` `<시작>` `<끝>` | `sar -f /var/log/sa/sa<DD> -s <시작> -e <끝>`에 `-u`(CPU 분해)·`-r`(메모리)·`-B`(페이징)·`-q`(run-queue·load)·`-d`(디스크)·`-n DEV`(네트워크) — **장애 시각 전후 과거 값**(§4.2 · 인자: 날짜는 보존 기간 안 · 시각 `HH:MM:SS` · 창 최대 6시간) |

- **결정적 요지**: `noise_gate` 상태지문(`derive_fingerprint`)·요지(`summary_lines`)는 소비처에 둔다 — 게이트웨이는 원시 출력과 명령별 rc만 준다(판정은 소비처 · D-035).
- 명령이 없으면(`sysstat` 미설치 → `mpstat`·`iostat`·`sar` 없음) `rc=127`을 사유로 싣고 나머지는 계속한다. `sa` 파일이 없으면(`sysstat` 수집 비활성) `diag.history`는 `reason="sysstat_history_unavailable"`. 설치·활성률은 W0 실측.
- `diag.history`의 시각 인자는 소비처가 알람 발생 시각에서 결정적으로 만든다(`noise_gate`·`sre_agent` 조사 컨텍스트) — LLM이 시각을 지어내지 않는다.

### 7.3 매개변수형 항목 (v1.4 — 「상황마다 다른 명령」을 생성 대신 인자로)

LLM 생성이 필요해 보이는 경우 대부분은 **고정 명령 + 검증된 인자**로 덮인다. 인자는 자유 문자열이 아니라 **카탈로그·인벤토리에 있는 값 중 하나**다.

| profile | 인자 | 명령 | 인자 출처(결정적) |
|---|---|---|---|
| `diag.log_tail` `<path_id>` `<lines>` | `path_id` = 카탈로그 로그 경로 표의 키 · `lines` ∈ {200, 500, 1000} | `tail -n <lines> <절대 경로>` | 경로 표(`path_id` → 절대 경로 · 와일드카드 금지)는 카탈로그에 두고 **수집 스크립트에 상수로 박힌다**. 호스트별로 쓸 수 있는 `path_id`는 인벤토리에 둔다 — 목록 밖이면 게이트웨이가 SSH 없이 `arg_invalid` |
| `diag.mw.<kind>` `[<instance>]` | `kind` = 미들웨어 종류 · `instance` = 식별 결과의 인스턴스 id | 종류별 고정 묶음(예: 기동 인자·스레드 수·자기 로그 꼬리 — 항목 내용은 W0에서 조직 미들웨어 목록 확정 뒤 정함) | `kind`·`instance`는 `diag.process` 출력에 **기존 결정적 식별 규칙**(`config/middleware_signatures.yaml` — D-035 · LLM 추정 없음)을 적용해 얻는다. 게이트웨이는 `src/`를 import하지 않으므로 같은 YAML 파일을 읽는다(식별 위치는 W1에서 확정) |

- 로그 본문은 응답에만 싣고 저장하지 않는다(G-7). 마스킹은 §6 ⑨.
- 인자를 받는 항목은 이 표와 §7.2(`unit`·`since`·`pid`·`history` 시각)로 한정한다. 새 인자형은 §7.4 경로로만 늘린다.

### 7.4 카탈로그 확장 경로 — LLM은 초안 작성자 (v1.4)

**LLM이 놓일 자리는 둘뿐이다.**

| 시점 | LLM의 일 | 위치 | 실행과의 거리 |
|---|---|---|---|
| 조사 중(실행 시점) | 카탈로그에서 **프로파일을 고르고** 인자를 채운다 | `sre_agent`(이미 있음 · §8) | 게이트웨이 입력 검증 + 대상 강제 명령이 다시 막는다 |
| 카탈로그 작성 시점(오프라인) | 새 항목의 **YAML 초안**을 쓴다 | `host_gateway`의 작성 CLI(`python -m host_gateway.catalog draft`) — **개발·운영 작업 환경에서 돌리고, 존 인스턴스의 MCP 서버 프로세스는 LLM 클라이언트를 갖지 않는다** | 결정적 검사 → 사람 승인 → 스크립트 재생성 → 재배포를 지나야 대상 서버에 생긴다 |

**흐름**:

1. **공백 기록(결정적)** — 두 출처에서 모은다.
   - `sre_agent`: 지침 1줄 「카탈로그에 없는 정보가 필요하면 명령을 만들지 말고 `collection_gap`으로 남긴다」. 조사 결과에 구조화 필드 `collection_gaps[{purpose, needed_signal, host, alarm_id, candidate?}]`를 싣고, 결론에는 「수집 불가 항목 있음」으로 밝힌다(정상으로 서술 금지). `candidate`(LLM이 떠올린 명령)는 **실행되지 않는 참고 텍스트**다.
   - 게이트웨이: `profile_unknown`·`arg_invalid` 거부를 감사 jsonl에 이미 남긴다(§6 ⑩) — 그대로 공백 신호로 쓴다.
   - 두 출처는 jsonl 파일 계약으로만 넘긴다(패키지 import 0).
2. **초안 작성(LLM)** — `catalog draft --gaps <jsonl…>`가 공백을 묶어 LLM에 주고, 항목 초안(목적 · 명령 · 인자 규칙 정규식 · 가드 · 파서 형식 · 「읽기 전용」 근거 · 필요 권한)을 `host_gateway/catalog/proposals/<날짜>-<slug>.yaml`에 쓴다. **활성 카탈로그는 건드리지 않는다.** LLM은 내부망 FabriX(개발은 로컬 MLX · 과금 외부 API는 건마다 승인 — D-127·D-240). 공백 텍스트에는 알람 본문 등 외부 문자열이 섞일 수 있으므로 **초안 전체를 신뢰하지 않는 입력으로 다룬다** — 아래 검사와 사람 승인이 그 가정 위에 있다.
3. **결정적 검사** — `catalog check <초안>`:
   - 스키마 · 인자마다 정규식 또는 허용값 필수(자유 문자열 인자 거부)
   - 명령 바이너리가 **읽기 전용 허용목록**(카탈로그에 둔 바이너리 목록) 안 · 금지 명령·옵션(§6 ④) 0 · 셸 메타문자 0 · 리다이렉트·파이프 0(가공은 게이트웨이 파서가 함)
   - 생성된 함수 `bash -n`·`shellcheck` 통과 · 가드 접두 자동 부착 확인
   - Rocky 픽스처 실행(`RUN_DOCKER_IT=1`) — rc·출력 상한·소요 시간 기록
   - 결과는 통과/실패와 사유 목록만 낸다. **검사는 승인을 대신하지 않는다.**
4. **사람 승인** — 운영자가 초안을 고쳐 활성 카탈로그로 옮긴다(저장소 리뷰). 새 바이너리·새 권한(sudoers)·새 로그 경로가 들어가면 보안팀 확인(G-14).
5. **재생성·재배포** — 생성기가 수집 스크립트를 다시 만든다. 해시가 바뀌므로 배포가 덜 된 서버는 `dispatcher_version_mismatch`로 드러나고, 새 항목은 배포된 서버에서만 쓸 수 있다.

**불변식**: 실행 시점에 LLM 출력이 셸 명령이 되는 경로는 없다. LLM이 만든 문자열은 사람 승인과 재배포를 거치기 전까지 대상 서버에 존재하지 않는다(D-367 ① · D-233).

---

## 8. 소비처 배선

| 소비처 | 배선 | 플래그(기본 off) |
|---|---|---|
| `sre_agent` | `_build_mcp_servers()`에 존 인스턴스를 각각 등록(`host_bankjon`·`host_gongjon` — URL이 빈 존은 등록하지 않음) · 같은 도구 이름은 holmes가 `host_bankjon__host_collect`처럼 나눠 노출(§2.2 실측) · 지침 1블록: 「조사 대상 db_id의 존 도구를 쓴다 — `polestar_b0`=은행존 · `polestar_cm_*`=공동존」 + 소스 선택 규칙(실시간 OS·로그·최근 변경 = host / 시계열 = PromQL / 장기 추세·알람 = 폴스타) + 「카탈로그에 없는 정보는 명령을 만들지 말고 `collection_gap`으로 남긴다」(§7.4) · bash는 계속 off | `HOST_GATEWAY_BANKJON_MCP_URL` · `HOST_GATEWAY_GONGJON_MCP_URL` · 토큰 각각 |
| `noise_gate` L3 | `l3_host_access_mode`에 `"gateway"` 추가 → 알람 db_id → zone(db_registry) → 해당 존 키의 `MCP_SOURCE_ENDPOINTS` 주소로 `host_collect(host, "diag.<kind>")` 호출(존 선택은 결정적 · LLM 없음) · 상태지문·요지 로직은 그대로 | 기존 `l3_enrichment_enabled` + 모드 값 · **주소는 별도 키를 만들지 않는다**(D-364 선례) — `MCP_SOURCE_ENDPOINTS`의 `host_bankjon`·`host_gongjon` · 토큰은 `NOISE_HOST_MCP_TOKEN`(소비처별 분리 · §6 ⑪) |
| 본체 `src/` | §8.1 — `host_inspect` 프로파일 2개 추가(`host_config`·`config_changes`) · 스냅샷 조회 도구만 · 실시간 수집은 `fault_diagnosis` 경유 | 별도 `enable_*` 없음 — `MCP_SOURCE_ENDPOINTS`에 존 키가 없으면 비활성(`plans/125` A-2 규약) |

- **존 선택이 LLM에 남는 곳은 `sre_agent` 하나다.** 잘못 고르면 인스턴스가 `host_not_in_inventory` + `hint`(「다른 존 도구로 다시 호출」)를 돌려주므로 오수집은 없다(인벤토리 밖 호스트는 SSH 자체를 하지 않음). 이것을 결정적으로 바꾸려면 중앙 라우터(host → zone 조회 후 전달)를 둔다 — G-11에서 판단.
- 경계: `sre_agent`·`noise_gate` ↔ `host_gateway` import 0(MCP 계약만) — `tests/test_boundary.py` 전례로 양방향 고정.
- 지침 실측: 지침이 실제 시스템 프롬프트에 실리는지 런타임 로그로 확인한다(mock 통과 ≠ 프로덕션).

### 8.1 본체 연결 검토 (v1.2 · 사용자 지시 「본체에 전달하는게 필요한지 검토하여 적용하라」)

**결론: 필요하다. 단 본체는 저장된 스냅샷만 읽고, 실시간 수집은 부르지 않는다.**

본체 채팅에서 서버 정보가 필요한 질의는 세 갈래이고, 갈래마다 필요성이 다르다.

| 질의 갈래 | 예 | 지금 본체 경로 | 게이트웨이 정보가 없으면 | 판정 |
|---|---|---|---|---|
| ① 장애 원인 분석 | "○○ 서버 원인 분석해줘" | `fault_diagnosis` → `sre_agent` 위임(submit/poll) | — | **본체 변경 불필요** — `sre_agent`가 W5에서 host 도구를 쓰면 위임 응답에 서버 직접 수집 근거가 실린다. 본체가 따로 부르면 같은 수집 경로가 둘이 된다(D-189 근거 4) |
| ② 서버 구성·변경 단건 조회 | "○○ 서버 sysctl·설치 패키지·서비스 목록", "어제 이후 바뀐 설정", "열린 포트" | `host_inspect` `os_config`(폴스타 EAV) — 패키지·서비스·sysctl·포트·cron·변경 이력이 **없다** | 답할 수 없다. 이것을 알려고 원인 분석(①)을 돌리면 분 단위 LLM 조사가 되고, `HOST_AUTHZ_MODE`에 막히는 사용자는 아예 못 묻는다 | **필요 — 편입** |
| ③ 실시간 OS 상태 단건 | "지금 ○○ 서버 top", "메모리 실시간" | `process_query`(폴스타 실시간 프로세스 API) · `metrics_live`(Prometheus) | 은행존·Prometheus 미등록 서버는 공백 | **1차 편입 안 함** — ①로 묻게 한다(아래 근거) |

**③을 본체에서 직접 부르지 않는 근거**:
1. **부하** — 채팅 질의 수는 조사 수보다 훨씬 많다. 채팅 한 줄이 운영 서버 SSH 수집을 일으키면 대상 부하·감사 범위가 질의 수만큼 늘어난다. 스냅샷 조회는 게이트웨이 SQLite만 읽어 대상에 닿지 않는다.
2. **경로 단일화** — 실시간 수집의 소비처를 `sre_agent`(사람이 요청한 진단)와 `noise_gate`(알람 자동 보강)로 한정해야 부하 상한·호스트별 1건 규칙이 한 곳에서 판단된다.
3. **인가** — 실시간 수집은 「서버에 명령을 보내는 것」이라 조사 권한(`HOST_AUTHZ_MODE`)의 대상이다. ①이 이미 그 인가를 거친다. 본체에 같은 인가를 한 번 더 구현할 이유가 없다.
4. 운영 뒤 ③ 수요가 실측되면(①로 넘어가는 단건 질의 비율) 운영자 한정 프로파일로 다시 판단한다(§14).

**적용 설계(②)**:
- **프로파일 2개 추가** — `host_inspect` `_PROFILE_KEYWORDS` 끝(최저 우선순위 — 기존 판정을 뺏지 않음)에 `host_config`(키워드 예: 「sysctl」·「설치 패키지」·「패키지 목록」·「서비스 목록」·「열린 포트」·「리스닝 포트」·「마운트」·「crontab」) · `config_changes`(「변경 이력」·「바뀐 설정」·「변경된 패키지」·「구성 변경」). 키워드는 W6에서 기존 `os_config`(「커널」·「OS 정보」)와 겹치지 않는지 확인한다 — **「커널」·「OS 정보」는 계속 `os_config`(폴스타)가 받는다**(측정 없이 넓히지 않는다는 `host_inspect` 원칙 준수).
- **호출** — `src/clients/source_mcp_client.py`(plans/125 공용 클라이언트)로 `host_facts(host)` · `host_snapshot_diff(host, since)`. 본체에 새 MCP 클라이언트를 만들지 않는다. `since`는 입력 파서의 시간 표현(결정적 해석기)에서 고르고, 없으면 기본 7일.
- **존 선택은 결정적** — 대상 db_id → db_registry zone → `MCP_SOURCE_ENDPOINTS["host_<zone>"]`. 키가 없으면 그 존은 비활성이고 「서버 직접 수집이 연동되지 않은 존」 사유로 거부한다(침묵 금지).
- **호스트 식별** — 기존 `host_inspect`처럼 대상 해석(server_name → hostname · D-046)을 거쳐 hostname으로 부른다.
- **인가** — `authorize_targets`(allowed_db_ids)에 더해 `host_authz`(조사 인가)를 적용한다(G-12 권고). 열린 포트·패키지 버전은 공격 표면 정보라 DB 조회 권한만으로 열지 않는다.
- **응답** — 결정적 표 + 수집 시각 명시(「YYYY-MM-DD HH:MM 수집 스냅샷 기준」 — 실시간 값으로 오인하지 않게 · `investigation_cache` 「침묵 금지」 원칙). 스냅샷이 없으면 `reason="no_snapshot"`으로 「아직 수집되지 않은 서버」라고 밝힌다.
- **매뉴얼** — 사용자가 쓰는 새 질의 유형이므로 D-255에 따라 `src/static/manual/user.html`(원천 `scripts/manual/`)을 같은 Wave에서 갱신한다.

---

## 9. OS 범위

- **1차: RHEL**(사용자 2026-11-05 — 대부분 RHEL). 메이저 버전 7·8·9 분포를 W0에서 폴스타 `polestar_os_config`로 확정한다. 모두 systemd·journald·chrony·`ss`를 쓰므로 카탈로그 하나로 덮는다. 버전 차이는 `yum`(7) / `dnf`(8·9) 이력 명령 하나뿐이며 스크립트가 `/etc/os-release`로 고른다.
- **RHEL 6이 남아 있으면**(SysV init · systemd 없음 · upstart) 서비스·로그 항목이 달라 별도 판단한다(W0).
- 비-RHEL Linux(소수)는 bash가 있으므로 같은 스크립트로 돌고, 패키지 항목만 `dpkg-query` 분기를 둔다(실재할 때만). D-117 「Linux 1차」 계승.
- AIX·Solaris·Windows는 명령·권한 모델이 달라 W0 분포 실측 후 별도 결정. 카탈로그 항목에 `os_family` 키를 두어 확장 여지만 남긴다.

---

## 10. Wave 계획

> 회귀 정책(D-303): Wave 중간은 바꾼 테스트 파일 `pytest` + `regress.py --no-tests --files …`. 계획 끝에 `regress.py --base <계획 시작 SHA> --files <계획이 바꾼 파일…>` 1회. 독립 패키지 테스트는 `cd host_gateway && ../.venv/bin/python -m pytest`.

| Wave | 내용 | verify |
|---|---|---|
| **W0 · 실측·결정(코드 0)** | 남은 게이트(§12) 수렴 · §6 표를 보안팀에 먼저 제출 · 폐쇄망 실측: RHEL 메이저 버전 분포(`polestar_os_config` 집계 · RHEL 6 잔존 여부) · `sysstat` 설치·수집 활성률과 `sa` 보존 일수 · `chrony`·`ionice` 설치율 · journald 영속 여부 · `/var/log/messages` 권한 · SELinux 모드 · `yum`/`dnf history` 비-root 실행 가능 여부 · `kernel.dmesg_restrict` 값 · SSH 정책(포트·bastion·`AllowUsers`·`authorized_keys` 위치) · 존 안 세부 망(김포·여의도 분리 여부) · 중앙 → 존 게이트웨이 MCP 포트 허용 가능 여부 · 표본 1대 SSH 왕복 지연 | 측정표 §15 기록 · 게이트 확정 |
| **W1 · 골격·카탈로그·디스패처 생성기** | `host_gateway/` 패키지(pyproject · config(zone·인벤토리·키 경로) · `__main__`) · 카탈로그 YAML(§7) · **수집 스크립트 생성기**(YAML → bash · 항목 = 함수 · 해시 머리 · `shellcheck`) · 입력 검증 · 인벤토리 로더 · 변경 명령 부재 테스트 | 생성물 재현성 · `bash -n`·`shellcheck` 통과 · 금지 명령 1개를 YAML에 넣으면 생성이 실패 · 스크립트에 `python` 호출 0 단언 |
| **W2 · 실행 엔진·가드** | OpenSSH 서브프로세스 실행기(§4.1 고정 옵션) · 버전 대조 · 호스트별 진행 1건·동시 상한 · 출력 파서(구분자 분리) · 마스킹 · 감사 jsonl · **Docker 픽스처(RHEL 호환)**: Rocky Linux 8·9 컨테이너 각 1대(RHEL 바이너리 호환 · 자유 배포) — sshd · 전용 계정 · 강제 명령 수집 스크립트 · `sysstat`(sa 파일 생성) · journal 그룹 권한 · `/var/log/messages` 0600 재현 · 수집 스크립트가 Python을 쓰지 않음을 확인(기존 ubuntu `target-vm`은 Prometheus 픽스처로 그대로 둔다) · W0에서 RHEL 7 비중이 크면 RHEL 7 호환 이미지 1대 추가 | `RUN_DOCKER_IT=1` 실 SSH 수집 통과 · 미기동 시 skip · 디스패처 거부 전수(메타문자·미등록 항목·잘못된 인자) · 동시 2요청 중 1건 `busy` · known_hosts 불일치 → `host_key_mismatch` |
| **W3 · MCP 도구** | 4종 · Bearer 인증 · 반환·오류 계약(§5.1) · 경계 테스트 | MCP 클라이언트 호출 · 인벤토리 밖 host → SSH 없이 `host_not_in_inventory` |
| **W4 · 스냅샷·비교** | SQLite 저장 · 정규화 · 비교(제외 목록) · 스케줄러 · 수동 CLI · 보존 정리 · RPM 기본(`yum`/`dnf` 이력 분기 · Debian은 실재할 때만) | 픽스처에서 패키지 1개 설치 후 비교 결과에 그 1건만 |
| **W5 · sre_agent 배선** | 플래그 · 존별 등록 · 지침 블록 · 지침 주입 실측 · `collection_gaps` 구조화 필드·jsonl 기록(§7.4 ①) · 대표 시나리오(메모리 포화) **MLX 1회**(두 평면 mlx 확인 후 · D-240) | 플래그 off 시 `_build_mcp_servers()` 결과 비트 동일 · 두 존 등록 시 holmes 도구 이름 `host_<zone>__*` 실측 · MLX 조사가 해당 존 `host_collect` 호출 |
| **W6 · 본체 연결(§8.1)** | `host_inspect` 프로파일 `host_config`·`config_changes`(키워드 끝 추가) · `source_mcp_client`로 `host_facts`·`host_snapshot_diff` 호출 · db_id → zone → `MCP_SOURCE_ENDPOINTS["host_<zone>"]` · `host_authz` 적용(G-12) · 결정적 표 + 수집 시각 · `no_snapshot`·존 미연동 사유 · `fault_diagnosis` 응답에 host 근거가 실리는지 종단 확인(본체 코드 변경 없음) · **매뉴얼 갱신**(D-255 — `features.yaml` → `content/user.md` → 캡처 → build → `pytest tests/test_manual`) | 존 키 미설정 시 프로파일 판정·응답이 현행 비트 동일 · 기존 4 프로파일 판정 회귀 0(키워드 겹침 테스트) · 본체 경로 호출 도구가 스냅샷 2종뿐임을 단언 · 권한 밖 사용자 거부 |
| **W7 · noise_gate L3 전환** | `l3_host_access_mode="gateway"` · 존 결정적 선택(`MCP_SOURCE_ENDPOINTS` 존 키) · 기존 `allowlist_exec` 병존 → 검증 후 `build_ssh_runner` 폐기 판정(D-161 ② 4항 실측 첨부) · D-189 부기 | `test_l3_postgate_enrichment`·`test_host_diagnostic_collector` 통과 · 모드 기본값 비트 동일 |
| **W8 · 카탈로그 확장 경로(§7.3·§7.4)** | 매개변수형 항목 `diag.log_tail`(경로 표·인벤토리 `path_id`)·`diag.mw.<kind>`(미들웨어 식별 YAML 재사용) · 작성 CLI `catalog draft`(LLM 초안 → `proposals/`)·`catalog check`(결정적 검사) · 런타임 MCP 프로세스에 LLM 클라이언트 import 0 경계 테스트 · 초안 LLM 검증은 가짜 LLM 기본 + MLX 대표 1회(두 평면 mlx 확인 · D-240) | 인벤토리 밖 `path_id` → SSH 없이 `arg_invalid` · 변경 명령·파이프·자유 문자열 인자를 넣은 초안 3종이 `check`에서 실패 · 정상 초안 1종 통과 · 초안 생성이 활성 카탈로그·스크립트 해시를 바꾸지 않음 · 승인 전 그 항목 호출 → `profile_unknown` |
| **W9 · 문서** | `docs/37_host_gateway_guide.md`(존별 배포 · 계정 · 키 · 디스패처 설치 · sudoers 예시 · 방화벽 · 운영 켜기 · 카탈로그 확장 절차(공백 → 초안 → 검사 → 승인 → 재배포) · 본체·sre_agent·noise_gate 설정 키) · 보안팀 제출용 §6 표 · `docs/26_sre_agent_guide.md`·`docs/28` 갱신 · `docs/21` 변경 없음 확인(새 단·처리기 없음 — 기존 `host_inspect` 프로파일 추가) | 링크 검사 |
| **W10 · 폐쇄망 파일럿** | 존별 2~3대(G-8) 계정·디스패처 배포 · 스냅샷 1주 · 실 장애 또는 재현 알람 1건 조사에 host 근거 인용 확인 · 본체 채팅 「어제 이후 바뀐 설정」 1건 | §11 수용 기준 7·8·10 |

---

## 11. 수용 기준

1. 카탈로그 밖 항목·인자는 게이트웨이에서 거부되고, 게이트웨이를 우회해 같은 키로 접속해도 디스패처가 거부한다(픽스처 실측).
2. 변경 명령이 카탈로그·생성된 디스패처에 0건(테스트로 고정).
3. 전 명령에 부하 가드가 붙고, 같은 호스트 동시 수집은 1건만 진행된다.
4. 전 호출이 감사 로그에 남고, 출력 본문·키는 로그에 남지 않는다.
5. `StrictHostKeyChecking=yes`·비밀번호 인증 불가가 설정으로 고정되고, 존 인스턴스끼리 키를 공유하지 않는다.
6. 소비처 플래그 off에서 `sre_agent`·`noise_gate` 동작이 현행과 비트 동일하다.
7. 폐쇄망 파일럿에서 스냅샷 비교가 실제 변경(패키지·서비스·sysctl 중 1건 이상)을 잡는다.
8. 조사 결론이 host 도구 근거(명령·시각)를 인용하고, 수집 실패 시 사유를 밝히며 「정상」으로 서술하지 않는다.
9. `arch_check --ci`·`overfit_check --ci` 통과 · 경계 테스트(import 0) 통과.
10. 본체 채팅의 서버 구성·변경 질의가 스냅샷으로 답하고(수집 시각 명시), 본체 경로는 대상 서버 SSH를 일으키지 않는다(게이트웨이 감사 로그에 본체 토큰의 `host_collect` 0건).
11. 장애 시각을 지정한 `diag.history`가 그 창의 `sar` 과거 값을 돌려주고, `sa` 파일이 없으면 사유로 밝힌다(픽스처 · 파일럿).
12. 실행 시점에 LLM 출력이 셸 명령이 되는 경로가 0이다 — 존 인스턴스 MCP 프로세스에 LLM 클라이언트 import 0(경계 테스트) · `sre_agent`는 카탈로그 밖 요구를 `collection_gaps`로 남기고 결론에 밝힌다.
13. LLM 초안은 `catalog check`와 사람 승인·재배포 전에는 호출되지 않는다(`profile_unknown`) · 변경 명령·파이프·자유 문자열 인자를 담은 초안은 검사에서 실패한다.

---

## 12. 결정 게이트

| # | 질문 | 상태 · 권고 |
|---|---|---|
| G-1 | SSH 미채택 결정 개정 | **확정(2026-11-05) — 개정** |
| G-2 | 운영팀 Ansible·인벤토리 사용 | **확정 — 미사용** |
| G-3 | 대상 Python | **확정 — 미설치** → Ansible 제외 · 대상 OS 대부분 **RHEL**(확정 2026-11-05) |
| G-4 | 실행 엔진 | **확정(G-3에서 따라 나옴) — OpenSSH 직접 실행** |
| G-5 | 대상 계정 이름·발급 절차·sudo 범위(보안팀) | 대기 · 그룹 권한 우선 · sudoers 정확 일치 최소 |
| G-6 | 존 망 분리 | **확정 — 분리** → 존별 인스턴스 |
| G-7 | 수집 범위 경계 — 사용자 crontab · `ss -p` · 앱 설정 파일 · 로그 본문 보존 | 대기 · 1차는 시스템 범위만 · 로그 본문은 응답에만 싣고 저장하지 않음 |
| G-8 | 파일럿 대상 서버 수·선정 | 대기 · 존별 2~3대 · 장애 빈도 높은 업무 |
| G-9 | 스냅샷 주기·보존 | 대기 · 일 1회 · 90일 |
| **G-10** | 대상 서버에 **강제 명령 디스패처**(bash 수집 스크립트 1개 · 상주 작업 없음) 배포 허용 | 대기 · **권고: 허용** — 키 유출 시에도 카탈로그 밖 실행 불가(§6 ②) · 거부 시 대상 쪽 방어 없음 |
| **G-11** | 존 게이트웨이 배치 호스트 · 중앙 → 존 MCP 포트 허용 · `sre_agent` 존 선택을 중앙 라우터로 결정화할지 | 대기 · 권고: 존 망 안 기존 관리 호스트에 배치 · 라우터는 W5 실측(오선택 빈도) 뒤 판단 |
| **G-12** | 본체 채팅의 스냅샷 조회(패키지·포트·sysctl·변경 이력)에 조사 인가(`HOST_AUTHZ_MODE` · 기본 `admin_only`)를 적용할지 | 대기 · **권고: 적용** — 열린 포트·패키지 버전은 공격 표면 정보라 DB 조회 권한만으로 열지 않는다. 일반 사용자에게 열려면 `HOST_AUTHZ_MODE` 값으로 푼다(정책 1곳) |
| **G-13** | 대상 cron 주기 수집(§4.2 S2 — 장애 이전 프로세스 상태 링 버퍼)을 둘지 | 대기 · **권고: 1차 미채택** — 과거 시스템 지표는 `sysstat` `sa`로 얻는다 · 파일럿에서 프로세스 이력 공백이 진단을 막은 사례가 나오면 재판단 |
| **G-14** | 카탈로그 확장 초안의 승인자 · 보안팀 재확인 범위(§7.4 ④) | 대기 · **권고**: 기존 허용 바이너리·기존 권한 안의 항목은 운영팀 승인 · 새 바이너리·새 sudoers 줄·새 로그 경로는 보안팀 확인 · 승인 이력은 저장소 리뷰로 남김 |

---

## 13. 리스크

| 리스크 | 영향 | 대응 |
|---|---|---|
| 보안 승인 지연(계정·방화벽·키·디스패처) | 파일럿 지연 | W1~W5는 Docker 픽스처로 선행 · §6 표를 W0에서 먼저 제출 |
| 원격 명령은 결국 대상 계정의 셸이 해석한다(SSH의 본성) | 인자 주입 | 게이트웨이 정규식 + 디스패처 재검사 + 메타문자 즉시 거부 · 인자를 받는 항목은 §7.2·§7.3 표로 한정(허용값·정규식 필수) |
| 디스패처 버전 불일치(배포 누락) | 수집 거부 | 해시 대조로 명시 거부(`dispatcher_version_mismatch`) · 배포 절차에 버전 확인 단계 |
| 포화 서버에 수집 부하 | 장애 악화 | §6 ⑧ 가드 · 프로파일 단위 · 호스트별 1건 |
| 대량 알람 시 동시 수집 폭주 | 게이트웨이·대상 부하 | 인스턴스 동시 상한 · `noise_gate` `l3_max_inflight` 유지 · 상한 초과는 사유 반환(무한 대기 금지) |
| 출력 속 민감정보 | 유출 | §6 ⑨ · 저장은 스냅샷(구성)만 · 진단 출력은 응답에만 |
| hostname 불일치(VM name ≠ OS hostname) | 엉뚱한 서버 수집 | 인벤토리 키 = OS hostname · 폴스타 해석은 `mcp_server` 재사용 · 인벤토리 밖이면 SSH 하지 않음 |
| `sre_agent` 존 오선택 | 1회 헛호출 | `host_not_in_inventory` + `hint` · G-11 라우터 |
| 서버에 `sysstat`이 없거나 수집이 꺼져 있음 | `diag.history`·`mpstat`·`iostat` 불가 | 사유 반환 · W0 설치·활성률 실측 · 켜는 것은 운영팀 설정 변경(이 계획이 바꾸지 않음) |
| 공백 기록·초안에 외부 문자열(알람 본문 등)이 섞여 악성 명령이 초안에 들어감 | 승인되면 대상에서 실행 | 초안을 신뢰하지 않는 입력으로 다룸 · `catalog check` 허용목록 · 사람 승인(G-14) · 실행 시점 경로 없음(§7.4 불변식) |
| 카탈로그 확장이 느려 조사 공백이 오래 남음 | 진단 품질 | 공백 집계를 정기 검토 · 결론에 「수집 불가 항목」 명시로 공백이 가려지지 않게 함 |
| 본체 스냅샷을 실시간 값으로 오인 | 잘못된 판단 | 응답에 수집 시각 고정 표기 · 실시간이 필요하면 원인 분석(`fault_diagnosis`)으로 안내 |
| 새 키워드가 기존 `host_inspect`·`data_query` 판정을 잠식 | 기존 질의 회귀 | 키워드 끝 추가(최저 우선순위) · 겹침 테스트 · 「커널」·「OS 정보」는 `os_config` 유지 |

---

## 14. 범위 밖 · 후속 후보

- **로그 중앙 전송(E안)** — rsyslog/journald → 중앙 수집 서버. 과거 로그 공백의 근본 해법. 별도 계획.
- **node_exporter 설정 확장(D안)** — `--collector.systemd`·textfile collector. Prometheus 등록 서버에만 효과.
- **본체에서 실시간 수집 직접 호출**(§8.1 ③) — 운영 뒤 단건 실시간 질의가 원인 분석으로 넘어가는 비율을 재서, 필요하면 운영자 한정 프로파일로 연다.
- **양식(Excel·Word) 채우기에 스냅샷 값 사용** — 서버 사양 양식의 OS·커널·패키지 칸. 폼필 결정적 조립 경로에 소스를 더하는 별도 계획.
- **조치(remediation)** — 재기동·설정 변경 자동화는 하지 않는다. 권고만(D-117 불변식).
- 다수 호스트 일괄 진단 · 비동기 작업 큐.

---

## 15. W0 실측 기록 (착수 시 채움)

| 항목 | 값 | 출처·일시 |
|---|---|---|
| Ansible 사용 | 미사용 | 사용자 2026-11-05 |
| 대상 Python | 미설치 | 사용자 2026-11-05 |
| 은행존·공동존 망 | 분리 | 사용자 2026-11-05 |
| 대상 OS | 대부분 RHEL | 사용자 2026-11-05 |
| RHEL 메이저 버전 분포(6·7·8·9) | | |
| `sysstat` 설치·수집 활성률 · `sa` 보존 일수 | | |
| journald 영속(`/var/log/journal`) 여부 | | |
| `/var/log/messages` 권한 · SELinux 모드 | | |
| `yum`/`dnf history` 비-root 실행 가능 여부 | | |
| 패키지 관리자 인터프리터(참고 — 결정 무관) | | |
| 공동존 안 김포·여의도 망 분리 | | |
| OS·버전 분포 | | |
| `kernel.dmesg_restrict` | | |
| SSH 정책(포트·bastion·`AllowUsers`) | | |
| 중앙 → 존 게이트웨이 MCP 포트 | | |
| 표본 SSH 왕복 지연 | | |
