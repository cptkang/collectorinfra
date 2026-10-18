# 시큐어 코딩 근거 — 표준·문헌

조사일 2026-10-14. 표준은 발행 기관 원문 페이지(owasp.org·top10.owasp.org·genai.owasp.org·cwe.mitre.org·rfc-editor.org·csrc.nist.gov·ASVS GitHub v5.0.0 태그)에서, 논문은 Crossref·doi.org로 확인했다. **요구 칸은 원문 전문이 아니라 한국어 요약이다** — 문구를 인용하려면 원문을 대조한다. 판마다 조항 ID가 바뀌므로 **버전 접두를 붙여 인용**한다(예: `ASVS v5.0.0-9.2.2`). ASVS 4.0.3 번호와 섞지 않는다.

층: **S** 표준 · **P** 동료심사 · **V** 벤더 문서 · **D** 초안

## 1. 현행 결함 ↔ 근거

| 결함(`plans/163`) | 근거 |
|---|---|
| S1 MCP 읽기 전용 검사가 한 진입점뿐 · 세션 읽기 전용 없음 | ASVS v5.0.0-1.2.4·13.2.2·15.2.5 · CWE-89·CWE-250 · Top 10:2025 A05 · LLM05·LLM06:2025 · Pedro 외 2025 · Saltzer & Schroeder 1975(완전 중재·최소 권한) · OWASP SQL Injection Prevention CS(읽기 전용 계정) |
| S2 사용자 JWT `type` 미검증 | RFC 8725 §2.8·§3.11·§3.12 · RFC 7519 §5.1 · ASVS v5.0.0-9.2.2 · CWE-345·CWE-287·CWE-863 · Top 10:2025 A01·A07 |
| S3 예외 문자열 응답 노출 | ASVS v5.0.0-16.5.1·16.5.4·13.4.2 · CWE-209 · Top 10:2025 A10 · OWASP Error Handling CS |
| X6 설정 비면 localhost 폴백 · env가 인자보다 우선 | CWE-1188·CWE-636 · ASVS v5.0.0-16.5.3·13.2.3·13.2.4 · Top 10:2025 A02·A10 · NIST SSDF PW.9 · Saltzer & Schroeder(fail-safe defaults). **X6을 직접 겨냥한 단일 조항은 없다** — 조합 근거 |

## 2. 표준

