# 65. 노이즈 캔슬링 목업 이벤트 생성기 (Mock Polestar Event Generator)

> 작성일: 2026-07-23
> **목적**: 폴스타 실계 없이 **사전 정의된 이벤트를 간단한 호출로 생성·주입**하여, collectorinfra 노이즈 캔슬링 구조(alarm_server → Redis Stream → AlarmWorker → 노이즈 게이트)가 해당 이벤트를 **캔슬링(SUPPRESS)하는지 사용자가 직접 테스트**할 수 있게 한다.
> **대상/선행 계획**: Plan 52(노이즈 게이트 — 구현 완료, D-048·D-049), Plan 60(노이즈 캔슬링 정밀화 — 구현 완료, D-106~D-114), docs/16(Plan 52 테스트 가이드 §6), docs/20(Plan 60 기능 테스트 가이드).
> **관련 결정**: D-003(읽기전용 — 본 도구는 DB에 쓰지 않음, Redis 스트림·TCP 주입만), D-035(임베딩·LLM 주석 전용 경계 — 본 도구는 판정 로직을 변경하지 않음), D-048(노이즈 게이트), D-049(decision_store 감사 JSONL — 본 도구의 판정 근거).
> **신규 결정(본 계획에서 부여, 착수 시 등재)**: **D-115**(목업 이벤트 주입 경로 — TCP 실경로 기본 + Redis 직주입 폴백).
> ※ 번호 규칙(Known Mistakes): `## D-` 헤더 등재 최댓값 **D-114**, 02_decision.md 언급상 다음 번호 D-115. 단 Plan 64가 D-101~D-103 재조정 예정이므로, **등재 직전 `## D-` 헤더와 「변경 이력」 표를 모두 재확인**해 충돌 시 다음 빈 번호로 재조정한다.
> **사용자 확정(2026-07-23)**: 실행 방식 = **상주 프로세스 + 번호키 메뉴 선택** — 프로그램을 띄워 두고 사용자가 번호를 입력하면 해당 번호의 사전 정의 이벤트를 발생시키는 대화형 방식(§3.1·§5).
> **갱신(2026-07-23)**: 사용자 제공 **실제 ITSM 알림 샘플 13장 반영** — 실측 알람 형식·패턴을 §2.4에 정리하고, 시나리오 카탈로그(§4)의 이벤트 내용을 실측 텍스트 기반으로 구체화. `distinct-pair` 시나리오(음성 대조군) 추가.
> **갱신(2026-07-24 · D-118)**: SREAgent 통합으로 이관 계획 **sre-agent/03(목업 생성기)이 본 계획으로 대체**됨(그쪽 상태 헤더 명시). 그쪽 잔존 유효분(델타)인 **`invest-trigger` 시나리오**(PAGE→게이트 훅→`sre_investigate_alarm` submit 확인)를 **§4.3**으로 편입. 관련: `plans/sre-agent/03`(대체됨)·`plans/sre-agent/05`(submit/poll MCP 계약)·Plan 64 §0.2 CW-A(게이트 훅 배선).
> **상태**: **구현 완료 (2026-07-27 · Plan 66 Wave 1-A · D-121 등재)** — `scripts/mock_polestar_events.py`(카탈로그 12종·`make_payload`·TcpSender/RedisSender·JSONL 판정기·대화형 메뉴·`--send`), `tests/test_scripts/`(42 passed·1 skipped[RUN_E2E]), `docs/20` §8 가이드. TCP 기본+Redis 폴백·침묵 폴백 금지·src/ 무변경. **1-C 교차 검증(Plan 66)**: E7 수용 기준 대조 후 카탈로그 E7 커버리지 갭 2건 보완 — [13] non-alarm(E7-b·S8 승인요청)·[14] net-site-cascade(E7-c/d·S5 `||(장애)` 사이트 포맷) 추가(`tests/test_scripts` 74 passed). **잔여**: cascade(E4)·change-corr(E5)는 픽스처 부재(§7 G-3)로 정의·플래그 점검까지만, invest-trigger[12]는 R8(게이트 훅 submit) 구현 후 활성(현재 스텁).

---

## 1. 배경 및 목적

### 1.1 문제

