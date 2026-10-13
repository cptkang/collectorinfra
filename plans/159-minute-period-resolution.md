# 159. 분 단위 기간 해석 — 「최근 30분」·「10분치」가 시간 되묻기로 끝나 제니퍼 조회에 닿지 않는 결함 교정

> **작성일**: 2026-10-13 · **v1.0**(최초) · **v1.1**(2026-10-13 — 게이트 G-1~G-6 권고안 확정 · D-326) · **v1.2**(2026-10-13 — W0~W5 구현 완료 · 검증 교정) · **재채번**(2026-10-13 — 원 plans/153 · `origin/main` 병합 시 원격 apm_noise가 같은 번호를 먼저 푸시)
> **상태**: **완료(2026-10-13)** — W0~W5 · verifier Critical 0(High 2·Medium 2·Low 1 전건 교정) · 계획 끝 모듈 회귀 신규 실패 0 · 152 E1 87.6% → 97.7%(악화 0) · 매뉴얼 갱신 · 결정 **D-326**(D-309 ④ 부분 개정 · 구현 실측 부기) · 후속은 §9 「남긴 것」
> **요청 원문(2026-10-13)**: *"분 단위 기간 결함 후속 계획 작성하라"* → 게이트 답 *"권고대로 정의하고 착수하라."*
> **발견 경위**: `plans/152`(제니퍼 APM 조회 벤치마크) 외부망 E1 — 남은 실패 16턴 중 **12턴 + 연쇄 4턴**이 이 결함이다(152 §9 · §10.7). 가짜 LLM이 정답 계획을 넣어도 실패하므로 few-shot으로 고칠 수 없는 **결정적 경로 결함**이다.
> **성격**: 현행 실측 + 결정 충돌 정리 + 설계 + 작업 분해.
> **상위·인접 계획**: `plans/152`(발견 · 효과 측정 수단) · `plans/122`(시간 해석 트랙 T — 해석기 정본) · `plans/134` W6(APM 창 = 요청 해석 · D-313) · `plans/132`(「최근 5분 내 종료된 프로세스」 3회차 검증)
> **관련 결정**: **D-309 ④(개정 대상)** · D-291 ①(해석 불가 = 되묻기) · D-275 · D-313 ①(APM 창) · D-319(만료 필터 흔적) · D-131(사본 금지) · D-162(신규 플래그 기본 off) · D-255(매뉴얼) · D-303(회귀) · D-127·D-240(실 LLM = 로컬 MLX)
> **실측 기준**: 2026-10-13 `main` HEAD `6810336`. `file:line`은 이 시점 값이다 — W0에서 다시 대조한다.
> **계획 시작 SHA**: `6810336` · **회귀 대상 파일**: `src/domain/time_spec.py` · `src/domain/time_expr.py` · `src/domain/disclosure.py` · `src/orchestration/apm_query.py` · `src/prompts/input_parser.py` · `scripts/scenario/catalog.py` · `scripts/apm_bench/fake_llm.py` · `scripts/apm_bench/judge.py` · `scripts/manual/content/{user,admin}.md` · `src/static/manual/{user,admin}.html` · 테스트 신규 5(`tests/test_domain/test_plan159_{minute_period,verify}.py` · `tests/test_orchestration/test_plan159_{minute_consumers,ladder_passthrough,verify}.py`) · 개정 6(`tests/test_domain/test_{time_spec,time_expr,query_time}.py` · `tests/test_nodes/test_plan122_t3_input_parser.py` · `tests/test_orchestration/test_plan132_round3_verify.py` · `tests/test_plan122_t_verify.py`)

---

## 0. 요약

「최근 30분 응답시간」·「느린 거래도 10분치」처럼 **분 단위 기간**을 말하면 요청 전단 `input_parser`의 시간 해석이 `clarify=unresolved`로 끝나 그래프가 `input_parser → END`로 닫힌다. 제니퍼(APM) 처리기는 분 단위 창(`lookback_minutes`)을 이미 다루지만 요청이 거기까지 오지 않는다.

원인은 해석기에 **분 단위가 없는 것**이고, 이는 D-309 ④가 의도한 동작(「해석기 단위에 분이 없어 되묻는다」 — 조용히 기본값으로 가지 않게)이다. 그래서 이 계획은 **결정 개정(D-309 ④)을 사용자 확정 게이트로 먼저 받고**, 그 뒤 도메인 해석기에 `minute` 단위를 넣는다. 주체별로 결과가 다르다.

| 주체 | 분 단위 결과(권고) | 이유 |
|---|---|---|
| 사건(`event` — 제니퍼 창 · 알람 이력 `ctime`) | 기준 시각까지 **분 그대로** `[지금 − N분, 지금)` | 원천이 분·초 정밀도를 갖는다 |
| 성능 통계(`metric` — 폴스타 `cmm_metric_stat_h` 이상) | **시 단위로 넓히고 고지**(G-2 권고 (a)) | 통계 최소 입도가 1시간이라 30분 구간이 존재하지 않는다 |