| 서지·버전 | 층 | 조항 | 요구(요약) |
|---|---|---|---|
| OWASP ASVS 5.0.0 (2025-05). https://github.com/OWASP/ASVS 태그 v5.0.0 `5.0/en/` | S | 9.1.1 (L1) | 자기완결 토큰은 신뢰 전에 서명·MAC 검증 |
| 〃 | | 9.1.2 (L1) | 허용 알고리즘 목록 고정 · unsigned 토큰 거부 |
| 〃 | | 9.2.1 (L1) | 유효 기간(nbf·exp) 안에서만 수용 |
| 〃 | | **9.2.2** (L2) | 토큰이 올바른 **종류**이고 현재 용도에 맞는지 확인한 뒤 내용 사용 |
| 〃 | | 9.2.3 (L2) | aud가 이 서비스인 토큰만 수용 |
| 〃 | | 1.2.4 (L1) | DB 질의는 파라미터화 등으로 인젝션 방어. 식별자(테이블·컬럼·ORDER BY)는 허용 목록 등 다른 통제 |
| 〃 | | 13.2.2 (L2) | 백엔드 통신은 최소 권한 계정으로 |
| 〃 | | 13.2.3 (L2) | 서비스 자격증명이 기본값이 아닐 것 |
| 〃 | | 13.2.4·13.2.5 (L2) | 통신 가능한 외부 자원 허용 목록 |
| 〃 | | 13.3.1·13.3.2 (L2) | 시크릿 관리 솔루션·소스 외부 보관 / 시크릿 접근 최소 권한 |
| 〃 | | 13.4.2 (L2) | 운영에서 디버그 모드 해제 |
| 〃 | | 15.2.5 (L3) | 위험 기능은 샌드박스·격리 등 추가 보호 |
| 〃 | | 16.2.5 (L2) | 로그 민감 데이터 금지·마스킹 |
| 〃 | | 16.3.2·16.3.3·16.3.4 (L2) | 인가 실패 · 통제 우회 시도 · 예기치 않은 오류·통제 실패 로깅 |
| 〃 | | 16.4.1 (L2) | 로그 인젝션 방지 인코딩 |
| 〃 | | **16.5.1** (L2) | 예기치 않은·민감한 오류에는 일반 메시지(스택·쿼리·키·토큰 노출 금지) |
| 〃 | | 16.5.2 (L2) | 외부 자원 실패 시에도 안전하게 계속 동작 |
| 〃 | | **16.5.3** (L2) | 예외 시 안전하게 실패(fail-open 방지) |
| 〃 | | 16.5.4 (L3) | 최후 예외 핸들러 · 상세는 로그 |
| OWASP Top 10:2025. https://top10.owasp.org/2025 | S | A01 Broken Access Control · A02 Security Misconfiguration · A04 Cryptographic Failures · A05 Injection · A07 Authentication Failures · A09 Security Logging and Alerting Failures · **A10 Mishandling of Exceptional Conditions**(신설) | 2021판과 번호가 다르다(Injection 2021 A03 → 2025 A05). 2025를 기본으로 인용 |
| OWASP Top 10 for LLM Applications 2025. https://genai.owasp.org/llm-top-10/ | S | LLM01 Prompt Injection · LLM02 Sensitive Information Disclosure · **LLM05 Improper Output Handling** · LLM06 Excessive Agency | LLM05: 모델 출력을 하류(DB 등)로 넘기기 전 비신뢰로 검증, DB 연산은 파라미터화 |
| MITRE CWE (v4.20). https://cwe.mitre.org | S | CWE-89 SQL Injection · CWE-209 Error Message Containing Sensitive Information · CWE-250 Execution with Unnecessary Privileges · CWE-287 Improper Authentication · CWE-345 Insufficient Verification of Data Authenticity · CWE-347 Improper Verification of Cryptographic Signature · CWE-532 Sensitive Information in Log File · CWE-636 Not Failing Securely · CWE-863 Incorrect Authorization · CWE-1188 Insecure Default · CWE-1426 Improper Validation of Generative AI Output | CWE-1426은 실취약점 매핑에 「DISCOURAGED」 — 실결함 분류에는 CWE-77/94/116 등 구체 ID를 쓴다. S2는 CWE-345·287·863이 직접 대응(CWE-347은 서명 검증 자체 결함일 때) |
| CWE Top 25 2025. https://cwe.mitre.org/top25/archive/2025/2025_cwe_top25.html | S | CWE-89 2위 · CWE-862 4위 · CWE-863 17위 | CWE-209·636·1188·347·287·532는 Top 25 밖 |
| RFC 8725 JSON Web Token Best Current Practices (BCP 225, 2020-02). https://www.rfc-editor.org/rfc/rfc8725 | S | §2.8 Cross-JWT Confusion · §3.1 Algorithm Verification · §3.8 Issuer·Subject · §3.9 Audience · **§3.11 Use Explicit Typing** · **§3.12 Mutually Exclusive Validation Rules** | 한 용도 토큰을 다른 문맥에서 쓰는 혼동을 막는다 · 종류별 검증 규칙은 서로 배타적으로 |
| RFC 7519 JSON Web Token (2015-05). https://www.rfc-editor.org/rfc/rfc7519 | S | §5.1 "typ" · §7.2 Validating a JWT · §4.1.3 "aud" | `typ`는 라이브러리가 아니라 애플리케이션이 처리 |
| NIST SP 800-218 SSDF v1.1 (2022-02). doi:10.6028/NIST.SP.800-218 | S | PW.1 · PW.5.1 · PW.7 · PW.8 · **PW.9**(보안 기본 설정) · RV.1~RV.3 | SP 800-218A(2024-07, 생성형 AI 프로파일) doi:10.6028/NIST.SP.800-218A |
| NIST SP 800-218 Rev.1 (SSDF v1.2) IPD (2025-12) | D | — | **초안** — 최종본 미확인 |
| OWASP Cheat Sheet Series. https://cheatsheetseries.owasp.org/cheatsheets/ | S | Error_Handling · SQL_Injection_Prevention · Logging · Secrets_Management | Secrets Management CS는 기본값·실패 동작을 다루지 않는다(X6 근거로 쓰지 않는다) |

## 3. DB 읽기 전용 — 벤더 문서

