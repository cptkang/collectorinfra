# 51. 장애분석 데이터 수집 및 진단 기법 (OS-Level Evidence Collection & Diagnostic Techniques)

> 작성일: 2026-06-26
> **상위/자매 계획**: `plans/50-fault-diagnosis-rca.md` (진단 서브그래프·오케스트레이션 아키텍처).
> 본 계획은 그 중 **증거 수집(evidence_collector)** 과 **상관/인과 분석(correlation_engine·causal_reasoner)** 에
> 들어갈 **데이터 소스·수집 방법·장애분석 기법**을 정립한다.
> **관련 Plan**: 47(알람 이력), 47-1(실시간 프로세스 API), 44/46(알람 파이프라인)
> **관련 결정**: D-003(읽기전용), D-022/D-028(폴스타 조인·허용테이블), D-035/D-036(이력·프로세스) /
> 구현 착수 시 **D-039**로 등재 예정 (§11)
> **상태**: 계획 (미구현)

---

## 1. 개요 및 목표

### 1.1 배경

Plan 50은 "사건 구간 다중신호 상관 → 인과 추론 → 리포트"의 **진단 골격**을 정의했다. 본 계획은 그 골격에
실제로 흐를 **데이터**를 다룬다. 즉 "장애 진단·원인 분석을 위해 운영체제에서 **무엇을 추가로 수집해야 하는가**
(syslog 등 로그, 프로세스 현황·추이, 과거 데이터, 메모리 사용량, OS 오류 메시지 등), 그것을 **어떻게 수집하며**,
**어떤 분석 기법으로 진단에 활용하는가**"를 정립한다.

핵심 발견(코드·스키마 실측): **폴스타는 이미 OS 로그·프로세스를 "관제"하고 있으나, 본 에이전트는 그 자산의
일부(알람·메트릭·실시간 프로세스)만 사용하고 있다.** 따라서 진단 데이터는 세 계층으로 나뉜다 — (L1) 폴스타가
이미 보유하여 즉시 조회 가능한 것, (L2) 폴스타가 관제하지만 본 에이전트가 매핑/확장해야 하는 것, (L3) 폴스타에
없어 신규 수집 경로가 필요한 것(원시 syslog/dmesg/OOM 포렌식·프로세스 추이 등). 이 3계층 구분이 본 계획의
중심 축이며, 비용·보안 위험·구현 우선순위를 결정한다.

### 1.2 목표

1. **장애분석 기법(방법론)** 을 정리하여, LLM이 흉내가 아니라 **검증된 절차**로 진단하도록 한다(§3).
2. 장애 유형별로 **필요한 OS 진단 데이터 카탈로그**를 정의한다(§4).
3. 각 데이터의 **수집 가능성을 3계층으로 분석**하고, 신규 수집(L3)의 **보안·안전 설계 옵션**과 결정 사항을
   제시한다(§5, §9).
4. 장애 유형별 **진단 플레이북**(필요 증거 → 수집 계층 → 분석 기법 → LLM 활용)을 제시한다(§6).
5. Plan 50의 `evidence_collector`에 꽂히는 **수집기 아키텍처**와 LLM 그라운딩 통합을 설계한다(§7, §8).

### 1.3 설계 원칙 (Plan 50 계승 + 본 계획 추가)

- **읽기 전용·비침습 절대 원칙** — 모든 수집은 조회/읽기만. 호스트에 어떤 변경·재기동·부하도 가하지 않는다.
- **수치·상관은 결정적 Python, 인과 해석만 LLM** — Plan 47 §3.3, Plan 50 §3.3 계승.
- **검증된 기법으로 그라운딩** — USE Method·Four Golden Signals·타임라인·변경기반 RCA 등 정립된 절차를
  체크리스트로 변환하여 LLM 프롬프트에 주입(환각 대신 절차).
- **근거 인용 의무 + 신뢰도 + 사람 게이팅** — LLM-RCA 연구의 핵심 교훈(§3.4). 자동 조치는 사람 승인 후.
- **폴스타 우선, 신규 수집은 최소·옵트인** — 이미 있는 자산(L1)을 먼저 쓰고, L3(호스트 직접 수집)는
  보안 결정을 거쳐 옵트인으로만 도입.

### 1.4 추가 수집 데이터 및 분석 방법 (명세)

> "OS에서 무엇을 추가로 수집하고, 어떻게 분석하는가"의 한눈 요약. 상세는 §4(카탈로그)·§5(계층)·§6(플레이북)·부록 A.

