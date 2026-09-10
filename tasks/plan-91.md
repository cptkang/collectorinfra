# Plan 91 실행 계획 — 장애 조사 잔여 통합(§1.1 코드 7항목)

> `plans/91` §1.1·§2 · `CAPABILITY-MAP-91.md` · 착수 2026-09-10 · 가정은 지도 ASSUMPTIONS(특히 4: 1-3 전제 정정)

## 구현 순서와 그 이유

```
M1 correlation-e2e-assertions  ← 뒤 모듈 산출물의 "브리핑 도달"을 과금 0으로 단언할 경로가 먼저
M2 change-event-overlay        ← G1 앵커 패턴 재사용(가장 싸고 §4.5 인과력 최대) · 플래그 off 비트 동일
M3 related-host-consumption    ← 페이로드 실측 정정 위에서(root_resource_name 1줄) · 플래그 off 비트 동일
M4 investigation-feedback-ref  ← 83 피드백 루프에 investigation_id 필드만
M5 playbook-guidance           ← 독립(병렬 가능) · 51 §6 → kind별 결정적 문구
M6 l3-postgate-enrichment      ← 독립(병렬 가능) · 60 §18 (나)안
M7 residual-doc-sync           ← 각 모듈 뒤 · D-번호 채번은 완료 시(예약 없음)
```

## 검증 체크포인트

| 이후 | 확인 |
|---|---|
| M1 | `sre_agent` 스텁 테스트 과금 0 통과 · RUN_E2E 미설정 시 실 테스트 skip |
| M2 | `mcp_server` SQL 스냅샷 불변 · `sre_agent` 플래그 off 기존 테스트 전부 통과 · 골든(변경 T-20m ⇒ 가설 1위) |
| M3 | 메타 부재·플래그 off 종전 결과 비트 동일 · 연관 알람 부분 실패 시 대표 결과 보존 |
| M4 | 기존 알람 피드백 경로 비트 동일 · 존 RBAC 거부 · 철회 |
| M5 | 미매칭 kind 문자열 동일 · kind별 골든 · `system_prompt_additions` 도달 |
| M6 | 플래그 off 비트 동일 · 변경명령 차단·마스킹·감사 · `arch_check --ci` 0 |
| 공통 | `sre_agent/tests` · `mcp_server/tests` · 본체 `pytest` 신규 실패 0 · `arch_check`·`overfit_check --ci` 0 |