| 엔진 | 문서 | 내용 | 한계 |
|---|---|---|---|
| PostgreSQL | https://www.postgresql.org/docs/current/runtime-config-client.html (`default_transaction_read_only`) · https://www.postgresql.org/docs/current/sql-set-transaction.html | 읽기 전용 트랜잭션은 DML·DDL·GRANT 등 거부. `ALTER ROLE`/`ALTER DATABASE` 수준 설정 가능 | **같은 세션에서 `SET TRANSACTION READ WRITE`로 되돌릴 수 있다** · 문서 스스로 「디스크 쓰기를 모두 막는 것은 아님」 → 롤 권한(SELECT만)이 본질(설계 해석) |
| MariaDB | https://mariadb.com/kb/en/start-transaction/ | `START TRANSACTION READ ONLY` · 기본값은 `tx_read_only` | 세션 내 되돌림 여부 미확인 |
| MySQL 8.4 | https://dev.mysql.com/doc/refman/8.4/en/commit.html | `START TRANSACTION READ ONLY` — 영속 테이블 수정·잠금 거부 | — |
| Db2 LUW 11.5 | https://www.ibm.com/docs/en/db2/11.5?topic=keywords-readonlyconnection | 드라이버 키워드 `ReadOnlyConnection=1` | 서버 강제인지 힌트인지 문서에 없음 → 보조 통제로만 · GRANT SELECT 병용 |

## 4. 문헌

| 서지 | 층 | 요지 |
|---|---|---|
| Saltzer, J. H., & Schroeder, M. D. (1975). The protection of information in computer systems. *Proceedings of the IEEE, 63*(9), 1278–1308. doi:10.1109/PROC.1975.9939 | P | 설계 원칙 — fail-safe defaults · least privilege · complete mediation · economy of mechanism (원칙 이름은 널리 알려진 것 · 이번에 원문 문구 대조는 안 함) |
| Pedro, R., Coimbra, M. E., Castro, D., Carreira, P., & Santos, N. (2025). Prompt-to-SQL injections in LLM-integrated web applications: Risks and defenses. *ICSE 2025*. doi:10.1109/ICSE55347.2025.00007 (전신 arXiv:2308.01990 "From Prompt Injections to SQL Injection Attacks…") | P | LangChain·LlamaIndex 앱에서 프롬프트를 통한 SQL 주입(P2SQL) 확인 · 방어책 제안. 쪽수는 출처마다 엇갈려 DOI로만 인용 |
| Greshake, K., Abdelnabi, S., Mishra, S., Endres, C., Holz, T., & Fritz, M. (2023). Not what you've signed up for: Compromising real-world LLM-integrated applications with indirect prompt injection. *AISec '23*, 79–90. doi:10.1145/3605764.3623985 | P | 간접 프롬프트 주입 — 앱이 읽어 들이는 외부 데이터가 지시가 된다 |
| Halfond, W. G. J., Viegas, J., & Orso, A. (2006). A classification of SQL-injection attacks and countermeasures. *Proc. IEEE ISSSE 2006* | P | SQLi 공격·대응 분류(서지는 검색 결과로만 확인) |
| Pearce, H., Ahmad, B., Tan, B., Dolan-Gavitt, B., & Karri, R. (2022). Asleep at the keyboard? Assessing the security of GitHub Copilot's code contributions. *IEEE S&P 2022*, 754–768. doi:10.1109/SP46214.2022.9833571 | P | AI 코드 생성 산출물에 CWE 취약 코드가 상당 비율 — AI가 쓴 코드도 리뷰·스캔 대상(수치는 원문 미확인이라 인용하지 않는다) |
| Perry, N., Srivastava, M., Kumar, D., & Boneh, D. (2023). Do users write more insecure code with AI assistants? *ACM CCS '23*, 2785–2799. doi:10.1145/3576915.3623157 | P | AI 보조 사용자가 덜 안전한 코드를 쓰면서 더 안전하다고 믿는 경향 |
| Iannone, E., Codabux, Z., Lenarduzzi, V., De Lucia, A., & Palomba, F. (2023). Rubbing salt in the wound? A large-scale investigation into the effects of refactoring on security. *Empirical Software Engineering*. doi:10.1007/s10664-023-10287-x | P | 리팩터와 보안의 연관은 제한적 · 일부 유형(Extract & Move Method 등)은 취약점 도입 커밋에 더 자주 등장 → 리팩터와 보안 수정을 분리하고, 리팩터 뒤에도 보안 게이트를 돌린다 |

## 5. 확인 실패 · 공백

- OWASP JWT 치트시트 URL 2종이 404 — JWT 근거는 RFC 8725·ASVS V9로 대신한다.
- SEI CERT에 Python 공식 코딩 표준이 있는지는 확인하지 않았다 — 「없다」고 단정하지 않는다.
- LLM 생성 SQL을 읽기 전용으로 강제하는 text2sql 전용 표준·동료심사 가이드는 찾지 못했다. ASVS 1.2.4·13.2.2 · LLM05 · OWASP SQLi CS(최소 권한) · Pedro 외를 조합한다.
- "On the Impact of Refactorings on Software Attack Surface"(IEEE Access 2024) — 저자·DOI 미확인으로 인용하지 않는다.
