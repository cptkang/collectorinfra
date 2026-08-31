# 28. 노이즈 캔슬링 운영자 가이드 — 알람 카드의 `확인`·`유효`·`노이즈` 버튼 활용법

> **대상**: 웹 UI(에이전트 채팅 화면)에서 알람 카드를 받는 **운영자**와, 게이트 플래그를 켜고
> 끄는 **운영 담당자**.
> **범위**: 카드에 붙은 버튼 3종이 각각 무엇을 바꾸는지, 노이즈 캔슬링 전체 흐름의 어느 지점에
> 개입하는지, 그래서 **어떤 순서로 쓰면 억제 품질이 올라가는지**.
> **근거 결정**: D-048(4-티어 게이트·재현율 우선)·D-048.11(E4 LLM 액션가능성 피드백 few-shot)·
> D-049(ack/incident 라이프사이클 계측)·D-035(결정적=판단 / LLM=주석 경계).
> **선행 문서**: `docs/16`(Plan 52 게이트 테스트 정본) · `docs/20`(Plan 60 기능 테스트) ·
> `plans/52-alarm-noise-cancellation.md`(설계 정본). 본 문서는 **테스트가 아니라 운영 사용법**만 다룬다.
> **실측일**: 2026-08-28 — 코드(`noise_gate/`, `src/api/routes/alarm.py`, `src/static/js/app.js`)와
> 리포지토리 `.env` 실제값으로 확인했다. 아래 플래그 상태는 **이 리포지토리의 `.env` 기준**이므로,
> 운영 서버에서는 §5.1 명령으로 자기 환경 값을 반드시 다시 확인할 것.

---

## 1. 30초 요약

| 버튼 | 한 줄 의미 | 즉시 효과 | 다음 알람에 미치는 영향 |
|---|---|---|---|
| **확인** | "내가 이 사건을 맡았다" | incident 상태 `open → acked` (담당·시각 기록) | **없음** — 억제/학습과 무관, MTTA 지표에만 반영 |
| **유효** | "이 알람은 조치가 필요했다" | 피드백 JSONL에 `valid` 1줄 적재(작성자·자원·서버 포함) | 같은 **알람명**의 다음 알람이 **승격(통보 강화)** 쪽으로 자문됨 |
| **노이즈** | "이 알람은 볼 필요 없었다" | 피드백 JSONL에 `noise` 1줄 적재 | 같은 **알람명**의 다음 알람이 **강등(억제)** 쪽으로 자문됨. 단 심각도 3은 불변 |

**핵심 원칙 3가지** (이걸 모르면 버튼을 잘못 쓴다):

1. **피드백은 명령이 아니라 자문이다.** `노이즈`를 눌러도 그 알람이 즉시 차단되지 않는다. LLM의
   보조 판단(`llm_actionability`)에 입력될 뿐이고, 최종 발송 판단은 결정적 규칙이 내린다(D-035).
2. **승격은 쉽고 강등은 어렵다(재현율 우선).** `유효`는 항상 1단계 승격으로 반영되지만, `노이즈`는
   심각도가 `NOISE_SUPPRESS_MAX_SEVERITY`(기본 2) 이하일 때만, 그리고 승격 신호가 하나도 없을 때만
   반영된다. **심각도 3(심각)은 어떤 피드백으로도 억제되지 않는다.**
3. **억제된 알람에는 피드백을 달 수 없다.** SUPPRESS 티어는 UI 카드로 뜨지 않는다. 즉 "잘못 억제한
   알람(오억제)"은 버튼으로 못 잡는다 — `logs/alarm_decisions.jsonl` 감사 파일로만 찾을 수 있다(§6.3).

---

## 2. 노이즈 캔슬링 전체 흐름 속에서 버튼의 위치

