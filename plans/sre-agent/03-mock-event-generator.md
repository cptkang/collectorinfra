# 03. 폴스타 목업 이벤트 생성기 (Mock Event Generator)

> 작성일: 2026-07-24 · **이관일: 2026-07-24** (SREAgent → collectorinfra `plans/sre-agent/`, 통합 결정: collectorinfra D-118 / SREAgent D-021)
> **원본**: collectorinfra `plans/65-noise-cancellation-mock-event-generator.md`를 SREAgent로 이식. 시나리오(실측 ITSM 사례 S1~S8)·주입 방식은 유지하되, 주입 대상을 Plan 01의 수신부(단일 프로세스 MVP)로, 판정 검증 범위에 Plan 02 조사 트리거를 추가했다.
> **선행 계획**: `plans/01-event-noise-gate.md`(수신부·게이트·감사 JSONL이 주입/판정 대상)
> **신규 결정(본 계획 예약이었음)**: D-012 — 대체됨에 따라 등재하지 않는다(결번 처리).
> **상태**: **대체됨(superseded, 2026-07-24)** — 주입 대상이던 SREAgent 자체 수신부·게이트(Plan 01)가 통합으로 소멸했다. 목업 주입·판정 대조는 **collectorinfra 원본 Plan 65(그쪽 게이트 대상)가 담당**한다.
> **잔존 유효분(Plan 65에 반영할 델타)**: §5의 `invest-trigger` 시나리오 — PAGE 판정 시 게이트 훅이 `sre_investigate_alarm`(Plan 05)을 호출하고 브리핑을 수신하는 경로까지 확인하는 시나리오를 Plan 65 카탈로그에 추가한다(실 HolmesGPT 완주는 `RUN_E2E=1` 옵트인).
> **번호 체계 주의**: 본 문서의 D-번호는 SREAgent(이관 전) 결정 체계의 인용 — collectorinfra D-번호와 무관(폴더 README 참조).

---

## 1. 목적

폴스타 실계 연동 없이 **사전 정의된 이벤트를 번호 선택만으로 생성·주입**해, 수신→게이트→감사 파이프라인이 해당 이벤트를 기대 티어(SUPPRESS 등)로 판정하는지 사용자가 직접 확인한다.

- 실행 방식: **상주 프로세스 + 번호키 메뉴**(대화형). 번호 입력 → 주입 → 판정 자동 대조 → 결과 출력 → 메뉴 복귀.
- 비목표: 게이트·판정 로직 변경 없음(주입·관찰만). 폴스타 DB 신호(중요도·토폴로지 등)가 필요한 시나리오는 DB 픽스처 전제를 사전 점검하고, 미충족 시 사유를 출력하고 주입하지 않는다(침묵 실패 금지). E3 동적 baseline은 이벤트 주입으로 재현 불가(메트릭 테이블 조회 기반)라 범위 외.

## 2. 실측 ITSM 사례 기반 시나리오 (S1~S8)

원본이 실제 ITSM알리미 알림 13건(2026-06~07)에서 추출한 사례를 그대로 계승한다. 공통 형식: `[ITSM]<host> <host>(<역할#번호>) <항목> [<값> (<조건>[, N회 연속])]`, 운영자 후속 통보는 동일 본문 + `=> 담당자 …` 재발신.

| # | 사례 | 노이즈 캔슬링 관점 |
|---|---|---|
| S1 | 가용성 DOWN → 15분 후 UP | DOWN·해소(클리어) 페어 — sev0 상관 |
| S2 | 파일시스템 사용률 95%/90% 임계 | 임계치 알람, resourceName=마운트 경로 |
| S3 | 동일 알람 + 운영자 주석 재발신("담당자 통화"/"영향 없음") | 표현만 다른 동일 알람 — 주석 하베스팅(E7-a)·의미적 근접 중복 |
| S4 | 계획 IPL로 6대 동시 가용성 DOWN + "예정 작업" 주석 | 동시 다발 상관(E2) + 변경·작업 연관(E5) 복합 |
| S5 | 상위 원인(전기 작업·네트워크 다운) → 하위 장비 다발 | 연쇄(E4). 네트워크 장비는 `<장비ID>.<도메인>||(장애) <사이트명>` 이질 형식 |
| S6 | 가용성 DOWN → 주석 재발신 → 8분 후 동일 알람 재발 | 재발 반복(E1 recurrence) |
| S7 | 동일 호스트에서 가용성 DOWN + 서버 기동 지속시간 알람 동시 | **음성 대조군** — 상이한 알람 2건은 각각 독립 판정돼야 함(dedup 오탐 방지) |
| S8 | UPS 설비 경고·Oracle Down·"승인 바랍니다"(비알람) 혼재 | 파서 견고성(E7-c)·비알람 분류(E7-b) 재료 |

가명화: 담당자 실명은 `담당자 ○○○` 형식만 유지. DB 신호가 필요한 시나리오는 픽스처 서버명(`noise-test-*`)을, 텍스트 중심 시나리오는 실측 형식 호스트명을 쓴다.

