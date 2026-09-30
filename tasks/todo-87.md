# TODO 87 — 제니퍼 APM 연동 J1~J4

> 계획 `tasks/plan-87.md` · 맵 `spec/CAPABILITY-MAP-87.md` · 보류: J5 · J6 · J7 · J0-L-b · J0-O(맵 착수 판정 표)

- [x] T-01 게이트웨이 패키지 골격 — `apm_gateway/pyproject.toml` · `.env.example` · 정책 yaml 3종 · `config.py`
  - Acceptance: 자체 cwd · 루트 venv · `mcp<2` · `.env`는 기존 키를 덮어쓰지 않음 · 폴스타 DB 연결 문자열 0건 · `.env.example` 키 커버리지
  - Verify: `tests/test_config.py` · `tests/test_boundary.py::test_no_polestar_db_connection_settings`
- [x] T-02 허용목록 정본 + 클라이언트 — `adapters/jennifer/allowlist.py` · `client.py`
  - Acceptance: §5.2(e) 거부 입력 23건 + v1 POST 변형 + 쿼리 `token` → HTTP 0회 · 3xx 비추종 · 크기 상한 · 토큰 0회 · 본문 기준 오류 분류 · 카탈로그 사본 대조
  - Verify: `tests/test_allowlist.py` · `tests/test_client.py`
- [x] T-03 벤더 필드 매핑·조회 함수 — `adapters/jennifer/fields.py` · `api.py`
  - Acceptance: 벤더 경로·필드명은 `adapters/jennifer/` 밖에 0건 · 지표 식별자 두 체계 매핑 표 · 이벤트 유형 접두 정규화
  - Verify: `tests/test_boundary.py::test_vendor_literals_only_in_adapter` · `tests/test_signals.py`
- [x] T-04 정합 — `application/resolver.py`
  - Acceptance: override → `hostName`(FQDN·대소문자) → exact → prefix/regex · 미매칭 `instance_unresolved` · 도메인 0건·전 도메인 실패 `source_unavailable` · 최대 5 인스턴스
  - Verify: `tests/test_tools_contract.py`(정합 4건)
- [x] T-05 WAS 판정 — `domain/signals.py` · `config/was_signatures.yaml`
  - Acceptance: 목업 시나리오 6종 + 스레드 정체 · 오류 급증 · 경계 음성 · 임계 파일 = 기본값
  - Verify: `tests/test_signals.py`
- [x] T-06 도구 8종 + `gateway_health` — `application/tools.py` · `application/masking.py`
  - Acceptance: 반환·오류 계약 · 1분 분할 상한 10분 · 과거 기준시각·창 초과 `[한계]` · 마스킹 · `profile_ref` 누락/불일치 · 조사당 프로파일 상한 · `/__mock/hits` 허용목록 밖 0 · 토큰 0회
  - Verify: `tests/test_tools_contract.py`
- [x] T-07 MCP 서버·Bearer·감사·엔트리 — `interface/server.py` · `interface/audit.py` · `__main__.py`
  - Acceptance: 도구 표면 `apm_*` 8 + health · 벤더 중립 설명 · Bearer 401 · 감사에 조사 id·thread_id · SSE 실기동
  - Verify: `tests/test_server.py` · 수동 스모크(게이트웨이 + 목 서버 + MCP SSE 클라이언트)
- [x] T-08 이벤트 폴러 — `application/poller.py` · `domain/events.py` · `config/event_levels.yaml`
  - Acceptance: 계약 페이로드 · 멱등(재조회·재기동·같은 ms) · 지표 이벤트 `metricsName` · 최소 레벨(해소 통과) · 미접속 커서 유지·백오프 · 계약 위반 중지 · XADD 실패 재시도
  - Verify: `tests/test_poller.py`
- [x] T-09 `sre_agent` 소비측(J3 · 병렬 작업자)
  - Acceptance: `SPEC-apm-sre-agent.md` §4
  - Verify: `cd sre_agent && .venv/bin/python -m pytest tests -q`(747 passed · 3 skipped · 2026-09-29) · `test_apm_consumer.py` · `test_apm_was_scenarios.py`
- [x] T-10 `noise_gate` 소비측(J4 · 병렬 작업자)
  - Acceptance: `SPEC-apm-noise-gate.md` §4
  - Verify: `noise_gate/tests/test_plan87_apm_consumer.py` · `test_plan60_flags_off_regression.py` · 루트 pytest
- [x] T-11 품질 게이트 — ruff · mypy(게이트웨이) · `arch_check --ci` · `overfit_check --ci`(게이트웨이 편입 · 어댑터 제외 · 기준선 무변경)
- [x] T-12 문서 — `docs/31` v4 · 계획서 `-WIP`·§0.11·§13 · `plans/INDEX.md` · D-195 부기 · `docs/18`
- [ ] T-13 `CLAUDE.md` 「저장소 지도」·「패키지 경계」 — **미반영**(에이전트 지시만으로 설정 파일을 바꾸지 않는다 · 초안 패치를 팀 리드에게 전달)