```
폴스타 알람 (TCP 9100)
   └─ noise_gate/alarm_server  →  Redis Stream(alarm:raw)
        └─ AlarmWorker (본체 프로세스 in-process)
             ├─ dedup / 재통보 창 (E1)
             ├─ alarm_context_enricher   신호 수집(중요도·유지보수·통보정책·토폴로지·변경이력)
             ├─ alarm_analyzer           LLM 분석 + 패턴 + ★ 피드백 few-shot 주입 ★
             ├─ (agentic_enricher)       옵션
             ├─ notification_gate        ★ 결정적 4-티어 판단 + 감사 기록 ★
             │     step0.5 비알람 → step3 심각도3=PAGE 단락 → step4 해소 → step6 유지보수
             │     → step6.4 의존성 → step6.5 인히비션 → step7 스톰 → step7.5 크로스호스트
             │     → step8 우선순위 매트릭스 → ★ step9 보조 조정(피드백 자문 소비) ★
             └─ alarm_notifier           티어별 라우팅
                   PAGE      → worKB 쪽지 발송 (+ incident open 이벤트)
                   TICKET    → 일배치 요약 큐 + UI 카드
                   DASHBOARD → UI 카드만
                   SUPPRESS  → 감사 기록만 (카드 없음)
```

운영자가 개입하는 지점은 딱 두 곳이다.

- **`확인`(ack)** — 파이프라인 **바깥**. PAGE로 승격된 사건의 대응 상태를 기록한다(사후).
- **`유효`/`노이즈`** — `alarm_analyzer`의 few-shot 입력으로 **되먹임(feedback loop)**. 다음 번 같은
  알람이 파이프라인을 지날 때 step9 보조 조정에 반영된다.

즉, **버튼은 "이번 알람"이 아니라 "다음 알람"을 바꾼다.** 이번 알람을 즉시 조용히 시키고 싶으면
버튼이 아니라 유지보수 모드·통보 정책·중요도 매핑 같은 **결정적 설정**을 써야 한다(§7.3).

---

## 3. 버튼별 정확한 동작

### 3.1 `확인` (ack)

| 항목 | 내용 |
|---|---|
| 표시 조건 | 카드 payload에 `incident_id`가 있을 때만 (`src/static/js/app.js:2499`) |
| 호출 | `POST /api/v1/alarm/incidents/{incident_id}/ack` |
| 저장 | PostgreSQL incident 테이블 — `acked_at`, `acked_by`(로그인 사용자) |
| 응답 | `{acked: true}` → 버튼이 `확인됨 · HH:MM:SS`로 바뀜 / `{acked: false}` → `이미 확인됨` |
| 실패 | `확인 실패 · 다시 시도` 표시, 카드는 유지 |

`incident_id`가 붙으려면 **세 조건이 모두** 참이어야 한다.

1. `NOISE_INCIDENT_TRACKING_ENABLED=true`
2. 그 알람의 티어가 **PAGE** (TICKET/DASHBOARD는 incident를 만들지 않는다 — 사건전환율 분모 정합)
3. API 프로세스가 incident open 이벤트를 받아 PG에 영속 성공(`iid > 0`)

**ack가 하는 일과 하지 않는 일**

- 한다: MTTA(평균 확인 시간) 계측, 관리자 화면 `열린 사건` 탭에서 목록 제거, 중복 대응 방지 표식.
- 하지 않는다: 알람 억제, 재통보 중단, 학습. ack는 노이즈 판단에 **전혀** 관여하지 않는다.
- 해소(resolved)와도 다르다. 해소는 폴스타의 해소 알람(severity 0)이나 자가복구 상관으로만 일어난다
  (D-003 읽기 전용 — 에이전트가 사건을 임의로 닫지 않는다).

관리자 화면(`/admin` → **열린 사건** 탭)에서도 같은 ack를 할 수 있다. 카드가 스크롤에 묻혔을 때는
이쪽이 편하다.

### 3.2 `유효` / `노이즈` (운영자 피드백)

