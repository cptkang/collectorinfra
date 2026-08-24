# 71. CPU/메모리 실시간 사용률 조회 — 폴스타 measurement API 데이터 평면

> 작성일: 2026-07-24
> **상위 문서**: Plan 75 §1 (검토·확정 완료 — 2안 API 채택, 실측 5건 해소, 라우팅 게이트 B안 확정)
> **관련 결정**: D-003(읽기전용 — HTTP GET만), D-035(결정적 게이트, LLM 분류 불의존), D-066(LIMIT 계열과 무관하나 원문 신호 state 운반 원칙 공유)
> **신규 결정**: **D-144 등재 완료**(2026-07-24, 실시간 사용률 데이터 평면 — `docs/02_decision.md`)
> **상태**: **구현 완료 (2026-07-24)**. **옵트인 기본 OFF**(`POLESTAR_REST_REALTIME_USAGE_ENABLED=false`) — 비활성 시 기존 경로 바이트 무변경(회귀 0). 활성화는 `.env`에 `POLESTAR_REST_REALTIME_USAGE_ENABLED=true` 1줄. 폐쇄망 검증(§6-2·3) 대기.
>
> **구현 결과**: §3 표의 6개 구성요소 전부 구현 — ①[config.py](../src/config.py) `PolestarRestConfig`(base_urls_csv에 b0 포트 포함 기본값·timeout 10s·chunk 200·stale 15분) ②[clients/polestar_measurement.py](../src/clients/polestar_measurement.py)(청크 병렬+전체 가드·확정 shape 파서·부분 실패 병합) ③[query_gen_common.is_realtime_usage_query](../src/utils/query_gen_common.py)(B안 게이트) ④[nodes/realtime_usage.py](../src/nodes/realtime_usage.py)(2단계 하이브리드·미수집/수집 지연 표기·감사로깅) ⑤[subagents.py](../src/orchestration/subagents.py)(data_query 분기 + 원문 기준 의도 승격 realtime_usage_intent) ⑥[tests/test_nodes/test_realtime_usage.py](../tests/test_nodes/test_realtime_usage.py) 17종 통과(§4 게이트 경계 표 전부 고정). 그래프 직행 경로는 미배선(활성 런타임=트랙 A, D-144 주의 참조).

---

## 1. 확정 입력 (Plan 75 §1.3 실측 — 재논의 불요)

| 항목 | 확정값 |
|---|---|
| API | **2안** `GET {base}/rest/v1/dashboard/measurement?resourceIds={ids}&definitions={def}&type={type}&timeSelector=recent&count=1` |
| 파라미터 | `timeSelector`(오타 아님), `count=1` 고정 |
| 지표 | CPU: `definitions=Utilization&type=server.Cpus` / MEM: `definitions=UsedPercent&type=server.Memory` — 지표별 1콜 |
| 응답 shape | 최상위 `date`(호출 시각) + `data.measurement[]`(resourceId·resourceName·min/avg/max·time(수집 시각, Unix ms)·targetId) + `id` |
| 성능 | 200대/콜 확정(yd 814ms·gp 2,460ms/37KB). 200 초과 시 청크 분할+병렬 |
| 타임아웃 | measurement 전용 별도 설정(기본 10s) — 프로세스 API 3s 재사용 금지(gp 200대 2.46s 경계) |
| base_url | gp=`http://polestar.kbonecloud.com`, yd=`http://yd-polestar.kbonecloud.com`, **b0=`http://10.37.16.51:9010`(포트 주의)** |
| 라우팅 게이트 | **B안**: 기간 표현 없음 + "실시간/현재/지금" 명시 시에만 API 경로. "현황" 단독은 DB 유지 |

## 2. 아키텍처 — 2단계 하이브리드 (결정적 조립)

```
질의 → [게이트 B안: 결정적] → ①서버 목록 SQL (cmm_resource, 결정적 조립 — LLM 우회)
                              → ②measurement API (200대/콜 청크·존별 병렬·전체 타임아웃 가드)
                              → ③병합 (요청 ID 대조 → 미수집 표기, time→KST 신선도 플래그)
                              → organized_data rows (기존 output 경로 재사용)
```

