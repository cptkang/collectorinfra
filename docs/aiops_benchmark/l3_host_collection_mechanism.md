# L3 OS 명령 조회 메커니즘 제안 + HolmesGPT 심층 활용 방안 (L3 Host Command Collection Mechanism & HolmesGPT Adoption)

> 작성일: 2026-07-21
> **성격**: 조사·제안 문서 (구현 계획 아님). Plan 64 §7 / Plan 51 §5.3 의 **L3 접근 메커니즘(B-1 보안결정)** 을 실제 배선 수준으로 구체화하고, 그 근거로 조사한 선행 오픈소스(**HolmesGPT/CNCF**)의 활용 방안을 상세화한다.
> **대상/선행 계획**: **Plan 64**(자동 조사 오케스트레이션 — `host_diagnostic_collector.py` 명명·§7 L3 보안통제), **Plan 51**(§5.3 L3 3옵션 A/B/C·§9 보안·부록 A 허용목록·시그니처), **Plan 60 §14**(게이트 PAGE → 조사 트리거 계약), **Plan 50**(`evidence_collector` 진단 파이프라인).
> **관련 결정**: **D-003(읽기전용 절대원칙 — 최대 제약)**, D-035(결정적 규칙=판단·LLM=보조), D-037(deepagents), Plan 64 예약 **D-102**(L3 실호스트 read-only 수집기 + 보안통제).
> **요구(사용자)**: "이벤트 발생 시 `top`/`vmstat`/`journalctl` 등 OS 명령을 호출해야 한다. 관련 논문·기술자료를 찾아 collectorinfra 에이전트에서 조회할 수 있는 방안을 제안하라." + "HolmesGPT/CNCF를 더 분석해 활용 방안을 상세히 정리하라."
> **상태**: 제안 (B-1 착수 게이트 미결정). 구현 전 §8 결정 항목을 **사용자·보안팀 확인**(CLAUDE.md 의사결정 규칙).
> **설치 제약(2026-07-21 사용자)**: 시스템 서버에 **신규 에이전트 설치는 곤란**. 현재 설치된 것은 **node_exporter + polestar 에이전트뿐**. → **§4A**가 이 기준으로 "즉시 가능(채널0) / 설정·벤더확장(채널2, 신규설치 0) / 최후 신규설치(채널3)"를 분리 분석하며, **본 문서의 실질 1차 실행안은 §4A**다(§4·§5는 채널3 착수 시 참조).
> **소스 우선순위(2026-07-21 사용자)**: **기본 = 폴스타(DB·REST·ES) / 폴백 = node_exporter.** 폴스타(프로세스·로그·실시간 사용률 통합)를 1순위 조회하고, node_exporter는 USE 분해·host 카운터·baseline 등 폴스타 미제공 신호에만 폴백(§4A.4·Plan 64 §4.5·§4.7). ⚠️ **§4~§9 본문은 원래 node_exporter 중심으로 작성됨** — 우선순위 재정렬은 **§4A.4가 최신 기준**이며, §3(osquery)·§9(Prometheus 테스트)는 폴백 채널 관점으로 읽는다.
> **근거 자료**: `incident_investigation_literature.md`(Plan 64 문헌 dossier)와 상호보완. 본 문서는 **실행 메커니즘·HolmesGPT 활용**에 특화.

---

## 1. 문제 정의 — 무엇이 이미 결정됐고 무엇이 비었나

Plan 60 §14 → Plan 64는 "이벤트가 노이즈 게이트를 PAGE로 판정하면 `top`/`uptime`/`vmstat`/`journalctl`/`dmesg`를 자동 조사한다"는 요구를 **설계 수준**으로 이미 확정했다. 아래는 **결정 완료** 사항이다.

| 이미 결정된 것 | 위치 |
|---|---|
| **무엇을 수집할지** (명령·시그니처·USE 매핑) | Plan 51 §3.2(Netflix 60초), 부록 A.1(시그니처 치트시트), A.2(USE 매핑) |
| **접근 3옵션** | Plan 51 §5.3 — A(폴스타 에이전트 확장) / B(허용목록 read-only 수집기) / C(로그 전송) |
| **보안통제 원칙** | Plan 64 §7.2, Plan 51 §9 — 허용목록·no-shell·마스킹·감사·최소권한·자격증명 분리 |
| **트리거 계약** | Plan 60 §14 — 게이트 PAGE → Plan 50 push 훅 비차단 emit |
| **오케스트레이션 골격** | Plan 50 `diagnosis_graph` → Plan 64 `investigation_graph`(evidence_collector에 L3 소스 추가) |

**비어 있는 것은 딱 하나** — 사용자가 물은 지점: **"에이전트가 실제로 어떤 배선(transport/execution)으로 이 명령을 호출하는가"**. Plan 64 §14가 명명한 `host_diagnostic_collector.py`의 **구체적 구현 메커니즘**이 미정이며, B-1(L3 보안결정)이 착수 게이트로 남아 있다. 본 문서는 그 메커니즘을 제안한다.

---

## 2. 결정적 아키텍처 제약 — 왜 "에이전트 셸"이면 안 되는가

주류 LLM-RCA 에이전트(HolmesGPT)는 **agentic loop**로 LLM이 매 스텝 어떤 명령을 실행할지 스스로 결정한다. 그러나 collectorinfra는 설계 원칙상 이 패턴을 **의도적으로 거부**한다(Plan 64 §1.3-5, Plan 51 §1.3):

1. **"tool-calling 비의존"** — 워커 LLM(FabriX/KBGenAIChat)·폐쇄망·외부 SaaS 및 인터넷 tool-calling 금지.
2. **"결정적 규칙=판단 / LLM=보조 서술"(D-035)** — LLM이 명령을 고르는 게 아니라, **고정 플레이북**(Plan 51 §6, 알람 kind별)이 결정적으로 명령셋을 선택하고 **코드가 실행**, LLM은 캡처된 출력의 **해석·서술만** 한다.

이 차이가 제안의 뼈대를 결정한다. **LLM에 셸/SSH를 주는 게 아니라, 결정적 코드가 고정 허용목록 명령을 실행하는 read-only 조회 채널**을 만든다. 이 방향은 오히려 안전성 연구 합의와 더 정합한다 — **RCACopilot(EuroSys 2024)** 의 검증된 2단계(결정적 수집 → LLM 예측, MS 4년+ 운영·정확도 0.766)와 동일하다.

