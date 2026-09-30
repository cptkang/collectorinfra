# Capability Map: 87 제니퍼(JENNIFER) APM 연동 — `apm_gateway` + 소비자(J1~J4)

> **작성일** 2026-09-29 · **계획서** `plans/87-*-jennifer-apm-integration.md`(v3.3 · 작업 트리 미커밋본이 정본) · **결정** D-195 · D-274
> **가이드** `docs/31_jennifer_integration_guide.md` · **사용자 지시** *"87번 계획을 구현하라."*(2026-09-29)
> **게이트**: G-1~G-12 전건 확정(계획서 §10). 이 맵은 확정 내용을 바꾸지 않는다.

## 착수 판정 (계획서 §6 "선행 완료 + 게이트 해제인 Wave만")

| Wave | 판정 | 근거(실측 2026-09-29) |
|---|---|---|
| J0-L-a | 완료(선행) | `apm_gateway/testdata/jennifer/` · 녹화본 21건 · 목 서버 · 도구 테스트 22건 |
| J0-L-b · J0-O | **보류 — 사용자/외부 전제** | 평가판 라이선스(2주 창)·최신 에이전트 입수 · 운영 접근 권한·토큰. 계약 픽스처는 녹화본(`local-docker`, 라이선스 없음) + 목 서버 + 스펙 5.6.4 스키마 합성 픽스처(테스트 안에서만)로 대신한다 |
| **J1** | **착수** | 선행 = J0-L-a(§6 [v3.2] "J1 골격은 J0-L-a 뒤 착수 가능") · 게이트 없음 |
| **J2** | **착수** | 선행 J1 · G-3 확정(`apm_*`) |
| **J3** | **착수** | 선행 J2 · 게이트 없음 |
| **J4** | **착수** | 선행 J2·J3 · G-4 해소(D-274 ④) · G-4b 확정(push는 실측 문제 시만 → 이번엔 폴링만) |
| J5 | **보류** | 선행 `plans/121` TP-9.1·9.2·10.5가 코드 0 — `metric_query` 0건 · `config/task_routines.yaml` 부재 · 레지스트리 `backend: mcp` 실행 0 · 121 §14.2 "[v7 미착수]" |
| J6 | **보류** | G-6 착수 조건 "J3 완료 **+ 목업 검증 뒤**" — J3 목업은 로컬 Docker 재현(v3.1 의존 그림)이라 J0-L-b 필요 · 덤프·PLC 실행 채널은 J0 확인 사항(U-8). 같은 작업에서 J3과 J6을 이어 붙이면 순차 검증 조건을 건너뛴다 |
| J7 | **보류** | G-9 ①′ 착수 조건 "J2 완료 + **소비자 확정**" — 운영 Prometheus URL 공란(가이드 §12) · 소비자 미확정 |
| **J8**(2026-09-30 추가) | **착수 · 구현** | 다중 제니퍼 소스(계획서 §0.13 · §0.14) — G-13~G-16 확정 · D-287 · 선행 J1~J4·목 서버 완료 · 라이선스·운영 접근 불필요. 모듈은 새로 만들지 않고 기존 세 모듈의 계약에 **추가만** 했다 — `SPEC-apm-gateway.md`·`SPEC-apm-noise-gate.md`·`SPEC-apm-sre-agent.md`의 [J8] 표지 · 루트 레지스트리 `solutions[apm].sources[]`와 알람 존 판정(`src/routing/`)은 본체 소유 · 본체 채팅 쪽(G-16·승계 패싯)은 `plans/125` |

## 모듈

