---
name: secure-coding
description: 이 저장소(LLM 생성 SQL·MCP DB 접근·JWT 사용자/운영자 분리·PII 마스킹·감사 로그)의 보안 규칙으로 코드를 쓰고 점검·강화한다. 인증·토큰 검증, SQL 실행 경로·MCP 도구 추가, API 오류 응답, 설정·시크릿·LLM 클라이언트, 로그·PII, 프롬프트에 외부 데이터를 넣는 코드를 만들거나 고칠 때, 「보안 점검」「시큐어 코딩」「취약점」「plans/163」처럼 보안 강화를 할 때 쓴다. 동작이 바뀌는 보안 수정은 구조 리팩터와 섞지 않는다(D-337 ⑥).
---

# 시큐어 코딩

이 시스템의 핵심 위험은 **LLM이 만든 SQL을 DB에 보낸다**는 구조 자체다(Pedro 외 ICSE 2025 — Prompt-to-SQL 주입). LLM 출력·DB 값·알람 본문·업로드 문서는 모두 **비신뢰 입력**으로 다룬다(OWASP LLM05:2025). 표준 조항 원문·서지는 `references/standards.md`.

## 언제

- 새로 만들거나 고칠 때: 토큰 발급·검증·역할 판정 · SQL을 만들거나 실행하는 코드(노드·`db_adapters`·`mcp_server` 도구) · API/SSE 오류 응답 · 설정 필드·LLM/외부 클라이언트·시크릿 · 로그·감사·PII · 프롬프트에 외부 데이터를 넣는 코드 · 파일 업로드·DRM.
- 점검·강화할 때: `plans/163`(S1·S2·S3·X6) 또는 보안 감사 요청.
- 구조 리팩터 중에는 보안 동작을 **바꾸지 않는다** — 발견하면 기록만 하고 별도 계획으로(D-337 ⑥). 리팩터가 보안을 저절로 좋게 만들지 않는다는 실증도 있다(Iannone 외 2023).

## 절차

1. **결정 확인**: D-003(3중 읽기 전용 — 절대 변경 불가, 강화만) · D-070(운영자 토큰 `type` 검증 · 사용자/운영자 시크릿 분리) · D-139/D-274(패키지 경계 — 제니퍼 토큰은 `apm_gateway`에만). `grep -n '<키워드>' docs/02_decision.md`로 더 찾는다.
2. **스캔**: `python .claude/skills/secure-coding/scripts/secure_scan.py [경로…] [--only R1,R2]` — 결과는 검토 후보다. 하나씩 코드로 판정하고, 의도된 것(도메인 예외 메시지·플래그로 제어되는 TLS 예외)은 근거를 적어 둔다.
3. **바뀌는 동작을 단언하는 테스트를 먼저**(실패 확인) — 정상 경로보다 **거부 경로**를 쓴다: 다른 종류 토큰 → 401 · 쓰기 SQL → 모든 진입점에서 차단 · 내부 예외 → 응답에 원문 없음 · 필수 설정 비어 있음 → 기동 실패.
4. 고친다. 아래 영역별 규칙을 따른다.
5. 검증 라운드에 **security-auditor를 verifier와 병렬로** 띄운다(`plans/163` §2.0).
6. 기록: 정책이 바뀌면 D-번호 · 실수였으면 `docs/18_known_mistakes.md` · 화면 문구가 바뀌면 매뉴얼(D-255).

## 영역별 규칙

### A. DB 읽기 전용 · SQL (D-003 · 근거: ASVS v5.0.0-1.2.4·13.2.2 · CWE-89·CWE-250 · LLM05·LLM06:2025 · Saltzer & Schroeder 완전 중재·최소 권한)