조사 중 **같은 계열의 조용한 오해석 2건**을 더 찾았다(§1.3). 「최근 1시간 30분」은 30분을 버리고 1시간으로, 「오늘 14시부터」·「어제 10시 30분부터」는 시각을 버리고 하루 전체로 해석된다. 앞의 것은 이 계획 범위(합성 기간)에 넣고, 시각 시작점은 G-5로 범위를 묻는다.

---

## 1. 문제

### 1.1 재현 (2026-10-13 · HEAD `6810336` · 기준 시각 2026-10-13 10:37 KST)

| 질의 | 현재 결과 | 기대 |
|---|---|---|
| 「{인스턴스} 최근 30분 응답시간」 | `clarify=unresolved` → 「[조회 기간] … 다시 알려 주세요」 | 제니퍼 `app_health` 30분 창 |
| 「느린 거래도 10분치」(후속 턴) | 같음 — 앞 턴 대상 승계도 끊김(152 연쇄 4턴) | `slow_tx` 10분 창 |
| 「최근 10분 알람」 | 같음(`tests/test_plan122_t_verify.py:621`이 이 동작을 고정) | 알람 이력 `ctime` `[10:27, 10:37)` |
| 「최근 5분 CPU 사용률」 | 같음(같은 테스트) | G-2 결정대로 |
| 「최근 5분 내 종료된 프로세스」 | 같음(`tests/test_orchestration/test_plan132_round3_verify.py:152`) | 사건 5분 창 |

152 E1에서 실패한 분 단위 턴: `SLOWTX-10m`·`SLOWTX-full`(5분)·`SLOWTX-colloquial`(15분)·`HEALTH-host-30m`·`HEALTH-colloquial`·`EVENT-normal`·`MT-profile`·`MT-profile-sql`·`MT-inherit-target`·`SRC-common`·`SRC-ambiguous`·`SRC-inherit` 등(152 산출물 `w6_e1` · 정확한 목록은 W0에서 run 결과로 다시 뽑는다).

### 1.2 원인 경로

1. 규칙 인식기 `src/domain/time_expr.py:76` `_UNIT` = `개월|달|주일|주|일|시간|년|…` — **「분」이 없다** → 「최근 30분」을 기간으로 못 잡는다.
2. 흔적 정규식 `_TRACE_RE`(`time_expr.py:488-490`)는 `\d+\s*분(?!기)`를 **흔적으로 잡는다**(주석 `:479-480` — D-309 ④ 「조용히 기본값으로 가지 않고 되묻는다」 · 검증 D8).
3. LLM 슬롯 `time_expr`가 `unit: "minute"`을 내도 검증 `time_expr.py:724` `_opt_enum(slot.get("unit"), UNITS, "invalid_unit")`이 버린다 — `src/domain/time_spec.py:57` `Unit`에 `minute`이 없다. 프롬프트 `src/prompts/input_parser.py:185`도 단위 목록에 minute을 주지 않는다.
4. 규칙 기간 없음 + 흔적 있음 + 슬롯 폐기 → `clarify=unresolved` → `src/nodes/input_parser.py:197-198`이 `final_response`를 싣고 사다리 공통 전단에서 끝난다(D-309 ④).
5. 하류는 준비돼 있다: `src/orchestration/apm_query.py:526` `plan_window`는 `QueryTime.event` 구간을 `lookback_minutes`(분)로 넘기고, 롤링 구간을 기준 시각까지 민다(`:555-560` · V6-2).

### 1.3 같은 계열의 조용한 오해석 (실측 · 이 계획에서 함께 다룬다)

| 질의 | 현재 해석(`event`) | 문제 |
|---|---|---|
| 「최근 1시간 30분 응답시간」 | `[09:00, 10:00)` rolling — **30분을 버림** · 되묻기 없음 | 침묵 절단(D-309 「침묵 폴백 금지」 위반). 「1시간」이 잡혀 흔적 검사가 「30분」을 보지 않는다 |
| 「오늘 14시부터 알람」 | `[00:00, 10:37)` to_date — **14시를 버림** | 시각 시작점 미지원(G-5) |
| 「어제 10시 30분부터 알람」 | `[어제 00:00, 오늘 00:00)` — **시각을 버림** | 같음 |
| 「최근 3시간 알람」 | `[07:00, 10:00)` — 사건인데 직전 정시에서 끝남 | 알람 이력은 최근 37분이 빠진다(APM은 V6-2로 보정하지만 알람 경로는 보정 없음) — G-4 |

---

## 2. 현행 실측 (영향 범위)

### 2.1 도메인 (정본 — 사본 금지 D-131)

