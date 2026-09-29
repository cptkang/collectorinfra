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