- **3중 방어를 모두 유지**: ① 프롬프트 「SELECT만」 ② `src/security/sql_guard.py`(`SQLGuard.is_safe_select` — 금지 키워드·인젝션 패턴·다중 문장)와 `query_validator` ③ DB 접근 계층 readonly(MCP 서버). LLM은 프롬프트를 무시할 수 있으므로 ②③이 본체다.
- **완전 중재**: 검사는 실행 함수 바로 앞 **한 지점**에 둔다. 도구·헬퍼마다 따로 부르면 새 경로가 우회한다(현행 결함 — `mcp_server`는 `validate_readonly`가 `execute_sql` 하나뿐이고 실행 22곳이 `db.py` `PoolManager.execute`로 직행 · `plans/163` S1).
- **최소 권한이 본질**: 접속 계정은 SELECT 권한만. 세션 읽기 전용(PG `default_transaction_read_only`·`SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY` · MariaDB `START TRANSACTION READ ONLY` · Db2 `ReadOnlyConnection`)은 **보조**다 — PG는 같은 세션의 `SET TRANSACTION READ WRITE`로 되돌릴 수 있다(문서 사실 · 「권한 분리가 본질」은 그로부터의 설계 해석).
- **값은 파라미터, 식별자는 허용 목록**: 사용자·LLM이 준 값을 SQL 문자열에 이어 붙이지 않는다. 테이블·컬럼·정렬 키처럼 파라미터화할 수 없는 식별자는 스키마 카탈로그 허용 목록으로 검증한다(ASVS 1.2.4 비고). 스키마 이름은 엔진 규칙대로(DB2 `POLESTAR` 대문자).
- 생성 SQL은 실행 전 검증(구문·안전성·참조 테이블/컬럼 존재·LIMIT · 기본 1000). DB 레벨 timeout·max_rows는 MCP 서버 소관.
- **쓰기 기능은 어떤 요건이 와도 만들지 않는다**(D-003). 제니퍼 L2 조치 실행은 DB 쓰기가 아니라 D-195 ③ 소관.

### B. 인증·인가 (D-070 · 근거: RFC 8725 §2.8·§3.11·§3.12 · RFC 7519 §5.1·§7.2 · ASVS v5.0.0-9.1.1·9.1.2·9.2.1·9.2.2 · CWE-345·CWE-287·CWE-863 · Top 10:2025 A01·A07)

- `jwt.decode`는 **알고리즘 목록을 고정**해 부른다(`algorithms=["HS256"]`). 서명 검증 끄기 금지.
- 서명 통과 ≠ 인가. 디코드 뒤 **토큰 종류(`type`)와 역할을 명시 검증**한다. 종류별 검증 규칙은 서로 배타적이어야 한다(RFC 8725 §3.12) — `typ`는 라이브러리가 아니라 애플리케이션이 검사한다(RFC 7519 §5.1). 현행 결함: 사용자 쪽 `_verify_user_token`(`src/api/dependencies.py:31-53`)이 `type`을 보지 않는다(`plans/163` S2).
- 사용자·운영자 **시크릿 분리**(D-070)를 유지하고, 두 값이 같게 설정되면 기동을 막는 가드를 둔다(현재 없음).
- 역할 판정은 단일 술어(`is_admin(user)` 등)로 — `"admin"` 리터럴 비교를 라우트마다 쓰지 않는다.
- 인증 UX는 미로그인 첫 방문 경로로 확인한다(Known Mistakes).

### C. 오류 처리 (근거: ASVS v5.0.0-16.5.1·16.5.3·16.5.4 · CWE-209·CWE-636 · Top 10:2025 A10 · OWASP Error Handling Cheat Sheet)

- 사용자 응답에는 **고정 문구 + 상관 ID**. 예외 원문(`str(e)`·`{e}`)은 `logger.exception`으로 서버 로그에만 — DSN·SQL·경로·외부 API 응답이 섞인다. 현행: `query.py`·`apm_jobs.py`·`conversation.py`·`admin.py` 등(`plans/163` S3).
- 사용자에게 보여 줄 메시지가 필요하면 **안전 문구만 담는 도메인 예외**를 정의하고 그 메시지만 내보낸다(`JobNotFound` 등은 허용 사례).
- `degraded_reason`을 붙이는 처리기의 `error`·`final_response`는 **고정 문구 + 요청 번호**, 원문은 로그에만 — `src/utils/user_error.py`가 `degraded_reason` 붙은 결과 문구를 처리기 문구로 믿고 화면에 그대로 낸다(plans/184 S6).
- **실패 시 닫힌다**(fail-closed): 검증·인가 단계의 예외를 삼키고 계속 진행하지 않는다. 침묵 폴백 금지 — 실패 사유를 구조화해 응답에 노출하고 원문은 로그로(CLAUDE.md).

### D. 설정·시크릿·외부 연결 (근거: CWE-1188·CWE-636 · ASVS v5.0.0-13.2.3·13.2.4·13.3.1·16.5.3 · NIST SSDF PW.9 · Saltzer & Schroeder fail-safe defaults)

