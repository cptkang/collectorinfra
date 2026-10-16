# 167. 로그 비밀값 가림 필터 — 로그 출력 단계에서 자격증명·토큰을 가린다 (기록)

> **작성일**: 2026-10-16 · **v1.0**(기록만 · 코드 0)
> **상태**: **TODO** — 사용자 지시 「별도 계획으로 기록만」(2026-10-16 인터뷰). 착수하려면 §4 결정 3건을 정하고 D-번호를 잡는다.
> **출처**: `plans/163` W1 security-auditor 잔여 Low 권고 「로그 비밀 마스킹 필터」(2026-10-14). 163은 화면·응답에서 예외 원문을 걷어냈지만(S3·S3+) **원문은 의도적으로 서버 로그에 남긴다**(진단용 — 163 S3 테스트가 caplog에 원문이 있음을 단언). 그 원문에 자격증명이 섞일 수 있다는 지적이다.
> **실측 기준**: RCA `3a0a1101`. `file:line`은 이 시점 값이다.
> **관련 결정**: D-337 ⑥(행동 변경은 구조 리팩터와 분리 — 이 계획은 로그 문자열이 바뀌는 행동 변경) · D-332 부기(사내망 `verify=False`는 결함 아님 — 이 계획 범위 밖) · D-070(사용자/운영자 시크릿 분리) · `docs/pii_filtering_rules.md`(PII 덤프 `logs/pii_block/`은 서버 밖으로 나가지 않음)
> **적용 스킬**: `.claude/skills/secure-coding/`

---

## 1. 현황 (2026-10-16 실측)

| 항목 | 실측 |
|---|---|
| 로깅 진입점 | 본체 `src/security/audit_logger.py:643` `setup_logging`(`logging.basicConfig(force=True)` + structlog) · `noise_gate/alarm_server/__main__.py:30` · `mcp_server/mcp_server/__main__.py:20` · `apm_gateway/apm_gateway/__main__.py:94` — 각자 `basicConfig` |
| 기존 로그 필터 | 0 (`logging.Filter`·`addFilter` 사용처 없음 — `tests/` 제외) |
| 비밀값을 직접 찍는 호출 | 0 — `token`·`api_key`·`password`·`secret`·`Authorization`이 든 로그 호출은 `mcp_server/__main__.py:51` 1건이며 on/off만 찍는다 |
| 남는 위험 | **예외 원문**: DB 드라이버·HTTP 클라이언트 예외 메시지에 DSN(`user:pw@host`)·URL 쿼리 키·`Bearer` 헤더가 실릴 수 있다. 163 S3/S3+ 이후 이 원문은 응답에서는 빠졌고 `logger.*(…, exc_info=…)`·`%s` 인자로 로그에만 남는다 |
| 응답 경로 | 163에서 처리 완료 — 범위 밖 |

## 2. 목표

로그 레코드가 핸들러에 닿기 전에 자격증명 형태 문자열을 가린다. 진단에 필요한 나머지 원문(예외 타입·호스트·SQL 상태 코드)은 남긴다.

## 3. 범위 초안

- **W0 실측**: 예외 원문에 비밀값이 실리는 경로를 드라이버별로 재현(asyncpg·ibm_db·aiomysql·httpx·redis — 로컬 샌드박스는 **읽기만** · 쓰기 시도 금지). 가릴 패턴 목록 확정(DSN `://user:pw@` · `Bearer …` · `api_key=`·`token=` 쿼리 · `.env` 키 이름 `*_API_KEY`·`*_SECRET*`·`*_PASSWORD` 값).
- **W1 구현**: 공용 `logging.Filter`(레코드 `msg`·`args`·`exc_text` 렌더 결과를 가림) 1개 → 진입점 4곳에 부착. 패키지 경계(D-139 — `mcp_server`·`apm_gateway`는 본체와 import 0)를 지키려면 패키지마다 사본을 두거나 패턴만 공유하는 방식을 §4에서 정한다.
- **검증**: 패턴별 가림 단언 · 163 S3 caplog 단언(원문 존재)이 비밀값이 아닌 부분으로 계속 통과하는지 · 성능(핫 경로 로그 비용) 측정.

## 4. 착수 전 결정할 것

| # | 질문 | 선택지 |
|---|---|---|
| 1 | 가림 위치 | (a) 핸들러 필터(전역 · 누락 없음) · (b) 예외 원문을 찍는 지점마다 헬퍼(정밀 · 누락 위험) |
| 2 | 패키지 경계 | (a) 패키지마다 필터 사본 + 사본 일치 테스트 · (b) 본체만 적용하고 `mcp_server`·`apm_gateway`는 기존 계약(비밀값 미로그) 유지 |
| 3 | 기존 로그 파일 | (a) 소급 처리 없음 · (b) 보관 로그 일괄 가림 스크립트 |

## 5. 범위 밖

- 사내망 클라이언트 `verify=False`(D-332 부기 — 의도)
- 응답·화면의 예외 원문(163에서 완료)
- `logs/pii_block/` 덤프(서버 밖 반출 없음 · 별도 규칙 `docs/pii_filtering_rules.md`)

---

## 변경 이력

- 2026-10-16 v1.0 — 최초 작성(기록만 · 코드 0). 163 auditor Low 권고를 별건으로 분리 — 사용자 결정 「별도 계획으로 기록만」.