| 계층 | 추가 수집 데이터 | 수집 방법 | 분석 방법 |
|------|-----------------|----------|----------|
| **L1** (폴스타 보유, 즉시) | 알람+**conditionLogText**(매칭 로그), 성능추이(`cmm_metric_stat`), 서브리소스 `avail_status`, 실시간 프로세스, OS설정(sysctl/커널버전) | DBHub **읽기전용 고정 SQL** + 프로세스 API (§5.1) | 결정적: **USE U/S/E 매핑**(부록 A.2)·이상탐지(baseline·z-score·지속성)·**시그니처 인식**(부록 A.1)·타임라인 |
| **L2** (매핑 필요) | `server.Netstat`/`Process`/`Other` 리소스, NW 성능지표, LogMonitor 본문 | 표본 조사 후 고정 SQL/엔드포인트 추가 (§5.2) | L1과 동일 분석에 편입(네트워크 USE·로그 시그니처) |
| **L3** (신규수집·옵트인) | 원시 syslog/journald/**dmesg/OOM**, 프로세스 추이, 메모리 분해(swap/cached/slab), `ss`/`iostat`/`df -i`, SMART | A 폴스타에이전트 확장 / C 로그전송 / B 허용목록 read-only 명령 (§5.3·§9) | 결정적: **시그니처 매칭**(부록 A.1)·USE·추이/이상탐지 → 타임라인 |

**분석 절차**: 위 데이터를 `evidence_collector`(소스 어댑터)가 동시 수집 → **결정적 엔진**이 USE 1차분류·
메트릭 이상탐지·로그 시그니처 룰·타임라인 정렬·선후 판정(§3.1~3.5, 부록 A) → **LLM `causal_reasoner`** 가
정립 기법 체크리스트(USE/골든시그널/변경기반/반증)와 **근거 인용·신뢰도(수집 계층·정밀도 연동)** 로 진단(§8).
수치·시그니처 매칭은 Python(결정적), 인과 해석만 LLM.

---

## 2. 현재 가용 데이터 실측 (코드·스키마 기준)

| 데이터 | 위치 | 현재 사용 | 비고 |
|--------|------|----------|------|
| 알람 이벤트 + conditionLogText | `cmm_alarm`(+`cmm_alarm_def`,`cmm_resource`) | O (Plan 47 이력) | `CONDITIONLOGTEXT`/conditions에 임계 초과·**매칭된 로그 내용**이 담김 |
| 성능 시계열 | `cmm_metric_stat_h/d/m` | O (조회 파이프라인) | CPU/메모리/파일시스템 `Utilization`, 디스크 `MaxIORate`. min/avg/max |
| 리소스 가용상태(서브리소스별) | `cmm_resource.avail_status` | △ (서버 단위만) | CPU/디스크/FS/NW/**LogMonitor/ProcessMonitor**별 0=정상/≠0=비정상 |
| 로그 관제 정의 | `cmm_resource` `server.LogMonitor` | ✕ (미조회) | "Syslog Monitor", "DB2진단로그", 보안로그(Secuve/Deep Security) 등 |
| 프로세스 관제 정의 | `cmm_resource` `server.ProcessMonitor` | ✕ (미조회) | "ntpd","DB2엔진","NC_NODEMANAGER" 등 — 생존 관제 |
| 네트워크 세션 | `cmm_resource` `server.Netstat` | ✕ (미조회) | 세부 값 위치 미확인(L2 조사) |
| 기타정보 | `cmm_resource` `server.Other` | ✕ (미조회) | IPCS, OS Table |
| 실행 프로세스 관제 | `cmm_resource` `server.Process` | ✕ (미조회) | 세부 값 위치 미확인(L2 조사) |
| OS 설정/커널 | `core_config_prop` EAV | O (사양 조회) | `OSParameter`(sysctl), `OSVerson`(커널), `PatchLevel`, `OSType` |
| 실시간 프로세스 Top | 폴스타 REST `/rest/server/process/listByhostname` | O (Plan 47-1) | **실시간 단면만**, top N, args 마스킹 |

> 출처: `schema/polestar-schema.md`(L9-97 리소스 타입·계층, L139-184 EAV), `schema/polestar-data.md`(L52-171
> LogMonitor/ProcessMonitor 실데이터), `config/db_profiles/polestar_cm_gp.yaml`(L210-257 메트릭, L493-514
> 허용 테이블), `src/alarm/infrastructure/polestar_process_api.py`, `src/alarm/infrastructure/polestar_history.py`.

**결론**: "OS 오류 메시지"의 일부(폴스타가 관제하는 syslog/보안/DB2 로그의 **매칭분**)와 성능 추이·프로세스
단면은 **이미 가용**하다. 그러나 **원시 전체 로그·dmesg/OOM 상세·프로세스 추이·메모리 분해(swap/cached)·
상세 netstat**은 폴스타 표준 스키마에 없어 L2 매핑 또는 L3 신규 수집이 필요하다.

---

## 3. 장애 분석 기법 (방법론)

LLM이 "그럴듯한 추측"이 아니라 **정립된 진단 절차**를 따르도록, 아래 기법을 체크리스트로 변환하여
`causal_reasoner` 프롬프트와 `correlation_engine` 규칙에 인코딩한다.

### 3.1 자원 중심 1차 분류 — USE Method (Brendan Gregg)

**"모든 자원에 대해 Utilization(사용률)·Saturation(포화/대기)·Errors(오류)를 점검한다."**
오류를 먼저(해석이 가장 빠름), 그다음 사용률, 그다음 포화. 사용률 100%=병목, ~70%부터 큐잉 시작,
**0이 아닌 포화·0이 아닌 오류는 곧 문제**.

| 자원 | Utilization | Saturation | Errors |
|------|-------------|-----------|--------|
| CPU | `vmstat`(us+sy+st), `sar -u`, `mpstat -P ALL` | `vmstat r`>코어수, `sar -q` runq-sz | `perf`(CPC) |
| 메모리 | `free -m`, `/proc/meminfo` | `vmstat si/so`(스왑), `sar -B`(pgscan) | `dmesg`(OOM/하드웨어) |
| 디스크 IO | `iostat -xz %util`, `sar -d` | `iostat avgqu-sz>1`, 높은 await | `smartctl`, `/sys ioerr_cnt`, dmesg FS 오류 |
| 디스크 용량 | `df -h` | ENOSPC(가득 참) | `df -i`(inode), strace ENOSPC |
| 네트워크 | `sar -n DEV`(rx/txKB) vs 한계, `ip -s link` | drop/overrun, `netstat -s` 재전송 | `ifconfig` errors, `netstat -i` RX/TX-ERR |
| 소프트웨어(FD/스레드) | `ls /proc/PID/fd\|wc -l` vs ulimit, `sar -v` file-nr | task capacity vs threads-max | EMFILE/ENFILE |

→ `correlation_engine`는 수집된 메트릭을 이 표의 U/S/E 슬롯에 결정적으로 매핑하여 "어느 자원이 병목인가"를
1차 분류한다. (출처: brendangregg.com/usemethod.html, USEmethod/use-linux.html)

### 3.2 신속 트리아지 — "Linux Performance in 60s" 10단계 (Netflix)

`uptime`(부하추세) → `dmesg | tail`(**OOM·드롭, 놓치지 말 것**) → `vmstat 1`(r·si/so·wa) →
`mpstat -P ALL`(단일 핫 CPU=단일스레드) → `pidstat 1`(프로세스별 CPU) → `iostat -xz 1`(await·avgqu-sz·%util) →
`free -m`(buffers/cache) → `sar -n DEV 1`(NIC) → `sar -n TCP,ETCP 1`(재전송) → `top`.
→ "사건 구간 스냅샷" 수집 항목의 **표준 목록**으로 채택(L1/L3 매핑은 §5).
(출처: brendangregg.com/Articles/Netflix_Linux_Perf_Analysis_60s.pdf)

### 3.3 서비스/사용자 관점 — Four Golden Signals / RED

- **Four Golden Signals**(Google SRE): Latency·Traffic·Errors·Saturation. 포화는 **선행 지표**.
  성공/실패 요청의 latency를 구분(빠른 오류가 문제를 가릴 수 있음).
- **RED**(Rate·Errors·Duration): 요청 구동 서비스에 적용. 배치/스트리밍엔 부적합.
- 운영 합의: **"자원엔 USE, 서비스엔 RED."** 본 진단은 인프라 중심이라 USE를 기본,
  서비스 영향 추정엔 골든시그널을 보조로 사용. (출처: sre.google/sre-book/monitoring-distributed-systems)

### 3.4 사건 재구성 — 타임라인 + 변경기반 RCA

- **타임라인**: 모든 신호(알람·메트릭 이상·로그 매칭·프로세스 상태변화)를 **기준 시각** 좌표로 시간순 정렬
  (now() 금지 — Plan 50 §3.4). 포스트모템의 기본 산출물.
- **변경기반 RCA("무엇이 바뀌었나")**: **SRE 통계상 장애의 ~70%가 라이브 변경에서 비롯**. 사건 직전 창의
  배포/구성/패치/부하 변화를 타임라인에 겹쳐 직전 변경을 1차 용의자로. → 변경 이벤트 증거(§4.7) 확보가
  인과력에 결정적. (출처: sre.google/sre-book/introduction)

### 3.5 다중 가설 — Differential Diagnosis(가설→반증→분할정복)

여러 원인이 가능할 때: 가설을 다수 세우고 **반증(falsify)** 하는 관찰/쿼리로 후보를 제거(분할정복으로
탐색공간 절반씩 축소). LLM은 "확인"이 아니라 "반증"하도록 유도(확증편향 차단).
→ Plan 50 동적 보강(추가 ad-hoc 조회)과 결합. (출처: sre.google/sre-book/effective-troubleshooting)

### 3.6 인과 분해 보조 — 5 Whys / Fishbone / FTA (용도별)

- **5 Whys**: 단일·선형 원인의 빠른 드릴다운. 한계: 증상에서 멈춤·조사자 지식 한정·재현성 낮음 → 단독 신뢰 금지.
- **Fishbone(6M)**: 원인 범주(사람/장비/방법/자재/측정/환경)를 넓게 열거(단일원인 편향 보정). 가설 생성용,
  데이터로 검증 필요.
- **FTA(결함수)**: AND/OR 게이트로 알려진 실패모드를 표현. **알려진 장애 패턴의 지식표현**으로 활용(상시
  트리아지엔 과중). → 운영 빈발 장애를 FTA 룰북으로 축적(Phase C).

### 3.7 가드레일 (반드시 인코딩)

- **상관 ≠ 인과**: 공통 원인(confounder)에 의한 동반 변동을 인과로 단정 금지. 동반 신호는 "후보"로만.
- **이상탐지**: baseline(직전 동시간대 N기간 μ/σ)·계절성 고려. z-score는 비정규 분포에서 취약 →
  지속성(연속 K구간) 규칙 병행. **지연(latency)은 평균이 아니라 백분위수(p95/p99)**.
- **알람은 증상 기준**: 원인 알람 다발(스톰)은 토폴로지 억제·중복제거로 묶어 1차 신호화.
  (출처: sre.google, Etsy Skyline, p99conf/Gil Tene)

### 3.8 LLM-RCA 적용 패턴 (연구 합의)

원시 정확도는 모델 크기보다 **그라운딩+검증+사람 게이팅**으로 향상된다(연구 합의).
- **2단계 파이프라인**(RCACopilot): 결정적 증거 수집 → LLM 예측. (본 프로젝트 Plan 50 구조와 동일)
- **증거만으로 답하라 + 인용 의무(glass box)**: 주장마다 로그줄/메트릭/커밋을 인용(30초 내 검증 가능).
- **명시적 검증기**: 증거충분성·시간순서·출처일관성 검증을 순위화 전에 수행(최대 레버리지).
- **읽기전용 스키마 제약 + 재시도 상한**: 스키마 환각·정체 루프 차단(본 프로젝트 검증 파이프라인과 정합).
- **순위 top-k + 신뢰도 + 위험기반 게이팅**: 제안은 사람 검증 전제로 노출, 자동조치는 고신뢰+사람승인.
- 참고 수치: ReAct형 도구사용은 정답시 환각<1%(RAG 26% 대비) — **도구 기반 조회가 환각을 크게 낮춤**.
  (출처: arXiv 2301.03797, 2305.15778(RCACopilot), 2401.13810, 2403.04123, 2309.05833(PACE-LM))

---

## 4. 진단 데이터 카탈로그 (장애 유형별)

각 항목: **무엇을 드러내는가 / 수집 소스(명령·파일·로그) / 형식**. 수집 계층(L1/L2/L3) 매핑은 §5.

### 4.1 시스템·커널 로그 (OS 오류 메시지)

| 데이터 | 드러내는 것 | 수집 소스 | 형식 |
|--------|------------|----------|------|
| syslog/rsyslog | 서비스·시스템 일반 오류 | `/var/log/messages`,`/var/log/syslog` (RFC5424 facility/severity) | 텍스트 라인 |
| systemd journal | 부팅 이후 구조화 로그 | `journalctl -p err --since`, `-k`(커널), `-u <unit>` | 텍스트/JSON |
| 커널 링버퍼 | 하드웨어·드라이버·FS 오류 | `dmesg -T`, `/dev/kmsg` | `[ts] msg` |
| **OOM Killer** | 메모리 고갈로 프로세스 강제종료 | dmesg/journal: `Out of memory: Killed process <pid> (<name>)`, `oom-kill:` , `oom_score` | 커널 라인 |
| 커널 패닉/소프트락업/hung task | 시스템 멈춤·D상태 | dmesg: `BUG:`,`soft lockup`,`hung_task`,call trace | 콜트레이스 |
| 인증/보안 로그 | 침입·권한 오류 | `/var/log/secure`,`/var/log/auth.log` | 텍스트 |
| 애플리케이션 로그 | 앱 예외·스택 | 앱별 경로(비표준) | 비정형 |

### 4.2 메모리 장애

`free -m`·`/proc/meminfo`(MemAvailable·Buffers·Cached·SwapFree·**Slab**·Committed_AS) ·
`vmstat`(si/so 스왑, 페이지스캔) · `sar -r/-B/-W` · OOM(§4.1) · `slabtop`/`/proc/slabinfo`(커널 슬랩 누수) ·
cgroup(`memory.stat`,`memory.max` — 컨테이너 한계) · 프로세스별(`pidstat -r`,`/proc/PID/status` VmRSS,
smaps RSS/PSS). **추이**(누수 판별)는 시계열 필요.

### 4.3 CPU 장애

`uptime`/`/proc/loadavg`·`vmstat r`(런큐=CPU 포화, 부하평균보다 정확) · `mpstat -P ALL`(단일 핫코어) ·
`pidstat -u`·`top`(`%us/%sy/%wa/%st/%si` — **iowait**=IO대기, **steal**=가상화 경합, **softirq**=인터럽트) ·
`sar -u/-q` · cgroup cpu 쓰로틀/서멀 쓰로틀.

### 4.4 디스크·파일시스템·IO 장애

`df -h`(용량)·**`df -i`(inode 고갈 — 용량 남아도 쓰기 실패)** · `iostat -xz`(await=앱 체감지연, %util,
avgqu-sz>1 포화) · `sar -d`·`iotop`/`pidstat -d`(프로세스별 IO) · `smartctl`(디스크 HW 수명/오류) ·
dmesg FS 오류·**read-only 리마운트** · `lsof +L1`(삭제됐지만 열린 파일이 용량 점유).

### 4.5 네트워크 장애

`ss -s`/`ss -tan`(연결 상태·**TIME_WAIT 폭증**·listen backlog) · `ip -s link`/`ifconfig`(errors/drops/overruns) ·
`sar -n DEV/EDEV/TCP,ETCP`(재전송·리셋) · `netstat -s` · **conntrack 테이블 고갈** · **임시포트(ephemeral) 고갈** ·
DNS 해소 지연 · `ping`/`mtr`(지연·손실)·MTU.

### 4.6 프로세스 장애

상태(R/S/**D**=무중단 슬립(보통 IO 멈춤)/**Z**=좀비) · `/proc/PID/`(status·wchan·stack·fd·limits) ·
**FD 고갈**(`ls /proc/PID/fd|wc -l` vs `ulimit -n`, EMFILE) · 프로세스 트리(`pstree`,ppid) · **재시작 루프/플래핑** ·
크래시 시그널(dmesg `segfault`) · 스레드 수 vs `threads-max`/cgroup `pids.max`.

### 4.7 변경/구성 이벤트 (§3.4 — 인과력 최상)

배포/릴리스 로그, 구성·피처플래그 변경, 인프라 변경, 트래픽 변화. 폴스타/연관 시스템(ITSM 변경관리 등)에
변경 이력 테이블이 있는지 **선조사 필요**(있다면 "무엇이 바뀌었나" RCA에 직접 연결).

---

## 5. 수집 가능성 3계층 분석 (이 프로젝트 기준)

§4 카탈로그를 본 프로젝트가 **실제로 얻을 수 있는 경로**로 매핑한다. 이것이 비용·보안·우선순위의 핵심.

### 5.1 L1 — 폴스타 기존 보유 (즉시 조회, 신규 수집 불필요)

기존 DBHub(읽기전용)·프로세스 API로 **지금 바로** 얻는다. Plan 50 증거수집의 기본 계층.

| 진단 데이터 | L1 소스 | 비고 |
|------------|--------|------|
| OS 오류 메시지(폴스타 관제분) | `cmm_alarm` + `server.LogMonitor` 알람의 `CONDITIONLOGTEXT` | Syslog/보안/DB2진단로그의 **매칭 라인**. 전체 syslog는 아님 |
| 프로세스 다운 | `server.ProcessMonitor` 알람·`avail_status` | ntpd/DB2엔진 등 관제 대상 한정 |
| 자원별 가용/이상 | 서브리소스 `avail_status`(CPU/디스크/FS/NW/Log/Process Monitor) | 어느 자원에서 시작됐는지 좁히기 |
| 성능 추이(과거 데이터) | `cmm_metric_stat_h/d/m`(CPU/Mem/FS Util, Disk MaxIORate) | USE의 Utilization 슬롯. 시/일/월 정밀도 |
| 알람 다발/패턴 | `cmm_alarm` 이력(Plan 47 통계) | 스톰·주기·급증 |
| 실시간 프로세스 Top | 프로세스 API(Plan 47-1) | **실시간 단면만**(과거 사건 부정합 — Plan 50 R-2) |
| OS 설정/커널 | `core_config_prop`(OSParameter/OSVerson/PatchLevel) | sysctl·커널버전(정적). 변경비교는 불가(스냅샷) |

**즉시 추가 작업**: LogMonitor/ProcessMonitor/서브리소스 avail_status를 조회하는 **고정 SQL 템플릿** 추가
(`polestar_cm_gp.yaml` 허용테이블에 이미 `cmm_resource` 포함 — 신규 권한 불필요).

### 5.2 L2 — 폴스타 관제하나 매핑/확장 필요

폴스타가 데이터를 가지나 본 에이전트가 위치를 모르거나 미사용. **선조사 후 고정 SQL/엔드포인트 추가**.

| 진단 데이터 | L2 작업 | 불확실성 |
|------------|--------|---------|
| 네트워크 세션 | `server.Netstat` 리소스의 값 저장 위치(`realtime_info`/별도 테이블) 조사 | 세부 컬럼 미문서화 |
| 실행 프로세스(관제) | `server.Process` 값 위치 조사 | 실시간 API와 중복/차이 확인 |
| 기타(IPCS/OS Table) | `server.Other` 내용 조사 | 용도 불명 |
| 네트워크 성능 메트릭 | metric_stat에 NW 처리량/오류 definition 존재 여부 | 프로필 미기재 |
| 폴스타 추가 REST API | 프로세스 API 외 로그/이벤트/이력 엔드포인트 존재 여부(벤더 문서) | 미확인 |
| conditionLogText 실내용 | LogMonitor 알람이 실제로 매칭 로그 본문을 담는지 표본 검증 | 추정 |

→ **벤더(폴스타) 협의 + 표본 쿼리**로 확정. L2가 채워지면 syslog 매칭·netstat·프로세스 관제가 강화된다.

### 5.3 L3 — 폴스타에 없어 신규 수집 경로 필요 (고비용·보안 결정)

원시 전체 로그·dmesg/OOM 상세·프로세스 추이·메모리 분해(swap/cached/slab)·상세 ss/iostat/df-i 등
**임의 OS 포렌식**은 폴스타 표준 스키마에 없다. 이를 얻으려면 **호스트에 닿는 신규 수집 경로**가 필요하며,
이는 운영 호스트 접근이라 **보안상 중대한 결정**이다(§9). 세 옵션:

| 옵션 | 방식 | 장점 | 단점/위험 |
|------|------|------|----------|
| **A. 폴스타 에이전트 확장** | 폴스타가 이미 호스트에 둔 에이전트로 추가 수집 항목(dmesg/free/ss 스냅샷)을 정의·노출 | 신규 접근경로 없음(에이전트 재사용), 읽기전용·검증된 채널 | 벤더 협의·제품 기능 의존, 커스텀 한계 |
| **B. 신규 read-only 진단 수집기** | on-demand로 호스트에서 **허용목록 명령만** 실행(에이전트리스 SSH 또는 경량 수집 에이전트), 결과를 에이전트로 반환 | 폴스타 미보유 데이터까지 임의 수집 | **운영 호스트 접근·자격증명 관리·명령 안전성** 큰 위험. 강력한 통제 필수(§9) |
| **C. 로그 전송 파이프라인** | 호스트 로그를 중앙(rsyslog/syslog-ng 전달, Filebeat/Fluentd→저장소)으로 상시 수집 후 조회 | 과거 로그 포렌식 가능(연속 수집), 호스트 직접접근 불필요(전달형) | 저장소·보존정책·인프라 신설, 도입 전 사건 공백 |

**권고**: 1순위 **A(폴스타 에이전트 확장)** — 신규 접근경로 없이 가장 안전. 불가 항목에 한해
2순위 **C(로그 전송)** — 과거 로그가 필요한 포렌식에 적합. **B(직접 명령 실행)** 는 최후수단이며 §9의
통제(허용목록·읽기전용·최소권한·감사·마스킹) 전제 하에서만, 그리고 **사용자 명시 승인** 후 도입한다.

### 5.4 데이터 × 계층 매핑 요약

| 사용자가 언급한 항목 | 계층 | 경로 |
|---------------------|------|------|
| syslog(원시 전체) | L2(매칭분) → L3(전체) | LogMonitor 알람(L1/L2) + 전체는 C 로그전송 |
| OS 오류 메시지 | L1(관제분) → L3 | conditionLogText + dmesg는 A/C |
| 프로세스 현황 | L1(실시간 Top) + L2(관제) | 프로세스 API + ProcessMonitor/Process |
| 프로세스 추이(과거) | L3 | 폴스타 미보유 → A(스냅 적재)/C |
| 과거 데이터(성능) | L1 | metric_stat h/d/m |
| 메모리 사용량(Util%) | L1 | metric_stat. 분해(swap/cached)는 L3 |
| OOM/커널 상세 | L3(또는 L1 if 폴스타 syslog 관제가 OOM 포함) | dmesg → A/C |

---

## 6. 장애 유형별 진단 플레이북

각 유형: **트리거 → 필요 증거(수집 계층) → 분석 기법 → LLM 활용**. `correlation_engine`이 결정적으로
증거를 정렬·판정하고, `causal_reasoner`가 인용·신뢰도와 함께 가설을 서술한다.

### 6.1 CPU 포화

- 증거: metric_stat CPU Util 추이(L1) · 알람 타임라인(L1) · 실시간 Top CPU 프로세스(L1) ·
  (가능시) iowait/steal 분해·런큐(L3) · 변경 이벤트(L2/L3).
- 기법: USE(CPU U/S/E) → iowait↑면 디스크로 드릴다운(§6.3), steal↑면 가상화 경합, 단일핫코어면 단일스레드 앱.
  타임라인으로 메트릭 선행 여부 판정.
- LLM: "Top 프로세스 X가 CPU Util 선행 상승과 일치 → 유력 원인(신뢰도 medium, 프로세스는 현재 단면)".

### 6.2 메모리 고갈 / OOM

- 증거: metric_stat Mem Util 추이(L1) · OOM 로그(L3: dmesg `Out of memory: Killed process`) 또는 폴스타
  syslog 관제 알람(L1) · 실시간 Top Mem(L1) · swap/slab 분해·누수 추이(L3).
- 기법: vmstat si/so 스왑·OOM 시그니처가 결정적. 추이로 **누수 vs 스파이크** 구분(지속 증가=누수).
- LLM: OOM 라인의 killed process + Mem 추이 인용. swap 분해 없으면 한계 명시.

### 6.3 디스크 풀 / inode / IO 지연

- 증거: metric_stat FS Util·Disk MaxIORate(L1) · `df -i` inode(L3) · iostat await/%util(L3) ·
  삭제된 열린 파일(L3 `lsof +L1`) · FS read-only 리마운트(L3 dmesg) · FS 알람(L1).
- 기법: 용량 100%인데 쓰기실패면 **inode 고갈** 의심. await↑+%util↑=IO 포화. RO 리마운트=FS 손상 신호.
- LLM: "FS Util 100% + df -i 99% → inode 고갈" 처럼 두 증거 결합. iostat 없으면 IO포화 단정 보류.

### 6.4 네트워크 이상

- 증거: NW 인터페이스 errors/drops(L2/L3) · ss 상태·재전송(L3) · conntrack/ephemeral 고갈(L3) ·
  Netstat 리소스(L2) · NW 알람(L1).
- 기법: USE(NW U/S/E). 재전송↑=네트워크/원격 문제, TIME_WAIT 폭증=커넥션 과다, conntrack 고갈=신규연결 실패.
- LLM: 손실/재전송 인용. 대부분 L2/L3 의존 → L1만으론 신뢰도 제한 명시.

### 6.5 프로세스 다운 / 플래핑

- 증거: ProcessMonitor avail_status·알람(L1) · 실시간 프로세스 존재(L1) · 크래시 시그널(L3 dmesg segfault) ·
  FD/스레드 한계(L3) · 변경 이벤트(L2/L3).
- 기법: avail_status 변화 타임라인 → 재시작 루프(짧은 간격 반복) 판정. segfault/FD고갈로 원인 분기.
- LLM: "ProcessMonitor ntpd 14:02 다운, 직전 배포 13:58 → 변경기반 용의(신뢰도 변경이벤트 확인 시 상승)".

### 6.6 로그 패턴 오류 (보안/앱/DB)

- 증거: LogMonitor 알람 conditionLogText(L1) · 전체 로그 컨텍스트(L3) · 동시간대 타 신호(L1).
- 기법: 매칭 로그줄을 타임라인에 배치, 동반 자원 이상과 상관. 보안로그면 인증로그(L3)와 교차.
- LLM: 매칭 로그줄 그대로 인용(환각 금지). 전체 컨텍스트 없으면 "추가 로그 확인 필요".

---

## 7. 수집기 아키텍처 (Plan 50 `evidence_collector` 확장)

### 7.1 EvidenceSource 레지스트리 (어댑터 패턴)

각 증거원을 동일 인터페이스의 어댑터로 등록 → `evidence_collector`가 `asyncio.gather`로 동시 수집
(부분 실패 허용·타임아웃 — Plan 50 §5.2, `alarm_context_enricher` 패턴 계승).

```
src/diagnosis/infrastructure/sources/
├── alarm_source.py        # L1: cmm_alarm + LogMonitor/ProcessMonitor + conditionLogText
├── metric_source.py       # L1: cmm_metric_stat_h/d/m (Plan 50 §4.2)
├── avail_source.py        # L1: 서브리소스 avail_status
├── process_source.py      # L1: 실시간 프로세스 API (재사용)
├── osconfig_source.py     # L1: core_config_prop (OSParameter/OSVerson)
├── netstat_source.py      # L2: server.Netstat/Process (조사 후)
└── host_probe_source.py   # L3: 신규 수집기(옵션 B/C) — 플래그·승인 게이트 뒤에서만
```

- 공통 인터페이스: `async collect(scope: IncidentScope) -> Evidence` (read-only, 타임아웃, 실패 시 None).
- 각 어댑터는 자신의 **가용 계층(L1/L2/L3)·신뢰도 라벨**(예: 실시간단면·정적스냅샷)을 결과에 부착 →
  `correlation_engine`/`causal_reasoner`가 신뢰도에 반영.

### 7.2 스냅샷 수집 vs 연속 수집

| 방식 | 적합 | 본 프로젝트 |
|------|------|-----------|
| 스냅샷(사건 시점 on-demand) | 현재/방금 사건 | L1 메트릭·실시간 프로세스·L3 옵션 B |
| 연속(상시 적재 후 조회) | 과거 포렌식·추이 | metric_stat(폴스타 연속) · L3 옵션 C 로그전송 |

- 구체 도구(L3 도입 시 참고): **연속** = `sysstat`(sadc→`/var/log/sa/saDD`, sar 재생; 기본 10분 샘플 →
  단기 스파이크 앨리어싱 주의), Prometheus `node_exporter`(`/proc`·`/sys` 시계열). **스냅샷** = `sosreport`/
  `sos report`(RHEL), `supportconfig`(SUSE), 또는 사용자 정의 진단 스크립트(무겁다 → nice/timeout·레이트리밋).
- **베스트프랙티스: 연속 baseline + 알람 트리거 스냅샷** — 임계 초과 시 진단 스크립트를 실행해 추이+심층을 동시 확보
  (Plan 50 push 진단과 정합).

→ **과거 사건의 원시 로그/프로세스 추이는 연속 수집(C) 없이는 불가**임을 명확히(R-과거공백).

### 7.3 읽기전용·마스킹 강제

- 모든 소스는 SELECT/GET만. L3 옵션 B는 **허용목록 명령**만(§9).
- 수집 결과는 `src/security/data_masker.py` + 프로세스 args 마스킹(`mask_args`) 통과 후 LLM 주입
  (비밀번호/토큰/연결문자열 — Known Mistakes·Plan 47-1 계승).

---

## 8. LLM 진단 파이프라인 통합 (§3.8 인코딩)

Plan 50 `causal_reasoner`에 §3.8 기법을 인코딩:

1. **결정적 증거 → 요약 텍스트만 LLM 주입**(수치 환각 차단).
2. **시스템 프롬프트에 절차 체크리스트**(USE·골든시그널·타임라인·변경기반·반증) 주입 → "추측 말고 절차".
3. **인용 의무**: 모든 가설은 주입 증거 항목 ID를 인용. 인용 불가 → `further_investigation`.
4. **검증기 노드(선택, Phase B)**: 순위화 전 (a)증거충분성 (b)시간순서 (c)출처일관성 자동 점검.
5. **순위 top-k + 신뢰도 + 데이터한계**: Plan 50 §7.2 스키마 재사용. 신뢰도는 **수집 계층·정밀도**에 연동
   (L3 미수집·월단위 메트릭·실시간단면 → 상한 제한).
6. **위험기반 게이팅**: 진단은 "제안"으로 노출(사람 검증). 자동 조치(재기동 등)는 본 계획 범위 외(읽기전용).

---

## 9. 보안 및 안전 설계 (특히 L3)

L1/L2는 기존 읽기전용 DBHub/REST 통제를 그대로 따른다. **L3 옵션 B(호스트 명령 실행)** 도입 시:

- **허용목록(allowlist) 전용**: 사전 정의된 읽기전용 진단 명령만(`free`,`df`,`ss -s`,`dmesg`(권한 시),
  `journalctl -p err --since`,`iostat`,`cat /proc/meminfo` 등). 임의 명령·셸 메타문자·쓰기 명령 차단.
- **읽기전용·비침습 보장**: 출력만 캡처, 어떤 변경·재기동도 금지. 명령 인자 화이트리스트·정규식 검증.
- **최소권한 계정**: 진단 전용 비-root 계정, 명령별 sudoers 한정(가능시), 호스트별 스코프.
- **자격증명 관리**: SSH 키/시크릿은 MCP 서버 측에만(에이전트는 URL/핸들만 — 기존 분리 원칙 계승).
- **감사 로그**: 모든 L3 수집을 대상·명령·시각·결과크기로 감사 기록(기존 감사 인프라 재사용).
- **마스킹**: 로그/프로세스 args의 비밀정보 마스킹 후에만 LLM·저장(§7.3).
- **레이트리밋·타임아웃·스로틀**: 사건당 수집 횟수·동시성 상한, `nice -n19 ionice -c3`로 부하 최소화,
  `lsof`/`/proc` 전수 스캔은 `-p PID`로 스코프 한정(호스트 부하·폭주 방지).
- **변경(mutating) 명령 절대 제외목록**: `dmesg -C/-c`(버퍼 삭제), sysctl 쓰기, `oom_score_adj`/`memory.max`/
  `scaling_governor` 쓰기, `systemctl restart/reset-failed`, `fsck`(파괴적·마운트중 금지), `coredumpctl debug` 등.
  허용목록은 **카운터·`/proc`·`/sys`·로그 읽기 전용**으로만 구성(§부록 A.3 참조).
- **ptrace 계열 금지(프로덕션)**: `strace`/`ltrace`는 측정상 최대 ~173× 슬로다운 유발 → 운영 호스트 금지.
  필요 시 샘플링/eBPF(`perf trace` 등) 한정. 기본은 읽기전용 `/proc`+카운터.
- **D-state 블로킹 방지**: `/proc/PID/{stack,smaps,environ,cmdline}` 읽기는 멈춘(wedged) 태스크에서
  **수집기 자체가 D 상태로 블록**될 수 있음 → 모든 호스트 읽기를 `timeout` 래핑, D 태스크 strace/gdb 금지.
- **비밀 포함 소스 기본 미수집**: `/proc/PID/environ`·코어덤프는 DB 비밀번호/토큰/PII를 포함 → 기본 수집 대상에서
  제외. 부득이 수집 시 엣지에서 마스킹(rsyslog `mmanon`/regex) 후에만 — 단, 시그니처(`Too many open files` 등)는 보존.
- **사용자 명시 승인 게이트**: L3는 기본 비활성. 도입 자체가 보안 정책 결정 → §11·§12 확인 항목.

> 원칙: **운영 호스트에 새 접근경로를 여는 것은 모니터링 솔루션(폴스타)이 이미 가진 채널을 재사용하는 것보다
> 항상 위험하다.** 따라서 A(폴스타 확장)·C(전달형)를 우선하고 B는 통제·승인 하에 최후수단.

---

## 10. 단계별 구현 계획

> Plan 50 Phase A(pull 단일서버 진단) 위에 데이터 계층을 점증한다. **L1 먼저, L3는 결정 후.**

- **Phase D1 — L1 증거원 (Plan 50 Phase A와 병행)**
  - alarm/metric/avail/process/osconfig 소스 어댑터 + 고정 SQL 템플릿.
  - verify: 각 소스 읽기전용·타임아웃·부분실패 graceful, 사건구간 정확 조회, 마스킹.
- **Phase D2 — 방법론 인코딩**
  - `correlation_engine`에 USE 매핑·이상탐지·타임라인·선후 판정 / `causal_reasoner`에 절차·인용·신뢰도.
  - verify: 수치 환각 0, 인용 강제, 신뢰도가 계층/정밀도에 연동.
- **Phase D3 — L2 매핑 (벤더 협의 의존)**
  - Netstat/Process/Other·NW메트릭·추가 API·conditionLogText 표본 검증 → 소스 추가.
  - verify: 표본 쿼리 검증, 허용테이블 갱신, 프로필 query_guide 보강.
- **Phase D4 — 변경 이벤트 증거 (§4.7)**
  - 폴스타/ITSM 변경이력 데이터 가용성 조사 → 타임라인 오버레이.
  - verify: 변경기반 RCA가 직전 변경을 타임라인에 표시.
- **Phase D5 — L3 신규 수집 (사용자 승인 후, 옵트인)**
  - 우선 A/C 검토. B 채택 시 §9 통제 일체 + 허용목록 + 감사 + 사용자 승인 게이트.
  - verify: 허용목록 외 명령 차단, 읽기전용 보장, 감사 기록, 마스킹, 레이트리밋.
- **Phase D6 — 검증기·플레이북 룰북**
  - 증거충분성/시간순서/출처일관성 검증기, 빈발 장애 FTA 룰북 축적.

---

## 11. 의사결정 영향 (`docs/02_decision.md`)

작업 착수 시 **D-039. 장애분석 OS 데이터 수집 계층화 및 진단 기법** 등재. 핵심:

- **D-039.1** 진단 데이터는 L1(폴스타 보유)→L2(매핑)→L3(신규수집) 3계층. L1 우선, L3 옵트인.
- **D-039.2** 방법론(USE·골든시그널·타임라인·변경기반·반증)을 체크리스트로 인코딩. 수치는 결정적 Python.
- **D-039.3** L3 호스트 수집은 A(폴스타 확장)·C(로그전송) 우선, B(직접명령)는 §9 통제+사용자 승인 하 최후수단.
- **D-039.4** 모든 수집 읽기전용·마스킹·감사. 자동 조치 없음(진단=제안, 사람 게이팅).

기존 결정 정합: D-003(읽기전용) 강화, D-022/D-028(폴스타 조인·허용테이블) 준수, D-035/D-036 재사용. 충돌 없음.

### 11.1 사용자 확인 필요 항목 (착수 전 결정)

1. **L3 호스트 직접 수집 도입 여부·방식**: A/C/B 중 무엇을 허용하는가? (보안 정책 결정 — 최대 쟁점)
2. **폴스타 추가 자산 협의 가능 여부**: LogMonitor conditionLogText 본문·Netstat/Process 값 위치·추가 REST
   API·에이전트 수집 확장(A)을 벤더와 협의할 수 있는가?
3. **로그 전송 파이프라인(C) 신설 의향**: 과거 로그 포렌식이 필요한가, 인프라 신설을 감수하는가?
4. **변경 이력 데이터 소스**: 배포/구성 변경 이력이 폴스타/ITSM 등에 존재하는가? (변경기반 RCA 직결)
5. **메트릭 시간 정밀도(`_h`) 보존 기간**: 시간단위 통계 보존 기간(이상탐지 정밀도 직결 — Plan 50 §16와 동일).

---

## 12. 변경 범위 요약

### 12.0 기존 코드 통합 분석 (수정 지점)

> 51은 50의 **데이터·기법 계층**이라 기존 코드 수정은 최소이며, 대부분 **신규 모듈 + 프로필 보강**이다.

- **기존 코드 수정 거의 없음** — L1 증거 수집은 기존 `src/routing/db_registry.py`(`DBRegistry.get_client`→
  `execute_sql`, 읽기전용)를 **그대로 호출**한다. 신규 로직은 `src/diagnosis/infrastructure/sources/`(신규 모듈).
- **DB 프로필 보강(수정)** — `config/db_profiles/polestar_*.yaml`:
  - 허용테이블/컬럼에 `cmm_metric_stat_[h,d,m]`(시간정밀도 분기), `cmm_resource` 계층(parent/platform_resource·
    avail_status·LogMonitor/ProcessMonitor) 노출.
  - query_guide에 메트릭 추이·시그니처 인식 가이드 추가. **⚠️ Known Mistakes 준수**: 예시 SQL에 `is_lob=1` 금지,
    공동존 서버명은 `r.name` 컬럼, avail_status `!= 0` 규칙.
- **`src/config.py`(수정)** — `DiagnosisConfig`에 소스별 토글·타임아웃·캐시 TTL(50과 공유).
- **L2/L3는 별개** — L2는 프로필/소스 추가(벤더 매핑 후), L3는 신규 인프라(보안결정, §9). 기존 코드 불변.
- 진단 서브그래프 배선·intent 통합은 **Plan 50 §15.0** 참조(51 자체는 서브그래프를 만들지 않음).

### 신규 파일
- `src/diagnosis/infrastructure/sources/{alarm,metric,avail,process,osconfig,netstat,host_probe}_source.py`
- `src/diagnosis/domain/{metric_anomaly,correlation}.py` 보강(USE 매핑·이상탐지 — Plan 50과 공유)
- `src/diagnosis/prompts/causal_reasoner.py` 보강(절차 체크리스트·인용 규칙)
- (L3 채택 시) `mcp_server`에 read-only 진단 도구 또는 별도 수집 채널 + 허용목록 정의
- `tests/test_diagnosis/test_sources_*.py`, `test_correlation_use_method.py`, `test_no_hallucination.py`

### 수정 파일
- `src/config.py` — `DiagnosisConfig`에 소스별 토글·L3 게이트·허용목록·레이트리밋 추가(기본 off)
- `config/db_profiles/polestar_*.yaml` — 허용테이블/ query_guide에 LogMonitor/ProcessMonitor/Netstat 보강
- `docs/02_decision.md` — D-039 등재
- `.env.example` — `DIAGNOSIS_*` 소스/보안 설정

### 변경하지 않는 파일 (재사용)
- 알람 이력 repo·프로세스 API·마스킹·감사·DBHub 클라이언트·Plan 50 진단 서브그래프 골격.

---

## 부록 A. OS 진단 레퍼런스 (구현·프롬프트 직접 입력용)

> `correlation_engine`의 결정적 로그/메트릭 패턴 룰과 `causal_reasoner` 프롬프트(시그니처 인식·인용)에
> 그대로 인코딩한다. 모든 항목은 **읽기 전용**이며, 변경 명령은 §A.3 제외목록에 둔다.

### A.1 OS 장애 시그니처 치트시트 (verbatim — 로그 패턴 룰)

커널 소스/벤더 문서에서 그대로 인용한 시그니처. LLM은 이 문자열을 인식·인용만 하고 임의 생성하지 않는다.

| 장애 | 시그니처(원문) | 출처 위치 |
|------|---------------|----------|
| OOM 강제종료 | `Out of memory: Killed process <pid> (<name>) total-vm:…kB, anon-rss:…kB,…` / `oom-kill:constraint=…` / `<comm> invoked oom-killer: gfp_mask=…` | dmesg/journal -k. anon-rss 높음=누수 의심, `constraint=CONSTRAINT_NONE…global_oom`=시스템 OOM vs `task_memcg=`=cgroup OOM |
| 소프트 락업 | `watchdog: BUG: soft lockup - CPU#N stuck for Ns! [comm:pid]` | dmesg |
| Hung task | `INFO: task <comm>:<pid> blocked for more than 120 seconds.` (+ call trace) | dmesg. D상태 장기 블록 |
| RCU stall | `rcu: INFO: rcu_sched detected stalls on CPUs/tasks` | dmesg |
| 커널 패닉 | `Kernel panic - not syncing: …` + `Call Trace:` | 패닉 시 버퍼 소실 → serial/netconsole/kdump 필요 |
| FS/블록 오류 | `EXT4-fs error (device X)` / `blk_update_request: I/O error` / `Buffer I/O error … lost async page write` / `Remounting filesystem read-only` / XFS `Corruption detected`·`Shutting down filesystem` | dmesg. RO 리마운트=FS 보호조치 |
| conntrack 고갈 | `nf_conntrack: table full, dropping packet` | dmesg. 신규연결 무음 드롭 |
| SYN 플러드 | `possible SYN flooding on port N. Sending cookies.` | dmesg |
| 포트 고갈 | `connect: Cannot assign requested address` (EADDRNOTAVAIL) | 앱 로그. TIME_WAIT 폭증 동반 |
| MTU 블랙홀 | `ping: local error: Message too long, mtu=…` | 큰 전송만 멈춤 |
| FD 고갈 | `Too many open files` (EMFILE) | 앱 로그 |
| 스레드/PID 고갈 | `fork: retry: Resource temporarily unavailable` (EAGAIN) / pthread_create EAGAIN | 앱 로그. cgroup `pids.events:max` 상승 |
| Segfault | `segfault at <addr> ip <addr> sp <addr> error <N> in <lib>` (error 4=read, 6=write unmapped) | dmesg/coredumpctl |
| GP fault | `traps: <comm>[pid] general protection ip:… in <lib>` | dmesg |
| systemd 플래핑 | `Start request repeated too quickly` / `Failed with result 'exit-code'` / `start-limit-hit` | journalctl -u |
| 인증 브루트포스 | `Failed password for … from <ip>` / `pam_unix(sshd:auth): authentication failure` | /var/log/secure·auth.log |
| NFS | `Stale file handle` (ESTALE) | dmesg/앱 |

### A.2 장애유형 → 1차 신호 → 데이터 소스 맵 (USE 매핑)

| 장애 | 1차 신호 | 소스 | 계층 |
|------|---------|------|------|
| 메모리 고갈 | `MemAvailable` 낮음(MemFree 아님), `Committed_AS`≈`CommitLimit` | `/proc/meminfo` | L1(Util%)/L3(분해) |
| 스왑 스래싱 | si≈so 지속 + 높은 `%wa` + 낮은 `%vmeff` | vmstat, sar -B/-W | L3 |
| 프로세스 OOM | dmesg `Killed process` 테이블, `oom_score`/VmRSS | dmesg, /proc/PID | L3(또는 폴스타 syslog 관제 시 L1) |
| 컨테이너 OOM | `memory.current`≈`memory.max`, `memory.events:oom_kill` | cgroup | L3 |
| CPU 포화 | `vmstat r`>nproc, `/proc/pressure/cpu some` | vmstat, PSI | L1(Util)/L3(런큐·PSI) |
| CPU 경합 | `nvcswch/s`, schedstat run_delay | pidstat -w, /proc/PID/schedstat | L3 |
| VM steal | `%st` 지속 | top/mpstat/sar | L3(또는 metric 제공 시 L1) |
| 컨테이너 CPU 쓰로틀 | `cpu.stat nr_throttled/throttled_usec` | cgroup | L3 |
| 디스크 포화 | 높은 `await`(HDD는 +%util; **SSD/NVMe는 %util 신뢰 금지 → await·처리량**) | iostat -xz | L1(MaxIORate)/L3 |
| 디스크 고장 | SMART Reallocated/Pending, dmesg I/O error | smartctl, dmesg | L3 |
| 디스크 풀/inode | `df -h`/**`df -i`**, `lsof +L1`(삭제된 열린 파일) | df, lsof | L1(FS Util)/L3 |
| TCP 손실 | `TcpRetransSegs`, mtr Loss% | nstat, mtr | L3 |
| accept 큐 오버플로 | `ss -ltn` Recv-Q≈Send-Q, `ListenOverflows` | ss, nstat | L3 |
| conntrack/포트 고갈 | count vs max, EADDRNOTAVAIL | /proc/sys/net | L3 |
| Hung I/O | 다수 `D` 상태, wchan | ps, /proc/PID/wchan | L3 |
| 좀비 | `Z` 상태, PPID(부모가 원인) | ps | L1(실시간 API)/L3 |
| FD/스레드 고갈 | EMFILE/EAGAIN, fd수 vs limit | /proc/PID/{fd,limits}, cgroup pids | L3 |
| 크래시 | `segfault at`, coredumpctl | dmesg, coredumpctl | L3 |

> 해석 주의(가드레일 §3.7): 부하평균↑은 CPU가 아니라 **D상태 I/O**일 수 있음(`vmstat r`로 구분). `iowait`는
> idle의 한 형태(디스크 지표). `%util`은 SSD/NVMe에서 오도(await 사용). `MemFree`가 아니라 `MemAvailable`을 본다.

### A.3 읽기전용 허용목록 / 변경명령 제외 (L3 옵션 B 도입 시)

- **허용(read-only)**: `cat /proc/*`·`/sys/*`·cgroupfs, `free`,`vmstat`,`mpstat`,`pidstat`,`sar`,`ss`,`iostat`,
  `df -h/-i`,`du`,`lsof -p PID`,`smartctl -H/-a`,`ps`,`pstree`,`systemctl status`,`journalctl -p err --since`,
  `dmesg`(권한 시),`coredumpctl info`. 모두 `timeout nice -n19 ionice -c3`로 래핑.
- **금지(mutating)**: `dmesg -C/-c`, sysctl 쓰기, `oom_score_adj`/`memory.max`/`scaling_governor` 쓰기,
  `systemctl restart/reset-failed`, `fsck`, `coredumpctl debug`, `strace`/`ltrace`(프로덕션), 임의 셸/파이프.

---

## 13. 참고 (방법론 출처)

- USE Method: https://www.brendangregg.com/usemethod.html , .../USEmethod/use-linux.html
- Linux Performance in 60s: https://www.brendangregg.com/Articles/Netflix_Linux_Perf_Analysis_60s.pdf
- 방법론 토킷: https://www.brendangregg.com/methodology.html
- Four Golden Signals / 알림 철학: https://sre.google/sre-book/monitoring-distributed-systems/
- 변경기반 RCA(~70% 장애): https://sre.google/sre-book/introduction/
- Effective Troubleshooting(상관≠인과·differential): https://sre.google/sre-book/effective-troubleshooting/
- 포스트모템/타임라인: https://sre.google/sre-book/postmortem-culture/
- RED Method: https://grafana.com/blog/the-red-method-how-to-instrument-your-services/
- 관측성 3축: https://opentelemetry.io/docs/concepts/observability-primer/
- 이상탐지(Skyline)/백분위(p99): https://github.com/etsy/skyline , https://www.p99conf.io/2023/03/28/gil-tene/
- LLM-RCA: arXiv 2301.03797(MS ICSE'23), 2305.15778(RCACopilot EuroSys'24), 2401.13810(GPT-4 ICL),
  2403.04123(LLM Agents/ReAct), 2309.05833(PACE-LM 신뢰도)
- OS 데이터 소스(부록 A): Linux man-pages(man7.org: free/vmstat/mpstat/pidstat/sar/ss/iostat/journalctl/dmesg/
  smartctl/proc_pid_*), kernel.org docs(proc.html, cgroup-v2, accounting/psi, scheduler/sched-stats·sched-bwc,
  admin-guide/lockup-watchdogs, RCU/stallwarn, admin-guide/pm/cpufreq, networking/{statistics,snmp_counter}),
  커널 소스(`mm/oom_kill.c`,`kernel/watchdog.c` — OOM/락업 시그니처 원문), RFC 5424(syslog),
  Red Hat/SUSE/Elastic/rsyslog/Prometheus 문서
- 데이터 모델: `schema/polestar-schema.md`, `schema/polestar-data.md`, `config/db_profiles/polestar_cm_gp.yaml`
- 자매 계획: `plans/50-fault-diagnosis-rca.md`