- 설정은 `AppConfig`(`cfg.<그룹>.<필드>`)로만 읽는다. `os.getenv`로 설정을 판단하지 않는다 — `.env`는 `os.environ`에 없다(Known Mistakes). 클라이언트는 받은 인자만 쓴다.
- **필수 값이 비면 조용히 기본 엔드포인트로 가지 말고 생성 시점에 실패**한다(현행 결함 — `fabrix_client.py:54-56` `localhost:8002` 폴백 · `ollama_client.py:52` 셸 env가 인자보다 우선 · `plans/163` X6).
- **사내망 구간은 TLS를 요구하지 않는다**(사용자 확정 2026-10-14 · D-332 부기). FabriX 클라이언트(`fabrix_kbgenai.py` 4곳·`fabrix_retrieval.py:272`)와 폴스타 REST의 `verify=False`는 의도된 것이다 — 결함으로 보고하지 않는다. 사내망 **밖**으로 나가는 연결(과금 외부 API 등)의 TLS 검증은 끄지 않는다. 플래그로 끄는 경로(`llm.py:279` `config.orchestrator.verify_ssl`)는 경고 로그를 유지한다.
- 시크릿은 `.env`/`.encenv`·SecretStr로. 코드·로그·예외 메시지·테스트 픽스처에 넣지 않는다. 과금 외부 API 키가 있어도 키 존재만으로 호출 경로가 열리지 않게 옵트인 뒤에 둔다(D-127·D-240).

### E. 로그·감사·PII (근거: ASVS v5.0.0-16.2.5·16.3.2~16.3.4·16.4.1 · CWE-532 · LLM02:2025 · OWASP Logging Cheat Sheet)

- 토큰·비밀번호·DSN·키는 로그에 남기지 않는다. 행 데이터는 `data_masker.py`(민감 컬럼·IP·이메일 부분 마스킹)를 거친다.
- 외부 LLM으로 나가는 텍스트는 `pii_filter.py` 규칙(`docs/pii_filtering_rules.md`)을 지킨다. 차단 진단 덤프는 `logs/pii_block/`에만 — 서버 밖으로 내보내지 않는다.
- 모든 질의 실행·인가 실패·통제 우회 시도는 감사 로그 대상(`src/security/audit_*`·`audit_middleware.py`). 로그에 사용자 입력을 넣을 때는 개행·제어문자를 인코딩한다(로그 인젝션).

### F. LLM 특화 (근거: OWASP Top 10 for LLM Applications 2025 LLM01·LLM05·LLM06 · Greshake 외 2023 · Pedro 외 2025)

- DB 값·알람 본문·업로드 문서·외부 도구 응답이 프롬프트에 들어가면 **간접 프롬프트 주입** 경로다. 그 출력이 하는 일(SQL 실행·도구 호출)은 결정적 가드가 막아야 한다 — 프롬프트 지시로 막지 않는다.
- LLM 출력(분류·매핑·SQL·도구 인자)은 결정적 검증 뒤에만 쓴다. 고정 형태 쿼리는 코드가 SQL을 직접 조립한다(CLAUDE.md).
- 에이전트·도구 권한은 최소로(LLM06 Excessive Agency): MCP 도구는 읽기 전용, 조치 실행 평면은 별도 통제(D-195 ③).
- LLM 자동 등록(유사어 등)은 쓰기 지점에서 결정적으로 차단한다(오염 자기강화).

## 하지 말 것

- 보안 수정을 리팩터 커밋에 섞기(D-337 ⑥) · 쓰기 허용 방향의 어떤 변경(D-003) · 서명만 보고 인가 판단 · 예외 원문 응답 · `os.getenv` 설정 판단 · 하드코딩 `verify=False` · 시크릿·토큰 로그 · 프롬프트 지시만으로 안전 보장.
- 과금 외부 API(Gemini 등)·가드 해제 `RUN_E2E=1`은 건마다 사용자 승인. 보안 검증용 실 LLM 호출은 로컬 MLX로.

## 보고

바꾼 파일 · 영역(A~F) · 바뀌는 동작과 그 단언 테스트 · 스캐너 판정 결과(후보 수 · 실결함 · 의도 확인) · security-auditor 지적 처리 · 관련 표준 ID(버전 접두 포함, 예: `ASVS v5.0.0-9.2.2`) · 매뉴얼 반영 여부.