- `src/domain/time_spec.py`
  - `:57` `Unit = Literal["hour", "day", "week", "month", "quarter", "half", "year"]` · `:59` `Grain = hour|day|month` · `:73-76` `N_MAX`(hour = 24×366)
  - `:93-96` `_UNIT_GRAIN`(단위 → 통계 입도) · `:380` `_floor`(분·초를 0으로 깎고 시작 — 분 단위 칸 없음) · `:408` `_shift`
  - `:590-598` `_policy_cap`(rolling → hour) · `:652-662` `last`: hour만 기본 rolling · rolling 끝 = `_floor(anchor, "hour")`
  - `:304-321` `label()` — 시 입도면 `%Y-%m-%d %H:%M`(분 표기 가능 · 변경 불요 예상)
  - `:860-871` `floor_to_unit`·`ceil_to_unit` — `UNITS` 밖이면 `invalid_unit`(소비처: `apm_query.py:557,660,677` · `polestar/time_period.py:82,90`)
- `src/domain/time_expr.py` — `:76` `_UNIT` · `:70-72` 한글 수사 · `:80` `_KO_NUM_UNITS` · `:466-497` 흔적(`_NOT_PERIOD_AFTER` — 이상·이하·이내·간격·씩·마다 등은 기간 아님) · `:724` 슬롯 단위 검증
- `src/domain/query_time.py` — `resolve_query_time`(요청 단위 단일 출처 · D-309 ③) · `resolve_task_time`(2단 task 재해석)

### 2.2 소비처 (`time_resolution`/`QueryTime`을 읽는 파일 35개(도메인·state·config 포함) — W0에서 분 단위 영향 전수 분류)

| 소비처 | 분 단위가 들어오면 | 예상 조치 |
|---|---|---|
| `src/orchestration/apm_query.py` `plan_window`·`_period_list`(비교) | 사건 구간 → `lookback_minutes` 그대로. `:557` 롤링 보정 조건이 `end == floor(anchor,"hour")`라 분 롤링(끝 = 기준 시각)은 보정 불요 | 조건 확인 · 비교 질의 「최근 30분 대비」(`:677` `period.unit in UNITS`) 동작 확인 |
| `src/db_adapters/polestar/time_period.py` `stat_bounds`·`alarm_ts_bounds` | 통계: 입도 hour로 내림·올림(`:82-90`) · 알람: `TS_FORMAT = %Y-%m-%d %H:%M:%S`(`:43` — 분 리터럴 이미 가능) | G-2 정책 반영 · 고지 |
| `src/db_adapters/polestar/validators.py` `check_time_conditions` | 알람 `ctime` 경계가 분 단위 리터럴 | 정규화 비교가 분을 받는지 확인 |
| `src/nodes/prompt_blocks.py` 기간 블록 · `src/nodes/output_generator.py` `[조회 기간]` 고지 | 라벨 `HH:MM` | 문구 확인 |
| `src/orchestration/deep_agent.py`·`subagents.py`·`tier3_plan.py`·`result_aggregator.py` | 값 전달만 | 변경 없음 예상 |
| `src/db_adapters/time_hint.py`(범용 DB ISO 힌트) | ISO 그대로 | 확인만 |

### 2.3 이 동작을 고정한 테스트 (개정 시 함께 바꾼다 — repo 전체 grep 기준)

- `tests/test_plan122_t_verify.py:621` `test_minutes_are_clarified_not_defaulted`(「최근 5분 CPU 사용률」·「최근 10분 알람」 = unresolved)
- `tests/test_orchestration/test_plan132_round3_verify.py:149-152`·`:204`(「최근 5분 내 종료된 프로세스」 = unresolved)
- `tests/test_domain/test_query_time.py:197-198`
- 그 밖 분 표현을 쓰는 파일(동작 고정 여부 W0 확인): `tests/test_incident_time.py` · `tests/test_domain/test_time_spec.py` · `tests/test_scenario/test_plan122_t9_time_assertions.py` · `tests/test_orchestration/test_plan125_apm_query.py` · `test_plan134_w1_apm.py` · `test_plan134_w34_verify_real.py`

### 2.4 매뉴얼 (D-255 — 사용자 동작이 바뀐다)

`scripts/manual/content/user.md:597`(해석할 수 없는 기간 = 되묻기) · `:1374`(제니퍼 기본 구간) · `:1391`(두 기간 비교 — 「최근 3시간은 정시까지」).

---

## 3. 사용자 확정 게이트 — 전건 확정 (2026-10-13 · 권고안 = 첫 항 채택 · D-326)

