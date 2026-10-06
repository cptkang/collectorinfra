# 31. 제니퍼(JENNIFER) APM 연동 가이드 — 준비 · 설정 · 읽기 전용 통제 · 검증 · 운영

> **정본 범위**: 제니퍼 APM을 이 저장소에 붙이는 **운영자 절차**를 한 곳에 모은 문서다. 설계 근거와 결정 과정은
> `plans/87-WIP-jennifer-apm-integration.md`(이하 "87")가 정본이고, 이 문서는 그 계획을 **연동 관점 하나로** 재구성한다.
> 형식은 `docs/27_prometheus_integration_guide.md`를 따른다.
> **작성**: 2026-09-29(87 v2.2와 함께) · **v3 개정**: 2026-09-29 — 제니퍼 연동을 **독립 최상위 패키지 `apm_gateway/`**(자체 MCP 서버 ·
> 독립 프로세스)로 분리하는 구조가 확정됐다(**D-274** — D-119 ① 개정 · 87 §0.7). 기준 HEAD `2e635a9` + 미커밋 작업 트리.
> **v3.1 개정**: 2026-09-29 — 87 게이트 G-1~G-12 **전건 확정** · **D-195 본문 등재**(①②③ — L2는 ~~D-003 실행 평면 한정 예외~~ **v3.3 재기록: D-003 범위 밖 · 예외 아님**) · 공유 OpenMetrics 기계의
> 2.0 협상 **수정 완료**(G-10) · **로컬 Docker 제니퍼 검증 환경(J0-L)** 추가(§3.8 · 제품 사실은 「조사 대기」 → **v3.2에서 [J-24] 조사 결과로 채움**).
> **v3.2 개정**: 2026-09-29 — **Open API 공식 스펙 대조 반영**([J-23] — 정본 5.6.4 스펙 **39경로·63오퍼레이션** 전수 파싱 · 52건 중 정정 24 · 스펙에 없음 3 ·
> 확인 불가 2 · 실 서버 호출 0). 경로·파라미터는 이제 "스펙 5.6.4 확인" 또는 「확인 불가 — J0-L」로 표기한다. 허용목록은 **메서드 + 경로 정확 일치**다(§5.3).
> **v3.3 개정**: 2026-09-29 — **로컬 Docker 제니퍼 실측(J0-L-a · 라이선스 없음 · Open API 40건)** 반영(87 §0.10). §3.8을 실제 파일·명령 기준 【현재 가능】으로
> 다시 썼고, 실측으로 확인된 오류 모델(HTTP 500 + `exception.message`) · `profile.txt` Accept · 쿼리 토큰·민감 GET 통과 · 사용량 단위를 §3.6·§5.3·§5.6·§6·§8.2·§11에 넣었다.
> 5.7.0.1 로컬 설치본에서 잰 사실은 "(로컬 실측)"으로 표기한다 — 운영 버전에서 다를 수 있다(87 R-25).
> 같은 날 부기: **L2 기록 방식 확정** — D-003 예외가 아니라 **D-003 범위 밖**(사용자 확정 "권고" · §0 · §5.4 · §12.1) · **목 Open API 서버·녹화 하네스**(사용자 지시
> *"목 서버와 녹화 하네스도 만들어라"* — §3.8 · §10.1 · §11 · 실서버와 40건 불일치 0).
> **v4 개정**: 2026-09-29 — 사용자 지시 *"87번 계획을 구현하라."*에 따라 **J1~J4를 구현했다**(게이트웨이 `apm_gateway/` · `sre_agent` 소비측 · `noise_gate` 소비측).
> 그 부분은 【현재 가능】으로 옮겼다. **J5(채팅 질의) · J6(L2) · J7(OpenMetrics)은 보류**다 — 선행·게이트 조건이 안 됐다(§12.2). 운영 투입 전에는 J0-O(운영 실측)가 필수다.
> **v4.1 개정**: 2026-09-29 — 사용자 지시 *"구현한 내용을 확인하여 docs폴더의 31번 가이드도 업데이트하라."* — 구현 코드와 절마다 다시 대조했다(키·기본값·상수·파일·
> 심볼·테스트 이름). 같은 날 **로컬 Docker 제니퍼 실서버 검증**(87 §0.12 — 게이트웨이·소비측 58항목 통과 · 라이선스 없는 범위) 결과와 거기서 새로 안 동작(토큰 사용량
> 반영 지연 · 빈 인벤토리 캐시 · 401 사유 · 기준 URL 경로 302)을 §3.8·§4.5·§5·§6.1·§8.4·**§10.2**·§11에 넣었다. 남은 「예정」 표기는 J5·J6·J7과 미구현 `was_object` 브릿지 몫뿐이다.
> **v5 개정**: 2026-09-30 — 사용자 지시 *"87번 계획을 구현하라."*(Wave J8) — **다중 제니퍼 소스**(은행존·공동존·레거시 등 뷰 서버 N개)를 구현했다(87 §0.14 · **D-287**).
> 게이트웨이 1개가 소스 N개를 묶는다 — 설정 `JENNIFER_SOURCES` + `JENNIFER_<ID>_*`(§4.2) · 소스 ↔ 존 = 루트 레지스트리 `solutions[apm].sources[]`(§4.4) ·
> 도구 인자 `source_ids`·`profile_ref.source_id`(§6) · 알람 `dbId` `jennifer_<id>`(§8.2) · **존 구독자에게 APM 알람이 전달된다**(F-7 해소 — §8.4 ⑥ · §11) ·
> `app_impact` 존 좁히기(§4.7 · §8.4 ①) · 실패·빈 인벤토리 30초 캐시(F-3 해소 — §4.5) · §10.2 두 소스판. 단일 설정(`JENNIFER_API_URL`만)은 v4와 같다.
>
> **먼저 알아 둘 것 — 87은 J1~J4까지 구현됐다(2026-09-29).** 게이트웨이 코드는 `apm_gateway/apm_gateway/`에 있고, 계약 테스트는 목 Open API 서버와
> 스펙 5.6.4 스키마로 만든 합성 픽스처로 돈다 — **실데이터 모양은 J0-L-b(평가판 라이선스) 녹화 전까지 미검증**이다. 절마다 아래 표지로 나눈다.
> 표지가 없는 서술은 배경 설명이다.
>
> | 표지 | 뜻 |
> |---|---|
> | **【현재 가능】** | 코드·설정이 저장소에 있고 지금 쓸 수 있다. 근거 `file:line`을 붙였다 |
> | **【계획 — 87 §x · Jn】** | 87의 Wave `Jn`이 구현된 뒤의 절차다. **지금 따라 하면 동작하지 않는다**(v4 기준 J5·J6·J7) |
> | **「예정 이름」** | 코드에 아직 없는 패키지·설정 키·도구·파일 이름이다(v4 기준 J5·J6·J7과 미구현 `was_object` 브릿지 몫만 남았다) |
> | ~~「조사 대기」~~ | (v3.1에서 쓴 표지 — **v3.2에서 모두 해소**) 확인한 사실은 [J-23]·[J-24]로 채웠고, 공개 자료가 답하지 않는 것은 「확인 불가」로 적는다 |
>
> 제니퍼 제품 사실은 87 §12.3의 **[J-xx]** 근거를 인용한다. 87에서 "추정(△)"이나 "미확인(✖)"으로 둔 사실은 여기서도 그렇게 적는다.
> 확정 사항은 이 문서 전체의 전제다 — **G-1 API 위주**(DB·RDB Export 경로 미채택) · **G-2 표준 연동 규격 = CNCF OpenMetrics 1.0** ·
> **G-8 제니퍼 공식 MCP 서버 미채택(Open API 직접 호출)** · **D-274 독립 패키지 `apm_gateway/`** · **D-195 제니퍼 연동(①②③)**.

---

## 목차

0. 30초 요약 — 지금 상태
1. 연동 아키텍처
2. 용어
3. 제니퍼 측 준비 (§3.8 로컬 Docker 검증 환경 — J0-L)
4. 본체 측 설정
5. 읽기 전용 통제 (D-003 · D-189)
6. 도구 목록과 입출력 계약
7. 소비자별 사용 흐름 — 조사 · 채팅 질의
8. 이벤트 연동 — API 폴링과 어댑터 push
9. OpenMetrics 노출 — 선택 트랙 J7
10. 검증 절차 — 로컬 목 → 로컬 Docker 제니퍼 → 로컬 MLX → 내부망
11. 트러블슈팅
12. 미결 항목과 확정 결과
13. 참조
14. 변경 이력

---

## 0. 30초 요약 — 지금 상태

| 항목 | 상태 (2026-09-29 실측) |
|---|---|
| 제니퍼 연동 코드 | **J1~J4 구현(v4 · 2026-09-29)** — 게이트웨이 `apm_gateway/apm_gateway/`(허용목록·클라이언트·정합·`apm_*` 8종·WAS 판정·폴러·MCP 서버) · `sre_agent` 소비측(두 번째 MCP 서버·APM 지침·판정 승격·WAS 권고) · `noise_gate` 소비측(`apm` kind 선판정·배지·트리거 힌트·`app_impact` 승격). `mcp_server`·`src`(질의 경로)에는 제니퍼 코드 0건 |
| 제니퍼 소스 | **여러 개(v5 · J8 · D-287)** — 게이트웨이 1개가 뷰 서버 N개(예 `bank`·`common`·`legacy`)를 묶는다. 소스 ↔ 존 정본은 루트 레지스트리 `solutions[apm].sources[]`(레거시 = 은행존). 단일 설정은 소스 `default`(존 없음 · v4와 같음) — §4.2·§4.4 |
| 연동 구조(확정) | **독립 최상위 패키지 `apm_gateway/`** — 자체 MCP 서버 · 독립 프로세스. 제니퍼 Open API(REST · Bearer 토큰)를 게이트웨이가 직접 호출한다(G-1 · G-8 · D-274) |
| 제니퍼 설정 키 | 제니퍼 URL·토큰은 **`apm_gateway/.env`에만**(예시 `apm_gateway/.env.example` · §4.2). 소비자는 게이트웨이 MCP 주소·Bearer만 갖는다(`sre_agent/.env` `APM_MCP_*` · 루트 `.env` `NOISE_APM_MCP_*` — §4.6·§4.7). 전부 기본 off·빈 값 = 현행과 비트 동일 |
| 재사용할 기존 기계 | **전례로 있다** — `mcp_server`의 HTTP 도구·반환 계약·Bearer 미들웨어(게이트웨이가 **복제**), `sre_agent`의 MCP 서버 등록, `noise_gate`의 `alarm:raw` 형식·MCP 클라이언트, OpenMetrics 노출 기계(`om_exposition.py` — 복제 또는 추출은 G-12) |
| 지금 WAS를 볼 수 있는 길 | 게이트웨이를 띄우면 `apm_*` 8종으로 앱 계층(골든 시그널·힙/GC·풀·느린 트랜잭션·액티브 서비스·이벤트·프로파일)을 본다(§6). 게이트웨이가 없으면 종전대로 호스트(OS) 관점뿐 |
| 운영 조사(원격) 셸 | **없다**(D-233 — 2026-09-21부터 bash off). 조사 LLM이 대상 호스트를 보는 길은 MCP 도구뿐이다 |
| 채팅 질의 편입 | **보류** — 87 J5는 `plans/121` TP-9.1·9.2·10.5(처리기 계약)가 선행인데 코드 0건이다(2026-09-29 실측 — `metric_query`·`config/task_routines.yaml` 없음) |
| 표준 노출(OpenMetrics) | 87 J7(선택) — 게이트웨이가 낸다. 공유 기계의 OpenMetrics **2.0** 협상은 **2026-09-29 수정 완료**(1.0 상한 · G-10) — 장기 실행 중인 `mcp_server`는 재기동해야 반영된다(§9.3) |
| 게이트·결정 | 87 게이트 G-1~G-12 **전건 확정**(2026-09-29) · **D-195 본문 등재**(①②③) · D-274 · L2(대응·복구)는 **D-003 범위 밖**(WAS 조치 · DB 쓰기 없음 — **예외가 아니다** · 실행 평면은 D-195 ③이 별도 통제 · J3·목업 검증 뒤 착수) |
| 로컬 검증 환경 | **로컬 Docker 제니퍼(J0-L) — J0-L-a 환경이 있다**(§3.8 · 2026-09-29 기동·실측 · arm64 네이티브). 라이선스가 없어 에이전트 데이터는 아직 없다 → **IP 기반 평가판 라이선스(2주)** + 최신 Java 에이전트를 받으면 J0-L-b(2주 채집) · 못 구하면 목 Open API 서버 · **목 Open API 서버·녹화 하네스도 있다**(§3.8 — 녹화본 21건 · 실서버와 40건 불일치 0) · **v4.1: 게이트웨이·소비측을 로컬 Docker 제니퍼에 붙여 58항목 검증 통과**(§10.2 · 87 §0.12 — 라이선스 없는 범위) |

**한 줄로**: 제니퍼 측 준비(§3) → 게이트웨이 설정·기동(§4.2·§4.3) → 소비자 연결(§4.6·§4.7) 순서로 붙인다. 운영 투입 전에는 **J0-O 운영 실측**(§3.7 —
인스턴스↔hostname 일치율 · 토큰 정책 · PII 샘플)과 **J0-L-b 실데이터 녹화**(§3.8)로 계약 픽스처를 바꿔야 한다 — 지금 테스트는 목 서버와 스펙 합성 픽스처 기준이다.

---

## 1. 연동 아키텍처

### 1.1 한 장 그림 【현재 가능 — J1~J4 · J7 `/metrics/apm`·`was_object` 정합 호출은 미구현】

```
 [제니퍼 View Server]  Open API  /api/*(v1 · 조회) · /api-v2/*(관리 · 쓰기 포함)   ※ 정본 스펙 5.6.4 [J-4]
        │  HTTPS/HTTP · Authorization: Bearer <AIOps 전용 토큰>
        │  GET + 경로 허용목록만 통과(§5) · 리다이렉트 비추종 · 타임아웃·호출 상한
        ▼
 ┌──────── apm_gateway (독립 최상위 패키지 · 자체 MCP 서버 · 독립 프로세스 · D-274) ────────┐
 │ adapters/jennifer/  Open API 클라이언트 · 허용목록(코드 상수) · 토큰은 이 프로세스에만        │
 │ application/        인스턴스↔hostname 정합 · 상위 N 축약 · 마스킹 · 이벤트 폴러              │
 │ domain/             WAS 시그니처 결정적 판정 · 이벤트 모델 · 레벨 매핑                        │
 │ interface/          MCP apm_* ≤ 8 · Bearer · 감사 · (J7) GET /metrics/apm                    │
 └──────┬─────────────────┬──────────────────────┬──────────────────────┬──────────────────┘
        │ MCP(정합 조회)   │ MCP(apm_*)            │ Redis XADD alarm:raw │ HTTP 스크레이프
        ▼                 ▼                       ▼                      ▼
   mcp_server        sre_agent(조사)          noise_gate              Prometheus 등
  (폴스타·PromQL)     src 2단(채팅 질의)       (게이트 → 조사 트리거)    (J7 · 선택)
```

- **제니퍼 자격증명은 `apm_gateway` 한 프로세스에만 있다.** `mcp_server`(폴스타 DB 자격증명)·`sre_agent`·본체는 제니퍼 URL·토큰을 모른다.
- **게이트웨이는 폴스타 DB 자격증명을 갖지 않는다.** 폴스타 `was_object` 정합이 필요해지면 `mcp_server`를 MCP로 부른다(한 방향 의존 · §4.5 — **v4 미구현**: U-10 미확인이라 정합은 `hostName` 직접 대조·정합 파일로만 한다).
- **이벤트 폴링은 게이트웨이가 한다.** 게이트웨이가 `alarm:raw`의 두 번째 생산자가 된다(첫째는 `alarm_server`). 폴링 경로 문제(G-4)는 해소됐다.
- **양방향 import 0** — `apm_gateway` ↔ `src`·`noise_gate`·`sre_agent`·`mcp_server`. 통신은 MCP · Redis Stream · HTTP 스크레이프 계약만 쓴다.

### 1.2 왜 이 구조인가

| 선택 | 이유 | 근거 |
|---|---|---|
| DB가 아니라 **Open API** | JENNIFER 5는 성능 데이터를 자체 파일 DB에 둔다. 진단 핵심 증거(액티브 서비스·X-View 프로파일·이벤트·실시간 지표)는 Open API로만 나온다 | 87 §0.2·§0.4 · [J-1][J-4][J-17] |
| RDB Export 적재본 **미채택** | 사용자 가공용 적재본이고 액티브·프로파일·이벤트가 없다. 뷰 서버 설정·적재 DB 운영 부담이 새로 생긴다 | 87 §0.4 · G-1 |
| 공식 MCP 서버 **미채택** | LLM 프록시 제품 전체를 운영해야 하고, 같은 토큰이라 권한 이점이 없으며, 공개 스펙 대신 비공개 도구 스키마에 묶인다 | 87 §0.5 · G-8 |
| `mcp_server` 확장이 아니라 **독립 패키지** | ①쓰기·제어 API까지 여는 토큰을 폴스타 DB 자격증명과 다른 프로세스에 둔다 ②제니퍼 API 지연·폴링·캐시가 폴스타 조회에 영향을 주지 않는다 ③제니퍼가 없는 환경에는 배포하지 않는다 ④WAS 판정을 한 곳에서 정의한다 | **D-274**(D-119 ① 개정) · 87 §0.7 |
| 도구 이름 `apm_*`(벤더 중립) | 벤더가 바뀌어도 도구 표면·계약·테스트는 그대로다. 패키지 이름 `apm_gateway`·레지스트리 솔루션 `apm`과 맞춘다 | 87 §5.2 · G-3(확정 — §12.1) |
| 표준 규격 = OpenMetrics(선택 출구) | 제니퍼는 OpenMetrics를 내지 않는다. 받아 온 **수치 지표만** 게이트웨이가 OpenMetrics 1.0으로 다시 노출한다. 이벤트·프로파일은 규격 밖이다 | 87 §0.4 (2) · G-2 · [OM-1] |

### 1.3 무엇이 어디에 있는가 【현재 가능 — v4】

| 구성 요소 | 전례(복제·참조 원본) | 구현 위치(v4) |
|---|---|---|
| 설정 형식 | `mcp_server/mcp_server/config.py` — dataclass + `.env` 로더(기존 키 우선) | `apm_gateway/apm_gateway/config.py`(`.env` + 정책 yaml — `config.toml`은 두지 않았다: 비밀 아닌 값이 적어 `.env` 한 곳으로 충분) |
| 허용목록(정본) | OpenMetrics 타깃 허용목록 `openmetrics_tools.py` | `apm_gateway/apm_gateway/adapters/jennifer/allowlist.py` — 16템플릿 · 경로별 쿼리 키 |
| 인증 헤더·강제 timeout·리다이렉트 비추종·크기 상한 | `promql_tools.py`(`make_client`) · `openmetrics_tools.py`(`follow_redirects=False`) | `apm_gateway/apm_gateway/adapters/jennifer/client.py` |
| 벤더 필드 → 중립 레코드 · 식별자 매핑 · 이벤트 유형 → 시그니처 | — | `adapters/jennifer/fields.py` · 조회 함수 `adapters/jennifer/api.py` |
| 정합(인스턴스 ↔ hostname) | — | `application/resolver.py` + 정책 `apm_gateway/config/instance_map.yaml` |
| WAS 판정(`was_signals`) | `sre_agent` `severity_signatures`(OS 시그니처) | `domain/signals.py`(단일 정의) + 임계 `apm_gateway/config/was_signatures.yaml` |
| 반환 계약 `{rows,row_count,queried_at,source_kind,...}`/`{error}` | `polestar_tools.py` `_ok`/`_err` | `application/tools.py`(복제 · `source_kind="apm_api"` · 오류는 `{"error": code, "reason"}`) |
| MCP 서버 전송 인증(정적 Bearer) | `mcp_server` 서버 모듈 `StaticBearerAuthMiddleware` | `interface/server.py`(복제 · 원본과 판정 문장 대조 테스트) |
| HTTP 도구 감사 | PromQL `_audit`(logger 1줄) | `interface/audit.py`(같은 형식 + `investigation_id`·`thread_id` · 호출 수) |
| `alarm:raw` 생산 | `noise_gate/alarm_server/base_receiver.py`(XADD `{"data": json}`) | `application/poller.py` + 정규화 `domain/events.py` + 레벨 `apm_gateway/config/event_levels.yaml` |
| 조사 쪽 MCP 서버 등록 | `sre_agent/sre_agent/interface/mcp_service.py` `_build_mcp_servers` | `"apm"` 항목(§4.6) |
| 게이트 → 게이트웨이 MCP 클라이언트 | `noise_gate/infrastructure/sre_agent_client.py` | `noise_gate/infrastructure/apm_gateway_client.py`(§4.7) |
| 본체 → MCP 도구 호출 | `src/dbhub/client.py`(`inspect_host`) | **없음** — J5 보류(`plans/121` 처리기 선행) |
| OpenMetrics 노출 기계 | `mcp_server/mcp_server/om_exposition.py` | **없음** — J7 보류(소비자 미확정 · G-9) |

---

## 2. 용어

| 제니퍼 용어 | 뜻 | 우리 쪽 대응 |
|---|---|---|
| 도메인(domain) | 에이전트 묶음(서비스·업무 단위). Open API 조회의 필수 인자 `domain_id` [J-4] | 존·실행 그룹과 비슷한 관리 축 |
| 인스턴스(instance) | 모니터링 대상 WAS 프로세스 1개(`instanceId`·`instanceName`) | 폴스타 서버(호스트) — **1 호스트 : N 인스턴스** |
| 액티브 서비스 | 지금 실행 중인 트랜잭션(스레드) 목록·상태·경과 시간·스택 [J-4] | 없음 |
| X-View | 트랜잭션 응답시간 산점도와 개별 트랜잭션 프로파일 [J-9] | 없음 |
| 이벤트(EVENT) | 임계·예외 기반 알람. 레벨 normal/warning/fatal [J-6] | 폴스타 알람 심각도 1/2/3 |
| PLC | 동시 액티브 서비스 상한 초과 요청을 거절하는 부하 제어 [J-3][J-10] | 없음(조치 후보 — 87 §5.7) |
| **server_name / hostname** | — | 폴스타 등록 서버명(`cmm_resource.name`) / OS 호스트명. **공동존은 둘이 다르다**(D-046). 도구 인자 `hostname`은 값이 `server_name`인 경우가 있다(D-119 ③ 이름 과적) |
| **게이트웨이** | — | `apm_gateway` 프로세스(기본 `127.0.0.1:9096` · SSE `/sse`) — 제니퍼 Open API를 부르는 유일한 프로세스 |

---

## 3. 제니퍼 측 준비

제니퍼 운영 조직과 협의해 준비한다. 이 절의 항목은 **우리 코드 없이** 진행할 수 있다.

### 3.1 버전 확인 【현재 가능】

- 87이 전제하는 버전은 **5.6.4 이상**이다(Open API 정본 스펙 `info.version` 5.6.4 · **39경로 · 63오퍼레이션** [J-4][J-23]). 최신 릴리즈는 **5.7.0(2026-08-13)** [J-18]이고,
  최신 핫픽스는 **5.7.0.1(2026-09-04)**, 5.6.5 계열은 5.6.5.12(2026-09-28)다[J-23]. 공개 정본 스펙은 여전히 5.6.4에서 멈춰 있다(5.6.5·5.7 변경 반영 여부 미상).
- 5.7.0.1부터 뷰 서버 **SNI 호스트 검증이 기본 on**이다(§3.5). 5.7.0.1은 비동기 트랜잭션을 애플리케이션 통계에 포함한다 — 전후 수치를 바로 비교하지 않는다.
- 5.7.0은 javax → jakarta 전환이 있다. **커스텀 이벤트 어댑터(§8.3 2-B)를 쓸 때만** 영향이 있다 [J-18].
- 운영 버전을 기록해 둔다. 87 J0의 U-12 항목이다.

### 3.2 Open API 토큰 발급 【현재 가능 — 제니퍼 콘솔 작업】

1. 제니퍼 콘솔 **[설정 > JENNIFER 서버 > 인증토큰 발급]**에서 **AIOps 전용 토큰**을 발급한다 [J-17]. (5.6.2 이전 메뉴는 [관리 > 인증 토큰 관리]였다 — 87 U-5.)
2. **토큰별 사용량 제한**을 운영 조직과 정한다. 제한을 0으로 두면 무제한 토큰이 된다 [J-20]. 제한의 **단위와 초과 시 응답은 공개 자료에 없다**(미확인 — U-5). 조사·폴링·질의·J7이 이 토큰 하나를 같이 쓰므로(§5.6) 무제한보다 **측정 후 상한**을 권한다.
3. 토큰은 **게이트웨이 호스트의 `apm_gateway/.env`에만** 둔다(§4.2). `mcp_server`·채팅 서버·조사 서비스·알람 서버에는 두지 않는다.

**하지 말 것**
- 폴스타 DB의 `was_connection.jennifer_token`을 읽어 재사용하지 않는다. 제니퍼 측 발급 주체·사용량 제한·감사를 우회하고, 폴스타 쪽 토큰이 바뀌면 조사 경로가 조용히 끊긴다(87 §8.2).
- 제니퍼 AI의 **벤더 공개 프록시(`insight.jennifersoft.com`) 모드**는 어떤 경우에도 쓰지 않는다. 외부 LLM으로 데이터가 나가고 최대 30일 보관된다 [J-22] · D-120.
- 쿼리 파라미터 인증(`?token=…`)을 쓰지 않는다. v2 매뉴얼에 브라우저용으로 적혀 있지만 URL·프록시·접근 로그에 토큰이 남는다. 게이트웨이는 `Authorization: Bearer` 헤더만 쓰고,
  쿼리에 `token` 키가 있는 요청은 거부한다(§5.3)[J-23].

### 3.3 권한 — 조회 전용 토큰이 없다는 전제

- 제니퍼 토큰에 **조회 전용 등급이 있는지는 미확인**이다(U-5).
- 같은 Open API 스펙에 **쓰기·제어 API가 조회 API와 같은 인증 체계로 공존**한다. 알려진 예:

| 메서드 · 경로 | 동작 | 출처 |
|---|---|---|
| `POST /api-v2/manage/data-server/control` | 데이터 서버 제어 | [J-4] |
| `PUT /api-v2/manage/domain/put` | 도메인 변경 | [J-4] |
| `PUT /api-v2/manage/instance/{domainId}/{instanceId}/domain-id` | 인스턴스 도메인 변경 | [J-4] |
| `/api-v2/manage/instance/<domain-id>/gc`(메서드 미공개) | 도메인 단위 인스턴스 강제 GC | 5.6.4.28 [J-19] |
| `POST /api-v2/manage/data-server/db/property/1000/copy` | 데이터 서버 설정 DB 복사 | [J-23] |
| `POST /api-v2/manage/domain-group` | 도메인 그룹 지정 | [J-23] |
| `POST /restapi/user/` · `PUT /restapi/user/{id}` · `DELETE /restapi/user/{id}` | 제니퍼 사용자 생성·수정·삭제 | [J-23] |
| `PUT /api-v2/manage/rule/event/error/<d>/<ERROR유형>/applied` | ERROR EVENT 룰 적용 on/off | [J-23] |
| `PUT` · `DELETE /api-v2/manage/rule/event/error/<d>/<ERROR유형>/individual-setting/<인스턴스>` | 대상별 EVENT 설정 저장·제거 | [J-23] |
| `POST /api-v2/manual-rdb-export?date=YYYY-MM-DD` | 과거 날짜 RDB Export 작업 추가 | [J-23] |
| `PUT` · `DELETE /api-v2/configuration/rdb-export-password-override` | RDB Export 대상 DB 암호 설정·삭제 | [J-23] |
| `/api-v2/k8s/…` · `/api-v2/encrypt-string/<문자열>`(메서드 미공개) | 쿠버네티스 API 중계 · 문자열 암호화 | [J-23] |

- **[v3.2 정정]** 종전 표의 `/api-v2/manage-rule-event*`는 API 경로가 아니라 v2 매뉴얼의 **파일 이름**이었다. 룰 조회 경로는
  `GET /api-v2/manage/rule/event/{error|metric|compare}/<domainId>[/<대상타입>]`이고, 공개된 룰 쓰기 API는 위 표의 **적용 on/off**와 **대상별 설정**뿐이다.
  **임계 변경 API는 공개 자료에 없다**[J-23].
- **상태는 안 바꾸지만 민감한 GET**도 있다. GET만 허용해서는 막히지 않는다.

| 경로(GET) | 노출 내용 | 출처 |
|---|---|---|
| `/api/auth/userlist` · `/api/auth/userlist.xml` | 제니퍼 사용자 목록 — 5.6.2.7부터 **이메일·휴대폰 번호** | [J-23] |
| `/restapi/users` · `/restapi/user/{id}` | 사용자 정보(스키마에 `password`·`allowIp`·`group` 선언) | [J-23] |
| `/api-v2/environment-variable/<d>` | 인스턴스별 시스템 환경변수·JVM 속성(비밀 포함 가능) | [J-23] |
| `/api-v2/active-service/detail/<d>/<txid>` | 실행 중 SQL·HTTP query·userId | [J-23] |
| `/api-v2/loaded-class/<d>/<i>` · `/api-v2/manage/db/path/<d>` · `/api-v2/manage/data-server/{domains,resource,system-property-config}` | 내부 구성·경로·JVM 옵션 | [J-23] |
| `/api-v2/manage/rule/event/…` | EVENT 룰 설정(`autoScriptCommand` 경로 포함) — J0 수동 채집에만 | [J-23] |


- 그래서 **1차 통제는 게이트웨이의 허용목록 — 메서드 + 경로 템플릿 정확 일치 · 그 밖은 전부 거부**다(§5.3). 위 두 표는 예시이고, allow 방식이라 목록 밖은 전부 막힌다.
  토큰 권한 축소는 운영 조직과 협의가 되면 2차 방어로 더한다.
- 이 토큰을 폴스타 DB 자격증명과 **다른 프로세스**에 두는 것이 독립 패키지를 택한 첫째 이유다(D-274).
- 테스트 환경에서만, 발급한 토큰으로 쓰기 API가 실제로 거부되는지 확인해 둔다(U-5). **운영 뷰 서버에서는 쓰기 API를 호출해 보지 않는다.**

### 3.4 Open API 활성 여부 【현재 가능 — 제니퍼 운영 조직 확인】

뷰 서버에는 Open API를 끄는 **비공식 옵션**이 있다(5.6.2.4+) [J-20]. **[v3.2]** 옵션 이름은 공개돼 있다 — 뷰 서버 **실행 스크립트의 JVM 속성
`jennifer.unofficial.disable.open.api=true`**[J-23]. 운영 조직과 함께 아래를 확인한다.

1. 뷰 서버 기동 스크립트에 `jennifer.unofficial.disable.open.api=true`가 **없는지**. 켜져 있으면 Open API가 기본 페이지 정보를 돌려주지 않는다(응답 코드는 미공개 — U-5).
2. 시스템 환경 변수 `ignore_auth_token` 옵션이 **켜져 있지 않은지**. 특정 API를 토큰 없이 여는 옵션이다(5.6.0.19 버그 수정 기록 · 적용 범위·설정 위치 미공개 — U-5)[J-23].
3. AIOps 토큰의 **도메인 단위 권한** — 5.6.1부터 권한이 없는 도메인에 대한 API 호출은 거부된다. 그 권한이 토큰 발급자·그룹 중 무엇에 묶이는지는 미공개다(U-5).
4. (참고) `api-v2` 크로스 도메인 처리·`enable_preflight_for_cors` 설정은 브라우저용이라 게이트웨이와 무관하다.