- **게이트 판정은 원문 기준** — sub_query 재작성과 무관하도록 D-066 후속7과 동일하게 state 승격 또는 원문 접근 지점에서 판정(구현: `_make_isolated_input` 시점의 `state["user_query"]`).
- ①은 LLM 없이 코드가 조립(D-035): `SELECT id, name, hostname FROM {schema}cmm_resource WHERE resource_type='server.Server' AND dtime IS NULL` (+엔진별 한정). CPU/MEM 값은 DB가 아니라 API에서 오므로 메트릭 조인 불필요 — few-shot 캡 모방(D-066 후속8) 계열 위험 원천 제거.
- ③ 병합 규칙: 요청 resourceIds − 응답 resourceId = "미수집"; `time` 15분 초과 = "수집 지연" 플래그; avg 소수 2자리 반올림.
- **폴백 침묵 금지**: API 실패(비200/타임아웃/파싱 실패) 시 해당 존은 기존 SQL 경로로 폴백하고 응답에 "실시간 조회 실패 — DB 마지막 수집값" 명시.
- **감사로깅**: API 호출도 audit 로그(대상 존, 서버 수, 소요 ms, 성공/실패).

## 3. 구현 구성요소

| # | 구성요소 | 파일 | 내용 |
|---|---|---|---|
| 1 | 설정 | `src/config.py` | `PolestarRestConfig`(신규 섹션): `base_urls_csv`(db_id=url CSV, 기본값에 b0 포함), `measurement_timeout_seconds=10`, `measurement_chunk_size=200`, `realtime_usage_enabled=false`. 기존 `AlarmConfig.process_api_base_urls_csv`는 유지(하위호환) — 프로세스 API 소비처는 무변경, 후속 통합은 별도 |
| 2 | 클라이언트 | `src/clients/polestar_measurement.py` | httpx GET, 청크 분할, 존별 병렬(asyncio.gather) + 전체 타임아웃 가드(asyncio.wait_for), 응답 파싱(확정 shape), 실패 시 None(graceful) |
| 3 | 게이트 | `src/utils/query_gen_common.py` | `is_realtime_usage_query(query)`: 결정적 — ("실시간"/"현재"/"지금") AND (cpu/씨피유 또는 메모리/mem) AND 기간 표현 부재(`resolve_stat_month_range` None AND "지난/개월/추이/통계/월별" 미포함) |
| 4 | 핸들러 | `src/nodes/realtime_usage.py`(신규) | ①존별 서버 목록 SQL(결정적) ②API 호출 ③병합 → `{organized_data, query_results, source:"realtime_api"}`. 서버 목록 실패/API 실패 존은 폴백 사유 구조화 |
| 5 | 배선 | 오케스트레이션 data_query 진입부 | 플래그 ON + 게이트 참(원문 기준) + 대상 DB가 폴스타일 때만 realtime 핸들러로 분기, 그 외 기존 SQL 파이프라인 |
| 6 | 테스트 | `tests/test_nodes/test_realtime_usage.py` | 게이트 경계(B안 표면어·기간 혼합 질의), 청크 분할, 응답 파싱(확정 shape 고정), 미수집/지연 플래그, 병합, 폴백 사유 |

## 4. 게이트 경계 케이스 (테스트 고정 대상)

| 질의 | 판정 | 근거 |
|---|---|---|
| "은행존의 모든 서버들에 대해 **실시간** CPU 사용률을 조회해줘" (§2 버튼) | **API** | B안 표면어 + 기간 없음 |
| "…**현재** 메모리 사용률" / "**지금** CPU 얼마나 써?" | API | 동상 |
| "…CPU 사용률 **현황**을 조회해줘" | DB | "현황" 단독은 B안 비트리거 |
| "**지난달** 실시간 CPU…" (혼합) | DB | 기간 표현 우선 — 통계 경로 |
| "실시간 **디스크** 사용률" | DB | 지원 지표(CPU/MEM) 외 — Plan 75 §1 범위 |
| "서버 abc01 지금 CPU" (단일 서버) | API | 서버 목록 SQL이 hostname 필터로 좁힘(가능하면), 아니면 존 전체 후 필터 |