노이즈 캔슬링(Plan 52 게이트 + Plan 60 정밀화)의 동작 확인은 현재:

- 운영 경로: 폴스타 실계가 TCP(9100)로 이벤트를 보내야만 전 구간이 동작 — **개발/검증 환경에서 폴스타 없이 재현 불가**.
- `scripts/noise_gate_scenario_test.py`(Plan 52): Redis 직주입(XADD)·API 경로만 지원 — **alarm_server(TCP 수신부)를 건너뛰며**, Plan 60 신기능(E1~E6·B-7) 시나리오가 없다.
- docs/20(Plan 60 테스트 가이드): 수동 시나리오 절차는 있으나 **이벤트를 손으로 만들어 넣어야** 한다.

### 1.2 목표

사용자가 목업 프로그램을 **상주 프로세스로 띄워 두고**, 화면의 시나리오 메뉴에서 **번호키만 입력하면** 해당 번호의 사전 정의 이벤트(단건 또는 시퀀스)가 생성·주입되고, 그 이벤트가 캔슬링됐는지(tier=SUPPRESS 등)를 **자동으로 판정·보고**받는다. 판정 후 메뉴로 돌아와 반복 테스트할 수 있다.

```
$ python scripts/mock_polestar_events.py
══ 노이즈 캔슬링 목업 이벤트 생성기 (TCP localhost:9100) ══
 [1] sev3-page      severity 3 단건            → 기대: PAGE
 [4] dup-suppress   동일 알람 2연속            → 기대: 2건째 SUPPRESS
 ...
 [q] 종료
번호 입력> 4
[주입] MOCK-a1b2c3d4-1, MOCK-a1b2c3d4-2 (2건, 간격 3s)
[판정] 1건째 PAGE ✔ / 2건째 SUPPRESS(dedup) ✔ — 기대 일치
번호 입력>
```

### 1.3 비목표 (범위 제외)

- 폴스타 DB 데이터 목업 생성(서버 메타·토폴로지·변경이력 픽스처) — 기존 도커 픽스처(`testdata/pg`)를 **전제**로 사용하며 본 도구가 만들지 않는다. 부족한 픽스처는 후속(§7 G-3).
- E3 동적 baseline 시나리오 — 이벤트 주입이 아니라 `cmm_metric_stat` 조회 기반이라 이벤트 생성만으로 재현 불가. docs/20 수동 절차 유지.
- 게이트·판정 로직 변경 — 본 도구는 **주입·관찰만** 한다(파이프라인 코드 무변경).

---

## 2. 현황 실측 — 수신 파이프라인과 기존 자산

### 2.1 운영 수신 경로 (실측 확인)

```
폴스타 알람 시스템
  │ TCP 단일행 JSON (포트 9100, ALARM_SERVER_SOCKET_PORT)
  ▼
alarm_server/tcp_receiver.py (TcpReceiver._parse → json.loads)
  │ XADD alarm:raw  {"data": "<json>"}   (base_receiver.py)
  ▼
src/alarm/application/alarm_worker.py (XREADGROUP, Consumer Group)
  │ enricher → 노이즈 게이트(SUPPRESS/TICKET/PAGE) → 통보
  ▼
logs/alarm_decisions.jsonl (decision_store 감사, D-049)  ← 판정 근거
```

### 2.2 이벤트 페이로드 스키마 (실측: `_build_alarm_event_from_payload`)

폴스타 권장 단일행 JSON 템플릿. 목업 이벤트는 이 필드를 채워 생성한다:

| 필드 | 의미 | 비고 |
|---|---|---|
| `alarmId` | 알람 식별자 | 목업은 `MOCK-<run_id>-<seq>` 형식으로 생성(판정 시 추적 키) |
| `severity` | 심각도 0~3 | **0=클리어**(is_clear), 3=단락 PAGE |
| `alarmStatus` | 알람 상태 | ACK 상태 — 판정 무관(Plan 47 §9) |
| `serverName`/`hostname`/`ipAddress` | 서버 식별 | 도커 픽스처 서버명(`noise-test-*`) 사용 시 중요도·유지보수 신호 연동 |
| `dbId` | 대상 DB 프로필 | 기본 `polestar_pg`(도커 픽스처) |
| `resourceType`/`resourceName`/`resourceAncestry` | 자원 정보 | E4 연쇄 시나리오에서 상·하위 자원 구분에 사용 |
| `alarmName`/`conditions`/`conditionLog` | 알람 내용 | E2 상관·B-7 유사도 시나리오의 텍스트 재료 |
| `alarmTime` | `%Y%m%d%H%M%S` | 미지정 시 현재 시각 |