> ⚠️ **보안 경고(리서치 근거)**: 범용 SSH MCP 서버(예: `tufantunc/ssh-mcp`)처럼 **임의 셸을 노출하는 MCP는 D-003 위반이며 절대 채택 불가**. 2025년 `mcp-remote` RCE(CVE-2025-6514, CVSS 9.6)와 Invariant Labs의 SSH 키 탈취 tool-poisoning 사례가 이 위험을 실증했다. 우리가 만들 것은 **허용목록만 실행하는 제약형 채널**이지 범용 명령 서버가 아니다.

---

## 3. 핵심 통찰 — collectorinfra는 SQL 에이전트다 → osquery가 절반을 공짜로 준다

collectorinfra의 본질은 "자연어 → SQL → DBHub(MCP) 조회"다. **osquery**(Meta 개발, CrowdStrike/보안업계 광범위 배포)는 OS 상태(프로세스·메모리·소켓·마운트·FD)를 **관계형 DB로 노출**해 SQL로 조회하게 한다. osquery는 **쿼리 엔진이라 변이(write) 자체가 불가능** — read-only가 본성이며 Linux/Windows/macOS 공통이다. 즉 **기존 NL→SQL 파이프라인·DBHub 클라이언트(`src/dbhub/client.py`) 패턴을 그대로 재사용**해 OS 상태를 조회할 수 있다. 새 실행 모델이 아니라 "DB가 하나 더 늘어난 것"으로 취급된다.

다만 osquery는 **구조화된 상태**는 잘 주지만 **순간 delta와 로그는 못 준다**. 이 경계가 하이브리드 구조를 결정한다.

| 트리아지 단계 / 명령 | osquery 커버 | osquery 테이블 | 미커버 → 필요 채널 |
|---|---|---|---|
| `free`/`meminfo` (메모리) | ✅ 완전 | `memory_info` | — |
| `ps`/프로세스 Top | ✅ 완전 | `processes` (+ 폴스타 프로세스 API L1) | — |
| `ss`/`netstat` (소켓·리슨) | ✅ 완전 | `listening_ports`, `process_open_sockets` | — |
| `df -h`/`-i` (용량·inode) | ✅ 대부분 | `mounts` (inode 정밀은 보조) | inode는 relay 보조 |
| `top` %us/%sy/**%wa**·`vmstat r`·`si/so` | ❌ 순간 delta 없음 | — | **relay: `top -bn1`/`vmstat 1 3`/`mpstat`** |
| `journalctl -p err`·`dmesg` (OOM·FS RO) | ❌ 로그 조회 테이블 없음 | — | **relay: `journalctl`/`dmesg`** 또는 C(로그전송) |

> osquery는 이벤트 기반 테이블(감사)은 있으나 "`journalctl -p err --since T`를 재현하는 로그 조회 테이블"과 "순간 포화 지표(vmstat r, si/so)"는 제공하지 않는다(공식 문서·배포 가이드 확인). 따라서 로그·순간 delta는 별도 채널 필수.

**결론**: osquery가 USE의 ②병목·③프로세스 격리의 "구조화 상태"를 SQL로 커버하고, **순간 포화 지표와 로그 시그니처는 별도 read-only relay가 담당**하는 **2채널 하이브리드**가 최적이다.

---

## 4. 제안 아키텍처 — 2채널 read-only 진단 수집 + 폴스타 채널 우선

```
                      ┌─ (L1 재사용) DBHub MCP ─ cmm_metric_stat / cmm_alarm / cmm_resource
 evidence_collector ──┤
 (Plan 50/51 노드,    ├─ 채널1: osquery 엔드포인트 ──── 구조화 OS 상태(SQL, read-only)
  asyncio.gather,     │        (프로세스·메모리·소켓·마운트)   ← 기존 NL→SQL 패턴 재사용
  고정 플레이북)      │
                      └─ 채널2: 진단 명령 relay ──────── 순간 delta·로그
                               (허용목록·no-shell·audit)   top/vmstat/journalctl/dmesg
```

**채널2의 실행 방식은 Plan 51 §5.3 우선순위를 그대로 따른다** (원칙: "운영 호스트에 새 접근경로를 여는 것은 폴스타가 이미 가진 채널 재사용보다 항상 위험").

1. **1순위 — 옵션 A (폴스타 에이전트 확장)**: 폴스타는 이미 호스트에 검증된 read-only 에이전트를 두고 있다(프로세스 API가 그 증거 — `polestar_process_api.py`). 여기에 `dmesg`/`journalctl -p err`/`vmstat` **스냅샷 항목을 정의·노출**하도록 벤더 협의. **신규 접근경로 0**, 자격증명·감사 채널 기존 재사용. 가장 안전.
2. **2순위 — 옵션 C (로그 전송)**: `journalctl`/`dmesg`는 rsyslog/journald forward → 중앙 수집 후 **SQL 조회**(DBHub 재사용). 과거 로그 포렌식 가능, 호스트 직접접근 불필요. E3 Holt-Winters baseline이 요구하는 **과거 시계열**도 이 계열(`sysstat`/`node_exporter`)로 확보(Plan 51 §7.2).
3. **최후수단 — 옵션 B (허용목록 read-only 수집기)**: A/C로 못 얻는 항목만. **DBHub와 동형의 전용 read-only 진단 MCP/REST 서버**로 구현(§5). 자격증명(SSH 키)은 **서버측에만**, 에이전트는 URL/핸들만 보유(D-003 분리 원칙 — DB·프로세스 API에서 이미 확립).

---

## 4A. 현실 제약 — 설치된 에이전트(node_exporter + polestar)만 기준

> **사용자 제약(2026-07-21)**: "현재 시스템 서버에 신규 에이전트 설치는 쉽지 않다. 현재는 **node_exporter와 polestar 에이전트만** 설치되어 있다." → §4의 "osquery + relay 신규 2채널"을 **"기존 2에이전트 최대화 → 설정/벤더확장 → (최후) 신규 relay"** 로 재배치한다. osquery는 신규 설치라 **1차 범위에서 제외**.

### 4A.1 현재 설치 기준으로 처리 가능한 것 (커버리지 매트릭스)

| 트리아지 | 필요 신호 | node_exporter | polestar 에이전트 | 커버 |
|---|---|---|---|---|
| **① 부하** | load, run-queue | `node_load1/5/15`, `node_procs_running/blocked`(=vmstat r/b) | `cmm_metric_stat` Util 추이·알람 | ✅ 완전 |
| **② CPU 분해** | us/sy/**wa**/steal | `node_cpu_seconds_total{mode}` → rate | (Util 총량만) | ✅ **node_exporter** |
| **② 메모리** | MemAvailable, **swap si/so** | `node_memory_*`, `node_vmstat_pswpin/pswpout` | Mem Util 추이 | ✅ 완전 |
| **② IO** | await·%util | `node_disk_*`(카운터 rate로 근사) | Disk MaxIORate | ✅ 대부분(근사) |
| **② NW** | 재전송·drop | `node_netstat_Tcp_*`·`node_network_*_errs/drop` | NW 알람 | ✅ 대부분 |
| **② inode/FD** | inode·host FD | `node_filesystem_files_free`·`node_filefd_*` | FS Util | ✅ (host 레벨) |
| **③ 프로세스 격리** | top CPU/Mem 프로세스 | ❌ (host 레벨만) | **실시간 프로세스 API(Plan 47-1)** | ✅ 폴스타(실시간 단면) |
| **③ 프로세스 생존** | down·재시작 루프 | `node_systemd_*`(활성화 시) | ProcessMonitor `avail_status` | ✅ |
| **④ OOM 발생여부** | OOM 발생 | `node_vmstat_oom_kill`(카운트) | LogMonitor 관제 시 conditionLogText | △ 발생만 앎, PID·상세 X |
| **④ 로그 시그니처** | journalctl/dmesg 원문 | ❌ | conditionLogText(**관제 매칭분만**) | △ 관제 규칙 내만 |
| **baseline(E3)** | 과거 시계열 | Prometheus TSDB | `cmm_metric_stat_h/d/m` | ✅ 둘 다 |

