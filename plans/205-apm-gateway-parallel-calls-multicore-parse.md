# 205. APM 게이트웨이 조회 가속 — 도구 안 독립 호출 동시 실행 · 큰 응답 파싱·자격증명 검사 멀티코어

> **작성일**: 2026-11-02 · v1
> **상태**: **TODO** — 코드 0건
> **계획 시작 SHA**: `16ea44fa`
> **관련 결정**: D-356(이 계획 · 기본 on — D-162 예외) · D-347 ③(소스별 in-flight 상한 8 — 유지) · D-296 ④(메모리 임계·스풀 — 유지) · D-344 · D-274 ③(양방향 import 0) · D-303(회귀)
> **회귀 대상 파일**: W1 `apm_gateway/apm_gateway/adapters/parse_pool.py`(신규) · `adapters/jennifer/client.py` · `config.py` · `interface/config_admin_adapter.py` · 조립부(`__main__.py`·`interface/server.py` 중 클라이언트 생성·종료 지점) · `apm_gateway/.env.example` / W2 `application/fanout.py`(신규) · `application/resolver.py` · `apm_core.py` · `manage_tools.py` · `stats_tools.py` · `scope_tools.py` · `transaction_tools.py` · `compare_tools.py` · `health_tools.py` · `fleet_tools.py` · `instance_tools.py`(W0 분류 결과로 확정) / W3 `application/batch.py`

---

## 1. 배경 · 실측 (2026-11-02)

사용자 제기: *"apm gateway가 api 조회 시 시간이 오래 걸린다. 1코어에서 동작하니 행이 걸리는 것으로 보인다."*

- 게이트웨이는 **프로세스 1개 · 이벤트 루프 1개**다(`__main__.py` `asyncio.run` + uvicorn 단일 서버). MCP 요청·폴러·장기 작업이 이 루프를 같이 쓴다.
- **I/O 직렬**: 여러 건 조회 경로 대부분이 `for` 루프 안에서 `await`로 한 건씩 기다린다 — `resolver.py:253`(도메인마다 `/api/instance`) · `stats_tools.py:387`(인스턴스 × 지표) · `scope_tools.py:691`(단위 × 지표) · `transaction_tools.py:272` · `compare_tools.py:267·630` · `health_tools.py:110·199` · `apm_core.py:237` · `manage_tools.py:316`(`_run` 공용) · `batch.py:263`(`targets` 항목 — 독스트링에 「차례로」 명시). 호출 1건 1초 × 20건이면 20초 이상이다. 클라이언트의 속도 제어(초당 5) · in-flight 상한(8)은 이미 있으므로 동시에 보내도 제니퍼 부하 상한은 지켜진다.
- **CPU 직렬**: 응답 파싱 + 자격증명 검사(`scrub_detail`)는 순수 파이썬 객체 순회라 GIL을 쥔다. 256KiB 초과분을 `asyncio.to_thread`로 넘기지만(`client.py:174-206`) **코어는 1개**다. 맥북 실측(합성 응답):

| 응답 | `json.loads` | `scrub_detail` |
|---|---:|---:|
| 324KiB(1천 건) | 2ms | 29ms |
| 1.6MiB(5천 건) | 9ms | 147ms |
| 6.5MiB(2만 건) | 39ms | 602ms |

  6.5MiB × 8건: 순차 5.00s → `ProcessPoolExecutor(4)` 1.45s(약 3.4배) · 결과 pickle 왕복 25ms. 프로파일상 단일 핫스팟이 없다(`_dict`·`_emit`·`_classify` 분산) — 알고리즘 교정보다 프로세스 분산이 맞다.
