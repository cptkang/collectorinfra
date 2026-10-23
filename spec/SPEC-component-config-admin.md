# SPEC — 분리 컴포넌트 설정 관리 API (`/admin/config`) · 본체 프록시

> **출처**: `plans/191` §3 · **결정**: D-345(D-179 개정) · **작성**: 2026-10-21 (W0)
> **대상**: `sre_agent` · `apm_gateway` · `mcp_server` · `noise_gate/alarm_server` · `fabrix_proxy` (+ 본체 프록시·UI)

## 1. 원칙

1. 각 컴포넌트가 **자기 설정의 조회·검증·저장**을 소유한다. 본체는 HTTP JSON 계약만 쓴다(import 0 · D-139).
2. **기본 off = 현행 비트 동일**: 컴포넌트 관리 토큰이 없으면 관리 라우트를 **등록하지 않는다**. 이때 `/admin/config/*` 요청은 기존 앱이 처리한다(서비스 Bearer가 켜져 있으면 401, 아니면 404 — 오늘과 같다).
3. 반영은 **재기동 필요**만(U-3). 모든 항목의 `apply_mode`는 `restart`다.
4. 시크릿 값은 컴포넌트 밖으로 나가지 않는다. 오가는 것은 `is_set` 불리언뿐이다.
5. 키는 **필드 표 허용목록**으로만 받는다. 쓰지 못하는 키는 사유와 함께 돌려준다(침묵 금지).

## 2. 컴포넌트 HTTP 계약

경로 접두 `/admin/config`. 인증은 `Authorization: Bearer <관리 토큰>`(상수 시간 비교 `hmac.compare_digest`). 불일치·누락 → `401 {"error":"unauthorized"}`. 본문 상한 64KB(초과 413).

### 2.1 `GET /admin/config/schema`

```json
{
  "component": {"name": "sre_agent", "version": "0.1.0", "pid": 4242,
                "started_at": "2026-10-21T09:00:00+09:00",
                "restart_hint": "cd sre_agent && .venv/bin/python -m sre_agent.run_service"},
  "cwd": "/opt/collectorinfra/sre_agent",
  "sources": [
    {"path": "/opt/collectorinfra/sre_agent/.env",    "role": "write_target", "exists": true,  "priority": 1},
    {"path": "/opt/collectorinfra/sre_agent/.encenv", "role": "override",     "exists": true,  "priority": 2},
    {"path": "(process environment)",                  "role": "override",     "exists": true,  "priority": 3}
  ],
  "writable": true,
  "write_block_reason": null,
  "groups": [{"group_key": "llm", "title": "LLM", "settings": [ITEM, "..."]}],
  "restart_pending_keys": [],
  "warnings": []
}
```

`ITEM`은 본체 `SettingSchemaItem`(`src/api/settings_catalog.py`)의 **같은 이름 부분집합**이다 — `admin.js` 위젯을 그대로 쓰기 위해서다.

| 필드 | 뜻 |
|---|---|
| `env_key` · `group_key` · `type` · `enum_choices` · `default` · `description` | 필드 표 메타. `default`·`effective_value`는 `.env` 표기 문자열(bool `"true"/"false"` · list/dict JSON). `type` ∈ `bool·int·float·enum·json_list·json_object·csv·string·secret` |
| `file_value` | 쓰기 대상 파일의 현재 값(없으면 `null`). 시크릿은 항상 `null` |
| `effective_value` | 기동 시 실제로 쓰인 값(시크릿은 `null`) |
| `override` | `null` · `"os"`(셸·systemd env) · `"encenv"` · `"dotenv"`(쓰기 대상보다 우선하는 `.env`) · `"toml"`(읽기 전용 원천) |
| `is_secret` · `is_set` | 시크릿 여부 · 설정 여부 |
| `writable` · `readonly_reason` | 항목 단위 쓰기 가능 여부와 불가 사유 |
| `apply_mode`=`"restart"` · `requires_restart`=`true` | 고정 |
| `restart_pending` | 파일 값이 기동 시 스냅샷과 다르다 |

### 2.2 `PUT /admin/config`

본문은 본체 `PUT /admin/settings`와 같은 꼴이다: `{"settings": {"KEY": "값"}, "reset_keys": ["KEY"], "dry_run": false}`. 헤더 `X-Request-ID`(있으면 감사에 기록).