### 2.3 기존 자산과의 관계 (중복 회피)

| | `noise_gate_scenario_test.py` (Plan 52, 기존) | **본 계획 목업 생성기 (신규)** |
|---|---|---|
| 주입 경로 | API(`/alarm/analyze-test/raw`)·Redis XADD | **TCP 실경로(alarm_server 포함 전 구간)** 기본 + Redis 폴백 |
| 시나리오 | Plan 52(중요도·유지보수·dedup·sev3) | Plan 52 핵심 재수록 + **Plan 60(E1·E2·E4·E5·B-7)** |
| 시퀀스 이벤트 | 단건 중심 | **다건 시퀀스**(상관·연쇄는 순서·간격이 본질) |
| 판정 | 결정 JSONL tier 대조 | 동일 방식 재사용 + Plan 60 감사 필드(noise_ctx 등) 표시 |
| 위상 | E2E 회귀 실행기 | **사용자 대화형 수동 테스트 도구** |

기존 스크립트는 유지(회귀용)하고, 신규 도구는 **전송·시퀀스·Plan 60 관찰**에 집중한다. 판정(JSONL 조회) 로직은 재사용 가능하면 공용화하되, 단순 재구현이 더 작으면 그쪽을 택한다(Simplicity First).

### 2.4 실측 ITSM 알림 샘플 (2026-07-23 사용자 제공, 스크린샷 13장)

실제 운영 환경의 ITSM알리미 알림(2026.6.27~7.22 기간)에서 추출한 알람 형식과 노이즈 패턴. **목업 이벤트의 텍스트는 이 형식을 따른다.**

**공통 형식**: `[ITSM]<host> <host>(<역할#번호>) <항목> [<값> (<조건>[, N회 연속])]`
운영자 후속 통보는 동일 본문 뒤에 `=> 담당자 …` / `메세지는 예정된 <작업>으로 발생. 담당자 …` 를 부가한 **재발신 메시지**로 온다.

| # | 패턴 | 실측 샘플 (요지) | 노이즈 캔슬링 관점 |
|---|---|---|---|
| S1 | 가용성 DOWN→UP 순단 | `nspist02(일체형-망연계스트리밍(내부)#2) 가용성 [DOWN (= DOWN, 2회 연속)]` → 15분 후 `가용성 [UP (= UP)]` (ncpvmoa1304는 3분 후 UP) | DOWN·복구(클리어) 페어 |
| S2 | 파일시스템 임계 | `fnosaso1 파일시스템 사용률 [95 % (> 95 %)]`, `ecrapo13 /fsapp 사용률 [90 % (> 90 %, 2회 연속)]` | 임계치 알람, resourceName=마운트 경로 |
| S3 | 운영자 주석 재발신 | `szaaso01 파일시스템 사용률 [95 % (> 95 %)]` → 7분 후 동일 본문+`=> 담당자 ○○○ 수석차장 통화. 확인 후 연락 준다고함.` → 37분 후 동일 본문+`=> … 서비스 영향 없음.` | **표현만 다른 동일 알람** — B-7 semantic-dup 실측 재료 |
| S4 | 계획작업(IPL) 일괄 DOWN | 동일 시각(오후 8:00) `staapos1/2(직판AP)`·`stadbos1/2(직판DB)`·`stadboc1/2(컨버젼DB)` 6대 동시 가용성 DOWN → `예정된 IPL 작업으로 발생` 주석. `daidbo11/12`·`ncpfsoi1/2`(VDI_김포)도 동일 패턴, 주석에 `계획정지 관련 작업으로 서비스 이상없음` | **동시 다발 상관(E2) + 변경·작업 연관(E5)** 복합 실측 사례 |
| S5 | 상위 원인→하위 장비 연쇄 | `S8530JUM-4331-1.sotori.com\|\|(장애) 세종대` + `K8530JUM-4331-2 …` 동시 발생 → `영업점 세종대 네트워크 3등급 장애` → `세종대 전기 작업으로 14:00까지 네트워크 다운` 원인 통보. 한국인터넷진흥원_파출수납(S8970a/K8970a 페어)도 `건물 전기작업` 동일 구조 | **상위 원인(전기/네트워크) → 하위 장비 다발(E4 cascade)**. 네트워크 장비는 `<장비ID>.<도메인>\|\|(장애) <사이트명>` 별도 형식 |
| S6 | 반복 재발 | `stafxo01(FAX#1) 가용성 [DOWN (= DOWN, 2회 연속)]` 20:30 → 주석 재발신 20:37 → **동일 알람 재발** 20:45 | 재발 반복(E1 recurrence) |
| S7 | DOWN+재기동 지표 동반 | `nipdbr36(운영-MCIDDB#2) 가용성 [DOWN …]`과 동시각 `서버 기동 지속시간 [7 sec (< 1.5 min)]` | 같은 호스트의 **상이한 알람 2건** — dedup 오탐 방지 대조군 |
| S8 | 형식 이질·비알람 혼재 | `SAE 0011649 자본시장추진부 UPS 출력 전압 하한 경고 임계치 미만`(설비), `kfexdb02_DSFEXA02 DSFEXA02] Oracle Down!`(DB 인스턴스), `내부Cloud ○○○님의 Cloud PC 사양변경 승인바랍니다.`(비알람 승인요청) | 호스트 접두 없는 이질 형식도 수신 가능 — 파서 견고성 재료 |