- **A·B 상호작용**: 호출을 동시에 보내면 큰 응답이 한꺼번에 도착해 CPU 처리가 몰린다 → A만 하면 루프 막힘이 더 길어질 수 있다. 둘을 함께 한다.
- **채택하지 않는 것**: uvicorn 다중 워커 — ① MCP SSE 세션이 프로세스 메모리라 `POST /messages`가 다른 워커로 가면 깨진다 ② 속도 제어·in-flight 상한이 워커 수만큼 늘어난다(D-347 ③ 위반) ③ 폴러 중복 · 인스턴스 캐시·작업 관리자 분열.
- **넘지 않는 한계**: 초당 5건 상한(§0.10 #10)은 그대로다 — 호출 N건은 최소 N/5초. 상한 변경은 이 계획 밖이다.

## 2. 설계 원칙

1. **기본 on**(사용자 결정 2026-11-02 · D-356 — D-162 「기본 off」 예외). 되돌리기 값으로 현행과 비트 동일: `APM_PARSE_WORKERS=0`(현행 `to_thread`) · `APM_FANOUT_CONCURRENCY=1`(현행 순차 · 같은 호출 순서).
2. **결과 비트 동일**: 행 순서 · `[한계]`·실패 고지 순서 · 봉투 칸 · 호출 수 예상(`expect_calls`)·진행 신고는 순차 실행과 같아야 한다. 동시 실행 안에서는 **API 호출만** 하고, `limits.append/fail`·행 조립·태깅은 모아서 **입력 순서대로** 처리한다.
3. **상한은 기존 장치가 진다**: 동시 호출은 모두 `JenniferClient._get`(자리 → 속도 제어 → HTTP)를 지난다. 새 상한을 클라이언트에 더하지 않는다.
4. **침묵 폴백 금지**: 프로세스 풀 장애는 WARNING 로그(사유 구조화) 후 그 호출만 스레드 경로로 처리하고 풀을 다시 만든다.
5. 자격증명 경계 불변(D-296 ③): 가린 값만 위 계층으로 간다 — 워커 프로세스가 파싱과 검사를 **함께** 하고 가린 결과만 돌려준다(원본 객체를 부모로 보내지 않는다).

## 3. Wave

### W1 — 큰 응답 파싱·자격증명 검사 프로세스 풀 (B)

- 신규 `adapters/parse_pool.py`:
  - 최상위 워커 함수 `parse_scrub_json(text: str | None, path: str | None, encoding: str) -> (cleaned, notes, masked)` — `path`면 `load_json_file`, 아니면 `json.loads` 후 `scrub_detail`. `scrub_text_worker(text) -> str`.
  - `ParsePool(workers: int, min_bytes: int)` — `ProcessPoolExecutor`를 **지연 생성**, `mp_context=get_context("spawn")`(실행 중 루프·스레드가 있는 프로세스의 fork 회피 · 맥/리눅스 동일 동작). `async def json(body) / text(text)` → `loop.run_in_executor`. `BrokenProcessPool` → WARNING + 풀 폐기·재생성 + 그 호출은 `asyncio.to_thread`. `shutdown()`은 `cancel_futures=True`.
  - 워커 import 표면은 `domain.credentials`·`adapters.json_stream`(stdlib 전용)만 — 서버·설정 모듈을 끌어오지 않는다. spawn은 `__main__`을 `__mp_main__`으로 다시 import하므로 `apm_gateway/__main__.py`가 `if __name__ == "__main__":` 가드 아래에서만 서버를 띄우는지 **실측**한다.
- `client.py`: 생성자 인자 `parse_pool: ParsePool | None = None`(None = 현행 `to_thread` · 기존 테스트 비트 동일). `get_json`: `body.size >= min_bytes`(스풀 파일 포함)이면 풀에서 파싱+검사, 아니면 현행 인라인. `get_text`도 같은 규칙. 임시 파일 삭제는 계속 부모(`finally body.discard()`)가 한다 — 워커는 읽기만.
- 설정(`config.py` · 새 dataclass 또는 `JobConfig` 옆 `PerfConfig`): `APM_PARSE_WORKERS`(기본 `min(4, max(1, os.cpu_count() - 1))` · 0 = 끔) · `APM_PARSE_PROCESS_MIN_BYTES`(기본 262144 = 현 `_THREAD_SCRUB_BYTES`). 풀은 **프로세스당 1개**를 소스별 클라이언트가 공유한다. 설정 관리 키 목록(`config_admin_adapter.py:211` 부근)과 `.env.example`에 등재(기본값 주석 + D-356).
- 종료: 클라이언트 `aclose`를 부르는 조립 지점에서 풀 `shutdown`.
- **테스트**: ① 풀 경로와 인라인 경로의 결과 동일(cleaned·notes·masked — 자격증명 포함 표본 · 스풀 파일 표본) ② 임계 미만은 풀 미사용 ③ 파싱 오류 → 기존과 같은 `ApmError(API_ERROR)` 문구 ④ 깨진 풀(워커 강제 종료) → WARNING + 결과 정상 ⑤ `APM_PARSE_WORKERS=0` → 풀 미생성 ⑥ 이벤트 루프 지연: 6MiB 응답 4건 처리 동안 10ms 틱 코루틴의 최대 지연이 풀 경로에서 유의하게 작다(느슨한 단언 · `serial` 마커 검토).

### W2 — 도구 안 독립 호출 동시 실행 (A)

- **W0(착수 전 분류)**: §1의 루프 전수(`grep` — `for` 본문 안의 `await …api…`/`resolver.inventory()`)를 ① 독립(병렬 대상) ② 종속(앞 결과로 다음 호출 결정 — `apm_core.fetch_errors` 표기 후보 순차 · 페이지 넘김) ③ 캐시 1회성(`fleet_tools.py:206` 소스별 캐시 명단 — 효과 미미)으로 나눠 표로 남긴다(이 계획서 §5). ②·③은 바꾸지 않는다. 폴러(`poller.py:119`)는 이미 `gather`.
- 신규 `application/fanout.py`: `async def gather_ordered(items, call, *, limit) -> list[tuple[item, result | ApmError]]` — `asyncio.Semaphore(limit)`로 묶고 **입력 순서**로 돌려준다. `ApmError`만 결과로 잡고, 그 밖 예외는 나머지를 취소한 뒤 다시 올린다. `limit == 1`이면 태스크 없이 차례로 await(현행과 같은 호출 순서 — 녹화 응답 순서에 기대는 테스트 보호). 호출 맥락(ContextVar `current_scope`)은 태스크에 복사되어 같은 맥락 객체를 공유함을 테스트로 고정한다.
- 공용 헬퍼 우선: `manage_tools._run` 하나로 다수 도구가 바뀐다. 그 밖은 W0 표의 ① 대상을 하나씩 — 호출부를 `gather_ordered`로 바꾸고, 결과 처리 루프는 종전 코드를 입력 순서 그대로 둔다.
- 설정 `APM_FANOUT_CONCURRENCY`(기본 4 · 1 = 현행 순차) — 도구 호출 1건 안의 동시 상한.
- **위험 R-1 — 자리 대기 시간 초과**: 지금은 작업 4개 × 순차 1 = 소스당 동시 4 < 자리 8이라 자리 대기가 없다. 동시 4면 작업 4 × 4 = 16 > 8 → 제니퍼가 느릴 때 자리 대기가 API timeout(30s)을 넘어 **종전엔 없던 `SOURCE_UNAVAILABLE`**이 날 수 있다. 검증: 가짜 전송(지연 3s · 5s) × 작업 4 동시 × 도구당 독립 호출 12건에서 자리 대기 초과 0건을 단언. 실패하면 기본값을 `max(1, max_inflight_per_source // APM_JOB_MAX_CONCURRENT)`(= 2)로 낮추고 근거를 D-356 부기에 남긴다. 자리 상한(8) 자체는 올리지 않는다(D-347 ③).
- **테스트**: 각 바꾼 경로마다 ① 동시 4와 순차 1의 봉투 동일(행·`limits` 순서·`partial`·오류 봉투) — 일부 단위 실패 · 전부 실패 포함 ② 지연 1s 가짜 전송으로 소요 단축 단언(느슨) ③ 작업 감사 `api_calls`·`calls_by_source`·예상 호출 수 동일.

### W3 — `targets` 배치 항목 동시 실행

- `batch.py`: 항목을 `gather_ordered`로 돌린다(같은 `APM_FANOUT_CONCURRENCY`). `_Plan`은 「도는 항목 1개」 가정(`current`·`running`)을 항목별 카운터로 바꿔 동시 항목의 신고를 합친다 — **바깥 작업에 신고되는 총 예상 호출 수의 최종값은 순차와 같아야** 한다(중간 값은 달라질 수 있음 — 독스트링 갱신). `_ItemScope`는 항목별로 이미 분리돼 있다.
- 배치 안 하위 도구가 W2로 자체 동시 실행을 하므로 실효 동시 = 항목 동시 × 도구 동시다 — 둘 다 자리·속도 제어 아래이므로 R-1 검증을 배치 조합으로 한 번 더 한다.
- **테스트**: 항목 순서·`target_index`·부분 실패 고지·하위 봉투 고지·최상위 `limits` 합집합이 순차와 동일 · 최종 예상 호출 수 동일.

### W3b — 다중 사용자 동시성 (사용자 요구 2026-11-02 「다중 사용자가 사용할 수 있는 동시성」)

**문제(실측)**: 자리 8(`PrioritySlots`)·초당 5(`PriorityThrottle`)는 **소스별 1세트를 게이트웨이 전체가 공유**한다(`sources.py` `build_source_set` — 소스당 클라이언트 1개). 대기자 선택은 우선순위(poller > interactive > background)+에이징뿐이라 **같은 순위 안에서는 먼저 온 순서**다 → W2·W3로 한 사용자의 질의가 여러 건을 동시에 보내면 공용 자리를 한 사용자가 다 채우고, 뒤에 온 다른 사용자는 그 뒤에 줄을 선다(대기 30s 초과 시 `SOURCE_UNAVAILABLE` — R-1이 다중 사용자에서 증폭). 장기 작업 슬롯(`APM_JOB_MAX_CONCURRENT` 4 · `jobs.py` `wait_slot`)도 게이트웨이 전역 FIFO라 한 사용자의 작업 여러 개가 4칸을 다 쥘 수 있다.

**호출자 식별**: 호출자 키 = (주체 `principal`, `owner`). 본체는 사용자마다 `owner=user:<sub>`를 싣는다(`src/orchestration/apm_jobs.py` `gateway_owner`). owner가 없는 호출(sre_agent 조사 등)은 주체 단위 1개 키다. 데이터 도구는 모두 작업(`JobManager.execute`)으로 돌므로 작업 맥락에서 키를 얻는다 — `CallScope`에 `caller` 속성(기본 `""`)을 더하고 `Job`·`_ItemScope`가 바깥 값을 그대로 돌려준다.

- ① **공정 자리 분배**(`adapters/throttle.py` `PrioritySlots`): 순위·에이징 규칙은 그대로 두고, **같은 유효 순위 안에서는 지금 자리를 가장 적게 쥔 호출자를 먼저** 준다(동률이면 종전 순서). 호출자별 동시 상한 `JENNIFER_MAX_INFLIGHT_PER_CALLER`(기본 `max(1, 자리 // 2)` = 4) — 한 사용자의 동시 호출이 자리를 다 채우지 못한다. 상한에 닿은 호출자의 대기자는 다른 호출자가 기다리는 동안 건너뛴다(**자리가 남는데 아무도 못 받는 상태 금지** — 기다리는 다른 호출자가 없으면 상한을 넘겨 받는다 · 일 보존). poller는 상한 제외. `caller == ""`(키 없음)은 종전 동작.
- ② **공정 작업 슬롯**(`jobs.py` `_Slots`): 같은 owner의 실행 중 작업 상한 `APM_JOB_MAX_CONCURRENT_PER_OWNER`(기본 2) · 대기 작업은 실행 중 작업이 가장 적은 owner부터(동률은 FIFO) · 일 보존 규칙 같음. 전역 `APM_JOB_MAX_CONCURRENT`(4)는 유지.
- ③ **속도 제어**: 자리가 먼저 공정하게 거르므로(속도 제어 앞 대기자는 최대 자리 수) 속도 제어 순서 규칙은 바꾸지 않는다 — 측정에서 불공정이 보이면 같은 규칙을 넣는다.
- ④ **혼잡 가시화**: 호출마다 `slot_wait_ms`·`throttle_wait_ms`를 재어 작업 기록·감사에 싣고(합계·최대), 상태 표면(`apm_health` 또는 설정 관리 현황 — 기존 노출 위치 실측 후 한 곳)에 소스별 `in_flight`·`waiting`·호출자 수를 낸다. 자리 대기 초과 오류 사유에 「동시 사용 혼잡(대기 n건 · 호출자 m)」을 적어 제니퍼 장애와 구분한다.
- ⑤ **용량 산정 규칙(문서 · §6)**: 자리 ≈ 초당 상한 × p95 응답 시간. 동시 사용자 U명이 각 N건을 부르면 응답은 대략 U·N/초당 상한 초 — 초당 상한은 소스 전체라 자리를 늘려도 넘지 않는다. 자리·초당 상한의 코드 기본값은 이 계획에서 바꾸지 않는다(D-347 ③ · 운영 `.env`로 조정).
- **기본 on**(D-356 ⑤) · 되돌리기: `JENNIFER_MAX_INFLIGHT_PER_CALLER=0`·`APM_JOB_MAX_CONCURRENT_PER_OWNER=0`(0 = 상한·공정 분배 끔 → 종전 순서와 비트 동일).
- **테스트**: ① 순위가 공정 분배보다 앞선다(poller·interactive 우선 불변 · 에이징 불변) ② 사용자 A가 동시 12건을 먼저 넣은 뒤 B가 1건 → B가 A의 남은 대기분보다 먼저 자리를 받는다 ③ 호출자 하나뿐이면 상한을 넘겨 자리를 다 쓴다(일 보존) ④ 작업 슬롯: A 작업 4개 + B 작업 1개 → B가 3번째 이내로 실행 ⑤ **다중 사용자 부하**: 가짜 전송(지연 1s·3s) × 사용자 10명 × 도구 혼합(W2 동시 경로 포함) — 굶주림 없음(각 사용자 첫 호출 시작이 공정 몫 안) · 호출자별 동시 ≤ 상한 · 자리 대기 초과 0건(지연 3s까지) · 결과 봉투는 단독 실행과 동일 ⑥ 되돌리기 값 = 종전 순서.
- **회귀 대상 파일 추가**: `adapters/throttle.py` · `domain/call_context.py` · `application/jobs.py` · `application/batch.py`(`_ItemScope.caller`) · `config.py` · `interface/config_admin_adapter.py` · 상태 표면 파일 · `.env.example`.

### W4 — 기록·측정

- D-356 상태 갱신 · `plans/INDEX.md` · 개발용 측정 스크립트 `apm_gateway/scripts/bench_parallel.py`(가짜 전송 지연·응답 크기 인자 → 순차/동시 · 풀 on/off 소요와 루프 최대 지연 표) — 폐쇄망 재측정 절차를 §6에 적는다.
- 매뉴얼(D-255): 내부 성능 변경이라 **대상 아님**. 운영 설정 키는 `.env.example`과 설정 관리 키 목록으로 노출.

## 4. 회귀

- 각 Wave: implementer가 자기 테스트 + `apm_gateway`에서 `../.venv/bin/python -m pytest tests/<바꾼·추가 파일>` · `python scripts/regress.py --no-tests --files <바꾼 파일…>`.
- 계획 끝(W3 뒤): team-lead가 `cd apm_gateway && ../.venv/bin/python -m pytest`(패키지 테스트 — 루트 수집 밖) + `python scripts/regress.py --base 16ea44fa --files <계획이 바꾼 파일…>`. 전체 회귀는 사용자 요청 시만.

## 5. W0 분류표

2026-11-02 팀 리드 · `application/*.py` 전수 + `adapters/jennifer`(await 루프 없음). 분류: ① 독립(동시 대상) · ② 종속(앞 결과로 다음 호출 결정·조기 종료) · ③ 캐시 1회성 · 범위 밖. 「W2」 칸: a = W2a · b = W2b · — = 바꾸지 않음.

| # | 파일:줄 · 함수 | 단위 · API | 분류 | 순서 민감 부수효과(입력 순서 후처리 대상) | W2 |
|---|---|---|---|---|---|
| 1 | resolver.py:253 `_load` | 도메인 · `instances` | ① | `inv.instances.extend` 순서 = 명단 순서 · `unavailable` | a |
| 2 | apm_core.py:235 `realtime` | (소스,도메인) · `realtime` | ① | `out[key]` · `limits.fail` | a |
| 3 | apm_core.py:258 `xview` 바깥 / 260 청크 while | (소스,도메인) / 1분 청크 · `transactions` | 바깥 ① / 청크 ② | `seen`(키에 `len(txs)` — 먼저 온 쪽 우선) · 청크 실패 `break` | a(바깥만) |
| 4 | apm_core.py:306 `hourly` | (소스,도메인) · `application_status` | ① | `rows.extend` · `limits.fail` | a |
| 5 | apm_core.py:437 `metric_plan` | 소스 · `catalog`(TTL) | ③ | — | — |
| 6 | apm_core.py:609 `fetch_errors` | 표기 후보(≤3) · `errors` | ② | 처음 비지 않은 결과에서 return | — |
| 7 | stats_tools.py:187 `_status_fetch` | (소스,도메인) · `status_stats`(+정렬 거부 재조회 ②) | 바깥 ① | `received` 순서(정렬 안정성) · 루프 안 `expect_calls(1)` | b |
| 8 | stats_tools.py:386 instance series | 인스턴스×지표 · `instance_metric_series` | ① | `empty`·`rows`·`limits.fail` | b |
| 9 | stats_tools.py:442 `_metric_catalog` | 소스 · `catalog` | ③ | — | — |
| 10 | scope_tools.py:337 `apm_service_status` | (소스,도메인) · `realtime_domains` | ① | `got.setdefault`·`failed.setdefault`(첫 오류) | b |
| 11 | scope_tools.py:413 `_business_defs` | 도메인 · `business_defs_of` | ① | `out.append` · 헬퍼 `limits.fail` | b |
| 12 | scope_tools.py:567 `_current_all` | 도메인 · `realtime_business` | ① | `rows` · `failed.setdefault` | b |
| 13 | scope_tools.py:597 `_current_named` | 업무 · `realtime_business` | ① | `missing`·`rows`·`failed.setdefault` | b |
| 14 | scope_tools.py:691 `metric_series` | 대상×지표 · domain/business series | ① | `empty`·`rows`·`limits.fail` | b |
| 15 | scope_tools.py:797 `_default_series_plan` | 소스 · `catalog` | ③ | — | — |
| 16 | scope_tools.py:839 `_business_by_id` | 도메인 · `business_defs_of` | ① | `out`·`unresolve` | b |
| 17 | health_tools.py:106 `apm_runtime_health` | 인스턴스×지표 · series | ① | `trend`·`trends`·`limits.fail` | a |
| 18 | health_tools.py:196 `apm_resource_pool` | (소스,도메인) · `active_services` | ① | `services`·`limits.fail` | a |
| 19 | transaction_tools.py:147 `apm_active_services` | (소스,도메인) · `active_services` | ① | `services`·`limits.fail` | a |
| 20 | transaction_tools.py:271 `apm_events` | (소스,도메인)×{events, errors} | ① (안쪽 #6은 ②) | `events/errors`·`limits.fail` | a |
| 21 | compare_tools.py:121 `_change_rows` 바깥 / 124 조각 while | (소스,도메인) / 25시간 조각 · `source_changes` | 바깥 ① / 조각 ② | `seen`(먼저 온 쪽 우선) · 조각 실패 `break` | b(바깥만) |
| 22 | compare_tools.py:264 `apm_transaction_trace` | (소스,도메인) · `transactions_by_guid` | ① | `hits`·`unique`(먼저 온 쪽 우선) | b |
| 23 | compare_tools.py:461 `apm_change_impact` | 변경 건×(전·후) · `_impact_side` | ① | 루프 안 `expect_calls(1)` · `limits.extend(sub)` | b(봉투 동일 못 맞추면 보류·근거) |
| 24 | compare_tools.py:626 `apm_period_compare` | 인스턴스×구간 · `application_status` | ① | `failed[period]` 순서가 `[한계]` 문구 | b |
| 25 | fleet_tools.py:203 `_groups` | 소스 · `inventory`(캐시) | ③ | — | — |
| 26 | fleet_tools.py:323 `_realtime_records` | (소스,도메인) · `realtime` | ① | `records` 순서(순위 동점) · `CONTRACT_VIOLATION` 즉시 raise | b |
| 27 | fleet_tools.py:530 `_period_records` | 도메인×인스턴스 · `application_status` | ① | 다수 목록 · `CONTRACT_VIOLATION` 즉시 raise | b |
| 28 | fleet_tools.py:711 `events` | (소스,도메인) · `events_checked` | ① | `coverage`·`rows` · `CONTRACT_VIOLATION` raise | b |
| 29 | instance_tools.py:85 `_fill_host_address` | 웹 세션(소스 lock 직렬) | 범위 밖 | — | — |
| 30 | instance_tools.py:106 `_domain_descriptions` | 소스 · `inventory` | ③ | — | — |
| 31 | instance_tools.py:497 `_business_trace` 1단 | 도메인 · `business_defs_of` | ① | `matched` | b |
| 32 | instance_tools.py:517 `_business_trace` 2단 | 매칭 도메인 · `_business_seen_of` | ① (안쪽 #33 ②) | `out` | b |
| 33 | instance_tools.py:557 `_business_seen_of` 청크 while | 1분 청크 · `transactions` | ② | `break` · 완전할 때만 캐시 | — |
| 34 | manage_tools.py:314 `_run`(공용) | 호출부 단위 — 7곳 · apm_config 7종·apm_environment·apm_users | ① | `done/failed` 순서(`_source_statuses` 첫 오류) · `absent.append`(apm_users) | a |
| 35 | manage_tools.py:670 `_process_instance` 결과 | 소스 · `inventory` | ③ | — | — |
| 36 | tools.py:211 `app_health_preflight` | 대상 · `resolve`(캐시) | ③ | — | — |
| 37 | batch.py:264 `run_batch` | `targets` 항목 · 하위 도구 | ① | `_Plan` 단일 항목 가정 | W3 |

- **공통 순서 위험**: 클라이언트 `_absorb`가 HTTP 1회마다 `current_scope().note()/masked()`를 부른다 — 동시 실행이면 자격증명 메모가 응답 도착 순으로 쌓인다. `gather_ordered`가 항목별로 메모·가린 칸을 모아 **입력 순서로 다시 신고**한다(호출 훅·우선순위·호출 수는 바깥 맥락 그대로).
- **오류 경로 호출 수 차이(허용 · 봉투 불변)**: #26~#28의 `CONTRACT_VIOLATION` 즉시 raise와 청크 `break`(#3·#21 — 바깥만 동시라 안쪽 생략은 유지)는 순차에서 뒤 단위를 부르지 않는다. 동시 실행에서는 이미 나간 호출이 있어 감사 `api_calls`가 순차보다 클 수 있다(결과 처리·raise 지점은 입력 순서라 봉투는 같다).
- **루프 아닌 순차 독립 호출**(transaction_tools.py:462/467/484 profile 3호출 · `apm_app_health`의 realtime→xview→hourly · `_impact_side` xview·errors)은 이 계획 범위 밖 — 후속 후보.

## 6. 폐쇄망 측정 (배포 뒤)

- `APM_GATEWAY_LOG_LEVEL=DEBUG`에서 같은 질의를 `APM_FANOUT_CONCURRENCY=1 · APM_PARSE_WORKERS=0`(현행)과 기본값으로 각각 실행 — 도구 `elapsed_ms`(감사) · `apm http … elapsed_ms` 합 · 스풀 로그 건수 · RSS(워커 수 × 증가분)를 비교한다.

## 7. 결과

(완료 시 기록)