처리 순서 — **전부 통과하거나 파일 불변**:
1. 전체 쓰기 차단(`writable=false`)이면 `409 {"error":"write_blocked","reason":…}`. 본문이 JSON 객체가 아니거나 `settings`·`reset_keys`·`dry_run`(bool) 형이 틀리면 400.
2. 키 분류: 필드 표에 없는 키 → `errors`. 시크릿 키·마스킹 값(`********`)·`writable=false`·`override` 걸린 키 → `ignored_keys`(사유 포함 · 파일 미기록).
3. sanitize: 값에 `#`·`$`·백틱 금지(`${VAR}` 확장으로 시크릿이 비시크릿 필드로 새는 것 차단), 유니코드 범주 `C*`·`Zl`·`Zp` 문자 전부 금지(`\x0b`·`\x85`·`\u2028` 등 줄 구분자 주입 차단 — 파서는 `\r\n|\n|\r`만 줄 경계로 본다), 앞뒤 공백·따옴표 금지. 쓰기 직전 후보 내용을 재파싱해 요청 밖 키가 생기거나 바뀌면 거부한다. 쓰기 대상 파일에 같은 키 활성 줄이 2개 이상이면 그 키는 `errors`(로더마다 첫 줄/끝 줄 우선이 달라 결정적으로 고칠 수 없다).
4. 타입 검증(필드 표 `type`).
5. **dry-run**: 후보 파일 내용을 임시 파일에 쓰고 **컴포넌트 자기 설정 로더**로 읽는다(실패 → `errors` · 파일 불변). `dry_run=true`면 여기서 끝.
6. 저장: 원본을 `<파일>.bak`으로 복사 → 같은 디렉토리 임시 파일 · fsync · 원 파일 권한 유지 · `os.replace`. 기존 줄은 제자리 교체(주석·순서 보존), 새 키는 끝에 추가, `reset_keys`는 활성 줄 삭제.
7. 감사 JSONL 1줄: `{"ts","request_id","component","result":"saved|dry_run|rejected","changes":[{"key","old","new"}]}` — 시크릿 키는 기록하지 않는다(애초에 저장 대상이 아니다).

`errors[]` 항목은 `{"key", "message"}`(키 없는 로더 오류는 `key: ""`). 감사 쓰기가 실패하면 저장은 유지하고 `"audit_logged": false`를 싣는다. 감사 파일은 0600, 값의 URL userinfo는 가리고 200자로 자른다.

응답 `200` (오류 있으면 `400`, 같은 형태):
```json
{"saved_keys": ["MAX_STEPS"], "ignored_keys": [{"key": "API_KEY", "reason": "secret"}],
 "errors": [], "dry_run": false, "restart_required": true, "restart_pending_keys": ["MAX_STEPS"]}
```
`ignored_keys[].reason` 어휘: `secret` · `masked` · `readonly` · `override_os` · `override_encenv` · `override_dotenv`(어휘만 유지 — 2026-10-21 alarm_server 순서 교정 뒤 내는 어댑터 없음). `sources[].role` 어휘: `write_target` · `override` · `readonly` · `fallback`(쓰기 대상보다 약한 보충 원천).

### 2.3 `GET /admin/config/status`

`{"component": {...}, "writable", "write_block_reason", "restart_pending_keys": [...]}` — 본체 컴포넌트 목록 카드용 가벼운 응답.

### 2.4 재기동 대기 판정

관리 앱을 만들 때(=기동 시) 쓰기 대상 파일을 파싱한 스냅샷을 잡는다. 이후 현재 파일 파싱 값과 **값으로** 비교한다(mtime 아님 · 같은 값 재저장은 대기 아님). `override` 걸린 키는 대기 대상이 아니다(파일을 바꿔도 반영되지 않는다).

## 3. 컴포넌트별 규칙

