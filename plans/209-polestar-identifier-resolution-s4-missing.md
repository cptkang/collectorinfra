# plans/209 — 폴스타 서버 식별자 해소(등록명↔호스트명) · S4 지표 0건 결손 판정

**상태**: 구현 완료(2026-11-03) · 폐쇄망 검증 대기 · 결정 D-360(부기 1) — W1 mcp_server `requery_with_resolved_identifier`(7도구 공유) · W2 질문 식별자 라벨·조사 지침 블록·S0 스코프 `_rescoped_polestar_server` · W3 `metric_finding` 3상태·S4 결손/판단 보류·하류 R-E·step_findings · 화면 JS 무변경(칩 매핑 기존 그대로 · 매뉴얼 대상 아님)
**브랜치**: `improvement` (기준 `f0d1d9c4`)
**계획 시작 SHA**: `f0d1d9c4`
**회귀 대상 파일**: `mcp_server/mcp_server/polestar_tools.py` · `sre_agent/sre_agent/interface/mcp_service.py` · `sre_agent/sre_agent/application/investigation_guidance.py` · `sre_agent/sre_agent/application/playbook_steps.py` · `sre_agent/sre_agent/application/playbook_runner.py` · `sre_agent/sre_agent/domain/correlation.py` · `sre_agent/sre_agent/domain/playbook_readings.py` · `sre_agent/sre_agent/domain/step_findings.py` (+ 테스트)
**이탈**: 판정 불가 범위를 계획 문구(기준선 부족·분산 0 전부)보다 좁힘 — 절대 임계가 있으면 현행 판정, 분산 0+최대≤평균은 이상 없음(D-360 부기 1).
**관련 결정**: D-360(본 계획) · D-046(프로세스 조회 서버명→호스트명 해소) · D-061(등록명·호스트명·IP 구분) · D-188(역조회 보수 원칙 — 모호하면 승격 안 함) · D-348(APM→폴스타 확정 연결만 자동 사용) · D-341 부기 21(S0r·S6 「없음」과 「조회 실패」 분리)

## 1. 배경 — 폐쇄망 보고(2026-11-03)

1. HolmesGPT 조사가 폴스타 도구를 OS 호스트명으로 호출해 「worker1에 대한 정보가 없다」고 결론 낸다. 등록명은 `cocm-ngcpkd05`, 호스트명은 `worker1`이다.
2. 「단계별 근거」 S4(`host.usage_trend`)가 「CPU 사용률 최고 - · 기준선 평균 - · z -」인데 「이상: 아니오」이고 배지는 「정상」이다.

## 2. 원인 (코드 실측)

### 2.1 mcp_server — 등록명 전용 도구에 해소·0건 안내가 없다
- `polestar_tools.py`의 `polestar_alarm_history`·`polestar_incident_alarms`·`polestar_metric_trend`·`polestar_resource_status`·`polestar_topology`·`polestar_change_history`는 `SVR.NAME = x` 완전 일치다. 호스트명을 넣으면 오류 없이 `rows=[]`이고 안내가 없다.
- `polestar_os_config`는 반대로 호스트명만 받는다(등록명이면 0건).
- 해소는 `polestar_process_snapshot`만 한다(`resolve_process_hostname` → `resolve_polestar_anchor` → `build_anchor_lookup_sql` `NAME = x OR HOSTNAME = x` · D-046).
- 도구 설명은 「폴스타 등록 서버명」 한 줄뿐이고, 프로세스·OS 도구는 「OS 호스트명」을 요구해 LLM이 혼동한다.

### 2.2 sre_agent — 식별자가 라벨 없이 하나만 실린다
- `interface/mcp_service.py:185` `server = serverName or hostname` — 질문 첫머리에 값 1개만 들어간다. 등록명/호스트명 구분·폴스타 source(dbId)가 없다.
- `serverName`이 실제로 호스트명인 경우: 폴스타 템플릿 `"serverName":"${hostname}"`(D-188) 역조회가 모호·실패·3초 초과면 승격 생략(`noise_gate/application/server_identity.py`) · APM 이벤트는 `"serverName": hostname or instance_name`(`apm_gateway/.../domain/events.py:124`).

### 2.3 sre_agent — S0 뒤 스코프 재작성이 등록명을 호스트명으로 덮는다
- `application/playbook_steps.py:1292-1295`: `polestar_link != "linked"`이면 `polestar_server = host or ctx.scope.polestar_server`. 폴스타 알람처럼 이벤트가 이미 등록명을 가진 경우에도 제니퍼 호스트명으로 덮어 S4 이후 폴스타 조회가 0건이 된다.