**핵심 소득**: 원래 osquery/relay(신규 설치)에 맡기려던 **②병목 USE 분해(us/sy/wa/steal, swap si/so, inode)가 node_exporter로 이미 확보**된다. 폴스타 Util%가 못 주던 분해를 node_exporter 카운터가 채운다. ③은 폴스타 프로세스 API로 커버. **즉 ①②③ + baseline은 신규 에이전트 0으로 즉시 가능** — 조사 브리핑 뼈대의 대부분.

> 정직한 한계: (1) node_exporter **systemd collector 기본 비활성** — `--collector.systemd` 켜야 재시작 루프 확보. (2) `iostat` await/avgqu-sz 정밀치는 카운터 rate **근사**. (3) `node_procs_blocked`는 D-state **개수**만(어느 PID인지 X). (4) conditionLogText가 로그 **본문**을 담는지는 표본검증 필요(Plan 51 §5.2).

### 4A.2 신규 에이전트 **없이** 갭(④)을 좁히는 방법 — 설치가 아닌 "설정/기존 확장"

| 수단 | 무엇을 얻나 | 신규 에이전트? |
|---|---|---|
| **폴스타 LogMonitor 규칙 확장(옵션 A)** | OOM/segfault/FS-RO 시그니처를 관제 대상에 추가 → conditionLogText로 매칭 라인 확보 | ❌ **기존 폴스타 에이전트 재사용**(벤더 협의) |
| **node_exporter systemd collector 활성화** | 재시작 루프·유닛 상태 | ❌ 설정 변경만 |
| **node_exporter textfile collector + cron** | `dmesg` 시그니처 **카운트**를 `.prom`으로 노출 | △ 호스트 스크립트 배포(경계선, 카운트만·본문 X) |
| **rsyslog/journald 중앙 전송(옵션 C)** | journald/dmesg 원문을 중앙 수집 후 조회 | ❌ **rsyslog/journald는 OS 기본** → per-host 설치 0, 중앙 수집서버 **1대만** 신설 |

→ 옵션 C가 핵심 회피책이다: **로그 전송은 호스트마다 에이전트를 까는 게 아니라 OS 기본 rsyslog/journald 설정**이고, 신설은 중앙 수집서버 1대뿐이다.

### 4A.3 그래도 신규 설치가 필요한 **잔여 갭** (분리)

| 잔여 갭 | 무엇을 위해 | 필요 수단(신규) | A/C로 회피 가능? |
|---|---|---|---|
| 관제규칙 **밖** 커널 원문(call trace·anon-rss 상세·hung_task·FS RO 리마운트) | OOM된 PID·크래시 정밀 포렌식(부록 A.1) | 옵션 B relay 또는 osquery | ✅ 폴스타 LogMonitor 확장(A)+로그전송(C)이 **대부분 대체** |
| **과거** 프로세스 추이 | 누수·선행 프로세스 상관 | process-exporter(신규) / 폴스타 스냅 적재(A) | △ 실시간 단면+메트릭 추이로 근사 |
| **per-process 정밀**(FD vs limit, D-state wchan, thread수) | FD 고갈·hung task 원인 프로세스 지목 | osquery / process-exporter / B relay | △ host 레벨 `node_filefd_*`·`node_procs_blocked`로 1차 판정만 |

### 4A.4 재배치된 채널 구조 (설치 제약 반영)

> **소스 우선순위(2026-07-21 확정): 기본 = 폴스타(DB·REST·ES) / 폴백 = node_exporter.** 폴스타는 벤더 검증 채널이자 프로세스·로그·실시간 사용률을 통합 제공하므로 1순위로 조회하고, node_exporter는 폴스타가 못 주는 신호(USE 분해·host 카운터·baseline)에만 폴백한다(Plan 64 §4.5·§4.7 정합).