| # | 질문 | **확정**(권고 · 첫 항) | 대안(기각) |
|---|---|---|---|
| **G-1** | **D-309 ④ 「최근 N분 = 되묻기」를 개정해 분 단위를 해석할까?** | **(a) 개정 — 분을 해석기 단위로 넣는다**(152 E1 12+4턴 · 제니퍼 기본 창이 10분·30분이라 분 단위 질문이 흔하다) | (b) 현행 유지 — 대신 APM task에서만 원문을 다시 읽는 우회(해석 단일 출처 D-309 ③과 충돌 · 비권고) |
| **G-2** | 성능 통계(폴스타 시 단위 통계)에 「최근 30분 CPU」가 오면? | **(a) 직전 1시간(시 단위로 넓힘) + 의무 고지** 「시 단위 통계라 최근 30분 대신 ○○:00~○○:00으로 조회했습니다」 | (b) 되묻기 유지(전용 문구 — 「성능 통계는 1시간 단위가 가장 짧습니다」) · (c) 실시간 현재값(`present`)으로 돌림 |
| **G-3** | 합성 기간 「최근 1시간 30분」은? | **(a) 합쳐서 90분**(지금은 30분을 조용히 버림 — §1.3) | (b) 되묻기 |
| **G-4** | 사건 주체의 시 단위 롤링 「최근 3시간 알람」의 끝을 기준 시각까지 밀까? | **(b) 이 계획에서 바꾸지 않는다 — 발견만 기록**(APM은 이미 보정 · 알람 경로 변경은 D-291 사건 정책 개정이라 별건) | (a) 함께 고친다(사건 롤링 끝 = 기준 시각) |
| **G-5** | 시각 시작점 「오늘 14시부터」·「10시 30분부터」(지금 시각을 버리고 하루 전체 — §1.3)는? | **(b) 이 계획 밖 — 별도 후속 계획.** 단 이 계획에서 「10시 30분」의 「30분」을 **기간으로 오인하지 않게만** 막는다(시각 뒤 「분」 제외) | (a) 함께 지원(`start.minute` 키 · 슬롯 스키마 확장) |
| **G-6** | 기능 플래그를 둘까? | **(a) 두지 않는다** — 결함 교정이며 D-309 ①(시간 해석 기본 on)의 연장. D-162 「신규 플래그 기본 off」의 예외로 근거를 config 주석 대신 결정 본문에 남긴다 | (b) `QUERY_TIME_MINUTE_UNIT_ENABLED` 기본 off(off = 현행 비트 동일) |

결정 등재(2026-10-13 완료): **D-326** 「분 단위 기간 해석」 신규 · D-309 파일 부기·색인 상태 칸 「D-326 부분 개정(④ 분 단위)」 · CHANGELOG 1행.

---

## 4. 설계 (G-1~G-6 확정안)

### 4.1 도메인 — `time_spec.py`

- `Unit`에 `"minute"` 추가 → `UNITS` 자동 포함. `N_MAX["minute"] = 60 × 24 × 7`(7일 — 값 범위 검사용 · 그보다 길면 시·일로 말한다).
- `_floor(dt, "minute")` = 초·마이크로초만 깎는다(현재 `_floor`는 첫 줄에서 분을 0으로 만든다 — 분 분기를 그 앞에 둔다). `_shift(dt, "minute", k)` = `timedelta(minutes=k)`. `_ceil` 같은 규칙.
- `_UNIT_GRAIN["minute"] = "hour"`(통계 입도 하한). `Grain`·`DisplayGrain`은 늘리지 않는다(분 표시 입도는 요구 없음).
- `resolve`:
  - `last`·분: 기본 completeness `rolling`. **사건** = `[floor_min(anchor) − N분, floor_min(anchor))`(끝 = 기준 시각의 분 경계 · 정시로 깎지 않는다). **통계** = G-2 (a): 시 경계로 넓힌다 — `end` = 직전 정시 · 길이 = ⌈N/60⌉시간(30분 → 1시간 · 90분 → 2시간 — 시 단위 롤링 「최근 N시간」과 같은 끝) + 새 고지 코드 `NOTE_MINUTE_WIDENED = "minute_widened_to_hour"`.
  - `ago`·분(「5분 전」): 사건 = `[anchor − N분, anchor − N분 + 1분)`은 너무 좁아 0건이 정상처럼 보인다 → **`[anchor − N분, anchor)`로 읽는다**(「5분 전부터」와 같은 뜻 · 고지). 통계 = `last`와 같은 확장. W1에서 사례 표로 확정.
  - `this`·`to_date`·`next`·분(「이번 분」 등): `TimeSpecError("invalid_spec")` → 되묻기(실사용 표현 아님).
- `label()`: 시 입도 분기 그대로 `HH:MM`(분 경계가 그대로 보인다) — 변경 불요 확인.

### 4.2 인식기 — `time_expr.py`

- `_UNIT`에 `분(?!기|석|류|야|포|당|량|산|할|배|리)`·`minutes?` 추가(「분기」·「분석」·「분류」 등 차단 — W1에서 어휘 표 확정 · 숫자 뒤만 본다). 한글 수사는 분과 결합하지 않는다(`_KO_NUM_UNITS` 불변 — 「세 분」은 사람 수).
- **시각 뒤 분 제외(G-5 (b))**: `\d{1,2}\s*시\s*\d{1,2}\s*분`은 기간 단위로 보지 않는다 — 흔적으로 남겨 **종전처럼 되묻는다**(「10시 30분부터」가 「최근 30분」으로 바뀌는 오해석 방지).
- **합성(G-3 (a))**: `N\s*시간\s*M\s*분`(「1시간 30분」·「2시간 반」은 W1 사례 표에서 결정)을 한 명세 `last n=60N+M unit=minute`로 만든다 — 더 긴 매칭 우선 규칙(`:25`) 안에서.
- 흔적 `_TRACE_RE`의 `분(?!기)`는 **유지**한다 — 규칙이 못 잡은 분 표현(시각 뒤 분 등)은 여전히 되묻는다. `_NOT_PERIOD_AFTER`(간격·씩·마다·이상·이내·넘…)는 그대로 — 「5분 간격」·「30분 이상 걸린 거래」·「5분마다」는 기간이 아니다.
- 주석 `:479-480`(D-309 ④ 근거)을 새 결정 번호로 고친다.
- 슬롯 검증 `:724`는 `UNITS` 확장으로 자동 통과. 스팬 대조·`N_MAX` 검사는 그대로.

