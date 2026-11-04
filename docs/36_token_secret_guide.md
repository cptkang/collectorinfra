# 토큰·시크릿 설정 가이드 — 어디에 두고, 무엇끼리 같아야 하나

> 대상: 운영 배포 담당자. 컴포넌트별로 흩어진 토큰·키를 한곳에서 정리한다.
> 기준 원천: 각 패키지의 `.env.example`·`.encenv.example`과 설정 코드(2026-10-29 실측).
> 값을 바꾸면 **양쪽 프로세스를 모두 재기동**해야 반영된다.

## 1. 세 가지 종류만 기억하면 된다

| 종류 | 규칙 | 틀리면 |
|---|---|---|
| **짝 토큰** | 서버 쪽 값 = 호출하는 쪽 값 (§3) | 401 · 「관리 토큰이 맞지 않습니다」 · 조사·APM 조회 실패 |
| **달라야 하는 토큰** | 같은 서버의 서비스 토큰 ≠ 관리 토큰, 사용자 JWT ≠ 운영자 JWT (§4) | 관리 경로가 **등록되지 않음**(기동 경고 로그 1줄만) · 권한 상승 위험 |
| **단독 시크릿** | 외부 시스템이 발급한 값을 그대로 둔다 — 짝 없음 (§5) | 해당 외부 호출만 실패 |

## 2. 컴포넌트별 설정 파일

| 컴포넌트 | 읽는 파일 | 비고 |
|---|---|---|
| 본체 (`src/`) | 루트 `.env` · 루트 `.encenv` | **그룹마다 `.encenv`를 읽는지가 다르다** — 아래 표의 「파일」 칸을 따른다 |
| `sre_agent` | 기동 CWD의 `.env` · `.encenv` | `sre_agent/`를 CWD로 기동한다(레포 루트에서 띄우면 본체 파일을 읽고, 웹 설정 저장도 막힌다) |
| `mcp_server` | `mcp_server/.env` (또는 `config.toml [server]`) | `.encenv` 없음 |
| `apm_gateway` | `apm_gateway/.env` | `.encenv` 없음 |
| `alarm_server` | CWD의 `.env`(공용) + `alarm_server.env`(전용 — 이것이 이긴다) | 예시: `noise_gate/alarm_server/alarm_server.env.example` |
| `fabrix_proxy` | `fabrix_proxy/.env` · `fabrix_proxy/.encenv` | `.encenv`가 이긴다 |

**본체에서 `.encenv`를 읽지 않는 그룹** — 이 키들은 `.encenv`에 넣으면 무시되므로 **`.env`에 둔다**:
`DBHUB_*`·`MCP_SOURCE_*`(DBHubConfig) · `NOISE_*` · `OBS_*` · `RAG_*`.
`.encenv`를 읽는 그룹: `LLM_*` · `ORCHESTRATOR_*` · `ADMIN_*` · `AUTH_*` · `REDIS_*` · `WORKB_*` · `COMPONENT_ADMIN_*`.

## 3. 짝 토큰 — 같은 값이어야 하는 것

### 3.1 연결 구조

```
                       ┌──────────────── 서비스 Bearer ────────────────┐
 본체 ── DBHUB_BEARER_TOKEN ─────────────▶ mcp_server   (MCP_BEARER_TOKEN)
  │  ── MCP_SOURCE_TOKENS["apm"] ────────▶ apm_gateway  (APM_GATEWAY_BEARER_TOKEN[S])
  │  ── NOISE_APM_MCP_TOKEN ─────────────▶ apm_gateway  (〃)
  │  ── NOISE_INVESTIGATION_SERVICE_TOKEN ▶ sre_agent   (SERVICE_BEARER_TOKEN)
  │                                          │── POLESTAR_MCP_TOKEN ▶ mcp_server  (MCP_BEARER_TOKEN)
  │                                          └── APM_MCP_TOKEN ─────▶ apm_gateway (APM_GATEWAY_BEARER_TOKEN[S])
  │
  └── COMPONENT_ADMIN_TOKENS{이름: 토큰} ─ 관리 토큰 ─▶ 각 컴포넌트의 *_ADMIN_TOKEN
```