```
[기본] 채널0(설치됨): 폴스타 에이전트 (DB·REST·ES) ── 통합 소스
   · DBHub(SQL)        ── cmm_metric_stat(추이)·cmm_alarm·conditionLogText·avail_status
   · REST 프로세스 API  ── ③ 실시간 top 프로세스·생존
   · 폴스타 API(ES 백엔드) ── ①② CPU/메모리 실시간 사용률·③ 과거 프로세스 추이·④ 로그(제공 시)  [REST GET·직접 ES 아님]
[폴백] 채널0f(설치됨): node_exporter/Prometheus ── ② USE 분해(us/sy/wa/steal·si/so)·host 카운터·E3 baseline
                                                 [폴스타 미제공 신호·분해 필요 시만]
── 채널0(폴스타 기본) + 폴백(node_exporter)로 ①②③ + 과거추이 + baseline 커버 ──
채널2(설정/벤더확장, 신규 에이전트 0):
   · 폴스타 LogMonitor 규칙 확장(A) ── ④ 관제 시그니처 매칭 라인
   · rsyslog/journald 중앙 전송(C) ── ④ 로그 원문(중앙서버 1대만 신설)
── 여기까지로 ④ 대부분 커버 ──
채널3(최후·B-1 보안결정): B relay / process-exporter / osquery(신규 설치)
   · 관제규칙 밖 커널 원문 정밀 포렌식 + per-process 상세 (좁은 잔여만)
```

**결론(소스 우선순위 — 기본 폴스타/ES · 폴백 node_exporter)**: **기본은 폴스타(DB·REST·ES)** — 벤더 검증 채널이자 프로세스·로그·실시간 사용률을 통합 제공하므로 1순위 조회. **node_exporter는 폴백** — USE 분해(us/sy/wa/steal)·host 카운터·baseline 등 폴스타 미제공 신호에만 보강(Prometheus 미배포 존에선 폴스타 API만으로 ①② 실시간 판정 가능). 채널2(설정/벤더확장, 신규 설치 0)로 ④ 로그 대부분, 진짜 신규 설치(채널3)는 관제규칙 밖 커널 포렌식·per-process 정밀의 좁은 잔여로 최후순위화. **구현 매핑**: 폴스타 API(ES 백엔드) = `polestar_es_api.py`(폴스타 REST 클라이언트·**직접 ES 아님**·기본·미착수, Plan 64 §4.7) · node_exporter = `prometheus_client.py`(폴백, §9 구현 완료). HolmesGPT 활용(§7)·옵션 B(§5)는 각 채널 착수 시 적용.

---

## 5. 옵션 B 상세 — DBHub 패턴을 복제한 "제약형 진단 서버" (채널3 최후수단)

핵심은 **범용 명령 서버가 아니라, 파라미터화된 고정 명령만 실행하는 `execute_sql`의 진단판(版)**이다. `execute_sql(sql)`이 read-only 가드를 거치듯, `run_diagnostic(command_id, args)`가 허용목록 가드를 거친다.

### 5.1 서버측 실행 안전 (리서치 근거: Semgrep/Snyk/OWASP 명령 인젝션 방지)

- **`command_id` 이넘 → 사전등록 argv 배열** 매핑. 사용자/LLM은 명령 문자열을 절대 전달하지 못한다.
- `subprocess`는 **`shell=False` + argv 리스트**(셸 메타문자 해석 불가). 인자는 정규식 화이트리스트(hostname·unit명·`--since` 타임스탬프만).
- 모든 호스트 읽기 **`timeout nice -n19 ionice -c3` 래핑**(D-state 태스크 블록·부하 방지, Plan 51 §9).
- **변이 명령 물리적 부재**: `renice`/`kill`/`dmesg -C`/`systemctl restart`/`fsck`는 서버 코드에 **존재하지 않는다**(테스트로 고정). 조치는 Plan 64 §8 권고 경로로만.
- 최소권한 비-root + 명령별 sudoers 한정(Red Hat sudoers allowlist·agentless SSH 관행).
- 결과는 `src/security/data_masker.py` + `mask_args` 통과 후에만 반환(`/proc/PID/environ`·코어덤프 기본 미수집).

### 5.2 에이전트측 배선 (코드베이스 실측 매핑)

| 신규/재사용 | 위치 | 역할 |
|---|---|---|
| 신규 인프라 어댑터 | `src/alarm/infrastructure/host_diagnostic_collector.py`(Plan 64 §14 명명) / `src/diagnosis/infrastructure/sources/host_probe_source.py`(Plan 51 §7.1) | relay/osquery 호출. **`src/dbhub/client.py`의 MCP SSE 클라이언트 구조를 미러링** |
| 재사용 패턴 | `src/alarm/infrastructure/polestar_process_api.py` | 읽기전용·인증분리·base_url 고정·graceful None 반환 — 그대로 계승 |
| 소비 노드 | Plan 50 `evidence_collector`(`asyncio.gather` 부분실패 허용) | 고정 플레이북(kind별)으로 osquery+relay 호출, 실패 시 L1 폴백 |
| 설정 | `src/config.py` 신규 플래그 | `l3_host_collection_enabled=False`·`l3_host_access_mode`(A/B/C)·허용목록·타임아웃(전부 기본 off) |

---

## 6. 명령별 최종 매핑 (제안)

| 트리아지 | 명령 | 권고 채널 | 근거 |
|---|---|---|---|
| ① 부하 | `uptime`, `top -bn1` | A(폴스타 스냅) → B relay | Netflix 60초 진입 2단계 |
| ② 병목 CPU | `vmstat 1 3`(r·si/so·wa), `mpstat -P ALL` | **B relay**(순간 delta, osquery 불가) | USE Saturation |
| ② 병목 MEM | `free -m`, `/proc/meminfo` | **osquery `memory_info`** | osquery 완전 커버 |
| ② 병목 IO | `iostat -xz`, `df -i` | B relay(+osquery `mounts`) | await·inode |
| ③ 프로세스 | Top CPU/Mem 프로세스 | **osquery `processes`** + 폴스타 프로세스 API(L1) | 이중 확인 |
| ③ 소켓 | `ss -s`, listen backlog | **osquery `listening_ports`/`process_open_sockets`** | osquery 완전 커버 |
| ④ 로그 | `journalctl -p err --since`, `dmesg` | **C(로그전송) → B relay** | OOM/FS RO 시그니처(Plan 51 부록 A.1) |

---

## 7. HolmesGPT 심층 분석 및 활용 방안

> 사용자 요청: "(HolmesGPT/CNCF)를 더 분석하여 활용하는 방안을 상세하게 정리하라." — 아래는 HolmesGPT의 아키텍처를 해부하고, collectorinfra의 **폐쇄망·tool-calling 비의존·결정적 우선** 제약 하에서 **무엇을 어떻게 활용할지**를 3단계(패턴 차용 / MCP 공유 / 엔진 채택)로 상세화한다.

### 7.1 HolmesGPT란