### 4.3 LLM 슬롯 프롬프트 — `src/prompts/input_parser.py:185`

- 단위 목록에 `minute` 추가 · 예시 1줄(「최근 30분」 → n=30·unit=minute). 프롬프트 바이트가 바뀐다 → `scripts/prompt_render_diff.py`로 의도된 변화만인지 확인 · MLX 대표 소수 1회(D-240 「바뀐 프롬프트만」).

### 4.4 소비처

- `apm_query.plan_window`: 사건 분 구간이 그대로 `lookback_minutes`로 간다. 롤링 보정(`:555-560`)은 끝이 정시가 아니므로 걸리지 않는다 — **변경 0 예상**(W0·W3 확인). 비교(`_period_list`)는 「최근 30분 대비」가 앞 30분과 비교되는지 확인.
- 폴스타 `time_period.stat_bounds`: 통계 해석이 이미 시 경계라 변경 0 예상 · 고지 `minute_widened_to_hour` 문구를 `[조회 기간]` 고지 경로(D-309 ⑦)에 더한다(의무 · neutral).
- 폴스타 `alarm_ts_bounds`·`validators.check_time_conditions`: 분 리터럴 경계 비교 확인(테스트 추가).
- 2단 `resolve_task_time`·1단 `_AMBIENT_KEYS`·3단 `task_prompt`: 같은 값 전달 — 종단 테스트로 확인만.

---

## 5. 작업 분해

| Wave | 내용 | 완료 기준 |
|---|---|---|
| **W0** 실측 재대조 | §1·§2 인용 재대조 · 소비처 35파일 분 단위 영향 분류표 · 152 E1 run에서 분 단위 실패 턴 목록 재추출 · 동작 고정 테스트 전수(grep `분`) | §9 기록 · 영향 표 확정 |
| **W1** 도메인 | `time_spec`(Unit·N_MAX·floor/shift/ceil·resolve 분 분기·고지 코드) · `time_expr`(단위 정규식·시각 뒤 분 제외·합성) · 사례 표(아래 §6 ①) 단위 테스트 | 사례 표 전건 통과 · 기존 골드(122 233건) 무회귀 |
| **W2** 슬롯 프롬프트 | `prompts/input_parser.py` 단위 목록·예시 · `prompt_render_diff` | 의도된 diff만 · 슬롯 검증 테스트 |
| **W3** 소비처 | 고지 `minute_widened_to_hour` · 알람 `ctime` 분 경계·검증기 · APM 창·비교 확인 테스트 | 종단(가짜 LLM · `build_graph()` · 2단) — 「최근 30분 응답시간」이 APM 처리기에 30분 창으로 닿음 · 「최근 30분 CPU」 = 시 확장 + 고지 |
| **W4** 테스트 개정·측정 | §2.3 고정 테스트를 새 결정대로 개정(삭제가 아니라 기대값 교체 + 근거 주석) · `python -m scripts.apm_bench --run --env e1` 재실행 · MLX 대표 3턴(두 평면 `mlx` 확인 후) | 152 E1 분 단위 턴 해소(기대: 16턴 중 분 단위 12+4 해소 → 턴 정확도 87.6% → 약 99%) |
| **W5** 문서·결정·회귀 | 새 D-번호 등재 · D-309 부기 · 매뉴얼 user 「기간」 절·제니퍼 절(D-255 절차) · `docs/18` 해당 시 · 계획 끝 `regress.py --base <시작 SHA> --files …` 1회 | 게이트 통과 · 매뉴얼 테스트 통과 · INDEX 갱신 |

---

## 6. 수용 기준

① **사례 표**(기준 시각 2026-10-13 10:37 KST · W1 테스트로 고정)

