# 161. 저장소 제니퍼(APM) 소스 구성을 폐쇄망 운영 구성으로 일치

> **작성일**: 2026-10-13 · **v1.0**
> **상태**: **WIP — W1 구현·검증 완료 · 잔여 3건** — ① 매뉴얼 캡처 4장 재촬영(브라우저 있는 PC) ② D-329 주의 ⑤ 복합 단어 제외 꼬리·⑥ 「은행존이랑 공동존」 사용자 결정 ③ sre_agent 픽스처 1건 자체 venv 미실행 · 모듈 회귀 2300 통과 · 실패 2(원래 실패 — Windows CRLF)
> **계획 시작 SHA**: `4bf000b` → 2026-10-13 `origin/main` pull 뒤 `1181e9d` 위로 재적용(번호 156→161 · D-327→D-329 — origin `plans/156`·D-327 선점)
> **회귀 대상 파일**: `config/db_registry.yaml` · `docs/02_decision.md` · `docs/18_known_mistakes.md` · `docs/31_jennifer_integration_guide.md` · `docs/decisions/CHANGELOG.md` · `docs/decisions/D-322.md` · `docs/decisions/D-329.md` · `noise_gate/scripts/mock_polestar_events.py` · `noise_gate/testdata/cross_source/mock_events.jsonl` · `noise_gate/testdata/plan153/console_responses.golden.json` · `noise_gate/testdata/plan153/cross_source_incident.jsonl` · `noise_gate/testdata/plan153/decision_records.golden.json` · `noise_gate/testdata/plan153/trigger_payloads.golden.json` · `noise_gate/tests/test_mock_polestar_events.py` · `noise_gate/tests/test_plan144_cross_source.py` · `noise_gate/tests/test_plan144_w4.py` · `noise_gate/tests/test_plan153_w0_baseline.py` · `noise_gate/tests/test_plan87_apm_consumer.py` · `noise_gate/tests/test_plan87_j8_multi_source.py` · `plans/152-WIP-apm-query-benchmark.md` · `plans/161-WIP-apm-sources-production-ids.md` · `scripts/apm_bench/__main__.py` · `scripts/apm_bench/driver.py` · `scripts/apm_source_check.py` · `scripts/manual/apm_harness.py` · `scripts/manual/captures.yaml` · `scripts/manual/content/admin.md` · `scripts/manual/content/user.md` · `spec/SPEC-apm-gateway.md` · `spec/SPEC-apm-noise-gate.md` · `src/static/manual/admin.html` · `src/static/manual/user.html` · `sre_agent/tests/fixtures/plan153/noise_gate_episode_payloads.json` · `testdata/apm_bench/placeholders.e1.yaml` · `testdata/apm_bench/scenarios.yaml` · `tests/test_api/test_plan147_verify.py` · `tests/test_api/test_plan147_w2_route.py` · `tests/test_api/test_plan147_w3_scope.py` · `tests/test_api/test_plan147_w4_real_gateway.py` · `tests/test_api/test_plan87_j8_apm_alarm_zone.py` · `tests/test_orchestration/test_plan134_w34_apm.py` · `tests/test_orchestration/test_plan134_w34_verify_body.py` · `tests/test_orchestration/test_plan134_w34_verify_real.py` · `tests/test_orchestration/test_plan134_w34_verify_schema.py` · `tests/test_orchestration/test_plan134_w6_verify_real.py` · `tests/test_orchestration/test_plan147_w0_repro.py` · `tests/test_orchestration/test_plan147_w2_e2e.py` · `tests/test_orchestration/test_plan147_w2_ladder.py` · `tests/test_orchestration/test_plan147_w2_pipeline.py` · `tests/test_routing/test_plan147_source_select.py` · `tests/test_routing/test_plan87_j8_registry_sources.py` · `tests/test_scripts/test_apm_bench_cli.py` · `tests/test_scripts/test_apm_bench_driver.py` · `tests/test_scripts/test_apm_bench_judge.py`
> **요청(2026-10-13 · 사용자 확정 「A안」)**: 저장소 제니퍼 소스 구성(`bank`·`common`·`legacy` 잠정값 — D-322)을 폐쇄망 운영 구성(`b0`·`cm_prd`·`cm_stg`)으로 맞춘다.
> **관련 결정**: D-329(이 계획) · D-322(잠정 → 확정 부기) · D-287 · D-274(게이트웨이 경계) · D-271(DB 존 용어 — 제니퍼 소스 terms와 별개) · D-255(매뉴얼) · D-303(회귀)
> **관련 실수**: `docs/18` 2026-10-13 「폐쇄망 반입 목록에 `config/db_registry.yaml`을 넣어 운영 레지스트리를 덮어씀」