- **정체**: Robusta가 개발한 오픈소스 **AI SRE 에이전트**. 경보 발생 시 read-only 관측 도구를 자동 실행해 근본원인을 조사·서술한다. **CNCF Sandbox 프로젝트**(2026-01 편입), **Apache 2.0 라이선스**.
- **커버리지**: Kubernetes·VM·클라우드·DB·SaaS 등 인프라 무관. 모든 주요 관측 벤더와 read-only 연동.
- **핵심 안전장치**: 전 툴셋 read-only, 플랫폼 권한(K8s RBAC·Grafana 역할·클라우드 IAM) 준수, **모든 tool call 전수 감사 로깅**.
- **왜 우리에게 중요한가**: collectorinfra가 Plan 64에서 하려는 것(경보→read-only 조사→원인 서술)을 **이미 프로덕션 수준으로 구현한 검증된 레퍼런스**다. 다만 오케스트레이션 철학이 다르다(§7.6).

### 7.2 아키텍처 해부 (DeepWiki 실측)

```
경보/질의 → ToolCallingLLM(agentic loop)
              │  ① LLM에 tool 정의 + 현재 컨텍스트 전달
              │  ② LLM이 호출할 tool 선택(tool_calls)
              │  ③ ToolExecutor 실행 ── 서버측 필터링·JSON 트리 순회
              │  ④ 결과를 대화 이력에 append(컨텍스트 윈도우 예산 관리)
              └─ ⑤ LLM이 최종 답 낼 때까지 ①~④ 반복
```

- **`ToolCallingLLM`**: agentic loop 관리 클래스. LLM이 누적 증거를 보고 **다음 도구를 스스로 선택**해 점진적으로 이해를 정련.
- **`ToolExecutor` 안전장치**(우리가 직접 이식할 가치가 큼):
  - `TOOL_MEMORY_LIMIT_MB` — 대용량 출력 OOM 방지
  - `TOOL_MAX_ALLOCATED_CONTEXT_WINDOW_PCT` — 도구 응답 크기를 컨텍스트 예산으로 제한(오버플로 차단)
  - **call limiting** — 과도·반복 호출 방지(무한 루프 방어)
- **`ToolsetManager`**: 툴셋을 계층 관리.

### 7.3 툴셋 시스템 5종 + YAML 스키마

| 유형 | 용도 |
|---|---|
| Built-in Python | 복잡한 통합 로직 하드코딩(K8s 쿼리 등) |
| **YAML 툴셋** | 설정만으로 빠른 확장(커스텀 HTTP·명령) |
| HTTP 커넥터 | REST API를 동적 도구화 |
| **MCP 서버** | Model Context Protocol 연동(stdio/SSE/HTTP) |
| Database | 헬스체크용 직접 SQL |

**YAML 툴셋 스키마(실측 예제)** — 이것이 collectorinfra 허용목록 설계에 직접 이식 가능한 핵심:

```yaml
toolsets:
- name: "switch_clusters"
  tools:
  - name: "switch_cluster"
    description: "Used to switch between multiple kubernetes contexts(clusters)"
    command: "kubectl config use-context {{ cluster_name }}"   # Jinja2 템플릿 변수
```

> **결정적 안전 원칙(HolmesGPT 문서 원문)**: *"The LLM can only control parameters that you expose as template variables"* — **LLM은 개발자가 `{{ }}`로 노출한 파라미터만 제어**하고 명령 골격은 못 바꾼다. 이는 §5.1의 "`command_id`→고정 argv + 인자만 화이트리스트"와 **정확히 동일한 안전 모델**이다.

### 7.4 read-only 강제 메커니즘

- **인프라 무변경 보장**: "HolmesGPT never modifies your infrastructure, respects RBAC, only reads data" — 프로덕션 안전.
- **Bash 툴셋의 read-only 자동허용**: `cat`·`head`·`tail`·`wc`·`jq` 등 read-only 명령은 **저장 디렉토리에 한해 승인 프롬프트 없이 자동 허용**, 그 외 명령은 승인 게이트. → **허용목록 기반 + 승인 게이트** 패턴이 우리 §5.1·Plan 51 §9와 정합.
- **전수 감사**: 모든 tool call 로깅 → Plan 64 §7.2 감사 요건과 정합.

### 7.5 런북(Runbooks) — 조사 절차 인코딩

- **정의**: 알려진 경보 패턴에 대해 **조사 절차를 코드화**. 매칭 경보 발생 시 HolmesGPT가 자체 조사와 **함께 런북 지시를 따른다**.
- **매핑**: 이것이 **Plan 51 §6 "장애 유형별 진단 플레이북"(트리거→필요증거→분석기법→LLM활용)** 및 Plan 64 §4(트리아지 단계별 매핑)와 개념적으로 동일하다. HolmesGPT 런북은 collectorinfra 플레이북의 **표현 형식 레퍼런스**로 쓸 수 있다.

### 7.6 collectorinfra와의 근본 긴장 (정직한 진단)

| 축 | HolmesGPT | collectorinfra(Plan 51/64) | 긴장 |
|---|---|---|---|
| 오케스트레이션 | **agentic loop**(LLM이 도구 선택) | **고정 LangGraph + 결정적 플레이북** | ★ 철학 충돌 |
| LLM 요구 | **function/tool-calling 필수**(LiteLLM 경유) | 워커 LLM(FabriX/KBGenAIChat) — tool-calling 미보장 | ★ 채택 블로커 |
| 네트워크 | 클라우드·인터넷 전제 다수 | **폐쇄망·외부 SaaS 금지** | △ (Ollama 로컬로 완화 가능) |
| 판정 주체 | LLM이 원인 서술·판정 | **결정적 규칙이 판정(D-035)**, LLM은 서술만 | ★ |

→ **HolmesGPT를 그대로 도입하는 것은 "결정적 우선·tool-calling 비의존" 원칙과 정면 충돌**한다. 따라서 활용은 **선별적**이어야 하며, 아래 3단계로 정리한다.

### 7.7 활용 방안 3단계

#### 활용 A — 패턴·코드 차용 〔권장·즉시·저위험〕

HolmesGPT는 Apache 2.0이므로 **설계·코드를 합법적으로 참조·이식**할 수 있다. 오케스트레이션 철학은 버리고, **검증된 부품만** 가져온다.