| 컴포넌트 | 관리 토큰(키 · 위치) | 쓰기 대상 | 쓰기 차단 · 우선 원천 | dry-run 로더 | 읽기 전용 항목 |
|---|---|---|---|---|---|
| `sre_agent` | `SRE_AGENT_ADMIN_TOKEN`(필드명 `sre_agent_admin_token` — `admin_token`+alias는 `populate_by_name=True`라 필드명 env `ADMIN_TOKEN`도 읽어 루트 `.env`의 본체 `ADMIN_TOKEN`과 충돌 · W1 실측) · `.encenv` | CWD `.env` | **CWD ≠ `sre_agent/` 패키지 루트면 전체 차단**(G-2 — 루트 CWD는 본체 `.env`를 읽는다). CWD `.encenv` 키는 `override_encenv`, OS env는 `override_os` | `AgentSettings(_env_file=(tmp, cwd/.encenv))` | `SERVICE_HOST`(바인딩 노출) |
| `apm_gateway` | `APM_GATEWAY_ADMIN_TOKEN` · `apm_gateway/.env` | `apm_gateway/.env`(패키지 루트 고정) | 기동 전 `os.environ`에 이미 있던 키(= `load_dotenv`가 넣지 않은 키)는 `override_os` — **기동 시 주입 키 집합을 기록**해야 판정된다 | `load_config(env=<후보 파일 ∪ OS 원천 키>)` | `APM_GATEWAY_HOST` · `JENNIFER_SOURCES`(소스 추가·삭제 — G-5) |
| `mcp_server` | `MCP_ADMIN_TOKEN` · `mcp_server/.env` | `mcp_server/.env`(`config.toml`과 같은 디렉토리) | OS 원천 키는 `override_os`(apm과 같은 방식) · `config.toml` 값은 `override="toml"` 읽기 전용(G-3) · `[[sources]]`는 이름·타입·`is_set`만(`connection` 값 미노출) | toml 파싱 + **`os.environ`을 건드리지 않는** env 매핑 적용(현 `load_config`는 `os.environ`을 고친다) | `SERVER_HOST` · `EXPOSE_EXECUTE_SQL` · `POLESTAR_DOMAIN_GUARD` · `EXPOSE_RAW_PROMQL`(데이터 경계 가드 — D-003 취지) |
| `alarm_server` | `ALARM_SERVER_ADMIN_TOKEN` + `ALARM_SERVER_ADMIN_PORT`(미설정 = 리스너 미기동) · `ALARM_SERVER_ADMIN_HOST`(기본 `127.0.0.1`) | CWD `alarm_server.env` | 파일이 없으면 전체 차단 + 「전용 파일 생성」 안내(G-4). **`alarm_server.env`가 CWD `.env`보다 우선**(로더 `[.env, alarm_server.env]` — pydantic-settings는 목록 뒤 파일이 이긴다 · 2026-10-21 순서 교정) → `.env`는 `sources[]`에 `role: "fallback"`(보충)으로만 표시하고 쓰기를 막지 않는다 | `AlarmServerConfig(_env_file=[cwd/.env, tmp])` | `ALARM_SERVER_SOCKET_HOST` · `ALARM_SERVER_ADMIN_*` |
| `fabrix_proxy` | `FABRIX_PROXY_ADMIN_TOKEN` · `fabrix_proxy/.encenv` | `fabrix_proxy/.env`(패키지 루트 고정) | `.encenv` 키 `override_encenv` · OS env `override_os` | `ProxySettings(_env_file=(tmp, root/.encenv))` | `FABRIX_PROXY_HOST`(있으면) |

- **보안 경계 키는 전 컴포넌트 읽기 전용**(2026-10-21 감사 M-2·M-3 반영): 바인딩 호스트 · 자격증명이 따라가는 URL·호스트(`*_URL`·`API_BASE`·`REDIS_HOST` 등 — 바꾸면 저장된 토큰이 다른 호스트로 간다) · 보안 가드(`*_GUARD*`·`*VERIFY_SSL*`·도메인 제한) · 파일·디렉토리 경로(`*_PATH`·`*_DIR`·`*_FILE`) · 프롬프트 주입 경로(`INVESTIGATION_GUIDANCE_EXTRA`). 위 표의 「읽기 전용 항목」은 최소 예시다.
- `mcp_server`는 env가 toml을 이기므로 `override="toml"`을 내지 않는다 — `config.toml`은 `sources[]`에 `role: "readonly"`로만 표시한다.
- `sre_agent`의 `override_os` 판정은 **패키지 import 시점의 프로세스 환경 키 스냅샷** 기준이다(litellm import가 `load_dotenv()`로 `.env`를 `os.environ`에 넣기 때문 — verifier 실측).
- 공통: 관리 토큰 키 자체는 필드 표에서 시크릿(쓰기 불가)이다. 관리 토큰이 서비스·MCP Bearer와 **같은 값이면 라우트를 등록하지 않고** 경고 로그 1줄(G-1).
- 시크릿 판정: `SecretStr` 자동 + 컴포넌트별 **명시 시크릿 목록**(`*_TOKEN(S)`·`*_PASSWORD`·`*_API_KEY`·`*_CLIENT_KEY`·`*_CONNECTION`·`PROMETHEUS_AUTH_HEADER`·`ANTHROPIC_API_KEY`·`OPENAI_API_KEY` 등).
- 커버리지 테스트: `필드 표 ⊇ .env.example 키 − 시크릿`(주석 처리된 `# KEY=` 포함).

## 4. 공용 모듈 — `config_admin.py` 바이트 동일 사본 (D-339 ②)

