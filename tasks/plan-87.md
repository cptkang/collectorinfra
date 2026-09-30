# Plan 87 — 제니퍼 APM 연동 구현 계획 (J1~J4)

> 맵 `spec/CAPABILITY-MAP-87.md` · 스펙 `spec/SPEC-apm-gateway.md` · `spec/SPEC-apm-sre-agent.md` · `spec/SPEC-apm-noise-gate.md` · 계획서 `plans/87`
> 보류: J5(`plans/121` TP-9.1·9.2·10.5 코드 0) · J6(G-6 "J3 완료 + 목업 검증 뒤") · J7(G-9 소비자 미확정) · J0-L-b·J0-O(사용자·외부 전제)

## 의존 그래프

```
apm-gw-core ──> apm-gw-adapter ──> apm-gw-resolve ──┬──> apm-gw-tools ──> (MCP 계약) ──> apm-sre   (J3 · sre_agent)
apm-gw-signals ─────────────────────────────────────┤
                                                    └──> apm-gw-poller ─> (alarm:raw 계약) ─> apm-noise (J4 · noise_gate)
```

소비측 두 모듈은 **계약 문서만 보고** 게이트웨이와 병렬로 만든다(import 0 — R-21 계약 픽스처 복제).

## 수직 슬라이스 순서

| # | 슬라이스 | 왜 이 순서인가 | 검증 |
|---|---|---|---|
| 1 | 허용목록 정본 + 클라이언트 | 모든 호출의 1차 통제 — 거부 = HTTP 0회 | §5.2(e) 거부 입력 전건 · 카탈로그 사본 대조 · MockTransport 호출 수 0 |
| 2 | 정합(resolver) | 모든 도구의 대상 확정 | 목 서버 + 합성 픽스처 — FQDN·대소문자 · prefix medium · 미해소 오류 |
| 3 | WAS 판정(domain) | 도구·폴러가 공유 · 순수 함수 | 목업 시나리오 6종 + 나머지 2 kind · 임계 파일 = 기본값 |
| 4 | 도구 8종 + health | 소비자 계약의 본체 | 계약 테스트(목 서버) · `/__mock/hits` 허용목록 밖 0 · 토큰 0회 |
| 5 | MCP 서버·Bearer·감사·엔트리 | 소비자가 붙는 표면 | FastMCP `call_tool` · Bearer 401 · SSE 실기동 스모크 |
| 6 | 폴러 | `alarm:raw` 생산 | 가짜 Redis — 멱등·재기동·같은 ms·백오프·계약 위반 중지·XADD 실패 재시도 |
| 7 | 소비측(sre_agent · noise_gate) | 계약 확정 뒤 병렬 | 각 패키지 스위트 신규 실패 0 · 플래그 off 비트 동일 |
| 8 | 게이트·문서 | 마감 | ruff·mypy·arch_check·overfit_check · CLAUDE.md · docs/31 · 계획서 · D-195 부기 |

## 리스크와 완화

| 리스크 | 완화 |
|---|---|
| 실데이터 모양 없음(녹화본이 라이선스 없는 상태) | 합성 픽스처는 테스트 임시 디렉터리에만 · 녹화본으로 "도메인 0건 → source_unavailable" 확인 · J0-L-b 재녹화 뒤 교체(한계 보고) |
| 2단 중첩 모듈을 `arch_check`가 해석 못 함 | G-11 기준 ②에 따라 편입하지 않고 게이트웨이 테스트에 계층 방향 AST 검사 |
| 병행 세션 미커밋 변경과 충돌 | 소비측은 자기 패키지·자기 줄만 · 공용 파일 편집 직전 `git diff` 재확인 · stash·reset 금지 |
| 복제 코드 드리프트(Bearer·계약) | 원본과 판정 문장 대조 테스트 · 계약 픽스처를 양쪽에 복제 |

## 검증 체크포인트

- 슬라이스 1 후: 거부 입력 전건이 네트워크 0회로 막힌다
- 슬라이스 4 후: 목 서버 접근 기록에 허용목록 밖·쿼리 token 0건
- 슬라이스 6 후: 같은 이벤트를 몇 번 다시 봐도 `alarm:raw` 1건
- 슬라이스 8 후: 게이트웨이 스위트 · `sre_agent` · `mcp_server` · 루트(본체+noise_gate) 신규 실패 0

---

## J8 — 다중 제니퍼 소스 (2026-09-30 · `plans/87` §0.13 · D-287)