| 질의 | 사건(`event`) | 통계(`metric`) |
|---|---|---|
| 최근 30분 응답시간 / 지난 30분 / 30분간 / 30분 동안 / 30분치 | `[10:07, 10:37)` | `[09:00, 10:00)` + `minute_widened_to_hour` |
| 최근 90분 | `[09:07, 10:37)` | `[08:00, 10:00)` + 고지 |
| 최근 1시간 30분 | `[09:07, 10:37)` | `[08:00, 10:00)` + 고지 |
| 5분 전 에러 | `[10:32, 10:37)` | 시 확장 + 고지 |
| 최근 5분 내 종료된 프로세스 | `[10:32, 10:37)` | — |
| 5분 간격 / 5분마다 / 30분 이상 걸린 거래 / 30분 넘게 | 기간 아님(종전과 같음) | 같음 |
| 10시 30분부터 / 14시 30분 알람 | **되묻기 유지**(G-5 (b)) | 같음 |
| 3분기 / 분석 / 분류 / 세 분 | 분 단위 아님(종전과 같음) | 같음 |
| 최근 10081분 | `N_MAX` 초과 → 되묻기 | 같음 |

② 종단: 「{인스턴스} 최근 30분 응답시간」 → 2단 APM 처리기 `lookback_minutes=30` · `reference_time=null` · 되묻기 없음 · `[조회 기간]` 고지 없음(APM은 자체 창 고지). 「최근 10분 알람」 → 알람 이력 SQL `ctime >= '… 10:27:00' AND ctime < '… 10:37:00'` · 검증기 통과.

③ 152 E1 재실행: 분 단위 실패 턴 0 · 다른 턴 악화 0(`--compare`).

④ 무회귀: 122 시간 골드 · 프롬프트 렌더 diff는 §4.3 의도분만 · `arch_check`·`overfit_check` 통과 · `regress.py` 신규 실패 0.

⑤ 매뉴얼: 「해석할 수 없는 기간」 문단과 제니퍼 기본 구간 문단이 새 동작을 적는다(`pytest tests/test_manual`).

---

## 7. 위험

- **오인식**: 「분」은 흔한 글자다(분기·분석·분류·분야·분당·몇 분·세 분). 숫자 바로 뒤만 보고 후행 부정 어휘 표를 W1에서 사례로 고정한다. 시각 「10시 30분」은 기간에서 뺀다(G-5 (b)).
- **통계 확장의 기대 불일치**: 「최근 30분 CPU」에 1시간치를 주면 사용자는 30분치로 믿을 수 있다 → 의무 고지(G-2 (a)). 고지가 본문 3줄 상한에 밀리지 않게 `query_period`와 같은 의무 등급.
- **결정 개정의 파급**: D-309 ④를 인용한 문서·주석·테스트가 여럿이다 → W0에서 `grep -rn 'D8\|최근 N분\|최근 5분'`로 전수.
- **152 연쇄 4턴**은 앞 턴 해소로 자동 해소된다고 가정했다 — W4에서 확인하고 아니면 별도 원인을 기록한다.
- **존 사전 게이트 결함**(152 두 번째 결함 — `src/api/routes/query.py:2188`)은 이 계획 밖이다.

## 8. 의사결정 정합

- **충돌 해소**: D-309 ④(「최근 N분」 = 흔적 → 되묻기) — 사용자 확정(G-1 (a))으로 **D-326가 부분 개정**한다.
- 유지: D-291 ①(해석 불가 = 되묻기 — 시각 뒤 분·`N_MAX` 초과·「이번 분」은 여전히 되묻는다) · D-309 ③(해석 단일 출처 — 우회 경로를 만들지 않는다) · D-309 ⑦(고지 — 새 코드 1종) · D-313 ①(APM 창 = 요청 해석) · D-131(사본 금지 — `floor_to_unit` 공개 함수만 확장).
- G-6 (a) = D-162 예외 — D-326 ⑦에 근거를 남겼다.

## 9. 진행 기록

