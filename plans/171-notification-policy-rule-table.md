# 171. 알림 판정 규칙 표 — `decide_notification` 조기 종결 단계를 순서 있는 규칙 표로 (166 단계 6a · 스멜 G1)

> **작성일**: 2026-10-16 · **v1.0**
> **상태**: **완료**(2026-10-16 · 커밋 전 — 메인이 커밋) — `RULES` 13행 + `GateInputs` · `decide_notification` cc 110→5 · 정책 테스트 390 수정 0 · 계획 끝 회귀 실패 0(모듈 단위) · verifier 차분 94,800건 0 · Critical·Major 0 · §3·§4
> **상위**: `plans/166` §0.1 단계 6 · **계획 ID 6a**(스멜 G1 — §3.6 G1 행 · §5.6 W4 G1 행 · §5.2 W0 G1 골든). 같은 시간에 6b(O2 · `src/orchestration/apm_query.py`)가 병렬로 돈다 — 파일 겹침 0.
> **성격**: 동작 불변 구조 리팩터(D-337) — 반환 `NotificationDecision` 전 필드(tier·reason·priority·signals·fingerprint·stage·evidence)가 바이트까지 같다 · 단계 1 스냅샷 갱신 금지(166 §0.1 규칙 4) · 테스트 단언 변경 금지
> **계획 시작 SHA**: `1ad7c161`(RCA)
> **회귀 대상 파일(누적)**: 1 — `noise_gate/domain/notification_policy.py` *(누적 끝 — 계획 끝 회귀 1회 실행: §3)*
> **관련 결정**: D-337(리팩터 규약) · D-048(심각도3 절대 PAGE·수집 실패 보수적 PAGE — D-330 부기) · D-318(step 9 `is_routine=False` 비승격) · D-330 ③(`collection_failed_page_sev3_only` · shadow 「했을 판정」) · D-336(제니퍼 정책 경로 심각도3 유형 정책 — step 3 안) · D-303(회귀) · D-088(`noise_gate/domain` overfit 스캔)
> **적용 스킬**: `.claude/skills/refactoring/`

---

## 1. 범위

대상은 `noise_gate/domain/notification_policy.py`의 `decide_notification` 하나다(착수 시 443~1072줄 · 근사 cc **110** · 630줄 — `smell_metrics.py` 실측. 166 분석 시점 cc98에서 D-330·D-336 머지로 늘었다).

### 1.1 착수 시 단계 목록(실측 — 평가 순서 그대로)

| 순서 | 단계(현 주석 번호) | 결과 | 조건 |
|---:|---|---|---|
| — | step 1 실효 심각도 · step 2 수집 실패 보수화 · 보조 입력 | 사전 계산 | 무조건 |
| 1 | 0.5 비알람 사전분류 | SUPPRESS `non_alarm` | `non_alarm_filter_enabled` |
| 2 | 3 심각도3 단락(D-048 · D-336 제니퍼 정책 경로 PAGE/TICKET) | PAGE/TICKET `severity3` | 실효 심각도 3 |
| 3 | 4 해소·자가복구 | SUPPRESS/DASHBOARD `self_heal`·`resolved` | `is_clear` |
| 4 | 5 수집 실패 보수적 PAGE(D-330 ③ 플래그 on이면 통과 · shadow면 재귀 1회) | PAGE `collection_failed` | 수집 실패 · 실효 심각도≥1 · 플래그 off |
| — | (5 통과 뒤 `EVIDENCE_COLLECTION_FAILED` 표지 활성) | | |
| 5 | 6 유지보수 | SUPPRESS `maintenance` | |
| 6 | 6.2 침묵 규칙 | SUPPRESS `silence` | 규칙 주입 |
| 7 | 6.4 의존성(다홉/1홉) | SUPPRESS/DASHBOARD `dependency` | `dependency_suppression` |
| 8 | 6.5 인히비션 | SUPPRESS `inhibition` | |
| 9 | 6(중복 번호) 플래핑 | SUPPRESS `flapping` | |
| 10 | 7 스톰 | SUPPRESS `storm` | |
| 11 | 7.5 크로스-호스트 상관 | SUPPRESS `correlation` | |
| 12 | 7.6 크로스소스 상한(매트릭스 결과가 DASHBOARD 초과일 때만 — 아니면 통과) | DASHBOARD `cross_source` | `cross_source_cap` |
| 13 | 7.7 계획-무해 주석 | DASHBOARD `annotation` | |
| 끝 | 8 매트릭스 · 9 보조 조정(D-318) · 9.5 앱 영향 승격 | `matrix` | 항상 |

`page_review`(D-330 ①)는 이 함수 밖(`notification_gate_node`)이라 대상이 아니다.

### 1.2 처방(166 §3.6 G1 행)

- 조기 종결 단계(위 표 1~13)를 **순서 있는 규칙 표**(`RULES` 튜플 + for 루프 — 함수 튜플형 Chain of Responsibility)로 바꾼다. 규칙별 클래스 계층은 만들지 않는다.
- 입력은 `frozen dataclass GateInputs`로 모은다. 공개 시그니처 `decide_notification(...)`은 그대로 두고 내부에서 조립한다(호출부 3곳·테스트 무변경).
- step 8~9.5는 체인이 아니라 함수로 둔다(step 9 누적 조정 함수 분리).
- 단계 번호 정규화(주석) — 규칙 표 하나가 평가 순서의 단일 출처가 된다. 문서·결정이 참조하는 기존 번호(0.5·3·…·7.7)는 바꾸지 않는다.

### 1.3 수용 기준