> 스펙 `spec/SPEC-apm-gateway.md`(J8 표지 — §2.1 설정 · §3 인자·봉투 · §4 · §5 페이로드 · §6-4·8) · `spec/SPEC-apm-noise-gate.md` §3-2~4 · `spec/SPEC-apm-sre-agent.md` §3-3·7.
> 범위 밖(보고만): 본체 `apm_query` 위치어 좁히기(G-16)·승계 패싯 `apm_source_id`·첫 홉 `source_id` 보존 = `plans/125`(D-281) · F-4 · `CLAUDE.md`.

### 의존 그래프

```
gw-config(소스 설정) ──> gw-sources(소스 묶음·부분 실패) ──┬──> gw-tools(계약 추가) ──> (MCP 계약) ──> sre_agent 지침
                                                           ├──> gw-poller(커서·멱등·dbId) ──> (alarm:raw) ──┐
registry sources[] ──> zones.db_id_to_zone(F-7) ──> 알람 라우트 존 판정                                   │
                   └─> noise_gate(배지 툴팁 · app_impact source_ids · 힌트) <─────────────────────────────────┘
```

게이트웨이와 소비측은 계약 문서(§0.13 M-6·M-7)만 보고 병렬로 만든다(import 0 — R-21).

### 수직 슬라이스 순서

| # | 슬라이스 | 검증 |
|---|---|---|
| 1 | 설정 — `JENNIFER_SOURCES` + 접두 키 · 단일 설정 호환(`default`) · 동시 설정·필수 키 누락·형식 위반 기동 실패 | `tests/test_config.py`(다중 소스 13건) · `.env.example` 접미 키 커버리지 |
| 2 | 소스 묶음 — 소스별 클라이언트·정합기 · 선택(`source_ids`) · 병렬 인벤토리 · 부분 실패 `[한계]`·`sources[]` · 전부 실패 오류 · 실패·빈 인벤토리 30초 캐시(F-3) | `tests/test_multi_source.py` |
| 3 | 도구 계약 추가 — (소스, 인스턴스) 키 · 호출 묶음 (소스, 도메인) · 행·`profile_ref`의 `source_id` · `instance_refs` · `apm_transaction_profile(source_id)` · 신호 `source_id`·중복 제거 키 · `gateway_health` 소스별 행 · 감사 `sources=` | 기존 계약 테스트(단언 3곳 갱신) + 목 서버 2개 충돌 경우 |
| 4 | 폴러 — 소스 병렬 · 커서·멱등 키·백오프에 `source_id` · `dbId` `jennifer_<id>` · `apm.source_id` · `resourceAncestry` | 두 소스 같은 값 이벤트 2건 발행 · 재기동 중복 0 · 단일 설정 v4 식별자 |
| 5 | 레지스트리 `solutions[apm].sources[]` · 로더 검증 · `alarm_source` · `db_id_to_zone`(F-7) | `tests/test_routing/test_plan87_j8_registry_sources.py` · `tests/test_api/test_plan87_j8_apm_alarm_zone.py` · 프롬프트 렌더 지문 11종 대조 |
| 6 | `noise_gate` — 배지 툴팁 존 · `app_impact` 존 좁히기 · 힌트 `source_id` / `sre_agent` 지침·힌트 | `noise_gate/tests/test_plan87_j8_multi_source.py` · `sre_agent/tests/test_plan87_j8_source_id.py` |
| 7 | 실프로세스 — 목 서버 2개 + 임시 Redis + 게이트웨이 · 로컬 Docker 1대를 두 소스로 | 세션 scratchpad 스크립트(저장소 밖) · `RUN_DOCKER_IT=1` 두 소스판 |
| 8 | 게이트·문서 | ruff·mypy(게이트웨이)·arch_check·overfit_check · SPEC 3종 · `docs/31` · 매뉴얼 · 계획서 §0.14 · D-287 부기 |

### 리스크와 완화

| 리스크 | 완화 |
|---|---|
| 레지스트리 `sources[].id` ↔ 게이트웨이 `JENNIFER_SOURCES` 불일치 | 게이트웨이는 모르는 `source_ids`를 `invalid_argument`(설정된 id 목록)로 거절 · `noise_gate`는 판정 유지 + 사유(재시도 없음) · 운영 절차 `docs/31` §4.5 |
| 느린 소스 하나가 응답을 늦춤(R-33) | 소스별 `_API_TIMEOUT_SECONDS` · `noise_gate`는 존으로 좁혀 부른다 |
| 프롬프트 바이트 변화(레지스트리에 키 추가) | 렌더 지문 11종을 세션 시작 커밋 worktree와 대조(동일) |
