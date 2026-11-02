# 207. 서버 미지정 APM 조회 지연 교정 — 끝나기 어려운 작업 조기 접수(B) · 도메인 단위 병렬 호출(C)

> **작성일**: 2026-10-23 · **v1.0**
> **상태**: **WIP** — W0~W4 구현(2026-10-23) · 잔여: 폐쇄망 재측정 · §7 후속 · 원 plans/192·D-346 → 2026-10-27 main 병합 시 plans/197·D-348 → 2026-11-02 main 병합 시 plans/207·D-358로 재부여, 동시 상한은 D-347 `JENNIFER_MAX_INFLIGHT_PER_SOURCE`(기본 8)로 통일(D-358 ① 개정 — 아래 §3 `JENNIFER_MAX_IN_FLIGHT` 문구는 작성 당시 설계)
> **계획 시작 SHA**: `7ef25af1`
> **회귀 대상 파일**: (구현 후 기입)
> **성격**: 행동 변경(D-337 ⑥) — 구조 리팩터와 섞지 않는다(공용 병렬 헬퍼 도입은 호출 지점 변경과 같은 Wave에서 한다 — plans/190 §3.1 선례).
> **관련 결정**: **D-358**(이 계획의 정본 · 예약 2026-10-23) · D-296 ⑤(「큰 조회는 소스·도메인 병렬 호출」 — 결정만 있고 구현은 직렬) · D-313 ⑤(서버 미지정 = 부하 상위 N) · D-310 · D-267 ⑥(조회 마감) · D-299 ④(장기 작업 수명 분리) · D-344(b0 X-View 부하 — 무거운 호출 병렬 제외 근거) · D-274(게이트웨이 경계 — 양방향 import 0)
> **패키지**: 본체 `src/orchestration/`(B) · `apm_gateway/`(C)

## 1. 발단 — 폐쇄망 실측(2026-10-23 · 사용자 화면)

질의 「은행존 WAS 중 메모리 사용률이 가장 높은 5개 서버 보여줘」가 285초 뒤 **접수 답**으로 끝났다.

| 관찰 | 값 |
|---|---|
| 응답 | 「제니퍼 조회가 오래 걸려 작업으로 접수」 · 완료 · 285.0s · ROWS 0 |
| 작업 | 전체 WAS 인스턴스 순위(`apm_fleet` ranking — 서버 미지정) · 은행 제니퍼(b0)만 |
| 진행 | 응답 시점 74/350 API 호출 · 남은 예상 92.6초(측정 기반 — 호출당 약 0.33초) |

**원인(코드 실측)**:
1. **350 = b0 도메인 수**. 실시간 순위는 (소스, 도메인)마다 `/api/realtime/instance` 1회를 **직렬 `for … await`**로 부른다(`fleet_tools.py` `_realtime_records`). throttle(`PriorityThrottle`)은 호출 **시작 간격**(1/rate)만 지키므로, 직렬이면 처리량 = min(rate, 1/지연)이다.
   모의 측정(실 `PriorityThrottle` · 지연 0.33초 · 2026-10-23): 직렬 3.0회/초(350회 ≈ 116초) · 병렬 rate 5 = 4.9회/초(≈ 71초 · 동시 최대 2) · 병렬 rate 10 = 9.5회/초(≈ 37초).
2. **본체는 끝나기 어려운 작업도 조회 마감까지 기다린다**(`src/orchestration/apm_jobs.py` `poll_job` — 조기 종료 조건 없음). 285초 ≈ 폐쇄망 `API_QUERY_TIMEOUT`(추정 300) − 서술 예약 15. 그 시간에 74회만 진행된 이유(호출 전 LLM 단계 · 작업 슬롯 대기 · 승격 뒤 `background` 우선순위가 폴러에 밀림)는 **로그로 미확정**(§7).
3. 같은 직렬 도메인 루프가 게이트웨이에 여럿 있다 — 인스턴스 명단 적재(`resolver._load` — plans/190 §7의 b0 콜드 적재 351회)도 그중 하나다.