**v3.3** — 옵션을 넣지 않은 로컬 5.7.0.1 설치본에서 Open API는 **기본 활성**이었다(토큰 없음 401 · Bearer 200 — 로컬 실측 · 87 §0.10 #9).

### 3.5 네트워크 (폐쇄망)

| 방향 | 출발 → 도착 | 포트 | 필요한 단계 |
|---|---|---|---|
| 조회 | **게이트웨이 호스트** → 제니퍼 뷰 서버 Open API | 뷰 서버 HTTP(S) 포트 — 설치 기본 `server_port` **7900**(운영 값 확인)[J-23] | J1부터 필수 |
| 정합 조회 | 게이트웨이 → `mcp_server` | `mcp_server` 포트(본체용 9099 · 조사용 9097 등) | **v4는 불필요** — 폴스타 `was_object` 브릿지 미구현(§4.5 · J0-O에서 U-10 확인 뒤 재판정) |
| 이벤트 발행 | 게이트웨이 → Redis | Redis 포트(6379 등) | J4(폴러) |
| MCP 소비 | `sre_agent`·게이트 → 게이트웨이 MCP 서버(SSE `/sse`) | 게이트웨이 포트(기본 9096 · `APM_GATEWAY_PORT`) | J3·J4(본체 채팅 질의 J5는 보류) |
| 이벤트 push(선택) | 제니퍼 뷰 서버 → 게이트웨이 수신 | UDP 162(SNMP trap 2-A) 또는 TCP(커스텀 어댑터 2-B) | J4 2단계를 착수할 때만(§8.3 · G-4b) |
| 표준 노출(선택) | Prometheus 등 수집기 → 게이트웨이 `GET /metrics/apm` | 게이트웨이 포트 | J7을 착수할 때만 |

- 외부 인터넷(egress)은 필요 없다. 제니퍼는 사내 설치형이다.
- 제니퍼 뷰 서버로 나가는 조회 연결은 **게이트웨이 한 프로세스만** 연다.
- **SNI 호스트 검증(5.7.0.1+ 기본 on · `ssl_sni_host_check=true`)** — HTTPS 접속 주소는 IP가 아니라 **뷰 서버 인증서의 호스트명(CN/SAN)**이어야 한다.
  `JENNIFER_API_URL`에 IP를 쓰지 않는다. 게이트웨이 호스트가 그 이름을 해석할 수 있어야 한다(내부 DNS 또는 hosts)[J-23].

### 3.6 연결 사전 확인 (curl) 【현재 가능 — 우리 코드 없이】

우리 코드가 없어도 토큰과 도달성은 지금 확인할 수 있다. **게이트웨이를 둘 호스트**에서 실행한다. 아래 값은 모두 **플레이스홀더**다.
실제 호스트명·토큰을 문서나 채팅에 남기지 않는다.

```bash
# [게이트웨이 예정 호스트 · 셸 이력에 토큰이 남지 않게 read -s로 받는다]
read -s JENNIFER_TOKEN
export JENNIFER_URL="https://jennifer.example.internal:7900"   # 플레이스홀더 · 뷰 서버 기본 포트 7900 · IP 금지(SNI)

# ① 도달성·인증 — 도메인 목록(가장 가벼운 조회 · 파라미터 없음 · 스펙 5.6.4 확인).
#    벤더의 인증 확인용 `GET /api-v2/auth-test`는 정본 스펙에 없어(v2 매뉴얼에만) 쓰지 않는다.
curl -sS -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer ${JENNIFER_TOKEN}" \
  "${JENNIFER_URL}/api/domain"

# ② 인스턴스 목록 — `domain_id` 필수 · 스펙 5.6.4 확인. 응답의 `hostName`·`ipAddress`가
#    87 J0 U-4(인스턴스 ↔ 폴스타 hostname 일치율)의 1차 입력이다(87 §5.3 [v3.2])
curl -sS -H "Authorization: Bearer ${JENNIFER_TOKEN}" \
  "${JENNIFER_URL}/api/instance?domain_id=<도메인ID>" | head -c 2000
```

판정:

| 결과 | 뜻 | 다음 행동 |
|---|---|---|
| 200 + JSON | 토큰·도달성 정상 | §3.7 J0 채집으로 |
| 401/403 | 토큰 오류·권한 부족 | 토큰 재발급, 사용량 제한 확인 |
| 404 | 경로 불일치 · **요청한 데이터 없음**(v2) · 또는 Open API 비활성 | 경로를 스펙 5.6.4와 대조, §3.4 옵션 확인(비활성 시 응답 코드는 미공개 — U-5) |
| 405 | **메서드 오류**(v2 공통 코드) | GET 외 메서드를 보냈는지 확인 — 사전 확인은 GET만 |
| 500 + `{"exception":{"message":"… Domain is not connected"}}` | 그 도메인에 붙은 에이전트가 없다(로컬 실측 — 인스턴스 데이터 API가 모두 이렇게 응답) | 라이선스·에이전트 접속 상태 확인. `/api/domain`이 200이어도 이 상태일 수 있다 |
| 500 + `Required request parameter …` | 필수 파라미터 누락 — 제니퍼는 400이 아니라 **500**을 준다(로컬 실측) | `domain_id` 등 필수 파라미터를 넣었는지 확인(§5.3) |
| TLS·SNI 오류(인증서 호스트명 불일치) | 5.7.0.1+ SNI 호스트 검증 | IP가 아니라 인증서의 호스트명으로 접속(§3.5) |
| 연결 거부·타임아웃 | 방화벽·포트 | §3.5 표의 조회 방향 개방 요청 |
| 3xx | 로그인 페이지 등으로 리다이렉트 | URL이 콘솔 주소인지 확인. 게이트웨이는 리다이렉트를 따라가지 않는다(§5.2) |

- **GET만 쓴다.** 사전 확인 단계에서도 `POST`·`PUT`·`DELETE`를 보내지 않는다.
- 응답을 파일로 남길 때는 §3.7의 마스킹 절차를 먼저 따른다.

### 3.7 87 J0 실측 체크리스트 【현재 가능 — 착수 전 선행】

87 구현의 첫 Wave(J0)는 코드가 아니라 **실측**이다. 아래 결과가 J1~J3의 설계 값(허용 경로·정합 규칙·임계·마스킹 규칙)을 정한다.

> **[v3.1]** J0은 **J0-L(로컬 Docker — 운영 접근 없이)**과 **J0-O(운영 실측 — 접근 권한·테스트 토큰 필요)**로 나뉜다. 마지막 열이 어디서 푸는지다
> (87 §0.8 (5)). 쓰기·제어 API 실측(U-5·U-8)은 **로컬에서만** 한다.

| # | 무엇을 | 어떻게 | 무엇이 정해지나 | 어디서(v3.1) |
|---|---|---|---|---|
| U-1 | 운영 EVENT 룰 목록·이벤트 유형 정식 명칭 · `level`·`eventLevel` 값 형식 | `GET /api-v2/manage/rule/event/error/<domainId>`(1차 · **GET만** · v2 매뉴얼 — 정본 5.6.4 미수록) · `…/metric/<d>/<대상>` · `…/compare/<d>/<대상>`(예제는 `comparing` — 「확인 불가 — J0-L」) · 콘솔 [관리 > EVENT 룰] | 심각도·시그니처 매핑 | L(기본 룰) · O(커스텀 룰) |
| U-2 | `/api/dbmetrics/{domain,instance,business}` 응답 필드 전수 · `interval_minute` 허용값 · 1회 조회 창 상한 | 테스트 토큰으로 호출 → recorded JSON | 구간 조회 조립 · 임계 표본 | **L 해소**(**v3.3** — J0-L-a는 부분: 지표 식별자 카탈로그(snake_case) 확인 · 응답 형태·`interval_minute`는 J0-L-b) · **[v3.2]** 응답은 스펙상 지표 1개 시계열(`time`·`value`) · `metrics` 필수 — `interval_minute` 허용값·창 상한은 「확인 불가 — J0-L」 |
| U-3 | 데이터 보존 기간 | `/api/dbmetrics/*` 최소 조회 가능 일자 | 조회 창 상한 · "API로 답할 수 없는 기간" | L(기본값) · O(운영값) |
| U-4 | 인스턴스명 ↔ 폴스타 hostname 일치율 | `/api/instance` 전수 ↔ 폴스타 서버 목록 대조 | 정합 파일 필요 여부(**최대 리스크**) | **O 전용** |
| U-5 | 토큰 권한 등급 · 사용량 제한 단위 · 초과 시 응답 | 콘솔 · 테스트 환경 전용 호출 | 허용목록 · 호출 상한 | **L 해소**(쓰기 API 실측 — **v3.3** J0-L-a는 부분: 사용량 단위 = 요청 수(500 포함) · 쿼리 토큰 통과 · 쓰기 경로 라우팅(GET) 확인 · 초과 응답·실제 거부는 J0-L-b) · O(토큰 정책) · **[v3.2]** + `jennifer.unofficial.disable.open.api`·`ignore_auth_token` 미설정 확인 · 도메인 단위 권한 · 비활성 시 응답 코드(§3.4) |
| U-6 | 응답의 개인정보 포함 여부(URL 파라미터·SQL 바인드·`clientIp`) | 샘플 → `python scripts/pii_probe.py` | 마스킹 규칙(`docs/pii_filtering_rules.md`) | L(필드 구조) · O(실 샘플) |
| U-7 | 뷰 서버 Open API 부하 상한 | 운영 조직 확인 | 타임아웃·호출 상한 | O(운영 조직) |
| U-8 | 스레드 덤프·PLC 조작의 API 노출 여부 | 정본 스펙 전수 · 벤더 문의 | 조치 카탈로그(87 §5.7) | 「확인 불가 — J0-L」(공개 API 0건 · 벤더 문의) · O(버전 차이) |
| U-9 | 뷰 서버 어댑터 배포 정책(`extension_allowed_packages`) · SNMP 메시지 패턴 | 콘솔 어댑터 화면 | 이벤트 push 방식(§8.3) | L(SNMP 재현) · O(배포 정책) |
| U-10 | 폴스타 `was_object`·`was_connection`의 운영 DB 실재·채움률 | 폴스타 DB 조회 | 정합 1순위 브릿지 | **O 전용**(폴스타) |
| U-12 | 운영 버전 · 87 도구가 쓰는 v1 엔드포인트 전건 실 응답 | 호출 → recorded JSON | 계약 테스트 픽스처 | L(1차 채집) · O(운영 버전) · **[v3.2]** + MB 정의 · 단위 미기재 필드 · `max_row` 미지정 기본값 · **v3.3** 오류 모델(HTTP 500 + 메시지)·`.xml`/POST 변형 확인 |
| U-13 | 운영 `errorType` 전 목록과 알람 kind 분류 결과 | U-1 결과를 두 분류기에 대조(§8.4) | 이벤트 정규화 · R-16 | L(기본 유형) · O(커스텀) · **[v3.2]** 지표 기반 이벤트는 `metricsName`으로 분류 입력 |
| U-14 | (v3.2) 시간대·시계 기준 · X-View 기록 전수 여부 | 로컬 설치본에서 epoch ms 왕복 · X-View 건수 대 TPS 대조 | 시각 처리 · p95·에러율 신뢰도 | 「확인 불가 — J0-L」 · O(운영 시계) |

**채집물 보관 규칙**
- 저장 위치는 `apm_gateway/testdata/`다(D-139 — 패키지가 자기 testdata를 소유 · 지금은 `testdata/jennifer/recorded/<source>/` — §3.8). **마스킹본만** 커밋한다.
- 마스킹 대상: 인스턴스명·도메인명의 운영 식별자, IP, URL 파라미터, SQL 바인드 값, 사용자 ID, 토큰. 원본은 저장소 밖(운영 서버)에 두고 옮기지 않는다.
- 실 운영 응답을 외부 LLM(Gemini 등)에 넣지 않는다(D-120 · D-127).
- 실측 결과 보고서는 87 J0 산출물이다. 문서 번호는 착수 시 다시 정한다(`docs/29`는 이미 다른 문서가 쓴다).

---

### 3.8 로컬 Docker 검증 환경 (J0-L) 【현재 가능 — J0-L-a 환경 · 87 §0.8·§0.10 · 2026-09-29 실측】

운영 제니퍼 접근 권한이 없어도 J0의 일부를 풀 수 있게, 제니퍼를 로컬 Docker로 띄워 검토하는 환경이다(사용자 지시 2026-09-29 *"제니퍼도 도커로 설치하여
검토할 수 있도록 계획에 포함시켜라."* · *"도커에 제니퍼를 설치하여 현재 계획이 정상적인지 직접 테스트를 진행하여 계획을 업데이트하라."*).
**파일은 `apm_gateway/testdata/jennifer/`에 있고 2026-09-29에 기동·실측했다**(87 §0.10). 절차의 세부 정본은 그 폴더의 `README.md`다.
**라이선스가 없어 에이전트 데이터는 아직 들어오지 않는다** — 지금 확인할 수 있는 것과 라이선스가 있어야 하는 것을 아래에서 나눴다.

**먼저 알아 둘 사실**
- **공식 Docker 이미지는 없다**([J-24]). 공식 GitHub 샘플은 JDK 8 기반이라 서버 5.6.5 이상(JDK 17/21 필수)에 쓸 수 없어 Dockerfile을 새로 썼다.
- **라이선스가 없으면 에이전트가 데이터 서버에 붙지 못한다.** 거부는 **에이전트 로그**에 남는다 — *"Cannot create data server session. Rejected by data server.
  reason=no license found, serverVersion=5.7.0.1"*(데이터 서버 INFO 로그에는 없다 · 실측). 평가판은 **2주 · 기능 제한 없음 · IP 기반(루프백 불가) · 회사 이메일과
  전화 확인 · 사이트당 1회**다([J-24]).
- **Bootstrap Check는 끄지 않는다.** 5.7.0.1 데이터 서버는 Docker를 *"virtual environment is not recommended (Docker Container)"* 경고로만 남기고 기동을 마친다(실측).
  자원이 모자라 기동이 막힐 때만 `.env`의 `DISABLE_BOOTSTRAP_CHECK=1`로 끈다(로컬 한정 · **운영 적용 금지**).
- **arm64(Apple Silicon)에서 서버가 네이티브로 뜬다**(`eclipse-temurin:21-jdk` aarch64 · 실측). amd64 에뮬레이션은 필요 없었다.
- **뷰 서버가 배포하는 Java 에이전트는 5.5.2.5**(2021 빌드)이고 **JDK 17에서 JVM 기동을 실패시킨다**(`Unknown Java version string: 17.0.20.1` →
  `FATAL ERROR in native method: processing of -javaagent failed`). 그래서 샘플 WAS는 **JDK 11**이다. 최신 5.6.x 에이전트는 라이선스 키로 공식 다운로드에서 받는다(87 R-28).
- **에이전트는 `JENNIFER_*` 환경변수를 설정으로 읽는다.** WAS 환경에 `JENNIFER_VIEW_URL` 등을 두었더니 `jennifer.conf`의 주소 대신 127.0.0.1:5000으로 접속했다(실측 ·
  어느 변수가 원인인지는 미확정). 그래서 compose는 WAS에 `AGENT_*` 이름만 넘긴다(87 R-29).
- 포트: 뷰 서버와 Open API **7900**(같은 포트) · 데이터 서버와 에이전트 **5000**(실측).

**구성**(`docker-compose.yml` · 프로젝트 이름 `jennifer-local`)

| 서비스 | 이미지 | 호스트 게시 | 내부 IP |
|---|---|---|---|
| `jennifer-server` | `eclipse-temurin:21-jdk` + 설치본(볼륨 `jennifer-home`에 1회 풀기) — 데이터 서버(백그라운드) → 뷰 서버(포그라운드) | `127.0.0.1:17900` → 7900(뷰·Open API) | `172.29.87.10`(5000은 게시하지 않는다) |
| `sample-was` | `tomcat:9.0-jdk11-temurin` + 제니퍼 Java 에이전트 — 기동 때 뷰 서버 `/download/agent/java/latest`에서 1회 받아 `jennifer.conf`의 `server_address`·`server_port`(5000)·`domain_id`·`inst_name`만 덮어쓴다 | `127.0.0.1:18080` → 8080 | `172.29.87.20` |

- 네트워크 `jennifer-net`(`172.29.87.0/24` 고정) · 서버 헬스체크(7900 응답)가 통과한 뒤 WAS가 뜬다.
- 샘플 앱: `/sample/slow.jsp?ms=`(느린 응답) · `/sample/heap.jsp?mb=`(힙 증가 · `reset=1`로 해제) · `/sample/error.jsp`(예외). SQL 호출 엔드포인트는 없다.

| 파일 | 내용 |
|---|---|
| `docker-compose.yml` · `.env.example` | 위 두 서비스 · compose 전용 키(아래) |
| `server/Dockerfile` · `server/entrypoint.sh` | 설치본 1회 풀기 · Bootstrap Check 옵트인 끄기 · 잔여 `db.lock` 정리 · `license` 파일이 있으면 복사 · 서버 기동 |
| `was/Dockerfile` · `was/entrypoint.sh` · `was/app/*.jsp` | 에이전트 다운로드·설정 · 샘플 JSP |
| `scripts/bootstrap_local.py` | 최고관리자 생성 + Open API 토큰 발급(**로컬 전용**) |
| `scripts/probe_openapi.py` | Open API 실측 프로브 — 허용목록 경로 · 필수 파라미터 · 민감 GET · 쓰기 경로 존재(쓰기는 실행하지 않고 GET으로만) · 인증 방식. 결과는 `recorded/raw/<시각>/`(커밋 제외 · 민감 경로는 키 이름만 저장) |
| `scripts/jennifer_catalog.py` | 허용목록 16템플릿 **사본**(필수·선택 파라미터 · `profile.txt` Accept `text/plain` · 도메인 필요 여부 · 미연결 시 빈 결과 경로) · EventData 13필드 · 허용목록 밖 경로의 실측 응답 · `match_template()`(정확 일치만 — `..`·`//`·`%`·`.xml` 거부). 정본은 게이트웨이 `apm_gateway/apm_gateway/adapters/jennifer/allowlist.py`이고, 둘이 같은지 `apm_gateway/tests/test_allowlist.py::test_catalog_copy_matches_canonical`이 대조한다(한쪽만 바뀌면 실패) |
| `scripts/masking.py` | 마스킹 — 비밀·개인정보 키(password·token·email·phoneNumber·allowIp·userId 등) · 값(이메일·휴대폰·주민번호) · SQL 리터럴 · URL 쿼리 값 · `profile.txt`는 SQL 줄 숫자만 · `ops-masked`는 호스트명·IP·인스턴스명을 결정적 가명(`host-<8자>`·`10.x.y.z`)으로 |
| `scripts/record_openapi.py` | **녹화 하네스** — 아래 절 |
| `scripts/mock_openapi.py` | **목 Open API 서버**(stdlib · 127.0.0.1 전용) — 아래 절 |
| `recorded/local-docker/` | 라이선스 없는 상태의 녹화본 21건 + `index.json`(마스킹본 — 커밋 대상 · 원본 `recorded/raw/`는 커밋 제외) |
| `README.md` | 절차 정본 |
| (패키지 쪽) `apm_gateway/tests/test_jennifer_fixture_tools.py` | 카탈로그·하네스·마스킹·목 서버·녹화→재생 왕복 테스트 22건 — 루트 pytest 수집 밖이라 명시 실행(§10.1) |

**compose 전용 `.env` 키**(`.env.example` → `.env` · `.env`는 gitignore)

| 키 | 기본 | 뜻 |
|---|---|---|
| `JENNIFER_DIST_DIR` | (필수 — 비면 compose가 멈춘다) | 설치본 `jennifer-server-<버전>.zip`과 (받으면) `license` 파일을 둔 **저장소 밖** 절대 경로 — 컨테이너 `/dist`에 읽기 전용 마운트 |
| `JENNIFER_VERSION` | `5.7.0.1` | 설치본 버전(파일 이름과 맞춘다) |
| `JENNIFER_PLATFORM` | `linux/arm64` | 실패 시 `linux/amd64` |
| `JENNIFER_VIEW_HOST_PORT` · `SAMPLE_WAS_HOST_PORT` | 17900 · 18080 | 127.0.0.1 게시 포트 |
| `JENNIFER_DOMAIN_ID` · `JENNIFER_INST_NAME` | 1000 · `sample-was-01` | compose가 WAS의 `AGENT_DOMAIN_ID`·`AGENT_INST_NAME`으로 옮긴다 |
| `TZ` | `Asia/Seoul` | 서버·WAS 공통 |
| `DISABLE_BOOTSTRAP_CHECK` | 0 | 자원이 모자라 기동이 막힐 때만 1(로컬 한정) |

- `.env.example`의 주석은 모두 별도 줄에 있다(값 뒤 인라인 주석 0건 — 2026-09-29 확인). 복사한 `.env`에 설명을 더할 때도 별도 줄에 둔다 — 이 저장소의 `.env` 규칙(§4.8 · 특히 빈 값 뒤 주석 금지)을 compose `.env`에도 똑같이 적용한다.

**준비(사용자)**
1. 설치본을 **저장소 밖** 디렉터리에 둔다 — 2026-09-29 사용자 확정으로 공개 S3의 `jennifer-server-5.7.0.1.zip`(590,809,047바이트 · sha256
   `e3a1dc9e984de04fe2e9ec9f48edd361b22f110dde99c0a2411c7824bb34828f`)을 받았다. 라이선스를 받으면 같은 디렉터리에 `license` 파일로 둔다.
2. `.env.example`을 `.env`로 복사하고 `JENNIFER_DIST_DIR`에 그 디렉터리의 절대 경로를 적는다.
3. Docker Desktop 자원 — CPU 2개 이상 · 메모리 8GB 이상(실측 호스트: CPU 10 · 11.7GiB).
4. 호스트 포트가 비었는지 확인한다 — `lsof -i :17900 -i :18080`.

**기동 · 초기 설정 · 실측** 【현재 가능】

```bash
# [CWD=apm_gateway/testdata/jennifer/]
docker compose up -d --build            # 사용자가 직접 실행 — 베이스 이미지를 받는다 · 서버가 healthy가 된 뒤 WAS가 뜬다
docker compose ps

# 최고관리자 생성 + Open API 토큰 발급 (로컬 전용 · 토큰은 저장소 밖 파일로)
JENNIFER_LOCAL_ADMIN_ID=jadmin JENNIFER_LOCAL_ADMIN_PW='<임의 값>' \
  python3 scripts/bootstrap_local.py > /path/outside/repo/jennifer-token.env

# Open API 실측 (결과: recorded/raw/<시각>/ — 커밋 제외)
set -a; . /path/outside/repo/jennifer-token.env; set +a
python3 scripts/probe_openapi.py
```

`bootstrap_local.py`가 하는 일(5.7.0.1 · 실측): 첫 기동 때 생기는 `server.view/db_view/security/admin-bootstrap.token`으로 `POST /login/user/setup`(폼 `id`·
`password`·`name`·`token` → 성공 응답 `M0478` · 토큰 파일은 사라진다)을 불러 관리자를 만들고, `POST /login/page`(302)로 로그인한 세션으로
`POST /auth/token/create`(폼 `tokenType`=**정수 0** = OPEN_API · `validTime`(ms) · `tokenCount` · `memo`)를 불러 토큰을 받는다. 관리자가 이미 있으면 생성은 건너뛴다.
**운영 토큰은 이 스크립트로 만들지 않는다** — 운영은 콘솔 [설정 > JENNIFER 서버 > 인증토큰 발급] 수작업이다(§3.2).

**라이선스 없이 확인되는 것**(2026-09-29 · `probe_openapi.py` 40건 · 87 §0.10)

| 호출 | 결과 | 뜻 |
|---|---|---|
| `GET /api-v2/auth-test` | 토큰 없음 401 · Bearer 200 `"OK"` | Open API는 기본 활성이다 |
| 허용 경로 + 쿼리 `?token=` | 200 | 서버는 쿼리 토큰을 받아 준다 — 게이트웨이가 거부해야 한다(§5.3) |
| `GET /api/domain` · `/api/realtime/instance` · `/api/transaction/time` | 200 + 빈 `result` | 빈 결과가 "정상 0건"을 뜻하지 않는다 |
| `GET /api/metrics` | 200 — 지표 카탈로그(snake_case 식별자 · `tps` 식별자 없음) | `/api/dbmetrics/*`의 `metrics` 인자 이름 |
| `/api/instance` 등 인스턴스 데이터 API | **HTTP 500** `{"exception":{"message":"1000 Domain is not connected"}}` | 도메인에 붙은 에이전트가 없다 |
| 필수 파라미터 누락 | **HTTP 500**(400 아님) `Required request parameter '…' … is not present` | 코드가 아니라 메시지로 가른다(§6.2) |
| `GET /api/transaction/profile.txt`를 `Accept: application/json`으로 | 404(HTML) | `Accept: text/plain`으로 부른다 |
| 민감 GET(`/api/auth/userlist` · `/restapi/users`) · 쓰기·관리 경로(GET으로) | 200 · 400·405·500 — 토큰으로 라우팅된다 | 토큰 단위 읽기 전용 제한이 없다 — 허용목록이 1차 통제다(§5.3) |
| 토큰 `usageCount` | 요청마다 1 증가 — 500 응답도 센다 · **v4.1**: 약 5초 늦게 반영 · 타임아웃 난 요청도 센다 · 401·연결 거부는 세지 않는다 | 실패 호출도 한도를 쓴다(§5.6) · 호출 직후 읽으면 안 늘어난 것처럼 보인다 |

**라이선스가 있어야 하는 것(J0-L-b)** — 실 인스턴스·이벤트·트랜잭션 응답 형태 · `interval_minute` 허용값 · X-View 전수 여부(U-14) · `hostName` 형식(U-4 형식) ·
이벤트 재현(U-1·U-13) · 룰 조회 경로 응답 · 덤프·PLC(U-8) · `/api/status/*`의 "시 단위만" 규칙(분 단위 호출도 미접속 오류로 먼저 막혀 아직 확인하지 못했다).
인스턴스↔hostname 일치율(U-4)·폴스타 `was_object`(U-10)·운영 PII 샘플은 여전히 운영(J0-O)에서만 푼다.

**녹화 하네스**(`scripts/record_openapi.py`) 【현재 가능】 — §5.3 허용목록의 **GET만** 호출한다. 허용목록 밖 경로 · 쿼리 `token` · 허용 밖 쿼리 키 · 경로 변수 누락은
**네트워크 호출 전에** 거부한다(`NotAllowedError`). 도메인 → 인스턴스 → realtime·activeService → 지표 카탈로그 → dbmetrics(snake_case 6지표) → transaction/time(1분 창을 거슬러
첫 비지 않은 창) → txid·profile.txt·sql → dbsearch event·error → status 3종(시 단위) → deploy 순으로 식별자를 찾아가며 녹화하고, 마스킹해 출처 표지와 함께
`recorded/<source>/`에 저장한다. 오류 응답도 fixture로 남긴다.

```bash
# [CWD=apm_gateway/testdata/jennifer/]
set -a; . /path/outside/repo/jennifer-token.env; set +a
JENNIFER_VERSION=5.7.0.1 python3 scripts/record_openapi.py --source local-docker
# 운영 뷰 서버(J0-O): 호스트명·IP를 같은 입력 → 같은 가명으로 바꾼다
python3 scripts/record_openapi.py --source ops-masked --base https://<뷰 서버>
```

- 파일 이름 `GET_<경로>__<variant>[__<label>].json` · variant = `ok` · `domain_not_connected` · `missing_param` · `status_<코드>` · `index.json`이 목록과 적용한 마스킹 규칙을 담는다.
- fixture 필드: `fixture_version` · `source` · `jennifer_version` · `recorded_at` · `request{method,template,path,query,accept}` · `response{status,content_type,body_json|body_text}` ·
  `variant` · `label` · `masking`.
- `--keep-raw`를 주면 마스킹 전 원본을 `recorded/raw/`(커밋 제외)에 남긴다.
- 지금 `recorded/local-docker/`는 라이선스 없는 녹화본이다(21건 — `ok`는 `domain`·`realtime/instance`·`metrics`·`transaction/time`, 나머지는 `domain_not_connected`).
  평가판을 적용하면(J0-L-b) 다시 녹화한다.

**목 Open API 서버**(`scripts/mock_openapi.py`) 【현재 가능】 — 녹화 fixture를 응답 원천으로 쓰고 실측한 실서버 동작을 흉내 낸다. **스펙에서 만든 합성 응답은 없다**
(추정한 모양을 계약으로 굳히지 않는다). 로컬 실서버와 목 서버에 같은 40건 프로브를 돌려 **상태 코드·응답 모양 불일치 0건**을 확인했다(2026-09-29). **v4.1 재확인**(게이트웨이 구현 뒤): 상태 코드 40/40 · JSON 응답 모양
38/38 일치 — 비JSON 2건(404 HTML 페이지 · `userlist.xml`)은 본문 내용만 다르다(목 서버의 간이 HTML과 개인정보 자리 가짜 값 — 의도).

```bash
# [CWD=apm_gateway/testdata/jennifer/]
python3 scripts/mock_openapi.py --port 17901 --token mock-token                        # fixtures — 녹화 상태 그대로
python3 scripts/mock_openapi.py --port 17901 --token mock-token --mode connected       # ok fixture만 · 이벤트 주입
python3 scripts/mock_openapi.py --port 17901 --token mock-token --mode disconnected    # 라이선스 없음 흉내
```

| 흉내 내는 실서버 동작 | 목 서버 |
|---|---|
| 인증 | Bearer · 쿼리 `?token=`도 받는다(게이트웨이가 거부해야 한다 — 접근 기록에 `query_token` 표지) · 없으면 401 |
| 필수 파라미터 누락 | 500 `{"exception":{"message":"Required request parameter ..."}}` |
| `profile.txt` + `Accept: application/json` | 404 |
| 미연결 | 500 *"Domain is not connected"*(deploy는 문자열 본문 · `.xml` 변형은 XML 예외) |
| 허용목록 밖(민감 GET·쓰기 경로·`.xml`·POST 변형) | 실측과 같은 상태·모양(개인정보 자리는 가짜 값) + **접근 기록** |

제어 경로(인증 없음 · 127.0.0.1 전용): `GET /__mock/hits`(접근 기록 — `allowlisted`·`query_token` 표지) · `POST /__mock/events`(EventData 13필드만 · `time` 필수 — 폴러 커서·멱등
검증용 이벤트 주입) · `GET /__mock/usage` · `POST /__mock/mode`(`{"mode": "fixtures|connected|disconnected"}`) · `POST /__mock/reset`.
**한계** — 실제 EVENT 발생과 필드 변형은 재현하지 못한다(이벤트는 주입으로 만든다). 실데이터 모양은 평가판 녹화(J0-L-b) 뒤에 채워진다.

**정지 · 정리** 【현재 가능】

```bash
docker compose down        # 컨테이너만 내린다(설치·관리자·토큰은 볼륨에 남는다)
docker compose down -v     # 볼륨까지 지운다(다음 기동 때 설치본을 다시 풀고 관리자를 다시 만든다)
```

**포트 — 호스트 게시와 라이선스 IP는 다른 개념이다**

| 개념 | 무엇 | 값 |
|---|---|---|
| 호스트 게시(127.0.0.1) | 개발 맥에서 브라우저·스크립트·(J1 이후) 게이트웨이가 붙는 주소 | 뷰 서버·Open API `127.0.0.1:17900 → 7900` · 샘플 앱 `127.0.0.1:18080 → 8080` · **5000은 게시하지 않는다**(compose 내부 네트워크 전용) |
| 라이선스 IP | 평가판 신청 폼의 **Agent IP · Server IP**(루프백 불가) | compose 고정 IP — 서버 `172.29.87.10` · WAS `172.29.87.20`. Docker Desktop(Mac)에서 맥 LAN IP를 적어야 하는지는 **「확인 불가」 — 벤더 확인**(사용자 할 일 1) |

- 호스트 포트 17900·18080은 2026-09-29 실측 점유 목록 밖이다: `5433`·`5434`(PostgreSQL) · `6379`·`6380`(Redis) · `50000`(DB2) · `3307`(itam) · `9190`·`9101`·`9102`(Prometheus 픽스처) ·
  `8000`(본체 API) · `9097`·`9099`(MCP) · `9098`(sre_agent) · `9100`(alarm_server) · `8080`(MLX) · `18981`·`18982`(매뉴얼 캡처). 기동 전에 `lsof`로 다시 확인한다.
- 기존 로컬 compose들은 모든 인터페이스에 바인딩한다. 이 환경은 **127.0.0.1로 고정**했다.

**통제**
- **WAS(에이전트를 붙인 JVM) 환경에 `JENNIFER_*` 이름을 쓰지 않는다** — 위 "먼저 알아 둘 사실" 참조. compose 스크립트 전용 변수는 `AGENT_*`다.
- 설치본·라이선스·녹화 원본은 커밋되지 않는다 — 루트 `.gitignore`의 `apm_gateway/testdata/jennifer/**/*.zip` · `…/**/license*` · `…/recorded/raw/`와 기존 `.env` 패턴
  (`git check-ignore`로 확인). 녹화 원본은 커밋하지 않고, fixture는 마스킹본(`recorded/local-docker/` 등)만 커밋 대상이다.
- 설치본·베이스 이미지 다운로드는 **사용자가 직접 실행**한다(스크립트가 인터넷에서 받지 않는다 — WAS 에이전트는 로컬 뷰 서버에서 받는다).
- 컨테이너가 비정상 종료되면 `db.lock`이 볼륨에 남는다 — 서버 entrypoint가 기동 때 지운다(그 볼륨을 이 컨테이너만 쓰는 로컬 한정 처리다).
- 로컬 인스턴스라 과금 경로가 아니다. `tests/conftest.py:85-86`이 사설·루프백 IP를 내부로 보므로 127.0.0.1 게시 포트로 붙는 테스트는 외부 네트워크 가드와 충돌하지 않는다.
- HTTP(`http://127.0.0.1:17900`)로 붙으므로 5.7.0.1의 SNI 검증과는 관계없다(HTTPS로 둘 때만 호스트명 필요).
- recorded JSON에는 출처 표지(`local-docker`·`mock`·`ops-masked`)를 단다. 로컬 채집분과 운영 마스킹분을 섞지 않는다.
- 로컬 Docker 수치로 부하·지연 결론을 내지 않는다(성능은 내부망).
- 【현재 가능 — v4】 Docker 통합 테스트는 `RUN_DOCKER_IT=1`일 때만 돈다 — `apm_gateway/tests/test_docker_it.py`(로컬 127.0.0.1 전용 · 저장소 밖 로컬 토큰 `JENNIFER_IT_TOKEN` · 라이선스 없으면 `source_unavailable`이 정상). **v4.1 실행 — 2 passed**(§10.2).

**두 단계**

| 단계 | 착수 조건 | 할 일 | 상태 |
|---|---|---|---|
| **J0-L-a**(라이선스 없이) | 없음 | Dockerfile · compose · 샘플 앱 · 초기 설정 스크립트 · 녹화 하네스 · 목 서버 · README → 기동 확인(관리자 · `GET /api/domain` · 에이전트 거부 로그 · arm64) | **【현재 가능】 2026-09-29 완료** — 녹화본은 라이선스 없는 상태(21건 · J0-L-b에서 재녹화) · 부하 재현 스크립트는 J0-L-b(이벤트 재현과 함께) |
| **J0-L-b**(평가판 적용 · **2주 안**) | 사용자 할 일 1·2·9 | 아래 2주 체크리스트 | 【계획】 |

**J0-L-b 2주 체크리스트** 【계획】(일자는 영업일)
- D1 — 라이선스 등록 · 에이전트 접속 확인(에이전트 로그의 거부 줄이 사라지는지) · `GET /api/instance`의 `hostName`·`ipAddress` 확인 · 최신 에이전트를 받았으면 WAS를 JDK 17로 올린다 ·
  `/api/status/*` 시 단위 규칙 확인(토큰은 `bootstrap_local.py`로 이미 받는다)
- D1~D2 — 녹화 하네스로 §5.3 허용 경로 **전건** recorded JSON 채집 · MB·ms 단위 대조(U-2·U-12)
- D2~D3 — 쓰기·제어 API가 실제 메서드로 거부되는지(로컬만 · U-5 — GET 라우팅은 J0-L-a에서 확인) · 콘솔로 덤프·PLC 기능 확인(U-8)
- D3~D5 — 샘플 앱 + EVENT 룰 임계 하향으로 이벤트 재현(`errorType`·`metricsName` 양쪽 · U-1·U-13) · 폴러 커서 검증용 이벤트 채집
- D5~D7 — X-View 전수 여부(건수 대 TPS · U-14) · p95·에러율 집계 검증 데이터 · 시각 형식(epoch ms)
- D8~D10 — 계약 테스트 픽스처 확정(마스킹 · 출처 표지 · 에이전트 버전) · 누락분 재채집
- D11~D14 — 여유 · **만료 전** recorded JSON 전체 백업 → 이후는 목 서버
- 2주 안에 끝내지 못할 것 같으면 **장기 개발용 라이선스를 영업에 문의**한다.

**이벤트 재현** 【계획 — J0-L-b】 — 샘플 앱의 느린 응답·힙 증가·예외를 반복 호출하고, 콘솔 [관리 > 룰 > EVENT룰]에서 임계를 낮춰 EVENT를 일으킨다(임계 변경 API는
없다). 콘솔과 `/api/dbsearch/event`로 확인하고, 게이트웨이 폴러(§8.2 — v4 구현)가 정규화 레코드를 발행하는지 본다. **의도한 종단 시험이 아니면 발행처를 공유 로컬
Redis(`redis/docker-compose.yml` — 호스트 6380)의 `alarm:raw`로 두지 않는다** — 본체 서버가 떠 있으면 AlarmWorker가 그 스트림을 소비해 게이트·조사까지 흘러간다.
임시 Redis 컨테이너(§10.2 예시 — 127.0.0.1:16390)나 별도 스트림 키(`APM_EVENT_STREAM_KEY`)를 쓰고, 끝나면 자기가 띄운 것만 지운다.

**폴백 — 목 Open API 서버** 【현재 가능】 — 라이선스를 구하지 못하거나 2주가 지나면 위 목 서버로 대체한다(87 R-23). 허용목록·반환 계약·폴러 멱등 검증에는 충분하지만 실제 EVENT
발생·필드 변형은 재현하지 못한다. 어느 쪽이든 J1 이후 계약 테스트의 정본은 recorded JSON(fixture)이고, 게이트웨이 계약 테스트는 목 서버를 상대로 돌린 뒤 `/__mock/hits`로
**허용목록 밖 호출 0회**를 단언한다(87 §6 [v3.3]).

**벤더 데모 서버(선택지 · 기본 미사용)** — `https://java.jennifersoft.com`·`https://dev.jennifersoft.com`은 Open API가 열려 있지만(`/api-v2/auth-test` 401 · 토큰 필요)
**공개 계정·토큰이 없다**. 쓰려면 벤더에 계정·토큰을 요청하고, 외부 서비스 호출이라 **건별 사용자 승인**을 받는다.

**사용자 할 일(외부 전제 — J0-L-b 착수 조건)** — 87 §0.8 (10)이 정본이다.

| # | 할 일 | 상태 |
|---|---|---|
| 1 | **평가판 라이선스 신청** — 폼에 Agent IP·Server IP를 적는다(초안: 서버 `172.29.87.10` · WAS `172.29.87.20`). 벤더에 "Docker Desktop(Mac)에서 등록할 IP"를 확인한다(회사 이메일·전화 확인 · 2주 · 사이트당 1회) | 남음 |
| 2 | **2주 일정 확보** 또는 **장기 개발용 라이선스 문의**(sales.ko@jennifersoft.com) | 남음 |
| 3 | 설치본 입수 경로 결정 | **완료**(2026-09-29 — 공개 S3 · 사용자 확정) |
| 4 | Docker Desktop 자원(메모리 8GB · CPU 2 이상) | **완료**(자원 충분 — Bootstrap Check를 켠 채 기동) |
| 5 | arm64 결과 수용 | **완료**(서버 arm64 네이티브 기동) |
| 6 | 로컬 뷰 서버 초기 설정 | 관리자·토큰은 **스크립트로 자동화(완료)** · 라이선스 등록만 남음(파일을 `JENNIFER_DIST_DIR/license`로 두면 entrypoint가 복사 — 동작은 수령 뒤 확인) |
| 7 | (계획에 편입 완료) 샘플 앱 + EVENT 룰 임계 조정 | — |
| 8 | (선택) 벤더 데모 서버 — 계정·토큰 요청 + 외부 호출 건별 승인 | 선택 |
| 9 | **최신 Java 에이전트(5.6.x) 입수** — 공식 다운로드(라이선스 키). 못 구하면 5.5.2.5 + JDK 11로 채집하고 출처 표지에 에이전트 버전을 적는다 | **신규(2026-09-29)** · 남음 |

**주의** — 로컬 설치본(5.7.0.1)·에이전트(5.5.2.5)와 운영 버전이 다를 수 있다(87 R-25·R-28) — 운영 채집분이 생기면 계약 픽스처를 운영분 우선으로 바꾼다. 로컬 설치본에
공식 MCP(LLM 프록시)가 들어 있는지는 「확인 불가」이고, 된다 해도 G-8(미채택) 판정은 바뀌지 않는다(87 §0.8 (7)).

## 4. 본체 측 설정

### 4.1 설정 파일 지도

| 파일 | 프로세스 | 지금(v4) |
|---|---|---|
| `apm_gateway/.env`(gitignore · 예시 `apm_gateway/.env.example`) | `apm_gateway` | 【현재 가능】 제니퍼 URL·토큰 · 게이트웨이 Bearer · 호출 상한 · 폴러 · Redis — §4.2 |
| `apm_gateway/config/instance_map.yaml` · `event_levels.yaml` · `was_signatures.yaml` | `apm_gateway` | 【현재 가능】 게이트웨이 소유 정책(정합 규칙·수동 매핑 · 이벤트 레벨 → severity · WAS 잠정 임계). 루트 `config/`가 아니다(자체 cwd) |
| `config/db_registry.yaml` | 본체 | 【현재 가능】 `solutions[apm]` 등재(`plans/125` A-1 — 보기 표 `views` · 엔드포인트가 있어야 활성) + **`sources[]`**(v5 · J8 — 제니퍼 소스 ↔ 존 정본) — §4.4 |
| `sre_agent/.env` | `sre_agent` | 【현재 가능】 `APM_MCP_URL`·`APM_MCP_TOKEN` · `APM_GUIDANCE_ENABLED` · `APM_SIGNATURES_ENABLED` — §4.6 |
| 루트 `.env` | 본체(게이트 포함) | 【현재 가능】 `NOISE_APP_IMPACT_ENABLED` · `NOISE_APM_MCP_URL`·토큰 · 사건창 — §4.7 · (J5) 두 번째 MCP 엔드포인트는 보류 |
| `mcp_server/config.toml`·`mcp_server/.env` | `mcp_server` | **제니퍼 키를 넣지 않는다.** v4는 `mcp_server`를 바꾸지 않았다(정합용 폴스타 도구 미추가 — §4.5) |
| `noise_gate/alarm_server/alarm_server.env` | `alarm_server` | **변경 없음** — 제니퍼 폴러는 게이트웨이에 있다 |

### 4.2 게이트웨이 설정 【현재 가능 — v4 · `apm_gateway/apm_gateway/config.py` · `apm_gateway/.env.example`】

비밀·접속값은 `apm_gateway/.env` 한 곳에 둔다(`mcp_server` 로더와 같이 **이미 있는 환경변수는 덮어쓰지 않는다**). 정책은 `apm_gateway/config/*.yaml`이다.

```dotenv
# apm_gateway/.env — 인라인 주석 금지(주석은 별도 줄). list 값은 JSON 배열.
# 이 파일의 JENNIFER_* 키는 게이트웨이 프로세스 전용이다 — 에이전트를 붙인 WAS 호스트 셸에 export하지 않는다(R-29).
JENNIFER_API_URL=https://jennifer.example.internal:7900
JENNIFER_API_TOKEN=<AIOps 전용 토큰>
# 조회 도메인 제한(비면 /api/domain 전체 — 좁히기만)
JENNIFER_DOMAIN_IDS=[]
JENNIFER_API_TIMEOUT_SECONDS=10
JENNIFER_RATE_LIMIT_PER_SEC=5
JENNIFER_MAX_RESPONSE_BYTES=4194304
APM_GATEWAY_HOST=127.0.0.1
APM_GATEWAY_PORT=9096
APM_GATEWAY_LOG_LEVEL=INFO
# 게이트웨이 MCP 서버 전송 인증 — 비면 무인증(운영 필수)
APM_GATEWAY_BEARER_TOKEN=<정적 Bearer 토큰>
APM_TIMEZONE=Asia/Seoul
APM_INSTANCE_CACHE_SECONDS=600
APM_PROFILE_CALLS_PER_INVESTIGATION=5
# 이벤트 폴러(옵트인 · 주기 하한 10초)
APM_EVENT_POLLER_ENABLED=false
APM_EVENT_POLL_INTERVAL_SECONDS=30
APM_EVENT_MIN_LEVEL=warning
APM_EVENT_STREAM_KEY=alarm:raw
REDIS_HOST=redis.example.internal
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=
```

**기본값 원칙**
- 제니퍼는 **패키지 배포 자체가 스위치**다. 게이트웨이를 배포하지 않으면 현행과 비트 동일하다(소비자 플래그·URL도 기본 off·빈 값).
- 게이트웨이 안의 폴러는 기본 off다. 원시 API 도구(`EXPOSE_RAW_APM_API`)·J7(`EXPOSE_APM_EXPORTER`)은 **만들지 않았다**(수용 기준 밖 · J7 보류).
- URL이 비었는데 도구를 부르면 빈 결과가 아니라 `{"error": "not_configured"}`를 돌려준다.
- **게이트웨이 설정에 폴스타 DB 연결 문자열이 없다**(`apm_gateway/tests/test_boundary.py` `test_no_polestar_db_connection_settings`).
- `.env.example`이 로더가 읽는 키를 빠짐없이 담는지 테스트가 소스에서 뽑아 대조한다(`apm_gateway/tests/test_config.py` — `mcp_server` 전례).

**다중 소스 설정(v5 · J8 · D-287 ③)** 【현재 가능 — `config.py` `_load_sources`】 — 뷰 서버가 여럿이면 위의 `JENNIFER_API_URL`·`JENNIFER_API_TOKEN`을 **비우고**
`JENNIFER_SOURCES`와 소스별 접두 키를 쓴다. 두 방식을 함께 쓰면 게이트웨이가 뜨지 않는다(정본 모호 — 침묵 선택 금지).

```dotenv
# 소스 id JSON 배열 — 소문자 슬러그 [a-z][a-z0-9_]{0,15} · 예약어 default·api · 선언 순서 = 조회·표시 순서
# id는 루트 config/db_registry.yaml solutions[apm].sources[].id와 같게 둔다(존은 레지스트리가 정한다)
JENNIFER_SOURCES=["bank","common","legacy"]
# 소스마다 필수 두 키 — <ID>는 id 대문자(bank → JENNIFER_BANK_*) · 토큰은 소스별 AIOps 전용 토큰
JENNIFER_BANK_API_URL=https://jennifer-bank.example.internal:7900
JENNIFER_BANK_API_TOKEN=<은행존 뷰 서버 토큰>
JENNIFER_COMMON_API_URL=https://jennifer-common.example.internal:7900
JENNIFER_COMMON_API_TOKEN=<공동존 뷰 서버 토큰>
JENNIFER_LEGACY_API_URL=https://jennifer-legacy.example.internal:7900
JENNIFER_LEGACY_API_TOKEN=<레거시 뷰 서버 토큰>
# 선택 키 — 비면 전역 JENNIFER_DOMAIN_IDS·JENNIFER_API_TIMEOUT_SECONDS·JENNIFER_RATE_LIMIT_PER_SEC·JENNIFER_MAX_RESPONSE_BYTES
JENNIFER_LEGACY_API_TIMEOUT_SECONDS=4
JENNIFER_LEGACY_DOMAIN_IDS=[3000]
```

- 기동 실패(메시지 1줄 · 종료 코드 2 · 값은 싣지 않는다): 두 방식 동시 설정 · 소스 필수 키(`_API_URL`·`_API_TOKEN`) 누락 · id 형식 위반·예약어·중복 · `JENNIFER_SOURCES`가 문자열 JSON 배열이 아님.
- 소스마다 클라이언트가 따로다 — 토큰·초당 상한·응답 크기 상한·인벤토리 캐시가 소스별이고, 한 소스의 토큰은 다른 소스 요청에 실리지 않는다(`apm_gateway/tests/test_multi_source.py`).
- **소스 타임아웃은 소비자 호출 상한보다 짧게** 둔다. `noise_gate`의 게이트웨이 호출 상한은 5초(코드 상수)인데 전역 기본 타임아웃은 10초다 — 느린 소스 하나가 게이트웨이 응답 전체를
  늦춘다(87 R-33). 게이트웨이는 소스를 병렬로 부르고, 타임아웃 난 소스는 `[한계]`로 빼고 나머지를 돌려준다.
- 레지스트리 `sources[].id`와 `JENNIFER_SOURCES`가 다르면 `noise_gate`의 존 좁히기가 `invalid_argument`(모르는 `source_ids`)로 실패한다(판정은 그대로 · §4.7). 둘을 같이 바꾼다.
- `JENNIFER_<ID>_*` 키도 R-29 대상이다 — 에이전트를 붙인 WAS JVM 환경에 두지 않는다.

**장기 작업 · 결과 파일 · 호출 주체(`plans/134` W0-B · D-296 ④ · D-299 ④)** 【현재 가능 — `application/jobs.py`·`spool.py` · `interface/server.py` · 계약 `spec/SPEC-apm-question-coverage.md` §3】
— 조회 범위 상한을 걷어낸 대신, 오래 걸리는 조회는 **작업**으로 돌고 큰 결과는 **결과 파일**(스풀)로 나간다.

```dotenv
# 결과 파일 위치 — 상대 경로는 게이트웨이 루트(apm_gateway/) 기준 · 디렉터리 0700 · 파일 0600 · 저장소에서는 gitignore
APM_SPOOL_DIR=var/spool
# 도구 응답에 바로 싣는 행 수(넘으면 나머지는 결과 파일 — 조회는 전량) · 결과 파일 청크 1개 행 수
APM_INLINE_ROWS=500
APM_ARTIFACT_CHUNK_ROWS=2000
# 결과 보관 기간(초) — 지나면 지운다(조회 범위 축소 수단이 아니다)
APM_ARTIFACT_RETENTION_SECONDS=86400
# 백그라운드 작업 동시 실행 수 · 정체 판정(초) · 우선순위 에이징(초)
APM_JOB_MAX_CONCURRENT=4
APM_JOB_STALL_SECONDS=300
APM_PRIORITY_AGING_SECONDS=10
# 소비자별 전송 토큰(선택) — 호출 주체를 가른다. 단일 APM_GATEWAY_BEARER_TOKEN은 주체 default
APM_GATEWAY_BEARER_TOKENS={"chat": "<본체용>", "investigation": "<sre_agent용>", "alarm": "<noise_gate용>"}
```

- **`wait_seconds`**: 본체 채팅은 데이터 도구에 `wait_seconds`(호출 상한 − 2초 · 조회 마감 이내)와 `owner`(`user:<sub>`)를 싣는다. 그 안에 끝나지 않으면 게이트웨이는 작업 ID를 돌려주고 백그라운드로
  계속한다. `wait_seconds`를 안 넘기는 소비자(`sre_agent`·`noise_gate`)는 종전처럼 끝날 때까지 기다린다.
- **작업 도구 3종** `apm_job_status`·`apm_job_cancel`·`apm_job_read`는 **같은 주체 + 같은 `owner`**일 때만 답한다(아니면 `job_not_found` — 존재를 드러내지 않는다). 본체는 그 위에 사용자 소유자 확인을 한 번 더 한다(작업 API `/api/v1/apm/jobs/*` · D-262 판정).
- **프로파일 예산(134 W5)**: 주체 `chat`(본체 채팅 전용 토큰)은 `apm_transaction_profile` 예산을 쓰지 않는다. 그 밖 주체(`investigation`·단일 토큰 `default`·무인증 `anonymous`)는 칸(주체 · 조사 ID → 없으면 `owner` → 없으면 미지정)마다 `APM_PROFILE_CALLS_PER_INVESTIGATION`(기본 5)회/1시간이다. 주체는 전송 토큰으로만 정해진다 — 인자로 면제를 얻을 수 없다. 단일 토큰으로 운영하면 채팅도 사용자별 5회 제한에 걸리므로, 채팅 면제가 필요하면 위처럼 `chat` 토큰을 따로 두고 본체 `MCP_SOURCE_TOKENS`(`{"apm": "<토큰>"}`)의 APM 토큰을 그 값으로 맞춘다.
- **호출 속도는 그대로 5회/초**(G-12 협의 전)다. 대기열은 폴러 > 대화형 > 백그라운드 순으로 판다(10초 넘게 기다리면 한 단계 올림) — 큰 작업이 폴러 알람 수집을 굶기지 않는다.
- **`JENNIFER_MAX_RESPONSE_BYTES`의 뜻이 바뀌었다** — 종전 「넘으면 오류」 → 지금 「메모리 임계」(넘으면 스풀 임시 파일로 받아 원소 단위로 읽는다 · 오류로 끊지 않는다).
- **재기동**: 진행 중이던 작업은 `interrupted`(「게이트웨이 재기동으로 중단 — 다시 요청해야 합니다」)가 되고, 끝난 작업의 결과는 보관 기간 동안 그대로 읽힌다. SIGTERM 정상 종료도 같은 기록을 남긴다.
- 같은 토큰을 두 주체에 주면 기동 실패다. 감사 1줄에 `principal=`·`job_id=`가 붙는다(토큰 값 없음).

### 4.3 게이트웨이 기동 【현재 가능 — v4 · v5 기동 로그】

```bash
# [CWD=apm_gateway/ · 루트 venv 공유 — mcp_server와 같은 방식(둘 다 Python ≥3.11) · 설정은 apm_gateway/.env]
cd apm_gateway && ../.venv/bin/python -m apm_gateway
# 기동 로그 예(v5 — 소스 id와 설정 여부만 · URL·토큰 값 없음):
#   APM 게이트웨이 시작: 127.0.0.1:9096 · 허용 경로 37(130 W2 — 종전 36 · v4 16) · {'sources': [{'id': 'bank', 'url_set': True, 'token_set': True, 'domain_filter': []},
#   {'id': 'common', …}], 'bearer': True, 'poller': False, 'poll_interval': 30, 'overrides': 0}
#   (단일 설정이면 sources = [{'id': 'default', …}] · 둘 다 비면 [])
#   (Bearer가 비면) 전송 인증 off(APM_GATEWAY_BEARER_TOKEN 미설정) — 운영 배치에서는 필수
#   (설정 오류면) APM 게이트웨이 설정 오류 — 기동 중단: JENNIFER_SOURCES와 단일 설정 키(…)를 함께 쓸 수 없다 …  (종료 코드 2)
```

- SSE 엔드포인트는 `http://<호스트>:9096/sse`다. 소비자(`sre_agent`·게이트)는 이 주소와 Bearer만 안다.
- 헬스체크는 도구 `gateway_health`다(소스마다 `/api/domain` 1회 · 병렬 · 30초 캐시) — **소스별 행**(설정·도달·도메인 수·허용 경로 수)과 최상위 `status`(`ok` 모두 정상 ·
  `degraded` 하나라도 · `not_configured` 소스 0개)·폴러 상태를 돌려준다.
- 제니퍼 **버전 에코는 없다**(87 R-12의 `api_version_expect`) — 허용목록 37템플릿에 버전 조회 경로가 없다. 에이전트 버전은 `apm_instance_map` 결과의 `agent_version`에 실린다.
- 로컬에는 `mcp_server`(9099)·조사 프로파일용(9097)·`sre_agent`(9098)·`alarm_server`(TCP 9100)가 장기 실행 중일 수 있다. 게이트웨이 기본 포트 9096은
  이들과 겹치지 않는다(2026-09-29 실측). 남이 띄운 프로세스는 건드리지 않는다.
- `mcp` 패키지는 `<2`로 고정돼 있다(D-181 · 설치본 1.29.1). 게이트웨이 `pyproject.toml`도 같은 제약을 선언한다.

### 4.4 `config/db_registry.yaml` — `apm` 솔루션과 제니퍼 소스 【현재 가능 — `plans/125` A-1 등재 · v5 J8 `sources[]`】

`apm` 솔루션은 `plans/125` A-1(87 J5 본체 쪽 · D-281)이 등재했다(`backend: mcp` · `family: jennifer` · 보기 표 `views` — 채팅 처리기가 부르는 고정 보기).
**엔드포인트(`MCP_SOURCE_ENDPOINTS`의 `apm`)가 설정돼야만 활성**이고, 비활성이면 처리기 등록·분해 프롬프트 렌더가 0이다(D-281 ②). v5(J8)는 여기에 **제니퍼 소스 표**를 더했다.

```yaml
solutions:
  - code: apm
    label: 제니퍼(APM · WAS 모니터링)
    backend: mcp
    family: jennifer
    views: [ ... ]            # plans/125 — 보기 id → 게이트웨이 도구
    sources:                  # v5 · J8 · D-287 ④ — 제니퍼 소스 ↔ 존 정본
      - {id: bank, label: 은행존 제니퍼, zone: bankjon}
      - {id: common, label: 공동존 제니퍼, zone: gongjon}
      - {id: legacy, label: 레거시 제니퍼, zone: bankjon}
```

- **정본은 레지스트리 하나다**(D-053) — 게이트웨이는 존을 모른다(루트 `config/`를 읽지 않는다 · D-274 ③). 소비자(본체 알람 라우트·`noise_gate`)가 이 표로 존을 푼다.
- 레거시 소스는 은행존이다(G-14 ① — `zones` 라벨 「은행존(K리전 은행/레거시)」).
- 로더 검증(`src/routing/registry.py` `_parse_sources` — 틀리면 `RegistryError`로 로드 실패): id 형식(`[a-z][a-z0-9_]{0,15}`) · 예약어 `default`·`api` · 같은 솔루션 안 중복 ·
  선언되지 않은 존. `zone`을 비우면 존 없는 소스(전 존 구독자·관리자만).
- 조회 함수: `sources_of("apm")`(선언 순서) · `alarm_source(db_id)` — 알람 `dbId` `jennifer_<id>`(= `{family}_{id}`) → (시스템, 소스). 단일 설정 `jennifer`는 존 없음(경고 없음),
  표에 없는 `jennifer_<id>`는 **id별 경고 1회** 뒤 존 없음. 알람 존 판정 `src/routing/zones.py` `db_id_to_zone`이 이 함수를 쓴다(§8.4 ⑥).
- 소스 추가 절차: 게이트웨이 `JENNIFER_SOURCES`·접두 키(§4.2)와 이 표를 **같은 id로 함께** 바꾼다. 존을 모르면 `zone`을 비우고 추가해도 된다(알람은 전 존 구독자·관리자만 본다).
- 이 표를 더해도 분해·라우팅 프롬프트 렌더는 바이트가 같다(v5 실측 — 렌더 지문 11종 · 87 §0.14). 채팅의 위치어 → 소스 좁히기(G-16)는 `plans/125`가 구현한다.
- `databases`·`ACTIVE_DB_IDS`에는 넣지 않는다. 제니퍼는 DB가 아니다.

### 4.5 인스턴스 ↔ hostname 정합 【현재 가능 — v4 · `application/resolver.py` · `config/instance_map.yaml`】

제니퍼 인스턴스명과 폴스타 hostname이 같다는 보장이 없다(87 R-1 — 최대 리스크). 게이트웨이 `application/`이 정합하고, 순서는 다음과 같다
(87 §5.3 [v3.2]).

0. 정합 파일의 수동 `overrides`(`instance_name`·`instance_id`·`domain_id` 중 하나 이상 + `hostname`) — 모든 자동 규칙보다 우선(high).
1. **`Instance.hostName` 직접 대조**(`host_name` 규칙) — 대소문자·FQDN 정규화 후 짧은 이름끼리도 대조한다(high). 벤더 자체 필드라 폴스타 테이블 실재에 기대지 않는다.
2. **폴스타 `was_object` — v4 미구현.** 운영 실재·채움률(U-10)이 확인되지 않아 `mcp_server` 정합 도구(「예정 이름」 `polestar_was_instances`)와 게이트웨이의
   MCP 호출을 만들지 않았다. 규칙 목록에 `polestar_was_object`를 넣으면 경고 후 건너뛴다. J0-O에서 U-10을 확인한 뒤 재판정한다.
   `ipAddress` ↔ 폴스타 IP 보조 대조도 폴스타 조회가 필요해 같은 이유로 보류다.
3. 인스턴스명 규칙 — `exact`(high) → `prefix`(`hostname_`·`hostname-` · medium) → `regex`(명명 그룹 `hostname` · medium).

```yaml
# apm_gateway/config/instance_map.yaml — 기본값(값은 플레이스홀더)
version: 1
match_rules:
  - kind: host_name
  - kind: exact
  - kind: prefix
overrides: []
#  - instance_name: "WAS-EXAMPLE-01"
#    domain_id: 1000
#    hostname: "example-host01"
#    port: 8080
#    kind: tomcat
# (v5 · J8 — 다중 소스) override에 source_id를 주면 그 소스에만 쓴다(없으면 전 소스)
#    source_id: bank
# (v5 · J8) 소스별 규칙 — 있으면 그 소스는 전역 match_rules 대신 쓴다(레거시 명명 규칙이 다를 때 · J0-O 실측 뒤 채운다)
# per_source:
#   legacy:
#     match_rules: [{kind: host_name}, {kind: exact}]
```

- **다중 소스(v5)**: 정합은 소스마다 한다(소스 하나 = 정합기 하나). 한 hostname이 두 소스에서 정합되면 둘 다 싣고(상한 5 공유) 행마다 `source_id`와 그 소스의
  `match_confidence`·`match_reason`을 단다 — 해소 결과의 신뢰도는 정합된 소스가 모두 high일 때만 high다. 다른 존의 서로 다른 서버가 같은 hostname이면(87 R-32)
  override의 `source_id`로 고정하거나 `source_ids`로 좁힌다(`noise_gate`는 알람 존으로 좁힌다 — §4.7). 정합 파일이 설정에 없는 소스를 가리키면 기동 로그에 경고 1줄.

- 해소 결과(`instance_resolution`)에 신뢰도와 근거(`override`·`host_name`·`exact`·`prefix`·`regex`)를 싣고, medium이면 `limits`에 적는다.
  **미매칭은 빈 결과가 아니라 `{"error": "instance_unresolved"}`**다. 도메인이 0건이거나 모든 도메인 조회가 실패하면 `source_unavailable`이다(빈 결과를 정상으로 보지 않는다).
- 한 호스트에 인스턴스가 여럿이면 최대 5개까지 함께 본다(넘치면 `limits` · `instance_id`로 좁힌다).
- 역방향(이벤트 → hostname)은 override → `hostName` → `regex` 순이다. 못 찾으면 `hostname=""`로 발행한다(§8.2).
- 인벤토리(도메인 → 인스턴스)는 소스마다 TTL 캐시(기본 600초 · `APM_INSTANCE_CACHE_SECONDS`)한다. **v5(J8)부터 도메인 목록 실패·도메인 0건·전 도메인 조회 불가
  인벤토리는 30초만 캐시한다**(87 F-3 해소 — `gateway_health` 캐시와 같은 값). v4.1에서는 이 결과도 600초 캐시돼 라이선스 적용 직후 도구와 `gateway_health`가 어긋났다.
  로컬 Docker(도메인 0건)에서 31초 뒤 두 소스 모두 다시 조회하는 것을 감사 `sources=bank:1,common:1`로 확인했다(87 §0.14).
- **부기(2026-09-30 · D-195 부기 — 운영 실측)**: 적재는 도메인마다 순차 호출이라 도메인이 많으면 오래 걸린다(운영: 도메인 약 350개 · 호출 상한 5회/초 → 적재 1회 ≈ 70초).
  그래서 **적재는 한 번에 하나**(겹친 요청은 같은 적재를 기다린다 · 기다리던 요청이 끊겨도 적재는 끝까지 가서 캐시에 남는다)이고, **만료 뒤에는 기존 명단으로 바로 답하면서
  뒤에서 한 번만 갱신**한다(갱신 실패 시 기존 명단 유지 · 60초 뒤 재시도 · 기준 시각이 지난 명단이면 `[한계] 인스턴스 목록이 N분 전 기준` 고지). 게이트웨이는 **기동 직후
  백그라운드로 선적재**하며 로그 `인스턴스 목록 적재: 도메인 N · 인스턴스 M · … · X초`로 끝을 알린다 — 재기동 직후 이 줄이 나오기 전의 요청은 적재를 기다린다.
  `APM_INSTANCE_CACHE_SECONDS`는 이제 정상 명단의 **갱신 주기**다(명단이 자주 바뀌지 않으면 늘려도 된다). 뒤에서 갱신하는 것은 **정상 명단뿐**이다 — 위 30초 캐시
  인벤토리(도메인 목록 실패·도메인 0건·전 도메인 조회 불가)는 쓸 명단이 없으므로 30초가 지나면 요청이 적재를 기다린다. 갱신 결과가 그런 인벤토리면 기존 정상 명단을
  유지한다. 선적재·적재 로그·`[한계]` 고지는 소스마다다(로그 끝에 `· 소스 <id>` · 소스가 여럿이면 `[한계] 소스 <id> 인스턴스 목록이 …`).
- **인스턴스 이름·업무명 → 인스턴스(`plans/130`)** — hostname 대신 정확한 인스턴스 이름(`instance_name`)으로 부르거나, 이름 일부·업무명으로 찾는 길은 §6.4다. 업무명 → 인스턴스 수동 매핑은 같은 파일의 선택 키 `business_map`이다(작성법 §6.4.6).
- OpenMetrics 노출(§9)의 `nodename`(= 폴스타 `server_name`) 역해소는 J7 몫이다(보류).

### 4.6 `sre_agent` 【현재 가능 — v4 · J3】

- `sre_agent/.env`(CWD 기준으로 읽힌다 — 레포 루트에서 띄우면 루트 `.env`·`.encenv`를 읽어 과금 경로로 갈 수 있다 · `docs/26_sre_agent_guide.md` §5.3):

  | 키 | 기본 | 뜻 |
  |---|---|---|
  | `APM_MCP_URL` · `APM_MCP_TOKEN` | 빈 값 | 게이트웨이 SSE 주소(예 `http://127.0.0.1:9096/sse`) · 게이트웨이 Bearer(`APM_GATEWAY_BEARER_TOKEN`과 같은 값 — 제니퍼 토큰이 아니다) |
  | `APM_GUIDANCE_ENABLED` | false | APM 지침(앵커 4종 · 조사 순서 · 현재값·폴백 노트 · apm 사건의 APM 플레이북 · 브리핑 소스 라벨·APM 한계 · 정체 가드) |
  | `APM_SIGNATURES_ENABLED` | false | 게이트웨이 `was_signals`를 중요도 판정 신호로 승격(`SEVERITY_JUDGE_ENABLED=true`와 함께) |

- `APM_MCP_URL`이 있으면 `_build_mcp_servers()`(`sre_agent/sre_agent/interface/mcp_service.py`)가 `"apm"` 서버(`mode: sse` · `health_check_tool: gateway_health` ·
  Bearer)를 더한다. 폴스타 URL 없이 APM만 있어도 등록한다. **비우면 결과가 종전 dict와 같다**(`sre_agent/tests/test_apm_consumer.py`).
- 플래그 둘이 off면 조사 지침·판정·권고·브리핑이 **바이트 동일**하다(KV 캐시 보존 — 같은 테스트 파일이 단언).
- `resourceType="apm.Instance"` 이벤트의 kind는 플래그와 무관하게 `apm`이다(R-16 선판정 — `sre_agent/sre_agent/domain/investigation_limits.py` ·
  `application/investigation_guidance.py` `classify_alarm_kind`). 그래서 WAS 사건에 OS 플레이북이 붙지 않는다.

### 4.7 `noise_gate`와 본체 【현재 가능 — v4 · J4 / 계획 — J5】

- 제니퍼 이벤트 폴러는 `alarm_server`가 아니라 **게이트웨이**에 있다(§8.2). `alarm_server`는 바뀌지 않았다.
- 루트 `.env`(`NoiseGateConfig` · `env_prefix="NOISE_"` · `src/config.py`):

  | 키 | 기본 | 뜻 |
  |---|---|---|
  | `NOISE_APP_IMPACT_ENABLED` | false | `app_impact` 승격(§8.4 ①) — `NOISE_ENABLE_NOISE_GATE`가 켜져 있어야 한다 |
  | `NOISE_APM_MCP_URL` · `NOISE_APM_MCP_TOKEN` | 빈 값 | 게이트웨이 SSE 주소 · 게이트웨이 Bearer(제니퍼 토큰이 아니다) |
  | `NOISE_APP_IMPACT_WINDOW_MINUTES` | 10 | 사건창(분) — `apm_events`의 `lookback_minutes` |

- 게이트웨이 클라이언트는 `noise_gate/infrastructure/apm_gateway_client.py`다(`sre_agent_client.py` 전례 — SSE · Bearer · 호출 타임아웃 5초 · TCP 사전 도달성 확인 · 미가용이면 30초 동안 재시도 안 함).
  켜졌는데 URL이 비었거나 생성에 실패하면 기동 로그에 경고를 남기고 승격 없이 진행한다.
- **존 좁히기(v5 · J8)** — `app_impact` 조회는 알람 DB의 존에 대응하는 제니퍼 소스만 `source_ids`로 넘긴다(`notification_gate.py` `apm_source_ids_for` — 예 `polestar_b0` →
  `["bank","legacy"]` · `polestar_cm_gp`·`polestar_cm_yd` → `["common"]`). 존이 없거나 그 존에 소스가 없으면 인자를 넣지 않는다(전 소스 — 종전 호출과 같다). 다른 존의 같은
  hostname 인스턴스 이벤트로 승격하지 않게 하려는 것이다. 게이트웨이가 모르는 id라고 답하면(`invalid_argument` — 레지스트리·게이트웨이 소스 불일치) 판정은 그대로 두고 사유를
  `stage_evidence.app_impact_error`에 남긴다 — **전 소스로 다시 부르지 않는다.** 단일 설정 게이트웨이(`default`)에 레지스트리 소스가 있는 배포는 이 상태가 되므로,
  `app_impact`를 켜기 전에 게이트웨이를 다중 설정으로 맞춘다.
- `ALARM_PROCESS_API_BASE_URLS_CSV`(호스트 참고 표)의 키는 알람 `dbId`다 — 다중 소스 이벤트는 `jennifer_<id>`로 온다(종전 `jennifer=` 매핑은 단일 설정에만 맞는다).
- 채팅 질의(J5)는 **보류**다 — `plans/121` 처리기가 게이트웨이를 두 번째 MCP 엔드포인트로 부르는 구조인데 처리기 코드가 아직 없다(`DBHubConfig.server_url`은 하나).

### 4.8 `.env` 작성 규칙 (Known Mistakes) 【현재 가능】

- list/dict 값은 JSON 배열 형식(`["a","b"]`)으로 쓴다.
- **인라인 주석 금지.** 주석은 별도 줄에 쓴다. 특히 빈 값 뒤에 주석을 붙이지 않는다.
- 본체·`sre_agent`·`alarm_server`(pydantic-settings)의 `env_file` 값은 `os.environ`에 들어가지 않는다. 설정 유무를 `os.getenv()`로 판단하지 않는다.
  `mcp_server`는 자체 로더가 `os.environ`에 넣는다(`mcp_server/mcp_server/config.py:201-219`). **게이트웨이도 같은 방식이다** — `apm_gateway/apm_gateway/config.py`
  `load_dotenv`가 `apm_gateway/.env`를 `os.environ`에 넣되 **이미 있는 키는 그대로 둔다**(값은 로그에 남기지 않는다). 셸에서 export한 값이 `.env`보다 이긴다.
- 게이트웨이 `.env.example`이 로더가 읽는 키를 빠짐없이 담는지, 인라인 주석이 없는지 `apm_gateway/tests/test_config.py`가 소스에서 키를 뽑아 대조한다
  (전례 `mcp_server/tests/test_env_example_coverage.py`).

### 4.9 신규 키 전체 표 (v4 — 실제 키)

| 키 | 파일 | 기본 | 뜻 |
|---|---|---|---|
| `JENNIFER_API_URL` · `JENNIFER_API_TOKEN` | `apm_gateway/.env` | 빈 값 | 단일 설정 — Open API 주소 · AIOps 전용 토큰(여기에만) |
| `JENNIFER_SOURCES` · `JENNIFER_<ID>_API_URL` · `JENNIFER_<ID>_API_TOKEN` | `apm_gateway/.env` | 빈 값 | (v5) 다중 설정 — 소스 id 목록 · 소스별 필수 키(단일 설정과 동시 사용 금지 — §4.2) |
| `JENNIFER_<ID>_DOMAIN_IDS` · `_API_TIMEOUT_SECONDS` · `_RATE_LIMIT_PER_SEC` · `_MAX_RESPONSE_BYTES` | `apm_gateway/.env` | 전역 값 | (v5) 소스별 선택 키 |
| `JENNIFER_DOMAIN_IDS` · `JENNIFER_API_TIMEOUT_SECONDS` · `JENNIFER_RATE_LIMIT_PER_SEC` · `JENNIFER_MAX_RESPONSE_BYTES` | `apm_gateway/.env` | `[]` · 10 · 5 · 4194304 | 도메인 제한 · timeout · 초당 상한 · 응답 크기 상한 |
| `APM_GATEWAY_HOST` · `APM_GATEWAY_PORT` · `APM_GATEWAY_LOG_LEVEL` | `apm_gateway/.env` | 127.0.0.1 · 9096 · INFO | MCP 서버 |
| `APM_GATEWAY_BEARER_TOKEN` | `apm_gateway/.env` | 빈 값 | 게이트웨이 MCP 서버 전송 인증 — 비면 무인증(운영 필수) |
| `APM_TIMEZONE` · `APM_INSTANCE_CACHE_SECONDS` · `APM_PROFILE_CALLS_PER_INVESTIGATION` | `apm_gateway/.env` | Asia/Seoul · 600 · 5 | 시각 해석·렌더 · 정합 캐시 · 조사당 프로파일 상한 |
| `APM_EVENT_POLLER_ENABLED` · `APM_EVENT_POLL_INTERVAL_SECONDS` · `APM_EVENT_MIN_LEVEL` · `APM_EVENT_STREAM_KEY` | `apm_gateway/.env` | false · 30(하한 10) · warning · `alarm:raw` | 이벤트 폴러 |
| `REDIS_HOST` · `REDIS_PORT` · `REDIS_DB` · `REDIS_PASSWORD` | `apm_gateway/.env` | localhost · 6379 · 0 · 빈 값 | 폴러 XADD·커서·멱등 키 |
| `APM_MCP_URL` · `APM_MCP_TOKEN` | `sre_agent/.env` | 빈 값 | 조사의 두 번째 MCP 서버 |
| `APM_GUIDANCE_ENABLED` · `APM_SIGNATURES_ENABLED` | `sre_agent/.env` | false | 조사 지침 · 판정 결과 승격 |
| `NOISE_APP_IMPACT_ENABLED` · `NOISE_APM_MCP_URL` · `NOISE_APM_MCP_TOKEN` · `NOISE_APP_IMPACT_WINDOW_MINUTES` | 루트 `.env` | false · 빈 값 · 빈 값 · 10 | 게이트 → 게이트웨이 조회 · `app_impact` 승격 |
| (J5) 두 번째 MCP 엔드포인트 | 루트 `.env` | — | **보류** — `plans/121` 처리기 설계 때 확정 |
| (J7) `EXPOSE_APM_EXPORTER` · `APM_OPENMETRICS_CACHE_SECONDS` · (원시 도구) `EXPOSE_RAW_APM_API` | `apm_gateway/.env` | — | **만들지 않았다**(J7 보류 · 원시 도구 수용 기준 밖) |
| `REMEDIATION_EXECUTOR_ENABLED` | (J6 · 신규 패키지 `remediation/`) | — | **보류** — G-6 착수 조건(J3 완료 + 목업 검증 뒤) |

---

## 5. 읽기 전용 통제 (D-003 · D-189)

### 5.1 왜 토큰만으로는 부족한가

- 이 저장소의 원칙은 **읽기 전용 접근**이다(D-003 — 3중 방어: 지시 · 검증 · 서버 readonly).
- 폴스타 DB는 SELECT 전용 계정으로 막을 수 있다. 제니퍼는 다르다. **조회용으로 받은 토큰이 쓰기·제어 API도 부를 수 있다**(§3.3).
- 그래서 87은 **게이트웨이 코드가 허용한 GET 경로만 나가게** 하는 것을 1차 통제로 둔다. 토큰 권한 축소는 협의되면 2차로 더한다(87 §8.1·§8.2 · R-4).
  토큰 자체도 폴스타 DB 자격증명과 다른 프로세스(게이트웨이)에 격리한다(D-274).

### 5.2 통제 목록 【현재 가능 — v4 · 구현 위치 `apm_gateway/apm_gateway/adapters/jennifer/`·`interface/`】

| # | 통제 | 구현 위치(v4) | 전례 |
|---|---|---|---|
| C-1 | **GET만** 보낸다. 다른 메서드는 HTTP 호출 없이 거부 | `allowlist.check_request` | OpenMetrics 도구 I-2(`mcp_server/mcp_server/openmetrics_tools.py:11`) |
| C-2 | **경로 허용목록 — 메서드 + 경로 템플릿 정확 일치**(§5.3 표 · 와일드카드·`.xml` 변형 없음 · 경로 정규화 뒤 대조). **GET이라도 목록 밖이면 거부**(민감 GET 존재). 설정으로 **좁히기만** 가능(`JENNIFER_DOMAIN_IDS` — 도메인만). 신규 관리 API가 생겨도 자동 차단 | `allowlist.ALLOWED`·`match_template`(경로 변수는 숫자만) | OpenMetrics 타깃 허용목록(`openmetrics_tools.py:9-10`) |
| C-3 | **리다이렉트 비추종** — 3xx는 오류로 돌려준다(허용목록 우회 차단) | `client.py`(`follow_redirects=False` · `trust_env=False`) | `make_scrape_client` `follow_redirects=False`(`openmetrics_tools.py:83`) |
| C-4 | 서버 강제 **timeout**·**응답 크기 상한**·**초당 호출 상한** | `client.py`(Content-Length 선검사 + 스트림 누적 · 요청 간격) | `make_client`(`promql_tools.py:188-198`) |
| C-5 | LLM은 **URL·경로·필드명을 넘기지 못한다** — 값 인자(hostname·구간·N)만 받는다 | `interface/server.py` 도구 시그니처 | D-122 값 인자 조립 · PromQL `nodename` 서버 조립(D-119 ③) |
| C-6 | 원시 API 도구는 **만들지 않았다(v4)**. 만들게 되면 같은 허용목록을 그대로 쓴다(넓히지 않는다) | — | `execute_sql`·원시 PromQL 기본 비노출(D-122) · **[v3.2]** 민감 GET(`/api/auth/userlist*`·`/restapi/users`·`/api-v2/environment-variable/*`·`/api-v2/active-service/detail/*`)도 원시 도구로 불가 |
| C-7 | 토큰을 로그·오류 메시지·감사에 **0회** 노출 | `client.redact` · 헤더 전용 · 테스트(`test_client.py`·`test_tools_contract.py`·`test_server.py`) | — (J1 수용 기준) · **[v3.2]** 쿼리 `token=` 인증은 쓰지 않고, 쿼리에 `token` 키가 있는 요청은 거부 |
| C-8 | 게이트웨이는 **실행 자격증명을 갖지 않는다** — L2 실행기(J6)는 별도 패키지 | 경계 | D-274 ⑧ · 87 G-7 |

### 5.3 허용목록 정본과 거부 사례 【현재 가능 — v4 · 정본 `apm_gateway/apm_gateway/adapters/jennifer/allowlist.py` · 스펙 5.6.4 확인(87 §5.2(e))】

| 메서드 | 경로 템플릿(정확 일치) | 쓰는 곳 | 필수 파라미터 · 확인 |
|---|---|---|---|
| GET | `/api/domain` | 인스턴스 목록 전수 1단계 | 없음 · 스펙 5.6.4 확인 |
| GET | `/api/instance` | `apm_instance_map` | `domain_id` · 응답 `hostName`·`ipAddress` · 스펙 5.6.4 확인 |
| GET | `/api/realtime/instance` | `apm_app_health`·`apm_runtime_health`·`apm_resource_pool`·(J7) | `domain_id`(`instance_id` 선택) · 스펙 5.6.4 확인 |
| GET | `/api/dbmetrics/instance` | 구간 추세 | `domain_id`·`instance_id`·`interval_minute`·`metrics`·`start_time`·`end_time` · 지표 1개/호출 · 스펙 5.6.4 확인(허용값 「확인 불가 — J0-L」) |
| GET | `/api/metrics` | 지표 이름 카탈로그 | 없음 · 스펙 5.6.4 확인 |
| GET | `/api/activeService/list` | `apm_active_services`·`apm_resource_pool` | `domain_id`(`instance_id` 선택 — v4 추가) · 스펙 5.6.4 확인 |
| GET | `/api/transaction/time` | `apm_slow_transactions`·`apm_app_health`(p95·에러율) | `domain_id`·`start_time`·`end_time`(`instance_id` 선택 — v4 추가) · **1분 창** · 스펙 5.6.4 확인 |
| GET | `/api/transaction/txid` · `/api/transaction/profile.txt` · `/api/transaction/sql` | `apm_transaction_profile` | `domain_id`·`txid`·**`time`** · 스펙 5.6.4 확인 · `profile.txt`의 선택 키 `key`는 **16진수 8자리 이상**만 받는다(v4.1 로컬 실측 — 그 밖이면 500) · 게이트웨이는 `key`를 보내지 않는다 |
| GET | `/api/dbsearch/event` · `/api/dbsearch/error` | `apm_events` · 폴러 | `domain_id`·`start_time`·`end_time`(ms) · `level`은 event 전용 · `error_type`은 error 전용(대문자) · 스펙 5.6.4 확인 |
| GET | `/api/status/application` · `/api/status/sql` · `/api/status/external_call` | 시 단위 맥락(v4는 `application`만 사용) | 구간은 **시 단위만**(*"Units below hour must be set to zero"*) · `application`은 `instance_id`·`max_row` 선택(v4 추가 · `max_row=20`으로 호출) · `max_row` 기본값은 정본에 없음 · 스펙 5.6.4 확인 |
| GET | `/api-v2/deploy/{domainId}` | `apm_source_changes`·`apm_change_impact`(134 W2·W6) | `startTime`·`endTime`(ms) · 25시간 이하 · 5.6.0.5+ · v2 매뉴얼(정본 미수록) — 실응답 「확인 불가 — J0-L」 |
| GET | `/api/transaction/guid` | `apm_transaction_trace`(134 W5) | `domain_id`·`guid`·`start_time`·`end_time` · 스펙 5.6.4 확인 · `time_pattern` 거부 |
| GET | `/api/business` | `apm_instance_map(business=…)` 업무 정의 근거(130 W2 · D-290 ④ · §6.4) | `domain_id` 필수 · 도메인별 10분 캐시 · `/api/realtime/business`·`/api/dbmetrics/business`는 계속 거부 |
| GET | `/api/auth/userlist` · `/restapi/users` · `/restapi/user/{id}`(`account`) | `apm_users`(134 W7) | 없음 · 경로 `id` · **`password`는 중앙 자격증명 경계가 키째 제거** · ID·이름·이메일·휴대폰·허용 IP는 가림(G-11 미결) |
| GET | `/api-v2/environment-variable/{domainId}` | `apm_environment`(134 W7) | 경로 `domainId` · 키를 줄이지 않고 비밀 값만 `[가림]` · 값의 이메일·주민번호·휴대폰 가림 |
| GET | `/api-v2/active-service/detail/{domainId}/{txid}` | `apm_active_detail`(134 W7) | 경로 `domainId`·`txid`(`sint` — 음수 가능) · 선택 `sessionId`·`threadHash`(필수 여부 미기재 — 받은 값만 보낸다) |
| GET | `/api-v2/manage/rule/event/error/{d}` · `…/metric/{d}/{targetType}` · `…/compare/{d}/{targetType}` · `…/comparing/{d}/{targetType}` · `…/error/{d}/{errorType}/applied` · `…/error/{d}/{errorType}/individual-setting/{i}` | `apm_config(kind=event_rules)`(134 W7) | 경로 변수 형식 `enum`·`token`·`int` · `compare` 404일 때만 `comparing` 재질의(매뉴얼 표기 불일치 COV E-01) · 개별 설정 404 = 설정 없음 |
| GET | `/api-v2/manage/rule/active-service-color-range-boundary` · `/api-v2/manage/instance`(`processId` 필수 · `hostname` 선택) · `/api-v2/manage/data-server/{domains,resource,system-property-config}` · `/api-v2/manage/db/path/{d}` · `/api-v2/loaded-class/{d}/{i}`(`search` 선택) · `/api-v2/manual-rdb-export` | `apm_config(kind=color_boundary·process_instance·data_server·db_path·loaded_classes·rdb_export)`(134 W7) | v2 맨 배열·객체 · 모양이 다르면 `apm_api_error` · 404·405 = 버전 미지원 가능(COV E-28) |

- **규칙**: 표에 없는 메서드·경로는 전부 거부한다. `.xml` 변형(`/api/domain.xml` 등 5개) 제외 · 와일드카드 없음 · v1 조회 API의 **POST 변형도 거부**(게이트웨이는 GET만) ·
  경로 정규화(`..`·중복 슬래시·퍼센트 인코딩) 뒤 대조 · 쿼리 **`token` 키 거부** · 3xx 비추종.
- **거부 테스트 입력**(HTTP 호출 0회로 거부되는지 단언 — `apm_gateway/tests/test_allowlist.py` · v4 통과):

| 분류 | 요청 |
|---|---|
| 비GET | `POST /api-v2/manage/data-server/control` · `POST /api-v2/manage/data-server/db/property/1000/copy` · `POST /api-v2/manage/domain-group` · `PUT /api-v2/manage/domain/put` · `POST /restapi/user/` · `PUT /api-v2/configuration/rdb-export-password-override` · `PUT /api-v2/manage/rule/event/error/1000/ERROR_X/applied` · `POST /api-v2/manual-rdb-export?date=2026-01-01` · `POST /api/domain` |
| ~~민감 GET~~ → **허용(134 W7 · D-296 ①)** | `/api/auth/userlist` · `/restapi/users` · `/restapi/user/1` · `/api-v2/environment-variable/1000` · `/api-v2/active-service/detail/1000/1` · `/api-v2/loaded-class/1000/1` · `/api-v2/manage/data-server/system-property-config` · `/api-v2/manage/rule/event/error/1000`은 이제 허용(자격증명 제거·식별자 가림 뒤 반환) — **거부 유지**: `/api/auth/userlist.xml`(`.xml` 꼬리 일반 거부) · 같은 경로의 PUT·POST·DELETE·PATCH · 형식 밖 경로 변수 |
| 변형·우회 | `/api/domain.xml` · `/api/realtime/instance.xml` · `/api/transaction/time/../../auth/userlist` · 허용 경로 + `?token=…` · 3xx 리다이렉트 응답 |

- **v3.3 로컬 실측**(5.7.0.1 · 87 §0.10): 쿼리 `?token=` 200 · 민감 GET 200(`/api/auth/userlist` → `email`·`phoneNumber` · `/restapi/users` → `allowIp`·`password` 키) ·
  쓰기·관리 경로가 Open API 토큰으로 라우팅(GET으로 400·405·500 · 일부 200) · `POST /api/domain`·`/api/domain.xml` 200. **서버는 위 거부 사례를 하나도 막지 않는다** —
  거부는 전부 게이트웨이 몫이다. 요청 `Accept`는 경로별로 고정한다(`profile.txt` = `text/plain` · 그 밖 = `application/json`).
- **v4.1 실서버 확인**(로컬 5.7.0.1 · 87 §0.12): 위 거부 입력(비GET 9 · 민감 GET 9 · 변형 10 · 쿼리 `token`/`TOKEN` · 허용 밖 키 · 필수 키 누락 2) 34건을 게이트웨이
  클라이언트로 보내는 동안 로컬 제니퍼의 토큰 `usageCount`가 **한 번도 늘지 않았다**(반영 지연 대기 뒤 Δ=0) — 서버에 닿기 전에 막힌다는 실측이다. 허용 경로 16개는
  필수 키로 불렀을 때 계약 위반(`Required request parameter`) 0건이었고, 결과는 200(`domain`·`realtime/instance`·`metrics`·`transaction/time`) 또는 도메인 미접속 500뿐이었다.
- ~~`GET /api-v2/manage/rule/event/…`·`GET /api-v2/manage/instance…`는 J0 수동 채집 전용~~ — **134 W7(2026-10-06)에서 허용목록에 넣었다**(D-296 ① · 위 표). 허용목록은 37템플릿이다(130 W2에서 `/api/business` 추가 — 업무명 해석 · §6.4).

경로 이름과 필수 파라미터는 **정본 스펙 5.6.4로 대조를 마쳤다**([J-23] — 실 서버 호출 0회). 실응답과의 차이는 J0-L(로컬)·J0-O(운영)에서 recorded JSON으로 확인한다. 87의 진단 조회는 전부 v1(`/api/*`)이고, v1은
*"not removed for compatibility, but are no longer maintained"* 상태다 [J-4] — 필드가 바뀔 수 있으므로 recorded JSON 계약 테스트로
고정한다(87 R-12).

### 5.4 조치(대응·복구)는 권고만 【현재 가능 — 불변식】

- **LLM 평면에는 실행 도구가 없다.** `apm_*`는 전부 조회다. 조사 브리핑의 권고에는 *"※ 실행은 운영자 승인 후 수동 — 시스템은 제안만(자동
  실행 경로 없음)"* 문구가 강제된다(`sre_agent/sre_agent/application/briefing_builder.py:36` `HUMAN_GATED_NOTE` · D-189 불변식).
- 제니퍼의 강제 GC API(도메인 단위)는 허용목록 **밖**이다. 도메인 전 인스턴스가 대상이라 단일 인스턴스 원칙에도 어긋난다(87 §2.5).
- 승인 후 실행(L2)은 87 J6이다. **G-6·G-7이 확정됐다(2026-09-29 · D-195 ③)** — 이 조치(덤프 채취 · PLC 상한 조정 · 인스턴스 재기동)는 WAS 조치이고 DB 쓰기가 없어 **D-003의 범위 밖이며 예외가 아니다**(2026-09-29 사용자 확정 "권고"). 실행 평면은 **D-195 ③**이 별도로 통제하고, 착수는 **J3 완료 + 목업 검증 뒤**다.
  초기 카탈로그 3종(힙/스레드 덤프 채취 low · PLC 상한 임시 하향·원복 medium · 인스턴스 재기동 high·이중 승인·단일 인스턴스) · 승인된 제안 id ·
  대상 1 인스턴스 · 정책 파일 · 트랜잭션 검증-롤백 · LLM 미탑재 실행기 `remediation/`(게이트웨이 밖 · 실행 자격증명 격리). **조사 평면(게이트웨이·
  `mcp_server`·`sre_agent`)은 계속 읽기 전용**이고 DB 쓰기는 없다. L3(자율)은 범위 밖이고, 실행 채널은 D-189 read-only 허용목록과 별개 경계다.

### 5.5 불신 데이터와 개인정보 【현재 가능 — v4 · `application/masking.py` · 규칙 확정은 J0-O】

- 제니퍼 응답(`running_text`·`message`·프로파일·SQL·URL 파라미터)은 **불신 데이터**다. 지시문이 섞여 있을 수 있다. 게이트웨이가 상위 N으로 줄이고
  마스킹한 뒤에만 MCP 결과로 내보낸다. 원문은 감사 로그에도 남기지 않는다.
- 마스킹 대상(v4 구현): `client_ip`(끝 두 옥텟) · URL 쿼리 값 · SQL 문자열·숫자 리터럴 · 자유 텍스트의 이메일·주민번호·휴대폰·IP. 운영 샘플이 없어 넓게 잡았다 —
  규칙은 J0-O 샘플로 `scripts/pii_probe.py`를 돌려 확정하고 `docs/pii_filtering_rules.md`에 반영한다(U-6). FabriX PII 필터 차단 덤프는 `logs/pii_block/`에만 남는다.
- 실 운영 데이터를 외부 LLM(Gemini 등)에 보내지 않는다(D-120).
- **자격증명 제거(`plans/134` W0-B · D-296 ③)** 【현재 가능 — `apm_gateway/apm_gateway/domain/credentials.py`】 — 민감·관리 조회를 열기 전에 경계부터 넣었다. 어댑터가 응답을 파싱한 **직후 모든 경로에 한 번** 적용하므로
  도구 반환·결과 파일·감사·로그·오류 사유는 가린 값만 본다. 계정 객체의 `password` 필드는 키째 지우고, 비밀 패턴 키(붙여 쓴 `PGPASSWORD`·`DB_PW2`·`sshKey` 포함)의 값과
  값 안의 자격증명(URL·JDBC 사용자 정보 · `Authorization`·`Cookie` 헤더 줄 · `-D…password=` · `--password`·붙은 `-p<값>` · Oracle `user/pw@db` 등)을 `[가림]`으로 바꾼다.
  일반 설정값(`PATH`·`JAVA_HOME`)은 가리지 않는다. 규칙 정본은 `spec/SPEC-apm-question-coverage.md` §4.2다. 독립 보안 감사·검증의 우회 입력(카나리아)이 회귀 테스트로 고정돼 있다 —
  다만 패턴 테스트만으로 모든 비밀을 보장했다고 선언하지 않는다(운영 마스킹 녹화본 대조는 W10).
- **개인정보 식별자**(G-11 미결 동안): `userId`·`clientId`·계정 ID·이름은 앞 1자만 남기고 가린다(`mask_identifier`) · HTTP 쿼리 문자열은 첫 값까지 가린다(종전 `mask_url`이 첫 값을 남기던 결함 교정).
  원값을 누가 어디서 보게 할지는 미결(G-11)이다.
- **관리·민감 조회(`plans/134` W7 · 2026-10-06)** — 사용자 목록·계정·환경변수·JVM 속성·데이터 서버 설정·실행 중 요청 상세·룰을 이제 조회한다(§5.3). 보안 감사 뒤 규칙을 넓혔다:
  룰 스크립트 같은 **명령 문맥 칸은 실행 파일만 남기고 인자 전체를 `[가림]`** · 키 판정은 구분자를 걷은 전체 키에도(`APIkey`·`dbPASSword` · `DB_CREDS`·`비밀번호`) · 값만 있는 접속 문자열(`scott/<pw>@ORCL`)·콜론 없는 URL 토큰도 가림 ·
  `,`·`;`에서 끊긴 꼬리를 남기지 않음. 사용자 전화는 `<phone>` · 메일은 `@` 앞만 가림 · 설정 값의 이메일·주민번호·휴대폰은 가리되 서버 IP·경로·포트는 남긴다 · 프로파일 SQL은 SQL 문 칸이면 리터럴만, 그 밖(바인드 값)은 앞 1자만.
  계정 ID가 든 요청 경로는 로그·오류 사유에 템플릿(`/restapi/user/{id}`)으로 남고, httpx 라이브러리 요청 로그는 꺼 둔다(INFO 로그에 URL 원값이 찍히던 것 — 2026-10-06 실측). 남은 모양은 `spec/SPEC-apm-question-coverage.md` §4.2(W10 녹화본으로 판단).

### 5.6 부하 가드와 토큰 사용량 【현재 가능 — v4】

| 가드 | 기본(v4) |
|---|---|
| 호출별 타임아웃 | 10초(`JENNIFER_API_TIMEOUT_SECONDS`) |
| 초당 호출 상한 | 5(`JENNIFER_RATE_LIMIT_PER_SEC` — 게이트웨이 전체 공유) |
| 조사당 `apm_transaction_profile` 호출 | 5(`APM_PROFILE_CALLS_PER_INVESTIGATION` · 칸 = (주체, `investigation_id` → `owner` → 미지정) · 1시간 뒤 만료 · 넘으면 `rate_limited` · **주체 `chat`은 면제** — 134 W5) |
| 트랜잭션 시간 검색 | 1분 창으로 게이트웨이가 분할 · 최대 10분(사건 직전) · 넘는 창은 시 단위 통계로 보충 |
| 조사 동시성 | 현행 `investigation_max_concurrent=2`(`sre_agent/sre_agent/settings.py:109`) |
| 폴링 주기 | 기본 30초 · 하한 10초 · 도메인 미접속 시 백오프(×2 · 최대 ×8) |
| 토큰 사용량 초과 | 빈 결과가 아니라 `{"error": "apm_quota_exceeded"}`(HTTP 429로 잠정 판정 — 실제 초과 응답 모양은 U-5) · 브리핑 `[한계]`에 기재 |
| 사용량 단위(v3.3 로컬 실측) | 요청 1건 = 1 — **500 응답도 센다**. 재시도·미접속 도메인 폴링·`/api/dbmetrics/*` 지표별 호출이 모두 한도를 쓴다 · **v4.1**: 타임아웃 난 요청도 센다 · 401·연결 거부는 세지 않는다 · 콘솔 값은 약 5초 늦게 반영된다 |

폴링·조사·질의·J7이 **토큰 하나를 공유**한다. 게이트웨이 한 프로세스가 모든 호출을 하므로 경로별 호출 예산을 한곳에서 나눠 줄 수 있다 —
폴링이 조사 예산을 잠식하면 안 된다.

---

## 6. 도구 목록과 입출력 계약 【현재 가능 — v4 · 제공 주체 = `apm_gateway` · 계약 정본 `spec/SPEC-apm-gateway.md` §3~§5】

### 6.1 데이터 도구 18종 + 작업 도구 3종 + `gateway_health` — 게이트웨이 MCP 서버가 노출 【`plans/134` W0-B~W2 · W5~W7(2026-10-06)로 갱신 · D-299 ③ — 8종 상한 폐지】

공통 인자: `investigation_id?`·`thread_id?`(감사 레코드에만 싣는다 — R-19). 구간 인자 `reference_time?`(ISO 8601 · naive면 `APM_TIMEZONE`) ·
`lookback_minutes?` — 창은 `[reference_time − lookback, reference_time]`이고 `reference_time`을 빼면 "지금"이다(기존 사건 좌표계와 같다).

**공통 선택 인자(`plans/134` W0-B)** — `owner?`(결과·작업 소유자 · 불투명 문자열)·`wait_seconds?`(이 초 안에 못 끝나면 작업 ID를 돌려주고 백그라운드로 계속 — 생략하면 끝날 때까지 대기). 응답이 인라인 500행을 넘으면 앞 500행 + `artifact`(결과 파일 · 청크) + `total_row_count`. 큰 조회를 `wait_seconds` 없이 부르면 끝날 때까지 기다린다(조사·알람 소비자 종전 의미).

**소스 인자(v5 · J8)** — `apm_transaction_profile`·`apm_active_detail`(단수 `source_id`)과 작업 도구를 뺀 데이터 도구는 선택 인자 `source_ids`(소스 id 목록)를 받는다. 비면 전 소스(설정 선언 순서)이고, 모르는 id는 `invalid_argument`
(사유에 설정된 id 목록)다. 행·`profile_ref`에는 `source_id`가 붙고, 해소 결과에는 `instance_refs[]`(`{source_id, domain_id, instance_id}`)가, 봉투에는 `sources[]`
(`{source_id, status, reason}` — `ok`·`no_match`·`empty`·`unavailable`)가 붙는다. 봉투의 `source_kind`·`source`는 그대로다(소비자 인식 키). 도메인·인스턴스 id는 서버마다
따로 매겨 겹칠 수 있어, 게이트웨이는 인스턴스를 (소스, 도메인, 인스턴스)로 구분하고 호출을 그 소스 서버로만 보낸다.

| 도구 | 인자(값만) | 뒷단 Open API(§5.3) | 반환 핵심 필드 |
|---|---|---|---|
| `apm_instance_map` | `hostname?`·`query?`·`business?`·`source_ids?`·`domain_id?`(`hostname`·`query`·`business`는 셋 중 하나만) | 소스별 `/api/domain` → 도메인별 `/api/instance` · (`business`) 도메인별 `/api/business` + `/api/activeService/list`·`/api/transaction/time`(최근 5분) | `source_id`·`instance_id`·`instance_name`·`domain_id`·`domain_name`·`host_name`·`ip_address`·`platform`·`status`·`agent_version`·`description`·`config_file_path`·`match_confidence`·`match_reason` (hostname 없이 부르면 **전 인스턴스** — 상한 없음 · 큰 목록은 결과 파일 · `domain_id`를 주면 그 도메인 인스턴스만 — 없는 도메인은 행 0 + 「도메인 목록에 없음(있는 도메인 …)」 고지 · `plans/130` W1-D) · `query`(인스턴스 이름·설명 검색)·`business`(업무명 → 인스턴스)는 `match_kind`·`match_tier`·`search_confidence`·`hostname` 칸과 봉투 `search`·`business`·`suggestions`를 더한다(§6.4) |
| `apm_app_health` | `hostname`·`instance_id?`·구간 | `/api/realtime/instance`(대상 `instance_id` · 창 끝이 지금일 때) · `/api/transaction/time`(1분 분할 · **창 전체**) · `/api/status/application`(창 > 10분 — 시 단위 · **전 애플리케이션**) | 평균 응답시간·TPS·액티브·PLC 거절률·동시 사용자·**방문·호출 수**(`visit_day`·`visit_hour`·`hit_day`·`hit_hour` — 단위·하루 경계 미확인 고지)·액티브 구간 4칸 + 행별 `window{…p50, p95…}` + `hourly`(합계 = Σtotal ÷ Σcalls · `top_applications`는 요약 5) + `was_signals` |
| `apm_runtime_health` | 같음 + `metrics?`·`interval_minute?` | `/api/realtime/instance` · `/api/dbmetrics/instance`(기본 지표 3종 · 간격 5 · **정합 인스턴스 전부**) | 힙·GC·CPU·스레드·소켓·파일 + `trend{지표: [{time_ms, value}]}`(카탈로그의 인스턴스 지표 전부 지정 가능 · 모르는 이름은 빼고 후보 ≤3을 `[한계]`) + `was_signals` |
| `apm_resource_pool` | `hostname`·`instance_id?` | `/api/realtime/instance` · `/api/activeService/list` | DB 풀·스레드·실행 모드별·데이터소스별 액티브 + `was_signals` · 현재값 전용 |
| `apm_slow_transactions` | `hostname`·`instance_id?`·구간(기본 10분)·`n?`(기본 10 · 상한 없음)·`full?` | `/api/transaction/time`(1분 분할 · 창 전체) · `/api/status/application`(창 > 10분) | 상위 N(또는 전부): 시간 분해·오류 유형·`guid`·`client_ip`(마스킹)·`user_id`·`client_id`(식별자 가림)·`profile_ref` + `summary` + `was_signals` |
| `apm_active_services` | `hostname`·`instance_id?`·`n?`·`full?` | `/api/activeService/list` | 경과 순: 상태·실행 모드·실행 텍스트(마스킹 전문)·`session_id`·`thread_hash`·`active_ref{source_id, domain_id, txid, session_id, thread_hash}` + `summary` + `was_signals` · 현재값 전용 |
| `apm_events` | `hostname`·구간(기본 30분 · 상한 없음)·`level?`(fatal·warning·normal)·`level_mode?`(min·exact)·`error_type?`·`record?`(event·error)·`n?`(기본 전부)·`full?` | `/api/dbsearch/event` · `/api/dbsearch/error`(`error_type` — 정규화 이름 먼저, 0건이면 `ERROR_`·`WARNING_` 표기 재조회) | 이벤트 행(또는 `record=error`면 오류 기록 행) · 메시지 마스킹 전문 · `errors_by_type`(전 유형) + `was_signals` |
| `apm_transaction_profile` | `hostname`·`source_id`·`domain_id`·`txid`·`time_ms`·`top_k?`(비우면 SQL 전부)·`profile_no?`·`include_param_key?`(134 W5) | `/api/transaction/txid` · `/api/transaction/profile.txt` · `/api/transaction/sql` | 분해 · 화면용 발췌(60줄) · 발췌가 잘리면 **전문은 결과 파일 텍스트**(`artifact.text_parts["profile"]`) · SQL 전부(리터럴 마스킹 · SQL 문 칸이 아닌 문자열은 바인드 값으로 보고 앞 1자만) · **예산**: 채팅 전용 토큰(주체 `chat`)이면 없음 · 조사·단일 토큰은 칸(주체·조사 ID/owner)당 기본 5회/시간 |
| `apm_transaction_trace` | `guid`·`hostname?`·구간·`around_ms?`·`around_minutes?`(기본 5)·`source_ids?` | `/api/transaction/guid`(도메인마다 1회 · 호스트 미지정이면 전 도메인) | 같은 GUID 거래(시작 시각순 `trace_order` · 중복 제거) + `summary` · 「호출 관계 아님」·시계 차이 고지 · 부분 실패 partial |
| `apm_change_impact` | `hostname`·구간(변경 탐색 · 기본 24시간)·`width_minutes?`(기본 60)·`n?`·`full?` | `/api-v2/deploy/{domainId}` + `/api/transaction/time` + `/api/dbsearch/error` | 변경마다 전·후 호출·오류·오류율·평균·p95·오류 기록과 증감(기준 0 = N/A · 오류율 차 %p) · 「원인 확정 아님」 |
| `apm_period_compare` | `hostname`·`current_start/end`·`baseline_start/end`(ISO)·`n?` | `/api/status/application`(인스턴스 × 구간 · 시 단위) | 인스턴스별·전체 호출·실패·실패율·가중 평균 응답(Σ÷호출)·최대 + 증감 · p95 없음 고지 — **조사용**(채팅 배선 없음) |
| `apm_config` | `kind`(event_rules·color_boundary·process_instance·data_server·db_path·loaded_classes·rdb_export)·`hostname?`·`rule_type?`·`target?`·`error_type?`·`process_id?`·`search?` | 이벤트 룰·색상 경계·프로세스→인스턴스·데이터 서버·DB 경로·로드된 클래스·수동 RDB Export(§5.3) | kind별 행(긴 형식 · 표 밖 키는 `extra`) · 자격증명 제거 · 설정 값 개인정보 가림 · 버전 조건 고지 |
| `apm_environment` | `hostname?`·`scope?`(SYSTEM·JAVA)·`key?` | `/api-v2/environment-variable/{d}` | 인스턴스별 긴 형식(`scope`·`name`·`value`) — 키 전부 · 비밀 값 `[가림]` |
| `apm_users` | `user_id?` | `/api/auth/userlist` · `/restapi/users` · `/restapi/user/{id}` | 사용자·계정 행(ID·이름·이메일·휴대폰·허용 IP 가림 · 비밀번호 없음) |
| `apm_active_detail` | `active_ref` 칸(`domain_id`·`txid`·`session_id`·`thread_hash`·`source_id`)·`hostname?` | `/api-v2/active-service/detail/{d}/{txid}` | 실행 중 요청 1건(사용자 ID 가림·GUID·SQL 리터럴 가림·HTTP 메서드·쿼리 값 가림) · 현재값 전용 |
| `apm_status_stats` | `kind`(application·sql·external_call)·`hostname`·`instance_id?`·구간(기본 60분)·`sort_by?`·`n?`·`full?`·`application_name?` | `/api/status/{application,sql,external_call}`(정시 경계 · `max_row`=n · `sort_by_metrics`) | URL·SQL·외부 호출별 시 단위 통계(이름 마스킹 · 25/7필드) + `summary`(평균 = Σ`total_response_ms` ÷ Σ`calls`) · 원천이 정렬 기준을 거부하면 전체를 받아 로컬 정렬 |
| `apm_metrics` | `mode`(catalog·series)·`scope`·`metrics`·`interval_minute?`·대상·구간 | `/api/metrics` · `/api/dbmetrics/instance`(W2 — domain은 W3 · business는 W4) | 카탈로그 행 `{source_id, scope, metric}`(6군 · TTL 캐시·변경 감지) · 시계열 긴 형식 행 |
| `apm_source_changes` | `hostname`·구간(기본 24시간) | `/api-v2/deploy/{domainId}`(25시간 이하 조각) | 변경 감지 행 `change_detected_ms`·`change_detected_at` · `[한계] 변경 감지 — 배포 확정 아님` |
| `apm_job_status`·`apm_job_cancel`·`apm_job_read` | `job_id`·`owner?`·(`read`) `chunk?`·`part?` | 없음(제니퍼 호출 0) | 작업 상태·진행·예측·`result_meta`·미리보기 / 취소 / 결과 파일 청크·텍스트 — **같은 주체 + 같은 `owner`만**(아니면 `job_not_found`) |
| `gateway_health` | 없음 | 소스마다 `/api/domain` 1회(병렬 · 30초 캐시) | **소스별 행**(`source_id`·상태·설정·도달·도메인 수·허용 경로 수(37)·API 호출 수) + 최상위 `status` · `poller` · `jobs`(running·queued·슬롯) |

- **`instance_name?`(`plans/130` W1)** — 표에서 `hostname`을 받는 데이터 도구 13종(`apm_app_health`·`apm_runtime_health`·`apm_resource_pool`·`apm_slow_transactions`·`apm_active_services`·`apm_events`·`apm_transaction_profile`·`apm_status_stats`·`apm_metrics`·`apm_source_changes`·`apm_transaction_trace`·`apm_change_impact`·`apm_period_compare`)과 관리 도구 3종(`apm_config`·`apm_environment`·`apm_active_detail`)은 `hostname` 대신 **정확한 인스턴스 이름** `instance_name`으로도 부른다 — `hostname`은 선택이 됐고 대상이 필요한 도구는 둘 중 하나가 있어야 한다. 부분 이름은 `apm_instance_map(query=…)`로 먼저 찾는다(§6.4).
- 도구 설명문은 벤더 중립이다(`jennifer`·「제니퍼」 없음 — `apm_gateway/tests/test_server.py`).
- **`was_signals`**는 게이트웨이 `domain/signals.py`의 **WAS 시그니처 결정적 판정 결과**다 — `kind`·`level`·`category`·`label`·`evidence`·`instance_id`·`source_tool`·`source_id`(v5).
  kind 8종: `was_service_queuing` · `was_thread_pool_exhaustion` · `was_db_pool_exhaustion` · `was_gc_stall` · `was_heap_pressure` · `was_slow_sql` ·
  `was_external_call_delay` · `was_error_burst`(잠정 임계 `apm_gateway/config/was_signatures.yaml`). `sre_agent`·`noise_gate`는 규칙을 다시 구현하지 않는다(D-274 ⑤ · D-035).
- 지표 식별자 두 체계(realtime camelCase ↔ dbmetrics snake_case)는 `adapters/jennifer/fields.py` `METRIC_FIELDS` 표가 잇는다. TPS처럼 dbmetrics 식별자가 없는 지표는 추세로 조회하지 않는다.
  **v4.1 실측**: 표의 dbmetrics 식별자 13개는 로컬 5.7.0.1 `/api/metrics`의 `instance` 카탈로그(60개)에 모두 있다. 카탈로그에는 `average_db_pool_active_count`·`max_tps`도
  있지만 표는 `db_pool_active`·`tps`의 추세 식별자를 비워 두었다(TPS 쪽은 평균이 아니라 최대값 — 의미 확인은 J0-L-b · 87 §0.12 F-5).
- p95·에러율은 스펙에 백분위 필드가 없어 X-View 1분 분할 결과로 계산한다(nearest-rank · X-View 전수 여부는 U-14).

### 6.2 반환 계약과 오류 계약

```json
{
  "rows": [ ... ],
  "row_count": 2,
  "queried_at": "2026-09-29T10:00:00+09:00",
  "source_kind": "apm_api",
  "source": "jennifer",
  "tool": "apm_runtime_health",
  "instance_resolution": {"matched": true, "confidence": "high", "reason": "host_name", "instances": [1001, 1002],
                          "instance_refs": [{"source_id": "bank", "domain_id": 1000, "instance_id": 1001},
                                            {"source_id": "bank", "domain_id": 1000, "instance_id": 1002}]},
  "window": {"start": "2026-09-29T09:30:00+09:00", "end": "2026-09-29T10:00:00+09:00", "minutes": 30},
  "was_signals": [{"kind": "was_heap_pressure", "level": "WARNING", "category": "medium", "label": "힙 메모리 압박",
                   "evidence": "heap 사용률 ≥ 0.9 연속 3샘플(최근 0.99)", "instance_id": 1001, "source_tool": "apm_runtime_health",
                   "source_id": "bank"}],
  "limits": ["[한계] ..."],
  "sources": [{"source_id": "bank", "status": "ok", "reason": ""},
              {"source_id": "common", "status": "no_match", "reason": ""}]
}
```

오류는 예외가 아니라 `{"error": "<code>", "reason": "<마스킹된 설명>", "source_kind": "apm_api", "source": "jennifer", "tool": "<name>"}`다.

| `error` | 뜻 | 호출자가 할 일 |
|---|---|---|
| `not_configured` | 소스 0개(`JENNIFER_API_URL`·`JENNIFER_SOURCES` 모두 미설정) | §4.2 |
| `invalid_argument` | 인자 오류(`hostname`·`instance_name` 둘 다 빈 값(130 — 「hostname 또는 instance_name이 필요하다(둘 다 비어 있음)」) · (130) `hostname`+`query`·`business` 조합 · 검색어가 비었거나 200자 초과 · `profile_ref` 누락 · `n < 1` · 미지 `level` · (v5) 모르는 `source_ids` · 소스가 둘 이상인데 `source_id` 없음 · (134) `wait_seconds` 음수·NaN · 모르는 지표만 준 series · 접두만 있는 `error_type` · 청크 범위 밖) | 인자를 고친다 · 소스 목록·지표 후보는 사유에 있다 |
| `instance_unresolved` | hostname에 대응하는 인스턴스가 없다 · (130) `instance_name`과 정확히 같은 인스턴스가 없다(「… — apm_instance_map(query=…)로 검색」) · `instance_id`·`hostname`과의 교집합이 비었다 | 정합 파일 확인(§4.5) — 상관 보류 · 이름이면 `apm_instance_map(query=…)`로 후보를 찾는다(§6.4) |
| `profile_ref_mismatch` | `apm_transaction_profile`의 (`source_id`, `domain_id`)가 그 소스에서의 hostname 정합 도메인이 아님 | 앞 도구의 `profile_ref`를 그대로 넘겼는지 확인 |
| `source_unavailable` | 제니퍼 본문 *"… Domain is not connected"*(HTTP 500) · 도메인 0건 · 연결 실패 · timeout · (v5) 고른 소스 전부 실패(원인 코드가 섞일 때) | `[한계]`에 사유 — 빈 결과로 삼키지 않는다 |
| `contract_violation` | 제니퍼 본문 *"Required request parameter …"*·*"Cannot parse null string"* | **게이트웨이 버그** — 재시도하지 않는다(경고 로그) |
| `apm_quota_exceeded` | HTTP 429(초과 응답의 실제 모양은 U-5 — 잠정) | 사용량 협의 · `[한계]` |
| `apm_api_error` | 그 밖의 비200 · 리다이렉트(비추종) · 파싱 실패 · 응답 모양 위반(빈 결과로 강등하지 않음) — (134) 응답 크기는 더 이상 오류가 아니다(메모리 임계 넘으면 임시 파일로 받는다) | §11 |
| `job_not_found` | (134) 작업이 없거나 남의 작업이거나 보관 기간(24시간)이 지났다 — 존재 여부를 드러내지 않는다 | 다시 요청 |
| `job_not_ready` | (134) 아직 끝나지 않은 작업의 결과를 읽으려 했다 | `apm_job_status`로 진행을 본다 |
| `rate_limited` | 조사당 프로파일 호출 상한 초과 | 다른 증거로 판단 |

- **침묵 폴백 금지**: 일부 도메인·일부 호출 실패, 창 상한, 과거 기준시각(실시간 스냅샷 생략)은 `limits`에 `[한계]`로 적고, 쓸 데이터가 하나도 없으면 오류를 돌려준다.
- **부분 실패(v5 · D-287 ⑦)**: 한 소스가 실패하면 그 소스만 빠지고 `[한계] APM 소스 <id> 조회 불가(<code>) — 그 소스의 결과는 빠졌다`와 `sources[].status`로 드러난다.
  고른 소스가 전부 실패하거나 전부 도메인 0건이면 오류다 — 원인 코드가 모두 같으면 그 코드(소스 1개면 v4와 같은 코드·사유), 섞이면 `source_unavailable`. 소스가 하나면
  `[한계]` 문구는 v4 그대로(소스 표기 없음)이고, 둘 이상이면 위치에 `소스 <id> · 도메인 <n>`이 들어간다.
- 제니퍼 오류는 HTTP 코드로 가를 수 없다(도메인 미접속·필수 파라미터 누락이 모두 500 — 로컬 실측). 게이트웨이는 v1 JSON `exception.message`·v2 문자열 본문으로 가른다.
  라이선스·에이전트가 없어도 일부 경로는 **200 + 빈 결과**를 준다 — 도메인이 0건이면 `source_unavailable`로 돌려준다(87 §0.10 #18).
- 반환 형태는 폴스타 도구 계약을 **복제**했다(import 없음 — 소비자는 이 계약을 각자 복제한 픽스처로 테스트한다).

### 6.3 인접 도구 — 폴스타 MCP 도구 【현재 가능 — `mcp_server` 제공 · WAS 호스트의 OS 관점 · APM 미가용 시 폴백】

게이트웨이가 없거나 미가용이면 조사는 이 도구로 WAS가 도는 **호스트**를 본다(D-233 — 셸 없음). 앱 계층(트랜잭션·힙·스레드)은 볼 수 없다.

| 도구 | 무엇을 | 식별자 |
|---|---|---|
| `polestar_process_snapshot` | 실시간 상위 프로세스(WAS JVM 프로세스의 CPU·메모리 점유) | OS hostname — `PROCESS_API_BASE_URL` 필요 |
| `polestar_os_config` | OS 구성 | OS hostname |
| `polestar_resource_status` · `polestar_metric_trend` | 자원 현황 · CPU·메모리·파일시스템 추세 | `server_name` |
| `polestar_alarm_history` · `polestar_incident_alarms` | 알람 이력·사건 구간 알람 | `server_name` |
| `prom_metric_instant` · `prom_metric_range` | PromQL 현재값·시계열(`PROMETHEUS_URL` 필요 — 현재 운영 공란) | `hostname`(값은 `server_name`) |
| `om_metric_instant` · `om_metric_catalog` | exporter 현재값(옵트인) | `hostname`(값은 `server_name`) |

도구별 식별자가 다르다(D-046 — 공동존은 `server_name` ≠ OS hostname). 섞으면 0건이 된다.

### 6.4 인스턴스 이름·업무명으로 조회 【현재 가능 — `plans/130` W1~W4(2026-10-06) · 게이트웨이 계약 `spec/SPEC-apm-gateway.md` §3.4 · 본체 `spec/SPEC-apm-question-coverage.md` §7.8】

hostname을 모르는 사용자가 「abc-was 응답시간」·「결제 업무 WAS 힙 사용률」처럼 **제니퍼 인스턴스 이름이나 업무명으로** 묻는 경우다(이 절의 이름은 모두 가상). 정합(이름 → 인스턴스)은
게이트웨이 한 곳에서 한다. 새 도구·새 플래그는 없다 — 새 인자를 주지 않으면 결과가 종전과 같다(D-299 ③). 찾은 인스턴스 수와 후보 수에 상한이 없다(D-296 ④).

#### 6.4.1 무엇을 알고 있나 → 어떻게 부르나

| 아는 것 | 부르는 법 | 결과 |
|---|---|---|
| 정확한 인스턴스 이름 | 데이터 도구에 `instance_name="abc-was-01"`(hostname 대신 · §6.1 표 아래 목록 16종) | **정확 일치만**(앞뒤 공백 제거 · 대소문자 무시). 여러 소스·도메인에 같은 이름이 있으면 모두. `hostname`과 함께 주면 AND |
| 이름 일부·설명 | `apm_instance_map(query="abc-was")` → 행의 `instance_name`·`source_id`·`instance_id`로 데이터 도구를 다시 부른다 | 아래 검색 단계 |
| 업무명 | `apm_instance_map(business="결제")` | 아래 업무명 근거 |

`hostname`·`query`·`business`는 셋 중 하나만 준다(함께 주면 `invalid_argument`). `domain_id`·`source_ids`와는 AND다. 검색어는 앞뒤 공백을 지운 뒤 1~200자다.

#### 6.4.2 인스턴스 이름 검색 단계 (`query`)

결과가 있는 **첫 단계만** 쓴다(앞 단계에 맞는 인스턴스가 있으면 뒤 단계의 느슨한 일치는 섞지 않는다). 검색은 캐시한 인스턴스 명단에서만 한다 — 제니퍼 호출이 늘지 않는다.

| 단계 | 일치 | 예(검색어 `abc-was`) | 신뢰도 |
|---|---|---|---|
| `exact` | 이름이 같다(대소문자 무시) | `ABC-WAS` | high |
| `normalized` | `-`·`_`·`.`·공백을 빼고 같다 | `abc_was`·`abcwas` | high |
| `prefix` | 이름이 검색어로 시작하고 바로 뒤가 끝이거나 구분자 | `abc-was-01`·`abc-was_02` | medium |
| `contains` | 구분자를 뺀 검색어가 3자 이상일 때만 — 이름에 들어 있거나 **가린** 설명에 들어 있다 | `new-abc-was`·설명에 「abc-was 이중화」 | medium |

행에는 `match_kind="instance_name"`·`match_tier`(단계)·`search_confidence`·`hostname`(역정합 — 없으면 빈 값)이 붙고, 봉투 `search`에 채택 단계와 단계별 인스턴스 수가 실린다(감사 꼬리 `search=prefix(exact:0,normalized:0,prefix:2,contains:0)`).

#### 6.4.3 업무명 근거 (`business` · G-3 ①)

| 근거 | 무엇을 보나 | 비고 |
|---|---|---|
| **B0 업무 수동 매핑** | 정합 파일 `business_map`의 업무명·별칭이 같으면 그 항목의 인스턴스 이름(정확 일치)만 | **B0가 맞으면 B0만 쓴다**(다른 근거 호출 없음 · high). 파일에 적었는데 명단에 없는 이름은 `[한계]` 1줄 — 작성법은 §6.4.6 |
| B1 제니퍼 도메인 이름 | 도메인 이름이 업무명과 같다(대소문자·구분자 무시) → 그 도메인의 전 인스턴스 | medium |
| B2 제니퍼 업무 정의 | 도메인마다 `GET /api/business`의 업무 이름·(가린) 설명에 업무명이 들어 있으면, 그 업무를 지금 처리 중(액티브 서비스)이거나 **최근 5분** 처리한(트랜잭션) 인스턴스 | medium · `[한계] 업무 정의 근거는 최근 처리한 인스턴스만 찾는다` — 한동안 그 업무를 처리하지 않은 인스턴스는 빠진다 |
| B3 인스턴스 이름·설명 | §6.4.2 검색을 업무명으로 | medium |

B0가 없으면 B1~B3의 **합집합**이다. 인스턴스마다 `match_kind`(첫 근거)·`match_kinds`(근거 전부)·`business_names`(B2로 맞은 업무 이름)가 붙고, 봉투 `business.counts`에 근거별 인스턴스 수가 실린다.
B2 조회가 일부 실패하면 그 단위만 `[한계]`·부분 결과이고 B1·B3 결과는 그대로 온다.

**채팅에서는 폴스타 근거가 하나 더 붙는다(E6)** — 본체가 게이트웨이 업무명 해석과 **동시에** 폴스타 서버 등록명·비고에서 업무명을 찾고(사용자가 권한을 가진 DB만 · 2자 이상), 그 서버의
hostname으로 다시 `apm_instance_map(hostname=…)`을 불러(E1r) 인스턴스를 얻는다. 두 결과를 합치고 근거를 함께 알려 준다. 폴스타를 조회하지 못하면 근거에서만 빠진다.

#### 6.4.4 찾지 못했을 때 — 대신 조회하지 않는다 (D-290 ⑥)

- 게이트웨이: 행 0 + `[한계]` 「인스턴스 이름 '…'과(와) 일치하는 인스턴스를 찾지 못했습니다」(업무명이면 「업무명 '…'에 해당하는 APM 인스턴스를 찾지 못했습니다」) + 봉투 `suggestions`
  — 구분자를 뺀 이름이 80% 이상 비슷한 인스턴스 **최대 3개**(`instance_name`·`source_id`·`domain_id`). 후보는 **자동으로 고르지 않는다**.
- `instance_name`이 0건이면 `instance_unresolved`(「… — apm_instance_map(query=…)로 검색」)다. 부분 이름으로 여러 인스턴스를 부르지 않는다.
- 채팅 답: 「'abc-wsa'에 해당하는 제니퍼 인스턴스를 찾지 못해 조회하지 않았습니다(검색: 제니퍼 인스턴스 이름·설명 · 제니퍼 업무명 · 폴스타 등록명·비고). 다른 인스턴스로 대신 조회하지 않았습니다.
  비슷한 이름: abc-was-01 · abc-was-02 — 자동으로 고르지 않았습니다.」 — 사용자가 후보 이름으로 다시 묻는다.

#### 6.4.5 채팅 흐름 (2단 `apm_query`)

1. 분해 LLM이 `apm_query` task에 `targets: [{"text": "abc-was", "kind": "instance"}]`를 싣는다(`kind` = `instance`·`business`·`auto` · hostname·IP는 넣지 않는다 · APM 활성 배포에서만 프롬프트에 이 안내가 있다).
   폴스타 등록명으로 hostname을 잇지 못한 서버 이름(연결 없음·조회 안 함·여러 hostname)도 `auto` 대상으로 더한다.
2. `instance`·`auto` → 인스턴스 이름 검색. `business`, 또는 `auto`인데 0건 → 게이트웨이 업무명 해석 ∥ 폴스타 E6 → E1r. 근거마다 따로 시도한다(하나가 실패해도 계속 · 실패는 `apm_partial_sources` 고지).
3. 찾은 인스턴스를 **전부** 부른다 — `instance_name` + `source_ids`(+ 인스턴스 id를 받는 도구면 `instance_id`). 인스턴스가 많으면 호출도 많아 느려질 수 있다(장기 작업으로 접수될 수 있다).
4. 대상 이름을 말했으면 **인스턴스 목록 첫 홉을 끼우지 않는다** — 하나도 못 찾았으면 아무 인스턴스도 조회하지 않고 위 「찾지 못함」과 후보가 답이다.
5. 답 머리에 근거 고지: 「'결제' → 제니퍼 인스턴스 3개(근거: 제니퍼 업무 정의 2 · 폴스타 비고 1)」.

#### 6.4.6 `business_map` 작성법 (업무 수동 매핑 · B0)

업무명이 제니퍼 도메인·업무 정의·인스턴스 이름 어디에도 드러나지 않거나, B2(최근 처리 한정)로는 빠지는 인스턴스가 있을 때 쓴다. 적으면 그 업무명은 **이 매핑만** 쓴다(다른 근거와 합치지 않는다).

1. 게이트웨이 정책 파일 `apm_gateway/config/instance_map.yaml`의 `business_map`에 항목을 넣는다(루트 `config/`가 아니다).

   ```yaml
   business_map:
     - business: "결제"                       # 필수 — 업무명(대소문자·구분자 무시로 비교)
       aliases: ["페이", "payment"]           # 선택 — 같은 업무의 다른 이름
       instances: ["abc-was-01", "abc-was-02"]  # 필수 — 제니퍼 인스턴스 이름(정확히 · 대소문자 무시)
       source_id: bank                        # 선택 — 그 제니퍼 소스에서만 찾는다(없으면 전 소스)
   ```

2. **저장소의 기본값은 빈 목록(`business_map: []`)이다** — 운영 업무명·인스턴스 실명은 배포 환경의 파일에만 적는다(수동 `overrides`와 같다). 위 예는 가상 이름이다.
3. 게이트웨이를 재기동한다(정책 파일은 기동 때 한 번 읽는다).
4. 기동 로그를 본다 — 형식이 틀린 항목은 「정합 파일 business_map[N] 형식 오류 — 무시(…)」, 목록이 아니면 「business_map은 목록이어야 한다 — 무시」, 설정에 없는 `source_id`는 「정합 파일이 설정에 없는 소스를 가리킨다(쓰이지 않음)」.
5. 확인: `apm_instance_map(business="결제")` → 행의 `match_kind="business_map"`·`search_confidence="high"`. 명단에 없는 이름은 `[한계] 업무 수동 매핑(business_map)의 인스턴스 이름을 조회한 APM 인벤토리에서 찾지 못했다: …`로 드러난다(오타·폐기 인스턴스 점검).

#### 6.4.7 첫 질의 비용 — 업무명

- 업무 정의(B2)는 도메인마다 `/api/business`를 1회 부르고, 업무가 맞은 도메인은 액티브 서비스 1회 + 최근 5분 트랜잭션(1분 창 5회)을 더 부른다.
- **캐시가 빈 첫 업무명 질의는 도메인 수만큼 호출한다** — 운영처럼 도메인이 약 350개이고 호출 상한이 초당 5회면 수십 초(약 70초)가 걸린다(§4.5 부기와 같은 셈). 장기 작업 승격 판단에 호출 계획을 미리 알린다.
- 업무 정의 목록과 관측 결과는 (소스, 도메인)마다 **10분** 캐시한다 — 그 안의 다른 업무명 질의는 제니퍼 호출이 없다. 관측 결과는 전부 성공했을 때만 캐시한다(일부 실패를 10분 동안 굳히지 않는다).
- 인스턴스 이름 검색(`query`)과 `instance_name`은 인스턴스 명단 캐시만 쓴다 — 추가 호출이 없다.

---

## 7. 소비자별 사용 흐름

### 7.1 `sre_agent` 조사 【현재 가능 — v4 · J3 · 설정 §4.6】

1. `APM_MCP_URL`을 설정하면 조사가 **MCP 서버 둘**(`mcp_server` · `apm_gateway`)의 도구를 함께 발견한다. 헬스체크 도구는 `gateway_health`다.
2. `APM_GUIDANCE_ENABLED=true`면 사건창 앵커에 `apm_app_health`·`apm_runtime_health`·`apm_events`·`apm_slow_transactions`가 더해진다
   (`sre_agent/sre_agent/application/investigation_guidance.py` `APM_ANCHORED_TOOLS`). 현재값 도구(`apm_active_services`·`apm_resource_pool`)는
   "현재 상태로만 서술" 노트를 받는다.
3. 조사 순서 노트(`APM_FOCUS_NOTE_TEMPLATE`) — 대상 확정(`apm_instance_map`) → 선행 이벤트 → 골든 시그널·런타임 → 증상별 분기(큐잉·지연·풀 —
   `profile_ref`의 `source_id`·`domain_id`·`txid`·`time_ms`를 그대로 넘긴다 · v5) → 인프라 대조 → 반증 도구 1회 · `apm_*`에 `investigation_id` 인자. `plans/130`부터 대상 확정 줄에 「이후 `apm_*`는 hostname 대신 정확한 인스턴스 이름(`instance_name`)으로도 부를 수 있다 — 부분 이름은 `apm_instance_map(query=…)`로 먼저 찾는다」가 붙는다(§6.4).
4. **APM 사건**(`resourceType="apm.Instance"` 또는 트리거 `meta.hints.solution == "apm"`)이면 OS 플레이북 대신 **APM 플레이북** 하나만 싣고, 트리거 힌트
   (`event_type`·`source_id`(v5)·`instance_id`·`domain_id`·`txid`)를 한 줄로 붙인다.
5. 판정은 결정적이다 — WAS 시그니처는 **게이트웨이가 판정해 `was_signals`로 넘기고**, `APM_SIGNATURES_ENABLED=true`면 `sre_agent`가
   `Signal(source="apm")`로 승격만 한다(`domain/severity_signatures.py` `was_signals_from_outputs` — 규칙 재구현 없음).
6. 권고는 WAS kind별 표에서 고른다(`domain/remediation.py` — 가역성 순 · 항목마다 **검증 방법·롤백**). 조치는 권고만이다(§5.4).
7. 브리핑 — 증거에 "애플리케이션(APM)"(apm_* 인용)·"인프라" 소스 라벨을 붙이고, `[한계]`에 정합 신뢰도(medium 이하)·APM 미가용 폴백
   (`source_unavailable`·`instance_unresolved`·`not_configured` → 폴스타 MCP 도구로 대체)·게이트웨이 `limits`(10분 상한 등)를 싣는다.
   APM 사건인데 `apm_*` 호출이 0건이면(게이트웨이 헬스체크 실패로 도구가 등록되지 않은 경우 등) 그 사실도 적는다.
8. **정체 가드(P15)** — 같은 도구·같은 인자 호출이 3회 이상이면 조사를 "미결"로 적고 `[한계]`에 사유를 남긴다. holmes 루프를 중간에 끊는 공개 훅이 없어
   **사후 판정**이다(반복 호출이 쓴 step은 되돌리지 못한다 — `domain/investigation_limits.py`).
9. 감사는 두 프로세스(`mcp_server`·게이트웨이)에 나뉜다. 게이트웨이 감사 1줄에 `investigation_id`·`thread_id`가 실리므로(LLM이 인자를 넘겼을 때) 합쳐 볼 수 있다(87 R-19).
10. 결정적 사전수집(`evidence_prefetch.py`)에는 APM 도구를 넣지 않았다(계획서 J3 선택 항목 — LLM이 ReAct로 부른다).

### 7.2 채팅 질의 경로 【현재 가능 — 2단 처리기 `apm_query`(`plans/125` A-3 · `plans/134` W0-B~W2) · 운영 2단 전환 전에는 효력 없음】

- 기준 경로는 사다리 2단 `intent_orchestration`이다(D-251). **운영 `.env`는 아직 1단이 확정되는 상태라**(`docs/21_orchestration_ladder.md`) 채팅 제니퍼 조회는 2단 전환 뒤에 효력이 난다.
  APM 엔드포인트(`MCP_SOURCE_ENDPOINTS`의 `apm`)가 설정된 배포에서만 처리기·분해 프롬프트 줄이 붙는다(비활성 배포 바이트 불변).
- 분해 LLM이 **보기**(`views` — 레지스트리 `solutions[apm].views` 닫힌 어휘 12종)와 **조건**(`view_args` — 개수·전체·레벨·오류 유형·정렬·지표 이름·간격 등)을 고르고, 코드가 형식을 검증한다.
  모르는 조건은 버리고 고지한 채 조회한다. 보기가 요청 영역을 못 덮으면 보기 카탈로그로 **한 번 더** 고르고(LLM 1회), 그래도 못 덮은 부분만 후보를 들어 되묻는다.
- 처리기는 게이트웨이를 **두 번째 MCP 엔드포인트**로 부른다(본체는 제니퍼 URL·토큰을 갖지 않는다). 데이터 도구에 `owner`(`user:<sub>`)와 `wait_seconds`(호출 상한 − 2초 · 조회 마감 이내)를 싣는다.
  오래 걸리는 조회는 **작업으로 접수**하고(데이터 답 아님 — 작업 카드에서 진행·취소·결과 보기·전체 CSV) · 큰 결과는 화면 앞 500행 + 결과 파일이다(§4.2 「장기 작업」).
- 답에는 게이트웨이 판정(`was_signals`)과 집계(구간 p50·p95·오류율 · 시 단위 합계 · 오류 유형별 건수)가 **`**판정·집계**` 블록**으로 그대로 실린다(LLM 산문에 맡기지 않음).
- **인스턴스 이름·업무명 질문(`plans/130`)** — 분해 칸 `targets`로 받아 게이트웨이 검색·업무명 해석과 폴스타 업무명 간선(E6)으로 인스턴스를 찾고, 찾은 인스턴스를 상한 없이 전부 부른다. 못 찾으면 다른 인스턴스로 대신 조회하지 않고 비슷한 이름 후보를 보여 준다(§6.4.4·§6.4.5).
- **권한** — 소스 단위 인가 `allowed_sources`(D-285 ①)를 실행 경계에서 판정한다. 작업 API·결과 파일 다운로드는 질의한 사용자(또는 관리자)만(D-262).
- **한계** — 「하루 넘게 지난 기간」은 아직 조회하지 않는다(`plans/134` W6에서 해상도 선택과 함께 폐지) · 서비스·업무·전 대상 순위·GUID·설정/계정 조회는 W3~W7 잔여 ·
  실 제니퍼 응답 모양은 미검증(W10). 사용자 매뉴얼 U-50·U-51(D-255).

### 7.3 사용자 pull 조사 위임 — 주의 【현재 가능 — 3단 한정】

채팅에서 조사를 위임하는 `fault_diagnosis`는 **3단 `semantic_router` 경로에서만** 배선된다(`src/graph.py:497-501·594-599·728-741`,
`NOISE_FAULT_DIAGNOSIS_ENABLED` on일 때). 2단(기준)·1단(운영)에서는 도달하지 않는다. 알람 → 자동 조사(push, §8)는 사다리와 무관하게 동작한다.
이 공백은 87 소관 밖이다. **소유는 `plans/121`로 지정됐다**(2026-09-29 · D-270 ⑯ 2단 배선 소유 · 121 §14.2 부기 · 87 R-18).

---

## 8. 이벤트 연동 — API 폴링과 어댑터 push

### 8.1 지금 수신할 수 있는 것 【현재 가능】

- `alarm_server`는 **폴스타 형식 JSON 1행**을 TCP 9100으로 받아 Redis Stream `alarm:raw`에 넣는다(`noise_gate/alarm_server/base_receiver.py:38-53`
  · `tcp_receiver.py`). 게이트·조사 트리거가 이 스트림을 소비한다.
- 목업 주입 도구는 `noise_gate/scripts/mock_polestar_events.py`다(`docs/20_plan60_feature_test_guide.md` §8).
- **제니퍼 이벤트를 폴스타 형식으로 흉내 내 수동 주입하는 것은 권하지 않는다.** 정규화와 kind 선판정이 없어 결과가 틀린다(§8.4 R-16).

### 8.2 1단계 — 게이트웨이 API 폴러 【현재 가능 — v4 · `application/poller.py` · `domain/events.py` · `APM_EVENT_POLLER_ENABLED=true`로 켠다】

| 항목 | 내용 |
|---|---|
| 위치 | `apm_gateway/apm_gateway/application/poller.py` — `alarm_server`가 아니다. 게이트웨이 프로세스 안에서 MCP 서버와 같은 이벤트 루프로 돈다 |
| 동작 | 주기(기본 30초 · 하한 10초)마다 **소스 간 병렬 · 소스 안은 도메인별로**(v5) `GET /api/dbsearch/event?domain_id=<id>&start_time=<ms>&end_time=<ms>`를 호출한다. `level` 쿼리는 값 형식이 미정의(U-1)라 **보내지 않고** 받은 뒤 최소 레벨(`APM_EVENT_MIN_LEVEL` · 기본 warning)로 거른다 — 해소 레벨(recovery·clear)은 늘 통과시킨다 |
| 커서 | (소스, 도메인)별 Redis 키 `apm_gateway:poller:cursor:<source_id>:<domain_id>`(v5 — 단일 설정은 `…:cursor:default:<domain_id>` · 운영 발행 이력이 없어 v4 키 `…:cursor:<domain_id>`는 옮기지 않았다). 첫 주기는 `[지금 − 주기, 지금]` · 이후 `[커서, 지금]`을 **경계 포함** 재조회 · 커서는 `지금 − 60초`(늦게 들어온 이벤트용 겹침)까지만 전진하고 뒤로 가지 않는다 |
| 멱등 | 응답에 **`eventId`가 없다** → 합성 멱등 키 sha256(`source_id`(v5)·`domainId`·`instanceId`·`errorType\|metricsName`·`time`·`txid`) — 두 서버의 값이 같은 이벤트도 둘 다 발행된다. Redis `SET NX EX 86400`이 성공한 이벤트만 XADD — 재기동·경계 재조회·같은 ms 여러 건에도 **중복 발행 0**(`apm_gateway/tests/test_poller.py`) |
| 형식 | `alarm_server`와 같은 `{"data": <json>}` 레코드(스트림 기본 `alarm:raw`) — 소비자(게이트·트리거)는 폴스타 알람과 같은 파서로 받는다 |
| 오류 처리 | 도메인 미접속(`source_unavailable`) → **커서 유지** · 백오프(주기 ×2 · 최대 ×8) · 상태 `unavailable` · 계약 위반(`contract_violation`) → 그 도메인 폴링 **중지**(경고 로그 · 게이트웨이 버그) · Redis XADD 실패 → 멱등 키를 지우고 커서를 유지해 다음 주기에 다시 발행. 백오프·중지는 (소스, 도메인)별이고 한 소스의 실패(도메인 목록 실패 포함)가 다른 소스 폴링을 막지 않는다. 상태는 `gateway_health`의 `poller.domains`(`"<source_id>:<domain_id>"`)에 보인다 |
| 호출 경로 | 게이트웨이 안에서 끝난다(토큰 한 곳 — G-4 해소 · D-274 ④) |
| 제니퍼 측 준비 | 없음(Java 코드 0) — Open API 토큰과 네트워크(§3)뿐 |

**정규화(`domain/events.py` `build_alarm_payload` · 계약 정본 `spec/SPEC-apm-gateway.md` §5)**:

| 제니퍼 필드 | `alarm:raw` 필드 | 비고 |
|---|---|---|
| `instanceId`·`instanceName` | `hostname`(정합 역방향 — §4.5) · `serverName`(= hostname, 미해소면 `instanceName`) · `ipAddress`(`Instance.ipAddress`) · `resourceName`(`instanceName`) | 미해소면 `hostname=""` → 트리거가 사유를 남기고 생략 |
| `eventLevel` | `severity`(fatal·critical 3 · warning 2 · normal 1 · recovery·clear 0 · 미지 2) | `apm_gateway/config/event_levels.yaml` · 대소문자 무시 |
| `errorType` 또는(비면) `metricsName` | `alarmName` · `resourceType="apm.Instance"` | 원문 유지 |
| `time` | `alarmTime`(`yyyyMMddHHmmss` · `APM_TIMEZONE`) | 워커 파서 형식 |
| `message`·`value` | `conditionLog`(`message` 마스킹 + `(value=…)`) · `conditions`(`JENNIFER EVENT <level> — <alarmName>`) | |
| (소스) | `dbId="jennifer_<source_id>"`(v5 — 단일 설정 `default`는 `jennifer`) · `source="jennifer"`(항상) · `alarmId="jennifer:<멱등 키 앞 16자>"` · `resourceAncestry="JENNIFER > <source_id> > <domainName> > <instanceName>"`(단일 설정은 `JENNIFER > <domainName> > <instanceName>`) | 소스 배지(§8.4 ②) · 존 판정(§8.4 ⑥) |
| 부가 | `apm{source_id(v5), domain_id, domain_name, instance_id, instance_name, event_type, event_kind, level, value, txid, time_ms, application, match_confidence, match_reason, was_signals, idempotency_key}` | 워커의 `raw_payload.apm` — 게이트 `hints`·조사 플레이북 입력 |

조사 트리거 계약은 `serverName`·`hostname`·`severity`를 필수로 요구한다(`sre_agent/sre_agent/application/investigation_jobs.py:45`). 게이트웨이 테스트는 이 계약과
워커가 읽는 키를 **import 없이 복제**해 단언한다(`apm_gateway/tests/test_poller.py` — R-21).

### 8.3 2단계 — 어댑터 push 【계획 — 조건부 착수 · G-4b】

**착수 조건**: 1단계 폴링의 지연·뷰 서버 부하·토큰 사용량이 **실측으로 문제가 될 때만** 착수한다(G-1 "API 위주" · 87 §5.5). 2단계는 폴링을
**대체**한다(같은 이벤트를 두 경로로 발행하지 않는다). 수신기는 정규화·정합이 있는 **게이트웨이 안에 두는 것을 권고**한다(G-4b 미결).

| 방식 | 제니퍼 측 | 우리 측(권고 위치 = 게이트웨이) | 장점 | 단점 |
|---|---|---|---|---|
| **2-A SNMP trap** | 공식 `event.SNMPAdapter`(5.2.3+)를 **설정만으로** 등록 [J-15] · EVENT 룰 "외부연동" 토글 | UDP trap 수신기 · SNMP 라이브러리 신규 의존(루트 venv 미설치) | Java 코드 0 · 벤더 공식 | 메시지 패턴 필드 한정(기본 time·domain·instance·level·name·value — `txid`·`detailMessage` 없음) → `apm_events` 보강 조회 1회 · community 문자열은 평문 인증 → 수신 포트를 뷰 서버 IP로 제한 |
| **2-B 커스텀 EVENT 어댑터** | 공식 확장 튜토리얼 골격으로 Java/Kotlin 어댑터 빌드·배포 [J-6] | JSON 수신기(목적지는 `alarm_server` 9100이 아니라 게이트웨이) | EventData 전 필드 | 업그레이드마다 호환 확인 · 5.7.0은 javax→jakarta · `extension_allowed_packages` 등록 필요 [J-18] |

선택 기준: 운영 조직이 커스텀 어댑터 배포를 거부하면 2-A, 아니면 필드 완전성 때문에 2-B. 5.7.0 이후 커스텀 어댑터는 등록이 빠지면 **무증상으로
로드되지 않는다** — 배포 후 테스트 이벤트 1건 수신 확인을 체크리스트에 넣는다(87 R-13).

이벤트는 OpenMetrics 범위 밖이다(*"Contrary to metrics, singular events occur at a specific time"* [OM-1]). 이벤트를 게이지로 바꿔 §9 노출에
태우지 않는다.

### 8.4 게이트 통합과 주의점 【현재 가능 — v4 · J4 · `noise_gate`에 남는 부분 · 설정 §4.7】

1. **`app_impact`는 승격 전용**이다(`NOISE_APP_IMPACT_ENABLED=true` + `NOISE_APM_MCP_URL`). 매트릭스 단계에서 DASHBOARD·TICKET으로 판정된
   **폴스타(비 APM) 알람**에 대해서만 게이트웨이 `apm_events(hostname, reference_time=<알람 시각>, lookback_minutes=<사건창>, level="fatal")`를 부르고,
   fatal 이벤트가 1건 이상이면 **PAGE로 올린다**. 억제 단계·SUPPRESS·심각도 3 단락은 건드리지 않는다. **v5 — 알람 존의 제니퍼 소스만 부른다**(`source_ids` · §4.7 — 다른 존의 같은 hostname으로 승격하지 않는다). 게이트웨이 오류·미가용·계약 위반은 판정을 바꾸지 않고
   사유를 로그와 결정 기록(`stage_evidence.app_impact_error`)에 남긴다(`noise_gate/application/nodes/notification_gate.py` · 도메인 규칙 `domain/notification_policy.py` step 9.5).
   근거는 결정 기록의 `app_impact_fatal_events`·`app_impact_event_types`·`app_impact_was_signals`에 남는다. 관제 화면 결정 근거 패널은 이 키들을 「앱 영향 — …」 한글 이름으로 보여 준다(`src/static/js/noise-help.js`).
   게이트웨이가 느리면 대상 알람 1건에 최대 약 7초가 더해진다(호출 5초 · TCP 사전 확인 2초 · 미가용 판정 뒤 30초 동안 재시도 안 함 — 워커는 알람을 하나씩 처리).
   워커가 직렬이라 그동안 뒤에 쌓인 알람도 함께 밀린다 — 켜기 전에 게이트웨이 응답 시간을 먼저 본다. **v4.1 실측**(로컬 게이트웨이 · 도메인 0건): 추가 지연 0.20초(첫 호출) ·
   게이트웨이 다운이면 0.001초 만에 `gateway_unreachable`로 넘어가고 쿨다운 중에는 0초 · Bearer 불일치(401)는 v4.1 당시 `… unhandled errors in a TaskGroup (1 sub-exception)`으로만 남았다 →
   **2026-10-01 수정(87 §0.12 F-4)**: `gateway_error — 게이트웨이 호출 실패(apm_events): HTTP 401 인증 실패 — NOISE_APM_MCP_TOKEN이 게이트웨이의 APM_GATEWAY_BEARER_TOKEN과 같은지 확인`(Bearer 없음도 같음 · 로컬 게이트웨이 실측 · §11).
2. **소스 배지** — 게이트웨이 이벤트(`dbId="jennifer"`·`"jennifer_<id>"`)는 배지가 **「제니퍼」**다. 레지스트리 DB가 아니라서 `db_id` 해석 앞에서 분기한다.
   툴팁은 `제니퍼 — <존 약칭> <제니퍼 도메인>; <dbId>`다(v5 — 예 `제니퍼 — 은행존 운영도메인; jennifer_legacy` · 존은 레지스트리 `sources[]`에서 · 존 없는 소스와 단일 설정은
   v4 그대로 `제니퍼 — <도메인>; jennifer`). hostname 역조회(D-188)가 폴스타 서버를 찾아도 배지는 바뀌지 않는다(`noise_gate/application/server_identity.py`).
3. **알람 kind 분류 충돌(87 R-16) — 해소.** 게이트 `classify_alarm_kind`(`noise_gate/domain/process_rank.py`)와 조사측 동형 함수가 `resourceType="apm.Instance"`를
   OS 키워드보다 **먼저** `apm`으로 판정한다(대칭 · 플래그 무관 — 게이트웨이 이벤트에서만 발현). 결과(U-13 확정안):
   - OS 플레이북·"영향 프로세스" 표·L3 kind 프로파일(`alarm_notifier.py` — 매핑 파일로 apm을 연결해도 끊는다)이 **붙지 않는다**.
   - E6 호스트 보강은 **"호스트 참고(WAS 이벤트 교차 확인)"**로 남는다(`domain/enrichment_profile.py` — host-wide 참고 표 · 원인 판정 아님).

   2026-09-29 전 실측 표(수정 전): `WARNING_JVM_HEAP_MEM_HIGH`·`ERROR_OUTOFMEMORY` → `memory` · `ERROR_JVM_CPU_HIGH_LONGTIME` → `cpu` · `ERROR_PROCESS_DOWN` → `process` —
   지금은 §2.3 전 유형(접두 유무 둘 다)이 `apm`이다(`noise_gate/tests/test_plan87_apm_consumer.py` · `sre_agent/tests/test_apm_consumer.py`).
4. 트리거 페이로드 — APM 이벤트에만 `meta.hints = {solution: "apm", source_id(v5), instance_id, domain_id, event_type, txid}`를 싣는다(`noise_gate/domain/investigation_payload.py`).
   그 밖의 이벤트는 페이로드가 바이트 동일하다. hostname이 미해소(`""`)인 APM 이벤트는 조사 서비스에 보내지 않고 사유(`target_unresolved`)를 남긴다.
5. 플래그 off 비트 동일은 `noise_gate/tests/test_plan60_flags_off_regression.py`가 단언한다.
6. **알람 존 전달 — v5에서 F-7 해소**(87 §0.13 (6) · D-287 ④). 알람 수신 범위(알림그룹 = 존)는 이벤트 `dbId`의 존으로 거른다(`src/api/routes/alarm.py`
   `event_visible_to` · ack·피드백 `_zone_permits`). v4의 `dbId="jennifer"`는 레지스트리 DB가 아니라 존이 없었고, 그래서 **존 일부만 받는 구독자에게 APM 알람이 가지 않고
   ack·피드백도 막혔다**(전 존 구독자·관리자만 봤다). v5는 `src/routing/zones.py` `db_id_to_zone`이 `jennifer_<id>`를 레지스트리 `sources[]`로 풀어 — 은행존 구독자는
   `jennifer_bank`·`jennifer_legacy`, 공동존 구독자는 `jennifer_common` 알람을 받고 ack할 수 있다. 존 없는 소스(단일 설정 `jennifer` · 표에 없는 id · `zone` 빈 값)는 종전대로
   전 존 구독자·관리자만 본다(`tests/test_api/test_plan87_j8_apm_alarm_zone.py`).

---

## 9. OpenMetrics 노출 — 선택 트랙 J7

### 9.1 왜 필요한가

사용자가 정한 표준 연동 규격은 **CNCF OpenMetrics**다(G-2). 제니퍼는 OpenMetrics를 내지 않는다(정본 스펙에 `openmetrics`·`prometheus`·`otlp`
0건 — 87 §0.4 O-3). 그래서 게이트웨이가 API로 받은 **인스턴스 수치 지표만** OpenMetrics 1.0으로 다시 내보낸다. Prometheus·타 수집기가 제니퍼
지표를 벤더 API 없이 가져가게 하는 **추가 출구**다. 조사·질의는 `apm_*`가 정본이다.

### 9.2 지금 있는 노출 기계 — 폴스타 브리지 【현재 가능 — `plans/92` B-2 · `mcp_server`】

J7이 참고할 기계가 `mcp_server`에 이미 있다. 게이트웨이는 이 모듈을 import할 수 없으므로 **복제하거나 공용으로 추출**한다(87 **G-12**).
구성·동작은 같으므로 미리 익혀 둘 수 있다.

```toml
# mcp_server/config.toml
[openmetrics]
expose_polestar_exporter = true     # 또는 mcp_server/.env EXPOSE_POLESTAR_EXPORTER=true
bridge_cache_seconds = 300

[[openmetrics.bridge_sources]]
name = "polestar_cm_gp"             # [[sources]] 이름
zone = "gongjon"
```

```bash
# [CWD=mcp_server/] 기동 후 — 토큰은 MCP_BEARER_TOKEN 값(비었으면 헤더 없이)
curl -sS -H "Authorization: Bearer ${MCP_BEARER_TOKEN}" \
  -H 'Accept: application/openmetrics-text; version=1.0.0' \
  http://127.0.0.1:9099/metrics | tail -5          # 마지막 줄이 "# EOF"
```

- 라우트는 `mcp_server/mcp_server/polestar_exporter.py:74`(`/metrics`) · 등록 `:518-538`. 공유 부품은 `om_exposition.py`(패밀리 헬퍼 · Accept 협상 ·
  TTL 캐시 + single-flight · 수집 실패 시 503 · 종료 정리 · 321행).
- 전송 인증은 `mcp_server` 전체에 걸린 Bearer 미들웨어를 그대로 탄다. `MCP_BEARER_TOKEN`이 비면 **무인증 노출**이다.

### 9.3 형식 버전 주의 — OpenMetrics 2.0 협상 (87 R-17) 【현재 가능 — 확인 방법】

G-2는 **OpenMetrics 1.0 고정**이다(2.0은 Experimental — [OM-2]). 공유 `render_exposition`은 `prometheus_client.exposition.choose_encoder`로
Accept를 협상하는데, prometheus-client 0.26.0은 1.0.0 이상이면 요청 버전을 그대로 되돌린다. **2026-09-29 G-10으로 수정했다** — OpenMetrics 요청
버전을 먼저 1.0.0으로 낮춘 뒤 협상한다(`mcp_server/mcp_server/om_exposition.py` `_cap_openmetrics_version` · 테스트 9건 · `mcp_server` 600 passed).

| 요청 `Accept` | 수정 전 응답 | **수정 후 응답** |
|---|---|---|
| (없음) · `*/*` | `text/plain; version=0.0.4` | 같음 |
| `application/openmetrics-text; version=1.0.0` | `…version=1.0.0` | 같음 |
| `application/openmetrics-text; version=2.0.0`(3.0.0·rc 포함) | `…version=2.0.0` ← G-2와 어긋남 | **`…version=1.0.0`** + `# EOF` |
| `application/openmetrics-text; version=0.0.1` | `text/plain; version=0.0.4` | 같음(상한은 버전을 올려 주지 않는다) |

**장기 실행 중인 `mcp_server`(9099·9097)는 재기동해야 반영된다.** 재기동 전 인스턴스에서는 아래 명령이 여전히 2.0.0을 보인다.

확인 명령(폴스타 브리지 대상):

```bash
curl -sS -D - -o /dev/null -H "Authorization: Bearer ${MCP_BEARER_TOKEN}" \
  -H 'Accept: application/openmetrics-text; version=2.0.0' \
  http://127.0.0.1:9099/metrics | grep -i '^content-type'
```

- 원본은 `plans/92` 소유 코드이고, 수정 사실은 92 §5.1 O5에 부기했다. 게이트웨이는 이 모듈을 복제하므로(G-12 ①) **복제본이 수정을 물려받는다**.
- 잔여: 본체 `/metrics`(`src/observability/metrics.py:76`)도 `choose_encoder`를 직접 불러 같은 2.0 응답이 남는다 — G-10 범위 밖, `plans/92` 소관.
- 수정 전후와 관계없이, 비정상 버전 문자열(`version=abc`)은 라이브러리가 TypeError를 낸다(기존 동작 · 이번 범위 밖).

### 9.4 J7이 낼 지표 — 게이트웨이 `GET /metrics/apm` 【계획 — 87 §5.9 · J7 · 이름은 잠정】

| MetricFamily | 타입·단위 | 원천 필드(`/api/realtime/instance`) | 비고 |
|---|---|---|---|
| `jennifer_instance_info` | info | 도메인·인스턴스 목록 + 정합 결과 | 라벨 `nodename`·`jennifer_domain_id`·`jennifer_instance_id`·`jennifer_instance_name`·`match_confidence` |
| `jennifer_active_services` | gauge | `activeService` | |
| `jennifer_transactions_per_second` | gauge | `tps` | 비율이라 counter가 아니다 |
| `jennifer_response_time_seconds` | gauge · seconds | `responseTime` | ms → 초 변환(ms는 스펙 명시 — 스펙 5.6.4 확인) |
| `jennifer_concurrent_users` | gauge | `concurrentUser` | |
| `jvm_memory_used_bytes` · `jvm_memory_committed_bytes` | gauge · bytes | `heapUsed` · `heapCommitted` | 원천은 **MB** — `_bytes`로 내려면 **×1,048,576 변환 필수**(2²⁰ 추정 · 10⁶/2²⁰ 여부는 「확인 불가 — J0-L」 U-12) · 라벨 `jvm_memory_type="heap"` |
| `jennifer_bridge_up` | gauge | Open API 응답 성공 여부 | 1/0 — **Open API 장애는 503이 아니라 0으로**(수집 함수가 오류를 잡아 상태 gauge로) |
| `jennifer_bridge_truncated` | gauge | 인스턴스 상한 초과 여부 | |

**라벨 규약**
- `nodename` = 폴스타 **`server_name`**(OS hostname 아님). node_exporter·폴스타 브리지와 같은 키로 조인된다. 역해소는 게이트웨이 → `mcp_server` 호출(§4.5).
- 정합이 안 된 인스턴스에는 `nodename`을 붙이지 않는다. `match_confidence="none"`으로만 표시한다(잘못된 조인 방지).
- 트랜잭션명·URL·SQL·txid·클라이언트 IP는 **라벨 금지**(카디널리티·개인정보).
- 샘플에 명시 타임스탬프를 달지 않는다.

**부하** — 스크레이프마다 Open API를 치지 않는다. 캐시 TTL(기본 60초) · 스크레이프당 도메인 1회 호출 · J7 전용 호출 예산(토큰 공유).

**인증** — 게이트웨이 자체 Bearer(`APM_GATEWAY_BEARER_TOKEN` — v4에 있다) 뒤에만 둔다. 무인증 노출 금지.

**수집기 예시** — 「예정」(경로 `/metrics/apm`은 J7에서 확정 · 포트는 게이트웨이 포트 — 기본 9096):

```yaml
# prometheus.yml (예시 · 플레이스홀더)
scrape_configs:
  - job_name: jennifer_bridge
    metrics_path: /metrics/apm
    scrape_interval: 60s
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/apm_gateway.token
    static_configs:
      - targets: ["apm-gateway.example.internal:<게이트웨이 포트>"]
```

같은 지표를 `prom_*`와 `apm_*` 두 경로로 LLM에 주지 않는다(소스 혼동 — D-119 대안 기각 사유).

---

## 10. 검증 절차 — 로컬 목 → 로컬 Docker 제니퍼 → 로컬 MLX → 내부망

네 단계를 순서대로 밟는다. 앞 단계가 통과해야 다음으로 간다. 로컬 Docker 제니퍼를 구하지 못하면 단계 2는 목 Open API 서버로 대신한다(§3.8 — 목 서버는 **지금 있다**).

### 10.1 단계 1 — 로컬 목(mock) · LLM 0회 【현재 가능 — v4】

| 검증 | 방법(v4 테스트) |
|---|---|
| **경계 불변식** | `apm_gateway` ↔ `src`·`noise_gate`·`sre_agent`·`mcp_server` 양방향 import 0(AST) · 계층 방향 · 벤더 리터럴은 `adapters/jennifer/`에만 · 엔트리 1개 · 실행 경로 0 — `apm_gateway/tests/test_boundary.py` · `sre_agent/tests/test_boundary.py`(금지 목록에 `apm_gateway` 추가) |
| **선택 배포** | 게이트웨이 없이 본체·조사·게이트·`mcp_server` 테스트 무회귀 · `APM_MCP_URL` 미설정이면 `_build_mcp_servers()`가 종전 dict와 같음 · 소비자 플래그 off 바이트 동일 |
| 자격증명 격리 | 게이트웨이 설정·env에 폴스타 DB 연결 문자열 0건 · 토큰이 로그·오류·감사·도구 반환에 0회 — `test_boundary.py`·`test_client.py`·`test_tools_contract.py`·`test_server.py` |
| HTTP 동작 | **목 Open API 서버**(`testdata/jennifer/scripts/mock_openapi.py`)를 임시 포트로 띄워 도구 경로 전체를 돈다 · 단위 수준은 `httpx.MockTransport` |
| 계약 | 녹화본(`recorded/local-docker` — 라이선스 없음) → "도메인 0건 = `source_unavailable`" · **스펙 5.6.4 스키마 합성 픽스처**(테스트 임시 디렉터리에만 · 출처 `spec-synthetic`) → 8종 반환 계약·정합·마스킹·판정·오류 — **실데이터 모양은 J0-L-b 녹화 뒤 교체** |
| 읽기 전용 통제 | §5.2(e) 거부 입력 23건 + v1 POST 변형 + 쿼리 `token` → **HTTP 0회** · 3xx 비추종(대상 호출 0) · 크기 상한 · 목 서버 `GET /__mock/hits`에 **허용목록 밖 0건 · 쿼리 token 0건** — `test_allowlist.py`·`test_tools_contract.py` |
| 허용목록 사본 대조 | J0 도구용 사본 `jennifer_catalog.py` ↔ 정본 `adapters/jennifer/allowlist.py`(템플릿·필수/선택 키·Accept) — `test_allowlist.py::test_catalog_copy_matches_canonical` |
| 판정 | 목업 WAS 시나리오 6종(큐잉·DB 풀·GC stall·힙·슬로우 SQL·외부 지연) + 스레드 정체·오류 급증 — 게이트웨이 `test_signals.py` · 조사측 승격·권고 `sre_agent/tests/test_apm_was_scenarios.py` |
| 이벤트 계약 | 게이트웨이 `alarm:raw` 레코드가 워커 키·`REQUIRED_EVENT_FIELDS`를 만족 · 중복 발행 0(재조회·재기동·같은 ms) — `apm_gateway/tests/test_poller.py` · 같은 레코드를 `noise_gate`가 파싱·트리거 — `noise_gate/tests/test_plan87_apm_consumer.py`(계약 복제) |
| kind 분류 | §2.3 전 유형(접두 유무)이 두 분류기에서 `apm` — `noise_gate/tests/test_plan87_apm_consumer.py` · `sre_agent/tests/test_apm_consumer.py` |
| 감사 병합 | 게이트웨이 감사 1줄에 `investigation_id`·`thread_id` — `test_server.py` |
| **다중 소스(v5 · J8)** | 목 서버 **2개**가 같은 `domain_id`·`instance_id`·hostname을 갖는 충돌 경우 — 값이 섞이지 않음 · `source_ids`로 좁히면 다른 서버 호출 0 · `profile_ref`로 다른 소스 호출 0 · 소스 A 토큰이 B 요청에 0회(목 서버 `bearer_fp`) · 거부 입력 소스마다 HTTP 0 · 한 소스 다운 → 나머지 + `[한계]`·`sources[]` · 전부 다운·전부 도메인 0건 → `source_unavailable` · 실패·빈 인벤토리 30초 재조회(F-3) · `overrides[].source_id`·`per_source` · 감사 `sources=` · 폴러 두 소스 같은 값 이벤트 2건·커서 키 분리·재기동 중복 0 · 단일 설정 v4 식별자 — `apm_gateway/tests/test_multi_source.py` · 설정 — `test_config.py` · 레지스트리·존 판정 — `tests/test_routing/test_plan87_j8_registry_sources.py`·`tests/test_api/test_plan87_j8_apm_alarm_zone.py` · 소비측 — `noise_gate/tests/test_plan87_j8_multi_source.py`·`sre_agent/tests/test_plan87_j8_source_id.py` |
| 실기동 스모크 | 게이트웨이 프로세스 + 목 서버 → MCP SSE 클라이언트(`list_tools`·`gateway_health`·`apm_events`) · Bearer 없이 `/sse` 401 · `noise_gate` 클라이언트 → 게이트웨이 `apm_events` · `sre_agent` 승격 함수 ← 게이트웨이 실출력(2026-09-29 수동 확인) · **실서버판은 §10.2(v4.1)** |

```bash
# 게이트웨이 (루트 venv 공유 · 루트 pytest 수집 밖 — 별도 실행)
cd apm_gateway && ../.venv/bin/python -m pytest -q
# 본체 + noise_gate
.venv/bin/python -m pytest
# mcp_server (자체 venv 없음 — 루트 venv)
cd mcp_server && ../.venv/bin/python -m pytest
# sre_agent (자체 venv)
cd sre_agent && .venv/bin/python -m pytest tests -q
# 품질 게이트
python scripts/arch_check.py --ci
python scripts/overfit_check.py --ci
# (옵트인) 로컬 Docker 제니퍼 통합 테스트 — 저장소 밖 로컬 토큰
RUN_DOCKER_IT=1 JENNIFER_IT_URL=http://127.0.0.1:17900 JENNIFER_IT_TOKEN=<로컬 토큰> \
  ../.venv/bin/python -m pytest tests/test_docker_it.py -q     # [CWD=apm_gateway/]
```

- **G-11 판정(J1 · 2026-09-29)**: `arch_check`에는 **넣지 않았다** — 2단 중첩이라 스캐너가 파일 경로(`apm_gateway.apm_gateway.*`)와 import 이름(`apm_gateway.*`)을
  맞추지 못한다(판정 기준 ② 불성립 · 스캐너 변경 필요). 대신 같은 계층 규칙을 `apm_gateway/tests/test_boundary.py`가 AST로 검사한다. `overfit_check`에는
  **넣었다**(`apm_gateway/apm_gateway` 스캔 · `adapters/jennifer/` 제외 — 신규 유입 0 · 기준선 무변경). `apm_gateway/tests`는 루트 수집에 넣지 않고
  `sre_agent`·`mcp_server`처럼 별도로 돈다(루트에서 `apm_gateway/tests`를 직접 지정해도 된다 — conftest가 경로를 맞춘다).

### 10.2 단계 2 — 로컬 Docker 제니퍼 (J0-L) 【J0-L-a 현재 가능 · J0-L-b 계획 — §3.8 · 87 §0.10】

| 검증 | 방법 |
|---|---|
| 환경 통제 | 게시 포트는 127.0.0.1만(17900·18080 제안) · 5000 비게시 · 점유 포트와 충돌 0 · `RUN_DOCKER_IT=1` 없이는 통합 테스트 skip · 설치본·라이선스 파일 저장소 0건 · **v3.3 충족**(17900·18080 · `.gitignore` 3패턴 + `.env` — `git check-ignore` 확인) · `RUN_DOCKER_IT` 통합 테스트 **2 passed(v4.1)** |
| 기동(J0-L-a) | 라이선스 없이 서버 기동 · 최고관리자 생성 · `GET /api/domain` · 에이전트 접속 거부 로그 확인(**에이전트 로그**) · arm64 네이티브 결과 기록 · ~~Bootstrap Check off(로컬 한정)~~ Bootstrap Check 기본 on — **v3.3 완료(2026-09-29)**: arm64 네이티브 · 관리자·토큰은 `bootstrap_local.py` |
| Open API 1차 프로브(J0-L-a · v3.3) | `probe_openapi.py` 40건 — 인증 · 허용 경로 · 필수 파라미터 · 민감 GET · 쓰기 경로 라우팅(GET으로만) · 변형 → 결과는 87 §0.10 · 원본은 `recorded/raw/`(커밋 제외) |
| 채집(J0-L-b · 2주) | §3.8 체크리스트 D1~D14 완료 · 만료일 기록 · 만료 전 recorded JSON 백업 |
| 응답 형태 · recorded JSON | 로컬 토큰으로 §5.3 경로를 호출해 채집(출처 `local-docker`) → 계약 테스트 픽스처 1차본 · **v3.3 부기**: 녹화 하네스(`record_openapi.py`)로 라이선스 없는 녹화본 21건을 만들었다 — 평가판 적용 뒤(J0-L-b) 재녹화 |
| 쓰기·제어 API 실측 | 정본 스펙 비GET 경로 전수 · 로컬 토큰으로 실제로 열리는지 확인 → 허용목록 차단 테스트 입력(**로컬에서만**) · **v3.3**: GET 라우팅은 J0-L-a에서 확인 — 실제 메서드로 거부되는지는 J0-L-b |
| 이벤트 재현(J0-L-b) | 재현 스크립트 → EVENT 발생 → 게이트웨이 폴러 → **임시 Redis 또는 별도 스트림 키**(§3.8 이벤트 재현 — 공유 6380 `alarm:raw`는 의도한 종단 시험에만) |
| **게이트웨이 실서버 검증(v4.1)** | 아래 절 — 게이트웨이·소비측 58항목 통과(라이선스 없는 범위) |
| (선택) J7 | 로컬 Prometheus 픽스처가 게이트웨이 `/metrics/apm`을 스크레이프 |

**게이트웨이를 로컬 Docker 제니퍼에 붙이기 — 실서버 검증(v4.1)** 【현재 가능 — 라이선스 없는 범위 · 87 §0.12】

```bash
# [CWD=apm_gateway/] 에이전트를 붙인 WAS 컨테이너와 무관한 개발 셸에서만 JENNIFER_*를 쓴다(R-29).
# 로컬 토큰은 저장소 밖 파일에서 셸 환경으로만 읽는다 — apm_gateway/.env에 로컬 토큰을 적지 않는다.
set -a; . /path/outside/repo/jennifer-token.env; set +a        # JENNIFER_API_TOKEN
export JENNIFER_API_URL=http://127.0.0.1:17900                 # 뷰 서버 루트만 — 경로를 붙이면 302 → apm_api_error
export APM_GATEWAY_PORT=19096 APM_GATEWAY_BEARER_TOKEN="$(openssl rand -hex 12)"
# (폴러까지 볼 때) 임시 Redis — 공유 6380은 쓰지 않는다
docker run -d --rm --name apm-it-redis -p 127.0.0.1:16390:6379 redis:7-alpine
export APM_EVENT_POLLER_ENABLED=true REDIS_HOST=127.0.0.1 REDIS_PORT=16390
../.venv/bin/python -m apm_gateway                             # 셸 값이 apm_gateway/.env보다 이긴다(§4.8)
# 정리: 게이트웨이 Ctrl-C → docker rm -f apm-it-redis
```

- 포트 19096·16390은 예시다. 기동 전에 `lsof -nP -iTCP:<포트> -sTCP:LISTEN`으로 비었는지 본다.
- 소비자는 같은 주소·Bearer를 쓴다 — `sre_agent/.env` `APM_MCP_URL=http://127.0.0.1:19096/sse`·`APM_MCP_TOKEN` / 루트 `.env` `NOISE_APM_MCP_URL`·`NOISE_APM_MCP_TOKEN`.

라이선스가 없을 때 **정상 응답**: `gateway_health` = `status: degraded` · `jennifer_reachable: true` · `domain_count: 0` · `allowlist_size: 16` / `apm_*` 8종 = 모두
`{"error": "source_unavailable", "reason": "APM 도메인 0건 — …"}`(빈 결과를 정상으로 보지 않는다) / 폴러 = 발행 0 · 커서·멱등 키 0 · `gateway_health.poller.domains` 빈 객체.

2026-09-29 검증 결과(58항목 통과 · 검증 스크립트는 일회성이라 저장소에 넣지 않았다 — 정본 87 §0.12):

| 묶음 | 확인한 것 |
|---|---|
| 어댑터(24) | 인증(Bearer 200 · 틀린 토큰·토큰 없음 401) · 허용 16경로 계약 위반 0 · 거부 34건 동안 서버 `usageCount` Δ=0 · 허용 호출 8건 = 사용량 8 · 크기 상한 · 초당 상한 · 타임아웃 · 연결 거부 · 실서버 302 비추종 · 실오류 본문 분류(계약 위반·미접속 v1/v2·기타) · 토큰 로그 0회 · 지표 식별자 13/13 |
| 게이트웨이 프로세스(14) | Bearer 없음·틀림 401 · `tools/list` 9종 · `gateway_health` · 8종 계약 JSON · `profile_ref` 누락·`n=0` → `invalid_argument` · 폴러 · 기동 로그 1줄 · 감사 `investigation_id`·`thread_id` · 주기 하한 10초 · 비밀 로그 0회 |
| Docker IT(2) | `RUN_DOCKER_IT=1 … pytest tests/test_docker_it.py` 2 passed |
| `noise_gate`(7) | 게이트 노드 + 실클라이언트: 미가용이면 DASHBOARD·TICKET 유지 + `app_impact_error` · 지연 0.20초 · 틀린 Bearer → `gateway_error` · 다운 → `gateway_unreachable`(쿨다운) |
| `sre_agent`(6) | `_build_mcp_servers` = `apm`만 · holmes가 실게이트웨이를 헬스체크하고 도구 9종 발견(toolset `apm` ENABLED) · 틀린 Bearer → FAILED·도구 0 · 실출력 → `was_signals` 승격 0 · 브리핑 한계 "APM 미가용(…) — 폴스타 MCP 도구로 대체" |
| 녹화·목·한계(5) | 목 서버 상태 40/40 · JSON 모양 38/38 · 재녹화 21건 동일 · 에이전트 거부 로그 · 샘플 앱 부하 뒤에도 데이터 0 |

**이 단계로도 확인하지 못한 것(J0-L-b)** — 실 인스턴스·이벤트·트랜잭션 응답 모양 · `interval_minute` 허용값 · `hostName` 형식 · 이벤트 재현 · 폴러의 실제
발행·멱등 · `was_signals` 실판정 · `app_impact` 실승격 · 실 LLM 조사 완주.

**두 소스판 — 로컬 Docker 1대를 소스 두 개로(v5 · J8)** 【현재 가능 — 라이선스 없는 범위 · 87 §0.14】

로컬 Docker 제니퍼는 1대뿐이라 같은 뷰 서버를 소스 id 두 개(`bank`·`common`)로 등록해 다중 소스 경로(설정·소스별 클라이언트·병렬 인벤토리·부분 실패·감사)를 본다.
토큰이 같으므로 **토큰 교차 검사는 목 서버 2개로** 한다(§10.1 · `test_multi_source.py`).

```bash
# [CWD=apm_gateway/] R-29 — 에이전트를 붙인 WAS와 무관한 개발 셸에서만. 단일 설정 키를 먼저 지운다(동시 설정 = 기동 실패).
unset JENNIFER_API_URL JENNIFER_API_TOKEN
TOK="$(grep '^JENNIFER_API_TOKEN=' /path/outside/repo/jennifer-token.env | cut -d= -f2-)"   # 화면에 출력하지 않는다
export JENNIFER_SOURCES='["bank","common"]'
export JENNIFER_BANK_API_URL=http://127.0.0.1:17900 JENNIFER_BANK_API_TOKEN="$TOK"
export JENNIFER_COMMON_API_URL=http://127.0.0.1:17900 JENNIFER_COMMON_API_TOKEN="$TOK"
unset TOK
export APM_GATEWAY_PORT=19096 APM_GATEWAY_BEARER_TOKEN="$(openssl rand -hex 12)"
# (폴러까지 볼 때) 임시 Redis — 공유 6380은 쓰지 않는다
docker run -d --rm --name apm-it-redis -p 127.0.0.1:16390:6379 redis:7-alpine
export APM_EVENT_POLLER_ENABLED=true REDIS_HOST=127.0.0.1 REDIS_PORT=16390
../.venv/bin/python -m apm_gateway
# 정리: 게이트웨이 Ctrl-C → docker rm -f apm-it-redis

# 두 소스판 통합 테스트(게이트웨이 프로세스 없이 — 같은 로컬 서버를 bank·common으로 등록한다)
RUN_DOCKER_IT=1 JENNIFER_IT_URL=http://127.0.0.1:17900 JENNIFER_IT_TOKEN=<로컬 토큰> \
  ../.venv/bin/python -m pytest tests/test_docker_it.py -q      # 4 passed(단일 2 + 두 소스 2)
```

라이선스가 없을 때 **정상 응답(두 소스)**: `gateway_health` = 행 2개(`bank`·`common` — 둘 다 `jennifer_reachable: true` · `domain_count: 0`) · 최상위 `status: degraded` /
`apm_instance_map` = `{"error": "source_unavailable", "reason": "모든 APM 소스 조회 불가 — bank: source_unavailable: APM 도메인 0건 …; common: …"}` /
`source_ids: ["bank"]`로 좁히면 사유가 v4 문구 그대로(`APM 도메인 0건 — …`) / 폴러 = 발행 0 · 커서·멱등 키 0.

2026-09-30 검증 결과(검증 스크립트는 세션 scratchpad — 저장소 밖 · 정본 87 §0.14):

| 대상 | 확인한 것 |
|---|---|
| 목 서버 2개 + 임시 Redis + 게이트웨이 프로세스(25) | 동시 설정·필수 키 누락 → 기동 실패(종료 코드 2 · 메시지에 토큰 0회) · 기동 로그 소스 id · 도구 9종 · `source_ids` 인자 7종·프로파일 `source_id` · 틀린 Bearer 401 · 헬스 소스별 행 · 같은 id 두 소스 행 4개 · `source_ids=["common"]` → A 호출 0 · 값 분리 · `profile_ref(bank)` → B 호출 0 · `source_id` 없음 → `invalid_argument` · 모르는 id → `invalid_argument`+목록 · `noise_gate` 실클라이언트 `source_ids` 통과 · 폴러 2건(`jennifer_bank`·`jennifer_common`) · `apm.source_id`·`resourceAncestry` · 커서 키 2개 · 재기동 중복 0 · 폴러 상태 키 · 한 소스 다운 → 나머지 + `[한계]` · 헬스 degraded · 감사 `sources=` · DEBUG 로그 토큰·Bearer 0회 · 소스 A 요청에 B 토큰 0회 |
| 로컬 Docker 두 소스 + 게이트웨이 프로세스(10) | 헬스 행 2 · 도달 · degraded(도메인 0) · 전 소스 도메인 0건 → `source_unavailable`(소스별 사유) · 단일 소스로 좁히면 v4 사유 · 폴러 발행 0·키 0 · 로그 비밀 0회 · 좁힌 호출 감사 `sources=bank:1` · 전 소스 호출은 30초 캐시된 소스를 빼고 조회(`sources=common:1`) · **31초 뒤 두 소스 재조회(`sources=bank:1,common:1` — F-3)** |
| `RUN_DOCKER_IT=1` | 4 passed |

### 10.3 단계 3 — 로컬 MLX 실 LLM · 비과금 【현재 가능(도구) / APM 시나리오 실 LLM 완주는 미실행 — v4는 결정적 테스트로 수용】

실 LLM이 필요한 검증(조사 완주·브리핑 인용 판정)은 **로컬 MLX**로 한다(D-240). 두 평면(워커·오케스트레이터)이 모두 `mlx`(127.0.0.1 루프백)면
사용자 승인 없이 실행할 수 있다.

```bash
# ① MLX 서버 (맥북 · 캐시 모델만 · 127.0.0.1) — docs/03_setup_guide.md §7.2
scripts/mlx_server.sh --dry-run
scripts/mlx_server.sh

# ② 두 평면이 모두 mlx로 해석되는지 먼저 확인 — 하나라도 과금 평면이면 멈춘다(.encenv에 Gemini 키가 상존)
python -m scripts.bench --show-env
python -m scripts.bench --preflight

# ③ 본체 live_llm 테스트 — 외부 차단 가드는 유지된다
RUN_LOCAL_LLM=1 pytest <대상 테스트> -m live_llm
```

- 조사 LLM(`sre_agent`)은 OpenAI 호환 3필드(`MODEL`·`API_BASE`·`API_KEY`)로 MLX 서버(`http://127.0.0.1:8080/v1`)를 가리킨다(D-229 — 로컬 MLX로
  검증된 경로). 설정 방법은 `docs/26_sre_agent_guide.md` §5.6(vLLM 절)과 같고 주소만 다르다. `sre_agent`는 **CWD 기준으로 `.env`를 읽는다** —
  `sre_agent/` 안에서 띄운다(§5.3 과금 주의).
- 87 J3 목업 WAS 시나리오 6종(큐잉·DB 풀·GC stall·힙·슬로우 SQL·외부 지연)의 **결정적 판정·승격·권고는 테스트로 있다**(§10.1 — LLM 0회).
  **실 LLM 조사 완주는 아직 돌리지 않았다** — 라이선스 없는 로컬 제니퍼는 도메인 0건이라 `apm_*`가 모두 `source_unavailable`이다(§10.2). J0-L-b에서 실데이터가
  생기면 게이트웨이를 로컬 Docker 제니퍼에 붙이고 샘플 앱 부하로 사건을 만든 뒤 MLX로 완주를 본다(목업 이벤트 생성기 `noise_gate/scripts/mock_polestar_events.py`의
  WAS 확장은 필요할 때).
- **MLX 결과는 로직 확인용**이다. 응답 지연·성능 결론은 내부망 결과로만 낸다.
- 공유 MLX 서버(8080)를 여럿이 동시에 쓰면 메모리 부족으로 `/health`만 200인 좀비가 될 수 있다. 생존은 1토큰 생성으로 판정하고, 남이 띄운 서버는
  종료하지 않는다.
- Gemini 등 **과금 API는 건별 사용자 승인** 없이는 호출하지 않는다(D-127). `RUN_E2E=1`은 그 승인 뒤에만 설정한다.

### 10.4 단계 4 — 내부망 실측 (J0-O) 【계획 — 운영 접근 권한 선행 · 각 Wave 수용】

| 순서 | 할 일 | 합격 기준 |
|---|---|---|
| 1 | §3.6 연결 사전 확인 · §3.7 J0 채집 | U-1~U-13 기록 · recorded JSON(마스킹본) |
| 2 | 게이트웨이를 임시 포트로 기동 · 도구 1종씩 호출 | 반환 계약 일치 · 허용목록 밖 호출 0 · 토큰 비노출 · 뷰 서버 부하 협의 범위 안 |
| 2′ | (v5 · 다중 소스) 소스마다 1~2를 반복 · 게이트웨이 `JENNIFER_SOURCES` id와 루트 레지스트리 `solutions[apm].sources[].id`·존 대조 · 소스별 버전(레거시 4.x 여부 — R-30)·망 도달(R-31) | 모든 소스가 `gateway_health` 행에서 `ok` · 두 설정의 id 집합이 같다(다르면 `app_impact` 존 좁히기가 `invalid_argument` — §4.7) · 소스 간 같은 hostname 목록(R-32 — 있으면 override `source_id`) |
| 3 | 정합 일치율 측정(U-4) · (U-10이 확인되면) `was_object` 브릿지 재판정(v4 미구현 — §4.5) | 미매칭 인스턴스 목록과 정합 파일 예외(override) · 브릿지를 만들면 `mcp_server` 중단 시 강등 사유 노출 |
| 4 | 조사 서비스에 `APM_MCP_URL` 연결 · 실 사건 1건 재조사(운영 조사 LLM — FabriX는 조사 경로 밖이라 사내 vLLM) | 브리핑에 APM 증거가 인용됨 · 게이트웨이 미가용 시 사유 노출 |
| 5 | 폴러(J4) 섀도 — 발행은 별도 스트림 또는 기록만 | 중복 0 · kind 분류 R-16 해소 확인 · 트리거 계약 통과 |
| 6 | (선택) J7 스크레이프 | 1.0 파서 무오류(1.0 요청 시) · `jennifer_bridge_up` · 캐시 TTL 안 호출 증가 0 |

실 운영 데이터는 외부로 나가지 않는다. 결과 보고에 운영 식별자(호스트명·IP·토큰)를 쓰지 않는다.

---

## 11. 트러블슈팅

| 증상 | 확인 | 원인 · 조치 | 단계 |
|---|---|---|---|
| curl 401/403 | 토큰·헤더 형식 | 토큰 재발급 · `Authorization: Bearer <토큰>` 형식 · 사용량 제한 초과 여부 · 권한 없는 도메인 호출(5.6.1+ 도메인 단위 권한) | 【현재 가능】 |
| 무제한 토큰(사용량 제한 0)인데 401 | 뷰 서버 버전 | 5.6.2.10·5.6.2.13 미만의 알려진 결함(v1·api-v2 각각) — 버전 확인 · 제한값을 두는 토큰으로 대체[J-23] | 【현재 가능】 |
| curl 405 | 메서드 | GET 외 메서드를 보냈다 — 사전 확인·게이트웨이 모두 GET만 | 【현재 가능】 |
| HTTPS 연결 실패 · 인증서 CN/SAN 불일치 | 접속 주소 · 뷰 서버 버전 | 5.7.0.1+ **SNI 호스트 검증 기본 on** — IP 대신 인증서의 호스트명으로 접속(§3.5) | 【현재 가능】 |
| 폴링을 오래 돌리면 뷰 서버 부하가 계속 오름 | 뷰 서버 버전 | 5.6.2.18 미만은 토큰 호출마다 **Jetty 세션 누적** — 버전 확인 · 폴링 주기·호출 상한을 보수적으로(U-7)[J-23] | 【현재 가능】 |
| curl 404 | 경로·버전 | 운영 버전 정본 스펙과 경로 대조 · Open API 비활성 옵션(§3.4) | 【현재 가능】 |
| curl 3xx(로그인 페이지) · 게이트웨이 `apm_api_error` "리다이렉트 응답(비추종): HTTP 302" | `JENNIFER_URL` · `JENNIFER_API_URL` | 콘솔 URL이 아니라 Open API 기준 URL인지 확인 — **뷰 서버 루트(스킴·호스트·포트)만** 적는다. 경로를 붙이면(예 `…:7900/x`) 제니퍼가 `/login`으로 302를 준다(v4.1 로컬 실측) · 게이트웨이는 따라가지 않는다 | 【현재 가능】 |
| 연결 타임아웃 | 방화벽 | §3.5 조회 방향(게이트웨이 호스트 → 뷰 서버) 개방 | 【현재 가능】 |
| `/metrics`가 인증 없이 열림 | `MCP_BEARER_TOKEN` | 비어 있으면 무인증이다 — 운영은 반드시 설정 | 【현재 가능】 |
| `/metrics`가 `version=2.0.0`으로 응답 | 프로세스 기동 시각 | 2026-09-29 수정(G-10) **이전에 뜬 인스턴스**다 — 재기동하면 1.0.0으로 응답한다(§9.3) | 【현재 가능】 |
| 에이전트가 붙지 않음(**에이전트 로그** `Cannot create data server session. Rejected by data server. reason=no license found`) | 에이전트 로그(데이터 서버 INFO 로그에는 거부 줄이 없다 — 로컬 실측) · 라이선스 | 라이선스 미등록 또는 IP 불일치 — 평가판 IP(Agent/Server)와 compose 고정 IP 대조 · Docker Desktop IP는 벤더 확인(§3.8) · 못 구하면 목 서버(87 R-23) | 【현재 가능 — 로컬】 |
| 데이터 서버가 뜨지 않음 | 데이터 서버 로그(Bootstrap Check) · 메모리 | 5.7.0.1은 Docker를 경고만 남기고 기동한다(로컬 실측) — 막혔다면 CPU 2·메모리 8GB 기준 미달이 먼저다 · Docker VM 자원을 늘리거나 로컬 한정 `DISABLE_BOOTSTRAP_CHECK=1`(운영 금지) · 디스크 여유(100MB 미만 자동 종료) | 【현재 가능 — 로컬】 |
| 서버 재기동이 `FileAlreadyExistsException … Other server is running`으로 거부 | 볼륨의 `db_view/db.lock`·`db_data/db.lock` | 비정상 종료가 남긴 잠금 파일 — 로컬 entrypoint가 기동 때 지운다(그 볼륨을 다른 프로세스가 쓰지 않을 때만 안전) | 【현재 가능 — 로컬】 |
| 로컬 제니퍼가 매우 느림(Apple Silicon) | 컨테이너 아키텍처(`platform`) | amd64 에뮬레이션 중일 수 있다 — 서버는 arm64 네이티브로 뜬다(로컬 실측 · `JENNIFER_PLATFORM=linux/arm64`) · 기능 검증만 로컬에서, 성능은 내부망(87 R-24) · 로컬 수치로 결론 금지 | 【현재 가능 — 로컬】 |
| WAS가 뜨지 않고 `FATAL ERROR in native method: processing of -javaagent failed` | WAS 로그 앞부분(`Unknown Java version string: 17…`) · 에이전트 버전 | 에이전트가 JDK보다 오래됐다 — 뷰 서버 배포본 5.5.2.5는 JDK 17 불가(로컬 실측). 로컬은 JDK 11 WAS · 운영은 JDK에 맞는 에이전트로(87 R-28) | 【현재 가능】 |
| 에이전트가 `jennifer.conf`와 다른 주소(예: 127.0.0.1:5000)로 접속 | 에이전트 로그 `Agent settings set in system environment` · WAS 환경변수 | 에이전트가 `JENNIFER_*` 환경변수를 설정으로 읽는다 — WAS 환경에서 `JENNIFER_*`를 지운다(87 R-29) | 【현재 가능】 |
| `profile.txt`가 404(HTML 페이지) | 요청 `Accept` | JSON Accept면 404다(로컬 실측) — `Accept: text/plain`(또는 `*/*`) | 【현재 가능】 |
| HTTP 500 `{"exception":{"message":"… Domain is not connected"}}` | 라이선스 · 에이전트 접속 | 도메인에 붙은 에이전트가 없다 — 인스턴스 데이터 API가 모두 이렇게 응답한다(로컬 실측) | 【현재 가능】 |
| HTTP 500 `Required request parameter … is not present` · `Cannot parse null string` | 쿼리 파라미터 | 필수 파라미터 누락(400이 아니라 500) — `domain_id`·`start_time`·`end_time`·`txid`·`time` 확인(§5.3) | 【현재 가능】 |
| `/api/realtime/instance`가 200인데 빈 `result` | 도메인 연결 상태 | 라이선스·에이전트가 없어도 200 + 빈 결과다(로컬 실측) — 정상 0건으로 단정하지 않는다 | 【현재 가능】 |
| 목 서버가 501 `{"mock_error": "fixture 없음: <템플릿> (mode=…)"}` | 목 서버 모드 · `recorded/<source>/` | 그 템플릿의 fixture가 없다 — 특히 `connected` 모드는 `ok` fixture만 쓰는데 지금 녹화본(라이선스 없음)은 `ok`가 4종뿐이다. `fixtures` 모드로 돌리거나 J0-L-b 재녹화 뒤 다시 | 【현재 가능 — 로컬】 |
| 목 서버 `/__mock/events`가 400 | 주입 본문 | JSON 배열이어야 하고 각 이벤트는 EventData 13필드 안 · `time` 필수 | 【현재 가능 — 로컬】 |
| 녹화 하네스가 `NotAllowedError` | 경로·쿼리 | 허용목록 밖 경로 · 쿼리 `token` · 허용 밖 쿼리 키 · 경로 변수 누락 — 네트워크 호출 전에 막은 것이다(정상 동작) | 【현재 가능 — 로컬】 |
| 목 서버·하네스 테스트가 `pytest`에 안 잡힘 | 수집 경로 | `apm_gateway/tests`는 루트 수집 밖 — `.venv/bin/python -m pytest apm_gateway/tests -q` | 【현재 가능】 |
| 로컬 compose 포트 충돌 | `docker compose ps` · 점유 포트 목록(§3.8) | 호스트 포트를 점유 목록 밖으로 · 127.0.0.1 바인딩 확인 | 【현재 가능 — 로컬】 |
| `/metrics` 503 | 서버 로그 "OpenMetrics 노출 수집 실패" | 수집 함수 예외(원천 전면 불가). 부분 실패는 상태 gauge로 나와야 정상 | 【현재 가능】 |
| 설정을 바꿨는데 반영 안 됨 | 프로세스 기동 시각 | 플래그는 기동 시 1회 해석 — 재기동. 장기 실행 인스턴스(9099·9097·9098)는 소유자 확인 후 | 【현재 가능】 |
| 조사가 WAS 원인을 OS 쪽으로만 서술 | 조사 도구 목록 · `sre_agent/.env` | `APM_MCP_URL` 미설정·게이트웨이 미기동·헬스체크 실패면 앱 계층 도구가 없다(그때는 정상) · `APM_GUIDANCE_ENABLED` off면 APM 조사 순서 노트가 없다 · 운영 원격 조사는 셸도 없다(D-233) | 【현재 가능】 |
| 채팅에서 "장애 원인 분석해줘"가 조사로 안 감 | 사다리 단 | `fault_diagnosis`는 3단 전용(§7.3) | 【현재 가능】 |
| 조사에 `apm_*` 도구가 안 보임 | `sre_agent/.env` `APM_MCP_URL` · 게이트웨이 기동 여부 · `gateway_health` | 두 번째 MCP 서버 미등록 또는 게이트웨이 미기동(헬스체크 실패면 holmes가 도구를 등록하지 않는다 — 브리핑 `[한계]`에 「apm_* 호출 0건」) · Bearer 불일치(holmes toolset `apm` 상태가 `FAILED`·도구 0 — v4.1 실측) | 【현재 가능】 |
| 게이트웨이 기동 실패 | 기동 로그 · CWD | `apm_gateway/` 안에서 루트 venv로 기동했는지 · `mcp<2` · `.env` 인라인 주석 · `JENNIFER_DOMAIN_IDS`가 JSON 배열인지 · 포트 9096 점유 | 【현재 가능】 |
| `{"error": "instance_unresolved"}` | `apm_instance_map`(hostname 없이)로 인스턴스·`host_name` 확인 · `apm_gateway/config/instance_map.yaml` | 제니퍼 `hostName`과 폴스타 hostname이 다르면 override 등록 또는 `regex` 규칙 추가 · `was_object` 브릿지는 v4 미구현(§4.5) | 【현재 가능】 |
| `{"error": "apm_quota_exceeded"}` | 토큰 사용량(HTTP 429 기준 잠정 — U-5) | 제한값 협의 · 폴링 주기 늘리기 · `JENNIFER_RATE_LIMIT_PER_SEC` 낮추기 | 【현재 가능】 |
| 허용 경로인데 거부(`contract_violation` — "허용목록 거부") | `adapters/jennifer/allowlist.py` · 쿼리 키 | 게이트웨이 버그 — 도구가 목록 밖 요청을 만들었다(경로별 쿼리 키 포함 · 설정으로는 넓힐 수 없다 — 코드와 사본·테스트를 함께 바꾼다) | 【현재 가능】 |
| 제니퍼 알람이 게이트에 안 들어옴 | `gateway_health`의 `poller`(도메인별 `state`·`reason`) · 폴러 스위치 · Redis 접속 · 스트림 키 | `APM_EVENT_POLLER_ENABLED` · `REDIS_*` · `APM_EVENT_STREAM_KEY`=`alarm:raw` · 최소 레벨(`APM_EVENT_MIN_LEVEL`) · 도메인 `unavailable`(미접속 — 백오프 중) · `stopped`(계약 위반 — 재기동 전까지 중지) | 【현재 가능】 |
| 제니퍼 알람에 OS 메모리 플레이북이 붙음 | kind 분류 | v4에서 해소 — 그래도 보이면 이벤트 `resourceType`이 `apm.Instance`인지(수동 주입 이벤트 등) | 【현재 가능】 |
| 제니퍼 알람 배지가 "폴스타"로 보임 | 이벤트 `dbId`·`source` | v4에서 해소(`dbId="jennifer"`·`"jennifer_<id>"`면 「제니퍼」) — 게이트웨이가 아닌 경로로 들어온 이벤트인지 확인 | 【현재 가능】 |
| **존 구독자에게 제니퍼 알람이 안 옴 · ack가 403**(F-7) | 알람 `dbId` · 레지스트리 `solutions[apm].sources[]` · 사용자 알림그룹 | **v5에서 해소** — `dbId`가 `jennifer_<id>`이고 그 id가 레지스트리 표에 존과 함께 있어야 존 구독자에게 간다. 단일 설정(`dbId="jennifer"`) · 표에 없는 id(서버 로그에 「레지스트리에 없는 apm 소스 — 존 없음으로 다룬다」 id별 1회) · `zone` 빈 값은 존이 없어 전 존 구독자·관리자만 본다 — 게이트웨이를 다중 설정으로 바꾸거나 레지스트리 표에 id·존을 더한다(§4.2·§4.4) | 【현재 가능】 |
| 게이트웨이 기동 실패 `JENNIFER_SOURCES와 단일 설정 키(…)를 함께 쓸 수 없다` · `소스 '…' 필수 키 없음` · `예약어` · `소문자 슬러그` | `apm_gateway/.env` · 셸 환경 | 두 방식 중 하나만(§4.2) — 셸에 남은 `JENNIFER_API_URL`·`JENNIFER_API_TOKEN`도 센다(셸 값이 `.env`보다 이긴다) · 소스마다 `_API_URL`·`_API_TOKEN` · id는 `[a-z][a-z0-9_]{0,15}`(`default`·`api` 금지) | 【현재 가능】 |
| `{"error": "invalid_argument", "reason": "모르는 source_ids […] — 설정된 소스: […]"}` | 게이트웨이 `JENNIFER_SOURCES` ↔ 레지스트리 `sources[].id` | 둘이 다르다 — `noise_gate` `app_impact_error`에 이 사유가 남으면(판정은 그대로) 같은 id로 맞춘다(§4.7) | 【현재 가능】 |
| `apm_transaction_profile`이 `invalid_argument` "소스가 N개라 profile_ref의 source_id가 필요하다" | 호출 인자 | 앞 도구가 준 `profile_ref`를 **통째로**(`source_id` 포함) 넘긴다(§7.1) | 【현재 가능】 |
| 한 소스만 결과가 빠지고 `[한계] APM 소스 <id> 조회 불가(…)` | 그 소스 `gateway_health` 행 · 망 도달 | 그 뷰 서버 미도달·토큰·도메인 0건 — 나머지 소스는 정상 결과다(부분 실패 · D-287 ⑦). 게이트웨이 호스트에서 모든 뷰 서버에 닿지 않으면 87 R-31(G-13 재판정) | 【현재 가능】 |
| 폴링이 같은 이벤트를 두 번 발행 | Redis 키 `apm_gateway:poller:seen:*`(TTL 24시간) | 멱등 키가 Redis에 남는지(다른 DB 번호 · 플러시) · 같은 게이트웨이를 두 개 띄우지 않았는지 | 【현재 가능】 |
| 한 조사의 감사가 둘로 흩어짐 | 게이트웨이 로그 `apm audit: … investigation_id=` | 정상 구조(R-19) — 두 감사 로그를 id로 합쳐 본다(LLM이 인자를 빠뜨리면 `-`) | 【현재 가능】 |
| `app_impact_error`가 `gateway_error — 게이트웨이 호출 실패(apm_events): HTTP 401 인증 실패 — NOISE_APM_MCP_TOKEN이 게이트웨이의 APM_GATEWAY_BEARER_TOKEN과 같은지 확인`(2026-10-01 이전 코드는 `… unhandled errors in a TaskGroup (1 sub-exception)`) | `NOISE_APM_MCP_TOKEN` ↔ `APM_GATEWAY_BEARER_TOKEN` · 게이트웨이 로그 | Bearer 불일치·누락(401)이다 — 두 값을 맞춘다. 사유에 HTTP 상태를 싣도록 고쳤다(87 §0.12 F-4 · 2026-10-01) | 【현재 가능】 |
| 라이선스·에이전트를 붙였는데 `apm_*`가 계속 "APM 도메인 0건"(`gateway_health`는 도메인 수가 보임) | 게이트웨이 버전(코드) | v4.1까지는 빈 인벤토리가 600초 캐시됐다. **v5(J8)부터 실패·빈 인벤토리는 30초만 캐시한다**(F-3 해소 · §4.5) — 30초 넘게 계속되면 다른 원인(도메인 필터 `JENNIFER_DOMAIN_IDS` 등)을 본다 | 【현재 가능】 |
| 제니퍼 콘솔의 토큰 사용량이 호출 직후 안 늘어남 | 몇 초 뒤 다시 확인 | 약 5초 늦게 반영된다(v4.1 로컬 실측) — 반영 뒤에는 요청 수와 같다(타임아웃 요청 포함 · 401·연결 거부 제외) | 【현재 가능】 |
| `profile.txt`가 500 `For input string: "…" under radix 16` 또는 `Range [0, 8) out of bounds` | 쿼리 `key` | 선택 키 `key`는 16진수 8자리 이상만 받는다(v4.1 로컬 실측) — 게이트웨이는 `key`를 보내지 않으므로 수동 호출에서만 생긴다 | 【현재 가능】 |
| 샘플 앱 첫 호출이 매우 느림(`slow.jsp?ms=1500`이 7초대) | 첫 호출 여부 | JSP 첫 컴파일이다(로컬 실측) — 부하·지연 재현은 워밍업 호출 뒤에 잰다 | 【현재 가능 — 로컬】 |
| SNMP trap이 안 들어옴 | 어댑터 등록 · 포트 | `extension_allowed_packages`(5.7.0+) · EVENT 룰 "외부연동" 토글 · UDP 162 개방(게이트웨이 수신) | 【계획】 |
| 커스텀 어댑터가 무증상으로 미로드 | 뷰 서버 로그 | jakarta 호환 · 패키지 허용 등록 · 테스트 이벤트 1건 확인 | 【계획】 |

---

## 12. 미결 항목과 확정 결과

### 12.1 게이트 확정 결과 (87 §10 · 2026-09-29 전건 확정)

| # | 확정 내용 |
|---|---|
| G-1 · G-2 · G-8 | (2026-09-17) API 위주 · CNCF OpenMetrics 1.0 · 공식 MCP 미채택 |
| G-3 | `apm_*`(패키지명 `apm_gateway`와 일치) |
| G-4 | 해소(D-274 ④ — 폴러가 게이트웨이 안) |
| G-4b | push는 폴링의 지연·부하·토큰 사용량이 실측으로 문제될 때만 · 수신은 게이트웨이 |
| G-5′ | ⓐ `plans/121` 처리기 계약(TP-9.1·9.2·10.5) 뒤 APM 1급 처리기 |
| G-6 | J3 완료·목업 검증 뒤 착수 · 카탈로그 3종 · LLM 미탑재 `remediation/` + 정책 파일 · **D-195 ③**(D-003 범위 밖 — 예외 아님 · v3.3 재기록) · 조사 평면 읽기 전용 |
| G-7 | 신규 최상위 `remediation/`(게이트웨이와 별개) |
| G-9 | J2 완료 + 소비자(운영 수집기) 확정 · 공유 기계 재사용(복제본) |
| G-10 | 1.0 상한 — **수정 완료(2026-09-29)** · 재기동 필요(§9.3) |
| G-11 | 경계 불변식 테스트 J1 필수 · `overfit_check` 편입(`adapters/jennifer/` 제외) · **`arch_check` 비편입(J1 판정 — 2단 중첩 해석 불가 · 게이트웨이 AST 계층 테스트로 대체)** · 테스트는 루트 수집 밖 별도 실행(§10.1) |
| G-12 | 복제 — G-10 수정을 물려받는다 |

### 12.2 남은 미결 — 외부 전제 · 보류 Wave

| 항목 | 막는 것 | 누가 · 언제 |
|---|---|---|
| **J5 채팅 질의 편입(보류)** | 채팅 APM 답변·배지 · `db_registry` `apm` 등재 | `plans/121` TP-9.1·9.2·10.5 코드가 생긴 뒤(2026-09-29 코드 0) |
| **J6 대응·복구 L2(보류)** | 승인 대기함·`remediation/` | G-6 착수 조건 — J3 완료 **+ 목업 검증(로컬 Docker 재현 · J0-L-b)** 뒤 · 덤프·PLC 실행 채널 확인(U-8) |
| **J7 OpenMetrics 노출(보류)** | `GET /metrics/apm` | G-9 — 소비자(운영 Prometheus 등) 확정 뒤 |
| 정합 ②순위 `was_object` 브릿지 · `polestar_was_instances`(미구현) | 정합 교차 확인 · `ipAddress` 보조 대조 | J0-O에서 U-10(운영 실재·채움률) 확인 뒤 재판정 |
| `CLAUDE.md` 「저장소 지도」·「패키지 경계」 갱신(J1 산출물 · 미반영) | 새 최상위 패키지 등재 | 팀 리드·사용자 반영(설정 파일 변경은 에이전트 지시만으로 하지 않는다 — 초안 패치 준비됨) |
| **J0-O** 운영 실측(U-4·U-10·운영 버전·토큰 정책·PII 실 샘플) | 정합 파일 확정 · 운영 배포 | 제니퍼 운영 접근 권한·테스트 토큰(외부 전제) |
| ~~Open API 경로 대조~~ | ~~§3.6·§5.3·§6.1의 경로·파라미터 확정~~ | **완료(2026-09-29 · v3.2 · [J-23])** — 실응답 차이는 J0-L·J0-O의 U-항목으로 |
| ~~로컬 Docker 제품 사실~~ | ~~J0-L 착수~~ | **완료(2026-09-29 · [J-24])** — 남은 「확인 불가」: Mac 라이선스 IP · 장기 라이선스 · LLM 프록시 포함 여부 · **v3.3** Bootstrap 가상 환경 감지는 실측으로 해소(경고만) · 직접 링크 약관은 사용자 확정(공개 S3)으로 수용 |
| ~~J0-L-a 로컬 실측~~ | ~~라이선스 없는 범위의 응답·통제 확인~~ | **완료(2026-09-29 · 87 §0.10)** — 녹화 하네스·목 서버까지 완료(40건 불일치 0) · **v4.1**: 게이트웨이·소비측 실서버 검증 58항목 통과 · `RUN_DOCKER_IT` 2 passed(§10.2) · 남은 것: J0-L-b 재녹화 · 부하 재현 스크립트 |
| 게이트웨이 개선 후보(v4.1) | ~~빈 인벤토리 600초 캐시(F-3)~~ **v5(J8)에서 해소 — 30초** · `noise_gate` 401 사유 불명확(F-4) · 지표 식별자 보강(F-5 — J0-L-b 실데이터로 판단) | F-4는 사용자 결정 뒤 — 87 §0.12 |
| **다중 소스 — 본체 채팅 쪽(v5 · J8 범위 밖)** | 위치어 → 소스 좁히기(G-16) · 승계 패싯 `apm_source_id` · 첫 홉 `source_id` 보존 | `plans/125`(D-281 — 87은 통지만) |
| **다중 소스 — 소스별 J0-O**(v5) | 소스마다 뷰 서버 버전·16경로 가용(레거시가 4.x면 Open API가 없을 수 있다 — R-30) · 망 도달(R-31) · 도메인 id · 명명 규칙(`per_source` 값) · 소스 간 같은 hostname(R-32) | 운영 접근 권한 뒤 |
| **사용자 할 일**(§3.8 표 1·2·9 — 3~6은 2026-09-29 완료) | J0-L-b(2주 채집) | 사용자 — 평가판 신청(IP 벤더 확인) · 최신 Java 에이전트(5.6.x) 입수 |

U-1~U-14 가운데 로컬(J0-L)에서 풀 수 있는 것과 운영(J0-O)에서만 풀 수 있는 것은 §3.7 마지막 열에 나눴다. 특히 **U-4(인스턴스 ↔ hostname 일치율)**는
운영에서만 풀리고 설계를 가장 크게 바꾼다.

### 12.3 87 소관 밖이지만 연동에 영향을 주는 것

| 항목 | 영향 | 소유 |
|---|---|---|
| `plans/121` TP-9.1·9.2·10.5(카탈로그·비SQL 등재·처리기) · 두 번째 MCP 엔드포인트 | 채팅 질의 편입(J5)의 선행 | `plans/121`(D-270 ⑯) |
| `allowed_sources`(비DB 소스 인가) | 채팅 질의의 권한 통제 | `plans/121`(D-270 ⑰) |
| 2단·1단의 사용자 pull 조사 위임 공백 | 채팅 → 조사 위임 | **`plans/121`**(2026-09-29 지정 · §14.2 부기 · 87 R-18) |
| 본체 `/metrics`의 OpenMetrics 2.0 협상(공유 기계는 수정 완료) | 본체 메트릭 노출 | `plans/92`(B-1 잔여) |
| 운영 Prometheus URL 공란 | J7 소비자 · `prom_*` 교차 확인 | `plans/92` · `plans/91` |
| `CLAUDE.md` 「저장소 지도」·「패키지 경계」 | 새 최상위 패키지 등재 | 87 J1 산출물 — **미반영**(§12.2) |

---

## 13. 참조

**계획·결정**
- `plans/87-WIP-jennifer-apm-integration.md` — §0.4(API 단일 경로) · §0.5(공식 MCP 미채택) · §0.6(2026-09-29 구현 실측 41항목) ·
  **§0.7(v3 독립 패키지 `apm_gateway/`)** · §2(제니퍼 조사) · §5(아키텍처) · §6(Wave J0~J7 · v3 재정의) · §7.0(v3 파일 배치·키) · §8(안전 통제) ·
  §10(게이트) · §12.3(제니퍼 1차 자료 [J-xx])
- `plans/92-WIP-prometheus-openmetrics-integration.md`(OpenMetrics 노출 기계) · `plans/121-WIP-intent-routine-hierarchical-planning.md`(처리기 계약)
- `plans/55`(멀티소스 로드맵) · `plans/78`(W7 미들웨어) · `plans/101`(ML — `apm_*` 하류 소비자)
- `docs/02_decision.md` — D-003 · D-035 · D-046 · D-118 · D-119(① D-274로 개정) · D-120 · D-122 · D-125 · D-127 · D-139 · D-189 · D-209 ·
  D-229 · D-233 · D-240 · D-251 · D-255 · D-270 · D-272 · **D-274** · **D-195(본문 등재 · 구현 부기 2026-09-29)** · D-003(부기 — L2는 범위 밖 · 예외 아님 · 2026-09-29 재기록)
- `docs/27_prometheus_integration_guide.md`(형식 전례) · `docs/26_sre_agent_guide.md`(조사 서비스 · §5.3 과금 · §5.6 조사 LLM) ·
  `docs/21_orchestration_ladder.md`(실행 경로) · `docs/03_setup_guide.md` §7.2(MLX) · `docs/pii_filtering_rules.md`

**코드 위치(2026-09-29 · 전례와 연결 지점 · 작업 트리에서 수정 중인 파일은 HEAD 기준)**

| 무엇 | 위치 |
|---|---|
| Bearer 미들웨어(게이트웨이가 복제) · 도구 등록 | `mcp_server/mcp_server/server.py:31-88·129-158` |
| 설정 로더 · HTTP 소스 설정 전례 | `mcp_server/mcp_server/config.py:53-66·151-219·348-462` |
| 반환 계약 · REST 도구 전례 | `mcp_server/mcp_server/polestar_tools.py:655-671·930-990` |
| 인증 헤더 · 강제 timeout · 감사 | `mcp_server/mcp_server/promql_tools.py:156-198` |
| 리다이렉트 비추종 · 허용목록 전례 | `mcp_server/mcp_server/openmetrics_tools.py:8-14·79-84` |
| OpenMetrics 노출 기계 · 폴스타 브리지 | `mcp_server/mcp_server/om_exposition.py` · `polestar_exporter.py:74·518-538` |
| 경계 불변식 테스트 전례 | `sre_agent/tests/test_boundary.py` |
| 조사 MCP 서버 등록 · 원격 프로파일 | `sre_agent/sre_agent/interface/mcp_service.py:105-140` · `toolset_profiles.py:211-240` |
| 사건창 앵커 · 플레이북 · kind 분류(조사) | `sre_agent/sre_agent/application/investigation_guidance.py:20-26·93-169` |
| 조사 계약 | `sre_agent/sre_agent/application/investigation_jobs.py:41-45·130-137` |
| `alarm:raw` 생산 형식 · 수신기 기동 | `noise_gate/alarm_server/base_receiver.py:20-58` · `__main__.py:24·40` |
| 게이트 → MCP 서버 클라이언트 전례 | `noise_gate/infrastructure/sre_agent_client.py` |
| kind 분류(게이트) · `app_impact` 자리 · 소스 배지 | `noise_gate/domain/process_rank.py:50-82` · `application/nodes/agentic_enricher.py:279` · `application/server_identity.py:69·163` |
| 본체 → MCP 도구 배관 · MCP 접속 설정 | `src/dbhub/client.py:421-449·510-570` · HEAD `src/config.py:166-174`(`DBHubConfig`) |
| `apm` 솔루션 자리 · 능력 카탈로그 | `config/db_registry.yaml:41-72·74-107` |
| pull 조사 위임 배선(3단) | `src/graph.py:497-501·594-599·728-741` |

**구현 위치(v4 · 작업 트리 · 2026-09-29 — 위 표는 전례와 HEAD 연결 지점, 이 표는 지금 코드)**

| 무엇 | 위치 |
|---|---|
| 게이트웨이 기동 · 설정 로더 | `apm_gateway/apm_gateway/__main__.py` · `config.py`(`load_config`·`load_dotenv`·`describe`) |
| 허용목록 정본 · 클라이언트(오류 분류·크기 상한·초당 상한·토큰 가림) | `adapters/jennifer/allowlist.py`(`ALLOWED`·`check_request`) · `adapters/jennifer/client.py`(`JenniferClient` · `_classify`) |
| 벤더 필드·지표 식별자·이벤트 유형 매핑 · 조회 함수 | `adapters/jennifer/fields.py`(`METRIC_FIELDS`·`EVENT_TYPE_SIGNALS`) · `adapters/jennifer/api.py`(`JenniferApi`) |
| 정합 · 도구 코어 · 마스킹 · 폴러 | `application/resolver.py`(`InstanceResolver` — 소스 하나) · `application/sources.py`(v5 — `SourceSet`·`build_source_set` · 소스 선택·부분 실패) · `application/tools.py`(`ApmTools`) · `application/masking.py` · `application/poller.py`(`EventPoller`) |
| WAS 판정 · 이벤트 정규화 · 오류 어휘 · 소스 id | `domain/signals.py` · `domain/events.py`(`build_alarm_payload`) · `domain/errors.py` · `domain/sources.py`(v5 — id 규칙·`alarm_db_id`) |
| MCP 서버 · Bearer · 감사 | `interface/server.py`(`register_tools`·`StaticBearerAuthMiddleware`·`run_tool`) · `interface/audit.py` |
| 정책 파일 | `apm_gateway/config/instance_map.yaml` · `event_levels.yaml` · `was_signatures.yaml` |
| 조사 쪽 | `sre_agent/sre_agent/interface/mcp_service.py`(`_build_mcp_servers` `"apm"`) · `application/investigation_guidance.py`(`APM_ANCHORED_TOOLS`·`APM_FOCUS_NOTE_TEMPLATE`) · `domain/severity_signatures.py`(`was_signals_from_outputs`) · `domain/investigation_limits.py`(`apm_limitations`·정체 가드) · `domain/remediation.py` · `application/briefing_builder.py` · `settings.py`(`apm_*` 4필드) |
| 레지스트리·존 판정(v5) | `config/db_registry.yaml` `solutions[apm].sources[]` · `src/routing/registry.py`(`SourceSpec`·`sources_of`·`alarm_source`·`_parse_sources`) · `src/routing/zones.py`(`db_id_to_zone`) |
| 게이트 쪽 | `noise_gate/infrastructure/apm_gateway_client.py` · `application/nodes/notification_gate.py`(`_fetch_app_impact` · v5 `apm_source_ids_for`) · `domain/notification_policy.py`(step 9.5) · `domain/process_rank.py`(`is_apm_event`) · `application/server_identity.py`(배지 「제니퍼」) · `domain/investigation_payload.py`(`apm_trigger_hints`) · `src/config.py` `NoiseGateConfig`(4필드) · `src/static/js/noise-help.js`(근거 키 한글 라벨) |
| 테스트 | `apm_gateway/tests/`(9파일 + J0 도구 1) · `sre_agent/tests/test_apm_consumer.py`·`test_apm_was_scenarios.py` · `noise_gate/tests/test_plan87_apm_consumer.py` · `noise_gate/tests/test_plan60_flags_off_regression.py`(섹션 P) |

**제니퍼·표준 1차 자료** — 87 §12.3·§12.4의 [J-1]~[J-24] · [OM-1]~[OM-3] · [W-5]를 그대로 쓴다. **[J-24]**(v3.2)는 Docker 로컬 설치 조사다(공식 이미지 없음 · 샘플 2종 · 공개 설치본 · 평가판 · 설치 가이드 1~4장 · Docker Hub 베이스 이미지 · 데모 서버). **[J-23]**(v3.2)은 Open API 공식 스펙 대조다 —
정본 = `https://raw.githubusercontent.com/jennifersoft/jennifer5-open-api/gh-pages/index.html`(gh-pages 커밋 `0152c7b4` · 2026-03-25 · `CNAME` = `openapi.jennifersoft.com`)에
인라인된 OpenAPI 3.0.3 스펙(5.6.4 · 39경로 · 63오퍼레이션)을 추출해 전수 파싱 · 보조 = v2 매뉴얼 · 릴리즈 노트 5.6.0~5.7.0.1 · 설치 가이드 3·10장 · 실 서버 호출 0회. 주요 항목:
[J-4] Open API 정본 스펙 https://openapi.jennifersoft.com/ · [J-17] 설치 가이드 10장(토큰 발급 메뉴·MCP) · [J-18] 릴리즈 노트 5.7.0 ·
[J-19] 릴리즈 노트 5.6.5(도메인 GC API) · [J-20] 릴리즈 노트 5.6.3(토큰 사용량 제한·Open API 비활성 옵션) · [J-22] 설치 가이드 11장(제니퍼 AI 데이터 보안) ·
[J-15] SNMP 어댑터 · [J-6] 뷰 서버 확장 튜토리얼 · [OM-1] OpenMetrics 1.0 · [OM-2] OpenMetrics 2.0(Experimental).

---

## 14. 변경 이력

| 일자 | 내용 |
|---|---|
| 2026-09-29 | 최초 작성 — 사용자 지시 *"현재 구현을 검토하여 87번 제니퍼 연동 계획서를 업데이트하고 연동 가이드를 상세하게 docs 폴더에 작성하라."* · 87 v2.2(§0.6 구현 실측)와 함께. 87이 미구현이라 절마다 【현재 가능】/【계획】/「예정 이름」을 나눴다. 운영자 문서이며 화면·버튼 추가가 아니므로 D-255 매뉴얼 갱신 대상이 아니다 |
| 2026-09-29 | **v3 구조 반영** — 사용자 승인(*"권고에 맞게 계획을 수정하라."*)에 따라 제니퍼 연동이 **독립 최상위 패키지 `apm_gateway/`**(자체 MCP 서버 · 독립 프로세스)로 확정됐다(**D-274** · 87 §0.7). §0 요약 · §1 그림·근거·위치 표 · §3.2·§3.5·§3.6·§3.7(토큰·네트워크·채집 위치) · §4 설정 전체(키 위치 `mcp_server/.env` → `apm_gateway/.env` · 기동 명령 「예정」 `cd apm_gateway && ../.venv/bin/python -m apm_gateway` · 정합은 게이트웨이 → `mcp_server` MCP 호출 · `sre_agent` 두 번째 MCP 서버) · §5 통제 구현 위치 · §6 도구 제공 주체·`was_signals` · §7 소비 흐름 · §8 폴러 위치(G-4 해소 · G-4b) · §9 J7 노출 주체·직렬화기(G-12) · §10 경계 불변식·선택 배포 검증 · §11 게이트웨이 증상 · §12 게이트(G-4 해소 · G-4b · G-11 · G-12) · §13 D-274 |
| 2026-09-29 | **v3.1** — 사용자 지시 *"권고에 맞게 진행하고 미결사항은 해결하라."* · G-6 「권고대로 확정」 · *"제니퍼도 도커로 설치하여 검토할 수 있도록 계획에 포함시켜라."* 반영. §0 요약(게이트·결정 · 로컬 검증 환경 행) · 목차 · §3.7(J0-L/J0-O 열) · **§3.8 로컬 Docker 검증 환경 신설**(구성 · 위치 · 통제 · 기동·정지·정리 · 토큰 발급 · 이벤트 재현 · 쓰기 API 실측 · 목 서버 폴백 — 제품 사실은 「조사 대기」) · §5.4(G-6·G-7 확정 · D-195 ③) · §7.3(R-18 소유 = `plans/121`) · §9.3(G-10 수정 전후 표 · 재기동 필요 · 본체 `/metrics` 잔여) · §10(4단계 — 로컬 Docker 제니퍼 단계 추가 · 10.2 신설 · 이하 번호 한 칸씩) · §11(재기동·Docker 증상) · §12(확정 결과 표 · 남은 미결 = J0-O · API 대조 · Docker 사실) · §13(D-195·D-003) |
| 2026-09-29 | **v3.2** — Open API **공식 스펙 대조 반영**([J-23] · 87 §0.9). §0 머리말 · §3.1(5.7.0.1·5.6.5.12·39경로/63오퍼레이션·SNI) · §3.2(구 메뉴명 · 쿼리 `token=` 금지) · §3.3(룰 경로 정정 — `manage-rule-event`는 파일명 · 임계 변경 API 없음 · 쓰기·제어 목록 확장 · 민감 GET 표) · §3.4(비활성 옵션 `jennifer.unofficial.disable.open.api=true` · `ignore_auth_token` · 도메인 권한) · §3.5(기본 포트 7900 · SNI) · §3.6(curl 7900 · 스펙 확인 표기 · 404/405/SNI 판정) · §3.7(U-1 실제 경로 · U-8 확인 불가 · U-14 신설) · §5.2(C-2 정확 일치 · C-6·C-7) · **§5.3 허용목록 정본 표와 거부 사례** · §6.1(도구별 뒷단·필드 — `hostName`·`gcTimeUsage`·`activeDBConnection`·백분위 없음·`profile_ref`) · §6.2(`profile_ref_mismatch`) · §8.2(폴러 필수 파라미터·합성 멱등 키·`metricsName`) · §9.4(`heapUsed` MB → bytes) · §11(401 결함·405·SNI·Jetty 세션) · §12.2(경로 대조 완료) · §13([J-23]) |
| 2026-09-29 | **v3.2 부기 — Docker 로컬 설치 조사 반영**([J-24] · 87 §0.8). §3.8을 다시 썼다(공식 이미지 없음 → Dockerfile 신규 · 라이선스 없으면 에이전트 거부 · 평가판 2주·IP 기반 · Bootstrap Check off 로컬 한정 · 포트 7900/5000 · arm64 네이티브 먼저 · 포트 이중 구조와 라이선스 IP 구분 · J0-L-a/b와 2주 체크리스트 · 첫 기동·토큰 발급 · 샘플 앱 · 벤더 데모 서버 선택지 · 사용자 할 일 표) · §0 요약 · §10.2 · §11(라이선스 거부·Bootstrap 실패·arm64) · §12.2(Docker 사실 완료 · 사용자 할 일) · §13([J-24]). 「조사 대기」 칸은 모두 채우거나 「확인 불가」로 바꿨다 |
| 2026-09-29 | **v3.3** — 사용자 지시 *"도커에 제니퍼를 설치하여 현재 계획이 정상적인지 직접 테스트를 진행하여 계획을 업데이트하라."*(설치본 = 공개 S3 · 라이선스 없이 먼저 — 사용자 확정) · 87 §0.10 반영. 머리말 · §0 요약 · §3.4(기본 활성 실측) · §3.6 판정 표(500 두 종) · §3.7 U-2·U-5·U-12 · **§3.8을 실제 파일·명령 기준 【현재 가능】으로 재작성**(구성·파일·compose 키·준비·기동·토큰 발급 흐름·라이선스 없이 확인되는 것·통제·두 단계·사용자 할 일 9) · §5.3(실측 근거 · Accept) · §5.6(사용량 단위) · §6.1·§6.2(오류 분류 · 식별자 두 체계) · §8.2(폴러 오류 처리) · §10.2 · §11(7행 추가·3행 정정) · §12.2. 라이선스 거부 로그는 **에이전트 로그**로 정정 |
| 2026-09-29 | **v3.3 부기 — D-003 재기록**(사용자 확정 "권고") — L2는 D-003 예외가 아니라 **D-003 범위 밖**(WAS 조치 · DB 쓰기 없음) · 실행 평면은 D-195 ③이 별도 통제. 머리말 · §0 · §5.4 · §12.1 · §13 정정 |
| 2026-09-29 | **v3.3 부기 — 목 서버·녹화 하네스**(사용자 지시 *"목 서버와 녹화 하네스도 만들어라"*) — §3.8(파일 표 · 녹화 하네스 · 목 서버 · 폴백 【현재 가능】) · §10 머리·§10.1(목 서버 · 사본 대조 · `/__mock/hits` · J0 도구 테스트 명령 · G-11 확정 반영) · §11(목 서버·하네스 4행) · §12.2 · §0 |
| 2026-09-29 | **v4 — J1~J4 구현 반영**(사용자 지시 *"87번 계획을 구현하라."*) — 게이트웨이 `apm_gateway/`(허용목록 정본·클라이언트·정합·`apm_*` 8종·`gateway_health`·WAS 판정·이벤트 폴러·MCP 서버·Bearer·감사) · `sre_agent` 소비측(두 번째 MCP 서버·APM 지침·판정 승격·WAS 권고·브리핑 라벨·정체 가드) · `noise_gate` 소비측(`apm` kind 선판정·배지·트리거 힌트·`app_impact` 승격). 머리말 · §0 · §1.1·§1.3 · §2 · §4.1~§4.3·§4.5~§4.7·§4.9(실제 키) · §5.2·§5.3·§5.5·§5.6 · **§6 실제 도구·반환·오류 계약** · §7.1 · §8.2·§8.4 · §10.1(테스트·G-11 판정) · §12. J5·J6·J7 보류와 사유 · `was_object` 정합 미구현 · 원시 API 도구 미구현 · 버전 에코 없음을 적었다. 게이트웨이는 운영자 도구라 화면 변화가 없고, 알람 화면의 배지 값(「제니퍼」) 반영은 `noise_gate` 작업의 매뉴얼 판정을 따른다 |
| 2026-09-30 | **v5 — J8 다중 제니퍼 소스 구현 반영**(사용자 지시 *"87번 계획을 구현하라."* · 87 §0.14 · D-287) — 머리말 · §0(제니퍼 소스 행) · §4.1 · **§4.2 다중 소스 설정**(`JENNIFER_SOURCES`·접두 키·기동 실패·타임아웃 주의) · §4.3(기동 로그·헬스 소스별) · **§4.4 재작성**(`plans/125` A-1 등재 현황 + `sources[]` 정본·검증·조회 함수·소스 추가 절차) · §4.5(`overrides[].source_id`·`per_source` · F-3 해소) · §4.7(존 좁히기 · `invalid_argument` 시 재시도 없음 · 호스트 참고 표 키) · §4.9 · §6(소스 인자·`instance_refs`·`sources[]`·부분 실패 · 오류 표) · §7.1 · §8.2(커서·멱등·`dbId`·`resourceAncestry`) · §8.4(존 좁히기 · 툴팁 · 힌트 · **⑥ 알람 존 전달 F-7 해소**) · §10.1 · **§10.2 두 소스판**(절차 · 정상 응답 · 목 서버 25 + Docker 10 · IT 4 passed) · §11(6행 추가 · 2행 정정) · §12.2 · §13. 관리자 매뉴얼 A-30·9.5 동반 갱신(D-255) |
| 2026-09-29 | **v4.1 — 구현 대조 · 실서버 검증 반영**(사용자 지시 *"구현한 내용을 확인하여 docs폴더의 31번 가이드도 업데이트하라."*) — 구현 코드와 절마다 다시 대조: G-3 「미결」 표기 → 확정 · 남은 「예정」 표기 정리(§3.2·§3.5·§3.7·§9.4 — J5·J6·J7과 `was_object` 브릿지 몫만 남김) · §3.5 정합·MCP 소비 행(v4 실제) · §3.8 카탈로그 정본 = `allowlist.py`·대조 테스트 이름 · 깨진 문장 1곳 · §4.2 `APM_GATEWAY_LOG_LEVEL` · §4.8 게이트웨이 로더 실제 동작 · §13 **구현 위치 표** 신설 · D-195 표기. **87 §0.12 실서버 검증 반영**: §3.8(사용량 반영 지연 · 목 서버 재확인 · Docker IT 2 passed · 이벤트 재현 발행처 주의) · §4.5(빈 인벤토리 캐시) · §5.3(거부 34건 `usageCount` Δ=0 · `profile.txt` `key` 형식) · §5.6 · §6.1(지표 식별자 13/13) · §8.4(지연 실측 · 401 사유) · **§10.2 게이트웨이 실서버 검증 절(절차 · 정상 응답 · 58항목 · 미확인)** · §10.3(목업 판정은 테스트로 있음 · 실 LLM 미실행) · §10.4 · §11(6행 추가 · 5행 정정) · §12.2. 코드 변경 없음 · 화면 변화 없음(D-255 매뉴얼 대상 아님) |
| 2026-10-06 | **`plans/130` W1~W4 반영(W5 문서)** — 인스턴스 이름·업무명 조회: §4.3 기동 로그·버전 에코(허용 경로 37) · §4.5 교차 참조 · §5.3 `/api/business` 행(필수 `domain_id` · D-290 ④)·37템플릿 · §6.1 `apm_instance_map` `query?`·`business?` · `instance_name?`(데이터 13 + 관리 3) · `gateway_health` 허용 경로 수 37 · §6.2 오류 표(`invalid_argument`·`instance_unresolved` 추가 경우) · **§6.4 신설**(부르는 법 · 검색 단계 · 업무명 근거 B0~B3 + 폴스타 E6 · 찾지 못함과 후보 · 채팅 흐름 · `business_map` 작성법 · 첫 질의 비용) · §7.1 조사 지침 한 줄 · §7.2 채팅 질문. 예시 이름은 모두 가상 |
