# 165. 시연 카탈로그 설계 중 발견한 제품 결함 4건 — 기록 (수정 미착수)

> **작성일**: 2026-10-14 · **v1.0**(기록만 · 코드 0)
> **상태**: **TODO** — 사용자 지시 「별도 기록으로만 남겨두고」(2026-10-14). 시연(`testdata/demo/sessions.yaml` · D-326 부기)은 질의 원칙으로 네 건을 모두 피해 간다. 착수하려면 범위·정책을 정해 계획서를 보강하고 D-번호를 잡는다.
> **발견 경위**: 장애 대응 연계 시연 카탈로그(D-326 2026-10-14 부기)를 폐쇄망 LLM temperature ≠ 0 전제로 코드 검토하던 중.
> **실측 기준**: main `9ccc44f`. `file:line`은 이 시점 값이다.
> **관련 결정**: D-326(시연) · D-334(APM 대상 승계) · D-202(알람 결정적 조립) · D-046(server_name ≠ hostname) · D-161(경로 판정) · D-179(overfit) · D-274(게이트웨이 경계)

---

## 1. 결함 목록

| # | 결함 | 발현 조건 | 시연 회피 방법 | 운영 영향 |
|---|---|---|---|---|
| F1 | 알람 결정적 조립이 서버 지목을 무시 → 존 전체 활성 알람 | `TEXT2SQL_ALARM_DETERMINISTIC=true`(기본 off) · 시맨틱 경로는 `semantic_compose`도 | 템플릿 실행 전 조건 「false 유지」 | 플래그를 켤 수 없게 만든다 |
| F2 | MCP `polestar_metric_trend`·`polestar_resource_status`가 b0에서 0건(파일시스템 복수형 고정 · 등록명 정확 일치) | sre_agent 증거 사전수집(상시) · 본체 `host_inspect`(`composite.investigation_enabled` 기본 off) | 원칙 6 — 조사 도구 키워드 미사용 | **sre_agent b0 알람 조사에서 파일시스템 증거가 늘 빔** |
| F3 | 제니퍼 → 폴스타 서버 범위 승계가 hostname 정확 일치(대소문자·FQDN) · 빈 hostname 침묵 누락 | 제니퍼 결과 뒤 「그 서버들」을 폴스타로 | 원칙 2·4 — 폴스타 턴은 서버명 직접 지목 · `was_host`는 폴스타 표기 | 연계 질의에서 서버가 말없이 빠짐 |
| F4 | 서버가 아닌 식별 칸(instance_id·domain_id·…_name)이 직전 엔티티 상한 20개를 잠식 · `previous_entities_complete`는 행 수로 판정 | 직전 결과 행에 식별 칸이 여럿 · 행 수가 많음 · 소스 이름을 써서 D-334 승계가 꺼진 제니퍼 → 제니퍼 「그 서버들」 | 원칙 3 — 제니퍼 결과를 다시 「그 서버들」로 잇지 않음(A2·B1·B2를 2턴으로) | 「그 서버들」이 일부 서버로만 조회되는데 완전하다고 표시 |

## 2. 상세

### F1 알람 결정적 조립 — 서버 조건 부재
- 진입점 3곳이 플래그·`routing_intent`·어댑터만 본다: `src/nodes/query_generator.py:1385` `_try_deterministic_alarm_single` · `src/nodes/multi_db_executor.py:615-645` · `src/nodes/output_generator.py:1664`.
- `src/db_adapters/polestar/assembler.py:1488` `recognize_active_alarm_query`에 서버 식별 게이트가 없고 `build_active_alarm_sql`에 서버 WHERE가 없다.
- `src/nodes/semantic_compiler.py` 패턴 C `_compile_c`는 `server_scope`를 받지 않고, scope가 있으면 식별 누락 검사(:1359)를 건너뛴다.
- **수정안**: (a) 이번 턴 서버 식별 필터(`has_host_identifier_filter`)·직전 서버 범위가 있으면 조립 포기 → LLM(작음 · 플래그 off와 비트 동일) · (b) 조립기·`_compile_c`에 서버 조건 조립(b0 등록명 접미사 규칙 `config/db_profiles/polestar_b0.yaml` 정의 재사용 · 경계 매칭 정책 필요).
- **트레이드오프**: (a) 서버 지목 알람 질의는 결정성을 못 얻는다. (b) 정확 일치는 b0 등록명 접미사로 0건, 단순 LIKE는 `sicwso01`이 `sicwso011`을 잡는다 — 경계 규칙·DB2 방언 처리 필요.
- **권장**: (a) 먼저(플래그를 켜기 전 필수), (b)는 별도 Wave.