## 2. 범위

| # | 항목 | 대상 |
|---|---|---|
| W0 | 실측 — ① 게이트웨이의 (소스, 도메인) 직렬 루프 전수와 분류(**가벼움** = 도메인당 1회 실시간·목록·통계 / **무거움** = X-View 1분 조각 등 대용량) ② `poll_job` 호출 경로(사다리 1단 `deep_agent`·2단 공통 여부) ③ 작업 상태 봉투의 `progress`·`estimate` 실 shape | implementer · §2.6 신설에 표로 기록 |
| W1 | **C-1 게이트웨이 병렬 기반** — 공용 유계 병렬 헬퍼(입력 순서대로 결과·예외 반환) · 소스별 동시 요청 상한 설정 · `wait_slot` 동시 진입 경합 교정 · 예상 시간 식의 병렬 반영 | `apm_gateway/application/`(헬퍼 신설 1개) · `jobs.py` · `config.py` · `adapters/jennifer/client.py` · `apm_gateway/.env.example` |
| W2 | **C-2 가벼운 도메인 루프 병렬화** — W0에서 「가벼움」으로 분류한 루프(최소 `fleet_tools._realtime_records` · `resolver._load`)를 W1 헬퍼로 바꾼다. **결과·`[한계]`·실패 목록의 순서는 종전과 같게**(결과를 모은 뒤 입력 순서로 처리) | `fleet_tools.py` · `resolver.py` · W0 표의 그 밖 가벼운 루프 |
| W3 | **B 본체 조기 접수** — 승격된 작업을 다시 보다가 「남은 예상 시간 > 조회 마감까지 남은 시간」이면 마감을 기다리지 않고 그 시점 상태로 접수 답을 낸다 | `src/orchestration/apm_jobs.py`(`poll_job`) · 필요 시 `apm/jobs_notices.py` |
| W4 | 독립 검증 · 회귀 · 기록 — D-358 본문·색인·CHANGELOG · D-296 ⑤ 부기(구현) · `spec/SPEC-apm-question-coverage.md` §3.4·§3.8 · `spec/SPEC-apm-gateway.md`(설정 표) · INDEX | verifier · team-lead |

**범위 밖**: 무거운 루프(X-View 1분 조각 · `apm_core.xview` 등) 병렬화 — b0 X-View 1조각 약 4초·4MB(plans/190 §1)라 동시 요청이 뷰 서버 부하를 키운다 · `JENNIFER_RATE_LIMIT_PER_SEC` 값 변경(G-12 운영 협의) · `/api/realtime/instance` `domain_id` 생략 1회 조회(개선안 A — 폐쇄망 실측 대기 · §7) · 결과 캐시(개선안 D) · 질의 의미 문제(「메모리 사용률」 ↔ 카드 문구 「가장 느린-바쁜 N개」 · 폴스타 OS 메모리와 제니퍼 heap 라우팅 — §7).

## 3. 설계

### 3.1 W1 병렬 기반(게이트웨이)

- **헬퍼**: `async def gather_bounded(items, fn, limit) -> list[결과 | ApmError]` 류 — `asyncio.Semaphore(limit)` + `gather`. 입력 순서대로 돌려주고, `ApmError`는 값으로 담아 호출자가 종전 `try/except` 분기를 순서대로 재현한다. 그 밖 예외는 종전처럼 전파한다. `CancelledError`는 남은 태스크를 취소하고 전파한다(승격 전 취소 계약 — SPEC §3.2). 호출 맥락(`current_scope` contextvar)은 자식 태스크가 물려받는다 — 테스트로 고정.
- **동시 요청 상한**: 소스별 `max_in_flight`(env `JENNIFER_MAX_IN_FLIGHT` · 소스별 `JENNIFER_<ID>_MAX_IN_FLIGHT` — 기존 `RATE_LIMIT_PER_SEC` 소스별 키 패턴 그대로). **기본 4** — 「신규 설정 기본 off」 원칙의 예외(근거: D-296 ⑤가 병렬을 이미 결정 · 속도 상한은 그대로라 초당 호출 수는 늘지 않음 · 기본 1이면 보고된 지연이 그대로 남음 — D-344 기본 10 선례). `1` = 종전 직렬과 동작 동일. 잘못된 값은 warning 후 기본값.
  상한은 헬퍼 `limit`와 별개로 **클라이언트에서도** 지킨다(`JenniferClient` 소스별 세마포어) — 속도 상한 0(무제한) 설정에서도 동시 요청이 폭주하지 않게. 한 소스의 모든 호출(폴러·동기·작업)이 이 상한을 함께 쓴다.