## 5. 비범위 (후속)

- 1안 lastdata 드릴다운(targetId 활용 기간별 조회) — 필요 시 후속.
- 디스크/네트워크 지표 확장.
- `process_api_base_urls_csv` → 제네릭 통합 rename(Plan 75 §1.3-5) — 프로세스 API 소비처 회귀 검토 필요라 본 계획에서는 신규 섹션 추가로 갈음, rename은 별도 턴.
- UI 전용 표시(신선도 배지 등) — 1차는 기존 테이블 렌더 재사용.

## 5.4 폐쇄망 검증 결과 및 보완 (2026-07-24)

- ✅ **API 조회 동작 확인**(사용자 실측) — 플래그 ON 후 실시간 CPU 사용률이 measurement API로 반환됨.
- 보완 1 — **처리 현황 라벨**: task agent 고정 라벨("DB 조회")이 실제 경로와 불일치 → 실행 결과의 `source="realtime_api"`를 `_summarize_tasks`가 노출하고 프론트가 라벨을 "실시간 API 조회"로 교체. ※ **의도 분석 단계(실행 전)는 여전히 "DB 조회"로 표시** — 실행 전에는 API/SQL이 미확정(플래그·폴스타 대상·폴백 여부)이므로 의도된 정직한 표기이며, 작업 실행 완료 시점부터 라벨이 바뀐다.
- 보완 2 — **상태 "미수집" 원인 구분**: 종전에는 두 원인(API 청크 실패 vs measurement에 수집값 부재)이 모두 "미수집"으로 표기돼 파악 불가 → `MeasurementResult.failed_ids`로 실패 청크 소속 서버를 식별해 **"조회 실패"**(API 구간 오류 — 재질의로 해소 가능)와 **"미수집"**(recent 수집값 부재 — 수집 중단·에이전트 미설치·모니터링 미등록)을 분리. summary에 상태별 집계(정상/수집 지연/미수집/조회 실패 N대)와 원인 안내 문구 자동 포함.
- ~~미수집 판독 기준(1차)~~ → **반증 실측(사용자, 2026-07-24)**: yd에서 '미수집' 표기된 서버의 ID를 콘솔에서 조회해 **단건 API 호출하면 정상 반환**됨 — "수집 대상 아님" 가설로는 설명 불가. 갱신된 유력 가설(우선순위순):
  1. **ID 불일치 — 동일 서버명 중복 등록**: 재등록된 구행이 `dtime IS NULL`로 잔존하면 서버 목록 SQL에 구행 id가 섞이고, 그 id는 measurement에 데이터가 없어 '미수집'으로 보임(수동 호출에 쓴 콘솔 id=신행이라 정상). 같은 서버명이 결과 표에 2행(정상+미수집)으로 나타나는 것이 시그니처.
  2. **API 배치 상한**: 한 콜의 resourceIds 대수가 일정 수를 넘으면 응답 measurement가 절단될 가능성(Plan 75 실측은 응답 크기만 확인, 항목 수 미검증).