## 3. 이벤트 스키마와 주입 경로 (D-012 예약)

**페이로드**: Plan 01 §4의 폴스타 단일행 JSON 스키마 그대로. `alarmId`는 `MOCK-<run_id>-<seq>`, `alarmTime`은 `%Y%m%d%H%M%S`. severity는 원본 가정 계승(가용성 DOWN=2, UP=0, 사용률 임계=1, 단락 테스트용만 3) — **착수 시 게이트 매트릭스와 대조해 확정**.

**주입 경로**:

```
TCP 단일행 JSON (포트: Plan 01 수신 설정값 `listen_port`, 기본 9100) → Plan 01 event_receiver → gate_pipeline
  → 노이즈 게이트 → logs/alarm_decisions.jsonl (판정·감사)
```

- **TCP(기본)**: 폴스타 실경로와 동일한 수신부·파싱을 포함해 검증한다("폴스타가 보낸 것과 동일한 목업" 취지). 전제: Plan 01 프로세스 기동.
- 수신부 미기동 시 **명확한 에러**를 출력한다(침묵적 폴백 금지). Plan 01이 향후 큐 분리(Redis Stream 등)를 도입하면 그때 직주입 폴백 경로를 추가 결정한다.

## 4. 구현 구조 — 단일 파일 `scripts/mock_polestar_events.py`

```
├── 시나리오 카탈로그 : SCENARIOS (번호 고정 — 이벤트 시퀀스 + 기대 티어 + 필요 플래그/픽스처)
├── 이벤트 빌더      : make_payload() — §3 스키마 단일행 JSON
├── 전송기          : TcpSender (stdlib socket)
├── 판정기          : logs/alarm_decisions.jsonl 폴링(기본 30s) → alarm_id별 tier·감사 필드 대조
└── 메뉴 루프       : 기동 시 전제 점검(TCP 도달성·JSONL 존재) → 번호 메뉴 → 주입·판정·결과 → 복귀
```

- 입력은 stdlib `input()` 루프(termios/curses 미의존). 보조 키: `l`(메뉴 재표시)·`v`(판정 대기 토글)·`q`(종료). 잘못된 번호·전제 미충족 시 사유 출력 후 미주입 복귀.
- 보조 모드 `--send <시나리오명>`: 비대화형 단발 실행(e2e·자동화용 — `RUN_E2E=1` 테스트가 이 모드를 사용).
- 의존성: stdlib만(신규 패키지 0). `scripts/` 소속으로 arch-check 계층 규칙 비대상.

## 5. 시나리오 카탈로그 (기대 판정)

| 시나리오 | 원사례 | 기대 결과 | 필요 조건 |
|---|---|---|---|
| `sev3-page` | S8 변형 | PAGE (단락) | 없음 — 플래그 전부 off에서 동작 |
| `low-suppress` | S2 | TICKET 또는 SUPPRESS(매트릭스) | 픽스처 중요도 |
| `maint-suppress` | S4 | SUPPRESS(유지보수) | 픽스처 IS_MAINTENANCE |
| `dup-suppress` | S6 | 2건째 SUPPRESS + recurrence 감사 | 없음 |
| `clear` | S1 | 2건째(UP) 통보 없음(자가복구 상관) | 없음 |
| `distinct-pair` | S7 | **각각 독립 판정**(음성 대조군) | 없음 |
| `cross-host` | S4/S5 | 대표 1건 통보 + 이후 멤버 step7.5 SUPPRESS | Phase 2 플래그 |
| `cascade` | S5 | root만 통보, 하위 SUPPRESS/DASHBOARD | Phase 2 플래그 + 픽스처 토폴로지 |
| `change-corr` | S4 | step9 promote 감사 표기 | Phase 2 플래그 + lifecycle 픽스처 |
| `annotation` | S3 | 억제 유지 + 주석 신호 감사 표기 | Phase 3 플래그 |
| `invest-trigger` | S8 변형(sev3) | PAGE + **Plan 02 조사 트리거 발화 확인** | `investigation_trigger_enabled` — 기본은 트리거 로그만 확인(실 HolmesGPT 호출은 비용 발생 → `RUN_E2E=1`에서만 완주) |

각 시나리오는 필요 플래그·픽스처를 실행 시 사전 점검하고, 판정기 출력에 티어 외 감사 필드(recurrence·correlation 메타·cascaded/root·change_nearby)를 함께 표시한다.

## 6. 수용 기준

1. 번호 입력만으로 주입~판정~복귀가 완료되고 반복 테스트 가능.
2. `dup-suppress` 2건째 SUPPRESS + recurrence 감사가 자동 확인됨.
3. `distinct-pair`가 오억제되지 않음(각각 독립 판정).
4. 파이프라인(`src/`) 코드 무변경 — 생성기는 주입·관찰만.
5. 전제 미충족 시나리오는 침묵 실패 없이 사유를 출력.
6. 기존 테스트 무회귀·`python scripts/arch_check.py --ci` exit 0.