| 항목 | 내용 |
|---|---|
| 표시 조건 | 피드백 기능이 **켜져 있을 때만** 표시(`GET /alarm/capabilities`로 판정 — Plan 83) |
| 호출 | `POST /api/v1/alarm/feedback` |
| 전송 필드 | `alarm_name`, `resource_name`, `pattern_type`(결정적 사전분류), `severity`, `db_id`, `server_name`, `note`, `label` |
| 권한 | **자기 존(zone) 알람만** — 다른 존이면 403 (Plan 83) |
| 철회 | 성공 메시지 옆 `취소` 링크 → `POST /alarm/feedback/retract` |
| 저장 | `NOISE_FEEDBACK_STORE_PATH`(기본 `logs/alarm_feedback.jsonl`)에 JSONL 1줄 append |
| 성공 | 두 버튼 비활성 + `피드백 감사합니다` |
| 503 | `피드백 비활성` — 게이트 또는 액션가능성 플래그가 꺼져 있음(§5) |
| 그 외 실패 | `전송 실패` — 버튼 재활성, 다시 누르면 됨 |

적재되는 한 줄의 예:

```json
{"ts":"2026-08-28T01:22:33+00:00","label":"noise","alarm_name":"CPU 사용률 임계 초과",
 "resource_name":"cpu_usage","pattern":"주기적","server_name":"","db_id":"","severity":1,"note":""}
```

> **Plan 83 반영**: 웹 UI가 `db_id`·`server_name`을 함께 보내고, 버튼 옆 입력란으로 `note`를
> 남길 수 있다(200자·민감정보 금지). 라벨에는 작성자(`labeled_by`)가 감사용으로 기록되며,
> **few-shot 프롬프트에는 실리지 않는다**(작성자 이름이 LLM 판단에 개입할 이유가 없다).
> `pattern`은 LLM 산출값이 아니라 **결정적 사전분류**를 저장한다 — 조회 키와 원천을 통일했다.

---

## 4. 피드백이 실제로 반영되는 경로

`노이즈`를 한 번 누르면 다음 알람에서 이런 일이 일어난다.

**① 유사 피드백 조회** (`noise_gate/infrastructure/feedback_store.py:85` `find_similar`)

- **`alarm_name` 완전 일치가 필수**다. 알람명이 다르면 아무리 같은 서버·같은 자원이어도 후보에서 빠진다.
- 후보 중 `resource_name` 일치 **+2점**, `pattern` 일치 **+1점**으로 랭킹, 동점이면 **최신 우선**.
- 최대 `NOISE_ACTIONABILITY_FEWSHOT_COUNT`건(기본 3)만 프롬프트에 들어간다.

> **실측 주의**: 저장할 때의 `pattern`은 UI가 보낸 **LLM 산출 `pattern_type`**이고, 조회할 때 비교하는
> `pattern`은 **결정적 사전분류(`pre_classification`)**다(`alarm_analyzer.py:244`). 값 도메인은 같지만
> (`첫 발생|주기적|급증|산발적`) 둘이 어긋나면 +1 가점만 못 받는다 — 후보에서 탈락하지는 않는다.

**② 프롬프트 주입** — 조회된 예시가 `[운영자 피드백 — 유사 과거 알람에 대한 운영자 라벨]` 섹션으로
렌더되어 알람 분석 프롬프트에 붙는다. **추가 LLM 호출은 없다**(기존 응답에 필드 하나가 늘 뿐이라
비용·지연 증가가 없다). 섹션이 없으면 LLM은 `llm_actionability=null`을 출력한다.

**③ 게이트 step9 소비** (`noise_gate/domain/notification_policy.py:434`)

```
llm_actionability == "actionable"  → promote 목록에 추가 (조건 없음)
llm_actionability == "noise"       → demote 목록에 추가 (단 실효심각도 ≤ suppress_max_severity)
promote가 하나라도 있으면 → 1단계 승격, demote는 전부 무시
promote가 없고 demote만 있으면 → 1단계 강등 (하한 SUPPRESS)
```

`promote`에는 피드백 말고도 `폴스타 통보 정책(notify)`, `비일상 패턴(is_routine=False)`,
`변경 근접(원인성)`이 들어온다. **이 중 하나라도 있으면 `노이즈` 라벨은 그 회차에 무시된다.**