1. **YAML 툴셋 스키마 → collectorinfra 허용목록 정의 형식**: `{{ }}` 템플릿-변수-전용 안전 모델(§7.3)을 `host_diagnostic_collector`의 허용목록 스키마로 채택. 명령 골격은 고정, 인자만 노출.
2. **`ToolExecutor` 안전장치 이식**(§7.2): 출력 메모리 상한·컨텍스트 예산·call limiting을 relay 서버와 evidence_collector에 그대로 반영(대용량 `journalctl`/`dmesg` 출력 방어에 직접 유효 — Plan 51 §9의 "결과크기 감사"·Plan 64 §3.2 "전체 타임아웃"과 결합).
3. **Bash 툴셋 read-only 허용목록 파싱 로직**: 명령 검증·승인 게이트 구현 참조(§5.1 정규식 화이트리스트의 검증된 구현체).
4. **런북 스키마 → Plan 51 §6 플레이북 표현 형식**(§7.5).
5. **구조화 출력·인용**: Pydantic 기반 결과 직렬화·대화이력-도구결과 상관을 Plan 64 §6 브리핑 "인용 의무" 구현 레퍼런스로.

- **코드베이스 매핑**: `host_diagnostic_collector.py`(허용목록 스키마·ToolExecutor 가드), `investigation_graph` 브리핑 노드(Pydantic 출력).
- **리스크**: 낮음(패턴 차용, 런타임 의존성 0). **폐쇄망·D-035 무손상**.

#### 활용 B — read-only OS 진단 MCP 서버 공유 〔중기·미래보장〕

§5의 옵션 B를 **MCP 서버**로 구현하면, **collectorinfra의 결정적 collector와 HolmesGPT가 동일 서버를 소비**할 수 있다(HolmesGPT는 MCP stdio/SSE/HTTP 연동 지원). 즉 **진단 채널을 한 번 만들면 두 오케스트레이션이 공유**한다.

- **효과**: 지금은 collectorinfra 결정적 경로로 쓰고, 훗날 활용 C(엔진 채택)를 실험하더라도 **동일 read-only MCP 서버를 재사용** → 투자 보호.
- **전제**: MCP 서버는 반드시 **허용목록·no-shell·자격증명 서버측**(§5.1) — HolmesGPT가 소비하더라도 서버가 안전 경계를 강제하므로 agentic loop도 임의 명령을 못 낸다.
- **리스크**: 중(신규 MCP 서버 운영). B-1 보안결정 하에서만.

#### 활용 C — HolmesGPT를 격리된 L3 조사 엔진으로 채택 〔실험·조건부·병렬 경로〕

`investigation_graph`의 evidence_collector 뒤에 **HolmesGPT를 옵트인 병렬 경로**로 붙여, 결정적 조사가 불충분할 때 심층 조사에 활용.

- **채택 전제(모두 충족 시에만)**:
  1. **tool-calling 가능 LLM을 폐쇄망에 확보** — HolmesGPT는 LiteLLM 경유 **Ollama 로컬 모델** 지원. 폐쇄망 GPU에 function-calling 지원 모델(예: 로컬 Llama/Qwen 계열) 배포 협의 필요.
  2. **read-only 툴셋만 등록**(활용 B의 MCP 서버) — 임의 Bash·인터넷 검색 툴셋 비활성.
  3. **결과는 상향 전용·권고만**(Plan 64 §5·§8) — HolmesGPT 서술이 결정적 판정을 **소급 변경 못 함**.
- **리스크**: 높음(폐쇄망 tool-calling LLM 운영·비결정성·D-035 긴장). **1차 범위 밖**, 로컬 LLM 확보 후 실험 경로로만.

### 7.8 활용 방안 요약 결론

| 활용 | 시점 | 무엇을 | 리스크 | 원칙 정합 |
|---|---|---|---|---|
| **A 패턴 차용** | 즉시 | YAML 툴셋 스키마·ToolExecutor 가드·런북·Pydantic 인용 | 낮음 | ✅ 무손상 |
| **B MCP 공유** | 중기(B-1 후) | read-only 진단 MCP 서버를 collector·HolmesGPT 공용 | 중 | ✅ |
| **C 엔진 채택** | 실험(로컬 LLM 후) | HolmesGPT를 격리 L3 심층 조사기로 | 높음 | △ (옵트인·상향전용 격리 시) |

**권고**: **A를 즉시 채택**(설계·코드 레퍼런스로 Plan 64 구현 품질↑, 의존성 0)하고, **B는 옵션 B 착수 시 MCP 형태로 구현**해 미래 보장하며, **C는 폐쇄망 tool-calling LLM이 확보될 때 실험 경로**로 검토한다. HolmesGPT의 핵심 교훈 — **"read-only 툴셋 + 템플릿-변수-전용 파라미터 + 전수 감사 + 승인 게이트"** — 는 오케스트레이션 철학과 무관하게 collectorinfra의 L3 설계에 그대로 유효하다.

---

## 8. 단계적 착수 + 사용자·보안팀 결정 필요 (설치 제약 §4A 반영)

- **W-A(즉시, 결정·설치 불요)**: 채널0·1(**node_exporter + 폴스타, 이미 설치됨**)로 ①부하·②USE병목분해·③프로세스격리 + E3 baseline 구현 → 중요도 2차·브리핑 MVP. **활용 A(HolmesGPT 패턴 차용)** 병행. 호스트 신규 접근·설치 없음.
  - 선결(설치 아님): node_exporter `--collector.systemd` 활성화, Prometheus TSDB 조회 경로 배선, `host_diagnostic_collector`가 PromQL HTTP API를 read-only GET(폴스타 프로세스 API 패턴)으로 호출.