### 3.2 서비스 Bearer (데이터 조회·조사 호출)

| # | 서버 쪽 (받는 쪽) | 호출하는 쪽 (같은 값) |
|---|---|---|
| S1 | `mcp_server/.env` `MCP_BEARER_TOKEN` | 본체 `.env` `DBHUB_BEARER_TOKEN` · `sre_agent/.env` `POLESTAR_MCP_TOKEN` · Prometheus가 `/metrics` 브리지를 긁으면 그 `authorization.credentials` |
| S2 | `apm_gateway/.env` `APM_GATEWAY_BEARER_TOKEN` (단일) | 본체 `.env` `MCP_SOURCE_TOKENS={"apm": "<값>"}` · 본체 `.env` `NOISE_APM_MCP_TOKEN` · `sre_agent/.env` `APM_MCP_TOKEN` |
| S3 | `sre_agent/.env` `SERVICE_BEARER_TOKEN` | 본체 `.env` `NOISE_INVESTIGATION_SERVICE_TOKEN` |

**S2를 소비자별로 나누는 경우**(`APM_GATEWAY_BEARER_TOKENS` — 권장): 주체마다 다른 토큰을 주고 호출하는 쪽에 그 주체의 값을 넣는다. 같은 토큰을 두 주체에 주면 게이트웨이가 기동을 거부한다.

```dotenv
# apm_gateway/.env
APM_GATEWAY_BEARER_TOKENS={"chat": "tok-chat", "alarm": "tok-alarm", "investigation": "tok-inv"}
```

| 주체 | 호출하는 쪽 키 |
|---|---|
| `chat` | 본체 `MCP_SOURCE_TOKENS={"apm": "tok-chat"}` — 채팅은 프로파일 호출 예산을 쓰지 않는다 |
| `alarm` | 본체 `NOISE_APM_MCP_TOKEN=tok-alarm` |
| `investigation` | `sre_agent` `APM_MCP_TOKEN=tok-inv` |

> 제니퍼 Open API 토큰(`JENNIFER_API_TOKEN`)은 게이트웨이 → 제니퍼 구간 전용이다. 위 Bearer와 섞지 않는다(D-274).

### 3.3 관리 토큰 (관리자 화면 「설정」 탭의 컴포넌트 설정 조회·저장 — plans/191 · D-345)

본체 루트 `.encenv`에 JSON 한 줄로 둔다. 토큰이 있는 컴포넌트만 화면에 나타난다.

```dotenv
COMPONENT_ADMIN_TOKENS={"sre_agent": "adm-sre", "mcp_server": "adm-mcp", "apm_gateway": "adm-apm", "alarm_server": "adm-alarm", "fabrix_proxy": "adm-fx"}
```

| `COMPONENT_ADMIN_TOKENS` 키 | 같은 값을 둘 곳 | 주소 |
|---|---|---|
| `sre_agent` | `sre_agent/.encenv` `SRE_AGENT_ADMIN_TOKEN` | 자동 — 본체 `NOISE_INVESTIGATION_SERVICE_URL`의 호스트·포트 |
| `mcp_server` | `mcp_server/.env` `MCP_ADMIN_TOKEN` | 자동 — 본체 `DBHUB_SERVER_URL` (`SERVER_TRANSPORT=sse`일 때만 등록) |
| `apm_gateway` | `apm_gateway/.env` `APM_GATEWAY_ADMIN_TOKEN` | 자동 — 본체 `MCP_SOURCE_ENDPOINTS["apm"]`, 없으면 `NOISE_APM_MCP_URL` |
| `alarm_server` | `alarm_server.env` `ALARM_SERVER_ADMIN_TOKEN` (+ `ALARM_SERVER_ADMIN_PORT`) | **직접 적는다** — 본체 `.env` `COMPONENT_ADMIN_ENDPOINTS={"alarm_server": "http://127.0.0.1:<ADMIN_PORT>"}` |
| `fabrix_proxy` | `fabrix_proxy/.encenv` `FABRIX_PROXY_ADMIN_TOKEN` | **직접 적는다** — `COMPONENT_ADMIN_ENDPOINTS`에 `"fabrix_proxy": "http://127.0.0.1:9095"` |