**④ 감사 기록** — 실제로 소비된 값이 `logs/alarm_decisions.jsonl`의 `signals.llm_actionability`에
남는다. 피드백이 반영됐는지 확인하는 유일한 확실한 방법이다(§8.3).

### 4.1 피드백이 **못 하는 일**

| 기대 | 실제 |
|---|---|
| "노이즈 누르면 이 알람 안 온다" | 아니다. 최대 **1단계** 강등이고, 다음 회차부터다 |
| "심각한 알람도 노이즈로 끌 수 있다" | 아니다. 심각도 3은 step3에서 즉시 PAGE 단락 — step9에 도달조차 안 한다 |
| "노이즈 여러 번 누르면 더 세게 억제된다" | 아니다. 최근 3건이 예시로 들어갈 뿐, 횟수 가중치는 없다 |
| "서버별로 따로 학습된다" | 아니다. 조회 키는 알람명(+자원명 가점)이다. 서버 구분은 UI 경로에 없다 |
| "PAGE→SUPPRESS로 한 번에 내릴 수 있다" | 아니다. 티어 이동은 1단계로 제한된다 |

---

## 4-A. 표시 레벨 — 어떤 알람까지 화면에 띄울지 고르기 (Plan 83)

헤더의 `알림 수신` 토글 옆 셀렉트로 **개인 표시 범위**를 고른다. 설정은 브라우저에 저장되며
(localStorage) 기기마다 따로 관리된다.

| 레벨 | 보이는 티어 | 언제 쓰나 |
|---|---|---|
| 긴급만 | PAGE | 당직·야간 — 즉시 대응할 것만 |
| 통보 대상 | PAGE, TICKET | 일반 운영 |
| **전체 (기본)** | PAGE, TICKET, DASHBOARD | 관제 — 기본값이며 종전과 같은 범위다 |
| 억제 포함(감사) | 전부 + SUPPRESS | **관리자 전용** — 오억제 점검 |

**티어 기준인 이유**: 심각도로 거르면 "심각도 1인데 승격돼 PAGE가 된 알람"을 놓친다. 게이트의
최종 산출물인 티어로 걸러야 재현율 우선 원칙과 어긋나지 않는다.

**권한과 선호는 다르다.** 존(zone)과 SUPPRESS 수신은 **서버가** 판정하고, page/ticket/dashboard
사이의 표시는 브라우저가 고른다. 그래서 레벨을 바꿔도 볼 수 없던 알람이 보이게 되지는 않는다.

**억제 포함(감사) 레벨의 전제**: SUPPRESS는 기본적으로 SSE로 발행되지 않는다.
`NOISE_SSE_SUPPRESSED_ENABLED=true`로 켜야 선택지에 나타나며, 관리자에게만 전송된다.
켜면 억제된 알람이 전부 흐르므로 억제율이 높은 환경에서는 **가장 큰 트래픽**이 된다 —
상시 켜두기보다 점검 기간에만 켜는 편이 낫다. 이 레벨은 §6.3 ②(감사 파일 tail)를 UI로
대체하는 용도다.

---

## 4-B. PAGE 알람에 붙는 **자동 조사 브리핑** — 어디서 오는 건가

게이트가 PAGE로 판정하면 통보에 **조사 브리핑**이 붙을 수 있다. 이건 노이즈 게이트가 만든 게
아니라 **별도 프로세스(`sre_agent`)가 HolmesGPT로 조사한 결과**다. 운영자가 알아야 할 건 세 가지다.

**① `sre_agent`는 HolmesGPT가 도는 프로세스다 — 추론 서버가 아니다.**
HolmesGPT는 SDK로 임포트돼 `sre_agent` 프로세스 안에서 ReAct 루프를 돌린다(GPU 불요).
**LLM 추론은 별도 백엔드**(사내 vLLM)가 담당한다. "HolmesGPT를 vLLM에서 돌린다"가 아니라
"HolmesGPT가 부르는 조사 LLM을 vLLM이 서빙한다"가 맞다.