- **`wait_slot` 경합 교정**: 지금은 `holds_slot` 검사 → `await acquire()` → 표시 순서라, 같은 작업의 동시 호출이 슬롯을 여러 개 가져가고 하나만 반납한다(슬롯 누수 → 다른 작업 영구 대기). 작업마다 `asyncio.Lock`(또는 단일 future)으로 한 번만 획득하게 한다. 재현 테스트(교정 전 실패)를 먼저 쓴다.
- **예상 시간**: `estimate.seconds = 남은 호출 × max(1/rate, 소요 EMA ÷ min(max_in_flight, 남은 호출))`. `max_in_flight = 1`이면 종전 식과 같다. `basis` 어휘는 그대로.

### 3.2 W2 루프 교체

- 각 루프는 「호출 → 결과 처리」를 **호출(병렬) / 처리(입력 순서 직렬)**로 나눈다. 행·`limits` 문구·실패 목록·`expect_calls` 값이 `max_in_flight = 1`일 때 종전과 바이트 동일해야 한다(기존 테스트 무수정 통과가 기준).
- `resolver._load`(인스턴스 명단 적재)도 같은 방식이다. 공유 적재의 맥락 분리(`contextvars.Context()` — 시작한 작업의 우선순위·슬롯에 끌려가지 않게)는 유지한다.

### 3.3 W3 조기 접수(본체)

- `poll_job`은 상태를 볼 때마다 「남은 예상 시간」 R을 낸다:
  - `estimate.seconds`(게이트웨이 값 — `null`이면 없음)
  - 본체 관측 진행률: 같은 작업에서 처음 본 시각·`progress.done`부터 지금까지의 (Δdone ÷ Δt) — 관측 창이 `EARLY_ACCEPT_MIN_OBSERVE_SEC`(상수 · 예: 5초) 이상이고 `progress.total`이 있을 때만. Δdone = 0이면 무한대.
  - R = 둘 중 큰 값(있는 것만). 둘 다 없으면 조기 접수하지 않는다(종전 동작).
- R > (조회 마감까지 남은 시간)이면 그 상태 봉투로 즉시 돌아간다 → 종전 접수 답 경로(`apm_job_accepted` 고지 · 작업 카드)가 그대로 처리한다. 접수 문구·카드·장부는 바꾸지 않는다.
- 작업이 `queued`(슬롯 대기)여도 같은 식으로 판단한다(진행 0이면 관측 창 뒤 R = 무한대 → 접수).
- 새 설정은 두지 않는다(상수만) — 접수 답은 기존 계약이고 「마감까지 침묵 대기」를 줄이는 교정이다. 근거는 D-358에 적는다.
- 대상 선정용 첫 홉 작업(SPEC §3.8 「첫 홉이 승격돼 마감을 넘기면 취소」)에는 적용하지 않는다 — W0에서 그 경로가 `poll_job`을 쓰는지 확인하고, 쓰면 조기 접수가 첫 홉 취소 규칙을 바꾸지 않게 한다.

## 4. Wave