### 2.4 sre_agent — S4가 0건을 정상으로 판정한다(침묵 폴백)
- `playbook_steps.py:1846` 결손 판정이 `resp.data is None`(조회 실패)뿐 — 조회 성공 + 0행은 빈 시리즈로 진행.
- `domain/correlation.py:137-150` `metric_finding` 초기값 `is_anomalous: False` · 점 0개면 notes만 남기고 False 반환(기준선 부족·분산 0도 z=None인데 False).
- `playbook_steps.py:1857·1871` → `status="ok", verdict="normal"`. 화면(`briefing-view.js` `stepChip`)은 verdict를 그대로 「정상」으로 옮긴다.
- 하류: `domain/playbook_readings.py:632` R-E(cpu_owner)가 `bool(is_anomalous)`로 「CPU 이상 없음」 판독 · `investigation_guidance.py:148`·`briefing_builder.py:139`도 같은 값.

## 3. 범위와 비범위

- **범위**: 아래 W1~W3.
- **비범위**: 「조사 LLM 응답에 도구 호출 원문이 섞임」 경고(hermes `<tool_call>` 변환 등) — 사용자 결정으로 별도 검토(운영 로그·vLLM 파서 확인 후). noise_gate 역조회(D-188) 규칙 변경 없음. D-348 후보·학습 로직 변경 없음.

## 4. 설계

### W1 — mcp_server: 0건일 때 식별자 재해소 + 안내 (D-360 ①②)
- **규칙(현행 비트 동일 보장)**: 받은 값으로 먼저 조회한다. **행이 있으면 지금과 같다**(추가 조회 0). 0행이고 `source`가 정해져 있을 때만 `resolve_polestar_anchor(pool, value, source=source)`를 1회 부른다.
  - 등록명 전용 도구 6개: `resolved`이고 `anchor.name`이 받은 값과 다르면 그 등록명으로 **1회 재조회**하고 응답에 `server_name_note`(예: 「OS 호스트명 'worker1' → 폴스타 등록명 'cocm-ngcpkd05'로 조회」)를 단다.
  - `polestar_os_config`: 대칭 — `anchor.hostname`으로 재조회(기존 `resolve_process_hostname` 재사용 가능).
  - `ambiguous`·`not_found`·`lookup_error`·source 없음 → 재조회하지 않는다(D-188 보수 원칙). 0건 응답에 `hint`(아래)와 사유(모호 후보 수·조회 실패)를 단다. 침묵 폴백 금지.
- **0건 hint 문구**(등록명 도구): 「server_name은 폴스타 등록명(서버 이름)이다 — OS 호스트명을 넣었다면 등록명으로 다시 조회하라(polestar_process_snapshot 응답의 server_name 참고)」. os_config는 반대 문구.
- **도구 설명(docstring)**: 6개에 「server_name = 폴스타 등록명(OS 호스트명 아님 · 0건이면 서버가 호스트명으로 한 번 더 찾는다)」, os_config·process_snapshot에 「hostname = OS 호스트명」을 명시.
- 재조회 헬퍼는 한 곳(예: `polestar_helpers.py` 또는 `polestar_tools.py` 내부 함수)에 두고 7개 도구가 공유한다(중복 금지).
- `source` 인자가 없는 도구는 해소를 건너뛰고 hint만 단다.
- overfit_check: `mcp_server/mcp_server`는 스캔 대상 — 독스트링·주석에 스키마 리터럴(테이블·컬럼명)을 새로 쓰지 않는다(D-088).

### W2 — sre_agent: 식별자 라벨 · 스코프 덮어쓰기 교정 (D-360 ③④)
- **질문**: `mcp_service.py:185` 근처에서 등록명·호스트명·폴스타 source를 라벨과 함께 싣는다(예: 「대상 서버: 폴스타 등록명 X · OS 호스트명 Y · 폴스타 source Z」 — 값이 없거나 같으면 생략). 첫머리 서버 표기는 등록명 우선.
- **조사 지침**(`build_guidance`): 「polestar_* 도구의 server_name = 폴스타 등록명 · polestar_os_config/process_snapshot의 hostname = OS 호스트명 · source = 폴스타 dbId」 블록 1개를 더한다. prom_* 지침과 문구 중복이 없게.
- **사전 수집**: `evidence_prefetch.py:122`는 그대로 두되 W1의 서버 측 재해소로 보완된다(변경 최소). 필요하면 등록명 필드를 우선 읽는 것만.
- **스코프 재작성**(`playbook_steps.py:1292-1295`): `linked`가 아니어도 **이벤트가 준 폴스타 서버명이 이벤트 hostname과 다르면(= 등록명으로 승격된 값) 덮지 않는다**. 덮는 경우(원래 값이 비었거나 hostname과 같을 때)에만 `host`를 쓰고, 그 사실을 `polestar_basis`/사유에 「제니퍼 호스트명으로 조회」로 남긴다. D-348 소스 순위(④)·확정 연결 우선은 그대로다.