- 2026-10-13 — v1.0 작성(코드 0). 실측: §1.1 재현 · §1.3 조용한 오해석 4건(합성 기간 절단 · 시각 시작점 무시 2 · 사건 시 롤링 정시 절단) · 소비처 35파일 목록 · 동작 고정 테스트 3파일.
- 2026-10-13 — 게이트 확정 · D-326 등재 · 착수(team-lead 위임).
- 2026-10-13 — **W0 실측 재대조**: 인용 어긋남은 줄 번호만(`N_MAX` :77-80 · `_UNIT_GRAIN` :97-100 · `_TRACE_RE` :488-497 · `_policy_cap` :591-601 · `last` :652-665 · `floor/ceil/shift_unit` :860/867/874). 소비처는 grep 기준 src+scripts 46파일(§2.2의 35는 과소) — 변경 필요(A)는 `time_spec`(분 키 없으면 `_floor`가 조용히 year 절단·`_policy_cap`이 조용히 day·`_UNIT_GRAIN` KeyError 등)·`time_expr`(`_unit_of` year fall-through · `_SUFFIX` 「간」이 「간격」에 매칭)·`prompts/input_parser`·`apm_query.plan_compare_window`(분 롤링 비교 역전)·`disclosure`·벤치 주석 2. 고정 테스트는 §2.3 3파일 + `tests/test_nodes/test_plan122_t3_input_parser.py`(UNITS ↔ 프롬프트 단언). 152 E1 분 단위 실패 = `clarify:time` 12턴 + 연쇄 4턴(목록 §1.1과 같음).
- 2026-10-13 — **W1 도메인**: `time_spec`(Unit·N_MAX 10080·`_UNIT_GRAIN`·`_floor`/`_shift` 분 분기·`NOTE_MINUTE_WIDENED`·분은 `last`·`ago`만·`_resolve_minute`) · `time_expr`(`_MIN` 부정 후행 · `_SUFFIX` `간(?!격)` · 규칙 `hour_minute`(「N시간 M분/반」 → 60N+M) · `minute_ago` · 시각 뒤 분 가드). §6 ① 사례 표 전건 + 152 실패 턴 원문 통과 · 122 시간 골드 254 무회귀. 판단: 맨 「30분」은 맨 「N시간」과 대칭(흔적) · 「N시간 반」 = 합성(현행 침묵 절단 교정) · 「N 전」은 분 전용. 고정 테스트 3파일 기대값 교체(+ `# D-326` 주석 · 삭제 0) — 계획은 W4였으나 W1 변경이 직접 뒤집는 것이라 W1에서 함께 했다.
- 2026-10-13 — **W2 슬롯 프롬프트**: unit 목록에 `minute` + 줄 「(「최근 30분」은 n=30·unit=minute. 「10시 30분」처럼 시각 뒤에 붙은 분은 unit이 아닙니다)」. **이탈**: `scripts/prompt_render_diff.py`는 폴스타 query_generator 템플릿만 비교하고 input_parser를 보지 않는다(rc=0) → `src/prompts/` 대문자 상수 127개 전후 비교로 대체 — 바뀐 상수는 `INPUT_PARSER_TIME_SLOT_SECTION` 1개뿐.
- 2026-10-13 — **W3 소비처**: `disclosure` `_PERIOD_NOTE_PHRASES` 「시 단위 통계라 분 단위 기간을 정시 구간으로 넓혀 조회했습니다」(kind `query_period` 의무·중립) + `KIND_TABLE`·`scripts/scenario/catalog.py` 사본 등재(drift 테스트) · `apm_query.plan_compare_window` 분 롤링 비교 결함(「최근 30분 대비」 → (10:07,10:00) 역전 → ask) 교정 — **이탈(D-326 ③ 연장)**: 게이트웨이 `apm_period_compare`가 시 단위 통계로 경계를 넓히므로 분 기간 비교는 공개 `resolve(…, subject="metric")`로 정시 확장(비교 [09,10) · 기준 [08,09))하고 안내 rule에 적는다. `plan_window`·`time_period`·`validators`·`output_generator` 변경 0. 종단 3건(가짜 LLM · `build_graph()` · 2단 · 10:37): APM `lookback_minutes=30`·`reference_time` 없음 · 「최근 30분 CPU」 `[09:00,10:00)` + 고지 1회 · 「최근 10분 알람」 `ctime` 10:27:00~10:37:00 · 검증기 통과.
- 2026-10-13 — **W4 측정**: 152 E1(목·가짜 LLM) 87.6%(113/129) → **97.7%(126/129)** · dev 96.6% · holdout 87.5% → 100% · 악화 0 · 해소 14턴(분 단위 11 + 연쇄 target 3). 남은 3턴은 모두 기준 run에서도 실패: `APM-JSERVER-domains#0`(존 게이트 · 범위 밖) · `APM-SRC-ambiguous#1`(**벤치 드라이버 결함** — 소스 답 턴에서 라우트는 원 질문을 다시 파싱(`src/api/routes/query.py:1350-1353`)하는데 드라이버는 답 문장으로 가짜 파싱 답을 만든다(`scripts/apm_bench/driver.py:629`) → 152 소관 · 미수정) · `APM-SRC-inherit#1`(`target_wrong` — 분해 `targets`로만 지목한 인스턴스 미승계 · plans/130 사안 · 종전엔 시간 되묻기에 가려 있었다). 공동존 APM 호출 478 → 883(해소 턴 실조회 + 분 창 0행 시 시 단위 재조회) — 152 §3.7 E2 예산 참고. 벤치 주석 `fake_llm.py`·`judge.py` D-326 기준 수정. 1단 `_extract_ambient_state`·3단 `task_payload`/`task_prompt` 값 전달 테스트 4건. MLX(127.0.0.1 · `--show-env`/`--preflight` 두 평면 mlx): 벤치 3시나리오 4턴 source·views·target·window 100%(args는 few-shot 대상 `arg_hallucination`) — 4턴 모두 규칙이 기간을 잡아 슬롯 미사용 → 슬롯만 따로 3문장 확인: 맨 「응답시간 30분」 슬롯 `last·30·minute` 수락 · 「10시 30분부터 알람」 슬롯 null → 되묻기 · 「삼십 분 동안」 슬롯 null·흔적 없음 → 기본값(기존 틈 — 아래). 띄운 PID만 종료.
- 2026-10-13 — **독립 검증**(verifier · 한 라운드): Critical 0 · High 2 · Medium 2 · Low 1 → 전건 교정. V-1(회귀) 「최근 3시간 동안 5분 간격」이 합성 꼬리 오인으로 unresolved → 꼬리 검사를 단위 끝·접미 없을 때만 · V-2(침묵 기간 변경) 「지난 3시간 10분 간격」 → 190분 → 분 자리 뒤 빈도·조건 어휘면 합성 거부 · V-3(D-326 ⑧) 「최근 5분 간격으로」·「최근 30분 이상 걸린」 → 분 단위에 한해 빈도·조건 어휘 앞이면 기간 아님 · V-4(⑤) 슬롯 `unit=minute`이 시각 뒤 분·빈도 어휘 스팬과 겹치면 `slot_to_spec` 결정적 가드로 거절 · V-5 두 기간 비교(「어제 대비 최근 30분」)에도 정시 확장 — 넓힌 두 구간이 겹치면 `COMPARE_OVERLAP_NOTE`로 되묻기(「지난 1시간 대비 최근 30분」은 verifier 기대 window → ask로 교체 · 시 단위로 겹치지 않는 두 구간을 만들 수 없음). 경미: `_MIN` 부정 후행 「위」·「의 + 숫자」 · 「N시간 M분」 M ≥ 60은 합성하지 않고 되묻기. 「평균」은 빈도 어휘에 넣지 않았다(「최근 1시간 30분 평균 응답시간」을 1시간으로 줄이게 됨 · 「지난 24시간 5분 평균」은 1445분 + 고지). 교정 뒤 E1 재측정 97.7%(126/129) 유지 · holdout 100% · 악화 0.
- 2026-10-13 — **계획 끝 모듈 단위 회귀 1회**(교정 반영 뒤 · `regress.py --base 6810336 --files` 회귀 대상 23파일): 본체 통과 4,708 · 실패 12(전부 `[원래 실패]` — `tests/test_api/test_plan122_t4_time_resolution_route.py` 파라미터 12건 · 기준 `6810336`에서도 실패) · 건너뜀 6 · repo_guard 135 통과 · arch_check·overfit_check 통과 · ruff·mypy 이번 diff 줄 0 · `범위: 모듈 단위 — 전체 미실행` · `[전체 회귀 권고]` 없음. 이 python에는 `pytest-xdist`가 없어 152 때처럼 환경을 바꾸지 않고 `split_workers`를 (0,0)으로 바꾼 직렬 래퍼로 같은 선택을 돌렸다. (교정 전 1회는 통과 4,686 · 같은 원래 실패 12 — 교정 뒤 재실행이 정본.)
- 2026-10-13 — **매뉴얼(D-255)**: `scripts/manual/content/user.md` U-53(분 단위 표 · 알람 표 · `[조회 기간]` 예시 · 되묻는 분 표현 · 빈도·조건 주의) · U-51(제니퍼 분 그대로 · 「최근 30분 대비」 정시 확장) · `admin.md` A-70 주의 1줄 → `python -m scripts.manual.build` · `pytest tests/test_manual` 577 통과. features.yaml 무변경 · 캡처 생략(채팅 문구만 바뀌고 화면 요소 변화 없음 — U-53·U-51 `no_ui`). 검증 교정 뒤 맨 「30분」 문장을 실측(슬롯이 읽으면 「모델 해석」 고지 + 조회)으로 고쳤다.
- **남긴 것(후속 · 이 계획 밖)**: ① 시각 시작점(「오늘 14시부터」·「10시 30분부터」 — G-5 · 「2026년 9월 15일 10시 30분부터」 = 9/15 하루도 같은 계열) ② 사건 시 단위 롤링 정시 절단(「최근 3시간 알람」 — G-4) ③ 한자어 수사(「삼십 분」·「삼십 일」·「이십 시간」)는 규칙·흔적 모두 못 봐 침묵 기본값 — 기준 SHA 동일 · 모든 단위 ④ 「N시간 전」 되묻기 vs 「N분 전」 해석 비대칭 · 시·일 단위 「최근 3시간 이상 걸린」 = 3시간(분과 비대칭) ⑤ 「30 min」·「1시간 반쯤」·슬롯 unit ↔ 원문 스팬 단위 미대조(기존) ⑥ 152 벤치 드라이버 소스 답 턴 결함 · APM 분해 `targets` 대상 미승계(plans/130) · 존 사전 게이트(`src/api/routes/query.py:2188`) ⑦ `prompt_render_diff.py`가 input_parser 프롬프트를 비교하지 않는다.

## 변경 이력

- **v1.2**(2026-10-13) — W0~W5 구현 완료 · verifier 지적 전건 교정 · 계획 끝 모듈 회귀 · 152 E1 97.7% · 매뉴얼 · 파일명 태그 제거(`159-TODO-…` → `159-minute-period-resolution.md`).
- **v1.1**(2026-10-13) — 게이트 G-1~G-6 권고안 확정(사용자 *"권고대로 정의하고 착수하라."*) · D-326 등재 · 시작 SHA `6810336`.
- **v1.0**(2026-10-13) — 최초 작성.
