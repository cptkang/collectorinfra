# Capability Map: 장애진단 · 원인분석 잔여 (`plans/50` v2.1 · D-197 예정)

> **작성일**: 2026-09-02 · **근거**: `plans/50-fault-diagnosis-rca.md` §0(v2.1)
> **전제**: 조사 실행·증거 수집 도구·pull/push 트리거·인가 게이트는 **이미 구현됨**(D-118·D-122·D-124).
> 본 맵은 §0.3이 실측한 **잔여 6건(G1~G6)** 만을 대상으로 한다.
> **소유권 확정(§0.4-b)**: 상관·사전수집=`sre_agent` · 구간 앵커 SQL=`mcp_server` · 표현=`src`/`noise_gate`.

## 모듈

| Module id | 책임 | 소비자 | 패키지 | Depends on |
|---|---|---|---|---|
| `briefing-contract` | 브리핑 6요소 **키 계약 정합** — 생산자 키를 정본으로 소비자 2곳을 정렬하고, list·dict 전용 렌더러로 **repr 누출 제거**. 현재 `[한계]`·`[가설]`이 사용자에게 **도달하지 않는다**(G6) | 운영자(챗 응답 · WorkB 알림) | `sre_agent` + `src` + `noise_gate` | — |
| `incident-window-tools` | 폴스타·PromQL 조회 도구에 **사건 구간 앵커** 인자 추가 + 구간 내 **전 알람** 도구 신설 + **baseline** 조회(G1·G2·G3). 미지정 시 종전 SQL과 문자열 동일 | 조사 LLM(HolmesGPT) · `evidence-correlation` | `mcp_server` | — |
| `investigation-guidance` | 조사 지침 주입 배선 — `system_prompt_additions`를 넘기는 **프로덕션 호출부가 0건**이라 `MIDDLEWARE_FOCUS_NOTE`조차 주입되지 않는다(G5) | 조사 LLM · **`plans/51` §6 플레이북**(외부 소비자) | `sre_agent` | — |
| `incident-scope` | 사건 **기준시각·구간**의 파싱과 위임 계약 전달 — `sre_diagnose` 시각 인자 · pull은 질의 파싱(시/분 해상도) · push는 보유 중인 `event.alarmTime` 반영(A′-5) | `incident-window-tools` 호출자 | `src` + `sre_agent` | `incident-window-tools` |
| `evidence-correlation` | 구간 증거 **결정적 사전수집** + **상관 계산**(타임라인 병합 · z-score 이상탐지 · 선행성 · 단일서버 시차 · `notes`)(G4·G4-b) | `diagnosis-briefing` | `sre_agent` | `incident-window-tools` · `incident-scope` · `investigation-guidance` |
| `diagnosis-briefing` | 상관 결과를 브리핑에 반영 — **복수 가설 rank·confidence**(§7.2) · **상대시각 타임라인 `T-15m`**(§9.1) · 데이터 한계의 결정적 기록 | 운영자 | `sre_agent` | `evidence-correlation` · `briefing-contract` |

**Build order**: `briefing-contract` · `incident-window-tools` · `investigation-guidance` (**서로 독립 — 병렬 가능**)
→ `incident-scope` → `evidence-correlation` → `diagnosis-briefing`

> **구현 완료(2026-09-02 · D-197)** — 6모듈 전부. 구현 중 확정: `evidence-correlation`의 호출 수단은 계획 B′(`mcp` 클라이언트)를
> 선행 채택(holmes `ToolInvokeContext`가 LLM 객체 필수 — 실측). 검증 수치·잔여는 `plans/50` §0.3 해소 표·§18 v2.2.

## 경계가 이렇게 그어진 이유

- **`briefing-contract`가 맨 앞이고 아무것도 의존하지 않는 이유**: 출력 계약이 깨져 있는 동안에는
  **뒤 모듈의 산출물이 사용자에게 도달했는지 검증할 수 없다.** 지금 `limitations`(list)는 소비자
  키(`limitation`)와 어긋나 폴백 루프에서도 탈락한다 — 여기에 rank·confidence를 더 얹으면
  **그것도 똑같이 탈락한다.** 또한 이 모듈은 **단독으로 사용자 가시 효과**를 낸다(한계·가설이 처음 노출).
- **`incident-window-tools`와 `incident-scope`를 나누는 이유**: 전자는 **읽기 경계**(`mcp_server`)의
  SQL 인자이고 후자는 **조사 계약 + 질의 파싱**(`sre_agent`+`src`)이다. 패키지가 다르고 검증 방식도
  다르다(생성 SQL 문자열 단언 ↔ "어제 14시" 파싱 단언). 전자는 후자 없이도 값이 있다 — 조사 LLM이
  인자를 직접 쓸 수 있다.