### F2 MCP 조사 도구 — b0 0건
- `mcp_server/mcp_server/polestar_tools.py` `_METRIC_KIND_MAP["filesystem"]=("server.FileSystems","Utilization")` — b0는 단수 `server.FileSystem`만 있다(`config/knowledge/polestar_b0/` 카탈로그 주석 실측 · 공동존은 둘 다).
- metric_trend `svr.name = …` · resource_status `SVR.NAME = …` 정확 일치 — b0 등록명은 `<hostname> (<업무 설명>)` 꼴이라 hostname을 넘기면 0건.
- 호출자: `sre_agent/sre_agent/application/evidence_prefetch.py` `TOOL_METRIC`(상시 · 알람의 server_name은 등록명이라 ②는 대개 무관, ①은 확실히 걸림) · `src/orchestration/host_inspect.py:306`(기본 off) · 1단 deep_agent `inspect_host`.
- **수정안**: ① 소스별 파일시스템 resource_type(b0 단수 · PG 존 `IN`) — mcp_server 자기 소스 설정에 둔다(본체 config import 금지 · `overfit_check`가 `mcp_server/mcp_server` 리터럴을 스캔 — D-179) ② `name = v OR hostname = v`(대소문자 무시).
- **트레이드오프**: ② 한 hostname에 등록명이 여럿이면 행이 는다 — 서버별 묶음·「여러 건 매칭」 표시 필요. 테스트는 `../.venv/bin/python -m pytest`(루트 수집 밖).
- **권장**: ① 즉시 가치 높음(작고 확실) · ②는 같은 작업에 묶는다.

### F3 제니퍼 → 폴스타 hostname 정확 일치
- `src/db_adapters/polestar/assembler.py:1152-1158` `HAVING MAX(CASE … c.hostname END) IN (...)` 정확 일치. 제니퍼 hostname은 `resolver.reverse`(`fleet_tools.py:498,519`) 원표기(대문자·FQDN 가능), 실패 시 `""` → `query_gen_common.py:1398`에서 침묵 누락.
- 정규화(LOWER·짧은 이름)는 `key_bridge.py:330-358`에만 있고 `CROSS_SYSTEM_KEY_BRIDGE_ENABLED`(기본 off) 뒤에 있다. `query_generator.py:596-615`는 hostname 칼럼이면 HAVING 경로를 우선한다.
- 반대 방향(폴스타 → 제니퍼)은 게이트웨이 `_host_equal`이 대소문자 무시·짧은 이름 비교 — **비대칭**.
- **수정안**: HAVING 비교를 양쪽 `LOWER(짧은 이름)`으로 · 빠진 hostname은 「n건 중 m건 hostname 미확인」 고지.
- **트레이드오프**: 짧은 이름 충돌(`web01.a`·`web01.b`) · 정확 일치 단언 테스트 갱신 · 0건 → 행 있음으로 바뀌므로 「기본 off = 비트 동일」 원칙 예외로 결함 수정 처리할지 사용자 결정 필요.

### F4 직전 엔티티 상한 잠식 · complete 오판
- `src/nodes/context_resolver.py:53` `_IDENTITY_KEY_HINTS = (…, "name", …, "id")`를 부분 일치(:361)로 써 `instance_id`·`domain_id`·`instance_name`·`service_name`·`resource_id`가 수집된다. 상한 20(`ENTITY_SAMPLE_ROWS`)은 **값 개수**인데 complete(:131)는 **행 수 ≤ 20**으로 판정 → 잘려도 「완전」.
- 대상 해소(`src/utils/prior_targets.py` · `HOST_IDENTIFIER_FIELDS`)는 hostname·server_name·name 계열만 쓰므로 잠식된 슬롯만큼 서버가 빠진다. 플래너는 앞 10개만 주입(`intent_planner.py:1165-1179`).
- 제니퍼 → 제니퍼에서 특히 걸린다: 소스 단어가 있으면 D-334 `inherited_targets`(`apm_query.py:1856` · `_same_sources`)가 꺼져 대상이 `previous_entities` ③에만 의존한다. `_last_targets`(인스턴스 이름 전수)는 소스 단어 없는 후속에서만 쓰인다.
- **수정안**: ① 상한을 행(서버) 수 기준으로 ② complete에 엔티티 절단 여부 반영 ③(선택) 힌트를 단어 경계 일치로·hostname 계열 우선 정렬 ④(선택) 같은 소스 재언급(소스 단어 = 직전 승계 소스)은 `_same_sources`에서 변경으로 보지 않기.
- **트레이드오프**: ③은 `name` 칸만 있는 결과의 승계를 끊을 위험 · `subagents._IDENTITY_KEY_HINTS`와 같이 바꿔야 함. ①② 후 complete=False가 늘어 생략형 승계가 막히는 경우가 는다(정상). ④는 D-334 G-3 ④ 규칙 개정 — 결정 부기 필요.
- **권장**: ①② 먼저, ③④는 실측 뒤.

## 3. 권장 착수 순서

F2 ① → F3 → F4 ①② → F1 (a) → 후속(F1 (b) · F2 ② · F4 ③④). 착수 시 계획 시작 SHA·회귀 대상 파일을 머리에 적는다(D-303).