**§2.2 payload 매핑 규칙** (이벤트 빌더가 따를 변환):

| §2.2 필드 | 샘플에서의 대응 | 예 |
|---|---|---|
| `serverName`/`hostname` | `[ITSM]` 직후 호스트 토큰 | `staapos2`, `nipdbr36` |
| `alarmName` | 항목명 | `가용성`, `파일시스템 사용률`, `서버 기동 지속시간` |
| `conditionLog` | 대괄호 조건 원문 | `[DOWN (= DOWN, 2회 연속)]`, `[95 % (> 95 %)]`, `[7 sec (< 1.5 min)]` |
| `conditions` | 임계 조건부 | `> 95 %`, `= DOWN, 2회 연속` |
| `resourceType`/`resourceName` | 역할 괄호·마운트 경로·장비ID | `직판AP#2`, `/fsapp`, `S8530JUM-4331-1.sotori.com` |
| `severity` | **샘플에 명시 없음 — 가정**: 가용성 DOWN=2, UP=0(클리어), 사용률 임계=1, UPS 등 설비 경고 중 단락 테스트용만 3 | 착수 시 게이트 매트릭스와 대조해 확정 |

- **가명화 원칙**: 샘플의 담당자 실명은 계획서·목업 텍스트 모두 `담당자 ○○○`로 대체한다(형식만 유지). 호스트명·역할명은 시나리오 재현에 필요하므로 유지.
- **호스트명 제약**: 중요도·유지보수·토폴로지·변경이력 등 **DB 연동 신호가 필요한 시나리오는 도커 픽스처 서버명(`noise-test-*`)을 유지**하고, 실측 호스트명은 DB 신호가 불필요한 텍스트 중심 시나리오(dedup·semantic-dup·recurrence 등)에만 사용한다.

---

## 3. 설계

### 3.1 구성 요소 (단일 파일 · 대화형 메뉴 상주 프로세스)

```
scripts/mock_polestar_events.py
├── 시나리오 카탈로그   : SCENARIOS 목록 — 번호 순서 고정, 사전 정의 이벤트(단건/시퀀스) + 기대 티어 + 필요 플래그
├── 이벤트 빌더        : make_payload() — §2.2 스키마의 단일행 JSON 생성
├── 전송기            : TcpSender(기본, socket) / RedisSender(폴백, XADD alarm:raw)
├── 판정기            : 결정 JSONL에서 alarm_id별 tier·감사 필드 조회, 기대치 대조
└── 메뉴 루프         : 기동 시 시나리오 번호 메뉴 표시 → 번호 입력 → 주입·판정·결과 출력 → 메뉴 복귀
```

