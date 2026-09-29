# 87. 제니퍼(JENNIFER) APM 연동 — 미들웨어(WAS) 장애 진단·대응·복구 범위 확대

> **작성일**: 2026-09-03 · **v2 갱신**: 2026-09-17(연동 방식 재조사 — §0.4) · **v2.1**: 2026-09-17(G-1·G-2 사용자 확정 · G-8 실측 판정 — §0.5) · **v2.2**: 2026-09-29(현재 구현 재실측 · 17일 이후 결정 반영 · 연동 가이드 `docs/31` 신설 — §0.6) · **v3**: 2026-09-29(**독립 최상위 패키지 `apm_gateway/`** — 자체 MCP 서버 · 독립 프로세스 · **D-274**(D-119 ① 개정) — §0.7) · **v3.1**: 2026-09-29(게이트 G-1~G-12 전건 확정 · **D-195 본문 등재** · G-10 코드 수정 · R-18 소유 지정 · 파급 반영 · **로컬 Docker 제니퍼 검증 환경 J0-L** — §0.8 · §10) · **v3.2**: 2026-09-29(**Open API 공식 스펙 대조 반영** — 52건 중 정정 24 · 스펙에 없음 3 · 확인 불가 2 · 원천 [J-23] — §0.9) · **v3.3**: 2026-09-29(**로컬 Docker 제니퍼 실측 J0-L-a**(라이선스 없음 · Open API 40건) — 전제 정정 · §0.10 · **D-003 재기록**(L2 = D-003 범위 밖 — 예외 아님 · D-195 ③ 별도 통제) · **목 Open API 서버·녹화 하네스**(J0-L-a ④⑤ 완료 · 실서버와 40건 불일치 0)) · **v4**: 2026-09-29(**J1~J4 구현** — `apm_gateway/` 패키지 · `sre_agent`·`noise_gate` 소비측 · J5·J6·J7 보류 · 파일명 `-TODO` → `-WIP` — **§0.11**) · **v4.1**: 2026-09-29(**로컬 Docker 제니퍼 실서버 검증** — 게이트웨이·소비측 58항목 통과 · 후속 후보 F-3·F-4 — §0.12)
> **성격**: 구현 계획 · **[v4] 상태: 부분 구현(J1~J4 · 작업 트리 · 커밋 없음) — 보류 J5·J6·J7 · 선행 J0-L-b·J0-O(사용자·외부) · 정본 §0.11** · ~~구현 전~~ · (이력) **상태: 계획(미구현) — 게이트 G-1~G-12 전건 확정(G-1·G-2·G-8 2026-09-17 · 나머지 2026-09-29) · D-195 본문 등재(①②③) · D-274 · 남은 선행 = J0-O 외부 전제(운영 제니퍼 접근 권한·테스트 토큰) · ~~로컬 Docker 사실 조사~~ **v3.2 완료(§0.8 · [J-24])** · ~~Open API 경로 대조~~ **v3.2 완료(§0.9)** · **[v3.3] J0-L-a 로컬 실측 완료(§0.10 — 라이선스 없는 범위)** · 사용자 할 일(평가판 신청 · **최신 Java 에이전트 5.6.x 입수** — J0-L-b 착수 조건 §0.8 (10)) · (v2.2) 제니퍼 코드 0건 재확인(`grep -i jennifer` — 샌드박스 DDL 2파일뿐) → 파일명 `-TODO` 유지(v3도 코드 0 · **[v3.3]** `apm_gateway/testdata/jennifer/` 로컬 검증 픽스처만 생김 — 패키지 코드 0 · §10)
> **v2.2 요청(사용자 지시 원문, 2026-09-29)**: *"현재 구현을 검토하여 87번 제니퍼 연동 계획서를 업데이트하고 연동 가이드를 상세하게 docs 폴더에 작성하라."*
> → 결과: 계획서가 기대거나 전제한 코드를 전수 재실측(§0.6 표 — 41항목) · 17일 이후 결정(D-229·D-233·D-240·D-244·D-251·D-255·**D-270·D-272**) 반영 · 운영자용 연동 가이드
> **`docs/31_jennifer_integration_guide.md`** 신설(구현 전이라 절마다 「현재 가능」/「구현 후 절차(계획)」를 나눴다). **확정 사항 G-1·G-2·G-8은 바꾸지 않았다.**
> **v3 요청(사용자 지시 원문, 2026-09-29 — 순서대로)**: ① *"제니퍼 연동은 sre_agent처럼 별도의 소스로 구분하여 구현하도록 계획이 되어 있냐?"*
> ② *"데이터 소스이긴 하지만 WAS의 상태를 조회하거나 분석하는 등의 노이즈 캔슬링이나 장애 진단, 분석 등에서 사용될 데이터 소스이다. 제니퍼
> 연동기능은 별도의 패키지 형식으로 분리하는 것에 대해 검토하라."* ③ (메인 세션 검토 보고 뒤) *"권고에 맞게 계획을 수정하라."*
> → **채택안 B — 독립 최상위 패키지 `apm_gateway/` + 독립 프로세스(자체 MCP 서버)**. 권고 4항목 전부 채택: ① **D-119 ① 개정**(도메인 판정 로직이나
> 별도 자격증명을 가진 관측 소스는 독립 게이트웨이 패키지로 둘 수 있다 — **D-274**) ② 패키지명 `apm_gateway/` ③ 이벤트 폴러를 게이트웨이 안에 —
> 게이트웨이가 `alarm:raw` 생산자(**G-4 폴링 경로 해소**) ④ WAS 시그니처 결정적 판정은 게이트웨이 `domain/`에 두고 noise_gate·sre_agent가 함께 쓴다.
> v2.2까지의 확정 G-1·G-2·G-8과 미결 G-3·G-5′·G-6·G-7·G-9·G-10은 유지하고, 게이트웨이 구조로 내용이 바뀌는 것만 `[v3]` 표지로 정정했다. **정본은 §0.7**이다.
> **v3.1 요청(사용자 지시 원문, 2026-09-29)**: ① *"권고에 맞게 진행하고 미결사항은 해결하라."* ② (G-6 AskUserQuestion 응답) 「권고대로 확정 (Recommended)」
> ③ *"제니퍼도 도커로 설치하여 검토할 수 있도록 계획에 포함시켜라."*
> → 게이트 전건 확정(§10 [v3.1] 확정 요약) · **D-195 본문 등재**(①②③ · D-003 부기) · G-10 원본 수정(`om_exposition` 1.0 상한 — `plans/92` 소유 코드라
> 87 착수로 보지 않는다 → `-TODO` 유지) · R-18 소유 = `plans/121` · 파급 부기(101·121·92·55·78·`sre-agent/README`) · **J0 분할 — J0-L(로컬 Docker) /
> J0-O(운영 실측)** — §0.8. 제니퍼 Docker 이미지·포트·라이선스 등 제품 사실은 메인 세션 조사 결과를 받기 전이라 「조사 대기」로 비워 뒀다 → **[v3.2] 조사 결과로 채웠다([J-24] · §0.8).**
> **v3.3 요청(사용자 지시 원문, 2026-09-29)**: *"도커에 제니퍼를 설치하여 현재 계획이 정상적인지 직접 테스트를 진행하여 계획을 업데이트하라."*
> 사용자 확정(AskUserQuestion): 설치본 입수 = 「공개 S3에서 받기 (Recommended)」 · 라이선스 = 「라이선스 없이 먼저 진행 (Recommended)」.
> → 메인 세션이 로컬 Docker에 제니퍼 5.7.0.1(데이터+뷰 서버)과 에이전트를 붙인 샘플 WAS를 띄워 **J0-L-a(라이선스 없는 범위)**를 실측했다(Open API 40건 ·
> `apm_gateway/testdata/jennifer/`). 결과와 정정한 전제는 **§0.10이 정본**이다 — Bootstrap Check는 끌 필요가 없다 · 뷰 서버가 배포하는 에이전트 5.5.2.5는
> JDK 17에서 WAS 기동을 실패시킨다 · 에이전트가 `JENNIFER_*` 환경변수를 읽는다 · 라이선스 거부는 에이전트 로그에 남는다 · 오류는 HTTP 500 + `exception.message`다 ·
> 민감 GET·쓰기 경로가 Open API 토큰에 열려 있다. **G-1·G-2·G-8 · D-274 구조 · 허용목록 방침은 바뀌지 않았다**(허용목록 1차 통제의 필요는 실측으로 확인됐다).
> **v3.3 부기(사용자 지시 원문, 2026-09-29)**: *"목 서버와 녹화 하네스도 만들어라"* → 메인 세션이 `apm_gateway/testdata/jennifer/scripts/`에 카탈로그 사본·마스킹·녹화 하네스·목 Open API 서버를,
> `recorded/local-docker/`에 라이선스 없는 녹화본 21건을, `apm_gateway/tests/`에 도구 테스트 22건을 만들었다. 로컬 실서버와 목 서버에 같은 40건 프로브를 돌려 **상태 코드·응답 모양 불일치 0건**(§0.10 #20).
> **요청 취지(사용자 지시 원문)**: *"제니퍼라는 APM 솔루션을 연동하여 미들웨어(WAS) 모니터링을 연동하여
> 장애 진단과 대응을 지원하려고 한다. 연동방식은 폴스타와 동일하게 mcp DB를 연동하려고 한다. 필요한 경우
> 제니퍼와 api연동도 검토한다. 검색을 통해 제니퍼의 기능을 검토하여 sre agent에서 처리하는 장애 진단, 대응,
> 복구 처리를 위한 범위 확대 계획을 별도의 파일로 정리하라. 필요한 경우 관련 문헌, 논문 검토를 통해 장애
> 대응에 적절한 구현 방향을 계획에서 반영하라."*
> **v2 요청(사용자 지시 원문, 2026-09-17)**: *"87번의 제니퍼 연동을 위해서는 db가 아닌 제니퍼 api를 사용하거나
> 표준 연동 규격으로 연동해야 되는 것으로 알고 있다. 관련 자료를 조사하여 계획을 업데이트하라."*
> **⚠ 전제 정정(§0.2 · v2 재정정 §0.4)**: JENNIFER 5는 RDBMS 리포지토리가 아니라 **자체 파일 DB**를 쓴다. v1.1은
> 이를 뷰 서버 RDB Export(PostgreSQL 적재)로 우회해 **SQL(적재본) + REST(Open API) + 이벤트** 3중 연동을
> 권고했으나, **v2에서 SQL(적재본) 경로를 철회한다.** 제니퍼가 외부에 여는 연동 표면은 **Open API**이고(제니퍼
> 자체 AI와 공식 MCP 서버도 Open API 위에서 돈다[J-17]), 제니퍼가 내보내는 업계 표준 프로토콜은 **SNMP trap
> (이벤트)·Kafka(트랜잭션)·MCP**뿐이다(OTLP는 수신만, Prometheus/OpenMetrics는 양방향 ✖). → **조회는 Open API
> 단일 경로(`mcp_server` 경유) + 이벤트는 API 폴링(필요 시 어댑터 push)**으로 재구성한다.
> **v2.1 사용자 확정(원문, 2026-09-17)**: *"표준 연동 규격은 cncf openmatric에 정의된 규격을 말한다. api 위주로 가자.
> 3번은 실측하여 판정하라."* → **G-2 = CNCF OpenMetrics**(제니퍼는 OpenMetrics를 노출하지 않으므로 우리가 API로 받은
> 수치 지표를 **OpenMetrics 1.0으로 노출**하는 선택 트랙 J7 — §5.9) · **G-1 = API 위주(ⓐ)** · **G-8(공식 MCP) = 실측 판정
> 「미채택 — Open API 직접 호출 확정」**(§0.5 · 재검토 트리거 명시).
> **상위/선행 계획**: **Plan 55**(멀티소스 관측 로드맵 — 본 계획은 그 M0·M1·M3(미들웨어)·M4(교차 상관 일부)의
> 실행 계획) · **Plan 78 W7-2단계**(APM 연계 · **D-168 예약** — 본 계획이 그 2단계의 구체화) · Plan 64 §8(조치
> 권고 거버넌스) · Plan 66(SRE Agent 통합 시퀀스) · `plans/sre-agent/02·04·05·06` · Plan 81(가용성 사전 판정) ·
> Plan 82(솔루션 축 실행 그룹 — `db_registry.yaml`의 `apm` 자리 예약) · **(v2.2) `plans/121`**(2단 소스 카탈로그·처리기 계약 — **Plan 82 Wave 7의
> `backend` 실행자 디스패치를 흡수**, D-270 ⑯ G-17 · J5의 새 선행) · `plans/92` B-2(OpenMetrics 노출 기계 — **구현 완료**, J7이 재사용)
> **하류 소비자(v2.2)**: `plans/101`(ML 진단 — `apm_instance_map`·`apm_slow_transactions`를 **재구현 없이 소비**한다고 명시, 101 §4.3·§5.4)
> **관련 결정**: D-003(읽기 전용) · D-035(결정적=판단·LLM=서술) · D-118(`sre_agent` 경계) · **D-119**(`mcp_server`
> = 관측 데이터 읽기 경계 — 소스 추가는 이 경계의 확장 · **[v3] ①은 D-274로 개정**) · D-122(고수준 도구 8종·값 인자 SQL 조립) · D-123/D-124
> (조사 계약·트리거 배선) · D-125(정적 Bearer) · D-127(과금 API 건별 승인) · D-139(패키지 경계) · D-161(폐기 실측
> 의무) · D-174(LLM 평면) · D-175(가용성 사전 판정) · D-176(솔루션 축) · **D-189**(L3 옵션 B — 허용목록
> read-only 명령 · 조치는 권고만) · **(v2.2)** D-209 ⑥(L3 post-gate 보강 — 게이트 kind별 read-only 명령) · D-229(조사 LLM =
> OpenAI 호환 3필드 · 로컬 MLX 검증 경로) · **D-233**(원격 조사 프로파일 bash 제거) · D-240(실 LLM 테스트 = 로컬 MLX) · **D-244**(SDD
> 산출물은 `spec/`) · **D-251**(기준 경로 = 사다리 2단) · **D-255**(사용자·관리자 기능은 매뉴얼 동반) · **D-270 · D-272**(`plans/121` —
> 소스 카탈로그 · 처리기 계약 · `allowed_sources` · 82 Wave 7 흡수) · **(v3) D-274**(관측 소스 독립 게이트웨이 패키지 허용 — **D-119 ① 개정** ·
> `apm_gateway/` · D-139 신규 최상위 패키지 · 2026-09-29 본문 등재)
> **신규 결정 예약**: **D-195**(제니퍼 APM 연동 — §11). `docs/02_decision.md` 「채번 이력」 표에 등재(2026-09-03). → **[v3.1] 2026-09-29 본문 등재 완료**(`## D-195` — ①②③ · 같은 번호 소진 관례).
> ※ 채번 실측 2026-09-03 — `## D-` 헤더 최댓값 **193** · 「변경 이력」 표 최댓값 **193** · 「채번 이력」 표
> (D-105·115·134·158·163~168·176 예약) 대조 → 다음 번호는 194이나, 작성 시점에 **D-194를 `plans/50` v2.1·
> `CAPABILITY-MAP-50.md`·`SPEC-briefing-contract.md`가 "예정"으로 쓰고 있었다**(표 미등재). 충돌을 피해 본 계획은
> **D-195**를 잡았고, **같은 날 D-194가 `plans/50` v2.2로 본문 등재**되어(`## D-194` 확인) 번호가 연속으로 맞았다. ※ 그 D-194는 2026-09-07 원격 병합 시 **D-197**로 재부여됐다(D-194는 FabriX 프로파일이 선점) — 이하 본문의 D-194는 당시 번호.
> **실측 기준**: 아래 모든 `file:line`·값은 2026-09-03 현 브랜치(`multiintent`)에서 직접 확인했다.
> **v2.2 재실측 기준(2026-09-29)**: HEAD `2e635a9` + 미커밋 작업 트리(병행 세션이 `src/orchestration/*`·`src/config.py` 등을 수정 중).
> 작업 트리에서 수정 중인 파일은 **HEAD 줄 번호**로, 그 밖의 파일은 작업 트리 값으로 인용한다. §3의 옛 `file:line`은 이력 보존을 위해
> 지우지 않았다 — 최신 값과 판정은 **§0.6 표가 정본**이다.
> **제니퍼 자료 범례**: ✔ 확인(출처 첨부) · △ 추정(근거 있음, 실측 필요) · ✖ 미확인(공개 자료 없음).
> 제니퍼 정보는 **JENNIFER 5.6.x 공개 자료 + 4.5 매뉴얼(구조·이벤트 유형의 보조 근거)** 기준이다.
> JENNIFER 6은 2026-09 기준 공개 근거가 없어 다루지 않는다. **v2(2026-09-17)**: 최신 릴리즈는 **5.7.0(2026-08-13)**
> [J-18]이고, Open API 정본 스펙은 **5.6.4**(`openapi.jennifersoft.com`)[J-4]다. v2가 새로 인용한 제니퍼 자료 중 설계를
> 바꾸는 주장은 릴리즈 노트·설치 가이드 원문(`docs.jennifersoft.com`)과 스펙 원문으로 직접 확인했고, 조사 보고에만 근거한
> 항목은 △로 표기했다(사용자 매뉴얼·엔지니어 문서는 로그인 필요 — ✖).

---

## 0. 요약 — 이 계획이 실제로 푸는 문제

### 0.1 세 조각으로 갈라지는 요청

요청은 "제니퍼를 연동해 WAS 장애 진단·대응·복구를 지원한다" 한 줄이지만, 실측하면 **난이도와 선행 결정이
서로 다른 세 조각**이다.

| 조각 | 난이도 | 선행 결정 | 근거 |
|---|---|---|---|
| **① 데이터 소스 편입** — 제니퍼 데이터를 `mcp_server`의 세 번째 소스로 | 중 | 없음(D-119 ①이 이미 허용) | Prometheus 편입(D-119 R-B — **HTTP API 소스**)이 청사진. 폴스타 도구 8종의 반환 계약·서버측 결정적 조립·감사·Bearer가 전부 재사용된다(`mcp_server/mcp_server/polestar_tools.py:636-641`, `config.py:70-89`) |
| **② 진단 범위 확대** — `sre_agent`가 WAS 관점 증거(트랜잭션·힙/GC·풀·큐잉)를 읽고 판정·권고 | 중 | 없음 | `sre_agent`는 `mcp_server` 한 엔드포인트의 도구를 **자동 발견**한다(`interface/mcp_service.py:97-113`, RemoteMCPToolset) → 도구가 늘어도 sre_agent 배선 변경 0. 확장 지점은 지침·시그니처·권고 표·브리핑뿐 |
| **③ 대응·복구 범위 확대** — 권고를 넘어 조치를 실행 | **높음** | ~~**D-003 예외 신설**~~ **[v3.3] D-003 범위 밖(DB 쓰기 없음 — 예외 아님) · 실행 평면은 D-195 ③이 별도 통제** **+ Plan 78 §8.3 5조건** | 현행 불변식: *"조치는 권고만(자동 실행 경로 없음 · `HUMAN_GATED_NOTE` 강제)"*(D-189 불변식 · `briefing_builder.py:36`). 자율성 3단계 진입은 5조건 **동시 충족**이 요구된다(Plan 78 §8.3). 본 계획은 그 조건을 **충족시키는 설계**를 제시하되, 착수는 **사용자 확정(G-6)** 뒤로 둔다 |

→ ①·②는 기존 결정 안에서 착수 가능하고, ③은 **새 결정이 필요한 유일한 조각**이다. 세 조각을 한 계획에
두는 이유는 ③의 안전 설계(검증 루프·롤백)가 ①·②의 데이터(조치 후 `apm_app_health` 재조회)에 의존하기
때문이다 — 순서를 갈라도 설계는 함께 봐야 한다.

> **[v3]** ①(데이터 소스 편입)은 `mcp_server`의 세 번째 소스가 아니라 **독립 최상위 패키지 `apm_gateway/`**로 한다. 그러려면 D-119 ①의
> "별도 서버가 아니라 이 경계의 확장"을 바꿔야 했고, 그 결정은 **D-274로 확정**됐다(2026-09-29). ②의 WAS 판정도 게이트웨이 `domain/`에 둔다 — §0.7.

### 0.2 전제 정정 — "폴스타와 동일한 MCP DB 연동"은 그대로 성립하지 않는다

| 사용자 전제 | 실측 | 판정 |
|---|---|---|
| 제니퍼도 폴스타처럼 리포지토리 RDBMS가 있어 SQL로 직접 읽는다 | ✔ JENNIFER 5는 성능 데이터를 **자체 파일 DB**에 저장한다 — 원문은 *"사용자가 원하는 가공 형태의 데이터를 조회하는데 어려움이 있습니다"* 로, SQL 조회 대안 둘(API 서버+JDBC · RDB Export)을 제시한다[J-1]. 4.x까지는 Derby/Oracle/DB2 리포지토리였으나[J-3] 5에서 파일 DB로 바뀌었다. ※ v1.1의 인용 *"직접 접근이 제한적"* 은 원문 문장이 아니어서 v2에서 정정 | **리포지토리 직접 SQL 불가** — API 서버+JDBC 드라이버로 SQL은 되나 `jennifer5-api-server` 최종 push **2023-02-07**(✔ GitHub API) · JDBC 드라이버 최종 릴리즈 v5.5.3.2(2021-03 △) · 5.6.2~5.7.0 릴리즈 노트 언급 0건(△) — **정체**[J-15] |
| 그래도 DB로 읽을 길은 있는가 | ✔ 뷰 서버 내장 **RDB Export**가 통계를 외부 RDB에 적재한다 — 2021년 Oracle 12 / MySQL 5.7 / PostgreSQL 9.x[J-2] · 이후 MariaDB·Tibero·SQL 통계·개별 ERROR(5.6.1 △) · 분 단위 애플리케이션 통계(5.6.3 ✔)[J-20] · **5.7.0에서도 PG/MSSQL 오류 수정**[J-18] — 지원·개선 중 | **적재본 SQL 경로는 기술적으로 성립** — 단 v2에서 **미채택**(§0.4) |
| 그 DB만으로 진단이 되는가 | ✖ 적재본에는 **액티브 서비스(현재 실행 중 트랜잭션·스택)·X-View 프로파일·이벤트·실시간 지표가 없다**. 이들은 Bearer 토큰 기반 **Open API**[J-4][J-5]가 유일한 경로다 | **진단에는 API가 필수** — v2에서는 추세·기준선까지 API(`/api/dbmetrics/*`)로 받아 **API 단일 경로**로 둔다 |
| 이벤트(알람)는 어떻게 받는가 | ✔ EVENT 룰의 "외부연동" + **EVENT 어댑터**(Java, `com.aries.extension`)[J-6] — 공식 **SNMP trap 어댑터** 포함[J-15] — 또는 Open API `/api/dbsearch/event` 폴링[J-4] | 폴스타 TCP JSON → `alarm:raw`와 **같은 스트림**으로 편입 가능 |

**되돌리는 비용**(사용자가 "DB만"을 고집할 경우): 얻는 것은 5분 이상 집계된 인스턴스·도메인 지표와 일 통계
뿐이다. 잃는 것은 액티브 스택·프로파일·이벤트 — 곧 **미들웨어 장애 진단의 핵심 증거**다. §2.4 비교표가
근거이며, 이 판정은 **G-1 게이트**로 사용자에게 되돌린다. (v2: 반대 방향 — SQL 경로를 **빼는** 비용은 §0.4.)

### 0.3 한 줄 권고

*(v2.1) **Open API(REST) 하나로** 실시간·프로파일·추세(`/api/dbmetrics/*`)를 받고, 이벤트는 **API 폴링**으로 받아
(어댑터 push는 폴링이 부족할 때만), **모두 `mcp_server` 하나의 `apm_*` 도구 표면 뒤에 숨긴다**(벤더 무지·D-119).
표준 연동 규격(**CNCF OpenMetrics**)은 제니퍼가 내지 않으므로, 받아 온 **수치 지표만** `mcp_server`가 OpenMetrics 1.0으로
다시 노출하는 선택 트랙(J7)으로 충족한다 — 이벤트·액티브 서비스·프로파일은 규격 밖이라 API가 정본이다. 같은 토큰이 쓰기·제어 API까지 여므로 `mcp_server`가 **GET·경로 허용목록을
코드로 강제**한다. `sre_agent`는 도구를 자동 발견하므로 **지침·시그니처·권고·브리핑만 WAS 관점으로 확장**한다.
대응·복구는 **L1(권고) → L2(승인 후 실행)** 로 한 단만 올리되, 실행기는 LLM 평면 밖의 **결정적 런북 실행기**로
두고 ~~D-003 예외~~(**[v3.3]** D-003 범위 판정 — 범위 밖 · D-195 ③)·승인 UX·blast radius·검증-롤백·적응형 공격 평가를 **G-6에서 사용자 확정 후** 착수한다.*
(v1.1 권고 *"RDB Export 적재본(SQL)으로 추세·통계를"* 은 v2에서 철회 — §0.4.)
**[v2.2 부기]** 권고의 뼈대는 그대로다. 달라진 것은 둘이다. 본체 질의 경로 편입은 Plan 82 Wave 7이 아니라 `plans/121`의 처리기 계약을
따른다(D-270 ⑯). 운영 조사에는 셸이 없으므로(D-233) "OS 근사 폴백"은 `mcp_server` 폴스타 도구를 뜻한다 — §0.6.
**[v3 부기]** 권고 첫 문장의 "모두 `mcp_server` 하나의 `apm_*` 도구 표면 뒤에 숨긴다"는 **"모두 `apm_gateway`(자체 MCP 서버)의 `apm_*` 도구 표면 뒤에
숨긴다"**로 읽는다(D-274). 이벤트 폴링도 게이트웨이가 하고(`alarm:raw` 생산자), OpenMetrics 재노출(J7)도 게이트웨이가 한다. 벤더 중립 표면·GET 허용목록·
결정적 판정·L2 설계는 그대로다 — §0.7.

### 0.4 v2 재판정 (2026-09-17) — "DB가 아닌 API 또는 표준 연동 규격"

**(1) 사용자 전제 판정 — 채택.** 벤더가 "DB 연동 금지"라고 적은 문장은 없지만(✖), 아래 근거로 **조회 경로를
Open API 단일로 재구성**한다.

| # | 근거 | 확인 |
|---|---|---|
| 1 | **제니퍼 자신이 외부 연동을 Open API 위에 올렸다** — 공식 MCP 서버: *"MCP 서버는 제니퍼 OpenAPI를 통해 데이터를 조회하므로…"*[J-17] · 제니퍼 AI: *"제니퍼 Open API 의 모니터링 데이터를 자동으로 분석"*(설치 가이드 11장 — △ 조사 보고 인용, 원문 미재확인) · Insight Chat도 Open API를 tool로 호출[J-8] | ✔ MCP 원문 / △ AI |
| 2 | Open API는 **OpenAPI 3.0.3 기계 판독 스펙**(`openapi.jennifersoft.com`, `info.version` 5.6.4, ~~52경로~~ 39경로·63오퍼레이션[v3.2], `bearerAuth`)으로 공개된 계약이다[J-4]. 적재본 테이블은 컬럼 DDL이 공개돼 있지 않다(U-2 · 설정 상세는 비공개 엔지니어 문서) | ✔ 스펙 원문 |
| 3 | 진단 핵심 증거(액티브 서비스·X-View·이벤트·실시간)는 원래 API로만 나온다(§0.2) — SQL 경로를 둬도 **API 의존은 사라지지 않고 경로만 둘이 된다** | ✔ |
| 4 | 추세·기준선은 API `/api/dbmetrics/{domain,instance,business}`(`interval_minute`)로 대체된다[J-4] — 적재본이 유일하게 주던 가치가 줄어든다 | ✔ 스펙 · 보존 기간 U-3 |
| 5 | 적재본 경로는 **새 운영 부담**을 만든다 — 뷰 서버 `server_view.conf` 편집·재기동, 적재 DB 신설·버전 호환, 스키마 변경 추적. 벤더 API 경유는 스키마 진화를 벤더가 흡수하고 자격증명을 콘솔 권한 체계로 관리한다(산업 전례: SolarWinds SWIS *"allows SolarWinds to evolve the database schema while providing a consistent, backward-compatible object model"*[S-6]) | ✔ |
| 6 | **폴스타 제품 자신도** 제니퍼 연결을 URL+토큰으로 모델링한다 — `was_connection.jennifer_url`·`jennifer_token`·`jennifer_domain_id`가 Open API의 Bearer·`domain_id` 필수 인자와 정확히 대응(§3.3). 운영 사용 여부는 ✖(U-10) | △ 정황 |

**SQL 경로를 빼는 비용**: ① 적재본 위 **자유 SQL 집계·장기 보관 조인**(text2sql로 "지난 분기 WAS별 일평균 응답시간")을
잃는다 — `/api/dbmetrics/*`의 보존 기간(U-3) 안에서만 답한다. ② text2sql 질의 경로(J5)가 `backend: sql`로
**기존 SQL 파이프라인을 재사용하지 못하고** Plan 82 Wave 7의 그룹 실행자 훅(`backend: mcp` 변형)을 기다린다(§5.6).
③ 되돌리는 비용은 작다 — §5.2(a) `[[sources]]` 1항목 + `db_profiles` 편입으로 복원되며, 요구가 확인되면 **G-1 ⓑ**로 되살린다.

**(2) "표준 연동 규격" = CNCF OpenMetrics (v2.1 사용자 확정 · G-2)**

> v2는 후보를 넷(제니퍼 자체 규격 · 업계 표준 프로토콜 · 국내 공공·금융 표준 · 조직 내부 규격)으로 나눠 사용자에게
> 되물었고, 사용자가 **CNCF OpenMetrics**로 확정했다. 네 갈래 표는 §13 v2 이력으로 대체한다.

| # | 실측 | 설계 함의 |
|---|---|---|
| O-1 | **규격 상태**: OpenMetrics **1.0 = "Status: Published · November 2020"** · 2024년 CNCF Prometheus 프로젝트로 편입 · **2.0 = "Experimental"(RC — *"we reserve the right to break the compatibility"*)**[OM-1][OM-2] | **1.0 고정**(`application/openmetrics-text; version=1.0.0; charset=utf-8` · `# EOF` 필수). 2.0은 정식화 후 재검토 — `plans/92`와 같은 기준 |
| O-2 | **규격 범위**: *"This standard expresses all system states as numerical values … Contrary to metrics, singular events occur at a specific time."*[OM-1] | **수치 지표만 규격 대상.** 제니퍼 EVENT·액티브 서비스 목록·X-View 프로파일·트랜잭션 목록은 **규격 밖** → 진단 핵심 증거는 **API가 정본**(G-1 "API 위주"와 정합) |
| O-3 | **제니퍼는 OpenMetrics를 노출하지 않는다**: 정본 스펙(~~52경로~~ 39경로·63오퍼레이션[v3.2])의 응답 형식은 `application/json`·`text/plain`(프로파일 텍스트)뿐이고 **[v3.2 정정: 선언은 `*/*` 47 · JSON 6 · `text/plain` 2이고 `.xml` 변형 경로 5개가 있다 · `opentelemetry` 4건은 데이터 서버 OTel 수신 설정 필드 — 내보내기 0건이라 결론은 같다]** `prometheus`·`openmetrics`·`exposition`·`scrape`·`otlp`·`opentelemetry` **0건**(2026-09-17 스펙 원문 검색)[J-4] · 릴리즈 노트·공개 리포 언급 0건(△ 조사 보고) · 웹 검색 0건 | **"제니퍼를 OpenMetrics로 연동"은 제니퍼 측만으로는 불성립.** 규격 준수는 **우리 쪽 브리지**로만 가능 — API로 받은 인스턴스 지표를 `mcp_server`가 OpenMetrics 1.0으로 노출(**J7 · §5.9 · 선택 트랙**) |
| O-4 | **노출 규칙**: 단위가 있으면 이름 접미사 필수(*"it MUST be a suffix of the MetricFamily name"*) · counter는 `_total` · *"MetricPoint timestamps should not be exposed"*[OM-1] | 제니퍼 응답시간(ms)은 **초로 변환**해 `_seconds` · TPS는 비율이라 counter가 아니라 **gauge** · 타임스탬프 미노출(스크레이프 시각은 수집기 몫) |
| O-5 | `plans/92` 트랙 B-2가 **폴스타 → OpenMetrics 브리지**(`mcp_server` `custom_route`)를 이미 설계했다 · FastMCP 1.29.1 `custom_route(path, methods, name=None, include_in_schema=True)` 실측 | J7은 **같은 기계 재사용**(직렬화기·인증·부하 가드) — 제니퍼 전용 코드는 "API 응답 → MetricFamily" 매핑뿐 |

→ 이벤트 push 방식(SNMP trap 등)은 OpenMetrics와 무관하므로 v2가 붙였던 "표준" 라벨을 떼고, **API 폴링이 정본**,
어댑터 push는 폴링의 부하·지연이 실측으로 문제될 때만 착수한다(§5.5 · G-4).

### 0.5 G-8 실측 판정 (v2.1 · 2026-09-17) — 제니퍼 공식 MCP 서버: **미채택, Open API 직접 호출 확정**

**실측 한계(먼저 밝힌다)**: 공식 MCP의 **런타임 `tools/list`는 실측하지 못했다.** 프록시 설치본(`jennfer-llm-1.x.x.zip`)의
획득 경로가 설치 가이드에 없고(원문: *"프록시 서버: jennfer-llm-1.x.x.zip 파일이 필요"* 뿐)[J-17], `jennifersoft` GitHub
조직 공개 리포에 llm·mcp 리포 0건(GitHub API)·Docker Hub `jennifersoft` 이미지는 2018년 1건뿐이며, 벤더 공개 프록시
`insight.jennifersoft.com`은 `302 → /login`(인증 필요)이다. 로컬·폐쇄망 모두 제니퍼 서버 접근 설정이 없다(`.env` 제니퍼 키 0건).
그래서 **실측 가능한 1차 자료와 우리 측 실측**으로 판정하고, 미실측 항목이 **최선의 값이어도 판정이 뒤집히지 않는지**를 따로 봤다.

| # | 판정 기준 | 실측 | 결과 |
|---|---|---|---|
| M-1 | 설치·운영 전제 | 프록시 설정 `server.llm/conf/server_llm.conf`에 **LLM 공급자**(`llm_proxy_model_provider` = `private_solar`·`azure_openai`·`aws_bedrock`·`private_openai`)와 **자체 대화 DB**(`llm_proxy_db_filename`·`username`·`password`)를 설정하고 `bin/startup_llm.sh`로 띄운다 · 뷰 서버에 `llm_proxy_host`·`llm_proxy_uuid`·`llm_proxy_org` 등록 · **5.6.5+ · JDK 17+**[J-17] | ✖ — MCP 하나를 쓰려고 **LLM 프록시 제품 전체**(LLM 연결·대화 DB·뷰 서버 등록)를 운영해야 한다 |
| M-2 | 권한·자격증명 | MCP 클라이언트가 매 요청 헤더로 `X-Jennifer-Api-Url`·`X-Jennifer-Api-Token`(콘솔 발급 **같은 Open API 토큰**)을 보낸다 · *"MCP 서버는 제니퍼 OpenAPI를 통해 데이터를 조회하므로"*[J-17] | ✖ 이점 0 — 권한 경계가 Open API 직접 호출과 **동일**(같은 토큰·같은 권한). 허용목록은 어차피 우리가 걸어야 한다 |
| M-3 | 계약 가시성·v1 동결(R-12) 흡수 | MCP 도구도 Open API 위에서 동작[J-17] → v1 변경 시 **프록시 갱신**이 필요. 결합 대상이 **공개 OpenAPI 3.0.3 스펙**(~~52경로~~ 39경로·63오퍼레이션[v3.2])에서 **비공개 도구 스키마**(공개 예시 4종: `metrics_list`·`transaction_list_by_application`·`sql_statistics`·`jennifer-mcp-id_get_domain_list` — 마지막은 Open WebUI 서버 id 접두로 보임 △)로 바뀐다 | ✖ — 벤더 흡수 이점보다 **계약 검증 가능성 상실**이 크다(recorded JSON 계약 테스트의 기준이 사라짐) |
| M-4 | 데이터 경계(D-120) | 프록시에는 **벤더 운영 공개 프록시 모드**가 있다 — *"제니퍼 AI 에서 운영 중인 공개 프록시 서버 (insight.jennifersoft.com)"* · *"공개 프록시 서버의 데이터는 … 최대 30 일간 보관"* · 기본은 *"폐쇄망 내부에 자체 설치된 거대 언어 모델(LLM)을 사용하도록 설계"*[J-22] | △ — 사내 설치형만 허용 가능하나, **설정 하나로 외부 전송 모드가 되는 구성요소**를 조회 경로에 넣는 것 자체가 통제 지점 추가 |
| M-5 | 데이터 범위 | 제니퍼 AI가 Open API에서 읽는 범위: *"도메인, 인스턴스 목록, 도메인, 인스턴스 별 성능 메트릭, 트랜잭션, 스택 트레이스, 프로파일 텍스트 (SQL 파라미터 제외), 에러, 이벤트"*[J-22] | △ — §5.2 8종 영역과 대체로 겹칠 **가능성**(도구 단위 확인 불가). hostname 앵커·상위 N 축약·마스킹은 어느 경우든 우리 몫 |
| M-6 | 우리 측 기술 호환 | `mcp` 1.29.1 `streamablehttp_client(url, headers=None, timeout=30, sse_read_timeout=300, …)` — **헤더 주입 가능**(inspect.signature 실측) | ✔ — ②는 기술적으로 가능하다(판정을 가르는 기준이 아님) |

**판정**: **② `mcp_server` 뒤 백엔드 = 미채택 · ① Open API 직접 호출 = 확정 · ③ `sre_agent` 직결 = 기각 유지**(§2.8).
M-5(도구 커버리지)가 최선(전 영역 커버·쓰기 도구 0·JSON 구조화 반환)이어도 **M-1(LLM 프록시 운영)·M-2(권한 이점 0)·
M-3(계약 가시성 상실)은 그대로**라 결론이 바뀌지 않는다. → §5.2(d)의 "백엔드 교체 자리"는 **만들지 않는다**(단일 구현에
추상화 금지) — 벤더 호출은 `apm_client.py`에 캡슐화하는 것으로 충분하다(R-9).

**재검토 트리거**(셋 **모두** 충족 시에만 재판정 — 그때 `tools/list`를 실측한다): ⓐ 운영에 **사내 설치형** 제니퍼 LLM 프록시가
**이미 가동 중**(M-1 비용 소멸) ⓑ 벤더가 Open API v1의 **제거 일정을 공지**하고 MCP 도구가 대체 경로로 제시됨(M-3 역전) ⓒ `tools/list`
실측에서 §5.2 8종 뒷단 커버 · 쓰기·제어 도구 0(또는 서버측 차단 가능) · 구조화(JSON) 반환 확인.

### 0.6 v2.2 구현 실측 재대조 (2026-09-29) — 계획서 전제 ↔ 현재 코드

**요약(먼저)**
- **제니퍼 전용 코드는 여전히 0건이다.** `grep -ri jennifer`(src·mcp_server·sre_agent·noise_gate·config·scripts·tests·testdata)가
  잡는 것은 샌드박스 DDL 2파일뿐이고, 네 `.env` 계열 파일에 `JENNIFER`·`APM_` 키가 없다. 파일명 `-TODO`를 유지한다.
- **재사용하겠다던 기계는 대부분 그대로 있고, 하나는 새로 생겼다.** 반환 계약·HTTP 소스 설정·도구 자동 발견·수신기 골격은 성립한다.
  특히 `plans/92` B-2의 **OpenMetrics 노출 기계가 구현돼**(`om_exposition.py`) J7의 공유 대상이 실재한다.
- **네 전제는 틀어졌다.** ① 질의 경로(J5)의 선행이던 Plan 82 Wave 7 실행자 디스패치는 **D-270 ⑯(G-17)로 `plans/121`에 흡수**됐다.
  ② 운영 원격 조사에는 셸이 없다(D-233). "OS 근사 2차 폴백"은 셸 명령이 아니라 `mcp_server` 폴스타 도구뿐이다. W7-1 미들웨어 자산은
  **프로덕션 호출부가 0건**이다. ③ 사용자 pull 조사(`fault_diagnosis`)는 **3단에서만 배선**된다. 기준 경로 2단과 운영 1단에서는 도달하지 않는다.
  ④ 공유 노출 기계는 `version=2.0.0` 요청에 **OpenMetrics 2.0**으로 응답한다. G-2의 "1.0 고정"과 어긋난다(92 소유 코드).
- **새 설계 위험 1건**: 제니퍼 이벤트 이름(`JVM_HEAP_MEM_HIGH` 등)이 알람 kind 분류기의 부분 문자열 키워드와 겹친다.
  그러면 OS 플레이북과 L3 보강이 잘못 붙는다(**R-16**).

**(1) 실측 표** — 판정 범례: ✔ 성립 · ✏ 정정(계획 문구를 고친다) · ➕ 보강(계획에 없던 사실·선례) · ⚠ 불일치·위험

| # | 항목 | 계획서 전제(위치) | 실제 상태(`file:line`) | 판정 |
|---|---|---|---|---|
| **A** | **`mcp_server` 데이터 평면** | | | |
| 1 | 도구 등록 자리 | §3.1 `server.py:123-135` | `mcp_server/mcp_server/server.py:129-158` — `register_tools` → 폴스타(옵션) → PromQL → OpenMetrics → 소스 사다리 → 폴스타 브리지(옵션) 순. `apm` 0건 | ✔ 줄 이동. `register_apm_tools`는 브리지 뒤에 둔다 |
| 2 | HTTP 소스 설정 전례 | §5.2(b) `PrometheusConfig` 동형 | `config.py:53-66` — **dataclass + `config.toml` 섹션 + `_apply_env_overrides`(`:348-462`)**. pydantic-settings가 아니다 | ✔ `JenniferApiConfig`도 dataclass · `[jennifer]` 섹션 · env 오버라이드로 만든다 |
| 3 | `.env` 로딩 | (명시 없음) | `config.py:201-219` `_load_dotenv`가 `mcp_server/.env`를 `os.environ`에 넣는다(이미 있는 키는 두고) | ➕ 키 위치는 `mcp_server/.env`다(루트 `.env` 아님) |
| 4 | `.env.example` 동반 | (명시 없음) | `mcp_server/tests/test_env_example_coverage.py` — 오버라이드 소스에서 키를 뽑아 `.env.example` 누락을 실패로 판정 | ➕ J1 수용 기준: 신규 키는 `.env.example`에 같이 올린다 |
| 5 | REST 도구 선례 | §3.1 `polestar_tools.py:39` httpx | `polestar_tools.py:930-990` `polestar_process_snapshot` — 호출마다 `httpx.AsyncClient(timeout=10.0)`(`:963`) · 인증 없음 · `source_kind="polestar_process_realtime"`(`:985`) | ✔ 다만 인증·허용목록·리다이렉트 차단이 없어 **제니퍼에는 부족**. 인증 헤더 전례는 `promql_tools.py:181-198`. 리다이렉트 금지·본문 상한 전례는 `openmetrics_tools.py:8-14·79-84` |
| 6 | 반환 계약 | §3.1 `polestar_tools.py:636-641` | `polestar_tools.py:655-671` `_ok`/`_err` — `{rows,row_count,queried_at,source_kind,source,engine}` / `{error}` | ✔ 줄 이동 |
| 7 | 기존 도구 수 | §5.1 그림 "polestar_* 8" | `register_polestar_tools` **9종**(`polestar_tools.py:704-1105` — `change_history`·`condition_log` 추가) + `om_*` 2종(`openmetrics_tools.py:338-411`) | ✏ 8 → 9 · `om_*` 2 추가. `apm_*` 8종 상한(P4)은 그대로 |
| 8 | 감사 형식 | §5.8 "`sql_log`와 같은 형식" | SQL은 `sql_log.py`(D-140 · `logs/sql/`). HTTP 도구는 `promql_tools.py:156-172` `_audit`(logger 1줄 — 도구·질의·소요·행수·오류) | ✏ `apm_*` 감사는 PromQL `_audit`과 같은 형태를 선례로 한다. 별도 감사 저장소가 필요하면 그때 결정한다 |
| 9 | httpx 선언 | §3.1·G7 "`mcp_server/pyproject.toml` 미선언" | `mcp_server/pyproject.toml:7-17` 여전히 없음 · 루트 `pyproject.toml:39` `httpx>=0.27.0` · 설치본 0.28.1 | ✔ 갭 그대로(G7) |
| 10 | 테스트 목킹 | J1 "`respx`" | 루트 venv에 **`respx` 미설치**. 기존 HTTP 도구 테스트는 `httpx.MockTransport`(`mcp_server/tests/test_promql_tools.py`·`test_openmetrics_tools.py` 등 4파일) | ✏ 신규 의존 없이 `MockTransport`로 |
| 11 | Bearer 범위 | §8.2 "J7도 Bearer 뒤" | `server.py:71-88` — 미들웨어가 **앱 전체**(SSE + `custom_route`)를 감싼다. 단 토큰이 비면 무인증 통과(`:45`) | ✔ 운영은 `MCP_BEARER_TOKEN` 필수(가이드에 명시) |
| 12 | overfit 격리 | §5.9 "벤더 리터럴은 `apm_openmetrics.py`에" | `scripts/overfit_check.py:62` 스캔 대상 `mcp_server/mcp_server` · `:71-76` 제외 = `polestar_tools.py`·`polestar_exporter.py` | ➕ 벤더 모듈(`apm_client.py`·`apm_openmetrics.py`)은 **제외 목록 등재**가 선례. `apm_tools.py`(벤더 중립)는 스캔 대상으로 둔다 |
| 13 | 소스 타입 | §3.1 `postgresql\|db2` | `config.py:132` `postgresql\|db2\|mariadb`(D-214) | ✔ 무관(제니퍼는 DB 소스가 아니다) |
| **B** | **OpenMetrics 노출(J7)** | | | |
| 14 | B-2 노출 기계 | §5.9 "`plans/92` B-2가 설계" · G-9 ② "B-2 착수와 묶음" | **구현 완료** — `om_exposition.py:1-321`(벤더 중립 · 모듈 docstring에 "plans/87 J7 공유" 명시) · `polestar_exporter.py:74·518-538`(`GET /metrics`) · `config.py:98-124` · 테스트 `test_om_exposition.py`·`test_polestar_exporter.py` · `plans/92` v5 O5 실 스크레이프 통과 | ✔ 전제 충족. **G-9 ②의 "묶음"은 소멸**(묶을 대상이 이미 있다) → §10 G-9 권고 갱신 |
| 15 | 노출 형식 버전 | §5.9·O-1 "1.0 고정" | `om_exposition.py:162-185`가 `prometheus_client.exposition.choose_encoder`로 Accept를 협상한다. **실측(prometheus-client 0.26.0)**: 빈 Accept·`*/*` → text 0.0.4 · `version=1.0.0` → 1.0 · **`version=2.0.0` → `Content-Type: …version=2.0.0`**(게이지 표본의 본문은 1.0과 같다). `render_exposition` docstring(`:168-169`)의 "version ≥ 1.0.0이면 OpenMetrics 1.0"은 사실과 다르다 | ⚠ **불일치** — 공유 기계가 G-2의 "1.0 고정"을 보장하지 않는다. 수정 대상은 `plans/92` 소유 코드 → 소유자 통보(§10 **G-10** · **R-17**) |
| 16 | `custom_route` 서명 | O-5 FastMCP 1.29.1 | `mcp` 1.29.1 설치 · `FastMCP.custom_route(path, methods, name=None, include_in_schema=True)`(inspect 실측) | ✔ 불변 |
| 17 | 수집 실패 표현 | J7 수용 기준 "장애 시 `jennifer_bridge_up 0`(500 아님)" | `om_exposition.py:242-270` — 수집 함수가 **예외를 올리면 503**. 부분 실패는 상태 gauge로 표현하는 것이 계약(`:42-43`) | ✔ 정합 — J7 수집 함수는 Open API 오류를 잡아 `jennifer_bridge_up 0`으로 낸다 |
| **C** | **질의 경로(J5)** | | | |
| 18 | `apm` 솔루션 자리 | §3.3 `db_registry.yaml:59-64` `backend: rest` | `config/db_registry.yaml:60-65` 주석 그대로(`backend: rest`) · 등록 0건을 `tests/test_orchestration/test_execution_groups.py:31-33`이, `capability_providers("was_metric") == ()`를 `:40-42`가 단언 | ✔ 자리 유지. J5 착수 시 **두 테스트를 함께 갱신**한다 |
| 19 | `backend` 실행자 | §3.3 "운반만, `rest` 실행 0건" · J5 선행 = Plan 82 Wave 7 | `src/routing/registry.py:77`·`execution_groups.py:77` 운반만 · `src/nodes/multi_db_executor.py:1247` `"backend": "sql"` 고정 · `rest`/`mcp` 실행 코드 0건 · Plan 82 Wave 7 미착수 · **D-270 ⑯(G-17): 디스패치를 `plans/121` TP-9.2·TP-10.5가 흡수**(둘 다 묶음 C · 코드 0 — `metric_query`·`config/task_routines.yaml` 부재) | ✏ **선행 교체** — J5 선행 = `plans/121` TP-9.1·9.2(카탈로그·비SQL 시스템 등재)와 TP-10.5(1급 처리기 선례) |
| 20 | 능력 카탈로그 | §5.6 `capabilities: [was_metric, …, apm_event]` | `config/db_registry.yaml:74-107` `capabilities:` 카탈로그 신설(D-224 ① — 설명 렌더 재료). 소유 정본은 `solutions[].capabilities` | ➕ J5는 새 능력 코드마다 카탈로그 설명 행을 추가한다 |
| 21 | 본체 → `mcp_server` 도구 호출 선례 | (v2에서 미파악) | `src/dbhub/client.py:421-449` `HOST_INSPECT_PROFILES`(프로파일 → 고수준 도구) · `:510-570` `inspect_host` · HEAD `src/orchestration/subagents.py:1689` `host_inspect` 서브에이전트 · 게이트 HEAD `host_inspect.py:251`(`composite.investigation_enabled` — HEAD `src/config.py:1161` 기본 False) · `metrics_live`(92 O3)가 `om_metric_instant`를 같은 방식으로 붙였다 | ➕ **새 선례** — 2단에 본체가 `mcp_server` 도구를 부르는 배관이 이미 있다. J5는 이 MCP 세션을 쓰고 본체에 제니퍼 자격증명을 두지 않는다(§5.6 `mcp` 근거와 정합) |
| 22 | 소스 인가 | (없음) | D-270 ⑰(G-18) `allowed_sources` — 비DB 소스 인가(D-232 확장 · 미구현) | ➕ J5 수용 기준: 권한 밖 사용자에게 APM 소스를 노출하지 않는다 |
| 23 | pull 조사 위임 | §5.6 "`fault_diagnosis` 의도는 변경 없음" | `src/graph.py:497-501·594-599·728-741` — `fault_diagnosis` 노드는 **3단 `semantic_router` 경로에서만**(`noise_gate.fault_diagnosis_enabled` on) 배선 · HEAD `src/orchestration/schemas.py:17-23` "서브에이전트가 아니라 그래프 노드" → 2단(기준)·1단(운영)에서 **도달 불가** | ⚠ **전제 한정** — J3의 사용자 pull 진입은 지금 3단에서만 검증된다. 2단 조사 위임 공백은 87 소관 밖이고 소유 계획이 없다(사용자 보고) |
| **D** | **`sre_agent`(J3)** | | | |
| 24 | 도구 자동 발견 | §3.2 `mcp_service.py:97-113` | `sre_agent/sre_agent/interface/mcp_service.py:105-118` `_build_mcp_servers`(서버 1개 · `list_sources` 헬스체크 · Bearer) · `:139-140` | ✔ `apm_*`는 배선 변경 없이 발견된다 |
| 25 | OS 근사 폴백 | §1.1·§3.2·§5.4-a "APM 1차 · OS 근사(ps·ss·top·journalctl, W7-1) 2차" | 운영 원격 프로파일 `sre_agent/sre_agent/toolset_profiles.py:211-240` — **bash `{"enabled": False}`(`:240` · D-233, 2026-09-21)**. `middleware_profile()`(`:172-193`)·`src/domain/middleware.py:107` `identify`는 **프로덕션 호출부 0건**(테스트만 — `sre_agent/tests/test_middleware_profile.py`·`tests/test_middleware/test_identification.py`) | ✏ **정정** — 원격(운영)의 "OS 근사"는 셸이 아니라 `mcp_server` 폴스타 도구(`polestar_process_snapshot`·`polestar_os_config`·`polestar_metric_trend`)뿐이다. 게이트 쪽 L3 보강(D-209 ⑥ · `noise_gate/infrastructure/host_diagnostic_collector.py:39-55`)은 별도 경로다 |
| 26 | 사건창 앵커 | §3.2 `investigation_guidance.py:19-34` | `sre_agent/sre_agent/application/investigation_guidance.py:20-26` `ANCHORED_TOOLS` 4종 그대로 · `om_*`는 현재값 전용이라 제외하고 `OPENMETRICS_NOTE`(`:180-197`)로 서술 규칙만 준다 | ✔ `apm_*` 구간 도구만 앵커에 넣는다. 실시간 도구(`apm_active_services` 등)는 `OPENMETRICS_NOTE`처럼 "현재 상태로만" 노트를 둔다 |
| 27 | 플레이북 | §5.4-a `config/apm_playbooks.yaml` | 기존 플레이북은 **코드 dict** `PLAYBOOK_NOTES`(`:124-169`, kind 6종). 플래그 전례는 `openmetrics_guidance_enabled`(`sre_agent/sre_agent/settings.py:146`) | ✏ 선례 병기 — yaml 정본안(P5 · D-270 ③의 선언 파일 자세)은 유지한다. kind 판정은 28과 함께 설계한다 |
| 28 | 알람 kind 분류 | (없음) | 게이트 `noise_gate/domain/process_rank.py:50-82` `classify_alarm_kind`와 조사측 동형 `investigation_guidance.py:93-112`가 **부분 문자열**로 판정한다. §5.5 표대로 `resourceType="apm.Instance"`·`alarmName=errorType`을 넣어 두 함수를 직접 호출하면(2026-09-29 실측 · 12개 표본) `WARNING_JVM_HEAP_MEM_HIGH`→`memory`(`mem`) · `ERROR_OUTOFMEMORY`→`memory` · `ERROR_JVM_CPU_HIGH_LONGTIME`→`cpu` · `ERROR_PROCESS_DOWN`→`process`가 되고, 나머지 8개는 `None`이다. 그러면 OS 플레이북 주입, `alarm_context_enricher.py:323·408` 프로세스 보강, L3 kind 프로파일이 붙는다 | ⚠ **신규 위험 R-16** — J4 정규화에서 `resourceType="apm.Instance"`를 OS 키워드보다 **먼저** 판정한다. 두 분류기에 **대칭**으로 넣는다(D-139 동형 재정의) |
| 29 | 결정적 사전수집 | (v2 미언급) | `sre_agent/sre_agent/application/evidence_prefetch.py:31-33` 폴스타 도구 3종만(D-197·D-209) | ➕ 확장 지점 — `apm_events`·`apm_app_health` 사전수집 편입은 J3 선택 항목 |
| 30 | 인용 판정 | §5.4-d "`tool_names`에 `apm_*` 포함" | `briefing_builder.py:126-137` `_is_cited` · `investigation_dispatcher.py:401` `tool_names=[실제 호출 도구]` | ✏ **배선 변경 0** — 호출된 도구명이 자동으로 실린다 |
| 31 | 조사 계약 | §3.2 `investigation_jobs.py:45,112-119` | `:41` `CONTRACT_VERSION="1"` · `:45` `REQUIRED_EVENT_FIELDS` · `:130-137` 검증 | ✔ 줄 이동 |
| 32 | 권고 표 | §3.2 `remediation.py:28-116` | `:36` `_HIGH_RISK_MIN_CONFIDENCE` · `:70-113` 11 kind — 항목이 `(문구, 위험도)` 튜플 | ✔ 검증법·롤백 필드(§5.4-c)는 튜플 구조 확장이 필요하다(J3) |
| **E** | **`noise_gate`(J4)** | | | |
| 33 | 수신기 골격 | §3.4 `BaseReceiver` 추상 `start()` | `noise_gate/alarm_server/base_receiver.py:20-58`(`_publish` → `xadd` `:44`) · **`__main__.py:24·40`가 `TcpReceiver`를 고정 기동**(수신기 선택 배선 없음) | ✔ 골격 성립 · 선택 배선은 J4 몫(§7 `__main__.py` 변경 항목과 일치) |
| 34 | `app_impact` 자리 | §5.5 "Plan 55 §3 · D-048.6 자리" | `noise_gate/application/nodes/agentic_enricher.py:279` `ctx.setdefault("app_impact", None)` — **예약 키만**(값 공급·판정 0건) | ✔ 자리 존재 · 판정 코드 0 |
| 35 | 소스 배지 | §5.5 "`source_label` family 제니퍼" | `noise_gate/domain/alarm.py:33` · `application/server_identity.py:69·163` `source_labels_for(event.db_id, …)` — **db_id 키**로 레지스트리를 본다 | ✏ `dbId="jennifer"`는 레지스트리 DB가 아니다(121 TP-9.2 "solutions 전용 family"). 라벨 해석 분기가 필요하다 |
| 36 | 목업 생성기 | J3 "Plan 65 목업 확장" | `noise_gate/scripts/mock_polestar_events.py`(D-139 이전 후 위치) | ✏ 경로 정정 |
| **F** | **문서·절차** | | | |
| 37 | SDD 산출물 위치 | §6 "`CAPABILITY-MAP-87.md` → `SPEC-*.md`" | D-244 — `spec/`에만 둔다 · `spec/`에 제니퍼 맵·SPEC 0건 | ✏ `spec/CAPABILITY-MAP-87.md`·`spec/SPEC-*.md` |
| 38 | J0 보고서 번호 | §6·§7 `docs/29_jennifer_integration_survey.md` | `docs/29_query_performance_test_plan.md`가 선점. 이번 가이드가 `docs/31`을 쓴다 | ✏ J0 실측 보고는 착수 시 번호를 다시 실측한다 |
| 39 | 매뉴얼 동반 | (없음) | D-255 — 사용자·관리자 기능은 `scripts/manual/` 원천 갱신 + `pytest tests/test_manual` | ➕ J4(알람 소스 배지)·J5(채팅 응답·배지)·J6(승인 대기함) 수용 기준에 추가 |
| 40 | 운영 설정 | §0.5 "`.env` 제니퍼 키 0건" | 루트 `.env`·`.env.example`·`mcp_server/.env`·`mcp_server/.env.example`에 `JENNIFER\|APM_` 0건 · `testdata/jennifer/` 없음 | ✔ 불변(착수 전) |
| 41 | 폴스타 연동 흔적 | §3.3 DDL `:3802-3860` | `testdata/pg/init/04_create_all_tables.sql:3802-3816`(`was_connection.jennifer_*`) · `:3850-3853`(`was_object.agent_id`) · `config/`에 `was_*` 등재 0건 | ✔ 불변 — U-10 그대로 |

**(2) 17일 이후 결정이 87에 미치는 영향**

| 결정 | 내용 | 87에 미치는 영향 | 반영 위치 |
|---|---|---|---|
| **D-251**(09-23) | 기준 경로 = 사다리 2단 `intent_orchestration` · 3단은 비교 arm | J5의 1차 대상은 **2단**이다. 본체 쪽 APM 조회는 2단 서브에이전트·처리기로 들어간다. `fault_diagnosis`(3단 전용)에 기대는 서술은 3단에 한정한다(표 #23) | §5.6 · §6 J5 |
| **D-270 · D-272**(09-28) | `plans/121` — 소스 카탈로그(정본 파생) · 처리기 계약 · 비SQL 시스템 레지스트리 등재(`backend: mcp`) · `allowed_sources` · **82 Wave 7 디스패치 흡수** | J5 선행이 Plan 82 Wave 7에서 **121 TP-9.1·9.2·10.5**로 바뀐다. APM은 Prometheus(`metric_query`)에 이은 **두 번째 비SQL 1급 처리기**가 된다. 121 §부록의 "새 시스템은 카탈로그 레코드 + 처리기 계약 + 매니페스트만 추가"가 J5의 목표 형태다 | §5.6 · §6 J5 · §10 G-5 |
| **D-233**(09-21) | 원격 조사 프로파일 bash 제거 | 운영 조사 LLM이 쓰는 도구는 MCP뿐이다. `apm_*`가 WAS 관점 증거의 **유일한** 앱 계층 경로가 된다. "OS 근사 폴백"은 폴스타 MCP 도구로 다시 정의한다(표 #25) | §5.4-a |
| **D-229 · D-240**(09-17 · 09-21) | 조사 LLM = OpenAI 호환 3필드(`MODEL`·`API_BASE`·`API_KEY`) · 실 LLM 테스트 = 로컬 MLX(두 평면 루프백이면 승인 없이) | J3 목업 시나리오의 실 LLM 확인은 로컬 MLX로 한다(로직 확인용 — 성능 결론은 내부망). 과금 API는 건별 승인(D-127) 그대로 | §6 수용 기준 · 가이드 §9 |
| **D-244**(09-22) | SDD 산출물은 `spec/`에만 | 맵·SPEC 경로 정정(표 #37) | §6 머리 |
| **D-255**(09-23) | 사용자·관리자 기능은 매뉴얼 동반 | J4·J5·J6이 화면·채팅 기능을 바꾸면 같은 작업에서 매뉴얼을 갱신한다(표 #39) | §6 수용 기준 |
| **D-209 ⑥**(09-09) | L3 post-gate 보강 — 게이트 kind별 read-only 명령 | 제니퍼 이벤트가 kind 분류에 걸리면 L3 명령이 WAS 호스트에 나간다(읽기 전용이라 안전하지만 판정이 틀린다 — R-16) | §5.5 · §9 |

**(3) 이 판정이 바꾸지 않는 것**: G-1(API 위주)·G-2(CNCF OpenMetrics · 1.0)·G-8(공식 MCP 미채택)은 그대로다. 표 #15는 G-2를 **지키기 위한**
결함 보고다. G-2를 뒤집자는 것이 아니다. D-195의 예약 범위(①②③)도 바뀌지 않는다.

> **[v3 읽는 법]** 이 표의 `mcp_server` 행(#1~#4·#5·#8·#11·#12·#14~#17)은 v3에서 **제니퍼 코드의 위치가 아니라 복제·참조할 전례**로 읽는다.
> 제니퍼 도구·설정·노출은 `apm_gateway/`로 간다(§0.7). 소비자 쪽 행(#18~#41)은 그대로 유효하다.

### 0.7 v3 — 독립 최상위 패키지 `apm_gateway/` (2026-09-29 · 사용자 승인 · **D-274**)

**(1) 결정** — 제니퍼 연동을 `mcp_server`의 세 번째 소스가 아니라 **독립 최상위 패키지 `apm_gateway/`**(자체 MCP 서버 · 독립 프로세스)로
만든다. 이를 위해 **D-119 ①을 개정했다(D-274)**: *"도메인 판정 로직이나 별도 자격증명을 가진 관측 소스는 독립 게이트웨이 패키지(자체 MCP
서버)로 둘 수 있다. 얇은 조회 래퍼는 종전대로 `mcp_server`로 확장한다."* D-119가 C안(별도 Prometheus MCP 서버)을 기각한 사유는 *"얇은 래퍼에
패키지·프로세스·인증 과중"*이었다. 제니퍼는 여기에 해당하지 않는다 — 인스턴스↔hostname 정합 · WAS 시그니처 판정 · 이벤트 폴링·정규화 ·
OpenMetrics 노출을 모두 갖고, 소비자가 셋(`noise_gate`·`sre_agent`·`src`)이다. **G-1(API 위주)·G-2(OpenMetrics 1.0)·G-8(공식 MCP 미채택)은
그대로다** — 새 안은 "우리가 만든 게이트웨이가 Open API를 직접 호출"하는 것이다.

**(2) 채택 근거**

| # | 근거 | 설명 |
|---|---|---|
| 1 | 토큰 격리 | 제니퍼 토큰 하나로 쓰기·제어 API까지 열린다(§0.4 · §1.3-3). 이 토큰을 폴스타 DB 자격증명과 **다른 프로세스**에 둔다 |
| 2 | 장애 격리 | 제니퍼 API 지연·폴링·캐시가 폴스타 조회 프로세스(`mcp_server` 9099)에 영향을 주지 않는다 |
| 3 | 선택 배포 | 제니퍼가 없는 환경에는 패키지를 배포하지 않는다 — `mcp_server`에 끄는 스위치를 두는 것보다 단순하다 |
| 4 | WAS 판정 단일 정의 | 결정적 판정을 게이트웨이 `domain/` 한 곳에 둔다. R-16의 `classify_alarm_kind` 이중 정의 같은 문제가 WAS 판정에서 반복되지 않는다 |
| 5 | 비용 시점 | 코드가 0건이라 지금 바꾸면 문서 비용만 든다 |

**(3) 구조** — D-139 최상위 패키지(`sre_agent`(D-118)·`mcp_server`와 같은 격)

```
apm_gateway/                  자체 pyproject · 자체 cwd · 루트 venv 공유(mcp_server 전례 · 둘 다 ≥3.11)
│                             2단 중첩(D-139 ③ — 자체 cwd라 무해) · 독립 프로세스 · 자체 MCP 서버(SSE · 정적 Bearer D-125)
├─ apm_gateway/
│   ├─ domain/                WAS 시그니처 결정적 판정 · 이벤트 모델 · 레벨 매핑 (순수 함수 · 벤더 무지)
│   ├─ adapters/jennifer/     Open API 클라이언트 · GET·경로 허용목록(코드 상수) · 벤더 리터럴은 여기에만
│   ├─ application/           인스턴스↔hostname 정합 · 상위 N 축약·마스킹 · 이벤트 폴러(→ Redis alarm:raw)
│   └─ interface/             MCP 서버(apm_* 도구) · Bearer · 감사 · (J7) GET /metrics/apm
├─ tests/                     경계 불변식 · 허용목록 · 계약(recorded JSON) · 폴러 멱등
├─ scripts/                   J0 채집·마스킹 도구 등
└─ testdata/                  recorded JSON(마스킹본) — D-139 "각 패키지가 자기 testdata를 소유"
```

- 게이트웨이 소유 설정·정책 파일(정합 파일 · 이벤트 레벨 · WAS 임계)은 **`apm_gateway/` 아래**에 둔다(「예정」 — 자체 cwd 기동이라 루트
  `config/` 참조를 피한다 · `mcp_server/config.toml` 전례). 정확한 경로는 J1에서 정한다.
- 정본 레지스트리(`config/db_registry.yaml`의 `apm` 솔루션·`jennifer` family)는 **본체 소유**라 루트 `config/`에 그대로 둔다(§5.6).

**(4) 경계 불변식** — `apm_gateway` ↔ `src`·`noise_gate`·`sre_agent`·`mcp_server` **양방향 import 0**. 통신은 **MCP · Redis Stream(`alarm:raw`) ·
HTTP 스크레이프** 계약만 쓴다. `sre_agent/tests/test_boundary.py`(AST로 금지 최상위 모듈 import 검사) 방식의 불변식 테스트를 J1 수용 기준에 넣는다.

**(5) 무엇이 게이트웨이로 가고 무엇이 남는가**

| 기능 | v2.2 위치 | **v3 위치** | 비고 |
|---|---|---|---|
| Open API 호출 · 허용목록 · 레이트 리밋 · 토큰 | `mcp_server/apm_client.py` | `apm_gateway/adapters/jennifer/` | 벤더 리터럴 격리 |
| `apm_*` 도구 표면 · 반환 계약 · Bearer · 감사 | `mcp_server/apm_tools.py` · `server.py` | `apm_gateway/interface/` | Bearer 미들웨어(`mcp_server/mcp_server/server.py:31` 약 40행)·반환 계약은 **복제**(소규모) |
| 인스턴스↔hostname 정합 · 축약 · 마스킹 | `mcp_server` | `apm_gateway/application/` | 폴스타 `was_object`는 `mcp_server` MCP 호출(7) |
| 이벤트 폴러 · 정규화 | `noise_gate/alarm_server/jennifer_poll_receiver.py` · `noise_gate/domain/apm_event.py` | `apm_gateway/application/`(폴러) · `apm_gateway/domain/`(이벤트 모델·레벨 매핑) | 게이트웨이가 `alarm:raw` 생산자(XADD — `noise_gate/alarm_server/base_receiver.py:44` 형식) · **G-4 폴링 경로 해소** |
| WAS 시그니처 결정적 판정 | `sre_agent/domain/severity_signatures.py` 확장 | `apm_gateway/domain/` | 판정 결과를 도구 반환·이벤트 페이로드에 실어 두 소비자가 함께 쓴다(D-035) |
| (J7) `GET /metrics/apm` | `mcp_server` `custom_route` | `apm_gateway/interface/` | 직렬화기 복제 vs 공용 추출은 **G-12** |
| 알람 kind 분류(`classify_alarm_kind`의 apm 선판정 — R-16) | noise_gate · sre_agent | **남는다** — noise_gate(게이트)와 sre_agent(지침 동형) | 게이트웨이는 `resourceType="apm.Instance"`를 정확히 채워 보낸다 |
| 조사 지침 · `ANCHORED_TOOLS` · APM 플레이북 · 권고 표 | sre_agent | **남는다** — sre_agent | 조사 쪽 프롬프트·권고는 sre_agent 소관 |
| `app_impact` 승격 판단(게이트 정책) | noise_gate | **남는다** — noise_gate | 게이트웨이 판정 결과를 입력으로만 쓴다 |
| L2 실행기(J6) | 신규 `remediation/`(G-7) | **남는다**(게이트웨이 밖) | 게이트웨이는 읽기 전용 · 실행 자격증명 분리 |

**(6) 소비자 연결**

| 소비자 | 연결 방식 | 지금 코드 | v3 이후(「예정」) |
|---|---|---|---|
| `sre_agent`(조사) | MCP | `_build_mcp_servers()`가 `{"polestar": …}` 하나(`sre_agent/sre_agent/interface/mcp_service.py:105-118`) | `"apm"` 항목 추가 + `AgentSettings.apm_mcp_url`·`apm_mcp_token`. holmes `Config.mcp_servers`는 dict라 여러 서버 등록이 가능하다(D-119 본문 실측) |
| `noise_gate`(이벤트) | Redis Stream `alarm:raw` | `alarm_server`만 생산자 | 게이트웨이도 생산자. `REQUIRED_EVENT_FIELDS`(`investigation_jobs.py:45`) 계약 테스트를 게이트웨이 쪽에도 둔다(복제된 계약 — import 없음) |
| `noise_gate`(`app_impact` 조회) | MCP | MCP 클라이언트 전례 `noise_gate/infrastructure/sre_agent_client.py`(SSE · Bearer · 재연결) | 같은 형식의 게이트웨이 클라이언트 신설 |
| `src` 2단(채팅 질의) | MCP | `DBHubConfig.server_url` 하나(HEAD `src/config.py:173`) · 처리기 코드 0 | `plans/121` 처리기가 **두 번째 MCP 엔드포인트**를 호출. 설계 시점 비용뿐 — G-5′ 권고 ⓐ와 정합 |
| Prometheus 등 수집기(J7) | HTTP 스크레이프 | — | 게이트웨이 `GET /metrics/apm` |

**(7) 폴스타 정합 1순위 브릿지(`was_object`)** — 게이트웨이는 **폴스타 DB 자격증명을 갖지 않는다.** `was_object` 조회는 게이트웨이가 `mcp_server`를
**MCP로 호출**해 수행한다(TTL 10분 캐시). 의존은 게이트웨이 → `mcp_server` **한 방향**이다. 기존 `execute_sql`은 조사 배치에서 비노출(D-122)이므로,
필요하면 `mcp_server`에 폴스타 고수준 도구 1종(「예정 이름」 예: `polestar_was_instances` — 값 인자 · 서버측 SQL 조립)을 추가하는 것을 J1 범위로 둔다.
OpenMetrics 노출의 `nodename`(= `server_name`) 역해소(§5.9 2026-09-22 정정)도 같은 경로로 한다.

**(8) 자격증명 배치**

| 프로세스 | 갖는 자격증명 | 갖지 않는 것 |
|---|---|---|
| `apm_gateway` | 제니퍼 Open API 토큰 · Redis 접속(`alarm:raw` XADD) · `mcp_server` 호출용 Bearer(클라이언트) · 자기 MCP 서버 Bearer | 폴스타 DB · 실행 자격증명 |
| `mcp_server` | 폴스타 DB 연결 문자열 · Prometheus 인증 헤더 · 자기 Bearer | 제니퍼 토큰 |
| `sre_agent` | 두 MCP 서버의 클라이언트 Bearer · 조사 LLM 키 | 제니퍼 토큰 · 폴스타 DB |
| 본체(`src`·`noise_gate` 워커) | Redis · 두 MCP 서버의 클라이언트 Bearer | 제니퍼 토큰 · 폴스타 DB(`DB_BACKEND=dbhub`일 때) |
| `remediation/`(J6 · G-7) | 실행 자격증명(WAS 관리 API·SSH·LB) | 제니퍼 토큰 |

**(9) §2.8 대안 ③ 기각 사유의 재해석** — ③은 "`sre_agent`가 **제니퍼 공식 MCP**에 직결"이었다. 기각 사유 (b) 토큰이 클라이언트 헤더로 넘어감 ·
(c) 허용목록·감사 부재 · (d) P4 통제 밖은 셋 다 **공식 MCP 직결에만** 해당하고 자체 게이트웨이에는 해당하지 않는다(게이트웨이는 토큰을 서버측에
두고, 허용목록·감사·도구 수를 우리가 정한다). (a) D-119 ① 위반은 D-274로 해소됐다. **공식 MCP 직결 기각과 G-8은 유지**한다.

**(10) 비용과 완화**(§9 R-19~R-22)

| 비용 | 완화 |
|---|---|
| 감사가 두 프로세스로 나뉜다(D-119 ④ 약화) | 같은 감사 형식(PromQL `_audit` 전례 — logger 1줄) · 조사 id·thread_id를 호출 인자로 전파해 합쳐 볼 수 있게 한다 |
| 운영 대상 1세트 추가(프로세스·포트·Bearer·헬스체크) | 제니퍼가 있는 환경에만 배포 |
| Bearer 미들웨어·반환 계약 복제 | 소규모(약 40행 + 헬퍼) — 계약 테스트로 드리프트 감지 |
| OpenMetrics 직렬화기(`om_exposition.py` 321행) | 복제할지 공용 추출할지 J7 착수 시 결정(G-12). R-17(2.0 협상) 수정은 어느 쪽이든 적용 |
| 게이트웨이 → `mcp_server` 호출 1 hop | TTL 캐시로 흡수 |
| 품질 게이트 범위 | `arch_check`·`overfit_check` 스캔 대상에 `apm_gateway` 편입 여부는 구현 시 결정(G-11) · 벤더 리터럴은 `adapters/jennifer/`에 격리 |
| 문서 | `CLAUDE.md` 「저장소 지도」·「패키지 경계」 표 갱신은 **패키지를 실제로 만드는 J1의 산출물**이다(지금은 수정하지 않는다) |

### 0.8 v3.1·v3.2 — 로컬 Docker 제니퍼 검증 환경 (J0-L · 2026-09-29 · 사용자 지시)

> 사용자 지시 원문: *"제니퍼도 도커로 설치하여 검토할 수 있도록 계획에 포함시켜라."* · **[v3.2] 제품 사실은 메인 세션 조사로 채웠다**([J-24] — 공식 GitHub 샘플 2종 ·
> 공개 설치본 · 평가판 페이지·신청 폼 · 설치 가이드 1~4장 · 릴리즈 노트 · Docker Hub). 공개 자료가 답하지 않은 것은 「확인 불가」로 표기했고 추정은 △로 적었다.
> v3.1 시점의 「조사 대기」 칸은 모두 없앴다. 설계 반영은 메인 세션 권고를 사용자의 *"권고에 맞게 진행"* 지시 범위로 채택한 것이다.
> **[v3.3]** 2026-09-29 로컬 Docker 실측(J0-L-a)으로 이 절의 전제 일부를 정정했다 — 정정한 칸은 `[v3.3]` 표지 · 근거는 **§0.10**.

**(1) 왜** — J0(선행 실측)은 운영 제니퍼 접근 권한과 테스트 토큰이라는 외부 전제에 막혀 있다. 로컬 Docker 제니퍼가 있으면 J0 가운데 **운영 데이터가
필요 없는 부분**(API 응답 형태 · recorded JSON 1차 채집 · 허용목록 검증 · 쓰기·제어 API 목록 실측 · 이벤트 발생 재현)을 먼저 푼다. 그래서 J0을
**J0-L(로컬 Docker — 운영 접근 없이 착수)**과 **J0-O(운영 실측 — 인스턴스↔hostname 일치율 · 운영 버전 · 토큰 정책 · PII 샘플)**로 나눈다.
**[v3.2]** 에이전트 접속에는 **IP 기반 라이선스**가 필요하므로(라이선스가 없으면 데이터 서버가 에이전트 접속을 거부한다) J0-L은 다시 **J0-L-a(라이선스 없이
준비)**와 **J0-L-b(평가판 2주 창 안의 채집)**로 나눈다 — (9).

**(2) 구성 요소**

| 구성 | 역할 | 사실(v3.2 · [J-24]) |
|---|---|---|
| 제니퍼 서버(데이터 서버 + 뷰 서버) | Open API · 이벤트 · 콘솔(토큰 발급) · 에이전트 배포 | **공식 Docker 이미지는 없다**(Docker Hub `jennifersoft`에 제품 이미지 없음 · 제3자 이미지 미사용). 공식 GitHub 샘플 2종(`jennifer-container-sample` 2023 · `jennifer-install-script` 2021)은 **데이터·뷰 서버를 한 컨테이너**에 넣는다 — 둘 다 **JDK 8 기반**인데 서버 5.6.5+는 **JDK 17/21 필수**라 **Dockerfile을 새로 쓴다**(베이스 `eclipse-temurin:17`/`21` — arm64·amd64 제공). 설치본은 공개 S3 `jennifer-server-5.7.0.1.zip`(약 591MB · 2026-09-04)에서 로그인 없이 받아지지만 **직접 링크에 평가판 약관이 적용되는지는 「확인 불가」**. 포트: 뷰 서버·Open API **7900** · 데이터 서버·에이전트 **5000**. 힙: 데이터 2g · 뷰 2g · **[v3.3 실측]** 베이스 `eclipse-temurin:21-jdk`로 **arm64 네이티브 기동 성공** · 설치본은 사용자 확정으로 공개 S3에서 받았다(§0.10 #1 · (10) 3) |
| 데이터 발생용 WAS + 제니퍼 Java 에이전트 | 트랜잭션·힙·스레드 데이터 발생 | 부착 = JVM 옵션 두 개 `-javaagent:<agent>/jennifer.jar -Djennifer.config=<agent>/conf/jennifer.conf` · 설정 `server_address`·`server_port`(5000)·`domain_id`(기본 1000)·`inst_name` · 지원 JDK 8~26(문서 기준 — **[v3.3] 뷰 서버 배포본에는 맞지 않는다**) · 에이전트 입수 = 로컬 뷰 서버 `GET /download/agent/java/latest`(공식 샘플 방식 · 인증 없음 — **[v3.3] 실측: 배포본은 5.5.2.5이고 경로 끝 값과 무관하게 같은 파일**). 베이스는 ~~`tomcat:9.0-jdk17-temurin`(arm64 제공)~~ **[v3.3] `tomcat:9.0-jdk11-temurin` — 5.5.2.5는 JDK 17에서 JVM 기동을 실패시킨다(§0.10 #5 · R-28). 최신 5.6.x 에이전트는 라이선스 키로 공식 다운로드((10) 9)** · **[v3.3]** WAS 환경에 `JENNIFER_*` 이름을 두지 않는다 — 에이전트가 설정으로 읽는다(§0.10 #6 · R-29). **Spring Boot 전용 공식 가이드는 없다**(같은 두 옵션으로 붙인다) |
| 샘플 앱(v3.2 — 계획 범위 편입) | 이벤트·지표를 일부러 발생 | **공식 데모 앱·부하 가이드는 없다.** 자체 샘플 앱의 최소 엔드포인트 = **느린 응답**(`/slow?ms=`) · **힙 증가**(`/heap?mb=`) · **예외**(`/error`) · (선택) **SQL 호출**(`/sql?ms=` — 컨테이너 내부 DB) — **[v3.3] 구현 = `/sample/slow.jsp?ms=` · `/sample/heap.jsp?mb=`(`reset=1`로 해제) · `/sample/error.jsp`(SQL 엔드포인트는 만들지 않았다)**. 이벤트는 콘솔 [관리 > 룰 > EVENT룰]에서 **임계를 낮춰** 일으킨다(임계 변경 API는 없다 — §0.9 ①) |
| 부하·장애 재현 스크립트 | 샘플 앱 호출 반복 | 우리가 작성(「예정」 · 127.0.0.1 게시 포트로만 호출) |
| 녹화 하네스 | 허용목록 경로(§5.2(e)) 전건 호출 → 마스킹 → recorded JSON(출처 `local-docker`) | **【현재 가능 — v3.3】** `scripts/record_openapi.py`(GET만 · 허용목록 밖 경로·쿼리 `token`·허용 밖 쿼리 키·경로 변수 누락은 **네트워크 호출 전에** `NotAllowedError` · 발견 순서 domain → instance → realtime·activeService → metrics → dbmetrics(snake_case 6지표) → transaction/time(1분 창을 거슬러 첫 비지 않은 창) → txid·profile.txt·sql → dbsearch event·error → status 3종(시 단위) → deploy · 오류 응답도 variant `ok`·`domain_not_connected`·`missing_param`·`status_N`으로 저장 · `--keep-raw`는 원본을 `recorded/raw/`(gitignore)에) + `scripts/masking.py`(키 기반 password·token·email·phoneNumber·allowIp·userId 등 · 값 기반 이메일·휴대폰·주민번호 · SQL 리터럴 · URL 쿼리 값 · `profile.txt`는 SQL 줄 숫자만 · `ops-masked`는 호스트명·IP·인스턴스명을 결정적 가명 `host-<8자>`·`10.x.y.z`로 — 조인 보존) + `scripts/jennifer_catalog.py`(허용목록 16템플릿 사본 — 아래 (6)) · fixture 스키마 `fixture_version`·`source`·`jennifer_version`·`recorded_at`·`request{method,template,path,query,accept}`·`response{status,content_type,body_json\|body_text}`·`variant`·`label`·`masking` + `index.json` · 현재 `recorded/local-docker/` 21건(라이선스 없음 — J0-L-b에서 재녹화) · 1차 프로브 `scripts/probe_openapi.py`(40건)는 그대로 둔다 |
| (선택) Prometheus 픽스처 연결 | J7(`/metrics/apm`) 스크레이프 검증 | 기존 `testdata/prometheus/docker-compose.yml`(9190) 재사용 |
| 폴백 — 목(mock) Open API 서버 | 라이선스를 못 구하거나 2주가 지난 뒤 · **[v3.3] 게이트웨이 계약 테스트의 상대(J1~)** | **【현재 가능 — v3.3】** `scripts/mock_openapi.py`(stdlib · 127.0.0.1 전용 · 기본 포트 17901) — (6) |

**(3) 배치 — `apm_gateway/testdata/jennifer/`(채택)** — Dockerfile · compose · compose 전용 `.env.example` · 에이전트 설정 · 샘플 앱 · 재현 스크립트 · 녹화 하네스 ·
목 서버 · README. D-139에 따라 패키지가 자기 `testdata/`를 소유한다. 이 폴더는 **J0-L에서 먼저** 만들고 패키지 코드는 J1에서 만든다. 대안인 최상위 `db2/`형 폴더는
택하지 않는다 — 루트 `db/`·`db2/`·`redis/`는 본체 공용 인프라이고 `testdata/prometheus`·`testdata/itam`은 본체 테스트 픽스처다. 제니퍼 환경은 게이트웨이만 쓰는 검증
픽스처이고 게이트웨이를 배포하지 않는 환경에는 필요 없다. **J0-L 폴더 생성은 87의 코드 착수로 보지 않는다**(검증 픽스처 · 파일명 `-TODO` 유지 판단은 J1 착수 시 재판정 — **[v3.3] 폴더가 생겼고 판단은 그대로다**(§10)).
**설치본(zip)과 라이선스 파일은 저장소 밖에 둔다** — ~~compose 전용 `.env`의 「예정 이름」 `JENNIFER_SERVER_ZIP`·`JENNIFER_LICENSE_FILE`(저장소 밖 절대 경로)로 받아
읽기 전용으로 마운트하고, `.gitignore`에 `apm_gateway/testdata/jennifer/.env`·`*.zip`·`license*`·녹화 원본 디렉터리를 더한다(J0-L-a 산출물 · 현재 `.gitignore`에 해당 패턴 없음 — 실측).~~
**[v3.3 실제]** 키는 **`JENNIFER_DIST_DIR` 하나**다 — 설치본 `jennifer-server-<버전>.zip`과 (받으면) `license` 파일을 함께 둔 저장소 밖 디렉터리이고, 컨테이너 `/dist`에
읽기 전용으로 마운트한다(서버 entrypoint가 설치본을 볼륨에 1회 풀고 `license`가 있으면 복사한다). `.gitignore`는 루트 `.gitignore`에 3패턴(`apm_gateway/testdata/jennifer/**/*.zip` ·
`…/**/license*` · `…/recorded/raw/`)을 더했고, compose `.env`는 기존 `.env` 패턴이 덮는다(`git check-ignore` 실측). 나머지 키는 §7.0.

**(4) 통제 조건**

| 통제 | 내용 |
|---|---|
| **포트 이중 구조** | 호스트 게시는 **127.0.0.1만** — 뷰 서버·Open API `127.0.0.1:<비점유 포트> → 7900`(제안 **17900**) · 샘플 앱 `127.0.0.1:<비점유> → 8080`(제안 **18080**). **데이터 서버 5000은 compose 내부 네트워크 전용이고 호스트에 게시하지 않는다**(에이전트는 내부 네트워크로 접속). 기존 로컬 compose(`testdata/prometheus`·`itam`·`pg`·`db`·`db2`·`redis`)는 전 인터페이스 바인딩이다 — 이 환경부터 루프백으로 고정한다 |
| 포트 충돌 회피 | 2026-09-29 실측 점유: compose `5433`·`5434`·`6380`·`50000`·`3307`·`9190`·`9101`·`9102` / 프로세스 `8000`·`6379`·`9097`·`9098`·`9099`·`9100`·`8080`(MLX)·`18981`·`18982`. 제안 17900·18080은 이 목록 밖이다 — 착수 시 `lsof -i :17900`로 다시 확인한다 |
| **라이선스 IP ≠ 호스트 바인딩** | 라이선스의 Agent IP·Server IP는 **compose 사용자 정의 네트워크의 고정 서브넷·고정 IP**(서버 컨테이너·WAS 컨테이너 — 「예정」 예: `172.29.87.0/24`의 `.10`·`.20`)로 설계한다. 127.0.0.1 게시와는 **다른 개념**이다(라이선스는 루프백 불가[J-24]). Docker Desktop(Mac)에서 맥 LAN IP를 적어야 하는지 컨테이너 IP를 적어야 하는지는 **「확인 불가」 — 벤더 확인 사항**(사용자 할 일 1) · **[v3.3]** compose 초안 = 서버 `172.29.87.10` · WAS `172.29.87.20`(`172.29.87.0/24` 고정) — 벤더 확인은 여전히 필요 |
| **Bootstrap Check** | 데이터 서버는 CPU 2코어·메모리 8GB·NFS 아님·**가상 환경 아님** 중 하나라도 어긋나면 기동하지 않는다. ~~Docker Desktop은 Linux VM 위라 감지 여부가 「확인 불가」 — **로컬 한정으로 `server_data.conf`에 `jennifer_bootstrap_check=false`**를 둔다. 매뉴얼의 *"데이터 유실이 발생할 수 있음"* 경고는 **로컬 검증 데이터라 수용**한다(채집한 recorded JSON이 정본이고 원천 데이터는 버려도 된다).~~ **[v3.3 실측] 끄지 않는다** — 5.7.0.1 데이터 서버는 Docker를 `WARN BootstrapChecks - virtual environment is not recommended (Docker Container)`로 경고만 하고 `Data server startup completed`까지 간다(§0.10 #2). 자원이 모자라 기동이 막힐 때만 compose `.env`의 `DISABLE_BOOTSTRAP_CHECK=1`로 끈다(로컬 한정 옵트인 · 기본 0 · entrypoint가 `server_data.conf`에 반영). **운영에는 적용 금지** |
| 시간대 | 데이터·뷰 서버는 TIMEZONE이 같고 시각 차이가 10초 미만이어야 한다 — 한 컨테이너에 두고 `TZ`를 WAS와 같게 맞춘다 |
| 자원 | Docker Desktop VM 메모리 **8GB 이상**(힙 합계 데이터 2g + 뷰 2g + WAS · Bootstrap 기준 — 권장값은 추정 △) · CPU 2코어 이상 · 디스크 여유(성능 데이터 파티션 4GB 미만 경고 · 100MB 미만 자동 종료) · **[v3.3]** 실측 호스트 Docker Desktop 28.4.0 · CPU 10 · 11.7GiB에서 Bootstrap Check를 켠 채 기동 |
| 옵트인 | Docker 통합 테스트는 `RUN_DOCKER_IT=1`(기존 Prometheus 픽스처 전례 · 기본 스위트 skip) |
| 다운로드 | 설치본·베이스 이미지 다운로드는 **사용자가 명시적으로 실행**하는 절차로만 둔다(스크립트가 자동으로 받지 않는다) |
| 라이선스·반입 | 평가판·장기 라이선스 확보와 설치본 반입은 **사용자 몫**이다 — (10) |
| 과금·네트워크 | 로컬 인스턴스라 과금 경로가 아니다. `tests/conftest.py:85-86`(`_is_external`)이 사설·루프백 IP를 내부로 보므로 127.0.0.1 게시 포트로 붙는 계약 테스트는 외부 네트워크 가드에 막히지 않는다(실측) |
| HTTP·SNI | 로컬은 `http://127.0.0.1:17900`(HTTP)로 붙는다 — 5.7.0.1의 SNI 검증은 HTTPS에서만 관계있다 |
| 데이터 출처 표지 | recorded JSON마다 출처(`local-docker` · `mock` · `ops-masked`)를 표지한다. 로컬 채집분과 운영 마스킹분을 섞지 않는다 |
| 쓰기·제어 API | 쓰기·제어 API 실측(U-5·U-8)은 **로컬에서만** 한다. 운영 뷰 서버에서는 하지 않는다 · **[v3.3]** J0-L-a는 GET으로 라우팅만 확인했다(쓰기 미실행 · §0.10 #15) |
| **환경변수 이름**(v3.3) | 에이전트를 붙인 JVM의 환경(WAS 컨테이너 · 셸 프로필)에 **`JENNIFER_*` 이름을 두지 않는다** — 에이전트가 이 접두의 환경변수를 설정으로 읽어 `jennifer.conf`보다 앞세운다(§0.10 #6 · R-29). compose 스크립트 전용 변수는 `AGENT_*` |
| 잠금 파일(v3.3) | 비정상 종료 뒤 `db_view/db.lock`·`db_data/db.lock`이 볼륨에 남아 재기동이 거부된다 — 서버 entrypoint가 기동 때 지운다(그 볼륨을 그 컨테이너만 쓰는 로컬 한정 · §0.10 #8) |

**(5) U-항목의 J0-L / J0-O 분류**

| # | 항목 | J0-L(로컬 Docker)에서 푸는 것 | J0-O(운영)에서만 푸는 것 |
|---|---|---|---|
| U-1 | 이벤트 유형 명칭 · EVENT 룰 | 설치본 기본 룰·유형 명칭(버전 같을 때) | 운영 커스텀 룰 목록 |
| U-2 | `/api/dbmetrics/*` 응답 필드 · `interval_minute` · 창 상한 | ~~**해소**(형태)~~ **[v3.3] 부분** — 지표 식별자 카탈로그(`/api/metrics` · snake_case) 확인 · 응답 필드·`interval_minute`는 J0-L-b(라이선스 필요) | 운영 값 차이 확인만 |
| U-3 | 보존 기간 | 설치 기본값 | 운영 설정값 |
| U-4 | 인스턴스↔hostname 일치율 | `Instance.hostName` 형식(컨테이너 호스트명) | **일치율 전용** |
| U-5 | 토큰 권한 등급 · 사용량 제한 단위 · 초과 응답 · 쓰기 API 거부 여부 | ~~**해소**(로컬 토큰으로 쓰기·제어 API 실측 — 로컬이라 안전)~~ **[v3.3] 부분** — 사용량 단위 = 요청 수(500 포함) · 쿼리 `?token=` 통과 · Open API 기본 활성 · 쓰기·관리 경로가 같은 토큰으로 라우팅됨(GET으로만 확인) · 남은 것 = 초과 시 응답 · 실제 쓰기 거부 여부(J0-L-b · 로컬만) | 운영 토큰 정책 협의 |
| U-6 | 응답 PII 포함 여부 | 필드 구조(어느 필드에 무엇이 실리나) | 실 데이터 샘플 → `pii_probe.py` |
| U-7 | 뷰 서버 부하 상한 | 대략 응답 시간(참고 — ~~Bootstrap off라~~ **[v3.3]** 로컬 Docker라 성능 결론 아님) | 운영 조직 확인 |
| U-8 | 덤프·PLC 조작의 API 노출 | 설치본 콘솔로 **확인**(공개 API 0건 — [J-23] #43 · 벤더 문의 병행) | 운영 버전과의 차이 |
| U-9 | 어댑터 배포 정책 · SNMP 메시지 패턴 | SNMP 어댑터 동작 재현(G-4b 착수 시) | 운영 배포 정책 |
| U-10 | 폴스타 `was_object` 운영 실재 | — | **전용**(폴스타) |
| U-11 | (판정 완료) 공식 MCP `tools/list` | 설치본에 LLM 프록시가 포함되는지 **「확인 불가」**(프록시는 별도 zip `jennfer-llm-1.x.x.zip`[J-17]) — (7) | — |
| U-12 | 버전 · v1 엔드포인트 실 응답 | 로컬 5.7.0.1 응답(recorded JSON 1차) · **[v3.3] 부분** — v1 경로 존재 · 오류 모델(HTTP 500 + `exception.message`) · `.xml`·POST 변형 응답 확인, 실 응답 필드는 J0-L-b | 운영 버전·차이(R-25) |
| U-13 | `errorType` 전 목록 · kind 분류 | 기본 룰 유형으로 분류 테스트 | 운영 커스텀 룰 |
| U-14 | (v3.2) 시간 기준·시간대 · X-View 기록 전수 여부 | 응답 시각 형식·단위 · X-View(`/api/transaction/time`)가 전 트랜잭션인지 표본인지 | 운영 뷰·데이터 서버 시계·시간대 설정 |

**(6) 폴백** — ~~라이선스를 구할 수 없거나 평가판 2주가 지나면 **목(mock) Open API 서버**로 대체한다. 응답 원천은 J0-L-b에서 채집한 recorded JSON(있으면)이고, 없으면~~
~~정본 스펙(OpenAPI 3.0.3)의 스키마로 만든 합성 응답이다(스펙의 예시 포함 여부는 **「확인 불가」** — 대조에서 다루지 않았다 · 폴백 착수 시 확인). J0-O 운영 채집분(마스킹)이~~
~~생기면 그것으로 바꾼다. **어느 쪽이든 J1 이후 계약 테스트의 정본은 recorded JSON**이다. 목 서버는 허용목록·반환 계약·폴러 멱등 검증에는 충분하지만, 실제 EVENT 발생·필드~~
~~변형은 재현하지 못한다(한계 표기). 라이선스 만료 뒤 로컬 제니퍼는 에이전트 데이터를 받지 못한다.~~
**[v3.3 구현 — 목 Open API 서버 `scripts/mock_openapi.py`]** 응답 원천은 **녹화 fixture**(`recorded/<source>/` — 지금은 `local-docker` 21건)이고, **스펙 스키마로 만든 합성 응답은
만들지 않았다**(추정한 모양을 계약으로 굳히지 않는다). 모드 = `fixtures`(녹화 그대로) · `connected`(`ok` fixture만 · 이벤트 주입) · `disconnected`(라이선스 없음 흉내). 흉내 내는 실서버 동작(§0.10 실측) =
Bearer 인증 · 쿼리 `?token=` 수용(게이트웨이가 거부해야 함을 드러내려고) · 토큰 없음 401 · 필수 파라미터 누락 500 `exception.message` · `profile.txt` + JSON Accept 404 · 미연결 500
*"Domain is not connected"*(deploy는 문자열 본문 · `.xml` 변형은 XML 예외) · 허용목록 밖 경로(민감 GET·쓰기 경로·`.xml`·POST 변형)는 실측과 같은 상태·모양(개인정보 자리는 canary 값).
제어 경로(인증 없음) = `GET /__mock/hits`(접근 기록 — `allowlisted`·`query_token` 표지) · `POST /__mock/events`(EventData 13필드만 · `time` 필수 — 폴러 커서·멱등 검증용) ·
`GET /__mock/usage` · `POST /__mock/mode` · `POST /__mock/reset`. **이벤트는 주입으로 만든다.** **한계**: 실제 EVENT 발생·필드 변형은 재현하지 못한다 · 지금 fixture는 라이선스 없는
녹화본이라 `ok`는 `domain`·`realtime/instance`(빈 결과)·`metrics`·`transaction/time`(빈 결과)뿐이고 나머지는 `domain_not_connected`다 — `connected` 모드에서 `ok` fixture가 없는
템플릿은 501 `{"mock_error": "fixture 없음: …"}`로 답한다 · 실데이터 모양은 J0-L-b 재녹화 뒤에 채워진다. J0-O 운영 채집분(`ops-masked`)이 생기면 그것을 우선한다.
**어느 쪽이든 J1 이후 계약 테스트의 정본은 recorded JSON(fixture)**이다. 라이선스 만료 뒤 로컬 제니퍼는 에이전트 데이터를 받지 못한다.
**카탈로그 사본** — `scripts/jennifer_catalog.py`는 허용목록 16템플릿(필수·선택 파라미터 · `profile.txt` Accept `text/plain` · 도메인 필요 여부 · 미연결 시 빈 결과 경로)과 EventData 13필드,
허용목록 밖 경로의 실측 응답, `match_template()`(정확 일치만 — `..`·`//`·`%`·`.xml` 거부)을 담는다. **허용목록 정본은 J1 `apm_client.py`(「예정」)이고 이 파일은 J0 도구용 사본**이다 —
J1에서 두 목록을 대조하는 테스트를 둔다(§6 [v3.3] 수용 기준).

**(7) G-8 재검토와의 관계** — 로컬 설치본에서 공식 MCP(LLM 프록시)의 `tools/list`를 실측할 수 있을지는 **「확인 불가」**다 — 서버 설치본에 LLM 프록시가 포함되는지
조사로 확인되지 않았다(프록시는 별도 zip · 획득 경로 미기재[J-17]). 된다 해도 §0.5 재검토 트리거 ⓒ의 **입력만** 얻는 것이다. ⓐ(운영에 사내 설치형 프록시 가동)·ⓑ(v1 제거
공지)는 별개이므로 **판정(G-8 미채택)은 바꾸지 않는다.** 벤더 공개 프록시 모드는 금지(D-120)다.

**(8) 위험** — R-23(라이선스 2주·IP 기반·사이트당 1회) · R-24(arm64 — 네이티브 먼저, 실패 시 amd64 에뮬레이션 · **[v3.3] 서버 해소**) · R-25(로컬과 운영의 버전 차이) · ~~R-26(Bootstrap Check off의
데이터 유실 — 로컬 한정)~~ **[v3.3] 철회** · R-27(설치본 직접 링크 약관 불확실 · **[v3.3] 사용자 확정으로 수용**) · **[v3.3] R-28**(배포 에이전트 5.5.2.5 ↔ JDK 17) · **R-29**(`JENNIFER_*` 환경변수 충돌) — §9.

**(9) [v3.2] J0-L 두 단계와 2주 체크리스트**

| 단계 | 착수 조건 | 할 일 | 산출물 | 소요(추정 △) |
|---|---|---|---|---|
| **J0-L-a**(라이선스 없이) | 없음(파일 작성) · 기동 확인만 설치본·Docker 자원 필요 | ① Dockerfile(JDK 17/21 temurin · **arm64 네이티브 먼저**, 실패 시 `platform: linux/amd64`) ② compose(고정 서브넷 · 127.0.0.1 게시 · 5000 비게시 · 설치본·라이선스 외부 마운트 · ~~`jennifer_bootstrap_check=false`~~ **[v3.3] Bootstrap 기본 유지** · 공통 `TZ`) ③ 샘플 앱·재현 스크립트 ④ 녹화 하네스(§5.2(e) 경로 전건 · 마스킹 · 출처 표지) ⑤ 목 Open API 서버 ⑥ 기동 확인 — 최고관리자 생성 · `GET /api/domain` · 에이전트 접속 거부 로그 확인(~~*"Agent connection rejected. reason=no license found"*~~ **[v3.3] 에이전트 로그** *"Cannot create data server session. Rejected by data server. reason=no license found, serverVersion=5.7.0.1"* — 데이터 서버 INFO 로그에는 없다) · arm64 기동 결과 기록 | `apm_gateway/testdata/jennifer/` 전체 · README · arm64 실측 기록 · **[v3.3] 진행: ①~⑥ 완료 — ④는 라이선스 없는 녹화본(21건 · J0-L-b에서 재녹화) · ③의 부하 재현 스크립트는 J0-L-b(이벤트 재현과 함께) — §0.10 (4)** | 3~5 영업일 |
| **J0-L-b**(평가판 적용 · **2주 안**) | ~~사용자 할 일 1~6 완료(평가판 키 수령)~~ **[v3.3]** 사용자 할 일 1(평가판 키)·2(일정)·**9(최신 Java 에이전트)** — 3~6은 J0-L-a에서 끝났다 | 아래 체크리스트 | recorded JSON(`local-docker`) · 쓰기·제어 API 실측표 · 이벤트 재현 기록 · U-항목 결과 | 10 영업일 + 여유 |

J0-L-b 체크리스트(평가판 창 기준 · 일자는 영업일):
- D1 — 라이선스 등록 · Open API 토큰 발급([설정 > JENNIFER 서버 > 인증토큰 발급]) · 에이전트 접속 확인 · `GET /api/instance`의 `hostName`·`ipAddress` 확인(U-4 형식) · **[v3.3]** 로컬 토큰은 `bootstrap_local.py`로 이미 발급된다 · 최신 에이전트로 바꾸면 WAS 베이스를 JDK 17로 올린다 · `/api/status/*` 시 단위 규칙 확인(§0.10 #19 — J0-L-a에서 미검증)
- D1~D2 — 녹화 하네스로 §5.2(e) 허용 경로 **전건** recorded JSON 채집(U-2·U-12 · MB·ms 단위 대조)
- D2~D3 — 쓰기·제어 API 실측(로컬만 · U-5 — 토큰 권한·405·404) · 콘솔로 덤프·PLC 기능 확인(U-8) · **[v3.3]** 쓰기·관리 경로의 GET 라우팅은 J0-L-a에서 확인했다(§0.10 #15) — 여기서는 실제 메서드로 호출했을 때 거부되는지만 본다
- D3~D5 — 샘플 앱 + EVENT 룰 임계 하향으로 이벤트 재현(U-1·U-13 · `errorType`/`metricsName` 양쪽) · 폴러 커서·합성 키 검증용 이벤트 채집
- D5~D7 — X-View 전수 여부(건수 대 TPS · U-14) · p95·에러율 집계 검증 데이터 · 시각 형식(epoch ms) 확인
- D8~D10 — 계약 테스트 픽스처 확정(마스킹 · 출처 표지) · 누락분 재채집
- D11~D14 — 여유(재시도) · **만료 전** recorded JSON 전체 백업 · 이후는 목 서버로 전환
- 2주 안에 끝내지 못할 것 같으면 **장기 개발용 라이선스를 영업에 문의**한다(사용자 할 일 2).

**(10) [v3.2] 사용자 할 일(외부 전제)** — **J0-L-b(라이선스 적용) 착수 조건**이다. J0-L-a의 파일 작성은 이것을 기다리지 않는다. 단 4·5는 J0-L-a의 기동 확인부터 필요하다. **[v3.3] 3·4·5·6은 J0-L-a에서 끝났다 — 남은 J0-L-b 착수 조건은 1·2·9다.**

| # | 할 일 | 필요 시점 | 비고 |
|---|---|---|---|
| 1 | **평가판 라이선스 신청** — 폼에 **Agent IP·Server IP**를 적어야 하므로 (4)의 compose 고정 IP 설계를 먼저 확정한다. 벤더에 "Docker Desktop(Mac)에서 등록할 IP"(맥 LAN IP인지 컨테이너 IP인지)를 확인한다 | J0-L-b | 2주 · 기능 제한 없음 · IP 기반 · **회사 이메일 + 전화 확인** · **사이트당 1회** · 처리 8시간~영업일 3일(원천마다 다름) · **[v3.3]** IP 설계 초안 = 서버 `172.29.87.10` · WAS `172.29.87.20`(compose 고정) — 벤더 확인은 남았다 |
| 2 | **2주 일정 확보** 또는 **장기 개발용 라이선스 문의**(sales.ko@jennifersoft.com) | J0-L-b | 만료 뒤에는 에이전트 데이터를 받지 못한다 |
| 3 | ~~**설치본 입수 경로 결정** — 공식 다운로드 페이지(라이선스 키 입력 + 평가판 약관 동의) 권고 · 공개 S3 직접 링크를 써도 되는지는 약관·영업 확인~~ **[v3.3] 완료 — 사용자 확정 「공개 S3에서 받기 (Recommended)」(2026-09-29)** | J0-L-a 기동 확인 | 설치본(약 591MB)·라이선스 파일은 **저장소 밖**에 두고 커밋하지 않는다 · **[v3.3]** `jennifer-server-5.7.0.1.zip` 590,809,047바이트 · sha256 `e3a1dc9e984de04fe2e9ec9f48edd361b22f110dde99c0a2411c7824bb34828f` · 저장소 밖 보관 |
| 4 | ~~**Docker Desktop 자원** — VM 메모리 8GB 이상 · CPU 2코어 이상 할당~~ **[v3.3] 완료 — 자원 충분**(Docker Desktop 28.4.0 · CPU 10 · 11.7GiB) | J0-L-a 기동 확인 | ~~`jennifer_bootstrap_check=false`(로컬 한정)는 계획에서 채택했다~~ **[v3.3]** Bootstrap Check를 켠 채 기동했다 — 끄기 승인이 필요 없다 |
| 5 | ~~**arm64 실측 결과 수용** — 네이티브 기동이 실패하면 amd64 에뮬레이션(느림)으로 갈지 결정~~ **[v3.3] 완료 — 서버 arm64 네이티브 기동 성공**(amd64 불필요) | J0-L-a 기동 확인 | R-24(서버 해소) |
| 6 | **로컬 뷰 서버 초기 설정** — ~~(브라우저 수작업) 최고관리자 생성 · 라이선스 등록 · Open API 토큰 발급~~ **[v3.3] 관리자 생성·토큰 발급은 `scripts/bootstrap_local.py`로 자동화(완료)** · 남은 수작업 = 라이선스 등록(파일을 `JENNIFER_DIST_DIR/license`로 두면 entrypoint가 복사 — 동작은 수령 뒤 확인) | J0-L-b D1 | 토큰은 ~~로컬용 「예정」 `apm_gateway/.env`에만~~ **[v3.3]** 저장소 밖 파일(스크립트 표준출력을 받는다 — README) · J1 이후 게이트웨이 로컬 연결 때는 `apm_gateway/.env`(gitignore) |
| 7 | (완료 — 계획 채택) 데이터 발생 앱 — 자체 샘플 앱 + EVENT 룰 임계 조정을 계획 범위에 넣었다 | — | (2) |
| 8 | (선택) **벤더 데모 서버** — 쓰려면 벤더에 계정·토큰을 요청하고 **외부 호출 건별 승인**을 받는다 | 선택 | (11) · 기본 미사용 |
| 9 | **(v3.3 신규) 최신 Java 에이전트(5.6.x) 입수** — 공식 다운로드(라이선스 키 입력). 뷰 서버가 배포하는 5.5.2.5는 JDK 17에서 WAS 기동을 실패시킨다(§0.10 #5) | J0-L-b | 못 구하면 5.5.2.5 + JDK 11로 채집하고 recorded JSON 출처 표지에 에이전트 버전을 적는다(5.6.x에서 생긴 수집 항목이 빠질 수 있다 △) · R-28 |

**(11) [v3.2] 벤더 데모 서버** — 스펙 `servers`에 `https://java.jennifersoft.com`(Demo)·`https://dev.jennifersoft.com`(Dev)가 있고, 인증 없이 루트는 `/login`으로,
`/api-v2/auth-test`는 401로 응답한다(Open API는 열려 있고 토큰 필요 · 2026-09-29 실측). **공개 계정·토큰은 없다(「확인 불가」 — 벤더 요청 필요).** 외부 서비스 호출이라
**기본 사용하지 않고**, 쓰려면 건별 사용자 승인을 받는다(선택지로만 둔다). 제니퍼 AI 공개 프록시(`insight.jennifersoft.com`)는 여전히 금지다.

### 0.9 v3.2 — Open API 공식 스펙 대조 반영 (2026-09-29)

> **원천**: 메인 세션의 대조 결과(52건 — 일치 23 · 정정 필요 24 · 스펙에 없음 3 · 확인 불가 2) · 정본 = gh-pages `index.html`에 인라인된 OpenAPI 3.0.3 스펙
> (5.6.4 · **39경로 · 63오퍼레이션**)을 전수 파싱한 것 · 보조 = v2 매뉴얼 · 릴리즈 노트 5.6.0~5.7.0.1 · 설치 가이드 3·10장([J-23]). `openapi.jennifersoft.com`
> 직접 열람은 이 환경에서 타임아웃이라 같은 원천(리포 `CNAME` = 그 도메인 · gh-pages 커밋 1개 `0152c7b4` · 2026-03-25)을 읽었다. **실 제니퍼 서버는
> 호출하지 않았다** — 아래는 선언된 스펙 기준이고 실 응답 차이는 J0-L·J0-O에서 확인한다. 「확인 불가」는 사실로 쓰지 않고 U-항목으로 옮겼다.

**(1) 설계 판단 5건**

| # | 대조 결과 | 결론(v3.2) | 근거 |
|---|---|---|---|
| ① | `/api-v2/manage-rule-event`는 **경로가 아니라 v2 매뉴얼의 파일명**이다. 실제 경로 `GET /api-v2/manage/rule/event/{error\|metric\|compare}/<domainId>[/<대상타입>]`. 룰 쓰기 API는 **적용 on/off**(`…/error/<d>/<ERROR유형>/applied` PUT)와 **대상별 설정**(`…/individual-setting/<id>` PUT·DELETE)뿐이고 **임계 변경 API는 공개 자료에 없다**(#36·#37). 덤프·PLC·인터럽트 API는 공개 원천 0건(#43 확인 불가) | §2.3·§2.5·U-1·U-13의 경로·"임계 변경 API ✔"를 정정한다. U-1 채집은 `…/error/<domainId>`(GET)를 1차로, `compare`/`comparing` 표기 차이는 J0-L 실측. **G-6 확정 내용은 그대로 둔다.** 카탈로그의 **덤프 채취·PLC 상한 하향·원복은 실행 채널을 J0에서 확인하고, 제니퍼 API가 없으면 허용목록 스크립트**(재기동과 같은 별도 실행 경계 — JDK 표준 `jcmd` 덤프[W-8] · 에이전트 설정 반영)로 한다(§5.7) | [J-23] S3 `spec/manage-rule-event*.md` · S4 |
| ② | `/api/auth/userlist`(이메일·휴대폰)·`/restapi/users`·`/api-v2/environment-variable/<d>`(환경변수)·`/api-v2/active-service/detail/…`(SQL·HTTP query)는 **민감한 GET**이다. 문서 밖 쓰기·제어 경로도 11건 이상 더 있다(#40·#41) | **"GET만"은 필요조건일 뿐이다.** 1차 통제를 **메서드 + 경로 템플릿 정확 일치 허용목록 · 그 밖은 전부 거부**로 못 박는다(§5.2(e) 정본 표 · §8.1). 와일드카드(`/api/transaction/*`) 금지 · `.xml` 변형 제외 · 쿼리 `token=` 거부 · 거부 사례 목록을 J1 테스트 입력으로 둔다 | [J-23] §4.4 (a)(b) |
| ③ | `/api/dbsearch/event`는 `domain_id`·`start_time`·`end_time`(epoch ms)이 **필수**다(`start` 아님). 응답 `EventData`에 **`eventId`가 없다**. 지표 기반 이벤트는 `errorType`이 비고 `metricsName`이 찬다(#31·#32·#34) | 폴러 호출 = **도메인별** `?domain_id=&start_time=&end_time=&level=` · 커서 = 마지막 `time`(경계 포함 재조회) + **합성 멱등 키**(`domainId`·`instanceId`·`errorType\|metricsName`·`time`·`txid` 해시) · 정규화 `alarmName = errorType`(비면 `metricsName`) · `eventLevel` 매핑은 대소문자 무시 · R-16 kind 선판정 입력도 같은 규칙(§5.5) | [J-23] #31·#32·#34 |
| ④ | `/api/transaction/profile.txt`·`/sql`·`/txid`는 `domain_id`·`txid`·**`time`**이 필수다(#28·#29) | `apm_transaction_profile` 인자 = `hostname` + `domain_id` + `txid` + `time_ms`. 세 값은 앞선 도구(`apm_slow_transactions` — X-View `TransactionData`의 `txid`·`endTime` / `apm_events` — `EventData`의 `domainId`·`txid`·`time`)가 돌려주는 **`profile_ref{domain_id, txid, time_ms}`**를 그대로 옮긴다(LLM이 만들지 않는다). 게이트웨이는 `domain_id`가 `hostname`의 정합 결과 도메인에 속하는지 검사하고 아니면 `{error: "profile_ref_mismatch"}`(§5.2(c)) | [J-23] #28·#29 |
| ⑤ | `/api/status/*`의 `start_time`·`end_time`은 **시 단위만** 받는다(*"Units below hour must be set to zero"*) — 시간 내 집계라 서버에서 분 단위 사건창으로 다시 거를 수도 없다(#24) | `apm_slow_transactions`의 **1차 뒷단 = `/api/transaction/time`(X-View 기본 데이터 · 1분 창)** — 사건창을 1분 단위로 나눠 호출(상한 10분)하고 게이트웨이가 응답시간 상위 N·시간 분해(`cpuTime`·`sqlTime`·`fetchTime`·`externalcallTime`·`networkTime`)·`errorType`을 집계한다. 분 단위로 정확하고 `profile_ref`를 얻는다. **2차 = `/api/status/application`** — 사건창을 시 경계로 내림·올림한 **시 단위 맥락**(평균·최대·표준편차·`failures/calls`)으로만 쓰고 `[한계]`에 "시 단위 통계"를 적는다. 사건창이 10분을 넘으면 1차는 상한 10분(사건 직전 구간)만 보고 나머지는 2차로 채운다 | [J-23] #24·#25·#27 · 부록 A |

**(2) 정정·보강 반영표**(대조 항목 # → 결론 → 이 계획의 위치)

| # | 결론 | 위치 |
|---|---|---|
| 2 | 스펙 규모 "52경로" → **39경로 · 63오퍼레이션**(api-v1 27/49 · api-v2 9/9 · rest-api 3/5) | §0.4 #2·O-3 · §0.5 M-3 · §12.3 [J-4] |
| 4 | 응답 content 선언은 `*/*` 47 · JSON 6 · `text/plain` 2 · `.xml` 변형 경로 5개. `opentelemetry` 4건은 데이터 서버 OTel **수신** 설정 필드 — OpenMetrics·OTLP 내보내기 0건(O-3 결론 유지) | §0.4 O-3 |
| 6 | 쿼리 `token=` 인증이 보조 자료에 있다 → **쓰지 않는다**(URL·프록시·접근 로그에 남음) · 허용목록 검사가 쿼리 `token` 키를 거부 | §5.2(e) · §8.1 |
| 7 | 구 메뉴명 = [관리 > 인증 토큰 관리] | U-5 |
| 8 | (보강) 5.6.2.18 미만 = 토큰 호출마다 Jetty 세션 누적 · 5.6.2.10/13 미만 = 무제한 토큰 401 | §8.4 · 가이드 §11 |
| 9 | 비활성 옵션 이름 **공개** = 뷰 서버 실행 스크립트 JVM 속성 `jennifer.unofficial.disable.open.api=true`(5.6.2.4+) | §1.3-2 · U-5 |
| 10 | 404 = 데이터 없음도 포함 · **405 = 메서드 오류** · 비활성 시 응답 코드 미공개(U-5) | 가이드 §3.6 |
| 11·12 | (보강) `/api/domain`은 파라미터 없음 · `/api/instance`는 `domain_id` 필수이고 응답에 **`hostName`·`ipAddress`** | §5.3 정합 순서 |
| 13 | `Instance`에 `port`·`kind` 없음 → `apm_instance_map` 반환에서 뺀다(override에서만) · 전수 = `/api/domain` → 도메인별 `/api/instance` | §5.2(c) |
| 15 | `responseTime` = ms(스펙 명시) — △ → ✔ | §5.9 |
| 16 | `heapUsed`·`heapCommitted`·`nonHeapUsed`·`procMemory` = **MB** → J7 `_bytes`는 ×1,048,576 변환(2²⁰ 추정 — 10⁶/2²⁰ 여부 U-12) | §5.9 |
| 17 | `activeDBConnection`(5.6.3.9+) · `averageDbPoolIdleCount` · `averageDbPoolConfiguredCount` — △ → ✔ | §5.2(c) · §5.4(b) |
| 18 | **(스펙에 없음)** WAS 스레드 풀 상한 필드 없음 — `threadCurrent`(JVM 전체)·`activeService`로 근사하고 `[한계]`에 적는다 | §5.2(c) · §5.4(b) |
| 19 | GC = `gcTimeUsage`(**%**) — 지속시간 아님 · `was_gc_stall`은 `gcTimeUsage ≥ x`로 판정 | §5.2(c) · §5.4(b) |
| 20 | **(스펙에 없음)** 백분위(p50/p95) 필드 없음 — 평균 `responseTime`·최대·표준편차만. p95·에러율은 **X-View 1분 분할 집계**(사건창 ≤ 10분)로 게이트웨이가 계산, 넘으면 시 단위 `failures/calls` | §5.2(c) · 판단 ⑤ |
| 21 | (보강) `/api/dbmetrics/*`는 `metrics` 필수 · 응답 = **지표 1개 시계열**(`time`·`value`) → 지표 수만큼 호출 | §5.2(c) · §8.4 |
| 22 | **(확인 불가)** `interval_minute` 허용값·1회 조회 창 상한 | U-2(J0-L) |
| 23 | `max_row` 기본 1000은 **구 사본(S2)** 값 — 정본 5.6.4는 기본값 미선언(미지정 호출 결과 U-12) | §2.2 · §2.4 B |
| 24·25·27 | 판단 ⑤ · `error_type`은 `TransactionData`에만 · `networkTime`은 합계 | §5.2(c) |
| 26 | (보강) 액티브 서비스에 `txid`·`statusElapseTime`(ms)·`startTime` 등도 있다 · `elapseTime` 단위 미기재(U-12) | §5.2(c) |
| 28·29 | 판단 ④ | §5.2(c) |
| 30 | `level`은 event 전용 · `error_type`은 error 전용(대문자) | §2.2 |
| 31·32·34 | 판단 ③ | §5.5 |
| 33 | Open API `EventData`에는 `otype`·`detailMessage`·`serviceName`이 **없고** `applicationName`·`instanceOid`가 있다(어댑터 모델과 다름) | §2.3 |
| 36·37 | 판단 ① — **(스펙에 없음 #37)** 임계 변경 API | §2.3 · §2.5 · U-1 · U-13 |
| 39 | (보강) 도메인 GC 메서드 미공개 — 허용목록 밖이라 영향 없음 | §5.2(e) |
| 40·41·42 | 판단 ② | §5.2(e) · §8.1 · R-4 |
| 43 | **(확인 불가)** 덤프·PLC·인터럽트 API · `active-service/detail`은 ✔지만 SQL·HTTP query 노출이라 허용목록 밖 | U-8 · §5.7 |
| 44 | `/api-v2/deploy/<d>?startTime=&endTime=`(ms · **25시간 이하** · 5.6.0.5+ · 정본 미수록) — △ → ✔ · `was_error_burst` 근거로 허용목록에 **정확 경로 추가** | §4.6 · §5.2(e) · §5.4(c) |
| 45 | (보강) `/api-v2/manage/instance?processId=&hostname=`(5.6.0.21+) — `processId` 필수라 역조회 불가 · `/api-v2/manage/*`라 **J0 수동 대조용만**(허용목록 밖) | §5.3 |
| 46 | 최신 **5.7.0.1 핫픽스(2026-09-04)** · 5.6.5 계열 5.6.5.12(2026-09-28) · 5.7.0.1은 비동기 트랜잭션을 애플리케이션 통계에 포함(전후 수치 비교 주의) | §1.3-1 · §2.1 |
| 47·48 | v1 확장 이력 △ → ✔ · 구 사본 기본값(`max_row` 1000 · `level` 기본 · profile `-1`)은 정본에서 빠졌다 | §2.4 B |
| 51 | 뷰 서버 `server_port` 기본 **7900** | 가이드 §3.6 |
| 52 | 5.7.0.1+ **SNI 호스트 검증 기본 on**(`ssl_sni_host_check=true`) — `JENNIFER_API_URL`은 IP가 아니라 인증서 호스트명으로 | §1.3-4 · 가이드 §3.5·§11 |
| §4.3 | 토큰 우회 옵션 `ignore_auth_token`(5.6.0.19 버그 수정 기록)이 존재 · API 호출에 **도메인 단위 권한 검사**(5.6.1) · 권한이 무엇에 묶이는지 미공개 | U-5(J0 점검) |
| §4.5 | 요청 시각 = **epoch ms**, 응답 시각 = string(epoch ms) · 게이트웨이는 `time_pattern`을 쓰지 않는다 · **시간대·시계 기준은 확인 불가** · 단위 미기재 필드 다수 | U-12 · **U-14 신설** |

**(3) 이 대조가 바꾸지 않는 것** — G-1·G-2·G-8과 D-274의 구조, `apm_*` 8종 이름, D-195 ①②의 방향. 허용목록은 **더 엄격해진다**(D-195 ① 정정 부기).
D-003 부기·G-6 기록 방식은 **사용자 결정 대기 중이라 손대지 않았다.** **[v3.3] 2026-09-29 확정(사용자 "권고") — L2는 "D-003 예외"가 아니라 "D-003 범위 밖"으로 다시 적었다(§5.7 · §10 G-6 · §11 · `docs/02` D-003 부기 · D-195 ③).** 로컬 Docker 「조사 대기」 칸은 같은 날 Docker 조사 결과([J-24])로 채웠다(§0.8).

### 0.10 v3.3 — J0-L-a 로컬 Docker 실측 (라이선스 없음 · 2026-09-29)

> **원천**: 메인 세션 실측(2026-09-29). 설치본 `jennifer-server-5.7.0.1.zip`(공개 S3 — 사용자 확정 · 590,809,047바이트 · sha256 `e3a1dc9e…34828f` · 저장소 밖 보관) ·
> 환경 `apm_gateway/testdata/jennifer/`(Dockerfile 2 · compose · `.env.example` · 샘플 JSP · 로컬 전용 스크립트 2 · README — 메인 세션 소유) · Open API 프로브 40건
> (`recorded/raw/20260929-123655/summary.json` — 원본은 커밋 제외). 호스트 = Docker Desktop 28.4.0 · CPU 10 · 메모리 11.7GiB(Apple Silicon).
> **라이선스가 없어 에이전트 데이터는 한 건도 들어오지 않았다.** 아래는 데이터 없이 알 수 있는 것뿐이고, 실데이터 형태는 J0-L-b(§0.8 (9))로 남긴다(#19).
> 범례: ✔ 계획 전제 확인 · ✏ 계획 전제 정정 · ➕ 새로 안 사실 — 합계 20항목(✔ 8 · ✏ 3 · ➕ 8 · 미해소 1 — #20은 v3.3 부기).

**(1) 실측 결과**

| # | 판정 | 실측 | 계획 반영 |
|---|---|---|---|
| 1 | ✔ | **arm64 네이티브 기동 성공** — 서버 컨테이너 `eclipse-temurin:21-jdk`(aarch64) · amd64 에뮬레이션 불필요 | R-24 서버분 해소 · §0.8 (10) 5 완료 |
| 2 | ✏ | **Bootstrap Check를 끌 필요가 없다** — 5.7.0.1 데이터 서버는 `WARN BootstrapChecks - virtual environment is not recommended (Docker Container)`를 남기고 `Data server startup completed`까지 간다 | §0.8 (4) Bootstrap 행 정정(기본 유지 · 자원 부족으로 막힐 때만 `DISABLE_BOOTSTRAP_CHECK=1` 옵트인 · 로컬 한정) · R-26 철회 |
| 3 | ➕ | **5.7.0.1 최초 관리자 흐름** — 첫 기동 때 `server.view/db_view/security/admin-bootstrap.token` 파일이 생긴다 → `/login/setup?token=` 화면 또는 `POST /login/user/setup`(폼 `id`·`password`·`name`·`token`)으로 관리자 생성(성공 응답 `M0478` · 토큰 파일 소멸) → `POST /login/page`(폼 `id`·`password` → 302) → `POST /auth/token/create`(폼 `tokenType` = **정수 0**(OPEN_API — 문자열을 주면 400 `MethodArgumentTypeMismatch`) · `validTime`(ms) · `tokenCount` · `memo`) · 목록 `GET /auth/token/list` · 발급 토큰은 11자 | 로컬 전용 `scripts/bootstrap_local.py`가 자동화 — §0.8 (10) 6 · 가이드 §3.8. **운영 토큰 발급은 콘솔 수작업 그대로**다(이 스크립트는 운영에 쓰지 않는다) |
| 4 | ✔ | 포트 7900(뷰 서버·Open API)·5000(데이터 서버·에이전트) 확인 · 에이전트 배포 `/download/agent/java/<아무 값>`은 **인증 없이** 응답한다 | §0.8 (2)(4) 그대로 |
| 5 | ✏ | **뷰 서버가 배포하는 Java 에이전트는 5.5.2.5**(MANIFEST 5.5.2 · 빌드 2021-04-20)다 — `latest`·`5.6.5.12`·`5.7.0.1` 어느 경로든 같은 6,034,551바이트 파일. 이 에이전트는 **JDK 17에서 JVM 기동을 실패시킨다**(`IllegalArgumentException: Unknown Java version string: 17.0.20.1` → `FATAL ERROR in native method: processing of -javaagent failed` → JVM abort · 17.0.12도 같음). JDK 11(11.0.32.1)·JDK 8(1.8.0_504)은 정상 기동 → 샘플 WAS = `tomcat:9.0-jdk11-temurin`. 최신 5.6.x 에이전트는 **라이선스 키로 공식 다운로드**에서 받는다 | §0.8 (2) 정정 · **R-28 신설** · §0.8 (10) 할 일 9 신설(J0-L-b 착수 조건) · §5.4(b)에는 넣지 않는다(판단은 §5.4(b) [v3.3]) |
| 6 | ➕ | **에이전트가 `JENNIFER_*` 환경변수를 설정으로 읽는다** — 로그 `Agent settings set in system environment: server_address / domain_id / view_url / inst_name`. WAS 컨테이너에 `JENNIFER_VIEW_URL`·`JENNIFER_SERVER_ADDRESS`·`JENNIFER_DOMAIN_ID`·`JENNIFER_INST_NAME`을 두자 `jennifer.conf`의 `server_address` 대신 127.0.0.1:5000으로 접속했다(tcpdump SYN→RST). 이름을 `AGENT_*`로 바꾸자 172.29.87.10:5000으로 접속. 넷 중 어느 변수가 원인인지는 **미확정**(로그에 값이 모두 null로 찍혔다) | **R-29 신설** · §0.8 (4) 통제 행 · 가이드 §11. 규칙 = 에이전트를 붙인 JVM 환경(WAS 컨테이너 · 셸 프로필)에 `JENNIFER_*`를 두지 않는다. 게이트웨이 키 `JENNIFER_API_URL`·`JENNIFER_API_TOKEN`(「예정 이름」)은 `apm_gateway/.env` 파일로만 읽고 WAS 호스트에 export하지 않는다 |
| 7 | ✏ | **라이선스 거부는 에이전트 로그에 남는다** — `Cannot create data server session. Rejected by data server. reason=no license found, serverVersion=5.7.0.1`. 데이터 서버 INFO 로그에는 거부 줄이 없다 | §0.8 (9) ⑥ 문구 정정(v3.2의 *"Agent connection rejected…"*는 데이터 서버 로그로 가정한 문구였다) · 가이드 §11 |
| 8 | ➕ | 비정상 종료 뒤 `db_view/db.lock`·`db_data/db.lock`이 남아 재기동이 `FileAlreadyExistsException … Other server is running`으로 거부된다 → 서버 entrypoint가 기동 때 지운다(그 볼륨은 그 컨테이너만 쓴다) | §0.8 (4) 통제 행 · 가이드 §11 |
| 9 | ✔ | 인증 — 토큰 없음 401(`"401 unauthorized"`) · Bearer 200(`"OK"`) · **쿼리 `?token=`도 200으로 통과**한다 · Open API는 기본 활성 | §5.2(e) "쿼리 `token` 키 항상 거부"가 **서버가 막아 주지 않는 것**임이 확인됐다 · U-5 일부 |
| 10 | ➕ | **토큰 `usageCount`는 요청 1건마다 1 증가**한다(41호출 → 41) — **500 응답도 센다** | U-5 부분 해소(사용량 단위 = 요청 수) · §8.4 · §5.5 — 재시도·미접속 도메인 폴링도 한도를 쓴다 · **[v4.1]** 반영은 약 5초 지연 · 타임아웃 요청도 센다 · 401·연결 거부는 세지 않는다(§0.12 F-1) |
| 11 | ➕ | **오류 모델** — 인스턴스 단위 API는 미접속을 **HTTP 500 + `{"exception":{"message":"1000 Domain is not connected"}}`**로 돌려준다(`.xml` 변형은 `<exception><message>…</message></exception>`). **필수 파라미터 누락도 400이 아니라 500**이다(`Required request parameter 'domain_id' for method parameter type short is not present`). `/api-v2/deploy/{d}`는 500 + 문자열 본문 `"500 500 com.aries.view.core.nio.DataServerDownException: 1000 Domain is not connected"` | **HTTP 코드로 오류를 가를 수 없다** → 게이트웨이는 `exception.message`(v2는 문자열 본문)로 분류한다: "Domain is not connected" → 소스 미가용(사유 노출) · "Required request parameter" → **계약 위반(우리 쪽 버그)** — §5.2(c) 반환 계약 · §5.5 폴러 · §8 |
| 12 | ✔ | 필수 파라미터 — `/api/dbsearch/event`는 인자 없이 부르면 `domain_id` 누락, `start`·`end`를 주면 `start_time` 누락으로 500 · `/api/transaction/profile.txt`에 `txid`만 주면 `Cannot parse null string` | §0.9 판단 ③·④ 확인 |
| 13 | ➕ | `/api/transaction/profile.txt`를 **`Accept: application/json`으로 부르면 404**(HTML 페이지)다 — `text/plain`(또는 `*/*`)으로 불러야 한다. 그 밖의 경로는 `application/json`으로 불러 JSON을 받았다 | §5.2(c) `apm_transaction_profile` · §5.2(e) — 클라이언트가 경로별 `Accept`를 고정 |
| 14 | ✔ | **민감 GET이 Open API 토큰에 열려 있다** — `/api/auth/userlist` 200(→ `email`·`id`·`name`·`phoneNumber`) · `.xml` 변형 200 · `/restapi/users` 200(→ `allowIp`·`creationTime`·`group`·`id`·`lastLoginTime`·`name`·`password` — `password`는 빈 문자열) · `/api-v2/manage/data-server/system-property-config` 200(빈 객체) · `environment-variable`·`rule/event/error`는 500(도메인 미접속) | §0.9 판단 ② · §5.2(e) (b) 거부 입력이 실측으로 뒷받침됐다 |
| 15 | ✔ | **쓰기·관리 경로가 Open API 토큰으로 라우팅된다**(GET으로만 확인 · 쓰기는 실행하지 않았다) — `data-server/control`·`db/property/1000/copy` → 400 `content is not a json` · `rdb-export-password-override` → 405 `method=GET` · `domain-group`·`manual-rdb-export` → GET 200(`[]`) · `domain/put` → 500 NPE | **토큰 단위 읽기 전용 제한은 없다**(U-5 — "거부되지 않을 개연성"까지만 · 쓰기를 실행하지 않았으므로 "라우팅 확인"으로 적는다) · 허용목록 1차 통제(§8.1)의 필요 확인 |
| 16 | ✔ | v1 조회 API의 변형 — `POST /api/domain` 200 · `GET /api/domain.xml` 200 | §5.2(e) "POST 변형·`.xml` 변형 거부" 규칙 확인 |
| 17 | ➕ | **`/api/metrics` 지표 카탈로그**(라이선스 없이 200) — `result`에 `domain`·`instance`·`business` 등 6키 · 식별자는 **snake_case**(`heap_used`·`heap_committed`·`heap_usage`·`gc_time_usage`·`gc_time`·`sys_cpu`·`proc_cpu`·`thread_current`·`service_rate`·`service_time`·`service_err_count`·`active_service`·`bad_response_active_service`·`average_db_pool_active_count`·`max_db_pool_active_count`·`max_tps`·`error_count`·`event_*_count` 등) · **`tps` 식별자는 없다** | `/api/dbmetrics/*`의 `metrics` 인자는 이 snake_case 이름이고 `/api/realtime/instance`의 camelCase 필드(`heapUsed` 등)와 다르다 → §5.2(c) **식별자 매핑 표**(J2 산출물) · U-2·U-12 부분 해소 |
| 18 | ➕ | 라이선스 없이도 200인 것 — `/api/domain`(빈 `result`) · `/api/realtime/instance`(빈 `result`) · `/api/transaction/time`(빈 `result` · 1분·10분 창 모두). 그 밖의 인스턴스 데이터 API는 500 | 빈 `result`를 "정상 · 0건"으로 단정하지 않는다 — 가용 판정(§5.4-a)·반환 계약(§5.2(c))은 도메인 연결 상태와 함께 본다 |
| 19 | — | **미해소(라이선스 필요 → J0-L-b)**: 실 인스턴스·이벤트·트랜잭션 응답 형태 · `interval_minute` 허용값 · X-View 전수 여부(U-14) · `hostName` 형식(U-4) · 이벤트 재현(U-1·U-13) · 룰 경로 응답 · 덤프·PLC(U-8). **`/api/status/*`의 "시 단위만" 규칙도 미검증**이다 — 분 단위 호출도 "Domain is not connected"로 먼저 막혔다 | §0.8 (9) J0-L-b 체크리스트 |
| 20 | ✔ | **(v3.3 부기) 목 서버 동일성** — 로컬 실서버와 목 서버(`fixtures` 모드)에 같은 40건 프로브(`probe_openapi.py`)를 돌려 **상태 코드·응답 모양 불일치 0건**. 첫 비교에서는 7건이 달랐는데 모두 허용목록 밖 경로였고, 실측 모양으로 맞춘 뒤 0건이 됐다 | §0.8 (6) "목 서버는 허용목록·반환 계약·폴러 멱등 검증에 충분"의 근거 · 라이선스 없는 상태 기준이다(J0-L-b 재녹화 뒤 다시 대조) |

**(2) 정정한 전제(before → after)**

| 위치 | v3.2까지 | v3.3 |
|---|---|---|
| §0.8 (4) Bootstrap Check | Docker 감지 여부 「확인 불가」 → 로컬 한정 `jennifer_bootstrap_check=false` · 데이터 유실 경고 수용(R-26) | **끄지 않는다**(5.7.0.1은 Docker를 WARN만 남기고 기동) · 자원 부족으로 막힐 때만 `DISABLE_BOOTSTRAP_CHECK=1` 옵트인 · R-26 철회 |
| §0.8 (2) WAS·에이전트 | 베이스 `tomcat:9.0-jdk17-temurin` · 에이전트 = 로컬 뷰 서버 `/download/agent/java/latest` · 지원 JDK 8~26 | 베이스 **`tomcat:9.0-jdk11-temurin`** — 뷰 서버 배포본은 **5.5.2.5**(경로 무관 같은 파일)이고 JDK 17에서 JVM 기동 실패 · 최신 5.6.x는 라이선스 키로 공식 다운로드(할 일 9 · R-28) |
| §0.8 (9) ⑥ 라이선스 거부 확인 | 데이터 서버 로그 *"Agent connection rejected. reason=no license found"* | **에이전트 로그** *"Cannot create data server session. Rejected by data server. reason=no license found, serverVersion=5.7.0.1"*(데이터 서버 INFO 로그에는 없음) |
| §0.8 (3) · §7.0 compose `.env` 키 | `JENNIFER_SERVER_ZIP` · `JENNIFER_LICENSE_FILE` · `JENNIFER_VIEW_HOST_PORT` · `JENNIFER_WAS_HOST_PORT` · `JENNIFER_SUBNET` | **`JENNIFER_DIST_DIR`**(zip + `license` 한 디렉터리) · `JENNIFER_VERSION` · `JENNIFER_PLATFORM` · `JENNIFER_VIEW_HOST_PORT` · `SAMPLE_WAS_HOST_PORT` · `JENNIFER_DOMAIN_ID` · `JENNIFER_INST_NAME` · `TZ` · `DISABLE_BOOTSTRAP_CHECK` · 서브넷은 compose에 고정(키 아님) |
| §0.8 (10) 6 초기 설정 | 브라우저 수작업(관리자 · 라이선스 · 토큰) | 관리자·토큰은 `bootstrap_local.py` 자동화 · 라이선스 등록만 남음 |
| §0.8 (2) 샘플 앱 | `/slow?ms=` · `/heap?mb=` · `/error` · (선택) `/sql?ms=` | `/sample/slow.jsp?ms=` · `/sample/heap.jsp?mb=`(`reset=1`) · `/sample/error.jsp` · SQL 엔드포인트 없음 |
| §0.8 (5) · §2.6 U-2 · U-5 | J0-L에서 **해소** | J0-L-a **부분 해소** — 잔여는 J0-L-b(U-2 응답 형태·`interval_minute` / U-5 초과 응답·실제 쓰기 거부) |
| §5.2 오류 처리 | (명시 없음 — 미매칭만 `instance_unresolved`) | 제니퍼 오류는 **HTTP 500 + 본문**이라 코드가 아니라 `exception.message`로 분류(§5.2(c) [v3.3]) |

**(3) 바뀌지 않는 것** — G-1·G-2·G-8 · D-274 구조 · D-195 ①②③ · `apm_*` 8종 · §5.2(e) 허용목록 정본(거부 규칙 전부가 "서버가 막아 주지 않는 것"으로 실측됐다) ·
J0-L-a/b 분할 · 사용자 할 일 1(라이선스 IP — 벤더 확인)·2(일정). `apm_gateway/testdata/jennifer/`의 파일·코드는 메인 세션 소유라 이 개정에서 고치지 않았다.

**(4) J0-L-a 진행 상태**(§0.8 (9) ①~⑥) — ① Dockerfile ✔(서버 temurin 21 · WAS tomcat jdk11) ② compose ✔(127.0.0.1:17900·18080 · 5000 비게시 · `172.29.87.0/24` 고정 IP ·
`JENNIFER_DIST_DIR` 읽기 전용 마운트 · 공통 `TZ`) ③ 샘플 앱 ✔(SQL 엔드포인트 제외) · 재현 스크립트는 J0-L-b ④ 녹화 하네스 ✔(**v3.3 부기** — `record_openapi.py` + `masking.py` · 녹화본 `recorded/local-docker/` 21건 + `index.json` ·
라이선스 없는 상태라 J0-L-b에서 재녹화 · 1차 프로브 `probe_openapi.py`는 유지) ⑤ 목 Open API 서버 ✔(**v3.3 부기** — `mock_openapi.py` · 실서버와 40건 불일치 0 · 도구 테스트 22건) ⑥ 기동 확인 ✔(관리자 생성 · `GET /api/domain` ·
에이전트 거부 로그 · arm64 기록).

### 0.11 v4 — J1~J4 구현 결과 (2026-09-29 · 사용자 지시 *"87번 계획을 구현하라."*)

> **정본 요약**. SDD 산출물 = `spec/CAPABILITY-MAP-87.md` · `spec/SPEC-apm-gateway.md`(소비자 계약 §3~§5) · `spec/SPEC-apm-sre-agent.md` · `spec/SPEC-apm-noise-gate.md` ·
> `tasks/plan-87.md` · `tasks/todo-87.md`. 운영자 절차 = `docs/31` v4. 결정 = D-195 구현 부기(신규 D-번호 없음). 작업 트리 반영 · **커밋하지 않았다**.

**(1) 착수 판정(§6 "선행 완료 + 게이트 해제인 Wave만")**

| Wave | 판정 | 근거(2026-09-29 실측) |
|---|---|---|
| J1·J2·J3·J4 | **구현** | J1 선행 = J0-L-a(§6 [v3.2]) · G-3·G-4·G-4b 확정 · 계약 픽스처는 녹화본 + 목 서버 + 스펙 합성(한계 (4)) |
| J5 | **보류** | 선행 `plans/121` TP-9.1·9.2·10.5 코드 0(`metric_query`·`config/task_routines.yaml` 부재 · 레지스트리 `backend: mcp` 실행 0 · 121 §14.2 "[v7 미착수]") |
| J6 | **보류** | G-6 착수 조건 "J3 완료 **+ 목업 검증 뒤**" — J3 목업은 로컬 Docker 재현(§6 v3.1 의존 그림)이라 J0-L-b 필요 · 덤프·PLC 실행 채널은 J0 확인(U-8) |
| J7 | **보류** | G-9 ①′ "J2 완료 + **소비자 확정**" — 운영 Prometheus URL 공란(소비자 미확정) |
| J0-L-b · J0-O | 사용자·외부 전제 | 평가판 라이선스·최신 에이전트 / 운영 접근 권한·토큰 |

**(2) 구현 위치** — 게이트웨이 `apm_gateway/`(자체 `pyproject.toml` · `.env.example` · 정책 `config/{instance_map,event_levels,was_signatures}.yaml` ·
`apm_gateway/{config,__main__}.py` · `domain/{errors,signals,events}.py` · `adapters/jennifer/{allowlist,client,fields,api}.py` · `application/{masking,resolver,tools,poller}.py` ·
`interface/{server,audit}.py` · 테스트 9파일) · `sre_agent/`(settings · `mcp_service._build_mcp_servers` · `investigation_guidance` · `severity_signatures` · `remediation` ·
`briefing_builder` · `investigation_dispatcher` · `diagnosis`(tool params) · 신규 `domain/investigation_limits.py` · 테스트 2파일 + `test_boundary`) · `noise_gate/`(`process_rank` ·
`enrichment_profile` · `investigation_payload` · `server_identity` · `alarm_worker` · `notification_gate` · `notification_policy` · `alarm_context_enricher` · `alarm_notifier` ·
`investigation_trigger` · 신규 `infrastructure/apm_gateway_client.py` · 테스트 1파일 + `test_plan60_flags_off_regression`) · `src/config.py` `NoiseGateConfig` 4필드 · 루트 `.env.example` ·
`scripts/overfit_check.py`(스캔 편입 · 어댑터 제외) · J0 사본 `testdata/jennifer/scripts/jennifer_catalog.py`(선택 키 3개 동기화) · 설정 카탈로그 가드 대응(`src/api/settings_catalog.py` 구획 4행 · `config/settings_help/noise_gate_investigation.yaml` 4항목 · `tests/test_api/test_settings_catalog.py` 총수 361→365) · 관제 근거 키 한글 라벨 `src/static/js/noise-help.js` · 관리자 매뉴얼 A-59·A-60 9.5절. **`mcp_server/`·`src/` 질의 경로는 바꾸지 않았다.**

**(3) 계획 대비 이탈 — 근거와 되돌리는 비용**

| # | 계획 | 실제 | 근거 | 되돌리는 비용 |
|---|---|---|---|---|
| E-1 | 정합 ②순위 `was_object` 브릿지 + `mcp_server` `polestar_was_instances`(§0.7 (7) "필요 시") | 미구현 — 규칙 이름은 경고 후 건너뜀 | U-10(운영 실재·채움률) 미확인 · v3.2 순서상 `hostName` 직접 대조가 ①이라 없이도 정합 성립 · 미확인 테이블에 SQL을 만들지 않는다 | `resolver`에 규칙 1종 + `mcp_server` 도구 1종 + MCP 클라이언트(J0-O 뒤) |
| E-2 | 원시 도구 `apm_raw_api`(`EXPOSE_RAW_APM_API`) | 미구현 | 수용 기준에 없음 · 공격 표면만 는다 | 같은 허용목록으로 도구 1종 |
| E-3 | 기동 시 버전 에코(R-12 `api_version_expect`) | 없음 | 허용목록 16템플릿에 버전 조회 경로가 없다(에이전트 버전은 `apm_instance_map.agent_version`) | 계획서에 경로 추가 → 허용목록·사본·테스트 동시 갱신 |
| E-4 | 폴러가 `level=<warning·fatal>` 쿼리 | `level`을 보내지 않고 받은 뒤 최소 레벨로 거름(해소 레벨은 통과) | 값 형식 미정의(U-1) — 잘못된 값이 조용히 0건을 만들 위험 | 폴러 한 줄 |
| E-5 | 허용목록 경로별 쿼리 키(§5.2(e) 표에는 필수만) | 선택 키 3개 추가 — `activeService/list`·`transaction/time`의 `instance_id` · `status/application`의 `instance_id`·`max_row` | 스펙 5.6.4 선언 파라미터 · 인스턴스로 좁혀 응답 크기·호출 수를 줄인다 · 방침(메서드 + 경로 정확 일치 · 경로별 허용 키 · `token` 거부)은 그대로 · J0 사본도 같이 바꿔 대조 테스트 유지 | 키 삭제 + 로컬 필터 |
| E-6 | `apm_*` 8종 | 8종 + `gateway_health`(헬스체크 — holmes `health_check_tool` · R-20) | 조사 서비스 등록 시 도구 헬스체크가 필요하다 · 제니퍼 호출 1회·30초 캐시 | 도구 1종 삭제(소비자 헬스체크 도구명 교체) |
| E-7 | 설정 = dataclass + `config.toml` + env(§5.2(b) `mcp_server` 전례) | `.env` + 정책 yaml(`config.toml` 없음) | 비밀 아닌 값이 적어 `.env` 한 곳이 단순 · 정책은 yaml(계획서 "정책은 파일에") | 로더에 toml 섹션 추가 |
| E-8 | `arch_check` 편입은 J1 판정(G-11) | **비편입** — 게이트웨이 AST 계층 테스트로 대체 · `overfit_check` 편입 | 판정 기준 ② 불성립(2단 중첩 모듈명 해석에 스캐너 변경 필요) | 스캐너에 패키지 루트 해석 추가 |
| E-9 | `CLAUDE.md` 「저장소 지도」·「패키지 경계」(J1 산출물) | **미반영** — 초안 패치를 팀 리드에게 전달 | 에이전트 메시지는 `CLAUDE.md` 변경 승인이 될 수 없다(운영 규칙) · `docs/18` 기록 | 패치 1개 적용 |
| E-10 | 판정 임계(§5.4(b)) | 잠정 절대 임계 — `was_error_burst`는 3σ 기준선 없이 `error_rate ≥ 0.2`(최소 20건) + `HIGH_RATE_FAIL` · 힙 누수 = 추세 3구간 하한 계단 | 기준선 이력 조회 경로가 없다 · "임계 잠정 · J0 보정"(§5.4(b)) | yaml 임계 조정 |

**(3′) 소비측 잔여** — `sre_agent` push 질문(`_job_to_question`)의 "핵심 지표(CPU·메모리·디스크·네트워크) 위주" 문구가 WAS 사건에도 그대로 나간다(후속) · 정체 가드는 `max_steps` 소진 조사를 판정하지 못한다(holmes 0.36.0이 예외로 끝나 도구 목록이 사라짐) · `sre_health`에 APM 도달성 미노출 · `noise_gate` 호스트 참고 표는 `ALARM_PROCESS_API_BASE_URLS_CSV`에 `jennifer=` 매핑이 없으면 제목만 붙는다 · 게이트웨이가 느리면 DASHBOARD·TICKET 알람 1건에 최대 약 7초 지연(워커 직렬 처리 · 시간 값은 코드 상수).

**(4) 한계(측정하지 못한 것)** — 녹화본이 라이선스 없는 상태라 **실데이터 응답 모양은 한 번도 게이트웨이를 통과하지 않았다**. 도구 경로 계약 테스트는 스펙 5.6.4 스키마로 만든
합성 응답(테스트 임시 디렉터리에만 · 출처 `spec-synthetic`)으로 돌았다 — 필드 타입(예: 시각 문자열)·`interval_minute` 허용값·`profile.txt` 형식·`/api/transaction/sql` 모양·
이벤트 레벨 값 형식·`errorType` 접두 표기는 J0-L-b 재녹화 뒤 교체·확인해야 한다. 실 LLM(로컬 MLX) APM 조사 완주는 돌리지 않았다(결정적 테스트로 수용). 로컬 Docker 제니퍼
통합 테스트(`RUN_DOCKER_IT=1`)는 추가했지만 로컬 토큰이 저장소 밖이라 실행하지 않았다. → **[v4.1] 실행 2 passed · 실서버 검증 58항목은 §0.12.**

**(5) 수용 기준 충족 요지** — J1: 경계 불변식·계층·벤더 격리·폴스타 DB 문자열 0·거부 입력 전건 HTTP 0회·3xx·크기 상한·토큰 0회·사본 대조·`/__mock/hits` 밖 0·`.env.example` 커버리지 ✔ ·
J2: 8종 계약·`instance_unresolved`·1분 분할 상한·10분 초과 `[한계]`·`profile_ref` 누락/불일치·마스킹 ✔ · J3: 두 번째 MCP 서버(미설정 비트 동일)·R-16 전 유형·목업 6종 승격·권고·
APM 라벨·폴백 사유·정체 가드(사후) ✔ · 실 LLM 완주 ✖(미실행) · 골든셋 비공개 관리 ✖(해당 없음) · J4: 폴러 멱등·같은 ms·`metricsName`·계약 필드 · `app_impact` 승격 전용·심각도 3 불변·
플래그 off 비트 동일·배지 「제니퍼」·힌트 ✔ · 공통 R-19: 감사 1줄에 `investigation_id`·`thread_id` ✔(LLM이 인자를 넘겼을 때). 테스트 수치는 §13 v4 행.

### 0.12 v4.1 — 로컬 Docker 제니퍼 실서버 검증 (2026-09-29 · 사용자 지시 *"도커에 설치되어 있는 제니퍼를 이용하여 검증할 수 있는 항목들은 모두 검증하라."*)

> **대상**: 로컬 Docker 제니퍼 5.7.0.1(데이터+뷰 서버 · 127.0.0.1:17900) + 에이전트 5.5.2.5를 붙인 샘플 WAS(127.0.0.1:18080) — **라이선스 없음**(에이전트 로그
> `reason=no license found` 16:17 재확인 · 도메인 0건). 토큰은 저장소 밖 파일에서 읽었다. 검증 스크립트는 세션 scratchpad에 두었고 저장소에는 넣지 않았다. 컨테이너는
> 재기동하지 않았다. 폴러용 Redis는 임시 컨테이너(`redis:7-alpine` · 127.0.0.1:16390)를 띄웠다가 지웠다 — 공유 Redis(6380)는 쓰지 않았다. 과금 API 호출 0 ·
> holmes LLM은 닫힌 루프백(127.0.0.1:9)을 가리켜 호출이 구조적으로 나갈 수 없게 했다. **결과: 58항목 전부 통과**(첫 실행 실패 4건 = 판정 스크립트 오류 2 · 측정 방법 오류 1 ·
> 입력값 오류 1 — 모두 원인 확정 뒤 재실행 통과 · 코드 결함 아님).

**(1) 검증 항목**

| 묶음 | 항목 | 결과 |
|---|---|---|
| 어댑터 · 인증 | Bearer 200(`result=[]`) · 잘못된 토큰·토큰 없음 → `apm_api_error`(HTTP 401 · "인증 실패") | ✔ 3 |
| 어댑터 · 허용 16경로 | 16템플릿 전부를 필수 키(+선택 키)로 호출 → **계약 위반 0건**(서버가 필수 키 누락을 한 번도 말하지 않음) · 결과 = 200(`domain`·`realtime/instance`·`metrics`·`transaction/time`) 또는 `source_unavailable`(나머지 12 · "Domain is not connected") · `profile.txt`를 `text/plain`으로 불러 404 아님 · v2 문자열 오류 본문(deploy) → `source_unavailable` | ✔ 4 |
| 어댑터 · 거부 = HTTP 0회 | §5.2(e) 비GET 9 · 민감 GET 9 · 변형 10 · 쿼리 `token`/`TOKEN` · 허용 밖 키 · 필수 키 누락 2 · 경로 변수 비숫자 2 → 전부 `NotAllowedError` · **서버 토큰 `usageCount` Δ=0**(반영 지연 대기 뒤 · 34건) · 허용 호출 8건(200 4 · 500 4) → `usageCount` Δ=8 = 클라이언트 호출 수 | ✔ 3 |
| 어댑터 · 방어 | 응답 크기 상한(200B) → `/api/metrics`(653B) 차단 · 초당 2회 상한 → 5회 2.01초 · 타임아웃 → `source_unavailable` · 연결 거부 → `source_unavailable` · **잘못된 기준 경로(`/x`) → 실서버 302(→`/login`) 비추종** → `apm_api_error` · `gateway_health` degraded · DEBUG 로그·오류 사유·결과에 토큰 0회 | ✔ 8 |
| 어댑터 · 오류 분류 | 실서버 오류 본문을 분류기에 그대로: 필수 키 누락(`Required request parameter 'start_time'`) · `Cannot parse null string` → `contract_violation` · 미접속 v1 JSON · v2 문자열 → `source_unavailable` · 미지 오류 → `apm_api_error` | ✔ 5 |
| 지표 식별자 | `METRIC_FIELDS`의 dbmetrics 식별자 13개가 실서버 `/api/metrics` `instance` 카탈로그(60개)에 **전부 있다** | ✔ 1 |
| 게이트웨이 실프로세스 | `python -m apm_gateway`(poller on · 임시 Redis) + MCP SSE 클라이언트: Bearer 없음·틀림 401 · `tools/list` = 9종 · `gateway_health` 도달 True(degraded · 도메인 0) · `apm_*` 8종 예외 없이 계약 JSON · 도메인 0건 → `source_unavailable`(빈 결과를 정상으로 보지 않음) · `profile_ref` 없이 → `invalid_argument` · `n=0` → `invalid_argument` · 폴러 발행 0·커서/멱등 키 0 · `poller` 상태 최상위 노출 · 기동 로그 1줄(허용 경로 16 · 비밀 없음) · 감사에 `investigation_id`·`thread_id` · 폴링 주기 5초 → 하한 10초 경고 · 토큰·Bearer 로그 0회(DEBUG) | ✔ 14 |
| Docker IT | `RUN_DOCKER_IT=1 … pytest tests/test_docker_it.py` | ✔ 2 passed |
| noise_gate 소비측 | 게이트 노드 + 실 `ApmGatewayClient` → 실게이트웨이: `source_unavailable`이면 DASHBOARD·TICKET 유지 + `stage_evidence.app_impact_error` · 추가 지연 **0.20초**(첫 호출) · 틀린 Bearer → `gateway_error` · 판정 유지 · 게이트웨이 다운 → `gateway_unreachable`(1회 0.001초 · 쿨다운 중 0초) · 게이트웨이 로그 비밀 0회 | ✔ 7 |
| sre_agent 소비측 | `_build_mcp_servers` = `apm`만(sse · `gateway_health` · Bearer) · **holmes 0.36.0이 실게이트웨이를 헬스체크하고 도구 9종 발견**(toolset `apm` ENABLED) · 틀린 Bearer → toolset FAILED · 도구 0 · 실게이트웨이 출력 3건 → `apm_payloads` 인식 3 · `was_signals` 승격 0(추측 승격 없음) · 브리핑 한계 = "APM 미가용(…source_unavailable…) — 폴스타 MCP 도구로 대체" | ✔ 6 |
| 녹화·목 서버 | 40건 프로브 실서버 ↔ 목 서버: **상태 코드 40/40 일치 · JSON 모양 38/38 일치** · 비JSON 2건(404 HTML 페이지 · `userlist.xml`)은 본문 내용만 다르다(목 서버의 간이 HTML · 개인정보 자리 가짜 값 — 의도) · 녹화 하네스 재녹화 21건 = 커밋본과 파일명 동일 · 시각 필드 외 내용 차이 0 · 토큰 0회 | ✔ 3 |
| 라이선스 없는 한계 재확인 | 샘플 WAS에 slow·error·heap 요청 뒤에도 `domain` 0 · `transaction/time` 빈 결과 · `dbsearch/event` 미접속 → 실데이터 모양·이벤트 재현은 여전히 J0-L-b | ✔ 2 |

**(2) 새로 안 사실 · 정정**

| # | 실측 | 반영 |
|---|---|---|
| F-1 | 토큰 `usageCount`는 **약 5초 늦게** 반영된다 — 호출 직후 읽으면 Δ=0으로 보인다(첫 측정 오판의 원인). 반영 뒤에는 요청 수와 정확히 같다: 인증 성공 요청은 200·500·**타임아웃 난 요청까지** 센다 · 인증 실패(401)·연결 거부는 세지 않는다(99 → 123 = 24건 · 이어서 6건 → 129) | §0.10 #10 부기. 사용량 기반 검증은 반영 대기(값이 두 번 연속 같을 때까지) 뒤에 읽는다 |
| F-2 | `profile.txt`의 선택 키 `key`는 **16진수 8자리 이상**을 기대한다(`"x"` → `Range [0, 8) out of bounds` · `"abcdefgh"` → `under radix 16`) — 게이트웨이는 `key`를 보내지 않는다 | 결함 아님. 허용목록 선택 키로 남아 있으므로 쓰게 되면 형식 검증을 더한다(후속 후보) |
| F-3 | **빈 인벤토리(도메인 0건)도 `APM_INSTANCE_CACHE_SECONDS`(기본 600초) 동안 캐시**된다 — 두 번째 `apm_events` 호출의 감사 `api_calls=0`. 라이선스 적용·에이전트 접속 뒤 최대 10분 동안 도구는 "도메인 0건"을 돌려주고, `gateway_health`(자체 30초 캐시)는 먼저 회복돼 **둘이 어긋난다** | 후속 후보(빈 인벤토리·미가용 도메인은 짧게 캐시) — 코드는 바꾸지 않았다 |
| F-4 | `noise_gate` 클라이언트의 Bearer 불일치(401) 사유가 `unhandled errors in a TaskGroup (1 sub-exception)`로만 남는다 — 인증 실패인지 알 수 없다 | 후속 후보(예외 그룹을 풀어 HTTP 상태를 사유에 싣기) |
| F-5 | 실서버 카탈로그에 `average_db_pool_active_count`(instance)·`max_tps`가 있다 — `METRIC_FIELDS`의 `db_pool_active`·`tps`는 dbmetrics 식별자를 `None`으로 두었다 | 후속 후보(`db_pool_active` 구간 추세 · TPS는 평균이 아니라 최대값이라 의미 확인 필요) — J0-L-b 실데이터로 판단 |
| F-6 | 샘플 WAS의 JSP 첫 호출은 컴파일로 느리다(`slow.jsp?ms=1500` → 7.7초) | J0-L-b 부하 재현 스크립트는 워밍업 호출 뒤 측정 |

**(3) 이 검증으로도 확인하지 못한 것** — 라이선스가 없어 §0.10 #19와 §0.11 (4)의 미확인 목록(실 인스턴스·이벤트·트랜잭션 응답 모양 · `interval_minute` 허용값 ·
`hostName` 형식 · 이벤트 재현 · 폴러의 실제 발행·멱등 · `was_signals` 실판정 · `app_impact` 실승격)은 그대로다. 실 LLM(MLX) 조사 완주도 돌리지 않았다. → J0-L-b.

---

## 1. 요구 해석

### 1.1 요구 → 기능 매핑

| 요구 | 현행(폴스타 단일 소스) | 확대 후 | 절 |
|---|---|---|---|
| **진단** — WAS 장애의 원인 지목 | OS 근사(`ps`·`ss`·`top`·`journalctl`, Plan 78 W7-1 · `middleware_profile()` = `vm_profile()` 동일) | **APM 1차**(트랜잭션·힙/GC·스레드·풀·큐잉·슬로우 SQL·외부 호출) · OS 근사 2차 폴백 | §5.2·§5.4 |
| **대응** — 영향 완화(mitigation) 제안·실행 | 권고만(`remediation.recommend`, OS 시그니처 11종) | WAS 시그니처 8종 추가 + **제니퍼가 제공하는 완화 수단**(PLC 부하 제어·스레드 인터럽트·EVENT 룰) 카탈로그화 · **L2 승인 후 실행**(게이트) | §5.4·§5.7 |
| **복구** — 정상 상태 복원·검증 | 없음 | 조치 후 **검증 루프**(`apm_app_health` 재조회로 회복 판정) · 실패 시 롤백/에스컬레이션 · 사후 브리핑 | §5.7 |
| **연동 방식** (v1: MCP DB → **v2: API·표준 규격**) | 폴스타 DB(PG·DB2) | ~~RDB Export 적재 PG를 `mcp_server` `[[sources]]`로 등록~~ → **v2 미채택**(§0.4). 제니퍼 데이터는 DB 소스 없이 `mcp_server` **API 소스**로만 편입 | §0.4·§5.2 |
| **API 연동** (v1: 검토 → **v2: 정본**) | Prometheus HTTP(`promql_tools.py`) · 폴스타 프로세스 API(`polestar_tools.py:39` httpx) | 제니퍼 Open API 클라이언트(httpx 재사용) — 서버측 자격증명·타임아웃·감사·**GET·경로 허용목록** | §5.2·§8.1 |
| **이벤트(알람)** | 폴스타 TCP JSON → `alarm:raw` → 노이즈 게이트 → 조사 트리거 | 제니퍼 이벤트를 **같은 스트림**으로(**API 폴링 정본** · 어댑터 push는 선택 — v2.1) + `app_impact` 축 | §5.5 |
| **질의(pull)** | text2sql 폴스타 3 DB | `apm` 솔루션 축(`backend: rest` — v2, DB 등록 없음) → "OO WAS 응답시간 추이" 같은 질의 | §5.6 |

> **[v2.2 정정]** "진단" 행의 현행 "OS 근사(ps·ss·top·journalctl)"는 **로컬 프로파일에만** 있다. 운영 원격 조사는 bash가 꺼져 있고(D-233 ·
> `toolset_profiles.py:240`), W7-1 자산(`middleware_profile`·`src/domain/middleware.identify`)은 프로덕션 호출부가 0건이다. 운영에서
> "OS 근사 2차 폴백"은 `mcp_server` 폴스타 도구(`polestar_process_snapshot`·`polestar_os_config`·`polestar_metric_trend`)를 뜻한다(§0.6 #25).
> "질의(pull)" 행의 `backend: rest`는 J5에서 `mcp`로 바꾸고, 실행은 `plans/121` 처리기 계약을 따른다(§0.6 #19 · §5.6).

### 1.2 범위

**안**: ~~`mcp_server` 소스·도구 확장~~ **[v3] 독립 패키지 `apm_gateway/` 신설(Open API 어댑터·`apm_*` 도구·이벤트 폴러·WAS 판정 — §0.7)**, 엔티티 정합 파일, `sre_agent` 지침·시그니처·권고·브리핑 확장, 이벤트
편입(폴링·어댑터), 노이즈 게이트 `app_impact` 축, text2sql 등록, **L2 대응·복구의 설계와 게이트 정의**.

**밖(v2 추가)**: RDB Export 적재본 SQL 경로(§0.4 — G-1 ⓑ로만 복원) · 제니퍼 API 서버+JDBC 드라이버(정체) ·
Kafka 트랜잭션 Export 소비(원시 스트림 필요 근거 없음 — §2.4 D).

**밖**: 제니퍼 제품 도입·라이선스·에이전트 설치(전제 조건 — §1.3), DPM(DB 성능) 연동(Plan 55 M3의 DPM
축 — 별건), L3 완전 자율 조치(Plan 78 §8.3 — 본 계획도 2단계에서 멈추고 L2까지만 연다), 제니퍼 자체 AI
(Insight Chat)와의 통합(폐쇄망에서 외부 LLM 의존[J-8] — 미채택 근거 §2.7).

### 1.3 전제·가정 (명시)

1. **제니퍼 버전은 5.6.4 이상**이며 온프레미스 설치형이다(v2 정정 — 정본 스펙 기준 5.6.4 · 최신 5.7.0[J-18] ·
   공식 MCP는 5.6.5+[J-17]). 운영 버전은 J0에서 실측한다(U-12). ~~5.6.0.5+ 조건인 API 서버~~ — v2 미사용.
   **[v3.2]** 최신 핫픽스는 **5.7.0.1(2026-09-04)**, 5.6.5 계열은 5.6.5.12(2026-09-28)다. 5.7.0.1은 비동기 트랜잭션을 애플리케이션 통계에 포함하므로
   전후 수치 비교에 주의한다. Open API 정본 스펙은 여전히 5.6.4다[J-23].
2. ~~RDB Export를 PostgreSQL로 활성화할 수 있다~~ — **v2 철회**(§0.4). 대신 **Open API 토큰을 AIOps 전용으로 발급**
   받는다 — 발급 위치 [설정 > JENNIFER 서버 > 인증토큰 발급][J-17] · **토큰별 사용량 제한**이 있다(*"사용량 제한을
   0으로 설정할 경우, 무제한 토큰으로 동작"*[J-20] — 제한 단위 ✖, U-5) · 뷰 서버에 Open API 비활성 비공식 옵션이
   있으므로(5.6.3[J-20]) 켜져 있는지 확인한다. **[v3.2]** 옵션 이름은 공개돼 있다 — 뷰 서버 실행 스크립트의 JVM 속성
   `jennifer.unofficial.disable.open.api=true`(5.6.2.4+). 토큰 검사를 건너뛰는 `ignore_auth_token` 옵션도 존재한다(5.6.0.19 기록) — 둘 다 U-5에서 점검[J-23].
3. **읽기 전용 자격증명은 토큰 1종**이다. 제니퍼 토큰에 조회 전용 등급이 있는지 ✖ — **같은 스펙에 쓰기·제어 API가
   공존**한다(`POST /api-v2/manage/data-server/control` · `PUT /api-v2/manage/domain/put` · `PUT /api-v2/manage/instance/{domainId}/{instanceId}/domain-id`[J-4] ·
   도메인 단위 인스턴스 GC 요청 `/api-v2/manage/instance/<domain-id>/gc`(5.6.4.28)[J-19]). → §8.1 **GET·경로 허용목록을
   1차 통제로 격상**한다(토큰 권한은 협의되면 추가 방어). **[v3.2]** 문서 밖 쓰기·제어 경로가 11건 이상 더 있고 민감한 GET도 있어
   1차 통제는 **메서드 + 경로 템플릿 정확 일치 허용목록**이다(§0.9 ② · §5.2(e)). 폴스타 DB의 `was_connection.jennifer_token`을 읽어 재사용하지
   않는다(제니퍼 측 권한 통제·감사 우회 — §8.2).
4. **네트워크 도달성**: `mcp_server` 호스트 → 뷰 서버 Open API 포트(v2 — 적재 PG 경로 삭제). 이벤트 push 단계에서는
   뷰 서버 → `alarm_server` 수신 포트(TCP 또는 SNMP trap UDP 162 — G-4). 폐쇄망 내부이므로 egress 없음.
   **[v3.2]** 조회 출발지는 v3에서 `apm_gateway` 호스트다. 뷰 서버 기본 포트는 `server_port` **7900**이다. 5.7.0.1+ 뷰 서버는 **SNI 호스트 검증이 기본 on**
   (`ssl_sni_host_check=true`)이라 `JENNIFER_API_URL`에는 IP가 아니라 **인증서의 호스트명**을 쓴다[J-23].
5. WAS는 **Java 계열(Tomcat·JEUS·WebLogic 등)** 이 1차 대상이다. `config/middleware_signatures.yaml`의 기본 세트와
   맞춘다. .NET/PHP/Node 인스턴스는 제니퍼가 지원하나[J-7] 시그니처·권고 표는 Java 우선이다.
6. 폴스타 `hostname`과 제니퍼 인스턴스명이 **일치하지 않을 수 있다** — 정합은 선언적 매핑 파일로 푼다(§5.3).

### 1.4 용어

| 제니퍼 용어 | 뜻 | 폴스타 대응 |
|---|---|---|
| 도메인(domain) | 에이전트 묶음(서비스·업무 단위) | (없음 — 존/실행 그룹과 유사한 관리 축) |
| 인스턴스(instance)/에이전트 | 모니터링 대상 WAS 프로세스 1개(`instanceId`·`instanceName`) | `cmm_resource`의 서버(호스트) — **1:N**(한 호스트에 인스턴스 여럿) |
| X-View | 트랜잭션 응답시간 산점도 + 개별 트랜잭션 프로파일(메서드·SQL·외부 호출 타임라인)[J-9] | (없음) |
| 액티브 서비스 | 지금 실행 중인 트랜잭션(스레드) 목록·상태·경과 시간·스택[J-4] | (없음) |
| PLC(Peak Load Control) | 동시 액티브 서비스 상한 초과 요청을 거절/리다이렉트하는 부하 제어[J-3][J-10] | (없음) |
| 이벤트(EVENT) | 임계·예외 기반 알람. 레벨 normal/warning/fatal[J-6] | `cmm_alarm` 심각도 1/2/3 |

---

## 2. 제니퍼 조사 결과 (2026-09-03 · 출처는 §12.3)

### 2.1 제품 구조 ✔

Agent → **Data Server**(에이전트 관리·데이터 처리) → **View Server**(화면·Open API·어댑터·RDB Export) +
Repository(자체 파일 DB, `db_data`/`db_view`) + HTML5 콘솔[J-11][J-1]. 지원 언어 Java · .NET · PHP · Python ·
Node.js(Go 없음)[J-7]. K8s 모니터링(5.6.1.1+)·MSA 토폴로지(2025-12)·OpenTelemetry 트레이스 수용(Collector 경유)
[J-12][J-13]이 5.x 마이너로 추가됐다. ~~OTel 메트릭/로그 수용·Prometheus exporter는 ✖~~ → **v2 정정**: OTel 메트릭
수용 **△**(5.7.0 *"stable `jvm.*` semantic convention 병행 인식"*·Go 런타임 메트릭 인식[J-18]) · 로그 수용 ✖ ·
**OTLP 내보내기 ✖ · Prometheus/OpenMetrics 양방향 ✖**. 5.6.5에 제니퍼 인사이트(서버 LLM·브라우저 LLM)[J-19],
5.7.0(2026-08-13)에 javax→jakarta(Jakarta EE 11) 전환[J-18] · **[v3.2]** 5.7.0.1 핫픽스(2026-09-04 — SNI 호스트 검증 기본 on)[J-23]. **공식 MCP 서버 ✔**(LLM 프록시 겸용 — §2.8)[J-17].

### 2.2 진단 관점 데이터 카탈로그 — 무엇을 어느 경로로 얻는가

| 데이터 | 경로 | 지연 | 진단 용도 | 확인 |
|---|---|---|---|---|
| 인스턴스 실시간 지표: `activeService`·`tps`·`responseTime`·`concurrentUser`·`rejectRate`·`heapUsed/heapCommitted`·`procCPU/procMemory`·`activeServiceRangeCount0~3` | **API** `/api/realtime/instance` | 초 | 골든 시그널·큐잉·거절 | ✔[J-4] |
| 메트릭 시계열(도메인/인스턴스/비즈니스, `interval_minute`) | **API** `/api/dbmetrics/{domain\|instance\|business}` | 분 | 사건창 추세 | ✔[J-4] |
| 메트릭 카탈로그(domain·instance·application·business·sql·externalCall) | **API** `/api/metrics` | — | 도구 매핑 실측 | ✔[J-4] |
| ~~인스턴스·도메인 5분/1시간/1일 통계, 애플리케이션 일 통계, 트랜잭션(1분)~~ | ~~**SQL** RDB Export 적재본~~ → **v2 미채택**(추세·기준선은 위 `/api/dbmetrics/*`가 대체) | 5분+ | 추세·기준선·질의 응답 | ✔ 테이블명[J-2] / ✖ 컬럼 DDL |
| 액티브 서비스 목록(`status`·`elapseTime`·`runningMode`·`runningFullText`·`cpuTime`·`clientIp`·`threadHash`·`runningDataSourceName`·`sqls`·`fetches`) | **API** `/api/activeService/list` | 초 | **큐잉·락·슬로우 SQL·외부 호출 대기 지목**(핵심) | ✔[J-4] |
| X-View 트랜잭션 검색(`txid`·`guid`·시간창 **1분 제한**)·프로파일 텍스트·SQL/파라미터 | **API** `/api/transaction/*` | 초 | 개별 트랜잭션 원인 분해(method/SQL/external/fetch/network) | ✔[J-4] · **[v3.2]** `profile.txt`·`sql`·`txid`는 `domain_id`·`txid`·**`time`** 필수(판단 ④) · `guid`는 `start_time`·`end_time` 필수 |
| 이벤트·에러 검색(`level`·`instance_id[]`·`error_type[]`) | **API** `/api/dbsearch/event`·`/error` | 초 | 사건창 선행 이벤트 | ✔[J-4] · **[v3.2]** `level`은 event 전용 · `error_type`은 error 전용(대문자) · 둘 다 `domain_id`·`start_time`·`end_time`(ms) 필수 · 응답에 `eventId` 없음 |
| 애플리케이션·SQL·외부 호출 통계(`max_row` 기본 1000, `sort_by_metrics`) | **API** `/api/status/{application\|sql\|external_call}` | 초 | 상위 N 느린 트랜잭션·SQL | ✔[J-4] · **[v3.2]** `max_row` 기본 1000은 구 사본(S2) 값 — 정본 5.6.4는 기본값 미선언 · `start_time`·`end_time`은 **시 단위만**(판단 ⑤) |
| 스레드 덤프·서비스 덤프·강제 GC·스레드 인터럽트/일시정지 | 콘솔 기능(4.x 매뉴얼 6.8·9.11·13장). **Open API 노출 ✖** — v2 정정: **강제 GC는 도메인 단위 API ✔**(`/api-v2/manage/instance/<domain-id>/gc`, 5.6.4.28[J-19] · 정본 스펙 5.6.4엔 미수록). 덤프·인터럽트·PLC는 정본 스펙 경로 검색 0건(△ — U-8) | — | 대응(§2.5) | △ 5.x 콘솔 유지 여부 |
| 토폴로지(도메인→인스턴스→애플리케이션→트랜잭션 Call Chain, 프로토콜별 엣지) | 콘솔(MSA 뷰). API 노출 ✖ | — | 의존 전파 | ✔ 기능[J-12] / ✖ API |
| 이상 탐지·상관(Anomaly Event · Metrics Correlation · Stacktrace Insight) | 콘솔(Jennifer Insight, 2025-12) | — | (참고 — 자체 AI) | ✔[J-8] |

### 2.3 이벤트 체계 ✔ (4.5 매뉴얼 11장 — 5.x 명칭은 J0에서 ~~`/api-v2/manage-rule-event`~~ **[v3.2] `GET /api-v2/manage/rule/event/error/<domainId>`(·`metric`·`compare`)**로 대조 — `manage-rule-event`는 v2 매뉴얼 파일명이다[J-23])

- **레벨**: `normal` / `warning` / `fatal` — 공식 PagerDuty 어댑터가 FATAL·CRITICAL→critical, WARNING→warning,
  NORMAL·RECOVERY·CLEAR→resolve로 매핑한다[J-14].
- **유형(접두 체계)**: `ERROR_*` — UNCAUGHT_EXCEPTION · **SERVICE_QUEUING** · **PLC_REJECTED** · JDBC_CONNECTION_FAIL ·
  DB_CONNECTION_FAIL · OUTOFMEMORY · SYSTEM_DOWN · PROCESS_DOWN · **JVM_DOWN** · JVM_CPU_HIGH_LONGTIME ·
  HIGH_RATE_REJECT · HIGH_RATE_FAIL · **MAYBE_GC_TIME_DELAY** · MAYBE_BUSY_PROCESS 등 / `WARNING_*` — **TX_BAD_RESPONSE** ·
  APP_BAD_RESPONSE · DB_BAD_RESPONSE · JDBC_BAD_RESPONSE · TX_CALL_EXCEPTION · JDBC_STMT_EXCEPTION · JVM_CPU_HIGH ·
  **JVM_HEAP_MEM_HIGH** · **RESOURCE_LEAK** · DB_TOOMANY_FETCH · **DB_CONN_UNCLOSED** · JDBC_*_UNCLOSED 등 /
  `USER_DEFINED_{FATAL,ERROR,WARNING,MESSAGE}` · `SYSTEM_MESSAGE`[J-3]. △ 5.x Application Insights 화면에
  `SERVICE_EXCEPTION`·`BAD_RESPONSE_TIME` 표기가 보여 일부 개명 가능성[J-8].
- **필드(EventData)**: `domainId`·`domainName`·`instanceId`·`instanceName`·`time`·`errorType`·`metricsName`·
  `eventLevel`·`message`·`value`·`otype`·`detailMessage`·`serviceName`·`txid`(Open API 응답은 `applicationName` 추가)[J-6].
  **[v3.2 정정]** 위는 **어댑터** EventData다. Open API `/api/dbsearch/event`의 `EventData`는 13필드(`applicationName`·`domainId`·`domainName`·`errorType`·
  `eventLevel`·`instanceId`·`instanceName`·`instanceOid`·`message`·`metricsName`·`time`(string)·`txid`·`value`)로, **`otype`·`detailMessage`·`serviceName`·`eventId`가
  없다**. ERROR 기반 이벤트는 `errorType`, 지표 기반 이벤트는 `metricsName`이 찬다[J-23].
- **외부 전송**: EVENT 룰 "외부연동" 토글 + 어댑터(Slack·Teams·PagerDuty·Rocket.Chat·LINE·JIRA·**SNMP trap**·
  eventlog 파일 — 공식 리포 30개)[J-15]. 범용 HTTP webhook 내장은 ✖(Slack/Teams 어댑터가 webhook 기반이므로
  같은 골격으로 커스텀 어댑터 작성 가능 △).
- **v2 보강**: SNMP 어댑터(`event.SNMPAdapter`, 5.2.3+)는 레벨별 trap OID(기본 셋 모두 `1.3.6.1.4.1.27767.1.1`)·
  community·대상 주소(기본 `127.0.0.1/162`)·**메시지 패턴(기본 time·domain·instance·level·name·value)** 을 설정한다
  — `txid`·`detailMessage`는 기본 패턴에 없다[J-15]. 뷰 서버에 **Kafka 트랜잭션 Export**가 내장됐다(5.6.2.7 · 트랜잭션만)[J-20].
  5.7.0부터 어댑터 패키지는 `extension_allowed_packages`에 등록해야 로드되고, javax→jakarta 전환의 기존 어댑터 영향은 ✖(U-9)[J-18].

### 2.4 연동 지점 비교와 판정

| 후보 | 데이터 | 장점 | 단점·리스크 | 확인 | **판정** |
|---|---|---|---|---|---|
| A. RDB Export → PostgreSQL을 `mcp_server` SQL 소스로 | 5분·1시간·1일 지표, 애플리케이션 일·분 통계, SQL 통계, 트랜잭션(1분) | 폴스타와 **동일 경로**(`SourceConfig`·asyncpg·`db_profiles`·`execute_sql` 게이트) · 제니퍼 서버 부하 격리 · 장기 보관·조인 자유 | 실시간 아님(5분+) · 액티브 서비스·프로파일·이벤트 **없음** · 컬럼 DDL ✖(비공개 엔지니어 문서) · 뷰 서버 설정·재기동 · 적재 DB 신설·운영 | ✔/✖ | ~~채택(J1)~~ → **v2 미채택(보류)** — 벤더 연동 표면이 아니라 사용자 가공용 적재본이다(§0.4). 장기 자유 집계 요구가 확인되면 G-1 ⓑ로 복원 |
| **B. Open API(REST, Bearer)** | 실시간 지표·액티브 서비스·이벤트/에러 검색·X-View·프로파일·SQL·통계·**메트릭 시계열(`/api/dbmetrics/*`)** | **가장 넓은 커버리지·실시간** · **OpenAPI 3.0.3 기계 판독 스펙**(`openapi.jennifersoft.com`)[J-4] · 제니퍼 자체 AI·공식 MCP도 같은 API를 쓴다[J-8][J-17] | `/api/transaction/time` 1분 창 · `max_row` 1000 · **토큰별 사용량 제한**[J-20] · 뷰 서버 부하 · **진단 조회가 전부 v1(`/api/*`)인데 v1은 *"not removed for compatibility, but are no longer maintained"*** [J-4](단 5.6.4에서도 v1 필드 추가 △ — 폐기 아님) · 같은 토큰으로 쓰기·제어 API 개방(§1.3-3) | ✔ | **채택(J1·J2 — v2 단일 조회 경로)** |
| C. JDBC 드라이버 + API 서버(Calcite SQL, 5.6.0.5+) | 파일 DB 일자별 테이블 SQL | 적재 없이 SQL | **별도 프로세스·계정** · 일자별 테이블 단위 · 조인·성능 한계 · 문서 희소 · **리포 2023-02 이후 정체**(§0.2) | ✔/△ | **미채택** — 정체된 비공식 SQL 우회로 |
| D. 트랜잭션 push(TransactionHandler 어댑터 · **Kafka 트랜잭션 Export**) | 실시간 트랜잭션 스트림 | 지연 최소 · v2: **Kafka Export가 뷰 서버에 내장**(5.6.2.7)[J-20] — Java 코드 0 | Kafka 브로커 신설·소비자 개발 · 업그레이드 호환 · ~~Kafka 공식 어댑터 ✖~~(v2 정정) | ✔ | **보류** — 고카디널리티 원시 스트림은 필요 근거가 없다(Plan 55 C-5). 이벤트만 E로 |
| **E. 이벤트 어댑터 → `alarm:raw`** — E-1 **SNMP trap 공식 어댑터** / E-2 **커스텀 EVENT 어댑터**(JSON TCP) | 이벤트(E-1: 메시지 패턴 필드 한정 · E-2: EventData 전 필드·txid) | 폴스타 알람과 **동일 구조로 노이즈 게이트 편입** · E-1은 **Java 코드 0·표준 프로토콜** · E-2는 기존 `tcp_receiver` 재사용 | 이벤트만 · "외부연동" 토글 · E-1: SNMP trap 수신기 신설·필드 빈약(API 보강 조회 필요) · E-2: Java 빌드·`extension_allowed_packages`·jakarta 호환 | ✔ | **채택(J4-2단계 · 방식은 G-4)** — 1단계는 B의 `/api/dbsearch/event` **폴링**(Java 코드 0) |
| F. **제니퍼 공식 MCP 서버**(LLM 프록시 `/mcp`) | Open API 위 벤더 정의 도구(예시 `metrics_list`·`transaction_list_by_application`·`sql_statistics`) | 벤더가 API 변화를 흡수 · 표준 프로토콜(MCP Streamable HTTP) — 우리 스택과 동일 | 도구 전체 목록·쓰기 도구 포함 여부·라이선스 ✖ · 5.6.5+·JDK 17 프록시 별도 설치 · **토큰을 클라이언트 헤더로 전달**(자격증명이 호출자에 있음) · hostname 앵커·마스킹·허용목록 없음 | ✔ 존재 / ✖ 상세 | ~~보류~~ → **v2.1 미채택**(G-8 실측 판정 · §0.5 — LLM 프록시 운영 전제·권한 이점 0·계약 가시성 상실) |

### 2.5 대응·복구 관점 — 제니퍼가 "할 수 있는 조작"과 "할 수 없는 것"

| 조작 | 제니퍼 제공 | 노출 경로 | 본 계획 위치 |
|---|---|---|---|
| **PLC 부하 제어**(동시 액티브 서비스 상한 · 거절 메시지/리다이렉트) | ✔ 에이전트 옵션 `set_limit_active_service`·`max_num_of_active_service`·`request_reject_type`[J-3][J-10] | 에이전트 설정(콘솔). API ✖ | L2 조치 후보 "유입 차단" — 실행 경로는 **콘솔 수동**(권고) 또는 설정 파일 반영(고위험) |
| 자동 서비스 덤프(`enable_dump_triggering`·`number_of_dump_trigger`) | ✔[J-3] | 에이전트 설정 | 진단 증거 채취 — **읽기성 조치**(L1에서 권고) |
| 스레드 인터럽트·우선순위 변경·일시정지·재시작(액티브 서비스 단위) | ✔ 4.x 콘솔 13장[J-3] | 콘솔. API ✖ · △ 5.x 유지 | L2 "특정 트랜잭션 중단" — **중위험**(단일 스레드 범위) |
| 강제 GC · VERBOSE:GC 토글 | ✔ 4.x 9.11.4[J-3] | 콘솔. v2 정정: **도메인 단위 GC 요청 API ✔**(`/api-v2/manage/instance/<domain-id>/gc`, 5.6.4.28[J-19]) | L2 "힙 압박 완화" — 중위험(STW 유발). **API는 도메인 전 인스턴스 대상이라 단일 인스턴스 원칙(§5.7 ③) 위반 → L2 카탈로그 비채택 · `mcp_server` 허용목록 밖(§8.1)** |
| EVENT 룰 on/off · ~~임계 변경~~ | ✔ Open API v2 ~~`manage-rule-event*`~~ **[v3.2] ERROR 룰 적용 on/off `PUT /api-v2/manage/rule/event/error/<d>/<유형>/applied` · 대상별 설정 `PUT·DELETE …/individual-setting/<id>`만** — **임계 변경 API는 공개 자료에 없다**(스펙에 없음 · 조회는 `GET …/rule/event/{error\|metric\|compare}/<d>`)[J-23] | ~~**API ✔**~~ **on/off·대상별 설정만 API ✔ · 임계 ✖** | **관측 설정 변경**은 조치가 아니라 통제 대상(§8.2) — 전부 허용목록 밖(§5.2(e)) |
| **인스턴스 재기동·배포 롤백·L4 트래픽 배제** | ✖ 제니퍼 기능 아님 | 외부 도구(WAS 관리 콘솔·스크립트·LB) | L2 고위험 조치 — 실행기는 제니퍼가 아니라 **별도 실행 경계**(§5.7) |

→ **제니퍼는 관측·차단(PLC)·스레드 제어까지**이고, 복구의 대표 조치(재기동·롤백)는 제니퍼 밖에 있다. 대응·
복구 설계(§5.7)는 그래서 "제니퍼 API로 조치"가 아니라 **"제니퍼 데이터로 판정·검증하고, 실행은 별도 경계"**
구조가 된다.

### 2.6 미확인 항목 → J0 실측 체크리스트

| # | 항목 | 실측 방법 | 좌우하는 설계 |
|---|---|---|---|
| U-1 | 5.6.x 이벤트 유형 정식 명칭·EVENT 룰 목록 | `/api-v2/manage-rule-event` · 콘솔 [관리 > EVENT 룰] | §5.5 심각도·시그니처 매핑 파일 · **[v3.2]** 실제 경로 `GET /api-v2/manage/rule/event/error/<d>`(1차)·`/metric/<d>/<대상>`·`/compare/<d>/<대상>`(예제는 `comparing` — 실측) · `level` 값 대소문자·복수 전달 형식·`eventLevel` 값도 확인(확인 불가 — [J-23] §5) |
| U-2 | ~~RDB Export 테이블 컬럼 DDL·최신 PG 호환~~ → **v2**: `/api/dbmetrics/{domain,instance,business}` **응답 필드 전수·`interval_minute` 허용값·1회 조회 창 상한** | 테스트 토큰으로 호출 → recorded JSON(`testdata/jennifer/`) | §5.2 구간 경로 조립 · 시그니처 임계 표본 · **[v3.2 — 확인 불가 #22]** `interval_minute` 허용값·1회 조회 창 상한은 공개 원천 0건 → **J0-L** 실측(1·5·60·1440 후보 · 테스트 환경만). 응답은 스펙상 지표 1개 시계열(`time`·`value`) — `metrics` 필수 · **[v3.3]** `metrics` 인자 값은 `/api/metrics` 카탈로그의 **snake_case** 식별자(`heap_used`·`service_time` 등 · `tps` 없음)다 — realtime의 camelCase 필드와 다르다(§0.10 #17). 응답 형태·`interval_minute`는 J0-L-b |
| U-3 | 5.x 데이터 보존 기본값(파일 DB — v2: 적재본 삭제) | 뷰 서버 설정 · `/api/dbmetrics/*` 최소 조회 가능 일자 | 조회 창 상한 · 인시던트 스코프 · **§0.4 "SQL 경로를 빼는 비용" ①의 크기** |
| U-4 | 인스턴스 명명 규칙 ↔ 폴스타 `hostname` 일치율 | `/api/instance` 전수 ↔ `cmm_resource` 대조 스크립트 | §5.3 매핑 파일 필요 여부(**최대 리스크 R-1**) |
| U-5 | Open API 토큰의 권한 등급(조회 전용 발급 가능?) · **v2**: 토큰별 **사용량 제한의 단위·초과 시 응답** · Open API 비활성 비공식 옵션 설정 여부 | 콘솔 **[설정 > JENNIFER 서버 > 인증토큰 발급]**(5.6.2+ · v1의 [관리 > 인증 토큰]은 구 메뉴) · v2 쓰기 API 호출 시도(**테스트 환경만**) | §8.1 허용목록(조회 전용 불가여도 1차 통제로 성립) · §8.4 레이트 리밋 · **[v3.2]** 구 메뉴 = [관리 > 인증 토큰 관리] · 점검 추가: 비활성 옵션 `jennifer.unofficial.disable.open.api`·토큰 우회 옵션 `ignore_auth_token`이 켜져 있지 않은지 · 비활성 시 응답 코드(미공개) · **도메인 단위 권한**(5.6.1)이 무엇에 묶이는지 · 쿼리 `token=` 방식은 쓰지 않음 · **[v3.3 부분 해소 — §0.10 #9·#10·#15]** 사용량 단위 = 요청 수(500 응답 포함) · 서버는 쿼리 `?token=`을 받아 준다(그래서 게이트웨이가 거부한다) · Open API 기본 활성 · 쓰기·관리 경로가 같은 토큰으로 라우팅된다(GET으로만 확인 — 토큰 단위 읽기 전용 제한이 없을 개연성) · 남은 것 = 초과 응답 · 실제 쓰기 거부 여부 · 도메인 권한 |
| U-6 | 액티브 서비스·프로파일 응답의 **PII 포함 여부**(URL 파라미터·SQL 바인드 값·clientIp) | 샘플 응답 수집 → `pii_probe.py` | §8.3 마스킹 규칙 |
| U-7 | 뷰 서버 Open API 호출 부하 상한(동시·초당) | 제니퍼 운영 조직 확인 | 도구 타임아웃·레이트 리밋 |
| U-8 | 스레드 덤프·PLC 조작의 5.x API 노출 여부 (v2: 강제 GC는 도메인 단위 API 존재 — [J-19]) | 정본 스펙(`openapi.jennifersoft.com`) + v2 GitHub 매뉴얼(`active-service/detail` 등 스펙 미수록분 △) 전수 · 벤더 문의 | §5.7 조치 카탈로그의 실행 경로 · **[v3.2 — 확인 불가 #43]** 정본·v2 매뉴얼·릴리즈 노트 5.6.0~5.7.0.1에 덤프·PLC·인터럽트 API **0건** → 벤더 문의 + J0-L 설치본 콘솔 확인. 없으면 L2 실행 채널은 허용목록 스크립트(§5.7) · `active-service/detail`은 존재하나 SQL·HTTP query 노출이라 허용목록 밖 |
| U-9 | ~~범용 webhook 내장 여부 · SNMP 어댑터 재사용 가능성~~ → **v2**: SNMP 어댑터 존재 ✔ — 운영 뷰 서버의 **어댑터 배포 정책**(`extension_allowed_packages` 등록 주체) · **SNMP 메시지 패턴에 instanceId·errorType를 넣을 수 있는가** · 5.7.0 jakarta 전환 후 커스텀 어댑터 빌드 호환 | 콘솔 어댑터 관리 화면 · 테스트 뷰 서버에 SNMP 어댑터 등록 → trap 수신 캡처 | §5.5 2단계 전송 방식(G-4) |
| **U-10** | **폴스타 `was_object`·`was_connection`의 운영 3종 DB 실재 여부** — 샌드박스 DDL에 `agent_id`·`hostname`·`obj_name`과 `jennifer_url`·`jennifer_token`·`jennifer_domain_id`·`jennifer_version`이 있다(§3.3) | `information_schema.tables`·행 수·`agent_id` 채움률 대조 · **v2**: `was_connection.connection_type` 값 분포(JDBC형 vs 제니퍼 URL·토큰형)·`jennifer_version` · `was_object.agent_id` ↔ `/api/instance` `instanceId` 표본 대조 | **§5.3 정합 1순위 브릿지**(R-1 완화) · 폴스타가 이미 제니퍼와 연동돼 있다면 자격증명 보관 주체 재검토(§8.2). ⚠ `was_object`의 `obj_hash`·`obj_name`·`obj_type`·`wakeup`은 오픈소스 APM Scouter `ObjectPack`과 같은 구조라 **폴스타 자체 WAS 에이전트 모델일 수 있다**(△) — `agent_id`를 제니퍼 인스턴스 ID로 **대조 전 단정 금지** |
| **U-11** | **제니퍼 공식 MCP 서버** — **v2.1: 판정 완료(§0.5 미채택)로 J0 필수 항목에서 제외.** 남는 것은 재검토 트리거 ⓐ 확인뿐: 운영에 사내 설치형 `jennifer-llm` 프록시가 가동 중인가 | 제니퍼 운영 조직 확인(뷰 서버 `llm_proxy_host` 설정 유무) · 트리거 ⓐ·ⓑ 성립 시에만 `tools/list` 실측 | §0.5 재검토 트리거 |
| **U-12** | 운영 제니퍼 **버전**(정본 스펙 5.6.4 / 최신 5.7.0) · §5.2 도구가 쓰는 **v1 엔드포인트 전건의 실 응답** | `/api/instance` 등 호출 · recorded JSON 채집 | R-12(v1 유지보수 중단) 영향 · 계약 테스트 픽스처 · **[v3.2]** 추가 확인: MB 정의(10⁶/2²⁰) · 단위 미기재 필드(`TransactionData`의 시간들·`elapseTime`·`runningTime`) · `max_row` 미지정 기본값 · `instance_id` 쉼표 복수값 실제 동작(스펙은 int32 선언) · **[v3.3 부분 해소 — §0.10 #11·#13·#16]** 오류는 HTTP 500 + `exception.message`(필수 파라미터 누락도 500) · `profile.txt`는 JSON Accept면 404 · v1 POST·`.xml` 변형 200 · 실 응답 필드는 J0-L-b |
| **U-13** | (v2.2) 운영 EVENT 룰이 실제로 내는 **`errorType` 전 목록**과 알람 kind 분류 결과 · WAS 사건에 **호스트 보강(E6 프로세스·L3)을 교차 증거로 남길지** | U-1 결과(`/api-v2/manage-rule-event`)를 두 분류기(`process_rank.classify_alarm_kind` · `investigation_guidance.classify_alarm_kind`)에 넣어 대조 | §5.5 정규화 · R-16 · J4 수용 기준 · **[v3.1] 권고·확정(2026-09-29)**: `apm` kind 선판정으로 OS 플레이북 주입과 OS kind L3 프로파일은 막는다(`apm` 전용 L3 프로파일은 두지 않는다 — D-209 ⑥ "kind 미판정이면 no-op") · **E6 호스트 상위 프로세스 보강은 교차 증거로 유지**하고 "호스트 참고"로 표기한다(WAS JVM 프로세스의 CPU·메모리 점유 확인에 유효) · 운영 `errorType` 전 목록 대조는 J0-O 실측으로 확인 · **[v3.2]** 지표 기반 이벤트는 `errorType`이 비고 `metricsName`이 찬다 — 분류 입력은 `alarmName = errorType`(비면 `metricsName`) |
| **U-14** | (v3.2 신설 — 확인 불가 [J-23] §4.5·§5) **시간대·시계 기준** — 응답 시각 필드마다 기준이 다르다(`endTime`은 애플리케이션 서버 · `collectTime`은 데이터 서버) · 시간대 명시 0건 · **X-View 기록이 전 트랜잭션인지 표본인지**(p95·에러율 집계의 전제 — 판단 ⑤) | J0-L: 로컬 설치본에서 epoch ms 왕복·X-View 건수 대 TPS 대조 · J0-O: 운영 시계·시간대 설정 | 게이트웨이 시각 처리(epoch ms만 · `time_pattern` 미사용) · `apm_app_health` p95·에러율의 신뢰도 표기 |

> **[v3.1]** U-1~U-13을 **J0-L(로컬 Docker)**과 **J0-O(운영)** 중 어디서 푸는지는 §0.8 (5) 표가 정본이다. U-4·U-10은 J0-O 전용, U-2·U-5는 J0-L에서 해소되고(**[v3.3] J0-L-a는 U-2·U-5·U-12 부분 해소 — §0.10**) U-8은 J0-L에서 확인한다(공개 API 0건 — [J-23] #43). U-14는 v3.2 신설.

### 2.7 유사 벤더의 AI·MCP 동향 (참고)

Dynatrace(공식 원격 MCP 서버 GA 2026-01 · Davis CoPilot)[V-1] · Datadog(MCP 서버 GA · Bits AI SRE — 알림
자동 조사)[V-2] · New Relic(AI MCP 서버 public preview 2025-11)[V-3] · **WhaTap**(국내, 공식 MCP 서버 10개 도구 —
APM 이상탐지·토폴로지·PromQL)[V-4] · Scouter(오픈소스, Web API v1 · 공식 MCP ✖)[V-5]. ~~제니퍼소프트 공식 MCP
서버는 ✖~~ → **v2 정정: 공식 MCP 서버 ✔** — 제니퍼 LLM 프록시가 MCP 서버를 겸한다(설치 가이드 10장 · §2.8)[J-17].
Insight Chat(2025-10)이 Open API를 tool로 쓰는 자체 에이전트다[J-8]. 공통 패턴은 *"벤더
API를 MCP 도구로 감싸고, 조사는 read-only, 조치는 승인 뒤"* 로 본 계획의 구조와 같다. 제니퍼 자체 AI를
채택하지 않는 이유: ① 외부 LLM 의존(폐쇄망용 브라우저 LLM은 개발 중[J-8] — v2: 5.6.5에서 서버 LLM·브라우저 LLM 출시[J-19]) ② 조사 두뇌를 둘로 쪼개면
Plan 64 §0 중복 금지·D-118 경계 위반 ③ 제니퍼 데이터만 보므로 인프라↔앱 교차 상관(Plan 55 §6)이 불가.
※ ①이 약해져도 ②·③은 그대로라 **제니퍼 AI(조사 두뇌) 미채택은 유지**한다. 공식 **MCP 서버(데이터 표면)** 는 별개
문제로 §2.8에서 판정한다.

### 2.8 제니퍼 공식 MCP 서버 — 어디에 붙일 수 있는가 (v2 신규 · **G-8**)

**확인된 사실**[J-17]: *"제니퍼 LLM 프록시 서버는 MCP(Model Context Protocol) 서버 역할을 동시에 수행합니다"* ·
MCP 서버 타입 (Streamable) HTTP · URL `<llm-proxy-server:port>/mcp` · *"MCP 서버는 제니퍼 OpenAPI를 통해 데이터를
조회하므로"* 헤더 `X-Jennifer-Api-Url`·`X-Jennifer-Api-Token` 필수 · 도구 예시 `metrics_list`·`transaction_list_by_application`·
`sql_statistics` · 제니퍼 인사이트 5.6.5+ · 프록시 JDK 17+ · 설치 파일 `jennifer-llm-1.x.x.zip`. 릴리즈 노트에는 MCP
언급이 없고 도구 전체 목록은 공개 자료에 없다(✖ → U-11).

| 안 | 구조 | 판정 |
|---|---|---|
| ① `mcp_server`가 Open API **직접** 호출(v1.1 설계) | `mcp_server` → Open API | ~~**확정(v2.1 · §0.5)**~~ **[v3] ④로 대체** — "Open API 직접 호출"(G-8)과 허용목록·hostname 앵커·마스킹·감사·상위 N 축약을 우리가 쥔다는 핵심은 ④가 그대로 잇는다. 바뀐 것은 호출 주체(`mcp_server` → 게이트웨이)뿐이다 |
| ② `mcp_server`가 제니퍼 MCP의 **클라이언트**가 되어 `apm_*`로 재노출 | `mcp_server` → 제니퍼 MCP → Open API | **미채택(v2.1 실측 판정 · §0.5 — M-1·M-2·M-3)** — 이점: 벤더가 v1 동결·API 변화를 흡수(R-12 완화). 비용: hop 1개·프록시 가용성 의존·벤더 도구 스키마 변경 추적. **전환 조건**: 도구 목록이 §5.2 표의 8종 뒷단을 덮고 · 쓰기 도구가 없거나 서버측에서 거를 수 있고 · 라이선스가 기존 계약 안일 것 |
| ③ `sre_agent`가 제니퍼 MCP에 **직결**(두 번째 MCP 서버) | `sre_agent` → 제니퍼 MCP | **기각** — (a) D-119 ① 관측 읽기 경계 일원화 위반 (b) **토큰이 클라이언트 헤더로 전달**되므로 조사 프로세스가 제니퍼 자격증명을 보유(D-119 ④ 서버측 자격증명 위반) (c) hostname 앵커·마스킹·허용목록·감사가 없다 (d) 도구 수·이름이 P4(8종 상한·질문형) 통제 밖 · **[v3 재해석]** (b)(c)(d)는 **공식 MCP 직결에만** 해당하고 자체 게이트웨이(④)에는 해당하지 않는다. (a)는 D-274로 해소됐다. **공식 MCP 직결 기각은 유지**(G-8) |
| **④ 자체 게이트웨이 패키지 `apm_gateway/`**(v3) | `apm_gateway`(자체 MCP 서버) → Open API · 소비자(`sre_agent`·`noise_gate`·`src`) → `apm_gateway`(MCP · Redis Stream · HTTP 스크레이프) | **확정(v3 · 2026-09-29 · D-274)** — 토큰·장애 격리 · 선택 배포 · WAS 판정 단일 정의 · 이벤트 폴러 내장(G-4 폴링 경로 해소). Open API 직접 호출이라 G-1·G-8과 정합 — §0.7 |

②를 재검토 트리거(§0.5)로 다시 열더라도 `sre_agent`·본체가 보는 표면은 `apm_*` 그대로다(`apm_client.py` 내부 교체).
**[v3]** 교체 지점은 `apm_gateway/adapters/jennifer/`다. 소비자가 보는 것은 게이트웨이의 `apm_*` 표면이라 재검토 결과와 무관하다.

---

## 3. 현행 실측 — 어디에 꽂히는가

> ⚠ **병렬 작업 주의**: 같은 날 `plans/50` v2.2(D-197 · 원 D-194) 작업이 `mcp_server/mcp_server/config.py` ·
> `sre_agent/sre_agent/interface/mcp_service.py` · `sre_agent/sre_agent/application/investigation_guidance.py`를
> **동시에 수정 중**임을 실측했다(디스크 변경 감지). 아래 `file:line`은 작성 시점 값이며, **각 Wave 착수 직전에
> 재실측**한다. 특히 `investigation_guidance.py`(§3.2 지침)는 D-197(원 D-194)이 만든 파일이라 §5.4-a의 확장 지점이
> 이동할 수 있다.
> **[v2.2]** 2026-09-29 재실측 결과(최신 `file:line`·판정)는 **§0.6 표가 정본**이다. 아래 본문의 줄 번호는 작성 시점 값으로 남겨 둔다.

### 3.1 `mcp_server` — 관측 데이터 읽기 경계 (D-119)

> **[v3]** 제니퍼는 여기 편입하지 않는다(D-274 — D-119 ① 개정). 이 절의 `mcp_server` 사실은 게이트웨이가 **복제·참조할 전례**(설정 형식·
> 반환 계약·Bearer·HTTP 클라이언트)와, 게이트웨이가 폴스타 정합(`was_object`)을 위해 **MCP로 호출하는 대상**으로만 쓴다 — §0.7 (5)·(7).

- **소스 선언**: `config.toml` `[[sources]]`(name·type `postgresql|db2`·readonly·query_timeout·max_rows·풀 크기) +
  `.env`의 `{NAME}_CONNECTION`(`mcp_server/mcp_server/config.py:70-79`, `config.toml:46-82`). ~~적재 PG는 항목 하나
  추가로 끝난다~~ → **v2: 제니퍼는 `[[sources]]`(DB)를 쓰지 않는다.** API 소스는 `PrometheusConfig` 전례(`config.py`의
  HTTP 소스 설정 객체)를 따른다.
- **도구 등록**: `server.py:123-135` — `register_tools`(SQL 일반·`list_sources`) · `register_polestar_tools`(옵션
  `expose_polestar_tools`) · `register_promql_tools(mcp, expose_raw_promql)`. 신규 `register_apm_tools(mcp, cfg)`가
  같은 자리에 선다.
- **반환 계약**: `{rows, row_count, queried_at, source_kind, source, engine}` / 오류 `{error}`
  (`polestar_tools.py:636-641`, D-122). `source_kind`는 `polestar_db`·`polestar_process_realtime`·`prometheus`가
  있다 — ~~`apm_db`·~~`apm_api`를 추가한다(v2: SQL 경로 철회로 `apm_db` 삭제).
- **HTTP 클라이언트**: `httpx`가 이미 쓰인다(`promql_tools.py:35`·`polestar_tools.py:39`). ⚠ `mcp_server/pyproject.toml`
  에는 미선언(루트 venv 공유로 동작 — 부채. 본 계획에서 선언 1줄 추가).
- **게이트**: `execute_sql` 기본 비노출(`expose_execute_sql=False`)·폴스타 도메인 deny·Bearer(D-125). ~~적재 PG에도
  같은 게이트가 자동 적용된다~~ → **v2**: SQL 게이트는 제니퍼와 무관해지고, 대신 **API 경로 허용목록**(§8.1)을 신설한다
  — `promql_tools`의 고수준/원시 분리(`expose_raw_promql`)가 전례다.

### 3.2 `sre_agent` — 조사 코어 (D-118·D-123)

- **도구 자동 발견**: `_build_mcp_servers()`가 `polestar_mcp_url` 하나를 `Config.mcp_servers`로 넘기고, 폴스타
  SQL·PromQL 도구가 **자동 발견**된다(`interface/mcp_service.py:97-113`). → `apm_*` 도구도 **배선 변경 0**으로
  보인다. 설정 키 이름이 `polestar_mcp_url`인 것은 의미상 어긋나지만(관측 경계 URL) **개명하지 않는다**(회귀 0).
- **프로파일**: 프로덕션은 `remote_vm_profile()` 고정(`mcp_service.py:130-134`). `middleware_profile()`은
  `vm_profile()`과 **allowlist가 동일**하고 차이는 조사 초점 노트뿐(`toolset_profiles.py:143-164`, W7-1).
  **[v2.2 정정]** `remote_vm_profile()`의 bash는 **2026-09-21부터 꺼져 있다**(D-233 · `toolset_profiles.py:240`). `middleware_profile()`은
  프로덕션 호출부가 없다(테스트만). 운영 조사가 대상 호스트를 보는 길은 `mcp_server` 도구뿐이다(§0.6 #25).
- **지침**: `investigation_guidance.build_guidance()`가 `ANCHORED_TOOLS` 4종(`polestar_metric_trend`·`polestar_alarm_history`·
  `polestar_incident_alarms`·`prom_metric_range`)에 사건 구간 인자를 강제한다(`investigation_guidance.py:19-34`).
  → `apm_*` 시계열 도구를 여기에 추가해야 사건창이 강제된다.
- **결정적 판정**: `domain/severity_signatures.py`의 `SIGNATURES`(OS 시그니처 · `match_signatures(tool_outputs)`)와
  `domain/remediation.py`의 `_CANDIDATES_BY_SIGNATURE`(11 kind — `oom_kill`·`fs_readonly`·`service_restart_loop`·
  `soft_lockup`·`hung_task`·`segfault`·`conntrack_full`·`fd_exhaustion`·`inode_or_disk_full` 계열, 위험도
  low/medium/high, **고위험×저신뢰 강등** `_HIGH_RISK_MIN_CONFIDENCE`)이 확장 지점이다(`remediation.py:28-116`).
- **브리핑**: `briefing_builder.BRIEFING_ELEMENTS` 6요소 · 인용 검증(`_is_cited(line, tool_names)`) ·
  `HUMAN_GATED_NOTE`(`briefing_builder.py:20-36`). `tool_names`에 `apm_*`가 들어가야 APM 증거가 "인용됨"으로 판정된다.
- **설정**: `settings.py` — `remediation_recommender_enabled=False`·`severity_judge_enabled=False`(기본 off) ·
  `investigation_timeout_seconds=300`·`investigation_max_concurrent=2`.
- **조사 계약**: `investigation_jobs.REQUIRED_EVENT_FIELDS=("serverName","hostname","severity")` · `contract_version`
  검증(`investigation_jobs.py:45,112-119`). 제니퍼 이벤트도 이 계약으로 들어와야 하므로 **인스턴스 → hostname
  해소가 트리거 전에** 끝나야 한다(§5.3·§5.5).

### 3.3 본체 `src` — 질의·조사 진입

- `fault_diagnosis` 노드(`src/nodes/fault_diagnosis.py:111-156`): `_extract_targets(state)` → (server_name, hostname,
  db_id) → 가용성 사전 판정(D-175) → `SreAgentClient.diagnose(...)`. 그래프 배선은 `noise_gate.fault_diagnosis_enabled`
  off면 미배선(`src/graph.py:352-354`).
  **[v2.2 한정]** 이 노드는 **3단 `semantic_router` 경로에만** 배선된다(`src/graph.py:497-501·594-599·728-741`). 기준 경로 2단(D-251)과
  운영 1단에서는 서브에이전트 목록에 없어 도달하지 않는다(HEAD `src/orchestration/schemas.py:17-23`). 사용자 pull 조사 위임은 지금 3단에서만
  검증된다. push(알람 → `investigation_trigger`)는 사다리와 무관하다(§0.6 #23).
- `config/db_registry.yaml:41-67`: `solutions`에 **`apm` 자리(주석)** — `backend: rest` · `capabilities: [was_metric,
  jvm_heap, thread_pool, transaction]` · `requires: [host_location]`. `backend` 축은 `sql | rest | mcp`를 지원하고,
  *"apm·dpm은 실제 연동 시 주석을 풀어 등재 · 그때까지 등록 0건 회귀를 테스트가 단언"*(plans/82 Wave 7 비범위).
- **폴스타 스키마에 제니퍼 연동 흔적이 이미 있다** (샌드박스 DDL `testdata/pg/init/04_create_all_tables.sql:3802-3860`):
  `polestar.was_connection`에 **`jennifer_url`·`jennifer_token`·`jennifer_domain_id`·`jennifer_version`** 컬럼,
  `polestar.was_object`에 **`agent_id`·`hostname`(NOT NULL)·`obj_name`(NOT NULL)** 이 한 행에 있고,
  `was_object_datasource`·`was_instance_resource`(`resource_id`)·`was_instance_group`·`was_dashboard`가 딸려 있다.
  → 폴스타 제품이 제니퍼 연동을 내장하고 있을 가능성이 크며, **`was_object`가 인스턴스↔hostname 정합의 1순위
  브릿지 후보**다(Plan 55 C-1·Plan 78 §4.7.3-1이 "최대 난제"로 지목한 지점). 다만 이 파일은 **합성 더미 픽스처**라
  운영 DB 실재·채움률은 **U-10에서 실측**한다. `config/db_profiles`·`knowledge`에는 이 테이블들이 등재돼 있지 않다
  (grep 0건) — 실재하면 프로필 편입 대상이다.
- **v2 재실측(2026-09-17 · 같은 DDL)**: `was_connection`에는 제니퍼 필드 옆에 **`connection_type`·`jdbc_url`·`jdbc_driver`·
  `db_user_name`·`db_user_pwd`·`db_sql`** 이 함께 있다 — 폴스타가 WAS 연결을 **JDBC형(DB 직결)과 제니퍼 URL·토큰형(API)
  둘 다 담는 테이블**로 모델링했다는 뜻이다(△). 제니퍼 5의 리포지토리가 파일 DB임을 감안하면 JDBC형은 4.x 리포지토리나
  타 APM용으로 보이며, **제니퍼 5 연결은 URL+토큰+`domain_id`(= Open API Bearer·`domain_id` 필수 인자)** 형태다 — 사용자
  전제("DB가 아니라 API")와 부합하는 정황이다. 샌드박스 값은 합성(`testdata/pg/init/05_insert_dummy_data.sql:2999` — `'connection_type_1'`·`'jennifer_token_1'` 류)이라
  운영 사용 여부는 ✖(U-10).
- `config/db_registry.yaml:59-64`의 `apm` 주석 자리는 원래부터 **`backend: rest`** 였다 — v1.1 §5.6이 이를 `backend: sql`로
  바꾸자고 했던 것을 v2에서 되돌린다. 단 `backend` 값은 `src/routing/registry.py:77`·`execution_groups.py:77`에서 **실어
  나르기만 하고 `rest`를 실행하는 코드는 0건**이다(grep 실측) — Plan 82 Wave 7 「`backend: rest` 그룹 실행자 훅」 소관.
  **[v2.2]** 2026-09-29에도 0건이다. 소관은 **`plans/121` TP-9.2·TP-10.5로 옮겨졌다**(D-270 ⑯ G-17 — 82 Wave 7 디스패치 흡수). 한편 2단에는
  본체가 `mcp_server` 고수준 도구를 부르는 배관(`DBHubClient.inspect_host` · `host_inspect` 서브에이전트)이 이미 있다(§0.6 #21).

### 3.4 `noise_gate` — 이벤트 수신·게이트·트리거

- 수신: `alarm_server`(TCP JSON 1행 → `xadd alarm:raw {"data": json}`, `base_receiver.py:44`, `tcp_receiver.py:78-90`).
  `BaseReceiver`가 추상 `start()`를 갖는다 → **새 수신기(폴링)** 를 같은 골격으로 둘 수 있다.
- 도메인: `AlarmEvent`는 폴스타 템플릿 변수와 **1:1**(`db_id`·`server_name`·`hostname`·`alarm_id`·`severity`·
  `resource_type`·`alarm_name`·`alarm_time`·`conditions`·`condition_log`, `domain/alarm.py:44-95`). `ServerIdentity.source_label`
  주석: *"소스 확장 시 family만 등록"*.
- 트리거: `application/nodes/investigation_trigger.py:95-170` — `resolve_targets` → `build_trigger_payload` →
  submit/poll. 대상 해소가 실패하면 사유를 남기고 생략(`:102-103`).

### 3.5 갭 — 무엇이 비어 있는가

| # | 갭 | 위치 |
|---|---|---|
| G1 | 제니퍼 소스·도구가 `mcp_server`에 없다(소스 0·도구 0) → **[v3]** `apm_gateway/` 패키지 자체가 없다(패키지·도구·폴러 0) | §0.7 · §5.2 |
| G2 | 인스턴스 ↔ hostname 정합 규약·파일이 없다 | §5.3 |
| G3 | `sre_agent` 지침·시그니처·권고·브리핑이 OS 시그니처만 안다(WAS 시그니처 0) | §5.4 |
| G4 | 제니퍼 이벤트 수신 경로·심각도 매핑·`app_impact` 축이 없다 | §5.5 |
| G5 | text2sql에 `apm` 솔루션이 미등록 (v2: `jennifer_export` DB 등록은 철회) · `backend: rest` 실행자 0건(Plan 82 Wave 7 → **v2.2: `plans/121` TP-9.2·10.5가 흡수**) | §5.6 |
| G8 (v2.2) | 제니퍼 이벤트 이름이 알람 kind 분류기(게이트·조사 양쪽)와 충돌 — OS 플레이북·보강이 잘못 붙는다 | §5.5 · R-16 |
| G9 (v2.2) | 2단(기준)·1단(운영)에서 사용자 pull 조사(`fault_diagnosis`)가 도달하지 않는다 — 87 소관 밖 | §3.3 · R-18 |
| G6 | 조치 실행 경로가 **의도적으로** 없다(불변식). L2로 올리려면 결정·실행기·승인 UX·검증·롤백 전부 신설 | §5.7 · G-6 |
| G7 | `mcp_server/pyproject.toml`에 `httpx` 미선언 | §7 |

---

## 4. 문헌 조사 → 설계 원칙

> 서지 검증: arXiv API 메타데이터(제목·저자·게재일)로 실측했다(2026-09-03). 게재처(venue)는 확인된 것만
> 적고, 확인하지 못한 것은 arXiv로 둔다. Plan 78 §3.3·§11이 이미 검증한 보안 문헌(IPIGuard·Task Shield·Adaptive
> Attacks·Coding Agents Are Guessing)은 **재인용**만 하고 카드를 반복하지 않는다. 카드 형식: 기여 → **시사점**.

### 4.1 LLM 기반 장애 진단 에이전트

- **RCACopilot** — Chen et al., *Automatic Root Cause Analysis via Large Language Models for Cloud Incidents*
  (**EuroSys 2024** · arXiv 2305.15778 · DOI 10.1145/3627703.3629553 — v1의 "ICSE 2024" 표기는 오류, v1.1 정정).
  인시던트 유형별 **사전 정의된 증거 수집 핸들러**를 먼저 돌리고, LLM은 수집된
  증거로 원인 범주 예측·요약만 한다. → **P1** 증거 수집은 결정적 핸들러(우리의 `apm_*` 고수준 도구), LLM은
  해석 — D-035와 동형. WAS 이벤트 유형별 "먼저 볼 것" 순서를 지침으로 고정한다(§5.4-a).
- **RCAgent** — Wang et al., *Cloud Root Cause Analysis by Autonomous Agents with Tool-Augmented LLMs*
  (CIKM 2024 · arXiv 2310.16340). 도구 증강 에이전트 + **관측값 스냅샷 압축·자기 일관성 검증·전문가 에이전트
  분업**. → **P2** 원시 응답(액티브 서비스 1000행·프로파일 텍스트)은 서버측에서 상위 N·요약으로 줄여 LLM에
  넣는다(Context-Minimization, Plan 78 §3.3).
- Ahmed et al., *Recommending Root-Cause and Mitigation Steps for Cloud Incidents using LLMs* (ICSE 2023 ·
  arXiv 2301.03797). 4만 건 인시던트로 원인·완화 단계 생성을 평가 — **완화 단계는 원인보다 훨씬 어렵고
  환각이 잦다**. → **P3** 완화·복구 권고는 LLM 자유 생성이 아니라 **시그니처 → 조치 표**(결정적)에서 고른다
  (현행 `remediation.py` 구조 유지·WAS 항목 추가).
- Roy et al., *Exploring LLM-based Agents for Root Cause Analysis* (FSE 2024 Companion · arXiv 2403.04123).
  ReAct 에이전트는 도구를 주면 정확도가 오르나 **도구 오남용·과잉 호출**이 는다. → **P4** 도구 수는 적게
  (8종 상한), 이름은 질문형(`apm_slow_transactions`), 인자는 값만(D-122).
- **Flow-of-Action** — Pei et al. (WWW 2025 Companion · arXiv 2502.08224). **SOP(표준 운영 절차)** 를 지식으로
  주입한 멀티에이전트 RCA — SOP 없는 에이전트보다 환각·헤맴이 준다. → **P5** WAS 장애 SOP(큐잉·풀 고갈·GC
  stall·슬로우 SQL)를 **선언적 파일**(`config/apm_playbooks.yaml`)로 두고 지침에 주입한다.
- **Xpert** — Jiang et al. (ICSE 2024 · arXiv 2312.11988) · **Nissist** — An et al. (arXiv 2402.17531) ·
  **StepFly** — Mao et al. (arXiv 2510.10074) · **FixItFlow** — Unnikrishnan et al. (arXiv 2607.13035).
  TSG(트러블슈팅 가이드)를 기계가 따라가게 구조화하거나 인시던트에서 자동 생성한다. → **P5 보강**: 플레이북은
  DAG(단계·조건·다음 단계)로 쓰고, 조사 완료 후 **브리핑에서 플레이북 후보를 역생성**하는 것은 후속(범위 밖).
- **Stalled, Biased, and Confused** — Riddell et al. (FORGE 2026 · ICSE 워크숍 · arXiv 2601.22208 · DOI
  10.1145/3793655.3793732). LLM RCA의 **추론 실패 16종** 분류 — 앵커링 편향·반복/정체·임의 증거 선택·믿음 갱신
  실패가 정확도를 **15%p 이상** 깎는 핵심 부정 예측 인자. → **P6** 브리핑은 인용된 증거만 결론으로 승격하고(현행
  `_is_cited`), 가설은 `[가설]` 접두로 분리한다. → **P15 반증·정체 가드**: 지침에 "주 가설에 대한 반증 도구 호출
  1회"를 요구하고, 코드가 **동일 도구·동일 인자 반복 호출(3회)** 을 감지해 `max_steps=40` 소진 전에 "미결"로
  종료한다(§5.4-a).
- **Agentic RCA through Evidence-Grounded Reasoning** — Wei et al. (arXiv 2607.22385) · **OpsAgent** — Luo et al.
  (arXiv 2510.24145). 증거 그라운딩·다단 에이전트. → P6 보강.
- **mABC** — Zhang et al. (arXiv 2404.12135) · **Blueprint First, Model Second** — Qiu et al. (arXiv 2508.02721).
  멀티에이전트 합의 · **결정적 워크플로 골격 위에 LLM**. Blueprint First의 배포 사례 V-A **"Java Heap-Exhaustion
  Diagnosis"**(수만 대 JVM · 주 약 40건 OOM 클러스터를 `jstat` → 힙 덤프 → 로그 분석 런북으로 자동화)가 WAS 도메인의
  직접 전례다. → **P7** 조사 골격(수집 → 판정 → 권고 → 검증)은 코드가 정하고 LLM은 칸을 채운다 —
  `investigation_dispatcher`·`briefing_builder`가 이미 이 구조다.
  ※ v1이 함께 인용한 *Multi-Agent LLM Orchestration … Incident Response*(arXiv 2511.15755)는 **저자 철회본**
  (코드 감사에서 결과 조작 확인)으로 실측되어 **v1.1에서 제외**했다.

### 4.2 멀티모달·트레이스 기반 마이크로서비스/미들웨어 RCA

- **Eadro** — Lee et al. (ICSE 2023 · arXiv 2302.05092): 로그·KPI·트레이스를 **하나의 그래프로 학습**해
  이상 탐지와 원인 지목을 동시에. **DiagFusion** — Zhang et al. (IEEE TSC 2023 · arXiv 2302.10512): 이벤트
  임베딩 + 배포 그래프. → **P8** 인프라(폴스타)·앱(제니퍼) 신호를 **같은 타임라인·같은 엔티티 키**로 병합해야
  상관이 선다(Plan 55 C-1·C-2). 학습 모델은 미채택(폐쇄망·라벨 부재) — 결정적 정렬만.
- **TraceDiag** — Ding et al. (FSE 2023 Industry · arXiv 2310.18740): 대규모 트레이스에서 **의존 그래프 가지치기
  (강화학습)** 로 해석 가능한 RCA. → 제니퍼 X-View의 프로파일(메서드·SQL·외부 호출 타임라인)이 곧 단일
  트랜잭션의 트레이스다. **P9** 느린 트랜잭션 상위 N의 **시간 분해(cpu/sql/fetch/externalcall/network)** 를
  결정적으로 집계해 "지연이 어디에 쌓였는가"를 먼저 판정한다(§5.4-b `apm_slow_transactions`).
- **MicroRank** — Yu et al. (WWW 2021 · DOI 10.1145/3442381.3449905) · **TraceRCA** — Li et al. (IEEE/ACM IWQoS 2021 ·
  DOI 10.1109/IWQOS52092.2021.9521340): 정상/비정상 트레이스가 각 구간을 지나는 비율(**스펙트럼 분석**)로 학습 없이
  원인을 랭킹 — "비정상은 많이, 정상은 적게 지나는 구간이 원인". **Nezha** — Yu et al. (FSE 2023 · DOI
  10.1145/3611643.3616249): 이종 관측 데이터를 **동질 이벤트로 정규화**한 뒤 패턴 마이닝(top-1 89.77%). →
  **P16 결정적 후보 축소**: 느린/에러 트랜잭션이 많이 지나고 정상은 적게 지나는 구간(SQL·외부 호출·메서드)을
  **서버측이 결정적으로 랭킹**해 후보를 줄인 뒤 LLM에 넘긴다(컨텍스트 비용과 앵커링을 동시에 줄임 · §5.2
  `apm_slow_transactions`의 분해 집계가 그 1차 구현). 제니퍼 이벤트와 폴스타 알람은 **하나의 이벤트 스키마**로
  정규화한다(§5.5 표 — Nezha의 골격).
- **CIRCA** — Li et al. (KDD 2022 · arXiv 2206.05871) · **RUN** — Lin et al. (AAAI 2024 · arXiv 2402.01140):
  개입 인식·Granger 인과로 원인 지목. → 인과 추론 모델은 미채택. 다만 **"선행 사건이 먼저"** 라는 순서 원칙
  (사건창 안의 선행 이벤트 확보 — 현행 `polestar_incident_alarms` 선행 규칙)을 APM 이벤트에도 적용한다.
- **RCAEval** — Pham et al. (WWW 2025 Companion · arXiv 2412.17015) · Wang et al., *A Comprehensive Survey on RCA
  in (Micro)Services* (arXiv 2408.00803). → 평가 축(원인 서비스·원인 지표·원인 유형)을 우리 골든 시나리오(§6 J3
  수용 기준)에 그대로 쓴다.

### 4.3 벤치마크·자율 운영 에이전트

- **OpenRCA** — Xu et al. (ICLR 2025 · 실제 장애 335건 + 68GB 텔레메트리): 최상위 모델도 **11.34%** 만 해결.
  **ITBench** — Jha et al. (ICML 2025 · PMLR 267 · arXiv 2502.05352, IBM): SOTA 에이전트가 SRE 시나리오 **13.8%**
  만 해결. **AIOpsLab** — Chen et al. (arXiv 2501.06706, Microsoft) · **OpsEval** — Liu et al. (FSE 2025 Companion ·
  arXiv 2310.07637 · 골든셋 80% 비공개 운영) · **SREGym** — Clark et al. (arXiv 2605.07161). 탐지→위치→진단→
  **완화**까지 단계별로 에이전트를 평가하며, 완화 단계 성공률이 가장 낮다. → **완전 자율 RCA·복구를 범위에서
  제외하고 "증거 수집 → 후보 축소 → 브리핑 → 승인형 완화"로 잡는 정량 근거**다(§5.7 L3 범위 밖). → **P10** 대응·복구는 진단과 별도
  **수용 기준**을 갖는다: "권고가 맞았는가"가 아니라 "실행 후 지표가 회복됐는가"(검증 루프). 그리고 착수 전에
  **목업 장애 시나리오**(Plan 65 목업 이벤트 생성기 확장 — WAS 시나리오)로 측정한다.
- **STRATUS** — Chen et al. (**NeurIPS 2025** · arXiv 2506.02009). 자율 SRE 멀티에이전트에 **Transactional
  No-Regression(TNR)** — (1) 완화 실패 시 항상 되돌릴 수 있고 (2) 건강 상태를 악화시키는 행동은 취소되도록 보장.
  AIOpsLab·ITBench 완화 성공률 1.5배 이상. → **P11** L2 실행기는 조치마다 (사전 상태 스냅샷,
  조치, 사후 검증, 실패 시 롤백)을 **하나의 트랜잭션**으로 묶는다(§5.7).

### 4.4 알람·이벤트 상관 (제니퍼 이벤트를 게이트에 넣을 때)

- **iPACK** — Liu et al. (ICSE 2023 · arXiv 2302.09520): 티켓·알람을 **인시던트 인식**으로 묶는다. **COLA** —
  Kuang et al. (ICSE-SEIP 2024 · arXiv 2403.06485): 상관 마이닝으로 시간·공간 관계를 잡고 **불확실한 케이스만
  LLM**에 넘기는 하이브리드(F1 0.901~0.930, 운영 배포). **DiLink** — Ghosh et al. (WWW 2024 Companion · arXiv
  2403.18639 — v1의 "LiLAC·FSE 2024" 표기는 오류): 텍스트 임베딩 + **서비스 의존 그래프** 정렬로 인시던트 연결
  (F1 0.96) — 상관 축에 **의존 토폴로지**를 넣으면 성능이 크게 오른다. **AlertGuardian** — Yu et al.
  (ASE 2025 · arXiv 2601.14912): 알람 생애주기 관리. **Oasis** — Jin et al. (FSE 2023 Industry · arXiv 2305.18084): 장애
  요약. → **P12** 제니퍼 이벤트는 **폴스타 알람의 대체가 아니라 상관 축**이다 — 같은 hostname·같은 사건창의
  인프라 알람과 묶어 하나의 사건으로 보고, `app_impact`는 **승격 전용**(억제를 되돌리지 않음 · 심각도 3 불변 —
  D-048 비대칭 계승)으로만 게이트에 영향을 준다.

### 4.5 대응·복구 자동화와 안전 (Plan 78 §3.3 문헌 재인용 + 추가)

- **IPIGuard**(EMNLP 2025) · **Task Shield**(ACL 2025) · **Adaptive Attacks**(NAACL Findings 2025) · **Coding Agents
  Are Guessing**(arXiv 2607.02294) — Plan 78 §3.3의 결론을 그대로 잇는다: *방어를 넣었다고 안전한 것이 아니라
  **실행될 명령이 아예 없어야** 인젝션이 성공해도 피해가 없다.* → **P13** L2에서도 **LLM 평면에는 실행 도구를
  주지 않는다**. 실행기는 LLM 미탑재·결정적·승인된 제안 id만 입력으로 받는 별도 경계다. HolmesGPT의
  `Toolset.approval_required_tools`(sre-agent/02 §9 실측)는 "LLM이 실행 도구를 갖되 승인만 붙이는" 방식이라
  **미채택**한다.
- **Google SRE Book ch.14 Managing Incidents · SRE Workbook ch.9 Incident Response**[S-1][S-2]: 역할 분리·
  명령 계통·IMAG 3C(Coordinate·Communicate·Control) · **"완화 먼저, 근본원인은 뒤"** — 롤백·드레인 같은 **일반
  완화(generic mitigation)** 우선 · 사후 검토. → **P14** 조치 카탈로그의 정렬 기준은 "효과"가 아니라 **가역성**
  이다(가역·저범위 먼저). → **P17** 브리핑에서 "즉시 적용 가능한 일반 완화"와 "근본원인 가설에 묶인 조치"를
  **분리된 섹션**으로 싣는다(§5.4-d).
- **Facebook FBAR(자가 치유)**[S-3] · **LinkedIn Nurse**[S-4]: 탐지 → 오류 클래스 **화이트리스트** 안에서만 자동
  복구 → 실패 시 **사람 티켓 종착**. 자동화 범위를 **가장 흔하고 가장 안전한 조치**부터 넓혔다. → P14 보강:
  L2 초기 카탈로그는 2~3개 조치로 시작하고, 카탈로그 밖은 실행기가 거부한다.
- **GIRA — Guarded Tool-Using LLM Agents for Incident Response** (OpenReview 워크숍 투고본 2026-03 · 저자·채택
  여부 미확인 — 보조 근거): 제안 생성과 행동 인가를 분리하는 다층 안전 게이트(차단 · dry-run 재작성 ·
  에스컬레이션), 툴 출력·티켓에 심긴 인젝션을 위협 모델에 포함, 지표로 **비인가 행동률**·blast radius. →
  §5.7 L2의 구조와 동형이며, J6 수용 기준에 "비인가 행동률 0"을 지표로 넣는다.
- **Agentic AIOps 가드레일 / AI SRE 성숙도 곡선**(Plan 78 §3.3 재인용): read-only → advised → **approval-based
  remediation** → autonomous. → 본 계획의 L2 = approval-based. L3(autonomous)는 범위 밖.

### 4.6 WAS 도메인 진단 지식 (1차 출처)

| 증상 | 진단 시그니처(제니퍼 데이터) | 표준 대응 | 출처 |
|---|---|---|---|
| **스레드 풀 고갈·서비스 큐잉** | `activeService` ≈ 상한 · `ERROR_SERVICE_QUEUING`/`PLC_REJECTED` · 액티브 서비스 다수가 같은 상태(`JEXENG`·외부 호출 대기)에 정체 · **stuck thread**(연속 작업 ≥ N초 — WebLogic `StuckThreadMaxTime` 기본 **600초**[W-6] · JEUS는 `max-thread-active-time` 초과 시 Blocked Thread 통지[W-7] △) · Tomcat은 `maxThreads` 포화 → `acceptCount` 큐 초과 시 거부[W-1] | 정체 지점(DB·외부 호출) 해소 → 원인 트랜잭션 격리 → 풀 상한·큐 조정은 재기동 필요 | Tomcat HTTP Connector·`Executor`[W-1] · WebLogic Overload[W-6] · JEUS[W-7] · JDK 21 Troubleshooting(행·루프)[W-8] |
| **커넥션 풀 고갈** | `runningDataSourceName` 대기 다수 · `JDBC_CONNECTION_FAIL` · `DB_CONN_UNCLOSED`/`RESOURCE_LEAK` · (HikariCP) pending↑ + `connectionTimeout` 예외 + `leakDetectionThreshold`(≥2000ms) 누수 로그 | 누수 트랜잭션 식별(X-View) → **풀 확대보다 누수·slow SQL 제거가 1순위** → 풀 상한·`removeAbandoned`·타임아웃 | Tomcat JDBC Pool[W-2] · HikariCP 풀 사이징(`core×2+spindle` · 데드락 최소 풀 `Tn×(Cm−1)+1`)[W-3] |
| **GC stall·힙 압박·누수** | `heapUsed/heapCommitted` 고점 지속 · `MAYBE_GC_TIME_DELAY` · `JVM_HEAP_MEM_HIGH` · `OUTOFMEMORY` · **OOM 메시지 7종**(Java heap space · GC overhead limit exceeded=GC 시간 98%·회수 2% 미만 5회 연속 · Metaspace · Requested array size · Out of swap · Compressed class space · Native method)[W-8] · **GC 후 old gen 하한이 계단식으로 상승 = 누수**(Cork · §12.1) | 힙 덤프(`HeapDumpOnOutOfMemoryError`·`jcmd GC.heap_dump`)·누수 객체 식별(컬렉션 유형 상위 우선) → 힙/GC 옵션 조정(재기동) | Oracle HotSpot GC Tuning Guide[W-4] · JDK 21 Troubleshooting(메모리 누수)[W-8] · OTel JVM 메트릭 컨벤션[W-5] |
| **슬로우 SQL** | `sqlTime`/`fetchTime` 비중↑ · `/api/status/sql` 상위 · `DB_TOOMANY_FETCH` | 실행계획·인덱스(DBA) — 앱 측은 페이징·fetch size | (DPM 축 — Plan 55 M3) |
| **외부 호출 지연** | `externalcallTime` 비중↑ · `HTTP_IO_EXCEPTION` | 타임아웃·서킷브레이커 · 의존 서비스 조사로 전이 | 토폴로지(제니퍼 MSA 뷰[J-12]) |
| **에러 급증** | `HIGH_RATE_FAIL` · `errorType` 분포 · 배포 직후 여부(`/api-v2/deploy` △ → **[v3.2] ✔ `GET /api-v2/deploy/<d>?startTime=&endTime=`** · ms · 25시간 이하 · 5.6.0.5+[J-23]) | 배포 롤백(고위험) | SRE Book rollback-first[S-1] |

### 4.7 설계 원칙 요약 (P1~P14 → 어디에 반영되는가)

| 원칙 | 반영 위치 |
|---|---|
| P1 증거 수집은 결정적 핸들러, LLM은 해석 | §5.2 도구 표면 · §5.4-a 지침 |
| P2 원시 응답은 서버측 축약 | §5.2 상위 N·마스킹 |
| P3 완화 권고는 시그니처→조치 표 | §5.4-c |
| P4 도구는 적게·질문형·값 인자 | §5.2 8종 상한 |
| P5 SOP/플레이북을 선언적 파일로 | §5.4-a `apm_playbooks.yaml` |
| P6 인용 승격·가설 분리·정체 종료 | §5.4-d 브리핑 |
| P7 골격은 코드·LLM은 칸 채우기 | 현행 구조 유지 |
| P8 같은 엔티티 키·같은 타임라인 | §5.3 정합 |
| P9 지연의 시간 분해 먼저 | §5.2 `apm_slow_transactions` |
| P10 대응·복구는 별도 수용 기준(회복 검증) | §6 J6 |
| P11 조치 = 트랜잭션(스냅샷·검증·롤백) | §5.7 |
| P12 APM 이벤트는 상관 축·승격 전용 | §5.5 |
| P13 LLM 평면에 실행 도구 없음 | §5.7·§8.1 |
| P14 가역성 우선·작게 시작 | §5.7 카탈로그 |
| P15 반증·정체 가드(동일 도구 반복 감지·반증 1회) | §5.4-a 지침 · J3 수용 기준 |
| P16 결정적 후보 축소(스펙트럼 랭킹)·이벤트 정규화 | §5.2 `apm_slow_transactions` · §5.5 표 |
| P17 일반 완화 섹션 분리(완화 먼저) | §5.4-d 브리핑 |

---

## 5. 목표 아키텍처

### 5.1 한 장 그림

```
 [제니퍼 View Server]  (v2: RDB Export 적재 경로 없음 · v2.1: 공식 MCP 서버 미채택 — §0.5)
   Open API /api/* · /api-v2/* (Bearer · 정본 스펙 5.6.4)
   (선택) EVENT 어댑터(SNMP trap | 커스텀 JSON) ──push──► alarm_server (§5.5)
        │  REST(httpx · GET·경로 허용목록)
        ▼
 ┌──────────────────────────── mcp_server (관측 읽기 경계 · D-119) ─────────────────────────────┐
 │  sources: polestar_*(PG/DB2)                            JenniferApiConfig(url·token·timeout·limits) │
 │  도구: polestar_* 8 · prom_* 7 · **apm_* ≤8** (벤더 중립 표면 · source_kind apm_api)                 │
 │  서버측: 인스턴스↔hostname 정합(config/apm_instance_map.yaml) · 상위 N 축약 · 마스킹 · 감사 · 허용목록 │
 │  (J7 선택) 표준 노출: GET /metrics/apm — OpenMetrics 1.0 (수치 지표만) ──► Prometheus·타 수집기      │
 └───────────────┬──────────────────────────────────────────────────────┬───────────────────────────┘
                 │ 자동 발견(RemoteMCPToolset)                            │ DBHubClient
                 ▼                                                        ▼
 ┌──────── sre_agent (조사 · D-118) ────────┐     ┌──────────── src 본체 / noise_gate ─────────────────┐
 │ 지침: APM 조사 순서·사건창(ANCHORED_TOOLS+)│     │ pull: text2sql `apm` 솔루션 축 · fault_diagnosis  │
 │ 판정: WAS 시그니처 8종(결정적·yaml 임계)  │     │ push: 제니퍼 이벤트 → alarm:raw → 게이트(app_impact│
 │ 권고: WAS 조치 표(가역성·위험도·검증법)   │     │       승격 전용) → investigation_trigger           │
 │ 브리핑: APM 증거 절 · 인용 검증           │     │ UI: 소스 배지 "제니퍼" · 조치 승인 화면(L2)        │
 └───────────────┬──────────────────────────┘     └──────────────────────────┬─────────────────────────┘
                 │ RemediationProposal(id·근거·위험도·검증법)  ── 승인(HITL) ──┘
                 ▼
 ┌──── remediation_executor (L2 · 신규 경계 · LLM 미탑재 · 옵트인 · G-6 후) ────┐
 │ 입력: 승인된 proposal id만 · 카탈로그 밖 조치 불가 · blast radius 한도          │
 │ 트랜잭션: 사전 스냅샷 → 조치 → 검증(apm_app_health 재조회) → 실패 시 롤백/에스컬 │
 │ 감사: 누가·무엇을·언제·근거·결과                                                │
 └────────────────────────────────────────────────────────────────────────────────┘
```

> **[v2.2 정정 — 그림 표기]** ① `mcp_server` 기존 도구는 `polestar_*` **9**(8 아님) · `prom_*` 7 · `om_*` 2(옵트인)이고, 폴스타 → OpenMetrics
> 브리지 `GET /metrics`가 이미 있다(`plans/92` B-2 구현 — §0.6 #7·#14). ② 본체 쪽 "text2sql `apm` 솔루션 축"은 `plans/121`의 **비SQL 1급 처리기**
> (2단 서브에이전트·처리기가 `DBHubClient` MCP 세션으로 `apm_*` 호출)로 읽는다 — `DBHubClient`는 그림의 연결선 이름 그대로다(§5.6).
> ③ `sre_agent` 칸의 "OS 근사"는 운영에서 셸이 아니라 폴스타 MCP 도구다(D-233).

**[v3 그림 — D-274 · 위 그림을 대체한다]** 위 그림의 `mcp_server` 상자 안 제니퍼 부분(`JenniferApiConfig`·`apm_*`·정합·`/metrics/apm`)은 전부
`apm_gateway`로 옮긴다. `sre_agent`·본체·게이트·L2 실행기 상자는 그대로다.

```
 [제니퍼 View Server]  Open API /api/* · /api-v2/*        (선택 · G-4b) EVENT 어댑터 push ──► apm_gateway 수신
        │ REST (GET · 경로 허용목록 · 리다이렉트 비추종 · 토큰은 여기서만)
        ▼
 ┌──────────── apm_gateway (독립 최상위 패키지 · 자체 MCP 서버 · 독립 프로세스 · D-274) ────────────┐
 │ adapters/jennifer/  Open API 클라이언트 · 허용목록(코드 상수) · 레이트 리밋                           │
 │ application/        인스턴스↔hostname 정합 · 상위 N 축약 · 마스킹 · 이벤트 폴러                        │
 │ domain/             WAS 시그니처 결정적 판정 · 이벤트 모델 · 레벨 매핑                                  │
 │ interface/          MCP apm_* ≤ 8 · Bearer · 감사 · (J7) GET /metrics/apm                              │
 └──────┬──────────────────┬──────────────────────┬──────────────────────┬───────────────────────┘
        │ MCP(was_object)  │ MCP(apm_*)           │ Redis XADD alarm:raw │ HTTP 스크레이프
        ▼                  ▼                      ▼                      ▼
   mcp_server         sre_agent · src 2단     noise_gate               Prometheus 등
  (폴스타·PromQL)     (조사 · 채팅 질의)       (게이트 · 조사 트리거)
```

### 5.2 데이터 평면 — ~~`mcp_server` 세 번째 소스~~ **[v3] `apm_gateway` 데이터 평면** (G1)

> **[v3 위치 대응]** 아래 (b)~(d)의 설계는 그대로 두고 위치만 바꾼다 — `JenniferApiConfig`·`apm_client.py` → `apm_gateway/adapters/jennifer/`
> (설정은 `apm_gateway/` 자체 설정 파일 + `apm_gateway/.env`) · `apm_tools.py` → `apm_gateway/interface/` · 정합·축약·마스킹 → `apm_gateway/application/` ·
> 반환 계약·Bearer는 `mcp_server`에서 복제. `source_kind="apm_api"`·`apm_*` 이름·8종 상한은 불변. "`mcp_server` 감사"는 **게이트웨이 감사**(같은 형식)로 읽는다.

**(a) ~~SQL 소스 `jennifer_export`~~ — v2 미채택(§0.4).** 복원 절차(G-1 ⓑ 선택 시): `config.toml` `[[sources]]` 1항목 +
`.env` `JENNIFER_EXPORT_CONNECTION`(`type=postgresql`·`readonly=true`) + `db_profiles/jennifer_export.yaml`(U-2 DDL 실측 후).
기존 `list_sources`·`execute_sql` 게이트가 자동 적용되므로 **복원 비용은 설정 추가 수준**이다.

**(b) API 설정 `JenniferApiConfig`** — `PrometheusConfig` 동형(`url`·`auth_token`(Bearer)·`query_timeout`·
`max_rows`·`rate_limit_per_sec`·`expose_raw_api=False`). 토큰은 `.env`에만(`JENNIFER_API_TOKEN`), 로그 마스킹.
**v2 추가**: `allowed_paths`(GET 허용 경로 목록 — **코드 상수 기본값**, 설정으로 넓히지 않고 좁히기만 가능) ·
`api_version_expect`(기동 시 1회 버전 확인 결과를 기동 로그에 — R-12).
**[v2.2 실측 반영]** `mcp_server` 설정은 pydantic-settings가 아니라 **dataclass + `config.toml` 섹션 + env 오버라이드**다(`config.py:53-66`·
`:348-462`). `.env`는 `mcp_server/.env`에서 읽는다(`:201-219`). 그래서 `JenniferApiConfig`는 `[jennifer]` TOML 섹션(비밀이 아닌 값)과
`mcp_server/.env`(URL·토큰)로 받는다. 신규 env 키는 `mcp_server/.env.example`에 같이 올려야 커버리지 테스트가 통과한다. HTTP 클라이언트는
PromQL `make_client`(`promql_tools.py:188-198` — base_url·인증 헤더·강제 timeout)와 OpenMetrics `make_scrape_client`
(`openmetrics_tools.py:79-84` — `follow_redirects=False`)를 합친 형태로 만든다. 리다이렉트를 따라가면 허용목록이 우회되기 때문이다.

**(c) 도구 표면 — 벤더 중립 `apm_*`(Plan 78 §4.7.2 4종 + 진단 필수 4종 = 8종 상한)**

| 도구 | 인자(값만) | 뒷단 | 반환 핵심 필드(OTel 컨벤션[W-5]) | 용도 |
|---|---|---|---|---|
| `apm_app_health` | `hostname`(또는 `instance`), `reference_time?`, `lookback_minutes?` | API `/api/realtime/instance`(현재) · API `/api/dbmetrics/instance`(구간 — v2, 구 SQL `INSTANCE_METRIC_5MIN`) | `http.server.request.duration`(p50/p95) · `tps` · `error_rate` · `active_services` · `reject_rate` | 골든 시그널 · **[v3.2]** 스펙에 백분위 필드 없음(#20) — 실시간·`/api/dbmetrics/instance`는 **평균** `responseTime`(ms)만 준다. p95·에러율은 사건창 ≤ 10분이면 **X-View(`/api/transaction/time`) 1분 분할 집계**, 넘으면 `/api/status/application` **시 단위** `failures/calls`·`maxResponseTime`(판단 ⑤ · 전수 여부 U-14) · `/api/dbmetrics/*`는 `metrics` 필수·지표 1개/호출 |
| `apm_runtime_health` | 동상 | API 실시간 · API `/api/dbmetrics/instance`(구간) | `jvm.memory.used/committed` · `jvm.gc.duration`(△ 메트릭 존재 시) · `process.cpu.utilization` | 힙/GC/CPU · **[v3.2]** GC는 `gcTimeUsage`(**%**, 지속시간 아님 · #19) · 힙은 `heapUsed`·`heapCommitted`(**MB**) · `procCPU`(%) |
| `apm_resource_pool` | `hostname` | API 액티브 서비스 집계 · API 실시간 인스턴스(△ 인스턴스별 Active DB Connection 필드 — U-12) | `db.client.connection.count{state}` · `thread_pool.active/max` | 풀 고갈 · **[v3.2]** DB 풀 ✔ `activeDBConnection`(5.6.3.9+)·`averageDbPoolIdleCount`·`averageDbPoolConfiguredCount`(#17) · **WAS 스레드 풀 상한 필드는 스펙에 없다**(#18) — `threadCurrent`(JVM 전체)·`activeService`로 근사하고 `[한계]`에 적는다 |
| `apm_slow_transactions` | `hostname`, `n≤20`, 구간 | API `/api/status/application`(sort) + `/api/transaction/*` | 상위 N: `name` · `duration` · **분해**(`cpu`·`sql`·`fetch`·`external`·`network`) · `error_type` | P9 시간 분해 · **[v3.2 — 판단 ⑤]** 1차 뒷단 = **`/api/transaction/time`**(1분 분할 · 상한 10분 · 분해 `cpuTime`·`sqlTime`·`fetchTime`·`externalcallTime`·`networkTime` · `errorType` · 결과에 `profile_ref{domain_id, txid, time_ms}`) · 2차 = `/api/status/application`(시 단위 맥락 · `[한계]` 표기) |
| `apm_active_services` | `hostname`, `n≤20` | API `/api/activeService/list` | `status` · `elapsed` · `running_mode` · `running_text`(마스킹) · `datasource` · `client_ip`(마스킹) | 큐잉·정체 지점 · **[v3.2]** `domain_id` 필수 · 응답에 `txid`·`statusElapseTime`(ms)·`startTime`도 있음 · `elapseTime` 단위 미기재(U-12) |
| `apm_events` | `hostname`, 구간, `level?` | API `/api/dbsearch/event` | `event_type` · `level` · `time` · `value` · `message`(마스킹) | 선행 이벤트 · **[v3.2 — 판단 ③]** `/api/dbsearch/event`는 `domain_id`·`start_time`·`end_time`(ms) 필수 · `level`은 event 전용 · 에러 보강은 `/api/dbsearch/error`(`error_type` 대문자) · 결과에 `profile_ref` |
| `apm_transaction_profile` | `txid` | API `/api/transaction/profile.txt`·`/sql` | 프로파일 **요약**(단계별 시간 상위 K, SQL 바인드 값 마스킹) | 개별 트랜잭션 · **[v3.2 — 판단 ④]** 인자 = `hostname` + `domain_id` + `txid` + `time_ms`(앞선 도구의 `profile_ref`를 그대로) · 뒷단 `profile.txt`·`sql`·`txid`는 `domain_id`·`txid`·`time` 필수 · `domain_id`가 정합 결과 도메인이 아니면 `{error: "profile_ref_mismatch"}` · **[v3.3]** `profile.txt`는 `Accept: text/plain`(또는 `*/*`)으로 부른다 — JSON Accept면 404(§0.10 #13) |
| `apm_instance_map` | `hostname?` | 정합 파일 + `/api/instance` | `instances[]{instance_id, name, domain, port, kind, match_confidence}` | 대상 확정·다중 인스턴스 · **[v3.2]** `Instance`에 `port`·`kind` **없음** — 반환 = `instance_id`·`name`·`domain`·**`hostName`·`ipAddress`**·`platform`·`status`·`match_confidence`(port·kind는 override에서만) · 전수 = `/api/domain` → 도메인별 `/api/instance`(`domain_id` 필수) |

- **구간 인자 규약**: `reference_time`·`lookback_minutes`는 현행 `incident_scope`와 동일 형식. 인자 없으면 "현재"
  이며, 지침(§5.4-a)이 사건 조사에서는 인자를 강제한다.
- **1분 창 제한**(`/api/transaction/time`)은 서버측이 구간을 1분 단위로 **분할 호출·상한(기본 10분)** 한다 — LLM에
  노출하지 않는다.
- **상위 N·마스킹은 서버측**(P2): `running_text`·`message`·SQL 바인드·URL 파라미터·`client_ip`는 `pii_filter`
  규칙(§8.3)으로 마스킹 후 반환. 원문은 감사 로그에도 남기지 않는다(마스킹본만).
- **원시 도구**는 옵트인: ~~SQL은 기존 `execute_sql` 게이트(소스 `jennifer_export` 허용 시)~~(v2 삭제), REST는 `apm_raw_api`
  (`expose_raw_api=True`일 때만 · GET 화이트리스트 경로만 — `/api-v2/manage/*`·`/restapi/user*`는 원시 도구로도 불가).
  **[v3.2]** 원시 도구도 아래 (e) 허용목록을 **그대로** 쓴다(넓히지 않는다). 민감 GET(`/api/auth/userlist*`·`/restapi/users`·`/api-v2/environment-variable/*`·
  `/api-v2/active-service/detail/*`)은 원시 도구로도 불가하다.
- **반환 계약**: `{rows|data, row_count, queried_at, source_kind: "apm_api", source: "jennifer",
  window?, instance_resolution: {matched, confidence, reason}}`(v2: `apm_db` 삭제). 미매칭 시 **빈 결과가 아니라 `{error:
  "instance_unresolved", reason}`**(침묵 폴백 금지).
- **[v3.3] 오류 분류 — HTTP 코드가 아니라 본문으로**(§0.10 #11): 제니퍼는 도메인 미접속과 필수 파라미터 누락을 모두 **HTTP 500**으로 돌려준다. 게이트웨이는
  v1 JSON의 `exception.message`(v2는 문자열 본문 `"500 500 …Exception: …"`)로 가른다 — ① *"Domain is not connected"* → 「예정 이름」 `{error: "source_unavailable", reason}`
  (사유 노출 · 침묵 폴백 금지) ② *"Required request parameter …"*·*"Cannot parse null string"* → `{error: "contract_violation"}`(**우리 쪽 버그** — 경고 로그 · 재시도하지 않는다 ·
  J1·J2 테스트가 잡아야 한다) ③ 그 밖의 4xx·5xx → `{error: "apm_api_error", status, message(마스킹)}`. 라이선스 없는 서버도 `/api/realtime/instance`·`/api/transaction/time`에
  **200 + 빈 `result`**를 주므로 빈 결과를 "정상 · 0건"으로 단정하지 않는다 — 인스턴스 해소 결과·도메인 연결 상태와 함께 판단한다(§0.10 #18).
- **[v3.3] 지표 식별자 두 체계**(§0.10 #17): `/api/realtime/instance`는 camelCase 필드(`heapUsed`·`gcTimeUsage`…), `/api/dbmetrics/*`의 `metrics` 인자는 `/api/metrics`
  카탈로그의 snake_case 식별자(`heap_used`·`gc_time_usage`…)다. `tps` 식별자는 없다 — TPS에 해당하는 지표는 J0-L-b 실데이터로 정한다(카탈로그에 `max_tps`·`service_rate`가 있다).
  J2는 **도구 반환 필드 ↔ realtime 필드 ↔ dbmetrics 지표명** 매핑 표를 `adapters/jennifer/` 상수로 두고, 기동 시 `/api/metrics` 응답으로 지표명이 있는지 확인한다(없으면 그 지표는 `[한계]`).

**(d) 벤더 매핑 계층(v2.1 개정 · G-8 확정 · [v3] 위치 = `apm_gateway/interface/`·`application/` ↔ `adapters/jennifer/`)** — `apm_tools.py`(도구 표면·계약·정합·축약·마스킹)와 `apm_client.py`(Open API
호출·허용목록·레이트 리밋)를 나눈다. ~~후자를 백엔드 인터페이스로 두고 `JenniferMcpBackend` 자리를 남긴다~~ — **v2.1 삭제**:
공식 MCP 미채택(§0.5)이므로 구현이 하나뿐인 추상화를 만들지 않는다. 계약 테스트는 recorded JSON → `apm_*` 반환으로 쓰고,
§0.5 재검토 트리거가 성립하면 그때 `apm_client.py` 내부를 교체한다(표면·계약 테스트는 그대로 회귀 기준).

**(e) [v3.2] 허용목록 정본 — 메서드 + 경로 템플릿 정확 일치 · 그 밖은 전부 거부**(§0.9 ② · [J-23] §4.4 · 부록 A·B)

| 메서드 | 경로 템플릿(정확 일치) | 쓰는 곳 | 필수 파라미터 · 비고 |
|---|---|---|---|
| GET | `/api/domain` | `apm_instance_map`(전수 1단계) | 없음 |
| GET | `/api/instance` | `apm_instance_map` | `domain_id` · 응답 `hostName`·`ipAddress` |
| GET | `/api/realtime/instance` | `apm_app_health`·`apm_runtime_health`·`apm_resource_pool`·(J7) | `domain_id` · `instance_id` 선택 |
| GET | `/api/dbmetrics/instance` | 구간 추세 | `domain_id`·`instance_id`·`interval_minute`·`metrics`·`start_time`·`end_time` · 지표 1개/호출 |
| GET | `/api/metrics` | 지표 이름 카탈로그 | 없음 |
| GET | `/api/activeService/list` | `apm_active_services`·`apm_resource_pool` | `domain_id` |
| GET | `/api/transaction/time` | `apm_slow_transactions`·`apm_app_health`(p95·에러율) | `domain_id`·`start_time`·`end_time` · **1분 창** |
| GET | `/api/transaction/txid` · `/api/transaction/profile.txt` · `/api/transaction/sql` | `apm_transaction_profile` | `domain_id`·`txid`·`time` · SQL 바인드 마스킹 |
| GET | `/api/dbsearch/event` · `/api/dbsearch/error` | `apm_events` · 이벤트 폴러 | `domain_id`·`start_time`·`end_time` |
| GET | `/api/status/application` · `/api/status/sql` · `/api/status/external_call` | 시 단위 맥락 · 상위 SQL·외부 호출 | 시 단위 구간 |
| GET | `/api-v2/deploy/{domainId}` | `was_error_burst` 근거(최근 배포) | `startTime`·`endTime`(ms) · 25시간 이하 · 5.6.0.5+ · 정본 미수록(v2 매뉴얼) — J0-L 실응답 확인 |

- **정확 일치 규칙**: 와일드카드 없음(`/api/transaction/*` 금지 — 5경로를 명시) · `.xml` 변형(`/api/domain.xml` 등 5개) 제외 · v1 조회 API의 **POST 변형도 거부**(우리는 GET만) ·
  경로 정규화(`..`·중복 슬래시·퍼센트 인코딩) 뒤 대조 · 쿼리 키는 경로별 허용 키만(예정) · 쿼리 **`token` 키는 항상 거부**(보조 자료의 `?token=` 인증은 URL·로그에 남는다) ·
  설정은 목록을 **좁히기만** 한다.
- **GET이라도 허용목록 밖이면 거부**한다 — GET = 안전이 아니다(PII·비밀 노출 GET이 있다).
- **[v3.3] 로컬 실측 근거**(§0.10 #9·#14~#16): 쿼리 `?token=` 200 · 민감 GET 200(`/api/auth/userlist` → `email`·`phoneNumber` · `/restapi/users` → `allowIp`·`password` 키) ·
  쓰기·관리 경로가 Open API 토큰으로 라우팅(400·405·500 NPE · GET 200) · `POST /api/domain`·`/api/domain.xml` 200 — 위 거부 규칙은 전부 **서버가 막아 주지 않는 것**이다.
  요청 `Accept`는 경로별로 고정한다(`/api/transaction/profile.txt` = `text/plain` · 그 밖 = `application/json` — 로컬 프로브 기준).
- **J1 거부 테스트 입력(HTTP 0회로 거부됨을 단언)**:
  (a) 비GET — `POST /api-v2/manage/data-server/control` · `POST /api-v2/manage/data-server/db/property/1000/copy` · `POST /api-v2/manage/domain-group` ·
  `PUT /api-v2/manage/domain/put` · `POST /restapi/user/` · `PUT /api-v2/configuration/rdb-export-password-override` · `PUT /api-v2/manage/rule/event/error/1000/ERROR_X/applied` ·
  `POST /api-v2/manual-rdb-export?date=2026-01-01` · `POST /api/domain`(v1 POST 변형)
  (b) 민감 GET — `GET /api/auth/userlist` · `GET /api/auth/userlist.xml` · `GET /restapi/users` · `GET /restapi/user/1` · `GET /api-v2/environment-variable/1000` ·
  `GET /api-v2/active-service/detail/1000/1` · `GET /api-v2/loaded-class/1000/1` · `GET /api-v2/manage/data-server/system-property-config` · `GET /api-v2/manage/rule/event/error/1000`
  (c) 변형·우회 — `GET /api/domain.xml` · `GET /api/realtime/instance.xml` · `GET /api/transaction/time/../../auth/userlist` · 허용 경로 + `?token=…` · 3xx 리다이렉트 응답
- 이 표 밖의 조회가 필요하면(예: `/api/realtime/domain`) 계획서에 경로를 추가하고 테스트를 함께 갱신한다. `GET /api-v2/manage/rule/event/…`·
  `/api-v2/manage/instance?processId=&hostname=`은 **J0 수동 채집 전용**(게이트웨이 밖 · 허용목록 밖)이다.

### 5.3 엔티티 정합 — 인스턴스 ↔ hostname (G2 · **R-1**)

> **[v3.2] 정합 순서 재판정** — `/api/instance` 응답에 **`hostName`·`ipAddress`**가 있다([J-23] #12). 새 순서: ⓪ 정합 파일 `overrides`(수동 · 최우선 — 종전과 같다) →
> **① `Instance.hostName` 직접 대조**(폴스타 `cmm_resource.hostname` = OS hostname과 대소문자·FQDN 정규화 후 일치 → high · 비면 `ipAddress` ↔ 폴스타 IP 보조 대조 → medium) →
> ② 폴스타 `was_object`(U-10 실재 시 — ①이 비었거나 교차 확인이 필요할 때) → ③ 인스턴스명 규칙(exact·prefix·regex). **①을 ②보다 앞에 두는 근거**: ①은 제니퍼
> 에이전트가 보고하는 벤더 자체 필드라 모든 설치에 있고 폴스타 테이블의 실재·채움률(U-10 미확인)에 기대지 않으며, `was_object`는 폴스타 자체 WAS 모델일
> 가능성(Scouter 구조 — U-10 경고)이 남아 있다. ① 결과는 OS hostname이므로 `nodename`(= `server_name`) 역해소 규칙(§5.9)은 그대로 적용한다.
> **R-1 완화 후보**로 기록한다 — 실제 채움률·일치율은 J0-L(형식)·J0-O(운영 일치율 · U-4)에서 확정한다. `/api-v2/manage/instance?processId=&hostname=`(5.6.0.21+)은
> `processId`가 필수라 역조회가 안 되고 `/api-v2/manage/*`라 **J0 수동 대조용만**이다([J-23] #45).

- **1순위 브릿지 — 폴스타 `was_object`**(`agent_id`·`hostname`·`obj_name`, §3.3): 운영 DB에 실재하고 `agent_id`가
  채워져 있으면(U-10) 이것이 정본이다 — 폴스타가 이미 관리하는 매핑을 재사용하고 새 매핑 파일은 **예외만** 담는다.
  조회는 `mcp_server`의 SQL 소스(폴스타)로 하며, 결과는 `apm_instance_map` 도구가 캐시(TTL 10분)한다.
  **[v3]** 게이트웨이는 폴스타 DB 자격증명을 갖지 않는다. `was_object` 조회는 게이트웨이가 **`mcp_server`를 MCP로 호출**해 수행한다(의존은
  게이트웨이 → `mcp_server` 한 방향 · TTL 10분 캐시). 조사 배치는 `execute_sql`이 비노출이므로(D-122) 필요하면 `mcp_server`에 폴스타 고수준 도구
  1종(「예정 이름」 `polestar_was_instances`)을 J1 범위로 추가한다 — §0.7 (7).
- 2순위 정본: **`config/apm_instance_map.yaml`**(선언적 · `middleware_signatures.yaml`과 같은 자세 — "정책은 파일에"). **[v3]** 위치는 게이트웨이 소유
  설정(`apm_gateway/` 아래 — 「예정」, 경로는 J1에서 확정)이다. 게이트웨이는 자체 cwd로 뜬다.
  ```yaml
  version: 1
  match_rules:                     # 자동 매칭(순서대로, 첫 성공)
    - kind: polestar_was_object    # 폴스타 was_object(agent_id ↔ hostname) — 운영 실재 시 1순위(U-10)
    - kind: exact                  # instanceName == hostname
    - kind: prefix                 # instanceName.startswith(hostname + "_")  → 인스턴스 구분자
    - kind: regex
      pattern: '^(?P<hostname>[a-z0-9-]+)[-_](?P<inst>\w+)$'
  overrides:                       # 수동 매핑(자동 규칙보다 우선)
    - instance_name: "WAS-KIMPO-01"
      hostname: "fgtsidd0"
      port: 8080
      kind: tomcat
  ```
- 해소 결과에 **신뢰도**(exact=high · prefix/regex=medium · override=high)와 **사유**를 싣고, `medium` 이하는
  브리핑에 "정합 근거"를 표기한다. 신뢰도 없음(미매칭)은 상관 보류(Plan 55 R-1 보수 원칙).
- 한 호스트 다중 인스턴스: `apm_instance_map`이 목록을 돌려주고, 사건 이벤트에 `instanceId`가 있으면 그것을
  우선, 없으면 **전 인스턴스를 순회**(상한 5)해 집계한다.
- 역방향(제니퍼 이벤트 → hostname)은 §5.5 수신기가 **같은 파일**로 해소한 뒤 `alarm:raw`에 넣는다 — 트리거
  계약 `REQUIRED_EVENT_FIELDS`(`hostname`)를 만족시키기 위해서다.

### 5.4 진단 확대 — `sre_agent` (G3)

**(a) 지침** — `investigation_guidance.py`
- `ANCHORED_TOOLS`에 `apm_app_health`·`apm_runtime_health`·`apm_events`·`apm_slow_transactions` 추가(사건창 강제).
- **APM 조사 순서 노트**(`APM_FOCUS_NOTE`) — RCACopilot의 유형별 핸들러 순서를 지침으로: ① `apm_instance_map`으로
  대상 확정 → ② `apm_events`(선행 이벤트) → ③ `apm_app_health`·`apm_runtime_health`(골든 시그널·런타임) →
  ④ 증상별 분기(큐잉이면 `apm_active_services`, 지연이면 `apm_slow_transactions` → `apm_transaction_profile`,
  풀이면 `apm_resource_pool`) → ⑤ 인프라 대조(`polestar_metric_trend`·`prom_metric_range`) → ⑥ OS 근사(W7-1)는
  **APM이 답하지 못한 것만**.
- **반증·정체 가드**(P15): 지침에 "주 가설에 대한 **반증 도구 호출 1회**"를 요구하고, 코드는 동일 도구·동일 인자
  **반복 호출 3회**를 감지해 조사를 "미결"로 종료한다(`max_steps` 소진 전 · 사유 브리핑 `[한계]`에 기재).
- **플레이북** `config/apm_playbooks.yaml`(P5): 이벤트 유형/시그니처 → 확인 순서·판정 기준·권고 후보 키.
  지침에는 해당 시그니처의 플레이북만 주입한다(컨텍스트 최소화).
  **[v2.2 선례]** 기존 OS 플레이북은 코드 dict `PLAYBOOK_NOTES`(`investigation_guidance.py:124-169`)이고, kind는 `classify_alarm_kind`
  (`:93-112` — 게이트 `process_rank.py:50-82`와 동형)가 `event.resourceType`·`alarmName`에서 부분 문자열로 뽑는다. 제니퍼 이벤트를 그대로
  넣으면 `JVM_HEAP_MEM_HIGH`·`OUTOFMEMORY`가 `memory`, `JVM_CPU_HIGH_LONGTIME`이 `cpu`, `PROCESS_DOWN`이 `process`로 잡혀 **OS 플레이북이
  주입된다**(2026-09-29 실측 — R-16). 그래서 APM 플레이북 선택은 `resourceType="apm.Instance"`(§5.5 정규화)를 **OS 키워드보다 먼저** 판정하는
  분기로 하고, 같은 분기를 게이트 분류기에도 **대칭**으로 넣는다(D-139 — 패키지 경계상 동형 재정의). 플래그는
  `openmetrics_guidance_enabled`(`settings.py:146`) 전례대로 기본 off · 끄면 조립 문자열 바이트 동일.
- **프로파일 2단계**: `middleware_profile()`은 유지하되, **APM 가용 여부**를 `sre_health`류로 사전 확인해 미가용
  시 "APM 미가용 — OS 근사로 폴백(사유)"를 지침과 브리핑 `[한계]`에 남긴다(Plan 78 W7-2 ④ · 침묵 강등 금지).
  **[v2.2 정정]** 운영(원격) 조사는 `middleware_profile()`을 쓰지 않고 bash도 없다(D-233). 폴백 문구는 "APM 미가용 — **폴스타 MCP 도구**
  (프로세스·OS 구성·메트릭 추세)로 대체(사유)"로 쓴다. 로컬 프로파일(`vm_profile`·`middleware_profile`)의 셸 근사는 개발 맥 검증용이다.
- **[v2.2 신규 — 선택] 결정적 사전수집**: `evidence_prefetch.py:31-33`은 폴스타 도구 3종만 먼저 부른다(D-197·D-209). APM 이벤트가 트리거한
  조사라면 `apm_events`·`apm_app_health`를 사전수집에 넣을 수 있다. 넣으면 LLM 단계 수가 준다. 넣지 않아도 LLM이 ReAct로 호출한다(J3 선택).

**(b) WAS 시그니처** — `domain/severity_signatures.py` 확장(결정적 · 임계는 `config/apm_signatures.yaml`)

> **[v3]** 판정 **정의**는 `apm_gateway/domain/`으로 옮긴다(단일 정의 · D-274 ⑤). 게이트웨이는 도구 반환(예: `apm_app_health`·`apm_runtime_health`)과
> 이벤트 페이로드에 판정 결과(「예정 이름」 `was_signals[]{kind, level, category, evidence}`)를 싣는다. `sre_agent` `severity_signatures`는 WAS 규칙을
> 다시 구현하지 않고 이 결과를 `Signal`로 승격만 한다. `noise_gate` `app_impact`도 같은 결과를 입력으로 쓴다. 임계 파일(`apm_signatures.yaml`)은
> 게이트웨이 소유 설정이다. 아래 표의 kind·판정·level은 그대로다.

| kind | 판정(도구 출력 기반) | level | category |
|---|---|---|---|
| `was_service_queuing` | `active_services ≥ 0.9·limit` 또는 이벤트 `SERVICE_QUEUING`/`PLC_REJECTED` | CRITICAL | strong |
| `was_thread_pool_exhaustion` | 액티브 서비스 상위 N 중 동일 `running_mode` 정체 비율 ≥ 0.7, `elapsed` ≥ T(기본 **600s** — WebLogic `StuckThreadMaxTime` 전례[W-6]) | CRITICAL | strong |
| `was_db_pool_exhaustion` | `db.client.connection.count{state=wait}` > 0 지속 또는 `JDBC_CONNECTION_FAIL`·`DB_CONN_UNCLOSED` | CRITICAL | strong |
| `was_gc_stall` | `MAYBE_GC_TIME_DELAY` 또는 ~~`jvm.gc.duration` 비중~~ **[v3.2] `gcTimeUsage`(%)** ≥ x% | WARNING | medium |
| `was_heap_pressure` | `jvm.memory.used/committed ≥ 0.9` 지속 ≥ k 샘플 · `JVM_HEAP_MEM_HIGH`·`OUTOFMEMORY`(메시지 7종 분기[W-8]) · GC 후 old gen 하한 **계단식 상승**(누수 시그니처 — ~~5분 통계 적재본~~ v2: `/api/dbmetrics/instance` 5분 간격으로 판정) | CRITICAL(OOM)/WARNING | strong/medium |
| `was_slow_sql` | 상위 N 트랜잭션의 `sql+fetch` 비중 ≥ 0.6 | WARNING | medium |
| `was_external_call_delay` | `external` 비중 ≥ 0.6 · `HTTP_IO_EXCEPTION` | WARNING | medium |
| `was_error_burst` | `error_rate` 기준선 대비 ≥ 3σ 또는 `HIGH_RATE_FAIL` | CRITICAL | strong |
임계값은 **잠정**이며 J0 실측·목업 시나리오(J3 수용 기준)로 보정한다. LLM은 임계를 판단하지 않는다(D-035).
**[v3.3]** 로컬 실측의 에이전트 부착 실패(JDK 17에서 `FATAL ERROR in native method: processing of -javaagent failed` → JVM abort · §0.10 #5)는 **시그니처 표에 넣지 않는다** —
이 상태의 WAS는 제니퍼에 접속하지 못해 `apm_*` 도구 출력이 없고, 판정 입력이 WAS 기동 로그다. 운영에서 에이전트나 JDK를 올린 뒤 WAS가 뜨지 않는 증상은 R-28과 가이드 §11로 다룬다.

**(c) 권고 표** — `domain/remediation.py` `_CANDIDATES_BY_SIGNATURE`에 WAS 항목 추가(P3·P14 — **가역성 순**)

| kind | 후보(요지) | 위험도 |
|---|---|---|
| `was_service_queuing` | 정체 지점(DB/외부) 확인 후 해당 트랜잭션 인터럽트(제니퍼 콘솔) | medium |
|  | PLC 상한 임시 하향으로 유입 차단(리다이렉트 페이지) | medium |
|  | 인스턴스 재기동(원인 미제거 시 재발) | **high** |
| `was_db_pool_exhaustion` | 누수 의심 트랜잭션(X-View `DB_CONN_UNCLOSED`) 식별·개발팀 전달 | low |
|  | 풀 상한·`removeAbandoned` 조정 후 재기동 | high |
| `was_heap_pressure` | 힙 덤프 채취(서비스 덤프) 후 누수 객체 분석 | low |
|  | 강제 GC(일시 완화 · STW) | medium |
|  | 힙 옵션 조정 후 재기동 | high |
| `was_error_burst` | 최근 배포 여부 확인 → 롤백 검토(**[v3.2]** 근거 조회 `GET /api-v2/deploy/{domainId}` — §5.2(e) · 25시간 이하) | high |
| `was_slow_sql` | 상위 SQL을 DBA 검토로 전달(DPM 축) | low |
| `was_external_call_delay` | 의존 서비스 조사로 전이(토폴로지) · 타임아웃 확인 | low |
고위험×저신뢰 강등 규칙(`_HIGH_RISK_MIN_CONFIDENCE`)은 그대로 적용된다. 각 항목에 **검증 방법**(예: 조치 후
`apm_app_health` p95·error_rate 회복)과 **롤백**(예: PLC 원복)을 필드로 추가한다 — L2(§5.7)의 입력이 된다.

**(d) 브리핑** — `briefing_builder`
- 인용 검증 `tool_names`에 `apm_*` 포함. 6요소 중 `evidence`에 **"애플리케이션(APM)"·"인프라"** 소스 라벨을 구분해
  싣는다(교차 상관의 가시화 · Plan 55 §6).
  **[v2.2 정정]** `tool_names`는 실제 호출된 도구명에서 만들어진다(`investigation_dispatcher.py:401`) — `apm_*` 추가에 **배선 변경이 필요 없다**.
  남는 일은 소스 라벨 구분뿐이다.
- `[한계]`에 정합 신뢰도·APM 미가용 폴백·1분 창 분할 상한 도달을 반드시 기재한다.
- **일반 완화 섹션 분리**(P17 · IMAG): 권고를 "즉시 적용 가능한 **일반 완화**(격리·트래픽 배제·PLC 유입 차단·
  재기동·롤백)"와 "**근본원인 가설에 묶인 조치**(근거·신뢰도)"로 나눠 싣는다 — 완화가 먼저, 근본원인은 뒤.
- `HUMAN_GATED_NOTE`는 L1에서 유지. L2 활성 시에는 "승인 대기 제안 id"로 문구가 바뀐다(§5.7).

### 5.5 이벤트 편입(push) — 제니퍼 이벤트 → 노이즈 게이트 → 조사 (G4)

> **[v3 — 폴러는 게이트웨이로 · G-4 폴링 경로 해소]** 폴링 수신기는 `noise_gate/alarm_server/`가 아니라 **`apm_gateway/application/`**(「예정 이름」
> `event_poller.py`)에 둔다. 게이트웨이가 `alarm:raw` **생산자**가 되며 형식은 `noise_gate/alarm_server/base_receiver.py:44`의 XADD(`{"data": json}`)와
> 같다. 아래 G-4 딜레마(① `mcp_server` 경유 / ② 직접 호출 → 자격증명 2곳)는 토큰이 게이트웨이 한 곳에만 있으므로 **사라진다**. 정규화
> (`noise_gate/domain/apm_event.py`로 계획했던 것)는 `apm_gateway/domain/`으로 간다. 게이트웨이는 Redis 접속 정보를 새로 갖는다.
> `noise_gate`에 남는 것은 kind 분류의 apm 선판정(R-16)·소스 배지·`app_impact` 승격뿐이다. 아래 원문은 이력으로 둔다.

**1단계 — 폴링 수신기(Java 코드 0)**: ~~`noise_gate/alarm_server/jennifer_poll_receiver.py`~~ **[v3] `apm_gateway/application/` 폴러**(~~`BaseReceiver` 상속~~ **[v3] 같은 XADD 형식을 복제**) —
주기(기본 30s)로 ~~`/api/dbsearch/event?level=warning,fatal&start=…`~~ **[v3.2] 도메인별 `/api/dbsearch/event?domain_id=<id>&start_time=<ms>&end_time=<ms>&level=<warning·fatal>`**(세 값 필수 · `level` 값 형식은 U-1)를 호출해 신규 이벤트만(~~`eventId`~~/`time`
커서) `alarm:raw`에 발행한다. ⚠ 이 수신기는 **`mcp_server`를 거치지 않고 Open API를 직접 호출**한다 — 관측
경계 일원화(D-119)와 긴장하는 지점이다. 선택지: ① 수신기가 `mcp_server`의 `apm_events`를 호출(경계 준수 ·
지연·의존 추가) ② 직접 호출(단순 · 자격증명 2곳). **권고 ①** — 자격증명·감사를 한 곳에 두는 것이 D-119의
취지이고, `alarm_server`는 이미 Redis만 아는 얇은 프로세스다. **G-4 게이트**.

**2단계 — 어댑터 push (선택 · v2.1: API 위주 확정으로 착수 조건부)**. 1단계 폴링의 지연·뷰 서버 부하·토큰 사용량이 **실측으로 문제될 때만** 착수한다. 둘 다 지연 초 단위 · 폴링 부하 0 · 선행은 제니퍼 운영 조직의
어댑터 배포 정책(U-9 — 5.7.0부터 `extension_allowed_packages` 등록 필요[J-18]).

| 방식 | 구성 | 장점 | 단점 |
|---|---|---|---|
| **2-A SNMP trap(공식 어댑터)** | 공식 `event.SNMPAdapter`[J-15]를 **설정만으로** 등록(Java 코드 0) → 신규 `noise_gate/alarm_server/snmp_trap_receiver.py`(`BaseReceiver` 상속 · UDP) → 정규화 → `alarm:raw` | **벤더 공식 어댑터 · Java 코드 0 · 빌드·jakarta 호환 부담 0** (※ 사용자가 정한 표준 연동 규격은 OpenMetrics라 SNMP의 "표준" 여부는 선택 근거가 아니다 — v2.1) | 메시지 패턴 필드 한정(기본 time·domain·instance·level·name·value — `txid`·`detailMessage` 없음) → 트리거 전 `apm_events`로 **보강 조회 1회** · SNMP 라이브러리 신규 의존(`pyproject` extra) · community 문자열 = 평문 인증(v2c 추정 △ — 수신 포트를 뷰 서버 IP로 제한) |
| **2-B 커스텀 EVENT 어댑터** | 공식 `jennifer-view-extension-tutorial`[J-6] 골격으로 EVENT 어댑터(Java/Kotlin) → 기존 `tcp_receiver.py`로 JSON 1행 | EventData **전 필드**(`txid`·`errorType`·`instanceId` 포함) · 수신 측 코드 재사용 | Java 빌드·배포·**업그레이드마다 호환 확인**(5.7.0 javax→jakarta) · 벤더 규격 밖 산출물을 우리가 유지 |

**[v3]** 2단계 수신기(2-A SNMP trap 수신 · 2-B 커스텀 어댑터의 JSON 수신)도 정규화·정합이 게이트웨이에 있으므로 **게이트웨이 안에 두는 것을 권고**한다
(2-B의 목적지가 `alarm_server` 9100이 아니라 게이트웨이 수신 포트가 된다). 착수 여부·방식·수신 위치는 **G-4b**로 남긴다.
권고(v2.1): 착수하게 되면 운영 조직이 커스텀 어댑터 배포를 거부하면 **2-A**, 아니면 필드 완전성 때문에 **2-B** — 어느 쪽이든
1단계(폴링)가 먼저 돌며, 2단계는 폴링을 **대체**한다(중복 발행 금지 — 커서·~~`eventId`~~ **[v3.2] 합성 멱등 키** 멱등).

> **[v3.2 — 판단 ③] 폴러 커서·멱등** — Open API `EventData`에는 **`eventId`가 없다**. 커서 = 마지막으로 발행한 `time`(epoch ms)이고, 다음 호출은
> `start_time`을 그 값으로 **경계 포함** 재조회한다(같은 ms에 여러 이벤트가 올 수 있다). 중복 제거는 **합성 멱등 키** = `domainId`·`instanceId`·
> (`errorType` 또는 `metricsName`)·`time`·`txid`의 해시로 하고, 최근 키 집합을 겹침 구간만큼 보관한다(재기동 복구용 저장 위치는 J4에서 — Redis 권고).
> 시각은 epoch ms로만 주고받고 `time_pattern`은 쓰지 않는다(시간대 해석 회피 · U-14). 이벤트는 OpenMetrics 범위 밖이다
(*"Contrary to metrics, singular events occur at a specific time"*[OM-1]) — 이벤트를 게이지로 바꿔 표준 노출에 태우지 않는다.

> **[v3.3] 폴러 오류 처리·호출 예산**(§0.10 #10·#11): 도메인이 미접속이면 `/api/dbsearch/event`가 HTTP 500 *"… Domain is not connected"*를 돌려준다. 폴러는
> 이것을 **그 도메인의 수집 불가 상태**로 기록하고 커서를 전진시키지 않는다(다음 주기에 같은 창부터 · 이벤트 유실 방지). 상태는 기동 로그·헬스체크에 드러낸다(침묵 금지).
> *"Required request parameter …"*는 폴러 버그이므로 경고 로그를 남기고 그 도메인 폴링을 멈춘다. 토큰 `usageCount`는 **실패 응답까지 1건씩** 세므로 미접속 도메인을
> 매 주기 다시 부르면 한도를 쓴다 — 미접속 도메인은 백오프(§8.4)로 간격을 늘린다.

**페이로드 정규화(공통)**: 폴스타 템플릿 필드로 매핑해 `AlarmEvent`가 그대로 받게 한다.

| 제니퍼 EventData | `alarm:raw` 필드 | 비고 |
|---|---|---|
| `instanceName`·`instanceId` | `hostname`·`serverName`(정합 파일로 해소) + `raw_payload.apm.instance_*` | 미해소 시 `hostname=""` → 트리거가 사유 남기고 생략 |
| `eventLevel` fatal/warning/normal(**[v3.2]** 대소문자 무시 — 값 형식 미정의 · U-1) | `severity` 3/2/1 · 해소(RECOVERY/CLEAR △) → 0 | 선언적 매핑 `config/apm_event_levels.yaml` |
| `errorType` **[v3.2] 또는(비면) `metricsName`** | `alarmName`(원문 유지 · 지표 기반 이벤트는 `errorType`이 비고 `metricsName`이 찬다 — R-16 kind 선판정 입력도 이 값) · `resourceType="apm.Instance"` | `resource_type`으로 소스 구분 |
| `time`·`value`·`message`·`txid` | `alarmTime`·`conditionLog`·`conditions`·`raw_payload.apm.txid` | `message` 마스킹 |
| (상수) | `dbId="jennifer"` · `source="jennifer"` | `ServerIdentity.source_label` 배지 = family "제니퍼" |

> **[v2.2 보강 — 정규화가 건드리는 기존 소비자 셋]** ① **kind 분류**: 게이트 `classify_alarm_kind`(`noise_gate/domain/process_rank.py:50-82`)는
> `resource_type`·`alarm_name` 부분 문자열로 판정한다. 위 매핑대로면 일부 제니퍼 이벤트가 `memory`·`cpu`·`process`로 잡혀 E6 프로세스 보강
> (`alarm_context_enricher.py:323·408`)과 L3 kind 프로파일(D-209 ⑥ · `host_diagnostic_collector.py:39-55`)이 붙는다. J4는
> `resource_type="apm.Instance"`를 **먼저** 판정하는 `apm` kind를 넣는다. 호스트 보강을 교차 증거로 **일부러 유지할지**는 J4에서 정한다(U-13).
> ② **소스 배지**: `source_labels_for`(`application/server_identity.py:69·163`)는 **db_id로** 레지스트리를 본다. `dbId="jennifer"`는 DB가 아니므로
> (121 TP-9.2 "solutions 전용 family") 라벨 해석에 분기가 필요하다. ③ **`app_impact`**: 예약 키만 있다(`agentic_enricher.py:279` — `None`).
> 판정 코드는 J4가 처음 만든다.

**게이트 통합** — `app_impact` 축(Plan 55 §3·D-048.6 자리): 같은 `hostname`·사건창(기본 10분)에 폴스타 알람과
제니퍼 fatal 이벤트가 겹치면 **승격만**(DASHBOARD→PAGE 등). 억제를 되돌리거나 심각도 3을 건드리지 않는다.
제니퍼 이벤트 **단독**은 유형별 정책(`apm_event_levels.yaml`의 `notify: page|dashboard|suppress`)을 따른다.
트리거 페이로드에 `hints: {solution: "apm", instance_id, event_type}`를 실어 `sre_agent` 지침이 플레이북을 고르게 한다.

### 5.6 질의 경로(pull) — text2sql (G5)

> **[v2.2 재정렬 — 먼저 읽을 것]** 아래 v2 서술의 "Plan 82 Wave 7 그룹 실행자 훅(`mcp` 변형)"은 **D-270 ⑯(G-17)로 `plans/121`에 흡수**됐다.
> 기준 경로도 2단으로 바뀌었다(D-251). 그래서 J5는 다음 형태로 읽는다.
> 1. **등재** — `config/db_registry.yaml` `solutions`에 `apm`(`backend: mcp` · 고유 family `jennifer` · `databases`·`ACTIVE_DB_IDS` 미사용)을
>    **`plans/121` TP-9.2의 비SQL 시스템 등재 형식**으로 올린다. 새 능력 코드(`was_metric` 등)마다 `capabilities:` 카탈로그(`:74-107` — D-224 ①)
>    설명 행을 더한다. 등록 0건을 단언하는 테스트 2건(`tests/test_orchestration/test_execution_groups.py:31-33·40-42`)을 함께 갱신한다.
> 2. **실행** — 2단에서 APM 조회를 맡는 **1급 처리기**를 둔다. 선례는 121 TP-10.5 Prometheus `metric_query`(LLM은 템플릿 id·기간·입도만 ·
>    서버가 조립)다. 본체는 기존 `DBHubClient` MCP 세션(`src/dbhub/client.py:421-449·510-570` `inspect_host` 배관)으로 `apm_*`를 부른다.
>    본체에 제니퍼 URL·토큰을 두지 않는다(아래 "`rest`가 아니라 `mcp`인 이유"와 같다).
> 3. **인가** — 소스 단위 인가 `allowed_sources`(D-270 ⑰ · D-232 확장)를 따른다. 권한 밖 사용자에게는 안내·역질문에도 APM을 노출하지 않는다(D-264 선례).
> 3′. **[v3] 엔드포인트** — 본체가 부르는 MCP 서버는 `mcp_server`가 아니라 **`apm_gateway`(두 번째 MCP 엔드포인트)**다. 현행 `DBHubConfig`는
>    `server_url` 하나(HEAD `src/config.py:173`)이고 처리기 코드도 0건이라, `plans/121` 처리기 설계 때 두 번째 엔드포인트 설정을 함께 정하면 된다
>    (설계 시점 비용뿐 · G-5′ 권고 ⓐ와 정합). 위 2.의 "기존 `DBHubClient` MCP 세션"은 "같은 형식의 두 번째 MCP 세션"으로 읽는다.
> 4. **선행** — `plans/121` TP-9.1·9.2·10.5(묶음 C · 2026-09-29 코드 0)와 87 J2. 121이 늦어질 때의 임시안(`host_inspect` 프로파일에
>    `apm_*`를 붙이는 방식 — 92 O3 `metrics_live` 선례)은 121의 "처리기 계약 하나" 방향과 갈라질 수 있어 **G-5에서 사용자 판단**을 받는다.
> 5. **pull 조사 위임** — "`fault_diagnosis` 의도는 변경 없음"은 **3단 전용**이다(§3.3 [v2.2 한정]). 2단에서 "OO WAS 장애 원인" 같은 조사 위임이
>    도달하지 않는 공백은 87 소관 밖이다(R-18).
> 6. **매뉴얼** — 채팅 응답에 APM 답변·소스 배지가 생기면 같은 작업에서 사용자 매뉴얼을 갱신한다(D-255).

- **v2 재설계** — DB 등록 없이 솔루션 축만 연다.
  `db_registry.yaml`: `solutions`의 `apm` 주석을 **해제·수정** — ~~`backend: sql`(적재본)~~ → **`backend: mcp`** ·
  `family: jennifer` · `capabilities: [was_metric, jvm_heap, thread_pool, transaction, apm_event]` · `requires: [host_location]`.
  `families`에 `jennifer`(product_terms `["제니퍼","jennifer","APM"]`). ~~`databases`에 `jennifer_export`~~ — 등록하지 않는다.
- **`rest`가 아니라 `mcp`인 이유**: 본체가 제니퍼 REST를 직접 부르면 토큰·허용목록·마스킹이 `src/`에도 생긴다(D-119 경계
  이중화). 그룹 실행자는 `mcp_server`의 `apm_*` 도구를 호출해 결과를 받는다 — 자격증명·통제는 `mcp_server` 한 곳.
  레지스트리 주석의 원래 값 `rest`는 J5 착수 시 이 근거로 `mcp`로 고친다(Plan 82 Wave 7 「`backend: rest` 그룹 실행자 훅」의
  **`mcp` 변형** — 그쪽 착수 시 접점 통보).
- ~~`config/db_profiles/jennifer_export.yaml`·`knowledge/jennifer_export/`·`synonym_seeds/jennifer_export.yaml` 신설 · 일자별
  `TRANSACTION_{domain}_YYYYMMDD` 결정적 조립~~ — **v2 삭제**(SQL 경로 철회). 질의 → 도구 인자(hostname·구간·지표)
  매핑은 **결정적 조립**으로 두고 LLM이 벤더 경로·필드명을 만들지 않는다(Known Mistakes "고정 스키마는 코드가 조립" 준용).
- **v2 한계(§0.4 비용 ①)**: 자유 SQL 집계("분기 WAS별 일평균")는 답하지 못한다 — `/api/dbmetrics/*` 보존 기간·
  `interval_minute` 안의 **지표 조회형 질의만** 받고, 밖은 부분 응답 + 사유(Plan 82 Wave 7 「미제공 능력의 부분 응답」).
- ※ `plans/90` §9(A3)가 우려한 "`jennifer_export`가 db_id로 `previous_db_ids`에 섞이는" 문제는 DB 등록 철회로 **소멸**한다.
- 라우팅: 실행 그룹이 `apm` 솔루션을 `order: 20`으로 폴스타 뒤에 순차 실행(Plan 82 D-176). "김포 WAS 응답시간"
  → `host_location`(폴스타) 선행 → `apm` 조회. `fault_diagnosis` 의도는 변경 없음(대상 확정 후 조사 위임).
- UI: 응답·알람 카드에 소스 배지 "제니퍼"(기존 `source_label` 경로).

### 5.7 대응·복구 — 자율성 사다리와 L2 실행 경계 (G6 · **G-6 게이트**)

| 단 | 이름 | 할 수 있는 것 | 상태 |
|---|---|---|---|
| L0 | 관측 | 조회·브리핑 | 현행 |
| L1 | 권고(advised) | 시그니처→조치 표에서 후보 제시(위험도·신뢰도·검증법·롤백) | 현행(`remediation_recommender_enabled`) — 본 계획이 WAS 항목 추가 |
| **L2** | **승인 후 실행(approval-based)** | 운영자가 제안 id를 승인하면 **결정적 실행기**가 카탈로그 조치를 트랜잭션으로 수행 | **본 계획이 여는 유일한 단** — ~~D-003 예외~~ **[v3.3] D-003 범위 밖 판정**·G-6 확정 뒤 → **[v3.1] ✅ G-6 확정(2026-09-29) · D-195 ③ 본문 등재** · 착수 = J3 완료 + 목업 검증 뒤 |
| L3 | 자율(autonomous) | 승인 없이 실행 | **범위 밖**(Plan 78 §8.3) |

**L2 설계 — Plan 78 §8.3 5조건에 대한 응답**

| 조건 | 설계 |
|---|---|
| ① (Plan 78 조건명) D-003 명시적 예외 | ~~**D-195 ③**로 신설 — 예외 범위는 "카탈로그에 등재된 조치 · 승인된 제안 id · 대상 1 인스턴스"로 한정. 읽기 전용 원칙은 조사 평면에서 **불변**, 실행 평면만 예외~~ **[v3.3] D-003 범위 밖으로 판정 — Plan 78 §8.3 조건 ①에 대한 응답**(사용자 확정 "권고" 2026-09-29). D-003은 DB 접근(질의·조사 평면)의 읽기 전용 결정이고 세 방어선이 모두 SQL/DB에 관한 것이다. L2 조치(덤프 채취 · PLC 상한 조정 · 인스턴스 재기동)는 WAS 조치이고 DB 쓰기가 없으므로 D-003의 **범위 밖**이며 **예외가 아니다**. 실행 평면은 **D-195 ③**이 별도 통제한다 — 카탈로그 조치 · 승인된 제안 id · 대상 1 인스턴스 · 고위험 이중 승인 · 정책 파일 · 트랜잭션 검증-롤백 · LLM 미탑재 실행기 · L3 범위 밖 · D-189와 별개 경계. 조사 평면 읽기 전용은 **불변** |
| ② 승인 UX·주체·권한 | 본체 어드민(운영자 role)에 **승인 대기함**: 제안(근거 인용·위험도·검증법·롤백·blast radius) 표시 → 승인/기각 · 고위험은 **이중 승인**(운영자 + 관리자). JWT `type`·role 명시 검증(보안 원칙) |
| ③ blast radius·롤백·policy-as-code | 정책 파일 `config/remediation_policy.yaml`: 조치별 허용 여부·최대 범위(인스턴스 1·사건당 1회·쿨다운 30분·동시 1)·롤백 절차·금지 시간대. 실행기는 정책 밖 요청을 **거부**한다 |
| ④ 감사·사후 검증 | 트랜잭션(P11): 사전 스냅샷(`apm_app_health`·`apm_runtime_health`) → 조치 → **검증**(N분 뒤 재조회, 회복 기준 = 정책 파일) → 실패 시 롤백 → 결과 브리핑. 전 단계 감사(누가·무엇을·언제·근거·결과) |
| ⑤ 적응형 공격 평가 | 실행기는 **LLM 미탑재**(P13)이므로 인젝션이 성공해도 실행될 명령이 카탈로그 밖에 없다. 그래도 J6 수용 기준에 **적응형 공격 시나리오**(도구 출력에 지시문 주입 → 제안 내용 변조 시도)를 넣어 "제안 변조가 실행에 도달하지 않음"을 측정한다 |

**실행기 경계** — `remediation_executor`는 어느 패키지인가: `sre_agent`(조사)와 **분리**한다(권한 분리 · 조사
프로세스에 실행 자격증명을 두지 않음). 후보: ① `noise_gate/` 하위(승인 대기함·감사가 본체 인증 계층에 있음)
② 신규 최상위 패키지 `remediation/`(D-139 — 자체 tests·scripts). **권고 ②** — 실행 자격증명(WAS 관리 API·
SSH 키·LB API)은 독립 프로세스·독립 venv에 두어 본체·조사와 격리한다. 통신은 MCP 계약뿐(D-139 준용). **G-7**.

**초기 카탈로그(P14 — 작게)**: ① 힙/스레드 덤프 채취(읽기성, 위험 low) ② PLC 상한 임시 하향·원복(가역, medium)
③ 인스턴스 재기동(high · 이중 승인 · 단일 인스턴스 · 다중 인스턴스 호스트에서 최소 1개 가용 확인 후). 재기동
실행 채널은 WAS 관리 API 또는 허용목록 스크립트이며 **D-189의 read-only allowlist와 별개 경계**다(섞지 않는다).
**[v3.2 — 실행 채널 · G-6 확정 내용은 그대로]** 공개 자료에 스레드 덤프·서비스 덤프·PLC·인터럽트 API가 **0건**이다(확인 불가 [J-23] #43 · U-8). 그래서
①·②의 실행 채널은 **J0에서 확인**하고(벤더 문의 + J0-L 설치본 콘솔), 제니퍼 API가 없으면 **허용목록 스크립트**로 한다 — ① 덤프 = JDK 표준 `jcmd <pid>
Thread.print`·`GC.heap_dump`[W-8](대상 1 인스턴스) · ② PLC = 에이전트 설정(`set_limit_active_service`·`max_num_of_active_service`[J-3]) 반영 스크립트(반영에
재기동이 필요한지는 J0 확인 — 필요하면 위험도가 재기동과 같아진다). 어느 쪽이든 재기동과 같은 **별도 실행 경계**(`remediation/`)이고 D-189 허용목록과 섞지 않는다.
EVENT 룰 API(on/off·대상별 설정)는 조치 카탈로그가 아니다(관측 설정 변경 — 허용목록 밖).

### 5.8 관측·감사·설정

- 모든 `apm_*` 호출은 `mcp_server` 감사(도구·인자·행수·소요·source_kind)에 남고 `sql_log`와 같은 형식이다.
  **[v2.2 정정]** `sql_log`는 SQL 전용이다(D-140). HTTP 도구의 감사 선례는 PromQL `_audit`(`promql_tools.py:156-172` — logger 1줄: 도구·질의·
  소요·행수·오류)이므로 `apm_*`도 그 형태로 남긴다(경로·파라미터는 마스킹본). 본체 쪽 조회 감사는 2단 서브에이전트 감사
  (`audited_investigation` — `plans/121` TP-1.13, 작업 트리)가 맡는다.
- **[v3]** 제니퍼는 패키지 배포 자체가 스위치다(배포하지 않으면 현행과 비트 동일). 게이트웨이 안에서는 원시 API 도구·J7·폴러를 개별 스위치로 둔다(§7 v3 키 표).
  소비자 쪽 스위치(`sre_agent` 지침·시그니처 승격 · `noise_gate` `app_impact` · 본체 `apm` 등록)는 그대로 기본 off다. 감사는 게이트웨이와 `mcp_server`에
  나뉘므로 **같은 형식**을 쓰고 조사 id·thread_id를 호출 인자로 전파한다(R-19).
- 플래그(전부 **기본 off** — 현행 비트 동일): ~~`mcp_server`~~ **[v3] `apm_gateway`** `expose_apm_tools=false`·`expose_raw_api=false` /
  `sre_agent` `apm_guidance_enabled=false`·`apm_signatures_enabled=false` / `noise_gate` `jennifer_events_enabled=false`·
  `app_impact_enabled=false` / 본체 `db_registry` `apm` 솔루션 `enabled`(등록 자체) / `remediation_executor_enabled=false`.
- 기동 시 1회 해석(플래그 원칙). 기동 로그 1줄로 "APM 소스 등록 여부·정합 파일 로드 건수"를 남긴다.
- (v2.1) `mcp_server` `expose_apm_openmetrics=false`(J7 · §5.9)도 같은 원칙.

### 5.9 표준 연동 규격(OpenMetrics) 노출 — 선택 트랙 J7 (v2.1 신규 · G-2 확정 · **G-9**)

**왜 선택 트랙인가**: 제니퍼는 OpenMetrics를 내지 않고(§0.4 O-3), 진단 핵심 증거는 규격 밖이다(O-2). 사용자 지시가
"API 위주"이므로 조사·질의 경로는 J1~J5(API)가 정본이고, J7은 **같은 API 결과를 표준 규격으로 외부에 내보내는** 추가
출구다 — Prometheus·타 수집기·대시보드가 제니퍼 지표를 **벤더 API 없이** 표준 형식으로 가져가게 한다.

- **위치**: `mcp_server` `custom_route("/metrics/apm", methods=["GET"])` — `plans/92` 트랙 B-2(`/metrics` 폴스타 브리지)와
  **직렬화기·인증·캐시 기계를 공유**하고 경로만 분리한다(스크레이프 잡·캐시 TTL·부하 상한을 소스별로 따로 둔다).
  **[v3 — 노출 주체는 게이트웨이]** `GET /metrics/apm`은 `mcp_server`가 아니라 **`apm_gateway/interface/`**가 낸다. 게이트웨이는 경계 불변식상
  `mcp_server`를 import할 수 없으므로 아래 공유 기계(`om_exposition.py` 321행)는 **복제하거나 벤더 중립 공용 모듈로 추출**해야 한다 — **G-12**(J7 착수 시
  결정). R-17(2.0 협상) 수정은 어느 쪽이든 적용한다. 인증은 게이트웨이 자체 Bearer다. `nodename` 역해소는 게이트웨이 → `mcp_server` MCP 호출(§0.7 (7)).
  아래 v2.2 문단은 기계의 동작 설명으로 유효하다.
  **[v2.2 실측 — 공유 기계는 이미 있다]** `mcp_server/mcp_server/om_exposition.py`가 구현돼 있다. 패밀리 헬퍼(`gauge_family`·`info_family`·
  `flag_gauge`), `render_exposition`(Accept 협상), `ExpositionCache`(TTL + single-flight), `make_exposition_endpoint`(수집 예외 → 503),
  종료 정리 배선을 갖춘다. 폴스타 브리지 `polestar_exporter.py`(`ROUTE_PATH="/metrics"` `:74` · 등록 `:518-538`)가 첫 사용자다. J7은
  `apm_openmetrics.py`에 **수집 함수와 패밀리 정의만** 쓰면 된다. 다만 두 가지를 착수 조건으로 둔다.
  ① **버전 상한(⚠ R-17)** — 공유 `render_exposition`은 `Accept: application/openmetrics-text; version=2.0.0` 요청에
  `Content-Type: …version=2.0.0`으로 응답한다(prometheus-client 0.26.0 실측 · §0.6 #15). G-2의 "1.0 고정"을 지키려면 공유 기계의 협상을
  1.0 이하로 묶어야 한다. 이 코드는 `plans/92` 소유이므로 **92 소유자에게 수정을 요청**하고(§10 G-10), J7 수용 기준에 "`version=2.0.0` 요청에도
  1.0 이하로 응답"을 넣는다. 빈 Accept·`*/*`는 text 0.0.4로 협상되므로 "OpenMetrics 1.0 파서로 파싱" 단언은 **1.0을 요청한 응답**에 건다.
  ② **실패 표현** — 공유 엔드포인트는 수집 함수가 예외를 올리면 503을 낸다. 아래 "Open API 장애 시 `jennifer_bridge_up 0`" 계약을 지키려면
  수집 함수가 Open API 오류를 잡아 상태 gauge로 바꿔야 한다(예외를 올리지 않는다).
- **형식**: **OpenMetrics 1.0 고정** — `application/openmetrics-text; version=1.0.0; charset=utf-8` · `# TYPE`·`# UNIT`·`# HELP` ·
  `# EOF` · **타임스탬프 미노출**(*"MetricPoint timestamps should not be exposed"*[OM-1]). 2.0은 Experimental이라 제외(O-1).
- **내는 것(1차 · 인스턴스 단위 수치만 · 이름은 잠정 — U-2·U-12 recorded JSON으로 단위 확정 후 고정)**:

  | MetricFamily | 타입·UNIT | 원천(`/api/realtime/instance` 필드[J-4]) | 비고 |
  |---|---|---|---|
  | `jennifer_instance_info` | info | 도메인·인스턴스 목록 + `apm_instance_map` | 라벨 `nodename`·`jennifer_domain_id`·`jennifer_instance_id`·`jennifer_instance_name`·`match_confidence` |
  | `jennifer_active_services` | gauge | `activeService` | |
  | `jennifer_transactions_per_second` | gauge | `tps` | 비율이라 counter(`_total`) 아님 |
  | `jennifer_response_time_seconds` | gauge · `seconds` | `responseTime` | ms→초 변환(단위 ~~△ U-12~~ **[v3.2] ✔ 스펙 명시 ms**) · 단위 접미사 필수[OM-1] |
  | `jennifer_concurrent_users` | gauge | `concurrentUser` | |
  | `jvm_memory_used_bytes` / `jvm_memory_committed_bytes` | gauge · `bytes` | `heapUsed` / `heapCommitted` | 라벨 `jvm_memory_type="heap"` — OTel `jvm.memory.used`의 Prometheus 명명 변환 규칙(점→`_` · 단위 접미사 · UNIT 메타)[OM-3] · **[v3.2]** `heapUsed`·`heapCommitted`는 **MB** — `_bytes`로 내려면 **×1,048,576 변환 필수**(2²⁰ 추정 · 10⁶/2²⁰ 여부는 U-12 recorded JSON과 콘솔 값 대조로 확정) · 단위 접미사 규칙[OM-1] |
  | `jennifer_bridge_up` | gauge | Open API 응답 성공 여부 | 1/0 — 브리지 자체 상태(침묵 실패 금지) |
  | `jennifer_bridge_truncated` | gauge | 인스턴스 상한 초과 여부 | 잘라낸 사실을 노출(`plans/92` B-2 동형) |

- **라벨 규약**: `nodename` = 폴스타 hostname(`apm_instance_map` 해소 · D-119 규약 — node_exporter·폴스타 브리지와 **같은 키로
  조인**) · **미정합 인스턴스는 `nodename` 없이 내지 않는다**(info의 `match_confidence="none"`로만 표시 — 잘못된 조인 방지).
  트랜잭션명·URL·SQL·txid·client IP는 **라벨 금지**(카디널리티·PII — §8.3).
  **[정정 2026-09-22 · `plans/92` v3 §0.0.4]** `nodename`의 값은 폴스타 **`server_name`**이다(OS hostname이 아니다).
  D-119 ③은 도구 인자 이름이 `hostname`일 뿐 그 값을 "`hostname(=server_name)`"로 규정한다. PromQL 도구(`promql_tools.py:447`)와
  `plans/92` B-2 폴스타 브리지도 `nodename = server_name`이다. 반면 §5.3의 `apm_instance_map`은 인스턴스를 **OS hostname**
  (`was_object.hostname` · 정합 파일 `hostname`)으로 해소한다. 공동존은 name≠hostname이다(D-046).
  따라서 J7은 해소한 OS hostname을 폴스타 `cmm_resource`(`server.Server` · `dtime IS NULL`)의 `name`으로 **한 번 더 결정적으로 역해소**해 `nodename`에 넣는다.
  이것은 D-046 해소기의 역방향이다. 역해소가 0건이나 다건이면 위 규칙대로 `nodename` 없이 `match_confidence="none"`으로만 낸다.
  은행존처럼 name = hostname인 존에서는 이 단계가 항등이다. 조사 경로(§5.3 · `REQUIRED_EVENT_FIELDS`)의 hostname 해소는 그대로 둔다.
  이 정정은 J7 노출 라벨에만 적용된다.
- **[부기 2026-09-22 · `plans/92` v3 §4.5 착수 조건 1]** `custom_route` 핸들러는 `mcp_server` lifespan 컨텍스트에 닿지 않는다.
  lifespan은 SSE 세션마다 열리고, 폴스타 DB 풀·설정은 도구의 `ctx.request_context.lifespan_context`에만 있기 때문이다.
  J7이 쓰는 폴스타 SQL(`was_object` 1순위 브릿지 · 위 역해소)과 Open API 클라이언트 설정도 B-2와 같은 방식으로 얻는다 —
  **브리지 전용 지연 자원**(첫 스크레이프 때 생성)이다. 공용 노출 모듈은 92 v3의 `om_exposition.py` 제안과 같은 것이다(아래 overfit 항목의 "공용 직렬화기").
- **내지 않는 것**: 이벤트·액티브 서비스 목록·X-View·프로파일·SQL 통계(규격 밖 O-2 또는 고카디널리티) → `apm_*` 도구(API)로만.
- **부하 가드**: 스크레이프마다 Open API를 치지 않는다 — 응답 캐시 TTL(기본 60s) · 스크레이프당 호출 = 도메인당 1회 ·
  인스턴스 상한 · **토큰 사용량 제한(§8.4)을 조사 경로와 공유**하므로 J7 전용 호출 예산을 둔다.
- **인증**: `mcp_server` Bearer 미들웨어를 그대로 통과(`plans/92` §4.5와 동일) — 무인증 노출 금지.
- **소비 측 선택지(우리 경로)**: 조사·질의는 `apm_*`가 정본이다. 운영 Prometheus가 이 출구를 스크레이프하면 기존 `prom_*`
  도구로도 추세를 볼 수 있으나, **같은 지표를 두 경로로 LLM에 주지 않는다**(소스 혼동 — D-119 대안 기각 사유 "A+B 병행"과 같은 이유).
- **overfit**: 벤더 리터럴(`jennifer_*`)은 `mcp_server/mcp_server/apm_openmetrics.py`에 격리하고 공용 직렬화기는 벤더 무지로 둔다.

---

## 6. 구현 계획 — Wave J0 ~ J6 (+ v2.1 선택 J7)

> 착수 판정은 `선행` 완료 + `게이트` 해제인 Wave만. 과금 API(Gemini 등) 실 호출이 필요한 검증은 **D-127 건별 승인**.
> 착수 시 최근 작업 단위 양식을 따른다 — 계획서 → `CAPABILITY-MAP-87.md` → 모듈별 `SPEC-*.md` →
> `tasks/plan-87.md`·`tasks/todo-87.md`(`tasks/`에 plan/todo 쌍 14개 전례). 각 Wave 착수 직전 §3의 `file:line`을 재실측한다.
> **[v2.2]** ① 맵·SPEC 위치는 **`spec/CAPABILITY-MAP-87.md`·`spec/SPEC-*.md`**다(D-244 — 루트 생성 금지). ② 재실측 기준은 §0.6 표다.
> ③ 실 LLM이 필요한 검증(J3 목업 시나리오의 조사 완주 등)은 **로컬 MLX**로 한다(D-240 — 두 평면이 `mlx` 루프백이면 승인 없이 · pytest는
> `RUN_LOCAL_LLM=1`). 조사 LLM은 OpenAI 호환 3필드로 MLX를 가리킨다(D-229). MLX 결과는 로직 확인용이고 지연 결론은 내부망 결과로만 낸다.
> ④ 사용자·관리자 기능(J4 알람 소스 배지 · J5 채팅 APM 답변·배지 · J6 승인 대기함)은 **같은 Wave에서 매뉴얼을 갱신**한다(D-255).
> ⑤ 운영자 절차는 **`docs/31_jennifer_integration_guide.md`**에 모은다. 각 Wave가 끝나면 가이드의 「구현 후 절차」 절을 「현재 가능」으로 옮긴다.

| Wave | 내용 | 산출물 | 선행 | 게이트 |
|---|---|---|---|---|
| **J0** 선행 실측 **[v3.1] → J0-L(로컬 Docker) · J0-O(운영) 분할 — §0.8** | §2.6 U-1~U-12 실측 · 인스턴스↔hostname 일치율 · ~~DDL 채집~~ **v1 엔드포인트 recorded JSON 채집**(v2) · 토큰 권한·사용량 제한 · PII 샘플 · ~~공식 MCP `tools/list`(U-11)~~(v2.1 판정 완료 — §0.5) | ~~`docs/29_jennifer_integration_survey.md`~~ **[v2.2] J0 실측 보고(번호는 착수 시 재실측 — `docs/29`는 선점됨, `docs/31`은 연동 가이드)** · `testdata/jennifer/`(마스킹 샘플·recorded JSON) · 가이드 `docs/31` §3·§11 갱신 | 제니퍼 접근 권한 · 테스트 토큰 | ~~G-1·G-2·G-8~~ **v2.1 확정 완료** — 외부 전제(접근 권한)만 남음 |
| **J1** 데이터 평면 기반(API) — v2 재정의(구 "SQL") | `JenniferApiConfig` · `apm_client.py`(Open API 호출 — v2.1: 백엔드 인터페이스 없음 §5.2(d)) · **GET·경로 허용목록**(코드 상수) · 레이트 리밋·토큰 마스킹 · `apm_instance_map`(정합) · 기동 로그 1줄(버전·허용 경로 수) | `apm_client.py`(httpx) · `config/apm_instance_map.yaml` · 테스트(허용목록 밖 경로·비GET **100% 거부** · recorded JSON 계약 · ~~`respx`~~ **[v2.2] `httpx.MockTransport`**(respx 미설치 · 기존 HTTP 도구 테스트 전례)) · `pyproject` httpx 선언 · **[v2.2]** `mcp_server/.env.example` 신규 키 동시 등재(`test_env_example_coverage.py`) · 벤더 모듈 overfit 제외 등재(`scripts/overfit_check.py:71-76` 전례) | J0(U-4·U-5·U-12) | — |
| **J2** 도구 표면(API) | `apm_*` 8종 — 실시간·**구간(`/api/dbmetrics/*`)**·액티브 서비스·트랜잭션(1분 창 분할)·이벤트 · 상위 N · 마스킹 | `apm_tools.py` · 계약 테스트(recorded JSON → `apm_*` 반환 · §5.2(d)) | J1 · J0(U-2·U-6) | **G-3** |
| **J3** 진단 확대 | 지침·플레이북·WAS 시그니처 8종·권고 표·브리핑 소스 라벨·프로파일 2단계 폴백 | `investigation_guidance.py`·`severity_signatures.py`·`remediation.py`·`briefing_builder.py` 변경 · `config/apm_playbooks.yaml`·`apm_signatures.yaml` · 목업 WAS 시나리오 6종(Plan 65 확장) | J2 | — |
| **J4** 이벤트 편입 | 1단계 폴링 수신기(경계 준수안) · 정규화 · `apm_event_levels.yaml` · `app_impact` 승격 축 · 트리거 힌트 · 2단계 어댑터 push(**2-A SNMP trap 수신기 또는 2-B 커스텀 어댑터** — 별건 착수) | `noise_gate/alarm_server/jennifer_poll_receiver.py` · `noise_gate/domain/apm_event.py`(순수 정규화 — 폴링·SNMP·TCP 세 입력 공통) · (2-A) `snmp_trap_receiver.py` · 게이트 테스트(플래그 off 비트 동일) | J2·J3 | **G-4** |
| **J5** 질의 경로 | `db_registry` `apm`(**`backend: mcp`** — v2)·`jennifer` family · ~~`jennifer_export` DB · 지식·유사어 시드~~(v2 삭제) · 라우팅 골든셋 추가 · 미제공 능력 부분 응답 · UI 배지 | 설정 파일 · `testdata/routing_gold` 추가 | J2 · ~~Plan 82 Wave 7(그룹 실행자 훅 — `mcp` 변형)~~ **[v2.2] `plans/121` TP-9.1·9.2·10.5**(D-270 ⑯ — 82 Wave 7 디스패치 흡수) | **G-5** |
| **J6** 대응·복구 L2 | 승인 대기함 UI·API · `remediation_policy.yaml` · 실행기 패키지(트랜잭션·검증·롤백·감사) · 카탈로그 3종 · 적응형 공격 시나리오 | 신규 패키지 `remediation/`(권고) · 본체 어드민 라우트 · 테스트(정책 거부·롤백·이중 승인·"제안 변조 → 실행 불가") | J3 | **G-6·G-7 + D-195 ③ 등재** |
| **J7** 표준 노출(선택 · v2.1) | `GET /metrics/apm` OpenMetrics 1.0 브리지(§5.9) — 인스턴스 수치 지표 8패밀리 · `nodename` 라벨 규약 · 캐시·호출 예산 · 브리지 상태 게이지 | `mcp_server/mcp_server/apm_openmetrics.py`(벤더 매핑) · 공용 직렬화기(`plans/92` B-2와 공유) · 테스트(1.0 형식 검증 — `# EOF`·`_total`·단위 접미사·타임스탬프 부재 · 미정합 인스턴스 `nodename` 미노출 · 라벨 금지 목록 · 플래그 off 라우트 404) | J2 · ~~(공유 시) `plans/92` O-B2~~ **[v2.2] 공유 기계 구현 완료(`om_exposition.py`) · 버전 상한 수정(92 소유 — R-17)** | **G-9 · G-10** |

```
(v2)
J0 ──► J1 ──► J2 ──┬──► J3 ──► J4 ──► (J6)
                   ├──────────────► J5 ◄── Plan 82 Wave 7
                   └──────────────► (J7) ◄┄ plans/92 B-2 직렬화기(공유 시)
```
(v1.1은 J1(SQL)·J2(API)가 병렬이었고 J5가 J1에 매달렸다. v2는 API 단일 경로라 기반(J1)→표면(J2) 직렬이다.)

```
(v2.2)
J0 ──► J1 ──► J2 ──┬──► J3 ──► J4 ──► (J6)
                   ├──────────────► J5 ◄── plans/121 TP-9.1·9.2·10.5 (82 Wave 7 흡수 · D-270 ⑯)
                   └──────────────► (J7) ◄── om_exposition.py (구현 완료) + 버전 상한 수정(92 소유 · R-17)
```

```
(v3 · D-274 — 모든 제니퍼 코드는 apm_gateway/ 안에서, 소비자 쪽 변경만 각 패키지에서)
J0 ──► J1(패키지 골격·MCP 서버·Bearer·감사·경계 불변식·Open API 클라이언트·허용목록·정합)
         └─► J2(apm_* 8종·WAS 판정) ──┬──► J3(sre_agent 두 번째 MCP 서버·지침) ──► J4(게이트웨이 폴러 + noise_gate 소비측) ──► (J6)
                                      ├────────────────────────────► J5 ◄── plans/121 TP-9.1·9.2·10.5(두 번째 MCP 엔드포인트)
                                      └────────────────────────────► (J7 게이트웨이 /metrics/apm) ◄── G-12 직렬화기 · R-17
```

```
(v3.1 · J0 분할 — §0.8)
J0-L(로컬 Docker 또는 목 서버 · 외부 전제 없음) ──► J1 ──► J2 ──┬──► J3(목업 = 로컬 Docker 재현) ──► J4 ──► J6(G-6 확정 · J3·목업 검증 뒤)
                                                                ├──► J5 ◄── plans/121 TP-9.1·9.2·10.5
                                                                └──► (J7) ◄── 소비자 확정(G-9)
J0-O(운영 실측 · 외부 전제) ──────► 정합 파일 확정(U-4·U-10) · 운영 계약 픽스처 교체 ──► 운영 배포(J1~J4 운영 투입 전 필수)
```

> **[v3.2]** J0-L = **J0-L-a**(라이선스 없이 — Dockerfile·compose·샘플 앱·녹화 하네스·목 서버 · 기동 확인) → **J0-L-b**(평가판 2주 창 — recorded JSON 채집·허용목록 실측·이벤트 재현).
> J1 골격은 J0-L-a 뒤 착수할 수 있고, 계약 테스트 픽스처는 J0-L-b 채집분(없으면 목 서버)을 쓴다(§0.8 (9)).

**[v3 Wave 재정의]** — 위 표의 "산출물" 칸 중 위치가 바뀐 것을 이 표가 대체한다(목표·게이트는 그대로).

| Wave | v3 내용 | 주 산출물(「예정 이름」) |
|---|---|---|
| **J0-L**(v3.1) | 로컬 Docker 제니퍼(또는 목 Open API 서버 — 폴백) · U-2·U-5 해소(**[v3.3] J0-L-a 부분 해소 · 잔여 J0-L-b — §0.10**) · U-8 확인(공개 API 0건 — [J-23] #43) · U-1·U-3·U-6·U-12·U-13·U-14 로컬분 · recorded JSON 1차 채집(출처 `local-docker`/`mock`) · 허용목록 검증(쓰기·제어 API 전수 실측 — **로컬에서만**) · 이벤트 발생 재현. **외부 전제 없음**(J0-L-a — 파일 작성 · 기동 확인에만 설치본·Docker 자원 필요) → **J0-L-b는 평가판 2주 창**(사용자 할 일 §0.8 (10) · 체크리스트 §0.8 (9)) | `apm_gateway/testdata/jennifer/`(compose 127.0.0.1 · 에이전트 설정 · 샘플 앱 · 재현 스크립트 · 목 서버 · README) — **패키지 코드보다 먼저** |
| **J0-O**(v3.1) | 운영 실측 — U-4·U-10(정합) · U-12(운영 버전·v1 차이) · U-5(토큰 정책) · U-6(PII 실 샘플) · U-13(운영 커스텀 룰) · U-7(부하 상한) | 운영 채집분(`ops-masked`) · J0 실측 보고(번호 착수 시 재실측) — **외부 전제: 제니퍼 운영 접근 권한·테스트 토큰** |
| **J1** | **`apm_gateway/` 패키지 골격**(자체 `pyproject` · 자체 cwd · 2단 중첩) · **자체 MCP 서버**(SSE) · Bearer(`mcp_server/mcp_server/server.py:31` 복제) · 감사 형식(PromQL `_audit` 전례) · **경계 불변식 테스트**(양방향 import 0 — `sre_agent/tests/test_boundary.py` 방식) · `adapters/jennifer/` Open API 클라이언트 · GET·경로 허용목록 · 레이트 리밋 · 정합(`was_object`는 `mcp_server` MCP 호출 · 필요 시 `mcp_server`에 폴스타 고수준 도구 1종 추가) · **`CLAUDE.md` 「저장소 지도」·「패키지 경계」 표 갱신** | `apm_gateway/` 전체 골격 · `tests/test_boundary.py` · `testdata/`(recorded JSON) |
| **J2** | `apm_*` 8종을 `apm_gateway/interface/`에 · WAS 시그니처 결정적 판정(`domain/`)과 판정 결과 필드(`was_signals`) | `interface/`·`domain/` · 계약 테스트 |
| **J3** | `sre_agent`: `_build_mcp_servers()`에 `"apm"` + `AgentSettings.apm_mcp_url`·`apm_mcp_token` · 지침·앵커·플레이북·권고 표 · 게이트웨이 판정 결과를 `Signal`로 **승격만**(WAS 규칙 재구현 금지) | `sre_agent` 변경분 |
| **J4** | 게이트웨이: 이벤트 폴러·정규화(`alarm:raw` XADD · 커서 멱등) · `noise_gate`: kind apm 선판정(R-16) · family 배지 · `app_impact` 승격(게이트웨이 MCP 클라이언트 — `sre_agent_client.py` 전례) | `apm_gateway/application/`·`domain/` · `noise_gate` 소비측 |
| **J5** | `plans/121` 처리기의 두 번째 MCP 엔드포인트 · 레지스트리 `apm` 등재 · `allowed_sources` | `src/` · `config/db_registry.yaml` |
| **J6** | 변경 없음 — `remediation/`은 게이트웨이 밖(읽기 전용 · 실행 자격증명 분리) | — |
| **J7** | 게이트웨이 `GET /metrics/apm` · 직렬화기 복제 또는 추출(G-12) · 버전 상한(R-17) | `apm_gateway/interface/` |

**수용 기준(요지)**
- J1·J2: `apm_*` 반환 계약 테스트 고정 · 미매칭 시 `error: instance_unresolved` · 1분 창 분할 상한 · 마스킹 검증
  (`pii_regex_check`) · 플래그 off 시 도구 미등록(비트 동일) · `mcp_server/tests` 무회귀 · **(v2) 허용목록 밖 경로·
  비GET 메서드 요청 100% 거부(`/api-v2/manage/*` 표본 포함) · 토큰이 로그·오류 메시지에 0회 노출**.
- J3: 목업 WAS 시나리오 6종(큐잉·DB 풀·GC stall·힙·슬로우 SQL·외부 지연)에서 **시그니처 판정 정확·권고 후보
  정확**(결정적 — LLM 미사용 테스트) · 브리핑에 APM 증거가 인용됨으로 판정 · APM 미가용 시 폴백 사유 노출.
  RCAEval 축(원인 지표·유형) 기준으로 골든 6/6. 골든셋 정답 일부는 **비공개**로 관리한다(OpsEval 방식 — 프롬프트
  과적합 방지). 반증·정체 가드(P15)는 "반복 호출 3회 → 미결 종료" 테스트로 고정한다.
- J4: 폴링 중복 0(커서) · 정규화 순수 함수 테스트 · `app_impact`가 **승격만** 하는 비대칭 테스트 · 심각도 3 불변 ·
  플래그 off 비트 동일(`test_plan60_flags_off_regression.py` 섹션 추가).
- J5: 라우팅 골든셋 회귀 0 · `apm` 등록 시 실행 그룹 순서(폴스타 → apm) · ~~`catalog_diff` 동등성~~(v2: DB 프로필 없음) ·
  보존 기간 밖·자유 집계 질의에 **부분 응답 + 사유**(침묵 0건) · 본체 `src/`에 제니퍼 토큰·URL 참조 0건(grep).
- J6(P10): 목업 시나리오에서 "승인 → 실행 → 검증 회복"과 "검증 실패 → 롤백 → 에스컬레이션" 둘 다 완주 ·
  정책 밖 요청 100% 거부 · 제안 변조 시나리오에서 실행 도달 0 · 감사 레코드 완전성.
- J7(v2.1): 노출 텍스트가 **OpenMetrics 1.0 파서로 무오류 파싱**(`prometheus_client.openmetrics.parser` 또는 `promtool check metrics`
  중 착수 시 실측해 택1) · `# EOF` 종결·단위 접미사·타임스탬프 부재 단언 · Open API 장애 시 `jennifer_bridge_up 0`(빈 응답·500 아님) ·
  캐시 TTL 안의 반복 스크레이프에서 Open API 호출 증가 0 · 라벨에 금지 키(트랜잭션명·URL·SQL·txid·IP) 0건.
- **[v2.2 추가 기준]**
  - J1: 신규 env 키가 `mcp_server/.env.example`에 있다(`test_env_example_coverage.py` 통과) · 벤더 모듈이 overfit 제외 목록에 있고
    `python scripts/overfit_check.py --ci` 신규 유입 0 · HTTP 목킹은 `httpx.MockTransport` · 리다이렉트 비추종(`follow_redirects=False`)과
    응답 크기 상한(`openmetrics_tools.py` I-2 전례) 단언.
  - J3: 목업 시나리오의 실 LLM 완주는 로컬 MLX로(D-240) · 제니퍼 이벤트 `errorType` 표본(§2.3 전 유형)이 **OS kind로 분류되지 않음**을
    게이트·조사 두 분류기에서 단언(R-16) · APM 미가용 폴백 문구가 "폴스타 MCP 도구로 대체"(D-233 반영).
  - J4: 제니퍼 이벤트의 소스 배지가 "제니퍼"로 나온다(db_id 아닌 family 해석) · 알람 화면이 바뀌면 매뉴얼 갱신(D-255).
  - J5: `plans/121` 처리기 계약 준수 · `allowed_sources` 권한 밖 사용자에게 APM 비노출 · 등록 0건 단언 테스트 2건 갱신 ·
    사용자 매뉴얼 갱신(D-255) · 2단 기준(D-251)에서 골든 회귀 0.
  - J6: 승인 대기함 화면은 관리자 매뉴얼 동반(D-255).
  - J7: `Accept: …version=2.0.0` 요청에도 1.0 이하로 응답(G-2 · R-17) · 1.0 파서 단언은 1.0을 요청한 응답에 건다 · 빈 Accept는 0.0.4.
- **[v3 추가 기준]**
  - J1: **경계 불변식** — `apm_gateway` → `src`·`noise_gate`·`sre_agent`·`mcp_server` import 0, 역방향 0(AST 검사) · 게이트웨이 설정·env에 폴스타 DB
    연결 문자열 0건 · 게이트웨이를 **배포하지 않은 상태**에서 본체·조사·게이트·`mcp_server` 테스트 무회귀(선택 배포) · 토큰이 로그·오류·감사에 0회.
  - J3: `APM_MCP_URL` 미설정이면 `_build_mcp_servers()` 결과가 종전 dict와 같다(비트 동일) · 설정 시 두 서버가 등록된다.
  - J4: 게이트웨이가 낸 `alarm:raw` 레코드가 `REQUIRED_EVENT_FIELDS`·`AlarmEvent` 파싱을 통과(양쪽 계약 픽스처 — import 없이 복제) ·
    `resourceType="apm.Instance"`가 채워진다 · 재기동·중복 조회에도 중복 발행 0.
  - 공통(R-19): 조사 id·thread_id가 게이트웨이 감사 레코드에 실린다(두 프로세스 감사를 합쳐 볼 수 있다).
- **[v3.1 추가 기준 — J0-L]**
  - compose의 모든 포트가 `127.0.0.1`에 바인딩되고 §0.8 (4)의 점유 포트와 충돌 0 · Docker 통합 테스트는 `RUN_DOCKER_IT=1` 없이는 skip ·
    이미지·설치 파일은 스크립트가 자동으로 받지 않는다(사용자 명시 실행) · README에 기동·정지·토큰 발급·이벤트 재현·정리(컨테이너·볼륨 삭제) 절차.
  - recorded JSON에 출처 표지(`local-docker`·`mock`·`ops-masked`) · 쓰기·제어 API 실측 결과(메서드·경로 전수)가 허용목록 차단 테스트의 입력이 된다.
  - 폴백(목 서버)으로 진행했으면 그 사실과 한계(실제 EVENT·필드 변형 미재현)를 J0-L 보고에 적는다.
  - **[v3.2]** 데이터 서버 5000은 호스트에 게시되지 않는다 · 라이선스 IP는 compose 고정 서브넷 IP로 설계돼 있고 벤더 확인 결과를 README에 적는다 ·
    ~~`jennifer_bootstrap_check=false`는 로컬 compose에만 있고~~ **[v3.3]** Bootstrap Check 끄기는 compose `.env`의 `DISABLE_BOOTSTRAP_CHECK=1` 옵트인으로만 있고(기본 0) 운영 설정 예시에는 없다 · 설치본·라이선스 파일이 저장소에 0건(`.gitignore` 패턴 + `git ls-files` 확인) ·
    arm64 네이티브 기동 결과(성공/실패 → amd64)를 기록 · J0-L-b는 §0.8 (9) 체크리스트 완료 표와 만료일을 보고에 적는다.
  - **[v3.3] J0-L-a 충족 현황(§0.10)**: 127.0.0.1 게시(17900·18080) · 5000 비게시 · 설치본·라이선스·녹화 원본은 `.gitignore` 3패턴과 `.env` 패턴으로 제외(`git check-ignore` 실측) ·
    arm64 네이티브 기동 기록 · README(기동·토큰 발급·실측·정지·정리) — 충족. 미충족 = 이벤트 재현 절차(J0-L-b) · ~~목 서버~~(**v3.3 부기 충족** — 녹화 하네스·목 서버 README 절 · 도구 테스트 22건) · Docker 통합 테스트(`RUN_DOCKER_IT=1` — 아직 테스트 없음) ·
    라이선스 IP 벤더 확인 결과의 README 기재.
- **[v3.3 추가 기준 — 목 서버·녹화 하네스 반영]**
  - J1: **카탈로그 사본 ↔ 정본 대조 테스트** — `apm_gateway/testdata/jennifer/scripts/jennifer_catalog.py`(J0 도구용 사본)의 템플릿·필수/선택 파라미터·Accept가 `adapters/jennifer/` `apm_client.py`(정본 · 「예정」) 허용목록과 같다(한쪽만 바뀌면 실패).
  - J1·J2: 게이트웨이 계약 테스트는 목 서버(`mock_openapi.py`)를 상대로 돌리고, 끝에 `GET /__mock/hits`로 **허용목록 밖 호출 0회**(`allowlisted=false` 0건 · `query_token` 0건)를 단언한다 — §5.2(e) 거부 테스트(HTTP 0회)와 짝을 이룬다.
  - J1: `apm_gateway/tests`는 루트 pytest 수집 경로(`pyproject.toml` `testpaths = ["tests", "noise_gate/tests"]`) 밖이다 — 지금은 `.venv/bin/python -m pytest apm_gateway/tests -q`로 명시 실행한다. 루트 수집 편입 여부를 **G-11 판정과 함께** J1 완료 보고에 적는다.
- **[v3.2 추가 기준]**
  - J1: §5.2(e) 허용목록 정본 표 밖의 요청이 **HTTP 0회로 100% 거부**된다 — 거부 테스트 입력은 §5.2(e)의 (a) 비GET 9건 · (b) 민감 GET 9건 · (c) 변형·우회 5건 전부 · 허용 경로의 v1 POST 변형도 거부 · 쿼리 `token` 키 거부.
  - J2: `apm_transaction_profile`은 `profile_ref` 없이 호출하면 인자 오류 · `domain_id` 불일치는 `profile_ref_mismatch` · `apm_slow_transactions`의 10분 초과 사건창은 `[한계]`에 "시 단위 통계" 표기.
  - J4: 폴러는 `domain_id`·`start_time`·`end_time`을 항상 싣고, 같은 ms 이벤트 여러 건·경계 재조회에서 **중복 발행 0**(합성 멱등 키) · 지표 기반 이벤트(`errorType` 빈 값)가 `metricsName`으로 정규화된다.

---

## 7. 산출물·파일 배치·설정

### 7.0 [v3] 파일 배치 — 정본 (D-274 · 아래 v2.2 표와 `.env` 키 목록은 이력)

| 패키지 | 신규/변경 | 파일(전부 「예정 이름」 — J1에서 확정) |
|---|---|---|
| **`apm_gateway/`**(신규 최상위 · D-139) | 신규 | `pyproject.toml` · `apm_gateway/__main__.py`(기동) · `domain/`(WAS 시그니처 판정 · 이벤트 모델 · 레벨 매핑) · `adapters/jennifer/`(Open API 클라이언트 · 허용목록 상수) · `application/`(정합 · 축약·마스킹 · 이벤트 폴러) · `interface/`(MCP 서버 · Bearer · 감사 · (J7) `/metrics/apm`) · 게이트웨이 소유 설정(자체 설정 파일 · 정합 파일 · 이벤트 레벨 · WAS 임계) · `tests/`(경계 불변식 · 허용목록 · 계약 · 폴러 멱등) · `scripts/`(J0 채집·마스킹) · `testdata/`(recorded JSON 마스킹본) |
| `apm_gateway/testdata/jennifer/`(v3.1 · **J0-L** — 패키지 코드보다 먼저) | 신규 | 로컬 Docker 검증 환경: **Dockerfile**(공식 이미지 없음 · JDK 17/21 temurin · 데이터+뷰 서버 한 컨테이너) · compose(127.0.0.1:17900→7900 · 127.0.0.1:18080→WAS · 5000 비게시 · 고정 서브넷 · 설치본·라이선스 외부 마운트 · ~~`jennifer_bootstrap_check=false`~~) · 에이전트 설정(`jennifer.conf` · 뷰 서버 `/download/agent/java/latest`) · 샘플 WAS 앱(느린 응답·힙 증가·예외·(선택) SQL) · 재현 스크립트 · 녹화 하네스 · 목 Open API 서버(폴백) · compose 전용 `.env.example` · README — **[v3.2] 사실은 [J-24] · §0.8** · **[v3.3] 실제 파일**: `server/{Dockerfile,entrypoint.sh}`(temurin 21) · `was/{Dockerfile,entrypoint.sh}`(tomcat jdk11 · 에이전트를 뷰 서버에서 1회 받아 `jennifer.conf` 필수 4키를 덮어쓴다) · `was/app/*.jsp` · `docker-compose.yml` · `.env.example` · `scripts/bootstrap_local.py`·`probe_openapi.py`(로컬 전용) · `README.md` — ~~녹화 하네스는 1차본, 재현 스크립트·목 서버는 아직 없다~~ **v3.3 부기**: `scripts/jennifer_catalog.py`(허용목록 사본)·`masking.py`·`record_openapi.py`(녹화 하네스)·`mock_openapi.py`(목 서버) · `recorded/local-docker/`(21건 + `index.json` — 마스킹본 · 커밋 대상) · `apm_gateway/tests/test_jennifer_fixture_tools.py`(22건 · 루트 수집 밖) · 부하 재현 스크립트만 J0-L-b(§0.10 (4)) |
| `mcp_server/` | 변경(선택 · J1) | 폴스타 고수준 도구 1종(`polestar_was_instances` — `was_object` 정합용 · 값 인자 · 서버측 SQL). **제니퍼 코드는 넣지 않는다** |
| `sre_agent/` | 변경 | `interface/mcp_service.py`(`_build_mcp_servers`에 `"apm"`) · `settings.py`(`apm_mcp_url`·`apm_mcp_token`·지침 플래그) · `application/investigation_guidance.py`(앵커·APM 노트·kind apm 선판정) · `domain/severity_signatures.py`(게이트웨이 판정 결과 승격 — 규칙 재구현 금지) · `domain/remediation.py`(WAS 권고 표) · (선택) `application/evidence_prefetch.py` |
| `noise_gate/` | 변경 | `domain/process_rank.py`(apm 선판정 · R-16) · `application/server_identity.py`(family 배지) · `application/nodes/notification_gate.py`(`app_impact` 승격) · `infrastructure/`(게이트웨이 MCP 클라이언트 — `sre_agent_client.py` 전례). **폴러·정규화 신설 없음**(게이트웨이로) |
| `src/` | 변경 | (J5) `plans/121` APM 처리기 · 두 번째 MCP 엔드포인트 설정 · `static/`(소스 배지) · (J6) `api/routes/remediation.py` |
| `config/` | 변경 | `db_registry.yaml`(`apm` `backend: mcp` · `jennifer` family · 능력 카탈로그) — 본체 정본이라 루트에 남는다. 게이트웨이 정책 파일은 여기 두지 않는다 |
| `tests/` | 변경 | `tests/test_orchestration/test_execution_groups.py:31-33·40-42`(등록 0건 단언 갱신 · J5) |
| 신규 패키지(J6) | 신규 | `remediation/`(G-7 — 게이트웨이와 별개) |
| 문서 | 변경 | `CLAUDE.md` 「저장소 지도」·「패키지 경계」(**J1 산출물**) · `docs/31_jennifer_integration_guide.md`(Wave마다) · 매뉴얼(D-255 — J4·J5·J6) |
| 품질 게이트 | 결정 필요 | `arch_check`·`overfit_check`에 `apm_gateway` 편입 여부(**G-11**) · 벤더 리터럴은 `adapters/jennifer/`에 격리 |

**[v3] `.env` 키 — 전부 「예정 이름」**(인라인 주석 금지 · list/dict는 JSON 배열)

| 키 | 파일 | 기본 | 뜻 |
|---|---|---|---|
| `JENNIFER_API_URL` · `JENNIFER_API_TOKEN` | `apm_gateway/.env` | 빈 값 | Open API 주소 · AIOps 전용 토큰(여기에만) |
| `APM_GATEWAY_BEARER_TOKEN` | `apm_gateway/.env` | 빈 값 | 게이트웨이 MCP 서버 전송 인증 — 비면 무인증(운영 필수) |
| `POLESTAR_MCP_URL` · `POLESTAR_MCP_TOKEN` | `apm_gateway/.env` | 빈 값 | `was_object` 정합용 `mcp_server` 호출(클라이언트) |
| Redis 접속(`REDIS_HOST`·`REDIS_PORT`·`REDIS_DB`·`REDIS_PASSWORD` 꼴) | `apm_gateway/.env` | 빈 값 | `alarm:raw` XADD(폴러) |
| `EXPOSE_RAW_APM_API` | `apm_gateway/.env` | false | 원시 API 도구 |
| `APM_EVENT_POLLER_ENABLED` · `APM_EVENT_POLL_INTERVAL_SECONDS` | `apm_gateway/.env` | false · 30(하한 10) | 이벤트 폴러 |
| `EXPOSE_APM_EXPORTER` · `APM_OPENMETRICS_CACHE_SECONDS` | `apm_gateway/.env` | false · 60 | J7 |
| `APM_MCP_URL` · `APM_MCP_TOKEN` | `sre_agent/.env` | 빈 값 | `AgentSettings.apm_mcp_url`·`apm_mcp_token` — 비면 종전 dict |
| `APM_GUIDANCE_ENABLED` · `APM_SIGNATURES_ENABLED` | `sre_agent/.env` | false | 조사 지침 · 판정 결과 승격 |
| `NOISE_APM_MCP_URL` · `NOISE_APP_IMPACT_ENABLED` | 루트 `.env` | 빈 값 · false | 게이트의 게이트웨이 조회 · `app_impact` 승격 |
| (J5) 두 번째 MCP 엔드포인트 | 루트 `.env` | 빈 값 | `plans/121` 처리기 설계 때 확정 |
| `REMEDIATION_EXECUTOR_ENABLED` | `remediation/` | false | G-6 확정 전 착수 금지 |
| ~~(v3.2) `JENNIFER_SERVER_ZIP` · `JENNIFER_LICENSE_FILE`~~ | ~~`apm_gateway/testdata/jennifer/.env`~~ | — | **[v3.3] 대체** — 아래 `JENNIFER_DIST_DIR` |
| ~~(v3.2) `JENNIFER_VIEW_HOST_PORT` · `JENNIFER_WAS_HOST_PORT` · `JENNIFER_SUBNET`~~ | — | — | **[v3.3] 대체** — 아래 실제 키 · 서브넷은 compose에 고정(`172.29.87.0/24` — 키 아님) |
| **[v3.3 실제]** `JENNIFER_DIST_DIR` | `apm_gateway/testdata/jennifer/.env`(compose 전용 · 기존 `.env` 패턴으로 gitignore) | 없음(필수 — 비면 compose가 멈춘다) | 설치본 zip과 `license` 파일을 둔 저장소 **밖** 디렉터리 — `/dist`에 읽기 전용 마운트 |
| **[v3.3 실제]** `JENNIFER_VERSION` · `JENNIFER_PLATFORM` | 같음 | `5.7.0.1` · `linux/arm64`(실패 시 `linux/amd64`) | 설치본 파일명의 버전 · 컨테이너 플랫폼 |
| **[v3.3 실제]** `JENNIFER_VIEW_HOST_PORT` · `SAMPLE_WAS_HOST_PORT` | 같음 | 17900 · 18080 | 127.0.0.1 게시 포트 |
| **[v3.3 실제]** `JENNIFER_DOMAIN_ID` · `JENNIFER_INST_NAME` · `TZ` | 같음 | 1000 · `sample-was-01` · `Asia/Seoul` | compose가 WAS 컨테이너의 `AGENT_*`로 옮긴다 — **WAS 컨테이너에는 `JENNIFER_*`로 넣지 않는다**(R-29) |
| **[v3.3 실제]** `DISABLE_BOOTSTRAP_CHECK` | 같음 | 0 | 자원 부족으로 기동이 막힐 때만 1(로컬 한정) |

v2.2 §7 [정정]의 `mcp_server/.env`·`ALARM_SERVER_JENNIFER_*` 키 배치는 v3에서 **대체**됐다(제니퍼 키는 `apm_gateway/.env`, 폴러는 게이트웨이).

### 7.1 (이력) v2.2 파일 배치

| 패키지 | 신규/변경 | 파일 |
|---|---|---|
| `mcp_server/` | 신규 | `mcp_server/apm_tools.py` · `mcp_server/apm_client.py` · (J7 선택) `mcp_server/apm_openmetrics.py` |
|  | 변경 | `config.py`(`JenniferApiConfig`·`expose_apm_tools`) · `server.py`(`register_apm_tools`) · ~~`config.toml`(`[[sources]] jennifer_export`)~~(v2 삭제) · `.env.example`(~~`JENNIFER_EXPORT_CONNECTION`~~·`JENNIFER_API_URL`·`JENNIFER_API_TOKEN`) · `pyproject.toml`(httpx 선언 — G7) · ~~`security.py`(apm 도메인 deny)~~ → **v2: 허용목록은 `apm_client.py` 코드 상수**(deny 목록이 아니라 allow 목록 — 신규 관리 API가 추가돼도 자동 차단) |
| `config/` | 신규 | `apm_instance_map.yaml` · `apm_playbooks.yaml` · `apm_signatures.yaml` · `apm_event_levels.yaml` · ~~`db_profiles/jennifer_export.yaml` · `knowledge/jennifer_export/` · `synonym_seeds/jennifer_export.yaml`~~(v2 삭제) · (J6) `remediation_policy.yaml` |
|  | 변경 | `db_registry.yaml`(`apm` 솔루션 `backend: mcp`·`jennifer` family — v2: `jennifer_export` DB 없음) |
| `sre_agent/` | 변경 | `application/investigation_guidance.py` · `domain/severity_signatures.py` · `domain/remediation.py` · `application/briefing_builder.py` · `settings.py`(플래그 2) · `toolset_profiles.py`(APM 폴백 노트) · **[v2.2]** `investigation_guidance.classify_alarm_kind`(게이트와 대칭 `apm` 선판정) · (선택) `application/evidence_prefetch.py`(`apm_*` 사전수집) · `briefing_builder.py`는 인용 판정 변경 불필요(§5.4-d) |
| `noise_gate/` | 신규 | `alarm_server/jennifer_poll_receiver.py` · `domain/apm_event.py` · (J4-2단계 2-A 선택 시) `alarm_server/snmp_trap_receiver.py` |
|  | 변경(v2.2) | `domain/process_rank.py`(`classify_alarm_kind` — `apm` kind 선판정 · R-16) · `application/server_identity.py`(`source_labels_for` — db_id 아닌 family 해석) |
|  | 변경 | `alarm_server/config.py`·`__main__.py`(수신기 선택) · `domain/alarm.py`(source family 배지 — 필드 추가 없이 `raw_payload` 활용 우선) · `application/nodes/notification_gate.py`(`app_impact` 승격 훅) · `application/nodes/investigation_trigger.py`(힌트) |
| `src/` | 변경 | `static/`(소스 배지) · (J6) `api/routes/remediation.py`(승인 대기함) · **[v2.2]** (J5) `plans/121` 처리기 계약에 맞춘 APM 처리기(2단) · `config/db_registry.yaml` `apm` 등재 · `tests/test_orchestration/test_execution_groups.py:31-33·40-42` 갱신 |
| 신규 패키지(J6·G-7) | 신규 | `remediation/`(자체 `pyproject`·`tests`·`scripts`) |
| 문서 | 신규 | ~~`docs/29_jennifer_integration_survey.md`(J0 실측)~~ **[v2.2]** `docs/31_jennifer_integration_guide.md`(운영자 연동 가이드 — **작성 완료 2026-09-29**, Wave마다 갱신) · J0 실측 보고(번호 착수 시 재실측) |
| 매뉴얼(v2.2 · D-255) | 변경 | J4·J5·J6이 화면·채팅 기능을 바꾸면 `scripts/manual/features.yaml`·`content/{user,admin}.md`·캡처 → `python -m scripts.manual.build` · `pytest tests/test_manual` |
| 품질 게이트(v2.2) | 변경 | `scripts/overfit_check.py` 제외 목록에 벤더 모듈(`apm_client.py`·`apm_openmetrics.py`) 등재 — `polestar_tools.py`·`polestar_exporter.py` 대칭 |

`.env` 신규 키(전부 미입력 시 현행 동작): ~~`JENNIFER_EXPORT_CONNECTION`~~(v2 삭제) · `JENNIFER_API_URL` · `JENNIFER_API_TOKEN` ·
`MCP_EXPOSE_APM_TOOLS` · `MCP_EXPOSE_RAW_API` · `APM_GUIDANCE_ENABLED` · `APM_SIGNATURES_ENABLED` ·
`JENNIFER_EVENTS_ENABLED` · `JENNIFER_POLL_INTERVAL_SECONDS` · `APP_IMPACT_ENABLED` · `REMEDIATION_EXECUTOR_ENABLED` ·
(2-A 선택 시) `JENNIFER_SNMP_TRAP_PORT`·`JENNIFER_SNMP_ALLOWED_SOURCES` · (J7 선택) `MCP_EXPOSE_APM_OPENMETRICS`·`APM_OPENMETRICS_CACHE_SECONDS`.
list/dict 값은 JSON 배열 형식 · 인라인 주석 금지(Known Mistakes).
**[v2.2 정정 — 키 이름 관례·위치]** 위 이름은 모두 **예정**이다(코드 0건). 착수 시 기존 관례에 맞춰 확정한다.
① `mcp_server` 노출 스위치는 `MCP_` 접두 없이 `EXPOSE_*`다(`EXPOSE_POLESTAR_TOOLS`·`EXPOSE_RAW_PROMQL`·`EXPOSE_OPENMETRICS_TOOLS`·
`EXPOSE_POLESTAR_EXPORTER` — `mcp_server/mcp_server/config.py` `_apply_env_overrides`). `MCP_` 접두는 `MCP_BEARER_TOKEN` 하나뿐이다. 그래서
`MCP_EXPOSE_APM_TOOLS`·`MCP_EXPOSE_RAW_API`·`MCP_EXPOSE_APM_OPENMETRICS`는 `EXPOSE_APM_TOOLS`·`EXPOSE_RAW_APM_API`·`EXPOSE_APM_EXPORTER` 꼴이
관례에 맞다. ② `JENNIFER_API_URL`·`JENNIFER_API_TOKEN`·`EXPOSE_*`는 **`mcp_server/.env`**에 둔다(루트 `.env` 아님 — `config.py:201-219`).
③ 폴링 수신기는 `alarm_server` 프로세스 안에서 돌므로 그 설정은 `AlarmServerConfig`(`env_prefix="ALARM_SERVER_"` · `alarm_server.env`)
관례를 따른다 — `ALARM_SERVER_JENNIFER_EVENTS_ENABLED` 꼴. ④ `APM_GUIDANCE_ENABLED`·`APM_SIGNATURES_ENABLED`는 `sre_agent/.env`
(`AgentSettings` — 접두 없음), `APP_IMPACT_ENABLED`는 본체 게이트 설정(루트 `.env` · `NoiseGateConfig` `env_prefix="NOISE_"` —
HEAD `src/config.py:834-839`)이라 `NOISE_APP_IMPACT_ENABLED` 꼴이 된다.

---

## 8. 안전 통제·거버넌스

### 8.1 불변식 (D-003·D-035·D-189 계승 — L1까지)

- **읽기 전용** (v2 개정 · **[v3] 위치 `apm_gateway/adapters/jennifer/`**): ~~`jennifer_export`는 SELECT 전용 계정~~ · **`apm_client`가 GET + 경로 허용목록만 통과**시킨다
  (1차 · 코드 상수 · allow 목록). 근거: 정본 스펙에 쓰기·제어 API가 조회 API와 같은 토큰으로 공존하고
  (`POST /api-v2/manage/data-server/control` 등[J-4] · 도메인 GC 요청[J-19]), 조회 전용 토큰 등급은 ✖(U-5). 토큰 권한
  축소는 협의되면 **2차** 방어로 더한다 · `apm_raw_api` 기본 비노출(열어도 허용목록은 동일).
  **[v3.2]** 위 "GET + 경로 허용목록"은 **메서드 + 경로 템플릿 정확 일치 허용목록 · 그 밖은 전부 거부**로 읽는다(§5.2(e)). GET이라도 목록 밖이면 거부한다 —
  `/api/auth/userlist`(이메일·휴대폰)·`/restapi/users`·`/api-v2/environment-variable/<d>` 같은 민감 GET이 있다. 쿼리 `token=` 인증은 쓰지 않고 거부한다[J-23]. **[v3.3]** 로컬 실측으로 확인했다 — 서버는 쿼리 토큰 · 민감 GET · 쓰기·관리 경로 라우팅을 막지 않는다(§0.10 #9·#14·#15).
- **LLM 평면에 실행 도구 없음**(P13): `apm_*`는 전부 조회다. L2 실행기는 LLM 밖(§5.7).
- **결정적=판단·LLM=서술**: 정합·시그니처·임계·권고 선택·심각도 매핑은 코드·yaml. LLM은 서술만.
- **대상은 결정적 확정**: 인스턴스 해소는 정합 파일 · LLM이 인스턴스명을 추측하지 않는다(scope overflow 방지).
- **침묵 폴백 금지**: 미매칭·미가용·창 상한은 `error`/`[한계]`로 노출.
- **폐쇄망**: 제니퍼는 사내 설치형 — 외부 egress 0. 실 운영 데이터의 외부 LLM 송신은 D-120·D-127 규약.

### 8.2 신뢰 경계

- 제니퍼 응답(`running_text`·`message`·프로파일·SQL·URL 파라미터)은 **불신 데이터**다 — 지시문이 섞일 수 있다
  (Plan 78 §7.2와 동형). 서버측 상위 N·마스킹 후에만 LLM에 넣고, 원문은 저장하지 않는다.
- 관리 API 차단은 ~~토큰 권한(1차)과 `mcp_server` 경로 deny(2차)~~ → **v2: `mcp_server` 허용목록(1차 · 항상 성립)과
  토큰 권한(2차 · 협의 시)** 의 **이중**이다 — 조회 전용 토큰이 없어도 1차가 선다.
- **토큰 출처**: AIOps 전용 토큰을 제니퍼 콘솔에서 발급받는다. 폴스타 DB `was_connection.jennifer_token`을 읽어
  재사용하지 않는다(제니퍼 측 발급 주체·사용량 제한·감사를 우회 · 폴스타 토큰 회전 시 조사 경로가 조용히 끊김).
- **공식 MCP 서버**: v2.1 미채택(§0.5). 재검토 트리거로 다시 열더라도 토큰은 `mcp_server` → 제니퍼 MCP 호출 헤더에만
  싣고(`sre_agent` 비보유 — §2.8 ③ 기각 사유), 허용목록은 **도구 이름 단위**로 건다. **벤더 공개 프록시(`insight.jennifersoft.com`)
  모드는 어떤 경우에도 금지**(외부 LLM·최대 30일 보관[J-22] — D-120).
- **J7 OpenMetrics 출구**: 노출 텍스트도 ~~`mcp_server`~~ **[v3] 게이트웨이** Bearer 뒤에만 둔다. 라벨은 §5.9 허용 키만(이름·IP·SQL·URL 금지).
- 자격증명은 ~~`mcp_server`(조회)와 `remediation/`(실행)~~ **[v3] `apm_gateway`(제니퍼 조회) · `mcp_server`(폴스타·Prometheus) · `remediation/`(실행)**에
  **분리 보관** — 조사 프로세스가 실행 자격증명을 갖지 않는다. 제니퍼 토큰은 폴스타 DB 자격증명과 다른 프로세스에 있다(§0.7 (8) 표).

### 8.3 PII·마스킹

- 액티브 서비스 `client_ip` · 트랜잭션 URL 파라미터 · SQL 바인드 값 · `message`/`detailMessage`는 J0 샘플로
  `pii_probe.py`를 돌려 규칙을 확정한다(`docs/pii_filtering_rules.md` 갱신). FabriX PII 필터 차단 시 덤프는
  `logs/pii_block/`에만.
- 감사 로그에도 마스킹본만 남긴다.

### 8.4 부하 가드

- 뷰 서버 보호: 도구별 타임아웃(기본 10s) · 초당 호출 상한(`rate_limit_per_sec`, 기본 5) · 사건당 `apm_transaction_profile`
  호출 상한(기본 5) · 조사 동시성은 현행 `investigation_max_concurrent=2`.
- 폴링 수신기 주기 하한 10s · 백오프.
- **(v2) 토큰 사용량 제한**[J-20]: AIOps 토큰의 제한값을 운영 조직과 정하고(U-5 — 단위 미확인), 초과 응답은
  `error: "apm_quota_exceeded"`로 구분해 브리핑 `[한계]`에 기재한다(빈 결과로 삼키지 않음). 폴링·조사·질의 경로가
  **같은 토큰을 공유**하므로 폴링 주기가 조사 예산을 잠식하지 않게 경로별 호출 상한을 둔다.
- **[v3.2] 호출 수 증가 요인**: `/api/dbmetrics/*`는 지표 1개/호출이라 구간 조회가 지표 수만큼 늘고, X-View 1분 분할(상한 10분 = 10호출)·도메인별 폴링이 더해진다.
  뷰 서버 5.6.2.18 미만은 토큰 호출마다 **Jetty 세션이 누적**되는 결함이 있고, 5.6.2.10/13 미만은 **무제한 토큰이 401**을 낸다 — 운영 버전(U-12)에 따라 폴링 주기·
  호출 상한을 보수적으로 잡는다[J-23].
- **[v3.3] 사용량 단위 실측**: 토큰 `usageCount`는 요청 1건당 1 증가하고 **500 응답도 센다**(§0.10 #10) — 재시도 · 미접속 도메인 폴링 · `/api/dbmetrics/*` 지표별 호출이
  모두 한도를 쓴다. 초과 시 응답은 아직 모른다(U-5).

---

## 9. 리스크·미해결

| # | 리스크 | 심각도 | 대응 |
|---|---|---|---|
| R-1 | **인스턴스↔hostname 미정합** → 데이터가 있어도 무용(Plan 78 R-12) | High | **폴스타 `was_object` 브릿지 우선**(U-10) · J0 U-4 일치율 실측 · 정합 파일은 예외만 · 신뢰도 표기 · 미매칭 상관 보류 · **[v3.2] 완화 후보**: `/api/instance` 응답의 `hostName`·`ipAddress` 직접 대조를 정합 ①순위로(§5.3) |
| R-2 | ~~RDB Export 비활성·PG 최신 버전 비호환·DDL 상이~~ → **(v2) API 단일 경로 의존** — 뷰 서버·Open API 장애(또는 비활성 옵션) 시 APM 증거 전무, 적재본 같은 우회 경로 없음 | Med | 기동·조사 시 APM 가용 사전 판정(§5.4-a) → **OS 근사 폴백 + 사유 명시**(침묵 강등 금지) · 보존 기간 밖·자유 집계는 부분 응답 · 요구 확인 시 G-1 ⓑ로 적재본 보조 경로 복원 |
| R-3 | 5.x 이벤트 명칭이 4.x와 달라 매핑 파일 오류 | Med | U-1 실측 후 파일 작성 · 미지 유형은 `unknown`으로 DASHBOARD(보수) |
| R-4 | Open API 토큰이 관리 API까지 허용 — **(v2) 스펙 실측으로 개연성 상향**: 쓰기·제어 API(데이터 서버 control·도메인 변경·도메인 GC)가 같은 인증 체계 | High | §8.1 **허용목록 1차(항상)** + 토큰 권한 2차(협의) · 비GET·목록 밖 경로 거부 테스트(J1 수용 기준) · **[v3.2]** 문서 밖 쓰기·제어 11건+ · 민감 GET 존재 → **메서드 + 경로 템플릿 정확 일치 허용목록**과 거부 테스트 입력(§5.2(e)) |
| R-5 | 뷰 서버 부하·1분 창 제한으로 조사 지연 | Med | §8.4 · 구간 상한 · 서버측 캐시(사건창 단위, TTL 60s) |
| R-6 | PII 노출(프로파일·바인드 값) | High | §8.3 · J0 샘플 검증 전 실 데이터 LLM 투입 금지 |
| R-7 | `app_impact`가 게이트를 과억제/과승격 | Med | 승격 전용 비대칭 · 심각도 3 불변 · 플래그 off 기본 · 결정 기록(decision_store) |
| R-8 | L2 실행기의 오작동·범위 초과 | **High** | 정책 파일 거부 · 단일 인스턴스 · 이중 승인 · 트랜잭션 롤백 · G-6 전 착수 금지 |
| R-9 | 벤더 API 변경(5.x 마이너 업그레이드) | Med | `apm_tools`가 벤더 매핑 계층을 캡슐화 · 계약 테스트(recorded JSON) |
| R-10 | 이벤트 폴링이 D-119 경계를 우회 | Low | G-4 권고안 ①(경계 경유) → **[v3] 해소** — 폴러가 게이트웨이 안이라 토큰·감사가 한 곳이다(D-274 ④) |
| R-11 | 미들웨어 OS 근사(W7-1)와 APM 판정 충돌 | Low | APM 1차·OS 2차 우선순위 설정 · 충돌 시 브리핑에 둘 다 표기 |
| **R-12** | (v2) **진단 조회가 전부 v1(`/api/*`)인데 v1은 "유지보수 중단"** — *"not removed for compatibility, but are no longer maintained"*[J-4]. 향후 버전에서 필드 변경·제거 가능 | Med | 폐기가 아니라 동결로 판정(5.6.4에도 v1 필드 추가 △) · recorded JSON 계약 테스트(U-12) · 기동 시 버전 에코 · 벤더 매핑 계층 캡슐화(§5.2(d)) · v2 대체 엔드포인트 출현 시 `apm_client.py` 내부만 교체 · v1 제거 공지 시 §0.5 재검토 트리거 ⓑ로 공식 MCP 재판정 |
| **R-13** | (v2) **5.7.0 javax→jakarta 전환**으로 커스텀 어댑터(2-B) 빌드·로드 실패 · `extension_allowed_packages` 미등록 시 어댑터 미로드(무증상) | Med | 2-A(SNMP 공식 어댑터) 우선 검토 · 2-B 선택 시 대상 버전 고정 빌드 + 기동 후 **테스트 이벤트 1건 수신 확인**을 배포 체크리스트에 |
| ~~**R-14**~~ | ~~(v2) "표준 연동 규격"이 조직 내부 규격으로 존재하는데 본 계획이 그와 다른 전송·인증을 택함~~ → **v2.1 해소**: 사용자가 CNCF OpenMetrics로 확정(G-2) | — | J7(§5.9)로 충족 · 이벤트·진단 데이터는 규격 범위 밖(O-2) |
| **R-15** | (v2.1) **J7 OpenMetrics 출구가 뷰 서버 부하·토큰 사용량을 잠식**하거나, 미정합 인스턴스가 잘못된 `nodename`으로 노출돼 인프라 지표와 **오조인** | Med | 캐시 TTL·도메인당 1호출·J7 전용 호출 예산(§5.9) · 미정합은 `nodename` 미부여 · `jennifer_bridge_up`/`_truncated`로 상태 노출 · 플래그 기본 off |
| **R-16** | (v2.2) **제니퍼 이벤트 이름이 알람 kind 분류기와 충돌** — 게이트 `classify_alarm_kind`와 조사측 동형 함수가 부분 문자열로 판정해 `JVM_HEAP_MEM_HIGH`·`OUTOFMEMORY`→`memory` · `JVM_CPU_HIGH_LONGTIME`→`cpu` · `PROCESS_DOWN`→`process`(2026-09-29 실측). OS 플레이북 주입·E6 프로세스 보강·L3 kind 프로파일이 WAS 사건에 붙어 조사 초점이 OS로 쏠린다 | Med | `resource_type="apm.Instance"` **선판정** `apm` kind를 두 분류기에 대칭으로 · §2.3 전 유형 표본 단언 테스트 · 호스트 보강을 교차 증거로 남길지는 U-13 → **[v3.1] 대응 확정(2026-09-29)**: `apm` kind 선판정을 두 분류기에 대칭 · §2.3 전 유형 표본 단언 · 호스트 보강 처리는 U-13 확정안 |
| **R-17** | (v2.2) **공유 노출 기계가 OpenMetrics 2.0을 협상** — `om_exposition.render_exposition`이 `version=2.0.0` 요청에 2.0 Content-Type으로 응답(prometheus-client 0.26.0 실측). G-2 "1.0 고정"·`plans/92` O-1과 어긋나고, 폴스타 브리지 `/metrics`에도 이미 해당한다 | Low~Med(소비자가 2.0을 요청할 때만 발현 △) | `plans/92` 소유자에게 수정 요청(협상 상한 1.0) · J7 수용 기준에 2.0 요청 단언 · docstring 정정 → **[v3.1] 해소(2026-09-29 · G-10)**: `render_exposition`이 OpenMetrics 버전을 1.0.0으로 상한 · 9099·9097 장기 실행 인스턴스는 재기동해야 반영 · 잔여: 본체 `/metrics`(`src/observability/metrics.py:76`)의 같은 패턴은 `plans/92` 소관 |
| **R-19** | (v3) **감사가 두 프로세스로 나뉜다**(D-119 ④ 약화) — 한 조사의 폴스타 조회는 `mcp_server`, 제니퍼 조회는 게이트웨이에 남는다 | Low~Med | 같은 감사 형식(PromQL `_audit` 전례) · 조사 id·thread_id를 호출 인자로 전파 · J1·J4 수용 기준 |
| **R-20** | (v3) **운영 대상 1세트 추가**(프로세스·포트·Bearer·헬스체크) | Low | 제니퍼가 있는 환경에만 배포 · 기동 로그 1줄(버전·허용 경로 수·폴러 상태) · 헬스체크 도구 |
| **R-21** | (v3) **복제 코드 드리프트** — Bearer 미들웨어·반환 계약·XADD 형식·`REQUIRED_EVENT_FIELDS`·(J7) OpenMetrics 직렬화기를 import 없이 복제한다 | Med | 양쪽에 같은 계약 픽스처 · 계약 테스트 · 직렬화기는 G-12에서 복제/추출 결정 |
| **R-22** | (v3) **게이트웨이 → `mcp_server` 의존**(`was_object` 정합·`nodename` 역해소) — `mcp_server` 장애 시 정합 1순위를 못 쓴다 | Low~Med | TTL 10분 캐시 · 2순위 정합 파일로 내려가되 신뢰도·사유를 반환에 싣는다(침묵 강등 금지) |
| **R-23** | (v3.1) **로컬 제니퍼 라이선스·이미지 미확보** — ~~체험판 제공 여부·이미지 공개 여부 「조사 대기」~~ → [v3.2] 끝 칸 사실 | Med | 목 Open API 서버 폴백(§0.8 (6) — **v3.3 구현 완료** · 실서버와 40건 불일치 0) · 계약 테스트 정본은 recorded JSON · 폴백 사용 사실과 한계를 J0-L 보고에 표기 · **[v3.2 사실]** 공식 이미지 없음(Dockerfile 새로 작성) · 평가판 = **2주 · IP 기반(루프백 불가) · 회사 이메일·전화 확인 · 사이트당 1회** · 라이선스 없으면 에이전트 접속 거부 → **J0-L-a/b 2단계 분할**(준비를 먼저 끝내고 2주 창에 채집 집중) · 장기 개발용 라이선스 영업 문의 · 만료 뒤 목 서버 |
| **R-24** | (v3.1) **arm64 에뮬레이션 성능** — 개발 맥(Apple Silicon)에서 amd64 전용 이미지면 에뮬레이션으로 느리거나 불안정(~~arm64 지원 여부 「조사 대기」~~ → [v3.2] 끝 칸) | Low~Med | 로컬은 **기능 검증만**(응답 형태·허용목록·이벤트 재현) · 지연·부하 결론은 내부망 결과로만 · 필요 시 x86 호스트에서 J0-L 수행 · **[v3.2]** 서버·Java 에이전트의 arm64 **공식 명시 없음**(Python·Node.js·.NET 에이전트만 명시) · 간접 근거(데이터 서버 네이티브 바이너리 없음 · JNA에 aarch64 포함 · temurin 17/21·tomcat jdk17 arm64 제공)로 △ → **네이티브 먼저 시도, 실패 시 `platform: linux/amd64` 에뮬레이션** · **[v3.3] 서버 해소** — `eclipse-temurin:21-jdk` arm64 네이티브 기동 성공(§0.10 #1). 에이전트 쪽 문제는 arm64가 아니라 버전이다(R-28) |
| **R-25** | (v3.1) **로컬과 운영의 버전 차이** — 로컬 설치본(최신 5.7.x 등)과 운영 버전이 달라 v1 응답 필드·API 존재가 다를 수 있다 | Med | recorded JSON 출처 표지 · J0-O에서 필드 차이 대조(U-12) · 운영 채집분이 생기면 계약 픽스처를 운영분 우선으로 교체 · 허용목록은 운영 버전 스펙 기준으로 확정 · **[v3.2]** 로컬은 최신 설치본 5.7.0.1(비동기 트랜잭션을 애플리케이션 통계에 포함 · SNI 기본 on) — 운영이 5.6.x면 통계 수치·필드가 다를 수 있다 |
| ~~**R-26**~~ | ~~(v3.2) **Bootstrap Check off의 데이터 유실** — `jennifer_bootstrap_check=false`는 *"충분한 수집 성능을 보장할 수 없으며 데이터 유실이 발생할 수 있음"*(매뉴얼)~~ **[v3.3] 철회** — 5.7.0.1은 Docker에서 Bootstrap Check를 켠 채 기동한다(§0.10 #2). 끄기는 자원 부족 시 옵트인(`DISABLE_BOOTSTRAP_CHECK=1`)으로만 남는다 | — | (이력) 로컬 검증 데이터라 수용 — 정본은 채집한 recorded JSON · 로컬 compose에만 두고 운영 설정 예시·문서에는 넣지 않는다 · 로컬 성능 수치로 결론 내지 않는다 |
| **R-27** | (v3.2) **설치본 입수 경로의 약관 불확실** — 공개 S3 직접 링크는 로그인·키 없이 받아지지만 평가판 약관 적용 여부 「확인 불가」 | Low | 공식 다운로드 페이지(키 입력 + 약관 동의) 권고 · 직접 링크는 약관·영업 확인 뒤 · 설치본은 저장소 밖(사용자 할 일 3) · **[v3.3]** 사용자가 공개 S3 입수를 확정했다(AskUserQuestion 2026-09-29 — 「공개 S3에서 받기 (Recommended)」) |
| **R-28** | (v3.3) **에이전트 버전 불일치** — 뷰 서버 5.7.0.1이 배포하는 Java 에이전트가 5.5.2.5(2021 빌드)라 JDK 17 WAS에서 JVM 기동이 실패한다(`Unknown Java version string: 17.0.20.1` → `processing of -javaagent failed` · §0.10 #5). 로컬 채집 데이터가 운영 에이전트와 다를 수 있다 | Med | 로컬은 JDK 11 WAS로 우회(실측 정상) · J0-L-b 착수 조건에 최신 5.6.x 에이전트 입수(§0.8 (10) 9) · 못 구하면 5.5.2.5로 채집하고 출처 표지에 에이전트 버전 · 운영 에이전트 버전·JDK는 J0-O에서 확인 · 가이드 §11 증상 등재(에이전트·JDK를 바꾼 뒤 WAS가 뜨지 않으면 첫 의심) |
| **R-29** | (v3.3) **`JENNIFER_*` 환경변수 충돌** — 에이전트가 이 접두의 환경변수를 설정으로 읽는다. 로컬에서 `JENNIFER_VIEW_URL` 등 4개를 두자 에이전트가 `jennifer.conf`의 `server_address` 대신 127.0.0.1:5000으로 접속했다(§0.10 #6 · 원인 변수 미확정) | Low~Med | 에이전트를 붙인 JVM 환경에 `JENNIFER_*`를 두지 않는다(로컬 compose는 `AGENT_*`) · 게이트웨이 `JENNIFER_API_URL`·`JENNIFER_API_TOKEN`(「예정 이름」)은 `apm_gateway/.env` 파일로만 읽고 WAS 호스트 셸에 export하지 않는다 · 가이드 §11 |
| **R-18** | (v2.2) **기준 경로 2단·운영 1단에서 사용자 pull 조사 위임 미도달** — `fault_diagnosis`는 3단 그래프 노드뿐(서브에이전트 아님). "OO WAS 장애 원인" 같은 채팅 요청이 `sre_agent`로 가지 않는다 | Med(87 소관 밖) | J3 pull 검증은 3단에서만 · 알람 push 경로(`investigation_trigger`)는 사다리 무관이라 영향 없음 · 2단 편입 소유 계획 지정은 사용자 결정(보고) → **[v3.1] 소유 지정 완료(2026-09-29)**: `plans/121`(D-270 ⑯ — 2단 배선 소유) §14.2 잔여 표에 부기 · 작업 트리 실측으로 2단 조사 위임 미존재 재확인(`src/orchestration/schemas.py:21` · `subagents.py:1796-1878`) |

**미해결**: 제니퍼 도입 시점·라이선스(범위 밖 · 본 계획의 착수 전제) · DPM 연동(Plan 55 M3 DPM 축) · L3.

---

## 10. 사용자 확정 게이트

| # | 질문 | 권고 | 영향 |
|---|---|---|---|
| **G-1** ✅ **확정(2026-09-17)** | 연동 방식: ⓐ API 단일(Open API 조회 + 이벤트 폴링→어댑터) ⓑ API + RDB Export 적재본 보조 ⓒ SQL + API + 이벤트 3중(v1.1) | **사용자 확정: ⓐ "api 위주로 가자"** — ⓑ는 장기 자유 집계 요구가 확인될 때만 복원(§5.2(a)) | J1·J2·J5 형태 |
| **G-2** ✅ **확정(2026-09-17)** | "표준 연동 규격"의 실체 | **사용자 확정: CNCF OpenMetrics**("cncf openmatric에 정의된 규격") — 1.0 고정(2.0 Experimental) · 제니퍼 네이티브 노출 ✖ → 우리 브리지로 충족(J7 · §0.4 (2) · §5.9) · 이벤트·진단 데이터는 규격 밖 | J7 신설 · G-9 · R-14 해소 |
| **G-3** ✅ **확정(2026-09-29 · 사용자 '권고에 맞게 진행하고 미결사항은 해결하라')** — `apm_*` | 도구 표면 이름: 벤더 중립 `apm_*` vs `jennifer_*` | **`apm_*`**(Plan 78 §4.7.2 · 벤더 교체 시 매핑 계층만) | J2 |
| **G-4** | 이벤트 폴링 수신기의 API 호출 경로: ① `mcp_server` 경유 ② 직접 · 2단계 push 착수 여부·방식(2-A SNMP trap 공식 어댑터 / 2-B 커스텀 EVENT 어댑터) | **①** — D-119 일원화 · (v2.1) 2단계는 **폴링의 지연·부하·토큰 사용량이 실측으로 문제될 때만** 착수, 방식은 운영 조직의 어댑터 배포 수용 여부로(§5.5) | J4 |
| **G-4** (v3 · **해소**) | 이벤트 폴링 수신기의 API 호출 경로 | **해소(2026-09-29 · D-274 ④)** — 폴러가 게이트웨이 안에 있어 토큰·감사가 한 곳뿐이다. ①/② 선택이 사라졌다 | J4 |
| **G-4b** ✅ **확정(2026-09-29 · 사용자 '권고에 맞게 진행하고 미결사항은 해결하라')** — push는 폴링의 지연·부하·토큰 사용량이 실측으로 문제될 때만 착수 · 수신은 게이트웨이 안 | 2단계 push 착수 여부 · 방식(2-A SNMP trap 공식 어댑터 / 2-B 커스텀 EVENT 어댑터) · **수신 위치** | 착수는 **폴링의 지연·부하·토큰 사용량이 실측으로 문제될 때만**(v2.1 그대로) · 수신은 **게이트웨이 안**(정규화·정합이 거기 있다) | J4 |
| **G-5** | text2sql 편입 시점: ~~J1 직후~~ vs J3 이후 | (v2) **J2 + Plan 82 Wave 7 직후** — SQL 파이프라인 재사용이 사라져 솔루션 그룹 실행자(`backend: mcp`)가 선행이다 | J5 |
| **G-5′** ✅ **확정(2026-09-29 · 사용자 '권고에 맞게 진행하고 미결사항은 해결하라')** — ⓐ `plans/121` 처리기 계약(TP-9.1·9.2·10.5)이 생긴 뒤 APM 1급 처리기로 | 질의 경로 편입 **방식·시점**: ⓐ `plans/121` TP-9.1·9.2·10.5(카탈로그·비SQL 등재·1급 처리기 선례) 뒤 **APM 1급 처리기**로 ⓑ 121을 기다리지 않고 2단 `host_inspect` 프로파일에 `apm_*`를 붙이는 **임시안**(92 O3 `metrics_live` 선례) 뒤 121로 이전 ⓒ 채팅 질의 편입 보류(조사·이벤트·J7만) | **ⓐ** — 82 Wave 7 디스패치는 D-270 ⑯으로 121에 흡수됐고, 121의 목표가 "새 시스템 = 카탈로그 레코드 + 처리기 계약 + 매니페스트"다. ⓑ는 빠르지만 `host_inspect`의 "의도적으로 좁다"(`host_inspect.py` 프로파일 판정 주석) 설계와 121 처리기 계약 양쪽에 두 번째 경로를 만든다 | J5 · `plans/121` 착수 순서 |
| **G-6** ✅ **확정(2026-09-29 · 사용자 AskUserQuestion '권고대로 확정')** — J3 완료·목업 검증 뒤 착수 · 초기 카탈로그 3종(덤프 low · PLC 하향·원복 medium · 재기동 high·이중 승인·단일 인스턴스) · LLM 미탑재 `remediation/` + 정책 파일 · **D-195 ③ 등재** · 조사 평면 읽기 전용 유지 | **L2(승인 후 실행) 착수 여부와 ~~D-003 예외 범위~~ [v3.3] 실행 평면 통제 범위**(카탈로그 3종·단일 인스턴스·이중 승인 — **D-003 범위 밖 · 예외 아님**(사용자 "권고" 2026-09-29) · D-195 ③ 별도 통제) | 착수하되 **J3 완료·목업 검증 후** · 초기 카탈로그는 §5.7 3종 | J6 · D-195 ③ |
| **G-7** ✅ **확정(2026-09-29 · 사용자 '권고에 맞게 진행하고 미결사항은 해결하라')** — 신규 최상위 `remediation/` | 실행기 위치: ① `noise_gate/` 하위 ② **신규 최상위 `remediation/`** | **②** — 실행 자격증명 격리(D-139) | J6 |
| **G-8** ✅ **실측 판정(2026-09-17)** | 제니퍼 공식 MCP 서버 활용: ① 미사용 — `mcp_server`가 Open API 직접 ② `mcp_server` 뒤 백엔드 ③ `sre_agent` 직결 | **① 확정 · ② 미채택 · ③ 기각** — 사용자 지시 "실측하여 판정하라"에 따라 §0.5 M-1~M-6 실측(런타임 `tools/list`는 설치본 비공개·SaaS 로그인으로 불가 — 최선값 가정에서도 결론 불변) · 재검토 트리거 ⓐ~ⓒ 전부 충족 시에만 재판정 | J1 · §5.2(d) 추상화 삭제 |
| **G-9** (v2.1 신규) | J7(OpenMetrics 표준 노출) **착수 시점**: ① J2 직후 단독(직렬화기 자체 구현) ② `plans/92` 트랙 B-2 착수와 묶음(직렬화기·인증·캐시 공유) ③ 소비자(운영 Prometheus 등)가 확정될 때 | **②** — 같은 형식 기계를 두 번 만들지 않는다. 단 소비자가 먼저 확정되면 ①로 앞당긴다 | J7 · `plans/92` O-B2 |
| **G-9** ✅ **확정(2026-09-29 · 사용자 '권고에 맞게 진행하고 미결사항은 해결하라')** — ①′ 착수 조건 = J2 완료 + 소비자 확정 · 공유 기계 재사용(v3에서는 복제본 — G-12) | 위와 같음 — 단 **②의 전제가 이미 충족됐다**(`om_exposition.py` 구현 · `plans/92` B-2 O5 실 스크레이프 통과) | **①′ J2 직후 · 공유 기계 재사용**(직렬화기를 새로 만들지 않는다) · 소비자(운영 Prometheus 등)가 없으면 ③까지 미룬다 — 즉 **"J2 완료 + 소비자 확정"이 착수 조건**이다 | J7 |
| **G-11** ✅ **확정(2026-09-29 · 사용자 '권고에 맞게 진행하고 미결사항은 해결하라')** — 경계 불변식 테스트 J1 필수 · `overfit_check` 편입 + `adapters/jennifer/` 제외 · `arch_check` 편입은 J1에서 판정(판정 기준: §10 아래 [v3.1] G-11 기준) | 품질 게이트 범위: `arch_check`(현재 `src`·`noise_gate`)·`overfit_check`(현재 `mcp_server/mcp_server` 포함) 스캔 대상에 `apm_gateway`를 넣을지 | 권고: **경계 불변식 테스트는 필수**(J1) · `overfit_check`는 편입하되 `adapters/jennifer/`는 제외 목록(`polestar_tools.py` 전례) · `arch_check` 편입(4계층 규칙 정의)은 J1에서 비용을 보고 결정 | J1 |
| **G-12** ✅ **확정(2026-09-29 · 사용자 '권고에 맞게 진행하고 미결사항은 해결하라')** — ① 복제. G-10을 원본에서 고쳤으므로(2026-09-29) 복제본은 그 수정을 물려받는다 | J7 OpenMetrics 직렬화기: ① `om_exposition.py`(321행)를 게이트웨이에 **복제** ② 벤더 중립 **공용 모듈로 추출**(두 패키지가 쓰는 새 공유 위치 — 새 경계 필요) | 권고: **①** — 소비자가 둘뿐이고 공용 위치 신설은 경계 하나를 더 만든다. R-17(2.0 협상) 수정은 복제본·원본 둘 다에 넣는다. 착수 시(J7) 확정 | J7 |
| **G-10** ✅ **확정(2026-09-29 · 사용자 '권고에 맞게 진행하고 미결사항은 해결하라')** — ① 원본 `om_exposition` 협상을 1.0 이하로 — **수정 완료**(`_cap_openmetrics_version` · 테스트 9건 · `mcp_server` 600 passed) | 공유 노출 기계의 **OpenMetrics 버전 상한**(§0.6 #15 · R-17): ① 공유 `om_exposition`의 협상을 1.0 이하로 묶는다(`plans/92` 소유 코드 — 폴스타 `/metrics`에도 적용) ② J7 라우트만 감싸서 1.0으로 고정 ③ 현행 유지(2.0 요청 시 2.0 응답) | **①** — G-2의 "1.0 고정"과 92 O-1이 같은 기준이고, 한 기계를 두 규칙으로 쓰지 않는다. 87은 코드 소유자가 아니므로 **92 소유자에게 요청**한다. ③은 G-2 확정과 어긋나 권고하지 않는다 | J7 · `plans/92` |

**[v3.1] 확정 요약(2026-09-29)** — 사용자 *"권고에 맞게 진행하고 미결사항은 해결하라."* · G-6은 AskUserQuestion 「권고대로 확정」

| 게이트 | 확정 내용 |
|---|---|
| G-1 · G-2 · G-8 | (2026-09-17 확정 그대로) API 위주 · CNCF OpenMetrics 1.0 · 공식 MCP 미채택 |
| G-3 | `apm_*` |
| G-4 | 해소(v3 · D-274 ④) — 폴러가 게이트웨이 안 |
| G-4b | push는 폴링의 지연·부하·토큰 사용량이 실측으로 문제될 때만 · 수신은 게이트웨이 안 |
| G-5′ | ⓐ `plans/121` 처리기 계약(TP-9.1·9.2·10.5) 뒤 APM 1급 처리기 |
| G-6 | J3 완료·목업 검증 뒤 착수 · 카탈로그 3종 · LLM 미탑재 `remediation/` + 정책 파일 · D-195 ③ 등재 · 조사 평면 읽기 전용 · **[v3.3] 기록 방식 = D-003 범위 밖(예외 아님 — 사용자 "권고" 2026-09-29)** |
| G-7 | 신규 최상위 `remediation/` |
| G-9 | ①′ J2 완료 + 소비자 확정 · 공유 기계 재사용(복제본) |
| G-10 | ① 원본 `om_exposition` 1.0 상한 — **수정 완료(2026-09-29)** |
| G-11 | 경계 불변식 J1 필수 · `overfit_check` 편입(`adapters/jennifer/` 제외) · `arch_check`는 아래 기준으로 J1에서 판정 |
| G-12 | ① 복제 — G-10 수정을 물려받는다 |
| 해석 5건(v3 보고) | 채택 확정 — `was_signals` 공유(`sre_agent` 승격만 · 권고 표는 `sre_agent`) · 게이트웨이 정책 파일은 `apm_gateway/` 아래(레지스트리는 루트) · `polestar_was_instances`(예정 이름) · §7.0 예정 키 이름 · D-119 ⑥ 결과 부기 |
| R-16 · U-13 | `apm` 선판정 대칭 · OS 플레이북·OS kind L3 차단 · E6 호스트 보강은 "호스트 참고"로 유지 |

**[v3.1] G-11 `arch_check` 편입 판정 기준(J1에서 적용)** — `scripts/arch_check.py`는 모듈 접두 → 계층 매핑(`MODULE_LAYER_MAP`)과
스캔 루트(`src`·`noise_gate` · `_INTERNAL_ROOTS`)로 동작한다. ① 매핑 추가(`domain`→domain · `adapters`→infrastructure · `application`→application ·
`interface`→interface · `__main__`→entry · 설정→config — 약 6~8행)와 스캔 루트 1개 추가로 끝나고 ② 2단 중첩(`apm_gateway/apm_gateway/`)의 모듈 이름
해석에 스캐너 로직 변경이 필요 없으며 ③ 기존 `src`·`noise_gate` 판정이 바뀌지 않으면(`--ci` 결과 동일) **편입한다**. ②가 성립하지 않으면(스캐너 변경
필요) 편입하지 않고, 게이트웨이 `tests/`에 계층 방향 AST 테스트(경계 불변식 테스트와 같은 방식)를 둔다. 어느 쪽인지 J1 완료 보고에 적는다.
**[v3.3 부기]** 판정 항목 하나를 더한다 — `apm_gateway/tests`(지금은 J0 도구 테스트 22건)는 루트 pytest 수집 경로(`testpaths = ["tests", "noise_gate/tests"]`) 밖이다.
루트 수집에 넣을지(본체 `pytest` 한 번으로 돈다) · `sre_agent`·`mcp_server`처럼 별도 실행으로 둘지를 같은 J1 보고에서 정한다.

**[v3.1] 파일명 `-TODO` 유지 판단** — G-10 수정(`mcp_server/mcp_server/om_exposition.py`)은 `plans/92` 소유 공유 기계의 결함 수정이고 제니퍼 코드가
아니다. 87의 착수(`apm_gateway/` 생성 · J0-L 로컬 환경 파일 포함)로 보지 않으므로 파일명 `-TODO`를 유지한다.
**[v3.3]** `apm_gateway/testdata/jennifer/`(Dockerfile 2 · compose · 샘플 JSP 4 · 로컬 전용 스크립트 2 · README — **v3.3 부기** 이후 스크립트 6 · 녹화본 21건 · `apm_gateway/tests/` 도구 테스트 1파일)가 생겼다. §0.8 (3) 기준대로 **검증 픽스처**라
87의 착수로 보지 않는다 — 게이트웨이 패키지 코드(`apm_gateway/apm_gateway/`)와 `pyproject.toml`은 0건이다(`apm_gateway/tests/`의 파일도 J0 도구 테스트다). INDEX 규칙(`-TODO` = 산출물 코드 0건)의 「코드」를
게이트웨이 패키지 코드로 읽은 판단이며, J1(패키지 골격) 착수 때 `-WIP`로 바꾼다.
**[v4 · 2026-09-29]** J1(패키지 골격)~J4가 구현돼 파일명을 **`-WIP`**로 바꿨다(잔여 J5·J6·J7 · J0-L-b·J0-O).


---

## 11. 신규 결정 예약 — D-195 (등재는 J0 완료·G-1 확정 시)

> **채번 근거(2026-09-03 실측)**: `docs/02_decision.md` `## D-` 헤더·「변경 이력」 표 최댓값 **D-193** ·
> 「채번 이력」 표 예약(D-105·115·134·158·163~168·176). 다음 번호 194는 작성 시점에 `plans/50` v2.1·
> `CAPABILITY-MAP-50.md`·`SPEC-briefing-contract.md`가 "D-194 예정"으로 쓰고 있어(표 미등재) 충돌을 피해
> **D-195**를 「채번 이력」 표에 등재했다. 같은 날 **D-194는 `plans/50` v2.2로 본문 등재 완료**(`## D-194` 실측)
> — 결과적으로 번호가 연속이며 재조정은 불필요하다.

| 번호 | 결정(예약) | Wave |
|---|---|---|
| **D-195** | **제니퍼 APM 연동** — ① `mcp_server` 세 번째 소스(~~SQL 적재본 +~~ **Open API 단일 조회 경로 + 이벤트 API 폴링(어댑터 push는 조건부)** — v2 2026-09-17: RDB Export SQL 경로 미채택 · GET·경로 허용목록 1차 통제 · **v2.1 2026-09-17: 사용자 확정 G-1 API 위주 · G-2 표준 연동 규격 = CNCF OpenMetrics → 수치 지표를 OpenMetrics 1.0으로 노출하는 선택 출구(J7) · G-8 실측 판정 = 제니퍼 공식 MCP 서버 미채택(Open API 직접 호출 확정)**) · 벤더 중립 `apm_*` 도구 표면 · OTel 컨벤션 · 서버측 정합·축약·마스킹·감사(D-119 ① 적용 · **D-168 2단계의 구체화**) ② 진단 확대 — `sre_agent` 지침·플레이북·WAS 시그니처·권고 표·브리핑(결정적 판정 · APM 1차·OS 근사 2차 폴백 사유 명시) ③ **대응·복구 L2** — ~~D-003의 **실행 평면 한정 예외**~~ **[v3.3] D-003 범위 밖(DB 쓰기 없음)의 별도 실행 평면 — D-195 ③이 통제**(카탈로그 조치·승인된 제안 id·단일 인스턴스·이중 승인·정책 파일·트랜잭션 검증-롤백·LLM 미탑재 실행기 · L3 범위 밖) — **③은 G-6 확정 전 등재하지 않는다** | J1~J6 |

**D-168과의 관계**: D-168은 Plan 78이 "미들웨어 조사 편입(1단계 OS 근사 · 2단계 APM 연계)"으로 예약한 번호다.
본 계획은 그 **2단계의 벤더 확정판**이므로, 등재 시 D-168을 소진(1단계 = 구현 완료 `middleware_profile`·
`middleware_signatures.yaml` · 2단계 = D-195로 위임)하고 D-195에서 상세를 갖는다. 둘을 한 번호로 합치지
않는 이유는 D-168이 Plan 78의 W7 범위 결정이고, D-195는 소스·조치 평면까지 포함하는 별개 범위이기 때문이다.
**[v2.2 정정]** 위 "1단계 = 구현 완료 `middleware_profile`·`middleware_signatures.yaml`"은 **정의·테스트까지만** 맞다. 두 자산은 프로덕션
호출부가 0건이고(`middleware_profile()` · `src/domain/middleware.identify`), 운영 원격 조사는 bash가 꺼져 있다(D-233). D-168을 등재할 때는
"1단계 = 로컬 프로파일 자산(정의·테스트) · 운영 원격 조사 미배선"으로 적는다. **폐기 제안이 아니다** — D-161 ② 4항(운영 `.env`·설치 상태·
`git log`·역방향 import)을 실측하지 않았다. D-195 예약 범위(①②③)는 v2.2에서 바뀌지 않는다.

**파급 문서(등재 시 갱신)**: `plans/55` §7 선행 결정(대상 제품=제니퍼·연동 방식=~~3중~~ **API 단일(v2)**·엔티티 매핑=정합 파일) ·
`plans/78` W7-2단계 상태 · `plans/64` §8.3(선행 거버넌스 → D-195 ③) · `plans/sre-agent/README.md` 계획 표 ·
`config/db_registry.yaml` 주석(`apm` `backend: rest` → `mcp`) · `docs/18_known_mistakes.md`(J0에서 실수 발생 시).
**v2 파급(각 소유 계획 착수 시 반영 — 본 갱신에서는 편집하지 않음)**: `plans/82` Wave 7(그룹 실행자 훅의 `mcp` 변형이
J5 선행) · `plans/90` §9 A3 표(`jennifer_export` db_id 혼입 우려 → 소멸) · `plans/92` 머리말("벤더 exporter가
OpenMetrics를 내면 같은 파서 재사용" → 제니퍼는 OpenMetrics 노출 ✖로 해당 없음) — **2026-09-22 `plans/92` v3에서 반영 완료**
(F-12 · 관계를 "87 J7이 92 B-2 노출 기계를 재사용"으로 정정).
**[v3 · D-274] 예약 내용 정정** — 위 D-195 ①의 "`mcp_server` 세 번째 소스"는 **"`apm_gateway` 독립 패키지(자체 MCP 서버 · 이벤트 폴러 · WAS 판정)"**로
정정했다. `docs/02_decision.md` 「채번 이력」 D-195 행도 같은 날 정정했다. "D-119 ① 적용"은 **"D-274(D-119 ① 개정) 적용"**으로 읽는다. ②③은 그대로다.
**v3 파급(D-274)** — 완료: `docs/02_decision.md`(D-274 본문 · D-119 개정 부기 · D-195 행 정정 · 변경 이력 · 안내 라인) · `docs/31` 가이드.
각 소유 계획·Wave 착수 시 반영: `CLAUDE.md` 「저장소 지도」·「패키지 경계」(**J1 산출물**) · `plans/121` 처리기 계약(두 번째 MCP 엔드포인트 — J5) ·
`plans/101`(`apm_*` 소비처가 `mcp_server`가 아니라 `apm_gateway` — 101 §4.3·§5.4 표기) · `plans/92`(G-12 직렬화기 · R-17) · `plans/55` §7(편입 방식 =
독립 게이트웨이) · `plans/78` W7-2단계 상태 · `plans/sre-agent/README.md`(하향 의존이 MCP 서버 둘).
**[v3.1] 등재·파급 완료(2026-09-29)** — **D-195 본문 등재**(①②③ · `docs/02_decision.md` `## D-195` · 「채번 이력」 D-195 행 `본문 등재 완료` · 「변경 이력」 1행) · D-003 부기(~~실행 평면 한정 예외 — D-195 ③~~ **[v3.3] 범위 밖 — 예외 아님 · D-195 ③ 별도 통제로 재기록**) ·
G-10 원본 수정(`mcp_server/mcp_server/om_exposition.py` · `plans/92` §5.1 O5 해소 부기) · R-18 소유 = `plans/121` §14.2 부기 · 파급 부기: `plans/101` · `plans/121` ·
`plans/92` · `plans/55` · `plans/78` · `plans/sre-agent/README.md`(각 파일에 "2026-09-29 · `plans/87` v3.1" 표지). D-168 예약 행(`plans/78` 소관)은 건드리지 않았다 —
2단계 위임 사실은 `## D-195` 본문에 적었다. `CLAUDE.md` 갱신은 여전히 **J1 산출물**이다.

---

## 12. 참고 문헌

> 서지 검증(2026-09-03): §12.1·§12.2는 arXiv API로 제목·저자·게재일을 실측했다. 게재처는 확인된 것만 적었다.
> v1.1은 문헌 조사 보고서(OpenAlex·Semantic Scholar 교차 검증 63건 — §12.5)의 **정정 2건**(RCACopilot 게재처 ·
> 철회본 arXiv 2511.15755 제외)과 게재처 확인분(ITBench·OpsEval·STRATUS·AlertGuardian·StepFly·Riddell)을 반영했다.
> 인용수는 OpenAlex 과소집계 문제(Plan 78 §11 실측)로 싣지 않는다 — 선정 기준은 본 계획과의 논리적 적합성이다.

### 12.1 동료심사 문헌

| 문헌 | 게재 | 식별자 | 반영 |
|---|---|---|---|
| Chen et al., Automatic Root Cause Analysis via Large Language Models for Cloud Incidents (RCACopilot) | **EuroSys 2024** | arXiv 2305.15778 · DOI 10.1145/3627703.3629553 | §4.1 P1 |
| Ahmed et al., Recommending Root-Cause and Mitigation Steps for Cloud Incidents using LLMs | ICSE 2023 | arXiv 2301.03797 | §4.1 P3 |
| Roy et al., Exploring LLM-based Agents for Root Cause Analysis | FSE 2024 Companion | arXiv 2403.04123 | §4.1 P4 |
| Wang et al., RCAgent: Cloud Root Cause Analysis by Autonomous Agents with Tool-Augmented LLMs | CIKM 2024 | arXiv 2310.16340 | §4.1 P2 |
| Jiang et al., Xpert: Empowering Incident Management with Query Recommendations via LLMs | ICSE 2024 | arXiv 2312.11988 | §4.1 P5 |
| Pei et al., Flow-of-Action: SOP Enhanced LLM-Based Multi-Agent System for RCA | WWW 2025 Companion | arXiv 2502.08224 | §4.1 P5 |
| Lee et al., Eadro: An End-to-End Troubleshooting Framework for Microservices on Multi-source Data | ICSE 2023 | arXiv 2302.05092 | §4.2 P8 |
| Zhang et al., Robust Failure Diagnosis of Microservice System through Multimodal Data (DiagFusion) | IEEE TSC 2023 | arXiv 2302.10512 | §4.2 P8 |
| Ding et al., TraceDiag: Adaptive, Interpretable, and Efficient RCA on Large-Scale Microservice Systems | FSE 2023 Industry | arXiv 2310.18740 | §4.2 P9 |
| Li et al., Causal Inference-Based RCA for Online Service Systems with Intervention Recognition (CIRCA) | KDD 2022 | arXiv 2206.05871 | §4.2 |
| Lin et al., Root Cause Analysis In Microservice Using Neural Granger Causal Discovery (RUN) | AAAI 2024 | arXiv 2402.01140 | §4.2 |
| Pham et al., RCAEval: A Benchmark for RCA of Microservice Systems with Telemetry Data | WWW 2025 Companion | arXiv 2412.17015 | §4.2·§6 J3 |
| Liu et al., Incident-aware Duplicate Ticket Aggregation for Cloud Systems (iPACK) | ICSE 2023 | arXiv 2302.09520 | §4.4 P12 |
| Kuang et al., Knowledge-aware Alert Aggregation in Large-scale Cloud Systems: a Hybrid Approach (COLA) | ICSE-SEIP 2024 | arXiv 2403.06485 | §4.4 P12 |
| Ghosh et al., Dependency Aware Incident Linking in Large Cloud Systems (DiLink) | WWW 2024 Companion | arXiv 2403.18639 · DOI 10.1145/3589335.3648311 | §4.4 P12 |
| Jin et al., Assess and Summarize: Improve Outage Understanding with LLMs (Oasis) | FSE 2023 Industry | arXiv 2305.18084 | §4.4 |
| IPIGuard · Task Shield · Adaptive Attacks (Plan 78 §11.1 검증 완료 — 재인용) | EMNLP 2025 · ACL 2025 · NAACL Findings 2025 | (Plan 78 §11.1) | §4.5 P13 |
| Xu et al., OpenRCA: Can Large Language Models Locate the Root Cause of Software Failures? | ICLR 2025 | https://github.com/microsoft/OpenRCA | §4.3 |
| Jha et al., ITBench: Evaluating AI Agents across Diverse Real-World IT Automation Tasks | ICML 2025 (PMLR 267) | arXiv 2502.05352 | §4.3 P10 |
| Liu et al., OpsEval: A Comprehensive Benchmark Suite for Evaluating LLMs' Capability in IT Operations Domain | FSE 2025 Companion | arXiv 2310.07637 · DOI 10.1145/3696630.3728572 | §4.3 · §6 J3 |
| Chen et al., STRATUS: A Multi-agent System for Autonomous Reliability Engineering of Modern Clouds | NeurIPS 2025 | arXiv 2506.02009 | §4.3 P11 |
| Riddell et al., Stalled, Biased, and Confused: Uncovering Reasoning Failures in LLMs for Cloud-Based RCA | FORGE 2026 (ICSE 워크숍) | arXiv 2601.22208 · DOI 10.1145/3793655.3793732 | §4.1 P6·P15 |
| Mao et al., StepFly: Agentic Troubleshooting Guide Automation for Incident Diagnosis | Proc. ACM Softw. Eng. (FSE 2026) | arXiv 2510.10074 · DOI 10.1145/3808143 | §4.1 P5 |
| Yu et al., AlertGuardian: Intelligent Alert Life-Cycle Management for Large-scale Cloud Systems | ASE 2025 | arXiv 2601.14912 | §4.4 |
| Yu et al., MicroRank: End-to-End Latency Issue Localization with Extended Spectrum Analysis in Microservice Environments | WWW 2021 | DOI 10.1145/3442381.3449905 | §4.2 P16 |
| Li et al., Practical Root Cause Localization for Microservice Systems via Trace Analysis (TraceRCA) | IEEE/ACM IWQoS 2021 | DOI 10.1109/IWQOS52092.2021.9521340 | §4.2 P16 |
| Yu et al., Nezha: Interpretable Fine-Grained Root Causes Analysis for Microservices on Multi-modal Observability Data | FSE 2023 | DOI 10.1145/3611643.3616249 | §4.2 P16 · §5.5 |
| Jump & McKinley, Cork: Dynamic Memory Leak Detection for Garbage-Collected Languages | POPL 2007 | DOI 10.1145/1190215.1190224 | §4.6 · §5.4-b |

### 12.2 Preprint (보조 근거)

| 문헌 | arXiv | 반영 |
|---|---|---|
| An et al., Nissist: An Incident Mitigation Copilot based on Troubleshooting Guides | 2402.17531 | §4.1 P5 |
| Unnikrishnan et al., FixItFlow: Automated Troubleshooting Guide Generation from Cloud Incidents | 2607.13035 | §4.1 |
| Wei et al., Agentic Root Cause Analysis through Evidence-Grounded Reasoning | 2607.22385 | §4.1 P6 |
| Luo et al., OpsAgent: An Evolving Multi-agent System for Incident Management in Microservices | 2510.24145 | §4.1 |
| Zhang et al., mABC: multi-Agent Blockchain-Inspired Collaboration for RCA in micro-services architecture | 2404.12135 | §4.1 P7 |
| Qiu et al., Blueprint First, Model Second: A Framework for Deterministic LLM Workflow | 2508.02721 | §4.1 P7 |
| Wang et al., A Comprehensive Survey on Root Cause Analysis in (Micro) Services | 2408.00803 | §4.2 |
| Chen et al., AIOpsLab: A Holistic Framework to Evaluate AI Agents for Enabling Autonomous Clouds | 2501.06706 | §4.3 P10 |
| Clark et al., SREGym: A Live Benchmark for AI SRE Agents with High-Fidelity Failure Scenarios | 2605.07161 | §4.3 P10 |
| Shan et al., RCA Copilot: Transforming Network Data into Actionable Insights via LLMs | 2507.03224 | §4.1 (참고) |
| Coding Agents Are Guessing: Action-Boundary Violations in Underspecified DevOps Instructions (Plan 78 재인용) | 2607.02294 | §4.5 |
| GIRA: Guarded Tool-Using LLM Agents for Incident Response — Safety-Gated Architecture (워크숍 투고본 2026-03 · 저자·채택 미확인) | [OpenReview LBt5eX6OKx](https://openreview.net/forum?id=LBt5eX6OKx) | §4.5 · §6 J6 지표 |

> ※ v1이 §12.2에 실었던 *Multi-Agent LLM Orchestration … Incident Response*(arXiv 2511.15755)는 **저자 철회본**으로
> 확인되어 v1.1에서 삭제했다(§4.1 P7 부기).

### 12.3 제니퍼 1차 자료 (J-번호)

- [J-1] SQL로 제니퍼 데이터 조회하기 — 자체 파일 DB·API 서버·JDBC(제니퍼소프트 기술 블로그, 2021-06) · https://jennifersoft.com/ko/blog/tech/2021-06-02/ — v2: 원문 표현은 *"사용자가 원하는 가공 형태의 데이터를 조회하는데 어려움이 있습니다"* (v1.1의 "직접 접근이 제한적"은 요약이었음)
- [J-2] 제니퍼 성능 데이터 RDB에 적재해서 활용하기 — RDB Export(Oracle/MySQL/PostgreSQL·테이블 목록·`server_view.conf`) · https://jennifersoft.com/ko/blog/tech/2021-07-19/ · 영문 테이블 노출 예시 https://jennifersoft.com/en/blog/tech/2021-08-23/
- [J-3] JENNIFER 4.5 매뉴얼(PDF) — 이벤트 유형 11장 · PLC 6.7 · 서비스 덤프 6.8 · 강제 GC 9.11.4 · 액티브 서비스 제어 13장 · 리포지토리 12.12 · https://cdn.jennifersoft.com/wp-content/uploads/Documents/ko/JENNIFER4.5_Manual.pdf (4.x 기준 — 5.x 대조는 U-1·U-8)
- [J-4] **(v2 정본 교체)** JENNIFER5 API Reference — **https://openapi.jennifersoft.com/** (OpenAPI 3.0.3 · `info.version` 5.6.4 · ~~52경로~~ 39경로·63오퍼레이션[v3.2] · `bearerAuth` · 설명 *"We are developing a new API v2. Existing Open APIs are not removed for compatibility, but are no longer maintained."*) · 원문: 리포 `jennifersoft/jennifer5-open-api` gh-pages `index.html`에 스펙 인라인(최종 push 2026-03-25) — 2026-09-17 직접 확인 · (구 사본·보조) `spec.json` https://github.com/jennifersoft/jennifer-developer-guide/blob/master/src/resources/spec.json · 개발자 가이드 https://jennifersoft.github.io/jennifer-developer-guide/
- [J-5] Open API v2 매뉴얼(Bearer·`auth-test`·`manage-rule-event*`(**[v3.2] 파일명 — 실제 경로는 `/api-v2/manage/rule/event/…` · [J-23]**)·`manual-rdb-export`·`deploy`) · https://github.com/jennifersoft/jennifer5-open-api-v2-manual — v2: 최종 push 2023-04-04(보조 자료 · 정본은 [J-4])
- [J-6] **(v2 교체)** 뷰 서버 확장 튜토리얼 · https://github.com/jennifersoft/jennifer-view-extension-tutorial (최종 push 2026-06-15 ✔) · (구) 어댑터 튜토리얼(TransactionHandler·EventHandler·EventData 필드) · https://github.com/jennifersoft/jennifer-view-adapter-tutorial/blob/master/README_ko.md
- [J-7] 지원 플랫폼(Java·.NET·PHP·Python·Node.js·OpenTelemetry) · https://jennifersoft.com/ko/product/apm/platforms/
- [J-8] Jennifer AI / Jennifer Insight(Anomaly Event·Metrics Correlation·Insight Chat = Open API tool 호출·폐쇄망 LLM) · https://jennifersoft.com/ko/blog/tech/2025-12-22-jenniferai-jenniferinsights/ · https://jennifersoft.com/ko/blog/tech/2025-02-10-jennifer-ai/
- [J-9] X-View·프로파일 소개(JENNIFER v5 소개서 PDF) · https://www.cywell-integration.com/files/02.WASJENNIFER_v5_.pdf
- [J-10] PLC 소개 · https://www.theteams.kr/teams/2747/post/68554
- [J-11] 제품 구성(Agent·Data Server·View Server·Repository) · https://www.fin-ncloud.com/marketplace/jennifer
- [J-12] MSA 모니터링(토폴로지·Call Chain·프로토콜) · https://jennifersoft.com/ko/blog/tech/2026-12-17-jennifer-msa-monitoring/
- [J-13] OpenTelemetry 연동 How-to(Collector → 제니퍼 트레이스) · https://jennifersoft.com/ko/blog/tech/otel-jennifer-howto/
- [J-14] PagerDuty 어댑터(레벨 매핑) · https://github.com/jennifersoft/jennifer-view-adapter-pagerduty
- [J-15] 제니퍼소프트 공개 리포지토리 목록(어댑터·플러그인·API 서버·JDBC) · https://github.com/orgs/jennifersoft/repositories · SNMP 어댑터 https://github.com/jennifersoft/jennifer-view-adapter-snmp (v2 확인: `event.SNMPAdapter` · 5.2.3+ · 레벨별 trap OID 기본 `1.3.6.1.4.1.27767.1.1` · 메시지 패턴 기본 time·domain·instance·level·name·value · community·대상 `127.0.0.1/162` 기본값 · 최종 push 2025-04-11) · API 서버 https://github.com/jennifersoft/jennifer5-api-server · JDBC https://github.com/jennifersoft/jennifer5-jdbc-driver
- [J-16] 릴리즈 노트 5.6.5 · https://docs.jennifersoft.com/ko/jennifer5_releasenote/5_6_5
- **v2 추가(2026-09-17 · `docs.jennifersoft.com`은 JS 렌더링이라 원문 markdown 엔드포인트 `/r/markdown/chapter/{bookId}/{chapterId}`로 직접 확인)**
- [J-17] JENNIFER5 설치 가이드 10장 「제니퍼 AI 설치 및 구성 (서버 및 브라우저 LLM, MCP, 도움말 챗봇)」 — MCP 연결(LLM 프록시 = MCP 서버 · Streamable HTTP `<llm-proxy-server:port>/mcp` · `X-Jennifer-Api-Url`·`X-Jennifer-Api-Token` · *"MCP 서버는 제니퍼 OpenAPI를 통해 데이터를 조회하므로"* · 도구 예시 · 토큰 발급 [설정 > JENNIFER 서버 > 인증토큰 발급] · 5.6.5+ · JDK 17 · `jennifer-llm-1.x.x.zip`) · https://docs.jennifersoft.com/ko/jennifer5_installation_guide/1cb5cd771533968d (원문 `…/r/markdown/chapter/f364f298fe59cc82/1cb5cd771533968d`)
- [J-18] 릴리즈 노트 **5.7.0**(릴리즈 날짜 2026-08-13) — *"stable `jvm.*` semantic convention 병행 인식"* · OpenTelemetry 메트릭·프로파일 조회 경로 보강 · javax→jakarta(Jakarta EE 11 · Spring 7 · Jetty 12) · `extension_allowed_packages` 등록 · PostgreSQL/MSSQL RDB Export 오류 수정 · https://docs.jennifersoft.com/ko/jennifer5_releasenote/5_7_0
- [J-19] 릴리즈 노트 **5.6.5**(릴리즈 날짜 2025-10-29) — 5.6.4.28 *"[뷰서버] 도메인 단위 인스턴스 GC 를 요청하는 오픈 API 추가 - /api-v2/manage/instance/<domain-id>/gc"* · 5.6.4.10 *"오픈 API를 이용한 데이터 서버 확장"*(도메인 관리·데이터 서버 제어) · 제니퍼 인사이트(서버 LLM·브라우저 LLM) · [J-16]과 같은 문서 · ※ 5.6.4 Hotfix 노트는 GC API를 5.6.4.27로 표기(△ 버전 표기 상이 — 조사 보고)
- [J-20] 릴리즈 노트 **5.6.3**(릴리즈 날짜 2024-01-17) — *"인증 토큰 추가시 사용량 제한을 0으로 설정할 경우, 무제한 토큰으로 동작"* · *"오픈 api 를 비활성화 하기 위한 뷰서버 비공식 옵션 추가"* · *"카프카 트랜잭션 Export 기능 추가"*(5.6.2.7) · *"RDB Export 분 단위 애플리케이션 통계 추가"* · https://docs.jennifersoft.com/ko/jennifer5_releasenote/5_6_3
- [J-23] (v3.2) **Open API 공식 스펙 대조**(2026-09-29 · 메인 세션) — 정본: https://raw.githubusercontent.com/jennifersoft/jennifer5-open-api/gh-pages/index.html (리포 https://github.com/jennifersoft/jennifer5-open-api · gh-pages 커밋 `0152c7b4` 2026-03-25 "Deploy OpenAPI docs" · `CNAME` = `openapi.jennifersoft.com` · DNS CNAME → `jennifersoft.github.io`) — `SwaggerUIBundle({spec: …})`에 인라인된 OpenAPI 3.0.3 JSON(5.6.4 · **39경로 · 63오퍼레이션**)을 추출해 전수 파싱. `openapi.jennifersoft.com` 직접 접속은 이 환경에서 타임아웃. 보조: v2 매뉴얼 `jennifer5-open-api-v2-manual`(README + `spec/*.md` 12건) · 구 사본 `jennifer-developer-guide` `spec.json`(Swagger 2.0 · 25경로) · 릴리즈 노트 5.6.0~5.7.0.1(목차 API `https://docs.jennifersoft.com/r/viewer/chapters/df108332addbd85d` · 5.7.0 Hotfix 장 `744135e0974e7208`) · 설치 가이드 3장(뷰 서버 `server_port` 기본 7900)·10장. **실 제니퍼 서버 호출 0회**(선언 스펙 기준). 판정 52건: 일치 23 · 정정 24 · 스펙에 없음 3 · 확인 불가 2 — 반영은 §0.9
- [J-24] (v3.2) **제니퍼 Docker 로컬 설치 조사**(2026-09-29 · 메인 세션) — Docker Hub `jennifersoft`(제품 이미지 없음 · https://hub.docker.com/u/jennifersoft) · 공식 샘플 https://github.com/jennifersoft/jennifer-container-sample (2023-02-07 · 데이터+뷰 서버 한 컨테이너 · JDK 8) · https://github.com/jennifersoft/jennifer-install-script (2021-12-22) · 공개 설치본 `https://jennifer5-release-public.s3.ap-northeast-2.amazonaws.com/server/jennifer-server-<버전>.zip`(5.7.0.1 · 590,809,047 bytes · HEAD 실측 · 약관 적용 확인 불가) · 평가판 https://jennifersoft.com/ko/jennifer-trial/ (2주 · IP 기반 · 신청 폼 Agent IP·Server IP · 사이트당 1회) · 설치 가이드 1~4장(Bootstrap Check · 포트 7900/5000 · 에이전트 부착 · JDK) · 릴리즈 노트(서버 JDK 17/21 · 에이전트 JDK 8~26 · arm64는 Python·Node.js·.NET 에이전트만 명시) · Docker Hub `eclipse-temurin`·`tomcat` 태그(arm64 v8) · 데모 서버 `java.jennifersoft.com`·`dev.jennifersoft.com`(auth-test 401 실측) · 저장소 `tests/conftest.py:85-86`(사설·루프백 허용). 원천 파일 §6(6.1~6.7) · 미확인은 §5
- [J-22] (v2.1) JENNIFER5 설치 가이드 11장 「제니퍼 AI 데이터 보안 정책 안내서」 — *"제니퍼 AI 는 제니퍼 Open API 의 모니터링 데이터를 자동으로 분석하는 기능"* · 읽는 데이터 범위(*"도메인, 인스턴스 목록, 도메인, 인스턴스 별 성능 메트릭, 트랜잭션, 스택 트레이스, 프로파일 텍스트 (SQL 파라미터 제외), 에러, 이벤트"*) · 공개 프록시 `insight.jennifersoft.com`(*"최대 30 일간 보관"*) · *"기본적으로 고객사의 폐쇄망 내부에 자체 설치된 거대 언어 모델(LLM)을 사용하도록 설계"* · https://docs.jennifersoft.com/ko/jennifer5_installation_guide/da3799f39e86eb9f (원문 `…/r/markdown/chapter/f364f298fe59cc82/da3799f39e86eb9f`) — 2026-09-17 직접 확인. ※ [J-17] 10장의 프록시 설정 키(`server.llm/conf/server_llm.conf` · `llm_proxy_model_provider` 등)·설치본 파일명 표기 *"jennfer-llm-1.x.x.zip"* 도 같은 날 원문 확인(획득 경로 기재 없음)

### 12.4 산업 자료·표준 (V·S·W-번호)

- [V-1] Dynatrace MCP 서버(GA) · https://docs.dynatrace.com/docs/dynatrace-intelligence/dynatrace-mcp
- [V-2] Datadog MCP 서버 · https://www.datadoghq.com/product/ai/mcp-server/
- [V-3] New Relic AI MCP 서버 · https://newrelic.com/blog/news/new-relic-ai-mcp-server-launch
- [V-4] WhaTap MCP 서버 · https://docs.whatap.io/mcp/how-to-use
- [V-5] Scouter Web API 가이드 · https://github.com/scouter-project/scouter/blob/master/scouter.document/tech/Web-API-Guide_kr.md
- [S-1] Google SRE Book — Managing Incidents · https://sre.google/sre-book/managing-incidents/
- [S-2] Google SRE Workbook — Incident Response · https://sre.google/workbook/incident-response/
- [S-3] Making Facebook Self-Healing (FBAR) · https://engineering.fb.com/2011/09/15/data-center-engineering/making-facebook-self-healing/
- [W-1] Apache Tomcat 9 — The Executor (thread pool) · https://tomcat.apache.org/tomcat-9.0-doc/config/executor.html
- [W-2] Apache Tomcat 9 — JDBC Connection Pool · https://tomcat.apache.org/tomcat-9.0-doc/jdbc-pool.html
- [W-3] HikariCP — About Pool Sizing · https://github.com/brettwooldridge/HikariCP/wiki/About-Pool-Sizing
- [W-4] Oracle — HotSpot Virtual Machine Garbage Collection Tuning Guide · https://docs.oracle.com/en/java/javase/17/gctuning/
- [W-5] OpenTelemetry Semantic Conventions — JVM 런타임 메트릭 · https://opentelemetry.io/docs/specs/semconv/runtime/jvm-metrics/ · DB 클라이언트 메트릭 · https://opentelemetry.io/docs/specs/semconv/database/database-metrics/
- [W-6] Oracle WebLogic Server 12.2.1.3 — Avoiding and Managing Overload(Stuck Thread · `StuckThreadMaxTime` 기본 600초) · https://docs.oracle.com/middleware/12213/wls/CNFGD/overload.htm
- [W-7] TmaxSoft JEUS 9 웹 엔진 안내서 — 스레드 풀(`max-thread-active-time` · Blocked Thread 통지) · https://docs.tmaxsoft.com/ko/jeus/9/web-engine-guide/chapter-thread-pool.html — △ 조사 시 호스트 접근 불가(검색 스니펫 기반) · **운영 JEUS 버전 문서로 재확인 필요**
- [W-8] Oracle Java SE 21 Troubleshooting Guide — Troubleshoot Memory Leaks(OOM 메시지 7종 · `HeapDumpOnOutOfMemoryError` · `jcmd GC.heap_dump`) · https://docs.oracle.com/en/java/javase/21/troubleshoot/troubleshooting-memory-leaks.html · Troubleshoot Process Hangs and Loops(스레드 덤프·데드락) · https://docs.oracle.com/en/java/javase/21/troubleshoot/troubleshoot-process-hangs-loops.html
- [S-6] (v2) SolarWinds OrionSDK — *"Why get data from SWIS instead of just querying the Orion database directly?"* (스키마 진화를 API 매핑이 흡수 · DB 자격증명 대신 콘솔 권한 체계) · https://github.com/solarwinds/OrionSDK/blob/gh-pages/docs/about-swis/index.md — 모니터링 벤더가 내부 저장소 직접 조회 대신 API 경유를 권하는 1차 전례(§0.4 근거 5)
- [OM-1] (v2.1) OpenMetrics 1.0 — *"Version: 1.0 | Status: Published | Date: November 2020"* · *"This standard expresses all system states as numerical values … Contrary to metrics, singular events occur at a specific time."* · Content-Type `application/openmetrics-text; version=1.0.0; charset=utf-8` · *"Expositions MUST end with EOF"* · 단위 접미사 MUST · *"MetricPoint timestamps should not be exposed"* · https://prometheus.io/docs/specs/om/open_metrics_spec/ (원본 저장소 `prometheus/OpenMetrics`) — 2026-09-17 직접 확인
- [OM-2] (v2.1) OpenMetrics 2.0 **[EXPERIMENTAL]** — *"we reserve the right to break the compatibility if it's necessary"* · `version=2.0.0` · `_total`·단위 접미사 필수→권고 · `_created`→인라인 `st@` · *"Around 2024, the OpenMetrics project was incorporated under the CNCF Prometheus project umbrella"* · https://prometheus.io/docs/specs/om/open_metrics_spec_2_0/ — 2026-09-17 직접 확인
- [OM-3] (v2.1) OpenTelemetry Specification v1.59.0 — Prometheus and OpenMetrics Compatibility(점 등 비권장 문자 → `_` · UCUM 단위 → 단위 접미사 · 단조 합계 `_total` · 속성 → 라벨 · *"The resulting unit SHOULD be added to the metric as UNIT metadata"*) · https://github.com/open-telemetry/opentelemetry-specification/blob/v1.59.0/specification/compatibility/prometheus_and_openmetrics.md — 2026-09-17 직접 확인
- [S-4] Introducing Nurse: Auto-Remediation at LinkedIn (2015) · https://engineering.linkedin.com/sre/introducing-nurse-auto-remediation-linkedin — △ 현재 404, 검색 스니펫으로 내용 확인
- [S-5] Automate Java performance troubleshooting with AI-powered thread dump analysis on Amazon ECS and EKS (AWS Containers Blog, 2025-12) · https://aws.amazon.com/blogs/containers/automate-java-performance-troubleshooting-with-ai-powered-thread-dump-analysis-on-amazon-ecs-and-eks/ — "알람 → 덤프 수집 → LLM 요약 → 보고서(자동 교정 없음)"의 산업 표준형
- HolmesGPT(CNCF Sandbox) · https://github.com/HolmesGPT/holmesgpt — `Toolset.approval_required_tools`(sre-agent/02 §9 실측 · 본 계획 미채택 근거 §4.5)

### 12.5 내부 참조

- `plans/55`(멀티소스 로드맵 · §5 C-1~C-5 · §7 선행 결정) · `plans/78` §3.3(조치 위험·통제 문헌)·§4.7.1~4.7.3(미들웨어 방식·APM 편입 경로·체크리스트)·W7·§7.1·§8.3 · `plans/64` §7~§8(L3 통제·조치 거버넌스) · `plans/sre-agent/02` §9(human-gated) · `docs/25_host_investigation_load_guard.md` · `config/middleware_signatures.yaml`(선언적 정책 파일 전례)
- 조사 보조 텍스트(스크래치패드, 저장소 밖 · 세션 종료 시 소멸): 4.5 매뉴얼 추출본 `j45.txt` · v5 소개서 추출본 `cywell.txt` ·
  문헌 조사 보고서 `literature_report.md`(63건 카드 · OpenAlex/S2 인용수 병기 · 미확인·정정 목록) · 저장소 실측 보고서 `repo_report.md`
- v2 조사(2026-09-17): 조사 서브에이전트 2건(제니퍼 공식 연동 표면 — 릴리즈 노트 72편·설치 가이드 11장·공개 리포 45개 검색 /
  "표준 연동 규격" 후보·국내 연동 관행 — 공개 RFP·구축 사례에서 제니퍼 연동 방식 명시 문서 0건). 계획에 반영한 제니퍼
  주장은 [J-4]·[J-17]·[J-18]·[J-19]·[J-20]·SNMP 어댑터·GitHub push 일자를 **원문으로 재확인**했고, 재확인 못 한 것은 △로 남겼다

---

## 13. 변경 이력

| 일자 | 내용 |
|---|---|
| 2026-09-03 | **v1** — 최초 작성. 제니퍼 기능·연동 지점 조사(§2, 출처 16건) · 저장소 실측(§3) · 문헌 33편 서지 실측(§4·§12) · 전제 정정(§0.2 — 파일 DB → RDB Export+API+이벤트 3중) · 아키텍처(§5) · Wave J0~J6(§6) · 게이트 G-1~G-7(§10) · D-195 예약 등재(작성 중 D-194가 `plans/50` v2.2로 본문 등재됨 — 번호 연속 확인) · 파일명 `-TODO` 접미사(INDEX 「파일명 상태 접미사」 규칙, 코드 0건) |
| 2026-09-03 | **v1.1** — 서브에이전트 보고 반영. ① **정정 2건**: RCACopilot 게재처 ICSE→**EuroSys 2024** · 철회본(arXiv 2511.15755) 인용 삭제 · DiLink 표기 정정 ② **폴스타 스키마의 제니퍼 연동 필드 발견**(`was_connection.jennifer_*` · `was_object.agent_id/hostname/obj_name`) → §3.3 실측 · §5.3 **1순위 브릿지** · U-10 · R-1 완화 ③ 벤치마크 정량 근거(OpenRCA 11.34% · ITBench 13.8%) → L3 범위 밖 ④ 문헌 추가(MicroRank·TraceRCA·Nezha·GIRA·Nurse·JDK 21 Troubleshooting·WebLogic·JEUS·Cork) → **P15~P17** 신설(반증·정체 가드 / 결정적 후보 축소 / 일반 완화 분리) · §4.6 WAS 시그니처 임계 구체화(stuck 600s · OOM 7종 · old gen 계단 상승) ⑤ 병렬 작업 주의(§3 — D-194 작업이 같은 파일 수정 중) · 착수 시 SDD 양식(§6) |
| 2026-09-17 | **v2** — 사용자 지시 *"db가 아닌 제니퍼 api를 사용하거나 표준 연동 규격으로 연동"* 에 따른 재조사. ① **SQL(RDB Export 적재본) 경로 철회 → Open API 단일 조회 경로**(§0.4 근거 6건 · 빼는 비용 3건 · G-1 권고 ⓒ→ⓐ) ② **"표준 연동 규격" 4갈래 분해**(제니퍼 공식 규격 채택 · 업계 표준은 SNMP trap·Kafka·MCP만 송신 가능, OTLP 수신만·Prometheus/OpenMetrics ✖ · 국내 공공·금융 표준 근거 없음 · 조직 내부 규격은 **G-2 개정**으로 사용자 확인) ③ **정정 6건**: 공식 MCP 서버 ✖→✔(§2.7·§2.8 신설·**G-8**) · 강제 GC API ✖→도메인 단위 ✔ · Kafka 트랜잭션 Export ✖→✔ · OTel 메트릭 수용 ✖→△ · [J-1] 인용 문구 · 정본 스펙 `spec.json`→`openapi.jennifersoft.com`(5.6.4 · v1 "no longer maintained") ④ **통제 격상**: 같은 토큰에 쓰기·제어 API 공존 실측 → GET·경로 허용목록을 1차 통제로(§8.1·§8.2 · R-4) · 토큰 사용량 제한(§8.4) · 폴스타 저장 토큰 재사용 금지 ⑤ 이벤트 2단계 = **2-A SNMP trap 공식 어댑터 / 2-B 커스텀 어댑터**(§5.5 · G-4) ⑥ 질의 경로 `backend: sql`→**`mcp`**(DB 등록 철회 · Plan 82 Wave 7 선행 · G-5) ⑦ Wave 재편(J1=API 기반·J2=도구 표면 직렬) · U-2·U-3·U-5·U-8·U-9·U-10 개정 · U-11·U-12 신설 · R-2 개정 · R-12~R-14 신설 · 출처 [J-17]~[J-20]·[S-6] 추가(원문 재확인) · 최신 버전 5.7.0(2026-08-13) 반영 ⑧ `docs/02` D-195 예약 행 · `plans/INDEX.md` 87행 갱신. 코드 0건 유지 → `-TODO` 유지 |
| 2026-09-17 | **v2.1** — 사용자 확정 *"표준 연동 규격은 cncf openmatric에 정의된 규격을 말한다. api 위주로 가자. 3번은 실측하여 판정하라."* ① **G-2 확정 = CNCF OpenMetrics**: §0.4 (2) 네 갈래 표를 OpenMetrics 실측 O-1~O-5로 교체(1.0 Published·2.0 Experimental · 규격 범위 = 수치 지표만 · 제니퍼 정본 스펙에 OpenMetrics/Prometheus/OTLP 0건 → 네이티브 노출 ✖) → **선택 트랙 J7**(§5.9 `GET /metrics/apm` OpenMetrics 1.0 브리지 · `plans/92` B-2 기계 공유 · **G-9** 신설) · R-14 해소 · R-15 신설 ② **G-1 확정 = API 위주(ⓐ)** · 이벤트 2단계 push에서 "표준" 라벨 제거 → 폴링 부족이 실측될 때만 착수(§5.5 · G-4) ③ **G-8 실측 판정 = 공식 MCP 미채택 · Open API 직접 호출 확정**(§0.5 신설 — M-1 LLM 프록시 운영 전제 · M-2 권한 이점 0 · M-3 계약 가시성 상실 · M-4 공개 프록시 모드 · M-5 데이터 범위 · M-6 `mcp` 1.29.1 헤더 주입 가능 실측. 런타임 `tools/list`는 설치본 비공개·`insight.jennifersoft.com` 302→/login으로 **실측 불가를 명시**하고 최선값 가정에서도 결론 불변 확인 · 재검토 트리거 ⓐ~ⓒ) → §5.2(d) 백엔드 인터페이스 삭제(단일 구현 추상화 금지) · U-11 축소 · §2.8 표 판정 갱신 ④ 출처 [J-22]·[OM-1]~[OM-3] 추가(원문 확인) ⑤ `docs/02` D-195 예약 행 · `plans/INDEX.md` 87행 갱신. 코드 0건 유지 → `-TODO` 유지 |
| 2026-09-22 | **v2.1 부기** — `plans/92` v3 재검토(사용자 지시 *"고치지 않은 것들을 권고에 맞게 모두 수정하라"*)의 교차 정정. ① **§5.9 J7 `nodename` 어휘 정정**: 값은 폴스타 `server_name`이다(D-119 ③ `hostname(=server_name)` · `promql_tools.py:447` · 92 B-2와 같은 키). `apm_instance_map`이 해소한 OS hostname을 `cmm_resource.name`으로 결정적으로 역해소하는 단계를 추가했다(D-046 역방향 · 0건·다건이면 `match_confidence="none"` · 조사 경로 hostname 해소는 무변경) ② **§5.9 부기**: `custom_route` 핸들러는 SSE 세션 lifespan 자원에 닿지 않는다 → 브리지 전용 지연 자원(92 v3 §4.5 착수 조건 1과 같다) ③ §11 파급 목록의 `plans/92` 머리말 항목 = **반영 완료** 표기. 게이트·범위 변경 없음 · 코드 0건 `-TODO` 유지 |
| 2026-09-29 | **v2.2** — 사용자 지시 *"현재 구현을 검토하여 87번 제니퍼 연동 계획서를 업데이트하고 연동 가이드를 상세하게 docs 폴더에 작성하라."* ① **§0.6 신설** — 계획서가 기대거나 전제한 코드 41항목을 HEAD `2e635a9`+작업 트리로 재실측(✔ 성립 · ✏ 정정 · ➕ 보강 · ⚠ 불일치). 제니퍼 코드 0건 재확인 → `-TODO` 유지 ② **전제 정정 4건**: 질의 경로 선행 Plan 82 Wave 7 → **`plans/121` TP-9.1·9.2·10.5**(D-270 ⑯ 흡수) · 운영 원격 조사 bash 없음(D-233) → "OS 근사 폴백" = 폴스타 MCP 도구, W7-1 자산 프로덕션 호출부 0 · `fault_diagnosis`는 3단 전용(2단·1단 미도달) · 공유 노출 기계가 OpenMetrics **2.0**을 협상(prometheus-client 0.26.0 실측 — G-2 "1.0 고정"과 불일치, 92 소유) ③ **보강**: `plans/92` B-2 노출 기계 구현 완료(J7 공유 대상 실재) · 2단 `inspect_host` MCP 배관 선례 · 능력 카탈로그(D-224 ①) · `allowed_sources`(D-270 ⑰) · `.env.example` 커버리지 테스트 · overfit 제외 전례 · `respx` 미설치 → `MockTransport` · 감사 형식 = PromQL `_audit` 선례 · 인용 판정 배선 변경 불필요 · 사전수집 확장 지점 · 목업 생성기 경로 ④ **신규 위험** R-16(제니퍼 이벤트 kind 분류 충돌 — 두 분류기 직접 호출 실측) · R-17(OM 2.0 협상) · R-18(2단 pull 조사 미도달) · 신규 미결 U-13 · 게이트 **G-5′**(질의 경로 방식) · **G-9 권고 갱신**(①′) · **G-10**(OM 버전 상한) ⑤ 17일 이후 결정 반영(D-229·D-233·D-240·D-244·D-251·D-255·D-270·D-272·D-209 ⑥) — `spec/` 위치 · 매뉴얼 동반 · MLX 검증 경로를 §6 수용 기준에 추가 ⑥ 운영자 연동 가이드 **`docs/31_jennifer_integration_guide.md`** 신설 · J0 보고서 번호 정정(`docs/29` 선점). **확정 사항 G-1·G-2·G-8 불변 · D-195 예약 범위 불변 · 신규 D-번호 없음** |
| 2026-09-29 | **v3** — 사용자 지시(순서대로) *"제니퍼 연동은 sre_agent처럼 별도의 소스로 구분하여 구현하도록 계획이 되어 있냐?"* → *"…제니퍼 연동기능은 별도의 패키지 형식으로 분리하는 것에 대해 검토하라."* → *"권고에 맞게 계획을 수정하라."* **채택안 B — 독립 최상위 패키지 `apm_gateway/`(자체 MCP 서버 · 독립 프로세스)**. ① **D-274 등재**(D-119 ① 개정 — 도메인 판정·별도 자격증명을 가진 관측 소스는 독립 게이트웨이 패키지 가능 · 얇은 래퍼는 `mcp_server` 확장 유지) · `docs/02` D-119 개정 부기 · D-195 예약 ① 정정 ② **§0.7 신설**(결정 · 채택 근거 5 · 구조 · 경계 불변식 · 이동/잔류 표 · 소비자 연결 · 폴스타 정합 브릿지 = 게이트웨이 → `mcp_server` MCP · 자격증명 배치 · §2.8 ③ 기각 사유 재해석 · 비용·완화) ③ `[v3]` 정정: §0.1·§0.3·§0.6 읽는 법·§1.2·§2.8(④ 확정 · ① 대체 · ③ 재해석)·§3.1·§3.5 G1·§5.1 그림·§5.2·§5.3·§5.4(b)(WAS 판정 → 게이트웨이 `domain/`)·§5.5(폴러 → 게이트웨이 · `alarm:raw` 생산자)·§5.6(두 번째 MCP 엔드포인트)·§5.8·§5.9(노출 주체 = 게이트웨이)·§6(v3 의존 그림 · Wave 재정의 — J1 = 패키지 골격·MCP 서버·Bearer·감사·경계 불변식 · v3 수용 기준)·§7(§7.0 v3 배치·`.env` 키 = `apm_gateway/.env` · §7.1 이력)·§8.1·§8.2·§11(예약 정정·v3 파급) ④ 게이트: **G-4 폴링 경로 해소** · G-4b(push 착수·방식·수신 위치) · G-11(품질 게이트 편입) · G-12(J7 직렬화기 복제/추출) 신설 ⑤ 위험 R-19(감사 분산)·R-20(운영 대상 추가)·R-21(복제 드리프트)·R-22(게이트웨이 → `mcp_server` 의존) 신설 · R-10 해소. **G-1·G-2·G-8 유지 · G-3·G-5′·G-6·G-7·G-9·G-10 유지 · 코드 0건 → `-TODO` 유지 · `CLAUDE.md` 갱신은 J1 산출물** |
| 2026-09-29 | **v3.1** — 사용자 지시 *"권고에 맞게 진행하고 미결사항은 해결하라."* · G-6 AskUserQuestion 「권고대로 확정 (Recommended)」 · *"제니퍼도 도커로 설치하여 검토할 수 있도록 계획에 포함시켜라."* ① **게이트 전건 확정** — G-3(`apm_*`) · G-4b · G-5′(ⓐ) · **G-6**(카탈로그 3종 · `remediation/` · D-195 ③) · G-7 · G-9(①′) · G-10(①) · G-11(+ `arch_check` 판정 기준) · G-12(① 복제) · 해석 5건 채택 · R-16·U-13 확정안 — §10 [v3.1] 확정 요약 ② **D-195 본문 등재**(①②③ · 예약 번호 같은 번호 소진 관례) · D-003 부기 ③ **G-10 코드 수정**: `mcp_server/mcp_server/om_exposition.py` `_cap_openmetrics_version` — OpenMetrics 요청 버전을 1.0.0으로 상한(TDD — 신규 테스트 9건 · `mcp_server` 591 → 600 passed · 11 skipped) · `plans/92` O5 해소 부기 · `docs/18` 1행 · 92 소유 코드라 87 착수 아님(`-TODO` 유지) ④ **R-18 소유 = `plans/121`**(작업 트리 실측 — 2단 조사 위임 미존재 · 121 §14.2 부기) ⑤ **파급 부기** 101·121·92·55·78·`sre-agent/README` ⑥ **로컬 Docker 검증 환경** — §0.8 신설(J0-L/J0-O 분할 · 구성 · 배치 `apm_gateway/testdata/jennifer/` · 통제(127.0.0.1 · 점유 포트 실측 · `RUN_DOCKER_IT=1` · 사용자 명시 다운로드) · U 분류 · 목 서버 폴백 · G-8 관계) · §6 J0-L/J0-O 행·의존 그림·수용 기준 · §7.0 행 · R-23~R-25 · 제품 사실은 「조사 대기」. 남은 선행 = J0-O 외부 전제 · Open API 경로 대조 · Docker 사실 조사 |
| 2026-09-29 | **v3.2** — Open API **공식 스펙 대조 반영**(메인 세션 대조 결과 52건 · 원천 [J-23] · 실 서버 호출 0회). ① **§0.9 신설** — 설계 판단 5건: 룰 이벤트 경로 정정(`manage-rule-event`는 파일명)·임계 변경 API 없음 → L2 덤프·PLC 실행 채널은 J0 확인·불가 시 허용목록 스크립트(G-6 확정 불변) · **허용목록 = 메서드 + 경로 템플릿 정확 일치 · 그 밖 전부 거부**(민감 GET·쓰기 11건+) · 폴러 필수 파라미터·`eventId` 부재 → 합성 멱등 키·`metricsName` · `apm_transaction_profile` = `domain_id`·`txid`·`time`(`profile_ref`) · `/api/status/*` 시 단위 → `apm_slow_transactions` 1차 뒷단 X-View 1분 분할 ② 정정 24 · 스펙에 없음 3(스레드 풀 상한 · 백분위 · 임계 변경 API) 반영 — 52경로 → 39경로·63오퍼레이션 · 비활성 옵션 이름 · 5.7.0.1·SNI · `heapUsed` MB · `gcTimeUsage` % · `activeDBConnection` ✔ · deploy API ✔(허용목록 추가) · `max_row` 기본값 출처 · EventData 필드 ③ **§5.2(e) 허용목록 정본 표**와 J1 거부 테스트 입력(비GET 9 · 민감 GET 9 · 변형 5) ④ **§5.3 정합 순서 재판정** — `Instance.hostName` 직접 대조를 `was_object`보다 앞(R-1 완화 후보) ⑤ 확인 불가 2건 → U-2(J0-L)·U-8 · **U-14 신설**(시간대·X-View 전수 여부) · U-1·U-5·U-12·U-13 보강 ⑥ D-195 ① 정정 부기(허용목록 정확 일치). D-003 부기·G-6 기록 방식(사용자 결정 대기)과 Docker 「조사 대기」 칸은 건드리지 않았다 |
| 2026-09-29 | **v3.2 부기 — Docker 로컬 설치 조사 반영**([J-24]) — §0.8을 조사 결과로 다시 썼다: 공식 이미지 없음 → JDK 17/21 Dockerfile 신규 · 데이터+뷰 서버 한 컨테이너 · 포트 이중 구조(127.0.0.1:17900→7900 · 5000 비게시) · 라이선스 IP = compose 고정 서브넷(벤더 확인) · Bootstrap Check off(로컬 한정 · R-26) · arm64 네이티브 먼저(R-24) · 샘플 앱 계획 편입 · 설치본·라이선스 저장소 밖(예정 키·`.gitignore`) · **J0-L-a/b 2단계 + 2주 체크리스트** · 사용자 할 일 8항목(J0-L-b 착수 조건) · 벤더 데모 서버는 선택지(외부 호출 건별 승인). 「조사 대기」 칸 전부 해소 — 답이 없는 칸은 「확인 불가」(Mac 라이선스 IP · Bootstrap 가상 환경 감지 · 장기 라이선스 · 직접 링크 약관 · LLM 프록시 포함 여부 · 스펙 예시 포함 여부 · 데모 계정). §6 J0-L 행·주석·수용 기준 · §7.0 폴더 행·키 · R-23~R-25 갱신 · R-26·R-27 신설 · [J-24] |
| 2026-09-29 | **v3.3** — 사용자 지시 *"도커에 제니퍼를 설치하여 현재 계획이 정상적인지 직접 테스트를 진행하여 계획을 업데이트하라."* · AskUserQuestion 확정 2건(설치본 = 「공개 S3에서 받기」 · 「라이선스 없이 먼저 진행」). 메인 세션이 로컬 Docker에 제니퍼 5.7.0.1 + 에이전트를 붙인 샘플 WAS를 띄워 **J0-L-a**를 실측(Open API 40건 · `apm_gateway/testdata/jennifer/`). ① **§0.10 신설** — 실측 19항목(✔ 7 · ✏ 3 · ➕ 8 · 미해소 1) · 정정한 전제 표 · J0-L-a 진행 상태 ② §0.8 정정 — Bootstrap Check 기본 유지(`DISABLE_BOOTSTRAP_CHECK` 옵트인) · WAS = `tomcat:9.0-jdk11-temurin`(배포 에이전트 5.5.2.5 ↔ JDK 17 불가) · compose `.env` 키 = `JENNIFER_DIST_DIR` 외 실제 키 · 라이선스 거부 = 에이전트 로그 · `JENNIFER_*`·`db.lock` 통제 행 · 사용자 할 일 3·4·5·6 완료 · **9(최신 Java 에이전트) 신설** ③ §5.2 — 오류는 HTTP 500 + `exception.message`로 분류(「예정 이름」 `source_unavailable`·`contract_violation`·`apm_api_error`) · `profile.txt` Accept `text/plain` · 지표 식별자 두 체계 매핑 표(J2) · 허용목록 거부 규칙의 실측 근거 ④ §5.5 폴러 오류 처리·호출 예산 · §8.1·§8.4 · §5.4(b) 에이전트 부착 실패는 시그니처 밖 ⑤ 리스크 — R-24 서버 해소 · R-26 철회 · R-27 사용자 수용 · **R-28·R-29 신설** ⑥ U-2·U-5·U-12 부분 해소 ⑦ §7.0 `.env` 키 정정 · §6 J0-L 수용 기준 충족 현황 ⑧ `-TODO` 유지 판단(§10). **G-1·G-2·G-8 · D-274 · D-195 ①② · `apm_*` 8종 · 허용목록 정본은 바뀌지 않았다.** `apm_gateway/testdata/jennifer/`의 파일·코드는 메인 세션 소유라 이 개정에서 수정하지 않았다 |
| 2026-09-29 | **v3.3 부기 — D-003 재기록**(사용자 확정 "권고" — 메인 세션 제시안 1(권고)·2(현행 유지)·3(G-6 철회) 중 1) — L2(WAS 조치 실행)는 DB 쓰기가 없으므로 **"D-003 예외"가 아니라 "D-003 범위 밖"**이다. "절대 변경 불가" 결정에 예외 선례를 남기지 않는다. 실행 평면은 D-195 ③이 별도 통제한다(통제 목록 불변). G-6 확정(범위·카탈로그·착수 조건)은 그대로이고 **기록 방식만** 바뀌었다. 정정 위치: §0.1 표 · §0.3 · §0.9 (3) · §5.7 L2 행·조건 ① · §10 G-6 행·확정 요약 · §11 D-195 행·등재 문단 · `docs/02` D-003 부기(교체) · `## D-195` 헤더·③·관련 · 「채번 이력」·「변경 이력」 D-195 행(취소선 보존) + 「변경 이력」 1행 · `docs/31` · `plans/78` §8.3 조건 ① 부기 1줄 |
| 2026-09-29 | **v3.3 부기 — 목 서버·녹화 하네스 반영**(사용자 지시 *"목 서버와 녹화 하네스도 만들어라"* · 산출물은 메인 세션 소유 · 미커밋) — `scripts/jennifer_catalog.py`(허용목록 16템플릿 사본 · `match_template()` 정확 일치) · `masking.py` · `record_openapi.py`(GET만 · 네트워크 전 거부 · variant 저장) · `mock_openapi.py`(fixtures·connected·disconnected · 제어 경로 `/__mock/*`) · `recorded/local-docker/` 21건 · `apm_gateway/tests` 22건(재실행 확인 22 passed). 로컬 실서버 ↔ 목 서버 40건 **불일치 0**. 반영: §0.8 (2) 두 행 【현재 가능】 · (6) 폴백 재작성(합성 응답 없음 · 이벤트 주입 · 한계 · 카탈로그 사본 ↔ J1 정본 대조) · (9) J0-L-a ①~⑥ 완료(④ 재녹화 예정 · 부하 재현 스크립트는 J0-L-b) · §0.10 #20 · (4) · §6 [v3.3 추가 기준](사본 대조 테스트 · `/__mock/hits` 허용목록 밖 0회 · 루트 수집 편입 판정) · §7.0 · R-23 · §10 G-11 판정 항목 · `-TODO` 판단 문구 |
| 2026-09-29 | **v4 — J1~J4 구현**(사용자 지시 *"87번 계획을 구현하라."* · 작업 트리 · 커밋 없음) — 착수 판정: J1~J4 구현 · J5 보류(`plans/121` TP-9.1·9.2·10.5 코드 0) · J6 보류(G-6 "J3 완료 + 목업 검증 뒤" — 목업 = J0-L-b 로컬 Docker 재현 · U-8) · J7 보류(G-9 소비자 미확정) · J0-L-b·J0-O 사용자·외부 전제. ① 게이트웨이 `apm_gateway/`(허용목록 정본 16템플릿 · 클라이언트 · 정합 · `apm_*` 8종 + `gateway_health` · WAS 판정 · 폴러 · MCP 서버·Bearer·감사) ② `sre_agent` 소비측(병렬 작업자 — 두 번째 MCP 서버 · APM 지침 · `was_signals` 승격 · WAS 권고 · 브리핑 라벨 · 정체 가드) ③ `noise_gate` 소비측(병렬 작업자 — `apm` 선판정 · 배지 · 힌트 · `app_impact` · 관리자 매뉴얼 9.5절) ④ SDD 산출물 `spec/CAPABILITY-MAP-87.md`·`SPEC-apm-*` 3종·`tasks/plan-87.md`·`todo-87.md` ⑤ **§0.11 신설**(착수 판정 · 구현 위치 · 이탈 E-1~E-10 · 한계 · 수용 기준) ⑥ 게이트: 게이트웨이 154 passed·2 skipped(Docker IT 옵트인) · `sre_agent` 747 passed·3 skipped · `mcp_server` 600 passed·11 skipped · 루트(본체+`noise_gate`) 12161 passed·31 skipped·3 xfailed · 실패 0 · ruff·mypy(게이트웨이) 무결 · `arch_check --ci` 0 · `overfit_check --ci` 신규 유입 0(게이트웨이 편입 · 어댑터 제외 · 기준선 무변경) ⑦ 실기동 스모크 3종(MCP SSE 클라이언트 · `noise_gate` 클라이언트 · `sre_agent` 승격 함수 ← 게이트웨이 실출력) ⑧ 문서: `docs/31` v4 · D-195 구현 부기 · `docs/18` 3행 · `plans/INDEX.md` · 파일명 `-TODO` → `-WIP` · `CLAUDE.md`는 미반영(E-9) |
| 2026-09-29 | **v4.1 — 로컬 Docker 제니퍼 실서버 검증**(사용자 지시 *"도커에 설치되어 있는 제니퍼를 이용하여 검증할 수 있는 항목들은 모두 검증하라."* · 라이선스 없음 · 코드 변경 없음) — **§0.12 신설**: 어댑터(인증 · 허용 16경로 계약 위반 0 · 거부 34건 서버 `usageCount` Δ=0 · 크기 상한 · 속도 제한 · 타임아웃 · 연결 거부 · 실서버 302 비추종 · 실오류 본문 분류 · 토큰 0회) · 지표 식별자 13/13 카탈로그 일치 · 게이트웨이 실프로세스(MCP SSE · Bearer · 9종 · 폴러 · 감사 · 기동 로그) · `RUN_DOCKER_IT` 2 passed · `noise_gate` 게이트 노드(실클라이언트 · 판정 유지 · 지연 0.20초 · 다운 쿨다운) · `sre_agent`(holmes 헬스체크·도구 9종 발견 · 틀린 Bearer 미등록 · 실출력 → 한계 문구) · 녹화·목 동일성(상태 40/40 · JSON 모양 38/38 · 재녹화 21건 동일) — **58항목 통과**. 새 사실 F-1(`usageCount` 약 5초 지연 · 타임아웃 계수 — §0.10 #10 부기) · F-2(`profile.txt` `key` = 16진수) · 후속 후보 F-3(빈 인벤토리 600초 캐시 → `gateway_health`와 어긋남) · F-4(401 사유 불명확) · F-5(`average_db_pool_active_count`·`max_tps` 식별자) · F-6 · §0.11 (4) Docker IT 부기 · 같은 날 `docs/31` v4.1(구현 대조 · 실서버 검증 절 §10.2) |
| 2026-09-29 | **통지(`plans/125` · D-281 — 코드 0 · 이 문서 본문 불변)** — 사용자 확정 125 G-1 (a)·G-2 (a): **J5 본체 쪽 실행**(2단 처리기 `apm_query` · 두 번째 MCP 엔드포인트 · 인가 · 매뉴얼)은 `plans/125`로 이관(이관 장부 125 §13) · G-5′ ⓐ 선행 = 「TP-2.1a + 125가 실행하는 TP-9.2」(D-195 부기) · 게이트웨이 도구 요청(도메인 단위 순위 · 다건 hostname · hostname+PID 정합)은 87 수용 여부로 기록(125 G-12) · §5.6 v2 서술(실행 그룹 `order: 20` 순차)은 121 루틴·소유표 방식으로 대체됨 |
| 2026-09-30 | **통지(`plans/125` · D-283 — 이 문서 본문 불변)** — J5 본체 쪽 125 이관분 중 **A-1(레지스트리 `solutions[apm]` · 보기 표) · A-2(`MCP_SOURCE_ENDPOINTS`·`MCP_SOURCE_TOKENS`·`MCP_SOURCE_CALL_TIMEOUT` · 관측 소스 MCP 세션) · A-3(`apm_query` — LLM 0 · 조건부 등록) · A-5(분해 `views[]`)** 랜딩(`multiintent` · 엔드포인트 없음 = 비활성 · 바이트 불변). 게이트웨이 도구 계약은 SPEC-apm-gateway §3 봉투 모양을 그대로 소비한다(`src` ↔ `apm_gateway` import 0). **87 쪽 요청(A-6 · G-12)**: 도메인 단위 인스턴스 순위 · 다건 hostname 입력 · hostname+PID 정합 — 지금은 호스트별 fan-out과 인스턴스 상한 고지로 대신한다. 인가(A-7)·매뉴얼(A-8)은 125 잔여(§14.2) |