1. `noise_gate/tests/test_plan166_w0_decision_matrix.py`(W0 골든) · `test_decision_snapshot.py` · 정책 테스트 6파일(`test_notification_policy.py`·`test_gate_off_regression.py`·`test_plan60_flags_off_regression.py`·`test_plan144_apm_policy.py`·`test_flapping_gate.py`·`test_min_severity_gate_handoff.py`)이 **수정 없이** 통과.
2. verifier가 변경 전 코드(`1ad7c161`)와 나란히 돌려 플래그 조합 × 심각도 0~3 × 해소·유지보수·침묵 × 수집 실패·제니퍼 정책·크로스소스·shadow 경우의 `NotificationDecision` 전 필드(evidence 키 순서 포함) 차분 0.
3. `decide_notification` 근사 cc ≤30(166 §5.8).
4. arch_check error 0 · overfit 신규 0(기준선 전면 재생성 금지) · ruff · mypy 통과.

## 2. Wave

단일 Wave(W1) — implementer 1 → verifier 1.

## 3. 결과 (W1 · 2026-10-16 · 커밋 전)

- **바뀐 파일**: `noise_gate/domain/notification_policy.py` 1개(+816 −502 · 1,082→1,396줄 — 규칙 함수별 독스트링·`GateInputs` 필드 선언으로 늘었다). 호출부 3곳·테스트 무변경.
- **구조**: `GateInputs`(frozen · 원 인자 15 + 플래그 11 + step 1·2·보조 입력 파생값 12 + `mark_collection_failed`) ← `_gate_inputs`(`_effective_severity`·`_context_signals`) · `_signals`·`_decision` 모듈 함수화 · 규칙 13개 `_rule_*` · `RULES: tuple[(라벨, 규칙, 표지)]` — 순서 0.5 → 3 → 4 → 5 → 6 → 6.2 → 6.4 → 6.5 → 6(플래핑) → 7 → 7.5 → 7.6 → 7.7 · 매트릭스 `_matrix_decision` = step 8 `_matrix_tier` + step 9 `_adjustment_signals`·`_adjusted_tier` + step 9.5 `_app_impact_promotion`. 수집 실패 표지는 「표지」 열(0.5·3·4·5 False · 6 이후·매트릭스 True)로 명시하고 `dataclasses.replace`로 적용.
- **cc(근사 · `smell_metrics.py`)**: `decide_notification` **110 → 5**(630→156줄 — 대부분 계약 독스트링) · 클로저 `_matrix_decision` 24 → 1 · 파일 최대 cc 15(`_gate_inputs`) · cc>20 함수 2 → 0.
- **테스트**: 정책 테스트 15파일 390 passed(W0 골든 `test_plan166_w0_decision_matrix`·`test_decision_snapshot`·§1.3의 6파일 포함 — 수정 0).
- **계획 끝 회귀(1회)**: `regress.py --base 1ad7c161 --files noise_gate/domain/notification_policy.py` 종료 0 — 파일 76 · 통과 1,960 · 실패 0 · arch·overfit·config_fallback rc=0 · ruff 위반 3·mypy 위반 5(이번 diff 줄 0 — 기존분). `[전체 회귀 권고]` 블록 없음. 범위: 모듈 단위 — 전체 미실행.
- **implementer 자체 차분**: 무작위 750,000건 불일치 0(scratchpad `harness/diff_fuzz.py`).
- **verifier 차분**: 변경 전 코드(`1ad7c161`)와 나란히 94,800건(+`now=None` 23,680건) `NotificationDecision` 전 필드·evidence 키 순서까지 차분 0 · 단계 15종·티어 4종 전부 도달 · 평가 순서 추적 19,750건 불일치 0 · 변이 6종(인히비션↔플래핑 교환·표지 오적용 등) 전부 검출(대조 0) · 예외 경로 동등. 하네스: scratchpad `verify171/`.

- **verifier 지적**: Critical 0 · Major 0 · Minor 3 — ① `STAGE_DESCRIPTIONS` 머리 주석이 옛 「decide_notification의 결정 지점」을 가리킴 → 「RULES 표의 규칙·_matrix_decision」으로 고침(팀 리드 · 주석만) ② 호출 1건 비용 약 4배(심각도2 5.5→23.0µs — 약 40필드 frozen dataclass를 조립·`replace`로 2회 생성) → §4 기록 ③ `GateInputs`·`RULES`가 공개 이름 → 166 §3.6·§4.2가 이 이름으로 처방했고 외부 사용 0이라 유지(되돌리는 비용: 이름 2개 치환).
- **이탈**: 6.2 `datetime.now(timezone.utc)`에 `# noqa: UP017`(리팩터 전 표기 보존 — 동작 동일하나 표기 통일은 리팩터 범위 밖).
- **행동 변경 후보**: 0건. 매뉴얼 미반영: 사용자 접점 변경 없음.

## 4. 후속

기록만 한다(이 계획에서 고치지 않음).

1. **`UTC`/`timezone.utc` 표기 통일** — step 5 shadow는 `UTC`, step 6.2는 `timezone.utc`. 같은 값이라 동작 무관 · 다음 이 파일 작업 시 `noqa` 제거와 함께.
2. **7.6 경로의 매트릭스 2회 계산** — 크로스소스 상한 조건이 참이고 매트릭스 결과가 DASHBOARD 이하이면 `_rule_cross_source_cap`과 끝의 `_matrix_decision`이 각각 계산한다(리팩터 전 클로저도 같았다 — 순수 계산이라 결과 동일). 필요하면 캐시.
3. **호출 비용 µs 단위 증가**(Minor ②) — 알람 1건당 수십 µs라 운영 영향 없음. 문제가 되면 `replace` 대신 표지만 별도 인자로 넘기는 방식으로 줄일 수 있다.
4. **파일 1,396줄(>1,000)** — 규칙 함수·`GateInputs`를 `notification_rules.py`로 옮기는 Move-only 분할은 166 §8 재측정(단계 8) 때 판단.