- **W-B(벤더·설정 협의, 신규 에이전트 0)**: 채널2 — **폴스타 LogMonitor 규칙 확장(A)** + **rsyslog/journald 중앙 전송(C)** 로 ④ 로그 포렌식 확보. per-host 설치 없음.
- **W-C(최후·B-1 보안결정 후)**: 채널3 — 관제규칙 밖 커널 원문·per-process 정밀만을 위한 **B relay / process-exporter / osquery 신규 설치**. §5·§7(활용 B·C)는 이 단계에만 적용.
- **결정 필요(착수 전 사용자·보안팀 확인)**:
  1. **Prometheus TSDB 조회 접근** — 에이전트가 PromQL API를 조회할 경로·권한(baseline 이력 필수)
  2. **폴스타 LogMonitor 규칙 확장(A)** 벤더 협의 가능성 (OOM/segfault/FS-RO 시그니처 관제 추가)
  3. **rsyslog/journald 중앙 전송(C)** 중앙 수집서버 1대 신설 의향 (호스트 설치 아님)
  4. **(채널3, B-1)** 신규 수집기(B relay/process-exporter/osquery) **설치 자체가 어렵다는 제약** 하에서 잔여 갭을 감수할지 vs 최소 설치를 협의할지
  5. **(활용 C 검토 시)** 폐쇄망 tool-calling 로컬 LLM(Ollama) 확보 가능성

확정 시 Plan 64 **D-102**(L3 실호스트 read-only 수집기 + 보안통제)로 `docs/02_decision.md`에 등재(번호 재확인). **설치 제약상 채널3(신규 설치)은 실질 보류 가능성이 높으므로, D-102 등재 시 "채널0~2로 커버되는 범위 + 잔여 갭의 명시적 수용"을 결정 본문에 포함**한다.

---

## 9. 테스트 환경 구성 계획 (트랙1 — Docker 기반 Linux node_exporter)

> §4A "채널0(node_exporter)"을 **프로덕션 접근 0으로 실측 검증**하는 로컬 환경. macOS 네이티브는 darwin collector 제한(`/proc` 부재 → `node_vmstat_*`·`node_procs_running`·`node_filefd_*`·`node_systemd_*` 부재)으로 **신호 의미론 신뢰 불가 → 제외**하고, 프로덕션(Linux)과 동형인 **Docker Linux 컨테이너**로 구성한다(CLAUDE.md "실측 우선·추정 금지" — mock 아닌 실 신호로 어댑터를 개발·검증).

### 9.1 목적·성공 기준

- **목적**: (a) 스크레이프→저장→PromQL→**에이전트 어댑터(`host_diagnostic_collector` 채널0 변형)** 배선, (b) USE 분해(us/sy/wa/steal·swap si/so·inode)가 실제 산출되는지, (c) 부하 시나리오에서 병목분류·baseline 이탈(E3)이 관측되는지.
- **성공 기준**: §9.7 검증 체크리스트 전 항목 PASS.

### 9.2 사전 요건

- Docker Desktop(macOS) 설치·기동 → verify: `docker version` 정상 출력.
- Docker Desktop은 Linux VM 위에서 돌므로 `/proc`·`/sys`는 **VM 기준**(맥 호스트 아님). 실 Linux 메트릭 이름·부하 관측엔 충분하다.

### 9.3 파일 구성 — `testenv/node_exporter/`

`docker-compose.yml`:
```yaml
services:
  node-exporter:
    image: prom/node-exporter:latest
    command:
      - '--path.procfs=/host/proc'
      - '--path.sysfs=/host/sys'
      - '--path.rootfs=/rootfs'
      - '--collector.textfile.directory=/textfile'   # ④ dmesg 시그니처 카운트 실험용
    volumes:
      - /proc:/host/proc:ro
      - /sys:/host/sys:ro
      - /:/rootfs:ro
      - ./textfile:/textfile:ro
    ports: ["9100:9100"]
  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
    ports: ["9090:9090"]
  stress:                                            # 부하 생성기
    image: alpine:latest
    command: sh -c "apk add --no-cache stress-ng && sleep infinity"
```

`prometheus.yml`:
```yaml
global:
  scrape_interval: 15s
scrape_configs:
  - job_name: node
    static_configs:
      - targets: ['node-exporter:9100']
```

### 9.4 실행 순서 (단계별 verify 게이트)

```
1. 기동      docker compose -f testenv/node_exporter/up -d
   → verify: docker ps 3컨테이너 Up · curl -s localhost:9100/metrics | head 200 OK
2. 스크레이프 확인
   → verify: localhost:9090 → PromQL `up{job="node"}` == 1
3. 정상구간 baseline 축적 (수십 분~수 시간 방치)
   → verify: query_range 가 연속 시계열 반환(E3 적합용 이력)
4. 부하 주입 (§9.5 stress-ng)
   → verify: 해당 USE 메트릭이 부하 구간에 상승(§9.6 PromQL)
5. 어댑터 조회 (§9.6 채널0)
   → verify: read-only GET 으로 query/query_range 값 수신
6. 정리      docker compose ... down -v
   → verify: 컨테이너·볼륨 제거
```

### 9.5 부하·이상 시나리오 (stress-ng — USE 분해·baseline 실측)

```bash
C=$(docker ps --filter name=stress --format '{{.Names}}')   # 실제 컨테이너명
# ② CPU 포화(us↑):          docker exec $C stress-ng --cpu 4 --timeout 120s
# ② 메모리 압박(swap si/so):  docker exec $C stress-ng --vm 2 --vm-bytes 1G --timeout 120s
# ② IO 부하(disk):          docker exec $C stress-ng --hdd 2 --timeout 120s
```

### 9.6 검증 PromQL + 채널0 어댑터 연동

각 트리아지 신호가 실제로 나오는지:
```promql
sum by (mode) (rate(node_cpu_seconds_total[1m]))              # ② CPU us/sy/wa/steal 분해
node_procs_running                                            # ① run-queue(=vmstat r)
rate(node_vmstat_pswpin[1m]) + rate(node_vmstat_pswpout[1m])  # ② swap si/so
node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes   # ② 메모리 여유
node_filesystem_files_free                                    # ② inode(df -i)
rate(node_network_receive_drop_total[1m])                     # ② NW drop
```

- **어댑터**: `host_diagnostic_collector`(채널0)가 **Prometheus HTTP API를 read-only GET**으로 조회 — `GET localhost:9090/api/v1/query?query=...`(**폴스타 프로세스 API의 read-only GET 패턴 재사용**, §5.2).
- **baseline(E3)**: `api/v1/query_range`로 과거 시계열 → `src/alarm/domain/anomaly.py`(Plan 60 §5.2 순수 Python Holt-Winters) 적합 → stress 스파이크로 이상탐지 트리거 확인. 짧은 이력은 합성 사인+노이즈 단위테스트로 보완(Plan 60 §5.3).
- **읽기전용 검증(D-003)**: 어댑터에 쓰기·remote-write 경로가 **부재**함을 테스트로 고정.