### W3 — sre_agent: S4 0건·판정 불가 → 결손/보류 (D-360 ⑤)
- `metric_finding`: 점 0개·기준선 부족·분산 0으로 z를 못 구하면 `is_anomalous: None`(판정 불가)과 `judgeable: False`·`reason`을 둔다. 판정 가능한 경우는 현행 그대로.
- `usage_trend`:
  - 모든 kind가 결손(조회 실패) 또는 판정 불가 → `status="missing"`, `missing=[kind…]`, `reason`에 notes(「사건 구간 데이터 없음」 등) + basis.
  - 일부만 판정 불가 · 나머지에 이상 있음 → `verdict="degraded"`(현행).
  - 일부만 판정 불가 · 나머지 이상 없음 → `verdict=None`(판단 보류 — 화면 칩 「판단 보류」) + `missing`에 판정 불가 kind.
  - 표 행의 `is_anomalous` 칸은 None 그대로(화면은 「—」).
- `None`을 `bool()`로 읽는 하류를 3상태로: `correlation.py`(243·291·297 부근) · `playbook_readings.py:632`(R-E — None이면 「호스트 CPU 추이 결손」 경로 `:629-630`로) · `investigation_guidance.py:148` · `briefing_builder.py:139` · `step_findings.py:265-293`(「모든 지표 평소 범위」 문구가 판정 불가를 포함하지 않게).
- 화면(`src/static/js/briefing-view.js`): 칩은 기존 `missing`·`hold` 매핑으로 충분 — `cellHtml`에서 `is_anomalous === null`이면 「판정 불가」로 표기(선택 · 바꾸면 `briefing-view.js?v=` 캐시 버전과 그 값을 단언하는 테스트 갱신).

## 5. 테스트

- **W1**(`mcp_server/tests/test_plan209_*.py`, `../.venv` 파이썬으로 실행): 가짜 풀로 ① 등록명 일치 → 재해소 호출 0(현행 동일) ② 호스트명 1건 해소 → 등록명 재조회·`server_name_note` ③ 모호·0건·조회 오류·source 없음 → 재조회 없음·hint·사유 ④ os_config 대칭 ⑤ 7개 도구 모두 같은 헬퍼 경유.
- **W2**(`sre_agent/tests/`, 자체 venv): 질문 라벨(값 같음/없음 생략) · 지침 블록 1회 · 스코프 — 이벤트 등록명≠hostname이면 미연결이어도 유지, 같거나 비면 host + basis 기록, `linked`는 현행.
- **W3**: 폴스타 ok + `rows=[]` → S4 `missing`(정상 아님) · 일부 0건 + 나머지 정상 → 판단 보류 · 일부 0건 + 나머지 이상 → degraded · R-E가 None을 결손으로 판독. 기존 `test_correlation.py`·`test_plan187_w20a_findings.py`·`test_plan187_w3_readings.py`·`test_plan196_axis_status.py`·`test_plan200_w2_dag.py`·`test_plan187_w20_verify.py`·`test_evidence_prefetch.py`·스냅샷 `fixtures/plan187/snapshots/w20_flow_hostcpu.json` 중 의미가 바뀌는 단언은 근거와 함께 갱신(스냅샷은 0건 입력 경로만 바뀌어야 한다).
- 회귀: 계획 끝에 `python scripts/regress.py --base f0d1d9c4 --files <바꾼 파일…>`(D-303). mcp_server·sre_agent는 루트 수집 밖이라 각자 pytest를 따로 돈다.

## 6. 배포·검증 (폐쇄망)

- 반입: mcp_server 재기동 · sre_agent 재기동(본체 재기동은 W3 화면 변경 시에만).
- 확인: ① 등록명≠호스트명 서버(cocm-ngcpkd05/worker1) 알람 조사에서 폴스타 도구 응답 `server_name_note` 유무 ② S4가 값이 있거나, 없으면 「결손」/「판단 보류」로 표시 ③ 등록명 일치 서버의 조회 지연 변화 없음(재해소는 0건일 때만).

## 7. 결정 기록

- D-360 신규(본 계획 결정). D-046·D-341 부기는 D-360 본문 「관련」에 적고 원 결정은 고치지 않는다.