| Module id | 패키지 | 책임 | Depends on | Wave |
|---|---|---|---|---|
| `apm-gw-core` | `apm_gateway/` | 패키지 골격 · 설정(.env + 정책 yaml) · MCP 서버(SSE) · 정적 Bearer · 감사 · 경계 불변식 · 계층 방향 테스트 | — | J1 |
| `apm-gw-adapter` | `apm_gateway/apm_gateway/adapters/jennifer/` | Open API 클라이언트 · 허용목록 정본(메서드 + 경로 템플릿 정확 일치) · 레이트 리밋 · 응답 크기 상한 · 리다이렉트 비추종 · 오류 분류(본문 기준) · 지표 식별자 매핑 | `apm-gw-core` | J1 |
| `apm-gw-resolve` | `apm_gateway/apm_gateway/application/` | 인스턴스 ↔ hostname 정합(override → `hostName` 직접 대조 → 이름 규칙) · TTL 캐시 | `apm-gw-adapter` | J1 |
| `apm-gw-signals` | `apm_gateway/apm_gateway/domain/` | WAS 시그니처 8종 결정적 판정(`was_signals`) · 이벤트 유형 → 시그니처 | — | J2 |
| `apm-gw-tools` | `apm_gateway/apm_gateway/application/` · `interface/` | `apm_*` 8종 + `gateway_health` · 상위 N 축약 · 마스킹 · 반환·오류 계약 | `apm-gw-resolve`, `apm-gw-signals` | J2 |
| `apm-gw-poller` | `apm_gateway/apm_gateway/application/` · `domain/` | 이벤트 폴러(도메인별 커서 · 합성 멱등 키) · 정규화 · `alarm:raw` XADD | `apm-gw-resolve`, `apm-gw-signals` | J4 |
| `apm-sre` | `sre_agent/` | 두 번째 MCP 서버 등록 · APM 지침·앵커·플레이북 · `apm` kind 선판정(R-16) · `was_signals` 승격 · WAS 권고 표 · 브리핑 소스 라벨 | `apm-gw-tools`(MCP 계약만) | J3 |
| `apm-noise` | `noise_gate/` (+ `src/config.py` `NoiseGateConfig` 필드) | `apm` kind 선판정(R-16) · family 배지 "제니퍼" · 트리거 `meta.hints` · `app_impact` 승격(게이트웨이 MCP 클라이언트) | `apm-gw-tools`·`apm-gw-poller`(계약만) | J4 |

**Build order**: `apm-gw-core` → `apm-gw-adapter` → `apm-gw-resolve` → `apm-gw-signals` → `apm-gw-tools` → `apm-gw-poller`.
`apm-sre`·`apm-noise`는 계약(`spec/SPEC-apm-gateway.md` §3~§5)만 보고 **병렬**로 만든다(import 0 — 계약 픽스처를 각자 복제).

## 경계 규칙

- `apm_gateway` ↔ `src`·`noise_gate`·`sre_agent`·`mcp_server` **양방향 import 0**(D-274 ③). 통신은 MCP · Redis Stream `alarm:raw` 계약만.
- 제니퍼 토큰·URL은 `apm_gateway/.env`에만. `src/`·`noise_gate/`·`sre_agent/`·`mcp_server/`에 제니퍼 토큰·URL 0건(소비자는 **게이트웨이 MCP URL·Bearer**만 가진다).
- 벤더 리터럴(Open API 경로·필드명)은 `adapters/jennifer/`에만. `domain/`·`application/`·`interface/`는 벤더 중립 어휘.
- 소비자 쪽은 플래그/URL 미설정 시 **현행과 비트 동일**. `resourceType="apm.Instance"` 선판정은 게이트웨이가 낸 이벤트에서만 발현하므로 게이트웨이 미배포 시 동작 불변.
- `mcp_server/`에는 손대지 않는다(폴스타 고수준 도구 `polestar_was_instances`는 U-10 미확인으로 **이번 범위 밖** — 정합 ②순위 보류).

## 이번 범위에서 제외 (계획서에는 있으나 후속)

| 제외 | 사유 |
|---|---|
| `was_object` 정합 브릿지(게이트웨이 → `mcp_server` MCP) · `polestar_was_instances` | §0.7 (7) "필요 시" — U-10(운영 `was_object` 실재·채움률) 미확인. v3.2 정합 순서가 `hostName` 직접 대조를 ①로 올렸으므로 ② 없이도 정합이 선다. J0-O 뒤 재판정 |
| `apm_raw_api`(원시 도구 · `EXPOSE_RAW_APM_API`) | 수용 기준에 없음 · 공격 표면만 늘린다. 필요 시 같은 허용목록으로 추가 |
| 기동 시 제니퍼 버전 에코(R-12 `api_version_expect`) | 허용목록 16템플릿에 버전 조회 경로가 없다(에이전트 버전은 `/api/instance.version`) — 경로 추가는 계획서 개정 사항 |
| 어댑터 push(2-A·2-B) | G-4b — 폴링의 문제가 실측될 때만 |
| 이벤트 유형별 `notify` 정책(page·dashboard·suppress) | 운영 유형 목록(U-1·U-13) 미확정 — 레벨 매핑만 둔다 |
| p95·에러율의 3σ 기준선 | 기준선 이력 조회 경로가 없다 — 잠정 절대 임계 + `HIGH_RATE_FAIL` 이벤트로 판정(계획서 §5.4(b) "임계 잠정 · J0 보정") |