**② 사내 운영에서 조사 LLM은 사실상 vLLM이다.**
조사는 네이티브 tool-calling이 필수인데 FabriX는 프로토콜상 불가(확정), Gemini는 실 폴스타
데이터 송신 금지(D-120)라 남는 선택지가 별도 vLLM이다(2026-08-25 결정).

**③ 조사가 없어도 노이즈 캔슬링은 완전히 돈다.**
조사 서비스나 vLLM이 없으면 조사만 `stub`으로 떨어지고 **게이트 판정·통보·피드백 버튼은 전부
정상**이다. 브리핑만 빠지고 사유가 감사에 남는다 — 이 가이드의 내용은 조사 없이도 그대로 유효하다.

**현재 상태(2026-08-28 실측)**: `.env`에 `NOISE_INVESTIGATION_*` 키가 0건이라 자동 조사
트리거는 **off**다. 즉 지금은 브리핑이 붙지 않는다.

> 상세는 **`docs/26_sre_agent_guide.md`** §1.3(무엇이 어디서 도는가)·§5.6(LLM 백엔드 배선),
> 엔드투엔드 기동은 `docs/23_plan66_mvp_test_guide.md` §7-V.

---

## 5. 활성화 상태 확인 및 켜는 방법

### 5.1 현재 상태 확인

```bash
grep -E "^NOISE_(ENABLE_NOISE_GATE|ENABLE_LLM_ACTIONABILITY|FEEDBACK_STORE|ACTIONABILITY|INCIDENT_TRACKING|SSE_BRIDGE)" .env
```

**이 리포지토리 `.env` 실측값(2026-08-28)**:

| 플래그 | 값 | 의미 |
|---|---|---|
| `NOISE_ENABLE_NOISE_GATE` | `true` | 게이트 동작 중(4-티어 판단·감사) |
| `NOISE_ENABLE_LLM_ACTIONABILITY` | **`false`** | **피드백 버튼을 누르면 503 → `피드백 비활성`** |
| `NOISE_FEEDBACK_STORE_ENABLED` | `true` | (액션가능성이 켜지면) 적재 활성 |
| `NOISE_FEEDBACK_STORE_PATH` | `logs/alarm_feedback.jsonl` | 적재 경로 |
| `NOISE_ACTIONABILITY_FEWSHOT_COUNT` | `3` | few-shot 최대 건수 |
| `NOISE_INCIDENT_TRACKING_ENABLED` | **`false`** | **`확인` 버튼이 아예 표시되지 않음** |
| `NOISE_SSE_SUPPRESSED_ENABLED` | **`false`** | 억제 포함(감사) 레벨 선택지가 나타나지 않음 (Plan 83) |
| `NOISE_FEEDBACK_STORE_MAX_LINES` | `20000` | 피드백 파일 회전 상한 = 조회 창 (Plan 83) |
| `NOISE_SSE_BRIDGE_ENABLED` | **`false`** | 워커가 만든 카드가 웹 UI로 중계되지 않음 |

> 지금 이 설정에서 웹 UI에 뜨는 알람 카드는 **테스트 엔드포인트(`POST /api/v1/alarm/analyze`,
> `push_to_ui=true`) 경로**뿐이며, 그 카드에는 `incident_id`가 없어 `확인` 버튼이 안 나온다
> (`src/api/routes/alarm.py:830`). 운영 화면에서 `확인` 버튼이 보인다면 그 서버는 이미
> `NOISE_INCIDENT_TRACKING_ENABLED=true`인 것이다 — §5.1 명령으로 해당 서버 `.env`를 확인할 것.

### 5.2 카드가 UI에 뜨는 조건 (버튼 이전 문제)

| 티어 | worKB 발송 | 웹 UI 카드 | 카드에 `확인` 버튼 |
|---|---|---|---|
| PAGE | ○ | incident 트래커 ON일 때만(incident open 재발행) | ○ |
| TICKET | × (일배치 큐) | SSE 브리지 ON(워커 경로) 또는 API 경로 | × |
| DASHBOARD | × | 위와 동일 | × |
| SUPPRESS | × | **×** | × |