---

## 1. 배경

- 저장소 `config/db_registry.yaml` `solutions[apm].sources`는 D-322 구현 때 잠정값(`bank`·`common`·`legacy`)이었다.
- 폐쇄망은 `b0`·`cm_prd`·`cm_stg`를 쓰고, 게이트웨이 `apm_gateway/.env` `JENNIFER_SOURCES`도 이 id다. 저장소 레지스트리 반입이 운영 값을 덮어 깨졌다.
- 저장소를 운영 구성에 맞춰 두면 다음 반입·벤치(plans/152 W8)에서 id가 어긋나지 않는다.

## 2. 확정값 (사용자 확정 2026-10-13)

```yaml
    sources:
      - {id: b0, label: 은행 제니퍼, zone: bankjon,
         terms: ["은행존", "은행", "은행 제니퍼", "레거시", "레거시 제니퍼"]}
      - {id: cm_prd, label: 공동존 운영 제니퍼, zone: gongjon,
         terms: ["공동존 운영", "김포", "운영", "DR", "공동존 운영 제니퍼"]}
      - {id: cm_stg, label: 공동존 개발/스테이징 제니퍼, zone: gongjon,
         terms: ["공동존 개발", "개발", "스테이징", "여의도", "공동존 개발/스테이징 제니퍼"]}
```

- `legacy` 소스는 없어지고 「레거시」는 `b0`의 단어다.
- 「공동존」 단독은 어느 소스의 단어도 아니다(무지목 → D-322 되묻기).
- 「DR」은 `cm_prd`에 둔다 — D-271(DB 존 용어 DR = 여의도)과 다르지만 제니퍼 소스 terms는 DB 존 용어와 별개다(사용자 값 · D-329).

## 3. 범위

| 묶음 | 대상 |
|---|---|
| 레지스트리 | `config/db_registry.yaml` sources 블록(팀 리드) |
| 본체·패키지 | 소스 id 하드코딩이 있으면 레지스트리 파생 — 실측 결과 `src/`는 0건. `noise_gate/scripts/mock_polestar_events.py` 제니퍼 소스 id 상수 |
| 테스트 | 실 레지스트리를 읽고 소스 id·라벨·단어를 단언하는 테스트 |
| 벤치 | `testdata/apm_bench/scenarios.yaml` APM-SRC-* · `placeholders.e1.yaml` `sources`/`answers` · 벤치 테스트 |
| 매뉴얼(D-255) | `scripts/manual/content/{user,admin}.md` 제니퍼 소스 절 · `scripts/manual/apm_harness.py` · `captures.yaml` |
| 문서 | `docs/31` · `spec/SPEC-apm-gateway.md` · `spec/SPEC-apm-noise-gate.md` · `plans/152`의 현행 구성 서술(이력 서술은 유지) |
| 범위 밖 | `zone_groups`의 `bank`·`common`(폴스타 존 그룹 — APM 소스 아님) · `apm_gateway/` 테스트 픽스처(게이트웨이 설정값 — D-274) |

## 4. 수용 기준

- AC-1 레지스트리 sources가 §2와 같다.
- AC-2 저장소에 APM 소스 id 의미의 `bank`·`common`·`legacy`가 정본처럼 남지 않는다(이력 서술·게이트웨이 독립 픽스처 제외).
- AC-3 APM-SRC 시나리오 기대값이 `select_apm_sources` 실측과 일치하고, 시나리오 수·split·covers 유지 · `--dry-run`·`--coverage` exit 0.
- AC-4 E1 소스 시나리오 종단 실행에서 소스 축 통과.
- AC-5 바꾼 파일 모듈 단위 회귀 rc 0(원래 실패만).
- AC-6 매뉴얼 제니퍼 소스 절이 새 단어·라벨과 일치.

## 5. Wave

- W1(단일 Wave): 레지스트리 교체 → 본체·테스트·매뉴얼 / 벤치 / 문서 병렬 → 계획 끝 회귀 1회 → 검증.