## 잔여(이 작업 밖)

| 항목 | 막는 것 |
|---|---|
| J0-L-b 실데이터 녹화 → 합성 픽스처 교체 | 평가판 라이선스(사용자 할 일 1·2·9) |
| J0-O 운영 실측(U-4·U-5·U-6·U-10·U-12·U-13) | 운영 접근 권한·테스트 토큰 |
| `was_object` 정합 브릿지 · `polestar_was_instances` | U-10 |
| J5 · J6 · J7 | `plans/121` 처리기 · G-6 목업 검증 · G-9 소비자 확정 |
| 실 LLM(로컬 MLX) APM 조사 완주 | 결정적 테스트로 수용 — 필요 시 별도 실행 |

---

# TODO 87 — J8 다중 제니퍼 소스 (2026-09-30 · D-287)

- [x] T-14 게이트웨이 설정 — `config.py`(`JENNIFER_SOURCES` · `JENNIFER_<ID>_*` · 단일 설정 = 소스 `default`) · `domain/sources.py` · `__main__.py`(설정 오류 = 종료 코드 2) · `.env.example`
  - Acceptance: 동시 설정·필수 키 누락·id 형식/예약어/중복·비배열 → 기동 실패 · 선택 키 전역 상속 · 기동 로그에 id·설정 여부만 · 정합 파일의 미설정 소스 경고
  - Verify: `apm_gateway/tests/test_config.py`
- [x] T-15 소스 묶음·정합 — `application/sources.py` · `application/resolver.py`(소스 하나 · 실패 인벤토리 비예외 · 30초 캐시 · `overrides[].source_id` · `per_source.<id>.match_rules`)
  - Acceptance: 부분 실패 `[한계]`+`sources[]` · 전부 실패·전부 도메인 0건 오류 · F-3 30초 재조회 · 한 hostname 두 소스 정합
  - Verify: `apm_gateway/tests/test_multi_source.py`
- [x] T-16 도구·서버·감사 — `application/tools.py` · `interface/server.py` · `interface/audit.py` · `domain/signals.py`
  - Acceptance: 계약 추가만(`source_ids` 7종 · `source_id` 프로파일 · 행·`profile_ref`·`instance_refs`·`sources[]`) · 호출이 그 소스 서버로만 · 토큰 교차 0 · 거부 입력 소스마다 HTTP 0 · 감사 `sources=`
  - Verify: `apm_gateway/tests/test_multi_source.py` · `test_tools_contract.py` · `test_server.py`
- [x] T-17 폴러 — `application/poller.py` · `domain/events.py`
  - Acceptance: 두 소스 같은 값 이벤트 모두 발행 · 커서 키 `…:cursor:<source_id>:<domain_id>` · 재기동 중복 0 · `dbId` `jennifer_<id>`(default `jennifer`) · `apm.source_id` · `resourceAncestry`
  - Verify: `apm_gateway/tests/test_multi_source.py` · `test_poller.py`
- [x] T-18 목 서버 Bearer 지문 — `testdata/jennifer/scripts/mock_openapi.py`(`bearer_fp`)
- [x] T-19 레지스트리·존 판정(F-7) — `config/db_registry.yaml` · `src/routing/registry.py` · `src/routing/zones.py`
  - Acceptance: 3소스·존 · 검증 실패 `RegistryError` · 미지 소스 경고 1회 · 존 구독자 수신·ack · 다른 존 미수신 · 존 없는 소스 전 존·관리자만 · 프롬프트 렌더 바이트 불변
  - Verify: `tests/test_routing/test_plan87_j8_registry_sources.py` · `tests/test_api/test_plan87_j8_apm_alarm_zone.py`
- [x] T-20 `noise_gate`·`sre_agent` 소비측
  - Acceptance: `SPEC-apm-noise-gate.md` §4 [J8] · `SPEC-apm-sre-agent.md` §4 [J8]
  - Verify: `noise_gate/tests/test_plan87_j8_multi_source.py` · `sre_agent/tests/test_plan87_j8_source_id.py`
- [x] T-21 실프로세스 검증 — 목 서버 2개 25항목 · 로컬 Docker 두 소스 10항목 · `RUN_DOCKER_IT=1` 4 passed(계획서 §0.14)
- [x] T-22 문서 — SPEC 3종 · `docs/31` · 관리자 매뉴얼(A-30 · 9.5) · 계획서 §0.14·§13 · `plans/INDEX.md` · D-287 부기

## 잔여(J8 밖)

| 항목 | 막는 것 · 소유 |
|---|---|
| 위치어 → 소스 좁히기(G-16) · 승계 패싯 `apm_source_id` · 첫 홉 `source_id` 보존 | `plans/125`(D-281) — 통지 행 |
| 소스별 J0-O(버전·16경로·망 도달·도메인 id·명명 규칙 · 레거시 4.x 여부 R-30) | 운영 접근 |
| `per_source` 정합 규칙 값 | J0-O 명명 규칙 실측 |
| F-4(noise_gate 401 사유) | 사용자 결정 대기 |