### 5.3 켜는 순서

```bash
# .env — 인라인 주석 금지(pydantic-settings가 값에 포함시킨다)
NOISE_ENABLE_LLM_ACTIONABILITY=true      # 피드백 버튼 + few-shot 자문 활성
NOISE_FEEDBACK_STORE_ENABLED=true
NOISE_SSE_BRIDGE_ENABLED=true            # 워커가 만든 TICKET/DASHBOARD 카드를 UI로 중계
NOISE_INCIDENT_TRACKING_ENABLED=true     # PAGE 사건 라이프사이클 + 확인 버튼 + MTTA/MTTR
```

- 세 플래그 모두 **API 서버 재기동**이 필요하다(기동 시 1회 배선).
- `NOISE_INCIDENT_TRACKING_ENABLED=true`는 **PostgreSQL 연결(`db_connection_string`)과 Redis**를
  요구한다. 테이블은 기동 시 자동 생성된다. 실패해도 서버는 뜨고 계측만 비활성된다
  (기동 로그에 `incident 계측 시작 실패 (계측 비활성)` 경고).
- 기동 로그에서 확인할 문장: `알람 분석 워커 시작`, `알람 SSE 브리지 구독 시작`, `incident 계측 시작`.

---

## 6. 운영 루프 — 이렇게 쓰면 효과가 난다

### 6.1 알람을 받은 순간 (초 단위)

1. 카드의 **요약 / 추정 원인 / 권고 조치**와 패턴 배지(`[주기적 · 일상 알람]`, `· 확인 필요`)를 읽는다.
2. 내가 대응한다면 **`확인`**을 누른다 — 다른 사람이 중복으로 달려들지 않게 하는 표식이다.
3. 대응이 끝났거나 볼 필요가 없다고 판단되면 **그때** `유효` / `노이즈`를 누른다.

> **순서가 중요하다.** 확인 → 조치 → 판단(라벨) 순이다. 카드를 보자마자 반사적으로 `노이즈`를 누르면
> 실제로는 조치가 필요했던 알람을 억제 방향으로 학습시키게 된다.

### 6.2 라벨링 기준

| 라벨 | 이럴 때 | 예 |
|---|---|---|
| **유효** | 실제로 조치했다 / 조사가 필요했다 / 다른 장애의 전조였다 | 임계 초과 후 실제 리소스 증설, 프로세스 재기동 |
| **노이즈** | 아무 조치도 필요 없었다 + 재발해도 똑같이 무시할 것이다 | 배치 시간대 CPU 급증, 계획 작업 중 발생, 이미 아는 자가복구 |
| **누르지 않는다** | 애매하다 / 판단할 정보가 부족하다 / 남의 담당이다 | 무라벨은 `llm_actionability=null` → 게이트 무영향(안전한 기본값) |

- **애매하면 누르지 않는 것이 정답이다.** 시스템 전체가 재현율 우선(놓치느니 시끄러운 편)으로
  설계돼 있고, 무라벨은 그 기본값을 그대로 유지한다.
- 같은 알람명에 **상반된 라벨**이 쌓이면 최신 3건이 예시로 들어가므로 **최근 판단이 이긴다**.
  담당자가 바뀌어 기준이 달라졌다면 새 라벨을 몇 건 남기면 자연히 갱신된다.
- 라벨은 **사람만** 남긴다. 자동 등록·일괄 스크립트 적재는 금지다 — 오염이 자기강화되는 루프가 된다
  (Known Mistakes: LLM 자동 등록 오염).

### 6.3 주간 리뷰 (운영 담당자)