**구현 완료 (2026-07-21) — node_exporter 폴백 어댑터 스켈레톤** (기본은 폴스타 API `polestar_es_api.py`·폴스타 REST·직접 ES 아님·미착수, §4A.4):
- `src/alarm/infrastructure/prometheus_client.py` — `PrometheusClient`(httpx 읽기전용 GET, `polestar_process_api.py` 패턴 복제). 메서드: `query(db_id, promql)`·`query_range(db_id, promql, start, end, step)`·`query_scalar(...)→float`·`query_series(...)→list[float]`(Holt-Winters 입력). 비200/오류/`status!=success` → None(graceful).
- `src/config.py::AlarmConfig` — `prometheus_enabled`(기본 off)·`prometheus_base_urls_csv`(db_id→URL, 존별 분리)·`prometheus_timeout_seconds` + `get_prometheus_base_url(db_id)`(프로세스 API와 동일 CSV 파싱).
- `tests/test_alarm/test_prometheus_client.py` — 10 케이스(미매핑·인코딩·스칼라/시계열 파싱·비200·status≠success·네트워크오류·읽기전용 메서드 부재). **전 통과**, `arch_check --ci` 계층 위반 0.
- 소비 배선(다음 단계): `alarm_context_enricher`의 `asyncio.gather` 4번째 코루틴 → `query_series`로 baseline 조회 → `AlarmState.anomaly_severity`(Plan 60 §5.2). hostname→Prometheus 라벨 매핑·존별 URL은 §9 관문 2개(relabel `hostname` 라벨 / db_id CSV).

### 9.7 검증 체크리스트

- [ ] `/metrics`에 USE 분해 메트릭(`node_cpu_seconds_total{mode}`·`node_vmstat_pswpin`·`node_filesystem_files_free`) 존재
- [ ] Prometheus 스크레이프·저장 정상(`up{job="node"}==1`)
- [ ] PromQL로 ①②(부하·병목) 신호 조회 성공, stress 부하 시 값 변동 관측
- [ ] 에이전트 어댑터가 read-only GET으로 query/query_range 조회, 쓰기 경로 부재
- [ ] baseline 이력 축적 → Holt-Winters 적합 → 스파이크 이상탐지 트리거

### 9.8 트랙1 범위 밖 (별도 처리)

- **④ 로그(journalctl/dmesg/OOM)**: 컨테이너 재현 제한 → 로그 **fixture(시그니처 문자열)로 파서 테스트**(Plan 51 부록 A.1). 실 OOM 유발은 호스트 영향 위험 → **지양**.
- **systemd collector(재시작 루프 ③)**: 컨테이너에 systemd/dbus 없음 → `--collector.systemd` 무효. 검증 필요 시 **systemd 포함 Linux VM(multipass/UTM)** 에서 별도.
- 산출물 위치: `testenv/node_exporter/`(compose·prometheus.yml·검증 스크립트). 임시 실행물은 `$CLAUDE_JOB_DIR/tmp`.

---

## 10. 근거 자료 (Sources)

**학술·엔지니어링 문헌**
- RCACopilot — *Automatic Root Cause Analysis via LLMs for Cloud Incidents*, EuroSys 2024, arXiv 2305.15778 (2단계: 결정적 수집→LLM, MS 4년+·정확도 0.766)
- USE Method / "Linux Performance in 60,000ms" — Brendan Gregg / Netflix (Plan 51 §3 근거)
- Google SRE Book — Effective Troubleshooting / Monitoring (Golden Signals·변경기반 RCA)

**오픈소스·기술자료**
- HolmesGPT — CNCF Sandbox / Robusta, Apache 2.0: [CNCF 블로그](https://www.cncf.io/blog/2026/01/07/holmesgpt-agentic-troubleshooting-built-for-the-cloud-native-era/) · [GitHub](https://github.com/HolmesGPT/holmesgpt) · [DeepWiki 아키텍처](https://deepwiki.com/robusta-dev/holmesgpt) · [커스텀 툴셋 YAML](https://github.com/robusta-dev/holmesgpt-community-toolsets/blob/master/custom-toolset.yaml) · [LiteLLM 연동(로컬 LLM)](https://docs.litellm.ai/docs/projects/HolmesGPT)
- osquery — Meta, OS를 관계형 DB로: [공식 문서](https://osquery.readthedocs.io/) · [엔드포인트 라이브 모니터링](https://www.query.ai/resources/blogs/how-to-monitor-endpoints-live-with-osquery/) · [CrowdStrike 개요](https://www.crowdstrike.com/en-us/cybersecurity-101/it-automation/osquery/)

**보안 근거**
- MCP 보안: [MCP Security Best Practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices) · [NSA MCP CSI](https://www.nsa.gov/Portals/75/documents/Cybersecurity/CSI_MCP_SECURITY.pdf) · [eSentire — CVE-2025-6514 mcp-remote RCE](https://www.esentire.com/blog/model-context-protocol-security-critical-vulnerabilities-every-ciso-should-address-in-2025)
- 반면교사(임의 셸 노출 MCP): [tufantunc/ssh-mcp](https://github.com/tufantunc/ssh-mcp)
- 명령 인젝션 방지: [Semgrep](https://semgrep.dev/docs/cheat-sheets/python-command-injection) · [Snyk](https://snyk.io/blog/command-injection-python-prevention-examples/)
- 최소권한 sudoers·agentless: [Red Hat sudoers](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/10/html/security_hardening/managing-sudo-access) · [Lansweeper agentless Linux](https://community.lansweeper.com/t5/requirements/linux-and-unix-agentless-scanning-requirements/ta-p/64378)

**프로젝트 내부**
- `plans/64-automated-incident-investigation-and-response.md`(§7 L3·§14 산출물), `plans/51-fault-diagnosis-data-collection.md`(§5.3·§9·부록 A), `plans/60-noise-cancellation-benchmark-refinement.md`(§14 트리거), `plans/50-fault-diagnosis-rca.md`
- 코드 실측: `src/dbhub/client.py`(MCP SSE 클라이언트 패턴), `src/alarm/infrastructure/polestar_process_api.py`(read-only GET·자격증명 분리)
- `docs/aiops_benchmark/incident_investigation_literature.md`(Plan 64 문헌 dossier)