- **`evidence-correlation`을 하나로 묶는 이유**: 사전수집과 상관 계산을 쪼개면 **사전수집이 소비자
  없는 코드**가 된다. 상관이 그 유일한 소비자이고, 둘의 수용 기준("사건 구간 증거로 선행 신호를
  결정적으로 산출한다")이 하나다.
- **`diagnosis-briefing`을 떼는 이유**: *"무엇을 계산했나"* 와 *"어떻게 보이나"* 는 다른 축이고,
  이 모듈만 **부모가 둘**이다(계산 결과 + 출력 계약). 계산이 맞아도 표현이 틀릴 수 있고 그 반대도 된다.
- **`investigation-guidance`가 작은데도 모듈인 이유**: 코드는 1곳 배선이지만 **소비자가 이 계획
  밖에도 있다** — `plans/85` §9 티어 1-①(Plan 51 §6 플레이북 프롬프트 편입)이 이 배선점 없이는
  착수 자체가 불가하다. 공유 인에이블러라 경계를 갖는다.
- **순환 없음**: `diagnosis-briefing` → `evidence-correlation` → `incident-window-tools` 단방향.
  `briefing-contract`는 아무것도 의존하지 않는다.

## 인터페이스 (경계 계약)

### ① 브리핑 dict — `briefing-contract`가 정의, 3곳이 준수

**정본은 생산자**(`sre_agent/application/briefing_builder.py`)다. 소비자는 생산자 키를 따른다.

```python
{
  "severity": {...},          # dict  — 중요도 헤더
  "summary": str,
  "timeline": [str, ...],     # list  — diagnosis-briefing이 상대시각 항목으로 교체
  "bottleneck": str,
  "cause": str,               # rank 1 가설의 cause(상관 있을 때)
  "root_cause_hypotheses": [  # diagnosis-briefing 추가(§7.2) — 상관 없으면 []
      {"rank": 1, "cause": str, "confidence": "high|medium|low", "evidence": [str], "reasoning": str}],
  "recommendation": {"items": [str, ...], "note": str},   # dict
  "limitations": [str, ...],  # list  — 현재 사용자에게 도달하지 않음(G6 대상)
  "citations_verified": bool,
  "hypotheses": [str, ...],   # list  — 인용 없어 강등된 단정
}
```

소비자 2곳: `src/nodes/fault_diagnosis.py::_briefing_to_text`(챗) ·
`noise_gate/application/nodes/alarm_notifier.py::_investigation_briefing_html`(알림).
**후자는 인라인 첨부와 후속 메시지가 공유하는 단일 렌더 함수**이므로(D-137 ⑥) 한 곳만 고치면 된다.

### ② `CorrelationResult` — `evidence-correlation`이 제공, `diagnosis-briefing`이 소비

```python
@dataclass(frozen=True)
class CorrelationResult:
    reference_time: str            # 기준 시각(ISO) — 좌표계 원점. now() 미사용
    timeline: list[TimelineItem]   # {t_offset_min:int, kind:str, detail:str} 시간순
    metric_findings: dict          # 지표별 {is_anomalous, kind, peak_value, peak_time, lead_lag_min}
    alarm_summary: dict            # 구간 알람 빈도·severity 추이·해소 여부
    leading_signal: str | None     # 가장 먼저 이상을 보인 신호(인과 후보)
    notes: list[str]               # 데이터 한계의 **결정적** 기록(정밀도·단면·결손)
```

`notes`는 `briefing.limitations`로 흘러간다 — **①의 계약이 성립해야 사용자에게 도달한다**(의존 방향의 근거).

### ③ 도구 인자 — `incident-window-tools`가 제공, `incident-scope`가 채운다

```
polestar_metric_trend(..., reference_time=None, lookback_minutes=None, baseline_periods=None)
polestar_alarm_history(..., reference_time=None, lookback_minutes=None)
polestar_incident_alarms(source, server_name, reference_time, lookback_minutes, alarm_name=None)  # 신설
sre_diagnose(question, server_name, hostname, db_id, target_state, reference_time=None, lookback_minutes=None)
```

**미지정(None)이면 종전 동작과 생성 SQL 문자열이 동일**하다 — 이것이 회귀 0의 검증 형태다.

## 산출물

| 모듈 | 스펙 |
|---|---|
| `briefing-contract` | `SPEC-briefing-contract.md` |
| `incident-window-tools` | `SPEC-incident-window-tools.md` |
| `investigation-guidance` | `SPEC-investigation-guidance.md` |
| `incident-scope` | `SPEC-incident-scope.md` |
| `evidence-correlation` | `SPEC-evidence-correlation.md` |
| `diagnosis-briefing` | `SPEC-diagnosis-briefing.md` |

계획·태스크: `tasks/plan-50.md` · `tasks/todo-50.md`

## 이 맵이 다루지 않는 것

- **조사 실행 자체** — `sre_agent` dispatcher·가드 6종·severity_judge·remediation은 손대지 않는다.
- **`src/diagnosis/` 서브그래프** — `plans/50` §5 폐기(D-118 위임). 노드 책임은 위 모듈로 흡수됐다.
- **`POST /diagnosis/analyze`** — §8.3 폐기(진입점은 챗·알람 2종으로 충분).
- **다중서버 연쇄 RCA · 진단 이력/피드백** — `plans/50` Phase C′(범위 밖) → **2026-09-09 `plans/91-WIP-fault-investigation-residual-consolidation.md` 1-1~1-4로 이관**(D-208).
- **실 LLM 조사 실행** — D-127 건별 승인 사항. 본 맵의 검증은 단위·통합 테스트까지다.
