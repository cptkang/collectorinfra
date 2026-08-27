# 구현 계획 — 차수 3-A (복합 질의 호스트 조사 오케스트레이션)

명세: `SPEC-composite-orchestration.md` · 지도: `CAPABILITY-MAP-composite-orchestration.md`
수용 기준 정본: `plans/78` §5 · 착수 판정 정본: `plans/80` §5.2

## 0. 선행 — 기준선 고정

전체 회귀를 **먼저** 돌려 기준선을 기록한다. 이후 매 모듈 완료 시 이 수와 대조한다.
현재 알려진 기준선(2026-08-27): **41 failed / 4625 passed / 5 errors / 2 xfailed**.
`2 xfailed`는 W0 갭 재현 테스트이며 **M1·M3이 해소하면서 마커를 뗀다** → 최종 `0 xfailed`,
passed는 그만큼 증가한다. 이 이동은 **회귀가 아니라 설계된 전이**다.

## 1. 컴포넌트와 의존

```
M1 prior-targets (utils)          ← 모든 것의 뿌리
 ├─ M2 investigation-audit        ← Tier 1. M7의 선행
 │   ├─ M7 fanout-compaction
 │   └─ M5 host-authz ──┐
 ├─ M3 target-fanout ─ M6 sufficiency-replan ─┼─ M8 diagnosis-consumption
 └─ M4 mcp-highlevel-tools ── M5 ─────────────┘
```

**순차 필수**: M1 → (M2 | M3 | M4) → (M5 | M6 | M7) → M8
**병렬 가능**: {M2, M3, M4} 서로 · {M5, M6, M7} 서로

## 2. 구현 순서와 이유

| # | 모듈 | 왜 이 자리인가 |
|---|---|---|
| 1 | **M1** `prior-targets` | 나머지 전부가 `TargetRef`를 쓴다. 타입 계약이 먼저 서지 않으면 뒤 모듈이 dict 키 오타 위에 쌓인다 |
| 2 | **M2** `investigation-audit` | **Tier 1**(78 §4.6.2) — M7의 하드 선행. 지표 없이 축약·캐시의 이득을 판정할 수 없다. M5의 인가 판정 슬롯도 여기서 난다 |
| 3 | **M3** `target-fanout` | G3 해소. `T-G3` xfail 마커를 뗀다 |
| 4 | **M4** `mcp-highlevel-tools` | M1과 독립이나 `TargetRef.server_name`/`hostname` 분기(C-2)를 쓰므로 M1 뒤 |
| 5 | **M5** `host-authz` | 전파 배선(C-4)이 딸려 있어 M2(감사 슬롯)·M4(호출 경계) 뒤 |
| 6 | **M6** `sufficiency-replan` | M3의 fan-out 결과 위에서만 충족도를 잴 수 있다 |
| 7 | **M7** `fanout-compaction` | **Tier 2** — M2 완료가 규율상 선행 |
| 8 | **M8** `diagnosis-consumption` | M5(인가 거부 사유)·M6(미충족 사유)를 응답에 함께 실어야 완결 |

## 3. 리스크와 완화

| 리스크 | 실체 | 완화 |
|---|---|---|
| **R-A** 회귀 | 세 소비 경로의 대상 해소를 동시에 갈아끼운다 | 플래그 **기본 off** → 비트동일. off 경로 단언을 모듈마다 넣는다 |
| **R-B** 계층 위반 | C-1이 이미 한 번 드러났다 | 모듈 완료마다 `arch_check --ci` |
| **R-C** 비대칭 | 1단(`deepagents_tools`)·2단(`subagents`) 한쪽만 고치는 반복 실수 | **양쪽 주입 실측 테스트 2건**을 M1 수용 기준으로 (80 §5.4-⑤) |
| **R-D** 인가 우회 | role이 state에 없어(C-4) 전파 누락 시 **fail-open** | 판정 기본값을 **차단**으로. role 미상 → 거부 단언 |
| **R-E** 라우팅 오염 | 3-A 조건 위반 | 신규 테스트에 라우팅 단언 금지. 완료 시 grep으로 확인 |
| **R-F** 계획서 드리프트 | C-1~C-5가 78·80과 어긋난 채 남는다 | 구현 후 일괄 반영(Q5) — 그 전까지 **SPEC이 정본**임을 명시 |
| **R-G** 과금 | 3단 LLM 컬럼 지목 | mock LLM 전용. `RUN_E2E` 설정하지 않는다(D-127) |

## 4. 검증 체크포인트

각 모듈 완료 시 **전부** 통과해야 다음으로 간다:

1. 해당 모듈 신규 테스트 **전건 통과**
2. `python -m pytest -q --ignore=tests/e2e` — **failed·errors가 기준선과 동일**
3. `python scripts/arch_check.py --ci` **exit 0**
4. 플래그 off 경로 **회귀 0 단언** 존재
5. 라우팅 단언 **0건**(grep)

## 5. 병렬화 판단

`{M2, M3, M4}`와 `{M5, M6, M7}`은 형식상 병렬 가능하나 **순차로 간다**. 이유: 전부 같은 파일
(`process_query.py`·`config.py`·`state.py`)을 만진다 — 병렬 편집은 충돌하고, 회귀 실패 시
**책임 소재를 확정할 수 없다**(격리 사본 대조가 무의미해진다).

## 6. 범위 밖 (명시)

- **WU-18**(W3-2·3 경로 선택) — WU-06(S-2) 실선행
- e2e 수용 검증 — S-1 후(3-B)
- `sre_agent`·`mcp_server` 코드 변경 — 요구·계약만 전달
- 권고 **생성** — `remediation.py` 소관
- `plans/78`·`plans/80` 본문 정정 — 구현 후 일괄(Q5)
