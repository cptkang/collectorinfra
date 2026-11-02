# plans/199 W0 화면 픽스처 (2026-10-29 · 8b32b317 기준)

조사 결과 한눈에 보기 카드·전달문·근거 요약·서술 보기(W1~W5)의 node 단위 테스트 입력이다.
형상 검사는 `tests/test_plan199_w0_fixtures.py`가 한다. `tests/fixtures/plan187_w5b/`(W5b 테스트 소유)는 건드리지 않았다.

## 만드는 법 — 생산 코드 산출(손 편집 없음)

```bash
.venv/bin/python tests/fixtures/plan199/make_fixtures.py   # 1단은 sre_agent/.venv로 자동 실행
```

1. `_gen_sre.py`(sre_agent 자체 venv) — `InvestigationDispatcher`를 plans/187 W0 녹화(`FakeCaller`)·가짜 소견기 LLM·
   가짜 HolmesGPT로 돌려 poll 응답(`job.summary()`)을 만든다. 망·LLM 호출 0.
2. `make_fixtures.py`(루트 venv) — 그 응답을 본체 경로로 통과시킨다: 트리거 접수 행(`InvestigationStore.record`) →
   `investigation_poll.poll_investigation`(가짜 조사 서비스 클라이언트) → 종결이면 저장(`clip_briefing` 32KB ·
   `render_briefing_lines`) → `investigation_view` + `_attach_live_progress`. 사용자 뷰는 본체 `_investigation_mask_fn`
   (기본 `mask_pattern`)을 그대로 쓴다. 고친 값은 `updated_at`(고정 시각) 하나뿐이다.

다시 만들면 벽시계 값만 달라진다 — `playbook.timings`·`sections[].elapsed_ms`·진행 중 `current_step`·실시간 배지의
「알람 후 N분」(evidence_missing S6). 테스트는 이 값들을 단언하지 않는다.

허브 실행 기록(`activity`)·KB 부록(`_kb_briefing_extras`)은 싣지 않는다. 판정 기록 요약 `alarm`은 `episode_scope`에만
있다(`with_alarm=1` 조회 모양 · 판정 기록에 `db_id`를 넣지 않아 `source` 문구 없음 — 레지스트리 비의존).

## 파일 — 키 `view`(운영자 뷰) · 일부 `user_view`(사용자 뷰) · `poll_view`(저장 전)

| 파일 | 케이스 |
|---|---|
| `confirmed_same_layer` | gc · 엔진 `same_layer`(gc) · 1순위 지지 → 「확정」 |
| `confirmed_cpu_ke` | case3 CPU · 계층 `cpu` · 알려진 오류 KE-DEMO-012 일치(1순위 고정 · 우회 있음) · 엔진 `not_comparable` |
| `evidence_missing` | case3 CPU · S6 결손인데 1순위 지지 · `evidence_steps`에 S6 → 「추정」 |
| `deep_skipped` · `deep_failed` | 심층 `skipped`(모드 off) · `failed`(예외) — 플레이북 1순위 지지 |
| `missing_many` | 폴스타 서버 미확정 — S4·S6 결손 · S3 생략 · 가설 없음 · 계층 `unknown` → 「미확정」 |
| `different_layer` | 플레이북 external ↔ HolmesGPT db · 「엔진 간 불일치」 |
| `non_playbook` | 종전 HolmesGPT 경로 — `playbook` 키 없음 · `briefing_lines` |
| `markdown_narrative` | 마크다운 답 11,000자대 → `poll_view`는 8,000자 + `…(이하 생략 — 원문 N자 중 앞 8000자)` · `view`(저장 통과본)는 32KB 상한으로 `deep_dive.narrative`가 빠진다 |
| `episode_scope` | 사건 종합 조사 `kind: episode` · `cross_source` · `alarm.related` · `user_view` |
| `user_view_secret` | 비밀값 꼴(`password=abc123` · Bearer JWT) 원문 `view` ↔ 마스킹 `user_view` 쌍 |
| `running_collect` · `running_deep` | 진행 중 — `progress`·`partial_briefing`(수집 4/10 · 심층 진행 중) |

리터럴은 전부 가짜다(plans/187 W0와 같은 호스트 `wasdemo01`·인스턴스 `A1` 가정).