- **진단 계측(2026-07-24 추가)**: ①청크별 `요청 N대 → 수신 M대, 미수신 ID 샘플` INFO 로그 ②존 합계 로그 ③결과에 **'리소스 ID' 컬럼** 노출(미수집 행의 id를 수동 호출 id와 즉시 대조) ④동일 서버명 중복 등록 감지 시 summary 경고.
- **판별 절차**: ①재실행 → 미수집 행의 '리소스 ID'와 수동 호출에 쓴 ID 비교 — **다르면 가설 1 확정**(같은 서버명 2행 여부도 확인). SQL 대조: `SELECT id, name, hostname, dtime FROM {스키마}cmm_resource WHERE name = '<서버명>' AND resource_type = 'server.Server'`. ②ID가 **같은데** 미수신이면 가설 2 — `.env`에 `POLESTAR_REST_MEASUREMENT_CHUNK_SIZE=50`으로 낮춰 재실행, 미수집이 사라지면 배치 상한 확정(상한 실측값으로 기본 청크 조정). ③로그의 "미수신 ID 샘플"로 어느 청크에서 빠졌는지 확인.
- ~~가설 1/2 판별~~ → **원인 확정(사용자 실측, 2026-07-24)**: 미수집 서버는 대부분 **Power off 또는 에이전트 통신 이슈** — measurement에 최근 수집값이 없는 것이 맞았고, 파이프라인 결함 아님(단건 수동 호출이 됐던 서버는 별개 사례로 해소). 반영: 서버 목록 SQL에 `avail_status`를 포함해 결과에 **"가용성" 컬럼**(정상/비정상(중지/통신이상)) 표기 — 미수집 원인이 행에서 바로 판독된다. summary 안내 문구도 실측 원인으로 갱신.
- **미수집의 두 번째 유형 — 일시 미수집 (사용자 실측, 2026-07-24)**: 가용성 '정상' 서버가 measurement `[]`를 반환하다가 반복 호출하면 종래 값이 나오는 현상. **`recent`의 시간 창과 수집 주기(60초 — 실측 time 간격)의 경합**으로 이해한다: `recent`는 최신 수집 슬롯 부근만 보므로, 에이전트 전송 지연·샘플 1회 누락·수집→조회 가능 시점 사이의 인제스트 지연이 겹치는 순간에 조회하면 빈 배열이고, 다음 수집 주기에 채워진다. 즉 **미수집은 영구 속성이 아니라 조회 순간의 스냅샷 상태**다.
  - 판독 규칙: **가용성 비정상 + 미수집 = 지속적**(Power off/에이전트 이슈, 대부분) / **가용성 정상 + 미수집 = 일시적일 수 있음**(재질의 시 해소 — summary에 안내 문구 반영).
  - 완화 옵션 검토: ⓐ 현행 유지 + 문구 구분(채택 — 대량 조회에서 소수 flapping은 스냅샷 특성상 자연스럽고, 규모 대비 비용 0) ⓑ "가용성 정상+미수집" 서버만 1회 재조회(다음 주기 대기 필요 — 수 초~1분 지연을 응답에 추가하게 되어 기각, 필요 시 후속) ⓒ timeSelector를 day 등으로 확대(의미가 "지금"→"금일 통계"로 변질 — 기각).

## 5.5 트러블슈팅 — "실시간" 질의가 SQL로 처리될 때 (2026-07-24 폐쇄망 실측)

증상: "…의 모든 서버들에 대해 실시간 CPU 사용률을 조회해줘"가 API 대신 SQL
(`cmm_metric_stat_h` 최근 1시간 조인)로 처리됨. **점검 순서**:

1. **플래그(최다 원인)**: `.env`에 `POLESTAR_REST_REALTIME_USAGE_ENABLED=true`가 있는지.
   기본 OFF이며, OFF면 SQL 경로가 **정상 동작**이다(LLM이 "실시간"을 최근 1시간 통계로
   해석하는 것도 자연스러운 DB 폴백). 서버 재기동 필요.
2. 로그 확인 — 침묵 스킵 방지용 진단 로그(v9 추가):
   - `realtime_usage 의도 감지 — 플래그 OFF…` → 1번 케이스 확정.
   - `realtime_usage 스킵: 대상에 비폴스타 DB 포함` → 라우팅이 폴스타 외 DB를 섞은 경우.
   - `realtime_usage 폴백 — 기존 SQL 파이프라인으로 진행` → API 호출 실패(base_url·타임아웃)
     — 감사 로그의 `[REALTIME-API]` 항목에서 실패 존 확인.
   - 아무 로그도 없음 → 게이트 미발동(표면어 확인: "실시간/현재/지금" + CPU/메모리 + 기간 표현 없음).

## 6. 검증 계획

1. 단위: §3-6 테스트(모두 mock — httpx는 respx/monkeypatch, DB는 stub rows).
2. 폐쇄망: 플래그 ON 후 §2 버튼 질의(은행존) → 응답에 API 수치·수집 시각 표기, 감사 로그에 API 호출 기록. 플래그 OFF → 기존 SQL 경로 바이트 동일(회귀 0).
3. 실패 주입: base_url 미설정 존 → SQL 폴백 + 사유 명시 확인.
