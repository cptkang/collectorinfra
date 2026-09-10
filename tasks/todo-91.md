# Todo 91 — 장애 조사 잔여 통합

> `tasks/plan-91.md` · 모듈 id는 `CAPABILITY-MAP-91.md` 기준

- [x] **T1. `correlation-e2e-assertions`** — 스텁 경로 브리핑 계약 단언 + RUN_E2E 실 경로 테스트 1건(실행은 D-127)
  - Files: `sre_agent/tests/test_investigation_dispatcher.py` · `sre_agent/tests/test_investigation_e2e.py`
- [x] **T2. `change-event-overlay`** — 도구 앵커 인자 · 배치 말미 1건(플래그) · `kind="change"` · `change_finding` · "변경 직후" 가설
  - Files: `mcp_server/mcp_server/polestar_tools.py` · `sre_agent/sre_agent/{settings.py, application/evidence_prefetch.py, domain/correlation.py, application/briefing_builder.py, interface/mcp_service.py}` · 테스트 4파일
- [x] **T3. `related-host-consumption`** — `meta.root_resource_name` 소비(페이로드 1줄) · 연관 알람 1건 · `AlarmPoint.server` · notes
- [x] **T4. `investigation-feedback-ref`** — 피드백 레코드 `investigation_id`
- [x] **T5. `playbook-guidance`** — 51 §6 6유형 kind별 문구
- [x] **T6. `l3-postgate-enrichment`** — 60 §18 (나)안
- [~] **T7. `residual-doc-sync`** — 85/53/91 상태 · D-209 · INDEX(`-TODO`→`-WIP`) **2026-09-10 반영** · 잔여 = 이후 항목 완료마다
- [x] **T8. 인터뷰 확정 반영(2026-09-10)** — 1-8 domain 편입 · 1-9 SQL · 1-10 ③(`docs/27` §3.3) · 1-4 UI 노출 · 1-3 클러스터 폐기(실측) · 88 R-E (c)
- [~] **T9. 과금 실 실행(2026-09-10 · 승인 13+1건)** — R-E 재검증 2건 통과(`.env` true) · 88 단계 4·5·7 11건 전부 기대 일치 · 91 1-1 실 경로는 Gemini 무료 등급 RPM 쿼터로 실패(유료 키로 재시도 — 별도 승인)