```bash
# ① 티어 분포·억제율·메타경보
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/alarm/metrics | jq

# ② 억제된 알람 목록 — 오억제(false negative) 점검. UI에 안 뜨므로 여기서만 볼 수 있다
jq -r 'select(.tier=="suppress") | [.ts, .alarm_id, .reason] | @tsv' logs/alarm_decisions.jsonl | tail -50

# ③ 피드백 라벨 집계 — 알람명별 유효/노이즈
jq -r '[.alarm_name, .label] | @tsv' logs/alarm_feedback.jsonl | sort | uniq -c | sort -rn | head -20
```

**보는 값**

| 지표 | 정상 감각 | 이상하면 |
|---|---|---|
| `suppress_ratio` | 과도하게 높으면(기본 임계 0.9) `meta_alerts`에 `high_suppress_ratio` | 억제 규칙이 과하다 — §7.3 결정적 설정을 재점검 |
| `actionable_ratio` | (page+ticket)/total | 너무 낮으면 놓치는 알람 의심, 너무 높으면 캔슬링 효과 미미 |
| `mtta_seconds` | 트래커 ON에서만 값 존재 | null이면 `unavailable_metrics.reason` 확인 |
| `open_incident_count` | 계속 증가 | ack가 안 되고 있다 — 운영 습관 문제 |
| `meta_alerts` 의 `no_events` | 빈 배열이 정상 | 알람 수신 자체가 끊겼을 수 있다(수신부·Redis 점검) |

**②가 가장 중요하다.** 버튼으로는 절대 알 수 없는 "잘못 억제한 알람"을 찾는 유일한 경로다.
억제 사유(`reason`)에 `의존성`·`스톰`·`플래핑`·`LLM 노이즈 판단(피드백)`이 붙은 건들을 훑어보고,
실제로는 봤어야 할 알람이 섞여 있으면 해당 규칙을 되돌린다.

### 6.4 라벨이 쌓인 다음 — 결정적 규칙으로 승격시키기

같은 알람명에 `노이즈`가 반복해서 쌓인다면, 그건 **피드백으로 계속 눌러야 할 대상이 아니라
결정적 설정으로 옮겨야 할 대상**이다. 피드백은 LLM 자문이라 100% 재현되지 않지만, 아래 설정은
매번 같은 결과를 낸다.

| 상황 | 옮겨갈 결정적 설정 |
|---|---|
| 특정 자원군이 항상 저우선 | 폴스타 `IMPORTANCE_ID` 매핑 / `NOISE_IMPORTANCE_VALUE_MAP_CSV` |
| 계획 작업 중 발생 | 폴스타 유지보수 모드(`IS_MAINTENANCE`) — 게이트 step6에서 SUPPRESS |
| 해당 알람 자체를 통보 대상에서 제외 | 폴스타 통보 정책(`cmm_alarm_def_noti*`) — step9 강등 신호 |
| 상위 장애의 연쇄 알람 | `NOISE_DEPENDENCY_SUPPRESSION=true` / `NOISE_MULTI_HOP_CASCADE_ENABLED=true` |
| 같은 서버에서 한꺼번에 쏟아짐 | `NOISE_STORM_GROUPING_ENABLED=true` |
| 상태가 진동(발생/해소 반복) | `NOISE_FLAPPING_ENABLED=true` |
| 상위 심각도 발생 중 하위 소음 | `NOISE_INHIBITION_ENABLED=true` |

> 이 플래그들은 현재 전부 `false`다(§5.1 명령으로 확인). 하나씩 켜고 §6.3 ②로 오억제를 확인한 뒤
> 다음 것을 켜는 방식이 안전하다 — 한꺼번에 켜면 어느 규칙이 억제했는지 사유 추적이 어려워진다.

---

## 7. 자주 겪는 상황

### 7.1 `피드백 비활성`이 뜬다
`NOISE_ENABLE_NOISE_GATE` 또는 `NOISE_ENABLE_LLM_ACTIONABILITY`가 `false`다(§5.3). 이 상태에서는
버튼이 보여도 적재되지 않는다.

### 7.2 `확인` 버튼이 없다
정상일 수 있다. ① 티어가 PAGE가 아니거나(TICKET/DASHBOARD 카드) ② `NOISE_INCIDENT_TRACKING_ENABLED`가
`false`이거나 ③ PG 영속에 실패했다. 서버 기동 로그의 `incident 계측 시작` 여부부터 본다.