- 의존: **stdlib + `starlette`만**. 컴포넌트 패키지·`src`를 import하지 않는다.
- 정본: `sre_agent/sre_agent/interface/config_admin.py`. 사본: `apm_gateway/apm_gateway/interface/config_admin.py` · `mcp_server/mcp_server/config_admin.py` · `noise_gate/alarm_server/config_admin.py` · `fabrix_proxy/fabrix_proxy/config_admin.py`. 일치는 루트 `tests/test_utils/test_config_admin_copies.py`가 바이트로 단언한다.
- 담당: 필드 명세 자료형 · `.env` 파싱·줄 보존 편집 · sanitize·타입 검증 · 원자 교체·`.bak` · 감사 줄 · 응답 직렬화 · Starlette 관리 앱 · **경로 분기 ASGI 래퍼**(`/admin/config` 접두만 관리 앱으로, 나머지는 기존 앱으로 — scope 무변경 · lifespan 등 비 HTTP는 기존 앱으로).
- 컴포넌트별로 다른 것(필드 표 · 쓰기 대상·차단 판정 · override 판정 · dry-run 로더 · 읽기 전용 항목 · 기동 명령)은 **어댑터**로 주입한다. 어댑터는 각 패키지의 자기 모듈(`*_admin_adapter.py` 등)에 둔다.
- 배선: 관리 토큰이 있을 때만 기존 앱을 래퍼로 감싼다. 서비스 Bearer 미들웨어는 손대지 않는다(관리 경로는 래퍼가 먼저 가로채므로 서비스 토큰을 요구하지 않는다).

## 5. 본체

- **설정** `ComponentAdminConfig`(`src/config/services.py` · `env_prefix="COMPONENT_ADMIN_"` · `env_file=(".env", ".encenv")`) → `AppConfig.component_admin`:
  - `endpoints: dict[str, str]`(`COMPONENT_ADMIN_ENDPOINTS` · JSON · 예 `{"sre_agent": "http://127.0.0.1:9098"}` · 미입력 = 기능 off)
  - `tokens: SecretStr`(`COMPONENT_ADMIN_TOKENS` · `.encenv` · JSON 객체 `{이름: 토큰}`)
  - `timeout_seconds: float = 5.0`
  - 허용 이름은 코드 상수 5종(`sre_agent·apm_gateway·mcp_server·alarm_server·fabrix_proxy`). 상수 밖 이름·`http(s)` 아닌 URL은 무시하고 경고.
- **클라이언트** `src/infrastructure/component_admin_client.py`: httpx · `follow_redirects=False` · 짧은 타임아웃 · 컴포넌트별 개별 try/except. 실패 사유 어휘 `connect_error` · `timeout` · `unauthorized`(401) · `not_enabled`(404) · `bad_response` · `http_error`. 응답 본문 상한 1 MiB — 넘으면 `bad_response`(메시지에 상한 초과 명시). URL은 설정값 + 고정 경로만 조립한다(요청 입력으로 URL·경로를 만들지 않는다 — SSRF).
- **라우트** `src/api/routes/admin_components.py`(`/api/v1` 접두 · 전부 `require_admin_user`):
  - `GET /admin/components` → `{"components": [{"name", "endpoint_configured", "reachable", "reason", "status": <2.3 응답|null>}]}`(병렬 · 개별 실패 격리)
  - `GET /admin/components/{name}/config` → 컴포넌트 2.1 응답 + `"reachable"`. 실패는 `502 {"reason", "message"}`.
  - `PUT /admin/components/{name}/config` → 본문 그대로 전달 + `X-Request-ID` 생성·전파. 본체 감사 `SETTINGS_UPDATE`에 `component`·`saved_keys`·`ignored_keys` 이름만(값 없음). 컴포넌트의 `audit_logged`는 그대로 두고, 본체 감사 결과는 `proxy_audit_logged`(bool)로 덧붙인다.
  - `{name}`이 허용 상수 ∩ 설정 키가 아니면 404. 응답에서 `is_secret` 항목의 `file_value`·`effective_value`를 한 번 더 지운다(방어 심화).
- **UI**: 설정 탭 상단 대상 선택(본체 + 설정된 컴포넌트). 컴포넌트가 0개면 선택 UI를 그리지 않는다(현행 화면 동일). 카드 = 연결 상태·버전·기동 시각·읽는 파일·쓰기 가능 여부·재기동 대기 배지. 저장 후 배너 「재기동 대기: N키 — `<restart_hint>`로 재기동하세요」. 컴포넌트가 준 문자열은 텍스트로만 렌더한다(XSS).

## 6. 오류·상태 어휘 요약

| 위치 | 어휘 |
|---|---|
| 컴포넌트 HTTP | 401 `unauthorized` · 409 `write_blocked` · 400(`errors[]`) · 413 |
| `ignored_keys.reason` | `secret` · `masked` · `readonly` · `override_os` · `override_encenv` · `override_dotenv` |
| 본체 클라이언트 `reason` | `connect_error` · `timeout` · `unauthorized` · `not_enabled` · `bad_response` · `http_error` |