| Wave | 내용 | 담당 | 완료 조건 |
|---|---|---|---|
| W0 | §2 W0 실측 | implementer | §2.6 표(루프 위치·호출 API·분류·근거 · `poll_job` 호출 경로 · 봉투 shape) |
| W1 | 병렬 기반 | implementer | 테스트: 헬퍼 순서 보존·예외 값 반환·취소 전파·contextvar 상속 · 클라이언트 동시 상한(가짜 transport로 동시 in-flight ≤ 상한) · `wait_slot` 경합 재현(교정 전 실패 → 교정 후 슬롯 1개만 사용·반납) · 예상 시간 식(상한 1 = 종전 값) · 설정 파싱(기본 4·소스별 키·잘못된 값) |
| W2 | 가벼운 루프 병렬화 | implementer | 기존 게이트웨이 테스트 무수정 통과 · 새 테스트: 상한 4에서 동시 in-flight > 1이고 결과·`limits`·실패 순서가 상한 1과 같음 · 속도 상한이 지켜짐(호출 시작 간격 ≥ 1/rate) |
| W3 | 본체 조기 접수 | implementer | 테스트(가짜 세션): 예상 > 남은 시간 = 첫 판단 시점에 반환(마감 전) · 예상 < 남은 시간 = 완료까지 대기(종전) · estimate·total 둘 다 없음 = 종전 · 진행 0(queued) = 관측 창 뒤 반환 · 끝난 상태는 즉시 반환(종전) |
| W4 | 검증 · 회귀 · 기록 | verifier · team-lead | §5 |

## 5. 검증

- 실 LLM·실 제니퍼는 쓰지 않는다 — 가짜 transport(httpx `MockTransport` + 지연)·가짜 MCP 세션·가짜 시계.
- Wave 중간: 자기 테스트 + `python scripts/regress.py --no-tests --files …`. `apm_gateway` 테스트는 `cd apm_gateway && ../.venv/bin/python -m pytest`(루트 venv가 없으면 실제 인터프리터와 그 사실을 보고).
- 계획 끝: 모듈 단위 회귀 1회(`python scripts/regress.py --base 7ef25af1 --files <바꾼 파일…>`) + `apm_gateway` 테스트 + `arch_check`·`overfit_check`. 범위 줄과 `[전체 회귀 권고]` 블록은 보고에 그대로 옮긴다.
- 성능 확인(로직): 가짜 transport 지연 0.33초 · 도메인 350 · rate 5에서 실시간 순위 소요가 직렬 약 116초 → 병렬 약 71초 수준으로 줄었는지(가짜 시계 또는 축소 규모로).

## 6. 폐쇄망 배포 메모

(완료 시 기입 — 반영 파일 · 확인 grep 심볼 · 권장 `.env`: `JENNIFER_MAX_INFLIGHT_PER_SOURCE` 기본 8(D-347 · 끄려면 1) · 재측정 항목: 같은 질의의 응답 시간(조기 접수 시점) · 작업 완료 시간 · 게이트웨이 `apm audit` 작업 `elapsed_ms`·`api_calls` · b0 콜드 적재 시간)

## 7. 후속(이 계획 밖)

- **285초 중 약 260초의 귀속** — 게이트웨이 `작업 승격(백그라운드): job_id=…` 시각과 작업 기록 `created_at`·`updated_at` · 본체 노드별 소요 · 폐쇄망 `.env`의 `API_QUERY_TIMEOUT`·`JENNIFER_RATE_LIMIT_PER_SEC`·`APM_JOB_MAX_CONCURRENT`.
- **개선안 A** — `/api/realtime/instance`를 `domain_id` 없이 1회 호출 가능한지 폐쇄망 실측(`/api/realtime/domain`은 생략 지원). 되면 실시간 순위 350회 → 1회.
- 승격 뒤 `background` 우선순위가 폴러에 밀리는 정도(throttle 대기 시간 계측).
- 질의 의미 — 「메모리 사용률 상위 N」이 heap 지표로 정렬됐는지 · 카드 범용 문구 · 「서버 메모리」의 폴스타/제니퍼 라우팅.
- 개선안 D(서버 미지정 순위 결과 단기 캐시).
- 속도 상한 상향(G-12).