### 7.3 특정 알람을 지금 당장 멈춰야 한다
피드백 버튼으로는 안 된다. 폴스타 유지보수 모드나 통보 정책 같은 결정적 설정을 쓰거나(§6.4),
긴급하면 `ALARM_MIN_SEVERITY` 상향 같은 전역 조치를 검토한다. 다만 전역 조치는 다른 알람도 같이
막으므로 최후 수단이다.

### 7.4 피드백을 남겼는데 다음에도 똑같이 온다
정상 동작일 수 있다. 확인 순서:

```bash
# ① 적재됐는가
tail -3 logs/alarm_feedback.jsonl

# ② 알람명이 정확히 같은가 (find_similar는 alarm_name 완전 일치가 필수)
jq -r .alarm_name logs/alarm_feedback.jsonl | sort -u

# ③ 게이트가 실제로 소비했는가
jq -r 'select(.signals.llm_actionability != null) | [.ts,.tier,.signals.llm_actionability,.reason] | @tsv' \
  logs/alarm_decisions.jsonl | tail -20
```

③에서 `reason`에 `(강등 신호 LLM 노이즈 판단(피드백)는 승격 우선으로 무시)`가 보이면, 승격 신호와
경쟁해서 진 것이다 — 설계상 정상이다(재현율 우선). 심각도 3이면 애초에 step9에 도달하지 않는다.

### 7.5 API로 직접 피드백 남기기 (서버명·메모 포함)

```bash
curl -s -X POST http://localhost:8000/api/v1/alarm/feedback \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"alarm_name":"CPU 사용률 임계 초과","label":"noise",
       "resource_name":"cpu_usage","pattern_type":"주기적","severity":1,
       "server_name":"was01","db_id":"polestar_cm_gp","note":"일 2시 배치 구간"}'
```

`note`는 few-shot 예시 줄 끝에 그대로 붙어 LLM에 전달된다. **짧고 사실만** 적는다(민감정보·계정·키 금지).

---

## 8. 빠른 참조

| 항목 | 값 |
|---|---|
| 피드백 API | `POST /api/v1/alarm/feedback` (게이트+액션가능성 ON 필요, 아니면 503) |
| 피드백 철회 API | `POST /api/v1/alarm/feedback/retract` (Plan 83) |
| 피드백 집계 API | `GET /api/v1/alarm/feedback/summary` — 관리자 화면 `알람 피드백` 탭 (Plan 83) |
| 기능 가용성 API | `GET /api/v1/alarm/capabilities` — UI가 버튼 렌더를 판단 (Plan 83) |
| ack API | `POST /api/v1/alarm/incidents/{id}/ack` (트래커 ON 필요, 아니면 503) |
| 열린 사건 목록 | `GET /api/v1/alarm/incidents?status=open&limit=100` |
| 운영 지표 | `GET /api/v1/alarm/metrics` |
| 피드백 적재 파일 | `logs/alarm_feedback.jsonl` |
| 판단 감사 파일 | `logs/alarm_decisions.jsonl` (SUPPRESS 포함 — 억제 ≠ 삭제) |
| 관리자 화면 | `/admin` → **열린 사건** 탭 |
| 카드 렌더 코드 | `src/static/js/app.js:2497`(ack) · `:2507`(피드백) |
| few-shot 조회 | `noise_gate/infrastructure/feedback_store.py:85` |
| step9 소비 | `noise_gate/domain/notification_policy.py:434` |

**관련 문서**: `docs/16`(게이트 테스트 정본) · `docs/20`(Plan 60 기능 테스트) · `docs/26_sre_agent_guide.md`(자동 조사·조사 LLM 백엔드 — §4-B) · `docs/25`(호스트 조사
부하 가드) · `plans/52-alarm-noise-cancellation.md`(설계) · `plans/54-noise-cancellation-dashboard.md`(대시보드)