`COMPONENT_ADMIN_ENDPOINTS`에 적은 주소는 자동 주소보다 우선한다.

### 3.4 LLM 프록시 (`fabrix_proxy`를 쓸 때만 — plans/148 PoC)

| 서버 쪽 | 호출하는 쪽 (같은 값) |
|---|---|
| `fabrix_proxy` `FABRIX_PROXY_TOKEN` (비면 기동 거부) | 본체 `.encenv` `ORCHESTRATOR_API_KEY` (`ORCHESTRATOR_BASE_URL=http://<proxy>:9095/v1`일 때) · `sre_agent/.encenv` `API_KEY` (`API_BASE=http://<proxy>:9095/v1`일 때) |

사내 vLLM을 `--api-key`로 띄웠다면 그 키를 본체 `LLM_VLLM_API_KEY`(워커) · `ORCHESTRATOR_API_KEY`(오케스트레이터) · `sre_agent` `API_KEY`에 같은 값으로 넣는다.

### 3.5 수집기(Prometheus) → 지표 주소

| 서버 쪽 | 호출하는 쪽 |
|---|---|
| 본체 `.env` `OBS_METRICS_BEARER_TOKEN` (`/api/v1/metrics` — 켰는데 비면 503) | Prometheus scrape job의 `authorization.credentials` |
| `mcp_server` `MCP_BEARER_TOKEN` (`EXPOSE_POLESTAR_EXPORTER=true`의 `/metrics`) | 〃 (S1과 같은 값) |

### 3.6 Redis 비밀번호 (같은 Redis를 쓰므로 같은 값)

| 위치 | 키 |
|---|---|
| 본체 `.encenv` | `REDIS_PASSWORD` |
| `alarm_server.env` | `ALARM_SERVER_REDIS_PASSWORD` |
| `apm_gateway/.env` | `REDIS_PASSWORD` (이벤트 폴러가 `alarm:raw`에 넣을 때) |

## 4. 달라야 하는 토큰

같은 서버에서 서비스 토큰과 관리 토큰이 같으면 **관리 경로를 등록하지 않는다**. 기동 로그에 「설정 관리 API 미등록: 관리 토큰이 … 같다」 1줄만 남고, 화면에는 「설정 관리 API가 꺼져 있습니다」 또는 「관리 토큰이 맞지 않습니다」로 보인다 — 짝(§3.3)이 맞는데 이 문구가 나오면 이 표를 먼저 본다.

| 서버 | 서로 달라야 하는 값 |
|---|---|
| `sre_agent` | `SERVICE_BEARER_TOKEN` ≠ `SRE_AGENT_ADMIN_TOKEN` |
| `mcp_server` | `MCP_BEARER_TOKEN` ≠ `MCP_ADMIN_TOKEN` |
| `apm_gateway` | `APM_GATEWAY_BEARER_TOKEN`·`APM_GATEWAY_BEARER_TOKENS`의 모든 값 ≠ `APM_GATEWAY_ADMIN_TOKEN` |
| `fabrix_proxy` | `FABRIX_PROXY_TOKEN` ≠ `FABRIX_PROXY_ADMIN_TOKEN` |
| 본체 | `AUTH_JWT_SECRET`(사용자) ≠ `ADMIN_JWT_SECRET`(운영자) — 시크릿 분리로 권한 상승 차단 |

## 5. 단독 시크릿 — 짝 없음