**메뉴 루프 동작 (기본 실행 형태)**:

1. 기동 시 전제 점검(전송 경로 도달성·결정 JSONL 존재) 결과와 함께 번호 메뉴를 출력한다. 각 항목에 시나리오명·설명·기대 티어·필요 플래그(Plan 60)를 한 줄로 표시한다.
2. 사용자가 **번호 + Enter**를 입력하면 해당 시나리오를 주입한다. 입력 처리는 stdlib `input()` 루프로 구현한다(단일 키 raw 입력은 termios 의존·이식성 문제로 채택하지 않음 — Simplicity First).
3. 주입 직후 판정기가 결정 JSONL을 폴링(기본 30s)해 결과(✔/✘·tier·감사 필드)를 출력하고 메뉴 프롬프트로 복귀한다. 시퀀스 시나리오(상관·연쇄)는 이벤트 간 간격을 두고 순차 전송하며 진행 상황을 표시한다.
4. 보조 키: `l`(메뉴 다시 표시), `v`(판정 대기 on/off 토글 — off면 주입만 하고 즉시 복귀), `q`(종료).
5. 잘못된 번호·전제 미충족 시나리오 선택 시 사유를 출력하고 메뉴로 복귀한다(주입하지 않음).

- **보조 모드(비대화형)**: `--send <이름>` 옵션으로 한 번 주입 후 종료 — 도구 자체의 e2e 테스트·자동화용(§6 #7). 사용자 대상 기본 형태는 어디까지나 메뉴 상주 방식.
- **의존성**: stdlib + redis(폴백 경로일 때만, 이미 프로젝트 의존성) — 신규 패키지 없음. curses·TUI 라이브러리 미사용.
- **아키텍처 계층**: `scripts/` 소속(기존 스크립트와 동일) — `src/` 계층 규칙 비대상, 단 `src` 임포트는 판정 공용화 시 infrastructure 쪽만.

### 3.2 주입 경로 (D-115 예약)

| 경로 | 커버 범위 | 전제 |
|---|---|---|
| **TCP (기본)** | 폴스타 실경로 100% — TcpReceiver 파싱·XADD 포함 | `python -m alarm_server` 기동 (포트 9100) |
| Redis (폴백) | 워커 이후 100% (수신부 제외) | Redis 접근만 (기존 스크립트와 동일 수준) |

TCP를 기본으로 하는 근거: 기존 스크립트가 커버하지 못하는 유일한 구간이 TCP 수신부이며, "폴스타가 보낸 것과 동일한" 목업이라는 본 계획의 취지에 부합. alarm_server 미기동 시 명확한 에러 메시지와 함께 `--path redis` 안내(침묵적 폴백 금지 — Known Mistakes).

### 3.3 판정 (캔슬링 여부 확인)

1. 주입한 `alarmId`(MOCK-<run_id>-<seq>) 목록 기억.
2. `logs/alarm_decisions.jsonl`을 폴링(타임아웃 기본 30s)하여 해당 alarm_id 레코드 대기.
3. 레코드의 `tier`를 시나리오의 기대 티어와 대조해 ✔/✘ 출력.
4. Plan 60 관찰 포인트가 있는 시나리오는 감사 필드도 표시: `recurrence`(E1), `correlation_meta`(E2), `noise_ctx.cascaded/root_resource`(E4), `noise_ctx.change_nearby`(E5), `semantic_annotation`(B-7).
5. 메뉴의 `v` 토글로 판정 대기를 끄면 주입만 하고 즉시 메뉴로 복귀한다(사용자가 UI·로그로 직접 확인하려는 경우).

전제: 서버와 같은 호스트/리포지토리 루트에서 실행(기존 스크립트와 동일 전제).

---

## 4. 사전 정의 이벤트 카탈로그 (초안)

### 4.1 기본 시나리오 — Plan 52 게이트 (Plan 60 플래그 off에서도 동작)

이벤트 텍스트는 §2.4 실측 샘플 형식을 따른다 (`샘플` 열은 §2.4 패턴 번호).

| 이름 | 이벤트 (실측 샘플 기반) | 샘플 | 기대 결과 |
|---|---|---|---|
| `sev3-page` | severity 3 단건 — `자본시장추진부 UPS 출력 전압 하한 경고 임계치 미만` | S8 | PAGE (단락) |
| `low-suppress` | `noise-test-low`(중요도 낮음) severity 1 — `/fsapp 사용률 [90 % (> 90 %, 2회 연속)]` | S2 | SUPPRESS/TICKET (매트릭스) |
| `maint-suppress` | `noise-test-maint`(유지보수 중) severity 2 — `가용성 [DOWN (= DOWN, 2회 연속)]` | S4 | SUPPRESS |
| `dup-suppress` | 동일 알람 2연속 (간격 수 초) — `stafxo01(FAX#1) 가용성 [DOWN (= DOWN, 2회 연속)]` 재발 패턴 | S6 | 1건째 게이트 통과, 2건째 SUPPRESS (dedup) |
| `clear` | DOWN(sev2) → 수 초 후 UP(sev0) 시퀀스 — `가용성 [DOWN (= DOWN, 2회 연속)]` → `가용성 [UP (= UP)]` | S1 | 1건째 게이트 판정, 2건째 클리어 처리 (통보 아님) |
| `distinct-pair` | 같은 호스트의 상이한 알람 2건 — `nipdbr36 가용성 [DOWN …]` + `nipdbr36 서버 기동 지속시간 [7 sec (< 1.5 min)]` | S7 | **음성 대조군**: 2건 모두 독립 판정 (2건째가 dedup SUPPRESS 되지 않아야 함) |

### 4.2 Plan 60 시나리오 — 해당 플래그 on 필요 (플래그 키는 .env Plan 60 블록·docs/20 참조)

| 이름 | 이벤트(시퀀스, 실측 샘플 기반) | 샘플 | 필요 플래그 | 기대 관찰 |
|---|---|---|---|---|
| `recur-audit` (E1) | 동일 알람 3회 반복 — `stafxo01(FAX#1) 가용성 [DOWN …]` 실측 재발 패턴(20:30→20:45)을 수 초 간격으로 압축 | S6 | (항상 on, `NOISE_RECURRENCE_AUDIT_EVERY_N`) | 감사 `recurrence` 필드에 재발 카운트 |
| `cross-host` (E2) | 유사 역할 호스트 3대에 동일 `가용성 [DOWN (= DOWN, 2회 연속)]`을 상관 창(기본 120s) 내 연속 주입 — 직판AP/DB 6대 동시 IPL DOWN 실측 패턴의 축소판 | S4 | `NOISE_CROSS_HOST_CORRELATION_ENABLED` | `correlation_meta` 클러스터 id·크기 |
| `cascade` (E4) | 상위 자원(네트워크/부모 호스트) 알람 → 수 초 후 하위 자원 알람 — 세종대 전기작업→네트워크 다운→하위 장비 `\|\|(장애)` 다발 실측 구조. 호스트는 픽스처 토폴로지(`noise-test-*`) 사용, 텍스트는 S5 형식 | S5 | `NOISE_MULTI_HOP_CASCADE_ENABLED` | 하위 알람 `noise_ctx.cascaded=true` + SUPPRESS |
| `change-corr` (E5) | 변경이력이 있는 자원의 가용성 DOWN (픽스처의 `cmm_resource_lifecycle_history` 창 내) — `예정된 IPL 작업`·`계획정지 관련 작업` 실측 상황의 재현 | S4 | `NOISE_CHANGE_CORRELATION_ENABLED` | `noise_ctx.change_nearby=true` |
| `semantic-dup` (B-7) | 표현만 다른 유사 텍스트 알람 쌍 — 실측 쌍 그대로: `szaaso01 파일시스템 사용률 [95 % (> 95 %)]` vs 동일 본문+`=> 담당자 ○○○ 수석차장. 서비스 영향 없음.` | S3 | `NOISE_SEMANTIC_DEDUP_ANNOTATION_ENABLED` + 로컬 모델(docs/19) | `semantic_annotation` 유사도 주석 (판정 불변 — D-035) |

- 각 시나리오는 **필요 플래그·픽스처 전제를 실행 시 사전 점검**해, 미충족이면 주입 전에 사유를 출력하고 중단한다(침묵 실패 금지).
- E4·E5는 도커 픽스처에 토폴로지(AVAIL_DEPEND)·변경이력 데이터가 있어야 한다 — 현 픽스처 충족 여부는 착수 시 실측(§7 G-3).

### 4.3 SRE-Agent 연동 시나리오 — 자동 조사 트리거 (2026-07-24 편입 · sre-agent/03 델타)

| 이름 | 이벤트 | 필요 플래그·전제 | 기대 관찰 |
|---|---|---|---|
| `invest-trigger` | S8 변형 severity 3 단건(PAGE 단락 확정) | `investigation_trigger_enabled` + `sre_agent` 조사 서비스 기동(Plan 64 §0.2 CW-A 배선 구현 후) | PAGE 판정 + 게이트 훅이 **`sre_investigate_alarm`(sre-agent/05 §3)을 submit**하고 `{investigation_id, status: accepted}` 응답을 수신 — 기본은 **트리거 로그·submit 응답 확인까지만**. 실 HolmesGPT 조사 완주·브리핑 수신 대조는 LLM 비용이 발생하므로 **`RUN_E2E=1` 옵트인에서만** |

- 조사 서비스 미기동 시에도 게이트 판정·통보는 정상 완료되고 트리거만 graceful 실패해야 한다(비차단 계약 — Plan 60 §14.2). 도구는 이 경우 "submit 실패(사유)"를 표시한다(침묵 실패 금지).
- 동일 시나리오 연속 주입 시 2회째 submit이 `status: duplicate`(기존 investigation_id 반환)인지도 표시한다(dispatcher dedup 확인 — sre-agent/05 §3).

---

## 5. 사용례 (초안)

**기본 — 대화형 메뉴 세션**:

```
$ python scripts/mock_polestar_events.py
[점검] TCP localhost:9100 도달 ✔ · 결정 로그 logs/alarm_decisions.jsonl ✔
══ 노이즈 캔슬링 목업 이벤트 생성기 ══
─ 기본 (Plan 52 게이트) ─────────────────────────────
 [1] sev3-page       UPS 출력 전압 하한 sev3        → 기대: PAGE
 [2] low-suppress    중요도 낮음 /fsapp 사용률 sev1 → 기대: SUPPRESS/TICKET
 [3] maint-suppress  유지보수 중 가용성 DOWN sev2   → 기대: SUPPRESS
 [4] dup-suppress    FAX#1 가용성 DOWN 2연속       → 기대: 2건째 SUPPRESS
 [5] clear           가용성 DOWN → UP 순단          → 기대: 2건째 통보 없음
 [6] distinct-pair   같은 호스트 상이 알람 2건       → 기대: 각각 독립 판정
─ Plan 60 (플래그 필요 — off면 선택 시 안내) ────────
 [7] recur-audit     동일 알람 3회 반복 (E1)
 [8] cross-host      3개 호스트 동시 DOWN (E2)      ⚑ NOISE_CROSS_HOST_CORRELATION_ENABLED
 [9] cascade         상위→하위 자원 연쇄 (E4)       ⚑ NOISE_MULTI_HOP_CASCADE_ENABLED
 [10] change-corr    IPL/변경이력 근접 알람 (E5)    ⚑ NOISE_CHANGE_CORRELATION_ENABLED
 [11] semantic-dup   운영자 주석 재발신 쌍 (B-7)    ⚑ 플래그 + 로컬 모델(docs/19)
─ SRE-Agent 연동 (§4.3 — off면 선택 시 안내) ────────
 [12] invest-trigger sev3 PAGE→자동 조사 submit     ⚑ INVESTIGATION_TRIGGER_ENABLED + sre_agent 서비스
─────────────────────────────────────────────────────
 [l] 메뉴 다시 표시  [v] 판정 대기 토글(현재 on)  [q] 종료
번호 입력> 4
[주입] MOCK-a1b2c3d4-1 전송 … MOCK-a1b2c3d4-2 전송 (간격 3s)
[판정] 1건째 tier=PAGE ✔ / 2건째 tier=SUPPRESS(dedup) ✔ — 기대 일치
번호 입력> q
```

**보조 — 비대화형 단발 주입 (자동화·도구 e2e용)**:

```bash
python scripts/mock_polestar_events.py --send dup-suppress
python scripts/mock_polestar_events.py --send cross-host --path redis
```

기동 옵션: `--host/--port`(TCP 대상, 기본 localhost:9100), `--path tcp|redis`(기본 tcp), `--redis-url`, `--decision-log`(기본 logs/alarm_decisions.jsonl), `--timeout`(판정 대기, 기본 30s), `--db-id`(기본 polestar_pg).

---

## 6. 구현 항목 및 산출물

| # | 작업 | 산출물 | 검증 |
|---|---|---|---|
| 1 | 시나리오 카탈로그 + 이벤트 빌더 | `scripts/mock_polestar_events.py` | 단위: 페이로드가 §2.2 스키마·§2.4 실측 샘플 형식(매핑 규칙) 준수, 메뉴 번호-시나리오 매핑 고정 |
| 2 | TCP 전송기 + Redis 폴백 | 〃 | 실측: alarm_server 경유 XADD 도달 확인 |
| 3 | 판정기 (JSONL 폴링·대조·감사 필드 표시) | 〃 | 실측: 주입 alarm_id의 tier 검출 |
| 4 | **대화형 메뉴 루프** (기동 점검·번호 입력·결과 출력·복귀, §3.1) | 〃 | 실측: 번호 입력→주입→판정→메뉴 복귀 시나리오 수동 확인 |
| 5 | 전제 조건 사전 점검 (플래그·서버 기동·픽스처) | 〃 | 미충족 시 명확한 에러 + 안내 후 메뉴 복귀 |
| 6 | 사용 가이드 절 추가 | `docs/20_plan60_feature_test_guide.md`에 §추가 (또는 docs/16 갱신) | 문서-실동작 일치 |
| 7 | 테스트 | `tests/test_scripts/` 또는 기존 위치 관례 — 빌더·카탈로그·메뉴 디스패치 단위 테스트 (전송·판정은 `--send` 경유 e2e 옵트인) | `pytest` 그린, 기존 713 passed 무회귀 |

원칙: **파이프라인(src/) 코드 무변경**. 판정 공용화를 위해 기존 스크립트에서 함수를 빼내는 리팩터링은 하지 않는다(Surgical Changes) — 필요 시 신규 파일 내 소규모 재구현.

---

## 7. 확인 게이트 (착수 시 사용자 인터뷰)

| # | 질문 | 권고 |
|---|---|---|
| G-1 | 주입 경로 기본값: TCP 실경로 vs Redis 직주입? | **TCP** (실경로 커버, §3.2) |
| G-2 | 기존 `noise_gate_scenario_test.py`와 통합 vs 신규 파일? | **신규 파일** (기존은 Plan 52 회귀용으로 유지, 목적 분리) |
| G-3 | E4(토폴로지)·E5(변경이력) 픽스처가 도커 폴스타에 없을 경우: 픽스처 SQL 추가를 본 계획 범위에 포함? | 실측 후 결정 (없으면 픽스처 추가는 소규모이므로 포함 권고) |
| G-4 | Plan 60 시나리오 범위: §4.2 전부 vs 기본(E1·E2·E4)만? | **전부** (B-7은 모델 미반입 시 자동 스킵 처리) |

---

## 8. 성공 기준

1. 프로그램을 띄운 뒤 **번호 입력만으로** 이벤트 주입부터 캔슬링 판정(✔/✘) 표시·메뉴 복귀까지 완료되고, 프로세스 종료 없이 반복 테스트할 수 있다.
2. `dup-suppress` 시나리오에서 2건째 이벤트가 SUPPRESS로 판정됨을 도구가 자동 확인한다 (캔슬링 검증의 최소 성공 사례).
3. Plan 60 플래그 on 시 §4.2 시나리오의 감사 필드가 도구 출력에 표시된다.
4. 도구 추가로 인한 기존 테스트 회귀 없음 (713 passed + 4 skipped 기준선 유지, `arch_check --ci` exit 0).
5. 전제 미충족(서버 미기동·플래그 off·픽스처 부재) 시 침묵 실패 없이 원인과 해결 방법이 출력된다.