| 위치 | 키 | 출처 |
|---|---|---|
| 본체 `.encenv` | `ADMIN_PASSWORD` · `ADMIN_JWT_SECRET` · `AUTH_JWT_SECRET` | 직접 생성 (`openssl rand -hex 32`) — `AUTH_ENABLED=true`면 필수 |
| 본체 `.encenv` | `LLM_FABRIX_API_KEY` · `LLM_FABRIX_CLIENT_KEY` | FabriX(KBGenAI) 발급 |
| 본체 `.encenv` | `LLM_GEMINI_API_KEY` · `LLM_OLLAMA_API_KEY` | 각 서비스 발급 (Gemini는 과금 — 쓸 때만) |
| 본체 `.encenv` | `WORKB_BEARER_TOKEN` | worKB 메신저 발급 |
| 본체 `.env` | `RAG_<문서군>_TOKEN` · `_CLIENT_KEY` (+ `_ENDPOINT`·`_RETRIEVAL_ID`) | GenAI 플랫폼 — 발급마다 4개를 함께 교체 |
| `fabrix_proxy` | `FABRIX_API_KEY` · `FABRIX_CLIENT_KEY` | FabriX 발급 (본체 `LLM_FABRIX_*`와 같은 키를 써도 된다) |
| `apm_gateway/.env` | `JENNIFER_API_TOKEN` 또는 `JENNIFER_<ID>_API_TOKEN` | 제니퍼 콘솔(AIOps 전용 토큰) |
| `sre_agent` | `GEMINI_API_KEY` 등 조사 LLM 키 | 조사 LLM 프로바이더 |

## 6. 점검 순서

1. **서버 쪽부터 정한다** — §3 표의 「서버 쪽」 값을 먼저 만들고, 「호출하는 쪽」에 복사한다.
2. **파일 위치를 확인한다** — 본체 `DBHUB_*`·`MCP_SOURCE_*`·`NOISE_*`·`OBS_*`는 `.env`다(§2). `.encenv`에 넣으면 무헤더로 호출되어 401이 난다.
3. **형식** — `MCP_SOURCE_TOKENS`·`APM_GATEWAY_BEARER_TOKENS`·`COMPONENT_ADMIN_TOKENS`는 한 줄 JSON. 값 뒤 인라인 주석 금지.
4. **재기동** — 서버와 호출하는 쪽을 모두 재기동한다.
5. **확인**

| 증상 | 볼 곳 |
|---|---|
| 설정 탭 카드 「관리 토큰이 맞지 않습니다」 | §3.3 짝 → 맞으면 §4 |
| 「설정 관리 API가 꺼져 있습니다」 | 컴포넌트 쪽 `*_ADMIN_TOKEN` 미설정 · §4 (서비스 토큰과 같은 값) · `mcp_server`는 `SERVER_TRANSPORT=sse` |
| 설정 탭에 컴포넌트가 안 보임 | `COMPONENT_ADMIN_TOKENS`에 그 키가 없거나 JSON 오류 |
| 「컴포넌트에 연결하지 못했습니다」 | 프로세스 미기동 · `alarm_server`·`fabrix_proxy` 주소(§3.3 `COMPONENT_ADMIN_ENDPOINTS`) |
| 자동 조사·장애 진단 실패 | S3 (`NOISE_INVESTIGATION_SERVICE_TOKEN` ↔ `SERVICE_BEARER_TOKEN`) |
| 채팅 DB 조회 401 | S1 (`DBHUB_BEARER_TOKEN` ↔ `MCP_BEARER_TOKEN`) |
| 제니퍼 질의·앱 영향 승격 실패 | S2 |

## 관련

- 결정: D-125(정적 Bearer) · D-274(APM 게이트웨이) · D-345(컴포넌트 설정 관리)
- 컴포넌트 안내: `docs/26_sre_agent_guide.md` · `docs/31_jennifer_integration_guide.md` · `docs/03_setup_guide.md`
- 관리자 매뉴얼: `scripts/manual/content/admin.md` 「설정」 절
